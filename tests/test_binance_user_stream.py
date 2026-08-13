"""USD-M listenKey ve ORDER_TRADE_UPDATE stream testleri (gerçek ağ yok)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.trading.binance_client_improved import ImprovedBinanceClient
from src.trading.user_stream import BinanceUserDataStream


class _Response:
    def __init__(self, body=None, status_code=200):
        self._body = body
        self.status_code = status_code
        self.content = b"" if body is None else json.dumps(body).encode()
        self.text = self.content.decode()

    def json(self):
        return self._body


class _HttpRecorder:
    def __init__(self):
        self.calls = []

    async def request(self, method, url, headers):
        self.calls.append((method, url, dict(headers)))
        if method == "POST":
            return _Response({"listenKey": "test-listen-key"})
        return _Response({})


async def _no_rate_limit_wait():
    return None


class TestListenKeyClient:
    async def test_create_keepalive_delete_use_api_key_header_without_signature(
        self, monkeypatch
    ):
        client = object.__new__(ImprovedBinanceClient)
        client.api_key = "unit-test-api-key"
        client.api_secret = "must-not-be-used"
        client.base_url = "https://testnet.binancefuture.com"
        client.client = _HttpRecorder()
        client.max_retries = 1
        client.retry_delay = 0.0
        client._listen_key = None
        monkeypatch.setattr(
            "src.trading.binance_client_improved.rate_limiter.wait_for_binance",
            _no_rate_limit_wait,
        )

        assert await client.create_listen_key() == "test-listen-key"
        await client.keepalive_listen_key()
        await client.delete_listen_key()

        assert [call[0] for call in client.client.calls] == ["POST", "PUT", "DELETE"]
        for _, url, headers in client.client.calls:
            assert url == "https://testnet.binancefuture.com/fapi/v1/listenKey"
            assert headers["X-MBX-APIKEY"] == "unit-test-api-key"
            assert "signature" not in url
            assert "timestamp" not in url
        assert client.listen_key is None

    async def test_keepalive_without_active_key_fails_closed(self):
        client = object.__new__(ImprovedBinanceClient)
        client._listen_key = None
        with pytest.raises(RuntimeError, match="listenKey"):
            await client.keepalive_listen_key()

    async def test_income_history_uses_signed_endpoint_and_bounded_limit(self):
        client = object.__new__(ImprovedBinanceClient)
        client._request_with_retry = AsyncMock(return_value=[{"income": "1.25"}])

        rows = await client.get_income_history(
            start_time_ms=100,
            end_time_ms=200,
            symbol="btcusdt",
            income_type="commission",
            limit=5000,
        )

        assert rows == [{"income": "1.25"}]
        client._request_with_retry.assert_awaited_once_with(
            "GET",
            "/fapi/v1/income",
            params={
                "limit": 1000,
                "startTime": 100,
                "endTime": 200,
                "symbol": "BTCUSDT",
                "incomeType": "COMMISSION",
            },
            signed=True,
        )

    async def test_wallet_balance_does_not_shrink_with_available_margin(self):
        client = object.__new__(ImprovedBinanceClient)
        client.logger = MagicMock()
        client._request_with_retry = AsyncMock(
            return_value={
                "totalWalletBalance": "5123.45",
                "totalAvailableBalance": "4679.06",
            }
        )

        assert await client.get_wallet_balance() == 5123.45


class _ListenClient:
    def __init__(self):
        self.base_url = "https://testnet.binancefuture.com"
        self.create_calls = 0
        self.keepalive_calls = 0
        self.delete_calls = 0

    async def create_listen_key(self):
        self.create_calls += 1
        return f"key-{self.create_calls}"

    async def keepalive_listen_key(self):
        self.keepalive_calls += 1

    async def delete_listen_key(self):
        self.delete_calls += 1

    def invalidate_listen_key(self):
        pass


class _OneEventWebsocket:
    def __init__(self, event):
        self.event = event
        self.sent = False
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.sent:
            raise StopAsyncIteration
        self.sent = True
        return json.dumps(self.event)

    async def close(self):
        self.closed = True


class _FailingConnection:
    async def __aenter__(self):
        raise OSError("simulated websocket disconnect")

    async def __aexit__(self, exc_type, exc, tb):
        return False


class TestUserDataStream:
    async def test_dispatches_order_update_and_uses_testnet_ws_host(self):
        client = _ListenClient()
        received = []
        connect_calls = []
        event = {"e": "ORDER_TRADE_UPDATE", "o": {"s": "BTCUSDT"}}
        stream = None

        async def callback(update):
            received.append(update)
            stream.running = False

        def connect(url, **kwargs):
            connect_calls.append((url, kwargs))
            return _OneEventWebsocket(event)

        stream = BinanceUserDataStream(
            client, callback, connect_factory=connect
        )
        await stream.run()

        assert received == [event]
        assert connect_calls[0][0] == "wss://fstream.binancefuture.com/ws/key-1"
        assert connect_calls[0][1]["ping_interval"] == 20
        assert client.create_calls == 1
        assert stream.connected is False

    async def test_reconnects_after_disconnect_and_keeps_rest_fallback_independent(self):
        client = _ListenClient()
        received = []
        connection_attempts = 0
        event = {"e": "ORDER_TRADE_UPDATE", "o": {"s": "ETHUSDT"}}
        stream = None

        async def callback(update):
            received.append(update)
            stream.running = False

        def connect(url, **kwargs):
            nonlocal connection_attempts
            connection_attempts += 1
            if connection_attempts == 1:
                return _FailingConnection()
            return _OneEventWebsocket(event)

        stream = BinanceUserDataStream(
            client,
            callback,
            connect_factory=connect,
            reconnect_min_seconds=0.1,
            reconnect_max_seconds=0.1,
        )
        await stream.run()

        assert connection_attempts == 2
        assert client.create_calls == 2
        assert stream.reconnect_count == 1
        assert received == [event]
