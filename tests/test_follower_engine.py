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
                       "sl_pct_fill": 0.054, "tp_roi_pct": [2.7, 5.4, 8.1],
                       "fee_roi_real_pct": 10.0, "tp1_covers_fees_real": False,
                       "margin_usdt": 100.0, "levels": {"source": "message"}}},
    )


def _make_engine(
    tmp_path,
    cfg=None,
    *,
    positions=None,
    position_amt=0.0,
    live_price=77126.08,
    all_positions=None,
):
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
    engine._orphans = []
    engine._orphans_checked_at = None
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
        _closing=set(),
        tracked_symbols=lambda: set(tracked.keys()),
        track=lambda sp: tracked.__setitem__(sp.position.symbol, sp),
        _handle_closed=AsyncMock(),
        ensure_tp_orders=AsyncMock(return_value=0),
        tp_repair_snapshot=MagicMock(return_value={}),
    )
    engine.executor = SimpleNamespace(
        is_entry_blocked=MagicMock(return_value=False),
        open_position=AsyncMock(return_value=_fake_position()),
        start_cooldown=MagicMock(),
        cooldown_snapshot=MagicMock(return_value=[]),
        reject_snapshot=MagicMock(return_value={}),
    )
    engine.client = SimpleNamespace(
        get_current_price=AsyncMock(return_value=live_price),
        get_account_balance=AsyncMock(return_value=1000.0),
        get_position_risk=AsyncMock(return_value={"positionAmt": position_amt}),
        quantize_quantity=AsyncMock(side_effect=lambda symbol, qty: qty),
        _request_with_retry=AsyncMock(return_value={}),
        # Yetim denetimi (bulgu 8) borsanın TÜM pozisyonlarını okur.
        get_all_positions=AsyncMock(return_value=list(all_positions or [])),
    )
    engine.fetcher = SimpleNamespace(get_klines=AsyncMock(return_value=[]))
    engine.brackets = SimpleNamespace(snapshot=MagicMock(return_value={}))
    return engine


class TestGates:
    async def test_symbol_outside_universe_rejected(self, tmp_path):
        engine = _make_engine(tmp_path)
        event = parse_follower_event(
            "🟢 BUY | BINANCE:SOLUSDT | TF: 1 | Price: 150 | SL: 149 "
            "| TP1: 150.5 | TP2: 151.0 | TP3: 151.5"
        )
        result = await engine.handle_event(event)
        assert result["accepted"] is False
        # D20b: TEK ret adı (köprü yolu ile aynı); insan-okur metin `detail`de.
        assert result["reason"] == "symbol_not_in_follower_universe"
        assert "evren" in result["detail"]
        engine.executor.open_position.assert_not_called()

    async def test_timeframe_mismatch_rejected(self, tmp_path):
        engine = _make_engine(tmp_path)
        event = parse_follower_event(
            "🟢 BUY | BINANCE:BTCUSDT | TF: 5 | Price: 100 | SL: 99.9 "
            "| TP1: 100.05 | TP2: 100.1 | TP3: 100.15"
        )
        result = await engine.handle_event(event)
        assert result["accepted"] is False
        assert "zaman dilimi" in result["reason"]

    async def test_1m_alias_accepted(self, tmp_path):
        engine = _make_engine(tmp_path)
        event = parse_follower_event(
            "🔴 SELL | BINANCE:BTCUSDT | TF: 1m | Price: 77126.08 | SL: 77167.77 "
            "| TP1: 77105.23 | TP2: 77084.39 | TP3: 77063.54"
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
        engine = _make_engine(tmp_path, live_price=100.0)
        event = parse_follower_event(
            "🟢 BUY | BINANCE:BTCUSDT | TF: 1 | Price: 100 | SL: 99.9999 "
            "| TP1: 100.00005 | TP2: 100.0001 | TP3: 100.00015"
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

    async def test_sizing_always_uses_the_live_price(self, tmp_path):
        """Bulgu 6: seviyeler/boyutlama ASLA bayat alarm fiyatından.

        Düzeltme olmadan KIRMIZI: eski kod `event.price` varsa canlı fiyatı
        HİÇ okumuyor ve `sl_pct`i (kaldıraç formülünün paydası) bayat
        fiyattan hesaplıyordu.
        """
        engine = _make_engine(tmp_path, live_price=77128.0)
        result = await engine.handle_event(parse_follower_event(SELL_ENTRY))
        assert result["accepted"] is True
        engine.client.get_current_price.assert_awaited()
        levels = engine.executor.open_position.await_args.kwargs["levels"]
        assert levels.entry == pytest.approx(77128.0)

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


class TestConcurrencyGates:
    """Eşzamanlılık kusurları (düşmanca inceleme, 2026-08-23) için regresyon."""

    async def test_close_in_flight_blocks_new_entry(self, tmp_path):
        """Kapanış defteri işlenirken açılan pozisyonun SL/TP'si iptal edilebilir.

        `_finalize_close`'un İLK işi `cancel_all_open_orders(symbol)`'dır ve
        saniyeler sürebilir; o pencerede açılan yeni pozisyon korumasız
        kalabilirdi.
        """
        engine = _make_engine(tmp_path)
        engine.exits._closing = {"BTCUSDT"}

        result = await engine.handle_event(parse_follower_event(SELL_ENTRY))

        assert result["accepted"] is False
        assert "kapanış defteri işleniyor" in result["reason"]
        engine.executor.open_position.assert_not_called()
        assert engine._reject_counters.get("close_in_flight") == 1

    async def test_stale_safety_loop_blocks_entries(self, tmp_path):
        """Safety turu (TP1→BE, kapanış defteri, kill switch) bayatsa giriş YOK."""
        engine = _make_engine(tmp_path)
        engine._safety_last_success_monotonic = time.monotonic() - 600.0
        engine._safety_last_error = "RuntimeError: boom"

        result = await engine.handle_event(parse_follower_event(SELL_ENTRY))

        assert result["accepted"] is False
        assert "safety turu bayat" in result["reason"]
        engine.executor.open_position.assert_not_called()

    async def test_flatten_waits_for_an_in_flight_entry(self, tmp_path):
        """`risk_event_flatten` `_entry_lock`'u ALIR.

        Halt kurulduğu anda `_handle_entry` içinde uçuşta olan bir giriş
        HENÜZ `tracked_symbols()`'a girmemiştir. Kilit alınmazsa flatten
        "hiç pozisyon yok" der ve saniyeler sonra AKTİF HALT ALTINDA açık
        bir pozisyon kalırdı.
        """
        engine = _make_engine(tmp_path)
        started = asyncio.Event()
        release = asyncio.Event()

        async def _slow_open(**_kwargs):
            started.set()
            await release.wait()
            return _fake_position()

        engine.executor.open_position = AsyncMock(side_effect=_slow_open)
        entry_task = asyncio.create_task(
            engine.handle_event(parse_follower_event(SELL_ENTRY))
        )
        await asyncio.wait_for(started.wait(), timeout=1.0)

        flatten_task = asyncio.create_task(
            engine.risk_event_flatten(reason="haber", source="test", ttl_minutes=5)
        )
        await asyncio.sleep(0.02)
        assert not flatten_task.done(), "flatten kilide takılmadı"

        release.set()
        assert (await asyncio.wait_for(entry_task, timeout=1.0))["accepted"] is True
        result = await asyncio.wait_for(flatten_task, timeout=1.0)

        assert result["flattened"] == ["BTCUSDT"]
        assert result["errors"] == []

    async def test_close_rejection_with_flat_position_counts_as_closed(self, tmp_path):
        """-2022: TP3/SL aynı anda dolduysa emir reddi 'açık' demek DEĞİLDİR."""
        engine = _make_engine(tmp_path, positions={"BTCUSDT": _fake_position()})
        engine.client.get_position_risk = AsyncMock(
            side_effect=[{"positionAmt": -0.12}, {"positionAmt": 0.0}]
        )
        engine.client._request_with_retry = AsyncMock(
            side_effect=RuntimeError("-2022 ReduceOnly Order is rejected")
        )

        result = await engine.handle_event(parse_follower_event(EXIT_EVENT))

        assert result["accepted"] is True
        engine.exits._handle_closed.assert_awaited_once()

    async def test_close_rejection_with_open_position_is_fail_closed(self, tmp_path):
        engine = _make_engine(tmp_path, positions={"BTCUSDT": _fake_position()})
        engine.client.get_position_risk = AsyncMock(
            return_value={"positionAmt": -0.12}
        )
        engine.client._request_with_retry = AsyncMock(
            side_effect=RuntimeError("-1111 precision")
        )

        result = await engine.handle_event(parse_follower_event(EXIT_EVENT))

        assert result["accepted"] is False
        engine.exits._handle_closed.assert_not_called()
        assert "BTCUSDT" in engine.exits.tracked_symbols()


class TestDuplicateDelivery:
    """İDEMPOTANS: aynı alarm iki kez iletilirse (TV retry / köprü tekrarı).

    Köprü fire-and-forget'tir ve TradingView bir alarmı yeniden gönderebilir;
    hiçbir olay türü ikinci gelişinde İKİNCİ bir pozisyon/işlem üretmemelidir.
    """

    def _finalizing_engine(self, tmp_path, **kwargs):
        """`_handle_closed`'ı GERÇEKÇİ yap: izleme listesinden düşür."""
        engine = _make_engine(tmp_path, **kwargs)
        tracked = engine.exits._positions

        async def _closed(symbol, sp, *, forced_exit_reason=None):
            tracked.pop(symbol, None)

        engine.exits._handle_closed = AsyncMock(side_effect=_closed)
        return engine

    async def test_duplicate_entry_opens_only_one_position(self, tmp_path):
        engine = _make_engine(tmp_path)
        event = parse_follower_event(SELL_ENTRY)

        first = await engine.handle_event(event)
        second = await engine.handle_event(event)

        assert first["accepted"] is True
        assert second["accepted"] is False
        assert "aynı yönde açık pozisyon" in second["reason"]
        assert engine.executor.open_position.await_count == 1
        assert engine._reject_counters.get("already_open") == 1

    async def test_concurrent_duplicate_entries_are_serialized(self, tmp_path):
        """Aynı anda gelen iki kopya: `_entry_lock` ikinciyi kapıya çarpar."""
        engine = _make_engine(tmp_path)
        event = parse_follower_event(SELL_ENTRY)

        results = await asyncio.gather(
            engine.handle_event(event), engine.handle_event(event)
        )

        assert sorted(bool(r["accepted"]) for r in results) == [False, True]
        assert engine.executor.open_position.await_count == 1

    async def test_duplicate_reverse_signal_flips_only_once(self, tmp_path):
        """Ters sinyal iki kez gelirse ikinci artık AYNI yöndedir → ret."""
        existing = _fake_position(direction=Direction.SHORT)
        engine = self._finalizing_engine(tmp_path, positions={"BTCUSDT": existing})
        engine.client.get_position_risk = AsyncMock(
            side_effect=[
                {"positionAmt": -0.12},  # flip kapanışı için canlı miktar
                {"positionAmt": 0.0},    # kapanış doğrulaması
                {"positionAmt": 0.0},    # yeni girişten önceki borsa kapısı
            ]
        )
        engine.executor.open_position = AsyncMock(
            return_value=_fake_position(direction=Direction.LONG)
        )
        event = parse_follower_event(BUY_ENTRY)

        first = await engine.handle_event(event)
        second = await engine.handle_event(event)

        assert first["accepted"] is True and first["flipped"] is True
        assert second["accepted"] is False
        assert "aynı yönde açık pozisyon" in second["reason"]
        assert engine.exits._handle_closed.await_count == 1
        assert engine.executor.open_position.await_count == 1

    async def test_duplicate_exit_closes_only_once(self, tmp_path):
        engine = self._finalizing_engine(
            tmp_path, positions={"BTCUSDT": _fake_position()}
        )
        engine.client.get_position_risk = AsyncMock(
            side_effect=[{"positionAmt": -0.12}, {"positionAmt": 0.0}]
        )
        event = parse_follower_event(EXIT_EVENT)

        first = await engine.handle_event(event)
        second = await engine.handle_event(event)

        assert first["accepted"] is True
        assert second["accepted"] is False
        assert "izlenen pozisyon yok" in second["reason"]
        assert engine.exits._handle_closed.await_count == 1

    async def test_duplicate_terminal_hit_finalizes_only_once(self, tmp_path):
        engine = self._finalizing_engine(
            tmp_path, positions={"BTCUSDT": _fake_position()}, position_amt=0.0
        )
        event = parse_follower_event(SL_HIT)

        first = await engine.handle_event(event)
        second = await engine.handle_event(event)

        assert first["accepted"] is True
        assert second["accepted"] is False
        assert engine.exits._handle_closed.await_count == 1

    async def test_duplicate_tp_hit_is_telemetry_only(self, tmp_path):
        engine = self._finalizing_engine(
            tmp_path, positions={"BTCUSDT": _fake_position()}, position_amt=-0.08
        )
        event = parse_follower_event(TP1_HIT)

        assert (await engine.handle_event(event))["accepted"] is True
        assert (await engine.handle_event(event))["accepted"] is True
        engine.exits._handle_closed.assert_not_called()
        assert engine._event_counters["tp1"] == 2


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
        # Ücret eşiği telemetrisi (D20): TP1 ROI %2.70 < komisyon %10 →
        # yapısal negatif beklenti adayı, /follower/status'ta GÖRÜNÜR.
        assert position["tp1_roi_pct"] == pytest.approx(2.7)
        assert position["fee_roi_pct"] == pytest.approx(10.0)
        assert position["tp1_covers_fees"] is False
        assert position["sl_pct_fill"] == pytest.approx(0.054)
        # Kapı varsayılan AÇIK olmalı (bulgu 3).
        assert snapshot["sizing"]["min_tp1_fee_ratio"] == 1.0
        assert snapshot["orphan_positions"] == []

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
    # SHORT pozisyon: BE stopu (77080.0) bir BUY STOP'tur ve piyasanın
    # ÜSTÜNDE olmalıdır. Varsayılan fiyat BE'nin ALTINDADIR (77070) — yani
    # işlem ücret eşiğini geçmiş, BE fiilen konulabilir durumdadır
    # (%0.02 yerleştirme payı dahil: 77080 >= 77050 + 15.4).
    def __init__(self, live_qty: float, price: float = 77050.0):
        self.live_qty = live_qty
        self.price = price
        self.calls: list = []

    async def get_position_risk(self, symbol, force_fresh=False):
        return {"positionAmt": -self.live_qty}

    async def get_current_price(self, symbol):
        return self.price

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
    def _manager(self, live_qty, price: float = 77050.0):
        client = _ExitFakeClient(live_qty, price=price)
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

    async def test_unreachable_breakeven_never_sends_the_order(self):
        """BE piyasanın yanlış tarafındaysa emir GÖNDERİLMEZ.

        `pm._replace_stop_loss` `-2021` alırsa pozisyonu ACİL KAPATIR. Takipçide
        ücret-farkında BE mesafesi (~%0.15) TP1'den (RR1 × sl_pct) uzak olduğu
        her işlemde bu durum KURALDIR — kalan 2/3 zorla düzleştirilirdi.
        """
        # SHORT, BE=77080 (BUY STOP): piyasa 77100'de, yani stop'un ÜSTÜNDE →
        # emir anında tetiklenirdi.
        manager, client, pm = self._manager(live_qty=0.08, price=77100.0)
        sp = _fake_position(qty=0.12)
        manager.track(sp)

        await manager._step_one("BTCUSDT", sp)

        pm.replace_stop_loss.assert_not_called()
        assert sp.tp1_done is False
        assert sp.position.current_stoploss == pytest.approx(77167.77)  # eski SL
        # BULGU 9 (bilinçli değişiklik): fill KANITI artık BE denemesinden
        # ÖNCE alınır. Eskiden ulaşılamayan BE'de erken dönülüyordu ve
        # `tp1_filled` hiç işaretlenmiyordu → TP2/TP3 doğrulaması ÖLÜYDÜ.
        # Dolum bir OLGUDUR; BE'nin konulabilirliğinden bağımsızdır.
        assert sp.tp1_filled is True
        assert ("get_algo_order", 501) in client.calls

    async def test_unreachable_breakeven_warns_only_once(self):
        manager, client, pm = self._manager(live_qty=0.08, price=77100.0)
        sp = _fake_position(qty=0.12)
        manager.track(sp)

        await manager._step_one("BTCUSDT", sp)
        await manager._step_one("BTCUSDT", sp)

        warnings = [
            c for c in manager.logger.warning.call_args_list
            if "break-even seviyesi" in str(c)
        ]
        assert len(warnings) == 1

    async def test_missing_price_skips_breakeven(self):
        """Fiyat okunamadıysa 'bilinmiyor' ASLA 'konulabilir' sayılmaz."""
        manager, client, pm = self._manager(live_qty=0.08)
        client.get_current_price = AsyncMock(side_effect=RuntimeError("ağ"))
        sp = _fake_position(qty=0.12)
        sp.position.current_price = 0.0
        manager.track(sp)

        await manager._step_one("BTCUSDT", sp)

        pm.replace_stop_loss.assert_not_called()
        assert sp.tp1_done is False

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


# ---------------------------------------------------------------------------
# D20a — düşmanca inceleme regresyonları (bulgu 6, 7, 8, 9)
# ---------------------------------------------------------------------------


class TestGatesDoNotBlockExits:
    """Bulgu 9: min_score/allowlist/TF kapıları ÇIKIŞI bloklamamalı.

    Düzeltme olmadan KIRMIZI: kapılar `_dispatch`in başındaydı ve EXIT/HIT
    olayları da onlardan geçiyordu — allowlist'ten çıkarılan bir sembolün
    AÇIK pozisyonu AlgoPro'nun EXIT komutunu HİÇ görmezdi.
    """

    async def test_exit_ignores_symbol_allowlist(self, tmp_path):
        cfg = _cfg(follower_symbol_allowlist="ETHUSDT")  # BTC evren DIŞINDA
        engine = _make_engine(
            tmp_path, cfg, positions={"BTCUSDT": _fake_position()}
        )
        result = await engine.handle_event(parse_follower_event(EXIT_EVENT))
        assert result["accepted"] is True
        assert result["reason"] == "pozisyon kapatıldı"

    async def test_exit_ignores_timeframe_mismatch(self, tmp_path):
        cfg = _cfg(follower_timeframe="5")
        engine = _make_engine(
            tmp_path, cfg, positions={"BTCUSDT": _fake_position()}
        )
        result = await engine.handle_event(parse_follower_event(EXIT_EVENT))
        assert result["accepted"] is True

    async def test_hit_ignores_symbol_allowlist(self, tmp_path):
        cfg = _cfg(follower_symbol_allowlist="ETHUSDT")
        engine = _make_engine(
            tmp_path, cfg, positions={"BTCUSDT": _fake_position()}
        )
        result = await engine.handle_event(parse_follower_event(SL_HIT))
        assert result["accepted"] is True

    async def test_entry_is_still_gated(self, tmp_path):
        cfg = _cfg(follower_symbol_allowlist="ETHUSDT")
        engine = _make_engine(tmp_path, cfg)
        result = await engine.handle_event(parse_follower_event(SELL_ENTRY))
        assert result["accepted"] is False
        # D20b: TEK ret adı (köprü yolu ile aynı); insan-okur metin `detail`de.
        assert result["reason"] == "symbol_not_in_follower_universe"
        assert "evren" in result["detail"]


class TestStaleSignalGates:
    """Bulgu 6: bayat olay/fiyat ile giriş yok."""

    async def test_event_age_blocks_entry(self, tmp_path):
        engine = _make_engine(tmp_path)
        stale = time.monotonic() - 60.0
        result = await engine.handle_event(
            parse_follower_event(SELL_ENTRY), received_monotonic=stale
        )
        assert result["accepted"] is False
        assert "bayat" in result["reason"]
        assert engine._reject_counters["event_age"] == 1
        engine.executor.open_position.assert_not_called()

    async def test_fresh_event_passes_the_age_gate(self, tmp_path):
        engine = _make_engine(tmp_path)
        result = await engine.handle_event(
            parse_follower_event(SELL_ENTRY), received_monotonic=time.monotonic()
        )
        assert result["accepted"] is True

    async def test_age_gate_can_be_disabled(self, tmp_path):
        engine = _make_engine(tmp_path, _cfg(follower_max_event_age_sec=0.0))
        result = await engine.handle_event(
            parse_follower_event(SELL_ENTRY),
            received_monotonic=time.monotonic() - 600.0,
        )
        assert result["accepted"] is True

    async def test_signal_drift_blocks_entry(self, tmp_path):
        # sl_pct(mesaj) ≈ %0.0541 → sınır ≈ %0.027 (≈ 20.8 birim); 30 birim aşar.
        engine = _make_engine(tmp_path, live_price=77126.08 - 30.0)
        result = await engine.handle_event(parse_follower_event(SELL_ENTRY))
        assert result["accepted"] is False
        assert "sapma" in result["reason"]
        assert engine._reject_counters["signal_drift"] == 1
        engine.executor.open_position.assert_not_called()

    async def test_stop_already_passed_blocks_entry(self, tmp_path):
        # SHORT stopu 77167.77; canlı fiyat onun ÜSTÜNDE → tez ölü.
        # Sapma kapısı BİLİNÇLİ olarak gevşetildi: varsayılan (SL mesafesinin
        # %50'si) zaten daha erken tetiklenir; bu test ikinci savunma
        # katmanının (taraf kontrolü) tek başına da çalıştığını kanıtlar.
        engine = _make_engine(
            tmp_path, _cfg(follower_max_signal_drift_pct=1.0), live_price=77200.0
        )
        result = await engine.handle_event(parse_follower_event(SELL_ENTRY))
        assert result["accepted"] is False
        assert "yanlış tarafında" in result["reason"]
        assert engine._reject_counters["stop_already_passed"] == 1
        engine.executor.open_position.assert_not_called()


class TestTerminalHitClosesOpenPosition:
    """Bulgu 7: SL/TP3 HIT + borsada AÇIK pozisyon = telemetri DEĞİL, ARIZA."""

    def _engine_with_open_position(self, tmp_path, amounts):
        engine = _make_engine(
            tmp_path, positions={"BTCUSDT": _fake_position()}
        )
        engine.client.get_position_risk = AsyncMock(
            side_effect=[{"positionAmt": amount} for amount in amounts]
        )
        return engine

    async def test_sl_hit_with_open_position_closes_it(self, tmp_path):
        # SHORT pozisyon: borsada positionAmt NEGATİFTİR.
        engine = self._engine_with_open_position(tmp_path, [-0.12, -0.12, 0.0])
        result = await engine.handle_event(parse_follower_event(SL_HIT))
        assert result["accepted"] is True
        engine.client._request_with_retry.assert_awaited()
        params = engine.client._request_with_retry.await_args.kwargs["params"]
        assert params["reduceOnly"] == "true"
        assert params["side"] == "BUY"  # SHORT pozisyonu kapatır
        engine.exits._handle_closed.assert_awaited()
        assert (
            engine.exits._handle_closed.await_args.kwargs["forced_exit_reason"]
            == "ALGOPRO_SL"
        )

    async def test_tp3_hit_with_open_position_closes_remainder(self, tmp_path):
        engine = self._engine_with_open_position(tmp_path, [-0.04, -0.04, 0.0])
        tp3_hit = "🏆 TP3 HIT | BINANCE:BTCUSDT | TF: 1 | Price: 77063.54"
        result = await engine.handle_event(parse_follower_event(tp3_hit))
        assert result["accepted"] is True
        assert (
            engine.exits._handle_closed.await_args.kwargs["forced_exit_reason"]
            == "ALGOPRO_TP3"
        )

    async def test_unverified_close_is_fail_closed(self, tmp_path):
        engine = _make_engine(
            tmp_path, positions={"BTCUSDT": _fake_position()}, position_amt=0.12
        )
        result = await engine.handle_event(parse_follower_event(SL_HIT))
        assert result["accepted"] is False
        assert "DOĞRULANAMADI" in result["reason"]
        engine.exits._handle_closed.assert_not_awaited()

    async def test_non_terminal_hit_repairs_missing_tp_orders(self, tmp_path):
        engine = _make_engine(
            tmp_path, positions={"BTCUSDT": _fake_position()}, position_amt=0.08
        )
        result = await engine.handle_event(parse_follower_event(TP1_HIT))
        assert result["accepted"] is True
        engine.exits.ensure_tp_orders.assert_awaited_once()

    async def test_identity_change_is_not_reported_as_accepted(self, tmp_path):
        """Bulgu 9: `_handle_closed` çağrılmadıysa `accepted` TRUE OLAMAZ."""
        engine = _make_engine(
            tmp_path, positions={"BTCUSDT": _fake_position()}, position_amt=0.0
        )
        original = engine.exits._positions["BTCUSDT"]

        async def _swap(symbol, force_fresh=False):
            # Bu await sırasında başka bir yol pozisyonu değiştirdi.
            engine.exits._positions["BTCUSDT"] = _fake_position()
            assert engine.exits._positions["BTCUSDT"] is not original
            return {"positionAmt": 0.0}

        engine.client.get_position_risk = _swap
        result = await engine.handle_event(parse_follower_event(SL_HIT))
        assert result["accepted"] is False
        engine.exits._handle_closed.assert_not_awaited()


class TestOrphanPositions:
    """Bulgu 8: borsada AÇIK ama izlenmeyen pozisyon = ENTRY-HALT + CRITICAL."""

    async def test_untracked_open_position_latches_entry_halt(self, tmp_path):
        engine = _make_engine(
            tmp_path, all_positions=[{"symbol": "ETHUSDT", "positionAmt": "0.5"}]
        )
        orphans = await engine._check_orphans()
        assert orphans == ["ETHUSDT"]
        assert engine._entry_halted is True
        assert engine._entries_ready() is False
        assert engine.snapshot()["orphan_positions"] == ["ETHUSDT"]

    async def test_tracked_position_is_not_an_orphan(self, tmp_path):
        engine = _make_engine(
            tmp_path,
            positions={"BTCUSDT": _fake_position()},
            all_positions=[{"symbol": "BTCUSDT", "positionAmt": "0.12"}],
        )
        assert await engine._check_orphans() == []
        assert engine._entry_halted is False

    async def test_closing_position_is_not_an_orphan(self, tmp_path):
        engine = _make_engine(
            tmp_path, all_positions=[{"symbol": "BTCUSDT", "positionAmt": "0.12"}]
        )
        engine.exits._closing.add("BTCUSDT")
        assert await engine._check_orphans() == []
        assert engine._entry_halted is False

    async def test_in_flight_entry_is_not_an_orphan(self, tmp_path):
        """Kilit tutuluyorsa uçuşta bir giriş var; henüz `track` edilmemiştir."""
        engine = _make_engine(
            tmp_path, all_positions=[{"symbol": "ETHUSDT", "positionAmt": "0.5"}]
        )
        async with engine._entry_lock:
            assert await engine._check_orphans() == []
        assert engine._entry_halted is False

    async def test_recovery_not_ready_skips_the_check(self, tmp_path):
        engine = _make_engine(
            tmp_path, all_positions=[{"symbol": "ETHUSDT", "positionAmt": "0.5"}]
        )
        engine._recovery_ready = False
        assert await engine._check_orphans() == []
        assert engine._entry_halted is False

    async def test_stale_snapshot_alone_never_latches(self, tmp_path):
        """Geri alınamaz karar (entry-halt) TAZE okumayla doğrulanır."""
        engine = _make_engine(tmp_path)
        engine.client.get_all_positions = AsyncMock(
            side_effect=[
                [{"symbol": "ETHUSDT", "positionAmt": "0.5"}],  # bayat görüntü
                [],  # taze: pozisyon yok
            ]
        )
        assert await engine._check_orphans() == []
        assert engine._entry_halted is False

    async def test_flatten_also_closes_orphans(self, tmp_path):
        engine = _make_engine(
            tmp_path, all_positions=[{"symbol": "ETHUSDT", "positionAmt": "0.5"}]
        )
        result = await engine.risk_event_flatten(
            reason="test", source="test", ttl_minutes=5
        )
        assert result["flattened"] == ["ETHUSDT"]
        assert result["errors"] == []
        params = engine.client._request_with_retry.await_args.kwargs["params"]
        assert params["symbol"] == "ETHUSDT"
        assert params["side"] == "SELL"  # LONG yetimi kapatır
        assert params["reduceOnly"] == "true"

    async def test_flatten_reports_unverified_orphan_close(self, tmp_path):
        engine = _make_engine(
            tmp_path,
            position_amt=0.5,  # kapanış doğrulanamıyor
            all_positions=[{"symbol": "ETHUSDT", "positionAmt": "0.5"}],
        )
        result = await engine.risk_event_flatten(
            reason="test", source="test", ttl_minutes=5
        )
        assert result["flattened"] == []
        assert any("yetim" in message for message in result["errors"])


class TestLadderStateFromExchangeFills:
    """Bulgu 9: TP2/TP3 doğrulaması `tp1_done`un ARKASINDA DEĞİL.

    `tp1_done` "stop break-even'e taşındı" demektir ve ücret-farkında BE
    ulaşılamadığı her işlemde (D20 "ücret eşiği") YAPISAL OLARAK False kalır
    → eski kodda TP2/TP3 dolumu HİÇ doğrulanmazdı. Merdiven artık BORSA
    DOLUMUNA (`tp1_filled`) bağlıdır.
    """

    def _manager(self, live_qty, price: float = 77050.0):
        client = _ExitFakeClient(live_qty, price=price)
        pm = SimpleNamespace(replace_stop_loss=AsyncMock(return_value=True))
        tracker = SimpleNamespace(record_close=AsyncMock(), close_seq=0)
        manager = FollowerExitManager(client, pm, tracker, _cfg())
        manager.logger = MagicMock()
        return manager, client, pm

    async def test_tp2_is_verified_even_when_breakeven_is_unreachable(self):
        # price=77100 → BE (77080) piyasanın YANLIŞ tarafında → tp1_done False.
        manager, client, pm = self._manager(live_qty=0.04, price=77100.0)
        sp = _fake_position(qty=0.12)
        manager.track(sp)

        await manager._step_one("BTCUSDT", sp)

        assert sp.tp1_done is False  # BE konulamadı (bilinçli)
        assert sp.tp1_filled is True  # ama DOLUM bir olgudur
        assert sp.tp2_done is True  # ve merdiven ilerledi

    async def test_tp3_follows_tp2(self):
        manager, client, pm = self._manager(live_qty=0.001, price=77100.0)
        sp = _fake_position(qty=0.12)
        sp.tp1_filled = True
        sp.tp2_done = True
        manager.track(sp)

        await manager._step_one("BTCUSDT", sp)

        assert sp.tp3_done is True


class TestMissingTpRepair:
    """Bulgu 7/9: eksik TP bacakları yeniden konur (takipçide TP = ÇIKIŞ)."""

    def _manager(self, *, algo_orders, live_qty=0.12, price=77100.0):
        client = _ExitFakeClient(live_qty, price=price)
        client.get_open_algo_orders = AsyncMock(return_value=algo_orders)
        client.place_take_profit = AsyncMock(return_value={"algoId": 777})
        pm = SimpleNamespace(replace_stop_loss=AsyncMock(return_value=True))
        tracker = SimpleNamespace(record_close=AsyncMock(), close_seq=0)
        manager = FollowerExitManager(client, pm, tracker, _cfg())
        manager.logger = MagicMock()
        return manager, client

    async def test_missing_leg_is_replaced(self):
        # TP1/TP2 canlı, TP3 (algoId 503) borsada YOK.
        manager, client = self._manager(
            algo_orders=[{"algoId": 501}, {"algoId": 502}]
        )
        sp = _fake_position(qty=0.12)
        placed = await manager.ensure_tp_orders("BTCUSDT", sp)
        assert placed == 1
        assert client.place_take_profit.await_args.kwargs["stop_price"] == (
            pytest.approx(77063.54)
        )
        assert sp.plan.tp3_algo_id == "777"
        assert manager.tp_repair_snapshot()["replaced"] == 1

    async def test_live_legs_are_not_duplicated(self):
        manager, client = self._manager(
            algo_orders=[{"algoId": 501}, {"algoId": 502}, {"algoId": 503}]
        )
        sp = _fake_position(qty=0.12)
        assert await manager.ensure_tp_orders("BTCUSDT", sp) == 0
        client.place_take_profit.assert_not_called()

    async def test_filled_legs_are_not_replaced(self):
        manager, client = self._manager(algo_orders=[])
        sp = _fake_position(qty=0.12)
        sp.tp1_filled = True
        sp.tp2_done = True
        sp.tp3_done = True
        assert await manager.ensure_tp_orders("BTCUSDT", sp) == 0
        client.place_take_profit.assert_not_called()

    async def test_wrong_side_leg_is_never_replaced(self):
        """Tetik fiyatı fiyatın gerisindeyse emir ANINDA tetiklenirdi."""
        # SHORT, fiyat 77090 → TP1 (77105.23) artık fiyatın ÜSTÜNDE (geride).
        manager, client = self._manager(algo_orders=[], price=77090.0)
        sp = _fake_position(qty=0.12)
        placed = await manager.ensure_tp_orders("BTCUSDT", sp)
        # TP1 atlandı; TP2/TP3 (77084.39 / 77063.54) hâlâ ileride.
        assert placed == 2
        assert manager.tp_repair_snapshot()["skipped_wrong_side"] == 1

    async def test_total_quantity_never_exceeds_the_live_position(self):
        """Reduce-only TP toplamı canlı miktarı AŞAMAZ (-2022 riski)."""
        manager, client = self._manager(algo_orders=[], live_qty=0.05)
        sp = _fake_position(qty=0.12)  # her bacak 0.04
        placed = await manager.ensure_tp_orders("BTCUSDT", sp)
        sizes = [
            call.kwargs["quantity"]
            for call in client.place_take_profit.await_args_list
        ]
        assert placed >= 1
        assert sum(sizes) <= 0.05 + 1e-9

    async def test_missing_price_blocks_repair(self):
        manager, client = self._manager(algo_orders=[])
        client.get_current_price = AsyncMock(return_value=None)
        sp = _fake_position(qty=0.12)
        assert await manager.ensure_tp_orders("BTCUSDT", sp) == 0
        client.place_take_profit.assert_not_called()
        assert manager.tp_repair_snapshot()["no_price"] == 1

    async def test_flat_position_is_never_repaired(self):
        manager, client = self._manager(algo_orders=[], live_qty=0.0)
        sp = _fake_position(qty=0.12)
        assert await manager.ensure_tp_orders("BTCUSDT", sp) == 0
        client.place_take_profit.assert_not_called()


class TestFeeGateIsRecorded:
    """Bulgu 3: kapıda reddedilen giriş DEFTERE yazılır + sayaçta görünür."""

    async def test_rejected_entry_is_written_to_the_calibration_ledger(
        self, tmp_path
    ):
        import json

        ledger = tmp_path / "follower_levels.jsonl"
        engine = _make_engine(
            tmp_path, _cfg(follower_levels_log_path=str(ledger))
        )
        engine.executor.open_position = AsyncMock(
            side_effect=FollowerRejected(
                "TP1 ROI komisyonun altında", code="fee_gate"
            )
        )
        result = await engine.handle_event(parse_follower_event(SELL_ENTRY))

        assert result["accepted"] is False
        assert engine._reject_counters["fee_gate"] == 1
        assert engine.snapshot()["reject_counters"]["fee_gate"] == 1
        rows = [json.loads(line) for line in ledger.read_text().splitlines()]
        assert rows[-1]["rejected"] == "fee_gate"
        assert "komisyon" in rows[-1]["rejected_reason"]

    async def test_accepted_entry_has_no_rejection_field(self, tmp_path):
        import json

        ledger = tmp_path / "follower_levels.jsonl"
        engine = _make_engine(
            tmp_path, _cfg(follower_levels_log_path=str(ledger))
        )
        await engine.handle_event(parse_follower_event(SELL_ENTRY))
        rows = [json.loads(line) for line in ledger.read_text().splitlines()]
        assert "rejected" not in rows[-1]
