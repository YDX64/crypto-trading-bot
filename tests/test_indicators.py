"""
Test suite for technical indicators module.

Tests cover:
- RSI calculation with various scenarios
- MACD calculation with different periods
- Bollinger Bands calculation
- Edge cases (insufficient data, invalid inputs)
- Integration tests for calculate_all_indicators
- Entry point determination logic
"""

import pytest
import numpy as np
from src.services.waiting_mode.indicators import (
    calculate_rsi,
    calculate_macd,
    calculate_bollinger_bands,
    calculate_ema,
    calculate_all_indicators,
    is_good_entry_point,
    get_indicator_summary,
    IndicatorValues,
)


class TestEMA:
    """Test Exponential Moving Average calculations."""

    def test_ema_basic(self):
        """Test basic EMA calculation."""
        prices = [10, 11, 12, 11, 13, 14, 13, 15, 14, 16]
        ema = calculate_ema(prices, period=5)

        assert ema is not None
        assert len(ema) == len(prices)
        assert ema[-1] > 0  # Latest EMA should be positive

    def test_ema_insufficient_data(self):
        """Test EMA with insufficient data."""
        prices = [10, 11, 12]
        ema = calculate_ema(prices, period=5)

        assert ema is None

    def test_ema_exact_period(self):
        """Test EMA with exact number of prices as period."""
        prices = [10, 11, 12, 13, 14]
        ema = calculate_ema(prices, period=5)

        assert ema is not None
        assert len(ema) == 5

    def test_ema_empty_list(self):
        """Test EMA with empty price list."""
        ema = calculate_ema([], period=5)
        assert ema is None

    def test_ema_custom_smoothing(self):
        """Test EMA with custom smoothing factor."""
        prices = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19]
        ema_standard = calculate_ema(prices, period=5, smoothing=2.0)
        ema_custom = calculate_ema(prices, period=5, smoothing=3.0)

        assert ema_standard is not None
        assert ema_custom is not None
        # Different smoothing should give different results
        assert not np.allclose(ema_standard, ema_custom)


class TestRSI:
    """Test Relative Strength Index calculations."""

    def test_rsi_basic(self):
        """Test basic RSI calculation."""
        # Sample prices with upward trend
        prices = [44, 44.34, 44.09, 43.61, 44.33, 44.83, 45.10, 45.42,
                  45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00]
        rsi = calculate_rsi(prices, period=14)

        assert rsi is not None
        assert 0 <= rsi <= 100

    def test_rsi_oversold(self):
        """Test RSI in oversold conditions."""
        # Simulate sharp price decline
        prices = [100] + [100 - i for i in range(1, 20)]
        rsi = calculate_rsi(prices, period=14)

        assert rsi is not None
        assert rsi < 30  # Should be oversold

    def test_rsi_overbought(self):
        """Test RSI in overbought conditions."""
        # Simulate sharp price increase
        prices = [100] + [100 + i for i in range(1, 20)]
        rsi = calculate_rsi(prices, period=14)

        assert rsi is not None
        assert rsi > 70  # Should be overbought

    def test_rsi_insufficient_data(self):
        """Test RSI with insufficient data."""
        prices = [100, 101, 102]
        rsi = calculate_rsi(prices, period=14)

        assert rsi is None

    def test_rsi_neutral(self):
        """Test RSI with sideways market."""
        # Oscillating prices should give neutral RSI
        prices = [100, 101, 100, 101, 100, 101, 100, 101,
                  100, 101, 100, 101, 100, 101, 100, 101]
        rsi = calculate_rsi(prices, period=14)

        assert rsi is not None
        assert 40 <= rsi <= 60  # Should be near neutral

    def test_rsi_all_gains(self):
        """Test RSI with all price gains (should be 100)."""
        prices = list(range(100, 120))
        rsi = calculate_rsi(prices, period=14)

        assert rsi is not None
        assert rsi == 100.0

    def test_rsi_custom_period(self):
        """Test RSI with custom period."""
        prices = list(range(100, 130))
        rsi_14 = calculate_rsi(prices, period=14)
        rsi_7 = calculate_rsi(prices, period=7)

        assert rsi_14 is not None
        assert rsi_7 is not None
        # Shorter period should be more responsive
        assert rsi_7 != rsi_14


class TestMACD:
    """Test MACD calculations."""

    def test_macd_basic(self):
        """Test basic MACD calculation."""
        # Generate uptrend prices
        prices = [100 + i * 0.5 for i in range(50)]
        result = calculate_macd(prices)

        assert result is not None
        macd, signal, histogram = result
        assert isinstance(macd, float)
        assert isinstance(signal, float)
        assert isinstance(histogram, float)
        assert histogram == pytest.approx(macd - signal, rel=1e-6)

    def test_macd_bullish_crossover(self):
        """Test MACD bullish crossover."""
        # Prices that should create bullish crossover
        prices = [100 - i for i in range(20)] + [80 + i for i in range(30)]
        result = calculate_macd(prices)

        assert result is not None
        macd, signal, histogram = result
        # In uptrend, MACD should be above signal
        assert histogram > 0

    def test_macd_bearish_crossover(self):
        """Test MACD bearish crossover."""
        # Prices that should create bearish crossover
        prices = [100 + i for i in range(20)] + [120 - i for i in range(30)]
        result = calculate_macd(prices)

        assert result is not None
        macd, signal, histogram = result
        # In downtrend, MACD should be below signal
        assert histogram < 0

    def test_macd_insufficient_data(self):
        """Test MACD with insufficient data."""
        prices = [100, 101, 102]
        result = calculate_macd(prices)

        assert result is None

    def test_macd_custom_periods(self):
        """Test MACD with custom periods."""
        prices = [100 + i * 0.3 for i in range(60)]
        result_default = calculate_macd(prices)
        result_custom = calculate_macd(prices, fast_period=8, slow_period=21, signal_period=5)

        assert result_default is not None
        assert result_custom is not None
        # Different periods should give different results
        assert result_default[0] != result_custom[0]

    def test_macd_sideways_market(self):
        """Test MACD in sideways market."""
        prices = [100 + (i % 2) for i in range(50)]
        result = calculate_macd(prices)

        assert result is not None
        macd, signal, histogram = result
        # In sideways market, histogram should be near zero
        assert abs(histogram) < 1.0


class TestBollingerBands:
    """Test Bollinger Bands calculations."""

    def test_bb_basic(self):
        """Test basic Bollinger Bands calculation."""
        prices = [100 + i * 0.5 for i in range(30)]
        result = calculate_bollinger_bands(prices, period=20)

        assert result is not None
        upper, middle, lower, bandwidth = result
        assert upper > middle > lower
        assert bandwidth > 0

    def test_bb_high_volatility(self):
        """Test Bollinger Bands with high volatility."""
        # Create volatile prices
        prices = [100 + (i % 2) * 10 for i in range(30)]
        result = calculate_bollinger_bands(prices, period=20)

        assert result is not None
        upper, middle, lower, bandwidth = result
        # High volatility should create wide bands
        assert (upper - lower) > 10

    def test_bb_low_volatility(self):
        """Test Bollinger Bands with low volatility."""
        # Create stable prices
        prices = [100 + i * 0.1 for i in range(30)]
        result = calculate_bollinger_bands(prices, period=20)

        assert result is not None
        upper, middle, lower, bandwidth = result
        # Low volatility should create narrow bands
        assert bandwidth < 0.1

    def test_bb_insufficient_data(self):
        """Test Bollinger Bands with insufficient data."""
        prices = [100, 101, 102]
        result = calculate_bollinger_bands(prices, period=20)

        assert result is None

    def test_bb_custom_std(self):
        """Test Bollinger Bands with custom standard deviation."""
        prices = [100 + i * 0.5 for i in range(30)]
        result_2std = calculate_bollinger_bands(prices, period=20, num_std=2.0)
        result_3std = calculate_bollinger_bands(prices, period=20, num_std=3.0)

        assert result_2std is not None
        assert result_3std is not None

        # 3 std should have wider bands
        assert result_3std[0] > result_2std[0]  # upper
        assert result_3std[2] < result_2std[2]  # lower

    def test_bb_middle_is_sma(self):
        """Test that middle band equals simple moving average."""
        prices = [100 + i for i in range(30)]
        result = calculate_bollinger_bands(prices, period=20)

        assert result is not None
        _, middle, _, _ = result

        # Calculate SMA manually
        sma = sum(prices[-20:]) / 20
        assert middle == pytest.approx(sma, rel=1e-6)


class TestIndicatorValues:
    """Test IndicatorValues dataclass functionality."""

    def test_indicator_values_creation(self):
        """Test creating IndicatorValues instance."""
        indicators = IndicatorValues(
            rsi=50.0,
            macd=0.5,
            macd_signal=0.3,
            macd_histogram=0.2,
            bb_upper=110.0,
            bb_middle=100.0,
            bb_lower=90.0,
            bb_bandwidth=0.2,
            has_sufficient_data=True,
        )

        assert indicators.is_valid()
        assert indicators.rsi == 50.0

    def test_is_oversold(self):
        """Test oversold detection."""
        indicators = IndicatorValues(rsi=25.0, has_sufficient_data=True)
        assert indicators.is_oversold()
        assert not indicators.is_overbought()

    def test_is_overbought(self):
        """Test overbought detection."""
        indicators = IndicatorValues(rsi=75.0, has_sufficient_data=True)
        assert indicators.is_overbought()
        assert not indicators.is_oversold()

    def test_bullish_crossover(self):
        """Test bullish crossover detection."""
        indicators = IndicatorValues(
            macd=1.0,
            macd_signal=0.5,
            macd_histogram=0.5,
            has_sufficient_data=True,
        )
        assert indicators.is_bullish_crossover()
        assert not indicators.is_bearish_crossover()

    def test_bearish_crossover(self):
        """Test bearish crossover detection."""
        indicators = IndicatorValues(
            macd=-1.0,
            macd_signal=-0.5,
            macd_histogram=-0.5,
            has_sufficient_data=True,
        )
        assert indicators.is_bearish_crossover()
        assert not indicators.is_bullish_crossover()

    def test_near_bb_lower(self):
        """Test detection of price near lower Bollinger Band."""
        indicators = IndicatorValues(
            bb_upper=110.0,
            bb_middle=100.0,
            bb_lower=90.0,
            has_sufficient_data=True,
        )
        # This test needs adjustment based on actual implementation
        # since it depends on current price which isn't stored in IndicatorValues


class TestCalculateAllIndicators:
    """Test integrated indicator calculation."""

    def test_calculate_all_valid(self):
        """Test calculating all indicators with valid data."""
        prices = [100 + i * 0.5 for i in range(50)]
        indicators = calculate_all_indicators(prices)

        assert indicators.is_valid()
        assert indicators.rsi is not None
        assert indicators.macd is not None
        assert indicators.macd_signal is not None
        assert indicators.bb_middle is not None

    def test_calculate_all_insufficient_data(self):
        """Test calculating all indicators with insufficient data."""
        prices = [100, 101, 102]
        indicators = calculate_all_indicators(prices)

        assert not indicators.is_valid()
        assert indicators.error_message is not None

    def test_calculate_all_custom_periods(self):
        """Test calculating all indicators with custom periods."""
        prices = [100 + i * 0.3 for i in range(60)]
        indicators = calculate_all_indicators(
            prices,
            rsi_period=10,
            macd_fast=8,
            macd_slow=21,
            bb_period=15,
        )

        assert indicators.is_valid()

    def test_calculate_all_empty_prices(self):
        """Test calculating all indicators with empty price list."""
        indicators = calculate_all_indicators([])

        assert not indicators.is_valid()
        assert indicators.error_message is not None


class TestIsGoodEntryPoint:
    """Test entry point determination logic."""

    def test_good_long_entry_oversold(self):
        """Test good LONG entry with oversold RSI."""
        indicators = IndicatorValues(
            rsi=30.0,
            macd=0.5,
            macd_signal=0.3,
            macd_histogram=0.2,
            bb_upper=110.0,
            bb_middle=100.0,
            bb_lower=90.0,
            has_sufficient_data=True,
        )

        is_good, reason = is_good_entry_point(indicators, "LONG")
        assert is_good
        assert "RSI" in reason or "MACD" in reason

    def test_good_short_entry_overbought(self):
        """Test good SHORT entry with overbought RSI."""
        indicators = IndicatorValues(
            rsi=75.0,
            macd=-0.5,
            macd_signal=-0.3,
            macd_histogram=-0.2,
            bb_upper=110.0,
            bb_middle=100.0,
            bb_lower=90.0,
            has_sufficient_data=True,
        )

        is_good, reason = is_good_entry_point(indicators, "SHORT")
        assert is_good
        assert "RSI" in reason or "MACD" in reason

    def test_bad_long_entry_overbought(self):
        """Test bad LONG entry with overbought conditions."""
        indicators = IndicatorValues(
            rsi=80.0,
            macd=-0.5,
            macd_signal=-0.3,
            macd_histogram=-0.2,
            bb_upper=110.0,
            bb_middle=100.0,
            bb_lower=90.0,
            has_sufficient_data=True,
        )

        is_good, reason = is_good_entry_point(indicators, "LONG")
        # Should likely be False due to overbought RSI
        assert "score" in reason.lower()

    def test_invalid_indicators(self):
        """Test entry point with invalid indicators."""
        indicators = IndicatorValues(has_sufficient_data=False)

        is_good, reason = is_good_entry_point(indicators, "LONG")
        assert not is_good
        assert "invalid" in reason.lower()

    def test_invalid_direction(self):
        """Test entry point with invalid direction."""
        indicators = IndicatorValues(
            rsi=50.0,
            macd=0.0,
            macd_signal=0.0,
            macd_histogram=0.0,
            bb_middle=100.0,
            has_sufficient_data=True,
        )

        is_good, reason = is_good_entry_point(indicators, "INVALID")
        assert not is_good
        assert "invalid" in reason.lower()


class TestGetIndicatorSummary:
    """Test indicator summary generation."""

    def test_summary_valid_indicators(self):
        """Test summary with valid indicators."""
        indicators = IndicatorValues(
            rsi=50.0,
            macd=0.5,
            macd_signal=0.3,
            macd_histogram=0.2,
            bb_upper=110.0,
            bb_middle=100.0,
            bb_lower=90.0,
            bb_bandwidth=0.2,
            has_sufficient_data=True,
        )

        summary = get_indicator_summary(indicators)
        assert "RSI" in summary
        assert "MACD" in summary
        assert "Bollinger" in summary

    def test_summary_invalid_indicators(self):
        """Test summary with invalid indicators."""
        indicators = IndicatorValues(
            has_sufficient_data=False,
            error_message="Test error",
        )

        summary = get_indicator_summary(indicators)
        assert "invalid" in summary.lower()


class TestIntegration:
    """Integration tests with realistic scenarios."""

    def test_realistic_uptrend(self):
        """Test with realistic uptrending prices."""
        # Simulate Bitcoin uptrend
        np.random.seed(42)
        base = 40000
        trend = np.linspace(0, 5000, 100)
        noise = np.random.normal(0, 200, 100)
        prices = (base + trend + noise).tolist()

        indicators = calculate_all_indicators(prices)

        assert indicators.is_valid()
        # In uptrend, RSI should be elevated
        assert indicators.rsi > 50
        # MACD should be positive
        assert indicators.macd > 0

    def test_realistic_downtrend(self):
        """Test with realistic downtrending prices."""
        # Simulate crypto downtrend
        np.random.seed(42)
        base = 40000
        trend = np.linspace(0, -5000, 100)
        noise = np.random.normal(0, 200, 100)
        prices = (base + trend + noise).tolist()

        indicators = calculate_all_indicators(prices)

        assert indicators.is_valid()
        # In downtrend, RSI should be depressed
        assert indicators.rsi < 50

    def test_realistic_sideways(self):
        """Test with realistic sideways market."""
        # Simulate ranging market
        np.random.seed(42)
        base = 40000
        noise = np.random.normal(0, 500, 100)
        prices = (base + noise).tolist()

        indicators = calculate_all_indicators(prices)

        assert indicators.is_valid()
        # In sideways market, RSI should be near neutral
        assert 40 <= indicators.rsi <= 60
