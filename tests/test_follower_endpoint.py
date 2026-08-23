"""`POST /follower/event` + `/tv-signal` köprü çağrı yeri (D20).

Sözleşme (docs/INTEGRATIONS.md §7): 403 = secret yanlış · 422 = gövde
çözülemedi/çok büyük · 503 = kanal kapalı ya da motor hazır değil — /risk-event
ile AYNI semantik.

KRİTİK: köprü, `/tv-signal`'ın 422'sinden ÖNCE çalışır (AlgoPro'nun EXIT/TP
HIT/SL HIT mesajları yön kelimesi taşımaz ve ana botta 422 alır) ama 403'te
(secret yanlış) HİÇBİR ŞEY iletilmez.

Test deseni tests/test_tv_signal_bridge.py ve tests/test_risk_event.py ile
aynı: endpoint fonksiyonu doğrudan sahte bir `Request` ile çağrılır.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

import src.main as main_module

SECRET = "f0ll0w3r-s3cr3t"
TV_SECRET = "tv-s3cr3t"

REAL_SELL = (
    "🔴 SELL | BINANCE:BTCUSDT | TF: 1 | Price: 77126.08 | TQI: .45 | Score: 8 "
    "| SL: 77167.77 | TP1: 77105.23 | TP2: 77084.39 | TP3: 77063.54 | TP: fixed ×1.00"
)
REAL_SL_HIT = "🛑 SL HIT | BINANCE:BTCUSDT | TF: 1 | Price: 77167.77"


class _FakeRequest:
    def __init__(self, body: bytes, query: dict | None = None, headers: dict | None = None):
        self._body = body
        self.query_params = query or {}
        self.headers = headers or {}

    async def body(self) -> bytes:
        return self._body


@pytest.fixture
def follower_ready(monkeypatch):
    monkeypatch.setattr(main_module.settings, "follower_forward_secret", SECRET)
    engine = MagicMock()
    engine.handle_event = AsyncMock(
        return_value={"accepted": True, "reason": "pozisyon açıldı"}
    )
    engine.snapshot = MagicMock(return_value={"mode": "follower", "running": True})
    monkeypatch.setattr(main_module, "follower_engine", engine)
    return engine


class TestFollowerEventAuth:
    async def test_disabled_channel_returns_503(self, monkeypatch):
        monkeypatch.setattr(main_module.settings, "follower_forward_secret", "")
        with pytest.raises(HTTPException) as exc:
            await main_module.follower_event(_FakeRequest(REAL_SELL.encode()))
        assert exc.value.status_code == 503

    async def test_wrong_secret_403(self, follower_ready):
        request = _FakeRequest(
            REAL_SELL.encode(), headers={"X-Follower-Secret": "yanlis"}
        )
        with pytest.raises(HTTPException) as exc:
            await main_module.follower_event(request)
        assert exc.value.status_code == 403
        follower_ready.handle_event.assert_not_called()

    async def test_missing_secret_403(self, follower_ready):
        with pytest.raises(HTTPException) as exc:
            await main_module.follower_event(_FakeRequest(REAL_SELL.encode()))
        assert exc.value.status_code == 403

    async def test_header_secret_accepted(self, follower_ready):
        request = _FakeRequest(
            REAL_SELL.encode(), headers={"X-Follower-Secret": SECRET}
        )
        result = await main_module.follower_event(request)
        assert result["ok"] is True
        assert result["kind"] == "entry"
        assert result["symbol"] == "BTCUSDT"
        follower_ready.handle_event.assert_awaited_once()

    async def test_query_secret_is_rejected(self, follower_ready):
        """`?secret=` BİLİNÇLİ olarak desteklenmez.

        uvicorn erişim logu (logs/supervisor.log) query string'i düz metin
        yazar ve rotasyonla yedeklere yayılır — secret asla oraya düşmemeli
        (CLAUDE.md kural 5). Köprü zaten `X-Follower-Secret` başlığını kullanır.
        """
        request = _FakeRequest(REAL_SELL.encode(), query={"secret": SECRET})
        with pytest.raises(HTTPException) as exc:
            await main_module.follower_event(request)
        assert exc.value.status_code == 403

    async def test_body_secret_accepted_for_template_form(self, follower_ready):
        body = f"secret={SECRET} kind=exit BTCUSDT tf=1 px=100"
        result = await main_module.follower_event(_FakeRequest(body.encode()))
        assert result["kind"] == "exit"


class TestFollowerEventValidation:
    async def test_oversized_body_422(self, follower_ready):
        request = _FakeRequest(
            b"x" * 5000, headers={"X-Follower-Secret": SECRET}
        )
        with pytest.raises(HTTPException) as exc:
            await main_module.follower_event(request)
        assert exc.value.status_code == 422

    async def test_empty_body_422(self, follower_ready):
        request = _FakeRequest(b"   ", headers={"X-Follower-Secret": SECRET})
        with pytest.raises(HTTPException) as exc:
            await main_module.follower_event(request)
        assert exc.value.status_code == 422

    async def test_unparseable_body_422(self, follower_ready):
        request = _FakeRequest(
            b"lorem ipsum", headers={"X-Follower-Secret": SECRET}
        )
        with pytest.raises(HTTPException) as exc:
            await main_module.follower_event(request)
        assert exc.value.status_code == 422
        follower_ready.handle_event.assert_not_called()

    async def test_engine_not_ready_503(self, monkeypatch):
        monkeypatch.setattr(main_module.settings, "follower_forward_secret", SECRET)
        monkeypatch.setattr(main_module, "follower_engine", None)
        request = _FakeRequest(
            REAL_SELL.encode(), headers={"X-Follower-Secret": SECRET}
        )
        with pytest.raises(HTTPException) as exc:
            await main_module.follower_event(request)
        assert exc.value.status_code == 503

    async def test_hit_event_is_dispatched(self, follower_ready):
        request = _FakeRequest(
            REAL_SL_HIT.encode(), headers={"X-Follower-Secret": SECRET}
        )
        result = await main_module.follower_event(request)
        assert result["kind"] == "sl"
        assert result["direction"] is None

    async def test_rejected_event_still_http_200(self, follower_ready):
        follower_ready.handle_event = AsyncMock(
            return_value={"accepted": False, "reason": "kapasite dolu"}
        )
        request = _FakeRequest(
            REAL_SELL.encode(), headers={"X-Follower-Secret": SECRET}
        )
        result = await main_module.follower_event(request)
        assert result["ok"] is True
        assert result["accepted"] is False
        assert result["reason"] == "kapasite dolu"


class TestFollowerStatus:
    async def test_status_without_engine_is_empty(self, monkeypatch):
        monkeypatch.setattr(main_module, "follower_engine", None)
        status = await main_module.follower_status()
        assert status["mode"] == "follower"
        assert status["running"] is False
        assert status["positions"] == []

    async def test_status_with_engine(self, follower_ready):
        status = await main_module.follower_status()
        assert status == {"mode": "follower", "running": True}


class TestTvSignalForwarding:
    """Köprü çağrı yeri — `/tv-signal` içindeki TEK satır."""

    @pytest.fixture
    def tv_ready(self, monkeypatch):
        monkeypatch.setattr(main_module.settings, "tv_webhook_secret", TV_SECRET)
        monkeypatch.setattr(main_module.settings, "tv_confluence_required", 1)
        engine = MagicMock()
        engine.external_signal = AsyncMock(return_value={"accepted": True})
        monkeypatch.setattr(main_module, "scalper_engine", engine)
        return engine

    @pytest.fixture
    def forwarded(self, monkeypatch):
        calls: list = []
        monkeypatch.setattr(
            main_module,
            "maybe_forward_algopro_event",
            lambda raw, source: calls.append((raw, source)),
        )
        return calls

    async def test_algopro_entry_is_forwarded(self, tv_ready, forwarded):
        body = f"{REAL_SELL} secret={TV_SECRET}"
        await main_module.tradingview_webhook(_FakeRequest(body.encode(), {}))
        assert len(forwarded) == 1
        assert forwarded[0][1] == "algopro"

    async def test_exit_event_forwarded_before_422(self, tv_ready, forwarded):
        """EXIT/TP/SL HIT mesajları ana botta 422 alır ama İLETİLİR."""
        body = f"{REAL_SL_HIT} secret={TV_SECRET}"
        with pytest.raises(HTTPException) as exc:
            await main_module.tradingview_webhook(_FakeRequest(body.encode(), {}))
        assert exc.value.status_code == 422
        assert len(forwarded) == 1
        assert forwarded[0][1] == "algopro"

    async def test_wrong_secret_403_is_never_forwarded(self, tv_ready, forwarded):
        body = f"{REAL_SL_HIT} secret=yanlis"
        with pytest.raises(HTTPException) as exc:
            await main_module.tradingview_webhook(_FakeRequest(body.encode(), {}))
        assert exc.value.status_code == 403
        assert forwarded == []

    async def test_non_algopro_source_forwarder_sees_it_but_skips(
        self, tv_ready, forwarded
    ):
        """Köprü çağrılır ama kaynak 'algopro' değilse iletim YAPMAZ.

        (Filtre `maybe_forward_algopro_event` içindedir — bkz.
        tests/test_follower_forwarder.py::TestSourceFiltering.)
        """
        body = json.dumps({"secret": TV_SECRET, "symbol": "BTCUSDT", "side": "buy"})
        await main_module.tradingview_webhook(
            _FakeRequest(body.encode(), {"src": "luxosc"})
        )
        assert len(forwarded) == 1
        assert forwarded[0][1] == "luxosc"

    async def test_forwarder_exception_does_not_break_webhook(
        self, tv_ready, monkeypatch
    ):
        def _boom(raw, source):
            raise RuntimeError("köprü çöktü")

        monkeypatch.setattr(main_module, "maybe_forward_algopro_event", _boom)
        body = json.dumps({"secret": TV_SECRET, "symbol": "BTCUSDT", "side": "buy"})
        result = await main_module.tradingview_webhook(
            _FakeRequest(body.encode(), {})
        )
        assert result["accepted"] is True
