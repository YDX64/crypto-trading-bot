"""Lider piyasa kapısı ("ters-gün kapısı", D15, spec §C) testleri.

Beş katman:
  1. `market_gate.evaluate_market_gate` — SAF fonksiyon: her alt-kapı, her
     yön, eşik SINIRLARI, eksik/None/sonsuz veri, kapı kapalı.
  2. `market_gate.day_open_from_daily_closes` / `market_gate_metrics` —
     ortak girdi türetme ve teşhis büyüklükleri.
  3. `ScalperEngine._market_gate_reason` / `_leader_market_snapshot` /
     `_market_gate_status` — AĞ YOK (sahte fetcher `KlineFetcher`'ın
     sayfalama + `_drop_unclosed` davranışını birebir taklit eder):
     önbellek (lider başına 60 sn), fail-open + WARNING, ret sayaçları,
     `/scalper/status` alanı. Ayrıca kapının GERÇEK `_evaluate_symbol`
     üzerinden — yani C taramasının VE TV dış sinyalinin ORTAK tek giriş
     noktasından — engellediği.
  4. `backtest.LeaderSeries` / `simulate_symbol` — harness tarafı: look-ahead
     yasağı, `missed_counter` anahtarları, kapı kapalıyken hiç etki yok.
  5. **Parite (CLAUDE.md kural 2, DECISIONS P1)** — motor ve harness AYNI
     fonksiyon NESNESİNİ, AYNI argümanlarla çağırır (casus/spy ile
     argüman-düzeyinde karşılaştırma), ve aynı kararı üretir.

Ek: `Settings` env parse (SCALPER_MARKET_GATE*) ve varsayılanların KAPALI
olduğu — golden backtest'in (tests/test_golden_backtest.py) değişmeden
geçmesinin ön koşulu.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import pytest

import src.strategies.scalper.backtest as backtest_module
import src.strategies.scalper.engine as engine_module
from src.core.config import Settings
from src.strategies.scalper import market_gate as market_gate_module
from src.strategies.scalper.backtest import LeaderSeries, simulate_symbol
from src.strategies.scalper.engine import ScalperEngine
from src.strategies.scalper.market_gate import (
    REASON_DAY,
    REASON_RUN,
    day_open_from_daily_closes,
    evaluate_market_gate,
    market_gate_metrics,
)
from src.strategies.scalper.types import (
    Candle,
    Direction,
    ScalpSignal,
    StrategyContext,
)
from src.trading.symbol_reservations import symbol_reservations

_DAY_MS = 86_400_000
_MIN_MS = 60_000


@dataclass
class _GateCfg:
    """Kapı ayarlarını taşıyan minimal cfg (pydantic'e gerek yok)."""

    scalper_market_gate: bool = True
    scalper_market_gate_symbol: str = "BTCUSDT"
    scalper_market_gate_day_pct: float = 1.0
    scalper_market_gate_run_pct: float = 15.0
    scalper_market_gate_run_days: int = 3


def _day_only(day_pct: float = 1.0) -> _GateCfg:
    return _GateCfg(scalper_market_gate_day_pct=day_pct, scalper_market_gate_run_pct=0.0)


def _run_only(run_pct: float = 15.0, run_days: int = 3) -> _GateCfg:
    return _GateCfg(
        scalper_market_gate_day_pct=0.0,
        scalper_market_gate_run_pct=run_pct,
        scalper_market_gate_run_days=run_days,
    )


# ==========================================================================
# 1) Saf fonksiyon
# ==========================================================================

class TestEvaluateMarketGateDisabled:
    def test_gate_off_never_blocks(self):
        cfg = _GateCfg(scalper_market_gate=False)
        # Hem gün-içi hem uzama eşiğini FAZLASIYLA aşan veri:
        assert evaluate_market_gate(
            Direction.LONG, 100.0, 90.0, [50.0, 60.0, 80.0, 90.0], cfg
        ) is None

    def test_missing_config_object_defaults_to_off(self):
        """Alanları hiç olmayan bir cfg (eski test çiftleri) kapıyı AÇMAZ."""
        assert evaluate_market_gate(
            Direction.LONG, 100.0, 90.0, [50.0, 90.0], SimpleNamespace()
        ) is None

    def test_unknown_direction_is_ignored(self):
        assert evaluate_market_gate(
            "FLAT", 100.0, 90.0, [50.0, 60.0, 80.0, 90.0], _GateCfg()
        ) is None


class TestDaySubGate:
    def test_long_blocked_when_leader_below_day_open(self):
        # 100 → 98.5 = −%1.5, eşik %1 → LONG engellenir.
        assert evaluate_market_gate(
            Direction.LONG, 100.0, 98.5, None, _day_only(1.0)
        ) == REASON_DAY

    def test_short_not_blocked_when_leader_below_day_open(self):
        """Aşağı sapma yalnız LONG'u engeller — SHORT trendle uyumludur."""
        assert evaluate_market_gate(
            Direction.SHORT, 100.0, 98.5, None, _day_only(1.0)
        ) is None

    def test_short_blocked_when_leader_above_day_open(self):
        assert evaluate_market_gate(
            Direction.SHORT, 100.0, 101.5, None, _day_only(1.0)
        ) == REASON_DAY

    def test_long_not_blocked_when_leader_above_day_open(self):
        assert evaluate_market_gate(
            Direction.LONG, 100.0, 101.5, None, _day_only(1.0)
        ) is None

    def test_threshold_is_inclusive_at_exact_boundary(self):
        """Tam eşikte ENGELLENİR (spec: '≥%X altındaysa')."""
        assert evaluate_market_gate(
            Direction.LONG, 100.0, 99.0, None, _day_only(1.0)
        ) == REASON_DAY
        assert evaluate_market_gate(
            Direction.SHORT, 100.0, 101.0, None, _day_only(1.0)
        ) == REASON_DAY

    def test_just_inside_threshold_passes(self):
        assert evaluate_market_gate(
            Direction.LONG, 100.0, 99.001, None, _day_only(1.0)
        ) is None
        assert evaluate_market_gate(
            Direction.SHORT, 100.0, 100.999, None, _day_only(1.0)
        ) is None

    def test_zero_pct_disables_only_this_sub_gate(self):
        cfg = _GateCfg(scalper_market_gate_day_pct=0.0, scalper_market_gate_run_pct=0.0)
        assert evaluate_market_gate(Direction.LONG, 100.0, 50.0, None, cfg) is None

    def test_string_direction_accepted(self):
        assert evaluate_market_gate(
            "long", 100.0, 98.5, None, _day_only(1.0)
        ) == REASON_DAY

    @pytest.mark.parametrize(
        "day_open,last_close",
        [(None, 98.5), (100.0, None), (0.0, 98.5), (-5.0, 98.5),
         (float("nan"), 98.5), (100.0, float("inf")), ("abc", 98.5)],
    )
    def test_missing_or_invalid_data_skips_gate(self, day_open, last_close):
        assert evaluate_market_gate(
            Direction.LONG, day_open, last_close, None, _day_only(1.0)
        ) is None


class TestRunSubGate:
    # 3 günlük koşu closes[-1]/closes[-4]: 100 → 120 = +%20.
    _UP_CLOSES = [100.0, 105.0, 112.0, 120.0]
    _DOWN_CLOSES = [120.0, 112.0, 105.0, 100.0]

    def test_long_blocked_after_up_run(self):
        assert evaluate_market_gate(
            Direction.LONG, 120.0, 120.0, self._UP_CLOSES, _run_only(15.0, 3)
        ) == REASON_RUN

    def test_short_not_blocked_after_up_run(self):
        assert evaluate_market_gate(
            Direction.SHORT, 120.0, 120.0, self._UP_CLOSES, _run_only(15.0, 3)
        ) is None

    def test_short_blocked_after_down_run(self):
        assert evaluate_market_gate(
            Direction.SHORT, 100.0, 100.0, self._DOWN_CLOSES, _run_only(15.0, 3)
        ) == REASON_RUN

    def test_run_below_threshold_passes(self):
        closes = [100.0, 102.0, 105.0, 110.0]  # +%10 < %15
        assert evaluate_market_gate(
            Direction.LONG, 110.0, 110.0, closes, _run_only(15.0, 3)
        ) is None

    def test_threshold_is_inclusive_at_exact_boundary(self):
        """Eşik KAPSAYICI (>=).

        Not: karşılaştırma düz float'tır (epsilon YOK). %15 eşiği ikilik
        tabanda tam temsil edilmediği için (115/100−1 = 14.999999999999998)
        sınır kanıtı tam temsil edilen %25 ile verilir; bu mertebede bir
        yuvarlama farkı kapı kararını pratikte etkilemez.
        """
        closes = [100.0, 101.0, 102.0, 125.0]  # tam +%25
        assert evaluate_market_gate(
            Direction.LONG, 125.0, 125.0, closes, _run_only(25.0, 3)
        ) == REASON_RUN
        # Eşiğin bir tık altı serbest.
        assert evaluate_market_gate(
            Direction.LONG, 124.9, 124.9, [100.0, 101.0, 102.0, 124.9],
            _run_only(25.0, 3),
        ) is None

    def test_run_needs_n_plus_one_closes(self):
        """3 günlük koşu 4 kapanış ister; 3 kapanışla kapı UYGULANMAZ."""
        closes = [100.0, 112.0, 120.0]  # yalnız 3 kapanış
        assert evaluate_market_gate(
            Direction.LONG, 120.0, 120.0, closes, _run_only(15.0, 3)
        ) is None

    def test_window_uses_exactly_n_days_back(self):
        """5 kapanışta N=3 penceresi closes[-4]'ü taban alır — closes[0] DEĞİL.

        closes[0]=10 (devasa koşu) ama 3 günlük pencere 100→110 = +%10 < %15.
        """
        closes = [10.0, 100.0, 104.0, 107.0, 110.0]
        assert evaluate_market_gate(
            Direction.LONG, 110.0, 110.0, closes, _run_only(15.0, 3)
        ) is None

    def test_run_days_one_uses_previous_close(self):
        closes = [100.0, 120.0]
        assert evaluate_market_gate(
            Direction.LONG, 120.0, 120.0, closes, _run_only(15.0, 1)
        ) == REASON_RUN

    def test_zero_pct_disables_only_this_sub_gate(self):
        cfg = _GateCfg(scalper_market_gate_day_pct=0.0, scalper_market_gate_run_pct=0.0)
        assert evaluate_market_gate(
            Direction.LONG, 120.0, 120.0, self._UP_CLOSES, cfg
        ) is None

    @pytest.mark.parametrize("closes", [None, [], [0.0, 0.0, 0.0, 120.0]])
    def test_missing_or_invalid_closes_skips_gate(self, closes):
        assert evaluate_market_gate(
            Direction.LONG, 120.0, 120.0, closes, _run_only(15.0, 3)
        ) is None


class TestSubGatePrecedenceAndCombination:
    def test_day_gate_reported_first_when_both_trigger(self):
        closes = [100.0, 105.0, 112.0, 120.0]  # +%20 koşu
        # Gün-içi de tetikte (120 → 118 = −%1.67)
        assert evaluate_market_gate(
            Direction.LONG, 120.0, 118.0, closes, _GateCfg()
        ) == REASON_DAY

    def test_run_gate_still_applies_when_day_gate_passes(self):
        closes = [100.0, 105.0, 112.0, 120.0]
        assert evaluate_market_gate(
            Direction.LONG, 120.0, 120.0, closes, _GateCfg()
        ) == REASON_RUN

    def test_both_sub_gates_clear_allows_entry(self):
        closes = [100.0, 101.0, 102.0, 103.0]  # +%3
        assert evaluate_market_gate(
            Direction.LONG, 103.0, 103.0, closes, _GateCfg()
        ) is None


# ==========================================================================
# 2) Ortak girdi türetme + teşhis
# ==========================================================================

class TestDayOpenFromDailyCloses:
    def test_returns_last_completed_close(self):
        assert day_open_from_daily_closes([1.0, 2.0, 3.0]) == 3.0

    @pytest.mark.parametrize("closes", [None, [], [0.0], [-1.0], [float("nan")]])
    def test_invalid_inputs_return_none(self, closes):
        assert day_open_from_daily_closes(closes) is None


class TestMarketGateMetrics:
    def test_metrics_are_computed(self):
        m = market_gate_metrics(100.0, 98.0, [100.0, 105.0, 112.0, 120.0], _GateCfg())
        assert m["day_drift_pct"] == pytest.approx(-2.0)
        assert m["run_pct"] == pytest.approx(20.0)

    def test_unavailable_metrics_are_none_not_zero(self):
        m = market_gate_metrics(None, None, [], _GateCfg())
        assert m == {"day_drift_pct": None, "run_pct": None}


# ==========================================================================
# Ortak veri kurgusu — motor ve harness AYNI mumları görür
# ==========================================================================

def _daily_candles(closes: List[float], first_day_index: int = 0) -> List[Candle]:
    """Kapanış listesinden UTC-hizalı günlük mumlar."""
    out: List[Candle] = []
    for n, close in enumerate(closes):
        start = (first_day_index + n) * _DAY_MS
        out.append(Candle(
            open_time=start, open=close, high=close, low=close,
            close=close, volume=1.0, close_time=start + _DAY_MS - 1,
        ))
    return out


def _minute_candles(
    closes: List[float], start_ms: int, step_ms: int = _MIN_MS
) -> List[Candle]:
    out: List[Candle] = []
    for n, close in enumerate(closes):
        start = start_ms + n * step_ms
        out.append(Candle(
            open_time=start, open=close, high=close, low=close,
            close=close, volume=1.0, close_time=start + step_ms - 1,
        ))
    return out


class _LeaderFetcher:
    """`KlineFetcher`'ın kapının kullandığı sözleşmesini taklit eder:
    `end_time=None` iken "şimdi"ye kadar olan SON `limit` mumu döner ve
    KAPANMAMIŞ son mumu ATAR (`_drop_unclosed` ile aynı anlam).

    `now_ms` testin ilerlettiği sanal duvar saatidir; gerçek `time` hiç
    kullanılmaz (deterministik).
    """

    def __init__(self, series: Dict[Tuple[str, str], List[Candle]], now_ms: int):
        self.series = series
        self.now_ms = now_ms
        self.calls: List[Tuple[str, str, int]] = []
        self.fail_with: Optional[Exception] = None

    async def get_klines(self, symbol, interval, limit=200, end_time=None):
        self.calls.append((symbol, interval, limit))
        if self.fail_with is not None:
            raise self.fail_with
        candles = self.series.get((symbol, interval))
        if candles is None:
            raise AssertionError(f"Beklenmeyen seri istendi: {symbol} {interval}")
        opened = [c for c in candles if c.open_time <= self.now_ms][-limit:]
        if opened and opened[-1].close_time > self.now_ms:
            opened = opened[:-1]
        return opened


# BTC senaryosu: 4 gün +%20 koşu (100 → 120), sonra 5. gün içinde 120 → 118
# (−%1.67 gün-içi sapma). Her iki alt-kapı da LONG'u engeller.
_LEADER_DAILY_CLOSES = [100.0, 105.0, 112.0, 120.0]
_LEADER_TODAY_INDEX = len(_LEADER_DAILY_CLOSES)          # 5. gün (indeks 4)
_LEADER_TODAY_START = _LEADER_TODAY_INDEX * _DAY_MS
# Bugünün (henüz kapanmamış) günlük mumu da seride: gerçek Binance yanıtı
# gibi — fetcher onu atmalı, harness ise close_time filtresiyle görmemeli.
_LEADER_DAILY = _daily_candles(_LEADER_DAILY_CLOSES + [118.0])
_LEADER_ENTRY = _minute_candles([118.6, 118.4, 118.2, 118.0], _LEADER_TODAY_START)
# Karar anı = liderin 4. dakika mumunun kapanışı (close = 118.0).
_DECISION_MS = _LEADER_ENTRY[-1].close_time


def _leader_series() -> LeaderSeries:
    return LeaderSeries(
        symbol="BTCUSDT",
        entry_close_times=[c.close_time for c in _LEADER_ENTRY],
        entry_closes=[c.close for c in _LEADER_ENTRY],
        daily_close_times=[c.close_time for c in _LEADER_DAILY],
        daily_closes=[c.close for c in _LEADER_DAILY],
    )


def _engine_cfg(**over: Any) -> SimpleNamespace:
    base: Dict[str, Any] = dict(
        scalper_market_gate=True,
        scalper_market_gate_symbol="BTCUSDT",
        scalper_market_gate_day_pct=1.0,
        scalper_market_gate_run_pct=15.0,
        scalper_market_gate_run_days=3,
        scalper_tf_entry="1m",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _bare_engine(cfg: Any, fetcher: Any) -> ScalperEngine:
    """__init__ ATLANIR (ağ/DB yok) — yalnız kapının okuduğu alanlar kurulur."""
    engine = ScalperEngine.__new__(ScalperEngine)
    engine.cfg = cfg
    engine.fetcher = fetcher
    engine._market_gate_cache = {}
    engine._market_gate_rejects = {}
    engine._market_gate_last_reason = None
    engine.logger = SimpleNamespace(
        info=lambda *a, **kw: None,
        warning=lambda *a, **kw: None,
        error=lambda *a, **kw: None,
    )
    return engine


# ==========================================================================
# 3) Canlı motor
# ==========================================================================

class TestEngineMarketGate:
    def _fetcher(self, now_ms: int = _DECISION_MS) -> _LeaderFetcher:
        return _LeaderFetcher(
            {("BTCUSDT", "1d"): _LEADER_DAILY, ("BTCUSDT", "1m"): _LEADER_ENTRY},
            now_ms,
        )

    async def test_long_blocked_and_short_allowed(self):
        engine = _bare_engine(_engine_cfg(), self._fetcher())
        assert await engine._market_gate_reason(Direction.LONG) == REASON_DAY
        assert await engine._market_gate_reason(Direction.SHORT) is None

    async def test_gate_off_makes_no_request_at_all(self):
        """Kapı kapalıyken TEK bir REST isteği bile gitmez (ağırlık diyeti)."""
        fetcher = self._fetcher()
        engine = _bare_engine(_engine_cfg(scalper_market_gate=False), fetcher)
        assert await engine._market_gate_reason(Direction.LONG) is None
        assert fetcher.calls == []

    async def test_leader_snapshot_is_cached_per_leader(self):
        fetcher = self._fetcher()
        engine = _bare_engine(_engine_cfg(), fetcher)
        for _ in range(5):
            await engine._market_gate_reason(Direction.LONG)
        # 5 değerlendirme → yalnız 2 istek (1d + 1m), TTL içinde tazelenmez.
        assert len(fetcher.calls) == 2
        assert {c[1] for c in fetcher.calls} == {"1d", "1m"}

    async def test_daily_limit_is_run_days_plus_two(self):
        """N günlük koşu N+1 TAMAMLANMIŞ kapanış ister; oluşmakta olan günlük
        mum düşeceği için N+2 istenir."""
        fetcher = self._fetcher()
        engine = _bare_engine(_engine_cfg(scalper_market_gate_run_days=3), fetcher)
        await engine._market_gate_reason(Direction.LONG)
        daily_call = [c for c in fetcher.calls if c[1] == "1d"][0]
        assert daily_call[2] == 5
        snapshot = engine._market_gate_cache["BTCUSDT"][0]
        # Bugünün kapanmamış mumu ATILDI → 4 tamamlanmış kapanış.
        assert snapshot["daily_closes"] == _LEADER_DAILY_CLOSES
        assert snapshot["day_open"] == 120.0        # dünkü kapanış
        assert snapshot["last_close"] == 118.0      # son kapanan 1m mum

    async def test_fetch_failure_is_fail_open_with_warning(self):
        fetcher = self._fetcher()
        fetcher.fail_with = RuntimeError("Binance 418")
        engine = _bare_engine(_engine_cfg(), fetcher)
        warnings: List[str] = []
        engine.logger = SimpleNamespace(
            info=lambda *a, **kw: None,
            warning=lambda msg, *a, **kw: warnings.append(msg),
            error=lambda *a, **kw: None,
        )
        # Kapı UYGULANMAZ (fail-open) ama sessiz kalmaz.
        assert await engine._market_gate_reason(Direction.LONG) is None
        assert len(warnings) == 1 and "kapı bu turda UYGULANMADI" in warnings[0]
        assert "BTCUSDT" in warnings[0]
        # Başarısız çekim ÖNBELLEĞE ALINMAZ — sonraki tur yeniden dener.
        assert engine._market_gate_cache == {}

    async def test_short_leader_series_is_fail_open_with_warning(self):
        fetcher = _LeaderFetcher(
            {("BTCUSDT", "1d"): [], ("BTCUSDT", "1m"): _LEADER_ENTRY}, _DECISION_MS
        )
        engine = _bare_engine(_engine_cfg(), fetcher)
        warnings: List[str] = []
        engine.logger = SimpleNamespace(
            info=lambda *a, **kw: None,
            warning=lambda msg, *a, **kw: warnings.append(msg),
            error=lambda *a, **kw: None,
        )
        assert await engine._market_gate_reason(Direction.LONG) is None
        assert len(warnings) == 1 and "yetersiz" in warnings[0]

    async def test_reject_counters_and_status_field(self):
        engine = _bare_engine(_engine_cfg(), self._fetcher())
        # Kapı kapalıyken bile durum sözlüğü ŞEKLİ sabittir.
        empty = engine._market_gate_status()
        assert set(empty) == {
            "enabled", "leader", "day_drift_pct", "run_pct", "last_reason", "rejects",
        }
        assert empty["enabled"] is True
        assert empty["leader"] == "BTCUSDT"
        assert empty["day_drift_pct"] is None and empty["run_pct"] is None

        await engine._market_gate_reason(Direction.LONG)   # day
        engine.cfg.scalper_market_gate_day_pct = 0.0       # yalnız uzama kalsın
        await engine._market_gate_reason(Direction.LONG)   # run
        await engine._market_gate_reason(Direction.SHORT)  # serbest

        status = engine._market_gate_status()
        assert status["rejects"] == {REASON_DAY: 1, REASON_RUN: 1}
        assert status["last_reason"] is None               # son değerlendirme serbest
        assert status["day_drift_pct"] == pytest.approx(-1.6666666, abs=1e-4)
        assert status["run_pct"] == pytest.approx(20.0)

    async def test_leader_symbol_is_normalised_and_defaults(self):
        engine = _bare_engine(_engine_cfg(scalper_market_gate_symbol="  btcusdt "), self._fetcher())
        assert engine._market_gate_leader() == "BTCUSDT"
        engine.cfg.scalper_market_gate_symbol = ""
        assert engine._market_gate_leader() == "BTCUSDT"

    async def test_status_survives_engine_without_init(self):
        """`object.__new__(ScalperEngine)` ile kurulan test çiftlerinde
        snapshot() AttributeError'a düşmemeli (tests/test_runtime_liveness)."""
        engine = ScalperEngine.__new__(ScalperEngine)
        engine.cfg = _engine_cfg()
        status = engine._market_gate_status()
        assert status["rejects"] == {} and status["last_reason"] is None


# --- Kapının GERÇEK giriş noktasında (C + TV ortak) olduğunun kanıtı -------

@dataclass
class _EvalCfg:
    """`_evaluate_symbol`'ün kapıya GELENE kadar okuduğu alanlar."""

    scalper_market_gate: bool = True
    scalper_market_gate_symbol: str = "BTCUSDT"
    scalper_market_gate_day_pct: float = 1.0
    scalper_market_gate_run_pct: float = 0.0
    scalper_market_gate_run_days: int = 3
    scalper_regime_filter: bool = False       # rejim kapısı KAPALI → izole test
    scalper_tv_regime_filter: bool = False
    scalper_tf_entry: str = "1m"
    scalper_tf_context: str = "5m"
    scalper_tf_regime: str = "15m"
    scalper_leverage: int = 10
    scalper_max_positions: int = 5
    scalper_shadow_mode: bool = False


class _AlwaysSignalStrategy:
    """Kapıya kadar gelen her turda sinyal üretir."""

    name = "C"

    def __init__(self, direction: Direction):
        self.direction = direction

    def evaluate(self, ctx: StrategyContext) -> ScalpSignal:
        return ScalpSignal(
            strategy="C", symbol=ctx.symbol, direction=self.direction,
            entry_price=ctx.current_price, stop_price=ctx.current_price * 0.99,
            reason="test", regime=ctx.regime, atr_5m=1.0,
        )


class _ExternalSignalLike(_AlwaysSignalStrategy):
    """`is_external` tespiti SINIF ADINA bakar (engine.py) — TV yolunun da
    aynı kapıdan geçtiğini kanıtlamak için adı birebir taklit edilir."""


_ExternalSignalLike.__name__ = "_ExternalSignalStrategy"


class _NeverBlockedExecutor:
    def is_entry_blocked(self, symbol: str) -> bool:
        return False

    def pending_symbols(self):
        return set()

    async def try_open(self, sig, ctx):  # pragma: no cover - çağrılmamalı
        raise AssertionError("Kapı engellemeliydi; try_open ÇAĞRILMAMALI")


class _NoPositionsExits:
    def tracked_symbols(self):
        return set()


class _EvalFetcher(_LeaderFetcher):
    """Lider serilerine EK olarak işlem sembolünün 3 zaman dilimini verir."""

    async def get_klines(self, symbol, interval, limit=200, end_time=None):
        if (symbol, interval) not in self.series:
            # İşlem sembolü — sinyal üretimi için yeterli, kapı için önemsiz.
            return _minute_candles([100.0] * 5, _LEADER_TODAY_START)
        return await super().get_klines(symbol, interval, limit, end_time)


class TestMarketGateInEvaluateSymbol:
    async def _run(self, strategy) -> List[str]:
        symbol_reservations.clear()
        try:
            fetcher = _EvalFetcher(
                {("BTCUSDT", "1d"): _LEADER_DAILY, ("BTCUSDT", "1m"): _LEADER_ENTRY},
                _DECISION_MS,
            )
            engine = _bare_engine(_EvalCfg(), fetcher)
            engine.executor = _NeverBlockedExecutor()
            engine.exits = _NoPositionsExits()
            engine._entry_lock = asyncio.Lock()
            engine._opening_symbols = set()
            engine._regimes = {}
            engine._regime_cache = {}
            infos: List[str] = []
            engine.logger = SimpleNamespace(
                info=lambda msg, *a, **kw: infos.append(msg),
                warning=lambda *a, **kw: None,
                error=lambda *a, **kw: None,
            )
            await engine._evaluate_symbol("ETHUSDT", [strategy])
            return infos
        finally:
            symbol_reservations.clear()

    async def test_scanner_long_signal_blocked_before_executor(self):
        infos = await self._run(_AlwaysSignalStrategy(Direction.LONG))
        assert any("piyasa kapısı" in m and "girişi engellendi" in m for m in infos)

    async def test_external_tv_signal_blocked_by_same_gate(self):
        """Rejim kapısında TV için ayrı bir bayrak var; piyasa kapısında YOK —
        TV sinyali de aynı tek giriş noktasından geçer (spec §C)."""
        infos = await self._run(_ExternalSignalLike(Direction.LONG))
        assert any("piyasa kapısı" in m and "TV sinyali engellendi" in m for m in infos)


# ==========================================================================
# 4) Harness
# ==========================================================================

class TestLeaderSeries:
    def test_inputs_at_decision_time(self):
        day_open, last_close, closes = _leader_series().inputs_at(_DECISION_MS)
        assert day_open == 120.0
        assert last_close == 118.0
        assert closes == _LEADER_DAILY_CLOSES

    def test_no_look_ahead_on_daily_series(self):
        """Bugünün (kapanmamış) günlük mumu seride OLSA BİLE karar anında
        görülmez — canlıdaki `_drop_unclosed` ile aynı sonuç."""
        _, _, closes = _leader_series().inputs_at(_DECISION_MS)
        assert 118.0 not in closes

    def test_entry_close_is_the_candle_closed_at_cutoff(self):
        series = _leader_series()
        _, last_close, _ = series.inputs_at(_LEADER_ENTRY[1].close_time)
        assert last_close == _LEADER_ENTRY[1].close
        # Bir milisaniye ÖNCESİ hâlâ önceki mumu görür.
        _, earlier, _ = series.inputs_at(_LEADER_ENTRY[1].close_time - 1)
        assert earlier == _LEADER_ENTRY[0].close

    def test_before_first_candle_returns_none(self):
        assert _leader_series().inputs_at(0) is None


@dataclass
class _SimCfg:
    """`simulate_symbol`'ün kapıya gelene kadar okuduğu minimum alan kümesi."""

    scalper_market_gate: bool = True
    scalper_market_gate_symbol: str = "BTCUSDT"
    scalper_market_gate_day_pct: float = 1.0
    scalper_market_gate_run_pct: float = 0.0
    scalper_market_gate_run_days: int = 3
    scalper_regime_filter: bool = False
    scalper_leverage: int = 10
    scalper_loss_cooldown_minutes: float = 0.0
    scalper_min_rr: float = 0.0
    scalper_stop_mode: str = "fixed_roi"
    scalper_fixed_stop_roi_pct: float = 40.0
    scalper_dynamic_leverage: bool = False
    scalper_min_stop_pct: float = 0.15
    scalper_max_stop_pct: float = 5.5
    scalper_risk_percentage: float = 10.0
    scalper_max_margin_pct: float = 5.0
    scalper_entry_mode: str = "taker"
    scalper_taker_fee_pct: float = 0.05
    scalper_maker_fee_pct: float = 0.02
    scalper_maker_fill_timeout_candles: int = 3
    scalper_tp1_roi: float = 8.0
    scalper_tp1_fraction: float = 0.40
    scalper_tp2_roi: float = 25.0
    scalper_tp2_fraction: float = 0.20
    scalper_breakeven_buffer_pct: float = 0.05
    scalper_chandelier_atr_mult: float = 3.5
    scalper_chandelier_atr_period: int = 14
    scalper_trail_relax_roi1_pct: float = 0.0
    scalper_trail_relax_mult1: float = 5.0
    scalper_trail_relax_roi2_pct: float = 150.0
    scalper_trail_relax_mult2: float = 7.0
    scalper_stop_atr_floor_mult: float = 0.5
    scalper_dyn_lev_stop_atr_mult: float = 3.0
    scalper_dyn_lev_min: int = 3
    scalper_dyn_lev_max: int = 20


def _traded_symbol_candles() -> List[Candle]:
    """İşlem sembolünün mumları — kapanış ZAMANLARI lider ile hizalı."""
    return _minute_candles([100.0] * len(_LEADER_ENTRY), _LEADER_TODAY_START)


class TestHarnessMarketGate:
    def _simulate(self, cfg: Any, direction: Direction, leader) -> Dict[str, int]:
        missed: Dict[str, int] = {}
        candles = _traded_symbol_candles()
        simulate_symbol(
            "ETHUSDT", candles, candles, candles,
            [_AlwaysSignalStrategy(direction)], cfg,
            missed_counter=missed, leader=leader,
        )
        return missed

    def test_day_gate_counts_into_missed_counter(self):
        missed = self._simulate(_SimCfg(), Direction.LONG, _leader_series())
        assert missed.get(REASON_DAY, 0) > 0
        assert REASON_RUN not in missed

    def test_run_gate_counts_into_missed_counter(self):
        cfg = _SimCfg(scalper_market_gate_day_pct=0.0, scalper_market_gate_run_pct=15.0)
        missed = self._simulate(cfg, Direction.LONG, _leader_series())
        assert missed.get(REASON_RUN, 0) > 0
        assert REASON_DAY not in missed

    def test_opposite_direction_not_blocked(self):
        missed = self._simulate(_SimCfg(), Direction.SHORT, _leader_series())
        assert REASON_DAY not in missed and REASON_RUN not in missed

    def test_gate_off_is_inert(self):
        cfg = _SimCfg(scalper_market_gate=False)
        missed = self._simulate(cfg, Direction.LONG, _leader_series())
        assert REASON_DAY not in missed and REASON_RUN not in missed

    def test_missing_leader_series_is_fail_open(self):
        """Lider serisi verilmezse kapı UYGULANMAZ (canlıdaki fail-open ile
        aynı ilke) — sinyal akışı bugünkü davranışını korur."""
        missed = self._simulate(_SimCfg(), Direction.LONG, None)
        assert REASON_DAY not in missed and REASON_RUN not in missed

    def test_gate_key_is_visible_in_report(self, capsys):
        missed = self._simulate(_SimCfg(), Direction.LONG, _leader_series())
        backtest_module.print_report([], missed_counter=missed)
        assert REASON_DAY in capsys.readouterr().out


# ==========================================================================
# 5) PARİTE — motor ve harness aynı fonksiyonu aynı argümanlarla çağırır
# ==========================================================================

class TestEngineHarnessParity:
    def test_both_modules_reference_the_same_function_object(self):
        """İki taraf da `market_gate` modülünün AYNI nesnesini kullanır —
        birinde yapılan bir değişiklik diğerini de kapsar (CLAUDE.md #2)."""
        assert engine_module.evaluate_market_gate is market_gate_module.evaluate_market_gate
        assert backtest_module.evaluate_market_gate is market_gate_module.evaluate_market_gate
        assert (
            engine_module.day_open_from_daily_closes
            is backtest_module.day_open_from_daily_closes
        )

    @staticmethod
    def _spy(monkeypatch, module) -> List[tuple]:
        recorded: List[tuple] = []

        def _record(direction, day_open, last_close, daily_closes, cfg):
            recorded.append((
                str(getattr(direction, "value", direction)),
                day_open, last_close,
                list(daily_closes) if daily_closes is not None else None,
            ))
            return market_gate_module.evaluate_market_gate(
                direction, day_open, last_close, daily_closes, cfg
            )

        monkeypatch.setattr(module, "evaluate_market_gate", _record)
        return recorded

    async def test_identical_arguments_and_verdict(self, monkeypatch):
        """AYNI mum verisi + AYNI karar anı → motor ve harness kapıya
        BİREBİR aynı argümanları verir ve aynı sonucu üretir.

        Bu test parite boşluğunun (DECISIONS P1) bu kapı için tekrar
        açılmasını engeller: girdi türetme (gün açılışı vekili, son kapanış,
        tamamlanmış günlük kapanışlar) iki tarafta ayrı kod yollarından
        geçer, sonuç aynı OLMAK ZORUNDA.
        """
        engine_calls = self._spy(monkeypatch, engine_module)
        harness_calls = self._spy(monkeypatch, backtest_module)

        # --- canlı motor -------------------------------------------------
        fetcher = _LeaderFetcher(
            {("BTCUSDT", "1d"): _LEADER_DAILY, ("BTCUSDT", "1m"): _LEADER_ENTRY},
            _DECISION_MS,
        )
        engine = _bare_engine(_engine_cfg(scalper_market_gate_run_pct=0.0), fetcher)
        engine_reason = await engine._market_gate_reason(Direction.LONG)

        # --- harness (aynı karar anında TEK değerlendirme) ---------------
        cfg = _SimCfg()
        candles = _traded_symbol_candles()
        # Yalnız karar anındaki mumda sinyal üret: son mum dolum mumu olarak
        # gerektiğinden sinyal bir önceki mumda aranır; kesim zamanı aynı
        # olsun diye lider serisini o mumun kapanışına hizalıyoruz.
        decision_idx = len(candles) - 2
        cutoff = candles[decision_idx].close_time
        leader = LeaderSeries(
            symbol="BTCUSDT",
            entry_close_times=[cutoff],
            entry_closes=[_LEADER_ENTRY[-1].close],
            daily_close_times=[c.close_time for c in _LEADER_DAILY],
            daily_closes=[c.close for c in _LEADER_DAILY],
        )

        class _OnlyAtDecision:
            name = "C"

            def evaluate(self, ctx: StrategyContext):
                if ctx.candles_5m[-1].close_time != cutoff:
                    return None
                return ScalpSignal(
                    strategy="C", symbol=ctx.symbol, direction=Direction.LONG,
                    entry_price=ctx.current_price,
                    stop_price=ctx.current_price * 0.99,
                    reason="test", regime=ctx.regime, atr_5m=1.0,
                )

        missed: Dict[str, int] = {}
        simulate_symbol(
            "ETHUSDT", candles, candles, candles, [_OnlyAtDecision()], cfg,
            missed_counter=missed, leader=leader,
        )

        assert len(engine_calls) == 1, engine_calls
        assert len(harness_calls) == 1, harness_calls
        assert engine_calls[0] == harness_calls[0]
        assert engine_reason == REASON_DAY
        assert missed.get(REASON_DAY) == 1


# ==========================================================================
# Ek) Settings — env parse + varsayılanlar KAPALI
# ==========================================================================

class TestMarketGateSettings:
    @staticmethod
    def _settings(monkeypatch, **env: str) -> Settings:
        """Env değişkenlerinden parse — zorunlu alanlar (anahtarlar) sabit
        yer tutucularla verilir (tests/test_shadow_mode.py ile aynı desen)."""
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        return Settings(
            _env_file=None,
            binance_api_key="x", binance_api_secret="x",
            telegram_bot_token="x", telegram_chat_id="x",
            openai_api_key="x", gemini_api_key="x", deepseek_api_key="x",
            jwt_secret="x",
            binance_base_url="https://testnet.binancefuture.com",
        )

    def test_defaults_are_off(self, monkeypatch):
        s = self._settings(monkeypatch)
        assert s.scalper_market_gate is False
        assert s.scalper_market_gate_symbol == "BTCUSDT"
        assert s.scalper_market_gate_day_pct == 1.0
        assert s.scalper_market_gate_run_pct == 15.0
        assert s.scalper_market_gate_run_days == 3

    def test_env_overrides_are_parsed(self, monkeypatch):
        s = self._settings(
            monkeypatch,
            SCALPER_MARKET_GATE="true",
            SCALPER_MARKET_GATE_SYMBOL="ETHUSDT",
            SCALPER_MARKET_GATE_DAY_PCT="0.7",
            SCALPER_MARKET_GATE_RUN_PCT="20",
            SCALPER_MARKET_GATE_RUN_DAYS="5",
        )
        assert s.scalper_market_gate is True
        assert s.scalper_market_gate_symbol == "ETHUSDT"
        assert s.scalper_market_gate_day_pct == 0.7
        assert s.scalper_market_gate_run_pct == 20.0
        assert s.scalper_market_gate_run_days == 5

    def test_gate_settings_reach_the_pure_function(self, monkeypatch):
        """Settings nesnesi doğrudan kapıya verilebilir (cfg sözleşmesi)."""
        s = self._settings(
            monkeypatch, SCALPER_MARKET_GATE="true",
            SCALPER_MARKET_GATE_DAY_PCT="0.5", SCALPER_MARKET_GATE_RUN_PCT="0",
        )
        assert evaluate_market_gate(Direction.LONG, 100.0, 99.4, None, s) == REASON_DAY
        assert evaluate_market_gate(Direction.LONG, 100.0, 99.6, None, s) is None
