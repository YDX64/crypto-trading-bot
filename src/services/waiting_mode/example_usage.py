"""
Example usage of the Waiting Mode Monitor

This script demonstrates how to use the waiting mode monitor
in different scenarios.
"""

import asyncio
from datetime import datetime, timedelta
from sqlalchemy import select

from src.core.database import AsyncSessionLocal, init_db
from src.core.logger import app_logger
from src.models.signal import SignalModel, SignalStatus, SignalDirection
from src.models.waiting_signal import WaitingSignalModel, WaitingStatus
from src.services.waiting_mode.monitor import WaitingModeMonitor
from src.trading.binance_client_improved import ImprovedBinanceClient


async def example_1_basic_usage():
    """Example 1: Basic usage - add signal to waiting queue"""
    app_logger.info("=" * 80)
    app_logger.info("EXAMPLE 1: Basic Usage")
    app_logger.info("=" * 80)

    # Initialize components
    binance = ImprovedBinanceClient()
    monitor = WaitingModeMonitor(binance)

    async with AsyncSessionLocal() as db_session:
        # Create a test signal (LONG signal with BEARISH AI verdict)
        test_signal = SignalModel(
            raw_message="Test LONG signal",
            coin="BTC",
            direction=SignalDirection.LONG,
            leverage=10,
            entry_min=50000.0,
            entry_max=51000.0,
            entry=50500.0,
            targets=[52000.0, 53000.0, 54000.0],
            stoploss=49000.0,
            status=SignalStatus.PARSED,
            ai_verdict="BEARISH",  # Contradicts LONG
            trend_aligned=False
        )

        db_session.add(test_signal)
        await db_session.commit()
        await db_session.refresh(test_signal)

        app_logger.info(f"Created test signal: {test_signal.coin} {test_signal.direction}")

        # Add to waiting queue
        waiting_signal = await monitor.add_to_waiting_queue(
            signal=test_signal,
            ai_verdict="BEARISH",
            db_session=db_session
        )

        if waiting_signal:
            app_logger.info(f"✅ Signal added to waiting queue (ID: {waiting_signal.id})")
            app_logger.info(f"   Symbol: {waiting_signal.symbol}")
            app_logger.info(f"   Direction: {waiting_signal.direction}")
            app_logger.info(f"   Current Price: {waiting_signal.current_price}")
            app_logger.info(f"   Status: {waiting_signal.status.value}")
        else:
            app_logger.error("Failed to add signal to waiting queue")

    await binance.close()


async def example_2_monitor_lifecycle():
    """Example 2: Full monitor lifecycle - start, monitor, stop"""
    app_logger.info("=" * 80)
    app_logger.info("EXAMPLE 2: Monitor Lifecycle")
    app_logger.info("=" * 80)

    # Initialize
    binance = ImprovedBinanceClient()
    monitor = WaitingModeMonitor(binance)

    # Start monitor
    await monitor.start()
    app_logger.info("✅ Monitor started")

    async with AsyncSessionLocal() as db_session:
        # Create and add a signal
        test_signal = SignalModel(
            raw_message="Test ETH SHORT signal",
            coin="ETH",
            direction=SignalDirection.SHORT,
            leverage=5,
            entry_min=3000.0,
            entry_max=3100.0,
            entry=3050.0,
            targets=[2900.0, 2800.0, 2700.0],
            stoploss=3200.0,
            status=SignalStatus.PARSED,
            ai_verdict="BULLISH",  # Contradicts SHORT
            trend_aligned=False
        )

        db_session.add(test_signal)
        await db_session.commit()
        await db_session.refresh(test_signal)

        # Add to queue
        waiting_signal = await monitor.add_to_waiting_queue(
            signal=test_signal,
            ai_verdict="BULLISH",
            db_session=db_session
        )

        if waiting_signal:
            app_logger.info(f"Signal #{waiting_signal.id} added to queue")

            # Monitor for 5 minutes
            app_logger.info("Monitoring for 5 minutes...")
            await asyncio.sleep(300)  # 5 minutes

            # Check status
            await db_session.refresh(waiting_signal)
            app_logger.info(f"Status after 5 min: {waiting_signal.status.value}")
            app_logger.info(f"Total checks: {waiting_signal.total_checks}")
            app_logger.info(f"Last score: {waiting_signal.last_score:.1f}/100")

    # Stop monitor
    await monitor.stop()
    app_logger.info("✅ Monitor stopped")

    await binance.close()


async def example_3_view_history():
    """Example 3: View indicator snapshot history"""
    app_logger.info("=" * 80)
    app_logger.info("EXAMPLE 3: View Indicator History")
    app_logger.info("=" * 80)

    monitor = WaitingModeMonitor()

    async with AsyncSessionLocal() as db_session:
        # Get all active waiting signals
        active_signals = await monitor.get_active_waiting_signals(db_session)

        if not active_signals:
            app_logger.info("No active waiting signals found")
            return

        # Show history for first signal
        waiting_signal = active_signals[0]
        app_logger.info(f"Showing history for {waiting_signal.symbol}")

        snapshots = await monitor.get_waiting_signal_history(
            waiting_signal.id,
            db_session,
            limit=10
        )

        app_logger.info(f"Found {len(snapshots)} snapshots")

        for i, snapshot in enumerate(snapshots, 1):
            app_logger.info(f"\nSnapshot #{i}")
            app_logger.info(f"  Time: {snapshot.timestamp}")
            app_logger.info(f"  Price: {snapshot.price:.4f}")
            app_logger.info(f"  RSI: {snapshot.rsi:.2f}")
            app_logger.info(f"  MACD: {snapshot.macd:.4f}")
            app_logger.info(f"  MACD Signal: {snapshot.macd_signal:.4f}")
            app_logger.info(f"  MACD Histogram: {snapshot.macd_histogram:.4f}")
            app_logger.info(f"  BB: [{snapshot.bb_lower:.2f}, {snapshot.bb_middle:.2f}, {snapshot.bb_upper:.2f}]")
            app_logger.info(f"  Overall Score: {snapshot.overall_score:.1f}/100")
            app_logger.info(f"  Conditions Met:")
            app_logger.info(f"    - RSI: {snapshot.rsi_condition_met}")
            app_logger.info(f"    - MACD: {snapshot.macd_condition_met}")
            app_logger.info(f"    - BB: {snapshot.bb_condition_met}")
            app_logger.info(f"    - Price: {snapshot.price_condition_met}")


async def example_4_cancel_signal():
    """Example 4: Cancel a waiting signal"""
    app_logger.info("=" * 80)
    app_logger.info("EXAMPLE 4: Cancel Waiting Signal")
    app_logger.info("=" * 80)

    monitor = WaitingModeMonitor()

    async with AsyncSessionLocal() as db_session:
        # Get active signals
        active_signals = await monitor.get_active_waiting_signals(db_session)

        if not active_signals:
            app_logger.info("No active waiting signals to cancel")
            return

        # Cancel first signal
        signal_to_cancel = active_signals[0]
        app_logger.info(f"Cancelling signal #{signal_to_cancel.id} ({signal_to_cancel.symbol})")

        success = await monitor.cancel_waiting_signal(
            signal_to_cancel.id,
            db_session
        )

        if success:
            app_logger.info("✅ Signal cancelled successfully")
        else:
            app_logger.error("❌ Failed to cancel signal")


async def example_5_dashboard():
    """Example 5: Simple monitoring dashboard"""
    app_logger.info("=" * 80)
    app_logger.info("EXAMPLE 5: Monitoring Dashboard")
    app_logger.info("=" * 80)

    monitor = WaitingModeMonitor()

    # Run dashboard for 5 iterations (2.5 minutes)
    for iteration in range(5):
        async with AsyncSessionLocal() as db_session:
            active_signals = await monitor.get_active_waiting_signals(db_session)

            print("\n" + "=" * 80)
            print(f"WAITING MODE DASHBOARD - Iteration {iteration + 1}/5")
            print(f"Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")
            print("=" * 80)
            print(f"Active Signals: {len(active_signals)}/{monitor.max_positions}")
            print("")

            if not active_signals:
                print("No active waiting signals")
            else:
                for ws in active_signals:
                    print(f"ID: {ws.id} | Symbol: {ws.symbol:12} | Direction: {ws.direction:5}")
                    print(f"Status: {ws.status.value:12} | Score: {ws.last_score:5.1f}/100")
                    print(f"Wait Time: {ws.wait_time_hours:5.1f}h/{ws.max_wait_hours}h | Checks: {ws.total_checks:4}")
                    print(f"Current Price: ${ws.current_price:.4f}")

                    if ws.rsi_value:
                        print(f"Indicators:")
                        print(f"  RSI: {ws.rsi_value:.2f}")
                        print(f"  MACD: {ws.macd_value:.4f} (Signal: {ws.macd_signal:.4f}, Hist: {ws.macd_histogram:.4f})")
                        print(f"  BB: Lower={ws.bb_lower:.2f}, Mid={ws.bb_middle:.2f}, Upper={ws.bb_upper:.2f}")

                    print("-" * 80)

        if iteration < 4:  # Don't sleep on last iteration
            await asyncio.sleep(30)  # 30 seconds between updates


async def example_6_test_indicators():
    """Example 6: Test indicator calculation for a symbol"""
    app_logger.info("=" * 80)
    app_logger.info("EXAMPLE 6: Test Indicator Calculation")
    app_logger.info("=" * 80)

    binance = ImprovedBinanceClient()
    monitor = WaitingModeMonitor(binance)

    symbol = "BTCUSDT"
    app_logger.info(f"Testing indicator calculation for {symbol}")

    # Fetch price history
    prices = await monitor._fetch_price_history(symbol, interval="5m", limit=100)

    if prices:
        app_logger.info(f"✅ Fetched {len(prices)} price candles")
        app_logger.info(f"   Latest price: {prices[-1]:.2f}")
        app_logger.info(f"   Price range: {min(prices):.2f} - {max(prices):.2f}")

        # Calculate indicators
        from src.services.waiting_mode.indicators import calculate_all_indicators, get_indicator_summary

        indicators = calculate_all_indicators(prices)

        if indicators.is_valid():
            app_logger.info("✅ Indicators calculated successfully")
            app_logger.info("\n" + get_indicator_summary(indicators))

            # Test entry score for LONG
            score_long = monitor._calculate_entry_score(indicators, "LONG")
            app_logger.info(f"\nLONG entry score: {score_long:.1f}/100")

            # Test entry score for SHORT
            score_short = monitor._calculate_entry_score(indicators, "SHORT")
            app_logger.info(f"SHORT entry score: {score_short:.1f}/100")
        else:
            app_logger.error(f"❌ Invalid indicators: {indicators.error_message}")
    else:
        app_logger.error("❌ Failed to fetch price history")

    await binance.close()


async def example_7_stress_test():
    """Example 7: Stress test - multiple concurrent signals"""
    app_logger.info("=" * 80)
    app_logger.info("EXAMPLE 7: Stress Test - Multiple Signals")
    app_logger.info("=" * 80)

    binance = ImprovedBinanceClient()
    monitor = WaitingModeMonitor(binance)

    # Start monitor
    await monitor.start()

    symbols = ["BTC", "ETH", "BNB"]
    created_signals = []

    async with AsyncSessionLocal() as db_session:
        # Create multiple signals
        for i, coin in enumerate(symbols):
            signal = SignalModel(
                raw_message=f"Stress test signal {i+1}",
                coin=coin,
                direction=SignalDirection.LONG if i % 2 == 0 else SignalDirection.SHORT,
                leverage=10,
                entry_min=1000.0,
                entry_max=1100.0,
                entry=1050.0,
                targets=[1200.0, 1300.0, 1400.0],
                stoploss=900.0,
                status=SignalStatus.PARSED,
                ai_verdict="BEARISH" if i % 2 == 0 else "BULLISH",
                trend_aligned=False
            )

            db_session.add(signal)
            await db_session.commit()
            await db_session.refresh(signal)

            # Add to queue
            waiting_signal = await monitor.add_to_waiting_queue(
                signal=signal,
                ai_verdict=signal.ai_verdict,
                db_session=db_session
            )

            if waiting_signal:
                created_signals.append(waiting_signal.id)
                app_logger.info(f"✅ Added {coin} to queue (ID: {waiting_signal.id})")

        app_logger.info(f"\n📊 Created {len(created_signals)} waiting signals")
        app_logger.info("Monitoring for 2 minutes...\n")

        # Monitor for 2 minutes
        await asyncio.sleep(120)

        # Check final status
        app_logger.info("\n📊 Final Status:")
        for ws_id in created_signals:
            result = await db_session.execute(
                select(WaitingSignalModel).where(WaitingSignalModel.id == ws_id)
            )
            ws = result.scalar_one_or_none()

            if ws:
                app_logger.info(f"  ID {ws.id}: {ws.symbol} - {ws.status.value} - Score: {ws.last_score:.1f}/100 - Checks: {ws.total_checks}")

    await monitor.stop()
    await binance.close()


async def main():
    """Main function to run examples"""
    print("\n")
    print("=" * 80)
    print("WAITING MODE MONITOR - EXAMPLES")
    print("=" * 80)
    print("\n")

    # Initialize database
    await init_db()

    # Menu
    print("Select an example to run:")
    print("1. Basic Usage - Add signal to waiting queue")
    print("2. Monitor Lifecycle - Full start/stop cycle")
    print("3. View History - Show indicator snapshots")
    print("4. Cancel Signal - Cancel a waiting signal")
    print("5. Dashboard - Live monitoring dashboard")
    print("6. Test Indicators - Calculate indicators for a symbol")
    print("7. Stress Test - Multiple concurrent signals")
    print("0. Exit")
    print("")

    choice = input("Enter your choice (0-7): ").strip()

    if choice == "1":
        await example_1_basic_usage()
    elif choice == "2":
        await example_2_monitor_lifecycle()
    elif choice == "3":
        await example_3_view_history()
    elif choice == "4":
        await example_4_cancel_signal()
    elif choice == "5":
        await example_5_dashboard()
    elif choice == "6":
        await example_6_test_indicators()
    elif choice == "7":
        await example_7_stress_test()
    elif choice == "0":
        print("Exiting...")
    else:
        print("Invalid choice!")

    print("\n" + "=" * 80)
    print("Example completed!")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
