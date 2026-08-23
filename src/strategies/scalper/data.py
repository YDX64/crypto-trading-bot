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
from collections import deque
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Deque, Dict, List, Optional, Tuple
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
#
# ⚠️ `1m` BURADA OLMAK ZORUNDA (düşmanca inceleme bulgusu): CANLI profil
# `SCALPER_TF_ENTRY=1m`'dir ve `_DEFAULT_TTL` 60 sn'dir — yani giriş dilimi
# tablodan düşseydi trailing/giriş TAM BİR MUM bayat veriyle karar verirdi
# (chandelier son kapanmış mumu hiç görmeden bir tur daha eski seviyeyi
# gönderirdi). TTL, mum periyodunun küçük bir kesri olmalı: 1m için 5 sn
# (mum süresinin ~%8'i), 5m için 20 sn (~%7), 15m için 60 sn (~%7).
_TTL_BY_INTERVAL: Dict[str, float] = {
    "1m": 5.0,
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
# (CANLI profil 1m/5m/15m, 8 sembol + 3 açık pozisyon) 70 istek/dk ≈ 140
# ağırlık/dk; `SCALPER_TOP_N=12` ile 90 istek/dk ≈ 180 ağırlık/dk (hesap:
# docs/ARCHITECTURE.md §2 "Kline ağırlık bütçesi"), yani bu bütçe hesaplananın
# ~3-4 katıdır ve normal işletmede bağlamaz — patolojik bir döngünün (ör. TTL
# önbelleğini atlayan bir regresyon) mainnet IP'sini yakmasını önleyen bir
# tavan olarak durur. ⚠️ HESAPTIR, ölçüm değil (D17 terfi adımı (c)).
_IP_WEIGHT_LIMIT_PER_MINUTE = 2400
_MARKET_DATA_WEIGHT_BUDGET_PER_MINUTE = 600
# Harness (backtest) profili AYRIDIR: `limit=1500` sayfaları ağırlık 10 eder ve
# koşu tek süreçte SIRAYLA ilerler (safety döngüsü yok, emir akışı yok). Canlı
# bütçe burada bir güvenlik değil yalnız yavaşlatmadır: 600/dk tavanı 8 sembol ×
# 30 günlük bir çekimi (≈656 ağırlık) pencere beklemeleriyle ~3× uzatıyordu
# (düşmanca inceleme bulgusu — araştırma aracını yavaşlatmak kanıt üretmeyi
# yavaşlatır). Harness bütçesi IP sınırının %50'sine çıkarılır; 429/418 koruması
# AYNEN sürer (kesici moddan bağımsızdır), yani gerçek sınır yine borsanınkidir.
_BATCH_WEIGHT_BUDGET_PER_MINUTE = 1200
_WEIGHT_WINDOW_SECONDS = 60.0
# Host başına iki istek arasındaki asgari boşluk. Canlı tarama zaten SIRAYLA
# (sembol döngüsü) çeker; bu yalnız burst tavanıdır. İMZALI yolun küresel
# rate_limiter'ı (0.5 sn) BİLİNÇLİ olarak paylaşılmaz: public veri emir
# akışını asla bloklamamalı (modül docstring'i, 1. ilke) ve 12 sembol ×
# 3 TF × 0.5 sn ≈ 18 sn'lik bir tarama turu safety/exits çağrılarını da
# aynı kuyrukta bekletirdi (bkz. dashboard force-fresh açlığı olayı,
# docs/ARCHITECTURE.md §9).
_MIN_REQUEST_SPACING_SECONDS = 0.15
# Harness sayfalama yaparken 0.15 sn × yüzlerce sayfa tek başına dakikalar
# eder; tek tüketici olduğu için burst riski yoktur.
_BATCH_MIN_REQUEST_SPACING_SECONDS = 0.05
# "batch" modunda bütçe için azami art arda bekleme sayısı (sonsuz döngü
# kalkanı). Normalde 1 bekleme yeter: en eski ağırlık girdisi pencereden düşer.
_BATCH_BUDGET_MAX_WAITS = 10
# Ban varsayılanları imzalı istemciyle AYNI (binance_client_improved).
_BAN_DEFAULT_SECONDS_BAN = 180.0   # 418 / -1003 (GERÇEK ban)
# 429 TEK BAŞINA ban değildir: "yavaşla" demektir. 90 sn'lik bir kesici, ayar
# BOŞKEN (kline'lar işlem host'undan) tek bir 429 yüzünden 1.5 dakika sinyal
# üretimini durduruyordu. Öncelik sırası: Retry-After başlığı →
# X-MBX-USED-WEIGHT-1M (sınır aşıldıysa pencere sonu) → bu varsayılan.
_BAN_DEFAULT_SECONDS_SOFT = 30.0   # 429 (soft throttle)
# Host GENELİ engel (401/403 WAF, 451 coğrafi): sembolle ilgisi yoktur ve
# tekrar denemek düzeltmez. Kısa bir kesici hem istek israfını hem log selini
# keser; kalıcıysa her pencerede bir kez yeniden denenir.
_HOST_BLOCK_DEFAULT_SECONDS = 60.0
# Retry-After / ban süresi için akıl sağlığı sınırları.
_MIN_BREAKER_SECONDS = 1.0
_MAX_BREAKER_SECONDS = 3600.0
_BREAKER_LOG_INTERVAL_SECONDS = 30.0
# Sembol KAPSAMLI 4xx'ler: yalnız bu ikisi tek sembole ait sayılır (`-1121
# Invalid symbol` 400 ile gelir). Diğer 4xx'ler (401/403/451/405/409 ...) host
# genelidir: kimlik/WAF/coğrafi engel bir sonraki sembolde de aynen sürer.
_SYMBOL_SCOPED_STATUSES = (400, 404)
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


class MarketDataHostError(MarketDataUnavailable):
    """Host GENELİ engel — sembolle ilgisi yok, tekrar denemek düzeltmez.

    İki kaynağı vardır:
      * ban DIŞI host-geneli 4xx: 401/403 (kimlik/WAF), 451 (coğrafi engel) ve
        sembol kapsamlı olmayan diğer 4xx'ler. Düşmanca inceleme bulgusu
        (HIGH): bunlar D17'de `MarketDataRequestError` (SEMBOL kapsamlı)
        sayılıyordu → 12 sembolün 12'si de aynı 403'ü alıyor, tur kesilmiyor,
        kesici kurulmuyor ve deploy ban kilidi kör kalıyordu. Bir WAF/geo
        engeli ise tam olarak "bu host'tan veri gelmiyor" demektir.
      * tükenmiş 5xx denemeleri: 3 deneme sonunda hâlâ 5xx ise sorun sembolde
        değil host'tadır; tur kesilmeli (kalan 11 sembol için 33 istek daha
        atmanın anlamı yok).
    `MarketDataUnavailable` alt sınıfıdır → `_scan_tick` turu keser,
    `exits._update_trailing` turu atlar, `/tv-signal` yapısal ret döner.
    """


class MarketDataRequestError(RuntimeError):
    """Kalıcı SEMBOL kapsamlı istemci hatası — ör. `-1121 Invalid symbol` (400).

    `MarketDataUnavailable` DEĞİLDİR: host geneli bir kesinti değil, TEK
    sembolün sorunudur. Bu yüzden tarama turu kesilmez, yalnız o sembol atlanır
    (`engine._scan_tick`'in jenerik `except Exception` dalı). Yalnız 400 ve 404
    bu sınıfa girer (`_SYMBOL_SCOPED_STATUSES`); host geneli 4xx'ler
    `MarketDataHostError`dır.
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


def _header(headers: Any, name: str) -> Optional[str]:
    """Başlığı güvenle oku (test çiftleri düz dict verebilir)."""
    try:
        raw = headers.get(name)
    except Exception:
        return None
    return None if raw is None else str(raw)


def retry_after_seconds(headers: Any) -> Optional[float]:
    """`Retry-After` başlığını saniyeye çevir (saniye ya da HTTP-date).

    Borsa "ne kadar bekle" diyorsa TAHMİN etmeyiz: 429'da kesici süresi önce
    bu başlıktan gelir. Akıl sağlığı sınırları uygulanır (1 sn … 1 saat) —
    bozuk/kötü niyetli bir başlık botu saatlerce susturmasın.
    """
    raw = _header(headers, "Retry-After")
    if raw is None:
        return None
    raw = raw.strip()
    seconds: Optional[float] = None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        try:
            when = parsedate_to_datetime(raw)
        except Exception:
            return None
        if when is None:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        seconds = when.timestamp() - time.time()
    if seconds is None or seconds != seconds:  # NaN
        return None
    return max(_MIN_BREAKER_SECONDS, min(_MAX_BREAKER_SECONDS, float(seconds)))


def used_weight_1m(headers: Any) -> Optional[int]:
    """`X-MBX-USED-WEIGHT-1M` başlığını oku (yoksa/bozuksa None)."""
    raw = _header(headers, "X-MBX-USED-WEIGHT-1M")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def host_of(base_url: str) -> str:
    """URL'den host (netloc). Şemasız/bozuk girdide URL'in kendisi döner —
    teşhis logu asla boş kalmasın."""
    parsed = urlparse(str(base_url or ""))
    return (parsed.netloc or str(base_url or "")).lower()


class _HostGuardState:
    """Host başına oran/ağırlık/ban durumu.

    Ağırlık penceresi KAYANDIR (dokümante edilen davranış buydu; D17'de
    yanlışlıkla "tumbling" — sabit sınırlı — kodlanmıştı): `window` içinde
    (monotonic_zaman, ağırlık) çiftleri tutulur ve 60 sn'den eskiler düşer.
    Tumbling pencerede sınır tam pencere sınırında sıfırlandığı için pratik
    tavan iki katına çıkabiliyordu (pencerenin sonunda 600 + hemen ardından
    yeni pencerede 600 = 1 dakikaya sığan 1200).
    """

    __slots__ = (
        "lock", "last_request_at", "window", "window_weight",
        "blocked_until", "hard_ban", "last_breaker_log", "last_weight_log",
        "last_budget_log", "last_used_weight",
    )

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.last_request_at: float = 0.0      # time.monotonic()
        # (monotonic zaman damgası, ağırlık) — en eski solda.
        self.window: Deque[Tuple[float, int]] = deque()
        self.window_weight: int = 0            # `window`ın toplamı (önbellek)
        self.blocked_until: float = 0.0        # time.time() (epoch sn)
        # GERÇEK ban mı (418 / -1003 / "banned until") yoksa yalnız soft
        # throttle mı (tek başına 429, 403/451 host engeli)? Deploy ban kilidi
        # (`scripts/server_deploy.sh`, `HTTP 418|banned`) YALNIZ gerçek banda
        # tetiklenmeli — tek bir 429 deploy'u 15 dk kilitlememeli.
        self.hard_ban: bool = False
        self.last_breaker_log: float = 0.0
        self.last_weight_log: float = 0.0
        self.last_budget_log: float = 0.0
        self.last_used_weight: int = 0

    # -- kayan pencere ---------------------------------------------------
    def prune(self, now: float) -> None:
        """60 sn'den eski ağırlık girdilerini düşür."""
        cutoff = now - _WEIGHT_WINDOW_SECONDS
        window = self.window
        while window and window[0][0] <= cutoff:
            self.window_weight -= window.popleft()[1]
        if not window:
            self.window_weight = 0

    def add(self, now: float, weight: int) -> None:
        self.window.append((now, weight))
        self.window_weight += weight

    def seconds_until_free(self, now: float) -> float:
        """En eski girdinin pencereden düşmesine kalan süre (sn)."""
        if not self.window:
            return 0.0
        return max(0.0, self.window[0][0] + _WEIGHT_WINDOW_SECONDS - now)


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
    def is_hard_ban(cls, host: str) -> bool:
        """Aktif kesici GERÇEK ban mı (418/-1003/"banned until")?

        İmzalı yolun AYNI host'taki kesicisi her zaman gerçek ban sayılır
        (`ImprovedBinanceClient` yalnız ban için kesici kurar).
        """
        state = cls._state(host)
        if state.hard_ban and time.time() < state.blocked_until:
            return True
        if cls._shares_trading_host(host):
            return time.time() < float(ImprovedBinanceClient._rest_blocked_until or 0.0)
        return False

    @classmethod
    def trip(
        cls,
        host: str,
        message: str,
        default_seconds: float,
        *,
        hard: bool = True,
    ) -> float:
        """Ban mesajından bitişi çöz ve kesiciyi kur (asla KISALTMAZ).

        Desen imzalı istemciyle tek kaynaktan gelir (`_BAN_UNTIL_RE`), böylece
        iki yol "banned until <epoch>" ayrıştırmasında ayrışamaz.

        `hard=False`: GERÇEK ban değil, yalnız soft throttle (tek başına 429)
        ya da host engeli (403/451). Kesici aynı şekilde kurulur (fail-closed)
        ama log dili "banned" DEMEZ — `scripts/server_deploy.sh`'nin
        `HTTP 418|banned` deploy kilidi yalnız gerçek banda kapanmalıdır.
        """
        state = cls._state(host)
        until = time.time() + default_seconds
        match = ImprovedBinanceClient._BAN_UNTIL_RE.search(message or "")
        if match:
            raw = float(match.group(1))
            parsed = raw / 1000.0 if raw > 1e12 else raw
            if parsed > time.time():
                until = parsed + 5.0
                # Mesajda açık bir "banned until" varsa bu GERÇEK bandır.
                hard = True
        # Önceki kesici HÂLÂ aktifse "hard" bayrağı asla geri alınmaz (aynı
        # pencerede önce 429 (soft), sonra 418 (hard) gelirse durum HARD
        # kalır); süresi dolmuş eski bir hard ban ise yeni soft throttle'ı
        # kirletmemelidir.
        previously_active = time.time() < state.blocked_until
        state.blocked_until = max(state.blocked_until, until)
        state.hard_ban = bool(hard or (previously_active and state.hard_ban))
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
        hard = cls.is_hard_ban(host)
        if now - state.last_breaker_log > _BREAKER_LOG_INTERVAL_SECONDS:
            state.last_breaker_log = now
            until_iso = datetime.fromtimestamp(
                blocked_until, tz=timezone.utc
            ).isoformat()
            if hard:
                # "banned until" ifadesi BİLİNÇLİ: scripts/server_deploy.sh
                # deploy'dan önce son 15 dk'da `HTTP 418|banned` arar. Tek
                # seferlik trip satırı 15 dk sonra pencereden düşer; SÜREN ban
                # boyunca bu periyodik satır kilidi açık tutar (ban aktifken
                # restart YASAK).
                app_logger.warning(
                    f"🚫 Piyasa verisi devre kesici aktif ({host}): IP banned until "
                    f"{until_iso} ({blocked_until - now:.0f} sn), {endpoint} "
                    "isteği atılmadı"
                )
            else:
                # Soft throttle / host engeli: "banned" DEMEZ — tek bir 429
                # deploy'u 15 dakika kilitlememeli (düşmanca inceleme bulgusu).
                app_logger.warning(
                    f"⏳ Piyasa verisi geçici olarak kısıtlandı ({host}): "
                    f"{until_iso}'a kadar bekleniyor ({blocked_until - now:.0f} sn), "
                    f"{endpoint} isteği atılmadı"
                )
        raise MarketDataBanError(
            f"Piyasa verisi devre kesici aktif ({host}); "
            + ("ban" if hard else "kısıtlama")
            + f" bitişine {blocked_until - now:.0f} sn",
            host,
            blocked_until,
        )

    # -- mod profilleri ---------------------------------------------------
    @staticmethod
    def budget_for(mode: str) -> int:
        """Moda göre 60 sn'lik ağırlık bütçesi (harness daha gevşek)."""
        return (
            _BATCH_WEIGHT_BUDGET_PER_MINUTE
            if mode == _GUARD_MODE_BATCH
            else _MARKET_DATA_WEIGHT_BUDGET_PER_MINUTE
        )

    @staticmethod
    def spacing_for(mode: str) -> float:
        """Moda göre iki istek arası asgari boşluk (sn)."""
        return (
            _BATCH_MIN_REQUEST_SPACING_SECONDS
            if mode == _GUARD_MODE_BATCH
            else _MIN_REQUEST_SPACING_SECONDS
        )

    # -- slot rezervasyonu ----------------------------------------------
    @classmethod
    async def acquire(
        cls, base_url: str, weight: int, mode: str = _GUARD_MODE_LIVE
    ) -> None:
        """İsteği göndermeden ÖNCE çağrılır: ban kontrolü + aralık + bütçe.

        Slot host kilidi ALTINDA atomik rezerve edilir; tek bekleme asgari
        aralıktır (canlıda ≤0.15 sn). Pencere KAYANDIR (60 sn) — dokümante
        edilen davranış buydu. Bütçe dolarsa `mode`a göre:
          - "live"  → `MarketDataBudgetError` (gerekçe: o istisnanın
            docstring'i) — çağıran turu atlar, olay döngüsü bloklanmaz.
          - "batch" → en eski ağırlık girdisi pencereden düşene kadar bekler
            (harness; safety döngüsü yok, uzun koşu ortada ölmemeli) ve daha
            gevşek bir bütçe/aralık kullanır.
        Ban her iki modda da fail-closed: beklenmez, hata yükselir.
        """
        host = host_of(base_url)
        state = cls._state(host)
        budget = cls.budget_for(mode)
        spacing = cls.spacing_for(mode)
        cls.ensure_allowed(host)
        async with state.lock:
            # Kilidi beklerken başka bir görev ban yemiş olabilir.
            cls.ensure_allowed(host)

            now = time.monotonic()
            spacing_wait = state.last_request_at + spacing - now
            if spacing_wait > 0:
                await _sleep(spacing_wait)
                now = time.monotonic()

            waits = 0
            while True:
                state.prune(now)
                if state.window_weight + weight <= budget:
                    break
                remaining = state.seconds_until_free(now)
                if now - state.last_budget_log > _WEIGHT_LOG_INTERVAL_SECONDS:
                    state.last_budget_log = now
                    app_logger.warning(
                        f"⚖️ Piyasa verisi ağırlık bütçesi doldu ({host}, mod={mode}): "
                        f"{state.window_weight}/{budget} ağırlık/60 sn (kayan) — "
                        f"en eski girdi {remaining:.0f} sn sonra düşer "
                        f"(IP sınırı {_IP_WEIGHT_LIMIT_PER_MINUTE}/dk)"
                    )
                if mode != _GUARD_MODE_BATCH:
                    raise MarketDataBudgetError(
                        f"Piyasa verisi ağırlık bütçesi dolu ({host}): "
                        f"{state.window_weight}/{budget} ağırlık/60 sn",
                        host,
                    )
                # Harness: tek tüketici, safety döngüsü yok → beklemek
                # güvenlidir ve koşuyu ortada öldürmekten iyidir.
                if remaining <= 0:
                    # Pencere boş ama tek istek bütçeden büyük — olanaksız
                    # (azami ağırlık 10, asgari bütçe 600); sonsuz döngü
                    # yerine geçmesine izin ver.
                    break
                waits += 1
                if waits > _BATCH_BUDGET_MAX_WAITS:
                    # Sonsuz döngü kalkanı: normalde tek bekleme yeter (en eski
                    # girdi düşer). Buraya gelmek saat/pencere muhasebesinde bir
                    # regresyon demektir — CPU'yu yakmaktansa koşuyu açık bir
                    # hatayla durdur.
                    raise MarketDataBudgetError(
                        f"Piyasa verisi ağırlık bütçesi {_BATCH_BUDGET_MAX_WAITS} "
                        f"beklemeden sonra hâlâ dolu ({host}): "
                        f"{state.window_weight}/{budget}",
                        host,
                    )
                await _sleep(remaining)
                cls.ensure_allowed(host)
                now = time.monotonic()

            now = time.monotonic()
            state.add(now, weight)
            state.last_request_at = now

    # -- telemetri -------------------------------------------------------
    @classmethod
    def note_response(cls, base_url: str, headers: Any) -> None:
        """X-MBX-USED-WEIGHT-1M başlığını kaydet; eşiği aşarsa (host başına
        60 sn'de bir) uyar. Ölçüm — tahmin değil."""
        used = used_weight_1m(headers)
        if used is None:
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
        state.prune(time.monotonic())
        return {
            "host": host,
            "banned": time.time() < blocked_until,
            # Kesici aktifken bunun GERÇEK ban mı yoksa soft throttle mı olduğu
            # operatör için farklıdır: gerçek ban restart'ı da yasaklar.
            "hard_ban": cls.is_hard_ban(host),
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
                # Ban kontrolü + oran/ağırlık slotu. Ban/bütçe devredeyse
                # `MarketDataUnavailable`, host geneli 4xx'te
                # `MarketDataHostError`, SEMBOL kapsamlı 4xx'te (400/404)
                # `MarketDataRequestError` yükselir; hiçbiri AŞAĞIDAKİ retry
                # bloğuna DÜŞMEZ (hepsi httpx dışı tipler): ban sırasında
                # tekrar denemek yasağı uzatır, bütçe dolmuşken ve kalıcı
                # hatada tekrar denemek anlamsızdır. YALNIZ 5xx + ağ hataları
                # 3 kez denenir; tükenirse host geneli hataya dönüşür.
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

        # Denemeler tükendi. Bu noktada sorun SEMBOLDE değil HOST'tadır (aynı
        # istek 3 kez 5xx/ağ hatası aldı): kalan 11 sembol için 33 istek daha
        # atmak ne veri getirir ne de log değeri katar. Host geneli hata
        # yükselt → `_scan_tick` turu keser, `exits` trailing'i atlar,
        # `/tv-signal` yapısal ret döner (düşmanca inceleme bulgusu).
        raise MarketDataHostError(
            f"Kline çekme {_MAX_RETRIES} denemeden sonra başarısız "
            f"({symbol} {interval}, {self.host}): {last_error}",
            self.host,
        )

    def _raise_if_permanent(
        self, response: httpx.Response, symbol: str, interval: str
    ) -> None:
        """Ban DIŞI 4xx'leri TEKRARSIZ yükselt — KAPSAMINA göre iki tipte.

        `-1121 Invalid symbol` gibi bir hata kendiliğinden düzelmez: 3 deneme
        (1 sn + 2 sn uyku) sembol başına ~3 sn'yi ve 2 gereksiz isteği boşa
        harcar. Ayrı market-data host'u bu senaryoyu gerçekçi kılar (işlem
        host'unda olup veri host'unda olmayan sembol) — 12 sembollük bir
        evrende tarama turu 30 sn'yi bulabilirdi.

        KAPSAM AYRIMI (düşmanca inceleme bulgusu, HIGH): D17'de TÜM 4xx'ler
        SEMBOL kapsamlı sayılıyordu. Oysa 401/403 (kimlik/WAF) ve 451
        (coğrafi engel) HOST GENELİDİR: 12 sembolün 12'si de aynı yanıtı alır,
        tur kesilmez, kesici kurulmaz ve deploy ban kilidi kör kalır. Yalnız
        `_SYMBOL_SCOPED_STATUSES` (400/404) sembole aittir; kalan 4xx'ler
        `MarketDataHostError` + kısa bir kesici üretir. 5xx ve ağ hataları
        geçicidir; onlar ESKİSİ GİBİ 3 kez denenir (tükenirse host hatası).
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

        if status in _SYMBOL_SCOPED_STATUSES:
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

        # Host geneli engel: kısa bir kesici kur (hard=False → deploy ban
        # kilidini tetiklemez, "banned" demez) ve turu kestir.
        seconds = retry_after_seconds(response.headers) or _HOST_BLOCK_DEFAULT_SECONDS
        MarketDataGuard.trip(self.host, str(message or ""), seconds, hard=False)
        self.logger.error(
            f"⛔ Piyasa verisi host geneli engel (HTTP {status}, code={code}, "
            f"{self.host}): {message} — {seconds:.0f} sn boyunca public kline "
            f"isteği atılmayacak (tekrar denenmedi; sembol kapsamlı DEĞİL)"
        )
        raise MarketDataHostError(
            f"HTTP {status} (code={code}) {self.host}: {message}", self.host
        )

    def _raise_if_banned(self, response: httpx.Response) -> None:
        """418/429/-1003 ise kesiciyi kur ve MarketDataBanError yükselt.

        İKİ SINIF (düşmanca inceleme bulgusu, MED):
          * GERÇEK ban — 418, `-1003`, ya da mesajında "banned until" geçen
            herhangi bir yanıt. Log satırı BİLİNÇLİ olarak "HTTP <status>" +
            "IP banned until" içerir: `scripts/server_deploy.sh` deploy'dan
            önce `logs/bot.log`'da tam bu kalıbı arar ("son 15 dk'da Binance
            ban izi var — ban aktifken restart YASAK"). Public yolun banı bu
            güvenliğe GÖRÜNÜR olmalıdır; D17 öncesinde görünmüyordu.
          * SOFT throttle — TEK BAŞINA 429 ("yavaşla", ban değil). Eskiden
            90 sn'lik bir kesici kurup log'a "banned" yazıyordu: ayar BOŞKEN
            (kline'lar işlem host'undan) TEK bir 429 hem 1.5 dakika sinyal
            üretimini durduruyor hem de 15 dakika deploy'u kilitliyordu.
            Artık süre önce `Retry-After`, sonra `X-MBX-USED-WEIGHT-1M`
            (sınır aşıldıysa pencerenin sonu), yoksa 30 sn'dir ve satır
            "banned" DEMEZ.
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

        text = str(message or "")
        hard = bool(
            status == 418
            or code == -1003
            or ImprovedBinanceClient._BAN_UNTIL_RE.search(text)
        )
        if hard:
            default_seconds = _BAN_DEFAULT_SECONDS_BAN
        else:
            default_seconds = self._soft_throttle_seconds(response)
        until = MarketDataGuard.trip(
            self.host, text, default_seconds, hard=hard
        )
        until_iso = datetime.fromtimestamp(until, tz=timezone.utc).isoformat()
        shared = MarketDataGuard._shares_trading_host(self.host)
        if hard:
            self.logger.critical(
                f"🚫 Piyasa verisi IP ban (HTTP {status}, code={code}, {self.host}): "
                f"IP banned until {until_iso} — public kline istekleri durduruldu; "
                "imzalı REST bu bandan ETKİLENMEZ (kendi kesicisi, kendi kanıtı)"
                + ("; host işlem host'uyla AYNI, imzalı yol da 418 görürse kendi "
                   "kesicisini kurar" if shared else "; host AYRI")
            )
        else:
            # "banned"/"HTTP 418" YOK: deploy kilidi tek bir 429'da kapanmamalı.
            self.logger.warning(
                f"⏳ Piyasa verisi hız sınırı (HTTP {status}, code={code}, "
                f"{self.host}): {default_seconds:.0f} sn beklenecek "
                f"({until_iso}) — ban DEĞİL, tekrar denenmedi"
            )
        raise MarketDataBanError(
            f"HTTP {status} (code={code}) {self.host}: {message}", self.host, until
        )

    @staticmethod
    def _soft_throttle_seconds(response: httpx.Response) -> float:
        """Tek başına 429 için bekleme süresi: başlıklar > varsayılan.

        1) `Retry-After` — borsa açıkça söylüyorsa tahmin etmeyiz.
        2) `X-MBX-USED-WEIGHT-1M` ≥ IP sınırı → 1 dakikalık pencerenin
           dolmasını beklemek gerekir (60 sn); sınırın altındaysa 429 başka
           bir sınırdandır (sipariş oranı vb.), kısa bekleme yeter.
        3) Aksi halde `_BAN_DEFAULT_SECONDS_SOFT` (30 sn).
        """
        explicit = retry_after_seconds(response.headers)
        if explicit is not None:
            return explicit
        used = used_weight_1m(response.headers)
        if used is not None and used >= _IP_WEIGHT_LIMIT_PER_MINUTE:
            return _WEIGHT_WINDOW_SECONDS
        return _BAN_DEFAULT_SECONDS_SOFT

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
