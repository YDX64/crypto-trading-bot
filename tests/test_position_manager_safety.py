"""Focused execution-safety tests for PositionManager."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import src.models.waiting_signal  # noqa: F401 - SQLAlchemy relationship setup
from src.models.position import PositionSide
from src.trading.binance_client_improved import BinanceAPIError
from src.trading.position_manager import PositionManager, UnprotectedPositionError


class _ImmediateTriggerClient:
    async def get_position_risk(self, _symbol):
        return {"positionAmt": "1.25"}

    async def place_stop_loss(self, **_kwargs):
        raise BinanceAPIError(400, -2021, "Order would immediately trigger")


def _position():
    return SimpleNamespace(
        symbol="TESTUSDT",
        side=PositionSide.LONG,
        sl_order_id="123",
    )


@pytest.mark.asyncio
async def test_crossed_replacement_stop_flattens_position_immediately():
    manager = PositionManager(_ImmediateTriggerClient())
    manager._emergency_close = AsyncMock(return_value=True)

    result = await manager.replace_stop_loss(_position(), 101.0)

    assert result is False
    manager._emergency_close.assert_awaited_once_with("TESTUSDT")


@pytest.mark.asyncio
async def test_crossed_stop_and_failed_flatten_raises_global_safety_error():
    manager = PositionManager(_ImmediateTriggerClient())
    manager._emergency_close = AsyncMock(return_value=False)

    with pytest.raises(UnprotectedPositionError):
        await manager.replace_stop_loss(_position(), 101.0)
