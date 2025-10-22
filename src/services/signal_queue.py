"""
Sinyal kuyruğu yönetimi.
Çoklu sinyalleri sırayla işler, API rate limitlerini korur.
"""

import asyncio
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.logger import app_logger


class SignalQueue:
    """Sinyal kuyruğu - Sinyalleri sırayla işler"""
    
    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()
        self.processing = False
        self.logger = app_logger
        self.delay = settings.signal_queue_delay_seconds
    
    async def add_signal(self, message: str):
        """Kuyruğa sinyal ekle"""
        await self.queue.put(message)
        queue_size = self.queue.qsize()
        self.logger.info(f"📥 Sinyal kuyruğa eklendi (Queue: {queue_size})")
    
    def get_queue_size(self) -> int:
        """Kuyruk boyutunu döndür"""
        return self.queue.qsize()
    
    async def process_queue(self, orchestrator, get_db_session):
        """
        Kuyruktaki sinyalleri sırayla işle.
        
        Args:
            orchestrator: TradingOrchestrator instance
            get_db_session: AsyncSession generator
        """
        self.processing = True
        self.logger.info("🔄 Signal queue processor başlatıldı")
        
        while self.processing:
            try:
                # Kuyruktan sinyal al (timeout ile)
                try:
                    message = await asyncio.wait_for(
                        self.queue.get(),
                        timeout=5.0
                    )
                except asyncio.TimeoutError:
                    # Timeout - döngüye devam et
                    continue
                
                self.logger.info(
                    f"⚙️ Sinyal işleniyor... (Kuyrukta {self.queue.qsize()} kaldı)"
                )
                
                # Rate limiting delay
                if self.delay > 0:
                    await asyncio.sleep(self.delay)
                
                # Database session oluştur ve işle
                async for db_session in get_db_session():
                    try:
                        position = await orchestrator.process_signal(
                            message,
                            db_session
                        )
                        
                        if position:
                            self.logger.info(
                                f"✅ Sinyal işlendi: {position.symbol}",
                                extra={"trade": True}
                            )
                        else:
                            self.logger.info("⚠️ Sinyal reddedildi veya atlandı")
                        
                        break  # Session'dan çık
                    
                    except Exception as e:
                        self.logger.error(
                            f"❌ Sinyal işleme hatası: {e}",
                            exc_info=True
                        )
                        break
                
                # Task tamamlandı
                self.queue.task_done()
            
            except Exception as e:
                self.logger.error(f"Queue processor hatası: {e}", exc_info=True)
                await asyncio.sleep(1)
        
        self.logger.info("🛑 Signal queue processor durdu")
    
    async def stop(self):
        """Queue processor'ı durdur"""
        self.processing = False
        self.logger.info("Signal queue durdurma sinyali gönderildi")
    
    async def wait_empty(self):
        """Kuyruğun boşalmasını bekle"""
        await self.queue.join()

