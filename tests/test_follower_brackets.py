"""LeverageBracketCache — borsa kaldıraç dilimi önbelleği (D20).

Bu modül bir GÜVENLİK KAPISIDIR: `plan.resolve_leverage` dilim listesi boş
gelirse girişi tamamen reddeder (fail-closed). Bu yüzden önbelleğin üç
davranışı kanıtlanmalıdır:

  1. **Fail-closed**: okuma başarısız ve elde kayıt yoksa BOŞ liste döner
     (100x'e "devam etmek" YOKTUR).
  2. **Bayat > yok**: okuma başarısız ama elde eski kayıt varsa o KULLANILIR —
     dilimler saatler içinde değişmez, ama veri yokluğu girişi kapatır.
  3. **Ağırlık disiplini**: taze kayıt varken ağa ÇIKILMAZ; eşzamanlı istekler
     tek uçuşta birleşir (Binance ağırlık bütçesi / 418, bkz. docs/RUNBOOK.md).

GERÇEK AĞ YOK: `client._request_with_retry` sahtelenir.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.strategies.follower.brackets import LeverageBracketCache

BTC_PAYLOAD = [
    {
        "symbol": "BTCUSDT",
        "brackets": [
            {
                "bracket": 1,
                "initialLeverage": 125,
                "notionalCap": 50000,
                "notionalFloor": 0,
                "maintMarginRatio": 0.004,
                "cum": 0.0,
            },
            {
                "bracket": 2,
                "initialLeverage": 100,
                "notionalCap": 250000,
                "notionalFloor": 50000,
                "maintMarginRatio": 0.005,
                "cum": 50.0,
            },
        ],
    }
]


class _FakeClient:
    """`_request_with_retry` çağrılarını sayan sahte istemci."""

    def __init__(self, payload=None, error: Exception | None = None):
        self.payload = payload if payload is not None else BTC_PAYLOAD
        self.error = error
        self.calls: list = []
        self.delay = 0.0

    async def _request_with_retry(self, method, path, params=None, signed=False):
        self.calls.append((method, path, dict(params or {}), signed))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.payload


def _cfg(ttl: float = 21600.0):
    return SimpleNamespace(follower_bracket_cache_ttl_seconds=ttl)


class TestFetchAndParse:
    async def test_fetch_parses_brackets(self):
        client = _FakeClient()
        cache = LeverageBracketCache(client, _cfg())

        rows = await cache.get("BTCUSDT")

        assert [r.max_leverage for r in rows] == [125, 100]
        assert rows[0].maint_margin_ratio == pytest.approx(0.004)
        assert rows[0].notional_cap == pytest.approx(50000)
        assert rows[1].notional_floor == pytest.approx(50000)

    async def test_request_is_signed_and_symbol_scoped(self):
        client = _FakeClient()
        cache = LeverageBracketCache(client, _cfg())

        await cache.get("btcusdt")

        method, path, params, signed = client.calls[0]
        assert method == "GET"
        assert path == "/fapi/v1/leverageBracket"
        # Sembol BÜYÜK harfe normalize edilir (önbellek anahtarı da öyle).
        assert params == {"symbol": "BTCUSDT"}
        assert signed is True

    async def test_symbol_case_shares_one_cache_entry(self):
        client = _FakeClient()
        cache = LeverageBracketCache(client, _cfg())

        await cache.get("BTCUSDT")
        await cache.get("btcusdt")

        assert len(client.calls) == 1


class TestCaching:
    async def test_second_call_uses_cache(self):
        client = _FakeClient()
        cache = LeverageBracketCache(client, _cfg())

        first = await cache.get("BTCUSDT")
        second = await cache.get("BTCUSDT")

        assert len(client.calls) == 1
        assert first == second

    async def test_expired_ttl_refetches(self):
        client = _FakeClient()
        cache = LeverageBracketCache(client, _cfg(ttl=0.001))

        await cache.get("BTCUSDT")
        await asyncio.sleep(0.005)
        await cache.get("BTCUSDT")

        assert len(client.calls) == 2

    async def test_cached_helper_never_hits_network(self):
        client = _FakeClient()
        cache = LeverageBracketCache(client, _cfg())

        assert cache.cached("BTCUSDT") == []
        assert client.calls == []

        await cache.get("BTCUSDT")
        assert len(cache.cached("BTCUSDT")) == 2

    async def test_concurrent_gets_collapse_to_one_request(self):
        """Tek uçuş kilidi: 5 eşzamanlı istek TEK ağ çağrısı yapar."""
        client = _FakeClient()
        client.delay = 0.01
        cache = LeverageBracketCache(client, _cfg())

        results = await asyncio.gather(*[cache.get("BTCUSDT") for _ in range(5)])

        assert len(client.calls) == 1
        assert all(len(rows) == 2 for rows in results)

    async def test_invalid_ttl_falls_back_to_default(self):
        client = _FakeClient()
        cache = LeverageBracketCache(client, SimpleNamespace(
            follower_bracket_cache_ttl_seconds="abc"
        ))

        await cache.get("BTCUSDT")
        await cache.get("BTCUSDT")

        assert len(client.calls) == 1


class TestFailClosed:
    async def test_read_error_without_cache_returns_empty(self):
        client = _FakeClient(error=RuntimeError("binance down"))
        cache = LeverageBracketCache(client, _cfg())

        assert await cache.get("BTCUSDT") == []

    async def test_unparseable_payload_returns_empty(self):
        client = _FakeClient(payload={"symbol": "BTCUSDT", "brackets": "bozuk"})
        cache = LeverageBracketCache(client, _cfg())

        assert await cache.get("BTCUSDT") == []

    async def test_empty_result_is_not_cached(self):
        """Başarısız okuma önbelleğe YAZILMAZ — sonraki giriş yeniden dener."""
        client = _FakeClient(error=RuntimeError("kısa süreli hata"))
        cache = LeverageBracketCache(client, _cfg())

        assert await cache.get("BTCUSDT") == []
        client.error = None
        rows = await cache.get("BTCUSDT")

        assert len(rows) == 2
        assert len(client.calls) == 2

    async def test_stale_cache_survives_a_failed_refresh(self):
        """Bayat kayıt > veri yokluğu: yenileme hatasında eski dilim kullanılır."""
        client = _FakeClient()
        cache = LeverageBracketCache(client, _cfg(ttl=0.001))

        fresh = await cache.get("BTCUSDT")
        await asyncio.sleep(0.005)
        client.error = RuntimeError("geçici ağ hatası")
        stale = await cache.get("BTCUSDT")

        assert stale == fresh
        assert len(client.calls) == 2


class TestWarmAndSnapshot:
    async def test_warm_counts_ready_symbols(self):
        client = _FakeClient()
        cache = LeverageBracketCache(client, _cfg())

        ready = await cache.warm(["BTCUSDT", "ETHUSDT"])

        assert ready == 2
        assert len(client.calls) == 2

    async def test_warm_does_not_raise_on_failure(self):
        client = _FakeClient(error=RuntimeError("hepsi düştü"))
        cache = LeverageBracketCache(client, _cfg())

        assert await cache.warm(["BTCUSDT", "ETHUSDT"]) == 0

    async def test_snapshot_reports_max_leverage_without_secrets(self):
        client = _FakeClient()
        cache = LeverageBracketCache(client, _cfg())
        await cache.get("BTCUSDT")

        snap = cache.snapshot()

        assert snap["BTCUSDT"]["max_leverage"] == 125
        assert snap["BTCUSDT"]["brackets"] == 2
        assert snap["BTCUSDT"]["age_seconds"] >= 0
        assert set(snap["BTCUSDT"]) == {"max_leverage", "brackets", "age_seconds"}

    async def test_snapshot_empty_before_any_fetch(self):
        cache = LeverageBracketCache(_FakeClient(), _cfg())
        assert cache.snapshot() == {}
