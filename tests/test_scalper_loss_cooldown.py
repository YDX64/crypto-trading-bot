"""ATR stop tabanı + kayıp sonrası sembol cooldown'u (2026-08-11 BEAT bulgusu).

Kök olay: BEATUSDT çöküşünde (2.58 → 0.78) C stratejisi 7 dakikada 4 kez
LONG açtı; yapısal stop (son 40 mumun dibi) fiyat zaten dip yaparken girişin
kılpayı altında kaldığından dördü de saniyeler içinde SL yedi (toplam -31 USDT).

İki koruma test edilir:
  1. setups.apply_stop_atr_floor — yapısal stop girişe ATR×mult'tan yakınsa
     ATR tabanına genişletilir (yalnız genişletme; USD riski boyutlamayla sabit).
  2. Kayıp sonrası sembol cooldown'u — canlı yol (ExitManager →
     ScalpExecutor.start_loss_cooldown) ve backtest paritesi (simulate_symbol).
"""

from types import SimpleNamespace
from typing import List, Optional

import pytest

from src.strategies.scalper.backtest import simulate_symbol
from src.strategies.scalper.executor import ScalpExecutor
from src.strategies.scalper.exits import ExitManager
from src.strategies.scalper.setups import apply_stop_atr_floor
from src.strategies.scalper.types import (
    Candle,
    Direction,
    Regime,
    ScalpSignal,
    StrategyContext,
    StrategyProtocol,
)

_INTERVAL_5M = 5 * 60 * 1000


def _mk_candle(i: int, open_: float, high: float, low: float, close: float) -> Candle:
    open_time = i * _INTERVAL_5M
    return Candle(
        open_time=open_time, open=open_, high=high, low=low, close=close,
        volume=100.0, close_time=open_time + _INTERVAL_5M - 1,
    )


def _mk_signal(
    entry_price: float,
    stop_price: float,
    direction: Direction = Direction.LONG,
    atr_5m: float = 1.0,
) -> ScalpSignal:
    return ScalpSignal(
        strategy="C", symbol="TESTUSDT", direction=direction,
        entry_price=entry_price, stop_price=stop_price, reason="test",
        regime=Regime.RANGE, atr_5m=atr_5m,
    )


# --------------------------------------------------------------------------
# 1) apply_stop_atr_floor
# --------------------------------------------------------------------------

class TestApplyStopAtrFloor:
    def test_long_tight_structural_stop_widened_to_atr_floor(self):
        # Yapısal stop girişe %0.2 mesafede; ATR=2.0, mult=1.0 -> taban 98.0.
        sig = _mk_signal(entry_price=100.0, stop_price=99.8, atr_5m=2.0)
        out = apply_stop_atr_floor(sig, 1.0)
        assert out.stop_price == pytest.approx(98.0)
        # Diğer alanlar korunur (frozen dataclass replace).
        assert out.symbol == sig.symbol and out.strategy == sig.strategy

    def test_short_tight_structural_stop_widened_to_atr_floor(self):
        sig = _mk_signal(
            entry_price=100.0, stop_price=100.2,
            direction=Direction.SHORT, atr_5m=2.0,
        )
        out = apply_stop_atr_floor(sig, 1.0)
        assert out.stop_price == pytest.approx(102.0)

    def test_structural_stop_already_wider_untouched(self):
        sig = _mk_signal(entry_price=100.0, stop_price=95.0, atr_5m=2.0)
        assert apply_stop_atr_floor(sig, 1.0) is sig

        short = _mk_signal(
            entry_price=100.0, stop_price=105.0,
            direction=Direction.SHORT, atr_5m=2.0,
        )
        assert apply_stop_atr_floor(short, 1.0) is short

    def test_disabled_when_mult_or_atr_nonpositive(self):
        sig = _mk_signal(entry_price=100.0, stop_price=99.9, atr_5m=2.0)
        assert apply_stop_atr_floor(sig, 0.0) is sig
        assert apply_stop_atr_floor(sig, -1.0) is sig

        no_atr = _mk_signal(entry_price=100.0, stop_price=99.9, atr_5m=0.0)
        assert apply_stop_atr_floor(no_atr, 1.0) is no_atr

    def test_long_floor_below_zero_price_untouched(self):
        # Aşırı ATR taban fiyatı sıfırın altına iterse dokunma (bozuk stop üretme).
        sig = _mk_signal(entry_price=1.0, stop_price=0.998, atr_5m=5.0)
        assert apply_stop_atr_floor(sig, 1.0) is sig

    def test_multiplier_scales_distance(self):
        sig = _mk_signal(entry_price=100.0, stop_price=99.9, atr_5m=2.0)
        assert apply_stop_atr_floor(sig, 0.5).stop_price == pytest.approx(99.0)
        assert apply_stop_atr_floor(sig, 1.5).stop_price == pytest.approx(97.0)


# --------------------------------------------------------------------------
# 2) ScalpExecutor.start_loss_cooldown
# --------------------------------------------------------------------------

def _executor(**cfg_overrides) -> ScalpExecutor:
    values = dict(
        scalper_loss_cooldown_minutes=60,
        scalper_protection_failure_cooldown_minutes=60,
    )
    values.update(cfg_overrides)
    cfg = SimpleNamespace(**values)
    return ScalpExecutor(
        SimpleNamespace(), SimpleNamespace(), SimpleNamespace(), cfg
    )


class TestStartLossCooldown:
    def test_blocks_entry_and_reports_reason(self):
        ex = _executor()
        assert not ex.is_entry_blocked("BEATUSDT")
        ex.start_loss_cooldown("beatusdt")  # normalize edilmeli
        assert ex.is_entry_blocked("BEATUSDT")
        snapshot = ex.cooldown_snapshot()
        assert len(snapshot) == 1
        assert snapshot[0]["symbol"] == "BEATUSDT"
        assert snapshot[0]["reason"] == "loss_exit"
        assert 0 < snapshot[0]["remaining_seconds"] <= 60 * 60

    def test_disabled_when_config_zero(self):
        ex = _executor(scalper_loss_cooldown_minutes=0)
        ex.start_loss_cooldown("BEATUSDT")
        assert not ex.is_entry_blocked("BEATUSDT")

    def test_does_not_shorten_longer_existing_cooldown(self):
        import time as _time

        ex = _executor(scalper_loss_cooldown_minutes=30)
        far_future = _time.time() + 4 * 60 * 60
        ex._cooldowns["BEATUSDT"] = {
            "reason": "initial_sl_failed_emergency_close",
            "expires_at": far_future,
        }
        ex.start_loss_cooldown("BEATUSDT")
        state = ex._cooldowns["BEATUSDT"]
        assert state["reason"] == "initial_sl_failed_emergency_close"
        assert state["expires_at"] == far_future

    def test_extends_shorter_existing_cooldown(self):
        import time as _time

        ex = _executor(scalper_loss_cooldown_minutes=60)
        ex._cooldowns["BEATUSDT"] = {
            "reason": "loss_exit",
            "expires_at": _time.time() + 60,  # 1 dakika kalmış
        }
        ex.start_loss_cooldown("BEATUSDT")
        assert ex._cooldowns["BEATUSDT"]["expires_at"] > _time.time() + 55 * 60

    def test_protection_failure_does_not_shorten_longer_loss_cooldown(self):
        # Simetri: _set_cooldown "uzun olan kazanır" kuralını İKİ yönde uygular.
        import time as _time

        ex = _executor(scalper_protection_failure_cooldown_minutes=60)
        far_future = _time.time() + 8 * 60 * 60
        ex._cooldowns["BEATUSDT"] = {
            "reason": "loss_exit",
            "expires_at": far_future,
        }
        ex._start_protection_failure_cooldown("BEATUSDT")
        state = ex._cooldowns["BEATUSDT"]
        assert state["reason"] == "loss_exit"
        assert state["expires_at"] == far_future


class TestCooldownPersistence:
    """Cooldown'lar restart'a dayanmalı (state/scalper_cooldowns.json)."""

    def test_cooldown_survives_restart(self, tmp_path):
        path = str(tmp_path / "cooldowns.json")
        ex1 = _executor(scalper_cooldown_state_path=path)
        ex1.start_loss_cooldown("BEATUSDT")
        assert ex1.is_entry_blocked("BEATUSDT")

        # "Restart": aynı cfg ile yeni executor — diskten yüklemeli.
        ex2 = _executor(scalper_cooldown_state_path=path)
        assert ex2.is_entry_blocked("BEATUSDT")
        snapshot = ex2.cooldown_snapshot()
        assert snapshot[0]["reason"] == "loss_exit"

    def test_expired_entries_dropped_on_load(self, tmp_path):
        import json as _json
        import time as _time

        path = tmp_path / "cooldowns.json"
        path.write_text(_json.dumps({
            "version": 1,
            "entries": {
                "OLDUSDT": {"reason": "loss_exit", "expires_at": _time.time() - 10},
                "NEWUSDT": {"reason": "loss_exit", "expires_at": _time.time() + 600},
            },
        }))
        ex = _executor(scalper_cooldown_state_path=str(path))
        assert not ex.is_entry_blocked("OLDUSDT")
        assert ex.is_entry_blocked("NEWUSDT")

    def test_corrupt_state_file_loads_empty_without_crash(self, tmp_path):
        path = tmp_path / "cooldowns.json"
        path.write_text("{bozuk json!!!")
        ex = _executor(scalper_cooldown_state_path=str(path))
        assert not ex.is_entry_blocked("BEATUSDT")
        # Bozuk dosyaya rağmen yeni cooldown yazılabilmeli.
        ex.start_loss_cooldown("BEATUSDT")
        assert ex.is_entry_blocked("BEATUSDT")

    def test_no_path_configured_disables_persistence(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)  # yanlışlıkla cwd'ye yazarsa görünür olsun
        ex = _executor()  # cfg'de scalper_cooldown_state_path YOK
        ex.start_loss_cooldown("BEATUSDT")
        assert ex.is_entry_blocked("BEATUSDT")
        assert list(tmp_path.iterdir()) == []


# --------------------------------------------------------------------------
# 3) ExitManager kayıp kapanışında callback tetiklemeli
# --------------------------------------------------------------------------

def _exit_manager(cb) -> ExitManager:
    async def _noop_klines(symbol, interval, limit):
        return []

    return ExitManager(
        client=SimpleNamespace(),
        pm=SimpleNamespace(),
        tracker=SimpleNamespace(),
        cfg=SimpleNamespace(),
        kline_fetch=_noop_klines,
        loss_cooldown_cb=cb,
    )


class TestExitManagerLossCooldownTrigger:
    def test_sl_close_triggers(self):
        calls: List[str] = []
        mgr = _exit_manager(calls.append)
        mgr._maybe_start_loss_cooldown("BEATUSDT", "SL", -10.86)
        assert calls == ["BEATUSDT"]

    def test_sl_close_triggers_even_with_positive_recorded_pnl(self):
        calls: List[str] = []
        mgr = _exit_manager(calls.append)
        mgr._maybe_start_loss_cooldown("BEATUSDT", "SL", 0.01)
        assert calls == ["BEATUSDT"]

    def test_negative_non_sl_close_triggers(self):
        calls: List[str] = []
        mgr = _exit_manager(calls.append)
        mgr._maybe_start_loss_cooldown("BEATUSDT", "TRAIL", -5.0)
        assert calls == ["BEATUSDT"]

    def test_profitable_non_sl_close_does_not_trigger(self):
        calls: List[str] = []
        mgr = _exit_manager(calls.append)
        mgr._maybe_start_loss_cooldown("BEATUSDT", "TP_LADDER", 75.06)
        assert calls == []

    def test_missing_callback_is_noop(self):
        mgr = _exit_manager(None)
        mgr._maybe_start_loss_cooldown("BEATUSDT", "SL", -1.0)  # patlamamalı

    def test_callback_exception_never_breaks_close_path(self):
        def _boom(symbol: str) -> None:
            raise RuntimeError("cooldown patladı")

        mgr = _exit_manager(_boom)
        mgr._maybe_start_loss_cooldown("BEATUSDT", "SL", -1.0)  # yutulmalı

    def test_gross_small_profit_below_fee_threshold_triggers(self):
        # estimated_gross yolunda brüt +0.5 ama tahmini komisyon 1.0 -> net eksi
        # sayılır ve cooldown başlar (backtest NET pnl<0 kuralıyla parite).
        calls: List[str] = []
        mgr = _exit_manager(calls.append)
        mgr._maybe_start_loss_cooldown("BEATUSDT", "TRAIL", 0.5, loss_threshold=1.0)
        assert calls == ["BEATUSDT"]

    def test_verified_net_small_profit_does_not_trigger(self):
        calls: List[str] = []
        mgr = _exit_manager(calls.append)
        mgr._maybe_start_loss_cooldown("BEATUSDT", "TRAIL", 0.5, loss_threshold=0.0)
        assert calls == []

    def test_estimated_roundtrip_fee_uses_conservative_rate(self):
        mgr = _exit_manager(None)
        mgr.cfg = SimpleNamespace(scalper_taker_fee_pct=0.05, scalper_maker_fee_pct=0.02)
        # (100 + 102) * 3 * 0.0005 = 0.303
        assert mgr._estimated_roundtrip_fee(100.0, 102.0, 3.0) == pytest.approx(0.303)

    async def test_recovery_estimate_close_triggers_cooldown(self):
        """Restart'ta belirsiz kapanan işlem (UNKNOWN, estimated_gross) brüt 0
        PnL ile kapanır — komisyon eşiği sayesinde cooldown yine başlamalı."""
        from unittest.mock import AsyncMock

        calls: List[str] = []
        mgr = _exit_manager(calls.append)
        mgr.cfg = SimpleNamespace(scalper_taker_fee_pct=0.05, scalper_maker_fee_pct=0.02)
        mgr.client = SimpleNamespace(get_current_price=AsyncMock(return_value=100.0))
        mgr.tracker = SimpleNamespace(record_close=AsyncMock())
        trade = SimpleNamespace(
            id=7, symbol="BEATUSDT", direction="LONG",
            entry_price=100.0, quantity=1.0,
        )
        ok = await mgr._record_recovery_estimate(trade, "test-notes")
        assert ok is True
        assert calls == ["BEATUSDT"]


# --------------------------------------------------------------------------
# 4) Backtest paritesi — simulate_symbol cooldown'u
# --------------------------------------------------------------------------

class _AlwaysLongStrategy(StrategyProtocol):
    name = "X"

    def evaluate(self, ctx: StrategyContext) -> Optional[ScalpSignal]:
        return ScalpSignal(
            strategy="X", symbol=ctx.symbol, direction=Direction.LONG,
            entry_price=ctx.current_price, stop_price=ctx.current_price * 0.99,
            reason="test", regime=Regime.UP, atr_5m=1.0,
        )


def _sim_cfg(**overrides) -> SimpleNamespace:
    values = dict(
        scalper_risk_percentage=2.0,
        scalper_leverage=20,
        scalper_tp1_roi=20.0,
        scalper_tp1_fraction=0.40,
        scalper_tp2_roi=50.0,
        scalper_tp2_fraction=0.30,
        scalper_min_stop_pct=0.15,
        scalper_max_stop_pct=3.0,
        scalper_breakeven_buffer_pct=0.05,
        scalper_chandelier_atr_mult=2.5,
        scalper_chandelier_atr_period=14,
        scalper_min_rr=0.0,
        scalper_entry_mode="taker",
        scalper_taker_fee_pct=0.05,
        scalper_maker_fee_pct=0.02,
        scalper_maker_fill_timeout_candles=3,
        scalper_max_margin_pct=50.0,
        scalper_stop_atr_floor_mult=0.0,
        scalper_loss_cooldown_minutes=0,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _sl_grinder_candles(n: int) -> List[Candle]:
    """Çift indeks: sinyal mumu; tek indeks: giriş + anında SL çakması."""
    candles: List[Candle] = []
    for i in range(n):
        if i % 2 == 0:
            candles.append(_mk_candle(i, 100.0, 100.1, 99.9, 100.0))
        else:
            candles.append(_mk_candle(i, 100.0, 100.5, 90.0, 95.0))
    return candles


class TestSimulateSymbolLossCooldown:
    def test_cooldown_disabled_reenters_immediately(self):
        trades = simulate_symbol(
            "TESTUSDT", _sl_grinder_candles(10), [], [],
            [_AlwaysLongStrategy()], _sim_cfg(),
        )
        assert [t.exit_idx for t in trades] == [1, 3, 5, 7, 9]

    def test_cooldown_blocks_reentry_after_sl(self):
        # 60 dk cooldown = 12 adet 5m mum. İlk SL exit_idx=1'de kapanır;
        # sonraki giriş ancak cooldown bittikten sonraki sinyal mumunda olur.
        candles = _sl_grinder_candles(40)
        trades = simulate_symbol(
            "TESTUSDT", candles, [], [],
            [_AlwaysLongStrategy()],
            _sim_cfg(scalper_loss_cooldown_minutes=60),
        )
        assert len(trades) >= 2
        cooldown_ms = 60 * 60 * 1000
        for prev, curr in zip(trades, trades[1:]):
            assert curr.entry_time >= prev.exit_time + cooldown_ms

    def test_cooldown_not_started_after_profitable_close(self):
        # Kârlı kapanış (TP_LADDER) cooldown başlatmamalı: kazançtan hemen
        # sonra gelen sinyal mumunda yeniden giriş serbest.
        candles: List[Candle] = []
        # 0: sinyal, 1: giriş + TP'lere koşu (SL'siz), 2: sinyal, 3: tekrar koşu
        for i in range(8):
            if i % 2 == 0:
                candles.append(_mk_candle(i, 100.0, 100.1, 99.9, 100.0))
            else:
                candles.append(_mk_candle(i, 100.0, 104.0, 99.95, 103.9))
        trades = simulate_symbol(
            "TESTUSDT", candles, [], [],
            [_AlwaysLongStrategy()],
            _sim_cfg(scalper_loss_cooldown_minutes=60),
        )
        assert len(trades) >= 2
        for t in trades:
            assert t.pnl > 0.0

    def test_atr_floor_widens_stop_in_simulation(self):
        # AlwaysLong yapısal stop %1 (99.0); ATR=1.0, mult=2.0 -> taban 98.0.
        cfg = _sim_cfg(scalper_stop_atr_floor_mult=2.0, scalper_max_stop_pct=5.0)
        candles = [
            _mk_candle(0, 100.0, 100.1, 99.9, 100.0),
            _mk_candle(1, 100.0, 100.2, 98.5, 98.6),  # 99.0'ı deler, 98.0'ı delmez
            _mk_candle(2, 98.6, 98.7, 98.5, 98.6),
        ]
        base = simulate_symbol(
            "TESTUSDT", candles, [], [], [_AlwaysLongStrategy()], _sim_cfg(),
        )
        floored = simulate_symbol(
            "TESTUSDT", candles, [], [], [_AlwaysLongStrategy()], cfg,
        )
        # Tabansız: 99.0 stopu 1. mumda çakar (SL). Tabanlı: stop 98.0'da,
        # 1. mumun dibi 98.5 -> SL YOK.
        assert any(t.exit_reason == "SL" for t in base)
        assert not any(t.exit_reason == "SL" for t in floored)
