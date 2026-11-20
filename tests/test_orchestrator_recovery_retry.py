"""D44: orchestrator açılış kurtarması başarısızsa izleme döngüsü yeniden dener.

Ağ yok, gerçek uyku yok: zaman `src.services.orchestrator.time` ad alanı
sahte bir saatle değiştirilerek sürülür (asyncio'nun kendi `time.monotonic`'ine
dokunulmaz). Borsa, her çağrıyı sayan bir sahtedir.
"""

from __future__ import annotations

import asyncio
import json
import time as real_time
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import src.main as main_module
import src.services.orchestrator as orch_module
from src.core.database import Base
from src.core.logger import app_logger
from src.models.position import PositionModel, PositionStatus
from src.models.scalp_trade import ScalpTradeModel
from src.services.orchestrator import TradingOrchestrator
from src.trading.binance_client_improved import ImprovedBinanceClient
from src.trading.position_manager import PositionManager
from src.trading.symbol_reservations import symbol_reservations

T0 = 1000.0


# ----------------------------------------------------------------------
# Test çiftleri
# ----------------------------------------------------------------------


class FakeClock:
    """`time` ad alanı yerine geçer; monotonic elle ilerletilir."""

    def __init__(self, start: float = T0) -> None:
        self.now = start

    def monotonic(self) -> float:
        return self.now

    def time(self) -> float:
        return real_time.time()

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeExchange:
    """Yalnız orchestrator'ın kurtarma yolunun dokunduğu uçlar."""

    def __init__(self) -> None:
        self.fail = False
        self.get_all_positions_calls = 0
        self.positions: List[Dict[str, Any]] = []
        self.algo_orders: List[Dict[str, Any]] = []
        self.algo_fail = False
        self.risk_fail = False
        self.risk_amounts: Dict[str, float] = {}
        self.risk_calls: List[tuple] = []
        self.orders: List[Dict[str, Any]] = []
        self.cancels: List[str] = []

    async def test_connection(self) -> bool:
        return True

    async def close(self) -> None:
        return None

    async def get_all_positions(self, **_kwargs: Any) -> List[Dict[str, Any]]:
        self.get_all_positions_calls += 1
        if self.fail:
            raise ConnectionError("binance erisilemiyor {kod: -1003}")
        return [dict(p) for p in self.positions]

    async def get_open_algo_orders(self, symbol: str) -> List[Dict[str, Any]]:
        if self.algo_fail:
            raise TimeoutError("algo okunamadi")
        return [dict(o) for o in self.algo_orders]

    async def get_position_risk(self, symbol: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        self.risk_calls.append((symbol, dict(kwargs)))
        if self.risk_fail:
            raise TimeoutError("risk okunamadi")
        if symbol not in self.risk_amounts:
            return None
        return {"symbol": symbol, "positionAmt": str(self.risk_amounts[symbol])}

    async def _request_with_retry(self, method: str, path: str, params=None, signed=False):
        self.orders.append({"method": method, "path": path, "params": dict(params or {})})
        # Emir dolunca borsadaki miktar sıfırlanır (gerçekçi davranış).
        self.risk_amounts[params["symbol"]] = 0.0
        return {"orderId": 777, "avgPrice": "0"}

    async def cancel_all_open_orders(self, symbol: str) -> Dict[str, Any]:
        self.cancels.append(symbol)
        return {}


def _btc_position(amt: str = "0.01") -> Dict[str, Any]:
    return {
        "symbol": "BTCUSDT",
        "positionAmt": amt,
        "entryPrice": "50000",
        "leverage": "10",
    }


def _eth_position(amt: str = "0.5") -> Dict[str, Any]:
    return {
        "symbol": "ETHUSDT",
        "positionAmt": amt,
        "entryPrice": "3000",
        "leverage": "10",
    }


STOP_ORDER = {"algoId": 12345, "orderType": "STOP_MARKET", "triggerPrice": "49000"}


class LogCapture:
    """loguru mesajlarını (seviye, metin) olarak toplar."""

    def __init__(self) -> None:
        self.records: List[tuple] = []
        self._id: Optional[int] = None

    def __enter__(self) -> "LogCapture":
        self._id = app_logger.add(
            lambda m: self.records.append((m.record["level"].name, m.record["message"])),
            level="DEBUG",
            format="{message}",
        )
        return self

    def __exit__(self, *exc: Any) -> None:
        app_logger.remove(self._id)

    def messages(self, level: str, needle: str) -> List[str]:
        return [m for lv, m in self.records if lv == level and needle in m]


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    symbol_reservations.clear()
    monkeypatch.setattr(ImprovedBinanceClient, "_rest_blocked_until", 0.0)
    yield
    symbol_reservations.clear()


@pytest.fixture
def clock(monkeypatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(orch_module, "time", fake)
    return fake


def _make_orchestrator(fake: FakeExchange, monkeypatch) -> TradingOrchestrator:
    orch = TradingOrchestrator()
    orch.binance = fake
    orch.position_manager = PositionManager(fake)
    # Döngü başlatılırsa gerçek bir tur dışında uyusun.
    monkeypatch.setattr(orch.config, "check_interval_seconds", 3600)
    return orch


async def _start_without_loop(orch: TradingOrchestrator) -> None:
    """start() çalıştır, sonra döngü görevini durdur (sayaçlar karışmasın)."""
    await orch.start()
    await orch.stop()


# ----------------------------------------------------------------------
# 1) Başarısız açılış, sonraki turda iyileşme (+ /health 503 -> 200)
# ----------------------------------------------------------------------


async def test_startup_failure_heals_on_due_tick_and_health_goes_503_to_200(
    clock, monkeypatch
):
    fake = FakeExchange()
    fake.fail = True
    orch = _make_orchestrator(fake, monkeypatch)
    try:
        await orch.start()
        await asyncio.sleep(0)  # döngünün ilk turu (monitoring damgası)
        await asyncio.sleep(0)

        assert orch._recovery_ready is False
        assert orch.health_snapshot()["healthy"] is False
        assert orch.health_snapshot()["recovery_next_retry_in_seconds"] is not None

        telegram = SimpleNamespace(health_snapshot=lambda: {"healthy": True})
        monkeypatch.setattr(main_module, "orchestrator", orch)
        monkeypatch.setattr(main_module, "telegram_bot", telegram)
        monkeypatch.setattr(main_module, "scalper_engine", None)
        monkeypatch.setattr(main_module.settings, "scalper_enabled", False)

        before = await main_module.health_check()
        assert before.status_code == 503
        assert json.loads(before.body)["orchestrator_details"]["recovery_ready"] is False

        # Borsa döndü; vade geldi; gerçek döngü tur sonunda damgayı basar.
        fake.fail = False
        clock.advance(31)
        await orch._retry_recovery_if_due()
        orch._last_monitoring_success_monotonic = clock.monotonic()

        snap = orch.health_snapshot()
        assert orch._recovery_ready is True
        assert snap["healthy"] is True
        assert snap["recovery_retry_attempts"] == 1
        assert snap["recovery_next_retry_in_seconds"] is None

        after = await main_module.health_check()
        assert after.status_code == 200
        assert json.loads(after.body)["orchestrator_details"]["recovery_ready"] is True
    finally:
        await orch.stop()


# ----------------------------------------------------------------------
# 2) GERÇEK _monitoring_loop uçtan uca iyileşme
# ----------------------------------------------------------------------


async def test_real_monitoring_loop_heals_recovery_flag_and_stays_alive(monkeypatch):
    fake = FakeExchange()
    fake.fail = True
    orch = _make_orchestrator(fake, monkeypatch)
    monkeypatch.setattr(orch.config, "check_interval_seconds", 0.005)
    monkeypatch.setattr(TradingOrchestrator, "RECOVERY_RETRY_INITIAL_SECONDS", 0.01)
    monkeypatch.setattr(TradingOrchestrator, "RECOVERY_RETRY_MAX_SECONDS", 0.02)
    try:
        await orch.start()
        assert orch._recovery_ready is False
        fake.fail = False
        for _ in range(200):
            if orch._recovery_ready:
                break
            await asyncio.sleep(0.005)

        assert orch._recovery_ready is True
        assert orch._exchange_ready is True
        assert orch.monitoring_task is not None and not orch.monitoring_task.done()
        assert orch.health_snapshot()["monitoring_task_alive"] is True
    finally:
        await orch.stop()
    assert orch.monitoring_task.done()


# ----------------------------------------------------------------------
# 3) Kalıcı hata: backoff 30/60/120/240/300/300, seyrek log
# ----------------------------------------------------------------------


async def test_persistent_failure_backs_off_and_logs_sparsely(clock, monkeypatch):
    fake = FakeExchange()
    fake.fail = True
    orch = _make_orchestrator(fake, monkeypatch)
    await _start_without_loop(orch)

    waits = [orch._recovery_retry_due_monotonic - clock.monotonic()]
    with LogCapture() as logs:
        for _ in range(8):
            clock.now = orch._recovery_retry_due_monotonic
            await orch._retry_recovery_if_due()  # asla fırlatmamalı
            waits.append(orch._recovery_retry_due_monotonic - clock.monotonic())

        assert orch._recovery_ready is False
        assert orch._recovery_retry_attempts == 8
        assert waits[:6] == [30.0, 60.0, 120.0, 240.0, 300.0, 300.0]
        assert all(w == 300.0 for w in waits[6:])

        # Denemeler t=30, 90, 210, 450, 750, 1050, 1350, 1650'de: hepsi ilk
        # uyarıdan (t=30) itibaren 1800 sn penceresi içinde -> TEK uyarı.
        failures = logs.messages("WARNING", "yeniden denemesi #")
        assert len(failures) == 1
        assert failures[0].startswith("⚠️ Açılış kurtarması yeniden denemesi #1 ")

        # 9. deneme t=1950 (>= 30+1800): pencere doldu, ikinci uyarı.
        clock.now = orch._recovery_retry_due_monotonic
        await orch._retry_recovery_if_due()
        failures = logs.messages("WARNING", "yeniden denemesi #")
        assert len(failures) == 2
        assert "denemesi #9 " in failures[1]
        # Sorgu hatası ERROR satırı yeniden denemelerde BASILMAZ.
        assert logs.messages("ERROR", "borsadan sorgulanamadı") == []


async def test_failure_log_is_single_inside_one_window(clock, monkeypatch):
    fake = FakeExchange()
    fake.fail = True
    orch = _make_orchestrator(fake, monkeypatch)
    await _start_without_loop(orch)

    with LogCapture() as logs:
        for _ in range(6):  # t=30, 90, 210, 450, 750, 1050 (hepsi < 1800 sn)
            clock.now = orch._recovery_retry_due_monotonic
            await orch._retry_recovery_if_due()
        failures = logs.messages("WARNING", "yeniden denemesi #")

    assert orch._recovery_retry_attempts == 6
    assert len(failures) == 1
    assert "binance erisilemiyor" in failures[0]  # neden: son borsa hatası


# ----------------------------------------------------------------------
# 4) Deneme başına TEK imzalı sorgu
# ----------------------------------------------------------------------


@pytest.mark.parametrize("retry_succeeds", [False, True])
async def test_one_signed_query_per_attempt(clock, monkeypatch, retry_succeeds):
    fake = FakeExchange()
    fake.fail = True
    orch = _make_orchestrator(fake, monkeypatch)
    await _start_without_loop(orch)

    fake.fail = not retry_succeeds
    clock.now = orch._recovery_retry_due_monotonic
    calls_before = fake.get_all_positions_calls

    await orch._retry_recovery_if_due()
    await orch._refresh_exchange_readiness_if_due()

    assert fake.get_all_positions_calls == calls_before + 1


# ----------------------------------------------------------------------
# 5) Kalıcı hatada GERÇEK döngü ölmez
# ----------------------------------------------------------------------


async def test_real_loop_survives_persistent_failures_and_keeps_stamping(monkeypatch):
    fake = FakeExchange()
    fake.fail = True
    orch = _make_orchestrator(fake, monkeypatch)
    monkeypatch.setattr(orch.config, "check_interval_seconds", 0.005)
    monkeypatch.setattr(TradingOrchestrator, "RECOVERY_RETRY_INITIAL_SECONDS", 0.005)
    monkeypatch.setattr(TradingOrchestrator, "RECOVERY_RETRY_MAX_SECONDS", 0.01)
    try:
        await orch.start()
        for _ in range(400):
            if orch._recovery_retry_attempts >= 3:
                break
            await asyncio.sleep(0.005)
        first_stamp = orch._last_monitoring_success_monotonic
        await asyncio.sleep(0.03)

        assert orch._recovery_retry_attempts >= 3
        assert orch._recovery_ready is False
        assert not orch.monitoring_task.done()
        assert orch._last_monitoring_success_at is not None
        assert orch._last_monitoring_success_monotonic > first_stamp
    finally:
        await orch.stop()


# ----------------------------------------------------------------------
# 6) Başarılı açılışta yeniden deneme YOK
# ----------------------------------------------------------------------


async def test_successful_startup_never_retries(clock, monkeypatch):
    fake = FakeExchange()
    orch = _make_orchestrator(fake, monkeypatch)
    original = orch.recover_open_positions
    calls = {"n": 0}

    async def spy(*args: Any, **kwargs: Any) -> bool:
        calls["n"] += 1
        return await original(*args, **kwargs)

    monkeypatch.setattr(orch, "recover_open_positions", spy)
    await _start_without_loop(orch)
    assert orch._recovery_ready is True
    assert orch._recovery_retry_due_monotonic is None

    for _ in range(5):
        clock.advance(10_000)
        await orch._retry_recovery_if_due()
        await orch._refresh_exchange_readiness_if_due()

    assert calls["n"] == 1
    assert orch.health_snapshot()["recovery_retry_attempts"] == 0
    assert orch.health_snapshot()["recovery_next_retry_in_seconds"] is None


# ----------------------------------------------------------------------
# 7) Ban açıkken deneme yok; ban kalkınca iyileşir
# ----------------------------------------------------------------------


async def test_rest_ban_blocks_attempt_without_consuming_it_then_heals(clock, monkeypatch):
    fake = FakeExchange()
    fake.fail = True
    orch = _make_orchestrator(fake, monkeypatch)
    await _start_without_loop(orch)

    monkeypatch.setattr(ImprovedBinanceClient, "_rest_blocked_until", real_time.time() + 3600)
    clock.now = orch._recovery_retry_due_monotonic + 5
    due_before = orch._recovery_retry_due_monotonic
    delay_before = orch._recovery_retry_delay
    calls_before = fake.get_all_positions_calls

    await orch._retry_recovery_if_due()

    assert fake.get_all_positions_calls == calls_before
    assert orch._recovery_retry_attempts == 0
    assert orch._recovery_retry_due_monotonic == due_before
    assert orch._recovery_retry_delay == delay_before

    monkeypatch.setattr(ImprovedBinanceClient, "_rest_blocked_until", 0.0)
    fake.fail = False
    await orch._retry_recovery_if_due()

    assert fake.get_all_positions_calls == calls_before + 1
    assert orch._recovery_retry_attempts == 1
    assert orch._recovery_ready is True


# ----------------------------------------------------------------------
# 8) entry-halt: deneme yok
# ----------------------------------------------------------------------


async def test_entry_halt_blocks_retry_even_when_due(clock, monkeypatch):
    fake = FakeExchange()
    fake.fail = True
    orch = _make_orchestrator(fake, monkeypatch)
    await _start_without_loop(orch)

    orch._entry_halted = True
    clock.now = orch._recovery_retry_due_monotonic + 100
    calls_before = fake.get_all_positions_calls

    await orch._retry_recovery_if_due()

    assert fake.get_all_positions_calls == calls_before
    assert orch._recovery_retry_attempts == 0
    assert orch._recovery_ready is False


# ----------------------------------------------------------------------
# 9) Ön koşul (scalper kurtarması)
# ----------------------------------------------------------------------


async def test_precondition_false_waits_then_true_attempts(clock, monkeypatch):
    fake = FakeExchange()
    fake.fail = True
    orch = _make_orchestrator(fake, monkeypatch)
    settled = {"value": False}
    orch.recovery_precondition = lambda: settled["value"]
    await _start_without_loop(orch)

    clock.now = orch._recovery_retry_due_monotonic + 1
    due_before = orch._recovery_retry_due_monotonic
    calls_before = fake.get_all_positions_calls

    await orch._retry_recovery_if_due()
    assert fake.get_all_positions_calls == calls_before
    assert orch._recovery_retry_attempts == 0
    assert orch._recovery_retry_due_monotonic == due_before

    settled["value"] = True
    fake.fail = False
    await orch._retry_recovery_if_due()

    assert fake.get_all_positions_calls == calls_before + 1
    assert orch._recovery_retry_attempts == 1
    assert orch._recovery_ready is True


# ----------------------------------------------------------------------
# 10) recover_open_positions beklenmedik istisna
# ----------------------------------------------------------------------


async def test_unexpected_exception_in_retry_backs_off_and_does_not_raise(clock, monkeypatch):
    fake = FakeExchange()
    fake.fail = True
    orch = _make_orchestrator(fake, monkeypatch)
    await _start_without_loop(orch)

    async def boom(*_args: Any, **_kwargs: Any) -> bool:
        raise RuntimeError("beklenmedik {json: gövde}")

    monkeypatch.setattr(orch, "recover_open_positions", boom)
    clock.now = orch._recovery_retry_due_monotonic

    with LogCapture() as logs:
        await orch._retry_recovery_if_due()  # fırlatmamalı (loguru {} güvenli)
        failures = logs.messages("WARNING", "yeniden denemesi #1 ")

    assert orch._recovery_ready is False
    assert orch._recovery_retry_attempts == 1
    assert orch._recovery_retry_due_monotonic == clock.monotonic() + 60.0
    assert len(failures) == 1
    assert "RuntimeError" in failures[0]


# ----------------------------------------------------------------------
# 11) İdempotans: GERÇEK recover_open_positions + gerçek sqlite
# ----------------------------------------------------------------------


@pytest.fixture
async def db_sessionmaker(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'recovery.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(orch_module, "AsyncSessionLocal", maker)
    yield maker
    await engine.dispose()


async def _position_rows(maker, symbol: str = "BTCUSDT") -> List[PositionModel]:
    async with maker() as session:
        return list(
            (await session.execute(select(PositionModel).where(PositionModel.symbol == symbol)))
            .scalars()
            .all()
        )


async def test_recovery_twice_with_open_position_is_idempotent(db_sessionmaker, monkeypatch):
    fake = FakeExchange()
    fake.positions = [_btc_position()]
    fake.algo_orders = [dict(STOP_ORDER)]
    fake.risk_amounts["BTCUSDT"] = 0.01
    orch = _make_orchestrator(fake, monkeypatch)

    assert await orch.recover_open_positions() is True
    assert await orch.recover_open_positions() is True

    rows = await _position_rows(db_sessionmaker)
    assert len(rows) == 1
    assert rows[0].sl_order_id == "12345"
    assert len(orch.active_positions) == 1
    assert symbol_reservations.snapshot() == {"BTCUSDT": "telegram"}
    assert fake.orders == []
    assert fake.cancels == []


async def test_recovery_retry_after_unreadable_algo_orders_does_not_duplicate(
    db_sessionmaker, monkeypatch
):
    fake = FakeExchange()
    fake.positions = [_btc_position()]
    fake.algo_orders = [dict(STOP_ORDER)]
    fake.algo_fail = True
    fake.risk_amounts["BTCUSDT"] = 0.01
    orch = _make_orchestrator(fake, monkeypatch)

    assert await orch.recover_open_positions() is False  # koruma kanıtlanamadı
    assert len(orch.active_positions) == 1  # pozisyon yine de izlemede

    fake.algo_fail = False
    assert await orch.recover_open_positions() is True

    rows = await _position_rows(db_sessionmaker)
    assert len(rows) == 1
    assert rows[0].sl_order_id == "12345"
    assert len(orch.active_positions) == 1
    assert symbol_reservations.snapshot() == {"BTCUSDT": "telegram"}
    assert fake.orders == []
    assert fake.cancels == []


async def test_recovery_skips_scalper_owned_position_on_every_attempt(
    db_sessionmaker, monkeypatch
):
    async with db_sessionmaker() as session:
        session.add(
            ScalpTradeModel(
                strategy="C",
                symbol="BTCUSDT",
                direction="LONG",
                entry_price=50000.0,
                quantity=0.01,
                leverage=10,
                margin_usdt=50.0,
                signal_reason="test",
                status="OPEN",
            )
        )
        await session.commit()

    fake = FakeExchange()
    fake.positions = [_btc_position()]
    fake.algo_orders = [dict(STOP_ORDER)]
    orch = _make_orchestrator(fake, monkeypatch)

    assert await orch.recover_open_positions() is True
    assert await orch.recover_open_positions() is True

    assert await _position_rows(db_sessionmaker) == []
    assert orch.active_positions == {}
    assert "telegram" not in symbol_reservations.snapshot().values()
    assert fake.orders == []
    assert fake.cancels == []


async def test_recovery_without_stop_closes_once_and_stale_snapshot_adds_no_orders(
    db_sessionmaker, monkeypatch
):
    fake = FakeExchange()
    fake.positions = [_btc_position()]
    fake.algo_orders = []  # borsada stop YOK
    fake.risk_amounts["BTCUSDT"] = 0.01
    orch = _make_orchestrator(fake, monkeypatch)

    assert await orch.recover_open_positions() is True  # acil kapatıldı

    assert len(fake.orders) == 1
    order = fake.orders[0]
    assert order["path"] == "/fapi/v1/order"
    assert order["params"]["type"] == "MARKET"
    assert order["params"]["reduceOnly"] == "true"
    assert order["params"]["side"] == "SELL"
    rows = await _position_rows(db_sessionmaker)
    assert len(rows) == 1 and rows[0].status == PositionStatus.CLOSED
    assert orch.active_positions == {}
    assert symbol_reservations.snapshot() == {}

    # Hesap anlık görüntüsü BAYAT: hâlâ listeliyor; ama get_position_risk 0.
    # İki taze okuma çelişiyor: karar verilmez, kurtarma tamamlanmış sayılmaz.
    assert fake.risk_amounts["BTCUSDT"] == 0.0
    assert await orch.recover_open_positions() is False

    assert len(fake.orders) == 1  # ikinci denemede EK emir yok
    assert orch.active_positions == {}
    assert symbol_reservations.snapshot() == {"BTCUSDT": "telegram"}
    rows = await _position_rows(db_sessionmaker)
    assert len(rows) == 1 and rows[0].status == PositionStatus.CLOSED

    # Hesap listesi yetişti: süpürme rezervasyonu bırakır, kurtarma tamamlanır.
    fake.positions = []
    assert await orch.recover_open_positions() is True

    assert symbol_reservations.snapshot() == {}
    assert len(fake.orders) == 1
    rows = await _position_rows(db_sessionmaker)
    assert len(rows) == 1 and rows[0].status == PositionStatus.CLOSED


async def test_listed_orphan_reported_flat_is_not_adopted_and_recovery_stays_unsettled(
    db_sessionmaker, monkeypatch
):
    """Liste "açık", taze positionRisk "kapalı": yetim satır açılmaz ama pozisyon
    kapanmış da sayılmaz; rezervasyon tutulur, kurtarma tamamlanmaz."""
    fake = FakeExchange()
    fake.positions = [_btc_position()]
    fake.algo_orders = []
    orch = _make_orchestrator(fake, monkeypatch)

    assert await orch.recover_open_positions() is False

    assert await _position_rows(db_sessionmaker) == []
    assert orch.active_positions == {}
    assert symbol_reservations.snapshot() == {"BTCUSDT": "telegram"}
    assert fake.orders == []
    # Kurtarma kararı önbellekten verilmez: 5 sn'lik anlık görüntü sıfır olmayan
    # bayat bir miktarı doğrulamadan döndürür ve bu kontrolü boşa çıkarırdı.
    assert fake.risk_calls == [("BTCUSDT", {"force_fresh": True})]

    fake.positions = []  # hesap listesi yetişti
    assert await orch.recover_open_positions() is True
    assert symbol_reservations.snapshot() == {}


async def test_orphan_is_adopted_once_position_risk_agrees_with_the_list(
    db_sessionmaker, monkeypatch
):
    fake = FakeExchange()
    fake.positions = [_btc_position()]
    fake.algo_orders = [dict(STOP_ORDER)]
    orch = _make_orchestrator(fake, monkeypatch)

    assert await orch.recover_open_positions() is False
    assert await _position_rows(db_sessionmaker) == []
    assert symbol_reservations.snapshot() == {"BTCUSDT": "telegram"}

    fake.risk_amounts["BTCUSDT"] = 0.01  # positionRisk yetişti
    assert await orch.recover_open_positions() is True

    rows = await _position_rows(db_sessionmaker)
    assert len(rows) == 1 and rows[0].status == PositionStatus.OPEN
    assert set(orch.active_positions) == {"BTCUSDT"}
    assert symbol_reservations.snapshot() == {"BTCUSDT": "telegram"}
    assert fake.orders == []


async def test_orphan_is_adopted_when_live_amount_cannot_be_verified(
    db_sessionmaker, monkeypatch
):
    """Canlı miktar okunamazsa yetim pozisyon eskisi gibi benimsenir (fail-closed)."""
    fake = FakeExchange()
    fake.positions = [_btc_position()]
    fake.algo_orders = [dict(STOP_ORDER)]
    fake.risk_fail = True
    orch = _make_orchestrator(fake, monkeypatch)

    assert await orch.recover_open_positions() is True

    rows = await _position_rows(db_sessionmaker)
    assert len(rows) == 1 and rows[0].status == PositionStatus.OPEN
    assert set(orch.active_positions) == {"BTCUSDT"}
    assert symbol_reservations.snapshot() == {"BTCUSDT": "telegram"}
    # Doğrulama gerçekten denendi (benimseme eski davranış olduğu için test,
    # kontrol tümden silinse de geçerdi).
    assert fake.risk_calls == [("BTCUSDT", {"force_fresh": True})]


async def test_orphan_is_adopted_when_live_record_has_no_amount(db_sessionmaker, monkeypatch):
    """Miktar alanı olmayan (bozuk) yanıt "kapanmış" sayılmaz: yanlış bir "yok"
    hükmü canlı pozisyonu yönetimsiz bırakırdı."""
    fake = FakeExchange()
    fake.positions = [_btc_position()]
    fake.algo_orders = [dict(STOP_ORDER)]
    orch = _make_orchestrator(fake, monkeypatch)

    calls: List[tuple] = []

    async def no_amount(symbol: str, **kwargs: Any) -> Dict[str, Any]:
        calls.append((symbol, dict(kwargs)))
        return {"symbol": symbol}

    monkeypatch.setattr(fake, "get_position_risk", no_amount)

    assert await orch.recover_open_positions() is True

    rows = await _position_rows(db_sessionmaker)
    assert len(rows) == 1 and rows[0].status == PositionStatus.OPEN
    assert set(orch.active_positions) == {"BTCUSDT"}
    assert symbol_reservations.snapshot() == {"BTCUSDT": "telegram"}
    assert calls == [("BTCUSDT", {"force_fresh": True})]  # taze kontrol gerçekten yapıldı


async def test_disagreeing_reads_leave_tracked_position_to_the_monitor(
    db_sessionmaker, monkeypatch
):
    """BTC izlemeye alındı, ETH'te istisna -> oturum geri alındı (satır yok).
    Sonraki denemede liste "açık", positionRisk "kapalı": izlemedeki pozisyon
    izleme döngüsünün işidir; kurtarma ne düşürür ne rezervasyonunu bırakır."""
    fake = FakeExchange()
    fake.positions = [_btc_position(), _eth_position()]
    fake.algo_orders = [dict(STOP_ORDER)]
    fake.risk_amounts["BTCUSDT"] = 0.01
    fake.risk_amounts["ETHUSDT"] = 0.5
    orch = _make_orchestrator(fake, monkeypatch)
    original = orch._ensure_protected

    async def flaky(position: Any) -> Optional[bool]:
        if position.symbol == "ETHUSDT":
            raise RuntimeError("beklenmedik")
        return None

    monkeypatch.setattr(orch, "_ensure_protected", flaky)
    with pytest.raises(RuntimeError):
        await orch.recover_open_positions()

    assert set(orch.active_positions) == {"BTCUSDT"}
    assert symbol_reservations.snapshot() == {"BTCUSDT": "telegram", "ETHUSDT": "telegram"}

    monkeypatch.setattr(orch, "_ensure_protected", original)
    fake.positions = [_btc_position()]
    fake.risk_amounts["BTCUSDT"] = 0.0

    assert await orch.recover_open_positions() is False

    assert set(orch.active_positions) == {"BTCUSDT"}
    assert symbol_reservations.snapshot() == {"BTCUSDT": "telegram"}  # ETH süpürüldü
    assert await _position_rows(db_sessionmaker, "BTCUSDT") == []
    assert await _position_rows(db_sessionmaker, "ETHUSDT") == []
    assert fake.orders == []


async def test_retry_that_emergency_closes_drops_previously_tracked_position(
    db_sessionmaker, monkeypatch
):
    """İlk denemede koruma OKUNAMADI (izlemeye alındı); ikincide stop YOK çıktı.

    Acil kapatılan pozisyon izleme listesinde bayat kalmamalı: rezervasyon
    bırakıldıktan sonra başka motor aynı sembolü açarsa orchestrator onu kendi
    pozisyonu sanıp yönetmeye kalkardı (çift yönetim).
    """
    fake = FakeExchange()
    fake.positions = [_btc_position()]
    fake.algo_fail = True
    fake.risk_amounts["BTCUSDT"] = 0.01
    orch = _make_orchestrator(fake, monkeypatch)

    assert await orch.recover_open_positions() is False
    assert set(orch.active_positions) == {"BTCUSDT"}
    assert symbol_reservations.snapshot() == {"BTCUSDT": "telegram"}

    fake.algo_fail = False
    fake.algo_orders = []  # koruma artık okunuyor: stop YOK
    assert await orch.recover_open_positions() is True

    assert orch.active_positions == {}
    assert symbol_reservations.snapshot() == {}
    assert len(fake.orders) == 1
    assert fake.orders[0]["params"]["reduceOnly"] == "true"
    rows = await _position_rows(db_sessionmaker)
    assert len(rows) == 1 and rows[0].status == PositionStatus.CLOSED


async def test_retry_releases_reservation_leaked_by_failed_attempt_once_position_is_gone(
    clock, db_sessionmaker, monkeypatch
):
    """Başarısız deneme rezervasyonu açık bırakır (fail-closed); pozisyon sonra
    kapanırsa döngü o sembole bir daha uğramaz -> artık rezervasyon bırakılmalı."""
    symbol_reservations.reserve("SOLUSDT", "scalper")
    fake = FakeExchange()
    fake.fail = True
    orch = _make_orchestrator(fake, monkeypatch)
    await _start_without_loop(orch)

    fake.fail = False
    fake.positions = [_btc_position()]
    fake.risk_amounts["BTCUSDT"] = 0.01

    async def boom(_position: Any) -> Optional[bool]:
        raise RuntimeError("beklenmedik")

    monkeypatch.setattr(orch, "_ensure_protected", boom)
    clock.now = orch._recovery_retry_due_monotonic
    await orch._retry_recovery_if_due()

    assert orch._recovery_ready is False
    assert orch.active_positions == {}
    assert symbol_reservations.snapshot() == {"SOLUSDT": "scalper", "BTCUSDT": "telegram"}

    fake.positions = []  # pozisyon arada kapandı
    clock.now = orch._recovery_retry_due_monotonic
    await orch._retry_recovery_if_due()

    assert orch._recovery_ready is True
    assert symbol_reservations.snapshot() == {"SOLUSDT": "scalper"}


async def test_tracked_position_missing_from_list_keeps_reservation_for_the_monitor(
    db_sessionmaker, monkeypatch
):
    """İzlemedeki pozisyon listeden düştüyse onu izleme döngüsü muhasebeyle
    kapatır; kurtarma rezervasyonuna dokunmamalı."""
    fake = FakeExchange()
    fake.positions = [_btc_position()]
    fake.algo_fail = True
    fake.risk_amounts["BTCUSDT"] = 0.01
    orch = _make_orchestrator(fake, monkeypatch)

    assert await orch.recover_open_positions() is False  # izlemede, koruma bilinmiyor

    fake.positions = []
    assert await orch.recover_open_positions() is True

    assert set(orch.active_positions) == {"BTCUSDT"}
    assert symbol_reservations.snapshot() == {"BTCUSDT": "telegram"}


async def test_entry_halt_keeps_untracked_reservation(monkeypatch):
    """Giriş durdurulduysa izlenmeyen rezervasyon kanıttır; insan bakmalı."""
    fake = FakeExchange()
    orch = _make_orchestrator(fake, monkeypatch)
    symbol_reservations.reserve("ETHUSDT", "telegram")
    orch._entry_halted = True

    assert await orch.recover_open_positions() is True

    assert symbol_reservations.snapshot() == {"ETHUSDT": "telegram"}


async def test_reservation_of_in_flight_signal_is_not_released(monkeypatch):
    """Uçuştaki giriş, pozisyonu izlemeye almadan önce sembolü rezerve eder."""
    fake = FakeExchange()
    orch = _make_orchestrator(fake, monkeypatch)
    symbol_reservations.reserve("ETHUSDT", "telegram")

    async with orch._signal_lock:
        assert await orch.recover_open_positions() is True

    assert symbol_reservations.snapshot() == {"ETHUSDT": "telegram"}


async def test_sweep_keeps_reservation_of_symbol_still_listed(monkeypatch):
    """Listede hâlâ açık görünen sembol süpürülmez: döngü ona gelene kadar araya
    DB/REST beklemeleri girer ve o arada başka motor sembolü sahiplenebilirdi."""
    orch = _make_orchestrator(FakeExchange(), monkeypatch)
    symbol_reservations.reserve("BTCUSDT", "telegram")

    orch._release_stale_reservations([_btc_position()])

    assert symbol_reservations.snapshot() == {"BTCUSDT": "telegram"}


# ----------------------------------------------------------------------
# 12) main._scalper_recovery_settled ve lifespan bağlaması
# ----------------------------------------------------------------------


async def test_lifespan_wires_scalper_precondition_before_orchestrator_start(monkeypatch):
    """Bağlama satırı silinirse ya da start()'tan SONRAya taşınırsa bu test kalır."""
    seen: Dict[str, Any] = {}

    class FakeOrchestrator:
        def __init__(self) -> None:
            self.active_positions: Dict[str, Any] = {}
            self.monitoring_task = None
            self.recovery_precondition = None

        async def start(self) -> None:
            seen["precondition_at_start"] = self.recovery_precondition

        async def close(self) -> None:
            return None

    class FakeTelegram:
        def __init__(self, orchestrator: Any) -> None:
            self.orchestrator = orchestrator

        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

    # lifespan modül globallerini doğrudan atar; test sonunda eski değerler dönsün.
    for name in ("orchestrator", "telegram_bot", "scalper_engine", "follower_engine"):
        monkeypatch.setattr(main_module, name, getattr(main_module, name))
    monkeypatch.setattr(main_module, "init_db", AsyncMock())
    monkeypatch.setattr(
        main_module, "_check_disabled_follower_open_trades", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(main_module, "close_db", AsyncMock())
    monkeypatch.setattr(main_module, "TradingOrchestrator", FakeOrchestrator)
    monkeypatch.setattr(main_module, "TelegramBotService", FakeTelegram)
    monkeypatch.setattr(main_module.settings, "scalper_enabled", False)

    async with main_module.lifespan(SimpleNamespace()):
        pass

    assert seen["precondition_at_start"] is main_module._scalper_recovery_settled


def test_scalper_recovery_settled_follows_engine_flag(monkeypatch):
    monkeypatch.setattr(main_module, "scalper_engine", None)
    assert main_module._scalper_recovery_settled() is True

    engine = SimpleNamespace(_recovery_ready=False)
    monkeypatch.setattr(main_module, "scalper_engine", engine)
    assert main_module._scalper_recovery_settled() is False

    engine._recovery_ready = True
    assert main_module._scalper_recovery_settled() is True

    monkeypatch.setattr(main_module, "scalper_engine", SimpleNamespace())
    assert main_module._scalper_recovery_settled() is False
