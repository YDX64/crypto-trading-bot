"""A3 — backtest: funding, likidasyon ve çöküş yüzdeleri. Sentetik mumlar,
elle doğrulanabilir aritmetik; komisyon ve kayma 0."""

from __future__ import annotations

import pytest

from src.core.config import settings
from src.strategies.scalper.backtest import (
    REALISM_FEATURES,
    BacktestTrade,
    OpenPosition,
    _build_arg_parser,
    _process_candle_exits,
    build_risk_realism,
    compute_stats,
    liquidation_price,
    maintenance_margin_rate,
    manage_position,
    open_position,
    realism_report,
    resolve_disabled_realism,
)
from src.strategies.scalper.funding import (
    FUNDING_INTERVAL_MS,
    FundingSchedule,
    settlements_between,
)
from src.strategies.scalper.types import Candle, Direction, ScalpSignal

# .env'deki REAPER/STALE_TP yaş kesmeleri testi etkilemesin (8 saatlik tutuşlar).
CFG = settings.model_copy(update={
    "scalper_max_hold_hours": 0.0, "scalper_stale_tp_hours": 0.0,
    "scalper_structure_exit": "off",
})

T0 = FUNDING_INTERVAL_MS          # 08:00 UTC, 1970-01-01 (epoch'a hizalı)
BAR = 300_000                      # 5 dk


def _c(open_time, o, h=None, l=None, c=None) -> Candle:
    h = max(o, c if c is not None else o) if h is None else h
    l = min(o, c if c is not None else o) if l is None else l
    c = o if c is None else c
    return Candle(open_time=open_time, open=o, high=h, low=l, close=c,
                  volume=1.0, close_time=open_time + BAR - 1)


def _flat(start, n, price=100.0):
    return [_c(start + i * BAR, price) for i in range(n)]


def _pos(direction=Direction.LONG, schedule=None, liq_price=None, stop=50.0,
         entry_time=T0 - BAR, qty=2.0, leverage=10):
    long_ = direction == Direction.LONG
    return OpenPosition(
        strategy="C", symbol="BTCUSDT", direction=direction,
        stop_price=stop, entry_idx=0, entry_price=100.0, entry_time=entry_time,
        qty_total=qty, leverage=leverage,
        tp1_price=1000.0 if long_ else 1.0, tp2_price=2000.0 if long_ else 0.5,
        tp1_qty=qty * 0.4, tp2_qty=qty * 0.2, runner_qty=qty * 0.4,
        breakeven_price=100.5 if long_ else 99.5,
        current_stop=stop, regime="UP",
        entry_commission_rate=0.0, exit_commission_rate=0.0,
        funding_schedule=schedule, liq_price=liq_price,
    )


def _sched(rows=(), fallback=0.0001):
    return FundingSchedule("BTCUSDT", rows, fallback_rate=fallback)


# ---- funding settlement sayımı ----------------------------------------------

def test_settlements_between_is_open_closed_interval():
    assert settlements_between(T0 - 1, T0) == [T0]
    assert settlements_between(T0, T0 + 1) == []          # T == start dahil DEĞİL
    assert settlements_between(T0 - BAR, T0 + 2 * FUNDING_INTERVAL_MS) == [
        T0, T0 + FUNDING_INTERVAL_MS, T0 + 2 * FUNDING_INTERVAL_MS]
    assert settlements_between(5, 5) == []


# ---- funding işareti ve tutarı ------------------------------------------------

def test_funding_long_pays_positive_rate_one_settlement():
    candles = [_c(T0 - BAR, 100.0), _c(T0, 100.0), _c(T0 + BAR, 101.0, c=101.0)]
    pos = _pos(Direction.LONG, _sched())
    trade = manage_position(pos, candles, CFG)
    # brüt (101-100)*2 = 2.0 ; funding 2 * 100 * 0.0001 = 0.02 öder
    assert trade.funding == pytest.approx(0.02)
    assert trade.funding_settlements == 1
    assert trade.pnl == pytest.approx(2.0 - 0.02)


def test_funding_short_receives_positive_rate():
    candles = [_c(T0 - BAR, 100.0), _c(T0, 100.0), _c(T0 + BAR, 99.0, c=99.0)]
    pos = _pos(Direction.SHORT, _sched(), stop=150.0)
    trade = manage_position(pos, candles, CFG)
    assert trade.funding == pytest.approx(-0.02)
    assert trade.pnl == pytest.approx(2.0 + 0.02)


def test_funding_negative_rate_long_receives_short_pays():
    rows = [(T0, -0.0003)]
    candles = [_c(T0 - BAR, 100.0), _c(T0, 100.0), _c(T0 + BAR, 100.0)]
    lt = manage_position(_pos(Direction.LONG, _sched(rows)), candles, CFG)
    st = manage_position(_pos(Direction.SHORT, _sched(rows), stop=150.0), candles, CFG)
    assert lt.funding == pytest.approx(-2 * 100.0 * 0.0003)
    assert st.funding == pytest.approx(+2 * 100.0 * 0.0003)
    assert lt.pnl == pytest.approx(0.06) and st.pnl == pytest.approx(-0.06)


def test_funding_two_settlements_uses_price_at_each():
    rows = [(T0, 0.0001), (T0 + FUNDING_INTERVAL_MS, -0.0002)]
    bars = (FUNDING_INTERVAL_MS // BAR) + 3
    candles = [_c(T0 - BAR, 100.0)]
    for i in range(bars):
        t = T0 + i * BAR
        price = 110.0 if t >= T0 + FUNDING_INTERVAL_MS else 100.0
        candles.append(_c(t, price))
    trade = manage_position(_pos(Direction.LONG, _sched(rows)), candles, CFG)
    # T0: 2*100*0.0001 = +0.02 ; T1: 2*110*(-0.0002) = -0.044
    assert trade.funding_settlements == 2
    assert trade.funding == pytest.approx(0.02 - 0.044)
    assert trade.funding_historical == 2 and trade.funding_fallback == 0


def test_funding_partial_close_before_settlement_reduces_quantity():
    # TP1 (qty 0.8 of 2.0) dolar settlement'tan ÖNCE → T0'da 1.2 açık.
    pos = _pos(Direction.LONG, _sched())
    pos.tp1_price = 101.0
    candles = [
        _c(T0 - 2 * BAR, 100.0),
        _c(T0 - BAR, 100.8, h=102.0, l=100.6, c=101.0),   # TP1 burada (entry_idx=1)
        _c(T0, 101.0),
        _c(T0 + BAR, 101.0),
    ]
    pos.entry_idx = 1
    pos.entry_time = candles[1].open_time
    trade = manage_position(pos, candles, CFG)
    assert pos.legs[0]["label"] == "TP1"
    assert trade.funding == pytest.approx(1.2 * 101.0 * 0.0001)


def test_funding_not_charged_when_no_settlement_inside_holding_time():
    candles = [_c(T0 + BAR, 100.0), _c(T0 + 2 * BAR, 100.0), _c(T0 + 3 * BAR, 100.0)]
    pos = _pos(Direction.LONG, _sched(), entry_time=T0 + BAR)
    trade = manage_position(pos, candles, CFG)
    assert trade.funding == 0.0 and trade.funding_settlements == 0
    assert trade.pnl == pytest.approx(0.0)


def test_funding_settlement_at_entry_time_not_charged():
    candles = [_c(T0, 100.0), _c(T0 + BAR, 100.0)]
    trade = manage_position(_pos(entry_time=T0, schedule=_sched()), candles, CFG)
    assert trade.funding_settlements == 0


def test_funding_historical_vs_fallback_rate_and_counts():
    # T0 tarihsel (ms kayık), T1 yedek (0.0005)
    rows = [(T0 + 5, 0.0002)]
    n = (FUNDING_INTERVAL_MS // BAR) + 3
    candles = [_c(T0 - BAR, 100.0)] + [_c(T0 + i * BAR, 100.0) for i in range(n)]
    trade = manage_position(
        _pos(Direction.LONG, _sched(rows, fallback=0.0005)), candles, CFG)
    assert trade.funding == pytest.approx(2 * 100 * 0.0002 + 2 * 100 * 0.0005)
    assert (trade.funding_historical, trade.funding_fallback) == (1, 1)


def test_funding_price_is_last_close_when_no_bar_starts_at_settlement():
    # T0'da başlayan mum yok (veri boşluğu): son kapanış (95) kullanılır.
    candles = [_c(T0 - BAR, 95.0), _c(T0 + BAR, 100.0)]
    trade = manage_position(_pos(Direction.LONG, _sched()), candles, CFG)
    assert trade.funding == pytest.approx(2.0 * 95.0 * 0.0001)


def test_equity_marks_include_funding_and_last_mark_equals_pnl():
    candles = [_c(T0 - BAR, 100.0), _c(T0, 100.0), _c(T0 + BAR, 101.0, c=101.0)]
    trade = manage_position(_pos(Direction.LONG, _sched()), candles, CFG)
    assert trade.equity_marks[-1][1] == pytest.approx(trade.pnl)


def test_funding_off_by_default_for_direct_position():
    candles = [_c(T0 - BAR, 100.0), _c(T0, 100.0), _c(T0 + BAR, 101.0, c=101.0)]
    trade = manage_position(_pos(Direction.LONG, None), candles, CFG)
    assert trade.funding == 0.0 and trade.pnl == pytest.approx(2.0)


# ---- liquidation --------------------------------------------------------------

def test_liquidation_price_formula_and_mmr_table():
    assert maintenance_margin_rate("BTCUSDT") == 0.004
    assert maintenance_margin_rate("ETHUSDT") == 0.005
    assert maintenance_margin_rate("DOGEUSDT") == 0.0065
    assert liquidation_price(100.0, 10, Direction.LONG, 0.004) == pytest.approx(
        100.0 * (1 - 0.1 + 0.004))
    assert liquidation_price(100.0, 10, Direction.SHORT, 0.004) == pytest.approx(
        100.0 * (1 + 0.1 - 0.004))


def test_liquidation_on_gap_bar_long_loses_margin_of_remaining():
    # lev 10, liq = 90.4. Mum 80'de AÇILIR; stop 85 de aşıldı ama likidasyon önce.
    pos = _pos(Direction.LONG, liq_price=90.4, stop=85.0)
    c = _c(T0, 80.0, h=81.0, l=78.0, c=79.0)
    assert _process_candle_exits(pos, c, Direction.LONG)
    assert pos.exit_reason == "LIQUIDATION"
    assert pos.exit_price == 80.0
    # marj = 2.0 * 100 / 10 = 20
    assert pos.legs[0]["pnl"] == pytest.approx(-20.0)
    assert pos.remaining_qty == 0.0


def test_liquidation_gap_bar_short():
    pos = _pos(Direction.SHORT, liq_price=109.6, stop=115.0)
    c = _c(T0, 120.0, h=121.0, l=119.0, c=120.0)
    assert _process_candle_exits(pos, c, Direction.SHORT)
    assert pos.exit_reason == "LIQUIDATION" and pos.exit_price == 120.0
    assert pos.legs[0]["pnl"] == pytest.approx(-20.0)


def test_stop_inside_liquidation_never_liquidates_even_on_deep_wick():
    # stop 95 > liq 90.4: fiyat önce stopa değer; mum 80'e kadar inse de SL.
    pos = _pos(Direction.LONG, liq_price=90.4, stop=95.0)
    c = _c(T0, 99.0, h=100.0, l=80.0, c=85.0)
    assert _process_candle_exits(pos, c, Direction.LONG)
    assert pos.exit_reason == "SL"
    assert pos.exit_price == 95.0


def test_stop_beyond_liquidation_liquidates_first():
    # stop 85 < liq 90.4: fiyat önce likidasyona değer.
    pos = _pos(Direction.LONG, liq_price=90.4, stop=85.0)
    c = _c(T0, 99.0, h=100.0, l=84.0, c=90.0)
    assert _process_candle_exits(pos, c, Direction.LONG)
    assert pos.exit_reason == "LIQUIDATION"
    assert pos.exit_price == pytest.approx(90.4)
    assert pos.legs[0]["pnl"] == pytest.approx(-20.0)


def test_stop_beyond_liquidation_not_hit_by_shallow_wick():
    pos = _pos(Direction.LONG, liq_price=90.4, stop=85.0)
    c = _c(T0, 99.0, h=100.0, l=91.0, c=95.0)
    assert not _process_candle_exits(pos, c, Direction.LONG)


def test_liquidation_after_partial_close_loses_margin_of_remaining_only():
    pos = _pos(Direction.LONG, liq_price=90.4, stop=85.0)
    pos.tp1_price = 101.0
    assert not _process_candle_exits(
        pos, _c(T0, 100.8, h=102.0, l=100.6, c=101.0), Direction.LONG)
    # TP1 sonrası stop BE'ye taşındı (stop içeride) → artık stop-liq yarışı yok;
    # ancak açılış likidasyonun ötesindeyse (boşluk) kalan tamamı marj kaybıdır.
    assert pos.remaining_qty == pytest.approx(1.2)
    assert _process_candle_exits(
        pos, _c(T0 + BAR, 80.0, h=81.0, l=79.0, c=80.0), Direction.LONG)
    assert pos.exit_reason == "LIQUIDATION"
    assert pos.legs[-1]["pnl"] == pytest.approx(-(1.2 * 100.0 / 10))


def test_liquidation_trade_flagged_and_counted_in_stats():
    candles = [_c(T0 - BAR, 100.0), _c(T0, 80.0, h=81.0, l=78.0, c=79.0)]
    pos = _pos(Direction.LONG, liq_price=90.4, stop=85.0)
    pos.stop_beyond_liq = True
    trade = manage_position(pos, candles, CFG)
    assert trade.exit_reason == "LIQUIDATION"
    assert trade.pnl == pytest.approx(-20.0)
    stats = compute_stats([trade])
    assert stats["liquidations"] == 1
    assert stats["stop_beyond_liquidation"] == 1


# ---- open_position: liq fiyatı + stop-ötesi bayrağı ------------------------------

def _cfg(**extra):
    updates = dict(
        scalper_entry_mode="taker", scalper_min_rr=0.0, scalper_leverage=10,
        scalper_slippage_rate=0.0, scalper_min_stop_pct=0.1,
        scalper_max_stop_pct=20.0, scalper_regime_filter=False,
        scalper_market_gate=False, scalper_loss_cooldown_minutes=0,
        scalper_max_hold_hours=0.0,
    )
    updates.update(extra)
    return settings.model_copy(update=updates)


def _sig(direction, stop, symbol="BTCUSDT"):
    from src.strategies.scalper.types import Regime
    return ScalpSignal(
        strategy="X", symbol=symbol, direction=direction, entry_price=100.0,
        stop_price=stop, reason="t", regime=Regime.UP, atr_5m=1.0,
        risk_multiplier=1.0,
    )


_BARS = [_c(T0 - BAR, 100.0), _c(T0, 100.0), _c(T0 + BAR, 100.0)]


def test_open_position_sets_liq_price_and_stop_beyond_flag():
    # BTC mmr 0.004, lev 10: liq = 100*(1-0.1+0.004) = 90.4
    near = open_position(_sig(Direction.LONG, 99.0), _BARS, 0, _cfg())
    assert near.liq_price == pytest.approx(90.4)
    assert near.stop_beyond_liq is False
    far = open_position(_sig(Direction.LONG, 88.0), _BARS, 0, _cfg())
    assert far.stop_beyond_liq is True
    sh = open_position(_sig(Direction.SHORT, 112.0), _BARS, 0, _cfg())
    assert sh.liq_price == pytest.approx(109.6) and sh.stop_beyond_liq is True
    assert sh.funding_schedule is not None


def test_open_position_default_mmr_for_other_symbols():
    pos = open_position(_sig(Direction.LONG, 99.0, "DOGEUSDT"), _BARS, 0, _cfg())
    assert pos.liq_price == pytest.approx(100.0 * (1 - 0.1 + 0.0065))


def test_open_position_features_off_gives_legacy_position():
    rr = build_risk_realism(["funding", "liquidation"])
    pos = open_position(_sig(Direction.LONG, 88.0), _BARS, 0, _cfg(), risk_realism=rr)
    assert pos.liq_price is None and pos.funding_schedule is None
    assert pos.stop_beyond_liq is False


def test_legacy_equivalence_same_trade_as_before_when_off():
    # Settlement içeren ve derin düşen bir yol: kapalıyken pnl eski modelle aynı.
    candles = [_c(T0 - BAR, 100.0), _c(T0, 100.0),
               _c(T0 + BAR, 99.0, h=100.0, l=85.0, c=90.0)]
    off = build_risk_realism(["funding", "liquidation"])
    p = open_position(_sig(Direction.LONG, 88.0), candles, 0, _cfg(), risk_realism=off)
    t = manage_position(p, candles, _cfg())
    assert t.funding == 0.0 and t.exit_reason == "SL"
    legacy_pos = _pos(Direction.LONG, None, stop=88.0)
    lt = manage_position(legacy_pos, candles, CFG)
    assert lt.pnl == pytest.approx((88.0 - 100.0) * 2.0)
    assert lt.funding == 0.0 and lt.funding_settlements == 0


def test_build_risk_realism_defaults_and_switches():
    rr = build_risk_realism()
    assert rr.funding and rr.liquidation and rr.funding_fallback_rate == pytest.approx(0.0001)
    rr2 = build_risk_realism(["funding"], 0.02)
    assert not rr2.funding and rr2.liquidation
    assert build_risk_realism(None, 0.02).funding_fallback_rate == pytest.approx(0.0002)
    rr3 = build_risk_realism(["liquidation"])
    assert rr3.funding and not rr3.liquidation
    assert rr.schedule_for("BTCUSDT") is rr.schedule_for("BTCUSDT")


def test_features_registered_and_cli_flags():
    assert "funding" in REALISM_FEATURES and "liquidation" in REALISM_FEATURES
    args = _build_arg_parser().parse_args(
        ["--funding-fallback-pct", "0.03", "--funding-cache-dir", "x/y"])
    assert args.funding_fallback_pct == 0.03 and args.funding_cache_dir == "x/y"
    legacy = resolve_disabled_realism(_build_arg_parser().parse_args(["--legacy-model"]))
    assert {"funding", "liquidation"} <= set(legacy)
    rep = realism_report(settings, ["funding"], None, None, 0.02)
    assert "funding" in rep["disabled"] and "liquidation" in rep["enabled"]
    assert rep["parameters"]["funding_fallback_pct"] == 0.02


# ---- drawdown yüzdeleri --------------------------------------------------------

def _tr(pnl, exit_time, marks=None, adverse=None):
    return BacktestTrade(
        strategy="C", symbol="BTCUSDT", direction="LONG", entry_price=100.0,
        entry_time=exit_time - 1, exit_price=100.0, exit_time=exit_time,
        quantity=1.0, leverage=10, margin_usdt=10.0, pnl=pnl, roi_pct=0.0,
        exit_reason="TRAIL", mae_pct=0.0, mfe_pct=0.0, duration_minutes=1.0,
        exit_idx=0, equity_marks=list(marks or []),
        equity_marks_adverse=list(adverse or []),
    )


def test_drawdown_percent_of_initial_and_of_peak():
    # kümülatif: +200 (pik) → +100 → +300 (yeni pik) → +150
    trades = [_tr(200.0, 1), _tr(-100.0, 2), _tr(200.0, 3), _tr(-150.0, 4)]
    s = compute_stats(trades, initial_balance=1000.0)
    assert s["max_drawdown"] == pytest.approx(150.0)
    assert s["max_drawdown_pct_initial"] == pytest.approx(15.0)       # 150/1000
    # pik özkaynak: 1000+300=1300 → 150/1300 ; ilk çukur 100/1200 = 8.33 < 11.54
    assert s["max_drawdown_pct_peak"] == pytest.approx(150.0 / 1300.0 * 100.0)


def test_drawdown_percent_default_balance_is_virtual_10000():
    s = compute_stats([_tr(-500.0, 1)])
    assert s["max_drawdown_pct_initial"] == pytest.approx(5.0)
    assert s["max_drawdown_pct_peak"] == pytest.approx(5.0)   # pik = başlangıç 10000


def test_bar_drawdown_adverse_marks_worse_than_close_marks():
    # Kapanışta ufak çukur, bar-içi aşırı uçta derin çukur.
    t = _tr(
        10.0, 300,
        marks=[(100, 0.0), (200, -5.0), (300, 10.0)],
        adverse=[(100, -20.0), (200, -40.0), (300, 10.0)],
    )
    s = compute_stats([t], initial_balance=1000.0)
    assert s["bar_max_drawdown"] == pytest.approx(5.0)
    assert s["bar_max_drawdown_adverse"] == pytest.approx(40.0)
    assert s["bar_max_drawdown_adverse_at"] == 200
    assert s["bar_max_drawdown_adverse_pct_initial"] == pytest.approx(4.0)
    assert s["bar_max_drawdown_pct_initial"] == pytest.approx(0.5)
    assert s["bar_max_drawdown_adverse_pct_peak"] == pytest.approx(4.0)


def test_adverse_marks_written_by_manage_position_long_and_short():
    candles = [
        _c(T0 - BAR, 100.0),
        _c(T0, 100.0, h=101.0, l=96.0, c=99.0),
        _c(T0 + BAR, 99.0, h=100.0, l=98.0, c=99.0),
    ]
    tl = manage_position(_pos(Direction.LONG, None), candles, CFG)
    # qty 2: bar1 adverse = (96-100)*2 = -8 ; kapanış = (99-100)*2 = -2
    assert tl.equity_marks[1][1] == pytest.approx(-2.0)
    assert tl.equity_marks_adverse[1][1] == pytest.approx(-8.0)
    shorts = [
        _c(T0 - BAR, 100.0),
        _c(T0, 100.0, h=104.0, l=99.0, c=101.0),
        _c(T0 + BAR, 101.0, h=101.0, l=100.0, c=100.0),
    ]
    ts = manage_position(_pos(Direction.SHORT, None, stop=150.0), shorts, CFG)
    assert ts.equity_marks_adverse[1][1] == pytest.approx(-8.0)   # (100-104)*2
    assert ts.equity_marks[1][1] == pytest.approx(-2.0)


def test_empty_stats_carry_new_keys():
    s = compute_stats([])
    for key in (
        "max_drawdown_pct_initial", "max_drawdown_pct_peak",
        "bar_max_drawdown_pct_initial", "bar_max_drawdown_pct_peak",
        "bar_max_drawdown_adverse", "bar_max_drawdown_adverse_at",
        "bar_max_drawdown_adverse_pct_initial", "bar_max_drawdown_adverse_pct_peak",
        "total_funding", "funding_settlements", "liquidations",
        "stop_beyond_liquidation",
    ):
        assert key in s


# ---- simulate_symbol uçtan uca (kwarg plumbing) --------------------------------

class _Once:
    name = "X"

    def __init__(self):
        self.fired = False

    def evaluate(self, ctx):
        if self.fired:
            return None
        self.fired = True
        return _sig(Direction.LONG, 99.0)


def _sim(disabled=(), pct=None, cache_dir=None):
    from src.strategies.scalper.backtest import simulate_symbol
    candles = [_c(T0 - 2 * BAR + i * BAR, 100.0) for i in range(8)]
    return simulate_symbol(
        "BTCUSDT", candles, [], [], [_Once()], _cfg(),
        disabled_realism=disabled, funding_fallback_pct=pct,
        funding_cache_dir=cache_dir,
    )


def test_simulate_symbol_charges_fallback_funding_and_respects_switch():
    (t,) = _sim(pct=0.02)
    assert t.funding_settlements == 1 and t.funding_fallback == 1
    assert t.funding == pytest.approx(t.quantity * 100.0 * 0.0002)
    (t_off,) = _sim(disabled=("funding",), pct=0.02)
    assert t_off.funding == 0.0 and t_off.funding_settlements == 0
    assert t_off.pnl - t.pnl == pytest.approx(t.funding)


def test_simulate_symbol_reads_cache_file_and_report_counts(tmp_path):
    import json
    from src.strategies.scalper.backtest import funding_report
    (tmp_path / "BTCUSDT.json").write_text(
        json.dumps([{"funding_time": T0 + 3, "rate": -0.0004}]))
    (t,) = _sim(cache_dir=tmp_path)
    assert t.funding_historical == 1 and t.funding_fallback == 0
    assert t.funding == pytest.approx(-t.quantity * 100.0 * 0.0004)
    rep = funding_report([t])
    assert rep["per_symbol"]["BTCUSDT"]["historical"] == 1
    assert rep["per_symbol"]["BTCUSDT"]["fallback"] == 0
    assert rep["settlements"] == 1


def test_json_report_has_funding_and_liquidation_blocks(tmp_path):
    import json
    from src.strategies.scalper.backtest import write_json_report
    (t,) = _sim(pct=0.02)
    path = write_json_report([t], 1, ["BTCUSDT"], "X", cfg=_cfg(), output_dir=tmp_path)
    rep = json.loads(open(path, encoding="utf-8").read())
    assert rep["funding"]["per_symbol"]["BTCUSDT"]["fallback"] == 1
    assert rep["liquidation"] == {
        "liquidations": 0, "stop_beyond_liquidation": 0, "margin_mode": "ISOLATED"}
    assert rep["overall"]["total_funding"] == pytest.approx(t.funding)
    assert "equity_marks_adverse" not in rep["trades"][0]
    assert rep["trades"][0]["funding"] == pytest.approx(t.funding)
    assert "funding" in rep["provenance"]["realism"]["enabled"]


def test_adverse_drawdown_peak_comes_from_close_marks_not_adverse_series():
    # Denetim bulgusu: tepe adverse serisinden alınınca fitilli işlemde tepe de
    # düşüyor ve adverse çöküş kapanış-bazlıdan KÜÇÜK/SIFIR çıkıyordu.
    t = _tr(
        -10.0, 2000,
        marks=[(1000, 100.0), (2000, 90.0)],
        adverse=[(1000, 20.0), (2000, 85.0)],
    )
    s = compute_stats([t], initial_balance=1000.0)
    assert s["bar_max_drawdown"] == pytest.approx(10.0)
    # tepe = kapanış 100 (t=1000), çukur = adverse 20 (t=1000) → 80
    assert s["bar_max_drawdown_adverse"] == pytest.approx(80.0)
    assert s["bar_max_drawdown_adverse_at"] == 1000
    assert s["bar_max_drawdown_adverse_pct_initial"] == pytest.approx(8.0)
    assert s["bar_max_drawdown_adverse_pct_peak"] == pytest.approx(80.0 / 1100.0 * 100.0)
    assert s["bar_max_drawdown_adverse"] >= s["bar_max_drawdown"]


def test_adverse_drawdown_never_shallower_than_close_drawdown_multi_trade():
    a = _tr(5.0, 300, marks=[(100, 10.0), (200, 4.0), (300, 5.0)],
            adverse=[(100, 6.0), (200, 1.0), (300, 5.0)])
    b = _tr(-3.0, 400, marks=[(200, 0.0), (300, -1.0), (400, -3.0)],
            adverse=[(200, -2.0), (300, -2.5), (400, -3.0)])
    s = compute_stats([a, b], initial_balance=1000.0)
    assert s["bar_max_drawdown_adverse"] >= s["bar_max_drawdown"] - 1e-12
    assert s["bar_max_drawdown_adverse_pct_initial"] >= s["bar_max_drawdown_pct_initial"] - 1e-12
