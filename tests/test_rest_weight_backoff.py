"""D22 (daraltılmış) — REST ağırlık TELEMETRİSİ + durum netliği + pano önbelleği.

Kusur (2026-08-23 canlı log): `X-MBX-USED-WEIGHT-1M` ≥ 1800 için YALNIZ bir
WARNING vardı — 276 satır/gün, tepe 4059/dk. Sayaç IP GENELİDİR (aynı çıkış
IP'sindeki başka süreçler de tüketir), yani bot bütçeyi görüyor ama ne
ölçüyor ne de raporluyordu.

DARALTMA (12-ajanlık düşmanca inceleme): geri çekilme mekanizması KALIR ama
**VARSAYILAN KAPALIDIR** (`BINANCE_WEIGHT_SOFT_LIMIT=0`, `HARD_LIMIT=0`).
Gerekçe ölçümdür: testnet'te aynı başlığın günlük MEDYANI 2373 (>2000)
ölçüldü — 2000/2300 ile açık olsaydı tarama turu KALICI olarak durur, bot
hiç işlem açmazdı. Telemetri eşiklerden BAĞIMSIZ çalışır ve eşikler ancak
o telemetriyle ölçüldükten sonra .env'den açılır.

Sözleşme:
  * varsayılan: geri çekilme KAPALI, ölçüm AÇIK,
  * açıkken: kritik OLMAYAN istekler (pano, evren taraması, teşhis) takvim
    dakikası dolana kadar gönderilmez; emir/SL-TP/positionRisk/kapanış
    doğrulaması HER ZAMAN geçer,
  * pencere ASLA `max()` ile kilitlenmez (ileri saat sıçraması botu
    süresiz durdurmamalı),
  * `/scalper/status.rest_weight` = {last, last_at, max_1m (DAKİKA DİLİMLİ),
    peak_at, soft_backoffs, hard_backoffs},
  * `/api/status` ve `/scalper/status` 5 sn sunucu-tarafı önbellek + `as_of`;
    durum DEĞİŞTİREN uçlar önbelleği düşürür; sorgu anahtarın parçasıdır,
  * `/scalper/status.entries_blocked_by` + `market_gate.stale_reason`.
"""

import inspect
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import src.main as main_module
from src.core import config as config_module
from src.trading.binance_client_improved import (
    ImprovedBinanceClient,
    RestWeightBackoff,
)


@pytest.fixture(autouse=True)
def _reset_client_state():
    def _reset():
        ImprovedBinanceClient.reset_weight_state()
        ImprovedBinanceClient._account_cache = None
        ImprovedBinanceClient._account_cache_ts = 0.0
        ImprovedBinanceClient._account_cache_lock = None
        ImprovedBinanceClient._price_cache = {}
        ImprovedBinanceClient._rest_blocked_until = 0.0
        main_module._reset_status_caches()

    _reset()
    yield
    _reset()


@pytest.fixture
def enabled_limits(monkeypatch):
    """Geri çekilmeyi AÇ (varsayılan kapalı) — sözleşmeyi test edebilmek için."""
    monkeypatch.setattr(
        config_module.settings, "binance_weight_soft_limit", 2000, raising=False
    )
    monkeypatch.setattr(
        config_module.settings, "binance_weight_hard_limit", 2300, raising=False
    )
    return (2000, 2300)


def _bare_client() -> ImprovedBinanceClient:
    client = object.__new__(ImprovedBinanceClient)
    client.logger = MagicMock()
    client.base_url = "https://testnet.invalid"
    return client


class _Req:
    """`Request` yerine geçen minimal sorgu taşıyıcısı."""

    def __init__(self, items):
        self.query_params = SimpleNamespace(multi_items=lambda: list(items))


# ---------------------------------------------------------------------------
# 0) VARSAYILAN KAPALI (D22 daraltması) — kanıt: testnet medyanı 2373
# ---------------------------------------------------------------------------

class TestDisabledByDefault:
    def test_config_defaults_are_zero(self):
        from src.core.config import Settings

        fields = Settings.model_fields
        assert fields["binance_weight_soft_limit"].default == 0
        assert fields["binance_weight_hard_limit"].default == 0

    def test_env_example_ships_disabled(self):
        from pathlib import Path

        env = Path("env.example").read_text(encoding="utf-8")
        assert "BINANCE_WEIGHT_SOFT_LIMIT=0" in env
        assert "BINANCE_WEIGHT_HARD_LIMIT=0" in env

    def test_measured_median_would_have_stopped_the_scanner(self):
        """2373 (testnet günlük medyanı) eski 2000 eşiğinin ÜSTÜNDEDİR."""
        ImprovedBinanceClient._note_used_weight(2373)
        # Varsayılan (kapalı) ile bot çalışmaya devam eder:
        assert ImprovedBinanceClient.weight_backoff_level() == "off"

    def test_disabled_limits_never_block(self):
        client = _bare_client()
        ImprovedBinanceClient._note_used_weight(4059)
        assert ImprovedBinanceClient.weight_backoff_level() == "off"
        client._weight_gate("/fapi/v2/account", "background")  # istisna YOK

    def test_snapshot_reports_disabled(self):
        snap = ImprovedBinanceClient.rest_weight_snapshot()
        assert (snap["soft_limit"], snap["hard_limit"]) == (0.0, 0.0)
        assert snap["enabled"] is False


# ---------------------------------------------------------------------------
# 1) Telemetri: eşiklerden BAĞIMSIZ, dakika dilimli tepe
# ---------------------------------------------------------------------------

class TestTelemetry:
    def test_last_and_peak_are_recorded_even_when_disabled(self):
        ImprovedBinanceClient._note_used_weight(1200)
        ImprovedBinanceClient._note_used_weight(4059)
        ImprovedBinanceClient._note_used_weight(300)
        snap = ImprovedBinanceClient.rest_weight_snapshot()
        assert (snap["last"], snap["max_1m"]) == (300, 4059)
        assert snap["last_at"] and snap["peak_at"]

    def test_peak_resets_with_the_calendar_minute(self, monkeypatch):
        """`max_1m` DAKİKA DİLİMLİDİR — Binance sayacı da orada sıfırlanır.

        Süreç ömrü boyu tutulan bir tepe farklı dakikaları tek sayıya
        katlar ve RUNBOOK'un "max_1m > 3000 ise araştır" kuralını okunamaz
        kılardı.
        """
        base = 1_800_000_060.0          # tam dakika + 0 sn
        monkeypatch.setattr(time, "time", lambda: base + 5.0)
        ImprovedBinanceClient._note_used_weight(4059)
        assert ImprovedBinanceClient.rest_weight_snapshot()["max_1m"] == 4059

        monkeypatch.setattr(time, "time", lambda: base + 65.0)   # sonraki dakika
        ImprovedBinanceClient._note_used_weight(120)
        assert ImprovedBinanceClient.rest_weight_snapshot()["max_1m"] == 120

    def test_snapshot_contract(self):
        ImprovedBinanceClient._note_used_weight(10)
        snap = ImprovedBinanceClient.rest_weight_snapshot()
        assert set(snap) >= {
            "last", "last_at", "max_1m", "peak_at",
            "soft_backoffs", "hard_backoffs", "backoff",
        }


# ---------------------------------------------------------------------------
# 2) Pencere: takvim dakikası, `max()` KİLİDİ YOK
# ---------------------------------------------------------------------------

class TestBackoffWindow:
    def test_below_soft_limit_is_off(self, enabled_limits):
        ImprovedBinanceClient._note_used_weight(1500)
        assert ImprovedBinanceClient.weight_backoff_level() == "off"
        assert ImprovedBinanceClient.weight_backoff_active() is False

    def test_soft_limit_opens_soft_window(self, enabled_limits):
        ImprovedBinanceClient._note_used_weight(2100)
        assert ImprovedBinanceClient.weight_backoff_level() == "soft"

    def test_hard_limit_opens_hard_window(self, enabled_limits):
        ImprovedBinanceClient._note_used_weight(4059)
        assert ImprovedBinanceClient.weight_backoff_level() == "hard"

    def test_window_ends_with_the_calendar_minute(self, enabled_limits):
        """Binance 1M sayacı takvim dakikasında sıfırlanır — pencere de."""
        ImprovedBinanceClient._note_used_weight(2400)
        end = ImprovedBinanceClient._weight_hard_until
        assert end % 60 == 0
        assert 0 < end - time.time() <= 60.0

    def test_forward_clock_jump_does_not_latch_the_window(
        self, enabled_limits, monkeypatch
    ):
        """İleri saat sıçraması (NTP/VM suspend) botu SÜRESİZ durduramaz.

        Eski kod pencereyi `max(mevcut, yeni)` ile çiviliyordu: bir saat
        ileri sıçrama, saat düzeltildikten sonra saatlerce sürecek bir geri
        çekilme bırakırdı — geri çekilmenin koruyacağı 418'den pahalı.
        """
        base = 1_800_000_000.0
        monkeypatch.setattr(time, "time", lambda: base + 3600.0)   # ileri sıçrama
        ImprovedBinanceClient._note_used_weight(2400)
        assert ImprovedBinanceClient._weight_hard_until <= base + 3600.0 + 60.0

        monkeypatch.setattr(time, "time", lambda: base)            # saat düzeldi
        assert ImprovedBinanceClient.weight_backoff_level() == "off"
        assert ImprovedBinanceClient._weight_hard_until == 0.0

    def test_second_measurement_does_not_extend_the_window(
        self, enabled_limits, monkeypatch
    ):
        base = 1_800_000_000.0
        monkeypatch.setattr(time, "time", lambda: base + 10.0)
        ImprovedBinanceClient._note_used_weight(2400)
        first = ImprovedBinanceClient._weight_hard_until
        monkeypatch.setattr(time, "time", lambda: base + 50.0)
        ImprovedBinanceClient._note_used_weight(2400)
        assert ImprovedBinanceClient._weight_hard_until == first


# ---------------------------------------------------------------------------
# 3) Kapı: kritik geçer, kritik olmayan durur (AÇIKKEN)
# ---------------------------------------------------------------------------

class TestWeightGate:
    def test_critical_is_never_blocked(self, enabled_limits):
        client = _bare_client()
        ImprovedBinanceClient._note_used_weight(4059)
        client._weight_gate("/fapi/v1/order", "critical")  # istisna YOK

    def test_background_is_blocked_and_counted(self, enabled_limits):
        client = _bare_client()
        ImprovedBinanceClient._note_used_weight(2100)
        with pytest.raises(RestWeightBackoff):
            client._weight_gate("/fapi/v2/account", "background")
        snap = ImprovedBinanceClient.rest_weight_snapshot()
        assert (snap["soft_backoffs"], snap["hard_backoffs"]) == (1, 0)

    def test_hard_backoff_logs_critical_at_most_once_per_minute(
        self, enabled_limits
    ):
        client = _bare_client()
        ImprovedBinanceClient._note_used_weight(2400)
        for _ in range(5):
            with pytest.raises(RestWeightBackoff):
                client._weight_gate("/fapi/v2/account", "background")
        assert client.logger.critical.call_count == 1
        snap = ImprovedBinanceClient.rest_weight_snapshot()
        assert snap["hard_backoffs"] == 5

    async def test_request_layer_applies_the_gate(self, enabled_limits):
        client = _bare_client()
        client._ensure_rest_allowed = lambda endpoint: None
        ImprovedBinanceClient._note_used_weight(2100)
        with pytest.raises(RestWeightBackoff):
            await client._request_with_retry(
                "GET", "/fapi/v2/account", signed=True, priority="background"
            )


# ---------------------------------------------------------------------------
# 4) Önbellekten servis: bayat veri > 418 (AÇIKKEN)
# ---------------------------------------------------------------------------

class TestStaleCacheServing:
    async def test_account_is_served_stale_without_a_new_request(
        self, enabled_limits
    ):
        client = _bare_client()
        calls = []

        async def fake_request(method, endpoint, params=None, signed=False):
            calls.append(endpoint)
            return {"assets": [{"asset": "USDT", "availableBalance": "900.0",
                                "walletBalance": "1000.0"}], "positions": []}

        client._request_with_retry = fake_request
        assert await client.get_account_balance() == 900.0
        assert len(calls) == 1

        # Önbellek yaşlansın, sonra bütçe dolsun.
        ImprovedBinanceClient._account_cache_ts = time.monotonic() - 3600.0
        ImprovedBinanceClient._note_used_weight(2400)

        assert await client.get_account_balance(priority="background") == 900.0
        assert len(calls) == 1, "geri çekilme sırasında YENİ istek gitti"

    async def test_price_is_served_stale_without_a_new_request(
        self, enabled_limits
    ):
        client = _bare_client()
        calls = []

        async def fake_request(method, endpoint, params=None, signed=False):
            calls.append(endpoint)
            return {"price": "60000.0"}

        client._request_with_retry = fake_request
        assert await client.get_current_price("BTCUSDT") == 60000.0
        ImprovedBinanceClient._price_cache["BTCUSDT"] = (
            time.monotonic() - 3600.0, 60000.0
        )
        ImprovedBinanceClient._note_used_weight(2400)

        assert await client.get_current_price(
            "BTCUSDT", priority="background"
        ) == 60000.0
        assert len(calls) == 1

    async def test_critical_read_still_goes_to_the_network(self, enabled_limits):
        client = _bare_client()
        calls = []

        async def fake_request(method, endpoint, params=None, signed=False):
            calls.append(endpoint)
            return {"assets": [], "positions": []}

        client._request_with_retry = fake_request
        ImprovedBinanceClient._note_used_weight(4059)
        await client._get_account(force_fresh=True)
        assert calls == ["/fapi/v2/account"]


# ---------------------------------------------------------------------------
# 5) Ağırlık uyarısı dakikada en fazla bir satır
# ---------------------------------------------------------------------------

class TestWarningRateLimit:
    async def test_warning_is_emitted_once_per_minute(self):
        import httpx

        client = _bare_client()
        client._ensure_rest_allowed = lambda endpoint: None
        client.max_retries = 1
        client.recv_window = 5000

        class _Http:
            async def get(self, url, headers=None):
                return httpx.Response(
                    200, json={"ok": True},
                    headers={"X-MBX-USED-WEIGHT-1M": "1900"},
                    request=httpx.Request("GET", url),
                )

        client.client = _Http()

        for _ in range(5):
            await client._request_with_retry("GET", "/fapi/v1/ping")

        warnings = [
            c for c in client.logger.warning.call_args_list
            if "kullanılan ağırlık" in str(c)
        ]
        assert len(warnings) == 1, "aynı uyarı dakikada birden fazla basıldı"


# ---------------------------------------------------------------------------
# 6) Motor: geri çekilme AÇIKKEN tarama turu hiç başlamaz
# ---------------------------------------------------------------------------

def _bare_engine():
    from src.strategies.scalper.engine import ScalperEngine

    engine = ScalperEngine.__new__(ScalperEngine)
    engine.logger = MagicMock()
    engine.cfg = SimpleNamespace(
        scalper_symbol_allowlist="BTCUSDT",
        scalper_scan_interval_seconds=30,
        scalper_market_gate=True,
        scalper_market_gate_symbol="BTCUSDT",
        scalper_market_gate_day_pct=1.0,
        scalper_market_gate_run_pct=0.0,
        scalper_market_gate_run_days=3,
        scalper_market_gate_retry_sec=60.0,
    )
    engine.client = ImprovedBinanceClient
    return engine


class TestScanTickBackoff:
    async def test_scan_round_is_skipped_and_marked_degraded(self, enabled_limits):
        engine = _bare_engine()
        touched = []
        engine.scanner = SimpleNamespace(
            get_universe=lambda: touched.append("universe")
        )
        ImprovedBinanceClient._note_used_weight(2400)

        await engine._scan_tick()

        assert touched == [], "geri çekilmede evren taraması yine de yapıldı"
        assert engine._scan_status() == "degraded:rest_weight"
        assert engine._scan_degraded_count == 1

    async def test_default_configuration_never_skips_the_scan(self):
        """VARSAYILAN (kapalı): 4059/dk ölçülse bile tarama DURMAZ."""
        engine = _bare_engine()
        ImprovedBinanceClient._note_used_weight(4059)
        assert engine._rest_weight_backoff_level() == "off"

    def test_postmortem_is_deferred_during_backoff(self, enabled_limits):
        engine = _bare_engine()
        engine.exits = SimpleNamespace(_market_data_down_reason=None)
        engine.fetcher = SimpleNamespace(base_url="")
        assert engine._forensics_postmortem_blocked() is None
        ImprovedBinanceClient._note_used_weight(2400)
        assert "ağırlık" in (engine._forensics_postmortem_blocked() or "")


# ---------------------------------------------------------------------------
# 7) Durum netliği — entries_blocked_by + market_gate.stale_reason
# ---------------------------------------------------------------------------

def _ready_engine():
    engine = _bare_engine()
    engine._entry_halted = False
    engine._kill_switch = False
    engine._exchange_ready = True
    engine._recovery_ready = True
    engine._risk_ready = True
    engine._exchange_last_success_monotonic = time.monotonic()
    engine._risk_event_halt_snapshot = lambda: {"active": False}
    return engine


class TestEntriesBlockedBy:
    def test_none_when_everything_is_ready(self):
        assert _ready_engine().entries_blocked_by() is None

    def test_entry_halt_wins(self):
        engine = _ready_engine()
        engine._entry_halted = True
        engine._kill_switch = True
        assert engine.entries_blocked_by() == "entry_halt"

    def test_kill_switch(self):
        engine = _ready_engine()
        engine._kill_switch = True
        assert engine.entries_blocked_by() == "kill_switch"

    def test_risk_event(self):
        engine = _ready_engine()
        engine._risk_event_halt_snapshot = lambda: {"active": True}
        assert engine.entries_blocked_by() == "risk_event"

    def test_exchange_readiness(self):
        engine = _ready_engine()
        engine._risk_ready = False
        assert engine.entries_blocked_by() == "exchange_readiness"

    def test_rest_weight_is_visible_when_the_backoff_is_enabled(
        self, enabled_limits
    ):
        engine = _ready_engine()
        ImprovedBinanceClient._note_used_weight(2400)
        assert engine.entries_blocked_by() == "rest_weight"

    def test_rest_weight_is_invisible_by_default(self):
        engine = _ready_engine()
        ImprovedBinanceClient._note_used_weight(4059)
        assert engine.entries_blocked_by() is None

    def test_stale_gate_blames_the_blocked_scan_not_the_leader(self):
        """Kill switch açıkken tarama dönmez → kapı bayat kalır.

        Eskiden bu, `stale=true, gate_effective=false` olarak "kapı bozuldu"
        gibi okunuyordu (2026-08-23 log incelemesi).
        """
        engine = _ready_engine()
        engine._kill_switch = True
        engine._market_gate_cache = {}
        status = engine._market_gate_status()
        assert status["stale"] is True
        assert status["stale_reason"] == "entries_blocked"

    def test_stale_gate_without_block_is_leader_stale(self):
        engine = _ready_engine()
        engine._market_gate_cache = {}
        status = engine._market_gate_status()
        assert status["stale"] is True
        assert status["stale_reason"] == "leader_stale"


# ---------------------------------------------------------------------------
# 8) `/scalper/status` + `/api/status`: 5 sn önbellek, `as_of`, sorgu anahtarı
# ---------------------------------------------------------------------------

class TestStatusContract:
    def test_empty_status_declares_the_new_fields(self):
        empty = main_module._EMPTY_SCALPER_STATUS
        assert "entries_blocked_by" in empty
        assert "rest_weight" in empty
        assert "forensics_queue" in empty
        assert "as_of" in empty
        assert "stale_reason" in empty["market_gate"]

    def test_both_endpoints_share_a_five_second_ttl(self):
        assert main_module._STATUS_CACHE_TTL == 5.0

    async def test_engineless_status_reports_live_weight_counters(self, monkeypatch):
        monkeypatch.setattr(main_module, "scalper_engine", None)
        ImprovedBinanceClient._note_used_weight(2100)

        payload = await main_module.scalper_status()

        assert set(payload["rest_weight"]) >= {
            "last", "last_at", "max_1m", "peak_at",
            "soft_backoffs", "hard_backoffs",
        }
        assert payload["rest_weight"]["last"] == 2100
        assert payload["as_of"]

    async def test_engineless_status_is_not_cached(self, monkeypatch):
        """Motorsuz yol REST yapmaz; olay defteri her çağrıda TAZE olmalı."""
        monkeypatch.setattr(main_module, "scalper_engine", None)
        ImprovedBinanceClient._note_used_weight(1000)
        first = await main_module.scalper_status()
        ImprovedBinanceClient._note_used_weight(1234)
        second = await main_module.scalper_status()
        assert (first["rest_weight"]["last"], second["rest_weight"]["last"]) == (
            1000, 1234
        )

    async def test_engine_status_is_cached_for_five_seconds(self, monkeypatch):
        calls = []

        class _Engine:
            def snapshot(self):
                calls.append(1)
                return {"n": len(calls)}

        monkeypatch.setattr(main_module, "scalper_engine", _Engine())

        first = await main_module.scalper_status()
        second = await main_module.scalper_status()
        assert first is second
        assert len(calls) == 1

    async def test_query_variants_get_separate_cache_entries(self, monkeypatch):
        """`?include_shadow=1` gibi bir sorgu ANAHTARIN parçasıdır.

        Aksi halde ileride eklenecek bir varyant, YANLIŞ gövdeyi 5 sn
        boyunca servis ederdi (sessiz veri sızıntısı).
        """
        calls = []

        class _Engine:
            def snapshot(self):
                calls.append(1)
                return {"n": len(calls)}

        monkeypatch.setattr(main_module, "scalper_engine", _Engine())

        plain = await main_module.scalper_status(_Req([]))
        shadow = await main_module.scalper_status(_Req([("include_shadow", "1")]))
        assert plain != shadow
        assert len(calls) == 2
        # aynı sorgu ikinci kez → önbellek
        again = await main_module.scalper_status(_Req([("include_shadow", "1")]))
        assert again is shadow
        assert len(calls) == 2

    async def test_state_changing_endpoints_drop_the_cache(self, monkeypatch):
        calls = []

        class _Engine:
            def snapshot(self):
                calls.append(1)
                return {"n": len(calls)}

        monkeypatch.setattr(main_module, "scalper_engine", _Engine())
        await main_module.scalper_status()
        main_module._reset_status_caches()
        await main_module.scalper_status()
        assert len(calls) == 2

    def test_mutating_endpoints_call_the_invalidator(self):
        """Sözleşme: risk-event ve TV olay sıfırlama önbelleği düşürür."""
        for fn in (main_module.risk_event, main_module.tv_events_reset):
            assert "_reset_status_caches()" in inspect.getsource(fn), fn.__name__

    async def test_api_status_is_cached_and_never_forces_fresh(self, monkeypatch):
        seen = []

        class _Client:
            async def get_account_balance(self, *, priority="critical"):
                seen.append(("balance", priority))
                return 100.0

            async def get_current_price(self, symbol, *, priority="critical"):
                seen.append(("price", priority))
                return 60000.0

            async def get_all_positions(self, *, force_fresh=True,
                                        priority="critical"):
                seen.append(("positions", priority, force_fresh))
                return []

        monkeypatch.setattr(
            main_module, "orchestrator", SimpleNamespace(binance=_Client())
        )
        monkeypatch.setattr(main_module, "follower_engine", None)
        monkeypatch.setattr(main_module, "telegram_bot", None)

        first = await main_module.api_status()
        second = await main_module.api_status()

        assert first is second, "pano yanıtı önbelleklenmedi"
        assert first["as_of"], "`as_of` damgası yok"
        assert [s[1] for s in seen] == ["background"] * 3
        assert ("positions", "background", False) in seen


# ---------------------------------------------------------------------------
# 9) Pano: "Sistem durumu" şeridi MEVCUT çağrıdan beslenir
# ---------------------------------------------------------------------------

class TestDashboardSysbar:
    def _html(self) -> str:
        from pathlib import Path

        return Path("static/dashboard.html").read_text(encoding="utf-8")

    def _sysbar_body(self) -> str:
        html = self._html()
        body = html[html.index("function renderSysbar("):]
        return body[: body.index("// 2. strateji skor tablosu")]

    def test_sysbar_exists_and_is_rendered_each_tick(self):
        html = self._html()
        assert 'id="sysbar"' in html
        assert "function renderSysbar(" in html
        assert "renderSysbar(scalperStatus);" in html

    def test_sysbar_reads_only_existing_status_fields(self):
        """YENİ istek YOK: tüm alanlar /scalper/status gövdesinden okunur."""
        body = self._sysbar_body()
        for field in (
            "s.market_gate", "s.kline_source", "s.kill_switch_active",
            "s.rest_weight", "s.tv_events", "s.forensics_queue",
            "s.entries_blocked_by", "gate.stale_reason",
        ):
            assert field in body, field
        assert "fetchJSON(" not in body, "şerit yeni bir istek açıyor"

    def test_kline_badge_compares_real_values(self):
        """`kline_source` DEĞERLERİ "separate"/"trading_host"tur.

        Rozet eskiden `=== "mainnet"` karşılaştırıyordu — doğru kurulumda
        bile ASLA yeşil olmuyordu. Karar artık gerçek host adından verilir.
        """
        body = self._sysbar_body()
        code = "\n".join(
            line for line in body.splitlines()
            if not line.lstrip().startswith("//")
        )
        assert 'src === "mainnet"' not in code
        assert 'kline_source === "mainnet"' not in code
        assert "s.market_data_base_url" in code
        assert "/testnet/i" in code

    def test_weight_badge_uses_the_new_telemetry_contract(self):
        body = self._sysbar_body()
        assert "w.max_1m" in body and "w.enabled" in body
        assert "dk tepe" in body, "tepe DAKİKA DİLİMLİ olarak etiketlenmeli"

    def test_rest_weight_block_reason_is_labelled(self):
        assert "rest_weight:" in self._html()

    def test_daily_breaker_states_the_utc_reset(self):
        assert "00:00 UTC'de sıfırlanır" in self._html()

    def test_last_update_uses_the_as_of_stamp(self):
        html = self._html()
        assert "setLastUpdate(scalperStatus, status);" in html
        assert "data.as_of" in html

    def test_market_exit_reasons_use_the_trail_chip(self):
        html = self._html()
        assert 'reason === "TRAIL_MARKET"' in html
        assert 'reason === "BE_MARKET"' in html


# ---------------------------------------------------------------------------
# 10) Uçlar GERÇEK HTTP yolundan da çalışmalı (Request enjeksiyonu)
# ---------------------------------------------------------------------------

class TestStatusRoutes:
    def _client(self):
        from fastapi.testclient import TestClient

        return TestClient(main_module.app)

    def test_routes_serve_and_stamp_as_of(self):
        """`request: Request = None` imzası FastAPI'de bozulmamalı.

        Varsayılan `None`, uçları doğrudan `await scalper_status()` diye
        çağıran testler için vardır; FastAPI yine de gerçek `Request`i
        enjekte eder. Bu test o sözleşmeyi HTTP yolundan çiviler.
        """
        client = self._client()
        for url in ("/scalper/status", "/api/status"):
            res = client.get(url)
            assert res.status_code == 200, url
            assert res.json().get("as_of"), url

    def test_query_string_is_accepted(self):
        res = self._client().get("/scalper/status?include_shadow=1")
        assert res.status_code == 200
