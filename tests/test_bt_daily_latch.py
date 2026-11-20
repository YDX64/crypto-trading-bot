"""A4 — günlük kayıp mandalı (daily_latch) harness'ta.

Canlı kural (engine._update_kill_switch, engine.py:5413-5531): UTC günü içinde
GERÇEKLEŞMİŞ PnL <= -(gün başı sermaye) * scalper_daily_loss_limit_pct/100 ise
o günün geri kalanında yeni giriş yok; ertesi UTC gece yarısı sıfırlanır.
Harness'ta sermaye tabanı sabit _DEFAULT_VIRTUAL_BALANCE (10_000) olduğundan
limit %1 → eşik -100.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.strategies.scalper.backtest import (
    BacktestTrade,
    REALISM_FEATURES,
    _build_arg_parser,
    _portfolio_filter,
    apply_daily_latch,
    resolve_disabled_realism,
)

H = 3_600_000
DAY = 24 * H
D0 = 20_000 * DAY  # bir UTC gün başlangıcı (epoch ms)

CFG = SimpleNamespace(scalper_daily_loss_limit_pct=1.0, scalper_max_positions=99)


def _t(sym, entry_h, exit_h, pnl, signal_h=None) -> BacktestTrade:
    entry = D0 + int(entry_h * H)
    return BacktestTrade(
        strategy="C", symbol=sym, direction="LONG", entry_price=100.0,
        entry_time=entry, exit_price=100.0, exit_time=D0 + int(exit_h * H),
        quantity=1.0, leverage=5, margin_usdt=100.0, pnl=pnl, roi_pct=0.0,
        exit_reason="SL", mae_pct=0.0, mfe_pct=0.0, duration_minutes=0.0,
        exit_idx=0,
        signal_close_time=None if signal_h is None else D0 + int(signal_h * H),
    )


def test_latch_triggers_at_right_trade_and_drops_later_entries():
    t1 = _t("A", 0.5, 1.0, -60.0)
    t2 = _t("B", 1.0, 2.0, -50.0)            # kümülatif -110 <= -100 -> mandal 02:00
    t_before = _t("C", 1.5, 5.0, +10.0)      # giriş mandaldan ÖNCE, açık kalır
    t_after = _t("D", 3.0, 4.0, -30.0)       # giriş 03:00 > 02:00 -> düşer
    kept, rep = apply_daily_latch([t1, t2, t_before, t_after], CFG)
    assert [t.symbol for t in kept] == ["A", "B", "C"]
    assert rep["dropped_trades"] == 1
    assert rep["threshold"] == -100.0
    assert len(rep["latched_days"]) == 1
    day = rep["latched_days"][0]
    assert day["latch_time_ms"] == D0 + 2 * H
    assert day["day_pnl"] == -110.0


def test_trades_before_latch_untouched_and_exact_equal_triggers():
    t1 = _t("A", 0.0, 1.0, -100.0)           # tam eşik: pnl <= eşik tetikler
    t2 = _t("B", 0.5, 3.0, -5.0)             # mandaldan önce girdi
    t3 = _t("C", 1.0, 2.0, -1.0)             # giriş == mandal anı -> düşer
    kept, rep = apply_daily_latch([t1, t2, t3], CFG)
    assert [t.symbol for t in kept] == ["A", "B"]
    assert rep["latched_days"][0]["latch_time_ms"] == D0 + 1 * H


def test_no_latch_when_above_threshold():
    ts = [_t("A", 0, 1, -40.0), _t("B", 2, 3, -40.0), _t("C", 4, 5, -19.0)]
    kept, rep = apply_daily_latch(ts, CFG)
    assert kept == ts
    assert rep["latched_days"] == [] and rep["dropped_trades"] == 0


def test_resets_at_next_utc_midnight():
    d1_loss = _t("A", 22.0, 23.0, -150.0)    # gün 0'da mandal 23:00
    same_day_drop = _t("B", 23.5, 23.9, -10.0)
    next_day = _t("C", 24.0, 25.0, -20.0)    # tam gece yarısı: yeni gün, sıfırlandı
    kept, rep = apply_daily_latch([d1_loss, same_day_drop, next_day], CFG)
    assert [t.symbol for t in kept] == ["A", "C"]
    assert rep["dropped_trades"] == 1


def test_dropped_trade_pnl_never_counts_toward_next_day():
    loss = _t("A", 1.0, 2.0, -150.0)         # gün 0 mandalı 02:00
    # gün 0'da giriş (düşer), ertesi gün çıkardı: günlük -999 eşiği
    # kurtarılmış sayılmamalı ve gün 1'e yazılmamalı
    dropped = _t("B", 5.0, 30.0, -999.0)
    after = _t("C", 26.0, 27.0, -50.0)       # gün 1: yalnız -50 -> mandal yok
    kept, rep = apply_daily_latch([loss, dropped, after], CFG)
    assert [t.symbol for t in kept] == ["A", "C"]
    assert len(rep["latched_days"]) == 1


def test_signal_time_is_decision_time():
    loss = _t("A", 1.0, 2.0, -150.0)         # mandal 02:00
    # sinyal 01:00'de (mandaldan önce), maker dolumu 03:00'te -> canlıda emir
    # zaten bekliyordu, düşmemeli
    pending = _t("B", 3.0, 4.0, -5.0, signal_h=1.0)
    kept, _ = apply_daily_latch([loss, pending], CFG)
    assert [t.symbol for t in kept] == ["A", "B"]


def test_limit_zero_disables():
    cfg = SimpleNamespace(scalper_daily_loss_limit_pct=0.0)
    ts = [_t("A", 0, 1, -5000.0), _t("B", 2, 3, -5000.0)]
    kept, rep = apply_daily_latch(ts, cfg)
    assert kept == ts and rep["enabled"] is False


def test_custom_capital_scales_threshold():
    ts = [_t("A", 0, 1, -30.0), _t("B", 2, 3, -5.0)]
    kept, rep = apply_daily_latch(ts, CFG, capital=1000.0)  # eşik -10
    assert [t.symbol for t in kept] == ["A"]
    assert rep["threshold"] == -10.0


def test_portfolio_filter_latch_off_equals_capacity_only():
    ts = [_t("A", 0, 1, -60.0), _t("B", 1, 2, -50.0), _t("C", 3, 4, -30.0)]
    miss = {}
    kept_off = _portfolio_filter(ts, ["A", "B", "C"], CFG, miss,
                                 disabled_realism={"daily_latch"})
    assert [t.symbol for t in kept_off] == ["A", "B", "C"]
    assert "daily_latch" not in miss
    miss2 = {}
    kept_on = _portfolio_filter(ts, ["A", "B", "C"], CFG, miss2)
    assert [t.symbol for t in kept_on] == ["A", "B"]
    assert miss2["daily_latch"] == 1


def test_legacy_model_turns_latch_off_and_feature_registered():
    assert "daily_latch" in REALISM_FEATURES
    args = _build_arg_parser().parse_args(["--legacy-model"])
    assert "daily_latch" in resolve_disabled_realism(args)
    args = _build_arg_parser().parse_args([])
    assert "daily_latch" not in resolve_disabled_realism(args)


def test_latch_dropped_trade_does_not_hold_a_capacity_slot():
    # Denetim bulgusu: kapasite önce uygulanınca mandalın düşüreceği B (hiç
    # açılmaz) slot tutuyor ve ondan SONRA gelen C'yi haksız reddediyordu.
    cfg = SimpleNamespace(scalper_daily_loss_limit_pct=1.0, scalper_max_positions=1)
    L = _t("L", 1, 2, -150.0)            # gün 0: -150 <= -100 -> mandal 02:00
    B = _t("B", 3, 26, +5.0)             # karar 03:00 mandaldan sonra -> düşer
    C = _t("C", 25, 25.5, +5.0)          # ertesi gün 01:00; B açılmadığı için slot boş
    miss = {}
    kept = _portfolio_filter([L, B, C], ["L", "B", "C"], cfg, miss)
    assert [t.symbol for t in kept] == ["L", "C"]
    assert miss == {"daily_latch": 1}


def test_joint_pass_report_matches_latch_semantics():
    cfg = SimpleNamespace(scalper_daily_loss_limit_pct=1.0, scalper_max_positions=1)
    L = _t("L", 1, 2, -150.0)
    B = _t("B", 3, 26, +5.0)
    C = _t("C", 25, 25.5, +5.0)
    rep = {}
    _portfolio_filter([L, B, C], ["L", "B", "C"], cfg, {}, report_out=rep)
    r = rep["daily_latch"]
    assert r["dropped_trades"] == 1 and r["dropped_pnl"] == 5.0
    assert len(r["latched_days"]) == 1 and r["latched_days"][0]["day_pnl"] == -150.0


def test_capacity_still_rejects_when_slot_really_taken():
    cfg = SimpleNamespace(scalper_daily_loss_limit_pct=1.0, scalper_max_positions=1)
    A = _t("A", 1, 5, +1.0)
    B = _t("B", 2, 3, +1.0)              # A hâlâ açık -> kapasite reddi
    miss = {}
    kept = _portfolio_filter([A, B], ["A", "B"], cfg, miss)
    assert [t.symbol for t in kept] == ["A"]
    assert miss == {"capacity": 1}
