"""Esdeger-yol (equity-path) Monte Carlo ve bootstrap istatistikleri.

Girdi her yerde ayni: kapanmis islemlerin listesi, her biri (cikis zamani UTC,
net PnL USDT; komisyon ve funding PnL'in icinde) + baslangic sermayesi.

Yeniden ornekleme birimi UTC GUNLERI'dir (islemler zamanda kumelenir; tek tek
islem karistirmak varyansi dusuk gosterir). Gun dizisi ilk islem gunuyle son
islem gunu arasindaki TUM takvim gunlerini icerir (islemsiz gunler de dizinin
parcasi, icinde islem yok). Dairesel blok bootstrap: blok uzunlugu gun
cinsinden (varsayilan 3); her gun kendi islemlerini kendi sirasiyla tasir.

Her fonksiyon verilen seed icin deterministiktir (stdlib random.Random).

Bu modul canli islem koduna dokunmaz; yalnizca strateji kararlarini
guvenilir kilmak icin degerlendirme yardimcisidir.

CLI: python -m src.strategies.scalper.montecarlo --source ledger --db <path>
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Sequence

# (cikis zamani, net PnL USDT)
Trade = tuple  # tuple[datetime, float]

MIN_TRADES = 30
MIN_TRADING_DAYS = 10
SIZING_MODES = ("additive", "compounding")


# --------------------------------------------------------------------------
# Gunluk gruplama
# --------------------------------------------------------------------------

def _to_utc(ts: datetime) -> datetime:
    """Naive zaman damgasi UTC kabul edilir; aware olan UTC'ye cevrilir."""
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _utc_day(ts: datetime) -> date:
    return _to_utc(ts).date()


def group_by_day(trades: Iterable[Trade]) -> tuple[list[date], list[list[float]]]:
    """Islemleri UTC gunune gore grupla; bos gunler dahil surekli gun dizisi.

    Gun ici sira, cikis zamanina gore (esitlikte girdi sirasi) korunur.
    """
    ordered = sorted(
        ((ts, pnl) for ts, pnl in trades),
        key=lambda t: _to_utc(t[0]),
    )
    if not ordered:
        return [], []
    by_day: dict[date, list[float]] = {}
    for ts, pnl in ordered:
        by_day.setdefault(_utc_day(ts), []).append(float(pnl))
    first, last = min(by_day), max(by_day)
    days: list[date] = []
    pnls: list[list[float]] = []
    d = first
    while d <= last:
        days.append(d)
        pnls.append(by_day.get(d, []))
        d += timedelta(days=1)
    return days, pnls


def _block_indices(rng: random.Random, n: int, block: int) -> list[int]:
    """Dairesel blok bootstrap indeksleri (uzunluk tam n)."""
    block = max(1, min(int(block), n))
    idx: list[int] = []
    while len(idx) < n:
        s = rng.randrange(n)
        idx.extend((s + k) % n for k in range(block))
    return idx[:n]


# --------------------------------------------------------------------------
# Yuzdelik / ozet
# --------------------------------------------------------------------------

def _percentile(sorted_vals: Sequence[float], q: float) -> float:
    """Dogrusal interpolasyonlu yuzdelik (numpy varsayilani). +-inf siralamada kalir."""
    n = len(sorted_vals)
    if n == 0:
        return float("nan")
    pos = (n - 1) * q / 100.0
    lo = int(math.floor(pos))
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    a, b = sorted_vals[lo], sorted_vals[hi]
    if a == b:
        return a
    if math.isinf(a) or math.isinf(b):
        return b if frac > 0 else a
    return a + (b - a) * frac


def _dist(values: Sequence[float]) -> dict:
    s = sorted(values)
    out = {f"p{q}": _percentile(s, q) for q in (5, 25, 50, 75, 95)}
    out["mean"] = (sum(s) / len(s)) if s and not any(math.isinf(v) for v in s) else (
        float("nan") if not s else (sum(s) / len(s))
    )
    return out


# --------------------------------------------------------------------------
# Tek yol simulasyonu
# --------------------------------------------------------------------------

def simulate_path(
    day_pnls: Sequence[Sequence[float]],
    start_capital: float,
    sizing: str = "additive",
    ruin_level: float | None = None,
    daily_loss_limit_pct: float | None = None,
) -> dict:
    """Bir gun dizisinin esdeger yolunu islem islem yurut.

    sizing="additive": PnL kayitli haliyle eklenir.
    sizing="compounding": her islemin PnL'i (mevcut ozsermaye / baslangic) ile
      olceklenir; yani pozisyon boyutunun ozsermayeyle orantili oldugu varsayilir.
      Ozsermaye sifira inerse daha fazla olcek kalmaz (0'da kilitlenir).

    Donus: final_pnl, max_dd (USDT), max_dd_pct (baslangicin %'si, islem islem),
    longest_losing_streak (art arda PnL<0 islem, gunler arasi surer),
    days_under_water (gun sonu ozsermayesi o ana dek en yuksek gun-sonu
    ozsermayesinin (baslangic dahil) altinda olan gunler), ruined (ozsermaye
    herhangi bir anda <= ruin_level; baslangic dahil), loss_breach_days
    (gun PnL <= -limit% * o gunun basindaki sermaye: additive'de baslangic,
    compounding'de gun basi ozsermaye).
    """
    if sizing not in SIZING_MODES:
        raise ValueError(f"sizing must be one of {SIZING_MODES}")
    start = float(start_capital)
    eq = start
    peak = start
    max_dd = 0.0
    day_peak = start
    streak = 0
    longest = 0
    under = 0
    ruined = ruin_level is not None and eq <= ruin_level
    breaches = 0
    comp = sizing == "compounding"
    for trades in day_pnls:
        day_start_eq = eq
        for pnl in trades:
            if comp:
                eq += pnl * (max(eq, 0.0) / start)
            else:
                eq += pnl
            if eq > peak:
                peak = eq
            elif peak - eq > max_dd:
                max_dd = peak - eq
            if pnl < 0:
                streak += 1
                if streak > longest:
                    longest = streak
            else:
                streak = 0
            if ruin_level is not None and eq <= ruin_level:
                ruined = True
        if daily_loss_limit_pct is not None and trades:
            basis = day_start_eq if comp else start
            if eq - day_start_eq <= -(daily_loss_limit_pct / 100.0) * basis:
                breaches += 1
        if eq > day_peak:
            day_peak = eq
        elif eq < day_peak:
            under += 1
    return {
        "final_pnl": eq - start,
        "max_dd": max_dd,
        "max_dd_pct": 100.0 * max_dd / start,
        "longest_losing_streak": longest,
        "days_under_water": under,
        "ruined": ruined,
        "loss_breach_days": breaches,
    }


def _sample_flag(trades: Sequence[Trade], n_trading_days: int) -> bool:
    return len(trades) < MIN_TRADES or n_trading_days < MIN_TRADING_DAYS


# --------------------------------------------------------------------------
# Esdeger-yol Monte Carlo
# --------------------------------------------------------------------------

def equity_path_monte_carlo(
    trades: Sequence[Trade],
    start_capital: float,
    sizing: str = "additive",
    n_paths: int = 5000,
    block_days: int = 3,
    seed: int = 12345,
    ruin_level: float | None = None,
    daily_loss_limit_pct: float | None = None,
) -> dict:
    """Gun-blok bootstrap ile esdeger-yol dagilimlari.

    ruin_level varsayilani baslangic sermayesinin %50'si. Sonuclar yetersiz
    ornekte de hesaplanir; insufficient_sample bayragi (<30 islem veya <10
    islemli gun) yanlis guven vermesin diye eklenir.
    """
    if ruin_level is None:
        ruin_level = 0.5 * start_capital
    days, day_pnls = group_by_day(trades)
    n_days = len(days)
    n_trading_days = sum(1 for p in day_pnls if p)
    res = {
        "sizing": sizing,
        "n_trades": len(trades),
        "n_days": n_days,
        "n_trading_days": n_trading_days,
        "n_paths": n_paths,
        "block_days": block_days,
        "seed": seed,
        "start_capital": start_capital,
        "ruin_level": ruin_level,
        "daily_loss_limit_pct": daily_loss_limit_pct,
        "insufficient_sample": _sample_flag(trades, n_trading_days),
    }
    if n_days == 0:
        res["empty"] = True
        return res
    rng = random.Random(seed)
    finals, dds, dd_pcts, streaks, unders = [], [], [], [], []
    ruins = 0
    any_breach = 0
    breach_total = 0
    for _ in range(n_paths):
        idx = _block_indices(rng, n_days, block_days)
        r = simulate_path([day_pnls[i] for i in idx], start_capital, sizing,
                          ruin_level, daily_loss_limit_pct)
        finals.append(r["final_pnl"])
        dds.append(r["max_dd"])
        dd_pcts.append(r["max_dd_pct"])
        streaks.append(r["longest_losing_streak"])
        unders.append(r["days_under_water"])
        ruins += 1 if r["ruined"] else 0
        if r["loss_breach_days"] > 0:
            any_breach += 1
        breach_total += r["loss_breach_days"]
    res["final_pnl"] = _dist(finals)
    res["max_drawdown_usdt"] = _dist(dds)
    res["max_drawdown_pct"] = _dist(dd_pcts)
    res["longest_losing_streak"] = _dist(streaks)
    res["days_under_water"] = _dist(unders)
    res["prob_ruin"] = ruins / n_paths
    if daily_loss_limit_pct is not None:
        res["prob_daily_loss_breach"] = any_breach / n_paths
        res["expected_breach_days"] = breach_total / n_paths
    return res


# --------------------------------------------------------------------------
# Guven araliklari
# --------------------------------------------------------------------------

def _stats_from_agg(net: float, n: int, wins: int, win_sum: float,
                    losses: int, loss_sum: float) -> dict:
    """loss_sum negatif toplam (PnL<0 islemlerin). Sifir kayipli PF = +inf."""
    if losses == 0:
        pf = float("inf") if win_sum > 0 else 0.0
    else:
        pf = win_sum / -loss_sum
    if losses == 0:
        wl = float("inf") if wins > 0 else 0.0
    elif wins == 0:
        wl = 0.0
    else:
        wl = (win_sum / wins) / (-loss_sum / losses)
    return {
        "net_pnl": net,
        "profit_factor": pf,
        "win_rate": (wins / n) if n else 0.0,
        "expectancy": (net / n) if n else 0.0,
        "avg_win_loss": wl,
    }


def _day_agg(pnls: Sequence[float]) -> tuple:
    net = sum(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    return (net, len(pnls), len(wins), sum(wins), len(losses), sum(losses))


def trade_statistics(trades: Sequence[Trade]) -> dict:
    """Nokta tahminleri (ayni tanimlarla; CI'larin gozlenen degeri)."""
    _, day_pnls = group_by_day(trades)
    a = [0.0, 0, 0, 0.0, 0, 0.0]
    for p in day_pnls:
        for i, v in enumerate(_day_agg(p)):
            a[i] += v
    return _stats_from_agg(a[0], int(a[1]), int(a[2]), a[3], int(a[4]), a[5])


def bootstrap_confidence_intervals(
    trades: Sequence[Trade],
    n_paths: int = 5000,
    block_days: int = 3,
    seed: int = 12345,
    confidence: float = 0.95,
) -> dict:
    """Yuzdelik bootstrap GA: net PnL, PF, kazanma orani, islem basi beklenti,
    ort. kazanc / ort. kayip. Ayni gun-blok semasi. Kayipsiz yeniornekte PF
    +inf olur ve siralamada kalir (atilmaz).
    """
    days, day_pnls = group_by_day(trades)
    n_days = len(days)
    n_trading_days = sum(1 for p in day_pnls if p)
    out = {
        "n_trades": len(trades),
        "n_days": n_days,
        "n_paths": n_paths,
        "block_days": block_days,
        "seed": seed,
        "confidence": confidence,
        "insufficient_sample": _sample_flag(trades, n_trading_days),
    }
    if n_days == 0:
        out["empty"] = True
        return out
    aggs = [_day_agg(p) for p in day_pnls]
    out["observed"] = trade_statistics(trades)
    rng = random.Random(seed)
    samples: dict[str, list[float]] = {k: [] for k in out["observed"]}
    for _ in range(n_paths):
        a = [0.0, 0, 0, 0.0, 0, 0.0]
        for i in _block_indices(rng, n_days, block_days):
            g = aggs[i]
            for j in range(6):
                a[j] += g[j]
        st = _stats_from_agg(a[0], int(a[1]), int(a[2]), a[3], int(a[4]), a[5])
        for k, v in st.items():
            samples[k].append(v)
    lo_q = (1.0 - confidence) / 2.0 * 100.0
    hi_q = 100.0 - lo_q
    out["ci"] = {}
    for k, v in samples.items():
        s = sorted(v)
        out["ci"][k] = {"lo": _percentile(s, lo_q), "hi": _percentile(s, hi_q)}
    return out


# --------------------------------------------------------------------------
# Eslestirilmis A/B
# --------------------------------------------------------------------------

def paired_ab_test(
    candidate: Sequence[Trade],
    baseline: Sequence[Trade],
    calendar_days: Sequence[date],
    n_paths: int = 5000,
    block_days: int = 3,
    seed: int = 12345,
    confidence: float = 0.95,
) -> dict:
    """Aday vs referans, ayni takvim gunleri uzerinde.

    Istatistik = sum_gun (aday gunluk PnL - referans gunluk PnL); islemsiz gun 0.
    calendar_days disindaki islemler yok sayilir. Donus: gozlenen delta, delta
    icin yuzdelik GA (gunluk farklarin blok yeniornegi), bootstrap std hatasi
    ve H0: delta <= 0 icin tek tarafli p-degeri. p, MERKEZLENMIS gunluk
    farklarin (d - ortalama) blok bootstrap'indan hesaplanir:
    (count(boot >= gozlenen) + 1) / (n + 1).
    """
    # Blok bootstrap takvim sirasini zaman sirasi sayar; tekrarlar fazladan gun olur.
    cal = sorted(set(calendar_days))
    n = len(cal)
    if n == 0:
        raise ValueError("calendar_days must not be empty")
    pos = {d: i for i, d in enumerate(cal)}
    diff = [0.0] * n
    for ts, pnl in candidate:
        i = pos.get(_utc_day(ts))
        if i is not None:
            diff[i] += float(pnl)
    for ts, pnl in baseline:
        i = pos.get(_utc_day(ts))
        if i is not None:
            diff[i] -= float(pnl)
    observed = sum(diff)
    mean = observed / n
    centred = [d - mean for d in diff]
    rng = random.Random(seed)
    boots, boots_c = [], []
    for _ in range(n_paths):
        idx = _block_indices(rng, n, block_days)
        boots.append(sum(diff[i] for i in idx))
        boots_c.append(sum(centred[i] for i in idx))
    m = sum(boots) / n_paths
    se = math.sqrt(sum((b - m) ** 2 for b in boots) / (n_paths - 1)) if n_paths > 1 else 0.0
    ge = sum(1 for b in boots_c if b >= observed)
    lo_q = (1.0 - confidence) / 2.0 * 100.0
    s = sorted(boots)
    return {
        "n_days": n,
        "n_paths": n_paths,
        "block_days": block_days,
        "seed": seed,
        "delta": observed,
        "ci": {"lo": _percentile(s, lo_q), "hi": _percentile(s, 100.0 - lo_q)},
        "std_error": se,
        "p_value": (ge + 1) / (n_paths + 1),
        "insufficient_sample": (len(candidate) < MIN_TRADES or len(baseline) < MIN_TRADES
                                or n < MIN_TRADING_DAYS),
    }


# --------------------------------------------------------------------------
# Yukleyiciler
# --------------------------------------------------------------------------

def load_backtest_report(path: str) -> list[Trade]:
    """Backtest JSON raporundan (trades[].exit_time ms UTC, pnl) islemleri oku."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    out: list[Trade] = []
    for t in data["trades"]:
        ts = datetime.fromtimestamp(t["exit_time"] / 1000.0, tz=timezone.utc)
        out.append((ts, float(t["pnl"])))
    out.sort(key=lambda x: x[0])
    return out


def load_ledger(db_path: str) -> list[Trade]:
    """Canli defter SQLite'tan scalp_trades (status='CLOSED') oku, salt-okunur.

    closed_at UTC kabul edilir (saat dilimsiz metin).
    """
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT closed_at, realized_pnl FROM scalp_trades "
            "WHERE status = 'CLOSED' AND closed_at IS NOT NULL "
            "AND realized_pnl IS NOT NULL ORDER BY closed_at"
        ).fetchall()
    finally:
        con.close()
    out: list[Trade] = []
    for closed_at, pnl in rows:
        ts = datetime.fromisoformat(str(closed_at))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        out.append((ts, float(pnl)))
    return out


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _fmt(v: float) -> str:
    if isinstance(v, float) and math.isinf(v):
        return "inf" if v > 0 else "-inf"
    return f"{v:,.2f}" if isinstance(v, float) else str(v)


def _print_mc(r: dict) -> None:
    print(f"\n== sizing: {r['sizing']} ==")
    if r.get("empty"):
        print("  islem yok")
        return
    for key, label in (("final_pnl", "final net PnL (USDT)"),
                       ("max_drawdown_usdt", "max drawdown (USDT)"),
                       ("max_drawdown_pct", "max drawdown (% baslangic)"),
                       ("longest_losing_streak", "en uzun kayip serisi (islem)"),
                       ("days_under_water", "su altinda gun")):
        d = r[key]
        print(f"  {label:32s} p5 {_fmt(d['p5'])}  p25 {_fmt(d['p25'])}  p50 {_fmt(d['p50'])}"
              f"  p75 {_fmt(d['p75'])}  p95 {_fmt(d['p95'])}  ort {_fmt(d['mean'])}")
    print(f"  ruin olasiligi (<= {_fmt(r['ruin_level'])}): {r['prob_ruin']:.4f}")
    if "prob_daily_loss_breach" in r:
        print(f"  gunluk kayip limiti asimi: P(>=1 gun) {r['prob_daily_loss_breach']:.4f}, "
              f"beklenen gun {r['expected_breach_days']:.2f}")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Esdeger-yol Monte Carlo + bootstrap GA")
    ap.add_argument("--source", choices=("ledger", "backtest"), required=True)
    ap.add_argument("--db", help="canli defter SQLite yolu (ledger)")
    ap.add_argument("--report", help="backtest JSON yolu (backtest)")
    ap.add_argument("--capital", type=float, default=1000.0)
    ap.add_argument("--daily-loss-pct", type=float, default=1.0)
    ap.add_argument("--ruin-pct", type=float, default=50.0,
                    help="ruin seviyesi, baslangic sermayesinin yuzdesi")
    ap.add_argument("--paths", type=int, default=5000)
    ap.add_argument("--block-days", type=int, default=3)
    ap.add_argument("--seed", type=int, default=12345)
    ap.add_argument("--json", action="store_true", help="ham JSON yazdir")
    a = ap.parse_args(argv)
    if a.source == "ledger":
        if not a.db:
            ap.error("--db gerekli")
        trades = load_ledger(a.db)
    else:
        if not a.report:
            ap.error("--report gerekli")
        trades = load_backtest_report(a.report)
    ruin = a.capital * a.ruin_pct / 100.0
    results = [equity_path_monte_carlo(trades, a.capital, m, a.paths, a.block_days,
                                       a.seed, ruin, a.daily_loss_pct)
               for m in SIZING_MODES]
    ci = bootstrap_confidence_intervals(trades, a.paths, a.block_days, a.seed)
    if a.json:
        print(json.dumps({"monte_carlo": results, "ci": ci}, indent=2, default=str))
        return 0
    r0 = results[0]
    print(f"islem {r0['n_trades']}, gun {r0['n_days']} (islemli {r0['n_trading_days']}), "
          f"yol {a.paths}, blok {a.block_days} gun, seed {a.seed}")
    if r0["insufficient_sample"]:
        print("UYARI: yetersiz ornek (<30 islem veya <10 islemli gun)")
    for r in results:
        _print_mc(r)
    if not ci.get("empty"):
        print(f"\n== {int(ci['confidence'] * 100)}% bootstrap GA ==")
        for k, v in ci["ci"].items():
            print(f"  {k:14s} gozlenen {_fmt(ci['observed'][k])}  GA [{_fmt(v['lo'])}, {_fmt(v['hi'])}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
