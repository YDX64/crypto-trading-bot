#!/usr/bin/env python3
"""
Example: Using Technical Indicators in Trading Bot

This example demonstrates how to integrate the technical indicators module
into the trading bot's waiting mode functionality.
"""

import asyncio
import sys
from pathlib import Path
from typing import List

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.services.waiting_mode.indicators import (
    calculate_all_indicators,
    is_good_entry_point,
    get_indicator_summary,
    IndicatorValues,
)


async def fetch_recent_prices(symbol: str, limit: int = 100) -> List[float]:
    """
    Fetch recent price data from exchange.

    In production, this would call your Binance client:

    from src.trading.binance_client import BinanceClient
    client = BinanceClient()
    klines = await client.get_klines(symbol, interval="5m", limit=limit)
    prices = [float(k[4]) for k in klines]  # closing prices
    return prices
    """
    # Simulated price data for example
    import random
    base_price = 40000.0
    prices = []
    for i in range(limit):
        # Simulate some price movement
        change = random.uniform(-100, 100)
        base_price += change
        prices.append(base_price)
    return prices


async def wait_for_optimal_entry(
    symbol: str,
    signal_direction: str,
    max_wait_minutes: int = 60,
    check_interval_seconds: int = 60
) -> tuple[bool, str, IndicatorValues]:
    """
    Wait for optimal entry point based on technical indicators.

    Args:
        symbol: Trading symbol (e.g., "BTCUSDT")
        signal_direction: "LONG" or "SHORT"
        max_wait_minutes: Maximum time to wait for optimal entry
        check_interval_seconds: How often to check indicators

    Returns:
        Tuple of (should_enter: bool, reason: str, indicators: IndicatorValues)
    """
    print(f"\n🔍 Waiting for optimal {signal_direction} entry on {symbol}")
    print(f"   Max wait time: {max_wait_minutes} minutes")
    print(f"   Check interval: {check_interval_seconds} seconds")

    max_checks = (max_wait_minutes * 60) // check_interval_seconds

    for check_num in range(1, max_checks + 1):
        print(f"\n--- Check {check_num}/{max_checks} ---")

        # Fetch recent price data
        prices = await fetch_recent_prices(symbol, limit=100)

        if not prices:
            print("❌ Failed to fetch prices")
            await asyncio.sleep(check_interval_seconds)
            continue

        # Calculate all technical indicators
        indicators = calculate_all_indicators(prices)

        if not indicators.is_valid():
            print(f"⚠️  Invalid indicators: {indicators.error_message}")
            await asyncio.sleep(check_interval_seconds)
            continue

        # Print current indicator values
        print(f"\nCurrent Price: ${prices[-1]:,.2f}")
        print(f"RSI: {indicators.rsi:.2f}", end="")
        if indicators.is_oversold():
            print(" (OVERSOLD 🔴)")
        elif indicators.is_overbought():
            print(" (OVERBOUGHT 🔴)")
        else:
            print(" (NEUTRAL)")

        print(f"MACD: {indicators.macd:.4f} / Signal: {indicators.macd_signal:.4f}", end="")
        if indicators.is_bullish_crossover():
            print(" (BULLISH CROSSOVER 📈)")
        elif indicators.is_bearish_crossover():
            print(" (BEARISH CROSSOVER 📉)")
        else:
            print()

        print(f"BB: ${indicators.bb_lower:.2f} < ${indicators.bb_middle:.2f} < ${indicators.bb_upper:.2f}")

        # Check if it's a good entry point
        is_good, reason = is_good_entry_point(indicators, signal_direction)

        print(f"\n{reason}")

        if is_good:
            print(f"\n✅ OPTIMAL ENTRY POINT FOUND!")
            print(f"   Direction: {signal_direction}")
            print(f"   Entry Price: ${prices[-1]:,.2f}")
            return True, reason, indicators

        print(f"⏳ Not optimal yet, waiting {check_interval_seconds}s...")
        await asyncio.sleep(check_interval_seconds)

    # Max wait time exceeded
    print(f"\n⏰ Max wait time exceeded ({max_wait_minutes} minutes)")

    # Get final indicators
    prices = await fetch_recent_prices(symbol, limit=100)
    indicators = calculate_all_indicators(prices)

    if indicators.is_valid():
        _, reason = is_good_entry_point(indicators, signal_direction)
        print(f"   Final analysis: {reason}")

        # You could decide to enter anyway or skip the signal
        return False, f"Timeout after {max_wait_minutes} minutes: {reason}", indicators
    else:
        return False, "Timeout with invalid indicators", indicators


async def example_long_signal():
    """Example: Processing a LONG signal with waiting mode."""
    print("=" * 70)
    print("EXAMPLE 1: LONG Signal with Waiting Mode")
    print("=" * 70)

    # Signal received from Telegram
    signal_data = {
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "entry_min": 39500,
        "entry_max": 40500,
        "targets": [41000, 42000, 43000],
        "stoploss": 38500,
    }

    print(f"\n📥 Received {signal_data['direction']} signal for {signal_data['symbol']}")
    print(f"   Entry range: ${signal_data['entry_min']:,} - ${signal_data['entry_max']:,}")

    # Wait for optimal entry
    should_enter, reason, indicators = await wait_for_optimal_entry(
        symbol=signal_data["symbol"],
        signal_direction=signal_data["direction"],
        max_wait_minutes=5,  # Short time for example
        check_interval_seconds=30,
    )

    if should_enter:
        print(f"\n🎯 Executing {signal_data['direction']} trade")
        print(f"   Entry: ${indicators.bb_middle:.2f}")
        print(f"   Targets: {signal_data['targets']}")
        print(f"   Stoploss: ${signal_data['stoploss']:,}")
    else:
        print(f"\n⛔ Skipping trade: {reason}")


async def example_short_signal():
    """Example: Processing a SHORT signal with waiting mode."""
    print("\n" + "=" * 70)
    print("EXAMPLE 2: SHORT Signal with Waiting Mode")
    print("=" * 70)

    signal_data = {
        "symbol": "ETHUSDT",
        "direction": "SHORT",
        "entry_min": 2900,
        "entry_max": 3000,
        "targets": [2800, 2700, 2600],
        "stoploss": 3100,
    }

    print(f"\n📥 Received {signal_data['direction']} signal for {signal_data['symbol']}")
    print(f"   Entry range: ${signal_data['entry_min']:,} - ${signal_data['entry_max']:,}")

    # Wait for optimal entry
    should_enter, reason, indicators = await wait_for_optimal_entry(
        symbol=signal_data["symbol"],
        signal_direction=signal_data["direction"],
        max_wait_minutes=5,
        check_interval_seconds=30,
    )

    if should_enter:
        print(f"\n🎯 Executing {signal_data['direction']} trade")
        print(f"   Entry: ${indicators.bb_middle:.2f}")
        print(f"   Targets: {signal_data['targets']}")
        print(f"   Stoploss: ${signal_data['stoploss']:,}")
    else:
        print(f"\n⛔ Skipping trade: {reason}")


async def example_instant_analysis():
    """Example: Instant analysis without waiting."""
    print("\n" + "=" * 70)
    print("EXAMPLE 3: Instant Technical Analysis")
    print("=" * 70)

    symbol = "BTCUSDT"
    print(f"\n📊 Analyzing {symbol} current conditions...")

    # Fetch recent prices
    prices = await fetch_recent_prices(symbol, limit=100)

    # Calculate indicators
    indicators = calculate_all_indicators(prices)

    if indicators.is_valid():
        # Print detailed summary
        print("\n" + get_indicator_summary(indicators))

        # Analyze for both directions
        print("\n" + "─" * 70)
        is_good_long, reason_long = is_good_entry_point(indicators, "LONG")
        print(f"\nLONG Analysis: {'✅ GOOD' if is_good_long else '❌ NOT OPTIMAL'}")
        print(f"   {reason_long}")

        is_good_short, reason_short = is_good_entry_point(indicators, "SHORT")
        print(f"\nSHORT Analysis: {'✅ GOOD' if is_good_short else '❌ NOT OPTIMAL'}")
        print(f"   {reason_short}")

        # Recommendation
        print("\n" + "─" * 70)
        if is_good_long and not is_good_short:
            print("💡 Recommendation: Consider LONG positions")
        elif is_good_short and not is_good_long:
            print("💡 Recommendation: Consider SHORT positions")
        elif is_good_long and is_good_short:
            print("💡 Recommendation: Conflicting signals, wait for clarity")
        else:
            print("💡 Recommendation: No strong signals, wait for better setup")
    else:
        print(f"❌ Analysis failed: {indicators.error_message}")


async def example_custom_thresholds():
    """Example: Using custom RSI thresholds."""
    print("\n" + "=" * 70)
    print("EXAMPLE 4: Custom Indicator Thresholds")
    print("=" * 70)

    symbol = "BTCUSDT"
    prices = await fetch_recent_prices(symbol, limit=100)
    indicators = calculate_all_indicators(prices)

    if indicators.is_valid():
        print(f"\nCurrent RSI: {indicators.rsi:.2f}")

        # Conservative thresholds (stricter)
        print("\n🔴 Conservative Strategy (RSI < 25 for LONG, > 75 for SHORT):")
        is_good, reason = is_good_entry_point(
            indicators,
            "LONG",
            rsi_oversold=25.0,
            rsi_overbought=75.0
        )
        print(f"   LONG: {reason}")

        # Aggressive thresholds (looser)
        print("\n🟢 Aggressive Strategy (RSI < 40 for LONG, > 60 for SHORT):")
        is_good, reason = is_good_entry_point(
            indicators,
            "LONG",
            rsi_oversold=40.0,
            rsi_overbought=60.0
        )
        print(f"   LONG: {reason}")

        # Standard thresholds
        print("\n🟡 Standard Strategy (RSI < 35 for LONG, > 65 for SHORT):")
        is_good, reason = is_good_entry_point(
            indicators,
            "LONG",
            rsi_oversold=35.0,
            rsi_overbought=65.0
        )
        print(f"   LONG: {reason}")


async def main():
    """Run all examples."""
    print("\n" + "🤖" * 35)
    print("Technical Indicators Usage Examples")
    print("🤖" * 35)

    try:
        await example_long_signal()
        await example_short_signal()
        await example_instant_analysis()
        await example_custom_thresholds()

        print("\n" + "=" * 70)
        print("✅ All examples completed successfully!")
        print("=" * 70 + "\n")

    except Exception as e:
        print(f"\n❌ Error running examples: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
