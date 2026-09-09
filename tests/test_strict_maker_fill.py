"""D37 production resolver boundary: one coherent order/quantity/average pair."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.trading.binance_client_improved import BinanceAPIError
from src.trading.position_manager import PositionManager


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("src.trading.position_manager.asyncio.sleep", AsyncMock())
    return SimpleNamespace(get_order=AsyncMock(), get_position_risk=AsyncMock())


def order(**values):
    return {"symbol": "TESTUSDT", "orderId": 555, "clientOrderId": "awa2sc_test",
            "executedQty": "0.6", "avgPrice": "0", **values}


async def test_complete_initial_pair_requires_no_fallback(client):
    assert await PositionManager(client).resolve_fill(
        "TESTUSDT", order(avgPrice="100"), strict_order_evidence=True
    ) == (100, 0.6)
    client.get_order.assert_not_awaited()
    client.get_position_risk.assert_not_awaited()


@pytest.mark.parametrize("quantity", [None, "0", "0.4", "nan", "inf", True])
async def test_fresh_average_cannot_pair_with_prior_quantity(client, quantity):
    client.get_order.return_value = order(executedQty=quantity, avgPrice="100")
    with pytest.raises(BinanceAPIError, match="coherent cumulative maker fill"):
        await PositionManager(client).resolve_fill(
            "TESTUSDT", order(), strict_order_evidence=True
        )
    assert client.get_order.await_count == 3
    client.get_position_risk.assert_not_awaited()


async def test_missing_fresh_quantity_cannot_borrow_prior_quantity(client):
    fresh = order(avgPrice="100")
    del fresh["executedQty"]
    client.get_order.return_value = fresh
    with pytest.raises(BinanceAPIError):
        await PositionManager(client).resolve_fill("TESTUSDT", order(), strict_order_evidence=True)
    client.get_position_risk.assert_not_awaited()


@pytest.mark.parametrize("price", [None, "0", "nan", "inf", True])
async def test_invalid_average_remains_unknown(client, price):
    client.get_order.return_value = order(avgPrice=price)
    with pytest.raises(BinanceAPIError):
        await PositionManager(client).resolve_fill("TESTUSDT", order(), strict_order_evidence=True)
    client.get_position_risk.assert_not_awaited()


@pytest.mark.parametrize("identity", [
    {"orderId": 556}, {"orderId": None}, {"clientOrderId": "different"}, {"symbol": "OTHERUSDT"},
])
async def test_wrong_response_identity_cannot_supply_average(client, identity):
    client.get_order.return_value = order(avgPrice="100", **identity)
    with pytest.raises(ValueError, match="identity mismatch"):
        await PositionManager(client).resolve_fill("TESTUSDT", order(), strict_order_evidence=True)
    client.get_position_risk.assert_not_awaited()


async def test_larger_unpriced_observation_advances_minimum_quantity(client):
    client.get_order.side_effect = [
        order(executedQty="0.8"),
        order(executedQty="0.6", avgPrice="100"),
        order(executedQty="0.8", avgPrice="101"),
    ]
    assert await PositionManager(client).resolve_fill(
        "TESTUSDT", order(), strict_order_evidence=True
    ) == (101, 0.8)
    assert client.get_order.await_count == 3
    client.get_position_risk.assert_not_awaited()


async def test_default_generic_resolver_contract_is_unchanged(client):
    manager = PositionManager(client)
    manager._resolve_fill = AsyncMock(return_value=(100, 0.6))
    original = order()
    assert await manager.resolve_fill("TESTUSDT", original) == (100, 0.6)
    manager._resolve_fill.assert_awaited_once_with("TESTUSDT", original)
