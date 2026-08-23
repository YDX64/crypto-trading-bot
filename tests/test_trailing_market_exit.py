"""D22 (daraltılmış) — `-2021` sonrası acil kapanışın DÜRÜST kaydı.

Kusur (2026-08-23 canlı log, 3 olay: DOGE/BNB/ETH):
  1. `exits._update_trailing` chandelier stopunu piyasanın YANLIŞ tarafına
     gönderiyordu,
  2. Binance `-2021 Order would immediately trigger` dönüyordu,
  3. `position_manager._replace_stop_loss` bunu bir çıkış kararı sayıp
     pozisyonu reduce-only MARKET ile KAPATIYORDU,
  4. ama `_update_trailing` `False` görüp "trailing SL güncellenemedi, eski
     SL korunuyor" logluyordu — pozisyon YOKKEN,
  5. ve kapanış bir sonraki turda `exit_reason=TRAIL` olarak deftere
     giriyordu; yani defter "iz tetiklendi" derken gerçekte bot piyasa
     emriyle çıkmıştı.

D22'nin İLK hâli buna "gönderMEDEN önce kendi fiyat okumanla karar ver ve
ÖNDEN piyasa emri gönder" diye cevap veriyordu; 12-ajanlık düşmanca inceleme
bunu REDDETTİ (bkz. docs/DECISIONS.md D22 "Reddedilenler"). Bu dosya
DARALTILMIŞ sözleşmeyi çiviler:

  * (A) AYNI host'ta kapı YOKTUR: stop borsaya GÖNDERİLİR. Bot kendi
    fiyat okumasına dayanarak geri alınamaz bir piyasa emri göndermez.
    Ayrı market-data host'unda (D17) kapı aynen durur.
  * (B) `-2021 → _emergency_close` gerçekleştiğinde kayıt DÜRÜSTTÜR:
    "acil kapanış gerçekleşti" loglanır (ASLA "eski SL korunuyor"),
    defter `TRAIL_MARKET`/`BE_MARKET` yazar, İKİNCİ BİR MARKET emri
    GÖNDERİLMEZ, etiket doğrulanamayan turda bile kaybolmaz ve kapanış
    fiyatı acil kapanış emrinin GERÇEK dolumundan okunur.
  * (F) beklenen `-2011` INFO seviyesindedir (DEBUG değil — üretimde
    DEBUG kapalıdır ve defter sapmasının izi tamamen kaybolurdu).
"""

import time
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest

from src.strategies.scalper.exits import (
    EXIT_REASON_BE_MARKET,
    EXIT_REASON_TRAIL_MARKET,
    MARKET_EXIT_REASONS,
    TRAIL_EXIT_REASONS,
    ExitManager,
)
from src.strategies.scalper.types import Candle, Direction
from src.trading.binance_client_improved import (
    BinanceAPIError,
    is_benign_cancel_error,
)
from src.trading.position_manager import (
    EmergencyCloseResult,
    PositionManager,
    StopReplaceResult,
)


# ---------------------------------------------------------------------------
# Ortak çiftler
# ---------------------------------------------------------------------------

def _candles(closes: List[float]) -> List[Candle]:
    out: List[Candle] = []
    for i, close in enumerate(closes):
        out.append(
            Candle(
                open_time=i * 60_000,
                open=close,
                high=close * 1.002,
                low=close * 0.998,
                close=close,
                volume=100.0,
                close_time=i * 60_000 + 59_999,
            )
        )
    return out


_RISING = _candles([100.0 + i * 0.1 for i in range(60)])


def _sp(current_price: float, *, current_stoploss: float = 90.0) -> Any:
    return SimpleNamespace(
        trade_id=42,
        signal=SimpleNamespace(direction=Direction.LONG, entry_price=100.0),
        position=SimpleNamespace(
            symbol="BTCUSDT",
            entry_price=100.0,
            current_price=current_price,
            current_stoploss=current_stoploss,
            quantity=2.0,
            opened_at=datetime.now(timezone.utc),
            entry_order_id="1",
            sl_order_id=None,
        ),
        plan=SimpleNamespace(
            breakeven_price=100.1,
            runner_floor_price=None,
            tp1_price=101.0,
            initial_stop=95.0,
            tp1_algo_id=None,
            tp2_algo_id=None,
            tp1_quantity=0.8,
            tp2_quantity=0.6,
            entry_fee_rate=0.0004,
        ),
        entry_candle_time=0,
        mae_pct=-1.0,
        mfe_pct=1.0,
        tp1_done=False,
        tp2_done=False,
        trailing_active=True,
        pending_exit_reason=None,
        market_close_order_id=None,
        market_close_price=None,
    )


class _Logger:
    """Seviye başına satırları toplayan minimal logger."""

    def __init__(self) -> None:
        self.lines: Dict[str, List[str]] = {
            "debug": [], "info": [], "warning": [], "error": [], "critical": []
        }

    def _add(self, level: str):
        def _log(message, *a, **kw):
            self.lines[level].append(str(message))
        return _log

    def __getattr__(self, name):
        if name in ("debug", "info", "warning", "error", "critical"):
            return self._add(name)
        raise AttributeError(name)

    def text(self, level: str) -> str:
        return "\n".join(self.lines[level])

    def all_text(self) -> str:
        return "\n".join("\n".join(v) for v in self.lines.values())


class _Client:
    """`get_position_risk` dışında HİÇBİR emir yolu sunmayan istemci çifti.

    Kasıtlı: `_finalize_market_exit` bir emir göndermeye kalkarsa test
    `AttributeError` ile patlar — "ikinci MARKET yok" sözleşmesi böyle
    çivilenir.
    """

    def __init__(self, amounts: List[float]) -> None:
        self._amounts = list(amounts)
        self.force_fresh_calls: List[bool] = []
        self.cancel_all_open_orders = AsyncMock()
        self.get_current_price = AsyncMock(return_value=99.0)
        self.get_income_history = AsyncMock(return_value=[])
        self.get_order = AsyncMock(
            return_value={"updateTime": int(time.time() * 1000) - 5000}
        )

    async def get_position_risk(self, symbol, force_fresh=False):
        self.force_fresh_calls.append(bool(force_fresh))
        amt = self._amounts.pop(0) if self._amounts else 0.0
        return {"positionAmt": str(amt)}


def _manager(
    *,
    replace_result: Any = None,
    fresh_price: bool = True,
    market_url: str = "",
    amounts: List[float] = None,
) -> ExitManager:
    """`__init__` çalıştırmadan kurulan ExitManager (repo konvansiyonu)."""
    mgr = ExitManager.__new__(ExitManager)
    mgr.cfg = SimpleNamespace(
        scalper_market_data_base_url=market_url,
        binance_base_url="https://testnet.binancefuture.com",
        scalper_tf_entry="5m",
        scalper_chandelier_atr_period=14,
        scalper_chandelier_atr_mult=3.0,
        scalper_trail_mult_tiers="",
        scalper_tp1_fraction=0.4,
        scalper_tp2_fraction=0.3,
        scalper_forensics_enabled=False,
    )
    mgr.logger = _Logger()
    mgr._market_data_down_reason = None
    mgr._trading_price_seen_at = (
        {"BTCUSDT": time.monotonic()} if fresh_price else {}
    )
    mgr._trailing_space_skips = 0
    mgr._trailing_gate_skips = 0
    mgr._trailing_market_exits = 0
    mgr._trailing_skip_log_at = {}
    mgr._data_price_error_log_at = {}
    mgr._positions = {}
    mgr._closing = set()
    mgr.data_price_fetch = None
    mgr.client = _Client([0.0] if amounts is None else amounts)
    mgr.finalized: List[Dict[str, Any]] = []

    async def fake_handle_closed(symbol, sp, *, forced_exit_reason=None):
        mgr.finalized.append({"symbol": symbol, "reason": forced_exit_reason})

    mgr._handle_closed = fake_handle_closed

    async def kline_fetch(symbol, tf, limit):
        return _RISING

    mgr.kline_fetch = kline_fetch

    mgr.replace_calls: List[float] = []

    async def replace_result_fn(position, new_stop):
        mgr.replace_calls.append(new_stop)
        # DİKKAT: `replace_result or ...` YAZILAMAZ — StopReplaceResult
        # bilerek falsy'dir (`__bool__` = ok), `or` onu yutardı.
        if replace_result is not None:
            return replace_result
        return StopReplaceResult(True, "replaced")

    mgr.pm = SimpleNamespace(replace_stop_loss_result=replace_result_fn)
    return mgr


_EMERGENCY = StopReplaceResult(
    False, "emergency_closed", -2021, close_order_id="777", close_price=99.5
)


# ---------------------------------------------------------------------------
# (A) AYNI host'ta ÖNDEN piyasa kapanışı YOK — emir gönderilir
# ---------------------------------------------------------------------------

class TestSameHostSendsTheOrder:
    async def test_wrong_side_stop_is_still_sent_on_the_same_host(self):
        """D22 öncesi davranış: kapı YOK, emir borsaya gider.

        Bot kendi fiyat okumasına dayanarak "iz çoktan tetiklendi" hükmü
        verip geri alınamaz bir piyasa emri GÖNDERMEZ; hükmü BORSA verir
        (-2021) ve mevcut acil kapanış yolu çalışır.
        """
        mgr = _manager()
        # Chandelier ≈ 104.8 ama işlem fiyatı 100.0: LONG stop fiyatın ÜSTÜNDE.
        await mgr._update_trailing("BTCUSDT", _sp(100.0))

        assert len(mgr.replace_calls) == 1, "stop borsaya gönderilmeliydi"
        assert mgr.trailing_skip_snapshot()["protective_gate_skips"] == 0
        assert mgr.finalized == []

    async def test_stale_price_does_not_block_the_order_on_the_same_host(self):
        """Bayat fiyat kapısı da kalktı: kapı yoksa fiyata da ihtiyaç yok."""
        mgr = _manager(fresh_price=False)
        await mgr._update_trailing("BTCUSDT", _sp(100.0))
        assert len(mgr.replace_calls) == 1

    async def test_protective_stop_is_sent_unchanged(self):
        mgr = _manager()
        await mgr._update_trailing("BTCUSDT", _sp(106.0))
        assert len(mgr.replace_calls) == 1
        assert mgr.trailing_skip_snapshot()["market_exits"] == 0


# ---------------------------------------------------------------------------
# (A) Ayrı market-data host'u (D17) — kapı AYNEN durur
# ---------------------------------------------------------------------------

class TestSeparateHostGateUnchanged:
    async def test_wrong_side_stop_is_not_sent_on_a_separate_host(self):
        """Ayrı host'ta yanlış taraf BAZ hatası olabilir → tur atlanır."""
        mgr = _manager(market_url="https://fapi.binance.com")

        async def data_price(symbol):
            return 100.0

        mgr.data_price_fetch = data_price
        await mgr._update_trailing("BTCUSDT", _sp(100.0))

        assert mgr.replace_calls == []
        assert mgr.trailing_skip_snapshot()["protective_gate_skips"] == 1
        assert mgr.finalized == []
        assert "eski SL" in mgr.logger.text("warning")


# ---------------------------------------------------------------------------
# (B) -2021 → acil kapanış: DÜRÜST kayıt
# ---------------------------------------------------------------------------

class TestEmergencyCloseIsRecordedHonestly:
    async def test_trailing_emergency_close_is_labelled_trail_market(self):
        mgr = _manager(replace_result=_EMERGENCY, amounts=[0.0])
        sp = _sp(106.0)
        await mgr._update_trailing("BTCUSDT", sp)

        assert len(mgr.replace_calls) == 1
        assert mgr.finalized == [
            {"symbol": "BTCUSDT", "reason": EXIT_REASON_TRAIL_MARKET}
        ]
        assert mgr.trailing_skip_snapshot()["market_exits"] == 1
        assert "eski SL korunuyor" not in mgr.logger.all_text()
        assert "ACİL KAPANIŞ GERÇEKLEŞTİ" in mgr.logger.text("warning")

    async def test_no_second_market_order_is_submitted(self):
        """`_finalize_market_exit` YALNIZ doğrular; emir göndermez.

        `_Client` bilerek hiçbir emir metodu sunmaz — bir kapanış emri
        denenirse test AttributeError ile patlar.
        """
        mgr = _manager(replace_result=_EMERGENCY, amounts=[0.0])
        await mgr._update_trailing("BTCUSDT", _sp(106.0))
        # Doğrulama TAZE okumayla yapılır (önbellek geri alınamaz kararda
        # kullanılmaz — D10 dersi #2).
        assert mgr.client.force_fresh_calls == [True]

    async def test_label_survives_an_unverified_close(self):
        """Borsa hâlâ miktar gösteriyorsa etiket `sp`de KALIR ve SL/TP'ye
        dokunulmaz; sonraki tur aynı etiketi kullanır."""
        mgr = _manager(replace_result=_EMERGENCY, amounts=[1.0] * 6)
        sp = _sp(106.0)
        await mgr._update_trailing("BTCUSDT", sp)

        assert mgr.finalized == [], "doğrulanmadan finalize edilmemeliydi"
        assert sp.pending_exit_reason == EXIT_REASON_TRAIL_MARKET
        assert "koruma emirleri iptal EDİLMEDİ" in mgr.logger.text("error")

    async def test_pending_label_is_used_by_the_next_finalizer(self):
        """-2022 / doğrulanamayan tur sonrası etiket KAYBOLMAZ."""
        mgr = _manager()
        # gerçek `_handle_closed`ı geri koy, `_finalize_close`u yakala
        del mgr._handle_closed
        captured: List[Any] = []

        async def fake_finalize(symbol, sp, *, forced_exit_reason=None):
            captured.append(forced_exit_reason)

        mgr._finalize_close = fake_finalize
        sp = _sp(106.0)
        sp.pending_exit_reason = EXIT_REASON_TRAIL_MARKET
        mgr._positions["BTCUSDT"] = sp

        await mgr._handle_closed("BTCUSDT", sp)   # etiket VERİLMEDİ
        assert captured == [EXIT_REASON_TRAIL_MARKET]

    async def test_close_order_id_and_price_are_recorded(self):
        mgr = _manager(replace_result=_EMERGENCY, amounts=[0.0])
        sp = _sp(106.0)
        await mgr._update_trailing("BTCUSDT", sp)
        assert sp.market_close_order_id == "777"
        assert sp.market_close_price == 99.5

    async def test_failed_result_keeps_the_old_stop_and_says_so(self):
        """Gerçekten "eski SL yerinde" olan TEK durum."""
        mgr = _manager(replace_result=StopReplaceResult(False, "failed", -1021))
        await mgr._update_trailing("BTCUSDT", _sp(106.0))

        assert mgr.finalized == []
        assert "eski SL korunuyor" in mgr.logger.text("warning")

    async def test_no_position_result_does_not_claim_old_stop(self):
        mgr = _manager(replace_result=StopReplaceResult(False, "no_position"))
        await mgr._update_trailing("BTCUSDT", _sp(106.0))

        assert mgr.finalized == []
        assert "eski SL korunuyor" not in mgr.logger.all_text()

    async def test_legacy_pm_without_structured_result_still_works(self):
        """Yalnız `replace_stop_loss` sunan eski `pm` ile davranış değişmez."""
        mgr = _manager()
        calls: List[float] = []

        async def legacy(position, new_stop):
            calls.append(new_stop)
            return True

        mgr.pm = SimpleNamespace(replace_stop_loss=legacy)
        await mgr._update_trailing("BTCUSDT", _sp(106.0))
        assert len(calls) == 1


# ---------------------------------------------------------------------------
# (B) Aynı sözleşme BE / runner / harici tetik yollarında da geçerli
# ---------------------------------------------------------------------------

class TestOtherStopPathsAreLabelledToo:
    async def test_tp1_breakeven_emergency_close_is_be_market(self):
        mgr = _manager(replace_result=_EMERGENCY, amounts=[0.0])
        mgr._confirmed_algo_fill = AsyncMock(return_value=True)
        sp = _sp(106.0)
        await mgr._check_tp1("BTCUSDT", sp, live_qty=1.0)

        assert mgr.finalized == [
            {"symbol": "BTCUSDT", "reason": EXIT_REASON_BE_MARKET}
        ]
        assert sp.tp1_done is False
        assert "eski SL korunuyor" not in mgr.logger.all_text()

    async def test_tp2_runner_floor_emergency_close_is_trail_market(self):
        mgr = _manager(replace_result=_EMERGENCY, amounts=[0.0])
        mgr._confirmed_algo_fill = AsyncMock(return_value=True)
        sp = _sp(106.0)
        sp.plan.tp2_quantity = 0.6
        sp.position.quantity = 2.0
        await mgr._check_tp2("BTCUSDT", sp, live_qty=0.2)

        assert mgr.finalized == [
            {"symbol": "BTCUSDT", "reason": EXIT_REASON_TRAIL_MARKET}
        ]
        assert sp.tp2_done is False

    async def test_force_stop_to_emergency_close_is_labelled(self):
        mgr = _manager(replace_result=_EMERGENCY, amounts=[0.0])
        sp = _sp(106.0)
        ok = await mgr.force_stop_to("BTCUSDT", sp, 105.0, reason="yapı")

        assert ok is False
        assert mgr.finalized == [
            {"symbol": "BTCUSDT", "reason": EXIT_REASON_TRAIL_MARKET}
        ]
        assert "eski SL korunuyor" not in mgr.logger.all_text()

    async def test_force_breakeven_emergency_close_is_be_market(self):
        mgr = _manager(replace_result=_EMERGENCY, amounts=[0.0])
        sp = _sp(106.0)
        mgr._positions["BTCUSDT"] = sp
        mgr.breakeven_side_ok = lambda symbol: True

        ok = await mgr.force_breakeven("BTCUSDT", reason="TV olayı")

        assert ok is False
        assert mgr.finalized == [
            {"symbol": "BTCUSDT", "reason": EXIT_REASON_BE_MARKET}
        ]
        assert "eski SL korunuyor" not in mgr.logger.all_text()

    async def test_follower_tp1_emergency_close_is_be_market(self):
        """Takipçi halkası (D20) aynı yapılandırılmış sonucu kullanır."""
        from src.strategies.follower.exits import FollowerExitManager

        mgr = FollowerExitManager.__new__(FollowerExitManager)
        mgr.cfg = SimpleNamespace(
            scalper_tp1_fraction=0.4,
            scalper_tp2_fraction=0.3,
            scalper_forensics_enabled=False,
        )
        mgr.logger = _Logger()
        mgr._positions = {}
        mgr._closing = set()
        mgr.finalized = []

        async def fake_handle_closed(symbol, sp, *, forced_exit_reason=None):
            mgr.finalized.append(forced_exit_reason)

        mgr._handle_closed = fake_handle_closed
        mgr.client = _Client([0.0])
        mgr._trailing_market_exits = 0
        mgr._confirmed_algo_fill = AsyncMock(return_value=True)

        async def replace_result_fn(position, new_stop):
            return _EMERGENCY

        mgr.pm = SimpleNamespace(replace_stop_loss_result=replace_result_fn)

        sp = _sp(106.0)
        sp.position.quantity = 3.0
        sp.plan.tp1_quantity = 1.0
        sp.tp1_filled = False
        await mgr._check_tp1_breakeven("BTCUSDT", sp, live_qty=2.0)

        assert mgr.finalized == [EXIT_REASON_BE_MARKET]
        assert "eski SL korunuyor" not in mgr.logger.all_text()


# ---------------------------------------------------------------------------
# `position_manager` yapılandırılmış sonuçları
# ---------------------------------------------------------------------------

def _position():
    from src.models.position import PositionSide

    return SimpleNamespace(
        symbol="BTCUSDT", side=PositionSide.LONG, sl_order_id="55",
    )


class TestStopReplaceResult:
    def test_bool_contract_is_preserved(self):
        assert bool(StopReplaceResult(True, "replaced")) is True
        assert bool(StopReplaceResult(False, "failed")) is False
        assert not StopReplaceResult(False, "emergency_closed")
        assert bool(EmergencyCloseResult(True)) is True
        assert not EmergencyCloseResult(False)

    async def test_minus_2021_reports_emergency_closed_with_fill(self):
        pm = PositionManager.__new__(PositionManager)
        pm.logger = _Logger()
        pm.binance = SimpleNamespace(
            get_position_risk=AsyncMock(return_value={"positionAmt": "1.0"}),
            place_stop_loss=AsyncMock(
                side_effect=BinanceAPIError(400, -2021, "would immediately trigger")
            ),
        )
        pm._emergency_close = AsyncMock(
            return_value=EmergencyCloseResult(True, "987", 101.25, 1.0)
        )

        result = await pm._replace_stop_loss_result(_position(), 101.0)

        assert result.outcome == "emergency_closed"
        assert result.error_code == -2021
        assert bool(result) is False
        assert (result.close_order_id, result.close_price) == ("987", 101.25)
        pm._emergency_close.assert_awaited_once()

    async def test_legacy_bool_emergency_close_still_supported(self):
        """`AsyncMock(return_value=True)` gibi eski çiftler bozulmaz."""
        pm = PositionManager.__new__(PositionManager)
        pm.logger = _Logger()
        pm.binance = SimpleNamespace(
            get_position_risk=AsyncMock(return_value={"positionAmt": "1.0"}),
            place_stop_loss=AsyncMock(
                side_effect=BinanceAPIError(400, -2021, "would immediately trigger")
            ),
        )
        pm._emergency_close = AsyncMock(return_value=True)
        result = await pm._replace_stop_loss_result(_position(), 101.0)
        assert result.outcome == "emergency_closed"
        assert result.close_order_id is None

    async def test_other_error_reports_failed(self):
        pm = PositionManager.__new__(PositionManager)
        pm.logger = _Logger()
        pm.binance = SimpleNamespace(
            get_position_risk=AsyncMock(return_value={"positionAmt": "1.0"}),
            place_stop_loss=AsyncMock(
                side_effect=BinanceAPIError(400, -1111, "precision")
            ),
        )
        result = await pm._replace_stop_loss_result(_position(), 101.0)
        assert (result.outcome, bool(result)) == ("failed", False)

    async def test_flat_position_reports_no_position(self):
        pm = PositionManager.__new__(PositionManager)
        pm.logger = _Logger()
        pm.binance = SimpleNamespace(
            get_position_risk=AsyncMock(return_value={"positionAmt": "0"}),
        )
        result = await pm._replace_stop_loss_result(_position(), 101.0)
        assert result.outcome == "no_position"

    async def test_emergency_close_returns_the_order_identity(self):
        pm = PositionManager.__new__(PositionManager)
        pm.logger = _Logger()
        pm.binance = SimpleNamespace(
            get_position_risk=AsyncMock(return_value={"positionAmt": "1.5"}),
            _request_with_retry=AsyncMock(
                return_value={"orderId": 4242, "avgPrice": "98.75"}
            ),
            cancel_all_open_orders=AsyncMock(),
        )
        result = await pm._emergency_close("BTCUSDT")
        assert bool(result) is True
        assert (result.order_id, result.avg_price, result.quantity) == (
            "4242", 98.75, 1.5
        )

    async def test_emergency_close_leaves_price_none_when_not_filled_yet(self):
        pm = PositionManager.__new__(PositionManager)
        pm.logger = _Logger()
        pm.binance = SimpleNamespace(
            get_position_risk=AsyncMock(return_value={"positionAmt": "-1.0"}),
            _request_with_retry=AsyncMock(
                return_value={"orderId": 7, "avgPrice": "0.00"}
            ),
            cancel_all_open_orders=AsyncMock(),
        )
        result = await pm._emergency_close("BTCUSDT")
        assert result.order_id == "7"
        assert result.avg_price is None, "uydurma fiyat yazılmamalı"


# ---------------------------------------------------------------------------
# Defter: etiket + GERÇEK kapanış fiyatı
# ---------------------------------------------------------------------------

def _ledger_manager(tracker, client) -> ExitManager:
    return ExitManager(
        client=client,
        pm=SimpleNamespace(),
        tracker=tracker,
        cfg=SimpleNamespace(
            scalper_tp1_fraction=0.4,
            scalper_tp2_fraction=0.3,
            scalper_chandelier_atr_mult=3.0,
            scalper_chandelier_atr_period=22,
            scalper_breakeven_buffer_pct=0.05,
        ),
        kline_fetch=AsyncMock(return_value=[]),
    )


class TestLedgerLabel:
    async def test_forced_reason_is_written_to_the_trade_record(self):
        client = SimpleNamespace(
            cancel_all_open_orders=AsyncMock(),
            get_current_price=AsyncMock(return_value=99.0),
            get_income_history=AsyncMock(return_value=[]),
            get_order=AsyncMock(
                return_value={"updateTime": int(time.time() * 1000) - 5000}
            ),
        )
        tracker = SimpleNamespace(record_close=AsyncMock())
        mgr = _ledger_manager(tracker, client)
        mgr.INCOME_RETRY_DELAYS = (0.0,)
        sp = _sp(99.0)
        mgr._positions["BTCUSDT"] = sp

        await mgr._handle_closed(
            "BTCUSDT", sp, forced_exit_reason=EXIT_REASON_TRAIL_MARKET
        )

        kwargs = tracker.record_close.await_args.kwargs
        assert kwargs["exit_reason"] == EXIT_REASON_TRAIL_MARKET
        assert "BTCUSDT" not in mgr._positions

    async def test_exit_price_comes_from_the_real_close_fill(self):
        """Kapanış fiyatı ticker'dan TAHMİN edilmez; emrin dolumundan okunur."""
        client = SimpleNamespace(
            cancel_all_open_orders=AsyncMock(),
            get_current_price=AsyncMock(return_value=1234.0),   # tuzak
            get_income_history=AsyncMock(return_value=[]),
            get_order=AsyncMock(
                return_value={"updateTime": int(time.time() * 1000) - 5000}
            ),
            get_account_trades=AsyncMock(return_value=[
                {"qty": "1.0", "price": "98.0"},
                {"qty": "1.0", "price": "100.0"},
            ]),
        )
        tracker = SimpleNamespace(record_close=AsyncMock())
        mgr = _ledger_manager(tracker, client)
        mgr.INCOME_RETRY_DELAYS = (0.0,)
        sp = _sp(99.0)
        sp.market_close_order_id = "4242"
        mgr._positions["BTCUSDT"] = sp

        await mgr._handle_closed(
            "BTCUSDT", sp, forced_exit_reason=EXIT_REASON_TRAIL_MARKET
        )

        kwargs = tracker.record_close.await_args.kwargs
        assert kwargs["exit_price"] == pytest.approx(99.0)   # VWAP
        assert "exit_fill=market_close_order" in (kwargs["notes"] or "")

    async def test_avg_price_is_used_when_trades_are_unavailable(self):
        client = SimpleNamespace(
            cancel_all_open_orders=AsyncMock(),
            get_current_price=AsyncMock(return_value=1234.0),
            get_income_history=AsyncMock(return_value=[]),
            get_order=AsyncMock(
                return_value={"updateTime": int(time.time() * 1000) - 5000}
            ),
            get_account_trades=AsyncMock(side_effect=RuntimeError("ağ")),
        )
        tracker = SimpleNamespace(record_close=AsyncMock())
        mgr = _ledger_manager(tracker, client)
        mgr.INCOME_RETRY_DELAYS = (0.0,)
        sp = _sp(99.0)
        sp.market_close_order_id = "4242"
        sp.market_close_price = 97.5
        mgr._positions["BTCUSDT"] = sp

        await mgr._handle_closed(
            "BTCUSDT", sp, forced_exit_reason=EXIT_REASON_TRAIL_MARKET
        )
        assert tracker.record_close.await_args.kwargs["exit_price"] == 97.5

    def test_anomalous_fill_rows_are_rejected_wholesale(self):
        assert ExitManager._fill_vwap([{"qty": "1", "price": "0"}]) is None
        assert ExitManager._fill_vwap([{"qty": "0", "price": "5"}]) is None
        assert ExitManager._fill_vwap(["nope"]) is None
        assert ExitManager._fill_vwap([]) is None
        assert ExitManager._fill_vwap(None) is None


# ---------------------------------------------------------------------------
# TRAIL ailesi: rapor/adli kayıt TRAIL_MARKET/BE_MARKET'i tanır, AYRI sayar
# ---------------------------------------------------------------------------

class TestTrailFamilyRecognition:
    def test_exits_module_declares_the_family(self):
        assert EXIT_REASON_TRAIL_MARKET == "TRAIL_MARKET"
        assert EXIT_REASON_BE_MARKET == "BE_MARKET"
        assert TRAIL_EXIT_REASONS == {"TRAIL", "TRAIL_MARKET"}
        assert MARKET_EXIT_REASONS == {"TRAIL_MARKET", "BE_MARKET"}

    def test_forensics_family_maps_to_trail(self):
        from src.strategies.scalper.forensics import exit_reason_family

        assert exit_reason_family("TRAIL_MARKET") == "TRAIL"
        assert exit_reason_family("BE_MARKET") == "TRAIL"
        assert exit_reason_family("TRAIL") == "TRAIL"
        assert exit_reason_family("SL") == "SL"

    def test_forensics_does_not_treat_winning_trail_market_as_a_stop(self):
        """Kârlı bir TRAIL_MARKET "stop yedi" (noise_stop) sayılmamalı.

        Kayıplı olan (net<0) elbette sayılır — kural TRAIL ile aynıdır.
        """
        from src.strategies.scalper import forensics as fx

        candles = [
            SimpleNamespace(high=101.0, low=99.0, close_time=60_000),
            SimpleNamespace(high=101.5, low=100.5, close_time=120_000),
        ]
        entry = {"direction": "LONG", "fill_price": 100.0}

        winner = fx.postmortem_from_candles(
            entry=entry,
            exit_={"reason": "TRAIL_MARKET", "realized_pnl": 12.0},
            candles=candles,
            closed_at_ms=0,
        )
        assert winner.get("tags", []) == []

        loser = fx.postmortem_from_candles(
            entry=entry,
            exit_={"reason": "TRAIL_MARKET", "realized_pnl": -12.0},
            candles=candles,
            closed_at_ms=0,
        )
        assert loser.get("tags") == [fx.TAG_NOISE_STOP]

    def test_ledger_report_counts_market_exits_separately(self):
        import importlib.util
        import sys

        spec = importlib.util.spec_from_file_location(
            "ledger_report_d22", "scripts/ledger_report.py"
        )
        module = importlib.util.module_from_spec(spec)
        # dataclass alan çözümlemesi modülü sys.modules'te arar.
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
        finally:
            sys.modules.pop(spec.name, None)

        assert "TRAIL_MARKET" in module.EXIT_REASON_ORDER
        assert "BE_MARKET" in module.EXIT_REASON_ORDER
        assert module.exit_reason_family("TRAIL_MARKET") == "TRAIL"
        assert module.exit_reason_family("BE_MARKET") == "TRAIL"
        # TRAIL ile aynı satırda BİRLEŞTİRİLMEZ.
        assert module.EXIT_REASON_ORDER.index("TRAIL_MARKET") != module.\
            EXIT_REASON_ORDER.index("TRAIL")

        trades = [
            module.ClosedTrade(
                id=1, strategy="C", symbol="BTCUSDT", direction="LONG",
                realized_pnl=10.0, exit_reason="TRAIL",
                closed_at=datetime(2026, 8, 23), day="2026-08-23",
            ),
            module.ClosedTrade(
                id=2, strategy="C", symbol="DOGEUSDT", direction="LONG",
                realized_pnl=-4.0, exit_reason="TRAIL_MARKET",
                closed_at=datetime(2026, 8, 23), day="2026-08-23",
            ),
        ]
        rows = module.build_exit_reason_direction_table(trades)
        by_reason = {r["exit_reason"]: r for r in rows}
        assert set(by_reason) == {"TRAIL", "TRAIL_MARKET"}
        assert by_reason["TRAIL_MARKET"]["exit_family"] == "TRAIL"
        assert by_reason["TRAIL_MARKET"]["trades"] == 1


# ---------------------------------------------------------------------------
# (F) Beklenen durumlar ERROR/WARNING olarak loglanmaz — ama DEBUG'a da inmez
# ---------------------------------------------------------------------------

class TestBenignLogLevels:
    def test_minus_2011_is_recognised_as_benign(self):
        assert is_benign_cancel_error(BinanceAPIError(400, -2011, "Unknown order sent."))
        assert is_benign_cancel_error(
            BinanceAPIError(400, -9999, "Order does not exist.")
        )
        assert not is_benign_cancel_error(BinanceAPIError(400, -2019, "margin"))
        assert not is_benign_cancel_error(RuntimeError("ağ"))

    async def test_cancel_all_open_orders_logs_missing_order_at_info(self):
        """INFO — DEBUG DEĞİL.

        Üretimde DEBUG kapalıdır; -2011 satırını oraya indirmek "iptal
        edilecek emir yoktu" izini tamamen kaybettirirdi ve bu iz, defter
        sapmasının (emir aradaki milisaniyelerde dolmuş) tek göstergesidir.
        """
        from src.trading.binance_client_improved import ImprovedBinanceClient

        client = ImprovedBinanceClient.__new__(ImprovedBinanceClient)
        client.logger = _Logger()

        async def fake_request(method, endpoint, params=None, signed=False):
            raise BinanceAPIError(400, -2011, "Unknown order sent.", endpoint)

        client._request_with_retry = fake_request
        client.get_open_algo_orders = AsyncMock(return_value=[])

        await client.cancel_all_open_orders("BTCUSDT")

        assert client.logger.lines["warning"] == []
        assert client.logger.lines["debug"] == []
        assert any("-2011" in line or "emir yok" in line
                   for line in client.logger.lines["info"])

    async def test_stale_stop_cancel_logs_at_info(self):
        pm = PositionManager.__new__(PositionManager)
        pm.logger = _Logger()
        pm.binance = SimpleNamespace(
            get_open_algo_orders=AsyncMock(return_value=[
                {"algoId": 11, "orderType": "STOP_MARKET", "side": "SELL"},
            ]),
            cancel_algo_order=AsyncMock(
                side_effect=BinanceAPIError(400, -2011, "Unknown order sent.")
            ),
        )
        await pm._cancel_stale_stops("BTCUSDT", keep_order_id=99)

        assert pm.logger.lines["warning"] == []
        assert pm.logger.lines["debug"] == []
        assert any("zaten yok" in line for line in pm.logger.lines["info"])

    def test_maker_partial_fill_is_not_a_warning(self):
        """Kısmi dolum akışta ele alınan BEKLENEN bir durumdur (INFO)."""
        source = open("src/strategies/scalper/executor.py", encoding="utf-8").read()
        assert "maker giriş kısmen doldu" in source
        idx = source.index("maker giriş kısmen doldu")
        window = source[max(0, idx - 200): idx]
        assert "self.logger.info(" in window
        assert "self.logger.warning(" not in window.split("self.logger.info(")[-1]


# ---------------------------------------------------------------------------
# Mükerrer finalize kalkanı ve "kapanışın nedeni biz değiliz" ayrımı
# ---------------------------------------------------------------------------

class TestDoubleFinalizeShield:
    async def test_a_recorded_close_is_never_written_twice(self):
        """`_closing` EŞZAMANLI, `close_recorded` ARDIŞIK yolu tutar.

        Ardışık yol gerçektir: `force_breakeven` acil kapanışı finalize
        ettikten sonra TV olay kanalı `EXIT_LOSING=close` ile
        `_close_position_market`a gidebilir; ikinci `record_close`
        `exit_reason`ı ÜZERİNE YAZARDI.
        """
        client = SimpleNamespace(
            cancel_all_open_orders=AsyncMock(),
            get_current_price=AsyncMock(return_value=99.0),
            get_income_history=AsyncMock(return_value=[]),
            get_order=AsyncMock(
                return_value={"updateTime": int(time.time() * 1000) - 5000}
            ),
        )
        tracker = SimpleNamespace(record_close=AsyncMock())
        mgr = _ledger_manager(tracker, client)
        mgr.INCOME_RETRY_DELAYS = (0.0,)
        sp = _sp(99.0)
        mgr._positions["BTCUSDT"] = sp

        await mgr._handle_closed(
            "BTCUSDT", sp, forced_exit_reason=EXIT_REASON_BE_MARKET
        )
        assert sp.close_recorded is True
        await mgr._handle_closed("BTCUSDT", sp, forced_exit_reason="TV_EVENT")

        assert tracker.record_close.await_count == 1
        assert (
            tracker.record_close.await_args.kwargs["exit_reason"]
            == EXIT_REASON_BE_MARKET
        )

    async def test_step_stops_after_a_close_was_recorded(self):
        """TP1 acil kapanışından sonra TP2/trailing turuna devam edilmez."""
        mgr = _manager(replace_result=_EMERGENCY, amounts=[0.0])
        mgr._confirmed_algo_fill = AsyncMock(return_value=True)
        mgr._check_tp2 = AsyncMock()
        mgr._update_trailing = AsyncMock()
        mgr._update_mae_mfe = lambda sp, price: None

        sp = _sp(106.0)
        mgr._positions["BTCUSDT"] = sp

        async def handle_closed(symbol, s, *, forced_exit_reason=None):
            mgr.finalized.append({"symbol": symbol, "reason": forced_exit_reason})
            s.close_recorded = True
            mgr._positions.pop(symbol, None)

        mgr._handle_closed = handle_closed
        mgr.client.get_current_price = AsyncMock(return_value=106.0)
        mgr.client._amounts = [1.0, 0.0]   # önce açık (step), sonra flat (finalize)

        await mgr._step_one("BTCUSDT", sp)

        assert mgr.finalized == [
            {"symbol": "BTCUSDT", "reason": EXIT_REASON_BE_MARKET}
        ]
        mgr._check_tp2.assert_not_awaited()
        mgr._update_trailing.assert_not_awaited()


class TestClosedBeforeWeActed:
    async def test_position_closed_by_something_else_is_not_mislabelled(self):
        """`-2021` geldi ama pozisyon ZATEN kapanmıştı → etiket UYDURULMAZ.

        Kapanışın nedeni biz değilsek (SL/TP tetiklenmiş olabilir) deftere
        `TRAIL_MARKET` basmak, D22'nin düzelttiği yalanın aynısı olurdu.
        Sonuç `no_position`tır ve defter normal kanıt merdivenine düşer.
        """
        pm = PositionManager.__new__(PositionManager)
        pm.logger = _Logger()
        pm.binance = SimpleNamespace(
            get_position_risk=AsyncMock(return_value={"positionAmt": "1.0"}),
            place_stop_loss=AsyncMock(
                side_effect=BinanceAPIError(400, -2021, "would immediately trigger")
            ),
        )
        pm._emergency_close = AsyncMock(
            return_value=EmergencyCloseResult(True, submitted=False)
        )

        result = await pm._replace_stop_loss_result(_position(), 101.0)
        assert result.outcome == "no_position"

    async def test_emergency_close_marks_a_flat_position_as_not_submitted(self):
        pm = PositionManager.__new__(PositionManager)
        pm.logger = _Logger()
        pm.binance = SimpleNamespace(
            get_position_risk=AsyncMock(return_value={"positionAmt": "0"}),
        )
        result = await pm._emergency_close("BTCUSDT")
        assert bool(result) is True
        assert result.submitted is False
