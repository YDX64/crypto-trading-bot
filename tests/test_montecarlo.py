"""montecarlo.py: gun-blok bootstrap, esdeger-yol, GA ve eslestirilmis A/B testleri."""
import math
import random
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from src.strategies.scalper import montecarlo as mc

D0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def T(day_offset, pnl, hour=12):
    return (D0.replace(hour=hour) + timedelta(days=day_offset), pnl)


def test_group_by_day_keeps_empty_days_and_order():
    days, pnls = mc.group_by_day([T(2, 5.0, 15), T(0, 1.0), T(2, -3.0, 9)])
    assert days == [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)]
    assert pnls == [[1.0], [], [-3.0, 5.0]]  # gun ici cikis zamanina gore


def test_determinism_and_seed_sensitivity():
    trades = [T(i, (-1) ** i * (i + 1.0)) for i in range(12)]
    a = mc.equity_path_monte_carlo(trades, 1000, n_paths=200, seed=7, daily_loss_limit_pct=1)
    b = mc.equity_path_monte_carlo(trades, 1000, n_paths=200, seed=7, daily_loss_limit_pct=1)
    c = mc.equity_path_monte_carlo(trades, 1000, n_paths=200, seed=8, daily_loss_limit_pct=1)
    assert a == b
    assert a["final_pnl"] != c["final_pnl"]
    assert mc.bootstrap_confidence_intervals(trades, 100, seed=3) == \
        mc.bootstrap_confidence_intervals(trades, 100, seed=3)


def test_tiny_case_support_enumerated_by_hand():
    # 2 gun: A=+10, B=-5; blok 1 -> AA, AB, BA, BB esit olasilik
    trades = [T(0, 10.0), T(1, -5.0)]
    r = mc.equity_path_monte_carlo(trades, 1000, block_days=1, n_paths=2000, seed=1)
    # destek {20, 5, -10}; p5 ve p95 uc degerlere oturur (olasilik 1/4 > %5)
    assert r["final_pnl"]["p5"] == -10.0
    assert r["final_pnl"]["p95"] == 20.0
    assert r["final_pnl"]["p50"] == 5.0
    assert r["final_pnl"]["mean"] == pytest.approx(5.0, abs=0.6)  # (20+5+5-10)/4
    # blok 2 (dairesel, N=2): baslangic 0 -> AB, 1 -> BA; hep 5
    r2 = mc.equity_path_monte_carlo(trades, 1000, block_days=2, n_paths=300, seed=1)
    assert r2["final_pnl"]["p5"] == r2["final_pnl"]["p95"] == 5.0


def test_path_drawdown_streak_underwater_breach_known_path():
    day_pnls = [[50.0, -30.0, -40.0], [10.0]]
    r = mc.simulate_path(day_pnls, 1000, "additive", ruin_level=500, daily_loss_limit_pct=1)
    # esdeger: 1050 (tepe), 1020, 980 (dd 70), 990
    assert r["final_pnl"] == -10.0
    assert r["max_dd"] == 70.0
    assert r["max_dd_pct"] == 7.0
    assert r["longest_losing_streak"] == 2
    assert r["days_under_water"] == 2  # 980 < 1050 ve 990 < 1050
    assert r["loss_breach_days"] == 1  # 1. gun -20 <= -10; 2. gun +10
    assert r["ruined"] is False


def test_ruin_probability_edges():
    losing = [T(i, -600.0) for i in range(5)]
    assert mc.equity_path_monte_carlo(losing, 1000, n_paths=50)["prob_ruin"] == 1.0
    winning = [T(i, 5.0) for i in range(5)]
    assert mc.equity_path_monte_carlo(winning, 1000, n_paths=50)["prob_ruin"] == 0.0
    # kayip zinciri ruin seviyesinin tam uzerinde kalirsa ruin degil; esitse ruin
    assert mc.simulate_path([[-500.0]], 1000, ruin_level=500)["ruined"] is True
    assert mc.simulate_path([[-499.0]], 1000, ruin_level=500)["ruined"] is False


def test_compounding_vs_additive_known_path():
    day_pnls = [[100.0], [100.0]]
    add = mc.simulate_path(day_pnls, 1000, "additive")
    comp = mc.simulate_path(day_pnls, 1000, "compounding")
    assert add["final_pnl"] == 200.0
    assert comp["final_pnl"] == pytest.approx(210.0)  # 100 + 100 * 1.1
    loss = mc.simulate_path([[-100.0], [-100.0]], 1000, "compounding")
    assert loss["final_pnl"] == pytest.approx(-190.0)  # -100 + -100 * 0.9
    assert loss["max_dd"] == pytest.approx(190.0)
    with pytest.raises(ValueError):
        mc.simulate_path(day_pnls, 1000, "other")


def test_daily_loss_breach_probability_and_expected_days():
    # her gun -20 (limit %1 = 10): her yolda her gun asim
    trades = [T(i, -20.0) for i in range(4)]
    r = mc.equity_path_monte_carlo(trades, 1000, n_paths=40, daily_loss_limit_pct=1)
    assert r["prob_daily_loss_breach"] == 1.0
    assert r["expected_breach_days"] == 4.0


def test_infinite_profit_factor_kept_in_ci():
    trades = [T(i, 1.0) for i in range(10)] + [T(10, -1.0)]
    r = mc.bootstrap_confidence_intervals(trades, n_paths=400, block_days=1, seed=5)
    assert r["observed"]["profit_factor"] == pytest.approx(10.0)
    # kayip gununu icermeyen yeniornekler (~%35) PF=+inf; atilmaz
    assert math.isinf(r["ci"]["profit_factor"]["hi"])
    assert r["ci"]["profit_factor"]["lo"] > 0
    allwin = mc.bootstrap_confidence_intervals([T(i, 2.0) for i in range(5)], n_paths=50)
    assert math.isinf(allwin["ci"]["profit_factor"]["lo"])
    assert math.isinf(allwin["ci"]["avg_win_loss"]["hi"])


def test_ci_point_statistics_exact():
    s = mc.trade_statistics([T(0, 10.0), T(0, -4.0), T(1, 6.0), T(2, -2.0)])
    assert s["net_pnl"] == 10.0
    assert s["profit_factor"] == pytest.approx(16.0 / 6.0)
    assert s["win_rate"] == 0.5
    assert s["expectancy"] == 2.5
    assert s["avg_win_loss"] == pytest.approx(8.0 / 3.0)  # 8 / 3


def test_insufficient_sample_flag():
    few = [T(i, 1.0) for i in range(5)]
    assert mc.equity_path_monte_carlo(few, 1000, n_paths=10)["insufficient_sample"] is True
    # 30 islem ama 3 gun -> yine yetersiz
    packed = [T(i % 3, 1.0, hour=i % 20) for i in range(30)]
    assert mc.equity_path_monte_carlo(packed, 1000, n_paths=10)["insufficient_sample"] is True
    ok = [T(i % 10, 1.0, hour=i % 20) for i in range(30)]
    assert mc.equity_path_monte_carlo(ok, 1000, n_paths=10)["insufficient_sample"] is False
    assert mc.bootstrap_confidence_intervals(few, 10)["insufficient_sample"] is True


def _cal(n):
    return [date(2026, 1, 1) + timedelta(days=i) for i in range(n)]


def test_paired_identical_lists():
    trades = [T(i, (-1) ** i * 3.0) for i in range(20)]
    r = mc.paired_ab_test(trades, list(trades), _cal(20), n_paths=500, seed=2)
    assert r["delta"] == 0.0
    assert r["p_value"] == pytest.approx(1.0)
    assert r["std_error"] == 0.0


def test_paired_constant_shift_rejects():
    cal = _cal(30)
    base = [T(i, 0.0) for i in range(30)]
    cand = [T(i, 2.0) for i in range(30)]
    r = mc.paired_ab_test(cand, base, cal, n_paths=999, block_days=3, seed=2)
    assert r["delta"] == 60.0
    assert r["p_value"] == pytest.approx(1 / 1000)  # (0 + 1) / (999 + 1)
    assert r["ci"]["lo"] == r["ci"]["hi"] == 60.0


def test_paired_ignores_days_outside_calendar_and_counts_empty_days():
    cal = _cal(3)  # gun 2 islemsiz -> fark 0
    cand = [T(0, 4.0), T(10, 99.0)]
    base = [T(1, 1.0)]
    r = mc.paired_ab_test(cand, base, cal, n_paths=50, seed=1)
    assert r["delta"] == 3.0  # 4 - 1


@pytest.mark.parametrize("block_days", [1, 3])
def test_paired_p_value_calibrated_under_null(block_days):
    rng = random.Random(2024 + block_days)
    n_days, reps, alpha = 40, 500, 0.10
    cal = _cal(n_days)
    rejected = 0
    for k in range(reps):
        diffs = [rng.gauss(0.0, 1.0) for _ in range(n_days)]
        cand = [T(i, d) for i, d in enumerate(diffs)]
        base = [T(i, 0.0) for i in range(n_days)]
        r = mc.paired_ab_test(cand, base, cal, n_paths=199, block_days=block_days, seed=k)
        rejected += r["p_value"] <= alpha
    rate = rejected / reps
    assert 0.05 <= rate <= 0.16, rate


def test_paired_calendar_order_and_duplicates_do_not_matter():
    cal = _cal(30)
    cand = [T(i, 5.0 if i < 15 else -5.0) for i in range(30)]
    kw = dict(n_paths=500, block_days=5, seed=1)
    ref = mc.paired_ab_test(cand, [], cal, **kw)
    shuffled = list(cal)
    random.Random(9).shuffle(shuffled)
    assert mc.paired_ab_test(cand, [], shuffled, **kw) == ref
    dup = cal + cal[:5]
    r = mc.paired_ab_test(cand, [], dup, **kw)
    assert r["n_days"] == 30
    assert r == ref


def test_group_by_day_mixed_naive_and_aware_treated_as_utc():
    aware = datetime(2026, 1, 1, 23, 0, tzinfo=timezone(timedelta(hours=-3)))  # 02:00 UTC 01-02
    naive = datetime(2026, 1, 2, 1, 0)  # UTC kabul
    days, pnls = mc.group_by_day([(aware, 1.0), (naive, 2.0)])
    assert days == [date(2026, 1, 2)]
    assert pnls == [[2.0, 1.0]]  # naive 01:00 UTC, aware 02:00 UTC


def test_loaders(tmp_path):
    import json
    rep = tmp_path / "bt.json"
    rep.write_text(json.dumps({"trades": [
        {"exit_time": 1769150459999, "pnl": 7.5},
        {"exit_time": 1769140000000, "pnl": -2.0}]}))
    t = mc.load_backtest_report(str(rep))
    assert [p for _, p in t] == [-2.0, 7.5]
    assert t[0][0].tzinfo is not None and t[0][0] < t[1][0]

    db = tmp_path / "l.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE scalp_trades (id INTEGER PRIMARY KEY, closed_at TEXT, "
                "realized_pnl REAL, status TEXT)")
    con.executemany("INSERT INTO scalp_trades (closed_at, realized_pnl, status) VALUES (?,?,?)", [
        ("2026-08-07 12:56:12.087220", 5.0, "CLOSED"),
        ("2026-08-07 10:00:00.000000", -1.0, "CLOSED"),
        ("2026-08-07 11:00:00.000000", 9.0, "OPEN"),
        (None, 3.0, "CLOSED")])
    con.commit()
    con.close()
    t = mc.load_ledger(str(db))
    assert [p for _, p in t] == [-1.0, 5.0]
    assert t[0][0] == datetime(2026, 8, 7, 10, 0, tzinfo=timezone.utc)
