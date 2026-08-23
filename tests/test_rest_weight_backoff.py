"""D22 — REST ağırlık yumuşak geri çekilmesi + durum netliği + pano önbelleği.

Kusur (2026-08-23 canlı log): `X-MBX-USED-WEIGHT-1M` ≥ 1800 için YALNIZ bir
WARNING vardı — 276 satır/gün, tepe 4059/dk (sınır 2400). Sayaç IP GENELİDİR,
yani başka süreçler de tüketir; bot bütçeyi görüyor ama davranışını
değiştirmiyordu. 418 ban = koruma turunun körleşmesi, repoda en pahalı arıza.

Sözleşme:
  * ağırlık ≥ soft (2000): KRİTİK OLMAYAN istekler (pano beslemeleri,
    periyodik hesap özeti, evren taraması, teşhis) dakika penceresi dolana
    kadar gönderilmez; önbellek varsa BAYAT servis edilir,
  * ağırlık ≥ hard (2300): tamamen durur + dakikada BİR CRITICAL satır,
  * emir/SL-TP/positionRisk koruma turu/kapanış doğrulaması HER ZAMAN geçer,
  * ağırlık uyarısı dakikada en fazla bir satır,
  * `/api/status` ≥10 sn, `/scalper/status` ≥5 sn sunucu-tarafı önbellek;
    pano yolundan `force_fresh` YOK,
  * `/scalper/status.rest_weight` = {last, max_1m, soft_backoffs, hard_backoffs},
  * `/scalper/status.entries_blocked_by` + `market_gate.stale_reason`.
"""

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import src.main as main_module
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


def _bare_client() -> ImprovedBinanceClient:
    client = object.__new__(ImprovedBinanceClient)
    client.logger = MagicMock()
    client.base_url = "https://testnet.invalid"
    return client


# ---------------------------------------------------------------------------
# 1) Ölçüm → geri çekilme penceresi
# ---------------------------------------------------------------------------

class TestBackoffWindow:
    def test_below_soft_limit_is_off(self):
        ImprovedBinanceClient._note_used_weight(1500)
        assert ImprovedBinanceClient.weight_backoff_level() == "off"
        assert ImprovedBinanceClient.weight_backoff_active() is False

    def test_soft_limit_opens_soft_window(self):
        ImprovedBinanceClient._note_used_weight(2100)
        assert ImprovedBinanceClient.weight_backoff_level() == "soft"

    def test_hard_limit_opens_hard_window(self):
        ImprovedBinanceClient._note_used_weight(4059)
        assert ImprovedBinanceClient.weight_backoff_level() == "hard"

    def test_window_ends_with_the_calendar_minute(self):
        """Binance 1M sayacı takvim dakikasında sıfırlanır — pencere de."""
        ImprovedBinanceClient._note_used_weight(2400)
        end = ImprovedBinanceClient._weight_hard_until
        assert end % 60 == 0
        assert 0 < end - time.time() <= 60.0

    def test_peak_is_remembered(self):
        ImprovedBinanceClient._note_used_weight(1200)
        ImprovedBinanceClient._note_used_weight(4059)
        ImprovedBinanceClient._note_used_weight(300)
        snap = ImprovedBinanceClient.rest_weight_snapshot()
        assert (snap["last"], snap["max_1m"]) == (300, 4059)


# ---------------------------------------------------------------------------
# 2) Kapı: kritik geçer, kritik olmayan durur
# ---------------------------------------------------------------------------

class TestWeightGate:
    def test_critical_is_never_blocked(self):
        client = _bare_client()
        ImprovedBinanceClient._note_used_weight(4059)
        client._weight_gate("/fapi/v1/order", "critical")  # istisna YOK

    def test_background_is_blocked_and_counted(self):
        client = _bare_client()
        ImprovedBinanceClient._note_used_weight(2100)
        with pytest.raises(RestWeightBackoff):
            client._weight_gate("/fapi/v2/account", "background")
        snap = ImprovedBinanceClient.rest_weight_snapshot()
        assert (snap["soft_backoffs"], snap["hard_backoffs"]) == (1, 0)

    def test_hard_backoff_logs_critical_at_most_once_per_minute(self):
        client = _bare_client()
        ImprovedBinanceClient._note_used_weight(2400)
        for _ in range(5):
            with pytest.raises(RestWeightBackoff):
                client._weight_gate("/fapi/v2/account", "background")
        assert client.logger.critical.call_count == 1
        snap = ImprovedBinanceClient.rest_weight_snapshot()
        assert snap["hard_backoffs"] == 5

    async def test_request_layer_applies_the_gate(self):
        client = _bare_client()
        client._ensure_rest_allowed = lambda endpoint: None
        ImprovedBinanceClient._note_used_weight(2100)
        with pytest.raises(RestWeightBackoff):
            await client._request_with_retry(
                "GET", "/fapi/v2/account", signed=True, priority="background"
            )

    def test_disabled_limits_restore_old_behaviour(self, monkeypatch):
        from src.core import config as config_module

        monkeypatch.setattr(
            config_module.settings, "binance_weight_soft_limit", 0, raising=False
        )
        monkeypatch.setattr(
            config_module.settings, "binance_weight_hard_limit", 0, raising=False
        )
        ImprovedBinanceClient._note_used_weight(4059)
        assert ImprovedBinanceClient.weight_backoff_level() == "off"


# ---------------------------------------------------------------------------
# 3) Önbellekten servis: bayat veri > 418
# ---------------------------------------------------------------------------

class TestStaleCacheServing:
    async def test_account_is_served_stale_without_a_new_request(self):
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

    async def test_price_is_served_stale_without_a_new_request(self):
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

    async def test_critical_read_still_goes_to_the_network(self):
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
# 4) Ağırlık uyarısı dakikada en fazla bir satır
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
# 5) Motor: tarama turu geri çekilmede hiç başlamaz
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
    async def test_scan_round_is_skipped_and_marked_degraded(self):
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

    async def test_normal_weight_does_not_skip(self):
        engine = _bare_engine()
        ImprovedBinanceClient._note_used_weight(100)
        assert engine._rest_weight_backoff_level() == "off"

    def test_postmortem_is_deferred_during_backoff(self):
        engine = _bare_engine()
        engine.exits = SimpleNamespace(_market_data_down_reason=None)
        engine.fetcher = SimpleNamespace(base_url="")
        assert engine._forensics_postmortem_blocked() is None
        ImprovedBinanceClient._note_used_weight(2400)
        assert "ağırlık" in (engine._forensics_postmortem_blocked() or "")


# ---------------------------------------------------------------------------
# 6) Durum netliği — entries_blocked_by + market_gate.stale_reason
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
# 7) `/scalper/status` sözleşmesi + pano önbellekleri
# ---------------------------------------------------------------------------

class TestStatusContract:
    def test_empty_status_declares_the_new_fields(self):
        empty = main_module._EMPTY_SCALPER_STATUS
        assert "entries_blocked_by" in empty
        assert "rest_weight" in empty
        assert "forensics_queue" in empty
        assert "stale_reason" in empty["market_gate"]

    async def test_engineless_status_reports_live_weight_counters(self, monkeypatch):
        monkeypatch.setattr(main_module, "scalper_engine", None)
        ImprovedBinanceClient._note_used_weight(2100)

        payload = await main_module.scalper_status()

        assert set(payload["rest_weight"]) >= {
            "last", "max_1m", "soft_backoffs", "hard_backoffs"
        }
        assert payload["rest_weight"]["last"] == 2100

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

        engine = _Engine()
        monkeypatch.setattr(main_module, "scalper_engine", engine)

        first = await main_module.scalper_status()
        second = await main_module.scalper_status()
        assert first is second
        assert len(calls) == 1

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
        assert [s[1] for s in seen] == ["background"] * 3
        assert ("positions", "background", False) in seen


# ---------------------------------------------------------------------------
# 8) Pano: "Sistem durumu" şeridi MEVCUT çağrıdan beslenir
# ---------------------------------------------------------------------------

class TestDashboardSysbar:
    def _html(self) -> str:
        from pathlib import Path

        return Path("static/dashboard.html").read_text(encoding="utf-8")

    def test_sysbar_exists_and_is_rendered_each_tick(self):
        html = self._html()
        assert 'id="sysbar"' in html
        assert "function renderSysbar(" in html
        assert "renderSysbar(scalperStatus);" in html

    def test_sysbar_reads_only_existing_status_fields(self):
        """YENİ istek YOK: tüm alanlar /scalper/status gövdesinden okunur."""
        html = self._html()
        body = html[html.index("function renderSysbar("):]
        body = body[: body.index("// 2. strateji skor tablosu")]
        for field in (
            "s.market_gate", "s.kline_source", "s.kill_switch_active",
            "s.rest_weight", "s.tv_events", "s.forensics_queue",
            "s.entries_blocked_by", "gate.stale_reason",
        ):
            assert field in body, field
        assert "fetchJSON(" not in body, "şerit yeni bir istek açıyor"

    def test_daily_breaker_states_the_utc_reset(self):
        assert "00:00 UTC'de sıfırlanır" in self._html()

    def test_trail_market_uses_the_trail_chip(self):
        assert 'reason === "TRAIL_MARKET"' in self._html()
