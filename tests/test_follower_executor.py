"""FollowerExecutor — korumalı açılış disiplini (D20).

Kanıtlanmak istenen sözleşme (scalper'ın `try_open` disiplininin AYNISI):
  * emirden ÖNCE borsa filtresi doğrulaması ve margin/leverage ayarı;
  * MARKET dolumdan hemen sonra SL; SL kurulamazsa pozisyon PositionManager
    tarafından kapatılır, defter satırı yazılır, cooldown başlar, TP GÖNDERİLMEZ;
  * TP başarısızlığı pozisyonu İPTAL ETTİRMEZ (SL zaten var);
  * 3 parça reduce-only TP, artık SON parçada;
  * borsa dilimi okunamazsa giriş YAPILMAZ (fail-closed).

GERÇEK AĞ/DB YOK: client/pm/tracker sahte; çağrı SIRASI kaydedilir.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.models.waiting_signal  # noqa: F401  (SQLAlchemy mapper zinciri)
from src.strategies.follower.executor import FollowerExecutor, FollowerPosition
from src.strategies.follower.levels import resolve_levels
from src.strategies.follower.parser import parse_follower_event
from src.strategies.follower.types import (
    FollowerRejected,
    LeverageBracket,
    MessageLevels,
)
from src.strategies.scalper.types import Direction

FREE_BRACKET = [LeverageBracket(125, 0.0, 0.0, float("inf"))]

SELL_ENTRY = (
    "🔴 SELL | BINANCE:BTCUSDT | TF: 1 | Price: 77126.08 | TQI: .45 | Score: 8 "
    "| SL: 77167.77 | TP1: 77105.23 | TP2: 77084.39 | TP3: 77063.54"
)


def _cfg(**overrides):
    base = dict(
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
        follower_cooldown_sec=60.0,
        scalper_breakeven_buffer_pct=0.05,
        scalper_taker_fee_pct=0.05,
        scalper_maker_fee_pct=0.02,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class _FakeClient:
    def __init__(self, step=0.001):
        self.step = step
        self.calls: list = []
        self.tp_orders: list = []
        self.tp_error: Exception | None = None
        self.filled_qty = 0.129
        self.entry_price = 77126.08

    async def get_symbol_filters(self, symbol):
        return {"stepSize": self.step, "minQty": 0.001, "minNotional": 5}

    async def quantize_quantity(self, symbol, quantity):
        self.calls.append("quantize_quantity")
        return round(quantity, 3)

    async def validate_order(self, symbol, quantity, price):
        self.calls.append("validate_order")

    async def set_margin_type(self, symbol, margin_type="ISOLATED"):
        self.calls.append("set_margin_type")

    async def set_leverage(self, symbol, leverage):
        self.calls.append(f"set_leverage:{leverage}")

    async def open_market_order(self, symbol, side, quantity):
        self.calls.append(f"open_market_order:{side}:{quantity}")
        return {"orderId": 111}

    async def place_take_profit(self, symbol, side, stop_price, quantity):
        self.calls.append("place_take_profit")
        if self.tp_error is not None:
            raise self.tp_error
        self.tp_orders.append({"price": stop_price, "qty": quantity, "side": side})
        return {"algoId": 600 + len(self.tp_orders)}

    async def get_user_commission_rate(self, symbol):
        raise RuntimeError("komisyon okunamadı")  # muhafazakâr fallback


class _FakePm:
    def __init__(self, sl_ok=True):
        self.sl_ok = sl_ok
        self.calls: list = []

    async def resolve_fill(self, symbol, entry_order):
        self.calls.append("resolve_fill")
        return 77126.08, 0.129

    async def place_stop_loss_or_close(self, **kwargs):
        self.calls.append("place_stop_loss_or_close")
        if not self.sl_ok:
            return None
        return {"algoId": 500, "effectiveStopPrice": kwargs["stop_price"]}


def _make_executor(cfg=None, *, sl_ok=True, brackets=FREE_BRACKET, step=0.001):
    client = _FakeClient(step=step)
    pm = _FakePm(sl_ok=sl_ok)
    tracker = SimpleNamespace(
        record_open=AsyncMock(return_value=42),
        record_failed_execution=AsyncMock(return_value=7),
    )
    bracket_cache = SimpleNamespace(get=AsyncMock(return_value=brackets))
    executor = FollowerExecutor(client, pm, tracker, cfg or _cfg(), bracket_cache)
    executor.logger = MagicMock()
    return executor, client, pm, tracker


def _levels(cfg=None):
    return resolve_levels(
        entry=77126.08,
        direction=Direction.SHORT,
        message=MessageLevels(
            sl=77167.77, tp1=77105.23, tp2=77084.39, tp3=77063.54
        ),
        atr_value=None,
        cfg=cfg or _cfg(),
    )


class TestHappyPath:
    async def test_full_protected_open(self):
        executor, client, pm, tracker = _make_executor()
        event = parse_follower_event(SELL_ENTRY)
        position = await executor.open_position(
            event=event, levels=_levels(), equity_usdt=1000.0
        )
        assert isinstance(position, FollowerPosition)
        assert position.trade_id == 42

        # Emirden ÖNCE doğrulama + margin/leverage; SL emirden HEMEN SONRA.
        assert client.calls.index("validate_order") < client.calls.index(
            "set_margin_type"
        )
        market_index = next(
            i for i, c in enumerate(client.calls) if c.startswith("open_market_order")
        )
        assert client.calls.index("set_margin_type") < market_index
        assert pm.calls == ["resolve_fill", "place_stop_loss_or_close"]
        assert client.calls.count("place_take_profit") == 3

    async def test_three_equal_tp_parts_with_remainder_last(self):
        executor, client, pm, tracker = _make_executor()
        await executor.open_position(
            event=parse_follower_event(SELL_ENTRY),
            levels=_levels(),
            equity_usdt=1000.0,
        )
        quantities = [order["qty"] for order in client.tp_orders]
        assert quantities[0] == quantities[1] == pytest.approx(0.043)
        assert quantities[2] == pytest.approx(0.043)
        assert sum(quantities) == pytest.approx(0.129)
        prices = [order["price"] for order in client.tp_orders]
        assert prices == pytest.approx([77105.23, 77084.39, 77063.54])
        # SHORT pozisyonu kapatan taraf BUY
        assert {order["side"] for order in client.tp_orders} == {"BUY"}

    async def test_ledger_row_carries_sizing(self):
        executor, client, pm, tracker = _make_executor()
        await executor.open_position(
            event=parse_follower_event(SELL_ENTRY),
            levels=_levels(),
            equity_usdt=1000.0,
        )
        kwargs = tracker.record_open.await_args.kwargs
        assert kwargs["leverage"] == 100
        assert kwargs["tp3_algo_id"] == "603"
        assert kwargs["entry_order_id"] == "111"
        reason = kwargs["signal"].reason
        assert reason.startswith("algopro:entry")
        assert "lev=100" in reason
        assert "sl_roi=" in reason
        assert "margin=" in reason

    async def test_leverage_sent_to_exchange_matches_plan(self):
        executor, client, pm, tracker = _make_executor()
        await executor.open_position(
            event=parse_follower_event(SELL_ENTRY),
            levels=_levels(),
            equity_usdt=1000.0,
        )
        assert "set_leverage:100" in client.calls

    async def test_exit_plan_holds_three_targets(self):
        executor, client, pm, tracker = _make_executor()
        position = await executor.open_position(
            event=parse_follower_event(SELL_ENTRY),
            levels=_levels(),
            equity_usdt=1000.0,
        )
        assert position.plan.tp1_price == pytest.approx(77105.23)
        assert position.plan.tp2_price == pytest.approx(77084.39)
        assert position.plan.tp3_price == pytest.approx(77063.54)
        assert position.plan.tp3_algo_id == "603"
        # BE seviyesi ücret-farkındadır: SHORT'ta girişin ALTINDA olmalı.
        assert position.plan.breakeven_price < position.position.entry_price
        assert position.plan.chandelier_atr_mult == 0.0  # trailing YOK


class TestProtectionFailure:
    async def test_sl_failure_records_and_skips_tps(self):
        executor, client, pm, tracker = _make_executor(sl_ok=False)
        position = await executor.open_position(
            event=parse_follower_event(SELL_ENTRY),
            levels=_levels(),
            equity_usdt=1000.0,
        )
        assert position is None
        assert "place_take_profit" not in client.calls
        tracker.record_open.assert_not_called()
        tracker.record_failed_execution.assert_awaited_once()
        notes = tracker.record_failed_execution.await_args.kwargs["notes"]
        assert "follower_initial_sl_failed" in notes
        assert "exit_fill=unverified" in notes
        # Aynı sembole hemen yeniden girilmez.
        assert executor.is_entry_blocked("BTCUSDT") is True

    async def test_tp_failure_does_not_cancel_position(self):
        executor, client, pm, tracker = _make_executor()
        client.tp_error = RuntimeError("borsa reddetti")
        position = await executor.open_position(
            event=parse_follower_event(SELL_ENTRY),
            levels=_levels(),
            equity_usdt=1000.0,
        )
        assert position is not None
        assert position.plan.tp1_algo_id is None
        tracker.record_open.assert_awaited_once()


class TestFailClosedGates:
    async def test_missing_bracket_blocks_entry(self):
        executor, client, pm, tracker = _make_executor(brackets=[])
        with pytest.raises(FollowerRejected) as exc:
            await executor.open_position(
                event=parse_follower_event(SELL_ENTRY),
                levels=_levels(),
                equity_usdt=1000.0,
            )
        assert exc.value.code == "no_bracket"
        assert not any(c.startswith("open_market_order") for c in client.calls)

    async def test_position_too_small_to_split_blocks_entry(self):
        # stepSize kaba: 3 parçanın ilk ikisi sıfıra yuvarlanır.
        executor, client, pm, tracker = _make_executor(step=0.1)
        with pytest.raises(FollowerRejected) as exc:
            await executor.open_position(
                event=parse_follower_event(SELL_ENTRY),
                levels=_levels(),
                equity_usdt=1000.0,
            )
        assert exc.value.code == "split"
        assert not any(c.startswith("open_market_order") for c in client.calls)

    async def test_validation_error_blocks_entry(self):
        executor, client, pm, tracker = _make_executor()

        async def _boom(symbol, quantity, price):
            raise RuntimeError("minNotional")

        client.validate_order = _boom
        with pytest.raises(FollowerRejected) as exc:
            await executor.open_position(
                event=parse_follower_event(SELL_ENTRY),
                levels=_levels(),
                equity_usdt=1000.0,
            )
        assert exc.value.code == "validate"
        assert not any(c.startswith("open_market_order") for c in client.calls)

    async def test_zero_fill_returns_none(self):
        executor, client, pm, tracker = _make_executor()

        async def _no_fill(symbol, entry_order):
            return 77126.08, 0.0

        pm.resolve_fill = _no_fill
        position = await executor.open_position(
            event=parse_follower_event(SELL_ENTRY),
            levels=_levels(),
            equity_usdt=1000.0,
        )
        assert position is None
        tracker.record_open.assert_not_called()


class TestCooldown:
    def test_cooldown_expires(self, monkeypatch):
        executor, *_ = _make_executor(_cfg(follower_cooldown_sec=0.0))
        executor.start_cooldown("BTCUSDT")
        assert executor.is_entry_blocked("BTCUSDT") is False  # 0 = kapalı

    def test_cooldown_snapshot(self):
        executor, *_ = _make_executor()
        executor.start_cooldown("BTCUSDT")
        rows = executor.cooldown_snapshot()
        assert rows[0]["symbol"] == "BTCUSDT"
        assert rows[0]["remaining_seconds"] <= 60.0

    def test_longer_cooldown_not_shortened(self):
        """UZUN OLAN KAZANIR — scalper `_set_cooldown` ile aynı ilke."""
        import time as _time

        executor, *_ = _make_executor()
        far_future = _time.time() + 3600.0
        executor._cooldowns["BTCUSDT"] = far_future
        executor.start_cooldown("BTCUSDT")  # 60 sn — kısaltmamalı
        assert executor._cooldowns["BTCUSDT"] == far_future
