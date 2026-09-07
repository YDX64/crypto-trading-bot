"""Venue-mark valuation is observational, identity-bound, fresh and no-I/O."""

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.strategies.scalper import exits as exits_module
from src.strategies.scalper.exits import ExitManager
from src.strategies.scalper.types import Direction
from src.trading.binance_client_improved import BinanceAPIError
from test_runtime_liveness import _make_engine


def _sp(direction=Direction.LONG):
    return SimpleNamespace(
        trade_id=1,
        signal=SimpleNamespace(direction=direction, strategy="C"),
        position=SimpleNamespace(
            symbol="BTCUSDT", quantity=2.0, entry_price=100.0,
            current_price=900.0, leverage=20, current_stoploss=97.5,
            opened_at=datetime(2026, 9, 7, 10, 0, 0),
        ),
        plan=SimpleNamespace(tp1_quantity=.8, tp2_quantity=.4),
        tp1_done=False, tp2_done=False, trailing_active=False,
    )


def _row(**changes):
    row = dict(symbol="BTCUSDT", positionAmt="0.6", entryPrice="100",
               markPrice="101", unRealizedProfit="0.6", updateTime=1)
    row.update(changes)
    return row


def _manager(sp=None):
    manager = ExitManager(
        client=SimpleNamespace(get_position_risk=AsyncMock(return_value=_row()),
                               get_current_price=AsyncMock(return_value=None)),
        pm=Mock(), tracker=Mock(), cfg=SimpleNamespace(), kline_fetch=AsyncMock(),
    )
    manager.logger = Mock()
    if sp is not None:
        manager.track(sp)
    return manager


def _observe(manager, sp, **changes):
    manager._observe_position_valuation("BTCUSDT", sp, _row(**changes))
    return manager.valuation_snapshot("BTCUSDT", sp)


@pytest.mark.parametrize("direction,amt,mark,pnl,roi", [
    (Direction.LONG, ".6", "101", ".6", 20.),
    (Direction.SHORT, "-.6", "101", "-.6", -20.),
    (Direction.SHORT, "-.6", "99", ".6", 20.),
])
def test_partial_tp_uses_venue_remaining_quantity_and_mark_without_mutation(direction, amt, mark, pnl, roi):
    sp = _sp(direction)
    before = deepcopy(sp)
    manager = _manager(sp)
    snapshot = _observe(manager, sp, positionAmt=amt, markPrice=mark, unRealizedProfit=pnl)
    assert snapshot["unrealized_pnl_status"] == "ok"
    assert snapshot["remaining_quantity"] == .6
    assert snapshot["mark_price"] == float(mark)
    assert snapshot["unrealized_pnl"] == float(pnl)  # not initial qty 2 × price delta
    assert snapshot["roi_pct"] == pytest.approx(roi)
    assert snapshot["valuation_source"] == "binance_position_risk"
    assert snapshot["valuation_timestamp_basis"] == "local_position_risk_observation"
    assert datetime.fromisoformat(snapshot["valuation_as_of"]).tzinfo is not None
    assert sp == before
    assert manager.client.get_position_risk.await_count == 0
    assert not manager.pm.mock_calls
    assert not manager.tracker.mock_calls


@pytest.mark.parametrize("missing", ["symbol", "positionAmt", "entryPrice", "markPrice", "unRealizedProfit"])
def test_missing_fields_invalidate_previous_good_value(missing):
    sp = _sp(); manager = _manager(sp)
    assert _observe(manager, sp)["unrealized_pnl_status"] == "ok"
    row = _row(); row.pop(missing)
    manager._observe_position_valuation("BTCUSDT", sp, row)
    result = manager.valuation_snapshot("BTCUSDT", sp)
    assert result["unrealized_pnl_status"] in {"invalid", "position_mismatch"}
    for key in ("remaining_quantity", "mark_price", "unrealized_pnl", "roi_pct"):
        assert result[key] is None


@pytest.mark.parametrize("field,value", [
    (field, value)
    for field in ("positionAmt", "entryPrice", "markPrice", "unRealizedProfit")
    for value in (True, False, None, "nan", "inf", "-inf", "bad")
] + [("entryPrice", -1), ("entryPrice", 0), ("markPrice", -1), ("markPrice", 0)])
def test_invalid_numbers_are_unknown_not_zero(field, value):
    sp = _sp(); manager = _manager(sp)
    result = _observe(manager, sp, **{field: value})
    assert result["unrealized_pnl_status"] == "invalid"
    assert result["unrealized_pnl"] is None
    assert result["mark_price"] is None


@pytest.mark.parametrize("change", [
    dict(symbol="ETHUSDT"), dict(positionAmt="-.6"), dict(positionAmt="0"),
    dict(entryPrice="101"), dict(positionAmt="2.01"),
])
def test_other_position_cannot_be_attributed_to_current_trade(change):
    sp = _sp(); manager = _manager(sp)
    result = _observe(manager, sp, **change)
    assert result["unrealized_pnl_status"] == "position_mismatch"
    assert result["unrealized_pnl"] is None


def test_float_representation_noise_in_known_entry_is_accepted():
    sp = _sp(); manager = _manager(sp)
    assert _observe(manager, sp, entryPrice="100.00000000000001")["unrealized_pnl_status"] == "ok"


@pytest.mark.parametrize("bad_row", [None, [], "bad", 0, False])
def test_bad_response_type_replaces_previous_valuation(bad_row):
    sp = _sp(); manager = _manager(sp)
    _observe(manager, sp)
    manager._observe_position_valuation("BTCUSDT", sp, bad_row)
    assert manager.valuation_snapshot("BTCUSDT", sp)["unrealized_pnl_status"] == "invalid"
    assert manager.valuation_snapshot("BTCUSDT", sp)["unrealized_pnl"] is None


def test_unexpected_telemetry_parser_error_is_swallowed_and_invalidates_old_value():
    sp = _sp(); manager = _manager(sp)
    _observe(manager, sp)
    manager._record_position_valuation = Mock(side_effect=RuntimeError("malformed data"))
    manager._observe_position_valuation("BTCUSDT", sp, _row())
    assert manager.valuation_snapshot("BTCUSDT", sp)["unrealized_pnl_status"] == "invalid"
    assert manager.valuation_snapshot("BTCUSDT", sp)["unrealized_pnl"] is None


def test_invalid_known_leverage_never_becomes_fake_one_x_roi():
    sp = _sp(); sp.position.leverage = 0; manager = _manager(sp)
    assert _observe(manager, sp)["unrealized_pnl_status"] == "invalid"


def test_freshness_boundary_and_future_clock_fail_unknown(monkeypatch):
    sp = _sp(); manager = _manager(sp)
    monkeypatch.setattr(exits_module.time, "monotonic", lambda: 100.)
    first = _observe(manager, sp)
    monkeypatch.setattr(exits_module.time, "monotonic", lambda: 180.)
    assert manager.valuation_snapshot("BTCUSDT", sp)["unrealized_pnl_status"] == "ok"
    monkeypatch.setattr(exits_module.time, "monotonic", lambda: 180.001)
    stale = manager.valuation_snapshot("BTCUSDT", sp)
    assert stale["unrealized_pnl_status"] == "stale"
    assert stale["unrealized_pnl"] is None
    assert stale["remaining_quantity"] is None
    assert stale["valuation_as_of"] == first["valuation_as_of"]
    monkeypatch.setattr(exits_module.time, "monotonic", lambda: 99.)
    assert manager.valuation_snapshot("BTCUSDT", sp)["unrealized_pnl_status"] == "invalid"
    monkeypatch.setattr(exits_module.time, "monotonic", lambda: float("nan"))
    assert manager.valuation_snapshot("BTCUSDT", sp)["unrealized_pnl"] is None


def test_exchange_update_time_is_not_the_local_observation_clock():
    sp = _sp(); manager = _manager(sp)
    before = datetime.now(timezone.utc)
    snapshot = _observe(manager, sp, updateTime=1)
    assert datetime.fromisoformat(snapshot["valuation_as_of"]) >= before


def test_unobserved_recovered_position_never_uses_entry_as_current_price():
    sp = _sp(); sp.position.current_price = sp.position.entry_price
    manager = _manager(sp)
    result = manager.valuation_snapshot("BTCUSDT", sp)
    assert result["unrealized_pnl_status"] == "unobserved"
    assert result["valuation_as_of"] is None
    assert result["unrealized_pnl"] is None


def test_same_symbol_replacement_clears_cache_and_late_old_read_cannot_overwrite_it():
    old = _sp(); manager = _manager(old)
    _observe(manager, old)
    new = _sp(); new.trade_id = 2
    manager.track(new)
    assert "BTCUSDT" not in manager._position_valuations
    assert manager.valuation_snapshot("BTCUSDT", new)["unrealized_pnl_status"] == "unobserved"
    _observe(manager, new, unRealizedProfit=".7")
    _observe(manager, old, unRealizedProfit="999")
    assert manager.valuation_snapshot("BTCUSDT", old)["unrealized_pnl_status"] == "unobserved"
    assert manager.valuation_snapshot("BTCUSDT", new)["unrealized_pnl"] == .7


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [RuntimeError("read failed"), BinanceAPIError(503, -1000, "read failed")])
async def test_failed_existing_read_invalidates_good_value_and_does_no_extra_io(error):
    sp = _sp(); manager = _manager(sp)
    _observe(manager, sp)
    manager.client.get_position_risk.side_effect = error
    before = deepcopy(sp)
    await manager._step_one("BTCUSDT", sp)
    result = manager.valuation_snapshot("BTCUSDT", sp)
    assert result["unrealized_pnl_status"] == "invalid"
    assert result["valuation_as_of"] is None
    assert result["unrealized_pnl"] is None
    manager.client.get_position_risk.assert_awaited_once_with("BTCUSDT")
    manager.client.get_current_price.assert_not_awaited()
    assert sp == before


@pytest.mark.asyncio
async def test_step_observes_existing_read_without_changing_exit_inputs_or_order_path():
    sp = _sp(); manager = _manager(sp)
    manager._check_tp1 = AsyncMock()
    manager._check_tp2 = AsyncMock()
    before = deepcopy(sp)
    await manager._step_one("BTCUSDT", sp)
    assert manager.valuation_snapshot("BTCUSDT", sp)["unrealized_pnl"] == .6
    manager.client.get_position_risk.assert_awaited_once_with("BTCUSDT")
    manager.client.get_current_price.assert_awaited_once_with("BTCUSDT")
    manager._check_tp1.assert_awaited_once_with("BTCUSDT", sp, .6)
    manager._check_tp2.assert_awaited_once_with("BTCUSDT", sp, .6)
    assert sp == before
    assert not manager.pm.mock_calls
    assert not manager.tracker.mock_calls


@pytest.mark.asyncio
async def test_closed_position_cache_is_removed():
    sp = _sp(); manager = _manager(sp)
    _observe(manager, sp)
    manager._finalize_close = AsyncMock()
    await manager._handle_closed("BTCUSDT", sp)
    assert manager.valuation_snapshot("BTCUSDT", sp)["unrealized_pnl_status"] == "unobserved"
    assert not manager._position_valuations


@pytest.mark.asyncio
async def test_finalizing_old_position_preserves_replacements_valuation():
    old = _sp(); manager = _manager(old)
    _observe(manager, old)
    new = _sp(); new.trade_id = 2
    async def replace_during_close(*args, **kwargs):
        manager.track(new)
        _observe(manager, new)
    manager._finalize_close = AsyncMock(side_effect=replace_during_close)
    await manager._handle_closed("BTCUSDT", old)
    assert manager.valuation_snapshot("BTCUSDT", new)["unrealized_pnl_status"] == "ok"


def test_snapshot_uses_mark_remaining_and_utc_projection_but_preserves_initial_quantity():
    sp = _sp(); manager = _manager(sp)
    _observe(manager, sp)
    engine = _make_engine(); engine.exits = manager
    before = deepcopy(sp)
    row = engine.snapshot()["tracked"][0]
    assert row["quantity"] == 2.0
    assert row["remaining_quantity"] == .6
    assert row["current_price"] == row["mark_price"] == 101.
    assert row["unrealized_pnl"] == .6
    assert row["roi_pct"] == pytest.approx(20.)
    assert row["opened_at"] == "2026-09-07T10:00:00+00:00"
    assert sp == before
    assert not manager.client.get_position_risk.mock_calls


def test_snapshot_unobserved_returns_null_not_initial_last_or_fake_zero():
    sp = _sp(); manager = _manager(sp)
    engine = _make_engine(); engine.exits = manager
    row = engine.snapshot()["tracked"][0]
    assert row["quantity"] == 2.
    for key in ("unrealized_pnl", "current_price", "mark_price", "roi_pct", "remaining_quantity"):
        assert row[key] is None
    assert row["unrealized_pnl_status"] == "unobserved"
