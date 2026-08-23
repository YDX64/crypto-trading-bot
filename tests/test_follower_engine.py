"""FollowerEngine + FollowerExitManager akışı (D20).

Kapsam:
  * kapılar: evren, zaman dilimi, kapasite, cooldown, kill switch, risk-olayı
    halt'ı, giriş kilidi, skor filtresi;
  * ters sinyal (flip) → kapat + yeni yöne gir;
  * AlgoPro EXIT → reduce-only kapanış + borsa doğrulaması (fail-closed);
  * TP/SL HIT çapraz doğrulaması (borsada pozisyon açıksa WARNING, kabul YOK);
  * gerçek `FollowerExitManager` ile açılış → TP1 → break-even akışı.

GERÇEK AĞ/DB YOK: client/pm/tracker sahte, motor `object.__new__` ile kurulur
(tests/test_risk_event.py'deki `_make_engine` deseni).
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.models.waiting_signal  # noqa: F401  (SQLAlchemy mapper zinciri)
from src.strategies.follower.engine import FollowerEngine
from src.strategies.follower.executor import FollowerPosition
from src.strategies.follower.exits import FollowerExitManager
from src.strategies.follower.parser import parse_follower_event
from src.strategies.follower.risk_halt import RiskEventHaltStore
from src.strategies.follower.types import FollowerRejected
from src.strategies.scalper.types import Direction, ExitPlan, Regime, ScalpSignal
from src.models.position import PositionModel, PositionSide, PositionStatus

SELL_ENTRY = (
    "🔴 SELL | BINANCE:BTCUSDT | TF: 1 | Price: 77126.08 | TQI: .45 | Score: 8 "
    "| SL: 77167.77 | TP1: 77105.23 | TP2: 77084.39 | TP3: 77063.54"
)
BUY_ENTRY = (
    "🟢 BUY | BINANCE:BTCUSDT | TF: 1 | Price: 77126.08 | TQI: .55 | Score: 9 "
    "| SL: 77084.39 | TP1: 77146.93 | TP2: 77167.77 | TP3: 77188.62"
)
EXIT_EVENT = "⚪ EXIT | BINANCE:BTCUSDT | TF: 1 | Price: 77100.00"
SL_HIT = "🛑 SL HIT | BINANCE:BTCUSDT | TF: 1 | Price: 77167.77"
TP1_HIT = "🎯 TP1 HIT | BINANCE:BTCUSDT | TF: 1 | Price: 77105.23"


def _cfg(**overrides):
    base = dict(
        follower_symbol_allowlist="BTCUSDT,ETHUSDT",
        follower_timeframe="1",
        follower_max_positions=4,
        follower_min_score=0.0,
        follower_flip=True,
        follower_cooldown_sec=60.0,
        follower_margin_pct=10.0,
        follower_sl_roi_target=30.0,
        follower_lev_min=3,
        follower_lev_max=100,
        follower_lev_liq_guard_pct=50.0,
        follower_mmr_safety_mult=2.0,
        follower_tp_rr1=0.5,
        follower_tp_rr2=1.0,
        follower_tp_rr3=1.5,
        follower_min_sl_pct=0.02,
        follower_max_sl_pct=5.0,
        follower_sl_atr_mult=3.0,
        follower_atr_len=14,
        follower_levels_log_path="",  # kalibrasyon defteri testte kapalı
        follower_daily_loss_limit_pct=15.0,
        scalper_breakeven_buffer_pct=0.05,
        scalper_taker_fee_pct=0.05,
        scalper_maker_fee_pct=0.02,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _fake_position(symbol="BTCUSDT", direction=Direction.SHORT, qty=0.12):
    signal = ScalpSignal(
        strategy="AP",
        symbol=symbol,
        direction=direction,
        entry_price=77126.08,
        stop_price=77167.77,
        reason="algopro:entry",
        regime=Regime.UNKNOWN,
        atr_5m=0.0,
        leverage=100,
    )
    position = PositionModel(
        symbol=symbol,
        side=PositionSide.SHORT if direction == Direction.SHORT else PositionSide.LONG,
        leverage=100,
        margin_type="ISOLATED",
        entry_price=77126.08,
        current_price=77126.08,
        quantity=qty,
        position_size=qty * 77126.08,
        initial_stoploss=77167.77,
        current_stoploss=77167.77,
        first_tp_price=77105.23,
        first_tp_quantity=qty / 3,
        targets="[]",
        status=PositionStatus.OPEN,
        entry_order_id="1",
        sl_order_id="500",
        tp_order_id="501",
        highest_price=77126.08,
        lowest_price=77126.08,
    )
    plan = ExitPlan(
        tp1_price=77105.23,
        tp1_quantity=qty / 3,
        tp2_price=77084.39,
        tp2_quantity=qty / 3,
        runner_quantity=0.0,
        initial_stop=77167.77,
        breakeven_price=77080.0,
        chandelier_atr_mult=0.0,
        entry_fee_rate=0.0005,
        exit_fee_rate=0.0005,
        fee_rate_source="config_conservative",
        breakeven_cost_pct=0.06,
        runner_floor_price=77105.23,
        tp1_algo_id="501",
        tp2_algo_id="502",
        tp3_price=77063.54,
        tp3_quantity=qty / 3,
        tp3_algo_id="503",
    )
    return FollowerPosition(
        trade_id=42,
        signal=signal,
        position=position,
        plan=plan,
        entry_candle_time=0,
        meta={"plan": {"leverage": 100, "sl_pct": 0.054, "sl_roi_pct": 5.4,
                       "margin_usdt": 100.0, "levels": {"source": "message"}}},
    )


def _make_engine(tmp_path, cfg=None, *, positions=None, position_amt=0.0):
    engine = object.__new__(FollowerEngine)
    # Kapanış doğrulama merdiveni testte GERÇEK uyku yapmasın.
    engine._CLOSE_VERIFY_DELAYS = (0.0, 0.0)
    engine.cfg = cfg or _cfg()
    engine.logger = MagicMock()
    engine.running = True
    engine._entry_lock = asyncio.Lock()
    engine._exchange_ready = True
    engine._exchange_last_error = None
    engine._exchange_last_success_monotonic = time.monotonic()
    engine._recovery_ready = True
    engine._risk_ready = True
    engine._entry_halted = False
    engine._entry_halt_reason = None
    engine._entry_halted_at = None
    engine._entry_halt_path = tmp_path / "follower_entry_halt.json"
    engine._kill_switch = False
    engine._kill_switch_day = None
    engine._daily_pnl = 0.0
    engine._daily_loss_threshold_usdt = None
    engine._risk_equity_usdt = None
    engine._events = deque(maxlen=50)
    engine._event_counters = {}
    engine._reject_counters = {}
    engine._last_event_at = None
    engine._safety_last_success_monotonic = time.monotonic()
    engine._safety_last_error = None
    engine._safety_task = None
    engine.halt = RiskEventHaltStore(str(tmp_path / "risk_halt.json"), logger=MagicMock())

    tracked = dict(positions or {})
    engine.exits = SimpleNamespace(
        _positions=tracked,
        tracked_symbols=lambda: set(tracked.keys()),
        track=lambda sp: tracked.__setitem__(sp.position.symbol, sp),
        _handle_closed=AsyncMock(),
    )
    engine.executor = SimpleNamespace(
        is_entry_blocked=MagicMock(return_value=False),
        open_position=AsyncMock(return_value=_fake_position()),
        start_cooldown=MagicMock(),
        cooldown_snapshot=MagicMock(return_value=[]),
        reject_snapshot=MagicMock(return_value={}),
    )
    engine.client = SimpleNamespace(
        get_current_price=AsyncMock(return_value=77126.08),
        get_account_balance=AsyncMock(return_value=1000.0),
        get_position_risk=AsyncMock(return_value={"positionAmt": position_amt}),
        quantize_quantity=AsyncMock(side_effect=lambda symbol, qty: qty),
        _request_with_retry=AsyncMock(return_value={}),
    )
    engine.fetcher = SimpleNamespace(get_klines=AsyncMock(return_value=[]))
    engine.brackets = SimpleNamespace(snapshot=MagicMock(return_value={}))
    return engine


class TestGates:
    async def test_symbol_outside_universe_rejected(self, tmp_path):
        engine = _make_engine(tmp_path)
        event = parse_follower_event(
            "🟢 BUY | BINANCE:SOLUSDT | TF: 1 | Price: 150 | SL: 149"
        )
        result = await engine.handle_event(event)
        assert result["accepted"] is False
        assert "evren" in result["reason"]
        engine.executor.open_position.assert_not_called()

    async def test_timeframe_mismatch_rejected(self, tmp_path):
        engine = _make_engine(tmp_path)
        event = parse_follower_event(
            "🟢 BUY | BINANCE:BTCUSDT | TF: 5 | Price: 100 | SL: 99.9"
        )
        result = await engine.handle_event(event)
        assert result["accepted"] is False
        assert "zaman dilimi" in result["reason"]

    async def test_1m_alias_accepted(self, tmp_path):
        engine = _make_engine(tmp_path)
        event = parse_follower_event(
            "🔴 SELL | BINANCE:BTCUSDT | TF: 1m | Price: 77126.08 | SL: 77167.77"
        )
        result = await engine.handle_event(event)
        assert result["accepted"] is True

    async def test_capacity_full_rejected(self, tmp_path):
        cfg = _cfg(follower_max_positions=1)
        engine = _make_engine(
            tmp_path, cfg, positions={"ETHUSDT": _fake_position("ETHUSDT")}
        )
        result = await engine.handle_event(parse_follower_event(SELL_ENTRY))
        assert result["accepted"] is False
        assert "kapasite" in result["reason"]

    async def test_cooldown_rejected(self, tmp_path):
        engine = _make_engine(tmp_path)
        engine.executor.is_entry_blocked = MagicMock(return_value=True)
        result = await engine.handle_event(parse_follower_event(SELL_ENTRY))
        assert result["accepted"] is False
        assert "cooldown" in result["reason"]

    async def test_kill_switch_rejected(self, tmp_path):
        engine = _make_engine(tmp_path)
        engine._kill_switch = True
        result = await engine.handle_event(parse_follower_event(SELL_ENTRY))
        assert result["accepted"] is False
        assert "kill switch" in result["reason"]

    async def test_risk_event_halt_rejected(self, tmp_path):
        engine = _make_engine(tmp_path)
        engine.halt.halt(reason="savaş çıktı", source="ops", ttl_minutes=60)
        result = await engine.handle_event(parse_follower_event(SELL_ENTRY))
        assert result["accepted"] is False
        assert "risk-event halt" in result["reason"]

    async def test_entry_halt_rejected(self, tmp_path):
        engine = _make_engine(tmp_path)
        engine._entry_halted = True
        engine._entry_halt_reason = "UnprotectedPositionError: test"
        result = await engine.handle_event(parse_follower_event(SELL_ENTRY))
        assert result["accepted"] is False
        assert "giriş kilidi" in result["reason"]

    async def test_min_score_filter(self, tmp_path):
        engine = _make_engine(tmp_path, _cfg(follower_min_score=9.0))
        result = await engine.handle_event(parse_follower_event(SELL_ENTRY))
        assert result["accepted"] is False
        assert "skoru düşük" in result["reason"]

    async def test_min_score_filter_disabled_by_default(self, tmp_path):
        engine = _make_engine(tmp_path)
        result = await engine.handle_event(parse_follower_event(SELL_ENTRY))
        assert result["accepted"] is True

    async def test_same_direction_duplicate_rejected(self, tmp_path):
        engine = _make_engine(
            tmp_path, positions={"BTCUSDT": _fake_position(direction=Direction.SHORT)}
        )
        result = await engine.handle_event(parse_follower_event(SELL_ENTRY))
        assert result["accepted"] is False
        assert "aynı yönde" in result["reason"]

    async def test_stop_band_rejection_is_reported(self, tmp_path):
        engine = _make_engine(tmp_path)
        event = parse_follower_event(
            "🟢 BUY | BINANCE:BTCUSDT | TF: 1 | Price: 100 | SL: 99.9999"
        )
        result = await engine.handle_event(event)
        assert result["accepted"] is False
        assert "bant dışı" in result["reason"]


class TestExchangeTruthGate:
    """Borsa gerçeği son kapıdır: izlenmeyen ama AÇIK pozisyon üstüne girilmez."""

    async def test_untracked_live_position_blocks_entry(self, tmp_path):
        engine = _make_engine(tmp_path, position_amt=-0.05)
        result = await engine.handle_event(parse_follower_event(SELL_ENTRY))
        assert result["accepted"] is False
        assert "izlenmeyen açık pozisyon" in result["reason"]
        engine.executor.open_position.assert_not_called()

    async def test_position_read_failure_is_fail_closed(self, tmp_path):
        engine = _make_engine(tmp_path)
        engine.client.get_position_risk = AsyncMock(
            side_effect=RuntimeError("ağ hatası")
        )
        result = await engine.handle_event(parse_follower_event(SELL_ENTRY))
        assert result["accepted"] is False
        assert "doğrulanamadı" in result["reason"]
        engine.executor.open_position.assert_not_called()


class TestEntryFlow:
    async def test_entry_opens_and_tracks(self, tmp_path):
        engine = _make_engine(tmp_path)
        result = await engine.handle_event(parse_follower_event(SELL_ENTRY))
        assert result["accepted"] is True
        assert result["trade_id"] == 42
        assert "BTCUSDT" in engine.exits.tracked_symbols()
        call = engine.executor.open_position.await_args.kwargs
        assert call["levels"].stop == pytest.approx(77167.77)
        assert call["levels"].tps == pytest.approx((77105.23, 77084.39, 77063.54))
        assert call["equity_usdt"] == pytest.approx(1000.0)

    async def test_executor_failure_reported(self, tmp_path):
        engine = _make_engine(tmp_path)
        engine.executor.open_position = AsyncMock(return_value=None)
        result = await engine.handle_event(parse_follower_event(SELL_ENTRY))
        assert result["accepted"] is False
        assert "emir yolu" in result["reason"]
        assert engine.exits.tracked_symbols() == set()

    async def test_rejection_from_executor_is_surfaced(self, tmp_path):
        engine = _make_engine(tmp_path)
        engine.executor.open_position = AsyncMock(
            side_effect=FollowerRejected("borsa dilimi okunamadı", code="no_bracket")
        )
        result = await engine.handle_event(parse_follower_event(SELL_ENTRY))
        assert result["accepted"] is False
        assert result["reason"] == "borsa dilimi okunamadı"
        assert engine._reject_counters["no_bracket"] == 1

    async def test_missing_price_falls_back_to_live_price(self, tmp_path):
        engine = _make_engine(tmp_path)
        event = parse_follower_event(
            "🔴 SELL | BINANCE:BTCUSDT | TF: 1 | SL: 77167.77"
        )
        result = await engine.handle_event(event)
        assert result["accepted"] is True
        engine.client.get_current_price.assert_awaited()

    async def test_atr_fallback_only_when_message_has_no_sl(self, tmp_path):
        engine = _make_engine(tmp_path)
        await engine.handle_event(parse_follower_event(SELL_ENTRY))
        engine.fetcher.get_klines.assert_not_called()


class TestFlip:
    async def test_reverse_signal_closes_and_reopens(self, tmp_path):
        existing = _fake_position(direction=Direction.SHORT)
        engine = _make_engine(tmp_path, positions={"BTCUSDT": existing})
        # 1) flip kapanışı için canlı miktar, 2) kapanış doğrulaması,
        # 3) yeni girişten önceki "izlenmeyen pozisyon" kapısı.
        engine.client.get_position_risk = AsyncMock(
            side_effect=[
                {"positionAmt": -0.12},
                {"positionAmt": 0.0},
                {"positionAmt": 0.0},
            ]
        )
        engine.executor.open_position = AsyncMock(
            return_value=_fake_position(direction=Direction.LONG)
        )
        result = await engine.handle_event(parse_follower_event(BUY_ENTRY))
        assert result["accepted"] is True
        assert result["flipped"] is True
        engine.exits._handle_closed.assert_awaited_once()
        assert (
            engine.exits._handle_closed.await_args.kwargs["forced_exit_reason"]
            == "AP_REVERSE"
        )

    async def test_flip_disabled_keeps_position(self, tmp_path):
        existing = _fake_position(direction=Direction.SHORT)
        engine = _make_engine(
            tmp_path, _cfg(follower_flip=False), positions={"BTCUSDT": existing}
        )
        result = await engine.handle_event(parse_follower_event(BUY_ENTRY))
        assert result["accepted"] is False
        assert "FOLLOWER_FLIP kapalı" in result["reason"]
        engine.exits._handle_closed.assert_not_called()

    async def test_kill_switch_still_allows_flip_close_but_no_reentry(self, tmp_path):
        """Kapı kapalıysa sonuç FLAT kalmaktır: ters sinyal kapatır, açmaz."""
        existing = _fake_position(direction=Direction.SHORT)
        engine = _make_engine(tmp_path, positions={"BTCUSDT": existing})
        engine._kill_switch = True
        engine.client.get_position_risk = AsyncMock(
            side_effect=[{"positionAmt": -0.12}, {"positionAmt": 0.0}]
        )
        result = await engine.handle_event(parse_follower_event(BUY_ENTRY))
        assert result["accepted"] is False
        assert "kill switch" in result["reason"]
        engine.exits._handle_closed.assert_awaited_once()  # kapanış YAPILDI
        engine.executor.open_position.assert_not_called()  # yeni giriş YOK

    async def test_flip_close_failure_blocks_new_entry(self, tmp_path):
        existing = _fake_position(direction=Direction.SHORT)
        engine = _make_engine(tmp_path, positions={"BTCUSDT": existing})
        # Kapanış borsada ASLA doğrulanmıyor → yeni giriş YAPILMAZ.
        engine.client.get_position_risk = AsyncMock(
            return_value={"positionAmt": -0.12}
        )
        result = await engine.handle_event(parse_follower_event(BUY_ENTRY))
        assert result["accepted"] is False
        assert "kapatılamadı" in result["reason"]
        engine.executor.open_position.assert_not_called()
        engine.exits._handle_closed.assert_not_called()


class TestExitAndHitEvents:
    async def test_exit_closes_position(self, tmp_path):
        engine = _make_engine(tmp_path, positions={"BTCUSDT": _fake_position()})
        engine.client.get_position_risk = AsyncMock(
            side_effect=[{"positionAmt": -0.12}, {"positionAmt": 0.0}]
        )
        result = await engine.handle_event(parse_follower_event(EXIT_EVENT))
        assert result["accepted"] is True
        assert (
            engine.exits._handle_closed.await_args.kwargs["forced_exit_reason"]
            == "AP_EXIT"
        )

    async def test_exit_without_position_is_noop(self, tmp_path):
        engine = _make_engine(tmp_path)
        result = await engine.handle_event(parse_follower_event(EXIT_EVENT))
        assert result["accepted"] is False
        assert "izlenen pozisyon yok" in result["reason"]

    async def test_exit_close_verification_failure_keeps_tracking(self, tmp_path):
        engine = _make_engine(tmp_path, positions={"BTCUSDT": _fake_position()})
        engine.client.get_position_risk = AsyncMock(
            return_value={"positionAmt": -0.12}
        )
        result = await engine.handle_event(parse_follower_event(EXIT_EVENT))
        assert result["accepted"] is False
        engine.exits._handle_closed.assert_not_called()
        assert "BTCUSDT" in engine.exits.tracked_symbols()

    async def test_sl_hit_when_exchange_flat_finalizes(self, tmp_path):
        engine = _make_engine(
            tmp_path, positions={"BTCUSDT": _fake_position()}, position_amt=0.0
        )
        result = await engine.handle_event(parse_follower_event(SL_HIT))
        assert result["accepted"] is True
        engine.exits._handle_closed.assert_awaited_once()

    async def test_sl_hit_while_position_open_warns(self, tmp_path):
        engine = _make_engine(
            tmp_path, positions={"BTCUSDT": _fake_position()}, position_amt=-0.12
        )
        result = await engine.handle_event(parse_follower_event(SL_HIT))
        assert result["accepted"] is False
        assert "borsada pozisyon açık" in result["reason"]
        assert engine.logger.warning.called

    async def test_tp1_hit_is_telemetry_only(self, tmp_path):
        engine = _make_engine(
            tmp_path, positions={"BTCUSDT": _fake_position()}, position_amt=-0.08
        )
        result = await engine.handle_event(parse_follower_event(TP1_HIT))
        assert result["accepted"] is True
        engine.exits._handle_closed.assert_not_called()

    async def test_hit_without_tracked_position(self, tmp_path):
        engine = _make_engine(tmp_path)
        result = await engine.handle_event(parse_follower_event(TP1_HIT))
        assert result["accepted"] is False


class TestTelemetry:
    async def test_events_and_counters_recorded(self, tmp_path):
        engine = _make_engine(tmp_path)
        await engine.handle_event(parse_follower_event(SELL_ENTRY))
        await engine.handle_event(parse_follower_event(TP1_HIT))
        assert engine._event_counters == {"entry": 1, "tp1": 1}
        assert len(engine._events) == 2
        assert engine._events[0]["score"] == 8.0
        assert engine._events[0]["tqi"] == 0.45

    async def test_snapshot_exposes_sizing_and_positions(self, tmp_path):
        engine = _make_engine(tmp_path, positions={"BTCUSDT": _fake_position()})
        snapshot = engine.snapshot()
        assert snapshot["mode"] == "follower"
        assert snapshot["strategy"] == "AP"
        assert snapshot["sizing"]["margin_pct"] == 10.0
        assert snapshot["sizing"]["lev_max"] == 100
        position = snapshot["positions"][0]
        assert position["symbol"] == "BTCUSDT"
        assert position["sl_pct"] == 0.054
        assert position["sl_roi_pct"] == 5.4
        assert position["margin_usdt"] == 100.0
        assert position["tp3"] == pytest.approx(77063.54)

    async def test_unexpected_error_is_contained(self, tmp_path):
        engine = _make_engine(tmp_path)
        engine.executor.open_position = AsyncMock(side_effect=RuntimeError("boom"))
        result = await engine.handle_event(parse_follower_event(SELL_ENTRY))
        assert result["accepted"] is False
        assert "beklenmeyen hata" in result["reason"]


# ---------------------------------------------------------------------------
# Gerçek FollowerExitManager: açılış → TP1 → break-even
# ---------------------------------------------------------------------------


class _ExitFakeClient:
    def __init__(self, live_qty: float):
        self.live_qty = live_qty
        self.calls: list = []

    async def get_position_risk(self, symbol, force_fresh=False):
        return {"positionAmt": -self.live_qty}

    async def get_current_price(self, symbol):
        return 77100.0

    async def get_algo_order(self, algo_id=None, client_algo_id=None):
        self.calls.append(("get_algo_order", algo_id))
        return {"actualOrderId": 9001, "quantity": 0.04}

    async def get_account_trades(self, symbol, order_id=None, limit=500):
        return [
            {
                "orderId": 9001,
                "qty": 0.04,
                "price": 77105.23,
                "buyer": True,
                "commission": 0.01,
                "commissionAsset": "USDT",
                "realizedPnl": 0.8,
                "time": int(time.time() * 1000),
                "id": 1,
            }
        ]


class TestExitManagerBreakEven:
    def _manager(self, live_qty):
        client = _ExitFakeClient(live_qty)
        pm = SimpleNamespace(replace_stop_loss=AsyncMock(return_value=True))
        tracker = SimpleNamespace(record_close=AsyncMock(), close_seq=0)
        manager = FollowerExitManager(client, pm, tracker, _cfg())
        manager.logger = MagicMock()
        return manager, client, pm

    async def test_tp1_fill_moves_stop_to_breakeven(self):
        manager, client, pm = self._manager(live_qty=0.08)
        sp = _fake_position(qty=0.12)
        manager.track(sp)
        await manager._step_one("BTCUSDT", sp)
        assert sp.tp1_done is True
        pm.replace_stop_loss.assert_awaited_once()
        assert pm.replace_stop_loss.await_args.args[1] == pytest.approx(77080.0)
        assert sp.position.current_stoploss == pytest.approx(77080.0)
        # Takipçide chandelier trailing YOKTUR.
        assert sp.trailing_active is False

    async def test_no_breakeven_without_confirmed_fill(self):
        manager, client, pm = self._manager(live_qty=0.08)
        client.get_algo_order = AsyncMock(return_value={"actualOrderId": None})
        sp = _fake_position(qty=0.12)
        manager.track(sp)
        await manager._step_one("BTCUSDT", sp)
        assert sp.tp1_done is False
        pm.replace_stop_loss.assert_not_called()

    async def test_no_action_while_quantity_unchanged(self):
        manager, client, pm = self._manager(live_qty=0.12)
        sp = _fake_position(qty=0.12)
        manager.track(sp)
        await manager._step_one("BTCUSDT", sp)
        assert sp.tp1_done is False
        pm.replace_stop_loss.assert_not_called()

    async def test_flat_position_is_finalized(self):
        manager, client, pm = self._manager(live_qty=0.0)
        manager._handle_closed = AsyncMock()
        sp = _fake_position(qty=0.12)
        manager.track(sp)
        await manager._step_one("BTCUSDT", sp)
        manager._handle_closed.assert_awaited_once()

    async def test_open_tp1_breakeven_exit_end_to_end(self, tmp_path):
        """TAM AKIŞ: AlgoPro girişi → TP1 dolumu → BE → AlgoPro EXIT → kapanış.

        Motor GERÇEK `FollowerExitManager` ile çalışır (yalnız executor ve
        borsa/DB sahte). Zincirin her halkası tek testte doğrulanır.
        """
        client = _ExitFakeClient(live_qty=0.12)
        pm = SimpleNamespace(replace_stop_loss=AsyncMock(return_value=True))
        tracker = SimpleNamespace(record_close=AsyncMock(), close_seq=0)
        cooldowns: list = []
        manager = FollowerExitManager(
            client, pm, tracker, _cfg(), exit_cooldown_cb=cooldowns.append
        )
        manager.logger = MagicMock()
        manager._finalize_close = AsyncMock()  # kapanış defteri ayrıca test edildi

        engine = _make_engine(tmp_path)
        engine.exits = manager
        engine.client.get_position_risk = AsyncMock(
            return_value={"positionAmt": 0.0}
        )
        engine.executor.open_position = AsyncMock(return_value=_fake_position(qty=0.12))

        # 1) Giriş
        result = await engine.handle_event(parse_follower_event(SELL_ENTRY))
        assert result["accepted"] is True
        assert manager.tracked_symbols() == {"BTCUSDT"}
        sp = manager._positions["BTCUSDT"]

        # 2) TP1 dolumu → break-even (safety turu)
        client.live_qty = 0.08
        await manager.step()
        assert sp.tp1_done is True
        assert sp.position.current_stoploss == pytest.approx(77080.0)
        assert sp.trailing_active is False  # takipçide trailing YOK

        # 3) AlgoPro EXIT → reduce-only kapanış + borsa doğrulaması
        engine.client.get_position_risk = AsyncMock(
            side_effect=[{"positionAmt": -0.08}, {"positionAmt": 0.0}]
        )
        exit_result = await engine.handle_event(parse_follower_event(EXIT_EVENT))
        assert exit_result["accepted"] is True
        engine.client._request_with_retry.assert_awaited()
        order = engine.client._request_with_retry.await_args.kwargs["params"]
        assert order["reduceOnly"] == "true"
        assert order["side"] == "BUY"  # SHORT'u kapatır
        assert order["quantity"] == pytest.approx(0.08)  # CANLI miktar
        assert manager.tracked_symbols() == set()
        # Kapanış defteri AP_EXIT etiketiyle işletildi (cooldown ve PnL
        # doğrulaması `_finalize_close` içinde — ayrıca test edilir).
        assert (
            manager._finalize_close.await_args.kwargs["forced_exit_reason"]
            == "AP_EXIT"
        )

    async def test_every_exit_starts_cooldown(self):
        """Scalper yalnız KAYIPTA cooldown başlatır; takipçi HER çıkışta."""
        cooldowns: list = []
        client = _ExitFakeClient(0.0)
        manager = FollowerExitManager(
            client,
            SimpleNamespace(),
            SimpleNamespace(),
            _cfg(),
            exit_cooldown_cb=cooldowns.append,
        )
        manager._maybe_start_loss_cooldown("BTCUSDT", "TP_LADDER", 12.5, 0.0)
        assert cooldowns == ["BTCUSDT"]
