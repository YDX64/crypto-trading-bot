"""Fee-aware break-even, exact TP fill and failed-protection regressions."""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import src.models.waiting_signal  # noqa: F401 - SQLAlchemy mapper setup
import src.strategies.scalper.tracker as tracker_module
from src.strategies.scalper.executor import ScalpExecutor
from src.strategies.scalper.exits import ExitManager
from src.strategies.scalper.tracker import ScalpTracker
from src.strategies.scalper.types import (
    Direction,
    Regime,
    ScalpSignal,
    fee_aware_breakeven_price,
)


def _cfg(**overrides):
    values = {
        "scalper_entry_mode": "maker",
        "scalper_maker_fee_pct": 0.02,
        "scalper_taker_fee_pct": 0.05,
        "scalper_breakeven_buffer_pct": 0.05,
        "scalper_leverage": 10,
        "scalper_tp1_roi": 20.0,
        "scalper_tp1_fraction": 0.4,
        "scalper_tp2_roi": 50.0,
        "scalper_tp2_fraction": 0.3,
        "scalper_chandelier_atr_mult": 2.5,
        "scalper_chandelier_atr_period": 14,
        "scalper_protection_failure_cooldown_minutes": 60,
        "scalper_virtual_capital_usdt": 0.0,
        "scalper_virtual_capital_start_trade_id": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _signal(direction=Direction.LONG):
    return ScalpSignal(
        strategy="C",
        symbol="BTCUSDT",
        direction=direction,
        entry_price=100.0,
        stop_price=99.0 if direction == Direction.LONG else 101.0,
        reason="fee-safety-test",
        regime=Regime.UP,
        atr_5m=1.0,
    )


def _manager(client, pm=None, cfg=None):
    return ExitManager(
        client=client,
        pm=pm or SimpleNamespace(),
        tracker=SimpleNamespace(),
        cfg=cfg or _cfg(),
        kline_fetch=AsyncMock(return_value=[]),
    )


def _sp(*, current_stop=99.0, tp1_done=False, tp2_done=False):
    return SimpleNamespace(
        signal=SimpleNamespace(direction=Direction.LONG),
        position=SimpleNamespace(quantity=1.0, current_stoploss=current_stop),
        plan=SimpleNamespace(
            breakeven_price=100.120060030015,
            tp1_price=102.0,
            tp1_quantity=0.4,
            tp2_quantity=0.3,
            runner_floor_price=102.0,
            tp1_algo_id="11",
            tp2_algo_id="12",
        ),
        tp1_done=tp1_done,
        tp2_done=tp2_done,
        trailing_active=tp1_done or tp2_done,
    )


@pytest.mark.asyncio
async def test_failed_execution_close_advances_tracker_close_seq(monkeypatch):
    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def add(self, trade):
            trade.id = 17

        async def commit(self):
            return None

    monkeypatch.setattr(tracker_module, "AsyncSessionLocal", lambda: _Session())
    tracker = ScalpTracker()

    trade_id = await tracker.record_failed_execution(
        signal=_signal(),
        entry_price=100.0,
        exit_price=99.8,
        quantity=1.0,
        leverage=10,
        realized_pnl=-0.27,
        pnl_source="binance_account_trades_net",
    )

    assert trade_id == 17
    assert tracker.close_seq == 1


@pytest.mark.asyncio
async def test_failed_execution_does_not_advance_close_seq_when_commit_fails(
    monkeypatch,
):
    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def add(self, trade):
            trade.id = 17

        async def commit(self):
            raise RuntimeError("commit failed")

    monkeypatch.setattr(tracker_module, "AsyncSessionLocal", lambda: _Session())
    tracker = ScalpTracker()

    with pytest.raises(RuntimeError, match="commit failed"):
        await tracker.record_failed_execution(
            signal=_signal(),
            entry_price=100.0,
            exit_price=99.8,
            quantity=1.0,
            leverage=10,
            realized_pnl=-0.27,
            pnl_source="binance_account_trades_net",
        )

    assert tracker.close_seq == 0


@pytest.mark.parametrize("direction", [Direction.LONG, Direction.SHORT])
def test_fee_aware_breakeven_satisfies_exact_net_zero_equation(direction):
    entry = 100.0
    entry_rate = 0.0002
    exit_rate = 0.0005
    buffer_rate = 0.0005
    price = fee_aware_breakeven_price(
        entry,
        direction,
        entry_rate,
        exit_rate,
        buffer_rate * 100.0,
    )

    if direction == Direction.LONG:
        net = price - entry - entry * entry_rate - price * exit_rate - entry * buffer_rate
        assert price > entry
    else:
        net = entry - price - entry * entry_rate - price * exit_rate - entry * buffer_rate
        assert price < entry
    assert net == pytest.approx(0.0, abs=1e-12)


@pytest.mark.asyncio
async def test_actual_commission_selects_maker_entry_and_taker_exit():
    client = SimpleNamespace(
        get_user_commission_rate=AsyncMock(
            return_value={
                "makerCommissionRate": "0.00017",
                "takerCommissionRate": "0.00043",
            }
        )
    )
    executor = ScalpExecutor(client, SimpleNamespace(), SimpleNamespace(), _cfg())

    entry_rate, exit_rate, source = await executor._resolve_commission_rates("BTCUSDT")

    assert entry_rate == pytest.approx(0.00017)
    assert exit_rate == pytest.approx(0.00043)
    assert source == "binance_user_commission"


@pytest.mark.asyncio
async def test_commission_lookup_failure_uses_higher_config_rate_for_both_legs():
    client = SimpleNamespace(
        get_user_commission_rate=AsyncMock(side_effect=RuntimeError("timeout"))
    )
    executor = ScalpExecutor(client, SimpleNamespace(), SimpleNamespace(), _cfg())

    entry_rate, exit_rate, source = await executor._resolve_commission_rates("BTCUSDT")

    assert entry_rate == pytest.approx(0.0005)
    assert exit_rate == pytest.approx(0.0005)
    assert source == "config_conservative"


@pytest.mark.asyncio
async def test_quantity_reduction_alone_does_not_activate_tp1_or_breakeven():
    client = SimpleNamespace(
        get_algo_order=AsyncMock(return_value={"algoStatus": "FINISHED", "actualOrderId": ""}),
        get_account_trades=AsyncMock(),
    )
    pm = SimpleNamespace(replace_stop_loss=AsyncMock(return_value=True))
    manager = _manager(client, pm)
    position = _sp(current_stop=99.0)

    await manager._check_tp1("BTCUSDT", position, live_qty=0.5)

    assert position.tp1_done is False
    assert position.trailing_active is False
    pm.replace_stop_loss.assert_not_awaited()
    client.get_account_trades.assert_not_awaited()


@pytest.mark.asyncio
async def test_exact_tp1_fill_activates_state_without_loosening_tighter_stop():
    client = SimpleNamespace(
        get_algo_order=AsyncMock(
            return_value={"actualOrderId": "101", "quantity": "0.4"}
        ),
        get_account_trades=AsyncMock(
            return_value=[{"orderId": 101, "qty": "0.4"}]
        ),
    )
    pm = SimpleNamespace(replace_stop_loss=AsyncMock(return_value=True))
    manager = _manager(client, pm)
    position = _sp(current_stop=101.0)

    await manager._check_tp1("BTCUSDT", position, live_qty=0.5)

    assert position.tp1_done is True
    assert position.trailing_active is True
    assert position.position.current_stoploss == pytest.approx(101.0)
    pm.replace_stop_loss.assert_not_awaited()


@pytest.mark.asyncio
async def test_exact_tp2_fill_ratchets_runner_to_fixed_tp1_floor():
    client = SimpleNamespace(
        get_algo_order=AsyncMock(
            return_value={"actualOrderId": "202", "quantity": "0.3"}
        ),
        get_account_trades=AsyncMock(
            return_value=[{"orderId": 202, "qty": "0.3"}]
        ),
    )
    pm = SimpleNamespace(replace_stop_loss=AsyncMock(return_value=True))
    manager = _manager(client, pm)
    position = _sp(current_stop=100.2, tp1_done=True)

    await manager._check_tp2("BTCUSDT", position, live_qty=0.3)

    assert position.tp2_done is True
    assert position.position.current_stoploss == pytest.approx(102.0)
    pm.replace_stop_loss.assert_awaited_once_with(position.position, 102.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", [Direction.LONG, Direction.SHORT])
async def test_rounded_small_tp1_fill_activates_breakeven(direction):
    # ETH 0.007 * 40% = 0.0028, but LOT_SIZE 0.001 submits 0.002.
    # The old 90%-of-raw-fraction hint rejected the actual remainder 0.005.
    client = SimpleNamespace(
        quantize_quantity=AsyncMock(return_value=0.002),
        get_algo_order=AsyncMock(
            return_value={"actualOrderId": "101", "quantity": "0.002"}
        ),
        get_account_trades=AsyncMock(
            return_value=[{"orderId": 101, "qty": "0.002"}]
        ),
    )
    pm = SimpleNamespace(replace_stop_loss=AsyncMock(return_value=True))
    manager = _manager(client, pm)
    position = _sp(current_stop=99 if direction == Direction.LONG else 101)
    position.signal.direction = direction
    position.position.quantity = 0.007
    position.plan.tp1_quantity = 0.0028
    position.plan.breakeven_price = 100.12 if direction == Direction.LONG else 99.88

    await manager._check_tp1("ETHUSDT", position, live_qty=0.005)

    assert position.tp1_done is True
    assert position.trailing_active is True
    pm.replace_stop_loss.assert_awaited_once_with(
        position.position, position.plan.breakeven_price
    )
    client.get_account_trades.assert_awaited_once_with(
        "ETHUSDT", order_id=101, limit=500
    )


@pytest.mark.asyncio
async def test_rounded_small_tp2_fill_activates_runner_floor():
    # TP1/TP2 at 40%/20% submit 0.002/0.001 rather than 0.0028/0.0014.
    client = SimpleNamespace(
        quantize_quantity=AsyncMock(side_effect=[0.002, 0.001]),
        get_algo_order=AsyncMock(
            return_value={"actualOrderId": "202", "quantity": "0.001"}
        ),
        get_account_trades=AsyncMock(
            return_value=[{"orderId": 202, "qty": "0.001"}]
        ),
    )
    pm = SimpleNamespace(replace_stop_loss=AsyncMock(return_value=True))
    manager = _manager(client, pm, _cfg(scalper_tp2_fraction=0.2))
    position = _sp(current_stop=100.2, tp1_done=True)
    position.position.quantity = 0.007
    position.plan.tp1_quantity = 0.0028
    position.plan.tp2_quantity = 0.0014

    await manager._check_tp2("ETHUSDT", position, live_qty=0.004)

    assert position.tp2_done is True
    pm.replace_stop_loss.assert_awaited_once_with(position.position, 102.0)


@pytest.mark.asyncio
async def test_tp2_fill_can_protect_runner_when_tp1_order_was_rejected():
    client = SimpleNamespace(
        get_algo_order=AsyncMock(
            return_value={"actualOrderId": "202", "quantity": "0.3"}
        ),
        get_account_trades=AsyncMock(
            return_value=[{"orderId": 202, "qty": "0.3"}]
        ),
    )
    pm = SimpleNamespace(replace_stop_loss=AsyncMock(return_value=True))
    manager = _manager(client, pm)
    position = _sp()
    position.plan.tp1_algo_id = None

    await manager._check_tp2("BTCUSDT", position, live_qty=0.7)

    assert position.tp2_done is True
    assert position.trailing_active is True
    pm.replace_stop_loss.assert_awaited_once_with(position.position, 102.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("filter_result", [0.0, float("nan"), RuntimeError("filters")])
async def test_unavailable_rounding_hint_still_requires_authoritative_tp_fill(filter_result):
    quantize = AsyncMock(
        side_effect=filter_result if isinstance(filter_result, Exception) else None,
        return_value=filter_result,
    )
    client = SimpleNamespace(
        quantize_quantity=quantize,
        get_algo_order=AsyncMock(return_value={"actualOrderId": ""}),
        get_account_trades=AsyncMock(),
    )
    pm = SimpleNamespace(replace_stop_loss=AsyncMock(return_value=True))
    manager = _manager(client, pm)
    position = _sp()

    await manager._check_tp1("BTCUSDT", position, live_qty=0.99)

    assert position.tp1_done is False
    pm.replace_stop_loss.assert_not_awaited()
    client.get_account_trades.assert_not_awaited()


@pytest.mark.asyncio
async def test_unchanged_position_skips_tp_rounding_and_signed_fill_queries():
    client = SimpleNamespace(
        quantize_quantity=AsyncMock(),
        get_algo_order=AsyncMock(),
    )
    manager = _manager(client)
    position = _sp()

    await manager._check_tp1("BTCUSDT", position, live_qty=1.0)
    await manager._check_tp2("BTCUSDT", position, live_qty=1.0)

    client.quantize_quantity.assert_not_awaited()
    client.get_algo_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_initial_sl_is_exactly_ledgered_and_starts_hour_cooldown():
    now_ms = int(time.time() * 1000)
    entry_row = {
        "id": 1,
        "orderId": 111,
        "buyer": True,
        "qty": "1",
        "price": "100",
        "realizedPnl": "0",
        "commission": "0.02",
        "commissionAsset": "USDT",
        "time": now_ms - 100,
    }
    close_row = {
        "id": 2,
        "orderId": 222,
        "buyer": False,
        "qty": "1",
        "price": "99.9",
        "realizedPnl": "-0.1",
        "commission": "0.05",
        "commissionAsset": "USDT",
        "time": now_ms,
    }

    async def account_trades(_symbol, *, order_id=None, **_kwargs):
        return [entry_row] if order_id == 111 else [entry_row, close_row]

    client = SimpleNamespace(
        get_account_trades=account_trades,
        get_current_price=AsyncMock(return_value=99.9),
    )
    pm = SimpleNamespace(place_stop_loss_or_close=AsyncMock(return_value=None))
    tracker = SimpleNamespace(record_failed_execution=AsyncMock(return_value=9))
    # Yanlışlıkla daha küçük ayarlansa bile güvenlik tabanı 60 dakikadır.
    executor = ScalpExecutor(
        client,
        pm,
        tracker,
        _cfg(scalper_protection_failure_cooldown_minutes=1),
    )
    executor.FAILED_LEDGER_RETRY_DELAYS = (0.0,)

    result = await executor._finalize_position(
        signal=_signal(),
        direction=Direction.LONG,
        sl_side="SELL",
        entry_price=100.0,
        filled_qty=1.0,
        entry_order_id="111",
        entry_candle_time=now_ms,
    )

    assert result is None
    ledger = tracker.record_failed_execution.await_args.kwargs
    assert ledger["exit_price"] == pytest.approx(99.9)
    assert ledger["realized_pnl"] == pytest.approx(-0.17)
    assert ledger["pnl_source"] == "binance_account_trades_net"
    assert executor.is_entry_blocked("BTCUSDT") is True
    assert executor.cooldown_snapshot()[0]["remaining_seconds"] > 3590


@pytest.mark.asyncio
async def test_non_usdt_commission_asset_is_not_labeled_verified_net():
    now_ms = int(time.time() * 1000)
    entry = {
        "id": 1,
        "orderId": 111,
        "buyer": True,
        "qty": "1",
        "price": "100",
        "realizedPnl": "0",
        "commission": "0.02",
        "commissionAsset": "USDC",
        "time": now_ms - 100,
    }
    close = {
        "id": 2,
        "orderId": 222,
        "buyer": False,
        "qty": "1",
        "price": "99.8",
        "realizedPnl": "-0.2",
        "commission": "0.05",
        "commissionAsset": "USDC",
        "time": now_ms,
    }

    async def account_trades(_symbol, *, order_id=None, **_kwargs):
        return [entry] if order_id == 111 else [entry, close]

    executor = ScalpExecutor(
        SimpleNamespace(
            get_account_trades=account_trades,
            get_current_price=AsyncMock(return_value=99.8),
        ),
        SimpleNamespace(),
        SimpleNamespace(),
        _cfg(),
    )
    executor.FAILED_LEDGER_RETRY_DELAYS = (0.0,)

    ledger = await executor._fetch_failed_execution_ledger(
        symbol="BTCUSDT",
        direction=Direction.LONG,
        entry_price=100.0,
        filled_qty=1.0,
        entry_order_id="111",
    )

    assert ledger.pnl_source == "estimated_gross"
    assert ledger.realized_pnl == pytest.approx(-0.2)
    assert "commission_asset_not_additive" in ledger.notes


@pytest.mark.asyncio
async def test_virtual_capital_compounds_only_tracker_eligible_pnl_and_caps_exchange():
    client = SimpleNamespace(get_account_balance=AsyncMock(return_value=5_000.0))
    tracker = SimpleNamespace(
        compounding_snapshot=AsyncMock(
            return_value={"eligible_realized_pnl": 75.0}
        )
    )
    executor = ScalpExecutor(
        client,
        SimpleNamespace(),
        tracker,
        _cfg(
            scalper_virtual_capital_usdt=1_000.0,
            scalper_virtual_capital_start_trade_id=8,
        ),
    )

    assert await executor.get_sizing_equity() == pytest.approx(1_075.0)
    assert executor.last_sizing_equity == pytest.approx(1_075.0)
    assert executor.sizing_snapshot()["start_trade_id"] == 8
    # D20b: scalper'ın sanal kasası gömülü takipçinin (strategy="AP")
    # satırlarını DIŞLAR — iki defter birbirini kirletmez.
    tracker.compounding_snapshot.assert_awaited_once_with(
        8, exclude_strategies=("AP",)
    )


@pytest.mark.asyncio
async def test_tracker_compounding_excludes_positive_fallback_and_all_legacy(monkeypatch):
    rows = [
        SimpleNamespace(realized_pnl=50.0, notes="pnl_source=binance_income_net"),
        SimpleNamespace(realized_pnl=-10.0, notes="pnl_source=binance_account_trades_net"),
        SimpleNamespace(realized_pnl=-20.0, notes="pnl_source=estimated_gross"),
        SimpleNamespace(realized_pnl=100.0, notes="pnl_source=estimated_gross"),
        SimpleNamespace(realized_pnl=999.0, notes=None),
        SimpleNamespace(realized_pnl=-999.0, notes=None),
    ]

    class _Result:
        def scalars(self):
            return self

        def all(self):
            return rows

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, _query):
            return _Result()

    monkeypatch.setattr(tracker_module, "AsyncSessionLocal", lambda: _Session())

    snapshot = await ScalpTracker().compounding_snapshot(start_trade_id=8)

    assert snapshot["eligible_realized_pnl"] == pytest.approx(20.0)
    assert snapshot["verified_count"] == 2
    assert snapshot["negative_fallback_count"] == 1
    assert snapshot["excluded_positive_fallback"] == 1
    assert snapshot["excluded_legacy"] == 2
