#!/usr/bin/env python3
"""
scripts/ledger_report.py — canlı defteri (scalp_trades) rejime böler.

NE YAPAR: `docs/RUNBOOK.md` "Haftalık" ve `docs/MAINNET_PLAN.md` §2.3'ün
istediği elle-yapılan işi otomatikleştirir — kapanmış işlemleri BTC günlük
% değişimine göre UP/FLAT/DOWN rejimine böler, yön/çıkış-nedeni/sembol/gün
kırılımlarını çıkarır ve soak (B halkası) terfi kontrol listesini
PASS/FAIL olarak yazdırır.

NE YAPMAZ: hüküm vermez — "terfi et" ya da "etme" demez; §2.3'ün maddelerini
tek tek işaretler, kararı insan verir (bkz. MAINNET_PLAN §2 madde 5).
Veritabanına/`.env`'e/sunucuya ASLA yazmaz, sadece OKUR.

Kullanım:
    python3 scripts/ledger_report.py --since "2026-08-21 12:35" --format md
    python3 scripts/ledger_report.py --since "2026-08-14" --until "2026-08-21" \
        --btc-klines-json data/btc_1d.json --format json --out report.json

Sunucuda (D6 soak örneği):
    ssh awa 'cd /opt/tradingbot-v2 && .venv/bin/python scripts/ledger_report.py \
        --since "2026-08-21 12:35" --format md'

Veri kaynağı: `--db` sqlite dosyasındaki `scalp_trades` tablosu (bkz.
src/models/scalp_trade.py) ve Binance USDⓈ-M Futures public
`/fapi/v1/klines` endpoint'i (kimlik doğrulama gerekmez) — ya da
`--btc-klines-json` ile aynı biçimde (Binance kline dizisi) çevrimdışı
bir dosya.

Bağımlılık: yalnız Python standart kütüphanesi (argparse, json, sqlite3,
urllib). Ağ isteği YALNIZ `--btc-klines-json` verilmediğinde yapılır.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_DB = "tradingbot.db"
BINANCE_KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"
DEFAULT_SYMBOL = "BTCUSDT"

# docs/EXPERIMENTS.md "rejim referansları" ile aynı eşikler.
UP_THRESHOLD_PCT = 1.5
DOWN_THRESHOLD_PCT = -1.5

REGIME_ORDER = ["UP", "FLAT", "DOWN", "?"]
EXIT_REASON_ORDER = ["SL", "TP_LADDER", "TRAIL", "MANUAL", "UNKNOWN"]

# docs/MAINNET_PLAN.md §2 madde 3 — soak (B halkası) terfi ölçütleri.
SOAK_MIN_DAYS = 5
SOAK_UNKNOWN_MAX_PCT = 5.0


# --------------------------------------------------------------------------
# Veri tipleri
# --------------------------------------------------------------------------

@dataclass
class ClosedTrade:
    id: int
    strategy: str
    symbol: str
    direction: str  # "LONG" | "SHORT"
    realized_pnl: float
    exit_reason: str  # ham değer NULL/"" ise "UNKNOWN"'a normalize edilir
    closed_at: datetime  # naive UTC
    day: str  # "YYYY-MM-DD" (UTC)


# --------------------------------------------------------------------------
# Zaman ayrıştırma
# --------------------------------------------------------------------------

_TIMESTAMP_FORMATS = (
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d",
)


def parse_dt(s: str) -> datetime:
    """CLI/DB'den gelen zaman metnini naive UTC datetime'a çevirir.

    DB'deki zamanlar naive UTC string'dir (`YYYY-MM-DD HH:MM:SS.ffffff`,
    `datetime.utcnow()` çıktısı). CLI `--since`/`--until` "YYYY-MM-DD HH:MM"
    biçiminde ama saniye/mikrosaniye de kabul edilir.
    """
    text = s.strip()
    for fmt in _TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"Zaman ayrıştırılamadı: {s!r}")


def _parse_db_timestamp(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return parse_dt(s)
    except ValueError:
        return None


def iter_days(since: datetime, until: datetime) -> List[str]:
    """[since.date(), until.date()] arasındaki her takvim gününü döner."""
    day = since.date()
    last = until.date()
    days: List[str] = []
    while day <= last:
        days.append(day.strftime("%Y-%m-%d"))
        day += timedelta(days=1)
    return days


# --------------------------------------------------------------------------
# scalp_trades okuma
# --------------------------------------------------------------------------

def load_closed_trades(
    db_path: str,
    since: datetime,
    until: datetime,
    strategy: Optional[str] = None,
) -> Tuple[List[ClosedTrade], int]:
    """`status='CLOSED'` (SHADOW/OPEN hariç) işlemleri `closed_at` aralığına
    göre yükler. `closed_at` ayrıştırılamayan (bozuk/NULL) kayıtlar atlanır;
    dönen ikinci değer atlanan kayıt sayısıdır (rapora not olarak düşer).

    `strategy` verilirse (ör. `AP` = AlgoPro takipçi halkası, D20) yalnız o
    strateji etiketli işlemler alınır — iki halka aynı şemayı kullandığı için
    (ayrı DB dosyaları olsa da) rapor tek bir kaynağa daraltılabilmelidir.
    """
    wanted_strategy = (strategy or "").strip().upper() or None
    con = sqlite3.connect(db_path)
    try:
        con.row_factory = sqlite3.Row
        cur = con.execute(
            "SELECT id, strategy, symbol, direction, realized_pnl, exit_reason, closed_at "
            "FROM scalp_trades WHERE status = 'CLOSED'"
        )
        rows = cur.fetchall()
    finally:
        con.close()

    trades: List[ClosedTrade] = []
    skipped = 0
    for row in rows:
        if wanted_strategy is not None:
            if str(row["strategy"] or "").strip().upper() != wanted_strategy:
                continue
        closed_dt = _parse_db_timestamp(row["closed_at"])
        if closed_dt is None:
            skipped += 1
            continue
        if not (since <= closed_dt <= until):
            continue
        exit_reason = (row["exit_reason"] or "").strip() or "UNKNOWN"
        trades.append(ClosedTrade(
            id=row["id"],
            strategy=row["strategy"] or "?",
            symbol=row["symbol"] or "?",
            direction=row["direction"] or "?",
            realized_pnl=float(row["realized_pnl"] or 0.0),
            exit_reason=exit_reason,
            closed_at=closed_dt,
            day=closed_dt.strftime("%Y-%m-%d"),
        ))
    trades.sort(key=lambda t: t.closed_at)
    return trades, skipped


# --------------------------------------------------------------------------
# BTC günlük rejim
# --------------------------------------------------------------------------

def fetch_binance_daily_klines(
    start_date: str, end_date: str, symbol: str = DEFAULT_SYMBOL,
) -> List[List[Any]]:
    """Binance USDⓈ-M Futures public `/fapi/v1/klines` (kimlik doğrulama
    GEREKMEZ). `start_date`/`end_date` "YYYY-MM-DD" (UTC, dahil)."""
    start_ms = int(
        datetime.strptime(start_date, "%Y-%m-%d")
        .replace(tzinfo=timezone.utc).timestamp() * 1000
    )
    end_exclusive = (
        datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        + timedelta(days=1)
    )
    end_ms = int(end_exclusive.timestamp() * 1000) - 1
    query = (
        f"symbol={symbol}&interval=1d&startTime={start_ms}&endTime={end_ms}&limit=1000"
    )
    url = f"{BINANCE_KLINES_URL}?{query}"
    req = urllib.request.Request(url, headers={"User-Agent": "tradingbot-ledger-report/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        payload = resp.read().decode("utf-8")
    return json.loads(payload)


def load_offline_klines(path: str) -> List[List[Any]]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def klines_to_daily_changes(klines: List[List[Any]]) -> Dict[str, float]:
    """Binance kline dizisini {"YYYY-MM-DD": günlük %değişim} sözlüğüne
    çevirir. Gün, mumun open_time'ının (ms, UTC) tarihidir."""
    out: Dict[str, float] = {}
    for row in klines:
        open_time_ms = int(row[0])
        day = datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        open_px = float(row[1])
        close_px = float(row[4])
        pct = ((close_px - open_px) / open_px * 100.0) if open_px else 0.0
        out[day] = pct
    return out


def classify_regime(pct: Optional[float]) -> str:
    """docs/EXPERIMENTS.md kuralı: UP>+1.5, DOWN<-1.5, aksi FLAT.
    Kline eksikse (pct=None) "?" — bilinmiyor."""
    if pct is None:
        return "?"
    if pct > UP_THRESHOLD_PCT:
        return "UP"
    if pct < DOWN_THRESHOLD_PCT:
        return "DOWN"
    return "FLAT"


# --------------------------------------------------------------------------
# İstatistik yardımcıları
# --------------------------------------------------------------------------

def _profit_factor(gross_win: float, gross_loss: float) -> float:
    if gross_loss > 0:
        return gross_win / gross_loss
    return float("inf") if gross_win > 0 else 0.0


def _group_stats(trades: List[ClosedTrade]) -> Dict[str, Any]:
    n = len(trades)
    wins = [t for t in trades if t.realized_pnl > 0]
    losses = [t for t in trades if t.realized_pnl < 0]
    pnl = sum(t.realized_pnl for t in trades)
    gross_win = sum(t.realized_pnl for t in wins)
    gross_loss = abs(sum(t.realized_pnl for t in losses))
    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "winrate": (len(wins) / n * 100.0) if n else 0.0,
        "pnl": pnl,
        "profit_factor": _profit_factor(gross_win, gross_loss),
        "avg_win": (gross_win / len(wins)) if wins else 0.0,
        "avg_loss": (-gross_loss / len(losses)) if losses else 0.0,
    }


# --------------------------------------------------------------------------
# Tablolar
# --------------------------------------------------------------------------

def build_regime_direction_table(
    trades: List[ClosedTrade], daily_changes: Dict[str, float], days: List[str],
) -> List[Dict[str, Any]]:
    """Tablo 1: rejim × yön. Rapor aralığında GERÇEKTEN görülen rejimler
    için LONG/SHORT satırları (işlemsiz kombinasyonlar da 0'la görünür —
    ör. hiç DOWN-SHORT işlemi yoksa bu boşluk görünür kalmalı)."""
    seen_regimes = {classify_regime(daily_changes.get(day)) for day in days}
    for t in trades:
        seen_regimes.add(classify_regime(daily_changes.get(t.day)))
    ordered_regimes = [r for r in REGIME_ORDER if r in seen_regimes]

    grouped: Dict[Tuple[str, str], List[ClosedTrade]] = {}
    for t in trades:
        regime = classify_regime(daily_changes.get(t.day))
        grouped.setdefault((regime, t.direction), []).append(t)

    rows: List[Dict[str, Any]] = []
    for regime in ordered_regimes:
        for direction in ("LONG", "SHORT"):
            stats = _group_stats(grouped.get((regime, direction), []))
            rows.append({"regime": regime, "direction": direction, **stats})
    return rows


def build_exit_reason_direction_table(trades: List[ClosedTrade]) -> List[Dict[str, Any]]:
    """Tablo 2: çıkış nedeni × yön. Yalnız gerçekten görülen kombinasyonlar
    (exit_reason evreni sabit değil — model yorumu 5 değer sayar ama alan
    serbest metin)."""
    grouped: Dict[Tuple[str, str], List[ClosedTrade]] = {}
    for t in trades:
        grouped.setdefault((t.exit_reason, t.direction), []).append(t)

    def sort_key(key: Tuple[str, str]) -> Tuple[int, str, str]:
        reason, direction = key
        idx = EXIT_REASON_ORDER.index(reason) if reason in EXIT_REASON_ORDER else len(EXIT_REASON_ORDER)
        return (idx, reason, direction)

    rows: List[Dict[str, Any]] = []
    for reason, direction in sorted(grouped.keys(), key=sort_key):
        stats = _group_stats(grouped[(reason, direction)])
        rows.append({"exit_reason": reason, "direction": direction, **stats})
    return rows


def build_symbol_table(trades: List[ClosedTrade]) -> List[Dict[str, Any]]:
    """Tablo 3: sembol bazında (yön birleşik)."""
    grouped: Dict[str, List[ClosedTrade]] = {}
    for t in trades:
        grouped.setdefault(t.symbol, []).append(t)
    rows: List[Dict[str, Any]] = []
    for symbol in sorted(grouped.keys()):
        stats = _group_stats(grouped[symbol])
        rows.append({"symbol": symbol, **stats})
    return rows


def build_daily_table(
    trades: List[ClosedTrade], daily_changes: Dict[str, float], days: List[str],
) -> List[Dict[str, Any]]:
    """Tablo 4: gün bazında BTC % + o günün PnL'i + kümülatif PnL. İşlemsiz
    günler de 0 işlemle listelenir (kümülatif çizgi kopmasın diye)."""
    by_day: Dict[str, List[ClosedTrade]] = {}
    for t in trades:
        by_day.setdefault(t.day, []).append(t)

    rows: List[Dict[str, Any]] = []
    cum = 0.0
    for day in days:
        day_trades = by_day.get(day, [])
        day_pnl = sum(t.realized_pnl for t in day_trades)
        cum += day_pnl
        pct = daily_changes.get(day)
        rows.append({
            "day": day,
            "regime": classify_regime(pct),
            "btc_pct": pct,
            "trades": len(day_trades),
            "pnl": day_pnl,
            "cum_pnl": cum,
        })
    return rows


def build_headline(
    trades: List[ClosedTrade], daily_changes: Dict[str, float], days: List[str],
) -> Dict[str, Any]:
    """Tablo 5: özet — toplamlar + maxDD + UP payı + UNKNOWN payı + rejim
    gün/PnL sayaçları (checklist bunun üstüne kurulur)."""
    overall = _group_stats(trades)

    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades:  # trades zaten closed_at'e göre sıralı
        cum += t.realized_pnl
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    total_pnl = overall["pnl"]
    up_pnl = sum(
        t.realized_pnl for t in trades
        if classify_regime(daily_changes.get(t.day)) == "UP"
    )
    up_pnl_share_pct = (up_pnl / total_pnl * 100.0) if total_pnl != 0 else None

    unknown_n = sum(1 for t in trades if t.exit_reason == "UNKNOWN")
    unknown_exit_share_pct = (unknown_n / overall["trades"] * 100.0) if overall["trades"] else 0.0

    regime_day_counts = {"UP": 0, "FLAT": 0, "DOWN": 0, "?": 0}
    for day in days:
        regime_day_counts[classify_regime(daily_changes.get(day))] += 1

    regime_pnls: Dict[str, float] = {}
    regime_trade_counts: Dict[str, int] = {}
    for regime in ("UP", "FLAT", "DOWN"):
        regime_trades = [t for t in trades if classify_regime(daily_changes.get(t.day)) == regime]
        regime_pnls[regime] = sum(t.realized_pnl for t in regime_trades)
        regime_trade_counts[regime] = len(regime_trades)

    return {
        "total_trades": overall["trades"],
        "winrate": overall["winrate"],
        "pnl": total_pnl,
        "profit_factor": overall["profit_factor"],
        "max_drawdown": max_dd,
        "up_pnl_share_pct": up_pnl_share_pct,
        "unknown_exit_share_pct": unknown_exit_share_pct,
        "regime_day_counts": regime_day_counts,
        "regime_pnls": regime_pnls,
        "regime_trade_counts": regime_trade_counts,
        "soak_days": len(days),
    }


def build_checklist(
    headline: Dict[str, Any], since: datetime, until: datetime,
) -> List[Dict[str, str]]:
    """docs/MAINNET_PLAN.md §2 madde 3 — B halkası soak ölçütleri.
    Her satır PASS/FAIL/N/A — GENEL HÜKÜM YOK, insan karar verir."""
    items: List[Dict[str, str]] = []

    soak_days = headline["soak_days"]
    items.append({
        "name": f"Soak süresi >= {SOAK_MIN_DAYS} gün",
        "status": "PASS" if soak_days >= SOAK_MIN_DAYS else "FAIL",
        "detail": f"{soak_days} gün ({since.date()} -> {until.date()})",
    })

    down_days = headline["regime_day_counts"]["DOWN"]
    items.append({
        "name": "En az 1 düşüş (DOWN) günü",
        "status": "PASS" if down_days >= 1 else "FAIL",
        "detail": f"{down_days} DOWN gün",
    })

    unknown_pct = headline["unknown_exit_share_pct"]
    items.append({
        "name": f"exit_reason=UNKNOWN oranı < %{SOAK_UNKNOWN_MAX_PCT:g}",
        "status": "PASS" if unknown_pct < SOAK_UNKNOWN_MAX_PCT else "FAIL",
        "detail": f"%{unknown_pct:.1f}",
    })

    for regime in ("UP", "FLAT", "DOWN"):
        n = headline["regime_trade_counts"][regime]
        pnl = headline["regime_pnls"][regime]
        if n == 0:
            status, detail = "N/A", "bu rejimde işlem yok"
        else:
            status = "PASS" if pnl >= 0 else "FAIL"
            detail = f"{pnl:+.2f} ({n} işlem)"
        items.append({
            "name": f"{regime} rejim PnL >= 0 (başabaş)",
            "status": status,
            "detail": detail,
        })

    return items


def build_report(
    trades: List[ClosedTrade],
    daily_changes: Dict[str, float],
    since: datetime,
    until: datetime,
    days: List[str],
    notes: List[str],
) -> Dict[str, Any]:
    headline = build_headline(trades, daily_changes, days)
    return {
        "meta": {
            "since": since.strftime("%Y-%m-%d %H:%M"),
            "until": until.strftime("%Y-%m-%d %H:%M"),
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S") + "Z",
        },
        "regime_direction": build_regime_direction_table(trades, daily_changes, days),
        "exit_reason_direction": build_exit_reason_direction_table(trades),
        "symbol": build_symbol_table(trades),
        "daily": build_daily_table(trades, daily_changes, days),
        "headline": headline,
        "checklist": build_checklist(headline, since, until),
        "notes": notes,
    }


# --------------------------------------------------------------------------
# Görüntüleme
# --------------------------------------------------------------------------

def _fmt_pf(pf: float) -> str:
    if pf == float("inf"):
        return "inf"
    return f"{pf:.2f}"


def _fmt_pct_or_q(pct: Optional[float]) -> str:
    return f"{pct:.2f}" if pct is not None else "?"


def _render_table(headers: List[str], rows: List[List[str]]) -> str:
    if not rows:
        widths = [len(h) for h in headers]
        header_line = " | ".join(h.ljust(w) for h, w in zip(headers, widths))
        sep = "-+-".join("-" * w for w in widths)
        return f"{header_line}\n{sep}\n(kayıt yok)"
    widths = [
        max(len(headers[i]), max(len(row[i]) for row in rows))
        for i in range(len(headers))
    ]
    header_line = " | ".join(h.ljust(w) for h, w in zip(headers, widths))
    sep = "-+-".join("-" * w for w in widths)
    body = "\n".join(
        " | ".join(v.ljust(w) for v, w in zip(row, widths)) for row in rows
    )
    return f"{header_line}\n{sep}\n{body}"


def _render_md_table(headers: List[str], rows: List[List[str]]) -> str:
    header_line = "| " + " | ".join(headers) + " |"
    sep_line = "|" + "|".join(["---"] * len(headers)) + "|"
    if not rows:
        return f"{header_line}\n{sep_line}\n| _kayıt yok_ |" + " |" * (len(headers) - 1)
    body = "\n".join("| " + " | ".join(row) + " |" for row in rows)
    return f"{header_line}\n{sep_line}\n{body}"


def _regime_direction_rows(report: Dict[str, Any]) -> List[List[str]]:
    return [
        [r["regime"], r["direction"], str(r["trades"]), f"{r['winrate']:.1f}",
         f"{r['pnl']:.2f}", _fmt_pf(r["profit_factor"]), f"{r['avg_win']:.2f}", f"{r['avg_loss']:.2f}"]
        for r in report["regime_direction"]
    ]


def _exit_reason_rows(report: Dict[str, Any]) -> List[List[str]]:
    return [
        [r["exit_reason"], r["direction"], str(r["trades"]), f"{r['winrate']:.1f}",
         f"{r['pnl']:.2f}", _fmt_pf(r["profit_factor"]), f"{r['avg_win']:.2f}", f"{r['avg_loss']:.2f}"]
        for r in report["exit_reason_direction"]
    ]


def _symbol_rows(report: Dict[str, Any]) -> List[List[str]]:
    return [
        [r["symbol"], str(r["trades"]), f"{r['winrate']:.1f}",
         f"{r['pnl']:.2f}", _fmt_pf(r["profit_factor"]), f"{r['avg_win']:.2f}", f"{r['avg_loss']:.2f}"]
        for r in report["symbol"]
    ]


def _daily_rows(report: Dict[str, Any]) -> List[List[str]]:
    return [
        [r["day"], r["regime"], _fmt_pct_or_q(r["btc_pct"]), str(r["trades"]),
         f"{r['pnl']:.2f}", f"{r['cum_pnl']:.2f}"]
        for r in report["daily"]
    ]


_TABLE1_HEADERS = ["Rejim", "Yön", "İşlem", "WR%", "PnL", "PF", "Ort.Kazanç", "Ort.Kayıp"]
_TABLE2_HEADERS = ["ÇıkışNedeni", "Yön", "İşlem", "WR%", "PnL", "PF", "Ort.Kazanç", "Ort.Kayıp"]
_TABLE3_HEADERS = ["Sembol", "İşlem", "WR%", "PnL", "PF", "Ort.Kazanç", "Ort.Kayıp"]
_TABLE4_HEADERS = ["Gün", "Rejim", "BTC%", "İşlem", "GünPnL", "KümülatifPnL"]


def render_text(report: Dict[str, Any]) -> str:
    meta = report["meta"]
    h = report["headline"]
    lines: List[str] = []
    lines.append("=" * 78)
    lines.append(f"CANLI DEFTER REJİM RAPORU  ({meta['since']} -> {meta['until']} UTC)")
    lines.append(f"Üretildi: {meta['generated_at']}")
    lines.append("=" * 78)

    if report["notes"]:
        lines.append("")
        lines.append("NOTLAR:")
        for note in report["notes"]:
            lines.append(f"  - {note}")

    lines.append("")
    lines.append("1) REJİM x YÖN")
    lines.append(_render_table(_TABLE1_HEADERS, _regime_direction_rows(report)))

    lines.append("")
    lines.append("2) ÇIKIŞ NEDENİ x YÖN")
    lines.append(_render_table(_TABLE2_HEADERS, _exit_reason_rows(report)))

    lines.append("")
    lines.append("3) SEMBOL BAZINDA")
    lines.append(_render_table(_TABLE3_HEADERS, _symbol_rows(report)))

    lines.append("")
    lines.append("4) GÜNLÜK (BTC % ve kümülatif PnL)")
    lines.append(_render_table(_TABLE4_HEADERS, _daily_rows(report)))

    lines.append("")
    lines.append("5) ÖZET")
    lines.append(f"  Toplam işlem       : {h['total_trades']}")
    lines.append(f"  Kazanma oranı      : {h['winrate']:.1f}%")
    lines.append(f"  Toplam PnL         : {h['pnl']:.2f}")
    lines.append(f"  Profit factor      : {_fmt_pf(h['profit_factor'])}")
    lines.append(f"  Maks. düşüş (DD)   : {h['max_drawdown']:.2f}")
    up_share = h["up_pnl_share_pct"]
    up_share_txt = f"%{up_share:.1f}" if up_share is not None else "N/A (toplam PnL=0)"
    lines.append(f"  UP günlerden PnL payı: {up_share_txt}")
    lines.append(f"  exit_reason=UNKNOWN oranı: %{h['unknown_exit_share_pct']:.1f}")
    rdc = h["regime_day_counts"]
    lines.append(
        f"  Rejim gün sayısı   : UP={rdc['UP']} FLAT={rdc['FLAT']} DOWN={rdc['DOWN']} ?={rdc['?']}"
    )

    lines.append("")
    lines.append("6) SOAK KONTROL LİSTESİ (docs/MAINNET_PLAN.md §2 madde 3)")
    for item in report["checklist"]:
        lines.append(f"  [{item['status']}] {item['name']}: {item['detail']}")
    lines.append("")
    lines.append("(Bu rapor hüküm vermez — terfi kararını insan onayı verir; MAINNET_PLAN §2 madde 5.)")

    return "\n".join(lines)


def render_md(report: Dict[str, Any]) -> str:
    meta = report["meta"]
    h = report["headline"]
    lines: List[str] = []
    lines.append(f"# Canlı defter rejim raporu ({meta['since']} → {meta['until']} UTC)")
    lines.append("")
    lines.append(f"_Üretildi: {meta['generated_at']}_")

    if report["notes"]:
        lines.append("")
        lines.append("**Notlar:**")
        for note in report["notes"]:
            lines.append(f"- {note}")

    lines.append("")
    lines.append("## 1) Rejim × yön")
    lines.append(_render_md_table(_TABLE1_HEADERS, _regime_direction_rows(report)))

    lines.append("")
    lines.append("## 2) Çıkış nedeni × yön")
    lines.append(_render_md_table(_TABLE2_HEADERS, _exit_reason_rows(report)))

    lines.append("")
    lines.append("## 3) Sembol bazında")
    lines.append(_render_md_table(_TABLE3_HEADERS, _symbol_rows(report)))

    lines.append("")
    lines.append("## 4) Günlük (BTC % ve kümülatif PnL)")
    lines.append(_render_md_table(_TABLE4_HEADERS, _daily_rows(report)))

    lines.append("")
    lines.append("## 5) Özet")
    up_share = h["up_pnl_share_pct"]
    up_share_txt = f"%{up_share:.1f}" if up_share is not None else "N/A (toplam PnL=0)"
    rdc = h["regime_day_counts"]
    lines.append(f"- Toplam işlem: **{h['total_trades']}**")
    lines.append(f"- Kazanma oranı: **{h['winrate']:.1f}%**")
    lines.append(f"- Toplam PnL: **{h['pnl']:.2f}**")
    lines.append(f"- Profit factor: **{_fmt_pf(h['profit_factor'])}**")
    lines.append(f"- Maks. düşüş (DD): **{h['max_drawdown']:.2f}**")
    lines.append(f"- UP günlerden PnL payı: **{up_share_txt}**")
    lines.append(f"- exit_reason=UNKNOWN oranı: **%{h['unknown_exit_share_pct']:.1f}**")
    lines.append(f"- Rejim gün sayısı: UP={rdc['UP']} FLAT={rdc['FLAT']} DOWN={rdc['DOWN']} ?={rdc['?']}")

    lines.append("")
    lines.append("## 6) Soak kontrol listesi (`docs/MAINNET_PLAN.md` §2 madde 3)")
    for item in report["checklist"]:
        lines.append(f"- **[{item['status']}]** {item['name']}: {item['detail']}")
    lines.append("")
    lines.append(
        "_Bu rapor hüküm vermez — terfi kararını insan onayı verir "
        "(MAINNET_PLAN §2 madde 5)._"
    )

    return "\n".join(lines)


def _sanitize_for_json(obj: Any) -> Any:
    if isinstance(obj, float):
        if math.isnan(obj):
            return None
        if math.isinf(obj):
            return "inf" if obj > 0 else "-inf"
        return round(obj, 6)
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_json(v) for v in obj]
    return obj


def render_json(report: Dict[str, Any]) -> str:
    return json.dumps(_sanitize_for_json(report), indent=2, ensure_ascii=False)


RENDERERS = {"text": render_text, "md": render_md, "json": render_json}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Canlı defteri (scalp_trades) rejime böler; soak (B halkası) "
            "kontrol listesini yazdırır. docs/MAINNET_PLAN.md §2.3."
        ),
    )
    parser.add_argument("--db", default=DEFAULT_DB, help=f"sqlite veritabanı yolu (varsayılan: {DEFAULT_DB})")
    parser.add_argument("--since", default=None, help="UTC 'YYYY-MM-DD HH:MM' (varsayılan: 7 gün önce)")
    parser.add_argument("--until", default=None, help="UTC 'YYYY-MM-DD HH:MM' (varsayılan: şimdi)")
    parser.add_argument(
        "--btc-klines-json", default=None,
        help="çevrimdışı BTCUSDT 1d kline JSON'u (Binance kline dizisi biçimi); verilmezse ağdan çekilir",
    )
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL, help=f"rejim referans sembolü (varsayılan: {DEFAULT_SYMBOL})")
    parser.add_argument(
        "--strategy", default=None,
        help="yalnız bu strateji etiketli işlemler (ör. 'AP' = AlgoPro takipçi halkası, D20)",
    )
    parser.add_argument("--format", choices=["text", "md", "json"], default="text")
    parser.add_argument("--out", default=None, help="çıktı dosyası (varsayılan: stdout)")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        until = parse_dt(args.until) if args.until else now
        since = parse_dt(args.since) if args.since else (until - timedelta(days=7))
    except ValueError as exc:
        print(f"Hata: {exc}", file=sys.stderr)
        return 2

    if since > until:
        print("Hata: --since, --until'dan sonra olamaz.", file=sys.stderr)
        return 2

    if not Path(args.db).exists():
        print(f"Hata: veritabanı bulunamadı: {args.db}", file=sys.stderr)
        return 2

    notes: List[str] = []
    trades, skipped = load_closed_trades(
        args.db, since, until, strategy=args.strategy
    )
    if args.strategy:
        notes.append(f"Rapor yalnız strategy='{args.strategy.strip().upper()}' işlemlerini kapsıyor.")
    if skipped:
        notes.append(f"{skipped} CLOSED kayıt closed_at ayrıştırılamadığı için atlandı.")

    days = iter_days(since, until)
    start_date, end_date = days[0], days[-1]

    if args.btc_klines_json:
        try:
            raw_klines = load_offline_klines(args.btc_klines_json)
        except (OSError, json.JSONDecodeError) as exc:
            notes.append(f"Çevrimdışı kline dosyası okunamadı ({exc}); günler '?' rejiminde kalacak.")
            raw_klines = []
    else:
        try:
            raw_klines = fetch_binance_daily_klines(start_date, end_date, symbol=args.symbol)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            notes.append(f"BTC klines alınamadı ({exc}); günler '?' rejiminde kalacak.")
            raw_klines = []

    daily_changes = klines_to_daily_changes(raw_klines)
    missing_days = [d for d in days if d not in daily_changes]
    if missing_days and raw_klines:
        notes.append(
            f"{len(missing_days)} gün için kline verisi yok, rejim '?' işaretlendi: "
            + ", ".join(missing_days)
        )

    if not trades:
        notes.append("Bu tarih aralığında kapanmış (CLOSED) işlem yok.")

    report = build_report(trades, daily_changes, since, until, days, notes)
    output = RENDERERS[args.format](report)

    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
