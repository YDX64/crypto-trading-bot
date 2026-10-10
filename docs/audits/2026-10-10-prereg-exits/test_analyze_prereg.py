"""analyze_prereg.py testleri (sentetik ama şemaya birebir uyan JSON'larla).

Çalıştır (repo kökünden):  .venv/bin/python -m pytest \
  docs/audits/2026-10-10-prereg-exits/test_analyze_prereg.py -q -p no:cacheprovider
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
import math
import shutil
import statistics
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True
import analyze_prereg as ap  # noqa: E402

REAL_JSON = Path("/home/user/crypto-trading-bot/logs/backtest_20261010_034322.json")
REAL_CACHE = Path("/home/user/crypto-trading-bot/data/klines_cache")
LIB = HERE / "lib.sh"
BH_CWD = ap.DEFAULT_BH_CWD

LABELS = {"V1": "FLAT", "V2": "DOWN", "V3": "UP", "V4": "FLAT",
          "V5": "UP", "V6": "FLAT", "V7": "DOWN", "V8": "DOWN"}


# --------------------------------------------------------------------------
# Sentetik, şemaya birebir uyan JSON üretimi
# --------------------------------------------------------------------------
TRADE_KEYS = ["strategy", "symbol", "direction", "entry_price", "entry_time", "exit_price",
              "exit_time", "quantity", "leverage", "margin_usdt", "pnl", "roi_pct",
              "exit_reason", "mae_pct", "mfe_pct", "duration_minutes", "regime",
              "signal_close_time", "legs"]
TOP_KEYS = ["generated_at", "params", "provenance", "overall", "by_strategy",
            "regime_breakdown", "missed_signals", "permutation", "trades"]


def ms(d: str, hour: int = 12) -> int:
    x = dt.date.fromisoformat(d)
    return int(dt.datetime(x.year, x.month, x.day, hour, tzinfo=dt.timezone.utc).timestamp() * 1000)


def mk_doc(pnls, exit_times, bar_dd=100.0):
    n = len(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gp, gl = sum(wins), abs(sum(losses))
    pf = (gp / gl) if gl > 0 else (float("inf") if gp > 0 else 0.0)
    ov = {"trades": n, "wins": len(wins), "winrate": (len(wins) / n * 100.0) if n else 0.0,
          "total_pnl": sum(pnls), "avg_roi": 0.0,
          # backtest._strict_json_value: sonsuz PF -> null
          "profit_factor": None if pf == float("inf") else pf,
          "max_consec_losses": 0, "max_drawdown": bar_dd, "avg_duration_min": 1.0,
          "avg_mae": 0.0, "avg_mfe": 0.0, "bar_max_drawdown": bar_dd,
          "bar_max_drawdown_at": None, "bar_equity_points": 10}
    trades = [{"strategy": "C", "symbol": "BTCUSDT", "direction": "LONG", "entry_price": 1.0,
               "entry_time": et - 60000, "exit_price": 1.0, "exit_time": et, "quantity": 1.0,
               "leverage": 20, "margin_usdt": 100.0, "pnl": p, "roi_pct": 0.0,
               "exit_reason": "TRAIL", "mae_pct": 0.0, "mfe_pct": 0.0, "duration_minutes": 1.0,
               "regime": "RANGE", "signal_close_time": et - 61000, "legs": []}
              for p, et in zip(pnls, exit_times)]
    return {"generated_at": "x", "params": {"days": 21}, "provenance": {},
            "overall": ov, "by_strategy": {"C": ov}, "regime_breakdown": {},
            "missed_signals": {}, "permutation": None, "trades": trades}


def gen_pnls(n, pf, loss=10.0, win_frac=0.7):
    if pf == 0:
        return [-loss] * n
    k = int(n * win_frac)
    m = n - k
    a = pf * m * loss / k
    return [a] * k + [-loss] * m


def times_in_window(start, n):
    s = dt.date.fromisoformat(start)
    return [ms((s + dt.timedelta(days=i % 21)).isoformat(), 6 + (i % 10)) for i in range(n)]


def _windows():
    win, _ = ap.parse_lib(LIB)
    return win


def write_run(root, variant, window, pnls, bar_dd=100.0, sfx="", mutate=None):
    win = _windows()
    doc = mk_doc(pnls, times_in_window(win[window][0], len(pnls)), bar_dd)
    if mutate:
        mutate(doc)
    p = ap.run_path(root, variant, window, sfx)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc))


def mk_root(tmp_path):
    root = tmp_path / "prereg"
    (root / "runs").mkdir(parents=True)
    shutil.copy(LIB, root / "lib.sh")
    win = _windows()
    fz = {"windows": {}}
    for w, (s, e) in win.items():
        fz["windows"][w] = {"start": s, "end": e, "label": LABELS.get(w, "FLAT"),
                            "shock": False, "return_pct": 0.0,
                            "max_abs_daily_return_pct": 1.0, "max_abs_daily_date": s}
    (root / "windows_frozen.json").write_text(json.dumps(fz))
    return root


def write_dev(root, variant, pf=1.5, n=130):
    for w in ap.DEV_WINDOWS:
        write_run(root, variant, w, gen_pnls(n, pf))


def write_val(root, variant, pfs, n=80, dd=300.0, only=None, sfx=""):
    for w, pf in zip(ap.VAL_WINDOWS, pfs):
        if only is not None and w not in only:
            continue
        write_run(root, variant, w, gen_pnls(n, pf), dd, sfx=sfx)


ALL_BUT = lambda keep: {v: "test" for v in ap.FAMILY if v not in keep}  # noqa: E731


# --------------------------------------------------------------------------
# Şema doğrulaması (gerçek JSON'a karşı)
# --------------------------------------------------------------------------
@pytest.mark.skipif(not REAL_JSON.exists(), reason="gerçek backtest JSON yok")
def test_synthetic_schema_matches_real_json():
    real = json.loads(REAL_JSON.read_text())
    assert list(real.keys()) == TOP_KEYS
    doc = mk_doc([1.0, -1.0], [1, 2])
    assert list(doc.keys()) == TOP_KEYS
    assert list(real["trades"][0].keys()) == TRADE_KEYS
    assert list(doc["trades"][0].keys()) == TRADE_KEYS
    assert set(doc["overall"]) <= set(real["overall"])
    for k in ("trades", "winrate", "total_pnl", "profit_factor", "bar_max_drawdown"):
        assert k in real["overall"]


@pytest.mark.skipif(not REAL_JSON.exists(), reason="gerçek backtest JSON yok")
def test_load_real_json():
    r = ap.Run.load(REAL_JSON)
    real = json.loads(REAL_JSON.read_text())
    assert r.n == 320 == real["overall"]["trades"]
    assert r.pf == pytest.approx(real["overall"]["profit_factor"])
    assert r.bar_dd == pytest.approx(real["overall"]["bar_max_drawdown"])
    assert r.pnl == pytest.approx(sum(t["pnl"] for t in real["trades"]))
    assert not r.warnings


# --------------------------------------------------------------------------
# Ölçüler
# --------------------------------------------------------------------------
def test_pooled_pf_is_not_mean_of_pfs():
    a = ap.Run("a", mk_doc([100.0, -50.0], [1, 2]))      # PF 2
    b = ap.Run("b", mk_doc([10.0, -100.0], [1, 2]))      # PF 0.1
    assert a.pf == pytest.approx(2.0) and b.pf == pytest.approx(0.1)
    assert ap.pooled_pf([a, b]) == pytest.approx(110.0 / 150.0)
    assert ap.pooled_pf([a, b]) != pytest.approx((2.0 + 0.1) / 2)


def test_median_pf_and_aggregate_and_calmar():
    pfs = [0.5, 0.8, 1.02, 1.2, 1.3, 1.5, 2.0, 3.0]
    runs = {}
    for w, pf in zip(ap.VAL_WINDOWS, pfs):
        runs[w] = ap.Run(w, mk_doc(gen_pnls(40, pf), list(range(40)), bar_dd=200.0))
    agg = ap.aggregate(runs, ap.VAL_WINDOWS)
    assert agg["pf_median"] == pytest.approx(statistics.median(pfs))
    assert agg["pf_median"] == pytest.approx(1.25)
    assert agg["pf_min"] == pytest.approx(0.5)
    assert agg["n_pf_gt1"] == 6                      
    assert agg["dd_sum"] == pytest.approx(1600.0)
    assert agg["calmar"] == pytest.approx(agg["pnl"] / 1600.0)
    assert agg["trades"] == 320


def test_calmar_edges():
    assert ap.calmar(100, 50) == 2
    assert ap.calmar(100, 0) == ap.INF
    assert ap.calmar(0, 0) == 0
    assert ap.calmar(-5, 0) == -ap.INF
    assert ap.calmar(-100, 50) == -2


def test_inf_clipping_and_null_pf():
    r = ap.Run("w", mk_doc([5.0, 7.0], [1, 2]))          # kayıp yok => JSON'da null
    assert r.pf_raw == ap.INF and r.pf == 5.0
    assert json.loads(json.dumps(mk_doc([5.0], [1])))["overall"]["profit_factor"] is None
    z = ap.Run("z", mk_doc([], []))                      # 0 işlem => PF 0.0, clip yok
    assert z.pf == 0.0 and z.n == 0
    loss = ap.Run("l", mk_doc([-1.0, -2.0], [1, 2]))
    assert loss.pf == 0.0
    # havuz PF ham sonsuz kalır (clip yalnız pencere PF'sine uygulanır) ama sonlu karışımda sonlu
    assert ap.pooled_pf([r]) == ap.INF
    assert ap.clip_pf(3.2) == 3.2 and ap.clip_pf(ap.INF) == 5.0
    # pencere PF'si clip'li olduğundan medyan sonlu
    agg = ap.aggregate({"w": r}, ["w"])
    assert agg["pf_median"] == 5.0


def test_pf_mismatch_warning():
    doc = mk_doc([10.0, -5.0], [1, 2])
    doc["overall"]["profit_factor"] = 7.0
    assert ap.Run("x", doc).warnings


def test_g1_threshold_proportional():
    assert ap.g1_threshold(8) == 6
    assert ap.g1_threshold(7) == 6
    assert ap.g1_threshold(6) == 5
    assert ap.g1_threshold(4) == 3            # 6/8 -> 3/4
    assert all(ap.g1_threshold(n) / n >= 0.75 for n in range(1, 9))


# --------------------------------------------------------------------------
# Bootstrap
# --------------------------------------------------------------------------
def test_bootstrap_deterministic_same_seed_same_p():
    daily = [3.0, -5.0, 0.0, 2.0, -1.0, 4.0, 0.0, -2.0, 1.0, 0.5] * 10
    p1 = ap.bootstrap_p(daily, n_boot=2000, seed=ap.BOOT_SEED)
    p2 = ap.bootstrap_p(list(daily), n_boot=2000, seed=ap.BOOT_SEED)
    assert p1 == p2
    ps = {ap.bootstrap_p(daily, n_boot=300, seed=s) for s in range(1, 8)}
    assert len(ps) > 1


def test_bootstrap_p_formula_and_extremes():
    n_boot = 500
    assert ap.bootstrap_p([5.0] * 50, n_boot=n_boot) == pytest.approx(1 / (n_boot + 1))
    assert ap.bootstrap_p([-5.0] * 50, n_boot=n_boot) == pytest.approx(1.0)
    assert ap.bootstrap_p([0.0] * 50, n_boot=n_boot) == pytest.approx(1.0)   # ≤ 0 sayılır
    assert ap.bootstrap_p([], n_boot=n_boot) == 1.0
    mix = [1.0, -1.0] * 40
    p = ap.bootstrap_p(mix, n_boot=4000)
    assert 0.3 < p < 0.8                     # sıfır ortalama: ~0.5 (≤0 sayımı)
    pos = [2.0, -1.0] * 40
    assert ap.bootstrap_p(pos, n_boot=4000) < 0.05


def test_bootstrap_default_constants():
    assert ap.BOOT_N == 20000 and ap.BOOT_SEED == 20261010 and ap.BH_ALPHA == 0.10
    assert len(ap.FAMILY) == 11


def test_daily_vector_168_days_zero_filled():
    root = Path("/nonexistent")
    win = _windows()
    fz = {"windows": {w: {"start": s, "end": e} for w, (s, e) in win.items()}}
    runs = {}
    for w in ap.VAL_WINDOWS:
        s = win[w][0]
        runs[w] = ap.Run(w, mk_doc([10.0, -4.0], [ms(s, 1), ms(s, 20)]))   # aynı gün 2 işlem
    vec = ap.daily_pnl_vector(runs, ap.VAL_WINDOWS, fz)
    assert len(vec) == 168
    assert sum(1 for x in vec if x != 0) == 8
    assert sum(vec) == pytest.approx(48.0)
    # pencere dışı çıkış günü kaybolmaz
    r2 = ap.Run("V1", mk_doc([9.0], [ms("2025-02-01")]))
    vec2 = ap.daily_pnl_vector({"V1": r2}, ["V1"], fz)
    assert len(vec2) == 22 and sum(vec2) == pytest.approx(9.0)


# --------------------------------------------------------------------------
# Kapılar
# --------------------------------------------------------------------------
GOOD = [1.6, 1.5, 1.4, 1.3, 1.5, 1.2, 1.4, 1.3]


def world(tmp_path, cand_pfs=GOOD, b0_pfs=(1.0,) * 8, cand_n=80, cand_dd=300.0,
          b0_dd=500.0, cand="E2"):
    root = mk_root(tmp_path)
    write_dev(root, "B0", 1.2, 130)
    write_val(root, "B0", b0_pfs, n=100, dd=b0_dd)
    write_dev(root, cand, 1.3, 130)
    write_val(root, cand, cand_pfs, n=cand_n, dd=cand_dd)
    return root


def gates_for(root, cand="E2"):
    prep = ap.prepare(root, root / "lib.sh", ALL_BUT({cand}))
    labels = {w: prep["frozen"]["windows"][w]["label"] for w in ap.VAL_WINDOWS}
    u = prep["usable"]
    b0 = ap.aggregate(prep["b0_runs"], u)
    agg = ap.aggregate(prep["var_runs"][cand], u)
    g = ap.evaluate_gates(agg, b0, labels, u, root, cand, None)
    return {x["id"]: x for x in g}, prep


def test_all_gates_pass_baseline_world(tmp_path):
    root = world(tmp_path)
    g, prep = gates_for(root)
    assert len(prep["usable"]) == 8
    for i in range(1, 9):
        assert g[f"G{i}"]["status"] is True, g[f"G{i}"]
    assert g["G9"]["status"] == "PENDING"       # fee/dly koşuları yok
    assert g["G10"]["status"] == "PENDING"      # bh_out yok


def test_g1_fails(tmp_path):
    root = world(tmp_path, cand_pfs=[1.6, 1.5, 0.95, 1.3, 0.9, 1.2, 0.99, 1.3])   # 3 pencere <1 => 5/8
    g, _ = gates_for(root)
    assert g["G1"]["status"] is False and "6/8" in g["G1"]["name"]
    # tam 6/8 geçer
    root2 = world(tmp_path / "b", cand_pfs=[1.6, 1.5, 0.95, 1.3, 1.01, 1.2, 0.99, 1.3])
    g2, _ = gates_for(root2)
    assert g2["G1"]["status"] is True


def test_g2_g8_pooled_threshold(tmp_path):
    pfs = [1.08] * 8
    root = world(tmp_path, cand_pfs=pfs)
    g, _ = gates_for(root)
    assert g["G2"]["status"] is False            # 1.08 < 1.15
    assert g["G3"]["status"] is False            # medyan 1.08 < 1.10
    root2 = world(tmp_path / "x", cand_pfs=GOOD, b0_pfs=[1.45] * 8)
    g2, _ = gates_for(root2)
    assert g2["G8"]["status"] is False           # B0 + 0.05 aşılmıyor


def test_g3_median_g4_worst(tmp_path):
    root = world(tmp_path, cand_pfs=[1.5, 1.5, 1.5, 1.05, 1.05, 1.05, 1.05, 1.05])
    g, _ = gates_for(root)
    assert g["G3"]["status"] is False and g["G2"]["status"] is True
    root2 = world(tmp_path / "w", cand_pfs=[2.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0, 0.65])
    g2, _ = gates_for(root2)
    assert g2["G4"]["status"] is False and g2["G1"]["status"] is True


def test_g5_window_dd_and_calmar(tmp_path):
    root = world(tmp_path, cand_dd=2500.0)
    g, _ = gates_for(root)
    assert g["G5"]["status"] is False and "aşan" in g["G5"]["detail"]
    # DD kapısı geçer ama Calmar B0'dan kötü
    root2 = world(tmp_path / "c", cand_dd=1900.0, b0_dd=10.0, b0_pfs=(1.3,) * 8)
    g2, _ = gates_for(root2)
    assert g2["G5"]["status"] is False and "Calmar" in g2["G5"]["detail"]


def test_g6_trade_count(tmp_path):
    root = world(tmp_path, cand_n=40)            # 320 işlem < 400
    g, _ = gates_for(root)
    assert g["G6"]["status"] is False
    root2 = world(tmp_path / "d", cand_n=55)     # 440 ≥ 400 ama B0 800*0.6=480 > 440
    g2, _ = gates_for(root2)
    assert g2["G6"]["status"] is False
    root3 = world(tmp_path / "e", cand_n=70)     # 560 ≥ 480
    g3, _ = gates_for(root3)
    assert g3["G6"]["status"] is True


def test_g7_uses_frozen_labels(tmp_path):
    # V2,V7,V8 = DOWN (donmuş etiket). DOWN pencerelerde PF 0.5 -> grup PF < 0.9
    pfs = [1.6, 0.5, 1.6, 1.6, 1.6, 1.6, 0.5, 0.5]
    root = world(tmp_path, cand_pfs=pfs)
    g, _ = gates_for(root)
    assert g["G7"]["status"] is False and "DOWN" in g["G7"]["detail"]
    # etiketi değiştir: aynı veri, DOWN grubu artık boş/başka -> sonuç değişir
    fz = json.loads((root / "windows_frozen.json").read_text())
    for w in ("V2", "V7", "V8"):
        fz["windows"][w]["label"] = "FLAT"
    (root / "windows_frozen.json").write_text(json.dumps(fz))
    g2, _ = gates_for(root)
    assert "DOWN: pencere yok" in g2["G7"]["detail"]
    assert g2["G7"]["status"] is True            # aynı veri, etiket farkı => sonuç farkı (FLAT havuz PF ~1.05)


def test_g9_stress_runs(tmp_path):
    root = world(tmp_path)
    write_val(root, "E2", [1.2] * 8, n=80, sfx="_fee")
    write_val(root, "E2", [1.2] * 8, n=80, sfx="_dly")
    g, _ = gates_for(root)
    assert g["G9"]["status"] is True
    write_val(root, "E2", [1.0] * 8, n=80, sfx="_fee")
    g, _ = gates_for(root)
    assert g["G9"]["status"] is False
    # G1-G8 geçmediyse G9 uygulanmaz
    root2 = world(tmp_path / "z", cand_pfs=[1.0] * 8)
    g2, _ = gates_for(root2)
    assert g2["G9"]["status"] == "NA"


def test_overall_state():
    mk = lambda *s: [{"status": x} for x in s]  # noqa: E731
    assert ap.overall_state(mk(True, True)) == "GECTI"
    assert ap.overall_state(mk(True, "PENDING")) == "BEKLIYOR"
    assert ap.overall_state(mk(True, False, "PENDING")) == "KALDI"
    assert ap.overall_state(mk(True, "NA")) == "KALDI"


# --------------------------------------------------------------------------
# Eksik pencere orantılama / geçersizlik
# --------------------------------------------------------------------------
def test_missing_windows_proportional_and_invalid(tmp_path):
    root = mk_root(tmp_path)
    write_dev(root, "B0", 1.2, 130)
    write_dev(root, "E2", 1.3, 130)
    keep6 = set(ap.VAL_WINDOWS[:6])
    write_val(root, "B0", [1.0] * 8, n=100, only=keep6)
    write_val(root, "E2", GOOD, n=80, only=keep6)
    prep = ap.prepare(root, root / "lib.sh", ALL_BUT({"E2"}))
    assert prep["valid"] and len(prep["usable"]) == 6 and prep["missing"] == ["V7", "V8"]
    assert ap.g1_threshold(6) == 5
    # B0 bir pencerede, E2 başka pencerede eksik: kesişim kullanılır
    root2 = mk_root(tmp_path / "m")
    write_dev(root2, "B0"); write_dev(root2, "E2")
    write_val(root2, "B0", [1.0] * 8, n=100, only=set(ap.VAL_WINDOWS) - {"V1"})
    write_val(root2, "E2", GOOD, n=80, only=set(ap.VAL_WINDOWS) - {"V2"})
    p2 = ap.prepare(root2, root2 / "lib.sh", ALL_BUT({"E2"}))
    assert p2["usable"] == ["V3", "V4", "V5", "V6", "V7", "V8"] and p2["valid"]
    # 5 pencere -> geçersiz
    root3 = mk_root(tmp_path / "n")
    write_dev(root3, "B0"); write_dev(root3, "E2")
    keep5 = set(ap.VAL_WINDOWS[:5])
    write_val(root3, "B0", [1.0] * 8, n=100, only=keep5)
    write_val(root3, "E2", GOOD, n=80, only=keep5)
    p3 = ap.prepare(root3, root3 / "lib.sh", ALL_BUT({"E2"}))
    assert not p3["valid"]
    assert ap.main(["--root", str(root3), "decide", "--not-coded",
                    ",".join(v for v in ap.FAMILY if v != "E2")]) == 0
    assert "DENEY GEÇERSİZ" in (root3 / "DECISION.md").read_text()


# --------------------------------------------------------------------------
# DEV elemesi ve aile sınıflandırması
# --------------------------------------------------------------------------
def test_dev_elimination_rules(tmp_path):
    root = mk_root(tmp_path)
    write_dev(root, "B0", 1.5, 130)                       # 390 işlem => eşik 195
    write_dev(root, "E1", 0.9, 130)                       # PF < 1.0
    write_dev(root, "E2", 1.4, 60)                        # 180 işlem < 195
    write_dev(root, "E3", 1.4, 130)                       # geçer
    scr = ap.dev_screen(root, ["E1", "E2", "E3", "E4"])
    assert scr["b0_dev_trades"] == 390 and scr["min_trades"] == 195
    v = scr["variants"]
    assert v["E1"]["status"] == "ELENDI" and "PF" in v["E1"]["reasons"][0]
    assert v["E2"]["status"] == "ELENDI" and "işlem" in v["E2"]["reasons"][0]
    assert v["E3"]["status"] == "GECTI"
    assert v["E4"]["status"] == "EKSIK"
    with pytest.raises(ap.PreregError):
        ap.classify_family(root, {x: "t" for x in ap.FAMILY if x not in ("E4",)})
    info = ap.classify_family(root, {x: "t" for x in ap.FAMILY if x not in ("E1", "E3")})
    assert info["E1"]["status"] == ap.ST_DEV and info["E3"]["status"] == ap.ST_BOOT
    assert info["C1"]["status"] == ap.ST_NOCODE


def test_c2_insufficient_coverage(tmp_path):
    root = mk_root(tmp_path)
    write_dev(root, "B0"); write_dev(root, "C2")
    write_val(root, "B0", [1.0] * 8, n=100)

    def tag(frac):
        def mutate(doc):
            for i, t in enumerate(doc["trades"]):
                t["tp_source"] = "bb" if i < frac * len(doc["trades"]) else "roi"
        return mutate
    for w, pf in zip(ap.VAL_WINDOWS, GOOD):
        write_run(root, "C2", w, gen_pnls(80, pf), 300.0, mutate=tag(0.3))
    prep = ap.prepare(root, root / "lib.sh", ALL_BUT({"C2"}))
    assert prep["info"]["C2"]["status"] == ap.ST_COVER and prep["cands"] == []
    for w, pf in zip(ap.VAL_WINDOWS, GOOD):
        write_run(root, "C2", w, gen_pnls(80, pf), 300.0, mutate=tag(0.5))
    prep = ap.prepare(root, root / "lib.sh", ALL_BUT({"C2"}))
    assert prep["info"]["C2"]["status"] == ap.ST_BOOT
    assert prep["info"]["C2"]["coverage_bb"] == pytest.approx(0.5, abs=0.02)
    # tp_source yoksa sessiz geçmez
    write_run(root, "C2", "V1", gen_pnls(80, 1.5), 300.0)
    with pytest.raises(ap.PreregError):
        ap.prepare(root, root / "lib.sh", ALL_BUT({"C2"}))


# --------------------------------------------------------------------------
# Uçtan uca: pvals (BH CLI) + decide
# --------------------------------------------------------------------------
needs_bh = pytest.mark.skipif(not (BH_CWD / "src/strategies/scalper/multitest.py").exists(),
                              reason="multitest.py yok")


def e2e_world(tmp_path, cand_pfs=GOOD, with_stress=True):
    root = world(tmp_path, cand_pfs=cand_pfs)
    if with_stress:
        write_val(root, "E2", [1.2] * 8, n=80, sfx="_fee")
        write_val(root, "E2", [1.2] * 8, n=80, sfx="_dly")
    (root / "PLAN.md").write_text("plan")
    import hashlib
    (root / "PLAN.sha256").write_text(hashlib.sha256(b"plan").hexdigest() + "  PLAN.md\n")
    return root


NC = ",".join(v for v in ap.FAMILY if v != "E2")


@needs_bh
def test_pvals_and_decide_winner(tmp_path, monkeypatch):
    root = e2e_world(tmp_path)
    rc = ap.main(["--root", str(root), "pvals", "--not-coded", NC, "--n-boot", "2000"])
    assert rc == 0
    pv = json.loads((root / "pvals.json").read_text())
    assert len(pv) == 11
    by = {r["name"]: r for r in pv}
    assert by["E2"]["status"] == "BOOTSTRAP" and by["E2"]["p_value"] < 0.01
    assert all(by[v]["p_value"] == 1.0 and by[v]["status"] == "KODLANMADI"
               for v in ap.FAMILY if v != "E2")
    bh = json.loads((root / "bh_out.json").read_text())
    assert bh["tests"] == 11 and bh["alpha"] == 0.1 and bh["effective_tests"] == 6
    e2 = next(r for r in bh["rows"] if r["name"] == "E2")
    assert e2["rejected"] is True
    # tekrar aynı tohum => aynı p
    first = by["E2"]["p_value"]
    assert ap.main(["--root", str(root), "pvals", "--not-coded", NC, "--n-boot", "2000"]) == 0
    assert next(r for r in json.loads((root / "pvals.json").read_text())
                if r["name"] == "E2")["p_value"] == first
    # decide
    assert ap.main(["--root", str(root), "decide", "--not-coded", NC]) == 0
    md = (root / "DECISION.md").read_text()
    assert "**KAZANAN: E2**" in md
    for g in range(1, 11):
        assert f"G{g} [DOĞRU]" in md
    assert "UYUŞUYOR" in md                       # PLAN kilidi
    assert (root / "results.csv").exists()
    gj = json.loads((root / "gates.json").read_text())
    assert gj["decision"] == "WINNER" and gj["winner"] == "E2"
    rows = list(__import__("csv").DictReader(open(root / "results.csv")))
    assert {"variant", "window", "trades", "winrate", "total_pnl", "pf", "bar_max_drawdown"} <= set(rows[0])


@needs_bh
def test_decide_no_winner_sentence(tmp_path):
    root = e2e_world(tmp_path, cand_pfs=[0.95, 1.05, 0.9, 1.0, 1.1, 0.8, 0.85, 1.0])
    assert ap.main(["--root", str(root), "pvals", "--not-coded", NC, "--n-boot", "1000"]) == 0
    assert ap.main(["--root", str(root), "decide", "--not-coded", NC]) == 0
    md = (root / "DECISION.md").read_text()
    assert ap.NO_WINNER_SENTENCE in md
    assert "KAZANAN:" not in md
    assert json.loads((root / "gates.json").read_text())["decision"] == "NO_WINNER"


@needs_bh
def test_decide_pending_when_no_stress_runs(tmp_path):
    root = e2e_world(tmp_path, with_stress=False)
    assert ap.main(["--root", str(root), "pvals", "--not-coded", NC, "--n-boot", "1000"]) == 0
    assert ap.main(["--root", str(root), "decide", "--not-coded", NC]) == 0
    md = (root / "DECISION.md").read_text()
    assert "**BEKLİYOR**" in md and ap.NO_WINNER_SENTENCE not in md
    assert json.loads((root / "gates.json").read_text())["decision"] == "PENDING"


@needs_bh
def test_stale_bh_detected(tmp_path):
    root = e2e_world(tmp_path)
    assert ap.main(["--root", str(root), "pvals", "--not-coded", NC, "--n-boot", "500"]) == 0
    pv = json.loads((root / "pvals.json").read_text())
    pv[1]["p_value"] = 0.5
    (root / "pvals.json").write_text(json.dumps(pv))
    assert ap.main(["--root", str(root), "decide", "--not-coded", NC]) == 2


def test_pvals_refuses_missing_validation_runs(tmp_path):
    root = mk_root(tmp_path)
    write_dev(root, "B0"); write_dev(root, "E2")
    write_val(root, "B0", [1.0] * 8, n=100)
    with pytest.raises(ap.PreregError):
        ap.prepare(root, root / "lib.sh", ALL_BUT({"E2"}))


# --------------------------------------------------------------------------
# freeze
# --------------------------------------------------------------------------
def mk_1d_cache(tmp_path, start, end, closes):
    """closes: günlük kapanış listesi; açılış = önceki kapanış (ilki 100)."""
    cache = tmp_path / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    s_ms, e_ms = ap.date_ms(start), ap.date_ms(end)
    rows, prev = [], 100.0
    for i, c in enumerate(closes):
        t = s_ms + i * 86_400_000
        rows.append({"open_time": t, "open": prev, "high": max(prev, c), "low": min(prev, c),
                     "close": c, "volume": 1.0, "close_time": t + 86_399_999})
        prev = c
    with gzip.open(cache / f"BTCUSDT_1d_{s_ms}_{e_ms}.json.gz", "wt") as f:
        json.dump(rows, f)
    return cache


def test_freeze_labels_and_shock(tmp_path):
    n = 21
    up = [100 * (1 + 0.06 * (i + 1) / n) for i in range(n)]       # +6%
    c = mk_1d_cache(tmp_path, "2025-01-06", "2025-01-27", up)
    r = ap.freeze_window("V1", "2025-01-06", "2025-01-27", c)
    assert r["label"] == "UP" and r["shock"] is False and r["days"] == 21
    assert r["return_pct"] == pytest.approx(6.0)
    down = [100 * (1 - 0.05 * (i + 1) / n) for i in range(n)]     # -5% tam sınır (≤ -5)
    down[-1] = 95.0
    c2 = mk_1d_cache(tmp_path / "a", "2025-01-06", "2025-01-27", down)
    assert ap.freeze_window("V1", "2025-01-06", "2025-01-27", c2)["label"] == "DOWN"
    flat = [100 + (1 if i % 2 else -1) for i in range(n)]
    c3 = mk_1d_cache(tmp_path / "b", "2025-01-06", "2025-01-27", flat)
    f3 = ap.freeze_window("V1", "2025-01-06", "2025-01-27", c3)
    assert f3["label"] == "FLAT" and f3["shock"] is False
    shock = list(flat); shock[10] = shock[9] * 0.93               # -7% gün
    c4 = mk_1d_cache(tmp_path / "c", "2025-01-06", "2025-01-27", shock)
    f4 = ap.freeze_window("V1", "2025-01-06", "2025-01-27", c4)
    assert f4["shock"] is True and f4["max_abs_daily_return_pct"] >= 6.0


def test_freeze_missing_cache_and_gap_errors(tmp_path):
    empty = tmp_path / "e"; empty.mkdir()
    with pytest.raises(ap.PreregError):
        ap.freeze_window("V1", "2025-01-06", "2025-01-27", empty)
    c = mk_1d_cache(tmp_path / "g", "2025-01-06", "2025-01-27", [100.0] * 21)
    f = next(c.glob("*.gz"))
    rows = json.load(gzip.open(f, "rt"))
    del rows[5]
    with gzip.open(f, "wt") as fh:
        json.dump(rows, fh)
    with pytest.raises(ap.PreregError):
        ap.freeze_window("V1", "2025-01-06", "2025-01-27", c)


def test_freeze_cli_all_or_nothing_and_no_overwrite(tmp_path):
    root = tmp_path / "prereg"; root.mkdir()
    shutil.copy(LIB, root / "lib.sh")
    empty = tmp_path / "cache"; empty.mkdir()
    assert ap.main(["--root", str(root), "freeze", "--cache-dir", str(empty)]) == 3
    assert not (root / "windows_frozen.json").exists()
    (root / "windows_frozen.json").write_text("{}")
    assert ap.main(["--root", str(root), "freeze", "--cache-dir", str(empty)]) == 2


@pytest.mark.skipif(not REAL_CACHE.exists(), reason="gerçek önbellek yok")
def test_freeze_real_dev_window():
    r = ap.freeze_window("AYI", "2026-01-23", "2026-02-13", REAL_CACHE)
    assert r["days"] == 21 and r["label"] in ("UP", "DOWN", "FLAT")
    assert r["cache_file"].startswith("BTCUSDT_1d_")


# --------------------------------------------------------------------------
# lib.sh ayrıştırma ve SON SINAV
# --------------------------------------------------------------------------
def test_parse_lib_matches_plan_tables():
    win, var = ap.parse_lib(LIB)
    for w in ap.DEV_WINDOWS + ap.VAL_WINDOWS + ap.EXTRA_WINDOWS:
        assert w in win
    assert win["V7"] == ("2025-10-06", "2025-10-27")
    assert all((dt.date.fromisoformat(e) - dt.date.fromisoformat(s)).days == 21
               for w, (s, e) in win.items() if w in ap.VAL_WINDOWS)
    # C1/C2 kod varyantları lib.sh VAR tablosuna (wt-code ile koşulmak üzere) eklendi; C3 M0 kapısından geçmedi.
    assert set(var) == {"B0", *ap.E_VARIANTS, "C1", "C2"}
    assert "C3" not in var
    assert var["E7"][1] == "SCALPER_DYNAMIC_LEVERAGE=false"


def _gates(root, decision="WINNER", winner="E2"):
    (root / "gates.json").write_text(json.dumps({"decision": decision, "winner": winner}))


def test_final_exam(tmp_path):
    root = mk_root(tmp_path)
    _gates(root)
    write_run(root, "B0", "CANLI", gen_pnls(100, 0.9), 2000.0)
    write_run(root, "E2", "CANLI", gen_pnls(150, 1.2), 800.0)
    assert ap.main(["--root", str(root), "final", "E2"]) == 0
    assert "GEÇTİ" in (root / "FINAL.md").read_text()
    root2 = mk_root(tmp_path / "b")
    _gates(root2)
    write_run(root2, "B0", "CANLI", gen_pnls(100, 0.9), 2000.0)
    write_run(root2, "E2", "CANLI", gen_pnls(150, 0.97), 800.0)
    assert ap.main(["--root", str(root2), "final", "E2"]) == 0
    assert "KALDI" in (root2 / "FINAL.md").read_text()


def test_final_is_single_shot_and_winner_only(tmp_path):
    """Düzeltme (analiz denetimi #3): SON SINAV tek atış, yalnız `decide` kazananıyla."""
    root = mk_root(tmp_path)
    write_run(root, "B0", "CANLI", gen_pnls(100, 0.9), 2000.0)
    write_run(root, "E2", "CANLI", gen_pnls(150, 1.2), 800.0)
    assert ap.main(["--root", str(root), "final", "E2"]) == 2          # gates.json yok
    _gates(root, decision="NO_WINNER", winner=None)
    assert ap.main(["--root", str(root), "final", "E2"]) == 2          # kazanan yok
    _gates(root, decision="WINNER", winner="E4")
    assert ap.main(["--root", str(root), "final", "E2"]) == 2          # başka kazanan
    _gates(root)
    assert ap.main(["--root", str(root), "final", "E2"]) == 0
    first = (root / "FINAL.md").read_text()
    write_run(root, "E2", "CANLI", gen_pnls(150, 0.5), 800.0)
    assert ap.main(["--root", str(root), "final", "E2"]) == 2          # ikinci atış reddedilir
    assert (root / "FINAL.md").read_text() == first


def test_missing_bar_dd_is_inf_not_zero(tmp_path):
    """Düzeltme (analiz denetimi #2): eksik bar_max_drawdown sessizce 0 olmaz."""
    root = mk_root(tmp_path)
    write_run(root, "E2", "V1", gen_pnls(80, 1.5), 300.0,
              mutate=lambda d: d["overall"].pop("bar_max_drawdown"))
    r = ap.Run.load(ap.run_path(root, "E2", "V1"))
    assert r.bar_dd == ap.INF
    assert any("bar_max_drawdown YOK" in w for w in r.warnings)


@needs_bh
def test_decide_pending_beats_premature_winner(tmp_path):
    """Düzeltme (analiz denetimi #1): G9'u eksik daha yüksek Calmar'lı bir aday BEKLERKEN kazanan ilan edilmez."""
    root = e2e_world(tmp_path)                     # E2: G1–G10 geçer (stress var)
    write_dev(root, "E4", 1.3, 130)
    write_val(root, "E4", [2.6, 2.5, 2.4, 2.3, 2.5, 2.2, 2.4, 2.3], n=80, dd=150.0)   # stress YOK
    nc = ",".join(v for v in ap.FAMILY if v not in ("E2", "E4"))
    assert ap.main(["--root", str(root), "pvals", "--not-coded", nc, "--n-boot", "1000"]) == 0
    assert ap.main(["--root", str(root), "decide", "--not-coded", nc]) == 0
    gj = json.loads((root / "gates.json").read_text())
    assert gj["decision"] == "PENDING" and gj["winner"] is None
    md = (root / "DECISION.md").read_text()
    assert "**BEKLİYOR**" in md and "KAZANAN:" not in md and "E2" in md
