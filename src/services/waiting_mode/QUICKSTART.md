# Waiting Mode Monitor - Quick Start Guide

## 🚀 Get Started in 5 Minutes

### Step 1: Validate Installation (30 seconds)

```bash
# Run validation script
python src/services/waiting_mode/validate.py
```

Expected output:
```
✅ Import Check: All required modules imported successfully
✅ Configuration Check: All 7 required settings present
✅ Database Check: All required database tables exist
✅ Binance Client Check: Successfully connected to Binance API
✅ Indicator Calculation Check: All indicators calculated
✅ Monitor Initialization Check: Monitor initialized successfully
✅ Price Fetching Check: Successfully fetched 100 price candles
✅ Scoring Algorithm Check: Scores calculated correctly
✅ Monitor Lifecycle Check: Monitor start/stop lifecycle works

Total: 9/9 tests passed
🎉 All validation tests passed! Monitor is ready to use.
```

If any tests fail, see troubleshooting section in MONITOR_README.md.

### Step 2: Update Environment Variables (1 minute)

Add to your `.env` file:

```env
# Waiting Mode - Quick Start Defaults
WAITING_MODE_ENABLED=true
WAITING_MODE_MAX_POSITIONS=3
WAITING_MODE_MAX_HOURS=24
WAITING_MODE_CHECK_INTERVAL_MINUTES=5

# Indicators (defaults are good to start)
WAITING_MODE_RSI_PERIOD=14
WAITING_MODE_RSI_OVERSOLD=30.0
WAITING_MODE_RSI_OVERBOUGHT=70.0
WAITING_MODE_MACD_FAST=12
WAITING_MODE_MACD_SLOW=26
WAITING_MODE_MACD_SIGNAL=9
WAITING_MODE_BB_PERIOD=20
WAITING_MODE_BB_STD_DEV=2.0
WAITING_MODE_MIN_CONDITIONS=2
WAITING_MODE_PRICE_IMPROVEMENT=0.5
```

### Step 3: Update Your Orchestrator (2 minutes)

Edit `src/services/orchestrator.py`:

```python
# At the top, add import
from src.services.waiting_mode import WaitingModeMonitor

class TradingOrchestrator:
    def __init__(self):
        # ... existing code ...

        # ADD THIS LINE
        self.waiting_monitor = WaitingModeMonitor(self.binance)

    async def start(self):
        # ... existing code ...

        # ADD THIS LINE
        await self.waiting_monitor.start()

    async def stop(self):
        # ... existing code ...

        # ADD THIS LINE
        await self.waiting_monitor.stop()

    async def process_signal(self, message: str, db_session: AsyncSession):
        # ... parse and analyze code ...

        # FIND THIS SECTION (around line 127):
        if not analyzed_signal.trend_aligned:
            self.logger.warning(
                f"❌ Trend uyumsuz! AI: {analyzed_signal.ai_verdict}, "
                f"Sinyal: {parsed_signal.direction}"
            )

            # ADD THIS BLOCK:
            if self.config.waiting_mode_enabled:
                self.logger.info("⏳ Adding to waiting mode...")
                waiting_signal = await self.waiting_monitor.add_to_waiting_queue(
                    signal=signal_model,  # Your SignalModel instance
                    ai_verdict=analyzed_signal.ai_verdict,
                    db_session=db_session
                )
                if waiting_signal:
                    self.logger.info(f"✅ Added to waiting queue (ID: {waiting_signal.id})")
                    return None

            # ... rest of code ...
```

### Step 4: Test It (1 minute)

Run the example script:

```bash
python src/services/waiting_mode/example_usage.py
```

Choose option 6 to test indicator calculation:
```
Select an example to run:
6. Test Indicators - Calculate indicators for a symbol

Testing indicator calculation for BTCUSDT
✅ Fetched 100 price candles
✅ Indicators calculated successfully

=== Technical Indicators Summary ===
RSI: 45.23 (Neutral)
MACD: 0.0234
MACD Signal: 0.0189
MACD Histogram: 0.0045 (Neutral)
Bollinger Upper: 50234.56
Bollinger Middle: 50123.45
Bollinger Lower: 50012.34
Bollinger Bandwidth: 0.0044

LONG entry score: 35.0/100
SHORT entry score: 25.0/100
```

### Step 5: Monitor in Production (ongoing)

View active waiting signals:

```python
from src.core.database import AsyncSessionLocal

async with AsyncSessionLocal() as db:
    active = await orchestrator.waiting_monitor.get_active_waiting_signals(db)

    for ws in active:
        print(f"{ws.symbol}: Score {ws.last_score:.1f}/100, "
              f"Wait: {ws.wait_time_hours:.1f}h, "
              f"Checks: {ws.total_checks}")
```

## 📊 Understanding the Output

### When Signal is Added to Queue:
```
⏳ Adding to waiting mode...
➕ Signal added to waiting queue: BTCUSDT LONG (AI: BEARISH, Price: 50123.45)
✅ Added to waiting queue (ID: 42)
```

### During Monitoring:
```
🔍 Started monitor for BTCUSDT
📊 BTCUSDT: Score: 35.0/100 - Continue monitoring
📊 BTCUSDT: 10 checks, last score: 42.0/100, wait time: 0.8h
```

### When Conditions are Met:
```
✅ Favorable conditions met for BTCUSDT! Score: 52.0/100
🎯 LONG entry score 52.0/6: RSI oversold (28.45), MACD bullish crossover, Price near lower Bollinger Band
🚀 EXECUTING TRADE: BTCUSDT LONG at price 49876.23
🎯 Successfully executed BTCUSDT
```

### When Signal Expires:
```
⏰ Signal BTCUSDT expired after 24.0 hours
```

## 🎯 What Happens Next?

1. **Signal with contradicting AI verdict** → Added to waiting queue
2. **Monitor checks every 5 minutes** → Calculates RSI, MACD, Bollinger Bands
3. **Evaluates conditions** → Scores 0-100 based on indicators
4. **When score ≥ 50** → Executes trade automatically
5. **After 24 hours** → Expires if conditions never met
6. **All data saved** → Snapshots stored for analysis

## 📈 Example Scenario

```
Time 00:00: LONG signal received, AI says BEARISH → Queue
Time 00:05: Check #1, RSI=65, Score=20/100 → Wait
Time 00:10: Check #2, RSI=62, Score=25/100 → Wait
Time 00:15: Check #3, RSI=58, Score=30/100 → Wait
...
Time 02:35: Check #32, RSI=35, MACD bullish, Score=55/100 → EXECUTE! ✅
```

## 🔍 Monitoring Dashboard

Run live dashboard:

```bash
python src/services/waiting_mode/example_usage.py
# Choose option 5 for dashboard
```

Output:
```
================================================================================
WAITING MODE DASHBOARD - Iteration 1/5
Time: 2025-01-22 14:35:42 UTC
================================================================================
Active Signals: 2/3

ID: 42 | Symbol: BTCUSDT      | Direction: LONG
Status: MONITORING | Score:  35.0/100
Wait Time:   0.8h/24h | Checks:   10
Current Price: $50123.4500
Indicators:
  RSI: 45.23
  MACD: 0.0234 (Signal: 0.0189, Hist: 0.0045)
  BB: Lower=50012.34, Mid=50123.45, Upper=50234.56
--------------------------------------------------------------------------------
ID: 43 | Symbol: ETHUSDT      | Direction: SHORT
Status: MONITORING | Score:  28.0/100
Wait Time:   1.2h/24h | Checks:   14
Current Price: $3045.6700
Indicators:
  RSI: 55.67
  MACD: -0.0156 (Signal: -0.0123, Hist: -0.0033)
  BB: Lower=3020.45, Mid=3045.67, Upper=3070.89
--------------------------------------------------------------------------------
```

## ⚙️ Common Adjustments

### Make It More Aggressive (execute faster):
```env
WAITING_MODE_RSI_OVERSOLD=35.0  # Was 30.0
WAITING_MODE_RSI_OVERBOUGHT=65.0  # Was 70.0
# Lower threshold = easier to trigger
```

### Make It More Conservative (wait longer):
```env
WAITING_MODE_RSI_OVERSOLD=25.0  # Was 30.0
WAITING_MODE_RSI_OVERBOUGHT=75.0  # Was 70.0
# Higher threshold = harder to trigger
```

### Check More Frequently:
```env
WAITING_MODE_CHECK_INTERVAL_MINUTES=3  # Was 5
# More responsive, but more API calls
```

### Wait Longer Before Expiring:
```env
WAITING_MODE_MAX_HOURS=48  # Was 24
# Give signals more time to find entry
```

## 🐛 Quick Troubleshooting

### "Monitor not starting"
```bash
# Check configuration
python -c "from src.core.config import settings; print(settings.waiting_mode_enabled)"
# Should print: True
```

### "No snapshots being saved"
```bash
# Test Binance connectivity
python src/services/waiting_mode/example_usage.py
# Choose option 6
```

### "Signals never execute"
```bash
# Check recent scores
python -c "
from src.core.database import AsyncSessionLocal
from src.models.waiting_signal import WaitingSignalModel
from sqlalchemy import select
import asyncio

async def check():
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(WaitingSignalModel))
        for ws in result.scalars():
            print(f'{ws.symbol}: last_score={ws.last_score:.1f}, checks={ws.total_checks}')

asyncio.run(check())
"
# If scores are consistently < 50, adjust thresholds
```

## 📚 Next Steps

1. ✅ **Run validation** - Make sure everything works
2. ✅ **Test with examples** - Understand behavior
3. ✅ **Paper trade** - Test in simulation first
4. ✅ **Monitor performance** - Track execution vs expiry ratio
5. ✅ **Optimize settings** - Adjust based on results
6. ✅ **Read full docs** - See MONITOR_README.md for details

## 💡 Pro Tips

1. **Start conservative**: Use default settings first
2. **Monitor logs**: Check for any errors or warnings
3. **Analyze snapshots**: Review historical data to optimize
4. **Don't over-optimize**: Too many waiting signals can miss opportunities
5. **Track metrics**: Keep an eye on execution rate and average wait time

## 🎓 Learn More

- **Full Documentation**: See `MONITOR_README.md`
- **Integration Guide**: See `INTEGRATION_GUIDE.md`
- **Implementation Details**: See `IMPLEMENTATION_SUMMARY.md`
- **Examples**: Run `example_usage.py`
- **Validation**: Run `validate.py`

## ✅ Checklist

Before going live:

- [ ] All validation tests pass
- [ ] Environment variables configured
- [ ] Orchestrator updated with waiting monitor
- [ ] Database tables created (run init_db)
- [ ] Tested with example scripts
- [ ] Reviewed logs for errors
- [ ] Set appropriate limits (max_positions, max_hours)
- [ ] Tested in paper trading mode
- [ ] Monitoring dashboard accessible
- [ ] Ready to go! 🚀

---

**Need Help?** Review the full documentation or check the troubleshooting section in MONITOR_README.md.

**Version:** 1.0.0
**Status:** Production Ready ✅
