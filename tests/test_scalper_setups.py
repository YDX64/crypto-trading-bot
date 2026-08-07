"""
src/strategies/scalper/setups.py (ve regime.py, get_enabled) için birim
testleri.

Sentetik mum üreticileri modül altında helper olarak tanımlıdır. Her
strateji için: pozitif (sinyal üretir) ve en az bir negatif (None döner)
senaryo kapsanır. Tüm sayısal eşikler gerçek fonksiyon çıktıları ile
doğrulanmıştır (elle tahmin edilmemiştir).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pytest

# position_manager.py (executor.py'nin bağımlılığı) src.models.signal'i içe
# aktarır; SignalModel'in "WaitingSignalModel" ilişkisi SQLAlchemy mapper
# yapılandırması sırasında çözülebilsin diye bu modül de içe aktarılmalı
# (aksi halde PositionModel() ilk kez örneklendiğinde InvalidRequestError).
import src.models.waiting_signal  # noqa: F401
from src.strategies.scalper.executor import ScalpExecutor, ScalpPosition
from src.strategies.scalper.indicators import atr
from src.strategies.scalper.regime import detect_regime
from src.strategies.scalper import setups as setups_module
from src.strategies.scalper.setups import (
    ALL_STRATEGIES,
    StrategyA,
    StrategyB,
    StrategyC,
    StrategyD,
    get_enabled,
    passes_equilibrium,
)
from src.strategies.scalper.types import (
    Candle,
    Direction,
    Regime,
    ScalpSignal,
    StrategyContext,
)


# --------------------------------------------------------------------------
# Sentetik mum üreticileri (helper'lar)
# --------------------------------------------------------------------------

def _mk(i: int, high: float, low: float, close: float | None = None,
        volume: float = 10.0) -> Candle:
    c = close if close is not None else (high + low) / 2
    return Candle(open_time=i, open=c, high=high, low=low, close=c,
                   volume=volume, close_time=i)


def _mk_ohlc(i: int, open_: float, high: float, low: float, close: float,
             volume: float = 10.0) -> Candle:
    return Candle(open_time=i, open=open_, high=high, low=low, close=close,
                  volume=volume, close_time=i)


def _flat_candles(n: int, price: float = 100.0, spread: float = 1.0,
                   volume: float = 10.0, start_idx: int = 0) -> list[Candle]:
    """Neredeyse yatay (sabit) mum dizisi — Donchian/BB tabanı için."""
    return [_mk(start_idx + i, price + spread, price - spread, price, volume)
            for i in range(n)]


def _leg(start_idx: int, start_price: float, end_price: float, steps: int,
         volume: float = 10.0, wick: float = 0.1) -> list[Candle]:
    """start_price -> end_price arası doğrusal enterpolasyonlu `steps` mum.
    Yükselen bacakta kapanış tepeye yakın (boğa), düşen bacakta dibe yakın
    (ayı) — gerçekçi mum gövdesi/fitil oranı için."""
    candles = []
    prev = start_price
    for offset in range(1, steps + 1):
        p = start_price + (end_price - start_price) * (offset / steps)
        o, c = prev, p
        if c >= o:
            h, l = c + wick, o - wick * 0.6
        else:
            h, l = o + wick * 0.6, c - wick
        idx = start_idx + offset - 1
        candles.append(_mk_ohlc(idx, o, h, l, c, volume))
        prev = p
    return candles


def _ctx(candles_5m, regime=Regime.UP, candles_15m=None, current_price=None,
         atr_period=14, leverage=20) -> StrategyContext:
    a5 = atr(candles_5m, atr_period)
    return StrategyContext(
        symbol="TESTUSDT",
        regime=regime,
        candles_4h=[],
        candles_15m=candles_15m if candles_15m is not None else [],
        candles_5m=candles_5m,
        current_price=current_price if current_price is not None else candles_5m[-1].close,
        atr_5m=a5 if a5 > 0 else 1.0,
        leverage=leverage,
    )


# --------------------------------------------------------------------------
# detect_regime
# --------------------------------------------------------------------------

class TestDetectRegime:
    def _rising(self, n=250, start=100.0, step=1.0):
        return [_mk(i, start + i * step + 0.5, start + i * step - 0.5, start + i * step)
                for i in range(n)]

    def _falling(self, n=250, start=400.0, step=1.0):
        return [_mk(i, start - i * step + 0.5, start - i * step - 0.5, start - i * step)
                for i in range(n)]

    def _flat(self, n=250, price=100.0):
        return [_mk(i, price + 0.3 * math.sin(i) + 0.5, price + 0.3 * math.sin(i) - 0.5,
                     price + 0.3 * math.sin(i)) for i in range(n)]

    def test_rising_series_is_up(self):
        assert detect_regime(self._rising()) == Regime.UP

    def test_falling_series_is_down(self):
        assert detect_regime(self._falling()) == Regime.DOWN

    def test_flat_series_is_range(self):
        assert detect_regime(self._flat()) == Regime.RANGE

    def test_short_series_is_unknown(self):
        assert detect_regime(self._rising(n=30)) == Regime.UNKNOWN

    def test_empty_series_is_unknown(self):
        assert detect_regime([]) == Regime.UNKNOWN


# --------------------------------------------------------------------------
# StrategyA — trend kırılması
# --------------------------------------------------------------------------

class TestStrategyA:
    def _breakout_candles(self, breakout_volume: float = 30.0) -> list[Candle]:
        candles = _flat_candles(24, price=100.0, spread=1.0, volume=10.0)
        candles.append(_mk_ohlc(24, 101.5, 106.0, 101.0, 105.0, volume=breakout_volume))
        return candles

    def test_breakout_with_volume_gives_long_signal(self):
        candles = self._breakout_candles(breakout_volume=30.0)
        ctx = _ctx(candles, regime=Regime.UP)
        sig = StrategyA().evaluate(ctx)
        assert sig is not None
        assert sig.strategy == "A"
        assert sig.direction == Direction.LONG
        assert sig.stop_price < sig.entry_price

    def test_breakout_without_volume_confirmation_returns_none(self):
        # hacim oranı yalnızca ~1.1x (eşik 1.5x) -> onay yok
        candles = self._breakout_candles(breakout_volume=11.0)
        ctx = _ctx(candles, regime=Regime.UP)
        assert StrategyA().evaluate(ctx) is None

    def test_range_regime_returns_none(self):
        candles = self._breakout_candles()
        ctx = _ctx(candles, regime=Regime.RANGE)
        assert StrategyA().evaluate(ctx) is None


# --------------------------------------------------------------------------
# StrategyB — trend içi uç avcısı
# --------------------------------------------------------------------------

class TestStrategyB:
    def _cooled_15m(self) -> list[Candle]:
        candles = _leg(0, 130.0, 100.0, steps=14)
        candles += _leg(14, 100.0, 101.0, steps=3)
        return candles

    def _dip_reversal_5m(self) -> list[Candle]:
        # ılımlı düşüş (100->98) + keskin tek mum (98->60, BB alt bandını
        # aşırı zorlayan "kapitülasyon" mumu) + 3 ufak toparlanma mumu.
        candles = _leg(0, 100.0, 98.0, steps=20)
        idx = 20
        candles.append(_mk_ohlc(idx, 98.0, 98.1, 59.7, 60.0, volume=10.0))
        idx += 1
        candles += _leg(idx, 60.0, 60.15, steps=3)
        return candles

    def test_up_regime_dip_gives_long_signal(self):
        candles = self._dip_reversal_5m()
        ctx = _ctx(candles, regime=Regime.UP, candles_15m=self._cooled_15m())
        sig = StrategyB().evaluate(ctx)
        assert sig is not None
        assert sig.strategy == "B"
        assert sig.direction == Direction.LONG
        assert sig.stop_price < sig.entry_price

    def test_high_rsi_no_dip_returns_none(self):
        # düz yükseliş -- RSI aşırı yüksek, hiçbir geri çekilme yok
        candles = _leg(0, 100.0, 150.0, steps=25)
        ctx = _ctx(candles, regime=Regime.UP, candles_15m=self._cooled_15m())
        assert StrategyB().evaluate(ctx) is None

    def test_range_regime_returns_none(self):
        candles = self._dip_reversal_5m()
        ctx = _ctx(candles, regime=Regime.RANGE, candles_15m=self._cooled_15m())
        assert StrategyB().evaluate(ctx) is None


# --------------------------------------------------------------------------
# StrategyC — saf uç avcısı (trend filtresiz)
# --------------------------------------------------------------------------

class TestStrategyC:
    def _divergence_candles(self) -> list[Candle]:
        # yatay dolgu (24) + dip1 (100->90, sürdürülmüş düşüş -> RSI çok
        # düşer) + toparlanma teyidi (2) + sıçrama (90->98) + keskin tek
        # mumla dip2 (98->55, dip1'den daha düşük fiyat AMA daha kısa/ani
        # düşüş olduğu için RSI dip1'den daha yüksek kalır -> boğa
        # diverjansı) + 2 ufak toparlanma mumu (dip2'yi swing-low olarak
        # teyit eder, kapanış hâlâ alt bandın altında kalır).
        candles = _flat_candles(24, price=100.0, spread=0.05, volume=10.0)
        idx = 24
        candles += _leg(idx, 100.0, 90.0, steps=8); idx += 8
        candles += _leg(idx, 90.0, 91.0, steps=2); idx += 2
        candles += _leg(idx, 91.0, 98.0, steps=5); idx += 5
        candles.append(_mk_ohlc(idx, 98.0, 98.2, 54.7, 55.0, volume=10.0)); idx += 1
        candles += _leg(idx, 55.0, 55.01, steps=2); idx += 2
        return candles

    def test_dip_with_divergence_gives_long_with_half_risk(self):
        candles = self._divergence_candles()
        ctx = _ctx(candles, regime=Regime.UP)
        sig = StrategyC().evaluate(ctx)
        assert sig is not None
        assert sig.strategy == "C"
        assert sig.direction == Direction.LONG
        assert sig.risk_multiplier == pytest.approx(0.5)
        assert sig.stop_price < sig.entry_price

    def test_works_regardless_of_trend_regime(self):
        # C trend filtresizdir: DOWN rejimde de aynı kurulum sinyal üretir
        candles = self._divergence_candles()
        ctx = _ctx(candles, regime=Regime.DOWN)
        sig = StrategyC().evaluate(ctx)
        assert sig is not None
        assert sig.direction == Direction.LONG

    def test_unknown_regime_returns_none(self):
        candles = self._divergence_candles()
        ctx = _ctx(candles, regime=Regime.UNKNOWN)
        assert StrategyC().evaluate(ctx) is None


# --------------------------------------------------------------------------
# StrategyD — Smart Money (likidite süpürmesi + CHoCH + para akışı)
# --------------------------------------------------------------------------

class TestStrategyD:
    def _bear_structure_leg(self, start_idx: int) -> list[Candle]:
        """Düşen tepe/düşen dip (bear) yapısı: 5 bacak (100->80->95->70->90->65),
        ardından son swing-low'u teyit eden 3 ufak toparlanma mumu."""
        candles = []
        idx = start_idx
        pivots = [100.0, 80.0, 95.0, 70.0, 90.0, 65.0]
        for a, b in zip(pivots[:-1], pivots[1:]):
            leg = _leg(idx, a, b, steps=6)
            candles += leg
            idx += 6
        confirm = _leg(idx, 65.0, 74.0, steps=3)
        candles += confirm
        idx += 3
        return candles

    def _long_setup_candles(self) -> list[Candle]:
        candles = self._bear_structure_leg(0)
        idx = len(candles)
        # süpürme + CHoCH mumu: son swing-low'un (~64.7) altına sarkar
        # (low=60) ama son swing-high'ın (~90.3) ÜZERİNE kapanır (close=95)
        # -- büyük hacim CMF'yi negatiften pozitife çevirir.
        candles.append(_mk_ohlc(idx, 76.0, 96.0, 60.0, 95.0, volume=200.0))
        return candles

    def _bull_structure_leg(self, start_idx: int) -> list[Candle]:
        """Yükselen tepe/yükselen dip (bull) yapısı — LONG kurulumunun
        aynası."""
        candles = []
        idx = start_idx
        pivots = [100.0, 120.0, 105.0, 130.0, 110.0, 135.0]
        for a, b in zip(pivots[:-1], pivots[1:]):
            leg = _leg(idx, a, b, steps=6)
            candles += leg
            idx += 6
        confirm = _leg(idx, 135.0, 126.0, steps=3)
        candles += confirm
        idx += 3
        return candles

    def _short_setup_candles(self) -> list[Candle]:
        candles = self._bull_structure_leg(0)
        idx = len(candles)
        # süpürme + CHoCH mumu: son swing-high'ın üzerine sarkar (high=140)
        # ama son swing-low'un ALTINA kapanır (close=105).
        candles.append(_mk_ohlc(idx, 124.0, 140.0, 104.0, 105.0, volume=200.0))
        return candles

    def test_long_setup_sweep_choch_low_flow_gives_long(self):
        candles = self._long_setup_candles()
        ctx = _ctx(candles, regime=Regime.RANGE)
        sig = StrategyD().evaluate(ctx)
        assert sig is not None
        assert sig.strategy == "D"
        assert sig.direction == Direction.LONG
        assert sig.risk_multiplier == pytest.approx(1.0)
        assert sig.stop_price < sig.entry_price
        assert "CHoCH" in sig.reason

    def test_long_setup_blocked_in_down_regime(self):
        candles = self._long_setup_candles()
        ctx = _ctx(candles, regime=Regime.DOWN)
        assert StrategyD().evaluate(ctx) is None

    def test_short_setup_sweep_choch_high_flow_gives_short(self):
        candles = self._short_setup_candles()
        ctx = _ctx(candles, regime=Regime.RANGE)
        sig = StrategyD().evaluate(ctx)
        assert sig is not None
        assert sig.strategy == "D"
        assert sig.direction == Direction.SHORT
        assert sig.stop_price > sig.entry_price

    def test_short_setup_blocked_in_up_regime(self):
        candles = self._short_setup_candles()
        ctx = _ctx(candles, regime=Regime.UP)
        assert StrategyD().evaluate(ctx) is None

    def test_unknown_regime_returns_none(self):
        candles = self._long_setup_candles()
        ctx = _ctx(candles, regime=Regime.UNKNOWN)
        assert StrategyD().evaluate(ctx) is None

    def test_no_sweep_returns_none(self):
        # aynı yapı ama son mum sıradan (süpürme yok) -> None
        candles = self._bear_structure_leg(0)
        candles += _leg(len(candles), 74.0, 75.0, steps=1)
        ctx = _ctx(candles, regime=Regime.RANGE)
        assert StrategyD().evaluate(ctx) is None


# --------------------------------------------------------------------------
# get_enabled
# --------------------------------------------------------------------------

class TestGetEnabled:
    def test_all_four_enabled(self):
        strategies = get_enabled("A,B,C,D")
        assert [s.name for s in strategies] == ["A", "B", "C", "D"]
        assert len(strategies) == 4

    def test_only_d_enabled(self):
        strategies = get_enabled("D")
        assert len(strategies) == 1
        assert strategies[0].name == "D"

    def test_all_strategies_contains_d(self):
        assert [s.name for s in ALL_STRATEGIES] == ["A", "B", "C", "D"]


# --------------------------------------------------------------------------
# Tüm stratejilerde: bozuk stop (stop giriş fiyatının yanlış tarafında) -> None
#
# current_price, adayları etkilemeyen bağımsız bir StrategyContext alanıdır
# (koşullar mum kapanışlarına dayanır); bilinçli olarak stop'un YANLIŞ
# tarafına taşınarak her stratejinin son güvenlik kapısı doğrulanır.
# --------------------------------------------------------------------------

class TestBrokenStopReturnsNone:
    def test_strategy_a_broken_stop(self):
        candles = TestStrategyA()._breakout_candles(breakout_volume=30.0)
        ctx = _ctx(candles, regime=Regime.UP, current_price=50.0)
        assert StrategyA().evaluate(ctx) is None

    def test_strategy_b_broken_stop(self):
        candles = TestStrategyB()._dip_reversal_5m()
        ctx = _ctx(candles, regime=Regime.UP, candles_15m=TestStrategyB()._cooled_15m(),
                    current_price=50.0)
        assert StrategyB().evaluate(ctx) is None

    def test_strategy_c_broken_stop(self):
        candles = TestStrategyC()._divergence_candles()
        ctx = _ctx(candles, regime=Regime.UP, current_price=50.0)
        assert StrategyC().evaluate(ctx) is None

    def test_strategy_d_long_broken_stop(self):
        candles = TestStrategyD()._long_setup_candles()
        ctx = _ctx(candles, regime=Regime.RANGE, current_price=10.0)
        assert StrategyD().evaluate(ctx) is None


# --------------------------------------------------------------------------
# passes_equilibrium — ortak denge (equilibrium) filtre kapısı
#
# Denge noktası ctx.candles_15m'den hesaplanır (bkz. passes_equilibrium
# docstring'i): B'nin zaten kullandığı "5m tetikler, 15m bağlam onaylar"
# örüntüsüyle tutarlı ve D'nin sert dönüş mumuyla tetiklenen kendi 5m
# aralığını kendi kuyruğunu kovalar hale getirmesini önler.
# --------------------------------------------------------------------------

class TestPassesEquilibrium:
    def _eq_candles_15m(self) -> list[Candle]:
        # left=3,right=3 varsayılanıyla: idx5 swing-high(20), idx9 swing-low(1)
        # -> eq = (20+1)/2 = 10.5 (gerçek equilibrium() çıktısıyla doğrulanmıştır)
        candles = []
        for i in range(15):
            high = 20.0 if i == 5 else 10.0
            low = 1.0 if i == 9 else 5.0
            candles.append(_mk(i, high, low))
        return candles

    def _ctx_with_eq(self, current_price: float, candles_15m=None) -> StrategyContext:
        candles_5m = _flat_candles(30, price=100.0, spread=1.0)
        c15 = candles_15m if candles_15m is not None else self._eq_candles_15m()
        return _ctx(candles_5m, candles_15m=c15, current_price=current_price)

    def test_disabled_always_true_long_and_short(self):
        ctx = self._ctx_with_eq(current_price=13.0)  # eq(10.5) üstü
        assert passes_equilibrium(ctx, Direction.LONG, enabled=False) is True
        assert passes_equilibrium(ctx, Direction.SHORT, enabled=False) is True

    def test_eq_none_when_no_swings_always_true(self):
        flat_15m = _flat_candles(15, price=100.0, spread=1.0)
        ctx = self._ctx_with_eq(current_price=100.0, candles_15m=flat_15m)
        assert passes_equilibrium(ctx, Direction.LONG, enabled=True) is True
        assert passes_equilibrium(ctx, Direction.SHORT, enabled=True) is True

    def test_long_below_eq_discount_passes(self):
        ctx = self._ctx_with_eq(current_price=8.0)  # eq(10.5) altı
        assert passes_equilibrium(ctx, Direction.LONG, enabled=True) is True

    def test_long_above_eq_premium_blocked(self):
        ctx = self._ctx_with_eq(current_price=13.0)  # eq(10.5) üstü
        assert passes_equilibrium(ctx, Direction.LONG, enabled=True) is False

    def test_short_above_eq_premium_passes(self):
        ctx = self._ctx_with_eq(current_price=13.0)  # eq(10.5) üstü
        assert passes_equilibrium(ctx, Direction.SHORT, enabled=True) is True

    def test_short_below_eq_discount_blocked(self):
        ctx = self._ctx_with_eq(current_price=8.0)  # eq(10.5) altı
        assert passes_equilibrium(ctx, Direction.SHORT, enabled=True) is False


# --------------------------------------------------------------------------
# Stratejilerde equilibrium filtre entegrasyonu — settings monkeypatch
#
# setups.py `from src.core.config import settings` ile TEKİL nesneyi
# modül seviyesinde import eder; monkeypatch.setattr bu nesnenin
# özniteliğini değiştirdiği için strateji evaluate() çağrılarına da yansır.
# --------------------------------------------------------------------------

class TestStrategyFilterIntegration:
    def _premium_candles_15m(self) -> list[Candle]:
        # eq=10.5 (bkz. TestPassesEquilibrium). StrategyC'nin ~55-100
        # aralığındaki fiyatları bu eq'in HER ZAMAN üzerinde kalır -> her
        # LONG senaryosu 'premium' bölgede sayılır.
        candles = []
        for i in range(15):
            high = 20.0 if i == 5 else 10.0
            low = 1.0 if i == 9 else 5.0
            candles.append(_mk(i, high, low))
        return candles

    def test_enabled_blocks_long_in_premium_zone(self, monkeypatch):
        monkeypatch.setattr(setups_module.settings, "scalper_use_equilibrium_filter", True)
        candles = TestStrategyC()._divergence_candles()
        ctx = _ctx(candles, regime=Regime.UP, candles_15m=self._premium_candles_15m())
        assert StrategyC().evaluate(ctx) is None

    def test_disabled_allows_signal_through_premium_zone(self, monkeypatch):
        monkeypatch.setattr(setups_module.settings, "scalper_use_equilibrium_filter", False)
        candles = TestStrategyC()._divergence_candles()
        ctx = _ctx(candles, regime=Regime.UP, candles_15m=self._premium_candles_15m())
        sig = StrategyC().evaluate(ctx)
        assert sig is not None
        assert sig.strategy == "C"
        assert sig.direction == Direction.LONG


# --------------------------------------------------------------------------
# ScalpExecutor.try_open — R:R (getiri/risk) kapısı
#
# AĞ YOK: client/pm/tracker tamamen sahte (fake) async nesnelerdir. Çağrı
# sırası kaydedilir ki R:R kapısının balance sorgusundan SONRA ama sonraki
# hiçbir ağ çağrısından (quantize_quantity dahil) ÖNCE reddettiği kanıtlanabilsin
# (mevcut akış: 1-bakiye, 2-stop mesafesi, 3-R:R, 4-boyutlama...).
# --------------------------------------------------------------------------

@dataclass
class _ExecCfg:
    """cfg sözleşmesinin test için minimal, ağdan/settings'ten bağımsız kopyası."""
    scalper_min_stop_pct: float = 0.15
    scalper_max_stop_pct: float = 3.0
    scalper_min_rr: float = 1.2
    scalper_risk_percentage: float = 2.0
    scalper_leverage: int = 20
    scalper_tp1_roi: float = 20.0
    scalper_tp1_fraction: float = 0.40
    scalper_tp2_roi: float = 50.0
    scalper_tp2_fraction: float = 0.30
    scalper_breakeven_buffer_pct: float = 0.05
    scalper_chandelier_atr_mult: float = 2.5


class _FakeClient:
    """ImprovedBinanceClient'ın try_open akışının ihtiyaç duyduğu metodlarını
    taklit eden sahte istemci — GERÇEK AĞ ÇAĞRISI YAPMAZ. Çağrı sırası
    self.calls'a kaydedilir."""

    def __init__(self, balance: float = 10_000.0):
        self.balance = balance
        self.calls: list[str] = []

    async def get_account_balance(self):
        self.calls.append("get_account_balance")
        return self.balance

    async def quantize_quantity(self, symbol, quantity):
        self.calls.append("quantize_quantity")
        return quantity

    async def validate_order(self, symbol, quantity, price):
        self.calls.append("validate_order")

    async def set_margin_type(self, symbol, margin_type="ISOLATED"):
        self.calls.append("set_margin_type")

    async def set_leverage(self, symbol, leverage):
        self.calls.append("set_leverage")

    async def open_market_order(self, symbol, side, quantity):
        self.calls.append("open_market_order")
        return {"orderId": 111}

    async def place_take_profit(self, symbol, side, stop_price, quantity):
        self.calls.append("place_take_profit")
        return {"orderId": 222}


class _FakePm:
    """PositionManager'ın try_open akışının ihtiyaç duyduğu metodlarını
    taklit eder — GERÇEK AĞ ÇAĞRISI YAPMAZ."""

    def __init__(self, entry_price: float, filled_qty: float):
        self.entry_price = entry_price
        self.filled_qty = filled_qty
        self.calls: list[str] = []

    async def resolve_fill(self, symbol, entry_order):
        self.calls.append("resolve_fill")
        return self.entry_price, self.filled_qty

    async def place_stop_loss_or_close(self, symbol, sl_side, stop_price):
        self.calls.append("place_stop_loss_or_close")
        return {"orderId": 333}


class _FakeTracker:
    def __init__(self):
        self.calls: list[str] = []

    async def record_open(self, **kwargs):
        self.calls.append("record_open")
        return 1


def _mk_exec_signal(entry_price: float, stop_price: float,
                     direction: Direction = Direction.LONG) -> ScalpSignal:
    return ScalpSignal(
        strategy="B", symbol="TESTUSDT", direction=direction,
        entry_price=entry_price, stop_price=stop_price, reason="test",
        regime=Regime.UP, atr_5m=1.0, risk_multiplier=1.0,
    )


def _mk_exec_ctx() -> StrategyContext:
    return StrategyContext(
        symbol="TESTUSDT", regime=Regime.UP, candles_4h=[], candles_15m=[],
        candles_5m=[], current_price=100.0, atr_5m=1.0, leverage=20,
    )


class TestExecutorRiskRewardGate:
    # cfg varsayılanlarıyla beklenen harman getiri (ROI%):
    # 20*0.4 + 50*0.3 + 20*0.3 = 8 + 15 + 6 = 29.0

    async def test_narrow_stop_passes_rr_gate_and_opens_position(self):
        cfg = _ExecCfg()
        client = _FakeClient(balance=10_000.0)
        pm = _FakePm(entry_price=100.0, filled_qty=1.0)
        tracker = _FakeTracker()
        executor = ScalpExecutor(client=client, pm=pm, tracker=tracker, cfg=cfg)

        entry = 100.0
        stop = entry * (1 - 0.005)  # %0.5 stop mesafesi -> rr=29/(0.5*20=10)=2.9 >= 1.2
        signal = _mk_exec_signal(entry, stop)
        ctx = _mk_exec_ctx()

        result = await executor.try_open(signal, ctx)

        assert result is not None
        assert isinstance(result, ScalpPosition)
        # RR kapısından geçip ağa çıktığının kanıtı: quantize_quantity çağrıldı
        assert "quantize_quantity" in client.calls

    async def test_wide_stop_blocked_by_rr_gate_before_further_network_calls(self):
        cfg = _ExecCfg()
        client = _FakeClient(balance=10_000.0)
        pm = _FakePm(entry_price=100.0, filled_qty=1.0)
        tracker = _FakeTracker()
        executor = ScalpExecutor(client=client, pm=pm, tracker=tracker, cfg=cfg)

        entry = 100.0
        # %2.5 stop mesafesi (max_stop_pct=3.0 içinde -> stop kapısını geçer)
        # rr = 29 / (2.5*20=50) = 0.58 < 1.2 -> R:R kapısı reddetmeli
        stop = entry * (1 - 0.025)
        signal = _mk_exec_signal(entry, stop)
        ctx = _mk_exec_ctx()

        result = await executor.try_open(signal, ctx)

        assert result is None
        # Mevcut akışa göre bakiye sorgusu R:R kapısından ÖNCE yapılır (1-2-3
        # sırası); ama R:R kapısı reddettiği için SONRAKİ hiçbir ağ çağrısı
        # (quantize_quantity dahil) yapılmamalı.
        assert client.calls == ["get_account_balance"]
        assert pm.calls == []
        assert tracker.calls == []
