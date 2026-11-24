"""E23 kosucusu (scripts/e23/run_e23.py) testleri: cevrimdisi, surec baslatma tek noktadan taklit edilir."""

from __future__ import annotations

import hashlib
import json
import py_compile
import subprocess
import sys
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "e23"))
import e23_common as ec  # noqa: E402
import run_e23 as rr  # noqa: E402

SNAP_LINES = ["SCALPER_FOO=1", "SCALPER_REGIME_FILTER=true", "SCALPER_ENTRY_BLOCK_WEEKDAYS_UTC=Mon,Tue",
              "SCALPER_ENTRY_MODE=maker"]
SNAP_TEXT = "\n".join(SNAP_LINES + ["NOT_SCALPER=x", "# yorum"]) + "\n"


class Stub:
    def __init__(self, root: Path):
        self.root = root
        self.calls: list[dict] = []
        self.n_reports = 1
        self.bt_rc = 0
        self.check_rc = 0
        self.touch_cache = False
        self.fill_fail: dict[str, int] = {}
        self.lock = threading.Lock()

    def __call__(self, cmd, env, cwd, log_path, timeout):
        with self.lock:
            self.calls.append({"cmd": list(cmd), "env": dict(env), "cwd": cwd, "log": log_path})
        if "src.strategies.scalper.backtest" in cmd:
            if self.touch_cache:
                (self.root / "cache" / "new.json").write_text("x")
            logs = Path(cwd) / "logs"
            logs.mkdir(exist_ok=True)
            for i in range(self.n_reports):
                (logs / f"backtest_2026010{i}_000000.json").write_text(json.dumps({"i": i}))
            return self.bt_rc
        if "check-run" in cmd:
            return self.check_rc
        if "s2-compare" in cmd:
            return self.check_rc
        if any(str(c).endswith("fill_klines_cache_from_vision.py") for c in cmd):
            sym = cmd[cmd.index("--symbols") + 1]
            with self.lock:
                left = self.fill_fail.get(sym, 0)
                if left:
                    self.fill_fail[sym] = left - 1
                    return 2
            return 0
        return 0

    def bt_calls(self):
        return [c for c in self.calls if "src.strategies.scalper.backtest" in c["cmd"]]


@pytest.fixture
def env_(tmp_path, monkeypatch):
    for k in list(__import__("os").environ):
        if k.startswith(("SCALPER_", "HTTP", "http", "NO_PROXY", "no_proxy", "SSL_CERT")):
            monkeypatch.delenv(k)
    root = tmp_path / "root"
    (root / "cache").mkdir(parents=True)
    (root / "cache" / "a.json").write_text("a")
    (root / "funding").mkdir()
    (root / "clone_H").mkdir()
    (root / "H.txt").write_text("deadbeef\n")
    snap = tmp_path / "snap.txt"
    snap.write_text(SNAP_TEXT)
    v = ec.load_variants()
    v["snapshot"]["sha256"] = hashlib.sha256(SNAP_TEXT.encode()).hexdigest()
    v["snapshot"]["scalper_lines"] = len(SNAP_LINES)
    vp = tmp_path / "variants.json"
    vp.write_text(json.dumps(v))
    stub = Stub(root)
    monkeypatch.setattr(rr, "_spawn", stub)

    def run(*args, snapshot=True):
        argv = ["--root", str(root), "--variants", str(vp), "--analyzer", "/x/analyze_e23.py"]
        if snapshot:
            argv += ["--snapshot", str(snap)]
        return rr.main(argv + list(args))

    return type("E", (), {"root": root, "snap": snap, "stub": stub, "run": staticmethod(run), "vp": vp,
                          "variants": v, "tmp": tmp_path})


def test_py_compile(tmp_path):
    py_compile.compile(str(REPO / "scripts" / "e23" / "run_e23.py"), cfile=str(tmp_path / "x.pyc"), doraise=True)


def test_env_isolated_and_overrides(env_, monkeypatch, capsys):
    monkeypatch.setenv("SCALPER_FOO", "PARENT")
    monkeypatch.setenv("BINANCE_API_KEY", "real-looking-secret-123")
    monkeypatch.setenv("SCALPER_ENTRY_MODE", "parent")
    assert env_.run("run", "--set", "CAL", "--arms", "K30B-L") == 0
    env = env_.stub.bt_calls()[0]["env"]
    assert env["SCALPER_FOO"] == "1"
    assert env["BINANCE_API_KEY"] == "dummy"
    assert "real-looking-secret-123" not in json.dumps(env)
    assert env["SCALPER_ENTRY_MODE"] == "taker"  # override snapshot'i (maker) ezer
    assert env["SCALPER_REGIME_FILTER"] == "false"
    assert "SCALPER_ENTRY_BLOCK_WEEKDAYS_UTC" in env and env["SCALPER_ENTRY_BLOCK_WEEKDAYS_UTC"] == ""
    assert env["HTTPS_PROXY"] == env["http_proxy"] == "http://127.0.0.1:9"
    assert env["PYTHONPATH"] == str(env_.root / "clone_H")
    assert env["PYTHONHASHSEED"] == "0"
    assert env["SCALPER_TF_ENTRY"] == "30m"
    assert env["SCALPER_SHOCK_TRIGGER_PCT"] == "1.5" and env["SCALPER_M_TREND"] == "context"
    assert env["SCALPER_MARKET_GATE"] == "false" and env["SCALPER_MARKET_GATE_SYMBOL"] == "BTCUSDT"
    assert not any(k.startswith("E23") for k in env)
    assert "PATH" in env and env["HOME"] != __import__("os").environ.get("HOME")


def test_fill_env_has_no_dead_proxy(env_, monkeypatch):
    env_.stub.fill_fail = {}
    assert env_.run("fill", "--set", "CAL", snapshot=False) == 0
    fills = [c for c in env_.stub.calls if "fill_klines" in " ".join(c["cmd"])]
    assert fills
    for c in fills:
        assert "HTTPS_PROXY" not in c["env"] and "http_proxy" not in c["env"]
        assert c["env"]["SCALPER_MARKET_GATE"] == "false"
        assert c["env"]["SCALPER_MARKET_GATE_SYMBOL"] == "BTCUSDT"
        assert c["env"]["SCALPER_MARKET_GATE_RUN_DAYS"] == "3"
        assert (c["env"]["SCALPER_TF_ENTRY"], c["env"]["SCALPER_TF_CONTEXT"], c["env"]["SCALPER_TF_REGIME"]) == \
            ("30m", "4h", "4h")
        assert "--market-gate" in c["cmd"]
        assert c["env"]["BINANCE_API_KEY"] == "dummy"
    monkeypatch.setenv("HTTPS_PROXY", "http://corp:3128")
    env_.stub.calls.clear()
    assert env_.run("fill", "--set", "CAL", snapshot=False) == 0
    assert all(c["env"]["HTTPS_PROXY"] == "http://corp:3128" for c in env_.stub.calls)


def test_fill_triples_and_retry(env_, capsys):
    env_.stub.fill_fail = {"SOLUSDT": 1}
    assert env_.run("fill", "--set", "CAL", snapshot=False) == 0
    fills = [c for c in env_.stub.calls if "fill_klines" in " ".join(c["cmd"])]
    assert len(fills) == 8 + 1                              # tek (30m) uclu; SOLUSDT bir kez yeniden denendi
    assert {c["env"]["SCALPER_TF_ENTRY"] for c in fills} == {"30m"}
    assert all("--market-gate" in c["cmd"] for c in fills)
    out = capsys.readouterr().out
    assert "FILL OK CAL 30m SOLUSDT" in out
    assert out.strip().splitlines()[-1] == "FILL DONE CAL rc=0"
    assert (env_.root / "fill_logs" / "botlogs").is_dir()


def test_fill_persistent_failure(env_, capsys):
    env_.stub.fill_fail = {"ADAUSDT": 99}
    assert env_.run("fill", "--set", "CAL", snapshot=False) == 2
    out = capsys.readouterr().out
    assert "FILL FAIL CAL 30m ADAUSDT" in out and "FILL DONE CAL rc=2" in out


def test_fill_funding_window(env_):
    assert env_.run("fill-funding", "--set", "CAL", snapshot=False) == 0
    cmd = env_.stub.calls[0]["cmd"]
    assert cmd[cmd.index("--start") + 1] == "2026-02-28" and cmd[cmd.index("--end") + 1] == "2026-06-01"
    assert cmd[cmd.index("--symbols") + 1] == env_.variants["symbols"]


def test_cli_fee2_and_dly1(env_):
    assert env_.run("run", "--set", "CAL", "--arms", "K30B-H", "--overlays", "ROB_FEE2,ROB_DLY1") == 0
    c = env_.stub.bt_calls()[0]
    cmd = c["cmd"]
    assert "--fee-stress" in cmd
    assert cmd[cmd.index("--entry-delay-candles") + 1] == "1"
    assert cmd[cmd.index("--strategies") + 1] == "M" and "--diagnostics" in cmd
    assert cmd[cmd.index("--start") + 1] == "2026-03-01" and cmd[cmd.index("--end") + 1] == "2026-06-01"
    assert cmd[cmd.index("--cache-dir") + 1] == str(env_.root / "cache")
    assert cmd[cmd.index("--funding-cache-dir") + 1] == str(env_.root / "funding")
    assert Path(c["cwd"]).name == "cwd"
    assert c["env"]["SCALPER_TF_ENTRY"] == "30m"
    row = json.loads((env_.root / "ledger.jsonl").read_text().splitlines()[0])
    assert row["label"] == "CAL_K30B-H__ROB_FEE2+ROB_DLY1" and row["H"] == "deadbeef"
    assert row["overlays"] == ["ROB_FEE2", "ROB_DLY1"]


def test_label_ledger_skip_and_console(env_, capsys):
    assert env_.run("run", "--set", "CAL", "--arms", "K30B-L") == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1 and out[0].startswith("BT OK CAL_K30B-L ") and out[0].split()[-1].isdigit()
    rep = env_.root / "sealed" / "runs" / "CAL_K30B-L.json"
    assert rep.exists() and (rep.stat().st_mode & 0o777) == 0o400
    n = len(env_.stub.bt_calls())
    assert env_.run("run", "--set", "CAL", "--arms", "K30B-L,K30B-H") == 0
    out = capsys.readouterr().out
    assert "BT SKIP CAL_K30B-L" in out and "BT OK CAL_K30B-H" in out
    assert len(env_.stub.bt_calls()) == n + 1
    assert env_.run("run", "--set", "CAL", "--arms", "K30B-L", "--label", "CAL_X", "--hashseed", "1") == 0
    assert env_.stub.bt_calls()[-1]["env"]["PYTHONHASHSEED"] == "1"
    assert "CAL_X" in env_.root.joinpath("ledger.jsonl").read_text()


def test_snapshot_mismatch(env_, tmp_path):
    env_.snap.write_text(SNAP_TEXT + "SCALPER_EXTRA=1\n")
    assert env_.run("run", "--set", "CAL", "--arms", "K30B-L") == 2
    assert not env_.stub.calls
    env_.snap.write_text(SNAP_TEXT)
    v = json.loads(env_.vp.read_text())
    v["snapshot"]["scalper_lines"] = 99
    env_.vp.write_text(json.dumps(v))
    assert env_.run("run", "--set", "CAL", "--arms", "K30B-L") == 2
    assert not env_.stub.calls


@pytest.mark.parametrize("n", [0, 2])
def test_report_count_failure(env_, capsys, n):
    env_.stub.n_reports = n
    rc = env_.run("run", "--set", "CAL", "--arms", "K30B-L")
    assert rc == 97
    # n=0 + rc 0 -> 'report' asamasi; ledger yok, rapor yok
    assert "BT FAIL CAL_K30B-L report" in capsys.readouterr().out
    assert not (env_.root / "ledger.jsonl").exists()
    assert not (env_.root / "sealed" / "runs" / "CAL_K30B-L.json").exists()


def test_infra_retry_once(env_, capsys):
    env_.stub.bt_rc = 1
    env_.stub.n_reports = 0
    assert env_.run("run", "--set", "CAL", "--arms", "K30B-L") == 97
    assert len(env_.stub.bt_calls()) == 2
    assert "altyapi" in (env_.root / "CHAIN.log").read_text()
    assert "BT FAIL CAL_K30B-L backtest" in capsys.readouterr().out


def test_cache_listing_change(env_, capsys):
    env_.stub.touch_cache = True
    assert env_.run("run", "--set", "CAL", "--arms", "K30B-L") == 97
    assert "BT FAIL CAL_K30B-L cache" in capsys.readouterr().out
    assert not (env_.root / "ledger.jsonl").exists()
    assert not (env_.root / "sealed" / "runs" / "CAL_K30B-L.json").exists()


def test_analyzer_failure_deletes_report(env_, capsys):
    env_.stub.check_rc = 97
    assert env_.run("run", "--set", "CAL", "--arms", "K30B-L,K30B-H") == 97
    assert not (env_.root / "sealed" / "runs" / "CAL_K30B-L.json").exists()
    assert not (env_.root / "ledger.jsonl").exists()
    assert capsys.readouterr().out.strip() == "BT FAIL CAL_K30B-L check"
    chk = [c for c in env_.stub.calls if "check-run" in c["cmd"]][0]["cmd"]
    assert chk[-4:] == ["--root", str(env_.root), "--label", "CAL_K30B-L"]
    assert len(env_.stub.bt_calls()) == 1  # zincir durdu


def test_smoke(env_, capsys, tmp_path):
    mc, mf = tmp_path / "mc", tmp_path / "mf"
    mc.mkdir()
    mf.mkdir()
    (mc / "k.json").write_text("k")
    assert env_.run("smoke", "--main-cache", str(mc), "--main-funding", str(mf)) == 0
    out = capsys.readouterr().out
    assert "SMOKE S1 OK" in out and "SMOKE S2 OK" in out
    bt = env_.stub.bt_calls()
    assert len(bt) == 3
    s1 = bt[0]
    assert s1["env"]["SCALPER_ENTRY_MODE"] == "maker" and "SCALPER_TF_ENTRY" not in s1["env"]
    assert s1["cmd"][s1["cmd"].index("--strategies") + 1] == "C"
    assert s1["cmd"][s1["cmd"].index("--cache-dir") + 1] == str(mc)
    assert [b["env"]["PYTHONHASHSEED"] for b in bt[1:]] == ["0", "1"]
    for lab in ("S1_AYI_C", "S2_K30B-L_h0", "S2_K30B-L_h1"):
        assert (env_.root / "sealed" / "runs" / f"{lab}.json").exists()
    assert any("s2-compare" in c["cmd"] for c in env_.stub.calls)


def test_smoke_s2_compare_failure(env_, capsys, tmp_path):
    mc, mf = tmp_path / "mc", tmp_path / "mf"
    mc.mkdir()
    mf.mkdir()
    base = rr._spawn

    def spawn(cmd, env, cwd, log, timeout):
        if "s2-compare" in cmd:
            return 97
        return base(cmd, env, cwd, log, timeout)

    rr._spawn = spawn
    try:
        assert env_.run("smoke", "--main-cache", str(mc), "--main-funding", str(mf)) == 97
    finally:
        rr._spawn = base
    assert "SMOKE S2 FAIL" in capsys.readouterr().out


def _git(*a, cwd):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd, check=True,
                   capture_output=True)


def test_init_clone_and_manifest(env_, tmp_path, capsys):
    src = tmp_path / "src"
    src.mkdir()
    _git("init", "-q", cwd=src)
    (src / "f.txt").write_text("1")
    _git("add", ".", cwd=src)
    _git("commit", "-q", "-m", "c1", cwd=src)
    h = subprocess.run(["git", "rev-parse", "HEAD"], cwd=src, capture_output=True, text=True).stdout.strip()
    root = tmp_path / "r2"
    argv = ["--root", str(root), "--variants", str(env_.vp)]
    assert rr.main(argv + ["init-clone", "--commit", h, "--source", str(src)]) == 0
    assert (root / "H.txt").read_text().strip() == h
    assert rr.main(argv + ["init-clone", "--commit", h, "--source", str(src)]) == 0
    (root / "clone_H" / "dirty.txt").write_text("x")
    assert rr.main(argv + ["init-clone", "--commit", h, "--source", str(src)]) == 2
    assert env_.run("manifest", snapshot=False) == 0
    m = json.loads((env_.root / "MANIFEST.data.json").read_text())
    assert m["cache"] == {"a.json": hashlib.sha256(b"a").hexdigest()} and m["funding"] == {}
