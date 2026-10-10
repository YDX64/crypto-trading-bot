"""TP1/TP2 emri başarısızlık sınıflaması (hata avı madde 4).

1. Belirsiz POST (ReadTimeout/5xx/-1007) + clientAlgoId uzlaştırması başarısız:
   emir borsada CANLI olabilir. `_place_conditional` özgün istisnaya
   `algo_state_unknown=True` + `client_algo_id` ekleyip AYNI istisnayı yeniden
   fırlatır (yeni alt sınıf yok); `_place_tp_safely` bunu `unidentified` sınıflar.
2. Ham miktar > 0 iken stepSize'a yuvarlanınca 0'a inen miktar emir
   GÖNDERİLMEDEN `zero_qty` ("rounded_zero") döner.

Gerçek `_place_conditional` / `place_take_profit` / `_place_tp_safely` çalışır;
mock yalnız HTTP sınırındadır (`_request_with_retry`). Yeni REST çağrısı yoktur.
"""
import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

from src.strategies.scalper.executor import ScalpExecutor
from src.trading.binance_client_improved import BinanceAPIError, ImprovedBinanceClient


class _Log:
    def __init__(self):
        self.lines = []

    def _rec(self, *a, **k):
        self.lines.append(str(a[0]) if a else "")

    debug = info = warning = error = critical = _rec


def _client(step="0.001", request=None):
    client = object.__new__(ImprovedBinanceClient)
    client.logger = MagicMock()
    filters = {
        "stepSize": Decimal(step), "tickSize": Decimal("0.1"),
        "minQty": Decimal("0"), "maxQty": Decimal("9999999"),
        "minNotional": Decimal("0"), "quantityPrecision": 3, "pricePrecision": 1,
    }

    async def get_symbol_filters(symbol):
        return filters

    client.get_symbol_filters = get_symbol_filters
    client.requests = []

    async def default_request(method, endpoint, params=None, signed=False):
        client.requests.append((method, endpoint, dict(params or {})))
        if method == "POST":
            raise httpx.ReadTimeout("cevap kayboldu")
        raise BinanceAPIError(400, -2013, "Order does not exist.", endpoint)

    client._request_with_retry = request or default_request
    return client


def _executor(client):
    ex = ScalpExecutor(client, SimpleNamespace(), SimpleNamespace(), SimpleNamespace())
    ex.logger = _Log()
    return ex


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def fast(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", fast)


async def test_readtimeout_and_unreconcilable_is_unidentified_not_rejected():
    client = _client()
    ex = _executor(client)

    result = await ScalpExecutor._place_tp_safely(
        ex, "BTCUSDT", "SELL", 102.0, 0.0105, "TP1"
    )

    assert result == (None, ScalpExecutor.TP_FAIL_UNIDENTIFIED)
    # POST bir kez + clientAlgoId sorguları (mevcut gecikme listesi kadar); EK
    # get_open_algo_orders ÇAĞRISI YOK.
    endpoints = [(m, e) for m, e, _ in client.requests]
    assert endpoints[0] == ("POST", "/fapi/v1/algoOrder")
    assert all(e == "/fapi/v1/algoOrder" for _, e in endpoints)
    assert not any(e == "/fapi/v1/openAlgoOrders" for _, e in endpoints)
    assert any("BELİRSİZ" in line for line in ex.logger.lines)


async def test_place_take_profit_reraises_original_exception_tagged():
    """Yeni alt sınıf yok: özgün httpx istisnası, etiketli, aynı tür."""
    client = _client()
    with pytest.raises(httpx.ReadTimeout) as info:
        await client.place_take_profit("BTCUSDT", "SELL", 102.0, 0.0105)
    exc = info.value
    assert type(exc) is httpx.ReadTimeout
    assert exc.algo_state_unknown is True
    post_id = client.requests[0][2]["clientAlgoId"]
    assert exc.client_algo_id == post_id
    # Uzlaştırma sorguları aynı kimliği kullandı
    assert {p.get("clientAlgoId") for m, _, p in client.requests if m == "GET"} == {post_id}


async def test_definitive_query_error_after_unknown_post_is_also_tagged():
    async def request(method, endpoint, params=None, signed=False):
        if method == "POST":
            raise BinanceAPIError(503, None, "service unavailable", endpoint)
        raise BinanceAPIError(401, -2015, "Invalid API-key", endpoint)

    client = _client(request=request)
    ex = _executor(client)
    assert await ScalpExecutor._place_tp_safely(
        ex, "BTCUSDT", "SELL", 102.0, 0.0105, "TP2"
    ) == (None, ScalpExecutor.TP_FAIL_UNIDENTIFIED)


async def test_definitive_post_rejection_stays_rejected_and_untagged():
    async def request(method, endpoint, params=None, signed=False):
        raise BinanceAPIError(400, -2022, "ReduceOnly Order is rejected.", endpoint)

    client = _client(request=request)
    ex = _executor(client)
    result = await ScalpExecutor._place_tp_safely(
        ex, "BTCUSDT", "SELL", 102.0, 0.0105, "TP1"
    )
    assert result == (None, ScalpExecutor.TP_FAIL_REJECTED)


async def test_reconciled_success_still_returns_algo_id():
    state = {"n": 0}

    async def request(method, endpoint, params=None, signed=False):
        if method == "POST":
            raise httpx.ReadTimeout("kayıp")
        state["n"] += 1
        return {"algoId": 777, "clientAlgoId": params["clientAlgoId"]}

    client = _client(request=request)
    ex = _executor(client)
    assert await ScalpExecutor._place_tp_safely(
        ex, "BTCUSDT", "SELL", 102.0, 0.0105, "TP1"
    ) == ("777", None)


async def test_rounded_zero_quantity_is_zero_qty_and_sends_nothing():
    """0.0005 > 0 ama stepSize 0.001 → 0: emir GÖNDERİLMEZ, REST yok."""
    client = _client(step="0.001")
    ex = _executor(client)

    result = await ScalpExecutor._place_tp_safely(
        ex, "BTCUSDT", "SELL", 102.0, 0.0005, "TP1"
    )

    assert result == (None, ScalpExecutor.TP_FAIL_ZERO_QTY)
    assert client.requests == []
    assert any("rounded_zero" in line for line in ex.logger.lines)


async def test_quantity_that_survives_rounding_is_still_sent():
    posted = []

    async def request(method, endpoint, params=None, signed=False):
        posted.append(dict(params))
        return {"algoId": 5, "clientAlgoId": params["clientAlgoId"]}

    client = _client(step="0.001", request=request)
    ex = _executor(client)
    assert await ScalpExecutor._place_tp_safely(
        ex, "BTCUSDT", "SELL", 102.0, 0.0019, "TP1"
    ) == ("5", None)
    assert posted[0]["quantity"] == 0.001


def test_order_health_counter_keys_unchanged():
    ex = _executor(_client())
    assert set(ex.order_health_snapshot()) == {
        "last_at", "last_symbol", "partial_fill_split", "tp1_missing",
        "tp1_unidentified", "tp2_missing", "tp2_unidentified", "tp_wrong_side",
        "window",
    }
