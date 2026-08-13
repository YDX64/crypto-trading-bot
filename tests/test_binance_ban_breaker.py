"""Binance IP ban devre kesicisi (2026-08-12).

Kök olay: -1003/418 "Way too many requests; IP banned until <ms>" sonrası
2 sn'lik güvenlik döngüsü istek atmaya devam edip yasağı uzattı; bot gün
içinde saatlerce fail-closed kilitli kaldı (24 adet -1003 kaydı).

Sözleşme: 418 veya code=-1003 görüldüğünde sınıf düzeyi kesici kurulur;
ban bitene kadar süreçteki HİÇBİR istemci örneği ağa istek çıkarmaz —
istekler ağa dokunmadan BinanceAPIError(418) ile kısa devre olur.
"""

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.trading.binance_client_improved import (
    BinanceAPIError,
    ImprovedBinanceClient,
)


@pytest.fixture(autouse=True)
def _reset_breaker():
    """Sınıf düzeyi kesici durumu testler arasına sızmasın."""
    ImprovedBinanceClient._rest_blocked_until = 0.0
    ImprovedBinanceClient._breaker_last_log = 0.0
    yield
    ImprovedBinanceClient._rest_blocked_until = 0.0
    ImprovedBinanceClient._breaker_last_log = 0.0


def _bare_client() -> ImprovedBinanceClient:
    client = object.__new__(ImprovedBinanceClient)
    client.logger = MagicMock()
    client.base_url = "https://testnet.invalid"
    return client


class TestTripBreaker:
    def test_parses_banned_until_epoch_ms(self):
        future_ms = int((time.time() + 600) * 1000)
        until = ImprovedBinanceClient._trip_breaker(
            f"Way too many requests; IP(1.2.3.4) banned until {future_ms}. "
            f"Please use the websocket for live updates to avoid bans.",
            default_seconds=180.0,
        )
        assert until == pytest.approx(future_ms / 1000.0 + 5.0, abs=0.01)
        assert ImprovedBinanceClient._rest_blocked_until == until

    def test_no_timestamp_falls_back_to_default(self):
        before = time.time()
        until = ImprovedBinanceClient._trip_breaker(
            "Too many requests queued.", default_seconds=90.0
        )
        assert before + 89.0 <= until <= time.time() + 91.0

    def test_never_shortens_existing_longer_block(self):
        far = time.time() + 3600
        ImprovedBinanceClient._rest_blocked_until = far
        until = ImprovedBinanceClient._trip_breaker("x", default_seconds=60.0)
        assert until == far

    def test_stale_ban_timestamp_ignored(self):
        past_ms = int((time.time() - 600) * 1000)
        before = time.time()
        until = ImprovedBinanceClient._trip_breaker(
            f"banned until {past_ms}", default_seconds=120.0
        )
        assert until >= before + 119.0  # geçmiş zaman değil, default kullanıldı


class TestRequestShortCircuit:
    async def test_signed_request_blocked_without_network(self):
        client = _bare_client()
        ImprovedBinanceClient._rest_blocked_until = time.time() + 60
        with pytest.raises(BinanceAPIError) as exc_info:
            await client._request_with_retry("GET", "/fapi/v2/account", signed=True)
        assert exc_info.value.status_code == 418
        assert exc_info.value.code == -1003
        # Ağa hiç çıkılmadı: httpx istemcisi kurulmamış bare objede bile
        # exception network hatası değil kesici hatası (kanıt: 418/-1003).

    async def test_api_key_only_request_blocked_without_network(self):
        client = _bare_client()
        ImprovedBinanceClient._rest_blocked_until = time.time() + 60
        with pytest.raises(BinanceAPIError) as exc_info:
            await client._request_api_key_only("POST", "/fapi/v1/listenKey")
        assert exc_info.value.status_code == 418

    async def test_expired_block_allows_flow_past_breaker(self):
        client = _bare_client()
        ImprovedBinanceClient._rest_blocked_until = time.time() - 1
        # Kesici geçmişte — akış kesiciyi GEÇMELİ; bare objede sonraki adım
        # (rate limiter/httpx) farklı bir hata verir ama 418 kesici hatası OLMAZ.
        client.max_retries = 1
        client.retry_delay = 0
        with pytest.raises(Exception) as exc_info:
            await client._request_with_retry("GET", "/fapi/v1/time")
        err = exc_info.value
        assert not (
            isinstance(err, BinanceAPIError)
            and "devre kesici" in str(getattr(err, "msg", ""))
        )

    def test_breaker_is_process_wide_across_instances(self):
        a = _bare_client()
        ImprovedBinanceClient._trip_breaker("banned until 99999999999999", 60.0)
        b = _bare_client()
        with pytest.raises(BinanceAPIError):
            b._ensure_rest_allowed("/fapi/v1/klines")
        with pytest.raises(BinanceAPIError):
            a._ensure_rest_allowed("/fapi/v1/klines")
