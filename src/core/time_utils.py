"""Explicit UTC serialization for legacy naive-UTC database timestamps."""

from datetime import datetime, timezone
from typing import Optional


def utc_isoformat(value: Optional[datetime]) -> Optional[str]:
    """Serialize without changing the stored timestamp or trading clocks.

    SQLAlchemy's existing SQLite rows use naive UTC. A timezone-less ISO
    string would instead be interpreted in the browser's local timezone.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()
