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
    # Sayaçlar ve uyarı oran-sınırı MODÜL DÜZEYİNDEDİR; testler arasında
    # sızmasın (aksi halde bir testin tükettiği uyarı kotası diğerini düşürür).
    fwd.reset_forwarder_stats()
    yield
    _FakeClient.calls = []
    fwd.reset_forwarder_stats()


def _configure(monkeypatch, url="http://127.0.0.1:9093/follower/event", secret="s3cr3t"):
    monkeypatch.setattr(fwd.settings, "follower_forward_url", url, raising=False)
    monkeypatch.setattr(fwd.settings, "follower_forward_secret", secret, raising=False)
    monkeypatch.setattr(
        fwd.settings, "follower_forward_timeout_seconds", 20.0, raising=False
    )


# GERÇEK AlgoPro V1.6 gövdesi (TV Desktop sondası, 2026-08-23).
BODY = (
    "🔴 SELL | BINANCE:BTCUSDT | TF: 1 | Price: 77126.08 | TQI: .45 | Score: 8 "
    "| SL: 77167.77 | TP1: 77105.23 | TP2: 77084.39 | TP3: 77063.54"
)
SL_HIT_BODY = "🛑 SL HIT | BINANCE:BTCUSDT | TF: 1 | Price: 77167.77"

# Köprünün İLETMEMESİ gereken gövdeler (düşmanca inceleme bulgu 5). Bunların
# hepsi ESKİ parmak izinden ("| TF:" ya da "| Price:" geçiyor mu?) geçerdi.
NOT_ALGOPRO_BODIES = [
    "src=luxosc BTCUSDT long | TF: 1 | Price: 100",
    "BotV3 sinyal | TF: 15 | Price: 100 | BTCUSDT bullish",
    "🟢 BUY | BTCUSDT | TF: 1 | Price: 100 | SL: 99 | TP1: 101 | TP2: 102 | TP3: 103",
    "Bullish reversal on BINANCE:BTCUSDT",
    "🔴 SELL | BINANCE:BTCUSDT | TF: 1 | Price: 77126.08 | SL: 77167.77",
]


class TestBodyRecognizer:
    """Bulgu 5: iletim kararı GÖVDEYE bakar, `?src=`e DEĞİL."""

    @pytest.mark.parametrize("body", NOT_ALGOPRO_BODIES)
    @pytest.mark.parametrize("source", ["algopro", "luxosc", ""])
    async def test_non_algopro_bodies_are_never_forwarded(
        self, monkeypatch, body, source
    ):
        _configure(monkeypatch)
        assert fwd.maybe_forward_algopro_event(body, source) is None
        assert _FakeClient.calls == []

    @pytest.mark.parametrize("source", ["algopro", "luxosc", "tv", "", "news"])
    async def test_real_algopro_body_is_forwarded_regardless_of_src(
        self, monkeypatch, source
    ):
        """`?src=` yanlış/eksik olsa bile GERÇEK AlgoPro alarmı iletilir."""
        _configure(monkeypatch)
        task = fwd.maybe_forward_algopro_event(BODY, source)
        assert task is not None
        await task
        assert len(_FakeClient.calls) == 1

    async def test_forward_call_shape(self, monkeypatch):
        _configure(monkeypatch)
        task = fwd.maybe_forward_algopro_event(BODY, "algopro")
        assert task is not None
        await task
        call = _FakeClient.calls[0]
        assert call["url"] == "http://127.0.0.1:9093/follower/event"
        assert call["body"] == BODY
        assert call["headers"][fwd.SECRET_HEADER] == "s3cr3t"
        # Bağlantı/yazma KISA, okuma UZUN (bkz. modül başlığı).
        timeout = call["timeout"]
        assert timeout.connect == pytest.approx(fwd.CONNECT_TIMEOUT_SECONDS)
        assert timeout.read == pytest.approx(20.0)

    async def test_hit_events_are_forwarded(self, monkeypatch):
        """SL/TP HIT gövdeleri takipçi için KRİTİKTİR — iletilmeli."""
        _configure(monkeypatch)
        task = fwd.maybe_forward_algopro_event(SL_HIT_BODY, "")
        assert task is not None
        await task
        assert len(_FakeClient.calls) == 1

    async def test_skipped_bodies_are_counted(self, monkeypatch):
        _configure(monkeypatch)
        fwd.reset_forwarder_stats()
        fwd.maybe_forward_algopro_event(NOT_ALGOPRO_BODIES[0], "algopro")
        stats = fwd.forwarder_stats()
        assert stats["counters"]["skipped_not_algopro"] == 1
        assert stats["last_skipped"]["reason"] == "not_algopro"

    async def test_forwarded_bodies_are_counted(self, monkeypatch):
        _configure(monkeypatch)
        fwd.reset_forwarder_stats()
        task = fwd.maybe_forward_algopro_event(BODY, "algopro")
        await task
        counters = fwd.forwarder_stats()["counters"]
        assert counters["forwarded"] == 1
        assert counters["forwarded_entry"] == 1
        assert counters["delivered"] == 1

    async def test_stats_never_contain_the_secret(self, monkeypatch):
        _configure(monkeypatch, secret="TOP-SECRET-VALUE")
        fwd.reset_forwarder_stats()
        fwd.maybe_forward_algopro_event(
            f"secret=TOP-SECRET-VALUE {NOT_ALGOPRO_BODIES[0]}", "algopro"
        )
        assert "TOP-SECRET-VALUE" not in str(fwd.forwarder_stats())


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


class TestFailureWarningsAreRateLimited:
    """Ana oturum eki: başarısız iletim = sayaç + dakikada 1 WARNING."""

    async def test_repeated_failures_warn_once_per_minute(self, monkeypatch):
        from src.core.logger import app_logger

        _configure(monkeypatch)
        fwd.reset_forwarder_stats()
        _FakeClient.raise_exc = RuntimeError("bağlantı reddedildi")
        records: list = []
        sink_id = app_logger.add(lambda message: records.append(str(message)))
        try:
            for _ in range(5):
                await fwd.maybe_forward_algopro_event(BODY, "algopro")
        finally:
            app_logger.remove(sink_id)

        counters = fwd.forwarder_stats()["counters"]
        assert counters["transport_error"] == 5  # hiçbiri kaybolmaz
        assert counters["suppressed_warnings"] == 4
        warnings = [r for r in records if "iletemedi" in r]
        assert len(warnings) == 1

    async def test_rate_limit_is_per_failure_kind(self, monkeypatch):
        _configure(monkeypatch)
        fwd.reset_forwarder_stats()
        _FakeClient.response = _FakeResponse(status_code=503, text="kapalı")
        await fwd.maybe_forward_algopro_event(BODY, "algopro")
        _FakeClient.response = _FakeResponse()
        _FakeClient.raise_exc = RuntimeError("kopuk")
        await fwd.maybe_forward_algopro_event(BODY, "algopro")
        counters = fwd.forwarder_stats()["counters"]
        assert counters["http_error"] == 1
        assert counters["transport_error"] == 1
        assert "suppressed_warnings" not in counters
