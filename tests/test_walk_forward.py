"""scripts/walk_forward.py testleri (paket E): kat tarihleri, train-only secim, havuzlanmis
toplama, saklı pencere kaydi, onbellek kapsami, run_windows.sh/autoresearch kayit denetimi.
Gercek fonksiyonlar kosulur; ag/ssh yok. Sahte runner yalnizca KOSU hattini (subprocess) taklit eder,
test edilen mantik (secim/toplama/kayit) gercektir."""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import autoresearch as ar  # noqa: E402
import walk_forward as wf  # noqa: E402
from src.strategies.scalper import kline_cache  # noqa: E402

D = date.fromisoformat


def noon_ms(d: date, hours: int = 12) -> int:
    return int(datetime(d.year, d.month, d.day, hours, tzinfo=timezone.utc).timestamp() * 1000)


# --------------------------------------------------------------------------
# 1. Kat tarihleri
# --------------------------------------------------------------------------


def test_rolling_folds_exact_dates_and_contiguous_tests():
    folds = wf.make_folds(D("2026-01-01"), D("2026-03-01"), 20, 10, mode="rolling")
    got = [(f.train_start.isoformat(), f.train_end.isoformat(),
            f.test_start.isoformat(), f.test_end.isoformat()) for f in folds]
    assert got == [
        ("2026-01-01", "2026-01-21", "2026-01-21", "2026-01-31"),
        ("2026-01-11", "2026-01-31", "2026-01-31", "2026-02-10"),
        ("2026-01-21", "2026-02-10", "2026-02-10", "2026-02-20"),
    ]  # k=3 test'i 03-02'de biterdi > end: kat yok
    for f in folds:
        assert f.train_end <= f.test_start
        assert f.train_days == 20 and f.test_days == 10
    for a, b in zip(folds, folds[1:]):
        assert a.test_end == b.test_start  # test pencereleri bitisik


def test_anchored_folds_grow_train_and_keep_start():
    folds = wf.make_folds(D("2026-01-01"), D("2026-03-01"), 20, 10, mode="anchored")
    assert [f.train_start for f in folds] == [D("2026-01-01")] * 3
    assert [f.train_end.isoformat() for f in folds] == ["2026-01-21", "2026-01-31", "2026-02-10"]
    assert [f.test_start.isoformat() for f in folds] == ["2026-01-21", "2026-01-31", "2026-02-10"]
    for f in folds:
        assert f.train_end <= f.test_start
    for a, b in zip(folds, folds[1:]):
        assert a.test_end == b.test_start


def test_embargo_hours_round_up_to_whole_days():
    f = wf.make_folds(D("2026-01-01"), D("2026-03-01"), 20, 10, embargo_hours=30)[0]
    assert f.train_end == D("2026-01-21") and f.test_start == D("2026-01-23")  # ceil(30/24)=2
    f0 = wf.make_folds(D("2026-01-01"), D("2026-03-01"), 20, 10, embargo_hours=0)[0]
    assert f0.test_start == f0.train_end


def test_step_smaller_than_test_is_refused_and_no_fold_raises():
    with pytest.raises(ValueError, match="iki kez"):
        wf.make_folds(D("2026-01-01"), D("2026-03-01"), 20, 10, step_days=5)
    with pytest.raises(ValueError, match="hic kat"):
        wf.make_folds(D("2026-01-01"), D("2026-01-15"), 20, 10)


# --------------------------------------------------------------------------
# 2. Train-only secim
# --------------------------------------------------------------------------


def day_trades(start: date, days: int, per_day: int, pnl_for_day, side="LONG"):
    out = []
    for i in range(days):
        d = start + timedelta(days=i)
        for j in range(per_day):
            out.append((noon_ms(d, 1 + j), float(pnl_for_day(i)), side))
    return out


TRAIN_START, TRAIN_END = "2026-01-01", "2026-01-11"   # 10 gun
TEST_START, TEST_END = "2026-01-11", "2026-01-16"     # 5 gun


def make_runner(table):
    """table[(cid, 'train'|'test')] -> fn(gun_index) -> pnl; islem sayisi per_day=7."""
    def runner(cid, start, end):
        kind = "train" if start == TRAIN_START else "test"
        spec = table[(cid, kind)]
        fn, per_day = spec if isinstance(spec, tuple) else (spec, 7)
        return day_trades(D(start), (D(end) - D(start)).days, per_day, fn)
    return runner


ONE_FOLD = wf.make_folds(D(TRAIN_START), D(TEST_END), 10, 5)


def test_selection_uses_train_window_only():
    base = lambda i: 1.0 + 0.2 * (i % 2)
    table = {
        (wf.BASELINE_ID, "train"): base, (wf.BASELINE_ID, "test"): base,
        # train'de iyi, test'te taban: SECILMELI
        ("GOOD", "train"): lambda i: base(i) + 1.0 + 0.5 * (i % 2),
        ("GOOD", "test"): base,
        # train'de kotu, test'te dev kazanc: SECILMEMELI (test'e bakilsaydi secilirdi)
        ("TESTHERO", "train"): lambda i: base(i) - 1.0 - 0.5 * (i % 2),
        ("TESTHERO", "test"): lambda i: base(i) + 50.0,
    }
    res = wf.run_walk_forward(make_runner(table), ["GOOD", "TESTHERO"], ONE_FOLD,
                              min_trades=10)
    fold = res["folds"][0]
    assert fold["selected"] == "GOOD"
    by_id = {c["id"]: c for c in fold["candidates"]}
    assert by_id["GOOD"]["eligible"] and by_id["GOOD"]["score"] >= wf.SELECT_MIN_SCORE
    assert not by_id["TESTHERO"]["eligible"] and by_id["TESTHERO"]["score"] < 0
    # secilen test'te donduruldu: GOOD'un TEST sonucu raporlanir (taban ile ayni, delta 0)
    assert fold["test_selected"]["net"] == pytest.approx(fold["test_baseline"]["net"])
    assert res["selection_counts"] == {"GOOD": 1}


def test_no_eligible_candidate_keeps_baseline_and_min_trade_rule():
    base = lambda i: 1.0 + 0.2 * (i % 2)
    table = {
        (wf.BASELINE_ID, "train"): base, (wf.BASELINE_ID, "test"): base,
        # cok iyi ama yalniz 1 islem/gun = 10 islem < 60: asgari-islem kurali reddeder
        ("FEWTRADES", "train"): (lambda i: base(i) + 9.0 + 0.5 * (i % 2), 1),  # 1 islem/gun, gunluk delta ~ +2.3..+3
        ("FEWTRADES", "test"): base,
        # esik altinda: delta ~ 0
        ("FLAT", "train"): base, ("FLAT", "test"): base,
    }
    res = wf.run_walk_forward(make_runner(table), ["FEWTRADES", "FLAT"], ONE_FOLD,
                              min_trades=60)
    fold = res["folds"][0]
    assert fold["selected"] == wf.BASELINE_ID
    by_id = {c["id"]: c for c in fold["candidates"]}
    assert by_id["FEWTRADES"]["score"] >= wf.SELECT_MIN_SCORE
    assert by_id["FEWTRADES"]["enough_trades"] is False and not by_id["FEWTRADES"]["eligible"]
    assert by_id["FLAT"]["delta"] == pytest.approx(0, abs=1e-9) and not by_id["FLAT"]["eligible"]
    assert res["selection_counts"] == {wf.BASELINE_ID: 1}


def test_highest_score_wins_among_eligible():
    base = lambda i: 1.0 + 0.2 * (i % 2)
    table = {(wf.BASELINE_ID, "train"): base, (wf.BASELINE_ID, "test"): base}
    for cid, add in (("SMALL", 0.5), ("BIG", 3.0)):
        table[(cid, "train")] = (lambda a: (lambda i: base(i) + a + 0.3 * (i % 2)))(add)
        table[(cid, "test")] = base
    res = wf.run_walk_forward(make_runner(table), ["SMALL", "BIG"], ONE_FOLD, min_trades=10)
    assert res["folds"][0]["selected"] == "BIG"


# --------------------------------------------------------------------------
# 3. Havuzlanmis toplama (elle kontrol edilebilir)
# --------------------------------------------------------------------------


def _agg_runner(cid, start, end):
    n = (D(end) - D(start)).days
    if n == 4:  # train: gun basina 1 islem, +2, LONG
        return day_trades(D(start), 4, 1, lambda i: 2.0, "LONG")
    # test (2 gun): gun1 +10 LONG, gun2 -4 SHORT
    return [(noon_ms(D(start)), 10.0, "LONG"), (noon_ms(D(start) + timedelta(days=1)), -4.0, "SHORT")]


def test_pooled_aggregation_arithmetic():
    folds = wf.make_folds(D("2026-01-01"), D("2026-01-11"), 4, 2)
    assert len(folds) == 3
    res = wf.run_walk_forward(_agg_runner, [], folds)
    p = res["pooled"]["selected"]
    assert p["trades"] == 6 and p["net"] == pytest.approx(18.0)
    assert p["winrate"] == pytest.approx(50.0)
    assert p["gross_profit"] == pytest.approx(30.0) and p["gross_loss"] == pytest.approx(12.0)
    assert p["pf"] == pytest.approx(2.5)
    assert p["max_dd"] == pytest.approx(4.0)          # +10, -4, +10, -4 ...: tepe 10 -> 6
    assert p["by_side"]["LONG"]["trades"] == 3 and p["by_side"]["LONG"]["net"] == pytest.approx(30.0)
    assert p["by_side"]["SHORT"]["trades"] == 3 and p["by_side"]["SHORT"]["net"] == pytest.approx(-12.0)
    assert res["pooled"]["test_days"] == 6
    # WFE: OOS 18/6 = 3.0 /gun ; train 3 kat x 8 = 24 / 12 gun = 2.0 /gun
    assert res["wfe"]["oos_net_per_day"] == pytest.approx(3.0)
    assert res["wfe"]["train_net_per_day"] == pytest.approx(2.0)
    assert res["wfe"]["value"] == pytest.approx(1.5)
    # secilen == taban: eslestirilmis delta 0
    assert res["paired_oos"]["delta"] == 0 and res["selection_counts"] == {wf.BASELINE_ID: 3}


def test_parallel_jobs_match_serial_and_failure_is_loud():
    folds = wf.make_folds(D("2026-01-01"), D("2026-01-11"), 4, 2)
    a = wf.run_walk_forward(_agg_runner, [], folds, jobs=1)
    b = wf.run_walk_forward(_agg_runner, [], folds, jobs=4)
    assert a == b
    with pytest.raises(wf.WalkForwardError, match="backtest basarisiz"):
        wf.run_walk_forward(lambda *a: None, [], folds)


def test_markdown_and_json_report_render():
    folds = wf.make_folds(D("2026-01-01"), D("2026-01-11"), 4, 2)
    res = wf.run_walk_forward(_agg_runner, [], folds)
    report = {"config": {"mode": "rolling", "train_days": 4, "test_days": 2, "step_days": 2,
                         "embargo_hours": 0, "start": "2026-01-01", "end": "2026-01-11",
                         "candidates": []},
              **res, "registry": [], "registry_text": "kayit"}
    md = wf.render_markdown(report)
    assert "Havuzlanmis ornek-disi" in md and "| 0 | 2026-01-01..2026-01-05 |" in md
    assert "Walk-forward verimliligi" in md and "1.50" in md
    json.dumps(wf._jsonable(report))  # inf/NaN siziyor mu


# --------------------------------------------------------------------------
# 4. Saklı pencere kaydi
# --------------------------------------------------------------------------


def make_registry(tmp_path: Path) -> Path:
    reg = [
        {"name": "B", "start": "2026-01-01", "end": "2026-01-31", "status": "burned", "note": "", "opens": []},
        {"name": "V", "start": "2026-09-04", "end": "2026-10-15", "status": "validation", "note": "", "opens": []},
        {"name": "S", "start": "2026-10-16", "end": "2026-11-19", "status": "sealed", "note": "", "opens": []},
    ]
    p = tmp_path / "registry.json"
    p.write_text(json.dumps(reg))
    return p


def test_sealed_window_refused_without_purpose_and_file_unchanged(tmp_path):
    p = make_registry(tmp_path)
    before = p.read_text()
    with pytest.raises(wf.SealedWindowError, match="MUHURLU"):
        wf.guard_windows(p, [("2026-10-10", "2026-10-17")], by="t")
    with pytest.raises(wf.SealedWindowError):
        wf.guard_windows(p, [("2026-10-10", "2026-10-17")], by="t", open_sealed="   ")
    assert p.read_text() == before


def test_registry_end_is_inclusive_and_run_end_exclusive(tmp_path):
    p = make_registry(tmp_path)
    # [10-10, 10-16) 10-15'te biter: V'ye degir, S'ye (10-16 baslar) DEGMEZ
    t = wf.guard_windows(p, [("2026-10-10", "2026-10-16")], by="t")
    assert [x["name"] for x in t] == ["V"]
    # [10-10, 10-17) S'nin ilk gununu icerir
    with pytest.raises(wf.SealedWindowError):
        wf.guard_windows(p, [("2026-10-10", "2026-10-17")], by="t")
    # S'nin son gunu 11-19: [11-19, 11-20) degir, [11-20, ..) degmez
    with pytest.raises(wf.SealedWindowError):
        wf.guard_windows(p, [("2026-11-19", "2026-11-20")], by="t")
    assert wf.guard_windows(p, [("2026-11-20", "2026-11-25")], by="t") == []


def test_opening_sealed_appends_entry_and_counts(tmp_path):
    p = make_registry(tmp_path)
    t1 = wf.guard_windows(p, [("2026-10-16", "2026-10-20"), ("2026-11-01", "2026-11-05")],
                          by="walk_forward", open_sealed="final sinav paketi 1")
    s1 = next(x for x in t1 if x["name"] == "S")
    assert s1["opens"] == 1 and s1["opened_now"]       # iki kosu penceresi = TEK acilis
    reg = json.loads(p.read_text())
    opens = next(w for w in reg if w["name"] == "S")["opens"]
    assert len(opens) == 1
    assert opens[0]["by"] == "walk_forward" and opens[0]["purpose"] == "final sinav paketi 1"
    datetime.strptime(opens[0]["at"], "%Y-%m-%dT%H:%M:%SZ")
    t2 = wf.guard_windows(p, [("2026-10-16", "2026-10-20")], by="autoresearch", open_sealed="paket 2")
    assert next(x for x in t2 if x["name"] == "S")["opens"] == 2
    # record=False: sayac artmaz
    t3 = wf.guard_windows(p, [("2026-10-16", "2026-10-20")], by="x", open_sealed="p", record=False)
    assert next(x for x in t3 if x["name"] == "S")["opens"] == 2
    assert len(json.loads(p.read_text())[2]["opens"]) == 2


def test_burned_never_written_validation_recorded_and_report_notes_untracked(tmp_path):
    p = make_registry(tmp_path)
    before = p.read_text()
    t = wf.guard_windows(p, [("2026-01-05", "2026-01-10")], by="t")
    assert [(x["name"], x["status"], x["opens"], x["opened_now"]) for x in t] == [("B", "burned", 0, False)]
    assert p.read_text() == before                     # yanik pencere: dosya DEGISMEZ (agac kirlenmez)
    text = wf.format_touched(t)
    assert "[burned" in text and "yanik" in text and "izlenmez" in text
    t = wf.guard_windows(p, [("2026-01-05", "2026-01-10"), ("2026-09-10", "2026-09-20")], by="t")
    assert [(x["name"], x["status"], x["opens"]) for x in t] == [("B", "burned", 0), ("V", "validation", 1)]
    t2 = wf.guard_windows(p, [("2026-09-10", "2026-09-20")], by="t2")
    assert [(x["name"], x["opens"]) for x in t2] == [("V", 2)]
    reg = json.loads(p.read_text())
    assert [o["by"] for o in next(w for w in reg if w["name"] == "V")["opens"]] == ["t", "t2"]
    assert next(w for w in reg if w["name"] == "B")["opens"] == []
    # record=False: sayac artmaz (on-denetim / dry-run)
    t3 = wf.guard_windows(p, [("2026-09-10", "2026-09-20")], by="t3", record=False)
    assert t3[0]["opens"] == 2


def test_registry_cli_exit_codes(tmp_path, capsys):
    p = make_registry(tmp_path)
    assert wf.main(["registry-check", "--registry", str(p), "--start", "2026-10-16", "--end", "2026-10-20"]) == 2
    assert "MUHURLU" in capsys.readouterr().err
    assert wf.main(["registry-check", "--registry", str(p), "--start", "2026-10-16", "--end", "2026-10-20",
                    "--open-sealed", "amac", "--by", "cli"]) == 0
    assert "acilis=1" in capsys.readouterr().out
    assert wf.main(["registry-check", "--registry", str(tmp_path / "yok.json"),
                    "--start", "2026-01-01", "--end", "2026-01-02"]) == 2   # kayit yok = fail-closed


def test_real_registry_is_valid_and_seeded():
    reg = wf.load_registry(wf.DEFAULT_REGISTRY)
    by = {w["name"]: w for w in reg}
    assert by["VAL-2026Q4a"]["status"] == "validation"
    assert (by["VAL-2026Q4a"]["start"], by["VAL-2026Q4a"]["end"]) == ("2026-09-04", "2026-10-15")
    assert by["SEAL-2026Q4b"]["status"] == "sealed" and by["SEAL-2026Q4b"]["opens"] == []
    assert (by["SEAL-2026Q4b"]["start"], by["SEAL-2026Q4b"]["end"]) == ("2026-10-16", "2026-11-19")
    # kullanilmis her pencere (autoresearch + run_windows.sh) yanik olarak kayitli
    used = [(s, e) for _, s, e in ar.WINDOWS] + [
        ("2026-03-01", "2026-04-01"), ("2026-08-21", "2026-09-03"), ("2026-05-04", "2026-05-25"),
        ("2026-06-08", "2026-06-29"), ("2026-02-13", "2026-03-01"), ("2026-07-21", "2026-08-07")]
    for s, e in used:
        touched = wf.touched_windows(reg, s, e)
        assert touched and all(w["status"] == "burned" for w in touched), (s, e)
    # her kosulan pencere run_windows.sh'deki tarihlerle ayni
    sh = (SCRIPTS_DIR / "run_windows.sh").read_text()
    for s, e in used:
        assert f"{s} {e} " in sh


def test_autoresearch_registry_gate(tmp_path, capsys):
    reg = [{"name": "SEAL", "start": "2026-01-23", "end": "2026-02-12", "status": "sealed",
            "note": "", "opens": []}]
    p = tmp_path / "r.json"
    p.write_text(json.dumps(reg))
    args = argparse.Namespace(registry=str(p), open_sealed=None)
    assert ar.registry_gate(args, record=True) == 2
    assert "MUHURLU" in capsys.readouterr().err
    args.open_sealed = "amac"
    assert ar.registry_gate(args, record=False) is None
    assert json.loads(p.read_text())[0]["opens"] == []
    assert ar.registry_gate(args, record=True) is None
    opens = json.loads(p.read_text())[0]["opens"]
    assert len(opens) == 1 and opens[0]["by"] == "autoresearch"
    assert "acilis=1" in capsys.readouterr().out


def test_extract_trade_series_carries_direction():
    payload = {"overall": {}, "trades": [
        {"exit_time": 1000, "pnl": 1.5, "direction": "LONG"},
        {"exit_time": 2000, "pnl": -0.5, "direction": "SHORT"}]}
    assert ar.extract_trade_series(payload)["trade_sides"] == ["LONG", "SHORT"]


# --------------------------------------------------------------------------
# 5. Onbellek kapsami
# --------------------------------------------------------------------------


def test_cache_constants_match_backtest():
    from src.strategies.scalper import backtest as bt
    from src.strategies.scalper.market_gate import MARKET_GATE_INTRADAY_TF

    assert wf.WARMUP_BY_ROLE == bt._WARMUP_BY_ROLE
    assert wf.LEADER_WARMUP_ENTRY == bt._CTX_5M_WINDOW
    assert wf.LEADER_WARMUP_INTRADAY == bt._CTX_15M_WINDOW
    assert wf.LEADER_DAILY_EXTRA_DAYS == bt._LEADER_DAILY_EXTRA_DAYS
    assert wf.LEADER_INTRADAY_TF == MARKET_GATE_INTRADAY_TF
    for tf, per_day in bt._CANDLES_PER_DAY.items():
        assert 86_400_000 // kline_cache._INTERVAL_MS[tf] == per_day


def test_required_series_matches_backtest_formulas():
    prof = wf.env_data_profile({"SCALPER_TF_ENTRY": "1m", "SCALPER_TF_CONTEXT": "5m",
                                "SCALPER_TF_REGIME": "15m", "SCALPER_MARKET_GATE": "true"})
    assert prof == {"timeframes": ("1m", "5m", "15m"), "leader": {"symbol": "BTCUSDT", "run_days": 3}}
    req = wf.required_series(["ETHUSDT"], prof, D("2026-01-02"), D("2026-01-04"))
    end_ms = noon_ms(D("2026-01-04"), 0)
    assert req == [
        ("ETHUSDT", "1m", 2 * 1440 + 150, end_ms),
        ("ETHUSDT", "5m", 2 * 288 + 100, end_ms),
        ("ETHUSDT", "15m", 2 * 96 + 250, end_ms),
        ("BTCUSDT", "1m", 2 * 1440 + 150, end_ms),
        ("BTCUSDT", "1d", 2 + 3 + 3, end_ms),
        ("BTCUSDT", "15m", 2 * 96 + 100, end_ms),
    ]


def candles(interval_ms: int, count: int, end_ms: int):
    from src.strategies.scalper.types import Candle
    out = []
    for i in range(count):
        o = end_ms - (count - i) * interval_ms
        out.append(Candle(o, 1.0, 2.0, 0.5, 1.5, 10.0, o + interval_ms - 1))
    return out


def test_ensure_cache_reports_missing_and_derives_from_covering_file(tmp_path):
    cache = tmp_path / "cache"
    prof = wf.env_data_profile({})           # 5m/15m/4h, lider kapisi yok
    req = wf.required_series(["BTCUSDT"], prof, D("2026-01-02"), D("2026-01-04"))
    end_ms = req[0][3]
    # bos onbellek: 3 eksik, mesajda sembol/aralik/mum sayisi var
    missing, derived = wf.ensure_cache(cache, req)
    assert derived == [] and len(missing) == 3
    msg = wf.format_missing(missing)
    assert "kline onbellegi yetersiz" in msg and "BTCUSDT 5m: 726 mum" in msg
    assert "BTCUSDT 15m: 292 mum" in msg and "BTCUSDT 4h: 262 mum" in msg
    # 5m icin BUYUK, daha gec biten kapsayici dosya: tam anahtar ondan dilimlenir
    big_end = end_ms + 5 * 86_400_000
    cs = candles(300_000, 5000, big_end)
    s_ms = kline_cache.window_start_ms("5m", 5000, big_end)
    kline_cache.save(cache, "BTCUSDT", "5m", s_ms, big_end, cs)
    missing, derived = wf.ensure_cache(cache, req)
    assert len(derived) == 1 and "BTCUSDT 5m" in derived[0]
    assert len(missing) == 2 and all(not m.startswith("BTCUSDT 5m") for m in missing)
    got = kline_cache.load(cache, "BTCUSDT", "5m", kline_cache.window_start_ms("5m", 726, end_ms), end_ms)
    assert len(got) == 726 and got[-1].close_time == end_ms - 1 and got[-1].close_time <= end_ms
    # ikinci cagri: tam anahtar var, artik turetme yok
    missing2, derived2 = wf.ensure_cache(cache, req)
    assert derived2 == [] and len(missing2) == 2


def test_covering_file_too_short_is_missing(tmp_path):
    cache = tmp_path / "cache"
    req = [("BTCUSDT", "5m", 726, noon_ms(D("2026-01-04"), 0))]
    big_end = req[0][3] + 86_400_000
    kline_cache.save(cache, "BTCUSDT", "5m", kline_cache.window_start_ms("5m", 100, big_end),
                     big_end, candles(300_000, 100, big_end))
    missing, derived = wf.ensure_cache(cache, req)
    assert derived == [] and len(missing) == 1


def _env_and_cands(tmp_path: Path):
    env = tmp_path / "env.txt"
    env.write_text("SCALPER_LEVERAGE=7\n")
    cands = tmp_path / "cands.json"
    cands.write_text("[]")
    return env, cands


def test_main_exits_with_missing_cache_list(tmp_path, capsys):
    env, cands = _env_and_cands(tmp_path)
    p = make_registry(tmp_path)
    rc = wf.main(["--start", "2026-01-01", "--end", "2026-01-11", "--train-days", "4", "--test-days", "2",
                  "--env-file", str(env), "--candidates", str(cands), "--symbols", "BTCUSDT",
                  "--cache-dir", str(tmp_path / "cache"), "--registry", str(p),
                  "--out-dir", str(tmp_path / "out")])
    err = capsys.readouterr().err
    assert rc == 3 and "kline onbellegi yetersiz" in err and "BTCUSDT 5m" in err


def test_main_refuses_sealed_before_cache_and_records_only_after_cache_ok(tmp_path, capsys):
    env, cands = _env_and_cands(tmp_path)
    p = make_registry(tmp_path)
    argv = ["--start", "2026-10-16", "--end", "2026-10-26", "--train-days", "4", "--test-days", "2",
            "--env-file", str(env), "--candidates", str(cands), "--symbols", "BTCUSDT",
            "--cache-dir", str(tmp_path / "cache"), "--registry", str(p), "--out-dir", str(tmp_path / "o")]
    assert wf.main(argv) == 2 and "MUHURLU" in capsys.readouterr().err
    # amac verilir ama onbellek yok: kosu baslamadi, ACILIS YAZILMAZ
    assert wf.main(argv + ["--open-sealed", "paket"]) == 3
    assert json.loads(p.read_text())[2]["opens"] == []


# --------------------------------------------------------------------------
# 6. run_windows.sh kayit denetimi
# --------------------------------------------------------------------------


def _run_windows(tmp_path: Path, registry: Path, open_sealed: str = "", relative_from: Path | None = None):
    repo = tmp_path / "wrepo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / ".scalper_env_snapshot.txt").write_text("SCALPER_LEVERAGE=7\n")
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.log"
    calls.write_text("")

    def write_exec(path: Path, content: str) -> None:
        path.write_text(content)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    write_exec(bin_dir / "timeout", '#!/usr/bin/env bash\nshift\nexec "$@"\n')
    write_exec(bin_dir / "python3", f"""#!/usr/bin/env bash
if [ "$1" = "-m" ]; then
  start=""
  while [ $# -gt 0 ]; do case "$1" in --start) start="$2";; esac; shift; done
  echo "$start" >> "{calls}"
  mkdir -p logs
  f="logs/backtest_${{start//-/}}_000000.json"
  echo '{{"overall": {{"trades": 70, "total_pnl": 5.0, "profit_factor": 1.5, "max_drawdown": 2.0, "winrate": 60.0}}}}' > "$f"
  echo "Backtest raporu yazildi: $f"
  exit 0
fi
exec "{sys.executable}" "$@"
""")
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["RUN_WINDOWS_REPO"] = str(repo)
    env["RUN_WINDOWS_OUT"] = str(tmp_path / "out")
    env["HOLDOUT_REGISTRY"] = str(registry)
    env.pop("FRESH", None)
    env.pop("OPEN_SEALED", None)
    if open_sealed:
        env["OPEN_SEALED"] = open_sealed
    script = str(SCRIPTS_DIR / "run_windows.sh")
    cwd = None
    if relative_from is not None:     # GORECELI cagri: baska bir dizinden `bash v2/scripts/run_windows.sh`
        script = os.path.relpath(script, relative_from)
        cwd = str(relative_from)
    proc = subprocess.run(["bash", script, "t1"], env=env, cwd=cwd,
                          capture_output=True, text=True)
    return proc, calls.read_text().split()


def test_run_windows_refuses_sealed_window_then_opens_with_purpose(tmp_path):
    reg = [{"name": "SEAL_YATAY", "start": "2026-07-01", "end": "2026-07-20", "status": "sealed",
            "note": "", "opens": []}]
    p = tmp_path / "reg.json"
    p.write_text(json.dumps(reg))
    proc, calls = _run_windows(tmp_path, p)
    assert proc.returncode == 2 and calls == []            # HIC koşu baslamadi
    assert "MUHURLU" in proc.stderr and json.loads(p.read_text())[0]["opens"] == []
    (tmp_path / "b").mkdir()
    proc, calls = _run_windows(tmp_path / "b", p, open_sealed="amac")
    assert proc.returncode == 0, proc.stderr
    assert len(calls) == 7
    opens = json.loads(p.read_text())[0]["opens"]
    assert len(opens) == 1 and opens[0]["by"] == "run_windows" and opens[0]["purpose"] == "amac"
    assert "acilis=1" in proc.stdout


def test_run_windows_works_when_invoked_by_relative_path_from_another_dir(tmp_path):
    reg = [{"name": "SEAL_YATAY", "start": "2026-07-01", "end": "2026-07-20", "status": "sealed",
            "note": "", "opens": []}]
    p = tmp_path / "reg.json"
    p.write_text(json.dumps(reg))
    proc, calls = _run_windows(tmp_path, p, relative_from=REPO_ROOT.parent)
    # Betik bulundu ve kayit denetimi GERCEKTEN calisti: red nedeni muhurlu pencere, kayip dosya degil.
    assert "No such file" not in proc.stderr and "can't open file" not in proc.stderr, proc.stderr
    assert proc.returncode == 2 and "MUHURLU" in proc.stderr and calls == []


# --------------------------------------------------------------------------
# 7. Kayit tohumu (docs/EXPERIMENTS.md ile tutarlilik) ve kayit parmak izi
# --------------------------------------------------------------------------


def test_april_is_burned_in_real_registry_with_judge_open():
    by = {w["name"]: w for w in wf.load_registry(wf.DEFAULT_REGISTRY)}
    apr = by["SAKLI_NISAN"]
    assert apr["status"] == "burned"                       # EXPERIMENTS.md:1534 "artik taze sayilmaz"
    assert any("213004" in o["purpose"] for o in apr["opens"])
    assert "Hiçbir koşuda kullanılmadı" not in apr["note"]
    # yanik pencereye degen bir walk-forward artik reddedilmez ama yanik olarak raporlanir
    t = wf.touched_windows(list(by.values()), "2026-04-05", "2026-04-10")
    assert [w["status"] for w in t] == ["burned"]


def test_registry_fingerprint_changes_with_content_and_is_printed(tmp_path, capsys):
    p = make_registry(tmp_path)
    f1 = wf.registry_fingerprint(p)
    assert len(f1) == 12
    assert wf.main(["registry-check", "--registry", str(p), "--start", "2026-09-10", "--end", "2026-09-11"]) == 0
    out = capsys.readouterr().out
    assert str(p) in out                                   # hangi kayit dosyasi
    assert wf.registry_fingerprint(p) != f1                # kullanim kaydi icerigi degistirdi
    assert f"sha256={wf.registry_fingerprint(p)}" in out   # yazdirilan = kullanimdan SONRAKI dosya


# --------------------------------------------------------------------------
# 8. Funding onbellegi: walk-forward is dizininde bile MUTLAK yol
# --------------------------------------------------------------------------

_ARGV_BACKTEST = '''\
import json, sys
from pathlib import Path
Path("logs").mkdir(exist_ok=True)
Path("argv.json").write_text(json.dumps(sys.argv[1:]))
Path("logs/backtest_1_1.json").write_text(json.dumps({"overall": {"trades": 70, "winrate": 60.0,
    "total_pnl": 1.0, "profit_factor": 1.5, "max_drawdown": 1.0}}))
print("Backtest raporu yazildi: logs/backtest_1_1.json")
'''


def test_job_backtest_command_carries_absolute_funding_cache_dir(tmp_path):
    job = tmp_path / "job"            # walk_forward is dizini: cwd REPO_ROOT DEGIL
    pkg = job / "src" / "strategies" / "scalper"
    pkg.mkdir(parents=True)
    for d in (job / "src", job / "src" / "strategies", pkg):
        (d / "__init__.py").write_text("")
    (pkg / "backtest.py").write_text(_ARGV_BACKTEST)
    ok, _ = ar.run_single_backtest(
        env_vars={}, symbols="BTCUSDT", start="2026-01-01", end="2026-01-02",
        cache_dir="c", log_path=tmp_path / "l.log", bot_log_dir=tmp_path / "bl", repo_root=job,
    )
    assert ok
    argv = json.loads((job / "argv.json").read_text())
    path = argv[argv.index("--funding-cache-dir") + 1]
    assert Path(path).is_absolute()
    assert Path(path) == REPO_ROOT / "data" / "funding_cache"
    assert argv.count("--funding-cache-dir") == 1


def test_explicit_funding_flag_in_extra_flags_is_not_duplicated(tmp_path):
    job = tmp_path / "job"
    pkg = job / "src" / "strategies" / "scalper"
    pkg.mkdir(parents=True)
    for d in (job / "src", job / "src" / "strategies", pkg):
        (d / "__init__.py").write_text("")
    (pkg / "backtest.py").write_text(_ARGV_BACKTEST)
    ar.run_single_backtest(
        env_vars={}, symbols="BTCUSDT", start="2026-01-01", end="2026-01-02", cache_dir="c",
        log_path=tmp_path / "l.log", bot_log_dir=tmp_path / "bl", repo_root=job,
        extra_flags=["--funding-cache-dir", "/x/y"],
    )
    argv = json.loads((job / "argv.json").read_text())
    assert argv.count("--funding-cache-dir") == 1 and argv[argv.index("--funding-cache-dir") + 1] == "/x/y"


def test_funding_coverage_lists_symbols_without_cache_file(tmp_path):
    (tmp_path / "BTCUSDT.json").write_text("[]")
    assert wf.funding_missing(["BTCUSDT", "ETHUSDT"], tmp_path) == ["ETHUSDT"]
