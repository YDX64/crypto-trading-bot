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
import json
import pathlib
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

#: `Settings(_env_file=None, …)` için zorunlu alanlar (tests/test_follower_mode.py
#: ile aynı desen): dosya okunmadığı için tümü açıkça verilmelidir.
_REQUIRED_SETTINGS = dict(
    binance_api_key="x",
    binance_api_secret="x",
    telegram_bot_token="x",
    telegram_chat_id="x",
    openai_api_key="x",
    gemini_api_key="x",
    deepseek_api_key="x",
    jwt_secret="x",
    binance_base_url="https://testnet.binancefuture.com",
)

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
    def test_out_of_band_sl_margin_rejected_when_follower_active(
        self, monkeypatch, value
    ):
        with pytest.raises(Exception):
            self._settings(
                monkeypatch,
                FOLLOWER_EMBEDDED="true",
                FOLLOWER_SL_MARGIN_PCT=value,
            )

    @pytest.mark.parametrize("value", ["9", "51"])
    def test_out_of_band_sl_margin_only_warns_when_follower_off(
        self, monkeypatch, value
    ):
        """Kapalı bir özelliğin ayarı ANA süreci başlatamaz hâle GETİRMEZ.

        Düşmanca inceleme: `.env`'de D20 döneminden kalan bant dışı bir
        FOLLOWER_SL_ROI_TARGET, takipçiyi hiç çalıştırmayan scalper sürecinin
        deploy'unu geri aldırıyordu.
        """
        with pytest.warns(UserWarning):
            cfg = self._settings(monkeypatch, FOLLOWER_SL_MARGIN_PCT=value)
        assert cfg.follower_active is False

    def test_embedded_requires_scalper_enabled(self, monkeypatch):
        with pytest.raises(Exception) as exc:
            self._settings(
                monkeypatch, FOLLOWER_EMBEDDED="true", SCALPER_ENABLED="false"
            )
        assert "SCALPER_ENABLED" in str(exc.value)

    def test_embedded_requires_positive_virtual_capital(self, monkeypatch):
        with pytest.raises(Exception) as exc:
            self._settings(
                monkeypatch,
                FOLLOWER_EMBEDDED="true",
                FOLLOWER_VIRTUAL_CAPITAL_USDT="0",
            )
        assert "FOLLOWER_VIRTUAL_CAPITAL_USDT" in str(exc.value)

    def test_follower_symbols_cannot_empty_the_scalper_universe(self, monkeypatch):
        with pytest.raises(Exception) as exc:
            self._settings(
                monkeypatch,
                FOLLOWER_EMBEDDED="true",
                FOLLOWER_SYMBOLS="BTCUSDT,ETHUSDT",
                SCALPER_SYMBOL_ALLOWLIST="BTCUSDT,ETHUSDT",
            )
        assert "boşaltıyor" in str(exc.value)

    def test_partial_overlap_with_scalper_universe_is_allowed(self, monkeypatch):
        cfg = self._settings(
            monkeypatch,
            FOLLOWER_EMBEDDED="true",
            FOLLOWER_SYMBOLS="BTCUSDT",
            SCALPER_SYMBOL_ALLOWLIST="BTCUSDT,ETHUSDT",
        )
        assert cfg.follower_reserved_symbols == ["BTCUSDT"]

    def test_lev_max_can_be_set_by_field_name(self):
        """`populate_by_name`: alan adı + alias İKİSİ de çalışır."""
        cfg = Settings(_env_file="env.example", follower_lev_max=42)
        assert cfg.follower_lev_max == 42

    def test_conflicting_leverage_aliases_fail_fast(self, monkeypatch):
        """İki ad AYNI ayardır; takipçi AKTİFKEN sessiz galip olmaz."""
        with pytest.raises(Exception) as exc:
            self._settings(
                monkeypatch,
                FOLLOWER_EMBEDDED="true",
                FOLLOWER_LEV_MAX="50",
                FOLLOWER_MAX_LEVERAGE="100",
            )
        assert "AYAR ÇELİŞKİSİ" in str(exc.value)

    def test_conflicting_aliases_only_warn_when_follower_off(self, monkeypatch):
        """Takipçi kapalıyken ikilik ANA süreci başlatamaz hâle GETİRMEZ."""
        with pytest.warns(UserWarning):
            cfg = self._settings(
                monkeypatch, FOLLOWER_LEV_MAX="50", FOLLOWER_MAX_LEVERAGE="100"
            )
        assert cfg.follower_active is False

    def test_env_file_none_does_not_read_any_file(self, tmp_path, monkeypatch):
        """`_env_file=None` → HİÇBİR dosya okunmaz (doğrulayıcı bulgusu Y2).

        Sunucunun canlı `.env`'inde iki ad birden varken, izole bir
        `Settings(_env_file=None)` kurulumu bundan ETKİLENMEMELİDİR — aksi
        halde bir ayar dosyası deploy test kapısını kırar (bulgu 29 sınıfı).
        """
        env_file = tmp_path / ".env"
        env_file.write_text(
            "FOLLOWER_LEV_MAX=50\nFOLLOWER_MAX_LEVERAGE=25\n", encoding="utf-8"
        )
        monkeypatch.chdir(tmp_path)
        cfg = Settings(_env_file=None, **_REQUIRED_SETTINGS)
        assert cfg.follower_lev_max == 100  # dosya OKUNMADI → varsayılan

    def test_given_env_file_is_honoured(self, tmp_path, monkeypatch):
        """Örneğe VERİLEN dosya okunur (sınıf varsayılanı değil)."""
        env_file = tmp_path / "custom.env"
        env_file.write_text("FOLLOWER_MAX_LEVERAGE=37\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        cfg = Settings(_env_file=str(env_file), **_REQUIRED_SETTINGS)
        assert cfg.follower_lev_max == 37

    def test_matching_leverage_aliases_are_accepted(self, monkeypatch):
        cfg = self._settings(
            monkeypatch, FOLLOWER_LEV_MAX="50", FOLLOWER_MAX_LEVERAGE="50"
        )
        assert cfg.follower_lev_max == 50

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
    """Gömülü modda scalper'ın günlük PnL kaynağı KENDİ DEFTERİDİR.

    Düşmanca inceleme (KRİTİK): income'dan AP'yi düşme yaklaşımı 120 sn
    önbellek yüzünden çağrıların ~%98'inde uygulanmıyordu ve AP'nin KISMİ TP
    dolumları hiç düşülemiyordu. Kill switch bir LATCH olduğu için tek kirli
    okuma scalper'ın tüm gününü kapatabilirdi.
    """

    def _scalper(self, cfg, tracker):
        from src.strategies.scalper.engine import ScalperEngine

        engine = object.__new__(ScalperEngine)
        engine.cfg = cfg
        engine.logger = MagicMock()
        engine.tracker = tracker
        engine._kill_switch = False
        engine._kill_switch_day = None
        engine._signals_today = 0
        engine._daily_pnl = 0.0
        engine._daily_pnl_source = "unavailable"
        engine._risk_ready = False
        engine._risk_equity_usdt = None
        engine._risk_equity_source = "disabled"
        engine._daily_loss_threshold_usdt = None
        engine._daily_income_cache = (None, 0.0, None)
        engine._income_cache_close_seq = -1
        engine._balance_cache = (None, 0.0)
        return engine

    def _cfg_scalper(self, **kw):
        base = dict(
            follower_embedded=True,
            scalper_daily_loss_limit_pct=10.0,
            scalper_virtual_capital_usdt=0.0,
        )
        base.update(kw)
        return SimpleNamespace(**base)

    async def test_ledger_source_excludes_ap(self):
        tracker = _tracker()
        tracker.realized_pnl_since = AsyncMock(return_value=-50.0)
        engine = self._scalper(self._cfg_scalper(), tracker)
        assert await engine._ledger_daily_pnl("2026-08-23") == pytest.approx(-50.0)
        kwargs = tracker.realized_pnl_since.await_args.kwargs
        assert kwargs["exclude_strategies"] == ("AP",)
        args = tracker.realized_pnl_since.await_args.args
        assert args[0] == datetime(2026, 8, 23)

    async def test_repeated_calls_return_the_same_value(self):
        """Önbellek-kirliliği sınıfı KÖKTEN kapalı: iki ardışık çağrı AYNI."""
        tracker = _tracker()
        tracker.realized_pnl_since = AsyncMock(return_value=-50.0)
        engine = self._scalper(self._cfg_scalper(), tracker)
        first = await engine._ledger_daily_pnl("2026-08-23")
        second = await engine._ledger_daily_pnl("2026-08-23")
        assert first == second == pytest.approx(-50.0)
        assert tracker.realized_pnl_since.await_count == 2

    async def test_kill_switch_uses_ledger_in_embedded_mode(self):
        tracker = _tracker()
        # AP bugün −500 kaybetmiş olsun; scalper defteri yalnız −20 gösterir.
        tracker.realized_pnl_since = AsyncMock(return_value=-20.0)
        engine = self._scalper(self._cfg_scalper(), tracker)
        engine._get_cached_balance = AsyncMock(return_value=1000.0)
        engine._get_account_daily_net_income = AsyncMock(return_value=-520.0)
        await engine._update_kill_switch()
        assert engine._daily_pnl == pytest.approx(-20.0)
        assert engine._daily_pnl_source == "scalper_ledger"
        # Takipçinin zararı scalper'ın kesicisini TETİKLEMEZ.
        assert engine._kill_switch is False
        # Hesap income'ı YALNIZ BİLGİ amaçlı okunur (D20b/Y8): kesiciyi
        # beslemez ama iki sayının farkı `/scalper/status`ta görünür.
        assert engine._daily_income_account == pytest.approx(-520.0)

    async def test_income_path_is_unchanged_when_embedded_off(self):
        tracker = _tracker()
        tracker.realized_pnl_since = AsyncMock(return_value=-20.0)
        engine = self._scalper(self._cfg_scalper(follower_embedded=False), tracker)
        engine._get_cached_balance = AsyncMock(return_value=1000.0)
        engine._get_account_daily_net_income = AsyncMock(return_value=-30.0)
        await engine._update_kill_switch()
        assert engine._daily_pnl == pytest.approx(-30.0)
        assert engine._daily_pnl_source == "binance_account_income"
        tracker.realized_pnl_since.assert_not_awaited()

    async def test_ledger_failure_is_fail_closed(self):
        tracker = _tracker()
        tracker.realized_pnl_since = AsyncMock(side_effect=RuntimeError("db kilitli"))
        engine = self._scalper(self._cfg_scalper(), tracker)
        await engine._update_kill_switch()
        assert engine._risk_ready is False
        assert engine._daily_pnl_source == "unavailable"


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
        engine._orphans_check_ok = True
        symbol_reservations.reserve("BTCUSDT", "follower")
        engine._sync_follower_reservations()
        assert symbol_reservations.owner("BTCUSDT") is None

    async def test_failed_orphan_audit_keeps_reservation(self, tmp_path):
        """Borsa okuması patladığında sahiplik BIRAKILMAZ (fail-closed).

        Düşmanca inceleme: `_check_orphans` hata hâlinde latch KURMADAN
        döner; senkron o turda sahipliği bırakırsa defter satırı yazılamamış
        açık bir pozisyon hem defterden hem kayıttan düşerdi.
        """
        engine = _make_engine(tmp_path)
        engine.client.get_all_positions = AsyncMock(side_effect=RuntimeError("418"))
        symbol_reservations.reserve("BTCUSDT", "follower")
        await engine._check_orphans()
        assert engine._orphans_check_ok is False
        engine._sync_follower_reservations()
        assert symbol_reservations.owner("BTCUSDT") == "follower"

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

    async def test_other_engines_TRACKED_position_is_not_an_orphan(self, tmp_path):
        """Rezervasyon DONMUŞ olsa bile gerçek izleme listesi kaynaktır.

        Düşmanca inceleme: scalper entry-halt'a düştüğünde rezervasyonları
        donar; yetim denetimi yalnız kayda bakarsa gerçek bir yetim o
        sembolde GÖRÜNMEZ olur. Bu yüzden diğer motorun GERÇEK izleme
        listesi ayrı bir kaynak olarak enjekte edilir.
        """
        engine = _make_engine(
            tmp_path,
            all_positions=[{"symbol": "ETHUSDT", "positionAmt": "1.5"}],
        )
        engine.foreign_tracked_cb = lambda: {"ETHUSDT"}
        assert await engine._check_orphans() == []
        assert engine._entry_halted is False
        assert engine._unknown_positions == []

    async def test_embedded_unowned_position_warns_but_never_halts(self, tmp_path):
        """Gömülü mod: SAHİPSİZ pozisyon = MEŞRU olabilir (elle/Telegram)."""
        engine = _make_engine(
            tmp_path,
            all_positions=[{"symbol": "ETHUSDT", "positionAmt": "1.5"}],
        )
        assert await engine._check_orphans() == []
        assert engine._entry_halted is False
        assert engine._orphans == []
        assert engine._unknown_positions == ["ETHUSDT"]
        assert engine._reject_counters.get("unknown_position") == 1
        assert engine.logger.warning.called
        # Kalıcı kilit dosyası YAZILMAMALI.
        assert not (tmp_path / "follower_entry_halt.json").exists()

    async def test_separate_ring_still_latches_entry_halt(self, tmp_path):
        """Ayrı halka (BOT_MODE=follower) D20a davranışını AYNEN korur."""
        engine = _make_engine(
            tmp_path,
            _cfg(follower_embedded=False),
            all_positions=[{"symbol": "ETHUSDT", "positionAmt": "1.5"}],
        )
        orphans = await engine._check_orphans()
        assert orphans == ["ETHUSDT"]
        assert engine._entry_halted is True

    async def test_embedded_flatten_does_not_touch_foreign_positions(self, tmp_path):
        """`/risk-event flatten` yabancı/sahipsiz pozisyona DOKUNMAZ."""
        engine = _make_engine(
            tmp_path,
            all_positions=[
                {"symbol": "ETHUSDT", "positionAmt": "1.5"},   # Telegram/elle
                {"symbol": "BTCUSDT", "positionAmt": "-0.2"},  # scalper
            ],
        )
        symbol_reservations.reserve("BTCUSDT", "scalper")
        engine.client.quantize_quantity = AsyncMock(side_effect=lambda s, q: q)
        engine._submit_reduce_only_market_close = AsyncMock()
        flattened, errors = await engine._flatten_orphans(set())
        assert flattened == []
        assert errors == []
        engine._submit_reduce_only_market_close.assert_not_called()
        assert engine.logger.critical.called


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
        # TEK AD: köprü yolu ile motor yolu AYNI reason/sayaç adını kullanır.
        assert result["reason"] == "symbol_not_in_follower_universe"
        assert "evren" in result["detail"]
        assert engine._reject_counters.get("symbol_not_in_follower_universe") == 1
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


# ==========================================================================
# 11) Defter izolasyonu: restart kurtarması (düşmanca inceleme, KRİTİK)
# ==========================================================================

class TestRecoveryLedgerIsolation:
    """Gömülü modda iki motor AYNI `scalp_trades` tablosunu paylaşır.

    Filtre YOKKEN her motorun `recover()`'ı DİĞERİNİN açık satırını kendi
    pozisyonu sanıp izlemeye alıyordu: aynı net pozisyonun İKİ yöneticisi
    (iki stop taşıma, iki kapanış defteri) ve AlgoPro pozisyonuna scalper'ın
    chandelier/reaper kuralları.
    """

    @pytest.fixture
    async def sqlite_tracker(self, tmp_path, monkeypatch):
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from src.core.database import Base
        from src.strategies.scalper import tracker as tracker_module
        from src.strategies.scalper.tracker import ScalpTracker

        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'iso.db'}")
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        monkeypatch.setattr(tracker_module, "AsyncSessionLocal", session_factory)
        yield ScalpTracker()
        await engine.dispose()

    async def _seed(self, tracker):
        from src.strategies.scalper.types import Direction, Regime, ScalpSignal

        for strategy, symbol in (("C", "BTCUSDT"), ("AP", "ADAUSDT")):
            await tracker.record_open(
                signal=ScalpSignal(
                    strategy=strategy,
                    symbol=symbol,
                    direction=Direction.LONG,
                    entry_price=100.0,
                    stop_price=99.0,
                    reason="seed",
                    regime=Regime.RANGE,
                    atr_5m=1.0,
                ),
                entry_price=100.0,
                quantity=1.0,
                leverage=10,
                margin_usdt=10.0,
                sl_algo_id="1",
                tp1_algo_id="2",
                tp2_algo_id="3",
            )

    async def test_open_trades_filters_by_strategy(self, sqlite_tracker):
        await self._seed(sqlite_tracker)
        scalper_rows = await sqlite_tracker.open_trades(exclude_strategies=("AP",))
        follower_rows = await sqlite_tracker.open_trades(strategies=("AP",))
        assert {r.symbol for r in scalper_rows} == {"BTCUSDT"}
        assert {r.symbol for r in follower_rows} == {"ADAUSDT"}
        # PARİTE: iki motorun kurtardığı kümelerin KESİŞİMİ BOŞ.
        assert not ({r.id for r in scalper_rows} & {r.id for r in follower_rows})
        # Filtresiz çağrı eski davranışı korur (geri uyum).
        assert len(await sqlite_tracker.open_trades()) == 2

    async def test_recovery_strategies_are_mirror_images(self):
        from src.strategies.follower.exits import FollowerExitManager
        from src.strategies.scalper.exits import ExitManager

        scalper = object.__new__(ExitManager)
        follower = object.__new__(FollowerExitManager)
        assert scalper.recovery_strategies() == (None, ("AP",))
        assert follower.recovery_strategies() == (("AP",), None)

    async def test_recover_skips_foreign_rows_as_second_defense(self, tmp_path):
        """Filtre bir yoldan atlanırsa satır YİNE de elenir + WARNING."""
        from src.strategies.scalper.exits import ExitManager

        manager = object.__new__(ExitManager)
        manager.logger = MagicMock()
        manager.tracker = SimpleNamespace(
            open_trades=AsyncMock(
                return_value=[SimpleNamespace(id=7, symbol="ADAUSDT", strategy="AP")]
            )
        )
        manager._recover_one = AsyncMock(return_value=True)
        assert await manager.recover() is True
        manager._recover_one.assert_not_called()
        assert manager.logger.warning.called

    async def test_deferred_recovery_also_reserves_symbols(self, tmp_path):
        """Ertelenmiş kurtarma yolu (ilk prob başarısız) da sahiplik ALIR."""
        engine = _make_engine(tmp_path, positions={"BTCUSDT": _fake_position()})
        engine.exits.recover = AsyncMock(return_value=True)
        symbol_reservations.clear()
        assert await engine._attempt_recovery() is True
        assert symbol_reservations.owner("BTCUSDT") == "follower"

    async def test_recovery_conflict_does_not_write_a_persistent_halt(self, tmp_path):
        """Çakışma RAM'de girişleri kapatır ama KALICI kilit YAZMAZ.

        Düşmanca inceleme: kalıcı dosya, her restart'ta operatörün elle dosya
        silmesini gerektiriyordu (açık pozisyon varsa deterministik).
        """
        engine = _make_engine(tmp_path, positions={"BTCUSDT": _fake_position()})
        engine.exits.recover = AsyncMock(return_value=True)
        symbol_reservations.reserve("BTCUSDT", "scalper")
        assert await engine._attempt_recovery() is False
        assert not (tmp_path / "follower_entry_halt.json").exists()
        assert engine._entry_halted is False
        assert engine.logger.critical.called


# ==========================================================================
# 12) Köprü: evren kapısı + TEK ayrıştırıcı
# ==========================================================================

class TestRoutingUniverseGate:
    @pytest.fixture
    def wired(self, monkeypatch):
        monkeypatch.setattr(main_module.settings, "tv_webhook_secret", TV_SECRET)
        monkeypatch.setattr(main_module.settings, "tv_confluence_required", 1)
        monkeypatch.setattr(main_module.settings, "follower_embedded", True)
        monkeypatch.setattr(main_module.settings, "follower_symbols", "ADAUSDT")
        scalper = MagicMock()
        scalper.external_signal = AsyncMock(return_value={"accepted": True})
        monkeypatch.setattr(main_module, "scalper_engine", scalper)
        follower = MagicMock()
        follower.handle_event = AsyncMock(
            return_value={"accepted": True, "reason": "pozisyon açıldı"}
        )
        follower.note_route_reject = MagicMock()
        monkeypatch.setattr(main_module, "follower_engine", follower)
        return SimpleNamespace(scalper=scalper, follower=follower)

    async def test_entry_outside_universe_is_reported_not_swallowed(self, wired):
        body = f"{REAL_SELL} secret={TV_SECRET}"  # BTCUSDT, evren ADAUSDT
        result = await main_module.tradingview_webhook(
            _FakeRequest(body.encode(), {})
        )
        assert result["routed"] == "follower"
        assert result["accepted"] is False
        assert result["reason"] == "symbol_not_in_follower_universe"
        # Motora HİÇ ulaşmaz; ana botun oy yoluna da DÜŞMEZ.
        wired.follower.handle_event.assert_not_called()
        wired.scalper.external_signal.assert_not_called()
        # Sessiz KALMAZ: sayaç + WARNING.
        wired.follower.note_route_reject.assert_called_once_with(
            "symbol_not_in_follower_universe", kind="entry"
        )

    async def test_exit_events_are_never_gated_by_the_universe(self, wired):
        """Riskten ÇIKMA hiçbir kapıya takılmaz (D20a bulgu 9 ilkesi)."""
        body = "🛑 SL HIT | BINANCE:BTCUSDT | TF: 1 | Price: 77167.77"
        result = await main_module.tradingview_webhook(
            _FakeRequest(body.encode(), {"secret": TV_SECRET})
        )
        assert result["accepted"] is True
        wired.follower.handle_event.assert_awaited_once()

    async def test_entry_inside_universe_reaches_the_engine(self, wired, monkeypatch):
        monkeypatch.setattr(main_module.settings, "follower_symbols", "BTCUSDT")
        body = f"{REAL_SELL} secret={TV_SECRET}"
        result = await main_module.tradingview_webhook(
            _FakeRequest(body.encode(), {})
        )
        assert result["accepted"] is True
        wired.follower.handle_event.assert_awaited_once()

    async def test_dry_run_matches_the_real_classification(self, wired):
        """TEK AYRIŞTIRICI: dry-run ile gerçek yol AYNI sonucu verir.

        Düşmanca inceleme: karar `algopro_alert_kind`, yürütme
        `parse_follower_event` ile veriliyordu; gövdedeki bir `kind=` belirteci
        dry-run'da "entry" görünürken gerçek istekte EXIT çalıştırıp pozisyonu
        kapatabiliyordu.
        """
        body = f"{REAL_SELL} secret={TV_SECRET}"
        dry = await main_module.tradingview_webhook(
            _FakeRequest(body.encode(), {"dry_run": "1"})
        )
        real = await main_module.tradingview_webhook(
            _FakeRequest(body.encode(), {})
        )
        assert dry["would"]["kind"] == real["kind"]
        assert dry["would"]["symbol"] == real["symbol"]
        assert dry["would"]["accepted"] == real["accepted"]
        # dry-run yanıtı YÖN de raporlar (RUNBOOK doğrulama komutu).
        assert dry["would"]["direction"] == "SHORT"

    async def test_dry_run_uses_the_same_parser_for_kind_template(self, wired):
        """Gövdede `kind=` belirteci varsa dry-run da AYNI olayı raporlar."""
        from src.strategies.follower.parser import parse_follower_event

        body = f"{REAL_SELL} secret={TV_SECRET}"
        expected = parse_follower_event(body)
        dry = await main_module.tradingview_webhook(
            _FakeRequest(body.encode(), {"dry_run": "1"})
        )
        assert dry["would"]["kind"] == expected.kind
        assert dry["would"]["symbol"] == expected.symbol


# ==========================================================================
# 13) Kapasite: her motor YALNIZ kendi pozisyonlarını sayar
# ==========================================================================

class TestCapacityIsolation:
    def test_registry_can_scope_capacity_to_one_owner(self):
        for i in range(4):
            assert symbol_reservations.reserve(f"F{i}USDT", "follower")
        # Hesap-geneli tavan 5 iken takipçinin 4 rezervasyonu scalper'ı
        # 1 slota düşürüyordu; artık scalper YALNIZ kendi sahiplerini sayar.
        assert symbol_reservations.reserve(
            "BTCUSDT", "scalper", capacity=5, capacity_owners=("scalper",)
        )
        # Varsayılan (None) hâlâ HESAP-GENELİ sayar — bugünkü davranış.
        assert not symbol_reservations.reserve("ETHUSDT", "scalper", capacity=5)

    async def test_follower_cannot_exceed_its_own_ceiling(self, tmp_path):
        positions = {
            f"S{i}USDT": _fake_position(symbol=f"S{i}USDT") for i in range(4)
        }
        engine = _make_engine(tmp_path, positions=positions)
        for symbol in positions:
            symbol_reservations.reserve(symbol, "follower")
        # 5. sembol: takipçinin KENDİ tavanı (4) doludur.
        assert engine._reserve_symbol("BTCUSDT", enforce_capacity=True) is False
        # Kurtarma yolunda tavan UYGULANMAZ (açık pozisyon sahipsiz kalmasın).
        assert engine._reserve_symbol("BTCUSDT") is True

    async def test_follower_positions_do_not_consume_scalper_slots(self, tmp_path):
        from src.strategies.scalper.engine import ScalperEngine

        scalper = object.__new__(ScalperEngine)
        scalper.cfg = SimpleNamespace(follower_embedded=True)
        for i in range(4):
            symbol_reservations.reserve(f"F{i}USDT", "follower")
        assert len(scalper._follower_managed_symbols()) == 4
        # Bayrak kapalıyken küme BOŞtur → hesap-geneli sayım bugünküyle aynı.
        scalper.cfg = SimpleNamespace(follower_embedded=False)
        assert scalper._follower_managed_symbols() == set()


# ==========================================================================
# 14) Pano/rapor ayrımı
# ==========================================================================

class TestReportSeparation:
    async def test_scalper_stats_combined_excludes_ap(self, monkeypatch):
        captured = {}

        class _Result:
            def scalars(self):
                return SimpleNamespace(all=lambda: [])

        class _DB:
            async def execute(self, stmt):
                captured["sql"] = str(stmt)
                return _Result()

        monkeypatch.setattr(main_module, "scalper_engine", None)
        payload = await main_module.scalper_stats(db=_DB())
        assert payload["combined"]["scope"] == "!AP"
        assert "strategy !=" in captured["sql"] or "strategy IS NOT" in captured["sql"]

    async def test_scalper_stats_can_target_ap(self, monkeypatch):
        captured = {}

        class _Result:
            def scalars(self):
                return SimpleNamespace(all=lambda: [])

        class _DB:
            async def execute(self, stmt):
                captured["sql"] = str(stmt)
                return _Result()

        monkeypatch.setattr(main_module, "scalper_engine", None)
        payload = await main_module.scalper_stats(db=_DB(), strategy="ap")
        assert payload["combined"]["scope"] == "AP"

    async def test_forensics_summary_excludes_ap_by_default(self, monkeypatch):
        seen = {}

        async def _summary(since=None, until=None, **kwargs):
            seen.update(kwargs)
            return {}

        monkeypatch.setattr(
            main_module.ScalpTracker, "forensics_summary", staticmethod(_summary)
        )
        await main_module.scalper_forensics_summary()
        assert seen["exclude_strategies"] == ("AP",)
        assert seen["strategies"] is None

        seen.clear()
        await main_module.scalper_forensics_summary(strategy="AP")
        assert seen["strategies"] == ("AP",)
        assert seen["exclude_strategies"] is None

    def test_dashboard_shows_unknown_positions_and_combined_scope(self):
        from pathlib import Path

        html = (
            Path(__file__).resolve().parents[1] / "static" / "dashboard.html"
        ).read_text(encoding="utf-8")
        assert "unknown_positions" in html
        assert "SAHİPSİZ POZİSYON" in html
        assert "TOPLAM — Scalper defteri (AP hariç)" in html


# ==========================================================================
# 15) Sanal defter önbelleği
# ==========================================================================

class TestVirtualEquityCache:
    async def test_repeated_reads_hit_the_cache(self, tmp_path):
        engine = _make_engine(tmp_path, tracker=_tracker(eligible=50.0))
        assert await engine._virtual_equity() == pytest.approx(1050.0)
        assert await engine._virtual_equity() == pytest.approx(1050.0)
        assert engine.tracker.compounding_snapshot.await_count == 1

    async def test_close_invalidates_the_cache(self, tmp_path):
        tracker = _tracker(eligible=50.0)
        engine = _make_engine(tmp_path, tracker=tracker)
        assert await engine._virtual_equity() == pytest.approx(1050.0)
        # Kapanış: `close_seq` artar → sanal sermaye ANINDA tazelenir.
        tracker.close_seq = 1
        tracker.compounding_snapshot = AsyncMock(
            return_value={"eligible_realized_pnl": -10.0}
        )
        assert await engine._virtual_equity() == pytest.approx(990.0)


# ==========================================================================
# 16) AP girişleri adli deftere (logs/trades.jsonl) yazılır
# ==========================================================================

class TestFollowerForensicsEntryLine:
    def test_executor_appends_the_entry_event(self):
        source = (
            pathlib.Path(__file__).resolve().parents[1]
            / "src/strategies/follower/executor.py"
        ).read_text(encoding="utf-8")
        assert "forensics_log.append_soon(" in source
        assert '"entry",' in source
        assert "forensics_entry=(forensics_document or {}).get(\"entry\")" in source


# ==========================================================================
# 17) Yetim AYRIMI: "benim yetimim" ile "sahipsiz" AYNI ŞEY DEĞİLDİR
# ==========================================================================

class TestOwnOrphanVsUnknown:
    """Kullanıcı kararı: entry-halt/flatten muafiyeti YALNIZ *rezerve
    edilmemiş* pozisyonlar içindir. Takipçinin KENDİ rezervasyonunu taşıyan
    ama izlemediği pozisyon (ör. `record_open` DB hatası) onun YETİMİDİR ve
    D20a davranışını gömülü modda da hak eder."""

    async def test_own_reserved_untracked_position_is_a_real_orphan(self, tmp_path):
        engine = _make_engine(
            tmp_path,
            all_positions=[{"symbol": "ADAUSDT", "positionAmt": "10"}],
        )
        symbol_reservations.reserve("ADAUSDT", "follower")
        orphans = await engine._check_orphans()
        assert orphans == ["ADAUSDT"]
        assert engine._entry_halted is True
        assert engine._unknown_positions == []

    async def test_unreserved_position_stays_unknown(self, tmp_path):
        engine = _make_engine(
            tmp_path,
            all_positions=[{"symbol": "ADAUSDT", "positionAmt": "10"}],
        )
        assert await engine._check_orphans() == []
        assert engine._entry_halted is False
        assert engine._unknown_positions == ["ADAUSDT"]

    async def test_flatten_closes_own_orphan_but_not_the_unknown_one(self, tmp_path):
        engine = _make_engine(
            tmp_path,
            all_positions=[
                {"symbol": "ADAUSDT", "positionAmt": "10"},   # takipçinin yetimi
                {"symbol": "ETHUSDT", "positionAmt": "1.5"},  # sahipsiz
            ],
        )
        symbol_reservations.reserve("ADAUSDT", "follower")
        engine.client.quantize_quantity = AsyncMock(side_effect=lambda s, q: q)
        engine._submit_reduce_only_market_close = AsyncMock()
        engine.client.get_position_risk = AsyncMock(
            return_value={"positionAmt": "0"}
        )
        flattened, errors = await engine._flatten_orphans(set())
        assert flattened == ["ADAUSDT"]
        assert errors == []
        closed = [c.args[0] for c in engine._submit_reduce_only_market_close.await_args_list]
        assert closed == ["ADAUSDT"]
        assert engine.logger.critical.called  # ETHUSDT atlandı, raporlandı


# ==========================================================================
# 18) Doğrulayıcı turu — regresyon ve kalıntı düzeltmeleri
# ==========================================================================

class TestRecoveryReservationOnFailurePaths:
    """`_attempt_recovery` İSTİSNA yollarında da sahiplik ALIR.

    Doğrulayıcı bulgusu (YÜKSEK regresyon): `exits.recover()` bir satırı
    izlemeye ALIP sonraki satırda patlarsa, erken `return` o sembolü
    "izleniyor ama rezerve DEĞİL" bırakıyordu ve scalper aynı net pozisyona
    girebiliyordu.
    """

    async def test_unprotected_error_still_reserves_tracked_symbols(self, tmp_path):
        from src.trading.position_manager import UnprotectedPositionError

        engine = _make_engine(tmp_path, positions={"BTCUSDT": _fake_position()})
        engine.exits.recover = AsyncMock(
            side_effect=UnprotectedPositionError("korumasız")
        )
        assert await engine._attempt_recovery() is False
        assert symbol_reservations.owner("BTCUSDT") == "follower"
        assert engine._entry_halted is True

    async def test_generic_error_still_reserves_tracked_symbols(self, tmp_path):
        engine = _make_engine(tmp_path, positions={"BTCUSDT": _fake_position()})
        engine.exits.recover = AsyncMock(side_effect=RuntimeError("db yok"))
        assert await engine._attempt_recovery() is False
        assert symbol_reservations.owner("BTCUSDT") == "follower"

    async def test_scalper_cannot_take_a_symbol_reserved_on_the_failure_path(
        self, tmp_path
    ):
        engine = _make_engine(tmp_path, positions={"BTCUSDT": _fake_position()})
        engine.exits.recover = AsyncMock(side_effect=RuntimeError("yarım kurtarma"))
        await engine._attempt_recovery()
        assert (
            symbol_reservations.reserve("BTCUSDT", "scalper") is False
        ), "scalper aynı net pozisyona giremez"


class TestUniverseEmptyGuardUsesRealUniverse:
    """`FOLLOWER_SYMBOLS` gerçek tarama evrenini boşaltıyorsa startup HATASI.

    Doğrulayıcı bulgusu Y1: config kontrolü yalnız `SCALPER_SYMBOL_ALLOWLIST`
    doluyken çalışabiliyordu; canlı `.env`'de o satır YOK ve evren
    `scanner.get_universe()`ten geliyor → koruma ÖLÜYDÜ.
    """

    def _engine(self, reserved, allowlist="", universe=None, boom=False):
        from src.strategies.scalper.engine import ScalperEngine

        engine = object.__new__(ScalperEngine)
        engine.cfg = SimpleNamespace(
            follower_reserved_symbols=reserved,
            scalper_symbol_allowlist=allowlist,
            follower_embedded=True,
        )
        engine.logger = MagicMock()
        getter = (
            AsyncMock(side_effect=RuntimeError("ağ"))
            if boom
            else AsyncMock(return_value=list(universe or []))
        )
        engine.scanner = SimpleNamespace(get_universe=getter)
        return engine

    async def test_scanner_universe_fully_reserved_raises(self):
        engine = self._engine(["BTCUSDT", "ETHUSDT"], universe=["BTCUSDT", "ETHUSDT"])
        with pytest.raises(RuntimeError) as exc:
            await engine._assert_universe_survives_follower_reservation()
        assert "boşaltıyor" in str(exc.value)

    async def test_partial_overlap_is_fine(self):
        engine = self._engine(["BTCUSDT"], universe=["BTCUSDT", "ETHUSDT"])
        await engine._assert_universe_survives_follower_reservation()

    async def test_unreadable_universe_only_warns(self):
        """Borsa erişimi yokken bot BAŞLAMAMAZLIK ETMEZ (418 dersi)."""
        engine = self._engine(["BTCUSDT"], boom=True)
        await engine._assert_universe_survives_follower_reservation()
        assert engine.logger.warning.called

    async def test_no_reserved_symbols_skips_the_check(self):
        engine = self._engine([], universe=[])
        await engine._assert_universe_survives_follower_reservation()
        engine.scanner.get_universe.assert_not_awaited()

    def test_scan_tick_marks_degraded_when_universe_empties(self):
        from src.strategies.scalper.engine import ScalperEngine

        engine = object.__new__(ScalperEngine)
        engine.cfg = SimpleNamespace(follower_reserved_symbols=["BTCUSDT"])
        engine.logger = MagicMock()
        engine._mark_scan_degraded = MagicMock()
        assert engine._exclude_follower_symbols(["BTCUSDT"]) == []
        engine._mark_scan_degraded.assert_called_once()
        assert engine._mark_scan_degraded.call_args.kwargs["kind"] == "universe_empty"


class TestParserClassificationMatchesGate:
    """Katı AlgoPro gövdesinde gövde ORTASINDAKİ `kind=` YOK SAYILIR.

    Doğrulayıcı bulgusu: köprünün katı tanıyıcısı "entry" derken yürütücü
    şablon yolunu seçip EXIT çalıştırabiliyordu (pozisyonu kapatırdı).
    """

    def test_mid_body_kind_does_not_flip_a_strict_entry(self):
        from src.strategies.follower.parser import (
            algopro_alert_kind,
            parse_follower_event,
        )

        body = (
            "🟢 BUY | BINANCE:BTCUSDT | TF: 1 | Price: 77126.08 | SL: 77084.39 "
            "| TP1: 77146.93 | TP2: 77167.77 | TP3: 77188.62 | note kind=exit"
        )
        assert algopro_alert_kind(body) == "entry"
        assert parse_follower_event(body).kind == "entry"

    def test_explicit_template_still_works(self):
        from src.strategies.follower.parser import parse_follower_event

        event = parse_follower_event("src=algopro kind=exit BTCUSDT tf=1")
        assert event.kind == "exit"


class TestRouteRejectTelemetry:
    def test_bridge_reject_moves_event_counters(self, tmp_path):
        engine = _make_engine(tmp_path)
        engine.note_route_reject("symbol_not_in_follower_universe")
        snap = engine.dashboard_snapshot()
        assert snap["events_total"] == 1
        assert snap["last_event_at"] is not None
        assert snap["reject_counters"]["symbol_not_in_follower_universe"] == 1

    async def test_unknown_position_counter_is_per_event(self, tmp_path):
        engine = _make_engine(
            tmp_path, all_positions=[{"symbol": "ETHUSDT", "positionAmt": "1.5"}]
        )
        for _ in range(3):
            await engine._check_orphans()
        # Sembol kümesi DEĞİŞMEDİ → sayaç 3 tur boyunca 1 kalır.
        assert engine._reject_counters["unknown_position"] == 1
        engine.client.get_all_positions = AsyncMock(
            return_value=[{"symbol": "SOLUSDT", "positionAmt": "2"}]
        )
        await engine._check_orphans()
        assert engine._reject_counters["unknown_position"] == 2


class TestRiskEventResumeCoversBothEngines:
    async def test_resume_reports_not_ok_when_follower_stays_halted(
        self, monkeypatch
    ):
        scalper = MagicMock()
        scalper.risk_event_resume = MagicMock(return_value={"active": False})
        follower = MagicMock()
        follower.risk_event_resume = MagicMock(
            return_value={"active": True, "reason": "dosya silinemedi"}
        )
        monkeypatch.setattr(main_module, "scalper_engine", scalper)
        monkeypatch.setattr(main_module, "follower_engine", follower)
        monkeypatch.setattr(main_module.settings, "risk_event_secret", "s" * 20)

        request = _FakeRequest(
            json.dumps({"secret": "s" * 20, "action": "resume"}).encode()
        )
        result = await main_module.risk_event(request)
        assert result["ok"] is False
        follower.risk_event_resume.assert_called_once()


class TestScalperRecoveryConflictWithFollower:
    def test_follower_owned_symbol_is_skipped_not_latched(self):
        """Gömülü modda takipçi sahipli sembol KALICI kilit YAZDIRMAZ."""
        source = (
            pathlib.Path(__file__).resolve().parents[1]
            / "src/strategies/scalper/engine.py"
        ).read_text(encoding="utf-8")
        block = source[source.index("| self.executor.pending_symbols():"):]
        block = block[: block.index("if not self._recovery_ready")]
        assert "FOLLOWER_RESERVATION_OWNER" in block
        assert "continue" in block
        # Diğer sahipler için eski davranış (latch + break) DURUYOR.
        assert "_latch_entry_halt" in block
        assert "break" in block


class TestDisabledFollowerOpenTrades:
    async def test_open_ap_rows_are_reported_when_flag_is_off(self, monkeypatch):
        rows = [SimpleNamespace(id=7, symbol="ADAUSDT", direction="LONG")]
        monkeypatch.setattr(main_module.settings, "follower_embedded", False)
        monkeypatch.setattr(main_module.settings, "bot_mode", "scalper")
        monkeypatch.setattr(
            main_module.ScalpTracker,
            "open_trades",
            AsyncMock(return_value=rows),
        )
        found = await main_module._check_disabled_follower_open_trades()
        assert found == [{"id": 7, "symbol": "ADAUSDT", "direction": "LONG"}]

    async def test_nothing_reported_when_follower_is_active(self, monkeypatch):
        monkeypatch.setattr(main_module.settings, "follower_embedded", True)
        getter = AsyncMock(return_value=[])
        monkeypatch.setattr(main_module.ScalpTracker, "open_trades", getter)
        assert await main_module._check_disabled_follower_open_trades() == []
        getter.assert_not_awaited()


class TestDayStartApproximationExcludesAp:
    async def test_wallet_mode_subtracts_ap_ledger(self):
        from src.strategies.scalper.engine import ScalperEngine

        engine = object.__new__(ScalperEngine)
        engine.cfg = SimpleNamespace(
            follower_embedded=True,
            scalper_daily_loss_limit_pct=10.0,
            scalper_virtual_capital_usdt=0.0,
        )
        engine.logger = MagicMock()
        engine._kill_switch = False
        engine._kill_switch_day = None
        engine._signals_today = 0
        engine._daily_pnl = 0.0
        engine._daily_pnl_source = "unavailable"
        engine._daily_income_account = None
        engine._risk_ready = False
        engine._risk_equity_usdt = None
        engine._risk_equity_source = "disabled"
        engine._daily_loss_threshold_usdt = None
        engine.tracker = SimpleNamespace(
            realized_pnl_since=AsyncMock(return_value=-20.0), close_seq=0
        )
        engine._get_cached_balance = AsyncMock(return_value=1000.0)
        engine._get_account_daily_net_income = AsyncMock(return_value=-500.0)
        engine._ledger_daily_pnl = AsyncMock(
            side_effect=lambda today, strategies=None: (
                -480.0 if strategies else -20.0
            )
        )
        await engine._update_kill_switch()
        # Gün başı = 1000 − (−20 + −480) = 1500 → eşik −150 (AP'nin −480'i
        # scalper'ın eşiğini ŞİŞİRMEZ; cüzdandan düşülür).
        assert engine._daily_loss_threshold_usdt == pytest.approx(-150.0)
