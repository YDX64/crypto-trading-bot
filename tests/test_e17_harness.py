"""E17 — harness ölçüm değişiklikleri (PLAN §5.1: H1, H2, H3).

TEMEL SÖZLEŞME: üçü de YALNIZ ÖLÇÜMDÜR; karar yolunu DEĞİŞTİRMEZ.
`tests/test_golden_backtest.py` nöbetçidir; buradaki testler ayrıca
"bayraklar yokken rapor şekli ve sayılar eskisiyle birebir aynı" iddiasını
doğrudan sınar.

* H1 — `open_position` içindeki sessiz `None` dönüşleri `missed_counter`'da
  sayılır (stop_band_low / stop_band_high / price_distance_zero / qty_zero /
  no_next_candle). Anahtar yalnız red olunca oluşur.
* H2 — `--diagnostics` (sayaç bloğu) ve `--same-bar-order {pessimistic,
  optimistic}` (aynı-bar sırası teşhisi). Varsayılanlar bugünkü davranıştır.
* H3 — `TestRoleTriplesE17`: E17'nin beş gerçek rol üçlüsünde pencere
  boyları, look-ahead yokluğu ve rol-anahtarı çakışması (30m/4h/4h).

Sentetik mum kullanılır; hiçbir gerçek piyasa verisi okunmaz, ağa çıkılmaz.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

import pytest

import src.strategies.scalper.backtest as bt
from src.strategies.scalper import kline_cache
from src.strategies.scalper.backtest import (
    DIAGNOSTIC_KEYS,
    SAME_BAR_ORDER_OPTIMISTIC,
    SAME_BAR_ORDER_PESSIMISTIC,
    OpenPosition,
    _process_candle_exits,
    build_context,
    manage_position,
    new_diagnostics,
    open_position,
)
from src.strategies.scalper.types import Candle, Direction, Regime, ScalpSignal

# --------------------------------------------------------------------------
# Ortak yardımcılar
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_cwd(tmp_path, monkeypatch):
    """CLI rapor yazımı (`logs/`) test dizinine gitsin; depoyu kirletme."""
    monkeypatch.chdir(tmp_path)


class _Cfg:
    """`open_position` için asgari ayar (TestEntryDelay._Cfg ile aynı alanlar)."""

    scalper_taker_fee_pct = 0.05
    scalper_maker_fee_pct = 0.02
    scalper_slippage_rate = 0.0002
    scalper_min_stop_pct = 0.15
    scalper_max_stop_pct = 3.0
    scalper_min_rr = 0.0
    scalper_entry_mode = "taker"
    scalper_maker_fill_timeout_candles = 3
    scalper_risk_percentage = 2.0
    scalper_leverage = 20
    scalper_max_margin_pct = 10.0
    scalper_tp1_roi = 10.0
    scalper_tp2_roi = 25.0
    scalper_tp1_fraction = 0.40
    scalper_tp2_fraction = 0.20
    scalper_breakeven_buffer_pct = 0.05

    def __init__(self, **overrides) -> None:
        for key, value in overrides.items():
            setattr(self, key, value)


def _signal(entry: float = 100.0, stop: float = 99.0) -> ScalpSignal:
    return ScalpSignal(
        strategy="C", symbol="BTCUSDT", direction=Direction.LONG,
        entry_price=entry, stop_price=stop, reason="test",
        regime=Regime.UP, atr_5m=1.0, score=1.0,
    )


def _flat_candles(n: int = 6) -> List[Candle]:
    out = []
    for i in range(n):
        p = 100.0 + i
        out.append(Candle(
            open_time=i * 1000, open=p, high=p + 0.5, low=p - 0.5,
            close=p, volume=1.0, close_time=i * 1000 + 999,
        ))
    return out


# --------------------------------------------------------------------------
# H1 — sessiz None dönüşlerinin sayaçları
# --------------------------------------------------------------------------


class TestSilentDropCounters:
    def test_stop_too_wide_counts_stop_band_high(self):
        mc: Dict[str, int] = {}
        # %6 stop (> MAX 3.0)
        assert open_position(_signal(100.0, 94.0), _flat_candles(), 0, _Cfg(),
                             missed_counter=mc) is None
        assert mc == {"stop_band_high": 1}

    def test_stop_too_tight_counts_stop_band_low(self):
        mc: Dict[str, int] = {}
        # %0.1 stop (< MIN 0.15)
        assert open_position(_signal(100.0, 99.9), _flat_candles(), 0, _Cfg(),
                             missed_counter=mc) is None
        assert mc == {"stop_band_low": 1}

    def test_in_band_stops_are_not_counted(self):
        mc: Dict[str, int] = {}
        cfg = _Cfg()
        for stop in (99.8, 99.0, 97.5):  # %0.2, %1, %2.5 — bant içi
            pos = open_position(_signal(100.0, stop), _flat_candles(), 0, cfg,
                                missed_counter=mc)
            assert pos is not None
        assert mc == {}

    def test_zero_price_distance_counts_price_distance_zero(self):
        mc: Dict[str, int] = {}
        # MIN bandı 0'a çekilirse stop == entry banttan geçer, mesafe 0 kalır.
        cfg = _Cfg(scalper_min_stop_pct=0.0)
        assert open_position(_signal(100.0, 100.0), _flat_candles(), 0, cfg,
                             missed_counter=mc) is None
        assert mc == {"price_distance_zero": 1}

    def test_taker_without_next_candle_counts_no_next_candle(self):
        mc: Dict[str, int] = {}
        candles = _flat_candles(3)
        assert open_position(_signal(), candles, len(candles) - 1, _Cfg(),
                             missed_counter=mc) is None
        assert mc == {"no_next_candle": 1}

    def test_taker_delay_past_end_counts_no_next_candle(self):
        mc: Dict[str, int] = {}
        assert open_position(_signal(), _flat_candles(), 0, _Cfg(),
                             missed_counter=mc, entry_delay_candles=10) is None
        assert mc == {"no_next_candle": 1}

    def test_zero_risk_counts_qty_zero(self):
        mc: Dict[str, int] = {}
        cfg = _Cfg(scalper_risk_percentage=0.0)
        assert open_position(_signal(), _flat_candles(), 0, cfg,
                             missed_counter=mc) is None
        assert mc == {"qty_zero": 1}

    def test_counters_accumulate(self):
        mc: Dict[str, int] = {}
        for _ in range(3):
            open_position(_signal(100.0, 94.0), _flat_candles(), 0, _Cfg(),
                          missed_counter=mc)
        open_position(_signal(100.0, 99.9), _flat_candles(), 0, _Cfg(),
                      missed_counter=mc)
        assert mc == {"stop_band_high": 3, "stop_band_low": 1}

    def test_accepted_signal_leaves_counter_untouched(self):
        mc: Dict[str, int] = {}
        assert open_position(_signal(), _flat_candles(), 0, _Cfg(),
                             missed_counter=mc) is not None
        assert mc == {}

    def test_existing_counters_still_work(self):
        mc: Dict[str, int] = {}
        # min_rr kapısı: kaldıraç 20 × %1 stop = 20 ROI risk; beklenen ROI 13.
        cfg = _Cfg(scalper_min_rr=5.0)
        assert open_position(_signal(), _flat_candles(), 0, cfg,
                             missed_counter=mc) is None
        assert mc == {"min_rr_rejected": 1}
        mc2: Dict[str, int] = {}
        cfg2 = _Cfg(scalper_entry_mode="maker", scalper_maker_fill_timeout_candles=1)
        assert open_position(_signal(), _flat_candles(), 0, cfg2,
                             missed_counter=mc2) is None
        assert mc2 == {"maker_missed": 1}

    def test_no_counter_passed_is_silent_and_safe(self):
        """`missed_counter=None` (permütasyon yolu) iken reddin sonucu aynı,
        istisna yok."""
        for stop in (94.0, 99.9):
            assert open_position(_signal(100.0, stop), _flat_candles(), 0, _Cfg()) is None
        assert open_position(
            _signal(100.0, 100.0), _flat_candles(), 0,
            _Cfg(scalper_min_stop_pct=0.0),
        ) is None

    def test_entry_hint_nonpositive_is_not_a_counted_key(self):
        """`entry_hint <= 0` ham veri bozukluğudur; PLAN §5.1'deki beş anahtar
        arasında DEĞİL — sayaç sözlüğünü kirletmemeli."""
        mc: Dict[str, int] = {}
        assert open_position(_signal(0.0, 0.0), _flat_candles(), 0, _Cfg(),
                             missed_counter=mc) is None
        assert mc == {}


# --------------------------------------------------------------------------
# H2 — aynı-bar sayaçları ve optimistic sıra
# --------------------------------------------------------------------------


def _pos(**overrides) -> OpenPosition:
    """LONG: giriş 100, stop 99, TP1 101 (4/10), TP2 102 (2/10), koşucu 4/10,
    BE 100.1. Komisyon oranları gerçekçi; testler yalnız DURUMU sınar."""
    kw = dict(
        strategy="C", symbol="BTCUSDT", direction=Direction.LONG,
        stop_price=99.0, entry_idx=0, entry_price=100.0, entry_time=0,
        qty_total=10.0, leverage=10, tp1_price=101.0, tp2_price=102.0,
        tp1_qty=4.0, tp2_qty=2.0, runner_qty=4.0, breakeven_price=100.1,
        current_stop=99.0, regime="UP",
        entry_commission_rate=0.0005, exit_commission_rate=0.0005,
    )
    remaining = overrides.pop("remaining_qty", None)
    kw.update(overrides)
    pos = OpenPosition(**kw)
    if remaining is not None:  # __post_init__ remaining_qty'yi qty_total'a sıfırlar
        pos.remaining_qty = remaining
    return pos


def _c(o: float, h: float, l: float, cl: float, t: int = 1000) -> Candle:
    return Candle(open_time=t, open=o, high=h, low=l, close=cl, volume=1.0,
                  close_time=t + 999)


class TestSameBarCounters:
    def test_diagnostic_keys_are_exactly_the_spec(self):
        assert DIAGNOSTIC_KEYS == (
            "tp1_fills", "sl_tp1_same_bar", "tp1_then_stop_same_bar",
            "tp2_then_floor_same_bar", "fill_bar_exit", "fill_bar_total",
        )
        assert new_diagnostics() == {k: 0 for k in DIAGNOSTIC_KEYS}

    @pytest.mark.parametrize("order", [SAME_BAR_ORDER_PESSIMISTIC,
                                       SAME_BAR_ORDER_OPTIMISTIC])
    def test_sl_and_tp1_same_bar_counts_and_sl_wins(self, order):
        pos, diag = _pos(), new_diagnostics()
        closed = _process_candle_exits(
            pos, _c(100, 101.5, 98.5, 100), Direction.LONG,
            diag=diag, same_bar_order=order,
        )
        assert closed and pos.exit_reason == "SL"
        assert diag["sl_tp1_same_bar"] == 1
        assert diag["tp1_fills"] == 0  # TP1 kısmi dolumu OLMAZ (kötümser kural)
        assert diag["tp1_then_stop_same_bar"] == 0

    def test_sl_only_and_tp1_only_do_not_count_as_same_bar(self):
        pos, diag = _pos(), new_diagnostics()
        _process_candle_exits(pos, _c(100, 100.5, 98.5, 99), Direction.LONG, diag=diag)
        assert diag["sl_tp1_same_bar"] == 0 and diag["tp1_fills"] == 0
        pos2, diag2 = _pos(), new_diagnostics()
        closed = _process_candle_exits(
            pos2, _c(100, 101.2, 100.5, 101), Direction.LONG, diag=diag2,
        )
        assert not closed
        assert diag2["tp1_fills"] == 1 and diag2["sl_tp1_same_bar"] == 0
        assert diag2["tp1_then_stop_same_bar"] == 0

    @pytest.mark.parametrize("order", [SAME_BAR_ORDER_PESSIMISTIC,
                                       SAME_BAR_ORDER_OPTIMISTIC])
    def test_tp1_then_stop_same_bar_is_counted_in_both_modes(self, order):
        pos, diag = _pos(), new_diagnostics()
        _process_candle_exits(
            pos, _c(100, 101.2, 100.0, 100.5), Direction.LONG,
            diag=diag, same_bar_order=order,
        )
        assert diag["tp1_fills"] == 1
        assert diag["tp1_then_stop_same_bar"] == 1
        assert diag["sl_tp1_same_bar"] == 0

    @pytest.mark.parametrize("order", [SAME_BAR_ORDER_PESSIMISTIC,
                                       SAME_BAR_ORDER_OPTIMISTIC])
    def test_tp2_then_floor_same_bar_is_counted_in_both_modes(self, order):
        pos = _pos(tp1_filled=True, trailing_active=True, current_stop=100.1,
                   remaining_qty=6.0)
        diag = new_diagnostics()
        _process_candle_exits(
            pos, _c(101, 102.5, 100.5, 101.5), Direction.LONG,
            diag=diag, same_bar_order=order,
        )
        assert diag["tp2_then_floor_same_bar"] == 1
        assert diag["tp1_fills"] == 0 and diag["tp1_then_stop_same_bar"] == 0

    def test_tp2_without_floor_touch_is_not_counted(self):
        pos = _pos(tp1_filled=True, trailing_active=True, current_stop=100.1,
                   remaining_qty=6.0)
        diag = new_diagnostics()
        closed = _process_candle_exits(
            pos, _c(101.2, 102.5, 101.1, 102.0), Direction.LONG, diag=diag,
        )
        assert not closed and pos.tp2_filled
        assert diag["tp2_then_floor_same_bar"] == 0

    def test_short_direction_mirrors_long(self):
        # SHORT: giriş 100, stop 101, TP1 99, BE 99.9
        pos = _pos(direction=Direction.SHORT, stop_price=101.0, current_stop=101.0,
                   tp1_price=99.0, tp2_price=98.0, breakeven_price=99.9)
        diag = new_diagnostics()
        closed = _process_candle_exits(
            pos, _c(100, 100.0, 98.8, 99.5), Direction.SHORT, diag=diag,
        )
        assert closed and pos.exit_reason == "TRAIL"
        assert diag["tp1_fills"] == 1 and diag["tp1_then_stop_same_bar"] == 1

    def test_default_args_behave_as_before(self):
        """Yeni parametreler olmadan çağrı (eski imza) bugünkü kötümser
        davranışı verir ve hiçbir sayaç gerektirmez."""
        pos = _pos()
        assert _process_candle_exits(
            pos, _c(100, 101.2, 100.0, 100.5), Direction.LONG,
        ) is True
        assert pos.exit_reason == "TRAIL" and pos.exit_price == pytest.approx(100.1)


class TestOptimisticBranches:
    def test_branch_i_no_be_check_in_tp1_bar(self):
        """(i) TP1'in dolduğu mumda BE stopu değerlendirilmez."""
        c = _c(100, 101.2, 100.0, 100.5)
        pess, opt = _pos(), _pos()
        assert _process_candle_exits(pess, c, Direction.LONG) is True
        assert pess.exit_reason == "TRAIL"
        closed = _process_candle_exits(
            opt, c, Direction.LONG, same_bar_order=SAME_BAR_ORDER_OPTIMISTIC,
        )
        assert closed is False
        assert opt.tp1_filled and opt.trailing_active
        assert opt.current_stop == pytest.approx(100.1)
        assert opt.remaining_qty == pytest.approx(6.0)
        assert opt.exit_reason is None

    def test_branch_i_next_bar_stop_still_applies(self):
        """(i) yalnız AYNI mum için; sonraki mumda BE stopu normal işler."""
        opt = _pos()
        _process_candle_exits(
            opt, _c(100, 101.2, 100.0, 100.5), Direction.LONG,
            same_bar_order=SAME_BAR_ORDER_OPTIMISTIC,
        )
        closed = _process_candle_exits(
            opt, _c(100.4, 100.6, 100.0, 100.2, t=2000), Direction.LONG,
            same_bar_order=SAME_BAR_ORDER_OPTIMISTIC,
        )
        assert closed and opt.exit_reason == "TRAIL"

    def test_branch_ii_no_floor_check_in_tp2_bar(self):
        """(ii) TP2'nin dolduğu mumda TP1 tabanı kontrol edilmez."""
        c = _c(101, 102.5, 100.5, 101.5)

        def fresh():
            return _pos(tp1_filled=True, trailing_active=True,
                        current_stop=100.1, remaining_qty=6.0)

        pess, opt = fresh(), fresh()
        assert _process_candle_exits(pess, c, Direction.LONG) is True
        assert pess.exit_reason == "TRAIL" and pess.exit_price == pytest.approx(101.0)
        closed = _process_candle_exits(
            opt, c, Direction.LONG, same_bar_order=SAME_BAR_ORDER_OPTIMISTIC,
        )
        assert closed is False
        assert opt.tp2_filled
        # Taban yine YÜKSELTİLİR (karar durumu aynı); yalnız aynı-mum kapanışı yok.
        assert opt.current_stop == pytest.approx(101.0)
        assert opt.remaining_qty == pytest.approx(4.0)

    def test_branch_iii_no_exit_processing_in_fill_bar(self):
        """(iii) dolum mumunda (idx == entry_idx) çıkış işlenmez."""
        cfg = _golden_cfg()
        # [0] sinyal mumu, [1] dolum mumu (SL'e değer), [2] sakin, [3] sakin.
        candles = [
            _c(100, 100.2, 99.8, 100, t=0),
            _c(100, 100.5, 98.9, 100, t=1000),
            _c(100, 100.6, 99.5, 100, t=2000),
            _c(100, 100.6, 99.5, 100.2, t=3000),
        ]
        d_p, d_o = new_diagnostics(), new_diagnostics()
        t_p = manage_position(_pos(entry_idx=1, entry_time=1000), candles, cfg,
                              diag=d_p)
        t_o = manage_position(_pos(entry_idx=1, entry_time=1000), candles, cfg,
                              diag=d_o, same_bar_order=SAME_BAR_ORDER_OPTIMISTIC)
        assert t_p.exit_reason == "SL" and t_p.exit_idx == 1
        assert t_o.exit_reason == "EOD" and t_o.exit_idx == 3
        assert (d_p["fill_bar_exit"], d_p["fill_bar_total"]) == (1, 1)
        assert (d_o["fill_bar_exit"], d_o["fill_bar_total"]) == (0, 1)

    def test_branch_iii_keeps_mae_mfe_and_eod_unchanged(self):
        """MAE/MFE dolum mumunu YİNE işler (pozisyon yalnız çıkış açısından
        pasif); EOD kapanışı optimistic'te de çalışır."""
        cfg = _golden_cfg()
        candles = [
            _c(100, 100.2, 99.8, 100, t=0),
            _c(100, 100.5, 98.9, 100, t=1000),
            _c(100, 100.6, 99.5, 100, t=2000),
            _c(100, 100.6, 99.5, 100.2, t=3000),
        ]
        t_p = manage_position(_pos(entry_idx=1, entry_time=1000), candles, cfg)
        t_o = manage_position(_pos(entry_idx=1, entry_time=1000), candles, cfg,
                              same_bar_order=SAME_BAR_ORDER_OPTIMISTIC)
        assert t_o.mae_pct == pytest.approx(t_p.mae_pct)  # 98.9 düşüşü ikisinde de
        assert t_o.mae_pct < 0
        assert t_o.exit_reason == "EOD"
        assert t_o.exit_price == pytest.approx(candles[-1].close)

    def test_pessimistic_default_equals_explicit_pessimistic(self):
        cfg = _golden_cfg()
        candles = [
            _c(100, 100.2, 99.8, 100, t=0),
            _c(100, 101.2, 100.0, 100.5, t=1000),
            _c(100, 100.6, 99.5, 100, t=2000),
        ]
        a = manage_position(_pos(entry_idx=1, entry_time=1000), candles, cfg)
        b = manage_position(_pos(entry_idx=1, entry_time=1000), candles, cfg,
                            same_bar_order=SAME_BAR_ORDER_PESSIMISTIC)
        assert a.to_dict() == b.to_dict()
        assert a.exit_idx == b.exit_idx

    def test_diagnostics_do_not_change_the_trade(self):
        """Sayaç geçirmek karar yolunu değiştirmez (pessimistic)."""
        cfg = _golden_cfg()
        candles = [
            _c(100, 100.2, 99.8, 100, t=0),
            _c(100, 101.2, 100.0, 100.5, t=1000),
            _c(100, 102.6, 100.6, 102, t=2000),
        ]
        a = manage_position(_pos(entry_idx=1, entry_time=1000), candles, cfg)
        b = manage_position(_pos(entry_idx=1, entry_time=1000), candles, cfg,
                            diag=new_diagnostics())
        assert a.to_dict() == b.to_dict()

    def test_fill_bar_eod_is_not_counted_as_exit(self):
        cfg = _golden_cfg()
        candles = [_c(100, 100.2, 99.8, 100, t=0), _c(100, 100.5, 99.6, 100, t=1000)]
        diag = new_diagnostics()
        trade = manage_position(_pos(entry_idx=1, entry_time=1000), candles, cfg,
                                diag=diag)
        assert trade.exit_reason == "EOD" and trade.exit_idx == 1
        assert (diag["fill_bar_exit"], diag["fill_bar_total"]) == (0, 1)

    def test_fill_bar_exit_counts_only_the_fill_bar_itself(self):
        """Çıkış dolum mumundan BİR SONRAKİ mumda ise fill_bar_exit sayılmaz
        (sınır: `exit_idx == entry_idx`, `<= entry_idx + 1` DEĞİL)."""
        cfg = _golden_cfg()
        candles = [
            _c(100, 100.2, 99.8, 100, t=0),
            _c(100, 100.5, 99.6, 100, t=1000),   # dolum mumu, sakin
            _c(100, 100.5, 98.9, 100, t=2000),   # SL'e değer (idx 2)
            _c(100, 100.6, 99.5, 100, t=3000),
        ]
        diag = new_diagnostics()
        trade = manage_position(_pos(entry_idx=1, entry_time=1000), candles, cfg,
                                diag=diag)
        assert trade.exit_reason == "SL" and trade.exit_idx == 2
        assert (diag["fill_bar_exit"], diag["fill_bar_total"]) == (0, 1)

    def test_branch_iii_also_skips_stale_reaper_in_fill_bar(self):
        """(iii) dolum mumunda REAPER/STALE_TP/yapı adımları da işlenmez;
        pessimistic'te REAPER dolum mumunda kapatırdı."""
        base = _golden_cfg()

        class _Cfg2:
            scalper_max_hold_hours = 0.0001  # ~0.36 sn: yaş 999 ms >= eşik

            def __getattr__(self, name):
                return getattr(base, name)

        candles = [
            _c(100, 100.2, 99.8, 100, t=0),
            _c(100, 100.5, 99.6, 100, t=1000),
            _c(100, 100.5, 99.6, 100, t=2000),
            _c(100, 100.5, 99.6, 100, t=3000),
        ]
        d_p, d_o = new_diagnostics(), new_diagnostics()
        t_p = manage_position(_pos(entry_idx=1, entry_time=1000), candles,
                              _Cfg2(), diag=d_p)
        t_o = manage_position(_pos(entry_idx=1, entry_time=1000), candles,
                              _Cfg2(), diag=d_o,
                              same_bar_order=SAME_BAR_ORDER_OPTIMISTIC)
        assert t_p.exit_reason == "REAPER" and t_p.exit_idx == 1
        assert d_p["fill_bar_exit"] == 1
        assert t_o.exit_reason == "REAPER" and t_o.exit_idx == 2
        assert d_o["fill_bar_exit"] == 0

    def test_unknown_order_is_rejected_by_the_low_level_functions(self):
        """Yazım hatası ('Optimistic') sessizce pessimistic olmamalı."""
        cfg = _golden_cfg()
        with pytest.raises(ValueError, match="same_bar_order"):
            _process_candle_exits(_pos(), _c(100, 100.5, 99.6, 100),
                                  Direction.LONG, same_bar_order="Optimistic")
        with pytest.raises(ValueError, match="same_bar_order"):
            manage_position(_pos(), _flat_candles(3), cfg,
                            same_bar_order="optimistc")

    def test_run_backtest_rejects_unknown_order(self):
        import asyncio

        with pytest.raises(ValueError, match="same_bar_order"):
            asyncio.run(bt.run_backtest(
                days=1, symbols=["BTCUSDT"], strategy_names="C",
                same_bar_order="sideways",
            ))

    def test_parser_defaults_and_choices(self):
        parser = bt._build_arg_parser()
        args = parser.parse_args([])
        assert args.diagnostics is False
        # None = "verilmedi": açık `pessimistic` ile ayırt edilebilsin diye.
        assert args.same_bar_order is None
        assert parser.parse_args(
            ["--same-bar-order", "pessimistic"]
        ).same_bar_order == "pessimistic"
        args = parser.parse_args(["--diagnostics", "--same-bar-order", "optimistic"])
        assert args.diagnostics is True and args.same_bar_order == "optimistic"
        with pytest.raises(SystemExit):
            parser.parse_args(["--same-bar-order", "sideways"])


def _golden_cfg():
    from test_golden_backtest import _GoldenCfg  # type: ignore

    return _GoldenCfg()


# --------------------------------------------------------------------------
# H2 — CLI kablolaması (golden fixture, AĞSIZ): rapor şekli ve sayılar
# --------------------------------------------------------------------------


class TestE17CliWiring:
    def _args(self, monkeypatch, tmp_path, **overrides):
        from test_backtest_measurement import TestCliWiring  # type: ignore

        return TestCliWiring()._args(monkeypatch, tmp_path, **overrides)

    @staticmethod
    def _report(tmp_path):
        reports = sorted(Path(tmp_path, "logs").glob("backtest_*.json"))
        assert reports, "JSON rapor yazılmadı"
        return json.loads(reports[-1].read_text(encoding="utf-8"))

    @pytest.mark.asyncio
    async def test_default_report_has_no_new_keys(self, monkeypatch, tmp_path):
        await bt.main_async(self._args(monkeypatch, tmp_path))
        report = self._report(tmp_path)
        assert "diagnostics" not in report
        assert "sim_options" not in report["provenance"]
        # Golden sayılar (CLI yolu): bayraksız koşu eskisiyle aynı.
        assert report["overall"]["trades"] == 2
        assert round(report["overall"]["total_pnl"], 2) == 23.79
        assert report["missed_signals"] == {"regime_gate": 4}

    @pytest.mark.asyncio
    async def test_diagnostics_flag_writes_block_and_keeps_results(
        self, monkeypatch, tmp_path
    ):
        await bt.main_async(self._args(monkeypatch, tmp_path, diagnostics=None))
        report = self._report(tmp_path)
        assert list(report["diagnostics"]) == list(DIAGNOSTIC_KEYS)
        assert report["provenance"]["sim_options"] == {
            "same_bar_order": "pessimistic", "diagnostics": True,
        }
        # Karar yolu aynı: golden işlem sayısı ve PnL değişmez.
        assert report["overall"]["trades"] == 2
        assert round(report["overall"]["total_pnl"], 2) == 23.79
        assert report["missed_signals"] == {"regime_gate": 4}
        diag = report["diagnostics"]
        assert diag["fill_bar_total"] == 2
        assert diag["tp1_fills"] == 2  # iki işlem de TRAIL = TP1 görmüş
        assert diag["sl_tp1_same_bar"] == 0

    @pytest.mark.asyncio
    async def test_optimistic_flag_is_recorded_without_diagnostics_block(
        self, monkeypatch, tmp_path
    ):
        await bt.main_async(
            self._args(monkeypatch, tmp_path, same_bar_order="optimistic")
        )
        report = self._report(tmp_path)
        assert "diagnostics" not in report
        assert report["provenance"]["sim_options"] == {
            "same_bar_order": "optimistic", "diagnostics": False,
        }

    @pytest.mark.asyncio
    async def test_explicit_pessimistic_flag_is_recorded(
        self, monkeypatch, tmp_path
    ):
        """Açık `--same-bar-order pessimistic` (tek başına) sim_options yazar;
        verilmeyen bayraktan ayırt edilir."""
        await bt.main_async(
            self._args(monkeypatch, tmp_path, same_bar_order="pessimistic")
        )
        report = self._report(tmp_path)
        assert "diagnostics" not in report
        assert report["provenance"]["sim_options"] == {
            "same_bar_order": "pessimistic", "diagnostics": False,
        }
        assert report["overall"]["trades"] == 2
        assert round(report["overall"]["total_pnl"], 2) == 23.79

    @pytest.mark.asyncio
    async def test_optimistic_order_reaches_manage_position_end_to_end(
        self, monkeypatch, tmp_path
    ):
        """main_async -> run_backtest -> simulate_symbol -> manage_position
        zincirinde `same_bar_order` HER halkada taşınır (zincirin bir halkası
        düşerse DIAG_SBO sessizce pessimistic olurdu)."""
        seen_sim: List[str] = []
        seen_mgr: List[str] = []
        real_sim, real_mgr = bt.simulate_symbol, bt.manage_position

        def spy_sim(*a, **kw):
            seen_sim.append(kw.get("same_bar_order"))
            return real_sim(*a, **kw)

        def spy_mgr(*a, **kw):
            seen_mgr.append(kw.get("same_bar_order"))
            return real_mgr(*a, **kw)

        monkeypatch.setattr(bt, "simulate_symbol", spy_sim)
        monkeypatch.setattr(bt, "manage_position", spy_mgr)
        await bt.main_async(
            self._args(monkeypatch, tmp_path, same_bar_order="optimistic")
        )
        assert seen_sim and set(seen_sim) == {"optimistic"}
        assert seen_mgr and set(seen_mgr) == {"optimistic"}
        # Varsayılan koşuda ise pessimistic taşınır.
        seen_sim.clear()
        seen_mgr.clear()
        await bt.main_async(self._args(monkeypatch, tmp_path))
        assert set(seen_sim) == {"pessimistic"}
        assert set(seen_mgr) == {"pessimistic"}

    @pytest.mark.asyncio
    async def test_optimistic_changes_results_vs_pessimistic(
        self, monkeypatch, tmp_path
    ):
        """Golden fixture'da optimistic sonuçları GERÇEKTEN değiştirir
        (bayrak kablolaması yalnız meta veri değildir)."""
        await bt.main_async(self._args(monkeypatch, tmp_path))
        pess = self._report(tmp_path)["overall"]
        for p in Path(tmp_path, "logs").glob("backtest_*.json"):
            p.unlink()
        await bt.main_async(
            self._args(monkeypatch, tmp_path, same_bar_order="optimistic")
        )
        opt = self._report(tmp_path)["overall"]
        assert (opt["trades"], round(opt["total_pnl"], 6)) != (
            pess["trades"], round(pess["total_pnl"], 6)
        )

    @pytest.mark.asyncio
    async def test_permutations_with_optimistic_order_is_rejected(
        self, monkeypatch, tmp_path
    ):
        """Gözlenen optimistic vs null pessimistic karşılaştırması geçersiz
        p-değeri verirdi: reddedilir (ağ/simülasyon başlamadan)."""
        with pytest.raises(ValueError, match="permutations"):
            await bt.main_async(self._args(
                monkeypatch, tmp_path, same_bar_order="optimistic",
                permutations=2, permutation_seed=7,
                permutation_metrics="total_pnl",
            ))

    @pytest.mark.asyncio
    async def test_permutation_path_is_unaffected_by_the_flags(
        self, monkeypatch, tmp_path
    ):
        """Permütasyon turları `diag=None` / varsayılan sıra ile koşar: sayaçlar
        yalnız GERÇEK koşuyu sayar (permüte turlar karışmaz)."""
        await bt.main_async(self._args(monkeypatch, tmp_path, diagnostics=None))
        base = self._report(tmp_path)["diagnostics"]
        for p in Path(tmp_path, "logs").glob("backtest_*.json"):
            p.unlink()
        await bt.main_async(self._args(
            monkeypatch, tmp_path, diagnostics=None, permutations=2,
            permutation_seed=7, permutation_metrics="total_pnl",
        ))
        report = self._report(tmp_path)
        assert report["diagnostics"] == base
        assert report["permutation"]["permutations"] == 2


# --------------------------------------------------------------------------
# H3 — rol üçlüleri (sentetik mum, AĞSIZ)
# --------------------------------------------------------------------------

_STEP_MS = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "4h": 14_400_000,
}
_PER_DAY = {"1m": 1440, "5m": 288, "15m": 96, "30m": 48, "1h": 24, "4h": 6}
# 4 saatlik sınıra hizalı bitiş: tüm TF'lerde son mum tam END-1'de kapanır.
_END = 122_222 * 14_400_000
_DAYS = 2


class _FakeExchange:
    """Binance benzeri sahte kaynak: epoch'a hizalı ızgarada, `end_time`'a
    kadar KAPANMIŞ son `limit` mumu (eski→yeni) döner. Fiyat open_time'dan
    deterministik türer."""

    def __init__(self) -> None:
        self.calls: List[tuple] = []

    async def __call__(self, symbol, interval, limit, end_time=None):
        self.calls.append((symbol, interval, limit, end_time))
        step = _STEP_MS[interval]
        last_open = (end_time + 1) // step * step - step  # close_time = open+step-1 <= end
        out = []
        for k in range(limit):
            ot = last_open - (limit - 1 - k) * step
            base = 100.0 + (ot // step % 97) * 0.01
            out.append(Candle(
                open_time=ot, open=base, high=base + 0.05, low=base - 0.05,
                close=base + 0.01, volume=1.0, close_time=ot + step - 1,
            ))
        return out


_TRIPLES = [
    ("5m", "30m", "1h"),
    ("15m", "1h", "4h"),
    ("30m", "4h", "4h"),
    ("1h", "4h", "4h"),
    ("1m", "5m", "15m"),
]
_TRIPLE_IDS = ["/".join(t) for t in _TRIPLES]
_WARMUPS = (150, 100, 250)  # giriş / bağlam / rejim bar sayıları


def _expected_lengths(triple) -> Dict[str, int]:
    """`gather_symbol_data` `out[interval]` ile yazar → rol-anahtarı
    çakışmasında SONRAKİ (rejim) seri kazanır."""
    out: Dict[str, int] = {}
    for interval, warm in zip(triple, _WARMUPS):
        out[interval] = _DAYS * _PER_DAY[interval] + warm
    return out


class TestRoleTriplesE17:
    def test_warmup_constants_are_150_100_250(self):
        assert bt._WARMUP_BY_ROLE == _WARMUPS
        assert (bt._CTX_5M_WINDOW, bt._CTX_15M_WINDOW, bt._CTX_4H_WINDOW) == _WARMUPS

    @pytest.mark.parametrize("triple", _TRIPLES, ids=_TRIPLE_IDS)
    def test_resolve_timeframes_reads_the_triple(self, triple):
        from types import SimpleNamespace

        cfg = SimpleNamespace(scalper_tf_entry=triple[0],
                              scalper_tf_context=triple[1],
                              scalper_tf_regime=triple[2])
        assert bt.resolve_timeframes(cfg) == triple

    @pytest.mark.asyncio
    @pytest.mark.parametrize("triple", _TRIPLES, ids=_TRIPLE_IDS)
    async def test_window_lengths_150_100_250(self, triple):
        data = await bt.gather_symbol_data(
            _FakeExchange(), "BTCUSDT", _DAYS, end_time=_END, timeframes=triple,
        )
        expected = _expected_lengths(triple)
        assert {k: len(v) for k, v in data.items()} == expected
        # Isınma + pencere: her serinin SON mumu pencere bitişinde kapanır.
        for series in data.values():
            assert series[-1].close_time == _END - 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("triple", _TRIPLES, ids=_TRIPLE_IDS)
    async def test_context_slices_have_role_window_sizes(self, triple):
        entry, context, regime = triple
        data = await bt.gather_symbol_data(
            _FakeExchange(), "BTCUSDT", _DAYS, end_time=_END, timeframes=triple,
        )
        e_s, c_s, r_s = data[entry], data[context], data[regime]
        idx = len(e_s) - 2  # sondan bir önceki giriş mumu
        ctx = build_context("BTCUSDT", e_s, c_s, r_s, idx, 10)
        assert len(ctx.candles_5m) == bt._CTX_5M_WINDOW      # giriş: 150
        assert len(ctx.candles_15m) == bt._CTX_15M_WINDOW    # bağlam: 100
        assert len(ctx.candles_4h) == bt._CTX_4H_WINDOW      # rejim: 250

    @pytest.mark.asyncio
    @pytest.mark.parametrize("triple", _TRIPLES, ids=_TRIPLE_IDS)
    async def test_no_look_ahead_at_any_sampled_index(self, triple):
        entry, context, regime = triple
        data = await bt.gather_symbol_data(
            _FakeExchange(), "BTCUSDT", _DAYS, end_time=_END, timeframes=triple,
        )
        e_s, c_s, r_s = data[entry], data[context], data[regime]
        n = len(e_s)
        for idx in (0, 1, n // 3, n // 2, n - 2, n - 1):
            ctx = build_context("BTCUSDT", e_s, c_s, r_s, idx, 10)
            cutoff = e_s[idx].close_time
            for name, sliced, series in (
                ("giriş", ctx.candles_5m, e_s),
                ("bağlam", ctx.candles_15m, c_s),
                ("rejim", ctx.candles_4h, r_s),
            ):
                assert all(c.close_time <= cutoff for c in sliced), (
                    f"{name} dilimi geleceğe bakıyor: idx={idx}"
                )
                if sliced:
                    # Dilim, kesim anına kadar KAPANMIŞ SON mumda biter ve
                    # hemen sonraki (henüz kapanmamış) mum dilimde YOKTUR.
                    later = [c for c in series if c.close_time > cutoff]
                    assert sliced[-1].close_time == max(
                        c.close_time for c in series if c.close_time <= cutoff
                    )
                    if later:
                        assert later[0] not in sliced
            assert ctx.candles_5m[-1] == e_s[idx]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("triple", _TRIPLES, ids=_TRIPLE_IDS)
    async def test_cache_keys_and_reload_without_network(self, triple, tmp_path):
        entry, context, regime = triple
        ex = _FakeExchange()
        data = await bt.gather_symbol_data(
            ex, "BTCUSDT", _DAYS, end_time=_END, timeframes=triple,
            cache_dir=tmp_path,
        )
        files = sorted(p.name for p in tmp_path.glob("*.json.gz"))
        # Her zaman 3 ayrı anahtar (aynı-TF üçlülerinde 4h iki farklı pencere).
        assert len(files) == 3 and len(set(files)) == 3
        # Ağsız yeniden okuma: aynı veri, sıfır istek.
        ex2 = _FakeExchange()
        again = await bt.gather_symbol_data(
            ex2, "BTCUSDT", _DAYS, end_time=_END, timeframes=triple,
            cache_dir=tmp_path,
        )
        assert ex2.calls == []
        assert again == data

    @pytest.mark.asyncio
    @pytest.mark.parametrize("clash", [
        ("5m", "15m", "5m"),   # giriş == rejim
        ("5m", "5m", "4h"),    # giriş == bağlam
        ("15m", "15m", "15m"),
    ], ids=["entry=regime", "entry=context", "all-equal"])
    async def test_entry_equal_to_context_or_regime_is_rejected(self, clash):
        """Seri sözlüğü aralıkla anahtarlandığından giriş rolünün ısınma
        penceresi sessizce ezilirdi; `run_backtest` veri çekmeden reddeder
        (bağlam==rejim serbest, aşağıdaki test)."""
        from types import SimpleNamespace

        cfg = SimpleNamespace(scalper_tf_entry=clash[0],
                              scalper_tf_context=clash[1],
                              scalper_tf_regime=clash[2])
        with pytest.raises(ValueError, match="çakış"):
            await bt.run_backtest(
                days=1, symbols=["BTCUSDT"], strategy_names="C", cfg=cfg,
                end_time_ms=_END,
            )

    def test_check_timeframe_roles_allows_context_equal_regime(self):
        bt.check_timeframe_roles(("30m", "4h", "4h"))
        bt.check_timeframe_roles(("1h", "4h", "4h"))

    @pytest.mark.asyncio
    @pytest.mark.parametrize("triple", [("30m", "4h", "4h"), ("1h", "4h", "4h")],
                             ids=["30m/4h/4h", "1h/4h/4h"])
    async def test_context_equals_regime_uses_two_4h_keys_regime_wins(
        self, triple, tmp_path
    ):
        entry, context, regime = triple
        assert context == regime == "4h"
        data = await bt.gather_symbol_data(
            _FakeExchange(), "BTCUSDT", _DAYS, end_time=_END, timeframes=triple,
            cache_dir=tmp_path,
        )
        four_h = sorted(tmp_path.glob("BTCUSDT_4h_*.json.gz"))
        assert len(four_h) == 2
        need_ctx = _DAYS * _PER_DAY["4h"] + bt._CTX_15M_WINDOW   # +100
        need_reg = _DAYS * _PER_DAY["4h"] + bt._CTX_4H_WINDOW    # +250
        start_ctx = kline_cache.window_start_ms("4h", need_ctx, _END)
        start_reg = kline_cache.window_start_ms("4h", need_reg, _END)
        assert start_ctx != start_reg
        assert start_reg < start_ctx  # +250 penceresi daha erken başlar
        ctx_series = kline_cache.load(tmp_path, "BTCUSDT", "4h", start_ctx, _END)
        reg_series = kline_cache.load(tmp_path, "BTCUSDT", "4h", start_reg, _END)
        assert ctx_series is not None and reg_series is not None
        assert len(ctx_series) == need_ctx and len(reg_series) == need_reg
        # Rejim serisi (+250) bağlam slotunu EZER: harness tek 4h serisi görür.
        assert data["4h"] == reg_series
        assert len(data["4h"]) == need_reg
        # Bağlam rolü yalnız son 100 barı okur: kuyruk +100 serisinin kendisi.
        assert data["4h"][-bt._CTX_15M_WINDOW:] == ctx_series[-bt._CTX_15M_WINDOW:]
        assert data["4h"][-need_ctx:] == ctx_series

    @pytest.mark.asyncio
    async def test_distinct_roles_have_distinct_series_for_5m_30m_1h(self):
        """Çakışma olmayan üçlüde üç rol üç ayrı seridir (ezme yok)."""
        data = await bt.gather_symbol_data(
            _FakeExchange(), "BTCUSDT", _DAYS, end_time=_END,
            timeframes=("5m", "30m", "1h"),
        )
        assert sorted(data) == ["1h", "30m", "5m"]
