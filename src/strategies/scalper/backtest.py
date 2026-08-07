"""
Scalper backtest motoru — tarihsel kline verisi üzerinde A/B/C(...) strateji
simülasyonu ve karşılaştırma raporu.

Tasarım: docs/superpowers/specs/2026-08-07-scalper-design.md

Veri: KlineFetcher public /fapi/v1/klines endpoint'inden GERİYE doğru
sayfalanarak (end_time ile) toplanır — imza/API anahtarı gerekmez, emir
yeteneği YOKTUR (salt veri okur). Tarihsel derinlik için varsayılan olarak
mainnet (https://fapi.binance.com) kullanılır; testnet geçmişi sığdır.

Zaman hizalama: her 5m adımında, o âna KADAR KAPANMIŞ 15m/4h mumları
dilimlenir (close_time <= anki 5m close_time) — geleceğe bakma (look-ahead)
YASAKTIR. build_context() bunu her çağrıda assert ile de doğrular.

Simülasyon sembol başına TEK eşzamanlı pozisyon kullanır: bir sinyal
pozisyon açtığında, o pozisyon kapanana kadar aynı sembolde yeni sinyal
aranmaz (backtest tarafı — canlı motorun scalper_max_positions kapısı
farklı bir kavramdır, burada YOK).

CLI:
    python -m src.strategies.scalper.backtest --days 30 \\
        --symbols BTCUSDT,ETHUSDT,SOLUSDT --strategies A,B,C
    python -m src.strategies.scalper.backtest --days 7 --symbols auto
"""

from __future__ import annotations

import argparse
import asyncio
import bisect
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from src.core.config import settings
from src.core.logger import app_logger
from src.strategies.scalper.data import KlineFetcher
from src.strategies.scalper.indicators import atr, chandelier_stop
from src.strategies.scalper.regime import detect_regime
from src.strategies.scalper.scanner import UniverseScanner
from src.strategies.scalper.setups import get_enabled
from src.strategies.scalper.types import (
    Candle,
    Direction,
    ScalpSignal,
    StrategyContext,
    StrategyProtocol,
    price_at_roi,
)

# --------------------------------------------------------------------------
# Sabitler
# --------------------------------------------------------------------------

# Kline çekme: aralığa göre günlük mum sayısı ve sayfalama arası bekleme.
_CANDLES_PER_DAY: Dict[str, int] = {"5m": 288, "15m": 96, "4h": 6}
_REQUEST_DELAY_SECONDS = 0.3
_MAX_PAGE_LIMIT = 1500  # Binance futures klines tek istekte azami mum sayısı

# StrategyContext pencere boyutları (tasarım dokümanıyla birebir).
_CTX_5M_WINDOW = 150
_CTX_15M_WINDOW = 100
_CTX_4H_WINDOW = 250

# Maliyet modeli.
_COMMISSION_RATE = 0.0005  # %0,05 nominal, giriş VE her çıkış bacağında
_SLIPPAGE_RATE = 0.0002    # %0,02, yalnız girişte, aleyhte

_DEFAULT_VIRTUAL_BALANCE = 10_000.0
_AUTO_UNIVERSE_TOP_N = 8

FetchFn = Callable[[str, str, int, Optional[int]], Awaitable[List[Candle]]]


# ==========================================================================
# Veri çekme — geriye doğru sayfalama
# ==========================================================================


class _ThrottledFetch:
    """FetchFn'i sarar; İLK çağrı hariç her çağrıdan önce `delay` kadar
    bekler. Böylece bir backtest koşusu boyunca (sayfalar arası, aralıklar
    arası, semboller arası) TÜM istekler arasında tutarlı bir bekleme
    uygulanır — borsaya karşı nazik davranılır."""

    def __init__(self, fetch: FetchFn, delay: float = _REQUEST_DELAY_SECONDS):
        self._fetch = fetch
        self._delay = delay
        self._first = True

    async def __call__(
        self, symbol: str, interval: str, limit: int, end_time: Optional[int] = None
    ) -> List[Candle]:
        if not self._first:
            await asyncio.sleep(self._delay)
        self._first = False
        return await self._fetch(symbol, interval, limit, end_time)


async def fetch_paginated(
    fetch: FetchFn,
    symbol: str,
    interval: str,
    total_needed: int,
    end_time: Optional[int] = None,
    page_limit: int = _MAX_PAGE_LIMIT,
) -> List[Candle]:
    """`fetch` ile GERİYE doğru sayfalayarak en az `total_needed` kadar mum
    toplar (eski → yeni sıralı, tekrarsız).

    Her sayfa `end_time`'dan (dahil) geriye doğru en fazla `page_limit`
    mum döner; sonraki sayfa bir önceki sayfanın en eski mumunun
    open_time-1'ini yeni end_time olarak kullanır. Sayfalama yalnızca borsa
    TAMAMEN BOŞ bir sayfa döndürdüğünde durur (gerçek tarihsel sınır).

    DİKKAT: "bu sayfa istenenden AZ mum döndürdü" tarihsel sınırın kanıtı
    DEĞİLDİR — KlineFetcher, "şu ana" en yakın (ilk) sayfada henüz
    KAPANMAMIŞ son mumu ATAR (_drop_unclosed); bu yüzden o sayfa doğal
    olarak `limit`'ten 1 eksik dönebilir, ama daha eski sayfalar hâlâ
    dolu olabilir. Bu yüzden erken durma YALNIZCA boş sayfada tetiklenir.
    """
    if total_needed <= 0:
        return []

    collected: List[Candle] = []
    cursor_end = end_time if end_time is not None else int(time.time() * 1000)

    while len(collected) < total_needed:
        remaining = total_needed - len(collected)
        limit = min(page_limit, max(remaining, 1))

        batch = await fetch(symbol, interval, limit, cursor_end)
        if not batch:
            break  # Borsa bu sembol/aralık için daha eski veri döndürmedi.

        collected = batch + collected
        cursor_end = batch[0].open_time - 1

    if len(collected) > total_needed:
        collected = collected[-total_needed:]

    return collected


async def gather_symbol_data(
    fetch: FetchFn, symbol: str, days: int, end_time: Optional[int] = None
) -> Dict[str, List[Candle]]:
    """Bir sembol için 5m/15m/4h tarihsel mum verisini `days` günlük derinlikte
    toplar."""
    out: Dict[str, List[Candle]] = {}
    for interval, per_day in _CANDLES_PER_DAY.items():
        needed = days * per_day
        out[interval] = await fetch_paginated(fetch, symbol, interval, needed, end_time=end_time)
    return out


# ==========================================================================
# Bağlam kurulumu — zaman hizalama (look-ahead koruması)
# ==========================================================================


def _slice_upto(
    candles: List[Candle], close_times: List[int], cutoff: int, max_len: int
) -> List[Candle]:
    """close_time <= cutoff olan mumların SON `max_len` tanesi (eski→yeni)."""
    idx = bisect.bisect_right(close_times, cutoff)
    start = max(0, idx - max_len)
    return candles[start:idx]


def build_context(
    symbol: str,
    candles_5m: List[Candle],
    candles_15m: List[Candle],
    candles_4h: List[Candle],
    index: int,
    leverage: int,
    close_times_5m: Optional[List[int]] = None,
    close_times_15m: Optional[List[int]] = None,
    close_times_4h: Optional[List[int]] = None,
) -> StrategyContext:
    """candles_5m[index] "şu an" kabul edilerek, o âna KADAR KAPANMIŞ
    15m/4h/5m dilimlerinden StrategyContext kurar.

    close_times_* önceden hesaplanmışsa (simulate_symbol'ün sıcak döngüsü
    için) yeniden hesaplanmaz; verilmezse burada hesaplanır (testler için
    uygun).

    Geleceğe bakma YASAK: assert ile de doğrulanır — bu asla tetiklenmemeli,
    tetiklenirse sayfalama/dilimleme mantığında bir hata var demektir.
    """
    current = candles_5m[index]
    cutoff = current.close_time

    ct5 = close_times_5m if close_times_5m is not None else [c.close_time for c in candles_5m]
    ct15 = close_times_15m if close_times_15m is not None else [c.close_time for c in candles_15m]
    ct4h = close_times_4h if close_times_4h is not None else [c.close_time for c in candles_4h]

    slice_5m = _slice_upto(candles_5m, ct5, cutoff, _CTX_5M_WINDOW)
    slice_15m = _slice_upto(candles_15m, ct15, cutoff, _CTX_15M_WINDOW)
    slice_4h = _slice_upto(candles_4h, ct4h, cutoff, _CTX_4H_WINDOW)

    assert all(c.close_time <= cutoff for c in slice_5m), "5m dilimi geleceğe bakıyor (look-ahead)"
    assert all(c.close_time <= cutoff for c in slice_15m), "15m dilimi geleceğe bakıyor (look-ahead)"
    assert all(c.close_time <= cutoff for c in slice_4h), "4h dilimi geleceğe bakıyor (look-ahead)"

    regime = detect_regime(slice_4h)
    atr_val = atr(slice_5m, 14)

    return StrategyContext(
        symbol=symbol,
        regime=regime,
        candles_4h=slice_4h,
        candles_15m=slice_15m,
        candles_5m=slice_5m,
        current_price=current.close,
        atr_5m=atr_val,
        leverage=leverage,
    )


# ==========================================================================
# Pozisyon simülasyonu — giriş/boyutlama, intrabar çıkış motoru
# ==========================================================================


@dataclass
class OpenPosition:
    """Simülasyon sırasında bir açık pozisyonun taşıdığı durum."""

    strategy: str
    symbol: str
    direction: Direction
    stop_price: float          # yapısal, DEĞİŞMEZ ilk stop (SL leg fiyatı)
    entry_idx: int              # candles_5m üzerindeki dolum mumu indeksi
    entry_price: float          # kayma uygulanmış GERÇEK dolum fiyatı
    entry_time: int             # dolum mumunun open_time'ı (ms)
    qty_total: float
    leverage: int
    tp1_price: float
    tp2_price: float
    tp1_qty: float
    tp2_qty: float
    runner_qty: float
    breakeven_price: float
    current_stop: float
    tp1_filled: bool = False
    tp2_filled: bool = False
    trailing_active: bool = False
    remaining_qty: float = 0.0
    legs: List[Dict[str, Any]] = field(default_factory=list)
    mae_pct: float = 0.0
    mfe_pct: float = 0.0
    exit_reason: Optional[str] = None
    exit_price: Optional[float] = None
    exit_time: Optional[int] = None

    def __post_init__(self) -> None:
        self.remaining_qty = self.qty_total


@dataclass
class BacktestTrade:
    """Kapanmış bir backtest işleminin özeti (rapor/JSON için)."""

    strategy: str
    symbol: str
    direction: str
    entry_price: float
    entry_time: int
    exit_price: float
    exit_time: int
    quantity: float
    leverage: int
    margin_usdt: float
    pnl: float
    roi_pct: float
    exit_reason: str
    mae_pct: float
    mfe_pct: float
    duration_minutes: float
    exit_idx: int
    legs: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if k != "exit_idx"}
        return d


def open_position(
    signal: ScalpSignal,
    candles_5m: List[Candle],
    signal_idx: int,
    cfg: Any,
    balance: float = _DEFAULT_VIRTUAL_BALANCE,
) -> Optional[OpenPosition]:
    """Sinyalden pozisyon kurar: risk bazlı boyutlama + stop mesafesi kapısı
    + SONRAKİ mumun open'ında kaymalı dolum. Kapı geçilmezse None döner
    (işlem yok)."""
    entry_hint = signal.entry_price
    stop_price = signal.stop_price
    if entry_hint <= 0:
        return None

    stop_distance_pct = abs(entry_hint - stop_price) / entry_hint * 100.0
    if not (cfg.scalper_min_stop_pct <= stop_distance_pct <= cfg.scalper_max_stop_pct):
        return None

    price_distance = abs(entry_hint - stop_price)
    if price_distance <= 0:
        return None

    if signal_idx + 1 >= len(candles_5m):
        return None  # sonraki mum yok — dolum yapılamaz

    risk_amount = balance * (cfg.scalper_risk_percentage / 100.0) * signal.risk_multiplier
    qty = risk_amount / price_distance

    leverage = cfg.scalper_leverage
    nominal_cap = balance * leverage * 0.5
    nominal = qty * entry_hint
    if nominal > nominal_cap and entry_hint > 0:
        qty = nominal_cap / entry_hint

    if qty <= 0:
        return None

    entry_idx = signal_idx + 1
    entry_candle = candles_5m[entry_idx]
    if signal.direction == Direction.LONG:
        entry_price = entry_candle.open * (1.0 + _SLIPPAGE_RATE)
    else:
        entry_price = entry_candle.open * (1.0 - _SLIPPAGE_RATE)

    tp1_price = price_at_roi(entry_price, cfg.scalper_tp1_roi, leverage, signal.direction)
    tp2_price = price_at_roi(entry_price, cfg.scalper_tp2_roi, leverage, signal.direction)
    tp1_qty = qty * cfg.scalper_tp1_fraction
    tp2_qty = qty * cfg.scalper_tp2_fraction
    runner_qty = max(qty - tp1_qty - tp2_qty, 0.0)

    buffer_frac = cfg.scalper_breakeven_buffer_pct / 100.0
    breakeven_price = (
        entry_price * (1 + buffer_frac) if signal.direction == Direction.LONG
        else entry_price * (1 - buffer_frac)
    )

    return OpenPosition(
        strategy=signal.strategy,
        symbol=signal.symbol,
        direction=signal.direction,
        stop_price=stop_price,
        entry_idx=entry_idx,
        entry_price=entry_price,
        entry_time=entry_candle.open_time,
        qty_total=qty,
        leverage=leverage,
        tp1_price=tp1_price,
        tp2_price=tp2_price,
        tp1_qty=tp1_qty,
        tp2_qty=tp2_qty,
        runner_qty=runner_qty,
        breakeven_price=breakeven_price,
        current_stop=stop_price,
    )


def _is_hit(c: Candle, level: float, direction: Direction, is_stop: bool) -> bool:
    """LONG'da stop=düşüşle (low), hedef=yükselişle (high) değer; SHORT
    aynası."""
    if direction == Direction.LONG:
        return (c.low <= level) if is_stop else (c.high >= level)
    return (c.high >= level) if is_stop else (c.low <= level)


def _leg_pnl(pos: OpenPosition, qty: float, exit_price: float) -> float:
    if pos.direction == Direction.LONG:
        return (exit_price - pos.entry_price) * qty
    return (pos.entry_price - exit_price) * qty


def _fill_leg(pos: OpenPosition, qty: float, price: float, label: str) -> None:
    qty = min(qty, pos.remaining_qty)
    if qty <= 0:
        return
    gross = _leg_pnl(pos, qty, price)
    commission = _COMMISSION_RATE * qty * price
    pos.legs.append({"label": label, "quantity": qty, "price": price, "pnl": gross - commission})
    pos.remaining_qty -= qty


def _close_remaining(pos: OpenPosition, price: float, close_time: int, reason: str) -> None:
    if pos.remaining_qty > 1e-12:
        _fill_leg(pos, pos.remaining_qty, price, reason)
    pos.exit_reason = reason
    pos.exit_price = price
    pos.exit_time = close_time


def _process_candle_exits(pos: OpenPosition, c: Candle, direction: Direction) -> bool:
    """Bir mum için çıkış kurallarını uygular. Pozisyon TAMAMEN kapandıysa
    True döner.

    AŞAMA A (TP1 dolmadan): ilk stop VS TP1. Aynı mumda ikisi de değindiyse
    SL ÖNCE sayılır (kötümser) — TP1 kısmi dolumu OLMAZ, tüm miktar SL'de
    kapanır.
    AŞAMA B (TP1 dolduktan sonra — aynı mumda devam edebilir): current_stop
    (break-even veya sonrasında chandelier trail) TÜM kalan miktarı korur;
    TP2 henüz dolmadıysa bağımsız sabit hedef olarak aynı anda izlenir. Yine
    aynı mumda ikisi de değindiyse SL/trail ÖNCE sayılır.
    """
    if not pos.tp1_filled:
        sl_hit = _is_hit(c, pos.current_stop, direction, is_stop=True)
        tp1_hit = _is_hit(c, pos.tp1_price, direction, is_stop=False)

        if sl_hit:
            _close_remaining(pos, pos.current_stop, c.close_time, "SL")
            return True
        if tp1_hit:
            _fill_leg(pos, pos.tp1_qty, pos.tp1_price, "TP1")
            pos.tp1_filled = True
            pos.current_stop = pos.breakeven_price
            pos.trailing_active = True

    if pos.remaining_qty <= 1e-12:
        return True

    if pos.tp1_filled:
        sl_hit = _is_hit(c, pos.current_stop, direction, is_stop=True)
        tp2_hit = (not pos.tp2_filled) and _is_hit(c, pos.tp2_price, direction, is_stop=False)

        if sl_hit:
            _close_remaining(pos, pos.current_stop, c.close_time, "TRAIL")
            return True
        if tp2_hit:
            _fill_leg(pos, pos.tp2_qty, pos.tp2_price, "TP2")
            pos.tp2_filled = True

    return pos.remaining_qty <= 1e-12


def _update_trailing(pos: OpenPosition, candles_5m: List[Candle], idx: int, cfg: Any) -> None:
    """Runner'ın chandelier trail'ini bu muma kadarki veriyle günceller —
    YALNIZ lehte (never geriye kaymaz)."""
    window = candles_5m[: idx + 1]
    raw_stop = chandelier_stop(
        window,
        pos.direction,
        atr_mult=cfg.scalper_chandelier_atr_mult,
        atr_period=cfg.scalper_chandelier_atr_period,
        since_index=pos.entry_idx,
    )
    if raw_stop == 0.0:
        return  # indicators.chandelier_stop: yetersiz veri = "hesaplanamadı"

    if pos.direction == Direction.LONG:
        new_stop = max(pos.breakeven_price, raw_stop)
        if new_stop > pos.current_stop:
            pos.current_stop = new_stop
    else:
        new_stop = min(pos.breakeven_price, raw_stop)
        if new_stop < pos.current_stop:
            pos.current_stop = new_stop


def _update_mae_mfe(pos: OpenPosition, c: Candle, direction: Direction, leverage: int) -> None:
    entry = pos.entry_price
    if entry <= 0:
        return
    if direction == Direction.LONG:
        best_price, worst_price = c.high, c.low
    else:
        best_price, worst_price = c.low, c.high

    best_delta = (best_price - entry) / entry * 100.0
    worst_delta = (worst_price - entry) / entry * 100.0
    if direction == Direction.SHORT:
        best_delta, worst_delta = -best_delta, -worst_delta

    pos.mfe_pct = max(pos.mfe_pct, best_delta * leverage)
    pos.mae_pct = min(pos.mae_pct, worst_delta * leverage)


def _finalize_trade(pos: OpenPosition, exit_idx: int, candles_5m: List[Candle]) -> BacktestTrade:
    if pos.exit_reason is None:
        # remaining_qty TP1+TP2 bacaklarıyla tamamen tükendi — SL/TRAIL/EOD
        # hiç tetiklenmedi.
        last_leg = pos.legs[-1] if pos.legs else None
        pos.exit_reason = "TP_LADDER"
        pos.exit_price = last_leg["price"] if last_leg else pos.entry_price
        pos.exit_time = candles_5m[exit_idx].close_time

    total_pnl = sum(leg["pnl"] for leg in pos.legs)
    entry_commission = _COMMISSION_RATE * pos.qty_total * pos.entry_price
    total_pnl -= entry_commission

    margin = (pos.qty_total * pos.entry_price / pos.leverage) if pos.leverage else pos.qty_total * pos.entry_price
    roi_pct = (total_pnl / margin * 100.0) if margin > 0 else 0.0

    duration_minutes = max(0.0, (pos.exit_time - pos.entry_time) / 60_000.0)

    return BacktestTrade(
        strategy=pos.strategy,
        symbol=pos.symbol,
        direction=pos.direction.value,
        entry_price=pos.entry_price,
        entry_time=pos.entry_time,
        exit_price=pos.exit_price,
        exit_time=pos.exit_time,
        quantity=pos.qty_total,
        leverage=pos.leverage,
        margin_usdt=margin,
        pnl=total_pnl,
        roi_pct=roi_pct,
        exit_reason=pos.exit_reason,
        mae_pct=pos.mae_pct,
        mfe_pct=pos.mfe_pct,
        duration_minutes=duration_minutes,
        exit_idx=exit_idx,
        legs=pos.legs,
    )


def manage_position(pos: OpenPosition, candles_5m: List[Candle], cfg: Any) -> BacktestTrade:
    """Pozisyonu entry_idx'ten itibaren, kapanana ya da veri bitene (EOD)
    kadar mum mum yönetir."""
    n = len(candles_5m)
    exit_idx = n - 1

    for idx in range(pos.entry_idx, n):
        c = candles_5m[idx]
        _update_mae_mfe(pos, c, pos.direction, pos.leverage)

        closed = _process_candle_exits(pos, c, pos.direction)
        if closed:
            exit_idx = idx
            break

        if pos.trailing_active:
            _update_trailing(pos, candles_5m, idx, cfg)
    else:
        exit_idx = n - 1
        last = candles_5m[-1]
        _close_remaining(pos, last.close, last.close_time, "EOD")

    return _finalize_trade(pos, exit_idx, candles_5m)


def simulate_symbol(
    symbol: str,
    candles_5m: List[Candle],
    candles_15m: List[Candle],
    candles_4h: List[Candle],
    strategies: List[StrategyProtocol],
    cfg: Any,
    initial_balance: float = _DEFAULT_VIRTUAL_BALANCE,
) -> List[BacktestTrade]:
    """Bir sembol için TEK eşzamanlı pozisyonla tam backtest simülasyonu.
    AĞ YOK — yalnız zaten çekilmiş mum listeleri üzerinde çalışır (saf,
    test edilebilir)."""
    trades: List[BacktestTrade] = []
    n5 = len(candles_5m)
    if n5 < 2:
        return trades

    close_times_5m = [c.close_time for c in candles_5m]
    close_times_15m = [c.close_time for c in candles_15m]
    close_times_4h = [c.close_time for c in candles_4h]

    leverage = cfg.scalper_leverage
    i = 0
    while i < n5:
        if i + 1 >= n5:
            break  # dolum yapılacak sonraki mum yok

        ctx = build_context(
            symbol, candles_5m, candles_15m, candles_4h, i, leverage,
            close_times_5m, close_times_15m, close_times_4h,
        )

        signal: Optional[ScalpSignal] = None
        for strat in strategies:
            sig = strat.evaluate(ctx)
            if sig is not None:
                signal = sig
                break

        if signal is None:
            i += 1
            continue

        pos = open_position(signal, candles_5m, i, cfg, initial_balance)
        if pos is None:
            i += 1
            continue

        trade = manage_position(pos, candles_5m, cfg)
        trades.append(trade)
        i = trade.exit_idx + 1

    return trades


# ==========================================================================
# İstatistik / rapor
# ==========================================================================


def compute_stats(trades: List[BacktestTrade]) -> Dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {
            "trades": 0, "wins": 0, "winrate": 0.0, "total_pnl": 0.0, "avg_roi": 0.0,
            "profit_factor": 0.0, "max_consec_losses": 0, "max_drawdown": 0.0,
            "avg_duration_min": 0.0, "avg_mae": 0.0, "avg_mfe": 0.0,
        }

    ordered = sorted(trades, key=lambda t: t.exit_time)
    wins = [t for t in ordered if t.pnl > 0]
    losses = [t for t in ordered if t.pnl < 0]
    total_pnl = sum(t.pnl for t in ordered)
    avg_roi = sum(t.roi_pct for t in ordered) / n
    gross_profit = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    else:
        profit_factor = float("inf") if gross_profit > 0 else 0.0

    max_consec = 0
    cur = 0
    for t in ordered:
        if t.pnl < 0:
            cur += 1
            max_consec = max(max_consec, cur)
        else:
            cur = 0

    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in ordered:
        cum += t.pnl
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    return {
        "trades": n,
        "wins": len(wins),
        "winrate": len(wins) / n * 100.0,
        "total_pnl": total_pnl,
        "avg_roi": avg_roi,
        "profit_factor": profit_factor,
        "max_consec_losses": max_consec,
        "max_drawdown": max_dd,
        "avg_duration_min": sum(t.duration_minutes for t in ordered) / n,
        "avg_mae": sum(t.mae_pct for t in ordered) / n,
        "avg_mfe": sum(t.mfe_pct for t in ordered) / n,
    }


def _group_by_strategy(trades: List[BacktestTrade]) -> Dict[str, List[BacktestTrade]]:
    out: Dict[str, List[BacktestTrade]] = {}
    for t in trades:
        out.setdefault(t.strategy, []).append(t)
    return out


def _fmt_pf(pf: float) -> str:
    return "inf" if pf == float("inf") else f"{pf:.2f}"


def print_report(all_trades: List[BacktestTrade]) -> None:
    by_strategy = _group_by_strategy(all_trades)
    overall = compute_stats(all_trades)

    cols = [
        ("Strateji", 8), ("İşlem", 6), ("Kazanma%", 9), ("Toplam PnL", 12),
        ("Ort ROI%", 9), ("P.Faktör", 9), ("Mks Ardş.Kyp", 13), ("Mks DD", 10),
        ("Ort Süre(dk)", 13), ("Ort MAE%", 9), ("Ort MFE%", 9),
    ]
    header = " | ".join(name.ljust(w) for name, w in cols)
    print("\n" + "=" * len(header))
    print("SCALPER BACKTEST RAPORU — Strateji Karşılaştırması")
    print("=" * len(header))
    print(header)
    print("-" * len(header))

    def _row(label: str, s: Dict[str, Any]) -> str:
        values = [
            label,
            str(s["trades"]),
            f"{s['winrate']:.1f}",
            f"{s['total_pnl']:.2f}",
            f"{s['avg_roi']:.2f}",
            _fmt_pf(s["profit_factor"]),
            str(s["max_consec_losses"]),
            f"{s['max_drawdown']:.2f}",
            f"{s['avg_duration_min']:.1f}",
            f"{s['avg_mae']:.2f}",
            f"{s['avg_mfe']:.2f}",
        ]
        return " | ".join(v.ljust(w) for v, (_, w) in zip(values, cols))

    for strategy_name in sorted(by_strategy.keys()):
        print(_row(strategy_name, compute_stats(by_strategy[strategy_name])))

    print("-" * len(header))
    print(_row("TOPLAM", overall))
    print("=" * len(header) + "\n")


def write_json_report(
    all_trades: List[BacktestTrade], days: int, symbols: List[str], strategy_names: str
) -> str:
    by_strategy = _group_by_strategy(all_trades)
    payload = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "params": {"days": days, "symbols": symbols, "strategies": strategy_names},
        "overall": compute_stats(all_trades),
        "by_strategy": {name: compute_stats(ts) for name, ts in by_strategy.items()},
        "trades": [t.to_dict() for t in all_trades],
    }

    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    filename = f"backtest_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    path = logs_dir / filename
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)

    app_logger.info(f"📄 Backtest raporu yazıldı: {path}")
    return str(path)


# ==========================================================================
# Üst seviye koşum + CLI
# ==========================================================================


async def run_backtest(
    days: int,
    symbols: List[str],
    strategy_names: str,
    base_url: str = "https://fapi.binance.com",
    cfg: Any = settings,
) -> List[BacktestTrade]:
    """Verilen semboller için tarihsel veriyi çeker ve tüm stratejileri
    simüle eder; tüm işlemleri (tüm semboller birleşik) döndürür."""
    strategies = get_enabled(strategy_names)
    if not strategies:
        raise ValueError(f"Geçerli scalper stratejisi bulunamadı: '{strategy_names}'")

    fetcher = KlineFetcher(base_url=base_url)
    throttled = _ThrottledFetch(fetcher.get_klines)

    all_trades: List[BacktestTrade] = []
    try:
        for symbol in symbols:
            app_logger.info(f"📥 {symbol}: tarihsel veri çekiliyor ({days} gün)...")
            data = await gather_symbol_data(throttled, symbol, days)
            candles_5m, candles_15m, candles_4h = data["5m"], data["15m"], data["4h"]
            app_logger.info(
                f"📊 {symbol}: 5m={len(candles_5m)} 15m={len(candles_15m)} 4h={len(candles_4h)} mum toplandı"
            )
            trades = simulate_symbol(symbol, candles_5m, candles_15m, candles_4h, strategies, cfg)
            app_logger.info(f"✅ {symbol}: {len(trades)} işlem simüle edildi")
            all_trades.extend(trades)
    finally:
        await fetcher.close()

    return all_trades


async def _resolve_symbols(symbols_arg: str) -> List[str]:
    if symbols_arg.strip().lower() == "auto":
        scanner = UniverseScanner(base_url="https://fapi.binance.com", top_n=_AUTO_UNIVERSE_TOP_N)
        try:
            universe = await scanner.get_universe()
        finally:
            await scanner.close()
        app_logger.info(f"🌐 Otomatik evren (mainnet, ilk {_AUTO_UNIVERSE_TOP_N}): {universe}")
        return universe
    return [s.strip().upper() for s in symbols_arg.split(",") if s.strip()]


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scalper (A/B/C/...) stratejileri için tarihsel backtest ve karşılaştırma raporu."
    )
    parser.add_argument("--days", type=int, default=30, help="Kaç günlük tarihsel veri (varsayılan 30)")
    parser.add_argument(
        "--symbols", type=str, default="BTCUSDT,ETHUSDT,SOLUSDT",
        help="Virgülle ayrılmış sembol listesi, veya 'auto' (UniverseScanner mainnet ilk 8)",
    )
    parser.add_argument(
        "--strategies", type=str, default="A,B,C",
        help="Virgülle ayrılmış strateji adları (setups.get_enabled ile dinamik yüklenir)",
    )
    return parser


async def main_async(args: argparse.Namespace) -> None:
    symbols = await _resolve_symbols(args.symbols)
    app_logger.info(
        f"🚀 Scalper backtest başlıyor: gün={args.days} sembol={symbols} strateji={args.strategies}"
    )

    all_trades = await run_backtest(days=args.days, symbols=symbols, strategy_names=args.strategies)

    print_report(all_trades)
    # NOT: src/core/logger.py loguru için sys.stdout.buffer'ı SARAN ayrı bir
    # TextIOWrapper kurar; süreç çıkışında bu iki wrapper'ın kapanma sırası
    # bazen print() çıktısının OS'e hiç flush edilmeden kaybolmasına yol
    # açabiliyor. logger.py'ye dokunmadan, kendi çıktımızı garantiye almak
    # için burada açıkça flush ediyoruz.
    sys.stdout.flush()
    write_json_report(all_trades, args.days, symbols, args.strategies)


def main() -> None:
    parser = _build_arg_parser()
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
