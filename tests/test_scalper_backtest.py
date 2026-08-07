"""
src/strategies/scalper/backtest.py için birim testleri — AĞ YOK.

Strateji sinyal tespiti (A/B/C) zaten test_scalper_indicators.py ve
setups.py'nin kendi mantığıyla ilgilidir; burada test edilen backtest
MOTORUNUN kendisidir: zaman hizalama (look-ahead koruması), giriş/boyutlama,
intrabar SL-önce kuralı, TP1→break-even→trailing akışı ve komisyon/kayma
maliyet modeli. Bu yüzden çoğu test gerçek StrategyA/B/C yerine doğrudan
open_position/manage_position'ı (veya basit bir sahte strateji) kullanır —
deterministik ve karmaşık gerçek strateji koşullarına bağımlı değildir.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import pytest

from src.strategies.scalper.backtest import (
    BacktestTrade,
    OpenPosition,
    build_context,
    compute_stats,
    fetch_paginated,
    manage_position,
    open_position,
    simulate_symbol,
)
from src.strategies.scalper.types import (
    Candle,
    Direction,
    Regime,
    ScalpSignal,
    StrategyContext,
    StrategyProtocol,
)


# --------------------------------------------------------------------------
# Yardımcılar
# --------------------------------------------------------------------------

_INTERVAL_5M = 300_000  # ms


def _mk_candle(i: int, open_: float, high: float, low: float, close: float,
               interval_ms: int = _INTERVAL_5M) -> Candle:
    open_time = i * interval_ms
    close_time = open_time + interval_ms - 1
    return Candle(
        open_time=open_time, open=open_, high=high, low=low, close=close,
        volume=100.0, close_time=close_time,
    )


@dataclass
class _Cfg:
    """cfg sözleşmesinin test için minimal, ağdan/settings'ten bağımsız kopyası."""
    scalper_risk_percentage: float = 2.0
    scalper_leverage: int = 20
    scalper_tp1_roi: float = 20.0
    scalper_tp1_fraction: float = 0.40
    scalper_tp2_roi: float = 50.0
    scalper_tp2_fraction: float = 0.30
    scalper_min_stop_pct: float = 0.1
    scalper_max_stop_pct: float = 5.0
    scalper_breakeven_buffer_pct: float = 0.05
    scalper_chandelier_atr_mult: float = 2.5
    scalper_chandelier_atr_period: int = 14


def _mk_signal(entry_price: float, stop_price: float,
               direction: Direction = Direction.LONG,
               strategy: str = "B", symbol: str = "TESTUSDT",
               risk_multiplier: float = 1.0) -> ScalpSignal:
    return ScalpSignal(
        strategy=strategy, symbol=symbol, direction=direction,
        entry_price=entry_price, stop_price=stop_price, reason="test",
        regime=Regime.UP, atr_5m=1.0, risk_multiplier=risk_multiplier,
    )


class _AlwaysLongStrategy(StrategyProtocol):
    """Her turda %1 yapısal stopla LONG sinyali üreten sahte strateji —
    backtest MOTORUNUN (tek eşzamanlı pozisyon, tarama/yönetim döngüsü)
    gerçek A/B/C koşullarından bağımsız test edilmesini sağlar."""

    name = "X"

    def evaluate(self, ctx: StrategyContext) -> Optional[ScalpSignal]:
        return _mk_signal(ctx.current_price, ctx.current_price * 0.99, symbol=ctx.symbol)


# --------------------------------------------------------------------------
# Look-ahead koruması
# --------------------------------------------------------------------------

class TestBuildContextLookAhead:
    def test_no_lookahead_in_15m_4h_5m_slices(self):
        # 50 tane 5m mum (idx0..49); "şu an" = idx49 -> cutoff = idx49.close_time
        candles_5m = [_mk_candle(i, 100 + i * 0.01, 100 + i * 0.01,
                                  100 + i * 0.01, 100 + i * 0.01) for i in range(50)]
        cutoff = candles_5m[49].close_time
        assert cutoff == 49 * _INTERVAL_5M + _INTERVAL_5M - 1

        # 15m: 20 mum, bir kısmı cutoff'un GELECEĞİNDE olacak şekilde kurgulanır
        candles_15m = [_mk_candle(i, 1, 1, 1, 1, interval_ms=900_000) for i in range(20)]
        future_15m = [c for c in candles_15m if c.close_time > cutoff]
        past_15m = [c for c in candles_15m if c.close_time <= cutoff]
        assert future_15m, "test kurgusu hatalı: en az bir 'gelecek' 15m mumu olmalı"
        assert past_15m, "test kurgusu hatalı: en az bir 'geçmiş' 15m mumu olmalı"

        # 4h: 2 mum, biri geçmiş biri gelecek
        candles_4h = [_mk_candle(i, 1, 1, 1, 1, interval_ms=14_400_000) for i in range(2)]
        assert candles_4h[0].close_time <= cutoff
        assert candles_4h[1].close_time > cutoff

        ctx = build_context("BTCUSDT", candles_5m, candles_15m, candles_4h, index=49, leverage=20)

        # Asıl look-ahead koruması doğrulaması: hiçbir dilim cutoff'u AŞMAZ.
        assert all(c.close_time <= cutoff for c in ctx.candles_15m)
        assert all(c.close_time <= cutoff for c in ctx.candles_4h)
        assert all(c.close_time <= cutoff for c in ctx.candles_5m)

        # Gelecekteki mumlar GERÇEKTEN dışlanmış (yalnız <= değil, sayı da doğru)
        assert len(ctx.candles_15m) == len(past_15m)
        assert len(ctx.candles_4h) == 1
        assert len(ctx.candles_5m) == 50
        assert ctx.current_price == candles_5m[49].close

    def test_window_caps_apply_after_cutoff_filter(self):
        # 400 tane 5m mum -> ctx penceresi son 150'ye kırpılmalı
        candles_5m = [_mk_candle(i, 100, 100, 100, 100) for i in range(400)]
        ctx = build_context("BTCUSDT", candles_5m, [], [], index=399, leverage=20)
        assert len(ctx.candles_5m) == 150
        assert ctx.candles_5m[-1].close_time == candles_5m[399].close_time


# --------------------------------------------------------------------------
# SL-önce kuralı (intrabar, kötümser)
# --------------------------------------------------------------------------

class TestSlBeforeTpRule:
    def test_sl_and_tp1_same_candle_sl_wins(self):
        signal = _mk_signal(entry_price=100.0, stop_price=99.0)
        cfg = _Cfg()

        candles_5m = [
            _mk_candle(0, 100.0, 100.0, 100.0, 100.0),      # sinyal mumu
            _mk_candle(1, 100.0, 102.0, 98.0, 100.0),        # giriş mumu: TP1(~101.02) VE SL(99.0) ikisi de değiyor
        ]

        pos = open_position(signal, candles_5m, signal_idx=0, cfg=cfg, balance=10_000.0)
        assert pos is not None

        trade = manage_position(pos, candles_5m, cfg)

        assert trade.exit_reason == "SL"
        assert len(trade.legs) == 1
        assert trade.legs[0]["label"] == "SL"
        assert trade.legs[0]["quantity"] == pytest.approx(trade.quantity)
        assert trade.legs[0]["price"] == pytest.approx(99.0)
        assert trade.exit_price == pytest.approx(99.0)

    def test_sl_and_tp2_same_candle_after_tp1_sl_wins(self):
        # TP1 önceki mumda dolmuş (BE aktif); bu mumda hem BE-SL hem TP2 değiyor -> SL(TRAIL) kazanır
        signal = _mk_signal(entry_price=100.0, stop_price=99.0)
        cfg = _Cfg()

        candles_5m = [
            _mk_candle(0, 100.0, 100.0, 100.0, 100.0),   # sinyal
            _mk_candle(1, 100.0, 101.5, 100.6, 101.0),   # giriş + TP1 dolumu (BE aktif olur)
            _mk_candle(2, 100.5, 103.0, 99.0, 100.0),    # aynı mumda hem BE(~100.07) hem TP2(~102.52) değiyor
        ]

        pos = open_position(signal, candles_5m, signal_idx=0, cfg=cfg, balance=10_000.0)
        trade = manage_position(pos, candles_5m, cfg)

        assert trade.exit_reason == "TRAIL"
        labels = [leg["label"] for leg in trade.legs]
        assert labels == ["TP1", "TRAIL"]
        assert trade.legs[1]["price"] == pytest.approx(pos.breakeven_price)


# --------------------------------------------------------------------------
# TP1 -> break-even -> trailing akışı
# --------------------------------------------------------------------------

class TestTp1BreakevenTrailFlow:
    def test_tp1_partial_then_be_then_trail_close(self):
        signal = _mk_signal(entry_price=100.0, stop_price=99.0)
        cfg = _Cfg()

        candles_5m = [
            _mk_candle(0, 100.0, 100.2, 99.8, 100.0),   # sinyal mumu
            _mk_candle(1, 100.0, 100.5, 99.5, 100.0),   # giriş mumu: hiçbir şey değmiyor
            _mk_candle(2, 100.6, 101.5, 100.6, 101.2),  # TP1 dolar (BE + trailing aktif olur)
            _mk_candle(3, 100.1, 100.2, 99.9, 100.0),   # BE'ye değip kapanır (TRAIL)
        ]

        pos = open_position(signal, candles_5m, signal_idx=0, cfg=cfg, balance=10_000.0)
        assert pos is not None
        assert pos.entry_idx == 1
        # Kayma: SONRAKİ mumun open'ı (100.0) + %0,02 aleyhte (LONG -> yukarı)
        assert pos.entry_price == pytest.approx(100.0 * 1.0002)

        trade = manage_position(pos, candles_5m, cfg)

        assert trade.exit_reason == "TRAIL"
        assert len(trade.legs) == 2

        tp1_leg, trail_leg = trade.legs
        assert tp1_leg["label"] == "TP1"
        assert tp1_leg["quantity"] == pytest.approx(trade.quantity * cfg.scalper_tp1_fraction)
        assert tp1_leg["price"] == pytest.approx(pos.tp1_price)

        assert trail_leg["label"] == "TRAIL"
        expected_runner_qty = trade.quantity * (1 - cfg.scalper_tp1_fraction - cfg.scalper_tp2_fraction) \
            + trade.quantity * cfg.scalper_tp2_fraction  # TP2 hiç dolmadı -> tp2+runner birlikte TRAIL'de kapanır
        assert trail_leg["quantity"] == pytest.approx(expected_runner_qty)
        assert trail_leg["price"] == pytest.approx(pos.breakeven_price)

        # SL gerçekten break-even'e taşınmış olmalı (yapısal stop 99.0'dan farklı)
        assert pos.breakeven_price > 99.0
        assert pos.breakeven_price == pytest.approx(pos.entry_price * (1 + cfg.scalper_breakeven_buffer_pct / 100.0))


# --------------------------------------------------------------------------
# Komisyon + kayma -> PnL (elle hesap karşılaştırması)
# --------------------------------------------------------------------------

class TestCommissionAndSlippage:
    def test_full_stop_loss_pnl_matches_manual_calculation(self):
        entry_hint = 100.0
        stop_price = 99.0
        balance = 10_000.0
        cfg = _Cfg()

        signal = _mk_signal(entry_price=entry_hint, stop_price=stop_price)
        candles_5m = [
            _mk_candle(0, 100.0, 100.0, 100.0, 100.0),   # sinyal
            _mk_candle(1, 100.0, 100.5, 98.0, 99.0),     # giriş + hemen SL (98.0 <= 99.0)
        ]

        pos = open_position(signal, candles_5m, signal_idx=0, cfg=cfg, balance=balance)
        assert pos is not None
        trade = manage_position(pos, candles_5m, cfg)

        # --- Elle hesap (spesifikasyondaki formüllerle, koddan bağımsız) ---
        qty_expected = (balance * cfg.scalper_risk_percentage / 100.0) / abs(entry_hint - stop_price)
        entry_price_expected = entry_hint * 1.0002  # LONG: kayma aleyhte (yukarı)
        exit_price_expected = stop_price             # SL dolumu yapısal stopta, kaymasız

        gross_leg_pnl = (exit_price_expected - entry_price_expected) * qty_expected
        exit_commission = 0.0005 * qty_expected * exit_price_expected
        entry_commission = 0.0005 * qty_expected * entry_price_expected
        expected_total_pnl = gross_leg_pnl - exit_commission - entry_commission

        assert trade.quantity == pytest.approx(qty_expected)
        assert trade.entry_price == pytest.approx(entry_price_expected)
        assert trade.exit_price == pytest.approx(exit_price_expected)
        assert trade.pnl == pytest.approx(expected_total_pnl, rel=1e-9)

        expected_margin = qty_expected * entry_price_expected / cfg.scalper_leverage
        expected_roi = expected_total_pnl / expected_margin * 100.0
        assert trade.roi_pct == pytest.approx(expected_roi, rel=1e-9)

    def test_short_entry_slippage_direction(self):
        entry_hint = 100.0
        stop_price = 101.0  # SHORT: stop girişin ÜSTÜNDE
        cfg = _Cfg()
        signal = _mk_signal(entry_price=entry_hint, stop_price=stop_price, direction=Direction.SHORT)

        candles_5m = [
            _mk_candle(0, 100.0, 100.0, 100.0, 100.0),
            _mk_candle(1, 100.0, 100.2, 99.8, 100.0),
        ]
        pos = open_position(signal, candles_5m, signal_idx=0, cfg=cfg, balance=10_000.0)
        assert pos is not None
        # SHORT: kayma aleyhte -> daha DÜŞÜK fiyattan dolum (satarken daha az alırsın)
        assert pos.entry_price == pytest.approx(100.0 * 0.9998)
        assert pos.entry_price < entry_hint


# --------------------------------------------------------------------------
# Boyutlama / risk kapısı
# --------------------------------------------------------------------------

class TestOpenPositionGates:
    def test_stop_distance_out_of_bounds_rejects(self):
        cfg = _Cfg(scalper_min_stop_pct=0.5, scalper_max_stop_pct=1.0)
        signal = _mk_signal(entry_price=100.0, stop_price=99.99)  # %0.01 -> min'in altında
        candles_5m = [_mk_candle(0, 100.0, 100.0, 100.0, 100.0),
                      _mk_candle(1, 100.0, 100.0, 100.0, 100.0)]
        assert open_position(signal, candles_5m, 0, cfg, balance=10_000.0) is None

    def test_no_next_candle_rejects(self):
        cfg = _Cfg()
        signal = _mk_signal(entry_price=100.0, stop_price=99.0)
        candles_5m = [_mk_candle(0, 100.0, 100.0, 100.0, 100.0)]  # sinyal mumu son mum -> giriş yok
        assert open_position(signal, candles_5m, 0, cfg, balance=10_000.0) is None

    def test_nominal_cap_clips_quantity(self):
        # Çok yüksek risk yüzdesiyle qty, nominal tavanı (balance*leverage*0.5) aşmalı ve kırpılmalı
        cfg = _Cfg(scalper_risk_percentage=90.0, scalper_leverage=20)
        signal = _mk_signal(entry_price=100.0, stop_price=99.5)  # dar stop -> büyük qty
        candles_5m = [_mk_candle(0, 100.0, 100.0, 100.0, 100.0),
                      _mk_candle(1, 100.0, 100.0, 100.0, 100.0)]
        pos = open_position(signal, candles_5m, 0, cfg, balance=10_000.0)
        assert pos is not None
        nominal_cap = 10_000.0 * cfg.scalper_leverage * 0.5
        assert pos.qty_total * 100.0 == pytest.approx(nominal_cap)


# --------------------------------------------------------------------------
# simulate_symbol — tek eşzamanlı pozisyon + tarama/yönetim döngüsü
# --------------------------------------------------------------------------

class TestSimulateSymbol:
    def test_single_concurrent_position_sequential_non_overlapping_trades(self):
        cfg = _Cfg()
        candles_5m: List[Candle] = []
        for i in range(10):
            if i % 2 == 0:
                candles_5m.append(_mk_candle(i, 100.0, 100.1, 99.9, 100.0))  # sinyal mumu
            else:
                candles_5m.append(_mk_candle(i, 100.0, 100.5, 90.0, 95.0))  # giriş + anında SL

        trades = simulate_symbol("TESTUSDT", candles_5m, [], [], [_AlwaysLongStrategy()], cfg)

        assert [t.exit_idx for t in trades] == [1, 3, 5, 7, 9]
        for prev, curr in zip(trades, trades[1:]):
            assert prev.exit_time < curr.entry_time  # örtüşme yok

    def test_no_strategies_enabled_yields_no_trades(self):
        cfg = _Cfg()
        candles_5m = [_mk_candle(i, 100.0, 100.1, 99.9, 100.0) for i in range(10)]
        trades = simulate_symbol("TESTUSDT", candles_5m, [], [], [], cfg)
        assert trades == []

    def test_too_few_candles_yields_no_trades(self):
        cfg = _Cfg()
        trades = simulate_symbol("TESTUSDT", [_mk_candle(0, 100, 100, 100, 100)], [], [],
                                  [_AlwaysLongStrategy()], cfg)
        assert trades == []


# --------------------------------------------------------------------------
# compute_stats
# --------------------------------------------------------------------------

class TestComputeStats:
    def _trade(self, pnl: float, roi: float, exit_time: int, duration: float = 15.0,
               mae: float = -5.0, mfe: float = 10.0) -> BacktestTrade:
        return BacktestTrade(
            strategy="B", symbol="TESTUSDT", direction="LONG",
            entry_price=100.0, entry_time=0, exit_price=101.0, exit_time=exit_time,
            quantity=1.0, leverage=20, margin_usdt=5.0, pnl=pnl, roi_pct=roi,
            exit_reason="TP_LADDER", mae_pct=mae, mfe_pct=mfe,
            duration_minutes=duration, exit_idx=0,
        )

    def test_empty_trades(self):
        stats = compute_stats([])
        assert stats["trades"] == 0
        assert stats["profit_factor"] == 0.0

    def test_winrate_profit_factor_drawdown(self):
        trades = [
            self._trade(pnl=100.0, roi=10.0, exit_time=1),
            self._trade(pnl=-40.0, roi=-4.0, exit_time=2),
            self._trade(pnl=-40.0, roi=-4.0, exit_time=3),
            self._trade(pnl=200.0, roi=20.0, exit_time=4),
        ]
        stats = compute_stats(trades)
        assert stats["trades"] == 4
        assert stats["wins"] == 2
        assert stats["winrate"] == pytest.approx(50.0)
        assert stats["total_pnl"] == pytest.approx(220.0)
        assert stats["profit_factor"] == pytest.approx(300.0 / 80.0)
        assert stats["max_consec_losses"] == 2
        # kümülatif: 100 -> 60 -> 20 -> 220 ; tepe=100 (idx0), en derin çöküş 100-20=80
        assert stats["max_drawdown"] == pytest.approx(80.0)


# --------------------------------------------------------------------------
# fetch_paginated — AĞ YOK, enjekte edilmiş sahte veri kaynağıyla
# --------------------------------------------------------------------------

class TestFetchPaginated:
    @pytest.mark.asyncio
    async def test_pages_backward_without_gaps_or_duplicates(self):
        all_candles = [_mk_candle(i, 100.0, 100.0, 100.0, 100.0) for i in range(1000)]
        call_count = 0

        async def fake_fetch(symbol: str, interval: str, limit: int,
                              end_time: Optional[int] = None) -> List[Candle]:
            nonlocal call_count
            call_count += 1
            cutoff = end_time if end_time is not None else all_candles[-1].close_time
            eligible = [c for c in all_candles if c.open_time <= cutoff]
            return eligible[-limit:]

        result = await fetch_paginated(
            fake_fetch, "BTCUSDT", "5m", total_needed=250,
            end_time=all_candles[-1].close_time, page_limit=100,
        )

        assert len(result) == 250
        assert result == all_candles[-250:]
        assert call_count == 3  # 100 + 100 + 50
        # eski -> yeni sıralı
        assert all(a.open_time < b.open_time for a, b in zip(result, result[1:]))

    @pytest.mark.asyncio
    async def test_stops_early_when_history_exhausted(self):
        all_candles = [_mk_candle(i, 100.0, 100.0, 100.0, 100.0) for i in range(30)]

        async def fake_fetch(symbol, interval, limit, end_time=None):
            cutoff = end_time if end_time is not None else all_candles[-1].close_time
            eligible = [c for c in all_candles if c.open_time <= cutoff]
            return eligible[-limit:]

        # 500 mum iste ama borsada sadece 30 mum var -> sonsuz döngüye girmemeli
        result = await fetch_paginated(
            fake_fetch, "BTCUSDT", "5m", total_needed=500,
            end_time=all_candles[-1].close_time, page_limit=100,
        )
        assert result == all_candles
        assert len(result) == 30
