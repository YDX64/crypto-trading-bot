"""E23 on-kayitli analizci testleri (scripts/e23/analyze_e23.py). AGSIZ ve SENTETIK: gercek rapor okunmaz.

Bootstrap B kucultulur (THRESH monkeypatch); tum dosya < 20 sn hedefler.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import random
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Optional, Sequence, Tuple

import pytest

REPO_REAL = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_REAL / "scripts" / "e23"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import analyze_e23 as A  # noqa: E402
import e23_common as C  # noqa: E402

from src.strategies.scalper.multitest import benjamini_hochberg  # noqa: E402

INF = float("inf")
DAY = 86_400_000
FAST = {"BOOT_N": 199, "BOOT_N_HI": 399}
_ARM_ORDER = ("K30R-L", "K30R-H", "K30B-L", "K30B-H", "G30B-L", "G30B-H")


def _six_arm_variants(v: Dict) -> Dict:
    """Mantik testleri tasarlanan 6 kolluk aile uzerinde calisir. Gercek variants.json CAL test
    edilebilirlik kuralindan sonra kol cikarabilir (PLAN §3.4); cikan -H kollari -L kardeslerinden
    (yalniz esik 3.0) geri kurulur. Gercek dosyanin kendisi DEGISMEZ."""
    v = json.loads(json.dumps(v))
    have = {a["id"] for a in v["family"]}
    for arm_id in _ARM_ORDER:
        if arm_id in have:
            continue
        sib = next(a for a in v["family"] if a["id"] == arm_id[:-1] + "L")
        arm = json.loads(json.dumps(sib))
        arm["id"] = arm_id
        arm["shock_pct"] = 3.0
        arm["env"]["SCALPER_SHOCK_TRIGGER_PCT"] = "3.0"
        v["family"].append(arm)
    v["family"].sort(key=lambda a: _ARM_ORDER.index(a["id"]))
    return v


VARIANTS = _six_arm_variants(C.load_variants(REPO_REAL / C.PLAN_REL / "variants.json"))
ARMS = C.arm_ids(VARIANTS)


def _write_test_variants(pdir: Path) -> None:
    (pdir / "variants.json").write_text(json.dumps(VARIANTS, indent=2) + "\n", encoding="utf-8")


def ms_of(day: str, hour: int = 12) -> int:
    return A.date_ms(day) + hour * 3_600_000


def mk(day: str, r: float, sym: str = "BTCUSDT", direction: str = "LONG", lev: int = 10, reason: str = "TP1",
       exit_day: Optional[str] = None) -> "A.Tr":
    """R = r (marj 100, S 1 -> risk 1)."""
    return A.make_tr(sym, direction, ms_of(day), ms_of(exit_day or day, 13), r, 100.0, 1.0, lev, reason,
                     ms_of(day, 11))


def mkrun(trades: Sequence["A.Tr"], set_name: str = "DEV", arm: str = "K30B-L", missed: Optional[dict] = None,
          dd: float = 1.0, shock: Optional[dict] = None) -> "A.Run":
    s, e = C.set_window(VARIANTS, set_name)
    days = (dt.date.fromisoformat(e) - dt.date.fromisoformat(s)).days
    return A.Run(f"{set_name}_{arm}", set_name, arm, [], 1.0, dt.date.fromisoformat(s), days, list(trades),
                 dict(missed or {}), dd, "", shock)


@pytest.fixture(autouse=True)
def fast_boot(monkeypatch):
    for k, v in FAST.items():
        monkeypatch.setitem(A.THRESH, k, v)


# ==========================================================================
# temel yapi taslari
# ==========================================================================
def test_daily_vector_by_exit_day_zero_fill():
    trs = [mk("2025-01-01", 1.0, exit_day="2025-01-03"), mk("2025-01-02", 2.0, exit_day="2025-01-03"),
           mk("2025-01-05", -0.5)]
    vec = A.daily_vector(trs, dt.date(2025, 1, 1), 365)
    assert len(vec) == 365
    assert vec[2] == 3.0 and vec[4] == -0.5 and sum(1 for v in vec if v != 0) == 2
    with pytest.raises(A.IntegrityError):
        A.daily_vector([mk("2026-01-01", 1.0)], dt.date(2025, 1, 1), 365)


def test_quarter_and_month_by_entry_time_boundary():
    a = A.make_tr("X", "LONG", A.date_ms("2025-04-01") - 1, A.date_ms("2025-04-02"), 1, 100, 1, 5, "TP1")
    b = A.make_tr("X", "LONG", A.date_ms("2025-04-01"), A.date_ms("2025-04-02"), 1, 100, 1, 5, "TP1")
    assert (a.quarter, a.month) == ("2025Q1", "2025-03")
    assert (b.quarter, b.month) == ("2025Q2", "2025-04")
    assert a.exit_day == dt.date(2025, 4, 2)


def test_calendar_windows_dev_val_final():
    fq, pq = A.calendar_quarters(*C.set_window(VARIANTS, "DEV"))
    assert fq == ["2025Q1", "2025Q2", "2025Q3", "2025Q4"] and pq == []
    fq, pq = A.calendar_quarters(*C.set_window(VARIANTS, "VAL"))
    assert len(fq) == 7 and fq[0] == "2023Q1" and fq[-1] == "2024Q3" and pq == ["2024Q4"]
    fm, pm = A.calendar_months(*C.set_window(VARIANTS, "VAL"))
    assert len(fm) == 22 and fm[-1] == "2024-10" and pm == ["2024-11"]
    fq, pq = A.calendar_quarters(*C.set_window(VARIANTS, "FINAL"))
    assert fq == [] and pq == ["2026Q4"]


def test_label_roundtrip_with_dash_in_arm_id():
    for arm in ARMS:
        for ovs in ([], ["ROB_FEE2"], ["ROB_FEE2", "ROB_DLY1"], ["DIAG_NOLATCH"]):
            lab = C.make_label("DEV", arm, ovs)
            assert C.parse_label(lab) == ("DEV", arm, ovs)
    assert C.make_label("DEV", "K30B-L") == "DEV_K30B-L"
    assert C.make_label("DEV", "K30B-L", ["ROB_FEE2"]) == "DEV_K30B-L__ROB_FEE2"
    assert C.parse_label("DEV_K30B-L__ROB_FEE2") == ("DEV", "K30B-L", ["ROB_FEE2"])
    assert A._label_kind("S2_K30B-L_h0") == ("s2", "CAL", "K30B-L", [])
    assert A._label_kind("S2_K30B-L_h1")[0] == "s2"
    assert A._label_kind("DEV_K30B-L__DIAG_Z0") == ("normal", "DEV", "K30B-L", ["DIAG_Z0"])
    assert A._label_kind("S1_AYI_C")[0] == "s1"
    with pytest.raises(A.CheckFail):
        A._label_kind("XYZ_K30B-L")


def test_env_resolution_order_and_triggers():
    env = C.resolve_env(VARIANTS, "K30B-H", ["DIAG_Z0"])
    assert env["SCALPER_SHOCK_TRIGGER_PCT"] == "3.0" and env["SCALPER_M_TREND"] == "context"
    assert env["SCALPER_TF_ENTRY"] == "30m" and env["SCALPER_MARKET_GATE_SYMBOL"] == "BTCUSDT"
    assert env["SCALPER_TAKER_FEE_PCT"] == "0" and C.shock_pct_of(VARIANTS, "K30R-L") == 1.5
    assert [o["id"] for o in VARIANTS["overlays"] if o["role"] == "diag"] == ["DIAG_Z0", "DIAG_NOLATCH"]


def test_shock_labels_from_days_threshold_inclusive():
    days = [{"date": "2025-03-03", "open": 100.0, "close": 103.0},        # tam %3 -> sok (>=)
            {"date": "2025-03-04", "open": 100.0, "close": 102.99},       # alti -> sok degil
            {"date": "2025-03-05", "open": 100.0, "close": 95.0},
            {"date": "2025-03-06", "open": 100.0, "close": 97.0}]         # -%3 tam
    assert A.shock_labels_from_days(days) == {"2025-03-03": "UP", "2025-03-05": "DOWN", "2025-03-06": "DOWN"}
    assert A.shock_labels_from_days(None) == {}
    with pytest.raises(A.IntegrityError):
        A.shock_labels_from_days([{"date": "2025-03-07", "open": 0.0, "close": 1.0}])


def test_shock_mismatch_counting_overlap_only():
    run_labels = {"2025-01-01": "UP", "2025-01-02": "DOWN", "2025-01-05": "UP", "2026-03-01": "UP"}
    e18 = {"2025-01-01": "UP", "2025-01-02": "DOWN", "2025-01-03": "UP"}
    dates = ["2025-01-0%d" % i for i in range(1, 6)] + ["2026-03-01"]        # 2026-03: E18 kapsami disi (CAL)
    ov, mm = A.shock_mismatch(run_labels, e18, VARIANTS, dates)
    assert ov == 5 and mm == 2                                               # 01-03 (E18'de var, kosuda yok) + 01-05
    e18_dir = dict(e18, **{"2025-01-02": "UP"})
    assert A.shock_mismatch(run_labels, e18_dir, VARIANTS, dates) == (5, 3)  # yon farki da uyusmazlik
    assert A.shock_mismatch({}, {}, VARIANTS, ["2026-10-20"]) == (0, 0)
    assert A.e18_overlap_dates(VARIANTS, ["2022-12-31", "2023-01-01", "2024-11-17", "2024-11-18", "2025-12-31",
                                          "2026-01-01"]) == ["2023-01-01", "2024-11-17", "2025-12-31"]


def test_regime_assignment_from_frozen_labels():
    labels = A.load_labels()
    assert labels["2025-02"] == "DOWN" and labels["2025-01"] == "UP"
    assert sum(1 for k, v in labels.items() if v == "DOWN" and k.startswith("2025")) == 3


def test_pf_incl_no_loss_and_empty():
    assert A.pf_of([1.0, -0.5, 1.0]) == pytest.approx(4.0)
    assert A.pf_of([1.0, 2.0]) == INF
    assert A.pf_of([]) == 0.0 and A.pf_of([-1.0]) == 0.0


# ==========================================================================
# bootstrap / BH
# ==========================================================================
def _naive_p(x, block, seed, boot_n):
    d = len(x)
    mean = sum(x) / d
    xc = [v - mean for v in x]
    t_obs = A.t_obs_of(x)
    nb = -(-d // block)
    rnd = random.Random(seed).random
    cnt = 0
    for _ in range(boot_n):
        s: List[float] = []
        for _k in range(nb):
            st = int(rnd() * d)
            s.extend(xc[(st + j) % d] for j in range(block))
        s = s[:d]
        if A._tstat(sum(s), sum(v * v for v in s), d) >= t_obs:
            cnt += 1
    return (1 + cnt) / (boot_n + 1)


def test_bootstrap_deterministic_and_matches_naive():
    rng = random.Random(3)
    x = [rng.gauss(0.05, 1.0) for _ in range(60)]
    seed = A.seed_for("DEV", "K30B-L", 7)
    p1, t1 = A.bootstrap_p(x, 7, seed, 150)
    p2, _ = A.bootstrap_p(x, 7, seed, 150)
    assert p1 == p2 and t1 == pytest.approx(A.t_obs_of(x))
    assert p1 == _naive_p(x, 7, seed, 150)
    assert A.seed_for("DEV", "K30B-L", 7) == int.from_bytes(hashlib.sha256(b"E23|DEV|K30B-L|b7").digest()[:8], "big")
    assert A.seed_for("DEV", "K30B-L", 7, hi=True) == int.from_bytes(
        hashlib.sha256(b"E23|DEV|K30B-L|b7|hi").digest()[:8], "big")


def test_bootstrap_centring_and_signal():
    rng = random.Random(11)
    x = [rng.gauss(0, 1) for _ in range(200)]
    m = sum(x) / len(x)
    x = [v - m for v in x]                           # ortalama tam 0 -> t_obs ~ 0
    p, _ = A.bootstrap_p(x, 7, 12345, 999)
    assert 0.35 < p < 0.65
    y = [v + 0.6 for v in x]
    assert A.bootstrap_p(y, 7, 12345, 999)[0] < 0.01
    assert A.bootstrap_p([0.0] * 50, 7, 1, 99)[0] == 1.0            # sd = 0 -> p = 1


def test_bootstrap_null_size():
    rejects = 0
    n = 300
    for i in range(n):
        rng = random.Random(1000 + i)
        x = [rng.gauss(0, 1) for _ in range(120)]
        p, _ = A.bootstrap_p(x, 7, 5000 + i, 199)
        rejects += p <= 0.05
    assert rejects / n <= 0.05 + 4 * math.sqrt(0.05 * 0.95 / n)


@pytest.mark.parametrize("ps", [[0.01, 0.04, 0.03, 0.5, 0.2, 1.0], [0.5] * 6, [0.001, 0.002, 0.003, 0.9, 1.0, 1.0]])
def test_bh_parity_with_multitest(ps):
    mine = A.bh_qvalues(ps)
    ref = [r["q_value"] for r in benjamini_hochberg(ps)]
    assert [round(v, 6) for v in mine] == [round(v, 6) for v in ref]


def test_refine_p_flips_only_near_threshold():
    called = []
    p, did = A.refine_p(0.05, lambda x: x <= 0.05, lambda: called.append(1) or 0.04, boot_n=19999)
    assert did and p == 0.04 and called
    p, did = A.refine_p(0.5, lambda x: x <= 0.05, lambda: 0.0, boot_n=19999)
    assert not did and p == 0.5


# ==========================================================================
# kapilar: G0..G15, esigin hemen ustu / hemen alti
# ==========================================================================
def _row(rows, name):
    return next(r for r in rows if r["name"] == name)


def _quarter_run(ns: Dict[str, int], set_name="DEV"):
    starts = {"2025Q1": "2025-01-10", "2025Q2": "2025-04-10", "2025Q3": "2025-07-10", "2025Q4": "2025-10-10"}
    trs = []
    for q, n in ns.items():
        trs += [mk(starts[q], 1.0, sym=f"S{i}") for i in range(n)]
    return mkrun(trs, set_name)


def test_g0_thresholds():
    ok = A.basic_metrics(mkrun([mk("2025-02-01", 1.0)] * 100, missed={"stop_band_low": 11}), {})
    bad = A.basic_metrics(mkrun([mk("2025-02-01", 1.0)] * 100, missed={"stop_band_low": 12}), {})
    assert A.rows_pass(A.gate_g0(True, ok)) and not A.rows_pass(A.gate_g0(True, bad))
    assert not A.rows_pass(A.gate_g0(False, ok))                      # check-run yok
    # ham sinyal = N + capacity + daily_latch + maker_missed + min_rr_rejected + sessiz
    m = A.basic_metrics(mkrun([mk("2025-02-01", 1.0)] * 90, missed={"capacity": 100, "qty_zero": 9}), {})
    assert m["silent_ratio"] == pytest.approx(9 / 199) and m["raw_signals"] == 199
    e8 = A.basic_metrics(mkrun([mk("2025-02-01", 1.0, reason="EOD")] * 8), {})
    e9 = A.basic_metrics(mkrun([mk("2025-02-01", 1.0, reason="EOD")] * 9), {})
    assert A.rows_pass(A.gate_g0(True, e8)) and not A.rows_pass(A.gate_g0(True, e9))


def test_g0_trigger_violation_and_nodata():
    on = [[ms_of("2025-02-01", 11), "LONG"]]
    good = mkrun([mk("2025-02-01", 1.0)], shock={"on_points": on, "nodata_points": 0})
    assert A.basic_metrics(good, {})["trigger_violations"] == 0 and A.rows_pass(A.gate_g0(True, A.basic_metrics(good, {})))
    wrong_dir = mkrun([mk("2025-02-01", 1.0, direction="SHORT")], shock={"on_points": on, "nodata_points": 0})
    m = A.basic_metrics(wrong_dir, {})
    assert m["trigger_violations"] == 1 and not A.rows_pass(A.gate_g0(True, m))
    off = mkrun([mk("2025-02-02", 1.0)], shock={"on_points": on, "nodata_points": 0})        # o noktada tetikleyici kapali
    assert A.basic_metrics(off, {})["trigger_violations"] == 1
    nod = mkrun([mk("2025-02-01", 1.0)], shock={"on_points": on, "nodata_points": 1})
    m = A.basic_metrics(nod, {})
    assert m["nodata_points"] == 1 and not A.rows_pass(A.gate_g0(True, m))
    assert _row(A.gate_g0(True, m), "veri-yok karar noktası")["pass"] is False


def test_g1_pool_and_active_quarters():
    def g1(ns):
        return A.gate_g1(A.basic_metrics(_quarter_run(ns), {}))
    ok = g1({"2025Q1": 40, "2025Q2": 30, "2025Q3": 20, "2025Q4": 10})               # N 100, aktif 4
    assert A.rows_pass(ok)
    assert not A.rows_pass(g1({"2025Q1": 40, "2025Q2": 30, "2025Q3": 20, "2025Q4": 9}))      # N 99
    three = g1({"2025Q1": 60, "2025Q2": 60, "2025Q3": 10, "2025Q4": 9})                      # 9 < 10: aktif 3, N 139
    assert A.rows_pass(three) and _row(three, "aktif çeyrek (N>=10)")["value"] == 3
    two = g1({"2025Q1": 60, "2025Q2": 60, "2025Q3": 9, "2025Q4": 0})                         # aktif 2
    assert not A.rows_pass(two) and _row(two, "havuz N")["pass"] and not _row(two, "aktif çeyrek (N>=10)")["pass"]
    assert A.rows_pass(g1({"2025Q1": 100, "2025Q2": 0, "2025Q3": 10, "2025Q4": 10}))         # 3 aktif (Q2 bos)


def test_g2_g3_g8_g14_edges():
    assert A.rows_pass(A.gate_g2({"pf": 1.15})) and not A.rows_pass(A.gate_g2({"pf": 1.1499}))
    assert A.rows_pass(A.gate_g2({"pf": INF}))
    assert A.rows_pass(A.gate_g3(0.10)) and not A.rows_pass(A.gate_g3(0.1001))
    assert A.rows_pass(A.gate_g8({"pf": 1.15})) and not A.rows_pass(A.gate_g8({"pf": 1.1499}))
    assert A.rows_pass(A.gate_g14({"calmar_r": 0.75})) and not A.rows_pass(A.gate_g14({"calmar_r": 0.7499}))
    assert A.gate_g3(0.103)[0]["borderline"] and not A.gate_g3(0.2)[0]["borderline"]


def test_calmar_formula():
    trs = [mk("2025-01-0%d" % (i + 1), 2.0) for i in range(5)]
    run = mkrun(trs, dd=4.0)                         # risk medyan 1 -> DD_R 4; Sigma R 10; D 365
    m = A.basic_metrics(run, {})
    assert m["dd_r"] == pytest.approx(4.0) and m["calmar_r"] == pytest.approx((10 * 365 / 365) / 4.0)


def test_g4_ceil_rule_over_active_quarters():
    mk_m = lambda sums, n=100: {"windows_full": {f"w{i}": {"sum_r": x, "n": n} for i, x in enumerate(sums)}}
    assert A.ceil_frac((3, 4), 3) == 3 and A.ceil_frac((3, 4), 4) == 3 and A.ceil_frac((3, 4), 2) == 2
    assert A.ceil_frac((5, 7), 7) == 5 and A.ceil_frac((5, 7), 6) == 5 and A.ceil_frac((5, 7), 5) == 4
    assert A.ceil_frac((5, 7), 0) == 0
    assert A.gate_g4(mk_m([1, 1, 1]))[0]["pass"] and not A.gate_g4(mk_m([1, 1, -1]))[0]["pass"]     # 3 aktif -> 3 gerekir
    assert A.gate_g4(mk_m([1, 1, 1, -1]))[0]["pass"] and not A.gate_g4(mk_m([1, 1, -1, -1]))[0]["pass"]  # 4 aktif -> 3
    assert not A.gate_g4(mk_m([1, 1, 0, 0]))[0]["pass"]                                            # ΣR = 0 pozitif degil
    m = mk_m([1, 1, 1])
    m["windows_full"]["w3"] = {"sum_r": -5, "n": 9}                           # aktif degil: sayilmaz
    row = A.gate_g4(m)[0]
    assert row["pass"] and row["threshold"] == 3 and row["note"] == "aktif 3"
    assert A.gate_pos_windows("G10", mk_m([1] * 5 + [-1] * 2), (5, 7))["pass"]           # 7 aktif -> 5
    assert not A.gate_pos_windows("G10", mk_m([1] * 4 + [-1] * 3), (5, 7))["pass"]
    assert A.gate_pos_windows("G10", mk_m([1] * 4 + [-1]), (5, 7))["pass"]                # 5 aktif -> ceil(25/7)=4


def _g5_run(jan_wins, feb_wins, feb_losses):
    trs = [mk("2025-01-%02d" % (i + 1), 1.0) for i in range(jan_wins)]
    trs += [mk("2025-02-%02d" % (i + 1), 1.0) for i in range(feb_wins)]
    trs += [mk("2025-02-%02d" % (i + 1), -1.0) for i in range(feb_losses)]
    return mkrun(trs)


def test_g5_concentration():
    ok = A.gate_g5(_g5_run(6, 4, 4))
    assert A.rows_pass(ok)                                          # ex-ay PF 1.0, top5 sonrasi ΣR 1, top3 gun sonrasi 1
    top5_bad = A.gate_g5(_g5_run(5, 4, 4))
    assert _row(top5_bad, "PF (en iyi ay hariç)")["pass"] and not _row(top5_bad, "ΣR (en iyi 5 işlem hariç)")["pass"]
    pf_bad = A.gate_g5(_g5_run(6, 3, 4))
    assert not _row(pf_bad, "PF (en iyi ay hariç)")["pass"]


def _days_run(big_days: int, per_day: int, small: int, set_name="DEV"):
    """big_days gunun her biri per_day islem (+1R); small ayri gun -0.5R tek islem."""
    trs = []
    for d in range(big_days):
        trs += [mk(f"2025-03-{d + 1:02d}", 1.0, sym=f"S{i}") for i in range(per_day)]
    trs += [mk(f"2025-04-{d + 1:02d}", -0.5) for d in range(small)]
    return mkrun(trs, set_name)


def test_best_days_exclusion_uses_daily_vector_not_trades():
    run = _days_run(3, 4, 12)                      # 12 islem +1 uc gunde; 12 gun -0.5 => toplam +6
    assert A.sum_excluding_top(run.trades, 5) == pytest.approx(1.0)       # en iyi 5 islem hariç: 7 - 6
    assert A.sum_excluding_top_days(run, 3) == pytest.approx(-6.0)        # en iyi 3 gun hariç: yalniz kayiplar
    rows = A.gate_g5(run)
    assert _row(rows, "ΣR (en iyi 5 işlem hariç)")["pass"]
    assert not _row(rows, "ΣR (en iyi 3 gün hariç)")["pass"]
    assert _row(rows, "ΣR (en iyi 3 gün hariç)")["value"] == pytest.approx(-6.0)
    # cikis gunune gore: ayni sinyal/giris gunu, cikis farkli gune dusunce gun toplami degisir
    spread = mkrun([mk("2025-03-01", 1.0, exit_day=f"2025-03-{d + 1:02d}") for d in range(12)] +
                   [mk(f"2025-04-{d + 1:02d}", -0.5) for d in range(12)])
    assert A.sum_excluding_top_days(spread, 3) == pytest.approx(9.0 - 6.0)
    assert A.gate_g5(_days_run(4, 4, 4))[2]["pass"]                      # 4 gun +4, 3 atilinca +4 - 2 > 0
    assert A.sum_excluding_top_days(mkrun([]), 3) == 0.0


def test_g6_g12_stress_rows():
    assert A.rows_pass(A.gate_stress("G6", {"ROB_FEE2": 0.0001, "ROB_DLY1": 0.5}))
    assert not A.rows_pass(A.gate_stress("G6", {"ROB_FEE2": 0.0, "ROB_DLY1": 0.5}))
    assert not A.rows_pass(A.gate_stress("G12", {"ROB_FEE2": 1.0, "ROB_DLY1": None}))      # eksik kosu = basarisiz
    assert not A.rows_pass(A.gate_stress("G12", {"ROB_FEE2": 1.0}))


def test_g7_pool_and_active_full_quarters():
    def m(ns, n_total=None):
        wf = {f"w{i}": {"n": n} for i, n in enumerate(ns)}
        return {"n": n_total if n_total is not None else sum(ns), "windows_full": wf}
    assert A.rows_pass(A.gate_g7(m([30, 30, 30, 30, 30, 0, 0])))                         # N 150, aktif 5
    assert not A.rows_pass(A.gate_g7(m([30, 30, 30, 30, 29, 0, 0])))                     # N 149
    assert not A.rows_pass(A.gate_g7(m([100, 100, 10, 10, 9, 9, 9])))                    # aktif 4
    assert A.rows_pass(A.gate_g7(m([100, 100, 10, 10, 10, 9, 9])))                       # aktif 5, N 248


def test_g9_bonferroni_k():
    assert A.rows_pass(A.gate_g9(0.025, 0.05, 2)) and not A.rows_pass(A.gate_g9(0.0251, 0.05, 2))
    assert not A.rows_pass(A.gate_g9(0.025, 0.0501, 2))
    assert A.rows_pass(A.gate_g9(0.05, 0.10, 1)) and not A.rows_pass(A.gate_g9(0.0501, 0.10, 1))


def _g10_args(half1_n, half1_wins, half2_n, half2_wins, pos=7):
    months = [f"2023-{m:02d}" for m in range(1, 13)] + [f"2024-{m:02d}" for m in range(1, 11)]
    assert len(months) == 22
    trs = []
    for ms, n, w in ((months[:11], half1_n, half1_wins), (months[11:], half2_n, half2_wins)):
        for i in range(n):
            trs.append(SimpleNamespace(month=ms[i % 11], r=1.0 if i < w else -1.0))
    m = {"windows_full": {f"w{i}": {"sum_r": 1 if i < pos else -1, "n": 50} for i in range(7)}, "full_months": months}
    return SimpleNamespace(trades=trs), m


def test_g10_breadth_and_halves():
    assert A.rows_pass(A.gate_g10(*_g10_args(50, 25, 50, 25)))
    assert not A.rows_pass(A.gate_g10(*_g10_args(49, 25, 50, 25)))             # yari N 49
    assert not A.rows_pass(A.gate_g10(*_g10_args(50, 24, 50, 25)))             # yari PF < 1
    assert not A.rows_pass(A.gate_g10(*_g10_args(50, 25, 50, 25, pos=4)))      # aktif tam ceyrek 4/7


def _syms(spec: List[Tuple[str, str, int, int]]):
    """(sembol, ay, kazanc, kayip)"""
    trs = []
    for sym, mo, w, l_ in spec:
        trs += [mk(f"{mo}-{(i % 27) + 1:02d}", 1.0, sym=sym) for i in range(w)]
        trs += [mk(f"{mo}-{(i % 27) + 1:02d}", -1.0, sym=sym) for i in range(l_)]
    return mkrun(trs, "VAL")


def test_g11_concentration_rows():
    pass_m = A.gate_g11(_syms([("A", "2023-01", 10, 0), ("A", "2023-02", 11, 10), ("B", "2023-02", 10, 10)]))
    assert _row(pass_m, "PF (en iyi ay hariç)")["pass"]
    fail_m = A.gate_g11(_syms([("A", "2023-01", 10, 0), ("A", "2023-02", 10, 10), ("B", "2023-02", 10, 10)]))
    assert not _row(fail_m, "PF (en iyi ay hariç)")["pass"]
    ok_s = A.gate_g11(_syms([("A", "2023-01", 40, 0), ("B", "2023-01", 21, 20)]))
    assert _row(ok_s, "PF (en iyi sembol hariç)")["pass"]
    bad_s = A.gate_g11(_syms([("A", "2023-01", 40, 0), ("B", "2023-01", 20, 20)]))
    assert not _row(bad_s, "PF (en iyi sembol hariç)")["pass"]
    ok_l = A.gate_g11(_syms([("A", "2023-01", 10, 0), ("B", "2023-01", 10, 10)]))
    assert _row(ok_l, "bir-çıkar PF (en kötü)")["pass"]
    bad_l = A.gate_g11(_syms([("A", "2023-01", 10, 0), ("B", "2023-01", 9, 10)]))
    assert not _row(bad_l, "bir-çıkar PF (en kötü)")["pass"]


def test_g11_best_five_days_exclusion():
    def run(big_days):
        trs = []
        for d in range(big_days):                                           # her biri 4 islem +1 (ayni gun)
            trs += [mk(f"2023-01-{d + 1:02d}", 1.0, sym=f"S{i % 2}") for i in range(4)]
        trs += [mk(f"2023-02-{d + 1:02d}", -0.5, sym=f"S{d % 2}") for d in range(10)]
        return mkrun(trs, "VAL")
    row = lambda r: _row(A.gate_g11(r), "ΣR (en iyi 5 gün hariç)")                # noqa: E731
    assert not row(run(5))["pass"] and row(run(5))["value"] == pytest.approx(-5.0)   # 5 gun atilinca yalniz kayip kalir
    r6 = row(run(6))
    assert r6["value"] == pytest.approx(4.0 - 5.0) and not r6["pass"]                 # 6. gun +4, kayip -5 -> -1
    r7 = row(run(7))
    assert r7["value"] == pytest.approx(8.0 - 5.0) and r7["pass"]
    assert A.THRESH["G11_TOP_DAYS"] == 5 and A.THRESH["G5_TOP_DAYS"] == 3


def test_g13_down_regime_pool():
    mkt = lambda w, l_: [mk("2025-02-%02d" % ((i % 27) + 1), 1.0) for i in range(w)] + \
                        [mk("2025-02-%02d" % ((i % 27) + 1), -1.0) for i in range(l_)]
    assert A.rows_pass(A.gate_g13(mkt(28, 22)))                                          # n 50 yeterli, PF 1.27
    assert A.rows_pass(A.gate_g13(mkt(55, 50)))                                          # PF tam 1.10
    assert not _row(A.gate_g13(mkt(28, 21)), "DOWN ayları havuz N")["pass"]              # n 49 -> TEST EDILEMEDI
    assert not _row(A.gate_g13(mkt(54, 51)), "DOWN ayları havuz PF")["pass"]
    assert not A.rows_pass(A.gate_g13([]))


def test_g15_branches():
    base = {"n": 20, "pf": 1.0, "sum_r": 1.0, "dd_r": 5.0}
    assert A.g15({**base, "n": 19})[0] == "SONUÇSUZ"
    assert A.g15({**base, "pf": 0.8999})[0] == "VETO"
    assert A.g15({**base, "pf": 0.9, "dd_r": 25.0, "sum_r": -1.0})[0] == "BELİRSİZ"
    assert A.g15({**base, "pf": 0.9, "dd_r": 25.01, "sum_r": -1.0})[0] == "VETO"
    assert A.g15({**base, "pf": 1.0, "sum_r": 1.0})[0] == "TUTARLI"
    assert A.g15({**base, "pf": 1.0, "sum_r": 0.0})[0] == "BELİRSİZ"


def test_flags():
    trs = [mk("2025-02-%02d" % ((i % 28) + 1), -1.0, direction="SHORT", lev=20) for i in range(30)]
    trs += [mk("2025-03-%02d" % (i + 1), 1.0, direction="LONG", lev=20) for i in range(5)]
    run = mkrun(trs)
    m = A.basic_metrics(run, {})
    fl = A.flags_for(m, run, {})                                           # sok gunu yok: 35 islem hep normal gun
    assert fl["NORMAL-ZAYIF"] and fl["KELEPÇE"] and not fl["TEK-YÖNLÜ"]    # SHORT n=30 < 50
    allshock = {f"2025-02-{i + 1:02d}": "DOWN" for i in range(28)} | {f"2025-03-{i + 1:02d}": "UP" for i in range(5)}
    assert A.flags_for(m, run, allshock)["NORMAL-ZAYIF"] is False           # hepsi sok gunu: normal gun islemi yok
    few = mkrun(trs[:29] + trs[30:])                                       # 34 normal gun islemi, ΣR < 0
    assert A.flags_for(A.basic_metrics(few, {}), few, {})["NORMAL-ZAYIF"]
    tiny = mkrun(trs[:24] + trs[30:])                                      # 24 + 5 = 29 < 30
    assert A.flags_for(A.basic_metrics(tiny, {}), tiny, {})["NORMAL-ZAYIF"] is False
    one = lambda n: mkrun([mk("2025-02-%02d" % ((i % 28) + 1), -1.0, direction="SHORT") for i in range(n)])
    assert A.flags_for(A.basic_metrics(one(50), {}), one(50), {})["TEK-YÖNLÜ"]
    assert not A.flags_for(A.basic_metrics(one(49), {}), one(49), {})["TEK-YÖNLÜ"]


def test_z0_sentence():
    assert A.z0_sentence(1.05, 0.99) == "ham kenar var, maliyet yiyor"
    assert A.z0_sentence(1.0499, 0.5) == "maliyetten önce de kenar yok"


def test_advance_selection_one_per_base_and_cap():
    sel = A.select_advancers([("K30B-L", 5.0), ("K30B-H", 4.0), ("K30R-L", 3.0), ("G30B-H", 2.0)], VARIANTS)
    assert sel == ["K30B-L", "K30R-L"]                                       # K30B-H ayni taban (K30B)
    assert A.select_advancers([("K30B-H", 5.0), ("K30B-L", 4.0), ("G30B-L", 3.0)], VARIANTS) == ["K30B-H", "G30B-L"]
    assert A.select_advancers([("K30R-L", 5.0), ("K30R-H", 4.5), ("K30B-L", 4.0), ("G30B-L", 3.0)],
                              VARIANTS) == ["K30R-L", "K30B-L"]               # en cok 2
    assert A.select_advancers([("K30R-L", 1.0)], VARIANTS) == ["K30R-L"] and A.select_advancers([], VARIANTS) == []
    assert A.select_advancers([("K30R-H", 2.0), ("K30R-L", 2.0)], VARIANTS) == ["K30R-H"]      # esitlik: ada gore


# ==========================================================================
# tetikleyici betimleyici istatistikleri (el ile kurulmus, cevabi bilinen ornek)
# ==========================================================================
def _hand_block():
    """5 gun: g1 UP sok, g2 DOWN sok, g3 sok degil, g4 sok degil, g5 UP sok.
    Tetikleyici: g1 08:00 LONG (+10:00 LONG); g2 03:30 LONG sonra 05:00 SHORT; g3 14:30 SHORT; g4/g5 hic."""
    days = [{"date": "2025-01-01", "open": 100.0, "close": 104.0}, {"date": "2025-01-02", "open": 100.0, "close": 96.0},
            {"date": "2025-01-03", "open": 100.0, "close": 101.0}, {"date": "2025-01-04", "open": 100.0, "close": 102.0},
            {"date": "2025-01-05", "open": 100.0, "close": 105.0}]
    at = lambda day, h, m=0: A.date_ms(day) + (h * 60 + m) * 60_000  # noqa: E731
    on = [[at("2025-01-01", 10), "LONG"], [at("2025-01-01", 8), "LONG"], [at("2025-01-02", 5), "SHORT"],
          [at("2025-01-02", 3, 30), "LONG"], [at("2025-01-03", 14, 30), "SHORT"]]
    return {"pct": 1.5, "days": days, "on_points": on, "decision_points": 240, "nodata_points": 0}


def test_trigger_stats_hand_example():
    st = A.trigger_stats(_hand_block())
    assert st["trigger_days"] == 3 and st["shock_days"] == 3 and st["both"] == 2
    assert st["hit_rate"] == pytest.approx(2 / 3)
    assert st["dir_hit_rate"] == pytest.approx(1 / 3)          # yalniz g1: ilk yon LONG = UP; g2 ilk yon LONG != DOWN
    assert st["recall"] == pytest.approx(2 / 3)                # g5 sok ama tetikleyici hic acilmadi
    assert st["both_direction_days"] == 1                      # yalniz g2 iki yonde de acildi
    assert st["first_hour_mean"] == pytest.approx((8.0 + 3.5 + 14.5) / 3)
    assert st["first_hour_median"] == pytest.approx(8.0)
    assert st["first_hour_buckets_4h"] == [1, 0, 1, 1, 0, 0]   # 3.5 -> 00-04, 8.0 -> 08-12, 14.5 -> 12-16
    assert st["on_points"] == 5 and st["n_days"] == 5


def test_trigger_stats_no_activity_and_empty_shock():
    blk = {"pct": 3.0, "days": [{"date": "2025-01-01", "open": 100.0, "close": 100.5}], "on_points": []}
    st = A.trigger_stats(blk)
    assert st["trigger_days"] == 0 and st["hit_rate"] is None and st["recall"] is None
    assert st["first_hour_mean"] is None and st["first_hour_buckets_4h"] == [0] * 6
    blk["on_points"] = [[A.date_ms("2025-01-01") + 23 * 3_600_000 + 59 * 60_000, "SHORT"]]
    st = A.trigger_stats(blk)
    assert st["trigger_days"] == 1 and st["shock_days"] == 0 and st["recall"] is None and st["hit_rate"] == 0.0
    assert st["first_hour_buckets_4h"] == [0, 0, 0, 0, 0, 1] and st["first_hour_mean"] == pytest.approx(23 + 59 / 60)


def test_arm_descriptive_splits_and_density():
    blk = _hand_block()
    sig = lambda day, h=11: A.date_ms(day) + h * 3_600_000  # noqa: E731
    mkt = lambda day, r, d: A.make_tr("BTCUSDT", d, sig(day, 12), sig(day, 13), r, 100.0, 1.0, 10, "TP1", sig(day, 11))  # noqa: E731
    trs = [mkt("2025-01-01", 2.0, "LONG"), mkt("2025-01-01", -1.0, "SHORT"),          # g1 UP: ayni +2, ters -1
           mkt("2025-01-02", 1.0, "SHORT"), mkt("2025-01-03", -0.5, "LONG")]          # g2 DOWN: ayni +1; g3 normal
    run = mkrun(trs, shock=blk)
    d = A.arm_descriptive(run, VARIANTS, {"2025-01-01": "UP", "2025-01-02": "DOWN", "2025-01-03": "UP"})
    assert d["shock_day"]["all"]["n"] == 3 and d["shock_day"]["all"]["sum_r"] == pytest.approx(2.0)
    assert d["shock_day"]["LONG"]["n"] == 1 and d["shock_day"]["SHORT"]["n"] == 2
    assert d["normal_day"]["all"]["n"] == 1 and d["normal_day"]["all"]["sum_r"] == pytest.approx(-0.5)
    assert d["shock_direction"]["same"]["n"] == 2 and d["shock_direction"]["same"]["sum_r"] == pytest.approx(3.0)
    assert d["shock_direction"]["opposite"]["n"] == 1 and d["shock_direction"]["opposite"]["sum_r"] == pytest.approx(-1.0)
    assert d["long_short"]["LONG"]["n"] == 2 and d["long_short"]["SHORT"]["n"] == 2
    assert d["density"]["trigger_days"] == 3 and d["density"]["trades_per_trigger_day"] == pytest.approx(4 / 3)
    assert d["density"]["trades_per_calendar_day"] == pytest.approx(4 / 365)
    assert d["density"]["trigger_days_with_trade"] == 3
    # e18 listesiyle uyusmazlik: g3 (E18'de UP, kosuda sok degil) + g5 (kosuda UP, E18'de yok) = 2
    assert d["e18_shock_overlap_days"] == 5 and d["e18_shock_mismatch_days"] == 2


def test_trigger_activity_consistency_flag():
    a, b, c = _hand_block(), _hand_block(), _hand_block()
    c["on_points"] = c["on_points"][:2]
    runs = {"K30R-L": mkrun([], arm="K30R-L", shock=a), "K30B-L": mkrun([], arm="K30B-L", shock=b),
            "K30R-H": mkrun([], arm="K30R-H", shock=c), "K30B-H": mkrun([], arm="K30B-H", shock=a)}
    out = A.trigger_activity(VARIANTS, runs)
    assert sorted(out["by_threshold"]) == ["1.5", "3.0"]
    assert out["by_threshold"]["1.5"]["consistent"] and out["by_threshold"]["1.5"]["source_arm"] == "K30R-L"
    assert not out["by_threshold"]["3.0"]["consistent"] and out["by_threshold"]["3.0"]["source_arm"] == "K30R-H"
    assert len(out["integrity_flags"]) == 1 and "3.0" in out["integrity_flags"][0]


# ==========================================================================
# boru hatti fixture'lari (kilit git'siz: verify_lock yamali)
# ==========================================================================
@pytest.fixture
def env(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    pdir = repo / C.PLAN_REL
    pdir.mkdir(parents=True)
    _write_test_variants(pdir)
    (repo / Path(C.E17_LABELS_REL).parent).mkdir(parents=True)
    shutil.copy(REPO_REAL / C.E17_LABELS_REL, repo / C.E17_LABELS_REL)
    (repo / Path(C.SHOCK_E18_REL).parent).mkdir(parents=True)
    (repo / C.SHOCK_E18_REL).write_text(json.dumps({"days": {"2025-02-03": {"dir": "UP", "ret_pct": 4.0}}}))
    root = tmp_path / "root"
    (root / "sealed" / "runs").mkdir(parents=True)
    (root / "sealed" / "logs").mkdir(parents=True)
    monkeypatch.setattr(A, "REPO", repo)
    monkeypatch.setattr(A, "verify_lock", lambda r: A.make_ctx(r, {"snapshot_sha256": "x"}, "L" * 40, "H" * 40, "locksha"))
    return SimpleNamespace(repo=repo, pdir=pdir, root=root, paths=C.Paths(root))


def trig_block(label: str, specs: Sequence[Tuple[str, float, str, str, int]], shock_days: Sequence[str] = ()) -> dict:
    """Sentetik shock_trigger blogu: her islemin sinyal anında (gun 11:00) tetikleyici o yonde ACIK."""
    set_name, arm, _ = C.parse_label(label) if not label.startswith("S2_") else ("CAL", label[3:-3], [])
    s0, e0 = C.set_window(VARIANTS, set_name)
    n_days = (dt.date.fromisoformat(e0) - dt.date.fromisoformat(s0)).days
    days = []
    for i in range(n_days):
        d = (dt.date.fromisoformat(s0) + dt.timedelta(days=i)).isoformat()
        days.append({"date": d, "open": 100.0, "close": 104.0 if d in shock_days else 100.1})
    on = sorted({(ms_of(day, 11), d) for day, _r, _s, d, _l in specs})
    return {"pct": C.shock_pct_of(VARIANTS, arm), "leader": "BTCUSDT", "tf": "30m",
            "window_start_ms": A.date_ms(s0), "window_end_ms": A.date_ms(e0), "decision_points": n_days * 48,
            "nodata_points": 0, "on_points": [[ms, d] for ms, d in on], "days": days}


def put_run(env, label: str, specs: Sequence[Tuple[str, float, str, str, int]], missed=None, dd=3.0, shock_days=()):
    """specs: (gun, R, sembol, yon, kaldirac). Rapor + ledger satiri yazar."""
    set_name, arm, ovs = C.parse_label(label)
    S = C.resolve_S(VARIANTS, arm, ovs)
    trades = []
    for day, r, sym, d, lev in specs:
        trades.append({"strategy": "M", "symbol": sym, "direction": d, "entry_time": ms_of(day),
                       "signal_close_time": ms_of(day, 11),
                       "exit_time": ms_of(day, 13), "leverage": lev, "margin_usdt": 100.0, "pnl": r * S,
                       "exit_reason": "TP1"})
    doc = {"overall": {"bar_max_drawdown": dd, "trades": len(trades)}, "missed_signals": missed or {},
           "trades": trades, "provenance": {"scalper_config": {"scalper_x": 1}},
           "shock_trigger": trig_block(label, specs, shock_days)}
    raw = json.dumps(doc).encode()
    env.paths.report(label).write_bytes(raw)
    with open(env.paths.ledger, "a") as fh:
        fh.write(json.dumps({"label": label, "report_sha256": hashlib.sha256(raw).hexdigest(), "H": "H" * 40}) + "\n")


SYMS = C.load_variants(REPO_REAL / C.PLAN_REL / "variants.json")["symbols"].split(",")


def good_specs(start: str, days: int, bad_every: int = 3):
    s = dt.date.fromisoformat(start)
    out = []
    for i in range(days):
        d = (s + dt.timedelta(days=i)).isoformat()
        out.append((d, -0.7 if i % bad_every == 0 else 1.0, SYMS[i % 8], "LONG" if i % 2 else "SHORT", 10))
    return out


def seed_dev(env, good=("K30R-L", "K30B-L"), stress_ok=True, dly1_bad=()):
    for arm in ARMS:
        if arm in good:
            put_run(env, f"DEV_{arm}", good_specs("2025-01-01", 365))
        else:
            put_run(env, f"DEV_{arm}", good_specs("2025-01-01", 10))
    if stress_ok:
        for arm in good:
            put_run(env, f"DEV_{arm}__ROB_FEE2", good_specs("2025-01-01", 365))
            put_run(env, f"DEV_{arm}__ROB_DLY1",
                    [(d, -1.0, s, di, lv) for d, _r, s, di, lv in good_specs("2025-01-01", 365)]
                    if arm in dly1_bad else good_specs("2025-01-01", 365))


def seed_val(env, arms, final_arm=None):
    for arm in arms:
        put_run(env, f"VAL_{arm}", good_specs("2023-01-01", 687))
        for ov in ("ROB_FEE2", "ROB_DLY1"):
            put_run(env, f"VAL_{arm}__{ov}", good_specs("2023-01-01", 687))
    if final_arm:
        put_run(env, f"FINAL_{final_arm}", good_specs("2026-10-16", 35))


def test_dev_screen_forces_p1_and_keeps_m6(env, capsys):
    seed_dev(env, good=("K30R-L",))
    assert A.main(["dev-screen", "--root", str(env.root)]) == 0
    assert capsys.readouterr().out.split() == ["K30R-L"]
    doc = json.loads((env.pdir / "run" / "DEV_SCREEN.json").read_text())
    assert doc["m"] == 6
    assert doc["arms"]["K30B-L"]["p7_used"] == 1.0 and not doc["arms"]["K30B-L"]["eligible_for_bh"]
    p = doc["arms"]["K30R-L"]["p7_used"]
    assert doc["arms"]["K30R-L"]["q"] == pytest.approx(min(1.0, p * 6))       # m = 6 sabit, tek gecerli kol
    assert not doc["arms"]["K30B-L"]["pass_g0_g5"]
    assert not A.rows_pass(doc["arms"]["K30B-L"]["gates"]["G1"])


def test_dev_decide_requires_stress_and_writes_once(env, capsys):
    seed_dev(env, stress_ok=False)
    A.main(["dev-screen", "--root", str(env.root)])
    capsys.readouterr()
    assert A.main(["dev-decide", "--root", str(env.root)]) == 0
    assert capsys.readouterr().out.strip() == "DEV-YOK"                      # eksik stres kosusu = G6 basarisiz
    target = env.pdir / "run" / "DEV_DECISION.json"
    before = target.read_bytes()
    assert A.main(["dev-decide", "--root", str(env.root)]) == 5
    assert target.read_bytes() == before


def test_full_pipeline_dev_val_final_report(env, capsys):
    seed_dev(env, dly1_bad=("K30B-L",))
    A.main(["dev-screen", "--root", str(env.root)])
    A.main(["dev-decide", "--root", str(env.root)])
    out = capsys.readouterr().out
    dec = json.loads((env.pdir / "run" / "DEV_DECISION.json").read_text())
    assert dec["advancers"] == ["K30R-L"] and "DEV-İLERLER:K30R-L" in out        # K30B-L DLY1 ΣR < 0 -> G6 kaldi
    assert not dec["arms"]["K30B-L"]["pass_g0_g6"]
    ta = dec["descriptive"]["trigger"]
    assert sorted(ta["by_threshold"]) == ["1.5", "3.0"] and set(dec["descriptive"]["arms"]) == set(ARMS)
    assert ta["by_threshold"]["1.5"]["source_arm"] == "K30R-L" and ta["by_threshold"]["1.5"]["trigger_days"] == 365
    assert not ta["by_threshold"]["1.5"]["consistent"] and len(ta["integrity_flags"]) == 1   # G30B-L: 10 gunluk islem
    assert ta["by_threshold"]["3.0"]["consistent"]
    dd = dec["descriptive"]["arms"]["K30R-L"]
    assert dd["density"]["trigger_days_with_trade"] == 365 and dd["long_short"]["LONG"]["n"] + dd["long_short"]["SHORT"]["n"] == 365
    seed_val(env, ["K30R-L"], final_arm="K30R-L")
    assert A.main(["val-decide", "--root", str(env.root)]) == 0
    val = json.loads((env.pdir / "run" / "VAL_DECISION.json").read_text())
    assert val["winner"] == "K30R-L" and val["k"] == 1 and val["arms"]["K30R-L"]["pass"]
    assert set(val["descriptive"]["arms"]) == {"K30R-L"} and val["descriptive"]["set"] == "VAL"
    assert val["descriptive"]["trigger"]["by_threshold"]["1.5"]["trigger_days"] == 687
    assert A.main(["val-decide", "--root", str(env.root)]) == 5
    assert A.main(["final", "--root", str(env.root)]) == 0
    fin = json.loads((env.pdir / "run" / "FINAL.json").read_text())
    assert fin["g15"] == "TUTARLI" and fin["label"] == "KAZANAN-HARNESS"
    assert A.main(["final", "--root", str(env.root)]) == 5
    assert A.main(["report", "--root", str(env.root)]) == 0
    md = (env.pdir / "run" / "DECISION.md").read_text()
    assert "Sonuç etiketi: KAZANAN-HARNESS" in md and A.SENT_WIN in md
    for sec in ("## Bütünlük", "## Aile tablosu", "## Kapı matrisi", "## Stres koşuları", "## Teşhis koşuları",
                "## Sınırlar", "LONG ve SHORT", "Rejim grubu", "şokla AYNI yön", "## VAL", "## FINAL",
                "# E23 karar raporu", "tetikleyici etkinliği", "İlk açılış saati", "Yoğunluk:", "Ay (giriş ayı)",
                "BÜTÜNLÜK BAYRAĞI", "E18 listesiyle örtüşen", "Bayraklar:"):
        assert sec in md
    assert "# E18" not in md and "ŞOK-ZAYIF" not in md
    assert not re.search("[\U0001F000-\U0001FFFF☀-➿]", md)           # emoji yok
    rows = (env.pdir / "run" / "results.csv").read_text().splitlines()
    assert rows[0].startswith("label,set,arm") and any(r.startswith("DEV_K30R-L,") for r in rows)


def test_val_decide_fails_gate_when_stress_negative(env):
    seed_dev(env)
    A.main(["dev-screen", "--root", str(env.root)])
    A.main(["dev-decide", "--root", str(env.root)])
    seed_val(env, ["K30R-L", "K30B-L"])
    neg = [(d, -1.0, s, di, lv) for d, _r, s, di, lv in good_specs("2023-01-01", 687)]
    # K30B-L'nin FEE2 koşusunu negatiflestir (rapor + ledger yeniden)
    env.paths.report("VAL_K30B-L__ROB_FEE2").unlink()
    lines = [ln for ln in env.paths.ledger.read_text().splitlines() if "VAL_K30B-L__ROB_FEE2" not in ln]
    env.paths.ledger.write_text("\n".join(lines) + "\n")
    put_run(env, "VAL_K30B-L__ROB_FEE2", neg)
    assert A.main(["val-decide", "--root", str(env.root)]) == 0
    val = json.loads((env.pdir / "run" / "VAL_DECISION.json").read_text())
    assert val["k"] == 2 and val["arms"]["K30R-L"]["pass"] and not val["arms"]["K30B-L"]["pass"]
    assert val["winner"] == "K30R-L"


def test_report_no_winner_uses_e23_sentence(env, capsys):
    seed_dev(env, good=())
    A.main(["dev-screen", "--root", str(env.root)])
    A.main(["dev-decide", "--root", str(env.root)])
    assert capsys.readouterr().out.strip() == "DEV-YOK"
    assert A.main(["report", "--root", str(env.root)]) == 0
    md = (env.pdir / "run" / "DECISION.md").read_text()
    assert "Sonuç etiketi: DEV-YOK" in md and A.SENT_NO in md and A.SENT_WIN not in md
    assert "BTC gün-içi şok tetikleyicisi %1.5 ve %3.0" in A.SENT_NO
    assert A.SENT_WIN == "Yalnız testnet ölçüm adayı; gerçek para için kanıt değil; §10 koşulları ve kullanıcı kararı gerekir."


def test_report_refuses_before_seal(env):
    assert A.main(["report", "--root", str(env.root)]) == 6
    assert not (env.pdir / "run" / "DECISION.md").exists()


def test_val_requires_advancer_and_final_requires_winner(env):
    assert A.main(["val-decide", "--root", str(env.root)]) == 4
    assert A.main(["final", "--root", str(env.root)]) == 4


# ==========================================================================
# kilit (gercek git, gecici depo)
# ==========================================================================
def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.email=a@b.c", "-c", "user.name=n", *args],
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def lockrepo(tmp_path, monkeypatch):
    repo = tmp_path / "lrepo"
    repo.mkdir()
    _git(repo, "init", "-q")
    pdir = repo / C.PLAN_REL
    pdir.mkdir(parents=True)
    _write_test_variants(pdir)
    (pdir / "PLAN.md").write_text("plan\n")
    (repo / Path(C.SHOCK_E18_REL).parent).mkdir(parents=True)
    (repo / C.SHOCK_E18_REL).write_text('{"days":{}}\n')
    fam = C.arm_ids(C.load_variants(pdir / "variants.json"))
    (pdir / "cal_counts.json").write_text(json.dumps(
        {"arms": {a: {"testable": True} for a in fam}, "family_testable": fam}) + "\n")
    (repo / Path(C.E17_LABELS_REL).parent).mkdir(parents=True)
    shutil.copy(REPO_REAL / C.E17_LABELS_REL, repo / C.E17_LABELS_REL)
    for rel in A.BLOB_PATHS:
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# {rel}\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "H")
    h = _git(repo, "rev-parse", "HEAD")
    root = tmp_path / "lroot"
    (root / "sealed" / "runs").mkdir(parents=True)
    monkeypatch.setattr(A, "REPO", repo)
    return SimpleNamespace(repo=repo, pdir=pdir, root=root, h=h)


def _do_lock(lr):
    assert A.main(["lock", "--root", str(lr.root), "--commit", lr.h]) == 0
    _git(lr.repo, "add", "-A")
    _git(lr.repo, "commit", "-q", "-m", "L")
    l_commit = _git(lr.repo, "rev-parse", "HEAD")
    sha = C.sha256_file(lr.pdir / "LOCK.json")
    (lr.root / "LOCK_REF.json").write_text(json.dumps({"L": l_commit, "lock_sha256": sha, "H": lr.h}))
    (lr.root / "H.txt").write_text(lr.h + "\n")
    return l_commit


def test_lock_and_verify_ok_then_mismatch_exit3_no_files(lockrepo, capsys):
    lr = lockrepo
    _do_lock(lr)
    lock = json.loads((lr.pdir / "LOCK.json").read_text())
    assert lock["H"] == lr.h and lock["H_tree"] == _git(lr.repo, "rev-parse", f"{lr.h}^{{tree}}")
    assert set(lock["files"]) == set(A.LOCK_FILE_NAMES) and set(lock["blobs_at_H"]) == set(A.BLOB_PATHS)
    assert lock["labels_e17"]["path"] == C.E17_LABELS_REL and lock["snapshot_sha256"]
    assert lock["labels_e17"]["sha256"] == C.sha256_file(lr.repo / C.E17_LABELS_REL)
    assert lock["shock_e18"] == {"path": C.SHOCK_E18_REL, "sha256": C.sha256_file(lr.repo / C.SHOCK_E18_REL)}
    assert set(A.LOCK_FILE_NAMES) == {"PLAN.md", "variants.json", "cal_counts.json"}
    assert "src/strategies/scalper/market_gate.py" in lock["blobs_at_H"] and "src/strategies/scalper/engine.py" in lock["blobs_at_H"]
    assert A.main(["verify-lock", "--root", str(lr.root)]) == 0
    cal_p = lr.pdir / "cal_counts.json"
    orig_cal = cal_p.read_text()
    cal_p.write_text("{}\n")                                                       # kilitli dosya degisti
    assert A.main(["verify-lock", "--root", str(lr.root)]) == 3
    assert A.main(["dev-screen", "--root", str(lr.root)]) == 3
    assert not (lr.pdir / "run").exists()                                         # hicbir sey yazilmadi
    cal_p.write_text(orig_cal)
    assert A.main(["verify-lock", "--root", str(lr.root)]) == 0
    shock_p = lr.repo / C.SHOCK_E18_REL
    shock_p.write_text('{"days":{"x":1}}\n')                                      # E18 sok listesi degisti
    assert A.main(["verify-lock", "--root", str(lr.root)]) == 3
    shock_p.write_text('{"days":{}}\n')
    assert A.main(["verify-lock", "--root", str(lr.root)]) == 0
    lab_p = lr.repo / C.E17_LABELS_REL                                            # E17 etiket dosyasi degisti
    orig = lab_p.read_bytes()
    lab_p.write_bytes(orig + b" ")
    assert A.main(["verify-lock", "--root", str(lr.root)]) == 3
    lab_p.write_bytes(orig)
    assert A.main(["verify-lock", "--root", str(lr.root)]) == 0
    (lr.repo / "scripts/e23/analyze_e23.py").write_text("# degisti\n")             # betik blob'u degisti
    assert A.main(["verify-lock", "--root", str(lr.root)]) == 3


def test_verify_lock_detects_tampered_lock_file_and_ref(lockrepo):
    lr = lockrepo
    _do_lock(lr)
    (lr.pdir / "LOCK.json").write_text((lr.pdir / "LOCK.json").read_text() + " ")
    assert A.main(["verify-lock", "--root", str(lr.root)]) == 3
    assert A.main(["report", "--root", str(lr.root)]) == 6                         # muhur once: DEV_DECISION yok


def test_lock_refuses_with_dev_label_and_dirty_scripts(lockrepo):
    lr = lockrepo
    with open(lr.root / "ledger.jsonl", "w") as fh:
        fh.write(json.dumps({"label": "DEV_K30R-L"}) + "\n")
    assert A.main(["lock", "--root", str(lr.root), "--commit", lr.h]) == 4
    assert not (lr.pdir / "LOCK.json").exists()
    (lr.root / "ledger.jsonl").write_text(json.dumps({"label": "CAL_K30R-L"}) + "\n")
    (lr.repo / "scripts/e23/run_e23.py").write_text("# kirli\n")
    assert A.main(["lock", "--root", str(lr.root), "--commit", lr.h]) == 4
    for tag in ("VAL_", "FINAL_"):
        (lr.root / "ledger.jsonl").write_text(json.dumps({"label": tag + "K30R-L"}) + "\n")
        assert A.main(["lock", "--root", str(lr.root), "--commit", lr.h]) == 4


# ==========================================================================
# check-run
# ==========================================================================
def _typed(v: str):
    if v in ("true", "false"):
        return v == "true"
    try:
        f = float(v)
        return f
    except ValueError:
        return v


def valid_report(label: str = "CAL_K30R-L", entry_trades: int = 2):
    kind, set_name, arm, ovs = A._label_kind(label)
    s, e = C.set_window(VARIANTS, set_name)
    days = (dt.date.fromisoformat(e) - dt.date.fromisoformat(s)).days
    cfg = {k.lower(): _typed(v) for k, v in C.resolve_env(VARIANTS, arm, ovs).items()}
    cfg["scalper_entry_block_weekdays_utc"] = ""
    triple = C.triple_of(VARIANTS, arm)
    cli = C.resolve_cli(VARIANTS, ovs)
    trades = [{"strategy": "M", "symbol": "BTCUSDT", "direction": "LONG", "entry_time": A.date_ms(s) + 3_600_000 * (i + 1),
               "exit_time": A.date_ms(s) + 3_600_000 * (i + 5), "leverage": 5, "margin_usdt": 100.0,
               "signal_close_time": A.date_ms(s) + 3_600_000 * (i + 1) - 1_800_000,
               "pnl": 1234.5678 + i, "exit_reason": "TP1"} for i in range(entry_trades)]
    total = sum(t["pnl"] for t in trades)
    block = {"pct": C.shock_pct_of(VARIANTS, arm), "leader": "BTCUSDT", "tf": "30m", "window_start_ms": A.date_ms(s),
             "window_end_ms": A.date_ms(e), "decision_points": days * 48, "nodata_points": 0,
             "on_points": [[t["signal_close_time"], "LONG"] for t in trades],
             "days": [{"date": s, "open": 100.0, "close": 100.1}]}
    return {
        "params": {"days": days, "symbols": VARIANTS["symbols"].split(","), "strategies": "M"},
        "provenance": {
            "git_sha": "H" * 40, "git_dirty": False,
            "scalper_config": cfg,
            "test_window": {"start_ms": A.date_ms(s), "end_ms": A.date_ms(e)},
            "data_windows": {sym: {tf: {"candles_in_test_window": days * C.BARS_PER_DAY[tf]} for tf in set(triple)}
                             for sym in VARIANTS["symbols"].split(",")},
            "cost_model": {"stress_multiplier": 2.0 if "--fee-stress" in cli else 1.0,
                           "entry_delay_candles": int(cli[cli.index("--entry-delay-candles") + 1])
                           if "--entry-delay-candles" in cli else 0},
            "realism": {"enabled": list(A.REALISM_NINE), "disabled": []},
        },
        "overall": {"trades": len(trades), "total_pnl": total, "profit_factor": None, "bar_max_drawdown": 77.7},
        "missed_signals": {"shock_trigger_off": 7, "shock_trigger_dir": 2, "shock_trigger_nodata": 0},
        "trades": trades, "shock_trigger": block,
    }


def run_check(env, label, doc, log="ok\n", h="H" * 40, write_log=True, capsys=None):
    (env.root / "H.txt").write_text(h + "\n")
    env.paths.report(label).write_text(json.dumps(doc))
    if write_log:
        env.paths.log(label).write_text(log)
    elif env.paths.log(label).exists():
        env.paths.log(label).unlink()
    rc = A.main(["check-run", "--root", str(env.root), "--label", label])
    return rc, capsys.readouterr().out


def test_check_run_ok_variants(env, capsys):
    for label in ("CAL_K30R-L", "DEV_K30B-H", "DEV_G30B-L__ROB_FEE2", "DEV_K30B-L__ROB_DLY1", "DEV_K30R-H__DIAG_Z0",
                  "DEV_G30B-H__DIAG_NOLATCH", "VAL_K30B-L__ROB_FEE2+ROB_DLY1", "S2_K30B-L_h0", "S2_K30B-L_h1"):
        rc, out = run_check(env, label, valid_report(label), capsys=capsys)
        assert rc == 0 and out.strip() == f"CHECK OK {label}", (label, out)


def _mut(fn):
    def apply(doc):
        fn(doc)
        return doc
    return apply


FAIL_CASES = {
    "git_sha": lambda d: d["provenance"].__setitem__("git_sha", "X" * 40),
    "dirty": lambda d: d["provenance"].__setitem__("git_dirty", True),
    "window": lambda d: d["provenance"]["test_window"].__setitem__("end_ms", 1),
    "strategies": lambda d: d["params"].__setitem__("strategies", "C"),
    "symbols": lambda d: d["params"].__setitem__("symbols", ["BTCUSDT"]),
    "triple": lambda d: d["provenance"]["scalper_config"].__setitem__("scalper_tf_context", "1h"),
    "env_value": lambda d: d["provenance"]["scalper_config"].__setitem__("scalper_m_trigger", "pullback"),
    "env_missing": lambda d: d["provenance"]["scalper_config"].pop("scalper_market_gate"),
    "env_bool": lambda d: d["provenance"]["scalper_config"].__setitem__("scalper_regime_filter", True),
    "env_weekdays": lambda d: d["provenance"]["scalper_config"].__setitem__("scalper_entry_block_weekdays_utc", "5,6"),
    "cost_stress": lambda d: d["provenance"]["cost_model"].__setitem__("stress_multiplier", 2.0),
    "cost_delay": lambda d: d["provenance"]["cost_model"].__setitem__("entry_delay_candles", 1),
    "realism_missing": lambda d: d["provenance"]["realism"]["enabled"].pop(),
    "realism_disabled": lambda d: d["provenance"]["realism"].__setitem__("disabled", ["funding"]),
    "trade_count": lambda d: d["overall"].__setitem__("trades", 99),
    "pnl_sum": lambda d: d["overall"].__setitem__("total_pnl", d["overall"]["total_pnl"] + 5),
    "strategy_not_m": lambda d: d["trades"][0].__setitem__("strategy", "C"),
    "candle_gap": lambda d: d["provenance"]["data_windows"]["ETHUSDT"]["30m"].__setitem__("candles_in_test_window", 5),
    "exit_outside": lambda d: d["trades"][0].__setitem__("exit_time", A.date_ms("2027-01-01")),
    "no_missed": lambda d: d.pop("missed_signals"),
    "no_dd": lambda d: d["overall"].pop("bar_max_drawdown"),
    # --- PLAN §5 tetikleyici denetimleri
    "trig_missing": lambda d: d.pop("shock_trigger"),
    "trig_pct": lambda d: d["shock_trigger"].__setitem__("pct", 3.0),
    "trig_leader": lambda d: d["shock_trigger"].__setitem__("leader", "ETHUSDT"),
    "trig_tf": lambda d: d["shock_trigger"].__setitem__("tf", "15m"),
    "trig_decision_points": lambda d: d["shock_trigger"].__setitem__("decision_points", 92 * 48 - 1),
    "trig_nodata": lambda d: d["shock_trigger"].__setitem__("nodata_points", 3),
    "trig_violation_off": lambda d: d["shock_trigger"].__setitem__("on_points", d["shock_trigger"]["on_points"][1:]),
    "trig_violation_dir": lambda d: d["shock_trigger"]["on_points"][0].__setitem__(1, "SHORT"),
    "trig_no_signal_time": lambda d: d["trades"][0].pop("signal_close_time"),
    "trig_onpoints_bad": lambda d: d["shock_trigger"].__setitem__("on_points", [[1, "UP"]]),
    "market_gate_missed": lambda d: d["missed_signals"].__setitem__("market_gate_opposite", 4),
}


@pytest.mark.parametrize("case", sorted(FAIL_CASES))
def test_check_run_failures_no_pnl_digits(env, capsys, case):
    doc = valid_report("CAL_K30R-L")
    FAIL_CASES[case](doc)
    rc, out = run_check(env, "CAL_K30R-L", doc, capsys=capsys)
    assert rc == 97 and out.startswith("CHECK FAIL CAL_K30R-L:")
    assert "1234" not in out and "5678" not in out and "77.7" not in out


def test_check_run_trigger_failure_reasons_named(env, capsys):
    doc = valid_report("CAL_K30R-L")
    doc["shock_trigger"]["on_points"] = doc["shock_trigger"]["on_points"][1:]
    rc, out = run_check(env, "CAL_K30R-L", doc, capsys=capsys)
    assert rc == 97 and "tetikleyici ihlali: 1 işlem" in out
    doc = valid_report("CAL_K30R-L")
    doc["shock_trigger"]["nodata_points"] = 5
    rc, out = run_check(env, "CAL_K30R-L", doc, capsys=capsys)
    assert rc == 97 and "nodata_points" in out
    # taban ve kaplama kosularinda ayni denetim; S1 (C stratejisi, tetikleyicisiz) bloksuz gecer
    doc = valid_report("DEV_K30B-L__ROB_DLY1")
    doc["shock_trigger"]["on_points"][0][1] = "SHORT"
    rc, out = run_check(env, "DEV_K30B-L__ROB_DLY1", doc, capsys=capsys)
    assert rc == 97 and "tetikleyici ihlali" in out
    s1 = s1_report()
    s1.pop("shock_trigger", None)
    rc, out = run_check(env, "S1_AYI_C", s1, capsys=capsys)
    assert rc == 0


def test_check_run_cost_and_log_failures(env, capsys):
    rc, out = run_check(env, "DEV_K30R-L__ROB_FEE2", {**valid_report("DEV_K30R-L__ROB_FEE2")} | {
        "provenance": {**valid_report("DEV_K30R-L__ROB_FEE2")["provenance"],
                       "cost_model": {"stress_multiplier": 1.0, "entry_delay_candles": 0}}}, capsys=capsys)
    assert rc == 97 and "stress_multiplier" in out
    rc, out = run_check(env, "CAL_K30R-L", valid_report(), log="x Binance'ten çekiliyor y", capsys=capsys)
    assert rc == 97 and "çekiliyor" in out
    rc, out = run_check(env, "CAL_K30R-L", valid_report(), write_log=False, capsys=capsys)
    assert rc == 97 and "log dosyası yok" in out
    rc, out = run_check(env, "CAL_K30R-L", valid_report(), h="Z" * 40, capsys=capsys)
    assert rc == 97 and "git_sha" in out


def s1_report(n=92, total=2808.05):
    rep = valid_report("CAL_K30R-L", entry_trades=0)
    s, e = VARIANTS["smoke"]["S1"]["window"]
    per = total / n
    rep["trades"] = [{"strategy": "C", "symbol": "BTCUSDT", "direction": "LONG", "entry_time": A.date_ms(s) + 1000 * (i + 1),
                      "exit_time": A.date_ms(s) + 7_200_000, "leverage": 5, "margin_usdt": 100.0, "pnl": per,
                      "exit_reason": "TP1"} for i in range(n)]
    rep["params"]["strategies"] = "C"
    rep["provenance"]["test_window"] = {"start_ms": A.date_ms(s), "end_ms": A.date_ms(e)}
    rep["provenance"]["scalper_config"] = {}
    rep["overall"].update({"trades": n, "total_pnl": sum(t["pnl"] for t in rep["trades"])})
    return rep


def test_check_run_s1_parity(env, capsys):
    rc, out = run_check(env, "S1_AYI_C", s1_report(), capsys=capsys)
    assert rc == 0 and out.strip() == "CHECK OK S1_AYI_C"
    rc, out = run_check(env, "S1_AYI_C", s1_report(n=91, total=2808.05), capsys=capsys)
    assert rc == 97 and "parite" in out and "2808" not in out
    rc, out = run_check(env, "S1_AYI_C", s1_report(n=92, total=2808.06), capsys=capsys)
    assert rc == 97


def test_values_equal_type_tolerant():
    ve = A.values_equal
    assert ve("false", False) and not ve("true", False) and ve("9", 9.0) and ve("9", 9) and ve("0", 0.0)
    assert ve("", "") and ve("", None) and ve("", []) and ve("a,b", ["a", "b"]) and not ve("a", "b")
    assert not ve("x", 1.0)


def test_s2_compare(env, capsys):
    base = valid_report("S2_K30B-L_h0")
    h = lambda tag: env.paths.report(tag)                                    # noqa: E731
    h("S2_K30B-L_h0").write_text(json.dumps(base))
    h("S2_K30B-L_h1").write_text(json.dumps({**base, "generated_at": "farkli"}))
    assert A.main(["s2-compare", "--root", str(env.root)]) == 0 and capsys.readouterr().out.strip() == "S2 OK"
    other = {**base, "trades": base["trades"][:1]}
    h("S2_K30B-L_h1").write_text(json.dumps(other))
    assert A.main(["s2-compare", "--root", str(env.root)]) == 97 and capsys.readouterr().out.strip() == "S2 FAIL"
    # trades ayni, shock_trigger blogu farkli -> basarisiz
    blk = json.loads(json.dumps(base["shock_trigger"]))
    blk["on_points"] = blk["on_points"][:1]
    h("S2_K30B-L_h1").write_text(json.dumps({**base, "shock_trigger": blk}))
    assert A.main(["s2-compare", "--root", str(env.root)]) == 97 and capsys.readouterr().out.strip() == "S2 FAIL"
    # blok eksik (iki raporda da) -> esit sayilmaz
    nob = {k: v for k, v in base.items() if k != "shock_trigger"}
    h("S2_K30B-L_h0").write_text(json.dumps(nob))
    h("S2_K30B-L_h1").write_text(json.dumps(nob))
    assert A.main(["s2-compare", "--root", str(env.root)]) == 97 and "eksik" in capsys.readouterr().out
    h("S2_K30B-L_h1").unlink()
    assert A.main(["s2-compare", "--root", str(env.root)]) == 97


# ==========================================================================
# cal-counts, freeze-shock, package, parser
# ==========================================================================
def test_cal_counts_poisoned_performance_fields(env, capsys):
    for i, arm in enumerate(ARMS):
        n = 38 if i == 0 else 37            # 38 * 365 / 92 = 150.8 >= 150 ; 37 -> 146.8 < 150
        trades = [{"direction": "LONG" if j % 2 else "SHORT", "entry_time": A.date_ms("2026-03-05") + j * 3_600_000,
                   "leverage": 20 if j % 4 == 0 else (3 if j % 4 == 1 else 8),
                   "pnl": "ZEHIR", "roi_pct": {"x": 1}, "exit_reason": ["ZEHIR"]} for j in range(n)]
        on = [[A.date_ms("2026-03-05") + 3_600_000, "LONG"], [A.date_ms("2026-03-05") + 7_200_000, "SHORT"],
              [A.date_ms("2026-03-07") + 3_600_000, "LONG"]]
        doc = {"overall": {"total_pnl": "ZEHIR", "profit_factor": "ZEHIR", "roi_pct": "ZEHIR"}, "trades": trades,
               "missed_signals": {"capacity": 3, "shock_trigger_off": 11, "shock_trigger_dir": 4},
               "shock_trigger": {"pct": 1.5, "decision_points": 4416, "nodata_points": 0, "on_points": on,
                                 "days": [{"date": "2026-03-05", "open": "ZEHIR", "close": "ZEHIR"}]}}
        env.paths.report(C.make_label("CAL", arm)).write_text(json.dumps(doc))
    assert A.main(["cal-counts", "--root", str(env.root)]) == 0
    out = capsys.readouterr().out
    assert "ZEHIR" not in out and "pnl" not in out.lower() and "pf" not in out.lower()
    raw = (env.pdir / "cal_counts.json").read_text()
    assert "ZEHIR" not in raw and "pnl" not in raw and "roi_pct" not in raw and "profit_factor" not in raw
    doc = json.loads(raw)
    assert doc["schema"] == "e23-cal/1"
    a0, a1 = doc["arms"][ARMS[0]], doc["arms"][ARMS[1]]
    assert a0["n"] == 38 and a0["projected_per_year"] == pytest.approx(38 * 365 / 92) and a0["testable"]
    assert a1["projected_per_year"] == pytest.approx(37 * 365 / 92) and not a1["testable"]
    assert a0["n_long"] + a0["n_short"] == 38 and sum(a0["per_month"].values()) == 38
    assert a0["lev20_share"] == pytest.approx(10 / 38) and a0["lev3_share"] == pytest.approx(10 / 38)
    assert a0["missed_signals"] == {"capacity": 3, "shock_trigger_off": 11, "shock_trigger_dir": 4}
    assert a0["trigger_days"] == 2 and a0["trigger_on_points"] == 3        # 03-05 (iki nokta) ve 03-07
    assert doc["family_testable"] == [ARMS[0]]


def test_package_strips_scalper_config(env):
    put_run(env, "DEV_K30R-L", good_specs("2025-01-01", 3))
    (env.root / "MANIFEST.data.json").write_text("{}")
    assert A.main(["package", "--root", str(env.root)]) == 6                               # muhur: DEV_DECISION yok
    assert not (env.pdir / "run" / "artifacts.tgz").exists()
    (env.pdir / "run").mkdir(parents=True, exist_ok=True)
    (env.pdir / "run" / "DEV_DECISION.json").write_text("{}")
    assert A.main(["package", "--root", str(env.root)]) == 0
    tgz = env.pdir / "run" / "artifacts.tgz"
    with tarfile.open(tgz) as tar:
        names = tar.getnames()
        assert names == ["runs/DEV_K30R-L.json", "ledger.jsonl", "MANIFEST.data.json"]
        data = json.loads(tar.extractfile("runs/DEV_K30R-L.json").read())
    assert "scalper_config" not in data["provenance"] and len(data["trades"]) == 3
    first = tgz.read_bytes()
    A.main(["package", "--root", str(env.root)])
    assert tgz.read_bytes() == first                                                     # belirlenimci uye sirasi/mtime


def test_parser_exposes_no_statistics_option():
    ap = A.build_parser()
    opts = set()
    for act in ap._actions:
        if isinstance(act, argparse._SubParsersAction):
            assert set(act.choices) == {"cal-counts", "lock", "verify-lock", "check-run", "s2-compare",
                                        "dev-screen", "dev-decide", "val-decide", "final", "report", "package"}
            for sp in act.choices.values():
                for a in sp._actions:
                    opts.update(a.option_strings)
    assert opts <= {"-h", "--help", "--root", "--commit", "--label"}
    for bad in ("--boot-n", "--alpha", "--block", "--threshold", "--seed"):
        with pytest.raises(SystemExit):
            ap.parse_args(["dev-screen", "--root", "x", bad, "5"])


def test_verify_lock_binds_runs_to_locked_commit(lockrepo):
    lr = lockrepo
    _do_lock(lr)
    assert A.main(["verify-lock", "--root", str(lr.root)]) == 0
    (lr.root / "ledger.jsonl").write_text(json.dumps({"label": "DEV_K30R-L", "H": lr.h}) + "\n")
    assert A.main(["verify-lock", "--root", str(lr.root)]) == 0
    (lr.root / "ledger.jsonl").write_text(json.dumps({"label": "DEV_K30R-L", "H": "0" * 40}) + "\n")
    assert A.main(["verify-lock", "--root", str(lr.root)]) == 3                    # baska commit'te kosulmus
    (lr.root / "ledger.jsonl").unlink()
    (lr.root / "H.txt").write_text("0" * 40 + "\n")
    assert A.main(["verify-lock", "--root", str(lr.root)]) == 3                    # klon baska commit'te
    (lr.root / "H.txt").unlink()
    assert A.main(["verify-lock", "--root", str(lr.root)]) == 3                    # H.txt yok


def test_lock_requires_family_equal_to_testable_arms(lockrepo):
    lr = lockrepo
    fam = C.arm_ids(C.load_variants(lr.pdir / "variants.json"))
    (lr.pdir / "cal_counts.json").write_text(json.dumps(
        {"arms": {a: {"testable": a != fam[0]} for a in fam}, "family_testable": fam[1:]}) + "\n")
    assert A.main(["lock", "--root", str(lr.root), "--commit", lr.h]) == 4          # test edilemeyen kol ailede
    assert not (lr.pdir / "LOCK.json").exists()


def test_lock_accepts_cal_record_of_dropped_arm(lockrepo):
    """Aileden çıkarılan (test edilemeyen) kolun CAL sayımı kayıtta kalır; kilit yine geçer."""
    lr = lockrepo
    fam = C.arm_ids(C.load_variants(lr.pdir / "variants.json"))
    arms = {a: {"testable": True} for a in fam}
    arms["DROPPED-X"] = {"testable": False}
    (lr.pdir / "cal_counts.json").write_text(json.dumps({"arms": arms, "family_testable": fam}) + "\n")
    assert A.main(["lock", "--root", str(lr.root), "--commit", lr.h]) == 0
    assert (lr.pdir / "LOCK.json").exists()


def test_final_label_marks_unconfirmed_final():
    labels = {"TUTARLI": "KAZANAN-HARNESS", "BELİRSİZ": "KAZANAN-HARNESS (FINAL doğrulamadı)",
              "VETO": "FINAL-VETO", "SONUÇSUZ": "ADAY-FINAL-SONUÇSUZ"}
    src = (REPO_REAL / "scripts/e23/analyze_e23.py").read_text(encoding="utf-8")
    for g, lab in labels.items():
        if g != "TUTARLI":
            assert f'"{g}": "{lab}"' in src
    assert A.overall_label({"advancers": ["K30R-L"]}, {"winner": "K30R-L"},
                           {"label": labels["BELİRSİZ"]}).startswith("KAZANAN-HARNESS")
