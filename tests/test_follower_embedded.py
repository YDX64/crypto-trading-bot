"""GÖMÜLÜ AlgoPro takipçisi (D20b) — kullanıcı kararı 2026-08-23.

Kapsam:
  * `FOLLOWER_EMBEDDED=false` (varsayılan) → SIFIR davranış farkı;
  * SANAL defter: equity = taban + AP net PnL (DB'den, restart'a dayanıklı),
    gerçek bakiye marja yetmezse giriş YOK;
  * iki defterin AYRIŞMASI: scalper'ın sanal kasası ve günlük kesicisi AP
    işlemlerinden etkilenmez, takipçininki de scalper'dan;
  * sembol sahipliği (rezervasyon) İKİ YÖNDE de çakışmayı engeller;
  * yetim denetimi diğer motorun pozisyonunu YETİM saymaz;
  * `FOLLOWER_SYMBOLS`: takipçi evreni + scalper tarama evreninden ve TV
    giriş oylamasından OTOMATİK dışlama;
  * `/tv-signal` köprüsü: AlgoPro alert() gövdesi YALNIZ takipçiye gider ve
    sağlamaya OY VERMEZ; eski özel mesaj biçimi eskisi gibi oy verir;
  * pano besleme şekli (`/api/status → follower`) ve `/follower/status`;
  * `FOLLOWER_SL_MARGIN_PCT` (kaldıraç formülünün payı) + 10–50 bandı.

GERÇEK AĞ/DB YOK: motor `object.__new__` ile kurulur (test_follower_engine.py
deseni), tracker/client sahtedir.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

import src.main as main_module
import src.models.waiting_signal  # noqa: F401  (SQLAlchemy mapper zinciri)
from src.core.config import Settings
from src.strategies.follower.engine import FollowerEngine
from src.strategies.follower.plan import target_leverage
from src.strategies.follower.parser import parse_follower_event
from src.strategies.follower.risk_halt import RiskEventHaltStore
from src.strategies.follower.types import FollowerRejected
from src.trading.symbol_reservations import symbol_reservations

from test_follower_engine import _cfg as _base_cfg, _fake_position

TV_SECRET = "tv-s3cr3t"

REAL_SELL = (
    "🔴 SELL | BINANCE:BTCUSDT | TF: 1 | Price: 77126.08 | TQI: .45 | Score: 8 "
    "| SL: 77167.77 | TP1: 77105.23 | TP2: 77084.39 | TP3: 77063.54 | TP: fixed ×1.00"
)
#: AlgoPro'nun ESKİ özel mesaj biçimi — ana botun sağlamasına oy verir ve
#: takipçinin katı tanıyıcısından GEÇMEZ (D20a bulgu 2/5).
LEGACY_ENTRY = "BUY on BTCUSDT | TF: 5 | Price: 77126.08"


def _cfg(**overrides):
    """test_follower_engine._cfg + gömülü mod alanları."""
    base = _base_cfg()
    defaults = dict(
        follower_embedded=True,
        follower_virtual_capital_usdt=1000.0,
        follower_symbols="",
        follower_universe=sorted({"BTCUSDT", "ETHUSDT"}),
        follower_reserved_symbols=[],
        follower_sl_margin_pct=30.0,
        follower_daily_loss_limit_pct=10.0,
    )
    defaults.update(overrides)
    for key, value in defaults.items():
        setattr(base, key, value)
    return base


def _tracker(*, eligible=0.0, daily=0.0):
    """AP defterini taklit eden sahte tracker (DB yok)."""
    return SimpleNamespace(
        compounding_snapshot=AsyncMock(
            return_value={"eligible_realized_pnl": eligible}
        ),
        eligible_compounding_pnl=AsyncMock(return_value=eligible),
        strategy_realized_pnl_since=AsyncMock(return_value=daily),
        close_seq=0,
    )


def _make_engine(
    tmp_path,
    cfg=None,
    *,
    positions=None,
    balance=1000.0,
    tracker=None,
    all_positions=None,
):
    engine = object.__new__(FollowerEngine)
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
    engine._balance_cache = (None, 0.0)
    engine._income_cache = (None, 0.0, None)
    engine._income_cache_close_seq = -1
    engine._virtual_equity_usdt = None
    engine._virtual_realized_pnl = 0.0
    engine._exchange_available_usdt = None
    engine._orphans = []
    engine._orphans_checked_at = None
    engine._events = deque(maxlen=50)
    engine._event_counters = {}
    engine._reject_counters = {}
    engine._last_event_at = None
    engine._safety_last_success_monotonic = time.monotonic()
    engine._safety_last_error = None
    engine._safety_task = None
    engine.halt = RiskEventHaltStore(
        str(tmp_path / "risk_halt.json"), logger=MagicMock()
    )
    engine.tracker = tracker or _tracker()

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
        get_current_price=AsyncMock(return_value=77126.08),
        get_account_balance=AsyncMock(return_value=balance),
        get_position_risk=AsyncMock(return_value={"positionAmt": 0.0}),
        get_all_positions=AsyncMock(return_value=list(all_positions or [])),
    )
    engine.fetcher = SimpleNamespace(get_klines=AsyncMock(return_value=[]))
    engine.brackets = SimpleNamespace(snapshot=MagicMock(return_value={}))
    return engine


@pytest.fixture(autouse=True)
def _clean_reservations():
    symbol_reservations.clear()
    yield
    symbol_reservations.clear()


# ==========================================================================
# 1) Ayar katmanı
# ==========================================================================

class TestEmbeddedSettings:
    def _settings(self, monkeypatch, **env):
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        return Settings(_env_file="env.example")

    def test_embedded_is_off_by_default(self, monkeypatch):
        cfg = self._settings(monkeypatch)
        assert cfg.follower_embedded is False
        assert cfg.follower_active is False
        # Gömülü mod kapalıyken HİÇBİR sembol scalper'dan çıkarılmaz.
        assert cfg.follower_reserved_symbols == []

    def test_follower_symbols_defines_universe_and_reservation(self, monkeypatch):
        cfg = self._settings(
            monkeypatch,
            FOLLOWER_EMBEDDED="true",
            FOLLOWER_SYMBOLS="adausdt, LTCUSDT ,adausdt",
        )
        assert cfg.follower_universe == ["ADAUSDT", "LTCUSDT"]
        assert cfg.follower_reserved_symbols == ["ADAUSDT", "LTCUSDT"]
        assert cfg.follower_active is True

    def test_empty_follower_symbols_falls_back_to_allowlist(self, monkeypatch):
        cfg = self._settings(monkeypatch, FOLLOWER_EMBEDDED="true")
        assert len(cfg.follower_universe) == 8
        assert cfg.follower_reserved_symbols == []

    def test_symbols_without_embedded_reserve_nothing(self, monkeypatch):
        """Bayrak kapalıyken FOLLOWER_SYMBOLS scalper'ı ETKİLEMEZ."""
        cfg = self._settings(monkeypatch, FOLLOWER_SYMBOLS="ADAUSDT")
        assert cfg.follower_reserved_symbols == []

    def test_bot_mode_follower_with_embedded_is_rejected(self, monkeypatch):
        monkeypatch.setenv("RISK_EVENT_SECRET", "x" * 24)
        with pytest.raises(Exception) as exc:
            self._settings(
                monkeypatch, BOT_MODE="follower", FOLLOWER_EMBEDDED="true"
            )
        assert "FOLLOWER_EMBEDDED" in str(exc.value)

    def test_embedded_on_mainnet_is_rejected(self, monkeypatch):
        with pytest.raises(Exception) as exc:
            self._settings(
                monkeypatch,
                FOLLOWER_EMBEDDED="true",
                BINANCE_BASE_URL="https://fapi.binance.com",
                ALLOW_MAINNET="true",
            )
        assert "TESTNET" in str(exc.value)

    def test_sl_margin_pct_alias_and_band(self, monkeypatch):
        cfg = self._settings(monkeypatch, FOLLOWER_SL_MARGIN_PCT="40")
        assert cfg.follower_sl_margin_pct == 40.0
        # Eski ad da eşitlenir (kod iki adı da okuyabilir).
        assert cfg.follower_sl_roi_target == 40.0

    def test_legacy_name_still_works(self, monkeypatch):
        cfg = self._settings(monkeypatch, FOLLOWER_SL_ROI_TARGET="25")
        assert cfg.follower_sl_margin_pct == 25.0

    def test_conflicting_names_fail_fast(self, monkeypatch):
        with pytest.raises(Exception):
            self._settings(
                monkeypatch,
                FOLLOWER_SL_MARGIN_PCT="35",
                FOLLOWER_SL_ROI_TARGET="30",
            )

    @pytest.mark.parametrize("value", ["9", "51", "0"])
    def test_out_of_band_sl_margin_rejected(self, monkeypatch, value):
        with pytest.raises(Exception):
            self._settings(monkeypatch, FOLLOWER_SL_MARGIN_PCT=value)

    def test_max_leverage_alias(self, monkeypatch):
        cfg = self._settings(monkeypatch, FOLLOWER_MAX_LEVERAGE="40")
        assert cfg.follower_lev_max == 40

    def test_leverage_formula_uses_sl_margin_pct(self):
        cfg = SimpleNamespace(
            follower_sl_margin_pct=40.0, follower_lev_min=3, follower_lev_max=100
        )
        # lev = clamp(round(40 / 0.5), 3, 100) = 80
        assert target_leverage(0.5, cfg) == 80


# ==========================================================================
# 2) Sanal defter
# ==========================================================================

class TestVirtualLedger:
    async def test_equity_is_base_plus_ap_pnl_from_db(self, tmp_path):
        engine = _make_engine(tmp_path, tracker=_tracker(eligible=137.5))
        assert await engine._entry_equity() == pytest.approx(1137.5)
        # Kaynak DB'dir (RAM değil): restart sonrası aynı çağrı aynı sonucu
        # verir çünkü toplam her seferinde defterden okunur.
        engine._virtual_equity_usdt = None
        assert await engine._entry_equity() == pytest.approx(1137.5)
        engine.tracker.compounding_snapshot.assert_awaited_with(
            0, strategies=("AP",)
        )

    async def test_losses_shrink_virtual_equity(self, tmp_path):
        engine = _make_engine(tmp_path, tracker=_tracker(eligible=-250.0))
        assert await engine._entry_equity() == pytest.approx(750.0)

    async def test_real_balance_below_required_margin_skips_entry(self, tmp_path):
        # marj = 1000 × %10 = 100 USDT; hesapta 40 USDT var.
        engine = _make_engine(tmp_path, balance=40.0)
        with pytest.raises(FollowerRejected) as exc:
            await engine._entry_equity()
        assert exc.value.code == "insufficient_balance"
        assert engine.logger.error.called

    async def test_entry_is_rejected_and_counted_when_balance_short(self, tmp_path):
        engine = _make_engine(tmp_path, balance=40.0)
        event = parse_follower_event(REAL_SELL)
        result = await engine.handle_event(event)
        assert result["accepted"] is False
        assert "bakiye yetersiz" in result["reason"]
        assert engine._reject_counters.get("insufficient_balance") == 1
        engine.executor.open_position.assert_not_called()

    async def test_ledger_error_is_fail_closed(self, tmp_path):
        tracker = _tracker()
        tracker.compounding_snapshot = AsyncMock(side_effect=RuntimeError("db yok"))
        engine = _make_engine(tmp_path, tracker=tracker)
        with pytest.raises(FollowerRejected) as exc:
            await engine._entry_equity()
        assert exc.value.code == "virtual_equity"

    async def test_disabled_ledger_returns_exchange_balance(self, tmp_path):
        """Ayrı halkada (embedded=false) bugünkü davranış birebir."""
        engine = _make_engine(
            tmp_path, _cfg(follower_embedded=False), balance=812.5
        )
        assert await engine._entry_equity() == pytest.approx(812.5)
        engine.tracker.compounding_snapshot.assert_not_awaited()

    async def test_risk_equity_uses_virtual_ledger(self, tmp_path):
        engine = _make_engine(tmp_path, tracker=_tracker(eligible=100.0))
        assert await engine._risk_equity() == pytest.approx(1100.0)

    async def test_daily_pnl_comes_from_ap_ledger_when_embedded(self, tmp_path):
        engine = _make_engine(tmp_path, tracker=_tracker(daily=-42.0))
        pnl = await engine._daily_net_income("2026-08-23")
        assert pnl == pytest.approx(-42.0)
        engine.tracker.strategy_realized_pnl_since.assert_awaited_once()
        args = engine.tracker.strategy_realized_pnl_since.await_args.args
        assert args[0] == "AP"
        assert args[1] == datetime(2026, 8, 23)

    async def test_kill_switch_threshold_is_measured_on_virtual_capital(
        self, tmp_path
    ):
        # Defter −120 USDT: sanal sermaye 880'e düşer, günün açılışı 1000,
        # limit %10 → eşik −100. PnL −120 ≤ −100 → kesici TETİKLENİR.
        engine = _make_engine(
            tmp_path, tracker=_tracker(eligible=-120.0, daily=-120.0)
        )
        await engine._update_kill_switch()
        assert engine._kill_switch is True
        assert engine._risk_equity_usdt == pytest.approx(880.0)
        assert engine._daily_loss_threshold_usdt == pytest.approx(-100.0)


# ==========================================================================
# 3) İki defterin ayrışması (scalper tarafı)
# ==========================================================================

class TestScalperLedgerSeparation:
    def _scalper(self, cfg, tracker):
        from src.strategies.scalper.engine import ScalperEngine

        engine = object.__new__(ScalperEngine)
        engine.cfg = cfg
        engine.logger = MagicMock()
        engine.tracker = tracker
        return engine

    async def test_offset_is_zero_when_embedded_off(self):
        tracker = _tracker(daily=-500.0)
        engine = self._scalper(SimpleNamespace(follower_embedded=False), tracker)
        assert await engine._follower_daily_pnl_offset("2026-08-23") == 0.0
        tracker.strategy_realized_pnl_since.assert_not_awaited()

    async def test_ap_pnl_is_subtracted_from_account_income(self):
        tracker = _tracker(daily=-90.0)
        engine = self._scalper(SimpleNamespace(follower_embedded=True), tracker)
        offset = await engine._follower_daily_pnl_offset("2026-08-23")
        assert offset == pytest.approx(-90.0)
        # income − offset: takipçinin −90'ı scalper'ın gününden DÜŞÜLÜR
        # (yani scalper'ın PnL'i −90 kadar YUKARI düzeltilir).
        assert (-140.0) - offset == pytest.approx(-50.0)

    async def test_offset_failure_does_not_break_kill_switch(self):
        tracker = _tracker()
        tracker.strategy_realized_pnl_since = AsyncMock(
            side_effect=RuntimeError("db kilitli")
        )
        engine = self._scalper(SimpleNamespace(follower_embedded=True), tracker)
        assert await engine._follower_daily_pnl_offset("2026-08-23") == 0.0
        assert engine.logger.warning.called


# ==========================================================================
# 4) Sembol sahipliği (rezervasyon) — iki yönde
# ==========================================================================

class TestSymbolReservation:
    async def test_follower_refuses_symbol_owned_by_scalper(self, tmp_path):
        symbol_reservations.reserve("BTCUSDT", "scalper")
        engine = _make_engine(tmp_path)
        result = await engine.handle_event(parse_follower_event(REAL_SELL))
        assert result["accepted"] is False
        assert "başka bir motorun" in result["reason"]
        assert engine._reject_counters.get("reserved_by_other") == 1
        engine.executor.open_position.assert_not_called()

    async def test_follower_reserves_symbol_on_entry(self, tmp_path):
        engine = _make_engine(tmp_path)
        result = await engine.handle_event(parse_follower_event(REAL_SELL))
        assert result["accepted"] is True
        assert symbol_reservations.owner("BTCUSDT") == "follower"

    async def test_scalper_skips_symbol_owned_by_follower(self):
        """`_evaluate_symbol` başka sahibi olan sembole HİÇ bakmaz."""
        from src.strategies.scalper.engine import ScalperEngine

        symbol_reservations.reserve("BTCUSDT", "follower")
        engine = object.__new__(ScalperEngine)
        engine.cfg = SimpleNamespace()
        engine.logger = MagicMock()
        engine._executor_entry_blocked = MagicMock(return_value=False)
        # Sahiplik kapısı ilk satırdadır: buraya gelinirse test patlar.
        engine._scan_open_symbols = set()
        await engine._evaluate_symbol("BTCUSDT", [])
        engine._executor_entry_blocked.assert_not_called()

    async def test_reservation_released_when_position_no_longer_tracked(
        self, tmp_path
    ):
        engine = _make_engine(tmp_path)
        symbol_reservations.reserve("BTCUSDT", "follower")
        engine._sync_follower_reservations()
        assert symbol_reservations.owner("BTCUSDT") is None

    def test_entry_halt_keeps_reservation(self, tmp_path):
        engine = _make_engine(tmp_path)
        engine._entry_halted = True
        symbol_reservations.reserve("BTCUSDT", "follower")
        engine._sync_follower_reservations()
        assert symbol_reservations.owner("BTCUSDT") == "follower"

    async def test_in_flight_entry_keeps_reservation(self, tmp_path):
        """Uçuşta giriş varken sahiplik BIRAKILMAZ (track öncesi pencere)."""
        engine = _make_engine(tmp_path)
        symbol_reservations.reserve("BTCUSDT", "follower")
        async with engine._entry_lock:
            engine._sync_follower_reservations()
        assert symbol_reservations.owner("BTCUSDT") == "follower"


# ==========================================================================
# 5) Yetim denetimi
# ==========================================================================

class TestOrphanAuditWithTwoEngines:
    async def test_other_engines_position_is_not_an_orphan(self, tmp_path):
        symbol_reservations.reserve("ETHUSDT", "scalper")
        engine = _make_engine(
            tmp_path,
            all_positions=[{"symbol": "ETHUSDT", "positionAmt": "1.5"}],
        )
        orphans = await engine._check_orphans()
        assert orphans == []
        assert engine._entry_halted is False

    async def test_unowned_open_position_is_still_an_orphan(self, tmp_path):
        engine = _make_engine(
            tmp_path,
            all_positions=[{"symbol": "ETHUSDT", "positionAmt": "1.5"}],
        )
        orphans = await engine._check_orphans()
        assert orphans == ["ETHUSDT"]
        assert engine._entry_halted is True


# ==========================================================================
# 6) FOLLOWER_SYMBOLS — evren ve otomatik dışlama
# ==========================================================================

class TestReservedSymbols:
    def test_scalper_universe_excludes_follower_symbols(self):
        from src.strategies.scalper.engine import ScalperEngine

        engine = object.__new__(ScalperEngine)
        engine.cfg = SimpleNamespace(follower_reserved_symbols=["ADAUSDT"])
        engine.logger = MagicMock()
        kept = engine._exclude_follower_symbols(
            ["BTCUSDT", "ADAUSDT", "ETHUSDT"]
        )
        assert kept == ["BTCUSDT", "ETHUSDT"]
        # Loga YAZILIR — operatör evrenin neden daraldığını görmeli.
        assert any(
            "ayrılmış" in str(call)
            for call in engine.logger.info.call_args_list
        )

    def test_universe_unchanged_when_nothing_reserved(self):
        from src.strategies.scalper.engine import ScalperEngine

        engine = object.__new__(ScalperEngine)
        engine.cfg = SimpleNamespace(follower_reserved_symbols=[])
        engine.logger = MagicMock()
        universe = ["BTCUSDT", "ETHUSDT"]
        assert engine._exclude_follower_symbols(universe) == universe
        engine.logger.info.assert_not_called()

    async def test_tv_entry_vote_rejected_for_reserved_symbol(self):
        """TV allowlist'ten BAĞIMSIZ kapı: ana sistem o coini görmez."""
        from src.strategies.scalper.engine import ScalperEngine

        engine = object.__new__(ScalperEngine)
        engine.running = True
        engine.logger = MagicMock()
        engine.cfg = SimpleNamespace(
            follower_reserved_symbols=["ADAUSDT"],
            # Allowlist ADAUSDT'yi İZİN VERİYOR olsa bile ret gelir.
            scalper_tv_symbol_allowlist="BTCUSDT,ADAUSDT",
        )
        from src.strategies.scalper.types import Direction

        result = await engine.external_signal("ADAUSDT", Direction.LONG)
        assert result["accepted"] is False
        assert "takipçiye ayrılmış" in result["reason"]

    async def test_follower_rejects_symbol_outside_follower_symbols(self, tmp_path):
        cfg = _cfg(
            follower_symbols="ADAUSDT",
            follower_universe=["ADAUSDT"],
            follower_reserved_symbols=["ADAUSDT"],
        )
        engine = _make_engine(tmp_path, cfg)
        result = await engine.handle_event(parse_follower_event(REAL_SELL))
        assert result["accepted"] is False
        assert "evren" in result["reason"]
        assert engine._reject_counters.get("symbol_allowlist") == 1
        engine.executor.open_position.assert_not_called()

    def test_engine_universe_follows_follower_symbols(self, tmp_path):
        cfg = _cfg(follower_symbols="ADAUSDT", follower_universe=["ADAUSDT"])
        engine = _make_engine(tmp_path, cfg)
        assert engine.symbol_allowlist() == {"ADAUSDT"}


# ==========================================================================
# 7) /tv-signal köprüsü — süreç içi teslim
# ==========================================================================

class _FakeRequest:
    def __init__(self, body: bytes, query=None, headers=None):
        self._body = body
        self.query_params = query or {}
        self.headers = headers or {}

    async def body(self) -> bytes:
        return self._body


class TestEmbeddedRouting:
    @pytest.fixture
    def wired(self, monkeypatch):
        monkeypatch.setattr(main_module.settings, "tv_webhook_secret", TV_SECRET)
        monkeypatch.setattr(main_module.settings, "tv_confluence_required", 1)
        monkeypatch.setattr(main_module.settings, "follower_embedded", True)
        scalper = MagicMock()
        scalper.external_signal = AsyncMock(return_value={"accepted": True})
        monkeypatch.setattr(main_module, "scalper_engine", scalper)
        follower = MagicMock()
        follower.handle_event = AsyncMock(
            return_value={"accepted": True, "reason": "pozisyon açıldı"}
        )
        monkeypatch.setattr(main_module, "follower_engine", follower)
        forwarded: list = []
        monkeypatch.setattr(
            main_module,
            "maybe_forward_algopro_event",
            lambda raw, source: forwarded.append((raw, source)),
        )
        return SimpleNamespace(
            scalper=scalper, follower=follower, forwarded=forwarded
        )

    async def test_algopro_body_goes_only_to_follower(self, wired):
        body = f"{REAL_SELL} secret={TV_SECRET}"
        result = await main_module.tradingview_webhook(
            _FakeRequest(body.encode(), {})
        )
        assert result["routed"] == "follower"
        assert result["accepted"] is True
        assert result["symbol"] == "BTCUSDT"
        wired.follower.handle_event.assert_awaited_once()
        # Ana botun sağlamasına OY YAZILMAZ, emir hattı ÇAĞRILMAZ.
        wired.scalper.external_signal.assert_not_called()
        # Gömülü modda HTTP köprüsü de kullanılmaz (çift teslim olmaz).
        assert wired.forwarded == []

    async def test_hit_event_is_routed_instead_of_422(self, wired):
        """HIT mesajları ana botta 422 alır; gömülü modda takipçiye GİDER.

        Secret alarm URL'sinde (`?secret=`) taşınır — gerçek AlgoPro
        alarmlarında olduğu gibi. Gövdeye eklenen bir `secret=` `Price:`
        alanının değerine yapışır ve KATI tanıyıcı (D20a bulgu 2) gövdeyi
        AlgoPro saymaz; bu bilinçli bir fail-closed davranıştır.
        """
        body = "🛑 SL HIT | BINANCE:BTCUSDT | TF: 1 | Price: 77167.77"
        result = await main_module.tradingview_webhook(
            _FakeRequest(body.encode(), {"secret": TV_SECRET})
        )
        assert result["routed"] == "follower"
        assert result["kind"] == "sl"

    async def test_legacy_body_still_votes(self, wired):
        """Eski özel mesaj biçimi ana botun hattında KALIR (davranış aynı)."""
        body = f"{LEGACY_ENTRY} secret={TV_SECRET}"
        result = await main_module.tradingview_webhook(
            _FakeRequest(body.encode(), {})
        )
        assert "routed" not in result
        wired.scalper.external_signal.assert_awaited_once()
        wired.follower.handle_event.assert_not_called()

    async def test_embedded_off_keeps_todays_path(self, wired, monkeypatch):
        monkeypatch.setattr(main_module.settings, "follower_embedded", False)
        body = f"{REAL_SELL} secret={TV_SECRET}"
        result = await main_module.tradingview_webhook(
            _FakeRequest(body.encode(), {})
        )
        assert "routed" not in result
        wired.follower.handle_event.assert_not_called()
        # Bugünkü yol: HTTP köprüsü çağrılır (kapalıysa kendisi atlar).
        assert len(wired.forwarded) == 1

    async def test_dry_run_has_no_side_effects(self, wired):
        body = f"{REAL_SELL} secret={TV_SECRET}"
        result = await main_module.tradingview_webhook(
            _FakeRequest(body.encode(), {"dry_run": "1"})
        )
        assert result["dry_run"] is True
        assert result["would"]["routed"] == "follower"
        wired.follower.handle_event.assert_not_called()
        wired.scalper.external_signal.assert_not_called()

    async def test_wrong_secret_never_reaches_follower(self, wired):
        body = f"{REAL_SELL} secret=yanlis"
        with pytest.raises(HTTPException) as exc:
            await main_module.tradingview_webhook(_FakeRequest(body.encode(), {}))
        assert exc.value.status_code == 403
        wired.follower.handle_event.assert_not_called()

    async def test_engine_missing_returns_503(self, wired, monkeypatch):
        monkeypatch.setattr(main_module, "follower_engine", None)
        body = f"{REAL_SELL} secret={TV_SECRET}"
        with pytest.raises(HTTPException) as exc:
            await main_module.tradingview_webhook(_FakeRequest(body.encode(), {}))
        assert exc.value.status_code == 503


# ==========================================================================
# 8) Pano besleme şekli
# ==========================================================================

class TestDashboardWiring:
    def test_dashboard_snapshot_has_card_fields(self, tmp_path):
        engine = _make_engine(
            tmp_path, positions={"BTCUSDT": _fake_position()}
        )
        engine._virtual_equity_usdt = 1100.0
        engine._virtual_realized_pnl = 100.0
        snap = engine.dashboard_snapshot()
        for key in (
            "universe", "reserved_symbols", "virtual_ledger", "daily_pnl",
            "positions", "fee_gate_rejects", "events_total", "last_event_at",
            "max_positions", "entry_block_reason", "orphan_positions",
        ):
            assert key in snap, key
        ledger = snap["virtual_ledger"]
        assert ledger["enabled"] is True
        assert ledger["base_usdt"] == pytest.approx(1000.0)
        assert ledger["equity_usdt"] == pytest.approx(1100.0)
        assert ledger["margin_per_trade_usdt"] == pytest.approx(110.0)
        position = snap["positions"][0]
        for key in ("entry_price", "stop_loss", "tp1", "tp2", "tp3", "roi_pct"):
            assert key in position, key

    def test_position_roi_is_computed_from_memory(self, tmp_path):
        sp = _fake_position()
        sp.position.current_price = sp.position.entry_price * 0.999  # SHORT kâr
        engine = _make_engine(tmp_path, positions={"BTCUSDT": sp})
        roi = engine.snapshot()["positions"][0]["roi_pct"]
        assert roi == pytest.approx(10.0, abs=0.2)

    @staticmethod
    def _fake_orchestrator():
        """Scalper halkası: `orchestrator` DOLUDUR (gömülü mod dahil)."""
        return SimpleNamespace(
            binance=SimpleNamespace(
                get_account_balance=AsyncMock(return_value=1234.0),
                get_current_price=AsyncMock(return_value=77000.0),
                get_all_positions=AsyncMock(return_value=[]),
            )
        )

    async def test_api_status_carries_follower_block(self, monkeypatch):
        engine = MagicMock()
        engine.dashboard_snapshot = MagicMock(
            return_value={"running": True, "universe": ["ADAUSDT"]}
        )
        monkeypatch.setattr(main_module, "follower_engine", engine)
        monkeypatch.setattr(main_module.settings, "follower_embedded", True)
        monkeypatch.setattr(main_module, "orchestrator", self._fake_orchestrator())
        monkeypatch.setattr(main_module, "telegram_bot", None)
        main_module._reset_status_caches()
        payload = await main_module.api_status(None)
        assert payload["follower"]["universe"] == ["ADAUSDT"]
        # Pano kartı REST doğurmaz: yalnız bellek anlık görüntüsü okunur.
        engine.dashboard_snapshot.assert_called_once()

    async def test_api_status_has_no_follower_key_when_disabled(self, monkeypatch):
        monkeypatch.setattr(main_module.settings, "follower_embedded", False)
        monkeypatch.setattr(main_module, "follower_engine", None)
        monkeypatch.setattr(main_module, "orchestrator", self._fake_orchestrator())
        monkeypatch.setattr(main_module, "telegram_bot", None)
        main_module._reset_status_caches()
        payload = await main_module.api_status(None)
        assert "follower" not in payload

    async def test_follower_status_available_in_embedded_mode(self, monkeypatch):
        engine = MagicMock()
        engine.snapshot = MagicMock(return_value={"mode": "follower", "running": True})
        monkeypatch.setattr(main_module, "follower_engine", engine)
        monkeypatch.setattr(main_module.settings, "follower_embedded", True)
        monkeypatch.setattr(main_module.settings, "bot_mode", "scalper")
        payload = await main_module.follower_status()
        assert payload["running"] is True

    async def test_follower_status_404_without_follower(self, monkeypatch):
        monkeypatch.setattr(main_module.settings, "follower_embedded", False)
        monkeypatch.setattr(main_module.settings, "bot_mode", "scalper")
        with pytest.raises(HTTPException) as exc:
            await main_module.follower_status()
        assert exc.value.status_code == 404

    def test_dashboard_marks_ap_rows(self):
        from pathlib import Path

        html = (
            Path(__file__).resolve().parents[1] / "static" / "dashboard.html"
        ).read_text(encoding="utf-8")
        assert "trade-ap" in html
        assert "badge-strat-ap" in html
        assert "renderFollower" in html
        # Kart MEVCUT /api/status gövdesinden beslenir — yeni yoklama YOK.
        assert 'fetchJSON("/follower/status")' not in html


# ==========================================================================
# 9) /risk-event iki motoru da kapsar
# ==========================================================================

class TestRiskEventCoversBothEngines:
    def test_risk_engines_lists_both_when_embedded(self, monkeypatch):
        scalper = MagicMock()
        follower = MagicMock()
        monkeypatch.setattr(main_module, "scalper_engine", scalper)
        monkeypatch.setattr(main_module, "follower_engine", follower)
        assert main_module._risk_engines() == [scalper, follower]
        # Tek motorlu kurulumda liste tek elemanlıdır (davranış aynı).
        monkeypatch.setattr(main_module, "follower_engine", None)
        assert main_module._risk_engines() == [scalper]


# ==========================================================================
# 10) ledger_report strateji bölümü
# ==========================================================================

class TestLedgerReportStrategySection:
    def test_strategy_table_splits_c_and_ap(self):
        import sys
        from pathlib import Path

        # tests/test_ledger_report.py ile AYNI desen: scripts/ sys.path'e
        # eklenir ve modül ADIYLA import edilir (dataclass çözümlemesi
        # modülün sys.modules'ta kayıtlı olmasını gerektirir).
        scripts_dir = str(Path(__file__).resolve().parents[1] / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        import ledger_report as lr

        def trade(idx, strategy, pnl):
            return lr.ClosedTrade(
                id=idx,
                strategy=strategy,
                symbol="BTCUSDT",
                direction="LONG",
                realized_pnl=pnl,
                exit_reason="SL",
                closed_at=datetime(2026, 8, 23, 12, idx),
                day="2026-08-23",
            )

        rows = lr.build_strategy_table(
            [trade(1, "C", 10.0), trade(2, "AP", -4.0), trade(3, "AP", 2.0)]
        )
        by_strategy = {r["strategy"]: r for r in rows}
        assert set(by_strategy) == {"C", "AP"}
        assert by_strategy["AP"]["trades"] == 2
        assert by_strategy["AP"]["pnl"] == pytest.approx(-2.0)
        assert by_strategy["C"]["pnl"] == pytest.approx(10.0)
