"""Ana bot → takipçi köprüsü (D20) — `src/services/follower_forwarder.py`.

Sözleşme: TV alarm URL'leri DEĞİŞMEZ; ana bot (scalper halkası) AlgoPro
kaynaklı olayları ayrı bir secret ile takipçi halkasına İLETİR. Köprü
fire-and-forget'tir ve ana motoru ASLA etkilemez.

Ağa çıkılmaz: `httpx.AsyncClient` sahte bir sınıfla değiştirilir.
"""

from __future__ import annotations

import asyncio

import pytest

import src.services.follower_forwarder as fwd


class _FakeResponse:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


class _FakeClient:
    """httpx.AsyncClient yerine geçen kayıt tutucu (GERÇEK AĞ YOK)."""

    calls: list = []
    response = _FakeResponse()
    raise_exc: Exception | None = None
    delay: float = 0.0

    def __init__(self, *args, **kwargs):
        self.init_kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, content=None, headers=None):
        if type(self).delay:
            await asyncio.sleep(type(self).delay)
        type(self).calls.append(
            {
                "url": url,
                "body": content.decode("utf-8") if content else "",
                "headers": dict(headers or {}),
                "timeout": self.init_kwargs.get("timeout"),
            }
        )
        if type(self).raise_exc is not None:
            raise type(self).raise_exc
        return type(self).response


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    _FakeClient.calls = []
    _FakeClient.response = _FakeResponse()
    _FakeClient.raise_exc = None
    _FakeClient.delay = 0.0
    monkeypatch.setattr(fwd.httpx, "AsyncClient", _FakeClient)
    yield
    _FakeClient.calls = []


def _configure(monkeypatch, url="http://127.0.0.1:9093/follower/event", secret="s3cr3t"):
    monkeypatch.setattr(fwd.settings, "follower_forward_url", url, raising=False)
    monkeypatch.setattr(fwd.settings, "follower_forward_secret", secret, raising=False)
    monkeypatch.setattr(
        fwd.settings, "follower_forward_timeout_seconds", 2.0, raising=False
    )


BODY = "🔴 SELL | BINANCE:BTCUSDT | TF: 1 | Price: 77126.08 | SL: 77167.77"


class TestSourceFiltering:
    @pytest.mark.parametrize("source", ["luxosc", "luxso", "botv3", "tv", "", "news"])
    async def test_non_algopro_sources_are_not_forwarded(self, monkeypatch, source):
        _configure(monkeypatch)
        assert fwd.maybe_forward_algopro_event(BODY, source) is None
        assert _FakeClient.calls == []

    async def test_algopro_is_forwarded(self, monkeypatch):
        _configure(monkeypatch)
        task = fwd.maybe_forward_algopro_event(BODY, "algopro")
        assert task is not None
        await task
        assert len(_FakeClient.calls) == 1
        call = _FakeClient.calls[0]
        assert call["url"] == "http://127.0.0.1:9093/follower/event"
        assert call["body"] == BODY
        assert call["headers"][fwd.SECRET_HEADER] == "s3cr3t"
        assert call["timeout"] == 2.0

    async def test_case_insensitive_source(self, monkeypatch):
        _configure(monkeypatch)
        task = fwd.maybe_forward_algopro_event(BODY, "AlgoPro")
        assert task is not None
        await task
        assert len(_FakeClient.calls) == 1


class TestDisabledConfiguration:
    async def test_no_url_means_disabled(self, monkeypatch):
        _configure(monkeypatch, url="")
        assert fwd.maybe_forward_algopro_event(BODY, "algopro") is None
        assert _FakeClient.calls == []

    async def test_no_secret_means_disabled(self, monkeypatch):
        _configure(monkeypatch, secret="")
        assert fwd.maybe_forward_algopro_event(BODY, "algopro") is None
        assert _FakeClient.calls == []

    async def test_forward_enabled_helper(self, monkeypatch):
        _configure(monkeypatch)
        assert fwd.forward_enabled() is True
        _configure(monkeypatch, secret="   ")
        assert fwd.forward_enabled() is False


class TestFailureIsolation:
    async def test_transport_error_is_swallowed(self, monkeypatch):
        _configure(monkeypatch)
        _FakeClient.raise_exc = RuntimeError("bağlantı reddedildi")
        task = fwd.maybe_forward_algopro_event(BODY, "algopro")
        await task  # istisna yükselmez
        assert task.exception() is None

    async def test_timeout_does_not_block_caller(self, monkeypatch):
        """Çağıran fonksiyon POST'u BEKLEMEZ (fire-and-forget)."""
        _configure(monkeypatch)
        _FakeClient.delay = 0.2
        loop = asyncio.get_running_loop()
        started = loop.time()
        task = fwd.maybe_forward_algopro_event(BODY, "algopro")
        elapsed = loop.time() - started
        assert elapsed < 0.05  # anında döndü
        await task
        assert len(_FakeClient.calls) == 1

    async def test_http_error_status_logged_not_raised(self, monkeypatch):
        _configure(monkeypatch)
        _FakeClient.response = _FakeResponse(status_code=403, text="Geçersiz secret")
        task = fwd.maybe_forward_algopro_event(BODY, "algopro")
        await task
        assert task.exception() is None

    async def test_oversized_body_not_forwarded(self, monkeypatch):
        _configure(monkeypatch)
        assert fwd.maybe_forward_algopro_event("x" * 5000, "algopro") is None
        assert _FakeClient.calls == []

    async def test_empty_body_not_forwarded(self, monkeypatch):
        _configure(monkeypatch)
        assert fwd.maybe_forward_algopro_event("   ", "algopro") is None

    def test_no_event_loop_returns_none(self, monkeypatch):
        """Senkron bağlamda (loop yok) çağrı sessizce None döner, patlamaz."""
        _configure(monkeypatch)
        assert fwd.maybe_forward_algopro_event(BODY, "algopro") is None


class TestSecretIsNeverLogged:
    async def test_secret_absent_from_logs(self, monkeypatch):
        from src.core.logger import app_logger

        _configure(monkeypatch, secret="TOP-SECRET-VALUE")
        _FakeClient.raise_exc = RuntimeError("bağlantı reddedildi")
        records: list = []
        sink_id = app_logger.add(lambda message: records.append(str(message)))
        try:
            task = fwd.maybe_forward_algopro_event(BODY, "algopro")
            await task
        finally:
            app_logger.remove(sink_id)
        assert records, "en az bir uyarı loglanmalı"
        assert not any("TOP-SECRET-VALUE" in line for line in records)

    async def test_url_carries_no_secret(self, monkeypatch):
        _configure(monkeypatch)
        task = fwd.maybe_forward_algopro_event(BODY, "algopro")
        await task
        assert "secret" not in _FakeClient.calls[0]["url"].lower()
