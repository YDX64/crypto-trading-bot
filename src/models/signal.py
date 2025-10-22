"""
Telegram sinyali veri modeli.
"""

from datetime import datetime
from typing import List, Optional
from enum import Enum
from pydantic import BaseModel, Field
from sqlalchemy import Column, Integer, String, Float, DateTime, JSON, Boolean, Enum as SQLEnum
from sqlalchemy.orm import relationship

from src.core.database import Base


class SignalDirection(str, Enum):
    """Sinyal yönü"""
    LONG = "LONG"
    SHORT = "SHORT"


class SignalStatus(str, Enum):
    """Sinyal durumu"""
    RECEIVED = "RECEIVED"
    PARSED = "PARSED"
    ANALYZING = "ANALYZING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"
    WAITING = "WAITING"  # Added for waiting mode


class SignalModel(Base):
    """Veritabanı sinyal modeli"""
    __tablename__ = "signals"
    
    id = Column(Integer, primary_key=True, index=True)
    raw_message = Column(String, nullable=False)
    coin = Column(String, index=True)
    direction = Column(SQLEnum(SignalDirection))
    leverage = Column(Integer)
    entry_min = Column(Float)
    entry_max = Column(Float)
    entry = Column(Float)
    targets = Column(JSON)  # List[float]
    stoploss = Column(Float)
    status = Column(SQLEnum(SignalStatus), default=SignalStatus.RECEIVED, index=True)
    ai_verdict = Column(String)  # BULLISH/BEARISH
    trend_aligned = Column(Boolean, default=False)
    ai_analysis = Column(JSON)  # AI analiz sonuçları
    error_message = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    waiting_signal = relationship("WaitingSignalModel", back_populates="signal", uselist=False)


class SignalParsed(BaseModel):
    """Parse edilmiş sinyal"""
    raw_message: str
    parsed: bool = False
    coin: Optional[str] = None
    direction: Optional[SignalDirection] = None
    leverage: Optional[int] = None
    margin_type: Optional[str] = None  # CROSS or ISOLATED
    entry_min: Optional[float] = None
    entry_max: Optional[float] = None
    entry: Optional[float] = None
    targets: List[float] = Field(default_factory=list)
    stoploss: Optional[float] = None
    symbol: Optional[str] = None
    error: Optional[str] = None


class SignalAnalyzed(BaseModel):
    """AI analiz sonuçlu sinyal"""
    signal: SignalParsed
    ai_verdict: str  # BULLISH or BEARISH
    trend_aligned: bool
    bullish_votes: int
    bearish_votes: int
    consensus: str
    analysis_1: str
    analysis_2: str
    analysis_3: str
    confidence: str = "MEDIUM"


class SignalWithPosition(BaseModel):
    """Pozisyon bilgisi eklenmiş sinyal"""
    signal: SignalAnalyzed
    quantity: float
    position_size: float
    risk_amount: float
    entry_price: Optional[float] = None

