# Waiting Mode Monitor - Integration Guide

## Overview

The Waiting Mode Monitor enables your trading bot to hold signals that have AI verdict contradicting the signal direction, continuously monitoring market conditions using technical indicators until favorable entry conditions are met.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Trading Orchestrator                     │
│                                                               │
│  1. Receive Signal                                           │
│  2. AI Analysis (3x perspectives)                            │
│  3. Check Trend Alignment                                    │
│                                                               │
│     ┌───────────────┬────────────────┐                      │
│     │  Aligned?     │  Not Aligned?  │                      │
│     │               │                 │                      │
│     ▼               ▼                 ▼                      │
│  Execute       Wait Mode?      Reject                       │
│  Trade         Enabled?                                      │
│                    │                                          │
│                    ▼                                          │
│            Add to Waiting Queue ───────────┐                │
└────────────────────────────────────────────┼────────────────┘
                                              │
                                              ▼
┌─────────────────────────────────────────────────────────────┐
│                  Waiting Mode Monitor                        │
│                                                               │
│  ┌──────────────────────────────────────────────────┐       │
│  │  Main Monitor Loop (every 1 min)                 │       │
│  │  - Checks for new waiting signals                │       │
│  │  - Spawns individual signal monitors             │       │
│  │  - Cleans up completed monitors                  │       │
│  └──────────────────────────────────────────────────┘       │
│                                                               │
│  ┌──────────────────────────────────────────────────┐       │
│  │  Signal Monitor (per signal, every 5 min)        │       │
│  │                                                   │       │
│  │  1. Fetch price history (100 candles)            │       │
│  │  2. Calculate indicators (RSI, MACD, BB)         │       │
│  │  3. Evaluate entry conditions                    │       │
│  │  4. Save snapshot to database                    │       │
│  │  5. Check if should execute                      │       │
│  │     - Score >= 50: Execute trade                 │       │
│  │     - Expired: Mark as expired                   │       │
│  │     - Otherwise: Continue monitoring             │       │
│  └──────────────────────────────────────────────────┘       │
└─────────────────────────────────────────────────────────────┘
```

## Integration Steps

### Step 1: Update Orchestrator

Modify `src/services/orchestrator.py` to integrate waiting mode:

```python
from src.services.waiting_mode.monitor import WaitingModeMonitor

class TradingOrchestrator:
    def __init__(self):
        # ... existing code ...

        # Add waiting mode monitor
        self.waiting_monitor = WaitingModeMonitor(self.binance)

    async def start(self):
        """Start the orchestrator and waiting mode monitor"""
        # ... existing code ...

        # Start waiting mode monitor
        await self.waiting_monitor.start()

    async def stop(self):
        """Stop the orchestrator and waiting mode monitor"""
        # ... existing code ...

        # Stop waiting mode monitor
        await self.waiting_monitor.stop()

    async def process_signal(self, message: str, db_session: AsyncSession):
        # ... existing parse and AI analysis code ...

        # 3. TREND ALIGNMENT CHECK
        if not analyzed_signal.trend_aligned:
            self.logger.warning(
                f"❌ Trend uyumsuz! AI: {analyzed_signal.ai_verdict}, "
                f"Sinyal: {parsed_signal.direction}"
            )

            # NEW: Check if waiting mode is enabled
            if self.config.waiting_mode_enabled:
                self.logger.info(
                    f"⏳ Adding signal to waiting queue for monitoring"
                )

                # Add to waiting queue
                waiting_signal = await self.waiting_monitor.add_to_waiting_queue(
                    signal=signal_model,  # Your SignalModel instance
                    ai_verdict=analyzed_signal.ai_verdict,
                    db_session=db_session
                )

                if waiting_signal:
                    self.logger.info(
                        f"✅ Signal added to waiting queue (ID: {waiting_signal.id})"
                    )
                    return None  # Don't execute immediately
                else:
                    self.logger.warning("Failed to add signal to waiting queue")

            await self._update_signal_status(db_session, parsed_signal, SignalStatus.REJECTED)
            return None

        # ... rest of execution code ...
```

### Step 2: Connect Execute Method

The monitor has a placeholder `_execute_waiting_signal` method. You need to connect it to your position manager:

```python
# In monitor.py, update _execute_waiting_signal method:

async def _execute_waiting_signal(
    self,
    waiting_signal: WaitingSignalModel,
    db_session: AsyncSession
) -> bool:
    """Execute a trade for a waiting signal."""
    try:
        # Get the original signal
        result = await db_session.execute(
            select(SignalModel).where(SignalModel.id == waiting_signal.signal_id)
        )
        signal = result.scalar_one_or_none()

        if not signal:
            self.logger.error(f"Original signal not found for waiting signal #{waiting_signal.id}")
            return False

        # Convert to SignalAnalyzed format
        from src.models.signal import SignalParsed, SignalAnalyzed

        parsed = SignalParsed(
            raw_message=signal.raw_message,
            parsed=True,
            coin=signal.coin,
            direction=signal.direction,
            leverage=signal.leverage,
            entry=signal.entry,
            entry_min=signal.entry_min,
            entry_max=signal.entry_max,
            targets=signal.targets,
            stoploss=signal.stoploss,
            symbol=signal.coin + "USDT"
        )

        analyzed = SignalAnalyzed(
            signal=parsed,
            ai_verdict=waiting_signal.ai_verdict,
            trend_aligned=True,  # We're executing because trend now aligns
            bullish_votes=2,  # Placeholder
            bearish_votes=1,
            consensus="WAITING_MODE_EXECUTION",
            analysis_1="Executed from waiting mode",
            analysis_2="Technical indicators favorable",
            analysis_3="Entry conditions met"
        )

        # Calculate position (you'll need access to orchestrator methods)
        # This is where you'd integrate with your position manager

        # Import and use position manager
        from src.trading.position_manager import PositionManager
        position_manager = PositionManager(self.binance)

        # Calculate position size
        balance = await self.binance.get_account_balance()
        risk_pct = self.config.risk_percentage / 100
        risk_amount = balance * risk_pct
        position_size = risk_amount
        quantity = position_size / waiting_signal.current_price

        from src.models.signal import SignalWithPosition
        signal_with_position = SignalWithPosition(
            signal=analyzed,
            quantity=quantity,
            position_size=position_size,
            risk_amount=risk_amount,
            entry_price=waiting_signal.current_price
        )

        # Open position
        position = await position_manager.open_position(signal_with_position)

        if position:
            # Save to database
            db_session.add(position)
            signal.status = SignalStatus.EXECUTED
            await db_session.commit()

            self.logger.info(
                f"🎯 Successfully executed waiting signal: {waiting_signal.symbol} "
                f"at {waiting_signal.current_price}"
            )
            return True
        else:
            self.logger.error("Failed to open position")
            return False

    except Exception as e:
        self.logger.error(f"Error executing waiting signal: {e}", exc_info=True)
        return False
```

### Step 3: Update Database Models

Make sure your database includes the waiting signal models:

```python
# In your database initialization (src/main.py or wherever you init DB):

from src.models.waiting_signal import WaitingSignalModel, IndicatorSnapshot, WaitingModeConfig

async def init_db():
    """Initialize database tables"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created")
```

### Step 4: Configuration

Ensure your `.env` file has waiting mode configuration:

```env
# Waiting Mode Configuration
WAITING_MODE_ENABLED=true
WAITING_MODE_MAX_POSITIONS=3
WAITING_MODE_MAX_HOURS=24
WAITING_MODE_CHECK_INTERVAL_MINUTES=5

# Technical Indicators
WAITING_MODE_RSI_PERIOD=14
WAITING_MODE_RSI_OVERSOLD=30.0
WAITING_MODE_RSI_OVERBOUGHT=70.0
WAITING_MODE_MACD_FAST=12
WAITING_MODE_MACD_SLOW=26
WAITING_MODE_MACD_SIGNAL=9
WAITING_MODE_BB_PERIOD=20
WAITING_MODE_BB_STD_DEV=2.0

# Entry Conditions
WAITING_MODE_MIN_CONDITIONS=2
WAITING_MODE_PRICE_IMPROVEMENT=0.5
```

## Usage Examples

### Check Active Waiting Signals

```python
async def check_waiting_status():
    async with AsyncSessionLocal() as db_session:
        active_signals = await waiting_monitor.get_active_waiting_signals(db_session)

        for ws in active_signals:
            print(f"Symbol: {ws.symbol}")
            print(f"Direction: {ws.direction}")
            print(f"Status: {ws.status}")
            print(f"Score: {ws.last_score}")
            print(f"Wait time: {ws.wait_time_hours:.1f} hours")
            print(f"Checks: {ws.total_checks}")
            print("---")
```

### View Indicator History

```python
async def view_indicator_history(waiting_signal_id: int):
    async with AsyncSessionLocal() as db_session:
        snapshots = await waiting_monitor.get_waiting_signal_history(
            waiting_signal_id,
            db_session,
            limit=20
        )

        for snapshot in snapshots:
            print(f"Time: {snapshot.timestamp}")
            print(f"Price: {snapshot.price}")
            print(f"RSI: {snapshot.rsi:.2f}")
            print(f"MACD: {snapshot.macd:.4f}")
            print(f"Score: {snapshot.overall_score:.1f}")
            print(f"Conditions: RSI={snapshot.rsi_condition_met}, "
                  f"MACD={snapshot.macd_condition_met}, "
                  f"BB={snapshot.bb_condition_met}")
            print("---")
```

### Cancel a Waiting Signal

```python
async def cancel_signal(waiting_signal_id: int):
    async with AsyncSessionLocal() as db_session:
        success = await waiting_monitor.cancel_waiting_signal(
            waiting_signal_id,
            db_session
        )

        if success:
            print(f"Signal #{waiting_signal_id} cancelled")
        else:
            print(f"Failed to cancel signal #{waiting_signal_id}")
```

## Monitoring Dashboard

You can create a simple dashboard to monitor waiting signals:

```python
async def waiting_mode_dashboard():
    """Display a live dashboard of waiting signals"""
    import os
    import time

    while True:
        os.system('clear')  # or 'cls' on Windows

        async with AsyncSessionLocal() as db_session:
            active = await waiting_monitor.get_active_waiting_signals(db_session)

            print("=" * 80)
            print("WAITING MODE MONITOR DASHBOARD")
            print("=" * 80)
            print(f"Active Signals: {len(active)}/{waiting_monitor.max_positions}")
            print("")

            for ws in active:
                print(f"Symbol: {ws.symbol:12} | Direction: {ws.direction:5}")
                print(f"Status: {ws.status.value:12} | Score: {ws.last_score:5.1f}/100")
                print(f"Wait Time: {ws.wait_time_hours:5.1f}h | Checks: {ws.total_checks:4}")
                print(f"Current Price: {ws.current_price:.4f}")

                if ws.rsi_value:
                    print(f"RSI: {ws.rsi_value:.2f} | MACD: {ws.macd_value:.4f} | "
                          f"BB: [{ws.bb_lower:.2f}, {ws.bb_middle:.2f}, {ws.bb_upper:.2f}]")

                print("-" * 80)

        await asyncio.sleep(30)  # Refresh every 30 seconds
```

## API Endpoints (Optional)

You can add REST API endpoints to manage waiting signals:

```python
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from src.core.database import get_db

router = APIRouter(prefix="/waiting-mode", tags=["waiting-mode"])

@router.get("/active")
async def get_active_waiting_signals(
    db: AsyncSession = Depends(get_db)
):
    """Get all active waiting signals"""
    signals = await waiting_monitor.get_active_waiting_signals(db)
    return {
        "count": len(signals),
        "signals": [
            {
                "id": ws.id,
                "symbol": ws.symbol,
                "direction": ws.direction,
                "status": ws.status.value,
                "score": ws.last_score,
                "wait_time_hours": ws.wait_time_hours,
                "total_checks": ws.total_checks
            }
            for ws in signals
        ]
    }

@router.get("/{waiting_signal_id}/history")
async def get_indicator_history(
    waiting_signal_id: int,
    limit: int = 50,
    db: AsyncSession = Depends(get_db)
):
    """Get indicator snapshot history"""
    snapshots = await waiting_monitor.get_waiting_signal_history(
        waiting_signal_id,
        db,
        limit
    )
    return {
        "waiting_signal_id": waiting_signal_id,
        "snapshots": [
            {
                "timestamp": s.timestamp.isoformat(),
                "price": s.price,
                "rsi": s.rsi,
                "macd": s.macd,
                "score": s.overall_score,
                "conditions": {
                    "rsi": s.rsi_condition_met,
                    "macd": s.macd_condition_met,
                    "bb": s.bb_condition_met,
                    "price": s.price_condition_met
                }
            }
            for s in snapshots
        ]
    }

@router.post("/{waiting_signal_id}/cancel")
async def cancel_waiting_signal(
    waiting_signal_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Cancel a waiting signal"""
    success = await waiting_monitor.cancel_waiting_signal(
        waiting_signal_id,
        db
    )
    return {"success": success}
```

## Testing

Test the waiting mode with a simple test signal:

```python
async def test_waiting_mode():
    """Test waiting mode functionality"""
    async with AsyncSessionLocal() as db_session:
        # Create a test signal
        test_signal = SignalModel(
            raw_message="Test signal",
            coin="BTC",
            direction=SignalDirection.LONG,
            leverage=10,
            entry_min=50000,
            entry_max=51000,
            entry=50500,
            targets=[52000, 53000, 54000],
            stoploss=49000,
            status=SignalStatus.PARSED
        )

        db_session.add(test_signal)
        await db_session.commit()
        await db_session.refresh(test_signal)

        # Add to waiting queue
        waiting_signal = await waiting_monitor.add_to_waiting_queue(
            signal=test_signal,
            ai_verdict="BEARISH",  # Contradicts LONG signal
            db_session=db_session
        )

        print(f"Created waiting signal: {waiting_signal.id}")

        # Wait a bit and check status
        await asyncio.sleep(60)

        await db_session.refresh(waiting_signal)
        print(f"Status: {waiting_signal.status}")
        print(f"Checks: {waiting_signal.total_checks}")
        print(f"Score: {waiting_signal.last_score}")
```

## Troubleshooting

### Monitor Not Starting

- Check that `WAITING_MODE_ENABLED=true` in your `.env` file
- Verify database tables are created
- Check logs for initialization errors

### No Snapshots Being Saved

- Verify Binance API access (klines endpoint)
- Check that price history is being fetched successfully
- Ensure sufficient historical data (need at least 50 candles)

### Signals Not Executing

- Check the scoring logic - may need to adjust thresholds
- Verify `_execute_waiting_signal` is properly integrated
- Check position manager is accessible

### High Memory Usage

- Reduce `WAITING_MODE_MAX_POSITIONS` to limit concurrent monitors
- Increase `WAITING_MODE_CHECK_INTERVAL_MINUTES` to reduce frequency
- Clean up old snapshots periodically

## Advanced Configuration

### Custom Indicator Weights

You can customize the scoring algorithm in `_calculate_entry_score` method to give different weights to different indicators based on your strategy.

### Symbol-Specific Configuration

Use the `WaitingModeConfig` model to set different parameters per symbol:

```python
async def set_symbol_config(symbol: str):
    async with AsyncSessionLocal() as db_session:
        config = WaitingModeConfig(
            symbol=symbol,
            enabled=True,
            rsi_period=14,
            rsi_oversold_long=35.0,  # More conservative for this symbol
            max_wait_hours=12,  # Shorter wait time
            check_interval_minutes=3  # More frequent checks
        )

        db_session.add(config)
        await db_session.commit()
```

## Performance Considerations

- Each waiting signal spawns an independent async task
- Check interval of 5 minutes is a good balance (300s * max_positions = reasonable load)
- Klines fetching is rate-limited through the Binance client
- Database writes are batched per check (1 snapshot per signal per check)

## Security

- Ensure your Binance API keys have appropriate permissions
- Use read-only keys for price data fetching if possible
- Monitor for unusual activity in waiting mode executions
- Set reasonable limits on max waiting positions

## Next Steps

1. Test in paper trading mode first
2. Monitor performance and adjust scoring thresholds
3. Analyze historical snapshots to optimize parameters
4. Consider adding machine learning for dynamic scoring
5. Add alerts for high-score signals waiting to execute
