"""D22 — trailing `-2021` YANILTICI YOLU ve `TRAIL_MARKET` etiketi.

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

Bu dosya dört davranışı çiviler:
  * (a) gönderMEDEN önce canlı fiyatla koruma-tarafı kontrolü → başarısız
    olacağı bilinen algoOrder çağrısı YAPILMAZ; bilinçli reduce-only MARKET
    kapanış (reaper/flatten ile aynı yol) ve `TRAIL_MARKET`,
  * (b) yarış hâlinde (-2021 yine gelirse) `pm` sonucu YAPILANDIRILMIŞ döner,
    exits doğru loglar ve `TRAIL_MARKET` yazar; "eski SL korunuyor" YALNIZ
    pozisyon gerçekten açıkken,
  * (c) işlem fiyatı bayatsa (>30 sn) ne emir ne kapanış — atla + sayaç,
  * TRAIL_MARKET'in defter/rapor/adli kayıtta TRAIL ailesi olarak ama AYRI
    sayılarak tanınması.
"""

import time
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock

import pytest

from src.strategies.scalper.exits import (
    EXIT_REASON_TRAIL_MARKET,
    TRAIL_EXIT_REASONS,
    ExitManager,
)
from src.strategies.scalper.types import Candle, Direction
from src.trading.binance_client_improved import (
    BinanceAPIError,
    is_benign_cancel_error,
)
from src.trading.position_manager import PositionManager, StopReplaceResult


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
            entry_fee_rate=0.0004,
        ),
        entry_candle_time=0,
        mae_pct=-1.0,
        mfe_pct=1.0,
        tp1_done=False,
        tp2_done=False,
        trailing_active=True,
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


def _manager(
    *,
    replace_result: Any = None,
    market_close: Optional[Any] = None,
    fresh_price: bool = True,
    market_url: str = "",
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
    )
    mgr.logger = _Logger()
    mgr._market_data_down_reason = None
    mgr._trading_price_seen_at = (
        {"BTCUSDT": time.monotonic()} if fresh_price else {}
    )
    mgr._trailing_space_skips = 0
    mgr._trailing_gate_skips = 0
    mgr._trailing_stale_price_skips = 0
    mgr._trailing_market_exits = 0
    mgr._trailing_skip_log_at = {}
    mgr._data_price_error_log_at = {}
    mgr._positions = {}
    mgr._closing = set()
    mgr.data_price_fetch = None
    mgr.market_close_cb = market_close

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


class _MarketClose:
    """`engine._close_position_market` yerine geçen çağrı kaydedici."""

    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.calls: List[Dict[str, Any]] = []

    async def __call__(self, symbol, sp, forced_exit_reason="RISK_EVENT", *,
                       exit_reason=None):
        self.calls.append({
            "symbol": symbol,
            "exit_reason": exit_reason or forced_exit_reason,
        })
        return self.ok


# ---------------------------------------------------------------------------
# (a) Gönderilmeden önce koruma-tarafı kontrolü — aynı host
# ---------------------------------------------------------------------------

class TestPreSendProtectiveGate:
    async def test_wrong_side_stop_is_never_sent_and_closes_at_market(self):
        """Stop güncel fiyatın yanlış tarafındaysa algoOrder HİÇ gönderilmez;
        pozisyon reduce-only MARKET ile kapatılır ve TRAIL_MARKET yazılır."""
        closer = _MarketClose()
        mgr = _manager(market_close=closer)
        # Chandelier ≈ 104.8 ama işlem fiyatı 100.0: LONG stop fiyatın ÜSTÜNDE
        # → "iz çoktan tetiklendi".
        await mgr._update_trailing("BTCUSDT", _sp(100.0))

        assert mgr.replace_calls == [], (
            "başarısız olacağı bilinen -2021 emri yine de gönderildi"
        )
        assert [c["exit_reason"] for c in closer.calls] == [
            EXIT_REASON_TRAIL_MARKET
        ]
        assert mgr.trailing_skip_snapshot()["market_exits"] == 1
        assert "eski SL korunuyor" not in mgr.logger.all_text()

    async def test_protective_stop_is_sent_unchanged(self):
        """Doğru taraftaki stop eskisi gibi gönderilir (davranış değişmedi)."""
        closer = _MarketClose()
        mgr = _manager(market_close=closer)
        await mgr._update_trailing("BTCUSDT", _sp(106.0))

        assert len(mgr.replace_calls) == 1
        assert closer.calls == []
        assert mgr.trailing_skip_snapshot()["market_exits"] == 0

    async def test_without_market_close_cb_nothing_is_sent(self):
        """Kapanış yolu bağlı değilse (eski kurulum) emir de GÖNDERİLMEZ."""
        mgr = _manager(market_close=None)
        await mgr._update_trailing("BTCUSDT", _sp(100.0))

        assert mgr.replace_calls == []
        assert mgr.trailing_skip_snapshot()["protective_gate_skips"] == 1

    async def test_unverified_market_close_is_logged_as_error(self):
        """Kapanış borsada doğrulanamazsa sessiz kalınmaz (fail-closed)."""
        closer = _MarketClose(ok=False)
        mgr = _manager(market_close=closer)
        await mgr._update_trailing("BTCUSDT", _sp(100.0))

        assert "DOĞRULANAMADI" in mgr.logger.text("error")


# ---------------------------------------------------------------------------
# (b) Yarış: kapı geçti ama emir borsaya varana kadar fiyat stopu geçti
# ---------------------------------------------------------------------------

class TestEmergencyCloseRace:
    async def test_emergency_closed_result_is_labelled_trail_market(self):
        closer = _MarketClose()
        mgr = _manager(
            replace_result=StopReplaceResult(False, "emergency_closed", -2021),
            market_close=closer,
        )
        await mgr._update_trailing("BTCUSDT", _sp(106.0))

        assert len(mgr.replace_calls) == 1, "emir gönderilmeliydi (kapı geçti)"
        assert [c["exit_reason"] for c in closer.calls] == [
            EXIT_REASON_TRAIL_MARKET
        ]
        assert mgr.trailing_skip_snapshot()["market_exits"] == 1
        assert "eski SL korunuyor" not in mgr.logger.all_text()

    async def test_failed_result_keeps_the_old_stop_and_says_so(self):
        """Gerçekten "eski SL yerinde" olan TEK durum."""
        closer = _MarketClose()
        mgr = _manager(
            replace_result=StopReplaceResult(False, "failed", -1021),
            market_close=closer,
        )
        await mgr._update_trailing("BTCUSDT", _sp(106.0))

        assert closer.calls == []
        assert "eski SL korunuyor" in mgr.logger.text("warning")

    async def test_no_position_result_does_not_claim_old_stop(self):
        closer = _MarketClose()
        mgr = _manager(
            replace_result=StopReplaceResult(False, "no_position"),
            market_close=closer,
        )
        await mgr._update_trailing("BTCUSDT", _sp(106.0))

        assert closer.calls == []
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
# (c) Bayat fiyat: ne gönder ne kapat
# ---------------------------------------------------------------------------

class TestStaleTradingPrice:
    async def test_stale_price_skips_round_without_order_or_close(self):
        closer = _MarketClose()
        mgr = _manager(market_close=closer, fresh_price=False)
        await mgr._update_trailing("BTCUSDT", _sp(100.0))

        assert mgr.replace_calls == []
        assert closer.calls == []
        assert mgr.trailing_skip_snapshot()["stale_price_skips"] == 1

    async def test_expired_stamp_counts_as_stale(self):
        closer = _MarketClose()
        mgr = _manager(market_close=closer)
        mgr._trading_price_seen_at = {"BTCUSDT": time.monotonic() - 45.0}
        await mgr._update_trailing("BTCUSDT", _sp(100.0))

        assert (mgr.replace_calls, closer.calls) == ([], [])
        assert mgr.trailing_skip_snapshot()["stale_price_skips"] == 1

    async def test_missing_price_counts_as_stale(self):
        closer = _MarketClose()
        mgr = _manager(market_close=closer)
        await mgr._update_trailing("BTCUSDT", _sp(0.0))

        assert (mgr.replace_calls, closer.calls) == ([], [])
        assert mgr.trailing_skip_snapshot()["stale_price_skips"] == 1


# ---------------------------------------------------------------------------
# `position_manager` yapılandırılmış sonucu
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

    async def test_minus_2021_reports_emergency_closed(self):
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
        assert result.error_code == -2021
        assert bool(result) is False
        pm._emergency_close.assert_awaited_once()

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


# ---------------------------------------------------------------------------
# Defter: TRAIL_MARKET etiketi kapanışa yazılır
# ---------------------------------------------------------------------------

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
        mgr = ExitManager(
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
        mgr.INCOME_RETRY_DELAYS = (0.0,)
        sp = _sp(99.0)
        mgr._positions["BTCUSDT"] = sp

        await mgr._handle_closed(
            "BTCUSDT", sp, forced_exit_reason=EXIT_REASON_TRAIL_MARKET
        )

        kwargs = tracker.record_close.await_args.kwargs
        assert kwargs["exit_reason"] == EXIT_REASON_TRAIL_MARKET
        assert "BTCUSDT" not in mgr._positions


# ---------------------------------------------------------------------------
# TRAIL ailesi: rapor/adli kayıt TRAIL_MARKET'i tanır ama AYRI sayar
# ---------------------------------------------------------------------------

class TestTrailFamilyRecognition:
    def test_exits_module_declares_the_family(self):
        assert EXIT_REASON_TRAIL_MARKET == "TRAIL_MARKET"
        assert TRAIL_EXIT_REASONS == {"TRAIL", "TRAIL_MARKET"}

    def test_forensics_family_maps_to_trail(self):
        from src.strategies.scalper.forensics import exit_reason_family

        assert exit_reason_family("TRAIL_MARKET") == "TRAIL"
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

    def test_ledger_report_counts_trail_market_separately(self):
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
        assert module.exit_reason_family("TRAIL_MARKET") == "TRAIL"
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
# Madde 4: beklenen durumlar ERROR/WARNING seviyesinde loglanmaz
# ---------------------------------------------------------------------------

class TestBenignLogLevels:
    def test_minus_2011_is_recognised_as_benign(self):
        assert is_benign_cancel_error(BinanceAPIError(400, -2011, "Unknown order sent."))
        assert is_benign_cancel_error(
            BinanceAPIError(400, -9999, "Order does not exist.")
        )
        assert not is_benign_cancel_error(BinanceAPIError(400, -2019, "margin"))
        assert not is_benign_cancel_error(RuntimeError("ağ"))

    async def test_cancel_all_open_orders_logs_missing_order_at_debug(self):
        from src.trading.binance_client_improved import ImprovedBinanceClient

        client = ImprovedBinanceClient.__new__(ImprovedBinanceClient)
        client.logger = _Logger()

        async def fake_request(method, endpoint, params=None, signed=False):
            raise BinanceAPIError(400, -2011, "Unknown order sent.", endpoint)

        client._request_with_retry = fake_request
        client.get_open_algo_orders = AsyncMock(return_value=[])

        await client.cancel_all_open_orders("BTCUSDT")

        assert client.logger.lines["warning"] == []
        assert any("-2011" in line or "zaten yok" in line
                   for line in client.logger.lines["debug"])

    def test_maker_partial_fill_is_not_a_warning(self):
        """Kısmi dolum akışta ele alınan BEKLENEN bir durumdur (INFO)."""
        source = open("src/strategies/scalper/executor.py", encoding="utf-8").read()
        assert "maker giriş kısmen doldu" in source
        idx = source.index("maker giriş kısmen doldu")
        window = source[max(0, idx - 200): idx]
        assert "self.logger.info(" in window
        assert "self.logger.warning(" not in window.split("self.logger.info(")[-1]
