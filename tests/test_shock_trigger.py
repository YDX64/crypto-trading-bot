"""E23 gerçek zamanlı BTC şok tetikleyicisi T (PLAN §3.2) testleri.

Kapsam:
  * `evaluate_shock_trigger` matrisi (kapalı / eşik DAHİL / ters yön / veri yok);
  * config doğrulaması;
  * NEDENSELLİK (a): `LeaderSeries.inputs_at(t)` + `evaluate_shock_trigger`,
    `t`'den sonra kapanan hiçbir lider mumuna bağlı değildir;
  * NEDENSELLİK (b): `simulate_symbol`'de lider serisinin `T` sonrası
    değiştirilmesi `signal_close_time <= T` işlemlerini DEĞİŞTİRMEZ;
  * harness kapısı (missed_counter, fail-closed), `shock_trigger_activity`;
  * canlı motor: `_shock_trigger_reason` ve `_evaluate_symbol` bağlantısı.
"""

from __future__ import annotations

import asyncio
import dataclasses
import math
import random
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

import src.strategies.scalper.engine as engine_module
from src.core.config import Settings
from src.strategies.scalper import intent
from src.strategies.scalper import market_gate as mg
from src.strategies.scalper.backtest import (
    LeaderSeries,
    shock_trigger_activity,
    simulate_symbol,
)
from src.strategies.scalper.market_gate import (
    REASON_SHOCK_DIR,
    REASON_SHOCK_NODATA,
    REASON_SHOCK_OFF,
    evaluate_shock_trigger,
    shock_trigger_direction,
    shock_trigger_enabled,
)
from src.strategies.scalper.types import Candle, Direction, ScalpSignal, StrategyContext
from src.trading.symbol_reservations import symbol_reservations

# Mevcut piyasa kapısı testlerinin kurgu parçaları (aynı dizin; pytest `tests/`
# klasörünü sys.path'e koyar).
from test_market_gate import (  # noqa: E402
    _DECISION_MS,
    _LEADER_DAILY,
    _LEADER_ENTRY,
    _LEADER_INTRADAY,
    _EvalCfg,
    _EvalFetcher,
    _FakeClock,
    _LeaderFetcher,
    _NeverBlockedExecutor,
    _NoPositionsExits,
    _SimCfg,
    _bare_engine,
)

_DAY_MS = 86_400_000
_M15 = 15 * 60_000
_M30 = 30 * 60_000
_M5 = 5 * 60_000


def _cfg(pct: float, **over: Any) -> SimpleNamespace:
    return SimpleNamespace(scalper_shock_trigger_pct=pct, **over)


# ==========================================================================
# 1) Saf fonksiyon matrisi
# ==========================================================================

class TestEvaluateShockTrigger:
    def test_disabled_returns_none_for_every_input(self):
        for pct in (0.0, -1.0):
            assert evaluate_shock_trigger("LONG", 100.0, 150.0, _cfg(pct)) is None
            assert evaluate_shock_trigger("SHORT", None, None, _cfg(pct)) is None
        # alan hiç yoksa da kapalı
        assert evaluate_shock_trigger("LONG", None, None, SimpleNamespace()) is None
        assert shock_trigger_enabled(SimpleNamespace()) is False
        assert shock_trigger_enabled(_cfg(1.5)) is True

    def test_long_exactly_at_plus_x_is_allowed(self):
        # 100 -> 102 = tam +%2.0 (ikili kayan nokta payı için 2.0 gerçekten 2.0)
        d = (102.0 / 100.0 - 1.0) * 100.0
        assert evaluate_shock_trigger("LONG", 100.0, 102.0, _cfg(d)) is None
        assert evaluate_shock_trigger("SHORT", 100.0, 102.0, _cfg(d)) == REASON_SHOCK_DIR

    def test_short_exactly_at_minus_x_is_allowed(self):
        d = abs((98.0 / 100.0 - 1.0) * 100.0)
        assert evaluate_shock_trigger("SHORT", 100.0, 98.0, _cfg(d)) is None
        assert evaluate_shock_trigger("LONG", 100.0, 98.0, _cfg(d)) == REASON_SHOCK_DIR

    def test_below_threshold_is_off_for_both_directions(self):
        for direction in ("LONG", "SHORT"):
            assert evaluate_shock_trigger(direction, 100.0, 101.0, _cfg(1.5)) == REASON_SHOCK_OFF
            assert evaluate_shock_trigger(direction, 100.0, 99.0, _cfg(1.5)) == REASON_SHOCK_OFF
            assert evaluate_shock_trigger(direction, 100.0, 100.0, _cfg(1.5)) == REASON_SHOCK_OFF

    def test_opposite_direction_is_blocked_when_trigger_is_on(self):
        assert evaluate_shock_trigger("SHORT", 100.0, 103.0, _cfg(3.0)) == REASON_SHOCK_DIR
        assert evaluate_shock_trigger("LONG", 100.0, 97.0, _cfg(3.0)) == REASON_SHOCK_DIR
        assert evaluate_shock_trigger("LONG", 100.0, 103.5, _cfg(3.0)) is None
        assert evaluate_shock_trigger("SHORT", 100.0, 96.0, _cfg(3.0)) is None

    @pytest.mark.parametrize(
        "day_open,last_close",
        [
            (None, 100.0), (100.0, None), (None, None),
            (0.0, 100.0), (-5.0, 100.0), (100.0, 0.0), (100.0, -1.0),
            (float("nan"), 100.0), (100.0, float("inf")), ("x", 100.0),
        ],
    )
    def test_missing_or_invalid_inputs_are_nodata_fail_closed(self, day_open, last_close):
        assert (
            evaluate_shock_trigger("LONG", day_open, last_close, _cfg(1.5))
            == REASON_SHOCK_NODATA
        )

    def test_enum_and_string_directions_agree(self):
        for direction in (Direction.LONG, Direction.SHORT):
            for last in (97.0, 100.0, 103.0):
                assert evaluate_shock_trigger(
                    direction, 100.0, last, _cfg(2.0)
                ) == evaluate_shock_trigger(direction.value, 100.0, last, _cfg(2.0))
                assert evaluate_shock_trigger(
                    direction.value.lower(), 100.0, last, _cfg(2.0)
                ) == evaluate_shock_trigger(direction.value, 100.0, last, _cfg(2.0))

    def test_direction_helper_is_pure_and_none_on_bad_input(self):
        assert shock_trigger_direction(100.0, 104.0, 3.0) == "LONG"
        assert shock_trigger_direction(100.0, 96.0, 3.0) == "SHORT"
        assert shock_trigger_direction(100.0, 101.0, 3.0) is None
        assert shock_trigger_direction(None, 101.0, 3.0) is None
        assert shock_trigger_direction(100.0, 104.0, 0.0) is None

    def test_reason_strings_match_intent_registry(self):
        assert REASON_SHOCK_OFF == intent.REASON_SHOCK_TRIGGER_OFF == "shock_trigger_off"
        assert REASON_SHOCK_DIR == intent.REASON_SHOCK_TRIGGER_DIR == "shock_trigger_dir"
        assert (
            REASON_SHOCK_NODATA == intent.REASON_SHOCK_TRIGGER_NODATA
            == "shock_trigger_nodata"
        )
        for name in (REASON_SHOCK_OFF, REASON_SHOCK_DIR, REASON_SHOCK_NODATA):
            assert name in intent.KNOWN_REASONS
            assert intent.REASON_LABELS[name].strip()


# ==========================================================================
# 2) Config doğrulaması
# ==========================================================================

class TestShockTriggerSetting:
    def test_default_is_off(self):
        assert Settings.model_fields["scalper_shock_trigger_pct"].default == 0.0

    def test_valid_values(self):
        for v in (0.0, 1.5, 3.0):
            assert Settings(scalper_shock_trigger_pct=v).scalper_shock_trigger_pct == v

    @pytest.mark.parametrize("bad", [-0.1, -3.0, float("nan"), float("inf"), float("-inf")])
    def test_rejects_negative_and_non_finite(self, bad):
        with pytest.raises(ValueError, match="SCALPER_SHOCK_TRIGGER_PCT"):
            Settings(scalper_shock_trigger_pct=bad)

    def test_env_var_is_read(self, monkeypatch):
        monkeypatch.setenv("SCALPER_SHOCK_TRIGGER_PCT", "1.5")
        assert Settings().scalper_shock_trigger_pct == 1.5


# ==========================================================================
# 3) Sentetik lider serisi + NEDENSELLİK (a)
# ==========================================================================

# Gün 0 ve gün 1 (UTC). Gün 1 karar günü; ondan önce 3 günlük 1d geçmişi.
_D1 = 20_000 * _DAY_MS          # karar günü 00:00 UTC (gün 20000)
_D0 = _D1 - _DAY_MS


def _candle(open_time: int, step: int, o: float, c: float) -> Candle:
    return Candle(
        open_time=open_time, open=o, high=max(o, c) * 1.001, low=min(o, c) * 0.999,
        close=c, volume=1.0, close_time=open_time + step - 1,
    )


def _walk(start_ms: int, n: int, step: int, p0: float, seed: int, drift: float) -> List[Candle]:
    rng = random.Random(seed)
    out: List[Candle] = []
    p = p0
    for k in range(n):
        q = p * (1.0 + drift + rng.uniform(-0.002, 0.002))
        out.append(_candle(start_ms + k * step, step, p, q))
        p = q
    return out


def _synthetic_raw(drift: float = 0.0004, seed: int = 7):
    """(15m, 30m, 1d) HAM mumlar: gün 0 + gün 1 (gün 1'in 1d mumu OLUŞMAKTA —
    close_time günün sonunda). 1d açılış = 00:00 UTC 15m açılışı."""
    c15 = _walk(_D0, 192, _M15, 100.0, seed, drift)
    # 30m seri 15m ile AYNI yolu izlesin (gün-açılışı ile tutarlı fiyat düzeyi)
    c30: List[Candle] = []
    for k in range(96):
        a, b = c15[2 * k], c15[2 * k + 1]
        c30.append(Candle(
            open_time=a.open_time, open=a.open, high=max(a.high, b.high),
            low=min(a.low, b.low), close=b.close, volume=2.0,
            close_time=a.open_time + _M30 - 1,
        ))
    c1d: List[Candle] = []
    for d in range(-3, 1):
        start = _D1 + d * _DAY_MS
        if d >= 0:
            seg = [c for c in c15 if start <= c.open_time < start + _DAY_MS]
            c1d.append(Candle(
                open_time=start, open=seg[0].open, high=max(c.high for c in seg),
                low=min(c.low for c in seg), close=seg[-1].close, volume=1.0,
                close_time=start + _DAY_MS - 1,
            ))
        else:
            p = 95.0 + d
            c1d.append(Candle(
                open_time=start, open=p, high=p, low=p, close=p + 0.5,
                volume=1.0, close_time=start + _DAY_MS - 1,
            ))
    return c15, c30, c1d


def _leader_from_raw(c15: List[Candle], c30: List[Candle], c1d: List[Candle]) -> LeaderSeries:
    return LeaderSeries(
        symbol="BTCUSDT",
        entry_close_times=[c.close_time for c in c30],
        entry_closes=[c.close for c in c30],
        daily_close_times=[c.close_time for c in c1d],
        daily_closes=[c.close for c in c1d],
        intraday_open_times=[c.open_time for c in c15],
        intraday_opens=[c.open for c in c15],
        intraday_close_times=[c.close_time for c in c15],
        entry_candles=list(c30), daily_candles=list(c1d), intraday_candles=list(c15),
    )


def _extreme(c: Candle, value: float) -> Candle:
    return dataclasses.replace(c, open=value, high=value * 1.01, low=value * 0.99, close=value)


def _mutate_after(candles: List[Candle], t: int, value: float) -> List[Candle]:
    return [_extreme(c, value) if c.close_time > t else c for c in candles]


def _decision(leader: LeaderSeries, t: int, cfg) -> Any:
    inputs = leader.inputs_at(t)
    if inputs is None:
        return ("no-inputs",)
    return (
        inputs[0], inputs[1],
        evaluate_shock_trigger("LONG", inputs[0], inputs[1], cfg),
        evaluate_shock_trigger("SHORT", inputs[0], inputs[1], cfg),
    )


class TestCausalityLeaderInputs:
    """PLAN §3.2 nedensellik (a)."""

    def test_future_candles_cannot_change_the_decision(self):
        c15, c30, c1d = _synthetic_raw()
        base = _leader_from_raw(c15, c30, c1d)
        cfg = _cfg(0.3)
        cutoffs = [c.close_time for c in c30 if _D1 <= c.open_time < _D1 + _DAY_MS]
        assert len(cutoffs) == 48
        seen = set()
        for t in cutoffs:
            ref = _decision(base, t, cfg)
            seen.add(ref[2:])
            for extreme in (1e6, 1e-3):
                mutated = _leader_from_raw(
                    _mutate_after(c15, t, extreme),
                    _mutate_after(c30, t, extreme),
                    _mutate_after(c1d, t, extreme),
                )
                assert _decision(mutated, t, cfg) == ref, (t, extreme)
        # kurgu gerçekten hem açık hem kapalı kararlar üretiyor (anlamlı test)
        assert len(seen) >= 2

    def test_the_still_forming_daily_candle_is_not_read(self):
        c15, c30, c1d = _synthetic_raw()
        t = _D1 + 12 * 3_600_000 - 1  # 11:59:59.999 → 30m mum kapanışı (12:00 hizası)
        t = max(c.close_time for c in c30 if c.close_time <= t)
        forming = [c for c in c1d if c.open_time == _D1][0]
        assert forming.close_time > t
        poisoned = [_extreme(c, 1e9) if c is forming else c for c in c1d]
        a = _leader_from_raw(c15, c30, c1d).inputs_at(t)
        b = _leader_from_raw(c15, c30, poisoned).inputs_at(t)
        assert a == b

    def test_sanity_past_candles_do_change_the_decision(self):
        """Aksi hâlde yukarıdaki test boş geçerdi: <= t mumları kararı değiştirir."""
        c15, c30, c1d = _synthetic_raw()
        t = _D1 + 12 * 3_600_000 - 1
        t = max(c.close_time for c in c30 if c.close_time <= t)
        base = _leader_from_raw(c15, c30, c1d).inputs_at(t)
        # gün açılışı mumu (00:00 15m) değişir → day_open değişir
        c15_open = [_extreme(c, 500.0) if c.open_time == _D1 else c for c in c15]
        changed_open = _leader_from_raw(c15_open, c30, c1d).inputs_at(t)
        assert changed_open[0] != base[0]
        # son kapanmış 30m mum değişir → last_close değişir
        c30_last = [_extreme(c, 500.0) if c.close_time == t else c for c in c30]
        changed_close = _leader_from_raw(c15, c30_last, c1d).inputs_at(t)
        assert changed_close[1] != base[1]
        cfg = _cfg(0.3)
        assert _decision(_leader_from_raw(c15_open, c30, c1d), t, cfg) != _decision(
            _leader_from_raw(c15, c30, c1d), t, cfg
        )


# ==========================================================================
# 4) Harness: simulate_symbol
# ==========================================================================

@dataclass
class _ShockSimCfg(_SimCfg):
    scalper_market_gate: bool = False
    scalper_shock_trigger_pct: float = 0.0


class _Strat:
    """Her turda sabit yönde sinyal (stop cfg politikasıyla yeniden hesaplanır)."""

    name = "C"

    def __init__(self, direction: Direction):
        self.direction = direction

    def evaluate(self, ctx: StrategyContext) -> ScalpSignal:
        p = ctx.current_price
        stop = p * (0.99 if self.direction == Direction.LONG else 1.01)
        return ScalpSignal(
            strategy="C", symbol=ctx.symbol, direction=self.direction,
            entry_price=p, stop_price=stop, reason="test", regime=ctx.regime,
            atr_5m=p * 0.002,
        )


def _symbol_candles(seed: int = 3) -> List[Candle]:
    """Gün 1'in 5m mumları (288) — gezinen bir yol; işlemler kapanabilsin."""
    rng = random.Random(seed)
    out: List[Candle] = []
    p = 50.0
    for k in range(288):
        # dalgalı yol: TP/SL bölgelerine girsin
        q = p * (1.0 + 0.004 * math.sin(k / 9.0) + rng.uniform(-0.003, 0.003))
        out.append(_candle(_D1 + k * _M5, _M5, p, q))
        p = q
    return out


def _simulate(cfg, leader, direction=Direction.LONG, missed=None):
    candles = _symbol_candles()
    missed = {} if missed is None else missed
    trades = simulate_symbol(
        "ETHUSDT", candles, candles, candles, [_Strat(direction)], cfg,
        missed_counter=missed, leader=leader,
    )
    return trades, missed


class TestHarnessShockTrigger:
    def _leader(self, last: float, open_: float = 100.0) -> LeaderSeries:
        """Gün boyu sabit lider: gün açılışı `open_`, tüm 30m kapanışları `last`."""
        c15 = [
            _candle(_D1 + k * _M15, _M15, open_ if k == 0 else last, last)
            for k in range(96)
        ]
        c30 = [_candle(_D1 + k * _M30, _M30, last, last) for k in range(48)]
        c1d = [_candle(_D1 - _DAY_MS, _DAY_MS, 99.0, 99.5)]
        return _leader_from_raw(c15, c30, c1d)

    def test_allowed_direction_trades_and_nothing_is_counted_as_blocked(self):
        cfg = _ShockSimCfg(scalper_shock_trigger_pct=1.0)
        trades, missed = _simulate(cfg, self._leader(103.0), Direction.LONG)
        assert len(trades) > 0
        # yalnız ilk 30m kapanıştan ÖNCEki 5m kararlarında girdi yok (nodata)
        assert REASON_SHOCK_DIR not in missed and REASON_SHOCK_OFF not in missed

    def test_opposite_direction_counted_as_dir(self):
        cfg = _ShockSimCfg(scalper_shock_trigger_pct=1.0)
        trades, missed = _simulate(cfg, self._leader(103.0), Direction.SHORT)
        assert trades == []
        assert missed.get(REASON_SHOCK_DIR, 0) > 0
        assert REASON_SHOCK_OFF not in missed

    def test_below_threshold_counted_as_off(self):
        cfg = _ShockSimCfg(scalper_shock_trigger_pct=3.0)
        trades, missed = _simulate(cfg, self._leader(102.0), Direction.LONG)
        assert trades == []
        assert missed.get(REASON_SHOCK_OFF, 0) > 0
        assert REASON_SHOCK_DIR not in missed

    def test_leader_none_blocks_everything_as_nodata(self):
        cfg = _ShockSimCfg(scalper_shock_trigger_pct=1.0)
        for direction in (Direction.LONG, Direction.SHORT):
            trades, missed = _simulate(cfg, None, direction)
            assert trades == []
            assert missed.get(REASON_SHOCK_NODATA, 0) > 0

    def test_leader_without_inputs_is_nodata(self):
        """Lider serisi var ama karar anında girdi yok (seri sonradan başlıyor)."""
        late = self._leader(103.0)
        late = dataclasses.replace(
            late, entry_close_times=[t + 10 * _DAY_MS for t in late.entry_close_times]
        )
        cfg = _ShockSimCfg(scalper_shock_trigger_pct=1.0)
        trades, missed = _simulate(cfg, late)
        assert trades == [] and missed.get(REASON_SHOCK_NODATA, 0) > 0

    def test_off_is_inert_and_identical_to_baseline(self):
        base_trades, base_missed = _simulate(_ShockSimCfg(), self._leader(103.0))
        none_trades, none_missed = _simulate(_ShockSimCfg(), None)
        assert [t.to_dict() for t in base_trades] == [t.to_dict() for t in none_trades]
        assert not any(k.startswith("shock_trigger") for k in base_missed | none_missed)

    def test_works_when_market_gate_is_off(self):
        """Tetikleyici piyasa kapısından bağımsız (kapı kapalı, tetik açık)."""
        cfg = _ShockSimCfg(scalper_market_gate=False, scalper_shock_trigger_pct=1.0)
        _, missed = _simulate(cfg, self._leader(103.0), Direction.SHORT)
        assert missed.get(REASON_SHOCK_DIR, 0) > 0
        assert "market_gate_day" not in missed


class TestCausalityHarness:
    """PLAN §3.2 nedensellik (b)."""

    def _build(self, perturb_after: Optional[int]) -> LeaderSeries:
        c15, c30, c1d = _synthetic_raw(drift=0.0006, seed=11)
        if perturb_after is not None:
            c15 = _mutate_after(c15, perturb_after, 100.0)
            c30 = _mutate_after(c30, perturb_after, 100.0)
            c1d = _mutate_after(c1d, perturb_after, 100.0)
        return _leader_from_raw(c15, c30, c1d)

    def test_trades_before_T_do_not_depend_on_leader_after_T(self):
        cfg = _ShockSimCfg(scalper_shock_trigger_pct=0.3)
        T = _D1 + 14 * 3_600_000 - 1
        base_trades, base_missed = _simulate(cfg, self._build(None))
        pert_trades, pert_missed = _simulate(cfg, self._build(T))

        def pre(trades):
            return [t.to_dict() for t in trades if t.signal_close_time <= T]

        assert len(pre(base_trades)) >= 1  # test boş değil
        assert pre(base_trades) == pre(pert_trades)
        # sanity: T sonrası lider düzleştirilince (|d| ≈ 0, sonra çok negatif)
        # gelecek kararlar GERÇEKTEN değişiyor → test algılayıcı
        post = lambda ts: [t.to_dict() for t in ts if t.signal_close_time > T]
        assert post(base_trades) != post(pert_trades) or base_missed != pert_missed


# ==========================================================================
# 5) Rapor yardımcısı: shock_trigger_activity
# ==========================================================================

class TestShockTriggerActivity:
    def _leader(self) -> LeaderSeries:
        # Gün 1: 30m kapanışları 100 → artan; gün açılışı 100.
        c15 = [_candle(_D1 + k * _M15, _M15, 100.0, 100.0) for k in range(96)]
        closes = [100.0] * 10 + [102.0] * 10 + [96.0] * 10 + [100.5] * 18
        c30 = [_candle(_D1 + k * _M30, _M30, closes[k], closes[k]) for k in range(48)]
        c1d = [
            _candle(_D1 - _DAY_MS, _DAY_MS, 99.0, 99.5),
            _candle(_D1, _DAY_MS, 100.0, 100.5),
            _candle(_D1 + _DAY_MS, _DAY_MS, 100.5, 101.0),
        ]
        return _leader_from_raw(c15, c30, c1d)

    def test_counts_on_points_and_days(self):
        leader = self._leader()
        out = shock_trigger_activity(leader, _D1, _D1 + _DAY_MS, 1.5)
        assert out["pct"] == 1.5 and out["leader"] == "BTCUSDT"
        assert out["window_start_ms"] == _D1 and out["window_end_ms"] == _D1 + _DAY_MS
        assert out["decision_points"] == 48
        assert out["nodata_points"] == 0
        on = out["on_points"]
        # k=10..19 → LONG (102 ≥ +1.5%), k=20..29 → SHORT (96 ≤ −1.5%)
        assert [d for _, d in on] == ["LONG"] * 10 + ["SHORT"] * 10
        assert [t for t, _ in on] == sorted(t for t, _ in on)
        assert on[0][0] == _D1 + 10 * _M30 + _M30 - 1
        assert out["days"] == [{"date": "1970-01-01", "open": 100.0, "close": 100.5}] or (
            out["days"][0]["open"] == 100.0 and out["days"][0]["close"] == 100.5
        )
        assert len(out["days"]) == 1

    def test_window_is_half_open_and_nodata_is_counted(self):
        leader = self._leader()
        # pencere yalnız ilk 3 karar noktası: [D1, D1 + 3*30m)
        out = shock_trigger_activity(leader, _D1, _D1 + 3 * _M30, 1.5)
        assert out["decision_points"] == 3
        # pencere seriden ÖNCE: karar noktası yok
        empty = shock_trigger_activity(leader, _D1 - 5 * _DAY_MS, _D1 - 4 * _DAY_MS, 1.5)
        assert empty["decision_points"] == 0 and empty["on_points"] == []
        # girdi olmayan seri: her nokta nodata
        broken = dataclasses.replace(leader, intraday_open_times=[], intraday_opens=[],
                                     intraday_close_times=[], daily_close_times=[],
                                     daily_closes=[])
        nd = shock_trigger_activity(broken, _D1, _D1 + _DAY_MS, 1.5)
        assert nd["nodata_points"] == nd["decision_points"] == 48
        assert nd["on_points"] == []

    def test_days_use_open_time_in_half_open_window(self):
        leader = self._leader()
        out = shock_trigger_activity(leader, _D1 - _DAY_MS, _D1 + 2 * _DAY_MS, 1.5)
        assert [d["open"] for d in out["days"]] == [99.0, 100.0, 100.5]
        out2 = shock_trigger_activity(leader, _D1, _D1 + _DAY_MS, 1.5)
        assert [d["open"] for d in out2["days"]] == [100.0]

    def test_report_key_only_when_trigger_on(self, tmp_path):
        import json
        import src.strategies.scalper.backtest as bt

        def write(meta):
            path = bt.write_json_report(
                [], days=1, symbols=["BTCUSDT"], strategy_names="C",
                cfg=SimpleNamespace(), run_metadata=meta, output_dir=tmp_path,
            )
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)

        assert "shock_trigger" not in write({})
        block = {"pct": 3.0, "leader": "BTCUSDT", "tf": "30m", "on_points": []}
        assert write({"shock_trigger": block})["shock_trigger"] == block


# ==========================================================================
# 6) Canlı motor
# ==========================================================================

class TestEngineShockTrigger:
    @staticmethod
    def _engine(cfg, snapshot):
        engine = _bare_engine(cfg, fetcher=None)
        calls: List[Optional[float]] = []

        async def fake_snapshot(max_age=None):
            calls.append(max_age)
            return snapshot

        engine._leader_market_snapshot = fake_snapshot
        return engine, calls

    @pytest.mark.parametrize("direction", [Direction.LONG, Direction.SHORT, "LONG", "SHORT"])
    @pytest.mark.parametrize("last", [96.0, 99.0, 100.0, 103.0, 103.5])
    async def test_matches_pure_function_on_same_inputs(self, direction, last):
        cfg = SimpleNamespace(scalper_shock_trigger_pct=3.0, scalper_scan_interval_seconds=60)
        engine, _ = self._engine(cfg, {"day_open": 100.0, "last_close": last})
        got = await engine._shock_trigger_reason(direction)
        assert got == evaluate_shock_trigger(direction, 100.0, last, cfg)

    async def test_snapshot_none_is_nodata(self):
        cfg = SimpleNamespace(scalper_shock_trigger_pct=3.0, scalper_scan_interval_seconds=60)
        engine, calls = self._engine(cfg, None)
        assert await engine._shock_trigger_reason(Direction.LONG) == REASON_SHOCK_NODATA
        assert len(calls) == 1

    async def test_snapshot_with_missing_fields_is_nodata(self):
        cfg = SimpleNamespace(scalper_shock_trigger_pct=3.0, scalper_scan_interval_seconds=60)
        engine, _ = self._engine(cfg, {"day_open": None, "last_close": 100.0})
        assert await engine._shock_trigger_reason(Direction.SHORT) == REASON_SHOCK_NODATA

    async def test_disabled_returns_none_and_never_fetches(self):
        cfg = SimpleNamespace(scalper_shock_trigger_pct=0.0, scalper_scan_interval_seconds=60)
        engine, calls = self._engine(cfg, {"day_open": 100.0, "last_close": 150.0})
        assert await engine._shock_trigger_reason(Direction.LONG) is None
        assert calls == []

    def test_leader_data_needed_follows_either_switch(self):
        def need(**kw):
            return _bare_engine(SimpleNamespace(**kw), None)._leader_data_needed()

        assert need(scalper_market_gate=False, scalper_shock_trigger_pct=0.0) is False
        assert need(scalper_market_gate=True, scalper_shock_trigger_pct=0.0) is True
        assert need(scalper_market_gate=False, scalper_shock_trigger_pct=1.5) is True
        assert need() is False

    async def test_refresh_only_runs_when_needed(self):
        calls: List[Any] = []

        async def run(**kw):
            engine = _bare_engine(SimpleNamespace(**kw), None)

            async def fake(max_age=None):
                calls.append(max_age)
                return None

            engine._leader_market_snapshot = fake
            await engine._refresh_leader_snapshot()

        await run(scalper_market_gate=False, scalper_shock_trigger_pct=0.0)
        assert calls == []
        await run(scalper_market_gate=False, scalper_shock_trigger_pct=1.5)
        assert calls == [0.0]


@dataclass
class _ShockEvalCfg(_EvalCfg):
    scalper_market_gate: bool = False
    scalper_shock_trigger_pct: float = 1.0


class _LongStrategy:
    name = "C"

    def __init__(self, direction: Direction):
        self.direction = direction

    def evaluate(self, ctx):
        return ScalpSignal(
            strategy="C", symbol=ctx.symbol, direction=self.direction,
            entry_price=ctx.current_price, stop_price=ctx.current_price * 0.99,
            reason="test", regime=ctx.regime, atr_5m=1.0,
        )


class TestShockTriggerInEvaluateSymbol:
    """`_evaluate_symbol` kapıdan sonra çalışmaz: engeller + niyet kaydı."""

    @pytest.fixture(autouse=True)
    def _clock(self, monkeypatch):
        monkeypatch.setattr(engine_module, "time", _FakeClock(wall_ms=_DECISION_MS))

    async def _run(self, cfg, strategy):
        symbol_reservations.clear()
        try:
            fetcher = _EvalFetcher(
                {("BTCUSDT", "1d"): _LEADER_DAILY, ("BTCUSDT", "1m"): _LEADER_ENTRY,
                 ("BTCUSDT", "15m"): _LEADER_INTRADAY},
                _DECISION_MS,
            )
            engine = _bare_engine(cfg, fetcher)
            engine.executor = _NeverBlockedExecutor()
            engine.exits = _NoPositionsExits()
            engine._entry_lock = asyncio.Lock()
            engine._opening_symbols = set()
            engine._regimes = {}
            engine._regime_cache = {}
            infos: List[str] = []
            records: List[Dict[str, Any]] = []
            engine.logger = SimpleNamespace(
                info=lambda msg, *a, **kw: infos.append(msg),
                warning=lambda *a, **kw: None,
                error=lambda *a, **kw: None,
            )
            engine._record_intent = lambda **kw: records.append(kw)
            await engine._evaluate_symbol("ETHUSDT", [strategy])
            return infos, records, fetcher
        finally:
            symbol_reservations.clear()

    async def test_opposite_direction_is_denied_and_recorded(self):
        # Lider: gün açılışı 121.0, son kapanış 118.0 → −%2.48 → tetik SHORT.
        infos, records, _ = await self._run(_ShockEvalCfg(), _LongStrategy(Direction.LONG))
        assert any("şok tetikleyicisi" in m and "engellendi" in m for m in infos)
        assert [r.get("reason") for r in records if r.get("reason")] == [REASON_SHOCK_DIR]
        denied = [r for r in records if r.get("reason")]
        assert denied[0]["decision"] == intent.DECISION_DENY

    async def test_below_threshold_is_off_reason(self):
        cfg = _ShockEvalCfg()
        cfg.scalper_shock_trigger_pct = 5.0
        _, records, _ = await self._run(cfg, _LongStrategy(Direction.LONG))
        assert [r.get("reason") for r in records if r.get("reason")] == [REASON_SHOCK_OFF]

    async def test_leader_fetch_failure_blocks_fail_closed(self):
        symbol_reservations.clear()
        try:
            fetcher = _LeaderFetcher({}, _DECISION_MS)
            fetcher.fail_with = RuntimeError("ağ")
            engine = _bare_engine(_ShockEvalCfg(), fetcher)
            reason = await engine._shock_trigger_reason(Direction.LONG)
            assert reason == REASON_SHOCK_NODATA
        finally:
            symbol_reservations.clear()

    async def test_trigger_off_makes_no_leader_requests(self):
        cfg = _ShockEvalCfg()
        cfg.scalper_shock_trigger_pct = 0.0
        symbol_reservations.clear()
        try:
            fetcher = _EvalFetcher({}, _DECISION_MS)
            engine = _bare_engine(cfg, fetcher)
            assert await engine._shock_trigger_reason(Direction.LONG) is None
            assert fetcher.calls == []
        finally:
            symbol_reservations.clear()
