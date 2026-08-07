"""
Technical Indicators Module for Trading Bot

This module provides calculations for common technical indicators used in
trading analysis: RSI, MACD, and Bollinger Bands.

All calculations use numpy for efficiency and include proper error handling
for edge cases like insufficient data.
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple
import numpy as np
from loguru import logger


@dataclass
class IndicatorValues:
    """Container for all technical indicator values at a specific point in time."""

    # RSI
    rsi: Optional[float] = None

    # MACD
    macd: Optional[float] = None
    macd_signal: Optional[float] = None
    macd_histogram: Optional[float] = None

    # Bollinger Bands
    bb_upper: Optional[float] = None
    bb_middle: Optional[float] = None
    bb_lower: Optional[float] = None
    bb_bandwidth: Optional[float] = None

    # Metadata
    has_sufficient_data: bool = False
    error_message: Optional[str] = None

    def is_valid(self) -> bool:
        """Check if indicator values are valid and usable."""
        return (
            self.has_sufficient_data
            and self.rsi is not None
            and self.macd is not None
            and self.bb_middle is not None
        )

    def is_oversold(self, rsi_threshold: float = 30.0) -> bool:
        """Check if RSI indicates oversold condition."""
        return self.rsi is not None and self.rsi < rsi_threshold

    def is_overbought(self, rsi_threshold: float = 70.0) -> bool:
        """Check if RSI indicates overbought condition."""
        return self.rsi is not None and self.rsi > rsi_threshold

    def is_bullish_crossover(self) -> bool:
        """Check if MACD shows bullish crossover (MACD > Signal)."""
        return (
            self.macd is not None
            and self.macd_signal is not None
            and self.macd > self.macd_signal
            and self.macd_histogram is not None
            and self.macd_histogram > 0
        )

    def is_bearish_crossover(self) -> bool:
        """Check if MACD shows bearish crossover (MACD < Signal)."""
        return (
            self.macd is not None
            and self.macd_signal is not None
            and self.macd < self.macd_signal
            and self.macd_histogram is not None
            and self.macd_histogram < 0
        )

    def near_bb_lower(
        self,
        threshold_pct: float = 5.0,
        current_price: Optional[float] = None
    ) -> bool:
        """
        Check if price is near the lower Bollinger Band.

        Args:
            threshold_pct: How close to the lower band counts as "near",
                expressed as a percentage of the lower-half band width
                (bb_middle - bb_lower). A price at or below the lower band
                always counts as near.
            current_price: Current market price to compare against the band.
                If omitted, this falls back to the legacy (broken) behavior
                of comparing the lower band's own value against a fraction
                of the half band-width, which does NOT reflect proximity of
                the actual price to the band and is effectively always
                False/near-constant. Callers should always pass the current
                price for a meaningful result; the fallback exists only for
                backward compatibility with old call sites.
        """
        if self.bb_lower is None or self.bb_middle is None:
            return False

        band_half_width = self.bb_middle - self.bb_lower

        if current_price is None:
            logger.warning(
                "near_bb_lower() called without current_price - falling back "
                "to legacy behavior that does not use the actual market "
                "price and is effectively dead logic. Pass current_price "
                "for an accurate Bollinger Band proximity check."
            )
            threshold = band_half_width * (threshold_pct / 100)
            return abs(self.bb_lower) < threshold

        if band_half_width <= 0:
            return False

        # Distance of the current price above the lower band, as a fraction
        # of the lower-half band width. Zero or negative means the price is
        # at or below the band (the strongest possible "near lower band"
        # signal), which should always count as near.
        distance = current_price - self.bb_lower
        threshold = band_half_width * (threshold_pct / 100)
        return distance <= threshold

    def near_bb_upper(
        self,
        threshold_pct: float = 5.0,
        current_price: Optional[float] = None
    ) -> bool:
        """
        Check if price is near the upper Bollinger Band.

        Args:
            threshold_pct: How close to the upper band counts as "near",
                expressed as a percentage of the upper-half band width
                (bb_upper - bb_middle). A price at or above the upper band
                always counts as near.
            current_price: Current market price to compare against the band.
                If omitted, this falls back to the legacy (broken) behavior
                of comparing the upper band's own value against a fraction
                of the half band-width, which does NOT reflect proximity of
                the actual price to the band. Callers should always pass the
                current price for a meaningful result; the fallback exists
                only for backward compatibility with old call sites.
        """
        if self.bb_upper is None or self.bb_middle is None:
            return False

        band_half_width = self.bb_upper - self.bb_middle

        if current_price is None:
            logger.warning(
                "near_bb_upper() called without current_price - falling back "
                "to legacy behavior that does not use the actual market "
                "price and is effectively dead logic. Pass current_price "
                "for an accurate Bollinger Band proximity check."
            )
            threshold = band_half_width * (threshold_pct / 100)
            return abs(self.bb_upper) < threshold

        if band_half_width <= 0:
            return False

        # Distance of the current price below the upper band, as a fraction
        # of the upper-half band width. Zero or negative means the price is
        # at or above the band (the strongest possible "near upper band"
        # signal), which should always count as near.
        distance = self.bb_upper - current_price
        threshold = band_half_width * (threshold_pct / 100)
        return distance <= threshold


def calculate_ema(
    prices: List[float],
    period: int,
    smoothing: float = 2.0
) -> Optional[np.ndarray]:
    """
    Calculate Exponential Moving Average (EMA).

    The EMA gives more weight to recent prices, making it more responsive
    to new information compared to Simple Moving Average (SMA).

    Formula: EMA = Price(t) * K + EMA(y) * (1 - K)
    where K = smoothing / (period + 1)

    Args:
        prices: List of price values
        period: Number of periods for EMA calculation
        smoothing: Smoothing factor (default: 2.0 for standard EMA)

    Returns:
        Numpy array of EMA values, or None if insufficient data

    Example:
        >>> prices = [10, 11, 12, 11, 13, 14]
        >>> ema = calculate_ema(prices, period=3)
        >>> print(ema[-1])  # Latest EMA value
    """
    if not prices or len(prices) < period:
        logger.warning(f"Insufficient data for EMA: {len(prices)} < {period}")
        return None

    try:
        prices_array = np.array(prices, dtype=np.float64)

        # Calculate the smoothing multiplier
        multiplier = smoothing / (period + 1)

        # Initialize EMA with Simple Moving Average (SMA) for first value
        ema = np.zeros(len(prices_array))
        ema[period - 1] = np.mean(prices_array[:period])

        # Calculate EMA for remaining values
        for i in range(period, len(prices_array)):
            ema[i] = (prices_array[i] * multiplier) + (ema[i - 1] * (1 - multiplier))

        return ema

    except Exception as e:
        logger.error(f"Error calculating EMA: {e}")
        return None


def calculate_rsi(
    prices: List[float],
    period: int = 14
) -> Optional[float]:
    """
    Calculate Relative Strength Index (RSI).

    RSI is a momentum oscillator that measures the speed and magnitude of
    price changes. It oscillates between 0 and 100.

    Traditional interpretation:
    - RSI > 70: Overbought (potential sell signal)
    - RSI < 30: Oversold (potential buy signal)
    - RSI = 50: Neutral momentum

    Formula:
        RSI = 100 - (100 / (1 + RS))
        where RS = Average Gain / Average Loss over period

    Args:
        prices: List of closing prices (most recent last)
        period: Number of periods for RSI calculation (default: 14)

    Returns:
        RSI value between 0 and 100, or None if insufficient data

    Example:
        >>> prices = [44, 44.34, 44.09, 43.61, 44.33, 44.83, 45.10, 45.42,
        ...           45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00]
        >>> rsi = calculate_rsi(prices, period=14)
        >>> print(f"RSI: {rsi:.2f}")
    """
    if not prices or len(prices) < period + 1:
        logger.warning(f"Insufficient data for RSI: {len(prices)} < {period + 1}")
        return None

    try:
        prices_array = np.array(prices, dtype=np.float64)

        # Calculate price changes
        deltas = np.diff(prices_array)

        # Separate gains and losses
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)

        # Calculate initial average gain and loss (SMA)
        avg_gain = np.mean(gains[:period])
        avg_loss = np.mean(losses[:period])

        # Calculate subsequent averages using smoothing
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        # Handle division by zero
        if avg_loss == 0:
            return 100.0

        # Calculate RS and RSI
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        return float(rsi)

    except Exception as e:
        logger.error(f"Error calculating RSI: {e}")
        return None


def calculate_macd(
    prices: List[float],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9
) -> Optional[Tuple[float, float, float]]:
    """
    Calculate MACD (Moving Average Convergence Divergence).

    MACD is a trend-following momentum indicator that shows the relationship
    between two moving averages of prices.

    Components:
    - MACD Line: Fast EMA - Slow EMA
    - Signal Line: EMA of MACD Line
    - Histogram: MACD Line - Signal Line

    Trading signals:
    - MACD crosses above Signal: Bullish signal
    - MACD crosses below Signal: Bearish signal
    - Histogram increasing: Momentum strengthening
    - Histogram decreasing: Momentum weakening

    Args:
        prices: List of closing prices (most recent last)
        fast_period: Period for fast EMA (default: 12)
        slow_period: Period for slow EMA (default: 26)
        signal_period: Period for signal line EMA (default: 9)

    Returns:
        Tuple of (MACD, Signal, Histogram) or None if insufficient data

    Example:
        >>> prices = [100, 102, 101, 103, 105, 104, 106, 108, 107, 109,
        ...           110, 112, 111, 113, 115, 114, 116, 118, 117, 119,
        ...           120, 122, 121, 123, 125, 124, 126, 128, 127, 129]
        >>> macd, signal, histogram = calculate_macd(prices)
        >>> print(f"MACD: {macd:.2f}, Signal: {signal:.2f}, Hist: {histogram:.2f}")
    """
    min_required = slow_period + signal_period
    if not prices or len(prices) < min_required:
        logger.warning(f"Insufficient data for MACD: {len(prices)} < {min_required}")
        return None

    try:
        # Calculate fast and slow EMAs
        fast_ema = calculate_ema(prices, fast_period)
        slow_ema = calculate_ema(prices, slow_period)

        if fast_ema is None or slow_ema is None:
            return None

        # Calculate MACD line (difference between EMAs)
        macd_line = fast_ema - slow_ema

        # Calculate signal line (EMA of MACD line)
        # Convert to list for consistency with calculate_ema input
        macd_list = macd_line[slow_period - 1:].tolist()
        signal_ema = calculate_ema(macd_list, signal_period)

        if signal_ema is None:
            return None

        # Get the latest values
        macd_value = float(macd_line[-1])
        signal_value = float(signal_ema[-1])
        histogram = macd_value - signal_value

        return (macd_value, signal_value, histogram)

    except Exception as e:
        logger.error(f"Error calculating MACD: {e}")
        return None


def calculate_bollinger_bands(
    prices: List[float],
    period: int = 20,
    num_std: float = 2.0
) -> Optional[Tuple[float, float, float, float]]:
    """
    Calculate Bollinger Bands.

    Bollinger Bands consist of a middle band (SMA) and two outer bands
    based on standard deviation. They expand and contract based on
    market volatility.

    Components:
    - Middle Band: Simple Moving Average (SMA)
    - Upper Band: SMA + (Standard Deviation × multiplier)
    - Lower Band: SMA - (Standard Deviation × multiplier)
    - Bandwidth: (Upper - Lower) / Middle

    Trading signals:
    - Price near lower band: Potential oversold (buy signal)
    - Price near upper band: Potential overbought (sell signal)
    - Squeeze (narrow bands): Low volatility, potential breakout
    - Expansion (wide bands): High volatility

    Args:
        prices: List of closing prices (most recent last)
        period: Number of periods for SMA (default: 20)
        num_std: Number of standard deviations for bands (default: 2.0)

    Returns:
        Tuple of (upper_band, middle_band, lower_band, bandwidth) or None

    Example:
        >>> prices = [100, 102, 101, 103, 105, 104, 106, 108, 107, 109,
        ...           110, 112, 111, 113, 115, 114, 116, 118, 117, 119,
        ...           120, 122]
        >>> upper, middle, lower, bandwidth = calculate_bollinger_bands(prices)
        >>> print(f"Upper: {upper:.2f}, Middle: {middle:.2f}, Lower: {lower:.2f}")
    """
    if not prices or len(prices) < period:
        logger.warning(f"Insufficient data for Bollinger Bands: {len(prices)} < {period}")
        return None

    try:
        prices_array = np.array(prices, dtype=np.float64)

        # Calculate middle band (SMA)
        middle_band = float(np.mean(prices_array[-period:]))

        # Calculate standard deviation
        std_dev = float(np.std(prices_array[-period:], ddof=1))

        # Calculate upper and lower bands
        upper_band = middle_band + (num_std * std_dev)
        lower_band = middle_band - (num_std * std_dev)

        # Calculate bandwidth (measure of volatility)
        bandwidth = (upper_band - lower_band) / middle_band if middle_band != 0 else 0

        return (upper_band, middle_band, lower_band, bandwidth)

    except Exception as e:
        logger.error(f"Error calculating Bollinger Bands: {e}")
        return None


def calculate_all_indicators(
    prices: List[float],
    rsi_period: int = 14,
    macd_fast: int = 12,
    macd_slow: int = 26,
    macd_signal: int = 9,
    bb_period: int = 20,
    bb_std: float = 2.0
) -> IndicatorValues:
    """
    Calculate all technical indicators at once.

    This is a convenience function that calculates RSI, MACD, and Bollinger
    Bands simultaneously and returns them in a structured container.

    Args:
        prices: List of closing prices (most recent last)
        rsi_period: Period for RSI calculation
        macd_fast: Fast period for MACD
        macd_slow: Slow period for MACD
        macd_signal: Signal period for MACD
        bb_period: Period for Bollinger Bands
        bb_std: Standard deviation multiplier for Bollinger Bands

    Returns:
        IndicatorValues dataclass with all calculated indicators

    Example:
        >>> prices = get_recent_prices("BTCUSDT", limit=100)
        >>> indicators = calculate_all_indicators(prices)
        >>> if indicators.is_valid():
        ...     print(f"RSI: {indicators.rsi:.2f}")
        ...     print(f"MACD: {indicators.macd:.2f}")
        ...     print(f"BB Upper: {indicators.bb_upper:.2f}")
    """
    result = IndicatorValues()

    # Validate input
    if not prices or len(prices) < max(rsi_period, macd_slow + macd_signal, bb_period):
        min_required = max(rsi_period + 1, macd_slow + macd_signal, bb_period)
        result.error_message = f"Insufficient data: need at least {min_required} prices, got {len(prices)}"
        logger.warning(result.error_message)
        return result

    try:
        # Calculate RSI
        result.rsi = calculate_rsi(prices, rsi_period)

        # Calculate MACD
        macd_result = calculate_macd(prices, macd_fast, macd_slow, macd_signal)
        if macd_result:
            result.macd, result.macd_signal, result.macd_histogram = macd_result

        # Calculate Bollinger Bands
        bb_result = calculate_bollinger_bands(prices, bb_period, bb_std)
        if bb_result:
            result.bb_upper, result.bb_middle, result.bb_lower, result.bb_bandwidth = bb_result

        # Check if we have sufficient valid data
        result.has_sufficient_data = (
            result.rsi is not None
            and result.macd is not None
            and result.bb_middle is not None
        )

        if result.has_sufficient_data:
            logger.debug(
                f"Calculated indicators - RSI: {result.rsi:.2f}, "
                f"MACD: {result.macd:.4f}, BB Middle: {result.bb_middle:.2f}"
            )
        else:
            result.error_message = "One or more indicators failed to calculate"
            logger.warning(result.error_message)

        return result

    except Exception as e:
        result.error_message = f"Error calculating indicators: {e}"
        logger.error(result.error_message)
        return result


def get_indicator_summary(indicators: IndicatorValues) -> str:
    """
    Generate a human-readable summary of indicator values.

    Args:
        indicators: IndicatorValues dataclass

    Returns:
        Formatted string summary of all indicators

    Example:
        >>> indicators = calculate_all_indicators(prices)
        >>> print(get_indicator_summary(indicators))
    """
    if not indicators.is_valid():
        return f"Invalid indicators: {indicators.error_message or 'Unknown error'}"

    summary_parts = [
        "=== Technical Indicators Summary ===",
        f"RSI: {indicators.rsi:.2f} ({'Oversold' if indicators.is_oversold() else 'Overbought' if indicators.is_overbought() else 'Neutral'})",
        f"MACD: {indicators.macd:.4f}",
        f"MACD Signal: {indicators.macd_signal:.4f}",
        f"MACD Histogram: {indicators.macd_histogram:.4f} ({'Bullish' if indicators.is_bullish_crossover() else 'Bearish' if indicators.is_bearish_crossover() else 'Neutral'})",
        f"Bollinger Upper: {indicators.bb_upper:.2f}",
        f"Bollinger Middle: {indicators.bb_middle:.2f}",
        f"Bollinger Lower: {indicators.bb_lower:.2f}",
        f"Bollinger Bandwidth: {indicators.bb_bandwidth:.4f}",
    ]

    return "\n".join(summary_parts)


def is_good_entry_point(
    indicators: IndicatorValues,
    signal_direction: str,
    rsi_oversold: float = 35.0,
    rsi_overbought: float = 65.0,
    current_price: Optional[float] = None
) -> Tuple[bool, str]:
    """
    Determine if current indicators suggest a good entry point.

    This function analyzes RSI, MACD, and Bollinger Bands to determine
    if the current market conditions align with the signal direction.

    Args:
        indicators: Calculated technical indicators
        signal_direction: "LONG" or "SHORT"
        rsi_oversold: RSI threshold for oversold condition
        rsi_overbought: RSI threshold for overbought condition
        current_price: Current market price, used to evaluate proximity to
            the Bollinger Bands. If omitted, the Bollinger Band check falls
            back to legacy (effectively dead) behavior - see
            IndicatorValues.near_bb_lower/near_bb_upper. Callers should pass
            the current price whenever available.

    Returns:
        Tuple of (is_good_entry: bool, reason: str)

    Example:
        >>> indicators = calculate_all_indicators(prices)
        >>> is_good, reason = is_good_entry_point(indicators, "LONG")
        >>> if is_good:
        ...     print(f"Good entry: {reason}")
    """
    if not indicators.is_valid():
        return False, f"Invalid indicators: {indicators.error_message}"

    direction = signal_direction.upper()
    reasons = []
    score = 0

    # For LONG positions
    if direction == "LONG":
        # Check RSI
        if indicators.rsi and indicators.rsi < rsi_oversold:
            score += 2
            reasons.append(f"RSI oversold ({indicators.rsi:.2f})")
        elif indicators.rsi and indicators.rsi < 50:
            score += 1
            reasons.append(f"RSI below neutral ({indicators.rsi:.2f})")

        # Check MACD
        if indicators.is_bullish_crossover():
            score += 2
            reasons.append("MACD bullish crossover")
        elif indicators.macd_histogram and indicators.macd_histogram > 0:
            score += 1
            reasons.append("MACD histogram positive")

        # Check Bollinger Bands
        if indicators.near_bb_lower(threshold_pct=10, current_price=current_price):
            score += 2
            reasons.append("Price near lower Bollinger Band")

        # Require at least score of 3 for good entry
        is_good = score >= 3
        reason = f"LONG entry score {score}/6: " + ", ".join(reasons) if reasons else "No favorable indicators"

    # For SHORT positions
    elif direction == "SHORT":
        # Check RSI
        if indicators.rsi and indicators.rsi > rsi_overbought:
            score += 2
            reasons.append(f"RSI overbought ({indicators.rsi:.2f})")
        elif indicators.rsi and indicators.rsi > 50:
            score += 1
            reasons.append(f"RSI above neutral ({indicators.rsi:.2f})")

        # Check MACD
        if indicators.is_bearish_crossover():
            score += 2
            reasons.append("MACD bearish crossover")
        elif indicators.macd_histogram and indicators.macd_histogram < 0:
            score += 1
            reasons.append("MACD histogram negative")

        # Check Bollinger Bands
        if indicators.near_bb_upper(threshold_pct=10, current_price=current_price):
            score += 2
            reasons.append("Price near upper Bollinger Band")

        # Require at least score of 3 for good entry
        is_good = score >= 3
        reason = f"SHORT entry score {score}/6: " + ", ".join(reasons) if reasons else "No favorable indicators"

    else:
        return False, f"Invalid signal direction: {direction}"

    return is_good, reason
