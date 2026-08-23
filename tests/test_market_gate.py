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

import os
import pytest

import src.strategies.scalper.backtest as backtest_module
import src.strategies.scalper.engine as engine_module
from src.core.config import Settings
from src.strategies.scalper import market_gate as market_gate_module
from src.strategies.scalper.backtest import LeaderSeries, simulate_symbol
from src.strategies.scalper.engine import ScalperEngine
from src.strategies.scalper.market_gate import (
    DAY_OPEN_SOURCE_INTRADAY,
    DAY_OPEN_SOURCE_PREV_CLOSE,
    MARKET_GATE_INTRADAY_TF,
    REASON_DAY,
    REASON_RUN,
    day_open_from_daily_closes,
    day_open_from_intraday,
    evaluate_market_gate,
    market_gate_metrics,
    resolve_day_open,
    utc_day_start_ms,
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


# BTC senaryosu: 4 gün +%20 koşu (100 → 120), sonra 5. gün içinde 118'e iniş.
# Her iki alt-kapı da LONG'u engeller.
_LEADER_DAILY_CLOSES = [100.0, 105.0, 112.0, 120.0]
_LEADER_TODAY_INDEX = len(_LEADER_DAILY_CLOSES)          # 5. gün (indeks 4)
_LEADER_TODAY_START = _LEADER_TODAY_INDEX * _DAY_MS
# Bugünün (henüz kapanmamış) günlük mumu da seride: gerçek Binance yanıtı
# gibi — fetcher onu atmalı, harness ise close_time filtresiyle görmemeli.
_LEADER_DAILY = _daily_candles(_LEADER_DAILY_CLOSES + [118.0])

# GERÇEK gün açılışı 121.0; ÖNCEKİ günlük kapanış (vekil) 120.0. İkisi
# BİLEREK farklı seçildi ki testler hangi türetme yolunun kullanıldığını
# AYIRT EDEBİLSİN (aynı olsalardı yol değişse bile sayılar tutardı).
_LEADER_TRUE_DAY_OPEN = 121.0
_M15_MS = 15 * 60 * 1000
_LEADER_INTRADAY = [
    Candle(
        open_time=_LEADER_TODAY_START + n * _M15_MS,
        open=(_LEADER_TRUE_DAY_OPEN if n == 0 else 119.0),
        high=121.5, low=117.5, close=118.5, volume=1.0,
        close_time=_LEADER_TODAY_START + (n + 1) * _M15_MS - 1,
    )
    for n in range(96)
]

# Karar anı günün ORTASINDA (12:00 UTC): 00:00 UTC 15m mumu çoktan kapandı,
# yani gerçek açılış yolu kullanılabilir olmalı.
_ENTRY_START = _LEADER_TODAY_START + 12 * 60 * 60 * 1000
_LEADER_ENTRY = _minute_candles([118.6, 118.4, 118.2, 118.0], _ENTRY_START)
_DECISION_MS = _LEADER_ENTRY[-1].close_time


def _leader_series() -> LeaderSeries:
    return LeaderSeries(
        symbol="BTCUSDT",
        entry_close_times=[c.close_time for c in _LEADER_ENTRY],
        entry_closes=[c.close for c in _LEADER_ENTRY],
        daily_close_times=[c.close_time for c in _LEADER_DAILY],
        daily_closes=[c.close for c in _LEADER_DAILY],
        intraday_open_times=[c.open_time for c in _LEADER_INTRADAY],
        intraday_opens=[c.open for c in _LEADER_INTRADAY],
        intraday_close_times=[c.close_time for c in _LEADER_INTRADAY],
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


# `/scalper/status` → `market_gate` sözleşmesi (motorlu ve motorsuz yol
# AYNI anahtarları vermeli — test_status_shape_matches_the_engineless_contract).
_MARKET_GATE_STATUS_KEYS = {
    "enabled", "gate_effective", "leader", "leader_ok", "leader_source_host",
    "thresholds", "stale", "snapshot_age_sec",
    "day_drift_pct", "run_drift_pct", "day_open_source",
    "last_ok_at", "last_error", "last_failure_at",
    "consecutive_failures", "failures_total",
    "last_reason", "last_block_at", "rejects",
}


@pytest.fixture(autouse=True)
def _deterministic_engine_clock(monkeypatch):
    """Motor kapısı testleri SANAL zamanla koşar.

    Zorunlu: lider anlık görüntüsü önbelleği artık UTC GÜN DAMGASIYLA
    anahtarlanıyor (gün sınırında dünün açılışıyla karar verilmesin diye).
    Kurgu mumlar epoch gün 4'te (1970) olduğu için gerçek duvar saatiyle
    koşulduğunda önbellek HİÇ isabet etmezdi. Kendi saatini kuran testler
    (`_install_clock`) bunu ezer.
    """
    monkeypatch.setattr(engine_module, "time", _FakeClock(wall_ms=_DECISION_MS))


def _bare_engine(cfg: Any, fetcher: Any) -> ScalperEngine:
    """__init__ ATLANIR (ağ/DB yok) — yalnız kapının okuduğu alanlar kurulur."""
    engine = ScalperEngine.__new__(ScalperEngine)
    engine.cfg = cfg
    engine.fetcher = fetcher
    engine._market_gate_cache = {}
    engine._market_gate_rejects = {}
    engine._market_gate_last_reason = None
    engine._market_gate_last_block_at = None
    engine._market_gate_leader_ok = None
    engine._market_gate_last_ok_at = None
    engine._market_gate_last_error = None
    engine._market_gate_consecutive_failures = 0
    engine._market_gate_retry_after = 0.0
    engine._market_gate_warn_at = {}
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
            {("BTCUSDT", "1d"): _LEADER_DAILY, ("BTCUSDT", "1m"): _LEADER_ENTRY,
             ("BTCUSDT", "15m"): _LEADER_INTRADAY},
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
        # 5 değerlendirme → yalnız 3 istek (1d + giriş TF + 15m), TTL içinde
        # tazelenmez. Üçü de limit<=100 olduğu için toplam ağırlık 3/dakika.
        assert len(fetcher.calls) == 3
        assert {c[1] for c in fetcher.calls} == {"1d", "1m", "15m"}
        intraday_call = [c for c in fetcher.calls if c[1] == MARKET_GATE_INTRADAY_TF][0]
        assert intraday_call[2] <= 100  # Binance ağırlık 1 sınırı

    async def test_daily_limit_has_margin_over_the_minimum(self):
        """N günlük koşu N+1 TAMAMLANMIŞ kapanış ister; oluşmakta olan günlük
        mum düşeceği için ASGARİ N+2'dir.

        2026-08-23 inceleme bulgusu: tam asgariyi istemek SIFIR PAYLIDIR —
        borsanın tek bir eksik/geç günlük mumu uzama alt-kapısını SESSİZCE
        fail-open yapardı. Pay ücretsiz (limit ≤ 100 → ağırlık 1)."""
        fetcher = self._fetcher()
        engine = _bare_engine(_engine_cfg(scalper_market_gate_run_days=3), fetcher)
        await engine._market_gate_reason(Direction.LONG)
        daily_call = [c for c in fetcher.calls if c[1] == "1d"][0]
        assert daily_call[2] == 3 + engine_module._LEADER_DAILY_LIMIT_MARGIN
        assert daily_call[2] > 3 + 2          # asgarinin ÜSTÜNDE
        assert daily_call[2] <= 100           # Binance ağırlık 1 sınırı
        snapshot = engine._market_gate_cache["BTCUSDT"][0]
        # Bugünün kapanmamış mumu ATILDI → 4 tamamlanmış kapanış.
        assert snapshot["daily_closes"] == _LEADER_DAILY_CLOSES
        # Gün açılışı GERÇEK 00:00 UTC 15m open'ından (121.0) gelir — önceki
        # günlük kapanış vekilinden (120.0) DEĞİL.
        assert snapshot["day_open"] == _LEADER_TRUE_DAY_OPEN
        assert snapshot["day_open_source"] == DAY_OPEN_SOURCE_INTRADAY
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
            {("BTCUSDT", "1d"): [], ("BTCUSDT", "1m"): _LEADER_ENTRY,
             ("BTCUSDT", "15m"): []},
            _DECISION_MS,
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
        # Henüz hiç veri çekilmemişken durum sözlüğü ŞEKLİ sabittir.
        empty = engine._market_gate_status()
        assert set(empty) == _MARKET_GATE_STATUS_KEYS
        assert empty["enabled"] is True
        # enabled ≠ KORUYOR: lider hiç doğrulanmadığı için kapı etkisiz.
        assert empty["gate_effective"] is False
        assert empty["leader_ok"] is None
        assert empty["leader"] == "BTCUSDT"
        assert empty["day_drift_pct"] is None and empty["run_drift_pct"] is None
        assert empty["consecutive_failures"] == 0 and empty["failures_total"] == 0
        assert empty["last_block_at"] is None
        assert empty["stale"] is True and empty["snapshot_age_sec"] is None
        # Yürürlükteki EŞİKLER dışa verilir: "log bir KONTROL değildir" (D14)
        # ilkesi eşikler için de geçerli — RUNBOOK bunları assert edebilmeli.
        assert empty["thresholds"] == {"day_pct": 1.0, "run_pct": 15.0, "run_days": 3}

        await engine._market_gate_reason(Direction.LONG)   # day → ENGEL
        engine.cfg.scalper_market_gate_day_pct = 0.0       # yalnız uzama kalsın
        await engine._market_gate_reason(Direction.LONG)   # run → ENGEL
        await engine._market_gate_reason(Direction.SHORT)  # serbest

        status = engine._market_gate_status()
        assert status["rejects"] == {REASON_DAY: 1, REASON_RUN: 1}
        # BULGU (2026-08-23): `last_reason` eskiden SERBEST geçişlerde de
        # yazılıyordu, bu yüzden `/scalper/status` pratikte HER ZAMAN null
        # gösteriyordu ("kapı hiç tetiklenmedi" yanılsaması). Artık yalnız
        # ENGELLEMEDE yazılır ve serbest geçiş onu SİLMEZ.
        assert status["last_reason"] == REASON_RUN
        assert status["last_block_at"] is not None
        # Başarılı çekim → kapı GERÇEKTEN etkili.
        assert status["leader_ok"] is True
        assert status["gate_effective"] is True
        assert status["last_ok_at"] is not None
        assert status["last_error"] is None
        # 118 / 121 − 1 = −%2.479 (gerçek açılışa göre; vekil olsaydı −%1.667)
        assert status["day_drift_pct"] == pytest.approx(-2.4793388, abs=1e-4)
        assert status["day_open_source"] == DAY_OPEN_SOURCE_INTRADAY
        # ÖLÇÜLEN koşu; eşik `thresholds.run_pct`. İkisine de `run_pct` demek
        # "uzama kapısı %20'de açık kalmış" gibi bir yanlış-teşhis üretiyordu.
        assert status["run_drift_pct"] == pytest.approx(20.0)
        # Eşikler CANLI okunur: test ortada `day_pct`'i 0'a çekti.
        assert status["thresholds"] == {"day_pct": 0.0, "run_pct": 15.0, "run_days": 3}

    async def test_status_shape_matches_the_engineless_contract(self):
        """`/scalper/status` motor VARKEN ve YOKKEN aynı ŞEKLİ döndürmeli.

        `src/main.py::_EMPTY_SCALPER_STATUS` motor kurulmadan da aynı
        sözlüğü verir; dashboard alan yokluğunu "kapı yok" ile karıştırmasın.
        Bu iki tanım ayrı dosyalarda olduğu için sessizce AYRIŞIYORDU
        (`day_open_source` motora eklendi, `_EMPTY_SCALPER_STATUS`'a değil) —
        test bu sınıfı kapatır."""
        from src.main import _EMPTY_SCALPER_STATUS

        engine = _bare_engine(_engine_cfg(), self._fetcher())
        assert set(_EMPTY_SCALPER_STATUS["market_gate"]) == set(
            engine._market_gate_status()
        )

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
            return _minute_candles([100.0] * 5, _ENTRY_START)
        return await super().get_klines(symbol, interval, limit, end_time)


class TestMarketGateInEvaluateSymbol:
    async def _run(self, strategy) -> List[str]:
        symbol_reservations.clear()
        try:
            fetcher = _EvalFetcher(
                {("BTCUSDT", "1d"): _LEADER_DAILY, ("BTCUSDT", "1m"): _LEADER_ENTRY,
             ("BTCUSDT", "15m"): _LEADER_INTRADAY},
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
        # GERÇEK açılış (00:00 UTC 15m open), vekil (120.0) DEĞİL.
        assert day_open == _LEADER_TRUE_DAY_OPEN
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
    return _minute_candles([100.0] * len(_LEADER_ENTRY), _ENTRY_START)


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
        assert engine_module.resolve_day_open is market_gate_module.resolve_day_open
        assert backtest_module.resolve_day_open is market_gate_module.resolve_day_open

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
            {("BTCUSDT", "1d"): _LEADER_DAILY, ("BTCUSDT", "1m"): _LEADER_ENTRY,
             ("BTCUSDT", "15m"): _LEADER_INTRADAY},
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
            intraday_open_times=[c.open_time for c in _LEADER_INTRADAY],
            intraday_opens=[c.open for c in _LEADER_INTRADAY],
            intraday_close_times=[c.close_time for c in _LEADER_INTRADAY],
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
        # Süreç env'inde (ör. sunucuda deploy testleri, .env kapı AÇIK)
        # bulunan SCALPER_MARKET_GATE* değişkenleri varsayılan testini
        # kirletmesin — yalnız bu testin verdiği env kalsın (2026-08-23 deploy dersi).
        for key in list(os.environ):
            if key.startswith("SCALPER_MARKET_GATE"):
                monkeypatch.delenv(key, raising=False)
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
        """Kapının kendisi KAPALI; eşikler ise ölçümün önerdiği çift.

        Varsayılan değişikliği (2026-08-23, ana oturum kararı): DAY_PCT
        1.0→1.3 ve RUN_PCT 15→0. İki BAĞIMSIZ ölçüm uyuşuyor (E7 motor-içi
        harness / E8 canlı defter post-hoc). Kapı zaten varsayılan kapalı
        olduğu için canlı davranış DEĞİŞMEZ; değişen, kapıyı çıplak açan
        operatörün ne alacağıdır (bkz. docs/DECISIONS.md D15)."""
        s = self._settings(monkeypatch)
        assert s.scalper_market_gate is False
        assert s.scalper_market_gate_symbol == "BTCUSDT"
        assert s.scalper_market_gate_day_pct == 1.3
        assert s.scalper_market_gate_run_pct == 0.0
        assert s.scalper_market_gate_run_days == 3
        assert s.scalper_market_gate_retry_sec == 60.0

    def test_run_sub_gate_is_off_by_default_end_to_end(self, monkeypatch):
        """Varsayılan RUN_PCT=0 → uzama alt-kapısı saf fonksiyonda da inert.

        (Çürütüldüğü için kapalı: E7 tek olay/n=1, E8 canlı defterde −152.7.)"""
        s = self._settings(monkeypatch, SCALPER_MARKET_GATE="true")
        # 3 günde +%40 koşu bile LONG'u engellemez — alt-kapı kapalı.
        assert evaluate_market_gate(
            Direction.LONG, 100.0, 100.0, [100.0, 110.0, 120.0, 140.0], s
        ) is None

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


# ==========================================================================
# Ek 2) Başlangıç bannerı — uzama alt-kapısı için operatör uyarısı
# ==========================================================================

class TestMarketGateStartupBanner:
    """`SCALPER_MARKET_GATE_RUN_PCT` varsayılanı 15 (spec §C'de onaylandı) ama
    iki bağımsız ölçüm (E7 harness + E8 canlı defter) uzama alt-kapısını
    desteklemiyor. Varsayılanı sessizce değiştirmek yerine kapıyı açan
    operatörü AÇIKÇA uyarıyoruz — sessiz tuzak bırakmamak için."""

    @staticmethod
    def _engine(cfg: Any):
        engine = ScalperEngine.__new__(ScalperEngine)
        engine.cfg = cfg
        warnings: List[str] = []
        engine.logger = SimpleNamespace(
            info=lambda *a, **kw: None,
            warning=lambda msg, *a, **kw: warnings.append(msg),
            error=lambda *a, **kw: None,
        )
        return engine, warnings

    def test_no_banner_when_gate_disabled(self):
        engine, warnings = self._engine(_engine_cfg(scalper_market_gate=False))
        engine._maybe_log_market_gate_banner()
        assert warnings == []

    def test_banner_when_gate_enabled_day_only(self):
        engine, warnings = self._engine(
            _engine_cfg(scalper_market_gate_day_pct=1.3, scalper_market_gate_run_pct=0)
        )
        engine._maybe_log_market_gate_banner()
        assert len(warnings) == 1
        assert "PİYASA KAPISI AÇIK" in warnings[0] and "BTCUSDT" in warnings[0]

    def test_extra_warning_when_run_sub_gate_active(self):
        engine, warnings = self._engine(_engine_cfg(scalper_market_gate_run_pct=15.0))
        engine._maybe_log_market_gate_banner()
        assert len(warnings) == 2
        assert "UZAMA alt-kapısı açık" in warnings[1]
        assert "SCALPER_MARKET_GATE_RUN_PCT=0" in warnings[1]

    def test_missing_config_fields_do_not_raise(self):
        engine, warnings = self._engine(SimpleNamespace())
        engine._maybe_log_market_gate_banner()
        assert warnings == []


# ==========================================================================
# Ek 3) Gün açılışı türetmesi — GERÇEK 00:00 UTC açılışı ve yedeği
# ==========================================================================

class TestUtcDayStart:
    def test_floors_to_utc_midnight(self):
        assert utc_day_start_ms(4 * _DAY_MS) == 4 * _DAY_MS
        assert utc_day_start_ms(4 * _DAY_MS + 1) == 4 * _DAY_MS
        assert utc_day_start_ms(5 * _DAY_MS - 1) == 4 * _DAY_MS


class TestDayOpenFromIntraday:
    """E8'in bulgusu (bağımsız doğrulandı: BTCUSDT mainnet+testnet, 76 gün
    sınırı, 0 uyuşmazlık, maks fark %0.00000000): `1d` mumunun open'ı, o günün
    00:00 UTC 15m mumunun open'ına BİREBİR eşittir. Böylece canlı motor
    `_drop_unclosed`'a hiç dokunmadan gerçek gün açılışını okuyabiliyor."""

    @staticmethod
    def _series():
        return (
            [c.open_time for c in _LEADER_INTRADAY],
            [c.open for c in _LEADER_INTRADAY],
            [c.close_time for c in _LEADER_INTRADAY],
        )

    def test_returns_open_of_midnight_candle(self):
        ot, op, ct = self._series()
        assert day_open_from_intraday(ot, op, ct, _DECISION_MS) == _LEADER_TRUE_DAY_OPEN

    def test_none_before_midnight_candle_closes(self):
        """Günün ilk 15 dakikasında o mum HENÜZ KAPANMAMIŞTIR → None
        (look-ahead yasak; canlıda `_drop_unclosed` ile aynı sonuç)."""
        ot, op, ct = self._series()
        just_after_midnight = _LEADER_TODAY_START + 60_000  # 00:01 UTC
        assert day_open_from_intraday(ot, op, ct, just_after_midnight) is None

    def test_available_exactly_when_midnight_candle_closes(self):
        ot, op, ct = self._series()
        close_ms = _LEADER_INTRADAY[0].close_time
        assert day_open_from_intraday(ot, op, ct, close_ms) == _LEADER_TRUE_DAY_OPEN
        assert day_open_from_intraday(ot, op, ct, close_ms - 1) is None

    def test_none_when_day_has_no_midnight_candle(self):
        ot, op, ct = self._series()
        assert day_open_from_intraday(ot, op, ct, _DECISION_MS + _DAY_MS) is None

    @pytest.mark.parametrize("bad", [0.0, -1.0, float("nan")])
    def test_invalid_open_falls_through(self, bad):
        ot, op, ct = self._series()
        op = list(op)
        op[0] = bad
        assert day_open_from_intraday(ot, op, ct, _DECISION_MS) is None

    def test_empty_series_returns_none(self):
        assert day_open_from_intraday([], [], [], _DECISION_MS) is None


class TestResolveDayOpen:
    @staticmethod
    def _series():
        return (
            [c.open_time for c in _LEADER_INTRADAY],
            [c.open for c in _LEADER_INTRADAY],
            [c.close_time for c in _LEADER_INTRADAY],
        )

    def test_prefers_true_open(self):
        ot, op, ct = self._series()
        value, source = resolve_day_open(ot, op, ct, _LEADER_DAILY_CLOSES, _DECISION_MS)
        assert value == _LEADER_TRUE_DAY_OPEN
        assert source == DAY_OPEN_SOURCE_INTRADAY

    def test_falls_back_within_first_15_minutes(self):
        ot, op, ct = self._series()
        value, source = resolve_day_open(
            ot, op, ct, _LEADER_DAILY_CLOSES, _LEADER_TODAY_START + 60_000
        )
        assert value == _LEADER_DAILY_CLOSES[-1]      # 120.0 = önceki kapanış
        assert source == DAY_OPEN_SOURCE_PREV_CLOSE

    def test_falls_back_when_intraday_series_missing(self):
        """Eski çağrıcılar / 15m çekimi boş dönerse davranış GERİYE UYUMLU."""
        value, source = resolve_day_open(
            None, None, None, _LEADER_DAILY_CLOSES, _DECISION_MS
        )
        assert value == _LEADER_DAILY_CLOSES[-1]
        assert source == DAY_OPEN_SOURCE_PREV_CLOSE

    def test_returns_none_when_both_paths_empty(self):
        value, source = resolve_day_open([], [], [], [], _DECISION_MS)
        assert value is None
        assert source == DAY_OPEN_SOURCE_PREV_CLOSE


class TestDayOpenSourceParity:
    """Motor ve harness AYNI kesim anında AYNI gün-açılışını ve AYNI kaynağı
    üretmeli — gerçek açılış yolunda da, yedek yolunda da."""

    async def test_engine_and_harness_agree_on_true_open(self):
        fetcher = _LeaderFetcher(
            {("BTCUSDT", "1d"): _LEADER_DAILY, ("BTCUSDT", "1m"): _LEADER_ENTRY,
             ("BTCUSDT", "15m"): _LEADER_INTRADAY},
            _DECISION_MS,
        )
        engine = _bare_engine(_engine_cfg(), fetcher)
        snapshot = await engine._leader_market_snapshot()
        harness = _leader_series().inputs_at(_DECISION_MS)

        assert snapshot["day_open"] == harness[0] == _LEADER_TRUE_DAY_OPEN
        assert snapshot["last_close"] == harness[1]
        assert snapshot["daily_closes"] == harness[2]
        assert snapshot["day_open_source"] == DAY_OPEN_SOURCE_INTRADAY

    async def test_engine_and_harness_agree_on_fallback(self):
        """Günün ilk 15 dakikası: İKİ TARAF DA vekile düşer (aynı değer)."""
        early = _LEADER_TODAY_START + 60_000
        early_entry = _minute_candles([118.0], _LEADER_TODAY_START)
        fetcher = _LeaderFetcher(
            {("BTCUSDT", "1d"): _LEADER_DAILY, ("BTCUSDT", "1m"): early_entry,
             ("BTCUSDT", "15m"): _LEADER_INTRADAY},
            early,
        )
        engine = _bare_engine(_engine_cfg(), fetcher)
        snapshot = await engine._leader_market_snapshot()

        harness_series = LeaderSeries(
            symbol="BTCUSDT",
            entry_close_times=[c.close_time for c in early_entry],
            entry_closes=[c.close for c in early_entry],
            daily_close_times=[c.close_time for c in _LEADER_DAILY],
            daily_closes=[c.close for c in _LEADER_DAILY],
            intraday_open_times=[c.open_time for c in _LEADER_INTRADAY],
            intraday_opens=[c.open for c in _LEADER_INTRADAY],
            intraday_close_times=[c.close_time for c in _LEADER_INTRADAY],
        )
        harness = harness_series.inputs_at(early_entry[-1].close_time)

        assert snapshot["day_open"] == harness[0] == _LEADER_DAILY_CLOSES[-1]
        assert snapshot["day_open_source"] == DAY_OPEN_SOURCE_PREV_CLOSE


# ==========================================================================
# Ek 4) Kapı GÖRÜNÜRLÜĞÜ — 2026-08-23 düşmanca inceleme bulguları
# ==========================================================================
#
# Kapı fail-open'dır (spec §C): lider verisi gelmezse giriş hattı bugünkü
# davranışını sürdürür. İnceleme bulgusu şuydu: fail-open SESSİZ olduğu için
# yanlış bir lider sembolü (ya da kalıcı bir ağ arızası) kapıyı görünmez
# biçimde devre dışı bırakıyordu — üstelik her sinyalde 3 seri × KlineFetcher
# yeniden denemeleri kadar boşa REST isteği açarak ve `KlineFetcher`'ın
# PAYLAŞILAN önbellek kilidini saniyelerce tutarak. Aşağıdaki testler
# fail-open semantiğini KORUYUP görünürlüğü ve maliyeti sabitler.


class _FakeClock:
    """engine modülünün `time`'ını değiştiren deterministik saat."""

    def __init__(self, wall_ms: int, mono: float = 1000.0):
        self.wall_ms = wall_ms
        self.mono = mono

    def time(self) -> float:
        return self.wall_ms / 1000.0

    def monotonic(self) -> float:
        return self.mono

    def advance(self, *, seconds: float = 0.0, wall_ms: Optional[int] = None):
        self.mono += seconds
        self.wall_ms = wall_ms if wall_ms is not None else self.wall_ms + int(seconds * 1000)


def _install_clock(monkeypatch, clock: _FakeClock) -> None:
    monkeypatch.setattr(engine_module, "time", clock)


class _FailingClient:
    """`get_symbol_filters` daima yükseltir — borsada olmayan lider taklidi."""

    def __init__(self, exc: Exception):
        self.exc = exc
        self.calls: List[str] = []

    async def get_symbol_filters(self, symbol: str):
        self.calls.append(symbol)
        raise self.exc


class _OkClient:
    def __init__(self):
        self.calls: List[str] = []

    async def get_symbol_filters(self, symbol: str):
        self.calls.append(symbol)
        return {"stepSize": 1}


class TestLeaderSymbolValidation:
    """Bulgu 1: yanlış lider sembolü kapıyı SESSİZCE devre dışı bırakıyordu."""

    def _engine(self, cfg: Any, client: Any):
        engine = _bare_engine(cfg, _LeaderFetcher({}, _DECISION_MS))
        engine.client = client
        records: Dict[str, List[str]] = {"error": [], "info": [], "warning": []}
        engine.logger = SimpleNamespace(
            info=lambda msg, *a, **kw: records["info"].append(str(msg)),
            warning=lambda msg, *a, **kw: records["warning"].append(str(msg)),
            error=lambda msg, *a, **kw: records["error"].append(str(msg)),
        )
        return engine, records

    async def test_unknown_symbol_logs_error_and_marks_gate_ineffective(self):
        engine, records = self._engine(
            _engine_cfg(scalper_market_gate_symbol="BTCUSD"),
            _FailingClient(RuntimeError("Sembol borsada bulunamadı: BTCUSD")),
        )
        assert await engine._validate_market_gate_leader() is False
        assert len(records["error"]) == 1
        assert "BTCUSD" in records["error"][0]
        assert "SCALPER_MARKET_GATE_SYMBOL" in records["error"][0]

        status = engine._market_gate_status()
        # enabled HÂLÂ True — ama koruma YOK ve bu artık GÖRÜNÜR.
        assert status["enabled"] is True
        assert status["leader_ok"] is False
        assert status["gate_effective"] is False
        assert "bulunamadı" in status["last_error"]
        assert status["consecutive_failures"] == 1

    async def test_valid_symbol_alone_does_not_prove_the_gate_works(self):
        """İNCELEME BULGUSU: doğrulama `leader_ok=True` yapıyor ama TEK bir
        lider mumu çekilmiş değil. `gate_effective` bunu "koruyor" saysaydı
        RUNBOOK'un ZORUNLU doğrulaması YANLIŞ-YEŞİL verirdi — girişler
        `_entries_ready()` yüzünden durmuşken (entry-halt, kill-switch,
        risk-event) kapı hiç veri çekmez ama yeşil görünürdü."""
        engine, records = self._engine(_engine_cfg(), _OkClient())
        assert await engine._validate_market_gate_leader() is True
        assert engine.client.calls == ["BTCUSDT"]
        status = engine._market_gate_status()
        assert status["leader_ok"] is True
        assert status["last_ok_at"] is None      # hiç mum çekilmedi
        assert status["gate_effective"] is False  # ...bu yüzden ETKİSİZ
        assert status["stale"] is True
        assert status["last_error"] is None
        assert records["error"] == []

    async def test_gate_effective_only_after_a_successful_snapshot(self):
        fetcher = TestLeaderSnapshotFreshness._fetcher()
        engine = _bare_engine(_engine_cfg(), fetcher)
        engine.client = _OkClient()
        assert await engine._validate_market_gate_leader() is True
        assert engine._market_gate_status()["gate_effective"] is False
        await engine._market_gate_reason(Direction.LONG)
        status = engine._market_gate_status()
        assert status["gate_effective"] is True and status["stale"] is False
        assert status["snapshot_age_sec"] == 0.0

    async def test_transient_failure_does_not_blame_the_config(self):
        """418/ağ hatasında metin `.env`'i kurcalamaya yönlendirmemeli —
        418 sırasında restart CLAUDE.md yasak #3."""
        engine, records = self._engine(
            _engine_cfg(), _FailingClient(RuntimeError("Binance [418] kod=-1003"))
        )
        assert await engine._validate_market_gate_leader() is False
        assert "ULAŞILAMADI" in records["error"][0]
        assert "RESTART YASAK" in records["error"][0]
        assert "borsada YOK" not in records["error"][0]

    async def test_validation_failure_does_not_mute_the_kline_path(self):
        """İNCELEME BULGUSU: doğrulama İMZALI istemcinin `/exchangeInfo`
        ucudur, lider mumları AYRI bir istemciden gelir. Geçici bir
        exchangeInfo hatası sapasağlam kline yolunu susturmamalı."""
        fetcher = TestLeaderSnapshotFreshness._fetcher()
        engine = _bare_engine(_engine_cfg(), fetcher)
        engine.client = _FailingClient(RuntimeError("geçici ağ"))
        await engine._validate_market_gate_leader()
        assert engine._market_gate_retry_after == 0.0   # negatif önbellek YOK
        assert await engine._market_gate_reason(Direction.LONG) == REASON_DAY
        assert len(fetcher.calls) == 3
        assert engine._market_gate_status()["gate_effective"] is True

    async def test_no_exchange_call_when_gate_is_disabled(self):
        """Kapı kapalıyken (varsayılan) TEK bir exchangeInfo isteği bile yok."""
        client = _OkClient()
        engine, _ = self._engine(_engine_cfg(scalper_market_gate=False), client)
        assert await engine._validate_market_gate_leader() is False
        assert client.calls == []

    async def test_validation_failure_does_not_close_the_entry_path(self):
        """Fail-open semantiği KORUNUR: doğrulama başarısız olsa da kapı
        girişleri engellemez (spec §C: lider verisi eksikliği risk olayı
        DEĞİLDİR). Değişen tek şey görünürlük."""
        engine, _ = self._engine(
            _engine_cfg(), _FailingClient(RuntimeError("ağ"))
        )
        await engine._validate_market_gate_leader()
        # Negatif önbellek dolu → veri denemesi bile yapılmaz, sonuç None.
        assert await engine._market_gate_reason(Direction.LONG) is None


class TestLeaderFailureNegativeCache:
    """Bulgu 1: başarısızlık önbelleğe alınmıyordu → her sinyalde boşa REST."""

    @staticmethod
    def _failing_fetcher() -> _LeaderFetcher:
        fetcher = _LeaderFetcher(
            {("BTCUSDT", "1d"): _LEADER_DAILY, ("BTCUSDT", "1m"): _LEADER_ENTRY,
             ("BTCUSDT", "15m"): _LEADER_INTRADAY},
            _DECISION_MS,
        )
        fetcher.fail_with = RuntimeError("Binance 418")
        return fetcher

    async def test_failure_suppresses_further_requests_for_retry_sec(self, monkeypatch):
        clock = _FakeClock(wall_ms=_DECISION_MS)
        _install_clock(monkeypatch, clock)
        fetcher = self._failing_fetcher()
        engine = _bare_engine(
            _engine_cfg(scalper_market_gate_retry_sec=60.0), fetcher
        )

        for _ in range(20):
            assert await engine._market_gate_reason(Direction.LONG) is None

        # Eskiden: 20 × 1 istek (+ KlineFetcher'ın 3 iç denemesi) = 60 istek.
        # Şimdi: TEK deneme, sonrası negatif önbellekten.
        assert len(fetcher.calls) == 1
        assert engine._market_gate_status()["consecutive_failures"] == 1

    async def test_retry_happens_after_the_window_expires(self, monkeypatch):
        clock = _FakeClock(wall_ms=_DECISION_MS)
        _install_clock(monkeypatch, clock)
        fetcher = self._failing_fetcher()
        engine = _bare_engine(
            _engine_cfg(scalper_market_gate_retry_sec=60.0), fetcher
        )

        await engine._market_gate_reason(Direction.LONG)
        assert len(fetcher.calls) == 1
        clock.advance(seconds=59.0)
        await engine._market_gate_reason(Direction.LONG)
        assert len(fetcher.calls) == 1          # pencere dolmadı
        clock.advance(seconds=2.0)
        await engine._market_gate_reason(Direction.LONG)
        assert len(fetcher.calls) == 2          # pencere doldu → yeniden dene

    async def test_recovery_clears_the_negative_cache_and_diagnostics(self, monkeypatch):
        clock = _FakeClock(wall_ms=_DECISION_MS)
        _install_clock(monkeypatch, clock)
        fetcher = self._failing_fetcher()
        engine = _bare_engine(_engine_cfg(), fetcher)

        await engine._market_gate_reason(Direction.LONG)
        assert engine._market_gate_status()["leader_ok"] is False
        fetcher.fail_with = None
        clock.advance(seconds=61.0)
        assert await engine._market_gate_reason(Direction.LONG) == REASON_DAY

        status = engine._market_gate_status()
        assert status["leader_ok"] is True and status["gate_effective"] is True
        assert status["consecutive_failures"] == 0
        assert status["last_error"] is None and status["last_ok_at"] is not None

    async def test_retry_sec_zero_disables_the_negative_cache(self, monkeypatch):
        """Operatör isterse eski davranışa dönebilir (0 = kapalı)."""
        clock = _FakeClock(wall_ms=_DECISION_MS)
        _install_clock(monkeypatch, clock)
        fetcher = self._failing_fetcher()
        engine = _bare_engine(_engine_cfg(scalper_market_gate_retry_sec=0), fetcher)
        for _ in range(3):
            await engine._market_gate_reason(Direction.LONG)
        assert len(fetcher.calls) == 3


class TestLeaderWarningRateLimit:
    """Bulgu 1: kalıcı arızada her sinyal bir WARNING satırı basıyordu."""

    async def test_at_most_one_warning_per_minute_per_kind(self, monkeypatch):
        clock = _FakeClock(wall_ms=_DECISION_MS)
        _install_clock(monkeypatch, clock)
        fetcher = TestLeaderFailureNegativeCache._failing_fetcher()
        engine = _bare_engine(
            _engine_cfg(scalper_market_gate_retry_sec=0.0), fetcher
        )
        warnings: List[str] = []
        engine.logger = SimpleNamespace(
            info=lambda *a, **kw: None,
            warning=lambda msg, *a, **kw: warnings.append(str(msg)),
            error=lambda *a, **kw: None,
        )

        for _ in range(10):
            await engine._market_gate_reason(Direction.LONG)
        assert len(warnings) == 1               # 10 hata, 1 satır
        assert len(fetcher.calls) == 10         # ama denemeler susturulmadı

        clock.advance(seconds=61.0)
        await engine._market_gate_reason(Direction.LONG)
        assert len(warnings) == 2               # pencere dolunca yeniden basar

    async def test_important_warning_is_not_muted_by_an_advisory_one(self, monkeypatch):
        """Uyarı türleri AYRI sayılır: 'uzama serisi kısa' tavsiyesi, hemen
        ardından gelen fail-open uyarısını SUSTURMAMALI."""
        clock = _FakeClock(wall_ms=_DECISION_MS)
        _install_clock(monkeypatch, clock)
        # Günlük seri kısa (2 kapanış) ama kapı yine de çalışır → tavsiye.
        short_daily = _daily_candles(_LEADER_DAILY_CLOSES[-2:], first_day_index=2)
        fetcher = _LeaderFetcher(
            {("BTCUSDT", "1d"): short_daily, ("BTCUSDT", "1m"): _LEADER_ENTRY,
             ("BTCUSDT", "15m"): _LEADER_INTRADAY},
            _DECISION_MS,
        )
        engine = _bare_engine(
            _engine_cfg(scalper_market_gate_run_pct=15.0), fetcher
        )
        warnings: List[str] = []
        engine.logger = SimpleNamespace(
            info=lambda *a, **kw: None,
            warning=lambda msg, *a, **kw: warnings.append(str(msg)),
            error=lambda *a, **kw: None,
        )
        await engine._market_gate_reason(Direction.LONG)
        assert len(warnings) == 1 and "İNERT" in warnings[0]

        # Şimdi çekim tamamen bozulsun — ÖNEMLİ uyarı yine de basılmalı.
        fetcher.fail_with = RuntimeError("Binance 418")
        engine._market_gate_cache.clear()
        await engine._market_gate_reason(Direction.LONG)
        assert len(warnings) == 2 and "UYGULANMADI" in warnings[1]

    async def test_short_daily_advisory_is_silent_when_run_sub_gate_is_off(self):
        """Varsayılan RUN_PCT=0 iken 'uzama serisi kısa' uyarısı GÜRÜLTÜDÜR."""
        short_daily = _daily_candles(_LEADER_DAILY_CLOSES[-2:], first_day_index=2)
        fetcher = _LeaderFetcher(
            {("BTCUSDT", "1d"): short_daily, ("BTCUSDT", "1m"): _LEADER_ENTRY,
             ("BTCUSDT", "15m"): _LEADER_INTRADAY},
            _DECISION_MS,
        )
        engine = _bare_engine(_engine_cfg(scalper_market_gate_run_pct=0.0), fetcher)
        warnings: List[str] = []
        engine.logger = SimpleNamespace(
            info=lambda *a, **kw: None,
            warning=lambda msg, *a, **kw: warnings.append(str(msg)),
            error=lambda *a, **kw: None,
        )
        await engine._market_gate_reason(Direction.LONG)
        assert warnings == []


class TestLeaderSnapshotFreshness:
    """Bulgu 2: canlı kapı liderin 60 sn'ye kadar BAYAT kapanışını kullanıyordu
    (harness kararın TAM mumunu kullanır) + gün sınırında dünün açılışı."""

    @staticmethod
    def _fetcher() -> _LeaderFetcher:
        return _LeaderFetcher(
            {("BTCUSDT", "1d"): _LEADER_DAILY, ("BTCUSDT", "1m"): _LEADER_ENTRY,
             ("BTCUSDT", "15m"): _LEADER_INTRADAY},
            _DECISION_MS,
        )

    async def test_round_start_refresh_forces_a_new_snapshot(self, monkeypatch):
        clock = _FakeClock(wall_ms=_DECISION_MS)
        _install_clock(monkeypatch, clock)
        fetcher = self._fetcher()
        engine = _bare_engine(_engine_cfg(), fetcher)

        await engine._refresh_leader_snapshot()
        assert len(fetcher.calls) == 3
        clock.advance(seconds=1.0)              # TTL içinde ama YENİ TUR
        await engine._refresh_leader_snapshot()
        assert len(fetcher.calls) == 6          # zorla tazelendi

    async def test_all_symbols_in_one_round_share_the_same_snapshot(self, monkeypatch):
        clock = _FakeClock(wall_ms=_DECISION_MS)
        _install_clock(monkeypatch, clock)
        fetcher = self._fetcher()
        engine = _bare_engine(_engine_cfg(), fetcher)

        await engine._refresh_leader_snapshot()
        assert len(fetcher.calls) == 3
        for _ in range(20):                      # turdaki 20 sembol
            clock.advance(seconds=0.2)
            await engine._market_gate_reason(Direction.LONG)
        assert len(fetcher.calls) == 3           # tur içinde ek istek YOK

    async def test_gate_disabled_refresh_makes_no_request(self):
        fetcher = self._fetcher()
        engine = _bare_engine(_engine_cfg(scalper_market_gate=False), fetcher)
        await engine._refresh_leader_snapshot()
        assert fetcher.calls == []

    async def test_tv_path_never_uses_a_snapshot_older_than_a_scan_round(
        self, monkeypatch
    ):
        """TV `external_signal` tarama turu DIŞINDA gelir; azami yaş
        `min(TTL, tarama aralığı)` ile sınırlıdır."""
        clock = _FakeClock(wall_ms=_DECISION_MS)
        _install_clock(monkeypatch, clock)
        fetcher = self._fetcher()
        engine = _bare_engine(
            _engine_cfg(scalper_scan_interval_seconds=10), fetcher
        )
        assert engine._market_gate_max_age() == 10.0

        await engine._market_gate_reason(Direction.LONG)
        assert len(fetcher.calls) == 3
        clock.advance(seconds=9.0)
        await engine._market_gate_reason(Direction.LONG)
        assert len(fetcher.calls) == 3           # hâlâ taze
        clock.advance(seconds=2.0)
        await engine._market_gate_reason(Direction.LONG)
        assert len(fetcher.calls) == 6           # bir turdan bayat → tazele

    async def test_cache_is_invalidated_at_the_utc_day_boundary(self, monkeypatch):
        """Bulgu 7: 'gün açılışı' UTC gün sınırında DEĞİŞİR. TTL'i sınıra taşan
        bir önbellek DÜNÜN açılışıyla karar verirdi (~1-2 dk'lık pencere)."""
        day_start = _LEADER_TODAY_START
        clock = _FakeClock(wall_ms=day_start + _DAY_MS - 5_000)   # 23:59:55
        _install_clock(monkeypatch, clock)
        fetcher = self._fetcher()
        engine = _bare_engine(_engine_cfg(), fetcher)

        await engine._market_gate_reason(Direction.LONG)
        assert len(fetcher.calls) == 3
        clock.advance(seconds=2.0)                # hâlâ aynı UTC günü
        await engine._market_gate_reason(Direction.LONG)
        assert len(fetcher.calls) == 3
        clock.advance(seconds=5.0)                # 00:00:02 — GÜN DEĞİŞTİ
        await engine._market_gate_reason(Direction.LONG)
        assert len(fetcher.calls) == 6            # TTL dolmadı ama gün değişti

    async def test_scan_tick_refreshes_the_leader_before_evaluating_symbols(self):
        """Tazeleme tarama turunun BAŞINDA — semboller değerlendirilmeden."""
        symbol_reservations.clear()
        try:
            fetcher = self._fetcher()
            cfg = _engine_cfg(
                scalper_symbol_allowlist="ETHUSDT,SOLUSDT",
                scalper_strategies="C",
                scalper_max_positions=5,
            )
            engine = _bare_engine(cfg, fetcher)
            engine.client = SimpleNamespace(
                get_all_positions=lambda: asyncio.sleep(0, result=[])
            )
            engine.executor = _NeverBlockedExecutor()
            engine.exits = _NoPositionsExits()
            engine._entries_ready = lambda: True

            order: List[str] = []
            original = engine._refresh_leader_snapshot

            async def _spy_refresh():
                order.append("refresh")
                await original()

            async def _spy_evaluate(symbol, strategies):
                order.append(f"evaluate:{symbol}")

            engine._refresh_leader_snapshot = _spy_refresh
            engine._evaluate_symbol = _spy_evaluate

            await engine._scan_tick()

            assert order[0] == "refresh"
            assert order[1:] == ["evaluate:ETHUSDT", "evaluate:SOLUSDT"]
            assert len(fetcher.calls) == 3        # tur başına TEK tazeleme
        finally:
            symbol_reservations.clear()


class TestLeaderDataSource:
    """Bulgu 3: canlı lider verisi TESTNET'ten, E7 mainnet'ten geliyor."""

    async def test_leader_series_comes_from_the_engine_kline_fetcher(self):
        """Kapı AYRI bir istemci kurmaz — motorun `self.fetcher`'ını kullanır
        (yani `SCALPER_MARKET_DATA_BASE_URL` gibi bir ayar tek noktadan
        hem stratejiyi hem kapıyı taşır)."""
        fetcher = TestLeaderSnapshotFreshness._fetcher()
        engine = _bare_engine(_engine_cfg(), fetcher)
        await engine._market_gate_reason(Direction.LONG)
        assert {c[0] for c in fetcher.calls} == {"BTCUSDT"}
        assert len(fetcher.calls) == 3           # HEPSİ bu fetcher'dan

    def test_status_reports_the_source_host_without_secrets(self):
        engine = _bare_engine(_engine_cfg(), SimpleNamespace(
            base_url="https://testnet.binancefuture.com/some/path?secret=abc"
        ))
        host = engine._market_gate_status()["leader_source_host"]
        assert host == "testnet.binancefuture.com"
        assert "secret" not in host and "/" not in host

    def test_status_source_host_is_none_without_a_fetcher(self):
        engine = ScalperEngine.__new__(ScalperEngine)
        engine.cfg = _engine_cfg()
        assert engine._market_gate_status()["leader_source_host"] is None


class TestBacktestTimeframeValidation:
    """Bulgu 6d: `_CANDLES_PER_DAY`'e '1d' eklenmesi, STRATEJİ zaman dilimi
    doğrulamasını gevşetmişti — `SCALPER_TF_REGIME=1d` artık sessizce kabul
    edilirdi (günde 1 mumla rejim hesaplanır)."""

    @staticmethod
    async def _empty_fetch(*a, **kw):
        return []

    async def test_daily_is_rejected_as_a_strategy_timeframe(self):
        """`SCALPER_TF_REGIME=1d` gürültülü bir ValueError almalı — sessizce
        günde 1 mumla rejim hesaplamamalı."""
        with pytest.raises(ValueError, match="Desteklenmeyen zaman dilimi"):
            await backtest_module.gather_symbol_data(
                self._empty_fetch, "BTCUSDT", 1, timeframes=("5m", "15m", "1d")
            )
        with pytest.raises(ValueError, match="Desteklenmeyen zaman dilimi"):
            await backtest_module.gather_symbol_data(
                self._empty_fetch, "BTCUSDT", 1, timeframes=("1d", "15m", "4h")
            )

    async def test_leader_entry_timeframe_uses_the_same_validation(self):
        with pytest.raises(ValueError, match="Desteklenmeyen zaman dilimi"):
            await backtest_module.gather_leader_series(
                self._empty_fetch, "BTCUSDT", 1, None, "1d", 3
            )

    def test_daily_entry_is_gone_because_nothing_read_it(self):
        """"1d" girdisi ÖLÜ idi: üretim kodu onu hiç okumuyordu (günlük mum
        sayısı `days + run_days + _LEADER_DAILY_EXTRA_DAYS` ile hesaplanır,
        önbellek anahtarı `kline_cache._INTERVAL_MS`'ten gelir). Tek fiilî
        etkisi doğrulamayı gevşetmekti."""
        assert "1d" not in backtest_module._CANDLES_PER_DAY

    async def test_leader_daily_series_still_works_without_the_entry(self):
        """Kaldırma lider günlük serisini BOZMAMALI."""
        seen: List[Tuple[str, str, int]] = []

        async def _fetch(symbol, interval, limit, end_time):
            seen.append((symbol, interval, limit))
            candles = {
                ("BTCUSDT", "1d"): _LEADER_DAILY,
                ("BTCUSDT", "1m"): _LEADER_ENTRY,
                ("BTCUSDT", "15m"): _LEADER_INTRADAY,
            }[(symbol, interval)]
            cutoff = end_time if end_time is not None else _DECISION_MS
            return [c for c in candles if c.close_time <= cutoff][-limit:]

        leader = await backtest_module.gather_leader_series(
            _fetch, "BTCUSDT", days=1, end_time=_DECISION_MS,
            tf_entry="1m", run_days=3,
        )
        assert leader.daily_closes == _LEADER_DAILY_CLOSES
        assert "1d" in {interval for _, interval, _ in seen}


class TestBacktestReportMarketGate:
    """Bulgu 6b: `metadata['market_gate']` JSON rapora HİÇ ulaşmıyordu —
    yani bir koşunun kapıyla mı kapısız mı yapıldığı rapordan okunamıyordu."""

    @staticmethod
    def _write(tmp_path, metadata: Dict[str, Any]):
        path = backtest_module.write_json_report(
            [], days=1, symbols=["BTCUSDT"], strategy_names="C",
            cfg=SimpleNamespace(), run_metadata=metadata, output_dir=tmp_path,
        )
        import json
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)

    def test_market_gate_metadata_reaches_the_report(self, tmp_path):
        gate_meta = {
            "leader": "BTCUSDT", "day_pct_threshold": 1.3,
            "run_pct_threshold": 0.0, "run_days": 3,
            "entry_tf": "5m", "intraday_tf": MARKET_GATE_INTRADAY_TF,
            "daily": {"candles": 29, "first_close_ms": 1, "last_close_ms": 2,
                      "first_close_utc": "a", "last_close_utc": "b"},
            "entry": {"candles": 6198, "first_close_ms": 1, "last_close_ms": 2,
                      "first_close_utc": "a", "last_close_utc": "b"},
            "intraday": {"candles": 2116, "first_close_ms": 1, "last_close_ms": 2,
                         "first_close_utc": "a", "last_close_utc": "b"},
        }
        payload = self._write(tmp_path, {"market_gate": gate_meta})
        assert payload["provenance"]["market_gate"] == gate_meta

    def test_report_shape_is_stable_when_the_gate_is_off(self, tmp_path):
        payload = self._write(tmp_path, {})
        assert "market_gate" in payload["provenance"]
        assert payload["provenance"]["market_gate"] is None

    def test_window_snapshot_reports_coverage_not_only_counts(self):
        snap = backtest_module._leader_window_snapshot(
            [c.close_time for c in _LEADER_DAILY], len(_LEADER_DAILY)
        )
        assert snap["candles"] == len(_LEADER_DAILY)
        assert snap["first_close_ms"] == _LEADER_DAILY[0].close_time
        assert snap["last_close_ms"] == _LEADER_DAILY[-1].close_time
        assert snap["first_close_utc"].startswith("1970-01-01")
        empty = backtest_module._leader_window_snapshot([], 0)
        assert empty["candles"] == 0 and empty["first_close_ms"] is None


class TestParityViaHarnessOwnDerivation:
    """Bulgu 6f: parite testi `LeaderSeries`'i ELLE kuruyordu — yani harness'ın
    GERÇEK türetme yolu (`gather_leader_series`) hiç test edilmiyordu; o yolda
    bir hata olsa test yine geçerdi. Artık seri harness'ın KENDİ fonksiyonuyla
    kurulur ve canlı motorun türettiği ÜÇLÜYLE karşılaştırılır."""

    @staticmethod
    async def _fake_fetch(symbol, interval, limit, end_time):
        series = {
            ("BTCUSDT", "1d"): _LEADER_DAILY,
            ("BTCUSDT", "1m"): _LEADER_ENTRY,
            ("BTCUSDT", "15m"): _LEADER_INTRADAY,
        }
        candles = series.get((symbol, interval))
        if candles is None:
            raise AssertionError(f"Beklenmeyen seri: {symbol} {interval}")
        cutoff = end_time if end_time is not None else _DECISION_MS
        return [c for c in candles if c.close_time <= cutoff][-limit:]

    async def test_harness_derivation_matches_the_live_engine(self):
        leader = await backtest_module.gather_leader_series(
            self._fake_fetch, "BTCUSDT", days=1, end_time=_DECISION_MS,
            tf_entry="1m", run_days=3,
        )
        # Harness'ın KENDİ türettiği seri (elle kurulmuş dataclass DEĞİL).
        harness_inputs = leader.inputs_at(_DECISION_MS)

        fetcher = _LeaderFetcher(
            {("BTCUSDT", "1d"): _LEADER_DAILY, ("BTCUSDT", "1m"): _LEADER_ENTRY,
             ("BTCUSDT", "15m"): _LEADER_INTRADAY},
            _DECISION_MS,
        )
        engine = _bare_engine(_engine_cfg(), fetcher)
        snapshot = await engine._leader_market_snapshot()

        assert harness_inputs is not None and snapshot is not None
        assert harness_inputs[0] == snapshot["day_open"] == _LEADER_TRUE_DAY_OPEN
        assert harness_inputs[1] == snapshot["last_close"]
        assert list(harness_inputs[2]) == list(snapshot["daily_closes"])
        # ...ve aynı KARARI verirler.
        cfg = _engine_cfg()
        assert evaluate_market_gate(
            Direction.LONG, *harness_inputs, cfg
        ) == await engine._market_gate_reason(Direction.LONG)

    async def test_harness_derivation_also_matches_on_the_fallback_path(self):
        """Günün ilk 15 dakikası: iki taraf da vekile (önceki günlük kapanış)
        düşer — türetme yolu ayrıştığında bu test kırılır."""
        early = _LEADER_TODAY_START + 60_000        # 00:01 UTC
        leader = await backtest_module.gather_leader_series(
            self._fake_fetch, "BTCUSDT", days=1, end_time=early,
            tf_entry="1m", run_days=3,
        )
        assert leader.inputs_at(early) is None      # o anda giriş mumu yok
        day_open, source = resolve_day_open(
            leader.intraday_open_times, leader.intraday_opens,
            leader.intraday_close_times, _LEADER_DAILY_CLOSES, early,
        )
        assert day_open == _LEADER_DAILY_CLOSES[-1]
        assert source == DAY_OPEN_SOURCE_PREV_CLOSE


class TestRunMetadataPropagation:
    """Bulgu 6b'nin KÖK NEDENİ (uçtan uca koşuda yakalandı, 2026-08-23).

    `run_backtest` önce `run_metadata.update(metadata)` yapıyor, SONRA
    `metadata["market_gate"] = ...` ile YENİ bir anahtar ekliyordu. `update`
    sığ bir kopya olduğu için iç içe sözlüklere yapılan yerinde değişiklikler
    (`data_windows[symbol] = ...`) dışarı ulaşıyor ama sonradan eklenen yeni
    anahtar SESSİZCE kayboluyordu — kapı metadata'sı üretiliyor, JSON rapora
    hiç ulaşmıyordu. Düzeltme: `metadata` artık `run_metadata`'nın KENDİSİ."""

    async def test_market_gate_metadata_reaches_the_caller(self, monkeypatch):
        captured: Dict[str, Any] = {}

        async def _fake_gather_symbol_data(*a, **kw):
            return {tf: [] for tf in kw.get("timeframes", ("5m", "15m", "4h"))}

        def _fake_simulate(*a, **kw):
            return []

        async def _fake_leader(*a, **kw):
            return LeaderSeries(
                symbol="BTCUSDT",
                entry_close_times=[c.close_time for c in _LEADER_ENTRY],
                entry_closes=[c.close for c in _LEADER_ENTRY],
                daily_close_times=[c.close_time for c in _LEADER_DAILY],
                daily_closes=[c.close for c in _LEADER_DAILY],
                intraday_open_times=[c.open_time for c in _LEADER_INTRADAY],
                intraday_opens=[c.open for c in _LEADER_INTRADAY],
                intraday_close_times=[c.close_time for c in _LEADER_INTRADAY],
            )

        monkeypatch.setattr(
            backtest_module, "gather_symbol_data", _fake_gather_symbol_data
        )
        monkeypatch.setattr(backtest_module, "simulate_symbol", _fake_simulate)
        monkeypatch.setattr(
            backtest_module, "gather_leader_series", _fake_leader
        )

        cfg = _SimCfg()
        cfg.scalper_market_gate = True
        await backtest_module.run_backtest(
            days=1, symbols=["ETHUSDT"], strategy_names="C",
            cfg=cfg, run_metadata=captured,
        )

        gate = captured.get("market_gate")
        assert gate is not None, "kapı metadata'sı ÇAĞIRANA ULAŞMADI"
        assert gate["leader"] == "BTCUSDT"
        assert gate["intraday_tf"] == MARKET_GATE_INTRADAY_TF
        # Eşik alanları AÇIKÇA "_threshold" — ölçülen büyüklükle karışmasın.
        assert "day_pct_threshold" in gate and "run_pct_threshold" in gate
        assert "run_pct" not in gate
        # Yalnız sayı değil KAPSAM da raporlanır.
        assert gate["daily"]["candles"] == len(_LEADER_DAILY)
        assert gate["daily"]["first_close_ms"] == _LEADER_DAILY[0].close_time
        assert gate["intraday"]["last_close_utc"] is not None
        # Var olan anahtarlar da korunur (regresyon).
        assert "data_windows" in captured and "git_sha" in captured

    async def test_no_market_gate_key_when_the_gate_is_off(self, monkeypatch):
        captured: Dict[str, Any] = {}

        async def _fake_gather_symbol_data(*a, **kw):
            return {tf: [] for tf in kw.get("timeframes", ("5m", "15m", "4h"))}

        monkeypatch.setattr(
            backtest_module, "gather_symbol_data", _fake_gather_symbol_data
        )
        monkeypatch.setattr(backtest_module, "simulate_symbol", lambda *a, **kw: [])

        cfg = _SimCfg()
        cfg.scalper_market_gate = False
        await backtest_module.run_backtest(
            days=1, symbols=["ETHUSDT"], strategy_names="C",
            cfg=cfg, run_metadata=captured,
        )
        assert captured.get("market_gate") is None


class TestLeaderValidationTimeout:
    """Doğrulama motor AÇILIŞINI bloke etmemeli.

    `ImprovedBinanceClient._request_with_retry` 3 deneme × 60 sn timeout +
    backoff yapar; ulaşılamayan bir borsada sınırsız `await` motoru
    dakikalarca açılmadan bırakırdı. Zaman sınırı aşılırsa kapı "degraded"
    olur (fail-open korunur) ve ilk tarama turunda yeniden denenir."""

    async def test_slow_exchange_does_not_block_startup(self, monkeypatch):
        monkeypatch.setattr(
            ScalperEngine, "_MARKET_GATE_VALIDATE_TIMEOUT", 0.05
        )

        class _SlowClient:
            async def get_symbol_filters(self, symbol):
                await asyncio.sleep(30)      # asla dönmez

        engine = _bare_engine(_engine_cfg(), _LeaderFetcher({}, _DECISION_MS))
        engine.client = _SlowClient()
        errors: List[str] = []
        engine.logger = SimpleNamespace(
            info=lambda *a, **kw: None,
            warning=lambda *a, **kw: None,
            error=lambda msg, *a, **kw: errors.append(str(msg)),
        )

        started = asyncio.get_running_loop().time()
        assert await engine._validate_market_gate_leader() is False
        elapsed = asyncio.get_running_loop().time() - started

        assert elapsed < 5.0, f"açılış bloke oldu: {elapsed:.1f} sn"
        assert len(errors) == 1 and "degraded" in errors[0]
        status = engine._market_gate_status()
        assert status["gate_effective"] is False
        assert "TimeoutError" in status["last_error"]


# ==========================================================================
# Ek 5) Düşmanca inceleme turu 2 — kapı görünürlüğünün YANLIŞ-YEŞİL yolları
# ==========================================================================

class TestGateEffectiveIsHonest:
    """`gate_effective` RUNBOOK'un ZORUNLU doğrulamasının tek kapısı; yanlış
    yeşil vermesi "soak başladı" beyanını kanıtsız bırakır."""

    @staticmethod
    def _engine(**over) -> Tuple[ScalperEngine, _LeaderFetcher]:
        fetcher = TestLeaderSnapshotFreshness._fetcher()
        engine = _bare_engine(_engine_cfg(**over), fetcher)
        return engine, fetcher

    async def test_zero_thresholds_are_not_effective(self):
        """İki eşik de 0 → kapı hiçbir şeyi engellemez. Eskiden `true` derdi.

        `RUN_PCT` varsayılanı 0'a çekildiği için bu senaryo gerçekçi:
        `.env`'e `SCALPER_MARKET_GATE_DAY_PCT=0` (ya da boş) yazan operatör
        kapı tamamen ÖLÜYKEN yeşil ışık görürdü."""
        engine, _ = self._engine(
            scalper_market_gate_day_pct=0.0, scalper_market_gate_run_pct=0.0
        )
        assert await engine._market_gate_reason(Direction.LONG) is None
        status = engine._market_gate_status()
        assert status["leader_ok"] is True and status["stale"] is False
        assert status["gate_effective"] is False        # koruma YOK → yeşil YOK
        assert status["thresholds"] == {
            "day_pct": 0.0, "run_pct": 0.0, "run_days": 3
        }

    async def test_one_active_threshold_is_enough(self):
        engine, _ = self._engine(
            scalper_market_gate_day_pct=1.0, scalper_market_gate_run_pct=0.0
        )
        await engine._market_gate_reason(Direction.LONG)
        assert engine._market_gate_status()["gate_effective"] is True

    async def test_stale_snapshot_is_not_effective_and_hides_metrics(
        self, monkeypatch
    ):
        """Bayat görüntü: status karar yolunun tazelik testini uygulamıyordu,
        yani `day_drift_pct` SINIRSIZ yaşta olabiliyor ve RUNBOOK'un
        "kapının ŞU AN gördüğü" ifadesi yanlış oluyordu."""
        clock = _FakeClock(wall_ms=_DECISION_MS)
        _install_clock(monkeypatch, clock)
        engine, _ = self._engine(scalper_scan_interval_seconds=30)
        await engine._market_gate_reason(Direction.LONG)
        fresh = engine._market_gate_status()
        assert fresh["gate_effective"] is True and fresh["stale"] is False
        assert fresh["day_drift_pct"] is not None

        clock.advance(seconds=61.0)              # > 2 × 30 sn
        stale = engine._market_gate_status()
        assert stale["stale"] is True
        assert stale["gate_effective"] is False
        assert stale["day_drift_pct"] is None
        assert stale["run_drift_pct"] is None
        assert stale["day_open_source"] is None
        assert stale["snapshot_age_sec"] == pytest.approx(61.0, abs=0.5)
        # ...ama SON BAŞARI zamanı korunur (ne zamandan beri bayat?).
        assert stale["last_ok_at"] is not None


class TestDayBoundaryUsesContentDay:
    """İNCELEME BULGUSU: gün damgası DUVAR SAATİNDEN türetiliyordu; oysa
    `day_open`'ın günü son KAPANAN giriş mumunun `close_time`'ı ile belirlenir.
    İkisi 00:00 UTC'de değil, yeni günün İLK giriş mumu kapandığında hizalanır
    — yani guard tam da yazıldığı pencereyi kaçırıyor ve o pencerede DÜNÜN
    açılışıyla karar veriliyordu (üstelik `day_open_source: intraday_open`
    etiketiyle, "kesin ölçüm" diye)."""

    async def test_cache_is_dropped_while_content_still_belongs_to_yesterday(
        self, monkeypatch
    ):
        day_start = _LEADER_TODAY_START
        clock = _FakeClock(wall_ms=day_start + _DAY_MS - 30_000)   # 23:59:30
        _install_clock(monkeypatch, clock)
        fetcher = TestLeaderSnapshotFreshness._fetcher()
        engine = _bare_engine(_engine_cfg(), fetcher)

        await engine._market_gate_reason(Direction.LONG)
        assert len(fetcher.calls) == 3
        # 00:00:30 — duvar saati YENİ günde, ama fetcher'ın verdiği son
        # kapanmış giriş mumu hâlâ DÜNE ait. Damga içerikten türediği için
        # önbellek geçersiz olmalı: aksi hâlde dünün açılışı servis edilirdi.
        clock.advance(seconds=60.0)
        await engine._market_gate_reason(Direction.LONG)
        assert len(fetcher.calls) == 6

    async def test_cache_holds_while_wall_and_content_agree(self, monkeypatch):
        clock = _FakeClock(wall_ms=_DECISION_MS)
        _install_clock(monkeypatch, clock)
        fetcher = TestLeaderSnapshotFreshness._fetcher()
        engine = _bare_engine(_engine_cfg(), fetcher)
        await engine._market_gate_reason(Direction.LONG)
        clock.advance(seconds=5.0)
        await engine._market_gate_reason(Direction.LONG)
        assert len(fetcher.calls) == 3           # aynı gün → önbellek geçerli


class TestRefreshNeverBlocksTheScanRound:
    """Kullanıcı şartı: TRADE HİÇ DURMASIN. Kapı tavsiye niteliğinde bir
    alt-sistemdir; `_scan_tick`'in İLK adımı olduğu için `await`i sınırsız
    bırakmak, lider erişilemezken turu `KlineFetcher`'ın 3 deneme ×
    (okuma timeout'u + backoff) zinciri kadar (~48 sn) bloke ederdi."""

    async def test_slow_leader_does_not_stall_the_round(self, monkeypatch):
        monkeypatch.setattr(
            ScalperEngine, "_MARKET_GATE_REFRESH_TIMEOUT", 0.05
        )

        class _HangingFetcher(_LeaderFetcher):
            async def get_klines(self, symbol, interval, limit=200, end_time=None):
                await asyncio.sleep(30)

        engine = _bare_engine(_engine_cfg(), _HangingFetcher({}, _DECISION_MS))
        warnings: List[str] = []
        engine.logger = SimpleNamespace(
            info=lambda *a, **kw: None,
            warning=lambda msg, *a, **kw: warnings.append(str(msg)),
            error=lambda *a, **kw: None,
        )

        started = asyncio.get_running_loop().time()
        await engine._refresh_leader_snapshot()
        elapsed = asyncio.get_running_loop().time() - started

        assert elapsed < 5.0, f"tarama turu bloke oldu: {elapsed:.1f} sn"
        assert len(warnings) == 1 and "BEKLETİLMEDİ" in warnings[0]
        status = engine._market_gate_status()
        assert status["gate_effective"] is False
        assert "TimeoutError" in status["last_error"]
        # Negatif önbellek kuruldu → sonraki turlar aynı bedeli ödemez.
        assert engine._market_gate_retry_after > 0.0

    async def test_unexpected_error_does_not_kill_the_round(self):
        class _BoomFetcher(_LeaderFetcher):
            async def get_klines(self, *a, **kw):
                raise KeyboardInterrupt("beklenmedik")   # except Exception dışı

        engine = _bare_engine(_engine_cfg(), TestLeaderSnapshotFreshness._fetcher())
        warnings: List[str] = []
        engine.logger = SimpleNamespace(
            info=lambda *a, **kw: None,
            warning=lambda msg, *a, **kw: warnings.append(str(msg)),
            error=lambda *a, **kw: None,
        )

        async def _boom(max_age=None):
            raise ValueError("beklenmedik")

        engine._leader_market_snapshot = _boom
        await engine._refresh_leader_snapshot()      # yükseltmemeli
        assert len(warnings) == 1 and "SÜRÜYOR" in warnings[0]


class TestFlappingFailureIsVisible:
    """İNCELEME BULGUSU: toparlanma TÜM sayaçları sıfırlıyordu. 60 sn bozuk /
    60 sn sağlıklı dönen bir kapı `/scalper/status`'te tertemiz görünürdü,
    oysa zamanın yarısında KORUMA YOKTUR."""

    async def test_total_counter_survives_recovery(self, monkeypatch):
        clock = _FakeClock(wall_ms=_DECISION_MS)
        _install_clock(monkeypatch, clock)
        fetcher = TestLeaderSnapshotFreshness._fetcher()
        engine = _bare_engine(_engine_cfg(), fetcher)

        for episode in range(3):
            fetcher.fail_with = RuntimeError("Binance 418")
            engine._market_gate_cache.clear()
            await engine._market_gate_reason(Direction.LONG)
            fetcher.fail_with = None
            clock.advance(seconds=61.0)
            await engine._market_gate_reason(Direction.LONG)

        status = engine._market_gate_status()
        assert status["consecutive_failures"] == 0        # şu an sağlıklı
        assert status["failures_total"] == 3              # ...ama 3 epizot oldu
        assert status["last_failure_at"] is not None
        assert status["leader_ok"] is True

    async def test_each_episode_logs_again_after_recovery(self, monkeypatch):
        """Toparlanma uyarı penceresini de sıfırlar: aksi hâlde T=0 arıza →
        T=30 toparlanma → T=40 YENİ arıza dizisinde ikinci epizot HİÇ
        loglanmazdı (60 sn penceresi ilk epizottan sayılırdı)."""
        clock = _FakeClock(wall_ms=_DECISION_MS)
        _install_clock(monkeypatch, clock)
        fetcher = TestLeaderSnapshotFreshness._fetcher()
        engine = _bare_engine(_engine_cfg(scalper_market_gate_retry_sec=0.0), fetcher)
        warnings: List[str] = []
        engine.logger = SimpleNamespace(
            info=lambda *a, **kw: None,
            warning=lambda msg, *a, **kw: warnings.append(str(msg)),
            error=lambda *a, **kw: None,
        )

        fetcher.fail_with = RuntimeError("418")
        await engine._market_gate_reason(Direction.LONG)     # T=0  → 1. satır
        assert len(warnings) == 1

        fetcher.fail_with = None
        clock.advance(seconds=30.0)
        engine._market_gate_cache.clear()
        await engine._market_gate_reason(Direction.LONG)     # T=30 → toparlandı

        fetcher.fail_with = RuntimeError("418")
        clock.advance(seconds=10.0)
        engine._market_gate_cache.clear()
        await engine._market_gate_reason(Direction.LONG)     # T=40 → 2. satır
        assert len(warnings) == 2, "ikinci arıza epizodu sessiz kaldı"


class TestNegativeCacheKeepsAUsableSnapshot:
    """İNCELEME BULGUSU: negatif önbellek kontrolü önbellek OKUMASINDAN
    ÖNCEYDİ; araya giren TEK bir geçici hata, elde TAZE ve geçerli bir görüntü
    VARKEN kapıyı `RETRY_SEC` boyunca tamamen kör bırakıyordu."""

    async def test_fresh_cache_is_served_during_the_retry_window(self, monkeypatch):
        clock = _FakeClock(wall_ms=_DECISION_MS)
        _install_clock(monkeypatch, clock)
        fetcher = TestLeaderSnapshotFreshness._fetcher()
        engine = _bare_engine(_engine_cfg(scalper_scan_interval_seconds=30), fetcher)

        await engine._market_gate_reason(Direction.LONG)     # taze görüntü
        assert len(fetcher.calls) == 3

        # Zorla tazeleme geçici bir hata alsın → negatif önbellek kurulur.
        fetcher.fail_with = RuntimeError("geçici")
        clock.advance(seconds=1.0)
        await engine._refresh_leader_snapshot()
        assert engine._market_gate_retry_after > 0.0

        # ...ama 1 sn önceki görüntü hâlâ `max_age` içinde: kapı KÖR DEĞİL.
        assert await engine._market_gate_reason(Direction.LONG) == REASON_DAY

    async def test_no_new_fetch_during_the_retry_window(self, monkeypatch):
        clock = _FakeClock(wall_ms=_DECISION_MS)
        _install_clock(monkeypatch, clock)
        fetcher = TestLeaderSnapshotFreshness._fetcher()
        fetcher.fail_with = RuntimeError("kalıcı")
        engine = _bare_engine(_engine_cfg(), fetcher)
        for _ in range(10):
            await engine._market_gate_reason(Direction.LONG)
        assert len(fetcher.calls) == 1           # yeni ÇEKİM yok


class TestDailyLimitWeightCap:
    async def test_limit_never_exceeds_binance_weight_1_boundary(self):
        fetcher = TestLeaderSnapshotFreshness._fetcher()
        engine = _bare_engine(_engine_cfg(scalper_market_gate_run_days=500), fetcher)
        await engine._market_gate_reason(Direction.LONG)
        daily = [c for c in fetcher.calls if c[1] == "1d"][0]
        assert daily[2] == 100, "limit > 100 → Binance ağırlığı 1'den 2'ye çıkar"


class TestLeaderSourceHostStripsUserinfo:
    def test_userinfo_is_not_leaked(self):
        engine = _bare_engine(_engine_cfg(), SimpleNamespace(
            base_url="https://kullanici:parola@testnet.binancefuture.com/fapi"
        ))
        host = engine._market_gate_status()["leader_source_host"]
        assert host == "testnet.binancefuture.com"
        assert "parola" not in host and "@" not in host


# ==========================================================================
# Ek 6) scripts/decompose_gate_runs.py — ayrıştırma aracının doğruluğu
# ==========================================================================
#
# Bu betik E7/D15'teki "engelleme vs yeniden tahsis" iddiasının KANITIDIR
# (CLAUDE.md yasak #6: log/rapor yolu olmayan sonuç kanıt sayılmaz). `logs/`
# commit'lenmediği için testler betiğin ARİTMETİĞİNİ sentetik veriyle çiviler.

def _dec_trade(symbol, entry, direction, pnl, *, minutes=10.0, tp1=True,
               exit_reason="TRAIL", exit_time=None):
    return {
        "symbol": symbol, "entry_time": entry, "direction": direction,
        "pnl": pnl, "duration_minutes": minutes,
        "exit_time": exit_time if exit_time is not None else entry + 1,
        "exit_reason": exit_reason,
        "legs": [{"label": "TP1"}] if tp1 else [],
    }


def _dec_run(trades):
    return {"trades": trades, "overall": {"total_pnl": sum(t["pnl"] for t in trades)}}


class TestDecomposeGateRuns:
    @staticmethod
    def _module():
        import importlib.util
        from pathlib import Path
        path = (
            Path(__file__).resolve().parent.parent
            / "scripts" / "decompose_gate_runs.py"
        )
        spec = importlib.util.spec_from_file_location("decompose_gate_runs", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_splits_delta_into_blocking_and_reallocation(self):
        mod = self._module()
        base = _dec_run([
            _dec_trade("A", 1, "LONG", -100.0),      # kapı engelleyecek
            _dec_trade("B", 2, "SHORT", +50.0),      # ortak
        ])
        gated = _dec_run([
            _dec_trade("B", 2, "SHORT", +50.0),      # ortak (PnL aynı)
            _dec_trade("C", 3, "SHORT", +30.0),      # boşalan slota YENİ giren
        ])
        out = mod._decompose(base, gated)
        assert out["delta"] == pytest.approx(130.0)
        assert out["block_only"] == pytest.approx(100.0)   # −(−100)
        assert out["realloc"] == pytest.approx(30.0)
        assert out["block_only"] + out["realloc"] == pytest.approx(out["delta"])
        assert len(out["blocked"]) == 1 and len(out["added"]) == 1
        assert out["mismatched"] == []

    def test_dirty_attribution_is_flagged_not_hidden(self):
        """Ortak bir işlemin PnL'i iki koşuda farklıysa ayrıştırma GEÇERSİZDİR
        — betik bunu sessizce yutmamalı."""
        mod = self._module()
        base = _dec_run([_dec_trade("B", 2, "SHORT", +50.0)])
        gated = _dec_run([_dec_trade("B", 2, "SHORT", +51.0)])
        out = mod._decompose(base, gated)
        assert out["mismatched"] == [("B", 2, "SHORT")]

    def test_reaper_candidate_requires_both_conditions(self):
        """D4: yaş limiti AŞILMIŞ **ve** TP1 GÖRÜLMEMİŞ (trailing_active muaf)."""
        mod = self._module()
        assert mod._is_reaper_candidate(
            _dec_trade("A", 1, "LONG", -1.0, minutes=481.0, tp1=False)
        )
        assert not mod._is_reaper_candidate(       # TP1 gördü → muaf
            _dec_trade("A", 1, "LONG", -1.0, minutes=481.0, tp1=True)
        )
        assert not mod._is_reaper_candidate(       # 8 saati doldurmadı
            _dec_trade("A", 1, "LONG", -1.0, minutes=479.0, tp1=False)
        )

    def test_max_drawdown_uses_exit_ordering(self):
        mod = self._module()
        trades = [
            _dec_trade("A", 1, "LONG", +100.0, exit_time=10),
            _dec_trade("B", 2, "LONG", -300.0, exit_time=20),
            _dec_trade("C", 3, "LONG", +50.0, exit_time=30),
        ]
        assert mod._max_drawdown(trades) == pytest.approx(300.0)

    def test_profit_factor(self):
        mod = self._module()
        trades = [
            _dec_trade("A", 1, "LONG", +200.0),
            _dec_trade("B", 2, "LONG", -100.0),
        ]
        assert mod._pf(trades) == pytest.approx(2.0)
        assert mod._pf([_dec_trade("A", 1, "LONG", +5.0)]) == float("inf")

    # -- ikinci-derece terimin MEKANİZMASI ---------------------------------
    #
    # D15/E7 belgesi "kapasite 0, cooldown 0, sembol-içi işgal penceresi %100"
    # diyor. Bu bir ATIF iddiasıdır; sınıflandırıcı yanlışsa belge yanlış olur.

    def test_realloc_inside_a_blocked_trades_window_is_occupancy(self):
        """Engellenen işlemin [giriş, çıkış] penceresi İÇİNDE açılan yeni
        işlem, taban koşuda sembol-içi tek-pozisyon kuralıyla ZATEN
        imkânsızdı — kapasiteye hiç sıra gelmez."""
        mod = self._module()
        blocked = [_dec_trade("A", 100, "LONG", -50.0, exit_time=200)]
        added = [_dec_trade("A", 150, "SHORT", +30.0, exit_time=250)]
        out = mod._classify_realloc(added, blocked, 60 * 60_000)
        assert len(out["occupancy"]) == 1
        assert out["cooldown"] == [] and out["capacity_or_other"] == []

    def test_realloc_after_a_blocked_losers_cooldown_is_cooldown(self):
        mod = self._module()
        blocked = [_dec_trade("A", 100, "LONG", -50.0, exit_time=200)]
        added = [_dec_trade("A", 250, "SHORT", +30.0, exit_time=300)]
        out = mod._classify_realloc(added, blocked, cooldown_ms=100)
        assert len(out["cooldown"]) == 1 and out["occupancy"] == []

    def test_cooldown_needs_a_losing_blocked_trade(self):
        """Kazanan bir işlem cooldown başlatmaz (motor: `pnl < 0` ya da SL)."""
        mod = self._module()
        blocked = [_dec_trade("A", 100, "LONG", +50.0, exit_time=200)]
        added = [_dec_trade("A", 250, "SHORT", +30.0, exit_time=300)]
        out = mod._classify_realloc(added, blocked, cooldown_ms=100)
        assert len(out["capacity_or_other"]) == 1 and out["cooldown"] == []

    def test_other_symbol_realloc_falls_through_to_capacity(self):
        mod = self._module()
        blocked = [_dec_trade("A", 100, "LONG", -50.0, exit_time=200)]
        added = [_dec_trade("B", 150, "SHORT", +30.0, exit_time=250)]
        out = mod._classify_realloc(added, blocked, 60 * 60_000)
        assert len(out["capacity_or_other"]) == 1

    def test_every_added_trade_is_classified_exactly_once(self):
        """Toplam korunmalı: bir işlem birden fazla mekanizmaya uyabilir,
        atanan yalnız EN DAR açıklamadır."""
        mod = self._module()
        blocked = [_dec_trade("A", 100, "LONG", -50.0, exit_time=200)]
        added = [
            _dec_trade("A", 150, "SHORT", +10.0, exit_time=260),   # occupancy
            _dec_trade("A", 260, "SHORT", +20.0, exit_time=300),   # cooldown
            _dec_trade("B", 400, "LONG", +30.0, exit_time=500),    # capacity
        ]
        out = mod._classify_realloc(added, blocked, cooldown_ms=100)
        assert sum(len(v) for v in out.values()) == len(added)
        assert [len(out[k]) for k in
                ("occupancy", "cooldown", "capacity_or_other")] == [1, 1, 1]

    def test_zero_cooldown_config_disables_the_cooldown_bucket(self):
        mod = self._module()
        blocked = [_dec_trade("A", 100, "LONG", -50.0, exit_time=200)]
        added = [_dec_trade("A", 250, "SHORT", +30.0, exit_time=300)]
        out = mod._classify_realloc(added, blocked, cooldown_ms=0)
        assert len(out["capacity_or_other"]) == 1 and out["cooldown"] == []

    def test_cooldown_minutes_are_read_from_the_report_provenance(self):
        mod = self._module()
        report = {"provenance": {"scalper_config":
                                 {"scalper_loss_cooldown_minutes": 60}}}
        assert mod._loss_cooldown_ms(report) == 60 * 60_000
        assert mod._loss_cooldown_ms({}) == 0
        assert mod._loss_cooldown_ms(
            {"provenance": {"scalper_config": {"scalper_loss_cooldown_minutes": None}}}
        ) == 0

    def test_decompose_exposes_the_mechanism_split(self):
        mod = self._module()
        base = _dec_run([
            _dec_trade("A", 100, "LONG", -50.0, exit_time=200),
            _dec_trade("B", 2, "SHORT", +50.0),
        ])
        gated = _dec_run([
            _dec_trade("B", 2, "SHORT", +50.0),
            _dec_trade("A", 150, "SHORT", +30.0, exit_time=250),
        ])
        out = mod._decompose(base, gated)
        assert len(out["mechanism"]["occupancy"]) == 1
        assert out["mechanism"]["capacity_or_other"] == []
