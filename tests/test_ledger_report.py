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
            # D20b: "strategy" = strateji bazlı kırılım (C = scalper,
            # AP = gömülü AlgoPro takipçisi; ikisi AYNI tabloya yazar).
            "meta", "regime_direction", "exit_reason_direction",
            "symbol", "strategy", "daily", "headline", "checklist", "notes",
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

# --------------------------------------------------------------------------
# --strategy filtresi (D20: AlgoPro takipçi halkası defterini ayırmak için)
# --------------------------------------------------------------------------


class TestStrategyFilter:
    def _db(self, tmp_path):
        return _make_db(tmp_path, [
            {"id": 1, "strategy": "C", "status": "CLOSED",
             "realized_pnl": 10.0, "closed_at": "2026-08-20 10:00:00.000000"},
            {"id": 2, "strategy": "AP", "status": "CLOSED",
             "realized_pnl": -5.0, "closed_at": "2026-08-20 11:00:00.000000"},
            {"id": 3, "strategy": "AP", "status": "CLOSED",
             "realized_pnl": 7.0, "closed_at": "2026-08-20 12:00:00.000000"},
        ], name="strategy.db")

    def test_no_filter_returns_all(self, tmp_path):
        db = self._db(tmp_path)
        since, until = lr.parse_dt("2026-08-20"), lr.parse_dt("2026-08-21")
        trades, _ = lr.load_closed_trades(db, since, until)
        assert len(trades) == 3

    def test_filter_selects_only_follower_rows(self, tmp_path):
        db = self._db(tmp_path)
        since, until = lr.parse_dt("2026-08-20"), lr.parse_dt("2026-08-21")
        trades, _ = lr.load_closed_trades(db, since, until, strategy="AP")
        assert [t.id for t in trades] == [2, 3]
        assert {t.strategy for t in trades} == {"AP"}

    def test_filter_is_case_insensitive(self, tmp_path):
        db = self._db(tmp_path)
        since, until = lr.parse_dt("2026-08-20"), lr.parse_dt("2026-08-21")
        trades, _ = lr.load_closed_trades(db, since, until, strategy=" ap ")
        assert len(trades) == 2

    def test_cli_flag_exists(self):
        args = lr.parse_args(["--strategy", "AP"])
        assert args.strategy == "AP"


# --------------------------------------------------------------------------
# --ai: AI karar katmanı gölge raporu (D23)
# --------------------------------------------------------------------------
# `document["ai"]` bloğunu `src/strategies/scalper/tracker.py::attach_ai`
# yazar; MIGRATION YOKTUR, blok mevcut `forensics` JSON sütununda yaşar.
# Bu yüzden AI testleri AYRI bir şema kullanır: `_SCHEMA`'da `forensics`
# sütunu YOKTUR ve o eksiklik ayrıca test edilir (rapor çökmemeli).

_AI_COLUMNS = (
    "id", "strategy", "symbol", "direction", "realized_pnl",
    "exit_reason", "status", "closed_at", "forensics",
)


def _make_ai_db(tmp_path: Path, rows: List[Dict[str, Any]], name: str = "ai.db") -> str:
    """`forensics` sütunu OLAN minimal defter."""
    db_path = tmp_path / name
    con = sqlite3.connect(str(db_path))
    try:
        con.execute(
            "CREATE TABLE scalp_trades (id INTEGER PRIMARY KEY, strategy TEXT,"
            " symbol TEXT, direction TEXT, realized_pnl REAL, exit_reason TEXT,"
            " status TEXT, closed_at TEXT, forensics TEXT)"
        )
        for row in rows:
            values = {
                "strategy": "C", "symbol": "BTCUSDT", "direction": "LONG",
                "realized_pnl": 0.0, "exit_reason": "SL", "status": "CLOSED",
                "closed_at": "2026-08-22 10:00:00.000000", "forensics": None,
            }
            values.update(row)
            con.execute(
                "INSERT INTO scalp_trades (%s) VALUES (%s)"
                % (", ".join(_AI_COLUMNS), ", ".join(["?"] * len(_AI_COLUMNS))),
                [values[col] for col in _AI_COLUMNS],
            )
        con.commit()
    finally:
        con.close()
    return str(db_path)


def _ai_block(
    status: str = "ok",
    verdict: Optional[str] = None,
    axes: Optional[Dict[str, float]] = None,
    pattern_ids: Optional[List[str]] = None,
    latency_ms: int = 1000,
    extra_document: Optional[Dict[str, Any]] = None,
) -> str:
    """`AiGate._record` çıktısının biçimce aynısı (d23.1).

    `status != "ok"` ise verdict/axes/pattern_ids ALANLARI HİÇ YAZILMAZ —
    fail-open kaydı gerçekte de böyledir.
    """
    record: Dict[str, Any] = {
        "schema_version": "d23.1", "status": status, "mode": "shadow",
        "applied": False, "stale": False, "provider": "deepseek",
        "model_version": "deepseek:deepseek-chat/d23-prompt-v1",
        "prompt_version": "d23-prompt-v1", "input_digest": "sha256:abc",
        "latency_ms": latency_ms, "at": "2026-08-22T10:00:00Z",
    }
    if verdict is not None:
        record.update({
            "verdict": verdict,
            "confidence": 0.62,
            "axes": axes or {},
            "pattern_ids": pattern_ids or [],
            "reason": "test",
            "horizon_end_at": "2026-08-22T12:00:00Z",
            "invalid_if": "test",
            "expected_outcome": "sl",
        })
    document: Dict[str, Any] = {"ai": record}
    if extra_document:
        document.update(extra_document)
    return json.dumps(document)


def _axes_for(pnl: float) -> Dict[str, float]:
    """`stop_sanity` PnL ile TAM DOĞRUSAL (r=+1 beklenir), `crowding` ve
    `regime_fit` SABİT (r tanımsız -> None beklenir)."""
    return {
        "regime_fit": 0.4,
        "tv_confluence_depth": 0.2,
        "stop_sanity": (pnl + 100.0) / 200.0,
        "crowding": 0.5,
        "structure_conflict": 0.1,
    }


@pytest.fixture()
def ai_dataset(tmp_path):
    """9 kapanmış işlem: 6 'ok' karar (3 deny / 3 allow), 1 fail-open kayıt,
    2 AI kaydı olmayan işlem.

      deny  : -30, -10, +10  -> ort -10, sd 20  (GA elle doğrulanır)
      allow : +100, -50, +25 -> brüt kâr 125 / brüt kayıp 50 -> PF 2.5
      taban : 9 işlem, brüt kâr 141 / brüt kayıp 95 -> PF 1.4842...
    """
    rows = [
        {"id": 1, "realized_pnl": -30.0, "closed_at": "2026-08-22 10:00:00.000000",
         "forensics": _ai_block(verdict="deny", axes=_axes_for(-30.0),
                                pattern_ids=["E8.7_tv_short_low_pf"], latency_ms=1000)},
        {"id": 2, "realized_pnl": -10.0, "closed_at": "2026-08-22 11:00:00.000000",
         "forensics": _ai_block(verdict="deny", axes=_axes_for(-10.0),
                                pattern_ids=["E8.7_tv_short_low_pf"], latency_ms=2000)},
        {"id": 3, "realized_pnl": 10.0, "closed_at": "2026-08-22 12:00:00.000000",
         "forensics": _ai_block(verdict="deny", axes=_axes_for(10.0),
                                pattern_ids=["D21_stale_signal"], latency_ms=3000)},
        {"id": 4, "realized_pnl": 100.0, "closed_at": "2026-08-22 13:00:00.000000",
         "forensics": _ai_block(verdict="allow", axes=_axes_for(100.0), latency_ms=1500)},
        {"id": 5, "realized_pnl": -50.0, "closed_at": "2026-08-22 14:00:00.000000",
         "forensics": _ai_block(verdict="allow", axes=_axes_for(-50.0), latency_ms=2500)},
        {"id": 6, "realized_pnl": 25.0, "closed_at": "2026-08-22 15:00:00.000000",
         "forensics": _ai_block(verdict="allow", axes=_axes_for(25.0), latency_ms=500)},
        # fail-open: verdict YOK, yalnız durum + gecikme kaydı
        {"id": 7, "realized_pnl": 5.0, "closed_at": "2026-08-22 16:00:00.000000",
         "forensics": _ai_block(status="ai_unavailable", latency_ms=40)},
        # adli kayıt var ama AI bloğu YOK (katman kapalıyken kapanmış işlem)
        {"id": 8, "realized_pnl": -5.0, "closed_at": "2026-08-22 17:00:00.000000",
         "forensics": json.dumps({"verdict": ["stale_signal"]})},
        # hiç adli kayıt yok
        {"id": 9, "realized_pnl": 1.0, "closed_at": "2026-08-22 18:00:00.000000",
         "forensics": None},
    ]
    return _make_ai_db(tmp_path, rows)


_AI_SINCE = datetime(2026, 8, 22, 0, 0)
_AI_UNTIL = datetime(2026, 8, 22, 23, 59, 59)


def _ai_report(db: str) -> Dict[str, Any]:
    rows, _ = lr.load_ai_rows(db, _AI_SINCE, _AI_UNTIL)
    return lr.build_ai_report(rows)


class TestAiCoverage:
    def test_rows_split_between_with_and_without_ai(self, ai_dataset):
        rows, notes = lr.load_ai_rows(ai_dataset, _AI_SINCE, _AI_UNTIL)
        assert len(rows) == 9
        assert sum(1 for r in rows if r["has_ai"]) == 7
        assert sum(1 for r in rows if not r["has_ai"]) == 2
        assert any("AI kaydı YOK" in n for n in notes)

    def test_coverage_and_status_breakdown(self, ai_dataset):
        report = _ai_report(ai_dataset)
        cov = report["coverage"]
        assert cov["closed_trades"] == 9
        assert cov["with_ai"] == 7
        assert cov["without_ai"] == 2
        assert cov["status_counts"]["ok"] == 6
        assert cov["status_counts"]["ai_unavailable"] == 1
        # sıfır sayılı durumlar da görünür: "hiç yok" ile "bakılmadı" ayrılır
        assert cov["status_counts"]["ai_malformed"] == 0
        assert cov["status_counts"]["ai_budget_exhausted"] == 0

    def test_non_ok_record_carries_no_verdict(self, ai_dataset):
        rows, _ = lr.load_ai_rows(ai_dataset, _AI_SINCE, _AI_UNTIL)
        fail_open = next(r for r in rows if r["id"] == 7)
        assert fail_open["has_ai"] is True
        assert fail_open["status"] == "ai_unavailable"
        assert fail_open["verdict"] is None
        assert fail_open["axes"] == {}
        assert fail_open["latency_ms"] == 40.0

    def test_no_block_note_is_not_an_approval(self, ai_dataset):
        _, notes = lr.load_ai_rows(ai_dataset, _AI_SINCE, _AI_UNTIL)
        assert any("'AI izin verdi' anlamına GELMEZ" in n for n in notes)

    def test_e86_capacity_warning_always_present(self, ai_dataset):
        _, notes = lr.load_ai_rows(ai_dataset, _AI_SINCE, _AI_UNTIL)
        assert any("E8.6" in n for n in notes)
        assert any("ALT SINIRDIR" in n for n in notes)

    def test_strategy_filter_applies(self, tmp_path):
        db = _make_ai_db(tmp_path, [
            {"id": 1, "strategy": "C", "realized_pnl": 10.0,
             "forensics": _ai_block(verdict="allow")},
            {"id": 2, "strategy": "AP", "realized_pnl": -5.0,
             "forensics": _ai_block(verdict="deny",
                                    pattern_ids=["E8.7_tv_short_low_pf"])},
        ], name="ai_strategy.db")
        rows, _ = lr.load_ai_rows(db, _AI_SINCE, _AI_UNTIL, strategy="ap")
        assert [r["id"] for r in rows] == [2]


class TestAiSetStatistics:
    def test_deny_mean_and_ci95_hand_checked(self, ai_dataset):
        deny = _ai_report(ai_dataset)["deny"]
        # -30, -10, +10 -> ort -10; sapmalar -20/0/+20 -> kareler toplamı 800,
        # n-1=2 -> varyans 400 -> sd 20; yarı-genişlik 1.96*20/sqrt(3)=22.6321
        assert deny["trades"] == 3
        assert deny["pnl"] == pytest.approx(-30.0)
        assert deny["avg_pnl"] == pytest.approx(-10.0)
        assert deny["winrate"] == pytest.approx(100.0 / 3.0)
        assert deny["ci95_low"] == pytest.approx(-32.6321, abs=1e-3)
        assert deny["ci95_high"] == pytest.approx(12.6321, abs=1e-3)
        # GA sıfırı kapsıyor: "engellemek kazandırırdı" iddiası kanıtlanmadı
        assert deny["ci95_low"] < 0.0 < deny["ci95_high"]

    def test_allow_profit_factor(self, ai_dataset):
        allow = _ai_report(ai_dataset)["allow"]
        assert allow["trades"] == 3
        assert allow["pnl"] == pytest.approx(75.0)
        assert allow["avg_pnl"] == pytest.approx(25.0)
        # brüt kâr 125 (100+25) / brüt kayıp 50 -> 2.5
        assert allow["profit_factor"] == pytest.approx(2.5)

    def test_baseline_is_all_closed_trades_not_only_ai_rows(self, ai_dataset):
        baseline = _ai_report(ai_dataset)["baseline"]
        assert baseline["trades"] == 9
        # brüt kâr 10+100+25+5+1 = 141 / brüt kayıp 30+10+50+5 = 95
        assert baseline["profit_factor"] == pytest.approx(141.0 / 95.0)

    def test_ci_is_dash_and_noted_when_n_below_two(self, tmp_path):
        db = _make_ai_db(tmp_path, [
            {"id": 1, "realized_pnl": -30.0,
             "forensics": _ai_block(verdict="deny",
                                    pattern_ids=["E8.7_tv_short_low_pf"])},
            {"id": 2, "realized_pnl": 10.0, "forensics": _ai_block(verdict="allow")},
        ], name="ai_small.db")
        report = _ai_report(db)
        assert report["deny"]["trades"] == 1
        assert report["deny"]["ci95_low"] is None
        assert report["deny"]["ci95_high"] is None
        assert any("güven aralığı" in n for n in report["notes"])
        assert lr._fmt_ci(None, None) == "—"

    def test_mean_ci95_helper_empty_and_single(self):
        assert lr._mean_ci95([]) == (None, None, None)
        mean, low, high = lr._mean_ci95([7.0])
        assert mean == pytest.approx(7.0)
        assert low is None and high is None


class TestAiPearson:
    def test_perfect_positive_and_negative(self):
        assert lr._pearson([1.0, 2.0, 3.0], [2.0, 4.0, 6.0]) == pytest.approx(1.0)
        assert lr._pearson([1.0, 2.0, 3.0], [6.0, 4.0, 2.0]) == pytest.approx(-1.0)

    def test_constant_axis_is_none_not_zero(self):
        # Sabit eksen: "ilişki yok" (0.0) DEĞİL, "ölçülemez" (None).
        assert lr._pearson([0.4, 0.4, 0.4, 0.4], [1.0, -2.0, 3.0, -4.0]) is None
        assert lr._pearson([0.5, 0.5], [1.0, 2.0]) is None

    def test_too_few_points_is_none(self):
        assert lr._pearson([1.0], [2.0]) is None
        assert lr._pearson([1.0, 2.0], [1.0]) is None

    def test_axis_table_marks_linear_axis_and_constant_axes(self, ai_dataset):
        report = _ai_report(ai_dataset)
        by_axis = {row["axis"]: row for row in report["axes"]}
        # ai_gate.AXES'in tamamı tabloda olmalı (veride görünmeyen eksen de)
        assert set(lr.AI_AXES) <= set(by_axis)
        assert by_axis["stop_sanity"]["n"] == 6
        assert by_axis["stop_sanity"]["pearson_r"] == pytest.approx(1.0)
        # 0.4 sabiti kayan noktada TAM sıfır varyans vermez; yine de None olmalı
        assert by_axis["regime_fit"]["pearson_r"] is None
        assert by_axis["crowding"]["pearson_r"] is None
        assert lr._fmt_r(None) == "—"

    def test_axes_match_the_engine_module(self):
        """Script'teki eksen listesi motorunkinden ayrışırsa test kırılır."""
        try:
            from src.strategies.scalper.ai_gate import AXES
        except Exception as exc:  # pragma: no cover - D23 motor modülü yoksa
            pytest.skip(f"ai_gate içe aktarılamadı: {exc}")
        assert lr.AI_AXES == list(AXES)


class TestAiCostAndPatterns:
    def test_latency_percentiles_and_decision_count(self, ai_dataset):
        cost = _ai_report(ai_dataset)["cost"]
        # gecikmeler: 40, 500, 1000, 1500, 2000, 2500, 3000 (n=7)
        assert cost["decisions"] == 7
        assert cost["latency_p50_ms"] == pytest.approx(1500.0)
        assert cost["latency_p95_ms"] == pytest.approx(3000.0)

    def test_token_cost_is_declared_unmeasured_not_invented(self, ai_dataset):
        cost = _ai_report(ai_dataset)["cost"]
        assert cost["tokens"] is None
        assert "ölçülemedi" in cost["cost_note"]

    def test_percentile_helper_edges(self):
        assert lr._percentile([], 50.0) is None
        assert lr._percentile([5.0], 95.0) == pytest.approx(5.0)
        assert lr._percentile([1.0, 2.0, 3.0, 4.0], 50.0) == pytest.approx(2.0)

    def test_pattern_table(self, ai_dataset):
        patterns = {row["pattern"]: row for row in _ai_report(ai_dataset)["patterns"]}
        assert set(patterns) == {"E8.7_tv_short_low_pf", "D21_stale_signal"}
        loser = patterns["E8.7_tv_short_low_pf"]
        assert loser["trades"] == 2
        assert loser["pnl"] == pytest.approx(-40.0)
        assert loser["avg_pnl"] == pytest.approx(-20.0)
        assert loser["profit_factor"] == pytest.approx(0.0)
        winner = patterns["D21_stale_signal"]
        assert winner["trades"] == 1
        assert winner["profit_factor"] == float("inf")

    def test_pattern_rows_may_exceed_trade_count(self, tmp_path):
        db = _make_ai_db(tmp_path, [
            {"id": 1, "realized_pnl": -20.0,
             "forensics": _ai_block(
                 verdict="deny",
                 pattern_ids=["E8.7_tv_short_low_pf", "D21_stale_signal"])},
        ], name="ai_multi.db")
        report = _ai_report(db)
        assert report["coverage"]["closed_trades"] == 1
        assert sum(row["trades"] for row in report["patterns"]) == 2


class TestAiRobustness:
    def test_missing_forensics_column_does_not_crash(self, tmp_path):
        db = _make_db(tmp_path, [
            {"id": 1, "status": "CLOSED", "realized_pnl": 10.0,
             "closed_at": "2026-08-22 10:00:00.000000"},
        ], name="no_forensics.db")
        rows, notes = lr.load_ai_rows(db, _AI_SINCE, _AI_UNTIL)
        assert rows == []
        assert any("forensics sütunu yok" in n for n in notes)
        report = lr.build_ai_report(rows)
        assert report["coverage"]["closed_trades"] == 0
        assert report["deny"]["trades"] == 0
        assert report["axes"][0]["pearson_r"] is None

    def test_broken_forensics_json_does_not_crash(self, tmp_path):
        db = _make_ai_db(tmp_path, [
            {"id": 1, "realized_pnl": -7.0, "forensics": "{bozuk json"},
            {"id": 2, "realized_pnl": 3.0, "forensics": "[1, 2, 3]"},
            {"id": 3, "realized_pnl": 11.0, "forensics": json.dumps({"ai": "metin"})},
            {"id": 4, "realized_pnl": 4.0, "forensics": _ai_block(verdict="allow")},
        ], name="ai_broken.db")
        rows, notes = lr.load_ai_rows(db, _AI_SINCE, _AI_UNTIL)
        assert len(rows) == 4
        assert sum(1 for r in rows if r["has_ai"]) == 1
        assert any("ayrıştırılamadı" in n for n in notes)
        assert any("sözlük değil" in n for n in notes)
        report = lr.build_ai_report(rows)
        assert report["coverage"]["without_ai"] == 3
        # render etmek de çökmemeli
        full = lr.build_report([], {}, _AI_SINCE, _AI_UNTIL, ["2026-08-22"], notes,
                               ai=report)
        assert "5c) AI KARAR KATMANI" in lr.render_text(full)
        assert json.loads(lr.render_json(full))["ai"]["coverage"]["with_ai"] == 1

    def test_empty_ai_report_renders(self, tmp_path):
        db = _make_ai_db(tmp_path, [], name="ai_empty.db")
        report = _ai_report(db)
        full = lr.build_report([], {}, _AI_SINCE, _AI_UNTIL, ["2026-08-22"], [],
                               ai=report)
        assert "5c)" in lr.render_text(full)
        assert "## 5c)" in lr.render_md(full)


class TestAiRendering:
    def _full(self, db: str) -> Dict[str, Any]:
        return lr.build_report(
            [], {}, _AI_SINCE, _AI_UNTIL, ["2026-08-22"], [], ai=_ai_report(db)
        )

    def test_report_has_no_ai_key_when_flag_absent(self, known_dataset):
        db, klines_path = known_dataset
        trades, _ = lr.load_closed_trades(db, datetime(2026, 8, 14), datetime(2026, 8, 17))
        report = lr.build_report(
            trades, {}, datetime(2026, 8, 14), datetime(2026, 8, 17),
            ["2026-08-14"], [],
        )
        assert "ai" not in report
        assert "5c)" not in lr.render_text(report)

    def test_text_section(self, ai_dataset):
        text = lr.render_text(self._full(ai_dataset))
        assert "5c) AI KARAR KATMANI — GÖLGE RAPORU (D23)" in text
        assert "ENGELLENEN (deny)" in text
        assert "İZİN VERİLEN (allow)" in text
        assert "TABAN (tüm işlemler)" in text
        assert "Eksen x PnL korelasyonu" in text
        assert "Kalıp x sonuç" in text
        assert "ölçülemedi" in text

    def test_md_section(self, ai_dataset):
        md = lr.render_md(self._full(ai_dataset))
        assert "## 5c) AI karar katmanı — gölge raporu (D23)" in md
        assert "| Küme | İşlem |" in md
        assert "**Eksen × PnL korelasyonu**" in md
        assert "**Kalıp × sonuç**" in md

    def test_json_has_no_nan_or_inf_literals(self, ai_dataset):
        text = lr.render_json(self._full(ai_dataset))
        assert "NaN" not in text
        assert "Infinity" not in text
        parsed = json.loads(text)
        winner = next(
            p for p in parsed["ai"]["patterns"] if p["pattern"] == "D21_stale_signal"
        )
        assert winner["profit_factor"] == "inf"


class TestAiCli:
    def test_flag_defaults_to_off(self):
        assert lr.parse_args([]).ai is False
        assert lr.parse_args(["--ai"]).ai is True

    def test_main_ai_json_end_to_end(self, tmp_path, ai_dataset, capsys):
        klines_path = _write_klines_json(tmp_path, [
            _kline_row("2026-08-22", 100.0, 100.1),
        ], name="ai_klines.json")
        rc = lr.main([
            "--db", ai_dataset,
            "--since", "2026-08-22 00:00", "--until", "2026-08-22 23:59:59",
            "--btc-klines-json", klines_path, "--ai", "--format", "json",
        ])
        assert rc == 0
        parsed = json.loads(capsys.readouterr().out)
        assert "ai" in parsed
        assert parsed["ai"]["coverage"]["with_ai"] == 7
        assert parsed["ai"]["deny"]["trades"] == 3
        assert parsed["ai"]["allow"]["profit_factor"] == pytest.approx(2.5)
        assert any("E8.6" in note for note in parsed["notes"])

    def test_main_ai_text_end_to_end(self, tmp_path, ai_dataset, capsys):
        klines_path = _write_klines_json(tmp_path, [
            _kline_row("2026-08-22", 100.0, 100.1),
        ], name="ai_klines_text.json")
        rc = lr.main([
            "--db", ai_dataset,
            "--since", "2026-08-22 00:00", "--until", "2026-08-22 23:59:59",
            "--btc-klines-json", klines_path, "--ai", "--format", "text",
        ])
        assert rc == 0
        out = capsys.readouterr().out
        assert "5c) AI KARAR KATMANI" in out
        assert "E8.6 UYARISI" in out

    def test_main_without_ai_flag_has_no_section(self, tmp_path, ai_dataset, capsys):
        klines_path = _write_klines_json(tmp_path, [
            _kline_row("2026-08-22", 100.0, 100.1),
        ], name="ai_klines_off.json")
        rc = lr.main([
            "--db", ai_dataset,
            "--since", "2026-08-22 00:00", "--until", "2026-08-22 23:59:59",
            "--btc-klines-json", klines_path, "--format", "json",
        ])
        assert rc == 0
        parsed = json.loads(capsys.readouterr().out)
        assert "ai" not in parsed
        assert not any("E8.6" in note for note in parsed["notes"])

# D24/A4 — konsantrasyon (canlı defter tarafı)
# --------------------------------------------------------------------------

class TestConcentration:
    def _t(self, pnl: float, symbol: str, day: str, tid: int = 1) -> lr.ClosedTrade:
        closed = datetime.strptime(day, "%Y-%m-%d")
        return lr.ClosedTrade(
            id=tid, strategy="C", symbol=symbol, direction="LONG",
            realized_pnl=pnl, exit_reason="TRAIL", closed_at=closed, day=day,
        )

    def test_shares_match_manual_finding_shape(self):
        """2026-08-21'de ELLE bulunan '+832'nin %68'i 4 günden' tespitinin
        otomatik karşılığı: tek günün payı doğru hesaplanmalı."""
        trades = [
            self._t(600.0, "BTCUSDT", "2026-08-10", 1),
            self._t(200.0, "ETHUSDT", "2026-08-10", 2),
            self._t(200.0, "ETHUSDT", "2026-08-11", 3),
        ]
        out = lr.build_concentration(trades)
        assert out["top_symbol"] == "BTCUSDT"
        assert out["top_symbol_pnl"] == pytest.approx(600.0)
        assert out["top_symbol_pnl_share"] == pytest.approx(60.0)
        assert out["top_trade_pnl"] == pytest.approx(600.0)
        assert out["top_trade_symbol"] == "BTCUSDT"
        assert out["top_day"] == "2026-08-10"
        assert out["top_day_pnl"] == pytest.approx(800.0)
        assert out["top_day_pnl_share"] == pytest.approx(80.0)
        assert out["distinct_symbols"] == 2
        assert out["distinct_days"] == 2

    def test_share_undefined_when_total_not_positive(self):
        trades = [
            self._t(10.0, "BTCUSDT", "2026-08-10", 1),
            self._t(-40.0, "ETHUSDT", "2026-08-11", 2),
        ]
        out = lr.build_concentration(trades)
        assert out["top_symbol_pnl_share"] is None
        assert out["top_day_pnl_share"] is None
        assert out["top_trade_pnl"] == pytest.approx(10.0)

    def test_empty(self):
        out = lr.build_concentration([])
        assert out["top_symbol"] is None
        assert out["distinct_days"] == 0

    def test_wired_into_headline_and_renderers(self):
        trades = [self._t(50.0, "SOLUSDT", "2026-08-10", 1)]
        headline = lr.build_headline(trades, {}, ["2026-08-10"])
        assert headline["concentration"]["top_symbol"] == "SOLUSDT"
        report = lr.build_report(
            trades, {}, datetime(2026, 8, 10), datetime(2026, 8, 11),
            ["2026-08-10"], [],
        )
        text = lr.render_text(report)
        md = lr.render_md(report)
        assert "Yoğunluk/sembol" in text
        assert "SOLUSDT" in text
        assert "Yoğunluk — gün" in md
        payload = json.loads(lr.render_json(report))
        assert payload["headline"]["concentration"]["distinct_days"] == 1

    def test_share_is_not_a_checklist_threshold(self):
        """Konsantrasyon BİLGİ satırıdır: soak kontrol listesine EŞİK olarak
        girmez (aksi halde D#P1 harness/motor paritesi tartışması açılır)."""
        trades = [self._t(1000.0, "BTCUSDT", "2026-08-10", 1)]
        headline = lr.build_headline(trades, {}, ["2026-08-10"])
        names = " ".join(i["name"] for i in lr.build_checklist(
            headline, datetime(2026, 8, 10), datetime(2026, 8, 11)
        ))
        assert "oğunluk" not in names
        assert "onsantrasyon" not in names
