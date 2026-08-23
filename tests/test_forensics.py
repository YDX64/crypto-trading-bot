"""D21 — işlem adli kaydı (trade forensics) testleri.

Kapsam:
  1. Saf etiket kuralları (her etiket için pozitif VE negatif durum).
  2. Gösterge anlık görüntüsü ve özet (etiket × sonuç) toplayıcısı.
  3. Post-mortem: look-ahead YOK (kapanıştan ÖNCEKİ mumlar sayılmaz).
  4. Şema migrasyonunun idempotansı.
  5. Adli kayıt hatasının GİRİŞİ ENGELLEMEDİĞİ (gözlem ≠ güvenlik kilidi).
  6. JSONL akışı: yazım, günlük rotasyon, saklama budaması, fail-safe.
  7. Tracker turu (gerçek geçici SQLite): giriş → çıkış → post-mortem.
  8. HTTP uçları (mock tracker).
  9. `scripts/ledger_report.py --forensics` bölümü ve etiket metni paritesi.
 10. D21-R3 düşmanca inceleme düzeltmeleri (bölüm 13): post-mortem'in safety
     turunu bloklamaması, restart kurtarmasında adli birleştirme, JSONL
     yazımının kilit dışına alınması, maker bağlam kimliği ve uç/rapor/pano
     sertleştirmeleri.
"""

import asyncio
import json
import queue
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# SignalModel'in "WaitingSignalModel" ilişkisi mapper yapılandırmasında
# çözülebilsin diye bu modül de içe aktarılır (aynı desen:
# tests/test_shadow_mode.py, tests/test_scalper_setups.py).
import src.models.waiting_signal  # noqa: F401
from src.core.database import Base
from src.models.scalp_trade import ScalpTradeModel
from src.strategies.scalper import forensics as fx
from src.strategies.scalper import forensics_log
from src.strategies.scalper import tracker as tracker_module
from src.strategies.scalper.executor import ScalpExecutor, ScalpPosition
from src.strategies.scalper.tracker import ScalpTracker
from src.strategies.scalper.types import Candle, Direction, Regime, ScalpSignal


REPO_ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# Yardımcılar
# --------------------------------------------------------------------------

def _entry(**overrides):
    base = {
        "direction": "LONG",
        "leader_gate": {"verdict": "geçti", "day_drift_pct": 0.0,
                        "run_drift_pct": 0.0},
        "tv": None,
        "fill_latency_sec": 1.0,
    }
    base.update(overrides)
    return base


def _exit(**overrides):
    base = {
        "reason": "TRAIL",
        "realized_pnl": 10.0,
        "gross_pnl": 12.0,
        "mfe_roi_pct": 5.0,
        "direction": "LONG",
    }
    base.update(overrides)
    return base


def _candle(close_time_ms: int, high: float, low: float) -> Candle:
    return Candle(
        open_time=close_time_ms - 60_000, open=(high + low) / 2,
        high=high, low=low, close=(high + low) / 2, volume=1.0,
        close_time=close_time_ms,
    )


# --------------------------------------------------------------------------
# 1) Kaynak ailesi
# --------------------------------------------------------------------------

class TestSourceFamily:
    def test_luxalgo_variants_collapse_to_one_family(self):
        assert fx.source_family("luxso_osc") == "luxalgo"
        assert fx.source_family("luxso_trend") == "luxalgo"
        assert fx.source_family("LUXSO") == "luxalgo"

    def test_distinct_vendors_stay_distinct(self):
        assert fx.source_family("algopro") == "algopro"
        assert fx.source_family("pac_choch") == "pac"
        assert fx.source_family("algopro") != fx.source_family("luxso_osc")

    def test_empty_source_is_unknown(self):
        assert fx.source_family("") == "?"
        assert fx.source_family(None) == "?"


# --------------------------------------------------------------------------
# 2) Giriş etiketleri — her biri POZİTİF ve NEGATİF
# --------------------------------------------------------------------------

class TestCounterDriftTags:
    def test_long_while_leader_falls_is_flagged(self):
        entry = _entry(direction="LONG",
                       leader_gate={"verdict": "geçti", "day_drift_pct": -1.8})
        assert fx.TAG_COUNTER_DRIFT_LONG in fx.classify_entry(entry)

    def test_long_within_threshold_is_not_flagged(self):
        entry = _entry(direction="LONG",
                       leader_gate={"verdict": "geçti", "day_drift_pct": -0.4})
        assert fx.TAG_COUNTER_DRIFT_LONG not in fx.classify_entry(entry)

    def test_short_while_leader_rises_is_flagged(self):
        entry = _entry(direction="SHORT",
                       leader_gate={"verdict": "geçti", "day_drift_pct": 2.4})
        assert fx.TAG_RELIEF_RALLY_SHORT in fx.classify_entry(entry)

    def test_short_while_leader_falls_is_not_flagged(self):
        entry = _entry(direction="SHORT",
                       leader_gate={"verdict": "geçti", "day_drift_pct": -2.4})
        tags = fx.classify_entry(entry)
        assert fx.TAG_RELIEF_RALLY_SHORT not in tags
        assert fx.TAG_COUNTER_DRIFT_LONG not in tags

    def test_missing_drift_produces_no_tag(self):
        entry = _entry(leader_gate={"verdict": "kapalı", "day_drift_pct": None})
        assert fx.classify_entry(entry) == []

    def test_zero_threshold_disables_the_rule(self):
        entry = _entry(direction="LONG",
                       leader_gate={"verdict": "geçti", "day_drift_pct": -9.9})
        th = fx.VerdictThresholds(counter_drift_pct=0.0)
        assert fx.classify_entry(entry, th) == []


class TestLateEntryAfterRun:
    def test_long_after_multi_day_rally_is_flagged(self):
        entry = _entry(direction="LONG",
                       leader_gate={"verdict": "geçti", "day_drift_pct": 0.0,
                                    "run_drift_pct": 7.5})
        assert fx.TAG_LATE_ENTRY_AFTER_RUN in fx.classify_entry(entry)

    def test_short_after_multi_day_selloff_is_flagged(self):
        entry = _entry(direction="SHORT",
                       leader_gate={"verdict": "geçti", "day_drift_pct": 0.0,
                                    "run_drift_pct": -7.5})
        assert fx.TAG_LATE_ENTRY_AFTER_RUN in fx.classify_entry(entry)

    def test_long_after_selloff_is_not_late_entry(self):
        entry = _entry(direction="LONG",
                       leader_gate={"verdict": "geçti", "day_drift_pct": 0.0,
                                    "run_drift_pct": -7.5})
        assert fx.TAG_LATE_ENTRY_AFTER_RUN not in fx.classify_entry(entry)

    def test_small_run_is_not_flagged(self):
        entry = _entry(direction="LONG",
                       leader_gate={"verdict": "geçti", "day_drift_pct": 0.0,
                                    "run_drift_pct": 1.0})
        assert fx.TAG_LATE_ENTRY_AFTER_RUN not in fx.classify_entry(entry)


class TestTvSingleFamily:
    def test_two_votes_from_one_vendor_are_flagged(self):
        entry = _entry(tv={"sources": ["luxso_osc", "luxso_trend"], "votes": 2})
        assert fx.TAG_TV_SINGLE_FAMILY in fx.classify_entry(entry)

    def test_two_votes_from_distinct_vendors_are_clean(self):
        entry = _entry(tv={"sources": ["luxso_osc", "algopro"], "votes": 2})
        assert fx.TAG_TV_SINGLE_FAMILY not in fx.classify_entry(entry)

    def test_single_vote_is_not_flagged(self):
        entry = _entry(tv={"sources": ["luxso_osc"], "votes": 1})
        assert fx.TAG_TV_SINGLE_FAMILY not in fx.classify_entry(entry)

    def test_no_tv_block_is_not_flagged(self):
        assert fx.TAG_TV_SINGLE_FAMILY not in fx.classify_entry(_entry())


class TestStaleSignal:
    def test_slow_fill_is_flagged(self):
        entry = _entry(fill_latency_sec=91.0)
        assert fx.TAG_STALE_SIGNAL in fx.classify_entry(entry)

    def test_fast_fill_is_not_flagged(self):
        entry = _entry(fill_latency_sec=1.2)
        assert fx.TAG_STALE_SIGNAL not in fx.classify_entry(entry)

    def test_unknown_latency_is_not_flagged(self):
        entry = _entry(fill_latency_sec=None)
        assert fx.TAG_STALE_SIGNAL not in fx.classify_entry(entry)


class TestGateBypassed:
    def test_gate_enabled_but_ineffective_is_flagged(self):
        entry = _entry(leader_gate={"verdict": "etkin_değil",
                                    "day_drift_pct": None})
        assert fx.TAG_GATE_BYPASSED in fx.classify_entry(entry)

    def test_gate_disabled_is_not_flagged(self):
        entry = _entry(leader_gate={"verdict": "kapalı"})
        assert fx.TAG_GATE_BYPASSED not in fx.classify_entry(entry)

    def test_gate_effective_is_not_flagged(self):
        entry = _entry(leader_gate={"verdict": "geçti", "day_drift_pct": 0.1})
        assert fx.TAG_GATE_BYPASSED not in fx.classify_entry(entry)

    def test_leader_gate_snapshot_three_states(self):
        assert fx.leader_gate_snapshot(
            {"enabled": False, "gate_effective": False}
        )["verdict"] == "kapalı"
        assert fx.leader_gate_snapshot(
            {"enabled": True, "gate_effective": True}
        )["verdict"] == "geçti"
        assert fx.leader_gate_snapshot(
            {"enabled": True, "gate_effective": False}
        )["verdict"] == "etkin_değil"


# --------------------------------------------------------------------------
# 3) Çıkış etiketleri
# --------------------------------------------------------------------------

class TestFeeDominated:
    def test_fees_eating_most_of_a_winner_is_flagged(self):
        tags = fx.classify_exit(_entry(), _exit(gross_pnl=10.0, realized_pnl=3.0))
        assert fx.TAG_FEE_DOMINATED in tags

    def test_fees_flipping_a_winner_to_a_loss_is_flagged(self):
        tags = fx.classify_exit(_entry(), _exit(gross_pnl=4.0, realized_pnl=-1.0))
        assert fx.TAG_FEE_DOMINATED in tags

    def test_healthy_winner_is_not_flagged(self):
        tags = fx.classify_exit(_entry(), _exit(gross_pnl=10.0, realized_pnl=9.4))
        assert fx.TAG_FEE_DOMINATED not in tags

    def test_losing_trade_is_not_fee_dominated(self):
        # Brüt zaten negatifse "ücret kârı yedi" demek anlamsızdır.
        tags = fx.classify_exit(
            _entry(), _exit(gross_pnl=-50.0, realized_pnl=-52.0, mfe_roi_pct=0.0)
        )
        assert fx.TAG_FEE_DOMINATED not in tags


class TestMfeGiveback:
    def test_reached_tp1_target_then_lost_is_flagged(self):
        tags = fx.classify_exit(
            _entry(), _exit(mfe_roi_pct=24.0, realized_pnl=-30.0, gross_pnl=-28.0)
        )
        assert fx.TAG_MFE_GIVEBACK in tags

    def test_reached_target_and_won_is_not_flagged(self):
        tags = fx.classify_exit(
            _entry(), _exit(mfe_roi_pct=24.0, realized_pnl=30.0, gross_pnl=31.0)
        )
        assert fx.TAG_MFE_GIVEBACK not in tags

    def test_small_mfe_loss_is_not_giveback(self):
        tags = fx.classify_exit(
            _entry(), _exit(mfe_roi_pct=3.0, realized_pnl=-30.0, gross_pnl=-28.0)
        )
        assert fx.TAG_MFE_GIVEBACK not in tags


class TestClassifyCombination:
    def test_entry_and_exit_tags_are_merged_and_deduplicated(self):
        entry = _entry(direction="LONG",
                       leader_gate={"verdict": "etkin_değil",
                                    "day_drift_pct": -2.0})
        exit_ = _exit(gross_pnl=10.0, realized_pnl=1.0)
        tags = fx.classify(entry, exit_)
        assert tags.count(fx.TAG_COUNTER_DRIFT_LONG) == 1
        assert fx.TAG_GATE_BYPASSED in tags
        assert fx.TAG_FEE_DOMINATED in tags

    def test_every_tag_has_a_turkish_label_and_stage(self):
        for tag in fx.ALL_TAGS:
            assert fx.TAG_LABELS[tag]
            assert fx.TAG_STAGE[tag] in ("entry", "exit", "postmortem")


class TestThresholdsFromCfg:
    def test_cfg_values_override_defaults(self):
        cfg = SimpleNamespace(
            scalper_forensics_counter_drift_pct=2.5,
            scalper_forensics_run_pct=9.0,
            scalper_forensics_stale_signal_sec=5.0,
            scalper_forensics_fee_ratio=0.25,
            scalper_tp1_roi=8.0,
            scalper_forensics_postmortem_min=15.0,
        )
        th = fx.thresholds_from_cfg(cfg)
        assert th.counter_drift_pct == 2.5
        assert th.run_pct == 9.0
        assert th.stale_signal_sec == 5.0
        assert th.fee_ratio == 0.25
        assert th.giveback_roi_pct == 8.0
        assert th.noise_window_min == 15.0

    def test_missing_fields_fall_back_to_defaults(self):
        th = fx.thresholds_from_cfg(SimpleNamespace())
        assert th == fx.VerdictThresholds()


# --------------------------------------------------------------------------
# 4) Post-mortem — look-ahead YOK
# --------------------------------------------------------------------------

class TestPostmortem:
    def test_candles_before_close_are_ignored(self):
        """Look-ahead güvencesi: kapanıştan ÖNCEKİ mumlar HİÇ sayılmaz."""
        closed_ms = 1_000_000
        candles = [
            _candle(closed_ms - 120_000, high=200.0, low=50.0),   # önce
            _candle(closed_ms - 60_000, high=200.0, low=50.0),    # önce
        ]
        out = fx.postmortem_from_candles(
            entry=_entry(fill_price=100.0),
            exit_=_exit(reason="SL", realized_pnl=-10.0),
            candles=candles, closed_at_ms=closed_ms,
        )
        assert out["candles_seen"] == 0
        assert out["returned_to_entry"] is None
        assert out["tags"] == []

    def test_long_stop_then_price_returns_is_noise_stop(self):
        closed_ms = 1_000_000
        candles = [
            _candle(closed_ms + 60_000, high=99.0, low=98.0),
            _candle(closed_ms + 120_000, high=101.5, low=99.0),   # girişi geçti
        ]
        out = fx.postmortem_from_candles(
            entry=_entry(fill_price=100.0),
            exit_=_exit(reason="SL", realized_pnl=-10.0),
            candles=candles, closed_at_ms=closed_ms,
        )
        assert out["returned_to_entry"] is True
        assert out["minutes_to_return"] == 2.0
        assert out["max_favorable_pct"] == pytest.approx(1.5)
        assert out["tags"] == [fx.TAG_NOISE_STOP]

    def test_price_never_returns_is_not_noise_stop(self):
        closed_ms = 1_000_000
        candles = [
            _candle(closed_ms + 60_000, high=98.0, low=95.0),
            _candle(closed_ms + 120_000, high=97.0, low=94.0),
        ]
        out = fx.postmortem_from_candles(
            entry=_entry(fill_price=100.0),
            exit_=_exit(reason="SL", realized_pnl=-10.0),
            candles=candles, closed_at_ms=closed_ms,
        )
        assert out["returned_to_entry"] is False
        assert out["tags"] == []

    def test_short_direction_uses_lows(self):
        closed_ms = 1_000_000
        candles = [_candle(closed_ms + 60_000, high=105.0, low=99.0)]
        out = fx.postmortem_from_candles(
            entry=_entry(direction="SHORT", fill_price=100.0),
            exit_=_exit(reason="SL", realized_pnl=-10.0, direction="SHORT"),
            candles=candles, closed_at_ms=closed_ms,
        )
        assert out["returned_to_entry"] is True
        assert out["tags"] == [fx.TAG_NOISE_STOP]

    def test_winning_trade_never_gets_noise_stop(self):
        closed_ms = 1_000_000
        candles = [_candle(closed_ms + 60_000, high=120.0, low=99.0)]
        out = fx.postmortem_from_candles(
            entry=_entry(fill_price=100.0),
            exit_=_exit(reason="TRAIL", realized_pnl=25.0),
            candles=candles, closed_at_ms=closed_ms,
        )
        assert out["returned_to_entry"] is True
        assert out["tags"] == []      # kâr ettik; "gürültü stopu" değil

    def test_candles_after_the_window_are_ignored(self):
        closed_ms = 1_000_000
        th = fx.VerdictThresholds(noise_window_min=1.0)
        candles = [_candle(closed_ms + 300_000, high=150.0, low=99.0)]
        out = fx.postmortem_from_candles(
            entry=_entry(fill_price=100.0),
            exit_=_exit(reason="SL", realized_pnl=-10.0),
            candles=candles, closed_at_ms=closed_ms, th=th,
        )
        assert out["candles_seen"] == 0
        assert out["tags"] == []

    def test_zero_window_disables_postmortem(self):
        th = fx.VerdictThresholds(noise_window_min=0.0)
        out = fx.postmortem_from_candles(
            entry=_entry(fill_price=100.0),
            exit_=_exit(reason="SL", realized_pnl=-10.0),
            candles=[_candle(1_060_000, high=150.0, low=99.0)],
            closed_at_ms=1_000_000, th=th,
        )
        assert out["candles_seen"] == 0


# --------------------------------------------------------------------------
# 5) Gösterge anlık görüntüsü ve giriş/çıkış kurucuları
# --------------------------------------------------------------------------

def _series(n: int, start: float = 100.0, step: float = 0.5):
    candles = []
    price = start
    for i in range(n):
        price += step if i % 3 else -step
        candles.append(_candle((i + 1) * 60_000, high=price + 1, low=price - 1))
    return candles


class TestIndicatorSnapshot:
    def test_snapshot_extracts_rsi_bollinger_and_atr(self):
        ctx = SimpleNamespace(
            candles_5m=_series(120), candles_15m=_series(80),
            candles_4h=[], current_price=100.0, atr_5m=0.5,
        )
        snap = fx.indicator_snapshot(ctx, SimpleNamespace(scalper_tf_entry="1m"))
        assert snap["rsi_entry"] is not None
        assert snap["bb_percent_b"] is not None
        assert snap["atr_pct"] == pytest.approx(0.5)
        assert snap["rsi_context"] is not None
        assert snap["tf_entry"] == "1m"
        # 200 mumdan az rejim serisi → EMA yayımlanmaz (uydurma yok).
        assert "ema200" not in snap

    def test_regime_emas_appear_with_enough_candles(self):
        ctx = SimpleNamespace(
            candles_5m=_series(60), candles_15m=[], candles_4h=_series(220),
            current_price=100.0, atr_5m=0.5,
        )
        snap = fx.indicator_snapshot(ctx, None)
        assert snap["ema50"] is not None
        assert snap["ema200"] is not None

    def test_empty_context_is_safe(self):
        ctx = SimpleNamespace(
            candles_5m=[], candles_15m=[], candles_4h=[],
            current_price=0.0, atr_5m=0.0,
        )
        assert fx.indicator_snapshot(ctx, None) is not None


class TestBuildEntryExit:
    def _signal(self, direction=Direction.LONG):
        return ScalpSignal(
            strategy="C", symbol="TESTUSDT", direction=direction,
            entry_price=100.0, stop_price=99.0, reason="test",
            regime=Regime.RANGE, atr_5m=1.0,
        )

    def test_slippage_sign_is_normalized_against_direction(self):
        long_entry = fx.build_entry(
            at="now", signal=self._signal(Direction.LONG), ctx=None,
            cfg=SimpleNamespace(), fill_price=100.5, quantity=1.0, leverage=10,
            margin_usdt=10.0, stop_price=99.0, tp1_price=101.0, tp2_price=102.0,
        )
        short_entry = fx.build_entry(
            at="now", signal=self._signal(Direction.SHORT), ctx=None,
            cfg=SimpleNamespace(), fill_price=100.5, quantity=1.0, leverage=10,
            margin_usdt=10.0, stop_price=101.0, tp1_price=99.0, tp2_price=98.0,
        )
        # LONG'da yukarı dolum ALEYHTEDİR (+), SHORT'ta LEHTEDİR (−).
        assert long_entry["slippage_pct"] == pytest.approx(0.5)
        assert short_entry["slippage_pct"] == pytest.approx(-0.5)

    def test_stop_distance_and_roi_are_derived_from_the_fill(self):
        entry = fx.build_entry(
            at="now", signal=self._signal(), ctx=None, cfg=SimpleNamespace(),
            fill_price=100.0, quantity=2.0, leverage=20, margin_usdt=10.0,
            stop_price=99.0, tp1_price=101.0, tp2_price=102.0,
        )
        assert entry["stop_distance_pct"] == pytest.approx(1.0)
        assert entry["stop_roi_pct"] == pytest.approx(20.0)
        assert entry["notional_usdt"] == pytest.approx(200.0)

    def test_exit_derives_fee_and_price_move(self):
        exit_doc = fx.build_exit(
            at="now", reason="SL", exit_price=99.0, entry_price=100.0,
            quantity=1.0, leverage=10, direction=Direction.LONG,
            realized_pnl=-1.2, gross_pnl=-1.0, pnl_source="binance_income_net",
            mae_roi_pct=-10.0, mfe_roi_pct=2.0, duration_sec=600.0,
        )
        assert exit_doc["fee_estimate"] == pytest.approx(0.2)
        assert exit_doc["price_move_pct"] == pytest.approx(-1.0)
        assert exit_doc["mae_price_pct"] == pytest.approx(-1.0)

    def test_unknown_context_keys_survive_into_the_document(self):
        entry = fx.build_entry(
            at="now", signal=self._signal(), ctx=None, cfg=SimpleNamespace(),
            fill_price=100.0, quantity=1.0, leverage=10, margin_usdt=10.0,
            stop_price=99.0, tp1_price=101.0, tp2_price=102.0,
            open_positions=2, daily_pnl=-5.0, btc_price=112000.0,
        )
        assert entry["open_positions"] == 2
        assert entry["daily_pnl"] == pytest.approx(-5.0)
        assert entry["btc_price"] == pytest.approx(112000.0)


# --------------------------------------------------------------------------
# 6) Özet
# --------------------------------------------------------------------------

class TestSummarize:
    def test_tag_rows_split_pnl_and_untagged_row_is_the_baseline(self):
        rows = [
            {"tags": ["counter_drift_long"], "pnl": -500.0},
            {"tags": ["counter_drift_long", "noise_stop"], "pnl": -300.0},
            {"tags": [], "pnl": 120.0},
        ]
        summary = fx.summarize(rows)
        by_tag = {row["tag"]: row for row in summary["tags"]}
        assert summary["trades"] == 3
        assert by_tag["counter_drift_long"]["trades"] == 2
        assert by_tag["counter_drift_long"]["pnl"] == pytest.approx(-800.0)
        assert by_tag["noise_stop"]["trades"] == 1
        assert by_tag["_etiketsiz_"]["trades"] == 1
        assert by_tag["_etiketsiz_"]["winrate"] == pytest.approx(100.0)

    def test_rows_are_sorted_worst_first(self):
        rows = [
            {"tags": ["fee_dominated"], "pnl": 5.0},
            {"tags": ["noise_stop"], "pnl": -900.0},
        ]
        summary = fx.summarize(rows)
        assert summary["tags"][0]["tag"] == "noise_stop"

    def test_duplicate_tags_on_one_trade_count_once(self):
        summary = fx.summarize([{"tags": ["noise_stop", "noise_stop"], "pnl": -1.0}])
        assert summary["tags"][0]["trades"] == 1

    def test_empty_input_is_safe(self):
        assert fx.summarize([]) == {"trades": 0, "total_pnl": 0.0, "tags": []}


# --------------------------------------------------------------------------
# 7) Şema migrasyonu
# --------------------------------------------------------------------------

class TestMigration:
    def test_forensics_column_is_added_idempotently(self):
        from sqlalchemy import create_engine, inspect as sa_inspect, text

        from src.core.database import _ensure_schema_migrations

        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE scalp_trades (id INTEGER PRIMARY KEY)"))
            _ensure_schema_migrations(conn)
            columns = {c["name"] for c in sa_inspect(conn).get_columns("scalp_trades")}
            assert "forensics" in columns
            # İkinci ve üçüncü çağrı hata üretmemeli (idempotent).
            _ensure_schema_migrations(conn)
            _ensure_schema_migrations(conn)
            conn.execute(text("INSERT INTO scalp_trades (id) VALUES (1)"))
            row = conn.execute(text("SELECT forensics FROM scalp_trades")).first()
            assert row[0] is None      # mevcut satırlar NULL kalır

    def test_missing_table_is_a_no_op(self):
        from sqlalchemy import create_engine

        from src.core.database import _ensure_schema_migrations

        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as conn:
            _ensure_schema_migrations(conn)   # tablo yok → sessiz dönüş


# --------------------------------------------------------------------------
# 8) JSONL akışı
# --------------------------------------------------------------------------

class TestForensicsLog:
    def test_append_writes_one_json_line_per_event(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRADINGBOT_LOG_DIR", str(tmp_path))
        forensics_log.reset_error_state()

        assert forensics_log.append("entry", {"trade_id": 1, "symbol": "BTCUSDT"})
        assert forensics_log.append("exit", {"trade_id": 1, "verdict": ["noise_stop"]})

        lines = (tmp_path / "trades.jsonl").read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        first = json.loads(lines[0])
        assert first["event"] == "entry"
        assert first["trade_id"] == 1
        assert "ts" in first
        assert json.loads(lines[1])["verdict"] == ["noise_stop"]

    def test_daily_rotation_archives_the_previous_day(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRADINGBOT_LOG_DIR", str(tmp_path))
        forensics_log.reset_error_state()
        yesterday = time.time() - 86_400

        forensics_log.append("entry", {"trade_id": 1}, now=yesterday)
        path = tmp_path / "trades.jsonl"
        import os
        os.utime(path, (yesterday, yesterday))

        forensics_log.append("entry", {"trade_id": 2})

        day = datetime.fromtimestamp(yesterday, tz=timezone.utc).strftime("%Y-%m-%d")
        archive = tmp_path / f"trades-{day}.jsonl"
        assert archive.exists()
        assert json.loads(archive.read_text().strip())["trade_id"] == 1
        assert json.loads(path.read_text().strip())["trade_id"] == 2

    def test_retention_prunes_archives_older_than_30_days(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRADINGBOT_LOG_DIR", str(tmp_path))
        forensics_log.reset_error_state()
        now = time.time()
        old_day = (datetime.fromtimestamp(now, tz=timezone.utc)
                   - timedelta(days=45)).strftime("%Y-%m-%d")
        recent_day = (datetime.fromtimestamp(now, tz=timezone.utc)
                      - timedelta(days=3)).strftime("%Y-%m-%d")
        (tmp_path / f"trades-{old_day}.jsonl").write_text("{}\n")
        (tmp_path / f"trades-{recent_day}.jsonl").write_text("{}\n")

        # Rotasyonu tetiklemek için dünkü damgalı bir ana dosya bırak.
        path = tmp_path / "trades.jsonl"
        path.write_text('{"event":"entry"}\n')
        import os
        stamp = now - 86_400
        os.utime(path, (stamp, stamp))
        forensics_log.append("entry", {"trade_id": 3}, now=now)

        assert not (tmp_path / f"trades-{old_day}.jsonl").exists()
        assert (tmp_path / f"trades-{recent_day}.jsonl").exists()

    def test_write_failure_is_swallowed(self, tmp_path, monkeypatch):
        # Log dizini olarak bir DOSYA verilir → mkdir/açma hata verir.
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("x")
        monkeypatch.setenv("TRADINGBOT_LOG_DIR", str(blocker))
        forensics_log.reset_error_state()

        assert forensics_log.append("entry", {"trade_id": 1}) is False
        # İkinci hata da sessizdir (tek sefer uyarı bayrağı).
        assert forensics_log.append("entry", {"trade_id": 2}) is False


# --------------------------------------------------------------------------
# 9) Tracker turu — gerçek geçici SQLite
# --------------------------------------------------------------------------

@pytest.fixture
async def real_tracker(tmp_path, monkeypatch):
    db_path = tmp_path / "forensics_test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(tracker_module, "AsyncSessionLocal", session_maker)
    tracker = ScalpTracker()
    try:
        yield tracker
    finally:
        await engine.dispose()


def _tracker_signal():
    return ScalpSignal(
        strategy="C", symbol="TESTUSDT", direction=Direction.LONG,
        entry_price=100.0, stop_price=99.0, reason="test girişi",
        regime=Regime.RANGE, atr_5m=1.0,
    )


class TestTrackerForensicsRoundtrip:
    async def test_entry_exit_and_postmortem_are_merged_into_one_document(
        self, real_tracker
    ):
        trade_id = await real_tracker.record_open(
            signal=_tracker_signal(), entry_price=100.0, quantity=1.0,
            leverage=10, margin_usdt=10.0, sl_algo_id="1", tp1_algo_id="2",
            tp2_algo_id="3",
            forensics={"entry": {"direction": "LONG", "fill_price": 100.0},
                       "verdict": ["counter_drift_long"]},
        )

        row = await real_tracker.forensics_for(trade_id)
        assert row["has_forensics"] is True
        assert row["entry"]["fill_price"] == 100.0
        assert row["verdict"] == ["counter_drift_long"]

        await real_tracker.record_close(
            trade_id, exit_price=99.0, realized_pnl=-10.0, exit_reason="SL",
            pnl_source="binance_income_net",
            forensics_exit={"reason": "SL", "realized_pnl": -10.0},
            verdict=["counter_drift_long", "fee_dominated"],
        )
        row = await real_tracker.forensics_for(trade_id)
        assert row["entry"]["fill_price"] == 100.0      # giriş KORUNDU
        assert row["exit"]["reason"] == "SL"
        assert row["verdict"] == ["counter_drift_long", "fee_dominated"]

        assert await real_tracker.record_postmortem(
            trade_id, {"returned_to_entry": True, "tags": ["noise_stop"]}
        )
        row = await real_tracker.forensics_for(trade_id)
        assert row["postmortem"]["returned_to_entry"] is True
        assert "noise_stop" in row["verdict"]
        assert row["verdict"].count("noise_stop") == 1

    async def test_close_merges_verdicts_instead_of_overwriting(
        self, real_tracker
    ):
        """Restart sonrası kurtarılan pozisyonda giriş etiketleri KAYBOLMAZ."""
        trade_id = await real_tracker.record_open(
            signal=_tracker_signal(), entry_price=100.0, quantity=1.0,
            leverage=10, margin_usdt=10.0, sl_algo_id=None, tp1_algo_id=None,
            tp2_algo_id=None,
            forensics={"entry": {"direction": "LONG"},
                       "verdict": ["counter_drift_long", "gate_bypassed"]},
        )
        # `exits.recover()` sonrası bellekte giriş belgesi YOK → kapanışta
        # yalnız çıkış etiketleri türetilebilir.
        await real_tracker.record_close(
            trade_id, exit_price=99.0, realized_pnl=-10.0, exit_reason="SL",
            forensics_exit={"reason": "SL"}, verdict=["fee_dominated"],
        )
        row = await real_tracker.forensics_for(trade_id)
        assert row["verdict"] == [
            "counter_drift_long", "gate_bypassed", "fee_dominated"
        ]

    async def test_row_without_forensics_reports_has_forensics_false(
        self, real_tracker
    ):
        trade_id = await real_tracker.record_open(
            signal=_tracker_signal(), entry_price=100.0, quantity=1.0,
            leverage=10, margin_usdt=10.0, sl_algo_id=None, tp1_algo_id=None,
            tp2_algo_id=None,
        )
        row = await real_tracker.forensics_for(trade_id)
        assert row["has_forensics"] is False
        assert row["verdict"] == []

    async def test_missing_trade_returns_none(self, real_tracker):
        assert await real_tracker.forensics_for(9999) is None

    async def test_corrupt_json_does_not_raise(self, real_tracker):
        trade_id = await real_tracker.record_open(
            signal=_tracker_signal(), entry_price=100.0, quantity=1.0,
            leverage=10, margin_usdt=10.0, sl_algo_id=None, tp1_algo_id=None,
            tp2_algo_id=None,
        )
        async with tracker_module.AsyncSessionLocal() as session:
            trade = await session.get(ScalpTradeModel, trade_id)
            trade.forensics = "{bozuk json"
            await session.commit()

        row = await real_tracker.forensics_for(trade_id)
        assert row["has_forensics"] is False

    async def test_recent_and_summary(self, real_tracker):
        for tags, pnl in ((["counter_drift_long"], -500.0), ([], 40.0)):
            trade_id = await real_tracker.record_open(
                signal=_tracker_signal(), entry_price=100.0, quantity=1.0,
                leverage=10, margin_usdt=10.0, sl_algo_id=None,
                tp1_algo_id=None, tp2_algo_id=None,
                forensics={"entry": {"direction": "LONG"}, "verdict": tags},
            )
            await real_tracker.record_close(
                trade_id, exit_price=99.0, realized_pnl=pnl,
                exit_reason="SL" if pnl < 0 else "TRAIL",
                forensics_exit={"reason": "SL"}, verdict=tags,
            )

        recent = await real_tracker.recent_forensics(limit=10)
        assert len(recent) == 2

        summary = await real_tracker.forensics_summary()
        by_tag = {row["tag"]: row for row in summary["tags"]}
        assert by_tag["counter_drift_long"]["pnl"] == pytest.approx(-500.0)
        assert by_tag["counter_drift_long"]["label"]
        assert summary["with_forensics"] == 2
        assert summary["without_forensics"] == 0

    async def test_postmortem_candidates_respect_the_window(self, real_tracker):
        trade_id = await real_tracker.record_open(
            signal=_tracker_signal(), entry_price=100.0, quantity=1.0,
            leverage=10, margin_usdt=10.0, sl_algo_id=None, tp1_algo_id=None,
            tp2_algo_id=None,
            forensics={"entry": {"direction": "LONG"}, "verdict": []},
        )
        await real_tracker.record_close(
            trade_id, exit_price=99.0, realized_pnl=-10.0, exit_reason="SL",
            forensics_exit={"reason": "SL"}, verdict=[],
        )

        now = datetime.utcnow()
        # Pencere DOLMADI → aday yok (look-ahead'e karşı ilk savunma).
        assert await real_tracker.postmortem_candidates(
            now=now, min_age_minutes=60.0
        ) == []
        # Pencere dolmuş gibi ileri bir "şimdi" ile → aday çıkar.
        candidates = await real_tracker.postmortem_candidates(
            now=now + timedelta(minutes=61), min_age_minutes=60.0
        )
        assert [c["id"] for c in candidates] == [trade_id]

        await real_tracker.record_postmortem(trade_id, {"tags": []})
        # Bir kez doldurulduktan sonra tekrar aday OLMAZ.
        assert await real_tracker.postmortem_candidates(
            now=now + timedelta(minutes=61), min_age_minutes=60.0
        ) == []

    async def test_zero_window_disables_candidate_scan(self, real_tracker):
        assert await real_tracker.postmortem_candidates(
            now=datetime.utcnow(), min_age_minutes=0.0
        ) == []


# --------------------------------------------------------------------------
# 10) Adli kayıt hatası GİRİŞİ ENGELLEMEZ
# --------------------------------------------------------------------------

@dataclass
class _ExecCfg:
    scalper_min_stop_pct: float = 0.15
    scalper_max_stop_pct: float = 3.0
    scalper_min_rr: float = 1.2
    scalper_risk_percentage: float = 2.0
    scalper_leverage: int = 20
    scalper_tp1_roi: float = 20.0
    scalper_tp1_fraction: float = 0.40
    scalper_tp2_roi: float = 50.0
    scalper_tp2_fraction: float = 0.30
    scalper_breakeven_buffer_pct: float = 0.05
    scalper_chandelier_atr_mult: float = 2.5
    scalper_forensics_enabled: bool = True


class _FakeClient:
    def __init__(self, balance: float = 10_000.0):
        self.balance = balance

    async def get_account_balance(self):
        return self.balance

    async def quantize_quantity(self, symbol, quantity):
        return quantity

    async def validate_order(self, symbol, quantity, price):
        return None

    async def set_margin_type(self, symbol, margin_type="ISOLATED"):
        return None

    async def set_leverage(self, symbol, leverage):
        return None

    async def open_market_order(self, symbol, side, quantity):
        return {"orderId": 111}

    async def place_take_profit(self, symbol, side, stop_price, quantity):
        return {"orderId": 222}


class _FakePm:
    async def resolve_fill(self, symbol, entry_order):
        return 100.0, 1.0

    async def place_stop_loss_or_close(self, symbol, sl_side, stop_price, **kw):
        return {"orderId": 333}


class _FakeTracker:
    def __init__(self):
        self.forensics_seen = "UNSET"

    async def record_open(self, **kwargs):
        self.forensics_seen = kwargs.get("forensics")
        return 42


def _exec_signal():
    return ScalpSignal(
        strategy="C", symbol="TESTUSDT", direction=Direction.LONG,
        entry_price=100.0, stop_price=99.5, reason="test",
        regime=Regime.RANGE, atr_5m=1.0, risk_multiplier=1.0,
    )


def _exec_ctx():
    return SimpleNamespace(
        symbol="TESTUSDT", regime=Regime.RANGE, candles_4h=[], candles_15m=[],
        candles_5m=[], current_price=100.0, atr_5m=1.0, leverage=20,
    )


class TestForensicsNeverBlocksTrading:
    async def test_entry_succeeds_when_forensics_builder_raises(self, monkeypatch):
        def _boom(**kwargs):
            raise RuntimeError("adli kayıt patladı")

        monkeypatch.setattr(fx, "build_entry", _boom)
        tracker = _FakeTracker()
        executor = ScalpExecutor(
            client=_FakeClient(), pm=_FakePm(), tracker=tracker, cfg=_ExecCfg()
        )

        result = await executor.try_open(
            _exec_signal(), _exec_ctx(), forensics={"source": "C"}
        )

        assert isinstance(result, ScalpPosition)     # POZİSYON AÇILDI
        assert tracker.forensics_seen is None        # kayıt yazılmadı, akış sürdü
        assert executor._forensics_error_logged is True

    async def test_entry_succeeds_when_jsonl_sink_raises(self, monkeypatch):
        def _boom(event, payload, **kwargs):
            raise OSError("disk dolu")

        monkeypatch.setattr(forensics_log, "append", _boom)
        tracker = _FakeTracker()
        executor = ScalpExecutor(
            client=_FakeClient(), pm=_FakePm(), tracker=tracker, cfg=_ExecCfg()
        )

        result = await executor.try_open(
            _exec_signal(), _exec_ctx(), forensics={"source": "C"}
        )
        assert isinstance(result, ScalpPosition)
        assert tracker.forensics_seen is not None    # DB kaydı yine de yazıldı

    async def test_disabled_flag_writes_nothing_but_still_opens(self):
        tracker = _FakeTracker()
        cfg = _ExecCfg(scalper_forensics_enabled=False)
        executor = ScalpExecutor(
            client=_FakeClient(), pm=_FakePm(), tracker=tracker, cfg=cfg
        )

        result = await executor.try_open(
            _exec_signal(), _exec_ctx(), forensics={"source": "C"}
        )
        assert isinstance(result, ScalpPosition)
        assert tracker.forensics_seen is None

    async def test_default_call_without_forensics_is_unchanged(self):
        tracker = _FakeTracker()
        executor = ScalpExecutor(
            client=_FakeClient(), pm=_FakePm(), tracker=tracker, cfg=_ExecCfg()
        )
        result = await executor.try_open(_exec_signal(), _exec_ctx())
        assert isinstance(result, ScalpPosition)
        # Bağlam verilmese bile belge kurulur (gerçek dolum sayıları vardır).
        assert tracker.forensics_seen is not None
        assert tracker.forensics_seen["entry"]["fill_price"] == pytest.approx(100.0)

    async def test_gate_results_are_recorded_into_the_context(self):
        tracker = _FakeTracker()
        executor = ScalpExecutor(
            client=_FakeClient(), pm=_FakePm(), tracker=tracker, cfg=_ExecCfg()
        )
        await executor.try_open(_exec_signal(), _exec_ctx(), forensics={})
        gates = tracker.forensics_seen["entry"]["gates"]
        assert gates["stop_distance"] == "passed"
        assert gates["min_rr"] == "passed"
        assert tracker.forensics_seen["entry"]["rr"] is not None


# --------------------------------------------------------------------------
# 10a) Motor tarafı: bağlam kurulumu ve post-mortem turu
# --------------------------------------------------------------------------

def _forensics_engine(cfg=None, **attrs):
    """`ScalperEngine`i __init__ çalıştırmadan kur (repo konvansiyonu:
    tests/test_runtime_liveness.py)."""
    from src.core.logger import app_logger
    from src.strategies.scalper.engine import ScalperEngine

    engine = ScalperEngine.__new__(ScalperEngine)
    engine.cfg = cfg or _ExecCfg()
    engine.logger = app_logger
    engine._forensics_error_logged = False
    engine._forensics_postmortem_at = 0.0
    engine._regimes = {"TESTUSDT": "RANGE"}
    engine._daily_pnl = -12.0
    engine._market_gate_cache = {}
    for key, value in attrs.items():
        setattr(engine, key, value)
    return engine


class TestEngineForensicsContext:
    def test_context_is_built_without_any_market_data_call(self):
        """Sözleşme: giriş bağlamı YENİ REST çağrısı YAPMAZ."""
        def _explode(*args, **kwargs):
            raise AssertionError("adli kayıt REST çağrısı yapmamalı")

        engine = _forensics_engine(
            fetcher=SimpleNamespace(get_klines=_explode, get_price=_explode),
        )
        engine._market_gate_status = lambda: {
            "enabled": True, "gate_effective": True, "leader": "BTCUSDT",
            "day_drift_pct": -0.4, "run_drift_pct": 0.0, "stale": False,
            "thresholds": {"day_pct": 1.3, "run_pct": 0.0},
        }
        engine._market_gate_leader = lambda: "BTCUSDT"
        engine._tv_events_mode = lambda: "off"
        engine._tv_ledger = lambda: None
        engine._kline_source_snapshot = lambda: {"kline_source": "trading_host"}

        ctx = SimpleNamespace(
            candles_5m=_series(60), candles_15m=_series(40), candles_4h=[],
            current_price=100.0, atr_5m=0.5, regime=Regime.RANGE,
        )
        out = engine._forensics_entry_context(
            symbol="TESTUSDT", signal=_exec_signal(), ctx=ctx,
            structure_state=None, is_external=False,
            signal_epoch=time.time(), open_positions=1,
        )
        assert out["source"] == "C"
        assert out["gates"]["leader"] == "passed"
        assert out["gates"]["tv_structure"] == "off"
        assert out["kline_source"] == "trading_host"
        assert out["open_positions"] == 1
        assert out["indicators"]["rsi_entry"] is not None

    def test_external_signal_metadata_is_attached(self):
        engine = _forensics_engine()
        engine._market_gate_status = lambda: {"enabled": False,
                                              "gate_effective": False}
        engine._market_gate_leader = lambda: "BTCUSDT"
        engine._tv_events_mode = lambda: "shadow"
        engine._tv_ledger = lambda: None
        engine._kline_source_snapshot = lambda: {"kline_source": "separate"}

        ctx = SimpleNamespace(
            candles_5m=[], candles_15m=[], candles_4h=[], current_price=100.0,
            atr_5m=0.0, regime=Regime.RANGE,
        )
        out = engine._forensics_entry_context(
            symbol="TESTUSDT", signal=_exec_signal(), ctx=ctx,
            structure_state=None, is_external=True, signal_epoch=time.time(),
            open_positions=0,
            external_meta={"sources": ["luxso_osc", "luxso_trend"], "votes": 2},
        )
        assert out["source"] == "TV"
        assert out["tv"]["sources"] == ["luxso_osc", "luxso_trend"]
        assert out["gates"]["tv_structure"] == "shadow"

    def test_broken_snapshot_returns_none_and_warns_once(self):
        def _boom():
            raise RuntimeError("kapı görüntüsü patladı")

        engine = _forensics_engine()
        engine._market_gate_status = _boom

        out = engine._forensics_entry_context(
            symbol="TESTUSDT", signal=_exec_signal(),
            ctx=SimpleNamespace(candles_5m=[], candles_15m=[], candles_4h=[],
                                current_price=0.0, atr_5m=0.0,
                                regime=Regime.RANGE),
            structure_state=None, is_external=False, signal_epoch=time.time(),
            open_positions=0,
        )
        assert out is None
        assert engine._forensics_error_logged is True

    def test_disabled_flag_returns_none(self):
        engine = _forensics_engine(
            cfg=_ExecCfg(scalper_forensics_enabled=False)
        )
        out = engine._forensics_entry_context(
            symbol="TESTUSDT", signal=_exec_signal(),
            ctx=SimpleNamespace(candles_5m=[], candles_15m=[], candles_4h=[],
                                current_price=0.0, atr_5m=0.0,
                                regime=Regime.RANGE),
            structure_state=None, is_external=False, signal_epoch=time.time(),
            open_positions=0,
        )
        assert out is None


class TestEnginePostmortemTick:
    async def test_zero_window_skips_the_scan_entirely(self):
        called = {"n": 0}

        async def _candidates(**kwargs):
            called["n"] += 1
            return []

        cfg = SimpleNamespace(
            scalper_forensics_enabled=True,
            scalper_forensics_postmortem_min=0.0,
        )
        engine = _forensics_engine(
            cfg=cfg, tracker=SimpleNamespace(postmortem_candidates=_candidates)
        )
        await engine._forensics_postmortem_tick()
        assert called["n"] == 0

    async def test_rate_limited_to_once_per_minute(self):
        called = {"n": 0}

        async def _candidates(**kwargs):
            called["n"] += 1
            return []

        cfg = SimpleNamespace(
            scalper_forensics_enabled=True,
            scalper_forensics_postmortem_min=60.0,
        )
        engine = _forensics_engine(
            cfg=cfg, tracker=SimpleNamespace(postmortem_candidates=_candidates)
        )
        await engine._forensics_postmortem_tick()
        await engine._forensics_postmortem_tick()
        await engine._forensics_postmortem_tick()
        assert called["n"] == 1

    async def test_one_symbol_per_tick_and_document_is_written(self, tmp_path,
                                                               monkeypatch):
        monkeypatch.setenv("TRADINGBOT_LOG_DIR", str(tmp_path))
        forensics_log.reset_error_state()
        closed_at = datetime.utcnow() - timedelta(minutes=90)
        closed_ms = int(closed_at.replace(tzinfo=timezone.utc).timestamp() * 1000)
        written = {}

        async def _candidates(**kwargs):
            return [
                {"id": 5, "symbol": "TESTUSDT", "closed_at": closed_at,
                 "entry": {"direction": "LONG", "fill_price": 100.0},
                 "exit": {"reason": "SL", "realized_pnl": -10.0}},
                {"id": 6, "symbol": "OTHERUSDT", "closed_at": closed_at,
                 "entry": {}, "exit": {}},
            ]

        async def _record(trade_id, postmortem):
            written[trade_id] = postmortem
            return True

        fetched = []

        async def _get_klines(symbol, interval, limit):
            fetched.append((symbol, interval, limit))
            return [_candle(closed_ms + 60_000, high=101.0, low=99.0)]

        cfg = SimpleNamespace(
            scalper_forensics_enabled=True,
            scalper_forensics_postmortem_min=60.0,
            scalper_tf_entry="1m",
        )
        engine = _forensics_engine(
            cfg=cfg,
            tracker=SimpleNamespace(postmortem_candidates=_candidates,
                                    record_postmortem=_record),
            fetcher=SimpleNamespace(get_klines=_get_klines),
        )

        await engine._forensics_postmortem_tick()
        # D21-R3: JSONL yazımı ayrı yazıcı iş parçacığındadır — dosyayı
        # okumadan önce kuyruk boşaltılır.
        assert forensics_log.drain() is True

        assert list(written) == [5]           # tur başına EN FAZLA BİR sembol
        assert written[5]["returned_to_entry"] is True
        assert written[5]["tags"] == [fx.TAG_NOISE_STOP]
        assert fetched == [("TESTUSDT", "1m", 150)]
        line = json.loads(
            (tmp_path / "trades.jsonl").read_text(encoding="utf-8").strip()
        )
        assert line["event"] == "postmortem" and line["trade_id"] == 5

    async def test_market_data_outage_is_swallowed(self):
        from src.strategies.scalper.data import MarketDataBanError

        async def _candidates(**kwargs):
            return [{"id": 5, "symbol": "TESTUSDT",
                     "closed_at": datetime.utcnow() - timedelta(minutes=90),
                     "entry": {}, "exit": {}}]

        async def _get_klines(symbol, interval, limit):
            raise MarketDataBanError("ban", "host", time.time() + 60)

        cfg = SimpleNamespace(
            scalper_forensics_enabled=True,
            scalper_forensics_postmortem_min=60.0,
            scalper_tf_entry="1m",
        )
        engine = _forensics_engine(
            cfg=cfg,
            tracker=SimpleNamespace(postmortem_candidates=_candidates),
            fetcher=SimpleNamespace(get_klines=_get_klines),
        )
        await engine._forensics_postmortem_tick()   # istisna SIZMAZ
        assert engine._forensics_error_logged is False

    async def test_symbol_scoped_error_marks_the_row_unmeasured(self):
        """SEMBOL kapsamlı KALICI hata kuyruğu tıkamamalı (host geneli aksine)."""
        from src.strategies.scalper.data import MarketDataRequestError

        marked = {}

        async def _candidates(**kwargs):
            return [{"id": 9, "symbol": "GHOSTUSDT",
                     "closed_at": datetime.utcnow() - timedelta(minutes=90),
                     "entry": {}, "exit": {}}]

        async def _record(trade_id, postmortem):
            marked[trade_id] = postmortem
            return True

        async def _get_klines(symbol, interval, limit):
            raise MarketDataRequestError("-1121 Invalid symbol")

        cfg = SimpleNamespace(
            scalper_forensics_enabled=True,
            scalper_forensics_postmortem_min=60.0,
            scalper_tf_entry="1m",
        )
        engine = _forensics_engine(
            cfg=cfg,
            tracker=SimpleNamespace(postmortem_candidates=_candidates,
                                    record_postmortem=_record),
            fetcher=SimpleNamespace(get_klines=_get_klines),
        )
        await engine._forensics_postmortem_tick()

        assert marked[9]["returned_to_entry"] is None
        assert "bulunamadı" in marked[9]["note"]


# --------------------------------------------------------------------------
# 10b) Çıkış tarafı: zaman çizgisi damgaları ve belge kurulumu
# --------------------------------------------------------------------------

def _exit_manager(cfg=None, context_cb=None):
    """`ExitManager`ı __init__ çalıştırmadan kur (repo konvansiyonu)."""
    from src.core.logger import app_logger
    from src.strategies.scalper.exits import ExitManager

    manager = ExitManager.__new__(ExitManager)
    manager.cfg = cfg or _ExecCfg()
    manager.logger = app_logger
    manager._forensics_context_cb = context_cb
    manager._forensics_error_logged = False
    return manager


def _tracked_position(**overrides):
    plan = SimpleNamespace(
        tp1_price=101.0, tp2_price=102.0, initial_stop=99.0,
        entry_fee_rate=0.0005, tp1_algo_id=None, tp2_algo_id=None,
    )
    position = SimpleNamespace(
        entry_price=100.0, quantity=1.0, leverage=20, current_stoploss=100.2,
        opened_at=datetime.utcnow() - timedelta(hours=1),
    )
    sp = SimpleNamespace(
        trade_id=5, plan=plan, position=position,
        signal=SimpleNamespace(direction=Direction.LONG),
        mae_pct=-12.0, mfe_pct=26.0, tp1_done=True, tp2_done=False,
        trailing_active=True, tp1_at="2026-08-22T11:40:00+00:00",
        tp2_at=None, be_at="2026-08-22T11:40:02+00:00", be_price=100.2,
        trail_updates=4, last_trail_stop=100.9, opened_epoch=time.time() - 3600,
        forensics_entry=None,
    )
    for key, value in overrides.items():
        setattr(sp, key, value)
    return sp


class TestExitForensics:
    def test_mark_path_stamps_once(self):
        from src.strategies.scalper.exits import ExitManager

        sp = SimpleNamespace(tp1_at=None)
        ExitManager._mark_path(sp, "tp1_at")
        first = sp.tp1_at
        assert first is not None
        ExitManager._mark_path(sp, "tp1_at")
        assert sp.tp1_at == first        # İLK damga korunur

    def test_exit_document_carries_the_timeline_and_context(self):
        manager = _exit_manager(context_cb=lambda symbol: {
            "regime": "DOWN", "leader_day_drift_pct": -2.1, "btc_price": 112000.0,
        })
        doc, verdict = manager._build_exit_forensics(
            symbol="TESTUSDT", sp=_tracked_position(), exit_price=99.0,
            realized_pnl=-30.0, gross_pnl=-28.0,
            pnl_source="binance_income_net", exit_reason="SL",
            verification_notes=[],
        )
        assert doc["reason"] == "SL"
        assert doc["path"]["trail_updates"] == 4
        assert doc["path"]["be_price"] == pytest.approx(100.2)
        assert doc["regime"] == "DOWN"
        assert doc["leader_day_drift_pct"] == pytest.approx(-2.1)
        # MFE TP1 hedefini gördü ama zararla kapandı → mfe_giveback
        assert fx.TAG_MFE_GIVEBACK in verdict

    def test_broken_context_callback_does_not_break_the_document(self):
        def _boom(symbol):
            raise RuntimeError("kapı görüntüsü okunamadı")

        manager = _exit_manager(context_cb=_boom)
        doc, verdict = manager._build_exit_forensics(
            symbol="TESTUSDT", sp=_tracked_position(), exit_price=101.0,
            realized_pnl=5.0, gross_pnl=6.0, pnl_source="estimated_gross",
            exit_reason="TRAIL", verification_notes=["exit_fill=unverified"],
        )
        assert doc is not None
        assert doc["regime"] is None
        assert doc["verification_notes"] == ["exit_fill=unverified"]
        assert manager._forensics_error_logged is True

    def test_disabled_flag_returns_no_document(self):
        manager = _exit_manager(cfg=_ExecCfg(scalper_forensics_enabled=False))
        doc, verdict = manager._build_exit_forensics(
            symbol="TESTUSDT", sp=_tracked_position(), exit_price=101.0,
            realized_pnl=5.0, gross_pnl=6.0, pnl_source="x",
            exit_reason="TRAIL", verification_notes=[],
        )
        assert doc is None and verdict is None

    def test_builder_error_is_swallowed(self, monkeypatch):
        def _boom(**kwargs):
            raise RuntimeError("patladı")

        monkeypatch.setattr(fx, "build_exit", _boom)
        manager = _exit_manager()
        doc, verdict = manager._build_exit_forensics(
            symbol="TESTUSDT", sp=_tracked_position(), exit_price=101.0,
            realized_pnl=5.0, gross_pnl=6.0, pnl_source="x",
            exit_reason="TRAIL", verification_notes=[],
        )
        assert doc is None and verdict is None
        assert manager._forensics_error_logged is True

    def test_entry_tags_are_carried_into_the_final_verdict(self):
        manager = _exit_manager()
        sp = _tracked_position(forensics_entry={
            "direction": "LONG",
            "leader_gate": {"verdict": "etkin_değil", "day_drift_pct": -3.0},
        })
        _doc, verdict = manager._build_exit_forensics(
            symbol="TESTUSDT", sp=sp, exit_price=99.0, realized_pnl=-30.0,
            gross_pnl=-28.0, pnl_source="x", exit_reason="SL",
            verification_notes=[],
        )
        assert fx.TAG_COUNTER_DRIFT_LONG in verdict
        assert fx.TAG_GATE_BYPASSED in verdict


# --------------------------------------------------------------------------
# 11) HTTP uçları
# --------------------------------------------------------------------------

class TestForensicsEndpoints:
    async def test_trade_forensics_returns_row(self, monkeypatch):
        import src.main as main

        row = {"id": 7, "has_forensics": True, "verdict": ["noise_stop"]}
        monkeypatch.setattr(
            main, "ScalpTracker",
            lambda: SimpleNamespace(forensics_for=_async_return(row)),
        )
        assert await main.scalper_trade_forensics(7) == row

    async def test_trade_forensics_404_for_unknown_trade(self, monkeypatch):
        import src.main as main
        from fastapi import HTTPException

        monkeypatch.setattr(
            main, "ScalpTracker",
            lambda: SimpleNamespace(forensics_for=_async_return(None)),
        )
        with pytest.raises(HTTPException) as exc:
            await main.scalper_trade_forensics(1234)
        assert exc.value.status_code == 404

    async def test_recent_endpoint_passes_the_limit(self, monkeypatch):
        import src.main as main

        seen = {}

        async def _recent(limit):
            seen["limit"] = limit
            return []

        monkeypatch.setattr(
            main, "ScalpTracker",
            lambda: SimpleNamespace(recent_forensics=_recent),
        )
        assert await main.scalper_forensics_recent(limit=17) == []
        assert seen["limit"] == 17

    async def test_summary_endpoint_parses_relative_since(self, monkeypatch):
        import src.main as main

        seen = {}

        async def _summary(since=None, until=None):
            seen["since"] = since
            seen["until"] = until
            return {"tags": []}

        monkeypatch.setattr(
            main, "ScalpTracker",
            lambda: SimpleNamespace(forensics_summary=_summary),
        )
        await main.scalper_forensics_summary(since="7d")
        assert seen["until"] is None
        delta = datetime.utcnow() - seen["since"]
        assert timedelta(days=6, hours=23) < delta < timedelta(days=7, hours=1)

    def test_since_parser_accepts_dates_and_rejects_garbage(self):
        import src.main as main
        from fastapi import HTTPException

        assert main._parse_since(None) is None
        assert main._parse_since("") is None
        assert main._parse_since("2026-08-21") == datetime(2026, 8, 21)
        assert main._parse_since("2026-08-21 12:35") == datetime(2026, 8, 21, 12, 35)
        with pytest.raises(HTTPException):
            main._parse_since("dün")


def _async_return(value):
    async def _inner(*args, **kwargs):
        return value
    return _inner


# --------------------------------------------------------------------------
# 12) ledger_report --forensics
# --------------------------------------------------------------------------

def _load_ledger_report():
    """`scripts/` bir paket değildir; yol eklenip adıyla import edilir
    (aynı desen: tests/test_ledger_report.py, tests/test_autoresearch.py)."""
    scripts_dir = str(REPO_ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import ledger_report

    return ledger_report


def _seed_db(path: Path, rows):
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE scalp_trades (id INTEGER PRIMARY KEY, strategy TEXT,"
        " symbol TEXT, direction TEXT, realized_pnl REAL, exit_reason TEXT,"
        " status TEXT, closed_at TEXT, forensics TEXT)"
    )
    con.executemany("INSERT INTO scalp_trades VALUES (?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()


class TestLedgerReportForensics:
    def test_tag_labels_match_the_engine_module(self):
        """İki taraf (stdlib script + motor modülü) ayrışırsa test kırılır."""
        module = _load_ledger_report()
        assert module.FORENSICS_TAG_LABELS == fx.TAG_LABELS

    def test_forensics_rows_and_table(self, tmp_path):
        module = _load_ledger_report()
        db = tmp_path / "ledger.db"
        _seed_db(db, [
            (1, "C", "BTCUSDT", "LONG", -500.0, "SL", "CLOSED",
             "2026-08-22 10:00:00",
             json.dumps({"verdict": ["counter_drift_long", "noise_stop"]})),
            (2, "C", "ETHUSDT", "SHORT", 120.0, "TRAIL", "CLOSED",
             "2026-08-22 12:00:00", json.dumps({"verdict": []})),
            (3, "C", "SOLUSDT", "LONG", -30.0, "SL", "CLOSED",
             "2026-08-22 13:00:00", None),
        ])

        rows, notes = module.load_forensics_rows(
            str(db), datetime(2026, 8, 21), datetime(2026, 8, 23)
        )
        assert len(rows) == 3
        assert any("adli kaydı YOK" in note for note in notes)

        table = module.build_forensics_table(rows)
        by_tag = {row["tag"]: row for row in table}
        assert by_tag["counter_drift_long"]["pnl"] == pytest.approx(-500.0)
        assert by_tag["counter_drift_long"]["label"] == fx.TAG_LABELS[
            "counter_drift_long"
        ]
        assert by_tag[module.UNTAGGED_KEY]["trades"] == 2
        assert table[0]["tag"] in ("counter_drift_long", "noise_stop")

    def test_missing_column_is_reported_not_crashed(self, tmp_path):
        module = _load_ledger_report()
        db = tmp_path / "old.db"
        con = sqlite3.connect(db)
        con.execute(
            "CREATE TABLE scalp_trades (id INTEGER PRIMARY KEY, strategy TEXT,"
            " realized_pnl REAL, closed_at TEXT, status TEXT)"
        )
        con.commit()
        con.close()

        rows, notes = module.load_forensics_rows(
            str(db), datetime(2026, 8, 21), datetime(2026, 8, 23)
        )
        assert rows == []
        assert any("sütunu okunamadı" in note for note in notes)

    def test_cli_renders_the_section_only_with_the_flag(self, tmp_path):
        module = _load_ledger_report()
        db = tmp_path / "cli.db"
        _seed_db(db, [
            (1, "C", "BTCUSDT", "LONG", -500.0, "SL", "CLOSED",
             "2026-08-22 10:00:00",
             json.dumps({"verdict": ["counter_drift_long"]})),
        ])
        klines = tmp_path / "klines.json"
        klines.write_text("[]")

        with_flag = tmp_path / "with.txt"
        assert module.main([
            "--db", str(db), "--since", "2026-08-21", "--until", "2026-08-23",
            "--btc-klines-json", str(klines), "--forensics",
            "--out", str(with_flag),
        ]) == 0
        text = with_flag.read_text(encoding="utf-8")
        assert "ETİKET x SONUÇ" in text
        assert "counter_drift_long" in text

        without = tmp_path / "without.txt"
        assert module.main([
            "--db", str(db), "--since", "2026-08-21", "--until", "2026-08-23",
            "--btc-klines-json", str(klines), "--out", str(without),
        ]) == 0
        assert "ETİKET x SONUÇ" not in without.read_text(encoding="utf-8")

    def test_json_format_includes_the_forensics_key(self, tmp_path):
        module = _load_ledger_report()
        db = tmp_path / "json.db"
        _seed_db(db, [
            (1, "C", "BTCUSDT", "LONG", -500.0, "SL", "CLOSED",
             "2026-08-22 10:00:00", json.dumps({"verdict": ["noise_stop"]})),
        ])
        klines = tmp_path / "klines.json"
        klines.write_text("[]")
        out = tmp_path / "rep.json"
        module.main([
            "--db", str(db), "--since", "2026-08-21", "--until", "2026-08-23",
            "--btc-klines-json", str(klines), "--forensics", "--format", "json",
            "--out", str(out),
        ])
        report = json.loads(out.read_text(encoding="utf-8"))
        assert report["forensics"][0]["tag"] == "noise_stop"


# --------------------------------------------------------------------------
# 13) D21-R3 — düşmanca inceleme düzeltmeleri
# --------------------------------------------------------------------------

class TestPostmortemNeverBlocksSafetyTick:
    """Bulgu 1: teşhis işi bir koruma işini ASLA geciktirmemeli."""

    def _safety_engine(self, order):
        engine = _forensics_engine(
            cfg=SimpleNamespace(
                scalper_forensics_enabled=True,
                scalper_forensics_postmortem_min=60.0,
            )
        )
        engine._kill_switch = False
        engine._entry_halted = False
        engine._entry_lock = asyncio.Lock()
        engine.executor = SimpleNamespace(
            pending_symbols=lambda: set(),
            check_pending=_async_return([]),
            cancel_all_pending=_async_return([]),
        )

        async def _step():
            order.append("exits")

        engine.exits = SimpleNamespace(step=_step, _market_data_down_reason=None)

        async def _noop():
            order.append("noop")

        engine._apply_structure_exits = _noop
        engine._apply_tv_event_exits = _noop
        engine._reap_aged_positions = _noop
        engine._track_opened_positions = lambda *a, **k: None
        engine._sync_scalper_reservations = lambda: order.append("reservations")

        async def _kill():
            order.append("kill_switch")

        engine._update_kill_switch = _kill
        return engine

    async def test_safety_tick_returns_before_the_postmortem_finishes(self):
        order = []
        engine = self._safety_engine(order)

        async def _slow_tick():
            order.append("postmortem_start")
            await asyncio.sleep(0.2)
            order.append("postmortem_end")

        engine._forensics_postmortem_tick = _slow_tick

        started = time.monotonic()
        await engine._safety_tick()
        elapsed = time.monotonic() - started

        # Tur post-mortem'i BEKLEMEDİ ...
        assert elapsed < 0.1
        assert "postmortem_end" not in order
        # ... ve koruma işleri (rezervasyon senkronu + kill switch) tamamlandı.
        assert order[-2:] == ["reservations", "kill_switch"]

        await asyncio.sleep(0.3)
        assert "postmortem_end" in order

    async def test_only_one_postmortem_runs_at_a_time(self):
        order = []
        engine = self._safety_engine(order)
        runs = {"n": 0}

        async def _slow_tick():
            runs["n"] += 1
            await asyncio.sleep(0.15)

        engine._forensics_postmortem_tick = _slow_tick

        engine._forensics_postmortem_schedule()
        engine._forensics_postmortem_schedule()
        engine._forensics_postmortem_schedule()
        await asyncio.sleep(0.05)
        assert runs["n"] == 1

        await asyncio.sleep(0.2)
        assert runs["n"] == 1
        assert engine._forensics_postmortem_task is None   # referans temizlendi

    async def test_market_data_outage_skips_the_round_entirely(self):
        order = []
        engine = self._safety_engine(order)
        engine.exits._market_data_down_reason = "ban 418"
        runs = {"n": 0}

        async def _tick():
            runs["n"] += 1

        engine._forensics_postmortem_tick = _tick
        engine._forensics_postmortem_schedule()
        await asyncio.sleep(0.02)

        assert runs["n"] == 0
        # Sayaç HARCANMADI: kesinti bitince ilk turda yeniden denenir.
        assert engine._forensics_postmortem_at == 0.0

    async def test_ban_on_the_kline_host_also_skips_the_round(self):
        """Açık pozisyon yokken `_market_data_down_reason` hiç dolmaz —
        ban'ı gören tek sinyal guard'dır (D21-R3)."""
        from src.strategies.scalper.data import MarketDataGuard, host_of

        order = []
        engine = self._safety_engine(order)
        engine.fetcher = SimpleNamespace(base_url="https://data.example.test")
        host = host_of("https://data.example.test")
        MarketDataGuard.trip(host, "418 test banı", 120.0, hard=True)
        try:
            assert engine._forensics_postmortem_blocked() is not None
            runs = {"n": 0}

            async def _tick():
                runs["n"] += 1

            engine._forensics_postmortem_tick = _tick
            engine._forensics_postmortem_schedule()
            await asyncio.sleep(0.02)
            assert runs["n"] == 0
        finally:
            MarketDataGuard._state(host).blocked_until = 0.0
            MarketDataGuard._state(host).hard_ban = False
        assert engine._forensics_postmortem_blocked() is None

    async def test_task_exception_is_consumed_and_warned_once(self):
        order = []
        engine = self._safety_engine(order)

        async def _boom():
            raise RuntimeError("post-mortem patladı")

        engine._forensics_postmortem_tick = _boom
        engine._forensics_postmortem_schedule()
        await asyncio.sleep(0.02)

        assert engine._forensics_error_logged is True
        assert engine._forensics_postmortem_task is None


class TestPostmortemTimeoutAndAttemptBudget:
    """Bulgu 1: yavaş host tur içinde beklenmez, sonsuz da denenmez."""

    def _engine(self, get_klines, record):
        cfg = SimpleNamespace(
            scalper_forensics_enabled=True,
            scalper_forensics_postmortem_min=60.0,
            scalper_tf_entry="5m",
        )

        async def _candidates(**kwargs):
            return [{"id": 7, "symbol": "SLOWUSDT",
                     "closed_at": datetime.utcnow() - timedelta(minutes=90),
                     "entry": {}, "exit": {}}]

        return _forensics_engine(
            cfg=cfg,
            tracker=SimpleNamespace(postmortem_candidates=_candidates,
                                    record_postmortem=record),
            fetcher=SimpleNamespace(get_klines=get_klines),
        )

    async def test_slow_host_is_cut_off_and_retried_at_most_three_times(self):
        marked = {}

        async def _record(trade_id, postmortem):
            marked[trade_id] = postmortem
            return True

        async def _hang(symbol, interval, limit):
            await asyncio.sleep(5.0)
            raise AssertionError("zaman aşımına düşmeliydi")

        engine = self._engine(_hang, _record)
        engine._FORENSICS_POSTMORTEM_TIMEOUT = 0.01

        for attempt in range(3):
            engine._forensics_postmortem_at = 0.0        # dakika kapısını aç
            await engine._forensics_postmortem_tick()
            if attempt < 2:
                assert marked == {}                      # ertelendi, yazılmadı

        assert "ölçülemedi" in marked[7]["note"]
        assert "3 deneme" in marked[7]["note"]
        assert marked[7]["returned_to_entry"] is None
        # Sayaç temizlendi: aynı işlem kuyruğa geri dönerse sıfırdan başlar.
        assert 7 not in engine._forensics_postmortem_attempts

    async def test_successful_measurement_clears_the_attempt_counter(self):
        closed_at = datetime.utcnow() - timedelta(minutes=90)
        closed_ms = int(closed_at.replace(tzinfo=timezone.utc).timestamp() * 1000)
        marked = {}

        async def _record(trade_id, postmortem):
            marked[trade_id] = postmortem
            return True

        async def _klines(symbol, interval, limit):
            return [_candle(closed_ms + 60_000, high=101.0, low=99.0)]

        engine = self._engine(_klines, _record)
        engine._forensics_postmortem_attempts = {7: 2}
        await engine._forensics_postmortem_tick()

        assert 7 in marked
        assert engine._forensics_postmortem_attempts == {}

    async def test_kline_request_uses_the_configured_entry_timeframe(self):
        """Maliyet metni (D21/ARCHITECTURE) `SCALPER_TF_ENTRY` demeli."""
        seen = []
        closed_at = datetime.utcnow() - timedelta(minutes=90)
        closed_ms = int(closed_at.replace(tzinfo=timezone.utc).timestamp() * 1000)

        async def _klines(symbol, interval, limit):
            seen.append((symbol, interval, limit))
            return [_candle(closed_ms + 60_000, high=101.0, low=99.0)]

        engine = self._engine(_klines, _async_return(True))
        engine.cfg.scalper_tf_entry = "5m"
        await engine._forensics_postmortem_tick()
        assert seen == [("SLOWUSDT", "5m", 150)]


class TestRecoverRestoresForensics:
    """Bulgu 2: restart sonrası zaman çizgisi UYDURULMAZ, birleştirilir."""

    def _manager(self):
        manager = _exit_manager()
        manager._forensics_error_logged = False
        return manager

    def test_entry_document_is_restored_from_the_database(self):
        manager = self._manager()
        sp = ScalpPosition(
            trade_id=3,
            signal=_exec_signal(),
            position=SimpleNamespace(symbol="TESTUSDT"),
            plan=SimpleNamespace(),
            entry_candle_time=0,
        )
        opened_at = datetime.utcnow() - timedelta(hours=2)
        trade = SimpleNamespace(
            forensics=json.dumps({
                "v": 1,
                "entry": {"direction": "LONG", "stop_price": 98.0,
                          "leader_gate": {"verdict": "etkin_değil"}},
            }),
            opened_at=opened_at,
        )

        manager._restore_forensics_entry(sp, trade)

        assert sp.forensics_restart_gap is True
        assert sp.forensics_entry["stop_price"] == 98.0
        assert sp.opened_epoch == pytest.approx(
            opened_at.replace(tzinfo=timezone.utc).timestamp()
        )
        # Karar-yolu tazelik damgası KASITLI geri yüklenmez (D19a-2).
        assert sp.price_ts is None

    def test_corrupt_document_does_not_raise(self):
        manager = self._manager()
        sp = ScalpPosition(
            trade_id=3, signal=_exec_signal(),
            position=SimpleNamespace(symbol="TESTUSDT"),
            plan=SimpleNamespace(), entry_candle_time=0,
        )
        manager._restore_forensics_entry(
            sp, SimpleNamespace(forensics="{bozuk", opened_at=None)
        )
        assert sp.forensics_entry is None
        assert sp.forensics_restart_gap is True

    def test_exit_document_marks_the_restart_gap_without_inventing_values(self):
        manager = self._manager()
        sp = _tracked_position(
            tp1_at=None, be_at=None, be_price=None,
            trail_updates=0, last_trail_stop=None,
            forensics_restart_gap=True,
            forensics_entry={"direction": "LONG", "stop_price": 97.5,
                             "leader_gate": {"verdict": "etkin_değil"}},
        )
        doc, verdict = manager._build_exit_forensics(
            symbol="TESTUSDT", sp=sp, exit_price=99.0, realized_pnl=-8.0,
            gross_pnl=-7.5, pnl_source="binance_income_net", exit_reason="SL",
            verification_notes=[],
        )

        path = doc["path"]
        assert path["restart_gap"] is True
        assert path["tp1_at"] is None and path["be_at"] is None
        assert path["trail_updates"] is None       # "0" UYDURMA olurdu
        # İlk stop kurtarmadaki CANLI stop değil, giriş belgesindeki gerçek.
        assert path["initial_stop"] == 97.5
        # Giriş belgesi geri geldiği için GİRİŞ etiketleri de türetilebiliyor.
        assert fx.TAG_GATE_BYPASSED in verdict

    def test_normal_close_is_unchanged_by_the_restart_branch(self):
        manager = self._manager()
        sp = _tracked_position()
        doc, _verdict = manager._build_exit_forensics(
            symbol="TESTUSDT", sp=sp, exit_price=101.0, realized_pnl=5.0,
            gross_pnl=6.0, pnl_source="binance_income_net", exit_reason="TRAIL",
            verification_notes=[],
        )
        path = doc["path"]
        assert "restart_gap" not in path
        assert path["trail_updates"] == 4
        assert path["initial_stop"] == 99.0


class TestForensicsLogQueue:
    """Bulgu 3: disk yazımı çağıranın (ve `_entry_lock`ın) dışında."""

    def test_append_soon_returns_before_the_write_happens(self, tmp_path,
                                                          monkeypatch):
        monkeypatch.setenv("TRADINGBOT_LOG_DIR", str(tmp_path))
        forensics_log.reset_error_state()
        calls = []
        real_append = forensics_log.append

        def _slow_append(event, payload, **kwargs):
            time.sleep(0.15)
            calls.append(event)
            return real_append(event, payload, **kwargs)

        monkeypatch.setattr(forensics_log, "append", _slow_append)

        started = time.monotonic()
        assert forensics_log.append_soon("entry", {"trade_id": 1}) is True
        elapsed = time.monotonic() - started

        assert elapsed < 0.05          # çağıran yazımı BEKLEMEDİ
        assert forensics_log.drain(timeout=5.0) is True
        assert calls == ["entry"]
        assert (tmp_path / "trades.jsonl").exists()

    def test_queue_overflow_drops_the_new_line_and_warns_once(self, monkeypatch):
        forensics_log.reset_error_state()
        monkeypatch.setattr(forensics_log, "_queue", queue.Queue(1))
        monkeypatch.setattr(forensics_log, "_ensure_writer", lambda: None)

        assert forensics_log.append_soon("entry", {"trade_id": 1}) is True
        assert forensics_log.append_soon("entry", {"trade_id": 2}) is False
        assert forensics_log.queue_snapshot()["dropped"] == 1

    def test_queued_line_lands_on_disk_after_drain(self, tmp_path,
                                                   monkeypatch):
        monkeypatch.setenv("TRADINGBOT_LOG_DIR", str(tmp_path))
        forensics_log.reset_error_state()
        assert forensics_log.append_soon("exit", {"trade_id": 9}) is True
        assert forensics_log.drain(timeout=5.0) is True
        line = json.loads(
            (tmp_path / "trades.jsonl").read_text(encoding="utf-8").strip()
        )
        assert line["trade_id"] == 9

    def test_payload_mutation_after_enqueue_does_not_change_the_line(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("TRADINGBOT_LOG_DIR", str(tmp_path))
        forensics_log.reset_error_state()
        payload = {"trade_id": 4, "symbol": "TESTUSDT"}
        forensics_log.append_soon("entry", payload)
        payload["symbol"] = "SONRADAN"
        assert forensics_log.drain(timeout=5.0) is True
        line = json.loads(
            (tmp_path / "trades.jsonl").read_text(encoding="utf-8").strip()
        )
        assert line["symbol"] == "TESTUSDT"

    def test_engine_paths_use_the_queue_not_the_blocking_writer(self):
        """Sözleşme: motor yolunda senkron `append` çağrısı KALMAMALI."""
        for name in ("executor.py", "exits.py", "engine.py"):
            src = (
                REPO_ROOT / "src" / "strategies" / "scalper" / name
            ).read_text(encoding="utf-8")
            assert "forensics_log.append(" not in src, name
            assert "forensics_log.append_soon(" in src, name


class TestPendingForensicsIdentity:
    """Bulgu 4: yetim bağlam BAŞKA bir sinyale iliştirilemez."""

    def _executor(self):
        executor = ScalpExecutor(
            client=_FakeClient(), pm=_FakePm(), tracker=_FakeTracker(),
            cfg=_ExecCfg(),
        )
        return executor

    def _pending(self, created_at_ms: int, direction=Direction.LONG):
        import dataclasses

        from src.strategies.scalper.executor import PendingEntry

        signal = dataclasses.replace(_exec_signal(), direction=direction)
        return PendingEntry(
            signal=signal, order_id=1, client_order_id="cid-1",
            limit_price=100.0, quantity=1.0, created_monotonic=0.0,
            created_at_ms=created_at_ms,
        )

    def test_matching_identity_returns_the_context(self):
        executor = self._executor()
        pending = self._pending(1_700_000_000_000)
        executor._store_pending_forensics(pending, {"source": "C"})
        assert executor._take_pending_forensics(pending) == {"source": "C"}
        # Tek kullanımlık: ikinci dolum aynı bağlamı YENİDEN almaz.
        assert executor._take_pending_forensics(pending) is None

    def test_orphan_context_is_dropped_for_a_different_signal(self):
        executor = self._executor()
        first = self._pending(1_700_000_000_000)
        executor._store_pending_forensics(first, {"source": "C", "rr": 1.9})

        # Aynı sembol, YENİ bir niyet (farklı zaman damgası): eski bağlam
        # bu doluma iliştirilmemeli.
        second = self._pending(1_700_000_600_000)
        assert executor._take_pending_forensics(second) is None

    def test_direction_flip_also_invalidates_the_context(self):
        executor = self._executor()
        stamp = 1_700_000_000_000
        executor._store_pending_forensics(self._pending(stamp), {"source": "C"})
        flipped = self._pending(stamp, direction=Direction.SHORT)
        assert executor._take_pending_forensics(flipped) is None

    def test_legacy_shaped_entry_is_ignored(self):
        executor = self._executor()
        pending = self._pending(1_700_000_000_000)
        executor._pending_forensics["TESTUSDT"] = {"source": "C"}   # kimliksiz
        assert executor._take_pending_forensics(pending) is None

    async def test_context_is_stored_only_after_the_order_intent_exists(self):
        """Emir hiç konmadıysa yetim bağlam BİRİKMEZ."""
        executor = self._executor()
        executor.cfg = _ExecCfg()
        executor.cfg.scalper_entry_mode = "maker"
        seen = {}

        async def _open(signal, ctx, side, quantity, *, forensics=None):
            seen["forensics"] = forensics

        executor._open_maker_entry = _open
        await executor.try_open(
            _exec_signal(), _exec_ctx(), forensics={"source": "C"}
        )

        assert seen["forensics"]["source"] == "C"
        assert executor._pending_forensics == {}     # saklama emirle birlikte


class TestForensicsHardening:
    """Bulgu 5: uç/rapor/pano sertleştirmeleri."""

    def test_forensics_warn_survives_a_missing_flag(self):
        executor = ScalpExecutor.__new__(ScalpExecutor)
        from src.core.logger import app_logger

        executor.logger = app_logger
        executor._forensics_warn("test")            # AttributeError YOK
        assert executor._forensics_error_logged is True

    def test_huge_relative_since_is_a_400_not_a_500(self):
        import src.main as main
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as err:
            main._parse_since("9999999999d")
        assert err.value.status_code == 400

    def test_window_beyond_the_cap_is_rejected(self):
        import src.main as main
        from fastapi import HTTPException

        assert main._parse_since("30d") is not None
        with pytest.raises(HTTPException) as err:
            main._parse_since(f"{main.FORENSICS_MAX_WINDOW_DAYS + 5}d")
        assert err.value.status_code == 400
        with pytest.raises(HTTPException):
            main._parse_since("1990-01-01")

    async def test_summary_defaults_to_seven_days(self, monkeypatch):
        import src.main as main

        seen = {}

        async def _summary(since=None, until=None):
            seen["since"] = since
            return {"tags": []}

        monkeypatch.setattr(
            main, "ScalpTracker",
            lambda: SimpleNamespace(forensics_summary=_summary),
        )
        await main.scalper_forensics_summary()
        delta = datetime.utcnow() - seen["since"]
        assert timedelta(days=6, hours=23) < delta < timedelta(days=7, hours=1)

    async def test_candidate_limit_is_applied_after_the_measured_filter(
        self, real_tracker
    ):
        """En yeni kapanışlar ölçülmüşse kuyruk BOŞ görünmemeli."""
        now = datetime.utcnow()
        rows = []
        for i in range(6):
            measured = i < 4          # en yeni 4'ü zaten ölçülmüş
            document = {"v": 1, "entry": {}, "exit": {}}
            if measured:
                document["postmortem"] = {"candles_seen": 3}
            rows.append((i, now - timedelta(minutes=90 + i), document))

        async with tracker_module.AsyncSessionLocal() as session:
            for idx, closed_at, document in rows:
                session.add(ScalpTradeModel(
                    strategy="C", symbol=f"S{idx}USDT", direction="LONG",
                    entry_price=100.0, quantity=1.0, leverage=20,
                    margin_usdt=10.0, status="CLOSED", closed_at=closed_at,
                    signal_reason="test", forensics=json.dumps(document),
                ))
            await session.commit()

        candidates = await real_tracker.postmortem_candidates(
            now=now, min_age_minutes=60.0, limit=2
        )
        assert [row["symbol"] for row in candidates] == ["S4USDT", "S5USDT"]

    def test_build_report_has_one_body_for_both_branches(self):
        module = _load_ledger_report()
        args = ([], {}, datetime(2026, 8, 20), datetime(2026, 8, 22),
                ["2026-08-20"], ["not"])
        plain = module.build_report(*args)
        tagged = module.build_report(*args, forensics=[])

        assert "forensics" not in plain
        assert tagged["forensics"] == []
        assert set(tagged) - set(plain) == {"forensics"}
        for key in plain:
            if key != "meta":
                assert tagged[key] == plain[key], key

    def test_dashboard_separates_read_error_from_missing_record(self):
        html = (REPO_ROOT / "static" / "dashboard.html").read_text(
            encoding="utf-8"
        )
        assert "adli kayıt OKUNAMADI" in html
        assert "row.fx_error" in html
        # Hatalı yanıt önbellekte KALMAZ (yeniden denenebilir).
        assert "!fxCache[id].fx_error" in html
