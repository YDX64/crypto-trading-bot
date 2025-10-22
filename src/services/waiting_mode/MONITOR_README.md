# Waiting Mode Monitor

## Overview

The Waiting Mode Monitor is a sophisticated component that handles signals where the AI analysis contradicts the signal direction. Instead of rejecting these signals outright, the monitor places them in a waiting queue and continuously evaluates technical indicators to find an optimal entry point.

## Key Features

### 🎯 Smart Signal Management
- Automatically queues signals with contradicting AI verdicts
- Configurable maximum waiting positions (default: 3)
- Automatic expiration after max wait time (default: 24 hours)
- Manual cancellation support

### 📊 Technical Analysis
- **RSI (Relative Strength Index)**: Identifies overbought/oversold conditions
- **MACD (Moving Average Convergence Divergence)**: Detects trend changes
- **Bollinger Bands**: Measures volatility and price extremes
- Customizable indicator periods and thresholds

### ⚡ Concurrent Monitoring
- Independent async task per waiting signal
- Configurable check interval (default: 5 minutes)
- Efficient resource usage with task pooling
- Automatic cleanup of completed monitors

### 💾 Comprehensive Tracking
- Snapshot storage of all indicator values
- Historical analysis capabilities
- Condition tracking (which indicators are favorable)
- Score calculation (0-100) for entry timing

### 🔄 Automatic Execution
- Executes trade when score threshold is met (≥50/100)
- Integrates with existing position manager
- Updates all related database records
- Proper error handling and retry logic

## Architecture

```
WaitingModeMonitor
├── Main Monitor Loop (runs every 60s)
│   ├── Scans for new waiting signals
│   ├── Spawns signal monitors
│   └── Cleans up completed tasks
│
└── Signal Monitor (one per signal, runs every 5min)
    ├── Fetch price history (100 candles)
    ├── Calculate technical indicators
    ├── Evaluate entry conditions
    ├── Save snapshot to database
    └── Execute or continue monitoring
```

## Configuration

All configuration is done through environment variables in your `.env` file:

```env
# Enable/Disable waiting mode
WAITING_MODE_ENABLED=true

# Maximum concurrent waiting positions
WAITING_MODE_MAX_POSITIONS=3

# Maximum wait time in hours
WAITING_MODE_MAX_HOURS=24

# Check interval in minutes
WAITING_MODE_CHECK_INTERVAL_MINUTES=5

# RSI Settings
WAITING_MODE_RSI_PERIOD=14
WAITING_MODE_RSI_OVERSOLD=30.0
WAITING_MODE_RSI_OVERBOUGHT=70.0

# MACD Settings
WAITING_MODE_MACD_FAST=12
WAITING_MODE_MACD_SLOW=26
WAITING_MODE_MACD_SIGNAL=9

# Bollinger Bands Settings
WAITING_MODE_BB_PERIOD=20
WAITING_MODE_BB_STD_DEV=2.0

# Entry Conditions
WAITING_MODE_MIN_CONDITIONS=2
WAITING_MODE_PRICE_IMPROVEMENT=0.5
```

## Database Models

### WaitingSignalModel
Stores the main waiting signal information:
- Signal reference (FK to SignalModel)
- Symbol and direction
- Original entry range
- Current price and AI verdict
- Technical indicator values
- Status tracking
- Monitoring statistics

### IndicatorSnapshot
Stores historical snapshots of indicator values:
- Timestamp
- Price and volume
- All indicator values (RSI, MACD, BB)
- Condition flags (which indicators are favorable)
- Overall score

### WaitingModeConfig
Optional per-symbol configuration:
- Custom indicator settings
- Symbol-specific thresholds
- Timing parameters

## Usage

### Basic Integration

```python
from src.services.waiting_mode import WaitingModeMonitor
from src.trading.binance_client_improved import ImprovedBinanceClient

# Initialize
binance = ImprovedBinanceClient()
monitor = WaitingModeMonitor(binance)

# Start monitoring
await monitor.start()

# Add signal to queue
waiting_signal = await monitor.add_to_waiting_queue(
    signal=signal_model,
    ai_verdict="BEARISH",  # Contradicts LONG signal
    db_session=db_session
)

# Later, stop monitoring
await monitor.stop()
```

### Check Active Signals

```python
async with AsyncSessionLocal() as db_session:
    active = await monitor.get_active_waiting_signals(db_session)

    for ws in active:
        print(f"{ws.symbol}: Score {ws.last_score:.1f}/100, "
              f"Wait time: {ws.wait_time_hours:.1f}h")
```

### View Indicator History

```python
snapshots = await monitor.get_waiting_signal_history(
    waiting_signal_id=123,
    db_session=db_session,
    limit=20
)

for snapshot in snapshots:
    print(f"Time: {snapshot.timestamp}, Score: {snapshot.overall_score:.1f}")
```

### Cancel a Signal

```python
success = await monitor.cancel_waiting_signal(
    waiting_signal_id=123,
    db_session=db_session
)
```

## Scoring Algorithm

The monitor uses a sophisticated scoring algorithm (0-100) that evaluates multiple conditions:

### For LONG Positions

**RSI Conditions (max 35 points):**
- RSI < 30: +35 points (strongly oversold)
- RSI < 40: +25 points (oversold)
- RSI < 50: +15 points (below neutral)

**MACD Conditions (max 35 points):**
- Bullish crossover: +35 points
- Positive histogram: +20 points

**Bollinger Bands (max 30 points):**
- Within 10% of lower band: +30 points
- Within 25% of lower band: +20 points
- Below middle band: +10 points

**Execution Threshold:** Score ≥ 50

### For SHORT Positions

**RSI Conditions (max 35 points):**
- RSI > 70: +35 points (strongly overbought)
- RSI > 60: +25 points (overbought)
- RSI > 50: +15 points (above neutral)

**MACD Conditions (max 35 points):**
- Bearish crossover: +35 points
- Negative histogram: +20 points

**Bollinger Bands (max 30 points):**
- Within 10% of upper band: +30 points
- Within 25% of upper band: +20 points
- Above middle band: +10 points

**Execution Threshold:** Score ≥ 50

## Monitoring Lifecycle

```
┌─────────────────────────────────────────────┐
│  1. Signal Added to Queue                   │
│     Status: WAITING                         │
└────────────────┬────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────┐
│  2. Monitor Started                         │
│     Status: MONITORING                      │
│     - Check every 5 minutes                 │
│     - Calculate indicators                  │
│     - Save snapshots                        │
└────────┬────────────────────┬───────────────┘
         │                    │
         │ Score ≥ 50         │ Time > Max Wait
         ▼                    ▼
┌─────────────────┐  ┌─────────────────┐
│  3a. Executed   │  │  3b. Expired    │
│  Status: EXEC   │  │  Status: EXP    │
└─────────────────┘  └─────────────────┘
```

## Performance Considerations

### Resource Usage
- Each waiting signal: ~1MB memory
- Database writes: 1 snapshot per check per signal
- API calls: 1 klines request per check per signal
- CPU usage: Minimal (indicator calculations are efficient)

### Scalability
- **Max 3 concurrent signals** (default): Safe for most systems
- **5-minute check interval**: Balanced frequency
- **100 price candles**: Sufficient for accurate indicators
- **Async architecture**: Non-blocking operations

### Optimization Tips
1. Increase check interval for slower systems
2. Reduce max positions for lower-tier VPS
3. Use Redis for caching price data (optional)
4. Archive old snapshots periodically

## Error Handling

The monitor includes comprehensive error handling:

### Binance API Errors
- Rate limiting: Automatic backoff and retry
- Connection errors: Retry with exponential backoff
- Invalid symbols: Skip and log error

### Database Errors
- Session management: Proper cleanup on errors
- Concurrent access: Handled by SQLAlchemy
- Failed commits: Rollback and retry

### Indicator Calculation Errors
- Insufficient data: Skip check and continue
- Invalid values: Log warning and use defaults
- NaN/Inf values: Sanitized before storage

## Monitoring and Alerts

### Logging
All operations are logged with appropriate levels:
- INFO: Normal operations, signal additions, executions
- WARNING: Expiry, high scores not executing, API issues
- ERROR: Failed executions, database errors
- DEBUG: Detailed indicator values, check cycles

### Metrics
Consider tracking these metrics:
- Average wait time before execution
- Success rate (executed vs expired)
- Average score at execution
- Most common failing conditions

## Testing

Run the example script to test functionality:

```bash
# Run interactive examples
python src/services/waiting_mode/example_usage.py

# Or run specific examples programmatically
python -c "
import asyncio
from src.services.waiting_mode.example_usage import example_6_test_indicators
asyncio.run(example_6_test_indicators())
"
```

## Troubleshooting

### Monitor Not Starting
**Problem:** Monitor doesn't start when called
**Solution:**
- Check `WAITING_MODE_ENABLED=true` in `.env`
- Verify database tables are created
- Check logs for initialization errors

### No Snapshots Saved
**Problem:** Snapshots table remains empty
**Solution:**
- Verify Binance API access (test with example_6)
- Check sufficient price history exists
- Ensure minimum 50 candles available

### Signals Not Executing
**Problem:** High scores but no execution
**Solution:**
- Check `_execute_waiting_signal` integration
- Verify position manager is accessible
- Review score threshold (may need adjustment)

### High Memory Usage
**Problem:** Memory grows over time
**Solution:**
- Reduce `WAITING_MODE_MAX_POSITIONS`
- Increase `WAITING_MODE_CHECK_INTERVAL_MINUTES`
- Archive old snapshots (delete snapshots > 30 days)

### Database Locking
**Problem:** SQLite database locked errors
**Solution:**
- Use PostgreSQL for production
- Increase SQLite timeout
- Reduce concurrent operations

## Advanced Features

### Custom Scoring
You can customize the scoring algorithm by modifying `_calculate_entry_score()`:

```python
def _calculate_entry_score(self, indicators: IndicatorValues, direction: str) -> float:
    score = 0.0

    # Your custom logic here
    if direction == "LONG":
        # Weight RSI more heavily
        if indicators.rsi < 25:
            score += 50  # Double weight
        # Add volume analysis
        # Add custom indicators

    return min(score, 100.0)
```

### Per-Symbol Configuration
Set different parameters per symbol:

```python
# In your database initialization or admin panel
config = WaitingModeConfig(
    symbol="BTCUSDT",
    rsi_oversold_long=35.0,  # More conservative
    max_wait_hours=12,       # Shorter wait
    check_interval_minutes=3 # More frequent
)
db_session.add(config)
```

### Machine Learning Integration
The snapshot data is perfect for ML:

```python
# Export snapshot data for training
snapshots = db.query(IndicatorSnapshot).all()

features = pd.DataFrame([
    {
        'rsi': s.rsi,
        'macd': s.macd,
        'macd_histogram': s.macd_histogram,
        'bb_position': (s.price - s.bb_lower) / (s.bb_upper - s.bb_lower),
        'score': s.overall_score,
        'executed': s.waiting_signal.status == WaitingStatus.EXECUTED
    }
    for s in snapshots
])

# Train model to predict optimal execution timing
```

## Best Practices

1. **Start Conservative**: Use default settings initially
2. **Monitor Performance**: Track execution vs expiry ratio
3. **Backtest Settings**: Analyze historical snapshots to optimize
4. **Use in Paper Trading First**: Validate before live trading
5. **Set Reasonable Limits**: Don't exceed 5 waiting positions
6. **Archive Old Data**: Keep database size manageable
7. **Monitor API Usage**: Ensure you stay within Binance limits
8. **Log Everything**: Comprehensive logging aids debugging

## Future Enhancements

Potential improvements for consideration:

- [ ] Volume-based indicators (OBV, Volume Profile)
- [ ] Multi-timeframe analysis
- [ ] Sentiment analysis integration
- [ ] Dynamic scoring based on market conditions
- [ ] Machine learning for score threshold optimization
- [ ] WebSocket for real-time price updates
- [ ] Telegram notifications for high-score signals
- [ ] Advanced risk management (correlations, portfolio level)
- [ ] Backtesting framework for parameter optimization
- [ ] Dashboard UI for monitoring

## Contributing

When contributing to the waiting mode monitor:

1. Follow existing code style and patterns
2. Add comprehensive docstrings
3. Include error handling
4. Write tests for new features
5. Update documentation
6. Consider backward compatibility

## License

This module is part of the trading bot and follows the same license as the main project.

## Support

For issues or questions:
1. Check this README and integration guide
2. Review example_usage.py for common patterns
3. Check logs for error messages
4. Review snapshot data for unexpected values
5. Consult with the development team

---

**Version:** 1.0.0
**Last Updated:** 2025-01-22
**Status:** Production Ready
