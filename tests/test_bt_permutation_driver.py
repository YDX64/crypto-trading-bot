"""A4 — permütasyon sürücüsü: tüm semboller VE lider seri AYNI permütasyon
indeksini paylaşır (montecarlo-4)."""

from __future__ import annotations

import math

import pytest

from src.strategies.scalper.backtest import (
    LeaderSeries,
    _build_arg_parser,
    _permuted_inputs,
    build_shared_window_plan,
)
from src.strategies.scalper.permutation import aggregate_from
from src.strategies.scalper.types import Candle

STEP = 300_000


def _series(n=120, scale=1.0, first=0, drop=()):
    out = []
    price = 100.0
    for i in range(first, first + n):
        o = price
        c = o * (1.0 + 0.004 * math.sin(i * 0.7) + 0.001 * ((i * 7) % 5 - 2))
        h = max(o, c) * (1.0 + 0.001 * ((i % 4) + 1))
        l = min(o, c) * (1.0 - 0.001 * ((i % 3) + 1))
        price = c
        if i in drop:
            continue
        out.append(Candle(open_time=i * STEP, open=o * scale, high=h * scale,
                          low=l * scale, close=c * scale, volume=10.0,
                          close_time=i * STEP + STEP - 1))
    return out


def _group(entry, k):
    out = []
    for j in range(0, len(entry) - k + 1, k):
        ch = entry[j:j + k]
        out.append(Candle(open_time=ch[0].open_time, open=ch[0].open,
                          high=max(c.high for c in ch), low=min(c.low for c in ch),
                          close=ch[-1].close, volume=sum(c.volume for c in ch),
                          close_time=ch[-1].close_time))
    return out


def _sym(entry):
    ctx = _group(entry, 3)
    return {"entry": entry, "context": ctx, "regime": ctx}


def _leader(entry):
    return LeaderSeries(
        symbol="BTCUSDT",
        entry_close_times=[c.close_time for c in entry],
        entry_closes=[c.close for c in entry],
        daily_close_times=[], daily_closes=[],
        entry_candles=entry,
        daily_candles=_group(entry, 12),
        intraday_candles=_group(entry, 3),
    )


TEST_START = 40 * STEP + STEP  # 40 mumluk ısınma gerçek kalır


def _run(series, symbols, leader=None, seed=7, block_length=1, clamp=True):
    plan = build_shared_window_plan(series, symbols, leader=leader,
                                    test_start_time_ms=TEST_START)
    return plan, _permuted_inputs(plan, seed=seed, block_length=block_length,
                                  clamp=clamp)


def test_two_symbols_share_index_scaled_copy_stays_scaled():
    a = _series()
    b = _series(scale=3.0)            # B = 3 x A  => ortak indeksle B' = 3 x A'
    series = {"A": _sym(a), "B": _sym(b)}
    _, (perm, _leader_out, _stats) = _run(series, ["A", "B"])
    pa, pb = perm["A"][0], perm["B"][0]
    assert len(pa) == len(a) == len(pb)
    changed = 0
    for x, y in zip(pa, pb):
        assert y.close == pytest.approx(3.0 * x.close, rel=1e-9)
        assert y.high == pytest.approx(3.0 * x.high, rel=1e-9)
    for x, r in zip(pa, a):
        changed += abs(x.close - r.close) > 1e-9
    assert changed > 50                 # gerçekten permüte edildi


def test_warmup_prefix_stays_real():
    a = _series()
    _, (perm, _l, _s) = _run({"A": _sym(a)}, ["A"])
    assert perm["A"][0][:41] == a[:41]


def test_leader_permuted_with_same_index():
    a = _series()
    btc = _series(scale=2.0)
    series = {"A": _sym(a), "BTCUSDT": _sym(btc)}
    leader = _leader(btc)
    _, (perm, perm_leader, _s) = _run(series, ["A", "BTCUSDT"], leader=leader)
    assert perm_leader is not None and perm_leader is not leader
    # lider giriş serisi, sembol listesindeki BTCUSDT'nin permüte serisiyle aynı
    assert perm_leader.entry_closes == [c.close for c in perm["BTCUSDT"][0]]
    # ... ve A ile ortak indeks: lider = 2 x A
    for lc, ac in zip(perm_leader.entry_closes, perm["A"][0]):
        assert lc == pytest.approx(2.0 * ac.close, rel=1e-9)
    # gerçek lider serisinden farklı
    assert perm_leader.entry_closes != leader.entry_closes
    # günlük/gün-içi seriler permüte girişten TÜRETİLDİ
    expect_daily = aggregate_from(
        [c for c in perm["BTCUSDT"][0]], leader.daily_candles)
    assert perm_leader.daily_closes == [c.close for c in expect_daily]


def test_leader_without_raw_candles_falls_back_to_real():
    a = _series()
    leader = LeaderSeries(symbol="BTCUSDT", entry_close_times=[1], entry_closes=[1.0],
                          daily_close_times=[], daily_closes=[])
    plan, (_perm, perm_leader, _s) = _run({"A": _sym(a)}, ["A"], leader=leader)
    assert perm_leader is leader
    assert plan.report["leader_permuted"] is False


def test_different_lengths_aligned_on_common_timestamps():
    a = _series(n=120)
    b = _series(n=130, first=-10, scale=3.0)       # B daha uzun, 10 mum önce başlıyor
    # B'nin sonunda da fazladan yok; ortak = A'nın aralığı
    c = _series(n=120, drop=(70,))                 # C'de ortada bir mum eksik
    series = {"A": _sym(a), "B": _sym(b), "C": _sym(c)}
    plan, (perm, _l, _s) = _run(series, ["A", "B", "C"])
    rep = plan.report
    assert rep["common_bars"] == 119 - 0          # C eksik mum: ortak 119 zaman
    assert rep["dropped_bars"]["A"] == 1          # A'nın 70. mumu ortak değil -> düşer
    assert rep["dropped_bars"]["C"] == 0
    # düşen mum permüte serilerde de yok
    times = [x.open_time for x in perm["A"][0]]
    assert 70 * STEP not in times
    # ortak pencerede A ve C aynı indeks: aynı yapı
    pa = {x.open_time: x for x in perm["A"][0]}
    pc = {x.open_time: x for x in perm["C"][0]}
    shared = sorted(set(pa) & set(pc))
    assert len(shared) == 119
    for t in shared[60:]:
        assert pa[t].close == pytest.approx(pc[t].close, rel=1e-9)


def test_same_seed_deterministic_and_seed_changes_result():
    series = {"A": _sym(_series()), "B": _sym(_series(scale=2.0))}
    _, (p1, _a, _b) = _run(series, ["A", "B"], seed=5)
    _, (p2, _c, _d) = _run(series, ["A", "B"], seed=5)
    _, (p3, _e, _f) = _run(series, ["A", "B"], seed=6)
    assert p1["A"][0] == p2["A"][0]
    assert p1["A"][0] != p3["A"][0]


def test_block_length_cli_flag_and_validation():
    args = _build_arg_parser().parse_args(["--permutation-block-length", "12"])
    assert args.permutation_block_length == 12
    assert _build_arg_parser().parse_args([]).permutation_block_length == 1
    series = {"A": _sym(_series())}
    plan = build_shared_window_plan(series, ["A"], leader=None,
                                    test_start_time_ms=TEST_START)
    with pytest.raises(ValueError):
        _permuted_inputs(plan, seed=1, block_length=0, clamp=True)


# ---------------------------------------------------------------------------
# Uçtan uca: gerçek fixture (ağsız), gerçek run_backtest + run_permutation_study
# ---------------------------------------------------------------------------
from dataclasses import dataclass  # noqa: E402

import src.strategies.scalper.backtest as backtest_module  # noqa: E402
from src.strategies.scalper.backtest import (  # noqa: E402
    _parse_utc_date,
    run_backtest,
    run_permutation_study,
    compute_stats,
)
from tests.test_golden_backtest import (  # noqa: E402
    _FIXTURE_DIR, _GoldenCfg, _NetworkForbiddenKlineFetcher, _apply_golden_settings,
)


@dataclass
class _LatchCfg(_GoldenCfg):
    scalper_daily_loss_limit_pct: float = 0.0001   # eşik -0.01 USD: ilk zarar kilitler
    scalper_max_positions: int = 5


async def _golden_run(monkeypatch, cfg, **kw):
    _apply_golden_settings(monkeypatch)
    monkeypatch.setattr(backtest_module, "KlineFetcher", _NetworkForbiddenKlineFetcher)
    meta, series, leaders = {}, {}, []
    trades = await run_backtest(
        days=3, symbols=["BTCUSDT", "ETHUSDT"], strategy_names="C", cfg=cfg,
        run_metadata=meta, series_out=series, leader_out=leaders,
        start_time_ms=_parse_utc_date("2026-08-07"),
        end_time_ms=_parse_utc_date("2026-08-10"),
        cache_dir=_FIXTURE_DIR, base_url="https://x.invalid", **kw,
    )
    return trades, meta, series, leaders


@pytest.mark.asyncio
async def test_e2e_permutation_shares_index_and_reports(monkeypatch):
    cfg = _GoldenCfg()
    trades, meta, series, leaders = await _golden_run(monkeypatch, cfg)
    real = compute_stats(trades)
    start = meta["test_window"]["start_ms"]
    study = run_permutation_study(
        series, ["BTCUSDT", "ETHUSDT"], "C", cfg, real, permutations=3,
        seed=11, test_start_time_ms=start, leader=leaders[0], progress=False,
    )
    again = run_permutation_study(
        series, ["BTCUSDT", "ETHUSDT"], "C", cfg, real, permutations=3,
        seed=11, test_start_time_ms=start, leader=leaders[0], progress=False,
    )
    assert study["null_samples"] == again["null_samples"]      # deterministik
    rep = study["shared_index"]
    assert rep["shared_index"] is True
    assert sorted(rep["symbols"]) == ["BTCUSDT", "ETHUSDT"]
    assert rep["permuted_bars"] > 0 and study["block_length"] == 1
    assert all("trades" in row for row in study["null_samples"])
    # lider kapalı koşuda (golden cfg) gerçek lider yok -> permüte edilmedi
    assert rep["leader_permuted"] is False


@pytest.mark.asyncio
async def test_e2e_latch_off_reproduces_old_output_and_on_drops(monkeypatch):
    base_trades, _m, _s, _l = await _golden_run(monkeypatch, _LatchCfg(
        scalper_daily_loss_limit_pct=0.0))
    off_trades, off_meta, _s, _l = await _golden_run(
        monkeypatch, _LatchCfg(), disabled_realism=["daily_latch"])
    assert [(t.symbol, t.entry_time, t.pnl) for t in off_trades] == \
           [(t.symbol, t.entry_time, t.pnl) for t in base_trades]
    assert "daily_latch" not in off_meta
    on_trades, on_meta, _s, _l = await _golden_run(monkeypatch, _LatchCfg())
    rep = on_meta["daily_latch"]
    assert rep["enabled"] is True
    assert len(on_trades) == len(base_trades) - rep["dropped_trades"]


def test_json_report_tolerates_infinite_real_in_p_value_rows(tmp_path):
    import json
    from pathlib import Path
    from src.strategies.scalper.backtest import write_json_report
    from src.strategies.scalper.permutation import compute_p_values

    real = {"profit_factor": float("inf"), "trades": 5}
    rows = [{"profit_factor": 1.1, "trades": 4}, {"profit_factor": 0.9, "trades": 3}]
    result = compute_p_values(real, rows, ["profit_factor"], seed=3, block_length=2)
    study = {"permutations": 2, "seed": 3, "block_length": 2, "metrics": ["profit_factor"],
             "result": result, "null_samples": rows}
    path = write_json_report([], days=1, symbols=["A"], strategy_names="C",
                             output_dir=tmp_path, permutation=study)
    raw = Path(path).read_text(encoding="utf-8")
    assert "Infinity" not in raw and "NaN" not in raw
    data = json.loads(raw)["permutation"]
    assert data["seed"] == 3 and data["block_length"] == 2
    assert data["result"]["seed"] == 3 and data["result"]["block_length"] == 2
