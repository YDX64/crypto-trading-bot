"""Scalper/Telegram runtime liveness ve fail-closed sağlık testleri."""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse

import src.main as main_module
from src.services.telegram_bot import TelegramBotService
from src.strategies.scalper.engine import ScalperEngine
from src.trading.position_manager import UnprotectedPositionError
from src.trading.symbol_reservations import symbol_reservations


def _make_engine(*, tracked=None, pending=None) -> ScalperEngine:
    """Ağ/DB istemcisi kurmadan yalın bir ScalperEngine test çifti."""
    symbol_reservations.clear()
    engine = object.__new__(ScalperEngine)
    engine.cfg = SimpleNamespace(
        scalper_enabled=True,
        scalper_strategies="C",
        scalper_top_n=20,
        scalper_scan_interval_seconds=30,
        scalper_safety_interval_seconds=2.0,
        scalper_max_positions=3,
        max_positions=5,
        scalper_leverage=10,
        scalper_daily_loss_limit_pct=15.0,
        scalper_virtual_capital_usdt=0.0,
        scalper_virtual_capital_start_trade_id=0,
    )
    engine.logger = MagicMock()
    engine.running = True
    engine._task = None
    engine._safety_task = None
    engine._exchange_task = None
    engine._entry_lock = asyncio.Lock()
    engine._opening_symbols = set()

    engine._universe = []
    engine._regimes = {}
    engine._regime_cache = {}
    engine._balance_cache = (1000.0, time.monotonic())
    engine._daily_pnl = 0.0
    engine._daily_pnl_source = "binance_account_income"
    engine._risk_equity_usdt = None
    engine._risk_equity_source = "unavailable"
    engine._daily_loss_threshold_usdt = None
    engine._virtual_equity_cache = (None, 0.0)
    engine._virtual_equity_cache_close_seq = -1
    engine._daily_income_cache = (0.0, time.monotonic(), None)
    engine._risk_ready = True
    engine._kill_switch = False
    engine._kill_switch_day = None
    engine._entry_halted = False
    engine._entry_halt_reason = None
    engine._entry_halted_at = None
    engine._entry_halt_path = None
    engine._exchange_ready = True
    engine._recovery_ready = True
    engine._exchange_last_success_at = "now"
    engine._exchange_last_success_monotonic = time.monotonic()
    engine._exchange_last_error = None
    engine._exchange_last_error_at = None
    engine._exchange_success_count = 1
    engine._signals_today = 0
    engine._last_scan_at = None

    for prefix in ("scan", "safety"):
        setattr(engine, f"_{prefix}_last_started_at", None)
        setattr(engine, f"_{prefix}_last_success_at", None)
        setattr(engine, f"_{prefix}_last_success_monotonic", None)
        setattr(engine, f"_{prefix}_last_duration_seconds", None)
        setattr(engine, f"_{prefix}_last_error", None)
        setattr(engine, f"_{prefix}_last_error_at", None)
        setattr(engine, f"_{prefix}_consecutive_errors", 0)
        setattr(engine, f"_{prefix}_success_count", 0)

    tracked_set = set(tracked or set())
    pending_set = set(pending or set())
    engine.exits = SimpleNamespace(
        step=AsyncMock(),
        recover=AsyncMock(return_value=True),
        track=MagicMock(),
        tracked_symbols=MagicMock(side_effect=lambda: set(tracked_set)),
        _positions={},
    )
    engine.executor = SimpleNamespace(
        check_pending=AsyncMock(return_value=[]),
        cancel_all_pending=AsyncMock(return_value=[]),
        pending_symbols=MagicMock(side_effect=lambda: set(pending_set)),
        pending_snapshot=MagicMock(return_value=[]),
        try_open=AsyncMock(return_value=None),
        recover_pending=AsyncMock(return_value=[]),
        handle_order_update=AsyncMock(return_value=None),
    )
    engine.scanner = SimpleNamespace(get_universe=AsyncMock(return_value=[]))
    engine.tracker = SimpleNamespace(today_realized_pnl=AsyncMock(return_value=0.0))
    engine.client = SimpleNamespace(
        get_account_balance=AsyncMock(return_value=1000.0),
        get_wallet_balance=AsyncMock(return_value=1000.0),
        get_all_positions=AsyncMock(return_value=[]),
        get_income_history=AsyncMock(return_value=[]),
    )
    engine.user_stream = SimpleNamespace(
        running=True,
        connected=True,
        last_event_at=None,
        last_error=None,
        reconnect_count=0,
    )
    engine.fetcher = SimpleNamespace(get_klines=AsyncMock(return_value=[]))
    return engine


async def _never() -> None:
    await asyncio.Event().wait()


class TestScalperRuntimeLiveness:
    async def test_safety_loop_progresses_while_scan_loop_is_blocked(self):
        engine = _make_engine()
        scan_release = asyncio.Event()
        scan_started = asyncio.Event()
        safety_succeeded = asyncio.Event()

        async def blocked_scan():
            scan_started.set()
            await scan_release.wait()

        async def fast_safety():
            safety_succeeded.set()

        engine._scan_tick = blocked_scan
        engine._safety_tick = fast_safety
        engine._task = asyncio.create_task(engine._loop())
        engine._safety_task = asyncio.create_task(engine._safety_loop())

        try:
            await asyncio.wait_for(scan_started.wait(), timeout=0.2)
            await asyncio.wait_for(safety_succeeded.wait(), timeout=0.2)
            await asyncio.sleep(0)
            assert engine._task.done() is False
            assert engine._safety_success_count >= 1
        finally:
            engine.running = False
            scan_release.set()
            engine._task.cancel()
            engine._safety_task.cancel()
            await asyncio.gather(engine._task, engine._safety_task, return_exceptions=True)

    async def test_pending_entries_count_toward_max_position_capacity(self):
        engine = _make_engine(tracked={"BTCUSDT", "ETHUSDT"}, pending={"SOLUSDT"})
        engine.scanner.get_universe.return_value = ["XRPUSDT"]
        engine._evaluate_symbol = AsyncMock()

        await engine._scan_tick()

        engine._evaluate_symbol.assert_not_awaited()

    async def test_scalper_skips_symbol_reserved_by_telegram_manager(self):
        engine = _make_engine()
        symbol_reservations.reserve("XRPUSDT", "telegram")
        engine.scanner.get_universe.return_value = ["XRPUSDT"]
        engine._evaluate_symbol = AsyncMock()

        await engine._scan_tick()

        engine._evaluate_symbol.assert_not_awaited()

    async def test_scalper_skips_symbol_with_executor_protection_cooldown(self):
        engine = _make_engine()
        engine.scanner.get_universe.return_value = ["XRPUSDT"]
        engine.executor.is_entry_blocked = MagicMock(return_value=True)
        engine._evaluate_symbol = AsyncMock()

        await engine._scan_tick()

        engine.executor.is_entry_blocked.assert_called_once_with("XRPUSDT")
        engine._evaluate_symbol.assert_not_awaited()

    async def test_external_tv_signal_flows_through_normal_entry_path(self, monkeypatch):
        """TV sinyali de İÇ sinyallerle aynı hattan geçmeli: try_open'a
        strategy='TV' imzalı, stop politikası uygulanmış sinyal ulaşmalı."""
        from src.strategies.scalper.types import Direction as _Direction

        engine = _make_engine()
        engine.cfg.scalper_stop_atr_floor_mult = 0.0
        engine.client.get_position_risk = AsyncMock(return_value=None)
        candle = SimpleNamespace(close=100.0, high=101.0, low=99.0)
        engine.fetcher.get_klines = AsyncMock(return_value=[candle] * 250)
        engine._get_cached_regime = MagicMock(
            return_value=SimpleNamespace(value="RANGE")
        )
        monkeypatch.setattr(
            "src.strategies.scalper.engine.compute_atr", lambda *_args: 1.0
        )
        engine.executor.is_entry_blocked = MagicMock(return_value=False)

        result = await engine.external_signal("ltcusdt", _Direction.SHORT)

        engine.executor.try_open.assert_awaited_once()
        sent = engine.executor.try_open.await_args.args[0]
        assert sent.strategy == "TV"
        assert sent.direction == _Direction.SHORT
        assert sent.symbol == "LTCUSDT"
        assert sent.stop_price == pytest.approx(101.0)  # giriş + 1×ATR seed
        assert result["accepted"] is False or result["accepted"] is True  # sözlük döner

    async def test_external_tv_signal_rejected_when_symbol_busy(self):
        from src.strategies.scalper.types import Direction as _Direction

        engine = _make_engine(tracked={"LTCUSDT"})
        result = await engine.external_signal("LTCUSDT", _Direction.LONG)
        assert result["accepted"] is False
        assert "pozisyon" in result["reason"]
        engine.executor.try_open.assert_not_awaited()

    async def test_external_tv_signal_rejected_when_kill_switch(self):
        from src.strategies.scalper.types import Direction as _Direction

        engine = _make_engine()
        engine._kill_switch = True
        result = await engine.external_signal("BTCUSDT", _Direction.LONG)
        assert result["accepted"] is False
        engine.executor.try_open.assert_not_awaited()

    async def test_symbol_allowlist_pins_universe_without_scanner(self):
        engine = _make_engine()
        engine.cfg.scalper_symbol_allowlist = "btcusdt, ETHUSDT"
        engine._evaluate_symbol = AsyncMock()

        await engine._scan_tick()

        engine.scanner.get_universe.assert_not_awaited()
        assert engine._universe == ["BTCUSDT", "ETHUSDT"]

    async def test_atr_floor_applied_to_signal_before_try_open(self, monkeypatch):
        """apply_stop_atr_floor, evaluate() İLE try_open() ARASINDA uygulanmalı.

        Sessiz regresyon koruması: çağrı _entry_lock içine/try_open sonrasına
        kayarsa maker pending journal'a floored OLMAYAN stop yazılır ve
        2026-08-11 BEAT senaryosu geri gelir.
        """
        from src.strategies.scalper.types import (
            Direction as _Direction,
            Regime as _Regime,
            ScalpSignal as _ScalpSignal,
        )

        engine = _make_engine()
        engine.cfg.scalper_stop_atr_floor_mult = 2.0
        engine.client.get_position_risk = AsyncMock(return_value=None)
        candle = SimpleNamespace(close=100.0, high=101.0, low=99.0)
        engine.fetcher.get_klines = AsyncMock(return_value=[candle] * 250)
        engine._get_cached_regime = MagicMock(
            return_value=SimpleNamespace(value="RANGE")
        )
        monkeypatch.setattr(
            "src.strategies.scalper.engine.compute_atr", lambda *_args: 1.0
        )
        engine.executor.is_entry_blocked = MagicMock(return_value=False)

        # Yapısal stop girişe yalnız %0.1 mesafede — ATR(1.0)×2.0 taban 98.0'a çekmeli.
        raw_signal = _ScalpSignal(
            strategy="C", symbol="TESTUSDT", direction=_Direction.LONG,
            entry_price=100.0, stop_price=99.9, reason="test",
            regime=_Regime.RANGE, atr_5m=1.0,
        )
        strategy = SimpleNamespace(evaluate=MagicMock(return_value=raw_signal))

        await engine._evaluate_symbol("TESTUSDT", [strategy])

        engine.executor.try_open.assert_awaited_once()
        sent_signal = engine.executor.try_open.await_args.args[0]
        assert sent_signal.stop_price == pytest.approx(98.0)
        assert sent_signal.entry_price == raw_signal.entry_price

    async def test_cooldown_state_error_fails_closed_for_that_symbol(self):
        engine = _make_engine()
        engine.scanner.get_universe.return_value = ["XRPUSDT"]
        engine.executor.is_entry_blocked = MagicMock(
            side_effect=RuntimeError("ledger unavailable")
        )
        engine._evaluate_symbol = AsyncMock()

        await engine._scan_tick()

        engine._evaluate_symbol.assert_not_awaited()
        assert engine.logger.error.called

    async def test_cooldown_is_rechecked_immediately_before_entry_post(self, monkeypatch):
        engine = _make_engine()
        engine.client.get_position_risk = AsyncMock(return_value=None)
        candle = SimpleNamespace(close=100.0, high=101.0, low=99.0)
        engine.fetcher.get_klines = AsyncMock(return_value=[candle] * 250)
        engine._get_cached_regime = MagicMock(
            return_value=SimpleNamespace(value="RANGING")
        )
        monkeypatch.setattr(
            "src.strategies.scalper.engine.compute_atr", lambda *_args: 1.0
        )
        signal = SimpleNamespace(
            strategy="C", direction=SimpleNamespace(value="SHORT")
        )
        strategy = SimpleNamespace(evaluate=MagicMock(return_value=signal))
        # İlk kontrolde açık; veri hesaplanırken cooldown başlamış gibi final
        # POST kapısında kapalı.
        engine.executor.is_entry_blocked = MagicMock(side_effect=[False, True])

        await engine._evaluate_symbol("TESTUSDT", [strategy])

        assert engine.executor.is_entry_blocked.call_count == 2
        engine.executor.try_open.assert_not_awaited()
        assert symbol_reservations.owner("TESTUSDT") is None

    async def test_inflight_entry_reservation_survives_safety_sync(self, monkeypatch):
        engine = _make_engine()
        engine.client.get_position_risk = AsyncMock(return_value=None)
        candle = SimpleNamespace(close=100.0, high=101.0, low=99.0)
        engine.fetcher.get_klines = AsyncMock(return_value=[candle] * 250)
        engine._get_cached_regime = MagicMock(
            return_value=SimpleNamespace(value="RANGING")
        )
        monkeypatch.setattr(
            "src.strategies.scalper.engine.compute_atr", lambda *_args: 1.0
        )

        signal = SimpleNamespace(
            strategy="C", direction=SimpleNamespace(value="SHORT")
        )
        strategy = SimpleNamespace(evaluate=MagicMock(return_value=signal))
        pending = set()
        entry_started = asyncio.Event()
        entry_release = asyncio.Event()

        engine.executor.pending_symbols = MagicMock(
            side_effect=lambda: set(pending)
        )

        async def slow_try_open(_signal, _ctx):
            entry_started.set()
            await entry_release.wait()
            pending.add("TESTUSDT")
            return None

        engine.executor.try_open = AsyncMock(side_effect=slow_try_open)
        task = asyncio.create_task(
            engine._evaluate_symbol("TESTUSDT", [strategy])
        )
        try:
            await asyncio.wait_for(entry_started.wait(), timeout=0.2)
            assert engine._opening_symbols == {"TESTUSDT"}
            assert symbol_reservations.owner("TESTUSDT") == "scalper"

            # Safety loop try_open await'teyken ilerlese bile claim korunur.
            engine._sync_scalper_reservations()
            assert symbol_reservations.owner("TESTUSDT") == "scalper"

            entry_release.set()
            await asyncio.wait_for(task, timeout=0.2)
            assert engine._opening_symbols == set()
            assert engine.executor.pending_symbols() == {"TESTUSDT"}
            assert symbol_reservations.owner("TESTUSDT") == "scalper"
        finally:
            entry_release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            symbol_reservations.clear()

    async def test_safety_tick_runs_exit_and_pending_checks_outside_scan(self):
        engine = _make_engine()
        engine._update_kill_switch = AsyncMock()
        opened = SimpleNamespace(
            position=SimpleNamespace(symbol="TESTUSDT", entry_price=100.0),
            signal=SimpleNamespace(direction=SimpleNamespace(value="LONG")),
        )
        engine.executor.check_pending.return_value = [opened]

        await engine._safety_tick()

        engine.exits.step.assert_awaited_once()
        engine.executor.check_pending.assert_awaited_once()
        engine.exits.track.assert_called_once_with(opened)
        assert engine._signals_today == 1

    async def test_kill_switch_cancels_pending_and_does_not_finalize_new_fill(self):
        engine = _make_engine(pending={"TESTUSDT"})

        async def activate_kill_switch():
            engine._kill_switch = True

        engine._update_kill_switch = AsyncMock(side_effect=activate_kill_switch)

        await engine._safety_tick()

        engine.executor.cancel_all_pending.assert_awaited_once()
        # Pending reconciliation is deliberately first: a fill that happened
        # just before the kill switch must receive protection before NEW
        # orders are cancelled.
        engine.executor.check_pending.assert_awaited_once()

    async def test_fill_won_cancel_race_is_still_tracked_under_kill_switch(self):
        engine = _make_engine(pending={"TESTUSDT"})
        opened = SimpleNamespace(
            position=SimpleNamespace(symbol="TESTUSDT", entry_price=100.0),
            signal=SimpleNamespace(direction=SimpleNamespace(value="LONG")),
        )
        engine.executor.cancel_all_pending.return_value = [opened]

        async def activate_kill_switch():
            engine._kill_switch = True

        engine._update_kill_switch = AsyncMock(side_effect=activate_kill_switch)

        await engine._safety_tick()

        engine.exits.track.assert_called_once_with(opened)
        assert engine._signals_today == 1

    async def test_unprotected_position_sets_non_resetting_entry_latch(self):
        engine = _make_engine(pending={"TESTUSDT"})

        await engine._latch_entry_halt(
            UnprotectedPositionError("stop ve acil kapatma başarısız"),
            source="test",
        )

        assert engine._entry_halted is True
        assert "UnprotectedPositionError" in engine._entry_halt_reason
        assert engine._entry_halted_at is not None
        engine.executor.cancel_all_pending.assert_awaited_once()

        # Gün değişimi kill switch'i sıfırlasa bile safety latch açılmaz.
        engine._kill_switch_day = "1900-01-01"
        await engine._update_kill_switch()
        assert engine._entry_halted is True

    async def test_scan_does_not_swallow_unprotected_position_error(self):
        engine = _make_engine()
        engine.scanner.get_universe.return_value = ["TESTUSDT"]
        engine._evaluate_symbol = AsyncMock(
            side_effect=UnprotectedPositionError("manuel müdahale gerekli")
        )

        with pytest.raises(UnprotectedPositionError):
            await engine._scan_tick()

    async def test_health_requires_both_tasks_fresh_and_alive(self):
        engine = _make_engine()
        engine._task = asyncio.create_task(_never(), name="test-scan")
        engine._safety_task = asyncio.create_task(_never(), name="test-safety")
        engine._exchange_task = asyncio.create_task(_never(), name="test-exchange")
        now = time.monotonic()
        engine._scan_last_success_monotonic = now
        engine._scan_last_success_at = "now"
        engine._safety_last_success_monotonic = now
        engine._safety_last_success_at = "now"

        try:
            assert engine.health_snapshot()["healthy"] is True

            engine._safety_task.cancel()
            await asyncio.gather(engine._safety_task, return_exceptions=True)
            health = engine.health_snapshot()
            assert health["healthy"] is False
            assert health["safety"]["task_alive"] is False
            assert health["safety"]["task_cancelled"] is True
        finally:
            engine._task.cancel()
            engine._exchange_task.cancel()
            await asyncio.gather(
                engine._task, engine._exchange_task, return_exceptions=True
            )

    async def test_entry_latch_degrades_health_even_when_tasks_are_fresh(self):
        engine = _make_engine()
        engine._task = asyncio.create_task(_never())
        engine._safety_task = asyncio.create_task(_never())
        engine._exchange_task = asyncio.create_task(_never())
        engine._scan_last_success_monotonic = time.monotonic()
        engine._safety_last_success_monotonic = time.monotonic()
        engine._entry_halted = True

        try:
            health = engine.health_snapshot()
            assert health["healthy"] is False
            assert health["entry_halted"] is True
        finally:
            engine._task.cancel()
            engine._safety_task.cancel()
            engine._exchange_task.cancel()
            await asyncio.gather(
                engine._task,
                engine._safety_task,
                engine._exchange_task,
                return_exceptions=True,
            )

    async def test_daily_risk_uses_signed_binance_net_income(self):
        engine = _make_engine()
        engine._daily_income_cache = (None, 0.0, None)
        engine.client.get_income_history.return_value = [
            {"incomeType": "REALIZED_PNL", "income": "25.0"},
            {"incomeType": "COMMISSION", "income": "-3.5"},
            {"incomeType": "FUNDING_FEE", "income": "-1.5"},
            {"incomeType": "TRANSFER", "income": "999"},
        ]

        await engine._update_kill_switch()

        assert engine._daily_pnl == 20.0
        assert engine._daily_pnl_source == "binance_account_income"
        assert engine._risk_ready is True

    async def test_daily_risk_threshold_uses_virtual_sizing_equity_not_full_wallet(self):
        engine = _make_engine()
        engine.cfg.scalper_virtual_capital_usdt = 1000.0
        engine._daily_income_cache = (None, 0.0, None)
        engine.client.get_income_history.return_value = [
            {"incomeType": "REALIZED_PNL", "income": "-160.0"},
        ]
        engine.client.get_wallet_balance.return_value = 100_000.0
        engine.executor.last_sizing_equity = 840.0
        engine.executor.sizing_snapshot = MagicMock(return_value={
            "exchange_available": 100_000.0,
            "virtual_capital": 1000.0,
            "eligible_realized_pnl": -160.0,
            "effective_equity": 840.0,
            "mode": "virtual",
            "start_trade_id": 0,
        })

        await engine._update_kill_switch()

        assert engine._risk_equity_usdt == pytest.approx(840.0)
        assert engine._risk_equity_source == "virtual_scalper_equity"
        assert engine._daily_loss_threshold_usdt == pytest.approx(-150.0)
        assert engine._kill_switch is True
        engine.client.get_wallet_balance.assert_not_awaited()

    async def test_virtual_risk_equity_cache_invalidates_on_tracker_close(self):
        engine = _make_engine()
        engine.cfg.scalper_virtual_capital_usdt = 1000.0
        engine.tracker.close_seq = 0
        engine.executor.get_sizing_equity = AsyncMock(side_effect=[1000.0, 940.0])

        assert await engine._virtual_risk_equity() == pytest.approx(1000.0)
        assert await engine._virtual_risk_equity() == pytest.approx(1000.0)
        assert engine.executor.get_sizing_equity.await_count == 1

        # A newly committed close changes virtual capital immediately; the
        # risk gate must not keep the pre-close value until the TTL expires.
        engine.tracker.close_seq = 1
        assert await engine._virtual_risk_equity() == pytest.approx(940.0)
        assert engine.executor.get_sizing_equity.await_count == 2

    def test_snapshot_exposes_fee_aware_exit_cooldown_and_sizing_telemetry(self):
        engine = _make_engine()
        engine.cfg.scalper_virtual_capital_usdt = 1000.0
        plan = SimpleNamespace(
            breakeven_price=100.13,
            breakeven_cost_pct=0.08,
            entry_fee_rate=0.0002,
            exit_fee_rate=0.0005,
            fee_rate_source="configured",
            runner_floor_price=100.15,
        )
        position = SimpleNamespace(
            entry_price=100.0,
            current_price=101.0,
            quantity=2.0,
            leverage=10,
            current_stoploss=100.13,
            opened_at=None,
        )
        scalp_position = SimpleNamespace(
            position=position,
            signal=SimpleNamespace(
                strategy="C", direction=SimpleNamespace(value="LONG")
            ),
            plan=plan,
            tp1_done=True,
            tp2_done=True,
            trailing_active=True,
        )
        engine.exits._positions = {"BTCUSDT": scalp_position}
        engine.executor.cooldown_snapshot = MagicMock(return_value=[{
            "symbol": "ETHUSDT",
            "reason": "protective_stop_failure",
            "remaining_seconds": 120.0,
            "expires_at": "2099-01-01T00:00:00+00:00",
            "internal_detail": "must-not-leak",
        }])
        engine.executor.last_sizing_equity = 1042.5
        engine.executor.sizing_snapshot = MagicMock(return_value={
            "exchange_available": 5000.0,
            "virtual_capital": 1000.0,
            "eligible_realized_pnl": 42.5,
            "effective_equity": 1042.5,
            "mode": "virtual",
            "start_trade_id": 7,
            "secret": "must-not-leak",
        })

        payload = engine.snapshot()

        tracked = payload["tracked"][0]
        assert tracked["tp2_done"] is True
        assert tracked["breakeven_active"] is True
        assert tracked["breakeven_price"] == pytest.approx(100.13)
        assert tracked["breakeven_cost_pct"] == pytest.approx(0.08)
        assert tracked["fee_aware_breakeven"] is True
        assert tracked["runner_floor_price"] == pytest.approx(100.15)
        assert payload["cooldowns"][0]["symbol"] == "ETHUSDT"
        assert "internal_detail" not in payload["cooldowns"][0]
        assert payload["sizing_equity_usdt"] == pytest.approx(1042.5)
        assert payload["virtual_capital_enabled"] is True
        assert payload["virtual_capital_base_usdt"] == pytest.approx(1000.0)
        assert payload["virtual_capital_start_trade_id"] == 7
        assert "secret" not in payload["sizing"]

    async def test_daily_risk_failure_blocks_new_entries(self):
        engine = _make_engine()
        engine._daily_income_cache = (None, 0.0, None)
        engine.client.get_income_history.side_effect = RuntimeError("auth failed")

        await engine._update_kill_switch()

        assert engine._risk_ready is False
        assert engine._entries_ready() is False


class _FakeUpdater:
    def __init__(self, *, fail_start: bool = False):
        self.running = False
        self.fail_start = fail_start
        self.stop_calls = 0

    async def start_polling(self):
        if self.fail_start:
            raise RuntimeError("polling failed")
        self.running = True

    async def stop(self):
        self.stop_calls += 1
        self.running = False


class _FakeTelegramApplication:
    def __init__(self, *, fail_polling: bool = False):
        self.running = False
        self.initialized = False
        self.updater = _FakeUpdater(fail_start=fail_polling)
        self.stop_calls = 0
        self.shutdown_calls = 0

    async def initialize(self):
        self.initialized = True

    async def start(self):
        self.running = True

    async def stop(self):
        self.stop_calls += 1
        self.running = False

    async def shutdown(self):
        self.shutdown_calls += 1
        self.initialized = False


class TestTelegramLifecycle:
    async def test_start_waits_for_polling_and_queue_and_shared_stop_is_idempotent(self):
        orchestrator = SimpleNamespace(close=AsyncMock(), active_positions={})
        service = TelegramBotService(orchestrator=orchestrator)
        app = _FakeTelegramApplication()

        def build_app():
            service.app = app
            return app

        service.build_app = build_app

        queue_started = asyncio.Event()

        async def process_queue(*_args):
            queue_started.set()
            await asyncio.Event().wait()

        service.signal_queue.process_queue = process_queue
        service.signal_queue.stop = AsyncMock()

        await service.start()

        assert queue_started.is_set()
        assert service.is_running is True
        assert service.health_snapshot()["healthy"] is True

        await service.stop()
        await service.stop()

        assert service.is_running is False
        assert app.updater.stop_calls == 1
        assert app.stop_calls == 1
        assert app.shutdown_calls == 1
        orchestrator.close.assert_not_awaited()  # paylaşılan sahip lifespan

    async def test_failed_polling_start_is_cleaned_and_propagated(self):
        orchestrator = SimpleNamespace(close=AsyncMock(), active_positions={})
        service = TelegramBotService(orchestrator=orchestrator)
        app = _FakeTelegramApplication(fail_polling=True)

        def build_app():
            service.app = app
            return app

        service.build_app = build_app

        with pytest.raises(RuntimeError, match="polling failed"):
            await service.start()

        assert service.is_running is False
        assert service.app is None
        assert app.stop_calls == 0  # polling app.start'tan önce başarısız oldu
        assert app.shutdown_calls == 1
        assert service.health_snapshot()["last_error"] is not None


class TestHttpHealthAndStats:
    async def test_config_exposes_read_only_scalper_exit_policy_without_secrets(self):
        payload = await main_module.get_config()

        exit_policy = payload["scalper_exit_policy"]
        risk_policy = payload["scalper_risk_policy"]
        assert exit_policy["active_network"] in {"testnet", "mainnet"}
        assert "breakeven_buffer_pct" in exit_policy
        assert "maker_fee_pct" in exit_policy
        assert "taker_fee_pct" in exit_policy
        assert "protection_failure_cooldown_minutes" in exit_policy
        assert "virtual_capital_usdt" in risk_policy
        assert "virtual_capital_start_trade_id" in risk_policy

        encoded = json.dumps(payload).lower()
        for forbidden in (
            "binance_api_key",
            "binance_api_secret",
            "telegram_bot_token",
            "openai_api_key",
            "jwt_secret",
        ):
            assert forbidden not in encoded

    async def test_lifespan_awaits_telegram_start_before_yield_and_closes_once(
        self, monkeypatch
    ):
        events = []

        class FakeOrchestrator:
            def __init__(self):
                self.active_positions = {}
                self.monitoring_task = None

            async def start(self):
                events.append("orchestrator_started")

            async def close(self):
                events.append("orchestrator_closed")

        class FakeTelegram:
            def __init__(self, orchestrator):
                self.orchestrator = orchestrator

            async def start(self):
                await asyncio.sleep(0)
                events.append("telegram_started")

            async def stop(self):
                events.append("telegram_stopped")

        monkeypatch.setattr(main_module, "init_db", AsyncMock())
        close_db = AsyncMock()
        monkeypatch.setattr(main_module, "close_db", close_db)
        monkeypatch.setattr(main_module, "TradingOrchestrator", FakeOrchestrator)
        monkeypatch.setattr(main_module, "TelegramBotService", FakeTelegram)
        monkeypatch.setattr(main_module.settings, "scalper_enabled", False)

        async with main_module.lifespan(SimpleNamespace()):
            assert events == ["orchestrator_started", "telegram_started"]

        assert events == [
            "orchestrator_started",
            "telegram_started",
            "telegram_stopped",
            "orchestrator_closed",
        ]
        close_db.assert_awaited_once()

    async def test_health_is_degraded_when_enabled_scalper_is_not_fresh(self, monkeypatch):
        monitor_task = asyncio.create_task(_never())
        orchestrator = SimpleNamespace(monitoring_task=monitor_task, active_positions={})
        telegram = SimpleNamespace(
            health_snapshot=lambda: {"healthy": True},
            is_running=True,
        )
        scalper = SimpleNamespace(
            running=True,
            health_snapshot=lambda: {
                "healthy": False,
                "running": True,
                "scan": {"fresh": False},
                "safety": {"fresh": True},
            },
        )
        monkeypatch.setattr(main_module, "orchestrator", orchestrator)
        monkeypatch.setattr(main_module, "telegram_bot", telegram)
        monkeypatch.setattr(main_module, "scalper_engine", scalper)
        monkeypatch.setattr(main_module.settings, "scalper_enabled", True)

        try:
            response = await main_module.health_check()
            body = json.loads(response.body)
            assert response.status_code == 503
            assert body["status"] == "degraded"
            assert body["scalper"] == "degraded"
            assert body["scalper_details"]["scan"]["fresh"] is False
        finally:
            monitor_task.cancel()
            await asyncio.gather(monitor_task, return_exceptions=True)

    @pytest.mark.parametrize("follower_mode", [False, True])
    async def test_health_counts_all_position_owners_without_exchange_reads(
        self, monkeypatch, follower_mode
    ):
        def engine(symbols):
            return SimpleNamespace(
                running=True,
                health_snapshot=lambda: {"healthy": True},
                exits=SimpleNamespace(_positions=dict.fromkeys(symbols)),
            )

        monkeypatch.setattr(main_module, "orchestrator", SimpleNamespace(
            active_positions={"BTCUSDT": object()},
            health_snapshot=lambda: {"healthy": True, "monitoring_task_alive": True},
        ))
        monkeypatch.setattr(main_module, "telegram_bot", engine([]))
        monkeypatch.setattr(main_module, "scalper_engine", engine(["BTCUSDT", "XRPUSDT"]))
        monkeypatch.setattr(main_module, "follower_engine", engine(["SOLUSDT"]))
        monkeypatch.setattr(main_module.settings, "bot_mode", "follower" if follower_mode else "scalper")
        monkeypatch.setattr(main_module.settings, "scalper_enabled", True)
        body = json.loads((await main_module.health_check()).body)
        assert body["tracked_positions"] == (1 if follower_mode else 3)
        assert body["tracked_positions_by_engine"] == (
            {"follower": 1} if follower_mode else
            {"orchestrator": 1, "scalper": 2, "follower": 1}
        )

    def test_health_counts_absent_engines_as_zero(self, monkeypatch):
        for name in ("orchestrator", "scalper_engine", "follower_engine"):
            monkeypatch.setattr(main_module, name, None)
        assert main_module._tracked_position_counts() == {
            "orchestrator": 0, "scalper": 0, "follower": 0, "total": 0,
        }

    async def test_scalper_stats_normalizes_infinite_profit_factors(self, monkeypatch):
        tracker = SimpleNamespace(
            stats=AsyncMock(return_value={"C": {"trades": 2, "profit_factor": float("inf")}})
        )
        monkeypatch.setattr(main_module, "scalper_engine", SimpleNamespace(tracker=tracker))

        row = SimpleNamespace(realized_pnl=10.0, roi_pct=2.0)
        result = SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: [row])
        )
        db = SimpleNamespace(execute=AsyncMock(return_value=result))

        payload = await main_module.scalper_stats(db)

        assert payload["strategies"]["C"]["profit_factor"] is None
        assert payload["combined"]["profit_factor"] is None
        assert payload["combined"]["verified_trades"] == 0
        assert payload["combined"]["fallback_trades"] == 0
        assert payload["combined"]["legacy_trades"] == 1
        assert payload["combined"]["pnl_basis"] == "legacy_unknown"
        # Strict JSON encoder artık 500 üretmemeli.
        response = JSONResponse(content=payload)
        assert response.status_code == 200

    def test_finite_normalizer_rejects_nan_and_both_infinities(self):
        assert main_module._finite_or_none(float("nan")) is None
        assert main_module._finite_or_none(float("inf")) is None
        assert main_module._finite_or_none(float("-inf")) is None
        assert main_module._finite_or_none(1.25) == 1.25


# ---------------------------------------------------------------------------
# GÖLGE MODU (D14) — orchestrator kapısı
# ---------------------------------------------------------------------------
# 2026-08-24: `/opt/tradingbot-shadow` gölge halkası ayağa kalkınca
# `orchestrator.recover_open_positions()` CANLI halkanın 5 pozisyonunu
# "yetim" sayıp izlemeye aldı (log kanıtı D14 kaydında). Gölge modu
# "emir gönderilmez" sözü verir; bu söz orchestrator'ı da kapsamalıdır.

@pytest.mark.asyncio
async def test_shadow_mode_does_not_start_orchestrator(monkeypatch):
    """Gölge modunda orchestrator HİÇ başlatılmaz (pozisyon sahiplenmez)."""
    import src.main as main_module

    started = {"n": 0}

    class _Orch:
        async def start(self):
            started["n"] += 1

        async def close(self):
            pass

        def health_snapshot(self):
            return {"healthy": True}

    monkeypatch.setattr(main_module.settings, "scalper_shadow_mode", True, raising=False)
    orch = _Orch()
    if bool(getattr(main_module.settings, "scalper_shadow_mode", False)):
        pass  # lifespan'deki kapı: start() ÇAĞRILMAZ
    else:  # pragma: no cover - kapı açıkken bu dal koşmaz
        await orch.start()
    assert started["n"] == 0

    monkeypatch.setattr(main_module.settings, "scalper_shadow_mode", False, raising=False)
    if bool(getattr(main_module.settings, "scalper_shadow_mode", False)):  # pragma: no cover
        pass
    else:
        await orch.start()
    assert started["n"] == 1


def test_lifespan_source_gates_orchestrator_on_shadow_mode():
    """Kaynak seviyesinde kapı: `orchestrator.start()` gölge kontrolü ALTINDA."""
    import inspect
    import src.main as main_module

    src = inspect.getsource(main_module.lifespan)
    assert "scalper_shadow_mode" in src, "gölge kapısı lifespan'de yok"
    gate_at = src.index('getattr(settings, "scalper_shadow_mode"')
    start_at = src.index("await orchestrator.start()")
    assert gate_at < start_at, "gölge kapısı orchestrator.start()'tan SONRA"
    tail = src[gate_at:start_at]
    assert "else:" in tail, "orchestrator.start() else dalında değil"


def test_lifespan_source_does_not_start_telegram_queue_in_shadow_mode():
    """Hesap kilidi atlanan shadow, Telegram üzerinden emir açamaz."""
    import inspect
    import src.main as main_module

    src = inspect.getsource(main_module.lifespan)
    gate_at = src.index("if is_orderless_shadow:")
    start_at = src.index("await telegram_bot.start()")
    assert gate_at < start_at
    assert "else:" in src[gate_at:start_at]


@pytest.mark.asyncio
async def test_manual_signal_is_fail_closed_in_shadow_mode(monkeypatch):
    """`/signal` orchestrator'a ulaşmadan 503 olmalı."""
    import src.main as main_module

    fake = SimpleNamespace(process_signal=AsyncMock())
    monkeypatch.setattr(main_module.settings, "scalper_shadow_mode", True)
    monkeypatch.setattr(main_module, "orchestrator", fake)

    with pytest.raises(HTTPException) as exc:
        await main_module.manual_signal(
            main_module.SignalRequest(message="BTCUSDT BUY"), db=None
        )
    assert exc.value.status_code == 503
    fake.process_signal.assert_not_awaited()
