"""
Real-time monitoring system using WebSocket
Tracks positions, orders, and account updates in real-time
"""

import asyncio
import json
from typing import Dict, Any, List, Callable, Optional
from datetime import datetime
from dataclasses import dataclass, field
from enum import Enum

from src.trading.binance_testnet_client import BinanceFuturesTestnetClient
from src.core.logger import app_logger


@dataclass
class PositionSnapshot:
    """Real-time position data"""
    symbol: str
    side: str  # LONG or SHORT
    quantity: float
    entry_price: float
    mark_price: float
    unrealized_pnl: float
    realized_pnl: float
    margin_ratio: float
    liquidation_price: float
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def pnl_percentage(self) -> float:
        """Calculate PNL percentage"""
        if self.entry_price == 0:
            return 0
        return (self.unrealized_pnl / (self.entry_price * abs(self.quantity))) * 100

    @property
    def is_profitable(self) -> bool:
        """Check if position is profitable"""
        return self.unrealized_pnl > 0


@dataclass
class OrderUpdate:
    """Real-time order update"""
    order_id: int
    symbol: str
    side: str
    order_type: str
    quantity: float
    price: float
    status: str
    executed_qty: float
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class AccountSnapshot:
    """Real-time account data"""
    total_balance: float
    available_balance: float
    total_unrealized_pnl: float
    total_margin_ratio: float
    positions: List[PositionSnapshot] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)


class AlertLevel(Enum):
    """Alert levels for monitoring"""
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class RealTimeMonitor:
    """
    Real-time monitoring system for Binance Futures
    - Tracks positions, orders, and account updates
    - Sends alerts for important events
    - Provides real-time analytics
    """

    def __init__(self, binance_client: BinanceFuturesTestnetClient):
        self.client = binance_client
        self.logger = app_logger

        # Current state
        self.account_snapshot: Optional[AccountSnapshot] = None
        self.positions: Dict[str, PositionSnapshot] = {}
        self.recent_orders: List[OrderUpdate] = []

        # Callbacks for different events
        self.alert_callbacks: List[Callable] = []
        self.position_callbacks: List[Callable] = []
        self.order_callbacks: List[Callable] = []

        # Monitoring settings
        self.margin_warning_threshold = 0.7  # 70% margin usage
        self.margin_critical_threshold = 0.9  # 90% margin usage
        self.pnl_alert_threshold = 10.0  # 10% PNL

        # Monitoring task
        self.monitoring_task = None
        self.is_running = False

    # === Event Registration ===

    def on_alert(self, callback: Callable):
        """Register alert callback"""
        self.alert_callbacks.append(callback)

    def on_position_update(self, callback: Callable):
        """Register position update callback"""
        self.position_callbacks.append(callback)

    def on_order_update(self, callback: Callable):
        """Register order update callback"""
        self.order_callbacks.append(callback)

    # === Monitoring Control ===

    async def start(self):
        """Start real-time monitoring"""
        if self.is_running:
            self.logger.warning("Monitor already running")
            return

        self.is_running = True
        self.logger.info("Starting real-time monitor...")

        # Initial snapshot
        await self._update_account_snapshot()

        # Start WebSocket monitoring
        self.monitoring_task = asyncio.create_task(
            self.client.start_websocket(self._handle_websocket_message)
        )

        # Start periodic checks
        asyncio.create_task(self._periodic_checks())

        self.logger.info("Real-time monitor started successfully")

    async def stop(self):
        """Stop monitoring"""
        self.is_running = False

        if self.monitoring_task:
            self.monitoring_task.cancel()
            try:
                await self.monitoring_task
            except asyncio.CancelledError:
                pass

        self.logger.info("Real-time monitor stopped")

    # === WebSocket Message Handling ===

    async def _handle_websocket_message(self, data: Dict[str, Any]):
        """Process WebSocket messages"""
        event_type = data.get("e")

        if event_type == "ACCOUNT_UPDATE":
            await self._process_account_update(data)
        elif event_type == "ORDER_TRADE_UPDATE":
            await self._process_order_update(data)
        elif event_type == "MARGIN_CALL":
            await self._process_margin_call(data)

    async def _process_account_update(self, data: Dict[str, Any]):
        """Process account update event"""
        account_data = data.get("a", {})

        # Update positions
        for position_data in account_data.get("P", []):
            symbol = position_data.get("s")
            position = PositionSnapshot(
                symbol=symbol,
                side="LONG" if float(position_data.get("pa", 0)) > 0 else "SHORT",
                quantity=abs(float(position_data.get("pa", 0))),
                entry_price=float(position_data.get("ep", 0)),
                mark_price=float(position_data.get("mp", 0)),
                unrealized_pnl=float(position_data.get("up", 0)),
                realized_pnl=float(position_data.get("rp", 0)),
                margin_ratio=0,  # Calculate separately
                liquidation_price=0  # Calculate separately
            )

            # Store position
            if position.quantity > 0:
                self.positions[symbol] = position

                # Check for alerts
                await self._check_position_alerts(position)

                # Notify callbacks
                for callback in self.position_callbacks:
                    await callback(position)
            elif symbol in self.positions:
                # Position closed
                closed_position = self.positions.pop(symbol)
                await self._send_alert(
                    AlertLevel.INFO,
                    f"Position closed: {symbol}",
                    {"position": closed_position, "final_pnl": closed_position.realized_pnl}
                )

        # Update account snapshot
        await self._update_account_snapshot()

    async def _process_order_update(self, data: Dict[str, Any]):
        """Process order update event"""
        order_data = data.get("o", {})

        order_update = OrderUpdate(
            order_id=order_data.get("i"),
            symbol=order_data.get("s"),
            side=order_data.get("S"),
            order_type=order_data.get("o"),
            quantity=float(order_data.get("q", 0)),
            price=float(order_data.get("p", 0)),
            status=order_data.get("X"),
            executed_qty=float(order_data.get("z", 0))
        )

        # Store recent order
        self.recent_orders.append(order_update)
        if len(self.recent_orders) > 100:
            self.recent_orders.pop(0)

        # Notify callbacks
        for callback in self.order_callbacks:
            await callback(order_update)

        # Log important order events
        if order_update.status == "FILLED":
            self.logger.info(
                f"Order filled: {order_update.symbol} {order_update.side} "
                f"{order_update.executed_qty} @ {order_update.price}"
            )
        elif order_update.status == "CANCELED":
            self.logger.info(f"Order canceled: {order_update.symbol} #{order_update.order_id}")
        elif order_update.status == "REJECTED":
            self.logger.error(f"Order rejected: {order_update.symbol} #{order_update.order_id}")

    async def _process_margin_call(self, data: Dict[str, Any]):
        """Process margin call event"""
        await self._send_alert(
            AlertLevel.CRITICAL,
            "MARGIN CALL RECEIVED!",
            data
        )

    # === Alert System ===

    async def _check_position_alerts(self, position: PositionSnapshot):
        """Check position for alert conditions"""

        # Check PNL alerts
        if abs(position.pnl_percentage) >= self.pnl_alert_threshold:
            level = AlertLevel.INFO if position.is_profitable else AlertLevel.WARNING
            await self._send_alert(
                level,
                f"{position.symbol} PNL Alert: {position.pnl_percentage:.2f}%",
                {"position": position}
            )

        # Check liquidation risk
        if position.liquidation_price > 0:
            price_distance = abs(position.mark_price - position.liquidation_price) / position.mark_price
            if price_distance < 0.05:  # Within 5% of liquidation
                await self._send_alert(
                    AlertLevel.CRITICAL,
                    f"{position.symbol} LIQUIDATION RISK!",
                    {"position": position, "distance": price_distance}
                )

    async def _send_alert(self, level: AlertLevel, message: str, data: Any = None):
        """Send alert to registered callbacks"""
        alert = {
            "level": level.value,
            "message": message,
            "data": data,
            "timestamp": datetime.now().isoformat()
        }

        # Log alert
        if level == AlertLevel.CRITICAL:
            self.logger.critical(message)
        elif level == AlertLevel.WARNING:
            self.logger.warning(message)
        else:
            self.logger.info(message)

        # Notify callbacks
        for callback in self.alert_callbacks:
            try:
                await callback(alert)
            except Exception as e:
                self.logger.error(f"Alert callback error: {e}")

    # === Account & Position Updates ===

    async def _update_account_snapshot(self):
        """Update account snapshot from API"""
        try:
            # Get account info
            account_info = await self.client.get_account_info()

            # Get positions
            positions_data = await self.client.get_position_risk()

            # Build positions list
            positions = []
            for pos_data in positions_data:
                if float(pos_data["positionAmt"]) != 0:
                    position = PositionSnapshot(
                        symbol=pos_data["symbol"],
                        side="LONG" if float(pos_data["positionAmt"]) > 0 else "SHORT",
                        quantity=abs(float(pos_data["positionAmt"])),
                        entry_price=float(pos_data["entryPrice"]),
                        mark_price=float(pos_data["markPrice"]),
                        unrealized_pnl=float(pos_data["unRealizedProfit"]),
                        realized_pnl=0,  # Not available in this endpoint
                        margin_ratio=float(pos_data.get("marginRatio", 0)),
                        liquidation_price=float(pos_data.get("liquidationPrice", 0))
                    )
                    positions.append(position)
                    self.positions[position.symbol] = position

            # Create account snapshot
            self.account_snapshot = AccountSnapshot(
                total_balance=float(account_info.get("totalWalletBalance", 0)),
                available_balance=float(account_info.get("availableBalance", 0)),
                total_unrealized_pnl=float(account_info.get("totalUnrealizedProfit", 0)),
                total_margin_ratio=float(account_info.get("totalMarginRatio", 0)),
                positions=positions
            )

            # Check account-level alerts
            await self._check_account_alerts()

        except Exception as e:
            self.logger.error(f"Failed to update account snapshot: {e}")

    async def _check_account_alerts(self):
        """Check account-level alert conditions"""
        if not self.account_snapshot:
            return

        # Check margin ratio
        margin_ratio = self.account_snapshot.total_margin_ratio

        if margin_ratio >= self.margin_critical_threshold:
            await self._send_alert(
                AlertLevel.CRITICAL,
                f"CRITICAL MARGIN LEVEL: {margin_ratio:.1%}",
                {"account": self.account_snapshot}
            )
        elif margin_ratio >= self.margin_warning_threshold:
            await self._send_alert(
                AlertLevel.WARNING,
                f"High margin usage: {margin_ratio:.1%}",
                {"account": self.account_snapshot}
            )

    # === Periodic Checks ===

    async def _periodic_checks(self):
        """Run periodic checks and updates"""
        while self.is_running:
            try:
                # Update account snapshot every 30 seconds
                await self._update_account_snapshot()

                # Check for stale positions
                await self._check_stale_positions()

                # Sleep
                await asyncio.sleep(30)

            except Exception as e:
                self.logger.error(f"Periodic check error: {e}")
                await asyncio.sleep(30)

    async def _check_stale_positions(self):
        """Check for positions that haven't been updated recently"""
        current_time = datetime.now()

        for symbol, position in self.positions.items():
            time_diff = (current_time - position.timestamp).seconds

            if time_diff > 300:  # 5 minutes
                self.logger.warning(f"Stale position detected: {symbol}")

    # === Analytics ===

    def get_total_pnl(self) -> float:
        """Get total PNL across all positions"""
        return sum(pos.unrealized_pnl for pos in self.positions.values())

    def get_position_count(self) -> int:
        """Get number of open positions"""
        return len(self.positions)

    def get_largest_position(self) -> Optional[PositionSnapshot]:
        """Get largest position by size"""
        if not self.positions:
            return None
        return max(
            self.positions.values(),
            key=lambda p: abs(p.quantity * p.entry_price)
        )

    def get_most_profitable(self) -> Optional[PositionSnapshot]:
        """Get most profitable position"""
        if not self.positions:
            return None
        return max(self.positions.values(), key=lambda p: p.unrealized_pnl)

    def get_most_losing(self) -> Optional[PositionSnapshot]:
        """Get biggest losing position"""
        if not self.positions:
            return None
        return min(self.positions.values(), key=lambda p: p.unrealized_pnl)

    def get_summary(self) -> Dict[str, Any]:
        """Get monitoring summary"""
        return {
            "account": {
                "total_balance": self.account_snapshot.total_balance if self.account_snapshot else 0,
                "available_balance": self.account_snapshot.available_balance if self.account_snapshot else 0,
                "margin_ratio": self.account_snapshot.total_margin_ratio if self.account_snapshot else 0
            },
            "positions": {
                "count": self.get_position_count(),
                "total_pnl": self.get_total_pnl(),
                "largest": self.get_largest_position(),
                "most_profitable": self.get_most_profitable(),
                "most_losing": self.get_most_losing()
            },
            "recent_orders": len(self.recent_orders),
            "timestamp": datetime.now().isoformat()
        }