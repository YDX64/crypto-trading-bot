#!/usr/bin/env python3
"""E23 kosucu: izole, tekrarlanabilir, MUHURLU (konsola sonuc basmaz) backtest orkestratoru.

Plan: docs/audits/2026-11-24-prereg-e23/PLAN.md (§3.2, §3.4, §4, §5, §6, Ek A).
Yalniz stdlib + e23_common. Konsola YALNIZ 'BT OK|FAIL|SKIP', 'FILL OK|FAIL|DONE',
'SMOKE S1|S2 OK|FAIL', 'INIT ...', 'MANIFEST ...' satirlari yazilir; PnL/PF/islem sayisi ASLA.

Alt komutlar: init-clone, fill, fill-funding, manifest, run, smoke.
Cikis kodlari: 0 tamam, 2 yapilandirma/doldurma hatasi, 97 zincir hatasi (muhur koruma).
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
import e23_common as ec  # noqa: E402

DEAD_PROXY = "http://127.0.0.1:9"
BT_TIMEOUT_S = 45 * 60
FILL_TIMEOUT_S = 4 * 3600
FUNDING_TIMEOUT_S = 2 * 3600
BT_ATTEMPTS = 2
PROXY_KEYS = ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "SSL_CERT_FILE",
              "https_proxy", "http_proxy", "no_proxy")
EXIT_CONFIG = 2
EXIT_CHAIN = 97


class ConfigError(Exception):
    pass


# ── Tek surec baslatma noktasi (testlerde monkeypatch edilir) ───────────────

def _spawn(cmd: Sequence[str], env: dict, cwd: str, log_path: str, timeout: float) -> int:
    """cmd'yi env/cwd ile calistirir, stdout+stderr -> log_path. Donus: rc (zaman asimi 124, baslamadi 127)."""
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "wb") as log:
        try:
            proc = subprocess.Popen(list(cmd), env=env, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                                    start_new_session=True)
        except OSError:
            return 127
        try:
            return proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                proc.kill()
            proc.wait()
            return 124


# ── Yardimcilar ─────────────────────────────────────────────────────────────

def _utc() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_snapshot(path: Path, variants: dict) -> list[tuple[str, str]]:
    """Dogrular (sha256 + SCALPER_ satir sayisi) ve (anahtar, deger) listesi doner."""
    p = Path(path)
    if not p.is_file():
        raise ConfigError("snapshot dosyasi yok")
    want = variants["snapshot"]
    if ec.sha256_file(p) != want["sha256"]:
        raise ConfigError("snapshot sha256 kilitle uyusmuyor")
    pairs: list[tuple[str, str]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.rstrip("\r")
        if line.startswith("SCALPER_") and "=" in line:
            k, _, v = line.partition("=")
            pairs.append((k, v))
    if len(pairs) != int(want["scalper_lines"]):
        raise ConfigError("snapshot SCALPER_ satir sayisi kilitle uyusmuyor")
    return pairs


def _path_env() -> str:
    return os.environ.get("PATH", "/usr/bin:/bin")


def build_run_env(arm_env: dict, snapshot: Sequence[tuple[str, str]], tmp: Path, clone: Path,
                  hashseed: int = 0) -> dict[str, str]:
    """Sifirdan kurulan kosu ortami (ebeveyn ortami ASLA miras alinmaz)."""
    env: dict[str, str] = {
        "PATH": _path_env(),
        "HOME": str(tmp),
        "PYTHONPATH": str(clone),
        "PYTHONHASHSEED": str(hashseed),
        "PYTHONDONTWRITEBYTECODE": "1",
        "TRADINGBOT_LOG_DIR": str(tmp / "botlogs"),
    }
    env.update(ec.DUMMY_SECRETS)
    for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
        env[k] = DEAD_PROXY
    for k, v in snapshot:
        env[k] = v
    env.update(arm_env)
    return env


def build_fill_env(triple: Sequence[str], clone: Path, root: Path, variants: dict) -> dict[str, str]:
    """Doldurma ortami: asgari + ag icin vekil/sertifika gecisi (ag GEREKLI).

    Lider (BTC 30m/15m/1d) onbellek dosyalari, backtest kosusuyla AYNI anahtarlarla yazilsin diye
    SCALPER_MARKET_GATE_SYMBOL ve SCALPER_MARKET_GATE_RUN_DAYS variants base_env'den gelir."""
    base = variants["base_env"]
    env: dict[str, str] = {
        "PATH": _path_env(),
        "HOME": os.environ.get("HOME", str(root)),
        "PYTHONPATH": str(clone),
        "PYTHONDONTWRITEBYTECODE": "1",
        "TRADINGBOT_LOG_DIR": str(root / "fill_logs" / "botlogs"),
        "SCALPER_TF_ENTRY": triple[0],
        "SCALPER_TF_CONTEXT": triple[1],
        "SCALPER_TF_REGIME": triple[2],
        "SCALPER_MARKET_GATE": "false",
        "SCALPER_MARKET_GATE_SYMBOL": str(base["SCALPER_MARKET_GATE_SYMBOL"]),
        "SCALPER_MARKET_GATE_RUN_DAYS": str(base["SCALPER_MARKET_GATE_RUN_DAYS"]),
    }
    env.update(ec.DUMMY_SECRETS)
    for k in PROXY_KEYS:
        if k in os.environ:
            env[k] = os.environ[k]
    return env


def _listing(*dirs: Path) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for d in dirs:
        if d.is_dir():
            for f in sorted(d.rglob("*")):
                if f.is_file():
                    out.append((str(f), f.stat().st_size))
    return out


def _chain_log(paths: ec.Paths, msg: str) -> None:
    paths.root.mkdir(parents=True, exist_ok=True)
    with open(paths.root / "CHAIN.log", "a", encoding="utf-8") as fh:
        fh.write(f"{_utc()} {msg}\n")


def _read_h(paths: ec.Paths) -> str:
    h = paths.root / "H.txt"
    if not h.is_file():
        raise ConfigError("H.txt yok (once init-clone)")
    return h.read_text(encoding="utf-8").strip()


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True)


# ── init-clone ──────────────────────────────────────────────────────────────

def cmd_init_clone(paths: ec.Paths, commit: str, source: str) -> int:
    clone = paths.clone
    paths.root.mkdir(parents=True, exist_ok=True)
    if clone.exists():
        head = _git("-C", str(clone), "rev-parse", "HEAD")
        want = _git("-C", str(clone), "rev-parse", "--verify", f"{commit}^{{commit}}")
        dirty = _git("-C", str(clone), "status", "--porcelain")
        if (head.returncode or want.returncode or dirty.returncode or
                head.stdout.strip() != want.stdout.strip() or dirty.stdout.strip()):
            print("INIT FAIL klon HEAD != H ya da kirli", file=sys.stderr)
            return EXIT_CONFIG
    else:
        r = _git("clone", "--quiet", source, str(clone))
        if r.returncode:
            print("INIT FAIL clone", file=sys.stderr)
            return EXIT_CONFIG
        r = _git("-C", str(clone), "checkout", "--quiet", "--detach", commit)
        if r.returncode:
            print("INIT FAIL checkout", file=sys.stderr)
            return EXIT_CONFIG
    full = _git("-C", str(clone), "rev-parse", "HEAD").stdout.strip()
    (paths.root / "H.txt").write_text(full + "\n", encoding="utf-8")
    print(f"INIT OK {full}")
    return 0


# ── fill / fill-funding / manifest ──────────────────────────────────────────

def _distinct_triples(variants: dict) -> list[list[str]]:
    seen: list[list[str]] = []
    for arm in ec.arm_ids(variants):
        t = ec.triple_of(variants, arm)
        if t not in seen:
            seen.append(t)
    return seen


def cmd_fill(paths: ec.Paths, variants: dict, py: str, set_name: str) -> int:
    start, end = ec.set_window(variants, set_name)
    symbols = [s for s in variants["symbols"].split(",") if s]
    paths.cache.mkdir(parents=True, exist_ok=True)
    paths.vision.mkdir(parents=True, exist_ok=True)
    (paths.fill_logs / "botlogs").mkdir(parents=True, exist_ok=True)
    script = paths.clone / "scripts" / "fill_klines_cache_from_vision.py"
    failed_any = False

    for triple in _distinct_triples(variants):
        env = build_fill_env(triple, paths.clone, paths.root, variants)

        def one(sym: str, triple=triple, env=env) -> int:
            cmd = [py, str(script), "--symbols", sym, "--start", start, "--end", end,
                   "--cache-dir", str(paths.cache), "--download-cache", str(paths.vision),
                   "--market-gate"]
            log = paths.fill_logs / f"{set_name}_{triple[0]}_{sym}.log"
            return _spawn(cmd, env, str(paths.clone), str(log), FILL_TIMEOUT_S)

        results: dict[str, int] = {}
        with cf.ThreadPoolExecutor(max_workers=8) as ex:
            for sym, rc in zip(symbols, ex.map(one, symbols)):
                results[sym] = rc
        retry = [s for s in symbols if results[s] != 0]
        if retry:
            with cf.ThreadPoolExecutor(max_workers=4) as ex:
                for sym, rc in zip(retry, ex.map(one, retry)):
                    results[sym] = rc
        for sym in symbols:
            ok = results[sym] == 0
            failed_any = failed_any or not ok
            print(f"FILL {'OK' if ok else 'FAIL'} {set_name} {triple[0]} {sym}")
    rc = 2 if failed_any else 0
    print(f"FILL DONE {set_name} rc={rc}")
    return rc


def cmd_fill_funding(paths: ec.Paths, variants: dict, py: str, set_name: str) -> int:
    start, end = ec.set_window(variants, set_name)
    start_m1 = (dt.date.fromisoformat(start) - dt.timedelta(days=1)).isoformat()
    paths.funding.mkdir(parents=True, exist_ok=True)
    (paths.fill_logs / "botlogs").mkdir(parents=True, exist_ok=True)
    env = build_fill_env(ec.triple_of(variants, ec.arm_ids(variants)[0]), paths.clone, paths.root, variants)
    cmd = [py, str(paths.clone / "scripts" / "fill_funding_cache.py"), "--symbols", variants["symbols"],
           "--start", start_m1, "--end", end, "--cache-dir", str(paths.funding)]
    rc = _spawn(cmd, env, str(paths.clone), str(paths.fill_logs / f"FUNDING_{set_name}.log"), FUNDING_TIMEOUT_S)
    ok = rc == 0
    print(f"FILL {'OK' if ok else 'FAIL'} {set_name} funding")
    return 0 if ok else 2


def cmd_manifest(paths: ec.Paths) -> int:
    def sums(d: Path) -> dict[str, str]:
        if not d.is_dir():
            return {}
        return {str(f.relative_to(d)): ec.sha256_file(f) for f in sorted(d.rglob("*")) if f.is_file()}

    data = {"cache": sums(paths.cache), "funding": sums(paths.funding)}
    paths.manifest.write_text(json.dumps(data, sort_keys=True, indent=1) + "\n", encoding="utf-8")
    print(f"MANIFEST OK cache={len(data['cache'])} funding={len(data['funding'])}")
    return 0


# ── backtest kosusu ─────────────────────────────────────────────────────────

class _Ctx:
    def __init__(self, paths, variants, py, analyzer, snapshot):
        self.paths, self.variants, self.py, self.analyzer, self.snapshot = paths, variants, py, analyzer, snapshot


def _analyzer_env(paths: ec.Paths) -> dict[str, str]:
    return {"PATH": _path_env(), "HOME": os.environ.get("HOME", str(paths.root)),
            "PYTHONDONTWRITEBYTECODE": "1"}


def _check_run(ctx: _Ctx, label: str) -> int:
    log = ctx.paths.root / "sealed" / "check" / f"{label}.log"
    cmd = [ctx.py, str(ctx.analyzer), "check-run", "--root", str(ctx.paths.root), "--label", label]
    return _spawn(cmd, _analyzer_env(ctx.paths), str(ctx.paths.clone), str(log), 600)


def _run_one(ctx: _Ctx, label: str, *, arm_env: dict, cli: list[str], start: str, end: str,
             cache: Path, funding: Path, hashseed: int, ledger_meta: Optional[dict],
             readonly_dirs: Sequence[Path]) -> int:
    """Tek backtest kosusu (PLAN §5). Donus: 0 ok, 97 hata (konsola 'BT FAIL <etiket> <asama>')."""
    p = ctx.paths
    h = _read_h(p)
    p.sealed_runs.mkdir(parents=True, exist_ok=True)
    p.sealed_logs.mkdir(parents=True, exist_ok=True)
    report, logf = p.report(label), p.log(label)
    for stale in (report, logf):
        if stale.exists():
            stale.unlink()

    def fail(stage: str) -> int:
        if report.exists():
            report.unlink()
        print(f"BT FAIL {label} {stage}")
        return EXIT_CHAIN

    before = _listing(*readonly_dirs)
    t0 = time.monotonic()
    done = False
    for attempt in range(1, BT_ATTEMPTS + 1):
        with tempfile.TemporaryDirectory(prefix="e23.") as td:
            tmp = Path(td)
            cwd = tmp / "cwd"
            cwd.mkdir()
            env = build_run_env(arm_env, ctx.snapshot, tmp, p.clone, hashseed)
            cmd = [ctx.py, "-m", "src.strategies.scalper.backtest", *cli, "--start", start, "--end", end,
                   "--cache-dir", str(cache), "--funding-cache-dir", str(funding)]
            rc = _spawn(cmd, env, str(cwd), str(logf), BT_TIMEOUT_S)
            reports = sorted((cwd / "logs").glob("backtest_*.json")) if (cwd / "logs").is_dir() else []
            if rc != 0 and not reports and attempt < BT_ATTEMPTS:
                _chain_log(p, f"{label} altyapi hatasi rc={rc}, yeniden deneniyor")
                continue
            if rc != 0:
                return fail("backtest")
            if len(reports) != 1:
                return fail("report")
            shutil.copyfile(reports[0], report)
            os.chmod(report, 0o400)
            done = True
            break
    if not done:
        return fail("backtest")
    if _listing(*readonly_dirs) != before:
        return fail("cache")
    if _check_run(ctx, label) != 0:
        return fail("check")
    seconds = int(round(time.monotonic() - t0))
    if ledger_meta is not None:
        row = {"label": label, **ledger_meta, "utc": _utc(), "seconds": seconds,
               "report_sha256": ec.sha256_file(report), "H": h}
        with open(p.ledger, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    print(f"BT OK {label} {seconds}")
    return 0


def cmd_run(ctx: _Ctx, set_name: str, arms: Sequence[str], overlays: Sequence[str],
            hashseed: int, label_override: Optional[str]) -> int:
    v, p = ctx.variants, ctx.paths
    start, end = ec.set_window(v, set_name)
    if label_override and len(arms) != 1:
        raise ConfigError("--label yalniz tek kolla kullanilir")
    for arm in arms:
        ec.get_arm(v, arm)
    for ov in overlays:
        ec.get_overlay(v, ov)
    for arm in arms:
        label = label_override or ec.make_label(set_name, arm, overlays)
        if label in p.ledger_labels():
            print(f"BT SKIP {label}")
            continue
        rc = _run_one(ctx, label, arm_env=ec.resolve_env(v, arm, overlays), cli=ec.resolve_cli(v, overlays),
                      start=start, end=end, cache=p.cache, funding=p.funding, hashseed=hashseed,
                      ledger_meta={"set": set_name, "arm": arm, "overlays": list(overlays)},
                      readonly_dirs=[p.cache])
        if rc:
            return rc
    return 0


def cmd_smoke(ctx: _Ctx, main_cache: Path, main_funding: Path) -> int:
    v, p = ctx.variants, ctx.paths
    s1 = v["smoke"]["S1"]
    rc = _run_one(ctx, "S1_AYI_C", arm_env={},
                  cli=["--strategies", s1["strategy"], "--symbols", v["symbols"]],
                  start=s1["window"][0], end=s1["window"][1], cache=main_cache, funding=main_funding,
                  hashseed=0, ledger_meta=None, readonly_dirs=[main_cache, main_funding])
    print(f"SMOKE S1 {'OK' if rc == 0 else 'FAIL'}")
    if rc:
        return EXIT_CHAIN
    s2 = v["smoke"]["S2"]
    arm = s2["arm"]
    start, end = ec.set_window(v, s2["set"])
    for hs in s2["hashseeds"]:
        rc = _run_one(ctx, f"S2_{arm}_h{hs}", arm_env=ec.resolve_env(v, arm, ()), cli=ec.resolve_cli(v, ()),
                      start=start, end=end, cache=p.cache, funding=p.funding, hashseed=int(hs),
                      ledger_meta=None, readonly_dirs=[p.cache])
        if rc:
            print("SMOKE S2 FAIL")
            return EXIT_CHAIN
    cmp_rc = _spawn([ctx.py, str(ctx.analyzer), "s2-compare", "--root", str(p.root)],
                    _analyzer_env(p), str(p.clone), str(p.root / "sealed" / "check" / "S2_compare.log"), 600)
    print(f"SMOKE S2 {'OK' if cmp_rc == 0 else 'FAIL'}")
    return 0 if cmp_rc == 0 else EXIT_CHAIN


# ── CLI ─────────────────────────────────────────────────────────────────────

def _csv(s: str) -> list[str]:
    return [x for x in s.split(",") if x]


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="E23 muhurlu kosucu")
    ap.add_argument("--root", required=True, help="E23_ROOT")
    ap.add_argument("--snapshot", help="SCALPER_* satirli snapshot (run/smoke icin zorunlu)")
    ap.add_argument("--py", default=sys.executable)
    ap.add_argument("--analyzer", default=str(Path(__file__).resolve().parent / "analyze_e23.py"))
    ap.add_argument("--variants", default=None, help="variants.json (varsayilan: plan klasoru)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("init-clone")
    s.add_argument("--commit", required=True)
    s.add_argument("--source", required=True)
    for name in ("fill", "fill-funding"):
        s = sub.add_parser(name)
        s.add_argument("--set", required=True, dest="set_name")
    sub.add_parser("manifest")
    s = sub.add_parser("run")
    s.add_argument("--set", required=True, dest="set_name")
    s.add_argument("--arms", required=True)
    s.add_argument("--overlays", default="")
    s.add_argument("--hashseed", type=int, default=0)
    s.add_argument("--label", default=None)
    s = sub.add_parser("smoke")
    s.add_argument("--main-cache", required=True)
    s.add_argument("--main-funding", required=True)
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    paths = ec.Paths(Path(args.root))
    try:
        variants = ec.load_variants(Path(args.variants) if args.variants else None)
        if args.cmd == "init-clone":
            return cmd_init_clone(paths, args.commit, args.source)
        if args.cmd == "fill":
            ec.set_window(variants, args.set_name)
            return cmd_fill(paths, variants, args.py, args.set_name)
        if args.cmd == "fill-funding":
            ec.set_window(variants, args.set_name)
            return cmd_fill_funding(paths, variants, args.py, args.set_name)
        if args.cmd == "manifest":
            return cmd_manifest(paths)
        if not args.snapshot:
            raise ConfigError("--snapshot zorunlu")
        snap = load_snapshot(Path(args.snapshot), variants)
        ctx = _Ctx(paths, variants, args.py, args.analyzer, snap)
        if args.cmd == "run":
            ec.set_window(variants, args.set_name)
            arms = ec.arm_ids(variants) if args.arms == "all" else _csv(args.arms)
            return cmd_run(ctx, args.set_name, arms, _csv(args.overlays), args.hashseed, args.label)
        if args.cmd == "smoke":
            return cmd_smoke(ctx, Path(args.main_cache), Path(args.main_funding))
    except (ConfigError, KeyError, ValueError) as exc:
        print(f"E23 HATA: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    return EXIT_CONFIG


if __name__ == "__main__":
    sys.exit(main())
