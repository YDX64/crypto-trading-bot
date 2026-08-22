"""Gölge modu (`SCALPER_SHADOW_MODE`, D14, docs/MAINNET_PLAN.md §3) testleri.

Dört katman:
  1. ScalpExecutor.try_open — AĞ YOK (fake client/pm, test_scalper_setups.py ile
     aynı desen): şeffaf kanıt — borsaya emir/margin/leverage isteği GİTMEZ,
     tracker.record_shadow çağrılır, cooldown/pending etkilenmez. "shadow off"
     testi AYNI fake'lerle bugünkü (gerçek emir açan) yolun DEĞİŞMEDİĞİNİ kanıtlar.
  2. ScalpTracker.record_shadow — gerçek (geçici dosya) SQLite üzerinde: DB'ye
     status="SHADOW" satırı yazıldığını, stats()/open_trades()'in bunu SQL
     WHERE seviyesinde dışladığını kanıtlar (mock session değil — gerçek sorgu).
  3. Settings._validate_binance_environment — mainnet'te gölge KAPALIYSA
     risk/webhook secret + allowlist zorunlu; gölge AÇIKKEN bypass; testnet
     etkilenmez.
  4. ScalperEngine._maybe_log_shadow_mode_banner — başlangıç uyarısı.

Backtest harness'e DOKUNULMADI: gölge modu yalnız executor.try_open'ın emir
gönderme noktasını değiştirir; harness (`simulate_symbol`) zaten hiçbir zaman
borsaya çıkmaz, bu nedenle canlı/harness paritesi bu madde için gerekmez
(spesifikasyonda da "canlı-only" olarak işaretli).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# position.py (executor.py'nin bağımlılığı) src.models.signal'i içe aktarır;
# SignalModel'in "WaitingSignalModel" ilişkisi SQLAlchemy mapper
# yapılandırması sırasında çözülebilsin diye bu modül de içe aktarılmalı
# (aksi halde PositionModel() ilk kez örneklendiğinde InvalidRequestError) —
# aynı desen tests/test_scalper_setups.py'de.
import src.models.waiting_signal  # noqa: F401
import src.strategies.scalper.tracker as tracker_module
from src.core.config import Settings
from src.core.database import Base
from src.models.scalp_trade import ScalpTradeModel
from src.strategies.scalper.engine import ScalperEngine
from src.strategies.scalper.executor import ScalpExecutor, ScalpPosition
from src.strategies.scalper.tracker import ScalpTracker
from src.strategies.scalper.types import (
    Candle,
    Direction,
    Regime,
    ScalpSignal,
    StrategyContext,
)
from src.trading.symbol_reservations import symbol_reservations


# --------------------------------------------------------------------------
# Ortak fake'ler — ScalpExecutor.try_open akışının ihtiyaç duyduğu minimal
# yüzey. GERÇEK AĞ ÇAĞRISI YAPMAZ; çağrı sırası self.calls'a kaydedilir.
# --------------------------------------------------------------------------

@dataclass
class _ShadowExecCfg:
    scalper_min_stop_pct: float = 0.15
    scalper_max_stop_pct: float = 3.0
    scalper_min_rr: float = 0.0  # RR kapısı testte kapalı — sayılar sade kalsın
    scalper_risk_percentage: float = 2.0
    scalper_leverage: int = 20
    scalper_tp1_roi: float = 20.0
    scalper_tp1_fraction: float = 0.40
    scalper_tp2_roi: float = 50.0
    scalper_tp2_fraction: float = 0.30
    scalper_breakeven_buffer_pct: float = 0.05
    scalper_chandelier_atr_mult: float = 2.5
    scalper_max_margin_pct: float = 50.0
    scalper_entry_mode: str = "taker"
    scalper_loss_cooldown_minutes: int = 60
    scalper_protection_failure_cooldown_minutes: int = 60
    scalper_shadow_mode: bool = False


class _FakeClient:
    """ImprovedBinanceClient'ın try_open akışının ihtiyaç duyduğu metodlarını
    taklit eden sahte istemci — GERÇEK AĞ ÇAĞRISI YAPMAZ."""

    def __init__(self, balance: float = 10_000.0):
        self.balance = balance
        self.calls: List[str] = []

    async def get_account_balance(self):
        self.calls.append("get_account_balance")
        return self.balance

    async def get_all_positions(self):
        self.calls.append("get_all_positions")
        return []

    async def quantize_quantity(self, symbol, quantity):
        self.calls.append("quantize_quantity")
        return quantity

    async def validate_order(self, symbol, quantity, price):
        self.calls.append("validate_order")

    async def set_margin_type(self, symbol, margin_type="ISOLATED"):
        self.calls.append("set_margin_type")

    async def set_leverage(self, symbol, leverage):
        self.calls.append("set_leverage")

    async def open_market_order(self, symbol, side, quantity):
        self.calls.append("open_market_order")
        return {"orderId": 111}

    async def place_take_profit(self, symbol, side, stop_price, quantity):
        self.calls.append("place_take_profit")
        return {"orderId": 222}


class _FakePm:
    """PositionManager'ın try_open akışının ihtiyaç duyduğu metodlarını
    taklit eder — GERÇEK AĞ ÇAĞRISI YAPMAZ."""

    def __init__(self, entry_price: float = 100.0, filled_qty: float = 400.0):
        self.entry_price = entry_price
        self.filled_qty = filled_qty
        self.calls: List[str] = []

    async def resolve_fill(self, symbol, entry_order):
        self.calls.append("resolve_fill")
        return self.entry_price, self.filled_qty

    async def place_stop_loss_or_close(
        self, symbol, sl_side, stop_price, *,
        reference_price=None, max_distance_pct=None,
    ):
        self.calls.append("place_stop_loss_or_close")
        return {"orderId": 333}

    async def emergency_close(self, symbol):
        self.calls.append("emergency_close")
        return True


@dataclass
class _FakeTracker:
    calls: List[str] = field(default_factory=list)
    shadow_kwargs: List[Dict[str, Any]] = field(default_factory=list)
    open_kwargs: List[Dict[str, Any]] = field(default_factory=list)

    async def record_shadow(self, **kwargs):
        self.calls.append("record_shadow")
        self.shadow_kwargs.append(kwargs)
        return 1

    async def record_open(self, **kwargs):
        self.calls.append("record_open")
        self.open_kwargs.append(kwargs)
        return 1


def _mk_signal(entry_price: float = 100.0, stop_price: float = 99.5) -> ScalpSignal:
    return ScalpSignal(
        strategy="C", symbol="TESTUSDT", direction=Direction.LONG,
        entry_price=entry_price, stop_price=stop_price, reason="shadow-test",
        regime=Regime.UP, atr_5m=1.0, risk_multiplier=1.0,
    )


def _mk_ctx() -> StrategyContext:
    return StrategyContext(
        symbol="TESTUSDT", regime=Regime.UP, candles_4h=[], candles_15m=[],
        candles_5m=[], current_price=100.0, atr_5m=1.0, leverage=20,
    )


# Sinyal fiyat=100, stop=99.5 (%0.5 mesafe), bakiye 10000, risk %2, kaldıraç 20:
# risk_amount = 200, price_distance = 0.5 -> qty = 400; nominal = 40000 <
# nominal_cap (10000*20*0.5=100000) -> kırpılmaz; margin = 400*100/20 = 2000.
_EXPECTED_QTY = 400.0
_EXPECTED_MARGIN = 2000.0


# --------------------------------------------------------------------------
# 1) ScalpExecutor.try_open — gölge AÇIK
# --------------------------------------------------------------------------

class TestShadowModeExecutorOpen:
    async def test_no_order_calls_and_shadow_recorded(self):
        client = _FakeClient(balance=10_000.0)
        pm = _FakePm()
        tracker = _FakeTracker()
        cfg = _ShadowExecCfg(scalper_shadow_mode=True)
        executor = ScalpExecutor(client=client, pm=pm, tracker=tracker, cfg=cfg)

        result = await executor.try_open(_mk_signal(), _mk_ctx())

        assert result is None
        # Bakiye/boyutlama/borsa-filtresi gates HALA çalıştı (gerçek leverage/
        # margin hesaplansın diye) ama emir/margin/leverage isteği GİTMEDİ.
        assert client.calls == ["get_account_balance", "quantize_quantity", "validate_order"]
        assert "set_margin_type" not in client.calls
        assert "set_leverage" not in client.calls
        assert "open_market_order" not in client.calls
        assert "place_take_profit" not in client.calls
        # PositionManager'a HİÇ dokunulmadı: SL/TP yok, dolum çözümü yok.
        assert pm.calls == []

        assert tracker.calls == ["record_shadow"]
        assert tracker.open_kwargs == []  # record_open ASLA çağrılmadı
        kwargs = tracker.shadow_kwargs[0]
        assert kwargs["signal"].symbol == "TESTUSDT"
        assert kwargs["signal"].direction == Direction.LONG
        assert kwargs["entry_price"] == pytest.approx(100.0)  # sinyal fiyatı
        assert kwargs["quantity"] == pytest.approx(_EXPECTED_QTY)
        assert kwargs["leverage"] == 20
        assert kwargs["margin_usdt"] == pytest.approx(_EXPECTED_MARGIN)

    async def test_no_position_tracking_pending_stays_empty(self):
        client = _FakeClient(balance=10_000.0)
        pm = _FakePm()
        tracker = _FakeTracker()
        cfg = _ShadowExecCfg(scalper_shadow_mode=True)
        executor = ScalpExecutor(client=client, pm=pm, tracker=tracker, cfg=cfg)

        result = await executor.try_open(_mk_signal(), _mk_ctx())

        assert result is None
        assert executor.pending_symbols() == set()

    async def test_capacity_unaffected_across_repeated_shadow_signals(self):
        """Kapasite sayımı engine'de tracked|pending kümesine dayanır; try_open
        None döndüğünde ScalpPosition hiç kurulmadığı ve pending'e hiç
        eklenmediği için art arda gölge sinyaller kapasiteyi TÜKETMEZ."""
        cfg = _ShadowExecCfg(scalper_shadow_mode=True)
        for _ in range(3):
            client = _FakeClient(balance=10_000.0)
            pm = _FakePm()
            tracker = _FakeTracker()
            executor = ScalpExecutor(client=client, pm=pm, tracker=tracker, cfg=cfg)
            result = await executor.try_open(_mk_signal(), _mk_ctx())
            assert result is None
            assert executor.pending_symbols() == set()

    async def test_cooldown_not_started_by_shadow_entry(self):
        client = _FakeClient(balance=10_000.0)
        pm = _FakePm()
        tracker = _FakeTracker()
        cfg = _ShadowExecCfg(scalper_shadow_mode=True)
        executor = ScalpExecutor(client=client, pm=pm, tracker=tracker, cfg=cfg)

        await executor.try_open(_mk_signal(), _mk_ctx())

        # Hiçbir risk alınmadığı için cooldown durumu boş kalmalı.
        assert executor._cooldowns == {}
        assert executor.is_entry_blocked("TESTUSDT") is False
        assert executor.cooldown_snapshot() == []

    async def test_existing_cooldown_still_blocks_shadow_signal(self):
        """Gölge modu, cooldown KAPISINI atlamaz — yalnız kendisi yeni cooldown
        BAŞLATMAZ. try_open'ın en üstündeki is_entry_blocked kontrolü aynen
        çalışmaya devam eder ("tüm kapılar bugünkü gibi" şartı)."""
        client = _FakeClient(balance=10_000.0)
        pm = _FakePm()
        tracker = _FakeTracker()
        cfg = _ShadowExecCfg(scalper_shadow_mode=True)
        executor = ScalpExecutor(client=client, pm=pm, tracker=tracker, cfg=cfg)
        executor.start_loss_cooldown("TESTUSDT")

        result = await executor.try_open(_mk_signal(), _mk_ctx())

        assert result is None
        assert client.calls == []  # cooldown kapısı balance sorgusundan ÖNCE reddetti
        assert tracker.calls == []  # gölge kaydı bile yazılmadı


# --------------------------------------------------------------------------
# 1b) ScalpExecutor.try_open — gölge tekilleştirme penceresi (D14 adversarial
#     review, bulgu A, HIGH): occupancy bırakmayan gölge dalı düzeltilmezse
#     aynı sinyal her tarama turunda yeniden yazılır (2-5x şişme).
# --------------------------------------------------------------------------

class TestShadowModeDeduplication:
    async def test_repeated_identical_signal_writes_one_shadow_row(self):
        """AYNI executor'da art arda iki try_open çağrısı — pencere içindeyken
        yalnız BİR record_shadow yazılmalı (review'ün 5 ardışık çağrıda 5
        satır bulduğu regresyon)."""
        client = _FakeClient(balance=10_000.0)
        pm = _FakePm()
        tracker = _FakeTracker()
        cfg = _ShadowExecCfg(scalper_shadow_mode=True)
        executor = ScalpExecutor(client=client, pm=pm, tracker=tracker, cfg=cfg)

        result1 = await executor.try_open(_mk_signal(), _mk_ctx())
        result2 = await executor.try_open(_mk_signal(), _mk_ctx())
        result3 = await executor.try_open(_mk_signal(), _mk_ctx())

        assert result1 is None and result2 is None and result3 is None
        assert tracker.calls == ["record_shadow"]  # yalnız BİR kayıt, 3 çağrı değil
        assert len(tracker.shadow_kwargs) == 1
        assert executor.shadow_active_count() == 1

    async def test_different_symbols_each_get_their_own_row(self):
        """Tekilleştirme SEMBOL bazlıdır — farklı semboller birbirini engellemez."""
        client = _FakeClient(balance=10_000.0)
        pm = _FakePm()
        tracker = _FakeTracker()
        cfg = _ShadowExecCfg(scalper_shadow_mode=True)
        executor = ScalpExecutor(client=client, pm=pm, tracker=tracker, cfg=cfg)

        sig_a = _mk_signal()
        sig_b = ScalpSignal(
            strategy="C", symbol="OTHERUSDT", direction=Direction.LONG,
            entry_price=100.0, stop_price=99.5, reason="shadow-test",
            regime=Regime.UP, atr_5m=1.0, risk_multiplier=1.0,
        )
        await executor.try_open(sig_a, _mk_ctx())
        await executor.try_open(sig_b, _mk_ctx())

        assert len(tracker.shadow_kwargs) == 2
        assert executor.shadow_active_count() == 2

    async def test_second_row_allowed_after_dedup_window_expires(self):
        """Pencere geçtikten sonra AYNI sembol yeniden kaydedilebilir — sonsuza
        dek susturulmuyor, yalnız pencere içi tekrar engelleniyor."""
        client = _FakeClient(balance=10_000.0)
        pm = _FakePm()
        tracker = _FakeTracker()
        cfg = _ShadowExecCfg(scalper_shadow_mode=True)
        executor = ScalpExecutor(client=client, pm=pm, tracker=tracker, cfg=cfg)

        await executor.try_open(_mk_signal(), _mk_ctx())
        assert len(tracker.shadow_kwargs) == 1

        # Gerçek sleep yerine pencereyi geçmiş gibi göster — deterministik.
        hold = executor._shadow_dedup_seconds()
        executor._shadow_recent["TESTUSDT"] = time.time() - hold - 1.0

        await executor.try_open(_mk_signal(), _mk_ctx())

        assert tracker.calls == ["record_shadow", "record_shadow"]
        assert len(tracker.shadow_kwargs) == 2
        # Eski kayıt _prune_cooldowns ile budandı; yalnız TAZE kayıt kalmalı.
        assert executor.shadow_active_count() == 1

    def test_dedup_seconds_defaults_to_loss_cooldown_minutes(self):
        cfg = _ShadowExecCfg(scalper_shadow_mode=True, scalper_loss_cooldown_minutes=45)
        executor = ScalpExecutor(
            client=_FakeClient(), pm=_FakePm(), tracker=_FakeTracker(), cfg=cfg,
        )
        assert executor._shadow_dedup_seconds() == pytest.approx(45 * 60.0)

    def test_dedup_seconds_uses_explicit_scalper_shadow_dedup_minutes(self):
        cfg = _ShadowExecCfg(scalper_shadow_mode=True, scalper_loss_cooldown_minutes=45)
        cfg.scalper_shadow_dedup_minutes = 5  # ayrı alan — loss_cooldown'ı EZER
        executor = ScalpExecutor(
            client=_FakeClient(), pm=_FakePm(), tracker=_FakeTracker(), cfg=cfg,
        )
        assert executor._shadow_dedup_seconds() == pytest.approx(5 * 60.0)

    def test_shadow_active_count_zero_when_nothing_recorded(self):
        cfg = _ShadowExecCfg(scalper_shadow_mode=True)
        executor = ScalpExecutor(
            client=_FakeClient(), pm=_FakePm(), tracker=_FakeTracker(), cfg=cfg,
        )
        assert executor.shadow_active_count() == 0


# --------------------------------------------------------------------------
# 2) ScalpExecutor.try_open — gölge KAPALI (bugünkü yol DEĞİŞMEDİ)
# --------------------------------------------------------------------------

class TestShadowModeDisabledPathUnchanged:
    async def test_shadow_disabled_opens_real_position_as_before(self):
        client = _FakeClient(balance=10_000.0)
        pm = _FakePm(entry_price=100.0, filled_qty=_EXPECTED_QTY)
        tracker = _FakeTracker()
        cfg = _ShadowExecCfg(scalper_shadow_mode=False)
        executor = ScalpExecutor(client=client, pm=pm, tracker=tracker, cfg=cfg)

        result = await executor.try_open(_mk_signal(), _mk_ctx())

        assert result is not None
        assert isinstance(result, ScalpPosition)
        assert "set_margin_type" in client.calls
        assert "set_leverage" in client.calls
        assert "open_market_order" in client.calls
        assert "resolve_fill" in pm.calls
        assert "place_stop_loss_or_close" in pm.calls
        assert tracker.calls == ["record_open"]  # record_shadow ASLA çağrılmadı

    async def test_shadow_field_missing_from_cfg_defaults_to_disabled(self):
        """Eski/minimal test cfg'lerinde scalper_shadow_mode alanı YOKSA
        getattr fallback'i False'a düşmeli — geriye uyumluluk."""
        client = _FakeClient(balance=10_000.0)
        pm = _FakePm(entry_price=100.0, filled_qty=_EXPECTED_QTY)
        tracker = _FakeTracker()
        cfg = SimpleNamespace(
            scalper_min_stop_pct=0.15, scalper_max_stop_pct=3.0, scalper_min_rr=0.0,
            scalper_risk_percentage=2.0, scalper_leverage=20, scalper_tp1_roi=20.0,
            scalper_tp1_fraction=0.40, scalper_tp2_roi=50.0, scalper_tp2_fraction=0.30,
            scalper_breakeven_buffer_pct=0.05, scalper_chandelier_atr_mult=2.5,
        )  # scalper_shadow_mode BİLEREK yok
        executor = ScalpExecutor(client=client, pm=pm, tracker=tracker, cfg=cfg)

        result = await executor.try_open(_mk_signal(), _mk_ctx())

        assert result is not None
        assert tracker.calls == ["record_open"]


# --------------------------------------------------------------------------
# 3) ScalpTracker.record_shadow — gerçek (geçici) SQLite üzerinde
# --------------------------------------------------------------------------

@pytest.fixture
async def real_tracker(tmp_path, monkeypatch):
    """stats()/open_trades()'in SHADOW satırlarını SQL WHERE seviyesinde
    dışladığını kanıtlamak için mock session yerine gerçek (geçici dosya)
    SQLite kullanılır — src/core/database.py'nin AsyncSessionLocal'ı
    monkeypatch'lenir, üretim DB'sine dokunulmaz."""
    db_path = tmp_path / "shadow_mode_test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    monkeypatch.setattr(tracker_module, "AsyncSessionLocal", session_maker)

    tracker = ScalpTracker()
    try:
        yield tracker
    finally:
        await engine.dispose()


class TestShadowModeTrackerPersistence:
    async def test_shadow_row_written_with_expected_fields(self, real_tracker):
        signal = _mk_signal(entry_price=100.0, stop_price=99.5)

        shadow_id = await real_tracker.record_shadow(
            signal=signal, entry_price=100.0, quantity=_EXPECTED_QTY,
            leverage=20, margin_usdt=_EXPECTED_MARGIN,
        )

        async with tracker_module.AsyncSessionLocal() as session:
            row = await session.get(ScalpTradeModel, shadow_id)

        assert row is not None
        assert row.status == "SHADOW"
        assert row.notes == "shadow_mode"
        assert row.strategy == "C"
        assert row.symbol == "TESTUSDT"
        assert row.direction == "LONG"
        assert row.entry_price == pytest.approx(100.0)
        assert row.quantity == pytest.approx(_EXPECTED_QTY)
        assert row.leverage == 20
        assert row.margin_usdt == pytest.approx(_EXPECTED_MARGIN)
        # Çıkış alanları hep boş — hiçbir zaman kapanmadı, açılmadı bile.
        assert row.exit_price is None
        assert row.exit_reason is None
        assert row.closed_at is None

    async def test_stats_and_open_trades_exclude_shadow_rows(self, real_tracker):
        signal = _mk_signal()

        real_id = await real_tracker.record_open(
            signal=signal, entry_price=100.0, quantity=1.0, leverage=10,
            margin_usdt=10.0, sl_algo_id="1", tp1_algo_id="2", tp2_algo_id="3",
        )
        await real_tracker.record_close(
            real_id, exit_price=101.0, realized_pnl=1.0, exit_reason="TP_LADDER",
            pnl_source="binance_income_net",
        )
        await real_tracker.record_shadow(
            signal=signal, entry_price=100.0, quantity=_EXPECTED_QTY,
            leverage=20, margin_usdt=_EXPECTED_MARGIN,
        )

        stats = await real_tracker.stats()
        assert stats["C"]["trades"] == 1
        assert stats["C"]["total_pnl"] == pytest.approx(1.0)

        assert await real_tracker.open_trades() == []  # SHADOW "OPEN" değildir

    async def test_shadow_row_not_recovered_as_open_position(self, real_tracker):
        """Restart kurtarma yolu (orchestrator/engine) yalnız status="OPEN"
        sorgular — gölge satırı asla 'yetim pozisyon' sanılmaz."""
        signal = _mk_signal()
        await real_tracker.record_shadow(
            signal=signal, entry_price=100.0, quantity=_EXPECTED_QTY,
            leverage=20, margin_usdt=_EXPECTED_MARGIN,
        )
        assert await real_tracker.open_trades() == []


# --------------------------------------------------------------------------
# 4) Settings._validate_binance_environment — mainnet gölge bypass'ı
# --------------------------------------------------------------------------

class TestShadowModeMainnetValidation:
    @staticmethod
    def _settings(**overrides) -> Settings:
        values = dict(
            # Zorunlu alanlar — validator testinde içerikleri önemsiz.
            binance_api_key="x", binance_api_secret="x",
            telegram_bot_token="x", telegram_chat_id="x",
            openai_api_key="x", gemini_api_key="x", deepseek_api_key="x",
            jwt_secret="x",
            binance_base_url="https://fapi.binance.com",  # mainnet
            allow_mainnet=True,
            app_env="production",  # ekstra "dev ortamında mainnet" uyarısını susturur
            scalper_entry_halt_enabled=True,
        )
        values.update(overrides)
        return Settings(_env_file=None, **values)

    def test_testnet_unaffected_even_without_secrets(self):
        s = self._settings(
            binance_base_url="https://testnet.binancefuture.com",
            allow_mainnet=False,
            scalper_shadow_mode=False,
            risk_event_secret="", tv_webhook_secret="", scalper_symbol_allowlist="",
        )
        assert s.is_testnet is True
        assert s.scalper_shadow_mode is False

    def test_mainnet_without_shadow_and_without_secrets_rejected(self):
        with pytest.raises(ValueError, match="RISK_EVENT_SECRET"):
            self._settings(
                scalper_shadow_mode=False,
                risk_event_secret="", tv_webhook_secret="", scalper_symbol_allowlist="",
            )

    def test_mainnet_without_shadow_lists_only_missing_secrets(self):
        with pytest.raises(ValueError) as exc_info:
            self._settings(
                scalper_shadow_mode=False,
                risk_event_secret="already-set",
                tv_webhook_secret="", scalper_symbol_allowlist="",
            )
        message = str(exc_info.value)
        # Eksik alanlar "boş bırakılamaz: <liste>." segmentinde sayılır; bu
        # segmenti izole ederek yalnız EKSİK olanların listelendiğini kanıtla
        # (RISK_EVENT_SECRET zaten dolu — mesajın başka yerinde AÇIKLAMA
        # amaçlı geçmesi sorun değil, ama eksik LİSTESİNDE olmamalı).
        missing_list_segment = message.split("boş bırakılamaz:", 1)[1].split(".", 1)[0]
        assert "TV_WEBHOOK_SECRET" in missing_list_segment
        assert "SCALPER_SYMBOL_ALLOWLIST" in missing_list_segment
        assert "RISK_EVENT_SECRET" not in missing_list_segment

    def test_mainnet_without_shadow_with_all_secrets_ok(self):
        s = self._settings(
            scalper_shadow_mode=False,
            risk_event_secret="risk-secret", tv_webhook_secret="tv-secret",
            scalper_symbol_allowlist="BTCUSDT,ETHUSDT",
        )
        assert s.scalper_shadow_mode is False
        assert s.risk_event_secret == "risk-secret"

    def test_mainnet_without_shadow_whitespace_only_secrets_rejected(self):
        """D14 review, bulgu C (HIGH): bare truthiness tırnaklı boşluğu
        ("   ") DOLU sayıp korumaları sessizce devre dışı bırakıyordu —
        tüketiciler (main.py, engine.py) zaten .strip() uyguluyor, validator
        uygulamıyordu. Üçü de artık boş sayılmalı."""
        with pytest.raises(ValueError, match="RISK_EVENT_SECRET"):
            self._settings(
                scalper_shadow_mode=False,
                risk_event_secret="   ", tv_webhook_secret="\t",
                scalper_symbol_allowlist="  ",
            )

    def test_mainnet_without_shadow_comma_only_allowlist_rejected(self):
        """SCALPER_SYMBOL_ALLOWLIST=',' engine.py'de boş evrene ayrışır
        (str.split(',') sonrası hepsi boş hücre) — mainnet korumasında da
        boş sayılmalı, aksi halde bot sessizce hiç taramaz."""
        with pytest.raises(ValueError, match="SCALPER_SYMBOL_ALLOWLIST"):
            self._settings(
                scalper_shadow_mode=False,
                risk_event_secret="risk-secret", tv_webhook_secret="tv-secret",
                scalper_symbol_allowlist=",,",
            )

    def test_mainnet_without_shadow_secrets_with_surrounding_whitespace_ok(self):
        """Gerçek bir değerin ETRAFINDA boşluk olması (kopyala-yapıştır kazası)
        reddedilmemeli — yalnız TAMAMEN boş/virgül olan değer reddedilir."""
        s = self._settings(
            scalper_shadow_mode=False,
            risk_event_secret="  risk-secret  ", tv_webhook_secret="tv-secret",
            scalper_symbol_allowlist=" BTCUSDT , ETHUSDT ",
        )
        assert s.scalper_shadow_mode is False

    def test_mainnet_shadow_mode_bypasses_secret_requirement(self):
        s = self._settings(
            scalper_shadow_mode=True,
            risk_event_secret="", tv_webhook_secret="", scalper_symbol_allowlist="",
        )
        assert s.scalper_shadow_mode is True

    def test_mainnet_entry_halt_check_still_enforced_regardless_of_shadow(self):
        """Gölge modu YALNIZ risk/webhook/allowlist zorunluluğunu bypass eder;
        SCALPER_ENTRY_HALT_ENABLED=false mainnet'te HALA yasaktır."""
        with pytest.raises(ValueError, match="ENTRY_HALT_ENABLED"):
            self._settings(
                scalper_shadow_mode=True,
                scalper_entry_halt_enabled=False,
            )


# --------------------------------------------------------------------------
# 5) ScalperEngine._maybe_log_shadow_mode_banner — başlangıç uyarısı
# --------------------------------------------------------------------------

class TestShadowModeStartupBanner:
    @staticmethod
    def _engine_with_cfg(cfg: Any) -> ScalperEngine:
        engine = ScalperEngine.__new__(ScalperEngine)  # __init__ atlanır (ağ yok)
        engine.cfg = cfg
        return engine

    def test_banner_logged_when_shadow_enabled(self):
        engine = self._engine_with_cfg(SimpleNamespace(scalper_shadow_mode=True))
        warnings: List[str] = []
        engine.logger = SimpleNamespace(warning=lambda msg, *a, **kw: warnings.append(msg))

        engine._maybe_log_shadow_mode_banner()

        assert warnings == ["⚠️ GÖLGE MODU AÇIK — emir gönderilmez"]

    def test_no_banner_when_shadow_disabled(self):
        engine = self._engine_with_cfg(SimpleNamespace(scalper_shadow_mode=False))
        warnings: List[str] = []
        engine.logger = SimpleNamespace(warning=lambda msg, *a, **kw: warnings.append(msg))

        engine._maybe_log_shadow_mode_banner()

        assert warnings == []

    def test_missing_config_field_defaults_to_no_banner(self):
        engine = self._engine_with_cfg(SimpleNamespace())  # alan YOK
        warnings: List[str] = []
        engine.logger = SimpleNamespace(warning=lambda msg, *a, **kw: warnings.append(msg))

        engine._maybe_log_shadow_mode_banner()

        assert warnings == []


# --------------------------------------------------------------------------
# 6) ScalperEngine._evaluate_symbol — gölge kapasite kapısı (D14 adversarial
#    review, bulgu B): gölge girişler tracked/pending'e hiç girmediği için bu
#    kapı canlıda hiç devreye girmiyordu. Fix: executor.shadow_active_count()
#    (tekilleştirme penceresindeki sembol sayısı) engine'in kapasite
#    kapısında open+shadow olarak SCALPER_MAX_POSITIONS'a karşı sayılır.
# --------------------------------------------------------------------------

@dataclass
class _CapacityEngineCfg(_ShadowExecCfg):
    scalper_max_positions: int = 2


class _FakeExitsNoPositions:
    """ExitManager'ın _evaluate_symbol'ün ihtiyaç duyduğu tek yüzeyi: hiçbir
    zaman gerçek pozisyon izlemez — kapasite kapısının GÖLGE tarafını test
    etmek için tracked/pending kasıtlı olarak hep boş."""

    def tracked_symbols(self):
        return set()

    def track(self, sp):
        """Gölge KAPALI kontrol testinde (record_open yolu) çağrılır — bu
        testin amacı kapasite kapısı olduğu için izleme durumu kasıtlı
        değişmeden kalır (tracked_symbols() hep boş)."""


class _FixedCandleFetcher:
    """KlineFetcher'ın get_klines'ını taklit eder — her sembol/zaman dilimi
    için AYNI sabit mum listesini döner (rejim UNKNOWN kalır, 200'den az 4h
    mumu olduğu için regime kapısı hiç devreye girmez — bu testin amacı
    sinyal/rejim mantığı değil, kapasite kapısıdır)."""

    def __init__(self, candles: List[Candle]):
        self._candles = candles

    async def get_klines(self, symbol, tf, limit):
        return self._candles


class _AlwaysLongSignalStrategy:
    """Strateji C'nin gerçek RSI/BB koşullarına bağlı kalmadan HER zaman sabit
    bir LONG sinyali üretir — bu testin amacı sinyal üretimini değil,
    engine.py'deki kapasite kapısını (~1253) sınamaktır."""

    def evaluate(self, ctx: StrategyContext):
        return ScalpSignal(
            strategy="C", symbol=ctx.symbol, direction=Direction.LONG,
            entry_price=100.0, stop_price=99.5, reason="capacity-gate-test",
            regime=ctx.regime, atr_5m=1.0, risk_multiplier=1.0,
        )


def _mk_capacity_test_candles(n: int = 5) -> List[Candle]:
    interval = 5 * 60 * 1000
    return [
        Candle(
            open_time=i * interval, open=100.0, high=101.0, low=99.0,
            close=100.0, volume=10.0, close_time=i * interval + interval - 1,
        )
        for i in range(n)
    ]


class TestShadowModeCapacityGate:
    async def _mk_engine(self, tracker: "_FakeTracker", cfg: Any, client: "_FakeClient"):
        pm = _FakePm()
        executor = ScalpExecutor(client=client, pm=pm, tracker=tracker, cfg=cfg)

        engine = ScalperEngine.__new__(ScalperEngine)  # __init__ atlanır (ağ yok)
        engine.cfg = cfg
        engine.client = client
        engine.executor = executor
        engine.exits = _FakeExitsNoPositions()
        engine.fetcher = _FixedCandleFetcher(_mk_capacity_test_candles())
        engine._entry_lock = asyncio.Lock()
        engine._opening_symbols = set()
        engine._regimes = {}
        engine._regime_cache = {}
        # _entries_ready() bu alanlara bakar — hepsi "hazır" göstermeli.
        engine._exchange_ready = True
        engine._exchange_last_success_monotonic = time.monotonic()
        engine._recovery_ready = True
        engine._risk_ready = True
        engine._entry_halted = False
        engine._kill_switch = False
        engine._signals_today = 0
        return engine

    async def test_capacity_gate_counts_shadow_active_across_symbols(self):
        """max_positions=2, 3 AYRI sembolde (tekrar değil, farklı sembol) gölge
        sinyal → yalnız 2 satır. Review'ün 'canlının reddedeceği sinyaller de
        sınırsız gölge satırına dönüşüyor' bulgusunun tam regresyonu."""
        symbol_reservations.clear()
        try:
            cfg = _CapacityEngineCfg(scalper_shadow_mode=True, scalper_max_positions=2)
            tracker = _FakeTracker()
            client = _FakeClient(balance=10_000.0)
            engine = await self._mk_engine(tracker, cfg, client)

            infos: List[str] = []
            engine.logger = SimpleNamespace(
                info=lambda msg, *a, **kw: infos.append(msg),
                error=lambda *a, **kw: None,
                warning=lambda *a, **kw: None,
            )

            strategies = [_AlwaysLongSignalStrategy()]
            for symbol in ("AAAUSDT", "BBBUSDT", "CCCUSDT"):
                await engine._evaluate_symbol(symbol, strategies)

            assert len(tracker.shadow_kwargs) == 2  # 3 sembol değil, kapasite=2
            recorded = {kw["signal"].symbol for kw in tracker.shadow_kwargs}
            assert recorded == {"AAAUSDT", "BBBUSDT"}
            assert engine.executor.shadow_active_count() == 2
            assert any("GÖLGE kapasite dolu" in msg for msg in infos)
        finally:
            symbol_reservations.clear()

    async def test_capacity_gate_inactive_when_shadow_mode_off(self):
        """Kontrol: gölge KAPALIYSA bu yeni dal hiç devreye girmemeli — bugünkü
        (gerçek tracked/pending'e dayalı) kapasite davranışı DEĞİŞMEMELİ."""
        symbol_reservations.clear()
        try:
            cfg = _CapacityEngineCfg(scalper_shadow_mode=False, scalper_max_positions=2)
            tracker = _FakeTracker()
            client = _FakeClient(balance=10_000.0)
            # Gölge kapalıyken gerçek emir yolu tetiklenir; PM'in dolum
            # döndürmesi gerekir (aksi halde emir başarısız sayılır).
            pm = _FakePm(entry_price=100.0, filled_qty=400.0)
            engine = ScalperEngine.__new__(ScalperEngine)
            engine.cfg = cfg
            engine.client = client
            engine.executor = ScalpExecutor(client=client, pm=pm, tracker=tracker, cfg=cfg)
            engine.exits = _FakeExitsNoPositions()
            engine.fetcher = _FixedCandleFetcher(_mk_capacity_test_candles())
            engine._entry_lock = asyncio.Lock()
            engine._opening_symbols = set()
            engine._regimes = {}
            engine._regime_cache = {}
            engine._exchange_ready = True
            engine._exchange_last_success_monotonic = time.monotonic()
            engine._recovery_ready = True
            engine._risk_ready = True
            engine._entry_halted = False
            engine._kill_switch = False
            engine._signals_today = 0

            infos: List[str] = []
            engine.logger = SimpleNamespace(
                info=lambda msg, *a, **kw: infos.append(msg),
                error=lambda *a, **kw: None,
                warning=lambda *a, **kw: None,
            )

            # exits sahte olduğu için gerçek açılan pozisyon tracked'e hiç
            # girmez — yalnız GÖLGE dalının bu senaryoda ATLANDIĞINI (yani
            # eski 'scalper pozisyon kapasitesi dolu' mesajının hâlâ kullanıma
            # açık olduğunu) kanıtlamak yeterli; kapasite hiçbir zaman
            # DOLMAZ çünkü _FakeExitsNoPositions.tracked_symbols() hep boş.
            await engine._evaluate_symbol("AAAUSDT", [_AlwaysLongSignalStrategy()])

            assert tracker.calls == ["record_open"]  # gölge kaydı YAZILMADI
            assert not any("GÖLGE kapasite" in msg for msg in infos)
        finally:
            symbol_reservations.clear()
