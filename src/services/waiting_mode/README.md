# Technical Indicators Module

A comprehensive technical analysis module for the trading bot's waiting mode functionality. This module calculates RSI, MACD, and Bollinger Bands to determine optimal entry points for trading signals.

## Features

- **RSI (Relative Strength Index)**: Momentum oscillator for overbought/oversold conditions
- **MACD (Moving Average Convergence Divergence)**: Trend-following momentum indicator
- **Bollinger Bands**: Volatility-based support and resistance levels
- **EMA (Exponential Moving Average)**: Foundation for MACD calculations
- **Entry Point Analysis**: Automated scoring system for optimal trade entries

## Installation

The module requires numpy and loguru, which are already in your requirements.txt:

```bash
pip install numpy loguru
```

## Quick Start

```python
from src.services.waiting_mode.indicators import calculate_all_indicators, is_good_entry_point

# Fetch recent closing prices (100 candles recommended)
prices = [40000.0, 40100.0, 40050.0, ...]  # List of closing prices

# Calculate all indicators at once
indicators = calculate_all_indicators(prices)

if indicators.is_valid():
    # Check if it's a good entry point for a LONG signal
    is_good, reason = is_good_entry_point(indicators, "LONG")

    if is_good:
        print(f"✅ Good entry point: {reason}")
        print(f"RSI: {indicators.rsi:.2f}")
        print(f"MACD: {indicators.macd:.4f}")
        print(f"Bollinger Middle: {indicators.bb_middle:.2f}")
    else:
        print(f"⏳ Wait for better entry: {reason}")
```

## API Reference

### Core Functions

#### `calculate_rsi(prices: List[float], period: int = 14) -> Optional[float]`

Calculate Relative Strength Index.

**Parameters:**
- `prices`: List of closing prices (most recent last)
- `period`: Number of periods for calculation (default: 14)

**Returns:** RSI value between 0-100, or None if insufficient data

**Example:**
```python
rsi = calculate_rsi(prices, period=14)
if rsi:
    if rsi < 30:
        print("Oversold condition")
    elif rsi > 70:
        print("Overbought condition")
```

#### `calculate_macd(prices: List[float], fast_period: int = 12, slow_period: int = 26, signal_period: int = 9) -> Optional[Tuple[float, float, float]]`

Calculate MACD indicator.

**Parameters:**
- `prices`: List of closing prices
- `fast_period`: Fast EMA period (default: 12)
- `slow_period`: Slow EMA period (default: 26)
- `signal_period`: Signal line EMA period (default: 9)

**Returns:** Tuple of (MACD, Signal, Histogram) or None

**Example:**
```python
result = calculate_macd(prices)
if result:
    macd, signal, histogram = result
    if histogram > 0:
        print("Bullish momentum")
```

#### `calculate_bollinger_bands(prices: List[float], period: int = 20, num_std: float = 2.0) -> Optional[Tuple[float, float, float, float]]`

Calculate Bollinger Bands.

**Parameters:**
- `prices`: List of closing prices
- `period`: SMA period (default: 20)
- `num_std`: Standard deviation multiplier (default: 2.0)

**Returns:** Tuple of (upper_band, middle_band, lower_band, bandwidth) or None

**Example:**
```python
result = calculate_bollinger_bands(prices)
if result:
    upper, middle, lower, bandwidth = result
    current_price = prices[-1]

    if current_price < lower:
        print("Price at lower band - potential buy")
```

#### `calculate_all_indicators(prices: List[float], **kwargs) -> IndicatorValues`

Calculate all indicators at once.

**Parameters:**
- `prices`: List of closing prices
- `rsi_period`: RSI period (default: 14)
- `macd_fast`: MACD fast period (default: 12)
- `macd_slow`: MACD slow period (default: 26)
- `macd_signal`: MACD signal period (default: 9)
- `bb_period`: Bollinger Bands period (default: 20)
- `bb_std`: Bollinger Bands std dev (default: 2.0)

**Returns:** IndicatorValues dataclass

**Example:**
```python
indicators = calculate_all_indicators(prices)

if indicators.is_valid():
    print(f"RSI: {indicators.rsi:.2f}")
    print(f"MACD: {indicators.macd:.4f}")
    print(f"BB Middle: {indicators.bb_middle:.2f}")
```

#### `is_good_entry_point(indicators: IndicatorValues, signal_direction: str, rsi_oversold: float = 35.0, rsi_overbought: float = 65.0) -> Tuple[bool, str]`

Determine if current conditions favor entry.

**Parameters:**
- `indicators`: Calculated indicators
- `signal_direction`: "LONG" or "SHORT"
- `rsi_oversold`: RSI threshold for oversold (default: 35.0)
- `rsi_overbought`: RSI threshold for overbought (default: 65.0)

**Returns:** Tuple of (is_good_entry: bool, reason: str)

**Scoring System:**
- RSI aligned with direction: +1 to +2 points
- MACD crossover aligned: +1 to +2 points
- Price near relevant BB: +2 points
- **Threshold: 3+ points = Good entry**

**Example:**
```python
is_good, reason = is_good_entry_point(indicators, "LONG")

if is_good:
    execute_trade()
else:
    print(f"Waiting: {reason}")
```

### Helper Functions

#### `calculate_ema(prices: List[float], period: int, smoothing: float = 2.0) -> Optional[np.ndarray]`

Calculate Exponential Moving Average (used internally by MACD).

#### `get_indicator_summary(indicators: IndicatorValues) -> str`

Generate human-readable summary of all indicators.

```python
summary = get_indicator_summary(indicators)
print(summary)
# Output:
# === Technical Indicators Summary ===
# RSI: 45.67 (Neutral)
# MACD: 0.0234 / Signal: 0.0189
# ...
```

### IndicatorValues Dataclass

Container for all indicator values with helper methods.

**Attributes:**
```python
@dataclass
class IndicatorValues:
    rsi: Optional[float] = None
    macd: Optional[float] = None
    macd_signal: Optional[float] = None
    macd_histogram: Optional[float] = None
    bb_upper: Optional[float] = None
    bb_middle: Optional[float] = None
    bb_lower: Optional[float] = None
    bb_bandwidth: Optional[float] = None
    has_sufficient_data: bool = False
    error_message: Optional[str] = None
```

**Helper Methods:**
- `is_valid()`: Check if all indicators calculated successfully
- `is_oversold(threshold=30.0)`: Check if RSI indicates oversold
- `is_overbought(threshold=70.0)`: Check if RSI indicates overbought
- `is_bullish_crossover()`: Check if MACD shows bullish signal
- `is_bearish_crossover()`: Check if MACD shows bearish signal
- `near_bb_lower(threshold_pct=5.0)`: Check if price near lower band
- `near_bb_upper(threshold_pct=5.0)`: Check if price near upper band

## Integration with Trading Bot

### Step 1: Fetch Price Data

```python
from src.trading.binance_client import BinanceClient

client = BinanceClient()
klines = await client.get_klines(
    symbol="BTCUSDT",
    interval="5m",  # 5-minute candles
    limit=100       # Get last 100 candles
)

# Extract closing prices
prices = [float(kline[4]) for kline in klines]
```

### Step 2: Calculate Indicators

```python
from src.services.waiting_mode.indicators import calculate_all_indicators

indicators = calculate_all_indicators(prices)

if not indicators.is_valid():
    logger.warning(f"Invalid indicators: {indicators.error_message}")
    return
```

### Step 3: Check Entry Point

```python
from src.services.waiting_mode.indicators import is_good_entry_point

is_good, reason = is_good_entry_point(
    indicators,
    signal_direction="LONG",  # or "SHORT"
    rsi_oversold=35.0,        # Custom threshold
    rsi_overbought=65.0
)

if is_good:
    logger.info(f"✅ Good entry point: {reason}")
    # Execute trade
else:
    logger.info(f"⏳ Waiting: {reason}")
    # Wait and check again later
```

### Step 4: Implement Waiting Loop

```python
import asyncio

async def wait_for_entry(symbol: str, direction: str, max_wait_minutes: int = 60):
    """Wait for optimal entry point."""

    check_interval = 60  # Check every minute
    max_checks = max_wait_minutes

    for i in range(max_checks):
        # Fetch prices
        klines = await client.get_klines(symbol, "5m", 100)
        prices = [float(k[4]) for k in klines]

        # Calculate indicators
        indicators = calculate_all_indicators(prices)

        if indicators.is_valid():
            is_good, reason = is_good_entry_point(indicators, direction)

            if is_good:
                logger.info(f"Entry point found: {reason}")
                return True, indicators

        # Wait before next check
        await asyncio.sleep(check_interval)

    return False, indicators
```

## Usage Examples

### Example 1: Conservative Strategy

```python
# Stricter thresholds for lower risk
indicators = calculate_all_indicators(prices)

is_good, reason = is_good_entry_point(
    indicators,
    "LONG",
    rsi_oversold=25.0,   # Only extremely oversold
    rsi_overbought=75.0  # Only extremely overbought
)
```

### Example 2: Aggressive Strategy

```python
# Looser thresholds for more opportunities
indicators = calculate_all_indicators(prices)

is_good, reason = is_good_entry_point(
    indicators,
    "SHORT",
    rsi_oversold=40.0,   # More relaxed
    rsi_overbought=60.0
)
```

### Example 3: Multiple Timeframes

```python
# Analyze multiple timeframes for confirmation
timeframes = ["5m", "15m", "1h"]
scores = []

for timeframe in timeframes:
    klines = await client.get_klines(symbol, timeframe, 100)
    prices = [float(k[4]) for k in klines]
    indicators = calculate_all_indicators(prices)

    is_good, reason = is_good_entry_point(indicators, "LONG")
    scores.append(1 if is_good else 0)

# Enter if 2 out of 3 timeframes agree
if sum(scores) >= 2:
    execute_trade()
```

### Example 4: Real-time Monitoring

```python
async def monitor_entry_conditions():
    """Monitor and log entry conditions in real-time."""

    while True:
        prices = await fetch_prices("BTCUSDT", limit=100)
        indicators = calculate_all_indicators(prices)

        if indicators.is_valid():
            print(get_indicator_summary(indicators))

            # Check both directions
            long_good, long_reason = is_good_entry_point(indicators, "LONG")
            short_good, short_reason = is_good_entry_point(indicators, "SHORT")

            if long_good:
                logger.info(f"📈 LONG opportunity: {long_reason}")
            if short_good:
                logger.info(f"📉 SHORT opportunity: {short_reason}")

        await asyncio.sleep(60)  # Check every minute
```

## Data Requirements

### Minimum Data Points

- **RSI**: `period + 1` prices (default: 15)
- **MACD**: `slow_period + signal_period` prices (default: 35)
- **Bollinger Bands**: `period` prices (default: 20)

**Recommended**: Fetch at least **100 candles** for accurate calculations.

### Timeframe Selection

Choose timeframe based on your trading strategy:

- **1m, 5m**: Scalping, very short-term
- **15m, 1h**: Intraday trading
- **4h, 1d**: Swing trading
- **Multiple**: Use multiple timeframes for confirmation

## Error Handling

The module provides comprehensive error handling:

```python
indicators = calculate_all_indicators(prices)

if not indicators.is_valid():
    if indicators.error_message:
        logger.error(f"Indicator error: {indicators.error_message}")

    # Handle specific cases
    if "Insufficient data" in indicators.error_message:
        # Fetch more data
        pass
    else:
        # Other error
        pass
```

## Performance Considerations

1. **Batch Calculations**: Use `calculate_all_indicators()` instead of individual functions
2. **Caching**: Cache recent calculations to avoid recalculation
3. **Array Operations**: Uses numpy for efficient calculations
4. **Timeframe**: Larger timeframes = fewer API calls

## Testing

Run the comprehensive test suite:

```bash
# Unit tests
pytest tests/test_indicators.py -v

# Manual verification
python3 test_indicators_manual.py

# Usage examples
python3 examples/indicator_usage_example.py
```

## Troubleshooting

### "Insufficient data" Error

```python
# Solution: Fetch more candles
klines = await client.get_klines(symbol, "5m", 100)  # Not 50
```

### Inconsistent Results

```python
# Ensure prices are in chronological order (oldest first, newest last)
prices = sorted(prices)  # If needed
```

### Invalid Indicators

```python
# Always check validity before using
if indicators.is_valid():
    # Use indicators
else:
    logger.warning(indicators.error_message)
```

## Best Practices

1. **Always validate**: Check `indicators.is_valid()` before using
2. **Use appropriate timeframes**: Match your trading strategy
3. **Combine signals**: Don't rely on a single indicator
4. **Adjust thresholds**: Customize for your risk tolerance
5. **Backtest**: Test your strategy on historical data
6. **Monitor performance**: Log and analyze entry decisions

## Mathematical Formulas

### RSI
```
RSI = 100 - (100 / (1 + RS))
where RS = Average Gain / Average Loss over period
```

### MACD
```
MACD Line = EMA(12) - EMA(26)
Signal Line = EMA(9) of MACD Line
Histogram = MACD Line - Signal Line
```

### Bollinger Bands
```
Middle Band = SMA(20)
Upper Band = Middle Band + (2 × StdDev)
Lower Band = Middle Band - (2 × StdDev)
Bandwidth = (Upper - Lower) / Middle
```

### EMA
```
Multiplier = 2 / (period + 1)
EMA = Price(t) × Multiplier + EMA(t-1) × (1 - Multiplier)
```

## Contributing

When adding new indicators:

1. Follow the existing pattern
2. Include comprehensive docstrings
3. Handle edge cases (insufficient data, division by zero)
4. Return `None` for invalid calculations
5. Add unit tests
6. Update this README

## License

Part of the TRADINGBOT project. See main LICENSE file.
