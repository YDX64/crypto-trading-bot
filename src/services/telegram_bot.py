"""
Telegram bot servisi.
VIP kanallardan gelen sinyalleri yakalar ve orchestrator'a gönderir.
"""

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    CommandHandler,
    ContextTypes,
    filters
)

from src.services.orchestrator import TradingOrchestrator
from src.services.signal_queue import SignalQueue
from src.parsers.telegram_parser import TelegramSignalParser
from src.core.config import settings
from src.core.logger import app_logger
from src.core.database import AsyncSessionLocal, get_db


class TelegramBotService:
    """Telegram bot servisi"""
    
    def __init__(self, orchestrator: Optional[TradingOrchestrator] = None):
        """
        Args:
            orchestrator: Paylaşılan orchestrator. Verilmezse yeni bir tane
                oluşturulur. UYGULAMADA MUTLAKA PAYLAŞILAN ÖRNEK VERİLMELİDİR:
                iki ayrı orchestrator olursa sinyali işleyen örneğin izleme
                döngüsü çalışmaz, izleme döngüsü çalışan örneğin de pozisyonu
                olmaz — trailing stop ve break-even hiç devreye girmez.
        """
        self.token = settings.telegram_bot_token
        self.chat_id = settings.telegram_chat_id
        self.logger = app_logger
        self._owns_orchestrator = orchestrator is None
        self.orchestrator = orchestrator or TradingOrchestrator()
        self.signal_queue = SignalQueue()
        self.parser = TelegramSignalParser()
        self.app: Optional[Application] = None
        self.queue_task: Optional[asyncio.Task] = None
        self._lifecycle_lock = asyncio.Lock()
        self._started = False
        self._orchestrator_closed = False
        self._last_started_at: Optional[str] = None
        self._last_error: Optional[str] = None
        self._last_error_at: Optional[str] = None
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Bot başlatma komutu"""
        await update.message.reply_text(
            "🤖 **VIP Trading Bot Aktif**\n\n"
            "✅ Sinyaller otomatik işleniyor\n"
            "✅ AI analizi aktif\n"
            "✅ Trailing SL/TP aktif\n"
            "✅ Break-even mekanizması aktif\n\n"
            "Komutlar:\n"
            "/status - Durum\n"
            "/positions - Açık pozisyonlar\n"
            "/stats - İstatistikler",
            parse_mode="Markdown"
        )
    
    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Bot durumu"""
        active_count = len(self.orchestrator.active_positions)
        
        await update.message.reply_text(
            f"📊 **Bot Durumu**\n\n"
            f"🟢 Aktif\n"
            f"📈 Açık Pozisyon: {active_count}\n"
            f"💰 Hesap: {settings.account_balance} USDT\n"
            f"⚡ Risk: %{settings.risk_percentage}\n"
            f"🎯 İlk TP: %{settings.first_tp_percentage}\n"
            f"🔄 Trailing SL: %{settings.trailing_stop_percentage}\n"
            f"📊 Kontrol Aralığı: {settings.check_interval_seconds}s",
            parse_mode="Markdown"
        )
    
    async def positions_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Açık pozisyonları listele"""
        if not self.orchestrator.active_positions:
            await update.message.reply_text("📭 Açık pozisyon yok")
            return
        
        message = "📊 **Açık Pozisyonlar**\n\n"
        
        for symbol, position in self.orchestrator.active_positions.items():
            pnl_emoji = "📈" if position.pnl_percentage >= 0 else "📉"
            
            message += (
                f"**{symbol}**\n"
                f"└ Yön: {position.side.value}\n"
                f"└ Giriş: ${position.entry_price:.2f}\n"
                f"└ Güncel SL: ${position.current_stoploss:.2f}\n"
                f"└ Durum: {position.status.value}\n"
                f"└ P&L: {pnl_emoji} {position.pnl_percentage:.2f}%\n"
                f"└ Break-Even: {'✅' if position.is_break_even else '❌'}\n"
                f"└ Trailing: {'✅' if position.is_trailing else '❌'}\n\n"
            )
        
        await update.message.reply_text(message, parse_mode="Markdown")
    
    async def handle_channel_post(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Kanal mesajlarını işle"""
        message = update.channel_post.text if update.channel_post else None
        
        if not message:
            return
        
        self.logger.info(f"📨 Telegram mesajı alındı: {message[:100]}...")
        
        # 1. Profit mesajı kontrolü
        if self.parser.is_profit_message(message):
            self.logger.info("💰 Profit mesajı tespit edildi, atlanıyor")
            return
        
        # 2. Basit filtreleme (coin sembolü var mı?)
        if "/USDT" not in message.upper():
            self.logger.debug("Mesaj sinyal değil, atlanıyor")
            return
        
        # 3. Kuyruğa ekle (sıralı işlem için)
        await self.signal_queue.add_signal(message)
        
        queue_size = self.signal_queue.get_queue_size()
        await self.send_notification(
            f"📥 Sinyal kuyruğa eklendi\n"
            f"Sıradaki: {queue_size}\n"
            f"Aktif pozisyon: {len(self.orchestrator.active_positions)}/{settings.max_positions}"
        )
    
    async def handle_private_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Özel mesajları işle (manuel sinyal)"""
        message = update.message.text
        
        self.logger.info(f"📨 Manuel sinyal alındı: {message[:100]}...")
        
        # Profit mesajı kontrolü
        if self.parser.is_profit_message(message):
            await update.message.reply_text("💰 Profit mesajı - atlanıyor")
            return
        
        # Kuyruğa ekle
        await self.signal_queue.add_signal(message)
        
        await update.message.reply_text(
            f"📥 Sinyal kuyruğa eklendi\n"
            f"Sıra: {self.signal_queue.get_queue_size()}",
            parse_mode="Markdown"
        )
    
    async def send_notification(self, message: str):
        """Bildirim gönder"""
        try:
            await self.app.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode="Markdown"
            )
        except Exception as e:
            self.logger.error(f"Bildirim gönderme hatası: {e}")
    
    def build_app(self) -> Application:
        """Telegram application'ı oluştur"""
        self.app = Application.builder().token(self.token).build()
        
        # Command handlers
        self.app.add_handler(CommandHandler("start", self.start_command))
        self.app.add_handler(CommandHandler("status", self.status_command))
        self.app.add_handler(CommandHandler("positions", self.positions_command))
        
        # Message handlers
        self.app.add_handler(
            MessageHandler(
                filters.ChatType.CHANNEL,
                self.handle_channel_post
            )
        )
        
        self.app.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                self.handle_private_message
            )
        )
        
        self.logger.info("✅ Telegram application oluşturuldu")
        return self.app

    @property
    def is_running(self) -> bool:
        """Polling + Application + queue task'ının birlikte ayakta olmasını ister."""
        app_running = bool(self.app and self.app.running)
        updater = self.app.updater if self.app else None
        updater_running = bool(updater and updater.running)
        queue_running = bool(self.queue_task and not self.queue_task.done())
        return bool(self._started and app_running and updater_running and queue_running)

    @staticmethod
    def _task_exception(task: Optional[asyncio.Task]) -> Optional[str]:
        if task is None or not task.done() or task.cancelled():
            return None
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return None
        return f"{type(exc).__name__}: {exc}" if exc is not None else None

    def health_snapshot(self) -> Dict[str, Any]:
        """HTTP health endpoint'i için gerçek Telegram bileşen durumu."""
        updater = self.app.updater if self.app else None
        queue_alive = bool(self.queue_task and not self.queue_task.done())
        return {
            "healthy": self.is_running,
            "application_running": bool(self.app and self.app.running),
            "updater_running": bool(updater and updater.running),
            "queue_task_alive": queue_alive,
            "queue_task_done": bool(self.queue_task.done()) if self.queue_task else None,
            "queue_task_cancelled": (
                bool(self.queue_task.cancelled()) if self.queue_task else None
            ),
            "queue_task_exception": self._task_exception(self.queue_task),
            "last_started_at": self._last_started_at,
            "last_error": self._last_error,
            "last_error_at": self._last_error_at,
        }

    async def start(self) -> None:
        """Bot'u eksiksiz başlat; polling hazır olmadan bu metod dönmez."""
        async with self._lifecycle_lock:
            if self.is_running:
                self.logger.info("ℹ️ Telegram bot zaten çalışıyor")
                return

            # Önceki yarım kalmış başlatma denemesini temizle.
            if self.app is not None or self.queue_task is not None:
                await self._stop_components()

            app = self.build_app()
            self.logger.info("🚀 Telegram bot başlatılıyor...")
            try:
                await app.initialize()
                if app.updater is None:
                    raise RuntimeError("Telegram updater oluşturulmadı")
                await app.updater.start_polling()
                # python-telegram-bot'un run_polling yaşam döngüsüyle aynı
                # sıra: initialize -> updater polling -> application start.
                await app.start()

                self.queue_task = asyncio.create_task(
                    self.signal_queue.process_queue(self.orchestrator, get_db),
                    name="telegram-signal-queue",
                )
                # Queue coroutine'una başlama fırsatı ver; anında öldüyse
                # uygulamayı yanlışlıkla hazır ilan etme.
                await asyncio.sleep(0)
                if self.queue_task.done():
                    detail = self._task_exception(self.queue_task) or "beklenmeden tamamlandı"
                    raise RuntimeError(f"Telegram signal queue başlatılamadı: {detail}")

                self._started = True
                self._last_started_at = datetime.now(timezone.utc).isoformat()
                self._last_error = None
                self.logger.info("✅ Telegram polling ve signal queue çalışıyor")
            except asyncio.CancelledError:
                await self._stop_components()
                raise
            except Exception as e:
                self._last_error = f"{type(e).__name__}: {e}"
                self._last_error_at = datetime.now(timezone.utc).isoformat()
                self.logger.error(f"❌ Telegram bot başlatılamadı: {e}", exc_info=True)
                await self._stop_components()
                raise

    async def _stop_components(self) -> None:
        """Telegram'a ait bileşenleri ters sırada ve idempotent kapat."""
        self._started = False

        task = self.queue_task
        if task is not None:
            try:
                await self.signal_queue.stop()
            except Exception as e:
                self.logger.warning(f"Signal queue durdurma sinyali başarısız: {e}")
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.queue_task = None

        app = self.app
        if app is not None:
            updater = app.updater
            if updater is not None and updater.running:
                try:
                    await updater.stop()
                except Exception as e:
                    self.logger.warning(f"Telegram updater durdurulamadı: {e}")
            if app.running:
                try:
                    await app.stop()
                except Exception as e:
                    self.logger.warning(f"Telegram application durdurulamadı: {e}")
            try:
                await app.shutdown()
            except RuntimeError as e:
                # Hiç initialize edilmemiş / zaten shutdown edilmiş uygulama.
                self.logger.debug(f"Telegram application shutdown atlandı: {e}")
            except Exception as e:
                self.logger.warning(f"Telegram application kapatılamadı: {e}")
        self.app = None

    async def stop(self) -> None:
        """Bot'u idempotent durdur; paylaşılan orchestrator'a dokunma."""
        async with self._lifecycle_lock:
            await self._stop_components()

            # Yalnızca servis kendi orchestrator'ını oluşturduysa onun
            # yaşam döngüsünden sorumludur. FastAPI'de paylaşılan örnek
            # lifespan tarafından tam bir kez kapatılır.
            if self._owns_orchestrator and not self._orchestrator_closed:
                await self.orchestrator.close()
                self._orchestrator_closed = True

            self.logger.info("Telegram bot durduruldu")

