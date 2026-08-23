"""
src/strategies/scalper/structure.py — piyasa yapısı (BOS/CHoCH) testleri.

Kapsam:
1. Saf durum makinesi: BOS/CHoCH sırası, pivot onay gecikmesi, eşitlik
   sınırları, seviye başına TEK olay, fitil vs kapanış modu, look-ahead yok.
2. Konfigürasyon çözümü (rol/zaman dilimi, pencere boyları, hatalı değer).
3. Karar fonksiyonları: giriş kapısı ve çıkış tetikleyicisi.
4. PARİTE: canlı motorun kullandığı fonksiyon = harness'ın kullandığı
   fonksiyon, aynı girdi → aynı çıktı (DECISIONS P1).
5. Kapalıyken (varsayılan) harness davranışı DEĞİŞMEZ.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import List, Optional

import pytest

import src.strategies.scalper.backtest as backtest_module
from src.strategies.scalper.backtest import (
    _CTX_4H_WINDOW,
    _CTX_5M_WINDOW,
    _CTX_15M_WINDOW,
    _StructureFeed,
    build_context,
    manage_position,
    open_position,
    simulate_symbol,
)
from src.strategies.scalper.indicators import swing_points
from src.strategies.scalper.structure import (
    BOS,
    CHOCH,
    StructureDirection,
    StructureExitInput,
    _is_pivot_high,
    _is_pivot_low,
    detect_structure,
    resolve_structure_role,
    scan_structure,
    structure_enabled,
    structure_exit_action,
    structure_exit_mode,
    structure_gate_blocks,
    structure_series,
    structure_snapshot,
    structure_state_for,
    structure_timeframe,
    structure_window_bars,
)
from src.strategies.scalper.types import (
    Candle,
    Direction,
    Regime,
    ScalpSignal,
    StrategyContext,
    StrategyProtocol,
)

_MIN = 60_000  # ms


def _c(i: int, high: float, low: float, close: float, interval_ms: int = _MIN) -> Candle:
    open_time = i * interval_ms
    return Candle(
        open_time=open_time, open=close, high=high, low=low, close=close,
        volume=1.0, close_time=open_time + interval_ms - 1,
    )


def _series(rows: List[tuple], interval_ms: int = _MIN) -> List[Candle]:
    """rows = [(high, low, close), ...]"""
    return [_c(i, h, l, cl, interval_ms) for i, (h, l, cl) in enumerate(rows)]


# BOS (yukarı kırılım) → CHoCH (aşağı kırılım) üreten referans seri; pivot=1.
# Elle doğrulanmış: pivot high idx1 (bar2'de onaylanır), pivot low idx3
# (bar4'te onaylanır), bar5 close 12.5 > 12 → BOS(BULL), bar6 close 6.5 < 7 →
# CHoCH(BEAR).
_REF_ROWS = [
    (10.0, 9.0, 9.5),     # 0
    (12.0, 10.0, 11.0),   # 1  <- pivot high (12)
    (11.0, 8.0, 8.5),     # 2
    (9.0, 7.0, 7.5),      # 3  <- pivot low (7)
    (10.0, 8.0, 9.5),     # 4
    (13.0, 9.0, 12.5),    # 5  <- close 12.5 > 12  => BOS, yön BULL
    (13.0, 6.0, 6.5),     # 6  <- close 6.5 < 7    => CHoCH, yön BEAR
]


@dataclass
class _Cfg:
    """Yapı ayarlarını taşıyan minimal cfg (settings sözleşmesinin alt kümesi)."""

    scalper_structure_gate: bool = True
    scalper_structure_tf: str = "context"
    scalper_structure_pivot: int = 1
    scalper_structure_use_close: bool = True
    scalper_structure_block_counter: bool = True
    scalper_structure_exit: str = "off"
    scalper_tf_entry: str = "1m"
    scalper_tf_context: str = "5m"
    scalper_tf_regime: str = "15m"


# ==========================================================================
# 1. Saf durum makinesi
# ==========================================================================

class TestScanStructure:
    def test_bos_then_choch_sequence(self):
        events, state = scan_structure(_series(_REF_ROWS), 1, 1)

        assert [e.event for e in events] == [BOS, CHOCH]
        assert [e.bar_index for e in events] == [5, 6]
        assert events[0].price == 12.0 and events[0].pivot_index == 1
        assert events[1].price == 7.0 and events[1].pivot_index == 3
        assert state.direction == StructureDirection.BEAR
        assert state.last_event == CHOCH
        assert state.event_bar_index == 6
        assert state.age_bars == 0
        assert state.bars == 7

    def test_first_break_without_direction_is_bos_not_choch(self):
        """Yön NONE iken ilk kırılım BOS'tur: değişecek bir 'karakter' yok."""
        _, state = scan_structure(_series(_REF_ROWS[:6]), 1, 1)
        assert state.direction == StructureDirection.BULL
        assert state.last_event == BOS

    def test_age_bars_counts_bars_since_event(self):
        rows = _REF_ROWS + [(7.0, 6.2, 6.4), (7.0, 6.2, 6.4)]
        _, state = scan_structure(_series(rows), 1, 1)
        assert state.event_bar_index == 6
        assert state.age_bars == 2

    def test_empty_and_short_series_are_neutral(self):
        assert detect_structure([], 5, 5).direction == StructureDirection.NONE
        short = _series([(10.0, 9.0, 9.5)] * 4)
        st = detect_structure(short, 5, 5)
        assert st.direction == StructureDirection.NONE
        assert st.last_event is None and st.swing_high is None

    def test_pivot_confirmation_delay_is_modelled(self):
        """Bir olay ASLA pivot mumundan `pivot_right` mum içinde oluşamaz —
        pivot o kadar mum kapanmadan onaylanamaz (look-ahead koruması)."""
        candles = _random_walk(400, seed=7)
        for right in (1, 3, 5, 8):
            events, _ = scan_structure(candles, right, right)
            assert events, f"right={right} için olay üretilmedi (test anlamsızlaşır)"
            for e in events:
                assert e.bar_index > e.pivot_index + right, (
                    f"olay pivot onayından ÖNCE üretildi: {e}"
                )

    def test_no_lookahead_prefix_stability(self):
        """Bir önek (prefix) üzerinde hesaplanan olaylar, tüm seri üzerinde
        hesaplananların o öneke düşen kısmıyla BİREBİR aynı olmalı."""
        candles = _random_walk(300, seed=11)
        full_events, _ = scan_structure(candles, 5, 5)
        for k in (50, 120, 200, 299):
            prefix_events, _ = scan_structure(candles[:k], 5, 5)
            expected = [e for e in full_events if e.bar_index < k]
            assert [(e.event, e.bar_index, e.price) for e in prefix_events] == [
                (e.event, e.bar_index, e.price) for e in expected
            ]

    def test_equality_is_not_a_break(self):
        """Kırılım KESİN aşmadır: close == seviye olay üretmez."""
        rows = list(_REF_ROWS[:5]) + [(13.0, 9.0, 12.0)]  # close tam 12.0 = seviye
        events, state = scan_structure(_series(rows), 1, 1)
        assert events == []
        assert state.direction == StructureDirection.NONE
        assert state.swing_high == 12.0 and state.swing_high_crossed is False

    def test_wick_mode_breaks_where_close_mode_does_not(self):
        rows = list(_REF_ROWS[:5]) + [(12.5, 9.0, 11.0)]  # high 12.5 > 12, close 11 < 12
        candles = _series(rows)
        assert scan_structure(candles, 1, 1, use_close=True)[0] == []
        wick_events, wick_state = scan_structure(candles, 1, 1, use_close=False)
        assert [e.event for e in wick_events] == [BOS]
        assert wick_state.direction == StructureDirection.BULL

    def test_level_fires_only_once(self):
        """Aynı seviye ikinci kez olay üretmez (yeni pivot onaylanana kadar)."""
        rows = list(_REF_ROWS[:6]) + [(13.0, 12.0, 12.6), (13.0, 12.0, 12.7)]
        events, _ = scan_structure(_series(rows), 1, 1)
        assert [e.event for e in events] == [BOS]
        assert [e.bar_index for e in events] == [5]

    def test_pivot_helpers_match_indicators_swing_points(self):
        """Aynı kod tabanında iki farklı swing tanımı olmamalı: bu modülün
        pivot testi `indicators.swing_points` ile birebir aynı sonucu vermeli."""
        candles = _random_walk(200, seed=3)
        for left, right in ((3, 3), (5, 5)):
            highs_idx, lows_idx = swing_points(candles, left, right)
            mine_h = [
                i for i in range(left, len(candles) - right)
                if _is_pivot_high(candles, i, left, right)
            ]
            mine_l = [
                i for i in range(left, len(candles) - right)
                if _is_pivot_low(candles, i, left, right)
            ]
            assert mine_h == highs_idx
            assert mine_l == lows_idx

    def test_deterministic_repeat(self):
        candles = _random_walk(250, seed=5)
        a = scan_structure(candles, 5, 5)
        b = scan_structure(candles, 5, 5)
        assert a == b


def _random_walk(n: int, seed: int) -> List[Candle]:
    rng = random.Random(seed)
    price = 100.0
    out: List[Candle] = []
    for i in range(n):
        price = max(1.0, price + rng.uniform(-1.5, 1.5))
        high = price + abs(rng.uniform(0.05, 1.0))
        low = price - abs(rng.uniform(0.05, 1.0))
        close = rng.uniform(low, high)
        out.append(_c(i, high, low, close))
    return out


# ==========================================================================
# 2. Konfigürasyon çözümü
# ==========================================================================

class TestConfigResolution:
    def test_role_names_and_timeframe_strings(self):
        cfg = _Cfg()
        assert resolve_structure_role(cfg) == "context"
        assert resolve_structure_role(_Cfg(scalper_structure_tf="regime")) == "regime"
        assert resolve_structure_role(_Cfg(scalper_structure_tf="5m")) == "context"
        assert resolve_structure_role(_Cfg(scalper_structure_tf="15m")) == "regime"
        assert resolve_structure_role(_Cfg(scalper_structure_tf="1m")) == "entry"
        assert resolve_structure_role(_Cfg(scalper_structure_tf="CONTEXT ")) == "context"

    def test_unknown_timeframe_raises(self):
        with pytest.raises(ValueError):
            resolve_structure_role(_Cfg(scalper_structure_tf="4h"))

    def test_timeframe_and_window_match_engine_and_harness(self):
        """Pencere boyları harness'ın dilim sabitleriyle AYNI olmalı — yapı
        durum makinesi geçmişe bağımlıdır, farklı pencere farklı yön üretir."""
        assert structure_window_bars(_Cfg(scalper_structure_tf="entry")) == _CTX_5M_WINDOW
        assert structure_window_bars(_Cfg(scalper_structure_tf="context")) == _CTX_15M_WINDOW
        assert structure_window_bars(_Cfg(scalper_structure_tf="regime")) == _CTX_4H_WINDOW
        assert structure_timeframe(_Cfg()) == "5m"
        assert structure_timeframe(_Cfg(scalper_structure_tf="regime")) == "15m"

    def test_defaults_are_inert(self):
        """Alanları HİÇ tanımlamayan bir cfg (ör. golden test) için her şey
        kapalı olmalı: yeni özellik mevcut davranışı değiştirmez."""

        class _Bare:
            pass

        bare = _Bare()
        assert structure_exit_mode(bare) == "off"
        assert structure_enabled(bare) is False
        assert structure_gate_blocks(None, Direction.LONG, bare) is False

    def test_invalid_exit_mode_falls_back_to_off(self):
        assert structure_exit_mode(_Cfg(scalper_structure_exit="hayir")) == "off"

    def test_series_selection_uses_existing_context_lists(self):
        ctx = _mk_ctx(
            candles_5m=_series([(1, 1, 1)]),
            candles_15m=_series([(2, 2, 2)] * 2),
            candles_4h=_series([(3, 3, 3)] * 3),
        )
        assert len(structure_series(ctx, _Cfg(scalper_structure_tf="entry"))) == 1
        assert len(structure_series(ctx, _Cfg(scalper_structure_tf="context"))) == 2
        assert len(structure_series(ctx, _Cfg(scalper_structure_tf="regime"))) == 3


def _mk_ctx(candles_5m=None, candles_15m=None, candles_4h=None) -> StrategyContext:
    return StrategyContext(
        symbol="TESTUSDT",
        regime=Regime.RANGE,
        candles_4h=candles_4h or [],
        candles_15m=candles_15m or [],
        candles_5m=candles_5m or [],
        current_price=100.0,
        atr_5m=1.0,
        leverage=10,
    )


# ==========================================================================
# 3. Giriş kapısı
# ==========================================================================

class TestEntryGate:
    def test_bear_structure_blocks_long_and_allows_short(self):
        state = detect_structure(_series(_REF_ROWS), 1, 1)
        assert state.direction == StructureDirection.BEAR
        cfg = _Cfg()
        assert structure_gate_blocks(state, Direction.LONG, cfg) is True
        assert structure_gate_blocks(state, Direction.SHORT, cfg) is False

    def test_bull_structure_blocks_short_and_allows_long(self):
        state = detect_structure(_series(_REF_ROWS[:6]), 1, 1)
        assert state.direction == StructureDirection.BULL
        cfg = _Cfg()
        assert structure_gate_blocks(state, Direction.SHORT, cfg) is True
        assert structure_gate_blocks(state, Direction.LONG, cfg) is False

    def test_gate_off_never_blocks(self):
        state = detect_structure(_series(_REF_ROWS), 1, 1)
        assert structure_gate_blocks(
            state, Direction.LONG, _Cfg(scalper_structure_gate=False)
        ) is False

    def test_block_counter_false_never_blocks(self):
        state = detect_structure(_series(_REF_ROWS), 1, 1)
        assert structure_gate_blocks(
            state, Direction.LONG, _Cfg(scalper_structure_block_counter=False)
        ) is False

    def test_unknown_structure_never_blocks(self):
        """Yön NONE (yeterli pivot yok) sessizce 'hiç işlem açma'ya dönüşmemeli."""
        state = detect_structure(_series([(10.0, 9.0, 9.5)] * 6), 5, 5)
        assert state.direction == StructureDirection.NONE
        assert structure_gate_blocks(state, Direction.LONG, _Cfg()) is False
        assert structure_gate_blocks(state, Direction.SHORT, _Cfg()) is False


# ==========================================================================
# 4. Çıkış tetikleyicisi
# ==========================================================================

def _exit_inp(direction=Direction.LONG, entry_close_time=0, current_price=100.0,
              current_stop=95.0, breakeven_price=99.0) -> StructureExitInput:
    return StructureExitInput(
        direction=direction, entry_close_time=entry_close_time,
        current_price=current_price, current_stop=current_stop,
        breakeven_price=breakeven_price,
    )


class TestExitTrigger:
    def _bear_choch_state(self):
        state = detect_structure(_series(_REF_ROWS), 1, 1)
        assert state.last_event == CHOCH and state.direction == StructureDirection.BEAR
        return state

    def test_off_mode_never_acts(self):
        assert structure_exit_action(
            self._bear_choch_state(), _exit_inp(), _Cfg(scalper_structure_exit="off")
        ) == "none"

    def test_close_mode_on_opposite_choch(self):
        assert structure_exit_action(
            self._bear_choch_state(), _exit_inp(),
            _Cfg(scalper_structure_exit="close"),
        ) == "close"

    def test_same_direction_choch_does_not_act(self):
        assert structure_exit_action(
            self._bear_choch_state(), _exit_inp(direction=Direction.SHORT),
            _Cfg(scalper_structure_exit="close"),
        ) == "none"

    def test_bos_does_not_act(self):
        state = detect_structure(_series(_REF_ROWS[:6]), 1, 1)
        assert state.last_event == BOS
        assert structure_exit_action(
            state, _exit_inp(direction=Direction.SHORT),
            _Cfg(scalper_structure_exit="close"),
        ) == "none"

    def test_stale_event_before_entry_does_not_act(self):
        state = self._bear_choch_state()
        assert state.event_close_time is not None
        assert structure_exit_action(
            state, _exit_inp(entry_close_time=state.event_close_time),
            _Cfg(scalper_structure_exit="close"),
        ) == "none"
        assert structure_exit_action(
            state, _exit_inp(entry_close_time=state.event_close_time + 1),
            _Cfg(scalper_structure_exit="close"),
        ) == "none"

    def test_be_mode_moves_stop_when_improving_and_valid(self):
        assert structure_exit_action(
            self._bear_choch_state(),
            _exit_inp(current_price=100.0, current_stop=95.0, breakeven_price=99.0),
            _Cfg(scalper_structure_exit="be"),
        ) == "be"

    def test_be_mode_refuses_when_not_improving(self):
        assert structure_exit_action(
            self._bear_choch_state(),
            _exit_inp(current_price=100.0, current_stop=99.5, breakeven_price=99.0),
            _Cfg(scalper_structure_exit="be"),
        ) == "none"

    def test_be_mode_refuses_when_stop_would_be_on_wrong_side_of_price(self):
        """LONG'da piyasanın ÜSTÜNE stop = borsada -2021 (anında tetiklenir),
        harness'ta ise sahte 'SL kârı'. İki tarafta da reddedilmeli."""
        assert structure_exit_action(
            self._bear_choch_state(),
            _exit_inp(current_price=98.0, current_stop=95.0, breakeven_price=99.0),
            _Cfg(scalper_structure_exit="be"),
        ) == "none"

    def test_be_mode_short_side(self):
        """SHORT'ta BE stopu piyasanın ÜSTÜNDE olmalı ve AŞAĞI inerek iyileşir."""
        state = detect_structure(_series(_REF_ROWS[:6]), 1, 1)  # BULL CHoCH? -> BOS
        # BULL yönlü CHoCH üretmek için ters seri: önce aşağı BOS sonra yukarı CHoCH
        rows = [
            (10.0, 9.0, 9.5),
            (9.5, 7.0, 8.0),    # pivot low 7
            (10.0, 8.0, 9.0),
            (12.0, 9.0, 11.0),  # pivot high 12
            (11.0, 9.5, 10.0),
            (11.0, 6.5, 6.8),   # close 6.8 < 7 -> BOS (BEAR)
            (13.0, 10.0, 12.5),  # close 12.5 > 12 -> CHoCH (BULL)
        ]
        state = detect_structure(_series(rows), 1, 1)
        assert state.direction == StructureDirection.BULL and state.last_event == CHOCH
        assert structure_exit_action(
            state,
            _exit_inp(direction=Direction.SHORT, current_price=100.0,
                      current_stop=105.0, breakeven_price=101.0),
            _Cfg(scalper_structure_exit="be"),
        ) == "be"
        # iyileşme yok (BE mevcut stopun ÜSTÜNDE)
        assert structure_exit_action(
            state,
            _exit_inp(direction=Direction.SHORT, current_price=100.0,
                      current_stop=100.5, breakeven_price=101.0),
            _Cfg(scalper_structure_exit="be"),
        ) == "none"

    def test_snapshot_is_json_friendly(self):
        snap = structure_snapshot(self._bear_choch_state())
        assert snap["direction"] == "BEAR"
        assert snap["last_event"] == "CHOCH"
        assert snap["age_bars"] == 0
        assert structure_snapshot(None) == {}


# ==========================================================================
# 5. Harness entegrasyonu ve PARİTE
# ==========================================================================

@dataclass
class _HarnessCfg(_Cfg):
    """simulate_symbol/manage_position için tam cfg."""

    scalper_risk_percentage: float = 2.0
    scalper_leverage: int = 20
    scalper_tp1_roi: float = 20.0
    scalper_tp1_fraction: float = 0.40
    scalper_tp2_roi: float = 50.0
    scalper_tp2_fraction: float = 0.30
    scalper_min_stop_pct: float = 0.1
    scalper_max_stop_pct: float = 5.0
    scalper_breakeven_buffer_pct: float = 0.05
    scalper_chandelier_atr_mult: float = 2.5
    scalper_chandelier_atr_period: int = 14
    scalper_entry_mode: str = "taker"
    scalper_taker_fee_pct: float = 0.05
    scalper_maker_fee_pct: float = 0.02
    scalper_maker_fill_timeout_candles: int = 3
    scalper_min_rr: float = 0.0
    scalper_regime_filter: bool = False
    scalper_max_positions: int = 3
    scalper_loss_cooldown_minutes: float = 0.0
    scalper_stop_mode: str = "structural"
    scalper_stop_atr_floor_mult: float = 0.0
    scalper_dynamic_leverage: bool = False


class _AlwaysLong(StrategyProtocol):
    name = "X"

    def evaluate(self, ctx: StrategyContext) -> Optional[ScalpSignal]:
        return ScalpSignal(
            strategy="X", symbol=ctx.symbol, direction=Direction.LONG,
            entry_price=ctx.current_price, stop_price=ctx.current_price * 0.99,
            reason="test", regime=ctx.regime, atr_5m=1.0,
        )


def _entry_series(n: int, start: int = 0, price: float = 100.0) -> List[Candle]:
    """Düz seyreden giriş (1m) serisi — SL/TP tetiklemez.

    `start`: ilk mumun indeksi (=> open_time). Bağlam serisinin TAMAMI giriş
    serisinden ÖNCE kapanmış olsun diye kaydırılabilir.
    """
    return [_c(i, price + 0.5, price - 0.5, price) for i in range(start, start + n)]


# Referans bağlam serisi 5 dk aralıklı 7 mumdur: son mumu (CHoCH) 2_099_999
# ms'de kapanır; 1m giriş serisi 36. mumdan itibaren başlarsa TÜM giriş
# mumlarında yapı görünürdür.
_CTX_AFTER_BAR = 36


class TestHarnessGateParity:
    def test_gate_blocks_counter_structure_entries_and_counts_them(self):
        cfg = _HarnessCfg(scalper_structure_gate=True, scalper_structure_tf="context")
        candles_5m = _entry_series(12, start=_CTX_AFTER_BAR)
        # Bağlam serisi: BEAR CHoCH ile biter (referans seri, 5 dk aralık).
        candles_15m = _series(_REF_ROWS, interval_ms=5 * _MIN)

        missed = {}
        trades = simulate_symbol(
            "TESTUSDT", candles_5m, candles_15m, [], [_AlwaysLong()], cfg,
            missed_counter=missed,
        )
        assert trades == []
        assert missed.get("structure_gate", 0) > 0

        missed_off = {}
        trades_off = simulate_symbol(
            "TESTUSDT", candles_5m, candles_15m, [], [_AlwaysLong()],
            _HarnessCfg(scalper_structure_gate=False), missed_counter=missed_off,
        )
        assert trades_off, "kapı kapalıyken işlem açılmalı (test anlamsızlaşmasın)"
        assert "structure_gate" not in missed_off

    def test_engine_and_harness_call_the_same_pure_function(self):
        """P1 paritesi: motorun `_evaluate_symbol` içinde kullandığı ikili
        (`structure_state_for` → `structure_gate_blocks`) ile harness'ın
        `simulate_symbol` içinde kullandığı ikili AYNI fonksiyonlardır ve
        AYNI ctx üzerinde AYNI kararı verir."""
        import src.strategies.scalper.engine as engine_module

        assert engine_module.structure_state_for is structure_state_for
        assert engine_module.structure_gate_blocks is structure_gate_blocks
        assert backtest_module.structure_state_for is structure_state_for
        assert backtest_module.structure_gate_blocks is structure_gate_blocks

        cfg = _HarnessCfg(scalper_structure_gate=True)
        candles_5m = _entry_series(12, start=_CTX_AFTER_BAR)
        candles_15m = _series(_REF_ROWS, interval_ms=5 * _MIN)
        ctx = build_context("TESTUSDT", candles_5m, candles_15m, [], 11, 20)
        state = structure_state_for(ctx, cfg)
        assert state.direction == StructureDirection.BEAR
        assert structure_gate_blocks(state, Direction.LONG, cfg) is True


class TestHarnessStructureExit:
    def _open_long(self, cfg, candles_5m):
        sig = ScalpSignal(
            strategy="X", symbol="TESTUSDT", direction=Direction.LONG,
            entry_price=candles_5m[0].close, stop_price=candles_5m[0].close * 0.99,
            reason="test", regime=Regime.RANGE, atr_5m=1.0,
        )
        pos = open_position(sig, candles_5m, 0, cfg, 10_000.0)
        assert pos is not None
        return pos

    def test_close_mode_exits_at_choch_bar_close(self):
        cfg = _HarnessCfg(scalper_structure_exit="close", scalper_structure_gate=False)
        candles_5m = _entry_series(40)
        candles_15m = _series(_REF_ROWS, interval_ms=5 * _MIN)
        feed = _StructureFeed(candles_15m, structure_window_bars(cfg), cfg)

        pos = self._open_long(cfg, candles_5m)
        # Giriş, CHoCH mumundan ÖNCE kapanmış olmalı (tazelik şartı anlamlı olsun)
        pos.signal_close_time = candles_15m[5].close_time
        trade = manage_position(pos, candles_5m, cfg, structure_feed=feed)

        assert trade.exit_reason == "CHOCH"
        assert trade.exit_time >= candles_15m[6].close_time

    def test_close_mode_ignores_choch_that_predates_entry(self):
        cfg = _HarnessCfg(scalper_structure_exit="close", scalper_structure_gate=False)
        candles_5m = _entry_series(40)
        candles_15m = _series(_REF_ROWS, interval_ms=5 * _MIN)
        feed = _StructureFeed(candles_15m, structure_window_bars(cfg), cfg)

        pos = self._open_long(cfg, candles_5m)
        pos.signal_close_time = candles_15m[6].close_time  # olayla aynı an
        trade = manage_position(pos, candles_5m, cfg, structure_feed=feed)
        assert trade.exit_reason == "EOD"

    def test_be_mode_moves_stop_and_relabels_stop_hit(self):
        cfg = _HarnessCfg(scalper_structure_exit="be", scalper_structure_gate=False)
        # Giriş 100 seviyesinde; sonra fiyat 100.3'e çıkar (BE üstü, TP1 altı);
        # ALTI), CHoCH görüldüğünde stop BE'ye çekilir, son mumda düşüş BE
        # stopunu vurur -> STRUCT_BE etiketi (SL DEĞİL).
        candles_5m = (
            _entry_series(2)
            + _entry_series(34, start=2, price=100.3)
            + [_c(36, 100.7, 99.0, 99.5)]
        )
        candles_15m = _series(_REF_ROWS, interval_ms=5 * _MIN)
        feed = _StructureFeed(candles_15m, structure_window_bars(cfg), cfg)

        pos = self._open_long(cfg, candles_5m)
        pos.signal_close_time = candles_15m[5].close_time
        trade = manage_position(pos, candles_5m, cfg, structure_feed=feed)

        assert trade.exit_reason == "STRUCT_BE"
        assert trade.exit_price == pytest.approx(pos.breakeven_price)

    def test_feed_memoization_matches_direct_computation(self):
        cfg = _HarnessCfg(scalper_structure_exit="close")
        candles_15m = _random_walk(200, seed=17)
        feed = _StructureFeed(candles_15m, structure_window_bars(cfg), cfg)
        window = structure_window_bars(cfg)
        for idx in (10, 50, 120, 199):
            cutoff = candles_15m[idx].close_time
            cached = feed.state_at(cutoff)
            direct = detect_structure(
                candles_15m[max(0, idx + 1 - window): idx + 1],
                pivot_left=cfg.scalper_structure_pivot,
                pivot_right=cfg.scalper_structure_pivot,
                use_close=cfg.scalper_structure_use_close,
            )
            assert cached == direct

    def test_signal_close_time_is_recorded_from_signal_bar(self):
        """Tazelik şartının referansı SİNYAL mumudur (canlıdaki
        `entry_candle_time` ile aynı anlam), dolum mumu değil."""
        cfg = _HarnessCfg()
        candles_5m = _entry_series(10)
        pos = self._open_long(cfg, candles_5m)
        assert pos.entry_idx == 1                      # dolum SONRAKİ mumda
        assert pos.signal_close_time == candles_5m[0].close_time

    def test_no_feed_means_no_behaviour_change(self):
        cfg = _HarnessCfg(scalper_structure_exit="off")
        candles_5m = _entry_series(20)
        pos_a = self._open_long(cfg, candles_5m)
        pos_b = self._open_long(cfg, candles_5m)
        a = manage_position(pos_a, candles_5m, cfg)
        b = manage_position(pos_b, candles_5m, cfg, structure_feed=None)
        assert a.exit_reason == b.exit_reason == "EOD"
        assert a.pnl == b.pnl


# ==========================================================================
# 6. Canlı motor: yapı-tabanlı çıkış
# ==========================================================================

def _make_exit_engine(cfg, positions):
    """`_apply_structure_exits` için minimal ScalperEngine test çifti."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from src.strategies.scalper.engine import ScalperEngine

    engine = object.__new__(ScalperEngine)
    engine.cfg = cfg
    engine.logger = MagicMock()
    engine.exits = SimpleNamespace(
        tracked_symbols=MagicMock(side_effect=lambda: set(positions.keys())),
        _positions=dict(positions),
        force_stop_to=AsyncMock(return_value=True),
    )
    engine.fetcher = SimpleNamespace(
        get_klines=AsyncMock(return_value=_series(_REF_ROWS, interval_ms=5 * _MIN))
    )
    engine._close_position_market = AsyncMock(return_value=True)
    return engine


def _mk_scalp_position(symbol="TESTUSDT", direction=Direction.LONG,
                       entry_close_time=0, current_price=100.0,
                       current_stop=95.0, breakeven_price=99.0):
    from types import SimpleNamespace

    return SimpleNamespace(
        signal=SimpleNamespace(direction=direction),
        position=SimpleNamespace(
            symbol=symbol, entry_price=100.0, current_price=current_price,
            current_stoploss=current_stop, quantity=1.0,
        ),
        plan=SimpleNamespace(breakeven_price=breakeven_price),
        entry_candle_time=entry_close_time,
    )


@dataclass
class _EngineCfg(_Cfg):
    scalper_structure_exit: str = "close"


class TestEngineStructureExit:
    @pytest.mark.asyncio
    async def test_off_mode_does_not_even_fetch_candles(self):
        engine = _make_exit_engine(
            _EngineCfg(scalper_structure_exit="off"),
            {"TESTUSDT": _mk_scalp_position()},
        )
        await engine._apply_structure_exits()
        engine.fetcher.get_klines.assert_not_called()
        engine.exits.force_stop_to.assert_not_called()
        engine._close_position_market.assert_not_called()

    @pytest.mark.asyncio
    async def test_close_mode_uses_shared_reduce_only_path_with_own_label(self):
        engine = _make_exit_engine(
            _EngineCfg(scalper_structure_exit="close"),
            {"TESTUSDT": _mk_scalp_position()},
        )
        await engine._apply_structure_exits()
        engine._close_position_market.assert_awaited_once()
        assert engine._close_position_market.await_args.kwargs["forced_exit_reason"] == (
            "STRUCT_CHOCH"
        )

    @pytest.mark.asyncio
    async def test_be_mode_moves_stop_via_exit_manager(self):
        engine = _make_exit_engine(
            _EngineCfg(scalper_structure_exit="be"),
            {"TESTUSDT": _mk_scalp_position(
                current_price=100.0, current_stop=95.0, breakeven_price=99.0
            )},
        )
        await engine._apply_structure_exits()
        engine.exits.force_stop_to.assert_awaited_once()
        assert engine.exits.force_stop_to.await_args.args[2] == 99.0
        engine._close_position_market.assert_not_called()

    @pytest.mark.asyncio
    async def test_kline_request_matches_scan_loop_key_no_extra_rest_weight(self):
        """(sembol, aralık, limit) üçlüsü tarama turununkiyle AYNI olmalı —
        KlineFetcher TTL önbelleğine düşsün, yeni REST ağırlığı doğmasın."""
        engine = _make_exit_engine(
            _EngineCfg(scalper_structure_exit="close"),
            {"TESTUSDT": _mk_scalp_position()},
        )
        await engine._apply_structure_exits()
        assert engine.fetcher.get_klines.await_args.args == ("TESTUSDT", "5m", 100)

    @pytest.mark.asyncio
    async def test_at_most_one_action_per_tick(self):
        engine = _make_exit_engine(
            _EngineCfg(scalper_structure_exit="close"),
            {
                "AUSDT": _mk_scalp_position(symbol="AUSDT"),
                "BUSDT": _mk_scalp_position(symbol="BUSDT"),
            },
        )
        await engine._apply_structure_exits()
        assert engine._close_position_market.await_count == 1

    @pytest.mark.asyncio
    async def test_kline_failure_is_survivable(self):
        from unittest.mock import AsyncMock

        engine = _make_exit_engine(
            _EngineCfg(scalper_structure_exit="close"),
            {"TESTUSDT": _mk_scalp_position()},
        )
        engine.fetcher.get_klines = AsyncMock(side_effect=RuntimeError("ağ"))
        await engine._apply_structure_exits()   # istisna dışarı sızmamalı
        engine._close_position_market.assert_not_called()


# ==========================================================================
# 7. Ayar doğrulaması (startup fail-fast)
# ==========================================================================

class TestSettingsValidation:
    def test_unknown_structure_tf_is_rejected_when_enabled(self):
        from src.core.config import Settings

        with pytest.raises(ValueError):
            Settings(scalper_structure_gate=True, scalper_structure_tf="7m")

    def test_unknown_structure_tf_is_ignored_when_disabled(self):
        from src.core.config import Settings

        s = Settings(scalper_structure_gate=False, scalper_structure_tf="7m")
        assert s.scalper_structure_gate is False

    def test_invalid_exit_mode_is_rejected(self):
        from src.core.config import Settings

        with pytest.raises(ValueError):
            Settings(scalper_structure_exit="kapat")

    def test_valid_combination_passes(self):
        from src.core.config import Settings

        # Canlı sunucu profili: entry=1m, context=5m, regime=15m.
        s = Settings(
            scalper_structure_gate=True,
            scalper_structure_tf="15m",
            scalper_tf_entry="1m",
            scalper_tf_context="5m",
            scalper_tf_regime="15m",
            scalper_structure_pivot=3,
            scalper_structure_exit="be",
        )
        assert resolve_structure_role(s) == "regime"
        assert structure_enabled(s) is True

    def test_role_precedence_when_two_roles_share_a_timeframe(self):
        """Belgelenmiş öncelik: context → regime → entry (kod varsayılanında
        context ve regime'in ikisi de 15m olabilir)."""
        from src.core.config import Settings

        s = Settings(
            scalper_structure_gate=True,
            scalper_structure_tf="15m",
            scalper_tf_context="15m",
            scalper_tf_regime="15m",
        )
        assert resolve_structure_role(s) == "context"
