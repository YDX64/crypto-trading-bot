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
from src.trading.binance_client_improved import BinanceAPIError

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
        # Ücret eşiği kapısı VARSAYILAN AÇIKTIR (1.0) ve ölçülen BTC 1m
        # seviyeleriyle HER girişi reddeder; mekanik testler kapıyı açıkça
        # kapatır, kapının kendisi TestFeeGate'te test edilir.
        follower_min_tp1_fee_ratio=0.0,
        follower_max_signal_drift_pct=0.0,
        follower_max_event_age_sec=20.0,
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
        # Emirden ÖNCEKİ canlı fiyat kapısı (bulgu 1) bunu okur.
        self.live_price: float | None = 77126.08

    async def get_current_price(self, symbol):
        self.calls.append("get_current_price")
        return self.live_price

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
        self.sl_kwargs: dict = {}
        self.emergency_closed: list = []
        self.emergency_ok = True

    async def emergency_close(self, symbol):
        self.calls.append("emergency_close")
        self.emergency_closed.append(symbol)
        return self.emergency_ok

    async def resolve_fill(self, symbol, entry_order):
        self.calls.append("resolve_fill")
        return 77126.08, 0.129

    async def place_stop_loss_or_close(self, **kwargs):
        self.calls.append("place_stop_loss_or_close")
        self.sl_kwargs = dict(kwargs)
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

    async def test_reanchor_budget_bounded_by_liquidation_guard(self):
        """-2021 sonrası yeniden çapalama bütçesi 100x'te %5 OLAMAZ.

        `FOLLOWER_MAX_SL_PCT` (%5) tek başına kullanılırsa 100x'te stop marjın
        5 katı uzağa taşınabilirdi (likidasyonun ötesi). Bütçe likidasyon
        kapısıyla tutarlı kırpılır: LIQ_GUARD / kaldıraç = 50/100 = %0.5.
        """
        executor, client, pm, tracker = _make_executor()
        await executor.open_position(
            event=parse_follower_event(SELL_ENTRY),
            levels=_levels(),
            equity_usdt=1000.0,
        )
        assert pm.sl_kwargs["max_distance_pct"] == pytest.approx(0.5)
        assert pm.sl_kwargs["reference_price"] == pytest.approx(77126.08)

    async def test_reanchor_budget_uses_band_when_leverage_low(self):
        """Düşük kaldıraçta bağlayıcı sınır FOLLOWER_MAX_SL_PCT'tir."""
        cfg = _cfg(follower_lev_max=5, follower_max_sl_pct=3.0)
        executor, client, pm, tracker = _make_executor(cfg)
        levels = _levels(cfg)
        await executor.open_position(
            event=parse_follower_event(SELL_ENTRY),
            levels=levels,
            equity_usdt=1000.0,
        )
        # 50/5 = %10 > %3 → bant kazanır.
        assert pm.sl_kwargs["max_distance_pct"] == pytest.approx(3.0)

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


class TestSafetyBudgetAndFeeThreshold:
    """Düşmanca inceleme (2026-08-23) — koruma aritmetiği regresyonları."""

    async def test_reanchor_budget_respects_the_mmr_gate(self):
        """Bütçe artık mmr kapısının fiyat karşılığını da içerir.

        100x + mmr 0.004 → likidasyon mesafesi (1/lev − mmr) yalnız %0.60;
        yalnız liq_guard kullanılsaydı yeniden çapalama stopu %0.50'ye kadar
        açılabilir, likidasyonun 0.1 puan yakınına taşınırdı. mmr kapısı
        (`(1/lev − mmr)/safety_mult`) %0.30'da keser.
        """
        cfg = _cfg(follower_max_sl_pct=5.0)
        brackets = [LeverageBracket(125, 0.004, 0.0, float("inf"))]
        executor, client, pm, _ = _make_executor(cfg, brackets=brackets)

        await executor.open_position(
            event=parse_follower_event(SELL_ENTRY),
            levels=_levels(cfg),
            equity_usdt=1000.0,
        )

        leverage = 100
        assert pm.sl_kwargs["max_distance_pct"] == pytest.approx(
            100.0 * (1.0 / leverage - 0.004) / 2.0
        )
        assert pm.sl_kwargs["max_distance_pct"] < 50.0 / leverage

    async def test_slippage_tightens_the_stop_never_widens_it(self):
        """Dolum kayması stop mesafesini bütçenin üstüne çıkarırsa SIKILAŞTIR.

        `sl_pct` SİNYAL fiyatından hesaplanır; MARKET girişte kayma gerçek
        mesafeyi büyütür ve planlanan risk sessizce likidasyon bölgesine
        kayabilir.
        """
        cfg = _cfg()
        executor, client, pm, _ = _make_executor(cfg)
        # Aleyhe kayma: SHORT girişi 77000'den doldu, AlgoPro stopu 77167.77
        # → gerçek mesafe %0.218, bütçe (liq_guard/lev = 50/100) %0.50 değil,
        # mmr kapısı 0 mmr'de %0.50 → bütçeyi daraltıp kırpmayı zorla.
        cfg.follower_lev_liq_guard_pct = 10.0  # bütçe %0.10
        pm.resolve_fill = AsyncMock(return_value=(77000.0, 0.129))

        position = await executor.open_position(
            event=parse_follower_event(SELL_ENTRY),
            levels=_levels(cfg),
            equity_usdt=1000.0,
        )

        assert position is not None
        clamped = pm.sl_kwargs["stop_price"]
        assert clamped < 77167.77  # SIKILAŞTIRILDI (girişe yaklaştı)
        assert clamped == pytest.approx(77000.0 * (1 + 0.10 / 100.0))
        assert position.position.current_stoploss == pytest.approx(clamped)
        assert executor.logger.warning.called

    async def test_stop_is_never_widened_when_inside_budget(self):
        cfg = _cfg()
        executor, client, pm, _ = _make_executor(cfg)

        await executor.open_position(
            event=parse_follower_event(SELL_ENTRY),
            levels=_levels(cfg),
            equity_usdt=1000.0,
        )

        assert pm.sl_kwargs["stop_price"] == pytest.approx(77167.77)

    async def test_tp1_failure_is_retried_once_then_counted(self):
        """TP1 yoksa break-even HİÇ kurulamaz — sessiz kalmak yasak."""
        executor, client, pm, _ = _make_executor()
        client.tp_error = BinanceAPIError(400, -2021, "Order would immediately trigger")

        position = await executor.open_position(
            event=parse_follower_event(SELL_ENTRY),
            levels=_levels(),
            equity_usdt=1000.0,
        )

        assert position is not None  # SL var, pozisyon iptal EDİLMEZ
        # 3 kademe + TP1 için 1 yeniden deneme
        assert client.calls.count("place_take_profit") == 4
        assert executor.reject_snapshot().get("tp1_missing") == 1
        assert executor.logger.critical.called

    async def test_successful_tp1_is_not_retried(self):
        executor, client, pm, _ = _make_executor()

        await executor.open_position(
            event=parse_follower_event(SELL_ENTRY),
            levels=_levels(),
            equity_usdt=1000.0,
        )

        assert client.calls.count("place_take_profit") == 3
        assert "tp1_missing" not in executor.reject_snapshot()

    async def test_fee_roi_is_recorded_and_warned(self):
        """Kaldıraç tavana dayandığında TP1 ROI komisyonun ALTINDA kalır."""
        executor, client, pm, tracker = _make_executor()

        position = await executor.open_position(
            event=parse_follower_event(SELL_ENTRY),
            levels=_levels(),
            equity_usdt=1000.0,
        )

        plan_meta = position.meta["plan"]
        # 100x, muhafazakâr taker %0.05 → gidiş-dönüş = marjın %10'u
        assert plan_meta["roundtrip_fee_roi_pct"] == pytest.approx(10.0)
        assert plan_meta["fee_roi_real_pct"] == pytest.approx(10.0)
        assert plan_meta["tp1_covers_fees"] is False
        # sl_pct %0.054 → tp1_roi = 0.5 × 100 × 0.054 = %2.70 < %10
        assert plan_meta["tp_roi_pct"][0] < plan_meta["roundtrip_fee_roi_pct"]
        assert any(
            "komisyonun" in str(c) for c in executor.logger.warning.call_args_list
        )
        reason = tracker.record_open.await_args.kwargs["signal"].reason
        assert "fee_roi=" in reason and "sl_pct_fill=" in reason

    async def test_optional_fee_gate_blocks_entry_when_enabled(self):
        """Varsayılan KAPALI kapı açıldığında giriş REDDEDİLİR (emir yok)."""
        cfg = _cfg(follower_min_tp1_fee_ratio=1.0)
        executor, client, pm, _ = _make_executor(cfg)

        with pytest.raises(FollowerRejected) as exc:
            await executor.open_position(
                event=parse_follower_event(SELL_ENTRY),
                levels=_levels(cfg),
                equity_usdt=1000.0,
            )

        assert exc.value.code == "fee_gate"
        assert not any(c.startswith("open_market_order") for c in client.calls)

    async def test_fee_gate_is_disabled_by_default(self):
        executor, client, pm, _ = _make_executor()
        position = await executor.open_position(
            event=parse_follower_event(SELL_ENTRY),
            levels=_levels(),
            equity_usdt=1000.0,
        )
        assert position is not None

    async def test_ledger_margin_matches_the_exchange_quantity(self):
        """`quantize_quantity` AŞAĞI yuvarlar — defterdeki marj GERÇEK olmalı."""
        executor, client, pm, _ = _make_executor()

        position = await executor.open_position(
            event=parse_follower_event(SELL_ENTRY),
            levels=_levels(),
            equity_usdt=1000.0,
        )

        plan_meta = position.meta["plan"]
        # `notional = margin × lev` ÖZDEŞLİĞİ borsa miktarından sonra da
        # korunmalı; aksi halde defterdeki marj planlanan (yuvarlama öncesi)
        # değerde kalır ve sonraki PF/risk analizi bozulur.
        assert plan_meta["notional_usdt"] == pytest.approx(
            plan_meta["margin_usdt"] * plan_meta["leverage"]
        )
        assert plan_meta["margin_usdt"] == pytest.approx(
            plan_meta["quantity"] * 77126.08 / plan_meta["leverage"]
        )


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


class TestSignalDriftAndStopSide:
    """Düşmanca inceleme bulgu 1: taraf kontrolü + sapma kapısı.

    Düzeltme olmadan bu testler KIRMIZIDIR: eski kod canlı fiyata HİÇ
    bakmadan MARKET emri gönderiyor, dolum stopu geçmişse `abs()` yüzünden
    bunu göremiyor ve stopu "bütçeye" sıkıştırarak (ya da -2021 sonrası
    `_reanchor_stop_price` ile) AlgoPro'nun HİÇ SEÇMEDİĞİ bir stop uyduruyordu.
    """

    async def test_stop_already_passed_before_order_blocks_entry(self):
        executor, client, pm, _ = _make_executor()
        # SHORT: stop 77167.77 girişin ÜSTÜNDE. Canlı fiyat stopun da
        # üstüne çıkmışsa AlgoPro tezi ölmüştür.
        client.live_price = 77200.0
        with pytest.raises(FollowerRejected) as exc:
            await executor.open_position(
                event=parse_follower_event(SELL_ENTRY),
                levels=_levels(),
                equity_usdt=1000.0,
            )
        assert exc.value.code == "stop_already_passed"
        assert not any(c.startswith("open_market_order") for c in client.calls)

    async def test_stale_signal_price_blocks_entry(self):
        """Sapma > SL mesafesinin %50'si → giriş YOK (türetilmiş varsayılan)."""
        executor, client, pm, _ = _make_executor()
        # sl_pct ≈ %0.0541 → sınır ≈ %0.027 (≈ 20.8 birim). 25 birim aşar.
        client.live_price = 77126.08 + 25.0
        with pytest.raises(FollowerRejected) as exc:
            await executor.open_position(
                event=parse_follower_event(SELL_ENTRY),
                levels=_levels(),
                equity_usdt=1000.0,
            )
        assert exc.value.code == "signal_drift"
        assert not any(c.startswith("open_market_order") for c in client.calls)

    async def test_drift_within_limit_still_opens(self):
        executor, client, pm, _ = _make_executor()
        client.live_price = 77126.08 + 5.0  # %0.0065 < %0.027
        position = await executor.open_position(
            event=parse_follower_event(SELL_ENTRY),
            levels=_levels(),
            equity_usdt=1000.0,
        )
        assert position is not None

    async def test_explicit_drift_limit_overrides_the_derived_default(self):
        cfg = _cfg(follower_max_signal_drift_pct=0.001)
        executor, client, pm, _ = _make_executor(cfg)
        client.live_price = 77126.08 + 5.0
        with pytest.raises(FollowerRejected) as exc:
            await executor.open_position(
                event=parse_follower_event(SELL_ENTRY),
                levels=_levels(cfg),
                equity_usdt=1000.0,
            )
        assert exc.value.code == "signal_drift"

    async def test_unreadable_live_price_is_fail_closed(self):
        executor, client, pm, _ = _make_executor()
        client.live_price = None
        with pytest.raises(FollowerRejected) as exc:
            await executor.open_position(
                event=parse_follower_event(SELL_ENTRY),
                levels=_levels(),
                equity_usdt=1000.0,
            )
        assert exc.value.code == "live_price"
        assert not any(c.startswith("open_market_order") for c in client.calls)

    async def test_fill_past_the_stop_closes_and_never_reanchors(self):
        """Dolum stopu geçtiyse: reduce-only kapat, stop KOYMA/ÇAPALAMA."""
        executor, client, pm, tracker = _make_executor()

        async def _late_fill(symbol, entry_order):
            pm.calls.append("resolve_fill")
            return 77200.0, 0.129  # SHORT dolumu stopun (77167.77) ÜSTÜNDE

        pm.resolve_fill = _late_fill
        position = await executor.open_position(
            event=parse_follower_event(SELL_ENTRY),
            levels=_levels(),
            equity_usdt=1000.0,
        )
        assert position is None
        assert pm.emergency_closed == ["BTCUSDT"]
        # Ne SL emri, ne TP emri: uydurulmuş stop YOK.
        assert "place_stop_loss_or_close" not in pm.calls
        assert client.tp_orders == []
        assert executor.reject_snapshot()["stop_already_passed"] == 1
        # Defter satırı yazıldı (sessiz kalma yok).
        notes = tracker.record_failed_execution.await_args.kwargs["notes"]
        assert "stop_already_passed" in notes

    async def test_unclosable_position_after_late_fill_raises(self):
        """Kapatılamıyorsa korumasız pozisyon latch'i (motor entry-halt kurar)."""
        from src.trading.position_manager import UnprotectedPositionError

        executor, client, pm, _ = _make_executor()
        pm.emergency_ok = False

        async def _late_fill(symbol, entry_order):
            return 77200.0, 0.129

        pm.resolve_fill = _late_fill
        with pytest.raises(UnprotectedPositionError):
            await executor.open_position(
                event=parse_follower_event(SELL_ENTRY),
                levels=_levels(),
                equity_usdt=1000.0,
            )

    async def test_sl_pct_fill_is_measured_from_the_real_stop(self):
        """Telemetri GERÇEKTEN KONAN stoptan yazılır (`effectiveStopPrice`)."""
        executor, client, pm, _ = _make_executor()

        async def _shifted_stop(**kwargs):
            pm.calls.append("place_stop_loss_or_close")
            pm.sl_kwargs = dict(kwargs)
            return {"algoId": 500, "effectiveStopPrice": 77160.0}

        pm.place_stop_loss_or_close = _shifted_stop
        position = await executor.open_position(
            event=parse_follower_event(SELL_ENTRY),
            levels=_levels(),
            equity_usdt=1000.0,
        )
        expected = abs(77126.08 - 77160.0) / 77126.08 * 100.0
        assert position.meta["plan"]["sl_pct_fill"] == pytest.approx(expected)

    async def test_tp_on_the_wrong_side_of_the_fill_is_not_placed(self):
        """LONG'da TP dolumun ÜSTÜNDE olmalı; SHORT'ta altında (bulgu 9)."""
        executor, client, pm, _ = _make_executor()

        async def _slipped_fill(symbol, entry_order):
            return 77100.0, 0.129  # TP1 (77105.23) artık dolumun ÜSTÜNDE

        pm.resolve_fill = _slipped_fill
        position = await executor.open_position(
            event=parse_follower_event(SELL_ENTRY),
            levels=_levels(),
            equity_usdt=1000.0,
        )
        assert position is not None
        prices = [order["price"] for order in client.tp_orders]
        assert 77105.23 not in prices  # anında tetiklenip zararla kapatırdı
        assert prices == pytest.approx([77084.39, 77063.54])
        assert executor.reject_snapshot()["tp_wrong_side"] == 1


class TestFeeGateDefault:
    """Bulgu 3: ücret eşiği kapısı VARSAYILAN AÇIK (ratio 1.0)."""

    def _cfg_without_ratio(self):
        return SimpleNamespace(
            **{
                k: v
                for k, v in vars(_cfg()).items()
                if k != "follower_min_tp1_fee_ratio"
            }
        )

    async def test_measured_btc_entry_is_refused_by_default(self):
        cfg = self._cfg_without_ratio()
        executor, client, pm, _ = _make_executor(cfg)
        with pytest.raises(FollowerRejected) as exc:
            await executor.open_position(
                event=parse_follower_event(SELL_ENTRY),
                levels=_levels(cfg),
                equity_usdt=1000.0,
            )
        assert exc.value.code == "fee_gate"
        # Kapı EMİRDEN ÖNCEDİR: hiçbir emir gönderilmedi.
        assert not any(c.startswith("open_market_order") for c in client.calls)

    async def test_gate_uses_the_real_exchange_commission_rate(self):
        """Borsadan okunan taker oranı düşükse aynı giriş GEÇER.

        Config oranı (%0.05) ile reddedilirdi — kapı gerçek oranla çalışmalı.
        """
        cfg = self._cfg_without_ratio()
        executor, client, pm, _ = _make_executor(cfg)

        async def _cheap(symbol):
            return {"takerCommissionRate": "0.0001", "makerCommissionRate": "0.0001"}

        client.get_user_commission_rate = _cheap
        position = await executor.open_position(
            event=parse_follower_event(SELL_ENTRY),
            levels=_levels(cfg),
            equity_usdt=1000.0,
        )
        assert position is not None
        assert position.meta["plan"]["tp1_covers_fees_real"] is True

    async def test_zero_ratio_disables_the_gate(self):
        executor, client, pm, _ = _make_executor(
            _cfg(follower_min_tp1_fee_ratio=0.0)
        )
        position = await executor.open_position(
            event=parse_follower_event(SELL_ENTRY),
            levels=_levels(),
            equity_usdt=1000.0,
        )
        assert position is not None
