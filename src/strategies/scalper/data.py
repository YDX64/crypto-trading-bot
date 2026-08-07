"""
Kline (mum) verisi çekme modülü — Scalper motoru için public Binance Futures
klines endpoint'i üzerinden veri sağlar.

Tasarım ilkeleri:
- İmza/API anahtarı GEREKMEZ: /fapi/v1/klines herkese açık bir endpoint'tir,
  emir yeteneği taşımaz. Bu yüzden ImprovedBinanceClient'ın imzalı istek
  altyapısını yeniden kullanmak yerine kendi hafif httpx.AsyncClient'ını
  kurar (ayrı bağlantı havuzu, ayrı timeout — canlı emir istemcisiyle
  kaynak paylaşmaz).
- base_url verilmezse settings.binance_base_url kullanılır: canlı motor
  testnet'teyse kline verisi de testnet'ten gelir (fiyat/likidite canlı
  motorla tutarlı kalır). Backtest tarihsel derinlik istediğinde açıkça
  https://fapi.binance.com geçebilir — public veri, imza/API anahtarı
  gerekmediği ve emir yeteneği taşımadığı için bu güvenlidir.
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
from typing import Dict, List, Optional, Tuple

import httpx

from src.core.config import settings
from src.core.logger import app_logger
from src.strategies.scalper.types import Candle


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


class KlineFetcher:
    """Public /fapi/v1/klines üzerinden mum verisi çeker ve kısa süreli
    TTL önbelleğiyle tekrar isteği önler."""

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = base_url or settings.binance_base_url
        self.logger = app_logger

        # Kendi bağlantı havuzu: imzalı emir istemcisinden (ImprovedBinanceClient)
        # bağımsız — public veri çekimi emir akışını asla bloklamamalı.
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=10.0),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )

        # (symbol, interval, limit) -> (mumlar, önbelleğe_alma_zamanı_monotonic)
        self._cache: Dict[Tuple[str, str, int], Tuple[List[Candle], float]] = {}
        self._cache_lock = asyncio.Lock()

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

        async with self._cache_lock:
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

        last_error: Optional[Exception] = None
        for attempt in range(_MAX_RETRIES):
            try:
                response = await self._client.get(
                    f"{self.base_url}{_KLINES_ENDPOINT}", params=params
                )
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
                    await asyncio.sleep(wait)
                    continue
                self.logger.error(
                    f"Kline çekme başarısız ({symbol} {interval}, "
                    f"{_MAX_RETRIES} deneme sonrası): {e}"
                )

        raise last_error or RuntimeError(
            f"Kline çekme {_MAX_RETRIES} denemeden sonra başarısız: "
            f"{symbol} {interval}"
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
