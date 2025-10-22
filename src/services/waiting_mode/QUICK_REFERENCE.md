# Technical Indicators - Quick Reference Card

## 🚀 Quick Start (Copy & Paste)

```python
from src.services.waiting_mode.indicators import calculate_all_indicators, is_good_entry_point

# 1. Get prices (closing prices, oldest first)
prices = [40000.0, 40100.0, 40050.0, ...]  # At least 100 recommended

# 2. Calculate all indicators
indicators = calculate_all_indicators(prices)

# 3. Check if good entry
if indicators.is_valid():
    is_good, reason = is_good_entry_point(indicators, "LONG")  # or "SHORT"

    if is_good:
        print(f"✅ Enter trade: {reason}")
    else:
        print(f"⏳ Wait: {reason}")
```

## 📊 Individual Indicators

### RSI
```python
from src.services.waiting_mode.indicators import calculate_rsi

rsi = calculate_rsi(prices, period=14)
# Returns: 0-100 (oversold < 30, overbought > 70)
```

### MACD
```python
from src.services.waiting_mode.indicators import calculate_macd

result = calculate_macd(prices, fast_period=12, slow_period=26, signal_period=9)
if result:
    macd, signal, histogram = result
    # Bullish: macd > signal (histogram > 0)
    # Bearish: macd < signal (histogram < 0)
```

### Bollinger Bands
```python
from src.services.waiting_mode.indicators import calculate_bollinger_bands

result = calculate_bollinger_bands(prices, period=20, num_std=2.0)
if result:
    upper, middle, lower, bandwidth = result
    # Price near lower = potential buy
    # Price near upper = potential sell
```

## 🎯 Entry Point Scoring

### LONG Entry (Score ≥3 needed)
- ✅ RSI < 35: **+2 points**
- ✅ RSI < 50: **+1 point**
- ✅ MACD bullish crossover: **+2 points**
- ✅ MACD histogram > 0: **+1 point**
- ✅ Price near lower BB: **+2 points**

### SHORT Entry (Score ≥3 needed)
- ✅ RSI > 65: **+2 points**
- ✅ RSI > 50: **+1 point**
- ✅ MACD bearish crossover: **+2 points**
- ✅ MACD histogram < 0: **+1 point**
- ✅ Price near upper BB: **+2 points**

## 🔧 IndicatorValues Helper Methods

```python
indicators.is_valid()                    # All indicators calculated?
indicators.is_oversold(threshold=30.0)   # RSI oversold?
indicators.is_overbought(threshold=70.0) # RSI overbought?
indicators.is_bullish_crossover()        # MACD bullish?
indicators.is_bearish_crossover()        # MACD bearish?
indicators.near_bb_lower(pct=5.0)        # Near lower band?
indicators.near_bb_upper(pct=5.0)        # Near upper band?
```

## 📈 Real-World Integration

### Fetch Binance Prices
```python
from src.trading.binance_client import BinanceClient

client = BinanceClient()
klines = await client.get_klines(
    symbol="BTCUSDT",
    interval="5m",  # 1m, 5m, 15m, 1h, 4h, 1d
    limit=100
)
prices = [float(k[4]) for k in klines]  # Closing prices
```

### Waiting Mode Loop
```python
import asyncio

async def wait_for_entry(symbol: str, direction: str, max_minutes: int = 60):
    for i in range(max_minutes):
        # Fetch prices
        klines = await client.get_klines(symbol, "5m", 100)
        prices = [float(k[4]) for k in klines]

        # Check indicators
        indicators = calculate_all_indicators(prices)
        if indicators.is_valid():
            is_good, reason = is_good_entry_point(indicators, direction)
            if is_good:
                return True  # Execute trade

        await asyncio.sleep(60)  # Wait 1 minute

    return False  # Timeout
```

## ⚙️ Configuration Examples

### Conservative (Low Risk)
```python
is_good, reason = is_good_entry_point(
    indicators,
    "LONG",
    rsi_oversold=25.0,   # Only extreme oversold
    rsi_overbought=75.0  # Only extreme overbought
)
```

### Aggressive (More Opportunities)
```python
is_good, reason = is_good_entry_point(
    indicators,
    "SHORT",
    rsi_oversold=40.0,   # Looser thresholds
    rsi_overbought=60.0
)
```

### Custom Indicator Periods
```python
indicators = calculate_all_indicators(
    prices,
    rsi_period=10,       # Faster RSI
    macd_fast=8,         # Faster MACD
    macd_slow=21,
    macd_signal=5,
    bb_period=15,        # Narrower BB
    bb_std=2.5          # Wider bands
)
```

## 📝 Common Patterns

### Pattern 1: Simple Check
```python
indicators = calculate_all_indicators(prices)
is_good, reason = is_good_entry_point(indicators, "LONG")
print(reason)
```

### Pattern 2: Detailed Analysis
```python
from src.services.waiting_mode.indicators import get_indicator_summary

indicators = calculate_all_indicators(prices)
print(get_indicator_summary(indicators))
```

### Pattern 3: Custom Logic
```python
indicators = calculate_all_indicators(prices)

if indicators.is_valid():
    # Custom conditions
    if (indicators.rsi < 30 and
        indicators.is_bullish_crossover() and
        indicators.near_bb_lower()):
        print("Strong LONG signal!")
```

### Pattern 4: Multi-Timeframe
```python
timeframes = ["5m", "15m", "1h"]
long_signals = 0

for tf in timeframes:
    prices = await get_prices(symbol, tf, 100)
    indicators = calculate_all_indicators(prices)
    is_good, _ = is_good_entry_point(indicators, "LONG")
    if is_good:
        long_signals += 1

if long_signals >= 2:  # Confirmation from 2+ timeframes
    execute_trade()
```

## ⚠️ Important Notes

### Data Requirements
- **Minimum**: 35 prices (for all indicators)
- **Recommended**: 100 prices (for accuracy)
- **Order**: Oldest first, newest last
- **Type**: Closing prices as floats

### Error Handling
```python
indicators = calculate_all_indicators(prices)

if not indicators.is_valid():
    print(f"Error: {indicators.error_message}")
    # Handle: fetch more data, skip signal, etc.
```

### Validation Pattern
```python
# ALWAYS check validity before using
if indicators.is_valid():
    # Safe to use all indicator values
    print(f"RSI: {indicators.rsi:.2f}")
else:
    # Handle error
    logger.warning(indicators.error_message)
```

## 🎓 Interpretation Guide

### RSI Values
- **0-30**: Oversold (potential buy for LONG)
- **30-50**: Below neutral
- **50**: Neutral
- **50-70**: Above neutral
- **70-100**: Overbought (potential sell for SHORT)

### MACD Signals
- **Histogram > 0**: Bullish momentum (MACD > Signal)
- **Histogram < 0**: Bearish momentum (MACD < Signal)
- **Histogram increasing**: Momentum strengthening
- **Histogram decreasing**: Momentum weakening

### Bollinger Bands
- **Price at lower band**: Oversold, potential buy
- **Price at upper band**: Overbought, potential sell
- **Narrow bands**: Low volatility (squeeze)
- **Wide bands**: High volatility

## 🔍 Debugging

### Print All Values
```python
indicators = calculate_all_indicators(prices)
print(f"RSI: {indicators.rsi}")
print(f"MACD: {indicators.macd}")
print(f"Signal: {indicators.macd_signal}")
print(f"Histogram: {indicators.macd_histogram}")
print(f"BB Upper: {indicators.bb_upper}")
print(f"BB Middle: {indicators.bb_middle}")
print(f"BB Lower: {indicators.bb_lower}")
print(f"Valid: {indicators.is_valid()}")
```

### Check Calculations
```python
from src.services.waiting_mode.indicators import calculate_rsi, calculate_macd

# Test individual functions
rsi = calculate_rsi(prices, period=14)
print(f"RSI only: {rsi}")

macd_result = calculate_macd(prices)
if macd_result:
    print(f"MACD: {macd_result[0]:.4f}")
```

## 📚 More Information

- **Full Documentation**: `README.md` in this directory
- **Tests**: `/tests/test_indicators.py`
- **Examples**: `/examples/indicator_usage_example.py`
- **Implementation**: `indicators.py` (571 lines, fully documented)

## 🆘 Common Issues

**Issue**: "Insufficient data"
**Fix**: Fetch at least 100 candles

**Issue**: `None` returned
**Fix**: Check if enough data points for period

**Issue**: Inconsistent results
**Fix**: Ensure prices are in chronological order

**Issue**: Can't import module
**Fix**: Add project root to Python path
```python
import sys
sys.path.insert(0, '/path/to/TRADINGBOT')
```

---

**Quick Copy Templates** 📋

### Template 1: Basic Usage
```python
from src.services.waiting_mode.indicators import calculate_all_indicators, is_good_entry_point

prices = await get_prices("BTCUSDT", limit=100)
indicators = calculate_all_indicators(prices)

if indicators.is_valid():
    is_good, reason = is_good_entry_point(indicators, "LONG")
    if is_good:
        execute_trade()
```

### Template 2: With Waiting
```python
for _ in range(60):  # Wait up to 60 minutes
    prices = await get_prices(symbol, limit=100)
    indicators = calculate_all_indicators(prices)

    if indicators.is_valid():
        is_good, reason = is_good_entry_point(indicators, direction)
        if is_good:
            return True

    await asyncio.sleep(60)
```

### Template 3: Custom Thresholds
```python
is_good, reason = is_good_entry_point(
    indicators,
    signal_direction,
    rsi_oversold=35.0,
    rsi_overbought=65.0
)
```
