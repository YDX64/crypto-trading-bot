#!/usr/bin/env python3
"""E17 pre-lock: BTCUSDT 1d market data (2022-12-01 .. 2026-01-01) -> kline_cache format, via the
fill script's own verified internals (VisionFetcher sha256 + ArchiveKlineSource + verify_cache_file).
Writes ONLY BTCUSDT 1d (regime labels are pre-registered market data, PLAN §2)."""
import asyncio, sys, os, datetime as dt
from pathlib import Path
REPO = Path("/home/user/crypto-trading-bot")
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "scripts"))
import fill_klines_cache_from_vision as fv
from src.strategies.scalper import kline_cache as kc

root = Path(os.environ.get("E17_ROOT", Path.home() / "e17_root"))
cache = root / "cache"; dl = root / "dl_btc1d"
START = dt.datetime(2022, 12, 1, tzinfo=dt.timezone.utc)
END = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
start_ms = int(START.timestamp() * 1000); end_ms = int(END.timestamp() * 1000)
step = 86_400_000
needed = (end_ms - start_ms) // step
assert kc.window_start_ms("1d", needed, end_ms) == start_ms
tr = fv.HttpTransport()
fetcher = fv.VisionFetcher(tr, dl, log=print)
src = fv.ArchiveKlineSource(fetcher)
candles = asyncio.run(src("BTCUSDT", "1d", needed, end_ms - 1))
print("candles:", len(candles), "needed:", needed)
kc.save(cache, "BTCUSDT", "1d", start_ms, end_ms, candles)
sf = fv.verify_cache_file(kc.cache_file(cache, "BTCUSDT", "1d", start_ms, end_ms))
print("VERIFIED", sf.path.name, len(sf.candles), "downloaded", fetcher.downloaded, "from_cache", fetcher.from_cache, "missing", fetcher.missing, "requests", tr.requests)
