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
    # Sabit tarih penceresi (UTC, [start,end)) — --days'i geçersiz kılar,
    # rejim bazlı (BEAR/BULL/FLAT) karşılaştırma için:
    python -m src.strategies.scalper.backtest \\
        --start 2026-01-23 --end 2026-02-13 --symbols BTCUSDT --strategies C
"""

from __future__ import annotations

import argparse
import asyncio
import bisect
import json
import math
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from src.core.config import settings
from src.core.logger import app_logger
from src.strategies.scalper.data import KlineFetcher
from src.strategies.scalper.indicators import atr, chandelier_stop
from src.strategies.scalper.regime import detect_regime
from src.strategies.scalper.scanner import UniverseScanner
from src.strategies.scalper.setups import apply_stop_policy, get_enabled
from src.strategies.scalper.types import (
    Candle,
    Direction,
    ScalpSignal,
    StrategyContext,
    StrategyProtocol,
    price_at_roi,
    resolve_trail_mult,
)

# --------------------------------------------------------------------------
# Sabitler
# --------------------------------------------------------------------------

# Kline çekme: aralığa göre günlük mum sayısı ve sayfalama arası bekleme.
_CANDLES_PER_DAY: Dict[str, int] = {
    "1m": 1440, "3m": 480, "5m": 288, "15m": 96, "30m": 48, "1h": 24, "4h": 6,
}
_REQUEST_DELAY_SECONDS = 0.3
_MAX_PAGE_LIMIT = 1500  # Binance futures klines tek istekte azami mum sayısı

# StrategyContext pencere boyutları (tasarım dokümanıyla birebir).
_CTX_5M_WINDOW = 150
_CTX_15M_WINDOW = 100
_CTX_4H_WINDOW = 250

# Sonuç penceresinden ÖNCE çekilen sabit bağlam. Bu mumlar yalnız indikatör
# ve rejim seed'i içindir; test metriğine/sinyal üretim penceresine girmez.
# Değerler canlı motorun her turda istediği bağlamla birebirdir.
BACKTEST_WARMUP_CANDLES: Dict[str, int] = {
    "5m": _CTX_5M_WINDOW,
    "15m": _CTX_15M_WINDOW,
    "4h": _CTX_4H_WINDOW,
}

_MILLISECONDS_PER_DAY = 86_400_000

# Maliyet modeli.
# NOT: komisyon oranları artık settings'ten okunur (scalper_taker_fee_pct /
# scalper_maker_fee_pct, % biriminde — /100 ile orana çevrilir). Kayma yalnız
# taker girişte uygulanır ve ayrı bir settings alanı YOK — sabit kalır.
_SLIPPAGE_RATE = 0.0002    # %0,02, yalnız taker girişte, aleyhte

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

        raw_batch = await fetch(symbol, interval, limit, cursor_end)
        if not raw_batch:
            break  # Borsa bu sembol/aralık için daha eski veri döndürmedi.

        # Binance `endTime` seçiminde kline open_time'ını kullanabilir; tam
        # tarihsel sınırda açılan ama sınırdan SONRA kapanan mum bu nedenle
        # yanıta girebilir. Backtest yalnız o anda kapanmış mumları görmeli.
        batch = [candle for candle in raw_batch if candle.close_time <= cursor_end]
        if not batch:
            # Yalnız sınır-sonrası mum geldiyse bir önceki open_time'ın
            # gerisine ilerle; aksi halde aynı sayfayı sonsuza dek isteme.
            next_cursor = min(candle.open_time for candle in raw_batch) - 1
            if next_cursor >= cursor_end:
                break
            cursor_end = next_cursor
            continue

        collected = batch + collected
        cursor_end = batch[0].open_time - 1

    if len(collected) > total_needed:
        collected = collected[-total_needed:]

    return collected


def resolve_timeframes(cfg: Any) -> Tuple[str, str, str]:
    """(entry, context, regime) zaman dilimi üçlüsünü cfg'den çöz.

    Varsayılan 5m/15m/4h tarihsel davranışla birebir; canlı motor da aynı
    ayarları okur (engine._evaluate_symbol) — parite korunur.
    """
    return (
        str(getattr(cfg, "scalper_tf_entry", "5m") or "5m"),
        str(getattr(cfg, "scalper_tf_context", "15m") or "15m"),
        str(getattr(cfg, "scalper_tf_regime", "4h") or "4h"),
    )


# Rol → warm-up penceresi (entry/context/regime, canlı bağlam boylarıyla aynı).
_WARMUP_BY_ROLE: Tuple[int, int, int] = (
    _CTX_5M_WINDOW, _CTX_15M_WINDOW, _CTX_4H_WINDOW
)


async def gather_symbol_data(
    fetch: FetchFn, symbol: str, days: int, end_time: Optional[int] = None,
    timeframes: Tuple[str, str, str] = ("5m", "15m", "4h"),
) -> Dict[str, List[Candle]]:
    """Bir sembol için test penceresi + sabit warm-up mumlarını toplar.

    Warm-up mumları yalnız bağlam/indikatör seed'i içindir. `run_backtest`,
    `simulate_symbol(..., test_start_time_ms=...)` ile sinyal üretimini istenen
    `days` penceresine sınırlar; böylece ilk günler eksik EMA/RSI ile ölçülmez.

    `timeframes` = (entry, context, regime); desteklenmeyen aralıkta açık
    ValueError (sessiz yanlış veri yerine).
    """
    out: Dict[str, List[Candle]] = {}
    for interval, warmup in zip(timeframes, _WARMUP_BY_ROLE):
        per_day = _CANDLES_PER_DAY.get(interval)
        if per_day is None:
            raise ValueError(
                f"Desteklenmeyen zaman dilimi: {interval!r} "
                f"(bilinenler: {sorted(_CANDLES_PER_DAY)})"
            )
        needed = days * per_day + warmup
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
    entry_price: float          # kayma uygulanmış (taker) ya da limit (maker) GERÇEK dolum fiyatı
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
    regime: str                 # giriş anındaki ctx.regime (sinyal üretim anı) — rapor kırılımı için
    entry_commission_rate: float  # giriş bacağı oranı (taker ya da maker fee)
    exit_commission_rate: float   # çıkış bacakları oranı — HER modda taker fee
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
    regime: str = "UNKNOWN"
    legs: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if k != "exit_idx"}
        return d


def _find_maker_fill(
    signal: ScalpSignal, candles_5m: List[Candle], signal_idx: int, cfg: Any,
) -> Optional[tuple]:
    """Maker giriş modu: LIMIT fiyatı = sinyal mumunun kapanışı. Sonraki
    `scalper_maker_fill_timeout_candles` mum içinde fiyat limite değerse
    (LONG: mum.low <= limit; SHORT: mum.high >= limit) ilk değen mumda,
    kaymasız, limit fiyatından dolum sayılır.

    Döner: (entry_idx, entry_price) ya da timeout içinde hiç değmezse None."""
    limit_price = candles_5m[signal_idx].close
    timeout = max(0, int(cfg.scalper_maker_fill_timeout_candles))
    n = len(candles_5m)

    for offset in range(1, timeout + 1):
        idx = signal_idx + offset
        if idx >= n:
            break
        c = candles_5m[idx]
        touched = (
            c.low <= limit_price if signal.direction == Direction.LONG
            else c.high >= limit_price
        )
        if touched:
            return idx, limit_price

    return None


def open_position(
    signal: ScalpSignal,
    candles_5m: List[Candle],
    signal_idx: int,
    cfg: Any,
    balance: float = _DEFAULT_VIRTUAL_BALANCE,
    missed_counter: Optional[Dict[str, int]] = None,
) -> Optional[OpenPosition]:
    """Sinyalden pozisyon kurar: risk bazlı boyutlama + stop mesafesi kapısı
    + dolum simülasyonu. Kapı geçilmezse None döner (işlem yok).

    Dolum simülasyonu `cfg.scalper_entry_mode`'a göre değişir:
    - "taker" (varsayılan): SONRAKİ mumun open'ında kaymalı (aleyhte) dolum,
      komisyon cfg.scalper_taker_fee_pct.
    - "maker": LIMIT emri simülasyonu (bkz. _find_maker_fill) — timeout
      içinde dolmazsa sinyal SESSİZCE iptal edilir; `missed_counter`
      verilmişse (mutasyonla) sayaç artırılır, işlem raporlanabilsin diye.
    """
    entry_hint = signal.entry_price
    stop_price = signal.stop_price
    if entry_hint <= 0:
        return None

    stop_distance_pct = abs(entry_hint - stop_price) / entry_hint * 100.0
    if not (cfg.scalper_min_stop_pct <= stop_distance_pct <= cfg.scalper_max_stop_pct):
        return None

    # Canlı executor ile aynı minimum R:R kapısı. Beklenen runner getirisi
    # muhafazakâr biçimde TP1'e eşit varsayılır; eşik <= 0 ise kapı kapalıdır.
    min_rr = float(getattr(cfg, "scalper_min_rr", 0.0) or 0.0)
    if min_rr > 0.0:
        tp1_frac = cfg.scalper_tp1_fraction
        tp2_frac = cfg.scalper_tp2_fraction
        runner_frac = max(0.0, 1.0 - tp1_frac - tp2_frac)
        expected_roi = (
            cfg.scalper_tp1_roi * tp1_frac
            + cfg.scalper_tp2_roi * tp2_frac
            + cfg.scalper_tp1_roi * runner_frac
        )
        sl_risk_roi = stop_distance_pct * (
            int(getattr(signal, "leverage", None) or 0) or cfg.scalper_leverage
        )
        rr = expected_roi / sl_risk_roi if sl_risk_roi > 0.0 else 0.0
        if rr < min_rr:
            if missed_counter is not None:
                missed_counter["min_rr_rejected"] = (
                    missed_counter.get("min_rr_rejected", 0) + 1
                )
            return None

    price_distance = abs(entry_hint - stop_price)
    if price_distance <= 0:
        return None

    entry_mode = getattr(cfg, "scalper_entry_mode", "taker")

    if entry_mode == "maker":
        fill = _find_maker_fill(signal, candles_5m, signal_idx, cfg)
        if fill is None:
            if missed_counter is not None:
                missed_counter["maker_missed"] = missed_counter.get("maker_missed", 0) + 1
            return None
        entry_idx, entry_price = fill
        entry_commission_rate = cfg.scalper_maker_fee_pct / 100.0
    else:
        if signal_idx + 1 >= len(candles_5m):
            return None  # sonraki mum yok — dolum yapılamaz
        entry_idx = signal_idx + 1
        entry_candle = candles_5m[entry_idx]
        if signal.direction == Direction.LONG:
            entry_price = entry_candle.open * (1.0 + _SLIPPAGE_RATE)
        else:
            entry_price = entry_candle.open * (1.0 - _SLIPPAGE_RATE)
        entry_commission_rate = cfg.scalper_taker_fee_pct / 100.0

    exit_commission_rate = cfg.scalper_taker_fee_pct / 100.0  # çıkışlar her modda taker

    risk_amount = balance * (cfg.scalper_risk_percentage / 100.0) * signal.risk_multiplier
    qty = risk_amount / price_distance

    # Canlı parite: sinyalin coin-bazlı dinamik kaldıracı öncelikli.
    leverage = int(getattr(signal, "leverage", None) or 0) or cfg.scalper_leverage
    margin_pct = getattr(cfg, "scalper_max_margin_pct", 50.0) / 100.0
    nominal_cap = balance * leverage * margin_pct
    nominal = qty * entry_hint
    if nominal > nominal_cap and entry_hint > 0:
        qty = nominal_cap / entry_hint

    if qty <= 0:
        return None

    entry_candle = candles_5m[entry_idx]

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
        regime=signal.regime.value if hasattr(signal.regime, "value") else str(signal.regime),
        entry_commission_rate=entry_commission_rate,
        exit_commission_rate=exit_commission_rate,
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
    commission = pos.exit_commission_rate * qty * price
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
        # Canlı exits._update_trailing ile parite: tepe ROI kademesi.
        atr_mult=resolve_trail_mult(cfg, pos.mfe_pct),
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
    entry_commission = pos.entry_commission_rate * pos.qty_total * pos.entry_price
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
        regime=pos.regime,
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
    missed_counter: Optional[Dict[str, int]] = None,
    test_start_time_ms: Optional[int] = None,
) -> List[BacktestTrade]:
    """Bir sembol için TEK eşzamanlı pozisyonla tam backtest simülasyonu.
    AĞ YOK — yalnız zaten çekilmiş mum listeleri üzerinde çalışır (saf,
    test edilebilir).

    `test_start_time_ms` verilirse daha eski mumlar yalnız warm-up bağlamı
    olarak kullanılır; o zamandan önce sinyal/işlem üretilmez ve dolayısıyla
    sonuç metriklerine dahil edilmez.

    cfg.scalper_regime_filter (varsayılan True) rejime ters sinyalleri
    engeller (DOWN'da LONG, UP'ta SHORT) — canlı motorla (engine.py) birebir
    parite. Engellenenler `missed_counter["regime_gate"]` altında sayılır.
    """
    trades: List[BacktestTrade] = []
    n5 = len(candles_5m)
    if n5 < 2:
        return trades

    close_times_5m = [c.close_time for c in candles_5m]
    close_times_15m = [c.close_time for c in candles_15m]
    close_times_4h = [c.close_time for c in candles_4h]

    leverage = cfg.scalper_leverage
    # Canlı parite: ortak stop politikası + kayıp sonrası sembol cooldown'u
    # (engine.apply_stop_policy + executor.start_loss_cooldown karşılığı).
    loss_cooldown_ms = int(
        float(getattr(cfg, "scalper_loss_cooldown_minutes", 0) or 0) * 60_000
    )
    cooldown_until_ms = 0
    i = (
        bisect.bisect_left(close_times_5m, test_start_time_ms)
        if test_start_time_ms is not None
        else 0
    )
    while i < n5:
        if i + 1 >= n5:
            break  # dolum yapılacak sonraki mum yok

        if loss_cooldown_ms > 0 and close_times_5m[i] < cooldown_until_ms:
            i += 1
            continue

        ctx = build_context(
            symbol, candles_5m, candles_15m, candles_4h, i, leverage,
            close_times_5m, close_times_15m, close_times_4h,
        )

        raw_signal: Optional[ScalpSignal] = None
        for strat in strategies:
            sig = strat.evaluate(ctx)
            if sig is not None:
                raw_signal = sig
                break

        if raw_signal is None:
            i += 1
            continue

        # Canlı parite: rejim kapısı (engine._evaluate_symbol ile birebir) —
        # DOWN rejimde LONG / UP rejimde SHORT engellenir. Bu kapı daha önce
        # yalnız CANLI motordaydı; backtest'te YOKTU (2026-08-21 tespiti) —
        # yani geçmiş backtest sonuçları rejime-ters işlemleri de içeriyordu.
        # TV dış sinyali backtest'te simüle edilmediği için
        # scalper_tv_regime_filter ayrımı burada anlamsız — tüm sinyaller
        # "iç" (scalper_regime_filter tek başına yeterli).
        if bool(getattr(cfg, "scalper_regime_filter", True)):
            rejim = getattr(ctx.regime, "value", str(ctx.regime))
            yon = getattr(raw_signal.direction, "value", str(raw_signal.direction))
            if (rejim == "DOWN" and yon == "LONG") or (rejim == "UP" and yon == "SHORT"):
                if missed_counter is not None:
                    missed_counter["regime_gate"] = missed_counter.get("regime_gate", 0) + 1
                i += 1
                continue

        signal = apply_stop_policy(raw_signal, cfg)

        pos = open_position(signal, candles_5m, i, cfg, initial_balance, missed_counter=missed_counter)
        if pos is None:
            i += 1
            continue

        trade = manage_position(pos, candles_5m, cfg)
        trades.append(trade)
        if loss_cooldown_ms > 0 and (trade.exit_reason == "SL" or trade.pnl < 0.0):
            cooldown_until_ms = trade.exit_time + loss_cooldown_ms
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


def _group_by_strategy_regime(trades: List[BacktestTrade]) -> Dict[str, Dict[str, List[BacktestTrade]]]:
    """(strateji, rejim) -> işlem listesi; strateji üstünde nested rejim
    sözlüğü olarak — hem konsol hem JSON kırılım raporu için ortak."""
    out: Dict[str, Dict[str, List[BacktestTrade]]] = {}
    for t in trades:
        out.setdefault(t.strategy, {}).setdefault(t.regime, []).append(t)
    return out


def _group_by_strategy_direction(trades: List[BacktestTrade]) -> Dict[str, Dict[str, List[BacktestTrade]]]:
    """(strateji, yön[LONG/SHORT]) -> işlem listesi — rejim kırılımıyla aynı
    şekil, rejim penceresi analizinde (BEAR/BULL/FLAT) yön dağılımını görmek
    için."""
    out: Dict[str, Dict[str, List[BacktestTrade]]] = {}
    for t in trades:
        out.setdefault(t.strategy, {}).setdefault(t.direction, []).append(t)
    return out


def _group_by_strategy_exit(trades: List[BacktestTrade]) -> Dict[str, Dict[str, List[BacktestTrade]]]:
    """(strateji, çıkış nedeni[SL/TP_LADDER/TRAIL/...]) -> işlem listesi."""
    out: Dict[str, Dict[str, List[BacktestTrade]]] = {}
    for t in trades:
        out.setdefault(t.strategy, {}).setdefault(t.exit_reason, []).append(t)
    return out


def _fmt_pf(pf: float) -> str:
    return "inf" if pf == float("inf") else f"{pf:.2f}"


def print_report(
    all_trades: List[BacktestTrade], missed_counter: Optional[Dict[str, int]] = None,
) -> None:
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
    print("=" * len(header))

    if missed_counter is not None:
        missed_total = sum(missed_counter.values())
        print(f"Kapılarda reddedilen/kaçan sinyal: {missed_total} {dict(missed_counter)}")

    print()
    _print_regime_breakdown(all_trades)
    _print_grouped_breakdown(
        "YÖN KIRILIMI (LONG/SHORT)", "Yön", _group_by_strategy_direction(all_trades),
    )
    _print_grouped_breakdown(
        "ÇIKIŞ NEDENİ KIRILIMI", "Çıkış", _group_by_strategy_exit(all_trades),
    )


def _print_regime_breakdown(all_trades: List[BacktestTrade]) -> None:
    """İkinci, kompakt tablo: her (strateji, rejim) çifti için işlem sayısı,
    kazanma%, toplam PnL, P.Faktör."""
    grouped = _group_by_strategy_regime(all_trades)

    cols = [
        ("Strateji", 8), ("Rejim", 8), ("İşlem", 6), ("Kazanma%", 9),
        ("Toplam PnL", 12), ("P.Faktör", 9),
    ]
    header = " | ".join(name.ljust(w) for name, w in cols)
    print("=" * len(header))
    print("REJİM KIRILIMI")
    print("=" * len(header))
    print(header)
    print("-" * len(header))

    if not grouped:
        print("(işlem yok)")
    else:
        for strategy_name in sorted(grouped.keys()):
            for regime_name in sorted(grouped[strategy_name].keys()):
                s = compute_stats(grouped[strategy_name][regime_name])
                values = [
                    strategy_name, regime_name, str(s["trades"]),
                    f"{s['winrate']:.1f}", f"{s['total_pnl']:.2f}", _fmt_pf(s["profit_factor"]),
                ]
                print(" | ".join(v.ljust(w) for v, (_, w) in zip(values, cols)))

    print("=" * len(header) + "\n")


def _print_grouped_breakdown(
    title: str, group_col_label: str, grouped: Dict[str, Dict[str, List[BacktestTrade]]],
) -> None:
    """`_print_regime_breakdown` ile aynı biçimde, genel amaçlı kırılım
    tablosu — yön (LONG/SHORT) ve çıkış nedeni (SL/TP_LADDER/TRAIL/...)
    raporları bunu paylaşır (rejim penceresi analizinde işe yarar)."""
    cols = [
        ("Strateji", 8), (group_col_label, 12), ("İşlem", 6), ("Kazanma%", 9),
        ("Toplam PnL", 12), ("P.Faktör", 9),
    ]
    header = " | ".join(name.ljust(w) for name, w in cols)
    print("=" * len(header))
    print(title)
    print("=" * len(header))
    print(header)
    print("-" * len(header))

    if not grouped:
        print("(işlem yok)")
    else:
        for strategy_name in sorted(grouped.keys()):
            for group_name in sorted(str(k) for k in grouped[strategy_name].keys()):
                s = compute_stats(grouped[strategy_name][group_name])
                values = [
                    strategy_name, group_name, str(s["trades"]),
                    f"{s['winrate']:.1f}", f"{s['total_pnl']:.2f}", _fmt_pf(s["profit_factor"]),
                ]
                print(" | ".join(v.ljust(w) for v, (_, w) in zip(values, cols)))

    print("=" * len(header) + "\n")


def _utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _ms_to_utc_iso(timestamp_ms: int) -> str:
    return (
        datetime.fromtimestamp(timestamp_ms / 1000.0, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _git_provenance() -> Dict[str, Any]:
    """Raporun üretildiği checkout'un commit ve dirty durumunu döndürür.

    Git bulunamazsa rapor yine yazılır; bilinmeyen alanlar None olur. Shell
    kullanılmaz ve komutlar repo köküne sabitlenir.
    """
    repo_root = Path(__file__).resolve().parents[3]
    try:
        sha_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
        return {
            "git_sha": sha_result.stdout.strip() or None,
            "git_dirty": bool(status_result.stdout.strip()),
        }
    except (OSError, subprocess.SubprocessError):
        return {"git_sha": None, "git_dirty": None}


def scalper_config_snapshot(cfg: Any) -> Dict[str, Any]:
    """`cfg` içindeki tüm `scalper_` ayarlarının JSON-uyumlu görüntüsü.

    Pydantic Settings ve testlerdeki dataclass/sade nesneler desteklenir.
    Prefix filtresi sayesinde API anahtarı gibi alakasız/gizli ayarlar rapora
    yanlışlıkla girmez.
    """
    if hasattr(cfg, "model_dump"):
        raw = cfg.model_dump()
    else:
        raw = vars(cfg)
    return {
        name: raw[name]
        for name in sorted(raw)
        if name.startswith("scalper_")
    }


def _candle_window_snapshot(
    candles: List[Candle], test_start_time_ms: int,
) -> Dict[str, Any]:
    if not candles:
        return {
            "candles_fetched": 0,
            "candles_in_test_window": 0,
            "fetched_start_ms": None,
            "fetched_start_utc": None,
            "fetched_end_ms": None,
            "fetched_end_utc": None,
        }
    return {
        "candles_fetched": len(candles),
        "candles_in_test_window": sum(
            1 for candle in candles if candle.close_time >= test_start_time_ms
        ),
        "fetched_start_ms": candles[0].open_time,
        "fetched_start_utc": _ms_to_utc_iso(candles[0].open_time),
        "fetched_end_ms": candles[-1].close_time,
        "fetched_end_utc": _ms_to_utc_iso(candles[-1].close_time),
    }


def write_json_report(
    all_trades: List[BacktestTrade],
    days: int,
    symbols: List[str],
    strategy_names: str,
    missed_counter: Optional[Dict[str, int]] = None,
    cfg: Any = settings,
    run_metadata: Optional[Dict[str, Any]] = None,
    output_dir: str | Path = "logs",
) -> str:
    by_strategy = _group_by_strategy(all_trades)
    by_strategy_regime = _group_by_strategy_regime(all_trades)
    regime_breakdown = {
        strategy_name: {
            regime_name: compute_stats(ts) for regime_name, ts in regimes.items()
        }
        for strategy_name, regimes in by_strategy_regime.items()
    }

    metadata = run_metadata or {}
    git_state = _git_provenance()
    provenance = {
        "git_sha": metadata.get("git_sha", git_state["git_sha"]),
        "git_dirty": metadata.get("git_dirty", git_state["git_dirty"]),
        "scalper_config": metadata.get(
            "scalper_config", scalper_config_snapshot(cfg)
        ),
        "test_window": metadata.get(
            "test_window", {"requested_days": days, "start_ms": None, "end_ms": None}
        ),
        "warmup_candles": metadata.get(
            "warmup_candles", dict(BACKTEST_WARMUP_CANDLES)
        ),
        "universe_snapshot": metadata.get(
            "universe_snapshot",
            {"selection_mode": "provided", "symbols": list(symbols)},
        ),
        "data_windows": metadata.get("data_windows", {}),
        "data_source_base_url": metadata.get("data_source_base_url"),
    }

    payload = {
        "generated_at": _utc_iso_now(),
        "params": {"days": days, "symbols": symbols, "strategies": strategy_names},
        "provenance": provenance,
        "overall": compute_stats(all_trades),
        "by_strategy": {name: compute_stats(ts) for name, ts in by_strategy.items()},
        "regime_breakdown": regime_breakdown,
        "missed_signals": dict(missed_counter) if missed_counter is not None else {},
        "trades": [t.to_dict() for t in all_trades],
    }

    logs_dir = Path(output_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)
    filename = f"backtest_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    path = logs_dir / filename
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            _strict_json_value(payload),
            f,
            ensure_ascii=False,
            indent=2,
            default=str,
            allow_nan=False,
        )

    app_logger.info(f"📄 Backtest raporu yazıldı: {path}")
    return str(path)


def _strict_json_value(value: Any) -> Any:
    """Replace non-finite floats so reports remain standards-compliant JSON."""

    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _strict_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_strict_json_value(item) for item in value]
    return value


# ==========================================================================
# Üst seviye koşum + CLI
# ==========================================================================


async def run_backtest(
    days: int,
    symbols: List[str],
    strategy_names: str,
    base_url: str = "https://fapi.binance.com",
    cfg: Any = settings,
    missed_counter: Optional[Dict[str, int]] = None,
    run_metadata: Optional[Dict[str, Any]] = None,
    end_time_ms: Optional[int] = None,
    start_time_ms: Optional[int] = None,
) -> List[BacktestTrade]:
    """Verilen semboller için tarihsel veriyi çeker ve tüm stratejileri
    simüle eder; tüm işlemleri (tüm semboller birleşik) döndürür.

    `missed_counter` verilirse (mutasyonla) kapılarda reddedilen/timeout olan
    sinyallerin sayısı buraya birikir (tüm semboller toplamı).

    `end_time_ms` tüm sembol ve aralıklara aynı sabit test bitişini uygular.
    `start_time_ms` verilirse (CLI --start/--end) test penceresi `days`
    yerine [start_time_ms, test_end_time_ms) olarak sabitlenir; `days`
    yalnız İÇSEL veri çekme boyutlandırması (kaç mum istenecek) için taban
    değer olur — `resolve_backtest_window` bunu pencere süresinden zaten
    doğru hesaplayıp geçirir, burada tekrar türetilmez.
    `run_metadata` verilirse gerçek veri pencereleri ve yeniden üretilebilirlik
    bilgileri bu sözlüğe yazılır.
    """
    if start_time_ms is None and days <= 0:
        raise ValueError("Backtest gün sayısı pozitif olmalı")

    strategies = get_enabled(strategy_names)
    if not strategies:
        raise ValueError(f"Geçerli scalper stratejisi bulunamadı: '{strategy_names}'")

    test_end_time_ms = end_time_ms if end_time_ms is not None else int(time.time() * 1000)
    if start_time_ms is not None:
        if start_time_ms >= test_end_time_ms:
            raise ValueError("start_time_ms, end_time_ms'den (test penceresi bitişi) önce olmalı")
        test_start_time_ms = start_time_ms
    else:
        test_start_time_ms = test_end_time_ms - days * _MILLISECONDS_PER_DAY
    metadata = {
        **_git_provenance(),
        "scalper_config": scalper_config_snapshot(cfg),
        "test_window": {
            "requested_days": days,
            "start_ms": test_start_time_ms,
            "start_utc": _ms_to_utc_iso(test_start_time_ms),
            "end_ms": test_end_time_ms,
            "end_utc": _ms_to_utc_iso(test_end_time_ms),
        },
        "warmup_candles": dict(BACKTEST_WARMUP_CANDLES),
        "universe_snapshot": {
            "captured_at": _utc_iso_now(),
            "selection_mode": "provided",
            "symbols": list(symbols),
        },
        "data_windows": {},
        "data_source_base_url": base_url,
    }
    if run_metadata is not None:
        run_metadata.clear()
        run_metadata.update(metadata)

    fetcher = KlineFetcher(base_url=base_url)
    throttled = _ThrottledFetch(fetcher.get_klines)

    all_trades: List[BacktestTrade] = []
    try:
        timeframes = resolve_timeframes(cfg)
        tf_entry, tf_context, tf_regime = timeframes
        for symbol in symbols:
            app_logger.info(f"📥 {symbol}: tarihsel veri çekiliyor ({days} gün)...")
            data = await gather_symbol_data(
                throttled, symbol, days, end_time=test_end_time_ms,
                timeframes=timeframes,
            )
            candles_5m = data[tf_entry]
            candles_15m = data[tf_context]
            candles_4h = data[tf_regime]
            metadata["data_windows"][symbol] = {
                interval: _candle_window_snapshot(candles, test_start_time_ms)
                for interval, candles in data.items()
            }
            app_logger.info(
                f"📊 {symbol}: {tf_entry}={len(candles_5m)} {tf_context}={len(candles_15m)} "
                f"{tf_regime}={len(candles_4h)} mum toplandı"
            )
            trades = simulate_symbol(
                symbol, candles_5m, candles_15m, candles_4h, strategies, cfg,
                missed_counter=missed_counter,
                test_start_time_ms=test_start_time_ms,
            )
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


def _parse_utc_date(date_str: str) -> int:
    """'YYYY-MM-DD' -> o günün 00:00:00 UTC'sinin epoch ms değeri.

    Hatalı biçimde ValueError fırlatır (argparse/main_async bunu yakalayıp
    kullanıcıya okunur biçimde gösterir).
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def resolve_backtest_window(
    days: int, start: Optional[str], end: Optional[str],
) -> Tuple[int, Optional[int], Optional[int]]:
    """CLI --days / --start / --end argümanlarını çözer (AĞ YOK, saf —
    testler için).

    --start/--end İKİSİ BİRDEN verilirse --days'i geçersiz kılar: pencere
    [start, end) UTC olur (end DAHİL DEĞİL). Yalnız biri verilirse hata.
    Hiçbiri verilmezse eski davranış (days, None, None) — `--days N` ile
    şu andan N gün geriye.

    Döner: (effective_days, start_ms, end_ms).
    `effective_days`, pencere modunda `gather_symbol_data`'nın kaç günlük
    mum isteyeceğini belirler (pencere süresinin YUKARI yuvarlanmışı) —
    warm-up zaten `gather_symbol_data` içinde ayrıca eklenir, burada tekrar
    hesaba katılmaz.
    """
    if (start is None) != (end is None):
        raise ValueError("--start ve --end birlikte verilmeli (yalnız biri verildi)")

    if start is None:
        return days, None, None

    start_ms = _parse_utc_date(start)
    end_ms = _parse_utc_date(end)
    if end_ms <= start_ms:
        raise ValueError(f"--end ({end}) --start'tan ({start}) sonra olmalı")

    span_days = (end_ms - start_ms) / _MILLISECONDS_PER_DAY
    effective_days = max(1, math.ceil(span_days))
    return effective_days, start_ms, end_ms


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scalper (A/B/C/...) stratejileri için tarihsel backtest ve karşılaştırma raporu."
    )
    parser.add_argument("--days", type=int, default=30, help="Kaç günlük tarihsel veri (varsayılan 30)")
    parser.add_argument(
        "--start", type=str, default=None,
        help="Pencere başlangıcı UTC YYYY-MM-DD (--end ile birlikte verilmeli; verilirse --days'i geçersiz kılar)",
    )
    parser.add_argument(
        "--end", type=str, default=None,
        help="Pencere bitişi UTC YYYY-MM-DD, DAHİL DEĞİL — [start, end) (--start ile birlikte verilmeli)",
    )
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
    effective_days, start_ms, end_ms = resolve_backtest_window(args.days, args.start, args.end)
    window_desc = f"{args.start}→{args.end} (UTC, [start,end))" if start_ms is not None else f"{args.days} gün"
    app_logger.info(
        f"🚀 Scalper backtest başlıyor: pencere={window_desc} sembol={symbols} strateji={args.strategies}"
    )

    missed_counter: Dict[str, int] = {}
    run_metadata: Dict[str, Any] = {}
    all_trades = await run_backtest(
        days=effective_days,
        symbols=symbols,
        strategy_names=args.strategies,
        missed_counter=missed_counter,
        run_metadata=run_metadata,
        start_time_ms=start_ms,
        end_time_ms=end_ms,
    )

    universe_snapshot = run_metadata["universe_snapshot"]
    universe_snapshot["selection_mode"] = (
        "auto_current_24h_volume"
        if args.symbols.strip().lower() == "auto"
        else "explicit_symbols"
    )
    universe_snapshot["requested_arg"] = args.symbols

    print_report(all_trades, missed_counter=missed_counter)
    # NOT: src/core/logger.py loguru için sys.stdout.buffer'ı SARAN ayrı bir
    # TextIOWrapper kurar; süreç çıkışında bu iki wrapper'ın kapanma sırası
    # bazen print() çıktısının OS'e hiç flush edilmeden kaybolmasına yol
    # açabiliyor. logger.py'ye dokunmadan, kendi çıktımızı garantiye almak
    # için burada açıkça flush ediyoruz.
    sys.stdout.flush()
    write_json_report(
        all_trades,
        effective_days,
        symbols,
        args.strategies,
        missed_counter=missed_counter,
        cfg=settings,
        run_metadata=run_metadata,
    )


def main() -> None:
    parser = _build_arg_parser()
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
