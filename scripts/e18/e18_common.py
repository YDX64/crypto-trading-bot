"""E18 ortak sozlesme: yollar, etiketler ve varyant cozumleme.

Kosucu (run_e18.py) ile analizci (analyze_e18.py) AYNI kurallari bu modulden okur;
kural degisikligi tek yerde yapilir. Plan: docs/audits/2026-11-20-prereg-e18/PLAN.md.
Yalniz stdlib kullanir; src/ icinden hicbir sey iceri almaz.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable, Optional, Sequence

PLAN_REL = "docs/audits/2026-11-20-prereg-e18"
E17_LABELS_REL = "docs/audits/2026-10-10-prereg-tf/labels_frozen.json"
E17_BTC1D_REL = "docs/audits/2026-10-10-prereg-tf/evidence/rev_btc"
SCHEMA = "e18-variants/1"
OVERLAY_SEP = "__"
BARS_PER_DAY = {"1m": 1440, "5m": 288, "15m": 96, "30m": 48, "1h": 24, "4h": 6}

# Settings zorunlu alanlari icin zararsiz sahte degerler (gercek gizli DEGIL).
DUMMY_SECRETS = {
    "BINANCE_API_KEY": "dummy",
    "BINANCE_API_SECRET": "dummy",
    "TELEGRAM_BOT_TOKEN": "dummy",
    "TELEGRAM_CHAT_ID": "0",
    "OPENAI_API_KEY": "dummy",
    "GEMINI_API_KEY": "dummy",
    "DEEPSEEK_API_KEY": "dummy",
    "JWT_SECRET": "dummy",
}


def repo_root() -> Path:
    """Bu dosyanin ait oldugu depo koku (scripts/e18/ -> iki ust)."""
    return Path(__file__).resolve().parents[2]


def plan_dir(repo: Optional[Path] = None) -> Path:
    return (repo or repo_root()) / PLAN_REL


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_variants(path: Optional[Path] = None) -> dict:
    p = path or (plan_dir() / "variants.json")
    data = json.loads(Path(p).read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA:
        raise ValueError(f"variants sema uyusmuyor: {data.get('schema')!r} != {SCHEMA!r}")
    return data


def arm_ids(variants: dict) -> list[str]:
    return [a["id"] for a in variants["family"]]


def get_arm(variants: dict, arm_id: str) -> dict:
    for arm in variants["family"]:
        if arm["id"] == arm_id:
            return arm
    raise KeyError(f"bilinmeyen kol: {arm_id!r}")


def get_overlay(variants: dict, overlay_id: str) -> dict:
    for ov in variants["overlays"]:
        if ov["id"] == overlay_id:
            return ov
    raise KeyError(f"bilinmeyen kaplama: {overlay_id!r}")


def triple_of(variants: dict, arm_id: str) -> list[str]:
    """[giris, baglam, rejim] zaman dilimleri."""
    return list(variants["triples"][get_arm(variants, arm_id)["entry_tf"]]["triple"])


def resolve_env(variants: dict, arm_id: str, overlays: Sequence[str] = ()) -> dict[str, str]:
    """Kolun override'lari: base_env + uclu env + kol env + kaplama env (bu sirayla; sonraki ezer).

    Snapshot satirlari BURADA yoktur; kosucu snapshot'in SCALPER_* satirlarini once koyar,
    bu sozlugu ustune uygular.
    """
    arm = get_arm(variants, arm_id)
    env: dict[str, str] = {}
    env.update(variants["base_env"])
    env.update(variants["triples"][arm["entry_tf"]]["env"])
    env.update(arm["env"])
    for ov_id in overlays:
        env.update(get_overlay(variants, ov_id).get("env", {}))
    return {k: str(v) for k, v in env.items()}


def resolve_cli(variants: dict, overlays: Sequence[str] = ()) -> list[str]:
    """Backtest bayraklari: common_cli + --symbols + kaplama bayraklari (pencere ve onbellek haric)."""
    cli = list(variants["common_cli"]) + ["--symbols", variants["symbols"]]
    for ov_id in overlays:
        cli.extend(get_overlay(variants, ov_id).get("cli", []))
    return cli


def resolve_S(variants: dict, arm_id: str, overlays: Sequence[str] = ()) -> float:
    """R biriminin paydasi: SCALPER_FIXED_STOP_ROI_PCT (kaplama S verirse o gecerli)."""
    s = float(variants["triples"][get_arm(variants, arm_id)["entry_tf"]]["S"])
    for ov_id in overlays:
        ov = get_overlay(variants, ov_id)
        if "S" in ov:
            s = float(ov["S"])
    return s


def set_window(variants: dict, set_name: str) -> tuple[str, str]:
    """(baslangic, bitis) UTC YYYY-MM-DD; bitis HARIC."""
    start, end = variants["sets"][set_name]
    return str(start), str(end)


def make_label(set_name: str, arm_id: str, overlays: Iterable[str] = ()) -> str:
    """Taban: SET_KOL; kaplama: SET_KOL__KAPLAMA (birden cok kaplama '+' ile)."""
    base = f"{set_name}_{arm_id}"
    ovs = list(overlays)
    return base if not ovs else base + OVERLAY_SEP + "+".join(ovs)


def parse_label(label: str) -> tuple[str, str, list[str]]:
    """make_label'in tersi -> (set, kol, [kaplamalar])."""
    head, _, tail = label.partition(OVERLAY_SEP)
    set_name, _, arm_id = head.partition("_")
    return set_name, arm_id, ([x for x in tail.split("+") if x] if tail else [])


class Paths:
    """E18_ROOT altindaki duzen (depo DISI; onbellek ve muhurlu raporlar burada)."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.cache = self.root / "cache"
        self.vision = self.root / "vision"
        self.funding = self.root / "funding"
        self.fill_logs = self.root / "fill_logs"
        self.sealed_runs = self.root / "sealed" / "runs"
        self.sealed_logs = self.root / "sealed" / "logs"
        self.ledger = self.root / "ledger.jsonl"
        self.clone = self.root / "clone_H"
        self.lock_ref = self.root / "LOCK_REF.json"
        self.manifest = self.root / "MANIFEST.data.json"

    def report(self, label: str) -> Path:
        return self.sealed_runs / f"{label}.json"

    def log(self, label: str) -> Path:
        return self.sealed_logs / f"{label}.log"

    def ledger_rows(self) -> list[dict]:
        if not self.ledger.exists():
            return []
        return [json.loads(line) for line in self.ledger.read_text(encoding="utf-8").splitlines() if line.strip()]

    def ledger_labels(self) -> set[str]:
        return {row["label"] for row in self.ledger_rows()}
