"""scripts/autoresearch.py P2-v2 karar kurali — saf fonksiyonlar, sentetik raporlar,
AG/backtest YOK. Gunluk PnL serileri montecarlo.paired_ab_test'e (gercek kod)
gider; hicbir sey mock'lanmaz."""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

import autoresearch as ar  # noqa: E402

PER_DAY = 5
BASE_DAY = [4.0, 3.0, -2.0, 2.0, -1.0]   # gunluk net +6, brut kar 9, brut zarar 3


def _days(start: str, end: str) -> List[date]:
    d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    return [d0 + timedelta(days=i) for i in range((d1 - d0).days + 1)]


def _ms(d: date, k: int) -> int:
    return int(datetime(d.year, d.month, d.day, 1 + k, tzinfo=timezone.utc).timestamp() * 1000)


def _metrics(trade_pnls, bar_dd=None) -> dict:
    pnls = [p for _, p in trade_pnls]
    gp = sum(p for p in pnls if p > 0)
    gl = abs(sum(p for p in pnls if p < 0))
    m = {
        "trades": len(pnls), "winrate": 50.0, "total_pnl": sum(pnls),
        "profit_factor": gp / gl if gl else float("inf"), "max_drawdown": 1.0,
        "trade_pnls": [list(x) for x in trade_pnls],
    }
    if bar_dd is not None:
        m["bar_max_drawdown"] = bar_dd
    return m


def build(per_trade: Callable[[str, date, int], float], n_per_day: Dict[str, int] = None,
          bar_dd: Dict[str, float] = None) -> Dict[str, dict]:
    out = {}
    for wid, s, e in ar.WINDOWS:
        n = (n_per_day or {}).get(wid, PER_DAY)
        tr = []
        for d in _days(s, e):
            for k in range(n):
                tr.append((_ms(d, k), per_trade(wid, d, k)))
        out[wid] = _metrics(tr, (bar_dd or {}).get(wid, 10.0))
    return out


def base_fn(wid, d, k):
    return BASE_DAY[k % 5]


BASELINE = build(base_fn)


def shifted(delta_fn):
    return build(lambda w, d, k: base_fn(w, d, k) + delta_fn(w, d, k))


def run(candidates: Dict[str, Dict[str, dict]], stress=None, baseline=BASELINE):
    evals = {cid: ar.p2v2_evaluate(baseline, v) for cid, v in candidates.items()}
    return ar.p2v2_stage1(evals)


def test_clear_consistent_improvement_passes():
    cand = shifted(lambda w, d, k: 2.0)
    res = run({"X": cand})["X"]
    assert res["label"] == "ADAY"
    assert res["needs_stress"] is True
    assert res["q"] <= 0.10
    # delta = 2 * (105 + 100 + 70) = 550 (21+20+14 gun x 5 islem). Eski beklenti 580 idi:
    # takvim her pencerenin HARIC bitis gunune sahte bir gun daha ekliyordu (22+21+15).
    assert res["delta"] == 550.0
    assert res["score"] > 0


def test_epsilon_improvement_rejected_but_old_rule_passes():
    # Gunluk fark +3,-2,+1,-1,+2,-3,... (gurultu), net kucuk pozitif.
    pattern = [3.0, -2.0, 1.0, -1.0, 2.0, -3.0, 1.0]

    def fn(w, d, k):
        if k != 0:
            return 0.0
        return pattern[(d.toordinal()) % len(pattern)]

    cand = shifted(fn)
    res = run({"EPS": cand})["EPS"]
    assert res["delta"] > 0
    assert res["label"] == "REDDEDILDI"
    assert "anlamli degil" in res["reason"] or "q" in res["reason"]
    # Eski kural bunu gecirir (AYI PF >= 1.1) ve gorunur sekilde raporlanir.
    assert res["old_decision"] == "ADAY"


def test_candidate_wrecking_one_window_rejected():
    # AYI ve YATAY'da +2/islem, BOGA'da -0.5/islem: toplam delta pozitif.
    cand = shifted(lambda w, d, k: -0.5 if w == "BOGA" else 2.0)
    res = run({"W": cand})["W"]
    assert res["checks"]["significance"] is True  # tek basina anlamli
    assert res["checks"]["no_harm"] is False
    assert res["label"] == "REDDEDILDI"
    assert "BOGA" in res["reason"]
    # BOGA: delta = -0.5*75 = -37.5 ; scale = max(90, 0.25*180) = 90 ; esik -18
    assert res["window_harm"]["BOGA"]["delta"] == -37.5
    assert res["window_harm"]["BOGA"]["scale"] == 90.0


def test_harm_boundary_exactly_20pct_is_allowed():
    # BOGA delta = -18 -> tam esik (scale 90 * 0.20): zarar sayilmaz.
    per = -18.0 / 75.0
    cand = shifted(lambda w, d, k: per if w == "BOGA" else 2.0)
    res = run({"B": cand})["B"]
    assert res["checks"]["no_harm"] is True


def test_insufficient_sample_distinct_label():
    cand = build(lambda w, d, k: base_fn(w, d, k) + 2.0, n_per_day={"BOGA": 3})  # 45 islem
    res = run({"S": cand})["S"]
    assert res["label"] == "YETERSIZ-ORNEK"
    assert res["label"] != "REDDEDILDI"
    assert "BOGA" in res["reason"]


def test_insufficient_baseline_sample_also_flagged():
    low_base = build(base_fn, n_per_day={"AYI": 2})
    cand = build(lambda w, d, k: base_fn(w, d, k) + 2.0)
    ev = ar.p2v2_evaluate(low_base, cand)
    assert ar.p2v2_stage1({"S": ev})["S"]["label"] == "YETERSIZ-ORNEK"


def test_bh_single_p004_among_20_does_not_pass():
    ps = [0.04] + [0.9] * 19
    q = ar.p2v2_bh_qvalues(ps)
    assert q[0] == 0.04 * 20 / 1 or abs(q[0] - 0.8) < 1e-9
    assert all(x > 0.10 for x in q)
    # tek aday olsa ayni p gecerdi
    assert ar.p2v2_bh_qvalues([0.04])[0] == 0.04


def test_bh_applied_across_sweep_candidates_in_stage1():
    # 1 gercek iyilesme + 19 ayni (delta 0, p yuksek) aday: iyilesen q'si p*20 olur
    # ama p ~ 1/5001 oldugundan hala gecer; q'nun aileye gore buyudugunu dogrula.
    good = shifted(lambda w, d, k: 2.0)
    cands = {"G": good}
    for i in range(19):
        cands[f"N{i}"] = BASELINE
    res = run(cands)
    assert res["G"]["q"] > res["G"]["p"]            # m=20 ile carpildi
    assert abs(res["G"]["q"] - res["G"]["p"] * 20) < 1e-6
    assert res["G"]["label"] == "ADAY"
    assert res["N0"]["label"] == "REDDEDILDI"


def test_drawdown_worse_than_20pct_rejected():
    dd_ok = build(lambda w, d, k: base_fn(w, d, k) + 2.0, bar_dd={"AYI": 10, "YATAY": 10, "BOGA": 12.0})
    dd_bad = build(lambda w, d, k: base_fn(w, d, k) + 2.0, bar_dd={"AYI": 10, "YATAY": 10, "BOGA": 13.0})
    # taban havuz DD = 30 ; sinir 36 ; ok = 32 gecer, bad = 33 gecer? 33<=36 -> gecer
    assert run({"A": dd_ok})["A"]["checks"]["drawdown"] is True
    dd_bad2 = build(lambda w, d, k: base_fn(w, d, k) + 2.0, bar_dd={"AYI": 20, "YATAY": 10, "BOGA": 10.0})
    r = run({"A": dd_bad2})["A"]   # 40 > 36
    assert r["checks"]["drawdown"] is False
    assert r["label"] == "REDDEDILDI"
    assert "DD" in r["reason"]


def test_drawdown_falls_back_to_trade_level_without_bar_dd():
    base = build(base_fn)
    for m in base.values():
        m.pop("bar_max_drawdown", None)
    cand = build(lambda w, d, k: base_fn(w, d, k) + 2.0)
    for m in cand.values():
        m.pop("bar_max_drawdown", None)
    ev = ar.p2v2_evaluate(base, cand)
    assert ev["dd_source"] == "trade"
    assert ev["base_dd"] > 0


def test_stress_failure_demotes():
    cand = shifted(lambda w, d, k: 2.0)
    res = run({"X": cand})["X"]
    assert res["label"] == "ADAY"
    base_stress = {"maliyet2x": BASELINE, "gecikme1": BASELINE}
    good = {"maliyet2x": cand, "gecikme1": cand}
    assert ar.p2v2_apply_stress(dict(res), base_stress, good)["label"] == "ADAY"
    # gecikme stresinde aday tabanin altina duser
    worse = shifted(lambda w, d, k: -1.0)
    bad = {"maliyet2x": cand, "gecikme1": worse}
    out = ar.p2v2_apply_stress(dict(res), base_stress, bad)
    assert out["label"] == "REDDEDILDI"
    assert "gecikme1" in out["reason"]
    # eksik stres sonucu da demote eder (fail-closed)
    out2 = ar.p2v2_apply_stress(dict(res), base_stress, {"maliyet2x": cand})
    assert out2["label"] == "REDDEDILDI"


def test_old_rule_still_reported_next_to_new_verdict():
    cand = shifted(lambda w, d, k: 2.0)
    res = run({"X": cand})["X"]
    old = ar.decide(BASELINE, cand)
    assert (res["old_decision"], res["old_reason"], res["old_score"]) == old


def test_missing_trade_series_is_hata():
    base = {w: {k: v for k, v in m.items() if k != "trade_pnls"} for w, m in BASELINE.items()}
    ev = ar.p2v2_evaluate(base, BASELINE)
    assert ar.p2v2_stage1({"X": ev})["X"]["label"] == "HATA"


def test_extract_trade_series_from_report_payload():
    payload = {"overall": {"bar_max_drawdown": 12.5},
               "trades": [{"exit_time": 1000, "pnl": 1.5}, {"exit_time": 2000, "pnl": -0.5}]}
    assert ar.extract_trade_series(payload) == {"trade_pnls": [[1000, 1.5], [2000, -0.5]],
                                                "bar_max_drawdown": 12.5}
    assert ar.extract_trade_series({"overall": {}}) is None
