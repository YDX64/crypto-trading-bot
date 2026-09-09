"""D37: never discard confirmed maker execution on regressing responses.

Offline exchange doubles only. No strategy, sizing or fee policy changes.
"""
import json
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from src.strategies.scalper.executor import PendingRecoveryError, ScalpExecutor
from src.trading.position_manager import PositionManager
from test_scalper_setups import (
    _FakeClientMaker,
    _FakePm,
    _FakeTracker,
    _mk_exec_ctx,
    _mk_exec_signal,
    _mk_maker_cfg,
)


async def _pending(tmp_path):
    client = _FakeClientMaker(limit_order_id=555)
    pm = _FakePm(entry_price=100.0, filled_qty=0.4)
    tracker = _FakeTracker()
    executor = ScalpExecutor(
        client=client, pm=pm, tracker=tracker,
        cfg=_mk_maker_cfg(scalper_pending_journal_path=str(tmp_path / "pending.json")),
    )
    await executor.try_open(_mk_exec_signal(100.0, 99.5), _mk_exec_ctx())
    pending = executor._pending["TESTUSDT"]
    executor._record_order_state(pending, {
        "orderId": 555, "status": "PARTIALLY_FILLED",
        "executedQty": "0.4", "avgPrice": "100.0",
    })
    return executor, client, pm, tracker


@pytest.mark.parametrize("status", ["CANCELED", "EXPIRED", "EXPIRED_IN_MATCH", "REJECTED"])
@pytest.mark.parametrize("fill", [{}, {"executedQty": "0"}, {"executedQty": "0.1", "avgPrice": "90"}])
async def test_poll_terminal_cannot_erase_persisted_fill(tmp_path, status, fill):
    executor, client, pm, tracker = await _pending(tmp_path)
    captured = []
    original = pm.resolve_fill

    async def capture(symbol, order, **kwargs):
        captured.append(dict(order))
        return await original(symbol, order, **kwargs)

    pm.resolve_fill = capture
    client.get_order_responses[555] = [{"orderId": 555, "status": status, **fill}]
    opened = await executor.check_pending()
    assert len(opened) == 1
    assert captured[0]["executedQty"] == "0.4"
    assert captured[0]["avgPrice"] == "100.0"
    assert pm.calls.count("place_stop_loss_or_close") == 1
    assert tracker.calls.count("record_open") == 1
    assert not executor.pending_symbols()
    assert json.loads((tmp_path / "pending.json").read_text())["entries"] == {}


@pytest.mark.parametrize("cancel_response", [
    {"status": "ALREADY_GONE"},
    {"status": "NEW", "executedQty": "0"},
])
async def test_cancel_requery_keeps_known_fill(tmp_path, cancel_response):
    executor, client, pm, _ = await _pending(tmp_path)
    client.get_order_responses[555] = [
        {"orderId": 555, "status": "NEW", "executedQty": "0"},
        {"orderId": 555, "status": "CANCELED", "executedQty": "0"},
    ]
    client.cancel_response = cancel_response
    opened = await executor.cancel_all_pending()
    assert len(opened) == 1
    assert opened[0].position.quantity == pytest.approx(0.4)
    assert pm.calls.count("place_stop_loss_or_close") == 1
    assert not executor.pending_symbols()


async def test_cancel_uncertainty_raises_and_next_halted_retry_protects_once(tmp_path):
    executor, client, pm, tracker = await _pending(tmp_path)
    client.get_order_responses[555] = [{"orderId": 555, "status": "NEW", "executedQty": "0"}]
    client.cancel_error = TimeoutError("unknown cancellation")
    with pytest.raises(PendingRecoveryError, match="known maker fill"):
        await executor.check_pending()
    assert executor._pending["TESTUSDT"].phase == "WORKING"
    assert executor._pending["TESTUSDT"].executed_qty == 0.4
    assert not pm.calls  # Never finalize while the original LIMIT may still execute.
    assert not tracker.calls
    client.cancel_error = None
    client.get_order_responses[555] = [{"orderId": 555, "status": "NEW"}]
    client.cancel_response = {"orderId": 555, "status": "CANCELED"}
    opened = await executor.cancel_all_pending()  # The engine's halted retry path.
    assert len(opened) == 1
    assert await executor.cancel_all_pending() == []
    assert pm.calls.count("place_stop_loss_or_close") == 1
    assert tracker.calls.count("record_open") == 1
    assert client.limit_post_calls == 1


async def test_restart_terminal_uses_journal_fill_evidence(tmp_path):
    executor, client, pm, tracker = await _pending(tmp_path)
    recovered = ScalpExecutor(client=client, pm=pm, tracker=tracker, cfg=executor.cfg)
    client.client_order_query_responses = [{"orderId": 555, "status": "CANCELED"}]
    opened = await recovered.recover_pending()
    assert len(opened) == 1
    assert opened[0].position.quantity == pytest.approx(0.4)
    assert not recovered.pending_symbols()
    assert pm.calls.count("place_stop_loss_or_close") == 1


async def test_restart_three_not_found_cannot_erase_known_fill(tmp_path):
    executor, client, pm, tracker = await _pending(tmp_path)
    recovered = ScalpExecutor(client=client, pm=pm, tracker=tracker, cfg=executor.cfg)
    client.client_order_query_responses = [{}, {}, {}]
    with pytest.raises(PendingRecoveryError, match="known fill absent"):
        await recovered.recover_pending()
    assert recovered.pending_symbols() == {"TESTUSDT"}
    assert recovered._recovery_needed
    assert json.loads((tmp_path / "pending.json").read_text())["entries"]["TESTUSDT"]["executed_qty"] == 0.4
    assert not pm.calls


async def test_websocket_terminal_zero_preserves_confirmed_fill(tmp_path):
    executor, _, pm, tracker = await _pending(tmp_path)
    event = {"e": "ORDER_TRADE_UPDATE", "o": {
        "s": "TESTUSDT", "c": executor._pending["TESTUSDT"].client_order_id,
        "i": 555, "X": "CANCELED", "z": "0", "ap": "0",
    }}
    opened = await executor.handle_order_update(event)
    assert opened is not None
    assert opened.position.quantity == pytest.approx(0.4)
    assert await executor.handle_order_update(event) is None
    assert pm.calls.count("place_stop_loss_or_close") == 1
    assert tracker.calls.count("record_open") == 1


@pytest.mark.parametrize("path", ["poll", "pre_cancel", "post_cancel", "nonterminal_requery"])
async def test_unknown_queries_with_known_fill_surface_safety_error(tmp_path, path):
    executor, client, pm, _ = await _pending(tmp_path)
    failure = TimeoutError("query unavailable")
    if path in ("poll", "pre_cancel"):
        client.get_order = AsyncMock(side_effect=failure)
    else:
        client.get_order = AsyncMock(side_effect=[
            {"orderId": 555, "status": "NEW"}, failure,
        ])
        client.cancel_response = {"status": "ALREADY_GONE" if path == "post_cancel" else "NEW"}
    with pytest.raises(PendingRecoveryError, match="known maker fill"):
        if path == "poll":
            await executor.check_pending()
        else:
            await executor.cancel_all_pending()
    assert executor._pending["TESTUSDT"].phase == "WORKING"
    assert executor._pending["TESTUSDT"].executed_qty == 0.4
    assert not pm.calls


@pytest.mark.parametrize("bad_average", [None, "0", "nan", "inf", "-inf", "bad", True])
async def test_larger_fill_never_reuses_old_or_nonfinite_average(tmp_path, bad_average):
    executor, _, _, _ = await _pending(tmp_path)
    pending = executor._pending["TESTUSDT"]
    executor._record_order_state(pending, {"executedQty": "0.6", "avgPrice": bad_average})
    assert pending.executed_qty == 0.6
    assert pending.avg_price == 0


@pytest.mark.parametrize("bad_quantity", [None, "nan", "inf", "-inf", True, "bad"])
async def test_malformed_quantity_cannot_erase_confirmed_evidence(tmp_path, bad_quantity):
    executor, _, _, _ = await _pending(tmp_path)
    pending = executor._pending["TESTUSDT"]
    normalized = executor._reconcile_pending_order(pending, {"executedQty": bad_quantity, "avgPrice": "90"})
    assert normalized["executedQty"] == "0.4"
    assert normalized["avgPrice"] == "100.0"


async def test_larger_quantity_invalidates_old_average_in_journal(tmp_path):
    executor, _, _, _ = await _pending(tmp_path)
    pending = executor._pending["TESTUSDT"]
    executor._record_order_state(pending, {"orderId": 555, "executedQty": "0.6"})
    assert pending.executed_qty == 0.6
    assert pending.avg_price == 0.0
    stored = json.loads((tmp_path / "pending.json").read_text())["entries"]["TESTUSDT"]
    assert stored["avg_price"] == 0.0
    # Later old average cannot be assigned to the new, larger fill either.
    result = executor._reconcile_pending_order(pending, {"executedQty": "0.4", "avgPrice": "100"})
    assert result["executedQty"] == "0.6"
    assert result["avgPrice"] == "0"


@pytest.mark.parametrize("identity", [{"orderId": 556}, {"clientOrderId": "different-intent"}])
async def test_different_order_identity_cannot_consume_fill_evidence(tmp_path, identity):
    executor, client, pm, _ = await _pending(tmp_path)
    client.get_order_responses[555] = [{"status": "CANCELED", **identity}]
    with pytest.raises(PendingRecoveryError, match="identity mismatch"):
        await executor.check_pending()
    assert executor.pending_symbols() == {"TESTUSDT"}
    assert not pm.calls


@pytest.mark.parametrize("path", ["poll", "cancel_all", "restart"])
async def test_batch_error_carries_already_protected_positions(tmp_path, path):
    executor, client, pm, tracker = await _pending(tmp_path)
    second_signal = replace(_mk_exec_signal(100.0, 99.5), symbol="ZZZUSDT")
    second_ctx = replace(_mk_exec_ctx(), symbol="ZZZUSDT")
    client.limit_order_id = 556
    await executor.try_open(second_signal, second_ctx)
    second = executor._pending["ZZZUSDT"]
    executor._record_order_state(second, {
        "orderId": 556, "status": "PARTIALLY_FILLED", "executedQty": "0.4", "avgPrice": "100",
    })
    client.cancel_error = TimeoutError("second remainder unknown")
    if path == "restart":
        executor = ScalpExecutor(client=client, pm=pm, tracker=tracker, cfg=executor.cfg)
        client.client_order_query_responses = [
            {"orderId": 555, "status": "CANCELED"},
            {"orderId": 556, "status": "PARTIALLY_FILLED", "executedQty": "0.4", "avgPrice": "100"},
        ]
    else:
        client.get_order_responses[555] = [{"orderId": 555, "status": "CANCELED"}]
        client.get_order_responses[556] = [{"orderId": 556, "status": "PARTIALLY_FILLED", "executedQty": "0.4"}]
    with pytest.raises(PendingRecoveryError) as raised:
        if path == "poll":
            await executor.check_pending()
        elif path == "cancel_all":
            await executor.cancel_all_pending()
        else:
            await executor.recover_pending()
    assert len(raised.value.opened_positions) == 1
    assert raised.value.opened_positions[0].signal.symbol == "TESTUSDT"
    assert raised.value.opened_positions[0].position.quantity == pytest.approx(0.4)
    assert pm.calls.count("place_stop_loss_or_close") == 1
    assert tracker.calls.count("record_open") == 1
    assert executor.pending_symbols() == {"ZZZUSDT"}


async def test_journal_cleanup_error_carries_committed_position(tmp_path):
    executor, client, pm, tracker = await _pending(tmp_path)
    original_store = executor._store_pending_record

    def fail_db_open_record(pending):
        if pending.phase == "DB_OPEN":
            raise PendingRecoveryError("journal unavailable after DB commit")
        original_store(pending)

    executor._store_pending_record = fail_db_open_record
    client.get_order_responses[555] = [{"orderId": 555, "status": "CANCELED"}]
    with pytest.raises(PendingRecoveryError) as raised:
        await executor.check_pending()
    assert len(raised.value.opened_positions) == 1
    assert raised.value.opened_positions[0].signal.symbol == "TESTUSDT"
    assert executor._pending["TESTUSDT"].phase == "DB_OPEN"
    assert tracker.calls.count("record_open") == 1
    assert pm.calls.count("place_stop_loss_or_close") == 1


@pytest.mark.parametrize("cleanup_branch", ["db_open", "not_found", "uncertain_flat"])
async def test_later_recovery_cleanup_error_preserves_earlier_protected_position(tmp_path, cleanup_branch):
    executor, client, pm, tracker = await _pending(tmp_path)
    client.limit_order_id = 556
    await executor.try_open(
        replace(_mk_exec_signal(100.0, 99.5), symbol="ZZZUSDT"),
        replace(_mk_exec_ctx(), symbol="ZZZUSDT"),
    )
    if cleanup_branch == "db_open":
        tracker.open_rows = [{"symbol": "ZZZUSDT"}]
    elif cleanup_branch == "uncertain_flat":
        second = executor._pending["ZZZUSDT"]
        second.phase = "PROTECTING"
        executor._store_pending_record(second)
    recovered = ScalpExecutor(client=client, pm=pm, tracker=tracker, cfg=executor.cfg)
    client.client_order_query_responses = [{"orderId": 555, "status": "CANCELED"}]
    if cleanup_branch == "not_found":
        client.client_order_query_responses += [{}, {}, {}]
    elif cleanup_branch == "uncertain_flat":
        client.client_order_query_responses += [{"orderId": 556, "status": "CANCELED"}]
    original_remove = recovered._remove_pending_record

    def fail_later_cleanup(symbol):
        if symbol == "ZZZUSDT":
            raise PendingRecoveryError("later cleanup failed")
        original_remove(symbol)

    recovered._remove_pending_record = fail_later_cleanup
    with pytest.raises(PendingRecoveryError) as raised:
        await recovered.recover_pending()
    assert len(raised.value.opened_positions) == 1
    assert raised.value.opened_positions[0].signal.symbol == "TESTUSDT"
    assert tracker.calls.count("record_open") == 1
    assert pm.calls.count("place_stop_loss_or_close") == 1


async def test_real_fill_resolver_cannot_regress_known_cumulative_quantity(tmp_path):
    executor, client, pm, tracker = await _pending(tmp_path)
    executor._record_order_state(executor._pending["TESTUSDT"], {
        "orderId": 555, "executedQty": "0.6", "avgPrice": "0",
    })
    # Exercise production fill resolution, not the fixed-quantity test double.
    pm.resolve_fill = PositionManager(client).resolve_fill
    client.get_order_responses[555] = [
        {"orderId": 555, "status": "CANCELED", "executedQty": "0.6", "avgPrice": "0"},
        {"orderId": 555, "status": "CANCELED", "executedQty": "0.4", "avgPrice": "100"},
    ]
    with pytest.raises(PendingRecoveryError, match="dolum çözülemedi"):
        await executor.check_pending()
    pending = executor._pending["TESTUSDT"]
    assert pending.phase == "RECOVERY_REQUIRED"
    assert pending.executed_qty == 0.6
    assert pending.avg_price == 0
    assert pm.calls == ["place_stop_loss_or_close"]
    assert not tracker.calls
    assert "place_take_profit" not in client.calls


@pytest.mark.parametrize("price,quantity", [
    (float("inf"), 0.4), (float("nan"), 0.4), (0, 0.4), (True, 0.4),
    (100, float("inf")), (100, float("nan")), (100, 0), (100, True),
])
async def test_invalid_resolver_values_use_terminal_protection_recovery_path(tmp_path, price, quantity):
    executor, client, pm, tracker = await _pending(tmp_path)
    pm.resolve_fill = AsyncMock(return_value=(price, quantity))
    client.get_order_responses[555] = [{"orderId": 555, "status": "CANCELED"}]
    with pytest.raises(PendingRecoveryError):
        await executor.check_pending()
    assert executor._pending["TESTUSDT"].phase == "RECOVERY_REQUIRED"
    assert pm.calls == ["place_stop_loss_or_close"]
    assert not tracker.calls


async def test_real_executor_engine_hold_retry_protects_once_without_resuming(tmp_path):
    from tests.test_pending_safety_isolation import make_safety_engine

    executor, client, pm, tracker = await _pending(tmp_path)
    engine = make_safety_engine(tmp_path)
    engine.executor = executor
    client.get_order_responses[555] = [{"orderId": 555, "status": "NEW", "executedQty": "0"}]
    client.cancel_error = TimeoutError("cancel acknowledgement unknown")
    with pytest.raises(PendingRecoveryError):
        await engine._safety_tick()
    assert engine._entry_halted
    assert engine._entry_halt_category == "pending_recovery"
    assert not engine._entries_ready()
    assert not pm.calls
    engine.exits.step.assert_awaited_once()

    # The mandatory hold continues the real executor's cancellation path.
    client.cancel_error = None
    client.get_order_responses[555] = [{"orderId": 555, "status": "NEW"}]
    client.cancel_response = {"orderId": 555, "status": "CANCELED", "executedQty": "0"}
    await engine._safety_tick()
    await engine._safety_tick()
    assert executor.pending_symbols() == set()
    assert pm.calls.count("place_stop_loss_or_close") == 1
    assert tracker.calls.count("record_open") == 1
    engine.exits.track.assert_called_once()
    assert engine.exits.step.await_count == 3
    assert engine._entry_halted
    assert not engine._entries_ready()
    assert client.limit_post_calls == 1
