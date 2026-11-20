"""Funding oranı kaynağı — YALNIZ backtest harness'ı (canlı motor kullanmaz).

Binance USDⓈ-M funding'i 00:00, 08:00 ve 16:00 UTC'de tahsil eder. Bu
modül (a) o anları sayar, (b) `data/funding_cache/<SYMBOL>.json` dosyasındaki
tarihsel oranları (`[{"funding_time": ms, "rate": float}, ...]`) okur ve
(c) dosyanın kapsamadığı anlar için sabit bir yedek oran verir.

Saf ve AĞSIZ: önbelleği `scripts/fill_funding_cache.py` doldurur.
"""

from __future__ import annotations

import bisect
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

FUNDING_INTERVAL_MS = 8 * 3_600_000  # 00:00 / 08:00 / 16:00 UTC (epoch'a hizalı)

# Binance funding_time bazen birkaç ms kayık gelir (ör. 00:00:00.005); bir
# tarihsel satır, ilgili tahsilat anına bu kadar yakınsa o anı kapsar.
MATCH_TOLERANCE_MS = 60_000

# Yedek oran (8 saatte bir, YÜZDE): 0.01 = %0.01 = Binance'in taban oranı.
DEFAULT_FUNDING_FALLBACK_PCT = 0.01

SOURCE_HISTORICAL = "historical"
SOURCE_FALLBACK = "fallback"


def settlements_between(start_ms: int, end_ms: int) -> List[int]:
    """`start_ms < T <= end_ms` aralığındaki tüm tahsilat anları (ms, artan)."""
    if end_ms <= start_ms:
        return []
    first = (int(start_ms) // FUNDING_INTERVAL_MS + 1) * FUNDING_INTERVAL_MS
    return list(range(first, int(end_ms) + 1, FUNDING_INTERVAL_MS))


def load_funding_rows(symbol: str, cache_dir: Optional[str | Path]) -> List[Tuple[int, float]]:
    """`<cache_dir>/<SYMBOL>.json` → zamana göre sıralı [(ms, oran)].
    Dosya yok / bozuk / `cache_dir=None` → boş liste (tamamı yedek oran)."""
    if cache_dir is None:
        return []
    path = Path(cache_dir) / f"{str(symbol).upper()}.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows: List[Tuple[int, float]] = []
    if not isinstance(raw, list):
        return rows
    for item in raw:
        try:
            rows.append((int(item["funding_time"]), float(item["rate"])))
        except (KeyError, TypeError, ValueError):
            continue
    rows.sort(key=lambda r: r[0])
    return rows


class FundingSchedule:
    """Tek sembolün funding oranları: tarihsel satırlar + yedek oran."""

    def __init__(
        self,
        symbol: str,
        rows: Sequence[Tuple[int, float]] = (),
        fallback_rate: float = DEFAULT_FUNDING_FALLBACK_PCT / 100.0,
    ) -> None:
        self.symbol = symbol
        self.fallback_rate = float(fallback_rate)
        self._rows: List[Tuple[int, float]] = sorted(
            ((int(t), float(r)) for t, r in rows), key=lambda r: r[0]
        )
        self._times: List[int] = [t for t, _ in self._rows]

    def rate_at(self, settlement_ms: int) -> Tuple[float, str]:
        """(oran, kaynak). Tarihsel satır ±MATCH_TOLERANCE_MS içinde varsa
        `historical`, yoksa sabit yedek oranla `fallback`."""
        if self._times:
            pos = bisect.bisect_left(self._times, settlement_ms - MATCH_TOLERANCE_MS)
            best: Optional[Tuple[int, float]] = None
            while pos < len(self._rows) and self._rows[pos][0] <= settlement_ms + MATCH_TOLERANCE_MS:
                t, r = self._rows[pos]
                if best is None or abs(t - settlement_ms) < abs(best[0] - settlement_ms):
                    best = (t, r)
                pos += 1
            if best is not None:
                return best[1], SOURCE_HISTORICAL
        return self.fallback_rate, SOURCE_FALLBACK


def load_funding_schedule(
    symbol: str,
    cache_dir: Optional[str | Path],
    fallback_rate: float = DEFAULT_FUNDING_FALLBACK_PCT / 100.0,
) -> FundingSchedule:
    return FundingSchedule(symbol, load_funding_rows(symbol, cache_dir), fallback_rate)


def funding_payment(direction_is_long: bool, qty: float, price: float, rate: float) -> float:
    """Ödenen funding (USD; pozitif = hesaptan ÇIKAR). LONG rate>0'da öder,
    rate<0'da alır; SHORT tersi."""
    notional_rate = qty * price * rate
    return notional_rate if direction_is_long else -notional_rate


def summarize_sources(events: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    out = {SOURCE_HISTORICAL: 0, SOURCE_FALLBACK: 0}
    for e in events:
        out[e.get("source", SOURCE_FALLBACK)] = out.get(e.get("source", SOURCE_FALLBACK), 0) + 1
    return out
