"""`BOT_MODE` ayrımı ve halka izolasyonu (D20).

İki iddia:
  1. Takipçi halkası mainnet'e KENDİ BAŞINA çıkamaz (config fail-fast,
     docs/MAINNET_PLAN.md §6) ve bilinmeyen bir BOT_MODE sessizce scalper
     gibi davranmaz.
  2. `BOT_MODE=scalper` (varsayılan) iken takipçi eklemeleri bugünkü davranışı
     DEĞİŞTİRMEZ: köprü kapalı, `/follower/*` uçları motorsuz, ExitPlan'ın
     yeni TP3 alanları scalper'da nötr, `record_open` tp3'süz çalışır.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# SQLAlchemy mapper zinciri: waiting_signals → signals foreign key'i ancak
# her iki model modülü de içe aktarılınca çözülür (aynı desen
# tests/test_shadow_mode.py'de scalper.executor import'u üzerinden gelir).
import src.models.signal  # noqa: F401
import src.models.position  # noqa: F401
import src.models.waiting_signal  # noqa: F401
import src.strategies.scalper.tracker as tracker_module
from src.core.config import Settings
from src.core.database import Base
from src.models.scalp_trade import ScalpTradeModel
from src.strategies.scalper.tracker import ScalpTracker
from src.strategies.scalper.types import Direction, ExitPlan, Regime, ScalpSignal


def _settings(**overrides) -> Settings:
    values = dict(
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
    values.update(overrides)
    return Settings(_env_file=None, **values)


class TestBotModeValidation:
    def test_default_is_scalper(self):
        settings = _settings()
        assert settings.bot_mode == "scalper"
        assert settings.is_follower_mode is False

    def test_follower_mode_recognized(self):
        settings = _settings(bot_mode="follower", risk_event_secret="r")
        assert settings.is_follower_mode is True

    def test_case_and_whitespace_normalized(self):
        assert _settings(
            bot_mode="  Follower ", risk_event_secret="r"
        ).is_follower_mode is True

    def test_typo_is_rejected_fail_fast(self):
        """`BOT_MODE=folower` sessizce scalper'a düşerse İKİ motor aynı
        hesapta işlem açar — startup'ta reddedilir."""
        with pytest.raises(ValueError, match="BOT_MODE"):
            _settings(bot_mode="folower")

    def test_empty_bot_mode_rejected(self):
        with pytest.raises(ValueError):
            _settings(bot_mode="")


class TestFollowerNeverOnMainnet:
    def test_mainnet_follower_rejected(self):
        with pytest.raises(ValueError, match="BOT_MODE=follower"):
            _settings(
                bot_mode="follower",
                binance_base_url="https://fapi.binance.com",
                allow_mainnet=True,
                app_env="production",
                risk_event_secret="r",
                tv_webhook_secret="t",
                scalper_symbol_allowlist="BTCUSDT",
            )

    def test_testnet_follower_allowed(self):
        settings = _settings(bot_mode="follower", risk_event_secret="r")
        assert settings.is_follower_mode is True
        assert settings.is_testnet is True

    def test_mainnet_scalper_still_allowed(self):
        """Scalper halkasının mainnet yolu bu değişiklikten ETKİLENMEZ."""
        settings = _settings(
            binance_base_url="https://fapi.binance.com",
            allow_mainnet=True,
            app_env="production",
            risk_event_secret="r",
            tv_webhook_secret="t",
            scalper_symbol_allowlist="BTCUSDT",
        )
        assert settings.is_follower_mode is False


class TestFollowerRequiresAKillSwitch:
    """Takipçi TESTNET'te bile RISK_EVENT_SECRET olmadan başlayamaz.

    Halkanın tek uzaktan durdurma yolu `POST /risk-event`tir: Telegram yok,
    scanner yok ve köprüyü kapatmak yalnız YENİ sinyali keser — açık pozisyonu
    kapatmaz. Marj %10 + ≤100x kaldıraçlı bir halkanın "durdurulamaz"
    başlaması kabul edilemez.
    """

    def test_follower_without_risk_event_secret_is_rejected(self):
        with pytest.raises(ValueError, match="RISK_EVENT_SECRET"):
            _settings(bot_mode="follower")

    def test_blank_risk_event_secret_is_rejected(self):
        with pytest.raises(ValueError, match="RISK_EVENT_SECRET"):
            _settings(bot_mode="follower", risk_event_secret="   ")

    def test_scalper_ring_is_unaffected(self):
        """Scalper testnet'te BUGÜNKÜ gibi secret'sız başlayabilir."""
        settings = _settings()
        assert settings.risk_event_secret == ""
        assert settings.is_follower_mode is False


class TestScalperDefaultsUnchanged:
    def test_forward_bridge_disabled_by_default(self):
        settings = _settings()
        assert settings.follower_forward_url == ""
        assert settings.follower_forward_secret == ""

    def test_follower_sizing_defaults_match_user_decision(self):
        settings = _settings()
        assert settings.follower_margin_pct == 10.0
        assert settings.follower_sl_roi_target == 30.0
        assert settings.follower_lev_min == 3
        assert settings.follower_lev_max == 100
        assert settings.follower_max_positions == 4
        assert settings.follower_cooldown_sec == 60.0
        assert settings.follower_flip is True
        assert settings.follower_min_score == 0.0
        assert settings.follower_timeframe == "1"
        assert settings.follower_symbol_allowlist.split(",") == [
            "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT",
            "DOGEUSDT", "BNBUSDT", "ADAUSDT", "LTCUSDT",
        ]
        assert (
            settings.follower_tp_rr1,
            settings.follower_tp_rr2,
            settings.follower_tp_rr3,
        ) == (0.5, 1.0, 1.5)

    def test_exit_plan_tp3_fields_are_neutral_by_default(self):
        """Scalper'ın ExitPlan'ı TP3 alanlarını HİÇ doldurmaz."""
        plan = ExitPlan(
            tp1_price=1.0,
            tp1_quantity=1.0,
            tp2_price=2.0,
            tp2_quantity=1.0,
            runner_quantity=1.0,
            initial_stop=0.5,
            breakeven_price=1.0,
            chandelier_atr_mult=2.5,
        )
        assert plan.tp3_price == 0.0
        assert plan.tp3_quantity == 0.0
        assert plan.tp3_algo_id is None


class TestTrackerTp3Column:
    """Gerçek (geçici) SQLite: tp3 kolonu opsiyoneldir, scalper'ı etkilemez."""

    @pytest.fixture
    async def sqlite_tracker(self, tmp_path, monkeypatch):
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'t.db'}")
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        monkeypatch.setattr(tracker_module, "AsyncSessionLocal", session_factory)
        yield ScalpTracker(), session_factory
        await engine.dispose()

    def _signal(self, strategy="C"):
        return ScalpSignal(
            strategy=strategy,
            symbol="BTCUSDT",
            direction=Direction.LONG,
            entry_price=100.0,
            stop_price=99.0,
            reason="test",
            regime=Regime.RANGE,
            atr_5m=1.0,
        )

    async def test_scalper_open_leaves_tp3_null(self, sqlite_tracker):
        tracker, session_factory = sqlite_tracker
        trade_id = await tracker.record_open(
            signal=self._signal(),
            entry_price=100.0,
            quantity=1.0,
            leverage=20,
            margin_usdt=5.0,
            sl_algo_id="1",
            tp1_algo_id="2",
            tp2_algo_id="3",
        )
        async with session_factory() as session:
            row = await session.get(ScalpTradeModel, trade_id)
            assert row.tp3_algo_id is None
            assert row.tp2_algo_id == "3"

    async def test_follower_open_persists_tp3(self, sqlite_tracker):
        tracker, session_factory = sqlite_tracker
        trade_id = await tracker.record_open(
            signal=self._signal(strategy="AP"),
            entry_price=100.0,
            quantity=1.0,
            leverage=100,
            margin_usdt=1.0,
            sl_algo_id="1",
            tp1_algo_id="2",
            tp2_algo_id="3",
            tp3_algo_id="4",
        )
        async with session_factory() as session:
            row = await session.get(ScalpTradeModel, trade_id)
            assert row.tp3_algo_id == "4"
            assert row.strategy == "AP"
