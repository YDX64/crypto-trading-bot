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
    python3 scripts/ledger_report.py --since "2026-08-21" --forensics   # D21 etiket × sonuç
    python3 scripts/ledger_report.py --since "2026-08-21" --ai          # D23 AI gölge raporu
    python3 scripts/ledger_report.py --since "2026-08-24" --counterfactual  # D27/B karşı-olgu
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
import os
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
# "TRAIL_MARKET"/"BE_MARKET" (D22): koruyucu stop borsaya konulamadı (-2021)
# ve `position_manager._emergency_close` pozisyonu reduce-only MARKET ile
# kapattı. TRAIL AİLESİNDENDİRLER ama AYRI SAYILIRLAR — sayıları artıyorsa
# stop kararı piyasa hızının gerisinde kalıyordur.
# "REAPER" (D27/A1): 8 saatlik yaş kesmesi (D4, `SCALPER_MAX_HOLD_HOURS`).
# Bugüne kadar "SL" diye etiketleniyordu; 2026-08-24 kök-neden analizinde
# ölçüldü: 43 kesme = −172.3 USDT = brüt zararın %27'si ve 12'si ARTIDA
# kesilmişti. **GERİYE DÖNÜK VERİ DÜZELTMESİ YOKTUR** — 2026-08-24 öncesinde
# kapanan yaş kesmeleri defterde hâlâ "SL"dir; rapor bunu REAPER_SPLIT_NOTE
# ile söyler.
EXIT_REASON_ORDER = [
    "SL", "REAPER", "TP_LADDER", "TRAIL", "TRAIL_MARKET", "BE_MARKET",
    "MANUAL", "UNKNOWN",
]
REAPER_SPLIT_NOTE = (
    "REAPER ayrımı 2026-08-24'ten itibaren geçerlidir (D27/A1): daha eski yaş "
    "kesmeleri (D4) defterde hâlâ 'SL' olarak durur — geriye dönük veri "
    "düzeltmesi YAPILMADI."
)
EXIT_REASON_FAMILY = {
    "SL": "SL",
    "REAPER": "REAPER",
    "TP_LADDER": "TP_LADDER",
    "TRAIL": "TRAIL",
    "TRAIL_MARKET": "TRAIL",
    "BE_MARKET": "TRAIL",
    "MANUAL": "MANUAL",
    "RISK_EVENT": "MANUAL",
    "TV_EVENT": "MANUAL",
}


def exit_reason_family(reason: str) -> str:
    """Çıkış nedeninin AİLESİ (rapor gruplaması). Bilinmeyen = kendisi."""
    return EXIT_REASON_FAMILY.get((reason or "").strip().upper(), reason or "UNKNOWN")

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
# İşlem adli kaydı (D21) — etiket × sonuç
# --------------------------------------------------------------------------

#: `src/strategies/scalper/forensics.py::TAG_LABELS` ile AYNI metinler.
#: Bu script bilinçli olarak yalnız stdlib kullanır (sunucuda `.venv`
#: olmadan da koşabilmeli), bu yüzden eşleme burada TEKRARLANIR. Yeni bir
#: etiket eklenirse iki taraf da güncellenmelidir — `tests/test_ledger_report.py`
#: bu eşitliği DOĞRULAR, yani sessizce ayrışamazlar.
FORENSICS_TAG_LABELS: Dict[str, str] = {
    "counter_drift_long": "lider düşerken LONG açıldı",
    "relief_rally_short": "lider yükselirken SHORT açıldı",
    "late_entry_after_run": "çok günlük koşunun ARDINDAN aynı yöne girildi",
    "tv_single_family": "TV sağlaması aynı aileden iki kaynakla doldu",
    "stale_signal": "sinyal ile dolum arasında uzun gecikme",
    "gate_bypassed": "kapı açık ama ETKİN DEĞİL (fail-open) iken girildi",
    "fee_dominated": "ücretler kârın yarısından fazlasını yedi",
    "mfe_giveback": "kâr TP1 hedefini gördü ama zararla kapandı",
    "noise_stop": "stop sonrası fiyat pencerede girişe geri döndü (gürültü)",
}

UNTAGGED_KEY = "_etiketsiz_"


def load_forensics_rows(
    db_path: str,
    since: datetime,
    until: datetime,
    strategy: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Kapanmış işlemlerin (etiketler, pnl) çiftleri + uyarı notları.

    `forensics` sütunu YOKSA (eski DB, migration çalışmamış) boş liste ve
    açıklayıcı bir not döner — rapor ÇÖKMEZ.
    """
    wanted_strategy = (strategy or "").strip().upper() or None
    notes: List[str] = []
    con = sqlite3.connect(db_path)
    try:
        con.row_factory = sqlite3.Row
        try:
            cur = con.execute(
                "SELECT id, strategy, realized_pnl, closed_at, forensics "
                "FROM scalp_trades WHERE status = 'CLOSED'"
            )
        except sqlite3.OperationalError as exc:
            notes.append(
                f"Adli kayıt sütunu okunamadı ({exc}); bu bölüm boş — DB "
                f"migration'ı (scalp_trades.forensics) henüz çalışmamış olabilir."
            )
            return [], notes
        raw_rows = cur.fetchall()
    finally:
        con.close()

    rows: List[Dict[str, Any]] = []
    without = 0
    for row in raw_rows:
        if wanted_strategy is not None:
            if str(row["strategy"] or "").strip().upper() != wanted_strategy:
                continue
        closed_dt = _parse_db_timestamp(row["closed_at"])
        if closed_dt is None or not (since <= closed_dt <= until):
            continue
        document: Dict[str, Any] = {}
        if row["forensics"]:
            try:
                parsed = json.loads(row["forensics"])
                if isinstance(parsed, dict):
                    document = parsed
            except (TypeError, ValueError):
                document = {}
        if not document:
            without += 1
        rows.append({
            "id": row["id"],
            "pnl": float(row["realized_pnl"] or 0.0),
            "tags": [t for t in (document.get("verdict") or []) if t],
        })

    if without:
        notes.append(
            f"{without} kapanmış işlemin adli kaydı YOK (D21 öncesi ya da "
            f"SCALPER_FORENSICS_ENABLED=false) — bunlar '{UNTAGGED_KEY}' "
            f"satırına düşer; 'etiketsiz' ≠ 'temiz'."
        )
    return rows, notes


def build_forensics_table(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Etiket × sonuç tablosu (en zararlıdan en kârlıya sıralı).

    Bir işlem birden çok etiket taşıyabilir; satırların işlem toplamı genel
    işlem sayısını AŞABİLİR — bu kasıtlıdır.
    """
    buckets: Dict[str, Dict[str, float]] = {}
    for row in rows:
        pnl = float(row.get("pnl") or 0.0)
        tags = []
        for tag in row.get("tags") or []:
            if tag not in tags:
                tags.append(tag)
        for name in tags or [UNTAGGED_KEY]:
            bucket = buckets.setdefault(
                name,
                {"trades": 0.0, "wins": 0.0, "pnl": 0.0,
                 "gross_win": 0.0, "gross_loss": 0.0},
            )
            bucket["trades"] += 1
            bucket["pnl"] += pnl
            if pnl > 0:
                bucket["wins"] += 1
                bucket["gross_win"] += pnl
            elif pnl < 0:
                bucket["gross_loss"] += abs(pnl)

    table: List[Dict[str, Any]] = []
    for name, bucket in buckets.items():
        trades = int(bucket["trades"])
        wins = int(bucket["wins"])
        gross_loss = bucket["gross_loss"]
        table.append({
            "tag": name,
            "label": FORENSICS_TAG_LABELS.get(name, ""),
            "trades": trades,
            "wins": wins,
            "winrate": (wins / trades * 100.0) if trades else 0.0,
            "pnl": bucket["pnl"],
            "avg_pnl": (bucket["pnl"] / trades) if trades else 0.0,
            "profit_factor": (
                (bucket["gross_win"] / gross_loss) if gross_loss > 0
                else (float("inf") if bucket["gross_win"] > 0 else 0.0)
            ),
        })
    table.sort(key=lambda item: (item["pnl"], -item["trades"]))
    return table


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
# AI karar katmanı gölge raporu (D23)
# --------------------------------------------------------------------------
# Veri kaynağı: `scalp_trades.forensics` JSON belgesindeki `document["ai"]`
# bloğu (`src/strategies/scalper/tracker.py::attach_ai` yazar; MIGRATION YOK).
# Bu bölüm YALNIZ GÖZLEMDİR: gölge modda hiçbir karar UYGULANMAZ, dolayısıyla
# "engellenen" küme aslında GİRİLMİŞ ve kapanmış işlemlerdir — "AI dinlenseydi
# ne olurdu" sorusunun gerçekleşmiş (ama EKSİK, bkz. E8.6) cevabıdır.

#: `src/strategies/scalper/ai_gate.py::AXES` ile AYNI adlar ve sıra.
#: FORENSICS_TAG_LABELS ile aynı gerekçe: bu script bilinçli olarak yalnız
#: stdlib kullanır (sunucuda `.venv` olmadan da koşabilmeli), bu yüzden liste
#: TEKRARLANIR. Ayrışma `tests/test_ledger_report.py` ile yakalanır.
AI_AXES: List[str] = [
    "regime_fit",
    "tv_confluence_depth",
    "stop_sanity",
    "crowding",
    "structure_conflict",
]

#: `ai_gate.py`'deki kayıt durumları. Sıfır sayılı olanlar da tabloda görünür:
#: "hiç ai_malformed yok" ile "bu alana hiç bakmadık" karıştırılmamalıdır.
AI_STATUS_ORDER: List[str] = [
    "ok",
    "ai_unavailable",
    "ai_malformed",
    "ai_stale",
    "ai_budget_exhausted",
    "ai_skipped",
    "ai_runaway",
]

#: "AI bloğu yok" = "AI izin verdi" DEĞİLDİR — raporun en kolay yanlış okunan
#: yeri burasıdır, bu yüzden not ZORUNLUDUR.
AI_NO_BLOCK_NOTE = (
    "AI bloğu OLMAYAN kapanmış işlemler 'AI izin verdi' anlamına GELMEZ: "
    "katman kapalıyken, bütçe bittiğinde ya da karar dolumdan sonra "
    "yetişmediğinde blok hiç yazılmaz. Kapsama satırındaki 'yok' sayısı bir "
    "örneklem eksiğidir, bir onay değil."
)

#: docs/EXPERIMENTS.md E8.6 — gölge ölçümünün yapısal alt sınırı.
AI_CAPACITY_NOTE = (
    "E8.6 UYARISI: Gölgede kapasite BOŞALMAZ — bir girişi engellemenin "
    "faydasının büyük kısmı, boşalan işgal penceresine giren YENİ işlemlerden "
    "gelir (docs/EXPERIMENTS.md E8.6: 11 işlem / +1217.4). Bu rapor faydanın "
    "EN KÜÇÜK parçasını ölçer; 'engellenenlerin PnL'i' bir ALT SINIRDIR."
)


def _ai_float(value: Any) -> Optional[float]:
    """Sayıya çevir; sayı değilse ya da NaN/sonsuzsa None (rapor uydurmaz)."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(out) or math.isinf(out):
        return None
    return out


def load_ai_rows(
    db_path: str,
    since: datetime,
    until: datetime,
    strategy: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Kapanmış işlemlerin (AI kaydı, pnl) çiftleri + uyarı notları.

    `forensics` sütunu YOKSA (eski DB) ya da JSON bozuksa eksik/boş veriyle
    döner — rapor ÇÖKMEZ, eksiklik nota yazılır (`load_forensics_rows` deseni).

    `verdict` YALNIZ `status == "ok"` kayıtlarında anlamlıdır; fail-open
    kayıtlarında (ai_unavailable/ai_malformed/...) alan yoktur ve OKUNMAZ.
    """
    wanted_strategy = (strategy or "").strip().upper() or None
    notes: List[str] = [AI_CAPACITY_NOTE]
    con = sqlite3.connect(db_path)
    try:
        con.row_factory = sqlite3.Row
        try:
            cur = con.execute(
                "SELECT id, strategy, realized_pnl, closed_at, forensics "
                "FROM scalp_trades WHERE status = 'CLOSED'"
            )
        except sqlite3.OperationalError as exc:
            notes.append(
                f"AI kararları okunamadı ({exc}); bu bölüm boş — "
                f"scalp_trades.forensics sütunu yok (D21 öncesi DB)."
            )
            return [], notes
        raw_rows = cur.fetchall()
    finally:
        con.close()

    rows: List[Dict[str, Any]] = []
    without = 0
    broken_json = 0
    broken_block = 0
    for row in raw_rows:
        if wanted_strategy is not None:
            if str(row["strategy"] or "").strip().upper() != wanted_strategy:
                continue
        closed_dt = _parse_db_timestamp(row["closed_at"])
        if closed_dt is None or not (since <= closed_dt <= until):
            continue

        document: Dict[str, Any] = {}
        if row["forensics"]:
            try:
                parsed = json.loads(row["forensics"])
                if isinstance(parsed, dict):
                    document = parsed
                else:
                    broken_json += 1
            except (TypeError, ValueError):
                broken_json += 1

        record = document.get("ai")
        entry: Dict[str, Any] = {
            "id": row["id"],
            "pnl": float(row["realized_pnl"] or 0.0),
            "has_ai": False,
            "status": None,
            "verdict": None,
            "confidence": None,
            "axes": {},
            "pattern_ids": [],
            "latency_ms": None,
            "schema_version": None,
        }
        if record is not None and not isinstance(record, dict):
            broken_block += 1
            record = None
        if isinstance(record, dict):
            entry["has_ai"] = True
            entry["status"] = str(record.get("status") or "").strip().lower() or "?"
            entry["schema_version"] = record.get("schema_version")
            entry["latency_ms"] = _ai_float(record.get("latency_ms"))
            # Fail-open kayıtlarında verdict/axes/pattern_ids YOKTUR; olsa
            # bile okunmaz — "ok olmayan bir karar" bir karar değildir.
            if entry["status"] == "ok":
                side = str(record.get("verdict") or "").strip().lower()
                entry["verdict"] = side if side in ("allow", "deny") else None
                entry["confidence"] = _ai_float(record.get("confidence"))
                raw_axes = record.get("axes")
                if isinstance(raw_axes, dict):
                    axes: Dict[str, float] = {}
                    for name, value in raw_axes.items():
                        num = _ai_float(value)
                        if num is not None:
                            axes[str(name)] = num
                    entry["axes"] = axes
                raw_patterns = record.get("pattern_ids")
                if isinstance(raw_patterns, list):
                    seen: List[str] = []
                    for name in raw_patterns:
                        text = str(name or "").strip()
                        if text and text not in seen:
                            seen.append(text)
                    entry["pattern_ids"] = seen
        else:
            without += 1
        rows.append(entry)

    if without:
        notes.append(
            f"{without} kapanmış işlemde AI kaydı YOK (katman kapalı, bütçe "
            f"bitmiş ya da karar yetişmemiş). " + AI_NO_BLOCK_NOTE
        )
    else:
        notes.append(AI_NO_BLOCK_NOTE)
    if broken_json:
        notes.append(
            f"{broken_json} işlemin forensics JSON'u ayrıştırılamadı; o "
            f"satırlar 'AI kaydı yok' sayıldı."
        )
    if broken_block:
        notes.append(
            f"{broken_block} işlemin 'ai' bloğu sözlük değil (bozuk kayıt); "
            f"o satırlar 'AI kaydı yok' sayıldı."
        )
    return rows, notes


def _mean_ci95(
    values: List[float],
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """(ortalama, GA-alt, GA-üst). n<2 ise GA hesaplanamaz -> (ort, None, None).

    Örneklem standart sapması (n-1) ile normal yaklaşım:
    ort ± 1.96 * sd / sqrt(n). Kenar ince olduğu için GA'sız bir ortalama
    karar dayanağı sayılmaz.
    """
    n = len(values)
    if n == 0:
        return None, None, None
    mean = sum(values) / n
    if n < 2:
        return mean, None, None
    variance = sum((v - mean) ** 2 for v in values) / (n - 1)
    half = 1.96 * math.sqrt(variance) / math.sqrt(n)
    return mean, mean - half, mean + half


def _pearson(xs: List[float], ys: List[float]) -> Optional[float]:
    """Pearson r — elle hesaplanır (numpy/scipy YOK; script saf stdlib kalmalı).

    Sabit varyansta (payda 0) korelasyon TANIMSIZDIR -> None. 0.0 döndürmek
    "ilişki yok" der; oysa doğru cevap "ölçülemez"dir.
    """
    n = len(xs)
    if n != len(ys) or n < 2:
        return None
    if min(xs) == max(xs) or min(ys) == max(ys):
        return None  # sabit eksen ya da sabit sonuç: tanımsız
    mx = sum(xs) / n
    my = sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    # Kayan nokta artığı: hepsi aynı olan bir eksende bile kareler toplamı
    # TAM 0 çıkmayabilir (altı tane 0.4'ün ortalaması 0.4 değildir) ve
    # 1e-32/1e-32 bölmesi UYDURMA bir r üretir. Eşik ölçeğe GÖRELİdir;
    # eksen değerleri 4 haneye yuvarlandığı için gerçek varyans bunun
    # milyarlarca katıdır, meşru sinyal elenmez.
    x_scale = max(abs(v) for v in xs) or 1.0
    y_scale = max(abs(v) for v in ys) or 1.0
    if sxx <= n * (x_scale * 1e-9) ** 2 or syy <= n * (y_scale * 1e-9) ** 2:
        return None
    r = sxy / math.sqrt(sxx * syy)
    return max(-1.0, min(1.0, r))  # kayan nokta taşmasını kırp


def _percentile(values: List[float], pct: float) -> Optional[float]:
    """En yakın sıra (nearest-rank) yüzdelik. Boş listede None."""
    if not values:
        return None
    ordered = sorted(values)
    rank = int(math.ceil(pct / 100.0 * len(ordered)))
    idx = min(len(ordered) - 1, max(0, rank - 1))
    return float(ordered[idx])


def _ai_set_stats(pnls: List[float]) -> Dict[str, Any]:
    """Bir küme (deny/allow/taban) için GERÇEKLEŞMİŞ sonuç istatistikleri."""
    n = len(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    mean, low, high = _mean_ci95(pnls)
    return {
        "trades": n,
        "wins": len(wins),
        "winrate": (len(wins) / n * 100.0) if n else 0.0,
        "pnl": sum(pnls),
        "avg_pnl": mean if mean is not None else 0.0,
        "ci95_low": low,
        "ci95_high": high,
        "profit_factor": _profit_factor(sum(wins), abs(sum(losses))),
    }


def build_ai_report(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """D23 gölge raporu: kapsama + deny/allow kümeleri + eksen korelasyonu +
    maliyet + kalıp × sonuç.

    Taban (`baseline`) TÜM kapanmış işlemlerdir — AI kaydı olsun olmasın.
    İzin verilen kümenin PF'i tabandan yüksek değilse "AI eliyor" iddiası
    kendi verisiyle çürütülmüş demektir.
    """
    section_notes: List[str] = []

    status_counts: Dict[str, int] = {name: 0 for name in AI_STATUS_ORDER}
    with_ai = 0
    for row in rows:
        if not row.get("has_ai"):
            continue
        with_ai += 1
        status = str(row.get("status") or "?")
        status_counts[status] = status_counts.get(status, 0) + 1

    coverage = {
        "closed_trades": len(rows),
        "with_ai": with_ai,
        "without_ai": len(rows) - with_ai,
        "status_counts": status_counts,
    }

    deny = _ai_set_stats([r["pnl"] for r in rows if r.get("verdict") == "deny"])
    allow = _ai_set_stats([r["pnl"] for r in rows if r.get("verdict") == "allow"])
    baseline = _ai_set_stats([r["pnl"] for r in rows])

    for name, stats in (("Engellenen (deny)", deny), ("İzin verilen (allow)", allow)):
        if stats["trades"] < 2:
            section_notes.append(
                f"{name} kümesinde n={stats['trades']} — %95 güven aralığı "
                f"hesaplanamaz ('—'); bu küme kanıt taşımaz."
            )

    # Eksen × PnL: her eksen AYRI ölçülür. Tek bir "AI skoru" hangi eksenin
    # işe yaradığını gizler; D23'ün ölçüm tasarımı bunu yasaklar.
    axis_names: List[str] = list(AI_AXES)
    for row in rows:
        for name in row.get("axes") or {}:
            if name not in axis_names:
                axis_names.append(name)

    axes_table: List[Dict[str, Any]] = []
    for name in axis_names:
        xs: List[float] = []
        ys: List[float] = []
        for row in rows:
            value = (row.get("axes") or {}).get(name)
            if value is None:
                continue
            xs.append(float(value))
            ys.append(float(row["pnl"]))
        axes_table.append({"axis": name, "n": len(xs), "pearson_r": _pearson(xs, ys)})

    latencies = [
        float(r["latency_ms"]) for r in rows
        if r.get("has_ai") and r.get("latency_ms") is not None
    ]
    cost = {
        "decisions": len(latencies),
        "latency_p50_ms": _percentile(latencies, 50.0),
        "latency_p95_ms": _percentile(latencies, 95.0),
        # Token/ücret DB'de TAŞINMIYOR. Tahmin etmek uydurmaktır; alan
        # bilinçli olarak "ölçülemedi" der (CLAUDE.md yasak #6).
        "tokens": None,
        "cost_note": "ölçülemedi (DB token/ücret taşımıyor)",
    }

    buckets: Dict[str, List[float]] = {}
    for row in rows:
        for name in row.get("pattern_ids") or []:
            buckets.setdefault(name, []).append(float(row["pnl"]))
    patterns: List[Dict[str, Any]] = []
    for name, pnls in buckets.items():
        stats = _ai_set_stats(pnls)
        patterns.append({
            "pattern": name,
            "trades": stats["trades"],
            "winrate": stats["winrate"],
            "pnl": stats["pnl"],
            "avg_pnl": stats["avg_pnl"],
            "profit_factor": stats["profit_factor"],
        })
    # En zararlıdan en kârlıya — bir kalıbın "işe yarar" olduğu iddiası
    # tablonun üst satırlarında çürütülür.
    patterns.sort(key=lambda item: (item["pnl"], -item["trades"]))

    return {
        "coverage": coverage,
        "deny": deny,
        "allow": allow,
        "baseline": baseline,
        "axes": axes_table,
        "cost": cost,
        "patterns": patterns,
        "notes": section_notes,
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
        rows.append({
            "exit_reason": reason,
            # D22: TRAIL_MARKET/BE_MARKET kendi satırlarında sayılır ama
            # aileleri TRAIL'dir.
            "exit_family": exit_reason_family(reason),
            "direction": direction,
            **stats,
        })
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


def build_strategy_table(trades: List[ClosedTrade]) -> List[Dict[str, Any]]:
    """Tablo 3b: STRATEJİ bazında (D20b).

    Gömülü takipçi (`strategy="AP"`) scalper ile AYNI `scalp_trades`
    tablosuna yazar. İki defteri tek bir toplamda okumak, birinin kenarını
    diğerininkiyle gizler — bu tablo ayrımı raporun kendisinde yapar.
    `--strategy` ile filtrelenmiş bir raporda tek satır kalır (ve o zaten
    notlarda yazılıdır).
    """
    grouped: Dict[str, List[ClosedTrade]] = {}
    for t in trades:
        grouped.setdefault(str(t.strategy or "?").upper(), []).append(t)
    rows: List[Dict[str, Any]] = []
    for strategy in sorted(grouped.keys()):
        stats = _group_stats(grouped[strategy])
        rows.append({"strategy": strategy, **stats})
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
        "concentration": build_concentration(trades),
    }


def build_concentration(trades: List[ClosedTrade]) -> Dict[str, Any]:
    """D24/A4 — konsantrasyon: kâr tek sembolden/işlemden/günden mi geldi?

    "+832'nin %68'i 4 yükseliş gününden" tespitini 2026-08-21'de ELLE bir kez
    yapmıştık; bu blok onu her raporda otomatik üretir. `backtest.py`'deki
    `concentration_stats` ile AYNI tanımı kullanır (iki taraf ayrışmasın):
    pay YALNIZ toplam PnL POZİTİFKEN tanımlıdır — toplam sıfır/negatifken
    "kârın payı" sorusu anlamsızdır, o durumda pay None döner ama MUTLAK
    katkı yine raporlanır.

    Bu bir EŞİK değil BİLGİ satırıdır: `build_checklist` bunu okumaz.
    """
    empty: Dict[str, Any] = {
        "top_symbol": None, "top_symbol_pnl": 0.0, "top_symbol_pnl_share": None,
        "top_trade_pnl": 0.0, "top_trade_pnl_share": None, "top_trade_symbol": None,
        "top_day": None, "top_day_pnl": 0.0, "top_day_pnl_share": None,
        "distinct_symbols": 0, "distinct_days": 0,
    }
    if not trades:
        return empty

    total_pnl = sum(t.realized_pnl for t in trades)
    by_symbol: Dict[str, float] = {}
    by_day: Dict[str, float] = {}
    for t in trades:
        by_symbol[t.symbol] = by_symbol.get(t.symbol, 0.0) + t.realized_pnl
        by_day[t.day] = by_day.get(t.day, 0.0) + t.realized_pnl

    def _share(value: float) -> Optional[float]:
        if total_pnl <= 0.0 or value <= 0.0:
            return None
        return value / total_pnl * 100.0

    top_symbol, top_symbol_pnl = max(by_symbol.items(), key=lambda kv: kv[1])
    top_day, top_day_pnl = max(by_day.items(), key=lambda kv: kv[1])
    best_trade = max(trades, key=lambda t: t.realized_pnl)

    return {
        "top_symbol": top_symbol,
        "top_symbol_pnl": top_symbol_pnl,
        "top_symbol_pnl_share": _share(top_symbol_pnl),
        "top_trade_pnl": best_trade.realized_pnl,
        "top_trade_symbol": best_trade.symbol,
        "top_trade_pnl_share": _share(best_trade.realized_pnl),
        "top_day": top_day,
        "top_day_pnl": top_day_pnl,
        "top_day_pnl_share": _share(top_day_pnl),
        "distinct_symbols": len(by_symbol),
        "distinct_days": len(by_day),
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
    forensics: Optional[List[Dict[str, Any]]] = None,
    ai: Optional[Dict[str, Any]] = None,
    counterfactual: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    # `forensics`/`ai` YOKSA anahtar hiç eklenmez (renderer'lar `is not None`
    # ile bölümü açar). Tek gövde: iki ayrı sözlük tutmak, birine eklenen bir
    # alanın diğerinde unutulmasına davetiyedir (D21-R3, bulgu 5).
    headline = build_headline(trades, daily_changes, days)
    report: Dict[str, Any] = {
        "meta": {
            "since": since.strftime("%Y-%m-%d %H:%M"),
            "until": until.strftime("%Y-%m-%d %H:%M"),
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S") + "Z",
        },
        "regime_direction": build_regime_direction_table(trades, daily_changes, days),
        "exit_reason_direction": build_exit_reason_direction_table(trades),
        "symbol": build_symbol_table(trades),
        "strategy": build_strategy_table(trades),
        "daily": build_daily_table(trades, daily_changes, days),
        "headline": headline,
        "checklist": build_checklist(headline, since, until),
    }
    if forensics is not None:
        report["forensics"] = forensics
    if ai is not None:
        report["ai"] = ai
    if counterfactual is not None:
        report["counterfactual"] = counterfactual
    report["notes"] = notes
    # D27/A1: "REAPER ayrımı ... tarihinden itibaren" uyarısı JSON tüketicisine
    # de görünmeli — metin/md renderer'ları bunu tablo altına yazar, JSON'da
    # ayrı bir anahtar taşır (renderer'a bağımlı bir uyarı, uyarı değildir).
    report["exit_reason_note"] = REAPER_SPLIT_NOTE
    return report


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


def _strategy_rows(report: Dict[str, Any]) -> List[List[str]]:
    return [
        [r["strategy"], str(r["trades"]), f"{r['winrate']:.1f}",
         f"{r['pnl']:.2f}", _fmt_pf(r["profit_factor"]), f"{r['avg_win']:.2f}", f"{r['avg_loss']:.2f}"]
        for r in report.get("strategy") or []
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
_TABLE3B_HEADERS = ["Strateji", "İşlem", "WR%", "PnL", "PF", "Ort.Kazanç", "Ort.Kayıp"]
_TABLE4_HEADERS = ["Gün", "Rejim", "BTC%", "İşlem", "GünPnL", "KümülatifPnL"]
_TABLE5_HEADERS = ["Etiket", "İşlem", "WR%", "PnL", "Ort.PnL", "PF", "Anlamı"]


_TABLE6_HEADERS = ["Küme", "İşlem", "WR%", "PnL", "Ort.PnL", "%95 GA (ort.)", "PF"]
_TABLE7_HEADERS = ["Eksen", "n", "Pearson r (eksen ~ PnL)"]
_TABLE8_HEADERS = ["Kalıp", "İşlem", "WR%", "PnL", "Ort.PnL", "PF"]


def _fmt_ci(low: Optional[float], high: Optional[float]) -> str:
    """Güven aralığı metni; hesaplanamadıysa (n<2) '—' — sıfır DEĞİL."""
    if low is None or high is None:
        return "—"
    return f"{low:.2f} .. {high:.2f}"


def _fmt_r(value: Optional[float]) -> str:
    """Pearson r; tanımsızsa (sabit eksen) '—' — 0.0 DEĞİL."""
    return "—" if value is None else f"{value:+.3f}"


def _fmt_ms(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:.0f}"


def _ai_set_rows(report: Dict[str, Any]) -> List[List[str]]:
    """Tablo 6: engellenen / izin verilen / taban kümelerinin karşılaştırması."""
    ai = report.get("ai") or {}
    rows: List[List[str]] = []
    for label, key in (
        ("ENGELLENEN (deny)", "deny"),
        ("İZİN VERİLEN (allow)", "allow"),
        ("TABAN (tüm işlemler)", "baseline"),
    ):
        stats = ai.get(key) or {}
        if not stats:
            continue
        rows.append([
            label,
            str(stats["trades"]),
            f"{stats['winrate']:.1f}",
            f"{stats['pnl']:.2f}",
            f"{stats['avg_pnl']:.2f}",
            _fmt_ci(stats["ci95_low"], stats["ci95_high"]),
            _fmt_pf(stats["profit_factor"]),
        ])
    return rows


def _ai_axis_rows(report: Dict[str, Any]) -> List[List[str]]:
    ai = report.get("ai") or {}
    return [
        [item["axis"], str(item["n"]), _fmt_r(item["pearson_r"])]
        for item in ai.get("axes") or []
    ]


def _ai_pattern_rows(report: Dict[str, Any]) -> List[List[str]]:
    ai = report.get("ai") or {}
    return [
        [item["pattern"], str(item["trades"]), f"{item['winrate']:.1f}",
         f"{item['pnl']:.2f}", f"{item['avg_pnl']:.2f}", _fmt_pf(item["profit_factor"])]
        for item in ai.get("patterns") or []
    ]


def _ai_coverage_lines(ai: Dict[str, Any]) -> List[str]:
    """Kapsama + maliyet satırları (text ve md aynı sayıları yazsın diye tek yer)."""
    cov = ai["coverage"]
    cost = ai["cost"]
    status_txt = ", ".join(
        f"{name}={count}" for name, count in cov["status_counts"].items()
    ) or "(kayıt yok)"
    return [
        f"Kapsama: {cov['closed_trades']} kapanmış işlem — AI kaydı OLAN "
        f"{cov['with_ai']}, OLMAYAN {cov['without_ai']}.",
        f"Durum kırılımı: {status_txt}",
        f"Gecikme (ms): p50={_fmt_ms(cost['latency_p50_ms'])} "
        f"p95={_fmt_ms(cost['latency_p95_ms'])} "
        f"(ölçülen karar: {cost['decisions']})",
        f"Token/ücret maliyeti: {cost['cost_note']}",
    ]


#: D27/B karşı-olgu tablosu. Ad DİKKATLE seçildi: `_TABLE8_HEADERS` ZATEN
#: AI kalıp tablosuna aitti (satır ~1215) ve üzerine yazmak `_ai_pattern_rows`
#: 6 sütunluk satırlarını 11 sütunluk başlıkla eşleştirip `IndexError` verir.
_TABLE9_HEADERS = [
    "RetGerekçesi", "n", "Ölçülen", "TP1", "STOP", "Açık", "Veriyok",
    "Ort.ROI%", "PF", "%95 GA", "Katlanan",
]

_COUNTERFACTUAL_NOTE = (
    "KARŞI-OLGU MODELİ: yalnız TP1 ya da İLK STOP modellenir; TP2, chandelier "
    "trailing, break-even çekme, 8 saatlik reaper (D4), komisyon ve kayma "
    "MODELLENMEZ. Aynı mumda ikisi de vurursa STOP kazanır (karamsar). "
    "'Veriyok' satırları ortalama/PF hesabına GİRMEZ. 'Katlanan', dedup "
    "penceresinde tek satıra indirgenmiş özdeş retlerin toplam ağırlığıdır."
)


def load_counterfactual_rows(
    since: datetime,
    until: datetime,
    *,
    log_dir: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """`logs/trades.jsonl`'den karşı-olgu satırlarını oku (D27/B).

    Kaynak DB DEĞİLDİR: reddedilen niyetlerin defterde satırı yoktur, izleri
    JSONL'dedir. Dosya yoksa boş liste + açıklayıcı not döner — rapor ÇÖKMEZ
    (`load_forensics_rows` deseni).
    """
    notes: List[str] = []
    if log_dir:
        os.environ["TRADINGBOT_LOG_DIR"] = str(log_dir)
    try:
        from src.strategies.scalper import forensics_log
    except Exception as exc:  # pragma: no cover - import yolu bozuksa
        return [], [f"Karşı-olgu satırları okunamadı (import): {exc}"]

    since_iso = since.replace(tzinfo=timezone.utc).isoformat()
    until_iso = until.replace(tzinfo=timezone.utc).isoformat()
    try:
        rows = forensics_log.read_events("counterfactual", since_iso=since_iso)
    except Exception as exc:
        return [], [f"Karşı-olgu satırları okunamadı: {exc}"]

    windowed = [
        row for row in rows
        if not isinstance(row.get("ts"), str) or row["ts"] <= until_iso
    ]
    if not windowed:
        notes.append(
            "Karşı-olgu defterinde bu pencerede satır yok "
            f"({forensics_log.log_path()}). Defter 2026-08-24'te (D27/B) "
            "açıldı ve yalnız REDDEDİLEN niyetleri kaydeder."
        )
    return windowed, notes


def build_counterfactual_report(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Ret gerekçesi × karşı-olgu sonucu — SAF çekirdek `counterfactual.summarize`."""
    try:
        from src.strategies.scalper import counterfactual as cf
    except Exception as exc:  # pragma: no cover - import yolu bozuksa
        return {"error": f"{type(exc).__name__}: {exc}", "by_reason": [], "overall": {}}
    summary = cf.summarize(rows)
    summary["note"] = _COUNTERFACTUAL_NOTE
    return summary


def _counterfactual_rows(report: Dict[str, Any]) -> List[List[str]]:
    section = report.get("counterfactual") or {}
    out: List[List[str]] = []
    entries = list(section.get("by_reason") or [])
    overall = section.get("overall")
    if overall:
        entries = entries + [overall]
    for item in entries:
        ci = item.get("ci95_roi_pct")
        out.append([
            str(item.get("reason") or "?"),
            str(item.get("n", 0)),
            str(item.get("measured", 0)),
            str(item.get("tp1", 0)),
            str(item.get("stop", 0)),
            str(item.get("open", 0)),
            str(item.get("no_data", 0)),
            _fmt_opt(item.get("avg_roi_pct")),
            # `profit_factor` None olabilir ("kayıp yok" ya da "hiç ölçüm
            # yok"): `_fmt_pf` yalnız sayı/inf bekler, burada ayrı okunur.
            _fmt_pf(item["profit_factor"])
            if item.get("profit_factor") is not None else "—",
            f"[{ci[0]:+.1f}, {ci[1]:+.1f}]" if ci else "—",
            str(item.get("collapsed", 0)),
        ])
    return out


def _fmt_opt(value: Optional[float]) -> str:
    """None = ÖLÇÜLMEDİ (0.0 DEĞİL) — rapor bunu ayırt edebilmeli."""
    return f"{value:+.2f}" if value is not None else "—"


def _forensics_rows(report: Dict[str, Any]) -> List[List[str]]:
    rows: List[List[str]] = []
    for item in report.get("forensics") or []:
        rows.append([
            item["tag"],
            str(item["trades"]),
            f"{item['winrate']:.1f}",
            f"{item['pnl']:.2f}",
            f"{item['avg_pnl']:.2f}",
            _fmt_pf(item["profit_factor"]),
            item.get("label") or "",
        ])
    return rows


def _fmt_share(value: Optional[float]) -> str:
    """Konsantrasyon payı: None = TANIMSIZ (kâr yok), 'ölçülmedi' DEĞİL."""
    return f"%{value:.1f}" if value is not None else "— (toplam PnL pozitif değil)"


def _concentration_lines(conc: Dict[str, Any]) -> List[str]:
    """D24/A4 — özet bloğunun konsantrasyon satırları (bilgi, eşik değil)."""
    if not conc:
        return []
    return [
        f"  Yoğunluk/sembol    : {conc.get('top_symbol') or '—'} "
        f"{float(conc.get('top_symbol_pnl') or 0.0):+.2f} "
        f"({_fmt_share(conc.get('top_symbol_pnl_share'))} kârın), "
        f"{conc.get('distinct_symbols', 0)} sembol",
        f"  Yoğunluk/işlem     : en iyi tek işlem "
        f"{float(conc.get('top_trade_pnl') or 0.0):+.2f} "
        f"({conc.get('top_trade_symbol') or '—'}, "
        f"{_fmt_share(conc.get('top_trade_pnl_share'))} kârın)",
        f"  Yoğunluk/gün       : {conc.get('top_day') or '—'} "
        f"{float(conc.get('top_day_pnl') or 0.0):+.2f} "
        f"({_fmt_share(conc.get('top_day_pnl_share'))} kârın), "
        f"{conc.get('distinct_days', 0)} işlem günü",
    ]


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
    lines.append(f"   NOT: {REAPER_SPLIT_NOTE}")

    lines.append("")
    lines.append("3) SEMBOL BAZINDA")
    lines.append(_render_table(_TABLE3_HEADERS, _symbol_rows(report)))

    lines.append("")
    lines.append("3b) STRATEJİ BAZINDA (C = scalper, AP = AlgoPro takipçisi)")
    lines.append(_render_table(_TABLE3B_HEADERS, _strategy_rows(report)))

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
    lines.extend(_concentration_lines(h.get("concentration") or {}))

    if report.get("forensics") is not None:
        lines.append("")
        lines.append("5b) ETİKET x SONUÇ (işlem adli kaydı, D21)")
        lines.append(_render_table(_TABLE5_HEADERS, _forensics_rows(report)))
        lines.append(
            "   (Bir işlem birden çok etiket taşıyabilir — satır toplamı işlem"
            " sayısını aşabilir.)"
        )

    cfx = report.get("counterfactual")
    if cfx is not None:
        lines.append("")
        lines.append("5d) KARŞI-OLGU DEFTERİ — REDDEDİLEN NİYETLER (D27/B)")
        lines.append(_render_table(_TABLE9_HEADERS, _counterfactual_rows(report)))
        lines.append(f"   {cfx.get('note') or _COUNTERFACTUAL_NOTE}")

    ai = report.get("ai")
    if ai is not None:
        lines.append("")
        lines.append("5c) AI KARAR KATMANI — GÖLGE RAPORU (D23)")
        for line in _ai_coverage_lines(ai):
            lines.append(f"   {line}")
        lines.append("")
        lines.append(_render_table(_TABLE6_HEADERS, _ai_set_rows(report)))
        lines.append("")
        lines.append("   Eksen x PnL korelasyonu:")
        lines.append(_render_table(_TABLE7_HEADERS, _ai_axis_rows(report)))
        lines.append("")
        lines.append("   Kalıp x sonuç:")
        lines.append(_render_table(_TABLE8_HEADERS, _ai_pattern_rows(report)))
        lines.append(
            "   (Bir işlem birden çok kalıp taşıyabilir — satır toplamı işlem"
            " sayısını aşabilir.)"
        )
        for note in ai.get("notes") or []:
            lines.append(f"   - {note}")

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
    lines.append(f"> {REAPER_SPLIT_NOTE}")

    lines.append("")
    lines.append("## 3) Sembol bazında")
    lines.append(_render_md_table(_TABLE3_HEADERS, _symbol_rows(report)))

    lines.append("")
    lines.append("## 3b) Strateji bazında (C = scalper, AP = AlgoPro takipçisi)")
    lines.append(_render_md_table(_TABLE3B_HEADERS, _strategy_rows(report)))

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
    conc = h.get("concentration") or {}
    if conc:
        lines.append(
            f"- Yoğunluk — sembol: **{conc.get('top_symbol') or '—'}** "
            f"{float(conc.get('top_symbol_pnl') or 0.0):+.2f} "
            f"({_fmt_share(conc.get('top_symbol_pnl_share'))} kârın, "
            f"{conc.get('distinct_symbols', 0)} sembol)"
        )
        lines.append(
            f"- Yoğunluk — tek işlem: **{float(conc.get('top_trade_pnl') or 0.0):+.2f}** "
            f"({conc.get('top_trade_symbol') or '—'}, "
            f"{_fmt_share(conc.get('top_trade_pnl_share'))} kârın)"
        )
        lines.append(
            f"- Yoğunluk — gün: **{conc.get('top_day') or '—'}** "
            f"{float(conc.get('top_day_pnl') or 0.0):+.2f} "
            f"({_fmt_share(conc.get('top_day_pnl_share'))} kârın, "
            f"{conc.get('distinct_days', 0)} işlem günü)"
        )

    if report.get("forensics") is not None:
        lines.append("")
        lines.append("## 5b) Etiket × sonuç (işlem adli kaydı, D21)")
        lines.append(_render_md_table(_TABLE5_HEADERS, _forensics_rows(report)))
        lines.append("")
        lines.append(
            "_Bir işlem birden çok etiket taşıyabilir — satır toplamı işlem "
            "sayısını aşabilir._"
        )

    cfx = report.get("counterfactual")
    if cfx is not None:
        lines.append("")
        lines.append("## 5d) Karşı-olgu defteri — reddedilen niyetler (D27/B)")
        lines.append(_render_md_table(_TABLE9_HEADERS, _counterfactual_rows(report)))
        lines.append("")
        lines.append(f"_{cfx.get('note') or _COUNTERFACTUAL_NOTE}_")

    ai = report.get("ai")
    if ai is not None:
        lines.append("")
        lines.append("## 5c) AI karar katmanı — gölge raporu (D23)")
        for line in _ai_coverage_lines(ai):
            lines.append(f"- {line}")
        lines.append("")
        lines.append(_render_md_table(_TABLE6_HEADERS, _ai_set_rows(report)))
        lines.append("")
        lines.append("**Eksen × PnL korelasyonu**")
        lines.append(_render_md_table(_TABLE7_HEADERS, _ai_axis_rows(report)))
        lines.append("")
        lines.append("**Kalıp × sonuç**")
        lines.append(_render_md_table(_TABLE8_HEADERS, _ai_pattern_rows(report)))
        lines.append("")
        lines.append(
            "_Bir işlem birden çok kalıp taşıyabilir — satır toplamı işlem "
            "sayısını aşabilir._"
        )
        for note in ai.get("notes") or []:
            lines.append(f"- {note}")

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
    parser.add_argument(
        "--forensics", action="store_true",
        help=(
            "işlem adli kaydı (D21) etiket × sonuç bölümünü ekle "
            "(scalp_trades.forensics sütunu; yoksa bölüm boş kalır)"
        ),
    )
    parser.add_argument(
        "--ai", action="store_true",
        help=(
            "AI karar katmanının (D23, GÖLGE) kapsama/deny-allow/eksen/maliyet "
            "bölümünü ekle (scalp_trades.forensics -> document['ai']; yoksa "
            "bölüm boş kalır)"
        ),
    )
    parser.add_argument(
        "--counterfactual", action="store_true",
        help=(
            "karşı-olgu defteri (D27/B): REDDEDİLEN niyetlerin 'girilseydi ne "
            "olurdu' tablosu. Kaynak DB DEĞİL, logs/trades.jsonl "
            "(event=counterfactual); yoksa bölüm boş kalır"
        ),
    )
    parser.add_argument(
        "--jsonl-dir", default=None,
        help=(
            "karşı-olgu satırlarının okunacağı log dizini (varsayılan: "
            "TRADINGBOT_LOG_DIR ya da ./logs)"
        ),
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

    forensics_table: Optional[List[Dict[str, Any]]] = None
    if args.forensics:
        forensics_rows, forensics_notes = load_forensics_rows(
            args.db, since, until, strategy=args.strategy
        )
        notes.extend(forensics_notes)
        forensics_table = build_forensics_table(forensics_rows)

    ai_section: Optional[Dict[str, Any]] = None
    if args.ai:
        ai_rows, ai_notes = load_ai_rows(
            args.db, since, until, strategy=args.strategy
        )
        notes.extend(ai_notes)
        ai_section = build_ai_report(ai_rows)

    counterfactual_section: Optional[Dict[str, Any]] = None
    if args.counterfactual:
        cf_rows, cf_notes = load_counterfactual_rows(
            since, until, log_dir=args.jsonl_dir
        )
        notes.extend(cf_notes)
        counterfactual_section = build_counterfactual_report(cf_rows)

    report = build_report(
        trades, daily_changes, since, until, days, notes,
        forensics=forensics_table,
        ai=ai_section,
        counterfactual=counterfactual_section,
    )
    output = RENDERERS[args.format](report)

    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
