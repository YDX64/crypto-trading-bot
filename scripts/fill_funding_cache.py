#!/usr/bin/env python3
"""
Binance USDⓈ-M tarihsel funding oranları -> backtest funding önbelleği.

Halka açık uç: GET https://fapi.binance.com/fapi/v1/fundingRate
(symbol, startTime, endTime, limit=1000; API anahtarı GEREKMEZ). Sayfalar
çağrılar arası 0.5 sn beklenerek çekilir; HTTP 418 / 429'da net bir mesajla
DURULUR (ban/limit aşılırken zorlanmaz). Çıktı: `<cache-dir>/<SEMBOL>.json` =
`[{"funding_time": ms, "rate": float}, ...]` (zamana göre sıralı, mevcut dosya
ile birleştirilir). Harness bunu `--funding-cache-dir` ile okur
(`src/strategies/scalper/funding.py`); kapsanmayan anlar için sabit yedek oran.

Örnek:
  .venv/bin/python scripts/fill_funding_cache.py \
      --symbols BTCUSDT,ETHUSDT --start 2026-08-07 --end 2026-08-21

Çıkış kodu: 0 başarı; 2 hata / durdurma (418, 429, ağ, argüman).
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

BASE_URL = "https://fapi.binance.com"
ENDPOINT = "/fapi/v1/fundingRate"
PAGE_LIMIT = 1000
SLEEP_SECONDS = 0.5
DEFAULT_CACHE_DIR = "data/funding_cache"

# Taşıyıcı: params -> (http_durumu, gövde[list|dict|None]). Test sahte verir.
Transport = Callable[[Dict[str, Any]], Tuple[int, Any]]


class FundingFetchError(RuntimeError):
    """Durdurulması gereken hata (418/429/HTTP/biçim)."""


def parse_funding_rows(payload: Any) -> List[Dict[str, Any]]:
    """Binance yanıtı [{"symbol","fundingTime","fundingRate","markPrice"}] ->
    [{"funding_time": int ms, "rate": float}]. Bozuk satırlar atlanır."""
    rows: List[Dict[str, Any]] = []
    if not isinstance(payload, list):
        return rows
    for item in payload:
        try:
            rows.append({
                "funding_time": int(item["fundingTime"]),
                "rate": float(item["fundingRate"]),
            })
        except (KeyError, TypeError, ValueError):
            continue
    return rows


def fetch_symbol(
    transport: Transport,
    symbol: str,
    start_ms: int,
    end_ms: int,
    sleep: Callable[[float], None] = time.sleep,
) -> List[Dict[str, Any]]:
    """[start_ms, end_ms] aralığındaki tüm funding satırları (sayfalayarak).
    Her çağrıdan SONRA `SLEEP_SECONDS` beklenir. 418/429 -> FundingFetchError."""
    out: Dict[int, Dict[str, Any]] = {}
    cursor = int(start_ms)
    while cursor <= end_ms:
        status, body = transport({
            "symbol": symbol, "startTime": cursor, "endTime": int(end_ms),
            "limit": PAGE_LIMIT,
        })
        sleep(SLEEP_SECONDS)
        if status in (418, 429):
            raise FundingFetchError(
                f"HTTP {status} ({symbol}): Binance istek sınırı/ban — DURULDU. "
                "Bir süre bekleyip yeniden dene; mevcut önbellek dosyaları korundu."
            )
        if status != 200:
            raise FundingFetchError(f"HTTP {status} ({symbol}): {str(body)[:200]}")
        rows = parse_funding_rows(body)
        for r in rows:
            out[r["funding_time"]] = r
        if len(rows) < PAGE_LIMIT:
            break
        cursor = max(r["funding_time"] for r in rows) + 1
    return [out[k] for k in sorted(out)]


def merge_and_write(cache_dir: Path, symbol: str, rows: Sequence[Dict[str, Any]]) -> Path:
    """Mevcut dosya + yeni satırlar (aynı funding_time'da YENİ kazanır) -> atomik yazım."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{symbol.upper()}.json"
    merged: Dict[int, Dict[str, Any]] = {}
    try:
        merged.update({r["funding_time"]: r for r in parse_cached(path.read_text(encoding="utf-8"))})
    except OSError:
        pass
    merged.update({int(r["funding_time"]): {"funding_time": int(r["funding_time"]), "rate": float(r["rate"])} for r in rows})
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps([merged[k] for k in sorted(merged)]), encoding="utf-8")
    os.replace(tmp, path)
    return path


def parse_cached(text: str) -> List[Dict[str, Any]]:
    try:
        raw = json.loads(text)
    except ValueError:
        return []
    out = []
    for r in raw if isinstance(raw, list) else []:
        try:
            out.append({"funding_time": int(r["funding_time"]), "rate": float(r["rate"])})
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _parse_date_ms(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def urllib_transport(params: Dict[str, Any]) -> Tuple[int, Any]:
    url = f"{BASE_URL}{ENDPOINT}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "funding-cache-filler"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 (sabit https host)
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise FundingFetchError(f"ağ hatası ({params.get('symbol')}): {exc}") from exc


def main(
    argv: Optional[Sequence[str]] = None,
    transport: Optional[Transport] = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--symbols", required=True, help="Virgülle ayrılmış semboller")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD (UTC, dahil)")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD (UTC, hariç)")
    parser.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR)
    args = parser.parse_args(argv)
    try:
        start_ms, end_ms = _parse_date_ms(args.start), _parse_date_ms(args.end)
    except ValueError as exc:
        print(f"HATA: tarih biçimi: {exc}", file=sys.stderr)
        return 2
    if start_ms >= end_ms:
        print("HATA: --start, --end'den önce olmalı", file=sys.stderr)
        return 2
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    send = transport or urllib_transport
    for symbol in symbols:
        try:
            rows = fetch_symbol(send, symbol, start_ms, end_ms - 1, sleep=sleep)
        except FundingFetchError as exc:
            print(f"HATA: {exc}", file=sys.stderr)
            return 2
        path = merge_and_write(Path(args.cache_dir), symbol, rows)
        print(f"{symbol}: {len(rows)} funding satırı -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
