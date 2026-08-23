"""AlgoPro olaylarını takipçi halkasına ileten köprü (ana bot → :9093).

NEDEN: TradingView alarm URL'leri (ve içindeki secret) DEĞİŞMESİN. Kullanıcı
49 alarmı yeniden kurmak zorunda kalmadan, ana bot (scalper halkası, :9091)
TEK TV girişi olarak kalır ve AlgoPro kaynaklı olayları ayrı bir secret ile
takipçi halkasına (``FOLLOWER_FORWARD_URL``) iletir.

GARANTİLER:
  * **Fire-and-forget**: istek ayrı bir task'ta gider; ``/tv-signal`` yanıtı
    beklemez. Ana motor bu köprüden ETKİLENMEZ.
  * **Sınırlı süre**: bağlantı/yazma 2 sn (erişilemeyen halka ana botta task
    biriktirmesin), okuma ``FOLLOWER_FORWARD_TIMEOUT_SECONDS`` (varsayılan
    **20 sn**): ``/follower/event`` yanıtı olay TAMAMEN işlendikten sonra
    döner ve bir giriş 3-6 sn sürer; kısa okuma timeout'u her BAŞARILI
    girişte sahte "iletemedi" uyarısı üretirdi (D20 kendi-inceleme notu n).
  * **Sessiz kalmaz**: her hata WARNING/ERROR loglanır; iletilmeyen gövdeler
    sayaçlara işlenir (``forwarder_stats()`` → ``GET /follower/forwarder``).
  * **Secret loglanmaz**: header'da taşınır, hiçbir log satırına yazılmaz;
    URL de secret İÇERMEZ (bu yüzden erişim logu sızdırmaz).
  * **Yalnız GERÇEK AlgoPro gövdeleri** (düşmanca inceleme bulgu 5): karar
    ``?src=``/``TV_SOURCE_ALLOWLIST``'e DEĞİL, gövdenin KENDİSİNE bakan katı
    tanıyıcıya (``parser.algopro_alert_kind``) dayanır. Eski parmak izi
    (``"| TF:" in body or "| Price:" in body``) elle yazılmış bir LuxAlgo/
    BotV3 şablonunda da tutabiliyordu ve takipçide sonucu POZİSYON açmaktı.
    Artık ``| BINANCE:<SEMBOL> |`` + ``| TF:`` + ``| Price:`` + başlıkta olay
    anahtarı (+ girişlerde dört seviye) şart; biri eksikse İLETİLMEZ.
  * Kapalı yapılandırma (URL veya secret boş) = özellik KAPALI, bugünkü
    davranış birebir korunur.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Dict, Optional, Set

import httpx

from src.core.config import settings
from src.core.logger import app_logger
from src.strategies.follower.parser import algopro_alert_kind

# Takipçi uç noktasının gövde sınırı ile aynı (bkz. main.follower_event).
MAX_FORWARD_BODY_BYTES = 4096
SECRET_HEADER = "X-Follower-Secret"

# Task referansları GC'den korunmalı (asyncio yalnız zayıf referans tutar).
_background_tasks: Set[asyncio.Task] = set()

# Bağlantı/yazma için üst sınır: takipçi halkası kapalıysa ana botta task
# birikmesin. OKUMA timeout'u yapılandırılabilir (bkz. modül başlığı).
CONNECT_TIMEOUT_SECONDS = 2.0

# Teşhis kaydından maskelenecek secret deseni (gövde içi `secret=…`).
_SECRET_IN_BODY_RE = re.compile(r"secret\s*[=:]\s*\S+", re.IGNORECASE)

# İletim telemetrisi — "sessiz kalmaz" ilkesinin sayaç tarafı.
_counters: Dict[str, int] = {}
# Son iletilmeyen gövdenin (kısaltılmış) teşhis kaydı. Secret İÇERMEZ:
# yalnız ilk 80 karakter ve neden yazılır.
_last_skipped: Dict[str, Any] = {}


# Başarısız iletim uyarıları ORAN SINIRLIDIR (dakikada 1): 8 sembol × 1m
# alarm hattında takipçi halkası düşerse log dakikada onlarca satırla dolar
# ve GERÇEK arıza (ban, disk, korumasız pozisyon) gözden kaçar. Bastırılan
# uyarılar sayaçta görünür (`suppressed_warnings`) — sessiz kalınmaz.
WARN_INTERVAL_SECONDS = 60.0
_last_warn_monotonic: Dict[str, float] = {}


def _count(name: str) -> None:
    _counters[name] = _counters.get(name, 0) + 1


def _warn_rate_limited(key: str, message: str) -> None:
    """Aynı türden uyarıyı en fazla dakikada bir logla; kalanı say."""
    now = time.monotonic()
    last = _last_warn_monotonic.get(key)
    if last is not None and (now - last) < WARN_INTERVAL_SECONDS:
        _count("suppressed_warnings")
        return
    _last_warn_monotonic[key] = now
    app_logger.warning(message)


def forwarder_stats() -> Dict[str, Any]:
    """Köprü sayaçları (``GET /follower/forwarder``). Secret İÇERMEZ."""
    return {
        "enabled": forward_enabled(),
        "read_timeout_seconds": _timeout_seconds(),
        "connect_timeout_seconds": CONNECT_TIMEOUT_SECONDS,
        "counters": dict(_counters),
        "last_skipped": dict(_last_skipped),
    }


def reset_forwarder_stats() -> None:
    """Yalnız testler için — sayaçları ve uyarı oran-sınırını sıfırla."""
    _counters.clear()
    _last_skipped.clear()
    _last_warn_monotonic.clear()


def forward_enabled() -> bool:
    """URL ve secret birlikte doluysa köprü açıktır."""
    url = str(getattr(settings, "follower_forward_url", "") or "").strip()
    secret = str(getattr(settings, "follower_forward_secret", "") or "").strip()
    return bool(url and secret)


def _timeout_seconds() -> float:
    """OKUMA timeout'u — varsayılan 20 sn (`config.py` ile AYNI değer)."""
    try:
        value = float(
            getattr(settings, "follower_forward_timeout_seconds", 20.0) or 20.0
        )
    except (TypeError, ValueError):
        value = 20.0
    return value if value > 0 else 20.0


def _timeout() -> Any:
    """httpx timeout: KISA bağlantı/yazma, UZUN okuma (bkz. modül başlığı)."""
    read = _timeout_seconds()
    connect = min(CONNECT_TIMEOUT_SECONDS, read)
    try:
        return httpx.Timeout(read, connect=connect, write=connect, pool=connect)
    except Exception:  # savunmacı: httpx sürümü farklıysa tek sayıya düş
        return read


async def _post(url: str, secret: str, body: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            response = await client.post(
                url,
                content=body.encode("utf-8"),
                headers={
                    SECRET_HEADER: secret,
                    "Content-Type": "text/plain; charset=utf-8",
                },
            )
        if response.status_code >= 400:
            # Yanıt gövdesi secret İÇERMEZ (takipçi uç noktası secret'ı
            # yankılamaz); yine de kısaltılarak loglanır.
            _count("http_error")
            _warn_rate_limited(
                "http_error",
                f"⚠️ Takipçi köprüsü HTTP {response.status_code}: "
                f"{response.text[:200]}",
            )
        else:
            _count("delivered")
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _count("transport_error")
        _warn_rate_limited(
            "transport_error",
            f"⚠️ Takipçi köprüsü iletemedi ({type(exc).__name__}: {exc}); "
            f"ana motor etkilenmedi",
        )


def _redacted_head(body: str) -> str:
    """Teşhis için gövdenin ilk 80 karakteri — `secret=…` MASKELENMİŞ.

    KRİTİK: `/tv-signal` gövdeleri secret'ı METİN İÇİNDE taşıyabilir
    (`secret=… BUY on BTCUSDT`). Maskelemeden saklamak, `GET
    /follower/forwarder` üzerinden TV_WEBHOOK_SECRET'ı sızdırırdı
    (CLAUDE.md kural 5).
    """
    return _SECRET_IN_BODY_RE.sub("secret=***", str(body or ""))[:80]


def _skip(reason: str, body: str, source: str) -> None:
    """İletilmeyen gövdeyi say + teşhis kaydını güncelle (secret'sız)."""
    _count(f"skipped_{reason}")
    _last_skipped.clear()
    _last_skipped.update(
        {
            "reason": reason,
            "source": str(source or "")[:32],
            "body_head": _redacted_head(body),
        }
    )


def maybe_forward_algopro_event(
    raw_body: str, source: str = ""
) -> Optional[asyncio.Task]:
    """GERÇEK AlgoPro V1.6 gövdesini takipçiye ilet (fire-and-forget).

    Karar GÖVDEYE bakar (``algopro_alert_kind``); ``source`` yalnız
    telemetri/log içindir — ``?src=`` ve ``TV_SOURCE_ALLOWLIST`` iletim
    kararına GİRMEZ (düşmanca inceleme bulgu 5).

    Dönüş: oluşturulan task (testler deterministik beklesin diye) ya da
    iletim yapılmadıysa ``None``. ASLA istisna yükseltmez.
    """
    try:
        body = str(raw_body or "")
        if not body.strip():
            return None
        if len(body.encode("utf-8")) > MAX_FORWARD_BODY_BYTES:
            _skip("oversize", body, source)
            app_logger.warning(
                "⚠️ Takipçi köprüsü: gövde 4KB sınırını aşıyor, iletilmedi"
            )
            return None

        kind = algopro_alert_kind(body)
        if kind is None:
            # SESSİZ KALMAZ: sayaç + (yalnız köprü açıkken) log. Kapalı
            # yapılandırmada her TV alarmı için log basmak gürültüdür.
            _skip("not_algopro", body, source)
            if forward_enabled():
                app_logger.info(
                    "ℹ️ Takipçi köprüsü: gövde AlgoPro V1.6 biçiminde değil "
                    f"(src='{str(source or '')[:32]}'), iletilmedi"
                )
            return None

        if not forward_enabled():
            _skip("disabled", body, source)
            return None

        url = str(getattr(settings, "follower_forward_url", "") or "").strip()
        secret = str(getattr(settings, "follower_forward_secret", "") or "").strip()

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Event loop yoksa (senkron bağlam) iletim yapılamaz — sessiz
            # kalmamak için loglanır.
            _skip("no_event_loop", body, source)
            app_logger.warning("⚠️ Takipçi köprüsü: event loop yok, iletilmedi")
            return None

        _count("forwarded")
        _count(f"forwarded_{kind}")
        task = loop.create_task(_post(url, secret, body))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
        return task
    except Exception as exc:  # savunmacı: köprü ana akışı ASLA düşürmez
        _count("setup_error")
        app_logger.warning(f"⚠️ Takipçi köprüsü kurulamadı ({exc})")
        return None
