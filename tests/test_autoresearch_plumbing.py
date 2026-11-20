"""scripts/autoresearch.py + scripts/run_windows.sh tesisat testleri (paket B1).

Kapsam: sonuc onbellegi anahtari (sha + kirli-agac parmak izi + env-file hash +
override + pencere + bayrak), .env izolasyonu, rapor yolunun AD ile eslenmesi
(mtime degil), PF=null -> +inf, run_windows.sh (bos etiket / cikis kodu /
TRADINGBOT_LOG_DIR). Gercek fonksiyonlar kosulur; ag/ssh/sunucu yok.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import autoresearch as ar  # noqa: E402

RUN_WINDOWS_SH = SCRIPTS_DIR / "run_windows.sh"


# --------------------------------------------------------------------------
# 1. Sonuc onbellegi anahtari
# --------------------------------------------------------------------------

_BASE_KEY_ARGS = dict(
    git_sha="aaa111",
    dirty_fingerprint="d1",
    env_hash="e1",
    overrides={"SCALPER_X": "1"},
    windows=[("AYI", "2026-01-23", "2026-02-13")],
    flags=["--strategies", "C", "--symbols", "BTCUSDT"],
    baseline_source="run",
)


def test_result_key_is_deterministic_and_order_insensitive_for_overrides() -> None:
    k1 = ar.compute_result_key(**_BASE_KEY_ARGS)
    args2 = dict(_BASE_KEY_ARGS, overrides={"SCALPER_X": "1"})
    assert ar.compute_result_key(**args2) == k1
    two_a = ar.compute_result_key(**dict(_BASE_KEY_ARGS, overrides={"A": "1", "B": "2"}))
    two_b = ar.compute_result_key(**dict(_BASE_KEY_ARGS, overrides={"B": "2", "A": "1"}))
    assert two_a == two_b


@pytest.mark.parametrize(
    "field,value",
    [
        ("git_sha", "bbb222"),
        ("dirty_fingerprint", "d2"),
        ("env_hash", "e2"),
        ("overrides", {"SCALPER_X": "2"}),
        ("windows", [("AYI", "2026-01-23", "2026-02-14")]),
        ("flags", ["--strategies", "C", "--symbols", "ETHUSDT"]),
        ("baseline_source", "abc"),
    ],
)
def test_result_key_changes_when_any_component_changes(field, value) -> None:
    base = ar.compute_result_key(**_BASE_KEY_ARGS)
    changed = ar.compute_result_key(**dict(_BASE_KEY_ARGS, **{field: value}))
    assert changed["digest"] != base["digest"]


def test_result_key_keeps_components_for_audit() -> None:
    key = ar.compute_result_key(**_BASE_KEY_ARGS)
    assert key["git_sha"] == "aaa111"
    assert key["env_hash"] == "e1"
    assert key["dirty_fingerprint"] == "d1"
    assert key["overrides"] == {"SCALPER_X": "1"}


def test_should_skip_candidate_requires_matching_key() -> None:
    key = ar.compute_result_key(**_BASE_KEY_ARGS)
    other = ar.compute_result_key(**dict(_BASE_KEY_ARGS, git_sha="bbb222"))
    summary = {"candidates": {"E1": {"status": "ok", "key": key}}}
    assert ar.should_skip_candidate(summary, "E1", key) is True
    assert ar.should_skip_candidate(summary, "E1", other) is False
    # Anahtarsiz (eski) kayit, anahtar istenince ASLA yeniden kullanilmaz.
    legacy = {"candidates": {"E1": {"status": "ok"}}}
    assert ar.should_skip_candidate(legacy, "E1", key) is False
    # hata her halde yeniden denenir
    bad = {"candidates": {"E1": {"status": "hata", "key": key}}}
    assert ar.should_skip_candidate(bad, "E1", key) is False


def test_baseline_reusable_requires_matching_key() -> None:
    key = ar.compute_result_key(**_BASE_KEY_ARGS)
    other = ar.compute_result_key(**dict(_BASE_KEY_ARGS, env_hash="zzz"))
    summary = {"baseline": {"status": "ok", "windows": {}, "key": key}}
    assert ar.baseline_reusable(summary, key) is True
    assert ar.baseline_reusable(summary, other) is False
    assert ar.baseline_reusable({"baseline": {"status": "ok", "windows": {}}}, key) is False
    assert ar.baseline_reusable({"baseline": {"status": "hata", "key": key}}, key) is False


def test_env_content_hash_is_sha256_of_bytes(tmp_path: Path) -> None:
    p = tmp_path / "env.txt"
    p.write_bytes(b"SCALPER_A=1\n")
    assert ar.env_content_hash(str(p)) == hashlib.sha256(b"SCALPER_A=1\n").hexdigest()
    p.write_bytes(b"SCALPER_A=2\n")
    assert ar.env_content_hash(str(p)) == hashlib.sha256(b"SCALPER_A=2\n").hexdigest()


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout


def test_dirty_fingerprint_tracks_src_and_scripts_diff(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    (repo / "src").mkdir(parents=True)
    (repo / "scripts").mkdir()
    (repo / "docs").mkdir()
    (repo / "src" / "a.py").write_text("x = 1\n")
    (repo / "scripts" / "s.py").write_text("y = 1\n")
    (repo / "docs" / "d.md").write_text("d\n")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")

    clean = ar.git_dirty_fingerprint(repo)
    assert clean == hashlib.sha256(b"").hexdigest()  # temiz agac = bos diff

    (repo / "docs" / "d.md").write_text("changed\n")
    assert ar.git_dirty_fingerprint(repo) == clean  # docs kapsam disi

    (repo / "scripts" / "s.py").write_text("y = 2\n")
    dirty1 = ar.git_dirty_fingerprint(repo)
    assert dirty1 != clean
    (repo / "scripts" / "s.py").write_text("y = 3\n")
    assert ar.git_dirty_fingerprint(repo) != dirty1
    (repo / "scripts" / "s.py").write_text("y = 1\n")
    (repo / "src" / "a.py").write_text("x = 2\n")
    assert ar.git_dirty_fingerprint(repo) not in (clean, dirty1)


def _stub_ok_windows() -> dict:
    return {
        w: {"trades": 100, "winrate": 60.0, "total_pnl": 10.0,
            "profit_factor": 1.2, "max_drawdown": 5.0,
            # P2-v2 islem serisi ister (yoksa sonuc yeniden kosulur): pencere
            # basinda tek islem; yalniz onbellek-yeniden-kullanim yolu sinanir.
            "trade_pnls": [[int(datetime.fromisoformat(start).replace(
                tzinfo=timezone.utc).timestamp() * 1000) + 3_600_000, 10.0]]}
        for w, start, _ in ar.WINDOWS
    }


def _setup_main(tmp_path: Path, monkeypatch, sha: str = "sha-A", dirty: str = "dirty-0"):
    """main()'i tmp REPO_ROOT'a yonlendirir; yalniz git/backtest kosusu sahtelenir."""
    monkeypatch.setattr(ar, "REPO_ROOT", tmp_path)
    (tmp_path / "docs").mkdir(exist_ok=True)  # append_experiments_rows docs/ var sayar (gercek repoda var)
    monkeypatch.setattr(ar, "ensure_clean_src", lambda repo_root=None: None)
    monkeypatch.setattr(ar, "get_git_sha", lambda repo_root=None: sha)
    monkeypatch.setattr(ar, "git_dirty_fingerprint", lambda repo_root=None: dirty)
    calls: list = []

    def fake_run_variant(variant_id, overrides, baseline_env, symbols, cache_dir, out_dir, repo_root=None, **kw):
        calls.append((variant_id, dict(overrides)))
        return "ok", _stub_ok_windows()

    monkeypatch.setattr(ar, "run_variant", fake_run_variant)
    return calls


def _write_inputs(tmp_path: Path, env_text: str = "SCALPER_LEVERAGE=7\n", overrides=None):
    env = tmp_path / "env.txt"
    env.write_text(env_text)
    cands = tmp_path / "cands.json"
    cands.write_text(json.dumps([{
        "id": "E1", "hypothesis": "h", "overrides": overrides or {"SCALPER_MAX_POSITIONS": "4"},
    }]))
    return env, cands


def _summary(tmp_path: Path) -> dict:
    files = list((tmp_path / "logs" / "autoresearch").glob("*/summary.json"))
    assert len(files) == 1
    return json.loads(files[0].read_text())


def test_main_reuses_results_only_on_exact_key_match(tmp_path: Path, monkeypatch) -> None:
    calls = _setup_main(tmp_path, monkeypatch)
    env, cands = _write_inputs(tmp_path)
    argv = ["--env-file", str(env), "--candidates", str(cands)]

    assert ar.main(argv) == 0
    assert [c[0] for c in calls] == ["BASELINE", "E1"]

    calls.clear()
    assert ar.main(argv) == 0
    assert calls == []  # her sey ayni -> yeniden kullanildi


def test_main_reruns_when_sha_changes_and_keeps_old_sha(tmp_path: Path, monkeypatch) -> None:
    calls = _setup_main(tmp_path, monkeypatch, sha="sha-A")
    env, cands = _write_inputs(tmp_path)
    argv = ["--env-file", str(env), "--candidates", str(cands)]
    ar.main(argv)
    first = _summary(tmp_path)
    assert first["git_sha"] == "sha-A"
    assert first["baseline"]["key"]["git_sha"] == "sha-A"

    calls.clear()
    monkeypatch.setattr(ar, "get_git_sha", lambda repo_root=None: "sha-B")
    ar.main(argv)
    assert [c[0] for c in calls] == ["BASELINE", "E1"]  # eski sha ile kosan sonuc YENIDEN kosuldu
    second = _summary(tmp_path)
    # ust duzey git_sha eski sonucun sha'sini tasir, sessizce ezilmez
    assert second["git_sha"] == "sha-A"
    assert second["baseline"]["key"]["git_sha"] == "sha-B"
    assert second["candidates"]["E1"]["key"]["git_sha"] == "sha-B"


def test_main_reruns_on_dirty_env_and_override_change(tmp_path: Path, monkeypatch) -> None:
    calls = _setup_main(tmp_path, monkeypatch)
    env, cands = _write_inputs(tmp_path)
    argv = ["--env-file", str(env), "--candidates", str(cands)]
    ar.main(argv)

    # 1) kirli-agac parmak izi degisti
    calls.clear()
    monkeypatch.setattr(ar, "git_dirty_fingerprint", lambda repo_root=None: "dirty-1")
    ar.main(argv)
    assert [c[0] for c in calls] == ["BASELINE", "E1"]

    # 2) env-file icerigi degisti
    calls.clear()
    env.write_text("SCALPER_LEVERAGE=8\n")
    ar.main(argv)
    assert [c[0] for c in calls] == ["BASELINE", "E1"]

    # 3) ayni id, degisen override: yalniz aday yeniden kosar
    calls.clear()
    cands.write_text(json.dumps([{
        "id": "E1", "hypothesis": "h", "overrides": {"SCALPER_MAX_POSITIONS": "6"},
    }]))
    ar.main(argv)
    assert [c[0] for c in calls] == ["E1"]
    assert calls[0][1] == {"SCALPER_MAX_POSITIONS": "6"}


# --------------------------------------------------------------------------
# 2. .env izolasyonu
# --------------------------------------------------------------------------


def test_subprocess_env_drops_inherited_scalper_and_real_secrets(monkeypatch) -> None:
    monkeypatch.setenv("SCALPER_LEAKED", "1")
    monkeypatch.setenv("scalper_lower_leak", "1")
    monkeypatch.setenv("BINANCE_API_KEY", "REAL-KEY-VALUE")
    monkeypatch.setenv("BINANCE_API_SECRET", "REAL-SECRET-VALUE")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///leak.db")
    env = ar.build_subprocess_env({"SCALPER_LEVERAGE": "7"}, Path("/x/botlog"))
    assert env["SCALPER_LEVERAGE"] == "7"
    assert "SCALPER_LEAKED" not in env and "scalper_lower_leak" not in env
    assert "DATABASE_URL" not in env
    assert env["BINANCE_API_KEY"] != "REAL-KEY-VALUE"
    assert env["BINANCE_API_SECRET"] != "REAL-SECRET-VALUE"
    assert env["TRADINGBOT_LOG_DIR"] == "/x/botlog"
    assert "PATH" in env


def test_subprocess_env_dummy_secrets_win_over_env_file_secrets() -> None:
    env = ar.build_subprocess_env(
        {"SCALPER_A": "1", "BINANCE_API_KEY": "from-env-file"}, Path("/x"),
    )
    assert env["BINANCE_API_KEY"] != "from-env-file"


def test_require_scalper_env_errors() -> None:
    with pytest.raises(ar.EnvFileError, match="SCALPER_"):
        ar.require_scalper_env({"FOO": "1"}, "snap.txt")
    with pytest.raises(ar.EnvFileError, match="SCALPER_"):
        ar.require_scalper_env({}, "snap.txt")
    ar.require_scalper_env({"SCALPER_A": "1"}, "snap.txt")  # sorun yok


def test_main_fails_clearly_without_scalper_keys(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_main(tmp_path, monkeypatch)
    _, cands = _write_inputs(tmp_path)
    empty = tmp_path / "empty.txt"
    empty.write_text("# nothing\nFOO=bar\n")
    assert ar.main(["--env-file", str(empty), "--candidates", str(cands)]) == 1
    assert "SCALPER_" in capsys.readouterr().err
    assert ar.main(["--env-file", str(tmp_path / "missing.txt"), "--candidates", str(cands)]) == 1
    assert "env" in capsys.readouterr().err.lower()
    assert ar.main(["--env-file", "", "--candidates", str(cands)]) == 1


def _run_probe(tmp_path: Path, code: str, prelude: bool) -> str:
    """tmp_path icinde (.env orada) python -c kosar; repo kodu PYTHONPATH ile bulunur."""
    env = ar.build_subprocess_env({"SCALPER_LEVERAGE": "7"}, tmp_path / "botlog")
    env["PYTHONPATH"] = str(REPO_ROOT)
    src = (ar.NO_DOTENV_PRELUDE if prelude else "") + code
    proc = subprocess.run(
        [sys.executable, "-c", src], cwd=tmp_path, env=env, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip().splitlines()[-1]


def test_local_dotenv_scalper_value_does_not_reach_settings(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SCALPER_MAX_POSITIONS=9\nSCALPER_LEVERAGE=3\n")
    code = (
        "from src.core.config import settings\n"
        "print(settings.scalper_max_positions, settings.scalper_leverage)\n"
    )
    # Kontrol: izolasyon OLMADAN yerel .env sizar (testin anlamli oldugunun kaniti).
    assert _run_probe(tmp_path, code, prelude=False) == "9 7"
    # Izolasyonlu: yalniz env-file degeri (leverage=7) gelir; max_positions config varsayilani (3).
    assert _run_probe(tmp_path, code, prelude=True) == "3 7"


# --------------------------------------------------------------------------
# 3 + 4. Rapor yolu atfi ve PF=null -> +inf (sahte backtest modulu ile)
# --------------------------------------------------------------------------

_FAKE_BACKTEST = '''\
import json, os, sys, time
from pathlib import Path

Path("logs").mkdir(exist_ok=True)
Path("env_dump.json").write_text(json.dumps(
    {k: v for k, v in os.environ.items() if k.upper().startswith(("SCALPER_", "BINANCE_"))}
))

def rep(pnl, pf):
    return {"overall": {"trades": 70, "winrate": 60.0, "total_pnl": pnl,
                        "profit_factor": pf, "max_drawdown": 5.0}}

Path("logs/backtest_20260101_000001.json").write_text(json.dumps(rep(111.0, None)))
print("Backtest raporu yazildi: logs/backtest_20260101_000001.json")
sys.stdout.flush()
time.sleep(0.05)
# Sonradan yazilan, mtime'i DAHA YENI baska bir rapor (esli kosu / eski dosya benzeri).
Path("logs/backtest_20260101_000002.json").write_text(json.dumps(rep(999.0, 1.5)))
'''


def _make_fake_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "fakerepo"
    pkg = repo / "src" / "strategies" / "scalper"
    pkg.mkdir(parents=True)
    for d in (repo / "src", repo / "src" / "strategies", pkg):
        (d / "__init__.py").write_text("")
    (pkg / "backtest.py").write_text(_FAKE_BACKTEST)
    return repo


def test_run_single_backtest_reads_printed_report_not_newest_file(tmp_path: Path, monkeypatch) -> None:
    repo = _make_fake_repo(tmp_path)
    monkeypatch.setenv("SCALPER_LEAKED", "1")
    monkeypatch.setenv("BINANCE_API_KEY", "REAL-KEY-VALUE")
    ok, metrics = ar.run_single_backtest(
        env_vars={"SCALPER_LEVERAGE": "7"}, symbols="BTCUSDT", start="2026-01-01",
        end="2026-01-02", cache_dir="data/klines_cache",
        log_path=tmp_path / "out" / "run.log", bot_log_dir=tmp_path / "out" / "botlog",
        repo_root=repo,
    )
    assert ok is True
    # Yazdirilan yoldaki rapor (pnl 111) okunur; mtime'a gore en yeni (999) DEGIL.
    assert metrics["total_pnl"] == 111.0
    # null profit_factor -> +inf (variant "hata" sayilmaz).
    assert metrics["profit_factor"] == float("inf")
    dump = json.loads((repo / "env_dump.json").read_text())
    assert dump["SCALPER_LEVERAGE"] == "7"
    assert "SCALPER_LEAKED" not in dump
    assert dump["BINANCE_API_KEY"] != "REAL-KEY-VALUE"


def test_extract_report_path_parses_last_printed_path(tmp_path: Path) -> None:
    out = (
        "x\nBacktest raporu yazildi: logs/backtest_1_1.json\n"
        "noise\n📄 Backtest raporu yazıldı: logs/backtest_2_2.json\n"
    )
    assert ar.extract_report_path(out, tmp_path) == tmp_path / "logs" / "backtest_2_2.json"
    absolute = f"📄 Backtest raporu yazıldı: {tmp_path}/o/backtest_3_3.json\n"
    assert ar.extract_report_path(absolute, tmp_path) == tmp_path / "o" / "backtest_3_3.json"
    assert ar.extract_report_path("hicbir sey", tmp_path) is None


def test_extract_metrics_profit_factor_null_and_inf_string() -> None:
    base = {"trades": 70, "winrate": 60.0, "total_pnl": 50.0, "max_drawdown": 1.0}
    m = ar.extract_metrics_from_json({"overall": dict(base, profit_factor=None)})
    assert m is not None and m["profit_factor"] == float("inf")
    m = ar.extract_metrics_from_json({"overall": dict(base, profit_factor="inf")})
    assert m is not None and m["profit_factor"] == float("inf")
    m = ar.extract_metrics_from_json({"overall": dict(base, profit_factor=1.5)})
    assert m is not None and m["profit_factor"] == 1.5
    # Anahtar HIC yoksa hala hata (null ile anahtar yoklugu ayri).
    assert ar.extract_metrics_from_json({"overall": dict(base)}) is None


def test_variant_with_infinite_pf_is_not_a_failure_in_decide() -> None:
    w = {"trades": 100, "winrate": 70.0, "total_pnl": 10.0,
         "profit_factor": float("inf"), "max_drawdown": 1.0}
    base = {k: dict(w, profit_factor=1.0) for k in ar.WINDOW_IDS}
    var = {k: dict(w) for k in ar.WINDOW_IDS}
    decision, _, _ = ar.decide(base, var)
    assert decision != "HATA"


# --------------------------------------------------------------------------
# 5. scripts/run_windows.sh
# --------------------------------------------------------------------------


def _write_exec(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _run_windows(tmp_path: Path, *, fresh: bool = False, fail_start: str = ""):
    repo = tmp_path / "wrepo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / ".scalper_env_snapshot.txt").write_text("SCALPER_LEVERAGE=7\n")
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.log"
    calls.write_text("")
    _write_exec(
        bin_dir / "timeout",
        '#!/usr/bin/env bash\nshift\nexec "$@"\n',
    )
    _write_exec(
        bin_dir / "python3",
        f"""#!/usr/bin/env bash
if [ "$1" = "-m" ]; then
  start=""; end=""
  while [ $# -gt 0 ]; do
    case "$1" in --start) start="$2";; --end) end="$2";; esac
    shift
  done
  echo "$start|$end|${{TRADINGBOT_LOG_DIR:-UNSET}}" >> "{calls}"
  if [ "$start" = "{fail_start}" ]; then echo boom >&2; exit 3; fi
  mkdir -p logs
  f="logs/backtest_${{start//-/}}_000000.json"
  echo '{{"overall": {{"trades": 70, "total_pnl": 5.0, "profit_factor": 1.5, "max_drawdown": 2.0, "winrate": 60.0}}}}' > "$f"
  echo "Backtest raporu yazildi: $f"
  exit 0
fi
exec "{sys.executable}" "$@"
""",
    )
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["RUN_WINDOWS_REPO"] = str(repo)
    env["RUN_WINDOWS_OUT"] = str(tmp_path / "out")
    env.pop("FRESH", None)
    if fresh:
        env["FRESH"] = "1"
    proc = subprocess.run(
        ["bash", str(RUN_WINDOWS_SH), "t1"], env=env, capture_output=True, text=True,
    )
    lines = [l.split("|") for l in calls.read_text().splitlines()]
    return proc, lines, tmp_path / "out"


def test_run_windows_has_no_empty_window_without_fresh(tmp_path: Path) -> None:
    proc, calls, _ = _run_windows(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert len(calls) == 7
    assert all(c[0] and c[1] for c in calls)  # bos --start/--end ile kosu YOK
    assert not any("FRESH" in l for l in proc.stdout.splitlines())


def test_run_windows_fresh_adds_two_windows(tmp_path: Path) -> None:
    proc, calls, _ = _run_windows(tmp_path, fresh=True)
    assert proc.returncode == 0, proc.stderr
    assert len(calls) == 9
    assert "FRESH_SUBAT" in proc.stdout and "FRESH_TEMMUZ" in proc.stdout


def test_run_windows_exits_nonzero_when_a_window_fails(tmp_path: Path) -> None:
    proc, calls, _ = _run_windows(tmp_path, fail_start="2026-07-01")
    assert proc.returncode != 0
    assert len(calls) == 7  # digerleri yine kosar
    assert "YATAY HATA" in proc.stdout
    assert proc.stdout.count("HATA") == 1


def test_run_windows_sets_tradingbot_log_dir(tmp_path: Path) -> None:
    proc, calls, out = _run_windows(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert {c[2] for c in calls} == {str(out / "_botlog")}


# --------------------------------------------------------------------------
# Takvim: pencere sonu HARIC; sonuc anahtari funding onbellegini ve untracked dosyayi kapsar
# --------------------------------------------------------------------------


def test_window_calendar_excludes_exclusive_end_day() -> None:
    cal = ar._window_calendar()
    expected = sum((date.fromisoformat(e) - date.fromisoformat(s)).days for _, s, e in ar.WINDOWS)
    assert len(cal) == expected == 55
    ayi_end = date.fromisoformat(next(e for n, _, e in ar.WINDOWS if n == "AYI"))
    assert ayi_end not in cal            # [start, end): bitis gunu takvimde yok (rapordaki son cikis end-1)


def test_funding_fingerprint_changes_with_cache_content_and_key_follows(tmp_path: Path) -> None:
    d = tmp_path / "fc"
    d.mkdir()
    empty = ar.funding_cache_fingerprint(d)
    assert ar.funding_cache_fingerprint(tmp_path / "yok") == empty      # yok == bos
    (d / "BTCUSDT.json").write_text('[[1, 0.0001]]')
    f1 = ar.funding_cache_fingerprint(d)
    assert f1 != empty
    (d / "BTCUSDT.json").write_text('[[1, 0.0002]]')                    # ayni ad, ayni boyut, farkli icerik
    f2 = ar.funding_cache_fingerprint(d)
    assert f2 not in (empty, f1)
    k1 = ar.compute_result_key(**dict(_BASE_KEY_ARGS, funding_hash=f1))
    k2 = ar.compute_result_key(**dict(_BASE_KEY_ARGS, funding_hash=f2))
    assert k1["digest"] != k2["digest"]


def test_dirty_fingerprint_includes_untracked_scripts_files(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    (repo / "src").mkdir(parents=True)
    (repo / "scripts").mkdir()
    (repo / "src" / "a.py").write_text("x = 1\n")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    clean = ar.git_dirty_fingerprint(repo)
    assert clean == hashlib.sha256(b"").hexdigest()
    (repo / "scripts" / "new_tool.py").write_text("z = 1\n")          # izlenmeyen dosya
    u1 = ar.git_dirty_fingerprint(repo)
    assert u1 != clean
    (repo / "scripts" / "new_tool.py").write_text("z = 2\n")
    assert ar.git_dirty_fingerprint(repo) != u1
