"""
src/strategies/scalper/indicators.py için birim testleri.

Her fonksiyon için: bilinen değerli doğrulama, yetersiz veri davranışı ve
(uygunsa) yapısal/monoton davranış testleri. Tüm hesaplamalar elle
doğrulanmış sabit sayılarla karşılaştırılır.
"""

from __future__ import annotations

import pytest

from src.strategies.scalper.indicators import (
    atr,
    atr_series,
    bearish_divergence,
    bollinger,
    bullish_divergence,
    chandelier_stop,
    donchian,
    ema,
    last_swing_high,
    last_swing_low,
    rsi_series,
    swing_points,
)
from src.strategies.scalper.types import Candle, Direction


def _mk_candle(i: int, high: float, low: float, close: float | None = None) -> Candle:
    """Tek bir sentetik mum üretir (open_time/close_time = i)."""
    c = close if close is not None else (high + low) / 2
    return Candle(
        open_time=i,
        open=c,
        high=high,
        low=low,
        close=c,
        volume=1.0,
        close_time=i,
    )


def _mk_candles(highs: list[float], lows: list[float],
                 closes: list[float] | None = None) -> list[Candle]:
    assert len(highs) == len(lows)
    if closes is None:
        closes = [(h + l) / 2 for h, l in zip(highs, lows)]
    return [_mk_candle(i, h, l, c) for i, (h, l, c) in enumerate(zip(highs, lows, closes))]


# --------------------------------------------------------------------------
# ema
# --------------------------------------------------------------------------

class TestEma:
    def test_known_values_period_3(self):
        # Elle hesap: k=2/(3+1)=0.5
        # i0: cum=1 -> 1/1=1.0 | i1: cum=3 -> 3/2=1.5 | i2 (seed): cum=6 -> 6/3=2.0
        # i3: 4*0.5+2*0.5=3.0 | i4: 5*0.5+3*0.5=4.0 | i5: 6*0.5+4*0.5=5.0
        values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        result = ema(values, 3)
        assert result == pytest.approx([1.0, 1.5, 2.0, 3.0, 4.0, 5.0])

    def test_same_length_as_input(self):
        values = [float(i) for i in range(10)]
        assert len(ema(values, 4)) == len(values)

    def test_empty_input(self):
        assert ema([], 5) == []

    def test_insufficient_data_all_cumulative_sma(self):
        # n < period -> hiç gerçek EMA'ya ulaşılmaz, hepsi kümülatif SMA
        values = [2.0, 4.0, 6.0]
        result = ema(values, 10)
        assert result == pytest.approx([2.0, 3.0, 4.0])

    def test_period_one_equals_input(self):
        values = [5.0, 1.0, 9.0, 3.0]
        assert ema(values, 1) == pytest.approx(values)


# --------------------------------------------------------------------------
# rsi_series
# --------------------------------------------------------------------------

class TestRsiSeries:
    def test_known_values_period_2(self):
        # closes=[10,12,11,13], period=2
        # deltas=[2,-1,2] -> gains=[2,0,2] losses=[0,1,0]
        # seed: avg_gain=(2+0)/2=1.0, avg_loss=(0+1)/2=0.5 -> RSI=100-100/3=66.6667
        # i=3: gain=2, loss=0 -> avg_gain=(1.0*1+2)/2=1.5, avg_loss=(0.5*1+0)/2=0.25
        #      RS=6.0 -> RSI=100-100/7=85.7143
        closes = [10.0, 12.0, 11.0, 13.0]
        result = rsi_series(closes, period=2)
        assert result[0] == pytest.approx(50.0)
        assert result[1] == pytest.approx(50.0)
        assert result[2] == pytest.approx(66.66666667, abs=1e-4)
        assert result[3] == pytest.approx(85.71428571, abs=1e-4)

    def test_same_length_as_input(self):
        closes = [float(i) for i in range(30)]
        assert len(rsi_series(closes, period=14)) == len(closes)

    def test_monotonic_increasing_series_approaches_100(self):
        closes = [100.0 + i for i in range(40)]
        result = rsi_series(closes, period=14)
        # Warmup sonrası tüm değerler 100'e çok yakın olmalı (yalnız kazanç var)
        for v in result[20:]:
            assert v == pytest.approx(100.0, abs=1e-6)

    def test_monotonic_decreasing_series_approaches_0(self):
        closes = [200.0 - i for i in range(40)]
        result = rsi_series(closes, period=14)
        for v in result[20:]:
            assert v == pytest.approx(0.0, abs=1e-6)

    def test_insufficient_data_all_neutral(self):
        closes = [1.0, 2.0, 3.0]
        result = rsi_series(closes, period=14)
        assert result == [50.0, 50.0, 50.0]

    def test_empty_input(self):
        assert rsi_series([], period=14) == []


# --------------------------------------------------------------------------
# atr / atr_series
# --------------------------------------------------------------------------

class TestAtr:
    def _candles(self) -> list[Candle]:
        # Elle hesap (period=2):
        # c0: H=10 L=8  C=9
        # c1: H=12 L=9  C=11  TR1=max(12-9=3, |12-9|=3, |9-9|=0)=3
        # c2: H=11 L=7  C=8   TR2=max(11-7=4, |11-11|=0, |7-11|=4)=4
        # c3: H=13 L=10 C=12  TR3=max(13-10=3, |13-8|=5, |10-8|=2)=5
        # seed(idx2)=mean(3,4)=3.5 ; idx3=(3.5*1+5)/2=4.25
        return [
            _mk_candle(0, high=10, low=8, close=9),
            _mk_candle(1, high=12, low=9, close=11),
            _mk_candle(2, high=11, low=7, close=8),
            _mk_candle(3, high=13, low=10, close=12),
        ]

    def test_atr_series_known_values(self):
        result = atr_series(self._candles(), period=2)
        assert result == pytest.approx([0.0, 0.0, 3.5, 4.25])

    def test_atr_single_value_matches_series_last(self):
        candles = self._candles()
        assert atr(candles, period=2) == pytest.approx(4.25)

    def test_atr_series_same_length_as_input(self):
        candles = self._candles()
        assert len(atr_series(candles, period=2)) == len(candles)

    def test_atr_insufficient_data_returns_zero(self):
        candles = self._candles()[:2]  # n=2 < period+1=3
        assert atr(candles, period=2) == 0.0

    def test_atr_series_insufficient_data_all_zero(self):
        candles = self._candles()[:2]
        assert atr_series(candles, period=2) == [0.0, 0.0]

    def test_atr_empty_input(self):
        assert atr([], period=14) == 0.0
        assert atr_series([], period=14) == []


# --------------------------------------------------------------------------
# bollinger
# --------------------------------------------------------------------------

class TestBollinger:
    def test_known_values(self):
        # closes=[10,12,14,16,18] period=5 -> mean=14
        # variance = (16+4+0+4+16)/5 = 8 -> std = sqrt(8) = 2.828427...
        closes = [10.0, 12.0, 14.0, 16.0, 18.0]
        upper, middle, lower = bollinger(closes, period=5, std_mult=2.0)
        assert middle == pytest.approx(14.0)
        assert upper == pytest.approx(14.0 + 2 * (8 ** 0.5))
        assert lower == pytest.approx(14.0 - 2 * (8 ** 0.5))

    def test_constant_series_zero_width(self):
        closes = [5.0] * 20
        upper, middle, lower = bollinger(closes, period=20)
        assert upper == pytest.approx(5.0)
        assert middle == pytest.approx(5.0)
        assert lower == pytest.approx(5.0)

    def test_uses_only_last_period_window(self):
        # İlk elemanlar aşırı uç değerde olsa da son `period` pencere kullanılmalı
        closes = [1000.0, -1000.0] + [10.0, 12.0, 14.0, 16.0, 18.0]
        upper, middle, lower = bollinger(closes, period=5, std_mult=2.0)
        assert middle == pytest.approx(14.0)

    def test_insufficient_data(self):
        closes = [1.0, 2.0]
        assert bollinger(closes, period=5) == (0.0, 0.0, 0.0)


# --------------------------------------------------------------------------
# donchian
# --------------------------------------------------------------------------

class TestDonchian:
    def _candles(self) -> list[Candle]:
        highs = [10, 12, 9, 15, 20]
        lows = [5, 6, 4, 8, 1]
        return _mk_candles(highs, lows)

    def test_exclude_last_true(self):
        # window = idx[1,2,3] -> highs[12,9,15] max=15 ; lows[6,4,8] min=4
        highest, lowest = donchian(self._candles(), period=3, exclude_last=True)
        assert (highest, lowest) == (15.0, 4.0)

    def test_exclude_last_false(self):
        # window = idx[2,3,4] -> highs[9,15,20] max=20 ; lows[4,8,1] min=1
        highest, lowest = donchian(self._candles(), period=3, exclude_last=False)
        assert (highest, lowest) == (20.0, 1.0)

    def test_exclude_last_ignores_breakout_candle(self):
        # Son mum (idx4) en uç değerlere sahip; exclude_last=True bunu
        # kanala dahil etmemeli (kırılma tespiti amaçlanan davranış).
        highest, _ = donchian(self._candles(), period=3, exclude_last=True)
        assert highest != 20.0

    def test_insufficient_data_exclude_last_false(self):
        candles = self._candles()[:2]
        assert donchian(candles, period=3, exclude_last=False) == (0.0, 0.0)

    def test_insufficient_data_exclude_last_true(self):
        candles = self._candles()[:3]  # n=3 < period+1=4
        assert donchian(candles, period=3, exclude_last=True) == (0.0, 0.0)


# --------------------------------------------------------------------------
# swing_points / last_swing_low / last_swing_high
# --------------------------------------------------------------------------

class TestSwingPoints:
    def test_swing_lows_zigzag(self):
        # low: 10,8,10,6,10,8,10 (left=1,right=1) -> dip indeksleri 1,3,5
        lows = [10, 8, 10, 6, 10, 8, 10]
        highs = [20] * len(lows)  # sabit -> hiçbir swing-high üretmemeli
        candles = _mk_candles(highs, lows)
        highs_idx, lows_idx = swing_points(candles, left=1, right=1)
        assert lows_idx == [1, 3, 5]
        assert highs_idx == []

    def test_swing_highs_zigzag(self):
        # high: 5,10,5,12,5,10,5 (left=1,right=1) -> tepe indeksleri 1,3,5
        highs = [5, 10, 5, 12, 5, 10, 5]
        lows = [1] * len(highs)  # sabit -> hiçbir swing-low üretmemeli
        candles = _mk_candles(highs, lows)
        highs_idx, lows_idx = swing_points(candles, left=1, right=1)
        assert highs_idx == [1, 3, 5]
        assert lows_idx == []

    def test_edges_cannot_be_swings(self):
        # left=3,right=3 varsayılanla ilk 3 ve son 3 mum aday olamaz
        lows = [1, 2, 3, 0, 3, 2, 1]  # dip tam ortada (idx3)
        highs = [10] * len(lows)
        candles = _mk_candles(highs, lows)
        _, lows_idx = swing_points(candles, left=3, right=3)
        assert lows_idx == [3]

    def test_insufficient_data_returns_empty(self):
        candles = _mk_candles([10, 11, 12], [5, 6, 7])
        highs_idx, lows_idx = swing_points(candles, left=3, right=3)
        assert highs_idx == []
        assert lows_idx == []


class TestLastSwing:
    def test_last_swing_low(self):
        lows = [10, 8, 10, 6, 10, 8, 10]
        highs = [20] * len(lows)
        candles = _mk_candles(highs, lows)
        assert last_swing_low(candles, left=1, right=1) == pytest.approx(8.0)

    def test_last_swing_high(self):
        highs = [5, 10, 5, 12, 5, 10, 5]
        lows = [1] * len(highs)
        candles = _mk_candles(highs, lows)
        assert last_swing_high(candles, left=1, right=1) == pytest.approx(10.0)

    def test_last_swing_low_none_when_absent(self):
        candles = _mk_candles([10, 11], [5, 6])
        assert last_swing_low(candles) is None

    def test_last_swing_high_none_when_absent(self):
        candles = _mk_candles([10, 11], [5, 6])
        assert last_swing_high(candles) is None


# --------------------------------------------------------------------------
# bullish_divergence / bearish_divergence
# --------------------------------------------------------------------------

class TestDivergence:
    def test_bullish_divergence_true(self):
        # low dip1=idx4 (80), dip2=idx12 (70) -> fiyat daha düşük dip
        lows = [100, 95, 90, 85, 80, 85, 90, 95, 100, 95, 90, 85, 70, 85, 100]
        highs = [l + 10 for l in lows]
        candles = _mk_candles(highs, lows)
        rsi_values = [50.0] * len(candles)
        rsi_values[4] = 30.0   # ilk dip: düşük RSI (oversold)
        rsi_values[12] = 40.0  # ikinci dip: daha yüksek RSI -> diverjans

        assert bullish_divergence(candles, rsi_values, lookback=30) is True

    def test_bullish_divergence_false_no_divergence(self):
        lows = [100, 95, 90, 85, 80, 85, 90, 95, 100, 95, 90, 85, 70, 85, 100]
        highs = [l + 10 for l in lows]
        candles = _mk_candles(highs, lows)
        rsi_values = [50.0] * len(candles)
        rsi_values[4] = 40.0   # RSI de düşüyor (diverjans yok)
        rsi_values[12] = 30.0

        assert bullish_divergence(candles, rsi_values, lookback=30) is False

    def test_bullish_divergence_false_insufficient_swings(self):
        lows = [10, 9, 8, 7, 6]
        highs = [l + 10 for l in lows]
        candles = _mk_candles(highs, lows)
        rsi_values = [50.0] * len(candles)
        assert bullish_divergence(candles, rsi_values, lookback=30) is False

    def test_bearish_divergence_true(self):
        # high tepe1=idx4 (120), tepe2=idx12 (130) -> fiyat daha yüksek tepe
        highs = [100, 105, 110, 115, 120, 115, 110, 105, 100, 105, 110, 115, 130, 115, 100]
        lows = [h - 10 for h in highs]
        candles = _mk_candles(highs, lows)
        rsi_values = [50.0] * len(candles)
        rsi_values[4] = 70.0   # ilk tepe: yüksek RSI
        rsi_values[12] = 60.0  # ikinci tepe: daha düşük RSI -> diverjans

        assert bearish_divergence(candles, rsi_values, lookback=30) is True

    def test_bearish_divergence_false_no_divergence(self):
        highs = [100, 105, 110, 115, 120, 115, 110, 105, 100, 105, 110, 115, 130, 115, 100]
        lows = [h - 10 for h in highs]
        candles = _mk_candles(highs, lows)
        rsi_values = [50.0] * len(candles)
        rsi_values[4] = 60.0
        rsi_values[12] = 70.0  # RSI de yükseliyor (diverjans yok)

        assert bearish_divergence(candles, rsi_values, lookback=30) is False

    def test_bearish_divergence_false_insufficient_swings(self):
        highs = [10, 11, 12, 13, 14]
        lows = [h - 10 for h in highs]
        candles = _mk_candles(highs, lows)
        rsi_values = [50.0] * len(candles)
        assert bearish_divergence(candles, rsi_values, lookback=30) is False


# --------------------------------------------------------------------------
# chandelier_stop
# --------------------------------------------------------------------------

class TestChandelierStop:
    def _uptrend_candles(self, n: int) -> list[Candle]:
        candles = []
        for i in range(n):
            base = 100.0 + i
            candles.append(_mk_candle(i, high=base + 1, low=base - 1, close=base))
        return candles

    def _downtrend_candles(self, n: int) -> list[Candle]:
        candles = []
        for i in range(n):
            base = 200.0 - i
            candles.append(_mk_candle(i, high=base + 1, low=base - 1, close=base))
        return candles

    def test_long_formula(self):
        candles = self._uptrend_candles(20)
        result = chandelier_stop(candles, Direction.LONG, atr_mult=2.5,
                                  atr_period=14, since_index=0)
        expected_high = max(c.high for c in candles)
        expected_atr = atr(candles, 14)
        assert result == pytest.approx(expected_high - 2.5 * expected_atr)

    def test_short_formula(self):
        candles = self._downtrend_candles(20)
        result = chandelier_stop(candles, Direction.SHORT, atr_mult=2.5,
                                  atr_period=14, since_index=0)
        expected_low = min(c.low for c in candles)
        expected_atr = atr(candles, 14)
        assert result == pytest.approx(expected_low + 2.5 * expected_atr)

    def test_long_stop_only_rises_with_uptrend_two_since_indexes(self):
        # Fiyat yükselirken (uptrend), zaman ilerledikçe (daha fazla mum
        # eklendikçe) chandelier stop yalnızca yükselmeli (asla düşmemeli).
        # İki farklı since_index ile doğrulanır.
        full = self._uptrend_candles(60)
        for since_index in (0, 20):
            stops = []
            for count in range(30, 61):
                partial = full[:count]
                s = chandelier_stop(partial, Direction.LONG, since_index=since_index)
                stops.append(s)
            for prev, curr in zip(stops, stops[1:]):
                assert curr >= prev - 1e-9, f"since_index={since_index}: stop düştü"
            # Gerçekten hareket ettiğini de doğrula (sabit kalmamış)
            assert stops[-1] > stops[0]

    def test_insufficient_data_returns_zero(self):
        candles = self._uptrend_candles(5)  # n=5 < atr_period+1=15
        result = chandelier_stop(candles, Direction.LONG, atr_period=14)
        assert result == 0.0

    def test_invalid_since_index_returns_zero(self):
        candles = self._uptrend_candles(20)
        assert chandelier_stop(candles, Direction.LONG, since_index=999) == 0.0
        assert chandelier_stop(candles, Direction.LONG, since_index=-1) == 0.0

    def test_empty_candles_returns_zero(self):
        assert chandelier_stop([], Direction.LONG) == 0.0
