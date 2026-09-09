"""D37: pending uncertainty must hold entries without starving live exits."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.strategies.scalper.executor import PendingRecoveryError
from src.trading.position_manager import UnprotectedPositionError
from tests.test_runtime_liveness import _make_engine


def make_safety_engine(tmp_path):
    engine = _make_engine(pending={"ETHUSDT"})
    engine.cfg.scalper_entry_halt_enabled = False
    engine._entry_halt_path = tmp_path / "entry-halt.json"
    engine._ai_gate_observe_filled = MagicMock()
    engine._sync_scalper_reservations = MagicMock()
    engine._forensics_postmortem_schedule = MagicMock()
    for name in ("_apply_structure_exits", "_apply_tv_event_exits",
                 "_close_stale_profitable_positions", "_reap_aged_positions",
                 "_update_kill_switch"):
        setattr(engine, name, AsyncMock())
    return engine


def protected_position():
    return SimpleNamespace(position=SimpleNamespace(symbol="BTCUSDT", entry_price=100),
                           signal=SimpleNamespace(direction=SimpleNamespace(value="LONG")))


@pytest.mark.parametrize("blocked_by", [None, "_kill_switch", "_entry_halted"])
async def test_pending_failure_runs_all_live_exit_stages_then_reports_failure(tmp_path, blocked_by):
    engine = make_safety_engine(tmp_path)
    if blocked_by:
        setattr(engine, blocked_by, True)
    failure = PendingRecoveryError("known fill; cancel unavailable",
                                   opened_positions=[protected_position()])
    pending = engine.executor.cancel_all_pending if blocked_by else engine.executor.check_pending
    pending.side_effect = failure
    stages = []

    def stage(name):
        async def run():
            assert engine._entry_halted
            assert json.loads(engine._entry_halt_path.read_text())["category"] == "pending_recovery"
            stages.append(name)
        return AsyncMock(side_effect=run)

    engine.exits.step = stage("exits")
    for name in ("_apply_structure_exits", "_apply_tv_event_exits",
                 "_close_stale_profitable_positions", "_reap_aged_positions",
                 "_update_kill_switch"):
        setattr(engine, name, stage(name))
    with pytest.raises(PendingRecoveryError) as raised:
        await engine._safety_tick()
    assert raised.value is failure
    assert stages == ["exits", "_apply_structure_exits", "_apply_tv_event_exits",
                      "_close_stale_profitable_positions", "_reap_aged_positions",
                      "_update_kill_switch"]
    engine.exits.track.assert_called_once()
    assert engine._signals_today == 1
    assert failure.opened_positions == []
    engine._forensics_postmortem_schedule.assert_called_once()

    # An outer-loop retry consumes no already-delivered batch object again.
    engine.executor.cancel_all_pending.side_effect = None
    await engine._latch_entry_halt(failure, source="safety loop")
    engine.exits.track.assert_called_once()
    assert engine._entry_halted  # cancellation success is not implicit resume


async def test_later_exit_error_does_not_undo_pending_hold(tmp_path):
    engine = make_safety_engine(tmp_path)
    engine.executor.check_pending.side_effect = PendingRecoveryError("unknown fill")
    engine.exits.step.side_effect = RuntimeError("exit read unavailable")
    with pytest.raises(RuntimeError, match="exit read"):
        await engine._safety_tick()
    assert engine._entry_halted
    assert json.loads(engine._entry_halt_path.read_text())["category"] == "pending_recovery"


def test_mandatory_hold_survives_restart_when_optional_halt_disabled(tmp_path):
    engine = make_safety_engine(tmp_path)
    assert engine._record_entry_halt(PendingRecoveryError("cancel unknown"), source="test")
    restarted = make_safety_engine(tmp_path)
    restarted._load_entry_halt()
    assert restarted._entry_halted
    assert restarted._entry_halt_category == "pending_recovery"


def test_optional_protection_policy_still_disabled_but_delivers_batch(tmp_path):
    engine = make_safety_engine(tmp_path)
    failure = UnprotectedPositionError("ordinary protection failure")
    failure.opened_positions = [protected_position()]
    assert not engine._record_entry_halt(failure, source="test")
    assert not engine._entry_halted
    engine.exits.track.assert_called_once()
    assert not engine._entry_halt_path.exists()


def test_ordinary_persisted_hold_is_ignored_only_when_optional_policy_disabled(tmp_path):
    engine = make_safety_engine(tmp_path)
    engine.cfg.scalper_entry_halt_enabled = True
    engine._record_entry_halt(UnprotectedPositionError("ordinary"), source="test")
    restarted = make_safety_engine(tmp_path)
    restarted._load_entry_halt()
    assert not restarted._entry_halted
    restarted.cfg.scalper_entry_halt_enabled = True
    restarted._load_entry_halt()
    assert restarted._entry_halted


def test_corrupt_persisted_hold_cannot_be_classified_optional(tmp_path):
    engine = make_safety_engine(tmp_path)
    engine._entry_halt_path.write_text("not-json")
    engine._load_entry_halt()
    assert engine._entry_halted
    assert engine._entry_halt_category == "pending_recovery"


async def test_latch_retry_delivers_earlier_protected_fill_despite_later_failure(tmp_path):
    engine = make_safety_engine(tmp_path)
    second_failure = PendingRecoveryError("second symbol unknown",
                                         opened_positions=[protected_position()])
    engine.executor.cancel_all_pending.side_effect = second_failure
    await engine._latch_entry_halt(PendingRecoveryError("first failure"), source="test")
    engine.exits.track.assert_called_once()
    assert second_failure.opened_positions == []
    assert engine._entry_halted


async def test_risk_event_catch_preserves_batch_and_mandatory_hold(tmp_path):
    engine = make_safety_engine(tmp_path)
    engine.executor.cancel_all_pending.side_effect = PendingRecoveryError(
        "unknown", opened_positions=[protected_position()])
    await engine._cancel_pending_for_risk_event(source_label="test")
    engine.exits.track.assert_called_once()
    assert engine._entry_halted


def test_batch_is_drained_before_reentrant_tracking(tmp_path):
    engine = make_safety_engine(tmp_path)
    failure = PendingRecoveryError("unknown", opened_positions=[protected_position()])
    def track(_positions, **_kwargs):
        engine._record_entry_halt(failure, source="reentrant")
    engine._track_opened_positions = MagicMock(side_effect=track)
    engine._record_entry_halt(failure, source="outer")
    engine._track_opened_positions.assert_called_once()


async def test_normal_pending_success_tracks_before_exit_work(tmp_path):
    engine = make_safety_engine(tmp_path)
    position = protected_position()
    engine.executor.check_pending.return_value = [position]
    async def exits_step():
        engine.exits.track.assert_called_once_with(position)
    engine.exits.step.side_effect = exits_step
    await engine._safety_tick()
    assert not engine._entry_halted
    engine.executor.cancel_all_pending.assert_not_awaited()
