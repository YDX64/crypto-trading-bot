"""Stop politikası (fixed_roi modu) + C giriş filtreleri (2026-08-12).

Kullanıcı isteği: "SL çok hızlı vuruyor — marjın %50'sine esnet" ve
"para akışı / dönüş bölgesi tahmin eden indikatörler ekle".

  1. apply_stop_policy: "structural" mevcut ATR-taban davranışını korur;
     "fixed_roi" stop mesafesini fixed_stop_roi_pct/kaldıraç fiyat yüzdesine
     sabitler (10x + %50 → %5). Boyutlama risk tabanlı olduğu için işlem
     başına USD riski değişmez.
  2. passes_flow_confirm: MFI para akışı aşırı uçtan dönmeden giriş yok.
  3. passes_reversal_zone: order block / EQL-EQH kümesine yaslanmayan
     ("boşlukta") dip/tepe avı engellenir.
"""

from types import SimpleNamespace
from typing import List

import pytest

from src.strategies.scalper.backtest import simulate_symbol
from src.strategies.scalper.setups import (
    apply_stop_policy,
    passes_flow_confirm,
    passes_reversal_zone,
)
from src.strategies.scalper.types import (
    Candle,
    Direction,
    Regime,
    ScalpSignal,
    StrategyContext,
    StrategyProtocol,
)

_INTERVAL_5M = 5 * 60 * 1000


def _mk_candle(i: int, open_: float, high: float, low: float, close: float,
               volume: float = 100.0) -> Candle:
    open_time = i * _INTERVAL_5M
    return Candle(
        open_time=open_time, open=open_, high=high, low=low, close=close,
        volume=volume, close_time=open_time + _INTERVAL_5M - 1,
    )


def _mk_signal(entry_price: float, stop_price: float,
               direction: Direction = Direction.LONG,
               atr_5m: float = 1.0) -> ScalpSignal:
    return ScalpSignal(
        strategy="C", symbol="TESTUSDT", direction=direction,
        entry_price=entry_price, stop_price=stop_price, reason="test",
        regime=Regime.RANGE, atr_5m=atr_5m,
    )


def _mk_ctx(candles_5m: List[Candle], current_price: float,
            atr_5m: float = 1.0) -> StrategyContext:
    return StrategyContext(
        symbol="TESTUSDT", regime=Regime.RANGE,
        candles_4h=[], candles_15m=[], candles_5m=candles_5m,
        current_price=current_price, atr_5m=atr_5m, leverage=10,
    )


# --------------------------------------------------------------------------
# 1) apply_stop_policy
# --------------------------------------------------------------------------

class TestApplyStopPolicy:
    def test_fixed_roi_long_sets_margin_pct_stop(self):
        # 10x + %50 ROI stop → %5 fiyat mesafesi: SL vurunca marjın %50'si gider.
        cfg = SimpleNamespace(
            scalper_stop_mode="fixed_roi",
            scalper_fixed_stop_roi_pct=50.0,
            scalper_leverage=10,
        )
        sig = _mk_signal(entry_price=100.0, stop_price=99.9)
        out = apply_stop_policy(sig, cfg)
        assert out.stop_price == pytest.approx(95.0)

    def test_fixed_roi_short_mirror(self):
        cfg = SimpleNamespace(
            scalper_stop_mode="fixed_roi",
            scalper_fixed_stop_roi_pct=50.0,
            scalper_leverage=10,
        )
        sig = _mk_signal(entry_price=100.0, stop_price=100.1,
                         direction=Direction.SHORT)
        assert apply_stop_policy(sig, cfg).stop_price == pytest.approx(105.0)

    def test_fixed_roi_scales_with_leverage_and_pct(self):
        cfg = SimpleNamespace(
            scalper_stop_mode="fixed_roi",
            scalper_fixed_stop_roi_pct=25.0,
            scalper_leverage=20,
        )
        sig = _mk_signal(entry_price=100.0, stop_price=99.9)
        # 25/20 = %1.25 fiyat mesafesi
        assert apply_stop_policy(sig, cfg).stop_price == pytest.approx(98.75)

    def test_fixed_roi_invalid_params_leave_signal_unchanged(self):
        for roi, lev in ((0.0, 10), (50.0, 0), (-5.0, 10)):
            cfg = SimpleNamespace(
                scalper_stop_mode="fixed_roi",
                scalper_fixed_stop_roi_pct=roi,
                scalper_leverage=lev,
            )
            sig = _mk_signal(entry_price=100.0, stop_price=99.9)
            assert apply_stop_policy(sig, cfg) is sig

    def test_structural_mode_delegates_to_atr_floor(self):
        cfg = SimpleNamespace(
            scalper_stop_mode="structural",
            scalper_stop_atr_floor_mult=1.0,
            scalper_fixed_stop_roi_pct=50.0,
            scalper_leverage=10,
        )
        sig = _mk_signal(entry_price=100.0, stop_price=99.8, atr_5m=2.0)
        assert apply_stop_policy(sig, cfg).stop_price == pytest.approx(98.0)

    def test_missing_mode_defaults_to_structural(self):
        cfg = SimpleNamespace(scalper_stop_atr_floor_mult=0.0)
        sig = _mk_signal(entry_price=100.0, stop_price=99.8)
        assert apply_stop_policy(sig, cfg) is sig

    def test_roi_pct_clamped_at_liquidation_guard_cap(self):
        # %90 istenirse %70'e kırpılır: SL likidasyonun (~-%95 ROI) berisinde kalmalı.
        cfg = SimpleNamespace(
            scalper_stop_mode="fixed_roi",
            scalper_fixed_stop_roi_pct=90.0,
            scalper_leverage=10,
        )
        sig = _mk_signal(entry_price=100.0, stop_price=99.9)
        assert apply_stop_policy(sig, cfg).stop_price == pytest.approx(93.0)


class TestDynamicLeverage:
    """Coin-bazlı dinamik kaldıraç: lev = stop_roi / (mult × ATR%), [lo, hi].

    SL her durumda marjın stop_roi'si kalır; stop FİYAT mesafesi = ATR × mult
    olur (clamp'e takılmadıkça) — volatil coin düşük kaldıraç alır.
    """

    @staticmethod
    def _cfg(**over):
        base = dict(
            scalper_stop_mode="fixed_roi",
            scalper_fixed_stop_roi_pct=50.0,
            scalper_leverage=10,
            scalper_dynamic_leverage=True,
            scalper_dyn_lev_stop_atr_mult=3.0,
            scalper_dyn_lev_min=3,
            scalper_dyn_lev_max=20,
        )
        base.update(over)
        return SimpleNamespace(**base)

    def test_calm_coin_clamped_to_max_leverage(self):
        # ATR% = 0.05 → raw = 50/(3×0.05) = 333 → clamp 20.
        sig = _mk_signal(entry_price=100.0, stop_price=99.9, atr_5m=0.05)
        out = apply_stop_policy(sig, self._cfg())
        assert out.leverage == 20
        assert out.stop_price == pytest.approx(100.0 - 100.0 * 0.50 / 20)  # %2.5

    def test_wild_coin_clamped_to_min_leverage(self):
        # ATR% = 8 → raw = 50/24 ≈ 2.08 → clamp 3.
        sig = _mk_signal(entry_price=100.0, stop_price=99.0, atr_5m=8.0)
        out = apply_stop_policy(sig, self._cfg())
        assert out.leverage == 3
        assert out.stop_price == pytest.approx(100.0 - 100.0 * 0.50 / 3)

    def test_mid_vol_stop_distance_tracks_atr(self):
        # ATR% = 1.0 → raw = 50/3 ≈ 16.67 → 17; mesafe = 50/17 ≈ %2.94 ≈ 3×ATR.
        sig = _mk_signal(entry_price=100.0, stop_price=99.9, atr_5m=1.0)
        out = apply_stop_policy(sig, self._cfg())
        assert out.leverage == 17
        assert out.stop_price == pytest.approx(100.0 * (1 - 0.50 / 17))

    def test_disabled_keeps_global_leverage_and_none_field(self):
        sig = _mk_signal(entry_price=100.0, stop_price=99.9, atr_5m=1.0)
        out = apply_stop_policy(sig, self._cfg(scalper_dynamic_leverage=False))
        assert out.leverage is None
        assert out.stop_price == pytest.approx(95.0)  # 50/10 = %5

    def test_zero_atr_falls_back_to_global(self):
        sig = _mk_signal(entry_price=100.0, stop_price=99.9, atr_5m=0.0)
        out = apply_stop_policy(sig, self._cfg())
        assert out.leverage is None
        assert out.stop_price == pytest.approx(95.0)


class TestFixedRoiConfigValidator:
    """fixed_roi ↔ min_rr/max_stop tutarlılığı startup'ta fail-fast olmalı."""

    @staticmethod
    def _settings(**overrides):
        from src.core.config import Settings

        values = dict(
            # Zorunlu alanlar — validator testinde içerikleri önemsiz.
            binance_api_key="x", binance_api_secret="x",
            telegram_bot_token="x", telegram_chat_id="x",
            openai_api_key="x", gemini_api_key="x", deepseek_api_key="x",
            jwt_secret="x",
            scalper_stop_mode="fixed_roi",
            scalper_fixed_stop_roi_pct=50.0,
            scalper_leverage=10,
            scalper_min_rr=0.0,
            scalper_max_stop_pct=5.5,
        )
        values.update(overrides)
        return Settings(_env_file=None, **values)

    def test_consistent_profile_accepted(self):
        s = self._settings()
        assert s.scalper_stop_mode == "fixed_roi"

    def test_distance_over_max_stop_rejected(self):
        with pytest.raises(ValueError, match="MAX_STOP"):
            self._settings(scalper_max_stop_pct=3.0)  # 50/10=%5 > 3

    def test_min_rr_conflict_rejected(self):
        # rr = 29/50 = 0.58 < 1.2 → her sinyal reddedilirdi; startup'ta patlamalı.
        with pytest.raises(ValueError, match="MIN_RR"):
            self._settings(scalper_min_rr=1.2)

    def test_structural_mode_untouched_by_validator(self):
        s = self._settings(
            scalper_stop_mode="structural",
            scalper_min_rr=1.2,
            scalper_max_stop_pct=3.0,
        )
        assert s.scalper_min_rr == 1.2


# --------------------------------------------------------------------------
# 2) passes_flow_confirm — MFI dönüş teyidi
# --------------------------------------------------------------------------

def _falling_then(last_close_delta: float, n: int = 30) -> List[Candle]:
    """n-1 mum düşen typical price (MFI → aşırı satım), son mum delta kadar."""
    candles: List[Candle] = []
    price = 200.0
    for i in range(n - 1):
        price -= 1.0
        candles.append(_mk_candle(i, price + 0.5, price + 0.6, price - 0.1, price))
    last = price + last_close_delta
    candles.append(_mk_candle(n - 1, price, max(price, last) + 0.1,
                              min(price, last) - 0.1, last))
    return candles


class TestFlowConfirm:
    def test_disabled_always_passes(self):
        ctx = _mk_ctx(_falling_then(-1.0), current_price=170.0)
        assert passes_flow_confirm(ctx, Direction.LONG, False)

    def test_long_blocked_while_flow_still_falling(self):
        # Akış hâlâ satış yönünde (son mum da düşüş) — düşen bıçak, girme.
        ctx = _mk_ctx(_falling_then(-1.0), current_price=170.0)
        assert not passes_flow_confirm(ctx, Direction.LONG, True)

    def test_long_allowed_when_flow_turns_up_from_oversold(self):
        # MFI dipte VE son mumda yukarı dönüş var.
        ctx = _mk_ctx(_falling_then(+2.0), current_price=174.0)
        assert passes_flow_confirm(ctx, Direction.LONG, True)

    def test_short_mirror(self):
        rising = []
        price = 100.0
        for i in range(29):
            price += 1.0
            rising.append(_mk_candle(i, price - 0.5, price + 0.1, price - 0.6, price))
        still_rising = rising + [_mk_candle(29, price, price + 1.2, price - 0.1, price + 1.0)]
        turned_down = rising + [_mk_candle(29, price, price + 0.1, price - 2.2, price - 2.0)]
        assert not passes_flow_confirm(
            _mk_ctx(still_rising, price + 1.0), Direction.SHORT, True
        )
        assert passes_flow_confirm(
            _mk_ctx(turned_down, price - 2.0), Direction.SHORT, True
        )

    def test_insufficient_data_fails_closed(self):
        ctx = _mk_ctx(_falling_then(+2.0)[:5], current_price=100.0)
        assert not passes_flow_confirm(ctx, Direction.LONG, True)


# --------------------------------------------------------------------------
# 3) passes_reversal_zone — order block / seviye kümesi teyidi
# --------------------------------------------------------------------------

def _ob_candles(retest_price: float) -> List[Candle]:
    """Bullish order block + geri test senaryosu.

    idx 0-9: düz 100 (pivot üretmeyen dolgu); idx 10: kırmızı OB mumu
    (100→99, low 98.8, high 100.2); idx 11-13: güçlü yukarı impuls (toplam
    gövde 4.5 > 2×1.0, kapanış 103.5 > OB high) → [98.8, 100.2] dönüş bölgesi;
    idx 14-16: küçük gövdeli geri çekilme, son kapanış retest_price.
    """
    candles = [_mk_candle(i, 100.0, 100.05, 99.95, 100.0) for i in range(10)]
    candles.append(_mk_candle(10, 100.0, 100.2, 98.8, 99.0))       # OB (kırmızı)
    candles.append(_mk_candle(11, 99.0, 100.6, 98.9, 100.5))       # impuls 1
    candles.append(_mk_candle(12, 100.5, 102.1, 100.4, 102.0))     # impuls 2
    candles.append(_mk_candle(13, 102.0, 103.6, 101.9, 103.5))     # impuls 3
    candles.append(_mk_candle(14, 103.5, 103.6, retest_price - 0.1, retest_price + 0.2))
    candles.append(_mk_candle(15, retest_price + 0.2, retest_price + 0.3,
                              retest_price - 0.1, retest_price))
    candles.append(_mk_candle(16, retest_price, retest_price + 0.1,
                              retest_price - 0.1, retest_price))
    return candles


class TestReversalZone:
    def test_disabled_always_passes(self):
        ctx = _mk_ctx(_ob_candles(110.0), current_price=110.0)
        assert passes_reversal_zone(ctx, Direction.LONG, False)

    def test_price_at_order_block_passes(self, monkeypatch):
        import src.strategies.scalper.setups as setups_module
        monkeypatch.setattr(setups_module.settings, "scalper_c_zone_atr_tolerance", 0.75)
        ctx = _mk_ctx(_ob_candles(100.0), current_price=100.0, atr_5m=1.0)
        assert passes_reversal_zone(ctx, Direction.LONG, True)

    def test_price_far_from_any_zone_blocked(self, monkeypatch):
        import src.strategies.scalper.setups as setups_module
        monkeypatch.setattr(setups_module.settings, "scalper_c_zone_atr_tolerance", 0.75)
        # Geri test bölgeye inmemiş; fiyat boşlukta.
        ctx = _mk_ctx(_ob_candles(102.5), current_price=102.5, atr_5m=1.0)
        assert not passes_reversal_zone(ctx, Direction.LONG, True)

    def test_zero_atr_fails_closed(self, monkeypatch):
        import src.strategies.scalper.setups as setups_module
        monkeypatch.setattr(setups_module.settings, "scalper_c_zone_atr_tolerance", 0.75)
        ctx = _mk_ctx(_ob_candles(100.0), current_price=100.0, atr_5m=0.0)
        assert not passes_reversal_zone(ctx, Direction.LONG, True)


# --------------------------------------------------------------------------
# 4) Backtest paritesi — simulate_symbol fixed_roi stopu kullanmalı
# --------------------------------------------------------------------------

class _AlwaysLongStrategy(StrategyProtocol):
    name = "X"

    def evaluate(self, ctx: StrategyContext):
        return ScalpSignal(
            strategy="X", symbol=ctx.symbol, direction=Direction.LONG,
            entry_price=ctx.current_price, stop_price=ctx.current_price * 0.99,
            reason="test", regime=Regime.UP, atr_5m=1.0,
        )


def _sim_cfg(**overrides) -> SimpleNamespace:
    values = dict(
        scalper_risk_percentage=2.0,
        scalper_leverage=20,
        scalper_tp1_roi=20.0,
        scalper_tp1_fraction=0.40,
        scalper_tp2_roi=50.0,
        scalper_tp2_fraction=0.30,
        scalper_min_stop_pct=0.15,
        scalper_max_stop_pct=5.0,
        scalper_breakeven_buffer_pct=0.05,
        scalper_chandelier_atr_mult=2.5,
        scalper_chandelier_atr_period=14,
        scalper_min_rr=0.0,
        scalper_entry_mode="taker",
        scalper_taker_fee_pct=0.05,
        scalper_maker_fee_pct=0.02,
        scalper_maker_fill_timeout_candles=3,
        scalper_max_margin_pct=50.0,
        scalper_stop_atr_floor_mult=0.0,
        scalper_loss_cooldown_minutes=0,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


class TestSimulateSymbolFixedRoiStop:
    def test_fixed_roi_stop_survives_dip_that_kills_structural(self):
        # AlwaysLong yapısal stop %1 (99.0). fixed_roi 50 @ 20x → %2.5 (97.5).
        # Mum 98.0'a düşüyor: yapısal SL vurur, fixed_roi vurmadan devam eder.
        candles = [
            _mk_candle(0, 100.0, 100.1, 99.9, 100.0),
            _mk_candle(1, 100.0, 100.2, 98.0, 98.2),
            _mk_candle(2, 98.2, 98.4, 98.0, 98.3),
        ]
        structural = simulate_symbol(
            "TESTUSDT", candles, [], [], [_AlwaysLongStrategy()], _sim_cfg(),
        )
        fixed = simulate_symbol(
            "TESTUSDT", candles, [], [],
            [_AlwaysLongStrategy()],
            _sim_cfg(scalper_stop_mode="fixed_roi", scalper_fixed_stop_roi_pct=50.0),
        )
        assert any(t.exit_reason == "SL" for t in structural)
        assert not any(t.exit_reason == "SL" for t in fixed)

    def test_fixed_roi_sl_costs_half_margin(self):
        # SL gerçekleşirse kayıp ≈ marjın %50'si (komisyon hariç) olmalı.
        candles = [
            _mk_candle(0, 100.0, 100.1, 99.9, 100.0),
            _mk_candle(1, 100.0, 100.1, 96.0, 96.5),  # %2.5 stopu deler
        ]
        trades = simulate_symbol(
            "TESTUSDT", candles, [], [],
            [_AlwaysLongStrategy()],
            _sim_cfg(scalper_stop_mode="fixed_roi", scalper_fixed_stop_roi_pct=50.0),
        )
        assert len(trades) == 1
        t = trades[0]
        assert t.exit_reason == "SL"
        # roi_pct marj bazlıdır: komisyonla birlikte ~-%50 civarı olmalı.
        assert t.roi_pct == pytest.approx(-50.0, abs=3.0)
