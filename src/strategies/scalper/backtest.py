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
from src.strategies.scalper.indicators import atr, chandelier_stop
from src.strategies.scalper.market_gate import (
    MARKET_GATE_INTRADAY_TF,
    evaluate_market_gate,
    resolve_day_open,
)
from src.strategies.scalper.permutation import (
    DEFAULT_METRICS as PERMUTATION_DEFAULT_METRICS,
    aggregate_from,
    clamp_shift_report,
    compute_p_values,
    merge_clamp_stats,
    permute_candles,
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
    # D24/A3 — bar-bazlı mark-to-market işaretleri (bkz. OpenPosition).
    # `to_dict` DIŞINDA tutulur: 800 işlemlik bir koşuda bar başına iki sayı
    # JSON raporunu megabaytlara şişirir ve rapor tüketicileri (pano,
    # ledger_report) bunu okumaz — seri `bar_equity_series` ile TÜREVİLİR.
    equity_marks: List[Tuple[int, float]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = {
            k: v for k, v in self.__dict__.items()
            if k not in ("exit_idx", "equity_marks")
        }
        return d


def _find_maker_fill(
    signal: ScalpSignal, candles_5m: List[Candle], signal_idx: int, cfg: Any,
    entry_delay_candles: int = 0,
) -> Optional[tuple]:
    """Maker giriş modu: LIMIT fiyatı = sinyal mumunun kapanışı. Sonraki
    `scalper_maker_fill_timeout_candles` mum içinde fiyat limite değerse
    (LONG: mum.low <= limit; SHORT: mum.high >= limit) ilk değen mumda,
    kaymasız, limit fiyatından dolum sayılır.

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
    entry_delay_candles: int = 0,
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
    if entry_mode == "maker":
        fill = _find_maker_fill(
            signal, candles_5m, signal_idx, cfg, entry_delay_candles=delay,
        )
        if fill is None:
            if missed_counter is not None:
                missed_counter["maker_missed"] = missed_counter.get("maker_missed", 0) + 1
            return None
        entry_idx, entry_price = fill
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
        signal_close_time=candles_5m[signal_idx].close_time,
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
            # Etiket ayrımı (yalnız raporlama): yapı çıkışı (SCALPER_STRUCTURE_
            # EXIT=be) stopu BE'ye çekmişse bu bir "SL kaybı" değil, kesilmiş
            # bir kayıptır — E9 ölçümünde ayrı sayılabilsin. Bayrak varsayılan
            # False olduğundan kapalı yapılandırmada davranış birebir aynıdır.
            _close_remaining(
                pos, pos.current_stop, c.close_time,
                "STRUCT_BE" if pos.structure_be_applied else "SL",
            )
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
    if pos.equity_marks and pos.equity_marks[-1][0] == c.close_time:
        pos.equity_marks[-1] = (c.close_time, realized)
    else:
        pos.equity_marks.append((c.close_time, realized))


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
        equity_marks=list(pos.equity_marks),
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
        _update_mae_mfe(pos, c, pos.direction, pos.leverage)

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
                _close_remaining(pos, c.close, c.close_time, "CHOCH")
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
            _close_remaining(pos, c.close, c.close_time, EXIT_REASON_STALE_TP)
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
            _close_remaining(pos, c.close, c.close_time, "REAPER")
            exit_idx = idx
            _mark_equity(pos, c)
            break
        _mark_equity(pos, c)
    else:
        exit_idx = n - 1
        last = candles_5m[-1]
        _close_remaining(pos, last.close, last.close_time, "EOD")
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

        signal = apply_stop_policy(raw_signal, cfg)

        pos = open_position(
            signal, candles_5m, i, cfg, initial_balance,
            missed_counter=missed_counter,
            entry_delay_candles=entry_delay_candles,
        )
        if pos is None:
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


def bar_equity_series(trades: List[BacktestTrade]) -> List[Tuple[int, float]]:
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
    """
    events: List[Tuple[int, int, float]] = []
    for index, trade in enumerate(trades):
        for close_time, value in (trade.equity_marks or ()):
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


def bar_drawdown(series: Sequence[Tuple[int, float]]) -> Tuple[float, Optional[int]]:
    """Bar-bazlı özkaynak eğrisinden en derin tepe→dip çöküşü.

    Döner: (çöküş büyüklüğü, çukurun zaman damgası). Tepe 0.0'dan başlar —
    `compute_stats`'taki kapanış-bazlı `max_drawdown` ile aynı taban.
    """
    peak = 0.0
    worst = 0.0
    worst_at: Optional[int] = None
    for stamp, equity in series:
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


def compute_stats(trades: List[BacktestTrade]) -> Dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {
            "trades": 0, "wins": 0, "winrate": 0.0, "total_pnl": 0.0, "avg_roi": 0.0,
            "profit_factor": 0.0, "max_consec_losses": 0, "max_drawdown": 0.0,
            "avg_duration_min": 0.0, "avg_mae": 0.0, "avg_mfe": 0.0,
            # D24: EK alanlar — rapor ŞEKLİ boş koşuda da sabit kalmalı.
            "bar_max_drawdown": 0.0, "bar_max_drawdown_at": None,
            "bar_equity_points": 0,
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
    for t in ordered:
        cum += t.pnl
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    # D24/A3 — bar-bazlı (mark-to-market) çöküş. Kapanış-bazlı `max_drawdown`
    # açık pozisyonların bar-içi çukurunu göremez; bu ikinci sayı ONU ölçer ve
    # tanım gereği kapanış-bazlıdan KÜÇÜK OLAMAZ (aynı taban, daha sık örnekleme).
    series = bar_equity_series(ordered)
    bar_max_dd, bar_max_dd_at = bar_drawdown(series)

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
        **concentration_stats(ordered),
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
            )
            app_logger.info(f"✅ {symbol}: {len(trades)} işlem simüle edildi")
            all_trades.extend(trades)
    finally:
        await fetcher.close()

    # Canlı motor paritesi: kapasite kapısı (scalper_max_positions) semboller
    # arası bir kısıttır — sembol-içi simulate_symbol döngüsünde uygulanamaz,
    # bu yüzden tüm sembollerin adayları birleştikten SONRA burada, tek bir
    # kronolojik geçişle uygulanır (bkz. _apply_capacity_gate docstring'i).
    all_trades = _apply_capacity_gate(all_trades, symbols, cfg, missed_counter=missed_counter)

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


def _permute_symbol_series(
    series: Dict[str, List[Candle]],
    *,
    test_start_time_ms: Optional[int],
    seed: int,
    clamp: bool,
) -> Tuple[List[Candle], List[Candle], List[Candle], Dict[str, Any]]:
    """Bir sembolün üç dilimini TUTARLI biçimde permüte et.

    Giriş dilimi doğrudan permüte edilir; bağlam ve rejim dilimleri permüte
    giriş serisinden TÜRETİLİR (`aggregate_from`). Permüte serinin kapsamadığı
    (daha eski) bağlam/rejim barları GERÇEK kalır — bkz. permutation.py
    "NULL'UN KAPSAMI" notu. `run_backtest`'te bir aralık iki role birden
    hizmet ediyorsa (ör. golden koşuda 15m hem bağlam hem rejim) aynı liste
    nesnesi paylaşılır; burada da AYNI nesne paylaşımı korunur.
    """
    entry = series["entry"]
    context = series["context"]
    regime = series["regime"]

    start_index = _permutation_start_index(entry, test_start_time_ms)
    perm_entry, stats = permute_candles(
        entry, start_index=start_index, seed=seed, clamp=clamp
    )

    if context is entry:
        perm_context = perm_entry
    else:
        perm_context = aggregate_from(perm_entry, context)

    if regime is entry:
        perm_regime = perm_entry
    elif regime is context:
        perm_regime = perm_context
    else:
        perm_regime = aggregate_from(perm_entry, regime)

    return perm_entry, perm_context, perm_regime, stats


def _permutation_pass(
    series_by_symbol: Dict[str, Dict[str, List[Candle]]],
    symbols: Sequence[str],
    strategies: List[StrategyProtocol],
    cfg: Any,
    *,
    test_start_time_ms: Optional[int],
    base_seed: int,
    clamp: bool,
    leader: Optional[LeaderSeries],
    entry_delay_candles: int,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Tek bir permütasyon turu: tüm semboller permüte edilip simüle edilir,
    kapasite kapısı gerçek koşudaki gibi POST-HOC uygulanır ve `compute_stats`
    döner."""
    trades: List[BacktestTrade] = []
    clamp_stats: List[Dict[str, Any]] = []
    for offset, symbol in enumerate(symbols):
        series = series_by_symbol.get(symbol)
        if not series:
            continue
        perm_entry, perm_context, perm_regime, stats = _permute_symbol_series(
            series,
            test_start_time_ms=test_start_time_ms,
            seed=base_seed + offset,
            clamp=clamp,
        )
        clamp_stats.append(stats)
        trades.extend(simulate_symbol(
            symbol, perm_entry, perm_context, perm_regime, strategies, cfg,
            missed_counter=None,
            test_start_time_ms=test_start_time_ms,
            leader=leader,
            entry_delay_candles=entry_delay_candles,
        ))
    trades = _apply_capacity_gate(trades, list(symbols), cfg, missed_counter=None)
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
    progress: bool = True,
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

    started = time.monotonic()
    for index in range(count):
        base_seed = int(seed) + index * 1_000_003
        stats, clamp_stats = _permutation_pass(
            series_by_symbol, symbols, strategies, cfg,
            test_start_time_ms=test_start_time_ms,
            base_seed=base_seed,
            clamp=True,
            leader=leader,
            entry_delay_candles=entry_delay_candles,
        )
        clamped_rows.append({k: stats.get(k) for k in wanted})
        clamp_stats_all.extend(clamp_stats)

        if clamp_audit:
            raw_stats, raw_clamp = _permutation_pass(
                series_by_symbol, symbols, strategies, cfg,
                test_start_time_ms=test_start_time_ms,
                base_seed=base_seed,
                clamp=False,
                leader=leader,
                entry_delay_candles=entry_delay_candles,
            )
            unclamped_rows.append({k: raw_stats.get(k) for k in wanted})
            unclamped_stats_all.extend(raw_clamp)

        if progress and (index + 1) % 10 == 0:
            app_logger.info(
                f"🎲 permütasyon {index + 1}/{count} "
                f"({time.monotonic() - started:.1f}s)"
            )

    result = compute_p_values(real_stats, clamped_rows, wanted)
    out: Dict[str, Any] = {
        "permutations": count,
        "seed": int(seed),
        "metrics": wanted,
        "elapsed_sec": round(time.monotonic() - started, 2),
        "clamp": merge_clamp_stats(clamp_stats_all),
        "result": result,
        "null_samples": clamped_rows,
        "null_scope": (
            "KOŞULLU null: yalnız giriş dilimi permüte edilir, bağlam/rejim "
            "dilimleri ondan türetilir; permüte serinin kapsamadığı daha eski "
            "rejim barları GERÇEK kalır. p-değeri 'rejim arka planı aynıyken "
            "giriş sinyalinin kendisi şanstan ayırt edilebilir mi' sorusunu "
            "yanıtlar — koşulsuz bir null DEĞİLDİR."
        ),
    }
    if clamp_audit:
        raw_result = compute_p_values(real_stats, unclamped_rows, wanted)
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
    return parser


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
