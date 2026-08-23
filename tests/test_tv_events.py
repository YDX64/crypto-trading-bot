"""TradingView ÇIKIŞ + YAPI/DÖNÜŞ olay kanalı (D19, 2026-08-23).

Sözleşme (docs/INTEGRATIONS.md §7):
  * `/tv-signal` gövdesinde `kind=exit|choch|trend|tp1` varsa istek bir GİRİŞ
    OYU DEĞİLDİR — sağlamaya (TvConfluence) HİÇ girmez, `external_signal`
    ÇAĞRILMAZ, `src/services/tv_events.py` defterine yazılır.
  * `kind` yoksa davranış bugünküyle BİREBİR aynıdır (mevcut 49 alarm).
    Bunun regresyonu ayrıca `tests/test_tv_signal_bridge.py`'dir — o dosya bu
    değişiklikte DEĞİŞMEDEN geçmek zorundadır.
  * Gövdedeki `src` (allowlist'teyse) URL'deki `?src=`'i geçersiz kılar —
    kullanıcı yeni alarmları eskileri KLONLAYARAK kuruyor, URL değişmiyor.
  * `SCALPER_TV_EVENTS_MODE=shadow` (varsayılan) motorun davranışını
    DEĞİŞTİRMEZ: yalnız "ne olurdu" loglanır ve would_block/would_exit
    sayaçları artar.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

import src.main as main_module
import src.services.tv_events as tv_events_module
from src.core.config import Settings, settings
from src.services.tv_events import TvEvents
from src.strategies.scalper.engine import ScalperEngine
from src.strategies.scalper.exits import ExitManager
from src.strategies.scalper.types import (
    Candle,
    Direction,
    ScalpSignal,
    StrategyContext,
)
from src.trading.position_manager import UnprotectedPositionError
from src.trading.symbol_reservations import symbol_reservations

SECRET = "sup3r-gizli-t0ken"


# ---------------------------------------------------------------------------
# Ortak test çiftleri
# ---------------------------------------------------------------------------

class _FakeRequest:
    """`Request`'in webhook'ta kullanılan iki yüzeyi."""

    def __init__(self, body: bytes, query: dict | None = None):
        self._body = body
        self.query_params = query or {}

    async def body(self) -> bytes:
        return self._body


class _CfgProxy:
    """Gerçek `settings`i temel alır, yalnız verilen alanları ezer.

    Motor (`_evaluate_symbol`) onlarca `SCALPER_*` alanı okur; TV olay
    ayarlarını izole etmek için tüm ayarları elle taklit etmek yerine
    gerçeğine delege ediyoruz — böylece test, canlı varsayılanlarla aynı
    yolu yürür.
    """

    def __init__(self, **overrides):
        self._overrides = overrides

    def __getattr__(self, name):
        overrides = object.__getattribute__(self, "_overrides")
        if name in overrides:
            return overrides[name]
        return getattr(settings, name)


def _ledger(tmp_path, **overrides) -> TvEvents:
    """İzole (tmp_path'e yazan) bir olay defteri; süreç tekilini KİRLETMEZ."""
    cfg = _CfgProxy(
        scalper_tv_events_mode=overrides.pop("mode", "shadow"),
        scalper_tv_events_exit=overrides.pop("exit_action", "be"),
        scalper_tv_events_exit_losing=overrides.pop("exit_losing", "skip"),
        scalper_tv_events_be_margin_pct=overrides.pop("be_margin_pct", 0.05),
        scalper_tv_events_max_age_min=overrides.pop("max_age_min", 240.0),
        scalper_tv_events_gate_sources=overrides.pop(
            "gate_sources", "pac_choch,luxso_trend"
        ),
        **overrides,
    )
    return TvEvents(cfg, state_path=str(tmp_path / "tv_events.json"))


@pytest.fixture
def tv_ledger(tmp_path, monkeypatch):
    """Endpoint testleri için: süreç tekilini izole bir defterle değiştir."""
    ledger = _ledger(tmp_path)
    monkeypatch.setattr(main_module, "tv_events", ledger)
    return ledger


@pytest.fixture
def webhook_ready(monkeypatch):
    """Secret + hazır motor; `external_signal` AsyncMock (gerçek işlem yok)."""
    monkeypatch.setattr(main_module.settings, "tv_webhook_secret", SECRET)
    monkeypatch.setattr(main_module.settings, "tv_confluence_required", 1)
    engine = MagicMock()
    engine.external_signal = AsyncMock(return_value={"accepted": True})
    monkeypatch.setattr(main_module, "scalper_engine", engine)
    return engine


# ===========================================================================
# 1) Gövde yönlendirme — saf çözücüler
# ===========================================================================

class TestBodyRouting:
    def test_json_fields_parsed(self):
        raw = json.dumps(
            {"secret": SECRET, "symbol": "BTCUSDT", "src": "PAC_CHOCH", "kind": "CHoCH"}
        )
        assert main_module.resolve_tv_body_fields(raw, secret=SECRET) == {
            "src": "pac_choch",
            "kind": "choch",
        }

    def test_json_source_alias_accepted(self):
        raw = json.dumps({"secret": SECRET, "source": "luxso_exit", "kind": "exit"})
        fields = main_module.resolve_tv_body_fields(raw, secret=SECRET)
        assert fields["src"] == "luxso_exit"

    @pytest.mark.parametrize(
        "raw",
        [
            "src=luxso_exit kind=exit BTCUSDT",
            "src=luxso_exit,kind=exit,BTCUSDT",
            "src=luxso_exit|kind=exit|BTCUSDT",
            "SRC = luxso_exit   KIND = exit   BTCUSDT",
        ],
    )
    def test_plain_text_tokens_and_separators(self, raw):
        assert main_module.resolve_tv_body_fields(raw) == {
            "src": "luxso_exit",
            "kind": "exit",
        }

    def test_embedded_lookalike_token_not_matched(self):
        # "mysrc=" / "xkind=" gövdedeki başka bir kelimenin parçasıdır.
        assert main_module.resolve_tv_body_fields("mysrc=x xkind=exit BTCUSDT") == {}

    def test_secret_text_is_stripped_before_token_scan(self):
        weird = "abc-kind=exit-def"  # secret'ın içinde `kind=` geçiyor
        raw = f"secret={weird} BTCUSDT Bullish Confirmation"
        assert main_module.resolve_tv_body_fields(raw, secret=weird) == {}

    def test_missing_kind_defaults_to_entry(self):
        assert main_module.resolve_tv_kind(None) == "entry"
        assert main_module.resolve_tv_kind("") == "entry"

    @pytest.mark.parametrize("kind", ["entry", "exit", "choch", "trend", "tp1"])
    def test_known_kinds_accepted(self, kind):
        assert main_module.resolve_tv_kind(kind.upper()) == kind

    def test_unknown_kind_rejected_422_not_downgraded_to_entry(self):
        """Yazım hatası GİRİŞ oyuna DÖNÜŞMEZ: 422. `?src=` allowlist'inin
        'reddetme, tv'ye eşle' davranışının bilinçli tersi — orada en kötü
        sonuç bir oyun sayılmaması, burada istenmeyen bir POZİSYON."""
        with pytest.raises(HTTPException) as exc:
            main_module.resolve_tv_kind("exitt")
        assert exc.value.status_code == 422


class TestSourceOverride:
    def test_body_src_overrides_query_src(self):
        source, raw_rejected, body_rejected = main_module.resolve_tv_source_with_body(
            "luxso", "pac_choch", "src=pac_choch kind=choch bearish BTCUSDT"
        )
        assert (source, raw_rejected, body_rejected) == ("pac_choch", False, False)

    def test_body_src_outside_allowlist_falls_back_to_query(self, monkeypatch):
        monkeypatch.setattr(
            main_module.settings, "tv_source_allowlist", "luxosc,luxso,tv"
        )
        source, raw_rejected, body_rejected = main_module.resolve_tv_source_with_body(
            "luxso", "pac_chch", "..."
        )
        assert source == "luxso"          # mevcut davranış korundu
        assert raw_rejected is False
        assert body_rejected is True      # ama görünür kılındı

    def test_no_body_src_keeps_todays_behaviour(self):
        source, raw_rejected, body_rejected = main_module.resolve_tv_source_with_body(
            "luxosc", None, "Bullish Confirmation BTCUSDT.P"
        )
        assert (source, raw_rejected, body_rejected) == ("luxosc", False, False)

    def test_new_event_sources_are_in_default_allowlist(self):
        """`.env` TV_SOURCE_ALLOWLIST'i set etmiyorsa dört olay kaynağı
        kutudan çıktığı gibi çalışmalı (docs/RUNBOOK.md uyarısı: .env
        AÇIKÇA set ediyorsa oraya da eklenmeli)."""
        for name in ("luxso_exit", "luxso_trend", "pac_choch", "algopro_tp1"):
            source, rejected = main_module.resolve_tv_source(name, "")
            assert (source, rejected) == (name, False)


class TestEventDirectionResolution:
    def _direction(self, raw: str):
        return main_module.resolve_tv_event_direction({}, raw, SECRET)

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("src=luxso_trend kind=trend up BTCUSDT", Direction.LONG),
            ("src=luxso_trend kind=trend down BTCUSDT", Direction.SHORT),
            ("src=pac_choch kind=choch bullish BTCUSDT", Direction.LONG),
            ("src=pac_choch kind=choch bearish BTCUSDT", Direction.SHORT),
            ("kind=choch BTCUSDT Bullish S-CHOCH", Direction.LONG),
            ("kind=trend BTCUSDT Trend Catcher Down", Direction.SHORT),
        ],
    )
    def test_word_dictionary(self, raw, expected):
        assert self._direction(raw) == expected

    def test_directionless_exit_returns_none(self):
        assert self._direction("src=luxso_exit kind=exit BTCUSDT") is None
        assert self._direction("src=algopro_tp1 kind=tp1 BTCUSDT") is None

    def test_source_token_is_not_scanned_as_direction(self):
        """`src=luxso_exit` içindeki metin yön sanılmamalı."""
        assert self._direction("src=luxso_exit kind=exit ETHUSDT") is None

    def test_word_boundary_prevents_up_substring_false_positive(self):
        """'up' artık sözlükte — alt-dize eşleşmesi 'SETUP'/'SUPPORT'
        kelimelerinde yanlış yön üretirdi."""
        assert self._direction("kind=exit BTCUSDT SETUP SUPPORT") is None

    def test_conflicting_words_return_none(self):
        assert self._direction("kind=exit BTCUSDT bullish bearish") is None


# ===========================================================================
# 2) Endpoint yönlendirmesi — gerçek kod yolu
# ===========================================================================

class TestWebhookRouting:
    async def test_entry_alarm_path_unchanged(self, webhook_ready, tv_ledger):
        """`kind` yok → bugünkü giriş yolu; olay defterine HİÇBİR ŞEY yazılmaz."""
        body = json.dumps({"secret": SECRET, "symbol": "BTCUSDT", "side": "buy"})
        result = await main_module.tradingview_webhook(
            _FakeRequest(body.encode(), {"src": "luxosc"})
        )
        assert result["accepted"] is True
        assert result["source"] == "luxosc"
        assert "kind" not in result
        webhook_ready.external_signal.assert_awaited_once()
        assert tv_ledger.symbols() == []

    async def test_exit_event_never_reaches_confluence_or_engine(
        self, webhook_ready, tv_ledger, monkeypatch
    ):
        votes = []
        monkeypatch.setattr(
            main_module,
            "_tv_confluence",
            lambda: SimpleNamespace(vote=lambda *a, **kw: votes.append(a)),
        )
        request = _FakeRequest(
            f"secret={SECRET} src=luxso_exit kind=exit BTCUSDT".encode(),
            {"src": "luxso"},  # eski (klonlanmış) URL etiketi
        )
        result = await main_module.tradingview_webhook(request)

        assert result["routed"] == "event"
        assert result["kind"] == "exit"
        assert result["direction"] is None      # S&O "Exit Signal" YÖNSÜZ
        assert result["source"] == "luxso_exit"  # gövde, ?src=luxso'yu ezdi
        assert result["source_from_body"] is True
        webhook_ready.external_signal.assert_not_awaited()
        assert votes == []
        assert tv_ledger.pending_exit("BTCUSDT")["kind"] == "exit"

    async def test_choch_event_sets_structure(self, webhook_ready, tv_ledger):
        request = _FakeRequest(
            f"secret={SECRET} src=pac_choch kind=choch bearish BTCUSDT".encode(), {}
        )
        result = await main_module.tradingview_webhook(request)
        assert result["structure"] == "BEAR"
        assert tv_ledger.symbol_state("BTCUSDT")["structures"]["pac_choch"][
            "structure"
        ] == "BEAR"

    async def test_trend_up_maps_bull(self, webhook_ready, tv_ledger):
        request = _FakeRequest(
            f"secret={SECRET} src=luxso_trend kind=trend up ETHUSDT".encode(), {}
        )
        result = await main_module.tradingview_webhook(request)
        assert result["structure"] == "BULL"

    async def test_structure_event_without_direction_rejected_422(
        self, webhook_ready, tv_ledger
    ):
        request = _FakeRequest(
            f"secret={SECRET} src=pac_choch kind=choch BTCUSDT".encode(), {}
        )
        with pytest.raises(HTTPException) as exc:
            await main_module.tradingview_webhook(request)
        assert exc.value.status_code == 422
        assert tv_ledger.symbols() == []

    async def test_event_requires_valid_secret(self, webhook_ready, tv_ledger):
        request = _FakeRequest(b"secret=yanlis src=luxso_exit kind=exit BTCUSDT", {})
        with pytest.raises(HTTPException) as exc:
            await main_module.tradingview_webhook(request)
        assert exc.value.status_code == 403
        assert tv_ledger.symbols() == []

    async def test_event_accepts_url_secret_like_entry_path(
        self, webhook_ready, tv_ledger
    ):
        request = _FakeRequest(
            b"src=algopro_tp1 kind=tp1 SOLUSDT", {"secret": SECRET}
        )
        result = await main_module.tradingview_webhook(request)
        assert result["kind"] == "tp1"

    async def test_event_recorded_even_when_engine_not_ready(
        self, monkeypatch, tv_ledger
    ):
        monkeypatch.setattr(main_module.settings, "tv_webhook_secret", SECRET)
        monkeypatch.setattr(main_module, "scalper_engine", None)
        request = _FakeRequest(
            f"secret={SECRET} src=luxso_exit kind=exit XRPUSDT".encode(), {}
        )
        result = await main_module.tradingview_webhook(request)
        assert result["routed"] == "event"
        assert tv_ledger.pending_exit("XRPUSDT") is not None

    async def test_no_secret_in_log_or_response(
        self, webhook_ready, tv_ledger, monkeypatch
    ):
        lines: list = []
        monkeypatch.setattr(
            main_module.app_logger,
            "info",
            lambda msg, *a, **kw: lines.append(str(msg)),
        )
        monkeypatch.setattr(
            main_module.app_logger,
            "warning",
            lambda msg, *a, **kw: lines.append(str(msg)),
        )
        request = _FakeRequest(
            f"secret={SECRET} src=luxso_exit kind=exit BTCUSDT".encode(), {}
        )
        result = await main_module.tradingview_webhook(request)

        assert any("🧭 TV olayı" in line for line in lines)
        assert all(SECRET not in line for line in lines)
        assert SECRET not in json.dumps(result)
        assert SECRET not in json.dumps(tv_ledger.snapshot())


# ===========================================================================
# 3) TvEvents — durum, tazelik, kalıcılık
# ===========================================================================

class TestTvEventsLedger:
    def test_structure_kind_sets_bull_bear(self, tmp_path):
        ledger = _ledger(tmp_path)
        ledger.ingest("BTCUSDT", "choch", Direction.SHORT, "pac_choch")
        rows = ledger.fresh_gate_structures("BTCUSDT")
        assert [(r["source"], r["structure"]) for r in rows] == [
            ("pac_choch", "BEAR")
        ]

    def test_exit_kind_does_not_change_structure(self, tmp_path):
        """'Bullish Exit' = LONG pozisyon için çıkış; YAPI bilgisi DEĞİL."""
        ledger = _ledger(tmp_path)
        ledger.ingest("BTCUSDT", "choch", Direction.SHORT, "pac_choch")
        ledger.ingest("BTCUSDT", "exit", Direction.LONG, "luxso_exit")
        assert ledger.fresh_gate_structures("BTCUSDT")[0]["structure"] == "BEAR"
        assert ledger.pending_exit("BTCUSDT")["direction"] == "LONG"

    def test_non_gate_source_excluded_from_gate_but_visible_in_status(self, tmp_path):
        ledger = _ledger(tmp_path, gate_sources="pac_choch")
        ledger.ingest("BTCUSDT", "trend", Direction.LONG, "luxso_trend")
        assert ledger.fresh_gate_structures("BTCUSDT") == []
        state = ledger.symbol_state("BTCUSDT")
        assert state["structure"] == "NONE"
        assert state["structures"]["luxso_trend"]["structure"] == "BULL"

    def test_conflicting_gate_sources_report_mixed(self, tmp_path):
        ledger = _ledger(tmp_path)
        ledger.ingest("BTCUSDT", "choch", Direction.LONG, "pac_choch")
        ledger.ingest("BTCUSDT", "trend", Direction.SHORT, "luxso_trend")
        assert ledger.symbol_state("BTCUSDT")["structure"] == "MIXED"
        assert len(ledger.fresh_gate_structures("BTCUSDT")) == 2

    def test_max_age_expires_structure_and_exit(self, tmp_path):
        ledger = _ledger(tmp_path, max_age_min=10.0)
        old = time.time() - 11 * 60
        ledger.ingest("BTCUSDT", "choch", Direction.SHORT, "pac_choch", ts=old)
        ledger.ingest("BTCUSDT", "exit", None, "luxso_exit", ts=old)
        assert ledger.fresh_gate_structures("BTCUSDT") == []
        assert ledger.pending_exit("BTCUSDT") is None
        # ama telemetride hâlâ görünür (yaşıyla birlikte)
        assert ledger.symbol_state("BTCUSDT")["structures"]["pac_choch"]["age_s"] > 600

    def test_persistence_round_trip(self, tmp_path):
        path = tmp_path / "tv_events.json"
        first = _ledger(tmp_path)
        first.ingest("BTCUSDT", "choch", Direction.SHORT, "pac_choch")
        first.ingest("BTCUSDT", "exit", None, "luxso_exit")
        assert path.exists()

        second = _ledger(tmp_path)
        assert second.fresh_gate_structures("BTCUSDT")[0]["structure"] == "BEAR"
        assert second.pending_exit("BTCUSDT")["kind"] == "exit"
        # seq monoton devam etmeli (restart sonrası eski olay yeniden tetiklemesin)
        state = second.ingest("ETHUSDT", "exit", None, "luxso_exit")
        assert second.latest_seq("ETHUSDT")["exit"] > second.latest_seq("BTCUSDT")["exit"]
        assert state["last_exit"]["kind"] == "exit"

    def test_corrupt_state_file_yields_empty_state_and_warning(self, tmp_path):
        path = tmp_path / "tv_events.json"
        path.write_text("{bozuk-json", encoding="utf-8")
        warnings: list = []
        logger = SimpleNamespace(
            warning=lambda msg, *a, **kw: warnings.append(str(msg)),
            info=lambda *a, **kw: None,
            error=lambda *a, **kw: None,
            debug=lambda *a, **kw: None,
        )
        ledger = TvEvents(
            SimpleNamespace(), state_path=str(path), logger=logger
        )
        assert ledger.symbols() == []
        assert len(warnings) == 1
        assert "TV olay durumu okunamadı" in warnings[0]

    def test_snapshot_has_no_secret_and_reports_config(self, tmp_path):
        ledger = _ledger(tmp_path, mode="active", exit_action="close")
        ledger.ingest("BTCUSDT", "choch", Direction.LONG, "pac_choch")
        snap = ledger.snapshot()
        assert snap["mode"] == "active"
        assert snap["exit_action"] == "close"
        assert snap["gate_sources"] == ["luxso_trend", "pac_choch"]
        assert snap["counters"]["ingested"] == 1
        assert snap["symbols"]["BTCUSDT"]["structure"] == "BULL"
        assert SECRET not in json.dumps(snap)


# ===========================================================================
# 4) Motor — giriş kapısı (TEK giriş noktası: _evaluate_symbol)
# ===========================================================================

class _AlwaysLongStrategy:
    def evaluate(self, ctx: StrategyContext):
        return ScalpSignal(
            strategy="C",
            symbol=ctx.symbol,
            direction=Direction.LONG,
            entry_price=100.0,
            stop_price=99.5,
            reason="tv-events-gate-test",
            regime=ctx.regime,
            atr_5m=1.0,
            risk_multiplier=1.0,
        )


class _FixedCandles:
    def __init__(self, candles):
        self._candles = candles

    async def get_klines(self, symbol, tf, limit):
        return self._candles


def _candles(n: int = 60):
    interval = 5 * 60 * 1000
    return [
        Candle(
            open_time=i * interval,
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.0,
            volume=10.0,
            close_time=i * interval + interval - 1,
        )
        for i in range(n)
    ]


class _FakeExecutor:
    def __init__(self):
        self.try_open = AsyncMock(return_value=None)
        self.rejects: dict = {}

    def is_entry_blocked(self, symbol):
        return False

    def pending_symbols(self):
        return set()

    def shadow_active_count(self):
        return 0

    def _count_reject(self, reason):
        self.rejects[reason] = self.rejects.get(reason, 0) + 1


def _gate_engine(ledger: TvEvents) -> ScalperEngine:
    engine = ScalperEngine.__new__(ScalperEngine)
    # Motor ve defter AYNI cfg'yi okumalı (mod/kapı kaynakları tek yerden).
    engine.cfg = ledger.cfg
    engine.logger = SimpleNamespace(
        info=lambda msg, *a, **kw: engine._logs.append(str(msg)),
        warning=lambda msg, *a, **kw: engine._logs.append(str(msg)),
        error=lambda msg, *a, **kw: engine._logs.append(str(msg)),
        debug=lambda *a, **kw: None,
        critical=lambda *a, **kw: None,
    )
    engine._logs = []
    engine.tv_events = ledger
    engine.executor = _FakeExecutor()
    engine.exits = SimpleNamespace(
        tracked_symbols=lambda: set(), track=lambda sp: None, _positions={}
    )
    engine.fetcher = _FixedCandles(_candles())
    engine.client = SimpleNamespace(get_all_positions=AsyncMock(return_value=[]))
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
    engine._risk_event_halt_path = None
    engine._risk_event_halt_cache = None
    engine._risk_event_halt_ram = None
    engine._tv_exit_seen = {}
    engine._tv_struct_seen = {}
    return engine


class TestStructureEntryGate:
    async def _run(self, ledger):
        symbol_reservations.clear()
        try:
            engine = _gate_engine(ledger)
            await engine._evaluate_symbol("BTCUSDT", [_AlwaysLongStrategy()])
            return engine
        finally:
            symbol_reservations.clear()

    async def test_active_mode_blocks_opposing_structure(self, tmp_path):
        ledger = _ledger(tmp_path, mode="active")
        ledger.ingest("BTCUSDT", "choch", Direction.SHORT, "pac_choch")
        engine = await self._run(ledger)

        engine.executor.try_open.assert_not_awaited()
        assert engine.executor.rejects.get("tv_structure_gate") == 1
        assert ledger.snapshot()["counters"]["blocked"] == 1
        assert any("TV yapı kapısı" in line for line in engine._logs)

    async def test_shadow_mode_only_counts_and_logs(self, tmp_path):
        ledger = _ledger(tmp_path, mode="shadow")
        ledger.ingest("BTCUSDT", "choch", Direction.SHORT, "pac_choch")
        engine = await self._run(ledger)

        engine.executor.try_open.assert_awaited_once()   # motor davranışı AYNI
        assert engine.executor.rejects == {}
        assert ledger.snapshot()["counters"]["would_block"] == 1
        assert ledger.snapshot()["counters"]["blocked"] == 0
        assert any("GÖLGE" in line for line in engine._logs)

    async def test_off_mode_does_nothing(self, tmp_path):
        ledger = _ledger(tmp_path, mode="off")
        ledger.ingest("BTCUSDT", "choch", Direction.SHORT, "pac_choch")
        engine = await self._run(ledger)

        engine.executor.try_open.assert_awaited_once()
        assert ledger.snapshot()["counters"]["would_block"] == 0

    async def test_same_direction_structure_never_blocks(self, tmp_path):
        ledger = _ledger(tmp_path, mode="active")
        ledger.ingest("BTCUSDT", "choch", Direction.LONG, "pac_choch")
        engine = await self._run(ledger)

        engine.executor.try_open.assert_awaited_once()
        assert engine.executor.rejects == {}

    async def test_stale_structure_never_blocks(self, tmp_path):
        ledger = _ledger(tmp_path, mode="active", max_age_min=10.0)
        ledger.ingest(
            "BTCUSDT", "choch", Direction.SHORT, "pac_choch", ts=time.time() - 11 * 60
        )
        engine = await self._run(ledger)

        engine.executor.try_open.assert_awaited_once()

    async def test_non_gate_source_never_blocks(self, tmp_path):
        ledger = _ledger(tmp_path, mode="active", gate_sources="pac_choch")
        ledger.ingest("BTCUSDT", "trend", Direction.SHORT, "luxso_trend")
        engine = await self._run(ledger)

        engine.executor.try_open.assert_awaited_once()

    async def test_missing_ledger_is_fail_open(self, tmp_path):
        """Defter hiç yoksa (eski test çifti / bozuk kurulum) giriş DURMAZ."""
        ledger = _ledger(tmp_path, mode="active")
        ledger.ingest("BTCUSDT", "choch", Direction.SHORT, "pac_choch")
        symbol_reservations.clear()
        try:
            engine = _gate_engine(ledger)
            del engine.tv_events
            await engine._evaluate_symbol("BTCUSDT", [_AlwaysLongStrategy()])
            engine.executor.try_open.assert_awaited_once()
        finally:
            symbol_reservations.clear()


# ===========================================================================
# 5) Motor — çıkış tetikleyicisi (safety turu)
# ===========================================================================

def _fake_sp(
    symbol="BTCUSDT",
    direction=Direction.LONG,
    opened_minutes_ago=5.0,
    current_price=None,
):
    """Açık pozisyon çifti.

    `current_price` VARSAYILANI KÂRDA'dır (LONG 102 > BE 101, SHORT 100 < 101).
    D19a bulgu B'den sonra BE tetiği yalnız kârda uygulanır; zarardaki
    senaryolar bu parametreyi açıkça ters tarafa koyar.
    """
    opened = datetime.now(timezone.utc) - timedelta(minutes=opened_minutes_ago)
    if current_price is None:
        current_price = 102.0 if direction == Direction.LONG else 100.0
    # `price_ts`: `exits.step()` fiyatı BAŞARIYLA okuduğunda basılan damga.
    # Damgasız/bayat fiyat "bilinmiyor"dur (D19a-2) — testler canlıdaki
    # normal durumu (taze fiyat) taklit eder.
    return SimpleNamespace(
        price_ts=time.monotonic(),
        symbol=symbol,
        signal=SimpleNamespace(direction=direction),
        plan=SimpleNamespace(breakeven_price=101.0),
        position=SimpleNamespace(
            symbol=symbol,
            opened_at=opened,
            quantity=1.0,
            # LONG'da BE (101) ÜSTÜ = koruyucu, SHORT'ta ALTI. Test çifti
            # gerçekçi kalsın: her iki yönde de SL henüz BE'ye ULAŞMAMIŞtır.
            current_stoploss=95.0 if direction == Direction.LONG else 107.0,
            current_price=current_price,
            side=SimpleNamespace(value=direction.value),
        ),
        trailing_active=False,
        tp1_done=False,
    )


def _exit_manager_shim(positions, cfg, *, replace_calls, replace_ok=True):
    """GERÇEK `ExitManager` metotlarını çalıştıran hafif çift.

    Sahte bir "hep True" lambda'sı D19a'nın B/1 bulgularının regresyon
    testlerini değersiz kılardı: motorun `force_breakeven`ı çağırma SIRASI
    ve o fonksiyonun kendi iç kapıları (`_closing` → hedef → "zaten
    koruyucu" → zarar kontrolü) tam olarak test edilmek istenen şey.
    """
    shim = object.__new__(ExitManager)
    shim._positions = positions
    shim._closing = set()
    shim.cfg = cfg
    shim.logger = MagicMock()

    async def _replace(position, new_stop):
        replace_calls.append((position.symbol, new_stop))
        return replace_ok

    shim.pm = SimpleNamespace(replace_stop_loss=_replace)
    return shim


def _real_side_ok(positions, cfg):
    def _check(symbol: str):
        shim = object.__new__(ExitManager)
        shim._positions = positions
        shim.cfg = cfg
        return ExitManager.breakeven_side_ok(shim, symbol)

    return _check


def _exit_engine(ledger: TvEvents, sp, *, extra_positions=()) -> ScalperEngine:
    engine = ScalperEngine.__new__(ScalperEngine)
    engine.cfg = ledger.cfg
    engine._logs = []
    engine.logger = SimpleNamespace(
        info=lambda msg, *a, **kw: engine._logs.append(str(msg)),
        warning=lambda msg, *a, **kw: engine._logs.append(str(msg)),
        error=lambda msg, *a, **kw: engine._logs.append(str(msg)),
        debug=lambda *a, **kw: None,
        critical=lambda *a, **kw: None,
    )
    engine.tv_events = ledger
    positions = {sp.symbol: sp} if sp is not None else {}
    for extra in extra_positions:
        positions[extra.symbol] = extra
    # `pm.replace_stop_loss` çağrıları burada toplanır: "borsaya emir gitti
    # mi" sorusunun tek doğru cevabı budur (force_breakeven çağrılmış olabilir
    # ama kendi kapılarında emir göndermeden dönmüş olabilir).
    engine._replace_calls = []
    shim = _exit_manager_shim(positions, ledger.cfg, replace_calls=engine._replace_calls)

    async def _force_breakeven(symbol, *, reason=""):
        return await ExitManager.force_breakeven(shim, symbol, reason=reason)

    engine.exits = SimpleNamespace(
        _positions=positions,
        tracked_symbols=lambda: set(positions.keys()),
        force_breakeven=AsyncMock(side_effect=_force_breakeven),
        breakeven_side_ok=_real_side_ok(positions, ledger.cfg),
        breakeven_would_act=lambda symbol: ExitManager.breakeven_would_act(
            shim, symbol
        ),
        _handle_closed=AsyncMock(),
    )
    engine.client = SimpleNamespace(
        get_position_risk=AsyncMock(return_value={"positionAmt": "0"}),
        quantize_quantity=AsyncMock(side_effect=lambda s, q: q),
        _request_with_retry=AsyncMock(return_value={}),
    )
    engine._latch_entry_halt = AsyncMock()
    engine._tv_exit_seen = {}
    engine._tv_struct_seen = {}
    engine._tv_attempts = {}
    return engine


class TestEventExitTrigger:
    async def test_shadow_mode_changes_nothing(self, tmp_path):
        ledger = _ledger(tmp_path, mode="shadow", exit_action="be")
        sp = _fake_sp()
        engine = _exit_engine(ledger, sp)
        ledger.ingest("BTCUSDT", "exit", None, "luxso_exit")

        await engine._apply_tv_event_exits()

        engine.exits.force_breakeven.assert_not_awaited()
        engine.client._request_with_retry.assert_not_awaited()
        assert ledger.snapshot()["counters"]["would_exit"] == 1
        assert any("GÖLGE" in line for line in engine._logs)

    async def test_active_be_uses_existing_breakeven_path_once(self, tmp_path):
        ledger = _ledger(tmp_path, mode="active", exit_action="be")
        sp = _fake_sp()
        engine = _exit_engine(ledger, sp)
        ledger.ingest("BTCUSDT", "exit", None, "luxso_exit")

        await engine._apply_tv_event_exits()
        await engine._apply_tv_event_exits()  # aynı olay İKİNCİ kez tetiklememeli

        engine.exits.force_breakeven.assert_awaited_once()
        assert ledger.snapshot()["counters"]["exits_applied"] == 1

    async def test_active_close_uses_shared_reduce_only_path_with_tv_reason(
        self, tmp_path
    ):
        ledger = _ledger(tmp_path, mode="active", exit_action="close")
        sp = _fake_sp()
        engine = _exit_engine(ledger, sp)
        ledger.ingest("BTCUSDT", "tp1", None, "algopro_tp1")

        await engine._apply_tv_event_exits()

        # positionAmt=0 → zaten flat; emir gönderilmez ama ledger etiketlenir.
        engine.exits._handle_closed.assert_awaited_once()
        kwargs = engine.exits._handle_closed.await_args.kwargs
        assert kwargs["forced_exit_reason"] == "TV_EVENT"
        # force_fresh doğrulaması (D10 dersi) korunmuş olmalı
        assert engine.client.get_position_risk.await_args.kwargs["force_fresh"] is True

    async def test_exit_off_disables_every_action(self, tmp_path):
        ledger = _ledger(tmp_path, mode="active", exit_action="off")
        sp = _fake_sp()
        engine = _exit_engine(ledger, sp)
        ledger.ingest("BTCUSDT", "exit", None, "luxso_exit")

        await engine._apply_tv_event_exits()

        engine.exits.force_breakeven.assert_not_awaited()
        assert ledger.snapshot()["counters"]["exits_applied"] == 0
        # imleç yine de ilerledi: mod sonradan açılınca toplu tetikleme olmaz
        assert engine._tv_exit_seen["BTCUSDT"] > 0

    async def test_direction_mismatch_is_logged_not_applied(self, tmp_path):
        ledger = _ledger(tmp_path, mode="active", exit_action="be")
        sp = _fake_sp(direction=Direction.SHORT)
        engine = _exit_engine(ledger, sp)
        ledger.ingest("BTCUSDT", "exit", Direction.LONG, "luxso_exit")

        await engine._apply_tv_event_exits()

        engine.exits.force_breakeven.assert_not_awaited()
        assert any("uygulanmadı" in line for line in engine._logs)

    async def test_event_older_than_position_is_ignored(self, tmp_path):
        """3 saat önceki bir 'exit' alarmı, yeni açılan pozisyonu doğduğu anda
        kapatmamalı."""
        ledger = _ledger(tmp_path, mode="active", exit_action="be")
        sp = _fake_sp(opened_minutes_ago=1.0)
        engine = _exit_engine(ledger, sp)
        ledger.ingest("BTCUSDT", "exit", None, "luxso_exit", ts=time.time() - 3600)

        await engine._apply_tv_event_exits()

        engine.exits.force_breakeven.assert_not_awaited()

    async def test_opposing_structure_triggers_exit(self, tmp_path):
        ledger = _ledger(tmp_path, mode="active", exit_action="be")
        sp = _fake_sp(direction=Direction.LONG)
        engine = _exit_engine(ledger, sp)
        ledger.ingest("BTCUSDT", "choch", Direction.SHORT, "pac_choch")

        await engine._apply_tv_event_exits()

        engine.exits.force_breakeven.assert_awaited_once()

    async def test_same_direction_structure_does_not_trigger_exit(self, tmp_path):
        ledger = _ledger(tmp_path, mode="active", exit_action="be")
        sp = _fake_sp(direction=Direction.LONG)
        engine = _exit_engine(ledger, sp)
        ledger.ingest("BTCUSDT", "trend", Direction.LONG, "luxso_trend")

        await engine._apply_tv_event_exits()

        engine.exits.force_breakeven.assert_not_awaited()

    async def test_untracked_symbol_event_does_nothing(self, tmp_path):
        ledger = _ledger(tmp_path, mode="active", exit_action="be")
        engine = _exit_engine(ledger, None)
        ledger.ingest("BTCUSDT", "exit", None, "luxso_exit")

        await engine._apply_tv_event_exits()

        engine.exits.force_breakeven.assert_not_awaited()


# ===========================================================================
# 6) ExitManager.force_breakeven — mevcut BE mekanizması
# ===========================================================================

def _exit_manager(sp, *, replace_ok=True, be_margin_pct=0.05) -> ExitManager:
    manager = object.__new__(ExitManager)
    manager._positions = {sp.position.symbol: sp}
    manager._closing = set()
    manager.cfg = SimpleNamespace(scalper_tv_events_be_margin_pct=be_margin_pct)
    manager.logger = MagicMock()
    manager.pm = SimpleNamespace(replace_stop_loss=AsyncMock(return_value=replace_ok))
    return manager


class TestForceBreakeven:
    async def test_moves_stop_to_breakeven_without_flag_side_effects(self):
        sp = _fake_sp()
        manager = _exit_manager(sp)

        ok = await manager.force_breakeven("BTCUSDT", reason="TV olayı: exit")

        assert ok is True
        manager.pm.replace_stop_loss.assert_awaited_once()
        assert manager.pm.replace_stop_loss.await_args.args[1] == 101.0
        assert sp.position.current_stoploss == 101.0
        # D4 muafiyetini/chandelier'i sessizce açmamalı
        assert sp.trailing_active is False
        assert sp.tp1_done is False

    async def test_never_loosens_an_already_tighter_stop(self):
        sp = _fake_sp()
        sp.position.current_stoploss = 102.0  # BE'den (101) DAHA koruyucu (LONG)
        manager = _exit_manager(sp)

        ok = await manager.force_breakeven("BTCUSDT", reason="TV olayı: exit")

        assert ok is True
        manager.pm.replace_stop_loss.assert_not_awaited()
        assert sp.position.current_stoploss == 102.0

    async def test_skips_while_close_is_being_finalized(self):
        sp = _fake_sp()
        manager = _exit_manager(sp)
        manager._closing.add("BTCUSDT")

        assert await manager.force_breakeven("BTCUSDT", reason="x") is False
        manager.pm.replace_stop_loss.assert_not_awaited()

    async def test_returns_false_when_replace_fails(self):
        sp = _fake_sp()
        manager = _exit_manager(sp, replace_ok=False)

        assert await manager.force_breakeven("BTCUSDT", reason="x") is False
        assert sp.position.current_stoploss == 95.0  # eski SL korunur


# ===========================================================================
# 7) Telemetri — /scalper/status
# ===========================================================================

class TestStatusTelemetry:
    async def test_status_without_engine_still_reports_ledger(
        self, monkeypatch, tv_ledger
    ):
        monkeypatch.setattr(main_module, "scalper_engine", None)
        tv_ledger.ingest("BTCUSDT", "choch", Direction.LONG, "pac_choch")

        status = await main_module.scalper_status()

        assert status["tv_events"]["symbols"]["BTCUSDT"]["structure"] == "BULL"

    def test_engine_snapshot_helper_is_fail_open(self, tmp_path):
        engine = ScalperEngine.__new__(ScalperEngine)
        engine.logger = MagicMock()
        assert engine._tv_events_snapshot() == {}

        engine.tv_events = _ledger(tmp_path)
        assert "counters" in engine._tv_events_snapshot()


# ===========================================================================
# 8) D19a — düşmanca inceleme düzeltmelerinin regresyonu
# ===========================================================================
# Her sınıf bir bulguyu kapatır. Bulgu metinleri docs/DECISIONS.md D19a'da.


class TestEventSourceCannotVoteEntry:
    """A — olay kaynağı `kind=entry` ile GİRİŞ OYU VEREMEZ (422).

    Mekanizma: `kind` yokluğunun varsayılanı "entry"dir. Bir çıkış alarmının
    mesajından `kind` belirteci düşerse (yazım hatası, iç içe JSON, TV
    şablonunda unutma) istek sessizce bir sağlama OYUNA dönüşür ve gövdedeki
    `src=luxso_exit`/`pac_choch` YENİ BİR KAYNAK olarak sayılır — LuxAlgo
    ailesi tek başına 2/2 kotayı doldurup POZİSYON AÇTIRABİLİR.
    """

    @pytest.mark.parametrize(
        "src", ["luxso_exit", "luxso_trend", "pac_choch", "algopro_tp1"]
    )
    async def test_body_src_event_source_without_kind_is_422(
        self, webhook_ready, tv_ledger, src
    ):
        request = _FakeRequest(
            f"secret={SECRET} src={src} BTCUSDT buy".encode(), {"src": "luxso"}
        )
        with pytest.raises(HTTPException) as exc:
            await main_module.tradingview_webhook(request)
        assert exc.value.status_code == 422
        webhook_ready.external_signal.assert_not_awaited()
        assert tv_ledger.snapshot()["counters"][
            "rejected_entry_from_event_source"
        ] == 1

    @pytest.mark.parametrize(
        "src", ["luxso_exit", "luxso_trend", "pac_choch", "algopro_tp1"]
    )
    async def test_query_src_event_source_without_kind_is_422(
        self, webhook_ready, tv_ledger, src
    ):
        request = _FakeRequest(f"secret={SECRET} BTCUSDT buy".encode(), {"src": src})
        with pytest.raises(HTTPException) as exc:
            await main_module.tradingview_webhook(request)
        assert exc.value.status_code == 422
        webhook_ready.external_signal.assert_not_awaited()

    async def test_explicit_kind_entry_from_event_source_is_422(
        self, webhook_ready, tv_ledger
    ):
        """`kind=entry` AÇIKÇA yazılsa bile olay kaynağı oy veremez."""
        request = _FakeRequest(
            f"secret={SECRET} src=pac_choch kind=entry BTCUSDT buy".encode(), {}
        )
        with pytest.raises(HTTPException) as exc:
            await main_module.tradingview_webhook(request)
        assert exc.value.status_code == 422

    async def test_allowlist_downgrade_does_not_bypass_the_check(
        self, webhook_ready, tv_ledger, monkeypatch
    ):
        """Allowlist dışı bir olay kaynağı `tv`ye eşlenip korumadan SIYRILAMAZ."""
        monkeypatch.setattr(
            main_module.settings, "tv_source_allowlist", "luxosc,luxso,tv"
        )
        request = _FakeRequest(
            f"secret={SECRET} BTCUSDT buy".encode(), {"src": "pac_choch"}
        )
        with pytest.raises(HTTPException) as exc:
            await main_module.tradingview_webhook(request)
        assert exc.value.status_code == 422
        webhook_ready.external_signal.assert_not_awaited()

    async def test_event_source_never_reaches_confluence_vote(
        self, webhook_ready, tv_ledger, monkeypatch
    ):
        """Olay kaynağı TvConfluence.vote()'a HİÇ girmez (ne oy ne sayım)."""
        votes: list = []
        monkeypatch.setattr(main_module.settings, "tv_confluence_required", 2)
        monkeypatch.setattr(
            main_module,
            "_tv_confluence",
            lambda: SimpleNamespace(
                vote=lambda *a, **kw: votes.append(a)
                or {"triggered": False, "sources": []}
            ),
        )
        # (a) kind düşmüş çıkış alarmı → 422, oy YOK
        with pytest.raises(HTTPException):
            await main_module.tradingview_webhook(
                _FakeRequest(
                    f"secret={SECRET} src=luxso_exit BTCUSDT buy".encode(), {}
                )
            )
        # (b) düzgün çıkış olayı → olay yolu, oy YOK
        await main_module.tradingview_webhook(
            _FakeRequest(
                f"secret={SECRET} src=luxso_exit kind=exit BTCUSDT".encode(), {}
            )
        )
        assert votes == []
        webhook_ready.external_signal.assert_not_awaited()

    async def test_normal_entry_alarm_is_not_a_false_positive(
        self, webhook_ready, tv_ledger
    ):
        """Mevcut 49 alarmın kaynakları (luxosc/luxso/algopro/botv3) etkilenmez."""
        for src in ("luxosc", "luxso", "algopro", "botv3", "tv"):
            webhook_ready.external_signal.reset_mock()
            result = await main_module.tradingview_webhook(
                _FakeRequest(
                    f"secret={SECRET} BTCUSDT Bullish Confirmation".encode(),
                    {"src": src},
                )
            )
            assert result["accepted"] is True
            webhook_ready.external_signal.assert_awaited_once()


class TestLosingPositionNeverBreakeven:
    """B — zararda BE = stop'u piyasanın TERS tarafına koymak.

    `position_manager._replace_stop_loss` Binance'ten -2021 alınca
    "koruma kararı" olarak `_emergency_close` çağırır (satır 593-608) ve
    kapatma başarısız olursa `UnprotectedPositionError` fırlatır. Yani
    "yalnız stop sıkışır, geri alınabilir" sanılan `be` ayarı zararda fiilen
    PİYASA EMRİYLE KAPANIŞA dönüşürdü.
    """

    async def test_losing_long_skips_be_and_sends_no_order(self, tmp_path):
        ledger = _ledger(tmp_path, mode="active", exit_action="be")
        sp = _fake_sp(direction=Direction.LONG, current_price=99.0)  # BE=101 → ZARARDA
        engine = _exit_engine(ledger, sp)
        ledger.ingest("BTCUSDT", "exit", None, "luxso_exit")

        await engine._apply_tv_event_exits()

        # `force_breakeven` ÇAĞRILIR (kendi kapılarını uygular) ama borsaya
        # HİÇBİR emir gitmez — D19a-2 bulgu 1'in doğru sıralaması budur.
        assert engine._replace_calls == []
        engine.exits._handle_closed.assert_not_awaited()
        engine.client._request_with_retry.assert_not_awaited()
        counters = ledger.snapshot()["counters"]
        assert counters["exits_skipped_losing"] == 1
        assert counters["exits_noop"] == 1
        assert counters["exits_applied"] == 0     # dokunulmamış pozisyon "uygulandı" DEĞİL
        assert any("zararda" in line for line in engine._logs)

    async def test_losing_short_skips_be(self, tmp_path):
        ledger = _ledger(tmp_path, mode="active", exit_action="be")
        # SHORT: BE=101, fiyat 103 → ZARARDA
        sp = _fake_sp(direction=Direction.SHORT, current_price=103.0)
        engine = _exit_engine(ledger, sp)
        ledger.ingest("BTCUSDT", "exit", None, "luxso_exit")

        await engine._apply_tv_event_exits()

        assert engine._replace_calls == []
        assert ledger.snapshot()["counters"]["exits_skipped_losing"] == 1

    async def test_margin_keeps_a_price_hugging_be_out_of_profit(self, tmp_path):
        """Tick/spread gürültüsünde sınıra teğet fiyat 'kârda' sayılmaz."""
        ledger = _ledger(tmp_path, mode="active", exit_action="be", be_margin_pct=0.5)
        sp = _fake_sp(direction=Direction.LONG, current_price=101.2)  # pay 0.505
        engine = _exit_engine(ledger, sp)
        ledger.ingest("BTCUSDT", "exit", None, "luxso_exit")

        await engine._apply_tv_event_exits()

        assert engine._replace_calls == []

    async def test_stop_already_at_breakeven_is_never_market_closed(self, tmp_path):
        """D19a-2 bulgu 1: stopu BE'de olan KOŞUCU piyasadan kapatılamaz.

        TP1 dolmuş, SL girişte, chandelier iz sürüyor (D4 reaper muafiyeti).
        Fiyat BE'nin altına çekilirse "zararda" görünür — ama BE'ye çekilecek
        bir şey YOKTUR (stop zaten orada) ve `EXIT_LOSING=close` bu pozisyonu
        piyasa emriyle kapatmamalıdır.
        """
        ledger = _ledger(tmp_path, mode="active", exit_action="be", exit_losing="close")
        sp = _fake_sp(direction=Direction.LONG, current_price=100.0)
        sp.position.current_stoploss = 101.0      # = BE
        sp.trailing_active = True
        sp.tp1_done = True
        engine = _exit_engine(ledger, sp)
        ledger.ingest("BTCUSDT", "exit", None, "luxso_exit")

        await engine._apply_tv_event_exits()

        engine.exits._handle_closed.assert_not_awaited()   # piyasa kapanışı YOK
        assert engine._replace_calls == []                 # yeni SL emri de YOK
        counters = ledger.snapshot()["counters"]
        assert counters["exits_closed_losing"] == 0
        # Borsaya HİÇBİR istek gitmedi → `applied` DEĞİL `noop` (D19a-2 R2-3)
        assert counters["exits_applied"] == 0
        assert counters["exits_noop"] == 1

    async def test_unknown_price_is_treated_as_unsafe_and_retried(self, tmp_path):
        """`side_ok is None` = "bilinmiyor" — "ele alındı" DEĞİL (D19a-2 #2).

        Geçici bir ticker hatası olayı KALICI olarak yutmamalı: hiçbir emir
        gönderilmez ama olay tüketilmez, sonraki turlarda yeniden denenir.
        """
        ledger = _ledger(tmp_path, mode="active", exit_action="be", exit_losing="close")
        sp = _fake_sp(direction=Direction.LONG)
        sp.position.current_price = None  # fiyat okunamadı
        engine = _exit_engine(ledger, sp)
        ledger.ingest("BTCUSDT", "exit", None, "luxso_exit")

        await engine._apply_tv_event_exits()

        assert engine._replace_calls == []
        # `close` politikası bile EKSİK VERİYLE geri alınamaz emir göndermez
        engine.exits._handle_closed.assert_not_awaited()
        assert ledger.consumed_seq("BTCUSDT")["exit"] == 0      # TÜKETİLMEDİ
        assert ledger.snapshot()["counters"]["exits_failed"] == 1

        # fiyat geri geldi ve pozisyon kârda → olay hâlâ uygulanabilir
        sp.position.current_price = 105.0
        sp.price_ts = time.monotonic()
        await engine._apply_tv_event_exits()
        assert engine._replace_calls == [("BTCUSDT", 101.0)]

    async def test_stale_price_is_not_treated_as_profit(self, tmp_path):
        """Bayat `current_price` "kârda" hükmü VERMEZ (D19a-2 #5).

        `position.current_price` yalnız ticker okuması BAŞARILI olduğunda
        güncellenir; birkaç tur hata verirse alan sessizce bayatlar ve
        -2021 → `_emergency_close` yolunu açardı.
        """
        ledger = _ledger(tmp_path, mode="active", exit_action="be")
        sp = _fake_sp(direction=Direction.LONG, current_price=102.0)
        sp.price_ts = time.monotonic() - 120.0   # 2 dakikalık bayat fiyat
        engine = _exit_engine(ledger, sp)
        ledger.ingest("BTCUSDT", "exit", None, "luxso_exit")

        await engine._apply_tv_event_exits()

        assert engine._replace_calls == []
        assert ledger.snapshot()["counters"]["exits_failed"] == 1

    async def test_losing_close_policy_uses_reduce_only_path(self, tmp_path):
        ledger = _ledger(tmp_path, mode="active", exit_action="be", exit_losing="close")
        sp = _fake_sp(direction=Direction.LONG, current_price=99.0)
        engine = _exit_engine(ledger, sp)
        ledger.ingest("BTCUSDT", "exit", None, "luxso_exit")

        await engine._apply_tv_event_exits()

        assert engine._replace_calls == []
        engine.exits._handle_closed.assert_awaited_once()
        kwargs = engine.exits._handle_closed.await_args.kwargs
        assert kwargs["forced_exit_reason"] == "TV_EVENT"
        assert engine.client.get_position_risk.await_args.kwargs["force_fresh"] is True
        assert ledger.snapshot()["counters"]["exits_closed_losing"] == 1

    async def test_profitable_position_still_gets_be(self, tmp_path):
        ledger = _ledger(tmp_path, mode="active", exit_action="be")
        sp = _fake_sp(direction=Direction.LONG, current_price=105.0)
        engine = _exit_engine(ledger, sp)
        ledger.ingest("BTCUSDT", "exit", None, "luxso_exit")

        await engine._apply_tv_event_exits()

        engine.exits.force_breakeven.assert_awaited_once()
        assert ledger.snapshot()["counters"]["exits_skipped_losing"] == 0

    async def test_exit_manager_never_sends_the_2021_order(self):
        """ExitManager katmanı da kendini korur: `replace_stop_loss` ÇAĞRILMAZ.

        -2021 yolu (→ `_emergency_close`) buradan ulaşılamaz hale gelir;
        çağıranın (motor) unutması yeterli olmasın diye çift kontrol.
        """
        sp = _fake_sp(direction=Direction.LONG, current_price=99.0)
        manager = _exit_manager(sp)

        ok = await manager.force_breakeven("BTCUSDT", reason="TV olayı: exit")

        assert ok is False
        manager.pm.replace_stop_loss.assert_not_awaited()
        assert sp.position.current_stoploss == 95.0

    async def test_breakeven_side_ok_matrix(self):
        long_sp = _fake_sp(direction=Direction.LONG, current_price=102.0)
        assert _exit_manager(long_sp).breakeven_side_ok("BTCUSDT") is True
        long_sp.position.current_price = 100.0
        assert _exit_manager(long_sp).breakeven_side_ok("BTCUSDT") is False
        short_sp = _fake_sp(direction=Direction.SHORT, current_price=100.0)
        assert _exit_manager(short_sp).breakeven_side_ok("BTCUSDT") is True
        short_sp.position.current_price = 102.0
        assert _exit_manager(short_sp).breakeven_side_ok("BTCUSDT") is False
        short_sp.position.current_price = 0.0
        assert _exit_manager(short_sp).breakeven_side_ok("BTCUSDT") is None
        assert _exit_manager(long_sp).breakeven_side_ok("ETHUSDT") is None


class TestUnprotectedPositionLatch:
    """C — TV çıkış dalında `UnprotectedPositionError` YUTULMAZ (D10 deseni)."""

    async def test_latches_entry_halt_and_does_not_swallow(self, tmp_path):
        ledger = _ledger(tmp_path, mode="active", exit_action="be")
        sp = _fake_sp(current_price=105.0)
        engine = _exit_engine(ledger, sp)
        engine.exits.force_breakeven = AsyncMock(
            side_effect=UnprotectedPositionError("BTCUSDT: stop geçildi")
        )
        ledger.ingest("BTCUSDT", "exit", None, "luxso_exit")

        await engine._apply_tv_event_exits()

        engine._latch_entry_halt.assert_awaited_once()
        kwargs = engine._latch_entry_halt.await_args.kwargs
        assert kwargs["source"] == "TV olay çıkışı"
        assert isinstance(
            engine._latch_entry_halt.await_args.args[0], UnprotectedPositionError
        )
        assert ledger.snapshot()["counters"]["exits_failed"] == 1

    async def test_latched_event_is_consumed_not_retried_forever(self, tmp_path):
        ledger = _ledger(tmp_path, mode="active", exit_action="be")
        sp = _fake_sp(current_price=105.0)
        engine = _exit_engine(ledger, sp)
        engine.exits.force_breakeven = AsyncMock(
            side_effect=UnprotectedPositionError("x")
        )
        ledger.ingest("BTCUSDT", "exit", None, "luxso_exit")

        await engine._apply_tv_event_exits()
        await engine._apply_tv_event_exits()

        engine._latch_entry_halt.assert_awaited_once()

    async def test_other_exceptions_stay_fail_open(self, tmp_path):
        """Sıradan hata koruma döngüsünü DÜŞÜRMEZ ve halt latch'i TETİKLEMEZ."""
        ledger = _ledger(tmp_path, mode="active", exit_action="be")
        sp = _fake_sp(current_price=105.0)
        engine = _exit_engine(ledger, sp)
        engine.exits.force_breakeven = AsyncMock(side_effect=RuntimeError("boom"))
        ledger.ingest("BTCUSDT", "exit", None, "luxso_exit")

        await engine._apply_tv_event_exits()

        engine._latch_entry_halt.assert_not_awaited()
        assert ledger.snapshot()["counters"]["exits_failed"] == 1


class TestPersistentConsumption:
    """D — tüketim imleçleri DEFTERDE (restart'ta olay yeniden tetiklenmez)."""

    async def test_consumed_cursor_survives_restart(self, tmp_path):
        ledger = _ledger(tmp_path, mode="active", exit_action="be")
        sp = _fake_sp(current_price=105.0)
        engine = _exit_engine(ledger, sp)
        ledger.ingest("BTCUSDT", "exit", None, "luxso_exit")
        await engine._apply_tv_event_exits()
        engine.exits.force_breakeven.assert_awaited_once()

        # RESTART: yeni defter aynı dosyadan yüklenir, yeni motor RAM'i BOŞ
        restarted_ledger = _ledger(tmp_path, mode="active", exit_action="be")
        restarted = _exit_engine(restarted_ledger, _fake_sp(current_price=105.0))
        await restarted._apply_tv_event_exits()

        restarted.exits.force_breakeven.assert_not_awaited()
        assert restarted_ledger.consumed_seq("BTCUSDT")["exit"] > 0

    async def test_failed_action_is_retried_then_dropped(self, tmp_path):
        ledger = _ledger(tmp_path, mode="active", exit_action="be")
        sp = _fake_sp(current_price=105.0)
        engine = _exit_engine(ledger, sp)
        engine.exits.force_breakeven = AsyncMock(return_value=False)
        ledger.ingest("BTCUSDT", "exit", None, "luxso_exit")

        for _ in range(5):
            await engine._apply_tv_event_exits()

        assert engine.exits.force_breakeven.await_count == TvEvents.max_attempts()
        assert ledger.snapshot()["counters"]["exits_failed"] == TvEvents.max_attempts()
        assert any("bırakıldı" in line for line in engine._logs)

    async def test_failed_action_retry_survives_restart(self, tmp_path):
        ledger = _ledger(tmp_path, mode="active", exit_action="be")
        engine = _exit_engine(ledger, _fake_sp(current_price=105.0))
        engine.exits.force_breakeven = AsyncMock(return_value=False)
        ledger.ingest("BTCUSDT", "exit", None, "luxso_exit")
        await engine._apply_tv_event_exits()
        assert ledger.attempt_count("BTCUSDT", "exit", ledger.latest_seq("BTCUSDT")["exit"]) == 1

        restarted_ledger = _ledger(tmp_path, mode="active", exit_action="be")
        seq = restarted_ledger.latest_seq("BTCUSDT")["exit"]
        assert restarted_ledger.attempt_count("BTCUSDT", "exit", seq) == 1

    def test_counters_and_since_persist(self, tmp_path):
        ledger = _ledger(tmp_path)
        ledger.ingest("BTCUSDT", "exit", None, "luxso_exit")
        ledger.note("would_exit")
        snapshot = ledger.snapshot()

        restarted = _ledger(tmp_path)
        restored = restarted.snapshot()
        assert restored["counters"]["ingested"] == 1
        assert restored["counters"]["would_exit"] == 1
        assert restored["counters_since"] == snapshot["counters_since"]

    def test_v1_state_file_is_upgraded_not_discarded(self, tmp_path):
        """v1 dosyası (imleçsiz) atılırsa restart eski olayı yeniden tetiklerdi."""
        path = tmp_path / "tv_events.json"
        now = time.time()
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "seq": 7,
                    "counters": {"ingested": 3},
                    "symbols": {
                        "BTCUSDT": {
                            "structures": {
                                "pac_choch": {
                                    "structure": "BEAR",
                                    "kind": "choch",
                                    "ts": now,
                                    "seq": 7,
                                }
                            },
                            "last_exit": None,
                            "last_event": None,
                            "updated_ts": now,
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        ledger = _ledger(tmp_path)
        assert ledger.fresh_gate_structures("BTCUSDT")[0]["structure"] == "BEAR"
        assert ledger.consumed_seq("BTCUSDT") == {"exit": 0, "structure": 0}
        assert ledger.latest_seq("BTCUSDT")["structure"] == 7

    async def test_off_action_advances_cursor_persistently(self, tmp_path):
        ledger = _ledger(tmp_path, mode="active", exit_action="off")
        engine = _exit_engine(ledger, _fake_sp(current_price=105.0))
        ledger.ingest("BTCUSDT", "exit", None, "luxso_exit")

        await engine._apply_tv_event_exits()

        assert ledger.consumed_seq("BTCUSDT")["exit"] > 0
        restarted = _ledger(tmp_path, mode="active", exit_action="be")
        engine2 = _exit_engine(restarted, _fake_sp(current_price=105.0))
        await engine2._apply_tv_event_exits()
        engine2.exits.force_breakeven.assert_not_awaited()


class TestAllowlistIndependenceAndHealth:
    """E — `.env`'de `TV_SOURCE_ALLOWLIST` açıkken kanal SESSİZCE ÖLMEZ."""

    def test_config_health_reports_missing_event_sources(self, tmp_path):
        ledger = _ledger(tmp_path, tv_source_allowlist="luxosc,luxso,tv")
        health = ledger.config_health()
        assert health["allowlist_ok"] is False
        assert health["allowlist_missing"] == [
            "algopro_tp1", "luxso_exit", "luxso_trend", "pac_choch"
        ]
        assert any("TV_SOURCE_ALLOWLIST" in w for w in health["warnings"])

    def test_config_health_is_quiet_when_defaults_apply(self, tmp_path):
        ledger = _ledger(tmp_path)
        health = ledger.config_health()
        assert health["allowlist_ok"] is True
        assert health["gate_enabled"] is True
        assert health["warnings"] == []

    def test_log_config_health_emits_warnings(self, tmp_path):
        warnings: list = []
        cfg = _CfgProxy(
            tv_source_allowlist="luxosc,luxso,tv",
            scalper_tv_events_mode="shadow",
            scalper_tv_events_max_age_min=240.0,
            scalper_tv_events_gate_sources="pac_choch",
        )
        ledger = TvEvents(
            cfg,
            state_path=str(tmp_path / "tv_events.json"),
            logger=SimpleNamespace(
                warning=lambda msg, *a, **kw: warnings.append(str(msg)),
                info=lambda *a, **kw: None,
                error=lambda *a, **kw: None,
                debug=lambda *a, **kw: None,
            ),
        )
        ledger.log_config_health()
        assert len(warnings) == 1
        assert "TV olay kanalı" in warnings[0]

    def test_off_mode_does_not_nag(self, tmp_path):
        ledger = _ledger(tmp_path, mode="off", tv_source_allowlist="luxosc")
        assert ledger.config_health()["warnings"] == []

    async def test_event_path_keeps_body_tag_even_outside_allowlist(
        self, webhook_ready, tv_ledger, monkeypatch
    ):
        """Olay yolu allowlist'ten BAĞIMSIZ: etiket `pac_choch` KALIR."""
        monkeypatch.setattr(
            main_module.settings, "tv_source_allowlist", "luxosc,luxso,tv"
        )
        result = await main_module.tradingview_webhook(
            _FakeRequest(
                f"secret={SECRET} src=pac_choch kind=choch bearish BTCUSDT".encode(),
                {"src": "luxso"},
            )
        )
        assert result["source"] == "pac_choch"
        assert result["source_allowlisted"] is False
        assert result["structure"] == "BEAR"
        assert (
            tv_ledger.symbol_state("BTCUSDT")["structures"]["pac_choch"]["structure"]
            == "BEAR"
        )
        webhook_ready.external_signal.assert_not_awaited()

    async def test_non_entry_kind_never_falls_into_entry_path(
        self, webhook_ready, tv_ledger, monkeypatch
    ):
        monkeypatch.setattr(main_module.settings, "tv_source_allowlist", "")
        for kind, body in (
            ("exit", "src=luxso_exit kind=exit BTCUSDT"),
            ("tp1", "src=algopro_tp1 kind=tp1 BTCUSDT"),
            ("choch", "src=pac_choch kind=choch bearish BTCUSDT"),
            ("trend", "src=luxso_trend kind=trend down BTCUSDT"),
        ):
            result = await main_module.tradingview_webhook(
                _FakeRequest(f"secret={SECRET} {body}".encode(), {"src": "luxso"})
            )
            assert result["routed"] == "event"
            assert result["kind"] == kind
        webhook_ready.external_signal.assert_not_awaited()

    async def test_status_exposes_health_fields(self, monkeypatch, tv_ledger):
        monkeypatch.setattr(main_module, "scalper_engine", None)
        status = await main_module.scalper_status()
        tv = status["tv_events"]
        assert set(
            ["allowlist_ok", "allowlist_missing", "gate_enabled", "window_open",
             "event_sources", "exit_losing", "persist", "symbol_allowlist"]
        ) <= set(tv.keys())
        assert tv["persist"]["ok"] is True


class TestMixedAndSymbolAllowlist:
    """F — MIXED kapıyı UYGULAMAZ; olay yolu D7 sembol allowlist'ini uygular."""

    async def _gate_run(self, ledger):
        symbol_reservations.clear()
        try:
            engine = _gate_engine(ledger)
            await engine._evaluate_symbol("BTCUSDT", [_AlwaysLongStrategy()])
            return engine
        finally:
            symbol_reservations.clear()

    async def test_mixed_structure_does_not_block_entry(self, tmp_path):
        ledger = _ledger(tmp_path, mode="active")
        ledger.ingest("BTCUSDT", "choch", Direction.LONG, "pac_choch")
        ledger.ingest("BTCUSDT", "trend", Direction.SHORT, "luxso_trend")
        engine = await self._gate_run(ledger)

        engine.executor.try_open.assert_awaited_once()
        assert engine.executor.rejects == {}
        counters = ledger.snapshot()["counters"]
        assert counters["blocked"] == 0
        assert counters["would_block"] == 0
        assert counters["mixed_skipped"] == 1
        assert any("ÇELİŞİYOR" in line for line in engine._logs)

    async def test_mixed_is_still_visible_in_telemetry(self, tmp_path):
        ledger = _ledger(tmp_path, mode="active")
        ledger.ingest("BTCUSDT", "choch", Direction.LONG, "pac_choch")
        ledger.ingest("BTCUSDT", "trend", Direction.SHORT, "luxso_trend")
        state = ledger.symbol_state("BTCUSDT")
        assert state["structure"] == "MIXED"
        assert state["structure_source"] == "luxso_trend,pac_choch"

    async def test_mixed_structure_does_not_trigger_exit(self, tmp_path):
        ledger = _ledger(tmp_path, mode="active", exit_action="be")
        engine = _exit_engine(ledger, _fake_sp(current_price=105.0))
        ledger.ingest("BTCUSDT", "choch", Direction.LONG, "pac_choch")
        ledger.ingest("BTCUSDT", "trend", Direction.SHORT, "luxso_trend")

        await engine._apply_tv_event_exits()

        engine.exits.force_breakeven.assert_not_awaited()
        engine.exits._handle_closed.assert_not_awaited()
        assert ledger.snapshot()["counters"]["mixed_skipped"] >= 1

    async def test_mixed_resolves_when_one_source_expires(self, tmp_path):
        """Çelişki geçicidir: eski kaynak bayatlayınca kapı yeniden çalışır."""
        ledger = _ledger(tmp_path, mode="active", max_age_min=10.0)
        ledger.ingest(
            "BTCUSDT", "choch", Direction.LONG, "pac_choch", ts=time.time() - 11 * 60
        )
        ledger.ingest("BTCUSDT", "trend", Direction.SHORT, "luxso_trend")
        engine = await self._gate_run(ledger)

        engine.executor.try_open.assert_not_awaited()
        assert engine.executor.rejects.get("tv_structure_gate") == 1

    async def test_event_path_does_not_record_symbol_outside_tv_allowlist(
        self, webhook_ready, tv_ledger, monkeypatch
    ):
        """200 + `applied: false` — GİRİŞ yolundaki aynı kapıyla simetrik.

        `engine.external_signal` de aynı ayarı 200 + `accepted: false` ile
        uygular; aynı sembolde kurulu iki alarmdan biri TV'de yeşil diğeri
        kırmızı görünmemeli. 422 yalnız BİÇİM hataları içindir.
        """
        monkeypatch.setattr(
            main_module.settings, "scalper_tv_symbol_allowlist", "BTCUSDT,ETHUSDT"
        )
        result = await main_module.tradingview_webhook(
            _FakeRequest(
                f"secret={SECRET} src=luxso_exit kind=exit XRPUSDT".encode(), {}
            )
        )
        assert result["routed"] == "event"
        assert result["applied"] is False
        assert result["reason"] == "symbol_allowlist"
        assert tv_ledger.symbols() == []
        assert tv_ledger.snapshot()["counters"]["rejected_symbol_allowlist"] == 1

    async def test_dry_run_does_not_count_symbol_allowlist_rejection(
        self, webhook_ready, tv_ledger, monkeypatch
    ):
        """`dry_run` HİÇBİR yan etki bırakmaz — sayaç da bir yan etkidir."""
        monkeypatch.setattr(
            main_module.settings, "scalper_tv_symbol_allowlist", "BTCUSDT"
        )
        before = json.dumps(tv_ledger.snapshot()["counters"], sort_keys=True)
        result = await main_module.tradingview_webhook(
            _FakeRequest(
                f"secret={SECRET} src=luxso_exit kind=exit XRPUSDT".encode(),
                {"dry_run": "1"},
            )
        )
        assert result["applied"] is False
        assert json.dumps(tv_ledger.snapshot()["counters"], sort_keys=True) == before

    async def test_event_path_accepts_symbol_inside_tv_allowlist(
        self, webhook_ready, tv_ledger, monkeypatch
    ):
        monkeypatch.setattr(
            main_module.settings, "scalper_tv_symbol_allowlist", "BTCUSDT,ETHUSDT"
        )
        result = await main_module.tradingview_webhook(
            _FakeRequest(
                f"secret={SECRET} src=luxso_exit kind=exit ETHUSDT".encode(), {}
            )
        )
        assert result["routed"] == "event"
        assert tv_ledger.pending_exit("ETHUSDT") is not None

    def test_empty_symbol_allowlist_allows_everything(self, tmp_path):
        ledger = _ledger(tmp_path, scalper_tv_symbol_allowlist="")
        assert ledger.symbol_allowed("DOGEUSDT") is True


class TestHeaderRunScanning:
    """G1 — `src=`/`kind=` YALNIZ satır başı belirteç koşusundan okunur."""

    def test_free_text_tokens_are_not_read(self):
        """BotV3'ün gerçek gövdesi: `{{strategy.order.alert_message}}` çıktısı."""
        raw = (
            "BUY on BTCUSDT | TF: 5 | Price: 64210.5 | "
            "note: kind=exit src=luxso_exit"
        )
        assert main_module.resolve_tv_body_fields(raw) == {}

    def test_tokens_at_line_start_are_read(self):
        raw = "src=luxso_exit kind=exit\nBTCUSDT free text kind=entry"
        assert main_module.resolve_tv_body_fields(raw) == {
            "src": "luxso_exit",
            "kind": "exit",
        }

    def test_run_stops_at_first_free_text_token(self):
        raw = "src=luxso_exit {{ticker}} kind=entry"
        assert main_module.resolve_tv_body_fields(raw) == {"src": "luxso_exit"}

    def test_colon_separator_is_accepted(self):
        assert main_module.resolve_tv_body_fields("src:pac_choch kind:choch X") == {
            "src": "pac_choch",
            "kind": "choch",
        }

    def test_json_data_wrapper_is_read(self):
        raw = json.dumps(
            {
                "secret": SECRET,
                "symbol": "BTCUSDT",
                "data": {"src": "pac_choch", "kind": "choch"},
            }
        )
        assert main_module.resolve_tv_body_fields(raw, secret=SECRET) == {
            "src": "pac_choch",
            "kind": "choch",
        }

    def test_deeper_nested_json_is_not_read(self):
        raw = json.dumps(
            {
                "secret": SECRET,
                "symbol": "BTCUSDT",
                "meta": {"src": "pac_choch", "kind": "choch"},
            }
        )
        assert main_module.resolve_tv_body_fields(raw, secret=SECRET) == {}

    async def test_nested_json_event_alarm_cannot_open_a_position(
        self, webhook_ready, tv_ledger
    ):
        """İç içe JSON `kind`i düşürür → giriş oyu; olay kaynağı 422 alır."""
        raw = json.dumps(
            {
                "secret": SECRET,
                "symbol": "BTCUSDT",
                "side": "buy",
                "meta": {"src": "luxso_exit", "kind": "exit"},
            }
        )
        with pytest.raises(HTTPException) as exc:
            await main_module.tradingview_webhook(
                _FakeRequest(raw.encode(), {"src": "luxso_exit"})
            )
        assert exc.value.status_code == 422
        webhook_ready.external_signal.assert_not_awaited()

    def test_header_run_is_linear_on_a_pathological_body(self):
        """ReDoS koruması: 8192 baytlık en kötü girdi milisaniyelerde biter."""
        raw = "a=" + "b" * 4000 + " " + ("c=d " * 1000)
        started = time.monotonic()
        for _ in range(20):
            main_module.resolve_tv_body_fields(raw[:8192])
        assert time.monotonic() - started < 1.0

    @pytest.mark.parametrize(
        "raw",
        [
            "Confirmation Bullish | LTCUSDT.P | 1",
            "BUY on BTCUSDT | TF: 5 | Price: 64210.5",
            "LuxAlgo Bullish Confirmation BTCUSDT.P",
        ],
    )
    def test_real_entry_alarm_bodies_stay_entry(self, raw):
        assert main_module.resolve_tv_body_fields(raw) == {}
        assert main_module.resolve_tv_kind(None) == "entry"


class TestSecretBeforeParsing:
    """G2 — secret doğrulaması gövde ayrıştırmasından ve HER 422'den ÖNCE."""

    async def test_bad_secret_beats_invalid_kind_422(self, webhook_ready, tv_ledger):
        request = _FakeRequest(b"secret=yanlis src=luxso_exit kind=exitt BTCUSDT", {})
        with pytest.raises(HTTPException) as exc:
            await main_module.tradingview_webhook(request)
        assert exc.value.status_code == 403
        assert "kind" not in str(exc.value.detail)

    async def test_bad_secret_beats_symbol_422(self, webhook_ready, tv_ledger):
        request = _FakeRequest(b"secret=yanlis src=pac_choch kind=choch bearish", {})
        with pytest.raises(HTTPException) as exc:
            await main_module.tradingview_webhook(request)
        assert exc.value.status_code == 403

    async def test_bad_secret_beats_event_source_entry_422(
        self, webhook_ready, tv_ledger
    ):
        request = _FakeRequest(b"secret=yanlis BTCUSDT buy", {"src": "luxso_exit"})
        with pytest.raises(HTTPException) as exc:
            await main_module.tradingview_webhook(request)
        assert exc.value.status_code == 403
        assert tv_ledger.snapshot()["counters"][
            "rejected_entry_from_event_source"
        ] == 0

    async def test_reset_endpoint_requires_secret(self, webhook_ready, tv_ledger):
        tv_ledger.ingest("BTCUSDT", "exit", None, "luxso_exit")
        with pytest.raises(HTTPException) as exc:
            await main_module.tv_events_reset(_FakeRequest(b"", {"secret": "yanlis"}))
        assert exc.value.status_code == 403
        assert tv_ledger.symbols() == ["BTCUSDT"]


class TestEventSymbolValidationAndPruning:
    """G3 — olay yolunda KATI sembol biçimi + defter budaması."""

    async def test_malformed_symbol_rejected_422(self, webhook_ready, tv_ledger):
        raw = json.dumps(
            {
                "secret": SECRET,
                "symbol": "'; DROP--USDT",
                "src": "luxso_exit",
                "kind": "exit",
            }
        )
        with pytest.raises(HTTPException) as exc:
            await main_module.tradingview_webhook(_FakeRequest(raw.encode(), {}))
        assert exc.value.status_code == 422
        assert tv_ledger.symbols() == []

    @pytest.mark.parametrize(
        "given,expected",
        [
            ("BTCUSDT", "BTCUSDT"),
            ("BINANCE:ETHUSDT.P", "ETHUSDT"),
            ("1000pepeusdt", "1000PEPEUSDT"),
        ],
    )
    async def test_real_symbols_still_accepted(
        self, webhook_ready, tv_ledger, given, expected
    ):
        raw = json.dumps(
            {"secret": SECRET, "symbol": given, "src": "luxso_exit", "kind": "exit"}
        )
        result = await main_module.tradingview_webhook(_FakeRequest(raw.encode(), {}))
        assert result["symbol"] == expected

    def test_symbol_ledger_is_bounded(self, tmp_path):
        ledger = _ledger(tmp_path)
        limit = tv_events_module._MAX_SYMBOLS
        for i in range(limit + 10):
            ledger.ingest(f"SYM{i}USDT", "exit", None, "luxso_exit", ts=1000.0 + i)
        assert len(ledger.symbols()) <= limit
        # en yeni sembol KORUNUR, en eskiler düşer
        assert f"SYM{limit + 9}USDT" in ledger.symbols()
        assert "SYM0USDT" not in ledger.symbols()

    def test_structure_sources_are_bounded(self, tmp_path):
        ledger = _ledger(tmp_path)
        limit = tv_events_module._MAX_STRUCTURE_SOURCES
        for i in range(limit + 5):
            ledger.ingest(
                "BTCUSDT", "choch", Direction.LONG, f"src{i}", ts=1000.0 + i
            )
        assert len(ledger.symbol_state("BTCUSDT")["structures"]) <= limit


class TestViaSubSource:
    """G4 — aynı `src`i paylaşan alt-kaynaklar: SON OLAY KAZANIR, MIXED yok."""

    def test_two_sub_sources_do_not_create_mixed(self, tmp_path):
        ledger = _ledger(tmp_path)
        ledger.ingest("BTCUSDT", "trend", Direction.LONG, "luxso_trend", via="catcher")
        ledger.ingest("BTCUSDT", "trend", Direction.SHORT, "luxso_trend", via="tracer")
        state = ledger.symbol_state("BTCUSDT")
        assert state["structure"] == "BEAR"       # son olay kazandı
        assert state["structures"]["luxso_trend"]["via"] == "tracer"
        assert len(state["structures"]) == 1

    async def test_via_is_parsed_and_stripped_from_direction_scan(
        self, webhook_ready, tv_ledger
    ):
        result = await main_module.tradingview_webhook(
            _FakeRequest(
                f"secret={SECRET} src=luxso_trend kind=trend via=tracer "
                f"down ETHUSDT".encode(),
                {},
            )
        )
        assert result["via"] == "tracer"
        assert result["direction"] == "SHORT"
        assert result["structure"] == "BEAR"

    def test_via_absent_is_none(self, tmp_path):
        ledger = _ledger(tmp_path)
        ledger.ingest("BTCUSDT", "trend", Direction.LONG, "luxso_trend")
        assert ledger.symbol_state("BTCUSDT")["structures"]["luxso_trend"]["via"] is None


class TestZeroMeansClosed:
    """G5 — `MAX_AGE_MIN=0` ve boş `GATE_SOURCES` = KAPALI (sessiz kanal)."""

    def test_max_age_zero_closes_the_window(self, tmp_path):
        ledger = _ledger(tmp_path, max_age_min=0.0)
        ledger.ingest("BTCUSDT", "choch", Direction.SHORT, "pac_choch")
        ledger.ingest("BTCUSDT", "exit", None, "luxso_exit")
        assert ledger.window_open() is False
        assert ledger.fresh_gate_structures("BTCUSDT") == []
        assert ledger.pending_exit("BTCUSDT") is None
        assert ledger.config_health()["gate_enabled"] is False

    def test_empty_gate_sources_disable_the_gate(self, tmp_path):
        ledger = _ledger(tmp_path, gate_sources="")
        ledger.ingest("BTCUSDT", "choch", Direction.SHORT, "pac_choch")
        assert ledger.fresh_gate_structures("BTCUSDT") == []
        assert ledger.structure_verdict("BTCUSDT")[0] == "NONE"
        assert ledger.config_health()["gate_enabled"] is False

    async def test_zero_window_never_blocks_entry(self, tmp_path):
        ledger = _ledger(tmp_path, mode="active", max_age_min=0.0)
        ledger.ingest("BTCUSDT", "choch", Direction.SHORT, "pac_choch")
        symbol_reservations.clear()
        try:
            engine = _gate_engine(ledger)
            await engine._evaluate_symbol("BTCUSDT", [_AlwaysLongStrategy()])
            engine.executor.try_open.assert_awaited_once()
        finally:
            symbol_reservations.clear()

    def _settings(self, **overrides):
        values = dict(
            binance_api_key="x", binance_api_secret="x",
            telegram_bot_token="x", telegram_chat_id="x",
            openai_api_key="x", gemini_api_key="x", deepseek_api_key="x",
            jwt_secret="x",
        )
        values.update(overrides)
        return Settings(_env_file=None, **values)

    def test_active_that_can_do_nothing_is_fail_fast(self):
        """`active` + kapı yok + çıkış yok = sessizce ölü kanal → ValueError."""
        with pytest.raises(ValueError, match="HİÇBİR ŞEY yapamaz"):
            self._settings(
                scalper_tv_events_mode="active",
                scalper_tv_events_gate_sources="",
                scalper_tv_events_exit="off",
            )

    def test_active_with_zero_window_is_fail_fast(self):
        """Pencere kapalıysa kapı DA çıkış DA ölür — kaynak dolu olsa bile."""
        with pytest.raises(ValueError, match="HİÇBİR ŞEY yapamaz"):
            self._settings(
                scalper_tv_events_mode="active", scalper_tv_events_max_age_min=0.0
            )

    def test_active_exit_only_configuration_is_allowed(self):
        """Kapı kaynağı yok ama `exit`/`tp1` komutlarına uy — GEÇERLİ terfi adımı.

        `gate_sources` YALNIZ yapı olaylarını süzer; `TvEvents.pending_exit`
        ona hiç bakmaz (INTEGRATIONS §7.4 "kaynak kapsamı farkı").
        """
        s = self._settings(
            scalper_tv_events_mode="active",
            scalper_tv_events_gate_sources="",
            scalper_tv_events_exit="close",
        )
        assert s.scalper_tv_events_gate_sources == ""

    def test_shadow_without_gate_sources_is_allowed(self):
        s = self._settings(
            scalper_tv_events_mode="shadow",
            scalper_tv_events_gate_sources="",
            scalper_tv_events_exit="off",
        )
        assert s.scalper_tv_events_gate_sources == ""

    def test_invalid_exit_losing_is_fail_fast(self):
        with pytest.raises(ValueError, match="EXIT_LOSING"):
            self._settings(scalper_tv_events_exit_losing="skipp")

    def test_negative_be_margin_is_fail_fast(self):
        with pytest.raises(ValueError, match="BE_MARGIN"):
            self._settings(scalper_tv_events_be_margin_pct=-1.0)

    def test_zero_max_age_is_a_valid_configuration(self):
        s = self._settings(scalper_tv_events_max_age_min=0.0)
        assert s.scalper_tv_events_max_age_min == 0.0


class TestPerTickActionLimit:
    """G6 — TV çıkışlarında TUR BAŞINA 1 aksiyon (safety turu şişmesin)."""

    async def test_only_one_action_per_tick(self, tmp_path):
        ledger = _ledger(tmp_path, mode="active", exit_action="be")
        first = _fake_sp(symbol="BTCUSDT", current_price=105.0)
        second = _fake_sp(symbol="ETHUSDT", current_price=105.0)
        engine = _exit_engine(ledger, first, extra_positions=[second])
        ledger.ingest("BTCUSDT", "exit", None, "luxso_exit")
        ledger.ingest("ETHUSDT", "exit", None, "luxso_exit")

        await engine._apply_tv_event_exits()
        assert engine.exits.force_breakeven.await_count == 1

        await engine._apply_tv_event_exits()
        assert engine.exits.force_breakeven.await_count == 2
        symbols = {c.args[0] for c in engine.exits.force_breakeven.await_args_list}
        assert symbols == {"BTCUSDT", "ETHUSDT"}

    async def test_deferred_symbol_event_is_not_consumed(self, tmp_path):
        ledger = _ledger(tmp_path, mode="active", exit_action="be")
        first = _fake_sp(symbol="BTCUSDT", current_price=105.0)
        second = _fake_sp(symbol="ETHUSDT", current_price=105.0)
        engine = _exit_engine(ledger, first, extra_positions=[second])
        ledger.ingest("BTCUSDT", "exit", None, "luxso_exit")
        ledger.ingest("ETHUSDT", "exit", None, "luxso_exit")

        await engine._apply_tv_event_exits()
        assert ledger.consumed_seq("ETHUSDT")["exit"] == 0


class TestResetAndPersistHealth:
    """G7 — dosyayı silmek yetmez: reset endpoint'i + kalıcılık sağlığı."""

    async def test_reset_clears_ram_and_disk(self, webhook_ready, tv_ledger, tmp_path):
        tv_ledger.ingest("BTCUSDT", "exit", None, "luxso_exit")
        tv_ledger.ingest("ETHUSDT", "choch", Direction.LONG, "pac_choch")

        result = await main_module.tv_events_reset(
            _FakeRequest(b"", {"secret": SECRET})
        )

        assert result["reset"] is True
        assert result["cleared_symbols"] == 2
        assert tv_ledger.symbols() == []
        assert result["snapshot"]["symbols"] == {}
        on_disk = json.loads((tmp_path / "tv_events.json").read_text(encoding="utf-8"))
        assert on_disk["symbols"] == {}
        assert SECRET not in json.dumps(result)

    async def test_dry_run_validates_without_touching_the_ledger(
        self, webhook_ready, tv_ledger
    ):
        result = await main_module.tradingview_webhook(
            _FakeRequest(
                f"secret={SECRET} src=pac_choch kind=choch bearish BTCUSDT".encode(),
                {"dry_run": "1"},
            )
        )
        assert result["dry_run"] is True
        assert result["routed"] == "event"
        assert result["kind"] == "choch"
        assert result["direction"] == "SHORT"
        assert tv_ledger.symbols() == []
        assert tv_ledger.snapshot()["counters"]["ingested"] == 0

    def test_persist_failure_is_rate_limited_and_visible(self, tmp_path):
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        warnings: list = []
        cfg = _CfgProxy(
            scalper_tv_events_mode="shadow",
            scalper_tv_events_max_age_min=240.0,
            scalper_tv_events_gate_sources="pac_choch",
        )
        ledger = TvEvents(
            cfg,
            state_path=str(blocker / "tv_events.json"),
            logger=SimpleNamespace(
                warning=lambda msg, *a, **kw: warnings.append(str(msg)),
                info=lambda *a, **kw: None,
                error=lambda *a, **kw: None,
                debug=lambda *a, **kw: None,
            ),
        )
        for _ in range(5):
            ledger.ingest("BTCUSDT", "exit", None, "luxso_exit")

        assert len(warnings) == 1                    # log seli YOK
        snap = ledger.snapshot()
        assert snap["persist"]["ok"] is False
        assert snap["persist"]["errors"] >= 5
        assert snap["persist"]["last_error"]
        # RAM defteri çalışmaya devam eder (fail-open)
        assert ledger.pending_exit("BTCUSDT") is not None

    def test_reset_reports_persist_result(self, tmp_path):
        ledger = _ledger(tmp_path)
        ledger.ingest("BTCUSDT", "exit", None, "luxso_exit")
        assert ledger.reset() == {"cleared_symbols": 1, "persisted": True}


class TestCounterContract:
    """G8 — `gate_hits == would_block + blocked` (gölge ↔ aktif kıyaslanabilir)."""

    async def _gate_run(self, ledger):
        symbol_reservations.clear()
        try:
            engine = _gate_engine(ledger)
            await engine._evaluate_symbol("BTCUSDT", [_AlwaysLongStrategy()])
            return engine
        finally:
            symbol_reservations.clear()

    async def test_shadow_and_active_count_the_same_event(self, tmp_path):
        (tmp_path / "s").mkdir()
        shadow = _ledger(tmp_path / "s", mode="shadow")
        shadow.ingest("BTCUSDT", "choch", Direction.SHORT, "pac_choch")
        await self._gate_run(shadow)
        shadow_counters = shadow.snapshot()["counters"]

        (tmp_path / "a").mkdir()
        active = _ledger(tmp_path / "a", mode="active")
        active.ingest("BTCUSDT", "choch", Direction.SHORT, "pac_choch")
        await self._gate_run(active)
        active_counters = active.snapshot()["counters"]

        assert shadow_counters["gate_hits"] == active_counters["gate_hits"] == 1
        assert shadow_counters["would_block"] == 1 and shadow_counters["blocked"] == 0
        assert active_counters["blocked"] == 1 and active_counters["would_block"] == 0
        for counters in (shadow_counters, active_counters):
            assert counters["gate_hits"] == counters["would_block"] + counters["blocked"]

    async def test_exit_hits_counts_in_both_modes(self, tmp_path):
        shadow = _ledger(tmp_path, mode="shadow", exit_action="be")
        engine = _exit_engine(shadow, _fake_sp(current_price=105.0))
        shadow.ingest("BTCUSDT", "exit", None, "luxso_exit")
        await engine._apply_tv_event_exits()
        counters = shadow.snapshot()["counters"]
        assert counters["exit_hits"] == 1
        assert counters["would_exit"] == 1
        assert counters["exits_attempted"] == 0


# ===========================================================================
# 9) D19a-2 — ikinci düşmanca inceleme turunun regresyonu
# ===========================================================================

class TestMidMessageTokensFailLoud:
    """R1-1 — belirteçler mesajın ORTASINDAysa istek SESSİZCE giriş oyu OLMAZ.

    G1 daraltması yönlendirmeyi başlık koşusuyla sınırlar; bu doğru. Ama
    `BTCUSDT.P src=pac_choch kind=choch bearish` gibi bir gövdede hiçbir
    belirteç okunmaz → `kind` yokluğu "entry"dir → `bearish` sözcüğü yönü
    çözer → **CHoCH alarmı pozisyon açar**. Gövde geneli `src=` taraması bunu
    422'ye çevirir (yönlendirmeyi DEĞİŞTİRMEDEN).
    """

    # DEĞİŞMEZ KURAL: bir olay alarmı ne yaparsa yapsın GİRİŞ OYU OLAMAZ.
    # İki kabul edilebilir sonuç var — belirteçler hâlâ okunabiliyorsa olay
    # yoluna gider, okunamıyorsa 422 ile ölür. `external_signal` ASLA çağrılmaz.
    @pytest.mark.parametrize(
        "raw",
        [
            "BTCUSDT.P src=pac_choch kind=choch bearish",
            "LuxAlgo Exit Signal | BTCUSDT.P | src=luxso_exit kind=exit",
            "🚪 src=luxso_exit kind=exit BTCUSDT.P",
            "Trend Catcher Down BTCUSDT src=luxso_trend kind=trend down",
            "Bearish S-CHOCH detected on BTCUSDT / src=pac_choch kind=choch",
        ],
    )
    async def test_mid_message_event_alarm_never_becomes_an_entry_vote(
        self, webhook_ready, tv_ledger, raw
    ):
        try:
            result = await main_module.tradingview_webhook(
                _FakeRequest(f"secret={SECRET} {raw}".encode(), {"src": "luxso"})
            )
        except HTTPException as exc:
            assert exc.status_code == 422
        else:
            assert result["routed"] == "event"
        webhook_ready.external_signal.assert_not_awaited()

    @pytest.mark.parametrize(
        "raw",
        [
            "LuxAlgo Exit Signal | BTCUSDT.P | src=luxso_exit kind=exit",
            "Trend Catcher Down BTCUSDT src=luxso_trend kind=trend down",
            "Bearish S-CHOCH detected on BTCUSDT / src=pac_choch kind=choch",
        ],
    )
    async def test_unreadable_event_tokens_fail_loud_with_a_hint(
        self, webhook_ready, tv_ledger, raw
    ):
        """Belirteçler başlık koşusundan okunamıyorsa 422 + "BAŞINDA" ipucu."""
        assert main_module.resolve_tv_body_fields(
            f"secret={SECRET} {raw}", secret=SECRET
        ) == {}
        with pytest.raises(HTTPException) as exc:
            await main_module.tradingview_webhook(
                _FakeRequest(f"secret={SECRET} {raw}".encode(), {"src": "luxso"})
            )
        assert exc.value.status_code == 422
        assert "BAŞINDA" in str(exc.value.detail)
        assert tv_ledger.symbols() == []

    async def test_free_text_without_event_source_still_enters(
        self, webhook_ready, tv_ledger
    ):
        """Yanlış-pozitif olmasın: olay KAYNAĞI adı geçmiyorsa giriş sürer."""
        raw = f"secret={SECRET} BUY on ADAUSDT | TF: 5 | msg: take profit at kind=exit"
        result = await main_module.tradingview_webhook(
            _FakeRequest(raw.encode(), {"src": "algopro"})
        )
        assert result["accepted"] is True
        webhook_ready.external_signal.assert_awaited_once()

    def test_mention_scan_ignores_non_event_sources(self):
        assert main_module._tv_body_event_source_mentions("x src=luxosc y") == set()
        assert main_module._tv_body_event_source_mentions("x src=pac_choch y") == {
            "pac_choch"
        }

    def test_mention_scan_strips_secret(self):
        weird = "aaa-src=pac_choch-bbb"
        raw = f"secret={weird} BTCUSDT buy"
        assert main_module._tv_body_event_source_mentions(raw, secret=weird) == set()


class TestKindMentionWithoutSourceFailsLoud:
    """Bütünleşme incelemesi (2026-08-23, high) — R1-1'in KAPATMADIĞI yüz.

    `_tv_body_event_source_mentions` yalnız `src=<olay kaynağı>` arar. Bir
    olay alarmının mesajında `src=` HİÇ YOKSA (ya da yanlış yazıldıysa) ve
    belirteçler başlık koşusu DIŞINDAysa hiçbir şey okunmaz → `kind`
    yokluğu "entry"dir → gövdedeki `bullish`/`bearish` yönü çözer → alarm
    GİRİŞ OYU olur. `TV_CONFLUENCE_REQUIRED=1` iken bu DOĞRUDAN
    `external_signal`dır (pozisyon açar).

    Düzeltmesiz ölçüm (aynı gövdeler, `reject_entry_vote_from_kind_mention`
    devre dışı): 5/5 yerleşimde `external_signal` çağrıldı.
    """

    # `src=` YOK, `kind=<olay kind>` VAR — beş yerleşim.
    MISPLACED = [
        "BTCUSDT.P kind=choch bullish",                  # sembol önce
        "Bullish S-CHOCH kind=choch BTCUSDT.P",          # düz yazı önce
        "BTCUSDT bullish note kind=exit",                # sonda
        "LuxAlgo alert\nBTCUSDT.P kind=trend bullish",   # ikinci satırın ortasında
        "BTCUSDT bearish / kind:choch",                  # `:` ayracı, ortada
    ]

    @pytest.mark.parametrize("raw", MISPLACED)
    async def test_kind_without_src_never_becomes_an_entry_vote(
        self, webhook_ready, tv_ledger, raw
    ):
        with pytest.raises(HTTPException) as exc:
            await main_module.tradingview_webhook(
                _FakeRequest(raw.encode(), {"secret": SECRET})
            )
        assert exc.value.status_code == 422
        assert "BAŞINDA" in str(exc.value.detail)
        webhook_ready.external_signal.assert_not_awaited()
        assert tv_ledger.symbols() == []

    @pytest.mark.parametrize("raw", MISPLACED)
    async def test_confluence_is_never_consulted(
        self, webhook_ready, tv_ledger, monkeypatch, raw
    ):
        """`TV_CONFLUENCE_REQUIRED=2` yolunda da sağlamaya OY YAZILMAZ."""
        votes: list = []
        monkeypatch.setattr(main_module.settings, "tv_confluence_required", 2)
        monkeypatch.setattr(
            main_module,
            "_tv_confluence",
            lambda: SimpleNamespace(
                vote=lambda *a, **kw: votes.append(a)
                or {"triggered": False, "sources": []}
            ),
        )
        with pytest.raises(HTTPException) as exc:
            await main_module.tradingview_webhook(
                _FakeRequest(raw.encode(), {"secret": SECRET})
            )
        assert exc.value.status_code == 422
        assert votes == []
        webhook_ready.external_signal.assert_not_awaited()

    async def test_rejection_is_counted(self, webhook_ready, tv_ledger):
        with pytest.raises(HTTPException):
            await main_module.tradingview_webhook(
                _FakeRequest(self.MISPLACED[0].encode(), {"secret": SECRET})
            )
        assert tv_ledger.snapshot()["counters"]["rejected_entry_kind_mention"] == 1

    async def test_tokens_at_the_head_still_route_to_the_event_path(
        self, webhook_ready, tv_ledger
    ):
        """`src=` olmadan da BAŞTA duran `kind=` OLAY yoluna gider (422 değil)."""
        result = await main_module.tradingview_webhook(
            _FakeRequest(b"kind=choch BTCUSDT.P bullish", {"secret": SECRET})
        )
        assert result["routed"] == "event" and result["kind"] == "choch"
        webhook_ready.external_signal.assert_not_awaited()

    @pytest.mark.parametrize(
        "raw",
        [
            # AlgoPro/BotV3 tek satır biçimi — serbest metninde `kind=` geçse
            # bile GİRİŞ olarak kalır (bugünkü davranış).
            "BUY on ADAUSDT | TF: 5 | msg: take profit at kind=exit",
            "🟢 BUY  | BINANCE:BTCUSDT | TF: 1 | Price: 76556.52 | note kind=tp1",
        ],
    )
    async def test_known_entry_formats_ignore_free_text_kind(
        self, webhook_ready, tv_ledger, raw
    ):
        result = await main_module.tradingview_webhook(
            _FakeRequest(raw.encode(), {"secret": SECRET, "src": "algopro"})
        )
        assert result["accepted"] is True
        webhook_ready.external_signal.assert_awaited_once()

    async def test_json_entry_body_with_deep_kind_is_not_rejected(
        self, webhook_ready, tv_ledger
    ):
        """JSON giriş gövdesi (side/symbol) — derindeki `kind` bugünkü gibi
        okunmaz VE 422 üretmez (G1'in bilinçli kör noktası korunur)."""
        raw = json.dumps(
            {
                "secret": SECRET,
                "symbol": "BTCUSDT",
                "side": "buy",
                "meta": {"kind": "exit"},
            }
        )
        result = await main_module.tradingview_webhook(_FakeRequest(raw.encode(), {}))
        assert result["accepted"] is True

    @pytest.mark.parametrize("raw", ["BTCUSDT BUY", "BINANCE:ETHUSDT.P sell"])
    def test_simple_ticker_side_body_is_a_known_entry_format(self, raw):
        assert main_module._tv_body_is_known_entry_format(raw) is True

    def test_kind_mention_scan_ignores_unknown_kinds(self):
        scan = main_module._tv_body_event_kind_mentions
        assert scan("BTCUSDT kind=momentum bullish") == set()
        assert scan("BTCUSDT kind=entry bullish") == set()
        assert scan("BTCUSDT kind=choch bullish") == {"choch"}

    def test_kind_mention_scan_strips_secret(self):
        weird = "aaa-kind=choch-bbb"
        raw = f"secret={weird} BTCUSDT buy"
        assert main_module._tv_body_event_kind_mentions(raw, secret=weird) == set()

    def test_algopro_fingerprint_is_shared_with_source_resolution(self):
        """TEK parmak izi: kaynak tahmini ve fail-loud kapısı aynı fonksiyon."""
        body = "BUY on ADAUSDT | TF: 5 | Price: 1.23"
        assert main_module._tv_body_is_algopro_format(body) is True
        assert main_module.resolve_tv_source(None, body) == ("algopro", False)


class TestDryRunHasNoSideEffects:
    """Bütünleşme incelemesi (2026-08-23, medium) — `?dry_run=1` GİRİŞ yolu.

    `dry_run` yalnız OLAY dalına geçiriliyordu; giriş yolunda SESSİZCE yok
    sayılıyordu. Yani `docs/RUNBOOK.md`'nin "doğrulama" komutu, mesajın
    `kind=` belirteci düşmüşse (ki doğrulamanın sebebi tam da budur)
    sağlamaya GERÇEK bir oy yazıyor ve `external_signal` üzerinden
    GERÇEK EMİR açabiliyordu.
    """

    ENTRY_BODY = b"BTCUSDT BUY"

    @pytest.fixture(autouse=True)
    def _no_follower_bridge(self, monkeypatch):
        self.forwarded: list = []
        monkeypatch.setattr(
            main_module,
            "maybe_forward_algopro_event",
            lambda raw, source: self.forwarded.append(source),
        )

    async def test_entry_dry_run_does_not_open_anything(
        self, webhook_ready, tv_ledger
    ):
        result = await main_module.tradingview_webhook(
            _FakeRequest(self.ENTRY_BODY, {"secret": SECRET, "dry_run": "1"})
        )
        assert result == {
            "dry_run": True,
            "would": {"symbol": "BTCUSDT", "direction": "LONG", "source": "tv"},
        }
        webhook_ready.external_signal.assert_not_awaited()
        assert self.forwarded == []          # takipçi halkası da bir yan etkidir

    async def test_entry_dry_run_does_not_vote(
        self, webhook_ready, tv_ledger, monkeypatch
    ):
        votes: list = []
        monkeypatch.setattr(main_module.settings, "tv_confluence_required", 2)
        monkeypatch.setattr(
            main_module,
            "_tv_confluence",
            lambda: SimpleNamespace(
                vote=lambda *a, **kw: votes.append(a)
                or {"triggered": False, "sources": []}
            ),
        )
        result = await main_module.tradingview_webhook(
            _FakeRequest(self.ENTRY_BODY, {"secret": SECRET, "dry_run": "true"})
        )
        assert result["dry_run"] is True
        assert votes == []
        webhook_ready.external_signal.assert_not_awaited()

    async def test_entry_dry_run_works_without_an_engine(
        self, webhook_ready, tv_ledger, monkeypatch
    ):
        """Doğrulamanın değeri motordan bağımsızdır (olay yoluyla aynı ilke)."""
        monkeypatch.setattr(main_module, "scalper_engine", None)
        result = await main_module.tradingview_webhook(
            _FakeRequest(self.ENTRY_BODY, {"secret": SECRET, "dry_run": "yes"})
        )
        assert result["would"]["symbol"] == "BTCUSDT"

    async def test_entry_dry_run_reports_the_resolved_source(
        self, webhook_ready, tv_ledger
    ):
        result = await main_module.tradingview_webhook(
            _FakeRequest(
                b"BUY on ADAUSDT | TF: 5 | Price: 1.23",
                {"secret": SECRET, "dry_run": "1"},
            )
        )
        assert result["would"] == {
            "symbol": "ADAUSDT",
            "direction": "LONG",
            "source": "algopro",
        }
        assert self.forwarded == []

    async def test_dry_run_422_does_not_forward_to_the_follower(
        self, webhook_ready, tv_ledger
    ):
        """AlgoPro `🎯 TP1 HIT` gövdesi yön taşımaz → 422; köprü ATLANMALI."""
        raw = b"\xf0\x9f\x8e\xaf TP1 HIT | BINANCE:BTCUSDT | TF: 1 | Price: 76583.92"
        with pytest.raises(HTTPException) as exc:
            await main_module.tradingview_webhook(
                _FakeRequest(raw, {"secret": SECRET, "src": "algopro", "dry_run": "1"})
            )
        assert exc.value.status_code == 422
        assert self.forwarded == []

    async def test_without_dry_run_the_same_body_still_enters(
        self, webhook_ready, tv_ledger
    ):
        """Negatif kontrol: bayrak yokken bugünkü davranış birebir sürer."""
        result = await main_module.tradingview_webhook(
            _FakeRequest(self.ENTRY_BODY, {"secret": SECRET})
        )
        assert result["accepted"] is True
        webhook_ready.external_signal.assert_awaited_once()

    async def test_422_without_dry_run_still_forwards(
        self, webhook_ready, tv_ledger
    ):
        """D20 köprüsü bayraksız istekte AYNEN çalışmaya devam eder."""
        raw = b"\xf0\x9f\x8e\xaf TP1 HIT | BINANCE:BTCUSDT | TF: 1 | Price: 76583.92"
        with pytest.raises(HTTPException):
            await main_module.tradingview_webhook(
                _FakeRequest(raw, {"secret": SECRET, "src": "algopro"})
            )
        assert self.forwarded == ["algopro"]


class TestColonSeparatorIsProseSafe:
    """R1-2 — `:` düz yazı noktalamasıdır; TANINMAYAN değeri 422 ÜRETMEZ."""

    @pytest.mark.parametrize(
        "raw",
        [
            "Kind: Bullish Reversal BTCUSDT.P",
            "kind: reversal | BTCUSDT.P | buy",
            "Source: LuxAlgo | BTCUSDT.P | Bullish",
            "Src: TradingView BTCUSDT.P bullish",
        ],
    )
    def test_prose_colon_tokens_are_ignored(self, raw):
        assert main_module.resolve_tv_body_fields(raw) == {}

    async def test_prose_colon_entry_alarm_still_opens(self, webhook_ready, tv_ledger):
        result = await main_module.tradingview_webhook(
            _FakeRequest(
                f"secret={SECRET} Kind: Bullish Reversal BTCUSDT.P".encode(),
                {"src": "luxosc"},
            )
        )
        assert result["accepted"] is True
        assert result["direction"] == "LONG"

    def test_known_colon_values_are_still_routed(self):
        assert main_module.resolve_tv_body_fields("kind:exit src:luxso_exit X") == {
            "src": "luxso_exit",
            "kind": "exit",
        }

    def test_first_acceptable_token_wins_within_the_run(self):
        """Reddedilen bir `:` belirteci, aynı koşudaki gerçek `=` belirtecini
        GÖLGELEMEZ (ilk KABUL EDİLEBİLİR eşleşme alınır)."""
        assert main_module.resolve_tv_body_fields(
            "kind: prose kind=exit src: nope src=luxso_exit X"
        ) == {"src": "luxso_exit", "kind": "exit"}

    def test_equals_separator_stays_strict(self):
        """`=` kasıtlı belirteçtir: tanınmayan değer HÂLÂ 422'dir (D19)."""
        fields = main_module.resolve_tv_body_fields("kind=exitt BTCUSDT")
        assert fields == {"kind": "exitt"}
        with pytest.raises(HTTPException) as exc:
            main_module.resolve_tv_kind(fields["kind"])
        assert exc.value.status_code == 422


class TestDirectionScanStripsResolvedValues:
    """R1-9 — JSON gövdede `src` değeri yön taramasına SIZMAZ."""

    @pytest.mark.parametrize(
        "src", ["pac-bull", "luxso-down", "trend-up", "exit-short"]
    )
    def test_hyphenated_source_never_leaks_direction(self, src):
        raw = json.dumps(
            {"secret": SECRET, "symbol": "BTCUSDT", "src": src, "kind": "exit"}
        )
        assert main_module.resolve_tv_event_direction(
            main_module._tv_payload(raw), raw, SECRET
        ) is None

    def test_nested_data_source_also_stripped(self):
        raw = json.dumps(
            {
                "secret": SECRET,
                "symbol": "BTCUSDT",
                "data": {"src": "pac-bull", "kind": "exit"},
            }
        )
        assert main_module.resolve_tv_event_direction(
            main_module._tv_payload(raw), raw, SECRET
        ) is None

    def test_real_direction_still_resolved(self):
        raw = json.dumps(
            {
                "secret": SECRET,
                "symbol": "BTCUSDT",
                "src": "pac_choch",
                "kind": "choch",
                "side": "bearish",
            }
        )
        assert main_module.resolve_tv_event_direction(
            main_module._tv_payload(raw), raw, SECRET
        ) == Direction.SHORT


class TestShadowPredictsActive:
    """R2-4 — gölge, aktifte HİÇBİR ŞEY olmayacak olayı ayırt eder."""

    async def test_shadow_marks_noop_for_losing_position(self, tmp_path):
        ledger = _ledger(tmp_path, mode="shadow", exit_action="be")
        engine = _exit_engine(ledger, _fake_sp(current_price=99.0))
        ledger.ingest("BTCUSDT", "exit", None, "luxso_exit")

        await engine._apply_tv_event_exits()

        counters = ledger.snapshot()["counters"]
        assert counters["would_exit"] == 1
        assert counters["would_exit_noop"] == 1
        assert any("HİÇBİR ŞEY olmazdı" in line for line in engine._logs)

    async def test_shadow_marks_real_action_for_profitable_position(self, tmp_path):
        ledger = _ledger(tmp_path, mode="shadow", exit_action="be")
        engine = _exit_engine(ledger, _fake_sp(current_price=105.0))
        ledger.ingest("BTCUSDT", "exit", None, "luxso_exit")

        await engine._apply_tv_event_exits()

        counters = ledger.snapshot()["counters"]
        assert counters["would_exit"] == 1
        assert counters["would_exit_noop"] == 0
        assert engine._replace_calls == []          # gölge emir GÖNDERMEZ

    async def test_shadow_predicts_noop_for_stop_already_at_breakeven(self, tmp_path):
        """Kârda ama stop ZATEN BE'de: aktifte de emir gitmez → gölge noop."""
        ledger = _ledger(tmp_path, mode="shadow", exit_action="be")
        sp = _fake_sp(current_price=105.0)
        sp.position.current_stoploss = 101.0
        engine = _exit_engine(ledger, sp)
        ledger.ingest("BTCUSDT", "exit", None, "luxso_exit")

        await engine._apply_tv_event_exits()

        assert ledger.snapshot()["counters"]["would_exit_noop"] == 1

    async def test_shadow_does_not_mark_noop_when_close_policy_would_act(
        self, tmp_path
    ):
        """`EXIT_LOSING=close` ile zararda pozisyon KAPATILIRDI → noop DEĞİL."""
        ledger = _ledger(
            tmp_path, mode="shadow", exit_action="be", exit_losing="close"
        )
        engine = _exit_engine(ledger, _fake_sp(current_price=99.0))
        ledger.ingest("BTCUSDT", "exit", None, "luxso_exit")

        await engine._apply_tv_event_exits()

        counters = ledger.snapshot()["counters"]
        assert counters["would_exit"] == 1
        assert counters["would_exit_noop"] == 0

    async def test_shadow_and_active_agree_on_the_noop(self, tmp_path):
        """Gölgedeki `would_exit_noop` ile aktifteki `exits_noop` aynı olayı sayar."""
        (tmp_path / "s").mkdir()
        (tmp_path / "a").mkdir()
        shadow = _ledger(tmp_path / "s", mode="shadow", exit_action="be")
        active = _ledger(tmp_path / "a", mode="active", exit_action="be")
        for ledger in (shadow, active):
            engine = _exit_engine(ledger, _fake_sp(current_price=99.0))
            ledger.ingest("BTCUSDT", "exit", None, "luxso_exit")
            await engine._apply_tv_event_exits()
        assert shadow.snapshot()["counters"]["would_exit_noop"] == 1
        assert active.snapshot()["counters"]["exits_noop"] == 1
        assert active.snapshot()["counters"]["exits_applied"] == 0


class TestPruningProtectsOpenPositions:
    """R2-6 — açık pozisyonlu sembol alarm selinde defterden DÜŞMEZ."""

    async def test_tracked_symbol_survives_a_flood(self, tmp_path):
        ledger = _ledger(tmp_path, mode="active", exit_action="be")
        engine = _exit_engine(ledger, _fake_sp(current_price=105.0))
        ledger.ingest("BTCUSDT", "exit", None, "luxso_exit", ts=time.time() - 3600)

        # motor koruma listesini bildirir (safety turunun ilk işi)
        await engine._apply_tv_event_exits()
        ledger.mark_consumed("BTCUSDT", "exit", 0)  # imleç ilerlemesin diye no-op

        limit = tv_events_module._MAX_SYMBOLS
        for i in range(limit + 16):
            ledger.ingest(f"FL{i}USDT", "exit", None, "luxso_exit", ts=time.time())

        assert "BTCUSDT" in ledger.symbols()
        assert len(ledger.symbols()) <= limit

    def test_protect_is_bounded_and_normalised(self, tmp_path):
        ledger = _ledger(tmp_path)
        ledger.protect({" btcusdt ", "ethusdt", ""})
        ledger.ingest("BTCUSDT", "exit", None, "luxso_exit")
        assert "BTCUSDT" in ledger.symbols()
        ledger.protect([f"S{i}USDT" for i in range(200)])
        assert len(ledger._protected) <= tv_events_module._MAX_PROTECTED_SYMBOLS

    def test_protect_tolerates_garbage(self, tmp_path):
        ledger = _ledger(tmp_path)
        ledger.protect(None)          # TypeError yutulur, akış bozulmaz
        ledger.ingest("BTCUSDT", "exit", None, "luxso_exit")
        assert ledger.symbols() == ["BTCUSDT"]


class TestClosedWindowAdvancesCursors:
    """R2-7 — pencere kapalıyken imleçler ilerler (açılınca toplu tetikleme yok)."""

    async def test_zero_window_advances_and_reopening_is_quiet(self, tmp_path):
        closed = _ledger(tmp_path, mode="active", exit_action="be", max_age_min=0.0)
        engine = _exit_engine(closed, _fake_sp(current_price=105.0))
        closed.ingest("BTCUSDT", "exit", None, "luxso_exit")

        await engine._apply_tv_event_exits()

        assert engine._replace_calls == []
        assert closed.consumed_seq("BTCUSDT")["exit"] > 0

        # operatör pencereyi açıyor → BİRİKMİŞ olay toplu tetiklemez
        reopened = _ledger(tmp_path, mode="active", exit_action="be", max_age_min=240.0)
        engine2 = _exit_engine(reopened, _fake_sp(current_price=105.0))
        await engine2._apply_tv_event_exits()
        assert engine2._replace_calls == []


class TestLedgerRobustness:
    """R2-8/9/10 — sayaç yazımı, deneme sıralaması, bozuk yapı satırı."""

    def test_counter_writes_are_debounced(self, tmp_path, monkeypatch):
        ledger = _ledger(tmp_path)
        writes: list = []
        real_persist = ledger._persist

        def _counted(*a, **kw):
            writes.append(1)
            return real_persist(*a, **kw)

        monkeypatch.setattr(ledger, "_persist", _counted)
        for _ in range(50):
            ledger.note("would_block")
        assert ledger.snapshot()["counters"]["would_block"] == 50   # RAM DOĞRU
        assert len(writes) <= 2                                     # disk seli YOK

    def test_ingest_is_never_debounced(self, tmp_path):
        ledger = _ledger(tmp_path)
        ledger.note("would_block")
        ledger.ingest("BTCUSDT", "exit", None, "luxso_exit")
        restarted = _ledger(tmp_path)
        assert restarted.pending_exit("BTCUSDT") is not None
        assert restarted.snapshot()["counters"]["would_block"] == 1  # ingest'e bindi

    def test_attempt_keys_are_pruned_numerically(self, tmp_path):
        ledger = _ledger(tmp_path)
        ledger.ingest("BTCUSDT", "exit", None, "luxso_exit")
        limit = tv_events_module._MAX_ATTEMPT_KEYS
        for seq in range(1, limit + 4):
            ledger.note_attempt("BTCUSDT", "exit", seq)
        kept = sorted(
            int(k.split(":")[1])
            for k in ledger._symbols["BTCUSDT"]["attempts"]
        )
        assert len(kept) <= limit
        assert max(kept) == limit + 3      # EN YENİ deneme korunur
        assert 1 not in kept               # en eski düşer

    def test_corrupt_structure_row_is_dropped(self, tmp_path):
        path = tmp_path / "tv_events.json"
        path.write_text(
            json.dumps(
                {
                    "version": 2,
                    "seq": 3,
                    "symbols": {
                        "BTCUSDT": {
                            "structures": {
                                "pac_choch": {"structure": None, "kind": "choch",
                                              "ts": time.time(), "seq": 3},
                                "luxso_trend": {"structure": "BEAR", "kind": "trend",
                                                "ts": time.time(), "seq": 2},
                            },
                            "consumed": {"exit": 0, "structure": 0},
                            "attempts": {},
                            "updated_ts": time.time(),
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        ledger = _ledger(tmp_path)
        state = ledger.symbol_state("BTCUSDT")
        assert set(state["structures"]) == {"luxso_trend"}
        assert state["structure"] == "BEAR"   # "" HÜKMÜ YOK


class TestCounterAlgebra:
    """Sayaç kimlikleri (INTEGRATIONS §7.5'te belgelenen sözleşme)."""

    async def _drain(self, ledger, sp, ticks=4):
        engine = _exit_engine(ledger, sp)
        for _ in range(ticks):
            await engine._apply_tv_event_exits()
        return engine

    @pytest.mark.parametrize(
        "price,exit_losing",
        [(105.0, "skip"), (99.0, "skip"), (99.0, "close"), (None, "skip")],
    )
    async def test_exit_counter_identities_hold(self, tmp_path, price, exit_losing):
        ledger = _ledger(
            tmp_path, mode="active", exit_action="be", exit_losing=exit_losing
        )
        sp = _fake_sp(current_price=price if price is not None else 102.0)
        if price is None:
            sp.position.current_price = None
        ledger.ingest("BTCUSDT", "exit", None, "luxso_exit")

        await self._drain(ledger, sp)

        c = ledger.snapshot()["counters"]
        assert c["exit_hits"] == c["would_exit"] + c["exits_attempted"]
        assert c["exits_attempted"] == (
            c["exits_applied"] + c["exits_noop"] + c["exits_failed"]
        )

    async def test_shadow_identity_holds(self, tmp_path):
        ledger = _ledger(tmp_path, mode="shadow", exit_action="be")
        sp = _fake_sp(current_price=99.0)
        ledger.ingest("BTCUSDT", "exit", None, "luxso_exit")

        await self._drain(ledger, sp)

        c = ledger.snapshot()["counters"]
        assert c["exit_hits"] == c["would_exit"] + c["exits_attempted"]
        assert c["exits_attempted"] == 0
        assert c["would_exit_noop"] <= c["would_exit"]


# ===========================================================================
# 10) Özellik testleri — iki DEĞİŞMEZ kural, tohumlanmış rastgele gövdelerle
# ===========================================================================

class TestRoutingInvariants:
    """Kanalın iki değişmez kuralı, örnek testlerin kaçırabileceği kombinasyonlarda.

    1. Bir GİRİŞ alarmı, olay-kaynağı koruması yüzünden ASLA yanlışlıkla
       422 almaz (yanlış-pozitif yok).
    2. Bir OLAY alarmı, nereye yazılırsa yazılsın ASLA `external_signal`'a
       ya da `TvConfluence.vote()`'a ULAŞMAZ (sızıntı yok).
    """

    _WORDS = (
        "Bullish Bearish Confirmation Confirmation+ Contrarian Signal TF: Price: "
        "1 5 Buy Sell Trend Catcher Up Down SETUP SUPPORT Exit Hit | - note: msg: "
        "reversal strong weak zone {{ticker}} {{close}}"
    ).split()
    _SYMS = ["BTCUSDT.P", "ETHUSDT", "BINANCE:XRPUSDT.P", "SOLUSDT", "1000PEPEUSDT"]

    async def test_no_entry_alarm_is_ever_falsely_rejected(
        self, webhook_ready, tv_ledger
    ):
        rng = random.Random(7)
        checked = 0
        for i in range(400):
            body = " ".join(rng.choice(self._WORDS) for _ in range(rng.randint(2, 9)))
            body = f"{body} {rng.choice(self._SYMS)} {'buy' if i % 2 else 'sell'}"
            query = {"secret": SECRET}
            src = rng.choice([None, "luxosc", "luxso", "algopro", "botv3", "tv"])
            if src:
                query["src"] = src
            checked += 1
            try:
                await main_module.tradingview_webhook(_FakeRequest(body.encode(), query))
            except HTTPException as exc:
                # İKİ kalkanın da yanlış-pozitifi yasak: olay-kaynağı adı
                # (D19a A) ve `kind=` belirteci (bütünleşme incelemesi).
                for guard in ("Olay kaynağı", "Olay alarmı yanlış şablon"):
                    assert guard not in str(exc.detail), (
                        f"yanlış-pozitif 422 ({guard}): {body!r} {query!r}"
                    )
        assert checked == 400

    async def test_no_event_alarm_ever_reaches_the_entry_path(
        self, webhook_ready, tv_ledger, monkeypatch
    ):
        votes: list = []
        monkeypatch.setattr(main_module.settings, "tv_confluence_required", 2)
        monkeypatch.setattr(
            main_module,
            "_tv_confluence",
            lambda: SimpleNamespace(
                vote=lambda *a, **kw: votes.append(a)
                or {"triggered": False, "sources": []}
            ),
        )
        rng = random.Random(11)
        routed = rejected = 0
        for _ in range(400):
            src = rng.choice(["luxso_exit", "luxso_trend", "pac_choch", "algopro_tp1"])
            kind = rng.choice(["exit", "choch", "trend", "tp1"])
            direction = rng.choice(["bullish", "bearish", "up", "down", ""])
            symbol = rng.choice(self._SYMS)
            plain = symbol.split(":")[-1].replace(".P", "")
            place = rng.choice(["basta", "ortada", "sonda", "json", "data"])
            if place == "json":
                body = json.dumps(
                    {"secret": SECRET, "symbol": plain, "src": src, "kind": kind,
                     "side": direction}
                )
            elif place == "data":
                body = json.dumps(
                    {"secret": SECRET, "symbol": plain,
                     "data": {"src": src, "kind": kind}, "side": direction}
                )
            elif place == "basta":
                body = f"secret={SECRET} src={src} kind={kind} {symbol} {direction}"
            elif place == "ortada":
                body = (
                    f"secret={SECRET} LuxAlgo alert {symbol} src={src} "
                    f"kind={kind} {direction}"
                )
            else:
                body = f"secret={SECRET} {symbol} {direction} note src={src} kind={kind}"
            try:
                result = await main_module.tradingview_webhook(
                    _FakeRequest(body.encode(), {"src": "luxso"})
                )
            except HTTPException as exc:
                assert exc.status_code == 422, body
                rejected += 1
            else:
                assert result["routed"] == "event", body
                routed += 1
        assert routed and rejected                       # iki dal da gezildi
        assert votes == []                               # SAĞLAMAYA HİÇ girmedi
        webhook_ready.external_signal.assert_not_awaited()

    # `src=` DÜŞMÜŞ (ya da yanlış yazılmış) olay alarmları — bütünleşme
    # incelemesi. D19a'nın kalkanı yalnız `src=<olay kaynağı>`na bakıyordu;
    # `src` hiç yoksa kalkan boştu ve gövdedeki yön sözcüğü OY veriyordu.
    _KIND_PLACEMENTS = (
        "{sym} kind={kind} {dir}",                     # sembol önce (ortada)
        "Bullish S-CHOCH kind={kind} {sym} {dir}",     # düz yazı önce
        "{sym} {dir} note kind={kind}",                # sonda
        "LuxAlgo alert\n{sym} kind={kind} {dir}",      # ikinci satırın ortasında
        "kind={kind} {sym} {dir}",                     # BAŞTA (olay yoluna gider)
    )

    async def test_event_alarm_without_src_never_reaches_the_entry_path(
        self, webhook_ready, tv_ledger, monkeypatch
    ):
        votes: list = []
        monkeypatch.setattr(main_module.settings, "tv_confluence_required", 2)
        monkeypatch.setattr(
            main_module,
            "_tv_confluence",
            lambda: SimpleNamespace(
                vote=lambda *a, **kw: votes.append(a)
                or {"triggered": False, "sources": []}
            ),
        )
        rng = random.Random(23)
        routed = rejected = 0
        for _ in range(400):
            template = rng.choice(self._KIND_PLACEMENTS)
            body = template.format(
                sym=rng.choice(self._SYMS),
                kind=rng.choice(["exit", "choch", "trend", "tp1"]),
                dir=rng.choice(["bullish", "bearish", "buy", "sell"]),
            )
            # `?src=` YOK ve gövdede `src=` YOK: kalkanın eski dayanağı boş.
            query = {"secret": SECRET}
            if rng.random() < 0.5:
                query["src"] = rng.choice(["luxosc", "luxso", "algopro"])
            try:
                result = await main_module.tradingview_webhook(
                    _FakeRequest(body.encode(), query)
                )
            except HTTPException as exc:
                assert exc.status_code == 422, body
                rejected += 1
            else:
                assert result["routed"] == "event", body
                routed += 1
        assert routed and rejected
        assert votes == []
        webhook_ready.external_signal.assert_not_awaited()
