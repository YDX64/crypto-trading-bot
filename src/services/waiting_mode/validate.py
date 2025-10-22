"""
Validation script for Waiting Mode Monitor

This script performs comprehensive validation of the waiting mode monitor
to ensure all components are working correctly.
"""

import asyncio
import sys
from typing import List, Tuple

from loguru import logger

# Validation results
validation_results: List[Tuple[str, bool, str]] = []


def add_result(test_name: str, passed: bool, message: str):
    """Add a validation result."""
    validation_results.append((test_name, passed, message))
    if passed:
        logger.info(f"✅ {test_name}: {message}")
    else:
        logger.error(f"❌ {test_name}: {message}")


async def validate_imports():
    """Validate that all required imports work."""
    try:
        from src.services.waiting_mode.monitor import WaitingModeMonitor
        from src.services.waiting_mode.indicators import (
            calculate_all_indicators,
            is_good_entry_point,
            IndicatorValues
        )
        from src.models.waiting_signal import (
            WaitingSignalModel,
            IndicatorSnapshot,
            WaitingStatus,
            WaitingModeConfig
        )
        from src.trading.binance_client_improved import ImprovedBinanceClient

        add_result(
            "Import Check",
            True,
            "All required modules imported successfully"
        )
        return True
    except ImportError as e:
        add_result(
            "Import Check",
            False,
            f"Failed to import required modules: {e}"
        )
        return False


async def validate_config():
    """Validate configuration settings."""
    try:
        from src.core.config import settings

        required_settings = [
            "waiting_mode_enabled",
            "waiting_mode_max_positions",
            "waiting_mode_max_hours",
            "waiting_mode_check_interval_minutes",
            "waiting_mode_rsi_period",
            "waiting_mode_macd_fast",
            "waiting_mode_bb_period"
        ]

        missing = []
        for setting in required_settings:
            if not hasattr(settings, setting):
                missing.append(setting)

        if missing:
            add_result(
                "Configuration Check",
                False,
                f"Missing settings: {', '.join(missing)}"
            )
            return False

        add_result(
            "Configuration Check",
            True,
            f"All {len(required_settings)} required settings present"
        )
        return True
    except Exception as e:
        add_result(
            "Configuration Check",
            False,
            f"Configuration error: {e}"
        )
        return False


async def validate_database():
    """Validate database tables exist."""
    try:
        from src.core.database import engine, Base
        from src.models.waiting_signal import WaitingSignalModel, IndicatorSnapshot

        async with engine.begin() as conn:
            # Try to query the tables
            from sqlalchemy import select, text

            # Check if tables exist
            result = await conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )
            tables = [row[0] for row in result]

            required_tables = ["waiting_signals", "indicator_snapshots"]
            missing_tables = [t for t in required_tables if t not in tables]

            if missing_tables:
                add_result(
                    "Database Check",
                    False,
                    f"Missing tables: {', '.join(missing_tables)}. Run init_db() first."
                )
                return False

        add_result(
            "Database Check",
            True,
            f"All required database tables exist"
        )
        return True
    except Exception as e:
        add_result(
            "Database Check",
            False,
            f"Database error: {e}"
        )
        return False


async def validate_binance_client():
    """Validate Binance client connectivity."""
    try:
        from src.trading.binance_client_improved import ImprovedBinanceClient

        client = ImprovedBinanceClient()

        # Test basic connectivity
        try:
            # Ping Binance
            await client._request_with_retry("GET", "/fapi/v1/ping")

            add_result(
                "Binance Client Check",
                True,
                "Successfully connected to Binance API"
            )

            await client.close()
            return True
        except Exception as e:
            add_result(
                "Binance Client Check",
                False,
                f"Failed to connect to Binance: {e}"
            )
            await client.close()
            return False

    except Exception as e:
        add_result(
            "Binance Client Check",
            False,
            f"Failed to initialize Binance client: {e}"
        )
        return False


async def validate_indicator_calculation():
    """Validate technical indicator calculations."""
    try:
        from src.services.waiting_mode.indicators import calculate_all_indicators

        # Test data: 100 mock prices
        test_prices = [100.0 + i * 0.5 for i in range(100)]

        indicators = calculate_all_indicators(test_prices)

        if not indicators.is_valid():
            add_result(
                "Indicator Calculation Check",
                False,
                f"Indicators not valid: {indicators.error_message}"
            )
            return False

        # Check all indicators are calculated
        checks = [
            indicators.rsi is not None,
            indicators.macd is not None,
            indicators.macd_signal is not None,
            indicators.bb_upper is not None,
            indicators.bb_middle is not None,
            indicators.bb_lower is not None
        ]

        if not all(checks):
            add_result(
                "Indicator Calculation Check",
                False,
                "Some indicators were not calculated"
            )
            return False

        add_result(
            "Indicator Calculation Check",
            True,
            f"All indicators calculated (RSI={indicators.rsi:.2f}, MACD={indicators.macd:.4f})"
        )
        return True

    except Exception as e:
        add_result(
            "Indicator Calculation Check",
            False,
            f"Indicator calculation failed: {e}"
        )
        return False


async def validate_monitor_initialization():
    """Validate monitor can be initialized."""
    try:
        from src.services.waiting_mode.monitor import WaitingModeMonitor
        from src.trading.binance_client_improved import ImprovedBinanceClient

        binance = ImprovedBinanceClient()
        monitor = WaitingModeMonitor(binance)

        # Check attributes
        assert monitor.binance is not None
        assert monitor.config is not None
        assert hasattr(monitor, 'start')
        assert hasattr(monitor, 'stop')
        assert hasattr(monitor, 'add_to_waiting_queue')

        await binance.close()

        add_result(
            "Monitor Initialization Check",
            True,
            "Monitor initialized successfully with all required methods"
        )
        return True

    except Exception as e:
        add_result(
            "Monitor Initialization Check",
            False,
            f"Monitor initialization failed: {e}"
        )
        return False


async def validate_price_fetching():
    """Validate historical price fetching."""
    try:
        from src.services.waiting_mode.monitor import WaitingModeMonitor
        from src.trading.binance_client_improved import ImprovedBinanceClient

        binance = ImprovedBinanceClient()
        monitor = WaitingModeMonitor(binance)

        # Fetch price history for BTC
        prices = await monitor._fetch_price_history("BTCUSDT", interval="5m", limit=100)

        if not prices:
            add_result(
                "Price Fetching Check",
                False,
                "Failed to fetch price history"
            )
            await binance.close()
            return False

        if len(prices) < 50:
            add_result(
                "Price Fetching Check",
                False,
                f"Insufficient price data: got {len(prices)}, need at least 50"
            )
            await binance.close()
            return False

        add_result(
            "Price Fetching Check",
            True,
            f"Successfully fetched {len(prices)} price candles"
        )

        await binance.close()
        return True

    except Exception as e:
        add_result(
            "Price Fetching Check",
            False,
            f"Price fetching failed: {e}"
        )
        return False


async def validate_scoring_algorithm():
    """Validate scoring algorithm."""
    try:
        from src.services.waiting_mode.monitor import WaitingModeMonitor
        from src.services.waiting_mode.indicators import IndicatorValues

        monitor = WaitingModeMonitor()

        # Test with mock indicators - LONG scenario (oversold)
        indicators_long = IndicatorValues(
            rsi=25.0,  # Oversold
            macd=0.5,
            macd_signal=0.3,
            macd_histogram=0.2,  # Positive
            bb_upper=110.0,
            bb_middle=100.0,
            bb_lower=90.0,
            has_sufficient_data=True
        )

        score_long = monitor._calculate_entry_score(indicators_long, "LONG")

        # Test with mock indicators - SHORT scenario (overbought)
        indicators_short = IndicatorValues(
            rsi=75.0,  # Overbought
            macd=-0.5,
            macd_signal=-0.3,
            macd_histogram=-0.2,  # Negative
            bb_upper=110.0,
            bb_middle=100.0,
            bb_lower=90.0,
            has_sufficient_data=True
        )

        score_short = monitor._calculate_entry_score(indicators_short, "SHORT")

        # Validate scores are in valid range
        if not (0 <= score_long <= 100):
            add_result(
                "Scoring Algorithm Check",
                False,
                f"LONG score out of range: {score_long}"
            )
            return False

        if not (0 <= score_short <= 100):
            add_result(
                "Scoring Algorithm Check",
                False,
                f"SHORT score out of range: {score_short}"
            )
            return False

        # Both should have positive scores given favorable conditions
        if score_long < 30 or score_short < 30:
            add_result(
                "Scoring Algorithm Check",
                False,
                f"Scores unexpectedly low: LONG={score_long:.1f}, SHORT={score_short:.1f}"
            )
            return False

        add_result(
            "Scoring Algorithm Check",
            True,
            f"Scores calculated correctly: LONG={score_long:.1f}/100, SHORT={score_short:.1f}/100"
        )
        return True

    except Exception as e:
        add_result(
            "Scoring Algorithm Check",
            False,
            f"Scoring failed: {e}"
        )
        return False


async def validate_monitor_lifecycle():
    """Validate monitor start/stop lifecycle."""
    try:
        from src.services.waiting_mode.monitor import WaitingModeMonitor
        from src.trading.binance_client_improved import ImprovedBinanceClient

        binance = ImprovedBinanceClient()
        monitor = WaitingModeMonitor(binance)

        # Test start
        await monitor.start()

        if not monitor.is_running:
            add_result(
                "Monitor Lifecycle Check",
                False,
                "Monitor not running after start()"
            )
            await binance.close()
            return False

        # Wait a bit
        await asyncio.sleep(2)

        # Test stop
        await monitor.stop()

        if monitor.is_running:
            add_result(
                "Monitor Lifecycle Check",
                False,
                "Monitor still running after stop()"
            )
            await binance.close()
            return False

        add_result(
            "Monitor Lifecycle Check",
            True,
            "Monitor start/stop lifecycle works correctly"
        )

        await binance.close()
        return True

    except Exception as e:
        add_result(
            "Monitor Lifecycle Check",
            False,
            f"Lifecycle test failed: {e}"
        )
        return False


async def run_validation():
    """Run all validation tests."""
    logger.info("=" * 80)
    logger.info("WAITING MODE MONITOR - VALIDATION")
    logger.info("=" * 80)
    logger.info("")

    # Run all validation tests
    tests = [
        ("Imports", validate_imports),
        ("Configuration", validate_config),
        ("Database", validate_database),
        ("Binance Client", validate_binance_client),
        ("Indicator Calculation", validate_indicator_calculation),
        ("Monitor Initialization", validate_monitor_initialization),
        ("Price Fetching", validate_price_fetching),
        ("Scoring Algorithm", validate_scoring_algorithm),
        ("Monitor Lifecycle", validate_monitor_lifecycle)
    ]

    total_tests = len(tests)
    passed_tests = 0

    for test_name, test_func in tests:
        logger.info(f"Running: {test_name}...")
        try:
            result = await test_func()
            if result:
                passed_tests += 1
        except Exception as e:
            add_result(test_name, False, f"Unexpected error: {e}")
        logger.info("")

    # Summary
    logger.info("=" * 80)
    logger.info("VALIDATION SUMMARY")
    logger.info("=" * 80)

    for test_name, passed, message in validation_results:
        status = "✅ PASS" if passed else "❌ FAIL"
        logger.info(f"{status} | {test_name}")
        if not passed:
            logger.info(f"       {message}")

    logger.info("")
    logger.info(f"Total: {passed_tests}/{total_tests} tests passed")
    logger.info("")

    if passed_tests == total_tests:
        logger.info("🎉 All validation tests passed! Monitor is ready to use.")
        return 0
    else:
        logger.error(f"⚠️ {total_tests - passed_tests} test(s) failed. Please fix before using.")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(run_validation())
    sys.exit(exit_code)
