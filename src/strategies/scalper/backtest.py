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
aranmaz. Her sembol simulate_symbol() içinde BAĞIMSIZ (küresel saat YOK)
simüle edilir; canlı motorun `scalper_max_positions` KAPASİTE kapısı
(engine._evaluate_symbol, `len(tracked | pending) >= max`) bu yüzden
sembol-içi değil, semboller-ARASI bir kısıt olduğundan run_backtest()
seviyesinde, tüm sembollerin aday işlemleri birleştirildikten SONRA,
giriş zamanına göre kronolojik tek bir POST-HOC geçişle uygulanır (bkz.
`_apply_capacity_gate`, 2026-08-21 "parite: kapasite kapısı"). `manage_position`
TP1 görmemiş pozisyonlara canlıdaki `SCALPER_MAX_HOLD_HOURS`/REAPER yaş
kesmesini de uygular. Kapasite
yüzünden reddedilen adaylar `missed_counter["capacity"]`'de sayılır ve
işleme dahil edilmez. Bilinen sapmalar `_apply_capacity_gate` docstring'inde
(2 madde) listelenir.

CLI:
    python -m src.strategies.scalper.backtest --days 30 \\
        --symbols BTCUSDT,ETHUSDT,SOLUSDT --strategies A,B,C
    python -m src.strategies.scalper.backtest --days 7 --symbols auto
    # Sabit tarih penceresi (UTC, [start,end)) — --days'i geçersiz kılar,
    # rejim bazlı (BEAR/BULL/FLAT) karşılaştırma için:
    python -m src.strategies.scalper.backtest \\
        --start 2026-01-23 --end 2026-02-13 --symbols BTCUSDT --strategies C
    # Kline önbelleği (varsayılan açık, data/klines_cache/): aynı
    # (sembol, aralık, pencere) için sonraki koşular Binance'e gitmez.
    # --refresh önbelleği yok sayıp TAZE çeker ve üzerine yazar.
    python -m src.strategies.scalper.backtest --days 7 --symbols BTCUSDT \\
        --cache-dir data/klines_cache --refresh
"""

from __future__ import annotations

import argparse
import asyncio
import bisect
import copy
import heapq
import json
import math
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

from src.core.config import settings
from src.core.logger import app_logger
from src.strategies.scalper import kline_cache
from src.strategies.scalper.data import KlineFetcher
from src.strategies.scalper.funding import (
    DEFAULT_FUNDING_FALLBACK_PCT,
    SOURCE_FALLBACK,
    SOURCE_HISTORICAL,
    FundingSchedule,
    funding_payment,
    load_funding_schedule,
    settlements_between,
)
from src.strategies.scalper.indicators import atr, chandelier_stop
from src.strategies.scalper.entry_gates import (
    c_entry_guard_verdict,
    evaluate_entry_gates,
    validate_entry_gate_settings,
)
from src.strategies.scalper.market_gate import (
    MARKET_GATE_INTRADAY_TF,
    evaluate_market_gate,
    resolve_day_open,
)
from src.strategies.scalper.permutation import (
    DEFAULT_METRICS as PERMUTATION_DEFAULT_METRICS,
    aggregate_from,
    clamp_shift_report,
    _apply_index as _apply_permutation_index,  # ortak indeks + kelepçe istatistiği
    compute_p_values,
    draw_permutation_index,
    merge_clamp_stats,
)
from src.strategies.scalper.regime import detect_regime
from src.strategies.scalper.scanner import UniverseScanner
from src.strategies.scalper.setups import apply_stop_policy, get_enabled
from src.strategies.scalper.structure import (
    EXIT_OFF,
    StructureExitInput,
    StructureState,
    detect_structure,
    resolve_structure_role,
    structure_exit_action,
    structure_exit_mode,
    structure_gate_blocks,
    structure_gate_enabled,
    structure_pivot,
    structure_state_for,
    structure_use_close,
    structure_window_bars,
)
from src.strategies.scalper.types import (
    Candle,
    Direction,
    ScalpSignal,
    StrategyContext,
    EXIT_REASON_STALE_TP,
    StrategyProtocol,
    fee_aware_breakeven_price,
    fill_anchored_stop_price,
    position_roi_pct,
    price_at_roi,
    resolve_trail_mult,
    stale_tp_should_close,
)

# --------------------------------------------------------------------------
# Sabitler
# --------------------------------------------------------------------------

# Kline çekme: STRATEJİ zaman dilimi → günlük mum sayısı.
#
# ⚠️ "1d" BİLEREK YOKTUR (2026-08-23 inceleme bulgusu). Kısa bir süre buraya
# eklenmişti — sözde "lider piyasa kapısının (D15) günlük serisi için" — ama
# o giriş HİÇBİR YERDEN OKUNMUYORDU: `gather_leader_series` günlük mum
# sayısını `days + run_days + _LEADER_DAILY_EXTRA_DAYS` ile doğrudan
# hesaplar, önbellek anahtarı da `kline_cache._INTERVAL_MS`'ten gelir.
# Tek FİİLİ etkisi doğrulamayı GEVŞETMEKTİ: bu sözlük `gather_symbol_data`'nın
# tek kapısıdır ve `SCALPER_TF_REGIME=1d` gibi bir ayarı gürültülü bir
# `ValueError` yerine SESSİZCE kabul ettiriyordu (günde 1 mumla rejim ve
# indikatör hesaplanırdı). Buraya yeni bir dilim eklemeden önce: gerçekten
# bir STRATEJİ dilimi mi?
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
# NOT: komisyon oranları settings'ten okunur (scalper_taker_fee_pct /
# scalper_maker_fee_pct, % biriminde — /100 ile orana çevrilir). Kayma yalnız
# taker girişte uygulanır.
# D24/A5: kayma da artık settings'ten okunabilir (`SCALPER_SLIPPAGE_RATE`);
# aşağıdaki sabit yalnız GERİYE DÖNÜK varsayılan ve alanı taşımayan test
# çiftleri için yedektir. VARSAYILAN DEĞİŞMEDİ — golden backtest ve D#P1
# paritesi korunur.
_SLIPPAGE_RATE = 0.0002    # %0,02, yalnız taker girişte, aleyhte


def slippage_rate(cfg: Any) -> float:
    """Taker giriş kayması (oran). `cfg` alanı taşımıyorsa sabit varsayılan."""
    try:
        value = float(getattr(cfg, "scalper_slippage_rate", _SLIPPAGE_RATE))
    except (TypeError, ValueError):
        return _SLIPPAGE_RATE
    return value if value >= 0.0 else _SLIPPAGE_RATE


def stressed_cfg(cfg: Any, multiplier: float) -> Any:
    """Maliyet stres senaryosu (D24/A5): komisyon + kayma oranlarını
    `multiplier` ile çarpan SIĞ bir kopya döner.

    Neden: başabaş kazanma oranımız ≈%85 — kenar incedir ve "kaymayı/komisyonu
    2× yapmak sonucu tersine çeviriyor mu" sorusunun cevabını BİLMİYORUZ.
    Bu fonksiyon yalnız BACKTEST tarafındadır; canlı motora dokunmaz ve
    `multiplier == 1.0` iken ORİJİNAL nesneyi (kopya bile değil) döndürür →
    varsayılan davranış bit düzeyinde aynıdır.
    """
    try:
        mult = float(multiplier)
    except (TypeError, ValueError):
        return cfg
    if mult == 1.0:
        return cfg
    if mult <= 0.0:
        raise ValueError("Maliyet stres çarpanı pozitif olmalı")

    updates = {
        "scalper_taker_fee_pct": float(
            getattr(cfg, "scalper_taker_fee_pct", 0.0) or 0.0
        ) * mult,
        "scalper_maker_fee_pct": float(
            getattr(cfg, "scalper_maker_fee_pct", 0.0) or 0.0
        ) * mult,
        "scalper_slippage_rate": slippage_rate(cfg) * mult,
    }
    if hasattr(cfg, "model_copy"):
        # Pydantic Settings: doğrulayıcılar YENİDEN koşmaz — stres değeri
        # `scalper_slippage_rate` üst sınırını aşsa bile çevrimdışı harness
        # patlamamalı (canlı ayar DEĞİŞMİYOR, yalnız senaryo kopyası).
        return cfg.model_copy(update=updates)
    clone = copy.copy(cfg)
    for name, value in updates.items():
        setattr(clone, name, value)
    return clone

# ==========================================================================
# Gerçekçilik anahtarları (audit/eval-harness-fixes, paket A1+)
# ==========================================================================
# Harness'ın iyimser varsayımlarını kapatan özellikler. `--legacy-model` (ya
# da tümünü `--disable-realism` ile kapatmak) bu işten ÖNCEKİ sayıları BİREBİR
# üretir. Sonraki paketler (A2 maker giriş, A3 fonlama+likidasyon, A4 portföy)
# kendi adlarını bu demete ekler.
REALISM_FEATURES: Tuple[str, ...] = (
    "gap_fills",       # stop/TP tetik fiyatı = mum açılışı (açılış ötedeyse)
    "exit_slippage",   # her market-tipi çıkışta aleyhte kayma
    "intrabar_path",   # TP sonrası taşınan stop yalnız TP'DEN SONRAKİ yolda sınanır
    # A2 — maker (limit) giriş gerçekçiliği; YALNIZ scalper_entry_mode=maker'ı
    # etkiler (taker çıktısı bu üçüyle DEĞİŞMEZ).
    "maker_trade_through",  # limit yalnız fiyat limitin ÖTESİNE geçince dolar
    "maker_fill_bar",       # dolum mumunda yalnız dolumdan SONRAKİ yol sayılır
    "single_pending",       # bekleyen emir varken aynı sembole ikinci emir yok
    # A3 — fonlama ve likidasyon (izole marj yaklaşımı)
    "funding",              # 00/08/16 UTC funding'i açık miktar üzerinden tahsil et
    "liquidation",          # stop likidasyon fiyatının ötesindeyse / boşlukta likide ol
    # A4 — canlı günlük kayıp kesicisi (engine._update_kill_switch): UTC günü
    # gerçekleşmiş PnL eşiği aşınca o günün kalan girişleri düşer.
    "daily_latch",
)

# Maker trade-through eşiği, YÜZDE (0.01 = 1 baz puan = %0.01).
DEFAULT_MAKER_TRADE_THROUGH_PCT = 0.01


def normalize_disabled_realism(disabled: Any) -> frozenset:
    """None / virgüllü metin / iterable → doğrulanmış kapalı-özellik kümesi.
    Bilinmeyen ad ValueError'dır (yazım hatası sessizce 'gerçekçi' koşu
    üretmesin)."""
    if disabled is None:
        return frozenset()
    if isinstance(disabled, str):
        names = [n.strip() for n in disabled.split(",") if n.strip()]
    else:
        names = [str(n).strip() for n in disabled if str(n).strip()]
    unknown = [n for n in names if n not in REALISM_FEATURES]
    if unknown:
        raise ValueError(
            f"Bilinmeyen gerçekçilik özelliği: {unknown}; geçerli: {list(REALISM_FEATURES)}"
        )
    return frozenset(names)


@dataclass(frozen=True)
class ExitRealism:
    """Çıkış dolum modeli (bir koşu boyunca sabit). Varsayılan (alan
    varsayılanları) = ESKİ model: boşluksuz, kaymasız, tam-mum stop testi."""
    gap_fills: bool = False
    exit_slippage_rate: float = 0.0
    intrabar_path: bool = False


LEGACY_EXIT_REALISM = ExitRealism()


def build_exit_realism(
    cfg: Any,
    disabled: Any = None,
    exit_slippage_rate: Optional[float] = None,
) -> ExitRealism:
    """Çıkış gerçekçilik ayarı. `exit_slippage_rate=None` → giriş kaymasıyla
    aynı (`slippage_rate(cfg)`; `--fee-stress` zaten onu çarpar)."""
    off = normalize_disabled_realism(disabled)
    if exit_slippage_rate is None:
        rate = slippage_rate(cfg)
    else:
        rate = float(exit_slippage_rate)
        if rate < 0.0:
            raise ValueError("Çıkış kayması negatif olamaz")
    return ExitRealism(
        gap_fills="gap_fills" not in off,
        exit_slippage_rate=0.0 if "exit_slippage" in off else rate,
        intrabar_path="intrabar_path" not in off,
    )


@dataclass(frozen=True)
class MakerRealism:
    """Maker giriş dolum modeli (bir koşu boyunca sabit). Varsayılan (alan
    varsayılanları) = ESKİ model: temas = dolum, tam-mum, çoklu bekleyen emir."""
    trade_through_rate: float = 0.0   # oran (0.0001 = 1 bp); 0 = yalnız temas
    fill_bar_path: bool = False
    single_pending: bool = False


LEGACY_MAKER_REALISM = MakerRealism()


def build_maker_realism(
    disabled: Any = None,
    maker_trade_through_pct: Optional[float] = None,
) -> MakerRealism:
    """`maker_trade_through_pct` YÜZDE (None → DEFAULT_MAKER_TRADE_THROUGH_PCT);
    "maker_trade_through" kapalıysa oran 0 (eski temas-dolum)."""
    off = normalize_disabled_realism(disabled)
    pct = (
        DEFAULT_MAKER_TRADE_THROUGH_PCT if maker_trade_through_pct is None
        else float(maker_trade_through_pct)
    )
    if pct < 0.0:
        raise ValueError("Maker trade-through yüzdesi negatif olamaz")
    return MakerRealism(
        trade_through_rate=0.0 if "maker_trade_through" in off else pct / 100.0,
        fill_bar_path="maker_fill_bar" not in off,
        single_pending="single_pending" not in off,
    )


def maker_orders_report(stats: Optional[Dict[str, int]]) -> Dict[str, Any]:
    """JSON raporundaki "maker_orders": verilen/dolan/zaman aşımına uğrayan
    maker emirleri ve dolum oranı (emir yoksa None = ölçülmedi)."""
    s = stats or {}
    placed = int(s.get("placed", 0))
    filled = int(s.get("filled", 0))
    return {
        "placed": placed,
        "filled": filled,
        "timed_out": int(s.get("timed_out", 0)),
        "fill_rate": (filled / placed) if placed > 0 else None,
    }


def realism_report(
    cfg: Any,
    disabled: Any = None,
    exit_slippage_rate: Optional[float] = None,
    maker_trade_through_pct: Optional[float] = None,
    funding_fallback_pct: Optional[float] = None,
    funding_cache_dir: Optional[str | Path] = None,
) -> Dict[str, Any]:
    """JSON raporundaki "realism" nesnesi: açık/kapalı özellikler + sayılar."""
    off = normalize_disabled_realism(disabled)
    if exit_slippage_rate is None:
        rate = slippage_rate(cfg)
    else:
        rate = float(exit_slippage_rate)
    return {
        "enabled": [f for f in REALISM_FEATURES if f not in off],
        "disabled": [f for f in REALISM_FEATURES if f in off],
        "parameters": {
            "exit_slippage_rate": rate,
            "maker_trade_through_pct": (
                DEFAULT_MAKER_TRADE_THROUGH_PCT if maker_trade_through_pct is None
                else float(maker_trade_through_pct)
            ),
            "funding_fallback_pct": (
                DEFAULT_FUNDING_FALLBACK_PCT if funding_fallback_pct is None
                else float(funding_fallback_pct)
            ),
            "funding_cache_dir": (
                None if funding_cache_dir is None else str(funding_cache_dir)
            ),
        },
    }


# --------------------------------------------------------------------------
# A3 — likidasyon (izole marj yaklaşımı) ve funding ayarı
# --------------------------------------------------------------------------

# Binance USDⓈ-M TIER-1 bakım marjı oranları (YAKLAŞIK: tier'lar notional'a
# göre değişir; harness tier-1'i tüm pozisyona uygular). Tabloda olmayan
# sembol için varsayılan.
MAINTENANCE_MARGIN_RATES: Dict[str, float] = {
    "BTCUSDT": 0.004,
    "ETHUSDT": 0.005,
}
DEFAULT_MAINTENANCE_MARGIN_RATE = 0.0065


def maintenance_margin_rate(symbol: str) -> float:
    return MAINTENANCE_MARGIN_RATES.get(str(symbol).upper(), DEFAULT_MAINTENANCE_MARGIN_RATE)


def liquidation_price(
    entry_price: float, leverage: int, direction: Direction, mmr: float
) -> float:
    """İzole marj yaklaşımı: LONG entry*(1-1/lev+mmr), SHORT entry*(1+1/lev-mmr)."""
    lev = max(1, int(leverage))
    if direction == Direction.LONG:
        return entry_price * (1.0 - 1.0 / lev + mmr)
    return entry_price * (1.0 + 1.0 / lev - mmr)


@dataclass(frozen=True)
class RiskRealism:
    """Funding + likidasyon modeli (bir koşu boyunca sabit). Alan
    varsayılanları = ESKİ model (ikisi de kapalı)."""
    funding: bool = False
    liquidation: bool = False
    funding_fallback_rate: float = DEFAULT_FUNDING_FALLBACK_PCT / 100.0  # 8 saatlik oran
    funding_cache_dir: Optional[str] = None
    _schedules: Dict[str, FundingSchedule] = field(
        default_factory=dict, compare=False, repr=False
    )

    def schedule_for(self, symbol: str) -> Optional[FundingSchedule]:
        """Sembolün funding takvimi (dosya bir kez okunur); funding kapalıysa None."""
        if not self.funding:
            return None
        key = str(symbol).upper()
        sched = self._schedules.get(key)
        if sched is None:
            sched = load_funding_schedule(key, self.funding_cache_dir, self.funding_fallback_rate)
            self._schedules[key] = sched
        return sched


LEGACY_RISK_REALISM = RiskRealism()


def build_risk_realism(
    disabled: Any = None,
    funding_fallback_pct: Optional[float] = None,
    funding_cache_dir: Optional[str | Path] = None,
) -> RiskRealism:
    """`funding_fallback_pct` YÜZDE / 8 saat (None → 0.01). Önbellek dizini
    verilmezse tarihsel oran okunmaz (tamamı yedek oran; testler hermetik)."""
    off = normalize_disabled_realism(disabled)
    pct = (
        DEFAULT_FUNDING_FALLBACK_PCT if funding_fallback_pct is None
        else float(funding_fallback_pct)
    )
    return RiskRealism(
        funding="funding" not in off,
        liquidation="liquidation" not in off,
        funding_fallback_rate=pct / 100.0,
        funding_cache_dir=None if funding_cache_dir is None else str(funding_cache_dir),
    )


def intrabar_path(c: Candle) -> Tuple[float, float, float, float]:
    """Mum içi VARSAYILAN fiyat sırası: yükselen mum (close >= open) önce
    dibe, sonra tepeye gider; düşen mum tersine. OHLC bunu kanıtlayamaz —
    tek tutarlı varsayımdır ve tüm TP-sonrası kararlar bunu kullanır."""
    if c.close >= c.open:
        return (c.open, c.low, c.high, c.close)
    return (c.open, c.high, c.low, c.close)


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
    cache_dir: Optional[Path] = None,
    refresh: bool = False,
) -> Dict[str, List[Candle]]:
    """Bir sembol için test penceresi + sabit warm-up mumlarını toplar.

    Warm-up mumları yalnız bağlam/indikatör seed'i içindir. `run_backtest`,
    `simulate_symbol(..., test_start_time_ms=...)` ile sinyal üretimini istenen
    `days` penceresine sınırlar; böylece ilk günler eksik EMA/RSI ile ölçülmez.

    `timeframes` = (entry, context, regime); desteklenmeyen aralıkta açık
    ValueError (sessiz yanlış veri yerine).

    `cache_dir` verilirse (ve `end_time` biliniyorsa) her (sembol, aralık,
    pencere) serisi `kline_cache` ile diskte gzip JSON olarak saklanır:
    aynı anahtar için sonraki çağrı Binance'e gitmez, doğrudan diskten okur
    (bkz. kline_cache.window_start_ms — anahtar `needed` mum sayısından
    türer, warm-up sabitleri değişirse anahtar da değişir). `refresh=True`
    önbelleği yok sayar, taze çeker ve üzerine yazar. `cache_dir=None`
    (varsayılan) eski davranışla birebir aynıdır — her çağrı Binance'e gider.
    """
    out: Dict[str, List[Candle]] = {}
    for interval, warmup in zip(timeframes, _WARMUP_BY_ROLE):
        per_day = _CANDLES_PER_DAY.get(interval)
        if per_day is None:
            raise ValueError(
                f"Desteklenmeyen zaman dilimi: {interval!r} "
                f"(bilinenler: {sorted(_CANDLES_PER_DAY)})"
            )
        out[interval] = await _fetch_series_cached(
            fetch, symbol, interval, days * per_day + warmup,
            end_time=end_time, cache_dir=cache_dir, refresh=refresh,
        )
    return out


async def _fetch_series_cached(
    fetch: FetchFn, symbol: str, interval: str, needed: int,
    end_time: Optional[int], cache_dir: Optional[Path], refresh: bool,
) -> List[Candle]:
    """Tek bir (sembol, aralık, `needed` mum) serisini önbellekli çeker.

    `gather_symbol_data`'nın döngü gövdesinden ORTAK KULLANIM için ayrıldı
    (D15): lider piyasa kapısının serilerinin de BİREBİR aynı önbellek
    anahtarını üretmesi gerekiyordu — aksi hâlde evrende zaten bulunan bir
    liderin (BTCUSDT) giriş TF serisi ikinci kez Binance'ten çekilirdi.
    Davranış değişmedi (log metinleri dahil).
    """
    candles: Optional[List[Candle]] = None
    cache_start_ms: Optional[int] = None
    if cache_dir is not None and end_time is not None:
        cache_start_ms = kline_cache.window_start_ms(interval, needed, end_time)
        if not refresh:
            candles = kline_cache.load(cache_dir, symbol, interval, cache_start_ms, end_time)
            if candles is not None and len(candles) != needed:
                # Boyut uyuşmazlığı: eski/kısmi bir önbellek (aynı anahtarla
                # ama farklı içerikle) — güvenilmez say, taze çek.
                app_logger.warning(
                    f"⚠️ {symbol} {interval}: önbellek boyutu uyuşmuyor "
                    f"({len(candles)} != {needed}) — yeniden çekiliyor"
                )
                candles = None
            elif candles is not None:
                app_logger.info(
                    f"💾 {symbol} {interval}: önbellekten yüklendi ({len(candles)} mum)"
                )

    if candles is None:
        app_logger.info(f"📥 {symbol} {interval}: Binance'ten çekiliyor...")
        candles = await fetch_paginated(fetch, symbol, interval, needed, end_time=end_time)
        if cache_dir is not None and end_time is not None:
            kline_cache.save(cache_dir, symbol, interval, cache_start_ms, end_time, candles)

    return candles


# ==========================================================================
# Lider piyasa kapısı (D15) — pencere başında BİR KEZ çekilen lider serisi
# ==========================================================================

# Lider günlük serisi için ek gün payı: N günlük koşu N+1 kapanış ister ve
# pencerenin İLK gününde de uygulanabilmesi gerekir.
_LEADER_DAILY_EXTRA_DAYS = 3


@dataclass
class LeaderSeries:
    """Lider sembolün (varsayılan BTCUSDT) kapı girdilerini zaman-hizalı
    veren salt-okunur seri. AĞ YOK — yalnız çekilmiş mumlar üzerinde çalışır.

    Canlı motorla parite: `inputs_at(cutoff)` canlının o an gördüğü ÜÇ
    büyüklüğü birebir üretir — (gün açılışı, giriş TF son kapanışı,
    tamamlanmış günlük kapanışlar). Türetme kuralı iki tarafta da
    `market_gate.resolve_day_open`'dur: önce GERÇEK açılış (bugünün 00:00
    UTC 15m mumunun open'ı), o yoksa son tamamlanmış günlük kapanış vekili.
    """

    symbol: str
    entry_close_times: List[int]
    entry_closes: List[float]
    daily_close_times: List[int]
    daily_closes: List[float]
    # Gün açılışı serisi (15m). Eski çağrıcılar/testler vermezse yedek
    # (önceki günlük kapanış) yoluna düşülür — davranış geriye uyumlu.
    intraday_open_times: List[int] = field(default_factory=list)
    intraday_opens: List[float] = field(default_factory=list)
    intraday_close_times: List[int] = field(default_factory=list)
    # A4 — HAM mumlar (yalnız permütasyon sürücüsü okur; `inputs_at` kullanmaz).
    # Verilmezse lider permüte EDİLEMEZ, gerçek kalır (rapora yazılır).
    entry_candles: Optional[List[Candle]] = field(default=None, repr=False, compare=False)
    daily_candles: Optional[List[Candle]] = field(default=None, repr=False, compare=False)
    intraday_candles: Optional[List[Candle]] = field(default=None, repr=False, compare=False)

    def inputs_at(
        self, cutoff_ms: int
    ) -> Optional[Tuple[float, float, List[float]]]:
        """(day_open, last_close, daily_closes) — `cutoff_ms` anında KAPANMIŞ
        veriden; yetersizse None (kapı uygulanmaz, canlıdaki fail-open ile
        aynı ilke).

        Look-ahead YASAK: tüm seriler `close_time <= cutoff_ms` ile kesilir —
        canlı motorun `KlineFetcher._drop_unclosed`'ı ile aynı anlam (yalnız
        kapanmış mum görülür). Gün açılışı mumu da bu kurala tabidir: günün
        ilk 15 dakikasında henüz kapanmadığı için vekile düşülür, canlıda
        olduğu gibi.
        """
        j = bisect.bisect_right(self.entry_close_times, cutoff_ms) - 1
        if j < 0:
            return None
        k = bisect.bisect_right(self.daily_close_times, cutoff_ms)
        closes = self.daily_closes[:k]
        day_open, _source = resolve_day_open(
            self.intraday_open_times,
            self.intraday_opens,
            self.intraday_close_times,
            closes,
            cutoff_ms,
        )
        if day_open is None:
            return None
        return day_open, self.entry_closes[j], closes


def _leader_window_snapshot(
    close_times: Sequence[int], candles: int
) -> Dict[str, Any]:
    """Lider serisinin rapora yazılan kapsamı: mum sayısı + ilk/son kapanış.

    `market_gate` metadata'sı için (2026-08-23 inceleme bulgusu): rapor
    yalnız mum SAYISINI taşıyordu, o da serinin doğru PENCEREYİ kapsayıp
    kapsamadığını göstermiyordu. Zaman damgaları hem epoch ms hem okunur
    UTC olarak yazılır (log'a bakan insan için).
    """
    first = int(close_times[0]) if close_times else None
    last = int(close_times[-1]) if close_times else None
    return {
        "candles": int(candles),
        "first_close_ms": first,
        "last_close_ms": last,
        # `_ms_to_utc_iso` (aşağıda) None kabul etmez — boş seri de geçerli
        # bir durumdur (kapı fail-open olur), bu yüzden burada korunur.
        "first_close_utc": _ms_to_utc_iso(first) if first is not None else None,
        "last_close_utc": _ms_to_utc_iso(last) if last is not None else None,
    }


async def gather_leader_series(
    fetch: FetchFn, symbol: str, days: int, end_time: Optional[int],
    tf_entry: str, run_days: int,
    cache_dir: Optional[Path] = None, refresh: bool = False,
) -> LeaderSeries:
    """Lider sembolün giriş TF + günlük serilerini pencere başında BİR KEZ
    toplar.

    Giriş TF serisi `gather_symbol_data` ile AYNI `needed` formülünü
    kullanır — lider evrende zaten varsa aynı önbellek dosyasına düşer,
    ikinci bir Binance çekimi olmaz.
    """
    # tf_entry bir STRATEJİ dilimidir (lider dilimi değil) — aynı doğrulama.
    per_day = _CANDLES_PER_DAY.get(tf_entry)
    if per_day is None:
        raise ValueError(
            f"Desteklenmeyen zaman dilimi: {tf_entry!r} "
            f"(bilinenler: {sorted(_CANDLES_PER_DAY)})"
        )
    entry_candles = await _fetch_series_cached(
        fetch, symbol, tf_entry, days * per_day + _CTX_5M_WINDOW,
        end_time=end_time, cache_dir=cache_dir, refresh=refresh,
    )
    daily_needed = days + max(1, run_days) + _LEADER_DAILY_EXTRA_DAYS
    daily_candles = await _fetch_series_cached(
        fetch, symbol, "1d", daily_needed,
        end_time=end_time, cache_dir=cache_dir, refresh=refresh,
    )
    # Gün açılışı serisi: 00:00 UTC mumunun `open`'ı = `1d` mumunun `open`'ı
    # (ölçüldü: 76 gün sınırı, 0 uyuşmazlık). `needed` formülü
    # gather_symbol_data'nın 15m anahtarıyla aynı tutulur ki evrende zaten
    # bulunan bir liderin 15m serisi ikinci kez çekilmesin.
    intraday_needed = (
        days * _CANDLES_PER_DAY[MARKET_GATE_INTRADAY_TF] + _CTX_15M_WINDOW
    )
    intraday_candles = await _fetch_series_cached(
        fetch, symbol, MARKET_GATE_INTRADAY_TF, intraday_needed,
        end_time=end_time, cache_dir=cache_dir, refresh=refresh,
    )
    return LeaderSeries(
        symbol=symbol,
        entry_close_times=[c.close_time for c in entry_candles],
        entry_closes=[c.close for c in entry_candles],
        daily_close_times=[c.close_time for c in daily_candles],
        daily_closes=[c.close for c in daily_candles],
        intraday_open_times=[c.open_time for c in intraday_candles],
        intraday_opens=[c.open for c in intraday_candles],
        intraday_close_times=[c.close_time for c in intraday_candles],
        entry_candles=list(entry_candles),
        daily_candles=list(daily_candles),
        intraday_candles=list(intraday_candles),
    )


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
    # SİNYAL mumunun close_time'ı (dolum mumunun DEĞİL). Canlıda
    # `ScalpPosition.entry_candle_time` ile birebir aynı anlam
    # (executor.try_open: ctx.candles_5m[-1].close_time) — yapı çıkışının
    # "olay girişten SONRA mı" tazelik şartı bunu kullanır (parite).
    signal_close_time: int = 0
    # Yapı çıkışı (SCALPER_STRUCTURE_EXIT=be) stopu BE'ye çekti mi? Yalnız
    # RAPORLAMA için: TP1 öncesi bu stopa değen kapanış "SL" yerine
    # "STRUCT_BE" etiketlenir ki kesilen kayıplar sayılabilsin.
    structure_be_applied: bool = False
    # D24/A3 — BAR-BAZLI mark-to-market işaretleri: (bar close_time,
    # o barın kapanışında pozisyonun net K/Z'si). YALNIZ ÖLÇÜM; hiçbir
    # çıkış/stop/boyut kararı bunu okumaz (bkz. `_mark_equity`).
    equity_marks: List[Tuple[int, float]] = field(default_factory=list)
    # Çıkış dolum modeli. DOĞRUDAN kurulan OpenPosition ESKİ modeldir;
    # `open_position()` koşunun modelini buraya yazar.
    exit_realism: ExitRealism = LEGACY_EXIT_REALISM
    # maker_fill_bar: limit dolum tetiği (LONG L*(1-tt), SHORT L*(1+tt)).
    # None (varsayılan / taker / özellik kapalı) → dolum mumu TAM sayılır.
    fill_bar_trigger: Optional[float] = None
    # A3 — bar-içi AŞIRI UÇTA işaretlenen özkaynak (YALNIZ ÖLÇÜM; bkz.
    # `_mark_equity`): LONG için low, SHORT için high.
    equity_marks_adverse: List[Tuple[int, float]] = field(default_factory=list)
    # A3 — funding. `funding_schedule=None` → funding yok (eski model).
    funding_schedule: Optional[FundingSchedule] = None
    funding_paid: float = 0.0            # + = ödenen, - = alınan (USD)
    funding_events: List[Dict[str, Any]] = field(default_factory=list)
    funding_cursor: Optional[int] = None  # son işlenen mum open_time'ı
    # A3 — likidasyon. `liq_price=None` → likidasyon yok (eski model).
    liq_price: Optional[float] = None
    stop_beyond_liq: bool = False        # açılışta stop likidasyonun ÖTESİNDEydi

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
    # D33/GÖREV B (2026-09-04) — SİNYAL mumunun close_time'ı (dolum mumunun
    # DEĞİL; bkz. OpenPosition.signal_close_time). Post-hoc analiz notu:
    # harness'ta bu alan, canlıda `ScalpPosition.entry_candle_time` alanının
    # karşılığıdır — `entry_time` DEĞİL (o dolum anıdır). `to_dict()`/JSON
    # raporuna (`trades[]`) dahildir; eski koşulardan gelen kayıtlarda
    # `None` olabilir (geriye dönük doldurma YOK).
    signal_close_time: Optional[int] = None
    legs: List[Dict[str, Any]] = field(default_factory=list)
    # D24/A3 — bar-bazlı mark-to-market işaretleri (bkz. OpenPosition).
    # `to_dict` DIŞINDA tutulur: 800 işlemlik bir koşuda bar başına iki sayı
    # JSON raporunu megabaytlara şişirir ve rapor tüketicileri (pano,
    # ledger_report) bunu okumaz — seri `bar_equity_series` ile TÜREVİLİR.
    equity_marks: List[Tuple[int, float]] = field(default_factory=list)
    # A3 — bar-içi aşırı uçta işaretler (to_dict DIŞINDA, aynı gerekçe).
    equity_marks_adverse: List[Tuple[int, float]] = field(default_factory=list)
    # A3 — funding (pnl'e DAHİL; + = ödenen) ve likidasyon alanları.
    funding: float = 0.0
    funding_settlements: int = 0
    funding_historical: int = 0
    funding_fallback: int = 0
    stop_beyond_liq: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d = {
            k: v for k, v in self.__dict__.items()
            if k not in ("exit_idx", "equity_marks", "equity_marks_adverse")
        }
        return d


def _find_maker_fill(
    signal: ScalpSignal, candles_5m: List[Candle], signal_idx: int, cfg: Any,
    entry_delay_candles: int = 0,
    trade_through_rate: float = 0.0,
) -> Optional[tuple]:
    """Maker giriş modu: LIMIT fiyatı = sinyal mumunun kapanışı. Sonraki
    `scalper_maker_fill_timeout_candles` mum içinde fiyat limite değerse
    (LONG: mum.low <= limit; SHORT: mum.high >= limit) ilk değen mumda,
    kaymasız, limit fiyatından dolum sayılır.

    `trade_through_rate` (A2, varsayılan 0 = yalnız temas, eski davranış):
    emir kuyruğun ARKASINDA durur; yalnız fiyat limitin ötesine `tt` oranında
    geçerse (LONG low <= L*(1-tt), SHORT high >= L*(1+tt)) dolar. Dolum
    fiyatı yine L'dir.

    `entry_delay_candles` (D24/A5 çürütme koşusu, varsayılan 0) emrin borsaya
    N mum GEÇ ulaştığı senaryoyu simüle eder: tarama penceresi N mum kayar,
    limit fiyatı DEĞİŞMEZ. 0 iken davranış bit düzeyinde eskisiyle aynıdır.

    Döner: (entry_idx, entry_price) ya da timeout içinde hiç değmezse None."""
    limit_price = candles_5m[signal_idx].close
    timeout = max(0, int(cfg.scalper_maker_fill_timeout_candles))
    n = len(candles_5m)
    delay = max(0, int(entry_delay_candles or 0))

    for offset in range(1 + delay, timeout + 1 + delay):
        idx = signal_idx + offset
        if idx >= n:
            break
        c = candles_5m[idx]
        touched = (
            c.low <= limit_price * (1.0 - trade_through_rate)
            if signal.direction == Direction.LONG
            else c.high >= limit_price * (1.0 + trade_through_rate)
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
    entry_delay_candles: int = 0,
    exit_realism: Optional[ExitRealism] = None,
    maker_realism: Optional[MakerRealism] = None,
    maker_stats: Optional[Dict[str, int]] = None,
    risk_realism: Optional[RiskRealism] = None,
) -> Optional[OpenPosition]:
    """Sinyalden pozisyon kurar: risk bazlı boyutlama + stop mesafesi kapısı
    + dolum simülasyonu. Kapı geçilmezse None döner (işlem yok).

    `entry_delay_candles` (D24/A5, varsayılan 0): giriş emri N mum GEÇ
    dolar. Bugün harness sinyal mumunun BİR SONRAKİ open'ında dolduruyor;
    "bir mum gecikse ne olurdu" hiç ölçülmedi. 0 iken davranış birebir aynı.

    Dolum simülasyonu `cfg.scalper_entry_mode`'a göre değişir:
    - "taker" (varsayılan): SONRAKİ mumun open'ında kaymalı (aleyhte) dolum,
      komisyon cfg.scalper_taker_fee_pct.
    - "maker": LIMIT emri simülasyonu (bkz. _find_maker_fill) — timeout
      içinde dolmazsa sinyal SESSİZCE iptal edilir; `missed_counter`
      verilmişse (mutasyonla) sayaç artırılır, işlem raporlanabilsin diye.
      `maker_stats` verilirse {"placed","filled","timed_out"} sayaçları
      (mutasyonla) güncellenir. `maker_realism=None` → tam maker gerçekçiliği.
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

    delay = max(0, int(entry_delay_candles or 0))
    fill_bar_trigger: Optional[float] = None
    if entry_mode == "maker":
        mk = maker_realism if maker_realism is not None else build_maker_realism()
        if maker_stats is not None:
            for key in ("placed", "filled", "timed_out"):
                maker_stats.setdefault(key, 0)
            maker_stats["placed"] += 1
        fill = _find_maker_fill(
            signal, candles_5m, signal_idx, cfg, entry_delay_candles=delay,
            trade_through_rate=mk.trade_through_rate,
        )
        if fill is None:
            if missed_counter is not None:
                missed_counter["maker_missed"] = missed_counter.get("maker_missed", 0) + 1
            if maker_stats is not None:
                maker_stats["timed_out"] = maker_stats.get("timed_out", 0) + 1
            return None
        entry_idx, entry_price = fill
        if maker_stats is not None:
            maker_stats["filled"] = maker_stats.get("filled", 0) + 1
        if mk.fill_bar_path:
            fill_bar_trigger = (
                entry_price * (1.0 - mk.trade_through_rate)
                if signal.direction == Direction.LONG
                else entry_price * (1.0 + mk.trade_through_rate)
            )
        entry_commission_rate = cfg.scalper_maker_fee_pct / 100.0
    else:
        if signal_idx + 1 + delay >= len(candles_5m):
            return None  # sonraki mum yok — dolum yapılamaz
        entry_idx = signal_idx + 1 + delay
        entry_candle = candles_5m[entry_idx]
        slip = slippage_rate(cfg)
        if signal.direction == Direction.LONG:
            entry_price = entry_candle.open * (1.0 + slip)
        else:
            entry_price = entry_candle.open * (1.0 - slip)
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

    stop_price = fill_anchored_stop_price(
        entry_hint,
        stop_price,
        entry_price,
        signal.direction,
        float(getattr(cfg, "scalper_max_stop_pct", 0.0) or 0.0),
    )
    entry_candle = candles_5m[entry_idx]

    # A3: None → tam gerçekçilik (funding yedek oranla, likidasyon açık).
    risk = risk_realism if risk_realism is not None else build_risk_realism()
    liq_price: Optional[float] = None
    stop_beyond_liq = False
    if risk.liquidation:
        liq_price = liquidation_price(
            entry_price, leverage, signal.direction,
            maintenance_margin_rate(signal.symbol),
        )
        stop_beyond_liq = (
            stop_price < liq_price if signal.direction == Direction.LONG
            else stop_price > liq_price
        )

    tp1_price = price_at_roi(entry_price, cfg.scalper_tp1_roi, leverage, signal.direction)
    tp2_price = price_at_roi(entry_price, cfg.scalper_tp2_roi, leverage, signal.direction)
    tp1_qty = qty * cfg.scalper_tp1_fraction
    tp2_qty = qty * cfg.scalper_tp2_fraction
    runner_qty = max(qty - tp1_qty - tp2_qty, 0.0)

    breakeven_price = fee_aware_breakeven_price(
        entry=entry_price,
        direction=signal.direction,
        entry_fee_rate=entry_commission_rate,
        exit_fee_rate=exit_commission_rate,
        buffer_pct=cfg.scalper_breakeven_buffer_pct,
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
        signal_close_time=candles_5m[signal_idx].close_time,
        # None → tam gerçekçi çıkış modeli (varsayılan kayma = giriş kayması).
        exit_realism=(
            exit_realism if exit_realism is not None else build_exit_realism(cfg)
        ),
        fill_bar_trigger=fill_bar_trigger,
        funding_schedule=risk.schedule_for(signal.symbol),
        liq_price=liq_price,
        stop_beyond_liq=stop_beyond_liq,
    )


def _post_fill_candle(c: Candle, direction: Direction, trigger: float) -> Candle:
    """Maker dolum mumunun DOLUMDAN SONRAKİ görünümü: mum-içi yol
    (`intrabar_path`) tetik seviyesini ilk kestiği noktadan itibaren kısaltılır;
    open = tetik (mum zaten ötede açıldıysa gerçek open), high/low = kalan yolun
    uçları, close aynı. LONG'da low her zaman dolum ANINDA ya da SONRASINDA gelir;
    high yalnız low'dan SONRA geliyorsa kalır. Yol tetiği kesmiyorsa (tutarsız
    veri) mum aynen döner."""
    pts = intrabar_path(c)
    long_ = direction == Direction.LONG
    if (c.open <= trigger) if long_ else (c.open >= trigger):
        remaining = list(pts)  # açılışta zaten ötede: yolun TAMAMI dolum sonrası
    else:
        remaining = None
        for k in range(len(pts) - 1):
            p0, p1 = pts[k], pts[k + 1]
            if min(p0, p1) <= trigger <= max(p0, p1):
                remaining = [trigger] + list(pts[k + 1:])
                break
        if remaining is None:
            return c
    return Candle(
        open_time=c.open_time, open=remaining[0], high=max(remaining),
        low=min(remaining), close=c.close, volume=c.volume,
        close_time=c.close_time,
    )


def _is_hit(c: Candle, level: float, direction: Direction, is_stop: bool) -> bool:
    """LONG'da stop=düşüşle (low), hedef=yükselişle (high) değer; SHORT
    aynası."""
    if direction == Direction.LONG:
        return (c.low <= level) if is_stop else (c.high >= level)
    return (c.high >= level) if is_stop else (c.low <= level)


def _apply_exit_slippage(pos: OpenPosition, price: float) -> float:
    """Market-tipi çıkışta aleyhte kayma: LONG satar (aşağı), SHORT geri alır
    (yukarı). Maker (limit) dolumlarda ÇAĞRILMAZ."""
    s = pos.exit_realism.exit_slippage_rate
    if s <= 0.0:
        return price
    if pos.direction == Direction.LONG:
        return price * (1.0 - s)
    return price * (1.0 + s)


def _stop_fill_price(
    pos: OpenPosition, c: Candle, stop: float, allow_gap: bool = True
) -> float:
    """STOP_MARKET dolumu: mum stopun ÖTESİNDE açıldıysa tetik = açılış,
    değilse stop. `allow_gap=False` — stop bu mum İÇİNDE (TP dolumundan sonra)
    taşındıysa açılışta henüz yoktu; açılışa düşülemez."""
    trigger = stop
    if pos.exit_realism.gap_fills and allow_gap:
        if pos.direction == Direction.LONG:
            if c.open <= stop:
                trigger = c.open
        elif c.open >= stop:
            trigger = c.open
    return _apply_exit_slippage(pos, trigger)


def _tp_fill_price(pos: OpenPosition, c: Candle, level: float) -> float:
    """TAKE_PROFIT_MARKET (canlıda `place_take_profit`, binance_client_improved)
    tetikte market dolar: mum hedefin ötesinde açıldıysa açılışta (lehte
    boşluk), kayma aleyhte. Limit DEĞİL — bu yüzden kayma uygulanır."""
    trigger = level
    if pos.exit_realism.gap_fills:
        if pos.direction == Direction.LONG:
            if c.open >= level:
                trigger = c.open
        elif c.open <= level:
            trigger = c.open
    return _apply_exit_slippage(pos, trigger)


def _path_after_touch(
    pos: OpenPosition, c: Candle, touch_level: float, direction: Direction
) -> Optional[List[float]]:
    """`touch_level`e değdikten SONRAKİ mum-içi yol noktaları (ilk nokta
    dokunuş seviyesi ya da, açılış zaten ötedeyse, açılış). Seviye yolda
    bulunamazsa None (tutarsız veri)."""
    pts = intrabar_path(c)
    long_ = direction == Direction.LONG
    if (c.open >= touch_level) if long_ else (c.open <= touch_level):
        return list(pts)  # açılışta zaten ötede: yolun TAMAMI sonrası
    for p0, p1 in zip(pts, pts[1:]):
        if min(p0, p1) <= touch_level <= max(p0, p1):
            i = pts.index(p1)
            return [touch_level] + list(pts[i:])
    return None


def _stop_hit_after_touch(
    pos: OpenPosition, c: Candle, touch_level: float, stop: float, direction: Direction
) -> bool:
    """TP seviyesine değildikten SONRAKİ mum-içi yolda `stop`a değilir mi?
    (`intrabar_path` açıkken; kapalıyken tam-mum testi `_is_hit`.)"""
    if not pos.exit_realism.intrabar_path:
        return _is_hit(c, stop, direction, is_stop=True)
    remaining = _path_after_touch(pos, c, touch_level, direction)
    if remaining is None:  # tutarsız veri: tam-mum (muhafazakâr)
        return _is_hit(c, stop, direction, is_stop=True)
    if direction == Direction.LONG:
        return min(remaining) <= stop
    return max(remaining) >= stop


def _tp_before_stop_after_touch(
    pos: OpenPosition, c: Candle, touch_level: float, tp_level: float,
    stop: float, direction: Direction,
) -> bool:
    """`touch_level`e (TP1) değdikten sonraki yolda `tp_level` (TP2) `stop`tan
    (BE) ÖNCE mi değiyor? Yalnız `intrabar_path` açıkken; aynı parçada ikisi de
    değiyorsa ya da veri tutarsızsa stop ÖNCE (kötümser)."""
    if not pos.exit_realism.intrabar_path:
        return False
    remaining = _path_after_touch(pos, c, touch_level, direction)
    if not remaining:
        return False
    long_ = direction == Direction.LONG

    def stop_in(lo: float, hi: float) -> bool:
        return lo <= stop if long_ else hi >= stop

    def tp_in(lo: float, hi: float) -> bool:
        return hi >= tp_level if long_ else lo <= tp_level

    first = remaining[0]
    if stop_in(first, first):
        return False
    if tp_in(first, first):
        return True
    for p0, p1 in zip(remaining, remaining[1:]):
        lo, hi = min(p0, p1), max(p0, p1)
        if stop_in(lo, hi):
            return False
        if tp_in(lo, hi):
            return True
    return False


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


def _liquidation_hit(pos: OpenPosition, c: Candle, direction: Direction) -> bool:
    """İzole likidasyon: (a) mum likidasyonun ÖTESİNDE açıldı (boşluk), ya da
    (b) aşırı uç likidasyona değdi VE stop likidasyonun ötesinde (fiyat stopa
    varmadan önce likidasyona varır). Stop içerideyse fiyat önce stopa değer →
    likidasyon yok."""
    liq = pos.liq_price
    if liq is None or pos.remaining_qty <= 1e-12:
        return False
    if direction == Direction.LONG:
        return c.open <= liq or (c.low <= liq and pos.current_stop < liq)
    return c.open >= liq or (c.high >= liq and pos.current_stop > liq)


def _liquidate(pos: OpenPosition, c: Candle, direction: Direction) -> None:
    """Kalan miktarın MARJI kadar zarar; dolum = boşlukta açılış, yoksa
    likidasyon fiyatı. Komisyon/kayma yok (marj zaten tamamen gider)."""
    liq = float(pos.liq_price)
    price = c.open if (
        (direction == Direction.LONG and c.open <= liq)
        or (direction == Direction.SHORT and c.open >= liq)
    ) else liq
    qty = pos.remaining_qty
    margin = qty * pos.entry_price / pos.leverage if pos.leverage else qty * pos.entry_price
    pos.legs.append({"label": "LIQUIDATION", "quantity": qty, "price": price, "pnl": -margin})
    pos.remaining_qty = 0.0
    pos.exit_reason = "LIQUIDATION"
    pos.exit_price = price
    pos.exit_time = c.close_time


def _process_candle_exits(pos: OpenPosition, c: Candle, direction: Direction) -> bool:
    """Bir mum için çıkış kurallarını uygular. Pozisyon TAMAMEN kapandıysa
    True döner.

    AŞAMA A (TP1 dolmadan): ilk stop VS TP1. Aynı mumda ikisi de değindiyse
    SL ÖNCE sayılır (kötümser) — TP1 kısmi dolumu OLMAZ, tüm miktar SL'de
    kapanır. Bu eşitlik kuralı BİLEREK muhafazakârdır (mum-içi sıra
    bilinmez) ve `intrabar_path` ile değiştirilmez.
    AŞAMA B (TP1 dolduktan sonra — aynı mumda devam edebilir): current_stop
    (break-even veya sonrasında chandelier trail) TÜM kalan miktarı korur;
    TP2 henüz dolmadıysa bağımsız sabit hedef olarak aynı anda izlenir. Yine
    aynı mumda ikisi de değindiyse SL/trail ÖNCE sayılır.
    `intrabar_path` açıkken TP dolumuyla TAŞINAN stop (BE / TP1 tabanı) yalnız
    TP'ye değdikten SONRAKİ yolda sınanır; kapalıyken eski tam-mum testi.
    Aynı mumda TP1 dolduysa ve `intrabar_path` açıksa TP2 ile BE stopu arasındaki
    sıra yoldan okunur: TP2 stoptan önce değiyorsa TP2 dolar (sonra TP1 tabanı
    TP2-sonrası yolda sınanır). Yalnız ilk stop-vs-TP1 eşitliği sabit SL-önce.
    """
    # A3: likidasyon her şeyden ÖNCE (stop-vs-TP1 eşitliğindeki kötümser kural
    # ile aynı yönde: belirsizlikte zarar sayılır).
    if _liquidation_hit(pos, c, direction):
        _liquidate(pos, c, direction)
        return True

    # TP1 bu mumda mı doldu? (taşınan BE stopu bu mumda doğdu → açılışa
    # düşme yok, yolun TP-sonrası kısmı sınanır)
    tp1_this_bar = False
    if not pos.tp1_filled:
        sl_hit = _is_hit(c, pos.current_stop, direction, is_stop=True)
        tp1_hit = _is_hit(c, pos.tp1_price, direction, is_stop=False)

        if sl_hit:
            # Etiket ayrımı (yalnız raporlama): yapı çıkışı (SCALPER_STRUCTURE_
            # EXIT=be) stopu BE'ye çekmişse bu bir "SL kaybı" değil, kesilmiş
            # bir kayıptır — E9 ölçümünde ayrı sayılabilsin. Bayrak varsayılan
            # False olduğundan kapalı yapılandırmada davranış birebir aynıdır.
            _close_remaining(
                pos, _stop_fill_price(pos, c, pos.current_stop), c.close_time,
                "STRUCT_BE" if pos.structure_be_applied else "SL",
            )
            return True
        if tp1_hit:
            _fill_leg(pos, pos.tp1_qty, _tp_fill_price(pos, c, pos.tp1_price), "TP1")
            pos.tp1_filled = True
            tp1_this_bar = True
            pos.current_stop = pos.breakeven_price
            pos.trailing_active = True

    if pos.remaining_qty <= 1e-12:
        return True

    if pos.tp1_filled:
        if tp1_this_bar:
            sl_hit = _stop_hit_after_touch(
                pos, c, pos.tp1_price, pos.current_stop, direction
            )
        else:
            sl_hit = _is_hit(c, pos.current_stop, direction, is_stop=True)
        tp2_hit = (
            not pos.tp2_filled and pos.tp2_qty > 0
            and _is_hit(c, pos.tp2_price, direction, is_stop=False)
        )

        if (
            sl_hit and tp2_hit and tp1_this_bar
            and _tp_before_stop_after_touch(
                pos, c, pos.tp1_price, pos.tp2_price, pos.current_stop, direction
            )
        ):
            # TP1 sonrası yolda TP2, BE stopundan ÖNCE değiyor → TP2 dolar;
            # yükselen taban (TP1) aşağıdaki TP2 dalında TP2-sonrası yolda sınanır.
            sl_hit = False

        if sl_hit:
            _close_remaining(
                pos,
                _stop_fill_price(pos, c, pos.current_stop, allow_gap=not tp1_this_bar),
                c.close_time, "TRAIL",
            )
            return True
        if tp2_hit:
            _fill_leg(pos, pos.tp2_qty, _tp_fill_price(pos, c, pos.tp2_price), "TP2")
            pos.tp2_filled = True
            # Live ExitManager raises the remaining runner's floor to TP1
            # after a proven TP2 fill, while preserving any tighter stop.
            pos.current_stop = (
                max(pos.current_stop, pos.tp1_price)
                if direction == Direction.LONG
                else min(pos.current_stop, pos.tp1_price)
            )
            # Taşınan taban TP2'ye değdikten SONRAKİ yolda sınanır
            # (`intrabar_path` kapalıyken eski tam-mum kötümser testi).
            if pos.remaining_qty > 1e-12 and _stop_hit_after_touch(
                pos, c, pos.tp2_price, pos.current_stop, direction
            ):
                _close_remaining(
                    pos,
                    _stop_fill_price(pos, c, pos.current_stop, allow_gap=False),
                    c.close_time, "TRAIL",
                )
                return True

    return pos.remaining_qty <= 1e-12


def _mark_equity(pos: OpenPosition, c: Candle) -> None:
    """D24/A3 — bar SONUNDA pozisyonun net K/Z'sini işaretle (YALNIZ ÖLÇÜM).

    Değer = gerçekleşmiş bacaklar + kalan miktarın mumun KAPANIŞINDAKİ
    gerçekleşmemiş K/Z'si − giriş komisyonu. Kalan miktarın GELECEKTEKİ çıkış
    komisyonu düşülmez (mark-to-market sözleşmesi: çıkış ücreti çıkışta
    gerçekleşir); pozisyon kapandığı barda kalan miktar sıfır olduğundan son
    işaret `_finalize_trade`'in `pnl` değerine BİREBİR eşittir.

    Aynı `close_time` için ikinci çağrı işareti GÜNCELLER (EOD kapanışında
    mum sonu işareti bir kez açık, bir kez kapalı hesaplanır) — böylece
    "son işaret == trade.pnl" değişmezi korunur.

    Bu fonksiyon hiçbir karar değişkenine dokunmaz: yalnız `equity_marks`
    listesine yazar. Golden backtest sayıları bu yüzden DEĞİŞMEZ.
    """
    realized = sum(leg["pnl"] for leg in pos.legs)
    if pos.remaining_qty > 1e-12:
        realized += _leg_pnl(pos, pos.remaining_qty, c.close)
    realized -= pos.entry_commission_rate * pos.qty_total * pos.entry_price
    realized -= pos.funding_paid
    # A3: aynı işaret AŞIRI UÇ fiyatında (LONG low / SHORT high). Kapanışta
    # kalan miktar yoksa kapanış işaretiyle aynıdır; min() geçersiz mum
    # verisinde bile "aşırı uç <= kapanış" değişmezini korur.
    adverse = realized
    if pos.remaining_qty > 1e-12:
        worst = c.low if pos.direction == Direction.LONG else c.high
        adverse = min(
            realized,
            realized
            - _leg_pnl(pos, pos.remaining_qty, c.close)
            + _leg_pnl(pos, pos.remaining_qty, worst),
        )
    if pos.equity_marks and pos.equity_marks[-1][0] == c.close_time:
        pos.equity_marks[-1] = (c.close_time, realized)
    else:
        pos.equity_marks.append((c.close_time, realized))
    if pos.equity_marks_adverse and pos.equity_marks_adverse[-1][0] == c.close_time:
        pos.equity_marks_adverse[-1] = (c.close_time, adverse)
    else:
        pos.equity_marks_adverse.append((c.close_time, adverse))


def _charge_funding(pos: OpenPosition, candles_5m: List[Candle], idx: int) -> None:
    """Bu mumun BAŞINDA (çıkışlar işlenmeden) `cursor < T <= mum open_time`
    olan her funding anını, o anda AÇIK miktar üzerinden tahsil et.
    Fiyat: T'de başlayan mumun open'ı, yoksa T'den önceki son kapanış."""
    sched = pos.funding_schedule
    if sched is None:
        return
    c = candles_5m[idx]
    if pos.funding_cursor is None:
        pos.funding_cursor = pos.entry_time
    due = settlements_between(pos.funding_cursor, c.open_time)
    pos.funding_cursor = max(pos.funding_cursor, c.open_time)
    for t in due:
        qty = pos.remaining_qty
        if qty <= 1e-12:
            break
        if t == c.open_time or idx == 0:
            price = c.open
        else:
            price = candles_5m[idx - 1].close
        rate, source = sched.rate_at(t)
        payment = funding_payment(pos.direction == Direction.LONG, qty, price, rate)
        pos.funding_paid += payment
        pos.funding_events.append({
            "time": t, "price": price, "rate": rate, "quantity": qty,
            "payment": payment, "source": source,
        })


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
    total_pnl -= pos.funding_paid

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
        signal_close_time=pos.signal_close_time,
        legs=pos.legs,
        equity_marks=list(pos.equity_marks),
        equity_marks_adverse=list(pos.equity_marks_adverse),
        funding=pos.funding_paid,
        funding_settlements=len(pos.funding_events),
        funding_historical=sum(
            1 for e in pos.funding_events if e["source"] == SOURCE_HISTORICAL
        ),
        funding_fallback=sum(
            1 for e in pos.funding_events if e["source"] == SOURCE_FALLBACK
        ),
        stop_beyond_liq=pos.stop_beyond_liq,
    )


class _StructureFeed:
    """`manage_position` için yapı serisi besleyicisi (yalnız harness).

    Canlı karşılığı `engine._apply_structure_exits`: orada seri borsadan
    (TTL önbellekli) çekilir, burada zaten elde olan seriden `_slice_upto`
    ile aynı pencere kesilir. İKİ tarafta da AYNI saf fonksiyonlar
    (`detect_structure` → `structure_exit_action`) AYNI pencere boyuyla
    çağrılır — parite (DECISIONS P1).

    Bellekleme (memoization) semantiği DEĞİŞTİRMEZ: yapı durumu yalnız yeni
    bir yapı-TF mumu kapandığında değişir, bu yüzden sonuç (dilim son
    close_time'ı, dilim uzunluğu) anahtarıyla önbelleklenir; önbellekli ve
    önbelleksiz sonuçların aynılığı testle sabitlenmiştir.
    """

    __slots__ = ("series", "close_times", "window", "left", "right", "use_close", "_cache")

    def __init__(self, series: List[Candle], window: int, cfg: Any) -> None:
        self.series = series
        self.close_times = [c.close_time for c in series]
        self.window = window
        self.left, self.right = structure_pivot(cfg)
        self.use_close = structure_use_close(cfg)
        self._cache: Dict[tuple, StructureState] = {}

    def state_at(self, cutoff_ms: int) -> Optional[StructureState]:
        window_slice = _slice_upto(self.series, self.close_times, cutoff_ms, self.window)
        if not window_slice:
            return None
        key = (len(window_slice), window_slice[-1].close_time)
        cached = self._cache.get(key)
        if cached is None:
            cached = detect_structure(
                window_slice,
                pivot_left=self.left,
                pivot_right=self.right,
                use_close=self.use_close,
            )
            self._cache[key] = cached
        return cached


def manage_position(
    pos: OpenPosition,
    candles_5m: List[Candle],
    cfg: Any,
    structure_feed: Optional["_StructureFeed"] = None,
) -> BacktestTrade:
    """Pozisyonu entry_idx'ten itibaren, kapanana ya da veri bitene (EOD)
    kadar mum mum yönetir.

    `structure_feed` verilirse (yalnız `SCALPER_STRUCTURE_EXIT != off`) her
    mum sonunda yapı-tabanlı çıkış kararı da uygulanır — canlı safety
    turundaki sıra ile AYNI: önce SL/TP (intrabar), sonra trailing, yapı,
    TP1 görmemiş pozisyonun bayat-kâr kapanışı (STALE_TP, D30) ve son olarak
    max-hold/REAPER kesmesi. Verilmezse yalnız yapı adımı atlanır; STALE_TP
    ve REAPER cfg'deki eşikleri >0 ise yine işler.
    """
    n = len(candles_5m)
    exit_idx = n - 1

    for idx in range(pos.entry_idx, n):
        c = candles_5m[idx]
        if idx == pos.entry_idx and pos.fill_bar_trigger is not None:
            # maker_fill_bar: dolum mumunda yalnız dolum SONRASI yol sayılır
            # (TP, stop, MFE, MAE). Zincirleme izleyici (chandelier) hâlâ
            # gerçek mumu görür — bkz. rapor notu.
            c = _post_fill_candle(c, pos.direction, pos.fill_bar_trigger)
        _update_mae_mfe(pos, c, pos.direction, pos.leverage)

        _charge_funding(pos, candles_5m, idx)
        closed = _process_candle_exits(pos, c, pos.direction)
        if closed:
            exit_idx = idx
            _mark_equity(pos, c)
            break

        if pos.trailing_active:
            _update_trailing(pos, candles_5m, idx, cfg)

        if structure_feed is not None:
            action = structure_exit_action(
                structure_feed.state_at(c.close_time),
                StructureExitInput(
                    direction=pos.direction,
                    entry_close_time=pos.signal_close_time,
                    current_price=c.close,
                    current_stop=pos.current_stop,
                    breakeven_price=pos.breakeven_price,
                ),
                cfg,
            )
            if action == "close":
                _close_remaining(
                    pos, _apply_exit_slippage(pos, c.close), c.close_time, "CHOCH"
                )
                exit_idx = idx
                _mark_equity(pos, c)
                break
            if action == "be":
                pos.current_stop = pos.breakeven_price
                pos.structure_be_applied = True

        # Canlı `_safety_tick` sırası: exits.step → structure → TV event →
        # bayat-kâr kapanışı (D30) → reaper. Tarihsel harness TV olaylarını
        # modellemez; bu yüzden aynı sıradaki son modellenebilir adımlar
        # burada STALE_TP ve REAPER'dır. Canlıdaki gibi yalnız TP1'i hiç
        # görmemiş (`trailing_active == False`) pozisyonu yaş sınırında
        # reduce-only MARKET karşılığı mum kapanışından kapatır.
        # Mum içi SL/TP her zaman önce işlendi; çıkış ücreti `_close_remaining`
        # tarafından taker oranıyla düşülür.
        age_ms = c.close_time - pos.entry_time
        # D30 — bayat-kâr kapanışı: yaş ≥ SCALPER_STALE_TP_HOURS ve mum
        # KAPANIŞINDAKİ ROI ≥ SCALPER_STALE_TP_MIN_ROI_PCT ise kalanı kapat.
        # Canlı karşılığı `engine._close_stale_profitable_positions`: AYNI saf
        # karar (`stale_tp_should_close`), fiyat = safety turundaki güncel
        # fiyat. Kapalıyken (varsayılan 0) bu blok hiç çalışmaz.
        if not pos.trailing_active and stale_tp_should_close(
            cfg,
            age_ms=age_ms,
            roi_pct=position_roi_pct(
                pos.entry_price, c.close, pos.leverage, pos.direction
            ),
        ):
            _close_remaining(
                pos, _apply_exit_slippage(pos, c.close), c.close_time,
                EXIT_REASON_STALE_TP,
            )
            exit_idx = idx
            _mark_equity(pos, c)
            break
        max_hold_h = float(
            getattr(cfg, "scalper_max_hold_hours", 0.0) or 0.0
        )
        if (
            max_hold_h > 0.0
            and not pos.trailing_active
            and age_ms >= max_hold_h * 3_600_000.0
        ):
            _close_remaining(
                pos, _apply_exit_slippage(pos, c.close), c.close_time, "REAPER"
            )
            exit_idx = idx
            _mark_equity(pos, c)
            break
        _mark_equity(pos, c)
    else:
        exit_idx = n - 1
        last = candles_5m[-1]
        _close_remaining(
            pos, _apply_exit_slippage(pos, last.close), last.close_time, "EOD"
        )
        # Aynı `close_time` — `_mark_equity` son işareti GÜNCELLER (döngü
        # sonunda pozisyon henüz açıkken yazılmıştı; şimdi çıkış komisyonu
        # da düşülmüş kapanış değeriyle yer değiştirir).
        _mark_equity(pos, last)

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
    leader: Optional[LeaderSeries] = None,
    entry_delay_candles: int = 0,
    disabled_realism: Any = None,
    exit_slippage_rate: Optional[float] = None,
    maker_trade_through_pct: Optional[float] = None,
    maker_stats: Optional[Dict[str, int]] = None,
    funding_fallback_pct: Optional[float] = None,
    funding_cache_dir: Optional[str | Path] = None,
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

    `leader` verilir VE `cfg.scalper_market_gate` açıksa lider piyasa kapısı
    (D15) da uygulanır — canlı motorla AYNI saf fonksiyonla
    (`market_gate.evaluate_market_gate`). Engellenenler
    `missed_counter["market_gate_day"/"market_gate_run"]` altında sayılır.
    Kapı kapalıyken (varsayılan) bu blok HİÇ çalışmaz; çıktı bit düzeyinde
    değişmez (bkz. tests/test_golden_backtest.py).

    Genel deterministik giriş kapıları (2026-09-03; hafta günü + sembol×yön
    2026-09-04 eklendi — `cfg.scalper_c_blocked_cells` /
    `scalper_entry_block_hours_utc` / `scalper_entry_block_weekdays_utc` /
    `scalper_entry_block_weekdays_direction` / `scalper_symbol_direction_block`
    / `scalper_min_atr_pct` / `scalper_max_atr_pct`, hepsi varsayılan KAPALI)
    yapı kapısının ardında, `apply_stop_policy`'den ÖNCE, canlı motorla AYNI
    saf fonksiyonla (`entry_gates.evaluate_entry_gates`) uygulanır. Bu kapılar
    C taraması İÇİNDİR — TV dış sinyali bu harness'ta simüle EDİLMEZ (ayrı bir
    TV anahtarı da YOK; canlı motorda TV sinyali de AYNI kapıdan geçer, TV
    etkisini ölçmek isteyen canlı niyet defterinden okumalıdır). Engellenenler
    `missed_counter["cell_gate"/"hour_gate"/"weekday_gate"/"symbol_dir_gate"/
    "atr_gate"]` altında sayılır; kapalıyken anahtar EKLENMEZ.
    """
    trades: List[BacktestTrade] = []
    exit_realism = build_exit_realism(cfg, disabled_realism, exit_slippage_rate)
    maker_realism = build_maker_realism(disabled_realism, maker_trade_through_pct)
    risk_realism = build_risk_realism(
        disabled_realism, funding_fallback_pct, funding_cache_dir
    )
    # Sayaç her zaman yerel tutulur: single_pending zaman aşımını buradan okur.
    mstats: Dict[str, int] = maker_stats if maker_stats is not None else {}
    maker_timeout = max(0, int(getattr(cfg, "scalper_maker_fill_timeout_candles", 0) or 0))
    # D33: geçersiz kapı ayarı, kapıya hiç sinyal ulaşmayan pencerede bile
    # sessizce 'kapı kapalı' ölçümü üretmesin — alanlar yok/boş/0 iken no-op.
    validate_entry_gate_settings(cfg)
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

    # Yapı-tabanlı çıkış besleyicisi — yalnız SCALPER_STRUCTURE_EXIT != off
    # iken kurulur; aksi halde manage_position bugünküyle birebir aynı yolu
    # izler. Seri, rol adına göre ZATEN ELDE OLAN üç seriden seçilir (canlı
    # motorda da yeni bir veri kaynağı yok).
    structure_feed: Optional[_StructureFeed] = None
    if structure_exit_mode(cfg) != EXIT_OFF:
        _role_series = {
            "entry": candles_5m, "context": candles_15m, "regime": candles_4h,
        }[resolve_structure_role(cfg)]
        structure_feed = _StructureFeed(
            _role_series, structure_window_bars(cfg), cfg
        )
    market_gate_on = bool(getattr(cfg, "scalper_market_gate", False))
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

        # Canlı parite (D15): lider piyasa kapısı — rejim kapısının HEMEN
        # ardında, canlı motordaki (engine._evaluate_symbol) sırayla birebir.
        # Karar anı = bu 5m/1m mumun close_time'ı; lider serisi o âna kadar
        # KAPANMIŞ veriyle kesilir (look-ahead yasak, LeaderSeries.inputs_at).
        if market_gate_on and leader is not None:
            leader_inputs = leader.inputs_at(close_times_5m[i])
            if leader_inputs is not None:
                gate_reason = evaluate_market_gate(
                    raw_signal.direction,
                    leader_inputs[0], leader_inputs[1], leader_inputs[2], cfg,
                )
                if gate_reason is not None:
                    if missed_counter is not None:
                        missed_counter[gate_reason] = (
                            missed_counter.get(gate_reason, 0) + 1
                        )
                    i += 1
                    continue
        # Canlı parite: yapı kapısı (engine._evaluate_symbol'de rejim kapısının
        # HEMEN ARDINDA, AYNI saf fonksiyonla — structure.structure_gate_blocks).
        # Kapı kapalıyken (varsayılan) hiçbir şey hesaplanmaz.
        if structure_gate_enabled(cfg):
            # İstisna BİLİNÇLİ olarak yutulmaz: harness çevrimdışı bir ölçüm
            # aracıdır; hatalı bir SCALPER_STRUCTURE_* ayarı sessizce "kapı
            # kapalı" ölçümü üretmektense gürültüyle patlamalı. (Canlı motor
            # tersine fail-open'dır — tarama döngüsü düşmemeli, bkz.
            # engine._evaluate_symbol.)
            state = structure_state_for(ctx, cfg)
            if structure_gate_blocks(state, raw_signal.direction, cfg):
                if missed_counter is not None:
                    missed_counter["structure_gate"] = (
                        missed_counter.get("structure_gate", 0) + 1
                    )
                i += 1
                continue

        # Canlı parite (2026-09-03; hafta günü + sembol×yön 2026-09-04 eklendi):
        # genel deterministik giriş kapıları — rejim×yön hücresi / UTC saat
        # penceresi / hafta günü×yön / sembol×yön / ATR% bandı — motordaki
        # (engine._evaluate_symbol) sırayla birebir: yapı kapılarının HEMEN
        # ardında, apply_stop_policy'den ÖNCE, AYNI saf fonksiyon
        # (entry_gates.evaluate_entry_gates) AYNI argümanlarla:
        #   * zaman = bu mumun close_time'ı (close_times_5m[i]; piyasa kapısının
        #     kesim zamanıyla aynı değer — duvar saati YOK; hafta günü kapısı
        #     da AYNI değeri kullanır),
        #   * ATR% = HAM sinyal (raw_signal; apply_stop_policy ÖNCESİ),
        #   * sembol = bu koşunun `symbol` argümanı (motorda `_evaluate_symbol`'ün
        #     kendi `symbol` argümanı — sembol×yön kapısı için).
        # Beşi de varsayılan KAPALI: kapalıyken None döner, missed_counter'a
        # anahtar bile EKLENMEZ (tests/test_golden_backtest.py byte-for-byte).
        # İstisna BİLİNÇLİ olarak yutulmaz (yapı kapısındaki gerekçe): hatalı
        # bir ayar sessizce "kapı kapalı" ölçümü üretmektense patlamalı.
        entry_gate_reason = evaluate_entry_gates(
            ctx.regime,
            raw_signal.direction,
            close_times_5m[i],
            raw_signal.atr_5m,
            raw_signal.entry_price,
            symbol,
            cfg,
        )
        # D42: strateji C'ye özel bağlam-RSI + ATR% bandı koruması (KAPALI;
        # motorla AYNI fonksiyon/girdiler: ctx.candles_15m = bağlam dilimi).
        if entry_gate_reason is None:
            entry_gate_reason = c_entry_guard_verdict(
                getattr(raw_signal, "strategy", None),
                raw_signal.direction,
                ctx.candles_15m,
                raw_signal.atr_5m,
                raw_signal.entry_price,
                cfg,
            )[0]
        if entry_gate_reason is not None:
            if missed_counter is not None:
                missed_counter[entry_gate_reason] = (
                    missed_counter.get(entry_gate_reason, 0) + 1
                )
            i += 1
            continue

        signal = apply_stop_policy(raw_signal, cfg)

        timed_out_before = mstats.get("timed_out", 0)
        pos = open_position(
            signal, candles_5m, i, cfg, initial_balance,
            missed_counter=missed_counter,
            entry_delay_candles=entry_delay_candles,
            exit_realism=exit_realism,
            maker_realism=maker_realism,
            maker_stats=mstats,
            risk_realism=risk_realism,
        )
        if pos is None:
            if maker_realism.single_pending and mstats.get("timed_out", 0) > timed_out_before:
                # Canlı parite (executor `symbol in self._pending`): emir
                # sinyal mumundan timeout mum sonrasına kadar bekler ve bu
                # sürede aynı sembole yeni emir gitmez. Sonraki sinyal
                # değerlendirmesi pencerenin ARDINDAKİ mumda.
                i += maker_timeout + max(0, int(entry_delay_candles or 0)) + 1
            else:
                i += 1
            continue

        trade = manage_position(pos, candles_5m, cfg, structure_feed=structure_feed)
        trades.append(trade)
        if loss_cooldown_ms > 0 and (trade.exit_reason == "SL" or trade.pnl < 0.0):
            cooldown_until_ms = trade.exit_time + loss_cooldown_ms
        i = trade.exit_idx + 1

    return trades


# ==========================================================================
# İstatistik / rapor
# ==========================================================================


def bar_equity_series(
    trades: List[BacktestTrade], adverse: bool = False
) -> List[Tuple[int, float]]:
    """D24/A3 — tüm sembollerin BAR-BAZLI mark-to-market özkaynak eğrisi.

    Bugünkü `max_drawdown` yalnız İŞLEM KAPANIŞLARINDA örneklenen kümülatif
    PnL'den hesaplanıyor: açık pozisyonların bar-içi çukurunu HİÇ görmüyor ve
    gerçek çöküşü olduğundan SIĞ gösteriyor. 1000 USD sermaye ve %10/işlem
    sabit boyutta bu doğrudan bir HAYATTA KALMA sorusudur.

    Her işlem kendi bar işaretlerini (`equity_marks`) taşır; bir işlem
    kapandıktan sonra katkısı `pnl` değerinde SABİT kalır (gerçekleşmiştir).
    Bu fonksiyon işaretleri zaman sırasına dizip her zaman damgasında toplam
    portföy K/Z'sini verir. Başlangıç 0.0'dır (mutlak sermaye değil, GÖRECELİ
    özkaynak) — mevcut `max_drawdown` ile aynı taban, doğrudan kıyaslanabilir.

    İşaret taşımayan işlemler (ör. elle kurulmuş test nesneleri) sessizce
    atlanır ve seri boş dönebilir — bu bir hata değil, "ölçülmedi"dir.

    `adverse=True` (A3): açık pozisyonlar her barda AŞIRI UÇ fiyatında
    (LONG low / SHORT high) işaretlenir. Aynı bardaki eşzamanlı pozisyonların
    aşırı uçları AYNI ANDA yaşandı varsayılır (kötümser, üst sınır).
    """
    events: List[Tuple[int, int, float]] = []
    for index, trade in enumerate(trades):
        marks = trade.equity_marks_adverse if adverse else trade.equity_marks
        for close_time, value in (marks or ()):
            events.append((int(close_time), index, float(value)))
    if not events:
        return []

    events.sort(key=lambda e: (e[0], e[1]))
    contribution: Dict[int, float] = {}
    total = 0.0
    out: List[Tuple[int, float]] = []
    i = 0
    count = len(events)
    while i < count:
        stamp = events[i][0]
        while i < count and events[i][0] == stamp:
            _, index, value = events[i]
            total += value - contribution.get(index, 0.0)
            contribution[index] = value
            i += 1
        out.append((stamp, total))
    return out


def drawdown_percent(
    values: Sequence[float], initial_balance: float,
    peaks: Optional[Sequence[float]] = None,
) -> Tuple[float, float]:
    """Göreli özkaynak değerlerinden (başlangıç 0) en derin çöküşün yüzdeleri:
    (başlangıç bakiyesinin %'si, tepe özkaynağın %'si). Tepe özkaynak =
    başlangıç + o ana kadarki en yüksek göreli K/Z; iki yüzde AYRI çukurlarda
    maksimum olabilir.

    `peaks` (verilirse `values` ile HİZALI): her noktadaki tepe değer dışarıdan
    gelir (adverse çöküş için kapanış-işaretli seriden); verilmezse tepe
    `values`'un kendi koşan maksimumudur."""
    base = float(initial_balance)
    peak = 0.0
    worst = 0.0
    worst_pct_peak = 0.0
    for i, v in enumerate(values):
        peak = max(peak, v)
        if peaks is not None:
            peak = max(peak, peaks[i])
        drop = peak - v
        worst = max(worst, drop)
        peak_equity = base + peak
        if peak_equity > 0:
            worst_pct_peak = max(worst_pct_peak, drop / peak_equity * 100.0)
    pct_initial = (worst / base * 100.0) if base > 0 else 0.0
    return pct_initial, worst_pct_peak


def running_peaks_at(
    series: Sequence[Tuple[int, float]], peak_source: Sequence[Tuple[int, float]]
) -> List[float]:
    """`series`'in her damgasında, `peak_source` (kapanış-işaretli seri) içinde
    o damga DAHİL koşan maksimum (0.0 tabanlı). Adverse çöküşün tepesi için:
    tepe, çukurla aynı (kötümser) seriden alınırsa fitilli işlemde tepe de düşer
    ve adverse çöküş kapanış-bazlıdan küçük çıkabilir."""
    out: List[float] = []
    j = 0
    peak = 0.0
    n = len(peak_source)
    for stamp, _ in series:
        while j < n and peak_source[j][0] <= stamp:
            peak = max(peak, peak_source[j][1])
            j += 1
        out.append(peak)
    return out


def bar_drawdown(
    series: Sequence[Tuple[int, float]],
    peaks: Optional[Sequence[float]] = None,
) -> Tuple[float, Optional[int]]:
    """Bar-bazlı özkaynak eğrisinden en derin tepe→dip çöküşü.

    Döner: (çöküş büyüklüğü, çukurun zaman damgası). Tepe 0.0'dan başlar —
    `compute_stats`'taki kapanış-bazlı `max_drawdown` ile aynı taban.
    `peaks` (verilirse `series` ile hizalı): tepe dışarıdan gelir
    (`running_peaks_at`); tepe yine `series`'in kendi değerlerini de içerir.
    """
    peak = 0.0
    worst = 0.0
    worst_at: Optional[int] = None
    for i, (stamp, equity) in enumerate(series):
        if peaks is not None and peaks[i] > peak:
            peak = peaks[i]
        if equity > peak:
            peak = equity
        drop = peak - equity
        if drop > worst:
            worst = drop
            worst_at = stamp
    return worst, worst_at


def _utc_day(timestamp_ms: Any) -> str:
    """epoch ms -> 'YYYY-MM-DD' (UTC). Çözülemezse boş dize."""
    try:
        return datetime.fromtimestamp(
            int(timestamp_ms) / 1000.0, tz=timezone.utc
        ).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def concentration_stats(trades: List[BacktestTrade]) -> Dict[str, Any]:
    """D24/A4 — konsantrasyon: kâr TEK bir sembolden/işlemden/günden mi geldi?

    Bu tespiti canlı defterde ELLE bir kez yapmıştık ("+832'nin %68'i 4
    yükseliş gününden"); burada sistematikleşiyor. `compute_stats` bugün
    yalnız strateji/rejim/yön/çıkış kırılımı üretiyor — sembol ve GÜN
    kırılımı yok.

    Pay tanımı (dürüstlük): pay = en büyük katkı / toplam PnL, YALNIZ toplam
    PnL POZİTİFKEN tanımlıdır. Toplam sıfır ya da negatifken "kârın payı"
    sorusu anlamsızdır → pay None döner ama MUTLAK katkı yine raporlanır
    (sayı kaybolmasın). Bu alanlar bir EŞİK değil BİLGİ satırıdır: P2 karar
    kuralına girmezler (aksi halde D#P1 harness/motor paritesi tartışması
    açılır — bkz. docs/DECISIONS.md).
    """
    empty: Dict[str, Any] = {
        "top_symbol": None, "top_symbol_pnl": 0.0, "top_symbol_pnl_share": None,
        "top_trade_pnl": 0.0, "top_trade_pnl_share": None,
        "top_day": None, "top_day_pnl": 0.0, "top_day_pnl_share": None,
        "distinct_symbols": 0, "distinct_days": 0,
    }
    if not trades:
        return empty

    total_pnl = sum(t.pnl for t in trades)
    by_symbol: Dict[str, float] = {}
    by_day: Dict[str, float] = {}
    for t in trades:
        by_symbol[t.symbol] = by_symbol.get(t.symbol, 0.0) + t.pnl
        day = _utc_day(t.exit_time)
        if day:
            by_day[day] = by_day.get(day, 0.0) + t.pnl

    def _share(value: float) -> Optional[float]:
        if total_pnl <= 0.0 or value <= 0.0:
            return None
        return round(value / total_pnl * 100.0, 2)

    top_symbol, top_symbol_pnl = (
        max(by_symbol.items(), key=lambda kv: kv[1]) if by_symbol else (None, 0.0)
    )
    top_day, top_day_pnl = (
        max(by_day.items(), key=lambda kv: kv[1]) if by_day else (None, 0.0)
    )
    top_trade_pnl = max(t.pnl for t in trades)

    return {
        "top_symbol": top_symbol,
        "top_symbol_pnl": round(top_symbol_pnl, 4),
        "top_symbol_pnl_share": _share(top_symbol_pnl),
        "top_trade_pnl": round(top_trade_pnl, 4),
        "top_trade_pnl_share": _share(top_trade_pnl),
        "top_day": top_day,
        "top_day_pnl": round(top_day_pnl, 4),
        "top_day_pnl_share": _share(top_day_pnl),
        "distinct_symbols": len(by_symbol),
        "distinct_days": len(by_day),
    }


def compute_stats(
    trades: List[BacktestTrade], initial_balance: float = _DEFAULT_VIRTUAL_BALANCE
) -> Dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {
            "trades": 0, "wins": 0, "winrate": 0.0, "total_pnl": 0.0, "avg_roi": 0.0,
            "profit_factor": 0.0, "max_consec_losses": 0, "max_drawdown": 0.0,
            "avg_duration_min": 0.0, "avg_mae": 0.0, "avg_mfe": 0.0,
            # D24: EK alanlar — rapor ŞEKLİ boş koşuda da sabit kalmalı.
            "bar_max_drawdown": 0.0, "bar_max_drawdown_at": None,
            "bar_equity_points": 0,
            # A3: çöküş yüzdeleri, bar-içi aşırı uç çöküşü, funding, likidasyon.
            "max_drawdown_pct_initial": 0.0, "max_drawdown_pct_peak": 0.0,
            "bar_max_drawdown_pct_initial": 0.0, "bar_max_drawdown_pct_peak": 0.0,
            "bar_max_drawdown_adverse": 0.0, "bar_max_drawdown_adverse_at": None,
            "bar_max_drawdown_adverse_pct_initial": 0.0,
            "bar_max_drawdown_adverse_pct_peak": 0.0,
            "total_funding": 0.0, "funding_settlements": 0,
            "liquidations": 0, "stop_beyond_liquidation": 0,
            **concentration_stats([]),
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
    closed_curve: List[float] = []
    for t in ordered:
        cum += t.pnl
        closed_curve.append(cum)
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    dd_pct_initial, dd_pct_peak = drawdown_percent(closed_curve, initial_balance)

    # D24/A3 — bar-bazlı (mark-to-market) çöküş. Kapanış-bazlı `max_drawdown`
    # açık pozisyonların bar-içi çukurunu göremez; bu ikinci sayı ONU ölçer ve
    # tanım gereği kapanış-bazlıdan KÜÇÜK OLAMAZ (aynı taban, daha sık örnekleme).
    series = bar_equity_series(ordered)
    bar_max_dd, bar_max_dd_at = bar_drawdown(series)
    bar_pct_initial, bar_pct_peak = drawdown_percent([v for _, v in series], initial_balance)
    adv_series = bar_equity_series(ordered, adverse=True)
    # Tepe KAPANIŞ-işaretli seriden, çukur adverse seriden: aksi halde fitilli
    # işlemde tepe de düşer ve adverse çöküş kapanış-bazlıdan küçük çıkar.
    adv_peaks = running_peaks_at(adv_series, series)
    adv_dd, adv_dd_at = bar_drawdown(adv_series, adv_peaks)
    adv_pct_initial, adv_pct_peak = drawdown_percent(
        [v for _, v in adv_series], initial_balance, adv_peaks
    )

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
        "bar_max_drawdown": bar_max_dd,
        "bar_max_drawdown_at": bar_max_dd_at,
        "bar_equity_points": len(series),
        "max_drawdown_pct_initial": dd_pct_initial,
        "max_drawdown_pct_peak": dd_pct_peak,
        "bar_max_drawdown_pct_initial": bar_pct_initial,
        "bar_max_drawdown_pct_peak": bar_pct_peak,
        "bar_max_drawdown_adverse": adv_dd,
        "bar_max_drawdown_adverse_at": adv_dd_at,
        "bar_max_drawdown_adverse_pct_initial": adv_pct_initial,
        "bar_max_drawdown_adverse_pct_peak": adv_pct_peak,
        "total_funding": sum(t.funding for t in ordered),
        "funding_settlements": sum(t.funding_settlements for t in ordered),
        "liquidations": sum(1 for t in ordered if t.exit_reason == "LIQUIDATION"),
        "stop_beyond_liquidation": sum(1 for t in ordered if t.stop_beyond_liq),
        **concentration_stats(ordered),
    }


def funding_report(trades: List[BacktestTrade]) -> Dict[str, Any]:
    """JSON raporundaki "funding": toplam + sembol başına tarihsel/yedek
    tahsilat sayısı (yedek oran = önbellek o anı kapsamıyor)."""
    per_symbol: Dict[str, Dict[str, Any]] = {}
    for t in trades:
        row = per_symbol.setdefault(
            t.symbol,
            {"historical": 0, "fallback": 0, "settlements": 0, "total_funding": 0.0},
        )
        row["historical"] += t.funding_historical
        row["fallback"] += t.funding_fallback
        row["settlements"] += t.funding_settlements
        row["total_funding"] += t.funding
    return {
        "total_funding": sum(t.funding for t in trades),
        "settlements": sum(t.funding_settlements for t in trades),
        "per_symbol": per_symbol,
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

    if overall["funding_settlements"] or overall["liquidations"] or overall["stop_beyond_liquidation"]:
        print(
            f"Funding: toplam {overall['total_funding']:.2f} USD "
            f"({overall['funding_settlements']} tahsilat) | likidasyon: "
            f"{overall['liquidations']} | stopu likidasyon ötesinde açılan: "
            f"{overall['stop_beyond_liquidation']}"
        )
    print(
        f"Çöküş: kapanış-bazlı %{overall['max_drawdown_pct_initial']:.2f} başlangıç / "
        f"%{overall['max_drawdown_pct_peak']:.2f} tepe | bar-içi aşırı uç "
        f"{overall['bar_max_drawdown_adverse']:.2f} USD "
        f"(%{overall['bar_max_drawdown_adverse_pct_initial']:.2f} başlangıç)"
    )

    print()
    _print_regime_breakdown(all_trades)
    _print_grouped_breakdown(
        "YÖN KIRILIMI (LONG/SHORT)", "Yön", _group_by_strategy_direction(all_trades),
    )
    _print_grouped_breakdown(
        "ÇIKIŞ NEDENİ KIRILIMI", "Çıkış", _group_by_strategy_exit(all_trades),
    )
    _print_risk_concentration(overall)


def _print_risk_concentration(stats: Dict[str, Any]) -> None:
    """D24/A3+A4 — bar-bazlı çöküş ve konsantrasyon satırları.

    İkisi de BİLGİ satırıdır: hiçbir karar kuralına eşik olarak girmez.
    """
    print("\n" + "-" * 78)
    print("RİSK YOĞUNLUĞU (D24 — bilgi satırı, karar eşiği DEĞİL)")
    print("-" * 78)
    closed_dd = float(stats.get("max_drawdown") or 0.0)
    bar_dd = float(stats.get("bar_max_drawdown") or 0.0)
    if stats.get("bar_equity_points"):
        extra = (
            f"{bar_dd / closed_dd:.2f}× daha derin"
            if closed_dd > 0 else "kapanış-bazlı ÇÖKÜŞ GÖRMÜYOR (0.00)"
        )
        print(
            f"Çöküş  : kapanış-bazlı {closed_dd:.2f} | bar-bazlı {bar_dd:.2f} "
            f"({extra}; {stats.get('bar_equity_points')} bar işareti, çukur "
            f"{_ms_to_utc_iso(stats['bar_max_drawdown_at']) if stats.get('bar_max_drawdown_at') else '—'})"
        )
    else:
        print("Çöküş  : bar-bazlı ölçüm YOK (işlemler bar işareti taşımıyor)")

    def _share(value: Any) -> str:
        return "—" if value is None else f"%{float(value):.1f}"

    print(
        f"Sembol : en iyi {stats.get('top_symbol') or '—'} "
        f"{float(stats.get('top_symbol_pnl') or 0.0):.2f} "
        f"({_share(stats.get('top_symbol_pnl_share'))} kârın) / "
        f"{stats.get('distinct_symbols', 0)} sembol"
    )
    print(
        f"İşlem  : en iyi tek işlem "
        f"{float(stats.get('top_trade_pnl') or 0.0):.2f} "
        f"({_share(stats.get('top_trade_pnl_share'))} kârın)"
    )
    print(
        f"Gün    : en iyi {stats.get('top_day') or '—'} "
        f"{float(stats.get('top_day_pnl') or 0.0):.2f} "
        f"({_share(stats.get('top_day_pnl_share'))} kârın) / "
        f"{stats.get('distinct_days', 0)} gün"
    )
    print(
        "Not: pay YALNIZ toplam PnL pozitifken tanımlıdır; '—' = tanımsız "
        "(kâr yok), 'ölçülmedi' DEĞİL."
    )
    print("-" * 78)


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
    permutation: Optional[Dict[str, Any]] = None,
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
        # D24/A5: fiilen uygulanan maliyet modeli ve giriş gecikmesi. Anahtar
        # HER ZAMAN vardır (varsayılan koşuda çarpan 1.0, gecikme 0) ki iki
        # rapor yan yana konduğunda "hangisi stres koşusuydu" sorusu rapordan
        # okunabilsin.
        "cost_model": metadata.get("cost_model", {
            "stress_multiplier": 1.0,
            "taker_fee_pct": getattr(cfg, "scalper_taker_fee_pct", None),
            "maker_fee_pct": getattr(cfg, "scalper_maker_fee_pct", None),
            "slippage_rate": slippage_rate(cfg),
            "entry_delay_candles": 0,
        }),
        # Gerçekçilik anahtarları: açık/kapalı özellikler + sayısal parametreler.
        "realism": metadata.get("realism", realism_report(cfg)),
        # Kapı açıkken lider serisinin kimliği/kapsamı; kapalıyken None
        # (rapor ŞEKLİ sabit kalsın diye anahtar her zaman var).
        "market_gate": metadata.get("market_gate"),
    }

    payload = {
        "generated_at": _utc_iso_now(),
        "params": {"days": days, "symbols": symbols, "strategies": strategy_names},
        "provenance": provenance,
        "overall": compute_stats(all_trades),
        "by_strategy": {name: compute_stats(ts) for name, ts in by_strategy.items()},
        "regime_breakdown": regime_breakdown,
        "missed_signals": dict(missed_counter) if missed_counter is not None else {},
        # A2: maker emir sayaçları (taker modda hepsi 0, fill_rate None).
        "maker_orders": metadata.get("maker_orders", maker_orders_report(None)),
        # A3: funding (sembol başına tarihsel/yedek oran sayısı) ve likidasyon.
        "funding": funding_report(all_trades),
        # A4: günlük kayıp mandalı (kapalıysa/ölçülmediyse None).
        "daily_latch": metadata.get("daily_latch"),
        "liquidation": {
            "liquidations": sum(1 for t in all_trades if t.exit_reason == "LIQUIDATION"),
            "stop_beyond_liquidation": sum(1 for t in all_trades if t.stop_beyond_liq),
            "margin_mode": "ISOLATED",
        },
        # D24/A1: Monte-Carlo permütasyon sonucu (yalnız --permutations ile
        # koşulduysa dolu; aksi halde None — "ölçülmedi").
        "permutation": permutation,
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


def _apply_capacity_gate(
    trades: List[BacktestTrade],
    symbols: List[str],
    cfg: Any,
    missed_counter: Optional[Dict[str, int]] = None,
) -> List[BacktestTrade]:
    """Canlı motorun kapasite kapısıyla PARİTE (2026-08-21).

    `engine._evaluate_symbol` yeni girişi yalnız `len(tracked | pending) <
    scalper_max_positions` iken açar (kural tur başında VE try_open'dan
    hemen önce iki kez doğrulanır — engine.py `_scan_tick`/`_evaluate_symbol`,
    satır ~1135 ve ~1252). `simulate_symbol` her sembolü BAĞIMSIZ simüle
    ettiğinden (küresel saat YOK — modül docstring'i) bu kapıyı sembol-içi
    döngüde uygulamak mümkün değil; en az müdahaleci yol tüm sembollerin
    aday işlemleri birleştirildikten SONRA, giriş zamanına göre KRONOLOJİK
    tek bir geçiş yapmaktır: açık aday sayısı `scalper_max_positions`'a
    ulaştığında yeni giriş REDDEDİLİR (`missed_counter["capacity"]`) ve
    sonuç kümesine hiç girmez. Eşitlikte (aynı 5m mumunda birden çok sembol
    sinyali) `symbols` argümanındaki sıra kullanılır — canlı taramanın
    `self._universe` döngü sırasına karşılık gelir. Bir pozisyon tam
    `exit_time`'ında kapanıyorsa slot AYNI ANDA boşalmış sayılır (canlı
    motorda da REST pozisyon özeti bir sonraki tur başında tazedir).

    BİLİNEN SAPMALAR (kapsam dışı bırakıldı — görev notu):
    1) Canlı motorda kapasite, emrin GÖNDERİLDİĞİ (pending) andan itibaren
       dolar; maker modda hiç dolmayan bir emir de bekleme süresince bir
       slot işgal eder. Backtest'te dolmayan maker sinyalleri hiçbir zaman
       BacktestTrade'e dönüşmediğinden (bkz. `missed_counter["maker_missed"]`,
       `_find_maker_fill`) bu bekleme penceresi burada modellenmiyor.
    2) Bir aday kapasite yüzünden reddedildiğinde, o sembolün
       `simulate_symbol` içinde ÜRETİLMİŞ sonraki sinyalleri (özellikle
       `scalper_loss_cooldown_minutes` soğuması) yine de reddedilen işlem
       GERÇEKLEŞMİŞ gibi hesaba katılmıştır — sembol simülasyonu
       kapasiteden habersiz, kendi başına tamamlanır. Tam parite; kapasiteyi
       BİLEN tek bir küresel event-driven simülasyona geçmeyi gerektirir.
    """
    max_positions_raw = getattr(cfg, "scalper_max_positions", 3)
    max_positions = int(max_positions_raw) if max_positions_raw is not None else 3

    order_index = {sym: idx for idx, sym in enumerate(symbols)}
    ordered = sorted(
        trades,
        key=lambda t: (t.entry_time, order_index.get(t.symbol, len(symbols)), t.symbol),
    )

    accepted: List[BacktestTrade] = []
    open_exit_times: List[int] = []  # min-heap: açık adayların exit_time'ları
    for trade in ordered:
        while open_exit_times and open_exit_times[0] <= trade.entry_time:
            heapq.heappop(open_exit_times)

        if len(open_exit_times) >= max_positions:
            if missed_counter is not None:
                missed_counter["capacity"] = missed_counter.get("capacity", 0) + 1
            continue

        heapq.heappush(open_exit_times, trade.exit_time)
        accepted.append(trade)

    return accepted


def _utc_day_index(ms: int) -> int:
    return int(ms) // 86_400_000


def apply_daily_latch(
    trades: List[BacktestTrade],
    cfg: Any,
    *,
    capital: float = _DEFAULT_VIRTUAL_BALANCE,
    missed_counter: Optional[Dict[str, int]] = None,
) -> Tuple[List[BacktestTrade], Dict[str, Any]]:
    """Canlı günlük kayıp kesicisiyle PARİTE (A4, parity-1).

    Canlı kural (`engine._update_kill_switch`, engine.py:5413-5531):
    `scalper_daily_loss_limit_pct > 0` iken UTC günü içindeki GERÇEKLEŞMİŞ
    (kapanmış işlem) PnL `<= -(gün başı sermaye) * limit/100` olunca
    `_kill_switch` o günün sonuna kadar kilitlenir (yeni giriş yok, açık
    pozisyonların çıkışı sürer); ertesi UTC günü sıfırlanır. Gün başı sermaye
    canlıda `risk_sermayesi - bugünkü_pnl`'dir (sanal scalper kasası ya da
    cüzdan); harness'ta boyutlama sabit `_DEFAULT_VIRTUAL_BALANCE` üzerinden
    yapıldığından eşik `-capital * limit / 100`'dür (bileşiksiz).

    Zaman sıralı tek geçiş: işlemler `exit_time` sırasıyla işlenir, gün PnL'i
    çıkış gününe yazılır (canlı defter de kapanış gününe yazar). Eşik aşılınca
    o günün mandal zamanı = o çıkışın `exit_time`'ı. Bir işlem, KARAR zamanı
    (`signal_close_time`, yoksa `entry_time`) mandal gününde ve mandal
    zamanından önce DEĞİLSE düşer (maker emri mandaldan önce verildiyse canlıda
    bekler/dolar; bu yüzden dolum anı değil sinyal anı esas). Düşen işlemin
    PnL'i hiçbir güne yazılmaz.

    BİLİNEN SAPMALAR: kısmi TP dolumları gün PnL'ine kapanış anında girer
    (canlı gömülü mod da böyle; income yolu kısmi dolumları anında sayar);
    yalnız kapanmış işlemler sayılır, açık pozisyonun anlık zararı sayılmaz
    (canlıda da).
    """
    try:
        limit = float(getattr(cfg, "scalper_daily_loss_limit_pct", 0.0) or 0.0)
    except (TypeError, ValueError):
        limit = 0.0
    report: Dict[str, Any] = {
        "enabled": limit > 0,
        "limit_pct": limit,
        "capital": float(capital),
        "threshold": (-float(capital) * limit / 100.0) if limit > 0 else None,
        "latched_days": [],
        "dropped_trades": 0,
        "dropped_pnl": 0.0,
    }
    if limit <= 0:
        return list(trades), report

    threshold = -float(capital) * limit / 100.0
    day_pnl: Dict[int, float] = {}
    latch_time: Dict[int, int] = {}
    dropped_ids: set = set()
    order = sorted(
        range(len(trades)),
        key=lambda i: (trades[i].exit_time, trades[i].entry_time, trades[i].symbol),
    )
    for i in order:
        t = trades[i]
        decision = t.signal_close_time if t.signal_close_time else t.entry_time
        d_day = _utc_day_index(decision)
        if d_day in latch_time and decision >= latch_time[d_day]:
            dropped_ids.add(i)
            continue
        x_day = _utc_day_index(t.exit_time)
        if x_day in latch_time:
            continue  # gün zaten kilitli; PnL'in artık önemi yok
        day_pnl[x_day] = day_pnl.get(x_day, 0.0) + t.pnl
        if day_pnl[x_day] <= threshold:
            latch_time[x_day] = t.exit_time
            report["latched_days"].append({
                "day": _ms_to_utc_iso(x_day * 86_400_000)[:10],
                "latch_time_ms": int(t.exit_time),
                "day_pnl": round(day_pnl[x_day], 6),
            })

    kept = [t for i, t in enumerate(trades) if i not in dropped_ids]
    report["dropped_trades"] = len(dropped_ids)
    report["dropped_pnl"] = round(sum(trades[i].pnl for i in dropped_ids), 6)
    if missed_counter is not None and dropped_ids:
        missed_counter["daily_latch"] = (
            missed_counter.get("daily_latch", 0) + len(dropped_ids)
        )
    return kept, report


def _apply_joint_gates(
    trades: List[BacktestTrade],
    symbols: List[str],
    cfg: Any,
    missed_counter: Optional[Dict[str, int]] = None,
    *,
    capital: float = _DEFAULT_VIRTUAL_BALANCE,
) -> Tuple[List[BacktestTrade], Dict[str, Any]]:
    """Kapasite kapısı + günlük mandal TEK kronolojik geçişte (denetim bulgusu).

    Önce kapasite sonra mandal uygulanınca mandalın düşüreceği (canlıda HİÇ
    açılmayacak) işlem kapasite slotu tutuyor ve ondan sonraki geçerli bir
    girişi haksız reddettiriyordu. Burada adaylar `_apply_capacity_gate` ile
    AYNI sırayla (giriş zamanı, sembol sırası) gezilir; her aday için ÖNCE
    mandal (karar zamanında, o UTC gününde kabul edilmiş işlemlerin
    `exit_time <= karar` olanlarıyla `apply_daily_latch` kuralı), SONRA
    kapasite sınanır. Düşen/reddedilen aday ne slot ne gün PnL'i tutar.
    Mandal gecikmesiz doğrudur: karar zamanından sonra çıkan işlem o kararı
    etkileyemez (çıkış > giriş >= karar).
    Döner: (kabul edilenler, `apply_daily_latch` biçiminde rapor).
    """
    max_positions_raw = getattr(cfg, "scalper_max_positions", 3)
    max_positions = int(max_positions_raw) if max_positions_raw is not None else 3
    limit = float(getattr(cfg, "scalper_daily_loss_limit_pct", 0.0) or 0.0)
    threshold = -float(capital) * limit / 100.0

    order_index = {sym: idx for idx, sym in enumerate(symbols)}
    ordered = sorted(
        trades,
        key=lambda t: (t.entry_time, order_index.get(t.symbol, len(symbols)), t.symbol),
    )

    accepted: List[BacktestTrade] = []
    open_exit_times: List[int] = []
    # gün -> kabul edilmiş işlemlerin (exit_time, entry_time, symbol, pnl) listesi
    by_exit_day: Dict[int, List[Tuple[int, int, str, float]]] = {}
    dropped_count = 0
    dropped_pnl = 0.0

    def _latch_time(day: int, decision: int) -> Optional[int]:
        cum = 0.0
        for exit_time, _entry, _sym, pnl in by_exit_day.get(day, ()):
            if exit_time > decision:
                break
            cum += pnl
            if cum <= threshold:
                return exit_time
        return None

    for trade in ordered:
        decision = trade.signal_close_time if trade.signal_close_time else trade.entry_time
        lt = _latch_time(_utc_day_index(decision), decision)
        if lt is not None and decision >= lt:
            dropped_count += 1
            dropped_pnl += trade.pnl
            if missed_counter is not None:
                missed_counter["daily_latch"] = missed_counter.get("daily_latch", 0) + 1
            continue

        while open_exit_times and open_exit_times[0] <= trade.entry_time:
            heapq.heappop(open_exit_times)
        if len(open_exit_times) >= max_positions:
            if missed_counter is not None:
                missed_counter["capacity"] = missed_counter.get("capacity", 0) + 1
            continue

        heapq.heappush(open_exit_times, trade.exit_time)
        accepted.append(trade)
        bisect.insort(
            by_exit_day.setdefault(_utc_day_index(trade.exit_time), []),
            (int(trade.exit_time), int(trade.entry_time), trade.symbol, float(trade.pnl)),
        )

    # Kabul edilenler üzerinde kural kendiliğinden tutarlıdır (hiçbiri düşmez);
    # `apply_daily_latch` yalnız mandal GÜNLERİNİ raporlamak için çağrılır.
    kept, rep = apply_daily_latch(accepted, cfg, capital=capital)
    rep["dropped_trades"] = dropped_count
    rep["dropped_pnl"] = round(dropped_pnl, 6)
    return kept, rep


def _portfolio_filter(
    trades: List[BacktestTrade],
    symbols: List[str],
    cfg: Any,
    missed_counter: Optional[Dict[str, int]] = None,
    disabled_realism: Any = None,
    report_out: Optional[Dict[str, Any]] = None,
) -> List[BacktestTrade]:
    """Portföy kapıları: kapasite (max pozisyon) ve günlük kayıp mandalı.

    Mandal açıkken ikisi TEK kronolojik geçişte uygulanır (`_apply_joint_gates`):
    sıra sonuç için FARK EDER — mandalın düşüreceği işlem slot tutmamalıdır.
    Mandal kapalıysa (özellik kapalı ya da limit <= 0) yalnız kapasite kapısı
    çalışır."""
    latch_on = "daily_latch" not in normalize_disabled_realism(disabled_realism)
    try:
        limit = float(getattr(cfg, "scalper_daily_loss_limit_pct", 0.0) or 0.0)
    except (TypeError, ValueError):
        limit = 0.0
    if not latch_on or limit <= 0:
        kept = _apply_capacity_gate(trades, symbols, cfg, missed_counter=missed_counter)
        if latch_on:
            kept, rep = apply_daily_latch(kept, cfg, missed_counter=missed_counter)
            if report_out is not None:
                report_out["daily_latch"] = rep
        return kept
    kept, rep = _apply_joint_gates(trades, symbols, cfg, missed_counter=missed_counter)
    if report_out is not None:
        report_out["daily_latch"] = rep
    return kept


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
    cache_dir: Optional[str | Path] = None,
    refresh: bool = False,
    entry_delay_candles: int = 0,
    series_out: Optional[Dict[str, Dict[str, List[Candle]]]] = None,
    leader_out: Optional[List[Optional[LeaderSeries]]] = None,
    disabled_realism: Any = None,
    exit_slippage_rate: Optional[float] = None,
    maker_trade_through_pct: Optional[float] = None,
    funding_fallback_pct: Optional[float] = None,
    funding_cache_dir: Optional[str | Path] = None,
) -> List[BacktestTrade]:
    """Verilen semboller için tarihsel veriyi çeker ve tüm stratejileri
    simüle eder; tüm işlemleri (tüm semboller birleşik) döndürür.

    `entry_delay_candles` (D24/A5, varsayılan 0) girişin N mum GEÇ dolduğu
    çürütme senaryosunu koşar; 0 iken davranış birebir aynıdır.
    `series_out` verilirse (mutasyonla) çekilen mum serileri sembol başına
    buraya yazılır — `run_permutation_study` aynı veriyi TEKRAR ÇEKMEDEN
    (sıfır ek Binance ağırlığı) permüte edebilsin diye. `leader_out` verilirse
    (mutasyonla) lider serisi oraya eklenir (kapı kapalıysa None) ki permüte
    koşular da GERÇEK koşuyla AYNI piyasa kapısını uygulasın — aksi halde
    null dağılımı kapısız koşulur ve kıyas taraflı olur.

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

    `cache_dir` verilirse `gather_symbol_data`'ya aynen iletilir — her sembol/
    aralık serisi diskte gzip JSON olarak saklanır ve sonraki aynı-pencereli
    koşular Binance'e gitmez (bkz. kline_cache modülü). `refresh=True`
    önbelleği yok sayıp taze çeker ve üzerine yazar. Varsayılan `cache_dir=None`
    ile davranış eskisiyle birebir aynıdır (her koşu Binance'e gider).
    """
    if start_time_ms is None and days <= 0:
        raise ValueError("Backtest gün sayısı pozitif olmalı")
    # Gerçekçilik anahtarları (REALISM_FEATURES): bilinmeyen ad burada patlar.
    disabled_realism = normalize_disabled_realism(disabled_realism)

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
        # D24/A5 — fiilen uygulanan maliyet modeli. `stress_multiplier`
        # burada 1.0'dır: stres, cfg'nin kendisi `stressed_cfg` ile
        # çarpılarak uygulanır (çarpanı CLI `main_async` yazar).
        "cost_model": {
            "stress_multiplier": 1.0,
            "taker_fee_pct": getattr(cfg, "scalper_taker_fee_pct", None),
            "maker_fee_pct": getattr(cfg, "scalper_maker_fee_pct", None),
            "slippage_rate": slippage_rate(cfg),
            "entry_delay_candles": int(entry_delay_candles or 0),
        },
        "realism": realism_report(
            cfg, disabled_realism, exit_slippage_rate, maker_trade_through_pct,
            funding_fallback_pct, funding_cache_dir,
        ),
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
        # ⚠️ Buradan sonra `metadata` ve `run_metadata` AYNI NESNE olmalı.
        # Aksi hâlde yalnız İÇ İÇE sözlüklere yapılan yerinde değişiklikler
        # (ör. `metadata["data_windows"][symbol] = ...`) dışarı ulaşır; SONRADAN
        # eklenen YENİ bir anahtar (ör. `metadata["market_gate"] = ...`) sessizce
        # kaybolurdu. 2026-08-23'te tam olarak bu oldu: kapı metadata'sı
        # üretiliyor ama JSON rapora HİÇ ulaşmıyordu (uçtan uca koşuda yakalandı).
        metadata = run_metadata

    # guard_mode="batch": harness tek tüketicidir ve safety döngüsü yoktur;
    # ağırlık bütçesi dolduğunda koşuyu ÖLDÜRMEK yerine pencere sonuna kadar
    # beklemek doğrudur (uzun pencereler — ör. 8 sembol × 30 gün ≈ 656 ağırlık
    # — aksi halde ortada `MarketDataBudgetError` ile düşerdi). Canlı motor
    # varsayılan "live" modda kalır: orada beklemek safety turunu bayatlatıp
    # watchdog restart'ı tetikleyebilir (bkz. data.py guard modları).
    fetcher = KlineFetcher(base_url=base_url, guard_mode="batch")
    throttled = _ThrottledFetch(fetcher.get_klines)
    cache_dir_path = Path(cache_dir) if cache_dir is not None else None

    all_trades: List[BacktestTrade] = []
    maker_stats: Dict[str, int] = {}
    try:
        timeframes = resolve_timeframes(cfg)
        tf_entry, tf_context, tf_regime = timeframes

        # D15 lider piyasa kapısı: lider serisi pencere başında BİR KEZ
        # çekilir ve her sembolün simülasyonuna aynen verilir. Kapı kapalıysa
        # (varsayılan) hiç çekilmez — mevcut koşuların istek sayısı ve çıktısı
        # bit düzeyinde değişmez.
        leader_series: Optional[LeaderSeries] = None
        if bool(getattr(cfg, "scalper_market_gate", False)):
            leader_symbol = str(
                getattr(cfg, "scalper_market_gate_symbol", "") or "BTCUSDT"
            ).strip().upper() or "BTCUSDT"
            try:
                leader_run_days = int(
                    getattr(cfg, "scalper_market_gate_run_days", 3) or 3
                )
            except (TypeError, ValueError):
                leader_run_days = 3
            app_logger.info(
                f"🧭 Lider piyasa kapısı AÇIK — {leader_symbol} serisi çekiliyor "
                f"(gün-içi %{getattr(cfg, 'scalper_market_gate_day_pct', 0)}, "
                f"uzama %{getattr(cfg, 'scalper_market_gate_run_pct', 0)}/"
                f"{leader_run_days}g)"
            )
            leader_series = await gather_leader_series(
                throttled, leader_symbol, days, test_end_time_ms, tf_entry,
                leader_run_days, cache_dir=cache_dir_path, refresh=refresh,
            )
            # Yeniden üretilebilirlik: kapının HANGİ veriyle karar verdiği
            # rapordan okunabilmeli — yalnız mum SAYISI değil, serilerin
            # zaman KAPSAMI da (bir seri beklenenden kısa/kaydıksa kapı
            # sessizce fail-open olur; sayı tek başına bunu göstermez).
            metadata["market_gate"] = {
                "leader": leader_symbol,
                # `*_threshold`: `/scalper/status`'te `run_drift_pct` ÖLÇÜLEN
                # koşudur; ikisine de `run_pct` demek, iki çıktıyı yan yana
                # koyan operatörde yanlış-teşhis üretiyordu.
                "day_pct_threshold": getattr(
                    cfg, "scalper_market_gate_day_pct", None
                ),
                "run_pct_threshold": getattr(
                    cfg, "scalper_market_gate_run_pct", None
                ),
                "run_days": leader_run_days,
                "entry_tf": tf_entry,
                "intraday_tf": MARKET_GATE_INTRADAY_TF,
                "daily": _leader_window_snapshot(
                    leader_series.daily_close_times, len(leader_series.daily_closes)
                ),
                "entry": _leader_window_snapshot(
                    leader_series.entry_close_times, len(leader_series.entry_closes)
                ),
                "intraday": _leader_window_snapshot(
                    leader_series.intraday_close_times,
                    len(leader_series.intraday_opens),
                ),
            }

        if leader_out is not None:
            leader_out.append(leader_series)

        for symbol in symbols:
            app_logger.info(f"📥 {symbol}: tarihsel veri çekiliyor ({days} gün)...")
            data = await gather_symbol_data(
                throttled, symbol, days, end_time=test_end_time_ms,
                timeframes=timeframes,
                cache_dir=cache_dir_path, refresh=refresh,
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
            if series_out is not None:
                series_out[symbol] = {
                    "entry": candles_5m,
                    "context": candles_15m,
                    "regime": candles_4h,
                }
            trades = simulate_symbol(
                symbol, candles_5m, candles_15m, candles_4h, strategies, cfg,
                missed_counter=missed_counter,
                test_start_time_ms=test_start_time_ms,
                leader=leader_series,
                entry_delay_candles=entry_delay_candles,
                disabled_realism=disabled_realism,
                exit_slippage_rate=exit_slippage_rate,
                maker_trade_through_pct=maker_trade_through_pct,
                maker_stats=maker_stats,
                funding_fallback_pct=funding_fallback_pct,
                funding_cache_dir=funding_cache_dir,
            )
            app_logger.info(f"✅ {symbol}: {len(trades)} işlem simüle edildi")
            all_trades.extend(trades)
    finally:
        await fetcher.close()
    metadata["maker_orders"] = maker_orders_report(maker_stats)

    # Canlı motor paritesi: kapasite kapısı (scalper_max_positions) semboller
    # arası bir kısıttır — sembol-içi simulate_symbol döngüsünde uygulanamaz,
    # bu yüzden tüm sembollerin adayları birleştikten SONRA burada, tek bir
    # kronolojik geçişle uygulanır (bkz. _apply_capacity_gate docstring'i).
    all_trades = _portfolio_filter(
        all_trades, symbols, cfg, missed_counter=missed_counter,
        disabled_realism=disabled_realism, report_out=metadata,
    )

    return all_trades


# ==========================================================================
# D24/A1 — Monte-Carlo permütasyon çalışması
# ==========================================================================

def _permutation_start_index(candles: Sequence[Candle], test_start_time_ms: Optional[int]) -> int:
    """Permütasyonun BAŞLAYACAĞI indeks: warm-up barları (test penceresinden
    ÖNCE kapananlar) gerçek kalmalı — indikatör/rejim seed'i bozulmasın ve
    permüte seri test penceresinin başındaki GERÇEK fiyat seviyesinden
    başlasın."""
    if test_start_time_ms is None or not candles:
        return 0
    close_times = [c.close_time for c in candles]
    first_in_window = bisect.bisect_left(close_times, test_start_time_ms)
    return max(0, first_in_window - 1)


@dataclass
class SharedWindowPlan:
    """Tüm sembollerin (ve lider serinin) AYNI permütasyon indeksiyle
    permüte edileceği ortak pencere planı (montecarlo-4).

    `series[ad]`: {"entry": gerçek giriş mumları (ısınma + ortak gövde),
    "context", "regime", "start_index", "context_is_entry", "regime_is_entry",
    "regime_is_context"}. `n_perm`: ortak permüte pencere bar sayısı.
    """
    series: Dict[str, Dict[str, Any]]
    n_perm: int
    leader_entry: Optional[List[Candle]]
    leader_start_index: int
    leader: Optional[LeaderSeries]
    report: Dict[str, Any]


def build_shared_window_plan(
    series_by_symbol: Dict[str, Dict[str, List[Candle]]],
    symbols: Sequence[str],
    *,
    leader: Optional[LeaderSeries],
    test_start_time_ms: Optional[int],
) -> SharedWindowPlan:
    """Ortak permütasyon penceresini kur.

    Hizalama: her serinin (semboller + lider giriş mumları) permüte başlangıcı
    `_permutation_start_index` ile bulunur; ortak pencere başı T0 = bunların en
    geç olanı, sonra TÜM serilerde bulunan zaman damgalarının (open_time)
    kesişimindeki ilk zamana yuvarlanır. T0'dan ÖNCEKİ bar (ısınma) her seride
    gerçek kalır. T0 ve sonrasında ortak OLMAYAN barlar o serilerden DÜŞÜRÜLÜR
    (ortak pencerede her seri aynı sayıda barla aynı indekse oturur; düşen sayı
    `report["dropped_bars"]`'ta). Aynı yoldan çekilmiş veride (normal koşu)
    hiçbir bar düşmez. Tek sembol, liderli/liderisiz: eski davranışla aynı
    pencere.
    """
    included: Dict[str, List[Candle]] = {}
    for sym in symbols:
        ser = series_by_symbol.get(sym)
        if ser and ser.get("entry"):
            included[sym] = ser["entry"]
    leader_entry = (
        leader.entry_candles if leader is not None and leader.entry_candles else None
    )
    inputs: Dict[str, List[Candle]] = dict(included)
    if leader_entry is not None:
        inputs["__leader__"] = leader_entry

    starts: Dict[str, int] = {}
    start_times: List[int] = []
    for name, candles in inputs.items():
        si = _permutation_start_index(candles, test_start_time_ms)
        if si + 1 < len(candles):          # permüte edilecek bar var
            starts[name] = si
            start_times.append(candles[si + 1].open_time)

    report: Dict[str, Any] = {
        "shared_index": True,
        "symbols": [n for n in starts if n != "__leader__"],
        "leader_permuted": False,
        "window_start_ms": None, "common_bars": 0, "permuted_bars": 0,
        "dropped_bars": {},
    }
    plan_series: Dict[str, Dict[str, Any]] = {}
    if len(starts) == 0:
        return SharedWindowPlan(plan_series, 0, None, 0, leader, report)

    time_sets = [set(c.open_time for c in inputs[n]) for n in starts]
    common = sorted(set.intersection(*time_sets))
    t0_wanted = max(start_times)
    candidates = [k for k, t in enumerate(common) if t >= t0_wanted and k >= 1]
    if not candidates:
        return SharedWindowPlan(plan_series, 0, None, 0, leader, report)
    k0 = candidates[0]
    t0 = common[k0]
    common_set = set(common)
    n_perm = len(common) - k0
    report.update({
        "window_start_ms": int(t0), "common_bars": len(common),
        "permuted_bars": n_perm,
    })

    def _base(candles: List[Candle]) -> Tuple[List[Candle], int, int]:
        base = [c for c in candles if c.open_time < t0 or c.open_time in common_set]
        prefix = sum(1 for c in base if c.open_time < t0)
        dropped = sum(1 for c in candles if c.open_time >= t0 and c.open_time not in common_set)
        return base, prefix - 1, dropped

    for name in starts:
        base, start_index, dropped = _base(inputs[name])
        if name == "__leader__":
            report["dropped_bars"]["__leader__"] = dropped
            leader_base, leader_si = base, start_index
            continue
        ser = series_by_symbol[name]
        plan_series[name] = {
            "entry": base,
            "context": ser["context"],
            "regime": ser["regime"],
            "start_index": start_index,
            "context_is_entry": ser["context"] is ser["entry"],
            "regime_is_entry": ser["regime"] is ser["entry"],
            "regime_is_context": ser["regime"] is ser["context"],
        }
        report["dropped_bars"][name] = dropped
    leader_ok = "__leader__" in starts
    if leader_ok:
        report["leader_permuted"] = True
    return SharedWindowPlan(
        plan_series, n_perm,
        leader_base if leader_ok else None,
        leader_si if leader_ok else 0,
        leader, report,
    )


def _permuted_inputs(
    plan: SharedWindowPlan,
    *,
    seed: int,
    block_length: int,
    clamp: bool,
) -> Tuple[
    Dict[str, Tuple[List[Candle], List[Candle], List[Candle]]],
    Optional[LeaderSeries],
    List[Dict[str, Any]],
]:
    """Bir tur için TEK permütasyon indeksi çiz ve tüm sembollere + lidere
    uygula. Bağlam/rejim dilimleri permüte giriş serisinden TÜRETİLİR
    (`aggregate_from`); permüte serinin kapsamadığı daha eski barlar GERÇEK
    kalır (permutation.py "NULL'UN KAPSAMI"). Lider: giriş serisi aynı indeksle
    permüte, günlük/gün-içi serileri ondan türetilir; ham mumu yoksa gerçek
    lider döner."""
    index = draw_permutation_index(
        plan.n_perm, seed=seed, block_length=block_length,
    )
    out: Dict[str, Tuple[List[Candle], List[Candle], List[Candle]]] = {}
    stats_all: List[Dict[str, Any]] = []
    for name, info in plan.series.items():
        perm_entry, stats = _apply_permutation_index(
            info["entry"], index, info["start_index"], clamp,
        )
        stats_all.append(stats)
        if info["context_is_entry"]:
            perm_context = perm_entry
        else:
            perm_context = aggregate_from(perm_entry, info["context"])
        if info["regime_is_entry"]:
            perm_regime = perm_entry
        elif info["regime_is_context"]:
            perm_regime = perm_context
        else:
            perm_regime = aggregate_from(perm_entry, info["regime"])
        out[name] = (perm_entry, perm_context, perm_regime)

    leader_out = plan.leader
    if plan.leader_entry is not None and plan.leader is not None:
        perm_l, _ = _apply_permutation_index(
            plan.leader_entry, index, plan.leader_start_index, clamp,
        )
        daily = aggregate_from(perm_l, plan.leader.daily_candles or [])
        intraday = aggregate_from(perm_l, plan.leader.intraday_candles or [])
        leader_out = LeaderSeries(
            symbol=plan.leader.symbol,
            entry_close_times=[c.close_time for c in perm_l],
            entry_closes=[c.close for c in perm_l],
            daily_close_times=[c.close_time for c in daily],
            daily_closes=[c.close for c in daily],
            intraday_open_times=[c.open_time for c in intraday],
            intraday_opens=[c.open for c in intraday],
            intraday_close_times=[c.close_time for c in intraday],
            entry_candles=perm_l,
            daily_candles=daily,
            intraday_candles=intraday,
        )
    return out, leader_out, stats_all


def _permutation_pass(
    plan: SharedWindowPlan,
    series_by_symbol: Dict[str, Dict[str, List[Candle]]],
    symbols: Sequence[str],
    strategies: List[StrategyProtocol],
    cfg: Any,
    *,
    test_start_time_ms: Optional[int],
    base_seed: int,
    clamp: bool,
    block_length: int = 1,
    entry_delay_candles: int,
    disabled_realism: Any = None,
    exit_slippage_rate: Optional[float] = None,
    maker_trade_through_pct: Optional[float] = None,
    funding_fallback_pct: Optional[float] = None,
    funding_cache_dir: Optional[str | Path] = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Tek bir permütasyon turu: TEK ortak indeksle tüm semboller + lider
    permüte edilip simüle edilir, portföy kapıları (kapasite + günlük mandal)
    gerçek koşudaki gibi POST-HOC uygulanır ve `compute_stats` döner."""
    permuted, perm_leader, clamp_stats = _permuted_inputs(
        plan, seed=base_seed, block_length=block_length, clamp=clamp,
    )
    trades: List[BacktestTrade] = []
    for symbol in symbols:
        if symbol in permuted:
            entry, context, regime = permuted[symbol]
            leader_for = perm_leader
        else:
            ser = series_by_symbol.get(symbol)
            if not ser:
                continue
            # Permüte edilemeyen sembol (pencere yok): gerçek seri.
            entry, context, regime = ser["entry"], ser["context"], ser["regime"]
            leader_for = plan.leader
        trades.extend(simulate_symbol(
            symbol, entry, context, regime, strategies, cfg,
            missed_counter=None,
            test_start_time_ms=test_start_time_ms,
            leader=leader_for,
            entry_delay_candles=entry_delay_candles,
            disabled_realism=disabled_realism,
            exit_slippage_rate=exit_slippage_rate,
            maker_trade_through_pct=maker_trade_through_pct,
            funding_fallback_pct=funding_fallback_pct,
            funding_cache_dir=funding_cache_dir,
        ))
    trades = _portfolio_filter(
        trades, list(symbols), cfg, missed_counter=None,
        disabled_realism=disabled_realism,
    )
    return compute_stats(trades), clamp_stats


def run_permutation_study(
    series_by_symbol: Dict[str, Dict[str, List[Candle]]],
    symbols: Sequence[str],
    strategy_names: str,
    cfg: Any,
    real_stats: Dict[str, Any],
    *,
    permutations: int,
    seed: int = 12345,
    metrics: Optional[Sequence[str]] = None,
    clamp_audit: bool = False,
    test_start_time_ms: Optional[int] = None,
    leader: Optional[LeaderSeries] = None,
    entry_delay_candles: int = 0,
    block_length: int = 1,
    progress: bool = True,
    disabled_realism: Any = None,
    exit_slippage_rate: Optional[float] = None,
    maker_trade_through_pct: Optional[float] = None,
    funding_fallback_pct: Optional[float] = None,
    funding_cache_dir: Optional[str | Path] = None,
) -> Dict[str, Any]:
    """D24/A1 — Monte-Carlo permütasyon testi (AĞ YOK; veri zaten elde).

    Her turda giriş dilimi log uzayında karıştırılır, bağlam/rejim dilimleri
    ondan türetilir, simülasyon TEKRAR koşulur ve metrikler toplanır. Sonuçta
    metrik başına yön-farkındalıklı p-değeri üretilir.

    `clamp_audit=True` iken AYNI tohumlarla ikinci bir null dağılımı KELEPÇE
    KAPALI olarak da üretilir (2× simülasyon maliyeti) ve `clamp_shift`
    tablosu kelepçenin null'u ne kadar kaydırdığını gösterir. Bu, kelepçenin
    keyfi bir düzeltme OLMADIĞININ kanıtıdır.

    Dönen sözlük JSON uyumludur ve doğrudan rapora gömülür.
    """
    count = max(0, int(permutations or 0))
    wanted = list(metrics) if metrics else list(PERMUTATION_DEFAULT_METRICS)
    if count == 0 or not series_by_symbol:
        return {
            "permutations": 0,
            "note": "permütasyon koşulmadı (--permutations 0)",
        }

    strategies = get_enabled(strategy_names)
    if not strategies:
        raise ValueError(f"Geçerli scalper stratejisi bulunamadı: '{strategy_names}'")

    clamped_rows: List[Dict[str, Any]] = []
    unclamped_rows: List[Dict[str, Any]] = []
    clamp_stats_all: List[Dict[str, Any]] = []
    unclamped_stats_all: List[Dict[str, Any]] = []

    if int(block_length) < 1:
        raise ValueError("block_length >= 1 olmalı")
    plan = build_shared_window_plan(
        series_by_symbol, symbols, leader=leader,
        test_start_time_ms=test_start_time_ms,
    )

    def _row(stats: Dict[str, Any]) -> Dict[str, Any]:
        # `trades`: sıfır-işlemli null turlarını p-değerinden ayıklamak için
        # (permutation.UNDEFINED_ON_ZERO_TRADES) satıra eklenir.
        row = {k: stats.get(k) for k in wanted}
        row["trades"] = stats.get("trades")
        return row

    started = time.monotonic()
    for index in range(count):
        base_seed = int(seed) + index * 1_000_003
        stats, clamp_stats = _permutation_pass(
            plan, series_by_symbol, symbols, strategies, cfg,
            test_start_time_ms=test_start_time_ms,
            base_seed=base_seed,
            clamp=True,
            block_length=int(block_length),
            entry_delay_candles=entry_delay_candles,
            disabled_realism=disabled_realism,
            exit_slippage_rate=exit_slippage_rate,
            maker_trade_through_pct=maker_trade_through_pct,
            funding_fallback_pct=funding_fallback_pct,
            funding_cache_dir=funding_cache_dir,
        )
        clamped_rows.append(_row(stats))
        clamp_stats_all.extend(clamp_stats)

        if clamp_audit:
            raw_stats, raw_clamp = _permutation_pass(
                plan, series_by_symbol, symbols, strategies, cfg,
                test_start_time_ms=test_start_time_ms,
                base_seed=base_seed,
                clamp=False,
                block_length=int(block_length),
                entry_delay_candles=entry_delay_candles,
                disabled_realism=disabled_realism,
                exit_slippage_rate=exit_slippage_rate,
                maker_trade_through_pct=maker_trade_through_pct,
                funding_fallback_pct=funding_fallback_pct,
                funding_cache_dir=funding_cache_dir,
            )
            unclamped_rows.append(_row(raw_stats))
            unclamped_stats_all.extend(raw_clamp)

        if progress and (index + 1) % 10 == 0:
            app_logger.info(
                f"🎲 permütasyon {index + 1}/{count} "
                f"({time.monotonic() - started:.1f}s)"
            )

    result = compute_p_values(
        real_stats, clamped_rows, wanted, seed=int(seed), block_length=int(block_length),
    )
    out: Dict[str, Any] = {
        "permutations": count,
        "seed": int(seed),
        "block_length": int(block_length),
        # montecarlo-4: tüm semboller + lider aynı indeksle permüte edilir.
        "shared_index": plan.report,
        "metrics": wanted,
        "elapsed_sec": round(time.monotonic() - started, 2),
        "clamp": merge_clamp_stats(clamp_stats_all),
        "result": result,
        "null_samples": clamped_rows,
        "null_scope": (
            "KOŞULLU null: TÜM semboller VE lider seri AYNI permütasyon "
            "indeksiyle permüte edilir (semboller arası ilişki ve lider "
            "ilişkisi korunur); yalnız giriş dilimi permüte edilir, bağlam/rejim "
            "dilimleri ondan türetilir; permüte serinin kapsamadığı daha eski "
            "rejim barları GERÇEK kalır. p-değeri 'rejim arka planı aynıyken "
            "giriş sinyalinin kendisi şanstan ayırt edilebilir mi' sorusunu "
            "yanıtlar — koşulsuz bir null DEĞİLDİR."
        ),
    }
    if clamp_audit:
        raw_result = compute_p_values(
            real_stats, unclamped_rows, wanted,
            seed=int(seed), block_length=int(block_length),
        )
        out["clamp_audit"] = {
            "result": raw_result,
            "shift": clamp_shift_report(result, raw_result),
            "unclamped_violation_stats": merge_clamp_stats(unclamped_stats_all),
        }
    return out


def print_permutation_report(study: Dict[str, Any]) -> None:
    """Permütasyon sonucunu konsola bas."""
    if not study or not study.get("permutations"):
        return
    cols = [
        ("Metrik", 18), ("Yön", 7), ("Gerçek", 12), ("Null ort", 12),
        ("Null p05", 12), ("Null p95", 12), ("p-değeri", 10),
    ]
    header = " | ".join(name.ljust(width) for name, width in cols)
    print("\n" + "=" * len(header))
    print(
        f"MONTE-CARLO PERMÜTASYON TESTİ — {study['permutations']} tur, "
        f"tohum {study.get('seed')} ({study.get('elapsed_sec')}s)"
    )
    print("=" * len(header))
    print(header)
    print("-" * len(header))
    for metric in study.get("metrics", []):
        row = (study.get("result", {}).get("metrics") or {}).get(metric)
        if not row:
            continue
        values = [
            metric,
            "büyük" if row.get("direction") == "higher" else "küçük",
            _fmt_opt(row.get("real")),
            _fmt_opt(row.get("null_mean")),
            _fmt_opt(row.get("null_p05")),
            _fmt_opt(row.get("null_p95")),
            _fmt_opt(row.get("p_value"), digits=4),
        ]
        print(" | ".join(v.ljust(w) for v, (_, w) in zip(values, cols)))
    print("-" * len(header))

    clamp = study.get("clamp") or {}
    print(
        f"High/Low kelepçesi: {clamp.get('violated_bar_pct', 0)}% bar düzeltildi "
        f"(high {clamp.get('high_violation_pct', 0)}%, low "
        f"{clamp.get('low_violation_pct', 0)}%), ortalama düzeltme "
        f"%{clamp.get('mean_abs_adjust_pct', 0)}, en büyük "
        f"%{clamp.get('max_abs_adjust_pct', 0)}"
    )
    audit = study.get("clamp_audit")
    if audit:
        print("Kelepçenin null'u kaydırması (kelepçeli − kelepçesiz):")
        for row in (audit.get("shift") or {}).get("rows", []):
            print(
                f"  {row['metric']:<18} null ort Δ {_fmt_opt(row.get('null_mean_delta'))}"
                f"   p Δ {_fmt_opt(row.get('p_value_delta'), digits=4)}"
            )
    for skip in (study.get("result", {}).get("skipped") or []):
        print(f"ATLANDI {skip['metric']}: {skip['reason']}")
    print(f"KAPSAM: {study.get('null_scope', '')}")
    print("=" * len(header))


def _fmt_opt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


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
    parser.add_argument(
        "--cache-dir", type=str, default="data/klines_cache",
        help=(
            "Kline önbellek dizini (yoksa oluşturulur). Aynı (sembol, aralık, "
            "pencere) için sonraki koşular Binance'e gitmez, buradan okur "
            "(varsayılan: data/klines_cache)"
        ),
    )
    parser.add_argument(
        "--refresh", action="store_true",
        help="Önbelleği yok say, Binance'ten TAZE çek ve üzerine yaz",
    )
    # ---- D24 ölçüm/kanıt bayrakları — hepsi VARSAYILAN KAPALI ----
    parser.add_argument(
        "--permutations", type=int, default=0,
        help=(
            "Monte-Carlo permütasyon turu sayısı (0 = kapalı). Veri TEKRAR "
            "ÇEKİLMEZ; her tur giriş dilimini karıştırıp simülasyonu yeniden "
            "koşar ve metrik başına p-değeri üretir (D24/A1)"
        ),
    )
    parser.add_argument(
        "--permutation-seed", type=int, default=12345,
        help="Permütasyon tohumu (yeniden üretilebilirlik; varsayılan 12345)",
    )
    parser.add_argument(
        "--permutation-block-length", type=int, default=1,
        help=(
            "Permütasyon blok uzunluğu (bar). 1 = bağımsız-bar null'u "
            "(volatilite kümelenmesi yok edilir); >1 = ardışık bloklar "
            "karıştırılır, blok içi yapı korunur"
        ),
    )
    parser.add_argument(
        "--permutation-metrics", type=str,
        default=",".join(PERMUTATION_DEFAULT_METRICS),
        help="p-değeri üretilecek metrikler (virgülle ayrılmış)",
    )
    parser.add_argument(
        "--permutation-clamp-audit", action="store_true",
        help=(
            "High/Low kelepçesinin null dağılımını NE KADAR kaydırdığını da "
            "ölç (AYNI tohumlarla kelepçesiz ikinci null; 2× süre)"
        ),
    )
    parser.add_argument(
        "--fee-stress", action="store_true",
        help=(
            "Maliyet stres senaryosu: komisyon VE kayma 2× (D24/A5). "
            "Varsayılan koşuyu DEĞİŞTİRMEZ — ayrı bir koşudur"
        ),
    )
    parser.add_argument(
        "--fee-stress-multiplier", type=float, default=2.0,
        help="--fee-stress çarpanı (varsayılan 2.0)",
    )
    parser.add_argument(
        "--entry-delay-candles", type=int, default=0,
        help=(
            "Çürütme koşusu: giriş emri N mum GEÇ dolsun (varsayılan 0 = "
            "bugünkü davranış, sinyal mumunun bir sonraki open'ı)"
        ),
    )
    parser.add_argument(
        "--disable-realism", type=str, default="",
        help=(
            "Virgülle ayrılmış gerçekçilik özellikleri KAPALI olsun "
            f"({', '.join(REALISM_FEATURES)}); bilinmeyen ad hata"
        ),
    )
    parser.add_argument(
        "--legacy-model", action="store_true",
        help="Tüm gerçekçilik özelliklerini kapat = bu işten ÖNCEKİ sayılar",
    )
    parser.add_argument(
        "--exit-slippage-pct", type=float, default=None,
        help=(
            "Market-tipi çıkışlarda aleyhte kayma, YÜZDE (0.02 = %0.02). "
            "Varsayılan: giriş kaymasıyla aynı (SCALPER_SLIPPAGE_RATE). "
            "--fee-stress bunu da çarpar"
        ),
    )
    parser.add_argument(
        "--maker-trade-through-pct", type=float, default=None,
        help=(
            "Maker limit dolumu için trade-through eşiği, YÜZDE (0.01 = 1 baz "
            "puan, varsayılan): LONG low <= L*(1-tt), SHORT high >= L*(1+tt). "
            "Yalnız SCALPER_ENTRY_MODE=maker; --legacy-model / "
            "--disable-realism maker_trade_through ile kapanır"
        ),
    )
    parser.add_argument(
        "--funding-fallback-pct", type=float, default=None,
        help=(
            "Önbelleğin kapsamadığı funding anları için sabit oran, 8 saatte "
            "bir, YÜZDE (varsayılan 0.01 = %0.01)"
        ),
    )
    parser.add_argument(
        "--funding-cache-dir", type=str, default="data/funding_cache",
        help=(
            "Tarihsel funding oranları (<SEMBOL>.json); "
            "scripts/fill_funding_cache.py doldurur. Dosya yoksa yedek oran"
        ),
    )
    return parser


def resolve_disabled_realism(args: argparse.Namespace) -> frozenset:
    """CLI: --legacy-model = tüm özellikler kapalı; yoksa --disable-realism."""
    if getattr(args, "legacy_model", False):
        return frozenset(REALISM_FEATURES)
    return normalize_disabled_realism(getattr(args, "disable_realism", "") or "")


async def main_async(args: argparse.Namespace) -> None:
    symbols = await _resolve_symbols(args.symbols)
    effective_days, start_ms, end_ms = resolve_backtest_window(args.days, args.start, args.end)
    window_desc = f"{args.start}→{args.end} (UTC, [start,end))" if start_ms is not None else f"{args.days} gün"

    # D24/A5 — maliyet stresi: canlı `settings` DEĞİŞMEZ, yalnız bu koşu için
    # sığ bir kopya çarpılır. `--fee-stress` verilmediğinde `stressed_cfg`
    # ORİJİNAL nesneyi döndürür → varsayılan koşu bit düzeyinde eskisiyle aynı.
    stress_multiplier = (
        float(args.fee_stress_multiplier) if getattr(args, "fee_stress", False) else 1.0
    )
    run_cfg = stressed_cfg(settings, stress_multiplier)
    entry_delay = max(0, int(getattr(args, "entry_delay_candles", 0) or 0))
    disabled_realism = resolve_disabled_realism(args)
    maker_tt_pct = getattr(args, "maker_trade_through_pct", None)
    exit_slip_pct = getattr(args, "exit_slippage_pct", None)
    funding_fallback_pct = getattr(args, "funding_fallback_pct", None)
    funding_cache_dir = getattr(args, "funding_cache_dir", None)
    # Açık verilen çıkış kayması da --fee-stress ile çarpılır; verilmezse
    # None → slippage_rate(run_cfg) (stres zaten uygulanmış).
    exit_slippage = (
        None if exit_slip_pct is None
        else float(exit_slip_pct) / 100.0 * stress_multiplier
    )
    if stress_multiplier != 1.0 or entry_delay:
        app_logger.info(
            f"🧪 Çürütme koşusu: maliyet çarpanı {stress_multiplier}× "
            f"(taker %{getattr(run_cfg, 'scalper_taker_fee_pct', 0):.4f}, kayma "
            f"{slippage_rate(run_cfg):.6f}), giriş gecikmesi {entry_delay} mum"
        )
    app_logger.info(
        f"🚀 Scalper backtest başlıyor: pencere={window_desc} sembol={symbols} strateji={args.strategies}"
    )

    missed_counter: Dict[str, int] = {}
    run_metadata: Dict[str, Any] = {}
    permutations = max(0, int(getattr(args, "permutations", 0) or 0))
    series_by_symbol: Dict[str, Dict[str, List[Candle]]] = {}
    leader_holder: List[Optional[LeaderSeries]] = []
    all_trades = await run_backtest(
        days=effective_days,
        symbols=symbols,
        strategy_names=args.strategies,
        cfg=run_cfg,
        missed_counter=missed_counter,
        run_metadata=run_metadata,
        start_time_ms=start_ms,
        end_time_ms=end_ms,
        cache_dir=args.cache_dir,
        refresh=args.refresh,
        entry_delay_candles=entry_delay,
        disabled_realism=disabled_realism,
        exit_slippage_rate=exit_slippage,
        maker_trade_through_pct=maker_tt_pct,
        funding_fallback_pct=funding_fallback_pct,
        funding_cache_dir=funding_cache_dir,
        series_out=series_by_symbol if permutations > 0 else None,
        leader_out=leader_holder if permutations > 0 else None,
    )
    run_metadata.setdefault("cost_model", {})
    run_metadata["cost_model"]["stress_multiplier"] = stress_multiplier

    universe_snapshot = run_metadata["universe_snapshot"]
    universe_snapshot["selection_mode"] = (
        "auto_current_24h_volume"
        if args.symbols.strip().lower() == "auto"
        else "explicit_symbols"
    )
    universe_snapshot["requested_arg"] = args.symbols

    print_report(all_trades, missed_counter=missed_counter)

    permutation_study: Optional[Dict[str, Any]] = None
    if permutations > 0:
        wanted_metrics = [
            name.strip() for name in str(args.permutation_metrics).split(",")
            if name.strip()
        ]
        app_logger.info(
            f"🎲 Monte-Carlo permütasyon: {permutations} tur × "
            f"{len(series_by_symbol)} sembol (AĞ YOK, veri elde)"
        )
        permutation_study = run_permutation_study(
            series_by_symbol,
            symbols,
            args.strategies,
            run_cfg,
            compute_stats(all_trades),
            permutations=permutations,
            seed=int(args.permutation_seed),
            metrics=wanted_metrics,
            clamp_audit=bool(args.permutation_clamp_audit),
            test_start_time_ms=run_metadata.get("test_window", {}).get("start_ms"),
            leader=leader_holder[0] if leader_holder else None,
            entry_delay_candles=entry_delay,
            block_length=int(getattr(args, "permutation_block_length", 1) or 1),
            disabled_realism=disabled_realism,
            exit_slippage_rate=exit_slippage,
            maker_trade_through_pct=maker_tt_pct,
            funding_fallback_pct=funding_fallback_pct,
            funding_cache_dir=funding_cache_dir,
        )
        print_permutation_report(permutation_study)

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
        cfg=run_cfg,
        run_metadata=run_metadata,
        permutation=permutation_study,
    )


def main() -> None:
    parser = _build_arg_parser()
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
