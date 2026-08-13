"""
Waiting Mode Monitor

Monitors signals in waiting mode by continuously evaluating technical indicators
to find optimal entry points when AI verdict contradicts signal direction.

Features:
- Continuous monitoring every N minutes (configurable)
- Technical indicator evaluation (RSI, MACD, Bollinger Bands)
- Snapshot storage for analysis
- Automatic execution when conditions are met
- Expiration after max wait time
- Concurrent handling of multiple waiting signals
"""

import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from loguru import logger

from src.core.config import settings
from src.core.database import AsyncSessionLocal
from src.models.signal import SignalModel, SignalStatus, SignalDirection
from src.models.waiting_signal import (
    WaitingSignalModel,
    WaitingStatus,
    IndicatorSnapshot,
    WaitingModeConfig
)
from src.services.waiting_mode.indicators import (
    calculate_all_indicators,
    is_good_entry_point,
    IndicatorValues,
    get_indicator_summary
)
from src.trading.binance_client_improved import ImprovedBinanceClient


class WaitingModeMonitor:
    """
    Monitor for signals in waiting mode.

    This class handles the lifecycle of waiting signals:
    1. Add signals to waiting queue when trend misaligned
    2. Continuously monitor with technical indicators
    3. Execute trade when conditions are favorable
    4. Expire after max wait time
    """

    def __init__(self, binance_client: Optional[ImprovedBinanceClient] = None):
        """
        Initialize the waiting mode monitor.

        Args:
            binance_client: Optional Binance client instance. If not provided,
                          a new instance will be created.
        """
        self.binance = binance_client or ImprovedBinanceClient()
        self.logger = logger
        self.config = settings

        # Monitoring state
        self.is_running = False
        self.monitor_task: Optional[asyncio.Task] = None
        self.active_monitors: Dict[int, asyncio.Task] = {}  # waiting_signal_id -> task

        # Configuration from settings
        self.enabled = self.config.waiting_mode_enabled
        self.max_positions = self.config.waiting_mode_max_positions
        self.check_interval_minutes = self.config.waiting_mode_check_interval_minutes
        self.max_wait_hours = self.config.waiting_mode_max_hours

        self.logger.info(
            f"Waiting Mode Monitor initialized: "
            f"enabled={self.enabled}, max_positions={self.max_positions}, "
            f"check_interval={self.check_interval_minutes}min, max_wait={self.max_wait_hours}h"
        )

    async def start(self):
        """Start the waiting mode monitor."""
        if not self.enabled:
            self.logger.info("Waiting mode is disabled. Monitor will not start.")
            return

        if self.is_running:
            self.logger.warning("Monitor is already running")
            return

        self.is_running = True
        self.monitor_task = asyncio.create_task(self._monitor_loop())
        self.logger.info("✅ Waiting Mode Monitor started")

    async def stop(self):
        """Stop the waiting mode monitor and all active signal monitors."""
        self.is_running = False

        # Cancel main monitor task
        if self.monitor_task:
            self.monitor_task.cancel()
            try:
                await self.monitor_task
            except asyncio.CancelledError:
                pass

        # Cancel all active signal monitors
        for task in self.active_monitors.values():
            task.cancel()

        if self.active_monitors:
            await asyncio.gather(*self.active_monitors.values(), return_exceptions=True)

        self.active_monitors.clear()
        self.logger.info("🛑 Waiting Mode Monitor stopped")

    async def add_to_waiting_queue(
        self,
        signal: SignalModel,
        ai_verdict: str,
        db_session: AsyncSession
    ) -> Optional[WaitingSignalModel]:
        """
        Add a signal to the waiting queue.

        Args:
            signal: The signal model to add to waiting queue
            ai_verdict: AI's verdict (BULLISH/BEARISH)
            db_session: Database session

        Returns:
            Created WaitingSignalModel or None if max positions reached
        """
        if not self.enabled:
            self.logger.info("Waiting mode disabled, signal not added to queue")
            return None

        # Check max waiting positions
        result = await db_session.execute(
            select(WaitingSignalModel).where(
                WaitingSignalModel.status.in_([WaitingStatus.WAITING, WaitingStatus.MONITORING])
            )
        )
        active_waiting = result.scalars().all()

        if len(active_waiting) >= self.max_positions:
            self.logger.warning(
                f"⚠️ Max waiting positions reached ({len(active_waiting)}/{self.max_positions}). "
                f"Signal not added to queue."
            )
            return None

        # Get current price
        current_price = await self.binance.get_current_price(signal.coin + "USDT")
        if not current_price:
            self.logger.error(f"Could not fetch current price for {signal.coin}")
            return None

        # Create waiting signal
        waiting_signal = WaitingSignalModel(
            signal_id=signal.id,
            symbol=signal.coin + "USDT",
            direction=signal.direction.value,
            original_entry_min=signal.entry_min or signal.entry,
            original_entry_max=signal.entry_max or signal.entry,
            current_price=current_price,
            ai_verdict=ai_verdict,
            max_wait_hours=self.max_wait_hours,
            check_interval_minutes=self.check_interval_minutes,
            status=WaitingStatus.WAITING,
            monitoring_started_at=datetime.utcnow()
        )

        db_session.add(waiting_signal)

        # Update signal status
        signal.status = SignalStatus.WAITING

        await db_session.commit()
        await db_session.refresh(waiting_signal)

        self.logger.info(
            f"➕ Signal added to waiting queue: {waiting_signal.symbol} {waiting_signal.direction} "
            f"(AI: {ai_verdict}, Price: {current_price:.4f})"
        )

        return waiting_signal

    async def _monitor_loop(self):
        """Main monitoring loop - checks for new waiting signals and manages monitors."""
        self.logger.info("📊 Starting waiting mode monitor loop")

        while self.is_running:
            try:
                async with AsyncSessionLocal() as db_session:
                    # Get all active waiting signals
                    result = await db_session.execute(
                        select(WaitingSignalModel).where(
                            WaitingSignalModel.status.in_([
                                WaitingStatus.WAITING,
                                WaitingStatus.MONITORING
                            ])
                        )
                    )
                    waiting_signals = result.scalars().all()

                    if waiting_signals:
                        self.logger.debug(
                            f"📊 Monitoring {len(waiting_signals)} waiting signals, "
                            f"{len(self.active_monitors)} active monitors"
                        )

                    # Start monitors for new signals
                    for ws in waiting_signals:
                        if ws.id not in self.active_monitors:
                            # Start a new monitor task for this signal
                            task = asyncio.create_task(self._monitor_signal(ws.id))
                            self.active_monitors[ws.id] = task
                            self.logger.info(f"🔍 Started monitor for {ws.symbol}")

                    # Clean up completed monitors
                    completed = [
                        ws_id for ws_id, task in self.active_monitors.items()
                        if task.done()
                    ]
                    for ws_id in completed:
                        del self.active_monitors[ws_id]

                # Check every minute for new signals
                await asyncio.sleep(60)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error in monitor loop: {e}", exc_info=True)
                await asyncio.sleep(10)

        self.logger.info("📊 Monitor loop stopped")

    async def _monitor_signal(self, waiting_signal_id: int):
        """
        Monitor a single waiting signal.

        This task runs continuously until:
        - Favorable conditions are met (executes trade)
        - Signal expires (max wait time reached)
        - Signal is cancelled

        Args:
            waiting_signal_id: ID of the waiting signal to monitor
        """
        self.logger.info(f"🔍 Starting monitor for waiting signal #{waiting_signal_id}")

        try:
            while True:
                async with AsyncSessionLocal() as db_session:
                    # Fetch waiting signal
                    result = await db_session.execute(
                        select(WaitingSignalModel).where(
                            WaitingSignalModel.id == waiting_signal_id
                        )
                    )
                    waiting_signal = result.scalar_one_or_none()

                    if not waiting_signal:
                        self.logger.warning(f"Waiting signal #{waiting_signal_id} not found")
                        break

                    # Check if cancelled
                    if waiting_signal.status == WaitingStatus.CANCELLED:
                        self.logger.info(f"Signal {waiting_signal.symbol} was cancelled")
                        break

                    # Check if expired
                    if waiting_signal.is_expired:
                        self.logger.warning(
                            f"⏰ Signal {waiting_signal.symbol} expired after "
                            f"{waiting_signal.wait_time_hours:.1f} hours"
                        )
                        waiting_signal.status = WaitingStatus.EXPIRED
                        await db_session.commit()
                        break

                    # Update status to monitoring
                    if waiting_signal.status == WaitingStatus.WAITING:
                        waiting_signal.status = WaitingStatus.MONITORING
                        await db_session.commit()

                    # Evaluate indicators
                    should_execute, score, indicators, current_price = await self._evaluate_entry_conditions(
                        waiting_signal
                    )

                    # Save snapshot (reuse the current_price already fetched above
                    # instead of hitting Binance again)
                    await self._save_indicator_snapshot(
                        waiting_signal, indicators, score, db_session, current_price
                    )

                    # Update waiting signal stats
                    waiting_signal.last_checked_at = datetime.utcnow()
                    waiting_signal.total_checks += 1
                    waiting_signal.last_score = score

                    if should_execute:
                        waiting_signal.conditions_met_count += 1

                    await db_session.commit()

                    # Check if we should execute
                    if should_execute:
                        self.logger.info(
                            f"✅ Favorable conditions met for {waiting_signal.symbol}! "
                            f"Score: {score:.1f}/100"
                        )

                        # Execute the trade
                        success = await self._execute_waiting_signal(waiting_signal, db_session)

                        if success:
                            waiting_signal.status = WaitingStatus.EXECUTED
                            waiting_signal.executed_at = datetime.utcnow()
                            # Fill price must be the real market price at execution
                            # time, not the Bollinger middle band (SMA) - the SMA is
                            # a historical average, not what was actually paid.
                            waiting_signal.executed_price = (
                                current_price if current_price is not None else indicators.bb_middle
                            )
                            await db_session.commit()
                            self.logger.info(f"🎯 Successfully executed {waiting_signal.symbol}")
                            break
                        else:
                            self.logger.warning(
                                f"Failed to execute {waiting_signal.symbol}, will continue monitoring"
                            )

                    # Log progress
                    if waiting_signal.total_checks % 10 == 0:  # Every 10 checks
                        self.logger.info(
                            f"📊 {waiting_signal.symbol}: "
                            f"{waiting_signal.total_checks} checks, "
                            f"last score: {score:.1f}/100, "
                            f"wait time: {waiting_signal.wait_time_hours:.1f}h"
                        )

                # Wait for next check
                await asyncio.sleep(self.check_interval_minutes * 60)

        except asyncio.CancelledError:
            self.logger.info(f"Monitor for waiting signal #{waiting_signal_id} cancelled")
            raise
        except Exception as e:
            self.logger.error(
                f"Error monitoring waiting signal #{waiting_signal_id}: {e}",
                exc_info=True
            )

    async def _evaluate_entry_conditions(
        self,
        waiting_signal: WaitingSignalModel
    ) -> Tuple[bool, float, IndicatorValues, Optional[float]]:
        """
        Evaluate if current market conditions are favorable for entry.

        Args:
            waiting_signal: The waiting signal to evaluate

        Returns:
            Tuple of (should_execute, score, indicators, current_price)
            - should_execute: True if conditions are met
            - score: Overall score (0-100)
            - indicators: Calculated indicator values
            - current_price: The real market price used for the evaluation
              (None if it could not be fetched)
        """
        symbol = waiting_signal.symbol
        direction = waiting_signal.direction

        # Fetch historical prices for indicators
        prices = await self._fetch_price_history(
            symbol,
            limit=100  # Get 100 candles for indicator calculation
        )

        if not prices or len(prices) < 50:
            self.logger.warning(f"Insufficient price data for {symbol}")
            return False, 0.0, IndicatorValues(), None

        # Fetch the real, current market price. This is used for Bollinger
        # Band proximity checks, scoring, and (if executed) the fill price -
        # it must NOT be confused with bb_middle (the SMA), which is a
        # historical average and not an actual tradable price.
        current_price = await self.binance.get_current_price(symbol)
        if current_price is None:
            self.logger.warning(
                f"Could not fetch current price for {symbol}, falling back to "
                f"last close from candle history"
            )
            current_price = prices[-1]

        # Calculate all indicators
        indicators = calculate_all_indicators(
            prices,
            rsi_period=self.config.waiting_mode_rsi_period,
            macd_fast=self.config.waiting_mode_macd_fast,
            macd_slow=self.config.waiting_mode_macd_slow,
            macd_signal=self.config.waiting_mode_macd_signal,
            bb_period=self.config.waiting_mode_bb_period,
            bb_std=self.config.waiting_mode_bb_std_dev
        )

        if not indicators.is_valid():
            self.logger.warning(f"Invalid indicators for {symbol}: {indicators.error_message}")
            return False, 0.0, indicators, current_price

        # Check if it's a good entry point (uses the real current price for
        # the Bollinger Band proximity check)
        is_good, reason = is_good_entry_point(
            indicators,
            direction,
            rsi_oversold=self.config.waiting_mode_rsi_oversold,
            rsi_overbought=self.config.waiting_mode_rsi_overbought,
            current_price=current_price
        )

        # Calculate score (0-100) using the real current price
        score = self._calculate_entry_score(indicators, direction, current_price)

        # Count how many independent entry conditions (RSI / MACD / Bollinger /
        # price improvement) are currently satisfied
        conditions_met, _ = self._count_conditions_met(
            waiting_signal, indicators, direction, current_price
        )

        # Log evaluation
        if is_good:
            self.logger.info(
                f"🎯 {symbol}: {reason}\n"
                f"{get_indicator_summary(indicators)}"
            )
        else:
            self.logger.debug(f"⏳ {symbol}: {reason} (score: {score:.1f}/100)")

        # Determine if we should execute: require at least
        # `waiting_mode_min_conditions` independent conditions to be met,
        # as configured in settings (instead of a hardcoded score threshold).
        should_execute = conditions_met >= self.config.waiting_mode_min_conditions

        return should_execute, score, indicators, current_price

    def _count_conditions_met(
        self,
        waiting_signal: WaitingSignalModel,
        indicators: IndicatorValues,
        direction: str,
        current_price: Optional[float]
    ) -> Tuple[int, Dict[str, bool]]:
        """
        Count how many independent entry conditions are currently satisfied.

        Conditions evaluated:
        - RSI: oversold (LONG) / overbought (SHORT)
        - MACD: favorable crossover or histogram direction
        - Bollinger Bands: price near the relevant band (uses the real
          current price, not the SMA)
        - Price improvement: current price has moved at least
          `settings.waiting_mode_price_improvement` percent past the
          original signal's entry range, in the favorable direction

        Args:
            waiting_signal: The waiting signal being evaluated
            indicators: Calculated indicator values
            direction: "LONG" or "SHORT"
            current_price: Real current market price (may be None)

        Returns:
            Tuple of (count_of_conditions_met, {condition_name: bool})
        """
        direction = direction.upper()

        if direction == "LONG":
            rsi_met = indicators.rsi is not None and indicators.rsi < 40
            macd_met = indicators.is_bullish_crossover() or (
                indicators.macd_histogram is not None and indicators.macd_histogram > 0
            )
            bb_met = indicators.near_bb_lower(threshold_pct=25, current_price=current_price)
        else:  # SHORT
            rsi_met = indicators.rsi is not None and indicators.rsi > 60
            macd_met = indicators.is_bearish_crossover() or (
                indicators.macd_histogram is not None and indicators.macd_histogram < 0
            )
            bb_met = indicators.near_bb_upper(threshold_pct=25, current_price=current_price)

        # Price improvement: has the price moved at least
        # waiting_mode_price_improvement percent past the original entry
        # range, in the direction favorable to entry?
        price_met = False
        if current_price is not None:
            improvement_fraction = self.config.waiting_mode_price_improvement / 100
            if direction == "LONG":
                price_met = current_price <= waiting_signal.original_entry_min * (1 - improvement_fraction)
            else:  # SHORT
                price_met = current_price >= waiting_signal.original_entry_max * (1 + improvement_fraction)

        conditions = {
            "rsi": rsi_met,
            "macd": macd_met,
            "bollinger": bb_met,
            "price": price_met,
        }
        return sum(1 for met in conditions.values() if met), conditions

    def _calculate_entry_score(
        self,
        indicators: IndicatorValues,
        direction: str,
        current_price: Optional[float] = None
    ) -> float:
        """
        Calculate an entry score (0-100) based on technical indicators.

        Args:
            indicators: Calculated indicator values
            direction: Signal direction (LONG/SHORT)
            current_price: Real current market price, used for the Bollinger
                Band proximity component. If omitted, falls back to
                bb_middle (the SMA) as a rough proxy, which is not an actual
                tradable price and should be avoided when the real price is
                available.

        Returns:
            Score from 0 to 100
        """
        score = 0.0
        max_score = 100.0

        direction = direction.upper()

        if direction == "LONG":
            # RSI conditions (max 35 points)
            if indicators.rsi is not None:
                if indicators.rsi < 30:
                    score += 35  # Strongly oversold
                elif indicators.rsi < 40:
                    score += 25  # Oversold
                elif indicators.rsi < 50:
                    score += 15  # Below neutral

            # MACD conditions (max 35 points)
            if indicators.is_bullish_crossover():
                score += 35  # Bullish crossover
            elif indicators.macd_histogram and indicators.macd_histogram > 0:
                score += 20  # Positive histogram

            # Bollinger Bands (max 30 points)
            if indicators.bb_lower and indicators.bb_middle and indicators.bb_upper:
                # Check how close to lower band (as percentage of band width)
                band_width = indicators.bb_upper - indicators.bb_lower
                if band_width > 0:
                    # Real market price - NOT the SMA (bb_middle) - falling
                    # back to bb_middle only if no real price was supplied.
                    price = current_price if current_price is not None else indicators.bb_middle
                    distance_from_lower = (price - indicators.bb_lower) / band_width

                    if distance_from_lower < 0.1:  # Within 10% of lower band
                        score += 30
                    elif distance_from_lower < 0.25:  # Within 25% of lower band
                        score += 20
                    elif distance_from_lower < 0.4:  # Below middle
                        score += 10

        elif direction == "SHORT":
            # RSI conditions (max 35 points)
            if indicators.rsi is not None:
                if indicators.rsi > 70:
                    score += 35  # Strongly overbought
                elif indicators.rsi > 60:
                    score += 25  # Overbought
                elif indicators.rsi > 50:
                    score += 15  # Above neutral

            # MACD conditions (max 35 points)
            if indicators.is_bearish_crossover():
                score += 35  # Bearish crossover
            elif indicators.macd_histogram and indicators.macd_histogram < 0:
                score += 20  # Negative histogram

            # Bollinger Bands (max 30 points)
            if indicators.bb_lower and indicators.bb_middle and indicators.bb_upper:
                band_width = indicators.bb_upper - indicators.bb_lower
                if band_width > 0:
                    # Real market price - NOT the SMA (bb_middle) - falling
                    # back to bb_middle only if no real price was supplied.
                    price = current_price if current_price is not None else indicators.bb_middle
                    distance_from_upper = (indicators.bb_upper - price) / band_width

                    if distance_from_upper < 0.1:  # Within 10% of upper band
                        score += 30
                    elif distance_from_upper < 0.25:  # Within 25% of upper band
                        score += 20
                    elif distance_from_upper < 0.4:  # Above middle
                        score += 10

        # Normalize to 0-100
        return min(score, max_score)

    async def _save_indicator_snapshot(
        self,
        waiting_signal: WaitingSignalModel,
        indicators: IndicatorValues,
        score: float,
        db_session: AsyncSession,
        current_price: Optional[float] = None
    ):
        """
        Save a snapshot of current indicator values.

        Args:
            waiting_signal: The waiting signal being monitored
            indicators: Current indicator values
            score: Overall entry score
            db_session: Database session
            current_price: Real current market price. When provided (the
                normal path, coming from `_evaluate_entry_conditions`, which
                already fetched it this cycle), it is reused instead of
                making another Binance request. If omitted, it is fetched
                here.
        """
        if not indicators.is_valid():
            return

        direction = waiting_signal.direction.upper()

        if current_price is None:
            current_price = await self.binance.get_current_price(waiting_signal.symbol)

        # Determine which conditions are met (RSI / MACD / Bollinger / price
        # improvement), using the real current price for the Bollinger Band
        # proximity and price-improvement checks.
        conditions_met, condition_flags = self._count_conditions_met(
            waiting_signal, indicators, direction, current_price
        )

        # Create snapshot
        snapshot = IndicatorSnapshot(
            waiting_signal_id=waiting_signal.id,
            timestamp=datetime.utcnow(),
            price=current_price if current_price is not None else indicators.bb_middle,
            rsi=indicators.rsi,
            macd=indicators.macd,
            macd_signal=indicators.macd_signal,
            macd_histogram=indicators.macd_histogram,
            bb_upper=indicators.bb_upper,
            bb_middle=indicators.bb_middle,
            bb_lower=indicators.bb_lower,
            rsi_condition_met=condition_flags["rsi"],
            macd_condition_met=condition_flags["macd"],
            bb_condition_met=condition_flags["bollinger"],
            price_condition_met=condition_flags["price"],
            overall_score=score
        )

        db_session.add(snapshot)

        # Also update waiting signal with latest values
        waiting_signal.current_price = current_price if current_price is not None else indicators.bb_middle
        waiting_signal.rsi_value = indicators.rsi
        waiting_signal.macd_value = indicators.macd
        waiting_signal.macd_signal = indicators.macd_signal
        waiting_signal.macd_histogram = indicators.macd_histogram
        waiting_signal.bb_upper = indicators.bb_upper
        waiting_signal.bb_middle = indicators.bb_middle
        waiting_signal.bb_lower = indicators.bb_lower

    async def _fetch_price_history(
        self,
        symbol: str,
        interval: str = "5m",
        limit: int = 100
    ) -> List[float]:
        """
        Fetch historical price data from Binance.

        Args:
            symbol: Trading symbol (e.g., BTCUSDT)
            interval: Candlestick interval (1m, 5m, 15m, 1h, etc.)
            limit: Number of candles to fetch

        Returns:
            List of closing prices (most recent last)
        """
        try:
            params = {
                "symbol": symbol,
                "interval": interval,
                "limit": limit
            }

            response = await self.binance._request_with_retry(
                "GET",
                "/fapi/v1/klines",
                params=params,
                signed=False
            )

            # Extract closing prices from klines
            # Kline format: [open_time, open, high, low, close, volume, close_time, ...]
            prices = [float(kline[4]) for kline in response]  # Index 4 is close price

            self.logger.debug(f"Fetched {len(prices)} price candles for {symbol}")

            return prices

        except Exception as e:
            self.logger.error(f"Error fetching price history for {symbol}: {e}")
            return []

    async def _execute_waiting_signal(
        self,
        waiting_signal: WaitingSignalModel,
        db_session: AsyncSession
    ) -> bool:
        """
        Execute a trade for a waiting signal.

        NOT YET WIRED UP: this method does not open a real position. Actual
        order placement requires integrating with the position manager
        (e.g. `await self.position_manager.open_position(...)`), which is a
        separate piece of work. Until that integration exists, this method
        deliberately always returns False instead of a real order call or a
        silent `return True` - reporting success here without ever
        submitting an order would make the rest of the system believe a
        trade was opened when it wasn't, corrupting position tracking, PnL,
        and risk limits.

        Args:
            waiting_signal: The waiting signal to execute
            db_session: Database session

        Returns:
            Always False until position_manager integration is implemented.
        """
        self.logger.error(
            f"⛔ Waiting signal #{waiting_signal.id} ({waiting_signal.symbol} "
            f"{waiting_signal.direction}) reached favorable entry conditions at "
            f"price {waiting_signal.current_price}, but execution was NOT performed: "
            f"position_manager integration is not implemented yet (separate work item). "
            f"Refusing to report success to avoid a phantom/untracked position."
        )

        # Keep the signal in an honest, still-monitored state rather than
        # marking it EXECUTED (which would be false) or EXPIRED/CANCELLED
        # (which would drop it prematurely). It will be re-evaluated on the
        # next check interval once real execution is wired up.
        waiting_signal.status = WaitingStatus.MONITORING

        return False

    async def cancel_waiting_signal(
        self,
        waiting_signal_id: int,
        db_session: AsyncSession
    ) -> bool:
        """
        Cancel a waiting signal.

        Args:
            waiting_signal_id: ID of the waiting signal to cancel
            db_session: Database session

        Returns:
            True if cancelled successfully, False otherwise
        """
        result = await db_session.execute(
            select(WaitingSignalModel).where(
                WaitingSignalModel.id == waiting_signal_id
            )
        )
        waiting_signal = result.scalar_one_or_none()

        if not waiting_signal:
            self.logger.warning(f"Waiting signal #{waiting_signal_id} not found")
            return False

        if waiting_signal.status in [WaitingStatus.EXECUTED, WaitingStatus.EXPIRED]:
            self.logger.warning(
                f"Cannot cancel waiting signal #{waiting_signal_id} "
                f"in status {waiting_signal.status}"
            )
            return False

        waiting_signal.status = WaitingStatus.CANCELLED
        await db_session.commit()

        self.logger.info(f"❌ Cancelled waiting signal #{waiting_signal_id}")
        return True

    async def get_active_waiting_signals(
        self,
        db_session: AsyncSession
    ) -> List[WaitingSignalModel]:
        """
        Get all active waiting signals.

        Args:
            db_session: Database session

        Returns:
            List of active waiting signals
        """
        result = await db_session.execute(
            select(WaitingSignalModel).where(
                WaitingSignalModel.status.in_([
                    WaitingStatus.WAITING,
                    WaitingStatus.MONITORING
                ])
            )
        )
        return result.scalars().all()

    async def get_waiting_signal_history(
        self,
        waiting_signal_id: int,
        db_session: AsyncSession,
        limit: int = 50
    ) -> List[IndicatorSnapshot]:
        """
        Get indicator snapshot history for a waiting signal.

        Args:
            waiting_signal_id: ID of the waiting signal
            db_session: Database session
            limit: Maximum number of snapshots to return

        Returns:
            List of indicator snapshots, ordered by timestamp descending
        """
        result = await db_session.execute(
            select(IndicatorSnapshot)
            .where(IndicatorSnapshot.waiting_signal_id == waiting_signal_id)
            .order_by(IndicatorSnapshot.timestamp.desc())
            .limit(limit)
        )
        return result.scalars().all()
