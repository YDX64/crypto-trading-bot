#!/usr/bin/env python3
"""news_bot_client.py — reference HTTP client for TRADINGBOT's two external-signal
channels (`docs/INTEGRATIONS.md`). Copy this file into a new bot's own repo; do
NOT import it from inside the trading engine (`src/`) — it is meant to live
outside the process it talks to, exactly like a real news/event bot would.

Dependency-light on purpose: Python 3.12 stdlib only (`urllib`), so a weekend
bot doesn't need a virtualenv just to send a POST. Two channels, two secrets,
two different places the secret travels — this mirrors the server exactly
(`src/main.py::resolve_tv_signal` / `src/main.py::risk_event`):

  /tv-signal   — directional signal only. Secret travels in the URL QUERY
                 STRING (?secret=...). This is not a style choice: TradingView's
                 own alert webhook cannot always fill the JSON body (LuxAlgo's
                 "Any alert() function call" mode lets the *indicator* write the
                 body) and can never set custom headers, so the engine accepts
                 the secret on the URL as a fallback. A news bot that sends its
                 secret the LuxAlgo way exercises the exact code path already
                 hardened for real TradingView alerts, instead of a second,
                 less-tested one.
  /risk-event  — halt / resume / flatten / status. Secret travels in the JSON
                 BODY. This channel has no TradingView constraint (a news bot
                 fully controls its own HTTP client), so the secret goes where
                 it is least likely to leak: never in an access log, never in
                 a URL that gets copy-pasted into a bug report.

Both secrets are optional independently: `tv_secret` is required to call
`signal()`; `risk_secret` is required to call `halt()`/`resume()`/`flatten()`/
`status()`. A bot that only ever proposes directions never needs a risk secret.

See docs/INTEGRATIONS.md before wiring a new bot — in particular the rules
in §2 (one `src` per bot, symbol allowlist, halt/flatten only for extreme
events) and the promotion checklist in §4/§6.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

__all__ = ["TradingBotClient", "TradingBotClientError", "main"]


class TradingBotClientError(Exception):
    """Raised for HTTP 4xx responses (client's own request was rejected).

    4xx is never retried: a wrong secret, an unresolved symbol, or a malformed
    body will not fix itself on the next attempt — retrying just spams the
    server's access log and delays the caller finding out.
    """

    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(f"HTTP {status}: {detail}")


class TradingBotClient:
    """Minimal client for `POST /tv-signal` and `POST /risk-event`.

    Parameters
    ----------
    base_url:
        e.g. ``"http://127.0.0.1:9091"`` (testnet bot) — no trailing slash needed.
    tv_secret:
        Value of the server's ``TV_WEBHOOK_SECRET``. Required for `signal()`.
    risk_secret:
        Value of the server's ``RISK_EVENT_SECRET``. Required for
        `halt()`/`resume()`/`flatten()`/`status()`. ``None`` (default) if this
        bot never sends risk events.
    source:
        This bot's `src` label (docs/INTEGRATIONS.md §2.2) — ONE label per bot
        instance. Must be added to the server's ``TV_SOURCE_ALLOWLIST`` before
        it counts as an independent confluence vote; until then it is silently
        folded into the generic ``"tv"`` source (still delivered, never
        rejected — see `resolve_tv_source` in `src/main.py`).
    dry_run:
        If True, no network call is made — every request is printed (with the
        secret redacted) to stdout instead. Use this to build/test a bot's
        classification logic before it is allowed to touch the real engine.
    """

    TV_PATH = "/tv-signal"
    RISK_PATH = "/risk-event"

    def __init__(
        self,
        base_url: str,
        tv_secret: str,
        risk_secret: Optional[str] = None,
        source: str = "news_macro",
        *,
        timeout: float = 5.0,
        max_retries: int = 2,
        backoff_base: float = 0.5,
        dry_run: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.tv_secret = tv_secret
        self.risk_secret = risk_secret
        self.source = source
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.dry_run = dry_run

    # -- public API -----------------------------------------------------

    def signal(self, symbol: str, side: str) -> dict:
        """POST /tv-signal?src=<source>&secret=<tv_secret> — a directional
        suggestion only. `side`: buy/long/bull for long, sell/short/bear for
        short (the engine parses these words; see `resolve_tv_signal` in
        `src/main.py`). Returns the parsed JSON response, including
        `accepted` and `confluence` (the vote state — a single source alone
        never opens a trade, see docs/INTEGRATIONS.md §1).
        """
        query = {"src": self.source, "secret": self.tv_secret}
        body = {"symbol": symbol, "side": side}
        return self._request(self.TV_PATH, query=query, body=body)

    def halt(self, reason: str, ttl_minutes: int = 60) -> dict:
        """POST /risk-event action=halt — stop new entries for `ttl_minutes`
        (auto-expires; open positions' SL/TP/trailing are untouched). `reason`
        is mandatory and is what ends up in the engine's audit log."""
        return self._risk_event("halt", reason=reason, ttl_minutes=ttl_minutes)

    def resume(self) -> dict:
        """POST /risk-event action=resume — lift a risk-event halt early."""
        return self._risk_event("resume")

    def flatten(self, reason: str, ttl_minutes: int = 240) -> dict:
        """POST /risk-event action=flatten — halt (see `halt()`) THEN
        reduce-only close every tracked scalper position. `reason` is
        mandatory. Reserve this for extreme, market-wide events only — it is
        not a per-trade exit tool (docs/INTEGRATIONS.md §2.4)."""
        return self._risk_event("flatten", reason=reason, ttl_minutes=ttl_minutes)

    def status(self) -> dict:
        """POST /risk-event action=status — read-only halt/position snapshot.
        Safe to poll; never mutates state."""
        return self._risk_event("status")

    # -- internals --------------------------------------------------------

    def _risk_event(
        self, action: str, reason: str = "", ttl_minutes: Optional[int] = None
    ) -> dict:
        if not self.risk_secret:
            raise ValueError(
                "risk_secret is required for halt/resume/flatten/status — "
                "construct TradingBotClient(..., risk_secret=<RISK_EVENT_SECRET>)"
            )
        body: dict = {"action": action, "secret": self.risk_secret, "source": self.source}
        if reason:
            body["reason"] = reason
        if ttl_minutes is not None:
            body["ttl_minutes"] = ttl_minutes
        return self._request(self.RISK_PATH, query=None, body=body)

    def _request(self, path: str, query: Optional[dict], body: dict) -> dict:
        url = self.base_url + path
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"

        if self.dry_run:
            self._print_dry_run(path, query, body)
            return {"dry_run": True, "path": path}

        payload = json.dumps(body).encode("utf-8")
        attempt = 0
        while True:
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                    return json.loads(raw.decode("utf-8")) if raw else {}
            except urllib.error.HTTPError as exc:
                detail = self._read_error_detail(exc)
                if 400 <= exc.code < 500:
                    # Never retry a client error — see TradingBotClientError docstring.
                    raise TradingBotClientError(exc.code, detail) from None
                if attempt >= self.max_retries:
                    raise TradingBotClientError(exc.code, detail) from None
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
                if attempt >= self.max_retries:
                    raise ConnectionError(f"{path}: {exc}") from exc

            attempt += 1
            time.sleep(self.backoff_base * (2 ** (attempt - 1)))

    @staticmethod
    def _read_error_detail(exc: urllib.error.HTTPError) -> str:
        try:
            raw = exc.read()
            parsed = json.loads(raw.decode("utf-8"))
            if isinstance(parsed, dict) and "detail" in parsed:
                return str(parsed["detail"])
            return raw.decode("utf-8", errors="replace")
        except Exception:
            return str(exc.reason)

    def _print_dry_run(self, path: str, query: Optional[dict], body: dict) -> None:
        print(f"[DRY-RUN] POST {self.base_url}{path}")
        if query:
            print(f"  query = {self._redact(query)}")
        print(f"  body  = {self._redact(body)}")

    @staticmethod
    def _redact(d: dict) -> dict:
        # Never print secrets — the ONLY field carrying one is literally named
        # "secret" on both channels (see class docstring).
        return {k: ("***" if k == "secret" else v) for k, v in d.items()}


# ---------------------------------------------------------------------------
# CLI — thin wrapper so a bot author can smoke-test the client by hand before
# wiring it into anything. Not meant to replace a real bot's own entrypoint.
# ---------------------------------------------------------------------------


_COMMON_FLAG_SPECS: list[tuple[tuple, dict]] = [
    (
        ("--base",),
        dict(
            default=os.environ.get("TRADINGBOT_BASE_URL", "http://127.0.0.1:9091"),
            help="Bot base URL, e.g. http://127.0.0.1:9091 (env TRADINGBOT_BASE_URL)",
        ),
    ),
    (
        ("--tv-secret",),
        dict(
            default=os.environ.get("TV_WEBHOOK_SECRET", ""),
            help="TV_WEBHOOK_SECRET value (env TV_WEBHOOK_SECRET). Not needed for --dry-run.",
        ),
    ),
    (
        ("--risk-secret",),
        dict(
            default=os.environ.get("RISK_EVENT_SECRET") or None,
            help="RISK_EVENT_SECRET value (env RISK_EVENT_SECRET). Only needed for halt/resume/flatten/status.",
        ),
    ),
    (
        ("--source",),
        dict(
            default=os.environ.get("NEWS_BOT_SOURCE", "news_macro"),
            help="This bot's src label (default: news_macro).",
        ),
    ),
    (("--timeout",), dict(type=float, default=5.0)),
    (("--max-retries",), dict(type=int, default=2)),
    (
        ("--dry-run",),
        dict(
            action="store_true",
            help="Print the request instead of sending it (secret always redacted).",
        ),
    ),
]


def _add_common_args(parser: argparse.ArgumentParser, *, suppress_defaults: bool) -> None:
    """Register the global flags (--base, --tv-secret, ...) on `parser`.

    Registered on BOTH the top-level parser and every subcommand parser, so a
    flag like `--dry-run` works whether it comes before or after the
    subcommand name — i.e. both `--dry-run signal BTCUSDT sell` and
    `signal BTCUSDT sell --dry-run` parse the same way.

    `suppress_defaults` MUST be True for the subparser copies: argparse's
    subparsers re-parse the remaining tokens into a fresh namespace and then
    overwrite the parent namespace with whatever that fresh namespace holds
    — including untouched defaults. Without `default=argparse.SUPPRESS` here,
    a flag given BEFORE the subcommand (e.g. `--risk-secret X halt ...`)
    would silently be wiped back to the subparser's own default the instant
    parsing reaches `halt`. With SUPPRESS, the subparser only contributes a
    value when the flag actually appears after the subcommand, leaving a
    pre-subcommand value (or the top-level default) alone.
    """
    for flag_args, kwargs in _COMMON_FLAG_SPECS:
        kwargs = dict(kwargs)
        if suppress_defaults:
            kwargs["default"] = argparse.SUPPRESS
        parser.add_argument(*flag_args, **kwargs)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="news_bot_client.py",
        description=(
            "Reference CLI for TRADINGBOT's external-signal / risk-event API. "
            "See docs/INTEGRATIONS.md before wiring a new bot."
        ),
    )
    _add_common_args(parser, suppress_defaults=False)

    sub = parser.add_subparsers(dest="command", required=True)

    p_signal = sub.add_parser("signal", help="POST /tv-signal — directional suggestion")
    _add_common_args(p_signal, suppress_defaults=True)
    p_signal.add_argument("symbol", help="e.g. BTCUSDT")
    p_signal.add_argument("side", choices=["buy", "long", "bull", "sell", "short", "bear"])

    p_halt = sub.add_parser("halt", help="POST /risk-event action=halt")
    _add_common_args(p_halt, suppress_defaults=True)
    p_halt.add_argument("reason")
    p_halt.add_argument("--ttl-minutes", type=int, default=60)

    p_resume = sub.add_parser("resume", help="POST /risk-event action=resume")
    _add_common_args(p_resume, suppress_defaults=True)

    p_flatten = sub.add_parser("flatten", help="POST /risk-event action=flatten")
    _add_common_args(p_flatten, suppress_defaults=True)
    p_flatten.add_argument("reason")
    p_flatten.add_argument("--ttl-minutes", type=int, default=240)

    p_status = sub.add_parser("status", help="POST /risk-event action=status (read-only)")
    _add_common_args(p_status, suppress_defaults=True)

    return parser


def main(argv: Optional[list] = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    client = TradingBotClient(
        base_url=args.base,
        tv_secret=args.tv_secret,
        risk_secret=args.risk_secret,
        source=args.source,
        timeout=args.timeout,
        max_retries=args.max_retries,
        dry_run=args.dry_run,
    )

    try:
        if args.command == "signal":
            result: Any = client.signal(args.symbol, args.side)
        elif args.command == "halt":
            result = client.halt(args.reason, ttl_minutes=args.ttl_minutes)
        elif args.command == "resume":
            result = client.resume()
        elif args.command == "flatten":
            result = client.flatten(args.reason, ttl_minutes=args.ttl_minutes)
        elif args.command == "status":
            result = client.status()
        else:  # pragma: no cover - argparse `required=True` prevents this
            parser.error(f"unknown command {args.command!r}")
            return 2
    except (TradingBotClientError, ConnectionError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not args.dry_run:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
