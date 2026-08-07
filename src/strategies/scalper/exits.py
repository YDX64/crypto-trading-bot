"""
ExitManager — scalper pozisyonları için TP dolum takibi, break-even geçişi ve
chandelier trailing döngüsü.

GÜVENLİK İLKESİ (bugünkü onarımlarla birebir): "bilinmiyor" ASLA "kapandı"
sayılmaz. Pozisyon durumu sorgulanamazsa izleme BIRAKILMAZ, o tur atlanır.
SL değişimleri PositionManager'ın boşluksuz deseniyle yapılır (önce yeni
reduceOnly SL, sonra eskisi iptal) — pm.replace_stop_loss sarmalayıcısı
üzerinden.
"""

from __future__ import annotations

from datetime import timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

from src.core.logger import app_logger
from src.models.position import PositionModel, PositionStatus, PositionSide
from src.strategies.scalper.executor import ScalpPosition
from src.strategies.scalper.indicators import chandelier_stop
from src.strategies.scalper.tracker import ScalpTracker
from src.strategies.scalper.types import (
    Candle,
    Direction,
    ExitPlan,
    Regime,
    ScalpSignal,
    price_at_roi,
)
from src.trading.binance_client_improved import (
    ImprovedBinanceClient,
    BinanceAPIError,
)
from src.trading.position_manager import PositionManager

KlineFetch = Callable[[str, str, int], Awaitable[List[Candle]]]


class ExitManager:
    """Açık scalper pozisyonlarını izler ve çıkış merdivenini yönetir."""

    def __init__(
        self,
        client: ImprovedBinanceClient,
        pm: PositionManager,
        tracker: ScalpTracker,
        cfg: Any,
        kline_fetch: KlineFetch,
    ):
        self.client = client
        self.pm = pm
        self.tracker = tracker
        self.cfg = cfg
        self.kline_fetch = kline_fetch
        self.logger = app_logger
        self._positions: Dict[str, ScalpPosition] = {}

    def track(self, sp: ScalpPosition) -> None:
        """Pozisyonu izleme listesine ekle (sembol anahtarlı)."""
        self._positions[sp.position.symbol] = sp

    def tracked_symbols(self) -> Set[str]:
        return set(self._positions.keys())

    # ------------------------------------------------------------------
    # Ana döngü adımı
    # ------------------------------------------------------------------

    async def step(self) -> None:
        """Engine her turda çağırır: her izlenen sembol için bir adım işlet."""
        for symbol in list(self._positions.keys()):
            sp = self._positions.get(symbol)
            if sp is None:
                continue
            await self._step_one(symbol, sp)

    async def _step_one(self, symbol: str, sp: ScalpPosition) -> None:
        try:
            pos_info = await self.client.get_position_risk(symbol)
        except BinanceAPIError as e:
            self.logger.error(
                f"⚠️ {symbol}: pozisyon durumu sorgulanamadı (kod={e.code}: {e.msg}). "
                f"İzleme sürüyor — 'bilinmiyor' 'kapandı' sayılmaz."
            )
            return
        except Exception as e:
            self.logger.error(
                f"⚠️ {symbol}: pozisyon durumu sorgusunda beklenmeyen hata ({e}). İzleme sürüyor."
            )
            return

        amt = abs(float(pos_info.get("positionAmt", 0))) if pos_info else 0.0

        if amt == 0:
            await self._handle_closed(symbol, sp)
            return

        # MAE/MFE güncelle (mark/güncel fiyat ile)
        try:
            current_price = await self.client.get_current_price(symbol)
        except Exception as e:
            current_price = None
            self.logger.debug(f"{symbol}: güncel fiyat alınamadı ({e}), MAE/MFE bu turda güncellenmiyor")
        if current_price:
            self._update_mae_mfe(sp, current_price)
            sp.position.current_price = current_price

        # TP1 dolum kontrolü → break-even
        if not sp.tp1_done:
            await self._check_tp1(symbol, sp, amt)

        # Chandelier trailing
        if sp.trailing_active:
            await self._update_trailing(symbol, sp)

    async def _check_tp1(self, symbol: str, sp: ScalpPosition, live_qty: float) -> None:
        filled = sp.position.quantity
        if filled <= 0:
            return
        tp1_fraction = self.cfg.scalper_tp1_fraction
        threshold = filled * (1 - tp1_fraction * 0.9)

        if live_qty > threshold:
            return  # TP1 henüz dolmadı

        self.logger.info(
            f"🎯 {symbol}: TP1 dolmuş görünüyor (kalan={live_qty}, eşik={threshold:.6f}) — "
            f"SL break-even'e taşınıyor"
        )
        ok = await self.pm.replace_stop_loss(sp.position, sp.plan.breakeven_price)
        if ok:
            sp.tp1_done = True
            sp.trailing_active = True
            sp.position.current_stoploss = sp.plan.breakeven_price
            self.logger.info(f"✅ {symbol}: break-even aktif, SL={sp.plan.breakeven_price}")
        else:
            self.logger.warning(
                f"⚠️ {symbol}: SL break-even'e taşınamadı, eski SL korunuyor. "
                f"Sonraki turda tekrar denenecek."
            )

    async def _update_trailing(self, symbol: str, sp: ScalpPosition) -> None:
        try:
            candles = await self.kline_fetch(symbol, "5m", 200)
        except Exception as e:
            self.logger.warning(f"⚠️ {symbol}: trailing için mum verisi alınamadı ({e}), tur atlanıyor")
            return

        if not candles:
            return

        since_index = len(candles) - 1
        for i, c in enumerate(candles):
            if c.close_time > sp.entry_candle_time:
                since_index = i
                break

        direction = sp.signal.direction
        try:
            raw_stop = chandelier_stop(
                candles,
                direction=direction,
                atr_mult=self.cfg.scalper_chandelier_atr_mult,
                atr_period=self.cfg.scalper_chandelier_atr_period,
                since_index=since_index,
            )
        except Exception as e:
            self.logger.error(f"❌ {symbol}: chandelier hesaplanamadı ({e})")
            return

        if raw_stop == 0.0:
            # indicators.chandelier_stop yetersiz veride 0.0 döner — "hesaplanamadı"
            # anlamına gelir, gerçek fiyat DEĞİLDİR. Bu turda güncelleme yapılmaz.
            self.logger.debug(f"{symbol}: chandelier için yetersiz veri, trailing bu turda atlandı")
            return

        current_sl = sp.position.current_stoploss or sp.plan.breakeven_price
        if direction == Direction.LONG:
            new_stop = max(sp.plan.breakeven_price, raw_stop)
            should_update = new_stop > current_sl * 1.0005
        else:
            new_stop = min(sp.plan.breakeven_price, raw_stop)
            should_update = new_stop < current_sl * 0.9995

        if not should_update:
            return

        ok = await self.pm.replace_stop_loss(sp.position, new_stop)
        if ok:
            sp.position.current_stoploss = new_stop
            self.logger.info(f"📈 {symbol}: chandelier trailing SL güncellendi -> {new_stop}")
        else:
            self.logger.warning(f"⚠️ {symbol}: trailing SL güncellenemedi, eski SL korunuyor")

    async def _handle_closed(self, symbol: str, sp: ScalpPosition) -> None:
        try:
            await self.client.cancel_all_open_orders(symbol)
        except Exception as e:
            self.logger.warning(f"⚠️ {symbol}: artık emirler temizlenemedi ({e})")

        exit_price: Optional[float] = None
        try:
            exit_price = await self.client.get_current_price(symbol)
        except Exception:
            pass
        if not exit_price:
            exit_price = sp.position.current_price or sp.position.entry_price

        direction = sp.signal.direction
        entry = sp.position.entry_price
        qty = sp.position.quantity
        if direction == Direction.LONG:
            realized_pnl = (exit_price - entry) * qty
        else:
            realized_pnl = (entry - exit_price) * qty

        exit_reason = self._infer_exit_reason(sp, exit_price)

        try:
            await self.tracker.record_close(
                trade_id=sp.trade_id,
                exit_price=exit_price,
                realized_pnl=realized_pnl,
                exit_reason=exit_reason,
                mae_pct=sp.mae_pct,
                mfe_pct=sp.mfe_pct,
            )
        except Exception as e:
            self.logger.error(f"❌ {symbol}: kapanış kaydı yazılamadı (#{sp.trade_id}): {e}")

        self.logger.info(
            f"🏁 Scalp pozisyon kapandı: {symbol} PNL={realized_pnl:.2f} neden={exit_reason}",
            extra={"trade": True},
        )
        self._positions.pop(symbol, None)

    @staticmethod
    def _infer_exit_reason(sp: ScalpPosition, exit_price: float) -> str:
        """Kaba çıkarım: son fiyat SL'ye mi TP tarafına mı yakındı + tp1_done bilgisi."""
        if sp.trailing_active:
            # TP1 sonrası trailing aktifken kapanmışsa TRAIL veya son SL — TRAIL say
            return "TRAIL"
        sl_price = sp.plan.initial_stop
        tp_price = sp.plan.tp1_price
        dist_to_sl = abs(exit_price - sl_price)
        dist_to_tp = abs(exit_price - tp_price)
        return "TP_LADDER" if dist_to_tp < dist_to_sl else "SL"

    def _update_mae_mfe(self, sp: ScalpPosition, current_price: float) -> None:
        entry = sp.position.entry_price
        leverage = sp.position.leverage or 1
        if entry <= 0:
            return
        price_delta_pct = (current_price - entry) / entry * 100.0
        if sp.signal.direction == Direction.SHORT:
            price_delta_pct = -price_delta_pct
        roi_pct = price_delta_pct * leverage
        sp.mfe_pct = max(sp.mfe_pct, roi_pct)
        sp.mae_pct = min(sp.mae_pct, roi_pct)

    # ------------------------------------------------------------------
    # Restart kurtarma
    # ------------------------------------------------------------------

    async def recover(self) -> None:
        """DB'de status=OPEN olan scalp işlemlerini borsadaki gerçek pozisyonlarla
        eşleştirip izlemeye geri al.

        Borsada karşılığı bulunmayan bir DB kaydı (manuel kapatma, dış müdahale,
        vb.) exit_reason=UNKNOWN ile kapatılır — "bilinmiyor" gerçeği maskelemez.
        """
        try:
            open_trades = await self.tracker.open_trades()
        except Exception as e:
            self.logger.error(f"❌ recover(): açık scalp kayıtları okunamadı ({e})")
            return

        if not open_trades:
            self.logger.info("ℹ️ recover(): DB'de açık scalp işlemi yok")
            return

        for trade in open_trades:
            await self._recover_one(trade)

    async def _recover_one(self, trade) -> None:
        symbol = trade.symbol
        try:
            pos_info = await self.client.get_position_risk(symbol)
        except Exception as e:
            self.logger.error(
                f"⚠️ recover(): {symbol} pozisyon durumu sorgulanamadı ({e}), "
                f"#{trade.id} bu turda atlanıyor"
            )
            return

        amt = abs(float(pos_info.get("positionAmt", 0))) if pos_info else 0.0

        if amt <= 0:
            self.logger.warning(
                f"⚠️ recover(): {symbol} borsada açık pozisyon yok ama DB'de #{trade.id} "
                f"OPEN görünüyor — UNKNOWN ile kapatılıyor"
            )
            try:
                exit_price = await self.client.get_current_price(symbol) or trade.entry_price
            except Exception:
                exit_price = trade.entry_price
            try:
                await self.tracker.record_close(
                    trade_id=trade.id, exit_price=exit_price, realized_pnl=0.0,
                    exit_reason="UNKNOWN",
                )
            except Exception as e:
                self.logger.error(f"❌ recover(): {symbol} #{trade.id} UNKNOWN kapanışı yazılamadı ({e})")
            return

        direction = Direction(trade.direction)
        leverage = trade.leverage

        current_stop = trade.entry_price
        try:
            algo_orders = await self.client.get_open_algo_orders(symbol)
        except Exception as e:
            algo_orders = []
            self.logger.warning(f"⚠️ recover(): {symbol} koşullu emirler okunamadı ({e})")
        for order in algo_orders:
            if order.get("orderType") in ("STOP_MARKET", "STOP"):
                trigger = order.get("triggerPrice") or order.get("stopPrice")
                if trigger:
                    current_stop = float(trigger)
                    break

        tp1_price = price_at_roi(trade.entry_price, self.cfg.scalper_tp1_roi, leverage, direction)
        tp2_price = price_at_roi(trade.entry_price, self.cfg.scalper_tp2_roi, leverage, direction)
        buffer_frac = self.cfg.scalper_breakeven_buffer_pct / 100.0
        breakeven_price = (
            trade.entry_price * (1 + buffer_frac) if direction == Direction.LONG
            else trade.entry_price * (1 - buffer_frac)
        )

        tp1_fraction = self.cfg.scalper_tp1_fraction
        tp2_fraction = self.cfg.scalper_tp2_fraction
        tp1_done = amt <= trade.quantity * (1 - tp1_fraction * 0.9)

        signal = ScalpSignal(
            strategy=trade.strategy,
            symbol=symbol,
            direction=direction,
            entry_price=trade.entry_price,
            stop_price=current_stop,
            reason=trade.signal_reason or "recover",
            regime=Regime.UNKNOWN,
            atr_5m=0.0,
        )

        position = PositionModel(
            symbol=symbol,
            side=PositionSide.LONG if direction == Direction.LONG else PositionSide.SHORT,
            leverage=leverage,
            margin_type="ISOLATED",
            entry_price=trade.entry_price,
            current_price=trade.entry_price,
            quantity=amt,
            position_size=amt * trade.entry_price,
            initial_stoploss=current_stop,
            current_stoploss=current_stop,
            first_tp_price=tp1_price,
            first_tp_quantity=trade.quantity * tp1_fraction,
            targets=str([tp1_price, tp2_price]),
            status=PositionStatus.OPEN,
            entry_order_id="",
            sl_order_id=trade.sl_algo_id,
            tp_order_id=trade.tp1_algo_id,
            highest_price=trade.entry_price,
            lowest_price=trade.entry_price,
            trailing_stop_distance=self.cfg.scalper_chandelier_atr_mult,
            trailing_profit_distance=self.cfg.scalper_tp1_roi,
            opened_at=trade.opened_at,
            notes=f"scalper:{trade.strategy}:recovered",
        )

        plan = ExitPlan(
            tp1_price=tp1_price,
            tp1_quantity=trade.quantity * tp1_fraction,
            tp2_price=tp2_price,
            tp2_quantity=trade.quantity * tp2_fraction,
            runner_quantity=max(trade.quantity * (1 - tp1_fraction - tp2_fraction), 0.0),
            initial_stop=current_stop,
            breakeven_price=breakeven_price,
            chandelier_atr_mult=self.cfg.scalper_chandelier_atr_mult,
            tp1_algo_id=trade.tp1_algo_id,
            tp2_algo_id=trade.tp2_algo_id,
        )

        entry_candle_time = 0
        if trade.opened_at:
            opened_at = trade.opened_at
            if opened_at.tzinfo is None:
                opened_at = opened_at.replace(tzinfo=timezone.utc)
            entry_candle_time = int(opened_at.timestamp() * 1000)

        sp = ScalpPosition(
            trade_id=trade.id,
            signal=signal,
            position=position,
            plan=plan,
            entry_candle_time=entry_candle_time,
            tp1_done=tp1_done,
            trailing_active=tp1_done,
        )
        self.track(sp)
        self.logger.info(
            f"♻️ recover(): {symbol} #{trade.id} izlemeye geri alındı "
            f"(miktar={amt}, tp1_done={tp1_done})",
            extra={"trade": True},
        )
