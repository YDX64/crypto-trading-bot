#!/usr/bin/env python3
"""E17 koşu betiklerinin yardımcı aracı (PLAN §3, §4, §5.2, §9.1, Ek A).

Yalnız standart kütüphane (yardımcı çocuk süreç hariç). Bash betikleri
(`lib17.sh`, `run_*.sh`) JSON ayrıştırma, env çözme ve aşama dosyası okuma
işlerini bununla yapar; böylece `jq`/`mapfile`/`declare -A` gerekmez.

GÜVENLİK: sunucu snapshot'ının İÇERİĞİ hiçbir alt komutta stdout/stderr'e
yazılmaz. `env` alt komutu çözülmüş env'i NUL ayraçlı olarak yazar ve YALNIZ
`env -i` komut satırına girmesi için bash tarafından okunur (konsola dönmez).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

PINNED_SYMBOLS = "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,BNBUSDT,ADAUSDT,LTCUSDT"
SCHEMA = "e17-variants/2"
PLAN_DIR = "docs/audits/2026-10-10-prereg-tf"

# Sahte gizliler (PLAN §5.2): Settings doğrulayıcılarını geçmek için; gerçek
# değer DEĞİL, `.env` YOK. Tek kaynak burası (bash bunu `dummy` ile okur).
DUMMY_SECRETS: Tuple[Tuple[str, str], ...] = (
    ("BINANCE_API_KEY", "dummy"),
    ("BINANCE_API_SECRET", "dummy"),
    ("TELEGRAM_BOT_TOKEN", "dummy"),
    ("TELEGRAM_CHAT_ID", "0"),
    ("OPENAI_API_KEY", "dummy"),
    ("GEMINI_API_KEY", "dummy"),
    ("DEEPSEEK_API_KEY", "dummy"),
    ("JWT_SECRET", "dummydummydummydummydummydummydummy12"),
)

GATE_KEY = "SCALPER_MARKET_GATE"
TF_KEYS = ("SCALPER_TF_ENTRY", "SCALPER_TF_CONTEXT", "SCALPER_TF_REGIME")
_TRUE = {"1", "true", "yes", "on", "t", "y"}


class ToolError(Exception):
    """Kullanıcıya okunur hata (çıkış kodu 3; bash `die`)."""


# --------------------------------------------------------------------------
# Yükleme / çözme
# --------------------------------------------------------------------------

def load_json(path: str | Path) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        raise ToolError(f"JSON okunamadı: {path} ({type(exc).__name__})") from exc


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _unquote(val: str) -> str:
    if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
        return val[1:-1]
    return val


def load_snapshot(path: str | Path) -> Dict[str, str]:
    """Snapshot'tan YALNIZ `SCALPER_*` satırlarını sıralı sözlük olarak okur."""
    out: Dict[str, str] = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ToolError(f"snapshot okunamadı ({type(exc).__name__})") from exc
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, val = line.partition("=")
        key = key.strip()
        if not sep or not re.fullmatch(r"SCALPER_[A-Z0-9_]+", key):
            continue
        out[key] = _unquote(val.strip())
    return out


def all_variants(variants: Dict[str, Any]) -> List[Dict[str, Any]]:
    return (
        list(variants.get("family", []))
        + list(variants.get("descriptive", []))
        + list(variants.get("references", []))
    )


def find_variant(variants: Dict[str, Any], vid: str) -> Dict[str, Any]:
    for v in all_variants(variants):
        if v.get("id") == vid:
            return v
    raise ToolError(f"bilinmeyen varyant: {vid!r}")


def find_overlay(variants: Dict[str, Any], oid: str) -> Dict[str, Any]:
    for o in variants.get("overlays", []):
        if o.get("id") == oid:
            return o
    raise ToolError(f"bilinmeyen kaplama: {oid!r}")


def load_variants(path: str | Path) -> Dict[str, Any]:
    data = load_json(path)
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise ToolError(f"variants.json şeması {SCHEMA!r} değil")
    return data


def resolve_env(
    variants: Dict[str, Any], snapshot: Dict[str, str], vid: str,
    overlays: Sequence[str] = (),
) -> Dict[str, str]:
    """snapshot + varyantın ÇÖZÜLMÜŞ override'ı + kaplama env'leri (sırayla)."""
    env = dict(snapshot)
    env.update({k: str(v) for k, v in find_variant(variants, vid).get("env", {}).items()})
    for oid in overlays:
        env.update({k: str(v) for k, v in find_overlay(variants, oid).get("env", {}).items()})
    return env


def parse_bool(value: Optional[str]) -> bool:
    return str(value if value is not None else "").strip().lower() in _TRUE


def gate_on(env: Dict[str, str]) -> bool:
    return parse_bool(env.get(GATE_KEY))


def resolve_triple(
    variants: Dict[str, Any], env: Dict[str, str], vid: str,
) -> Tuple[str, str, str]:
    """(giriş, bağlam, rejim) — env'den; variants.json `triple` ile eşit ve
    rol-anahtarı çakışmasız olmalıdır (PLAN §2.1 kritik engel 1)."""
    missing = [k for k in TF_KEYS if not env.get(k)]
    if missing:
        raise ToolError(f"{vid}: çözülmüş env'de TF anahtarı yok: {','.join(missing)}")
    triple = tuple(env[k] for k in TF_KEYS)
    expected = find_variant(variants, vid).get("triple")
    if expected is not None and list(triple) != list(expected):
        raise ToolError(f"{vid}: çözülmüş TF üçlüsü variants.json'dakiyle eşit değil")
    entry, ctx, regime = triple
    if entry == ctx or entry == regime:
        raise ToolError(
            f"{vid}: ROL ÇAKIŞMASI (giriş==bağlam veya giriş==rejim): {entry}/{ctx}/{regime}"
        )
    return entry, ctx, regime


def cli_args(variants: Dict[str, Any], overlays: Sequence[str] = ()) -> List[str]:
    common = [str(x) for x in variants.get("common_cli", [])]
    if "--symbols" not in common or common[common.index("--symbols") + 1] != PINNED_SYMBOLS:
        raise ToolError("common_cli --symbols sabit sırayla (PLAN §3.1 R5) değil")
    out = list(common)
    for oid in overlays:
        out.extend(str(x) for x in find_overlay(variants, oid).get("cli", []))
    for a in out:
        if a.startswith("--tf-"):
            raise ToolError("--tf-* bayrakları YASAK (PLAN §4.1)")
        if a in ("--start", "--end", "--cache-dir"):
            raise ToolError(f"{a} kaplama/ortak cli'da olamaz (betik verir)")
    return out


def get_window(variants: Dict[str, Any], name: str) -> Tuple[str, str]:
    w = variants.get("sets", {}).get(name)
    if not w or len(w) != 2:
        raise ToolError(f"pencere yok: {name!r}")
    return str(w[0]), str(w[1])


# --------------------------------------------------------------------------
# Aşama dosyaları (analizci çıktıları; tolerant okuma)
# --------------------------------------------------------------------------

def _arm_id(x: Any) -> Optional[str]:
    if isinstance(x, str):
        return x
    if isinstance(x, dict):
        for k in ("arm", "id", "variant"):
            if isinstance(x.get(k), str):
                return x[k]
    return None


def _strict_pass(rec: Any, what: str) -> bool:
    """Bir hüküm kaydı: bool veya {pass|ok|passed: bool}. Başka HER ŞEY ToolError (sessiz düşme YOK:
    analizci şeması kayarsa hangi koşuların yapılacağı sessizce değişirdi)."""
    if isinstance(rec, bool):
        return rec
    if isinstance(rec, dict):
        for k in ("pass", "ok", "passed"):
            if k in rec:
                if isinstance(rec[k], bool):
                    return rec[k]
                raise ToolError(f"{what}: '{k}' bool değil ({type(rec[k]).__name__})")
    raise ToolError(f"{what}: tanınmayan hüküm biçimi ({type(rec).__name__})")


def family_ids(variants: Dict[str, Any]) -> List[str]:
    return [v["id"] for v in variants.get("family", [])]


def screen_arms(variants: Dict[str, Any], screen: Dict[str, Any]) -> List[str]:
    """G6 koşusu gereken aile arm'ları. YETKİLİ kaynak analizcinin `_meta.g6_needed` listesidir
    (G0..G5'i yeniden hesaplamak, analizciye ek koşul eklenirse sessizce ayrışırdı). `_meta` yoksa
    (eski/elle biçim) `g6_arms` listesi, o da yoksa G0..G5'in HEPSİ `pass` olan arm'lar (SIKI okuma)."""
    fam = family_ids(variants)
    meta = screen.get("_meta")
    if isinstance(meta, dict) and "g6_needed" in meta:
        need = meta["g6_needed"]
        if not isinstance(need, list) or not all(isinstance(a, str) for a in need):
            raise ToolError("DEV_SCREEN.json _meta.g6_needed arm adı listesi değil")
        bad = [a for a in need if a not in fam]
        if bad:
            raise ToolError(f"DEV_SCREEN.json g6_needed aile dışı arm içeriyor: {bad}")
        return [a for a in fam if a in need]
    if isinstance(screen.get("g6_arms"), list):
        got = [a for a in (_arm_id(x) for x in screen["g6_arms"]) if a]
        return [a for a in fam if a in got]
    out = []
    for arm in fam:
        rec = screen.get(arm)
        if not isinstance(rec, dict):
            continue
        if all(_strict_pass(rec.get(f"G{i}"), f"DEV_SCREEN.{arm}.G{i}") for i in range(6)):
            out.append(arm)
    return out


def advanced_arms(decision: Dict[str, Any], variants: Optional[Dict[str, Any]] = None) -> List[str]:
    adv = decision.get("advanced", [])
    if not isinstance(adv, list):
        raise ToolError("DEV_DECISION.json `advanced` liste değil")
    out = []
    for x in adv:
        a = _arm_id(x)
        if not a:
            raise ToolError("DEV_DECISION.json `advanced` girdisi arm adı değil")
        out.append(a)
    if variants is not None:
        fam = family_ids(variants)
        bad = [a for a in out if a not in fam]
        if bad:
            raise ToolError(f"DEV_DECISION.json advanced aile üyesi değil: {bad} (VAL yalnız aile arm'larını koşar)")
    return out


def fee2_failed_arms(decision: Dict[str, Any]) -> List[str]:
    out = []
    for arm, rec in (decision.get("g6") or {}).items():
        if not isinstance(rec, dict) or "fee2" not in rec:
            raise ToolError(f"DEV_DECISION.json g6.{arm}.fee2 yok")
        if not _strict_pass(rec["fee2"], f"DEV_DECISION.g6.{arm}.fee2"):
            out.append(arm)
    return out


def val_winners(decision: Dict[str, Any]) -> List[str]:
    """VAL kazananı: analizcinin `_meta.winner`'ı (str|None) yetkilidir; yoksa üst düzey `winner`
    (str) ya da arm başına `winner: bool`. `winner` bool/str dışıysa ToolError."""
    meta = decision.get("_meta")
    if isinstance(meta, dict) and "winner" in meta:
        w = meta["winner"]
        if w is None:
            return []
        if isinstance(w, str) and w:
            return [w]
        raise ToolError("VAL_DECISION.json _meta.winner str/None değil")
    top = decision.get("winner")
    if isinstance(top, str) and top:
        return [top]
    out = []
    for arm, rec in decision.items():
        if arm.startswith("_") or not isinstance(rec, dict) or "winner" not in rec:
            continue
        if not isinstance(rec["winner"], bool):
            raise ToolError(f"VAL_DECISION.json {arm}.winner bool değil ({type(rec['winner']).__name__})")
        if rec["winner"]:
            out.append(arm)
    return out


def cache_fingerprint(d: str | Path) -> str:
    """Önbellek dizini parmak izi (ad, bayt, mtime_ns): backtest sırasında yazıldı mı?"""
    root = Path(d)
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            st = p.stat()
            h.update(f"{p.relative_to(root).as_posix()}\0{st.st_size}\0{st.st_mtime_ns}\n".encode("utf-8"))
    return h.hexdigest()


# --------------------------------------------------------------------------
# Çocuk süreç (depo kodunu temiz env'de çalıştırır — Settings/backtest sabitleri)
# --------------------------------------------------------------------------

def child_env(repo: str | Path, extra: Dict[str, str], tmp: str) -> Dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": tmp,
        "PYTHONPATH": str(repo),
        "PYTHONHASHSEED": "0",
        "TRADINGBOT_LOG_DIR": tmp,
    }
    env.update(dict(DUMMY_SECRETS))
    env.update(extra)
    return env


def run_repo_child(
    code: str, repo: str | Path, extra_env: Dict[str, str], payload: Any,
    python: Optional[str] = None,
) -> Any:
    """`code`'u depo kökünde taze cwd + dummy env ile çalıştırır; stdin JSON,
    stdout'un SON satırı JSON. Hata: ToolError (çıktı gizli tutulur)."""
    with tempfile.TemporaryDirectory(prefix="e17child.") as tmp:
        proc = subprocess.run(
            [python or sys.executable, "-c", code],
            input=json.dumps(payload), capture_output=True, text=True,
            env=child_env(repo, extra_env, tmp), cwd=tmp, timeout=600,
        )
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-1:] or ["?"]
        raise ToolError(f"çocuk süreç başarısız (rc={proc.returncode}): {tail[0][:200]}")
    # logger satırları stdout'a karışabilir: SON JSON satırı alınır.
    for line in reversed(proc.stdout.strip().splitlines()):
        if line[:1] in "[{":
            try:
                return json.loads(line)
            except ValueError:
                continue
    raise ToolError("çocuk süreç JSON üretmedi")


# Backtest'in KENDİ pencere/ısınma fonksiyonlarıyla en erken bar (PLAN §2).
EARLIEST_CODE = r'''
import json, sys
from src.strategies.scalper import backtest as B
from src.strategies.scalper import kline_cache as K
req = json.load(sys.stdin)
res = []
for q in req["queries"]:
    days, s_ms, e_ms = B.resolve_backtest_window(0, q["start"], q["end"])
    e, c, r = q["triple"]
    parts = {}
    for role, tf, warm in zip(("entry", "context", "regime"), (e, c, r), B._WARMUP_BY_ROLE):
        needed = days * B._CANDLES_PER_DAY[tf] + warm
        parts[role] = e_ms - needed * K._INTERVAL_MS[tf]
    if q.get("leader"):
        run_days = int(q.get("run_days", 3))
        parts["leader_entry"] = e_ms - (days * B._CANDLES_PER_DAY[e] + B._CTX_5M_WINDOW) * K._INTERVAL_MS[e]
        parts["leader_intraday"] = e_ms - (days * B._CANDLES_PER_DAY[B.MARKET_GATE_INTRADAY_TF] + B._CTX_15M_WINDOW) * K._INTERVAL_MS[B.MARKET_GATE_INTRADAY_TF]
        parts["leader_daily"] = e_ms - (days + max(1, run_days) + B._LEADER_DAILY_EXTRA_DAYS) * 86400000
    res.append({"days": days, "start_ms": s_ms, "end_ms": e_ms, "earliest_ms": min(parts.values()), "parts": parts})
print(json.dumps(res))
sys.stdout.flush()
'''


def earliest_bars(
    repo: str | Path, queries: List[Dict[str, Any]], python: Optional[str] = None,
) -> List[Dict[str, Any]]:
    return run_repo_child(EARLIEST_CODE, repo, {}, {"queries": queries}, python)


def ms_to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _date_ms(d: str) -> int:
    return int(datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.year * 12 + (d.month - 1) + n, 12)
    return date(y, m + 1, 1)


# --------------------------------------------------------------------------
# VAL boşluk kuralı (PLAN §9.5)
# --------------------------------------------------------------------------

GAP_RE = re.compile(
    r"([A-Z0-9]+)-([0-9a-z]+)-(\d{4}-\d{2})(?:-(\d{2}))?\.zip(?:\.CHECKSUM)?(?![\w/])"
)


def parse_gaps(text: str) -> List[Tuple[str, str, str]]:
    """Doldurucu günlüğünden (symbol, interval, YYYY-MM-DD) boşlukları çıkarır (PLAN §9.5:
    `ArchiveGapError` = zip 404 VE doğrulama hataları: CHECKSUM UYUŞMUYOR, bozuk zip, CHECKSUM 404).
    Yalnız hata satırlarına bakılır (`HATA:` / `arşivde YOK`); salt ağ hatası (`İndirilemedi`) boşluk
    DEĞİLDİR (altyapı), aylık zip adı ayın 1'i sayılır."""
    found = set()
    for line in text.splitlines():
        if "İndirilemedi" in line:
            continue
        if "arşivde YOK" in line or line.lstrip().startswith("HATA:"):
            for sym, iv, ym, day in GAP_RE.findall(line):
                found.add((sym, iv, f"{ym}-{day or '01'}"))
    return sorted(found, key=lambda t: (t[2], t[0], t[1]))


def compute_val_cut(
    repo: str | Path, variants: Dict[str, Any], orig_start: str, val_end: str,
    triples: List[List[str]], gap_date: str, max_shift_months: int = 6,
    python: Optional[str] = None, gap_interval: Optional[str] = None,
) -> Dict[str, Any]:
    """Boşluğu (ısınma dahil) dışarıda bırakan İLK takvim ayı başı.

    Boşluk `gap_interval` serisindedir: yalnız o aralığı OKUYAN üçlüler hesaba girer
    (hiçbiri okumuyorsa hepsi); yeni başlangıç yine TÜM arm/semboller için ortaktır.

    Döner: {"status": "CUT"|"VAL-YAPILAMADI", "val_start", "shift_months", ...}.
    Ortak (tüm semboller); en çok 6 ay; kalan VAL < 365 gün ise yapılamadı.
    """
    if gap_interval:
        hit = [t for t in triples if gap_interval in t]
        triples = hit or triples
    base = date.fromisoformat(orig_start)
    end_d = date.fromisoformat(val_end)
    gap_end_ms = _date_ms(gap_date) + 86_400_000  # boşluk gününün sonu
    cands = []
    for n in range(1, max_shift_months + 1):
        s = add_months(base, n)
        if s >= end_d:
            break
        cands.append((n, s))
    queries = [
        {"triple": t, "start": s.isoformat(), "end": val_end, "leader": False}
        for n, s in cands for t in triples
    ]
    res = earliest_bars(repo, queries, python) if queries else []
    k = len(triples)
    for i, (n, s) in enumerate(cands):
        chunk = res[i * k:(i + 1) * k]
        if min(r["earliest_ms"] for r in chunk) > gap_end_ms:
            remaining = (end_d - s).days
            if remaining < 365:
                return {"status": "VAL-YAPILAMADI", "reason": f"kalan VAL {remaining} gün < 365",
                        "shift_months": n, "val_start": s.isoformat()}
            return {"status": "CUT", "val_start": s.isoformat(), "shift_months": n,
                    "remaining_days": remaining}
    return {"status": "VAL-YAPILAMADI",
            "reason": f"boşluk {max_shift_months} aylık kaydırmayla aşılamadı",
            "shift_months": max_shift_months, "val_start": None}


# --------------------------------------------------------------------------
# Kilit ön-kontrolü (yetkili doğrulama analizcidedir; bu yalnız erken uyarı)
# --------------------------------------------------------------------------

def lock_guard(lock_path: str, variants_path: str, snapshot_path: str) -> List[str]:
    lock = load_json(lock_path)
    errs = []
    files = lock.get("files", {})
    want_v = next((h for p, h in files.items() if p.endswith("variants.json")), None)
    if want_v is None:
        errs.append("LOCK.json'da variants.json hash'i yok")
    elif sha256_file(variants_path) != want_v:
        errs.append("variants.json hash'i LOCK.json ile uyuşmuyor")
    want_s = lock.get("snapshot_sha256")
    if not want_s:
        errs.append("LOCK.json'da snapshot_sha256 yok")
    elif sha256_file(snapshot_path) != want_s:
        errs.append("snapshot hash'i LOCK.json ile uyuşmuyor")
    return errs


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _nul(items: Iterable[str]) -> None:
    sys.stdout.buffer.write(b"".join(s.encode("utf-8") + b"\0" for s in items))
    sys.stdout.buffer.flush()


def _lines(items: Iterable[str]) -> None:
    for s in items:
        sys.stdout.write(s + "\n")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="envtool.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, snapshot=False, variant=False, overlays=False):
        sp.add_argument("--variants", required=True)
        if snapshot:
            sp.add_argument("--snapshot", required=True)
        if variant:
            sp.add_argument("--variant", required=True)
        if overlays:
            sp.add_argument("--overlay", action="append", default=[])

    def add(name, snapshot=False, variant=False, overlays=False, with_common=True):
        sp = sub.add_parser(name)
        if with_common:
            common(sp, snapshot, variant, overlays)
        return sp

    add("env", True, True, True)
    add("dummy", with_common=False)
    add("cli", overlays=True)
    add("triple", True, True, True)
    add("gate", True, True, True)
    add("arm", True, True, True)
    sp = add("ids")
    sp.add_argument("--group", default="all", choices=["family", "descriptive", "references", "all"])
    sp = add("window")
    sp.add_argument("--name", required=True)
    add("quarters")
    sp = add("val-window")
    sp.add_argument("--root", required=True)
    sp = add("json-get", with_common=False)
    sp.add_argument("file")
    sp.add_argument("key")
    sp = add("screen-arms")
    sp.add_argument("file")
    for name in ("fee2-failed", "winner"):
        add(name, with_common=False).add_argument("file")
    sp = add("advanced", with_common=False)
    sp.add_argument("file")
    sp.add_argument("--variants", default=None, help="verilirse her arm aile üyesi olmalı")
    sp = add("cache-fp", with_common=False)
    sp.add_argument("dir")
    sp = add("guard", with_common=False)
    sp.add_argument("--lock", required=True)
    sp.add_argument("--variants", required=True)
    sp.add_argument("--snapshot", required=True)
    add("gaps", with_common=False).add_argument("logs", nargs="+")
    sp = add("val-cut")
    sp.add_argument("--root", required=True)
    sp.add_argument("--repo", required=True)
    sp.add_argument("--gap-date", required=True)
    sp.add_argument("--gap-source", default="")
    sp.add_argument("--gap-interval", default=None)
    sp.add_argument("--triples", required=True, help="e/c/r;e/c/r ...")
    sp.add_argument("--python", default=None)
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    a = build_parser().parse_args(argv)
    try:
        return _run(a)
    except ToolError as exc:
        print(f"envtool HATA: {exc}", file=sys.stderr)
        return 3


def _run(a: argparse.Namespace) -> int:
    cmd = a.cmd
    if cmd == "dummy":
        _nul(f"{k}={v}" for k, v in DUMMY_SECRETS)
        return 0
    if cmd == "guard":
        errs = lock_guard(a.lock, a.variants, a.snapshot)
        for e in errs:
            print(f"GUARD HATA: {e}", file=sys.stderr)
        return 3 if errs else 0
    if cmd == "json-get":
        cur: Any = load_json(a.file)
        for part in a.key.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
        if cur is None:
            return 4
        sys.stdout.write((cur if isinstance(cur, str) else json.dumps(cur)) + "\n")
        return 0
    if cmd == "advanced":
        _lines(advanced_arms(load_json(a.file), load_variants(a.variants) if a.variants else None))
        return 0
    if cmd == "cache-fp":
        print(cache_fingerprint(a.dir))
        return 0
    if cmd == "fee2-failed":
        _lines(fee2_failed_arms(load_json(a.file)))
        return 0
    if cmd == "winner":
        w = val_winners(load_json(a.file))
        _lines(w)
        return 0 if len(w) == 1 else 4
    if cmd == "gaps":
        text = ""
        for p in a.logs:
            try:
                text += Path(p).read_text(encoding="utf-8", errors="replace") + "\n"
            except OSError:
                pass
        _lines(f"{s} {i} {d}" for s, i, d in parse_gaps(text))
        return 0

    variants = load_variants(a.variants)
    if cmd == "cli":
        _nul(cli_args(variants, a.overlay))
        return 0
    if cmd == "ids":
        grp = {"family": variants.get("family", []), "descriptive": variants.get("descriptive", []),
               "references": variants.get("references", []), "all": all_variants(variants)}[a.group]
        _lines(v["id"] for v in grp)
        return 0
    if cmd == "window":
        s, e = get_window(variants, a.name)
        print(f"{s} {e}")
        return 0
    if cmd == "quarters":
        b0 = find_variant(variants, "B0")
        for i, (s, e) in enumerate(b0.get("dev_quarters", []), start=1):
            print(f"Q{i} {s} {e}")
        return 0
    if cmd == "val-window":
        s, e = get_window(variants, "VAL")
        cut = Path(a.root) / "VAL_WINDOW_CUT.json"
        if cut.exists():
            rec = load_json(cut)
            if rec.get("status") == "VAL-YAPILAMADI":
                raise ToolError("VAL-YAPILAMADI (VAL_WINDOW_CUT.json): VAL koşulamaz")
            s = str(rec.get("start") or rec.get("val_start") or s)
        print(f"{s} {e}")
        return 0
    if cmd == "screen-arms":
        _lines(screen_arms(variants, load_json(a.file)))
        return 0
    if cmd == "val-cut":
        s0, e0 = get_window(variants, "VAL")
        cut_path = Path(a.root) / "VAL_WINDOW_CUT.json"
        prev = load_json(cut_path) if cut_path.exists() else {}
        orig = str((prev.get("original") or [s0])[0])
        triples = [t.split("/") for t in a.triples.split(";") if t]
        res = compute_val_cut(a.repo, variants, orig, e0, triples, a.gap_date, python=a.python,
                              gap_interval=a.gap_interval)
        rec = {
            "schema": "e17-val-window-cut/1", "status": res["status"],
            "original": [orig, e0], "val_start": res.get("val_start"), "val_end": e0,
            # Analizci (Ctx.window) `start` anahtarını okur; yapılamadıysa özgün başlangıç (hiçbir şey koşmaz).
            "start": res.get("val_start") or orig, "end": e0,
            "shift_months": res.get("shift_months"), "gap_date": a.gap_date,
            "gap_source": a.gap_source, "reason": res.get("reason"),
            "utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        tmp = cut_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, cut_path)
        print(res["status"])
        return 0 if res["status"] == "CUT" else 4

    snapshot = load_snapshot(a.snapshot)
    if cmd == "env":
        env = resolve_env(variants, snapshot, a.variant, a.overlay)
        _nul(f"{k}={v}" for k, v in env.items())
        return 0
    env = resolve_env(variants, snapshot, a.variant, a.overlay)
    if cmd == "triple":
        print(" ".join(resolve_triple(variants, env, a.variant)))
        return 0
    if cmd == "gate":
        print("1" if gate_on(env) else "0")
        return 0
    if cmd == "arm":
        # tek süreçte: "giriş bağlam rejim kapı" (üçlü doğrulaması + kapı çözümü)
        print(" ".join(resolve_triple(variants, env, a.variant)) + " " + ("1" if gate_on(env) else "0"))
        return 0
    raise ToolError(f"bilinmeyen alt komut: {cmd}")


if __name__ == "__main__":
    sys.exit(main())
