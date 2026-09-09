"""Daily risk observations must keep updating while the entry latch is closed."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.strategies.scalper.engine import ScalperEngine
from src.strategies.scalper.executor import ScalpExecutor


def _engine():
    engine = object.__new__(ScalperEngine)
    engine.cfg = SimpleNamespace(
        scalper_daily_loss_limit_pct=1.0,
        scalper_virtual_capital_usdt=1000.0,
        scalper_virtual_capital_start_trade_id=278,
        follower_embedded=False,
    )
    engine.logger = MagicMock()
    engine._kill_switch = False
    engine._kill_switch_day = None
    engine._signals_today = 0
    engine._daily_income_cache = (None, 0.0, None)
    engine._income_cache_close_seq = -1
    engine._virtual_equity_cache = (None, 0.0)
    engine._virtual_equity_cache_close_seq = -1
    engine.tracker = SimpleNamespace(
        close_seq=0,
        compounding_snapshot=AsyncMock(return_value={
            "eligible_realized_pnl": -106.28460067,
        }),
    )
    engine.client = SimpleNamespace(
        get_account_balance=AsyncMock(return_value=5000.0),
        get_income_history=AsyncMock(return_value=[{
            "incomeType": "REALIZED_PNL", "income": "-28.4503",
        }]),
    )
    engine.executor = ScalpExecutor(
        engine.client, SimpleNamespace(), engine.tracker, engine.cfg,
    )
    return engine


@pytest.mark.asyncio
async def test_close_after_daily_latch_refreshes_capital_without_resetting_latch():
    engine = _engine()
    await engine._update_kill_switch()
    assert engine._kill_switch is True
    assert engine.executor.last_sizing_equity == pytest.approx(893.71539933)
    original_threshold = engine._daily_loss_threshold_usdt

    # XRP #369: a later protected position closes in profit after new entry
    # is already blocked. Its ledger change must not disappear from capital.
    engine.tracker.close_seq += 1
    engine.tracker.compounding_snapshot.return_value = {
        "eligible_realized_pnl": -100.47262295,
    }
    engine.client.get_income_history.return_value = [{
        "incomeType": "REALIZED_PNL", "income": "-22.63832228",
    }]
    await engine._update_kill_switch()

    assert engine.executor.last_sizing_equity == pytest.approx(899.52737705)
    assert engine._risk_equity_usdt == pytest.approx(899.52737705)
    assert engine.executor.sizing_snapshot()["eligible_realized_pnl"] == pytest.approx(-100.47262295)
    assert engine._kill_switch is True
    assert engine._daily_loss_threshold_usdt == original_threshold
    assert engine._kill_switch_day == datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert engine.client.get_account_balance.await_count == 2
    engine.tracker.compounding_snapshot.assert_awaited_with(278, exclude_strategies=("AP",))


@pytest.mark.asyncio
async def test_latched_capital_refresh_reuses_existing_cache_without_extra_reads():
    engine = _engine()
    await engine._update_kill_switch()
    await engine._update_kill_switch()
    await engine._update_kill_switch()
    assert engine._kill_switch is True
    engine.client.get_account_balance.assert_awaited_once()
    engine.client.get_income_history.assert_awaited_once()
    engine.tracker.compounding_snapshot.assert_awaited_once()


@pytest.mark.asyncio
async def test_capital_update_does_not_release_latch_after_recovering_above_threshold():
    engine = _engine()
    await engine._update_kill_switch()
    original_threshold = engine._daily_loss_threshold_usdt
    engine.tracker.close_seq += 1
    engine.tracker.compounding_snapshot.return_value = {"eligible_realized_pnl": -70.0}
    engine.client.get_income_history.return_value = [{
        "incomeType": "REALIZED_PNL", "income": "5.0",
    }]
    await engine._update_kill_switch()
    assert engine.executor.last_sizing_equity == pytest.approx(930.0)
    assert engine._daily_pnl == 5.0
    assert engine._kill_switch is True
    assert engine._daily_loss_threshold_usdt == original_threshold


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [None, True, False, "", "NaN", "Infinity", "-Infinity", [], {}])
async def test_invalid_signed_income_fails_closed_without_caching(invalid):
    engine = _engine()
    engine.client.get_income_history.return_value = [{
        "incomeType": "REALIZED_PNL", "income": invalid,
    }]
    await engine._update_kill_switch()
    assert engine._risk_ready is False
    assert engine._daily_pnl_source == "unavailable"
    assert engine._daily_income_cache == (None, 0.0, None)
    engine.client.get_account_balance.assert_not_awaited()


@pytest.mark.asyncio
async def test_finite_income_total_overflow_fails_closed():
    engine = _engine()
    engine.client.get_income_history.return_value = [
        {"incomeType": "REALIZED_PNL", "income": "1e308"},
        {"incomeType": "REALIZED_PNL", "income": "1e308"},
    ]
    await engine._update_kill_switch()
    assert engine._risk_ready is False
    assert engine._daily_income_cache == (None, 0.0, None)


@pytest.mark.asyncio
async def test_close_during_income_read_invalidates_old_response_generation():
    engine = _engine()

    async def concurrent_close(**_kwargs):
        engine.tracker.close_seq += 1
        return [{"incomeType": "REALIZED_PNL", "income": "0"}]

    engine.client.get_income_history.side_effect = concurrent_close
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert await engine._get_account_daily_net_income(today) == 0.0
    assert engine._income_cache_close_seq == 0
    assert engine.tracker.close_seq == 1
    engine.client.get_income_history.side_effect = None
    engine.client.get_income_history.return_value = [{
        "incomeType": "REALIZED_PNL", "income": "-28.45",
    }]
    assert await engine._get_account_daily_net_income(today) == -28.45
    assert engine.client.get_income_history.await_count == 2
    assert engine._income_cache_close_seq == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, RuntimeError("unavailable"), float("nan"), float("inf"), True])
async def test_modern_capital_failure_does_not_resurrect_old_sizing_snapshot(failure):
    engine = _engine()
    await engine._update_kill_switch()
    engine.tracker.close_seq += 1
    if isinstance(failure, Exception):
        engine.executor.get_sizing_equity = AsyncMock(side_effect=failure)
    else:
        engine.executor.get_sizing_equity = AsyncMock(return_value=failure)
    await engine._update_kill_switch()
    assert engine._kill_switch is True
    assert engine._risk_ready is False
    assert engine._risk_equity_usdt is None
    assert engine._virtual_equity_cache == (None, 0.0)
    assert engine._virtual_equity_refresh_failed is True
    # The executor's old snapshot is preserved as history, but the API
    # projection is unknown rather than a freshly labeled stale number.
    assert engine.executor.last_sizing_equity == pytest.approx(893.71539933)
    projected = engine._executor_sizing_snapshot()
    assert projected["effective_equity"] is None
    assert projected["virtual_capital"] is None
    assert projected["eligible_realized_pnl"] is None
    assert projected["mode"] == "virtual_capital_error"

    engine.executor.get_sizing_equity = AsyncMock(return_value=899.52737705)
    assert await engine._virtual_risk_equity() == pytest.approx(899.52737705)
    assert engine._virtual_equity_refresh_failed is False


@pytest.mark.asyncio
async def test_missing_modern_resolver_retains_legacy_snapshot_fallback():
    engine = _engine()
    engine.executor = SimpleNamespace(last_sizing_equity=901.0)
    assert await engine._virtual_risk_equity() == 901.0
    assert getattr(engine, "_virtual_equity_refresh_failed", False) is False


def test_status_does_not_restore_stale_last_capital_after_failed_refresh():
    from test_runtime_liveness import _make_engine

    engine = _make_engine()
    engine.cfg.scalper_virtual_capital_usdt = 1000.0
    engine.cfg.scalper_virtual_capital_start_trade_id = 278
    engine.executor.last_sizing_equity = 893.71539933
    engine._virtual_equity_refresh_failed = True
    status = engine.snapshot()
    assert status["virtual_capital_enabled"] is True
    assert status["virtual_capital_start_trade_id"] == 278
    assert status["virtual_capital_current_usdt"] is None
    assert status["sizing_equity_usdt"] is None
    assert status["sizing"]["effective_equity"] is None
