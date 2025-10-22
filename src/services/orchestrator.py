"""
Ana orkestrasyon modülü.
Tüm iş akışını koordine eder: Parse -> Analyze -> Trade -> Monitor
"""

import asyncio
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.signal import SignalParsed, SignalAnalyzed, SignalWithPosition, SignalModel, SignalStatus
from src.models.position import PositionModel, PositionStatus
from src.parsers.telegram_parser import TelegramSignalParser
from src.analyzers.ai_analyzer import AIAnalyzer
from src.trading.binance_client_improved import ImprovedBinanceClient as BinanceClient
from src.trading.position_manager import PositionManager
from src.core.config import settings
from src.core.logger import app_logger
from src.services.waiting_mode.monitor import WaitingModeMonitor


class TradingOrchestrator:
    """Trading bot orkestratörü"""
    
    def __init__(self):
        self.parser = TelegramSignalParser()
        self.analyzer = AIAnalyzer()
        self.binance = BinanceClient()
        self.position_manager = PositionManager(self.binance)
        self.waiting_monitor = WaitingModeMonitor(self.binance)  # Add waiting mode monitor
        self.logger = app_logger
        self.config = settings

        # Monitoring task
        self.monitoring_task: Optional[asyncio.Task] = None
        self.active_positions = {}  # symbol -> PositionModel
        self.waiting_signals = {}  # id -> WaitingSignalModel
    
    async def start(self):
        """Orchestrator'ı başlat"""
        self.logger.info("🎯 Trading Orchestrator başlatılıyor...")
        self.logger.info(f"📊 Max pozisyon sayısı: {self.config.max_positions}")
        self.logger.info(f"💰 Risk oranı: %{self.config.risk_percentage}")
        self.logger.info(f"🎯 İlk TP: %{self.config.first_tp_percentage}")
        self.logger.info(f"🔄 Trailing Stop: %{self.config.trailing_stop_percentage}")
        self.logger.info("✅ Orchestrator hazır ve sinyal bekliyor...")

        # Monitoring task'ı başlat
        if not self.monitoring_task:
            self.monitoring_task = asyncio.create_task(self._monitor_positions())

    async def stop(self):
        """Orchestrator'ı durdur"""
        self.logger.info("🛑 Trading Orchestrator durduruluyor...")
        if self.monitoring_task:
            self.monitoring_task.cancel()
            try:
                await self.monitoring_task
            except asyncio.CancelledError:
                pass
        self.logger.info("✅ Orchestrator durduruldu")

    async def _monitor_positions(self):
        """Açık pozisyonları sürekli kontrol et"""
        while True:
            try:
                if self.active_positions:
                    self.logger.debug(f"📊 {len(self.active_positions)} pozisyon izleniyor")
                    # Her pozisyon için trailing stop kontrolü
                    for symbol, position in self.active_positions.items():
                        # TODO: Trailing stop ve TP kontrolü
                        pass
                await asyncio.sleep(30)  # 30 saniyede bir kontrol
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"❌ Pozisyon monitoring hatası: {e}")
                await asyncio.sleep(10)

    async def process_signal(self, message: str, db_session: AsyncSession) -> Optional[PositionModel]:
        """
        Telegram sinyalini işle (tam akış).
        
        1. Parse signal
        2. AI analysis (3x)
        3. Trend alignment check
        4. Position calculation
        5. Open position
        6. Start monitoring
        """
        self.logger.info("=" * 80)
        self.logger.info("🚀 YENİ SİNYAL İŞLENİYOR")
        self.logger.info("=" * 80)
        
        # Max pozisyon kontrolü
        active_count = len(self.active_positions)
        if active_count >= self.config.max_positions:
            self.logger.warning(
                f"⚠️ Max pozisyon limitine ulaşıldı ({active_count}/{self.config.max_positions}). "
                f"Sinyal atlanıyor."
            )
            return None
        
        try:
            # 1. PARSE
            self.logger.info("ADIM 1: Sinyal parse ediliyor...")
            parsed_signal = self.parser.parse(message)
            
            if not parsed_signal.parsed:
                self.logger.warning(f"⚠️ Parse başarısız: {parsed_signal.error}")
                await self._save_signal(db_session, parsed_signal, SignalStatus.FAILED)
                return None
            
            await self._save_signal(db_session, parsed_signal, SignalStatus.PARSED)
            self.logger.info(f"✅ Parse başarılı: {parsed_signal.symbol} {parsed_signal.direction}")
            
            # 2. AI ANALYSIS
            self.logger.info("ADIM 2: AI analizi yapılıyor (3 perspektif)...")
            await self._update_signal_status(db_session, parsed_signal, SignalStatus.ANALYZING)
            
            analyzed_signal = await self.analyzer.analyze_signal(parsed_signal)
            
            self.logger.info(
                f"AI Sonuç: {analyzed_signal.ai_verdict} "
                f"({analyzed_signal.consensus})"
            )
            
            # 3. TREND ALIGNMENT
            self.logger.info("ADIM 3: Trend uyumluluğu kontrol ediliyor...")

            if not analyzed_signal.trend_aligned:
                # Check if waiting mode is enabled
                if self.config.waiting_mode_enabled:
                    self.logger.info(
                        f"🕐 Trend uyumsuz ama bekleme modu aktif! "
                        f"AI: {analyzed_signal.ai_verdict}, Sinyal: {parsed_signal.direction}"
                    )
                    self.logger.info("Sinyal bekleme moduna alınıyor...")

                    # First save the signal to get a database model
                    signal_model = SignalModel(
                        raw_message=parsed_signal.raw_message,
                        coin=parsed_signal.coin,
                        direction=parsed_signal.direction,
                        leverage=parsed_signal.leverage,
                        entry_min=parsed_signal.entry_min,
                        entry_max=parsed_signal.entry_max,
                        entry=parsed_signal.entry,
                        targets=parsed_signal.targets,
                        stoploss=parsed_signal.stoploss,
                        ai_verdict=analyzed_signal.ai_verdict,
                        trend_aligned=analyzed_signal.trend_aligned,
                        status=SignalStatus.WAITING
                    )
                    db_session.add(signal_model)
                    await db_session.commit()
                    await db_session.refresh(signal_model)

                    # Add to waiting queue
                    waiting_signal = await self.waiting_monitor.add_to_waiting_queue(
                        signal_model,
                        analyzed_signal.ai_verdict,
                        db_session
                    )

                    if waiting_signal:
                        self.logger.info(
                            f"✅ Sinyal bekleme kuyruğuna eklendi. "
                            f"Teknik indikatörlerle takip edilecek. ID: {waiting_signal.id}"
                        )
                    else:
                        self.logger.warning("⚠️ Sinyal bekleme kuyruğuna eklenemedi (limit dolmuş olabilir)")
                        await self._update_signal_status(db_session, parsed_signal, SignalStatus.REJECTED)

                    return None
                else:
                    self.logger.warning(
                        f"❌ Trend uyumsuz ve bekleme modu kapalı! "
                        f"AI: {analyzed_signal.ai_verdict}, Sinyal: {parsed_signal.direction}"
                    )
                    await self._update_signal_status(db_session, parsed_signal, SignalStatus.REJECTED)
                    return None
            
            self.logger.info("✅ Trend uyumlu! İşlem onaylandı.")
            await self._update_signal_status(db_session, parsed_signal, SignalStatus.APPROVED)
            
            # 4. POSITION CALCULATION
            self.logger.info("ADIM 4: Pozisyon hesaplanıyor...")
            signal_with_position = await self._calculate_position(analyzed_signal)
            
            self.logger.info(
                f"💰 Pozisyon: {signal_with_position.quantity} "
                f"(${signal_with_position.position_size:.2f})"
            )
            
            # 5. OPEN POSITION
            self.logger.info("ADIM 5: Pozisyon açılıyor...")
            position = await self.position_manager.open_position(signal_with_position)
            
            if not position:
                self.logger.error("❌ Pozisyon açılamadı!")
                await self._update_signal_status(db_session, parsed_signal, SignalStatus.FAILED)
                return None
            
            # Database'e kaydet
            db_session.add(position)
            await db_session.commit()
            await db_session.refresh(position)
            
            await self._update_signal_status(db_session, parsed_signal, SignalStatus.EXECUTED)
            
            # 6. START MONITORING
            self.logger.info("ADIM 6: Pozisyon takibi başlatılıyor...")
            self.active_positions[position.symbol] = position
            
            # Monitoring task başlat (eğer yoksa)
            if not self.monitoring_task or self.monitoring_task.done():
                self.monitoring_task = asyncio.create_task(
                    self._monitor_positions_loop(db_session)
                )
            
            self.logger.info("=" * 80)
            self.logger.info(f"✅ İŞLEM TAMAMLANDI: {position.symbol}")
            self.logger.info("=" * 80)
            
            return position
        
        except Exception as e:
            self.logger.error(f"❌ Sinyal işleme hatası: {e}", exc_info=True)
            return None
    
    async def _calculate_position(self, analyzed: SignalAnalyzed) -> SignalWithPosition:
        """Pozisyon büyüklüğünü hesapla (bakiyenin %10'u)"""
        signal = analyzed.signal
        
        # Bakiyeyi al
        balance = await self.binance.get_account_balance()
        
        if balance == 0:
            balance = self.config.account_balance
            self.logger.warning(f"Bakiye alınamadı, config'den kullanılıyor: {balance} USDT")
        
        # Risk hesapla
        risk_pct = self.config.risk_percentage / 100  # %10 = 0.10
        risk_amount = balance * risk_pct
        
        # Position size hesapla
        entry = signal.entry
        sl = signal.stoploss
        distance = abs(entry - sl)
        
        # Quantity hesapla
        position_size = risk_amount
        quantity = position_size / entry
        
        self.logger.debug(
            f"Hesaplama: Balance={balance}, Risk={risk_pct*100}%, "
            f"Amount={risk_amount}, Size={position_size}, Qty={quantity}"
        )
        
        return SignalWithPosition(
            signal=analyzed,
            quantity=quantity,
            position_size=position_size,
            risk_amount=risk_amount
        )
    
    async def _monitor_positions_loop(self, db_session: AsyncSession):
        """Aktif pozisyonları sürekli takip et"""
        self.logger.info("👁️ Pozisyon monitoring başladı")
        
        while self.active_positions:
            try:
                for symbol, position in list(self.active_positions.items()):
                    await self._monitor_single_position(position, db_session)
                
                # Interval kadar bekle
                await asyncio.sleep(self.config.check_interval_seconds)
            
            except Exception as e:
                self.logger.error(f"Monitoring loop hatası: {e}")
                await asyncio.sleep(5)
        
        self.logger.info("👁️ Pozisyon monitoring durdu (aktif pozisyon yok)")
    
    async def _monitor_single_position(
        self,
        position: PositionModel,
        db_session: AsyncSession
    ):
        """Tek bir pozisyonu takip et"""
        try:
            # Pozisyon hala açık mı?
            is_open = await self.position_manager.is_position_still_open(position)
            
            if not is_open:
                self.logger.info(f"🏁 Pozisyon kapandı: {position.symbol}")
                await self.position_manager.close_position_record(position)
                del self.active_positions[position.symbol]
                await db_session.commit()
                return
            
            # Durum kontrolü
            if position.status == PositionStatus.OPEN:
                # İlk TP vurdu mu?
                if await self.position_manager.check_first_tp_hit(position):
                    self.logger.info(f"🎯 İlk TP vurdu: {position.symbol}")
                    
                    # Break-even'e taşı
                    success = await self.position_manager.move_to_break_even(position)
                    
                    if success:
                        position.status = PositionStatus.BREAK_EVEN
                        await db_session.commit()
            
            elif position.status == PositionStatus.BREAK_EVEN:
                # Trailing modu aktif, ilk güncelleme
                position.status = PositionStatus.TRAILING
                await db_session.commit()
            
            elif position.status == PositionStatus.TRAILING:
                # Trailing stop güncelle
                await self.position_manager.update_trailing_stop(position)
                await db_session.commit()
        
        except Exception as e:
            self.logger.error(f"Pozisyon takip hatası [{position.symbol}]: {e}")
    
    async def _save_signal(
        self,
        db_session: AsyncSession,
        signal: SignalParsed,
        status: SignalStatus
    ):
        """Sinyali veritabanına kaydet"""
        signal_model = SignalModel(
            raw_message=signal.raw_message,
            coin=signal.coin,
            direction=signal.direction,
            leverage=signal.leverage,
            entry_min=signal.entry_min,
            entry_max=signal.entry_max,
            entry=signal.entry,
            targets=signal.targets,
            stoploss=signal.stoploss,
            status=status,
            error_message=signal.error
        )
        
        db_session.add(signal_model)
        await db_session.commit()
    
    async def _update_signal_status(
        self,
        db_session: AsyncSession,
        signal: SignalParsed,
        status: SignalStatus
    ):
        """Sinyal durumunu güncelle"""
        # Bu basitleştirilmiş versiyon, gerçekte signal_id ile update yapılmalı
        pass
    
    async def close(self):
        """Kaynakları temizle"""
        if self.monitoring_task and not self.monitoring_task.done():
            self.monitoring_task.cancel()
        
        await self.binance.close()
        self.logger.info("Orchestrator kapatıldı")

