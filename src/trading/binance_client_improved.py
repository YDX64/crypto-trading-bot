"""
Geliştirilmiş Binance Futures API client.

Tasarım ilkeleri:
- İmzalanan sorgu dizesi ile gönderilen sorgu dizesi YAPISAL olarak aynıdır
  (tek bir urlencode çıktısı hem imzalanır hem URL'e gömülür).
- Her retry denemesi parametreleri SIFIRDAN kurar; eski imza asla yeniden
  imzalanmaz.
- Borsa filtreleri (LOT_SIZE / PRICE_FILTER / MIN_NOTIONAL) önbelleğe alınır ve
  emir göndermeden önce uygulanır.
- API hataları Binance hata kodunu koruyan BinanceAPIError ile yüzeye çıkar;
  çağıranlar "yeniden denenebilir" ile "ölümcül" hatayı ayırt edebilir.
"""

import hmac
import hashlib
import time
import asyncio
import re
import uuid
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from urllib.parse import urlencode

import httpx

from src.core.config import settings
from src.core.logger import app_logger
from src.core.rate_limiter import rate_limiter


# Binance hata kodları — kurtarma mantığı bunlara göre karar verir
ERR_TIMESTAMP_AHEAD = -1021       # timestamp recvWindow dışında
ERR_INVALID_SIGNATURE = -1022     # imza geçersiz
ERR_PRECISION = -1111             # precision maksimumun üzerinde
ERR_MIN_NOTIONAL = -4164          # notional çok küçük
ERR_NO_NEED_MARGIN = -4046        # margin type zaten ayarlı
ERR_IMMEDIATE_TRIGGER = -2021     # emir anında tetiklenirdi
ERR_INSUFFICIENT_MARGIN = -2019   # marj yetersiz
ERR_REDUCE_ONLY_REJECTED = -2022  # reduceOnly emri reddedildi
ERR_DUPLICATE_CLIENT_ORDER_ID = -4116

# Binance bu kodlarda POST'un eşleştirme motoruna ulaşıp ulaşmadığını kesin
# söylemez. Koşullu emir için yeni kimlikle tekrar POST atmak yerine mevcut
# clientAlgoId ile durum sorgulanmalıdır.
UNKNOWN_EXECUTION_CODES = frozenset({-1000, -1001, -1006, -1007})

# Çağıranın normal akışta ele aldığı, gerçek arıza olmayan kodlar.
# Bunlar ERROR yerine DEBUG seviyesinde loglanır.
BENIGN_CODES = frozenset({
    ERR_NO_NEED_MARGIN,  # margin type zaten istenen değerde
    -2011,               # iptal edilecek emir zaten yok
})

# Yeniden denenmesi ANLAMSIZ olan hatalar: girdi yanlış, tekrar aynı sonucu verir
NON_RETRYABLE_CODES = frozenset({
    ERR_PRECISION,
    ERR_MIN_NOTIONAL,
    ERR_IMMEDIATE_TRIGGER,
    ERR_INSUFFICIENT_MARGIN,
    ERR_REDUCE_ONLY_REJECTED,
})


class RestWeightBackoff(Exception):
    """Kritik OLMAYAN bir istek dakikalık ağırlık bütçesi yüzünden gönderilmedi.

    `BinanceAPIError` DEĞİLDİR: ağa hiç çıkılmadı, borsanın bir cevabı yok ve
    `-1003`/418 ban semantiğiyle karıştırılmamalıdır (kesici kurulmaz).
    Çağıranlar bunu "bu tur veri yok" olarak ele alır; koruma/emir yolu bu
    istisnayı ASLA görmez (onlar `priority="critical"`tir).
    """

    def __init__(self, endpoint: str, used_weight: int, limit: float, level: str):
        self.endpoint = endpoint
        self.used_weight = used_weight
        self.limit = limit
        self.level = level
        super().__init__(
            f"REST ağırlık geri çekilmesi ({level}): {endpoint} gönderilmedi "
            f"(son ölçülen ağırlık {used_weight} ≥ {limit:g}/dk)"
        )


class BinanceAPIError(Exception):
    """Binance hata kodunu ve mesajını koruyan istisna.

    httpx.HTTPStatusError'ın metni yalnızca '400 Bad Request' içerir; gövdedeki
    {"code": -4164, "msg": "..."} kaybolur. Bu sınıf onu yüzeye çıkarır.
    """

    def __init__(self, status_code: int, code: Optional[int], msg: str, endpoint: str = ""):
        self.status_code = status_code
        self.code = code
        self.msg = msg
        self.endpoint = endpoint
        super().__init__(f"Binance [{status_code}] kod={code}: {msg} ({endpoint})")

    @property
    def is_retryable(self) -> bool:
        """Aynı isteği tekrar göndermenin anlamı var mı?"""
        if self.code in NON_RETRYABLE_CODES:
            return False
        return self.status_code >= 500 or self.status_code == 429


def is_benign_cancel_error(exc: BaseException) -> bool:
    """İptal edilmek istenen emir ZATEN yok mu? (-2011 / "does not exist")

    Bu bir arıza DEĞİL, beklenen bir yarıştır: emir aradaki milisaniyelerde
    dolmuş ya da borsa tarafından iptal edilmiştir. Çağıranlar bunu ERROR
    değil DEBUG/INFO olarak loglar (2026-08-23 log kirliliği).
    """
    if isinstance(exc, BinanceAPIError):
        if exc.code in (-2011, ERR_NO_NEED_MARGIN):
            return True
        return "does not exist" in (exc.msg or "").lower()
    return False


class ImprovedBinanceClient:
    """Geliştirilmiş Binance Futures API istemcisi"""

    # Borsa filtreleri nadiren değişir; süreç ömrü boyunca önbellek yeterli
    _FILTER_CACHE_TTL = 3600.0
    # Binance X-MBX-USED-WEIGHT-1M başlığından son ölçülen dakikalık ağırlık
    # (sınıf düzeyi: tüm istemci örnekleri aynı IP bütçesini paylaşır)
    _last_used_weight_1m: int = 0
    # Son ölçümün alındığı an (epoch sn) — telemetride `last_at`.
    _last_used_weight_at: float = 0.0
    # DAKİKA DİLİMLİ tepe: Binance'in 1M sayacı takvim dakikasında sıfırlanır,
    # dolayısıyla "tepe" ancak AYNI dakika içinde anlamlıdır. Süreç ömrü boyu
    # tutulan bir tepe farklı dakikaları tek sayıya katlar ve RUNBOOK'un
    # "max_1m > 3000 ise araştır" kuralını okunamaz kılardı.
    _peak_used_weight_1m: int = 0
    _peak_used_weight_at: float = 0.0
    _peak_window_start: float = 0.0
    # Ağırlık uyarı satırı bu eşikten itibaren basılır (gerçek sınır 2400).
    _WEIGHT_WARN_THRESHOLD = 1800
    # Uyarı/CRITICAL satırı dakikada en fazla BİR kez (2026-08-23: 276 satır/gün).
    _WEIGHT_LOG_INTERVAL = 60.0
    _weight_warn_at: float = 0.0
    _weight_hard_log_at: float = 0.0
    # Geri çekilme pencereleri (epoch sn) — Binance 1M sayacı takvim
    # dakikasında sıfırlanır, bu yüzden pencere dakikanın SONUNA kadardır.
    _weight_soft_until: float = 0.0
    _weight_hard_until: float = 0.0
    _weight_soft_backoffs: int = 0
    _weight_hard_backoffs: int = 0
    # /commissionRate IP weight=20; işlem başına çağrılmamalıdır.
    _COMMISSION_CACHE_TTL = 3600.0
    _CLIENT_ALGO_ID_RE = re.compile(r"^[.A-Za-z0-9_:/-]{1,36}$")
    _ALGO_RECONCILE_DELAYS = (0.0, 0.1, 0.3)

    # Ban devre kesicisi — SINIF düzeyinde: süreçteki tüm istemci örnekleri
    # (engine + orchestrator + telegram) aynı yasağa birlikte uyar.
    _rest_blocked_until: float = 0.0
    _breaker_last_log: float = 0.0
    _BAN_UNTIL_RE = re.compile(r"banned until (\d{10,16})")

    # --- REST ağırlık diyeti: süreç-geneli okuma önbellekleri -----------
    # 2026-08-15: positionRisk/account polling'i 2400 weight/dk sınırını
    # düzenli aşıp 418 ban döngüsü yaratıyordu; ban körlüğünde 5 pozisyon
    # günlerce "hayalet" kaldı ve restart'ta toplu UNKNOWN kapanışıyla
    # (−89 USDT) deftere indi. Sembolsüz positionRisk TÜM pozisyonları tek
    # weight-5 çağrıyla verir; account yanıtı da tüm bakiyeleri taşır.
    # Okumalar bu paylaşılan anlık görüntülerden beslenir. SINIF düzeyinde
    # paylaşım AYNI SÜREÇ içinde geçerlidir: src/main.py'nin engine +
    # orchestrator + in-process FastAPI app'i aynı görüntüyü kullanır. DİKKAT:
    # start_system.sh'nin ayrı süreç olarak başlattığı (deprecated)
    # src/api_server.py bu paylaşımın DIŞINDADIR — sınıf-düzeyi önbellek
    # süreç sınırını geçmez, o süreç kendi bağımsız önbelleğini yaşlandırır.
    _POS_SNAPSHOT_TTL = 5.0    # sn — safety turu 2 sn (config varsayılanı):
    #                            snapshot ~2-3 tur yaşar, tur başına ≤1 çağrı
    _POS_SNAPSHOT_FRESH_S = 1.0  # sn — bu yaştan taze TAM snapshot, sıfır/eksik
    #                              kayıt için de "taze doğrulama" sayılır
    #                              (yazma olmadıysa — bkz. _write_generation)
    _ACCOUNT_CACHE_TTL = 15.0  # sn — dashboard 5 sn'de bir bakiye soruyor
    _PRICE_CACHE_TTL = 2.5     # sn — MAE/MFE ve tahmini çıkış için yeterli
    _PRICE_CACHE_MAX = 256     # girdi tavanı — aşılınca süresi geçenler atılır
    # Her POST/DELETE'te artar. İki görevi var: (1) yazma sırasında havada
    # olan bir GET'in emir-ÖNCESİ yanıtı önbelleğe "taze" damgalamasını önler
    # (fetch başlarken alınan jenerasyon değiştiyse damga basılmaz);
    # (2) kilit içi 1 sn kısayolu yalnız son yazmadan SONRA alınmış snapshot'a
    # güvenir. Zaman damgası sıfırlamak tek başına bu yarışı kapatamıyordu.
    _write_generation: int = 0
    _pos_snapshot: Optional[Dict[str, Dict[str, Any]]] = None
    _pos_snapshot_ts: float = 0.0
    _pos_snapshot_gen: int = -1
    _pos_snapshot_lock: Optional[asyncio.Lock] = None
    _account_cache: Optional[Dict[str, Any]] = None
    _account_cache_ts: float = 0.0
    _account_cache_lock: Optional[asyncio.Lock] = None
    # Piyasa fiyatı bilinçli olarak invalidasyon/kilit dışında: kendi
    # yazmalarımız piyasa fiyatını değiştirmez, kaçırılan yarışın bedeli en
    # fazla bir yinelenen GET'tir. Bu sadeliği kopyalamadan önce dikkat:
    # HESAP verisi önbellekleyen her yeni endpoint kilitli+invalidasyonlu
    # desene (_pos_snapshot/_account_cache) uymalıdır.
    _price_cache: Dict[str, Tuple[float, float]] = {}
    # Ağırlık uyarısına iliştirilen teşhis: son uyarıdan bu yana endpoint
    # başına GERÇEK ağa çıkan istek sayısı. "Bütçeyi kim yiyor" sorusu
    # tahminle değil bu sayaçla cevaplanır (2026-08-15).
    _endpoint_counts: Dict[str, int] = {}

    @classmethod
    def _read_lock(cls, name: str) -> asyncio.Lock:
        """Sınıf-düzeyi kilidi tembel oluştur (import anında loop olmayabilir)."""
        lock = getattr(cls, name)
        if lock is None:
            lock = asyncio.Lock()
            setattr(cls, name, lock)
        return lock

    @classmethod
    def _invalidate_read_caches(cls, symbol: Optional[str] = None) -> None:
        """Her yazma isteğinde (POST/DELETE) çağrılır: kendi emirlerimizin
        etkisi asla bayat anlık görüntüden okunmasın.

        Sembollü yazmada pozisyon snapshot'ından YALNIZ o sembol düşer:
        pozisyon verisi sembol bazlıdır, A'ya atılan stop replace'i aynı
        exits.step turundaki B/C okumalarına weight-5 taze fetch ödetmemeli
        (aksi hâlde volatil anda diyet, diyetsiz baseline'a geriliyordu).
        Account yanıtı ise global (emir marjı her yazmayla değişebilir) —
        daima komple düşer. Jenerasyon her durumda artar; sıfır-servisi ve
        taze-damga bekçileri buna bakar."""
        cls._write_generation += 1
        if symbol and cls._pos_snapshot is not None:
            cls._pos_snapshot.pop(str(symbol).strip().upper(), None)
        else:
            cls._pos_snapshot_ts = 0.0
        cls._account_cache_ts = 0.0

    # ------------------------------------------------------------------
    # REST ağırlık geri çekilmesi (D22)
    # ------------------------------------------------------------------

    @classmethod
    def _weight_limits(cls) -> Tuple[float, float]:
        """(soft, hard) eşikleri — 0/negatif = o kademe KAPALI."""
        def _read(name: str, default: float) -> float:
            try:
                value = float(getattr(settings, name, default) or 0.0)
            except (TypeError, ValueError):
                return 0.0
            return value if value > 0 else 0.0

        return _read("binance_weight_soft_limit", 2000.0), _read(
            "binance_weight_hard_limit", 2300.0
        )

    @classmethod
    def _note_used_weight(cls, used_weight: int) -> None:
        """`X-MBX-USED-WEIGHT-1M` ölçümünü işle ve geri çekilme penceresini kur.

        Pencere, ölçümün alındığı TAKVİM DAKİKASININ sonuna kadar sürer:
        Binance'in 1M sayacı orada sıfırlanır, dolayısıyla daha uzun bir
        bekleme bütçeyi boş yere harcatır, daha kısası ise sayacı yeniden
        doldurur.
        """
        now = time.time()
        cls._last_used_weight_1m = used_weight
        cls._last_used_weight_at = now

        # Dakika dilimi değiştiyse tepe SIFIRLANIR (Binance sayacı da orada
        # sıfırlanır); aynı dilim içindeyse yalnız büyürse güncellenir.
        window_start = float(int(now // 60) * 60.0)
        if window_start != cls._peak_window_start:
            cls._peak_window_start = window_start
            cls._peak_used_weight_1m = 0
            cls._peak_used_weight_at = 0.0
        if used_weight > cls._peak_used_weight_1m:
            cls._peak_used_weight_1m = used_weight
            cls._peak_used_weight_at = now

        soft, hard = cls._weight_limits()
        # Pencere DAİMA içinde bulunulan takvim dakikasının sonudur ve ASLA
        # `max()` ile kilitlenmez: ileri bir saat sıçraması (NTP düzeltmesi,
        # VM suspend) `max()` yüzünden saatlerce sürecek bir geri çekilme
        # penceresi çivileyebilir ve bot bu süre boyunca hiç taramaz.
        # `min(..., now + 60)` aynı sıçramaya karşı ikinci kemerdir.
        window_end = min((int(now // 60) + 1) * 60.0, now + 60.0)
        if hard and used_weight >= hard:
            cls._weight_hard_until = window_end
            cls._weight_soft_until = window_end
        elif soft and used_weight >= soft:
            cls._weight_soft_until = window_end

    @classmethod
    def weight_backoff_level(cls) -> str:
        """"off" | "soft" | "hard" — kritik olmayan istekler için geçerli kademe.

        Pencere en fazla BİR dakika sürebilir. Daha uzağa işaret eden bir
        damga yalnız saatin geriye alınmasıyla oluşabilir (ileri sıçrama
        `_note_used_weight`te kırpılır); bu durumda pencere GEÇERSİZ sayılıp
        temizlenir — teşhis amaçlı bir kapının botu süresiz durdurması,
        koruyacağı 418'den daha pahalıdır.
        """
        now = time.time()
        horizon = now + 60.0
        if cls._weight_hard_until > horizon or cls._weight_soft_until > horizon:
            cls._weight_soft_until = 0.0
            cls._weight_hard_until = 0.0
            return "off"
        if now < cls._weight_hard_until:
            return "hard"
        if now < cls._weight_soft_until:
            return "soft"
        return "off"

    @classmethod
    def weight_backoff_active(cls) -> bool:
        """Kritik olmayan istekler şu an ertelenmeli mi?"""
        return cls.weight_backoff_level() != "off"

    def _weight_gate(self, endpoint: str, priority: str) -> None:
        """Kritik OLMAYAN isteği ağırlık bütçesi doluyken ağa bırakma.

        `priority="critical"` (varsayılan) hiçbir zaman engellenmez: emir,
        SL/TP, positionRisk koruma turu ve kapanış doğrulaması bir dakikalık
        bütçe uğruna ertelenmez — korumasız/ölçülmemiş pozisyon, 418'den
        pahalıdır.
        """
        if priority == "critical":
            return
        cls = type(self)
        level = cls.weight_backoff_level()
        if level == "off":
            return
        soft, hard = cls._weight_limits()
        now = time.time()
        if level == "hard":
            cls._weight_hard_backoffs += 1
            if now - cls._weight_hard_log_at >= cls._WEIGHT_LOG_INTERVAL:
                cls._weight_hard_log_at = now
                self.logger.critical(
                    f"🛑 REST ağırlık SERT sınırı: son ölçüm "
                    f"{cls._last_used_weight_1m}/dk (≥{hard:g}); kritik OLMAYAN "
                    f"tüm istekler dakika sonuna kadar durduruldu "
                    f"(toplam soft={cls._weight_soft_backoffs}, "
                    f"hard={cls._weight_hard_backoffs})"
                )
            raise RestWeightBackoff(endpoint, cls._last_used_weight_1m, hard, "hard")

        cls._weight_soft_backoffs += 1
        raise RestWeightBackoff(endpoint, cls._last_used_weight_1m, soft, "soft")

    @classmethod
    def rest_weight_snapshot(cls) -> Dict[str, Any]:
        """`/scalper/status.rest_weight` — teşhis (secret içermez)."""
        soft, hard = cls._weight_limits()
        level = cls.weight_backoff_level()   # geçersiz pencereyi de temizler
        now = time.time()
        until = max(cls._weight_soft_until, cls._weight_hard_until)

        def _iso(stamp: float) -> Optional[str]:
            if not stamp:
                return None
            return datetime.fromtimestamp(stamp, tz=timezone.utc).isoformat(
                timespec="seconds"
            )

        return {
            # Son ölçülen `X-MBX-USED-WEIGHT-1M` ve ölçüm anı.
            "last": int(cls._last_used_weight_1m),
            "last_at": _iso(cls._last_used_weight_at),
            # İÇİNDE BULUNULAN takvim dakikasının tepesi (dakika başında
            # sıfırlanır — Binance sayacı da orada sıfırlanır) ve tepe anı.
            "max_1m": int(cls._peak_used_weight_1m),
            "peak_at": _iso(cls._peak_used_weight_at),
            "soft_backoffs": int(cls._weight_soft_backoffs),
            "hard_backoffs": int(cls._weight_hard_backoffs),
            # 0 = o kademe KAPALI (varsayılan). Telemetri eşiklerden bağımsız.
            "soft_limit": soft,
            "hard_limit": hard,
            "enabled": bool(soft or hard),
            "backoff": level,
            "backoff_seconds_left": round(max(0.0, until - now), 1),
        }

    @classmethod
    def reset_weight_state(cls) -> None:
        """Yalnız testler için: ağırlık geri çekilme durumunu sıfırla."""
        cls._last_used_weight_1m = 0
        cls._last_used_weight_at = 0.0
        cls._peak_used_weight_1m = 0
        cls._peak_used_weight_at = 0.0
        cls._peak_window_start = 0.0
        cls._weight_soft_until = 0.0
        cls._weight_hard_until = 0.0
        cls._weight_soft_backoffs = 0
        cls._weight_hard_backoffs = 0
        cls._weight_warn_at = 0.0
        cls._weight_hard_log_at = 0.0

    def _ensure_rest_allowed(self, endpoint: str) -> None:
        """Küresel ban devre kesicisi: -1003/418 sonrası ban bitene kadar
        HİÇBİR istek ağa çıkmaz. Ban sırasında atılan her istek yasağı uzatır
        (2026-08-12: 24 adet -1003, bot gün içinde saatlerce kilitli kaldı).
        Sınıf düzeyinde paylaşılır — süreçteki TÜM istemciler birlikte uyar.
        """
        blocked_until = type(self)._rest_blocked_until
        now = time.time()
        if now >= blocked_until:
            return
        if now - type(self)._breaker_last_log > 30.0:
            type(self)._breaker_last_log = now
            self.logger.warning(
                f"🚫 REST devre kesici aktif: Binance IP ban bitişine "
                f"{blocked_until - now:.0f} sn var, {endpoint} isteği atılmadı"
            )
        raise BinanceAPIError(
            418, -1003,
            f"REST devre kesici aktif (ban bitişine {blocked_until - now:.0f} sn)",
            endpoint,
        )

    @classmethod
    def _trip_breaker(cls, message: str, default_seconds: float) -> float:
        """-1003/418 mesajından ban bitişini çöz ve kesiciyi kur.

        Mesajda "banned until <epoch_ms>" varsa o zamana +5 sn tampon; yoksa
        default_seconds. Mevcut daha uzun bir kesici asla KISALTILMAZ.
        Kurulan bitiş zamanını (epoch saniye) döndürür.
        """
        until = time.time() + default_seconds
        match = cls._BAN_UNTIL_RE.search(message or "")
        if match:
            raw = float(match.group(1))
            # epoch saniye mi milisaniye mi? 10^12 üstü kesin ms'dir.
            parsed = raw / 1000.0 if raw > 1e12 else raw
            if parsed > time.time():
                until = parsed + 5.0
        cls._rest_blocked_until = max(cls._rest_blocked_until, until)
        return cls._rest_blocked_until

    def __init__(self):
        self.api_key = settings.binance_api_key
        self.api_secret = settings.binance_api_secret
        self.base_url = settings.binance_base_url
        self.logger = app_logger

        # Boş değilse soketler bu yerel IP'ye bind edilir: NordVPN policy
        # routing'inde `from <ana-IP> lookup 100` kuralı bind'li soketi tünel
        # DIŞINA çıkarır → paylaşılan tünel IP'sinin weight bütçesi yerine
        # yalnız bize ait temiz bütçe (418 kök nedeni, 2026-08-15).
        bind_ip = str(getattr(settings, "binance_bind_ip", "") or "").strip()
        limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
        client_kwargs: Dict[str, Any] = {
            "timeout": httpx.Timeout(60.0, connect=10.0),
            "follow_redirects": True,
        }
        if bind_ip:
            # transport verildiğinde limits client'ta değil transport'ta geçerli
            client_kwargs["transport"] = httpx.AsyncHTTPTransport(
                local_address=bind_ip, limits=limits
            )
            self.logger.info(f"🔌 Binance REST soketleri {bind_ip} IP'sine bind edildi")
        else:
            client_kwargs["limits"] = limits
        self.client = httpx.AsyncClient(**client_kwargs)

        self.max_retries = 3
        self.retry_delay = 2.0
        self.recv_window = 10000

        # Sunucu saati ile yerel saat farkı (ms). İlk imzalı istekte hesaplanır.
        self._time_offset_ms: Optional[int] = None

        # symbol -> (filtreler, önbelleğe alma zamanı)
        self._symbol_filters: Dict[str, Tuple[Dict[str, Any], float]] = {}
        self._filter_lock = asyncio.Lock()

        # symbol -> (doğrulanmış oranlar, önbelleğe alma zamanı)
        self._commission_rates: Dict[str, Tuple[Dict[str, Any], float]] = {}
        self._commission_lock = asyncio.Lock()

        # USDⓈ-M user-data stream anahtarı. Bu endpoint'ler API-key header
        # ister fakat HMAC imzası/timestamp istemez.
        self._listen_key: Optional[str] = None

    # ------------------------------------------------------------------
    # İmzalama
    # ------------------------------------------------------------------

    def _sign(self, query_string: str) -> str:
        """Verilen sorgu dizesini imzala."""
        return hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _generate_signature(self, params: Dict[str, Any]) -> str:
        """Geriye dönük uyumluluk için korunan yardımcı.

        DİKKAT: params içinde 'signature' anahtarı BULUNMAMALIDIR. İç kod artık
        bunun yerine _sign(query_string) kullanır.
        """
        clean = {k: v for k, v in params.items() if k != "signature"}
        return self._sign(urlencode(clean))

    def _get_headers(self) -> Dict[str, str]:
        return {
            "X-MBX-APIKEY": self.api_key,
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "TradingBot/2.1",
        }

    async def _sync_time_offset(self) -> int:
        """Sunucu-yerel saat farkını hesapla ve önbelleğe al.

        Sabit '-500ms' hack'i yerine gerçek ölçüm kullanılır; böylece saat
        kayması olan makinelerde de imzalı istekler -1021 almaz.
        """
        if self._time_offset_ms is not None:
            return self._time_offset_ms
        try:
            resp = await self.client.get(f"{self.base_url}/fapi/v1/time", timeout=10.0)
            resp.raise_for_status()
            server_time = int(resp.json()["serverTime"])
            self._time_offset_ms = server_time - int(time.time() * 1000)
            if abs(self._time_offset_ms) > 1000:
                self.logger.warning(
                    f"⏱️ Saat farkı düzeltiliyor: {self._time_offset_ms}ms"
                )
            return self._time_offset_ms
        except Exception as e:
            self.logger.warning(f"Saat senkronu yapılamadı, offset=0 kullanılıyor: {e}")
            self._time_offset_ms = 0
            return 0

    # ------------------------------------------------------------------
    # İstek katmanı
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_error(response: httpx.Response) -> Tuple[Optional[int], str]:
        """Binance hata gövdesinden kod ve mesajı çıkar."""
        try:
            body = response.json()
            return body.get("code"), body.get("msg", response.text)
        except Exception:
            return None, response.text

    async def _request_with_retry(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        signed: bool = False,
        invalidate_symbol: Optional[str] = None,
        priority: str = "critical",
    ) -> Any:
        """API isteği yap — her deneme parametreleri sıfırdan kurar.

        KRİTİK: base_params asla mutasyona uğramaz. Retry sırasında önceki
        denemenin 'signature' alanı yeniden imzalanırsa Binance -1022 döndürür.

        invalidate_symbol: API'ye GÖNDERİLMEZ — yalnız önbellek invalidasyonu
        için. Sembolsüz yazma endpoint'leri (örn. algoId'li DELETE) etkilenen
        sembolü bununla bildirir ki invalidasyon global yerine hedefli olsun.

        priority (D22): "critical" (varsayılan) = emir/koruma/kapanış
        doğrulaması — ağırlık bütçesi dolsa bile gönderilir. "background" =
        pano beslemesi, periyodik hesap özeti, evren taraması, teşhis —
        `X-MBX-USED-WEIGHT-1M` yumuşak sınırı aştıysa dakika penceresi
        dolana kadar `RestWeightBackoff` ile geri çevrilir.
        """
        base_params = dict(params or {})
        cache_symbol = base_params.get("symbol") or invalidate_symbol
        url = f"{self.base_url}{endpoint}"
        last_error: Optional[Exception] = None

        self._ensure_rest_allowed(endpoint)
        self._weight_gate(endpoint, priority)

        # Yazma denemesi başlarken VE başarıyla bitince okuma önbellekleri
        # düşer: belirsiz sonuçlu (timeout) bir POST bile bayat okuma bırakmaz.
        if method != "GET":
            type(self)._invalidate_read_caches(cache_symbol)

        for attempt in range(self.max_retries):
            try:
                await rate_limiter.wait_for_binance()

                type(self)._endpoint_counts[endpoint] = (
                    type(self)._endpoint_counts.get(endpoint, 0) + 1
                )

                # Her denemede TAZE kopya — mutasyon yok
                attempt_params = dict(base_params)
                headers = {}

                if signed:
                    offset = await self._sync_time_offset()
                    attempt_params["timestamp"] = int(time.time() * 1000) + offset
                    attempt_params["recvWindow"] = self.recv_window
                    query = urlencode(attempt_params)
                    # İmzalanan dize ile gönderilen dize aynı: tek urlencode çıktısı
                    query = f"{query}&signature={self._sign(query)}"
                    headers = self._get_headers()
                else:
                    query = urlencode(attempt_params)

                request_url = f"{url}?{query}" if query else url

                if method == "GET":
                    response = await self.client.get(request_url, headers=headers)
                elif method == "POST":
                    response = await self.client.post(request_url, headers=headers)
                elif method == "DELETE":
                    response = await self.client.delete(request_url, headers=headers)
                else:
                    raise ValueError(f"Desteklenmeyen HTTP metodu: {method}")

                # Binance'in bildirdiği gerçek dakikalık ağırlık — tahmin değil
                # ölçüm. Testnet'in weight bütçesi mainnet'ten düşük ve resmi
                # olarak belgelenmemiş; 418 teşhisi bu log olmadan kör kalır.
                uw_raw = response.headers.get("X-MBX-USED-WEIGHT-1M")
                if uw_raw is not None:
                    try:
                        uw = int(uw_raw)
                    except (TypeError, ValueError):
                        uw = None
                    if uw is not None:
                        cls_ = type(self)
                        # D22: ölçüm yalnız kaydedilmez, geri çekilme
                        # penceresini de kurar (kritik olmayan istekler).
                        cls_._note_used_weight(uw)
                        # Testnet'te başlık edge-bazlı ve tutarsız (aynı dakika
                        # içinde 1912→375 gözlendi); mutlak değer değil trend
                        # sinyali. Eşik gerçek 2400 sınırına yakın tutulur ki
                        # log gürültüsü olmasın ama gerçek riske yaklaşım görünsün.
                        # Satır DAKİKADA EN FAZLA BİR: 2026-08-23'te aynı uyarı
                        # 276 kez basıldı ve gerçek arızayı gömdü.
                        now_log = time.time()
                        if (
                            uw >= cls_._WEIGHT_WARN_THRESHOLD
                            and now_log - cls_._weight_warn_at
                            >= cls_._WEIGHT_LOG_INTERVAL
                        ):
                            cls_._weight_warn_at = now_log
                            counts = cls_._endpoint_counts
                            top = sorted(
                                counts.items(), key=lambda kv: kv[1], reverse=True
                            )[:6]
                            detail = ", ".join(f"{ep}×{n}" for ep, n in top)
                            cls_._endpoint_counts = {}
                            self.logger.warning(
                                f"⚖️ Binance 1dk kullanılan ağırlık: {uw} ({endpoint}) "
                                f"| tepe {cls_._peak_used_weight_1m} "
                                f"| geri çekilme={cls_.weight_backoff_level()} "
                                f"| son uyarıdan beri istekler: {detail}"
                            )

                if response.status_code >= 400:
                    code, msg = self._parse_error(response)
                    err = BinanceAPIError(response.status_code, code, msg, endpoint)

                    if code == ERR_TIMESTAMP_AHEAD:
                        # Saat kaymış — offset'i sıfırla ve yeniden ölç
                        self.logger.warning("⏱️ Timestamp reddedildi, saat yeniden senkronize ediliyor")
                        self._time_offset_ms = None
                        last_error = err
                        if attempt < self.max_retries - 1:
                            continue

                    if response.status_code == 429:
                        # Soft limit: devre kesiciyi kısa süre kapat ki DİĞER
                        # coroutine'ler de istek yağdırıp 418'e tırmandırmasın.
                        type(self)._trip_breaker(msg, default_seconds=90.0)
                        self.logger.warning("Rate limit (429), 60s bekleniyor + kesici aktif")
                        last_error = err
                        if attempt < self.max_retries - 1:
                            await asyncio.sleep(60)
                            continue

                    if response.status_code == 418 or code == -1003:
                        until = type(self)._trip_breaker(msg, default_seconds=180.0)
                        self.logger.critical(
                            f"🚫 IP ban ({code}): TÜM REST istekleri "
                            f"{datetime.fromtimestamp(until, tz=timezone.utc).isoformat()}'e "
                            f"kadar durduruldu — ban sırasında istek atmak yasağı uzatır"
                        )
                        raise err

                    if err.is_retryable and attempt < self.max_retries - 1:
                        wait = self.retry_delay * (attempt + 1)
                        self.logger.warning(f"Sunucu hatası ({code}), {wait}s sonra tekrar...")
                        last_error = err
                        await asyncio.sleep(wait)
                        continue

                    # Yeniden denenmeyecek hata — hemen yüzeye çıkar.
                    # Çağıran tarafın normal akışta yakaladığı kodlar (örn.
                    # "margin type zaten ayarlı") ERROR olarak loglanmaz;
                    # aksi halde loglar sahte hatalarla dolar.
                    if code in BENIGN_CODES:
                        self.logger.debug(str(err))
                    else:
                        self.logger.error(str(err))
                    raise err

                if method != "GET":
                    type(self)._invalidate_read_caches(cache_symbol)
                return response.json()

            except BinanceAPIError:
                raise

            except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    wait = self.retry_delay * (attempt + 1)
                    self.logger.warning(f"Ağ hatası ({type(e).__name__}), {wait}s sonra tekrar...")
                    await asyncio.sleep(wait)
                    continue
                self.logger.error(f"Ağ hatası (denemeler tükendi): {e}")
                raise

        raise last_error or Exception(
            f"API isteği {self.max_retries} denemeden sonra başarısız: {endpoint}"
        )

    async def _request_api_key_only(
        self, method: str, endpoint: str
    ) -> Dict[str, Any]:
        """USER_STREAM endpoint'i: API-key header var, imza/timestamp yok.

        Genel imzalı POST retry semantiğine dokunmaz. listenKey create aynı
        hesapta aktif anahtarı döndürüp süresini uzattığından tekrarı
        idempotenttir; PUT/DELETE de aynı şekilde güvenle tekrarlanabilir.
        """
        url = f"{self.base_url}{endpoint}"
        last_error: Optional[Exception] = None
        self._ensure_rest_allowed(endpoint)
        for attempt in range(self.max_retries):
            try:
                await rate_limiter.wait_for_binance()
                response = await self.client.request(
                    method, url, headers=self._get_headers()
                )
                if response.status_code >= 400:
                    code, msg = self._parse_error(response)
                    error = BinanceAPIError(
                        response.status_code, code, msg, endpoint
                    )
                    last_error = error
                    if response.status_code == 418 or code == -1003:
                        type(self)._trip_breaker(msg, default_seconds=180.0)
                    if error.is_retryable and attempt < self.max_retries - 1:
                        await asyncio.sleep(self.retry_delay * (attempt + 1))
                        continue
                    raise error
                if not response.content:
                    return {}
                body = response.json()
                return body if isinstance(body, dict) else {}
            except BinanceAPIError:
                raise
            except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(self.retry_delay * (attempt + 1))
                    continue
                raise
        raise last_error or RuntimeError(f"USER_STREAM isteği başarısız: {endpoint}")

    # ------------------------------------------------------------------
    # Borsa filtreleri
    # ------------------------------------------------------------------

    async def get_symbol_filters(self, symbol: str) -> Dict[str, Any]:
        """Sembolün borsa filtrelerini getir (önbellekli).

        Döner: {"stepSize", "minQty", "tickSize", "minNotional",
                "quantityPrecision", "pricePrecision"}
        """
        now = time.monotonic()
        cached = self._symbol_filters.get(symbol)
        if cached and (now - cached[1]) < self._FILTER_CACHE_TTL:
            return cached[0]

        async with self._filter_lock:
            # Kilit beklerken başka bir görev doldurmuş olabilir
            cached = self._symbol_filters.get(symbol)
            if cached and (time.monotonic() - cached[1]) < self._FILTER_CACHE_TTL:
                return cached[0]

            info = await self._request_with_retry("GET", "/fapi/v1/exchangeInfo")
            found = None
            for s in info.get("symbols", []):
                if s["symbol"] == symbol:
                    found = s
                    break

            if not found:
                raise BinanceAPIError(400, None, f"Sembol borsada bulunamadı: {symbol}")

            by_type = {f["filterType"]: f for f in found.get("filters", [])}
            lot = by_type.get("LOT_SIZE", {})
            price_f = by_type.get("PRICE_FILTER", {})
            notional = by_type.get("MIN_NOTIONAL", {})

            filters = {
                "stepSize": Decimal(str(lot.get("stepSize", "0.001"))),
                "minQty": Decimal(str(lot.get("minQty", "0"))),
                "maxQty": Decimal(str(lot.get("maxQty", "9999999"))),
                "tickSize": Decimal(str(price_f.get("tickSize", "0.01"))),
                "minNotional": Decimal(str(notional.get("notional", "0"))),
                "quantityPrecision": int(found.get("quantityPrecision", 3)),
                "pricePrecision": int(found.get("pricePrecision", 2)),
            }
            self._symbol_filters[symbol] = (filters, time.monotonic())
            self.logger.debug(
                f"📐 {symbol} filtreleri: step={filters['stepSize']} "
                f"tick={filters['tickSize']} minNotional={filters['minNotional']}"
            )
            return filters

    @staticmethod
    def _quantize_down(value: float, step: Decimal) -> Decimal:
        """Değeri adım büyüklüğünün katına AŞAĞI yuvarla."""
        if step <= 0:
            return Decimal(str(value))
        d = Decimal(str(value))
        return (d / step).to_integral_value(rounding=ROUND_DOWN) * step

    async def quantize_quantity(self, symbol: str, quantity: float) -> float:
        """Miktarı LOT_SIZE stepSize'a göre yuvarla."""
        f = await self.get_symbol_filters(symbol)
        q = self._quantize_down(quantity, f["stepSize"])
        return float(q)

    async def quantize_price(self, symbol: str, price: float) -> float:
        """Fiyatı PRICE_FILTER tickSize'a göre yuvarla."""
        f = await self.get_symbol_filters(symbol)
        p = self._quantize_down(price, f["tickSize"])
        return float(p)

    async def quantize_maker_price(self, symbol: str, price: float, side: str) -> float:
        """Post-only LIMIT fiyatını defterin dışına doğru yuvarla.

        BUY için aşağı, SELL için yukarı yuvarlamak fiyatın tick-size
        düzeltmesi sırasında spread'in karşı tarafına taşınmasını engeller.
        GTX yine son ve otoriter post-only kapısıdır.
        """
        normalized_side = side.upper()
        if normalized_side not in {"BUY", "SELL"}:
            raise ValueError(f"Geçersiz emir tarafı: {side}")

        f = await self.get_symbol_filters(symbol)
        step = f["tickSize"]
        value = Decimal(str(price))
        if step > 0:
            rounding = ROUND_DOWN if normalized_side == "BUY" else ROUND_UP
            value = (value / step).to_integral_value(rounding=rounding) * step
        return float(value)

    async def quantize_protective_price(
        self, symbol: str, price: float, side: str
    ) -> float:
        """Koruyucu STOP/TP tetik fiyatını pozisyon yönüne göre yuvarla.

        Koruyucu emrin ``side`` değeri kapatılan pozisyonu tanımlar:

        - SELL (LONG kapatır): yukarı yuvarla; hesaplanan fiyat tabanının
          tick dönüşümünde aşağı aşılmasını engeller.
        - BUY (SHORT kapatır): aşağı yuvarla; hesaplanan fiyat tavanının
          tick dönüşümünde yukarı aşılmasını engeller.

        Bu yardımcı yalnız koşullu koruma emirleri içindir; GTX maker fiyat
        yuvarlamasının ters-taraf mantığını değiştirmez.
        """
        normalized_side = side.upper()
        if normalized_side not in {"BUY", "SELL"}:
            raise ValueError(f"Geçersiz emir tarafı: {side}")

        f = await self.get_symbol_filters(symbol)
        step = f["tickSize"]
        value = Decimal(str(price))
        if step > 0:
            rounding = ROUND_UP if normalized_side == "SELL" else ROUND_DOWN
            value = (value / step).to_integral_value(rounding=rounding) * step
        return float(value)

    async def validate_order(
        self, symbol: str, quantity: float, reference_price: float
    ) -> None:
        """Emir göndermeden ÖNCE borsa filtrelerine uygunluğu doğrula.

        Uymayan emirler Binance tarafından reddedilir; hatayı burada yakalamak
        pozisyonun yarım açılmasını engeller.
        """
        f = await self.get_symbol_filters(symbol)
        qty = Decimal(str(quantity))

        if qty <= 0:
            raise BinanceAPIError(
                400, ERR_PRECISION,
                f"Miktar sıfır veya negatif ({quantity}). Yuvarlama sonrası "
                f"stepSize={f['stepSize']} altına düşmüş olabilir.",
            )
        if qty < f["minQty"]:
            raise BinanceAPIError(
                400, ERR_PRECISION,
                f"Miktar minQty altında: {qty} < {f['minQty']}",
            )
        if qty > f["maxQty"]:
            raise BinanceAPIError(
                400, ERR_PRECISION,
                f"Miktar maxQty üstünde: {qty} > {f['maxQty']}",
            )

        notional = qty * Decimal(str(reference_price))
        if notional < f["minNotional"]:
            raise BinanceAPIError(
                400, ERR_MIN_NOTIONAL,
                f"Emir değeri MIN_NOTIONAL altında: {notional:.2f} < "
                f"{f['minNotional']} USDT. Pozisyon büyüklüğünü artırın.",
            )

    # ------------------------------------------------------------------
    # Bağlantı / hesap
    # ------------------------------------------------------------------

    async def test_connection(self) -> bool:
        try:
            self.logger.info("🔍 Binance bağlantısı test ediliyor...")
            await self._request_with_retry("GET", "/fapi/v1/ping")
            self.logger.info("✅ Ping başarılı")

            response = await self._request_with_retry("GET", "/fapi/v1/time")
            server_time = response.get("serverTime", 0)
            time_diff = abs(server_time - int(time.time() * 1000))
            if time_diff > 5000:
                self.logger.warning(f"⚠️ Server zaman farkı yüksek: {time_diff}ms (offset uygulanacak)")
            else:
                self.logger.info(f"✅ Server zaman senkronu iyi: {time_diff}ms fark")

            await self._request_with_retry("GET", "/fapi/v2/account", signed=True)
            self.logger.info("✅ API anahtarı geçerli")
            return True
        except Exception as e:
            self.logger.error(f"❌ Bağlantı testi başarısız: {e}")
            return False

    async def _get_account(
        self, *, force_fresh: bool = False, priority: str = "critical"
    ) -> Dict[str, Any]:
        """/fapi/v2/account yanıtı — süreç-geneli önbellek (TTL 15 sn).

        Bakiye/cüzdan okumaları ve dashboard polling'i tek weight-5 çağrıyı
        paylaşır. Yazma istekleri önbelleği düşürür; kurtarma akışları
        force_fresh=True ile her zaman taze okur.

        D22: `priority="background"` (pano/teşhis) çağrıları ağırlık geri
        çekilmesi sırasında SÜRESİ GEÇMİŞ önbellekten servis edilir — bayat
        bir bakiye göstermek, bütçeyi 418'e taşımaktan iyidir. Önbellek hiç
        yoksa `RestWeightBackoff` yüzeye çıkar ve çağıran "bilinmiyor" der.
        """
        cls = type(self)
        if (not force_fresh and cls._account_cache is not None
                and time.monotonic() - cls._account_cache_ts < cls._ACCOUNT_CACHE_TTL):
            return cls._account_cache
        if (priority != "critical" and cls._account_cache is not None
                and cls.weight_backoff_active()):
            return cls._account_cache
        async with self._read_lock("_account_cache_lock"):
            if (not force_fresh and cls._account_cache is not None
                    and time.monotonic() - cls._account_cache_ts < cls._ACCOUNT_CACHE_TTL):
                return cls._account_cache
            gen = cls._write_generation
            # `priority` YALNIZ kritik olmayan çağrılarda geçilir: varsayılan
            # yolun imzası (ve onu taklit eden test çiftleri) değişmesin.
            extra = {} if priority == "critical" else {"priority": priority}
            response = await self._request_with_retry(
                "GET", "/fapi/v2/account", signed=True, **extra
            )
            # Fetch sırasında yazma olduysa yanıt emir-öncesi olabilir:
            # çağırana döner ama "taze" diye damgalanmaz.
            if cls._write_generation == gen:
                cls._account_cache = response
                cls._account_cache_ts = time.monotonic()
            return response

    async def get_account_balance(
        self, *, priority: str = "critical"
    ) -> Optional[float]:
        """Kullanılabilir USDT bakiyesi.

        DİKKAT: Hata durumunda None döner (0.0 DEĞİL). Çağıran taraf bunu
        "bakiye bilinmiyor" olarak ele almalı ve işlem AÇMAMALIDIR. Eskiden
        0.0 dönüyordu ve bu, config'deki sahte bakiyeye düşülmesine yol açıyordu.
        """
        try:
            extra = {} if priority == "critical" else {"priority": priority}
            response = await self._get_account(**extra)
            for asset in response.get("assets", []):
                if asset["asset"] == "USDT":
                    balance = float(asset["availableBalance"])
                    self.logger.info(f"💰 Hesap bakiyesi: {balance:.2f} USDT")
                    return balance
            self.logger.error("USDT varlığı hesapta bulunamadı")
            return None
        except Exception as e:
            self.logger.error(f"Bakiye sorgusu hatası: {e}")
            return None

    async def get_wallet_balance(self) -> Optional[float]:
        """USDT wallet equity before open-order/position margin deductions."""

        try:
            response = await self._get_account()
            total = response.get("totalWalletBalance")
            if total not in (None, ""):
                return float(total)
            for asset in response.get("assets", []):
                if asset.get("asset") == "USDT":
                    return float(asset.get("walletBalance"))
            self.logger.error("USDT wallet bakiyesi hesapta bulunamadı")
            return None
        except Exception as e:
            self.logger.error(f"Wallet bakiyesi sorgusu hatası: {e}")
            return None

    @staticmethod
    def _validated_commission_decimal(value: Any, field: str) -> Decimal:
        """Binance komisyon değerini sonlu, oran biçimli Decimal'e çevir."""
        try:
            rate = Decimal(str(value))
        except Exception as exc:
            raise BinanceAPIError(
                502,
                None,
                f"commissionRate yanıtında geçersiz {field}: {value!r}",
                "/fapi/v1/commissionRate",
            ) from exc
        if not rate.is_finite() or rate < 0 or rate >= 1:
            raise BinanceAPIError(
                502,
                None,
                f"commissionRate yanıtında aralık dışı {field}: {value!r}",
                "/fapi/v1/commissionRate",
            )
        return rate

    async def get_user_commission_rate(self, symbol: str) -> Dict[str, Any]:
        """Kullanıcının sembol bazlı maker/taker oranlarını getir (cache'li).

        Oranlar kayan nokta yuvarlama hatası taşımamaları için ``Decimal``
        döner. Endpoint IP weight=20 olduğundan aynı sembol bir saat boyunca
        yeniden sorgulanmaz.
        """
        normalized_symbol = str(symbol).strip().upper()
        if not normalized_symbol:
            raise ValueError("Komisyon oranı için sembol boş olamaz")

        now = time.monotonic()
        cached = self._commission_rates.get(normalized_symbol)
        if cached and (now - cached[1]) < self._COMMISSION_CACHE_TTL:
            return dict(cached[0])

        async with self._commission_lock:
            cached = self._commission_rates.get(normalized_symbol)
            if cached and (
                time.monotonic() - cached[1]
            ) < self._COMMISSION_CACHE_TTL:
                return dict(cached[0])

            response = await self._request_with_retry(
                "GET",
                "/fapi/v1/commissionRate",
                params={"symbol": normalized_symbol},
                signed=True,
            )
            if not isinstance(response, dict):
                raise BinanceAPIError(
                    502,
                    None,
                    "commissionRate yanıtı nesne değil",
                    "/fapi/v1/commissionRate",
                )
            response_symbol = str(response.get("symbol", normalized_symbol)).upper()
            if response_symbol != normalized_symbol:
                raise BinanceAPIError(
                    502,
                    None,
                    f"commissionRate sembol uyuşmazlığı: {response_symbol}",
                    "/fapi/v1/commissionRate",
                )

            validated = dict(response)
            validated["symbol"] = normalized_symbol
            validated["makerCommissionRate"] = self._validated_commission_decimal(
                response.get("makerCommissionRate"), "makerCommissionRate"
            )
            validated["takerCommissionRate"] = self._validated_commission_decimal(
                response.get("takerCommissionRate"), "takerCommissionRate"
            )
            self._commission_rates[normalized_symbol] = (
                validated,
                time.monotonic(),
            )
            return dict(validated)

    async def set_leverage(self, symbol: str, leverage: int) -> Dict[str, Any]:
        try:
            response = await self._request_with_retry(
                "POST", "/fapi/v1/leverage",
                params={"symbol": symbol, "leverage": leverage}, signed=True,
            )
            self.logger.info(f"⚡ {symbol} leverage {leverage}x olarak ayarlandı")
            return response
        except Exception as e:
            self.logger.error(f"Leverage ayarlama hatası: {e}")
            raise

    async def set_margin_type(self, symbol: str, margin_type: str = "ISOLATED") -> Dict[str, Any]:
        try:
            response = await self._request_with_retry(
                "POST", "/fapi/v1/marginType",
                params={"symbol": symbol, "marginType": margin_type.upper()}, signed=True,
            )
            self.logger.info(f"🔧 {symbol} margin type {margin_type} olarak ayarlandı")
            return response
        except BinanceAPIError as e:
            # -4046: zaten istenen değerde — hata değil
            if e.code == ERR_NO_NEED_MARGIN or "No need to change margin type" in e.msg:
                self.logger.debug(f"Margin type zaten {margin_type}")
                return {"msg": "Already set"}
            raise

    async def get_symbol_precision(self, symbol: str) -> Tuple[int, int]:
        """(quantity_precision, price_precision) — geriye dönük uyumluluk."""
        try:
            f = await self.get_symbol_filters(symbol)
            return f["quantityPrecision"], f["pricePrecision"]
        except Exception as e:
            self.logger.error(f"Precision sorgusu hatası: {e}")
            raise

    def round_quantity(self, quantity: float, precision: int) -> float:
        return float(Decimal(str(quantity)).quantize(
            Decimal(1).scaleb(-precision), rounding=ROUND_DOWN
        ))

    def round_price(self, price: float, precision: int) -> float:
        return float(Decimal(str(price)).quantize(
            Decimal(1).scaleb(-precision), rounding=ROUND_DOWN
        ))

    # ------------------------------------------------------------------
    # Emirler
    # ------------------------------------------------------------------

    async def open_market_order(
        self, symbol: str, side: str, quantity: float
    ) -> Dict[str, Any]:
        """Market emri aç ve GERÇEK dolum bilgisiyle dön.

        newOrderRespType=RESULT kullanılır. Varsayılan ACK yanıtı avgPrice=null
        ve executedQty=0 döndürür; buna güvenen kod dolum fiyatını asla öğrenemez.
        """
        quantity = await self.quantize_quantity(symbol, quantity)
        reference_price = await self.get_current_price(symbol)
        if reference_price is None:
            raise BinanceAPIError(
                503, None, f"{symbol} fiyatı alınamadı — emir doğrulanamıyor"
            )
        await self.validate_order(symbol, quantity, reference_price)

        response = await self._request_with_retry(
            "POST", "/fapi/v1/order",
            params={
                "symbol": symbol,
                "side": side,
                "type": "MARKET",
                "quantity": quantity,
                "newOrderRespType": "RESULT",
            },
            signed=True,
        )
        self.logger.info(
            f"🚀 Market order açıldı: {symbol} {side} {quantity} "
            f"(dolum: {response.get('avgPrice')} / {response.get('executedQty')})",
            extra={"trade": True},
        )
        return response

    async def get_order(self, symbol: str, order_id: int) -> Dict[str, Any]:
        """Tek bir emrin güncel durumunu sorgula (dolum fiyatı doğrulaması için)."""
        return await self._request_with_retry(
            "GET", "/fapi/v1/order",
            params={"symbol": symbol, "orderId": order_id}, signed=True,
        )

    async def get_order_by_client_id(
        self, symbol: str, client_order_id: str
    ) -> Dict[str, Any]:
        """Emri deterministik istemci kimliğiyle sorgula.

        POST yanıtı kaybolduğunda Binance sonucu "unknown" kabul edilmelidir;
        aynı emri yeni bir kimlikle tekrar göndermek yerine bu uçla uzlaştırılır.
        """
        return await self._request_with_retry(
            "GET", "/fapi/v1/order",
            params={"symbol": symbol, "origClientOrderId": client_order_id},
            signed=True,
        )

    # --- Koşullu emirler: Algo Order API ------------------------------
    #
    # 2025-12-09'dan itibaren Binance USDⓈ-M Futures'ta koşullu emirler
    # (STOP_MARKET / TAKE_PROFIT_MARKET / STOP / TAKE_PROFIT /
    # TRAILING_STOP_MARKET) /fapi/v1/order üzerinden KABUL EDİLMEZ; -4120 ile
    # reddedilir. Bunlar /fapi/v1/algoOrder üzerinden gönderilir.
    #
    # Farklar:
    #   - stopPrice  -> triggerPrice
    #   - orderId    -> algoId
    #   - listeleme  -> /fapi/v1/openAlgoOrders (eski /fapi/v1/openOrders
    #                   koşullu emirleri GÖSTERMEZ)
    #   - iptal      -> DELETE /fapi/v1/algoOrder?algoId=
    #
    # Aşağıdaki metodlar yanıta `orderId` takma adını ekler; böylece emir
    # kimliğini saklayan mevcut kod (PositionModel.sl_order_id vb.) değişmeden
    # çalışır.

    async def get_algo_order(
        self,
        *,
        algo_id: Optional[int] = None,
        client_algo_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Koşullu emri ``algoId`` VEYA ``clientAlgoId`` ile sorgula."""
        if (algo_id is None) == (client_algo_id is None):
            raise ValueError(
                "Tam olarak bir algo_id veya client_algo_id gönderilmelidir"
            )

        params: Dict[str, Any]
        if algo_id is not None:
            normalized_algo_id = int(algo_id)
            if normalized_algo_id <= 0:
                raise ValueError("algo_id pozitif olmalıdır")
            params = {"algoId": normalized_algo_id}
        else:
            normalized_client_id = str(client_algo_id or "")
            if not self._CLIENT_ALGO_ID_RE.fullmatch(normalized_client_id):
                raise ValueError("client_algo_id Binance biçimine uymuyor")
            params = {"clientAlgoId": normalized_client_id}

        response = await self._request_with_retry(
            "GET", "/fapi/v1/algoOrder", params=params, signed=True
        )
        if not isinstance(response, dict):
            raise BinanceAPIError(
                502, None, "algoOrder yanıtı nesne değil", "/fapi/v1/algoOrder"
            )
        return response

    @staticmethod
    def _conditional_post_needs_reconciliation(exc: Exception) -> bool:
        """POST sonucu belirsiz veya istemci kimliği mükerrer mi?"""
        if not isinstance(exc, BinanceAPIError):
            # Transport/timeout/JSON decode gibi POST sonrası hatalarda emir
            # eşleştirme motoruna ulaşmış olabilir.
            return True
        message = exc.msg.lower()
        return (
            exc.code == ERR_DUPLICATE_CLIENT_ORDER_ID
            or exc.code in UNKNOWN_EXECUTION_CODES
            or exc.status_code >= 500
            or ("duplicate" in message and "client" in message)
        )

    @staticmethod
    def _algo_query_not_found(exc: Exception) -> bool:
        """Eventual-consistency sırasında henüz görünmeyen algo cevabı mı?"""
        if not isinstance(exc, BinanceAPIError):
            return False
        message = exc.msg.lower()
        return exc.code in {-2011, -2013} or "not exist" in message

    @staticmethod
    def _normalized_conditional_response(
        response: Dict[str, Any],
        *,
        expected_client_algo_id: str,
        reconciled: bool,
    ) -> Dict[str, Any]:
        """Algo cevabını doğrula ve eski ``orderId`` alias'ını ekle."""
        if not isinstance(response, dict):
            raise BinanceAPIError(
                502, None, "algoOrder yanıtı nesne değil", "/fapi/v1/algoOrder"
            )

        normalized = dict(response)
        returned_client_id = normalized.get("clientAlgoId")
        if returned_client_id not in (None, "") and (
            str(returned_client_id) != expected_client_algo_id
        ):
            raise BinanceAPIError(
                502,
                None,
                "algoOrder clientAlgoId uyuşmazlığı",
                "/fapi/v1/algoOrder",
            )
        normalized["clientAlgoId"] = expected_client_algo_id

        algo_id = normalized.get("algoId")
        try:
            valid_algo_id = int(algo_id)
        except (TypeError, ValueError) as exc:
            raise BinanceAPIError(
                502,
                None,
                f"algoOrder yanıtında geçersiz algoId: {algo_id!r}",
                "/fapi/v1/algoOrder",
            ) from exc
        if valid_algo_id <= 0:
            raise BinanceAPIError(
                502,
                None,
                f"algoOrder yanıtında geçersiz algoId: {algo_id!r}",
                "/fapi/v1/algoOrder",
            )
        normalized["algoId"] = valid_algo_id
        # Geriye uyumluluk: bu alias gerçek tetiklenen orderId DEĞİL, algoId.
        # Gerçek dolum emri Query Algo yanıtındaki actualOrderId alanındadır.
        normalized["orderId"] = valid_algo_id
        normalized["isAlgo"] = True
        if reconciled:
            normalized["reconciled"] = True
        return normalized

    async def _place_conditional(
        self, symbol: str, params: Dict[str, Any], label: str
    ) -> Dict[str, Any]:
        # Bir mantıksal emir = bir clientAlgoId. _request_with_retry kendi
        # denemelerinde base_params kopyaladığı için tüm POST retry'ları AYNI
        # kimliği taşır; burada asla ikinci/yeni kimlik üretilmez.
        client_algo_id = f"awa_{uuid.uuid4().hex}"
        request_params = {
            "algoType": "CONDITIONAL",
            "symbol": symbol,
            **params,
            "clientAlgoId": client_algo_id,
        }
        reconciled = False
        try:
            response = await self._request_with_retry(
                "POST",
                "/fapi/v1/algoOrder",
                params=request_params,
                signed=True,
            )
            normalized = self._normalized_conditional_response(
                response,
                expected_client_algo_id=client_algo_id,
                reconciled=False,
            )
        except Exception as post_error:
            if not self._conditional_post_needs_reconciliation(post_error):
                raise
            # Cevap kaybı/5xx/-1007 veya -4116: yeni kimlikle kör tekrar yok.
            # Binance'te aynı clientAlgoId ile kabul edilmiş emri sorgula.
            # Yeni kayıt kısa süreli eventual-consistency gecikmesiyle -2013
            # dönebildiği için yalnız GET, sınırlı ve kısa aralıklarla yinelenir.
            last_query_error: Optional[Exception] = None
            for delay in self._ALGO_RECONCILE_DELAYS:
                if delay > 0:
                    await asyncio.sleep(delay)
                try:
                    response = await self.get_algo_order(
                        client_algo_id=client_algo_id
                    )
                    break
                except Exception as query_error:
                    last_query_error = query_error
                    if not self._algo_query_not_found(query_error) and not isinstance(
                        query_error,
                        (httpx.TransportError, TimeoutError),
                    ):
                        self.logger.error(
                            f"{label}: clientAlgoId sorgusu kesin hata verdi "
                            f"({client_algo_id}): {query_error}"
                        )
                        raise post_error from query_error
            else:
                self.logger.error(
                    f"{label}: koşullu POST sonucu belirsiz ve clientAlgoId "
                    f"uzlaştırılamadı ({client_algo_id}): {last_query_error}"
                )
                raise post_error from last_query_error
            reconciled = True
            normalized = self._normalized_conditional_response(
                response,
                expected_client_algo_id=client_algo_id,
                reconciled=True,
            )
        self.logger.debug(
            f"{label}: algoId={normalized['algoId']} "
            f"clientAlgoId={client_algo_id} reconciled={reconciled}"
        )
        return normalized

    async def place_stop_loss(
        self,
        symbol: str,
        side: str,
        stop_price: float,
        close_position: bool = True,
        quantity: Optional[float] = None,
    ) -> Dict[str, Any]:
        """STOP_MARKET koşullu emri koy (Algo Order API).

        İki mod vardır ve seçim güvenlik açısından önemlidir:

        - close_position=True: pozisyonun TAMAMINI kapatır, miktar gerektirmez.
          İlk koruma için idealdir. ANCAK aynı yönde ikinci bir closePosition
          stop emri Binance tarafından reddedilir (-4130), bu yüzden mevcut bir
          stop'u DEĞİŞTİRİRKEN kullanılamaz.

        - close_position=False + quantity: reduceOnly stop. Bunlardan birden
          fazlası bir arada bulunabilir; bu sayede stop değiştirilirken önce
          yeni emir konup sonra eskisi iptal edilebilir ve pozisyon bir an bile
          korumasız kalmaz.
        """
        side = side.upper()
        stop_price = await self.quantize_protective_price(symbol, stop_price, side)
        if stop_price <= 0:
            raise BinanceAPIError(
                400, ERR_PRECISION,
                f"Geçersiz stop fiyatı: {stop_price}. Giriş fiyatı doğru "
                f"okunamamış olabilir.",
            )

        params: Dict[str, Any] = {
            "side": side,
            "type": "STOP_MARKET",
            "triggerPrice": stop_price,
        }
        if close_position:
            params["closePosition"] = "true"
        else:
            if quantity is None or quantity <= 0:
                raise BinanceAPIError(
                    400, ERR_PRECISION,
                    "reduceOnly stop emri için geçerli bir miktar gerekir",
                )
            params["quantity"] = await self.quantize_quantity(symbol, quantity)
            params["reduceOnly"] = "true"

        response = await self._place_conditional(symbol, params, "stop-loss")
        self.logger.info(
            f"🛡️ Stop Loss kondu: {symbol} @ {stop_price} (algoId={response.get('algoId')})",
            extra={"trade": True},
        )
        return response

    async def place_take_profit(
        self, symbol: str, side: str, stop_price: float, quantity: float
    ) -> Dict[str, Any]:
        """TAKE_PROFIT_MARKET koşullu emri koy — DAİMA reduceOnly.

        reduceOnly olmadan, pozisyon SL ile kapandıktan sonra bekleyen TP emri
        tetiklenirse TERS YÖNDE YENİ bir pozisyon açar.
        """
        side = side.upper()
        quantity = await self.quantize_quantity(symbol, quantity)
        stop_price = await self.quantize_protective_price(symbol, stop_price, side)

        if quantity <= 0:
            raise BinanceAPIError(
                400, ERR_PRECISION,
                f"TP miktarı yuvarlama sonrası sıfır ({quantity}). "
                f"Pozisyon stepSize'a göre çok küçük.",
            )
        if stop_price <= 0:
            raise BinanceAPIError(400, ERR_PRECISION, f"Geçersiz TP fiyatı: {stop_price}")

        response = await self._place_conditional(
            symbol,
            {
                "side": side,
                "type": "TAKE_PROFIT_MARKET",
                "triggerPrice": stop_price,
                "quantity": quantity,
                "reduceOnly": "true",
            },
            "take-profit",
        )
        self.logger.info(
            f"🎯 Take Profit kondu: {symbol} {quantity} @ {stop_price} "
            f"(reduceOnly, algoId={response.get('algoId')})",
            extra={"trade": True},
        )
        return response

    async def get_open_algo_orders(self, symbol: str) -> List[Dict[str, Any]]:
        """Açık KOŞULLU emirler.

        /fapi/v1/openOrders bu emirleri GÖSTERMEZ — stop-loss aramak için
        mutlaka bu metod kullanılmalıdır.
        """
        response = await self._request_with_retry(
            "GET", "/fapi/v1/openAlgoOrders", params={"symbol": symbol}, signed=True
        )
        return response if isinstance(response, list) else []

    async def cancel_algo_order(
        self, algo_id: int, *, symbol: Optional[str] = None
    ) -> Dict[str, Any]:
        """Koşullu emri iptal et.

        symbol API'ye gönderilmez; verilirse önbellek invalidasyonu global
        yerine yalnız o sembole uygulanır (bkz. _invalidate_read_caches).
        """
        try:
            response = await self._request_with_retry(
                "DELETE", "/fapi/v1/algoOrder", params={"algoId": algo_id},
                signed=True, invalidate_symbol=symbol,
            )
            self.logger.info(f"❌ Koşullu emir iptal edildi: algoId={algo_id}")
            return response
        except BinanceAPIError as e:
            # Emir zaten tetiklenmiş/iptal edilmiş olabilir — idempotent kabul et
            if e.code in (-2011, -4046) or "not exist" in e.msg.lower():
                self.logger.debug(f"Koşullu emir zaten yok: algoId={algo_id}")
                return {"algoStatus": "ALREADY_GONE"}
            raise

    async def cancel_order(self, symbol: str, order_id: int) -> Dict[str, Any]:
        try:
            response = await self._request_with_retry(
                "DELETE", "/fapi/v1/order",
                params={"symbol": symbol, "orderId": order_id}, signed=True,
            )
            self.logger.info(f"❌ Order iptal edildi: {symbol} #{order_id}")
            return response
        except BinanceAPIError as e:
            # -2011: emir zaten yok (dolmuş/iptal) — idempotent kabul et
            if e.code == -2011:
                self.logger.debug(f"Order #{order_id} zaten yok")
                return {"status": "ALREADY_GONE"}
            raise

    async def cancel_order_by_client_id(
        self, symbol: str, client_order_id: str
    ) -> Dict[str, Any]:
        """Normal emri istemci kimliğiyle idempotent olarak iptal et."""
        try:
            response = await self._request_with_retry(
                "DELETE", "/fapi/v1/order",
                params={"symbol": symbol, "origClientOrderId": client_order_id},
                signed=True,
            )
            self.logger.info(
                f"❌ Order iptal edildi: {symbol} clientOrderId={client_order_id}"
            )
            return response
        except BinanceAPIError as e:
            if e.code == -2011:
                self.logger.debug(
                    f"Order clientOrderId={client_order_id} zaten yok"
                )
                return {"status": "ALREADY_GONE"}
            raise

    async def get_open_orders(self, symbol: str) -> List[Dict[str, Any]]:
        """Açık emirleri getir.

        DİKKAT: Hata durumunda BOŞ LİSTE DÖNDÜRMEZ, istisna fırlatır. Eskiden
        [] dönüyordu; çağıran "iptal edilecek emir yok" sanıp yeni bir SL
        ekliyordu ve borsada mükerrer stop emirleri birikiyordu.
        """
        return await self._request_with_retry(
            "GET", "/fapi/v1/openOrders", params={"symbol": symbol}, signed=True
        )

    async def cancel_all_open_orders(self, symbol: str) -> Dict[str, Any]:
        """Sembolün TÜM açık emirlerini iptal et — normal VE koşullu.

        /fapi/v1/allOpenOrders koşullu (algo) emirleri kapsamaz; onlar tek tek
        iptal edilmelidir. Yalnızca ilkini çağırmak, pozisyon kapandıktan sonra
        borsada asılı kalan stop/TP emirleri bırakır.
        """
        result: Dict[str, Any] = {}
        try:
            result["orders"] = await self._request_with_retry(
                "DELETE", "/fapi/v1/allOpenOrders", params={"symbol": symbol}, signed=True
            )
        except Exception as e:
            # -2011 ("Order does not exist") beklenen bir yarıştır: emir
            # aradaki milisaniyelerde dolmuş/iptal olmuştur (D22 madde 4).
            if is_benign_cancel_error(e):
                # D22: INFO — arıza değil, ama defter sapmasının izi olabilir
                # (DEBUG üretimde kapalıdır ve iz tamamen kaybolurdu).
                self.logger.info(f"ℹ️ {symbol}: iptal edilecek normal emir yok ({e})")
            else:
                self.logger.warning(f"{symbol}: normal emirler iptal edilemedi: {e}")
            result["orders"] = {"error": str(e)}

        cancelled = 0
        try:
            for algo in await self.get_open_algo_orders(symbol):
                await self.cancel_algo_order(int(algo["algoId"]), symbol=symbol)
                cancelled += 1
        except Exception as e:
            if is_benign_cancel_error(e):
                self.logger.info(f"ℹ️ {symbol}: iptal edilecek koşullu emir yok ({e})")
            else:
                self.logger.warning(f"{symbol}: koşullu emirler iptal edilemedi: {e}")
        result["algo_cancelled"] = cancelled
        return result

    # ------------------------------------------------------------------
    # Pozisyon / piyasa
    # ------------------------------------------------------------------

    async def get_position_risk(
        self, symbol: str, *, force_fresh: bool = False
    ) -> Optional[Dict[str, Any]]:
        """Pozisyon bilgisi. Hata durumunda istisna fırlatır (None DEĞİL).

        None yalnızca "borsa bu sembol için kayıt döndürmedi" anlamına gelir.
        Ağ hatasını 'pozisyon kapandı' sanmak, izlemenin sessizce durmasına
        yol açıyordu.

        Ağırlık diyeti (2026-08-15): sembolsüz /fapi/v2/positionRisk tüm
        sembolleri TEK weight-5 çağrıyla döndürür; okumalar 5 sn'lik süreç-
        geneli anlık görüntüden beslenir. GÜVENLİK KURALI: önbellek "pozisyon
        sıfır / kayıt yok" diyorsa taze doğrulama ZORUNLU — bayat görüntüyle
        asla 'kapandı' kararı verilmez (14 Ağu toplu UNKNOWN kapanışı dersi).
        Tek istisna: _POS_SNAPSHOT_FRESH_S'ten (1 sn) taze VE son yazmadan
        sonra alınmış TAM snapshot taze doğrulama sayılır — aynı safety turu
        içindeki mükerrer weight-5 çağrıları bastırmak için. force_fresh=True
        bu istisnayı da atlar; geri alınamaz kararlar onu kullanmalı.
        Not: tek-yön (one-way) mod varsayılır; hedge modda sembol başına iki
        kayıt gelir ve sözlükte son gelen kazanır.
        """
        cls = type(self)
        sym = str(symbol).strip().upper()

        if not force_fresh and cls._pos_snapshot is not None:
            if time.monotonic() - cls._pos_snapshot_ts < cls._POS_SNAPSHOT_TTL:
                entry = cls._pos_snapshot.get(sym)
                if entry is not None and abs(float(entry.get("positionAmt", 0) or 0)) > 0:
                    return entry
                # sıfır/eksik → aşağıda taze doğrulama

        async with self._read_lock("_pos_snapshot_lock"):
            # Kilit beklerken başka bir coroutine tazelemiş olabilir;
            # _POS_SNAPSHOT_FRESH_S'ten taze ve yazma-sonrası bir görüntü
            # "taze doğrulama" sayılır (aynı safety turu). Jenerasyon şartı,
            # az önce emir atılmış bir sembol için emir-öncesi sıfırın
            # servis edilmesini engeller.
            if not force_fresh and cls._pos_snapshot is not None:
                if (
                    time.monotonic() - cls._pos_snapshot_ts < cls._POS_SNAPSHOT_FRESH_S
                    and cls._write_generation == cls._pos_snapshot_gen
                ):
                    return cls._pos_snapshot.get(sym)
            gen = cls._write_generation
            response = await self._request_with_retry(
                "GET", "/fapi/v2/positionRisk", signed=True
            )
            if not isinstance(response, list):
                raise BinanceAPIError(
                    502, None, "positionRisk yanıtı liste değil", "/fapi/v2/positionRisk"
                )
            snapshot = {
                str(p.get("symbol", "")).upper(): p
                for p in response
                if isinstance(p, dict)
            }
            # Fetch sırasında yazma olduysa yanıt emir-öncesi olabilir:
            # çağırana döner ama önbelleğe "taze" diye damgalanmaz.
            if cls._write_generation == gen:
                cls._pos_snapshot = snapshot
                cls._pos_snapshot_ts = time.monotonic()
                cls._pos_snapshot_gen = gen
            return snapshot.get(sym)

    async def get_all_positions(
        self, *, force_fresh: bool = True, priority: str = "critical"
    ) -> List[Dict[str, Any]]:
        """Borsadaki TÜM açık pozisyonlar — restart sonrası kurtarma için.

        Kurtarma kararı geri alınamaz; önbellek DEĞİL, her zaman taze okunur
        (varsayılan). force_fresh=False YALNIZ gösterim amaçlı çağrılar
        içindir (dashboard /api/status): 5 sn'lik panel polling'i her tikte
        weight-5 taze çağrı yapınca 2.5 sn'lik rate-limiter kuyruğunu tek
        başına doyurup scan döngüsünü açlığa itiyordu (2026-08-18 watchdog
        restart'ının kök nedeni) — 15 sn'lik account önbelleği panel için
        fazlasıyla taze.
        """
        extra = {} if priority == "critical" else {"priority": priority}
        account = await self._get_account(force_fresh=force_fresh, **extra)
        return [
            p for p in account.get("positions", [])
            if float(p.get("positionAmt", 0)) != 0
        ]

    async def get_income_history(
        self,
        *,
        start_time_ms: Optional[int] = None,
        end_time_ms: Optional[int] = None,
        symbol: Optional[str] = None,
        income_type: Optional[str] = None,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """Get signed USD-M income rows for verified net-PnL accounting."""

        params: Dict[str, Any] = {"limit": max(1, min(int(limit), 1000))}
        if start_time_ms is not None:
            params["startTime"] = int(start_time_ms)
        if end_time_ms is not None:
            params["endTime"] = int(end_time_ms)
        if symbol:
            params["symbol"] = str(symbol).upper()
        if income_type:
            params["incomeType"] = str(income_type).upper()

        rows = await self._request_with_retry(
            "GET", "/fapi/v1/income", params=params, signed=True
        )
        if not isinstance(rows, list):
            raise BinanceAPIError(
                502, None, "Income history yanıtı liste değil", "/fapi/v1/income"
            )
        return rows

    async def get_account_trades(
        self,
        symbol: str,
        *,
        order_id: Optional[int] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        """Sembolün imzalı gerçek fill/komisyon satırlarını getir.

        Binance ``userTrades`` yanıtı her fill için ``commission``,
        ``commissionAsset``, ``realizedPnl`` ve maker/taker bilgisini taşır.
        Böylece tahmini brüt PnL yerine emir bazlı net muhasebe kurulabilir.
        """
        normalized_symbol = str(symbol).strip().upper()
        if not normalized_symbol:
            raise ValueError("Trade geçmişi için sembol boş olamaz")

        params: Dict[str, Any] = {
            "symbol": normalized_symbol,
            "limit": max(1, min(int(limit), 1000)),
        }
        if order_id is not None and (start_time is not None or end_time is not None):
            raise ValueError("order_id ile start_time/end_time birlikte gönderilemez")
        if order_id is not None:
            normalized_order_id = int(order_id)
            if normalized_order_id <= 0:
                raise ValueError("order_id pozitif olmalıdır")
            params["orderId"] = normalized_order_id
        if start_time is not None:
            params["startTime"] = int(start_time)
        if end_time is not None:
            params["endTime"] = int(end_time)
        if (
            start_time is not None
            and end_time is not None
            and int(start_time) > int(end_time)
        ):
            raise ValueError("start_time end_time'dan büyük olamaz")

        response = await self._request_with_retry(
            "GET", "/fapi/v1/userTrades", params=params, signed=True
        )
        if not isinstance(response, list) or not all(
            isinstance(row, dict) for row in response
        ):
            raise BinanceAPIError(
                502,
                None,
                "userTrades yanıtı nesne listesi değil",
                "/fapi/v1/userTrades",
            )
        return response

    async def get_current_price(
        self, symbol: str, *, priority: str = "critical"
    ) -> Optional[float]:
        cls = type(self)
        sym = str(symbol).strip().upper()
        cached = cls._price_cache.get(sym)
        if cached is not None and time.monotonic() - cached[0] < cls._PRICE_CACHE_TTL:
            return cached[1]
        # D22: pano beslemesi ağırlık geri çekilmesinde SÜRESİ GEÇMİŞ fiyatı
        # gösterir (ek istek yok). Koruma yolu (critical) buraya düşmez.
        if (priority != "critical" and cached is not None
                and cls.weight_backoff_active()):
            return cached[1]
        extra = {} if priority == "critical" else {"priority": priority}
        try:
            response = await self._request_with_retry(
                "GET", "/fapi/v1/ticker/price", params={"symbol": sym}, **extra
            )
            price = float(response["price"])
            cls._price_cache[sym] = (time.monotonic(), price)
            if len(cls._price_cache) > cls._PRICE_CACHE_MAX:
                # waiting-mode/TV kaynaklı rastgele semboller haftalar içinde
                # birikmesin: tavan aşılınca süresi geçen girdiler atılır.
                cutoff = time.monotonic() - cls._PRICE_CACHE_TTL
                cls._price_cache = {
                    k: v for k, v in cls._price_cache.items() if v[0] >= cutoff
                }
            return price
        except RestWeightBackoff as e:
            # D22: bilinçli erteleme, arıza değil — ERROR loglanmaz.
            self.logger.debug(str(e))
            return None
        except Exception as e:
            self.logger.error(f"Fiyat sorgusu hatası: {e}")
            return None

    async def get_book_ticker(self, symbol: str) -> Dict[str, Any]:
        """Sembolün anlık en iyi alış/satış kotasyonunu getir."""
        response = await self._request_with_retry(
            "GET", "/fapi/v1/ticker/bookTicker", params={"symbol": symbol}
        )
        if not isinstance(response, dict):
            raise BinanceAPIError(
                502, None, f"{symbol}: bookTicker yanıtı nesne değil"
            )
        try:
            bid = float(response["bidPrice"])
            ask = float(response["askPrice"])
        except (KeyError, TypeError, ValueError) as e:
            raise BinanceAPIError(
                502, None, f"{symbol}: bookTicker fiyatları okunamadı ({e})"
            ) from e
        if bid <= 0 or ask <= 0 or bid > ask:
            raise BinanceAPIError(
                502, None, f"{symbol}: geçersiz bookTicker (bid={bid}, ask={ask})"
            )
        return response

    # ------------------------------------------------------------------
    # USDⓈ-M User Data Stream (API-key header, imzasız)
    # ------------------------------------------------------------------

    @property
    def listen_key(self) -> Optional[str]:
        return self._listen_key

    async def create_listen_key(self) -> str:
        response = await self._request_api_key_only(
            "POST", "/fapi/v1/listenKey"
        )
        listen_key = response.get("listenKey")
        if not isinstance(listen_key, str) or not listen_key:
            raise BinanceAPIError(
                502, None, "listenKey yanıtında geçerli anahtar yok",
                "/fapi/v1/listenKey",
            )
        self._listen_key = listen_key
        return listen_key

    async def keepalive_listen_key(self) -> None:
        if not self._listen_key:
            raise RuntimeError("Keepalive için aktif listenKey yok")
        await self._request_api_key_only("PUT", "/fapi/v1/listenKey")

    async def delete_listen_key(self) -> None:
        if not self._listen_key:
            return
        await self._request_api_key_only("DELETE", "/fapi/v1/listenKey")
        self._listen_key = None

    def invalidate_listen_key(self) -> None:
        """Binance listenKeyExpired eventi sonrası local anahtarı unut."""
        self._listen_key = None

    async def get_server_time(self) -> int:
        try:
            response = await self._request_with_retry("GET", "/fapi/v1/time")
            return response.get("serverTime", 0)
        except Exception as e:
            self.logger.error(f"Server zamanı sorgusu hatası: {e}")
            return 0

    async def close(self):
        await self.client.aclose()
