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
import time
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


# ---------------------------------------------------------------------------
# 4) Ağırlık / oran sınırlayıcı / ban semantiği
# ---------------------------------------------------------------------------

class _FakeHttpClient:
    """httpx.AsyncClient yerine geçer; sıradaki yanıtı döner ve çağrılan
    URL'leri kaydeder."""

    def __init__(self, responses: List[httpx.Response]):
        self._responses = list(responses)
        self.calls: List[str] = []

    async def get(self, url: str, params: Optional[Dict[str, Any]] = None):
        self.calls.append(url)
        if not self._responses:
            raise AssertionError(f"beklenmeyen ek istek: {url}")
        return self._responses.pop(0)

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
        state = MarketDataGuard._state(host_of(MAINNET))
        state.window_start = time.monotonic()
        state.window_weight = data_module._MARKET_DATA_WEIGHT_BUDGET_PER_MINUTE

        started = time.monotonic()
        with pytest.raises(data_module.MarketDataBudgetError):
            await MarketDataGuard.acquire(MAINNET, 2)
        assert time.monotonic() - started < 1.0, "bütçe dalı olay döngüsünü bekletti"
        # Bütçe TÜKETİLMEDİ (istek gitmedi) ve pencere yapay olarak sıfırlanmadı.
        assert state.window_weight == data_module._MARKET_DATA_WEIGHT_BUDGET_PER_MINUTE

    async def test_weight_budget_recovers_after_window(self):
        """Pencere dolunca sayaç sıfırlanır — kalıcı susma yok."""
        state = MarketDataGuard._state(host_of(MAINNET))
        state.window_start = time.monotonic() - data_module._WEIGHT_WINDOW_SECONDS - 1
        state.window_weight = data_module._MARKET_DATA_WEIGHT_BUDGET_PER_MINUTE

        await MarketDataGuard.acquire(MAINNET, 2)
        assert state.window_weight == 2

    async def test_budget_error_is_not_retried_by_fetch(self):
        """Bütçe hatası httpx retry döngüsüne DÜŞMEZ (ayrı tip) — istek ağa
        hiç çıkmaz."""
        state = MarketDataGuard._state(host_of(MAINNET))
        state.window_start = time.monotonic()
        state.window_weight = data_module._MARKET_DATA_WEIGHT_BUDGET_PER_MINUTE
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
    """D17 düşmanca inceleme (HIGH): chandelier MUTLAK bir fiyat üretir ve bu
    değer `pm.replace_stop_loss` ile İŞLEM borsasına emir olarak gider. Ayrı
    market-data host'unda seviye YABANCI bir defterin fiyat uzayındadır; baz
    farkı k×ATR'yi aşarsa Binance -2021 verir ve `position_manager` bunu
    "piyasa stop'u geçti" sayıp pozisyonu ACİL KAPATIR (kârlı koşucu piyasa
    emriyle kapanır). Düzeltme girişteki desenin aynısıdır
    (`executor._delay_adjusted_stop`): girişte ölçülen fark kadar ÖTELE.
    """

    @staticmethod
    def _exits(market_url: str) -> Any:
        from src.strategies.scalper.exits import ExitManager

        mgr = ExitManager.__new__(ExitManager)   # __init__ atlanır (ağ yok)
        mgr.cfg = SimpleNamespace(
            scalper_market_data_base_url=market_url,
            binance_base_url=TESTNET,
        )
        return mgr

    @staticmethod
    def _position(signal_price: float, fill_price: float) -> Any:
        return SimpleNamespace(
            signal=SimpleNamespace(entry_price=signal_price),
            position=SimpleNamespace(entry_price=fill_price),
        )

    def test_same_host_is_noop(self):
        """Varsayılan (tek host): bugünkü davranış BİREBİR korunur."""
        mgr = self._exits("")
        sp = self._position(100.0, 100.5)
        assert mgr._to_trading_price_space(sp, 99.0) == 99.0

    def test_separate_host_shifts_by_entry_basis(self):
        mgr = self._exits(MAINNET)
        # Sinyal (mainnet mumu) 100.0, gerçek dolum (testnet) 100.4 → baz +0.4
        sp = self._position(100.0, 100.4)
        assert mgr._to_trading_price_space(sp, 99.0) == pytest.approx(99.4)

    def test_shift_preserves_distance(self):
        """Öteleme mesafeyi (birim riski) korur — ölçek değiştirmez."""
        mgr = self._exits(MAINNET)
        sp = self._position(100.0, 100.4)
        moved = mgr._to_trading_price_space(sp, 99.0)
        assert (100.4 - moved) == pytest.approx(100.0 - 99.0)

    def test_missing_prices_fall_back_to_raw(self):
        mgr = self._exits(MAINNET)
        assert mgr._to_trading_price_space(self._position(0.0, 100.0), 99.0) == 99.0
        assert mgr._to_trading_price_space(self._position(100.0, 0.0), 99.0) == 99.0
        assert mgr._to_trading_price_space(self._position(100.0, 100.0), 0.0) == 0.0

    def test_negative_result_falls_back_to_raw(self):
        mgr = self._exits(MAINNET)
        sp = self._position(1000.0, 1.0)   # absürt baz
        assert mgr._to_trading_price_space(sp, 5.0) == 5.0

    def test_missing_cfg_fields_are_treated_as_same_host(self):
        """Eski test çiftleri (SimpleNamespace) alanı hiç tanımlamayabilir."""
        from src.strategies.scalper.exits import ExitManager

        mgr = ExitManager.__new__(ExitManager)
        mgr.cfg = SimpleNamespace()
        assert mgr._market_data_is_separate() is False


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
    """Harness (backtest) modu: bütçe dolunca ÖLMEZ, bekler."""

    async def test_batch_mode_waits_instead_of_raising(self, monkeypatch):
        slept: List[float] = []
        real_sleep = asyncio.sleep

        async def fake_sleep(seconds):
            slept.append(seconds)
            await real_sleep(0)

        monkeypatch.setattr(data_module, "_sleep", fake_sleep)
        state = MarketDataGuard._state(host_of(MAINNET))
        state.window_start = time.monotonic()
        state.window_weight = data_module._MARKET_DATA_WEIGHT_BUDGET_PER_MINUTE

        await MarketDataGuard.acquire(MAINNET, 10, data_module._GUARD_MODE_BATCH)

        assert any(s > 1.0 for s in slept), f"batch modu beklemedi: {slept}"
        assert state.window_weight == 10  # pencere sıfırlandı, istek geçti

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
