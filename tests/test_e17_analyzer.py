"""E17 ön-kayıtlı analizci testleri (scripts/e17/analyze_e17.py; PLAN §5.3 'Zorunlu testler').

Hepsi AĞSIZ ve SENTETİKTİR: 2025 (DEV), 2023-2024 (VAL), 2026-09-03..10-08 (FINAL) verisi KULLANILMAZ.
Sentetik raporlar gerçek harness raporunun şeklindedir (overall/provenance/trades/missed_signals/diagnostics).

Hız: tüm dosya ~40 sn altında kalır. Bootstrap üretim fonksiyonu `boot_n` ile (B=399/299) çağrılır; lock
anında bir kez koşan tam boyutlu (B=19 999, 600 seri) boyut testi yalnız `E17_SLOW=1` ile açılır.
"""

from __future__ import annotations

import csv
import datetime as dt
import gzip
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pytest

SCRIPTS_E17 = Path(__file__).resolve().parent.parent / "scripts" / "e17"
if str(SCRIPTS_E17) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_E17))

import analyze_e17 as a  # noqa: E402

from src.strategies.scalper.multitest import benjamini_hochberg  # noqa: E402

INF = float("inf")
DAY = 86_400_000
FAST = {"BOOT_N": 299, "BOOT_N_HI": 599}      # lab kilidi ve komutlar AYNI THRESH ile koşar (thresholds_sha256)


# ==========================================================================
# Sentetik varyant tablosu (gerçek variants.json şeması e17-variants/2'nin küçük kopyası)
# ==========================================================================
_TRI = {"5": ("5m", "30m", "1h"), "15": ("15m", "1h", "4h"), "30": ("30m", "4h", "4h"), "60": ("1h", "4h", "4h")}
_TB = {"5": ("40", "300"), "15": ("120", "900"), "30": ("240", "1800"), "60": ("480", "3600")}
_S = {"5": 5, "15": 9, "30": 13, "60": 16}
_BG = {"SCALPER_REGIME_FILTER": "false", "SCALPER_MARKET_GATE": "false", "SCALPER_ENTRY_BLOCK_WEEKDAYS_UTC": ""}


def _arm(vid: str, tf: str, scaled: bool, role: str = "family", bg: bool = True) -> Dict[str, Any]:
    t = _TRI[tf]
    env = {"SCALPER_TF_ENTRY": t[0], "SCALPER_TF_CONTEXT": t[1], "SCALPER_TF_REGIME": t[2]}
    if bg:
        env.update(_BG)
    env.update({"SCALPER_MAX_HOLD_HOURS": _TB[tf][0], "SCALPER_LOSS_COOLDOWN_MINUTES": _TB[tf][1]})
    if scaled:
        s = _S[tf]
        env.update({"SCALPER_DYN_LEV_STOP_ATR_MULT": "2", "SCALPER_FIXED_STOP_ROI_PCT": str(s),
                    "SCALPER_TP1_ROI": str(s), "SCALPER_TP2_ROI": str(2 * s)})
    return {"id": vid, "role": role, "entry_tf": t[0], "triple": list(t),
            "geometry": f"scaled_S{_S[tf]}" if scaled else "server_geometry",
            "S_arm": _S[tf] if scaled else 50, "env": env}


def make_variants() -> Dict[str, Any]:
    q = [list(x) for x in a.DEV_QUARTERS]
    refs = [
        _arm("S5G", "5", True, "reference_gated", bg=False), _arm("T5G", "5", False, "reference_gated", bg=False),
        {"id": "B0", "role": "reference_live", "entry_tf": "1m", "triple": ["1m", "5m", "15m"],
         "geometry": "server_geometry", "S_arm": 50, "env": {}, "dev_quarters": q},
        {"id": "R1", "role": "reference_bg_1m", "entry_tf": "1m", "triple": ["1m", "5m", "15m"],
         "geometry": "server_geometry", "S_arm": 50, "env": dict(_BG), "dev_quarters": q},
    ]
    refs[0]["env"].pop("SCALPER_TF_ENTRY"), refs[0]["env"].pop("SCALPER_TF_CONTEXT"), refs[0]["env"].pop("SCALPER_TF_REGIME")
    refs[1]["env"].pop("SCALPER_TF_ENTRY"), refs[1]["env"].pop("SCALPER_TF_CONTEXT"), refs[1]["env"].pop("SCALPER_TF_REGIME")
    refs[0]["env"].pop("SCALPER_MAX_HOLD_HOURS"), refs[0]["env"].pop("SCALPER_LOSS_COOLDOWN_MINUTES")
    refs[1]["env"].pop("SCALPER_MAX_HOLD_HOURS"), refs[1]["env"].pop("SCALPER_LOSS_COOLDOWN_MINUTES")
    overlays = [
        {"id": "ROB_FEE2", "env": {}, "cli": ["--fee-stress"]},
        {"id": "ROB_DLY1", "env": {}, "cli": ["--entry-delay-candles", "1"]},
        {"id": "ROB_TAKER", "env": {"SCALPER_ENTRY_MODE": "taker"}, "cli": []},
        {"id": "ROB_WALL", "env": {"SCALPER_MAX_HOLD_HOURS": "8", "SCALPER_LOSS_COOLDOWN_MINUTES": "60"}, "cli": []},
        {"id": "DIAG_Z0", "env": {"SCALPER_MAKER_FEE_PCT": "0", "SCALPER_TAKER_FEE_PCT": "0", "SCALPER_SLIPPAGE_RATE": "0"}, "cli": []},
        {"id": "DIAG_SBO", "env": {}, "cli": ["--same-bar-order", "optimistic"]},
        {"id": "DIAG_FEE15", "env": {}, "cli": ["--fee-stress", "--fee-stress-multiplier", "1.5"]},
    ]
    return {
        "schema": a.VARIANTS_SCHEMA, "symbols_order_pinned": a.SYMBOLS_PINNED,
        "family": [_arm("S5", "5", True), _arm("T5", "5", False), _arm("S15", "15", True),
                   _arm("T15", "15", False), _arm("S30", "30", True), _arm("T30", "30", False)],
        "descriptive": [_arm("S60", "60", True, "descriptive"), _arm("T60", "60", False, "descriptive")],
        "references": refs, "overlays": overlays,
        "sets": {k: list(v) for k, v in a.WINDOWS.items()},
    }


def stub_ctx(tmp: Path, h: str = "a" * 40) -> "a.Ctx":
    return a.Ctx(root=tmp, l="l" * 40, h=h, lock={}, variants=make_variants(), labels={})


# ==========================================================================
# Sentetik BTC 1d (rejim etiketleri) ve sentetik rapor üreticisi
# ==========================================================================
DEV_RETS = [9.4, -17.7, -2.1, 14.1, 11.1, 2.4, 8.0, -6.5, 5.3, -3.9, -17.6, -3.0]       # 5 UP / 4 FLAT / 3 DOWN
_VAL_PAT = ["UP", "UP", "FLAT", "DOWN", "UP", "FLAT", "UP", "DOWN", "UP", "FLAT", "UP", "UP", "DOWN",
            "FLAT", "UP", "UP", "FLAT", "DOWN", "UP", "DOWN", "UP", "FLAT"]               # 11 / 6 / 5
_RET_OF = {"UP": 7.0, "FLAT": 1.0, "DOWN": -7.0}


def synthetic_btc(dev_rets: Sequence[float] = tuple(DEV_RETS), val_pat: Sequence[str] = tuple(_VAL_PAT),
                  partial: str = "DOWN") -> Dict[int, Dict[str, Any]]:
    by_open: Dict[int, Dict[str, Any]] = {}
    price = 100.0

    def month(key: str, a_: dt.date, b_: dt.date, ret: float) -> None:
        nonlocal price
        days = (b_ - a_).days
        for i in range(days):
            t = a.date_ms(a_.isoformat()) + i * DAY
            o = price
            c = price * (1.0 + ret / 100.0) if i == days - 1 else price
            by_open[t] = {"open_time": t, "open": o, "high": max(o, c), "low": min(o, c), "close": c, "volume": 1.0}
        price = price * (1.0 + ret / 100.0)

    dev = a._months_in(*a.WINDOWS["DEV"])
    for (key, a_, b_, _p), r in zip(dev, dev_rets):
        month(key, a_, b_, r)
    val = a._months_in(*a.WINDOWS["VAL"])
    for i, (key, a_, b_, p) in enumerate(val):
        month(key, a_, b_, _RET_OF[partial] if p else _RET_OF[val_pat[i]])
    return by_open


def write_btc_cache(cache: Path, by_open: Dict[int, Dict[str, Any]]) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    ts = sorted(by_open)
    # iki ayrı dosyaya böl (birleştirme yolunu da sına)
    mid = len(ts) // 2
    for part, name in ((ts[:mid], "BTCUSDT_1d_1_2.json.gz"), (ts[mid:], "BTCUSDT_1d_3_4.json.gz")):
        with gzip.open(cache / name, "wt", encoding="utf-8") as f:
            json.dump([by_open[t] for t in part], f)


def typed(v: str) -> Any:
    if v in ("true", "false"):
        return v == "true"
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v


def gen_trades(window: Sequence[str], n: int, seed: int, *, pw: float = 0.62, w: float = 1.2, loss: float = 1.03,
               lev: int = 10, eod: int = 0, s_arm: float = 9.0, margin: float = 1000.0,
               lev_mix: Optional[Dict[int, float]] = None) -> List[Dict[str, Any]]:
    """Deterministik sentetik işlemler. R = pw olasılıkla +w, aksi halde -loss; gün başına eşit yayılım."""
    rng = random.Random(seed)
    start = a.date_ms(window[0])
    days = (a.date_ms(window[1]) - start) // DAY
    syms = a.SYMBOLS_PINNED.split(",")
    risk = margin * s_arm / 100.0
    out = []
    for i in range(n):
        d = min(days - 1, int(i * days / n))
        entry = start + d * DAY + 3 * 3_600_000
        r = w if rng.random() < pw else -loss
        lv = lev
        if lev_mix:
            u, acc = rng.random(), 0.0
            for k, share in lev_mix.items():
                acc += share
                if u < acc:
                    lv = k
                    break
        out.append({"strategy": "C", "symbol": syms[i % 8], "direction": "LONG" if i % 2 == 0 else "SHORT",
                    "entry_time": entry, "exit_time": entry + 2 * 3_600_000, "margin_usdt": margin,
                    "pnl": r * risk, "leverage": lv, "roi_pct": r, "exit_reason": "EOD" if i >= n - eod else ("TRAIL" if r > 0 else "SL"),
                    "entry_price": 1.0, "exit_price": 1.0, "quantity": 1.0, "mae_pct": 0.0, "mfe_pct": 0.0,
                    "duration_minutes": 120.0, "regime": "RANGE", "legs": []})
    return out


def make_report(ctx: "a.Ctx", label: str, trades: List[Dict[str, Any]], *, git_sha: Optional[str] = None,
                dirty: bool = False, dd_r: float = 5.0, missed: Optional[Dict[str, int]] = None,
                diagnostics: bool = True, drop: Sequence[str] = ()) -> Dict[str, Any]:
    info = a.parse_label(label, ctx.variants)
    v = ctx.vid[info.variant]
    start, end = a.run_window(ctx, info)
    s_ms, e_ms = a.date_ms(start), a.date_ms(end)
    days = (e_ms - s_ms) // DAY
    cfg = {"scalper_tf_entry": v["triple"][0], "scalper_tf_context": v["triple"][1], "scalper_tf_regime": v["triple"][2],
           "scalper_junk_key": 1, "scalper_fixed_stop_roi_pct": float(v["S_arm"])}
    for k, val in a.expected_env(ctx, info).items():
        cfg[k.lower()] = typed(val)
    stress, delay = a.expected_cost(info)
    dw = {}
    for sym in a.SYMBOLS_PINNED.split(","):
        dw[sym] = {iv: {"candles_fetched": days * a.CANDLES_PER_DAY[iv] + 10,
                        "candles_in_test_window": days * a.CANDLES_PER_DAY[iv],
                        "fetched_start_ms": s_ms - 10 * a.INTERVAL_MS[iv], "fetched_end_ms": e_ms - 1}
                   for iv in set(v["triple"])}
    pnls = [t["pnl"] for t in trades]
    pf = a.pf_of(pnls)
    risk = (trades[0]["margin_usdt"] * v["S_arm"] / 100.0) if trades else 1.0
    doc: Dict[str, Any] = {
        "generated_at": "2026-10-10T00:00:00Z",
        "params": {"days": days, "symbols": a.SYMBOLS_PINNED.split(","), "strategies": "C"},
        "provenance": {
            "git_sha": git_sha or ctx.h, "git_dirty": dirty, "scalper_config": cfg,
            "test_window": {"requested_days": days, "start_ms": s_ms, "end_ms": e_ms},
            "universe_snapshot": {"symbols": a.SYMBOLS_PINNED.split(",")},
            "data_windows": dw,
            "cost_model": {"stress_multiplier": stress, "entry_delay_candles": delay, "taker_fee_pct": 0.05},
            "sim_options": {"diagnostics": True, "same_bar_order": "optimistic" if info.overlay == "DIAG_SBO" else "pessimistic"},
        },
        "overall": {"trades": len(trades), "wins": sum(1 for p in pnls if p > 0),
                    "winrate": (sum(1 for p in pnls if p > 0) / len(trades) * 100.0) if trades else 0.0,
                    "total_pnl": sum(pnls), "profit_factor": None if pf == INF else pf,
                    "bar_max_drawdown": dd_r * risk},
        "missed_signals": dict(missed if missed is not None else {"regime_gate": 0, "maker_missed": 3}),
        "permutation": None,
        "trades": trades,
    }
    if diagnostics:
        doc["diagnostics"] = {k: 1 for k in a.DIAG_KEYS}
    for d in drop:
        cur = doc
        parts = d.split(".")
        for p in parts[:-1]:
            cur = cur[p]
        cur.pop(parts[-1], None)
    return doc


def write_smoke_pass(root: Path, ctx: "a.Ctx", ok: bool = True) -> None:
    """DEV aşamasının ön koşulu: bu kilit için SMOKE.json PASS."""
    (root / "SMOKE.json").write_text(json.dumps({"pass": ok, "l": ctx.l}), encoding="utf-8")


def put_run(root: Path, ctx: "a.Ctx", label: str, trades: List[Dict[str, Any]], *, ledger: bool = True, log: str = "ok\n",
            **kw: Any) -> Dict[str, Any]:
    doc = make_report(ctx, label, trades, **kw)
    rd = root / "sealed" / "runs"
    ld = root / "sealed" / "logs"
    rd.mkdir(parents=True, exist_ok=True)
    ld.mkdir(parents=True, exist_ok=True)
    p = rd / f"{label}.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    (ld / f"{label}.log").write_text(log, encoding="utf-8")
    if ledger:
        a.Ledger(root).append({"label": label, "utc": "2026-10-10T00:00:00Z", "seconds": 1.0,
                               "sha256": a.sha256_file(p), "git_sha": doc["provenance"]["git_sha"], "bytes": p.stat().st_size})
    return doc


# ==========================================================================
# Sentetik depo + kilit (lab)
# ==========================================================================
def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


class Lab:
    def __init__(self, base: Path):
        self.base = base
        self.repo = base / "repo"
        self.root = base / "root"

    def snap(self, name: str, root: Optional[Path] = None) -> Path:
        dst = self.base / f"snap_{name}"
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(root or self.root, dst)
        return dst

    def clone_root(self, name: str, tmp: Path) -> Path:
        dst = tmp / f"root_{name}"
        shutil.copytree(self.base / f"snap_{name}", dst)
        return dst


def build_lab(base: Path, mp: pytest.MonkeyPatch) -> Lab:
    lab = Lab(base)
    repo, root = lab.repo, lab.root
    for rel, content in {
        "requirements.txt": "pytest==0.0\n",
        "src/strategies/scalper/backtest.py": "# dummy\n", "src/strategies/scalper/setups.py": "# dummy\n",
        "src/core/config.py": "# dummy\n", "scripts/fill_klines_cache_from_vision.py": "# dummy\n",
        "tests/test_e17_analyzer.py": "# dummy\n", "README.md": "x\n",
        ".gitignore": "*.json\n",       # gerçek depo .gitignore'u gibi: lock -f olmadan JSON'ları SESSİZCE atlardı
    }.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    (repo / "scripts" / "e17").mkdir(parents=True, exist_ok=True)
    shutil.copy(SCRIPTS_E17 / "analyze_e17.py", repo / "scripts" / "e17" / "analyze_e17.py")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "H")
    ld = repo / a.LOCKDIR_REL
    ld.mkdir(parents=True, exist_ok=True)
    (ld / "PLAN.md").write_text("plan\n", encoding="utf-8")
    (ld / "variants.json").write_text(json.dumps(make_variants()), encoding="utf-8")
    (ld / "cal_constants.json").write_text("{}", encoding="utf-8")
    (ld / "archive_probe.json").write_text("{}", encoding="utf-8")
    (ld / "evidence").mkdir()
    (ld / "evidence" / "e.txt").write_text("ev\n", encoding="utf-8")
    root.mkdir(parents=True, exist_ok=True)
    (root / "server-scalper-env.txt").write_text("SCALPER_DUMMY=1\n", encoding="utf-8")
    mp.setattr(a, "REPO", repo)
    mp.setattr(a, "SELF_PATH", repo / "scripts" / "e17" / "analyze_e17.py")
    # freeze (BTC 1d sentetik) -> labels_frozen.json, sonra lock
    write_btc_cache(root / "cache", synthetic_btc())
    assert a.main(["freeze", "--root", str(root)]) == 0
    assert a.main(["lock", "--root", str(root)]) == 0
    return lab


@pytest.fixture
def lab_env(monkeypatch, request):
    """Lab'ın REPO/SELF_PATH yamaları + lab kilidinin içine gömülü hızlı THRESH (fonksiyon kapsamlı)."""
    lb = request.getfixturevalue("lab")
    for k, v in FAST.items():
        monkeypatch.setitem(a.THRESH, k, v)
    monkeypatch.setattr(a, "REPO", lb.repo)
    monkeypatch.setattr(a, "SELF_PATH", lb.repo / "scripts" / "e17" / "analyze_e17.py")
    return monkeypatch


# ==========================================================================
# 1) Parser: yalnız --root ve aşama adı (+hedef); istatistik bayrağı YOK
# ==========================================================================
def test_parser_surface_is_locked():
    p = a.build_parser()
    opts = {s for act in p._actions for s in act.option_strings}
    assert opts == {"-h", "--help", "--root"}
    positionals = [act.dest for act in p._actions if not act.option_strings]
    assert positionals == ["stage", "target"]
    assert len(p._actions) == 4            # fazlası (--n-boot/--seed/--alpha ...) kırmızı
    stage_action = next(act for act in p._actions if act.dest == "stage")
    assert set(stage_action.choices) == {"freeze", "lock", "verify-lock", "manifest", "smoke", "check-run",
                                         "dev-screen", "dev-decide", "diag", "val", "final", "decide"}


@pytest.mark.parametrize("argv", [["dev-screen", "--n-boot", "5"], ["decide", "--seed", "1"], ["bogus"], []])
def test_parser_rejects_stat_flags_exit_2(argv):
    with pytest.raises(SystemExit) as ei:
        a.main(argv)
    assert ei.value.code == 2


def test_usage_errors_exit_2(tmp_path):
    assert a.main(["check-run", "--root", str(tmp_path)]) == 2            # etiketsiz
    assert a.main(["manifest", "--root", str(tmp_path)]) == 2             # hedefsiz


# ==========================================================================
# 2) İstatistik: günlük vektör, R dönüşümü, merkezleme, determinizm, null boyutu
# ==========================================================================
def _tr(r, sym="BTCUSDT", month="2025-03", direction="LONG", lev=10, reason="TRAIL", entry=0, risk=100.0,
        day=dt.date(2025, 3, 5)):
    return a.Tr(symbol=sym, direction=direction, entry_ms=entry, exit_ms=entry + 1, r=r, pnl=r * risk, lev=lev,
                reason=reason, month=month, exit_day=day, risk=risk)


def test_r_conversion_uses_margin_and_s_arm():
    doc = {"trades": [{"symbol": "ETHUSDT", "direction": "LONG", "entry_time": a.date_ms("2025-02-03") + 5,
                       "exit_time": a.date_ms("2025-02-04") + 7, "margin_usdt": 1000.0, "pnl": 45.0, "leverage": 12,
                       "exit_reason": "TRAIL"}]}
    trs, med = a.trades_from_report(doc, 9.0, "x")
    assert med == 90.0 and trs[0].r == pytest.approx(0.5)
    assert trs[0].month == "2025-02" and trs[0].exit_day == dt.date(2025, 2, 4)      # ay: GİRİŞ; gün: ÇIKIŞ


def test_daily_vector_by_exit_day_zero_fill_and_out_of_window():
    w = [(dt.date(2025, 1, 1), 5)]
    ts = [_tr(1.0, day=dt.date(2025, 1, 2)), _tr(0.5, day=dt.date(2025, 1, 2)), _tr(-2.0, day=dt.date(2025, 1, 5))]
    assert a.daily_vector(ts, w) == [0.0, 1.5, 0.0, 0.0, -2.0]
    with pytest.raises(a.IntegrityError):
        a.daily_vector([_tr(1.0, day=dt.date(2025, 1, 6))], w)
    # çeyrek birleştirme: iki pencere uç uca
    w2 = [(dt.date(2025, 1, 1), 2), (dt.date(2025, 4, 1), 2)]
    assert a.daily_vector([_tr(1.0, day=dt.date(2025, 4, 2))], w2) == [0.0, 0.0, 0.0, 1.0]


def test_seed_scheme_is_sha256_first_8_bytes():
    want = int.from_bytes(hashlib.sha256(b"E17|dev|S15|b7").digest()[:8], "big")
    assert a.seed_for("dev", "S15", 7) == want
    assert a.seed_for("dev", "S15", 7, hi=True) == int.from_bytes(hashlib.sha256(b"E17|dev|S15|b7|hi").digest()[:8], "big")
    assert a.seed_for("dev", "S15", 28) != want


@pytest.mark.parametrize("d,block", [(40, 7), (41, 7), (41, 28), (120, 7)])
def test_prefix_bootstrap_equals_naive_concatenation(d, block):
    rng = random.Random(d + block)
    x = [rng.gauss(0.05, 1.0) if rng.random() < 0.7 else 0.0 for _ in range(d)]
    p_fast, _t = a.bootstrap_p(x, block, 123, 300)
    assert p_fast == a.bootstrap_p_naive(x, block, 123, 300)


def test_bootstrap_p_is_deterministic_for_a_given_seed():
    rng = random.Random(1)
    x = [rng.gauss(0.1, 1.0) for _ in range(90)]
    p1 = a.bootstrap_p(x, 7, a.seed_for("dev", "S15", 7), 399)
    assert p1 == a.bootstrap_p(x, 7, a.seed_for("dev", "S15", 7), 399)
    # farklı blok uzunluğu / aynı tohum farklı akış üretir ama yine belirlenimcidir
    assert a.bootstrap_p(x, 28, a.seed_for("dev", "S15", 28), 399) == a.bootstrap_p(x, 28, a.seed_for("dev", "S15", 28), 399)


def test_locked_statistical_constants():
    assert a.THRESH["BOOT_N"] == 19999 and a.THRESH["BOOT_N_HI"] == 199999
    assert (a.THRESH["BLOCK_PRIMARY"], a.THRESH["BLOCK_SECONDARY"], a.THRESH["BLOCK_INFO"]) == (7, 28, 14)
    assert a.THRESH["MC_SE_MULT"] == 3.0 and a.THRESH["G3_Q_MAX"] == 0.20


def test_refine_bh_recomputes_only_the_arm_whose_neighbourhood_can_flip_a_decision():
    calls: List[int] = []
    ps = [0.001, 0.066, 0.9, 1.0, 1.0, 1.0]
    valid = [True, True, True, False, False, False]
    new, refined = a.refine_bh(ps, valid, lambda i: calls.append(i) or ps[i])
    # rank 2/6 için BH eşiği p = 0.0667: 0.066 ± 3 SE (B=19999) bu eşiği keser -> karar dönebilir
    assert refined[1] is True and refined[0] is False and refined[3] is False
    assert calls == [1] and new == ps


def test_null_centering_zero_mean_gives_p_near_half():
    rng = random.Random(3)
    half = [rng.gauss(0, 1) for _ in range(60)]
    x = half + [-v for v in half]                       # ortalama TAM sıfır -> t_obs = 0
    p, t = a.bootstrap_p(x, 7, 99, 1999)
    assert abs(t) < 1e-9 and 0.40 < p < 0.60


def test_bootstrap_detects_real_edge_and_degenerate_is_one():
    rng = random.Random(4)
    x = [rng.gauss(0.6, 1.0) for _ in range(200)]
    assert a.bootstrap_p(x, 7, 5, 399)[0] <= 0.01
    assert a.bootstrap_p([0.0] * 50, 7, 5, 399)[0] == 1.0         # sd = 0
    assert a.bootstrap_p([], 7, 5, 399)[0] == 1.0                 # işlem yok
    assert a.bootstrap_p([1.0, 1.0, 1.0], 7, 5, 399)[0] == 1.0


def test_null_size_fast_1000_series_boot399_d120():
    """§5.3: üretim fonksiyonuyla 1000 simüle null seri x boot_n=399, D=120; red oranı <= 0.078 @ alfa 0.05."""
    rng = random.Random(20261010)
    rej = 0
    for i in range(1000):
        x = [rng.gauss(0, 1.5) if rng.random() < 0.7 else 0.0 for _ in range(120)]      # işlemsiz günler 0
        p, _t = a.bootstrap_p(x, 7, a.seed_for("null", f"s{i}", 7), 399)
        rej += p <= 0.05
    assert rej / 1000 <= 0.078, rej / 1000


@pytest.mark.skipif(os.environ.get("E17_SLOW") != "1", reason="tam boyut (B=19999, 600 seri): yalnız E17_SLOW=1")
def test_null_size_full_b19999_600_series(tmp_path):
    """Lock anında BİR KEZ koşar (E17_SLOW=1); sonuç JSON olarak yazılır (E17_SLOW_OUT, yoksa tmp_path) —
    orkestratör bu dosyayı `evidence/` altına kopyalar ve lock'tan önce hash'lenir (PLAN §5.3)."""
    rng = random.Random(77)
    rej = 0
    n_series, d, alpha = 600, 365, 0.05
    for i in range(n_series):
        x = [rng.gauss(0, 1.5) if rng.random() < 0.7 else 0.0 for _ in range(d)]
        p, _t = a.bootstrap_p(x, 7, a.seed_for("null", f"f{i}", 7), a.THRESH["BOOT_N"])
        rej += p <= alpha
    limit = alpha + 4 * (alpha * (1 - alpha) / n_series) ** 0.5
    out = Path(os.environ.get("E17_SLOW_OUT") or (tmp_path / "null_size_full.json"))
    out.write_text(json.dumps({"test": "null_size_full_b19999_600_series", "boot_n": a.THRESH["BOOT_N"],
                               "block": 7, "series": n_series, "days": d, "alpha": alpha, "rejections": rej,
                               "rejection_rate": rej / n_series, "limit": limit, "pass": rej / n_series <= limit,
                               "thresholds_sha256": a.thresholds_sha256(), "utc": a.utc_now()}, indent=2) + "\n",
                   encoding="utf-8")
    assert rej / n_series <= limit, (rej / n_series, str(out))


def test_mc_error_rule_recomputes_only_when_decision_can_flip():
    calls = []
    p, rec = a.refine_p(0.0495, lambda p: p <= 0.05, lambda: calls.append(1) or 0.0481)
    assert rec and calls and p == 0.0481                              # 0.0495 ± 3·SE aralığı 0.05'i kesiyor
    p2, rec2 = a.refine_p(0.30, lambda p: p <= 0.05, lambda: calls.append(1) or 0.0)
    assert not rec2 and p2 == 0.30 and len(calls) == 1
    # SE çarpanı THRESH'te: 0 yapılırsa hiç yeniden hesap olmaz
    a.THRESH["MC_SE_MULT"], old = 0.0, a.THRESH["MC_SE_MULT"]
    try:
        assert a.refine_p(0.0495, lambda p: p <= 0.05, lambda: 0.0)[1] is False
    finally:
        a.THRESH["MC_SE_MULT"] = old


@pytest.mark.parametrize("ps", [[0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205, 0.212, 0.216],
                                [0.5, 0.5, 0.5, 0.01, 1.0, 0.0],
                                [0.03, 0.04, 0.2, 0.9, 0.0123, 0.2]])
def test_bh_parity_with_multitest(ps):
    theirs = benjamini_hochberg(ps, alpha=0.20)
    q = a.bh_qvalues(ps)
    assert [round(x, 6) for x in q] == [r["q_value"] for r in theirs]
    assert [x <= 0.20 for x in q] == [r["rejected"] for r in theirs]


def test_pf_helpers_inf_cap_and_ci():
    assert a.pf_of([1.0, 2.0]) == INF and a.pf_of([]) == 0.0 and a.pf_of([-1.0]) == 0.0
    assert a.cap_pf(INF) == 5.0 and a.cap_pf(1.3) == 1.3
    assert a.pf_of([2.0, -1.0, 1.0]) == pytest.approx(3.0)
    pos = [1.0 if i % 3 == 0 else 0.0 for i in range(60)]
    neg = [-1.0 if i % 3 == 1 else 0.0 for i in range(60)]
    lo, hi = a.pf_confidence(pos, neg, 7, 11, 399)
    assert 0.0 <= lo <= 1.0 <= hi <= 5.0


def test_pf_printing_four_decimals_and_borderline_marker():
    assert a.fmt_pf(1.00434) == "1.0043" and a.fmt_pf(INF).startswith("5.0000")
    r = a.rule_g2(1.1499)
    assert r["borderline"] is True and not r["pass"] and "SINIRDA" in a.fmt_row(r) and "1.149900" in a.fmt_row(r)
    assert a.rule_g2(1.30)["borderline"] is False


def test_null_pf_is_inf_capped_and_check_accepts_null_only_when_inf():
    ctx = stub_ctx(Path("."))
    label = "DEV_S15"
    info = a.parse_label(label, ctx.variants)
    wins = gen_trades(a.WINDOWS["DEV"], 10, 1, pw=1.0)               # kayıp yok -> PF = inf -> JSON null
    doc = make_report(ctx, label, wins)
    assert doc["overall"]["profit_factor"] is None
    assert a.check_report(ctx, info, doc, "ok") == []
    assert a.cap_pf(a.pf_of([t["pnl"] for t in wins])) == a.THRESH["PF_CAP"]
    mixed = gen_trades(a.WINDOWS["DEV"], 10, 1, pw=0.5)
    doc2 = make_report(ctx, label, mixed)
    doc2["overall"]["profit_factor"] = None                           # sonlu PF'in null yazılması tutarsızlık
    assert any("profit_factor" in r for r in a.check_report(ctx, info, doc2, "ok"))


# ==========================================================================
# 3) Rejim etiketleri (freeze)
# ==========================================================================
def test_regime_rule_boundaries():
    assert a.regime_label(5.0) == "UP" and a.regime_label(4.99) == "FLAT"
    assert a.regime_label(-5.0) == "DOWN" and a.regime_label(-4.99) == "FLAT"


def test_build_labels_reproduces_dev_5_4_3_and_val_11_6_5_and_partial_month_excluded():
    doc = a.build_labels(synthetic_btc())
    assert doc["counts"]["DEV"]["UP"] == 5 and doc["counts"]["DEV"]["FLAT"] == 4 and doc["counts"]["DEV"]["DOWN"] == 3
    assert (doc["counts"]["VAL"]["UP"], doc["counts"]["VAL"]["FLAT"], doc["counts"]["VAL"]["DOWN"]) == (11, 6, 5)
    assert doc["counts"]["VAL"]["months"] == 22 and doc["counts"]["VAL"]["partial_months"] == 1
    assert doc["2024-11"]["partial"] is True and doc["2024-11"]["label"] == "DOWN"       # kısmi ay etiketlenir, sayılmaz
    assert doc["2025-02"]["label"] == "DOWN" and doc["2025-02"]["shock"] is True
    assert doc["2025-01"]["ret_pct"] == pytest.approx(9.4)


def test_build_labels_wrong_counts_or_gaps_stop():
    bad = list(DEV_RETS)
    bad[0] = 0.5                                               # UP -> FLAT: 4/5/3
    with pytest.raises(a.IntegrityError, match="DEV"):
        a.build_labels(synthetic_btc(dev_rets=bad))
    by = synthetic_btc()
    del by[a.date_ms("2025-06-15")]
    with pytest.raises(a.IntegrityError, match="eksik"):
        a.build_labels(by)


def test_freeze_writes_once_and_nothing_on_mismatch(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(a, "REPO", repo)
    root = tmp_path / "root"
    bad = list(DEV_RETS)
    bad[0] = 0.5
    write_btc_cache(root / "cache", synthetic_btc(dev_rets=bad))
    assert a.main(["freeze", "--root", str(root)]) == 5
    assert not (repo / a.LOCKDIR_REL / "labels_frozen.json").exists()
    shutil.rmtree(root / "cache")
    write_btc_cache(root / "cache", synthetic_btc())
    assert a.main(["freeze", "--root", str(root)]) == 0
    assert a.main(["freeze", "--root", str(root)]) == 4          # ikinci dondurma reddedilir
    lab = json.loads((repo / a.LOCKDIR_REL / "labels_frozen.json").read_text())
    assert lab["counts"]["DEV"]["UP"] == 5 and "rule" in lab


def test_entry_month_assignment_uses_fill_bar_not_exit():
    t = {"symbol": "BTCUSDT", "direction": "LONG", "entry_time": a.date_ms("2025-01-31") + 23 * 3_600_000,
         "exit_time": a.date_ms("2025-02-01") + 2 * 3_600_000, "margin_usdt": 1000.0, "pnl": 1.0, "leverage": 10,
         "exit_reason": "TRAIL"}
    tr, _ = a.trades_from_report({"trades": [t]}, 9.0, "x")
    assert tr[0].month == "2025-01" and tr[0].exit_day == dt.date(2025, 2, 1)


# ==========================================================================
# 4) Yoğunlaşma / kaldırma fonksiyonları
# ==========================================================================
def test_concentration_and_leave_one_out_functions():
    ts = [_tr(5.0, "BTCUSDT", "2025-01", entry=1), _tr(-1.0, "BTCUSDT", "2025-01", entry=2),
          _tr(2.0, "ETHUSDT", "2025-02", entry=3), _tr(-1.0, "ETHUSDT", "2025-02", entry=4),
          _tr(1.0, "SOLUSDT", "2025-03", entry=5), _tr(-2.0, "SOLUSDT", "2025-03", entry=6)]
    assert a.best_month(ts) == "2025-01" and a.best_symbol(ts) == "BTCUSDT"
    assert a.pf_excluding_month(ts, "2025-01") == pytest.approx(3.0 / 3.0)
    assert a.pf_excluding_symbol(ts, "BTCUSDT") == pytest.approx(1.0)
    assert [t.r for t in a.top_trades(ts, 2)] == [5.0, 2.0]
    assert a.sum_excluding_top(ts, 2) == pytest.approx(-3.0)
    loo = a.loo_symbol_pfs(ts)
    assert loo["BTCUSDT"] == pytest.approx(1.0) and loo["ETHUSDT"] == pytest.approx(2.0) and loo["SOLUSDT"] == pytest.approx(3.5)
    assert a.pf_excluding_symbol([_tr(1.0)], "BTCUSDT") == 0.0                 # boş küme PF = 0


def test_regime_groups_and_month_table():
    labels = {"2025-01": "UP", "2025-02": "DOWN"}
    ts = [_tr(1.0, month="2025-01"), _tr(-1.0, month="2025-02"), _tr(2.0, month="2025-09")]
    g = a.regime_groups(ts, labels)
    assert len(g["UP"]) == 1 and len(g["DOWN"]) == 1 and g["FLAT"] == []
    mt = a.month_table(ts, labels)
    assert mt["2025-09"]["label"] == "NA" and mt["2025-01"]["sum_r"] == 1.0


def test_fixed_risk_check_and_silent_drop_definition():
    ts = [_tr(1.0, risk=100.0, entry=i) for i in range(20)]
    assert a.risk_band_share(ts) == 1.0
    ratio, silent, ham = a.silent_ratio({"capacity": 3, "maker_missed": 2, "min_rr_rejected": 1, "regime_gate": 99,
                                         "stop_band_low": 2, "qty_zero": 2}, 20)
    assert (silent, ham) == (4, 20 + 3 + 2 + 1 + 4) and ratio == pytest.approx(4 / 30)      # rejim/market kapısı ham sinyal DEĞİL


# ==========================================================================
# 5) Kapı eşikleri: THRESH mutasyon testleri (her kapı için eşiğin hemen altı/üstü)
# ==========================================================================
def _lab_trades(spec: Sequence[tuple]) -> List["a.Tr"]:
    """spec: (r, symbol, month) listesi -> Tr listesi (benzersiz giriş zamanı)."""
    return [_tr(r, sym, mon, entry=i + 1) for i, (r, sym, mon) in enumerate(spec)]


def _gpass(rows_or_entry, gid=None):
    rows = rows_or_entry["rows"] if isinstance(rows_or_entry, dict) and "rows" in rows_or_entry else [rows_or_entry]
    if gid is None:
        return all(r["pass"] for r in rows)
    return next(r for r in rows if r["gate"] == gid)["pass"]


def _validity(ts, missed=None, scaled=True, dev=True):
    return a.entry(a.rule_validity("G0", ts, missed or {}, scaled, dev))


def _n(k, **kw):
    return [_tr(1.0, entry=i + 1, **kw) for i in range(k)]


def _series(sum_r_total, dd_usd, days=365, risk=100.0):
    ts = [_tr(sum_r_total / 10, risk=risk, entry=i + 1) for i in range(10)]
    return a.Series(label="x", arm="S15", set="VAL", overlay="", s_arm=9.0, trades=ts, windows=[(dt.date(2023, 1, 1), days)],
                    missed={}, dd_usd=dd_usd, risk_median=risk)


def _pf_trades(n_win, win_r, n_loss, loss_r=-1.0, month="2025-01", sym="BTCUSDT"):
    spec = [(win_r, sym, month)] * n_win + [(loss_r, sym, month)] * n_loss
    return _lab_trades(spec)


_LABELS = {"2025-01": "UP", "2025-02": "FLAT", "2025-03": "DOWN"}
_M22 = [f"2023-{m:02d}" for m in range(1, 13)] + [f"2024-{m:02d}" for m in range(1, 11)]


def _g12_row(trades, gid):
    return _gpass(a.rule_g12(trades), gid)


def _g12_cases():
    A, B = "BTCUSDT", "ETHUSDT"
    return {
        "G12_BEST_SYMBOL_SHARE_MAX": (0.6,
            lambda: _g12_row(_lab_trades([(5.1, A, "m1"), (4.9, B, "m2")]), "G12a"),
            lambda: _g12_row(_lab_trades([(5.0, A, "m1"), (5.0, B, "m2")]), "G12a")),
        "G12_BEST_MONTH_SHARE_MAX": (0.4,
            lambda: _g12_row(_lab_trades([(3.1, A, "m1"), (2.9, B, "m2"), (2.0, A, "m3"), (2.0, B, "m4")]), "G12b"),
            lambda: _g12_row(_lab_trades([(3.0, A, "m1"), (3.0, B, "m2"), (2.0, A, "m3"), (2.0, B, "m4")]), "G12b")),
        "G12_BEST_TRADE_SHARE_MAX": (0.2,
            lambda: _g12_row(_lab_trades([(1.6, A, "m1")] + [(1.05, B, "m2")] * 8), "G12c"),
            lambda: _g12_row(_lab_trades([(1.5, A, "m1")] + [(1.0625, B, "m2")] * 8), "G12c")),
        "G12_BEST5_SHARE_MAX": (0.5,
            lambda: _g12_row(_lab_trades([(1.0, A, "m1")] * 5 + [(0.9, B, "m2")] * 8), "G12d"),
            lambda: _g12_row(_lab_trades([(1.0, A, "m1")] * 5 + [(0.9375, B, "m2")] * 8), "G12d")),
        "G12_PF_EX_SYMBOL_MIN": (1.0,
            lambda: _g12_row(_lab_trades([(10.0, A, "m1"), (1.04, B, "m2"), (-1.0, B, "m2")]), "G12e"),
            lambda: _g12_row(_lab_trades([(10.0, A, "m1"), (1.05, B, "m2"), (-1.0, B, "m2")]), "G12e")),
        "G12_PF_EX_MONTH_MIN": (1.0,
            lambda: _g12_row(_lab_trades([(10.0, A, "m1"), (1.04, A, "m2"), (-1.0, A, "m2")]), "G12f"),
            lambda: _g12_row(_lab_trades([(10.0, A, "m1"), (1.05, A, "m2"), (-1.0, A, "m2")]), "G12f")),
        "G12_LOO_SYMBOL_PF_MIN": (0.95,
            lambda: _g12_row(_lab_trades([(2.0, A, "m1"), (-1.0, A, "m1"), (0.99, B, "m2"), (-1.0, B, "m2")]), "G12g"),
            lambda: _g12_row(_lab_trades([(2.0, A, "m1"), (-1.0, A, "m1"), (1.0, B, "m2"), (-1.0, B, "m2")]), "G12g")),
    }


def _sel(passing, t, tfm):
    return a.select_advancing(passing, t, tfm)[1]


def _mut_cases():
    """{THRESH anahtarı: (gevşetilmiş değer, below() -> bool [varsayılanda FALSE], above() -> bool [varsayılanda TRUE])}"""
    c: Dict[str, Any] = {}
    c["G0_SILENT_DROP_MAX"] = (0.2, lambda: _gpass(_validity(_n(20), {"qty_zero": 4}), "G0a"),
                               lambda: _gpass(_validity(_n(20), {"qty_zero": 2}), "G0a"))
    c["G0_EOD_MAX"] = (9, lambda: _gpass(_validity(_n(11) + _n(9, reason="EOD")), "G0b"),
                       lambda: _gpass(_validity(_n(12) + _n(8, reason="EOD")), "G0b"))
    c["G0_LEV3_MAX"] = (0.2, lambda: _gpass(_validity(_n(17) + _n(3, lev=3)), "G0c1"),
                        lambda: _gpass(_validity(_n(18) + _n(2, lev=3)), "G0c1"))
    c["G0_LEV20_MAX"] = (0.6, lambda: _gpass(_validity(_n(9) + _n(11, lev=20)), "G0c2"),
                         lambda: _gpass(_validity(_n(10) + _n(10, lev=20)), "G0c2"))
    c["G0_RISK_BAND"] = (0.07, lambda: _gpass(_validity(_n(90) + _n(10, risk=106.0)), "G0d"),
                         lambda: _gpass(_validity(_n(90) + _n(10, risk=104.0)), "G0d"))
    c["G0_RISK_SHARE_MIN"] = (0.9, lambda: _gpass(_validity(_n(94) + _n(6, risk=150.0)), "G0d"),
                              lambda: _gpass(_validity(_n(95) + _n(5, risk=150.0)), "G0d"))
    c["G1_N_MIN"] = (150, lambda: a.rule_g1(199)["pass"], lambda: a.rule_g1(200)["pass"])
    c["G2_PF_MIN"] = (1.10, lambda: a.rule_g2(1.1499)["pass"], lambda: a.rule_g2(1.15)["pass"])
    c["G3_Q_MAX"] = (0.25, lambda: a.rule_g3(0.2001)["pass"], lambda: a.rule_g3(0.20)["pass"])
    g4a_below = _lab_trades([(10.0, "BTCUSDT", "2025-01"), (0.99, "BTCUSDT", "2025-02"), (-1.0, "BTCUSDT", "2025-02")])
    g4a_above = _lab_trades([(10.0, "BTCUSDT", "2025-01"), (1.0, "BTCUSDT", "2025-02"), (-1.0, "BTCUSDT", "2025-02")])
    c["G4_PF_EX_MONTH_MIN"] = (0.95, lambda: _gpass(a.rule_g4(g4a_below), "G4a"), lambda: _gpass(a.rule_g4(g4a_above), "G4a"))
    top5 = [(10.0, "BTCUSDT", "2025-01")] * 5
    c["G4_SUMR_EX_TOP5_MIN"] = (-0.5,
                                lambda: _gpass(a.rule_g4(_lab_trades(top5 + [(-0.1, "BTCUSDT", "2025-02")] * 2)), "G4b"),
                                lambda: _gpass(a.rule_g4(_lab_trades(top5 + [(0.1, "BTCUSDT", "2025-02")] * 2)), "G4b"))
    g5_bad40 = _pf_trades(20, 1.0, 20, -2.0)
    c["G5_GROUP_N_MIN"] = (41, lambda: a.rule_g5(g5_bad40, _LABELS)["pass"],
                           lambda: a.rule_g5(g5_bad40[:39], _LABELS)["pass"])
    c["G5_GROUP_PF_MIN"] = (0.75, lambda: a.rule_g5(_pf_trades(25, 0.79, 25), _LABELS)["pass"],
                            lambda: a.rule_g5(_pf_trades(25, 0.80, 25), _LABELS)["pass"])
    c["G6_FEE2_PF_MIN"] = (0.99, lambda: _gpass(a.rule_g6(0.9999, 1.0, 1.0), "G6a"), lambda: _gpass(a.rule_g6(1.0, 1.0, 1.0), "G6a"))
    c["G6_DLY1_SUMR_MIN"] = (-0.001, lambda: _gpass(a.rule_g6(1.0, 0.0, 1.0), "G6b"), lambda: _gpass(a.rule_g6(1.0, 0.0001, 1.0), "G6b"))
    c["G6_TAKER_PF_MIN"] = (0.99, lambda: _gpass(a.rule_g6(1.0, 1.0, 0.9999), "G6c"), lambda: _gpass(a.rule_g6(1.0, 1.0, 1.0), "G6c"))
    tf3 = {"S5": 5, "S15": 15, "S30": 30}
    t3 = {"S5": 3.0, "S15": 2.0, "S30": 1.0}
    c["DEV_MAX_ADVANCE"] = (3, lambda: len(_sel(["S5", "S15", "S30"], t3, tf3)) == 3,
                            lambda: len(_sel(["S5", "S15", "S30"], t3, tf3)) == 2)
    tf2 = {"S5": 5, "T5": 5}
    t2 = {"S5": 3.0, "T5": 2.0}
    c["DEV_MAX_PER_TF"] = (2, lambda: len(_sel(["S5", "T5"], t2, tf2)) == 2, lambda: len(_sel(["S5", "T5"], t2, tf2)) == 1)
    c["G7_N_MIN"] = (200, lambda: a.rule_g7(249)["pass"], lambda: a.rule_g7(250)["pass"])
    c["G8_PF_MIN"] = (1.10, lambda: a.rule_g8(1.1499)["pass"], lambda: a.rule_g8(1.15)["pass"])
    c["G9_ALPHA_P7"] = (0.06, lambda: _gpass(a.rule_g9(0.0251, 0.0, 2), "G9a"), lambda: _gpass(a.rule_g9(0.025, 0.0, 2), "G9a"))
    c["G9_ALPHA_P28"] = (0.2, lambda: _gpass(a.rule_g9(0.0, 0.0501, 2), "G9b"), lambda: _gpass(a.rule_g9(0.0, 0.05, 2), "G9b"))

    def grp(n_up, n_flat, n_down, pf_down=2.0):
        ts = []
        for n_, mon, pf in ((n_up, "2025-01", 2.0), (n_flat, "2025-02", 2.0), (n_down, "2025-03", pf_down)):
            if n_:
                loss = 10
                ts += _lab_trades([(pf * loss / (n_ - loss) if n_ > loss else 0.0, "BTCUSDT", mon)] * (n_ - loss)
                                  + [(-1.0, "BTCUSDT", mon)] * loss)
        return ts

    c["G10A_GROUP_N_MIN"] = (41, lambda: a.rule_g10a(grp(41, 41, 40, 0.5), _LABELS)["pass"],
                             lambda: a.rule_g10a(grp(41, 41, 39, 0.5), _LABELS)["pass"])
    c["G10A_GROUP_PF_MIN"] = (0.4, lambda: a.rule_g10a(grp(45, 45, 50, 0.89), _LABELS)["pass"],
                              lambda: a.rule_g10a(grp(45, 45, 50, 0.90), _LABELS)["pass"])
    c["G10A_MIN_GROUPS"] = (1, lambda: a.rule_g10a(grp(45, 10, 10), _LABELS)["pass"],
                            lambda: a.rule_g10a(grp(45, 45, 10), _LABELS)["pass"])
    down = {"2023-03": "DOWN"}

    def pool(n, win):
        return _lab_trades([(win, "BTCUSDT", "2023-03")] * (n // 2) + [(-1.0, "BTCUSDT", "2023-03")] * (n - n // 2))

    c["G10B_PF_MIN"] = (1.05, lambda: a.rule_g10b([], pool(100, 1.09), down)["pass"], lambda: a.rule_g10b([], pool(100, 1.2), down)["pass"])
    c["G10B_N_MIN"] = (90, lambda: a.rule_g10b([], pool(99, 1.5), down)["pass"], lambda: a.rule_g10b([], pool(100, 1.5), down)["pass"])

    def months_pos(k):
        return _lab_trades([((1.0 if i < k else -1.0), "BTCUSDT", m) for i, m in enumerate(_M22)])

    c["G11_MONTH_FRAC"] = (0.5, lambda: a.rule_g11(months_pos(12), _M22)["pass"], lambda: a.rule_g11(months_pos(13), _M22)["pass"])
    c.update(_g12_cases())
    c["G13_CALMAR_MIN"] = (0.7, lambda: a.rule_g13(_series(14.9, 2000.0))["pass"], lambda: a.rule_g13(_series(15.0, 2000.0))["pass"])
    c["G15_FEE2_PF_MIN"] = (0.99, lambda: _gpass(a.rule_g15(0.9999, 1, 1, 1), "G15a"), lambda: _gpass(a.rule_g15(1.0, 1, 1, 1), "G15a"))
    c["G15_DLY1_SUMR_MIN"] = (-0.001, lambda: _gpass(a.rule_g15(1, 0.0, 1, 1), "G15b"), lambda: _gpass(a.rule_g15(1, 0.0001, 1, 1), "G15b"))
    c["G15_TAKER_PF_MIN"] = (0.99, lambda: _gpass(a.rule_g15(1, 1, 0.9999, 1), "G15c"), lambda: _gpass(a.rule_g15(1, 1, 1.0, 1), "G15c"))
    c["G15_WALL_SUMR_MIN"] = (-0.001, lambda: _gpass(a.rule_g15(1, 1, 1, 0.0), "G15d"), lambda: _gpass(a.rule_g15(1, 1, 1, 0.0001), "G15d"))
    m2 = ["2023-01", "2023-02"]

    def halves(n2, pf2_win):
        return _lab_trades([(2.0, "BTCUSDT", "2023-01")] * 50 + [(-1.0, "BTCUSDT", "2023-01")] * 50
                           + [(pf2_win, "BTCUSDT", "2023-02")] * (n2 // 2) + [(-1.0, "BTCUSDT", "2023-02")] * (n2 - n2 // 2))

    c["G16_HALF_N_MIN"] = (90, lambda: _gpass(a.rule_g16(halves(98, 2.0), m2), "G16.2n"), lambda: _gpass(a.rule_g16(halves(100, 2.0), m2), "G16.2n"))
    c["G16_HALF_PF_MIN"] = (0.95, lambda: _gpass(a.rule_g16(halves(100, 0.99), m2), "G16.2pf"), lambda: _gpass(a.rule_g16(halves(100, 1.0), m2), "G16.2pf"))
    c["G17_N_MIN"] = (15, lambda: a.rule_g17(19, 1.2, 1.0, 5.0)["pass"], lambda: a.rule_g17(20, 1.2, 1.0, 5.0)["pass"])
    c["G17_DD_R_MAX"] = (30.0, lambda: a.rule_g17(50, 1.2, 25.01, 5.0)["pass"], lambda: a.rule_g17(50, 1.2, 25.0, 5.0)["pass"])
    c["G17_VETO_PF"] = (0.85, lambda: a.rule_g17(50, 0.8999, 1.0, -1.0)["pass"], lambda: a.rule_g17(50, 0.90, 1.0, -1.0)["pass"])
    c["G17_CONSISTENT_PF"] = (0.95, lambda: a.rule_g17(50, 0.99, 1.0, 1.0)["status"] == "FINAL-TUTARLI",
                              lambda: a.rule_g17(50, 1.0, 1.0, 1.0)["status"] == "FINAL-TUTARLI")
    c["B0_ANCHOR_TRADES"] = (319, lambda: a.b0_anchor(319, 0.90)["ok"], lambda: a.b0_anchor(320, 0.90)["ok"])
    c["B0_ANCHOR_PF_LO"] = (0.89, lambda: a.b0_anchor(320, 0.8949)["ok"], lambda: a.b0_anchor(320, 0.895)["ok"])
    c["B0_ANCHOR_PF_HI"] = (0.91, lambda: a.b0_anchor(320, 0.905)["ok"], lambda: a.b0_anchor(320, 0.9049)["ok"])
    c["BAR_ORDER_OPT_PF_MIN"] = (1.10, lambda: a.bar_order_flag(1.0, 1.1499, 0.1, False), lambda: a.bar_order_flag(1.0, 1.15, 0.1, False))
    c["FEE15_PF_MIN"] = (0.99, lambda: a.fee15_flag(True, 0.9999), lambda: a.fee15_flag(True, 1.0))
    c["Z0_GROSS_PF_EDGE"] = (1.04, lambda: a.z0_reading(1.0499, 0.9).startswith("sinyalin"), lambda: a.z0_reading(1.05, 0.9).startswith("sinyalin"))
    c["Z0_NET_PF"] = (1.1, lambda: a.z0_reading(1.2, 1.0).startswith("sinyalin"), lambda: a.z0_reading(1.2, 0.9999).startswith("sinyalin"))
    c["TOPN_G4"] = (3, lambda: _gpass(a.rule_g4(_lab_trades(top5 + [(-0.1, "BTCUSDT", "2025-02")] * 2)), "G4b"),
                    lambda: _gpass(a.rule_g4(_lab_trades(top5 + [(0.1, "BTCUSDT", "2025-02")] * 2)), "G4b"))
    c["TOPN_G12"] = (3, lambda: _g12_row(_lab_trades([(1.0, "BTCUSDT", "m1")] * 5 + [(0.9, "ETHUSDT", "m2")] * 8), "G12d"),
                     lambda: _g12_row(_lab_trades([(1.0, "BTCUSDT", "m1")] * 5 + [(0.9375, "ETHUSDT", "m2")] * 8), "G12d"))
    c["LEV_LOW"] = (4, lambda: _gpass(_validity(_n(17) + _n(3, lev=3)), "G0c1"), lambda: _gpass(_validity(_n(18) + _n(2, lev=3)), "G0c1"))
    c["LEV_HIGH"] = (19, lambda: _gpass(_validity(_n(9) + _n(11, lev=20)), "G0c2"), lambda: _gpass(_validity(_n(10) + _n(10, lev=20)), "G0c2"))
    c["REGIME_UP_PCT"] = (4.9, lambda: a.regime_label(4.99) == "UP", lambda: a.regime_label(5.0) == "UP")
    c["REGIME_DOWN_PCT"] = (-4.9, lambda: a.regime_label(-4.99) == "DOWN", lambda: a.regime_label(-5.0) == "DOWN")
    c["REGIME_SHOCK_PCT"] = (2.0, lambda: a.build_labels(synthetic_btc())["2025-06"]["shock"],        # ret +2.4%
                             lambda: a.build_labels(synthetic_btc())["2025-01"]["shock"])             # ret +9.4%
    c["FEE2_MULT"] = (1.5, lambda: a.expected_cost(a.LabelInfo("x", "DEV", "S15", "ROB_FEE2", 0))[0] == 1.5,
                      lambda: a.expected_cost(a.LabelInfo("x", "DEV", "S15", "ROB_FEE2", 0))[0] == 2.0)
    c["FEE15_MULT"] = (1.4, lambda: a.expected_cost(a.LabelInfo("x", "DEV", "S15", "DIAG_FEE15", 0))[0] == 1.4,
                       lambda: a.expected_cost(a.LabelInfo("x", "DEV", "S15", "DIAG_FEE15", 0))[0] == 1.5)
    c["BULL_PNL_FRAC"] = (0.7, lambda: a.bull_check(79.9, 100.0) is True, lambda: a.bull_check(80.0, 100.0) is True)
    return c


MUT = _mut_cases()
# kapı olmayan (istatistik/gösterim/yapı) anahtarlar — kendi testleri aşağıda
NON_GATE_KEYS = {"BOOT_N", "BOOT_N_HI", "MC_SE_MULT", "BLOCK_PRIMARY", "BLOCK_SECONDARY", "BLOCK_INFO", "PF_CAP",
                 "PF_CI_LEVEL", "BORDERLINE_ABS", "BH_M", "VAL_MIN_DAYS"}


def test_every_thresh_key_is_mutation_covered_or_declared_non_gate():
    assert set(a.THRESH) == set(MUT) | NON_GATE_KEYS
    assert not (set(MUT) & NON_GATE_KEYS)


@pytest.mark.parametrize("key", sorted(MUT))
def test_mutation_threshold_just_below_fails_just_above_passes_and_relaxing_flips(key, monkeypatch):
    relaxed, below, above = MUT[key]
    assert above() is True, f"{key}: eşiğin hemen üstü/eşit GEÇMELİ"
    assert below() is False, f"{key}: eşiğin hemen altı KALMALI"
    monkeypatch.setitem(a.THRESH, key, relaxed)
    assert below() is True, f"{key}: eşik gevşetilince FAIL->PASS dönmeli (kapı fiilen testlenmiyor!)"


def test_val_g20x_cap_is_report_only_in_val_but_gate_in_dev():
    ts = _n(9) + _n(11, lev=20)
    dev = a.entry(a.rule_validity("G0", ts, {}, True, True))
    val = a.entry(a.rule_validity("G14", ts, {}, True, False))
    assert not _gpass(dev, "G0c2") and _gpass(val, "G14c2")
    assert next(r for r in val["rows"] if r["gate"] == "G14c2")["note"] == "kelepçe-yönetimli"
    t_arm = a.entry(a.rule_validity("G0", ts, {}, False, True))               # T arm'ında 3x/20x satırı yok
    assert [r["gate"] for r in t_arm["rows"]] == ["G0a", "G0b", "G0d"]


def test_nongate_thresholds_pf_cap_borderline_and_bh_m(monkeypatch):
    assert a.cap_pf(INF) == 5.0
    monkeypatch.setitem(a.THRESH, "PF_CAP", 4.0)
    assert a.cap_pf(INF) == 4.0
    assert a.is_borderline(1.0, 1.004) and not a.is_borderline(1.0, 1.006)
    monkeypatch.setitem(a.THRESH, "BORDERLINE_ABS", 0.001)
    assert not a.is_borderline(1.0, 1.004)
    assert a.THRESH["BH_M"] == len(a.FAMILY) == 6


def test_dev_advancement_rule_one_per_tf_max_two_ties_to_larger_tf():
    tf = {"S5": 5, "T5": 5, "S15": 15, "T15": 15, "S30": 30, "T30": 30}
    t = {"S5": 9.0, "T5": 8.0, "S15": 7.0, "T15": 6.0, "S30": 5.0, "T30": 4.0}
    ranked, adv = a.select_advancing(list(tf), t, tf)
    assert adv == ["S5", "S15"] and ranked[0] == "S5"                       # 5m'in ikinci arm'ı (T5) ilerleyemez
    # TF başına tek: S5 ve T5 geçtiyse yalnız t_obs yüksek olan; 30m ancak 5m/15m elenince
    assert a.select_advancing(["T5", "S30", "T30"], t, tf)[1] == ["T5", "S30"]
    # eşitlikte BÜYÜK TF
    assert a.select_advancing(["S5", "S15"], {"S5": 3.0, "S15": 3.0}, tf)[1] == ["S15", "S5"]
    assert a.select_advancing([], t, tf) == ([], [])


# ==========================================================================
# 6) check-run (§5.2): saf kontrol fonksiyonu
# ==========================================================================
@pytest.fixture
def cr(tmp_path):
    ctx = stub_ctx(tmp_path)
    label = "DEV_S15"
    info = a.parse_label(label, ctx.variants)
    trades = gen_trades(a.WINDOWS["DEV"], 30, 5, s_arm=9.0)
    return ctx, label, info, trades


def _fails(ctx, label, doc, log="ok\n"):
    return a.check_report(ctx, a.parse_label(label, ctx.variants), doc, log)


def test_check_report_passes_a_conforming_report(cr):
    ctx, label, _info, trades = cr
    assert _fails(ctx, label, make_report(ctx, label, trades)) == []


@pytest.mark.parametrize("label,extra", [("DEV_S15_ROB_FEE2", {}), ("DEV_S15_ROB_DLY1", {}), ("DEV_S15_ROB_TAKER", {}),
                                         ("DEV_S15_DIAG_SBO", {}), ("DEV_S15_DIAG_FEE15", {}), ("DEV_S15_DIAG_Z0", {}),
                                         ("VAL_S15_ROB_WALL", {}), ("DEV_B0_Q2", {}), ("FINAL_B0", {}), ("DEV_S5G", {}),
                                         ("DEV_T30", {}), ("DEV_S60", {}), ("SMOKE_B0_AYI", {}), ("SMOKE_B0_V1", {}),
                                         ("SMOKE_S15_h1", {}), ("SMOKE_T60_W", {}), ("SMOKE_R1_M1", {})])
def test_check_report_overlays_quarters_and_sets_have_expected_contract(tmp_path, label, extra):
    ctx = stub_ctx(tmp_path)
    info = a.parse_label(label, ctx.variants)
    s, e = a.run_window(ctx, info)
    v = ctx.vid[info.variant]
    tr = gen_trades((s, e), 12, 2, s_arm=v["S_arm"])
    assert _fails(ctx, label, make_report(ctx, label, tr)) == []


def test_check_report_overlay_contract_values():
    assert a.expected_cost(a.LabelInfo("x", "DEV", "S15", "ROB_FEE2", 0)) == (2.0, 0)
    assert a.expected_cost(a.LabelInfo("x", "DEV", "S15", "DIAG_FEE15", 0)) == (1.5, 0)
    assert a.expected_cost(a.LabelInfo("x", "DEV", "S15", "ROB_DLY1", 0)) == (1.0, 1)
    assert a.expected_cost(a.LabelInfo("x", "DEV", "S15", "", 0)) == (1.0, 0)


@pytest.mark.parametrize("mutate,needle", [
    (lambda d: d["provenance"].update(git_sha="b" * 40), "git_sha"),
    (lambda d: d["provenance"].update(git_dirty=True), "git_dirty"),
    (lambda d: d["provenance"]["test_window"].update(end_ms=1), "test penceresi"),
    (lambda d: d["provenance"]["scalper_config"].update(scalper_tf_context="4h"), "TF üçlüsü"),
    (lambda d: d["provenance"]["scalper_config"].update(scalper_fixed_stop_roi_pct=8.0), "scalper_fixed_stop_roi_pct"),
    (lambda d: d["provenance"]["scalper_config"].update(scalper_regime_filter=True), "scalper_regime_filter"),
    (lambda d: d["provenance"]["scalper_config"].pop("scalper_market_gate"), "scalper_market_gate yok"),
    (lambda d: d["provenance"]["cost_model"].update(stress_multiplier=2.0), "stress_multiplier"),
    (lambda d: d["provenance"]["cost_model"].update(entry_delay_candles=1), "entry_delay"),
    (lambda d: d["provenance"]["sim_options"].update(same_bar_order="optimistic"), "sim_options"),
    (lambda d: d["provenance"]["sim_options"].update(diagnostics=False), "sim_options"),
    (lambda d: d.pop("diagnostics"), "diagnostics"),
    (lambda d: d.pop("missed_signals"), "missed_signals"),
    (lambda d: d["overall"].pop("bar_max_drawdown"), "bar_max_drawdown"),
    (lambda d: d["overall"].update(bar_max_drawdown=float("inf")), "bar_max_drawdown"),
    (lambda d: d["overall"].update(trades=999), "overall.trades"),
    (lambda d: d["overall"].update(total_pnl=d["overall"]["total_pnl"] + 5.0), "total_pnl"),
    (lambda d: d["overall"].update(profit_factor=d["overall"]["profit_factor"] * 1.01), "profit_factor"),
    (lambda d: d["params"].update(symbols=list(reversed(d["params"]["symbols"]))), "symbols"),
    (lambda d: d["provenance"]["data_windows"]["BTCUSDT"]["15m"].update(candles_in_test_window=1), "boşluk"),
    (lambda d: d["provenance"]["data_windows"]["BTCUSDT"].pop("4h"), "TF kümesi"),
    (lambda d: d["provenance"]["data_windows"].pop("ADAUSDT"), "sembol kümesi"),
    (lambda d: d["provenance"]["data_windows"]["BTCUSDT"]["1h"].update(fetched_start_ms=a.date_ms("2024-11-17")), "VAL bitişinden"),
    (lambda d: d["trades"][0].update(exit_time=a.date_ms("2026-02-01")), "penceresinin dışında"),
])
def test_check_report_detects_each_contract_violation(cr, mutate, needle):
    ctx, label, _info, trades = cr
    doc = make_report(ctx, label, trades)
    mutate(doc)
    reasons = _fails(ctx, label, doc)
    assert any(needle in r for r in reasons), (needle, reasons)


def test_check_report_log_cache_miss_and_role_collision(cr):
    ctx, label, _info, trades = cr
    doc = make_report(ctx, label, trades)
    assert any("Binance'ten çekiliyor" in r for r in _fails(ctx, label, doc, "x\n📥 BTCUSDT 5m: Binance'ten çekiliyor...\n"))
    assert any("log dosyası yok" in r for r in a.check_report(ctx, a.parse_label(label, ctx.variants), doc, None))
    ctx.variants["family"][2]["triple"] = ["15m", "15m", "4h"]                # giriş = bağlam
    ctx.vid["S15"]["triple"] = ["15m", "15m", "4h"]
    d2 = make_report(ctx, label, trades)
    assert any("rol-anahtarı" in r for r in _fails(ctx, label, d2))
    with pytest.raises(a.IntegrityError):
        a.validate_variants_doc(ctx.variants)


def test_check_report_val_set_skips_earliest_bar_rule(tmp_path):
    ctx = stub_ctx(tmp_path)
    label = "VAL_S15"
    s, e = a.WINDOWS["VAL"]
    doc = make_report(ctx, label, gen_trades((s, e), 20, 1))
    assert doc["provenance"]["data_windows"]["BTCUSDT"]["15m"]["fetched_start_ms"] < a.date_ms(a.VAL_END)
    assert _fails(ctx, label, doc) == []


def test_label_grammar_and_applicability():
    v = make_variants()
    i = a.parse_label("DEV_S5G", v)
    assert (i.variant, i.overlay, i.quarter) == ("S5G", "", 0)                 # S5 ile S5G karışmaz
    i = a.parse_label("DEV_S5_ROB_FEE2", v)
    assert (i.variant, i.overlay) == ("S5", "ROB_FEE2")
    i = a.parse_label("DEV_B0_Q3", v)
    assert (i.variant, i.quarter) == ("B0", 3)
    i = a.parse_label("SMOKE_S15_h1", v)
    assert i.set == "SMOKE" and i.tag == "h1" and i.window_set == "SMOKE_W"
    for bad in ["DEV_B0", "DEV_S15_Q1", "VAL_S15_DIAG_Z0", "DEV_S5G_ROB_FEE2", "FINAL_S15_ROB_FEE2", "VAL_B0",
                "DEV_X9", "XYZ_S15", "SMOKE_S15_AYI", "DEV_S15_ROB_NOPE", "VAL_S15_Q1"]:
        with pytest.raises(a.UsageError):
            a.parse_label(bad, v)


# ==========================================================================
# 7) Kilit (lock / verify-lock): LOCK_REF + git show L:..., uyuşmazlık = exit 3 + SIFIR dosya
# ==========================================================================
@pytest.fixture(scope="module")
def lab(tmp_path_factory):
    base = tmp_path_factory.mktemp("e17lab")
    with pytest.MonkeyPatch.context() as mp:
        for k, v in FAST.items():
            mp.setitem(a.THRESH, k, v)
        lb = build_lab(base, mp)
        lb.snap("locked")
    return lb


def _tree_listing(p: Path) -> List[tuple]:
    out = []
    for f in sorted(p.rglob("*")):
        if f.is_file():
            out.append((str(f.relative_to(p)), f.stat().st_size, f.stat().st_mtime_ns))
    return out


def test_lock_creates_lock_json_commit_and_ref(lab, lab_env, capsys):
    root = lab.root
    ref = json.loads((root / "LOCK_REF.json").read_text())
    assert (lab.repo / a.LOCKDIR_REL / "LOCK.json").is_file()
    assert _git(lab.repo, "rev-parse", "HEAD") == ref["l"] and ref["pushed"] is False
    assert _git(lab.repo, "rev-parse", f"{ref['l']}^") == ref["h"]
    raw = subprocess.run(["git", "-C", str(lab.repo), "show", f"{ref['l']}:{a.LOCKDIR_REL}/LOCK.json"],
                         capture_output=True, check=True).stdout
    assert a.sha256_bytes(raw) == ref["lock_sha256"]
    lock = json.loads(raw)
    assert lock["h_commit"] == ref["h"] and lock["h_tree"] == _git(lab.repo, "rev-parse", f"{ref['h']}^{{tree}}")
    assert set(lock["code_blobs"]) >= set(a.REQUIRED_CODE) and "tests/test_e17_analyzer.py" in lock["code_blobs"]
    assert set(lock["files"]) == {"PLAN.md", "variants.json", "labels_frozen.json", "cal_constants.json", "archive_probe.json", "evidence/e.txt"}
    assert lock["thresholds_sha256"] == a.thresholds_sha256() and lock["python_version"]
    assert any(k.startswith("DEV:15m/1h/4h") for k in lock["earliest_bar_check"])
    assert lock["snapshot_sha256"] == a.sha256_file(root / "server-scalper-env.txt")
    assert "SCALPER_DUMMY" not in json.dumps(lock)


def test_lock_prints_exact_push_command(lab, lab_env, tmp_path, capsys):
    root = lab.clone_root("locked", tmp_path)
    # ikinci kilit (L2): DEV koşusu yok -> serbest; çıktı push komutunu verir
    (lab.repo / a.LOCKDIR_REL / "evidence" / "e.txt").write_text("ev2\n", encoding="utf-8")
    try:
        assert a.main(["lock", "--root", str(root)]) == 0
        out = capsys.readouterr().out
        l2 = json.loads((root / "LOCK_REF.json").read_text())
        assert f"git -C {lab.repo} push origin {l2['l']}:refs/heads/main-18w3qz" in out
        assert [h["kind"] for h in l2["history"]] == ["lock", "L2"]
        assert a.main(["verify-lock", "--root", str(root)]) == 0
    finally:
        _git(lab.repo, "reset", "-q", "--hard", json.loads((lab.root / "LOCK_REF.json").read_text())["l"])


def test_verify_lock_ok_then_each_tamper_gives_exit_3_and_writes_nothing(lab, lab_env, tmp_path, capsys):
    base = lab.clone_root("locked", tmp_path)
    assert a.main(["verify-lock", "--root", str(base)]) == 0
    ld = lab.repo / a.LOCKDIR_REL

    def run_expect3(root: Path, argv: List[str]) -> None:
        before = _tree_listing(root)
        assert a.main([*argv, "--root", str(root)]) == 3
        assert _tree_listing(root) == before                         # HİÇBİR dosya yazılmaz

    # 1) kilitli veri dosyası (çalışma kopyası) değişti
    orig = (ld / "PLAN.md").read_text()
    (ld / "PLAN.md").write_text(orig + "sessiz degisiklik\n")
    try:
        for argv in (["verify-lock"], ["dev-screen"], ["dev-decide"], ["diag"], ["val"], ["final"], ["decide"], ["smoke"],
                     ["manifest", "dev"], ["check-run", "DEV_S15"]):
            run_expect3(base, argv)
    finally:
        (ld / "PLAN.md").write_text(orig)
    # 1b) kilitte olmayan fazladan kanıt dosyası / kilitli dosyanın silinmesi
    extra = ld / "evidence" / "extra.txt"
    extra.write_text("sonradan eklendi")
    try:
        run_expect3(base, ["verify-lock"])
    finally:
        extra.unlink()
    keep = (ld / "archive_probe.json").read_text()
    (ld / "archive_probe.json").unlink()
    try:
        run_expect3(base, ["dev-screen"])
    finally:
        (ld / "archive_probe.json").write_text(keep)
    # 2) THRESH serileştirmesi değişti
    old = a.THRESH["G2_PF_MIN"]
    a.THRESH["G2_PF_MIN"] = 1.0
    try:
        run_expect3(base, ["verify-lock"])
        run_expect3(base, ["dev-screen"])
    finally:
        a.THRESH["G2_PF_MIN"] = old
    # 3) LOCK_REF kaydı bozuk / yok
    ref_p = base / "LOCK_REF.json"
    ref = json.loads(ref_p.read_text())
    ref_p.write_text(json.dumps({**ref, "lock_sha256": "0" * 64}))
    run_expect3(base, ["verify-lock"])
    ref_p.write_text(json.dumps({**ref, "l": "f" * 40}))
    run_expect3(base, ["verify-lock"])
    ref_p.unlink()
    run_expect3(base, ["final"])
    ref_p.write_text(json.dumps(ref))
    # 4) sunucu özeti yok/değişti
    snap = base / "server-scalper-env.txt"
    snap.write_text("SCALPER_X=2\n")
    run_expect3(base, ["verify-lock"])
    snap.write_text("SCALPER_DUMMY=1\n")
    # 5) çalışan analizci H'deki blob'la eşleşmiyor
    other = tmp_path / "analyze_e17.py"
    other.write_text((lab.repo / "scripts" / "e17" / "analyze_e17.py").read_text() + "\n# oynandi\n")
    old_self = a.SELF_PATH
    a.SELF_PATH = other
    try:
        run_expect3(base, ["verify-lock"])
    finally:
        a.SELF_PATH = old_self
    assert a.main(["verify-lock", "--root", str(base)]) == 0


def test_lock_refused_once_any_dev_run_in_ledger_exit_4(lab, lab_env, tmp_path):
    root = lab.clone_root("locked", tmp_path)
    ctx = a.verify_lock(root)
    put_run(root, ctx, "DEV_S15", gen_trades(a.WINDOWS["DEV"], 20, 1))
    head = _git(lab.repo, "rev-parse", "HEAD")
    assert a.main(["lock", "--root", str(root)]) == 4
    assert _git(lab.repo, "rev-parse", "HEAD") == head               # yeni commit yok


def test_lock_refuses_dirty_tree_and_leak_wall(lab, lab_env, tmp_path):
    root = lab.clone_root("locked", tmp_path)
    (lab.repo / "stray.txt").write_text("x")
    try:
        assert a.main(["lock", "--root", str(root)]) == 4
    finally:
        (lab.repo / "stray.txt").unlink()
    bad = {"DEV:15m/1h/4h": "2024-11-18T00:00:00Z", "FINAL:5m/30m/1h": "2026-08-23T14:00:00Z", "VAL:5m/30m/1h": "2022-12-21T14:00:00Z"}
    assert a.earliest_bar_violations(bad) == ["DEV:15m/1h/4h=2024-11-18T00:00:00Z"]       # <= VAL bitişi
    assert a.earliest_bar_violations({"DEV:15m/1h/4h": "2024-11-18T00:00:01Z"}) == []


def test_earliest_bar_matches_plan_table_via_harness_functions():
    tab = {("5m", "30m", "1h"): ("2024-12-21T14:00:00Z", "2022-12-21T14:00:00Z", "2026-08-23T14:00:00Z"),
           ("15m", "1h", "4h"): ("2024-11-20T08:00:00Z", "2022-11-20T08:00:00Z", None),
           ("30m", "4h", "4h"): ("2024-11-20T08:00:00Z", "2022-11-20T08:00:00Z", None),
           ("1h", "4h", "4h"): ("2024-11-20T08:00:00Z", "2022-11-20T08:00:00Z", None)}
    for tri, (dev, val, fin) in tab.items():
        assert a.ms_to_iso(a.earliest_bar_ms(tri, *a.WINDOWS["DEV"])) == dev
        assert a.ms_to_iso(a.earliest_bar_ms(tri, *a.WINDOWS["VAL"])) == val
        if fin:
            assert a.ms_to_iso(a.earliest_bar_ms(tri, *a.WINDOWS["FINAL"])) == fin
    chk = a.earliest_bar_check(make_variants())
    assert a.earliest_bar_violations(chk) == []
    assert min(a.date_ms(v[:10]) for k, v in chk.items() if k.startswith("DEV:")) > a.date_ms(a.VAL_END)
    # bağlam == rejim (30m/4h/4h): iki ayrı 4h önbellek anahtarı (+100 ve +250)
    rw = a.role_windows(("30m", "4h", "4h"), *a.WINDOWS["DEV"])
    assert rw[1][0] == rw[2][0] == "4h" and rw[1][1] != rw[2][1] and rw[1][1] > rw[2][1]


def test_validate_variants_doc_cross_checks_windows_and_symbols():
    v = make_variants()
    a.validate_variants_doc(v)
    v["sets"]["VAL"] = ["2023-01-01", "2024-12-31"]
    with pytest.raises(a.IntegrityError, match="sets"):
        a.validate_variants_doc(v)
    v2 = make_variants()
    v2["symbols_order_pinned"] = "ETHUSDT,BTCUSDT"
    with pytest.raises(a.IntegrityError):
        a.validate_variants_doc(v2)


# ==========================================================================
# 8) check-run uçtan uca (kilitli lab): ledger, bayat çıktı silme, sha uyuşmazlığı
# ==========================================================================
def _stage_files(root: Path, ctx: "a.Ctx", label: str, trades, **kw) -> Dict[str, Any]:
    return put_run(root, ctx, label, trades, ledger=False, **kw)


def test_check_run_end_to_end_appends_ledger_manifest_and_is_idempotent(lab, lab_env, tmp_path, capsys):
    root = lab.clone_root("locked", tmp_path)
    ctx = a.verify_lock(root)
    write_smoke_pass(root, ctx)
    _stage_files(root, ctx, "DEV_S15", gen_trades(a.WINDOWS["DEV"], 25, 9))
    (root / "sealed" / "logs" / "DEV_S15.sec").write_text("12.5\n")
    assert a.main(["check-run", "DEV_S15", "--root", str(root)]) == 0
    out = capsys.readouterr().out
    assert out.strip() == "CHECK OK DEV_S15"                                  # PnL/PF/işlem sayısı YANKILANMAZ
    led = [json.loads(x) for x in (root / "ledger.jsonl").read_text().splitlines()]
    assert led[0]["label"] == "DEV_S15" and led[0]["seconds"] == 12.5 and led[0]["git_sha"] == ctx.h
    assert led[0]["sha256"] == a.sha256_file(root / "sealed" / "runs" / "DEV_S15.json")
    man = json.loads((root / "MANIFEST.runs.json").read_text())
    assert man[0]["etiket"] == "DEV_S15" and man[0]["sha256"] == led[0]["sha256"]
    assert a.main(["check-run", "DEV_S15", "--root", str(root)]) == 0         # tekrar: yeni satır eklenmez
    assert len((root / "ledger.jsonl").read_text().splitlines()) == 1
    (root / "sealed" / "runs" / "DEV_S15.json").write_text("{}")             # kayıttan sonra değişen rapor
    assert a.main(["check-run", "DEV_S15", "--root", str(root)]) == 5


def test_check_run_failure_deletes_stale_output_and_does_not_ledger(lab, lab_env, tmp_path, capsys):
    root = lab.clone_root("locked", tmp_path)
    ctx = a.verify_lock(root)
    write_smoke_pass(root, ctx)
    _stage_files(root, ctx, "DEV_S15", gen_trades(a.WINDOWS["DEV"], 25, 9), git_sha="c" * 40)
    assert a.main(["check-run", "DEV_S15", "--root", str(root)]) == 5
    err = capsys.readouterr().err
    assert "git_sha" in err and "CHECK FAIL DEV_S15" in err
    assert not (root / "sealed" / "runs" / "DEV_S15.json").exists() and not (root / "sealed" / "logs" / "DEV_S15.log").exists()
    assert not (root / "ledger.jsonl").exists()
    _stage_files(root, ctx, "DEV_T15", gen_trades(a.WINDOWS["DEV"], 25, 9, s_arm=50.0), log="Binance'ten çekiliyor\n")
    assert a.main(["check-run", "DEV_T15", "--root", str(root)]) == 5
    assert a.main(["check-run", "DEV_NOPE", "--root", str(root)]) == 2
    assert a.main(["check-run", "DEV_S15", "--root", str(root)]) == 5            # rapor dosyası yok


def test_ledger_rejects_unledgered_run_files_and_duplicates(lab, lab_env, tmp_path):
    root = lab.clone_root("locked", tmp_path)
    ctx = a.verify_lock(root)
    put_run(root, ctx, "DEV_S15", gen_trades(a.WINDOWS["DEV"], 20, 1))
    a.Ledger(root).assert_clean()
    _stage_files(root, ctx, "DEV_T15", gen_trades(a.WINDOWS["DEV"], 20, 1, s_arm=50.0))      # ledger'sız dosya
    with pytest.raises(a.IntegrityError, match="ledger'da yok"):
        a.Ledger(root).assert_clean()
    with open(root / "ledger.jsonl", "a") as f:
        f.write(json.dumps({"label": "DEV_S15", "sha256": "x"}) + "\n")
    with pytest.raises(a.IntegrityError, match="çift"):
        a.Ledger(root)


# ==========================================================================
# 9) Aşama hattı (sentetik uçtan uca): dev-screen -> dev-decide -> val -> final -> decide (+ diag)
# ==========================================================================
GOOD = dict(pw=0.62, w=1.2)
STRONG = dict(pw=0.66, w=1.3)
MID = dict(pw=0.56, w=1.15)
FLATQ = dict(pw=0.50, w=1.0)       # PF ~ 0.97
BADQ = dict(pw=0.47, w=1.0)        # PF ~ 0.86


def _dev_spec() -> Dict[str, Dict[str, Any]]:
    return {
        "S5": dict(n=300, **FLATQ), "T5": dict(n=150, **GOOD), "S15": dict(n=320, **GOOD),
        "T15": dict(n=260, eod=9, **GOOD), "S30": dict(n=260, **STRONG), "T30": dict(n=260, **MID),
        "S60": dict(n=60, **GOOD), "T60": dict(n=60, **GOOD), "S5G": dict(n=100, **GOOD), "T5G": dict(n=100, **GOOD),
    }


def _arm_s(ctx, arm):
    return float(ctx.vid[arm]["S_arm"])


@pytest.fixture(scope="module")
def pipe(lab, tmp_path_factory):
    """Hattı BİR KEZ koşar ve her aşamadan sonra root anlık görüntüsü alır (testler kopyasını kullanır)."""
    with pytest.MonkeyPatch.context() as mp:
        for k, v in FAST.items():
            mp.setitem(a.THRESH, k, v)
        mp.setattr(a, "REPO", lab.repo)
        mp.setattr(a, "SELF_PATH", lab.repo / "scripts" / "e17" / "analyze_e17.py")
        root = lab.base / "pipe_root"
        shutil.copytree(lab.base / "snap_locked", root)
        ctx = a.verify_lock(root)
        write_smoke_pass(root, ctx)
        seed = [100]

        def add(label, n, s_arm, window, **kw):
            seed[0] += 1
            dd_r = kw.pop("dd_r", 5.0)
            missed = kw.pop("missed", None)
            g = gen_trades(window, n, seed[0], s_arm=s_arm, **kw)
            put_run(root, ctx, label, g, dd_r=dd_r, missed=missed)

        spec = _dev_spec()
        for arm, sp in spec.items():
            add(f"DEV_{arm}", sp["n"], _arm_s(ctx, arm), a.WINDOWS["DEV"], **{k: v for k, v in sp.items() if k != "n"})
        for ref in ("R1", "B0"):
            for q, (s, e) in enumerate(a.DEV_QUARTERS, 1):
                add(f"DEV_{ref}_Q{q}", 35, 50.0, (s, e), **GOOD)
        # --- dev-screen ---
        lab.snap("dev_runs", root)
        assert a.main(["dev-screen", "--root", str(root)]) == 0
        lab.snap("screened", root)
        # --- G6 kaplama koşuları: S15 hepsi iyi; S30 FEE2 kötü (G6a); T30 hepsi iyi ---
        for arm in ("S15", "S30", "T30"):
            for ov in a.G6_OVERLAYS:
                q = GOOD if not (arm == "S30" and ov == "ROB_FEE2") else BADQ
                add(f"DEV_{arm}_{ov}", 250, _arm_s(ctx, arm), a.WINDOWS["DEV"], **q)
        assert a.main(["dev-decide", "--root", str(root)]) == 0
        lab.snap("dev_decided", root)
        # --- teşhis koşuları (yalnız DEV_DECISION'dan sonra) ---
        for arm in a.FAMILY:
            add(f"DEV_{arm}_DIAG_Z0", 60, _arm_s(ctx, arm), a.WINDOWS["DEV"], **GOOD)
            q = STRONG if arm == "S5" else GOOD
            add(f"DEV_{arm}_DIAG_SBO", 300 if arm == "S5" else 60, _arm_s(ctx, arm), a.WINDOWS["DEV"], **q)
        for arm in ("S15", "S30", "T30"):                      # S30 FEE2 kaldı; ilerleyenler S15/T30
            add(f"DEV_{arm}_DIAG_FEE15", 60, _arm_s(ctx, arm), a.WINDOWS["DEV"], **GOOD)
        assert a.main(["diag", "--root", str(root)]) == 0
        lab.snap("diagnosed", root)
        # --- VAL: S15 hepsi iyi; T30 ROB_WALL kötü (G15d) ---
        for arm, n in (("S15", 700), ("T30", 650)):
            add(f"VAL_{arm}", n, _arm_s(ctx, arm), a.WINDOWS["VAL"], **GOOD)
            for ov in a.VAL_OVERLAYS:
                q = BADQ if (arm == "T30" and ov == "ROB_WALL") else GOOD
                add(f"VAL_{arm}_{ov}", 300, _arm_s(ctx, arm), a.WINDOWS["VAL"], **q)
        for arm in a.DESCRIPTIVE:                               # açıklayıcılar VAL'de de koşulur (kazanamaz, yalnız rapor)
            add(f"VAL_{arm}", 90, _arm_s(ctx, arm), a.WINDOWS["VAL"], **GOOD)
        assert a.main(["val", "--root", str(root)]) == 0
        lab.snap("validated", root)
        # --- FINAL ---
        add("FINAL_S15", 60, 9.0, a.WINDOWS["FINAL"], **GOOD)
        b0 = gen_trades(a.WINDOWS["FINAL"], 320, 7, s_arm=50.0)
        wins = 232                                         # PF'i TAM 0.90 yap: 232 kazanç x +10, 88 kayıp x -L
        loss = (wins * 10.0 / 0.9) / (320 - wins)
        for i, t in enumerate(b0):
            t["pnl"] = 10.0 if i < wins else -loss
            t["exit_reason"] = "TRAIL" if i < wins else "SL"
        put_run(root, ctx, "FINAL_B0", b0)
        assert a.main(["final", "--root", str(root)]) == 0
        lab.snap("finalized", root)
        assert a.main(["decide", "--root", str(root)]) == 0
        lab.snap("decided", root)
    return lab


def _j(p: Path) -> Dict[str, Any]:
    return json.loads(p.read_text(encoding="utf-8"))


def test_dev_screen_marks_only_the_designed_arms_and_prints_only_arm_names(pipe, lab_env, tmp_path, capsys):
    root = pipe.clone_root("screened", tmp_path)
    scr = _j(root / "DEV_SCREEN.json")
    assert scr["_meta"]["g6_needed"] == ["S15", "S30", "T30"]
    assert scr["T5"]["G1"]["pass"] is False and scr["T5"]["p7"] == 1.0                   # N < 200 -> p := 1.0
    assert scr["T15"]["G0"]["pass"] is False and scr["T15"]["p7"] == 1.0                 # EOD > 8 -> GEÇERSİZ
    assert scr["S5"]["G2"]["pass"] is False and scr["S5"]["pass_g0_g5"] is False         # PF < 1.15
    assert scr["S15"]["pass_g0_g5"] and scr["S30"]["pass_g0_g5"] and scr["T30"]["pass_g0_g5"]
    assert scr["S15"]["p7"] < 0.02 and scr["S15"]["q"] <= 0.2 and scr["S15"]["G3"]["pass"]
    bh = scr["_meta"]["bh"]
    assert bh["m"] == 6 and set(bh["p"]) == set(a.FAMILY)
    q = a.bh_qvalues([bh["p"][x] for x in a.FAMILY])
    assert [round(x, 9) for x in q] == [round(bh["q"][x], 9) for x in a.FAMILY]
    for g in ("G0", "G1", "G2", "G3", "G4", "G5"):
        for r in (scr["S15"][g]["rows"] if "rows" in scr["S15"][g] else [scr["S15"][g]]):
            assert {"value", "threshold", "pass", "borderline"} <= set(r)
    # konsol: yalnız hangi arm'a G6 koşusu gerektiği (PF/p/N yankısı yok)
    assert a.main(["dev-screen", "--root", str(root)]) == 0
    out = capsys.readouterr().out.strip()
    assert out == "G6 kaplama koşuları gereken arm'lar: S15 S30 T30"


def test_stage_preconditions_exit_4(pipe, lab_env, tmp_path):
    # DEV taban koşuları tamam değil
    root = pipe.clone_root("locked", tmp_path)
    assert a.main(["dev-screen", "--root", str(root)]) == 4
    assert not (root / "DEV_SCREEN.json").exists()
    # dev-decide: screen yok
    assert a.main(["dev-decide", "--root", str(root)]) == 4
    # val: DEV_DECISION olmadan; final: VAL kazananı olmadan; diag: DEV_DECISION olmadan; decide: aynı
    for st in ("val", "final", "diag", "decide"):
        assert a.main([st, "--root", str(root)]) == 4, st
    assert not any((root / f).exists() for f in ("VAL_DECISION.json", "FINAL.json", "DIAG.json", "DECISION.md"))
    # G6 koşusu eksikken dev-decide
    r2 = pipe.clone_root("screened", tmp_path)
    assert a.main(["dev-decide", "--root", str(r2)]) == 4
    assert not (r2 / "DEV_DECISION.json").exists()
    # manifest val/final aşama kapısı
    assert a.main(["manifest", "val", "--root", str(r2)]) == 4
    assert a.main(["manifest", "final", "--root", str(r2)]) == 4


def test_dev_decide_g6_runs_for_every_g0_g5_passer_and_advancement(pipe, lab_env, tmp_path):
    root = pipe.clone_root("dev_decided", tmp_path)
    dd = _j(root / "DEV_DECISION.json")
    assert dd["g6"]["S30"]["pass"] is False and dd["g6"]["S30"]["fee2"]["pass"] is False      # G6a
    assert dd["g6"]["S15"]["pass"] and dd["g6"]["T30"]["pass"]
    assert sorted(dd["advanced"]) == ["S15", "T30"] and dd["k"] == 2 and dd["stop"] is False   # S30 G6'da elendi -> T30 temsilci
    assert {r["arm"] for r in dd["ranked"]} == {"S15", "T30"}
    assert dd["bh"]["m"] == 6
    # ikinci yazım reddedilir; dosya DEĞİŞMEZ
    before = (root / "DEV_DECISION.json").read_bytes()
    assert a.main(["dev-decide", "--root", str(root)]) == 4
    assert (root / "DEV_DECISION.json").read_bytes() == before
    # dev-screen artık çalıştırılamaz
    assert a.main(["dev-screen", "--root", str(root)]) == 4


def test_dev_decide_no_advancer_writes_stop_record(pipe, lab_env, tmp_path):
    root = pipe.clone_root("screened", tmp_path)
    ctx = a.verify_lock(root)
    for arm in ("S15", "S30", "T30"):
        for ov in a.G6_OVERLAYS:
            put_run(root, ctx, f"DEV_{arm}_{ov}", gen_trades(a.WINDOWS["DEV"], 250, 3, s_arm=_arm_s(ctx, arm), **BADQ))
    assert a.main(["dev-decide", "--root", str(root)]) == 0
    dd = _j(root / "DEV_DECISION.json")
    assert dd["advanced"] == [] and dd["stop"] is True and dd["k"] == 0
    assert a.main(["val", "--root", str(root)]) == 4                         # ilerleyen yok: VAL çalışmaz
    assert a.main(["manifest", "val", "--root", str(root)]) == 4
    # decide -> DEV-YOK
    assert a.main(["decide", "--root", str(root)]) == 0
    assert "DEV-YOK" in (root / "DECISION.md").read_text()


def test_diag_labels_bar_order_and_marginal_fee(pipe, lab_env, tmp_path):
    root = pipe.clone_root("diagnosed", tmp_path)
    dg = _j(root / "DIAG.json")["arms"]
    assert "BAR-ORDER-BELİRSİZ" in dg["S5"]["labels"]                         # kötümser PF < 1.15, iyimser >= 1.15, p kapısı geçti
    assert dg["S5"]["pf_opt"] > dg["S5"]["pf_pess"]
    assert "MARJİNAL-ÜCRETE-DUYARLI" in dg["S30"]["labels"]                  # FEE2 geçemedi, FEE1.5 geçti
    assert dg["S15"]["labels"] == [] and "pf_fee15" in dg["S15"]
    assert all(dg[x]["z0_reading"] for x in a.FAMILY)
    assert a.z0_reading(1.2, 0.9) == "sinyalin ham kenarı var, ücret yiyor"
    assert a.z0_reading(1.0, 0.9) == "ücretten önce de kenar yok"
    # eksik teşhis koşusu -> aşama ön koşulu
    r2 = pipe.clone_root("dev_decided", tmp_path)
    assert a.main(["diag", "--root", str(r2)]) == 4


def test_val_gates_winner_and_single_write(pipe, lab_env, tmp_path):
    root = pipe.clone_root("validated", tmp_path)
    vd = _j(root / "VAL_DECISION.json")
    assert vd["_meta"]["winner"] == "S15" and vd["_meta"]["k"] == 2 and vd["_meta"]["full_months"] == 22
    s15 = vd["S15"]
    assert s15["pass_all"] is True and s15["winner"] is True and not s15["kelepce_yonetimli"]
    for g in ("G7", "G8", "G9", "G10", "G11", "G12", "G13", "G14", "G15", "G16"):
        assert s15[g]["pass"] is True, g
    assert s15["G9"]["rows"][0]["threshold"] == pytest.approx(0.025) and s15["G9"]["rows"][1]["threshold"] == pytest.approx(0.05)
    t30 = vd["T30"]
    assert t30["G15"]["pass"] is False and t30["pass_all"] is False and t30["winner"] is False   # G15d (WALL)
    assert [r["pass"] for r in t30["G15"]["rows"]] == [True, True, True, False]
    assert a.main(["val", "--root", str(root)]) == 4                         # ikinci yazım reddedilir
    # FINAL verisi VAL'den önce var olamaz, VAL aşamasında manifest final kapısı açık
    assert a.main(["manifest", "final", "--root", str(root)]) == 5            # kazanan var ama önbellek dosyaları yok


def test_val_window_shorter_than_365_days_is_infrastructure_error(pipe, lab_env, tmp_path):
    root = pipe.clone_root("dev_decided", tmp_path)
    (root / "VAL_WINDOW_CUT.json").write_text(json.dumps({"start": "2024-06-01"}))       # 170 gün
    assert a.main(["val", "--root", str(root)]) == 0
    vd = _j(root / "VAL_DECISION.json")
    assert vd["_meta"]["status"] == "VAL-YAPILAMADI" and vd["_meta"]["winner"] is None
    assert a.main(["final", "--root", str(root)]) == 4                       # FINAL'e dokunulmaz
    assert a.main(["decide", "--root", str(root)]) == 0
    assert "VAL-YAPILAMADI" in (root / "DECISION.md").read_text()
    # VAL_MIN_DAYS THRESH'ten okunur
    ctx = a.verify_lock(root)
    assert ctx.window("VAL")[0] == "2024-06-01" and ctx.window_days("VAL") == 170


def test_g16_and_g11_use_full_months_only_partial_month_trades_excluded():
    m = a.full_months_of(a.WINDOWS["VAL"])
    assert len(m) == 22 and m[0] == "2023-01" and m[-1] == "2024-10" and "2024-11" not in m
    cut = a.full_months_of(("2023-04-01", "2024-11-18"))
    assert len(cut) == 19 and cut[0] == "2023-04"                              # M ve G16 yarıları otomatik güncellenir
    ts = [_tr(1.0, month="2024-11"), _tr(1.0, month="2023-02")]
    assert a.rule_g11(ts, ["2023-01", "2023-02"])["value"] == 1


def test_final_anchor_b0_and_single_shot(pipe, lab_env, tmp_path):
    root = pipe.clone_root("finalized", tmp_path)
    fd = _j(root / "FINAL.json")
    assert fd["b0_anchor"]["ok"] is True and fd["b0_anchor"]["trades"] == 320
    assert 0.895 <= fd["b0_anchor"]["pf"] < 0.905
    assert fd["arm"]["name"] == "S15" and fd["arm"]["status"] == "FINAL-TUTARLI" and fd["outcome"] == "KAZANAN-HARNESS"
    before = (root / "FINAL.json").read_bytes()
    assert a.main(["final", "--root", str(root)]) == 4 and (root / "FINAL.json").read_bytes() == before


def test_final_outcomes_for_anchor_veto_and_inconclusive(pipe, lab_env, tmp_path):
    def run_final(arm_trades, b0_override=None):
        r = pipe.clone_root("validated", tmp_path / f"f{random.random()}")
        ctx = a.verify_lock(r)
        put_run(r, ctx, "FINAL_S15", arm_trades)
        b0 = gen_trades(a.WINDOWS["FINAL"], 320, 7, s_arm=50.0)
        for i, t in enumerate(b0):
            t["pnl"] = 10.0 if i < 232 else -(232 * 10.0 / 0.9) / 88
        if b0_override:
            b0_override(b0)
        put_run(r, ctx, "FINAL_B0", b0)
        assert a.main(["final", "--root", str(r)]) == 0
        return _j(r / "FINAL.json")

    risk = 1000.0 * 9.0 / 100.0

    def fixed_pf(n, pf_target):
        ts = gen_trades(a.WINDOWS["FINAL"], n, 1, s_arm=9.0, **GOOD)
        for i, t in enumerate(ts):
            t["pnl"] = (pf_target * risk) if i < n // 2 else -risk
        return ts

    good60 = fixed_pf(60, 1.5)
    # N < 20 -> SONUÇSUZ, kazanan olamaz
    f = run_final(fixed_pf(19, 1.5))
    assert f["arm"]["status"] == "SONUÇSUZ" and f["outcome"] == "ADAY-FINAL-SONUÇSUZ"
    # PF < 0.90 -> VETO
    f = run_final(fixed_pf(60, 0.8))
    assert f["arm"]["status"] == "VETO" and f["outcome"] == "FINAL-VETO"
    # 0.90 <= PF < 1.00 -> BELİRSİZ (veto yok, doğrulama da yok)
    mid = fixed_pf(60, 0.95)
    f = run_final(mid)
    assert f["arm"]["status"] == "FINAL-BELİRSİZ" and f["outcome"] == "KAZANAN-HARNESS" and "doğrulamadı" in f["note"]
    # B0 çapası tutmazsa FINAL GEÇERSİZ: iyi arm bile kazanan olamaz
    def break_anchor(b0):
        del b0[0]
    f = run_final(good60, break_anchor)
    assert f["b0_anchor"]["ok"] is False and f["valid"] is False and f["outcome"] == "ADAY-FINAL-SONUÇSUZ"


def test_decide_writes_decision_md_and_results_csv_with_six_decimals(pipe, lab_env, tmp_path):
    root = pipe.clone_root("decided", tmp_path)
    md = (root / "DECISION.md").read_text(encoding="utf-8")
    assert "KAZANAN-HARNESS" in md and "Testnet adayı" in md and "G9a" in md
    assert "bu veriyle karara bağlanamaz" in md
    assert "DOWN PF" in md and "boğa maddesi" in md                     # CLAUDE.md kural #1 satırları
    assert "S5 → S5G" in md and "Z0 S5" in md
    rows = list(csv.DictReader(open(root / "results.csv", encoding="utf-8")))
    assert list(rows[0].keys()) == a.CSV_COLS
    by = {r["label"]: r for r in rows}
    assert "DEV_B0_Q1" in by and by["DEV_B0_Q1"]["quarter"] == "1" and "FINAL_B0" in by and "VAL_S15" in by
    pf = by["DEV_S15"]["pf"]
    assert len(pf.split(".")[1]) == 6                                   # CSV'de 6 hane
    assert by["DEV_S5"]["pf_pess"] != "" and by["DEV_S5"]["pf_opt"] != ""
    assert float(by["DEV_S5"]["pf_opt"]) > float(by["DEV_S5"]["pf_pess"])
    assert by["DEV_S15"]["s_arm"] == "9" and by["DEV_T30"]["s_arm"] == "50"
    assert not any(r["label"].startswith("SMOKE") for r in rows)


def test_decide_outcome_labels_from_stage_files(tmp_path):
    def mk(files: Dict[str, Any]) -> Path:
        d = tmp_path / f"o{random.random()}"
        d.mkdir()
        for k, v in files.items():
            (d / k).write_text(json.dumps(v))
        return d

    assert a.decide_outcome(mk({"DEV_DECISION.json": {"advanced": []}}))[0] == "DEV-YOK"
    with pytest.raises(a.StageError):
        a.decide_outcome(mk({"DEV_DECISION.json": {"advanced": ["S15"]}}))
    base = {"DEV_DECISION.json": {"advanced": ["S15"]}}
    assert a.decide_outcome(mk({**base, "VAL_DECISION.json": {"_meta": {"winner": None, "status": "TAMAM"}}}))[0] == "VAL-YOK"
    assert a.decide_outcome(mk({**base, "VAL_DECISION.json": {"_meta": {"winner": None, "status": "VAL-YAPILAMADI"}}}))[0] == "VAL-YAPILAMADI"
    with pytest.raises(a.StageError):
        a.decide_outcome(mk({**base, "VAL_DECISION.json": {"_meta": {"winner": "S15"}}}))
    assert a.decide_outcome(mk({**base, "VAL_DECISION.json": {"_meta": {"winner": "S15"}},
                                "FINAL.json": {"outcome": "FINAL-VETO"}}))[0] == "FINAL-VETO"
    assert a.final_outcome(True, "FINAL-TUTARLI") == "KAZANAN-HARNESS" and a.final_outcome(False, "FINAL-TUTARLI") == "ADAY-FINAL-SONUÇSUZ"
    assert a.final_outcome(True, "SONUÇSUZ") == "ADAY-FINAL-SONUÇSUZ" and a.final_outcome(True, "VETO") == "FINAL-VETO"


def test_write_once_is_exclusive(tmp_path):
    p = tmp_path / "x.json"
    a.write_once(p, "1")
    with pytest.raises(a.StageError):
        a.write_once(p, "2")
    assert p.read_text() == "1" and not list(tmp_path.glob("*.tmp*"))


# ==========================================================================
# 10) smoke (S1/S2/S3), manifest, G13/DD_R ve rapor alanları
# ==========================================================================
def _smoke_reports(root: Path, ctx: "a.Ctx", *, s1_ok=True, s2_ok=True) -> None:
    def s1(tag, win):
        n, wr, pf, pnl, dd = a.SMOKE_S1_EXPECT[tag]
        wins = round(n * wr / 100)
        g = gen_trades(win, n, 4, s_arm=50.0)
        # PF ve PnL'i hedefe oturt: toplam kazanç W, toplam kayıp L: W - L = pnl ve W / L = pf  =>  L = pnl / (pf - 1)
        loss_n = n - wins
        ly = pnl / (pf - 1.0)
        wx = pf * ly
        for i, t in enumerate(g):
            t["pnl"] = wx / wins if i < wins else -ly / loss_n
        doc = make_report(ctx, f"SMOKE_B0_{tag}", g, dd_r=1.0)
        doc["overall"]["winrate"] = wins / n * 100.0 if s1_ok else 50.0
        doc["overall"]["total_pnl"] = sum(t["pnl"] for t in g)
        doc["overall"]["profit_factor"] = a.pf_of([t["pnl"] for t in g])
        if dd is not None:
            doc["overall"]["bar_max_drawdown"] = dd
        label = f"SMOKE_B0_{tag}"
        (root / "sealed" / "runs").mkdir(parents=True, exist_ok=True)
        p = root / "sealed" / "runs" / f"{label}.json"
        p.write_text(json.dumps(doc))
        a.Ledger(root).append({"label": label, "utc": "u", "seconds": 1, "sha256": a.sha256_file(p), "git_sha": ctx.h})

    s1("AYI", a.WINDOWS["S1_AYI"])
    s1("V1", a.WINDOWS["S1_V1"])
    g = gen_trades(a.WINDOWS["SMOKE_W"], 20, 5, s_arm=9.0)
    put_run(root, ctx, "SMOKE_S15_h0", g)
    g2 = [dict(t) for t in g]
    if not s2_ok:
        g2[3]["pnl"] += 1.0
    put_run(root, ctx, "SMOKE_S15_h1", g2)
    for arm in a.FAMILY + a.DESCRIPTIVE:
        put_run(root, ctx, f"SMOKE_{arm}_W", gen_trades(a.WINDOWS["SMOKE_W"], 15, 6, s_arm=_arm_s(ctx, arm)))
    put_run(root, ctx, "SMOKE_R1_M1", gen_trades(a.WINDOWS["SMOKE_1M"], 15, 6, s_arm=50.0))


def test_smoke_pass_and_each_failure_mode(lab, lab_env, tmp_path, capsys):
    root = lab.clone_root("locked", tmp_path)
    ctx = a.verify_lock(root)
    assert a.main(["smoke", "--root", str(root)]) == 4                     # koşular yok
    _smoke_reports(root, ctx)
    assert a.main(["smoke", "--root", str(root)]) == 0
    out = capsys.readouterr().out
    assert "S1 B0 AYI: PASS" in out and "S2 belirlenimcilik: PASS" in out and "S3 konfigürasyon: PASS" in out
    assert "3x payı" in out and "20x payı" in out                            # yalnız basılır, kapı değil
    res = _j(root / "SMOKE.json")
    assert res["pass"] is True and res["S3"]["R1"]["n"] == 15
    r2 = tmp_path / "r2"
    shutil.copytree(lab.base / "snap_locked", r2)
    _smoke_reports(r2, ctx, s2_ok=False)
    assert a.main(["smoke", "--root", str(r2)]) == 5 and _j(r2 / "SMOKE.json")["S2"]["pass"] is False
    r3 = tmp_path / "r3"
    shutil.copytree(lab.base / "snap_locked", r3)
    _smoke_reports(r3, ctx, s1_ok=False)
    assert a.main(["smoke", "--root", str(r3)]) == 5 and _j(r3 / "SMOKE.json")["S1_AYI"]["pass"] is False


def test_smoke_s1_expected_values_are_the_e16_numbers():
    assert a.SMOKE_S1_EXPECT["AYI"] == (149, 87.25, 2.0284, 5703.2322, 1428.4953)
    assert a.SMOKE_S1_EXPECT["V1"][:4] == (194, 79.38, 0.8630, -1868.1712)


def test_manifest_records_cache_hashes_and_stage_gate(lab, lab_env, tmp_path):
    root = lab.clone_root("locked", tmp_path)
    ctx = a.verify_lock(root)
    cache = root / "cache"
    # BTC 1d etiket verisi (lab'dan kalır) VAL/FINAL yasak kontrolünden MUAF
    assert a.main(["manifest", "smoke", "--root", str(root)]) == 5            # beklenen duman dosyaları yok
    # eksik dosyaları sentetik üret
    def fill(set_name):
        for tri, s, e in a.manifest_scope(ctx, set_name):
            for sym in a.SYMBOLS_PINNED.split(","):
                for iv, st, en in a.role_windows(tri, s, e):
                    p = cache / f"{sym}_{iv}_{st}_{en}.json.gz"
                    if not p.exists():
                        with gzip.open(p, "wt") as f:
                            json.dump([{"open_time": st, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1, "close_time": st + 1}], f)
    fill("smoke")
    assert a.main(["manifest", "smoke", "--root", str(root)]) == 0
    man = _j(root / "MANIFEST.data.json")
    some = next(v for k, v in man.items() if k.startswith("ETHUSDT_5m_"))
    assert some["set"] == "smoke" and some["fill_phase"] == "P1b" and some["n_candles"] == 1 and len(some["sha256"]) == 64
    assert {"sha256", "bytes", "symbol", "interval", "n_candles", "first_open_time", "last_open_time", "set", "fill_phase"} <= set(some)
    # 30m/4h/4h: iki AYRI 4h önbellek anahtarı beklenir ve kayıtlıdır
    s30 = [k for k in man if k.startswith("BTCUSDT_4h_") and k.endswith(f"_{a.date_ms('2026-05-01')}.json.gz")]
    assert len(s30) >= 2
    # değişen önbellek dosyası -> bütünlük hatası
    victim = cache / next(iter(man))
    with gzip.open(victim, "wt") as f:
        json.dump([{"open_time": 1, "open": 2, "high": 2, "low": 2, "close": 2, "volume": 2, "close_time": 2}], f)
    assert a.main(["manifest", "smoke", "--root", str(root)]) == 5
    # VAL verisi aşama kapısından önce fiziksel olarak VAR ise dev/smoke manifest reddeder
    fill("dev")
    (cache / f"ETHUSDT_5m_1_{a.date_ms(a.VAL_END)}.json.gz").write_bytes(b"x")
    assert a.main(["manifest", "dev", "--root", str(root)]) == 5


def test_dd_r_calmar_and_report_fields():
    s = _series(15.0, 2000.0)
    assert a.dd_r_of(s) == pytest.approx(20.0) and a.calmar_r(15.0, 365, 20.0) == pytest.approx(0.75)
    assert a.calmar_r(5.0, 100, 0.0) == INF and a.calmar_r(-5.0, 100, 0.0) == -INF
    ctx = stub_ctx(Path("."))
    rp = a.arm_report(ctx, s, {}, "dev", extra_p=False)
    assert {"months", "groups", "dir_x_regime", "symbols", "exits", "lev_hist", "signals", "eod", "dd_r", "calmar_r"} <= set(rp)
    assert rp["dir"]["LONG"]["n"] + rp["dir"]["SHORT"]["n"] == 10


def test_set_windows_match_plan_constants():
    d = lambda w: (a.date_ms(w[1]) - a.date_ms(w[0])) // DAY          # noqa: E731
    assert d(a.WINDOWS["DEV"]) == 365 and d(a.WINDOWS["VAL"]) == 687 and d(a.WINDOWS["FINAL"]) == 35
    assert sum(d(q) for q in a.DEV_QUARTERS) == 365


# ==========================================================================
# 11) FIXER turu: gitignore'lu JSON'lar, S_arm/özet denetimi, aşama sırası, istatistik bilinen değerler, çıkış kodları
# ==========================================================================
def test_lock_force_adds_gitignored_json_files_into_l_commit(lab, lab_env):
    """Depo .gitignore'u `*.json`ı yok sayar; düz `git add` LOCK.json/variants.json/... dosyalarını SESSİZCE atlardı."""
    ref = json.loads((lab.root / "LOCK_REF.json").read_text())
    _git(lab.repo, "check-ignore", "-q", "--no-index", f"{a.LOCKDIR_REL}/variants.json")             # gerçekten yok sayılıyor (rc 0)
    tree = set(_git(lab.repo, "ls-tree", "-r", "--name-only", ref["l"]).splitlines())
    lock = json.loads(subprocess.run(["git", "-C", str(lab.repo), "show", f"{ref['l']}:{a.LOCKDIR_REL}/LOCK.json"],
                                     capture_output=True, check=True).stdout)
    for n in [*lock["files"], "LOCK.json"]:
        assert f"{a.LOCKDIR_REL}/{n}" in tree, n
    assert a.main(["verify-lock", "--root", str(lab.root)]) == 0


def test_lock_aborts_and_reverts_when_commit_lacks_a_locked_file(lab, lab_env, tmp_path, monkeypatch):
    root = lab.clone_root("locked", tmp_path)
    orig_l = json.loads((root / "LOCK_REF.json").read_text())["l"]
    ref_before = (root / "LOCK_REF.json").read_text()
    real = a.git_tree_paths
    monkeypatch.setattr(a, "git_tree_paths",
                        lambda rev: [p for p in real(rev) if not p.endswith("/LOCK.json")])
    try:
        assert a.main(["lock", "--root", str(root)]) == 5
        assert _git(lab.repo, "rev-parse", "HEAD") == orig_l                       # commit geri alındı
        assert (root / "LOCK_REF.json").read_text() == ref_before                  # kayıt yazılmadı, push komutu basılmadı
    finally:
        _git(lab.repo, "reset", "-q", "--hard", orig_l)


def test_lock_rejects_variants_snapshot_hash_mismatch_and_accepts_match(lab, lab_env, tmp_path, capsys):
    root = lab.clone_root("locked", tmp_path)
    orig_l = json.loads((root / "LOCK_REF.json").read_text())["l"]
    vp = lab.repo / a.LOCKDIR_REL / "variants.json"
    doc = json.loads(vp.read_text())
    try:
        vp.write_text(json.dumps({**doc, "snapshot": {"sha256": "0" * 64}}))
        assert a.main(["lock", "--root", str(root)]) == 5
        assert _git(lab.repo, "rev-parse", "HEAD") == orig_l
        vp.write_text(json.dumps({**doc, "snapshot": {"sha256": a.sha256_file(root / "server-scalper-env.txt")}}))
        assert a.main(["lock", "--root", str(root)]) == 0
        assert a.main(["verify-lock", "--root", str(root)]) == 0
    finally:
        _git(lab.repo, "reset", "-q", "--hard", orig_l)


def test_lock_refuses_when_leak_wall_is_violated(lab, lab_env, tmp_path, monkeypatch):
    root = lab.clone_root("locked", tmp_path)
    head = _git(lab.repo, "rev-parse", "HEAD")
    ref_before = (root / "LOCK_REF.json").read_text()
    monkeypatch.setattr(a, "earliest_bar_check", lambda v: {"DEV:15m/1h/4h": "2024-11-18T00:00:00Z"})
    assert a.main(["lock", "--root", str(root)]) == 5
    assert _git(lab.repo, "rev-parse", "HEAD") == head and (root / "LOCK_REF.json").read_text() == ref_before


def test_required_code_includes_fill_script_and_lock_hashes_it(lab):
    assert a.FILL_SCRIPT_REL in a.REQUIRED_CODE
    lock = json.loads((lab.repo / a.LOCKDIR_REL / "LOCK.json").read_text())
    assert a.FILL_SCRIPT_REL in lock["code_blobs"]


def test_verify_lock_working_copy_lock_json_must_equal_l_blob(lab, lab_env, tmp_path):
    root = lab.clone_root("locked", tmp_path)
    lp = lab.repo / a.LOCKDIR_REL / "LOCK.json"
    orig = lp.read_text()
    try:
        lp.write_text(orig + " ")
        before = _tree_listing(root)
        assert a.main(["verify-lock", "--root", str(root)]) == 3
        assert _tree_listing(root) == before
        lp.unlink()
        assert a.main(["verify-lock", "--root", str(root)]) == 3
    finally:
        lp.write_text(orig)
    assert a.main(["verify-lock", "--root", str(root)]) == 0


def test_verify_lock_detects_requirements_and_python_version_tamper(lab, lab_env, tmp_path, monkeypatch):
    root = lab.clone_root("locked", tmp_path)
    real = a.git_show
    monkeypatch.setattr(a, "git_show", lambda rev, path: real(rev, path) + (b"x" if path == a.REQ_REL else b""))
    assert a.main(["verify-lock", "--root", str(root)]) == 3
    monkeypatch.setattr(a, "git_show", real)
    assert a.main(["verify-lock", "--root", str(root)]) == 0
    monkeypatch.setattr(a.platform, "python_version", lambda: "2.7.18")
    assert a.main(["verify-lock", "--root", str(root)]) == 3


def test_verify_lock_malformed_lock_record_is_exit_3_not_5(lab, lab_env, tmp_path, monkeypatch):
    root = lab.clone_root("locked", tmp_path)

    def boom(*_a, **_k):
        raise KeyError("h_tree")

    monkeypatch.setattr(a, "_verify_lock", boom)
    assert a.main(["verify-lock", "--root", str(root)]) == 3
    monkeypatch.undo()
    (root / "LOCK_REF.json").write_text("{not json")
    for k, v in FAST.items():
        a.THRESH[k] = v
    assert a.main(["verify-lock", "--root", str(root)]) == 3


def test_remote_check_requires_l_on_origin_when_a_remote_exists(lab, lab_env, tmp_path):
    root = lab.clone_root("locked", tmp_path)
    l = json.loads((root / "LOCK_REF.json").read_text())["l"]
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    _git(lab.repo, "remote", "add", "origin", str(bare))
    try:
        assert a.main(["verify-lock", "--root", str(root)]) == 3                  # L uzağa itilmedi: kilit YOKTUR
        a.verify_lock(root, remote=False)                                          # check-run uzak denetimini atlar
        with pytest.raises(a.LockMismatch):
            a.verify_lock(root)
        _git(lab.repo, "push", "-q", "origin", f"{l}:refs/heads/main-18w3qz")
        assert a.main(["verify-lock", "--root", str(root)]) == 0
        _git(lab.repo, "remote", "set-url", "origin", str(tmp_path / "nonexistent.git"))     # ağ/uzak hatası: atlanır
        assert a.main(["verify-lock", "--root", str(root)]) == 0
    finally:
        _git(lab.repo, "remote", "remove", "origin")


def test_unexpected_exceptions_map_to_exit_5(monkeypatch, tmp_path):
    def raising(exc):
        def f(root, target):
            raise exc
        return f

    for exc in (AssertionError("x"), IndexError("i"), RuntimeError("r"), ZeroDivisionError()):
        monkeypatch.setitem(a.DISPATCH, "decide", raising(exc))
        assert a.main(["decide", "--root", str(tmp_path)]) == 5
    with pytest.raises(a.IntegrityError):
        a.need(None, "x")
    assert a.need(3, "x") == 3


# --- check-report: S_arm ve sunucu özeti ---------------------------------------------------------
@pytest.mark.parametrize("label", ["DEV_T15", "DEV_B0_Q1", "FINAL_B0", "DEV_S5G", "DEV_T30"])
def test_check_report_verifies_s_arm_even_if_arm_env_does_not_list_it(tmp_path, label):
    ctx = stub_ctx(tmp_path)
    info = a.parse_label(label, ctx.variants)
    s, e = a.run_window(ctx, info)
    v = ctx.vid[info.variant]
    if "SCALPER_FIXED_STOP_ROI_PCT" in v["env"]:
        pytest.skip("yalnız env'i S'yi listelemeyen arm'lar")
    tr = gen_trades((s, e), 12, 2, s_arm=v["S_arm"])
    ok = make_report(ctx, label, tr)
    assert a.check_report(ctx, info, ok, "ok") == []
    bad = make_report(ctx, label, tr)
    bad["provenance"]["scalper_config"]["scalper_fixed_stop_roi_pct"] = 30.0
    assert any("scalper_fixed_stop_roi_pct" in r and "S_arm" in r for r in a.check_report(ctx, info, bad, "ok"))
    gone = make_report(ctx, label, tr)
    gone["provenance"]["scalper_config"].pop("scalper_fixed_stop_roi_pct")
    assert any("scalper_fixed_stop_roi_pct yok" in r for r in a.check_report(ctx, info, gone, "ok"))


def test_check_report_compares_unlisted_keys_against_local_snapshot(tmp_path, monkeypatch):
    monkeypatch.delenv("E17_SNAPSHOT", raising=False)
    (tmp_path / "server-scalper-env.txt").write_text(
        "SCALPER_TP1_FRACTION=0.5\nSCALPER_ENTRY_MODE=maker\nSCALPER_SYMBOL_ALLOWLIST=BTCUSDT\n"
        "SCALPER_MAKER_FEE_PCT=0.02\nSCALPER_NOT_IN_SETTINGS=1\n# yorum\n", encoding="utf-8")
    ctx = stub_ctx(tmp_path)

    def report(label, **cfg):
        info = a.parse_label(label, ctx.variants)
        s, e = a.run_window(ctx, info)
        doc = make_report(ctx, label, gen_trades((s, e), 12, 2, s_arm=ctx.vid[info.variant]["S_arm"]))
        base = {"scalper_tp1_fraction": 0.5, "scalper_entry_mode": "maker", "scalper_maker_fee_pct": 0.02}
        doc["provenance"]["scalper_config"].update({**base, **cfg})
        return a.check_report(ctx, info, doc, "ok")

    assert report("DEV_T15") == []                                            # özetle uyumlu; allowlist/olmayan anahtar yok sayılır
    bad = report("DEV_T15", scalper_entry_mode="taker")
    assert any("scalper_entry_mode" in r and "özet" in r for r in bad)
    assert not any("maker" in r for r in bad)                                  # özet DEĞERİ neden metnine sızmaz
    assert any("scalper_tp1_fraction" in r for r in report("DEV_T15", scalper_tp1_fraction=0.7))
    assert report("DEV_T15", scalper_symbol_allowlist="XYZ") == []           # evren CLI ile belirlenir: atlanır
    assert report("DEV_S15_ROB_TAKER", scalper_entry_mode="taker") == []     # kaplama env'i özetin yerine geçer
    assert report("DEV_S15_ROB_FEE2", scalper_maker_fee_pct=0.04) == []      # ücret stresinde maliyet alanları atlanır
    assert any("scalper_maker_fee_pct" in r for r in report("DEV_S15", scalper_maker_fee_pct=0.04))


# --- check-run: bozuk rapor, aşama sırası, duman kapısı ----------------------------------------------
def test_check_run_malformed_report_types_fail_cleanly_and_delete_all_outputs(lab, lab_env, tmp_path, capsys):
    root = lab.clone_root("locked", tmp_path)
    ctx = a.verify_lock(root)
    write_smoke_pass(root, ctx)
    doc = _stage_files(root, ctx, "DEV_S15", gen_trades(a.WINDOWS["DEV"], 25, 9))
    rp = root / "sealed" / "runs" / "DEV_S15.json"
    doc["provenance"]["cost_model"]["stress_multiplier"] = None               # float(None) -> TypeError
    rp.write_text(json.dumps(doc))
    sec = root / "sealed" / "logs" / "DEV_S15.sec"
    sec.write_text("99\n")
    assert a.main(["check-run", "DEV_S15", "--root", str(root)]) == 5
    assert "CHECK FAIL DEV_S15" in capsys.readouterr().err
    assert not rp.exists() and not sec.exists() and not (root / "sealed" / "logs" / "DEV_S15.log").exists()
    assert not (root / "ledger.jsonl").exists()


def test_dev_requires_a_passing_smoke_for_this_lock(lab, lab_env, tmp_path):
    root = lab.clone_root("locked", tmp_path)
    ctx = a.verify_lock(root)
    _stage_files(root, ctx, "DEV_S15", gen_trades(a.WINDOWS["DEV"], 25, 9))
    rp = root / "sealed" / "runs" / "DEV_S15.json"
    assert a.main(["check-run", "DEV_S15", "--root", str(root)]) == 4          # SMOKE.json yok
    (root / "SMOKE.json").write_text(json.dumps({"pass": False, "l": ctx.l}))
    assert a.main(["check-run", "DEV_S15", "--root", str(root)]) == 4          # FAIL
    (root / "SMOKE.json").write_text(json.dumps({"pass": True, "l": "e" * 40}))
    assert a.main(["check-run", "DEV_S15", "--root", str(root)]) == 4          # başka kilit için
    assert rp.exists() and not (root / "ledger.jsonl").exists()                # ön koşul ihlali çıktıyı SİLMEZ
    (root / "SMOKE.json").write_text("{kötü")
    assert a.main(["dev-screen", "--root", str(root)]) == 4
    write_smoke_pass(root, ctx)
    assert a.main(["check-run", "DEV_S15", "--root", str(root)]) == 0
    assert a.main(["check-run", "SMOKE_B0_AYI", "--root", str(root)]) == 5      # SMOKE koşuları ön koşulsuzdur (rapor yok -> 5)


def _stage_and_check(root: Path, label: str) -> int:
    ctx = a.verify_lock(root, remote=False)
    info = a.parse_label(label, ctx.variants)
    s, e = a.run_window(ctx, info)
    put_run(root, ctx, label, gen_trades((s, e), 12, 3, s_arm=ctx.vid[info.variant]["S_arm"]), ledger=False)
    return a.main(["check-run", label, "--root", str(root)])


def test_check_run_stage_ordering_dev_overlays(pipe, lab_env, tmp_path):
    r = pipe.clone_root("screened", tmp_path)                                  # dev-screen bitti, DEV_DECISION yok
    assert _stage_and_check(r, "VAL_S15") == 4                                 # DEV kararı olmadan VAL yok
    assert _stage_and_check(r, "DEV_S15_DIAG_Z0") == 4                         # teşhis DEV kararından sonra
    assert _stage_and_check(r, "DEV_S5_ROB_FEE2") == 4                         # S5 G0-G5'i geçmedi: G6 koşusu yok
    assert _stage_and_check(r, "DEV_S15_ROB_FEE2") == 0
    d = pipe.clone_root("dev_decided", tmp_path / "d")                         # advanced = S15, T30; S30 FEE2'de elendi
    assert _stage_and_check(d, "DEV_S15_DIAG_Z0") == 0
    assert _stage_and_check(d, "DEV_S5_DIAG_FEE15") == 4                       # FEE2'yi kaçırmadı, ilerlemedi
    assert _stage_and_check(d, "DEV_S30_DIAG_FEE15") == 0                      # FEE2'yi kaçırdı
    assert _stage_and_check(d, "VAL_S5") == 4                                  # DEV'de ilerlemedi
    assert _stage_and_check(d, "VAL_S15") == 0 and _stage_and_check(d, "VAL_S60") == 0 and _stage_and_check(d, "VAL_T60") == 0
    assert _stage_and_check(d, "FINAL_B0") == 4                                # VAL kararı yok


def test_check_run_stage_ordering_final_needs_val_winner(pipe, lab_env, tmp_path):
    v = pipe.clone_root("validated", tmp_path)                                 # kazanan S15
    assert _stage_and_check(v, "FINAL_T30") == 4
    assert _stage_and_check(v, "FINAL_S15") == 0 and _stage_and_check(v, "FINAL_B0") == 0
    nowin = pipe.clone_root("dev_decided", tmp_path / "nw")
    (nowin / "VAL_DECISION.json").write_text(json.dumps({"_meta": {"winner": None, "status": "TAMAM"}}))
    assert _stage_and_check(nowin, "FINAL_B0") == 4                            # kazanan yok: FINAL'e dokunulmaz


def test_dev_screen_requires_passing_smoke(pipe, lab_env, tmp_path):
    root = pipe.clone_root("dev_runs", tmp_path)
    (root / "SMOKE.json").unlink()
    assert a.main(["dev-screen", "--root", str(root)]) == 4 and not (root / "DEV_SCREEN.json").exists()


def test_dev_screen_entries_follow_a4_value_threshold_pass_borderline(pipe, lab_env, tmp_path):
    scr = _j(pipe.clone_root("screened", tmp_path) / "DEV_SCREEN.json")
    for arm in a.FAMILY:
        for g in ("G0", "G1", "G2", "G3", "G4", "G5"):
            assert {"value", "threshold", "pass", "borderline"} <= set(scr[arm][g]), (arm, g)
    g0 = scr["T15"]["G0"]                                                      # EOD > 8: kapıyı belirleyen satır
    assert g0["pass"] is False and g0["limiting"] == "G0b" and g0["value"] == 9 and g0["threshold"] == 8


# --- istatistik: bilinen değerler, bağımsız referans, eşitlik, t dağılımı ------------------------------
def test_tstat_known_values_use_ddof_1():
    x = [1.0, 2.0, 3.0, 4.0]
    sd = 1.2909944487358056                                                    # statistics.stdev([1,2,3,4])
    assert a.t_obs_of(x) == pytest.approx(2.5 / (sd / 2.0), rel=1e-12) and a.t_obs_of(x) == pytest.approx(3.872983346207417)
    assert a._tstat(sum(x), sum(v * v for v in x), 4) == pytest.approx(3.872983346207417)
    assert a.t_obs_of([5.0]) == 0.0 and a.t_obs_of([]) == 0.0
    assert a.t_obs_of([2.0, 2.0, 2.0]) == INF                                  # sd = 0, ortalama > 0
    assert a.t_obs_of([0.0, 0.0, 0.0]) == 0.0


def _reference_bootstrap_p(x, block, seed, boot_n):
    """Bağımsız (statistics tabanlı) referans: aynı RNG akışı, açık birleştirme + stdev(ddof=1)."""
    import statistics as st
    d = len(x)
    mean = st.fmean(x)
    xc = [v - mean for v in x]
    t_obs = mean / (st.stdev(x) / d ** 0.5)
    rnd = random.Random(seed).random
    cnt = 0
    for _ in range(boot_n):
        s: List[float] = []
        for _k in range(-(-d // block)):
            st0 = int(rnd() * d)
            s.extend(xc[(st0 + j) % d] for j in range(block))
        s = s[:d]
        sd = st.stdev(s)
        t = (st.fmean(s) / (sd / d ** 0.5)) if sd > 1e-12 else 0.0
        if t >= t_obs:
            cnt += 1
    return (1 + cnt) / (boot_n + 1)


@pytest.mark.parametrize("seed,block,d", [(1, 7, 60), (2, 7, 61), (3, 28, 90), (4, 14, 45)])
def test_bootstrap_p_matches_independent_statistics_reference(seed, block, d):
    rng = random.Random(seed)
    x = [rng.gauss(0.08, 1.0) if rng.random() < 0.6 else 0.0 for _ in range(d)]
    assert a.bootstrap_p(x, block, 777 + seed, 250)[0] == pytest.approx(_reference_bootstrap_p(x, block, 777 + seed, 250), abs=1e-12)


def test_exceedance_count_is_inclusive_of_ties(monkeypatch):
    monkeypatch.setattr(a, "_tstat", lambda s1, s2, n: 1.25)                   # her t* == t_obs
    p, _t = a.bootstrap_p([1.0, 2.0, 0.5, 3.0], 2, 7, 99)
    assert p == 1.0                                                            # (1 + 99) / (99 + 1); '>' olsaydı 0.01


def _t_cdf_by_integration(t: float, df: int, n: int = 40000) -> float:
    import math
    c = math.exp(math.lgamma((df + 1) / 2) - math.lgamma(df / 2)) / math.sqrt(df * math.pi)
    f = lambda u: c * (1 + u * u / df) ** (-(df + 1) / 2)          # noqa: E731
    h = abs(t) / n
    acc = f(0) + f(abs(t)) + sum((4 if i % 2 else 2) * f(i * h) for i in range(1, n))
    half = acc * h / 3.0
    return 0.5 + math.copysign(half, t)


@pytest.mark.parametrize("t,df", [(1.0, 1), (2.0, 10), (3.872983346207417, 3), (-1.3, 21), (0.4, 11), (0.0, 5)])
def test_t_sf_matches_numerical_integration(t, df):
    assert a.t_sf(t, df) == pytest.approx(1.0 - _t_cdf_by_integration(t, df), abs=1e-8)
    assert a.t_sf(1.0, 1) == pytest.approx(0.25, abs=1e-12)                    # Cauchy: kapalı form
    assert a.t_sf(INF, 4) == 0.0 and a.t_sf(-INF, 4) == 1.0


def test_month_level_t_is_one_sided_t_over_full_months_only():
    months = ["2025-01", "2025-02", "2025-03", "2025-04"]
    ts = [_tr(1.0, month="2025-01"), _tr(2.0, month="2025-02"), _tr(3.0, month="2025-03"), _tr(4.0, month="2025-04"),
          _tr(100.0, month="2025-05")]                                          # tam ay listesinde değil -> dışarıda
    mt = a.month_level_t(ts, months)
    assert mt["m"] == 4 and mt["t"] == pytest.approx(3.872983346207417) and mt["p"] == pytest.approx(0.01527, abs=2e-4)
    assert a.month_level_t(ts, months[:2]) is None                              # < 3 tam ay
    flat = a.month_level_t([_tr(1.0, month=m) for m in months], months)
    assert flat["t"] == INF and flat["p"] == 0.0
    zero = a.month_level_t([], months)                                           # işlemsiz: ortalama 0, sd 0
    assert zero["p"] == 1.0


def test_pf_r_caps_lossless_series_and_reports_are_finite(tmp_path):
    assert a.pf_r([_tr(1.0)]) == a.THRESH["PF_CAP"] == 5.0 and a.pf_r([_tr(1.0), _tr(-1.0)]) == 1.0
    ctx = stub_ctx(tmp_path)
    s = a.Series(label="x", arm="S15", set="DEV", overlay="", s_arm=9.0, trades=[_tr(1.0, day=dt.date(2025, 3, 5))],
                 windows=[(dt.date(2025, 1, 1), 365)], missed={}, dd_usd=10.0, risk_median=100.0)
    rp = a.arm_report(ctx, s, {}, "dev", extra_p=False)
    assert rp["pf"] == 5.0 and "inf" not in json.dumps(a.jsonable(rp["groups"]))
    assert a.fmt_pf(5.0) == "5.0000+" and a.fmt_pf(4.99999) == "5.0000"
    assert a.f4(INF) == "inf"                                                   # DD_R/Calmar sonsuzu PF gibi basılmaz


def test_tf_trend_bull_check_and_ci_sentence():
    assert a.tf_trend([1.0, 1.1, 1.2]) == "monoton artan" and a.tf_trend([1.2, 1.1, 1.0]) == "monoton azalan"
    assert a.tf_trend([1.0, 1.3, 1.1]) == "monoton değil"
    assert a.bull_check(1.0, 0.0) is None and a.bull_check(-5.0, -2.0) is None
    reports = {"S15": {"pf_ci90": (0.8, 1.3)}, "S5": {"pf_ci90": (0.7, 1.1)}, "VAL_S30": {"pf_ci90": (0.9, 1.4)},
               "VAL_S60": {"pf_ci90": (0.1, 9.0)}}                              # S60 aile dışı: sayılmaz
    s = a.ci_upper_sentence(reports, "VAL-YOK")
    assert "1.4000" in s and "VAL_S30" in s and "9.0000" not in s
    assert a.ci_upper_sentence(reports, "KAZANAN-HARNESS") == "" and a.ci_upper_sentence({}, "DEV-YOK") == ""


def test_val_p_refines_only_when_alpha_over_k_is_inside_the_mc_band(monkeypatch):
    for k, v in FAST.items():
        monkeypatch.setitem(a.THRESH, k, v)
    calls: List[bool] = []

    def fake(series, stage, block, hi=False, boot_n=None):
        calls.append(hi)
        return (0.011 if hi else 0.012), 1.5

    monkeypatch.setattr(a, "p_for", fake)
    p, t, refined = a.val_p(None, 7, 0.025)             # 0.012 ± 3·SE aralığı 0.025'i keser -> B=199999 ile yeniden
    assert refined is True and p == 0.011 and calls == [False, True]
    calls.clear()
    p, t, refined = a.val_p(None, 7, 0.05)              # /k UYGULANMASAYDI (α=0.05) yeniden hesap olmazdı
    assert refined is False and p == 0.012 and calls == [False]


def test_cmd_val_passes_alpha_over_k_to_the_mc_rule(pipe, lab_env, tmp_path, monkeypatch):
    root = pipe.clone_root("validated", tmp_path)
    (root / "VAL_DECISION.json").unlink()
    seen: List[tuple] = []
    real = a.val_p
    monkeypatch.setattr(a, "val_p", lambda ser, block, al: (seen.append((block, round(al, 6))) or real(ser, block, al)))
    assert a.main(["val", "--root", str(root)]) == 0
    assert seen == [(7, 0.025), (28, 0.05), (7, 0.025), (28, 0.05)]            # k = 2 arm: α/k


def test_calmar_annualises_with_actual_window_days_not_365():
    assert not a.rule_g13(_series(28.0, 2000.0, days=687))["pass"]             # 28·365/687/20 = 0.744 < 0.75
    assert a.rule_g13(_series(28.0, 2000.0, days=365))["pass"]                 # aynı veri 365 günde 1.4
    assert a.rule_g13(_series(28.6, 2000.0, days=687))["pass"]


def test_reaper_is_not_counted_as_eod_and_g12_zero_total_fails_without_crash():
    assert a.eod_count([_tr(1.0, reason="REAPER"), _tr(1.0, reason="EOD"), _tr(1.0, reason="TRAIL")]) == 1
    g = a.rule_g12(_lab_trades([(1.0, "BTCUSDT", "m1"), (-1.0, "ETHUSDT", "m2")]))     # T == 0
    assert g["pass"] is False and g["rows"][0]["note"] == "T <= 0"


def test_g16_splits_odd_month_counts_earlier_half_smaller():
    ms = ["2023-01", "2023-02", "2023-03"]
    ts = _lab_trades([(1.0, "BTCUSDT", "2023-01")] * 3 + [(1.0, "BTCUSDT", "2023-02")] * 5 + [(-1.0, "BTCUSDT", "2023-03")] * 7)
    rows = {r["gate"]: r for r in a.rule_g16(ts, ms)["rows"]}
    assert rows["G16.1n"]["value"] == 3 and rows["G16.2n"]["value"] == 12        # 1 ay | 2 ay


def test_median_risk_and_b0_anchor_pf_are_usd_based():
    doc = {"trades": [{"symbol": "BTCUSDT", "direction": "LONG", "entry_time": a.date_ms("2025-02-03"),
                       "exit_time": a.date_ms("2025-02-04"), "margin_usdt": m, "pnl": 1.0, "leverage": 10, "exit_reason": "TRAIL"}
                      for m in (1000.0, 1000.0, 4000.0)]}
    _trs, med = a.trades_from_report(doc, 10.0, "x")
    assert med == 100.0                                                         # medyan, ortalama (200) DEĞİL
    ts = [_tr(0.1, risk=100.0), _tr(-0.025, risk=400.0)]                        # pnl +10 / -10
    assert a.b0_pf_usd(ts) == 1.0 and a.pf_r(ts) == pytest.approx(4.0)


def test_final_anchor_uses_usd_pf_even_when_margins_differ(pipe, lab_env, tmp_path):
    r = pipe.clone_root("validated", tmp_path)
    ctx = a.verify_lock(r)
    put_run(r, ctx, "FINAL_S15", gen_trades(a.WINDOWS["FINAL"], 60, 1, s_arm=9.0, **GOOD))
    b0 = gen_trades(a.WINDOWS["FINAL"], 320, 7, s_arm=50.0)
    for i, t in enumerate(b0):
        t["pnl"] = 10.0 if i < 232 else -(232 * 10.0 / 0.9) / 88                # USD PF tam 0.90
    for t in b0[-20:]:
        t["margin_usdt"] = 5000.0                                               # R-PF ayrışır, USD-PF aynı kalır
    put_run(r, ctx, "FINAL_B0", b0)
    assert a.main(["final", "--root", str(r)]) == 0
    assert _j(r / "FINAL.json")["b0_anchor"]["ok"] is True


def test_decide_report_has_s_vs_t_trend_month_t_and_val_descriptive_block(pipe, lab_env, tmp_path):
    root = pipe.clone_root("decided", tmp_path)
    md = (root / "DECISION.md").read_text(encoding="utf-8")
    assert "S – T karşılaştırması" in md and "TF eğilimi ölçekli (S)" in md and "TF eğilimi yalnız zamanlama (T)" in md
    assert "ay düzeyinde tek-yönlü t (bilgi, M=12 tam ay)" in md and "ay düzeyinde tek-yönlü t (bilgi, M=22 tam ay)" in md
    assert "Açıklayıcı arm'lar, VAL" in md and "VAL_S60" in md and "VAL_T60" in md
    rows = list(csv.DictReader(open(root / "results.csv", encoding="utf-8")))
    assert not any("inf" in (r["pf"] + r["pf_pess"] + r["pf_opt"]) for r in rows)
    assert {"VAL_S60", "VAL_T60"} <= {r["label"] for r in rows}


def test_no_winner_decision_states_pf_ci_upper_bound(pipe, lab_env, tmp_path):
    root = pipe.clone_root("screened", tmp_path)
    ctx = a.verify_lock(root)
    for arm in ("S15", "S30", "T30"):
        for ov in a.G6_OVERLAYS:
            put_run(root, ctx, f"DEV_{arm}_{ov}", gen_trades(a.WINDOWS["DEV"], 250, 3, s_arm=_arm_s(ctx, arm), **BADQ))
    assert a.main(["dev-decide", "--root", str(root)]) == 0
    assert a.main(["decide", "--root", str(root)]) == 0
    md = (root / "DECISION.md").read_text(encoding="utf-8")
    assert "DEV-YOK" in md and "PF %90 GA üst sınırı" in md


def test_val_g10b_pools_dev_and_val_down_month_trades(pipe, lab_env, tmp_path):
    root = pipe.clone_root("validated", tmp_path)
    ctx = a.verify_lock(root)
    labels = a.month_labels(ctx)
    led = a.Ledger(root)
    dev = a.load_series(ctx, "DEV", "S15", "", led)
    val = a.load_series(ctx, "VAL", "S15", "", led)
    n_dev = sum(1 for t in dev.trades if labels.get(t.month) == "DOWN")
    n_val = sum(1 for t in val.trades if labels.get(t.month) == "DOWN")
    assert n_dev > 0 and n_val > 0
    row = _j(root / "VAL_DECISION.json")["S15"]["G10"]["rows"][1]
    assert row["gate"] == "G10b" and row["n"] == n_dev + n_val
