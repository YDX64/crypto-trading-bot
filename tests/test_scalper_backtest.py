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

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import pytest

import src.strategies.scalper.backtest as backtest_module
from src.strategies.scalper.backtest import (
    BACKTEST_WARMUP_CANDLES,
    BacktestTrade,
    OpenPosition,
    _apply_capacity_gate,
    build_context,
    compute_stats,
    fetch_paginated,
    gather_symbol_data,
    manage_position,
    open_position,
    resolve_backtest_window,
    run_backtest,
    scalper_config_snapshot,
    simulate_symbol,
    write_json_report,
)
from src.strategies.scalper.regime import detect_regime
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
    """cfg sözleşmesinin test için minimal, ağdan/settings'ten bağımsız kopyası.

    NOT: scalper_entry_mode/taker_fee_pct/maker_fee_pct/maker_fill_timeout_candles
    burada da mevcut çünkü backtest.py artık komisyon oranlarını settings yerine
    doğrudan cfg'den okuyor (bkz. görev: "Sabit 0.05/0.02 değerlerini koddan sök").
    Varsayılanlar ESKİ sabitlerle (_COMMISSION_RATE=0.0005 -> %0.05 taker) birebir
    aynı — bu yüzden mevcut testlerin sayısal beklentileri DEĞİŞMEDEN geçerli kalır.
    """
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
    scalper_entry_mode: str = "taker"
    scalper_taker_fee_pct: float = 0.05
    scalper_maker_fee_pct: float = 0.02
    scalper_maker_fill_timeout_candles: int = 3
    scalper_min_rr: float = 1.2
    scalper_regime_filter: bool = True
    scalper_max_positions: int = 3


def _mk_signal(entry_price: float, stop_price: float,
               direction: Direction = Direction.LONG,
               strategy: str = "B", symbol: str = "TESTUSDT",
               risk_multiplier: float = 1.0,
               regime: Regime = Regime.UP) -> ScalpSignal:
    return ScalpSignal(
        strategy=strategy, symbol=symbol, direction=direction,
        entry_price=entry_price, stop_price=stop_price, reason="test",
        regime=regime, atr_5m=1.0, risk_multiplier=risk_multiplier,
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

    def test_regime_requires_fixed_ema200_and_excludes_future_4h_candle(self):
        interval_4h = 14_400_000
        past_4h = [
            _mk_candle(
                i,
                100.0 + i,
                100.5 + i,
                99.5 + i,
                100.0 + i,
                interval_ms=interval_4h,
            )
            for i in range(200)
        ]
        assert detect_regime(past_4h[:199]) == Regime.UNKNOWN
        assert detect_regime(past_4h) == Regime.UP

        # 200 kapanmış 4h mumunun hemen ardından uç bir GELECEK mumu ekle.
        # 5m cutoff tam 200. 4h mumun kapanışına denk gelir; gelecek mum
        # bağlama/rejime sızmamalı.
        future_4h = _mk_candle(
            200, 1.0, 1.0, 1.0, 1.0, interval_ms=interval_4h,
        )
        current_5m = _mk_candle(200 * 48 - 1, 299.0, 299.0, 299.0, 299.0)
        ctx = build_context(
            "BTCUSDT", [current_5m], [], past_4h + [future_4h], index=0, leverage=20,
        )

        assert len(ctx.candles_4h) == 200
        assert future_4h not in ctx.candles_4h
        assert ctx.regime == Regime.UP


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

    def test_min_rr_gate_matches_live_formula(self):
        # Beklenen harman ROI = 29; SL riski = %3 * 20x = 60;
        # R:R = 0.483 < 1.2 -> canlı executor gibi reddedilmeli.
        cfg = _Cfg(scalper_max_stop_pct=5.0, scalper_min_rr=1.2)
        signal = _mk_signal(entry_price=100.0, stop_price=97.0)
        candles_5m = [
            _mk_candle(0, 100.0, 100.0, 100.0, 100.0),
            _mk_candle(1, 100.0, 100.0, 100.0, 100.0),
        ]
        missed_counter: dict = {}

        assert open_position(
            signal, candles_5m, 0, cfg, balance=10_000.0,
            missed_counter=missed_counter,
        ) is None
        assert missed_counter == {"min_rr_rejected": 1}

    def test_min_rr_gate_can_be_disabled_like_live(self):
        cfg = _Cfg(scalper_max_stop_pct=5.0, scalper_min_rr=0.0)
        signal = _mk_signal(entry_price=100.0, stop_price=97.0)
        candles_5m = [
            _mk_candle(0, 100.0, 100.0, 100.0, 100.0),
            _mk_candle(1, 100.0, 100.0, 100.0, 100.0),
        ]

        assert open_position(signal, candles_5m, 0, cfg, balance=10_000.0) is not None


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

    def test_warmup_candles_do_not_enter_requested_window_metrics(self):
        cfg = _Cfg()
        candles_5m: List[Candle] = []
        for i in range(8):
            if i % 2 == 0:
                candles_5m.append(_mk_candle(i, 100.0, 100.1, 99.9, 100.0))
            else:
                candles_5m.append(_mk_candle(i, 100.0, 100.5, 90.0, 95.0))

        test_start = candles_5m[4].close_time
        trades = simulate_symbol(
            "TESTUSDT",
            candles_5m,
            [],
            [],
            [_AlwaysLongStrategy()],
            cfg,
            test_start_time_ms=test_start,
        )

        assert [trade.exit_idx for trade in trades] == [5, 7]
        assert all(trade.entry_time >= test_start for trade in trades)


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

    @pytest.mark.asyncio
    async def test_historical_end_time_excludes_candle_that_closes_after_boundary(self):
        candles = [
            _mk_candle(i, 100.0, 100.0, 100.0, 100.0)
            for i in range(3)
        ]
        boundary = candles[2].open_time

        async def open_time_filtered_exchange(
            symbol, interval, limit, end_time=None,
        ):
            # Binance-benzeri davranış: endTime'a open_time ile dahil eder;
            # son mum boundary'de açılır ama daha sonra kapanır.
            eligible = [
                candle for candle in candles
                if end_time is None or candle.open_time <= end_time
            ]
            return eligible[-limit:]

        result = await fetch_paginated(
            open_time_filtered_exchange,
            "BTCUSDT",
            "5m",
            total_needed=2,
            end_time=boundary,
            page_limit=2,
        )

        assert result == candles[:2]
        assert all(candle.close_time <= boundary for candle in result)


class TestBacktestWarmupAndProvenance:
    @pytest.mark.asyncio
    async def test_gather_fetches_fixed_live_context_warmup(self):
        interval_ms = {"5m": 300_000, "15m": 900_000, "4h": 14_400_000}
        expected = {
            "5m": 288 + BACKTEST_WARMUP_CANDLES["5m"],
            "15m": 96 + BACKTEST_WARMUP_CANDLES["15m"],
            "4h": 6 + BACKTEST_WARMUP_CANDLES["4h"],
        }
        available = {
            interval: [
                _mk_candle(i, 100.0, 100.0, 100.0, 100.0, interval_ms=interval_ms[interval])
                for i in range(count)
            ]
            for interval, count in expected.items()
        }

        async def fake_fetch(symbol, interval, limit, end_time=None):
            candles = available[interval]
            eligible = [c for c in candles if end_time is None or c.open_time <= end_time]
            return eligible[-limit:]

        data = await gather_symbol_data(
            fake_fetch, "BTCUSDT", days=1, end_time=10**15,
        )

        assert {interval: len(candles) for interval, candles in data.items()} == expected

    @pytest.mark.asyncio
    async def test_run_backtest_wires_warmup_boundary_and_metadata(self, monkeypatch):
        interval_ms = {"5m": 300_000, "15m": 900_000, "4h": 14_400_000}

        class FakeKlineFetcher:
            def __init__(self, base_url, guard_mode="live"):
                # guard_mode: harness "batch" geçer (D17 — bütçe dolunca koşu
                # ölmesin, beklesin). Sahte sınıf için yalnız imza uyumu.
                self.base_url = base_url
                self.guard_mode = guard_mode

            async def get_klines(self, symbol, interval, limit, end_time=None):
                span = interval_ms[interval]
                cutoff = end_time if end_time is not None else 1_000_000_000_000
                last_open = ((cutoff - span) // span) * span
                first_open = last_open - (limit - 1) * span
                return [
                    Candle(
                        open_time=first_open + i * span,
                        open=100.0,
                        high=100.1,
                        low=99.9,
                        close=100.0,
                        volume=100.0,
                        close_time=first_open + (i + 1) * span - 1,
                    )
                    for i in range(limit)
                ]

            async def close(self):
                return None

        monkeypatch.setattr(backtest_module, "KlineFetcher", FakeKlineFetcher)
        monkeypatch.setattr(
            backtest_module, "_ThrottledFetch", lambda fetch: fetch,
        )
        monkeypatch.setattr(
            backtest_module, "get_enabled", lambda _names: [_AlwaysLongStrategy()],
        )

        end_time_ms = 1_000_000_000_000
        metadata: dict = {}
        trades = await run_backtest(
            days=1,
            symbols=["BTCUSDT"],
            strategy_names="X",
            cfg=_Cfg(),
            run_metadata=metadata,
            end_time_ms=end_time_ms,
        )

        assert trades
        assert all(
            trade.entry_time >= metadata["test_window"]["start_ms"]
            for trade in trades
        )
        assert metadata["test_window"]["end_ms"] == end_time_ms
        assert metadata["universe_snapshot"]["symbols"] == ["BTCUSDT"]
        assert {
            interval: window["candles_fetched"]
            for interval, window in metadata["data_windows"]["BTCUSDT"].items()
        } == {
            "5m": 288 + BACKTEST_WARMUP_CANDLES["5m"],
            "15m": 96 + BACKTEST_WARMUP_CANDLES["15m"],
            "4h": 6 + BACKTEST_WARMUP_CANDLES["4h"],
        }

    def test_json_report_records_config_code_window_and_universe(self, tmp_path):
        cfg = _Cfg(scalper_entry_mode="maker", scalper_min_rr=1.5)
        cfg_snapshot = scalper_config_snapshot(cfg)
        metadata = {
            "git_sha": "abc123",
            "git_dirty": True,
            "scalper_config": cfg_snapshot,
            "test_window": {"requested_days": 7, "start_ms": 100, "end_ms": 200},
            "warmup_candles": dict(BACKTEST_WARMUP_CANDLES),
            "universe_snapshot": {
                "selection_mode": "explicit_symbols",
                "symbols": ["BTCUSDT"],
            },
            "data_windows": {"BTCUSDT": {"5m": {"candles_fetched": 2166}}},
            "data_source_base_url": "https://fapi.binance.com",
        }

        path = write_json_report(
            [],
            days=7,
            symbols=["BTCUSDT"],
            strategy_names="C",
            cfg=cfg,
            run_metadata=metadata,
            output_dir=tmp_path,
        )
        with open(path, encoding="utf-8") as report_file:
            payload = json.load(report_file)

        provenance = payload["provenance"]
        assert provenance["git_sha"] == "abc123"
        assert provenance["git_dirty"] is True
        assert provenance["scalper_config"] == cfg_snapshot
        assert set(cfg_snapshot) == {
            name for name in vars(cfg) if name.startswith("scalper_")
        }
        assert provenance["test_window"] == metadata["test_window"]
        assert provenance["universe_snapshot"] == metadata["universe_snapshot"]
        assert provenance["data_windows"] == metadata["data_windows"]


# --------------------------------------------------------------------------
# Maker giriş modu (cfg.scalper_entry_mode == "maker")
# --------------------------------------------------------------------------

class TestMakerEntryMode:
    def test_maker_fill_at_limit_price_no_slippage_maker_commission(self):
        # Sinyal mumunun kapanışı (100.0) LIMIT fiyatıdır. idx1 değmiyor
        # (low=100.2 > 100.0), idx2 değiyor (low=99.8 <= 100.0) -> orada dolum.
        cfg = _Cfg(scalper_entry_mode="maker", scalper_maker_fill_timeout_candles=3)
        signal = _mk_signal(entry_price=100.0, stop_price=99.0)

        candles_5m = [
            _mk_candle(0, 100.0, 100.0, 100.0, 100.0),   # sinyal mumu -> limit=100.0
            _mk_candle(1, 100.3, 100.5, 100.2, 100.3),   # değmiyor
            _mk_candle(2, 100.1, 100.2, 99.8, 100.0),    # değiyor -> dolum
            _mk_candle(3, 100.0, 100.5, 99.5, 100.0),
        ]

        pos = open_position(signal, candles_5m, signal_idx=0, cfg=cfg, balance=10_000.0)

        assert pos is not None
        assert pos.entry_idx == 2
        assert pos.entry_price == pytest.approx(100.0)  # limit fiyatı, kayma YOK
        assert pos.entry_commission_rate == pytest.approx(cfg.scalper_maker_fee_pct / 100.0)
        assert pos.exit_commission_rate == pytest.approx(cfg.scalper_taker_fee_pct / 100.0)

    def test_maker_timeout_no_fill_yields_no_trade_and_increments_missed_counter(self):
        # timeout=2 mum içinde limit'e hiç değmiyor (idx1, idx2 low > limit);
        # idx3'te değse de timeout dışında -> sinyal sessizce iptal, kaçan sayaç artar.
        cfg = _Cfg(scalper_entry_mode="maker", scalper_maker_fill_timeout_candles=2)
        signal = _mk_signal(entry_price=100.0, stop_price=99.0)

        candles_5m = [
            _mk_candle(0, 100.0, 100.0, 100.0, 100.0),   # sinyal mumu -> limit=100.0
            _mk_candle(1, 100.5, 100.6, 100.3, 100.5),   # değmiyor
            _mk_candle(2, 100.4, 100.5, 100.2, 100.3),   # değmiyor (timeout'un son mumu)
            _mk_candle(3, 99.0, 99.5, 98.5, 99.0),       # değerdi ama timeout DIŞINDA
        ]

        missed_counter: dict = {}
        pos = open_position(
            signal, candles_5m, signal_idx=0, cfg=cfg, balance=10_000.0,
            missed_counter=missed_counter,
        )

        assert pos is None
        assert missed_counter.get("maker_missed") == 1

    def test_exit_commission_stays_taker_even_in_maker_mode(self):
        # Maker girişte dolum + hemen sonrasında SL -> giriş komisyonu MAKER,
        # çıkış (SL) komisyonu TAKER oranıyla hesaplanmalı (elle karşılaştırma).
        cfg = _Cfg(scalper_entry_mode="maker", scalper_maker_fill_timeout_candles=2)
        signal = _mk_signal(entry_price=100.0, stop_price=99.0)

        candles_5m = [
            _mk_candle(0, 100.0, 100.0, 100.0, 100.0),   # sinyal -> limit=100.0
            _mk_candle(1, 100.0, 100.2, 99.8, 100.0),    # değiyor -> dolum @100.0
            _mk_candle(2, 99.5, 99.6, 98.5, 99.0),        # SL'ye değiyor (98.5 <= 99.0)
        ]

        pos = open_position(signal, candles_5m, signal_idx=0, cfg=cfg, balance=10_000.0)
        assert pos is not None
        trade = manage_position(pos, candles_5m, cfg)

        # --- Elle hesap ---
        qty_expected = (10_000.0 * cfg.scalper_risk_percentage / 100.0) / abs(100.0 - 99.0)
        entry_price_expected = 100.0  # limit, kaymasız
        exit_price_expected = 99.0    # yapısal stop

        gross_leg_pnl = (exit_price_expected - entry_price_expected) * qty_expected
        exit_commission = (cfg.scalper_taker_fee_pct / 100.0) * qty_expected * exit_price_expected
        entry_commission = (cfg.scalper_maker_fee_pct / 100.0) * qty_expected * entry_price_expected
        expected_total_pnl = gross_leg_pnl - exit_commission - entry_commission

        assert trade.exit_reason == "SL"
        assert trade.entry_price == pytest.approx(entry_price_expected)
        assert trade.exit_price == pytest.approx(exit_price_expected)
        assert trade.pnl == pytest.approx(expected_total_pnl, rel=1e-9)


# --------------------------------------------------------------------------
# Rejim etiketi
# --------------------------------------------------------------------------

class TestRegimeTagging:
    def test_position_and_trade_record_signal_regime(self):
        cfg = _Cfg()
        signal = _mk_signal(entry_price=100.0, stop_price=99.0, regime=Regime.DOWN)

        candles_5m = [
            _mk_candle(0, 100.0, 100.0, 100.0, 100.0),
            _mk_candle(1, 100.0, 100.5, 98.0, 99.0),   # giriş + hemen SL
        ]

        pos = open_position(signal, candles_5m, signal_idx=0, cfg=cfg, balance=10_000.0)
        assert pos is not None
        assert pos.regime == "DOWN"

        trade = manage_position(pos, candles_5m, cfg)
        assert trade.regime == "DOWN"

    def test_simulate_symbol_tags_trades_with_context_regime(self):
        # Sahte strateji sinyalin regime'ini ctx.regime'den alır (gerçek A/B/C
        # stratejileriyle birebir aynı sözleşme); backtest kaydına geçmeli.
        cfg = _Cfg()

        class _RegimeAwareLongStrategy(StrategyProtocol):
            name = "X"

            def evaluate(self, ctx: StrategyContext) -> Optional[ScalpSignal]:
                return _mk_signal(
                    ctx.current_price, ctx.current_price * 0.99,
                    symbol=ctx.symbol, regime=ctx.regime,
                )

        candles_5m = [
            _mk_candle(0, 100.0, 100.1, 99.9, 100.0),
            _mk_candle(1, 100.0, 100.5, 90.0, 95.0),   # giriş + anında SL
        ]

        trades = simulate_symbol(
            "TESTUSDT", candles_5m, [], [], [_RegimeAwareLongStrategy()], cfg,
        )

        assert len(trades) == 1
        # build_context([]) 4h verisi yokken detect_regime -> UNKNOWN döner (regime.py sözleşmesi)
        assert trades[0].regime == "UNKNOWN"


def test_json_report_uses_null_for_infinite_values(tmp_path):
    report_path = backtest_module.write_json_report(
        [],
        days=1,
        symbols=["BTCUSDT"],
        strategy_names="C",
        run_metadata={"data_windows": {"sentinel": float("inf")}},
        output_dir=tmp_path,
    )

    raw = Path(report_path).read_text(encoding="utf-8")
    assert "Infinity" not in raw
    assert json.loads(raw)["provenance"]["data_windows"]["sentinel"] is None


# --------------------------------------------------------------------------
# --start/--end pencere çözümü (resolve_backtest_window) — AĞ YOK, saf
# --------------------------------------------------------------------------

class TestResolveBacktestWindow:
    def test_days_only_keeps_legacy_behavior(self):
        # --start/--end verilmezse eski --days davranışı birebir korunmalı:
        # start_ms/end_ms None döner, run_backtest kendi "şu andan N gün
        # geriye" mantığını uygular.
        effective_days, start_ms, end_ms = resolve_backtest_window(30, None, None)
        assert effective_days == 30
        assert start_ms is None
        assert end_ms is None

    def test_start_end_overrides_days_and_computes_span(self):
        # 2026-01-23 -> 2026-02-13 = 21 gün tam; --days (30) görmezden gelinmeli.
        effective_days, start_ms, end_ms = resolve_backtest_window(
            30, "2026-01-23", "2026-02-13",
        )
        assert start_ms == 1769126400000  # 2026-01-23T00:00:00Z
        assert end_ms == 1770940800000    # 2026-02-13T00:00:00Z
        assert end_ms - start_ms == 21 * 86_400_000
        assert effective_days == 21

    def test_end_exclusive_window_is_utc_midnight_to_midnight(self):
        _, start_ms, end_ms = resolve_backtest_window(1, "2026-08-07", "2026-08-08")
        assert (end_ms - start_ms) == 86_400_000  # tam 1 gün, [start, end)

    def test_single_day_window_still_fetches_at_least_one_day(self):
        # --start/--end yalnız tarih (saat yok) aldığından fark her zaman tam
        # gün sayısıdır; en küçük geçerli pencere (1 gün) bile en az 1 günlük
        # veri istemeli (aşağı taşma yok).
        effective_days, start_ms, end_ms = resolve_backtest_window(
            30, "2026-08-07", "2026-08-08",
        )
        assert effective_days == 1
        assert end_ms - start_ms == 86_400_000

    def test_only_start_without_end_raises(self):
        with pytest.raises(ValueError):
            resolve_backtest_window(30, "2026-01-23", None)

    def test_only_end_without_start_raises(self):
        with pytest.raises(ValueError):
            resolve_backtest_window(30, None, "2026-02-13")

    def test_end_not_after_start_raises(self):
        with pytest.raises(ValueError):
            resolve_backtest_window(30, "2026-02-13", "2026-01-23")
        with pytest.raises(ValueError):
            resolve_backtest_window(30, "2026-02-13", "2026-02-13")  # eşit de reddedilir

    def test_bad_date_format_raises(self):
        with pytest.raises(ValueError):
            resolve_backtest_window(30, "23-01-2026", "2026-02-13")


# --------------------------------------------------------------------------
# Rejim kapısı paritesi (simulate_symbol) — canlı engine.py ile birebir:
# DOWN rejimde LONG / UP rejimde SHORT engellenir. Bu kapı 2026-08-21'e
# kadar yalnız canlı motordaydı; backtest'te YOKTU.
# --------------------------------------------------------------------------

class TestSimulateSymbolRegimeGate:
    _INTERVAL_4H = 1_000  # ms — sentetik, gerçek 4h süresi ÖNEMSİZ (yalnız sıralama)

    def _down_regime_4h_candles(self) -> List[Candle]:
        # Azalan kapanış dizisi -> EMA50 < EMA200 ve son kapanış < EMA50 => DOWN
        # (test_regime_requires_fixed_ema200... testindeki artan UP dizisinin aynası).
        return [
            _mk_candle(i, 300.0 - i, 300.5 - i, 299.5 - i, 300.0 - i,
                       interval_ms=self._INTERVAL_4H)
            for i in range(200)
        ]

    def _entry_5m_candles(self) -> List[Candle]:
        # 4h bağlamın son close_time'ından (199_999) çok sonrasında iki mum:
        # 0=sinyal, 1=giriş + anında SL (LONG stopu %1 altı, low=90 deler).
        big_offset = 500_000
        return [
            Candle(open_time=big_offset, open=100.0, high=100.1, low=99.9,
                   close=100.0, volume=100.0, close_time=big_offset + 299_999),
            Candle(open_time=big_offset + 300_000, open=100.0, high=100.5,
                   low=90.0, close=95.0, volume=100.0,
                   close_time=big_offset + 599_999),
        ]

    def test_down_regime_blocks_long_by_default(self):
        cfg = _Cfg()
        candles_4h = self._down_regime_4h_candles()
        assert detect_regime(candles_4h) == Regime.DOWN  # ön koşulu doğrula

        missed: dict = {}
        trades = simulate_symbol(
            "TESTUSDT", self._entry_5m_candles(), [], candles_4h,
            [_AlwaysLongStrategy()], cfg, missed_counter=missed,
        )

        assert trades == []
        assert missed.get("regime_gate") == 1

    def test_down_regime_allows_long_when_gate_disabled(self):
        cfg = _Cfg(scalper_regime_filter=False)
        candles_4h = self._down_regime_4h_candles()

        trades = simulate_symbol(
            "TESTUSDT", self._entry_5m_candles(), [], candles_4h,
            [_AlwaysLongStrategy()], cfg,
        )

        assert len(trades) == 1
        assert trades[0].exit_reason == "SL"


# --------------------------------------------------------------------------
# Kapasite kapısı paritesi (_apply_capacity_gate / run_backtest) — canlı
# engine.py ile birebir: `len(tracked | pending) >= scalper_max_positions`
# iken yeni giriş açılmaz. simulate_symbol her sembolü BAĞIMSIZ simüle
# ettiğinden (küresel saat YOK) bu kapı semboller birleştirildikten SONRA,
# run_backtest() içinde kronolojik bir geçişle uygulanır (2026-08-21,
# görev: "parite boşluğu — capacity"). Bu kapı 2026-08-21'e kadar YOKTU;
# SCALPER_MAX_POSITIONS=3 varyantının sunucunun 5'iyle birebir aynı sonucu
# vermesi bunu kanıtladı (autoresearch E4h, docs/EXPERIMENTS.md).
# --------------------------------------------------------------------------

def _mk_trade(symbol: str, entry_time: int, exit_time: int,
              strategy: str = "X", direction: str = "LONG") -> BacktestTrade:
    return BacktestTrade(
        strategy=strategy, symbol=symbol, direction=direction,
        entry_price=100.0, entry_time=entry_time,
        exit_price=101.0, exit_time=exit_time,
        quantity=1.0, leverage=20, margin_usdt=5.0, pnl=1.0, roi_pct=1.0,
        exit_reason="EOD", mae_pct=0.0, mfe_pct=0.0,
        duration_minutes=max(0.0, (exit_time - entry_time) / 60_000.0),
        exit_idx=0, regime="RANGE",
    )


class TestApplyCapacityGate:
    """`_apply_capacity_gate` doğrudan (run_backtest'in ağ/veri katmanı
    olmadan) — üst düzeyde kapasite mantığının kendisini test eder."""

    def test_max_positions_1_only_earlier_trade_survives(self):
        # AAA t=0'da girer, t=1000'de kapanır. BBB t=100'de girer (AAA hâlâ
        # açık) — max_positions=1 iken yalnız ERKEN (AAA) hayatta kalır.
        aaa = _mk_trade("AAAUSDT", entry_time=0, exit_time=1000)
        bbb = _mk_trade("BBBUSDT", entry_time=100, exit_time=900)
        cfg = _Cfg(scalper_max_positions=1)
        missed: dict = {}

        accepted = _apply_capacity_gate(
            [aaa, bbb], symbols=["AAAUSDT", "BBBUSDT"], cfg=cfg, missed_counter=missed,
        )

        assert [t.symbol for t in accepted] == ["AAAUSDT"]
        assert missed == {"capacity": 1}

    def test_max_positions_2_both_overlapping_trades_survive(self):
        aaa = _mk_trade("AAAUSDT", entry_time=0, exit_time=1000)
        bbb = _mk_trade("BBBUSDT", entry_time=100, exit_time=900)
        cfg = _Cfg(scalper_max_positions=2)
        missed: dict = {}

        accepted = _apply_capacity_gate(
            [aaa, bbb], symbols=["AAAUSDT", "BBBUSDT"], cfg=cfg, missed_counter=missed,
        )

        assert {t.symbol for t in accepted} == {"AAAUSDT", "BBBUSDT"}
        assert missed == {}

    def test_non_overlapping_trades_never_rejected_even_at_max_positions_1(self):
        # BBB, AAA tam kapandıktan (exit_time=1000) SONRA açılıyor (t=1000) ->
        # slot aynı anda boşalmış sayılır, kapasite dolu DEĞİLDİR.
        aaa = _mk_trade("AAAUSDT", entry_time=0, exit_time=1000)
        bbb = _mk_trade("BBBUSDT", entry_time=1000, exit_time=2000)
        cfg = _Cfg(scalper_max_positions=1)
        missed: dict = {}

        accepted = _apply_capacity_gate(
            [aaa, bbb], symbols=["AAAUSDT", "BBBUSDT"], cfg=cfg, missed_counter=missed,
        )

        assert {t.symbol for t in accepted} == {"AAAUSDT", "BBBUSDT"}
        assert missed == {}

    def test_same_entry_time_tie_break_uses_symbols_list_order(self):
        # Aynı 5m mumunda iki sembol de sinyal verirse (entry_time eşit),
        # canlı taramanın `self._universe` sırasına karşılık gelen
        # `symbols` argüman sırası kazanır.
        aaa = _mk_trade("AAAUSDT", entry_time=0, exit_time=1000)
        bbb = _mk_trade("BBBUSDT", entry_time=0, exit_time=1000)
        cfg = _Cfg(scalper_max_positions=1)

        accepted_aaa_first = _apply_capacity_gate(
            [bbb, aaa], symbols=["AAAUSDT", "BBBUSDT"], cfg=cfg,
        )
        assert [t.symbol for t in accepted_aaa_first] == ["AAAUSDT"]

        accepted_bbb_first = _apply_capacity_gate(
            [aaa, bbb], symbols=["BBBUSDT", "AAAUSDT"], cfg=cfg,
        )
        assert [t.symbol for t in accepted_bbb_first] == ["BBBUSDT"]

    def test_missing_scalper_max_positions_defaults_to_3(self):
        # cfg'de alan hiç yoksa (ör. eski bir sahte cfg) canlı Settings
        # varsayılanıyla (3) aynı taban kullanılır — patlamaz.
        @dataclass
        class _NoCapacityCfg:
            pass

        trades = [
            _mk_trade("A1USDT", 0, 1000), _mk_trade("A2USDT", 0, 1000),
            _mk_trade("A3USDT", 0, 1000), _mk_trade("A4USDT", 0, 1000),
        ]
        missed: dict = {}

        accepted = _apply_capacity_gate(
            trades, symbols=["A1USDT", "A2USDT", "A3USDT", "A4USDT"],
            cfg=_NoCapacityCfg(), missed_counter=missed,
        )

        assert len(accepted) == 3
        assert missed == {"capacity": 1}


class TestRunBacktestCapacityGateWiring:
    """run_backtest()'in _apply_capacity_gate'i gerçekten uyguladığını,
    simulate_symbol'ü sahte adaylarla değiştirip (ağ/gösterge hesaplaması
    devre dışı) uçtan uca doğrular."""

    @pytest.mark.asyncio
    async def test_run_backtest_drops_overlapping_trade_over_capacity(self, monkeypatch):
        trades_by_symbol = {
            "AAAUSDT": [_mk_trade("AAAUSDT", entry_time=0, exit_time=1000)],
            "BBBUSDT": [_mk_trade("BBBUSDT", entry_time=100, exit_time=900)],
        }

        async def fake_gather_symbol_data(*args, **kwargs):
            return {"5m": [], "15m": [], "4h": []}

        def fake_simulate_symbol(symbol, *args, **kwargs):
            return list(trades_by_symbol[symbol])

        monkeypatch.setattr(backtest_module, "gather_symbol_data", fake_gather_symbol_data)
        monkeypatch.setattr(backtest_module, "simulate_symbol", fake_simulate_symbol)

        missed_counter: dict = {}
        trades = await run_backtest(
            days=1,
            symbols=["AAAUSDT", "BBBUSDT"],
            strategy_names="C",
            cfg=_Cfg(scalper_max_positions=1),
            missed_counter=missed_counter,
            end_time_ms=10_000_000,
        )

        assert [t.symbol for t in trades] == ["AAAUSDT"]
        assert missed_counter.get("capacity") == 1

    @pytest.mark.asyncio
    async def test_run_backtest_keeps_both_when_capacity_allows(self, monkeypatch):
        trades_by_symbol = {
            "AAAUSDT": [_mk_trade("AAAUSDT", entry_time=0, exit_time=1000)],
            "BBBUSDT": [_mk_trade("BBBUSDT", entry_time=100, exit_time=900)],
        }

        async def fake_gather_symbol_data(*args, **kwargs):
            return {"5m": [], "15m": [], "4h": []}

        def fake_simulate_symbol(symbol, *args, **kwargs):
            return list(trades_by_symbol[symbol])

        monkeypatch.setattr(backtest_module, "gather_symbol_data", fake_gather_symbol_data)
        monkeypatch.setattr(backtest_module, "simulate_symbol", fake_simulate_symbol)

        missed_counter: dict = {}
        trades = await run_backtest(
            days=1,
            symbols=["AAAUSDT", "BBBUSDT"],
            strategy_names="C",
            cfg=_Cfg(scalper_max_positions=2),
            missed_counter=missed_counter,
            end_time_ms=10_000_000,
        )

        assert {t.symbol for t in trades} == {"AAAUSDT", "BBBUSDT"}
        assert missed_counter.get("capacity") is None
