"""PnL provenance and restart-protection regression tests."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import src.models.waiting_signal  # noqa: F401 - SQLAlchemy relationship setup
import src.strategies.scalper.tracker as tracker_module
from src.strategies.scalper.exits import ExitManager
from src.strategies.scalper.tracker import ScalpTracker
from src.strategies.scalper.types import Direction
from src.trading.position_manager import PositionManager, UnprotectedPositionError


def _cfg():
    return SimpleNamespace(
        scalper_tp1_roi=10.0,
        scalper_tp2_roi=25.0,
        scalper_breakeven_buffer_pct=0.05,
        scalper_tp1_fraction=0.4,
        scalper_tp2_fraction=0.3,
        scalper_chandelier_atr_mult=3.0,
        scalper_chandelier_atr_period=22,
    )


def _manager(client, tracker=None, pm=None):
    manager = ExitManager(
        client=client,
        pm=pm or SimpleNamespace(),
        tracker=tracker or SimpleNamespace(),
        cfg=_cfg(),
        kline_fetch=AsyncMock(return_value=[]),
    )
    manager.INCOME_RETRY_DELAYS = (0.0,)
    return manager


def _scalp_position(*, entry_price=100.0, quantity=2.0, order_id="123"):
    return SimpleNamespace(
        trade_id=7,
        signal=SimpleNamespace(direction=Direction.LONG),
        position=SimpleNamespace(
            entry_price=entry_price,
            quantity=quantity,
            current_price=entry_price,
            opened_at=datetime.now(timezone.utc),
            entry_order_id=order_id,
        ),
        plan=SimpleNamespace(initial_stop=95.0, tp1_price=110.0),
        trailing_active=False,
        mae_pct=-2.0,
        mfe_pct=4.0,
    )


def _trade(**overrides):
    values = {
        "id": 11,
        "strategy": "C",
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "entry_price": 100.0,
        "quantity": 1.0,
        "leverage": 10,
        "signal_reason": "test",
        "opened_at": datetime.now(timezone.utc),
        "sl_algo_id": "55",
        "tp1_algo_id": "56",
        "tp2_algo_id": "57",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_closed_position_uses_signed_binance_income_net_of_fees_and_funding():
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    client = SimpleNamespace(
        cancel_all_open_orders=AsyncMock(),
        get_current_price=AsyncMock(return_value=110.0),
        get_order=AsyncMock(
            return_value={"symbol": "BTCUSDT", "orderId": 123, "updateTime": now_ms - 5000}
        ),
        get_income_history=AsyncMock(
            return_value=[
                {"symbol": "BTCUSDT", "incomeType": "REALIZED_PNL", "income": "12.0", "time": now_ms, "tranId": 1},
                {"symbol": "BTCUSDT", "incomeType": "COMMISSION", "income": "-1.0", "time": now_ms, "tranId": 2},
                {"symbol": "BTCUSDT", "incomeType": "FUNDING_FEE", "income": "-0.5", "time": now_ms, "tranId": 3},
                {"symbol": "BTCUSDT", "incomeType": "TRANSFER", "income": "99", "time": now_ms, "tranId": 4},
                {"symbol": "ETHUSDT", "incomeType": "REALIZED_PNL", "income": "500", "time": now_ms, "tranId": 5},
            ]
        ),
    )
    tracker = SimpleNamespace(record_close=AsyncMock())
    manager = _manager(client, tracker=tracker)

    await manager._handle_closed("BTCUSDT", _scalp_position())

    close = tracker.record_close.await_args.kwargs
    assert close["realized_pnl"] == pytest.approx(10.5)
    assert close["pnl_source"] == "binance_income_net"


@pytest.mark.asyncio
async def test_closed_position_falls_back_to_labeled_gross_estimate_when_income_empty():
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    client = SimpleNamespace(
        cancel_all_open_orders=AsyncMock(),
        get_current_price=AsyncMock(return_value=110.0),
        get_order=AsyncMock(return_value={"updateTime": now_ms - 5000}),
        get_income_history=AsyncMock(return_value=[]),
    )
    tracker = SimpleNamespace(record_close=AsyncMock())
    manager = _manager(client, tracker=tracker)

    await manager._handle_closed("BTCUSDT", _scalp_position())

    close = tracker.record_close.await_args.kwargs
    assert close["realized_pnl"] == pytest.approx(20.0)
    assert close["pnl_source"] == "estimated_gross"


@pytest.mark.asyncio
async def test_income_reconciliation_retries_a_bounded_number_for_delayed_rows():
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    delayed_rows = [
        {"symbol": "BTCUSDT", "incomeType": "REALIZED_PNL", "income": "4.0", "time": now_ms},
        {"symbol": "BTCUSDT", "incomeType": "COMMISSION", "income": "-0.2", "time": now_ms},
    ]
    client = SimpleNamespace(
        get_order=AsyncMock(return_value={"updateTime": now_ms - 1000}),
        get_income_history=AsyncMock(side_effect=[[], delayed_rows]),
    )
    manager = _manager(client)
    manager.INCOME_RETRY_DELAYS = (0.0, 0.0)

    net = await manager._fetch_net_income(
        "BTCUSDT",
        datetime.now(timezone.utc),
        "123",
    )

    assert net == pytest.approx(3.8)
    assert client.get_income_history.await_count == 2


@pytest.mark.asyncio
async def test_unverified_entry_time_guards_against_same_symbol_income_contamination():
    client = SimpleNamespace(
        cancel_all_open_orders=AsyncMock(),
        get_current_price=AsyncMock(return_value=110.0),
        get_order=AsyncMock(side_effect=RuntimeError("order lookup unavailable")),
        get_income_history=AsyncMock(
            return_value=[{"symbol": "BTCUSDT", "incomeType": "REALIZED_PNL", "income": "999"}]
        ),
    )
    tracker = SimpleNamespace(record_close=AsyncMock())
    manager = _manager(client, tracker=tracker)

    await manager._handle_closed("BTCUSDT", _scalp_position())

    close = tracker.record_close.await_args.kwargs
    assert close["realized_pnl"] == pytest.approx(20.0)
    assert close["pnl_source"] == "estimated_gross"
    client.get_income_history.assert_not_awaited()


@pytest.mark.asyncio
async def test_recovery_position_query_failure_returns_false_readiness():
    trade = _trade()
    client = SimpleNamespace(get_position_risk=AsyncMock(side_effect=RuntimeError("timeout")))
    tracker = SimpleNamespace(open_trades=AsyncMock(return_value=[trade]))
    pm = SimpleNamespace(emergency_close=AsyncMock())

    assert await _manager(client, tracker=tracker, pm=pm).recover() is False
    pm.emergency_close.assert_not_awaited()


@pytest.mark.asyncio
async def test_recovery_algo_query_failure_returns_false_without_assuming_no_stop():
    trade = _trade()
    client = SimpleNamespace(
        get_position_risk=AsyncMock(return_value={"positionAmt": "1"}),
        get_open_algo_orders=AsyncMock(side_effect=RuntimeError("timeout")),
    )
    tracker = SimpleNamespace(open_trades=AsyncMock(return_value=[trade]))
    pm = SimpleNamespace(emergency_close=AsyncMock())

    assert await _manager(client, tracker=tracker, pm=pm).recover() is False
    pm.emergency_close.assert_not_awaited()


@pytest.mark.asyncio
async def test_recovery_missing_stop_flattens_and_records_unknown_estimate():
    trade = _trade()
    client = SimpleNamespace(
        get_position_risk=AsyncMock(return_value={"positionAmt": "1"}),
        get_open_algo_orders=AsyncMock(return_value=[]),
        get_current_price=AsyncMock(return_value=95.0),
    )
    tracker = SimpleNamespace(
        open_trades=AsyncMock(return_value=[trade]),
        record_close=AsyncMock(),
    )
    pm = SimpleNamespace(emergency_close=AsyncMock(return_value=True))

    assert await _manager(client, tracker=tracker, pm=pm).recover() is True

    pm.emergency_close.assert_awaited_once_with("BTCUSDT")
    close = tracker.record_close.await_args.kwargs
    assert close["exit_reason"] == "UNKNOWN"
    assert close["realized_pnl"] == pytest.approx(-5.0)
    assert close["pnl_source"] == "estimated_gross"
    assert close["notes"] == "recovery=missing_stop_emergency_close"


@pytest.mark.asyncio
async def test_recovery_with_live_directional_stop_resumes_tracking():
    trade = _trade()
    client = SimpleNamespace(
        get_position_risk=AsyncMock(return_value={"positionAmt": "1"}),
        get_open_algo_orders=AsyncMock(
            return_value=[
                {
                    "orderType": "STOP_MARKET",
                    "side": "SELL",
                    "triggerPrice": "94.5",
                }
            ]
        ),
    )
    tracker = SimpleNamespace(open_trades=AsyncMock(return_value=[trade]))
    pm = SimpleNamespace(emergency_close=AsyncMock())
    manager = _manager(client, tracker=tracker, pm=pm)

    assert await manager.recover() is True
    assert manager.tracked_symbols() == {"BTCUSDT"}
    pm.emergency_close.assert_not_awaited()


@pytest.mark.asyncio
async def test_recovery_missing_stop_and_failed_flatten_raises():
    trade = _trade()
    client = SimpleNamespace(
        get_position_risk=AsyncMock(return_value={"positionAmt": "1"}),
        get_open_algo_orders=AsyncMock(return_value=[]),
    )
    tracker = SimpleNamespace(open_trades=AsyncMock(return_value=[trade]))
    pm = SimpleNamespace(emergency_close=AsyncMock(return_value=False))

    with pytest.raises(UnprotectedPositionError):
        await _manager(client, tracker=tracker, pm=pm).recover()


@pytest.mark.asyncio
async def test_position_manager_public_emergency_close_delegates_to_safety_flow():
    manager = PositionManager(SimpleNamespace())
    manager._emergency_close = AsyncMock(return_value=True)

    assert await manager.emergency_close("BTCUSDT") is True
    manager._emergency_close.assert_awaited_once_with("BTCUSDT")


def test_tracker_pnl_provenance_labels_and_basis():
    tracker = ScalpTracker()

    notes = tracker._merge_close_notes(
        existing="scalper:C;pnl_source=estimated_gross",
        pnl_source="binance_income_net",
        notes="close=reconciled",
    )
    assert notes == "scalper:C;pnl_source=binance_income_net;close=reconciled"
    assert tracker._pnl_source(notes) == "verified"
    assert tracker._pnl_source("pnl_source=estimated_gross") == "fallback"
    assert tracker._pnl_source(None) == "legacy"
    assert tracker._pnl_basis(3, 0, 0) == "binance_income_net"
    assert tracker._pnl_basis(1, 1, 0) == "mixed"


@pytest.mark.asyncio
async def test_tracker_stats_exposes_verified_fallback_and_legacy_counts(monkeypatch):
    rows = [
        SimpleNamespace(strategy="C", realized_pnl=3.0, roi_pct=3.0, notes="pnl_source=binance_income_net"),
        SimpleNamespace(strategy="C", realized_pnl=-1.0, roi_pct=-1.0, notes="pnl_source=estimated_gross"),
        SimpleNamespace(strategy="C", realized_pnl=0.0, roi_pct=0.0, notes=None),
    ]

    class _Result:
        def scalars(self):
            return self

        def all(self):
            return rows

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, _query):
            return _Result()

    monkeypatch.setattr(tracker_module, "AsyncSessionLocal", lambda: _Session())

    stats = await ScalpTracker().stats()

    assert stats["C"]["trades"] == 3
    assert stats["C"]["total_pnl"] == pytest.approx(2.0)
    assert stats["C"]["verified_trades"] == 1
    assert stats["C"]["fallback_trades"] == 1
    assert stats["C"]["legacy_trades"] == 1
    assert stats["C"]["pnl_basis"] == "mixed"
