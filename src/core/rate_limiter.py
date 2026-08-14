"""
Rate limiting sistemi.
API çağrıları arasında minimum bekleme süresi uygular.

2026-08-14: Kilitsiz check-then-act yarışı düzeltildi. Eski sürümde N
coroutine aynı anda `last_binance_call`'u okuyor, hepsi aynı beklemeyi
hesaplayıp paralel uyuyor ve SONRA hepsi aynı anda istek atıyordu —
delay eşzamanlılık altında hiç uygulanmıyordu. Sonuç: Binance -1003/418
IP ban döngüsü (bkz. 2026-08-14 sunucu kesintisi). asyncio.Lock ile her
bekleme-slotu atomik olarak rezerve edilir; global hız gerçekten
delay başına 1 istekle sınırlanır.
"""

import time
import asyncio
from src.core.config import settings
from src.core.logger import app_logger


class RateLimiter:
    """Rate limiter - API çağrıları arasında delay ekler (coroutine-güvenli)"""

    def __init__(self):
        self.last_openai_call = 0
        self.last_binance_call = 0
        self.logger = app_logger
        self.openai_delay = settings.openai_rate_limit_seconds
        self.binance_delay = settings.binance_rate_limit_seconds
        # Py>=3.10: Lock oluşturma anında event loop'a bağlanmaz; import
        # zamanında (module-level singleton) güvenle kurulabilir.
        self._openai_lock = asyncio.Lock()
        self._binance_lock = asyncio.Lock()

    async def wait_for_openai(self):
        """
        OpenAI API çağrısı öncesi bekle.
        Minimum delay: 3 saniye (rate limit koruması)
        """
        async with self._openai_lock:
            now = time.time()
            wait_time = self.last_openai_call + self.openai_delay - now
            if wait_time > 0:
                self.logger.debug(f"OpenAI rate limit: {wait_time:.2f}s bekleniyor...")
                await asyncio.sleep(wait_time)
            # Slot, kilit İÇİNDE rezerve edilir — sıradaki coroutine bizim
            # bıraktığımız zamana göre bekler; burst oluşamaz.
            self.last_openai_call = time.time()

    async def wait_for_binance(self):
        """
        Binance API çağrısı öncesi bekle.
        Minimum delay: 0.5 saniye (rate limit koruması)
        """
        async with self._binance_lock:
            now = time.time()
            wait_time = self.last_binance_call + self.binance_delay - now
            if wait_time > 0:
                await asyncio.sleep(wait_time)
            self.last_binance_call = time.time()


# Global rate limiter instance
rate_limiter = RateLimiter()
