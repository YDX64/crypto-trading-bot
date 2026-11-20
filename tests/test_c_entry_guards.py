"""D42 — strateji C giriş korumaları (bağlam RSI + ATR% bandı) testleri.

Korumalar VARSAYILAN KAPALI; ölçülen büyüklükler forensics.indicator_snapshot
ile aynıdır (bağlam RSI = RSI(14) bağlam dilimin son kapanmış mumu; ATR% =
atr_5m / giriş fiyatı × 100). Motor ve harness AYNI fonksiyonu çağırır.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

import src.strategies.scalper.backtest as backtest_module
import src.strategies.scalper.engine as engine_module
from src.core.config import Settings
from src.strategies.scalper import entry_gates as eg
from src.strategies.scalper import forensics, intent
from src.strategies.scalper.backtest import build_context, simulate_symbol
from src.strategies.scalper.types import Candle, Direction, Regime
from tests.test_entry_gates import (
    _REQUIRED_SETTINGS,
    _AlwaysSignalStrategy,
    _EngineCfg,
    _ExternalSignalLike,
    _OnlyAtDecision,
    _SimCfg,
    _denied,
    _minute_candles,
    _ms,
    _run_engine,
)

_START = _ms(4, 3)
_ENV_KEYS = (
    "SCALPER_C_CTX_RSI_SHORT_MAX",
    "SCALPER_C_CTX_RSI_LONG_MIN",
    "SCALPER_C_ATR_PCT_MAX",
    "SCALPER_C_ATR_PCT_MIN",
)


@dataclass
class _GuardEngineCfg(_EngineCfg):
    scalper_c_ctx_rsi_short_max: float = 100.0
    scalper_c_ctx_rsi_long_min: float = 0.0
    scalper_c_atr_pct_max: float = 0.0
    scalper_c_atr_pct_min: float = 0.0


@dataclass
class _GuardSimCfg(_SimCfg):
    scalper_c_ctx_rsi_short_max: float = 100.0
    scalper_c_ctx_rsi_long_min: float = 0.0
    scalper_c_atr_pct_max: float = 0.0
    scalper_c_atr_pct_min: float = 0.0


def _closes(kind: str, n: int = 30) -> List[float]:
    if kind == "up":      # tek yönlü yükseliş → RSI 100
        return [100.0 + 0.5 * k for k in range(n)]
    if kind == "down":    # tek yönlü düşüş → RSI 0
        return [150.0 - 0.5 * k for k in range(n)]
    # "mix": +1, -0.6 salınımı → RSI ~ 60-65, son mum yukarı
    out, p = [], 100.0
    for k in range(n):
        p += 1.0 if k % 2 == 0 else -0.6
        out.append(p)
    return out


def _candles(kind: str, n: int = 30) -> List[Candle]:
    return _minute_candles(_closes(kind, n), _START)


def _cfg(**kw: Any) -> SimpleNamespace:
    return SimpleNamespace(**kw)


def _verdict(strategy, direction, candles, cfg, atr=1.0, price=100.0):
    return eg.c_entry_guard_verdict(strategy, direction, candles, atr, price, cfg)


# ==========================================================================
# 1) Saf fonksiyon — tanımlar, eşikler, kapalı = inert
# ==========================================================================

class TestPureGuards:
    def test_off_by_default_never_blocks(self):
        for cfg in (_cfg(), _GuardEngineCfg(), Settings(_env_file=None, **_REQUIRED_SETTINGS)):
            assert eg.c_entry_guards_enabled(cfg) is False
            for kind in ("up", "down", "mix"):
                for side in (Direction.LONG, Direction.SHORT):
                    assert _verdict("C", side, _candles(kind), cfg, atr=50.0) == (None, {})

    def test_context_rsi_definition_matches_forensics_snapshot(self):
        candles = _candles("mix")
        ctx = SimpleNamespace(candles_5m=[], candles_15m=candles, candles_4h=[])
        snap = forensics.indicator_snapshot(ctx)
        assert snap["rsi_context"] == round(eg.context_rsi_of(candles), 2)

    def test_atr_pct_definition_matches_forensics_snapshot(self):
        c5 = _candles("mix")
        ctx = SimpleNamespace(
            candles_5m=c5, candles_15m=[], candles_4h=[], atr_5m=0.37, current_price=c5[-1].close,
        )
        snap = forensics.indicator_snapshot(ctx)
        assert snap["atr_pct"] == round(eg.atr_pct_of(0.37, c5[-1].close), 3)

    def test_short_refused_at_or_above_short_max(self):
        candles = _candles("mix")
        rsi = eg.context_rsi_of(candles)
        assert 50.0 < rsi < 99.0
        refuse = _cfg(scalper_c_ctx_rsi_short_max=rsi)           # eşit → RED
        allow = _cfg(scalper_c_ctx_rsi_short_max=rsi + 0.01)     # eşiğin altı → serbest
        assert _verdict("C", Direction.SHORT, candles, refuse)[0] == eg.REASON_C_CTX_RSI
        assert _verdict("C", Direction.SHORT, candles, allow)[0] is None
        # SHORT eşiği LONG'u ETKİLEMEZ
        assert _verdict("C", Direction.LONG, candles, refuse)[0] is None

    def test_long_refused_at_or_below_long_min(self):
        candles = _candles("mix")
        rsi = eg.context_rsi_of(candles)
        refuse = _cfg(scalper_c_ctx_rsi_long_min=rsi)            # eşit → RED
        allow = _cfg(scalper_c_ctx_rsi_long_min=rsi - 0.01)
        assert _verdict("C", Direction.LONG, candles, refuse)[0] == eg.REASON_C_CTX_RSI
        assert _verdict("C", Direction.LONG, candles, allow)[0] is None
        assert _verdict("C", Direction.SHORT, candles, refuse)[0] is None

    def test_candidate_values_60_40(self):
        cfg = _cfg(scalper_c_ctx_rsi_short_max=60.0, scalper_c_ctx_rsi_long_min=40.0)
        assert _verdict("C", Direction.SHORT, _candles("up"), cfg)[0] == eg.REASON_C_CTX_RSI    # RSI 100
        assert _verdict("C", Direction.LONG, _candles("down"), cfg)[0] == eg.REASON_C_CTX_RSI   # RSI 0
        assert _verdict("C", Direction.SHORT, _candles("down"), cfg)[0] is None
        assert _verdict("C", Direction.LONG, _candles("up"), cfg)[0] is None

    def test_atr_band_both_sides(self):
        cfg = _cfg(scalper_c_atr_pct_max=0.12, scalper_c_atr_pct_min=0.05)
        c = _candles("mix")
        # price 100: ATR% = atr
        assert _verdict("C", Direction.LONG, c, cfg, atr=0.12)[0] == eg.REASON_C_ATR_BAND   # >= MAX
        assert _verdict("C", Direction.LONG, c, cfg, atr=0.1199)[0] is None
        assert _verdict("C", Direction.SHORT, c, cfg, atr=0.05)[0] is None                  # tam MIN geçer
        assert _verdict("C", Direction.SHORT, c, cfg, atr=0.0499)[0] == eg.REASON_C_ATR_BAND  # < MIN
        only_max = _cfg(scalper_c_atr_pct_max=0.12)
        assert _verdict("C", Direction.LONG, c, only_max, atr=0.001)[0] is None
        only_min = _cfg(scalper_c_atr_pct_min=0.05)
        assert _verdict("C", Direction.LONG, c, only_min, atr=9.0)[0] is None

    def test_other_strategies_unaffected(self):
        cfg = _cfg(
            scalper_c_ctx_rsi_short_max=1.0, scalper_c_ctx_rsi_long_min=99.0,
            scalper_c_atr_pct_max=0.01, scalper_c_atr_pct_min=50.0,
        )
        for strat in ("A", "B", "D", "TV", "", None):
            for side in (Direction.LONG, Direction.SHORT):
                assert _verdict(strat, side, _candles("up"), cfg) == (None, {})
        assert _verdict("C", Direction.SHORT, _candles("up"), cfg)[0] is not None

    def test_unmeasurable_inputs_fail_open(self):
        cfg = _cfg(
            scalper_c_ctx_rsi_short_max=1.0, scalper_c_ctx_rsi_long_min=99.0,
            scalper_c_atr_pct_max=0.01, scalper_c_atr_pct_min=50.0,
        )
        short_series = _candles("up", n=14)           # RSI(14) için yetersiz
        assert eg.context_rsi_of(short_series) is None
        assert eg.context_rsi_of([]) is None
        assert eg.context_rsi_of(None) is None
        assert _verdict("C", Direction.SHORT, short_series, cfg, atr=0.0)[0] is None
        assert _verdict("C", Direction.LONG, None, cfg, atr=None)[0] is None

    def test_reason_strings_and_labels(self):
        assert eg.REASON_C_CTX_RSI == intent.REASON_C_CTX_RSI_GUARD == "c_ctx_rsi_guard"
        assert eg.REASON_C_ATR_BAND == intent.REASON_C_ATR_BAND == "c_atr_band"
        for r in (eg.REASON_C_CTX_RSI, eg.REASON_C_ATR_BAND):
            assert r in intent.KNOWN_REASONS
            assert intent.REASON_LABELS[r].strip()
            assert eg.REASON_ENV_VARS[r]

    def test_general_gate_order_and_signature_unchanged(self):
        # C korumaları genel kapı zincirine GİRMEZ: kapalı genel kapılar + açık
        # C koruması → evaluate_entry_gates hâlâ None.
        cfg = _cfg(scalper_c_atr_pct_max=0.01)
        assert eg.evaluate_entry_gates(Regime.UP, Direction.LONG, _ms(4, 3), 5.0, 100.0, "ETHUSDT", cfg) is None
        assert eg.entry_gates_enabled(cfg) is False


# ==========================================================================
# 2) Config doğrulaması
# ==========================================================================

class TestSettings:
    @staticmethod
    def _settings(monkeypatch, env=None, **fields) -> Settings:
        for key in list(os.environ):
            if key.upper() in _ENV_KEYS:
                monkeypatch.delenv(key, raising=False)
        for k, v in (env or {}).items():
            monkeypatch.setenv(k, v)
        return Settings(_env_file=None, **_REQUIRED_SETTINGS, **fields)

    def test_defaults_off(self, monkeypatch):
        s = self._settings(monkeypatch)
        assert s.scalper_c_ctx_rsi_short_max == 100.0
        assert s.scalper_c_ctx_rsi_long_min == 0.0
        assert s.scalper_c_atr_pct_max == 0.0
        assert s.scalper_c_atr_pct_min == 0.0
        assert eg.c_entry_guards_enabled(s) is False

    def test_env_parse(self, monkeypatch):
        s = self._settings(monkeypatch, env={
            "SCALPER_C_CTX_RSI_SHORT_MAX": "60", "SCALPER_C_CTX_RSI_LONG_MIN": "40",
            "SCALPER_C_ATR_PCT_MAX": "0.12", "SCALPER_C_ATR_PCT_MIN": "0.05",
        })
        assert (s.scalper_c_ctx_rsi_short_max, s.scalper_c_ctx_rsi_long_min) == (60.0, 40.0)
        assert (s.scalper_c_atr_pct_max, s.scalper_c_atr_pct_min) == (0.12, 0.05)
        assert eg.c_entry_guards_enabled(s) is True

    @pytest.mark.parametrize(
        "env",
        [
            {"SCALPER_C_CTX_RSI_SHORT_MAX": "101"},
            {"SCALPER_C_CTX_RSI_SHORT_MAX": "-1"},
            {"SCALPER_C_CTX_RSI_LONG_MIN": "100.5"},
            {"SCALPER_C_CTX_RSI_LONG_MIN": "-5"},
            {"SCALPER_C_ATR_PCT_MAX": "-0.1"},
            {"SCALPER_C_ATR_PCT_MIN": "-0.1"},
            {"SCALPER_C_ATR_PCT_MIN": "0.2", "SCALPER_C_ATR_PCT_MAX": "0.1"},
            {"SCALPER_C_ATR_PCT_MIN": "0.1", "SCALPER_C_ATR_PCT_MAX": "0.1"},
        ],
    )
    def test_invalid_fails_fast(self, monkeypatch, env):
        with pytest.raises(ValueError):
            self._settings(monkeypatch, env=env)

    def test_valid_edges_ok(self, monkeypatch):
        self._settings(monkeypatch, env={
            "SCALPER_C_CTX_RSI_SHORT_MAX": "0", "SCALPER_C_CTX_RSI_LONG_MIN": "100",
            "SCALPER_C_ATR_PCT_MIN": "0.05", "SCALPER_C_ATR_PCT_MAX": "0.06",
        })
        # tek uç açıkken MIN<MAX şartı aranmaz
        self._settings(monkeypatch, env={"SCALPER_C_ATR_PCT_MIN": "5"})

    def test_harness_validates_too(self):
        with pytest.raises(ValueError):
            simulate_symbol(
                "ETHUSDT", _candles("up"), _candles("up"), _candles("up"), [],
                _GuardSimCfg(scalper_c_atr_pct_min=0.3, scalper_c_atr_pct_max=0.1),
            )

    def test_env_example_ships_disabled(self):
        text = Path("env.example").read_text(encoding="utf-8")
        for key in _ENV_KEYS:
            assert key in text
            for line in text.splitlines():
                assert not line.strip().startswith(f"{key}=")
        assert eg.c_entry_guards_enabled(Settings(_env_file="env.example")) is False


# ==========================================================================
# 3) Canlı motor + harness — aynı mumlar, aynı karar, niyet kaydı
# ==========================================================================

def _decision(candles: List[Candle]):
    """Harness: son mum dolum mumu → karar mumu = sondan bir önceki."""
    idx = len(candles) - 2
    return idx, candles[idx].close_time


async def _engine_denied(cfg, direction, candles, strategy_cls=_AlwaysSignalStrategy, atr=1.0):
    _, infos, intents = await _run_engine(
        cfg, strategy_cls(direction, atr), candles=candles
    )
    return infos, _denied(intents)


def _harness_missed(cfg, direction, candles, atr=1.0) -> Dict[str, int]:
    _, ms = _decision(candles)
    missed: Dict[str, int] = {}
    strat = _OnlyAtDecision(direction, ms)
    strat.atr_5m = atr
    simulate_symbol("ETHUSDT", candles, candles, candles, [strat], cfg, missed_counter=missed)
    return missed


class TestEngineAndHarness:
    def test_same_function_object_in_engine_and_harness(self):
        assert engine_module.c_entry_guard_verdict is eg.c_entry_guard_verdict
        assert backtest_module.c_entry_guard_verdict is eg.c_entry_guard_verdict

    @pytest.mark.parametrize(
        "kind,direction,kw,expected",
        [
            ("up", Direction.SHORT, dict(scalper_c_ctx_rsi_short_max=60.0), eg.REASON_C_CTX_RSI),
            ("down", Direction.LONG, dict(scalper_c_ctx_rsi_long_min=40.0), eg.REASON_C_CTX_RSI),
            ("up", Direction.LONG, dict(scalper_c_ctx_rsi_short_max=60.0, scalper_c_ctx_rsi_long_min=40.0), None),
            ("mix", Direction.LONG, dict(scalper_c_atr_pct_max=0.5), eg.REASON_C_ATR_BAND),
            ("mix", Direction.LONG, dict(scalper_c_atr_pct_min=5.0), eg.REASON_C_ATR_BAND),
            ("mix", Direction.LONG, dict(scalper_c_atr_pct_min=0.5, scalper_c_atr_pct_max=5.0), None),
        ],
    )
    async def test_live_and_harness_agree(self, kind, direction, kw, expected):
        candles = _candles(kind)
        engine_candles = candles[: len(candles) - 1]  # son mum dolum mumu: karar mumu sonu
        assert engine_candles[-1].close_time == _decision(candles)[1]
        # canlı: aynı mumlar (karar mumuna kadar), ATR% = 1.0/~110
        atr = 1.0
        if expected is None:
            # kapı serbest → apply_stop_policy'ye ulaşır (nöbetçi), ret yok
            def _boom(sig, c):
                raise RuntimeError("reached_stop_policy")
            orig = engine_module.apply_stop_policy
            engine_module.apply_stop_policy = _boom
            try:
                with pytest.raises(RuntimeError, match="reached_stop_policy"):
                    await _engine_denied(_GuardEngineCfg(**kw), direction, engine_candles, atr=atr)
            finally:
                engine_module.apply_stop_policy = orig
            live = None
        else:
            _, denied = await _engine_denied(_GuardEngineCfg(**kw), direction, engine_candles, atr=atr)
            assert len(denied) == 1
            live = denied[0]["reason"]
        missed = _harness_missed(_GuardSimCfg(**kw), direction, candles, atr=atr)
        harness = next(iter(missed), None) if missed else None
        harness = harness if harness in (eg.REASON_C_CTX_RSI, eg.REASON_C_ATR_BAND) else None
        assert live == expected == harness

    async def test_live_writes_intent_reason_and_detail(self):
        candles = _candles("up")[:-1]
        infos, denied = await _engine_denied(
            _GuardEngineCfg(scalper_c_ctx_rsi_short_max=60.0), Direction.SHORT, candles
        )
        assert len(denied) == 1
        row = denied[0]
        assert row["reason"] == intent.REASON_C_CTX_RSI_GUARD == "c_ctx_rsi_guard"
        assert row["extra"]["gate"] == "c_ctx_rsi_guard"
        assert row["extra"]["ctx_rsi"] == 100.0
        assert row["extra"]["direction"] == "SHORT"
        assert any("bağlam RSI" in line for line in infos)

        _, denied = await _engine_denied(
            _GuardEngineCfg(scalper_c_atr_pct_max=0.5), Direction.LONG, _candles("mix")[:-1]
        )
        assert denied[0]["reason"] == "c_atr_band"
        assert denied[0]["extra"]["atr_pct"] > 0.5
        assert denied[0]["extra"]["atr_pct_max"] == 0.5

    async def test_tv_external_signal_not_guarded(self):
        """Yalnız strateji C: TV dış sinyali (strategy != 'C') korumadan geçmez."""
        class _TV(_ExternalSignalLike):
            def evaluate(self, ctx):
                sig = super().evaluate(ctx)
                return sig.__class__(**{**sig.__dict__, "strategy": "TV"})

        _TV.__name__ = "_ExternalSignalStrategy"

        def _boom(sig, c):
            raise RuntimeError("reached_stop_policy")
        orig = engine_module.apply_stop_policy
        engine_module.apply_stop_policy = _boom
        try:
            with pytest.raises(RuntimeError, match="reached_stop_policy"):
                await _run_engine(
                    _GuardEngineCfg(scalper_c_ctx_rsi_short_max=1.0, scalper_c_atr_pct_max=0.001),
                    _TV(Direction.SHORT), candles=_candles("up")[:-1],
                )
        finally:
            engine_module.apply_stop_policy = orig

    async def test_guard_failure_is_fail_open_live(self, monkeypatch):
        def _raise(*a, **k):
            raise RuntimeError("kapı arızası")
        monkeypatch.setattr(engine_module, "c_entry_guard_verdict", _raise)
        monkeypatch.setattr(
            engine_module, "apply_stop_policy",
            lambda s, c: (_ for _ in ()).throw(RuntimeError("reached_stop_policy")),
        )
        with pytest.raises(RuntimeError, match="reached_stop_policy"):
            await _run_engine(
                _GuardEngineCfg(scalper_c_ctx_rsi_short_max=60.0), _AlwaysSignalStrategy(Direction.SHORT),
                candles=_candles("up")[:-1],
            )

    def test_defaults_leave_harness_output_identical(self):
        """Alanları HİÇ taşımayan cfg ile varsayılan (kapalı) alanlı cfg →
        işlem dizisi ve missed_counter BİREBİR aynı."""
        candles = _candles("mix")
        _, ms = _decision(candles)

        def run(cfg):
            missed: Dict[str, int] = {}
            trades = simulate_symbol(
                "ETHUSDT", candles, candles, candles, [_OnlyAtDecision(Direction.LONG, ms)],
                cfg, missed_counter=missed,
            )
            fp = [(t.entry_time, round(t.entry_price, 8), t.exit_reason, round(t.pnl, 8)) for t in trades]
            return fp, missed

        assert run(_SimCfg()) == run(_GuardSimCfg())

    def test_no_look_ahead_context_slice(self):
        """Karar çubuğundan SONRAKİ mumlar (uç değerli) eklense ya da kesilse
        koruma kararı ve ölçülen bağlam RSI'sı değişmez."""
        base = _candles("mix", n=40)
        idx = 30
        future_junk = _minute_candles([1.0, 9999.0, 1.0, 9999.0, 1.0], base[-1].close_time + 1)
        long_series = base + future_junk
        cfg = _cfg(scalper_c_ctx_rsi_short_max=60.0, scalper_c_ctx_rsi_long_min=40.0,
                   scalper_c_atr_pct_max=0.12)
        verdicts = []
        for series in (base[: idx + 2], base, long_series):
            ctx = build_context("ETHUSDT", series, series, series, idx, 10)
            assert ctx.candles_15m[-1].close_time == base[idx].close_time
            verdicts.append((
                eg.context_rsi_of(ctx.candles_15m),
                [_verdict("C", d, ctx.candles_15m, cfg, atr=0.05)[0] for d in (Direction.LONG, Direction.SHORT)],
            ))
        assert verdicts[0] == verdicts[1] == verdicts[2]

    def test_no_look_ahead_harness_counter(self):
        base = _candles("up", n=40)
        idx = len(base) - 2
        ms = base[idx].close_time
        cfg = _GuardSimCfg(scalper_c_ctx_rsi_short_max=60.0)

        def run(series):
            missed: Dict[str, int] = {}
            simulate_symbol("ETHUSDT", series, series, series, [_OnlyAtDecision(Direction.SHORT, ms)],
                            cfg, missed_counter=missed)
            return missed

        junk = _minute_candles([1.0, 9999.0, 1.0], base[-1].close_time + 1)
        assert run(base) == run(base + junk) == {eg.REASON_C_CTX_RSI: 1}


# ==========================================================================
# 4) Ölçülemeyen girdi canlıda SESSİZ kalmaz (uyarı loglanır, fail-open sürer)
# ==========================================================================

async def _engine_warnings(cfg, direction, candles, atr, monkeypatch):
    from tests.test_entry_gates import _FlatFetcher, _bare_engine, symbol_reservations

    class _Reached(Exception):
        pass

    def _boom(sig, c):
        raise _Reached()

    monkeypatch.setattr(engine_module, "apply_stop_policy", _boom)
    symbol_reservations.clear()
    try:
        engine, _, intents = _bare_engine(cfg, _FlatFetcher(candles), None)
        strat = _AlwaysSignalStrategy(direction, atr)
        with pytest.raises(_Reached):  # kapı uygulanmadı → giriş yoluna ilerledi
            await engine._evaluate_symbol("ETHUSDT", [strat])
    finally:
        symbol_reservations.clear()
    assert not _denied(intents)
    return engine._test_warnings


from tests.test_entry_gates import _CANDLES as _ENGINE_SHORT  # 6 mum: RSI(14) ölçülemez


class TestUnmeasurableLogged:
    async def test_ctx_rsi_unmeasurable_warns(self, monkeypatch):
        cfg = _GuardEngineCfg(scalper_c_ctx_rsi_short_max=60.0)
        warns = await _engine_warnings(cfg, Direction.SHORT, _ENGINE_SHORT, 1.0, monkeypatch)
        assert any("bağlam RSI" in w and "fail-open" in w for w in warns)

    async def test_atr_unmeasurable_warns(self, monkeypatch):
        cfg = _GuardEngineCfg(scalper_c_atr_pct_max=0.5)
        warns = await _engine_warnings(cfg, Direction.LONG, _ENGINE_SHORT, 0.0, monkeypatch)
        assert any("ATR%" in w and "fail-open" in w and "SCALPER_C_ATR" in w for w in warns)

    async def test_measurable_inputs_do_not_warn(self, monkeypatch):
        cfg = _GuardEngineCfg(scalper_c_ctx_rsi_short_max=90.0, scalper_c_atr_pct_max=5.0)
        warns = await _engine_warnings(cfg, Direction.SHORT, _candles("mix")[:-1], 1.0, monkeypatch)
        assert not [w for w in warns if "fail-open" in w]

    async def test_guards_off_never_warn(self, monkeypatch):
        warns = await _engine_warnings(
            _GuardEngineCfg(), Direction.SHORT, _ENGINE_SHORT, 0.0, monkeypatch
        )
        assert not warns
