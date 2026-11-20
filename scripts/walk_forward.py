#!/usr/bin/env python3
"""Walk-forward kosucusu + saklı pencere (holdout) kayıt defteri (paket E).

Iki is yapar:

1. WALK-FORWARD: [start, end) araligini katlara boler. Her katta taban ve her aday
   TRAIN penceresinde olculur; aday tabanin yerine yalniz (a) en az MIN_TRAIN_TRADES
   islem yaptiysa ve (b) TRAIN penceresindeki eslestirilmis skoru (havuzlanmis gunluk
   delta / bootstrap std hatasi, montecarlo.paired_ab_test) >= SELECT_MIN_SCORE ise
   gecer; en yuksek skor secilir, yoksa kat tabani korur. Secilen yapilandirma ve
   taban, parametreler DONDURULMUS olarak TEST penceresinde kosulur. Havuzlanmis
   ornek-disi (OOS) sonuclar, walk-forward verimliligi ve eslestirilmis gun-blok
   bootstrap farki raporlanir.

   AG YOK: kosu yalniz kline onbelleginden okur (--cache-dir). Baslamadan once her
   pencere icin gereken (sembol, zaman dilimi) serileri dogrular; eksik varsa acik
   bir liste ile cikar. Tam pencere anahtari yoksa ama onbellekte o araligi KAPSAYAN
   baska bir dosya varsa, tam anahtarli dosya o dosyadan DILIMLENEREK yazilir
   (backtest'in ag'dan cekecegi mumlarla ayni mumlar).

2. SAKLI PENCERE KAYDI docs/holdout_registry.json: her pencere "burned" | "validation"
   | "sealed". Mühürlü pencereye degen kosu --open-sealed "<amac>" olmadan REDDEDILIR;
   her MUHURLU/dogrulama acilisi `opens` listesine eklenir ("burned"e hic yazilmaz) ve her rapor, dokunulan pencerelerin kac kez
   acildigini basar. Kayit defterinde "end" KAPSAYICIDIR (backtest'in [start,end)
   penceresi icin end-1 gun yazilir).

   Alt komut: python scripts/walk_forward.py registry-check --start S --end E
   (E hariç; run_windows.sh ve baska arac bunu cagirir).

Yalniz stdlib + src.strategies.scalper.{montecarlo,kline_cache} (config/.env YUKLENMEZ).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import os
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent
for _p in (str(REPO_ROOT), str(SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DEFAULT_REGISTRY = REPO_ROOT / "docs" / "holdout_registry.json"
REGISTRY_STATUSES = ("burned", "validation", "sealed")

# Katlarda aday secim kurali.
MIN_TRAIN_TRADES = 60          # scripts/autoresearch.py MIN_TRADES_PER_WINDOW ile ayni
SELECT_MIN_SCORE = 1.0         # eslestirilmis delta / bootstrap std hatasi
SCORE_SENTINEL = 1e9           # se == 0 ve delta > 0
N_PATHS = 5000
BLOCK_DAYS = 3
SEED = 12345
BASELINE_ID = "BASELINE"

# backtest.py ile AYNI olmasi gereken onbellek anahtari sabitleri (backtest'i import
# etmek config/.env yukler; tests/test_walk_forward.py bunlari backtest ile karsilastirir).
WARMUP_BY_ROLE = (150, 100, 250)   # entry / context / regime
LEADER_WARMUP_ENTRY = 150
LEADER_WARMUP_INTRADAY = 100
LEADER_DAILY_EXTRA_DAYS = 3
LEADER_INTRADAY_TF = "15m"
DEFAULT_TIMEFRAMES = ("5m", "15m", "4h")

# (cikis_zamani_ms, net_pnl, yon "LONG"/"SHORT"/None)
Trade = Tuple[int, float, Optional[str]]
Runner = Callable[[str, str, str], Optional[List[Trade]]]


class RegistryError(RuntimeError):
    """Kayit defteri okunamadi / gecersiz."""


class SealedWindowError(RegistryError):
    """Muhurlu pencereye --open-sealed olmadan dokunulmak istendi."""


class WalkForwardError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Saklı pencere kayit defteri
# --------------------------------------------------------------------------


def load_registry(path: Path) -> List[Dict[str, Any]]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RegistryError(f"saklı pencere kaydı okunamadı: {path} ({exc})") from exc
    if not isinstance(raw, list):
        raise RegistryError(f"saklı pencere kaydı bir liste olmali: {path}")
    seen = set()
    for w in raw:
        try:
            name, status = w["name"], w["status"]
            date.fromisoformat(w["start"])
            date.fromisoformat(w["end"])
            if not isinstance(w.setdefault("opens", []), list):
                raise TypeError("opens")
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise RegistryError(f"gecersiz pencere kaydi {w!r}: {exc}") from exc
        if status not in REGISTRY_STATUSES:
            raise RegistryError(f"{name}: bilinmeyen status {status!r}")
        if name in seen:
            raise RegistryError(f"tekrarlayan pencere adi: {name}")
        seen.add(name)
    return raw


def save_registry(path: Path, registry: List[Dict[str, Any]]) -> None:
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _to_date(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def touched_windows(
    registry: Sequence[Dict[str, Any]], start: Any, end_exclusive: Any,
) -> List[Dict[str, Any]]:
    """[start, end_exclusive) ile kesisen kayit pencereleri (kayit 'end'i kapsayici)."""
    s, e = _to_date(start), _to_date(end_exclusive)
    out = []
    for w in registry:
        ws = date.fromisoformat(w["start"])
        we_excl = date.fromisoformat(w["end"]) + timedelta(days=1)
        if s < we_excl and ws < e:
            out.append(w)
    return out


def guard_windows(
    registry_path: Path,
    windows: Sequence[Tuple[Any, Any]],
    by: str,
    open_sealed: Optional[str] = None,
    record: bool = True,
) -> List[Dict[str, Any]]:
    """Kosunun dokunacagi pencereleri kayit defterine karsi denetler.

    `windows` = [(start, end_exclusive), ...]. Muhurlu bir pencereye degiliyorsa ve
    `open_sealed` (amac metni) bos ise SealedWindowError. `record=True` ile dokunulan HER
    pencerenin `opens` listesine BIR kayit eklenir (kosu basina bir acilis; ayni pencereye
    birden cok kosu penceresi degse de tek kayit); dogrulama pencerelerinde amac
    "kullanim" olur. "burned" pencerelere HIC yazilmaz (acilis izlenmez, dosya degismez).
    Doner: dokunulan pencerelerin ozeti [{name,status,opens,opened_now}] (opens sayisi
    bu acilis DAHIL).
    """
    registry = load_registry(registry_path)
    touched: Dict[str, Dict[str, Any]] = {}
    for s, e in windows:
        for w in touched_windows(registry, s, e):
            touched[w["name"]] = w
    purpose = (open_sealed or "").strip()
    sealed = [w for w in touched.values() if w["status"] == "sealed"]
    if sealed and not purpose:
        names = ", ".join(f"{w['name']} ({w['start']}..{w['end']})" for w in sealed)
        raise SealedWindowError(
            f"MUHURLU pencereye degiyor: {names}. Final sinavi aday paketi basina BIR kez "
            f"acilir; bilincli acmak icin --open-sealed \"<amac>\" ver (acilis kayda yazilir)."
        )
    opened_now = set()
    if record and touched:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        changed = False
        for w in touched.values():
            if w["status"] == "sealed":
                w["opens"].append({"at": now, "by": by, "purpose": purpose})
                opened_now.add(w["name"])
                changed = True
            elif w["status"] == "validation":
                w["opens"].append({"at": now, "by": by, "purpose": purpose or "kullanim"})
                changed = True
            # "burned": ASLA yazilmaz (takipli dosya; her siradan backtest agaci kirletir ve
            # deploy kapisi kirli agaci reddeder). Yanik pencerenin acilisi izlenmez.
        if changed:
            save_registry(registry_path, registry)
    return [
        {
            "name": w["name"], "status": w["status"], "opens": len(w["opens"]),
            "opened_now": w["name"] in opened_now,
        }
        for w in sorted(touched.values(), key=lambda x: x["start"])
    ]


def registry_fingerprint(path: Path) -> str:
    """Kayit dosyasinin sha256 on eki (12 hane): raporda hangi kayit surumune karsi
    denetlendigi gorunsun (--registry ile baska dosya gostermek sessiz kalmasin)."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]


def funding_missing(symbols: Sequence[str], funding_dir: Path) -> List[str]:
    """Funding onbellek dosyasi (<SEMBOL>.json) olmayan semboller: bunlar icin backtest
    sabit yedek orana duser (maliyet modeli iyimser/sabit)."""
    return [s for s in symbols if not (Path(funding_dir) / f"{s}.json").is_file()]


def format_touched(entries: Sequence[Dict[str, Any]]) -> str:
    if not entries:
        return "Saklı pencere kaydı: dokunulan kayıtlı pencere yok."
    lines = ["Saklı pencere kaydı (dokunulan pencereler, açılış sayısı):"]
    for t in entries:
        mark = "  <- BU KOSU ACTI" if t.get("opened_now") else ""
        lines.append(f"  [{t['status']:<10}] {t['name']:<14} acilis={t['opens']}{mark}")
    if any(t["status"] == "burned" for t in entries):
        lines.append("  NOT: yanik pencerelerin acilislari izlenmez (kayda yazilmaz; sayac eski kayitlardir).")
    return "\n".join(lines)


def registry_cli(argv: Sequence[str]) -> int:
    p = argparse.ArgumentParser(prog="walk_forward.py registry-check")
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True, help="backtest gibi HARIC")
    p.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    p.add_argument("--by", default="registry-check")
    p.add_argument("--open-sealed", default=None, metavar="AMAC")
    a = p.parse_args(list(argv))
    try:
        entries = guard_windows(
            Path(a.registry), [(a.start, a.end)], by=a.by, open_sealed=a.open_sealed,
        )
    except RegistryError as exc:
        print(f"HATA: {exc}", file=sys.stderr)
        return 2
    print(format_touched(entries))
    print(f"  kayit: {a.registry} sha256={registry_fingerprint(Path(a.registry))}")
    return 0


# --------------------------------------------------------------------------
# Kat tarihleri (saf)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Fold:
    index: int
    train_start: date
    train_end: date     # HARIC
    test_start: date
    test_end: date      # HARIC

    @property
    def train_days(self) -> int:
        return (self.train_end - self.train_start).days

    @property
    def test_days(self) -> int:
        return (self.test_end - self.test_start).days


def make_folds(
    start: date, end: date, train_days: int, test_days: int, step_days: Optional[int] = None,
    mode: str = "rolling", embargo_hours: float = 0.0,
) -> List[Fold]:
    """[start, end) icinde katlar. Pencereler gun hassasiyetindedir (backtest --start/--end
    tarihtir); embargo saatleri YUKARI tam gune yuvarlanir (train_end -> test_start arasi).
    rolling: train penceresi her katta step kadar kayar; anchored: train_start sabit,
    train_end step kadar uzar. Test pencereleri step == test_days iken bitisiktir; step <
    test_days ORNEK-DISI GUNLERI IKI KEZ SAYAR ve reddedilir."""
    if mode not in ("rolling", "anchored"):
        raise ValueError(f"mode rolling|anchored olmali: {mode!r}")
    step = test_days if step_days is None else step_days
    if min(train_days, test_days, step) <= 0 or embargo_hours < 0:
        raise ValueError("train/test/step gun sayilari pozitif, embargo >= 0 olmali")
    if step < test_days:
        raise ValueError(
            f"step-days ({step}) < test-days ({test_days}): test pencereleri ornek-disi "
            f"gunleri iki kez sayar"
        )
    embargo = math.ceil(embargo_hours / 24.0)
    folds: List[Fold] = []
    k = 0
    while True:
        if mode == "rolling":
            tr_s = start + timedelta(days=k * step)
            tr_e = tr_s + timedelta(days=train_days)
        else:
            tr_s = start
            tr_e = start + timedelta(days=train_days + k * step)
        te_s = tr_e + timedelta(days=embargo)
        te_e = te_s + timedelta(days=test_days)
        if te_e > end:
            break
        folds.append(Fold(k, tr_s, tr_e, te_s, te_e))
        k += 1
    if not folds:
        raise ValueError(
            f"[{start}, {end}) araligina hic kat sigmiyor (train {train_days} + embargo "
            f"{embargo} + test {test_days} gun)"
        )
    return folds


# --------------------------------------------------------------------------
# Istatistikler
# --------------------------------------------------------------------------


def _utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


def trade_stats(trades: Sequence[Trade]) -> Dict[str, Any]:
    """Islem listesinden: islem, kazanma %, PF, net, brut kar/zarar, islem-bazli max DD
    (cikis zamanina gore kronolojik kumulatif)."""
    n = len(trades)
    gp = sum(p for _, p, _ in trades if p > 0)
    gl = -sum(p for _, p, _ in trades if p < 0)
    wins = sum(1 for _, p, _ in trades if p > 0)
    cum = peak = dd = 0.0
    for _, p, _ in sorted(trades, key=lambda t: t[0]):
        cum += p
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    if gl > 0:
        pf: float = gp / gl
    else:
        pf = float("inf") if gp > 0 else 0.0
    return {
        "trades": n,
        "winrate": (100.0 * wins / n) if n else 0.0,
        "pf": pf,
        "net": sum(p for _, p, _ in trades),
        "gross_profit": gp,
        "gross_loss": gl,
        "max_dd": dd,
    }


def pooled_stats(trades: Sequence[Trade]) -> Dict[str, Any]:
    out = trade_stats(trades)
    out["by_side"] = {
        side: trade_stats([t for t in trades if t[2] == side]) for side in ("LONG", "SHORT")
    }
    return out


def _days(start: date, end: date) -> List[date]:
    return [start + timedelta(days=i) for i in range((end - start).days)]


def paired_score(
    cand: Sequence[Trade], base: Sequence[Trade], days: Sequence[date],
    n_paths: int = N_PATHS, block_days: int = BLOCK_DAYS, seed: int = SEED,
) -> Dict[str, Any]:
    """Aday - taban gunluk PnL farki: havuzlanmis delta, bootstrap std hatasi, skor = delta/se,
    GA ve p-degeri (montecarlo.paired_ab_test)."""
    from src.strategies.scalper import montecarlo

    r = montecarlo.paired_ab_test(
        [(_utc(t), p) for t, p, _ in cand], [(_utc(t), p) for t, p, _ in base],
        list(days), n_paths=n_paths, block_days=block_days, seed=seed,
    )
    se, delta = r["std_error"], r["delta"]
    if se > 0:
        score = delta / se
    else:
        score = SCORE_SENTINEL if delta > 0 else 0.0
    return {"delta": delta, "se": se, "score": score, "ci": r["ci"], "p_value": r["p_value"],
            "n_days": r["n_days"], "insufficient_sample": r["insufficient_sample"]}


def select_candidate(
    base_train: Sequence[Trade], cand_train: Dict[str, Sequence[Trade]], days: Sequence[date],
    min_trades: int = MIN_TRAIN_TRADES, min_score: float = SELECT_MIN_SCORE,
    n_paths: int = N_PATHS, block_days: int = BLOCK_DAYS, seed: int = SEED,
) -> Tuple[str, List[Dict[str, Any]]]:
    """YALNIZ train verisiyle secim. Doner: (secilen_id | BASELINE_ID, aday ozetleri)."""
    details: List[Dict[str, Any]] = []
    best_id, best_score = BASELINE_ID, -math.inf
    for cid, trades in cand_train.items():
        s = paired_score(trades, base_train, days, n_paths, block_days, seed)
        enough = len(trades) >= min_trades
        eligible = enough and s["score"] >= min_score
        details.append({
            "id": cid, "trades": len(trades), "enough_trades": enough, "score": s["score"],
            "delta": s["delta"], "se": s["se"], "eligible": eligible,
        })
        if eligible and s["score"] > best_score:
            best_id, best_score = cid, s["score"]
    return best_id, details


# --------------------------------------------------------------------------
# Walk-forward surucusu
# --------------------------------------------------------------------------


class Evaluator:
    """(yapilandirma_id, start, end) -> islem listesi; ayni gorev bir kez kosulur.
    `prefetch` gorevleri `jobs` iscide paralel kosar (yalniz onbellek-okuma modu)."""

    def __init__(self, runner: Runner, jobs: int = 1) -> None:
        self.runner = runner
        self.jobs = max(1, int(jobs))
        self.cache: Dict[Tuple[str, str, str], List[Trade]] = {}

    def prefetch(self, tasks: Sequence[Tuple[str, str, str]]) -> None:
        todo = [t for t in dict.fromkeys(tasks) if t not in self.cache]
        if not todo:
            return
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.jobs) as pool:
            results = list(pool.map(lambda t: self.runner(*t), todo))
        for task, res in zip(todo, results):
            if res is None:
                raise WalkForwardError(f"backtest basarisiz: {task[0]} {task[1]}..{task[2]}")
            self.cache[task] = list(res)

    def get(self, cid: str, start: date, end: date) -> List[Trade]:
        key = (cid, start.isoformat(), end.isoformat())
        self.prefetch([key])
        return self.cache[key]


def run_walk_forward(
    runner: Runner, candidate_ids: Sequence[str], folds: Sequence[Fold], jobs: int = 1,
    min_trades: int = MIN_TRAIN_TRADES, min_score: float = SELECT_MIN_SCORE,
    n_paths: int = N_PATHS, block_days: int = BLOCK_DAYS, seed: int = SEED,
) -> Dict[str, Any]:
    ev = Evaluator(runner, jobs)
    ids = [c for c in candidate_ids if c != BASELINE_ID]
    iso = lambda d: d.isoformat()  # noqa: E731

    # Faz 1: tum katlarin train kosulari (taban + adaylar).
    ev.prefetch([
        (cid, iso(f.train_start), iso(f.train_end))
        for f in folds for cid in [BASELINE_ID] + ids
    ])
    selections: Dict[int, Tuple[str, List[Dict[str, Any]]]] = {}
    for f in folds:
        base_tr = ev.get(BASELINE_ID, f.train_start, f.train_end)
        cand_tr = {cid: ev.get(cid, f.train_start, f.train_end) for cid in ids}
        selections[f.index] = select_candidate(
            base_tr, cand_tr, _days(f.train_start, f.train_end),
            min_trades, min_score, n_paths, block_days, seed,
        )
    # Faz 2: secilen (donduruldu) + taban test kosulari.
    ev.prefetch([
        (cid, iso(f.test_start), iso(f.test_end))
        for f in folds for cid in {BASELINE_ID, selections[f.index][0]}
    ])

    fold_rows: List[Dict[str, Any]] = []
    sel_test: List[Trade] = []
    base_test: List[Trade] = []
    sel_train_net = 0.0
    train_day_total = 0
    test_cal: List[date] = []
    for f in folds:
        sel, details = selections[f.index]
        tr_sel = ev.get(sel, f.train_start, f.train_end)
        te_sel = ev.get(sel, f.test_start, f.test_end)
        te_base = ev.get(BASELINE_ID, f.test_start, f.test_end)
        sel_test += te_sel
        base_test += te_base
        sel_train_net += trade_stats(tr_sel)["net"]
        train_day_total += f.train_days
        test_cal += _days(f.test_start, f.test_end)
        fold_rows.append({
            "index": f.index,
            "train": [iso(f.train_start), iso(f.train_end)],
            "test": [iso(f.test_start), iso(f.test_end)],
            "selected": sel,
            "candidates": details,
            "train_selected": trade_stats(tr_sel),
            "train_baseline": trade_stats(ev.get(BASELINE_ID, f.train_start, f.train_end)),
            "test_selected": trade_stats(te_sel),
            "test_baseline": trade_stats(te_base),
        })

    pooled_sel, pooled_base = pooled_stats(sel_test), pooled_stats(base_test)
    test_day_total = len(test_cal)
    oos_per_day = pooled_sel["net"] / test_day_total if test_day_total else 0.0
    train_per_day = sel_train_net / train_day_total if train_day_total else 0.0
    return {
        "folds": fold_rows,
        "pooled": {"selected": pooled_sel, "baseline": pooled_base,
                   "test_days": test_day_total},
        "wfe": {
            "oos_net_per_day": oos_per_day, "train_net_per_day": train_per_day,
            "value": (oos_per_day / train_per_day) if train_per_day != 0 else None,
        },
        "paired_oos": paired_score(sel_test, base_test, test_cal, n_paths, block_days, seed),
        "selection_counts": dict(Counter(sel for sel, _ in selections.values())),
    }


# --------------------------------------------------------------------------
# Onbellek kapsami (ag YOK)
# --------------------------------------------------------------------------


def _truthy(v: Optional[str]) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def env_data_profile(env: Dict[str, str]) -> Dict[str, Any]:
    """Env'den onbellek anahtarini belirleyen ayarlar: (entry, context, regime) TF ve lider kapisi."""
    tf = (
        env.get("SCALPER_TF_ENTRY", DEFAULT_TIMEFRAMES[0]) or DEFAULT_TIMEFRAMES[0],
        env.get("SCALPER_TF_CONTEXT", DEFAULT_TIMEFRAMES[1]) or DEFAULT_TIMEFRAMES[1],
        env.get("SCALPER_TF_REGIME", DEFAULT_TIMEFRAMES[2]) or DEFAULT_TIMEFRAMES[2],
    )
    leader = None
    if _truthy(env.get("SCALPER_MARKET_GATE")):
        try:
            run_days = int(env.get("SCALPER_MARKET_GATE_RUN_DAYS", "3") or 3)
        except ValueError:
            run_days = 3
        leader = {
            "symbol": (env.get("SCALPER_MARKET_GATE_SYMBOL") or "BTCUSDT").strip().upper() or "BTCUSDT",
            "run_days": run_days,
        }
    return {"timeframes": tf, "leader": leader}


def _ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)


def required_series(
    symbols: Sequence[str], profile: Dict[str, Any], start: date, end: date,
) -> List[Tuple[str, str, int, int]]:
    """Bir backtest penceresinin okuyacagi (sembol, aralik, needed, end_ms) serileri
    (backtest.gather_symbol_data / gather_leader_series ile ayni formuller)."""
    from src.strategies.scalper import kline_cache

    def per_day(tf: str) -> int:
        step = kline_cache._INTERVAL_MS.get(tf)
        if step is None:
            raise ValueError(f"Desteklenmeyen zaman dilimi: {tf!r}")
        return 86_400_000 // step

    days = max(1, math.ceil((end - start).days))
    end_ms = _ms(end)
    out: List[Tuple[str, str, int, int]] = []
    for sym in symbols:
        for tf, warm in zip(profile["timeframes"], WARMUP_BY_ROLE):
            out.append((sym, tf, days * per_day(tf) + warm, end_ms))
    leader = profile.get("leader")
    if leader:
        sym, entry_tf = leader["symbol"], profile["timeframes"][0]
        out.append((sym, entry_tf, days * per_day(entry_tf) + LEADER_WARMUP_ENTRY, end_ms))
        out.append((sym, "1d", days + max(1, leader["run_days"]) + LEADER_DAILY_EXTRA_DAYS, end_ms))
        out.append((sym, LEADER_INTRADAY_TF,
                    days * per_day(LEADER_INTRADAY_TF) + LEADER_WARMUP_INTRADAY, end_ms))
    return list(dict.fromkeys(out))


def _cache_index(cache_dir: Path, symbol: str, interval: str) -> List[Tuple[int, int, Path]]:
    out = []
    for p in Path(cache_dir).glob(f"{symbol}_{interval}_*_*.json.gz"):
        parts = p.name[: -len(".json.gz")].split("_")
        try:
            out.append((int(parts[-2]), int(parts[-1]), p))
        except (ValueError, IndexError):
            continue
    return out


def ensure_cache(
    cache_dir: Path, required: Sequence[Tuple[str, str, int, int]],
) -> Tuple[List[str], List[str]]:
    """Her gerekli seri icin tam anahtarli onbellek dosyasini garanti eder. Yoksa onbellekte
    o araligi kapsayan baska bir dosyadan dilimleyip yazar. Doner: (eksikler, turetilenler)."""
    from src.strategies.scalper import kline_cache

    missing: List[str] = []
    derived: List[str] = []
    for sym, tf, needed, end_ms in required:
        start_ms = kline_cache.window_start_ms(tf, needed, end_ms)
        exact = kline_cache.cache_file(cache_dir, sym, tf, start_ms, end_ms)
        label = f"{sym} {tf}: {needed} mum, bitis {_utc(end_ms):%Y-%m-%d %H:%M}"
        if exact.exists():
            continue
        done = False
        for f_start, f_end, path in sorted(_cache_index(cache_dir, sym, tf)):
            if f_end < end_ms or f_start > start_ms:
                continue
            candles = kline_cache.load(cache_dir, sym, tf, f_start, f_end)
            if not candles:
                continue
            sliced = [c for c in candles if c.close_time <= end_ms][-needed:]
            if len(sliced) == needed:
                kline_cache.save(cache_dir, sym, tf, start_ms, end_ms, sliced)
                derived.append(label)
                done = True
                break
        if not done:
            missing.append(label)
    return missing, derived


def format_missing(missing: Sequence[str]) -> str:
    return (
        "HATA: kline onbellegi yetersiz (walk-forward AG KULLANMAZ). Eksik seriler:\n  "
        + "\n  ".join(missing)
        + "\nOnbellegi doldur (backtest.py --cache-dir ile ilgili pencereyi bir kez kos) veya "
        "--start/--end'i onbellegin kapsadigi araliga daralt."
    )


# --------------------------------------------------------------------------
# Rapor
# --------------------------------------------------------------------------


def _jsonable(o: Any) -> Any:
    if isinstance(o, float) and math.isinf(o):
        return "inf" if o > 0 else "-inf"
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    return o


def _pf(v: float) -> str:
    return "inf" if v == float("inf") else f"{v:.2f}"


def _cell(s: Dict[str, Any]) -> str:
    return f"{s['trades']} / {_pf(s['pf'])} / {s['net']:+.1f}"


def render_markdown(report: Dict[str, Any]) -> str:
    cfg = report["config"]
    L: List[str] = []
    L.append(f"# Walk-forward raporu ({cfg['mode']}, train {cfg['train_days']}g, "
             f"test {cfg['test_days']}g, adim {cfg['step_days']}g, embargo {cfg['embargo_hours']}s)")
    L.append("")
    L.append(f"Aralik [{cfg['start']}, {cfg['end']}) · {len(report['folds'])} kat · "
             f"{len(cfg['candidates'])} aday. Her pencere bagimsiz yeniden simulasyondur; "
             f"pozisyonlar pencere sonunda zorla kapanir (sinirdan tasan islem yok).")
    L.append("")
    L.append("| Kat | Train | Test | Secilen | Train sec. (n/PF/net) | Test sec. | Test taban |")
    L.append("|---|---|---|---|---|---|---|")
    for f in report["folds"]:
        L.append(
            f"| {f['index']} | {f['train'][0]}..{f['train'][1]} | {f['test'][0]}..{f['test'][1]} "
            f"| {f['selected']} | {_cell(f['train_selected'])} | {_cell(f['test_selected'])} "
            f"| {_cell(f['test_baseline'])} |"
        )
    L.append("")
    p = report["pooled"]
    L.append(f"## Havuzlanmis ornek-disi sonuc ({p['test_days']} test gunu)")
    L.append("")
    L.append("| | Islem | Kazanma % | PF | Net PnL | Max DD | LONG n/net | SHORT n/net |")
    L.append("|---|---|---|---|---|---|---|---|")
    for name, s in (("Secilen", p["selected"]), ("Taban", p["baseline"])):
        lg, sh = s["by_side"]["LONG"], s["by_side"]["SHORT"]
        L.append(
            f"| {name} | {s['trades']} | {s['winrate']:.1f} | {_pf(s['pf'])} | {s['net']:+.1f} "
            f"| {s['max_dd']:.1f} | {lg['trades']} / {lg['net']:+.1f} | {sh['trades']} / {sh['net']:+.1f} |"
        )
    L.append("")
    w = report["wfe"]
    wfe = "tanimsiz (train net/gun = 0)" if w["value"] is None else f"{w['value']:.2f}"
    L.append(f"- Walk-forward verimliligi (OOS net/gun / train net/gun): {wfe} "
             f"({w['oos_net_per_day']:+.2f} / {w['train_net_per_day']:+.2f})")
    pr = report["paired_oos"]
    L.append(f"- Eslestirilmis gun-blok bootstrap (secilen - taban, havuzlanmis OOS gunleri): "
             f"delta {pr['delta']:+.1f}, %95 GA [{pr['ci']['lo']:+.1f}, {pr['ci']['hi']:+.1f}], "
             f"p = {pr['p_value']:.4f}" + (" (YETERSIZ ORNEK)" if pr["insufficient_sample"] else ""))
    counts = ", ".join(f"{k}: {v}" for k, v in sorted(report["selection_counts"].items()))
    L.append(f"- Aday secilme sayilari: {counts}")
    L.append("")
    L.append("```text")
    L.append(report["registry_text"])
    L.append("```")
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------
# Gercek runner (autoresearch'un duzeltilmis kosu hattini kullanir)
# --------------------------------------------------------------------------


def make_cli_runner(
    env_by_id: Dict[str, Dict[str, str]], symbols: str, cache_dir: str, out_dir: Path,
) -> Runner:
    """Her gorev KENDI calisma dizininde kosar: backtest raporu `logs/backtest_<saniye>.json`
    adiyla cwd'ye yazilir; paralel kosularin ayni saniyede ayni dosyayi ezmemesi icin her
    gorev kendi dizinine (src -> repo/src sembolik baglanti) sahiptir."""
    import autoresearch as ar

    def runner(cid: str, start: str, end: str) -> Optional[List[Trade]]:
        job = out_dir / "_jobs" / f"{cid}_{start}_{end}"
        job.mkdir(parents=True, exist_ok=True)
        link = job / "src"
        if not link.exists():
            link.symlink_to(REPO_ROOT / "src")
        ok, metrics = ar.run_single_backtest(
            env_vars=env_by_id[cid], symbols=symbols, start=start, end=end,
            cache_dir=cache_dir, log_path=job / "backtest.log", bot_log_dir=job / "_botlog",
            repo_root=job,
        )
        if not ok or metrics is None or "trade_pnls" not in metrics:
            return None
        sides = metrics.get("trade_sides") or [None] * len(metrics["trade_pnls"])
        return [(int(t), float(p), s) for (t, p), s in zip(metrics["trade_pnls"], sides)]

    return runner


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Walk-forward: train pencerelerinde aday sec, parametreleri dondurup bitisik test "
            "pencerelerinde olc. Yalniz kline onbellegi (AG YOK)."
        )
    )
    p.add_argument("--start", required=True, help="YYYY-MM-DD (UTC)")
    p.add_argument("--end", required=True, help="YYYY-MM-DD (UTC, HARIC)")
    p.add_argument("--train-days", type=int, required=True)
    p.add_argument("--test-days", type=int, required=True)
    p.add_argument("--step-days", type=int, default=None, help="varsayilan = --test-days")
    p.add_argument("--mode", choices=("rolling", "anchored"), default="rolling")
    p.add_argument("--candidates", default=None, help="aday JSON (varsayilan: autoresearch_candidates.json)")
    p.add_argument("--env-file", required=True, help="taban SCALPER_* env dosyasi")
    p.add_argument("--symbols", default=None, help="virgullu; varsayilan env'deki allowlist")
    p.add_argument("--jobs", type=int, default=1, help="paralel backtest sayisi (yalniz onbellek modu)")
    p.add_argument(
        "--embargo-hours", type=float, default=0.0,
        help=(
            "train sonu ile test basi arasi bosluk (saat, YUKARI tam gune yuvarlanir; varsayilan 0). "
            "Her pencere BAGIMSIZ yeniden simulasyondur ve pozisyonlar pencere sonunda zorla "
            "kapanir: hicbir islem sinirdan tasmaz, embargo bu yuzden varsayilan olarak gereksizdir."
        ),
    )
    p.add_argument("--cache-dir", default="data/klines_cache")
    p.add_argument("--out-dir", default=None, help="varsayilan logs/walk_forward/<zaman>")
    p.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    p.add_argument("--open-sealed", default=None, metavar="AMAC",
                   help="muhurlu pencereye degen kosuyu bilincli ac (amac kayda yazilir)")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "registry-check":
        return registry_cli(argv[1:])
    args = build_arg_parser().parse_args(argv)

    import autoresearch as ar

    try:
        if not Path(args.env_file).is_file():
            raise ar.EnvFileError(f"env-file bulunamadi: '{args.env_file}'")
        base_env = ar.parse_env_file(args.env_file)
        ar.require_scalper_env(base_env, args.env_file)
        start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
        folds = make_folds(start, end, args.train_days, args.test_days, args.step_days,
                           args.mode, args.embargo_hours)
    except (ar.EnvFileError, ValueError) as exc:
        print(f"HATA: {exc}", file=sys.stderr)
        return 1
    step = args.test_days if args.step_days is None else args.step_days
    symbol_str = (args.symbols or base_env.get("SCALPER_SYMBOL_ALLOWLIST", "")).strip() \
        or ar.DEFAULT_SYMBOLS
    symbols = [s.strip() for s in symbol_str.split(",") if s.strip()]
    candidates = ar.load_candidates(Path(args.candidates) if args.candidates else ar.DEFAULT_CANDIDATES_PATH)

    env_by_id: Dict[str, Dict[str, str]] = {BASELINE_ID: dict(base_env)}
    for c in candidates:
        env_by_id[c.id] = {**base_env, **c.overrides}

    # 1) Saklı pencere denetimi (kayit henuz YAZILMAZ).
    windows = sorted({(f.train_start, f.train_end) for f in folds}
                     | {(f.test_start, f.test_end) for f in folds})
    try:
        guard_windows(Path(args.registry), windows, by="walk_forward",
                      open_sealed=args.open_sealed, record=False)
    except RegistryError as exc:
        print(f"HATA: {exc}", file=sys.stderr)
        return 2

    # 2) Onbellek kapsami.
    cache_dir = Path(args.cache_dir)
    if not cache_dir.is_absolute():
        cache_dir = REPO_ROOT / cache_dir
    required: List[Tuple[str, str, int, int]] = []
    for env in env_by_id.values():
        profile = env_data_profile(env)
        for ws, we in windows:
            required += required_series(symbols, profile, ws, we)
    required = list(dict.fromkeys(required))
    missing, derived = ensure_cache(cache_dir, required)
    if missing:
        print(format_missing(missing), file=sys.stderr)
        return 3
    if derived:
        print(f"Onbellekten dilimlenerek {len(derived)} tam-anahtarli dosya yazildi.")

    # 3) Kayit: muhurlu acilis burada yazilir (artik kosu gercekten baslayacak).
    try:
        touched = guard_windows(Path(args.registry), windows, by="walk_forward",
                                open_sealed=args.open_sealed, record=True)
    except RegistryError as exc:
        print(f"HATA: {exc}", file=sys.stderr)
        return 2
    registry_text = format_touched(touched) + (
        f"\n  kayit: {args.registry} sha256={registry_fingerprint(Path(args.registry))}")
    fmiss = funding_missing(symbols, ar.DEFAULT_FUNDING_CACHE_DIR)
    if fmiss:
        registry_text += (
            f"\n  UYARI: funding onbellegi yok ({', '.join(fmiss)}): bu semboller sabit yedek "
            f"oranla olculur (scripts/fill_funding_cache.py)")
    print(registry_text)

    out_dir = Path(args.out_dir) if args.out_dir else (
        REPO_ROOT / "logs" / "walk_forward" / datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"))
    out_dir.mkdir(parents=True, exist_ok=True)
    runner = make_cli_runner(env_by_id, ",".join(symbols), str(cache_dir), out_dir)
    try:
        result = run_walk_forward(runner, [c.id for c in candidates], folds, jobs=args.jobs)
    except WalkForwardError as exc:
        print(f"HATA: {exc}", file=sys.stderr)
        return 1

    report = {
        "config": {
            "start": args.start, "end": args.end, "train_days": args.train_days,
            "test_days": args.test_days, "step_days": step, "mode": args.mode,
            "embargo_hours": args.embargo_hours, "symbols": symbols,
            "candidates": [c.id for c in candidates], "min_train_trades": MIN_TRAIN_TRADES,
            "select_min_score": SELECT_MIN_SCORE, "jobs": args.jobs,
            "git_sha": ar.get_git_sha(REPO_ROOT),
            "git_dirty_fingerprint": ar.git_dirty_fingerprint(REPO_ROOT),
            "env_hash": ar.env_content_hash(args.env_file),
        },
        **result,
        "registry": touched,
        "registry_text": registry_text,
    }
    (out_dir / "report.json").write_text(
        json.dumps(_jsonable(report), indent=2, ensure_ascii=False), encoding="utf-8")
    md = render_markdown(report)
    (out_dir / "report.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"Rapor: {out_dir}/report.json , report.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
