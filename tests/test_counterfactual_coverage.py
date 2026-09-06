"""Counterfactual measurements must not bridge unobserved price paths."""

from dataclasses import replace

import pytest

from src.strategies.scalper import counterfactual as cf
from src.strategies.scalper import counterfactual_store as store
from src.strategies.scalper.types import Candle


BASE = 1_700_000_000.0


def candle(minute, *, high=100.5, low=99.5, close=100.0, inclusive=False):
    opened = int((BASE + minute * 60) * 1000)
    return Candle(
        open_time=opened, close_time=opened + 60_000 - int(inclusive),
        open=100.0, high=high, low=low, close=close, volume=1.0,
    )


def pending(**changes):
    values = dict(
        at="2026-09-06T00:00:00Z", at_epoch=BASE, symbol="BTCUSDT",
        direction="LONG", reason="hour_gate", price=100.0,
        stop_price=99.0, tp1_price=102.0, leverage=10,
        horizons_h=[0.5, 1.0],
    )
    values.update(changes)
    return cf.build_pending(**values)


def resolve(candles, **changes):
    return cf.resolve(
        pending=pending(**changes), candles=candles, now_epoch=BASE + 3601,
    )


def test_interior_missing_bar_cannot_be_skipped_before_later_tp():
    bars = [candle(i) for i in range(60) if i != 40]
    bars[-1] = candle(59, high=103.0, close=102.5)
    result = resolve(bars)
    assert result["measured"] is False
    assert result["sim"]["gap"] == "interior_gap"
    assert result["pnl_roi_pct"] is None
    assert result["horizons"][0]["price"] == 100.0
    assert result["horizons"][1]["price"] is None


def test_sparse_open_times_cannot_inflate_the_inferred_bar_interval():
    result = resolve([candle(0), candle(59, high=103.0)])
    assert result["measured"] is False
    assert result["sim"]["gap"] == "interior_gap"


@pytest.mark.parametrize("count", [1, 30, 59])
def test_stale_tail_is_not_an_open_position_mark_at_the_horizon(count):
    result = resolve([candle(i) for i in range(count)])
    assert result["measured"] is False
    assert result["sim"]["gap"] == "stale_tail"
    assert result["sim"]["exit_price"] is None
    assert result["pnl_roi_pct"] is None
    assert result["horizons"][-1]["price"] is None


@pytest.mark.parametrize("inclusive", [False, True])
def test_complete_path_accepts_both_candle_close_timestamp_conventions(inclusive):
    result = resolve([candle(i, inclusive=inclusive) for i in range(60)])
    assert result["measured"] is True
    assert result["sim"]["outcome"] == "open"
    assert result["sim"]["bars"] == 60
    assert result["horizons"][-1]["price"] == 100.0
    assert result["coverage"] == {
        "policy": cf.COVERAGE_POLICY, "complete": True, "gap": None,
    }


def test_unaligned_horizon_allows_only_its_unfinished_last_bar():
    result = resolve(
        [candle(i, inclusive=True) for i in range(61)],
        at_epoch=BASE + 0.5,
    )
    assert result["measured"] is True
    assert result["sim"]["bars"] == 59
    assert result["sim"]["at_epoch"] < BASE + 3600.5


@pytest.mark.parametrize("direction,terminal", [
    ("LONG", "tp1"), ("LONG", "stop"),
    ("SHORT", "tp1"), ("SHORT", "stop"),
])
def test_proven_terminal_outcome_survives_a_later_gap(direction, terminal):
    is_tp = terminal == "tp1"
    high = 103.0 if (direction == "LONG") == is_tp else 100.5
    low = 97.0 if (direction == "SHORT") == is_tp else 99.5
    result = resolve(
        [candle(0), candle(1, high=high, low=low), candle(59)],
        direction=direction,
        stop_price=99.0 if direction == "LONG" else 101.0,
        tp1_price=102.0 if direction == "LONG" else 98.0,
    )
    assert result["measured"] is True
    assert result["sim"]["outcome"] == terminal
    assert all(h["price"] is None for h in result["horizons"])


@pytest.mark.parametrize("changes", [
    {"low": float("nan")}, {"high": float("inf")},
    {"close": 1000.0}, {"open": 10.0},
    {"close_time": int(BASE * 1000) - 1},
])
def test_unreadable_earlier_bar_cannot_hide_a_stop(changes):
    bars = [candle(i) for i in range(60)]
    bars[0] = replace(bars[0], **changes)
    bars[-1] = candle(59, high=103.0)
    result = resolve(bars)
    assert result["measured"] is False
    assert result["sim"]["gap"] == "invalid_candle"


def test_rolling_store_keeps_the_hole_visible_in_persisted_measurement(monkeypatch):
    from src.strategies.scalper import forensics_log

    logged = []
    monkeypatch.setattr(forensics_log, "append_soon", lambda event, row: logged.append(row) or True)
    store.reset()
    store.configure(enabled=True, horizons_h=[1.0])
    try:
        store.register(
            at="2026-09-06T00:00:00Z", at_epoch=BASE,
            symbol="BTCUSDT", direction="LONG", reason="hour_gate",
            price=100.0, stop_price=99.0, tp1_price=102.0, leverage=10,
        )
        assert store.resolve_symbol("BTCUSDT", [candle(i) for i in range(20)], BASE + 1200) == []
        result = store.resolve_symbol(
            "BTCUSDT", [candle(i) for i in range(40, 60)], BASE + 3601,
        )
        assert len(result) == 1
        assert result[0]["measured"] is False
        assert result[0]["sim"]["gap"] == "interior_gap"
        assert logged == result
        stats = store.counters_snapshot()
        assert stats["resolved"] == 1 and stats["measured"] == 0
        assert stats["pending"] == 0 and stats["candle_buffer_bars"] == 0
    finally:
        store.reset()


def test_legacy_pending_is_stamped_with_the_version_that_resolved_it():
    old_pending = pending()
    old_pending["version"] = 1
    result = cf.resolve(
        pending=old_pending, candles=[candle(i) for i in range(60)],
        now_epoch=BASE + 3601,
    )
    assert result["model"] == old_pending["model"] == "tp1_or_stop_v1"
    assert result["version"] == 2
    assert old_pending["version"] == 1


def test_summary_separates_legacy_coverage_from_new_validated_measurements():
    current = resolve([candle(i) for i in range(60)])
    legacy = dict(current, version=1, pnl_roi_pct=80.0)
    legacy.pop("coverage")
    summary = cf.summarize([legacy, current])
    assert summary["mixed_measurements"] is True
    groups = {row["version"]: row for row in summary["by_measurement"]}
    assert groups[1]["coverage_policy"] is None
    assert groups[1]["avg_roi_pct"] == 80.0
    assert groups[2]["coverage_policy"] == cf.COVERAGE_POLICY
    assert groups[2]["avg_roi_pct"] == 0.0
    assert groups[1]["n"] == groups[2]["n"] == 1
    # Compatibility: existing totals survive; callers can see the mixed
    # flag and select a measurement group instead of trusting pooled PnL.
    assert summary["overall"]["n"] == 2
    assert "coverage" not in legacy


def test_capacity_expiry_cannot_resurrect_the_same_symbols_expired_bucket():
    store.reset()
    store.configure(enabled=True, horizons_h=[1.0], max_pending=1, max_age_h=1.0, dedup_sec=0)
    try:
        args = dict(
            at="2026-09-06T00:00:00Z", symbol="BTCUSDT", direction="LONG",
            reason="hour_gate", price=100.0, stop_price=99.0, tp1_price=102.0,
            leverage=10,
        )
        store.register(at_epoch=BASE, **args)
        store.register(at_epoch=BASE + 7200, **args)
        rows = store.pending_for("BTCUSDT")
        assert len(rows) == 1
        assert rows[0]["at_epoch"] == BASE + 7200
        stats = store.counters_snapshot()
        assert stats["expired"] == 1
        assert stats["pending"] == len(rows)
    finally:
        store.reset()
