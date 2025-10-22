"""
Trading pozisyonu veri modeli.
"""

from datetime import datetime
from typing import Optional
from enum import Enum
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, Enum as SQLEnum, ForeignKey

from src.core.database import Base


class PositionStatus(str, Enum):
    """Pozisyon durumu"""
    OPENING = "OPENING"
    OPEN = "OPEN"
    FIRST_TP_HIT = "FIRST_TP_HIT"
    BREAK_EVEN = "BREAK_EVEN"
    TRAILING = "TRAILING"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"
    STOPPED = "STOPPED"
    LIQUIDATED = "LIQUIDATED"


class PositionSide(str, Enum):
    """Pozisyon tarafı"""
    LONG = "LONG"
    SHORT = "SHORT"


class PositionModel(Base):
    """Veritabanı pozisyon modeli"""
    __tablename__ = "positions"
    
    id = Column(Integer, primary_key=True, index=True)
    signal_id = Column(Integer, ForeignKey("signals.id"), index=True)
    
    # Pozisyon Bilgileri
    symbol = Column(String, index=True, nullable=False)
    side = Column(SQLEnum(PositionSide), nullable=False)
    leverage = Column(Integer, nullable=False)
    margin_type = Column(String, default="ISOLATED")
    
    # Fiyat Bilgileri
    entry_price = Column(Float, nullable=False)
    current_price = Column(Float)
    quantity = Column(Float, nullable=False)
    position_size = Column(Float, nullable=False)
    
    # Stop Loss & Take Profit
    initial_stoploss = Column(Float, nullable=False)
    current_stoploss = Column(Float, nullable=False)
    first_tp_price = Column(Float, nullable=False)
    first_tp_quantity = Column(Float, nullable=False)
    targets = Column(String)  # JSON array
    
    # Durum Bilgileri
    status = Column(SQLEnum(PositionStatus), default=PositionStatus.OPENING, index=True)
    is_break_even = Column(Boolean, default=False)
    is_trailing = Column(Boolean, default=False)
    first_tp_hit_at = Column(DateTime, nullable=True)
    
    # P&L
    realized_pnl = Column(Float, default=0.0)
    unrealized_pnl = Column(Float, default=0.0)
    total_pnl = Column(Float, default=0.0)
    pnl_percentage = Column(Float, default=0.0)
    
    # Binance Order IDs
    entry_order_id = Column(String)
    sl_order_id = Column(String)
    tp_order_id = Column(String)
    
    # Trailing Bilgileri
    highest_price = Column(Float)  # LONG için
    lowest_price = Column(Float)   # SHORT için
    trailing_stop_distance = Column(Float)
    trailing_profit_distance = Column(Float)
    
    # Timestamps
    opened_at = Column(DateTime, default=datetime.utcnow, index=True)
    closed_at = Column(DateTime, nullable=True)
    last_checked_at = Column(DateTime, default=datetime.utcnow)
    
    # Metadata
    error_message = Column(String, nullable=True)
    notes = Column(String, nullable=True)


class PositionInfo(BaseModel):
    """Pozisyon bilgisi DTO"""
    id: int
    symbol: str
    side: PositionSide
    leverage: int
    entry_price: float
    current_price: Optional[float]
    quantity: float
    position_size: float
    current_stoploss: float
    status: PositionStatus
    is_break_even: bool
    is_trailing: bool
    unrealized_pnl: float
    pnl_percentage: float
    opened_at: datetime
    
    class Config:
        from_attributes = True


class PositionUpdate(BaseModel):
    """Pozisyon güncelleme"""
    current_price: float
    current_stoploss: Optional[float] = None
    status: Optional[PositionStatus] = None
    unrealized_pnl: Optional[float] = None
    pnl_percentage: Optional[float] = None


class TrailingInfo(BaseModel):
    """Trailing bilgisi"""
    symbol: str
    side: str
    current_price: float
    entry_price: float
    new_stop_loss: float
    should_update: bool
    trailing_pct: float
    highest_price: Optional[float] = None
    lowest_price: Optional[float] = None

