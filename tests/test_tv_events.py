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
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

import src.main as main_module
from src.core.config import settings
from src.services.tv_events import TvEvents
from src.strategies.scalper.engine import ScalperEngine
from src.strategies.scalper.exits import ExitManager
from src.strategies.scalper.types import (
    Candle,
    Direction,
    ScalpSignal,
    StrategyContext,
)
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

def _fake_sp(symbol="BTCUSDT", direction=Direction.LONG, opened_minutes_ago=5.0):
    opened = datetime.now(timezone.utc) - timedelta(minutes=opened_minutes_ago)
    return SimpleNamespace(
        symbol=symbol,
        signal=SimpleNamespace(direction=direction),
        plan=SimpleNamespace(breakeven_price=101.0),
        position=SimpleNamespace(
            symbol=symbol,
            opened_at=opened,
            quantity=1.0,
            current_stoploss=95.0,
            side=SimpleNamespace(value="LONG"),
        ),
        trailing_active=False,
        tp1_done=False,
    )


def _exit_engine(ledger: TvEvents, sp) -> ScalperEngine:
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
    engine.exits = SimpleNamespace(
        _positions=positions,
        tracked_symbols=lambda: set(positions.keys()),
        force_breakeven=AsyncMock(return_value=True),
        _handle_closed=AsyncMock(),
    )
    engine.client = SimpleNamespace(
        get_position_risk=AsyncMock(return_value={"positionAmt": "0"}),
        quantize_quantity=AsyncMock(side_effect=lambda s, q: q),
        _request_with_retry=AsyncMock(return_value={}),
    )
    engine._tv_exit_seen = {}
    engine._tv_struct_seen = {}
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

def _exit_manager(sp, *, replace_ok=True) -> ExitManager:
    manager = object.__new__(ExitManager)
    manager._positions = {sp.position.symbol: sp}
    manager._closing = set()
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
        sp.position.current_stoploss = 102.0  # BE'den (101) DAHA koruyucu
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
