"""D17 — piyasa verisi kaynağı: "veri mainnet'ten, emirler testnet'te".

Kök bulgu (docs/DECISIONS.md D17): `ScalperEngine.__init__` `KlineFetcher()`i
argümansız kuruyordu → `settings.binance_base_url`. Canlı bot TESTNET'te
olduğu için RSI/Bollinger/diverjans/rejim/ATR hesapları TESTNET mumlarından
üretiliyordu; backtest harness'i ise mainnet (`https://fapi.binance.com`)
okuyor — canlı motor ile harness AYNI sinyalleri görmüyordu (parite açığı).

Bu dosya dört sözleşmeyi kilitler:
  1) `SCALPER_MARKET_DATA_BASE_URL` boşken davranış BUGÜNKÜYLE BİREBİR aynı;
     doluyken YALNIZ public kline çekimi o host'a gider.
  2) Ayar doğrulaması fail-fast (https zorunlu, sondaki '/' yok, mainnet'te
     testnet URL'i REDDEDİLİR).
  3) Ağırlık/ban: public istekler host BAŞINA oran sınırlayıcı + ağırlık
     bütçesinden geçer; 418/429 fail-closed kesici kurar (tekrar YOK) ve
     `_scan_tick` turu keser. Kesici paylaşımı TEK YÖNLÜ: imzalı yolun banı
     public'i durdurur, public ban imzalıyı DURDURMAZ (bkz. ilgili testin
     docstring'i); ayrı host'ta tamamen izole.
  4) Teşhis: `/scalper/status` alanları + başlangıç log satırı.
"""

import asyncio
import re
import time
from collections import deque
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import httpx
import pytest

from src.core.config import Settings
from src.strategies.scalper import data as data_module
from src.strategies.scalper import engine as engine_module
from src.strategies.scalper.data import (
    KlineFetcher,
    MarketDataBanError,
    MarketDataGuard,
    host_of,
    klines_weight,
)
from src.strategies.scalper.engine import ScalperEngine
from src.strategies.scalper.types import Direction
from src.trading.binance_client_improved import ImprovedBinanceClient


MAINNET = "https://fapi.binance.com"
TESTNET = "https://testnet.binancefuture.com"


async def _async_value(value):
    return value


@pytest.fixture(autouse=True)
def _reset_guards():
    """Süreç-geneli ban/ağırlık durumu testler arasına sızmasın."""
    MarketDataGuard.reset()
    ImprovedBinanceClient._rest_blocked_until = 0.0
    ImprovedBinanceClient._breaker_last_log = 0.0
    yield
    MarketDataGuard.reset()
    ImprovedBinanceClient._rest_blocked_until = 0.0
    ImprovedBinanceClient._breaker_last_log = 0.0


# ---------------------------------------------------------------------------
# 1) Ayar + doğrulama
# ---------------------------------------------------------------------------

def _settings(**overrides) -> Settings:
    values = dict(
        binance_api_key="x", binance_api_secret="x",
        telegram_bot_token="x", telegram_chat_id="x",
        openai_api_key="x", gemini_api_key="x", deepseek_api_key="x",
        jwt_secret="x",
        binance_base_url=TESTNET,
        app_env="production",
    )
    values.update(overrides)
    return Settings(_env_file=None, **values)


class TestMarketDataSetting:
    def test_empty_falls_back_to_trading_host(self):
        s = _settings()
        assert s.scalper_market_data_base_url == ""
        assert s.market_data_base_url == TESTNET
        assert s.kline_source == "trading_host"
        assert s.market_data_is_testnet is True

    def test_separate_host_is_reported(self):
        s = _settings(scalper_market_data_base_url=MAINNET)
        assert s.market_data_base_url == MAINNET
        assert s.kline_source == "separate"
        assert s.market_data_is_testnet is False
        # İşlem host'u DEĞİŞMEZ — emir/bakiye/pozisyon hâlâ testnet.
        assert s.binance_base_url == TESTNET
        assert s.is_testnet is True

    def test_whitespace_only_is_empty(self):
        s = _settings(scalper_market_data_base_url="   ")
        assert s.scalper_market_data_base_url == ""
        assert s.kline_source == "trading_host"

    def test_explicitly_same_as_trading_host_is_not_separate(self):
        s = _settings(scalper_market_data_base_url=TESTNET)
        assert s.kline_source == "trading_host"

    def test_http_rejected(self):
        with pytest.raises(ValueError, match="https://"):
            _settings(scalper_market_data_base_url="http://fapi.binance.com")

    def test_trailing_slash_rejected(self):
        with pytest.raises(ValueError, match="sonunda"):
            _settings(scalper_market_data_base_url="https://fapi.binance.com/")

    def test_path_rejected(self):
        with pytest.raises(ValueError, match="şema\\+host"):
            _settings(scalper_market_data_base_url="https://fapi.binance.com/fapi/v1")

    def test_mainnet_trading_rejects_testnet_market_data(self):
        """Gerçek parayla işlem SAHTE (testnet) mumlara dayandırılamaz."""
        with pytest.raises(ValueError, match="TESTNET"):
            _settings(
                binance_base_url=MAINNET, allow_mainnet=True,
                scalper_shadow_mode=True,          # secret zorunluluğunu bypass eder
                scalper_market_data_base_url=TESTNET,
            )

    def test_mainnet_trading_rejects_demo_market_data(self):
        """demo-fapi.binance.com da testnet'tir (TESTNET_HOSTS)."""
        with pytest.raises(ValueError, match="TESTNET"):
            _settings(
                binance_base_url=MAINNET, allow_mainnet=True,
                scalper_shadow_mode=True,
                scalper_market_data_base_url="https://demo-fapi.binance.com",
            )

    def test_unknown_host_rejected(self):
        """Bu yol İMZASIZDIR: yanlış host hiçbir kimlik doğrulama hatası
        üretmez, bot sessizce YABANCI mumlarla karar verir ve o mumlardan
        türeyen chandelier seviyesi gerçek bir stop emrine dönüşür. Bu yüzden
        TAM host allowlist'i (alt-dize değil)."""
        for bad in (
            "https://fapi.binance.com.evil.tld",
            "https://binance-klines.attacker.example",
            "https://127.0.0.1:8443",
            "https://testnet.binancefuture.com.evil.tld",
        ):
            with pytest.raises(ValueError, match="bilinmeyen host"):
                _settings(scalper_market_data_base_url=bad)

    def test_known_binance_hosts_allowed(self):
        for good in (
            "https://fapi.binance.com",
            "https://fapi1.binance.com",
            "https://testnet.binancefuture.com",
            "https://demo-fapi.binance.com",
        ):
            assert _settings(scalper_market_data_base_url=good)

    def test_mainnet_trading_allows_empty(self):
        s = _settings(
            binance_base_url=MAINNET, allow_mainnet=True, scalper_shadow_mode=True,
        )
        assert s.market_data_base_url == MAINNET
        assert s.kline_source == "trading_host"

    def test_mainnet_trading_allows_mainnet_market_data(self):
        s = _settings(
            binance_base_url=MAINNET, allow_mainnet=True, scalper_shadow_mode=True,
            scalper_market_data_base_url=MAINNET,
        )
        assert s.market_data_is_testnet is False


# ---------------------------------------------------------------------------
# 2) Motor kablolaması — fetcher ayarı kullanıyor mu?
# ---------------------------------------------------------------------------

class _RecordingKlineFetcher:
    """KlineFetcher yerine geçer: yalnız base_url'i kaydeder (ağ/httpx yok)."""

    created: List[Optional[str]] = []

    def __init__(self, base_url: Optional[str] = None):
        type(self).created.append(base_url)
        self.base_url = base_url or "https://unset.invalid"
        self.host = host_of(self.base_url)

    async def get_klines(self, *a, **kw):  # pragma: no cover - çağrılmaz
        raise AssertionError("bu testte kline çekilmemeli")

    async def get_price(self, *a, **kw):  # pragma: no cover - çağrılmaz
        raise AssertionError("bu testte ticker fiyatı çekilmemeli")

    async def close(self) -> None:
        return None


async def _build_engine(monkeypatch, market_data_url: str) -> ScalperEngine:
    monkeypatch.setattr(
        engine_module.settings, "scalper_market_data_base_url", market_data_url,
        raising=False,
    )
    _RecordingKlineFetcher.created = []
    monkeypatch.setattr(engine_module, "KlineFetcher", _RecordingKlineFetcher)
    return ScalperEngine()


async def _close_engine(engine: ScalperEngine) -> None:
    for closer in (engine.scanner.close, engine.client.close):
        try:
            await closer()
        except Exception:
            pass


class TestEngineWiring:
    def test_real_fetcher_without_base_url_uses_trading_host(self):
        """GERÇEK `KlineFetcher` sözleşmesi: base_url=None → işlem host'u
        (sahte kayıt sınıfı değil, asıl sınıf)."""
        from src.core.config import settings as live_settings

        fetcher = KlineFetcher()
        assert fetcher.base_url == live_settings.binance_base_url
        assert fetcher.host == host_of(live_settings.binance_base_url)

    async def test_empty_setting_uses_trading_host(self, monkeypatch):
        engine = await _build_engine(monkeypatch, "")
        try:
            # base_url=None → KlineFetcher settings.binance_base_url'e düşer
            # (bugünkü davranış birebir korunur).
            assert _RecordingKlineFetcher.created == [None]
            assert engine.fetcher.base_url == "https://unset.invalid"
        finally:
            await _close_engine(engine)

    async def test_separate_setting_is_passed_to_fetcher(self, monkeypatch):
        engine = await _build_engine(monkeypatch, MAINNET)
        try:
            assert _RecordingKlineFetcher.created == [MAINNET]
            assert engine.fetcher.base_url == MAINNET
            # Emir yolu ve evren taraması İŞLEM host'unda kalmalı.
            assert engine.client.base_url == engine_module.settings.binance_base_url
            assert engine.scanner.base_url == engine_module.settings.binance_base_url
        finally:
            await _close_engine(engine)

    async def test_whitespace_setting_is_treated_as_empty(self, monkeypatch):
        engine = await _build_engine(monkeypatch, "   ")
        try:
            assert _RecordingKlineFetcher.created == [None]
        finally:
            await _close_engine(engine)

    async def test_exit_manager_shares_the_same_fetcher(self, monkeypatch):
        """Trailing (chandelier) mumları da AYNI kaynaktan gelmeli — aksi
        halde giriş ve çıkış farklı piyasa verisiyle karar verirdi."""
        engine = await _build_engine(monkeypatch, MAINNET)
        try:
            assert engine.exits.kline_fetch.__self__ is engine.fetcher
        finally:
            await _close_engine(engine)


class TestHarnessParity:
    """P1 (harness = canlı motor) veri tarafı: harness'ın okuduğu host ile
    canlı motorun okuyabileceği host AYNI olabilmeli.

    Harness'a yeni bir bayrak EKLENMEDİ — `run_backtest` zaten mainnet
    varsayılanıyla çalışır; bu test o varsayılanın canlı ayarla aynı değere
    getirilebildiğini (ve bugün olmadığını) kilitler.
    """

    def test_backtest_default_matches_configurable_live_source(self):
        import inspect

        from src.strategies.scalper import backtest as backtest_module

        harness_url = inspect.signature(
            backtest_module.run_backtest
        ).parameters["base_url"].default
        assert harness_url == MAINNET

        live = _settings(scalper_market_data_base_url=harness_url)
        assert live.market_data_base_url == harness_url
        assert live.kline_source == "separate"

        # Ayar boşken (bugünkü canlı durum) harness ile canlı AYRIŞIR —
        # D17'nin çözmek için var olduğu parite açığı budur.
        today = _settings()
        assert today.market_data_base_url != harness_url

    async def test_same_url_yields_same_fetcher_host(self, monkeypatch):
        """Harness'ın kurduğu fetcher ile canlı motorun kurduğu fetcher aynı
        host'a bakar (aynı URL verildiğinde) — mumlar birebir aynı kaynaktan."""
        import inspect

        from src.strategies.scalper import backtest as backtest_module

        harness_url = inspect.signature(
            backtest_module.run_backtest
        ).parameters["base_url"].default
        harness_fetcher = KlineFetcher(base_url=harness_url)
        try:
            engine = await _build_engine(monkeypatch, harness_url)
            try:
                assert host_of(engine.fetcher.base_url) == harness_fetcher.host
            finally:
                await _close_engine(engine)
        finally:
            await harness_fetcher.close()


# ---------------------------------------------------------------------------
# 3) Teşhis: status alanları + başlangıç logu
# ---------------------------------------------------------------------------

def _bare_engine(market_data_url: str, trading_url: str = TESTNET) -> ScalperEngine:
    engine = ScalperEngine.__new__(ScalperEngine)  # __init__ atlanır (ağ yok)
    engine.fetcher = SimpleNamespace(base_url=market_data_url)
    engine.client = SimpleNamespace(base_url=trading_url)
    return engine


class TestDiagnostics:
    def test_snapshot_reports_trading_host(self):
        info = _bare_engine(TESTNET)._kline_source_snapshot()
        assert info["market_data_base_url"] == TESTNET
        assert info["trading_base_url"] == TESTNET
        assert info["kline_source"] == "trading_host"

    def test_snapshot_exposes_guard_state(self):
        """Ban/ağırlık durumu status'ta GÖRÜNMELİ: veri host'u banlıyken tarama
        turu 'başarılı' sayıldığı için sağlık YEŞİL kalır; operatörün tek izi
        log satırı olmamalı (düşmanca inceleme bulgusu)."""
        info = _bare_engine(MAINNET)._kline_source_snapshot()
        guard = info["market_data_guard"]
        assert guard["host"] == "fapi.binance.com"
        assert guard["banned"] is False
        assert guard["weight_budget_per_minute"] > 0
        # health_snapshot BİLİNÇLİ olarak değiştirilmedi (ban sırasında
        # "unhealthy" watchdog restart'ını davet ederdi).
        assert "healthy" not in guard

    def test_snapshot_reports_separate_host(self):
        info = _bare_engine(MAINNET)._kline_source_snapshot()
        assert info["market_data_base_url"] == MAINNET
        assert info["trading_base_url"] == TESTNET
        assert info["kline_source"] == "separate"

    def test_startup_log_single_line_with_host(self):
        engine = _bare_engine(MAINNET)
        lines: List[str] = []
        engine.logger = SimpleNamespace(info=lambda msg, *a, **kw: lines.append(msg))
        engine._log_kline_source()
        assert len(lines) == 1
        assert lines[0].startswith("📡 Kline kaynağı: fapi.binance.com")
        assert "testnet.binancefuture.com" in lines[0]  # emirler nereye gidiyor
        # Secret sızıntısı yok: gerçek anahtar/secret değerleri satırda geçmez.
        from src.core.config import settings as live_settings

        assert live_settings.binance_api_key not in lines[0]
        assert live_settings.binance_api_secret not in lines[0]

    def test_startup_log_trading_host_variant(self):
        engine = _bare_engine(TESTNET)
        lines: List[str] = []
        engine.logger = SimpleNamespace(info=lambda msg, *a, **kw: lines.append(msg))
        engine._log_kline_source()
        assert lines == ["📡 Kline kaynağı: testnet.binancefuture.com (işlem host'u)"]

    def test_empty_status_payload_exposes_fields(self):
        from src.main import _EMPTY_SCALPER_STATUS

        assert "market_data_base_url" in _EMPTY_SCALPER_STATUS
        assert "kline_source" in _EMPTY_SCALPER_STATUS
        assert _EMPTY_SCALPER_STATUS["kline_source"] in ("trading_host", "separate")

    def test_snapshot_fields_land_in_engine_snapshot(self, monkeypatch):
        """`snapshot()` gerçekten bu alanları yayıyor mu (sadece yardımcı
        fonksiyon değil, /scalper/status gövdesi)."""
        engine = _bare_engine(MAINNET)
        engine.cfg = SimpleNamespace(
            scalper_enabled=True, scalper_shadow_mode=False,
            scalper_scan_interval_seconds=30, scalper_safety_interval_seconds=2.0,
            scalper_daily_loss_limit_pct=10.0, scalper_stop_mode="fixed_roi",
            scalper_virtual_capital_usdt=0.0, scalper_virtual_capital_start_trade_id=0,
        )
        engine.running = True
        engine.exits = SimpleNamespace(_positions={})
        engine.executor = SimpleNamespace(
            pending_snapshot=lambda: [], cooldown_snapshot=lambda: [],
            sizing_snapshot=lambda: {}, reject_snapshot=lambda: [],
            last_sizing_equity=None,
        )
        engine._universe = []
        engine._regimes = {}
        engine._daily_pnl = 0.0
        engine._daily_pnl_source = "unavailable"
        engine._risk_ready = True
        engine._risk_equity_usdt = None
        engine._risk_equity_source = "unavailable"
        engine._daily_loss_threshold_usdt = None
        engine._kill_switch = False
        engine._entry_halted = False
        engine._entry_halt_reason = None
        engine._entry_halted_at = None
        engine._signals_today = 0
        engine._last_scan_at = None
        monkeypatch.setattr(
            ScalperEngine, "health_snapshot", lambda self: {"healthy": True}
        )
        monkeypatch.setattr(
            ScalperEngine, "risk_event_status", lambda self: {"active": False}
        )

        snap = engine.snapshot()
        assert snap["market_data_base_url"] == MAINNET
        assert snap["trading_base_url"] == TESTNET
        assert snap["kline_source"] == "separate"


def _snapshot_ready_engine(monkeypatch, market_data_url: str = MAINNET) -> ScalperEngine:
    """`snapshot()` çağrılabilir en küçük motor (ağ yok, __init__ atlanır)."""
    engine = _bare_engine(market_data_url)
    engine.cfg = SimpleNamespace(
        scalper_enabled=True, scalper_shadow_mode=False,
        scalper_scan_interval_seconds=30, scalper_safety_interval_seconds=2.0,
        scalper_daily_loss_limit_pct=10.0, scalper_stop_mode="fixed_roi",
        scalper_virtual_capital_usdt=0.0, scalper_virtual_capital_start_trade_id=0,
    )
    engine.running = True
    engine.exits = SimpleNamespace(_positions={})
    engine.executor = SimpleNamespace(
        pending_snapshot=lambda: [], cooldown_snapshot=lambda: [],
        sizing_snapshot=lambda: {}, reject_snapshot=lambda: {},
        last_sizing_equity=None,
    )
    engine._universe = []
    engine._regimes = {}
    engine._daily_pnl = 0.0
    engine._daily_pnl_source = "unavailable"
    engine._risk_ready = True
    engine._risk_equity_usdt = None
    engine._risk_equity_source = "unavailable"
    engine._daily_loss_threshold_usdt = None
    engine._kill_switch = False
    engine._entry_halted = False
    engine._entry_halt_reason = None
    engine._entry_halted_at = None
    engine._signals_today = 0
    engine._last_scan_at = None
    monkeypatch.setattr(
        ScalperEngine, "health_snapshot", lambda self: {"healthy": True}
    )
    monkeypatch.setattr(
        ScalperEngine, "risk_event_status", lambda self: {"active": False}
    )
    monkeypatch.setattr(
        ScalperEngine, "_tv_events_snapshot", lambda self: {}
    )
    return engine


class TestStatusPayloadShape:
    """Bütünleşme incelemesi (2026-08-23) — `/scalper/status` TEK bir ŞEKLE sahip.

    `_EMPTY_SCALPER_STATUS` (motor kurulmadan) ile `engine.snapshot()` farklı
    anahtar kümeleri döndürüyordu: `market_data_guard`, `risk_event`,
    `tv_events`, `entry_rejects`, `stop_mode`, `symbol_reservations` yalnız
    motorlu payload'da vardı. Panelde "alan yok" sessizce "kanal yok" gibi
    okunur — özellikle `market_data_guard` (ban durumu) için tehlikeli.
    """

    def test_key_sets_are_identical(self, monkeypatch):
        from src.main import _EMPTY_SCALPER_STATUS

        engine = _snapshot_ready_engine(monkeypatch)
        assert set(_EMPTY_SCALPER_STATUS) == set(engine.snapshot())

    async def test_endpoint_refreshes_the_dynamic_fields(self, monkeypatch):
        """Motor yokken de ban durumu/olay defteri GERÇEK değerle döner."""
        import src.main as main_module

        monkeypatch.setattr(main_module, "scalper_engine", None)
        payload = await main_module.scalper_status()
        assert set(payload) == set(main_module._EMPTY_SCALPER_STATUS)
        assert payload["market_data_guard"]["host"] == host_of(
            main_module.settings.market_data_base_url
        )
        assert "mode" in payload["tv_events"]


# ---------------------------------------------------------------------------
# 4) Ağırlık / oran sınırlayıcı / ban semantiği
# ---------------------------------------------------------------------------

class _FakeHttpClient:
    """httpx.AsyncClient yerine geçer; sıradaki yanıtı döner ve çağrılan
    URL'leri kaydeder."""

    def __init__(self, responses: List[httpx.Response]):
        self._responses = list(responses)
        self.calls: List[str] = []
        self.params: List[Optional[Dict[str, Any]]] = []

    async def get(self, url: str, params: Optional[Dict[str, Any]] = None):
        self.calls.append(url)
        self.params.append(params)
        if not self._responses:
            raise AssertionError(f"beklenmeyen ek istek: {url}")
        nxt = self._responses.pop(0)
        if isinstance(nxt, BaseException):   # ağ hatası senaryosu
            raise nxt
        return nxt

    async def aclose(self) -> None:
        return None


def _fetcher(base_url: str, responses: List[httpx.Response]) -> KlineFetcher:
    fetcher = KlineFetcher(base_url=base_url)
    fetcher._client = _FakeHttpClient(responses)
    return fetcher


def _ban_response(status: int = 418, msg: str = "Way too many requests; IP banned.") -> httpx.Response:
    return httpx.Response(
        status_code=status,
        json={"code": -1003, "msg": msg},
        headers={"X-MBX-USED-WEIGHT-1M": "2450"},
        request=httpx.Request("GET", "https://example.invalid/fapi/v1/klines"),
    )


def _fill_window(host: str, weight: int, age_seconds: float = 0.0) -> Any:
    """Kayan ağırlık penceresini tek girdiyle doldur (yaş = kaç sn önce)."""
    state = MarketDataGuard._state(host)
    state.window.clear()
    state.window_weight = 0
    state.add(time.monotonic() - age_seconds, weight)
    return state


def _ok_response(rows: Optional[list] = None, used_weight: str = "12") -> httpx.Response:
    if rows is None:
        rows = [[0, "1", "2", "0.5", "1.5", "10", 1, "0", 0, "0", "0", "0"]]
    return httpx.Response(
        status_code=200,
        json=rows,
        headers={"X-MBX-USED-WEIGHT-1M": used_weight},
        request=httpx.Request("GET", "https://example.invalid/fapi/v1/klines"),
    )


class TestRateLimiterCoverage:
    async def test_guard_is_used_for_both_hosts(self, monkeypatch):
        """Oran sınırlayıcı hem işlem host'u hem AYRI mainnet host'u için
        çağrılır — yeni public trafik başıboş değildir."""
        seen: List[tuple] = []
        original = MarketDataGuard.acquire.__func__

        async def spy(cls, base_url, weight, mode="live"):
            seen.append((base_url, weight, mode))
            await original(cls, base_url, weight, mode)

        monkeypatch.setattr(MarketDataGuard, "acquire", classmethod(spy))

        await _fetcher(TESTNET, [_ok_response()])._fetch("BTCUSDT", "5m", 150, None)
        await _fetcher(MAINNET, [_ok_response()])._fetch("BTCUSDT", "5m", 250, None)

        assert seen == [(TESTNET, 2, "live"), (MAINNET, 2, "live")]

    async def test_spacing_is_enforced_per_host(self, monkeypatch):
        """Aynı host'a arka arkaya iki istek arasında asgari boşluk uygulanır
        (slot asyncio.Lock ALTINDA rezerve edilir — kilitsiz check-then-act
        yarışı yok, bkz. rate_limiter 2026-08-14 düzeltmesi)."""
        slept: List[float] = []
        real_sleep = asyncio.sleep

        async def fake_sleep(seconds):
            slept.append(seconds)
            await real_sleep(0)

        monkeypatch.setattr(data_module, "_sleep", fake_sleep)

        await MarketDataGuard.acquire(MAINNET, 2)
        await MarketDataGuard.acquire(MAINNET, 2)

        assert slept, "ikinci istek boşluk beklemeden gitti"
        assert 0 < slept[0] <= data_module._MIN_REQUEST_SPACING_SECONDS

    async def test_separate_hosts_do_not_share_spacing(self, monkeypatch):
        slept: List[float] = []
        real_sleep = asyncio.sleep

        async def fake_sleep(seconds):
            slept.append(seconds)
            await real_sleep(0)

        monkeypatch.setattr(data_module, "_sleep", fake_sleep)

        await MarketDataGuard.acquire(MAINNET, 2)
        await MarketDataGuard.acquire(TESTNET, 2)

        assert slept == []

    async def test_weight_budget_raises_instead_of_blocking(self):
        """Bütçe dolduğunda istek ATILMAZ ve BEKLENMEZ: `MarketDataBudgetError`.

        Beklemek host kilidi altında olurdu ve safety döngüsünün mum çekimini
        60 sn'ye kadar bloklardı (tazelik limiti 30 sn) → watchdog restart'ı.
        Restart, tarihsel felaket yolunun ta kendisidir.
        """
        state = _fill_window(
            host_of(MAINNET), data_module._MARKET_DATA_WEIGHT_BUDGET_PER_MINUTE
        )

        started = time.monotonic()
        with pytest.raises(data_module.MarketDataBudgetError):
            await MarketDataGuard.acquire(MAINNET, 2)
        assert time.monotonic() - started < 1.0, "bütçe dalı olay döngüsünü bekletti"
        # Bütçe TÜKETİLMEDİ (istek gitmedi) ve pencere yapay olarak sıfırlanmadı.
        assert state.window_weight == data_module._MARKET_DATA_WEIGHT_BUDGET_PER_MINUTE

    async def test_weight_budget_recovers_after_window(self):
        """60 sn'den eski girdiler pencereden düşer — kalıcı susma yok."""
        state = _fill_window(
            host_of(MAINNET),
            data_module._MARKET_DATA_WEIGHT_BUDGET_PER_MINUTE,
            age_seconds=data_module._WEIGHT_WINDOW_SECONDS + 1,
        )

        await MarketDataGuard.acquire(MAINNET, 2)
        assert state.window_weight == 2

    async def test_weight_window_slides_not_tumbles(self):
        """Pencere KAYAR (dokümante edilen davranış), sabit sınırda
        sıfırlanmaz (düşmanca inceleme bulgusu).

        Tumbling pencerede sınır anında sayaç sıfırlandığı için 60 sn'lik
        herhangi bir kayan aralığa bütçenin İKİ KATI sığabiliyordu. Burada
        30 sn önce harcanan ağırlık HÂLÂ pencerededir.
        """
        budget = data_module._MARKET_DATA_WEIGHT_BUDGET_PER_MINUTE
        state = _fill_window(host_of(MAINNET), budget - 2, age_seconds=30.0)
        # 30 sn önceki girdi hâlâ sayılır: 2 birimlik bir istek daha sığar...
        await MarketDataGuard.acquire(MAINNET, 2)
        assert state.window_weight == budget
        # ...ama bir sonraki reddedilir (tumbling olsaydı geçerdi).
        with pytest.raises(data_module.MarketDataBudgetError):
            await MarketDataGuard.acquire(MAINNET, 2)

    async def test_budget_error_is_not_retried_by_fetch(self):
        """Bütçe hatası httpx retry döngüsüne DÜŞMEZ (ayrı tip) — istek ağa
        hiç çıkmaz."""
        _fill_window(
            host_of(MAINNET), data_module._MARKET_DATA_WEIGHT_BUDGET_PER_MINUTE
        )
        fetcher = _fetcher(MAINNET, [])          # istek ağa çıkarsa AssertionError
        with pytest.raises(data_module.MarketDataBudgetError):
            await fetcher._fetch("BTCUSDT", "5m", 250, None)
        assert fetcher._client.calls == []

    def test_weight_table_matches_live_limits(self):
        # Canlı motorun kullandığı limitler: 250/100/150 (scan) + 200 (exits)
        assert klines_weight(250) == klines_weight(100) == klines_weight(150) == 2
        assert klines_weight(200) == 2
        assert klines_weight(99) == 1
        assert klines_weight(1000) == 5
        assert klines_weight(1500) == 10

    def test_weight_header_is_recorded(self):
        MarketDataGuard.note_response(MAINNET, {"X-MBX-USED-WEIGHT-1M": "1234"})
        assert MarketDataGuard.snapshot(MAINNET)["used_weight_1m"] == 1234


class TestBanSemantics:
    async def test_418_trips_breaker_without_retry(self):
        fetcher = _fetcher(MAINNET, [_ban_response()])
        with pytest.raises(MarketDataBanError):
            await fetcher._fetch("BTCUSDT", "5m", 250, None)
        # Tek istek: ban sırasında tekrar denemek yasağı uzatır.
        assert len(fetcher._client.calls) == 1
        assert MarketDataGuard.snapshot(MAINNET)["banned"] is True

    async def test_ban_log_matches_deploy_guard_pattern(self, monkeypatch):
        """`scripts/server_deploy.sh` logda 'HTTP 418|banned' arar; public
        yolun banı bu güvenliğe GÖRÜNÜR olmalı."""
        fetcher = _fetcher(MAINNET, [_ban_response()])
        critical: List[str] = []
        fetcher.logger = SimpleNamespace(
            critical=lambda msg, *a, **kw: critical.append(msg),
            warning=lambda *a, **kw: None,
            error=lambda *a, **kw: None,
        )
        with pytest.raises(MarketDataBanError):
            await fetcher._fetch("BTCUSDT", "5m", 250, None)
        assert critical and "HTTP 418" in critical[0]

    async def test_second_request_is_refused_without_network(self):
        fetcher = _fetcher(MAINNET, [_ban_response()])
        with pytest.raises(MarketDataBanError):
            await fetcher._fetch("BTCUSDT", "5m", 250, None)
        # Yanıt kuyruğu boş: bir istek daha ağa çıksaydı AssertionError alırdık.
        with pytest.raises(MarketDataBanError):
            await fetcher._fetch("ETHUSDT", "5m", 250, None)
        assert len(fetcher._client.calls) == 1

    async def test_public_ban_does_not_trip_signed_breaker(self):
        """BİLİNÇLİ ASİMETRİ: aynı host olsa bile public ban imzalı kesiciyi
        KURMAZ. KlineFetcher BINANCE_BIND_IP'ye bind edilmez → iki yol aynı
        host'a farklı IP'den gidebilir; public ban imzalı yolun banlı olduğunun
        kanıtı değildir ve emir/çıkış yönetimini kanıtsız durdurmak (SL
        değişimi, kapanış doğrulaması) en pahalı hatadır."""
        fetcher = _fetcher(TESTNET, [_ban_response()])
        with pytest.raises(MarketDataBanError):
            await fetcher._fetch("BTCUSDT", "5m", 250, None)
        assert ImprovedBinanceClient._rest_blocked_until == 0.0
        # ...ama public taraf kendi kesicisiyle DURUR (yasağı uzatmaz).
        assert MarketDataGuard.snapshot(TESTNET)["banned"] is True

    async def test_separate_host_ban_does_not_block_trading_host(self):
        """Mainnet VERİ banı testnet EMİR yönetimini kilitlemez."""
        fetcher = _fetcher(MAINNET, [_ban_response()])
        with pytest.raises(MarketDataBanError):
            await fetcher._fetch("BTCUSDT", "5m", 250, None)
        assert ImprovedBinanceClient._rest_blocked_until == 0.0
        # ...ve testnet public çekimi de serbest kalır.
        ok = _fetcher(TESTNET, [_ok_response()])
        await ok._fetch("BTCUSDT", "5m", 150, None)
        assert len(ok._client.calls) == 1

    async def test_signed_client_ban_stops_klines_on_same_host(self):
        """Ters yön: imzalı yolun banı public kline çekimini de durdurur."""
        ImprovedBinanceClient._trip_breaker("banned", default_seconds=120.0)
        fetcher = _fetcher(TESTNET, [])          # istek ağa çıkarsa AssertionError
        with pytest.raises(MarketDataBanError):
            await fetcher._fetch("BTCUSDT", "5m", 150, None)
        assert fetcher._client.calls == []

    async def test_signed_client_ban_does_not_stop_separate_market_data(self):
        ImprovedBinanceClient._trip_breaker("banned", default_seconds=120.0)
        fetcher = _fetcher(MAINNET, [_ok_response()])
        await fetcher._fetch("BTCUSDT", "5m", 250, None)
        assert len(fetcher._client.calls) == 1

    async def test_429_is_treated_as_soft_ban(self):
        response = httpx.Response(
            status_code=429,
            json={"code": -1015, "msg": "Too many requests"},
            request=httpx.Request("GET", "https://example.invalid/fapi/v1/klines"),
        )
        fetcher = _fetcher(MAINNET, [response])
        with pytest.raises(MarketDataBanError):
            await fetcher._fetch("BTCUSDT", "5m", 250, None)
        blocked_until = MarketDataGuard.blocked_until(host_of(MAINNET))
        assert time.time() < blocked_until <= time.time() + data_module._BAN_DEFAULT_SECONDS_SOFT + 1

    async def test_banned_until_epoch_is_parsed(self):
        future_ms = int((time.time() + 600) * 1000)
        fetcher = _fetcher(
            MAINNET,
            [_ban_response(msg=f"Way too many requests; IP(1.2.3.4) banned until {future_ms}.")],
        )
        with pytest.raises(MarketDataBanError):
            await fetcher._fetch("BTCUSDT", "5m", 250, None)
        blocked_until = MarketDataGuard.blocked_until(host_of(MAINNET))
        assert blocked_until == pytest.approx(future_ms / 1000.0 + 5.0, abs=0.01)

    async def test_non_ban_http_error_still_retries(self, monkeypatch):
        """Ban DIŞI hatalarda bugünkü davranış korunur: 3 deneme + backoff."""
        real_sleep = asyncio.sleep
        monkeypatch.setattr(data_module, "_sleep", lambda s: real_sleep(0))
        error = httpx.Response(
            status_code=500, json={"code": -1000, "msg": "internal"},
            request=httpx.Request("GET", "https://example.invalid/fapi/v1/klines"),
        )
        fetcher = _fetcher(MAINNET, [error, error, _ok_response()])
        candles = await fetcher._fetch("BTCUSDT", "5m", 250, None)
        assert len(fetcher._client.calls) == 3
        # Üçüncü deneme başarılı: mum gerçekten dönüyor (kesici KURULMADI).
        assert len(candles) == 1
        assert MarketDataGuard.snapshot(MAINNET)["banned"] is False

    async def test_scan_tick_aborts_round_on_market_data_ban(self):
        """Host geneli ban/bütçe hatasında tarama turu KESİLİR: kalan
        semboller için ayrı ayrı traceback basılmaz, gereksiz istek atılmaz.
        Sinyal üretilmemesi fail-closed'dır (SL/TP borsada durur)."""
        engine = ScalperEngine.__new__(ScalperEngine)
        engine.cfg = SimpleNamespace(
            scalper_symbol_allowlist="AAAUSDT,BBBUSDT,CCCUSDT",
            scalper_strategies="C",
            scalper_max_positions=3,
        )
        engine.client = SimpleNamespace(get_all_positions=lambda: _async_value([]))
        engine.exits = SimpleNamespace(tracked_symbols=lambda: set())
        engine.executor = SimpleNamespace(pending_symbols=lambda: set())
        engine._universe = []
        engine._scan_open_symbols = set()

        evaluated: List[str] = []
        warnings: List[str] = []
        errors: List[str] = []
        engine.logger = SimpleNamespace(
            warning=lambda msg, *a, **kw: warnings.append(msg),
            error=lambda msg, *a, **kw: errors.append(msg),
            info=lambda *a, **kw: None,
        )
        engine._entries_ready = lambda: True
        engine._executor_entry_blocked = lambda symbol: False

        async def boom(symbol, strategies):
            evaluated.append(symbol)
            raise MarketDataBanError("ban", host_of(MAINNET), time.time() + 60)

        engine._evaluate_symbol = boom
        await engine._scan_tick()

        assert evaluated == ["AAAUSDT"], "tur kesilmedi, diğer semboller denendi"
        assert errors == [], "ban için traceback'li ERROR basıldı"
        assert any("Piyasa verisi kullanılamıyor" in w for w in warnings)

    async def test_cache_still_prevents_repeat_requests(self):
        """Mevcut TTL önbelleği KORUNDU — guard fazladan istek doğurmaz."""
        fetcher = _fetcher(MAINNET, [_ok_response()])
        await fetcher.get_klines("BTCUSDT", "5m", 150)
        await fetcher.get_klines("BTCUSDT", "5m", 150)
        assert len(fetcher._client.calls) == 1

    async def test_429_with_1003_matches_deploy_guard_pattern(self):
        """İLK ban sinyali tipik olarak 429/-1003'tür (418 ancak ban sırasında
        istek atmaya devam edilirse gelir — D17 tasarımı gereği etmiyoruz).
        Deploy kilidi `HTTP 418|banned` arıyor: 429 satırı 'banned' içermeli."""
        fetcher = _fetcher(MAINNET, [_ban_response(status=429)])
        critical: List[str] = []
        fetcher.logger = SimpleNamespace(
            critical=lambda msg, *a, **kw: critical.append(msg),
            warning=lambda *a, **kw: None, error=lambda *a, **kw: None,
        )
        with pytest.raises(MarketDataBanError):
            await fetcher._fetch("BTCUSDT", "5m", 250, None)
        assert critical and "banned" in critical[0]

    async def test_ongoing_ban_warning_matches_deploy_guard_pattern(self, monkeypatch):
        """SÜREN ban: tek seferlik trip satırı 15 dk sonra pencereden düşer;
        periyodik kesici satırı kilidi açık tutmalı (ban aktifken restart YASAK)."""
        warnings: List[str] = []
        monkeypatch.setattr(
            data_module.app_logger, "warning",
            lambda msg, *a, **kw: warnings.append(msg),
        )
        MarketDataGuard.trip(host_of(MAINNET), "banned", 120.0)
        with pytest.raises(MarketDataBanError):
            MarketDataGuard.ensure_allowed(host_of(MAINNET))
        assert warnings and "banned" in warnings[0]

    async def test_permanent_4xx_is_not_retried(self):
        """`-1121 Invalid symbol` kendiliğinden düzelmez: 3 deneme (1+2 sn uyku)
        sembol başına ~3 sn ve 2 gereksiz istek harcardı."""
        invalid = httpx.Response(
            status_code=400,
            json={"code": -1121, "msg": "Invalid symbol."},
            request=httpx.Request("GET", "https://example.invalid/fapi/v1/klines"),
        )
        fetcher = _fetcher(MAINNET, [invalid])
        with pytest.raises(data_module.MarketDataRequestError):
            await fetcher._fetch("XYZUSDT", "5m", 250, None)
        assert len(fetcher._client.calls) == 1

    def test_permanent_error_is_symbol_scoped_not_host_scoped(self):
        """Kalıcı sembol hatası `MarketDataUnavailable` OLMAMALI — yoksa
        `_scan_tick` tek bozuk sembol yüzünden TÜM turu keserdi."""
        assert not issubclass(
            data_module.MarketDataRequestError, data_module.MarketDataUnavailable
        )

    async def test_5xx_still_retries_three_times(self, monkeypatch):
        """Geçici sunucu hataları ESKİSİ GİBİ 3 kez denenir."""
        real_sleep = asyncio.sleep
        monkeypatch.setattr(data_module, "_sleep", lambda s: real_sleep(0))
        error = httpx.Response(
            status_code=503, json={"code": -1001, "msg": "internal"},
            request=httpx.Request("GET", "https://example.invalid/fapi/v1/klines"),
        )
        fetcher = _fetcher(MAINNET, [error, error, _ok_response()])
        candles = await fetcher._fetch("BTCUSDT", "5m", 250, None)
        assert len(fetcher._client.calls) == 3
        assert len(candles) == 1

    async def test_slow_symbol_does_not_block_other_symbols(self):
        """Anahtar BAŞINA kilit: yavaş/asılı bir sembolün çekimi BAŞKA bir
        sembolün mumunu bloklamamalı (safety turu tazelik limiti 30 sn)."""
        gate = asyncio.Event()

        class _HangingClient:
            def __init__(self):
                self.calls: List[str] = []

            async def get(self, url, params=None):
                self.calls.append(str(params.get("symbol")))
                if params.get("symbol") == "SLOWUSDT":
                    await gate.wait()
                return _ok_response()

            async def aclose(self):
                return None

        fetcher = KlineFetcher(base_url=MAINNET)
        fetcher._client = _HangingClient()
        slow = asyncio.create_task(fetcher.get_klines("SLOWUSDT", "5m", 150))
        await asyncio.sleep(0)
        fast = await asyncio.wait_for(
            fetcher.get_klines("FASTUSDT", "5m", 150), timeout=2.0
        )
        assert len(fast) == 1
        gate.set()
        await slow


class TestTrailingPriceSpace:
    """D17 düşmanca inceleme (HIGH ×2): chandelier MUTLAK bir fiyat üretir ve
    bu değer `pm.replace_stop_loss` ile İŞLEM borsasına emir olarak gider.
    Ayrı market-data host'unda seviye YABANCI bir defterin fiyat uzayındadır;
    baz farkı k×ATR'yi aşarsa Binance -2021 verir ve `position_manager` bunu
    "piyasa stop'u geçti" sayıp pozisyonu ACİL KAPATIR (kârlı koşucu piyasa
    emriyle kapanır; log "eski SL korunuyor" derken kayıt TRAIL etiketlenir).

    İlk düzeltme STATİK bir baz kullanıyordu (`position.entry_price −
    signal.entry_price`) ve iki yerde kırılıyordu:
      1. baz yalnız GİRİŞ anında ölçülüp pozisyon ömrü boyunca sabit
         uygulanıyordu — borsalar arası baz saatler içinde kayar;
      2. `recover()` iki fiyatı da `trade.entry_price`'tan kurduğu için
         restart sonrası baz 0 çıkıyor, düzeltme SESSİZCE no-op oluyordu.
    Bu sınıf DİNAMİK bazı kilitler: `baz = işlem_host_güncel_fiyat −
    veri_host_son_kapanış`, her turda yeniden ölçülür.
    """

    @staticmethod
    def _exits(market_url: str, price_age: float = 0.0) -> Any:
        from src.strategies.scalper.exits import ExitManager

        mgr = ExitManager.__new__(ExitManager)   # __init__ atlanır (ağ yok)
        mgr.cfg = SimpleNamespace(
            scalper_market_data_base_url=market_url,
            binance_base_url=TESTNET,
        )
        mgr._trading_price_seen_at = {"BTCUSDT": time.monotonic() - price_age}
        return mgr

    @staticmethod
    def _position(current_price: float) -> Any:
        return SimpleNamespace(
            signal=SimpleNamespace(entry_price=100.0),
            position=SimpleNamespace(entry_price=100.0, current_price=current_price),
        )

    def test_same_host_is_noop(self):
        """Varsayılan (tek host): bugünkü davranış BİREBİR korunur — çeviri
        hiç uygulanmaz, `data_reference` okunmaz bile."""
        mgr = self._exits("")
        sp = self._position(100.5)
        assert mgr._to_trading_price_space(sp, 99.0, 88.0, "BTCUSDT") == 99.0

    def test_dynamic_basis_uses_current_prices(self):
        """Baz = işlem host'u güncel fiyatı − veri host'u son kapanışı."""
        mgr = self._exits(MAINNET)
        # İşlem host'u 100.4, veri host'unun son kapanışı 100.0 → baz +0.4
        sp = self._position(100.4)
        assert mgr._to_trading_price_space(
            sp, 99.0, 100.0, "BTCUSDT"
        ) == pytest.approx(99.4)

    def test_basis_is_independent_of_entry_prices(self):
        """`recover()` sonrası sinyal ve dolum fiyatı AYNIDIR (baz 0 olurdu);
        dinamik baz yine de doğru çeviriyi yapar — restart'ta ek DB kolonu
        gerekmez."""
        mgr = self._exits(MAINNET)
        sp = self._position(100.4)
        sp.signal.entry_price = 55.0     # statik baz olsaydı sonuç değişirdi
        sp.position.entry_price = 55.0
        assert mgr._to_trading_price_space(
            sp, 99.0, 100.0, "BTCUSDT"
        ) == pytest.approx(99.4)

    def test_shift_preserves_distance(self):
        """Öteleme mesafeyi (birim riski) korur — ölçek değiştirmez."""
        mgr = self._exits(MAINNET)
        sp = self._position(100.4)
        moved = mgr._to_trading_price_space(sp, 99.0, 100.0, "BTCUSDT")
        assert (100.4 - moved) == pytest.approx(100.0 - 99.0)

    def test_stale_trading_price_is_refused(self):
        """Bayat işlem fiyatı + taze veri kapanışı = SAHTE baz. En tehlikeli
        hâli `recover()` sonrası ilk turdur (current_price = giriş fiyatı)."""
        mgr = self._exits(MAINNET, price_age=999.0)
        sp = self._position(100.4)
        assert mgr._to_trading_price_space(sp, 99.0, 100.0, "BTCUSDT") is None

    def test_unknown_symbol_has_no_reference(self):
        mgr = self._exits(MAINNET)
        sp = self._position(100.4)
        assert mgr._to_trading_price_space(sp, 99.0, 100.0, "ETHUSDT") is None

    def test_missing_prices_are_refused(self):
        mgr = self._exits(MAINNET)
        assert mgr._to_trading_price_space(self._position(0.0), 99.0, 100.0, "BTCUSDT") is None
        assert mgr._to_trading_price_space(self._position(100.4), 99.0, 0.0, "BTCUSDT") is None
        # price<=0 "hesaplanamadı" demektir; çağıran zaten ayıklar.
        assert mgr._to_trading_price_space(self._position(100.4), 0.0, 100.0, "BTCUSDT") == 0.0

    def test_absurd_basis_is_refused(self):
        """%2'yi aşan baz = yanlış sembol/ölçek ya da donmuş bir defter."""
        mgr = self._exits(MAINNET)
        sp = self._position(100.0)
        assert mgr._to_trading_price_space(sp, 99.0, 50.0, "BTCUSDT") is None

    def test_missing_cfg_fields_are_treated_as_same_host(self):
        """Eski test çiftleri (SimpleNamespace) alanı hiç tanımlamayabilir."""
        from src.strategies.scalper.exits import ExitManager

        mgr = ExitManager.__new__(ExitManager)
        mgr.cfg = SimpleNamespace()
        assert mgr._market_data_is_separate() is False


class TestProtectiveSideGate:
    """Çeviriden SONRAKİ ikinci kalkan: seviye işlem host'unun GÜNCEL fiyatına
    göre yanlış taraftaysa emir HİÇ gönderilmez. Gönderilseydi Binance -2021
    verir, `position_manager._replace_stop_loss` bunu bir çıkış kararı sayıp
    pozisyonu PİYASA emriyle kapatırdı."""

    @staticmethod
    def _mgr(market_url: str, replace_ok: bool = True) -> Any:
        from src.strategies.scalper.exits import ExitManager

        mgr = ExitManager.__new__(ExitManager)
        mgr.cfg = SimpleNamespace(
            scalper_market_data_base_url=market_url,
            binance_base_url=TESTNET,
            scalper_tf_entry="5m",
            scalper_chandelier_atr_period=22,
            scalper_chandelier_atr_mult=3.0,
        )
        mgr.logger = SimpleNamespace(
            info=lambda *a, **kw: None, debug=lambda *a, **kw: None,
            warning=lambda *a, **kw: None, error=lambda *a, **kw: None,
        )
        mgr._market_data_down_reason = None
        mgr._trading_price_seen_at = {}
        mgr._trailing_space_skips = 0
        mgr._trailing_gate_skips = 0
        mgr._trailing_skip_log_at = {}
        return mgr

    def test_long_stop_must_be_below_current_price(self):
        from src.strategies.scalper.exits import ExitManager

        gate = ExitManager._is_protective_side
        assert gate(Direction.LONG, 99.0, 100.0) is True
        assert gate(Direction.LONG, 100.0, 100.0) is False
        assert gate(Direction.LONG, 101.0, 100.0) is False
        # Pay kadar uzak olmalı (mark/last farkı).
        assert gate(Direction.LONG, 99.999, 100.0) is False

    def test_short_stop_must_be_above_current_price(self):
        from src.strategies.scalper.exits import ExitManager

        gate = ExitManager._is_protective_side
        assert gate(Direction.SHORT, 101.0, 100.0) is True
        assert gate(Direction.SHORT, 100.0, 100.0) is False
        assert gate(Direction.SHORT, 99.0, 100.0) is False

    def test_missing_price_is_not_protective(self):
        from src.strategies.scalper.exits import ExitManager

        assert ExitManager._is_protective_side(Direction.LONG, 99.0, 0.0) is False
        assert ExitManager._is_protective_side(Direction.LONG, 0.0, 100.0) is False


class TestTrailingRoundIntegration:
    """`_update_trailing` uçtan uca: aynı host'ta BİREBİR eski davranış, ayrı
    host'ta dinamik çeviri + koruma kapısı."""

    @staticmethod
    def _candles(closes: List[float]) -> List[Any]:
        from src.strategies.scalper.types import Candle

        out = []
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

    def _mgr(
        self,
        market_url: str,
        candles: List[Any],
        replaced: List[float],
        data_price: Any = None,
    ) -> Any:
        """`data_price`: veri host'unun CANLI fiyatı (D17-R3 baz referansı).

        `None` = fetcher HİÇ bağlı değil (ayrı host'ta baz ölçülemez → tur
        atlanır); sayı = sabit fiyat; çağrılabilir/istisna = özel senaryo.
        Aynı host'ta bu fetcher ÇAĞRILMAMALIDIR (test: `_calls`).
        """
        from src.strategies.scalper.exits import ExitManager

        mgr = ExitManager.__new__(ExitManager)
        mgr.cfg = SimpleNamespace(
            scalper_market_data_base_url=market_url,
            binance_base_url=TESTNET,
            scalper_tf_entry="5m",
            scalper_chandelier_atr_period=14,
            scalper_chandelier_atr_mult=3.0,
            scalper_trail_mult_tiers="",
        )
        mgr.logger = SimpleNamespace(
            info=lambda *a, **kw: None, debug=lambda *a, **kw: None,
            warning=lambda *a, **kw: None, error=lambda *a, **kw: None,
        )
        mgr._market_data_down_reason = None
        mgr._trading_price_seen_at = {"BTCUSDT": time.monotonic()}
        mgr._trailing_space_skips = 0
        mgr._trailing_gate_skips = 0
        mgr._trailing_skip_log_at = {}
        mgr._data_price_error_log_at = {}

        async def fetch(symbol, tf, limit):
            return candles

        mgr.kline_fetch = fetch

        mgr.data_price_calls: List[str] = []
        if data_price is None:
            mgr.data_price_fetch = None
        else:
            async def price_fetch(symbol):
                mgr.data_price_calls.append(symbol)
                if isinstance(data_price, BaseException):
                    raise data_price
                if callable(data_price):
                    return data_price(symbol)
                return data_price

            mgr.data_price_fetch = price_fetch

        async def replace(position, new_stop):
            replaced.append(new_stop)
            return True

        mgr.pm = SimpleNamespace(replace_stop_loss=replace)
        return mgr

    @staticmethod
    def _sp(current_price: float) -> Any:
        return SimpleNamespace(
            signal=SimpleNamespace(direction=Direction.LONG, entry_price=100.0),
            position=SimpleNamespace(
                entry_price=100.0, current_price=current_price,
                current_stoploss=90.0, symbol="BTCUSDT",
            ),
            plan=SimpleNamespace(
                breakeven_price=100.1, runner_floor_price=None, tp1_price=101.0
            ),
            entry_candle_time=0,
            mfe_pct=1.0,
            tp2_done=False,
        )

    async def test_same_host_stop_is_byte_for_byte_unchanged(self):
        """Ayar BOŞKEN gönderilen stop, çeviri/kapı eklenmeden ÖNCEKİ ile
        birebir aynı olmalı — canlı davranış değişmedi."""
        closes = [100.0 + i * 0.1 for i in range(60)]
        candles = self._candles(closes)

        same_host: List[float] = []
        mgr = self._mgr("", candles, same_host)
        await mgr._update_trailing("BTCUSDT", self._sp(106.0))

        # Referans: çeviri/kapı OLMADAN saf chandelier + floor aritmetiği.
        from src.strategies.scalper.indicators import chandelier_stop
        from src.strategies.scalper.types import resolve_trail_mult

        raw = chandelier_stop(
            candles, direction=Direction.LONG,
            atr_mult=resolve_trail_mult(mgr.cfg, 1.0), atr_period=14, since_index=0,
        )
        expected = max(100.1, raw)
        assert same_host == [expected]
        # Aynı host: veri host'u fiyatı HİÇ istenmez (ek ağırlık yok).
        assert mgr.data_price_calls == []

    async def test_separate_host_shifts_and_still_sends(self):
        """Ayrı host: veri host'unun CANLI fiyatı 100.0'ken işlem fiyatı 100.4
        ise stop +0.4 ötelenir ve emir GİDER. Baz artık mum kapanışına DEĞİL,
        iki host'un CANLI fiyatına bakar (D17-R3)."""
        closes = [100.0 + i * 0.1 for i in range(60)]
        candles = self._candles(closes)
        data_live = candles[-1].close       # veri host'u canlı fiyatı

        sent: List[float] = []
        mgr = self._mgr(MAINNET, candles, sent, data_price=data_live)
        await mgr._update_trailing("BTCUSDT", self._sp(data_live + 0.4))

        from src.strategies.scalper.indicators import chandelier_stop
        from src.strategies.scalper.types import resolve_trail_mult

        raw = chandelier_stop(
            candles, direction=Direction.LONG,
            atr_mult=resolve_trail_mult(mgr.cfg, 1.0), atr_period=14, since_index=0,
        )
        assert sent == [pytest.approx(max(100.1, raw + 0.4))]
        assert mgr.data_price_calls == ["BTCUSDT"]

    async def test_separate_host_wrong_side_is_not_sent(self):
        """İşlem host'u çok daha düşükse ötelenmiş stop güncel fiyatın ÜSTÜNE
        düşer: emir gönderilmez, sayaç artar, eski SL korunur."""
        closes = [100.0 + i * 0.1 for i in range(60)]
        candles = self._candles(closes)
        sent: List[float] = []
        data_live = candles[-1].close
        mgr = self._mgr(MAINNET, candles, sent, data_price=data_live)
        # İşlem host'u veri host'unun %1.5 altında (baz negatif ama %2 tavanının
        # içinde) → ötelenmiş chandelier hâlâ güncel fiyatın üstünde kalır.
        sp = self._sp(data_live * 0.985)
        sp.plan.breakeven_price = sp.position.current_price * 1.01
        await mgr._update_trailing("BTCUSDT", sp)

        assert sent == [], "yanlış taraftaki stop borsaya gönderildi (-2021 riski)"
        assert mgr.trailing_skip_snapshot()["protective_gate_skips"] == 1

    async def test_separate_host_unmeasurable_basis_skips_round(self):
        """Baz ölçülemiyorsa (işlem fiyatı bayat) tur atlanır — YABANCI
        uzaydan emir gönderilmez."""
        closes = [100.0 + i * 0.1 for i in range(60)]
        candles = self._candles(closes)
        sent: List[float] = []
        mgr = self._mgr(
            MAINNET, candles, sent, data_price=candles[-1].close
        )
        mgr._trading_price_seen_at = {}          # hiç taze fiyat yok
        await mgr._update_trailing("BTCUSDT", self._sp(candles[-1].close + 0.4))

        assert sent == []
        assert mgr.trailing_skip_snapshot()["price_space_skips"] == 1

    async def test_skip_warning_is_rate_limited(self):
        """Safety turu 2 sn'de bir döner: sembol başına en fazla 60 sn'de bir
        satır (aksi halde saatte 1800 satır)."""
        closes = [100.0 + i * 0.1 for i in range(60)]
        candles = self._candles(closes)
        sent: List[float] = []
        mgr = self._mgr(
            MAINNET, candles, sent, data_price=candles[-1].close
        )
        mgr._trading_price_seen_at = {}
        warnings: List[str] = []
        mgr.logger = SimpleNamespace(
            info=lambda *a, **kw: None, debug=lambda *a, **kw: None,
            warning=lambda msg, *a, **kw: warnings.append(msg),
            error=lambda *a, **kw: None,
        )
        for _ in range(5):
            await mgr._update_trailing("BTCUSDT", self._sp(candles[-1].close + 0.4))
        assert len(warnings) == 1
        assert mgr.trailing_skip_snapshot()["price_space_skips"] == 5


class TestLikeForLikeBasis:
    """D17-R3 (bütünleşme incelemesi, medium) — baz İKİ CANLI fiyatın farkıdır.

    Eski biçim `işlem_host_CANLI − veri_host_son_KAPANIŞ` iki farklı türü
    çıkarıyordu: fark, borsa-arası bazın ÜSTÜNE MUM-İÇİ sürüklenmeyi de
    bindiriyordu. Etki sistematiktir — fiyat pozisyonun lehine gittikçe
    sürüklenme büyür, chandelier mandalı (`new_stop > current_sl`) her turda
    biraz daha sıkışır ve stop fiilen CANLI FİYATI izler, chandelier
    MESAFESİNİ değil.
    """

    _candles = staticmethod(TestTrailingRoundIntegration._candles)
    _sp = staticmethod(TestTrailingRoundIntegration._sp)
    _mgr = TestTrailingRoundIntegration._mgr

    @staticmethod
    def _chandelier(mgr, candles) -> float:
        from src.strategies.scalper.indicators import chandelier_stop
        from src.strategies.scalper.types import resolve_trail_mult

        return chandelier_stop(
            candles, direction=Direction.LONG,
            atr_mult=resolve_trail_mult(mgr.cfg, 1.0), atr_period=14, since_index=0,
        )

    async def test_intra_candle_drift_does_not_tighten_the_stop(self):
        """İki host AYNI fiyatta (baz 0) ama mum kapanışı geride: stop
        ÖTELENMEZ. Eski biçimde sürüklenme kadar (+%1) yukarı kilitlenirdi."""
        closes = [100.0 + i * 0.1 for i in range(60)]
        candles = self._candles(closes)
        last_close = candles[-1].close
        live = last_close * 1.01          # mum kapandıktan sonra %1 yükseldi

        sent: List[float] = []
        mgr = self._mgr(MAINNET, candles, sent, data_price=live)
        await mgr._update_trailing("BTCUSDT", self._sp(live))

        raw = self._chandelier(mgr, candles)
        assert sent == [pytest.approx(max(100.1, raw))]
        # Eski (mum kapanışlı) biçim bu turda sürüklenmeyi de eklerdi:
        assert sent[0] < max(100.1, raw + (live - last_close))

    async def test_basis_is_the_difference_of_two_live_prices(self):
        """Sürüklenme VARKEN bile öteleme yalnız borsa-arası bazdır."""
        closes = [100.0 + i * 0.1 for i in range(60)]
        candles = self._candles(closes)
        last_close = candles[-1].close
        data_live = last_close * 1.005        # veri host'u mum-içi yükseldi
        basis = 0.25                          # gerçek borsa-arası baz

        sent: List[float] = []
        mgr = self._mgr(MAINNET, candles, sent, data_price=data_live)
        await mgr._update_trailing("BTCUSDT", self._sp(data_live + basis))

        raw = self._chandelier(mgr, candles)
        assert sent == [pytest.approx(max(100.1, raw + basis))]

    async def test_unreadable_data_price_skips_the_round(self):
        """Veri host'u fiyatı okunamazsa çeviri None → tur atlanır (fail-safe)."""
        closes = [100.0 + i * 0.1 for i in range(60)]
        candles = self._candles(closes)
        sent: List[float] = []
        mgr = self._mgr(
            MAINNET, candles, sent, data_price=RuntimeError("ticker 500")
        )
        await mgr._update_trailing("BTCUSDT", self._sp(candles[-1].close + 0.4))

        assert sent == []
        assert mgr.trailing_skip_snapshot()["price_space_skips"] == 1

    async def test_host_wide_outage_silences_the_rest_of_the_round(self):
        """Ban/bütçe (`MarketDataUnavailable`) tur genelinde susturur —
        kline yolundaki davranışın AYNISI."""
        closes = [100.0 + i * 0.1 for i in range(60)]
        candles = self._candles(closes)
        sent: List[float] = []
        mgr = self._mgr(
            MAINNET, candles, sent,
            data_price=MarketDataBanError("418 ban", "fapi.binance.com", 60.0),
        )
        await mgr._update_trailing("BTCUSDT", self._sp(candles[-1].close + 0.4))

        assert sent == []
        assert mgr._market_data_down_reason is not None

    async def test_missing_price_fetcher_is_fail_closed(self):
        """Fetcher hiç bağlı değilse ayrı host'ta emir GÖNDERİLMEZ."""
        closes = [100.0 + i * 0.1 for i in range(60)]
        candles = self._candles(closes)
        sent: List[float] = []
        mgr = self._mgr(MAINNET, candles, sent, data_price=None)
        await mgr._update_trailing("BTCUSDT", self._sp(candles[-1].close + 0.4))

        assert sent == []
        assert mgr.trailing_skip_snapshot()["price_space_skips"] == 1

    async def test_data_price_error_warning_is_rate_limited(self):
        closes = [100.0 + i * 0.1 for i in range(60)]
        candles = self._candles(closes)
        sent: List[float] = []
        mgr = self._mgr(
            MAINNET, candles, sent, data_price=RuntimeError("ticker 500")
        )
        warnings: List[str] = []
        mgr.logger = SimpleNamespace(
            info=lambda *a, **kw: None, debug=lambda *a, **kw: None,
            warning=lambda msg, *a, **kw: warnings.append(msg),
            error=lambda *a, **kw: None,
        )
        for _ in range(5):
            await mgr._update_trailing("BTCUSDT", self._sp(candles[-1].close + 0.4))
        # Bir "fiyat okunamadı" + bir "baz ölçülemedi" satırı; ikisi de
        # sembol başına 60 sn'de bir.
        assert len(warnings) == 2
        assert mgr.trailing_skip_snapshot()["price_space_skips"] == 5

    def test_engine_wires_the_same_fetcher_for_prices(self):
        """Baz referansı, mumlarla AYNI host/ağırlık bütçesi/kesiciden gelmeli."""
        import inspect

        from src.strategies.scalper.engine import ScalperEngine

        source = inspect.getsource(ScalperEngine.__init__)
        assert "data_price_fetch=self.fetcher.get_price" in source


def _price_response(payload: dict, used_weight: str = "12") -> httpx.Response:
    return httpx.Response(
        status_code=200,
        json=payload,
        headers={"X-MBX-USED-WEIGHT-1M": used_weight},
        request=httpx.Request("GET", "https://example.invalid/fapi/v1/ticker/price"),
    )


class TestDataHostPriceFetch:
    """`KlineFetcher.get_price` sözleşmesi — ağırlık 1, guard'dan geçer, TTL."""

    async def test_price_goes_through_the_guard_with_weight_one(self):
        fetcher = _fetcher(
            MAINNET, [_price_response({"symbol": "BTCUSDT", "price": "100.5"})]
        )
        try:
            assert await fetcher.get_price("BTCUSDT") == 100.5
            assert fetcher._client.calls[0].endswith("/fapi/v1/ticker/price")
            # Sembolsüz çağrı ağırlık 2'dir — sembol ZORUNLU.
            assert fetcher._client.params[0] == {"symbol": "BTCUSDT"}
            assert MarketDataGuard.snapshot(MAINNET)["window_weight"] == 1
        finally:
            await fetcher.close()

    async def test_price_is_cached_per_symbol(self):
        fetcher = _fetcher(MAINNET, [_price_response({"price": "100.5"})])
        try:
            assert await fetcher.get_price("BTCUSDT") == 100.5
            assert await fetcher.get_price("BTCUSDT") == 100.5
            assert len(fetcher._client.calls) == 1   # ikinci istek AĞA ÇIKMADI
        finally:
            await fetcher.close()

    async def test_ban_blocks_the_price_call_without_network(self):
        MarketDataGuard.trip("fapi.binance.com", "418", 60.0, hard=True)
        fetcher = _fetcher(MAINNET, [])
        try:
            with pytest.raises(data_module.MarketDataUnavailable):
                await fetcher.get_price("BTCUSDT")
            assert fetcher._client.calls == []
        finally:
            await fetcher.close()

    async def test_missing_price_field_is_an_error_not_zero(self):
        """Sessiz 0 YOK: 0 bir baz referansı olarak felakettir."""
        fetcher = _fetcher(MAINNET, [_price_response({"symbol": "BTCUSDT"})])
        try:
            with pytest.raises(data_module.MarketDataUnavailable):
                await fetcher.get_price("BTCUSDT")
        finally:
            await fetcher.close()

    async def test_price_is_not_retried(self):
        """Kline yolundan farkı bilinçli: safety turunu 3 denemeyle bayatlatma."""
        fetcher = _fetcher(MAINNET, [httpx.RequestError("ağ")])
        try:
            with pytest.raises(httpx.RequestError):
                await fetcher.get_price("BTCUSDT")
            assert len(fetcher._client.calls) == 1
        finally:
            await fetcher.close()

    async def test_ticker_weight_is_one_in_the_budget_table(self):
        assert data_module._TICKER_PRICE_WEIGHT == 1
        # Önbellek ömrü safety turundan (2.0 sn) kısa OLMAMALI: sembol başına
        # tur başına en fazla bir istek (ARCHITECTURE ağırlık hesabı).
        from src.core.config import settings as live_settings

        assert (
            data_module._TICKER_PRICE_TTL_SECONDS
            >= live_settings.scalper_safety_interval_seconds
        )


class TestExitsMarketDataOutage:
    """Safety turu 2 sn'de bir döner; host geneli bir banda her tur her sembol
    için WARNING basmak 180 sn'de yüzlerce satır demekti (uzun banda saatlerce).
    Tur başına TEK satır + kalan sembollerde trailing atlanır; TP/kapanış
    tespiti İMZALI yoldan geldiği için ATLANMAZ."""

    @staticmethod
    def _mgr(fetch):
        from src.strategies.scalper.exits import ExitManager

        mgr = ExitManager.__new__(ExitManager)
        mgr.cfg = SimpleNamespace(scalper_tf_entry="5m")
        mgr.kline_fetch = fetch
        mgr._market_data_down_reason = None
        mgr._positions = {}
        return mgr

    async def test_single_warning_per_round(self):
        calls: List[str] = []
        warnings: List[str] = []

        async def fetch(symbol, tf, limit):
            calls.append(symbol)
            raise MarketDataBanError("ban", host_of(MAINNET), time.time() + 60)

        mgr = self._mgr(fetch)
        mgr.logger = SimpleNamespace(
            warning=lambda msg, *a, **kw: warnings.append(msg),
            debug=lambda *a, **kw: None, error=lambda *a, **kw: None,
        )
        sp = SimpleNamespace()
        await mgr._update_trailing("AAAUSDT", sp)
        await mgr._update_trailing("BBBUSDT", sp)
        await mgr._update_trailing("CCCUSDT", sp)

        assert calls == ["AAAUSDT"], "kesinti bilindiği hâlde tekrar denendi"
        assert len(warnings) == 1

    async def test_flag_resets_each_round(self):
        async def fetch(symbol, tf, limit):  # pragma: no cover - çağrılmaz
            raise AssertionError

        mgr = self._mgr(fetch)
        mgr._market_data_down_reason = "önceki tur"
        await mgr.step()          # _positions boş: yalnız bayrak sıfırlanır
        assert mgr._market_data_down_reason is None


class TestBatchGuardMode:
    """Harness (backtest) modu: bütçe dolunca ÖLMEZ, bekler — ve canlıdan
    DAHA GEVŞEK bir bütçe/aralık kullanır (araştırma aracını yavaşlatmak
    kanıt üretmeyi yavaşlatır; düşmanca inceleme bulgusu)."""

    async def test_batch_mode_waits_instead_of_raising(self, monkeypatch):
        slept: List[float] = []
        real_sleep = asyncio.sleep

        async def fake_sleep(seconds):
            slept.append(seconds)
            await real_sleep(0)

        state = _fill_window(
            host_of(MAINNET), data_module._BATCH_WEIGHT_BUDGET_PER_MINUTE
        )

        async def advancing_sleep(seconds):
            # Sahte uyku GERÇEK saati ilerletmez; pencereyi biz yaşlandırırız
            # (aksi halde üretim döngüsü gerçek 60 sn boyunca dönerdi).
            await fake_sleep(seconds)
            state.window = deque((ts - seconds, w) for ts, w in state.window)

        monkeypatch.setattr(data_module, "_sleep", advancing_sleep)

        await MarketDataGuard.acquire(MAINNET, 10, data_module._GUARD_MODE_BATCH)

        assert any(s > 1.0 for s in slept), f"batch modu beklemedi: {slept}"
        # Eski girdi pencereden düştü, istek geçti.
        assert state.window_weight == 10

    async def test_batch_budget_is_looser_than_live(self):
        """Canlı bütçenin TAM SINIRINDA batch modu geçer, live reddeder:
        8 sembol × 30 günlük bir çekim (≈656 ağırlık) artık pencere
        beklemeleriyle ~3× uzamaz."""
        _fill_window(
            host_of(MAINNET), data_module._MARKET_DATA_WEIGHT_BUDGET_PER_MINUTE
        )
        await MarketDataGuard.acquire(MAINNET, 10, data_module._GUARD_MODE_BATCH)

        _fill_window(
            host_of(TESTNET), data_module._MARKET_DATA_WEIGHT_BUDGET_PER_MINUTE
        )
        with pytest.raises(data_module.MarketDataBudgetError):
            await MarketDataGuard.acquire(TESTNET, 10)

    async def test_batch_spacing_is_shorter(self, monkeypatch):
        """Sayfa başına 0.15 sn, yüzlerce sayfada dakikalar eder; harness tek
        tüketicidir, burst riski yoktur. 429/418 koruması moddan bağımsızdır."""
        slept: List[float] = []
        real_sleep = asyncio.sleep

        async def fake_sleep(seconds):
            slept.append(seconds)
            await real_sleep(0)

        monkeypatch.setattr(data_module, "_sleep", fake_sleep)
        await MarketDataGuard.acquire(MAINNET, 10, data_module._GUARD_MODE_BATCH)
        await MarketDataGuard.acquire(MAINNET, 10, data_module._GUARD_MODE_BATCH)

        assert slept and slept[0] <= data_module._BATCH_MIN_REQUEST_SPACING_SECONDS
        assert (
            data_module._BATCH_MIN_REQUEST_SPACING_SECONDS
            < data_module._MIN_REQUEST_SPACING_SECONDS
        )

    async def test_batch_mode_still_respects_ban(self):
        """Kesici moddan BAĞIMSIZDIR: harness de ban sırasında istek atmaz."""
        MarketDataGuard.trip(host_of(MAINNET), "banned", 120.0)
        with pytest.raises(MarketDataBanError):
            await MarketDataGuard.acquire(
                MAINNET, 10, data_module._GUARD_MODE_BATCH
            )

    def test_backtest_uses_batch_mode(self):
        """8 sembol × 30 gün ≈ 656 ağırlık: "live" modda koşu ortada ölürdü."""
        import inspect

        from src.strategies.scalper import backtest as backtest_module

        src = inspect.getsource(backtest_module.run_backtest)
        assert 'guard_mode="batch"' in src

    def test_live_engine_keeps_live_mode(self):
        fetcher = KlineFetcher(base_url=MAINNET)
        assert fetcher.guard_mode == "live"
        assert KlineFetcher(base_url=MAINNET, guard_mode="batch").guard_mode == "batch"


class TestExternalSignalPath:
    """`/tv-signal` yolu (engine.external_signal) piyasa verisi kesikken
    HTTP 500'e düşmemeli: TradingView 2xx olmayan yanıtta alarmı TEKRAR
    gönderir ve her tekrar yine 500 üretirdi (sağlama oyu da boşa giderdi)."""

    async def test_market_data_ban_returns_structured_rejection(self):
        engine = ScalperEngine.__new__(ScalperEngine)
        engine.running = True
        engine.cfg = SimpleNamespace(scalper_tv_symbol_allowlist="")
        engine.exits = SimpleNamespace(tracked_symbols=lambda: set())
        engine.executor = SimpleNamespace(pending_symbols=lambda: set())
        engine._entries_ready = lambda: True
        warnings: List[str] = []
        engine.logger = SimpleNamespace(
            info=lambda *a, **kw: None,
            warning=lambda msg, *a, **kw: warnings.append(msg),
        )

        async def boom(symbol, strategies):
            raise MarketDataBanError("ban", host_of(MAINNET), time.time() + 60)

        engine._evaluate_symbol = boom
        result = await engine.external_signal("BTCUSDT", Direction.LONG)

        assert result["accepted"] is False
        assert "piyasa verisi" in result["reason"].lower()
        assert any("piyasa verisi yok" in w for w in warnings)

    async def test_symbol_scoped_error_also_returns_structured_rejection(self):
        """İkinci tur bulgusu: `MarketDataRequestError` `MarketDataUnavailable`
        DEĞİLDİR, yani eski `except` dalı onu YAKALAMIYORDU → /tv-signal HTTP
        500. Ayrı market-data host'unda bu senaryo gerçekçidir: işlem host'unda
        olup veri host'unda olmayan bir sembol için TV alarmı gelir ve her
        tekrar yine 500 üretirdi."""
        engine = ScalperEngine.__new__(ScalperEngine)
        engine.running = True
        engine.cfg = SimpleNamespace(scalper_tv_symbol_allowlist="")
        engine.exits = SimpleNamespace(tracked_symbols=lambda: set())
        engine.executor = SimpleNamespace(pending_symbols=lambda: set())
        engine._entries_ready = lambda: True
        warnings: List[str] = []
        engine.logger = SimpleNamespace(
            info=lambda *a, **kw: None,
            warning=lambda msg, *a, **kw: warnings.append(msg),
        )

        async def boom(symbol, strategies):
            raise data_module.MarketDataRequestError(
                "HTTP 400 (code=-1121) fapi.binance.com: Invalid symbol."
            )

        engine._evaluate_symbol = boom
        result = await engine.external_signal("XYZUSDT", Direction.LONG)

        assert result["accepted"] is False
        assert "bulunamadı" in result["reason"]
        assert any("tanımıyor" in w for w in warnings)


# ---------------------------------------------------------------------------
# 6) Hata KAPSAMI: host geneli mi, sembol mü? (ikinci tur düşmanca inceleme)
# ---------------------------------------------------------------------------

def _error_response(status: int, code: int = -1000, msg: str = "blocked",
                    headers: Optional[Dict[str, str]] = None) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        json={"code": code, "msg": msg},
        headers=headers or {},
        request=httpx.Request("GET", "https://example.invalid/fapi/v1/klines"),
    )


DEPLOY_BAN_PATTERN = re.compile(r"HTTP 418|banned")


class TestHostScopedErrors:
    """Bulgu (HIGH): 401/403 (kimlik/WAF) ve 451 (coğrafi engel) HOST
    GENELİDİR ama D17'de SEMBOL kapsamlı `MarketDataRequestError` sayılıyordu:
    12 sembolün 12'si de aynı yanıtı alıyor, tur kesilmiyor, kesici
    kurulmuyor ve deploy ban kilidi kör kalıyordu."""

    @pytest.mark.parametrize("status", [401, 403, 451])
    async def test_host_wide_4xx_is_host_scoped(self, status):
        fetcher = _fetcher(MAINNET, [_error_response(status)])
        with pytest.raises(data_module.MarketDataHostError):
            await fetcher._fetch("BTCUSDT", "5m", 250, None)
        # Tekrar YOK (tek istek) ve kesici KURULDU.
        assert len(fetcher._client.calls) == 1
        snap = MarketDataGuard.snapshot(MAINNET)
        assert snap["banned"] is True
        # ...ama GERÇEK ban değil: deploy kilidi tetiklenmemeli.
        assert snap["hard_ban"] is False

    def test_host_error_is_market_data_unavailable(self):
        """`_scan_tick` / `exits` / `/tv-signal` bu tipi zaten yakalıyor."""
        assert issubclass(
            data_module.MarketDataHostError, data_module.MarketDataUnavailable
        )

    @pytest.mark.parametrize("status", [400, 404])
    async def test_symbol_scoped_4xx_stays_symbol_scoped(self, status):
        fetcher = _fetcher(MAINNET, [_error_response(status, code=-1121,
                                                     msg="Invalid symbol.")])
        with pytest.raises(data_module.MarketDataRequestError):
            await fetcher._fetch("XYZUSDT", "5m", 250, None)
        assert len(fetcher._client.calls) == 1
        # Tek bozuk sembol yüzünden HOST kesicisi kurulmaz.
        assert MarketDataGuard.snapshot(MAINNET)["banned"] is False

    async def test_host_block_honours_retry_after(self):
        fetcher = _fetcher(
            MAINNET, [_error_response(403, headers={"Retry-After": "120"})]
        )
        with pytest.raises(data_module.MarketDataHostError):
            await fetcher._fetch("BTCUSDT", "5m", 250, None)
        blocked_until = MarketDataGuard.blocked_until(host_of(MAINNET))
        assert time.time() + 100 < blocked_until <= time.time() + 121

    async def test_host_block_log_does_not_match_deploy_guard(self, monkeypatch):
        """403 bir BAN değildir: deploy'u 15 dk kilitlememeli."""
        lines: List[str] = []
        fetcher = _fetcher(MAINNET, [_error_response(403, msg="Forbidden")])
        fetcher.logger = SimpleNamespace(
            critical=lambda msg, *a, **kw: lines.append(msg),
            warning=lambda msg, *a, **kw: lines.append(msg),
            error=lambda msg, *a, **kw: lines.append(msg),
        )
        with pytest.raises(data_module.MarketDataHostError):
            await fetcher._fetch("BTCUSDT", "5m", 250, None)
        assert lines and not any(DEPLOY_BAN_PATTERN.search(x) for x in lines)

    async def test_exhausted_5xx_becomes_host_error(self, monkeypatch):
        """3 deneme sonunda hâlâ 5xx ise sorun sembolde değil host'tadır:
        kalan 11 sembol için 33 istek daha atmanın anlamı yok."""
        real_sleep = asyncio.sleep
        monkeypatch.setattr(data_module, "_sleep", lambda s: real_sleep(0))
        error = _error_response(503, code=-1001, msg="internal")
        fetcher = _fetcher(MAINNET, [error, error, error])
        with pytest.raises(data_module.MarketDataHostError):
            await fetcher._fetch("BTCUSDT", "5m", 250, None)
        assert len(fetcher._client.calls) == 3
        # Geçici hata: kesici KURULMAZ (bir sonraki tur yeniden dener).
        assert MarketDataGuard.snapshot(MAINNET)["banned"] is False

    async def test_scan_round_is_cut_on_host_wide_4xx(self):
        """403 gören bir tur, 12 sembolün hepsini denemek yerine kesilir."""
        engine = ScalperEngine.__new__(ScalperEngine)
        engine.cfg = SimpleNamespace(
            scalper_symbol_allowlist="AAAUSDT,BBBUSDT,CCCUSDT",
            scalper_strategies="C", scalper_max_positions=3,
        )
        engine.client = SimpleNamespace(get_all_positions=lambda: _async_value([]))
        engine.exits = SimpleNamespace(tracked_symbols=lambda: set())
        engine.executor = SimpleNamespace(pending_symbols=lambda: set())
        engine._universe = []
        engine._scan_open_symbols = set()
        evaluated: List[str] = []
        engine.logger = SimpleNamespace(
            warning=lambda *a, **kw: None, error=lambda *a, **kw: None,
            info=lambda *a, **kw: None,
        )
        engine._entries_ready = lambda: True
        engine._executor_entry_blocked = lambda symbol: False

        async def boom(symbol, strategies):
            evaluated.append(symbol)
            raise data_module.MarketDataHostError("403", host_of(MAINNET))

        engine._evaluate_symbol = boom
        await engine._scan_tick()
        assert evaluated == ["AAAUSDT"]
        assert engine._scan_status() == "degraded:market_data"


class TestSoftThrottleSemantics:
    """Bulgu (MED): TEK bir 429, ayar BOŞKEN (kline'lar işlem host'undan)
    90-180 sn'lik küresel bir kesici + 15 dk'lık deploy kilidi doğuruyordu.
    429 tek başına BAN değildir."""

    async def test_plain_429_is_short_and_not_a_ban(self):
        fetcher = _fetcher(MAINNET, [_error_response(429, code=-1015,
                                                     msg="Too many requests")])
        with pytest.raises(MarketDataBanError):
            await fetcher._fetch("BTCUSDT", "5m", 250, None)
        blocked_until = MarketDataGuard.blocked_until(host_of(MAINNET))
        assert blocked_until <= time.time() + data_module._BAN_DEFAULT_SECONDS_SOFT + 1
        assert data_module._BAN_DEFAULT_SECONDS_SOFT == 30.0
        assert MarketDataGuard.snapshot(MAINNET)["hard_ban"] is False

    async def test_plain_429_log_does_not_lock_deploy(self):
        lines: List[str] = []
        fetcher = _fetcher(MAINNET, [_error_response(429, code=-1015,
                                                     msg="Too many requests")])
        fetcher.logger = SimpleNamespace(
            critical=lambda msg, *a, **kw: lines.append(msg),
            warning=lambda msg, *a, **kw: lines.append(msg),
            error=lambda msg, *a, **kw: lines.append(msg),
        )
        with pytest.raises(MarketDataBanError):
            await fetcher._fetch("BTCUSDT", "5m", 250, None)
        assert lines and not any(DEPLOY_BAN_PATTERN.search(x) for x in lines)

    async def test_ongoing_soft_breaker_log_does_not_lock_deploy(self, monkeypatch):
        warnings: List[str] = []
        monkeypatch.setattr(
            data_module.app_logger, "warning",
            lambda msg, *a, **kw: warnings.append(msg),
        )
        MarketDataGuard.trip(host_of(MAINNET), "slow down", 30.0, hard=False)
        with pytest.raises(MarketDataBanError):
            MarketDataGuard.ensure_allowed(host_of(MAINNET))
        assert warnings and not DEPLOY_BAN_PATTERN.search(warnings[0])

    async def test_429_uses_retry_after_header(self):
        fetcher = _fetcher(
            MAINNET,
            [_error_response(429, code=-1015, msg="slow",
                             headers={"Retry-After": "12"})],
        )
        with pytest.raises(MarketDataBanError):
            await fetcher._fetch("BTCUSDT", "5m", 250, None)
        blocked_until = MarketDataGuard.blocked_until(host_of(MAINNET))
        assert time.time() + 5 < blocked_until <= time.time() + 13

    async def test_429_over_ip_weight_waits_for_window(self):
        """X-MBX-USED-WEIGHT-1M sınırın üstündeyse 1 dakikalık pencerenin
        dolması gerekir — 30 sn yetmez."""
        fetcher = _fetcher(
            MAINNET,
            [_error_response(
                429, code=-1015, msg="slow",
                headers={"X-MBX-USED-WEIGHT-1M": str(
                    data_module._IP_WEIGHT_LIMIT_PER_MINUTE + 10)},
            )],
        )
        with pytest.raises(MarketDataBanError):
            await fetcher._fetch("BTCUSDT", "5m", 250, None)
        blocked_until = MarketDataGuard.blocked_until(host_of(MAINNET))
        assert time.time() + 45 < blocked_until <= time.time() + 61

    async def test_real_ban_still_locks_deploy(self):
        """418 / -1003 / "banned until" DEĞİŞMEDİ: hâlâ hard ban."""
        lines: List[str] = []
        fetcher = _fetcher(MAINNET, [_ban_response()])
        fetcher.logger = SimpleNamespace(
            critical=lambda msg, *a, **kw: lines.append(msg),
            warning=lambda msg, *a, **kw: lines.append(msg),
            error=lambda msg, *a, **kw: lines.append(msg),
        )
        with pytest.raises(MarketDataBanError):
            await fetcher._fetch("BTCUSDT", "5m", 250, None)
        assert any(DEPLOY_BAN_PATTERN.search(x) for x in lines)
        assert MarketDataGuard.snapshot(MAINNET)["hard_ban"] is True

    async def test_soft_then_hard_stays_hard(self):
        host = host_of(MAINNET)
        MarketDataGuard.trip(host, "slow", 30.0, hard=False)
        assert MarketDataGuard.is_hard_ban(host) is False
        MarketDataGuard.trip(host, "banned", 180.0, hard=True)
        assert MarketDataGuard.is_hard_ban(host) is True


class TestScanDegradedStatus:
    """Bulgu: piyasa verisi kesintisiyle KESİLEN tur "başarılı" sayılıyordu
    (success_count ↑, consecutive_errors sıfır, last_scan_at tazeleniyor,
    sağlık YEŞİL) — operatörün tek izi bir log satırıydı."""

    @staticmethod
    def _engine() -> Any:
        engine = ScalperEngine.__new__(ScalperEngine)
        engine.logger = SimpleNamespace(
            warning=lambda *a, **kw: None, error=lambda *a, **kw: None,
            info=lambda *a, **kw: None,
        )
        engine._scan_degraded_reason = None
        engine._scan_degraded_kind = "market_data"
        engine._scan_degraded_at = None
        engine._scan_degraded_count = 0
        engine._scan_degraded_log_at = 0.0
        return engine

    def test_status_is_ok_by_default(self):
        engine = self._engine()
        assert engine._scan_status() == "ok"
        assert engine._scan_degraded_snapshot()["scan_degraded_count"] == 0

    def test_degraded_marks_status_and_counter(self):
        engine = self._engine()
        engine._mark_scan_degraded("market_data: ban")
        snap = engine._scan_degraded_snapshot()
        assert snap["scan_status"] == "degraded:market_data"
        assert snap["scan_degraded_count"] == 1
        assert snap["scan_degraded_at"] is not None

    def test_degraded_warning_is_rate_limited(self):
        engine = self._engine()
        warnings: List[str] = []
        engine.logger = SimpleNamespace(
            warning=lambda msg, *a, **kw: warnings.append(msg),
            error=lambda *a, **kw: None, info=lambda *a, **kw: None,
        )
        for _ in range(5):
            engine._mark_scan_degraded("market_data: ban")
        assert len(warnings) == 1
        assert engine._scan_degraded_count == 5

    async def test_degraded_round_does_not_count_as_success(self):
        """`_loop`'un muhasebesi: kesilen tur success_count'u ARTIRMAZ ve
        `last_scan_at`'ı tazelemez; freshness (watchdog) BİLİNÇLİ tazelenir."""
        engine = ScalperEngine.__new__(ScalperEngine)
        engine.cfg = SimpleNamespace(
            scalper_symbol_allowlist="AAAUSDT", scalper_strategies="C",
            scalper_max_positions=3, scalper_scan_interval_seconds=30,
        )
        engine.client = SimpleNamespace(get_all_positions=lambda: _async_value([]))
        engine.exits = SimpleNamespace(tracked_symbols=lambda: set())
        engine.executor = SimpleNamespace(pending_symbols=lambda: set())
        engine.logger = SimpleNamespace(
            warning=lambda *a, **kw: None, error=lambda *a, **kw: None,
            info=lambda *a, **kw: None,
        )
        engine._universe = []
        engine._scan_open_symbols = set()
        engine._entries_ready = lambda: True
        engine._executor_entry_blocked = lambda symbol: False
        engine._scan_success_count = 7
        engine._scan_consecutive_errors = 2
        engine._last_scan_at = "önceki"
        engine._scan_degraded_reason = None
        engine._scan_degraded_count = 0
        engine._scan_degraded_log_at = 0.0
        engine._scan_last_success_monotonic = None

        async def boom(symbol, strategies):
            raise MarketDataBanError("ban", host_of(MAINNET), time.time() + 60)

        engine._evaluate_symbol = boom

        # _loop gövdesinin muhasebe kısmı (döngüsüz):
        await engine._scan_tick()
        engine._scan_last_success_monotonic = time.monotonic()
        if not engine._scan_degraded_reason:
            engine._scan_consecutive_errors = 0
            engine._scan_success_count += 1
            engine._last_scan_at = "yeni"

        assert engine._scan_success_count == 7, "kesilen tur başarı sayıldı"
        assert engine._scan_consecutive_errors == 2, "hata serisi silindi"
        assert engine._last_scan_at == "önceki"
        # Watchdog freshness'ı BİLİNÇLİ tazelenir (ban ortasında restart =
        # 2026-08-14 felaket yolu).
        assert engine._scan_last_success_monotonic is not None


class TestCacheTtlProfile:
    """Bulgu: `_TTL_BY_INTERVAL`'de `1m` yoktu → CANLI profil
    (`SCALPER_TF_ENTRY=1m`) `_DEFAULT_TTL`=60 sn'ye düşüyordu: trailing ve
    giriş TAM BİR MUM bayat veriyle karar veriyordu."""

    def test_one_minute_ttl_exists_and_is_short(self):
        fetcher = KlineFetcher(base_url=MAINNET)
        assert fetcher._ttl_for("1m") <= 5.0
        assert fetcher._ttl_for("1m") < data_module._DEFAULT_TTL

    def test_live_profile_timeframes_all_have_explicit_ttl(self):
        """Canlı profil 1m/5m/15m; hiçbiri varsayılana düşmemeli."""
        for tf in ("1m", "5m", "15m"):
            assert tf in data_module._TTL_BY_INTERVAL

    def test_ttl_is_a_fraction_of_the_candle_period(self):
        periods = {"1m": 60.0, "5m": 300.0, "15m": 900.0}
        for tf, period in periods.items():
            assert data_module._TTL_BY_INTERVAL[tf] <= period * 0.10


class TestAllowlistHygiene:
    """Bulgu: `testnet.binance.vision` Binance SPOT testnet'idir; `/fapi/...`
    yollarını hiç sunmaz. Allowlist'te kalırsa ayar kabul edilir ama bot her
    kline isteğinde 404 alır ve operatör "URL geçerli" diye çalıştığını sanır."""

    def test_spot_testnet_is_not_allowed(self):
        from src.core.config import MARKET_DATA_ALLOWED_HOSTS

        assert "testnet.binance.vision" not in MARKET_DATA_ALLOWED_HOSTS
        with pytest.raises(ValueError, match="bilinmeyen host"):
            _settings(scalper_market_data_base_url="https://testnet.binance.vision")

    def test_futures_hosts_still_allowed(self):
        for good in (MAINNET, TESTNET, "https://demo-fapi.binance.com"):
            assert _settings(scalper_market_data_base_url=good)


class TestOpsScripts:
    """İşletme betiklerinin iki sessiz kusuru (düşmanca inceleme)."""

    @staticmethod
    def _read(rel: str) -> str:
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        return (root / rel).read_text(encoding="utf-8")

    def test_ring_env_diff_masks_bind_ip(self):
        """`BINANCE_BIND_IP` sunucunun Binance'e çıktığı IP'dir; ban/ağırlık
        muhasebesi IP başınadır — diff çıktısında değeri görünmemeli."""
        src = self._read("scripts/ring_env_diff.sh")
        assert "*BIND_IP*" in src
        mask_line = [ln for ln in src.splitlines() if "*SECRET*" in ln]
        assert mask_line and "BIND_IP" in mask_line[0]

    def test_deploy_ban_window_uses_local_time(self):
        """`logs/bot.log` damgaları YEREL saattir (loguru {time}); kesim
        noktası `date -u` ile üretilirse pencere TZ ofseti kadar kayar."""
        src = self._read("scripts/server_deploy.sh")
        assert "date -u -d '15 minutes ago'" not in src
        assert "date -d '15 minutes ago'" in src
        assert "BAN_SINCE" in src
