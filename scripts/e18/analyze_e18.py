#!/usr/bin/env python3
"""E18 on-kayitli analizci / karar betigi.

Plan: docs/audits/2026-11-20-prereg-e18/PLAN.md (ikili kural metni). Bu betik PLAN'in yazili kurallarini
uygular; esik, B, blok uzunlugu, alfa gibi hicbir istatistik parametresi komut satirindan DEGISTIRILEMEZ
(THRESH sabitleri; lock bu dosyanin git blob'unu kilitler).

Alt komutlar: freeze-shock, cal-counts, lock, verify-lock, check-run, s2-compare, dev-screen, dev-decide,
val-decide, final, report, package.

Cikis kodlari: 0 tamam | 2 kullanim | 3 kilit uyusmazligi | 4 asama/sira ihlali | 5 butunluk / ikinci yazim |
6 muhur (DEV_DECISION yok iken report) | 97 check-run / s2 basarisiz.
Yalniz stdlib + e18_common.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import hashlib
import io
import json
import math
import os
import platform
import random
import re
import statistics
import subprocess
import sys
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import e18_common as C  # noqa: E402

INF = float("inf")
MS_PER_DAY = 86_400_000

# ==========================================================================
# 0. SABITLER (komut satirindan degistirilemez)
# ==========================================================================
THRESH: Dict[str, Any] = {
    "BOOT_N": 19999,
    "BOOT_N_HI": 199999,
    "BLOCK_PRIMARY": 7,
    "BLOCK_SECONDARY": 28,
    "MC_SE_MULT": 3.0,
    "PF_CI_LEVEL": 0.90,
    "SHOCK_ABS_RET": 0.03,
    "BORDERLINE_ABS": 0.005,
    "ALPHA_BH": 0.10,
    "ALPHA_VAL_P7": 0.05,
    "ALPHA_VAL_P28": 0.10,
    "MAX_ADVANCE": 2,
    "G0_SILENT_RATIO": 0.10,
    "G0_EOD_MAX": 8,
    "G1_MIN_N_WINDOW": 60,
    "G2_PF": 1.15,
    "G4_MIN_POS_WINDOWS": 3,
    "G5_PF_EX_MONTH": 1.00,
    "G5_TOP_K": 5,
    "G7_MIN_N_WINDOW": 60,
    "G8_PF": 1.15,
    "G10_MIN_POS_WINDOWS": 5,
    "G10_HALF_MIN_N": 100,
    "G10_HALF_PF": 1.00,
    "G11_PF_EX_MONTH": 1.05,
    "G11_PF_EX_SYMBOL": 1.05,
    "G11_PF_LOO": 1.00,
    "G13_PF": 1.10,
    "G13_MIN_N": 100,
    "G14_CALMAR": 0.75,
    "G15_MIN_N": 20,
    "G15_VETO_PF": 0.90,
    "G15_VETO_DD_R": 25.0,
    "G15_OK_PF": 1.00,
    "FLAG_ONE_SIDED_MIN_N": 100,
    "FLAG_SHOCK_MIN_N": 30,
    "FLAG_CLAMP_SHARE": 0.50,
    "CAL_TESTABLE_PER_QUARTER": 90,
    "CAL_DAYS": 92,
    "Z0_GROSS_PF_EDGE": 1.05,
}

REALISM_NINE = (
    "gap_fills", "exit_slippage", "intrabar_path", "maker_trade_through", "maker_fill_bar",
    "single_pending", "funding", "liquidation", "daily_latch",
)
SILENT_KEYS = ("stop_band_low", "stop_band_high", "price_distance_zero", "qty_zero", "no_next_candle")
RAW_EXTRA_KEYS = ("capacity", "daily_latch", "maker_missed", "min_rr_rejected")
SELF_RELS = ("scripts/e18/analyze_e18.py", "scripts/e18/run_e18.py", "scripts/e18/e18_common.py")
BLOB_PATHS = SELF_RELS + (
    "src/strategies/scalper/setups.py", "src/strategies/scalper/backtest.py",
    "src/strategies/scalper/indicators.py", "src/strategies/scalper/regime.py",
    "src/strategies/scalper/entry_gates.py", "src/strategies/scalper/funding.py",
    "src/core/config.py", "scripts/fill_klines_cache_from_vision.py", "scripts/fill_funding_cache.py",
)
LOCK_FILE_NAMES = ("PLAN.md", "variants.json", "shock_days.json", "cal_counts.json")
FETCH_LINE = "Binance'ten çekiliyor"

SENT_WIN = "Yalnız testnet ölçüm adayı; gerçek para için kanıt değil; §10 koşulları ve kullanıcı kararı gerekir."
SENT_NO = ("Bu ailede (kırılım ve geri çekilme, 15m ve 30m, iki trend tanımı) trend yönlü girişin komisyon ve "
           "kayma sonrası kenar ürettiğine dair kanıt yok.")

REPO: Path = Path(os.environ.get("E18_REPO") or C.repo_root())


# ==========================================================================
# 1. HATALAR / KUCUK YARDIMCILAR
# ==========================================================================
class E18Error(Exception):
    code = 1


class UsageError(E18Error):
    code = 2


class LockMismatch(E18Error):
    code = 3


class StageError(E18Error):
    code = 4


class IntegrityError(E18Error):
    code = 5


class WriteOnceError(E18Error):
    code = 5


class SealError(E18Error):
    code = 6


class CheckFail(E18Error):
    code = 97


def plan_dir() -> Path:
    return Path(REPO) / C.PLAN_REL


def run_dir() -> Path:
    return plan_dir() / "run"


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def jsonable(x: Any) -> Any:
    if isinstance(x, float):
        if x == INF:
            return "inf"
        if x == -INF:
            return "-inf"
        if x != x:
            return "nan"
        return x
    if isinstance(x, dict):
        return {str(k): jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, set)):
        return [jsonable(v) for v in x]
    if isinstance(x, (dt.date, dt.datetime)):
        return x.isoformat()
    return x


def dump_json(obj: Any) -> str:
    return json.dumps(jsonable(obj), ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def date_ms(d: str) -> int:
    x = dt.date.fromisoformat(d)
    return int(dt.datetime(x.year, x.month, x.day, tzinfo=dt.timezone.utc).timestamp() * 1000)


def ms_to_date(ms: int) -> dt.date:
    return dt.datetime.fromtimestamp(ms / 1000.0, tz=dt.timezone.utc).date()


def month_of_date(d: dt.date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def quarter_of_date(d: dt.date) -> str:
    return f"{d.year:04d}Q{(d.month - 1) // 3 + 1}"


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_once(path: Path, text: str) -> None:
    """TEK KEZ yazilan dosya: varsa WriteOnceError (cikis 5), hicbir sey yazilmaz."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise WriteOnceError(f"{path.name} zaten var; tek kez yazılır (ikinci yazım reddedildi)")
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    try:
        os.link(tmp, path)
    except FileExistsError:
        raise WriteOnceError(f"{path.name} zaten var; tek kez yazılır (ikinci yazım reddedildi)")
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def f4(x: Any) -> str:
    if isinstance(x, str):
        return x
    if x is None:
        return "-"
    if x == INF:
        return "inf"
    if x == -INF:
        return "-inf"
    return f"{x:.4f}"


def f6(x: Any) -> str:
    if isinstance(x, str):
        return x
    if x is None:
        return "-"
    if isinstance(x, bool):
        return str(x)
    if isinstance(x, int):
        return str(x)
    if x == INF:
        return "inf"
    if x == -INF:
        return "-inf"
    return f"{x:.6f}"


# ==========================================================================
# 2. GIT
# ==========================================================================
def git(args: Sequence[str], check: bool = True) -> bytes:
    p = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True)
    if check and p.returncode != 0:
        raise IntegrityError(f"git {' '.join(args)} başarısız: {p.stderr.decode('utf-8', 'replace').strip()[:200]}")
    return p.stdout


def git_text(args: Sequence[str]) -> str:
    return git(args).decode("utf-8", "replace").strip()


def git_show(rev: str, path: str) -> bytes:
    p = subprocess.run(["git", "-C", str(REPO), "show", f"{rev}:{path}"], capture_output=True)
    if p.returncode != 0:
        raise LockMismatch(f"{rev[:12]}:{path} okunamadı (kilit referansı bozuk/eksik)")
    return p.stdout


def git_blob_of_file(rel: str) -> str:
    p = subprocess.run(["git", "-C", str(REPO), "hash-object", "--", rel], capture_output=True)
    if p.returncode != 0:
        raise LockMismatch(f"{rel} çalışma kopyası okunamadı")
    return p.stdout.decode().strip()


# ==========================================================================
# 3. ISTATISTIK: PF, BH, bootstrap
# ==========================================================================
def pf_of(values: Iterable[float]) -> float:
    pos = neg = 0.0
    for v in values:
        if v > 0:
            pos += v
        elif v < 0:
            neg -= v
    if neg > 0:
        return pos / neg
    return INF if pos > 0 else 0.0


def bh_qvalues(p_values: Sequence[float]) -> List[float]:
    vals = []
    for x in p_values:
        try:
            v = float(x)
        except (TypeError, ValueError):
            v = 1.0
        if v != v:
            v = 1.0
        vals.append(min(1.0, max(0.0, v)))
    m = len(vals)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: (vals[i], i))
    q = [1.0] * m
    running = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        running = min(running, vals[i] * m / rank)
        q[i] = min(running, 1.0)
    return q


def seed_for(set_name: str, arm: str, block: int, hi: bool = False) -> int:
    key = f"E18|{set_name}|{arm}|b{block}" + ("|hi" if hi else "")
    return int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:8], "big")


def seed_pfci(set_name: str, arm: str) -> int:
    key = f"E18|{set_name}|{arm}|pfci"
    return int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:8], "big")


def _tstat(s1: float, s2: float, n: int) -> float:
    mean = s1 / n
    var = (s2 - s1 * s1 / n) / (n - 1)
    if var <= 1e-12 * (s2 / n) or var <= 0.0:
        return 0.0 if abs(mean) <= 1e-12 else math.copysign(INF, mean)
    return mean / math.sqrt(var / n)


def t_obs_of(x: Sequence[float]) -> float:
    n = len(x)
    if n < 2:
        return 0.0
    return _tstat(sum(x), sum(v * v for v in x), n)


def _prefix2(xs: Sequence[float]) -> Tuple[List[float], List[float]]:
    p1, p2 = [0.0], [0.0]
    a = a2 = 0.0
    for v in list(xs) + list(xs):
        a += v
        a2 += v * v
        p1.append(a)
        p2.append(a2)
    return p1, p2


def bootstrap_p(x: Sequence[float], block: int, seed: int, boot_n: Optional[int] = None) -> Tuple[float, float]:
    """Sifir-merkezli, tek yonlu, studentize dairesel gun-blok bootstrap (PLAN §7). Doner (p, t_obs).
    sd = 0 ya da islem yok -> p = 1. Blok toplamlari kumulatif toplamla (sarmal) hesaplanir."""
    boot_n = int(boot_n or THRESH["BOOT_N"])
    d = len(x)
    if d < 2:
        return 1.0, 0.0
    mean = sum(x) / d
    sd2 = sum((v - mean) ** 2 for v in x) / (d - 1)
    if sd2 <= 0.0 or all(v == x[0] for v in x):
        return 1.0, 0.0
    t_obs = t_obs_of(x)
    xc = [v - mean for v in x]
    b = max(1, min(int(block), d))
    nblocks = -(-d // b)
    p1, p2 = _prefix2(xc)
    rnd = random.Random(seed).random
    cnt = 0
    for _ in range(boot_n):
        s1 = s2 = 0.0
        remaining = d
        for _k in range(nblocks):
            st = int(rnd() * d)
            ln = b if remaining >= b else remaining
            s1 += p1[st + ln] - p1[st]
            s2 += p2[st + ln] - p2[st]
            remaining -= ln
        if _tstat(s1, s2, d) >= t_obs:
            cnt += 1
    return (1 + cnt) / (boot_n + 1), t_obs


def pf_confidence(pos: Sequence[float], neg: Sequence[float], block: int, seed: int,
                  boot_n: Optional[int] = None) -> Tuple[float, float]:
    """PF %90 GA: ayni dairesel blok yeniden orneklemesi, gunluk (pozitif toplam, negatif toplam) ciftleri.
    Yalniz bilgi. Kayip yok -> inf."""
    boot_n = int(boot_n or THRESH["BOOT_N"])
    d = len(pos)
    if d == 0:
        return 0.0, 0.0
    b = max(1, min(int(block), d))
    nblocks = -(-d // b)
    pp, _ = _prefix2(pos)
    pn, _ = _prefix2(neg)
    rnd = random.Random(seed).random
    vals = []
    for _ in range(boot_n):
        sp = sn = 0.0
        remaining = d
        for _k in range(nblocks):
            st = int(rnd() * d)
            ln = b if remaining >= b else remaining
            sp += pp[st + ln] - pp[st]
            sn += pn[st + ln] - pn[st]
            remaining -= ln
        if sn < 0:
            vals.append(sp / -sn)
        else:
            vals.append(INF if sp > 0 else 0.0)
    vals.sort()
    a = (1.0 - THRESH["PF_CI_LEVEL"]) / 2.0
    return vals[int(a * boot_n)], vals[min(boot_n - 1, int((1.0 - a) * boot_n))]


def refine_p(p: float, decide: Callable[[float], Any], recompute: Callable[[], float],
             boot_n: Optional[int] = None) -> Tuple[float, bool]:
    """Monte Carlo hata kurali (PLAN §7): karar p +- 3*SE araliginda ters donebiliyorsa recompute()."""
    boot_n = int(boot_n or THRESH["BOOT_N"])
    se = math.sqrt(max(0.0, p * (1.0 - p) / boot_n))
    lo = max(0.0, p - THRESH["MC_SE_MULT"] * se)
    hi = min(1.0, p + THRESH["MC_SE_MULT"] * se)
    if decide(lo) != decide(hi):
        return recompute(), True
    return p, False


# ==========================================================================
# 4. ISLEM / KOSU MODELI
# ==========================================================================
@dataclass(frozen=True)
class Tr:
    symbol: str
    direction: str
    entry_ms: int
    exit_ms: int
    r: float
    pnl: float
    lev: int
    reason: str
    month: str
    quarter: str
    entry_day: dt.date
    exit_day: dt.date
    risk: float = 1.0


@dataclass
class Run:
    label: str
    set: str
    arm: str
    overlays: List[str]
    S: float
    start: dt.date
    days: int
    trades: List[Tr]
    missed: Dict[str, int]
    dd_usd: float
    sha256: str = ""

    @property
    def n(self) -> int:
        return len(self.trades)


def make_tr(symbol: str, direction: str, entry_ms: int, exit_ms: int, pnl: float, margin: float, S: float,
            lev: int, reason: str) -> Tr:
    risk = margin * S / 100.0
    if not risk > 0:
        raise IntegrityError(f"{symbol}: işlem riski <= 0")
    ed = ms_to_date(entry_ms)
    return Tr(symbol, direction, entry_ms, exit_ms, pnl / risk, pnl, lev, reason, month_of_date(ed),
              quarter_of_date(ed), ed, ms_to_date(exit_ms), risk)


def trades_from_report(doc: Dict[str, Any], S: float, label: str) -> List[Tr]:
    out: List[Tr] = []
    for i, t in enumerate(doc["trades"]):
        try:
            out.append(make_tr(str(t["symbol"]), str(t["direction"]), int(t["entry_time"]), int(t["exit_time"]),
                               float(t["pnl"]), float(t["margin_usdt"]), S, int(t["leverage"]),
                               str(t["exit_reason"])))
        except KeyError as exc:
            raise IntegrityError(f"{label}: işlem {i} alanı eksik: {exc}")
    return out


def daily_vector(trades: Sequence[Tr], start: dt.date, days: int,
                 value: Callable[[Tr], float] = lambda t: t.r) -> List[float]:
    """Gunluk net-R, CIKIS gunune gore (UTC); islemsiz gun 0; uzunluk D = set gun sayisi."""
    vec = [0.0] * days
    for t in trades:
        k = (t.exit_day - start).days
        if not 0 <= k < days:
            raise IntegrityError(f"{t.symbol}: çıkış günü {t.exit_day} set penceresinin dışında")
        vec[k] += value(t)
    return vec


def sum_r(trades: Iterable[Tr]) -> float:
    return sum(t.r for t in trades)


def pf_r(trades: Iterable[Tr]) -> float:
    return pf_of(t.r for t in trades)


def group_sums(trades: Iterable[Tr], key: Callable[[Tr], str]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for t in trades:
        out[key(t)] = out.get(key(t), 0.0) + t.r
    return out


def argmax_key(d: Dict[str, float]) -> Optional[str]:
    if not d:
        return None
    return sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def top_trades(trades: Sequence[Tr], k: int) -> List[Tr]:
    return sorted(trades, key=lambda t: (-t.r, t.entry_ms, t.symbol))[:k]


def sum_excluding_top(trades: Sequence[Tr], k: int) -> float:
    ex = {id(t) for t in top_trades(trades, k)}
    return sum(t.r for t in trades if id(t) not in ex)


def silent_info(missed: Dict[str, Any], n: int) -> Tuple[float, int, int]:
    silent = sum(int(missed.get(k, 0) or 0) for k in SILENT_KEYS)
    raw = n + sum(int(missed.get(k, 0) or 0) for k in RAW_EXTRA_KEYS) + silent
    return (silent / raw if raw > 0 else 0.0), silent, raw


def calendar_quarters(start: str, end: str) -> Tuple[List[str], List[str]]:
    """[start, end) ile kesisen takvim ceyrekleri: (tam icinde olanlar, kismi olanlar)."""
    s, e = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    full: List[str] = []
    part: List[str] = []
    y, q = s.year, (s.month - 1) // 3 + 1
    while True:
        q_start = dt.date(y, 3 * (q - 1) + 1, 1)
        q_end = dt.date(y + (1 if q == 4 else 0), 1 if q == 4 else 3 * q + 1, 1)
        if q_start >= e:
            break
        name = f"{y:04d}Q{q}"
        if q_start >= s and q_end <= e:
            full.append(name)
        elif q_end > s:
            part.append(name)
        q += 1
        if q == 5:
            q, y = 1, y + 1
    return full, part


def calendar_months(start: str, end: str) -> Tuple[List[str], List[str]]:
    s, e = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    full: List[str] = []
    part: List[str] = []
    y, m = s.year, s.month
    while True:
        m_start = dt.date(y, m, 1)
        m_end = dt.date(y + (1 if m == 12 else 0), 1 if m == 12 else m + 1, 1)
        if m_start >= e:
            break
        name = f"{y:04d}-{m:02d}"
        if m_start >= s and m_end <= e:
            full.append(name)
        elif m_end > s:
            part.append(name)
        m += 1
        if m == 13:
            m, y = 1, y + 1
    return full, part


# ==========================================================================
# 5. KAPI SATIRLARI
# ==========================================================================
def is_borderline(value: Any, threshold: Any) -> bool:
    if isinstance(value, bool) or isinstance(threshold, bool):
        return False
    if not isinstance(value, (int, float)) or not isinstance(threshold, (int, float)):
        return False
    if value in (INF, -INF) or value != value:
        return False
    return abs(value - threshold) <= THRESH["BORDERLINE_ABS"]


def cmp_ok(value: Any, op: str, threshold: Any) -> bool:
    if value is None:
        return False
    if op == ">=":
        return value >= threshold
    if op == ">":
        return value > threshold
    if op == "<=":
        return value <= threshold
    if op == "<":
        return value < threshold
    raise ValueError(op)


def grow(gate: str, name: str, value: Any, op: str, threshold: Any, note: str = "") -> Dict[str, Any]:
    ok = cmp_ok(value, op, threshold)
    return {"gate": gate, "name": name, "value": value, "op": op, "threshold": threshold, "pass": bool(ok),
            "borderline": is_borderline(value, threshold), "note": note}


def rows_pass(rows: Sequence[Dict[str, Any]]) -> bool:
    return all(r["pass"] for r in rows)


def fmt_cell(r: Dict[str, Any]) -> str:
    mark = " SINIRDA" if r.get("borderline") else ""
    return f"{'PASS' if r['pass'] else 'FAIL'} {f6(r['value'])} {r['op']} {f6(r['threshold'])}{mark}"


# --- ortak metrikler ---------------------------------------------------------
def basic_metrics(run: Run, labels: Dict[str, str]) -> Dict[str, Any]:
    tr = run.trades
    n = len(tr)
    rs = [t.r for t in tr]
    risks = [t.risk for t in tr]
    risk_med = statistics.median(risks) if risks else 0.0
    ssum = sum(rs)
    wins = sum(1 for t in tr if t.pnl > 0)
    dd_r = (run.dd_usd / risk_med) if risk_med > 0 else 0.0
    ann = ssum * 365.0 / run.days if run.days else 0.0
    if dd_r > 0:
        calmar = ann / dd_r
    else:
        calmar = INF if ann > 0 else 0.0
    silent_ratio, silent, raw = silent_info(run.missed, n)
    start_s = run.start.isoformat()
    end_s = (run.start + dt.timedelta(days=run.days)).isoformat()
    fq, pq = calendar_quarters(start_s, end_s)
    fm, pm = calendar_months(start_s, end_s)

    def win(sel: Sequence[Tr]) -> Dict[str, Any]:
        r_ = [t.r for t in sel]
        return {"n": len(sel), "sum_r": sum(r_), "pf": pf_of(r_)}

    return {
        "n": n,
        "n_long": sum(1 for t in tr if t.direction == "LONG"),
        "n_short": sum(1 for t in tr if t.direction == "SHORT"),
        "wr_pct": (100.0 * wins / n) if n else 0.0,
        "pf": pf_of(rs),
        "sum_r": ssum,
        "mean_r": (ssum / n) if n else 0.0,
        "dd_usd": run.dd_usd,
        "risk_median": risk_med,
        "dd_r": dd_r,
        "calmar_r": calmar,
        "eod": sum(1 for t in tr if t.reason == "EOD"),
        "silent_ratio": silent_ratio,
        "silent_drops": silent,
        "raw_signals": raw,
        "lev20_share": (sum(1 for t in tr if t.lev == 20) / n) if n else 0.0,
        "lev3_share": (sum(1 for t in tr if t.lev == 3) / n) if n else 0.0,
        "missed": dict(run.missed),
        "windows_full": {q: win([t for t in tr if t.quarter == q]) for q in fq},
        "windows_partial": {q: win([t for t in tr if t.quarter == q]) for q in pq},
        "full_months": fm,
        "partial_months": pm,
    }


def stat_metrics(run: Run, boot_n: Optional[int] = None) -> Dict[str, Any]:
    """t_obs, p(b=7), p(b=28), PF %90 GA (ham; MC-hata yeniden hesabi karar katmaninda)."""
    x = daily_vector(run.trades, run.start, run.days)
    n7 = boot_n or THRESH["BOOT_N"]
    p7, t_obs = bootstrap_p(x, THRESH["BLOCK_PRIMARY"], seed_for(run.set, run.arm, THRESH["BLOCK_PRIMARY"]), n7)
    p28, _ = bootstrap_p(x, THRESH["BLOCK_SECONDARY"], seed_for(run.set, run.arm, THRESH["BLOCK_SECONDARY"]), n7)
    pos = daily_vector([t for t in run.trades if t.r > 0], run.start, run.days)
    neg = daily_vector([t for t in run.trades if t.r < 0], run.start, run.days)
    lo, hi = pf_confidence(pos, neg, THRESH["BLOCK_PRIMARY"], seed_pfci(run.set, run.arm), n7)
    return {"t_obs": t_obs, "p7": p7, "p28": p28, "pf_ci": [lo, hi]}


def p_recompute(run: Run, block: int) -> float:
    x = daily_vector(run.trades, run.start, run.days)
    return bootstrap_p(x, block, seed_for(run.set, run.arm, block, hi=True), THRESH["BOOT_N_HI"])[0]


# --- DEV kapilari ------------------------------------------------------------
def gate_g0(checked: bool, m: Dict[str, Any], tag: str = "G0") -> List[Dict[str, Any]]:
    chk = grow(tag, "check-run geçti (ledger)", 1 if checked else 0, ">=", 1)
    chk["borderline"] = False
    return [
        chk,
        grow(tag, "sessiz düşüş / ham sinyal", m["silent_ratio"], "<=", THRESH["G0_SILENT_RATIO"]),
        grow(tag, "EOD işlem sayısı", m["eod"], "<=", THRESH["G0_EOD_MAX"]),
    ]


def gate_min_window_n(gid: str, m: Dict[str, Any], thr: int) -> List[Dict[str, Any]]:
    ns = {q: w["n"] for q, w in m["windows_full"].items()}
    val = min(ns.values()) if ns else 0
    return [grow(gid, "pencere başına en az N", val, ">=", thr, note=json.dumps(ns, sort_keys=True))]


def gate_g2(m: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [grow("G2", "havuz PF", m["pf"], ">=", THRESH["G2_PF"])]


def gate_g3(q: float) -> List[Dict[str, Any]]:
    return [grow("G3", "BH q", q, "<=", THRESH["ALPHA_BH"])]


def gate_pos_windows(gid: str, m: Dict[str, Any], thr: int) -> Dict[str, Any]:
    pos = sum(1 for w in m["windows_full"].values() if w["sum_r"] > 0)
    return grow(gid, "ΣR>0 olan tam pencere", pos, ">=", thr, note=f"toplam {len(m['windows_full'])}")


def gate_g4(m: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [gate_pos_windows("G4", m, THRESH["G4_MIN_POS_WINDOWS"])]


def pf_excluding_month(trades: Sequence[Tr], month: Optional[str]) -> float:
    return pf_r(t for t in trades if t.month != month)


def pf_excluding_symbol(trades: Sequence[Tr], sym: Optional[str]) -> float:
    return pf_r(t for t in trades if t.symbol != sym)


def best_month(trades: Sequence[Tr]) -> Optional[str]:
    return argmax_key(group_sums(trades, lambda t: t.month))


def best_symbol(trades: Sequence[Tr]) -> Optional[str]:
    return argmax_key(group_sums(trades, lambda t: t.symbol))


def gate_g5(run: Run) -> List[Dict[str, Any]]:
    bm = best_month(run.trades)
    return [
        grow("G5", "PF (en iyi ay hariç)", pf_excluding_month(run.trades, bm), ">=", THRESH["G5_PF_EX_MONTH"],
             note=f"en iyi ay {bm}"),
        grow("G5", "ΣR (en iyi 5 işlem hariç)", sum_excluding_top(run.trades, THRESH["G5_TOP_K"]), ">", 0.0),
    ]


def gate_stress(gid: str, stress_sums: Dict[str, Optional[float]]) -> List[Dict[str, Any]]:
    return [grow(gid, f"{ov} ΣR", stress_sums.get(ov), ">", 0.0,
                 note="" if stress_sums.get(ov) is not None else "koşu eksik") for ov in ("ROB_FEE2", "ROB_DLY1")]


# --- VAL kapilari ------------------------------------------------------------
def gate_g8(m: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [grow("G8", "havuz PF", m["pf"], ">=", THRESH["G8_PF"])]


def gate_g9(p7: float, p28: float, k: int) -> List[Dict[str, Any]]:
    return [grow("G9", "p(b=7)", p7, "<=", THRESH["ALPHA_VAL_P7"] / k, note=f"k={k}"),
            grow("G9", "p(b=28)", p28, "<=", THRESH["ALPHA_VAL_P28"] / k, note=f"k={k}")]


def gate_g10(run: Run, m: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = [gate_pos_windows("G10", m, THRESH["G10_MIN_POS_WINDOWS"])]
    months = list(m["full_months"])
    half = len(months) // 2
    for tag, ms in (("ilk yarı", months[:half]), ("son yarı", months[half:])):
        sel = [t for t in run.trades if t.month in set(ms)]
        rows.append(grow("G10", f"{tag} N", len(sel), ">=", THRESH["G10_HALF_MIN_N"], note=f"{len(ms)} ay"))
        rows.append(grow("G10", f"{tag} PF", pf_r(sel), ">=", THRESH["G10_HALF_PF"]))
    return rows


def gate_g11(run: Run) -> List[Dict[str, Any]]:
    bm = best_month(run.trades)
    bs = best_symbol(run.trades)
    syms = sorted({t.symbol for t in run.trades})
    loo = {s: pf_excluding_symbol(run.trades, s) for s in syms}
    if loo:
        worst = min(loo, key=lambda s: (loo[s], s))
        worst_v = loo[worst]
    else:
        worst, worst_v = "-", 0.0
    return [
        grow("G11", "PF (en iyi ay hariç)", pf_excluding_month(run.trades, bm), ">=", THRESH["G11_PF_EX_MONTH"],
             note=f"en iyi ay {bm}"),
        grow("G11", "PF (en iyi sembol hariç)", pf_excluding_symbol(run.trades, bs), ">=",
             THRESH["G11_PF_EX_SYMBOL"], note=f"en iyi sembol {bs}"),
        grow("G11", "bir-çıkar PF (en kötü)", worst_v, ">=", THRESH["G11_PF_LOO"], note=f"sembol {worst}"),
    ]


def gate_g13(down_trades: Sequence[Tr]) -> List[Dict[str, Any]]:
    n = len(down_trades)
    return [grow("G13", "DOWN ayları havuz N", n, ">=", THRESH["G13_MIN_N"],
                 note="n<100 -> TEST EDİLEMEDİ = başarısız"),
            grow("G13", "DOWN ayları havuz PF", pf_r(down_trades) if n else 0.0, ">=", THRESH["G13_PF"])]


def gate_g14(m: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [grow("G14", "Calmar_R", m["calmar_r"], ">=", THRESH["G14_CALMAR"])]


def g15(m: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    n = m["n"]
    info = {"n": n, "pf": m["pf"], "sum_r": m["sum_r"], "dd_r": m["dd_r"]}
    if n < THRESH["G15_MIN_N"]:
        return "SONUÇSUZ", info
    if m["pf"] < THRESH["G15_VETO_PF"] or m["dd_r"] > THRESH["G15_VETO_DD_R"]:
        return "VETO", info
    if m["pf"] >= THRESH["G15_OK_PF"] and m["sum_r"] > 0:
        return "TUTARLI", info
    return "BELİRSİZ", info


def flags_for(m: Dict[str, Any], run: Run, shock: Dict[str, str]) -> Dict[str, bool]:
    one = False
    for side in ("LONG", "SHORT"):
        sel = [t for t in run.trades if t.direction == side]
        if len(sel) >= THRESH["FLAG_ONE_SIDED_MIN_N"] and pf_r(sel) < 1.0:
            one = True
    sh = [t for t in run.trades if t.entry_day.isoformat() in shock]
    shock_weak = len(sh) >= THRESH["FLAG_SHOCK_MIN_N"] and sum_r(sh) < 0
    return {"TEK-YÖNLÜ": one, "ŞOK-ZAYIF": shock_weak, "KELEPÇE": m["lev20_share"] > THRESH["FLAG_CLAMP_SHARE"]}


# ==========================================================================
# 6. BAGLAM (Ctx), KILIT, LEDGER, KOSU YUKLEME
# ==========================================================================
@dataclass
class Ctx:
    root: Path
    paths: C.Paths
    variants: Dict[str, Any]
    labels: Dict[str, str]
    lock: Dict[str, Any] = field(default_factory=dict)
    L: str = ""
    H: str = ""
    lock_sha256: str = ""
    _ledger: Optional[Dict[str, Dict[str, Any]]] = None

    @property
    def ledger(self) -> Dict[str, Dict[str, Any]]:
        if self._ledger is None:
            self._ledger = {r["label"]: r for r in self.paths.ledger_rows()}
        return self._ledger


def load_labels() -> Dict[str, str]:
    p = Path(REPO) / C.E17_LABELS_REL
    doc = json.loads(p.read_text(encoding="utf-8"))
    return {k: v["label"] for k, v in doc.items() if re.fullmatch(r"\d{4}-\d{2}", k) and isinstance(v, dict)}


def make_ctx(root: Path, lock: Optional[Dict[str, Any]] = None, l_commit: str = "", h: str = "",
             lock_sha: str = "") -> Ctx:
    variants = C.load_variants(plan_dir() / "variants.json")
    return Ctx(root=Path(root), paths=C.Paths(Path(root)), variants=variants, labels=load_labels(),
               lock=lock or {}, L=l_commit, H=h, lock_sha256=lock_sha)


def load_shock() -> Dict[str, str]:
    p = plan_dir() / "shock_days.json"
    if not p.is_file():
        return {}
    doc = json.loads(p.read_text(encoding="utf-8"))
    return {k: v["dir"] for k, v in doc["days"].items()}


def verify_lock(root: Path) -> Ctx:
    """Kilit dogrulamasi (PLAN §9.1). Her karar alt komutu ilk is olarak cagirir. Uyusmazlik -> LockMismatch (3)."""
    ref_p = Path(root) / "LOCK_REF.json"
    if not ref_p.is_file():
        raise LockMismatch(f"{ref_p} yok: kilit kaydı bulunmuyor")
    try:
        ref = json.loads(ref_p.read_text(encoding="utf-8"))
        l_commit = ref["L"]
        want = ref["lock_sha256"]
    except (ValueError, KeyError) as exc:
        raise LockMismatch(f"LOCK_REF.json bozuk: {exc}")
    raw = git_show(l_commit, f"{C.PLAN_REL}/LOCK.json")
    if sha256_bytes(raw) != want:
        raise LockMismatch("L'deki LOCK.json sha256'sı LOCK_REF.json ile eşleşmiyor")
    wl = plan_dir() / "LOCK.json"
    if not wl.is_file() or C.sha256_file(wl) != want:
        raise LockMismatch("çalışma kopyasındaki LOCK.json, L'deki blob'dan farklı/yok")
    try:
        lock = json.loads(raw.decode("utf-8"))
        h = lock["H"]
        files = lock["files"]
        blobs = lock["blobs_at_H"]
        lab = lock["labels_e17"]
    except (ValueError, KeyError) as exc:
        raise LockMismatch(f"LOCK.json bozuk: {exc}")
    if ref.get("H") not in (None, h):
        raise LockMismatch("LOCK_REF.H ile LOCK.json.H farklı")
    for name, w in files.items():
        wp = plan_dir() / name
        if not wp.is_file() or C.sha256_file(wp) != w:
            raise LockMismatch(f"çalışma kopyası kilitten farklı: {name}")
    lp = Path(REPO) / lab["path"]
    if not lp.is_file() or C.sha256_file(lp) != lab["sha256"]:
        raise LockMismatch("E17 etiket dosyası kilitten farklı")
    for rel in SELF_RELS:
        if rel not in blobs:
            raise LockMismatch(f"blobs_at_H içinde {rel} yok")
        if git_blob_of_file(rel) != blobs[rel]:
            raise LockMismatch(f"çalışma kopyası H blob'undan farklı: {rel}")
    variants = C.load_variants(plan_dir() / "variants.json")
    if lock.get("snapshot_sha256") != (variants.get("snapshot") or {}).get("sha256"):
        raise LockMismatch("snapshot sha256 kilitle eşleşmiyor")
    # Koşular kilitli H commit'inin klonunda yapılmış olmalı (PLAN §5 "git_sha == H", §9.1): check-run
    # raporu H.txt'ye bağlar, burada H.txt ve her ledger satırı kilitteki H'ye bağlanır.
    h_txt = Path(root) / "H.txt"
    if not h_txt.is_file() or h_txt.read_text(encoding="utf-8").strip() != h:
        raise LockMismatch("H.txt (koşuların yapıldığı klon commit'i) kilitli H'den farklı ya da yok")
    for row in C.Paths(Path(root)).ledger_rows():
        if row.get("H") != h:
            raise LockMismatch(f"ledger satırı kilitli H dışında koşulmuş: {row.get('label')}")
    return make_ctx(root, lock, l_commit, h, want)


def load_run(ctx: Ctx, label: str, require: bool = True) -> Optional[Run]:
    row = ctx.ledger.get(label)
    rf = ctx.paths.report(label)
    if row is None:
        if require:
            raise StageError(f"ledger'da yok (check-run'dan geçmemiş): {label}")
        return None
    if not rf.is_file():
        raise IntegrityError(f"ledger etiketi {label} için rapor dosyası yok")
    raw = rf.read_bytes()
    sha = sha256_bytes(raw)
    # Koşucu (run_e18.py) özeti "report_sha256" anahtarıyla yazar; eski "sha256" adı da kabul edilir.
    recorded = row.get("report_sha256") or row.get("sha256")
    if not recorded:
        raise IntegrityError(f"{label}: ledger kaydında rapor özeti yok")
    if recorded != sha:
        raise IntegrityError(f"{label}: rapor sha256'sı ledger kaydıyla eşleşmiyor")
    doc = json.loads(raw)
    set_name, arm, ovs = C.parse_label(label)
    S = C.resolve_S(ctx.variants, arm, ovs)
    start, end = C.set_window(ctx.variants, set_name)
    days = (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days
    trades = trades_from_report(doc, S, label)
    missed = {k: int(v) for k, v in (doc.get("missed_signals") or {}).items()}
    dd = float((doc.get("overall") or {}).get("bar_max_drawdown") or 0.0)
    return Run(label, set_name, arm, ovs, S, dt.date.fromisoformat(start), days, trades, missed, dd, sha)


def stress_sums(ctx: Ctx, set_name: str, arm: str) -> Dict[str, Optional[float]]:
    out: Dict[str, Optional[float]] = {}
    for ov in ("ROB_FEE2", "ROB_DLY1"):
        run = load_run(ctx, C.make_label(set_name, arm, [ov]), require=False)
        out[ov] = sum_r(run.trades) if run is not None else None
    return out


def arm_info(variants: Dict[str, Any], arm: str) -> Tuple[str, str]:
    a = C.get_arm(variants, arm)
    return a["trigger"], a["entry_tf"]


# ==========================================================================
# 7. DEV: ekran, karar
# ==========================================================================
def dev_screen_compute(ctx: Ctx) -> Dict[str, Any]:
    arms = C.arm_ids(ctx.variants)
    m_family = len(arms)
    runs: Dict[str, Run] = {}
    for arm in arms:
        runs[arm] = load_run(ctx, C.make_label("DEV", arm))  # type: ignore[assignment]
    info: Dict[str, Dict[str, Any]] = {}
    for arm in arms:
        run = runs[arm]
        m = basic_metrics(run, ctx.labels)
        st = stat_metrics(run)
        g0 = gate_g0(True, m)
        g1 = gate_min_window_n("G1", m, THRESH["G1_MIN_N_WINDOW"])
        eligible = rows_pass(g0) and rows_pass(g1)
        info[arm] = {"metrics": m, "stat": st, "G0": g0, "G1": g1, "eligible": eligible,
                     "p7_raw": st["p7"], "refined": False}

    def p_used(arm: str, override: Dict[str, float]) -> float:
        if not info[arm]["eligible"]:
            return 1.0
        return override.get(arm, info[arm]["stat"]["p7"])

    override: Dict[str, float] = {}
    refined_once: set = set()
    for _ in range(len(arms) + 1):
        changed = False
        for i, arm in enumerate(arms):
            if arm in refined_once or not info[arm]["eligible"]:
                continue

            def decide(x: float, i=i, arm=arm) -> bool:
                ps = [p_used(a, override) for a in arms]
                ps[i] = x
                return bh_qvalues(ps)[i] <= THRESH["ALPHA_BH"]

            p0 = p_used(arm, override)
            newp, did = refine_p(p0, decide, lambda arm=arm: p_recompute(runs[arm], THRESH["BLOCK_PRIMARY"]))
            if did:
                override[arm] = newp
                info[arm]["refined"] = True
                refined_once.add(arm)
                changed = True
        if not changed:
            break
    ps = [p_used(a, override) for a in arms]
    qs = bh_qvalues(ps)
    out_arms: Dict[str, Any] = {}
    need: List[str] = []
    for i, arm in enumerate(arms):
        d = info[arm]
        run = runs[arm]
        gates = {"G0": d["G0"], "G1": d["G1"], "G2": gate_g2(d["metrics"]), "G3": gate_g3(qs[i]),
                 "G4": gate_g4(d["metrics"]), "G5": gate_g5(run)}
        ok = all(rows_pass(v) for v in gates.values())
        if ok:
            need.append(arm)
        out_arms[arm] = {
            "metrics": d["metrics"], "t_obs": d["stat"]["t_obs"], "p7_raw": d["p7_raw"], "p7_used": ps[i],
            "p7_refined": d["refined"], "p28": d["stat"]["p28"], "q": qs[i], "pf_ci": d["stat"]["pf_ci"],
            "eligible_for_bh": d["eligible"], "gates": gates, "pass_g0_g5": ok,
            "trigger": arm_info(ctx.variants, arm)[0], "entry_tf": arm_info(ctx.variants, arm)[1],
            "report_sha256": run.sha256,
        }
    return {"schema": "e18-devscreen/1", "lock_sha256": ctx.lock_sha256, "m": m_family, "arms": out_arms,
            "need_stress": need}


def cmd_dev_screen(root: Path) -> int:
    ctx = verify_lock(root)
    doc = dev_screen_compute(ctx)
    atomic_write(run_dir() / "DEV_SCREEN.json", dump_json(doc))
    print(" ".join(doc["need_stress"]))
    return 0


def select_advancers(passers: List[Tuple[str, float]], variants: Dict[str, Any]) -> List[str]:
    """t_obs'a gore sirala; ayni (tetikleyici, giris TF) ciftinden en cok biri; toplam en cok MAX_ADVANCE."""
    ordered = sorted(passers, key=lambda kv: (-kv[1], kv[0]))
    seen: set = set()
    out: List[str] = []
    for arm, _t in ordered:
        key = arm_info(variants, arm)
        if key in seen:
            continue
        seen.add(key)
        out.append(arm)
        if len(out) >= THRESH["MAX_ADVANCE"]:
            break
    return out


def cmd_dev_decide(root: Path) -> int:
    target = run_dir() / "DEV_DECISION.json"
    if target.exists():
        raise WriteOnceError("DEV_DECISION.json zaten var; tek kez yazılır")
    ctx = verify_lock(root)
    sp = run_dir() / "DEV_SCREEN.json"
    if not sp.is_file():
        raise StageError("DEV_SCREEN.json yok: önce dev-screen")
    screen = json.loads(sp.read_text(encoding="utf-8"))
    if screen.get("lock_sha256") != ctx.lock_sha256:
        raise LockMismatch("DEV_SCREEN.json başka bir kilide ait")
    arms_out: Dict[str, Any] = {}
    passers: List[Tuple[str, float]] = []
    for arm in C.arm_ids(ctx.variants):
        a = screen["arms"][arm]
        entry: Dict[str, Any] = {"pass_g0_g5": a["pass_g0_g5"], "t_obs": a["t_obs"]}
        if a["pass_g0_g5"]:
            g6 = gate_stress("G6", stress_sums(ctx, "DEV", arm))
            entry["g6"] = g6
            entry["pass_g0_g6"] = rows_pass(g6)
            if entry["pass_g0_g6"]:
                t = a["t_obs"]
                passers.append((arm, float(t) if not isinstance(t, str) else (INF if t == "inf" else -INF)))
        else:
            entry["pass_g0_g6"] = False
        arms_out[arm] = entry
    adv = select_advancers(passers, ctx.variants)
    for arm in arms_out:
        arms_out[arm]["selected"] = arm in adv
    label = "DEV-YOK" if not adv else "DEV-İLERLER:" + ",".join(adv)
    doc = {"schema": "e18-devdecision/1", "lock_sha256": ctx.lock_sha256, "created_utc": utc_now(),
           "label": label, "advancers": adv, "k": len(adv),
           "ranking": [{"arm": a_, "t_obs": t_} for a_, t_ in sorted(passers, key=lambda kv: (-kv[1], kv[0]))],
           "arms": arms_out}
    write_once(target, dump_json(doc))
    print(label)
    return 0


# ==========================================================================
# 8. VAL ve FINAL
# ==========================================================================
def val_arm_compute(ctx: Ctx, arm: str, k: int) -> Dict[str, Any]:
    run = load_run(ctx, C.make_label("VAL", arm))
    dev_run = load_run(ctx, C.make_label("DEV", arm))
    assert run is not None and dev_run is not None
    m = basic_metrics(run, ctx.labels)
    st = stat_metrics(run)
    p7, p28 = st["p7"], st["p28"]
    b7, b28 = THRESH["BLOCK_PRIMARY"], THRESH["BLOCK_SECONDARY"]
    p7, r7 = refine_p(p7, lambda x: x <= THRESH["ALPHA_VAL_P7"] / k, lambda: p_recompute(run, b7))
    p28, r28 = refine_p(p28, lambda x: x <= THRESH["ALPHA_VAL_P28"] / k, lambda: p_recompute(run, b28))
    down = [t for t in dev_run.trades + run.trades if ctx.labels.get(t.month) == "DOWN"]
    gates = {
        "G0": gate_g0(True, m),
        "G7": gate_min_window_n("G7", m, THRESH["G7_MIN_N_WINDOW"]),
        "G8": gate_g8(m),
        "G9": gate_g9(p7, p28, k),
        "G10": gate_g10(run, m),
        "G11": gate_g11(run),
        "G12": gate_stress("G12", stress_sums(ctx, "VAL", arm)),
        "G13": gate_g13(down),
        "G14": gate_g14(m),
    }
    ok = all(rows_pass(v) for v in gates.values())
    return {"metrics": m, "t_obs": st["t_obs"], "p7": p7, "p7_refined": r7, "p28": p28, "p28_refined": r28,
            "pf_ci": st["pf_ci"], "gates": gates, "pass": ok, "report_sha256": run.sha256}


def cmd_val_decide(root: Path) -> int:
    target = run_dir() / "VAL_DECISION.json"
    if target.exists():
        raise WriteOnceError("VAL_DECISION.json zaten var; tek kez yazılır")
    ctx = verify_lock(root)
    dp = run_dir() / "DEV_DECISION.json"
    if not dp.is_file():
        raise StageError("DEV_DECISION.json yok")
    dev = json.loads(dp.read_text(encoding="utf-8"))
    adv = list(dev["advancers"])
    if not adv:
        raise StageError("DEV'de ilerleyen kol yok: VAL yapılmaz")
    k = len(adv)
    arms_out = {arm: val_arm_compute(ctx, arm, k) for arm in adv}
    passers = []
    for arm, a in arms_out.items():
        if a["pass"]:
            t = a["t_obs"]
            passers.append((arm, float(t) if not isinstance(t, str) else (INF if t == "inf" else -INF)))
    winner = sorted(passers, key=lambda kv: (-kv[1], kv[0]))[0][0] if passers else None
    doc = {"schema": "e18-valdecision/1", "lock_sha256": ctx.lock_sha256, "created_utc": utc_now(),
           "k": k, "advancers": adv, "winner": winner, "label": "VAL-YOK" if winner is None else "VAL-GEÇTİ",
           "arms": arms_out}
    write_once(target, dump_json(doc))
    print(doc["label"] + (f":{winner}" if winner else ""))
    return 0


def final_compute(ctx: Ctx, arm: str) -> Dict[str, Any]:
    run = load_run(ctx, C.make_label("FINAL", arm))
    assert run is not None
    m = basic_metrics(run, ctx.labels)
    g, info = g15(m)
    # PLAN §8: FINAL veto yok ve N >= 20 ise KAZANAN-HARNESS; G15 "BELİRSİZ" ise etiket bunu açıkça söyler.
    final_label = {"SONUÇSUZ": "ADAY-FINAL-SONUÇSUZ", "VETO": "FINAL-VETO",
                   "BELİRSİZ": "KAZANAN-HARNESS (FINAL doğrulamadı)"}.get(g, "KAZANAN-HARNESS")
    return {"arm": arm, "g15": g, "info": info, "label": final_label, "metrics": m,
            "report_sha256": run.sha256}


def cmd_final(root: Path) -> int:
    target = run_dir() / "FINAL.json"
    if target.exists():
        raise WriteOnceError("FINAL.json zaten var; tek kez yazılır")
    ctx = verify_lock(root)
    vp = run_dir() / "VAL_DECISION.json"
    if not vp.is_file():
        raise StageError("VAL_DECISION.json yok")
    val = json.loads(vp.read_text(encoding="utf-8"))
    if not val.get("winner"):
        raise StageError("VAL'de kazanan yok: FINAL'e dokunulmaz")
    doc = final_compute(ctx, val["winner"])
    doc.update({"schema": "e18-final/1", "lock_sha256": ctx.lock_sha256, "created_utc": utc_now()})
    write_once(target, dump_json(doc))
    print(f"{doc['g15']} {doc['label']}")
    return 0


# ==========================================================================
# 9. check-run, s2-compare
# ==========================================================================
def values_equal(expected: str, actual: Any) -> bool:
    """variants.json cozulmus env degeri (metin) == rapordaki yazili Settings degeri (tip-toleransli)."""
    if isinstance(actual, bool):
        e = expected.strip().lower()
        return e in (("true", "1", "yes", "on") if actual else ("false", "0", "no", "off"))
    if isinstance(actual, (int, float)):
        try:
            return float(expected) == float(actual)
        except ValueError:
            return False
    if actual is None:
        return expected.strip() == ""
    if isinstance(actual, (list, tuple)):
        return expected.strip() == ",".join(str(x) for x in actual)
    return str(actual) == expected


def _label_kind(label: str) -> Tuple[str, str, str, List[str]]:
    """-> (tur, set, kol, kaplamalar). tur: s1 | s2 | normal. Hatali -> CheckFail."""
    if label == "S1_AYI_C":
        return "s1", "S1", "C", []
    m = re.fullmatch(r"S2_([A-Z0-9]+)_h([01])", label)
    if m:
        return "s2", "CAL", m.group(1), []
    set_name, arm, ovs = C.parse_label(label)
    if set_name not in ("CAL", "DEV", "VAL", "FINAL") or not arm:
        raise CheckFail(f"bilinmeyen etiket: {label}")
    return "normal", set_name, arm, ovs


def check_report_reasons(ctx: Ctx, label: str, doc: Dict[str, Any], log_text: Optional[str], h: str) -> List[str]:
    """Basarisizlik nedenleri (bos = gecti). Hicbir PnL/PF/islem sayisi nedene yazilmaz."""
    v = ctx.variants
    kind, set_name, arm, ovs = _label_kind(label)
    f: List[str] = []
    prov = doc.get("provenance") or {}
    cfg = prov.get("scalper_config") or {}
    if kind == "s1":
        start, end = v["smoke"]["S1"]["window"]
        strategies = v["smoke"]["S1"]["strategy"]
    else:
        start, end = C.set_window(v, set_name)
        strategies = "M"
    s_d, e_d = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    days = (e_d - s_d).days
    s_ms, e_ms = date_ms(start), date_ms(end)
    if prov.get("git_sha") != h:
        f.append("provenance.git_sha != H")
    if prov.get("git_dirty") is not False:
        f.append("provenance.git_dirty false değil")
    tw = prov.get("test_window") or {}
    if tw.get("start_ms") != s_ms or tw.get("end_ms") != e_ms:
        f.append("test penceresi kilitli pencereyle eşleşmiyor")
    params = doc.get("params") or {}
    ps = params.get("strategies")
    ps_s = ",".join(ps) if isinstance(ps, (list, tuple)) else ps
    if ps_s != strategies:
        f.append("params.strategies beklenenden farklı")
    if ",".join(params.get("symbols") or []) != v["symbols"]:
        f.append("params.symbols sırası/kümesi sabit değil")
    entry_tf: Optional[str] = None
    if kind != "s1":
        triple = C.triple_of(v, arm)
        got = [cfg.get("scalper_tf_entry"), cfg.get("scalper_tf_context"), cfg.get("scalper_tf_regime")]
        if got != triple:
            f.append("TF üçlüsü kilitli üçlüyle eşleşmiyor")
        entry_tf = triple[0]
        for k_, want in sorted(C.resolve_env(v, arm, ovs).items()):
            key = k_.lower()
            if key not in cfg:
                f.append(f"scalper_config'te {key} yok")
            elif not values_equal(want, cfg[key]):
                f.append(f"scalper_config.{key} beklenenle eşleşmiyor")
        s_key = "scalper_fixed_stop_roi_pct"
        if s_key in cfg and not values_equal(str(C.resolve_S(v, arm, ovs)), cfg[s_key]):
            f.append(f"scalper_config.{s_key} R birimiyle eşleşmiyor")
        cli = C.resolve_cli(v, ovs)
        stress = 2.0 if "--fee-stress" in cli else 1.0
        delay = int(cli[cli.index("--entry-delay-candles") + 1]) if "--entry-delay-candles" in cli else 0
    else:
        stress, delay = 1.0, 0
    cm = prov.get("cost_model") or {}
    try:
        if float(cm.get("stress_multiplier", -1)) != stress:
            f.append("cost_model.stress_multiplier beklenenden farklı")
        if int(cm.get("entry_delay_candles", -1)) != delay:
            f.append("cost_model.entry_delay_candles beklenenden farklı")
    except (TypeError, ValueError):
        f.append("cost_model bozuk")
    rl = prov.get("realism") or {}
    if sorted(rl.get("enabled") or []) != sorted(REALISM_NINE) or (rl.get("disabled") or []):
        f.append("realism: dokuz anahtarın hepsi açık değil")
    if not isinstance(doc.get("missed_signals"), dict):
        f.append("missed_signals yok")
    ov = doc.get("overall") or {}
    trades = doc.get("trades")
    if not isinstance(trades, list):
        f.append("trades listesi yok")
        return f
    try:
        dd = float(ov.get("bar_max_drawdown"))
        if not math.isfinite(dd):
            raise ValueError
    except (TypeError, ValueError):
        f.append("overall.bar_max_drawdown yok/sonlu değil")
    try:
        if int(ov.get("trades", -1)) != len(trades):
            f.append("overall.trades != len(trades)")
        pnls = [float(t["pnl"]) for t in trades]
        tot = float(ov.get("total_pnl"))
        if abs(tot - sum(pnls)) > 1e-6 * max(1.0, abs(tot)):
            f.append("overall.total_pnl != Σ pnl")
        want_strat = strategies
        if any(str(t.get("strategy")) != want_strat for t in trades):
            f.append(f"işlem kurulumu {want_strat} olmayan kayıt var")
        for t in trades:
            ex = ms_to_date(int(t["exit_time"]))
            en = ms_to_date(int(t["entry_time"]))
            if not (s_d <= ex < e_d) or en < s_d:
                f.append("işlem zamanı test penceresinin dışında")
                break
    except (KeyError, TypeError, ValueError, AttributeError):
        f.append("trades[] alanları bozuk")
    if entry_tf is not None:
        dw = prov.get("data_windows") or {}
        if set(dw) != set(v["symbols"].split(",")):
            f.append("data_windows sembol kümesi eksik")
        for sym, per in sorted(dw.items()):
            w = (per or {}).get(entry_tf)
            if w is None:
                f.append(f"{sym}: data_windows'ta giriş TF yok")
            elif w.get("candles_in_test_window") != days * C.BARS_PER_DAY[entry_tf]:
                f.append(f"{sym} {entry_tf}: test penceresindeki mum sayısı beklenenden farklı (boşluk)")
    if log_text is None:
        f.append("log dosyası yok")
    elif FETCH_LINE in log_text:
        f.append("log'da 'Binance'ten çekiliyor' var (önbellek ıskası)")
    if kind == "s1":
        exp = v["smoke"]["S1"]["expect"]
        try:
            ok = int(ov.get("trades", -1)) == int(exp["trades"]) and \
                round(float(ov.get("total_pnl")), 2) == round(float(exp["total_pnl_2dp"]), 2)
        except (TypeError, ValueError):
            ok = False
        if not ok:
            f.append("S1 parite eşleşmedi")
    return f


def cmd_check_run(root: Path, label: str) -> int:
    paths = C.Paths(Path(root))
    hp = Path(root) / "H.txt"
    reasons: List[str]
    try:
        if not hp.is_file():
            raise CheckFail("H.txt yok")
        h = hp.read_text(encoding="utf-8").strip()
        rf = paths.report(label)
        if not rf.is_file():
            raise CheckFail("rapor dosyası yok")
        doc = json.loads(rf.read_text(encoding="utf-8"))
        lf = paths.log(label)
        log_text = lf.read_text(encoding="utf-8", errors="replace") if lf.is_file() else None
        ctx = make_ctx(root)
        reasons = check_report_reasons(ctx, label, doc, log_text, h)
    except CheckFail as exc:
        reasons = [str(exc)]
    except (ValueError, KeyError, TypeError, OSError) as exc:
        reasons = [f"okunamadı: {type(exc).__name__}"]
    if reasons:
        print(f"CHECK FAIL {label}: " + "; ".join(reasons))
        return CheckFail.code
    print(f"CHECK OK {label}")
    return 0


def cmd_s2_compare(root: Path) -> int:
    paths = C.Paths(Path(root))
    hs = []
    for tag in ("S2_K15R_h0", "S2_K15R_h1"):
        rf = paths.report(tag)
        if not rf.is_file():
            print(f"S2 FAIL: {tag} yok")
            return CheckFail.code
        hs.append(sha256_bytes(canon(json.loads(rf.read_text(encoding="utf-8")).get("trades")).encode("utf-8")))
    if hs[0] == hs[1]:
        print("S2 OK")
        return 0
    print("S2 FAIL")
    return CheckFail.code


# ==========================================================================
# 10. freeze-shock, cal-counts, lock, package
# ==========================================================================
def read_btc_1d(directory: Path) -> Dict[dt.date, Tuple[float, float]]:
    out: Dict[dt.date, Tuple[float, float]] = {}
    zips = sorted(directory.glob("BTCUSDT-1d-*.zip"))
    if not zips:
        raise IntegrityError(f"{directory} altında BTCUSDT-1d zip yok")
    for zp in zips:
        with zipfile.ZipFile(zp) as z:
            for name in z.namelist():
                if not name.lower().endswith(".csv"):
                    continue
                for line in z.read(name).decode("utf-8").splitlines():
                    parts = line.strip().split(",")
                    if len(parts) < 5 or not parts[0].strip().lstrip("-").isdigit():
                        continue
                    ot = int(parts[0])
                    if ot > 10 ** 14:
                        ot //= 1000
                    d = ms_to_date(ot)
                    if d in out:
                        raise IntegrityError(f"yinelenen gün: {d}")
                    out[d] = (float(parts[1]), float(parts[4]))
    return out


def build_shock(days: Dict[dt.date, Tuple[float, float]], variants: Dict[str, Any]) -> Dict[str, Any]:
    thr = THRESH["SHOCK_ABS_RET"]
    out_days: Dict[str, Any] = {}
    for d in sorted(days):
        o, c = days[d]
        ret = c / o - 1.0
        if abs(ret) >= thr:
            out_days[d.isoformat()] = {"ret_pct": ret * 100.0, "dir": "UP" if ret > 0 else "DOWN"}
    counts: Dict[str, int] = {}
    n_days: Dict[str, int] = {}
    for sn in ("DEV", "VAL"):
        s, e = (dt.date.fromisoformat(x) for x in C.set_window(variants, sn))
        n = (e - s).days
        for i in range(n):
            if s + dt.timedelta(days=i) not in days:
                raise IntegrityError(f"{sn}: {s + dt.timedelta(days=i)} günü arşivde yok")
        n_days[sn] = n
        counts[sn] = sum(1 for k in out_days if s <= dt.date.fromisoformat(k) < e)
    return {"schema": "e18-shock/1",
            "rule": "BTCUSDT 1d mumunda |kapanış/açılış - 1| >= 0.03 olan UTC günü (E20 tanımı); ileri bakış, yalnız betimleyici",
            "threshold": thr, "source": C.E17_BTC1D_REL, "days": out_days, "counts": counts, "n_days": n_days}


def cmd_freeze_shock(force: bool) -> int:
    target = plan_dir() / "shock_days.json"
    if target.exists() and not force:
        raise StageError("shock_days.json zaten var (--force ile yeniden yazılır)")
    variants = C.load_variants(plan_dir() / "variants.json")
    days = read_btc_1d(Path(REPO) / C.E17_BTC1D_REL)
    doc = build_shock(days, variants)
    atomic_write(target, json.dumps(doc, sort_keys=True, indent=2, ensure_ascii=False) + "\n")
    print(f"shock günleri: DEV {doc['counts']['DEV']}/{doc['n_days']['DEV']}  VAL {doc['counts']['VAL']}/{doc['n_days']['VAL']}")
    return 0


def cal_counts_for(doc: Dict[str, Any]) -> Dict[str, Any]:
    """YALNIZ sayim alanlari: trades[].direction/entry_time/leverage ve missed_signals. PnL alanlarina dokunulmaz."""
    trades = doc["trades"]
    n = len(trades)
    per_month: Dict[str, int] = {}
    n_long = n_short = l20 = l3 = 0
    for t in trades:
        d = t["direction"]
        n_long += 1 if d == "LONG" else 0
        n_short += 1 if d == "SHORT" else 0
        lev = int(t["leverage"])
        l20 += 1 if lev == 20 else 0
        l3 += 1 if lev == 3 else 0
        mo = month_of_date(ms_to_date(int(t["entry_time"])))
        per_month[mo] = per_month.get(mo, 0) + 1
    proj = n * 91.25 / THRESH["CAL_DAYS"]
    return {"n": n, "n_long": n_long, "n_short": n_short, "per_month": dict(sorted(per_month.items())),
            "lev20_share": (l20 / n) if n else 0.0, "lev3_share": (l3 / n) if n else 0.0,
            "missed_signals": {str(k): v for k, v in (doc.get("missed_signals") or {}).items()},
            "projected_per_quarter": proj, "testable": proj >= THRESH["CAL_TESTABLE_PER_QUARTER"]}


def cmd_cal_counts(root: Path) -> int:
    paths = C.Paths(Path(root))
    variants = C.load_variants(plan_dir() / "variants.json")
    arms: Dict[str, Any] = {}
    for arm in C.arm_ids(variants):
        rf = paths.report(C.make_label("CAL", arm))
        if not rf.is_file():
            raise StageError(f"CAL raporu yok: {rf.name}")
        arms[arm] = cal_counts_for(json.loads(rf.read_text(encoding="utf-8")))
    doc = {"schema": "e18-cal/1", "rule": "testable = N * 91.25 / 92 >= 90; yalnız sayım, PnL okunmadı",
           "arms": arms, "family_testable": [a for a in arms if arms[a]["testable"]]}
    atomic_write(plan_dir() / "cal_counts.json", json.dumps(doc, sort_keys=True, indent=2, ensure_ascii=False) + "\n")
    print("kol    N  long short  /çeyrek  test  20x    3x")
    for a, c in arms.items():
        print(f"{a:5s} {c['n']:4d} {c['n_long']:5d} {c['n_short']:5d} {c['projected_per_quarter']:8.1f} "
              f"{'EVET' if c['testable'] else 'HAYIR':5s} {c['lev20_share']:.2f} {c['lev3_share']:.2f}")
    return 0


def cmd_lock(root: Path, commit: str) -> int:
    paths = C.Paths(Path(root))
    bad = sorted(lb for lb in paths.ledger_labels() if lb.startswith(("DEV_", "VAL_", "FINAL_")))
    if bad:
        raise StageError(f"ledger'da {bad[0]} var: lock kapalı (§9.2)")
    h = git_text(["rev-parse", "--verify", f"{commit}^{{commit}}"])
    for rel in SELF_RELS:
        want = git_text(["rev-parse", f"{h}:{rel}"])
        if git_blob_of_file(rel) != want:
            raise StageError(f"çalışma kopyası H'deki blob'dan farklı: {rel}")
    pdir = plan_dir()
    files = {}
    for name in LOCK_FILE_NAMES:
        p = pdir / name
        if not p.is_file():
            raise StageError(f"kilitlenecek dosya yok: {name}")
        files[name] = C.sha256_file(p)
    lab = Path(REPO) / C.E17_LABELS_REL
    if not lab.is_file():
        raise StageError("E17 etiket dosyası yok")
    variants = C.load_variants(pdir / "variants.json")
    # PLAN §3.3: test edilebilirlik kuralını geçemeyen kol kilitten ÖNCE aileden çıkarılır; kilit,
    # variants.json ailesinin CAL sayımındaki test edilebilir kollarla aynı olduğunu doğrular.
    try:
        cal = json.loads((pdir / "cal_counts.json").read_text(encoding="utf-8"))
    except ValueError as exc:
        raise StageError(f"cal_counts.json okunamadı: {exc}")
    fam = sorted(C.arm_ids(variants))
    if sorted(cal.get("arms") or {}) != fam or sorted(cal.get("family_testable") or []) != fam:
        raise StageError("variants.json ailesi, cal_counts.json'daki test edilebilir kollarla aynı değil (PLAN §3.3)")
    doc = {
        "schema": "e18-lock/1", "locked_utc": utc_now(), "H": h, "H_tree": git_text(["rev-parse", f"{h}^{{tree}}"]),
        "files": files, "labels_e17": {"path": C.E17_LABELS_REL, "sha256": C.sha256_file(lab)},
        "blobs_at_H": {p: git_text(["rev-parse", f"{h}:{p}"]) for p in BLOB_PATHS},
        "snapshot_sha256": (variants.get("snapshot") or {}).get("sha256"),
        "python": platform.python_version(),
    }
    atomic_write(pdir / "LOCK.json", json.dumps(doc, sort_keys=True, indent=2, ensure_ascii=False) + "\n")
    print(f"LOCK.json yazıldı: H={h} sha256={C.sha256_file(pdir / 'LOCK.json')}")
    return 0


def cmd_verify_lock(root: Path) -> int:
    ctx = verify_lock(root)
    print(f"KİLİT DOĞRULANDI: L={ctx.L[:12]} H={ctx.H[:12]}")
    return 0


def cmd_package(root: Path) -> int:
    # Mühür (PLAN §5): DEV raporları DEV_DECISION.json yazılmadan paketlenmez; kilit de doğrulanır.
    if not (run_dir() / "DEV_DECISION.json").is_file():
        raise SealError("DEV_DECISION.json yok: mühürlü raporlar paketlenmez")
    verify_lock(root)
    paths = C.Paths(Path(root))
    out = run_dir() / "artifacts.tgz"
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w") as tar:
            def add(name: str, data: bytes) -> None:
                ti = tarfile.TarInfo(name)
                ti.size = len(data)
                ti.mtime = 0
                ti.mode = 0o644
                ti.uid = ti.gid = 0
                ti.uname = ti.gname = ""
                tar.addfile(ti, io.BytesIO(data))

            if paths.sealed_runs.is_dir():
                for p in sorted(paths.sealed_runs.glob("*.json")):
                    d = json.loads(p.read_text(encoding="utf-8"))
                    if isinstance(d.get("provenance"), dict):
                        d["provenance"].pop("scalper_config", None)
                    add(f"runs/{p.name}", json.dumps(d, sort_keys=True, ensure_ascii=False).encode("utf-8"))
            for extra in (paths.ledger, paths.manifest):
                if extra.is_file():
                    add(extra.name, extra.read_bytes())
    atomic_write_bytes(out, buf.getvalue())
    print(f"{out.name} yazıldı")
    return 0


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    tmp.write_bytes(data)
    os.replace(tmp, path)


# ==========================================================================
# 11. RAPOR
# ==========================================================================
def mdt(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> List[str]:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return out


def overall_label(dev: Optional[Dict[str, Any]], val: Optional[Dict[str, Any]],
                  fin: Optional[Dict[str, Any]]) -> str:
    if not dev or not dev["advancers"]:
        return "DEV-YOK"
    if val is None:
        return "ARA-RAPOR (DEV geçti, VAL koşmadı)"
    if val["winner"] is None:
        return "VAL-YOK"
    if fin is None:
        return "ARA-RAPOR (VAL geçti, FINAL koşmadı)"
    return fin["label"]


def z0_sentence(gross_pf: float, net_pf: float) -> str:
    if gross_pf >= THRESH["Z0_GROSS_PF_EDGE"] and net_pf < 1.0:
        return "ham kenar var, maliyet yiyor"
    if gross_pf < THRESH["Z0_GROSS_PF_EDGE"]:
        return "maliyetten önce de kenar yok"
    return "ham kenar var ve net PF de >= 1 (PLAN bu durum için cümle tanımlamıyor)"


def breakdown_rows(trades: Sequence[Tr], keys: Sequence[str], key: Callable[[Tr], str]) -> List[List[Any]]:
    rows = []
    for k in keys:
        sel = [t for t in trades if key(t) == k]
        r_ = [t.r for t in sel]
        wins = sum(1 for t in sel if t.pnl > 0)
        rows.append([k, len(sel), f4(100.0 * wins / len(sel)) if sel else "-", f4(pf_of(r_)), f4(sum(r_))])
    return rows


def build_report(ctx: Ctx) -> Tuple[str, List[Dict[str, Any]]]:
    rd = run_dir()
    screen = json.loads((rd / "DEV_SCREEN.json").read_text(encoding="utf-8")) if (rd / "DEV_SCREEN.json").is_file() else None
    dev = json.loads((rd / "DEV_DECISION.json").read_text(encoding="utf-8"))
    val = json.loads((rd / "VAL_DECISION.json").read_text(encoding="utf-8")) if (rd / "VAL_DECISION.json").is_file() else None
    fin = json.loads((rd / "FINAL.json").read_text(encoding="utf-8")) if (rd / "FINAL.json").is_file() else None
    shock = load_shock()
    arms = C.arm_ids(ctx.variants)
    label = overall_label(dev, val, fin)
    if label.startswith("KAZANAN-HARNESS"):
        sentence = SENT_WIN
    elif label.startswith("ARA-RAPOR"):
        sentence = "Ara rapor: zincir tamamlanmadı, hüküm verilmedi."
    else:
        sentence = SENT_NO
    out: List[str] = []
    csv_rows: List[Dict[str, Any]] = []

    def csv_add(run: Run, m: Dict[str, Any], extra: Optional[Dict[str, Any]] = None) -> None:
        row = {"label": run.label, "set": run.set, "arm": run.arm, "overlay": "+".join(run.overlays),
               "n": m["n"], "n_long": m["n_long"], "n_short": m["n_short"], "wr_pct": m["wr_pct"], "pf": m["pf"],
               "sum_r": m["sum_r"], "mean_r": m["mean_r"], "dd_r": m["dd_r"], "calmar_r": m["calmar_r"],
               "eod": m["eod"], "silent_ratio": m["silent_ratio"], "lev20_share": m["lev20_share"],
               "lev3_share": m["lev3_share"], "t_obs": "", "p7": "", "p28": "", "q": "", "pf_lo": "", "pf_hi": ""}
        row.update(extra or {})
        csv_rows.append(row)

    out.append("# E18 karar raporu")
    out.append("")
    out.append(f"## Sonuç etiketi: {label}")
    out.append("")
    out.append(sentence)
    out.append("")
    nrows = len(ctx.paths.ledger_rows())
    out += ["## Bütünlük", ""]
    out += mdt(["Alan", "Değer"], [["H", ctx.H], ["L", ctx.L], ["LOCK.json sha256", ctx.lock_sha256],
                                  ["ledger satırı", nrows],
                                  ["snapshot sha256", ctx.lock.get("snapshot_sha256", "")]])
    out.append("")

    # --- DEV koşuları
    dev_runs: Dict[str, Run] = {}
    dev_m: Dict[str, Dict[str, Any]] = {}
    for arm in arms:
        r = load_run(ctx, C.make_label("DEV", arm), require=False)
        if r is not None:
            dev_runs[arm] = r
            dev_m[arm] = basic_metrics(r, ctx.labels)
    sc_arms = (screen or {}).get("arms", {})
    out += ["## Aile tablosu (DEV, 2025)", ""]
    rows = []
    for arm in arms:
        if arm not in dev_m:
            continue
        m = dev_m[arm]
        sa = sc_arms.get(arm, {})
        ci = sa.get("pf_ci", ["-", "-"])
        rows.append([arm, m["n"], f4(m["wr_pct"]), f4(m["pf"]), f4(m["sum_r"]), f4(m["mean_r"]),
                     f4(sa.get("t_obs")), f4(sa.get("p7_used")), f4(sa.get("p28")), f4(sa.get("q")),
                     f"[{f4(ci[0])}, {f4(ci[1])}]", f4(m["dd_r"]), f4(m["calmar_r"])])
        csv_add(dev_runs[arm], m, {"t_obs": sa.get("t_obs", ""), "p7": sa.get("p7_used", ""),
                                   "p28": sa.get("p28", ""), "q": sa.get("q", ""),
                                   "pf_lo": ci[0], "pf_hi": ci[1]})
    out += mdt(["Kol", "N", "WR %", "PF", "ΣR", "ort R", "t_obs", "p(b=7)", "p(b=28)", "BH q", "PF %90 GA",
                "DD_R", "Calmar_R"], rows)
    out.append("")

    # --- kapi matrisi
    out += ["## Kapı matrisi", ""]
    matrix_cols: List[Tuple[str, Dict[str, List[Dict[str, Any]]]]] = []
    for arm in arms:
        if arm in sc_arms:
            gates = dict(sc_arms[arm]["gates"])
            d6 = dev["arms"].get(arm, {}).get("g6")
            if d6:
                gates["G6"] = d6
            matrix_cols.append((f"DEV {arm}", gates))
    if val:
        for arm, a in val["arms"].items():
            matrix_cols.append((f"VAL {arm}", a["gates"]))
    if matrix_cols:
        keys: List[Tuple[str, str]] = []
        for _n, g in matrix_cols:
            for gid in sorted(g, key=lambda s: int(s[1:])):
                for r in g[gid]:
                    k = (gid, r["name"])
                    if k not in keys:
                        keys.append(k)
        mrows = []
        for gid, name in keys:
            cells = []
            for _n, g in matrix_cols:
                hit = [r for r in g.get(gid, []) if r["name"] == name]
                cells.append(fmt_cell(hit[0]) if hit else "-")
            mrows.append([f"{gid} {name}"] + cells)
        out += mdt(["Kapı"] + [n for n, _ in matrix_cols], mrows)
    out.append("")
    out += [f"DEV kararı: {dev['label']}", ""]

    # --- tablolar (DEV taban kosulari)
    def tables_for(tag: str, runs: Dict[str, Run], ms: Dict[str, Dict[str, Any]]) -> None:
        nonlocal out
        for arm, run in runs.items():
            m = ms[arm]
            out.append(f"### {tag} {arm}")
            out.append("")
            out.append("Pencere (giriş çeyreği):")
            out.append("")
            wrows = [[q, w["n"], f4(w["sum_r"]), f4(w["pf"])] for q, w in m["windows_full"].items()]
            wrows += [[q + " (kısmi)", w["n"], f4(w["sum_r"]), f4(w["pf"])] for q, w in m["windows_partial"].items()]
            out += mdt(["Pencere", "N", "ΣR", "PF"], wrows)
            out.append("")
            out.append("LONG ve SHORT:")
            out.append("")
            out += mdt(["Yön", "N", "WR %", "PF", "ΣR"], breakdown_rows(run.trades, ["LONG", "SHORT"], lambda t: t.direction))
            out.append("")
            out.append("Şok günü ve normal gün (giriş gününe göre):")
            out.append("")
            sh = [t for t in run.trades if t.entry_day.isoformat() in shock]
            nm = [t for t in run.trades if t.entry_day.isoformat() not in shock]
            with_ = [t for t in sh if (t.direction == "LONG" and shock[t.entry_day.isoformat()] == "UP")
                     or (t.direction == "SHORT" and shock[t.entry_day.isoformat()] == "DOWN")]
            against = [t for t in sh if t not in with_]
            srows = [[n_, len(s_), f4(sum_r(s_)), f4(pf_r(s_))] for n_, s_ in
                     (("şok günü", sh), ("normal gün", nm), ("şok günü, şokla AYNI yön", with_),
                      ("şok günü, şoka KARŞI yön", against))]
            out += mdt(["Grup", "N", "ΣR", "PF"], srows)
            out.append("")
            out.append("Rejim grubu (giriş ayı etiketi):")
            out.append("")
            rrows = []
            for g in ("UP", "FLAT", "DOWN"):
                s_ = [t for t in run.trades if ctx.labels.get(t.month) == g]
                rrows.append([g, len(s_), f4(sum_r(s_)), f4(pf_r(s_))])
            out += mdt(["Rejim", "N", "ΣR", "PF"], rrows)
            out.append("")
            out.append("Sembol:")
            out.append("")
            syms = ctx.variants["symbols"].split(",")
            out += mdt(["Sembol", "N", "WR %", "PF", "ΣR"], breakdown_rows(run.trades, syms, lambda t: t.symbol))
            out.append("")
            reasons = sorted({t.reason for t in run.trades})
            out.append("Çıkış nedeni karışımı: " + (", ".join(f"{r_}={sum(1 for t in run.trades if t.reason == r_)}" for r_ in reasons) or "-"))
            out.append("")
            out.append(f"Kaldıraç payı: 20x={f4(m['lev20_share'])} 3x={f4(m['lev3_share'])}")
            out.append("")
            out.append("Kaçan sinyal sayaçları: " + (json.dumps(m["missed"], sort_keys=True) if m["missed"] else "{}")
                       + f"; sessiz düşüş / ham sinyal = {f4(m['silent_ratio'])} ({m['silent_drops']}/{m['raw_signals']})")
            out.append("")
            fl = flags_for(m, run, shock)
            out.append("Bayraklar: " + (", ".join(k for k, v in fl.items() if v) or "yok"))
            out.append("")

    out += ["## Ayrıntı tabloları", ""]
    tables_for("DEV", dev_runs, dev_m)

    # --- stres ve teshis
    out += ["## Stres koşuları", ""]
    srows = []
    for arm in arms:
        for ov in ("ROB_FEE2", "ROB_DLY1"):
            r = load_run(ctx, C.make_label("DEV", arm, [ov]), require=False)
            if r is not None:
                m = basic_metrics(r, ctx.labels)
                srows.append([f"DEV {arm}", ov, m["n"], f4(m["pf"]), f4(m["sum_r"])])
                csv_add(r, m)
        if val and arm in val["arms"]:
            for ov in ("ROB_FEE2", "ROB_DLY1"):
                r = load_run(ctx, C.make_label("VAL", arm, [ov]), require=False)
                if r is not None:
                    m = basic_metrics(r, ctx.labels)
                    srows.append([f"VAL {arm}", ov, m["n"], f4(m["pf"]), f4(m["sum_r"])])
                    csv_add(r, m)
    out += mdt(["Set kol", "Kaplama", "N", "PF", "ΣR"], srows)
    out.append("")
    out += ["## Teşhis koşuları (karar vermez)", ""]
    drows = []
    for arm in arms:
        for ov in ("DIAG_Z0", "DIAG_MAKER", "DIAG_T"):
            r = load_run(ctx, C.make_label("DEV", arm, [ov]), require=False)
            if r is not None:
                m = basic_metrics(r, ctx.labels)
                note = z0_sentence(m["pf"], dev_m[arm]["pf"]) if ov == "DIAG_Z0" and arm in dev_m else ""
                drows.append([arm, ov, m["n"], f4(m["pf"]), f4(m["sum_r"]), note])
                csv_add(r, m)
    out += mdt(["Kol", "Koşu", "N", "PF", "ΣR", "Okuma"], drows)
    out.append("")

    # --- VAL ve FINAL
    if val:
        out += ["## VAL (2023-01-01 → 2024-11-18)", "", f"Karar: {val['label']}" + (f" ({val['winner']})" if val["winner"] else ""), ""]
        vruns: Dict[str, Run] = {}
        vms: Dict[str, Dict[str, Any]] = {}
        for arm, a in val["arms"].items():
            r = load_run(ctx, C.make_label("VAL", arm))
            assert r is not None
            vruns[arm] = r
            vms[arm] = basic_metrics(r, ctx.labels)
            ci = a["pf_ci"]
            csv_add(r, vms[arm], {"t_obs": a["t_obs"], "p7": a["p7"], "p28": a["p28"], "pf_lo": ci[0], "pf_hi": ci[1]})
        out += mdt(["Kol", "N", "WR %", "PF", "ΣR", "t_obs", "p(b=7)", "p(b=28)", "PF %90 GA", "DD_R", "Calmar_R", "Geçti"],
                   [[arm, vms[arm]["n"], f4(vms[arm]["wr_pct"]), f4(vms[arm]["pf"]), f4(vms[arm]["sum_r"]),
                     f4(a["t_obs"]), f4(a["p7"]), f4(a["p28"]), f"[{f4(a['pf_ci'][0])}, {f4(a['pf_ci'][1])}]",
                     f4(vms[arm]["dd_r"]), f4(vms[arm]["calmar_r"]), "EVET" if a["pass"] else "HAYIR"]
                    for arm, a in val["arms"].items()])
        out.append("")
        tables_for("VAL", vruns, vms)
    if fin:
        out += ["## FINAL (2026-10-16 → 2026-11-20, yalnız veto)", ""]
        fr = load_run(ctx, C.make_label("FINAL", fin["arm"]))
        assert fr is not None
        fm = basic_metrics(fr, ctx.labels)
        csv_add(fr, fm)
        out += mdt(["Kol", "N", "PF", "ΣR", "DD_R", "G15"],
                   [[fin["arm"], fm["n"], f4(fm["pf"]), f4(fm["sum_r"]), f4(fm["dd_r"]), fin["g15"]]])
        out += ["", f"FINAL etiketi: {fin['label']}", ""]
    out += ["## Sınırlar", "",
            "1. Önsel olasılık düşük: bu projede trend yönlü girişe dair eldeki ölçümler olumsuz ya da çelişkili; "
            "\"kazanan yok\" çıkması muhtemeldir.",
            "2. Backtest canlıdan iyimserdir (TP1'e ulaşma %66, canlı %54). Kazanan çıksa bile yalnız "
            "\"testnet ölçüm adayı\"dır.", ""]
    return "\n".join(out) + "\n", csv_rows


CSV_FIELDS = ["label", "set", "arm", "overlay", "n", "n_long", "n_short", "wr_pct", "pf", "sum_r", "mean_r", "t_obs",
              "p7", "p28", "q", "pf_lo", "pf_hi", "dd_r", "calmar_r", "eod", "silent_ratio", "lev20_share", "lev3_share"]


def cmd_report(root: Path) -> int:
    if not (run_dir() / "DEV_DECISION.json").is_file():
        raise SealError("DEV_DECISION.json yok: mühür kapalı, report reddedildi")
    ctx = verify_lock(root)
    md, rows = build_report(ctx)
    atomic_write(run_dir() / "DECISION.md", md)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_FIELDS, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: (f6(v) if isinstance(v, float) else v) for k, v in r.items() if k in CSV_FIELDS})
    atomic_write(run_dir() / "results.csv", buf.getvalue())
    print("DECISION.md ve results.csv yazıldı")
    return 0


# ==========================================================================
# 12. CLI
# ==========================================================================
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="analyze_e18.py", description="E18 ön-kayıtlı analizci (PLAN.md)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name: str, root: bool = True) -> argparse.ArgumentParser:
        sp = sub.add_parser(name)
        if root:
            sp.add_argument("--root", default=os.environ.get("E18_ROOT"))
        return sp

    sp = add("freeze-shock", root=False)
    sp.add_argument("--force", action="store_true")
    add("cal-counts")
    sp = add("lock")
    sp.add_argument("--commit", required=True)
    add("verify-lock")
    sp = add("check-run")
    sp.add_argument("--label", required=True)
    for n in ("s2-compare", "dev-screen", "dev-decide", "val-decide", "final", "report", "package"):
        add(n)
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    ns = build_parser().parse_args(argv)
    try:
        if ns.cmd != "freeze-shock":
            if not ns.root:
                raise UsageError("--root (ya da E18_ROOT) gerekli")
            root = Path(ns.root)
        if ns.cmd == "freeze-shock":
            return cmd_freeze_shock(ns.force)
        if ns.cmd == "cal-counts":
            return cmd_cal_counts(root)
        if ns.cmd == "lock":
            return cmd_lock(root, ns.commit)
        if ns.cmd == "verify-lock":
            return cmd_verify_lock(root)
        if ns.cmd == "check-run":
            return cmd_check_run(root, ns.label)
        if ns.cmd == "s2-compare":
            return cmd_s2_compare(root)
        if ns.cmd == "dev-screen":
            return cmd_dev_screen(root)
        if ns.cmd == "dev-decide":
            return cmd_dev_decide(root)
        if ns.cmd == "val-decide":
            return cmd_val_decide(root)
        if ns.cmd == "final":
            return cmd_final(root)
        if ns.cmd == "report":
            return cmd_report(root)
        if ns.cmd == "package":
            return cmd_package(root)
        raise UsageError(f"bilinmeyen komut {ns.cmd}")
    except E18Error as exc:
        print(f"HATA[{exc.code}] {ns.cmd}: {exc}", file=sys.stderr)
        return exc.code
    except Exception as exc:  # noqa: BLE001 - bozuk girdi = butunluk hatasi
        print(f"HATA[5] {ns.cmd}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return IntegrityError.code


if __name__ == "__main__":
    sys.exit(main())
