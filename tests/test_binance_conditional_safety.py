"""Binance fee/fill ve idempotent algo-order istemci testleri (ağ yok)."""

from __future__ import annotations

import asyncio
import re
from decimal import Decimal
from urllib.parse import parse_qs, urlparse
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.trading.binance_client_improved import (
    BinanceAPIError,
    ImprovedBinanceClient,
)


def _bare_client() -> ImprovedBinanceClient:
    client = object.__new__(ImprovedBinanceClient)
    client.logger = MagicMock()
    return client


class TestCommissionAndTradeQueries:
    async def test_commission_rate_is_decimal_validated_and_cached(self):
        client = _bare_client()
        client._commission_rates = {}
        client._commission_lock = asyncio.Lock()
        client._request_with_retry = AsyncMock(
            return_value={
                "symbol": "BTCUSDT",
                "makerCommissionRate": "0.0002",
                "takerCommissionRate": "0.0004",
            }
        )

        first = await client.get_user_commission_rate("btcusdt")
        second = await client.get_user_commission_rate("BTCUSDT")

        assert first["makerCommissionRate"] == Decimal("0.0002")
        assert first["takerCommissionRate"] == Decimal("0.0004")
        assert isinstance(first["makerCommissionRate"], Decimal)
        assert second == first
        assert second is not first
        client._request_with_retry.assert_awaited_once_with(
            "GET",
            "/fapi/v1/commissionRate",
            params={"symbol": "BTCUSDT"},
            signed=True,
        )

    @pytest.mark.parametrize(
        "field,value",
        [
            ("makerCommissionRate", "nan"),
            ("makerCommissionRate", "-0.0001"),
            ("takerCommissionRate", "not-a-number"),
            ("takerCommissionRate", "1"),
        ],
    )
    async def test_commission_rate_rejects_malformed_values(self, field, value):
        client = _bare_client()
        client._commission_rates = {}
        client._commission_lock = asyncio.Lock()
        body = {
            "symbol": "BTCUSDT",
            "makerCommissionRate": "0.0002",
            "takerCommissionRate": "0.0004",
        }
        body[field] = value
        client._request_with_retry = AsyncMock(return_value=body)

        with pytest.raises(BinanceAPIError, match=field):
            await client.get_user_commission_rate("BTCUSDT")

    async def test_account_trades_uses_signed_filters_and_validates_list(self):
        client = _bare_client()
        rows = [{"orderId": 73, "commission": "0.04", "maker": False}]
        client._request_with_retry = AsyncMock(return_value=rows)

        result = await client.get_account_trades(
            "ethusdt", order_id=73, limit=5000
        )

        assert result == rows
        client._request_with_retry.assert_awaited_once_with(
            "GET",
            "/fapi/v1/userTrades",
            params={
                "symbol": "ETHUSDT",
                "limit": 1000,
                "orderId": 73,
            },
            signed=True,
        )

        client._request_with_retry.reset_mock()
        await client.get_account_trades(
            "ETHUSDT", start_time=100, end_time=200, limit=50
        )
        client._request_with_retry.assert_awaited_once_with(
            "GET",
            "/fapi/v1/userTrades",
            params={
                "symbol": "ETHUSDT",
                "limit": 50,
                "startTime": 100,
                "endTime": 200,
            },
            signed=True,
        )

        client._request_with_retry = AsyncMock(return_value={"not": "a list"})
        with pytest.raises(BinanceAPIError, match="userTrades"):
            await client.get_account_trades("ETHUSDT")

    async def test_account_trades_rejects_reversed_time_range_without_request(self):
        client = _bare_client()
        client._request_with_retry = AsyncMock()

        with pytest.raises(ValueError, match="start_time"):
            await client.get_account_trades(
                "BTCUSDT", start_time=200, end_time=100
            )
        client._request_with_retry.assert_not_awaited()

        with pytest.raises(ValueError, match="order_id"):
            await client.get_account_trades(
                "BTCUSDT", order_id=5, start_time=100
            )
        client._request_with_retry.assert_not_awaited()


class TestAlgoQueryAndIdempotency:
    async def test_get_algo_order_accepts_exactly_one_identifier(self):
        client = _bare_client()
        client._request_with_retry = AsyncMock(
            return_value={"algoId": 42, "clientAlgoId": "awa_test"}
        )

        assert (await client.get_algo_order(algo_id=42))["algoId"] == 42
        client._request_with_retry.assert_awaited_once_with(
            "GET",
            "/fapi/v1/algoOrder",
            params={"algoId": 42},
            signed=True,
        )

        client._request_with_retry.reset_mock()
        await client.get_algo_order(client_algo_id="awa_test")
        client._request_with_retry.assert_awaited_once_with(
            "GET",
            "/fapi/v1/algoOrder",
            params={"clientAlgoId": "awa_test"},
            signed=True,
        )

        with pytest.raises(ValueError, match="Tam olarak bir"):
            await client.get_algo_order()
        with pytest.raises(ValueError, match="Tam olarak bir"):
            await client.get_algo_order(algo_id=42, client_algo_id="awa_test")

    async def test_each_conditional_intent_has_unique_legal_client_algo_id(self):
        client = _bare_client()
        posted_ids = []

        async def request(method, endpoint, params=None, signed=False):
            assert method == "POST"
            assert endpoint == "/fapi/v1/algoOrder"
            assert signed is True
            posted_ids.append(params["clientAlgoId"])
            return {
                "algoId": len(posted_ids),
                "clientAlgoId": params["clientAlgoId"],
            }

        client._request_with_retry = request
        first = await client._place_conditional(
            "BTCUSDT", {"side": "SELL", "type": "STOP_MARKET"}, "sl"
        )
        second = await client._place_conditional(
            "BTCUSDT", {"side": "SELL", "type": "STOP_MARKET"}, "sl"
        )

        assert posted_ids[0] != posted_ids[1]
        assert all(len(value) <= 36 for value in posted_ids)
        assert all(re.fullmatch(r"[.A-Za-z0-9_:/-]{1,36}", value) for value in posted_ids)
        assert first["orderId"] == first["algoId"] == 1
        assert second["orderId"] == second["algoId"] == 2
        assert first["isAlgo"] is True

    @pytest.mark.parametrize(
        "post_error",
        [
            httpx.ReadTimeout("POST response lost"),
            BinanceAPIError(
                400, -4116, "clientOrderId is duplicated", "/fapi/v1/algoOrder"
            ),
        ],
    )
    async def test_lost_or_duplicate_post_reconciles_by_same_client_algo_id(
        self, post_error
    ):
        client = _bare_client()
        calls = []

        async def request(method, endpoint, params=None, signed=False):
            calls.append((method, endpoint, dict(params or {}), signed))
            if method == "POST":
                raise post_error
            assert method == "GET"
            return {
                "algoId": 991,
                "clientAlgoId": params["clientAlgoId"],
                "algoStatus": "TRIGGERED",
                "actualOrderId": "771",
                "actualQty": "0.005",
            }

        client._request_with_retry = request
        result = await client._place_conditional(
            "BTCUSDT", {"side": "SELL", "type": "STOP_MARKET"}, "sl"
        )

        assert [call[0] for call in calls] == ["POST", "GET"]
        post_client_id = calls[0][2]["clientAlgoId"]
        assert calls[1][2] == {"clientAlgoId": post_client_id}
        assert result["clientAlgoId"] == post_client_id
        assert result["reconciled"] is True
        assert result["actualOrderId"] == "771"
        assert result["actualQty"] == "0.005"

    async def test_definitive_conditional_rejection_is_not_reconciled(self):
        client = _bare_client()
        rejection = BinanceAPIError(
            400, -2021, "Order would immediately trigger", "/fapi/v1/algoOrder"
        )
        client._request_with_retry = AsyncMock(side_effect=rejection)

        with pytest.raises(BinanceAPIError) as raised:
            await client._place_conditional(
                "BTCUSDT", {"side": "SELL", "type": "STOP_MARKET"}, "sl"
            )
        assert raised.value is rejection
        assert client._request_with_retry.await_count == 1

    async def test_malformed_success_response_is_reconciled_not_reposted(self):
        client = _bare_client()
        calls = []

        async def request(method, endpoint, params=None, signed=False):
            calls.append((method, dict(params or {})))
            if method == "POST":
                # HTTP 200 alındı ama kimliksiz/eksik gövde: kabul sonucu
                # belirsizdir; yeni POST değil clientAlgoId sorgusu gerekir.
                return {"code": 200, "clientAlgoId": params["clientAlgoId"]}
            return {
                "algoId": 818,
                "clientAlgoId": params["clientAlgoId"],
                "algoStatus": "NEW",
            }

        client._request_with_retry = request
        result = await client._place_conditional(
            "BTCUSDT", {"side": "SELL", "type": "STOP_MARKET"}, "sl"
        )

        assert [method for method, _ in calls] == ["POST", "GET"]
        assert result["algoId"] == 818
        assert result["reconciled"] is True

    async def test_eventual_not_found_retries_only_get_with_same_id(
        self, monkeypatch
    ):
        client = _bare_client()
        calls = []
        sleeps = []
        not_found = BinanceAPIError(
            400, -2013, "Order does not exist", "/fapi/v1/algoOrder"
        )

        async def request(method, endpoint, params=None, signed=False):
            calls.append((method, dict(params or {})))
            if method == "POST":
                raise httpx.ReadTimeout("POST response lost")
            get_count = sum(1 for recorded_method, _ in calls if recorded_method == "GET")
            if get_count < 3:
                raise not_found
            return {
                "algoId": 555,
                "clientAlgoId": params["clientAlgoId"],
                "algoStatus": "NEW",
            }

        async def fake_sleep(delay):
            sleeps.append(delay)

        client._request_with_retry = request
        monkeypatch.setattr(
            "src.trading.binance_client_improved.asyncio.sleep", fake_sleep
        )

        result = await client._place_conditional(
            "BTCUSDT", {"side": "SELL", "type": "STOP_MARKET"}, "sl"
        )

        assert [method for method, _ in calls] == ["POST", "GET", "GET", "GET"]
        posted_id = calls[0][1]["clientAlgoId"]
        assert all(params == {"clientAlgoId": posted_id} for _, params in calls[1:])
        assert sleeps == [0.1, 0.3]
        assert result["algoId"] == 555
        assert result["reconciled"] is True

    async def test_transport_retry_keeps_identical_client_algo_id(self, monkeypatch):
        requests = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if len(requests) == 1:
                raise httpx.ReadTimeout("lost", request=request)
            return httpx.Response(200, json={"algoId": 1}, request=request)

        async def no_rate_limit_wait():
            return None

        client = _bare_client()
        client.api_key = "unit-key"
        client.api_secret = "unit-secret"
        client.base_url = "https://unit.test"
        client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client.max_retries = 2
        client.retry_delay = 0.0
        client.recv_window = 5000
        client._sync_time_offset = AsyncMock(return_value=0)
        monkeypatch.setattr(
            "src.trading.binance_client_improved.rate_limiter.wait_for_binance",
            no_rate_limit_wait,
        )

        try:
            await client._request_with_retry(
                "POST",
                "/fapi/v1/algoOrder",
                params={
                    "algoType": "CONDITIONAL",
                    "symbol": "BTCUSDT",
                    "clientAlgoId": "awa_same_intent",
                },
                signed=True,
            )
        finally:
            await client.client.aclose()

        assert len(requests) == 2
        ids = [
            parse_qs(urlparse(str(request.url)).query)["clientAlgoId"][0]
            for request in requests
        ]
        assert ids == ["awa_same_intent", "awa_same_intent"]


class TestProtectivePriceRounding:
    async def test_stop_and_tp_round_outward_by_closing_side(self):
        client = _bare_client()
        client.get_symbol_filters = AsyncMock(
            return_value={"tickSize": Decimal("0.1")}
        )

        assert await client.quantize_protective_price(
            "BTCUSDT", 100.01, "SELL"
        ) == pytest.approx(100.1)
        assert await client.quantize_protective_price(
            "BTCUSDT", 100.09, "BUY"
        ) == pytest.approx(100.0)

    async def test_existing_maker_rounding_remains_unchanged(self):
        client = _bare_client()
        client.get_symbol_filters = AsyncMock(
            return_value={"tickSize": Decimal("0.1")}
        )

        assert await client.quantize_maker_price(
            "BTCUSDT", 100.09, "BUY"
        ) == pytest.approx(100.0)
        assert await client.quantize_maker_price(
            "BTCUSDT", 100.01, "SELL"
        ) == pytest.approx(100.1)
