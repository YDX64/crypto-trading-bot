#!/usr/bin/env python3
"""ÖN-KAYITLI PLAN (prereg/PLAN.md §3-§4) analiz betiği.

Bu betik planın PARÇASIDIR ve ilk F2 koşusundan önce kilitlenir
(prereg/ANALYZE.sha256). Kilitten sonra yalnız hata düzeltmesi serbesttir;
eşik / pencere / karar kuralı değişikliği YASAKTIR.

Alt komutlar
  freeze  windows_frozen.json: BTCUSDT 1d önbelleğinden pencere rejim etiketi
          (UP/DOWN/FLAT) + ŞOK bayrağı. Mevcut dosyanın üzerine YAZMAZ (--force yok;
          dondurma geri alınamaz olmalıdır, silmek insan kararıdır).
  csv     results.csv (varyant x pencere x koşu türü).
  dev     DEV elemesi (F1): dev_screen.json + tablo.
  pvals   gün-bloklu bootstrap p-değerleri -> pvals.json; BH CLI'ı çağırır -> bh_out.json.
  decide  results.csv + gates.json + DECISION.md (G1-G10 satır satır).
  final   SON SINAV (tek varyant, CANLI penceresi) -> FINAL.md.

JSON şeması (`src/strategies/scalper/backtest.py` rapor yazıcısı, satır ~1995-2030 ve
gerçek koşu logs/backtest_20261010_034322.json'dan doğrulandı):
  {generated_at, params, provenance, overall{trades, wins, winrate(%), total_pnl,
   profit_factor, bar_max_drawdown, ...}, by_strategy, regime_breakdown,
   missed_signals, permutation, trades[{symbol, direction, entry_time, exit_time(ms),
   pnl, roi_pct, exit_reason, leverage, legs, ...}]}
  NOT: `_strict_json_value` sonsuz PF'yi JSON'a `null` yazar. Bu betik null'ı
  trades[]'ten yeniden hesaplar (kayıp yok & kâr var => inf => 5'e kırpılır).

Yalnız stdlib (PYTHONDONTWRITEBYTECODE ile çalıştırmak şart değil, ama
alt süreç BH CLI'ı bytecode yazmadan çağrılır).
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import random
import re
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------
# Sabitler (PLAN §1-§3). HEPSİ ÖNCEDEN SABİT.
# --------------------------------------------------------------------------
SP = Path(os.environ.get(
    "PREREG_SP",
    "/tmp/claude-0/-home-user-crypto-trading-bot/b53b3a36-e126-5de6-b27f-ca558d8408f8/scratchpad",
))
DEFAULT_ROOT = SP / "prereg"
DEFAULT_CACHE = Path("/home/user/crypto-trading-bot/data/klines_cache")
DEFAULT_BH_CWD = SP / "wt-research"  # multitest.py burada; yalnız okunur

INF = float("inf")
PF_CLIP = 5.0                       # inf -> 5
DEV_WINDOWS = ("AYI", "YATAY", "BOGA")
VAL_WINDOWS = tuple(f"V{i}" for i in range(1, 9))
EXTRA_WINDOWS = ("SMOKE", "CANLI")
E_VARIANTS = tuple(f"E{i}" for i in range(1, 9))
C_VARIANTS = ("C1", "C2", "C3")
FAMILY = E_VARIANTS + C_VARIANTS    # m = 11
BASE = "B0"
MIN_VALID_WINDOWS = 6               # <6 => deney geçersiz

DEV_MIN_PF = 1.0
DEV_MIN_TRADE_FRAC = 0.50           # B0 dev işleminin %50'si (386 -> 193)
G1_FRAC = 0.75                      # 6/8
G2_POOLED_PF = 1.15
G3_MEDIAN_PF = 1.10
G4_WORST_PF = 0.70
G5_MAX_WINDOW_DD = 2000.0
G6_MIN_TRADES = 400
G6_MIN_FRAC = 0.60
G7_GROUP_PF = 0.90
G8_MARGIN = 0.05
G9_STRESS_PF = 1.05
BH_ALPHA = 0.10
BH_EFF_TESTS = 6
C2_MIN_COVERAGE = 0.40

UP_THR, DOWN_THR, SHOCK_THR = 5.0, -5.0, 6.0   # %

BOOT_N = 20000
BOOT_SEED = 20261010

FINAL_MIN_PF = 1.05
FINAL_MIN_TRADES = 100
FINAL_MAX_DD = 2500.0
FINAL_B0_MARGIN = 0.10
FINAL_B0_PF_FALLBACK = 0.90

NO_WINNER_SENTENCE = (
    "KAZANAN YOK — kayıp-kazanç yapısı değişikliği bu veri üzerinde kanıtlanamadı; "
    "üretim ayarı değişmez."
)

# Durum değerleri
ST_BOOT = "BOOTSTRAP"
ST_DEV = "ELENDI_DEV"
ST_NOCODE = "KODLANMADI"
ST_COVER = "YETERSIZ_KAPSAM"


class PreregError(RuntimeError):
    """Plana göre ilerlenemeyen durum (eksik veri vb.). Sessiz geçilmez."""


# --------------------------------------------------------------------------
# Küçük yardımcılar
# --------------------------------------------------------------------------
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


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
    if isinstance(x, (list, tuple)):
        return [jsonable(v) for v in x]
    return x


def fmt(x: Any, nd: int = 3) -> str:
    if isinstance(x, float):
        if x == INF:
            return "inf"
        if x == -INF:
            return "-inf"
        return f"{x:.{nd}f}"
    return str(x)


def parse_lib(lib_path: Path) -> Tuple[Dict[str, Tuple[str, str]], Dict[str, List[str]]]:
    """lib.sh'teki WIN ve VAR tablolarını okur (tek gerçek kaynak)."""
    text = Path(lib_path).read_text(encoding="utf-8")
    i_win = text.index("declare -A WIN=(")
    i_var = text.index("declare -A VAR=(")
    i_end = text.index("fill_win ()")
    win_txt, var_txt = text[i_win:i_var], text[i_var:i_end]
    win: Dict[str, Tuple[str, str]] = {}
    for m in re.finditer(r'\[(\w+)\]="(\d{4}-\d\d-\d\d) (\d{4}-\d\d-\d\d)"', win_txt):
        win[m.group(1)] = (m.group(2), m.group(3))
    var: Dict[str, List[str]] = {}
    for m in re.finditer(r'\[(\w+)\]="([^"]*)"', var_txt):
        var[m.group(1)] = m.group(2).split()
    return win, var


def window_days(start: str, end: str) -> List[dt.date]:
    s = dt.date.fromisoformat(start)
    e = dt.date.fromisoformat(end)
    return [s + dt.timedelta(days=i) for i in range((e - s).days)]


def utc_day(ms: int) -> dt.date:
    return dt.datetime.fromtimestamp(ms / 1000.0, tz=dt.timezone.utc).date()


def date_ms(d: str) -> int:
    x = dt.date.fromisoformat(d)
    return int(dt.datetime(x.year, x.month, x.day, tzinfo=dt.timezone.utc).timestamp() * 1000)


# --------------------------------------------------------------------------
# Koşu yükleme ve metrikler
# --------------------------------------------------------------------------
def pf_from_sums(pos: float, neg_abs: float) -> float:
    if neg_abs > 0:
        return pos / neg_abs
    return INF if pos > 0 else 0.0


def clip_pf(x: float) -> float:
    """PLAN §3: inf ise 5'e kırpılır (yalnız inf)."""
    return PF_CLIP if x == INF else x


def pf_of_trades(pnls: Sequence[float]) -> float:
    pos = sum(p for p in pnls if p > 0)
    neg = abs(sum(p for p in pnls if p < 0))
    return pf_from_sums(pos, neg)


class Run:
    """Tek bir backtest JSON'u (varyant x pencere x tür)."""

    def __init__(self, label: str, doc: Dict[str, Any]):
        self.label = label
        ov = doc.get("overall")
        trades = doc.get("trades")
        if not isinstance(ov, dict) or not isinstance(trades, list):
            raise PreregError(f"{label}: JSON şeması bozuk (overall/trades yok)")
        self.trades_raw: List[Dict[str, Any]] = trades
        self.pnls: List[float] = [float(t["pnl"]) for t in trades]
        self.exit_times: List[int] = [int(t["exit_time"]) for t in trades]
        self.n = len(trades)
        self.warnings: List[str] = []
        if int(ov.get("trades", self.n)) != self.n:
            self.warnings.append(f"overall.trades={ov.get('trades')} != len(trades)={self.n}")
        self.winrate = float(ov.get("winrate") or 0.0)
        self.pnl = float(ov.get("total_pnl") or 0.0)
        if abs(self.pnl - sum(self.pnls)) > 1e-6 * max(1.0, abs(self.pnl)):
            self.warnings.append(f"overall.total_pnl={self.pnl} != Σtrades.pnl={sum(self.pnls)}")
        _bdd = ov.get("bar_max_drawdown")
        if _bdd is None:
            # Sessiz 0 YOK: eksik DD, G5'i (pencere DD ≤ 2000) geçirip Calmar'ı şişirirdi.
            self.warnings.append("overall.bar_max_drawdown YOK — INF sayıldı (G5/Calmar başarısız)")
            self.bar_dd = INF
        else:
            self.bar_dd = float(_bdd)
        calc = pf_of_trades(self.pnls)
        pf_json = ov.get("profit_factor")
        if pf_json is None:
            # JSON'da sonsuz PF null yazılır; trades[]'ten türet (0 işlem => 0.0).
            pf_raw = calc
        else:
            pf_raw = float(pf_json)
            if calc != INF and abs(pf_raw - calc) > 1e-6 * max(1.0, abs(calc)):
                self.warnings.append(f"overall.profit_factor={pf_raw} != trades'ten={calc}")
        self.pf_raw = pf_raw
        self.pf = clip_pf(pf_raw)

    @property
    def wins(self) -> int:
        return sum(1 for p in self.pnls if p > 0)

    @staticmethod
    def load(path: Path) -> "Run":
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
        return Run(Path(path).stem, doc)


def run_path(root: Path, variant: str, window: str, sfx: str = "") -> Path:
    return Path(root) / "runs" / f"{variant}_{window}{sfx}.json"


def load_runs(root: Path, variant: str, windows: Sequence[str], sfx: str = "") -> Dict[str, Run]:
    out: Dict[str, Run] = {}
    for w in windows:
        p = run_path(root, variant, w, sfx)
        if p.exists():
            out[w] = Run.load(p)
    return out


def pooled_pf(runs: Sequence[Run]) -> float:
    """Havuz PF = Σ(pozitif pnl) / |Σ(negatif pnl)| (tüm işlemler, ham; clip UYGULANMAZ)."""
    pnls: List[float] = []
    for r in runs:
        pnls.extend(r.pnls)
    return pf_of_trades(pnls)


def calmar(pnl: float, dd_sum: float) -> float:
    if dd_sum > 0:
        return pnl / dd_sum
    if pnl > 0:
        return INF
    return 0.0 if pnl == 0 else -INF


def g1_threshold(n_windows: int) -> int:
    """PF>1 şartı: 6/8 = %75, kullanılabilir pencere sayısına orantılı (yukarı yuvarla)."""
    return (3 * n_windows + 3) // 4


def aggregate(runs: Dict[str, Run], windows: Sequence[str]) -> Dict[str, Any]:
    rs = [runs[w] for w in windows]
    pfs = [r.pf for r in rs]
    pnl = sum(r.pnl for r in rs)
    dd = sum(r.bar_dd for r in rs)
    nt = sum(r.n for r in rs)
    wins = sum(r.wins for r in rs)
    return {
        "windows": list(windows),
        "trades": nt,
        "winrate": (wins / nt * 100.0) if nt else 0.0,
        "pnl": pnl,
        "pf_pooled": pooled_pf(rs),
        "pf_windows": {w: runs[w].pf for w in windows},
        "pf_median": statistics.median(pfs) if pfs else 0.0,
        "pf_min": min(pfs) if pfs else 0.0,
        "n_pf_gt1": sum(1 for x in pfs if x > 1.0),
        "dd_sum": dd,
        "dd_max": max((r.bar_dd for r in rs), default=0.0),
        "dd_windows": {w: runs[w].bar_dd for w in windows},
        "calmar": calmar(pnl, dd),
    }


# --------------------------------------------------------------------------
# freeze
# --------------------------------------------------------------------------
def _load_1d(path: Path) -> List[Dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def _cache_span(path: Path) -> Optional[Tuple[int, int]]:
    m = re.match(r"^BTCUSDT_1d_(\d+)_(\d+)\.json\.gz$", path.name)
    return (int(m.group(1)), int(m.group(2))) if m else None


def freeze_window(name: str, start: str, end: str, cache_dir: Path) -> Dict[str, Any]:
    """Tek pencere için dondurulmuş etiket. Kural (PLAN §1 Adım 0):
    pencere getirisi = son günün kapanışı / ilk günün açılışı - 1;
    >= +%5 UP, <= -%5 DOWN, aksi FLAT; günlük getiri = kapanış/açılış - 1 (1d mumu);
    herhangi |günlük| >= %6 => ŞOK."""
    s_ms, e_ms = date_ms(start), date_ms(end)
    days = (e_ms - s_ms) // 86_400_000
    cands: List[Tuple[int, Path]] = []
    for p in sorted(Path(cache_dir).glob("BTCUSDT_1d_*_*.json.gz")):
        span = _cache_span(p)
        if span and span[0] <= s_ms and span[1] >= e_ms:
            cands.append((span[1] - span[0], p))
    cands.sort()
    if not cands:
        raise PreregError(
            f"{name}: BTCUSDT 1d önbelleği {start}->{end} aralığını kapsamıyor "
            f"({cache_dir}); önce fill_win çalıştırın"
        )
    last_err = ""
    for _, p in cands:
        by_open = {int(c["open_time"]): c for c in _load_1d(p)}
        want = [s_ms + i * 86_400_000 for i in range(days)]
        miss = [t for t in want if t not in by_open]
        if miss:
            last_err = f"{p.name}: {len(miss)} gün eksik"
            continue
        cs = [by_open[t] for t in want]
        o0, c_last = float(cs[0]["open"]), float(cs[-1]["close"])
        ret = (c_last / o0 - 1.0) * 100.0
        dailies = [(float(c["close"]) / float(c["open"]) - 1.0) * 100.0 for c in cs]
        i_max = max(range(len(dailies)), key=lambda i: abs(dailies[i]))
        label = "UP" if ret >= UP_THR else ("DOWN" if ret <= DOWN_THR else "FLAT")
        return {
            "start": start, "end": end, "days": days,
            "btc_open": o0, "btc_close": c_last,
            "return_pct": ret, "label": label,
            "shock": abs(dailies[i_max]) >= SHOCK_THR,
            "max_abs_daily_return_pct": abs(dailies[i_max]),
            "max_abs_daily_date": dt.datetime.fromtimestamp(
                want[i_max] / 1000.0, tz=dt.timezone.utc).strftime("%Y-%m-%d"),
            "cache_file": p.name,
        }
    raise PreregError(f"{name}: kapsayan 1d dosyaları eksik gün içeriyor ({last_err})")


def cmd_freeze(args: argparse.Namespace) -> int:
    root = Path(args.root)
    out = root / "windows_frozen.json"
    if out.exists():
        print(f"HATA: {out} zaten var; dondurma tek seferliktir (silmek insan kararıdır).",
              file=sys.stderr)
        return 2
    win, _ = parse_lib(Path(args.lib))
    for w in DEV_WINDOWS + VAL_WINDOWS:
        if w not in win:
            raise PreregError(f"lib.sh WIN tablosunda {w} yok")
    res: Dict[str, Any] = {}
    errors: List[str] = []
    for name, (s, e) in win.items():
        kind = ("dev" if name in DEV_WINDOWS else "val" if name in VAL_WINDOWS
                else "other")
        try:
            info = freeze_window(name, s, e, Path(args.cache_dir))
        except PreregError as ex:
            if kind == "val":
                errors.append(str(ex))
                continue
            info = {"start": s, "end": e, "label": None, "shock": None, "note": str(ex)}
        info["kind"] = kind
        res[name] = info
    if errors:
        print("HATA: doğrulama pencerelerinden etiket dondurulamadı:", file=sys.stderr)
        for er in errors:
            print("  - " + er, file=sys.stderr)
        print("Dosya YAZILMADI (kısmi dondurma yok). fill_win bitince yeniden deneyin.",
              file=sys.stderr)
        return 3
    doc = {
        "rule": {
            "return": "son gün kapanış / ilk gün açılış - 1 (BTCUSDT 1d)",
            "UP": f">= +{UP_THR}%", "DOWN": f"<= {DOWN_THR}%", "FLAT": "aksi",
            "shock": f"herhangi günlük |kapanış/açılış-1| >= {SHOCK_THR}%",
        },
        "frozen_at_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "windows": res,
    }
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"YAZILDI {out}")
    for w in VAL_WINDOWS:
        i = res[w]
        print(f"  {w} {i['start']}->{i['end']} ret={i['return_pct']:+.2f}% "
              f"{i['label']}{' ŞOK' if i['shock'] else ''} (maks gün {i['max_abs_daily_return_pct']:.2f}% "
              f"@{i['max_abs_daily_date']})")
    return 0


def load_frozen(root: Path) -> Dict[str, Any]:
    p = Path(root) / "windows_frozen.json"
    if not p.exists():
        raise PreregError(f"{p} yok; önce `freeze` çalıştırın")
    return json.loads(p.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# DEV elemesi + aile sınıflandırması
# --------------------------------------------------------------------------
def dev_screen(root: Path, variants: Sequence[str]) -> Dict[str, Any]:
    b0 = load_runs(root, BASE, DEV_WINDOWS)
    if len(b0) != len(DEV_WINDOWS):
        raise PreregError(f"B0 dev koşuları eksik: {sorted(set(DEV_WINDOWS) - set(b0))}")
    b0_trades = sum(r.n for r in b0.values())
    thr = DEV_MIN_TRADE_FRAC * b0_trades
    out: Dict[str, Any] = {
        "b0_dev_trades": b0_trades,
        "b0_dev_pf": pooled_pf(list(b0.values())),
        "min_trades": thr,
        "note_386": None if b0_trades == 386 else f"PLAN 386 dedi, B0 dev işlem sayısı {b0_trades}",
        "variants": {},
    }
    for v in variants:
        rs = load_runs(root, v, DEV_WINDOWS)
        if len(rs) != len(DEV_WINDOWS):
            out["variants"][v] = {"status": "EKSIK", "missing": sorted(set(DEV_WINDOWS) - set(rs))}
            continue
        pf = pooled_pf(list(rs.values()))
        n = sum(r.n for r in rs.values())
        why = []
        if pf < DEV_MIN_PF:
            why.append(f"dev havuz PF {fmt(pf)} < {DEV_MIN_PF}")
        if n < thr:
            why.append(f"dev işlem {n} < {thr:g}")
        out["variants"][v] = {
            "status": "ELENDI" if why else "GECTI", "dev_pf": pf, "dev_trades": n,
            "dev_pnl": sum(r.pnl for r in rs.values()), "reasons": why,
            "pf_windows": {w: rs[w].pf for w in DEV_WINDOWS},
        }
    return out


def _trade_is_bb(t: Dict[str, Any]) -> Optional[bool]:
    v = t.get("tp_source")
    if v is None:
        legs = [lg.get("tp_source") for lg in (t.get("legs") or []) if isinstance(lg, dict)]
        legs = [x for x in legs if x is not None]
        if not legs:
            return None
        v = legs
    if isinstance(v, str):
        return "bb" in v.lower()
    if isinstance(v, (list, tuple)):
        return any("bb" in str(x).lower() for x in v)
    return None


def c2_coverage(runs: Dict[str, Run]) -> float:
    tot = bb = 0
    for r in runs.values():
        for t in r.trades_raw:
            flag = _trade_is_bb(t)
            if flag is None:
                raise PreregError(
                    "C2: işlem kaydında `tp_source` (veya legs[].tp_source) yok; "
                    "kapsam şartı ölçülemiyor — C2 uygulayıcısı tp_source yazmalı"
                )
            tot += 1
            bb += 1 if flag else 0
    return (bb / tot) if tot else 0.0


def classify_family(root: Path, excluded: Dict[str, str]) -> Dict[str, Dict[str, Any]]:
    """Her aile üyesi için durum. Eksik veri sessiz geçilmez (PreregError)."""
    info: Dict[str, Dict[str, Any]] = {}
    todo = [v for v in FAMILY if v not in excluded]
    screen = dev_screen(root, todo) if todo else {"variants": {}}
    for v in FAMILY:
        if v in excluded:
            info[v] = {"status": ST_NOCODE, "reason": excluded[v], "p_note": "p=1.0"}
            continue
        d = screen["variants"][v]
        if d["status"] == "EKSIK":
            raise PreregError(
                f"{v}: dev koşuları eksik {d['missing']}; kodlanmadıysa --not-coded {v} verin"
            )
        if d["status"] == "ELENDI":
            info[v] = {"status": ST_DEV, "reason": "; ".join(d["reasons"]), "dev": d,
                       "p_note": "p=1.0"}
            continue
        info[v] = {"status": ST_BOOT, "dev": d}
    return info


# --------------------------------------------------------------------------
# Bootstrap p-değeri
# --------------------------------------------------------------------------
def daily_pnl_vector(runs: Dict[str, Run], windows: Sequence[str],
                     frozen: Dict[str, Any]) -> List[float]:
    """Pencere günleri (işlemsiz gün = 0) + çıkış gününe (UTC) göre PnL.
    Pencere dışına düşen bir çıkış günü varsa kaybolmaz, ek gün olarak eklenir."""
    daily: Dict[Tuple[str, dt.date], float] = {}
    order: List[Tuple[str, dt.date]] = []
    for w in windows:
        fw = frozen["windows"][w]
        for d in window_days(fw["start"], fw["end"]):
            daily[(w, d)] = 0.0
            order.append((w, d))
    for w in windows:
        fw = frozen["windows"][w]
        days = set(window_days(fw["start"], fw["end"]))
        r = runs[w]
        for pnl, et in zip(r.pnls, r.exit_times):
            d = utc_day(et)
            key = (w, d)
            if d not in days and key not in daily:
                daily[key] = 0.0
                order.append(key)
            daily[key] += pnl
    return [daily[k] for k in order]


def bootstrap_p(daily: Sequence[float], n_boot: int = BOOT_N, seed: int = BOOT_SEED) -> float:
    """Gün-bloklu bootstrap; H0: ortalama günlük PnL <= 0.
    p = (#{ortalama* <= 0} + 1) / (n_boot + 1). Ortalama<=0 <=> toplam<=0 (n>0).
    Belirlenimci: stdlib random.Random(seed), indeks = int(random()*n)."""
    n = len(daily)
    if n == 0:
        return 1.0
    rng = random.Random(seed)
    rnd = rng.random
    vals = list(daily)
    cnt = 0
    for _ in range(n_boot):
        s = 0.0
        for _ in range(n):
            s += vals[int(rnd() * n)]
        if s <= 0.0:
            cnt += 1
    return (cnt + 1) / (n_boot + 1)


# --------------------------------------------------------------------------
# Ortak hazırlık: sınıflandırma + kullanılabilir pencereler
# --------------------------------------------------------------------------
def prepare(root: Path, lib: Path, excluded: Dict[str, str]) -> Dict[str, Any]:
    frozen = load_frozen(root)
    _, var = parse_lib(lib)
    info = classify_family(root, excluded)
    cands = [v for v in FAMILY if info[v]["status"] == ST_BOOT]
    b0_runs = load_runs(root, BASE, VAL_WINDOWS)
    var_runs = {v: load_runs(root, v, VAL_WINDOWS) for v in cands}
    for v in cands:
        if not var_runs[v]:
            raise PreregError(f"{v}: doğrulama koşusu hiç yok (dev'den geçti); F2 bitmeden "
                              f"analiz edilemez (kodlanmadıysa --not-coded {v})")
    usable = [w for w in VAL_WINDOWS
              if w in b0_runs and all(w in var_runs[v] for v in cands)]
    missing = [w for w in VAL_WINDOWS if w not in usable]
    # C2 kapsam şartı (yalnız validasyon verisiyle)
    if "C2" in cands:
        cov = c2_coverage({w: var_runs["C2"][w] for w in usable if w in var_runs["C2"]})
        info["C2"]["coverage_bb"] = cov
        if cov < C2_MIN_COVERAGE:
            info["C2"].update(status=ST_COVER, p_note="p=1.0",
                              reason=f"bb kaynaklı işlem payı {cov:.1%} < {C2_MIN_COVERAGE:.0%}")
            cands.remove("C2")
            # C2 çıkınca kullanılabilir pencere kümesi genişleyebilir
            usable = [w for w in VAL_WINDOWS
                      if w in b0_runs and all(w in var_runs[v] for v in cands)]
            missing = [w for w in VAL_WINDOWS if w not in usable]
    return {
        "frozen": frozen, "var_table": var, "info": info, "cands": cands,
        "b0_runs": b0_runs, "var_runs": var_runs, "usable": usable, "missing": missing,
        "valid": len(usable) >= MIN_VALID_WINDOWS, "root": Path(root),
    }


# --------------------------------------------------------------------------
# Kapılar
# --------------------------------------------------------------------------
def gate(gid: str, name: str, status: Any, detail: str) -> Dict[str, Any]:
    return {"id": gid, "name": name, "status": status, "detail": detail}


def evaluate_gates(agg: Dict[str, Any], b0: Dict[str, Any], labels: Dict[str, str],
                   usable: Sequence[str], root: Path, variant: str,
                   g10: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    gates: List[Dict[str, Any]] = []
    n = len(usable)
    thr = g1_threshold(n)
    gates.append(gate("G1", f"PF>1 olan pencere >= {thr}/{n}", agg["n_pf_gt1"] >= thr,
                      f"{agg['n_pf_gt1']}/{n} pencere PF>1; PF'ler "
                      + ", ".join(f"{w}={fmt(p, 2)}" for w, p in agg["pf_windows"].items())))
    gates.append(gate("G2", f"Havuz PF >= {G2_POOLED_PF}", agg["pf_pooled"] >= G2_POOLED_PF,
                      f"havuz PF={fmt(agg['pf_pooled'])}"))
    gates.append(gate("G3", f"Medyan PF >= {G3_MEDIAN_PF}", agg["pf_median"] >= G3_MEDIAN_PF,
                      f"medyan={fmt(agg['pf_median'])}"))
    gates.append(gate("G4", f"En kötü pencere PF >= {G4_WORST_PF}", agg["pf_min"] >= G4_WORST_PF,
                      f"min={fmt(agg['pf_min'])}"))
    over = {w: d for w, d in agg["dd_windows"].items() if d > G5_MAX_WINDOW_DD}
    g5 = (not over) and agg["calmar"] > 0 and agg["calmar"] > b0["calmar"]
    gates.append(gate("G5", f"bar_max_drawdown <= {G5_MAX_WINDOW_DD:g}/pencere; Calmar>0 ve > B0",
                      g5, f"maks pencere DD={fmt(agg['dd_max'], 1)}"
                      + (f" (aşan: {', '.join(f'{w}={d:.0f}' for w, d in over.items())})" if over else "")
                      + f"; Calmar={fmt(agg['calmar'])} (B0={fmt(b0['calmar'])})"))
    need6 = G6_MIN_FRAC * b0["trades"]
    gates.append(gate("G6", f"Toplam işlem >= {G6_MIN_TRADES} ve >= B0'ın {G6_MIN_FRAC:.0%}'ı",
                      agg["trades"] >= G6_MIN_TRADES and agg["trades"] >= need6,
                      f"işlem={agg['trades']} (eşik max({G6_MIN_TRADES}, {need6:.0f}); B0={b0['trades']})"))
    # G7: rejim grupları (DONDURULMUŞ etiket), havuz PF
    parts, ok7 = [], True
    for g in ("UP", "DOWN", "FLAT"):
        ws = [w for w in usable if labels.get(w) == g]
        if not ws:
            parts.append(f"{g}: pencere yok (boş grup, uygulanmaz)")
            continue
        runs = _runs_for(root, variant, ws)
        pf = pooled_pf(list(runs.values()))
        parts.append(f"{g}[{','.join(ws)}] havuz PF={fmt(pf)}")
        if not pf >= G7_GROUP_PF:
            ok7 = False
    gates.append(gate("G7", f"Her rejim grubunda havuz PF >= {G7_GROUP_PF}", ok7, "; ".join(parts)))
    gates.append(gate("G8", f"Havuz PF >= B0 + {G8_MARGIN}",
                      agg["pf_pooled"] >= b0["pf_pooled"] + G8_MARGIN,
                      f"havuz PF={fmt(agg['pf_pooled'])} vs B0={fmt(b0['pf_pooled'])}"
                      f" (eşik {fmt(b0['pf_pooled'] + G8_MARGIN)})"))
    g18 = all(g["status"] is True for g in gates)
    # G9
    if not g18:
        gates.append(gate("G9", f"fee-stress ve gecikme-1 havuz PF >= {G9_STRESS_PF}", "NA",
                          "G1-G8 geçmedi; G9 yalnız geçenlere uygulanır"))
    else:
        fee = _runs_for(root, variant, usable, "_fee", strict=False)
        dly = _runs_for(root, variant, usable, "_dly", strict=False)
        if len(fee) < n or len(dly) < n:
            gates.append(gate("G9", f"fee-stress ve gecikme-1 havuz PF >= {G9_STRESS_PF}", "PENDING",
                              f"koşu eksik: fee {len(fee)}/{n}, dly {len(dly)}/{n} (F3 koşulmalı)"))
        else:
            pf_f, pf_d = pooled_pf(list(fee.values())), pooled_pf(list(dly.values()))
            gates.append(gate("G9", f"fee-stress ve gecikme-1 havuz PF >= {G9_STRESS_PF}",
                              pf_f >= G9_STRESS_PF and pf_d >= G9_STRESS_PF,
                              f"fee-stress havuz PF={fmt(pf_f)}; gecikme-1 havuz PF={fmt(pf_d)}"))
    # G10
    if g10 is None:
        gates.append(gate("G10", f"BH rejected (q <= {BH_ALPHA})", "PENDING", "bh_out.json yok"))
    else:
        gates.append(gate("G10", f"BH rejected (q <= {BH_ALPHA})", bool(g10["rejected"]),
                          f"p={g10['p_value']:.6f}, q={g10['q_value']:.6f}, durum={g10.get('status')}"))
    return gates


def _runs_for(root: Path, variant: str, windows: Sequence[str], sfx: str = "",
              strict: bool = True) -> Dict[str, Run]:
    rs = load_runs(root, variant, windows, sfx)
    if strict and len(rs) != len(windows):
        raise PreregError(f"{variant}{sfx}: eksik koşu {sorted(set(windows) - set(rs))}")
    return rs


def overall_state(gates: Sequence[Dict[str, Any]]) -> str:
    if any(g["status"] is False for g in gates):
        return "KALDI"
    if any(g["status"] == "PENDING" for g in gates):
        return "BEKLIYOR"
    return "GECTI" if all(g["status"] is True for g in gates) else "KALDI"


# --------------------------------------------------------------------------
# CSV
# --------------------------------------------------------------------------
def write_results_csv(root: Path, lib: Path) -> Path:
    win, var = parse_lib(lib)
    try:
        frozen = load_frozen(root)["windows"]
    except PreregError:
        frozen = {}
    rows: List[Dict[str, Any]] = []
    for v in (BASE,) + FAMILY:
        for w in win:
            for sfx, kind in (("", "base"), ("_fee", "fee"), ("_dly", "dly")):
                p = run_path(root, v, w, sfx)
                if not p.exists():
                    continue
                r = Run.load(p)
                rows.append({
                    "variant": v, "window": w, "run": kind,
                    "window_kind": "dev" if w in DEV_WINDOWS else "val" if w in VAL_WINDOWS else "other",
                    "regime_frozen": (frozen.get(w) or {}).get("label"),
                    "shock": (frozen.get(w) or {}).get("shock"),
                    "trades": r.n, "winrate": round(r.winrate, 2), "total_pnl": round(r.pnl, 4),
                    "pf": "inf" if r.pf_raw == INF else round(r.pf_raw, 4),
                    "pf_clipped": round(r.pf, 4), "bar_max_drawdown": round(r.bar_dd, 4),
                })
    out = Path(root) / "results.csv"
    cols = ["variant", "window", "run", "window_kind", "regime_frozen", "shock", "trades",
            "winrate", "total_pnl", "pf", "pf_clipped", "bar_max_drawdown"]
    with open(out, "w", encoding="utf-8", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=cols)
        wr.writeheader()
        wr.writerows(rows)
    return out


# --------------------------------------------------------------------------
# Alt komutlar
# --------------------------------------------------------------------------
def parse_excl(s: Optional[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for tok in (s or "").split(","):
        tok = tok.strip()
        if tok:
            if tok not in FAMILY:
                raise PreregError(f"--not-coded: {tok} ailede yok ({FAMILY})")
            out[tok] = "kodlanmadı (önkoşul sağlanmadı / kod yok)"
    return out


def cmd_csv(args: argparse.Namespace) -> int:
    print(f"YAZILDI {write_results_csv(Path(args.root), Path(args.lib))}")
    return 0


def cmd_dev(args: argparse.Namespace) -> int:
    root = Path(args.root)
    excl = parse_excl(args.not_coded)
    todo = [v for v in FAMILY if v not in excl]
    scr = dev_screen(root, [v for v in todo if load_runs(root, v, DEV_WINDOWS) or args.strict])
    (root / "dev_screen.json").write_text(
        json.dumps(jsonable(scr), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"B0 dev: {scr['b0_dev_trades']} işlem, havuz PF {fmt(scr['b0_dev_pf'])}; "
          f"eşik >= {scr['min_trades']:g} işlem ve PF >= {DEV_MIN_PF}")
    if scr["note_386"]:
        print("UYARI:", scr["note_386"])
    for v, d in scr["variants"].items():
        if d["status"] == "EKSIK":
            print(f"  {v}: EKSİK {d['missing']}")
        else:
            print(f"  {v}: {d['status']:6s} PF={fmt(d['dev_pf'])} işlem={d['dev_trades']} "
                  f"pnl={d['dev_pnl']:.0f} {'; '.join(d['reasons'])}")
    return 0


def cmd_pvals(args: argparse.Namespace) -> int:
    root, lib = Path(args.root), Path(args.lib)
    excl = parse_excl(args.not_coded)
    prep = prepare(root, lib, excl)
    if not prep["valid"]:
        raise PreregError(f"kullanılabilir pencere {len(prep['usable'])} < {MIN_VALID_WINDOWS}: DENEY GEÇERSİZ")
    rows = []
    for v in FAMILY:
        inf = prep["info"][v]
        if inf["status"] == ST_BOOT:
            daily = daily_pnl_vector(prep["var_runs"][v], prep["usable"], prep["frozen"])
            p = bootstrap_p(daily, args.n_boot, args.seed)
            rows.append({"name": v, "p_value": p, "status": ST_BOOT, "days": len(daily),
                         "mean_daily_pnl": sum(daily) / len(daily) if daily else 0.0})
        else:
            rows.append({"name": v, "p_value": 1.0, "status": inf["status"],
                         "reason": inf.get("reason", "")})
    assert len(rows) == len(FAMILY) == 11
    pj = root / "pvals.json"
    pj.write_text(json.dumps(jsonable(rows), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"YAZILDI {pj} ({len(rows)} satır; usable={prep['usable']}, eksik={prep['missing']})")
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    cmd = [sys.executable, "-m", "src.strategies.scalper.multitest", "--json", str(pj),
           "--alpha", str(BH_ALPHA), "--effective-tests", str(BH_EFF_TESTS), "--format", "json"]
    cp = subprocess.run(cmd, cwd=str(args.bh_cwd), env=env, capture_output=True, text=True)
    if cp.returncode != 0:
        print(cp.stderr, file=sys.stderr)
        raise PreregError(f"BH CLI başarısız (rc={cp.returncode}); cwd={args.bh_cwd}")
    (root / "bh_out.json").write_text(cp.stdout, encoding="utf-8")
    rep = json.loads(cp.stdout)
    print(f"YAZILDI {root / 'bh_out.json'}: {rep['rejected']}/{rep['tests']} rejected (alpha={rep['alpha']})")
    for r in rep["rows"]:
        print(f"  {r['name']:3s} p={r['p_value']:.4f} q={r['q_value']:.4f} "
              f"{'REJECTED' if r['rejected'] else '-':8s} {r.get('status')}")
    return 0


def load_bh(root: Path) -> Dict[str, Dict[str, Any]]:
    bj, pj = Path(root) / "bh_out.json", Path(root) / "pvals.json"
    if not bj.exists() or not pj.exists():
        return {}
    rep = json.loads(bj.read_text(encoding="utf-8"))
    pv = {r["name"]: r["p_value"] for r in json.loads(pj.read_text(encoding="utf-8"))}
    out = {}
    for r in rep["rows"]:
        if abs(round(float(pv.get(r["name"], -1.0)), 6) - r["p_value"]) > 1e-9:
            raise PreregError("bh_out.json, pvals.json ile uyuşmuyor (bayat); `pvals` yeniden çalıştırın")
        out[r["name"]] = r
    return out


def cmd_decide(args: argparse.Namespace) -> int:
    root, lib = Path(args.root), Path(args.lib)
    excl = parse_excl(args.not_coded)
    csv_path = write_results_csv(root, lib)
    prep = prepare(root, lib, excl)
    frozen, usable, info, cands = prep["frozen"], prep["usable"], prep["info"], prep["cands"]
    labels = {w: frozen["windows"][w]["label"] for w in VAL_WINDOWS}
    plan_ok = _lock_line(root / "PLAN.md", root / "PLAN.sha256")
    self_ok = _lock_line(Path(__file__), root / "ANALYZE.sha256")
    lines: List[str] = []
    A = lines.append
    A("# KARAR (ön-kayıtlı plan, otomatik üretim)\n")
    A(f"- Üretim: {dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}")
    A(f"- PLAN.md kilidi: {plan_ok}")
    A(f"- analyze_prereg.py kilidi: {self_ok}")
    A(f"- Kullanılabilir doğrulama penceresi: {len(usable)}/8 = {', '.join(usable) or '-'}")
    if prep["missing"]:
        A(f"- **PENCERE EKSİK: {', '.join(prep['missing'])}** — kalan pencerelerle devam; "
          f"G1 eşiği {g1_threshold(len(usable))}/{len(usable)} (oran %75, yukarı yuvarlanır)")
    A("")
    if not prep["valid"]:
        A(f"## SONUÇ: DENEY GEÇERSİZ — kullanılabilir pencere {len(usable)} < {MIN_VALID_WINDOWS}.")
        (root / "DECISION.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("DENEY GEÇERSİZ"); return 0
    A("## Dondurulmuş rejim etiketleri")
    A("| Pencere | Aralık | BTC getirisi | Etiket | Şok | Maks |günlük| |")
    A("|---|---|---|---|---|---|")
    for w in VAL_WINDOWS:
        f = frozen["windows"][w]
        A(f"| {w} | {f['start']}→{f['end']} | {f['return_pct']:+.2f}% | {f['label']} | "
          f"{'EVET' if f['shock'] else '-'} | {f['max_abs_daily_return_pct']:.2f}% ({f['max_abs_daily_date']}) |")
    A("")
    b0 = aggregate(prep["b0_runs"], usable)
    # B0 kendi G1-G7 (taban ayakta mı)
    b0_gates = evaluate_gates(b0, dict(b0, calmar=-INF), labels, usable, root, BASE, None)[:7]
    # B0'da G5'in "> B0" kısmı kendine karşı anlamsız: yalnız DD<=2000 ve Calmar>0 bakılır
    over0 = [w for w, d in b0["dd_windows"].items() if d > G5_MAX_WINDOW_DD]
    b0_gates[4] = gate("G5", "B0: pencere DD <= 2000 ve Calmar > 0",
                       (not over0) and b0["calmar"] > 0,
                       f"maks DD={fmt(b0['dd_max'], 1)}; Calmar={fmt(b0['calmar'])}")
    b0_stands = all(g["status"] is True for g in b0_gates)
    A("## Taban B0")
    A(f"- işlem {b0['trades']}, WR %{b0['winrate']:.1f}, ΣPnL {b0['pnl']:.0f}, havuz PF {fmt(b0['pf_pooled'])}, "
      f"medyan PF {fmt(b0['pf_median'])}, en kötü PF {fmt(b0['pf_min'])}, Calmar {fmt(b0['calmar'])}")
    for g in b0_gates:
        A(f"- {g['id']} [{_tr(g['status'])}] {g['name']} — {g['detail']}")
    A(f"- **B0 G1-G7: {'GEÇTİ — taban doğrulamada ayakta' if b0_stands else 'GEÇMEDİ'}**\n")

    bh = load_bh(root)
    if not bh:
        A("> UYARI: bh_out.json yok — G10 BEKLİYOR (önce `pvals`).\n")
    results: Dict[str, Any] = {}
    for v in FAMILY:
        st = info[v]["status"]
        if st != ST_BOOT:
            results[v] = {"variant": v, "status": st, "gates": [],
                          "state": "ELENDI", "reason": info[v].get("reason", "")}
            continue
        agg = aggregate(prep["var_runs"][v], usable)
        gates = evaluate_gates(agg, b0, labels, usable, root, v, bh.get(v))
        results[v] = {"variant": v, "status": st, "agg": agg, "gates": gates,
                      "state": overall_state(gates), "p": bh.get(v)}
    # özet tablo
    A("## Özet")
    A("| Varyant | Durum | İşlem | Havuz PF | Medyan | Min | Calmar | G1 | G2 | G3 | G4 | G5 | G6 | G7 | G8 | G9 | G10 | Sonuç |")
    A("|" + "---|" * 18)
    for v in FAMILY:
        r = results[v]
        if r["state"] == "ELENDI":
            A(f"| {v} | {r['status']} | - | - | - | - | - |" + " - |" * 10 + f" ELENDİ ({r['reason']}) |")
            continue
        a = r["agg"]
        cells = " | ".join(_sym(g["status"]) for g in r["gates"])
        A(f"| {v} | {r['status']} | {a['trades']} | {fmt(a['pf_pooled'])} | {fmt(a['pf_median'])} | "
          f"{fmt(a['pf_min'])} | {fmt(a['calmar'])} | {cells} | {r['state']} |")
    A("")
    A("## Varyant varyant kapılar (G1–G10)")
    for v in FAMILY:
        r = results[v]
        A(f"### {v} — {r['state']}")
        if r["state"] == "ELENDI":
            A(f"- p=1.0, {r['status']}: {r['reason']}\n")
            continue
        for g in r["gates"]:
            A(f"- {g['id']} [{_tr(g['status'])}] {g['name']} — {g['detail']}")
        A("")
    # karar
    passed = [v for v in FAMILY if results[v]["state"] == "GECTI"]
    pending = [v for v in FAMILY if results[v]["state"] == "BEKLIYOR"]
    A("## SONUÇ")
    winner = None
    if passed and not pending:
        def key(v: str):
            c = results[v]["agg"]["calmar"]
            nparams = len(prep["var_table"].get(v, [])) if v in prep["var_table"] else 99
            return (-round(c, 6) if c != INF else -1e18, nparams, v)
        winner = sorted(passed, key=key)[0]
        a = results[winner]["agg"]
        A(f"**KAZANAN: {winner}** (G1–G10 hepsi doğru; Calmar {fmt(a['calmar'])}, havuz PF "
          f"{fmt(a['pf_pooled'])}). Tüm geçenler: {', '.join(passed)}. "
          f"Eşitlikte az parametre (lib.sh VAR override sayısı; tabloda olmayan: 99 = kaybeder).")
        A("Sonraki adım: SON SINAV (tek atış, `final` alt komutu); sonra testnet ≥ 5 gün; mainnet DEĞİL.")
        decision = "WINNER"
    elif pending:
        if passed:
            A(f"(Şimdiye dek tüm kapıları geçen: {', '.join(passed)} — ama daha yüksek Calmar'lı bir aday BEKLİYOR olabilir; "
              f"PLAN §3 'tüm kapıları geçenler arasından en yüksek Calmar' gereği karar verilmez.)")
        A(f"**BEKLİYOR** — G1–G8 geçen ama G9/G10 sonucu eksik: {', '.join(pending)}. "
          f"(F3 fee/gecikme koşuları ve/veya `pvals` tamamlanmalı.) Henüz karar yok.")
        decision = "PENDING"
    else:
        A(f"**{NO_WINNER_SENTENCE}**")
        decision = "NO_WINNER"
    if b0_stands:
        A("\nB0 kendisi G1–G7'yi geçti: **taban doğrulamada ayakta**.")
    warns = [f"{r.label}: {x}" for rs in [prep["b0_runs"], *prep["var_runs"].values()]
             for r in rs.values() for x in r.warnings]
    if warns:
        A("\n## Veri uyarıları")
        lines.extend(f"- {x}" for x in warns[:50])
    (root / "DECISION.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (root / "gates.json").write_text(json.dumps(jsonable({
        "decision": decision, "winner": winner, "usable": usable, "missing": prep["missing"],
        "b0": {"agg": b0, "gates": b0_gates, "stands": b0_stands},
        "variants": {v: {k: x for k, x in r.items() if k != "p"} | {"p": r.get("p")}
                     for v, r in results.items()},
    }), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"YAZILDI {csv_path}, {root / 'DECISION.md'}, {root / 'gates.json'}")
    print(f"KARAR: {decision}" + (f" {winner}" if winner else ""))
    return 0


def _tr(s: Any) -> str:
    return {True: "DOĞRU", False: "YANLIŞ", "PENDING": "BEKLİYOR", "NA": "UYGULANMAZ"}[s]


def _sym(s: Any) -> str:
    return {True: "✓", False: "✗", "PENDING": "…", "NA": "–"}[s]


def _lock_line(path: Path, shafile: Path) -> str:
    if not shafile.exists():
        return f"{shafile.name} YOK"
    want = shafile.read_text(encoding="utf-8").split()[0]
    got = sha256_file(path)
    return f"UYUŞUYOR ({got[:12]}…)" if want == got else f"**UYUŞMUYOR** (kayıtlı {want[:12]}…, şimdiki {got[:12]}…)"


def cmd_final(args: argparse.Namespace) -> int:
    root = Path(args.root)
    v = args.variant
    gpath = root / "gates.json"
    if not gpath.exists():
        print("HATA: gates.json yok — önce `decide`.", file=sys.stderr); return 2
    g = json.loads(gpath.read_text(encoding="utf-8"))
    if g.get("decision") != "WINNER" or g.get("winner") != v:
        print(f"HATA: SON SINAV yalnız `decide` kazananıyla koşulur (karar={g.get('decision')}, "
              f"kazanan={g.get('winner')}, istenen={v}).", file=sys.stderr); return 2
    if (root / "FINAL.md").exists():
        print("HATA: FINAL.md zaten var — SON SINAV TEK ATIŞTIR (PLAN §1c/§3); yeniden koşulmaz.", file=sys.stderr); return 2
    rv = Run.load(run_path(root, v, "CANLI"))
    p_b0 = run_path(root, BASE, "CANLI")
    b0_pf = Run.load(p_b0).pf if p_b0.exists() else FINAL_B0_PF_FALLBACK
    src = "B0_CANLI.json" if p_b0.exists() else f"sabit {FINAL_B0_PF_FALLBACK} (B0_CANLI.json yok)"
    checks = [
        ("PF >= 1.05", rv.pf >= FINAL_MIN_PF, f"PF={fmt(rv.pf)}"),
        ("ΣPnL > 0", rv.pnl > 0, f"ΣPnL={rv.pnl:.1f}"),
        ("İşlem >= 100", rv.n >= FINAL_MIN_TRADES, f"işlem={rv.n}"),
        ("bar_max_drawdown <= 2500", rv.bar_dd <= FINAL_MAX_DD, f"DD={rv.bar_dd:.1f}"),
        ("PF >= B0 + 0.10", rv.pf >= b0_pf + FINAL_B0_MARGIN, f"PF={fmt(rv.pf)} vs B0={fmt(b0_pf)} ({src})"),
    ]
    ok = all(c[1] for c in checks)
    md = [f"# SON SINAV — {v} (CANLI, tek atış)\n"]
    md += [f"- [{'DOĞRU' if c[1] else 'YANLIŞ'}] {c[0]} — {c[2]}" for c in checks]
    md.append(f"\n**{'GEÇTİ — sonraki adım testnet ≥ 5 gün (mainnet DEĞİL)' if ok else 'KALDI — kazanan yok, yeniden ayar yok'}**")
    (root / "FINAL.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--lib", default=None, help="lib.sh yolu (varsayılan <root>/lib.sh)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("freeze"); f.add_argument("--cache-dir", default=str(DEFAULT_CACHE))
    sub.add_parser("csv")
    for name in ("dev", "pvals", "decide"):
        p = sub.add_parser(name)
        p.add_argument("--not-coded", default="", help="virgülle: kodlanmayan aile üyeleri (p=1.0), ör. C1,C3")
    sub.choices["dev"].add_argument("--strict", action="store_true", help="eksik dev koşusunu EKSIK say")
    pv = sub.choices["pvals"]
    pv.add_argument("--n-boot", type=int, default=BOOT_N)
    pv.add_argument("--seed", type=int, default=BOOT_SEED)
    pv.add_argument("--bh-cwd", default=str(DEFAULT_BH_CWD))
    fn = sub.add_parser("final"); fn.add_argument("variant")
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.lib is None:
        args.lib = str(Path(args.root) / "lib.sh")
    handlers = {"freeze": cmd_freeze, "csv": cmd_csv, "dev": cmd_dev, "pvals": cmd_pvals,
                "decide": cmd_decide, "final": cmd_final}
    try:
        return handlers[args.cmd](args)
    except PreregError as ex:
        print(f"HATA: {ex}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
