"""`FollowerExitManager._recover_one` — restart kurtarması (D20).

Kurtarma yolu, canlı yolun AYNI güvenlik sözleşmesini uygulamak ZORUNDADIR;
aksi halde bir yeniden başlatma sessizce korumayı düşürür. Bu dosya iki gerçek
kusuru kilitler (düşmanca inceleme, 2026-08-23):

1. **Parçalar `quantity/3` DEĞİL, borsa `stepSize`'ıyla kurulur.** Canlı yolda
   `split_three_quantities` ilk iki parçayı AŞAĞI yuvarlar ve artığı SON
   parçaya verir (11 adım → 3/3/5). `quantity/3` (=3.67) varsayımı,
   `_check_tp1_breakeven`'in miktar-azalma eşiğini (`live_qty >
   filled - expected*0.9`) küçük adım sayılarında HİÇ geçirmez → restart
   sonrası break-even KALICI olarak ölür.
2. **`tp1_done=True` kurtarıldığında canlı stop break-even'den gevşekse**
   bayrak DÜŞÜRÜLÜR: takipçide trailing yoktur, `_check_tp1_breakeven` bir daha
   çağrılmazsa pozisyonun 2/3'ü tam risk stopuyla taşınırdı.

GERÇEK AĞ/DB YOK.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.models.waiting_signal  # noqa: F401  (SQLAlchemy mapper zinciri)
from src.strategies.follower.exits import FollowerExitManager
from src.strategies.follower.plan import split_three_quantities

ENTRY = 100.0
STEP = 0.001


def _cfg():
    return SimpleNamespace(
        scalper_breakeven_buffer_pct=0.05,
        scalper_taker_fee_pct=0.05,
        scalper_maker_fee_pct=0.02,
        scalper_chandelier_atr_mult=0.0,
        scalper_chandelier_atr_period=22,
    )


def _trade(quantity: float, **overrides):
    base = dict(
        id=7,
        symbol="BTCUSDT",
        strategy="AP",
        direction="LONG",
        leverage=50,
        entry_price=ENTRY,
        quantity=quantity,
        sl_algo_id="500",
        tp1_algo_id="501",
        tp2_algo_id="502",
        tp3_algo_id="503",
        entry_order_id="1",
        # Defter notu AlgoPro seviyelerini taşır (D20a bulgu 9): düşen bir
        # TP emrinin fiyatı yalnız burada bulunabilir.
        signal_reason=(
            "algopro:entry;tf=1;follower;lev=50;levels=message;"
            "ap_sl=99;ap_tp1=100.5;ap_tp2=101;ap_tp3=101.5"
        ),
        opened_at=datetime.now(timezone.utc),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _algo_orders(stop_price: float):
    return [
        {"orderType": "STOP_MARKET", "side": "SELL", "algoId": "500",
         "triggerPrice": stop_price},
        {"orderType": "TAKE_PROFIT_MARKET", "side": "SELL", "algoId": "501",
         "triggerPrice": 100.5},
        {"orderType": "TAKE_PROFIT_MARKET", "side": "SELL", "algoId": "502",
         "triggerPrice": 101.0},
        {"orderType": "TAKE_PROFIT_MARKET", "side": "SELL", "algoId": "503",
         "triggerPrice": 101.5},
    ]


def _manager(*, live_amt: float, stop_price: float, step: float = STEP,
             confirmed=()):
    """`confirmed`: fill'i doğrulanmış sayılacak algo id'ler."""
    client = SimpleNamespace(
        get_position_risk=AsyncMock(return_value={"positionAmt": live_amt}),
        get_open_algo_orders=AsyncMock(return_value=_algo_orders(stop_price)),
        get_symbol_filters=AsyncMock(return_value={"stepSize": step}),
        # Eksik TP onarımı (D20a bulgu 9) canlı fiyat + TP emri yolunu kullanır.
        get_current_price=AsyncMock(return_value=ENTRY),
        place_take_profit=AsyncMock(return_value={"algoId": 777}),
    )
    manager = FollowerExitManager(
        client,
        SimpleNamespace(),
        SimpleNamespace(record_close=AsyncMock(), close_seq=0),
        _cfg(),
    )
    manager.logger = MagicMock()

    seen: list = []

    async def _confirm(*, symbol, algo_id, expected_quantity, label):
        seen.append((label, algo_id, expected_quantity))
        return algo_id in confirmed and expected_quantity > 0

    manager._confirmed_algo_fill = _confirm
    manager._confirm_calls = seen
    return manager


class TestRecoveredSplitMatchesLivePath:
    async def test_parts_come_from_step_size_not_thirds(self):
        """11 adımlık miktar: gerçek parçalar 3/3/5 (quantity/3 = 3.67 DEĞİL)."""
        quantity = 11 * STEP
        manager = _manager(live_amt=quantity, stop_price=99.0)

        assert await manager._recover_one(_trade(quantity)) is True

        sp = manager._positions["BTCUSDT"]
        expected = split_three_quantities(quantity, STEP)
        assert expected == pytest.approx((3 * STEP, 3 * STEP, 5 * STEP))
        assert sp.plan.tp1_quantity == pytest.approx(expected[0])
        assert sp.plan.tp2_quantity == pytest.approx(expected[1])
        assert sp.plan.tp3_quantity == pytest.approx(expected[2])
        assert sp.position.first_tp_quantity == pytest.approx(expected[0])
        # Fill doğrulaması da GERÇEK parça miktarıyla sorulmalı.
        assert manager._confirm_calls[0][2] == pytest.approx(expected[0])

    async def test_breakeven_threshold_is_reachable_after_recovery(self):
        """Kurtarılan eşik canlı TP1 dolumunda GEÇİLEBİLİR olmalı.

        Kusurlu (quantity/3) hâlde: kalan = 8 adım, eşik = 11 − 0.9×3.67 =
        7.70 → `8 > 7.70` → BE hiç denenmez.
        """
        quantity = 11 * STEP
        manager = _manager(live_amt=quantity, stop_price=99.0)
        await manager._recover_one(_trade(quantity))
        sp = manager._positions["BTCUSDT"]

        live_after_tp1 = quantity - 3 * STEP  # borsada kalan
        threshold = sp.position.quantity - sp.plan.tp1_quantity * 0.9
        assert live_after_tp1 <= threshold  # eşik GEÇİLİR

    async def test_missing_filters_fall_back_to_thirds_with_warning(self):
        quantity = 0.012
        manager = _manager(live_amt=quantity, stop_price=99.0)
        manager.client.get_symbol_filters = AsyncMock(
            side_effect=RuntimeError("filtre yok")
        )

        assert await manager._recover_one(_trade(quantity)) is True

        sp = manager._positions["BTCUSDT"]
        assert sp.plan.tp1_quantity == pytest.approx(quantity / 3)
        assert manager.logger.warning.called


class TestRecoveredBreakEvenReconciliation:
    async def test_loose_live_stop_reopens_breakeven(self):
        """TP1 dolmuş ama stop hâlâ orijinal SL'de → bayrak DÜŞER."""
        quantity = 12 * STEP
        manager = _manager(
            live_amt=quantity, stop_price=99.0, confirmed={"501"}
        )

        await manager._recover_one(_trade(quantity))

        sp = manager._positions["BTCUSDT"]
        assert sp.tp1_done is False  # BE yeniden denenecek
        assert sp.position.current_stoploss == pytest.approx(99.0)
        assert any(
            "break-even" in str(c) for c in manager.logger.warning.call_args_list
        )

    async def test_stop_already_at_breakeven_keeps_the_flag(self):
        """Canlı stop zaten BE kadar koruyucuysa bayrak KORUNUR (tekrar yok)."""
        quantity = 12 * STEP
        # LONG BE ≈ 100 × (1 + 0.0005 + 0.0005) / (1 − 0.0005) ≈ 100.15
        manager = _manager(
            live_amt=quantity, stop_price=100.30, confirmed={"501"}
        )

        await manager._recover_one(_trade(quantity))

        sp = manager._positions["BTCUSDT"]
        assert sp.tp1_done is True
        assert sp.plan.breakeven_price < 100.30

    async def test_unfilled_tp1_is_not_affected(self):
        quantity = 12 * STEP
        manager = _manager(live_amt=quantity, stop_price=99.0)

        await manager._recover_one(_trade(quantity))

        sp = manager._positions["BTCUSDT"]
        assert sp.tp1_done is False
        assert sp.tp2_done is False
        assert sp.tp3_done is False


class TestRecoveryRepairsMissingTpOrders:
    """D20a bulgu 9: restart kurtarması KAYIP TP emirlerini yeniden koyar.

    Takipçide TP'ler ÇIKIŞIN KENDİSİDİR (trailing yok). Restart sırasında
    (ya da bir kapanış turunun `cancel_all_open_orders`'ında) düşen bir
    bacak, o dilimin AlgoPro hedefinde değil STOPTA kapanması demektir.
    Düzeltme olmadan bu testler KIRMIZIDIR (kurtarma eksik bacağı görmezdi).
    """

    async def test_missing_leg_is_replaced_after_restart(self):
        quantity = 12 * STEP
        manager = _manager(live_amt=quantity, stop_price=99.0)
        # TP3 (algoId 503) borsada YOK.
        manager.client.get_open_algo_orders = AsyncMock(
            return_value=[
                order
                for order in _algo_orders(99.0)
                if order["algoId"] != "503"
            ]
        )

        assert await manager._recover_one(_trade(quantity)) is True

        manager.client.place_take_profit.assert_awaited_once()
        kwargs = manager.client.place_take_profit.await_args.kwargs
        assert kwargs["stop_price"] == pytest.approx(101.5)
        assert kwargs["side"] == "SELL"  # LONG pozisyonu kapatır
        assert manager.tp_repair_snapshot()["replaced"] == 1

    async def test_complete_ladder_is_left_alone(self):
        quantity = 12 * STEP
        manager = _manager(live_amt=quantity, stop_price=99.0)

        assert await manager._recover_one(_trade(quantity)) is True

        manager.client.place_take_profit.assert_not_called()

    async def test_repair_failure_never_breaks_recovery(self):
        quantity = 12 * STEP
        manager = _manager(live_amt=quantity, stop_price=99.0)
        manager.client.get_open_algo_orders = AsyncMock(
            side_effect=[_algo_orders(99.0), RuntimeError("ağ")]
        )

        # Kurtarma BAŞARILI kalır: SL doğrulandı, TP onarımı yalnız bir ek.
        assert await manager._recover_one(_trade(quantity)) is True
