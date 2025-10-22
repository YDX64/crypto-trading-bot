"""
Waiting signal models for database.
Tracks signals in waiting mode with technical indicators.
"""

from datetime import datetime
from typing import Optional, List
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, JSON, Enum as SQLEnum
from sqlalchemy.orm import relationship
import enum

from src.core.database import Base


class WaitingStatus(enum.Enum):
    """Waiting signal status"""
    WAITING = "WAITING"
    MONITORING = "MONITORING"
    EXECUTED = "EXECUTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class WaitingSignalModel(Base):
    """Waiting signal database model"""
    __tablename__ = "waiting_signals"

    id = Column(Integer, primary_key=True, index=True)
    signal_id = Column(Integer, ForeignKey("signals.id"), nullable=False)

    # Signal details
    symbol = Column(String(20), nullable=False, index=True)
    direction = Column(String(10), nullable=False)
    original_entry_min = Column(Float, nullable=False)
    original_entry_max = Column(Float, nullable=False)
    current_price = Column(Float)
    ai_verdict = Column(String(20))

    # Technical indicator values
    rsi_value = Column(Float)
    macd_value = Column(Float)
    macd_signal = Column(Float)
    macd_histogram = Column(Float)
    bb_upper = Column(Float)
    bb_middle = Column(Float)
    bb_lower = Column(Float)

    # Monitoring config
    max_wait_hours = Column(Integer, default=24)
    check_interval_minutes = Column(Integer, default=5)
    entry_improvement_percentage = Column(Float, default=1.0)

    # Status tracking
    status = Column(SQLEnum(WaitingStatus), default=WaitingStatus.WAITING, index=True)
    monitoring_started_at = Column(DateTime)
    last_checked_at = Column(DateTime)
    executed_at = Column(DateTime)
    executed_price = Column(Float)

    # Conditions tracking
    conditions_met_count = Column(Integer, default=0)
    total_checks = Column(Integer, default=0)
    last_score = Column(Float, default=0.0)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    signal = relationship("SignalModel", back_populates="waiting_signal")
    snapshots = relationship("IndicatorSnapshot", back_populates="waiting_signal", cascade="all, delete-orphan")

    @property
    def is_expired(self) -> bool:
        """Check if signal has expired"""
        if self.created_at and self.max_wait_hours:
            time_diff = datetime.utcnow() - self.created_at
            return time_diff.total_seconds() > self.max_wait_hours * 3600
        return False

    @property
    def wait_time_hours(self) -> float:
        """Get current wait time in hours"""
        if self.created_at:
            time_diff = datetime.utcnow() - self.created_at
            return time_diff.total_seconds() / 3600
        return 0.0


class IndicatorSnapshot(Base):
    """Technical indicator snapshot at a point in time"""
    __tablename__ = "indicator_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    waiting_signal_id = Column(Integer, ForeignKey("waiting_signals.id"), nullable=False, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow)

    # Price and volume
    price = Column(Float, nullable=False)
    volume = Column(Float)

    # Technical indicators
    rsi = Column(Float)
    macd = Column(Float)
    macd_signal = Column(Float)
    macd_histogram = Column(Float)
    bb_upper = Column(Float)
    bb_middle = Column(Float)
    bb_lower = Column(Float)

    # Conditions at snapshot
    rsi_condition_met = Column(Boolean, default=False)
    macd_condition_met = Column(Boolean, default=False)
    bb_condition_met = Column(Boolean, default=False)
    price_condition_met = Column(Boolean, default=False)
    overall_score = Column(Float)  # 0-100 score

    # Relationship
    waiting_signal = relationship("WaitingSignalModel", back_populates="snapshots")


class WaitingModeConfig(Base):
    """Waiting mode configuration per symbol"""
    __tablename__ = "waiting_mode_config"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), unique=True)

    # Feature flags
    enabled = Column(Boolean, default=True)
    use_rsi = Column(Boolean, default=True)
    use_macd = Column(Boolean, default=True)
    use_bollinger = Column(Boolean, default=True)
    use_volume = Column(Boolean, default=False)

    # RSI settings
    rsi_period = Column(Integer, default=14)
    rsi_oversold_long = Column(Float, default=30.0)
    rsi_overbought_short = Column(Float, default=70.0)

    # MACD settings
    macd_fast = Column(Integer, default=12)
    macd_slow = Column(Integer, default=26)
    macd_signal_period = Column(Integer, default=9)

    # Bollinger settings
    bb_period = Column(Integer, default=20)
    bb_std_dev = Column(Float, default=2.0)

    # Entry conditions
    min_conditions_required = Column(Integer, default=2)
    price_improvement_required = Column(Float, default=0.5)

    # Timing
    max_wait_hours = Column(Integer, default=24)
    min_wait_minutes = Column(Integer, default=30)
    check_interval_minutes = Column(Integer, default=5)

    # Risk management
    max_waiting_positions = Column(Integer, default=3)
    expire_on_opposite_signal = Column(Boolean, default=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)