"""E17 koşu betikleri (scripts/e17/*.sh, envtool.py, verify_variants.py) testleri.

Gerçek ağa / sunucuya / backtest verisine DOKUNULMAZ. Bash betikleri, sahte bir "H klonu"
(gerçek bir git deposu; içinde sahte backtest/doldurucu/analizci) ile UÇTAN UCA koşturulur:
sahte backtest `os.environ`'u, argv'yi ve cwd'yi rapora yazar; test bunları doğrular.
Sentetik veri yalnız burada; DEV/VAL/FINAL penceresinde hiçbir backtest koşmaz.

Sözleşme (PLAN §5.2): env -i geçiş listesi (boş ENTRY_BLOCK_WEEKDAYS_UTC dahil), sabit
sembol sırası, bayat çıktı silme, hata yayılımı (exit 97), konsol sessizliği (PnL yok),
rol-anahtarı çakışması reddi, aşama kapıları, doldurma işçi bölme ve `--market-gate`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
E17 = REPO_ROOT / "scripts" / "e17"
LIB = E17 / "lib17.sh"
sys.path.insert(0, str(E17))

import envtool as ET  # noqa: E402
import verify_variants as VV  # noqa: E402

PINNED = "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,BNBUSDT,ADAUSDT,LTCUSDT"
SECRET_MARK = "0.02137"      # snapshot'a konan ayırt edici değer: hiçbir çıktıda görünmemeli
PNL_MARK = "999.99"          # sahte backtest'in stdout'a bastığı "PnL"
E17_SMOKE_LABELS = ("SMOKE_B0_AYI SMOKE_B0_V1 SMOKE_S15_h0 SMOKE_S15_h1 SMOKE_S5_W SMOKE_T5_W SMOKE_S15_W "
                    "SMOKE_T15_W SMOKE_S30_W SMOKE_T30_W SMOKE_S60_W SMOKE_T60_W SMOKE_R1_M1")

SNAPSHOT_LINES = [
    "# sahte sunucu snapshot'ı",
    "SCALPER_TF_ENTRY=1m", "SCALPER_TF_CONTEXT=5m", "SCALPER_TF_REGIME=15m",
    "SCALPER_MARKET_GATE=true",
    "SCALPER_ENTRY_BLOCK_WEEKDAYS_UTC=5,6", "SCALPER_ENTRY_BLOCK_WEEKDAYS_DIRECTION=LONG",
    "SCALPER_MAX_HOLD_HOURS=8", "SCALPER_LOSS_COOLDOWN_MINUTES=60",
    "SCALPER_ENTRY_MODE=maker", f"SCALPER_MAKER_FEE_PCT={SECRET_MARK}", "SCALPER_TAKER_FEE_PCT=0.05",
    "SCALPER_DYN_LEV_MIN=3", "SCALPER_DYN_LEV_MAX=20", "SCALPER_MAX_STOP_PCT=5.5",
    "SCALPER_FIXED_STOP_ROI_PCT=50", "SCALPER_DYN_LEV_STOP_ATR_MULT=3.0",
    "SCALPER_TP1_ROI=10", "SCALPER_TP2_ROI=25",
    "SCALPER_MAKER_FILL_TIMEOUT_CANDLES=3", "SCALPER_STALE_TP_HOURS=0", "SCALPER_MAX_POSITIONS=5",
    "NOT_SCALPER_KEY=ignored",
]

S_BY_MIN = {5: 5, 15: 9, 30: 13, 60: 16}
TB = {5: (40, 300), 15: (120, 900), 30: (240, 1800), 60: (480, 3600)}
TRIPLES = {5: ("5m", "30m", "1h"), 15: ("15m", "1h", "4h"), 30: ("30m", "4h", "4h"), 60: ("1h", "4h", "4h")}
BG = {"SCALPER_REGIME_FILTER": "false", "SCALPER_MARKET_GATE": "false",
      "SCALPER_ENTRY_BLOCK_WEEKDAYS_UTC": ""}


def make_variants(snapshot_sha: str) -> Dict[str, Any]:
    """Gerçek variants.json (e17-variants/2) yapısının birebir üreticisi."""
    def tb_env(m: int) -> Dict[str, str]:
        return {"SCALPER_MAX_HOLD_HOURS": str(TB[m][0]), "SCALPER_LOSS_COOLDOWN_MINUTES": str(TB[m][1])}

    def tf_env(m: int) -> Dict[str, str]:
        e, c, r = TRIPLES[m]
        return {"SCALPER_TF_ENTRY": e, "SCALPER_TF_CONTEXT": c, "SCALPER_TF_REGIME": r}

    def scaled(m: int) -> Dict[str, str]:
        s = S_BY_MIN[m]
        return {"SCALPER_DYN_LEV_STOP_ATR_MULT": "2", "SCALPER_FIXED_STOP_ROI_PCT": str(s),
                "SCALPER_TP1_ROI": str(s), "SCALPER_TP2_ROI": str(2 * s)}

    def arm(vid: str, m: int, scaled_geo: bool, role: str, gated: bool, sets: List[str]) -> Dict[str, Any]:
        env: Dict[str, str] = dict(tf_env(m))
        if not gated:
            env.update(BG)
        env.update(tb_env(m))
        if scaled_geo:
            env.update(scaled(m))
        return {"id": vid, "role": role, "entry_tf": TRIPLES[m][0], "triple": list(TRIPLES[m]),
                "geometry": f"scaled_S{S_BY_MIN[m]}" if scaled_geo else "server_geometry",
                "S_arm": S_BY_MIN[m] if scaled_geo else 50, "sets": sets, "env": env}

    quarters = [["2025-01-01", "2025-04-01"], ["2025-04-01", "2025-07-01"],
                ["2025-07-01", "2025-10-01"], ["2025-10-01", "2026-01-01"]]
    return {
        "schema": "e17-variants/2",
        "snapshot": {"policy": "test", "sha256": snapshot_sha},
        "common_cli": ["--strategies", "C", "--symbols", PINNED, "--diagnostics"],
        "symbols_order_pinned": PINNED,
        "constants": {"S_by_entry_minutes": {str(k): v for k, v in S_BY_MIN.items()},
                      "TB_table": {str(m): {"SCALPER_MAX_HOLD_HOURS": str(h), "SCALPER_LOSS_COOLDOWN_MINUTES": str(c)}
                                   for m, (h, c) in TB.items()},
                      "BG": BG},
        "family": [
            arm("S5", 5, True, "family", False, ["DEV", "VAL"]), arm("T5", 5, False, "family", False, ["DEV", "VAL"]),
            arm("S15", 15, True, "family", False, ["DEV", "VAL"]), arm("T15", 15, False, "family", False, ["DEV", "VAL"]),
            arm("S30", 30, True, "family", False, ["DEV", "VAL"]), arm("T30", 30, False, "family", False, ["DEV", "VAL"]),
        ],
        "descriptive": [
            arm("S60", 60, True, "descriptive", False, ["DEV", "VAL_if_reached"]),
            arm("T60", 60, False, "descriptive", False, ["DEV", "VAL_if_reached"]),
        ],
        "references": [
            arm("S5G", 5, True, "reference_gated", True, ["DEV"]),
            arm("T5G", 5, False, "reference_gated", True, ["DEV"]),
            {"id": "B0", "role": "reference_live", "entry_tf": "1m", "triple": ["1m", "5m", "15m"],
             "geometry": "server_geometry", "S_arm": 50, "sets": ["DEV_quarterly", "FINAL"], "env": {},
             "dev_quarters": quarters},
            {"id": "R1", "role": "reference_bg_1m", "entry_tf": "1m", "triple": ["1m", "5m", "15m"],
             "geometry": "server_geometry", "S_arm": 50, "sets": ["DEV_quarterly"], "env": dict(BG),
             "dev_quarters": quarters},
        ],
        "overlays": [
            {"id": "ROB_FEE2", "env": {}, "cli": ["--fee-stress"]},
            {"id": "ROB_DLY1", "env": {}, "cli": ["--entry-delay-candles", "1"]},
            {"id": "ROB_TAKER", "env": {"SCALPER_ENTRY_MODE": "taker"}, "cli": []},
            {"id": "ROB_WALL", "env": {"SCALPER_MAX_HOLD_HOURS": "8", "SCALPER_LOSS_COOLDOWN_MINUTES": "60"}, "cli": []},
            {"id": "DIAG_Z0", "env": {"SCALPER_MAKER_FEE_PCT": "0", "SCALPER_TAKER_FEE_PCT": "0",
                                      "SCALPER_SLIPPAGE_RATE": "0"}, "cli": []},
            {"id": "DIAG_SBO", "env": {}, "cli": ["--same-bar-order", "optimistic"]},
            {"id": "DIAG_FEE15", "env": {}, "cli": ["--fee-stress", "--fee-stress-multiplier", "1.5"]},
        ],
        "sets": {"DEV": ["2025-01-01", "2026-01-01"], "VAL": ["2023-01-01", "2024-11-18"],
                 "FINAL": ["2026-09-03", "2026-10-08"], "SMOKE_W": ["2026-04-01", "2026-05-01"],
                 "SMOKE_1M": ["2026-04-01", "2026-04-08"], "S1_AYI": ["2026-01-23", "2026-02-13"],
                 "S1_V1": ["2025-01-06", "2025-01-27"]},
    }


# ── Sahte H klonu ────────────────────────────────────────────────────────────

BACKTEST_STUB = '''\
import json, os, pathlib, sys
CTL = pathlib.Path({ctl!r}); CNT = pathlib.Path({counter!r})
ctl = json.loads(CTL.read_text()) if CTL.exists() else {{}}
n = (int(CNT.read_text()) if CNT.exists() else 0) + 1
CNT.write_text(str(n))
cwd = os.getcwd()
listing = sorted(os.listdir(cwd))
print(ctl.get("stdout", "TOPLAM 1 islem PnL +{pnl} PF 9.99"))
if ctl.get("cache_write"):
    a = sys.argv[1:]
    cd = pathlib.Path(a[a.index("--cache-dir") + 1])
    (cd / "POISON_BTCUSDT_5m.json.gz").write_text("ağdan çekildi")
if n <= ctl.get("fail_first", 0):
    sys.exit(1)
if ctl.get("write_report", True):
    os.makedirs("logs", exist_ok=True)
    pathlib.Path("logs", "backtest_20260101_000000.json").write_text(json.dumps(
        {{"argv": sys.argv[1:], "env": dict(os.environ), "cwd": cwd, "listing": listing, "call": n}}))
sys.exit(ctl.get("rc", 0))
'''

FILL_STUB = '''\
import json, os, pathlib, sys
CTL = pathlib.Path({ctl!r}); REC = pathlib.Path({fills!r}); REC.mkdir(exist_ok=True)
ctl = json.loads(CTL.read_text()) if CTL.exists() else {{}}
a = sys.argv[1:]
sym = a[a.index("--symbols") + 1]; start = a[a.index("--start") + 1]
(REC / (str(os.getpid()) + ".json")).write_text(json.dumps({{"argv": a, "env": dict(os.environ)}}))
if ctl.get("fill_writes_cache"):
    (pathlib.Path(a[a.index("--cache-dir") + 1]) / ("F_%s.json.gz" % os.getpid())).write_text("x")
gaps = list(ctl.get("gaps", []))
if ctl.get("gap"):
    gaps.append(ctl["gap"])
for gap in gaps:
    if start < gap["before"] and gap["symbol"] in sym.split(","):
        kind = gap.get("kind", "404")
        if kind == "checksum":
            print("HATA: CHECKSUM UYUŞMUYOR %s-%s-%s.zip: beklenen aa, hesaplanan bb" % (gap["symbol"], gap["interval"], gap["date"]), file=sys.stderr)
        elif kind == "network":
            print("HATA: İndirilemedi: https://x/%s/%s/%s-%s-%s.zip (URLError)" % (gap["symbol"], gap["interval"], gap["symbol"], gap["interval"], gap["date"]), file=sys.stderr)
        else:
            print("HATA: %s-%s-%s.zip arşivde YOK (404). pencereyi daraltın." % (gap["symbol"], gap["interval"], gap["date"]), file=sys.stderr)
        sys.exit(2)
once = ctl.get("fill_fail_once")
if once and once in sym.split(","):
    mk = REC / ("once_" + once)
    if not mk.exists():
        mk.write_text("x"); sys.exit(2)
if ctl.get("fill_fail_always") and ctl["fill_fail_always"] in sym.split(","):
    sys.exit(2)
print("OZET: 0 dosya")
'''

# Gerçek analizcinin KOMUT/ETİKET dilbilgisini taklit eder (parse_label, manifest hedefleri, LOCK_REF'i
# E17_REPO'dan değil çağıran ortamdan görmesi): uyuşmayan çağrı rc=2 (UsageError) ile reddedilir.
ANALYZER_STUB = '''\
import json, os, pathlib, re, sys
CTL = pathlib.Path({ctl!r}); LOG = pathlib.Path({alog!r}); ENVLOG = pathlib.Path({elog!r})
ctl = json.loads(CTL.read_text()) if CTL.exists() else {{}}
a = sys.argv[1:]
root = pathlib.Path(a[a.index("--root") + 1]); body = a[:a.index("--root")]
with LOG.open("a") as fh:
    fh.write(" ".join(body) + "\\n")
with ENVLOG.open("a") as fh:
    fh.write(json.dumps({{"cmd": body[0], "E17_REPO": os.environ.get("E17_REPO"), "cwd": os.getcwd(),
                         "E17_ROOT": os.environ.get("E17_ROOT"), "E17_SNAPSHOT": os.environ.get("E17_SNAPSHOT")}}) + "\\n")
cmd = body[0]
FAM = {{"S5", "T5", "S15", "T15", "S30", "T30", "S60", "T60"}}
ONLY = {{"AYI": {{"B0"}}, "V1": {{"B0"}}, "h0": {{"S15"}}, "h1": {{"S15"}}, "M1": {{"R1"}}}}


def label_ok(label):
    m = re.fullmatch(r"(DEV|VAL|FINAL|SMOKE)_(.+)", label)
    if not m:
        return False
    if m.group(1) != "SMOKE":
        return True
    mm = re.fullmatch(r"(B0|R1|[ST]\\d+)_(AYI|V1|h0|h1|W|M1)", m.group(2))
    if not mm:
        return False
    v, tag = mm.groups()
    return v in FAM if tag == "W" else v in ONLY[tag]


if cmd == "manifest" and body[1:] and body[1] not in ("smoke", "dev", "val", "final", "runs"):
    print("UsageError: manifest hedefi", file=sys.stderr); sys.exit(2)
if cmd == "check-run":
    if not label_ok(body[1]):
        print("UsageError: etiket", file=sys.stderr); sys.exit(2)
    if body[1] in ctl.get("check_fail", []):
        print("FAIL"); sys.exit(1)
    with (root / "ledger.jsonl").open("a") as fh:
        fh.write(json.dumps({{"label": body[1]}}) + "\\n")
    print("PASS"); sys.exit(0)
if cmd == "smoke":
    ok = ctl.get("smoke_pass", True)
    (root / "SMOKE.json").write_text(json.dumps({{"pass": ok}}))
    sys.exit(0 if ok else 5)
for key, fname in (("screen", "DEV_SCREEN.json"), ("decision", "DEV_DECISION.json"), ("vdecision", "VAL_DECISION.json")):
    want = {{"dev-screen": "screen", "dev-decide": "decision", "val": "vdecision"}}.get(cmd)
    if want == key and key in ctl:
        (root / fname).write_text(json.dumps(ctl[key]))
sys.exit(ctl.get("analyzer_rc", {{}}).get(cmd, 0))
'''

PLAN_REL = "docs/audits/2026-10-10-prereg-tf"


def git(args: List[str], cwd: Path, env: Dict[str, str]) -> str:
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()


def copy_scripts(dst: Path) -> None:
    """Gerçek betik dosyalarını (analizci HARİÇ: onun yerine sahte) hedef dizine kopyalar."""
    dst.mkdir(parents=True, exist_ok=True)
    for f in E17.iterdir():
        if f.is_file() and f.name != "analyze_e17.py":
            shutil.copy2(f, dst / f.name)


class Fx:
    """Bir testin tüm dosya düzeni: sahte DEPO (betik kopyaları + lockdir), sahte kök, snapshot, variants,
    sahte H klonu (betik kopyaları + sahte backtest/doldurucu/analizci). Varsayılan: KİLİT SONRASI kip
    (LOCK_REF.json analizcinin gerçek biçiminde {l,h,lock_sha256,...}); prelock=True kilit öncesi duman."""

    def __init__(self, tmp: Path, variants: Optional[Dict[str, Any]] = None, prelock: bool = False) -> None:
        self.tmp = tmp
        self.prelock = prelock
        self.root = tmp / "e17_root"
        self.home = tmp / "home"
        self.home.mkdir()
        self.snapshot = tmp / "snap.env"
        self.snapshot.write_text("\n".join(SNAPSHOT_LINES) + "\n")
        self.variants_path = tmp / "variants.json"
        self.ctl = tmp / "ctl.json"
        self.counter = tmp / "counter.txt"
        self.fills = tmp / "fills"
        self.alog = tmp / "analyzer.log"
        self.elog = tmp / "analyzer_env.log"
        self.clone = tmp / "clone"
        self.repo = tmp / "repo"
        self.e17dir = self.repo / "scripts" / "e17"
        self.lib = self.e17dir / "lib17.sh"
        self.lockjson = self.repo / PLAN_REL / "LOCK.json"
        self.genv = {"PATH": os.environ["PATH"], "HOME": str(self.home), "GIT_CONFIG_NOSYSTEM": "1",
                     "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                     "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        copy_scripts(self.e17dir)
        self.h = self.build_clone()
        self.write_variants(variants or make_variants(ET.sha256_file(self.snapshot)))
        if not prelock:
            self.write_root_json("LOCK_REF.json", {
                "l": "a" * 40, "h": self.h, "lock_sha256": "b" * 64,
                "lock_path": f"{PLAN_REL}/LOCK.json", "pushed": False, "history": []})
        self.set_ctl()

    def write_variants(self, v: Dict[str, Any]) -> None:
        """variants.json'u yazar ve sahte LOCK.json'daki hash'i tutarlı tutar (kilit ön-kontrolü)."""
        self.variants = v
        self.variants_path.write_text(json.dumps(v))
        self.lockjson.parent.mkdir(parents=True, exist_ok=True)
        self.lockjson.write_text(json.dumps({
            "files": {f"{PLAN_REL}/variants.json": ET.sha256_file(self.variants_path)},
            "snapshot_sha256": ET.sha256_file(self.snapshot)}))

    def build_clone(self) -> str:
        c = self.clone
        for rel in ("src/strategies/scalper",):
            (c / rel).mkdir(parents=True)
        copy_scripts(c / "scripts" / "e17")
        for pkg in ("src", "src/strategies", "src/strategies/scalper"):
            (c / pkg / "__init__.py").write_text("")
        (c / "src/strategies/scalper/backtest.py").write_text(
            BACKTEST_STUB.format(ctl=str(self.ctl), counter=str(self.counter), pnl=PNL_MARK))
        (c / "scripts/fill_klines_cache_from_vision.py").write_text(
            FILL_STUB.format(ctl=str(self.ctl), fills=str(self.fills)))
        (c / "scripts/e17/analyze_e17.py").write_text(
            ANALYZER_STUB.format(ctl=str(self.ctl), alog=str(self.alog), elog=str(self.elog)))
        (c / ".gitignore").write_text("__pycache__/\nlogs/\n")
        git(["init", "-q"], c, self.genv)
        git(["add", "-A"], c, self.genv)
        git(["commit", "-q", "-m", "H"], c, self.genv)
        return git(["rev-parse", "HEAD"], c, self.genv)

    def set_ctl(self, **kw: Any) -> None:
        self.ctl.write_text(json.dumps(kw))

    def env(self, **extra: str) -> Dict[str, str]:
        e = {"PATH": os.environ["PATH"], "HOME": str(self.home), "E17_ROOT": str(self.root),
             "E17_SNAPSHOT": str(self.snapshot), "E17_VARIANTS": str(self.variants_path),
             "E17_PY": sys.executable, "E17_CLONE_DIR": str(self.clone),
             "HTTPS_PROXY": "http://proxy.invalid:3128", "SOME_AMBIENT_VAR": "must-not-leak"}
        if self.prelock:
            e.update({"E17_PRELOCK": "1", "E17_H_COMMIT": self.h})
        e.update(extra)
        return e

    def run_script(self, body: str, **extra: str) -> subprocess.CompletedProcess:
        script = f'source "{self.lib}"\ne17_init\n{body}\n'
        return subprocess.run(["bash", "-c", script], env=self.env(**extra), capture_output=True,
                              text=True, timeout=300)

    def run_file(self, name: str, *args: str, **extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(self.e17dir / name), *args], env=self.env(**extra),
                              capture_output=True, text=True, timeout=600)

    def report(self, label: str) -> Dict[str, Any]:
        return json.loads((self.root / "sealed" / "runs" / f"{label}.json").read_text())

    def calls(self) -> int:
        return int(self.counter.read_text()) if self.counter.exists() else 0

    def analyzer_calls(self) -> List[str]:
        return self.alog.read_text().splitlines() if self.alog.exists() else []

    def analyzer_envs(self) -> List[Dict[str, Any]]:
        return [json.loads(ln) for ln in self.elog.read_text().splitlines()] if self.elog.exists() else []

    def fill_records(self) -> List[Dict[str, Any]]:
        if not self.fills.exists():
            return []
        return [json.loads(p.read_text()) for p in sorted(self.fills.glob("[0-9]*.json"))]

    def write_root_json(self, name: str, data: Any) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / name).write_text(json.dumps(data))


def _unlock_tree(path: Path) -> None:
    """Önbellek salt-okunur yapıldığı için pytest temizliği öncesi yazma iznini geri ver."""
    for p in [path, *path.rglob("*")]:
        try:
            p.chmod(p.stat().st_mode | 0o700)
        except OSError:
            pass


@pytest.fixture
def fx(tmp_path: Path):
    f = Fx(tmp_path)
    yield f
    _unlock_tree(tmp_path)


@pytest.fixture
def fxp(tmp_path: Path):
    """Kilit öncesi duman kipi (E17_PRELOCK=1)."""
    f = Fx(tmp_path, prelock=True)
    yield f
    _unlock_tree(tmp_path)


def assert_silent(proc: subprocess.CompletedProcess, fx: Optional["Fx"] = None) -> None:
    out = proc.stdout + proc.stderr
    if fx is not None:
        assert str(fx.snapshot) not in out, "snapshot yolu yankılandı"
    assert PNL_MARK not in out and "TOPLAM" not in out and "PF 9.99" not in out
    assert SECRET_MARK not in out, "snapshot değeri konsola sızdı"
    assert "must-not-leak" not in out


ALLOWED_LINE = re.compile(
    r"^(BT OK \S+ \d+|BT SKIP \S+|CHECK (OK|SKIP) \S+|FILL OK \S+ \d+|PHASE .*|G6 arm sayisi: \d+|DRY .*)$")


# ── Sözdizimi / sıkı kip ─────────────────────────────────────────────────────

SCRIPTS = ["lib17.sh", "run_smoke.sh", "run_dev.sh", "run_val.sh", "run_final.sh", "run_prova.sh"]


@pytest.mark.parametrize("name", SCRIPTS)
def test_bash_syntax_ok(name: str) -> None:
    proc = subprocess.run(["bash", "-n", str(E17 / name)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_strict_mode_and_no_swallowed_errors() -> None:
    lib = LIB.read_text()
    assert "set -Eeuo pipefail" in lib
    assert "trap " in lib and "ERR" in lib
    assert "exit 97" in lib and "PYTHONHASHSEED=0" in lib
    for name in SCRIPTS:
        text = (E17 / name).read_text()
        assert "|| echo" not in text, f"{name}: hata yutan '|| echo' var"
        assert "|| true" not in text, f"{name}: hata yutan '|| true' var"
        assert "--tf-" not in re.sub(r"#.*", "", text).replace("`--tf-*`", ""), f"{name}: --tf-* bayrağı yasak"
        if name != "lib17.sh":
            assert "set -Eeuo pipefail" in text
    # ERR tuzağı BASH_COMMAND'ı (snapshot değerleri taşır) yazdırmamalı
    assert "BASH_COMMAND" not in re.sub(r"#.*", "", lib)


def test_scripts_are_executable() -> None:
    for name in SCRIPTS[1:] + ["envtool.py", "verify_variants.py"]:
        assert os.access(E17 / name, os.X_OK), f"{name} çalıştırılabilir değil"


# ── run_bt: env -i, argv, sembol sırası, çıktı mührü ────────────────────────

def test_run_bt_env_passthrough_is_exact_and_includes_empty_value(fx: Fx) -> None:
    proc = fx.run_script('run_bt DEV_S5 S5 2025-01-01 2026-01-01')
    assert proc.returncode == 0, proc.stderr
    rep = fx.report("DEV_S5")
    env = {k: v for k, v in rep["env"].items() if k != "LC_CTYPE"}  # python C-yerel uyumu ekler
    # BG paketi: boş değer env -i ile AKTARILMALI (PLAN §15.3)
    assert "SCALPER_ENTRY_BLOCK_WEEKDAYS_UTC" in env and env["SCALPER_ENTRY_BLOCK_WEEKDAYS_UTC"] == ""
    snap = ET.load_snapshot(fx.snapshot)
    expected: Dict[str, str] = dict(snap)
    expected.update(fx.variants["family"][0]["env"])
    expected.update(dict(ET.DUMMY_SECRETS))
    non_secret_base = {"PATH", "HOME", "PYTHONPATH", "PYTHONHASHSEED", "TRADINGBOT_LOG_DIR"}
    assert set(env) == set(expected) | non_secret_base, "env -i geçiş listesi tam olarak §5.2 olmalı"
    for k, v in expected.items():
        assert env[k] == v, k
    assert env["PYTHONHASHSEED"] == "0"
    assert env["PYTHONPATH"] == str(fx.clone)
    assert env["PATH"] == os.environ["PATH"]
    assert env["HOME"] != str(fx.home) and env["TRADINGBOT_LOG_DIR"] == env["HOME"]
    # Kalıtılan ortam sızmaz; E17_* ve proxy yok (backtest ağ kullanmaz)
    assert "SOME_AMBIENT_VAR" not in env and "HTTPS_PROXY" not in env
    assert not any(k.startswith("E17_") for k in env)
    # Taze cwd: klonun/deponun DIŞINDA, `.env` yok, başlangıçta boş
    cwd = Path(rep["cwd"])
    assert not str(cwd).startswith(str(fx.clone)) and not str(cwd).startswith(str(REPO_ROOT))
    assert rep["listing"] == []
    assert not (cwd / ".env").exists()


def test_run_bt_argv_symbol_order_cache_dir_and_overlays(fx: Fx) -> None:
    proc = fx.run_script(
        'run_bt DEV_S15_ROB_FEE2 S15 2025-01-01 2026-01-01 ROB_FEE2\n'
        'run_bt DEV_S15_ROB_TAKER S15 2025-01-01 2026-01-01 ROB_TAKER\n')
    assert proc.returncode == 0, proc.stderr
    rep = fx.report("DEV_S15_ROB_FEE2")
    a = rep["argv"]
    assert a == ["--strategies", "C", "--symbols", PINNED, "--diagnostics", "--fee-stress",
                 "--start", "2025-01-01", "--end", "2026-01-01", "--cache-dir", str(fx.root / "cache")]
    assert a[a.index("--symbols") + 1].split(",") == PINNED.split(",")
    assert not any(x.startswith("--tf-") for x in a)
    # TAKER: env kaplaması, argv'de ek bayrak yok
    rep2 = fx.report("DEV_S15_ROB_TAKER")
    assert rep2["env"]["SCALPER_ENTRY_MODE"] == "taker" and "--fee-stress" not in rep2["argv"]
    assert rep["env"]["SCALPER_ENTRY_MODE"] == "maker"


def test_run_bt_hashseed_override_only_via_function_env(fx: Fx) -> None:
    proc = fx.run_script('E17_RUN_HASHSEED=1 run_bt SMOKE_S15_h1 S15 2026-04-01 2026-05-01\n'
                         'run_bt SMOKE_S15_h0 S15 2026-04-01 2026-05-01')
    assert proc.returncode == 0, proc.stderr
    assert fx.report("SMOKE_S15_h1")["env"]["PYTHONHASHSEED"] == "1"
    assert fx.report("SMOKE_S15_h0")["env"]["PYTHONHASHSEED"] == "0"


def test_run_bt_console_silence_and_seal(fx: Fx) -> None:
    proc = fx.run_script('run_bt DEV_T15 T15 2025-01-01 2026-01-01')
    assert proc.returncode == 0, proc.stderr
    assert_silent(proc, fx)
    lines = [ln for ln in proc.stdout.splitlines() if ln]
    assert all(ALLOWED_LINE.match(ln) for ln in lines), lines
    assert re.fullmatch(r"BT OK DEV_T15 \d+", lines[0])
    assert lines[1] == "CHECK OK DEV_T15"
    run = fx.root / "sealed" / "runs" / "DEV_T15.json"
    assert oct(run.stat().st_mode & 0o777) == "0o400"
    # PnL yalnız mühürlü günlükte (konsolda değil)
    assert PNL_MARK in (fx.root / "sealed" / "logs" / "DEV_T15.log").read_text()
    assert fx.analyzer_calls() == ["check-run DEV_T15"]


def test_run_bt_deletes_stale_outputs_even_when_run_fails(fx: Fx) -> None:
    runs, logs = fx.root / "sealed" / "runs", fx.root / "sealed" / "logs"
    runs.mkdir(parents=True)
    logs.mkdir(parents=True)
    (runs / "DEV_S5.json").write_text("STALE-REPORT")
    (logs / "DEV_S5.log").write_text("STALE-LOG")
    (logs / "DEV_S5.check.log").write_text("STALE-CHECK")
    fx.set_ctl(write_report=False, rc=1)
    proc = fx.run_script('run_bt DEV_S5 S5 2025-01-01 2026-01-01')
    assert proc.returncode == 97
    assert not (runs / "DEV_S5.json").exists(), "bayat rapor kalamaz"
    assert not (logs / "DEV_S5.check.log").exists()
    assert "STALE" not in (logs / "DEV_S5.log").read_text()
    assert proc.stdout.strip().splitlines()[-1] == "BT FAIL DEV_S5"
    assert_silent(proc)


def test_run_bt_failure_propagates_and_retries_only_infra(fx: Fx) -> None:
    # rapor üretilmedi + rc!=0 -> bir kez yeniden dene, sonra dur
    fx.set_ctl(write_report=False, rc=1)
    proc = fx.run_script('run_bt DEV_S5 S5 2025-01-01 2026-01-01\necho NOT-REACHED')
    assert proc.returncode == 97 and "NOT-REACHED" not in proc.stdout
    assert fx.calls() == 2
    chain = (fx.root / "CHAIN.log").read_text()
    assert "RETRY DEV_S5" in chain and "FAIL DEV_S5" in chain


def test_run_bt_retry_recovers_from_infra_failure(fx: Fx) -> None:
    fx.set_ctl(fail_first=1)
    proc = fx.run_script('run_bt DEV_S5 S5 2025-01-01 2026-01-01')
    assert proc.returncode == 0, proc.stderr
    assert fx.calls() == 2 and "RETRY DEV_S5" in (fx.root / "CHAIN.log").read_text()


def test_run_bt_no_retry_when_report_was_produced(fx: Fx) -> None:
    fx.set_ctl(write_report=True, rc=3)
    proc = fx.run_script('run_bt DEV_S5 S5 2025-01-01 2026-01-01')
    assert proc.returncode == 97 and fx.calls() == 1
    assert "BT FAIL DEV_S5" in proc.stdout
    assert "RETRY" not in (fx.root / "CHAIN.log").read_text()


def test_run_bt_stops_chain_when_check_run_fails(fx: Fx) -> None:
    fx.set_ctl(check_fail=["DEV_T5"])
    proc = fx.run_script('run_bt DEV_S5 S5 2025-01-01 2026-01-01\nrun_bt DEV_T5 T5 2025-01-01 2026-01-01\n'
                         'run_bt DEV_S15 S15 2025-01-01 2026-01-01')
    assert proc.returncode == 97
    assert "CHECK FAIL DEV_T5" in proc.stdout and "DEV_S15" not in proc.stdout
    assert not (fx.root / "sealed" / "runs" / "DEV_T5.json").exists()
    assert (fx.root / "sealed" / "runs" / "DEV_S5.json").exists()
    assert fx.calls() == 2


def test_run_bt_skips_completed_labels_in_ledger(fx: Fx) -> None:
    fx.run_script('run_bt DEV_S5 S5 2025-01-01 2026-01-01')
    assert fx.calls() == 1
    proc = fx.run_script('run_bt DEV_S5 S5 2025-01-01 2026-01-01')
    assert proc.returncode == 0 and "BT SKIP DEV_S5" in proc.stdout and fx.calls() == 1
    # ledger'da var ama rapor yok -> tutarsızlık, zincir durur
    (fx.root / "sealed" / "runs" / "DEV_S5.json").chmod(0o600)
    (fx.root / "sealed" / "runs" / "DEV_S5.json").unlink()
    assert fx.run_script('run_bt DEV_S5 S5 2025-01-01 2026-01-01').returncode == 97


@pytest.mark.parametrize("env_patch,msg", [
    ({"SCALPER_TF_CONTEXT": "5m"}, "ROL ÇAKIŞMASI"),                     # giriş == bağlam
    ({"SCALPER_TF_REGIME": "5m"}, "ROL ÇAKIŞMASI"),                      # giriş == rejim
    ({"SCALPER_TF_REGIME": "4h"}, "triple"),                             # variants.json üçlüsüyle çelişki
])
def test_run_bt_rejects_role_key_collisions(tmp_path: Path, env_patch: Dict[str, str], msg: str) -> None:
    probe = Fx(tmp_path / "probe") if (tmp_path / "probe").mkdir() is None else None
    v = probe.variants
    v["family"][0]["env"].update(env_patch)
    probe.write_variants(v)
    proc = probe.run_script('run_bt DEV_S5 S5 2025-01-01 2026-01-01')
    assert proc.returncode == 97
    assert probe.calls() == 0, "çakışan üçlüyle backtest ÇALIŞMAMALI"
    assert not (probe.root / "sealed" / "runs" / "DEV_S5.json").exists()
    assert msg in proc.stderr or "rol" in proc.stderr.lower()
    assert "BT OK" not in proc.stdout


def test_run_bt_rejects_cli_with_tf_flags_or_unpinned_symbols(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    a = Fx(tmp_path / "a")
    a.variants["common_cli"] = ["--strategies", "C", "--symbols", "ETHUSDT,BTCUSDT", "--diagnostics"]
    a.write_variants(a.variants)
    assert a.run_script('run_bt DEV_S5 S5 2025-01-01 2026-01-01').returncode == 97
    assert a.calls() == 0
    (tmp_path / "b").mkdir()
    b = Fx(tmp_path / "b")
    b.variants["common_cli"] = b.variants["common_cli"] + ["--tf-entry", "5m"]
    b.write_variants(b.variants)
    assert b.run_script('run_bt DEV_S5 S5 2025-01-01 2026-01-01').returncode == 97
    assert b.calls() == 0


def test_run_bt_rejects_dirty_or_wrong_clone(fx: Fx) -> None:
    (fx.clone / "untracked.txt").write_text("x")
    proc = fx.run_script('run_bt DEV_S5 S5 2025-01-01 2026-01-01')
    assert proc.returncode == 97 and fx.calls() == 0 and "kirli" in proc.stderr
    (fx.clone / "untracked.txt").unlink()
    proc = fx.run_script('run_bt DEV_S5 S5 2025-01-01 2026-01-01', E17_H_COMMIT="0" * 40)
    assert proc.returncode == 97 and fx.calls() == 0


def test_clone_is_created_from_repo_at_h_when_no_clone_dir(fx: Fx) -> None:
    env = fx.env(E17_REPO=str(fx.clone))
    del env["E17_CLONE_DIR"]
    script = f'source "{fx.lib}"\ne17_init\nrun_bt DEV_S5 S5 2025-01-01 2026-01-01\n'
    proc = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    new_clone = fx.root / "clone_H"
    assert git(["rev-parse", "HEAD"], new_clone, fx.genv) == fx.h
    assert fx.report("DEV_S5")["env"]["PYTHONPATH"] == str(new_clone)


def test_lock_ref_required_without_prelock(fx: Fx) -> None:
    (fx.root / "LOCK_REF.json").unlink()
    proc = fx.run_script('echo x')
    assert proc.returncode == 97 and "LOCK_REF" in proc.stderr
    # Analizcinin GERÇEK LOCK_REF biçimi: {l, h, lock_sha256, lock_path, pushed, history}
    fx.write_root_json("LOCK_REF.json", {"l": "a" * 40, "h": fx.h, "lock_sha256": "b" * 64,
                                         "lock_path": "x", "pushed": False, "history": []})
    assert fx.run_script('echo x').returncode == 0
    # eski/elle biçim: `h_commit` yedeği
    fx.write_root_json("LOCK_REF.json", {"h_commit": fx.h})
    assert fx.run_script('echo x').returncode == 0
    # E17_H_COMMIT ile LOCK_REF çelişirse reddedilir
    proc = fx.run_script('echo x', E17_H_COMMIT="f" * 40)
    assert proc.returncode == 97 and "çelişiyor" in proc.stderr
    fx.write_root_json("LOCK_REF.json", {"h": "f" * 40})
    assert fx.run_script('echo x').returncode == 97          # klon HEAD'i H değil


def test_post_lock_path_checks_lock_json_and_calls_verify_lock(fx: Fx) -> None:
    script = f'source "{fx.lib}"\ne17_init\ne17_verify_lock\nrun_bt DEV_S5 S5 2025-01-01 2026-01-01\n'
    env = fx.env()
    proc = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert fx.analyzer_calls()[0] == "verify-lock" and fx.report("DEV_S5")["env"]["PYTHONPATH"] == str(fx.clone)
    # Analizci H klonundan çalışır ve depo kökü olarak L'yi taşıyan checkout'u (E17_REPO) görür:
    # H klonunda lockdir/L YOKTUR.
    e = fx.analyzer_envs()[0]
    assert e["E17_REPO"] == str(fx.repo) and e["cwd"] == str(fx.clone)
    assert e["E17_ROOT"] == str(fx.root) and e["E17_SNAPSHOT"] == str(fx.snapshot)
    # E17_REPO açıkça verilirse o geçer
    other = fx.tmp / "other_repo"
    other.mkdir()
    proc = subprocess.run(["bash", "-c", script], env=fx.env(E17_REPO=str(other)), capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert fx.analyzer_envs()[-1]['E17_REPO'] == str(other)
    # kilitli hash'ten sapma (variants.json değişti, LOCK.json eski) -> başlamaz
    fx.variants["snapshot"]["note"] = "edited"
    fx.variants_path.write_text(json.dumps(fx.variants))
    proc = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
    assert proc.returncode == 97 and "kilit ön-kontrolü" in proc.stderr
    # analizci verify-lock başarısızsa zincir başlamaz
    fx.write_variants(fx.variants)
    fx.set_ctl(analyzer_rc={"verify-lock": 3})
    fx.counter.unlink()
    proc = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
    assert proc.returncode == 97 and "verify-lock" in proc.stderr and fx.calls() == 0


def test_post_lock_rejects_unaudited_overrides(fx: Fx) -> None:
    for var in ("E17_ANALYZER", "E17_LOCK_JSON"):
        proc = fx.run_script("echo x", **{var: str(fx.lockjson)})
        assert proc.returncode == 97 and var in proc.stderr, var
    # kilit öncesi duman kipinde serbesttir
    assert fx.run_script("echo x", E17_PRELOCK="1", E17_H_COMMIT=fx.h, E17_LOCK_JSON="/x").returncode == 0


def test_script_working_copy_must_match_h_clone(fx: Fx) -> None:
    (fx.e17dir / "run_smoke.sh").write_text((fx.e17dir / "run_smoke.sh").read_text() + "\n# kilit sonrası düzenleme\n")
    proc = fx.run_script('run_bt DEV_S5 S5 2025-01-01 2026-01-01')
    assert proc.returncode == 97 and "H klonundan farklı" in proc.stderr and "run_smoke.sh" in proc.stderr
    assert fx.calls() == 0


def test_chain_lock_rejects_concurrent_chain_and_recovers_stale(fx: Fx) -> None:
    lock = fx.root / ".chain.lock"
    fx.root.mkdir(parents=True, exist_ok=True)
    os.symlink(str(os.getpid()), lock)                                  # yaşayan süreç (atomik sembolik bağ)
    proc = fx.run_script("echo x")
    assert proc.returncode == 97 and "başka bir E17 zinciri" in proc.stderr
    assert os.readlink(lock) == str(os.getpid()), "başkasının kilidi silinmemeli"
    lock.unlink()
    os.symlink("999999", lock)                                          # ölü süreç -> devral
    proc = fx.run_script("echo x")
    assert proc.returncode == 0 and "STALE LOCK" in (fx.root / "CHAIN.log").read_text()
    assert not lock.is_symlink() and not lock.exists(), "çıkışta kilit bırakılmalı"
    # eski biçim (pid dosyasız dizin) kalıntısı yarış yaratmaz: devralınır
    lock.mkdir()
    assert fx.run_script("echo x").returncode == 0 and not lock.exists()


def test_sigterm_kills_background_workers_and_releases_lock(fx: Fx) -> None:
    pidfile = fx.tmp / "child.pid"
    script = f'source "{fx.lib}"\ne17_init\nsleep 31 &\necho $! > "{pidfile}"\nwait\n'
    proc = subprocess.Popen(["bash", "-c", script], env=fx.env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    for _ in range(100):
        if pidfile.exists() and pidfile.read_text().strip():
            break
        time.sleep(0.1)
    child = int(pidfile.read_text().strip())
    assert (fx.root / ".chain.lock").is_symlink()
    proc.send_signal(signal.SIGTERM)
    assert proc.wait(timeout=20) == 143
    time.sleep(0.3)
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)
    assert not (fx.root / ".chain.lock").exists(), "SIGTERM sonrası kilit bırakılmalı"


# ── Doldurma ─────────────────────────────────────────────────────────────────

def test_fill_splits_symbols_over_workers_gate_flag_and_env(fx: Fx) -> None:
    proc = fx.run_script('fill_or_die T_BG S5 2025-01-01 2026-01-01\nfill_or_die T_GATE S5G 2025-01-01 2026-01-01')
    assert proc.returncode == 0, proc.stderr
    assert_silent(proc)
    recs = fx.fill_records()
    assert len(recs) == 16                      # 8 işçi x 2 doldurma
    bg = [r for r in recs if "--market-gate" not in r["argv"]]
    gate = [r for r in recs if "--market-gate" in r["argv"]]
    assert len(bg) == 8 and len(gate) == 8      # yalnız kapısı AÇIK arm'da --market-gate
    syms = sorted(r["argv"][r["argv"].index("--symbols") + 1] for r in bg)
    assert syms == sorted(PINNED.split(","))    # her işçi tek sembol, hepsi kapsanır
    for r in recs:
        assert not any(x.startswith("--tf-") for x in r["argv"])
        assert r["argv"][r["argv"].index("--cache-dir") + 1] == str(fx.root / "cache")
        assert r["argv"][r["argv"].index("--download-cache") + 1] == str(fx.root / "dl")
        assert r["env"]["PYTHONHASHSEED"] == "0" and "SOME_AMBIENT_VAR" not in r["env"]
        assert r["env"]["HTTPS_PROXY"] == "http://proxy.invalid:3128"   # yalnız ağ geçişi
        assert r["env"]["SCALPER_TF_ENTRY"] == "5m"                      # arm'ın KENDİ env'i
    g = next(r for r in gate)
    assert g["env"]["SCALPER_MARKET_GATE"] == "true"
    assert next(r for r in bg)["env"]["SCALPER_MARKET_GATE"] == "false"
    assert all(r["env"]["SCALPER_ENTRY_BLOCK_WEEKDAYS_UTC"] == "" for r in bg)


def test_fill_fallback_to_four_workers_after_failure(fx: Fx) -> None:
    fx.set_ctl(fill_fail_once="SOLUSDT")
    proc = fx.run_script('fill_or_die T_BG S15 2025-01-01 2026-01-01')
    assert proc.returncode == 0, proc.stderr
    assert "FILL OK T_BG" in proc.stdout
    assert "FILL RETRY T_BG işçi 8 -> 4" in (fx.root / "CHAIN.log").read_text()
    sizes = [len(r["argv"][r["argv"].index("--symbols") + 1].split(",")) for r in fx.fill_records()]
    assert sizes[:8] == [1] * 8 and sorted(sizes[8:]) == [2, 2, 2, 2]


def test_fill_failure_stops_chain(fx: Fx) -> None:
    fx.set_ctl(fill_fail_always="DOGEUSDT")
    proc = fx.run_script('fill_or_die T_BG S15 2025-01-01 2026-01-01\necho NOT-REACHED')
    assert proc.returncode == 97
    assert "FILL FAIL T_BG" in proc.stdout and "NOT-REACHED" not in proc.stdout


def test_fill_workers_env_override(fx: Fx) -> None:
    proc = fx.run_script('fill_or_die T_BG S15 2025-01-01 2026-01-01', E17_FILL_WORKERS="2")
    assert proc.returncode == 0, proc.stderr
    sizes = sorted(len(r["argv"][r["argv"].index("--symbols") + 1].split(",")) for r in fx.fill_records())
    assert sizes == [4, 4]


# ── Zincir betikleri (kuru mod) ──────────────────────────────────────────────

def dry_lines(proc: subprocess.CompletedProcess, kind: str) -> List[str]:
    return [ln for ln in proc.stdout.splitlines() if ln.startswith(f"DRY {kind} ")]


def labels(proc: subprocess.CompletedProcess) -> List[str]:
    return [ln.split()[2] for ln in dry_lines(proc, "BT")]


def test_run_smoke_dry_run_plan(fx: Fx) -> None:
    proc = fx.run_file("run_smoke.sh", E17_DRY_RUN="1")
    assert proc.returncode == 0, proc.stderr
    # etiket dilbilgisi analizcinin parse_label'ı ile aynı: SMOKE_<arm>_W, SMOKE_R1_M1
    assert labels(proc) == ["SMOKE_B0_AYI", "SMOKE_B0_V1", "SMOKE_S15_h0", "SMOKE_S15_h1",
                            "SMOKE_S5_W", "SMOKE_T5_W", "SMOKE_S15_W", "SMOKE_T15_W", "SMOKE_S30_W", "SMOKE_T30_W",
                            "SMOKE_S60_W", "SMOKE_T60_W", "SMOKE_R1_M1"]
    bt = {ln.split()[2]: ln for ln in dry_lines(proc, "BT")}
    assert "window=2026-01-23..2026-02-13" in bt["SMOKE_B0_AYI"] and "window=2025-01-06..2025-01-27" in bt["SMOKE_B0_V1"]
    assert "hashseed=1" in bt["SMOKE_S15_h1"] and "hashseed=0" in bt["SMOKE_S15_h0"]
    assert "window=2026-04-01..2026-04-08" in bt["SMOKE_R1_M1"] and "triple=1m/5m/15m" in bt["SMOKE_R1_M1"]
    fills = dry_lines(proc, "FILL")
    gate_fills = {ln.split()[2] for ln in fills if "gate=1" in ln}
    assert gate_fills == {"SMOKE_B0_AYI", "SMOKE_B0_V1"}          # lider yalnız B0'da
    an = [ln.replace("DRY ANALYZER ", "") for ln in proc.stdout.splitlines() if ln.startswith("DRY ANALYZER")]
    assert an == ["verify-lock", "manifest smoke", "smoke", "manifest runs"]   # küçük harf hedef
    assert fx.calls() == 0 and not fx.fill_records()               # kuru mod hiçbir şey çalıştırmaz
    assert_silent(proc)


def test_dry_run_prints_full_argv_and_env_key_names(fx: Fx) -> None:
    proc = fx.run_script('run_bt DEV_S15_ROB_FEE2 S15 2025-01-01 2026-01-01 ROB_FEE2', E17_DRY_RUN="1")
    assert proc.returncode == 0, proc.stderr
    argv = next(ln for ln in proc.stdout.splitlines() if ln.startswith("DRY ARGV "))
    assert f"--start 2025-01-01 --end 2026-01-01 --cache-dir {fx.root}/cache" in argv and "--fee-stress" in argv
    env = next(ln for ln in proc.stdout.splitlines() if ln.startswith("DRY ENV "))
    assert "keys=PATH,HOME,PYTHONPATH,PYTHONHASHSEED,TRADINGBOT_LOG_DIR,dummy(8)" in env
    assert SECRET_MARK not in proc.stdout


def dev_fixture(fx: Fx) -> None:
    gates = {f"G{i}": {"value": 1, "threshold": 1, "pass": True, "borderline": False} for i in range(6)}
    bad = dict(gates)
    bad["G2"] = {"value": 1, "threshold": 1, "pass": False}
    fx.write_root_json("DEV_SCREEN.json", {"S15": gates, "T30": gates, "S5": bad, "T5": bad,
                                           "T15": bad, "S30": bad, "NOT_AN_ARM": gates,
                                           "_meta": {"g6_needed": ["S15", "T30"]}})
    fx.write_root_json("DEV_DECISION.json", {
        "g6": {"S15": {"fee2": {"pass": True}, "dly1": {"pass": True}, "taker": {"pass": True}},
               "T30": {"fee2": {"pass": False}, "dly1": {"pass": True}, "taker": {"pass": True}}},
        "ranked": [["S15", 3.0]], "advanced": ["S15"], "k": 1})


def test_run_dev_dry_run_order_matches_plan(fx: Fx) -> None:
    dev_fixture(fx)
    proc = fx.run_file("run_dev.sh", E17_DRY_RUN="1")
    assert proc.returncode == 0, proc.stderr
    p3 = (["DEV_S5", "DEV_T5", "DEV_S15", "DEV_T15", "DEV_S30", "DEV_T30", "DEV_S60", "DEV_T60", "DEV_S5G", "DEV_T5G"]
          + [f"DEV_R1_Q{i}" for i in range(1, 5)] + [f"DEV_B0_Q{i}" for i in range(1, 5)])
    g6 = [f"DEV_{a}_{o}" for a in ("S15", "T30") for o in ("ROB_FEE2", "ROB_DLY1", "ROB_TAKER")]
    fam = ["S5", "T5", "S15", "T15", "S30", "T30"]
    diag = ([f"DEV_{a}_DIAG_Z0" for a in fam] + [f"DEV_{a}_DIAG_SBO" for a in fam]
            + ["DEV_T30_DIAG_FEE15", "DEV_S15_DIAG_FEE15"])    # fee2'yi geçemeyen + ilerleyen
    got = labels(proc)
    assert got[:18] == p3
    assert got[18:24] == g6, "G6 yalnız ekranın listelediği arm'lara"
    assert sorted(got[24:]) == sorted(diag) and got[24:36] == diag[:12]
    # sıra: doldurma -> manifest -> koşular -> dev-screen -> dev-decide -> diag
    an = [ln for ln in proc.stdout.splitlines() if ln.startswith("DRY ANALYZER")]
    assert [a.replace("DRY ANALYZER ", "") for a in an] == ["verify-lock", "manifest dev", "dev-screen", "dev-decide", "diag", "manifest runs"]
    bt = {ln.split()[2]: ln for ln in dry_lines(proc, "BT")}
    assert "window=2025-04-01..2025-07-01" in bt["DEV_B0_Q2"] and "window=2025-01-01..2026-01-01" in bt["DEV_S5"]
    assert "overlays=ROB_FEE2" in bt["DEV_S15_ROB_FEE2"]
    # doldurma envanteri: 5m BG + 5m kapılı (lider) + 15m + 30m + 1h + 1m çeyrekler
    tags = {}
    for ln in dry_lines(proc, "FILL"):
        tags.setdefault(ln.split()[2], ln)
    assert list(tags) == ["DEV_5m_BG", "DEV_5m_GATE", "DEV_15m", "DEV_30m", "DEV_1h",
                          "DEV_1m_Q1", "DEV_1m_Q2", "DEV_1m_Q3", "DEV_1m_Q4"]
    assert "gate=1" in tags["DEV_5m_GATE"] and "flag=--market-gate" in tags["DEV_5m_GATE"]
    assert "gate=0" in tags["DEV_5m_BG"] and "flag=-" in tags["DEV_5m_BG"]
    assert "gate=1" in tags["DEV_1m_Q3"] and "window=2025-07-01..2025-10-01" in tags["DEV_1m_Q3"]
    assert "--tf-" not in proc.stdout
    assert fx.calls() == 0
    assert_silent(proc)


def smoke_done(fx: Fx, passed: bool = True) -> None:
    """Duman testini bitmiş say: 13 etiket ledger'da + analizci hükmü."""
    fx.root.mkdir(parents=True, exist_ok=True)
    with (fx.root / "ledger.jsonl").open("a") as fh:
        for lbl in E17_SMOKE_LABELS.split():
            fh.write(json.dumps({"label": lbl}) + "\n")
    fx.write_root_json("SMOKE.json", {"pass": passed})


def test_run_dev_refuses_without_finished_smoke(fx: Fx) -> None:
    proc = fx.run_file("run_dev.sh")
    assert proc.returncode == 97 and "duman testi tamamlanmadı" in proc.stderr
    assert fx.calls() == 0 and not fx.fill_records()
    # 13 koşunun biri eksik (örn. S3 arm'ı): yine reddedilir
    smoke_done(fx)
    lines = (fx.root / "ledger.jsonl").read_text().splitlines()
    (fx.root / "ledger.jsonl").write_text("\n".join(ln for ln in lines if "SMOKE_T30_W" not in ln) + "\n")
    proc = fx.run_file("run_dev.sh")
    assert proc.returncode == 97 and "SMOKE_T30_W" in proc.stderr and fx.calls() == 0


def test_run_dev_refuses_when_smoke_verdict_failed(fx: Fx) -> None:
    smoke_done(fx, passed=False)           # check-run geçti (ledger dolu) AMA analizci smoke hükmü FAIL
    proc = fx.run_file("run_dev.sh")
    assert proc.returncode == 97 and "GEÇMEDİ" in proc.stderr
    assert fx.calls() == 0 and not fx.fill_records()
    (fx.root / "SMOKE.json").unlink()
    proc = fx.run_file("run_dev.sh")
    assert proc.returncode == 97 and "SMOKE.json" in proc.stderr


def test_smoke_script_reports_failed_verdict_and_run_dev_then_refuses(fx: Fx) -> None:
    fx.set_ctl(smoke_pass=False)
    proc = fx.run_file("run_smoke.sh")
    assert proc.returncode == 97                         # analizci smoke rc!=0 -> zincir durur
    assert json.loads((fx.root / "SMOKE.json").read_text()) == {"pass": False}
    proc = fx.run_file("run_dev.sh")
    assert proc.returncode == 97 and "GEÇMEDİ" in proc.stderr


def test_prelock_refused_by_dev_val_final_but_allowed_for_smoke_and_prova(fxp: Fx) -> None:
    for name in ("run_dev.sh", "run_val.sh", "run_final.sh"):
        proc = fxp.run_file(name, E17_DRY_RUN="0")
        assert proc.returncode == 97 and "PRELOCK" in proc.stderr, name
    assert fxp.calls() == 0 and not fxp.fill_records() and not fxp.analyzer_calls()
    # smoke PRELOCK'ta çalışır: analizci HİÇ çağrılmaz, check-run atlanır, ledger yazılmaz
    proc = fxp.run_file("run_smoke.sh")
    assert proc.returncode == 0, proc.stderr
    assert fxp.analyzer_calls() == [] and not (fxp.root / "ledger.jsonl").exists()
    assert "CHECK SKIP SMOKE_B0_AYI" in proc.stdout and "CHECK OK" not in proc.stdout
    assert fxp.report("SMOKE_R1_M1")["argv"][0] == "--strategies"
    assert not (fxp.root / "LOCK_REF.json").exists()


def test_run_val_refuses_without_dev_decision_or_advanced_arm(fx: Fx) -> None:
    proc = fx.run_file("run_val.sh", E17_DRY_RUN="1")
    assert proc.returncode == 97 and "DEV_DECISION.json" in proc.stderr
    assert not dry_lines(proc, "FILL") and not dry_lines(proc, "BT")
    fx.write_root_json("DEV_DECISION.json", {"g6": {}, "advanced": [], "k": 0})
    proc = fx.run_file("run_val.sh", E17_DRY_RUN="1")
    assert proc.returncode == 97 and "ilerleyen" in proc.stderr
    assert not dry_lines(proc, "FILL"), "VAL verisi indirilmemeli"
    fx.write_root_json("DEV_DECISION.json", {"advanced": ["S5", "S15", "S30"]})
    assert fx.run_file("run_val.sh", E17_DRY_RUN="1").returncode == 97


def test_run_val_dry_run_plan_and_window_cut(fx: Fx) -> None:
    dev_fixture(fx)
    proc = fx.run_file("run_val.sh", E17_DRY_RUN="1")
    assert proc.returncode == 0, proc.stderr
    assert labels(proc) == ["VAL_S15", "VAL_S15_ROB_FEE2", "VAL_S15_ROB_DLY1", "VAL_S15_ROB_TAKER",
                            "VAL_S15_ROB_WALL", "VAL_S60", "VAL_T60"]
    fills = {ln.split()[2]: ln for ln in dry_lines(proc, "FILL")}
    assert list(fills) == ["VAL_S15", "VAL_S60"]               # ilerleyen üçlü + açıklayıcı; 1m YOK
    assert all("window=2023-01-01..2024-11-18" in ln for ln in fills.values())
    assert all("gate=0" in ln for ln in fills.values())
    an = [ln.replace("DRY ANALYZER ", "") for ln in proc.stdout.splitlines() if ln.startswith("DRY ANALYZER")]
    assert an == ["verify-lock", "manifest val", "val", "manifest runs"]
    # §9.5: kesilmiş VAL başlangıcı otomatik kullanılır (analizcinin okuduğu anahtar: `start`)
    fx.write_root_json("VAL_WINDOW_CUT.json", {"status": "CUT", "start": "2023-04-01", "val_start": "2023-04-01",
                                               "val_end": "2024-11-18"})
    proc = fx.run_file("run_val.sh", E17_DRY_RUN="1")
    assert "window=2023-04-01..2024-11-18" in dry_lines(proc, "BT")[0]
    fx.write_root_json("VAL_WINDOW_CUT.json", {"status": "CUT", "val_start": "2023-05-01"})   # eski biçim yedeği
    assert "window=2023-05-01..2024-11-18" in dry_lines(fx.run_file("run_val.sh", E17_DRY_RUN="1"), "BT")[0]
    fx.write_root_json("VAL_WINDOW_CUT.json", {"status": "VAL-YAPILAMADI", "val_start": None})
    assert fx.run_file("run_val.sh", E17_DRY_RUN="1").returncode == 97


def test_run_val_refuses_non_family_advanced_ids(fx: Fx) -> None:
    for bad in ("B0", "S5G", "R1", "T60", "NOPE"):
        fx.write_root_json("DEV_DECISION.json", {"advanced": [bad]})
        proc = fx.run_file("run_val.sh", E17_DRY_RUN="1")
        assert proc.returncode == 97 and "aile üyesi değil" in proc.stderr, bad
        assert not dry_lines(proc, "FILL") and not dry_lines(proc, "BT"), bad


def test_run_val_refuses_after_final_json(fx: Fx) -> None:
    fx.write_root_json("DEV_DECISION.json", {"advanced": ["S15"]})
    fx.write_root_json("FINAL.json", {})
    proc = fx.run_file("run_val.sh", E17_DRY_RUN="1")
    assert proc.returncode == 97 and "FINAL.json" in proc.stderr and not dry_lines(proc, "FILL")


def test_run_val_dedupes_triples_for_shared_timeframe(fx: Fx) -> None:
    fx.write_root_json("DEV_DECISION.json", {"advanced": ["S30", "T30"]})
    proc = fx.run_file("run_val.sh", E17_DRY_RUN="1")
    fills = list(dict.fromkeys(ln.split()[2] for ln in dry_lines(proc, "FILL")))
    assert fills == ["VAL_S30", "VAL_S60"]      # 30m/4h/4h bir kez; S60 (1h/4h/4h) bir kez


def test_run_final_refuses_without_unique_val_winner(fx: Fx) -> None:
    proc = fx.run_file("run_final.sh", E17_DRY_RUN="1")
    assert proc.returncode == 97 and "VAL_DECISION.json" in proc.stderr
    fx.write_root_json("VAL_DECISION.json", {"S15": {"winner": False}, "T15": {"winner": False}})
    assert fx.run_file("run_final.sh", E17_DRY_RUN="1").returncode == 97
    fx.write_root_json("VAL_DECISION.json", {"S15": {"winner": True}, "T30": {"winner": True}})
    assert fx.run_file("run_final.sh", E17_DRY_RUN="1").returncode == 97
    fx.write_root_json("VAL_DECISION.json", {"S60": {"winner": True}})
    assert fx.run_file("run_final.sh", E17_DRY_RUN="1").returncode == 97        # aile dışı
    # analizcinin gerçek biçimi: _meta.winner (None = kazanan yok)
    fx.write_root_json("VAL_DECISION.json", {"S15": {"winner": False}, "_meta": {"winner": None}})
    assert fx.run_file("run_final.sh", E17_DRY_RUN="1").returncode == 97
    fx.write_root_json("VAL_DECISION.json", {"S15": {"winner": True}, "_meta": {"winner": "S15"}})
    fx.write_root_json("FINAL.json", {})
    assert fx.run_file("run_final.sh", E17_DRY_RUN="1").returncode == 97        # tek atış


def test_run_final_dry_run_plan(fx: Fx) -> None:
    fx.write_root_json("VAL_DECISION.json", {"S15": {"winner": True}, "T15": {"winner": False},
                                             "_meta": {"winner": "S15", "status": "TAMAM"}})
    proc = fx.run_file("run_final.sh", E17_DRY_RUN="1")
    assert proc.returncode == 0, proc.stderr
    assert labels(proc) == ["FINAL_S15", "FINAL_B0"]
    fills = {ln.split()[2]: ln for ln in dry_lines(proc, "FILL")}
    assert "gate=0" in fills["FINAL_S15"] and "gate=1" in fills["FINAL_B0"]
    assert all("window=2026-09-03..2026-10-08" in ln for ln in fills.values())
    an = [ln.replace("DRY ANALYZER ", "") for ln in proc.stdout.splitlines() if ln.startswith("DRY ANALYZER")]
    assert an == ["verify-lock", "manifest final", "final", "decide", "manifest runs"]


# ── Uçtan uca (sahte komutlarla, kuru mod DEĞİL): aşama kapıları ve ledger ──

def test_end_to_end_chain_smoke_dev_val_final(fx: Fx) -> None:
    """Kilit SONRASI kip, analizcinin gerçek LOCK_REF/aşama-dosyası biçimleriyle, uçtan uca."""
    gates = {f"G{i}": {"pass": True} for i in range(6)}
    fx.set_ctl(screen={"S15": gates, "_meta": {"g6_needed": ["S15"]}},
               decision={"g6": {"S15": {"fee2": {"pass": True}}}, "advanced": ["S15"], "k": 1},
               vdecision={"S15": {"winner": True}, "_meta": {"winner": "S15", "status": "TAMAM"}})
    # DEV, duman bitmeden başlamaz
    assert fx.run_file("run_dev.sh").returncode == 97
    for script in ("run_smoke.sh", "run_dev.sh", "run_val.sh", "run_final.sh"):
        proc = fx.run_file(script)
        assert proc.returncode == 0, f"{script}: {proc.stderr}"
        assert_silent(proc)
        for ln in proc.stdout.splitlines():
            assert ALLOWED_LINE.match(ln), (script, ln)
    calls = fx.analyzer_calls()
    order = [c.split()[0] for c in calls if c.split()[0] not in ("check-run", "verify-lock")]
    assert order == ["manifest", "smoke", "manifest",
                     "manifest", "dev-screen", "dev-decide", "diag", "manifest",
                     "manifest", "val", "manifest",
                     "manifest", "final", "decide", "manifest"]
    assert [c for c in calls if c.startswith("manifest")] == [
        "manifest smoke", "manifest runs", "manifest dev", "manifest runs", "manifest val", "manifest runs",
        "manifest final", "manifest runs"]
    assert calls.count("verify-lock") == 5     # 4 aşama + (duman bitmeden reddedilen) ilk run_dev: her aşama kilidi doğrular
    assert all(e["E17_REPO"] == str(fx.repo) for e in fx.analyzer_envs())
    ledger = (fx.root / "ledger.jsonl").read_text()
    for lbl in ("SMOKE_R1_M1", "SMOKE_T60_W", "DEV_B0_Q4", "DEV_S15_ROB_TAKER", "DEV_T5_DIAG_SBO", "VAL_S15_ROB_WALL",
                "VAL_T60", "FINAL_S15", "FINAL_B0"):
        assert f'"{lbl}"' in ledger
    # tüm raporlar mühürlü (0400)
    for p in (fx.root / "sealed" / "runs").glob("*.json"):
        assert oct(p.stat().st_mode & 0o777) == "0o400"
    # yeniden çalıştırma: tamamlanmış koşular tekrarlanmaz
    n = fx.calls()
    assert fx.run_file("run_smoke.sh").returncode == 0 and fx.calls() == n


def test_val_gap_rule_cuts_window_on_persistent_archive_gap(fx: Fx) -> None:
    fx.set_ctl(gap={"symbol": "BTCUSDT", "interval": "5m", "date": "2023-03-10", "before": "2023-04-01"},
               decision={"advanced": ["S5"]}, vdecision={})
    fx.write_root_json("DEV_DECISION.json", {"advanced": ["S5"]})
    proc = fx.run_file("run_val.sh", E17_WINDOW_REPO=str(REPO_ROOT), E17_FILL_FALLBACK_WORKERS="8")
    assert proc.returncode == 0, proc.stderr
    cut = json.loads((fx.root / "VAL_WINDOW_CUT.json").read_text())
    # 5m/30m/1h ısınması 10.4 gün: 2023-03-10 boşluğunu ancak 2023-04-01 başlangıcı dışarıda bırakır
    assert cut["status"] == "CUT" and cut["val_start"] == "2023-04-01" and cut["shift_months"] == 3
    assert cut["gap_date"] == "2023-03-10" and cut["val_end"] == "2024-11-18"
    # analizcinin (Ctx.window) okuduğu anahtar
    assert cut["start"] == "2023-04-01" and cut["end"] == "2024-11-18"
    assert "VAL penceresi kesildi" in (fx.root / "CHAIN.log").read_text()
    argv = fx.report("VAL_S5")["argv"]
    assert argv[argv.index("--start") + 1] == "2023-04-01"


def test_val_gap_rule_uses_latest_gap_in_one_step(fx: Fx) -> None:
    fx.set_ctl(gaps=[{"symbol": "BTCUSDT", "interval": "5m", "date": "2023-02-01", "before": "2023-03-01"},
                     {"symbol": "ETHUSDT", "interval": "5m", "date": "2023-03-10", "before": "2023-04-01"}])
    fx.write_root_json("DEV_DECISION.json", {"advanced": ["S5"]})
    proc = fx.run_file("run_val.sh", E17_WINDOW_REPO=str(REPO_ROOT), E17_FILL_FALLBACK_WORKERS="8")
    assert proc.returncode == 0, proc.stderr
    chain = (fx.root / "CHAIN.log").read_text()
    assert chain.count("VAL penceresi kesildi") == 1, "en SON boşluk seçilmeli (tek adımda 2023-04-01)"
    assert "boşluk ETHUSDT 5m 2023-03-10" in chain
    assert json.loads((fx.root / "VAL_WINDOW_CUT.json").read_text())["start"] == "2023-04-01"


def test_val_gap_rule_handles_checksum_failure_but_not_network_errors(fx: Fx) -> None:
    fx.set_ctl(gap={"symbol": "BTCUSDT", "interval": "5m", "date": "2023-03-10", "before": "2023-04-01",
                    "kind": "checksum"})
    fx.write_root_json("DEV_DECISION.json", {"advanced": ["S5"]})
    proc = fx.run_file("run_val.sh", E17_WINDOW_REPO=str(REPO_ROOT), E17_FILL_FALLBACK_WORKERS="8")
    assert proc.returncode == 0, proc.stderr          # kalıcı doğrulama hatası da PLAN §9.5 boşluğudur
    assert json.loads((fx.root / "VAL_WINDOW_CUT.json").read_text())["start"] == "2023-04-01"
    # ağ hatası (İndirilemedi) boşluk DEĞİL: pencere kaymaz, zincir durur
    other = fx.tmp / "net"
    other.mkdir()
    nx = Fx(other)
    nx.set_ctl(gap={"symbol": "BTCUSDT", "interval": "5m", "date": "2023-03-10", "before": "2099-01-01",
                    "kind": "network"})
    nx.write_root_json("DEV_DECISION.json", {"advanced": ["S5"]})
    proc = nx.run_file("run_val.sh", E17_WINDOW_REPO=str(REPO_ROOT), E17_FILL_FALLBACK_WORKERS="8")
    assert proc.returncode == 97 and not (nx.root / "VAL_WINDOW_CUT.json").exists()


def test_val_transient_fill_failure_retries_once_without_cutting(fx: Fx) -> None:
    fx.set_ctl(fill_fail_once="SOLUSDT")
    fx.write_root_json("DEV_DECISION.json", {"advanced": ["S5"]})
    proc = fx.run_file("run_val.sh", E17_FILL_FALLBACK_WORKERS="8")
    assert proc.returncode == 0, proc.stderr
    chain = (fx.root / "CHAIN.log").read_text()
    assert "1 kez yeniden deneniyor" in chain and "VAL penceresi kesildi" not in chain
    assert not (fx.root / "VAL_WINDOW_CUT.json").exists()


def test_val_cut_infrastructure_failure_is_not_val_yapilamadi(fx: Fx) -> None:
    fx.set_ctl(gap={"symbol": "BTCUSDT", "interval": "5m", "date": "2023-03-10", "before": "2099-01-01"})
    fx.write_root_json("DEV_DECISION.json", {"advanced": ["S5"]})
    proc = fx.run_file("run_val.sh", E17_WINDOW_REPO=str(fx.tmp / "yok"), E17_FILL_FALLBACK_WORKERS="8")
    assert proc.returncode == 97 and "altyapı hatası" in proc.stderr
    chain = (fx.root / "CHAIN.log").read_text()
    assert "VAL-YAPILAMADI:" not in chain, "terminal etiket yalnız gerçek VAL-YAPILAMADI'da yazılır"
    assert not (fx.root / "VAL_WINDOW_CUT.json").exists()


def test_val_gap_rule_gives_up_when_gap_cannot_be_excluded(fx: Fx) -> None:
    fx.set_ctl(gap={"symbol": "BTCUSDT", "interval": "5m", "date": "2024-06-01", "before": "2099-01-01"})
    fx.write_root_json("DEV_DECISION.json", {"advanced": ["S5"]})
    proc = fx.run_file("run_val.sh", E17_WINDOW_REPO=str(REPO_ROOT), E17_FILL_FALLBACK_WORKERS="8")
    assert proc.returncode == 97 and "VAL-YAPILAMADI" in proc.stderr
    cut = json.loads((fx.root / "VAL_WINDOW_CUT.json").read_text())
    assert cut["status"] == "VAL-YAPILAMADI"
    assert fx.calls() == 0
    # kesik pencere kaydı varken yeniden başlatma da reddedilir
    assert fx.run_file("run_val.sh", E17_WINDOW_REPO=str(REPO_ROOT)).returncode == 97


def test_non_gap_fill_failure_does_not_shift_val_window(fx: Fx) -> None:
    fx.set_ctl(fill_fail_always="ETHUSDT")
    fx.write_root_json("DEV_DECISION.json", {"advanced": ["S5"]})
    proc = fx.run_file("run_val.sh", E17_FILL_FALLBACK_WORKERS="8")
    assert proc.returncode == 97
    assert not (fx.root / "VAL_WINDOW_CUT.json").exists()


# ── envtool birim testleri ───────────────────────────────────────────────────

def test_envtool_resolve_env_is_snapshot_plus_overrides_in_order(fx: Fx) -> None:
    snap = ET.load_snapshot(fx.snapshot)
    assert "NOT_SCALPER_KEY" not in snap and snap["SCALPER_ENTRY_BLOCK_WEEKDAYS_UTC"] == "5,6"
    env = ET.resolve_env(fx.variants, snap, "S5", ["ROB_TAKER", "ROB_WALL"])
    assert env["SCALPER_ENTRY_BLOCK_WEEKDAYS_UTC"] == ""
    assert env["SCALPER_ENTRY_MODE"] == "taker" and env["SCALPER_MAX_HOLD_HOURS"] == "8"
    assert env["SCALPER_FIXED_STOP_ROI_PCT"] == "5" and ET.gate_on(env) is False
    assert ET.gate_on(ET.resolve_env(fx.variants, snap, "B0")) is True
    assert ET.gate_on(ET.resolve_env(fx.variants, snap, "R1")) is False
    assert ET.gate_on(ET.resolve_env(fx.variants, snap, "S5G")) is True and ET.gate_on(ET.resolve_env(fx.variants, snap, "T5G"))


def test_envtool_snapshot_never_printed(fx: Fx) -> None:
    for sub in (["triple", "--variant", "S5"], ["gate", "--variant", "S5"], ["cli"], ["ids"]):
        cmd = [sys.executable, str(E17 / "envtool.py"), sub[0], "--variants", str(fx.variants_path)]
        if sub[0] in ("triple", "gate"):
            cmd += ["--snapshot", str(fx.snapshot)]
        cmd += sub[1:]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        assert SECRET_MARK not in proc.stdout + proc.stderr


def test_envtool_stage_file_parsers(fx: Fx) -> None:
    gates = {f"G{i}": {"pass": True} for i in range(6)}
    screen = {"S5": gates, "T5": {**gates, "G3": {"pass": False}}, "S60": gates}
    assert ET.screen_arms(fx.variants, screen) == ["S5"]                  # açıklayıcı/başarısız hariç
    # G5 tek başına başarısızsa da elenir
    assert ET.screen_arms(fx.variants, {"S5": {**gates, "G5": {"pass": False}}}) == []
    assert ET.screen_arms(fx.variants, {"g6_arms": ["T30", "S5", "X"]}) == ["S5", "T30"]
    # YETKİLİ kaynak: analizcinin _meta.g6_needed'ı (G0..G5'i yeniden hesaplamaz)
    assert ET.screen_arms(fx.variants, {"S5": {**gates, "G2": {"pass": False}},
                                        "_meta": {"g6_needed": ["T30", "S15"]}}) == ["S15", "T30"]
    with pytest.raises(ET.ToolError):
        ET.screen_arms(fx.variants, {"_meta": {"g6_needed": ["S60"]}})            # açıklayıcı aile dışı
    assert ET.advanced_arms({"advanced": ["S5", {"arm": "T5"}]}) == ["S5", "T5"]
    with pytest.raises(ET.ToolError):
        ET.advanced_arms({"advanced": ["B0"]}, fx.variants)                       # aile üyesi değil
    assert ET.fee2_failed_arms({"g6": {"S5": {"fee2": {"pass": False}}, "T5": {"fee2": True}}}) == ["S5"]
    assert ET.val_winners({"S5": {"winner": True}, "T5": {"winner": False}}) == ["S5"]
    assert ET.val_winners({"winner": "T15"}) == ["T15"]
    assert ET.val_winners({"S5": {"winner": False}, "_meta": {"winner": None}}) == []
    assert ET.val_winners({"S5": {"winner": False}, "_meta": {"winner": "S5"}}) == ["S5"]


def test_envtool_stage_file_parsers_fail_loudly_on_schema_drift(fx: Fx) -> None:
    # tanınmayan hüküm biçimi sessizce "geçmedi/atla" sayılmaz: ToolError
    with pytest.raises(ET.ToolError):
        ET.screen_arms(fx.variants, {"S5": {f"G{i}": {"status": "PASS"} for i in range(6)}})
    with pytest.raises(ET.ToolError):
        ET.screen_arms(fx.variants, {"S5": {f"G{i}": {"pass": "yes"} for i in range(6)}})
    with pytest.raises(ET.ToolError):
        ET.fee2_failed_arms({"g6": {"S5": {"fee2": 0.97}}})                       # sayısal fee2
    with pytest.raises(ET.ToolError):
        ET.fee2_failed_arms({"g6": {"S5": {"dly1": {"pass": True}}}})            # fee2 yok
    with pytest.raises(ET.ToolError):
        ET.val_winners({"S15": {"winner": "true"}})                               # dizge winner
    with pytest.raises(ET.ToolError):
        ET.val_winners({"_meta": {"winner": 3}})


def test_envtool_parse_gaps_and_val_cut_arithmetic() -> None:
    text = ("HATA: BTCUSDT-5m-2023-03-10.zip arşivde YOK (404). x\nHATA: ETHUSDT-1h-2023-02-01.zip arşivde YOK (404)\n")
    assert ET.parse_gaps(text) == [("ETHUSDT", "1h", "2023-02-01"), ("BTCUSDT", "5m", "2023-03-10")]
    v = make_variants("x")
    r = ET.compute_val_cut(REPO_ROOT, v, "2023-01-01", "2024-11-18", [["5m", "30m", "1h"]], "2023-03-10")
    assert r["status"] == "CUT" and r["val_start"] == "2023-04-01"
    r = ET.compute_val_cut(REPO_ROOT, v, "2023-01-01", "2024-11-18", [["15m", "1h", "4h"]], "2023-03-10")
    assert r["val_start"] == "2023-05-01"            # 41.7 gün ısınma: nisan başı yetmez
    r = ET.compute_val_cut(REPO_ROOT, v, "2023-01-01", "2024-11-18", [["5m", "30m", "1h"]], "2024-05-01")
    assert r["status"] == "VAL-YAPILAMADI"
    # boşluk, en erken barın bulunduğu GÜNÜN içinde (2023-03-21 14:00 sonrası): o günün zip'i de gerekli
    # -> 2023-04-01 yetmez (gün sonu hesabı), mayıs başı gerekir
    r = ET.compute_val_cut(REPO_ROOT, v, "2023-01-01", "2024-11-18", [["5m", "30m", "1h"]], "2023-03-21")
    assert r["val_start"] == "2023-05-01"
    # 6 aylık kaydırma sınırı: boşluk her adayın ısınma penceresinin içindeyse yapılamadı
    r = ET.compute_val_cut(REPO_ROOT, v, "2023-01-01", "2024-11-18", [["5m", "30m", "1h"]], "2023-07-15",
                           max_shift_months=2)
    assert r["status"] == "VAL-YAPILAMADI"


def test_envtool_parse_gaps_validation_errors_and_network_noise() -> None:
    text = (
        "HATA: CHECKSUM UYUŞMUYOR SOLUSDT-5m-2023-02-02.zip: beklenen aa, hesaplanan bb\n"
        "HATA: SOLUSDT-5m-2023-02-03.zip: bozuk zip (BadZipFile)\n"
        "HATA: CHECKSUM dosyası yok (404): https://x/klines/BTCUSDT/1h/BTCUSDT-1h-2023-02-04.zip.CHECKSUM — d\n"
        "HATA: İndirilemedi: https://x/klines/BTCUSDT/1h/BTCUSDT-1h-2023-02-05.zip ('timeout')\n"
        "  ! indirme önbelleği bozuk, yeniden indirilecek: BTCUSDT-1h-2023-02-06.zip\n"
        "  + indirildi BTCUSDT-1h-2023-02-07.zip (123 bayt)\n"
        "HATA: ETHUSDT-5m-2023-03.zip: aylık dosya bozuk\n")
    got = ET.parse_gaps(text)
    assert ("SOLUSDT", "5m", "2023-02-02") in got and ("SOLUSDT", "5m", "2023-02-03") in got
    assert ("BTCUSDT", "1h", "2023-02-04") in got and ("ETHUSDT", "5m", "2023-03-01") in got
    dates = {d for _s, _i, d in got}
    assert "2023-02-05" not in dates and "2023-02-06" not in dates and "2023-02-07" not in dates
    assert [d for _s, _i, d in got] == sorted(d for _s, _i, d in got)


def test_val_cut_only_counts_triples_reading_the_gap_interval() -> None:
    v = make_variants("x")
    triples = [["5m", "30m", "1h"], ["1h", "4h", "4h"]]
    # boşluk 5m'de: yalnız 5m üçlüsü (ısınma 10.4 g) belirler -> 2023-04-01
    r = ET.compute_val_cut(REPO_ROOT, v, "2023-01-01", "2024-11-18", triples, "2023-03-10", gap_interval="5m")
    assert r["val_start"] == "2023-04-01"
    # aralık hiçbir üçlüde yoksa hepsi hesaba girer (1h/4h/4h üçlüsü 41.7 g -> mayıs başı)
    r = ET.compute_val_cut(REPO_ROOT, v, "2023-01-01", "2024-11-18", triples, "2023-03-10", gap_interval="3m")
    assert r["val_start"] == "2023-05-01"


def test_envtool_lock_guard(fx: Fx, tmp_path: Path) -> None:
    lock = tmp_path / "LOCK.json"
    lock.write_text(json.dumps({
        "files": {"docs/audits/x/variants.json": ET.sha256_file(fx.variants_path)},
        "snapshot_sha256": ET.sha256_file(fx.snapshot)}))
    assert ET.lock_guard(str(lock), str(fx.variants_path), str(fx.snapshot)) == []
    fx.snapshot.write_text(fx.snapshot.read_text() + "SCALPER_TOP_N=3\n")
    assert ET.lock_guard(str(lock), str(fx.variants_path), str(fx.snapshot))


# ── verify_variants ──────────────────────────────────────────────────────────

def run_verify(fx: Fx) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(E17 / "verify_variants.py"), "--snapshot", str(fx.snapshot),
         "--variants", str(fx.variants_path), "--repo", str(REPO_ROOT)],
        capture_output=True, text=True, timeout=300)


def test_verify_variants_passes_on_consistent_fake_snapshot(fx: Fx) -> None:
    proc = run_verify(fx)
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-2000:]
    out = proc.stdout
    assert "[FAIL]" not in out and re.search(r"verify_variants: \d+ kontrol, 0 başarısız", out)
    assert SECRET_MARK not in out + proc.stderr, "snapshot değeri çıktıya sızdı"
    # kapsam: her varyant + her aile/açıklayıcı x kaplama bileşimi gerçek Settings'ten geçti
    assert out.count("Settings(") >= 12 + 8 * 7
    assert "Settings(S15+ROB_WALL)" in out and "SCALPER_ENTRY_BLOCK_WEEKDAYS_UTC" not in out
    assert "stop bandı [0.250,1.667]" in out                                    # S5: 5/20 .. 5/3
    assert "kilitli en erken bar (backtest fonksiyonlarıyla): 5m/30m/1h DEV == 2024-12-21 14:00" in out
    assert "sızıntı duvarı" in out and "2024-11-20 08:00" in out


def mutate(fx: Fx, fn) -> subprocess.CompletedProcess:
    fn(fx.variants)
    fx.variants_path.write_text(json.dumps(fx.variants))
    return run_verify(fx)


def test_verify_variants_detects_role_collision(fx: Fx) -> None:
    proc = mutate(fx, lambda v: v["family"][2]["env"].update({"SCALPER_TF_CONTEXT": "15m"}))
    assert proc.returncode == 1
    assert "[FAIL] S15: rol çakışması yok" in proc.stdout and "[FAIL] S15: TF üçlüsü == triple" in proc.stdout


def test_verify_variants_detects_stop_band_violation(fx: Fx) -> None:
    def tamper(v):
        e = v["family"][0]["env"]
        e.update({"SCALPER_FIXED_STOP_ROI_PCT": "2", "SCALPER_TP1_ROI": "2", "SCALPER_TP2_ROI": "4"})
        v["family"][0]["S_arm"] = 2
    proc = mutate(fx, tamper)
    assert proc.returncode == 1 and "[FAIL] S5: stop bandı" in proc.stdout     # 2/20 = 0.10 < MIN_STOP 0.15


def test_verify_variants_detects_wall_breach_and_hash_mismatch(fx: Fx) -> None:
    proc = mutate(fx, lambda v: v["sets"].update({"VAL": ["2023-01-01", "2025-06-01"]}))
    assert proc.returncode == 1 and "[FAIL] sızıntı duvarı" in proc.stdout
    fx2_snapshot = fx.snapshot.read_text() + "SCALPER_TOP_N=3\n"
    fx.snapshot.write_text(fx2_snapshot)
    proc = run_verify(fx)
    assert proc.returncode == 1 and "[FAIL] snapshot sha256" in proc.stdout


def test_verify_variants_detects_wrong_tb_and_bg(fx: Fx) -> None:
    proc = mutate(fx, lambda v: v["family"][0]["env"].update({"SCALPER_MAX_HOLD_HOURS": "8"}))
    assert proc.returncode == 1 and "[FAIL] S5: TB süre kuralı" in proc.stdout
    (fx.tmp / "x").mkdir()
    fx2 = Fx(fx.tmp / "x")
    proc = mutate(fx2, lambda v: v["family"][1]["env"].update({"SCALPER_MARKET_GATE": "true"}))
    assert proc.returncode == 1 and "[FAIL] T5: BG kapı paketi" in proc.stdout


def test_verify_variants_settings_rejection_does_not_leak_values(fx: Fx) -> None:
    fx.snapshot.write_text(fx.snapshot.read_text() + "SCALPER_MAX_POSITIONS=notanint-LEAKCHECK\n")
    fx.variants["snapshot"]["sha256"] = ET.sha256_file(fx.snapshot)
    fx.variants_path.write_text(json.dumps(fx.variants))
    proc = run_verify(fx)
    assert proc.returncode == 1 and "doğrulayıcı reddetti" in proc.stdout
    assert "LEAKCHECK" not in proc.stdout + proc.stderr and SECRET_MARK not in proc.stdout + proc.stderr


def test_verify_variants_structure_counts(fx: Fx) -> None:
    proc = mutate(fx, lambda v: v["family"].pop())
    assert proc.returncode == 1 and "[FAIL] family sayısı == 6" in proc.stdout


def test_values_equal_normalisation() -> None:
    assert VV.values_equal(False, "false") and VV.values_equal(True, "TRUE") and not VV.values_equal(True, "false")
    assert VV.values_equal(40.0, "40") and VV.values_equal(3, "3.0") and not VV.values_equal(5.5, "5.6")
    assert VV.values_equal("", "") and VV.values_equal("5,6", "5,6") and not VV.values_equal("5,6", "")
    assert VV.minutes_of("5m") == 5 and VV.minutes_of("1h") == 60 and VV.minutes_of("4h") == 240


@pytest.mark.skipif(not os.environ.get("E17_REAL_VARIANTS") or not os.environ.get("E17_REAL_SNAPSHOT"),
                    reason="gerçek variants.json/snapshot yalnız yerelde (E17_REAL_* ile)")
def test_verify_variants_on_real_inputs() -> None:  # pragma: no cover - yerel doğrulama
    proc = subprocess.run(
        [sys.executable, str(E17 / "verify_variants.py"), "--snapshot", os.environ["E17_REAL_SNAPSHOT"],
         "--variants", os.environ["E17_REAL_VARIANTS"], "--repo", str(REPO_ROOT), "--quiet"],
        capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stdout[-3000:]


# ── Önbellek koruması (backtest yalnız OKUR) ─────────────────────────────────

def test_backtest_writing_to_cache_is_detected_and_stops_chain(fx: Fx) -> None:
    fx.set_ctl(cache_write=True)
    proc = fx.run_script('run_bt DEV_S5 S5 2025-01-01 2026-01-01\necho NOT-REACHED')
    assert proc.returncode == 97 and "NOT-REACHED" not in proc.stdout
    assert "BT FAIL DEV_S5" in proc.stdout
    assert "önbellek backtest sırasında DEĞİŞTİ" in (fx.root / "CHAIN.log").read_text()
    assert not (fx.root / "ledger.jsonl").exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root dosya izinlerini aşar; parmak izi koruması yukarıda test edildi")
def test_cache_is_readonly_during_backtests_and_writable_only_for_fills(fx: Fx) -> None:
    fx.set_ctl(fill_writes_cache=True)
    proc = fx.run_script('fill_or_die A S5 2025-01-01 2026-01-01\nfill_or_die B S15 2025-01-01 2026-01-01')
    assert proc.returncode == 0, proc.stderr          # ikinci doldurma salt-okunur önbelleği yeniden açabilir
    cache = fx.root / "cache"
    assert len(list(cache.glob("F_*.json.gz"))) >= 16
    assert not os.access(cache, os.W_OK), "doldurma bitince önbellek salt-okunur olmalı"
    assert all(not os.access(p, os.W_OK) for p in cache.glob("F_*"))
    fx.set_ctl(cache_write=True)
    proc = fx.run_script('run_bt DEV_S5 S5 2025-01-01 2026-01-01')
    assert proc.returncode == 97                      # yazma denemesi PermissionError ile de durur
    assert not (cache / "POISON_BTCUSDT_5m.json.gz").exists()


# ── Etiket / komut dilbilgisi: GERÇEK analizciyle (paralel bileşen birleşince) ───

ANALYZER_REAL = E17 / "analyze_e17.py"


@pytest.mark.skipif(not ANALYZER_REAL.exists(), reason="analyze_e17.py bu dalda yok (paralel bileşen)")
def test_labels_and_commands_match_real_analyzer_grammar(fx: Fx) -> None:
    import importlib.util
    spec = importlib.util.spec_from_file_location("analyze_e17_real", ANALYZER_REAL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["analyze_e17_real"] = mod
    spec.loader.exec_module(mod)
    dev_fixture(fx)
    fx.write_root_json("VAL_DECISION.json", {"S15": {"winner": True}, "_meta": {"winner": "S15"}})
    seen_labels: List[str] = []
    stages: List[List[str]] = []
    for name in ("run_smoke.sh", "run_dev.sh", "run_val.sh", "run_final.sh"):
        proc = fx.run_file(name, E17_DRY_RUN="1")
        assert proc.returncode == 0, (name, proc.stderr)
        seen_labels += labels(proc)
        stages += [ln.replace("DRY ANALYZER ", "").split() for ln in proc.stdout.splitlines()
                   if ln.startswith("DRY ANALYZER")]
    assert len(set(seen_labels)) >= 55
    for lbl in set(seen_labels):
        mod.parse_label(lbl, fx.variants)              # UsageError => etiket dilbilgisi uyuşmuyor
    for st in stages:
        assert st[0] in mod.STAGES, st
        if st[0] == "manifest":
            assert st[1] in ("smoke", "dev", "val", "final", "runs"), st
    for lbl in E17_SMOKE_LABELS.split():
        mod.parse_label(lbl, fx.variants)


# ── run_prova.sh (P0 doldurma provası) ───────────────────────────────────────

def test_run_prova_fills_only_inside_cal_window(fxp: Fx) -> None:
    proc = fxp.run_file("run_prova.sh", "S15")
    assert proc.returncode == 0, proc.stderr
    assert "FILL OK PROVA_S15" in proc.stdout and len(fxp.fill_records()) == 8        # 8 işçilik prova
    starts = {r["argv"][r["argv"].index("--start") + 1] for r in fxp.fill_records()}
    ends = {r["argv"][r["argv"].index("--end") + 1] for r in fxp.fill_records()}
    assert starts == {"2026-04-01"} and ends == {"2026-05-01"}
    assert fxp.calls() == 0 and fxp.analyzer_calls() == []
    n = len(fxp.fill_records())
    for bad in (("2025-01-01", "2026-01-01"), ("2023-01-01", "2023-02-01"), ("2026-09-03", "2026-10-08"),
                ("2026-03-01", "2026-06-02"), ("2026-05-01", "2026-04-01"), ("x", "y")):
        proc = fxp.run_file("run_prova.sh", "S15", *bad)
        assert proc.returncode == 97, bad
    assert len(fxp.fill_records()) == n, "CAL dışı pencerede hiçbir şey indirilmemeli"


# ── verify_variants: planlı olmayan override anahtarları ─────────────────────

def test_verify_variants_flags_typo_and_unplanned_override_keys(fx: Fx) -> None:
    proc = mutate(fx, lambda v: v["family"][0]["env"].update({"SCALPER_FIXED_STOP_ROI_PCTX": "7"}))
    assert proc.returncode == 1
    assert "[FAIL] variants.json override anahtarları planlı kümede" in proc.stdout
    assert "SCALPER_FIXED_STOP_ROI_PCTX" in proc.stdout and "Settings alanı değil" in proc.stdout
    (fx.tmp / "y").mkdir()
    fx2 = Fx(fx.tmp / "y")
    proc = mutate(fx2, lambda v: v["family"][3]["env"].update({"SCALPER_MAX_POSITIONS": "9"}))
    assert proc.returncode == 1 and "planda olmayan: SCALPER_MAX_POSITIONS" in proc.stdout
    (fx.tmp / "z").mkdir()
    fx3 = Fx(fx.tmp / "z")
    proc = mutate(fx3, lambda v: v["overlays"][0]["env"].update({"SCALPER_TYPO_KEY": "1"}))
    assert proc.returncode == 1 and "SCALPER_TYPO_KEY" in proc.stdout


# ── Mutasyon avında hayatta kalan davranışlar ────────────────────────────────

def test_run_bt_rejects_invalid_label_characters(fx: Fx) -> None:
    for bad in ("DEV S5", "DEV/S5", "DEV-S5", "../x", ""):
        proc = fx.run_script(f'run_bt "{bad}" S5 2025-01-01 2026-01-01')
        assert proc.returncode == 97 and "geçersiz koşu etiketi" in proc.stderr, bad
    assert fx.calls() == 0


def test_run_bt_role_collision_detected_even_when_variants_triple_is_consistent(tmp_path: Path) -> None:
    probe = Fx(tmp_path)
    v = probe.variants
    v["family"][0]["env"]["SCALPER_TF_CONTEXT"] = "5m"
    v["family"][0]["triple"] = ["5m", "5m", "1h"]          # triple de çakışık: yalnız çakışma kontrolü yakalar
    probe.write_variants(v)
    proc = probe.run_script('run_bt DEV_S5 S5 2025-01-01 2026-01-01')
    assert proc.returncode == 97 and "ROL ÇAKIŞMASI" in proc.stderr and probe.calls() == 0
