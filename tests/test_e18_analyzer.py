"""E18 on-kayitli analizci testleri (scripts/e18/analyze_e18.py). AGSIZ ve SENTETIK: gercek rapor okunmaz.

Bootstrap B kucultulur (THRESH monkeypatch); tum dosya < 20 sn hedefler.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import math
import random
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pytest

REPO_REAL = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_REAL / "scripts" / "e18"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import analyze_e18 as A  # noqa: E402
import e18_common as C  # noqa: E402

from src.strategies.scalper.multitest import benjamini_hochberg  # noqa: E402

INF = float("inf")
DAY = 86_400_000
FAST = {"BOOT_N": 199, "BOOT_N_HI": 399}
VARIANTS = C.load_variants(REPO_REAL / C.PLAN_REL / "variants.json")
ARMS = C.arm_ids(VARIANTS)


def ms_of(day: str, hour: int = 12) -> int:
    return A.date_ms(day) + hour * 3_600_000


def mk(day: str, r: float, sym: str = "BTCUSDT", direction: str = "LONG", lev: int = 10, reason: str = "TP1",
       exit_day: Optional[str] = None) -> "A.Tr":
    """R = r (marj 100, S 1 -> risk 1)."""
    return A.make_tr(sym, direction, ms_of(day), ms_of(exit_day or day, 13), r, 100.0, 1.0, lev, reason)


def mkrun(trades: Sequence["A.Tr"], set_name: str = "DEV", arm: str = "K15R", missed: Optional[dict] = None,
          dd: float = 1.0) -> "A.Run":
    s, e = C.set_window(VARIANTS, set_name)
    days = (dt.date.fromisoformat(e) - dt.date.fromisoformat(s)).days
    return A.Run(f"{set_name}_{arm}", set_name, arm, [], 1.0, dt.date.fromisoformat(s), days, list(trades),
                 dict(missed or {}), dd)


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


def test_shock_assignment_and_threshold_inclusive():
    days = {}
    d = dt.date(2023, 1, 1)
    while d < dt.date(2026, 1, 1):
        days[d] = (100.0, 100.1)
        d += dt.timedelta(days=1)
    days[dt.date(2025, 3, 3)] = (100.0, 103.0)      # tam %3 -> sok (>=)
    days[dt.date(2025, 3, 4)] = (100.0, 102.99)     # alti -> sok degil
    days[dt.date(2023, 5, 5)] = (100.0, 95.0)
    doc = A.build_shock(days, VARIANTS)
    assert set(doc["days"]) == {"2025-03-03", "2023-05-05"}
    assert doc["days"]["2025-03-03"]["dir"] == "UP" and doc["days"]["2023-05-05"]["dir"] == "DOWN"
    assert doc["counts"] == {"DEV": 1, "VAL": 1} and doc["n_days"] == {"DEV": 365, "VAL": 687}


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
    seed = A.seed_for("DEV", "K15R", 7)
    p1, t1 = A.bootstrap_p(x, 7, seed, 150)
    p2, _ = A.bootstrap_p(x, 7, seed, 150)
    assert p1 == p2 and t1 == pytest.approx(A.t_obs_of(x))
    assert p1 == _naive_p(x, 7, seed, 150)
    assert A.seed_for("DEV", "K15R", 7) == int.from_bytes(hashlib.sha256(b"E18|DEV|K15R|b7").digest()[:8], "big")
    assert A.seed_for("DEV", "K15R", 7, hi=True) == int.from_bytes(
        hashlib.sha256(b"E18|DEV|K15R|b7|hi").digest()[:8], "big")


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


def test_g1_window_n():
    ok = A.basic_metrics(_quarter_run({"2025Q1": 60, "2025Q2": 60, "2025Q3": 60, "2025Q4": 60}), {})
    bad = A.basic_metrics(_quarter_run({"2025Q1": 60, "2025Q2": 59, "2025Q3": 60, "2025Q4": 60}), {})
    assert A.rows_pass(A.gate_min_window_n("G1", ok, 60)) and not A.rows_pass(A.gate_min_window_n("G1", bad, 60))


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


def test_g4_positive_windows():
    mk_m = lambda sums: {"windows_full": {f"w{i}": {"sum_r": s, "n": 100, "pf": 1.0} for i, s in enumerate(sums)}}
    assert A.gate_g4(mk_m([1, 1, 1, -1]))[0]["pass"] and not A.gate_g4(mk_m([1, 1, -1, -1]))[0]["pass"]
    assert not A.gate_g4(mk_m([1, 1, 0, 0]))[0]["pass"]                 # ΣR = 0 pozitif degil
    assert A.gate_pos_windows("G10", mk_m([1] * 5 + [-1] * 2), 5)["pass"]
    assert not A.gate_pos_windows("G10", mk_m([1] * 4 + [-1] * 3), 5)["pass"]


def _g5_run(jan_wins, feb_wins, feb_losses):
    trs = [mk("2025-01-%02d" % (i + 1), 1.0) for i in range(jan_wins)]
    trs += [mk("2025-02-%02d" % (i + 1), 1.0) for i in range(feb_wins)]
    trs += [mk("2025-02-%02d" % (i + 1), -1.0) for i in range(feb_losses)]
    return mkrun(trs)


def test_g5_concentration():
    ok = A.gate_g5(_g5_run(6, 4, 4))
    assert A.rows_pass(ok)                                          # ex-ay PF 1.0 ve top5 sonrasi ΣR 1
    top5_bad = A.gate_g5(_g5_run(5, 4, 4))
    assert _row(top5_bad, "PF (en iyi ay hariç)")["pass"] and not _row(top5_bad, "ΣR (en iyi 5 işlem hariç)")["pass"]
    pf_bad = A.gate_g5(_g5_run(6, 3, 4))
    assert not _row(pf_bad, "PF (en iyi ay hariç)")["pass"]


def test_g6_g12_stress_rows():
    assert A.rows_pass(A.gate_stress("G6", {"ROB_FEE2": 0.0001, "ROB_DLY1": 0.5}))
    assert not A.rows_pass(A.gate_stress("G6", {"ROB_FEE2": 0.0, "ROB_DLY1": 0.5}))
    assert not A.rows_pass(A.gate_stress("G12", {"ROB_FEE2": 1.0, "ROB_DLY1": None}))      # eksik kosu = basarisiz
    assert not A.rows_pass(A.gate_stress("G12", {"ROB_FEE2": 1.0}))


def test_g7_window_n():
    m = lambda n: {"windows_full": {f"w{i}": {"n": n if i == 0 else 90} for i in range(7)}}
    assert A.rows_pass(A.gate_min_window_n("G7", m(60), 60)) and not A.rows_pass(A.gate_min_window_n("G7", m(59), 60))


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
    m = {"windows_full": {f"w{i}": {"sum_r": 1 if i < pos else -1} for i in range(7)}, "full_months": months}
    return SimpleNamespace(trades=trs), m


def test_g10_breadth_and_halves():
    assert A.rows_pass(A.gate_g10(*_g10_args(100, 50, 100, 50)))
    assert not A.rows_pass(A.gate_g10(*_g10_args(99, 50, 100, 50)))            # N 99
    assert not A.rows_pass(A.gate_g10(*_g10_args(100, 49, 100, 50)))           # PF < 1
    assert not A.rows_pass(A.gate_g10(*_g10_args(100, 50, 100, 50, pos=4)))    # pencere 4/7


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


def test_g13_down_regime_pool():
    mkt = lambda w, l_: [mk("2025-02-%02d" % ((i % 27) + 1), 1.0) for i in range(w)] + \
                        [mk("2025-02-%02d" % ((i % 27) + 1), -1.0) for i in range(l_)]
    ok = A.gate_g13(mkt(55, 50))
    assert A.rows_pass(ok)                                                    # n 105, PF 1.10
    assert not _row(A.gate_g13(mkt(50, 49)), "DOWN ayları havuz N")["pass"]  # n 99 -> TEST EDILEMEDI
    assert not _row(A.gate_g13(mkt(54, 51)), "DOWN ayları havuz PF")["pass"]


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
    shock = {f"2025-02-{i + 1:02d}": "UP" for i in range(28)}
    fl = A.flags_for(m, run, shock)
    assert fl["ŞOK-ZAYIF"] and fl["KELEPÇE"] and not fl["TEK-YÖNLÜ"]       # SHORT n=30 < 100
    assert A.flags_for(m, run, {})["ŞOK-ZAYIF"] is False


def test_z0_sentence():
    assert A.z0_sentence(1.05, 0.99) == "ham kenar var, maliyet yiyor"
    assert A.z0_sentence(1.0499, 0.5) == "maliyetten önce de kenar yok"


def test_advance_selection_dedupe_and_cap():
    sel = A.select_advancers([("K15R", 5.0), ("K15B", 4.0), ("K30R", 3.0), ("G15B", 2.0)], VARIANTS)
    assert sel == ["K15R", "K30R"]                                           # K15B ayni (kirilim, 15m)
    assert A.select_advancers([("K15B", 5.0), ("K15R", 4.0), ("G15B", 3.0)], VARIANTS) == ["K15B", "G15B"]
    assert A.select_advancers([("K15R", 5.0), ("K30B", 4.0), ("K30R", 3.0)], VARIANTS) == ["K15R", "K30B"]
    assert A.select_advancers([("K15R", 1.0)], VARIANTS) == ["K15R"] and A.select_advancers([], VARIANTS) == []


# ==========================================================================
# boru hatti fixture'lari (kilit git'siz: verify_lock yamali)
# ==========================================================================
@pytest.fixture
def env(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    pdir = repo / C.PLAN_REL
    pdir.mkdir(parents=True)
    shutil.copy(REPO_REAL / C.PLAN_REL / "variants.json", pdir / "variants.json")
    (repo / Path(C.E17_LABELS_REL).parent).mkdir(parents=True)
    shutil.copy(REPO_REAL / C.E17_LABELS_REL, repo / C.E17_LABELS_REL)
    (pdir / "shock_days.json").write_text(json.dumps({"days": {"2025-02-03": {"dir": "UP", "ret_pct": 4.0}}}))
    root = tmp_path / "root"
    (root / "sealed" / "runs").mkdir(parents=True)
    (root / "sealed" / "logs").mkdir(parents=True)
    monkeypatch.setattr(A, "REPO", repo)
    monkeypatch.setattr(A, "verify_lock", lambda r: A.make_ctx(r, {"snapshot_sha256": "x"}, "L" * 40, "H" * 40, "locksha"))
    return SimpleNamespace(repo=repo, pdir=pdir, root=root, paths=C.Paths(root))


def put_run(env, label: str, specs: Sequence[Tuple[str, float, str, str, int]], missed=None, dd=3.0):
    """specs: (gun, R, sembol, yon, kaldirac). Rapor + ledger satiri yazar."""
    set_name, arm, ovs = C.parse_label(label)
    S = C.resolve_S(VARIANTS, arm, ovs)
    trades = []
    for day, r, sym, d, lev in specs:
        trades.append({"strategy": "M", "symbol": sym, "direction": d, "entry_time": ms_of(day),
                       "exit_time": ms_of(day, 13), "leverage": lev, "margin_usdt": 100.0, "pnl": r * S,
                       "exit_reason": "TP1"})
    doc = {"overall": {"bar_max_drawdown": dd, "trades": len(trades)}, "missed_signals": missed or {},
           "trades": trades, "provenance": {"scalper_config": {"scalper_x": 1}}}
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


def seed_dev(env, good=("K15R", "K30R"), stress_ok=True, dly1_bad=()):
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
    seed_dev(env, good=("K15R",))
    assert A.main(["dev-screen", "--root", str(env.root)]) == 0
    assert capsys.readouterr().out.split() == ["K15R"]
    doc = json.loads((env.pdir / "run" / "DEV_SCREEN.json").read_text())
    assert doc["m"] == 6
    assert doc["arms"]["K30R"]["p7_used"] == 1.0 and not doc["arms"]["K30R"]["eligible_for_bh"]
    p = doc["arms"]["K15R"]["p7_used"]
    assert doc["arms"]["K15R"]["q"] == pytest.approx(min(1.0, p * 6))       # m = 6 sabit, tek gecerli kol
    assert not doc["arms"]["K30R"]["pass_g0_g5"]
    assert not A.rows_pass(doc["arms"]["K30R"]["gates"]["G1"])


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
    seed_dev(env, dly1_bad=("K30R",))
    A.main(["dev-screen", "--root", str(env.root)])
    A.main(["dev-decide", "--root", str(env.root)])
    out = capsys.readouterr().out
    dec = json.loads((env.pdir / "run" / "DEV_DECISION.json").read_text())
    assert dec["advancers"] == ["K15R"] and "DEV-İLERLER:K15R" in out        # K30R DLY1 ΣR < 0 -> G6 kaldi
    assert not dec["arms"]["K30R"]["pass_g0_g6"]
    seed_val(env, ["K15R"], final_arm="K15R")
    assert A.main(["val-decide", "--root", str(env.root)]) == 0
    val = json.loads((env.pdir / "run" / "VAL_DECISION.json").read_text())
    assert val["winner"] == "K15R" and val["k"] == 1 and val["arms"]["K15R"]["pass"]
    assert A.main(["val-decide", "--root", str(env.root)]) == 5
    assert A.main(["final", "--root", str(env.root)]) == 0
    fin = json.loads((env.pdir / "run" / "FINAL.json").read_text())
    assert fin["g15"] == "TUTARLI" and fin["label"] == "KAZANAN-HARNESS"
    assert A.main(["final", "--root", str(env.root)]) == 5
    assert A.main(["report", "--root", str(env.root)]) == 0
    md = (env.pdir / "run" / "DECISION.md").read_text()
    assert "Sonuç etiketi: KAZANAN-HARNESS" in md and A.SENT_WIN in md
    for sec in ("## Bütünlük", "## Aile tablosu", "## Kapı matrisi", "## Stres koşuları", "## Teşhis koşuları",
                "## Sınırlar", "LONG ve SHORT", "Rejim grubu", "şokla AYNI yön", "## VAL", "## FINAL"):
        assert sec in md
    assert not re.search("[\U0001F000-\U0001FFFF☀-➿]", md)           # emoji yok
    rows = (env.pdir / "run" / "results.csv").read_text().splitlines()
    assert rows[0].startswith("label,set,arm") and any(r.startswith("DEV_K15R,") for r in rows)


def test_val_decide_fails_gate_when_stress_negative(env):
    seed_dev(env)
    A.main(["dev-screen", "--root", str(env.root)])
    A.main(["dev-decide", "--root", str(env.root)])
    seed_val(env, ["K15R", "K30R"])
    neg = [(d, -1.0, s, di, lv) for d, _r, s, di, lv in good_specs("2023-01-01", 687)]
    # K30R'nin FEE2 koşusunu negatiflestir (rapor + ledger yeniden)
    env.paths.report("VAL_K30R__ROB_FEE2").unlink()
    lines = [ln for ln in env.paths.ledger.read_text().splitlines() if "VAL_K30R__ROB_FEE2" not in ln]
    env.paths.ledger.write_text("\n".join(lines) + "\n")
    put_run(env, "VAL_K30R__ROB_FEE2", neg)
    assert A.main(["val-decide", "--root", str(env.root)]) == 0
    val = json.loads((env.pdir / "run" / "VAL_DECISION.json").read_text())
    assert val["k"] == 2 and val["arms"]["K15R"]["pass"] and not val["arms"]["K30R"]["pass"]
    assert val["winner"] == "K15R"


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
    shutil.copy(REPO_REAL / C.PLAN_REL / "variants.json", pdir / "variants.json")
    (pdir / "PLAN.md").write_text("plan\n")
    (pdir / "shock_days.json").write_text('{"days":{}}\n')
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
    assert A.main(["verify-lock", "--root", str(lr.root)]) == 0
    (lr.pdir / "shock_days.json").write_text('{"days":{"x":1}}\n')                # kilitli dosya degisti
    assert A.main(["verify-lock", "--root", str(lr.root)]) == 3
    assert A.main(["dev-screen", "--root", str(lr.root)]) == 3
    assert not (lr.pdir / "run").exists()                                         # hicbir sey yazilmadi
    (lr.pdir / "shock_days.json").write_text('{"days":{}}\n')
    assert A.main(["verify-lock", "--root", str(lr.root)]) == 0
    (lr.repo / "scripts/e18/analyze_e18.py").write_text("# degisti\n")             # betik blob'u degisti
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
        fh.write(json.dumps({"label": "DEV_K15R"}) + "\n")
    assert A.main(["lock", "--root", str(lr.root), "--commit", lr.h]) == 4
    assert not (lr.pdir / "LOCK.json").exists()
    (lr.root / "ledger.jsonl").write_text(json.dumps({"label": "CAL_K15R"}) + "\n")
    (lr.repo / "scripts/e18/run_e18.py").write_text("# kirli\n")
    assert A.main(["lock", "--root", str(lr.root), "--commit", lr.h]) == 4
    for tag in ("VAL_", "FINAL_"):
        (lr.root / "ledger.jsonl").write_text(json.dumps({"label": tag + "K15R"}) + "\n")
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


def valid_report(label: str = "CAL_K15R", entry_trades: int = 2):
    kind, set_name, arm, ovs = A._label_kind(label)
    s, e = C.set_window(VARIANTS, set_name)
    days = (dt.date.fromisoformat(e) - dt.date.fromisoformat(s)).days
    cfg = {k.lower(): _typed(v) for k, v in C.resolve_env(VARIANTS, arm, ovs).items()}
    cfg["scalper_entry_block_weekdays_utc"] = ""
    triple = C.triple_of(VARIANTS, arm)
    cli = C.resolve_cli(VARIANTS, ovs)
    trades = [{"strategy": "M", "symbol": "BTCUSDT", "direction": "LONG", "entry_time": A.date_ms(s) + 3_600_000 * (i + 1),
               "exit_time": A.date_ms(s) + 3_600_000 * (i + 5), "leverage": 5, "margin_usdt": 100.0,
               "pnl": 1234.5678 + i, "exit_reason": "TP1"} for i in range(entry_trades)]
    total = sum(t["pnl"] for t in trades)
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
        "missed_signals": {}, "trades": trades,
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
    for label in ("CAL_K15R", "DEV_K30R", "DEV_G15B__ROB_FEE2", "DEV_K15B__ROB_DLY1", "DEV_K15R__DIAG_Z0",
                  "DEV_K15R__DIAG_MAKER", "DEV_K30B__DIAG_T", "S2_K15R_h0", "S2_K15R_h1"):
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
    "triple": lambda d: d["provenance"]["scalper_config"].__setitem__("scalper_tf_context", "4h"),
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
    "candle_gap": lambda d: d["provenance"]["data_windows"]["ETHUSDT"]["15m"].__setitem__("candles_in_test_window", 5),
    "exit_outside": lambda d: d["trades"][0].__setitem__("exit_time", A.date_ms("2027-01-01")),
    "no_missed": lambda d: d.pop("missed_signals"),
    "no_dd": lambda d: d["overall"].pop("bar_max_drawdown"),
}


@pytest.mark.parametrize("case", sorted(FAIL_CASES))
def test_check_run_failures_no_pnl_digits(env, capsys, case):
    doc = valid_report("CAL_K15R")
    FAIL_CASES[case](doc)
    rc, out = run_check(env, "CAL_K15R", doc, capsys=capsys)
    assert rc == 97 and out.startswith("CHECK FAIL CAL_K15R:")
    assert "1234" not in out and "5678" not in out and "77.7" not in out


def test_check_run_cost_and_log_failures(env, capsys):
    rc, out = run_check(env, "DEV_K15R__ROB_FEE2", {**valid_report("DEV_K15R__ROB_FEE2")} | {
        "provenance": {**valid_report("DEV_K15R__ROB_FEE2")["provenance"],
                       "cost_model": {"stress_multiplier": 1.0, "entry_delay_candles": 0}}}, capsys=capsys)
    assert rc == 97 and "stress_multiplier" in out
    rc, out = run_check(env, "CAL_K15R", valid_report(), log="x Binance'ten çekiliyor y", capsys=capsys)
    assert rc == 97 and "çekiliyor" in out
    rc, out = run_check(env, "CAL_K15R", valid_report(), write_log=False, capsys=capsys)
    assert rc == 97 and "log dosyası yok" in out
    rc, out = run_check(env, "CAL_K15R", valid_report(), h="Z" * 40, capsys=capsys)
    assert rc == 97 and "git_sha" in out


def s1_report(n=92, total=2808.05):
    rep = valid_report("CAL_K15R", entry_trades=0)
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
    base = valid_report("S2_K15R_h0")
    env.paths.report("S2_K15R_h0").write_text(json.dumps(base))
    env.paths.report("S2_K15R_h1").write_text(json.dumps({**base, "generated_at": "farkli"}))
    assert A.main(["s2-compare", "--root", str(env.root)]) == 0 and capsys.readouterr().out.strip() == "S2 OK"
    other = {**base, "trades": base["trades"][:1]}
    env.paths.report("S2_K15R_h1").write_text(json.dumps(other))
    assert A.main(["s2-compare", "--root", str(env.root)]) == 97 and capsys.readouterr().out.strip() == "S2 FAIL"


# ==========================================================================
# cal-counts, freeze-shock, package, parser
# ==========================================================================
def test_cal_counts_poisoned_performance_fields(env, capsys):
    for i, arm in enumerate(ARMS):
        n = 92 if i == 0 else 90
        trades = [{"direction": "LONG" if j % 2 else "SHORT", "entry_time": A.date_ms("2026-03-05") + j * 3_600_000,
                   "leverage": 20 if j % 4 == 0 else (3 if j % 4 == 1 else 8),
                   "pnl": "ZEHIR", "roi_pct": {"x": 1}, "exit_reason": ["ZEHIR"]} for j in range(n)]
        doc = {"overall": {"total_pnl": "ZEHIR", "profit_factor": "ZEHIR", "roi_pct": "ZEHIR"}, "trades": trades,
               "missed_signals": {"capacity": 3}}
        env.paths.report(C.make_label("CAL", arm)).write_text(json.dumps(doc))
    assert A.main(["cal-counts", "--root", str(env.root)]) == 0
    out = capsys.readouterr().out
    assert "ZEHIR" not in out and "pnl" not in out.lower() and "pf" not in out.lower()
    raw = (env.pdir / "cal_counts.json").read_text()
    assert "ZEHIR" not in raw and "pnl" not in raw and "roi_pct" not in raw and "profit_factor" not in raw
    doc = json.loads(raw)
    a0, a1 = doc["arms"][ARMS[0]], doc["arms"][ARMS[1]]
    assert a0["n"] == 92 and a0["projected_per_quarter"] == pytest.approx(92 * 91.25 / 92) and a0["testable"]
    assert a1["projected_per_quarter"] == pytest.approx(90 * 91.25 / 92) and not a1["testable"]       # 89.27 < 90
    assert a0["n_long"] + a0["n_short"] == 92 and sum(a0["per_month"].values()) == 92
    assert a0["lev20_share"] == pytest.approx(23 / 92) and a0["lev3_share"] == pytest.approx(23 / 92)
    assert a0["missed_signals"] == {"capacity": 3} and doc["family_testable"] == [ARMS[0]]


def _btc_zip_dir(d: Path, header_first: bool = True):
    d.mkdir(parents=True)
    day = dt.date(2023, 1, 1)
    shock = {dt.date(2025, 6, 6): 1.05, dt.date(2024, 2, 2): 0.95}
    while day < dt.date(2026, 1, 1):
        ym = f"{day.year}-{day.month:02d}"
        zp = d / f"BTCUSDT-1d-{ym}.zip"
        rows = []
        if header_first and ym.endswith("01"):
            rows.append("open_time,open,high,low,close,volume,close_time")
        while day.month == int(ym[5:]) and day.year == int(ym[:4]):
            ot = A.date_ms(day.isoformat())
            if day.year == 2025:
                ot *= 1000                                              # mikrosaniye (yeni arsiv bicimi)
            c = 100.0 * shock.get(day, 1.001)
            rows.append(f"{ot},100.0,{c * 1.01},{c * 0.99},{c},10,{ot + 1}")
            day += dt.timedelta(days=1)
        with zipfile.ZipFile(zp, "w") as z:
            z.writestr(f"BTCUSDT-1d-{ym}.csv", "\n".join(rows) + "\n")


def test_freeze_shock_cli(env, capsys):
    _btc_zip_dir(env.repo / C.E17_BTC1D_REL)
    (env.pdir / "shock_days.json").unlink()
    assert A.main(["freeze-shock"]) == 0
    out = capsys.readouterr().out
    assert "DEV 1/365" in out and "VAL 1/687" in out
    p = env.pdir / "shock_days.json"
    doc = json.loads(p.read_text())
    assert doc["schema"] == "e18-shock/1" and doc["threshold"] == 0.03 and doc["source"] == C.E17_BTC1D_REL
    assert set(doc["days"]) == {"2025-06-06", "2024-02-02"} and doc["days"]["2024-02-02"]["dir"] == "DOWN"
    assert doc["counts"] == {"DEV": 1, "VAL": 1} and doc["n_days"] == {"DEV": 365, "VAL": 687}
    first = p.read_bytes()
    assert A.main(["freeze-shock"]) == 4 and p.read_bytes() == first                    # --force olmadan reddedilir
    assert A.main(["freeze-shock", "--force"]) == 0 and p.read_bytes() == first          # belirlenimci


def test_package_strips_scalper_config(env):
    put_run(env, "DEV_K15R", good_specs("2025-01-01", 3))
    (env.root / "MANIFEST.data.json").write_text("{}")
    assert A.main(["package", "--root", str(env.root)]) == 6                               # muhur: DEV_DECISION yok
    assert not (env.pdir / "run" / "artifacts.tgz").exists()
    (env.pdir / "run").mkdir(parents=True, exist_ok=True)
    (env.pdir / "run" / "DEV_DECISION.json").write_text("{}")
    assert A.main(["package", "--root", str(env.root)]) == 0
    tgz = env.pdir / "run" / "artifacts.tgz"
    with tarfile.open(tgz) as tar:
        names = tar.getnames()
        assert names == ["runs/DEV_K15R.json", "ledger.jsonl", "MANIFEST.data.json"]
        data = json.loads(tar.extractfile("runs/DEV_K15R.json").read())
    assert "scalper_config" not in data["provenance"] and len(data["trades"]) == 3
    first = tgz.read_bytes()
    A.main(["package", "--root", str(env.root)])
    assert tgz.read_bytes() == first                                                     # belirlenimci uye sirasi/mtime


def test_parser_exposes_no_statistics_option():
    ap = A.build_parser()
    opts = set()
    for act in ap._actions:
        if isinstance(act, argparse._SubParsersAction):
            assert set(act.choices) == {"freeze-shock", "cal-counts", "lock", "verify-lock", "check-run", "s2-compare",
                                        "dev-screen", "dev-decide", "val-decide", "final", "report", "package"}
            for sp in act.choices.values():
                for a in sp._actions:
                    opts.update(a.option_strings)
    assert opts <= {"-h", "--help", "--root", "--commit", "--label", "--force"}
    for bad in ("--boot-n", "--alpha", "--block", "--threshold", "--seed"):
        with pytest.raises(SystemExit):
            ap.parse_args(["dev-screen", "--root", "x", bad, "5"])


def test_verify_lock_binds_runs_to_locked_commit(lockrepo):
    lr = lockrepo
    _do_lock(lr)
    assert A.main(["verify-lock", "--root", str(lr.root)]) == 0
    (lr.root / "ledger.jsonl").write_text(json.dumps({"label": "DEV_K15R", "H": lr.h}) + "\n")
    assert A.main(["verify-lock", "--root", str(lr.root)]) == 0
    (lr.root / "ledger.jsonl").write_text(json.dumps({"label": "DEV_K15R", "H": "0" * 40}) + "\n")
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


def test_final_label_marks_unconfirmed_final():
    labels = {"TUTARLI": "KAZANAN-HARNESS", "BELİRSİZ": "KAZANAN-HARNESS (FINAL doğrulamadı)",
              "VETO": "FINAL-VETO", "SONUÇSUZ": "ADAY-FINAL-SONUÇSUZ"}
    src = (REPO_REAL / "scripts/e18/analyze_e18.py").read_text(encoding="utf-8")
    for g, lab in labels.items():
        if g != "TUTARLI":
            assert f'"{g}": "{lab}"' in src
    assert A.overall_label({"advancers": ["K15R"]}, {"winner": "K15R"},
                           {"label": labels["BELİRSİZ"]}).startswith("KAZANAN-HARNESS")
