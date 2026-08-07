"""
Telegram bot servisi.
VIP kanallardan gelen sinyalleri yakalar ve orchestrator'a gönderir.
"""

import asyncio
from typing import Optional

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
        self.orchestrator = orchestrator or TradingOrchestrator()
        self.signal_queue = SignalQueue()
        self.parser = TelegramSignalParser()
        self.app = None
        self.queue_task = None
    
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
        
        self.logger.info("✅ Telegram bot hazır")
        return self.app
    
    async def start(self):
        """Bot'u başlat"""
        app = self.build_app()
        
        self.logger.info("🚀 Telegram bot başlatılıyor...")
        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        
        # Queue processor'ı başlat
        self.queue_task = asyncio.create_task(
            self.signal_queue.process_queue(self.orchestrator, get_db)
        )
        
        self.logger.info("✅ Telegram bot ve signal queue çalışıyor")
    
    async def stop(self):
        """Bot'u durdur"""
        # Queue'yu durdur
        if self.queue_task:
            await self.signal_queue.stop()
            self.queue_task.cancel()
            try:
                await self.queue_task
            except asyncio.CancelledError:
                pass
        
        # Telegram bot'u durdur
        if self.app:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()
        
        await self.orchestrator.close()
        
        self.logger.info("Bot durduruldu")

