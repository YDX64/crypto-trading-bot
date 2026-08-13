"""Stochastic RSI / MACD / kesisim / S-R yakinlik indikatorleri + Strateji E.

Bu testler saf fonksiyonlari dogrular: IO yok, ag yok.
"""

import pytest

from src.strategies.scalper.indicators import (
    crossover,
    crossunder,
    macd,
    nearest_level,
    stoch_rsi_series,
)
from src.strategies.scalper.setups import StrategyE
from src.strategies.scalper.types import Candle, Direction, Regime, StrategyContext


# ----------------------------------------------------------------------
# crossover / crossunder
# ----------------------------------------------------------------------


def test_crossover_detects_only_the_transition_bar():
    # onceki mumda 1<=2, son mumda 3>2 -> kesisim
    assert crossover([1.0, 3.0], [2.0, 2.0]) is True
    # zaten ustunde kalmaya devam ediyor -> kesisim DEGIL
    assert crossover([3.0, 4.0], [2.0, 2.0]) is False


def test_crossunder_is_the_mirror():
    assert crossunder([3.0, 1.0], [2.0, 2.0]) is True
    assert crossunder([1.0, 0.5], [2.0, 2.0]) is False


def test_cross_helpers_need_two_bars():
    assert crossover([1.0], [2.0]) is False
    assert crossunder([1.0], [2.0]) is False


def test_touching_without_passing_is_not_a_crossover():
    # esitlik gecis sayilmaz; kesin buyuk olmali
    assert crossover([1.0, 2.0], [2.0, 2.0]) is False


# ----------------------------------------------------------------------
# stoch_rsi_series
# ----------------------------------------------------------------------


def test_stoch_rsi_returns_aligned_series():
    closes = [float(100 + (i % 7)) for i in range(120)]
    k, d = stoch_rsi_series(closes)
    assert len(k) == len(closes)
    assert len(d) == len(closes)


def test_stoch_rsi_stays_in_range():
    closes = [float(100 + (i % 11) * 3) for i in range(150)]
    k, d = stoch_rsi_series(closes)
    assert all(0.0 <= v <= 100.0 for v in k)
    assert all(0.0 <= v <= 100.0 for v in d)


def test_stoch_rsi_flat_input_is_neutral_not_crossing():
    """Duz seri sahte kesisim uretmemeli (isinma tuzagi)."""
    closes = [100.0] * 120
    k, d = stoch_rsi_series(closes)
    assert crossover(k, d) is False
    assert crossunder(k, d) is False


def test_stoch_rsi_empty_input():
    k, d = stoch_rsi_series([])
    assert k == [] and d == []


# ----------------------------------------------------------------------
# macd
# ----------------------------------------------------------------------


def test_macd_histogram_is_line_minus_signal():
    closes = [float(100 + i) for i in range(80)]
    line, signal, hist = macd(closes)
    assert len(line) == len(signal) == len(hist) == len(closes)
    for i in range(len(closes)):
        assert hist[i] == pytest.approx(line[i] - signal[i])


def test_macd_positive_in_uptrend():
    closes = [float(100 + i * 2) for i in range(120)]
    line, _signal, _hist = macd(closes)
    assert line[-1] > 0.0


def test_macd_empty_input():
    assert macd([]) == ([], [], [])


# ----------------------------------------------------------------------
# nearest_level
# ----------------------------------------------------------------------


def test_nearest_level_picks_closest_within_range():
    levels = [{"price": 90.0, "count": 3}, {"price": 99.0, "count": 2}]
    got = nearest_level(100.0, levels, max_distance=5.0)
    assert got is not None and got["price"] == 99.0


def test_nearest_level_returns_none_when_out_of_range():
    levels = [{"price": 50.0, "count": 3}]
    assert nearest_level(100.0, levels, max_distance=5.0) is None


def test_nearest_level_guards_bad_input():
    assert nearest_level(0.0, [{"price": 10.0}], 5.0) is None
    assert nearest_level(100.0, [], 5.0) is None
    assert nearest_level(100.0, [{"price": 99.0}], 0.0) is None


# ----------------------------------------------------------------------
# Strateji E
# ----------------------------------------------------------------------


def _candle(i, o, h, l, c, v=1000.0):
    return Candle(open_time=i * 300_000, open=o, high=h, low=l, close=c,
                  volume=v, close_time=i * 300_000 + 299_999)


def _ctx(candles, regime=Regime.RANGE, price=None, atr_5m=1.0):
    return StrategyContext(
        symbol="TESTUSDT",
        regime=regime,
        candles_4h=[],
        candles_15m=[],
        candles_5m=candles,
        current_price=price if price is not None else candles[-1].close,
        atr_5m=atr_5m,
        leverage=10,
    )


def test_strategy_e_blocks_unknown_regime():
    candles = [_candle(i, 100, 101, 99, 100) for i in range(80)]
    assert StrategyE().evaluate(_ctx(candles, regime=Regime.UNKNOWN)) is None


def test_strategy_e_needs_enough_candles():
    candles = [_candle(i, 100, 101, 99, 100) for i in range(10)]
    assert StrategyE().evaluate(_ctx(candles)) is None


def test_strategy_e_requires_positive_atr():
    candles = [_candle(i, 100, 101, 99, 100) for i in range(80)]
    assert StrategyE().evaluate(_ctx(candles, atr_5m=0.0)) is None


def test_strategy_e_no_signal_on_flat_market():
    """Seviye de kesisim de yokken sinyal uretmemeli."""
    candles = [_candle(i, 100, 100.5, 99.5, 100) for i in range(120)]
    assert StrategyE().evaluate(_ctx(candles)) is None


def test_strategy_e_is_pure_does_not_mutate_context():
    candles = [_candle(i, 100, 101, 99, 100 + (i % 5)) for i in range(120)]
    ctx = _ctx(candles)
    before = [c.close for c in ctx.candles_5m]
    StrategyE().evaluate(ctx)
    assert [c.close for c in ctx.candles_5m] == before


def test_strategy_e_registered_in_get_enabled():
    from src.strategies.scalper.setups import get_enabled

    enabled = get_enabled("E")
    assert len(enabled) == 1
    assert enabled[0].name == "E"
