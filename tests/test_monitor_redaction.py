"""Synthetic monitor credentials only; never starts the bot or calls a venue."""

from copy import deepcopy
import json
from types import SimpleNamespace
from urllib.parse import quote, quote_plus

import httpx
from pydantic import SecretStr
import pytest

from src.core.monitor_redaction import (
    CREDENTIAL_FIELDS,
    REDACTED,
    redact_monitor_payload,
)


TOKEN = "123456789:Synthetic_token_for_monitor_tests_only"
KEY = "synthetic-key:/?+& =monitor-test-only"


@pytest.mark.parametrize("field", CREDENTIAL_FIELDS)
def test_every_configured_credential_is_removed(field):
    cfg = SimpleNamespace(**{field: KEY})
    original = {"error": f"Provider rejected `{KEY}`", "quantity": 1.25}
    result = redact_monitor_payload(original, cfg)
    assert result == {"error": f"Provider rejected `{REDACTED}`", "quantity": 1.25}
    assert original["error"] == f"Provider rejected `{KEY}`"


@pytest.mark.parametrize("encode", [
    lambda text: text,
    lambda text: quote(text, safe=""),
    lambda text: quote_plus(text, safe=""),
    lambda text: quote(quote(text, safe=""), safe=""),
    lambda text: quote_plus(quote_plus(text, safe=""), safe=""),
    lambda text: quote(text, safe="").replace("%3A", "%3a").replace("%2F", "%2f").replace("%3F", "%3f").replace("%2B", "%2b"),
    lambda text: quote(quote(text, safe="").replace("%3A", "%3a"), safe=""),
])
def test_raw_and_url_encoded_values_are_removed(encode):
    encoded = encode(KEY)
    value = f"Request failed at https://provider.invalid/request?key={encoded}&mode=test"
    result = redact_monitor_payload(value, SimpleNamespace(gemini_api_key=SecretStr(KEY)))
    assert encoded not in result
    assert result == f"Request failed at https://provider.invalid/request?key={REDACTED}&mode=test"


def test_recursive_redaction_preserves_types_values_and_original_snapshot():
    snapshot = {
        "healthy": False,
        "count": 2,
        "pnl": -28.45,
        "missing": None,
        "positions": [{"message": TOKEN, "quantity": 0}],
        "nested": ({"url": f"https://api.telegram.org/bot{TOKEN}/getMe"}, True),
        f"error-{TOKEN}": "details",
    }
    before = deepcopy(snapshot)
    result = redact_monitor_payload(snapshot, SimpleNamespace(telegram_bot_token=TOKEN))
    assert snapshot == before
    assert result["healthy"] is False
    assert type(result["count"]) is int
    assert type(result["pnl"]) is float
    assert result["pnl"] == -28.45
    assert result["missing"] is None
    assert result["positions"] == [{"message": REDACTED, "quantity": 0}]
    assert isinstance(result["nested"], tuple)
    assert TOKEN not in json.dumps(result)
    assert result is not snapshot and result["positions"] is not snapshot["positions"]


@pytest.mark.parametrize("message", [
    "InvalidToken: The token `123456789:Rotated_token_not_in_current_settings` was rejected by the server.",
    "https://api.telegram.org/bot123456789:Rotated_token_not_in_current_settings/getMe",
    "https://provider.invalid/path?api_key=old-credential&mode=test",
    "https://provider.invalid/path?key=old-credential&mode=test",
    "https://provider.invalid/path?signature=old-credential&mode=test",
    "secret='old-credential'",
    "Authorization: Bearer old-credential",
    "Authorization: Basic old-credential",
])
def test_recognizable_rotated_credentials_are_removed(message):
    result = redact_monitor_payload(message, SimpleNamespace())
    assert "Rotated_token_not_in_current_settings" not in result
    assert "old-credential" not in result
    assert REDACTED in result


def test_empty_secrets_do_not_damage_strings_and_redaction_is_idempotent():
    value = {"status": "healthy", "hint": "risk_ready=true; no token configured", "error": f"token={TOKEN}"}
    cfg = SimpleNamespace(api_key="", jwt_secret=None, telegram_bot_token=SecretStr(TOKEN))
    result = redact_monitor_payload(value, cfg)
    assert result["status"] == "healthy"
    assert result["hint"] == value["hint"]
    assert redact_monitor_payload(result, cfg) == result


@pytest.mark.parametrize("scheme", ["https", "postgresql+asyncpg", "redis", "amqp"])
def test_url_userinfo_is_removed_without_exposing_url_password(scheme):
    value = f"Connection failed: {scheme}://example-user:unlisted-password@db.invalid:5432/database"
    result = redact_monitor_payload(value, SimpleNamespace())
    assert result == f"Connection failed: {scheme}://{REDACTED}@db.invalid:5432/database"


def test_ordinary_basic_and_bearer_text_is_unchanged():
    value = {"message": "basic telemetry and bearer status", "state": "not present"}
    assert redact_monitor_payload(value, SimpleNamespace()) == value


def test_quoted_authorization_error_is_redacted():
    value = "headers={'Authorization': 'Bearer stale-key'}"
    assert redact_monitor_payload(value, SimpleNamespace()) == f"headers={{'Authorization': 'Bearer {REDACTED}'}}"


@pytest.mark.parametrize("value,expected", [
    ('{"api_key": "rotated-secret"}', '{"api_key": "[REDACTED]"}'),
    ("headers={'X-MBX-APIKEY': 'rotated-secret'}", "headers={'X-MBX-APIKEY': '[REDACTED]'}"),
    ('{"API-KEY": "rotated-secret"}', '{"API-KEY": "[REDACTED]"}'),
    ("{'secret': 'rotated-secret'}", "{'secret': '[REDACTED]'}"),
])
def test_quoted_credential_names_in_raw_diagnostics_are_redacted(value, expected):
    assert redact_monitor_payload(value, SimpleNamespace()) == expected


@pytest.mark.parametrize("field", ["api_key", "X-MBX-APIKEY", "secret", "Authorization"])
def test_structured_credential_string_fields_are_redacted(field):
    payload = {"diagnostic": {field: "rotated-secret"}, "quantity": 1.5}
    assert redact_monitor_payload(payload, SimpleNamespace()) == {
        "diagnostic": {field: REDACTED}, "quantity": 1.5,
    }
    assert payload["diagnostic"][field] == "rotated-secret"


@pytest.mark.parametrize("value", [None, 0, 1.5, True, False])
def test_structured_credential_name_does_not_change_non_string_telemetry(value):
    payload = {"token": value, "other": "ordinary status"}
    result = redact_monitor_payload(payload, SimpleNamespace())
    assert result == payload
    assert type(result["token"]) is type(value)


def test_overlapping_secrets_do_not_leave_a_suffix():
    cfg = SimpleNamespace(api_key="synthetic-prefix", openai_api_key="synthetic-prefix-with-suffix")
    assert redact_monitor_payload("synthetic-prefix-with-suffix", cfg) == REDACTED


@pytest.fixture
def monitor_main(monkeypatch):
    import src.main as main

    # Never use current on-disk credential values in a test response/assertion.
    cfg = SimpleNamespace(
        telegram_bot_token=TOKEN,
        openai_api_key=KEY,
        api_key="synthetic-control-key",
        is_follower_mode=False,
        is_testnet=True,
        scalper_enabled=True,
        follower_embedded=False,
    )
    monkeypatch.setattr(main, "settings", cfg)
    main._reset_status_caches()
    yield main
    main._reset_status_caches()


@pytest.mark.asyncio
@pytest.mark.parametrize("core_healthy,status", [(True, 200), (False, 503)])
async def test_actual_health_response_redacts_invalid_token(monitor_main, monkeypatch, core_healthy, status):
    main = monitor_main
    monkeypatch.setattr(main, "_tracked_position_counts", lambda: {"scalper": 0, "follower": 0, "total": 0})
    monkeypatch.setattr(main, "orchestrator", SimpleNamespace(
        health_snapshot=lambda: {"healthy": core_healthy, "monitoring_task_alive": core_healthy},
    ))
    monkeypatch.setattr(main, "scalper_engine", SimpleNamespace(
        running=True, health_snapshot=lambda: {"healthy": core_healthy},
    ))
    monkeypatch.setattr(main, "telegram_bot", SimpleNamespace(health_snapshot=lambda: {
        "healthy": False,
        "last_error": f"InvalidToken: The token `{TOKEN}` was rejected by the server.",
    }))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == status
    assert TOKEN not in response.text
    assert response.json()["core_healthy"] is core_healthy
    assert "InvalidToken" in response.json()["telegram_details"]["last_error"]
    assert REDACTED in response.json()["telegram_details"]["last_error"]


@pytest.mark.asyncio
async def test_actual_scalper_status_redacts_copy_not_cached_state(monitor_main, monkeypatch):
    main = monitor_main
    snapshot = {
        "running": True,
        "daily_pnl": -28.45,
        "tracked": [],
        "ai_gate": {"last_error": f"provider rejected {KEY}"},
    }
    monkeypatch.setattr(main, "scalper_engine", SimpleNamespace(snapshot=lambda: snapshot))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        response = await client.get("/scalper/status")
        cached = await client.get("/scalper/status")
    assert response.status_code == cached.status_code == 200
    assert response.json() == cached.json()
    assert response.json()["daily_pnl"] == -28.45
    assert KEY not in response.text
    assert KEY in snapshot["ai_gate"]["last_error"]
    assert main._scalper_status_cache[""]["payload"] is snapshot


def test_every_monitor_json_route_uses_redaction_and_control_routes_do_not(monitor_main):
    from fastapi.routing import APIRoute

    expected = {
        "/health", "/api/status", "/positions", "/config", "/waiting-mode/active",
        "/scalper/status", "/scalper/stats", "/scalper/trades",
        "/scalper/forensics/summary", "/scalper/forensics/recent",
        "/scalper/trades/{trade_id}/forensics", "/follower/status",
    }
    routes = [route for route in monitor_main.app.routes if isinstance(route, APIRoute)]
    actual = {route.path for route in routes if route.response_class is monitor_main.MonitorJSONResponse}
    assert actual == expected
    assert all(route.methods == {"GET"} for route in routes if route.path in expected)
    assert all(route.response_class is not monitor_main.MonitorJSONResponse for route in routes if "POST" in route.methods)


@pytest.mark.asyncio
async def test_redaction_does_not_open_unknown_routes_or_change_control_auth(monitor_main):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=monitor_main.app), base_url="http://test") as client:
        unknown = await client.get("/guest-admin")
        control = await client.post("/signal", json={"message": "synthetic"})
        wrong_method = await client.post("/health")
    assert unknown.status_code == 404
    assert control.status_code == 401
    assert wrong_method.status_code == 405
