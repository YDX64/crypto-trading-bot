#!/usr/bin/env python3
"""news_bot_skeleton.py — reference event-loop pattern for a news/event-driven
signal bot that plugs into TRADINGBOT through `news_bot_client.py`, WITHOUT
touching the trading engine (`src/`). Copy both files into a new bot's own
repo, fill in `classify()`, and wire `ingest()`/`run_forever()` to a real
headline source (RSS poller, websocket, LLM pipeline, whatever).

Pattern (docs/INTEGRATIONS.md §2):

    ingest(headline) -> classify(headline) -> de-duplicate -> rate-limit -> send

This skeleton enforces, in code, the rules a new bot must not violate:
  - ONE `src` label per bot instance (`NewsBotConfig.source`) — never split
    one model's output into two `src` values; that fakes independent
    confluence votes from what is really one opinion (docs/INTEGRATIONS.md
    §2.2).
  - Directional signals only for symbols in `NEWS_BOT_SYMBOL_ALLOWLIST` — a
    signal for a symbol the engine isn't scanning is meaningless.
  - `halt`/`flatten` only ever fire with an explicit `reason` and
    `ttl_minutes` — never silent, never open-ended (docs/INTEGRATIONS.md §2.4).
  - The same headline is never re-sent, and a runaway classifier cannot spam
    the engine past `NEWS_BOT_MAX_SIGNALS_PER_10MIN` signals per symbol.

Everything above the `# TODO` marker in `classify()` is a real, working
mechanism (dedup, rate limiting, allowlist enforcement, HTTP delivery via
`TradingBotClient`); only the classification DECISION itself is a stub —
that is the one part specific to whatever news source/model a given bot uses.
"""
from __future__ import annotations

import collections
import dataclasses
import os
import sys
import time
from typing import Deque, Dict, FrozenSet, Iterable, Optional

try:
    from news_bot_client import TradingBotClient, TradingBotClientError
except ImportError:  # running from repo root instead of examples/
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from news_bot_client import TradingBotClient, TradingBotClientError


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Headline:
    """One unit of input. `id` must be stable across re-delivery of the same
    news item (e.g. a source-provided GUID, or a hash of the normalized
    text) — it is the de-duplication key."""

    id: str
    text: str
    ts: float = dataclasses.field(default_factory=time.time)


@dataclasses.dataclass(frozen=True)
class Classification:
    """Output of `classify()`. Exactly one of three shapes:

    - kind="direction": symbol + side set, everything else None.
    - kind="risk":       action + reason set (ttl_minutes optional, defaults
                          applied by NewsBot._handle_risk).
    - kind="ignore":     nothing else set. The default — and the SAFE default:
                          a false "ignore" costs nothing, a false direction or
                          risk call costs real money or halts real trading.
    """

    kind: str
    symbol: Optional[str] = None
    side: Optional[str] = None
    action: Optional[str] = None
    reason: Optional[str] = None
    ttl_minutes: Optional[int] = None


IGNORE = Classification(kind="ignore")


def classify(headline: Headline) -> Classification:
    """Decide what one headline means. THIS IS THE STUB — replace the body.

    # TODO: LLM/kural tabanlı sınıflandırma buraya

    Must return one of:
      Classification("direction", symbol="BTCUSDT", side="sell")
          — market-moving news naming a specific, allowlisted symbol.
      Classification("risk", action="halt", reason="...", ttl_minutes=60)
      Classification("risk", action="flatten", reason="...", ttl_minutes=240)
          — extreme, market-wide event (exchange outage, war, regulatory
          shock, exchange hack). NOT for routine bearish/bullish news — that
          is a `direction` signal, not a risk event.
      Classification("ignore")
          — everything else. This is the default; keep it the default.

    A rule-based placeholder is intentionally NOT provided here beyond
    IGNORE: a keyword-matching stub tends to get treated as "good enough" and
    shipped as-is. Force yourself to write (and backtest, and shadow-run per
    docs/INTEGRATIONS.md §4) a real classifier before this bot ever calls
    `signal()`/`halt()`/`flatten()` for real.
    """
    return IGNORE


# ---------------------------------------------------------------------------
# De-duplication + rate limiting — real, working mechanisms (not stubs)
# ---------------------------------------------------------------------------


class Deduplicator:
    """Remembers headline ids already processed so the same headline is never
    re-sent (e.g. a news feed re-delivering on reconnect, or two feeds
    carrying the same wire story). Bounded so a long-running bot doesn't leak
    memory."""

    def __init__(self, max_size: int = 5000) -> None:
        self._seen: "collections.OrderedDict[str, None]" = collections.OrderedDict()
        self._max_size = max_size

    def seen_before(self, headline_id: str) -> bool:
        if headline_id in self._seen:
            self._seen.move_to_end(headline_id)
            return True
        self._seen[headline_id] = None
        if len(self._seen) > self._max_size:
            self._seen.popitem(last=False)
        return False


class RateLimiter:
    """Sliding-window cap: at most `max_per_window` signals per symbol per
    `window_seconds`. Protects the engine from a classifier that starts
    firing repeatedly on the same story (e.g. every headline update of a
    developing event)."""

    def __init__(self, max_per_window: int, window_seconds: float = 600.0) -> None:
        self.max_per_window = max_per_window
        self.window_seconds = window_seconds
        self._sent: Dict[str, Deque[float]] = collections.defaultdict(collections.deque)

    def allow(self, symbol: str, now: Optional[float] = None) -> bool:
        now = now if now is not None else time.time()
        q = self._sent[symbol]
        cutoff = now - self.window_seconds
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= self.max_per_window:
            return False
        q.append(now)
        return True


# ---------------------------------------------------------------------------
# Config — read once at startup, fails loudly if the allowlist is missing
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class NewsBotConfig:
    base_url: str
    tv_secret: str
    risk_secret: Optional[str]
    source: str
    symbol_allowlist: FrozenSet[str]
    max_signals_per_symbol_per_10min: int
    dry_run: bool

    @classmethod
    def from_env(cls) -> "NewsBotConfig":
        allowlist_raw = os.environ.get("NEWS_BOT_SYMBOL_ALLOWLIST", "")
        allowlist = frozenset(
            s.strip().upper() for s in allowlist_raw.split(",") if s.strip()
        )
        if not allowlist:
            raise RuntimeError(
                "NEWS_BOT_SYMBOL_ALLOWLIST is empty. Set it to the symbols this "
                "bot may signal for, e.g. NEWS_BOT_SYMBOL_ALLOWLIST=BTCUSDT,ETHUSDT "
                "(should be a subset of the engine's own SCALPER_TV_SYMBOL_ALLOWLIST "
                "— docs/INTEGRATIONS.md §2.3). A direction signal for a symbol the "
                "engine isn't scanning does nothing useful."
            )
        return cls(
            base_url=os.environ.get("TRADINGBOT_BASE_URL", "http://127.0.0.1:9091"),
            tv_secret=os.environ.get("TV_WEBHOOK_SECRET", ""),
            risk_secret=os.environ.get("RISK_EVENT_SECRET") or None,
            source=os.environ.get("NEWS_BOT_SOURCE", "news_macro"),
            symbol_allowlist=allowlist,
            max_signals_per_symbol_per_10min=int(
                os.environ.get("NEWS_BOT_MAX_SIGNALS_PER_10MIN", "3")
            ),
            # Default ON: a freshly-copied bot must not be able to touch the
            # real engine until someone deliberately sets NEWS_BOT_DRY_RUN=0.
            dry_run=os.environ.get("NEWS_BOT_DRY_RUN", "1") != "0",
        )


# ---------------------------------------------------------------------------
# The bot
# ---------------------------------------------------------------------------


class NewsBot:
    def __init__(self, config: NewsBotConfig) -> None:
        self.config = config
        self.client = TradingBotClient(
            base_url=config.base_url,
            tv_secret=config.tv_secret,
            risk_secret=config.risk_secret,
            source=config.source,  # ONE src for every call this bot makes
            dry_run=config.dry_run,
        )
        self.dedup = Deduplicator()
        self.rate_limiter = RateLimiter(
            max_per_window=config.max_signals_per_symbol_per_10min,
            window_seconds=600.0,
        )

    def ingest(self, headline: Headline) -> Optional[dict]:
        """Process one headline end to end: classify -> de-dup -> rate-limit
        -> send. Returns the API response dict, or None if the headline was
        dropped (duplicate, ignored, off-allowlist, or rate-limited) — a drop
        is the expected outcome for most headlines, not an error."""
        if self.dedup.seen_before(headline.id):
            return None

        decision = classify(headline)

        if decision.kind == "ignore":
            return None
        if decision.kind == "direction":
            return self._handle_direction(decision)
        if decision.kind == "risk":
            return self._handle_risk(decision)

        raise ValueError(f"classify() returned unknown kind={decision.kind!r}")

    def _handle_direction(self, decision: Classification) -> Optional[dict]:
        symbol = (decision.symbol or "").upper()
        if symbol not in self.config.symbol_allowlist:
            return None
        if not self.rate_limiter.allow(symbol):
            return None
        try:
            return self.client.signal(symbol, decision.side or "")
        except TradingBotClientError as exc:
            # A 4xx here means OUR request was malformed (bad symbol/side/
            # secret) — surface it and move on, don't retry blindly.
            print(f"news_bot_skeleton: signal rejected: {exc}", file=sys.stderr)
            return None
        except ConnectionError as exc:
            print(f"news_bot_skeleton: signal delivery failed: {exc}", file=sys.stderr)
            return None

    def _handle_risk(self, decision: Classification) -> Optional[dict]:
        if not decision.reason:
            raise ValueError("risk classification requires a non-empty reason")
        try:
            if decision.action == "halt":
                return self.client.halt(decision.reason, ttl_minutes=decision.ttl_minutes or 60)
            if decision.action == "flatten":
                return self.client.flatten(decision.reason, ttl_minutes=decision.ttl_minutes or 240)
        except TradingBotClientError as exc:
            print(f"news_bot_skeleton: risk-event rejected: {exc}", file=sys.stderr)
            return None
        except ConnectionError as exc:
            print(f"news_bot_skeleton: risk-event delivery failed: {exc}", file=sys.stderr)
            return None
        raise ValueError(
            f"risk classification requires action='halt' or 'flatten', got {decision.action!r}"
        )


def run_forever(bot: NewsBot, headlines: Iterable[Headline]) -> None:
    """Minimal event loop: consume an iterable/generator of `Headline`s.

    In a real bot, `headlines` is not a finite list — it is an RSS poller, a
    websocket consumer, or a `queue.get()` loop that never returns. This
    function is intentionally that dumb: all the actual policy lives in
    `NewsBot.ingest()` above, this just drives it.
    """
    for headline in headlines:
        result = bot.ingest(headline)
        if result is not None:
            print(f"news_bot_skeleton: {headline.id} -> {result}")


if __name__ == "__main__":
    cfg = NewsBotConfig.from_env()
    bot = NewsBot(cfg)
    print(
        "news_bot_skeleton: source={source!r} allowlist={allowlist} dry_run={dry_run} "
        "— skeleton only, classify() is a stub and there is no real headline feed "
        "wired up. Import NewsBot/run_forever from your own bot instead of running "
        "this file directly.".format(
            source=cfg.source,
            allowlist=sorted(cfg.symbol_allowlist),
            dry_run=cfg.dry_run,
        ),
        file=sys.stderr,
    )
