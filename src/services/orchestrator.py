"""
Ana orkestrasyon modülü.
Tüm iş akışını koordine eder: Parse -> Analyze -> Trade -> Monitor

TASARIM NOTLARI:
- TEK bir izleme döngüsü vardır ve kendi veritabanı oturumlarını açar.
  (Eskiden iki döngü vardı; başlangıçta kurulan boş döngü, gerçek izleme
  döngüsünün hiç başlamamasına yol açıyordu.)
- Açık pozisyonlar bellekte tutulur ama başlangıçta BORSADAN kurtarılır;
  bot yeniden başladığında açık pozisyonlar sahipsiz kalmaz.
- Bakiye okunamazsa işlem AÇILMAZ. Sahte bir config bakiyesiyle emir
  göndermek gerçek parayla oynamaktır.
"""

import asyncio
import json
import time
from typing import Optional, Dict
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.signal import (
    SignalParsed, SignalAnalyzed, SignalWithPosition,
    SignalModel, SignalStatus,
)
from src.models.position import PositionModel, PositionStatus, PositionSide
from src.parsers.telegram_parser import TelegramSignalParser
from src.analyzers.ai_analyzer import AIAnalyzer
from src.trading.binance_client_improved import (
    ImprovedBinanceClient as BinanceClient,
    BinanceAPIError,
)
from src.trading.position_manager import PositionManager, UnprotectedPositionError
from src.trading.symbol_reservations import symbol_reservations
from src.core.config import settings
from src.core.database import AsyncSessionLocal
from src.core.logger import app_logger
from src.services.waiting_mode.monitor import WaitingModeMonitor


class TradingOrchestrator:
    """Trading bot orkestratörü"""

    # Pozisyon açılırken kullanılabilir marjın en fazla bu oranı bağlanır
    MAX_MARGIN_UTILISATION = 0.5
    RESERVATION_OWNER = "telegram"
    EXCHANGE_PROBE_INTERVAL_SECONDS = 60.0

    def __init__(self):
        self.parser = TelegramSignalParser()
        self.analyzer = AIAnalyzer()
        self.binance = BinanceClient()
        self.position_manager = PositionManager(self.binance)
        self.waiting_monitor = WaitingModeMonitor(self.binance)
        self.logger = app_logger
        self.config = settings

        self.monitoring_task: Optional[asyncio.Task] = None
        self.active_positions: Dict[str, PositionModel] = {}
        self.waiting_signals = {}

        # Sinyal işleme serileştirilir: max_positions kontrolü ile pozisyon
        # açma arasına başka bir sinyal giremez (TOCTOU koruması).
        self._signal_lock = asyncio.Lock()
        self._running = False
        self._exchange_ready = False
        self._recovery_ready = False
        self._last_exchange_probe_monotonic: Optional[float] = None
        self._last_exchange_success_at: Optional[str] = None
        self._last_exchange_error: Optional[str] = None
        self._entry_halted = False
        self._entry_halt_reason: Optional[str] = None
        self._last_monitoring_success_monotonic: Optional[float] = None
        self._last_monitoring_success_at: Optional[str] = None

    # ------------------------------------------------------------------
    # Yaşam döngüsü
    # ------------------------------------------------------------------

    async def start(self):
        """Orchestrator'ı başlat"""
        self.logger.info("🎯 Trading Orchestrator başlatılıyor...")
        self.logger.info(f"📊 Max pozisyon sayısı: {self.config.max_positions}")
        self.logger.info(f"💰 Risk oranı: %{self.config.risk_percentage}")
        self.logger.info(f"🎯 İlk TP: %{self.config.first_tp_percentage}")
        self.logger.info(f"🔄 Trailing Stop: %{self.config.trailing_stop_percentage}")

        if not await self.binance.test_connection():
            self.logger.error(
                "❌ Binance bağlantısı kurulamadı! Orchestrator sinyal kabul etmeyecek."
            )

        self._recovery_ready = await self.recover_open_positions()
        self._exchange_ready = self._recovery_ready and self._exchange_ready

        self._running = True
        if not self.monitoring_task or self.monitoring_task.done():
            self.monitoring_task = asyncio.create_task(self._monitoring_loop())

        self.logger.info("✅ Orchestrator hazır ve sinyal bekliyor...")

    async def stop(self):
        """Orchestrator'ı durdur"""
        self.logger.info("🛑 Trading Orchestrator durduruluyor...")
        self._running = False
        if self.monitoring_task:
            self.monitoring_task.cancel()
            try:
                await self.monitoring_task
            except asyncio.CancelledError:
                pass
        self.logger.info("✅ Orchestrator durduruldu")

    async def close(self):
        """Kaynakları temizle"""
        await self.stop()
        await self.binance.close()
        self.logger.info("Orchestrator kapatıldı")

    # ------------------------------------------------------------------
    # Yeniden başlatma sonrası kurtarma
    # ------------------------------------------------------------------

    async def recover_open_positions(self) -> bool:
        """Borsadaki açık pozisyonları belleğe ve veritabanına geri yükle.

        Bot yeniden başladığında active_positions boştur. Borsada gerçekten
        açık pozisyonlar varsa bunlar izlenmezse trailing/break-even hiç
        çalışmaz. Bu yüzden gerçek kaynak (borsa) esas alınır.
        """
        try:
            exchange_positions = await self.binance.get_all_positions()
        except Exception as e:
            self.logger.error(f"⚠️ Açık pozisyonlar borsadan sorgulanamadı: {e}")
            self._exchange_ready = False
            self._last_exchange_error = f"{type(e).__name__}: {e}"
            return False

        self._exchange_ready = True
        self._last_exchange_probe_monotonic = time.monotonic()
        self._last_exchange_success_at = datetime.utcnow().isoformat() + "Z"
        self._last_exchange_error = None

        if not exchange_positions:
            self.logger.info("📭 Borsada açık pozisyon yok")
            return True

        self.logger.warning(
            f"🔁 Borsada {len(exchange_positions)} açık pozisyon bulundu, kurtarılıyor..."
        )

        from src.models.scalp_trade import ScalpTradeModel

        recovery_ok = True
        async with AsyncSessionLocal() as session:
            for raw in exchange_positions:
                symbol = raw["symbol"]
                amt = float(raw["positionAmt"])
                side = PositionSide.LONG if amt > 0 else PositionSide.SHORT

                # TEK POZİSYON = TEK YÖNETİCİ: scalper'ın açık kaydı varsa bu
                # pozisyonu scalper ExitManager yönetir; orchestrator'ın da
                # benimsemesi çifte SL/TP müdahalesine yol açar.
                scalp_row = (await session.execute(
                    select(ScalpTradeModel.id)
                    .where(ScalpTradeModel.symbol == symbol)
                    .where(ScalpTradeModel.status == "OPEN")
                )).first()
                if scalp_row:
                    self.logger.info(
                        f"  ⏭️ {symbol}: scalper yönetiyor (scalp_trades #{scalp_row[0]}), atlanıyor"
                    )
                    continue

                if not symbol_reservations.reserve(symbol, self.RESERVATION_OWNER):
                    recovery_ok = False
                    self.logger.critical(
                        f"🚨 {symbol}: başka bir motor tarafından sahiplenilmiş; "
                        "çifte pozisyon yönetimini önlemek için orchestrator atladı.",
                        extra={"trade": True},
                    )
                    continue

                # Önce veritabanında bu sembole ait açık kayıt var mı?
                result = await session.execute(
                    select(PositionModel)
                    .where(PositionModel.symbol == symbol)
                    .where(PositionModel.status != PositionStatus.CLOSED)
                    .order_by(PositionModel.opened_at.desc())
                )
                position = result.scalars().first()

                if position:
                    # Kayıt var — borsadaki gerçek değerlerle senkronla
                    position.quantity = abs(amt)
                    position.entry_price = float(raw.get("entryPrice") or position.entry_price)
                    self.logger.info(
                        f"  ↩️ {symbol}: veritabanı kaydı bulundu (#{position.id}), senkronlandı"
                    )
                else:
                    # Kayıt yok — muhtemelen eski bir çökmeden kalan yetim pozisyon
                    entry = float(raw.get("entryPrice") or 0)
                    position = PositionModel(
                        symbol=symbol,
                        side=side,
                        leverage=int(float(raw.get("leverage") or self.config.max_leverage)),
                        margin_type=self.config.margin_type,
                        entry_price=entry,
                        current_price=entry,
                        quantity=abs(amt),
                        position_size=abs(amt) * entry,
                        initial_stoploss=0.0,
                        current_stoploss=0.0,
                        first_tp_price=0.0,
                        first_tp_quantity=0.0,
                        targets="[]",
                        status=PositionStatus.OPEN,
                        highest_price=entry,
                        lowest_price=entry,
                        trailing_stop_distance=self.config.trailing_stop_percentage,
                        trailing_profit_distance=self.config.trailing_profit_percentage,
                        opened_at=datetime.utcnow(),
                        notes="Kurtarıldı: borsada açıktı, veritabanında kaydı yoktu",
                    )
                    session.add(position)
                    self.logger.warning(
                        f"  ⚠️ {symbol}: YETİM pozisyon kurtarıldı ({abs(amt)} @ {entry}). "
                        f"Stop-loss durumu kontrol edilmeli!"
                    )

                try:
                    protection = await self._ensure_protected(position)
                except UnprotectedPositionError as e:
                    recovery_ok = False
                    self._entry_halted = True
                    self._entry_halt_reason = str(e)
                    self.active_positions[symbol] = position
                    self.logger.critical(
                        f"🚨 {symbol}: kurtarılan pozisyon korunamadı; yeni Telegram "
                        f"girişleri durduruldu ({e})",
                        extra={"trade": True},
                    )
                    continue

                if protection is False:
                    symbol_reservations.release(symbol, self.RESERVATION_OWNER)
                    continue
                if protection is None:
                    recovery_ok = False
                self.active_positions[symbol] = position

            await session.commit()

        self.logger.info(f"✅ {len(self.active_positions)} pozisyon izlemeye alındı")
        return recovery_ok

    async def _ensure_protected(self, position: PositionModel) -> Optional[bool]:
        """Pozisyonun borsada aktif bir stop emri var mı, kontrol et ve uyar.

        Koşullu emirler /fapi/v1/openOrders'ta görünmediği için algo emir
        listesi sorgulanır; aksi halde her pozisyon "korumasız" görünürdü.
        """
        try:
            algo_orders = await self.binance.get_open_algo_orders(position.symbol)
        except Exception as e:
            self.logger.warning(f"{position.symbol}: koşullu emirler kontrol edilemedi: {e}")
            return None

        stops = [o for o in algo_orders if o.get("orderType") in ("STOP_MARKET", "STOP")]
        if stops:
            position.sl_order_id = str(stops[0]["algoId"])
            position.current_stoploss = float(stops[0].get("triggerPrice") or 0)
            self.logger.info(
                f"  🛡️ {position.symbol}: aktif SL bulundu @ {position.current_stoploss}"
            )
            return True
        else:
            self.logger.critical(
                f"🚨 {position.symbol}: AÇIK POZİSYONUN STOP-LOSS EMRİ YOK; "
                "fail-closed acil kapatma deneniyor.",
                extra={"trade": True},
            )
            if await self.position_manager.emergency_close(position.symbol):
                position.status = PositionStatus.CLOSED
                position.closed_at = datetime.utcnow()
                position.notes = ((position.notes or "") + " | STOP yoktu; recovery acil kapattı").strip()
                self.logger.critical(
                    f"🛑 {position.symbol}: STOP'suz kurtarılan pozisyon acil kapatıldı",
                    extra={"trade": True},
                )
                return False
            raise UnprotectedPositionError(
                f"{position.symbol}: STOP yok ve acil kapatma başarısız"
            )

    # ------------------------------------------------------------------
    # İzleme döngüsü
    # ------------------------------------------------------------------

    async def _monitoring_loop(self):
        """Tek ve gerçek izleme döngüsü — kendi DB oturumunu yönetir."""
        self.logger.info("👁️ Pozisyon izleme döngüsü başladı")

        while self._running:
            try:
                await self._refresh_exchange_readiness_if_due()
                if self.active_positions:
                    async with AsyncSessionLocal() as session:
                        for symbol in list(self.active_positions.keys()):
                            position = self.active_positions.get(symbol)
                            if position is None:
                                continue
                            await self._monitor_single_position(position, session)
                        await session.commit()

                self._last_monitoring_success_monotonic = time.monotonic()
                self._last_monitoring_success_at = datetime.utcnow().isoformat() + "Z"
                await asyncio.sleep(self.config.check_interval_seconds)

            except asyncio.CancelledError:
                self.logger.info("👁️ İzleme döngüsü durduruldu")
                raise
            except Exception as e:
                self.logger.error(f"❌ İzleme döngüsü hatası: {e}", exc_info=True)
                await asyncio.sleep(10)

    async def _refresh_exchange_readiness_if_due(self) -> None:
        """Periodically prove that signed Futures account access still works."""

        now = time.monotonic()
        if (
            self._last_exchange_probe_monotonic is not None
            and now - self._last_exchange_probe_monotonic
            < self.EXCHANGE_PROBE_INTERVAL_SECONDS
        ):
            return

        self._last_exchange_probe_monotonic = now
        try:
            await self.binance.get_all_positions()
        except Exception as e:
            self._exchange_ready = False
            self._last_exchange_error = f"{type(e).__name__}: {e}"
            self.logger.error(
                f"❌ Signed Binance readiness probe başarısız; yeni girişler kapalı: {e}"
            )
            return

        self._exchange_ready = True
        self._last_exchange_success_at = datetime.utcnow().isoformat() + "Z"
        self._last_exchange_error = None

    async def _monitor_single_position(
        self, position: PositionModel, db_session: AsyncSession
    ):
        """Tek bir pozisyonu takip et."""
        try:
            is_open = await self.position_manager.is_position_still_open(position)

            if is_open is None:
                # Durum BİLİNMİYOR (API hatası). Kapandı VARSAYMA — izlemeye devam.
                return

            if is_open is False:
                self.logger.info(f"🏁 Pozisyon kapandı: {position.symbol}")
                await self.position_manager.close_position_record(position)
                self.active_positions.pop(position.symbol, None)
                symbol_reservations.release(position.symbol, self.RESERVATION_OWNER)
                if position not in db_session:
                    db_session.add(position)
                return

            if position.status == PositionStatus.OPEN:
                if await self.position_manager.check_first_tp_hit(position):
                    self.logger.info(f"🎯 İlk TP vurdu: {position.symbol}")
                    if await self.position_manager.move_to_break_even(position):
                        position.status = PositionStatus.BREAK_EVEN

            elif position.status == PositionStatus.BREAK_EVEN:
                position.status = PositionStatus.TRAILING
                await self.position_manager.update_trailing_stop(position)

            elif position.status == PositionStatus.TRAILING:
                await self.position_manager.update_trailing_stop(position)

            if position not in db_session:
                db_session.add(position)

        except UnprotectedPositionError as e:
            self._entry_halted = True
            self._entry_halt_reason = f"{type(e).__name__}: {e}"
            self.logger.critical(
                f"🚨 Pozisyon izleme koruma hatası [{position.symbol}]: {e}. "
                "Yeni Telegram girişleri durduruldu.",
                extra={"trade": True},
            )
            raise
        except Exception as e:
            self.logger.error(f"Pozisyon takip hatası [{position.symbol}]: {e}")

    # ------------------------------------------------------------------
    # Sinyal işleme
    # ------------------------------------------------------------------

    async def process_signal(
        self, message: str, db_session: AsyncSession
    ) -> Optional[PositionModel]:
        """Telegram sinyalini işle (tam akış)."""
        async with self._signal_lock:
            return await self._process_signal_locked(message, db_session)

    async def _process_signal_locked(
        self, message: str, db_session: AsyncSession
    ) -> Optional[PositionModel]:
        self.logger.info("=" * 80)
        self.logger.info("🚀 YENİ SİNYAL İŞLENİYOR")
        self.logger.info("=" * 80)

        if not self._exchange_ready or not self._recovery_ready or self._entry_halted:
            reason = self._entry_halt_reason or self._last_exchange_error or "exchange/recovery not ready"
            self.logger.error(f"⛔ Yeni Telegram girişi güvenlik nedeniyle kapalı: {reason}")
            return None

        active_count = len(symbol_reservations.snapshot())
        if active_count >= self.config.max_positions:
            self.logger.warning(
                f"⚠️ Max pozisyon limitine ulaşıldı ({active_count}/"
                f"{self.config.max_positions}). Sinyal atlanıyor."
            )
            return None

        signal_model: Optional[SignalModel] = None
        reserved_symbol: Optional[str] = None
        keep_reservation = False
        try:
            # 1. PARSE
            self.logger.info("ADIM 1: Sinyal parse ediliyor...")
            parsed_signal = self.parser.parse(message)

            signal_model = await self._save_signal(
                db_session, parsed_signal,
                SignalStatus.PARSED if parsed_signal.parsed else SignalStatus.FAILED,
            )

            if not parsed_signal.parsed:
                self.logger.warning(f"⚠️ Parse başarısız: {parsed_signal.error}")
                return None

            self.logger.info(
                f"✅ Parse başarılı: {parsed_signal.symbol} {parsed_signal.direction}"
            )

            if self.active_positions.get(parsed_signal.symbol):
                self.logger.warning(
                    f"⚠️ {parsed_signal.symbol} için zaten açık pozisyon var, sinyal atlanıyor."
                )
                await self._set_status(db_session, signal_model, SignalStatus.REJECTED)
                return None

            # 2. AI ANALİZ
            self.logger.info("ADIM 2: AI analizi yapılıyor (3 perspektif)...")
            await self._set_status(db_session, signal_model, SignalStatus.ANALYZING)

            analyzed_signal = await self.analyzer.analyze_signal(parsed_signal)
            self.logger.info(
                f"AI Sonuç: {analyzed_signal.ai_verdict} ({analyzed_signal.consensus})"
            )

            # 3. TREND UYUMU
            self.logger.info("ADIM 3: Trend uyumluluğu kontrol ediliyor...")
            if not analyzed_signal.trend_aligned:
                return await self._handle_unaligned(
                    parsed_signal, analyzed_signal, signal_model, db_session
                )

            self.logger.info("✅ Trend uyumlu! İşlem onaylandı.")
            await self._set_status(db_session, signal_model, SignalStatus.APPROVED)

            # 4. POZİSYON HESABI
            self.logger.info("ADIM 4: Pozisyon hesaplanıyor...")
            signal_with_position = await self._calculate_position(analyzed_signal)
            if signal_with_position is None:
                await self._set_status(db_session, signal_model, SignalStatus.FAILED)
                return None

            self.logger.info(
                f"💰 Pozisyon: {signal_with_position.quantity} "
                f"(${signal_with_position.position_size:.2f})"
            )

            # 5. POZİSYON AÇ
            self.logger.info("ADIM 5: Pozisyon açılıyor...")
            try:
                exchange_positions = await self.binance.get_all_positions()
            except Exception as e:
                self._exchange_ready = False
                self._last_exchange_error = f"{type(e).__name__}: {e}"
                self.logger.error(
                    f"⛔ Hesap pozisyonları doğrulanamadı; giriş fail-closed reddedildi: {e}"
                )
                await self._set_status(db_session, signal_model, SignalStatus.FAILED)
                return None

            live_symbols = {
                str(raw.get("symbol", "")).upper()
                for raw in exchange_positions
                if float(raw.get("positionAmt", 0) or 0) != 0
            }
            symbol = str(parsed_signal.symbol or "").upper()
            if symbol in live_symbols:
                self.logger.warning(f"⚠️ {symbol}: borsada zaten açık pozisyon var, giriş reddedildi")
                await self._set_status(db_session, signal_model, SignalStatus.REJECTED)
                return None
            if not symbol_reservations.reserve(
                symbol,
                self.RESERVATION_OWNER,
                capacity=self.config.max_positions,
                exchange_symbols=live_symbols,
            ):
                owner = symbol_reservations.owner(symbol)
                self.logger.warning(
                    f"⚠️ {symbol}: sembol başka motorun yönetiminde veya hesap kapasitesi dolu "
                    f"(owner={owner or 'capacity'}); giriş reddedildi"
                )
                await self._set_status(db_session, signal_model, SignalStatus.REJECTED)
                return None
            reserved_symbol = symbol

            position = await self.position_manager.open_position(signal_with_position)

            if not position:
                self.logger.error("❌ Pozisyon açılamadı!")
                await self._set_status(db_session, signal_model, SignalStatus.FAILED)
                return None

            if signal_model is not None:
                position.signal_id = signal_model.id

            db_session.add(position)
            await db_session.commit()
            await db_session.refresh(position)

            await self._set_status(db_session, signal_model, SignalStatus.EXECUTED)

            # 6. İZLEMEYE AL
            self.logger.info("ADIM 6: Pozisyon takibi başlatılıyor...")
            self.active_positions[position.symbol] = position
            keep_reservation = True

            self.logger.info("=" * 80)
            self.logger.info(f"✅ İŞLEM TAMAMLANDI: {position.symbol}")
            self.logger.info("=" * 80)
            return position

        except UnprotectedPositionError as e:
            # Bu istisna asla yutulmamalı — insan müdahalesi gerekiyor
            self.logger.critical(f"🚨 KORUMASIZ POZİSYON: {e}", extra={"trade": True})
            if signal_model is not None:
                await self._set_status(db_session, signal_model, SignalStatus.FAILED)
            self._entry_halted = True
            self._entry_halt_reason = f"{type(e).__name__}: {e}"
            keep_reservation = reserved_symbol is not None
            raise

        except Exception as e:
            self.logger.error(f"❌ Sinyal işleme hatası: {e}", exc_info=True)
            if signal_model is not None:
                try:
                    await self._set_status(db_session, signal_model, SignalStatus.FAILED)
                except Exception:
                    pass
            return None
        finally:
            if reserved_symbol and not keep_reservation:
                symbol_reservations.release(reserved_symbol, self.RESERVATION_OWNER)

    def health_snapshot(self) -> Dict[str, object]:
        task_alive = bool(self.monitoring_task and not self.monitoring_task.done())
        success_age = (
            max(0.0, time.monotonic() - self._last_monitoring_success_monotonic)
            if self._last_monitoring_success_monotonic is not None
            else None
        )
        freshness_limit = max(
            180.0, float(self.config.check_interval_seconds) * 5.0
        )
        fresh = bool(success_age is not None and success_age <= freshness_limit)
        return {
            "healthy": bool(
                self._running
                and task_alive
                and fresh
                and self._exchange_ready
                and self._recovery_ready
                and not self._entry_halted
            ),
            "running": self._running,
            "monitoring_task_alive": task_alive,
            "monitoring_fresh": fresh,
            "last_monitoring_success_at": self._last_monitoring_success_at,
            "last_monitoring_success_age_seconds": (
                round(success_age, 3) if success_age is not None else None
            ),
            "exchange_ready": self._exchange_ready,
            "recovery_ready": self._recovery_ready,
            "entry_halted": self._entry_halted,
            "entry_halt_reason": self._entry_halt_reason,
            "last_exchange_success_at": self._last_exchange_success_at,
            "last_exchange_error": self._last_exchange_error,
            "reservations": symbol_reservations.snapshot(),
        }

    async def _handle_unaligned(
        self,
        parsed_signal: SignalParsed,
        analyzed_signal: SignalAnalyzed,
        signal_model: Optional[SignalModel],
        db_session: AsyncSession,
    ) -> None:
        """Trend uyumsuz sinyali bekleme kuyruğuna al ya da reddet."""
        if not self.config.waiting_mode_enabled:
            self.logger.warning(
                f"❌ Trend uyumsuz ve bekleme modu kapalı! "
                f"AI: {analyzed_signal.ai_verdict}, Sinyal: {parsed_signal.direction}"
            )
            await self._set_status(db_session, signal_model, SignalStatus.REJECTED)
            return None

        self.logger.info(
            f"🕐 Trend uyumsuz ama bekleme modu aktif! "
            f"AI: {analyzed_signal.ai_verdict}, Sinyal: {parsed_signal.direction}"
        )

        # Sinyal ADIM 1'de zaten kaydedildi; TEKRAR INSERT ETME, durumunu güncelle.
        await self._set_status(db_session, signal_model, SignalStatus.WAITING)
        if signal_model is not None:
            signal_model.ai_verdict = analyzed_signal.ai_verdict
            signal_model.trend_aligned = analyzed_signal.trend_aligned
            await db_session.commit()

        waiting_signal = await self.waiting_monitor.add_to_waiting_queue(
            signal_model, analyzed_signal.ai_verdict, db_session
        )

        if waiting_signal:
            self.logger.info(
                f"✅ Sinyal bekleme kuyruğuna eklendi. ID: {waiting_signal.id}"
            )
        else:
            self.logger.warning("⚠️ Bekleme kuyruğuna eklenemedi (limit dolmuş olabilir)")
            await self._set_status(db_session, signal_model, SignalStatus.REJECTED)
        return None

    # ------------------------------------------------------------------
    # Pozisyon büyüklüğü
    # ------------------------------------------------------------------

    async def _calculate_position(
        self, analyzed: SignalAnalyzed
    ) -> Optional[SignalWithPosition]:
        """Pozisyon büyüklüğünü hesapla.

        RİSK TABANLI: risk_percentage, kaybedilmeyi göze alınan bakiye oranıdır.
        Miktar, stop-loss vurduğunda tam olarak bu kadar kaybedilecek şekilde
        seçilir:  quantity = (bakiye * risk%) / |giriş - stop|

        Ardından iki sınır uygulanır:
          - Nominal değer, kullanılabilir marj * kaldıraç * MAX_MARGIN_UTILISATION
            değerini aşamaz (marj yetersizliği hatasını önler).
          - Borsanın MIN_NOTIONAL / LOT_SIZE filtrelerine uyulur.

        NOT: Eski davranış nominal değeri doğrudan bakiyenin %'si yapıyor ve
        stop mesafesini yok sayıyordu; bu, "risk %2" ifadesini anlamsız kılıyordu.
        """
        signal = analyzed.signal

        balance = await self.binance.get_account_balance()
        if balance is None:
            self.logger.error(
                "❌ Bakiye okunamadı. Sahte bakiyeyle işlem AÇILMAYACAK. "
                "Sinyal iptal edildi."
            )
            return None
        if balance <= 0:
            self.logger.error(f"❌ Kullanılabilir bakiye yetersiz: {balance} USDT")
            return None

        entry = signal.entry
        stop = signal.stoploss
        if not entry or entry <= 0:
            self.logger.error("❌ Sinyalde geçerli giriş fiyatı yok")
            return None
        if not stop or stop <= 0:
            self.logger.error("❌ Sinyalde geçerli stop-loss yok")
            return None

        stop_distance = abs(entry - stop)
        if stop_distance <= 0:
            self.logger.error("❌ Stop-loss giriş fiyatına eşit — risk hesaplanamaz")
            return None

        leverage = signal.leverage or self.config.max_leverage
        risk_amount = balance * (self.config.risk_percentage / 100)

        # Risk tabanlı miktar
        quantity = risk_amount / stop_distance
        notional = quantity * entry

        # Marj sınırı
        max_notional = balance * leverage * self.MAX_MARGIN_UTILISATION
        if notional > max_notional:
            self.logger.warning(
                f"⚠️ Risk tabanlı nominal ({notional:.2f}) marj sınırını aşıyor "
                f"({max_notional:.2f}). Miktar sınıra çekiliyor."
            )
            notional = max_notional
            quantity = notional / entry

        # Borsa filtrelerine uydur
        try:
            quantity = await self.binance.quantize_quantity(signal.symbol, quantity)
            await self.binance.validate_order(signal.symbol, quantity, entry)
        except BinanceAPIError as e:
            self.logger.error(
                f"❌ Hesaplanan pozisyon borsa filtrelerine uymuyor "
                f"(kod={e.code}): {e.msg}"
            )
            return None
        except Exception as e:
            self.logger.error(f"❌ Pozisyon doğrulama hatası: {e}")
            return None

        notional = quantity * entry
        margin_required = notional / leverage

        self.logger.info(
            f"📐 Hesap: bakiye={balance:.2f} risk=%{self.config.risk_percentage} "
            f"({risk_amount:.2f} USDT) | stop mesafesi={stop_distance:.4f} | "
            f"miktar={quantity} nominal={notional:.2f} marj={margin_required:.2f} "
            f"kaldıraç={leverage}x"
        )

        return SignalWithPosition(
            signal=analyzed,
            quantity=quantity,
            position_size=notional,
            risk_amount=risk_amount,
        )

    # ------------------------------------------------------------------
    # Veritabanı yardımcıları
    # ------------------------------------------------------------------

    async def _save_signal(
        self, db_session: AsyncSession, signal: SignalParsed, status: SignalStatus
    ) -> Optional[SignalModel]:
        """Sinyali BİR KEZ kaydet ve modeli döndür.

        Model döndürüldüğü için sonraki durum güncellemeleri aynı satırı
        günceller; eskiden her adımda yeni satır ekleniyordu.
        """
        try:
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
                error_message=signal.error,
            )
            db_session.add(signal_model)
            await db_session.commit()
            await db_session.refresh(signal_model)
            return signal_model
        except Exception as e:
            self.logger.error(f"Sinyal kaydedilemedi: {e}")
            await db_session.rollback()
            return None

    async def _set_status(
        self,
        db_session: AsyncSession,
        signal_model: Optional[SignalModel],
        status: SignalStatus,
    ):
        """Sinyalin durumunu güncelle (eskiden bu fonksiyon hiçbir şey yapmıyordu)."""
        if signal_model is None:
            return
        try:
            signal_model.status = status
            await db_session.commit()
        except Exception as e:
            self.logger.error(f"Sinyal durumu güncellenemedi: {e}")
            await db_session.rollback()
