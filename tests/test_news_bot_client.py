"""examples/news_bot_client.py için birim testleri — gerçek Binance/ağ YOK.

Gerçek TRADINGBOT sunucusunu ayağa kaldırmadan istemcinin HTTP davranışını
doğrulamak için `http.server` ile yerel, sahte bir HTTP sunucusu kullanılır
(ayrı thread'de). Kapsanan sözleşmeler (bkz. docs/INTEGRATIONS.md §1/§3 ve
`examples/news_bot_client.py` docstring'i):

  - /tv-signal: secret QUERY string'de (?secret=...), gövdede DEĞİL.
  - /risk-event: secret gövdede ({"secret": ...}), query string'de DEĞİL.
  - 4xx asla retry edilmez (yanlış secret bir sonraki denemede düzelmez).
  - 5xx exponential backoff ile retry edilir (max_retries kez).
  - dry_run=True hiçbir ağ çağrısı yapmaz.
  - Secret hiçbir çıktıda (dry-run print'i, CLI stdout/stderr) asla görünmez.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
if str(EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_DIR))

from news_bot_client import (  # noqa: E402
    TradingBotClient,
    TradingBotClientError,
)
from news_bot_client import main as cli_main  # noqa: E402


# ---------------------------------------------------------------------------
# Sahte HTTP sunucu — her istek `requests` listesine kaydedilir; yanıt
# per-test verilen `responder(record, attempt_no) -> (status, json_payload)`
# ile belirlenir.
# ---------------------------------------------------------------------------


class _CapturingHandler(BaseHTTPRequestHandler):
    def _handle(self) -> None:
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw_body = self.rfile.read(length) if length else b""
        parsed = urlparse(self.path)
        try:
            body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except ValueError:
            body = {}
        record = {
            "path": parsed.path,
            "query": parse_qs(parsed.query),
            "raw_query": parsed.query,
            "body": body,
        }
        self.server.requests.append(record)  # type: ignore[attr-defined]
        status, payload = self.server.responder(record, len(self.server.requests))  # type: ignore[attr-defined]
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:  # noqa: N802 (http.server naming convention)
        self._handle()

    def log_message(self, format: str, *args) -> None:  # silence stderr access log
        pass


class _FastBindHTTPServer(HTTPServer):
    """HTTPServer.server_bind() calls socket.getfqdn(), which can block for
    tens of seconds on machines with a slow/misconfigured DNS resolver
    (observed on macOS sandboxes) even though nothing in these tests needs a
    reverse-DNS name. Skip it — use the bound IP as-is."""

    def server_bind(self) -> None:  # noqa: D102 - see class docstring
        import socketserver

        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


class FakeServer:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.responder = lambda record, n: (200, {"ok": True})
        self._httpd = _FastBindHTTPServer(("127.0.0.1", 0), _CapturingHandler)
        self._httpd.requests = self.requests  # type: ignore[attr-defined]
        self._httpd.responder = lambda record, n: self.responder(record, n)  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        _, port = self._httpd.server_address
        return f"http://127.0.0.1:{port}"

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=2)


@pytest.fixture
def fake_server():
    server = FakeServer()
    try:
        yield server
    finally:
        server.close()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    # CLI varsayılanları env'den okunuyor — testler arasında sızıntı olmasın.
    for var in (
        "TRADINGBOT_BASE_URL",
        "TV_WEBHOOK_SECRET",
        "RISK_EVENT_SECRET",
        "NEWS_BOT_SOURCE",
    ):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# /tv-signal — secret query'de, gövde yalnız symbol/side taşır
# ---------------------------------------------------------------------------


def test_signal_sends_secret_in_query_not_body(fake_server):
    fake_server.responder = lambda record, n: (
        200,
        {
            "symbol": record["body"]["symbol"],
            "direction": "short",
            "accepted": True,
            "confluence": {"votes": 1, "triggered": True},
            "source": "news_macro",
        },
    )
    client = TradingBotClient(fake_server.base_url, tv_secret="s3cr3t-tv", source="news_macro")

    result = client.signal("BTCUSDT", "sell")

    assert result["accepted"] is True
    assert len(fake_server.requests) == 1
    req = fake_server.requests[0]
    assert req["path"] == "/tv-signal"
    assert req["query"]["secret"] == ["s3cr3t-tv"]
    assert req["query"]["src"] == ["news_macro"]
    assert req["body"] == {"symbol": "BTCUSDT", "side": "sell"}
    assert "secret" not in req["body"]


# ---------------------------------------------------------------------------
# /risk-event — secret gövdede, query string boş
# ---------------------------------------------------------------------------


def test_halt_sends_secret_in_body_not_query(fake_server):
    fake_server.responder = lambda record, n: (
        200,
        {"ok": True, "action": record["body"]["action"], "halted_until": 12345, "flattened": [], "errors": []},
    )
    client = TradingBotClient(
        fake_server.base_url, tv_secret="tv-secret", risk_secret="r1sk-secret", source="news_macro"
    )

    result = client.halt("savaş çıktı, borsa durdu", ttl_minutes=15)

    assert result["ok"] is True
    req = fake_server.requests[0]
    assert req["path"] == "/risk-event"
    assert req["raw_query"] == ""  # secret KESİNLİKLE query string'e sızmamalı
    assert req["body"]["secret"] == "r1sk-secret"
    assert req["body"]["action"] == "halt"
    assert req["body"]["reason"] == "savaş çıktı, borsa durdu"
    assert req["body"]["ttl_minutes"] == 15
    assert req["body"]["source"] == "news_macro"


def test_flatten_requires_reason_field_present(fake_server):
    fake_server.responder = lambda record, n: (200, {"ok": True, "action": "flatten", "flattened": [], "errors": []})
    client = TradingBotClient(fake_server.base_url, tv_secret="tv", risk_secret="rk")

    client.flatten("exchange hacked", ttl_minutes=240)

    req = fake_server.requests[0]
    assert req["body"]["action"] == "flatten"
    assert req["body"]["reason"] == "exchange hacked"
    assert req["body"]["ttl_minutes"] == 240


def test_resume_and_status_omit_reason_and_ttl(fake_server):
    fake_server.responder = lambda record, n: (200, {"ok": True, "action": record["body"]["action"], "flattened": [], "errors": []})
    client = TradingBotClient(fake_server.base_url, tv_secret="tv", risk_secret="rk")

    client.resume()
    resume_req = fake_server.requests[-1]
    assert resume_req["body"]["action"] == "resume"
    assert "reason" not in resume_req["body"]
    assert "ttl_minutes" not in resume_req["body"]

    client.status()
    status_req = fake_server.requests[-1]
    assert status_req["body"]["action"] == "status"
    assert "reason" not in status_req["body"]


def test_risk_methods_without_risk_secret_raise_value_error(fake_server):
    client = TradingBotClient(fake_server.base_url, tv_secret="tv")  # risk_secret=None

    with pytest.raises(ValueError):
        client.halt("test")

    assert fake_server.requests == []  # hiç ağa çıkmadan reddedilmeli


# ---------------------------------------------------------------------------
# Retry politikası
# ---------------------------------------------------------------------------


def test_4xx_is_never_retried(fake_server):
    fake_server.responder = lambda record, n: (403, {"detail": "Geçersiz webhook secret"})
    client = TradingBotClient(fake_server.base_url, tv_secret="wrong", max_retries=2, backoff_base=0.01)

    with pytest.raises(TradingBotClientError) as excinfo:
        client.signal("BTCUSDT", "buy")

    assert excinfo.value.status == 403
    assert len(fake_server.requests) == 1


def test_5xx_is_retried_and_can_succeed(fake_server):
    def responder(record, n):
        if n < 3:
            return 500, {"detail": "boom"}
        return 200, {"accepted": True, "confluence": {"votes": 1}}

    fake_server.responder = responder
    client = TradingBotClient(fake_server.base_url, tv_secret="tv", max_retries=2, backoff_base=0.01)

    result = client.signal("ETHUSDT", "buy")

    assert result["accepted"] is True
    assert len(fake_server.requests) == 3  # 1 ilk deneme + 2 retry


def test_5xx_exhausts_retries_then_raises(fake_server):
    fake_server.responder = lambda record, n: (500, {"detail": "boom"})
    client = TradingBotClient(fake_server.base_url, tv_secret="tv", max_retries=2, backoff_base=0.01)

    with pytest.raises(TradingBotClientError) as excinfo:
        client.signal("ETHUSDT", "buy")

    assert excinfo.value.status == 500
    assert len(fake_server.requests) == 3  # 1 ilk deneme + 2 retry, sonra pes


def test_connection_error_is_retried_then_raises():
    # Hiçbir şeyin dinlemediği bir port — bağlantı hemen reddedilir.
    client = TradingBotClient(
        "http://127.0.0.1:1", tv_secret="tv", max_retries=1, backoff_base=0.01, timeout=1
    )

    with pytest.raises(ConnectionError):
        client.signal("BTCUSDT", "buy")


# ---------------------------------------------------------------------------
# dry_run — hiç ağ çağrısı yok, secret hiçbir çıktıda görünmez
# ---------------------------------------------------------------------------


def test_dry_run_makes_no_network_call_and_redacts_secret(fake_server):
    client = TradingBotClient(
        fake_server.base_url,
        tv_secret="super-secret-tv-value",
        risk_secret="super-secret-risk-value",
        dry_run=True,
    )

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        signal_result = client.signal("BTCUSDT", "buy")
        halt_result = client.halt("test", ttl_minutes=5)

    assert fake_server.requests == []
    assert signal_result == {"dry_run": True, "path": "/tv-signal"}
    assert halt_result == {"dry_run": True, "path": "/risk-event"}

    output = buf.getvalue()
    assert "super-secret-tv-value" not in output
    assert "super-secret-risk-value" not in output
    assert "***" in output  # redaksiyon işareti gerçekten yazılmış olmalı


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_dry_run_signal_prints_no_secret_and_makes_no_request(fake_server, capsys):
    exit_code = cli_main(
        [
            "--base",
            fake_server.base_url,
            "--tv-secret",
            "cli-secret-value",
            "--source",
            "news_macro",
            "--dry-run",
            "signal",
            "BTCUSDT",
            "sell",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "cli-secret-value" not in captured.out
    assert "cli-secret-value" not in captured.err
    assert fake_server.requests == []


def test_cli_signal_happy_path_hits_fake_server(fake_server, capsys):
    fake_server.responder = lambda record, n: (
        200,
        {"symbol": record["body"]["symbol"], "accepted": True, "confluence": {"votes": 1}},
    )

    exit_code = cli_main(
        [
            "--base",
            fake_server.base_url,
            "--tv-secret",
            "tv-secret",
            "signal",
            "BTCUSDT",
            "sell",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["accepted"] is True
    assert "tv-secret" not in captured.out


def test_cli_halt_without_risk_secret_errors_without_network(fake_server, capsys):
    exit_code = cli_main(
        [
            "--base",
            fake_server.base_url,
            "--tv-secret",
            "tv-secret",
            "halt",
            "test event",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "risk_secret" in captured.err
    assert fake_server.requests == []


def test_cli_4xx_error_reported_without_secret_leak(fake_server, capsys):
    fake_server.responder = lambda record, n: (403, {"detail": "Geçersiz webhook secret"})

    exit_code = cli_main(
        [
            "--base",
            fake_server.base_url,
            "--tv-secret",
            "wrong-secret-value",
            "signal",
            "BTCUSDT",
            "buy",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "403" in captured.err
    assert "wrong-secret-value" not in captured.err
    assert "wrong-secret-value" not in captured.out
