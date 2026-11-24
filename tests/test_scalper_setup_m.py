"""
Strateji M (D44/E18 ön-kayıtlı momentum girişi) birim + harness testleri.

M varsayılan KAPALIDIR (scalper_strategies varsayılanında yok). Kurallar
ön-kayıtlıdır; bu testler kuralları BİREBİR kilitler.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.core.config import Settings
from src.strategies.scalper import setups
from src.strategies.scalper.backtest import simulate_symbol
from src.strategies.scalper.indicators import atr, ema
from src.strategies.scalper.setups import ALL_STRATEGIES, StrategyM, get_enabled
from src.strategies.scalper.types import Candle, Direction, Regime
from tests.test_entry_gates import _REQUIRED_SETTINGS
from tests.test_scalper_setups import _ctx, _flat_candles, _leg, _mk, _mk_ohlc
from tests.test_scalper_stop_policy import _mk_candle, _sim_cfg

M = StrategyM()


@pytest.fixture(autouse=True)
def _default_modes(monkeypatch):
    monkeypatch.setattr(setups.settings, "scalper_m_trigger", "breakout")
    monkeypatch.setattr(setups.settings, "scalper_m_trend", "regime")


def _modes(monkeypatch, trigger=None, trend=None):
    if trigger is not None:
        monkeypatch.setattr(setups.settings, "scalper_m_trigger", trigger)
    if trend is not None:
        monkeypatch.setattr(setups.settings, "scalper_m_trend", trend)


# --------------------------------------------------------------------------
# Fixture'lar
# --------------------------------------------------------------------------

def _breakout_long(volume: float = 30.0, close: float = 103.0) -> list[Candle]:
    c = _flat_candles(60)  # high 101 / low 99, hacim 10
    c.append(_mk_ohlc(60, 100.0, close + 0.5, 100.5, close, volume))
    return c


def _mirror(candles: list[Candle], k: float = 300.0) -> list[Candle]:
    return [
        _mk_ohlc(c.open_time, k - c.open, k - c.low, k - c.high, k - c.close, c.volume)
        for c in candles
    ]


def _trend(n: int, start: float, step: float) -> list[Candle]:
    out, p = [], start
    for i in range(n):
        o, c = p, p + step
        p = c
        out.append(_mk_ohlc(i, o, max(o, c) + 0.1, min(o, c) - 0.1, c))
    return out


def _pullback_long(dips=(1.5, 1.5, 1.5, 1.0), last_up: float = 1.2,
                   prev_high_boost: float = 0.0) -> list[Candle]:
    """Yükseliş + 4 mumluk geri çekilme (hızlı EMA'ya dokunur) + boğa mumu."""
    c = _trend(60, 100.0, 0.5)
    p = c[-1].close
    for k, d in enumerate(dips):
        o, cl = p, p - d
        hi = o + 0.1 + (prev_high_boost if k == len(dips) - 1 else 0.0)
        c.append(_mk_ohlc(len(c), o, hi, cl - 0.1, cl))
        p = cl
    c.append(_mk_ohlc(len(c), p, p + last_up + 0.1, p - 0.1, p + last_up))
    return c


def _ctx15_up(n: int = 60) -> list[Candle]:
    return _trend(n, 100.0, 1.0)


def _ctx15_down(n: int = 60) -> list[Candle]:
    return _trend(n, 300.0, -1.0)


# --------------------------------------------------------------------------
# Breakout + regime
# --------------------------------------------------------------------------

class TestBreakoutRegime:
    def test_long(self):
        sig = M.evaluate(_ctx(_breakout_long(), Regime.UP))
        assert sig is not None
        assert sig.strategy == "M" and sig.direction == Direction.LONG
        assert sig.regime == Regime.UP
        assert sig.score == pytest.approx((103.0 - 101.0) / sig.atr_5m)

    def test_short(self):
        sig = M.evaluate(_ctx(_mirror(_breakout_long()), Regime.DOWN))
        assert sig is not None and sig.direction == Direction.SHORT
        assert sig.score == pytest.approx((199.0 - 197.0) / sig.atr_5m)

    @pytest.mark.parametrize("regime", [Regime.RANGE, Regime.UNKNOWN])
    def test_no_signal_without_trend(self, regime):
        assert M.evaluate(_ctx(_breakout_long(), regime)) is None

    def test_volume_ratio_must_exceed_1_5(self):
        # oran tam 1.5 → reddedilir; 1.6 → geçer
        assert M.evaluate(_ctx(_breakout_long(volume=15.0), Regime.UP)) is None
        assert M.evaluate(_ctx(_breakout_long(volume=16.0), Regime.UP)) is not None

    def test_close_must_exceed_donchian(self):
        assert M.evaluate(_ctx(_breakout_long(close=100.9), Regime.UP)) is None
        assert M.evaluate(_ctx(_breakout_long(close=101.0), Regime.UP)) is None

    def test_short_close_must_break_low(self):
        c = _mirror(_breakout_long(close=100.9))
        assert M.evaluate(_ctx(c, Regime.DOWN)) is None

    def test_direction_follows_regime_only(self):
        # yukarı kırılım ama rejim DOWN → SHORT aranır → kırılım yok → None
        assert M.evaluate(_ctx(_breakout_long(), Regime.DOWN)) is None

    def test_too_few_candles(self):
        c = _flat_candles(19)
        c.append(_mk_ohlc(19, 100.0, 103.5, 100.5, 103.0, 30.0))
        assert M.evaluate(_ctx(c, Regime.UP)) is None


# --------------------------------------------------------------------------
# Bağlam (context) trendi
# --------------------------------------------------------------------------

class TestContextTrend:
    def test_long(self, monkeypatch):
        _modes(monkeypatch, trend="context")
        sig = M.evaluate(_ctx(_breakout_long(), Regime.RANGE, candles_15m=_ctx15_up()))
        assert sig is not None and sig.direction == Direction.LONG

    def test_short(self, monkeypatch):
        _modes(monkeypatch, trend="context")
        sig = M.evaluate(_ctx(_mirror(_breakout_long()), Regime.RANGE,
                              candles_15m=_ctx15_down()))
        assert sig is not None and sig.direction == Direction.SHORT

    def test_mixed_emas_none(self, monkeypatch):
        _modes(monkeypatch, trend="context")
        flat = _flat_candles(60)
        assert M.evaluate(_ctx(_breakout_long(), Regime.UP, candles_15m=flat)) is None
        # yükseliş sonrası sert dip: kapanış < EMA20 ama EMA20 > EMA50
        dip = _ctx15_up(59) + [_mk_ohlc(59, 159.0, 159.1, 140.0, 140.5)]
        cl = [x.close for x in dip]
        assert cl[-1] < ema(cl, 20)[-1] and ema(cl, 20)[-1] > ema(cl, 50)[-1]
        assert M.evaluate(_ctx(_breakout_long(), Regime.UP, candles_15m=dip)) is None

    def test_regime_ignored(self, monkeypatch):
        _modes(monkeypatch, trend="context")
        for regime in (Regime.DOWN, Regime.RANGE, Regime.UNKNOWN):
            sig = M.evaluate(_ctx(_breakout_long(), regime, candles_15m=_ctx15_up()))
            assert sig is not None and sig.direction == Direction.LONG
            assert sig.regime == regime

    def test_context_needs_51_candles(self, monkeypatch):
        _modes(monkeypatch, trend="context")
        assert M.evaluate(_ctx(_breakout_long(), Regime.UP,
                               candles_15m=_ctx15_up(50))) is None
        assert M.evaluate(_ctx(_breakout_long(), Regime.UP,
                               candles_15m=_ctx15_up(51))) is not None

    def test_unknown_modes_give_none(self, monkeypatch):
        _modes(monkeypatch, trend="bogus")
        assert M.evaluate(_ctx(_breakout_long(), Regime.UP)) is None
        _modes(monkeypatch, trend="regime", trigger="bogus")
        assert M.evaluate(_ctx(_breakout_long(), Regime.UP)) is None


# --------------------------------------------------------------------------
# Pullback
# --------------------------------------------------------------------------

class TestPullback:
    @pytest.fixture(autouse=True)
    def _pb(self, monkeypatch):
        _modes(monkeypatch, trigger="pullback")

    def test_fixture_is_sound(self):
        c = _pullback_long()
        cl = [x.close for x in c]
        ef, es = ema(cl, 20), ema(cl, 50)
        n = len(c)
        assert ef[-1] > es[-1]
        assert any(c[i].low <= ef[i] for i in range(n - 6, n - 1))

    def test_long(self):
        c = _pullback_long()
        sig = M.evaluate(_ctx(c, Regime.UP))
        assert sig is not None and sig.direction == Direction.LONG
        assert sig.score == pytest.approx((c[-1].close - c[-2].high) / sig.atr_5m)

    def test_short(self):
        c = _mirror(_pullback_long())
        sig = M.evaluate(_ctx(c, Regime.DOWN))
        assert sig is not None and sig.direction == Direction.SHORT
        assert sig.score == pytest.approx((c[-2].low - c[-1].close) / sig.atr_5m)

    def test_fast_ema_not_above_slow(self):
        # düşüş + güçlü sıçrama mumu: son mum koşulları sağlar, EMA sırası bozuk
        c = _trend(60, 200.0, -0.5)
        cl = [x.close for x in c]
        k = 2.0 / 21
        last_close = ema(cl, 20)[-1] + 0.2 / (1 - k)
        p = c[-1].close
        c.append(_mk_ohlc(60, p, last_close + 0.1, p - 0.1, last_close))
        cl = [x.close for x in c]
        ef, es = ema(cl, 20), ema(cl, 50)
        assert ef[-1] < es[-1]
        assert c[-1].close > c[-1].open and c[-1].close > c[-2].high
        assert 0 < c[-1].close - ef[-1] <= atr(c, 14)
        assert M.evaluate(_ctx(c, Regime.UP)) is None

    def test_no_touch_in_previous_five(self):
        # küçük geri çekilme: dipler hızlı EMA'nın üstünde kalır
        c = _pullback_long(dips=(1.2, 1.2, 1.0, 0.3), last_up=0.45)
        cl = [x.close for x in c]
        ef, es = ema(cl, 20), ema(cl, 50)
        n = len(c)
        assert ef[-1] > es[-1]
        assert not any(c[i].low <= ef[i] for i in range(n - 6, n - 1))
        assert c[-1].close > c[-2].high and c[-1].close > c[-1].open
        assert (c[-1].close - ef[-1]) <= atr(c, 14)  # yalnız dokunuş eksik
        assert M.evaluate(_ctx(c, Regime.UP)) is None

    def test_last_close_not_above_prev_high(self):
        c = _pullback_long(prev_high_boost=2.0)
        assert c[-1].close <= c[-2].high and c[-1].close > c[-1].open
        assert M.evaluate(_ctx(c, Regime.UP)) is None

    def test_extension_over_one_atr(self):
        c = _pullback_long(last_up=6.0)
        cl = [x.close for x in c]
        ef = ema(cl, 20)
        assert (c[-1].close - ef[-1]) > atr(c, 14)
        assert c[-1].close > c[-2].high
        assert M.evaluate(_ctx(c, Regime.UP)) is None

    def test_too_few_candles(self):
        assert M.evaluate(_ctx(_pullback_long()[-50:], Regime.UP)) is None

    def test_context_trend_with_pullback(self, monkeypatch):
        _modes(monkeypatch, trend="context")
        sig = M.evaluate(_ctx(_pullback_long(), Regime.RANGE, candles_15m=_ctx15_up()))
        assert sig is not None and sig.direction == Direction.LONG


# --------------------------------------------------------------------------
# Ortak: atr, stop, saflık, kayıt
# --------------------------------------------------------------------------

def _ctx_atr(candles, regime, atr_val, **kw):
    return replace(_ctx(candles, regime, **kw), atr_5m=atr_val)


class TestCommon:
    @pytest.mark.parametrize("atr_val", [0.0, -1.0])
    def test_non_positive_atr(self, atr_val):
        assert M.evaluate(_ctx_atr(_breakout_long(), Regime.UP, atr_val)) is None

    def test_stop_exactly_two_atr(self):
        sig = M.evaluate(_ctx(_breakout_long(), Regime.UP))
        assert sig.stop_price == pytest.approx(sig.entry_price - 2.0 * sig.atr_5m)
        assert sig.stop_price < sig.entry_price
        c = _mirror(_breakout_long())
        sig = M.evaluate(_ctx(c, Regime.DOWN))
        assert sig.stop_price == pytest.approx(sig.entry_price + 2.0 * sig.atr_5m)
        assert sig.stop_price > sig.entry_price

    def test_long_stop_not_positive_returns_none(self):
        assert M.evaluate(_ctx_atr(_breakout_long(), Regime.UP, 60.0)) is None

    def test_pure(self):
        c = _breakout_long()
        ctx = _ctx(c, Regime.UP, candles_15m=_ctx15_up())
        before = (list(ctx.candles_5m), list(ctx.candles_15m), ctx.atr_5m)
        a, b = M.evaluate(ctx), M.evaluate(ctx)
        assert a is not None and a == b
        assert (ctx.candles_5m, ctx.candles_15m, ctx.atr_5m) == before

    def test_registry_and_get_enabled(self):
        assert [s.name for s in ALL_STRATEGIES][-1] == "M"
        assert get_enabled("M") == [ALL_STRATEGIES[-1]]
        assert isinstance(get_enabled("M")[0], StrategyM)
        assert [s.name for s in get_enabled("C,M")] == ["C", "M"]
        assert [s.name for s in get_enabled("M,C")] == ["M", "C"]

    def test_default_off(self):
        default = Settings.model_fields["scalper_strategies"].default
        assert "M" not in [x.strip().upper() for x in default.split(",")]


# --------------------------------------------------------------------------
# Ayar doğrulayıcı + env.example
# --------------------------------------------------------------------------

class TestSettingsM:
    @staticmethod
    def _make(**fields) -> Settings:
        return Settings(_env_file=None, **_REQUIRED_SETTINGS, **fields)

    def test_defaults(self):
        s = self._make()
        assert (s.scalper_m_trigger, s.scalper_m_trend) == ("breakout", "regime")

    def test_normalises(self):
        s = self._make(scalper_m_trigger=" Pullback ", scalper_m_trend=" CONTEXT ")
        assert (s.scalper_m_trigger, s.scalper_m_trend) == ("pullback", "context")
        assert self._make(scalper_m_trigger=" Breakout ").scalper_m_trigger == "breakout"

    @pytest.mark.parametrize("field", ["scalper_m_trigger", "scalper_m_trend"])
    def test_rejects_unknown(self, field):
        with pytest.raises(ValidationError):
            self._make(**{field: "foo"})

    def test_env_example_ships_commented(self):
        text = Path("env.example").read_text(encoding="utf-8")
        for key in ("SCALPER_M_TRIGGER", "SCALPER_M_TREND"):
            assert key in text
            assert any(line.strip().startswith(f"# {key}=") for line in text.splitlines())
            assert not any(line.strip().startswith(f"{key}=") for line in text.splitlines())
        s = Settings(_env_file="env.example")
        assert (s.scalper_m_trigger, s.scalper_m_trend) == ("breakout", "regime")


# --------------------------------------------------------------------------
# Harness entegrasyonu (fixed_roi): M sinyali → işlem, strategy == "M"
# --------------------------------------------------------------------------

_I15 = 15 * 60 * 1000


def test_harness_fixed_roi_trade_is_labelled_m(monkeypatch):
    _modes(monkeypatch, trend="context")
    c5 = [_mk_candle(i, 100.0, 101.0, 99.0, 100.0, 10.0) for i in range(170)]
    c5.append(_mk_candle(170, 100.0, 103.5, 100.5, 103.0, 30.0))   # kırılım mumu
    price = 103.0
    for i in range(171, 200):                                       # devam: yukarı
        c5.append(_mk_candle(i, price, price + 1.2, price - 0.1, price + 1.0, 10.0))
        price += 1.0
    c15 = []
    for j in range(60):
        o = 100.0 + j
        c15.append(Candle(open_time=j * _I15, open=o, high=o + 1.1, low=o - 0.1,
                          close=o + 1.0, volume=10.0, close_time=j * _I15 + _I15 - 1))
    cfg = _sim_cfg(scalper_stop_mode="fixed_roi", scalper_fixed_stop_roi_pct=50.0)
    trades = simulate_symbol("TESTUSDT", c5, c15, [], [StrategyM()], cfg)
    assert trades, "M sinyali harness'te işlem üretmedi"
    assert all(t.strategy == "M" for t in trades)
    assert trades[0].direction == "LONG"
