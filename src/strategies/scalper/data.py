"""
Kline (mum) verisi çekme modülü — Scalper motoru için public Binance Futures
klines endpoint'i üzerinden veri sağlar.

Tasarım ilkeleri:
- İmza/API anahtarı GEREKMEZ: /fapi/v1/klines herkese açık bir endpoint'tir,
  emir yeteneği taşımaz. Bu yüzden ImprovedBinanceClient'ın imzalı istek
  altyapısını yeniden kullanmak yerine kendi hafif httpx.AsyncClient'ını
  kurar (ayrı bağlantı havuzu, ayrı timeout — canlı emir istemcisiyle
  kaynak paylaşmaz).
- base_url verilmezse settings.binance_base_url kullanılır (BUGÜNKÜ
  davranış). Canlı motor D17'den beri SCALPER_MARKET_DATA_BASE_URL doluysa
  onu açıkça geçer: "piyasa verisi mainnet'ten, emirler testnet'te".
  Backtest harness'i zaten açıkça https://fapi.binance.com geçer — public
  veri, imza/API anahtarı gerekmediği ve emir yeteneği taşımadığı için bu
  güvenlidir.
- AĞIRLIK/BAN (D17): public istekler de artık başıboş DEĞİL. Host BAŞINA
  (MarketDataGuard) minimum istek aralığı + kayan 60 sn ağırlık bütçesi
  uygulanır; 418/429/-1003 yanıtı fail-closed bir ban devre kesicisi kurar
  (ban süresince HİÇBİR public istek ağa çıkmaz — ban sırasında istek atmak
  yasağı uzatır). Kesici paylaşımı TEK YÖNLÜDÜR: imzalı yolun AYNI host'taki
  banı public çekimi de durdurur (ucuz ve muhafazakâr), ama public ban imzalı
  kesiciyi KURMAZ — iki yol aynı host'a farklı çıkış IP'sinden gidebilir
  (BINANCE_BIND_IP yalnız imzalı istemciye uygulanır), bu yüzden public ban
  imzalı yolun banlı olduğunun kanıtı değildir (bkz. MarketDataGuard).
  AYRI host'ta durum tamamen ayrıdır — mainnet verisi banı testnet emir
  yönetimini kilitlemez.
- Son mum, HENÜZ KAPANMAMIŞSA (close_time > şimdi) HER ZAMAN atılır:
  oluşmakta olan mumun kapanmış gibi kullanılması (repaint) önlenir.
- TTL önbelleği: aynı (symbol, interval, limit) için kısa süreli tekrar
  isteği önler. end_time verilmişse (backtest sayfalama) önbellek BAŞTAN
  ATLANIR — her sayfa farklı bir zaman dilimini temsil eder ve önbelleğe
  alınırsa yanlış sayfa döndürülebilir.
- Hata durumunda ASLA sessiz [] dönmez: 3 deneme + üstel bekleme sonunda
  hâlâ başarısızsa istisna yükseltilir. Çağıran taraf (scanner/regime/
  setups) veri yokluğunu "sinyal yok" ile karıştırmamalı.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx

from src.core.config import settings
from src.core.logger import app_logger
from src.strategies.scalper.types import Candle
from src.trading.binance_client_improved import ImprovedBinanceClient


# Aralığa göre önbellek TTL'i (saniye). Mumun kapanış sıklığına göre
# ayarlıdır: 5m mum 5 dakikada bir kapanır, 20s TTL yeterince taze kalırken
# istek sayısını azaltır; 4h mum nadiren değiştiği için 300s'e kadar
# önbellekte kalabilir.
_TTL_BY_INTERVAL: Dict[str, float] = {
    "5m": 20.0,
    "15m": 60.0,
    "4h": 300.0,
}
_DEFAULT_TTL = 60.0

_KLINES_ENDPOINT = "/fapi/v1/klines"
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0  # saniye; deneme başına 2^n ile büyür (1s, 2s, 4s)

# --- Public market-data koruması (D17) ---------------------------------
# Binance USDⓈ-M Futures IP ağırlık sınırı 2400/dk'dır (mainnet dokümante;
# testnet başlığı edge-bazlı ve tutarsız — bkz. ARCHITECTURE §9). Public
# kline çekimi bunun küçük bir dilimiyle sınırlanır: HESAPLANAN canlı kullanım
# 8 sembollük allowlist'te ~41 istek/dk ≈ 82 ağırlık/dk (hesap:
# docs/ARCHITECTURE.md §2 "Kline ağırlık bütçesi"), yani bu bütçe ~7×
# başlıktır ve normal işletmede ASLA bağlamaz — patolojik bir döngünün
# (ör. TTL önbelleğini atlayan bir regresyon) mainnet IP'sini yakmasını
# önleyen bir tavan olarak durur.
_IP_WEIGHT_LIMIT_PER_MINUTE = 2400
_MARKET_DATA_WEIGHT_BUDGET_PER_MINUTE = 600
_WEIGHT_WINDOW_SECONDS = 60.0
# Host başına iki istek arasındaki asgari boşluk. Canlı tarama zaten SIRAYLA
# (sembol döngüsü) çeker; bu yalnız burst tavanıdır. İMZALI yolun küresel
# rate_limiter'ı (0.5 sn) BİLİNÇLİ olarak paylaşılmaz: public veri emir
# akışını asla bloklamamalı (modül docstring'i, 1. ilke) ve 12 sembol ×
# 3 TF × 0.5 sn ≈ 18 sn'lik bir tarama turu safety/exits çağrılarını da
# aynı kuyrukta bekletirdi (bkz. dashboard force-fresh açlığı olayı,
# docs/ARCHITECTURE.md §9).
_MIN_REQUEST_SPACING_SECONDS = 0.15
# Ban varsayılanları imzalı istemciyle AYNI (binance_client_improved).
_BAN_DEFAULT_SECONDS_BAN = 180.0   # 418 / -1003
_BAN_DEFAULT_SECONDS_SOFT = 90.0   # 429
_BREAKER_LOG_INTERVAL_SECONDS = 30.0
# X-MBX-USED-WEIGHT-1M telemetrisi: imzalı istemciyle aynı eşik, ama host
# başına 60 sn'de bir loglanır — testnet başlığı gürültülüdür (2026-08-21:
# günde ~1300 uyarı satırı, tek gerçek 418 yok).
_WEIGHT_WARN_THRESHOLD = 1800
_WEIGHT_LOG_INTERVAL_SECONDS = 60.0

# Guard modları:
#   "live"  — canlı motor: bütçe dolarsa BEKLEMEZ, hata yükseltir (tur atlanır).
#             Beklemek safety turunu bayatlatıp watchdog restart'ı tetikleyebilir.
#   "batch" — backtest harness'i (tek tüketici, safety döngüsü YOK): bütçe
#             dolunca pencere sonuna kadar BEKLER. Aksi halde uzun bir koşu
#             (ör. 8 sembol × 30 gün ≈ 656 ağırlık) ortasında ölürdü — araştırma
#             aracını kırmak kabul edilemez (düşmanca inceleme bulgusu).
_GUARD_MODE_LIVE = "live"
_GUARD_MODE_BATCH = "batch"

# asyncio.sleep'e modül düzeyi dolaylama: testler KÜRESEL asyncio.sleep'i
# yamalamak zorunda kalmasın (yamalanırsa süreçteki her döngü — rate_limiter,
# motor scan/safety — sahte uykuya düşer ve busy-loop riski doğar).
_sleep = asyncio.sleep


class MarketDataUnavailable(RuntimeError):
    """Public market-data isteği GÖNDERİLMEDİ; koruma devreye girdi.

    Çağıranlar (engine._evaluate_symbol / exits._update_trailing) bunu
    normal bir veri hatası gibi yakalar: sinyal üretilmez, trailing turu
    atlanır — borsadaki SL/TP emirleri yerinde kalır (fail-closed).
    """

    def __init__(self, message: str, host: str):
        super().__init__(message)
        self.host = host


class MarketDataBanError(MarketDataUnavailable):
    """Host ban altında (418/429/-1003) ya da ban sırasında istek istendi."""

    def __init__(self, message: str, host: str, blocked_until: float):
        super().__init__(message, host)
        self.blocked_until = blocked_until


class MarketDataRequestError(RuntimeError):
    """Kalıcı istemci hatası (4xx, ban DIŞI) — ör. `-1121 Invalid symbol`.

    `MarketDataUnavailable` DEĞİLDİR: host geneli bir kesinti değil, TEK
    sembolün sorunudur. Bu yüzden tarama turu kesilmez, yalnız o sembol atlanır
    (`engine._scan_tick`'in jenerik `except Exception` dalı).
    """


class MarketDataBudgetError(MarketDataUnavailable):
    """Kayan 60 sn ağırlık bütçesi dolu — istek ATILMADI.

    Neden beklemek yerine hata: bekleme host kilidi altında olurdu ve bir
    tur (en fazla 60 sn) boyunca safety/exits'in mum çekimini de bloklardı;
    safety döngüsünün tazelik limiti 30 sn'dir (`health_snapshot`) → watchdog
    "safety_task_stale" restart'ı tetiklenebilirdi ve restart, tarihsel
    felaket yolunun ta kendisidir (2026-08-14: ban ortasında restart →
    toplu UNKNOWN kapanış). Turu atlamak bedava ve fail-closed'dır.
    """


def klines_weight(limit: int) -> int:
    """/fapi/v1/klines IP ağırlığı (Binance dokümantasyonu, limit'e göre).

    Canlı motorun kullandığı limitler: 250 (rejim), 100 (bağlam), 150
    (giriş) ve 200 (exits trailing) → hepsi 2 ağırlık.
    """
    if limit < 100:
        return 1
    if limit < 500:
        return 2
    if limit <= 1000:
        return 5
    return 10


def host_of(base_url: str) -> str:
    """URL'den host (netloc). Şemasız/bozuk girdide URL'in kendisi döner —
    teşhis logu asla boş kalmasın."""
    parsed = urlparse(str(base_url or ""))
    return (parsed.netloc or str(base_url or "")).lower()


class _HostGuardState:
    """Host başına oran/ağırlık/ban durumu."""

    __slots__ = (
        "lock", "last_request_at", "window_start", "window_weight",
        "blocked_until", "last_breaker_log", "last_weight_log",
        "last_budget_log", "last_used_weight",
    )

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.last_request_at: float = 0.0      # time.monotonic()
        self.window_start: float = 0.0         # time.monotonic()
        self.window_weight: int = 0
        self.blocked_until: float = 0.0        # time.time() (epoch sn)
        self.last_breaker_log: float = 0.0
        self.last_weight_log: float = 0.0
        self.last_budget_log: float = 0.0
        self.last_used_weight: int = 0


class MarketDataGuard:
    """Public (imzasız) market-data istekleri için host BAŞINA oran
    sınırlayıcı + ağırlık bütçesi + ban devre kesicisi.

    İmzalı yolun (`ImprovedBinanceClient`) küresel `rate_limiter`'ı ile AYNI
    semantik: slot asyncio.Lock ALTINDA rezerve edilir (kilitsiz
    check-then-act yarışı yok — bkz. src/core/rate_limiter.py docstring'i),
    418/-1003 fail-closed kesici kurar ve kesici aktifken hiçbir istek ağa
    çıkmaz. FARK (bilinçli): ayrı sayaç/kilit kullanılır, çünkü public veri
    emir akışını bloklamamalıdır (modül docstring'i, 1. ilke).

    Kesici TEK YÖNLÜ paylaşılır (bilinçli asimetri):
      - İmzalı yol (`ImprovedBinanceClient._rest_blocked_until`) AYNI host'ta
        ban yemişse public çekim de DURUR: ban sırasında istek atmak yasağı
        uzatır (2026-08-12 olayı) ve kaybedilen tek şey 180 sn'lik sinyal
        üretimidir — borsadaki SL/TP emirleri yerinde kalır.
      - Public yolun banı imzalı kesiciyi KURMAZ: `KlineFetcher`ın httpx
        istemcisi `BINANCE_BIND_IP`'ye bind edilmez, yani iki yol aynı host'a
        farklı çıkış IP'sinden gidebilir; public ban imzalı yolun da banlı
        olduğunun kanıtı değildir. Emir/çıkış yönetimini kanıtsız durdurmak
        (SL değişimi, kapanış doğrulaması) para tarafında en pahalı hatadır.
    AYRI host'ta zaten tamamen ayrıdır: Binance ağırlık sayacı host/küme
    başınadır (mainnet fapi ile testnet ayrı kümelerdir), mainnet verisi banı
    testnet emir yönetimini kilitlememelidir.
    """

    _states: Dict[str, _HostGuardState] = {}

    # -- durum ----------------------------------------------------------
    @classmethod
    def _state(cls, host: str) -> _HostGuardState:
        state = cls._states.get(host)
        if state is None:
            state = _HostGuardState()
            cls._states[host] = state
        return state

    @classmethod
    def reset(cls) -> None:
        """Yalnız testler için: süreç-geneli durumu temizler."""
        cls._states = {}

    @staticmethod
    def _shares_trading_host(host: str) -> bool:
        return host == host_of(settings.binance_base_url)

    @classmethod
    def blocked_until(cls, host: str) -> float:
        """Bu host için ban bitişi (epoch sn); 0 = ban yok."""
        until = cls._state(host).blocked_until
        if cls._shares_trading_host(host):
            until = max(until, float(ImprovedBinanceClient._rest_blocked_until or 0.0))
        return until

    @classmethod
    def trip(cls, host: str, message: str, default_seconds: float) -> float:
        """Ban mesajından bitişi çöz ve kesiciyi kur (asla KISALTMAZ).

        Desen imzalı istemciyle tek kaynaktan gelir (`_BAN_UNTIL_RE`), böylece
        iki yol "banned until <epoch>" ayrıştırmasında ayrışamaz.
        """
        state = cls._state(host)
        until = time.time() + default_seconds
        match = ImprovedBinanceClient._BAN_UNTIL_RE.search(message or "")
        if match:
            raw = float(match.group(1))
            parsed = raw / 1000.0 if raw > 1e12 else raw
            if parsed > time.time():
                until = parsed + 5.0
        state.blocked_until = max(state.blocked_until, until)
        # BİLİNÇLİ ASİMETRİ (sınıf docstring'i): public ban imzalı
        # yolun kesicisini KURMAZ. Gerekçe: KlineFetcher'ın httpx istemcisi
        # BINANCE_BIND_IP'ye bind EDİLMEZ (yalnız ImprovedBinanceClient
        # edilir), yani iki yol aynı host'a FARKLI çıkış IP'lerinden gidebilir
        # ve ağırlık/ban IP başınadır. Public tarafın banı, imzalı tarafın da
        # banlı olduğunun KANITI değildir; emir/çıkış yönetimini kanıtsız
        # durdurmak (SL değişimi, kapanış doğrulaması) para tarafında en
        # pahalı hatadır. İmzalı yol kendi 418'ini görürse kendi kesicisini
        # zaten kurar.
        return state.blocked_until

    @classmethod
    def ensure_allowed(cls, host: str, endpoint: str = _KLINES_ENDPOINT) -> None:
        """Ban aktifse isteği ağa ÇIKARMADAN MarketDataBanError yükseltir."""
        blocked_until = cls.blocked_until(host)
        now = time.time()
        if now >= blocked_until:
            return
        state = cls._state(host)
        if now - state.last_breaker_log > _BREAKER_LOG_INTERVAL_SECONDS:
            state.last_breaker_log = now
            # "banned until" ifadesi BİLİNÇLİ: scripts/server_deploy.sh
            # deploy'dan önce son 15 dk'da `HTTP 418|banned` arar. Tek seferlik
            # trip satırı 15 dk sonra pencereden düşer; SÜREN ban boyunca bu
            # periyodik satır kilidi açık tutar (ban aktifken restart YASAK).
            app_logger.warning(
                f"🚫 Piyasa verisi devre kesici aktif ({host}): IP banned until "
                f"{datetime.fromtimestamp(blocked_until, tz=timezone.utc).isoformat()} "
                f"({blocked_until - now:.0f} sn), {endpoint} isteği atılmadı"
            )
        raise MarketDataBanError(
            f"Piyasa verisi devre kesici aktif ({host}); ban bitişine "
            f"{blocked_until - now:.0f} sn",
            host,
            blocked_until,
        )

    # -- slot rezervasyonu ----------------------------------------------
    @classmethod
    async def acquire(
        cls, base_url: str, weight: int, mode: str = _GUARD_MODE_LIVE
    ) -> None:
        """İsteği göndermeden ÖNCE çağrılır: ban kontrolü + aralık + bütçe.

        Slot host kilidi ALTINDA atomik rezerve edilir; tek bekleme asgari
        aralıktır (≤0.15 sn). Bütçe dolarsa `mode`a göre:
          - "live"  → `MarketDataBudgetError` (gerekçe: o istisnanın
            docstring'i) — çağıran turu atlar, olay döngüsü bloklanmaz.
          - "batch" → pencere sonuna kadar bekler (harness; safety döngüsü yok,
            uzun koşu ortada ölmemeli).
        Ban her iki modda da fail-closed: beklenmez, hata yükselir.
        """
        host = host_of(base_url)
        state = cls._state(host)
        cls.ensure_allowed(host)
        async with state.lock:
            # Kilidi beklerken başka bir görev ban yemiş olabilir.
            cls.ensure_allowed(host)

            now = time.monotonic()
            spacing_wait = state.last_request_at + _MIN_REQUEST_SPACING_SECONDS - now
            if spacing_wait > 0:
                await _sleep(spacing_wait)
                now = time.monotonic()

            if now - state.window_start >= _WEIGHT_WINDOW_SECONDS:
                state.window_start = now
                state.window_weight = 0

            if state.window_weight + weight > _MARKET_DATA_WEIGHT_BUDGET_PER_MINUTE:
                remaining = max(0.0, _WEIGHT_WINDOW_SECONDS - (now - state.window_start))
                if now - state.last_budget_log > _WEIGHT_LOG_INTERVAL_SECONDS:
                    state.last_budget_log = now
                    app_logger.warning(
                        f"⚖️ Piyasa verisi ağırlık bütçesi doldu ({host}, mod={mode}): "
                        f"{state.window_weight}/{_MARKET_DATA_WEIGHT_BUDGET_PER_MINUTE} "
                        f"ağırlık/dk — pencere {remaining:.0f} sn sonra sıfırlanır "
                        f"(IP sınırı {_IP_WEIGHT_LIMIT_PER_MINUTE}/dk)"
                    )
                if mode == _GUARD_MODE_BATCH:
                    # Harness: tek tüketici, safety döngüsü yok → beklemek
                    # güvenlidir ve koşuyu ortada öldürmekten iyidir.
                    if remaining > 0:
                        await _sleep(remaining)
                    cls.ensure_allowed(host)
                    state.window_start = time.monotonic()
                    state.window_weight = 0
                else:
                    raise MarketDataBudgetError(
                        f"Piyasa verisi ağırlık bütçesi dolu ({host}): "
                        f"{state.window_weight}/{_MARKET_DATA_WEIGHT_BUDGET_PER_MINUTE} "
                        f"ağırlık/dk",
                        host,
                    )

            state.window_weight += weight
            state.last_request_at = time.monotonic()

    # -- telemetri -------------------------------------------------------
    @classmethod
    def note_response(cls, base_url: str, headers: Any) -> None:
        """X-MBX-USED-WEIGHT-1M başlığını kaydet; eşiği aşarsa (host başına
        60 sn'de bir) uyar. Ölçüm — tahmin değil."""
        try:
            raw = headers.get("X-MBX-USED-WEIGHT-1M")
        except Exception:
            return
        if raw is None:
            return
        try:
            used = int(raw)
        except (TypeError, ValueError):
            return
        host = host_of(base_url)
        state = cls._state(host)
        state.last_used_weight = used
        now = time.monotonic()
        if used >= _WEIGHT_WARN_THRESHOLD and (
            now - state.last_weight_log > _WEIGHT_LOG_INTERVAL_SECONDS
        ):
            state.last_weight_log = now
            app_logger.warning(
                f"⚖️ Piyasa verisi 1dk kullanılan ağırlık: {used} ({host}) "
                f"| kendi public bütçemiz: {state.window_weight}/"
                f"{_MARKET_DATA_WEIGHT_BUDGET_PER_MINUTE}"
            )

    @classmethod
    def snapshot(cls, base_url: str) -> Dict[str, Any]:
        """Teşhis: host, ban durumu, son ölçülen ağırlık (secret içermez)."""
        host = host_of(base_url)
        state = cls._state(host)
        blocked_until = cls.blocked_until(host)
        return {
            "host": host,
            "banned": time.time() < blocked_until,
            "blocked_until": blocked_until or None,
            "used_weight_1m": state.last_used_weight or None,
            "window_weight": state.window_weight,
            "weight_budget_per_minute": _MARKET_DATA_WEIGHT_BUDGET_PER_MINUTE,
        }


class KlineFetcher:
    """Public /fapi/v1/klines üzerinden mum verisi çeker ve kısa süreli
    TTL önbelleğiyle tekrar isteği önler."""

    def __init__(
        self, base_url: Optional[str] = None, guard_mode: str = _GUARD_MODE_LIVE
    ):
        self.base_url = base_url or settings.binance_base_url
        # Teşhis/log için host (secret içermez). Ağırlık/ban durumu host
        # başınadır — bkz. MarketDataGuard.
        self.host = host_of(self.base_url)
        # "live" (canlı motor: bütçe dolunca hata) | "batch" (harness: bekler).
        self.guard_mode = (
            _GUARD_MODE_BATCH if guard_mode == _GUARD_MODE_BATCH else _GUARD_MODE_LIVE
        )
        self.logger = app_logger

        # Kendi bağlantı havuzu: imzalı emir istemcisinden (ImprovedBinanceClient)
        # bağımsız — public veri çekimi emir akışını asla bloklamamalı.
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=10.0),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )

        # (symbol, interval, limit) -> (mumlar, önbelleğe_alma_zamanı_monotonic)
        self._cache: Dict[Tuple[str, str, int], Tuple[List[Candle], float]] = {}
        # ANAHTAR BAŞINA kilit (D17 inceleme bulgusu): tek bir paylaşılan kilit,
        # yavaş/erişilemez bir host'ta (timeout 15 sn × 3 deneme ≈ 48 sn) BAŞKA
        # sembollerin mum çekimini de bloklardı — safety turunun tazelik limiti
        # 30 sn'dir (`engine.health_snapshot`), yani head-of-line blocking
        # watchdog restart'ına dönüşebilirdi. Ayrı market-data host'u bu riski
        # gerçekçi kılar (emir host'u sağlamken veri host'u yavaşlayabilir).
        self._cache_locks: Dict[Tuple[str, str, int], asyncio.Lock] = {}

    @staticmethod
    def _ttl_for(interval: str) -> float:
        return _TTL_BY_INTERVAL.get(interval, _DEFAULT_TTL)

    async def get_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 200,
        end_time: Optional[int] = None,
    ) -> List[Candle]:
        """Verilen sembol/aralık için mum listesini döndürür (eski→yeni,
        son eleman kapanmış en güncel mumdur).

        end_time (epoch ms) verilirse önbellek atlanır ve doğrudan borsadan
        çekilir — backtest'in tarihsel sayfalaması için gereklidir.

        Hata durumunda istisna yükseltir (sessiz [] YOK).
        """
        if end_time is not None:
            return await self._fetch(symbol, interval, limit, end_time)

        cache_key = (symbol, interval, limit)
        cached = self._cache.get(cache_key)
        if cached is not None and (time.monotonic() - cached[1]) < self._ttl_for(interval):
            return cached[0]

        lock = self._cache_locks.get(cache_key)
        if lock is None:
            # Tek event-loop: get/None-kontrol/set arasında await yok → atomik.
            lock = asyncio.Lock()
            self._cache_locks[cache_key] = lock

        async with lock:
            # Kilidi beklerken başka bir görev doldurmuş olabilir
            cached = self._cache.get(cache_key)
            if cached is not None and (time.monotonic() - cached[1]) < self._ttl_for(interval):
                return cached[0]

            candles = await self._fetch(symbol, interval, limit, None)
            self._cache[cache_key] = (candles, time.monotonic())
            return candles

    async def _fetch(
        self, symbol: str, interval: str, limit: int, end_time: Optional[int]
    ) -> List[Candle]:
        params: Dict[str, object] = {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }
        if end_time is not None:
            params["endTime"] = end_time

        weight = klines_weight(int(limit))
        last_error: Optional[Exception] = None
        for attempt in range(_MAX_RETRIES):
            try:
                # Ban kontrolü + oran/ağırlık slotu. Ban ya da bütçe
                # devredeyse `MarketDataUnavailable`, kalıcı bir 4xx'te
                # `MarketDataRequestError` yükselir; ikisi de AŞAĞIDAKİ retry
                # bloğuna DÜŞMEZ (ayrı tipler): ban sırasında tekrar denemek
                # yasağı uzatır, bütçe dolmuşken ve kalıcı hatada tekrar
                # denemek anlamsızdır.
                await MarketDataGuard.acquire(self.base_url, weight, self.guard_mode)

                response = await self._client.get(
                    f"{self.base_url}{_KLINES_ENDPOINT}", params=params
                )
                MarketDataGuard.note_response(self.base_url, response.headers)
                if response.status_code >= 400:
                    self._raise_if_banned(response)
                    self._raise_if_permanent(response, symbol, interval)
                response.raise_for_status()
                raw = response.json()
                candles = [Candle.from_binance(row) for row in raw]
                return self._drop_unclosed(candles)

            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                last_error = e
                if attempt < _MAX_RETRIES - 1:
                    wait = _RETRY_BASE_DELAY * (2 ** attempt)
                    self.logger.warning(
                        f"Kline çekme hatası ({symbol} {interval}), "
                        f"{wait}s sonra tekrar (deneme {attempt + 1}/{_MAX_RETRIES}): {e}"
                    )
                    await _sleep(wait)
                    continue
                self.logger.error(
                    f"Kline çekme başarısız ({symbol} {interval}, "
                    f"{_MAX_RETRIES} deneme sonrası): {e}"
                )

        raise last_error or RuntimeError(
            f"Kline çekme {_MAX_RETRIES} denemeden sonra başarısız: "
            f"{symbol} {interval}"
        )

    def _raise_if_permanent(
        self, response: httpx.Response, symbol: str, interval: str
    ) -> None:
        """Kalıcı istemci hatalarını (4xx, ban DIŞI) TEKRARSIZ yükselt.

        `-1121 Invalid symbol` gibi bir hata kendiliğinden düzelmez: 3 deneme
        (1 sn + 2 sn uyku) sembol başına ~3 sn'yi ve 2 gereksiz isteği boşa
        harcar. Ayrı market-data host'u bu senaryoyu gerçekçi kılar (işlem
        host'unda olup veri host'unda olmayan sembol) — 12 sembollük bir
        evrende tarama turu 30 sn'yi bulabilirdi (düşmanca inceleme bulgusu).
        5xx ve ağ hataları geçicidir; onlar ESKİSİ GİBİ 3 kez denenir.
        """
        status = response.status_code
        if not (400 <= status < 500):
            return
        code: Optional[int] = None
        message = response.text
        try:
            body = response.json()
            code = body.get("code")
            message = body.get("msg", response.text)
        except Exception:
            pass
        self.logger.error(
            f"Kline çekme kalıcı hata ({symbol} {interval}, {self.host}): "
            f"HTTP {status} code={code} — {message} (tekrar denenmedi)"
        )
        # BİLİNÇLİ: `MarketDataUnavailable` DEĞİL. O tip host GENELİ bir
        # kesintiyi ifade eder ve `_scan_tick` turu keser; bu hata ise TEK
        # sembole aittir — kalan semboller taranmaya devam etmeli.
        raise MarketDataRequestError(
            f"HTTP {status} (code={code}) {self.host}: {message}"
        )

    def _raise_if_banned(self, response: httpx.Response) -> None:
        """418/429/-1003 ise kesiciyi kur ve MarketDataBanError yükselt.

        Log satırı BİLİNÇLİ olarak "HTTP 418"/"banned" içerir: `scripts/
        server_deploy.sh` deploy'dan önce `logs/bot.log`'da tam bu kalıbı
        arar ("son 15 dk'da Binance ban izi var — ban aktifken restart
        YASAK"). Public yolun banı bu güvenliğe GÖRÜNÜR olmalıdır; D17
        öncesinde görünmüyordu (httpx'in kendi mesajı bu kalıba uymaz).
        """
        status = response.status_code
        code: Optional[int] = None
        message = response.text
        try:
            body = response.json()
            code = body.get("code")
            message = body.get("msg", response.text)
        except Exception:
            pass

        if status not in (418, 429) and code != -1003:
            return

        default_seconds = (
            _BAN_DEFAULT_SECONDS_SOFT if status == 429 and code != -1003
            else _BAN_DEFAULT_SECONDS_BAN
        )
        until = MarketDataGuard.trip(self.host, str(message or ""), default_seconds)
        until_iso = datetime.fromtimestamp(until, tz=timezone.utc).isoformat()
        shared = MarketDataGuard._shares_trading_host(self.host)
        # "HTTP <status>" + "banned until" BİLİNÇLİ: deploy ban kilidi
        # (`scripts/server_deploy.sh`, `grep -qE 'HTTP 418|banned'`) 429/-1003
        # ile gelen İLK ban sinyalini de görmeli — 418 ancak ban sırasında
        # istek atmaya devam edilirse gelir ve D17 tasarımı gereği devam etmeyiz.
        self.logger.critical(
            f"🚫 Piyasa verisi IP ban (HTTP {status}, code={code}, {self.host}): "
            f"IP banned until {until_iso} — public kline istekleri durduruldu; "
            "imzalı REST bu bandan ETKİLENMEZ (kendi kesicisi, kendi kanıtı)"
            + ("; host işlem host'uyla AYNI, imzalı yol da 418 görürse kendi "
               "kesicisini kurar" if shared else "; host AYRI")
        )
        raise MarketDataBanError(
            f"HTTP {status} (code={code}) {self.host}: {message}", self.host, until
        )

    @staticmethod
    def _drop_unclosed(candles: List[Candle]) -> List[Candle]:
        """Son mum henüz kapanmamışsa (close_time > şimdi) at — repaint önlenir."""
        if not candles:
            return candles
        now_ms = int(time.time() * 1000)
        if candles[-1].close_time > now_ms:
            return candles[:-1]
        return candles

    async def close(self) -> None:
        await self._client.aclose()
