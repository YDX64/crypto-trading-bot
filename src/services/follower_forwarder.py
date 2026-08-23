"""AlgoPro olaylarını takipçi halkasına ileten köprü (ana bot → :9093).

NEDEN: TradingView alarm URL'leri (ve içindeki secret) DEĞİŞMESİN. Kullanıcı
49 alarmı yeniden kurmak zorunda kalmadan, ana bot (scalper halkası, :9091)
TEK TV girişi olarak kalır ve AlgoPro kaynaklı olayları ayrı bir secret ile
takipçi halkasına (``FOLLOWER_FORWARD_URL``) iletir.

GARANTİLER:
  * **Fire-and-forget**: istek ayrı bir task'ta gider; ``/tv-signal`` yanıtı
    beklemez. Ana motor bu köprüden ETKİLENMEZ.
  * **Sınırlı süre**: ``FOLLOWER_FORWARD_TIMEOUT_SECONDS`` (varsayılan 2 sn).
  * **Sessiz kalmaz**: her hata WARNING/ERROR loglanır.
  * **Secret loglanmaz**: header'da taşınır, hiçbir log satırına yazılmaz;
    URL de secret İÇERMEZ (bu yüzden erişim logu sızdırmaz).
  * **Yalnız AlgoPro**: ``resolve_tv_source`` "algopro" demediyse HİÇBİR ŞEY
    iletilmez (LuxAlgo/botv3/tv olayları takipçiye gitmez).
  * Kapalı yapılandırma (URL veya secret boş) = özellik KAPALI, bugünkü
    davranış birebir korunur.
"""

from __future__ import annotations

import asyncio
from typing import Optional, Set

import httpx

from src.core.config import settings
from src.core.logger import app_logger

# Takipçi uç noktasının gövde sınırı ile aynı (bkz. main.follower_event).
MAX_FORWARD_BODY_BYTES = 4096
SECRET_HEADER = "X-Follower-Secret"

# Task referansları GC'den korunmalı (asyncio yalnız zayıf referans tutar).
_background_tasks: Set[asyncio.Task] = set()


def forward_enabled() -> bool:
    """URL ve secret birlikte doluysa köprü açıktır."""
    url = str(getattr(settings, "follower_forward_url", "") or "").strip()
    secret = str(getattr(settings, "follower_forward_secret", "") or "").strip()
    return bool(url and secret)


def _timeout_seconds() -> float:
    try:
        value = float(
            getattr(settings, "follower_forward_timeout_seconds", 2.0) or 2.0
        )
    except (TypeError, ValueError):
        value = 2.0
    return value if value > 0 else 2.0


async def _post(url: str, secret: str, body: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=_timeout_seconds()) as client:
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
            app_logger.warning(
                f"⚠️ Takipçi köprüsü HTTP {response.status_code}: "
                f"{response.text[:200]}"
            )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        app_logger.warning(
            f"⚠️ Takipçi köprüsü iletemedi ({type(exc).__name__}: {exc}); "
            f"ana motor etkilenmedi"
        )


def maybe_forward_algopro_event(
    raw_body: str, source: str
) -> Optional[asyncio.Task]:
    """AlgoPro kaynaklı olayı takipçiye ilet (fire-and-forget).

    Dönüş: oluşturulan task (testler deterministik beklesin diye) ya da
    iletim yapılmadıysa ``None``. ASLA istisna yükseltmez.
    """
    try:
        if str(source or "").strip().lower() != "algopro":
            return None
        if not forward_enabled():
            return None

        body = str(raw_body or "")
        if not body.strip():
            return None
        if len(body.encode("utf-8")) > MAX_FORWARD_BODY_BYTES:
            app_logger.warning(
                "⚠️ Takipçi köprüsü: gövde 4KB sınırını aşıyor, iletilmedi"
            )
            return None

        url = str(getattr(settings, "follower_forward_url", "") or "").strip()
        secret = str(getattr(settings, "follower_forward_secret", "") or "").strip()

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Event loop yoksa (senkron bağlam) iletim yapılamaz — sessiz
            # kalmamak için loglanır.
            app_logger.warning("⚠️ Takipçi köprüsü: event loop yok, iletilmedi")
            return None

        task = loop.create_task(_post(url, secret, body))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
        return task
    except Exception as exc:  # savunmacı: köprü ana akışı ASLA düşürmez
        app_logger.warning(f"⚠️ Takipçi köprüsü kurulamadı ({exc})")
        return None
