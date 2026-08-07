"""
Evren tarayıcı — 24 saatlik hacme göre en likit USDT paritelerini seçer.

Tasarım ilkeleri:
- GET /fapi/v1/ticker/24hr PUBLIC bir endpoint'tir; imza/API anahtarı
  gerekmez, emir yeteneği taşımaz.
- Kaldıraçlı token'lar (BTCUPUSDT, ETHDOWNUSDT, ...) ve vadeli-tarihli/
  endeks sözleşmeleri (BTCDOMUSDT, sembol içinde '_' geçenler — örn.
  BTCUSDT_260327) evrenden çıkarılır; bunlar normal scalping için uygun
  değildir (düşük likidite, farklı fiyatlama dinamiği).
  DİKKAT: yalnızca gerçek kaldıraçlı token SONEKLERİNE (...UPUSDT,
  ...DOWNUSDT, ...BULLUSDT, ...BEARUSDT) bakılır — "SUPERUSDT" gibi meşru
  bir sembolün içinde "UP"/"DOWN" alt dizesi geçmesi YÜZÜNDEN yanlışlıkla
  elenmez.
- Sonuç refresh_seconds (varsayılan saatlik) önbelleğe alınır: 24hr ticker
  borsadaki TÜM sembolleri döndüren ağır bir çağrıdır, her taramada tekrar
  çekilmemeli.
- Hata durumunda ASLA sessiz boş liste dönmez: önbellek varsa ESKİSİ
  WARNING ile döner (evren tamamen boşalıp motorun durmasındansa bayat
  veriyle devam etmek tercih edilir); önbellek de yoksa (ör. ilk açılışta
  hemen ağ hatasına denk gelinirse) BTCUSDT/ETHUSDT fallback'i ERROR ile
  döner.
"""

from __future__ import annotations

import asyncio
import time
from typing import List, Optional, Tuple

import httpx

from src.core.config import settings
from src.core.logger import app_logger


_TICKER_ENDPOINT = "/fapi/v1/ticker/24hr"
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0  # saniye; deneme başına 2^n ile büyür (1s, 2s, 4s)

# Kaldıraçlı token soneki: gerçek sembolün SONUNDA olmalı, herhangi bir
# yerinde geçmesi yeterli değil (bkz. SUPERUSDT yanlış-pozitif riski).
_LEVERAGED_SUFFIXES = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")
_EXCLUDED_SYMBOLS = frozenset({"BTCDOMUSDT"})
_FALLBACK_UNIVERSE: Tuple[str, ...] = ("BTCUSDT", "ETHUSDT")


class UniverseScanner:
    """24s hacme göre ilk top_n USDT paritesini seçer, refresh_seconds'da bir
    yeniler."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        top_n: int = 12,
        refresh_seconds: int = 3600,
    ):
        self.base_url = base_url or settings.binance_base_url
        self.top_n = top_n
        self.refresh_seconds = refresh_seconds
        self.logger = app_logger

        # Kendi bağlantı havuzu: imzalı emir istemcisinden bağımsız.
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=10.0),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )

        self._cached_universe: Optional[List[str]] = None
        self._cached_at: float = 0.0
        self._lock = asyncio.Lock()

    @staticmethod
    def _is_eligible(symbol: str) -> bool:
        """USDT paritesi mi ve kaldıraçlı/vadeli-tarihli/endeks sözleşmesi
        DEĞİL mi?"""
        if not symbol.endswith("USDT"):
            return False
        if symbol in _EXCLUDED_SYMBOLS:
            return False
        if "_" in symbol:
            return False
        if symbol.endswith(_LEVERAGED_SUFFIXES):
            return False
        return True

    async def get_universe(self) -> List[str]:
        """Güncel (veya taze önbellekteki) top_n USDT sembol listesini,
        quoteVolume'a göre azalan sırada döndürür."""
        now = time.monotonic()
        if self._cached_universe is not None and (now - self._cached_at) < self.refresh_seconds:
            return self._cached_universe

        async with self._lock:
            # Kilidi beklerken başka bir görev doldurmuş olabilir
            now = time.monotonic()
            if self._cached_universe is not None and (now - self._cached_at) < self.refresh_seconds:
                return self._cached_universe

            try:
                universe = await self._fetch_universe()
                self._cached_universe = universe
                self._cached_at = time.monotonic()
                return universe
            except Exception as e:
                if self._cached_universe is not None:
                    self.logger.warning(
                        f"Evren yenilenemedi, önbellekteki liste kullanılıyor "
                        f"({len(self._cached_universe)} sembol): {e}"
                    )
                    return self._cached_universe
                self.logger.error(
                    f"Evren hiç çekilemedi, fallback kullanılıyor "
                    f"{list(_FALLBACK_UNIVERSE)}: {e}"
                )
                return list(_FALLBACK_UNIVERSE)

    async def _fetch_raw_tickers(self) -> list:
        last_error: Optional[Exception] = None
        for attempt in range(_MAX_RETRIES):
            try:
                response = await self._client.get(f"{self.base_url}{_TICKER_ENDPOINT}")
                response.raise_for_status()
                return response.json()

            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                last_error = e
                if attempt < _MAX_RETRIES - 1:
                    wait = _RETRY_BASE_DELAY * (2 ** attempt)
                    self.logger.warning(
                        f"24hr ticker çekme hatası, {wait}s sonra tekrar "
                        f"(deneme {attempt + 1}/{_MAX_RETRIES}): {e}"
                    )
                    await asyncio.sleep(wait)
                    continue
                self.logger.error(
                    f"24hr ticker çekme başarısız ({_MAX_RETRIES} deneme sonrası): {e}"
                )

        raise last_error or RuntimeError(
            f"24hr ticker {_MAX_RETRIES} denemeden sonra çekilemedi"
        )

    async def _fetch_universe(self) -> List[str]:
        raw = await self._fetch_raw_tickers()
        if not isinstance(raw, list):
            raise RuntimeError(f"Beklenmeyen 24hr ticker yanıtı tipi: {type(raw)}")

        eligible: List[Tuple[str, float]] = []
        for entry in raw:
            symbol = entry.get("symbol", "")
            if not self._is_eligible(symbol):
                continue
            try:
                quote_volume = float(entry.get("quoteVolume", 0.0))
            except (TypeError, ValueError):
                continue
            eligible.append((symbol, quote_volume))

        eligible.sort(key=lambda pair: pair[1], reverse=True)
        top = [symbol for symbol, _ in eligible[: self.top_n]]

        if not top:
            raise RuntimeError("Filtre sonrası evren boş kaldı (beklenmeyen ticker yanıtı)")

        return top

    async def close(self) -> None:
        await self._client.aclose()
