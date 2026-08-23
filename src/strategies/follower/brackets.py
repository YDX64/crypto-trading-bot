"""Borsa kaldıraç dilimi önbelleği — `/fapi/v1/leverageBracket` (IO).

Kullanıcı kararı (2026-08-23): "borsanın sembol başına azami kaldıraç dilimi
(notional'a göre) aşılamaz — GERÇEK değeri oku, önbellekle".

Neden ayrı bir modül: ``ImprovedBinanceClient`` scalper halkasıyla PAYLAŞILAN
kritik bir dosyadır ve bu görevde davranışı byte-for-byte korunmalıdır; bu
yüzden yeni endpoint burada, motorun ``_submit_reduce_only_market_close``
desenindeki gibi ``_request_with_retry`` üzerinden çağrılır (aynı hız
sınırlayıcı, aynı retry, aynı ban devre kesici).

Önbellek TTL'i uzundur (varsayılan 6 saat): dilimler nadiren değişir ve
başarısız okuma girişi ENGELLER (fail-closed), o yüzden bayat veri değil,
veri YOKLUĞU risklidir.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Iterable, List, Tuple

from src.core.logger import app_logger
from src.strategies.follower.plan import parse_brackets
from src.strategies.follower.types import LeverageBracket

_ENDPOINT = "/fapi/v1/leverageBracket"


class LeverageBracketCache:
    """Sembol → kaldıraç dilimleri (TTL önbellekli, tek uçuş kilidi)."""

    def __init__(self, client: Any, cfg: Any):
        self.client = client
        self.cfg = cfg
        self.logger = app_logger
        self._cache: Dict[str, Tuple[List[LeverageBracket], float]] = {}
        self._lock = asyncio.Lock()

    def _ttl(self) -> float:
        try:
            ttl = float(
                getattr(self.cfg, "follower_bracket_cache_ttl_seconds", 21600.0)
            )
        except (TypeError, ValueError):
            ttl = 21600.0
        return ttl if ttl > 0 else 21600.0

    def cached(self, symbol: str) -> List[LeverageBracket]:
        """Yalnız RAM'deki (taze) kaydı döndür — ağa çıkmaz."""
        entry = self._cache.get(symbol.upper())
        if not entry:
            return []
        rows, cached_at = entry
        if (time.monotonic() - cached_at) >= self._ttl():
            return []
        return rows

    async def get(self, symbol: str) -> List[LeverageBracket]:
        """Dilimleri getir; okunamazsa BOŞ liste (çağıran fail-closed davranır).

        Bayat ama var olan bir önbellek kaydı, okuma hatasında KORUNUR: dilimler
        saatler içinde değişmez, ama "hiç veri yok" girişi tamamen kapatır.
        """
        key = symbol.upper()
        fresh = self.cached(key)
        if fresh:
            return fresh

        async with self._lock:
            fresh = self.cached(key)
            if fresh:
                return fresh
            try:
                payload = await self.client._request_with_retry(
                    "GET", _ENDPOINT, params={"symbol": key}, signed=True
                )
            except Exception as exc:
                stale = self._cache.get(key)
                if stale and stale[0]:
                    self.logger.warning(
                        f"⚠️ {key}: kaldıraç dilimi tazelenemedi ({exc}); "
                        f"bayat önbellek kullanılıyor"
                    )
                    return stale[0]
                self.logger.error(
                    f"❌ {key}: kaldıraç dilimi okunamadı ({exc}); giriş fail-closed"
                )
                return []

            rows = parse_brackets(payload)
            if not rows:
                self.logger.error(
                    f"❌ {key}: kaldıraç dilimi yanıtı çözülemedi; giriş fail-closed"
                )
                return []
            self._cache[key] = (rows, time.monotonic())
            self.logger.info(
                f"📐 {key}: kaldıraç dilimi okundu (azami {max(r.max_leverage for r in rows)}x, "
                f"{len(rows)} dilim)"
            )
            return rows

    async def warm(self, symbols: Iterable[str]) -> int:
        """Başlangıçta evreni önceden doldur; kaç sembol hazır olduğunu döner.

        Sıralı çağrı (paralel değil): Binance ağırlık bütçesi ve 418 disiplini
        (bkz. docs/RUNBOOK.md) — 8 sembol için tek seferlik maliyet.
        """
        ready = 0
        for symbol in symbols:
            try:
                if await self.get(symbol):
                    ready += 1
            except Exception as exc:  # savunmacı: warm asla start()'ı düşürmez
                self.logger.warning(f"⚠️ {symbol}: dilim ön yüklemesi hata verdi ({exc})")
        return ready

    def snapshot(self) -> Dict[str, Dict[str, Any]]:
        """/follower/status için özet (secret içermez)."""
        now = time.monotonic()
        out: Dict[str, Dict[str, Any]] = {}
        for symbol, (rows, cached_at) in sorted(self._cache.items()):
            if not rows:
                continue
            out[symbol] = {
                "max_leverage": max(r.max_leverage for r in rows),
                "brackets": len(rows),
                "age_seconds": round(now - cached_at, 1),
            }
        return out
