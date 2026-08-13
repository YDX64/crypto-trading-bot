"""Giriş ile koruma arasındaki yürütme gecikmesine dair regresyon testleri.

Bağlam: maker (GTX post-only) giriş emri, sinyal üretildikten saniyeler sonra
dolar. LONG bir maker emri ancak fiyat DÜŞERKEN dolduğu için, dolum anında
sinyal zamanına çapalı stop çoktan geçilmiş olabilir. Binance koşullu emri
``-2021 Order would immediately trigger`` ile reddediyor, bot da korumasız
pozisyon bırakmamak için pozisyonu anında piyasa emriyle kapatıyordu.

Sonuç: her giriş, açıldığı saniye içinde küçük bir zararla kapanıyordu
(09 Ağu BMTUSDT, 10 Ağu BEATUSDT).

Bu testler iki katmanı da kilitler:
  1. ScalpExecutor stop'u GERÇEK dolum fiyatına çapalar (mesafe korunur).
  2. PositionManager -2021 alınca stop'u canlı fiyata göre yeniden hesaplayıp
     tekrar dener; yalnız risk bütçesi aşılırsa acil kapatmaya düşer.
"""

import logging
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import src.models.waiting_signal  # noqa: F401 - SQLAlchemy relationship setup
from src.strategies.scalper.executor import ScalpExecutor
from src.strategies.scalper.types import Direction
from src.trading.binance_client_improved import BinanceAPIError
from src.trading.position_manager import PositionManager


# ----------------------------------------------------------------------
# PositionManager: -2021 sonrası yeniden çapalama
# ----------------------------------------------------------------------


class _StopClient:
    """Belirtilen sayıda denemeyi -2021 ile reddeden sahte istemci."""

    def __init__(self, price: float, reject_first: int = 1, tick: str = "0.0001"):
        self.price = price
        self.reject_first = reject_first
        self.tick = Decimal(tick)
        self.attempts: list = []

    async def get_position_risk(self, _symbol):
        return {"positionAmt": "1.25"}

    async def get_current_price(self, _symbol):
        return self.price

    async def get_symbol_filters(self, _symbol):
        return {"tickSize": self.tick}

    async def place_stop_loss(self, *, symbol, side, stop_price, close_position=True,
                              quantity=None):
        self.attempts.append(stop_price)
        if len(self.attempts) <= self.reject_first:
            raise BinanceAPIError(400, -2021, "Order would immediately trigger")
        return {"algoId": 555, "orderId": 555}


class _PrecisionErrorClient(_StopClient):
    async def place_stop_loss(self, *, symbol, side, stop_price, close_position=True,
                              quantity=None):
        self.attempts.append(stop_price)
        raise BinanceAPIError(400, -1111, "Precision is over the maximum")


async def test_long_stop_is_repriced_below_live_price_instead_of_flattening():
    # Dolum 100.20'de gerçekleşti, stop 100.50'de kaldı, fiyat 100.00'a düştü.
    client = _StopClient(price=100.0)
    manager = PositionManager(client)
    manager._emergency_close = AsyncMock(return_value=True)

    result = await manager.place_stop_loss_or_close(
        symbol="TESTUSDT", sl_side="SELL", stop_price=100.5,
        reference_price=100.2, max_distance_pct=1.0,
    )

    assert result is not None, "stop yeniden fiyatlanabilirken pozisyon kapatıldı"
    manager._emergency_close.assert_not_awaited()
    assert len(client.attempts) == 2
    assert client.attempts[0] == 100.5
    assert client.attempts[1] < client.price, "yeni stop canlı fiyatın altında olmalı"
    # Kayıt/çıkış planı borsadaki gerçek tetik fiyatını görmeli
    assert result["effectiveStopPrice"] == client.attempts[1]


async def test_short_stop_is_repriced_above_live_price():
    client = _StopClient(price=100.0)
    manager = PositionManager(client)
    manager._emergency_close = AsyncMock(return_value=True)

    result = await manager.place_stop_loss_or_close(
        symbol="TESTUSDT", sl_side="BUY", stop_price=99.5,
        reference_price=99.8, max_distance_pct=1.0,
    )

    assert result is not None
    manager._emergency_close.assert_not_awaited()
    assert client.attempts[1] > client.price, "SHORT stopu canlı fiyatın üstünde olmalı"


async def test_reprice_beyond_risk_budget_still_flattens():
    """Yeniden çapalama risk bütçesini aşıyorsa kapatmak DOĞRU davranıştır."""
    client = _StopClient(price=100.0)
    manager = PositionManager(client)
    manager._emergency_close = AsyncMock(return_value=True)

    result = await manager.place_stop_loss_or_close(
        symbol="TESTUSDT", sl_side="SELL", stop_price=100.5,
        reference_price=100.2, max_distance_pct=0.1,
    )

    assert result is None
    manager._emergency_close.assert_awaited_once_with("TESTUSDT")
    assert len(client.attempts) == 1, "bütçe aşılıyorsa ikinci deneme yapılmamalı"


async def test_non_price_error_is_not_repriced():
    """-1111 gibi girdi hataları yeniden fiyatlamayla düzelmez; davranış korunur."""
    client = _PrecisionErrorClient(price=100.0)
    manager = PositionManager(client)
    manager._emergency_close = AsyncMock(return_value=True)

    result = await manager.place_stop_loss_or_close(
        symbol="TESTUSDT", sl_side="SELL", stop_price=100.5,
        reference_price=100.2, max_distance_pct=1.0,
    )

    assert result is None
    assert len(client.attempts) == 1
    manager._emergency_close.assert_awaited_once_with("TESTUSDT")


# ----------------------------------------------------------------------
# ScalpExecutor: stop'un gerçek dolum fiyatına çapalanması
# ----------------------------------------------------------------------


def _executor(max_stop_pct: float = 1.0) -> ScalpExecutor:
    """__init__ yan etkileri (journal IO) olmadan saf yardımcıyı test et."""
    ex = ScalpExecutor.__new__(ScalpExecutor)
    ex.cfg = SimpleNamespace(scalper_max_stop_pct=max_stop_pct)
    ex.logger = logging.getLogger("test.scalper.executor")
    return ex


def _signal(entry: float, stop: float, direction: Direction):
    return SimpleNamespace(
        symbol="TESTUSDT", entry_price=entry, stop_price=stop, direction=direction
    )


def test_long_stop_follows_favourable_fill_and_preserves_distance():
    # Maker LONG: sinyal 100.0'da üretildi, emir 99.7'de doldu (0.3 lehte kayma).
    ex = _executor()
    signal = _signal(entry=100.0, stop=99.5, direction=Direction.LONG)

    adjusted = ex._delay_adjusted_stop(
        signal=signal, direction=Direction.LONG, entry_price=99.7
    )

    assert adjusted == pytest.approx(99.2)
    # Boyutlamada kullanılan birim risk birebir korunur
    assert (99.7 - adjusted) == pytest.approx(100.0 - 99.5)


def test_short_stop_follows_fill_drift():
    ex = _executor()
    signal = _signal(entry=100.0, stop=100.5, direction=Direction.SHORT)

    adjusted = ex._delay_adjusted_stop(
        signal=signal, direction=Direction.SHORT, entry_price=100.3
    )

    assert adjusted == pytest.approx(100.8)
    assert (adjusted - 100.3) == pytest.approx(100.5 - 100.0)


def test_extreme_drift_is_clamped_to_risk_ceiling_not_reverted():
    """Aşırı kaymada yapısal seviyeye dönmek stopu girişin ters tarafına atardı."""
    ex = _executor(max_stop_pct=1.0)
    signal = _signal(entry=100.0, stop=99.0, direction=Direction.LONG)

    adjusted = ex._delay_adjusted_stop(
        signal=signal, direction=Direction.LONG, entry_price=90.0
    )

    assert adjusted == pytest.approx(89.1)  # 90 * (1 - %1)
    assert adjusted < 90.0, "stop girişin altında kalmalı"


def test_no_drift_keeps_structural_stop():
    ex = _executor()
    signal = _signal(entry=100.0, stop=99.5, direction=Direction.LONG)

    adjusted = ex._delay_adjusted_stop(
        signal=signal, direction=Direction.LONG, entry_price=100.0
    )

    assert adjusted == pytest.approx(99.5)


def test_missing_signal_prices_fall_back_to_structural_stop():
    ex = _executor()
    signal = _signal(entry=0.0, stop=99.5, direction=Direction.LONG)

    adjusted = ex._delay_adjusted_stop(
        signal=signal, direction=Direction.LONG, entry_price=100.0
    )

    assert adjusted == pytest.approx(99.5)
