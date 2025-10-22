"""
Pozisyon yönetim modülü.
Açık pozisyonları takip eder, trailing SL/TP uygular, break-even'e taşır.
"""

import asyncio
from typing import Optional, Dict, Any
from datetime import datetime

from src.models.position import (
    PositionModel, PositionStatus, PositionSide,
    PositionUpdate, TrailingInfo
)
from src.models.signal import SignalWithPosition, SignalDirection
from src.trading.binance_client_improved import ImprovedBinanceClient as BinanceClient
from src.core.config import settings
from src.core.logger import app_logger


class PositionManager:
    """Pozisyon yönetim motoru"""
    
    def __init__(self, binance_client: BinanceClient):
        self.binance = binance_client
        self.logger = app_logger
        self.config = settings
    
    async def open_position(
        self,
        analyzed_signal: SignalWithPosition
    ) -> Optional[PositionModel]:
        """
        Pozisyon aç.
        1. Margin type ayarla
        2. Leverage ayarla
        3. Market order aç
        4. Stop loss koy
        5. İlk TP koy (%25)
        """
        signal = analyzed_signal.signal.signal
        
        try:
            self.logger.info(f"📊 Pozisyon açılıyor: {signal.symbol} {signal.direction}")
            
            # 1. Margin Type (mesajdan al, yoksa config'den)
            margin_type = signal.margin_type or self.config.margin_type
            leverage = signal.leverage or self.config.max_leverage
            
            self.logger.info(f"⚙️ Margin: {margin_type}, Leverage: {leverage}x")
            
            await self.binance.set_margin_type(
                signal.symbol,
                margin_type
            )
            
            # 2. Leverage
            await self.binance.set_leverage(
                signal.symbol,
                leverage
            )
            
            # 3. Market Order
            side = "BUY" if signal.direction == SignalDirection.LONG else "SELL"
            entry_order = await self.binance.open_market_order(
                symbol=signal.symbol,
                side=side,
                quantity=analyzed_signal.quantity
            )
            
            entry_price = float(entry_order.get("avgPrice", signal.entry))
            
            # 4. Stop Loss
            sl_side = "SELL" if signal.direction == SignalDirection.LONG else "BUY"
            sl_order = await self.binance.place_stop_loss(
                symbol=signal.symbol,
                side=sl_side,
                stop_price=signal.stoploss,
                close_position=True
            )
            
            # 5. İlk Take Profit (%25)
            first_tp_qty = analyzed_signal.quantity * (self.config.first_tp_percentage / 100)
            tp_order = await self.binance.place_take_profit(
                symbol=signal.symbol,
                side=sl_side,  # TP de aynı side (kapatma yönü)
                stop_price=signal.targets[0],
                quantity=first_tp_qty
            )
            
            # Position model oluştur
            position = PositionModel(
                symbol=signal.symbol,
                side=PositionSide.LONG if signal.direction == SignalDirection.LONG else PositionSide.SHORT,
                leverage=leverage,
                margin_type=margin_type,
                entry_price=entry_price,
                current_price=entry_price,
                quantity=analyzed_signal.quantity,
                position_size=analyzed_signal.position_size,
                initial_stoploss=signal.stoploss,
                current_stoploss=signal.stoploss,
                first_tp_price=signal.targets[0],
                first_tp_quantity=first_tp_qty,
                targets=str(signal.targets),
                status=PositionStatus.OPEN,
                entry_order_id=str(entry_order.get("orderId")),
                sl_order_id=str(sl_order.get("orderId")),
                tp_order_id=str(tp_order.get("orderId")),
                highest_price=entry_price,
                lowest_price=entry_price,
                trailing_stop_distance=self.config.trailing_stop_percentage,
                trailing_profit_distance=self.config.trailing_profit_percentage,
                opened_at=datetime.utcnow()
            )
            
            self.logger.info(
                f"✅ Pozisyon açıldı: {signal.symbol} {signal.direction} "
                f"{analyzed_signal.quantity} @ {entry_price}",
                extra={"trade": True}
            )
            
            return position
        
        except Exception as e:
            self.logger.error(f"❌ Pozisyon açma hatası: {e}")
            return None
    
    async def check_first_tp_hit(self, position: PositionModel) -> bool:
        """İlk TP vurdu mu kontrol et"""
        try:
            pos_info = await self.binance.get_position_risk(position.symbol)
            
            if not pos_info:
                return False
            
            current_qty = abs(float(pos_info.get("positionAmt", 0)))
            original_qty = position.quantity
            
            # %25 kapandıysa TP vurmuş demektir
            if original_qty > 0:
                remaining_pct = (current_qty / original_qty) * 100
                
                if remaining_pct <= 80:  # %80 veya daha az kaldıysa
                    self.logger.info(
                        f"🎯 İlk TP vurdu! Kalan: {remaining_pct:.1f}%",
                        extra={"trade": True}
                    )
                    return True
            
            return False
        
        except Exception as e:
            self.logger.error(f"TP kontrolü hatası: {e}")
            return False
    
    async def move_to_break_even(self, position: PositionModel) -> bool:
        """Stop Loss'u break-even'e (giriş fiyatına) taşı"""
        try:
            self.logger.info(f"🔄 Break-even'e taşınıyor: {position.symbol}")
            
            # Mevcut SL orderlarını iptal et
            open_orders = await self.binance.get_open_orders(position.symbol)
            
            for order in open_orders:
                if order["type"] in ["STOP_MARKET", "STOP"]:
                    await self.binance.cancel_order(
                        position.symbol,
                        int(order["orderId"])
                    )
            
            # Yeni SL'yi entry price'a koy
            sl_side = "SELL" if position.side == PositionSide.LONG else "BUY"
            new_sl_order = await self.binance.place_stop_loss(
                symbol=position.symbol,
                side=sl_side,
                stop_price=position.entry_price,
                close_position=True
            )
            
            # Pozisyonu güncelle
            position.current_stoploss = position.entry_price
            position.is_break_even = True
            position.is_trailing = True
            position.status = PositionStatus.BREAK_EVEN
            position.first_tp_hit_at = datetime.utcnow()
            position.sl_order_id = str(new_sl_order.get("orderId"))
            
            self.logger.info(
                f"✅ Break-even aktif! SL: {position.entry_price}",
                extra={"trade": True}
            )
            
            return True
        
        except Exception as e:
            self.logger.error(f"❌ Break-even hatası: {e}")
            return False
    
    async def update_trailing_stop(self, position: PositionModel) -> bool:
        """Trailing stop loss güncelle"""
        try:
            # Güncel fiyatı al
            current_price = await self.binance.get_current_price(position.symbol)
            
            if not current_price:
                return False
            
            # Trailing bilgisini hesapla
            trailing_info = self._calculate_trailing_stop(
                position=position,
                current_price=current_price
            )
            
            if not trailing_info.should_update:
                return False
            
            self.logger.info(
                f"📈 Trailing SL güncelleniyor: {position.symbol} "
                f"Eski: {position.current_stoploss} -> Yeni: {trailing_info.new_stop_loss}"
            )
            
            # Mevcut SL orderlarını iptal et
            open_orders = await self.binance.get_open_orders(position.symbol)
            
            for order in open_orders:
                if order["type"] in ["STOP_MARKET", "STOP"]:
                    await self.binance.cancel_order(
                        position.symbol,
                        int(order["orderId"])
                    )
            
            # Yeni SL koy
            sl_side = "SELL" if position.side == PositionSide.LONG else "BUY"
            new_sl_order = await self.binance.place_stop_loss(
                symbol=position.symbol,
                side=sl_side,
                stop_price=trailing_info.new_stop_loss,
                close_position=True
            )
            
            # Pozisyonu güncelle
            position.current_stoploss = trailing_info.new_stop_loss
            position.current_price = current_price
            position.sl_order_id = str(new_sl_order.get("orderId"))
            
            if position.side == PositionSide.LONG:
                position.highest_price = max(position.highest_price or 0, current_price)
            else:
                position.lowest_price = min(position.lowest_price or float('inf'), current_price)
            
            # P&L hesapla
            pnl_info = self._calculate_pnl(position, current_price)
            position.unrealized_pnl = pnl_info["unrealized_pnl"]
            position.pnl_percentage = pnl_info["pnl_percentage"]
            
            position.last_checked_at = datetime.utcnow()
            
            self.logger.info(
                f"✅ Trailing SL güncellendi: {trailing_info.new_stop_loss} "
                f"(P&L: {pnl_info['pnl_percentage']:.2f}%)",
                extra={"trade": True}
            )
            
            return True
        
        except Exception as e:
            self.logger.error(f"❌ Trailing stop güncelleme hatası: {e}")
            return False
    
    def _calculate_trailing_stop(
        self,
        position: PositionModel,
        current_price: float
    ) -> TrailingInfo:
        """Trailing stop hesapla"""
        trailing_pct = position.trailing_stop_distance / 100
        
        if position.side == PositionSide.LONG:
            # LONG: Fiyat yükselirse SL'yi yukarı çek
            new_sl = current_price * (1 - trailing_pct)
            should_update = new_sl > position.current_stoploss
            
            return TrailingInfo(
                symbol=position.symbol,
                side="LONG",
                current_price=current_price,
                entry_price=position.entry_price,
                new_stop_loss=new_sl,
                should_update=should_update,
                trailing_pct=position.trailing_stop_distance,
                highest_price=max(position.highest_price or 0, current_price)
            )
        
        else:  # SHORT
            # SHORT: Fiyat düşerse SL'yi aşağı çek
            new_sl = current_price * (1 + trailing_pct)
            should_update = new_sl < position.current_stoploss
            
            return TrailingInfo(
                symbol=position.symbol,
                side="SHORT",
                current_price=current_price,
                entry_price=position.entry_price,
                new_stop_loss=new_sl,
                should_update=should_update,
                trailing_pct=position.trailing_stop_distance,
                lowest_price=min(position.lowest_price or float('inf'), current_price)
            )
    
    def _calculate_pnl(
        self,
        position: PositionModel,
        current_price: float
    ) -> Dict[str, float]:
        """P&L hesapla"""
        if position.side == PositionSide.LONG:
            pnl = (current_price - position.entry_price) * position.quantity
        else:  # SHORT
            pnl = (position.entry_price - current_price) * position.quantity
        
        pnl_pct = (pnl / position.position_size) * 100 if position.position_size > 0 else 0
        
        return {
            "unrealized_pnl": pnl,
            "pnl_percentage": pnl_pct
        }
    
    async def is_position_still_open(self, position: PositionModel) -> bool:
        """Pozisyon hala açık mı?"""
        try:
            pos_info = await self.binance.get_position_risk(position.symbol)
            
            if not pos_info:
                return False
            
            pos_amt = abs(float(pos_info.get("positionAmt", 0)))
            
            return pos_amt > 0
        
        except Exception as e:
            self.logger.error(f"Pozisyon kontrolü hatası: {e}")
            return False
    
    async def close_position_record(self, position: PositionModel):
        """Pozisyon kaydını kapat"""
        position.status = PositionStatus.CLOSED
        position.closed_at = datetime.utcnow()
        
        self.logger.info(
            f"🏁 Pozisyon kapandı: {position.symbol} "
            f"(P&L: {position.pnl_percentage:.2f}%)",
            extra={"trade": True}
        )

