"""Process-wide ownership and admission control for futures symbols.

Binance Futures one-way mode exposes one net position per symbol.  The
Telegram orchestrator and the scalper use separate clients, but they must not
independently manage the same net position: a ``closePosition`` stop from one
manager would affect the other manager's quantity as well.

The registry is deliberately synchronous.  Operations are tiny in-memory
mutations protected by a ``threading.RLock``; this keeps them atomic across
async tasks without binding a global ``asyncio.Lock`` to a pytest/event loop.
Exchange state is still reconciled by callers before admission.
"""

from __future__ import annotations

from threading import RLock
from typing import Dict, Iterable, Optional, Set


class SymbolReservationRegistry:
    """Atomic symbol ownership plus account-wide capacity admission."""

    def __init__(self) -> None:
        self._owners: Dict[str, str] = {}
        self._lock = RLock()

    @staticmethod
    def _normalise(value: str) -> str:
        return str(value or "").strip().upper()

    def reserve(
        self,
        symbol: str,
        owner: str,
        *,
        capacity: Optional[int] = None,
        exchange_symbols: Iterable[str] = (),
        capacity_owners: Optional[Iterable[str]] = None,
    ) -> bool:
        """Claim *symbol* if no other manager owns it and capacity permits.

        ``exchange_symbols`` is the caller's most recent signed Binance
        snapshot.  Combining it with reservations closes the common race where
        two managers both observe one free slot before either submits an order.
        Re-acquiring a symbol by the same owner is idempotent.

        ``capacity_owners`` (D20b) restricts *which owners' reservations count
        toward ``capacity``*.  ``None`` (the default) keeps the historical
        account-wide semantics byte-for-byte.  The embedded AlgoPro follower
        runs in the same process with its own position ceiling; without this
        scoping its reservations silently consumed the scalper's (and the
        Telegram orchestrator's) account-wide slots, while nothing constrained
        the follower in return.  Callers pass the set of owners that share
        their ceiling and filter ``exchange_symbols`` accordingly.
        """

        normalised_symbol = self._normalise(symbol)
        normalised_owner = str(owner or "").strip()
        if not normalised_symbol or not normalised_owner:
            return False

        external: Set[str] = {
            self._normalise(item) for item in exchange_symbols if self._normalise(item)
        }

        with self._lock:
            current = self._owners.get(normalised_symbol)
            if current is not None:
                return current == normalised_owner

            if capacity_owners is None:
                counted = set(self._owners)
            else:
                allowed = {str(o).strip() for o in capacity_owners if str(o).strip()}
                counted = {s for s, o in self._owners.items() if o in allowed}
            occupied = counted | external
            if capacity is not None and capacity > 0 and len(occupied) >= capacity:
                return False

            self._owners[normalised_symbol] = normalised_owner
            return True

    def release(self, symbol: str, owner: str) -> bool:
        """Release only the caller's own claim; foreign claims are untouched."""

        normalised_symbol = self._normalise(symbol)
        normalised_owner = str(owner or "").strip()
        with self._lock:
            if self._owners.get(normalised_symbol) != normalised_owner:
                return False
            del self._owners[normalised_symbol]
            return True

    def owner(self, symbol: str) -> Optional[str]:
        with self._lock:
            return self._owners.get(self._normalise(symbol))

    def snapshot(self) -> Dict[str, str]:
        with self._lock:
            return dict(self._owners)

    def clear(self) -> None:
        """Clear process state (used only by deterministic tests/shutdown)."""

        with self._lock:
            self._owners.clear()


#: Ownership label of the embedded AlgoPro follower (D20b).  Defined here so
#: both engines agree on the string without importing each other.
FOLLOWER_RESERVATION_OWNER = "follower"

symbol_reservations = SymbolReservationRegistry()

