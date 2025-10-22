"""
Rate limiting sistemi.
API çağrıları arasında minimum bekleme süresi uygular.
"""

import time
import asyncio
from src.core.config import settings
from src.core.logger import app_logger


class RateLimiter:
    """Rate limiter - API çağrıları arasında delay ekler"""
    
    def __init__(self):
        self.last_openai_call = 0
        self.last_binance_call = 0
        self.logger = app_logger
        self.openai_delay = settings.openai_rate_limit_seconds
        self.binance_delay = settings.binance_rate_limit_seconds
    
    async def wait_for_openai(self):
        """
        OpenAI API çağrısı öncesi bekle.
        Minimum delay: 3 saniye (rate limit koruması)
        """
        elapsed = time.time() - self.last_openai_call
        if elapsed < self.openai_delay:
            wait_time = self.openai_delay - elapsed
            self.logger.debug(f"OpenAI rate limit: {wait_time:.2f}s bekleniyor...")
            await asyncio.sleep(wait_time)
        
        self.last_openai_call = time.time()
    
    async def wait_for_binance(self):
        """
        Binance API çağrısı öncesi bekle.
        Minimum delay: 0.5 saniye (rate limit koruması)
        """
        elapsed = time.time() - self.last_binance_call
        if elapsed < self.binance_delay:
            wait_time = self.binance_delay - elapsed
            await asyncio.sleep(wait_time)
        
        self.last_binance_call = time.time()


# Global rate limiter instance
rate_limiter = RateLimiter()

