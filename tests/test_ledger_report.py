"""
scripts/ledger_report.py için birim testleri — AĞ YOK (Binance klines
her zaman `--btc-klines-json` ile çevrimdışı verilir).

`scripts/` bir paket değil (__init__.py yok); modül dosya yoluyla
`sys.path`e eklenip adıyla import edilir (bkz. tests/test_autoresearch.py).
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import ledger_report as lr  # noqa: E402  (sys.path eklemesinden sonra import)


# --------------------------------------------------------------------------
# Yardımcılar
# --------------------------------------------------------------------------

# src/models/scalp_trade.py'nin sütunlarının minimal replikası — yalnızca
# scripts/ledger_report.py'nin okuduğu sütunlar + gerçekçilik için birkaç
# ekstra sütun (mevcut/gelecek migration'larla uyumsuzluk çıkarmasın diye
# NOT NULL zorlanmıyor, sqlite gevşek tipleme yapıyor).
_SCHEMA = """
CREATE TABLE scalp_trades (
    id INTEGER PRIMARY KEY,
    strategy TEXT,
    symbol TEXT,
    direction TEXT,
    entry_price REAL,
    exit_price REAL,
    quantity REAL,
    leverage INTEGER,
    margin_usdt REAL,
    realized_pnl REAL DEFAULT 0.0,
    roi_pct REAL DEFAULT 0.0,
    exit_reason TEXT,
    signal_reason TEXT,
    mae_pct REAL DEFAULT 0.0,
    mfe_pct REAL DEFAULT 0.0,
    status TEXT DEFAULT 'OPEN',
    opened_at TEXT,
    closed_at TEXT,
    sl_algo_id TEXT,
    tp1_algo_id TEXT,
    tp2_algo_id TEXT,
    entry_order_id TEXT,
    notes TEXT
)
"""


def _make_db(tmp_path: Path, rows: List[Dict[str, Any]], name: str = "test.db") -> str:
    db_path = tmp_path / name
    con = sqlite3.connect(str(db_path))
    try:
        con.execute(_SCHEMA)
        for row in rows:
            defaults = {
                "strategy": "C", "symbol": "BTCUSDT", "direction": "LONG",
                "entry_price": 100.0, "exit_price": 101.0, "quantity": 1.0,
                "leverage": 10, "margin_usdt": 10.0, "realized_pnl": 0.0,
                "roi_pct": 0.0, "exit_reason": "TRAIL", "signal_reason": "test",
                "mae_pct": 0.0, "mfe_pct": 0.0, "status": "CLOSED",
                "opened_at": "2026-08-01 00:00:00.000000", "closed_at": None,
                "sl_algo_id": None, "tp1_algo_id": None, "tp2_algo_id": None,
                "entry_order_id": None, "notes": None,
            }
            defaults.update(row)
            cols = ", ".join(defaults.keys())
            placeholders = ", ".join(["?"] * len(defaults))
            con.execute(
                f"INSERT INTO scalp_trades ({cols}) VALUES ({placeholders})",
                list(defaults.values()),
            )
        con.commit()
    finally:
        con.close()
    return str(db_path)


def _kline_row(date_str: str, open_px: float, close_px: float) -> List[Any]:
    """Binance kline dizisi formatında tek satır (yalnız open_time/open/close
    dolduruluyor — ledger_report.py bunların ötesini okumuyor)."""
    open_dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    open_ms = int(open_dt.timestamp() * 1000)
    close_ms = open_ms + 86_400_000 - 1
    return [
        open_ms, f"{open_px}", f"{max(open_px, close_px) * 1.001}",
        f"{min(open_px, close_px) * 0.999}", f"{close_px}", "1000.0",
        close_ms, "0", 0, "0", "0", "0",
    ]


def _write_klines_json(tmp_path: Path, rows: List[List[Any]], name: str = "klines.json") -> str:
    path = tmp_path / name
    path.write_text(json.dumps(rows), encoding="utf-8")
    return str(path)


# --------------------------------------------------------------------------
# parse_dt / iter_days
# --------------------------------------------------------------------------

class TestParseDt:
    def test_minute_precision(self):
        assert lr.parse_dt("2026-08-21 12:35") == datetime(2026, 8, 21, 12, 35)

    def test_microsecond_precision_db_format(self):
        assert lr.parse_dt("2026-08-21 12:35:07.123456") == datetime(2026, 8, 21, 12, 35, 7, 123456)

    def test_second_precision(self):
        assert lr.parse_dt("2026-08-21 12:35:07") == datetime(2026, 8, 21, 12, 35, 7)

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            lr.parse_dt("not-a-date")


class TestIterDays:
    def test_inclusive_range(self):
        since = datetime(2026, 8, 14, 12, 0)
        until = datetime(2026, 8, 21, 12, 0)
        days = lr.iter_days(since, until)
        assert days == [
            "2026-08-14", "2026-08-15", "2026-08-16", "2026-08-17",
            "2026-08-18", "2026-08-19", "2026-08-20", "2026-08-21",
        ]
        assert len(days) == 8

    def test_single_day(self):
        d = datetime(2026, 8, 21, 1, 0)
        assert lr.iter_days(d, d) == ["2026-08-21"]


# --------------------------------------------------------------------------
# klines_to_daily_changes / classify_regime
# --------------------------------------------------------------------------

class TestKlinesRegime:
    def test_up_flat_down_classification(self):
        rows = [
            _kline_row("2026-08-14", 100.0, 102.0),   # +2.0% -> UP
            _kline_row("2026-08-15", 100.0, 100.5),   # +0.5% -> FLAT
            _kline_row("2026-08-16", 100.0, 98.0),    # -2.0% -> DOWN
            _kline_row("2026-08-17", 100.0, 101.5),   # +1.5% tam sınır -> FLAT (> kesin, >= değil)
            _kline_row("2026-08-18", 100.0, 98.5),    # -1.5% tam sınır -> FLAT
        ]
        changes = lr.klines_to_daily_changes(rows)
        assert changes["2026-08-14"] == pytest.approx(2.0)
        assert lr.classify_regime(changes["2026-08-14"]) == "UP"
        assert lr.classify_regime(changes["2026-08-15"]) == "FLAT"
        assert lr.classify_regime(changes["2026-08-16"]) == "DOWN"
        assert lr.classify_regime(changes["2026-08-17"]) == "FLAT"
        assert lr.classify_regime(changes["2026-08-18"]) == "FLAT"

    def test_missing_day_is_unknown_regime(self):
        assert lr.classify_regime(None) == "?"


# --------------------------------------------------------------------------
# load_closed_trades — durum filtreleme + zaman aralığı
# --------------------------------------------------------------------------

class TestLoadClosedTrades:
    def test_excludes_open_and_shadow(self, tmp_path):
        db = _make_db(tmp_path, [
            {"id": 1, "status": "CLOSED", "realized_pnl": 10.0, "closed_at": "2026-08-15 10:00:00.000000"},
            {"id": 2, "status": "OPEN", "realized_pnl": 0.0, "closed_at": None},
            {"id": 3, "status": "SHADOW", "realized_pnl": 0.0, "closed_at": "2026-08-15 10:05:00.000000"},
        ])
        since = datetime(2026, 8, 1)
        until = datetime(2026, 8, 31)
        trades, skipped = lr.load_closed_trades(db, since, until)
        assert [t.id for t in trades] == [1]
        assert skipped == 0

    def test_filters_by_closed_at_range(self, tmp_path):
        db = _make_db(tmp_path, [
            {"id": 1, "status": "CLOSED", "closed_at": "2026-08-10 00:00:00.000000"},  # önce
            {"id": 2, "status": "CLOSED", "closed_at": "2026-08-15 00:00:00.000000"},  # içinde
            {"id": 3, "status": "CLOSED", "closed_at": "2026-08-25 00:00:00.000000"},  # sonra
        ])
        since = datetime(2026, 8, 14)
        until = datetime(2026, 8, 21)
        trades, _ = lr.load_closed_trades(db, since, until)
        assert [t.id for t in trades] == [2]

    def test_null_closed_at_is_skipped_and_counted(self, tmp_path):
        db = _make_db(tmp_path, [
            {"id": 1, "status": "CLOSED", "closed_at": None},
            {"id": 2, "status": "CLOSED", "closed_at": "2026-08-15 00:00:00.000000"},
        ])
        since = datetime(2026, 8, 1)
        until = datetime(2026, 8, 31)
        trades, skipped = lr.load_closed_trades(db, since, until)
        assert [t.id for t in trades] == [2]
        assert skipped == 1

    def test_null_exit_reason_normalized_to_unknown(self, tmp_path):
        db = _make_db(tmp_path, [
            {"id": 1, "status": "CLOSED", "closed_at": "2026-08-15 00:00:00.000000", "exit_reason": None},
            {"id": 2, "status": "CLOSED", "closed_at": "2026-08-15 00:00:00.000000", "exit_reason": ""},
            {"id": 3, "status": "CLOSED", "closed_at": "2026-08-15 00:00:00.000000", "exit_reason": "SL"},
        ])
        since = datetime(2026, 8, 1)
        until = datetime(2026, 8, 31)
        trades, _ = lr.load_closed_trades(db, since, until)
        by_id = {t.id: t for t in trades}
        assert by_id[1].exit_reason == "UNKNOWN"
        assert by_id[2].exit_reason == "UNKNOWN"
        assert by_id[3].exit_reason == "SL"

    def test_empty_db_range_returns_no_trades(self, tmp_path):
        db = _make_db(tmp_path, [])
        since = datetime(2026, 8, 1)
        until = datetime(2026, 8, 31)
        trades, skipped = lr.load_closed_trades(db, since, until)
        assert trades == []
        assert skipped == 0


# --------------------------------------------------------------------------
# Tablo/istatistik matematiği — bilinen bir veri setiyle elle doğrulama
# --------------------------------------------------------------------------

@pytest.fixture()
def known_dataset(tmp_path):
    """3 rejim gününe (UP/FLAT/DOWN) yayılmış, elle hesaplanabilir 6 işlem.

    Gün planı (klines):
      2026-08-14 UP   (+3.0%)
      2026-08-15 FLAT (+0.2%)
      2026-08-16 DOWN (-3.0%)

    İşlemler:
      UP   gün: LONG +100 (TRAIL), LONG +50 (TRAIL)   -> PnL +150, WR 100%, PF inf
      FLAT gün: LONG +40 (TP_LADDER), SHORT -20 (SL)  -> PnL +20,  WR 50%,  PF 2.0
      DOWN gün: SHORT -30 (SL, exit_reason NULL->UNKNOWN)
    """
    rows = [
        {"id": 1, "symbol": "BTCUSDT", "direction": "LONG", "realized_pnl": 100.0,
         "exit_reason": "TRAIL", "closed_at": "2026-08-14 10:00:00.000000"},
        {"id": 2, "symbol": "ETHUSDT", "direction": "LONG", "realized_pnl": 50.0,
         "exit_reason": "TRAIL", "closed_at": "2026-08-14 14:00:00.000000"},
        {"id": 3, "symbol": "BTCUSDT", "direction": "LONG", "realized_pnl": 40.0,
         "exit_reason": "TP_LADDER", "closed_at": "2026-08-15 09:00:00.000000"},
        {"id": 4, "symbol": "SOLUSDT", "direction": "SHORT", "realized_pnl": -20.0,
         "exit_reason": "SL", "closed_at": "2026-08-15 11:00:00.000000"},
        {"id": 5, "symbol": "BTCUSDT", "direction": "SHORT", "realized_pnl": -30.0,
         "exit_reason": None, "closed_at": "2026-08-16 08:00:00.000000"},
    ]
    db = _make_db(tmp_path, rows)
    klines = [
        _kline_row("2026-08-14", 100.0, 103.0),
        _kline_row("2026-08-15", 100.0, 100.2),
        _kline_row("2026-08-16", 100.0, 97.0),
    ]
    klines_path = _write_klines_json(tmp_path, klines)
    return db, klines_path


class TestKnownDatasetMath:
    def _build(self, tmp_path, known_dataset):
        db, klines_path = known_dataset
        since = datetime(2026, 8, 14)
        until = datetime(2026, 8, 16, 23, 59, 59)
        trades, skipped = lr.load_closed_trades(db, since, until)
        daily_changes = lr.klines_to_daily_changes(lr.load_offline_klines(klines_path))
        days = lr.iter_days(since, until)
        report = lr.build_report(trades, daily_changes, since, until, days, [])
        return report

    def test_regime_direction_up_pf_is_infinite(self, tmp_path, known_dataset):
        report = self._build(tmp_path, known_dataset)
        row = next(r for r in report["regime_direction"] if r["regime"] == "UP" and r["direction"] == "LONG")
        assert row["trades"] == 2
        assert row["winrate"] == pytest.approx(100.0)
        assert row["pnl"] == pytest.approx(150.0)
        assert row["profit_factor"] == float("inf")
        assert row["avg_win"] == pytest.approx(75.0)
        assert row["avg_loss"] == pytest.approx(0.0)

    def test_regime_direction_flat_short_has_loss(self, tmp_path, known_dataset):
        report = self._build(tmp_path, known_dataset)
        row = next(r for r in report["regime_direction"] if r["regime"] == "FLAT" and r["direction"] == "SHORT")
        assert row["trades"] == 1
        assert row["pnl"] == pytest.approx(-20.0)
        assert row["avg_loss"] == pytest.approx(-20.0)
        assert row["profit_factor"] == pytest.approx(0.0)

    def test_down_short_zero_trades_present_in_table(self, tmp_path, known_dataset):
        """DOWN LONG (0 işlem) tabloda görünmeli — boşluk gizlenmemeli."""
        report = self._build(tmp_path, known_dataset)
        row = next(r for r in report["regime_direction"] if r["regime"] == "DOWN" and r["direction"] == "LONG")
        assert row["trades"] == 0
        assert row["pnl"] == pytest.approx(0.0)
        assert row["profit_factor"] == pytest.approx(0.0)

    def test_exit_reason_direction_table(self, tmp_path, known_dataset):
        report = self._build(tmp_path, known_dataset)
        by_key = {(r["exit_reason"], r["direction"]): r for r in report["exit_reason_direction"]}
        assert by_key[("TRAIL", "LONG")]["trades"] == 2
        assert by_key[("TRAIL", "LONG")]["pnl"] == pytest.approx(150.0)
        assert by_key[("TP_LADDER", "LONG")]["trades"] == 1
        assert by_key[("SL", "SHORT")]["trades"] == 1
        assert by_key[("UNKNOWN", "SHORT")]["trades"] == 1
        assert by_key[("UNKNOWN", "SHORT")]["pnl"] == pytest.approx(-30.0)

    def test_symbol_table(self, tmp_path, known_dataset):
        report = self._build(tmp_path, known_dataset)
        by_symbol = {r["symbol"]: r for r in report["symbol"]}
        assert by_symbol["BTCUSDT"]["trades"] == 3  # +100, +40, -30
        assert by_symbol["BTCUSDT"]["pnl"] == pytest.approx(110.0)
        assert by_symbol["ETHUSDT"]["trades"] == 1
        assert by_symbol["SOLUSDT"]["pnl"] == pytest.approx(-20.0)

    def test_daily_table_cumulative_pnl(self, tmp_path, known_dataset):
        report = self._build(tmp_path, known_dataset)
        by_day = {r["day"]: r for r in report["daily"]}
        assert by_day["2026-08-14"]["pnl"] == pytest.approx(150.0)
        assert by_day["2026-08-14"]["cum_pnl"] == pytest.approx(150.0)
        assert by_day["2026-08-14"]["regime"] == "UP"
        assert by_day["2026-08-15"]["pnl"] == pytest.approx(20.0)
        assert by_day["2026-08-15"]["cum_pnl"] == pytest.approx(170.0)
        assert by_day["2026-08-15"]["regime"] == "FLAT"
        assert by_day["2026-08-16"]["pnl"] == pytest.approx(-30.0)
        assert by_day["2026-08-16"]["cum_pnl"] == pytest.approx(140.0)
        assert by_day["2026-08-16"]["regime"] == "DOWN"

    def test_headline_totals(self, tmp_path, known_dataset):
        report = self._build(tmp_path, known_dataset)
        h = report["headline"]
        assert h["total_trades"] == 5
        assert h["pnl"] == pytest.approx(140.0)
        assert h["winrate"] == pytest.approx(60.0)  # 3 kazanan / 5
        # gross_win = 100+50+40=190, gross_loss=20+30=50 -> PF 3.8
        assert h["profit_factor"] == pytest.approx(190.0 / 50.0)
        assert h["up_pnl_share_pct"] == pytest.approx(150.0 / 140.0 * 100.0)
        assert h["unknown_exit_share_pct"] == pytest.approx(20.0)  # 1/5
        assert h["regime_day_counts"] == {"UP": 1, "FLAT": 1, "DOWN": 1, "?": 0}
        assert h["regime_pnls"]["UP"] == pytest.approx(150.0)
        assert h["regime_pnls"]["FLAT"] == pytest.approx(20.0)
        assert h["regime_pnls"]["DOWN"] == pytest.approx(-30.0)

    def test_max_drawdown_from_trade_level_cumulative_curve(self, tmp_path, known_dataset):
        report = self._build(tmp_path, known_dataset)
        # sıra (closed_at): +100, +50, +40, -20, -30 -> kümülatif 100,150,190,170,140
        # peak 190, en düşük sonrası 140 -> maxDD = 190-140 = 50
        assert report["headline"]["max_drawdown"] == pytest.approx(50.0)

    def test_checklist_down_day_present_but_negative_pnl_fails(self, tmp_path, known_dataset):
        report = self._build(tmp_path, known_dataset)
        checklist = {item["name"]: item for item in report["checklist"]}
        down_day_item = next(i for i in report["checklist"] if "düşüş" in i["name"])
        assert down_day_item["status"] == "PASS"  # 1 DOWN günü var
        down_pnl_item = next(i for i in report["checklist"] if i["name"].startswith("DOWN rejim"))
        assert down_pnl_item["status"] == "FAIL"  # DOWN PnL -30 < 0
        up_pnl_item = next(i for i in report["checklist"] if i["name"].startswith("UP rejim"))
        assert up_pnl_item["status"] == "PASS"
        # yalnız 3 gün -> soak süresi FAIL
        soak_item = next(i for i in report["checklist"] if "Soak süresi" in i["name"])
        assert soak_item["status"] == "FAIL"
        assert "3 gün" in soak_item["detail"]
        # UNKNOWN oranı %20 >= %5 eşiği -> FAIL
        unknown_item = next(i for i in report["checklist"] if "UNKNOWN" in i["name"])
        assert unknown_item["status"] == "FAIL"


# --------------------------------------------------------------------------
# Checklist — N/A ve tüm-PASS senaryoları
# --------------------------------------------------------------------------

class TestChecklistScenarios:
    def test_regime_with_no_trades_is_na(self, tmp_path):
        db = _make_db(tmp_path, [
            {"id": 1, "status": "CLOSED", "realized_pnl": 10.0,
             "closed_at": "2026-08-15 00:00:00.000000", "exit_reason": "TRAIL"},
        ])
        klines = [_kline_row("2026-08-15", 100.0, 100.2)]  # FLAT, DOWN/UP hiç yok
        klines_path = _write_klines_json(tmp_path, klines)
        since = datetime(2026, 8, 15)
        until = datetime(2026, 8, 15, 23, 59, 59)
        trades, _ = lr.load_closed_trades(db, since, until)
        daily_changes = lr.klines_to_daily_changes(lr.load_offline_klines(klines_path))
        days = lr.iter_days(since, until)
        report = lr.build_report(trades, daily_changes, since, until, days, [])
        up_item = next(i for i in report["checklist"] if i["name"].startswith("UP rejim"))
        down_item = next(i for i in report["checklist"] if i["name"].startswith("DOWN rejim"))
        assert up_item["status"] == "N/A"
        assert down_item["status"] == "N/A"

    def test_all_pass_scenario(self, tmp_path):
        """>=5 gün, 1 DOWN günü, UNKNOWN yok, her rejim PnL >= 0 -> hepsi PASS/N/A."""
        rows = [
            {"id": 1, "status": "CLOSED", "realized_pnl": 10.0, "direction": "LONG",
             "exit_reason": "TRAIL", "closed_at": "2026-08-14 00:00:00.000000"},  # UP
            {"id": 2, "status": "CLOSED", "realized_pnl": 5.0, "direction": "LONG",
             "exit_reason": "TP_LADDER", "closed_at": "2026-08-15 00:00:00.000000"},  # FLAT
            {"id": 3, "status": "CLOSED", "realized_pnl": 1.0, "direction": "SHORT",
             "exit_reason": "TRAIL", "closed_at": "2026-08-18 00:00:00.000000"},  # DOWN
        ]
        db = _make_db(tmp_path, rows)
        klines = [
            _kline_row("2026-08-14", 100.0, 103.0),  # UP
            _kline_row("2026-08-15", 100.0, 100.1),  # FLAT
            _kline_row("2026-08-16", 100.0, 100.1),  # FLAT
            _kline_row("2026-08-17", 100.0, 100.1),  # FLAT
            _kline_row("2026-08-18", 100.0, 97.0),   # DOWN
        ]
        klines_path = _write_klines_json(tmp_path, klines)
        since = datetime(2026, 8, 14)
        until = datetime(2026, 8, 18, 23, 59, 59)
        trades, _ = lr.load_closed_trades(db, since, until)
        daily_changes = lr.klines_to_daily_changes(lr.load_offline_klines(klines_path))
        days = lr.iter_days(since, until)
        report = lr.build_report(trades, daily_changes, since, until, days, [])
        statuses = {item["status"] for item in report["checklist"]}
        assert statuses <= {"PASS", "N/A"}
        assert all(item["status"] != "FAIL" for item in report["checklist"])


# --------------------------------------------------------------------------
# Biçimler — text/md/json
# --------------------------------------------------------------------------

class TestRenderers:
    def _sample_report(self, tmp_path, known_dataset):
        db, klines_path = known_dataset
        since = datetime(2026, 8, 14)
        until = datetime(2026, 8, 16, 23, 59, 59)
        trades, _ = lr.load_closed_trades(db, since, until)
        daily_changes = lr.klines_to_daily_changes(lr.load_offline_klines(klines_path))
        days = lr.iter_days(since, until)
        return lr.build_report(trades, daily_changes, since, until, days, [])

    def test_text_contains_sections(self, tmp_path, known_dataset):
        report = self._sample_report(tmp_path, known_dataset)
        text = lr.render_text(report)
        assert "REJİM x YÖN" in text
        assert "ÇIKIŞ NEDENİ x YÖN" in text
        assert "SEMBOL BAZINDA" in text
        assert "GÜNLÜK" in text
        assert "ÖZET" in text
        assert "SOAK KONTROL LİSTESİ" in text
        assert "[PASS]" in text or "[FAIL]" in text

    def test_md_has_tables_and_checklist(self, tmp_path, known_dataset):
        report = self._sample_report(tmp_path, known_dataset)
        md = lr.render_md(report)
        assert "| Rejim | Yön |" in md
        assert "|---|---|" in md
        assert "## 6) Soak kontrol listesi" in md
        assert "**[FAIL]**" in md or "**[PASS]**" in md

    def test_json_round_trips_and_has_expected_keys(self, tmp_path, known_dataset):
        report = self._sample_report(tmp_path, known_dataset)
        text = lr.render_json(report)
        parsed = json.loads(text)
        assert set(parsed.keys()) == {
            "meta", "regime_direction", "exit_reason_direction",
            "symbol", "daily", "headline", "checklist", "notes",
        }
        assert parsed["headline"]["total_trades"] == 5

    def test_json_infinite_pf_sanitized_to_string(self, tmp_path, known_dataset):
        report = self._sample_report(tmp_path, known_dataset)
        text = lr.render_json(report)
        parsed = json.loads(text)
        up_long = next(
            r for r in parsed["regime_direction"]
            if r["regime"] == "UP" and r["direction"] == "LONG"
        )
        assert up_long["profit_factor"] == "inf"


# --------------------------------------------------------------------------
# Sağlamlık — eksik kline / boş defter
# --------------------------------------------------------------------------

class TestRobustness:
    def test_missing_kline_day_marked_question_mark(self, tmp_path):
        db = _make_db(tmp_path, [
            {"id": 1, "status": "CLOSED", "realized_pnl": 5.0,
             "closed_at": "2026-08-16 00:00:00.000000"},
        ])
        # yalnız 08-14 için kline var; 08-15/08-16 eksik.
        klines = [_kline_row("2026-08-14", 100.0, 103.0)]
        klines_path = _write_klines_json(tmp_path, klines)
        since = datetime(2026, 8, 14)
        until = datetime(2026, 8, 16, 23, 59, 59)
        trades, _ = lr.load_closed_trades(db, since, until)
        daily_changes = lr.klines_to_daily_changes(lr.load_offline_klines(klines_path))
        days = lr.iter_days(since, until)
        report = lr.build_report(trades, daily_changes, since, until, days, [])
        by_day = {r["day"]: r for r in report["daily"]}
        assert by_day["2026-08-14"]["regime"] == "UP"
        assert by_day["2026-08-15"]["regime"] == "?"
        assert by_day["2026-08-16"]["regime"] == "?"
        assert by_day["2026-08-16"]["btc_pct"] is None
        # işlem 08-16'da, rejimi "?" olan bir günde -> regime_direction'da "?" satırı olmalı
        q_row = next(
            r for r in report["regime_direction"]
            if r["regime"] == "?" and r["direction"] == "LONG"
        )
        assert q_row["trades"] == 1

    def test_empty_ledger_clean_message_no_crash(self, tmp_path):
        db = _make_db(tmp_path, [])
        klines = [_kline_row("2026-08-14", 100.0, 103.0)]
        klines_path = _write_klines_json(tmp_path, klines)
        since = datetime(2026, 8, 14)
        until = datetime(2026, 8, 14, 23, 59, 59)
        trades, _ = lr.load_closed_trades(db, since, until)
        assert trades == []
        daily_changes = lr.klines_to_daily_changes(lr.load_offline_klines(klines_path))
        days = lr.iter_days(since, until)
        notes = ["Bu tarih aralığında kapanmış (CLOSED) işlem yok."]
        report = lr.build_report(trades, daily_changes, since, until, days, notes)
        assert report["headline"]["total_trades"] == 0
        assert report["headline"]["pnl"] == 0.0
        assert report["headline"]["up_pnl_share_pct"] is None
        text = lr.render_text(report)
        assert "Bu tarih aralığında kapanmış (CLOSED) işlem yok." in text
        # render etmek çökmemeli, ve tüm checklist satırları N/A ya da FAIL olmalı (0 işlem)
        md = lr.render_md(report)
        assert "kapanmış (CLOSED) işlem yok" in md
        parsed = json.loads(lr.render_json(report))
        assert parsed["headline"]["total_trades"] == 0


# --------------------------------------------------------------------------
# CLI (main) — uçtan uca, ağsız
# --------------------------------------------------------------------------

class TestCli:
    def test_main_missing_db_returns_error(self, tmp_path, capsys):
        rc = lr.main([
            "--db", str(tmp_path / "nope.db"),
            "--since", "2026-08-14 00:00", "--until", "2026-08-15 00:00",
        ])
        assert rc == 2
        captured = capsys.readouterr()
        assert "bulunamadı" in captured.err
        assert not (tmp_path / "nope.db").exists()  # sqlite3 sessizce dosya YARATMAMALI

    def test_main_since_after_until_returns_error(self, tmp_path, capsys):
        db = _make_db(tmp_path, [])
        rc = lr.main([
            "--db", db, "--since", "2026-08-20 00:00", "--until", "2026-08-14 00:00",
        ])
        assert rc == 2
        assert "sonra olamaz" in capsys.readouterr().err

    def test_main_writes_md_to_out_file(self, tmp_path, known_dataset):
        db, klines_path = known_dataset
        out_path = tmp_path / "report.md"
        rc = lr.main([
            "--db", db, "--since", "2026-08-14 00:00", "--until", "2026-08-16 23:59:59",
            "--btc-klines-json", klines_path, "--format", "md", "--out", str(out_path),
        ])
        assert rc == 0
        content = out_path.read_text(encoding="utf-8")
        assert "# Canlı defter rejim raporu" in content
        assert "## 6) Soak kontrol listesi" in content

    def test_main_json_stdout(self, tmp_path, known_dataset, capsys):
        db, klines_path = known_dataset
        rc = lr.main([
            "--db", db, "--since", "2026-08-14 00:00", "--until", "2026-08-16 23:59:59",
            "--btc-klines-json", klines_path, "--format", "json",
        ])
        assert rc == 0
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed["headline"]["total_trades"] == 5

    def test_main_offline_klines_file_missing_falls_back_to_question_mark(self, tmp_path, capsys):
        db = _make_db(tmp_path, [
            {"id": 1, "status": "CLOSED", "realized_pnl": 5.0,
             "closed_at": "2026-08-15 00:00:00.000000"},
        ])
        rc = lr.main([
            "--db", db, "--since", "2026-08-15 00:00", "--until", "2026-08-15 23:59:59",
            "--btc-klines-json", str(tmp_path / "does-not-exist.json"), "--format", "text",
        ])
        assert rc == 0
        out = capsys.readouterr().out
        assert "okunamadı" in out or "?" in out
