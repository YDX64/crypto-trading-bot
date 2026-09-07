"""Naive database UTC must not become browser-local wall time."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.time_utils import utc_isoformat


@pytest.mark.parametrize(
    "stamp, expected",
    [
        (None, None),
        (datetime(2026, 9, 7, 16, 33), "2026-09-07T16:33:00+00:00"),
        (datetime(2026, 9, 7, 16, 33, tzinfo=timezone.utc), "2026-09-07T16:33:00+00:00"),
        (datetime(2026, 9, 7, 18, 33, tzinfo=timezone(timedelta(hours=2))), "2026-09-07T16:33:00+00:00"),
        (datetime(2026, 9, 7, 12, 33, tzinfo=timezone(timedelta(hours=-4))), "2026-09-07T16:33:00+00:00"),
    ],
)
def test_utc_isoformat_preserves_instant_and_does_not_mutate(stamp, expected):
    original = stamp
    assert utc_isoformat(stamp) == expected
    assert stamp == original


@pytest.mark.asyncio
async def test_positions_api_emits_explicit_utc_and_snapshot_time(monkeypatch):
    import src.main as main

    stamp = datetime(2026, 9, 7, 16, 33)
    position = SimpleNamespace(
        side=SimpleNamespace(value="SHORT"), entry_price=100.0,
        current_price=101.0, quantity=1.0, leverage=10,
        current_stoploss=105.0, status=SimpleNamespace(value="OPEN"),
        is_break_even=False, is_trailing=False, unrealized_pnl=-1.0,
        pnl_percentage=-10.0, opened_at=stamp,
    )
    monkeypatch.setattr(main, "orchestrator", SimpleNamespace(active_positions={"BTCUSDT": position}))
    payload = await main.get_positions()
    assert payload["positions"][0]["opened_at"] == "2026-09-07T16:33:00+00:00"
    assert datetime.fromisoformat(payload["as_of"]).tzinfo is not None
    assert position.opened_at is stamp


@pytest.mark.asyncio
async def test_closed_trade_api_emits_explicit_utc_without_rewriting_ledger():
    import src.main as main

    opened = datetime(2026, 9, 7, 7, 35)
    closed = datetime(2026, 9, 7, 15, 36)
    trade = SimpleNamespace(
        id=354, strategy="C", symbol="BTCUSDT", direction="LONG", status="CLOSED",
        entry_price=100.0, exit_price=99.0, realized_pnl=-1.0, roi_pct=-10.0,
        exit_reason="REAPER", opened_at=opened, closed_at=closed,
        signal_reason="fixture", notes=None, forensics=None,
    )
    result = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [trade]))
    db = SimpleNamespace(execute=AsyncMock(return_value=result))
    payload = await main.scalper_trades(db=db)
    assert payload[0]["opened_at"] == "2026-09-07T07:35:00+00:00"
    assert payload[0]["closed_at"] == "2026-09-07T15:36:00+00:00"
    assert trade.opened_at is opened and trade.closed_at is closed
