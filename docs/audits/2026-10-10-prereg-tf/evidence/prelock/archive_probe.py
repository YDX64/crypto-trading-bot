#!/usr/bin/env python3
"""E17 pre-lock: VAL archive existence probe (PLAN §4.7). HTTP HEAD ONLY (curl -sI via configured proxy), no content read.
Writes docs/audits/2026-10-10-prereg-tf/archive_probe.json  (url -> status)."""
import datetime as dt, json, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

OUT = Path("/home/user/crypto-trading-bot/docs/audits/2026-10-10-prereg-tf/archive_probe.json")
SYMS = "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,BNBUSDT,ADAUSDT,LTCUSDT".split(",")
BASE = "https://data.binance.vision/data/futures/um"

def months(a, b):  # inclusive "YYYY-MM"
    y, m = map(int, a.split("-")); yb, mb = map(int, b.split("-"))
    while (y, m) <= (yb, mb):
        yield f"{y:04d}-{m:02d}"
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)

def url(kind, sym, tf, period):
    return f"{BASE}/{kind}/klines/{sym}/{tf}/{sym}-{tf}-{period}.zip"

items = []   # (category, url)
for s in SYMS:
    for p in months("2022-11", "2024-10"):
        items.append(("4h_monthly", url("monthly", s, "4h", p)))
    for d in range(1, 18):
        items.append(("4h_daily_2024-11", url("daily", s, "4h", f"2024-11-{d:02d}")))
    for tf in ("1h", "30m"):
        for p in months("2022-12", "2024-10"):
            items.append((f"{tf}_monthly", url("monthly", s, tf, p)))
        for d in (1, 17):   # EXTRA (not in the task list): Nov-2024 partial tail for 1h/30m comes from daily zips
            items.append((f"{tf}_daily_2024-11_extra", url("daily", s, tf, f"2024-11-{d:02d}")))
    for tf in ("5m", "15m"):
        for p in months("2022-12", "2024-11"):
            items.append((f"{tf}_daily_15th", url("daily", s, tf, f"{p}-15")))
# CHECKSUM sample: first URL of each category per symbol
seen = set(); csum = []
for cat, u in items:
    k = (cat, u.split("/klines/")[1].split("/")[0])
    if k not in seen:
        seen.add(k); csum.append((cat + "_CHECKSUM", u + ".CHECKSUM"))
items += csum

def head(cu):
    cat, u = cu
    for attempt in range(3):
        r = subprocess.run(["curl", "-sI", "-o", "/dev/null", "-m", "30", "-w", "%{http_code}", u],
                           capture_output=True, text=True)
        code = r.stdout.strip()
        if code.isdigit() and code != "000":
            return cat, u, int(code)
        time.sleep(1 + attempt)
    return cat, u, 0

t0 = time.time()
with ThreadPoolExecutor(max_workers=4) as ex:
    res = list(ex.map(head, items))
status = {u: c for _cat, u, c in res}
cats = {}
for cat, u, c in res:
    d = cats.setdefault(cat, {"total": 0, "200": 0, "non200": 0})
    d["total"] += 1; d["200" if c == 200 else "non200"] += 1
non200 = {u: c for _cat, u, c in res if c != 200}
doc = {
    "schema": "e17-archive-probe/1",
    "plan": "PLAN.md §4.7 (VAL arsiv varlik yoklamasi; yalniz HTTP HEAD, icerik okunmadi)",
    "method": "curl -sI (HEAD) via configured proxy; <=4 parallel; retry x3 on transport failure",
    "base": BASE,
    "generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "symbols": SYMS,
    "totals": {"urls": len(res), "http_200": sum(1 for _c, _u, c in res if c == 200), "non_200": len(non200)},
    "by_category": cats,
    "non_200": non200,
    "status": dict(sorted(status.items())),
}
OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
print("urls", len(res), "200:", doc["totals"]["http_200"], "non200:", len(non200), "elapsed_s", round(time.time() - t0))
for k, v in sorted(cats.items()): print(" ", k, v)
for u, c in list(non200.items())[:40]: print("NON200", c, u)
