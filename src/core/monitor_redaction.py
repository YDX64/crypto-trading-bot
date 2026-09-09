"""Remove credentials from the read-only monitor's JSON representation.

Only the outbound copy is changed: engine state, cached diagnostics and the
trading/control API do not pass through this module. Credential values are
never included in logs, reprs or error messages here.
"""

import re
from typing import Any
from urllib.parse import quote, quote_plus


REDACTED = "[REDACTED]"
CREDENTIAL_FIELDS = (
    "binance_api_key",
    "binance_api_secret",
    "telegram_bot_token",
    "openai_api_key",
    "gemini_api_key",
    "deepseek_api_key",
    "jwt_secret",
    "api_key",
    "tv_webhook_secret",
    "risk_event_secret",
    "follower_forward_secret",
)

# Historical provider exceptions may contain a rotated credential no longer
# present in Settings. Mask recognizable credential-bearing text as well.
_CREDENTIAL_VALUE = re.compile(
    r"(?i)((?:\b(?:x-mbx-apikey|api[_-]?key|secret|token|password|signature|listenkey)"
    r"|(?<=[?&])key)"
    r"[\"']?\s*[=:]\s*[\"']?)([^\s&\"'`,}<>]+)"
)
_CREDENTIAL_FIELD_NAME = re.compile(
    r"(?i)(?:x-mbx-apikey|api[_-]?key|secret|token|password|signature|listenkey|authorization)"
)
_AUTHORIZATION = re.compile(
    r"(?i)(\bauthorization[\"']?\s*[=:]\s*[\"']?\s*"
    r"(?:bearer|basic)\s+)([^\s\"'`,}<>]+)"
)
_TELEGRAM_TOKEN = re.compile(r"(?<![A-Za-z0-9_])(?:bot)?\d{6,}:[A-Za-z0-9_-]{20,}")
_URL_USERINFO = re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://)[^\s/@?#]+@")


def _credential_variants(cfg: Any) -> tuple[str, ...]:
    values = set()
    for field in CREDENTIAL_FIELDS:
        value = getattr(cfg, field, None)
        # Pydantic SecretStr must be unwrapped deliberately; str(SecretStr)
        # returns stars, which would both miss the key and mask useful text.
        unwrap = getattr(value, "get_secret_value", None)
        if callable(unwrap):
            value = unwrap()
        if not isinstance(value, str) or not value:
            continue
        variants = {value}
        # Exception URLs can contain one or two percent-encoding layers.
        for _ in range(2):
            variants |= {
                encode(item, safe="")
                for item in tuple(variants)
                for encode in (quote, quote_plus)
            }
        values.update(variants)
        values.update(
            re.sub(r"%[0-9A-F]{2}", lambda match: match[0].lower(), item)
            for item in variants
        )
    # Replace longer values first when one configured key is another's prefix.
    return tuple(sorted(values, key=len, reverse=True))


def _credential_pattern(value: str) -> re.Pattern:
    def percent_escape(match: re.Match) -> str:
        # Only percent-escape hex digits are case-insensitive. Credential
        # letters themselves remain case-sensitive. Also covers %253A.
        escape = match[0]
        return escape[:-2] + "".join(
            f"[{char.lower()}{char.upper()}]" if char.isalpha() else char
            for char in escape[-2:]
        )

    return re.compile(
        re.sub(r"%(?:25)*[0-9A-Fa-f]{2}", percent_escape, re.escape(value))
    )


def redact_monitor_payload(payload: Any, cfg: Any) -> Any:
    """Copy JSON containers and mask only strings, preserving numeric values.

    Deliberately not a generic serializer: non-JSON objects retain their type
    so FastAPI's existing serialization/validation contracts still apply.
    """
    patterns = tuple(_credential_pattern(value) for value in _credential_variants(cfg))

    def redact_text(value: str) -> str:
        for credential in patterns:
            value = credential.sub(lambda match: REDACTED, value)
        value = _TELEGRAM_TOKEN.sub(REDACTED, value)
        value = _URL_USERINFO.sub(lambda match: match[1] + REDACTED + "@", value)
        value = _CREDENTIAL_VALUE.sub(lambda match: match[1] + REDACTED, value)
        return _AUTHORIZATION.sub(lambda match: match[1] + REDACTED, value)

    def copy(value: Any) -> Any:
        if isinstance(value, str):
            return redact_text(value)
        if isinstance(value, dict):
            return {
                redact_text(key) if isinstance(key, str) else key: (
                    REDACTED
                    if isinstance(key, str) and isinstance(item, str)
                    and _CREDENTIAL_FIELD_NAME.fullmatch(key)
                    else copy(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [copy(item) for item in value]
        if isinstance(value, tuple):
            return tuple(copy(item) for item in value)
        return value

    return copy(payload)
