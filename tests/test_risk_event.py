"""Risk-olayı kanalı (D10, 2026-08-21) — POST /risk-event.

Sözleşme: haber/olay botları giriş durdur/devam et/her-şeyi-düzleştir
diyebilir. YÖN sinyali GÖNDERMEZ, tv_confluence sağlamasından GEÇMEZ — ayrı
secret (`RISK_EVENT_SECRET`). Halt durumu `state/risk_event_halt.json`'da
tutulur; mevcut `state/scalper_entry_halt.json` (koruma hatası latch'i)
dosyasından BİLİNÇLİ olarak AYRI ve `SCALPER_ENTRY_HALT_ENABLED`
bayrağından BAĞIMSIZDIR (bkz. engine.py `_risk_event_halt_snapshot`,
main.py `risk_event`).

Test yapısı tests/test_tv_signal_bridge.py ile aynı desen: endpoint testleri
`_FakeRequest` çifti + monkeypatch'lenmiş `main_module.scalper_engine` ile
doğrudan `main_module.risk_event(request)` çağırır; motor-seviyesi testler
tests/test_runtime_liveness.py'deki `_make_engine` gibi `object.__new__` ile
ağ/DB kurmadan yalın bir ScalperEngine çifti kurar.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

import src.main as main_module
from src.core.logger import app_logger
from src.strategies.scalper.engine import ScalperEngine
from src.strategies.scalper.exits import ExitManager
from src.strategies.scalper.types import Direction

SECRET = "sup3r-risk-3v3nt-s3cr3t"


# ---------------------------------------------------------------------------
# Ortak test çiftleri
# ---------------------------------------------------------------------------

class _FakeRequest:
    """`Request`'in endpoint'te kullanılan iki yüzeyini taklit eden test çifti."""

    def __init__(self, body: bytes, query: dict | None = None):
        self._body = body
        self.query_params = query or {}

    async def body(self) -> bytes:
        return self._body


def _fake_sp(symbol: str, side: str = "LONG", qty: float = 1.0):
    """`_close_position_market`'ın ihtiyaç duyduğu minimum ScalpPosition çifti."""
    position = SimpleNamespace(symbol=symbol, side=SimpleNamespace(value=side), quantity=qty)
    return SimpleNamespace(position=position)


def _make_engine(halt_path, *, tracked=None) -> ScalperEngine:
    """Ağ/DB istemcisi kurmadan yalın bir ScalperEngine test çifti."""
    engine = object.__new__(ScalperEngine)
    engine.cfg = SimpleNamespace(scalper_tv_symbol_allowlist="")
    engine.logger = MagicMock()
    engine.running = True
    engine._entry_lock = asyncio.Lock()
    engine._opening_symbols = set()
    engine._exchange_ready = True
    engine._exchange_last_success_monotonic = time.monotonic()
    engine._recovery_ready = True
    engine._risk_ready = True
    engine._entry_halted = False
    engine._entry_halt_reason = None
    engine._kill_switch = False
    engine._risk_event_halt_path = halt_path
    engine._risk_event_halt_cache = None

    tracked_positions = dict(tracked or {})
    engine.exits = SimpleNamespace(
        _positions=tracked_positions,
        tracked_symbols=MagicMock(side_effect=lambda: set(tracked_positions.keys())),
        _handle_closed=AsyncMock(),
    )
    engine.executor = SimpleNamespace(
        pending_symbols=MagicMock(return_value=set()),
        cancel_all_pending=AsyncMock(return_value=[]),
    )
    engine.client = SimpleNamespace(
        quantize_quantity=AsyncMock(side_effect=lambda symbol, qty: qty),
        _request_with_retry=AsyncMock(return_value={}),
        get_position_risk=AsyncMock(return_value={"positionAmt": "0"}),
    )
    return engine


# ---------------------------------------------------------------------------
# Motor-seviyesi: halt/resume/flatten/ttl/fail-closed
# ---------------------------------------------------------------------------

class TestRiskEventHaltEngine:
    async def test_halt_blocks_entry_gate_and_external_signal(self, tmp_path):
        halt_path = tmp_path / "risk_event_halt.json"
        engine = _make_engine(halt_path)
        assert engine._entries_ready() is True

        snap = await engine.risk_event_halt(
            reason="savaş çıktı", source="newsbot", ttl_minutes=5
        )
        assert snap["active"] is True
        assert halt_path.exists()
        payload = json.loads(halt_path.read_text(encoding="utf-8"))
        assert payload["reason"] == "savaş çıktı"
        assert payload["source"] == "newsbot"
        assert payload["until_ts"] > time.time()

        # Motor giriş kapısı (_entries_ready — _evaluate_symbol/_scan_tick'in
        # tek kontrol noktası) artık kapalı.
        assert engine._entries_ready() is False

        # TV dış sinyali de AYNI kapıdan geçer, reddedilir.
        result = await engine.external_signal("BTCUSDT", Direction.LONG)
        assert result["accepted"] is False
        assert "risk-event" in result["reason"].lower()

    async def test_ttl_expiry_reallows_entries(self, tmp_path):
        halt_path = tmp_path / "risk_event_halt.json"
        engine = _make_engine(halt_path)
        # API'nin 1dk tabanını atlayıp doğrudan geçmişte bir until_ts yaz —
        # süre dolumunu gerçek uyku olmadan test etmek için.
        engine._persist_risk_event_halt(
            reason="test", source=None, until_ts=time.time() - 5.0
        )
        snap = engine._risk_event_halt_snapshot(force=True)
        assert snap["active"] is False
        assert engine._entries_ready() is True

    async def test_resume_clears_halt_file(self, tmp_path):
        halt_path = tmp_path / "risk_event_halt.json"
        engine = _make_engine(halt_path)
        await engine.risk_event_halt(reason="test", source=None, ttl_minutes=10)
        assert halt_path.exists()

        snap = engine.risk_event_resume()
        assert snap["active"] is False
        assert not halt_path.exists()
        assert engine._entries_ready() is True

    async def test_resume_does_not_touch_entry_halt_flag(self, tmp_path):
        # resume yalnız risk_event_halt_path'i temizler; koruma-hatası
        # scalper_entry_halt latch'i (_entry_halted) AYRI kalır.
        halt_path = tmp_path / "risk_event_halt.json"
        engine = _make_engine(halt_path)
        engine._entry_halted = True
        engine._entry_halt_reason = "UnprotectedPositionError: boo"
        await engine.risk_event_halt(reason="test", source=None, ttl_minutes=10)
        engine.risk_event_resume()
        assert engine._entry_halted is True  # dokunulmadı
        assert engine._entries_ready() is False  # ayrı kilit hâlâ aktif

    def test_corrupt_halt_file_fails_closed(self, tmp_path):
        halt_path = tmp_path / "risk_event_halt.json"
        halt_path.write_text("{ bozuk json", encoding="utf-8")
        engine = _make_engine(halt_path)

        snap = engine._risk_event_halt_snapshot()
        assert snap["active"] is True
        assert "okunamadı" in snap["reason"]
        assert engine._entries_ready() is False

    def test_halt_file_missing_until_ts_fails_closed(self, tmp_path):
        halt_path = tmp_path / "risk_event_halt.json"
        halt_path.write_text(json.dumps({"reason": "x"}), encoding="utf-8")
        engine = _make_engine(halt_path)

        snap = engine._risk_event_halt_snapshot()
        assert snap["active"] is True

    def test_no_halt_file_means_not_active(self, tmp_path):
        halt_path = tmp_path / "does-not-exist.json"
        engine = _make_engine(halt_path)
        snap = engine._risk_event_halt_snapshot()
        assert snap == {"active": False, "reason": None, "source": None, "until_ts": None}


class TestRiskEventFlattenEngine:
    async def test_flatten_closes_all_tracked_positions_and_sets_halt(self, tmp_path):
        halt_path = tmp_path / "risk_event_halt.json"
        tracked = {
            "BTCUSDT": _fake_sp("BTCUSDT", "LONG"),
            "ETHUSDT": _fake_sp("ETHUSDT", "SHORT"),
        }
        engine = _make_engine(halt_path, tracked=tracked)

        result = await engine.risk_event_flatten(
            reason="acil kapat", source="newsbot", ttl_minutes=15
        )

        assert result["flattened"] == ["BTCUSDT", "ETHUSDT"]
        assert result["errors"] == []
        assert result["halt"]["active"] is True
        assert halt_path.exists()

        assert engine.exits._handle_closed.await_count == 2
        for call in engine.exits._handle_closed.await_args_list:
            assert call.kwargs.get("forced_exit_reason") == "RISK_EVENT"

        # Sonrasında giriş kapısı da kapalı — "hiçbir şey hemen yeniden girmesin".
        assert engine._entries_ready() is False

    async def test_flatten_with_no_positions_is_ok_and_idempotent(self, tmp_path):
        halt_path = tmp_path / "risk_event_halt.json"
        engine = _make_engine(halt_path, tracked={})

        result = await engine.risk_event_flatten(
            reason="acil", source=None, ttl_minutes=10
        )
        assert result["flattened"] == []
        assert result["errors"] == []
        assert result["halt"]["active"] is True

        # İkinci çağrı da güvenli (izlenen pozisyon zaten yok).
        result2 = await engine.risk_event_flatten(
            reason="acil-2", source=None, ttl_minutes=10
        )
        assert result2["flattened"] == []
        assert result2["errors"] == []

    async def test_flatten_leaves_unverified_close_tracked_and_reports_error(
        self, tmp_path
    ):
        # Borsa üzerinde kapanış doğrulanamazsa (positionAmt hep != 0)
        # _handle_closed ASLA çağrılmamalı — fail-closed: SL/TP iptal
        # edilmeden pozisyon "kapandı" sayılamaz.
        halt_path = tmp_path / "risk_event_halt.json"
        tracked = {"BTCUSDT": _fake_sp("BTCUSDT", "LONG")}
        engine = _make_engine(halt_path, tracked=tracked)
        engine.client.get_position_risk = AsyncMock(
            return_value={"positionAmt": "1.0"}
        )

        result = await engine.risk_event_flatten(
            reason="acil", source=None, ttl_minutes=10
        )
        assert result["flattened"] == []
        assert len(result["errors"]) == 1
        assert "doğrulanamadı" in result["errors"][0]
        engine.exits._handle_closed.assert_not_awaited()
        # Halt yine de kurulur — kapanış doğrulanamasa da yeniden giriş kapalı kalsın.
        assert result["halt"]["active"] is True


# ---------------------------------------------------------------------------
# Endpoint: auth, boyut/şema doğrulama, aksiyon dağıtımı
# ---------------------------------------------------------------------------

@pytest.fixture
def _risk_event_ready(monkeypatch):
    """Endpoint'in erken-dönüş koşullarını (secret/scalper hazır) aşacak
    minimum global durum. Motor metotları AsyncMock/MagicMock — gerçek işlem
    açmaz/kapatmaz."""
    monkeypatch.setattr(main_module.settings, "risk_event_secret", SECRET)
    fake_engine = MagicMock()
    fake_engine.risk_event_status = MagicMock(
        return_value={
            "active": False, "reason": None, "source": None,
            "until_ts": None, "open_positions": 0,
        }
    )
    fake_engine.risk_event_resume = MagicMock(
        return_value={"active": False, "reason": None, "source": None, "until_ts": None}
    )
    fake_engine.risk_event_halt = AsyncMock(
        return_value={
            "active": True, "reason": "test", "source": "newsbot", "until_ts": 123.0,
        }
    )
    fake_engine.risk_event_flatten = AsyncMock(
        return_value={
            "flattened": ["BTCUSDT"],
            "errors": [],
            "halt": {"active": True, "reason": "acil", "source": None, "until_ts": 456.0},
        }
    )
    monkeypatch.setattr(main_module, "scalper_engine", fake_engine)
    return fake_engine


class TestRiskEventEndpointAuth:
    async def test_missing_secret_403(self, monkeypatch):
        monkeypatch.setattr(main_module.settings, "risk_event_secret", SECRET)
        request = _FakeRequest(json.dumps({"action": "status"}).encode())
        with pytest.raises(HTTPException) as e:
            await main_module.risk_event(request)
        assert e.value.status_code == 403

    async def test_wrong_secret_403(self, monkeypatch):
        monkeypatch.setattr(main_module.settings, "risk_event_secret", SECRET)
        body = json.dumps({"action": "status", "secret": "yanlis"})
        request = _FakeRequest(body.encode())
        with pytest.raises(HTTPException) as e:
            await main_module.risk_event(request)
        assert e.value.status_code == 403

    async def test_disabled_when_secret_not_configured_503(self, monkeypatch):
        monkeypatch.setattr(main_module.settings, "risk_event_secret", "")
        request = _FakeRequest(
            json.dumps({"action": "status", "secret": "whatever"}).encode()
        )
        with pytest.raises(HTTPException) as e:
            await main_module.risk_event(request)
        assert e.value.status_code == 503

    async def test_url_secret_accepted_when_body_omits_it(
        self, monkeypatch, _risk_event_ready
    ):
        request = _FakeRequest(
            json.dumps({"action": "status"}).encode(), {"secret": SECRET}
        )
        result = await main_module.risk_event(request)
        assert result["ok"] is True

    async def test_body_secret_wins_over_wrong_url_secret(self, monkeypatch):
        monkeypatch.setattr(main_module.settings, "risk_event_secret", SECRET)
        body = json.dumps({"action": "status", "secret": "yanlis"})
        request = _FakeRequest(body.encode(), {"secret": SECRET})
        with pytest.raises(HTTPException) as e:
            await main_module.risk_event(request)
        assert e.value.status_code == 403


class TestRiskEventEndpointValidation:
    async def test_unknown_action_422(self, monkeypatch, _risk_event_ready):
        body = json.dumps({"action": "nuke", "secret": SECRET})
        request = _FakeRequest(body.encode())
        with pytest.raises(HTTPException) as e:
            await main_module.risk_event(request)
        assert e.value.status_code == 422

    async def test_body_too_large_422(self, monkeypatch):
        monkeypatch.setattr(main_module.settings, "risk_event_secret", SECRET)
        body = json.dumps(
            {"action": "status", "secret": SECRET, "reason": "x" * 5000}
        ).encode()
        assert len(body) > 4096
        request = _FakeRequest(body)
        with pytest.raises(HTTPException) as e:
            await main_module.risk_event(request)
        assert e.value.status_code == 422

    async def test_reason_too_long_422(self, monkeypatch, _risk_event_ready):
        body = json.dumps(
            {"action": "halt", "secret": SECRET, "reason": "x" * 201}
        )
        request = _FakeRequest(body.encode())
        with pytest.raises(HTTPException) as e:
            await main_module.risk_event(request)
        assert e.value.status_code == 422

    async def test_source_too_long_422(self, monkeypatch, _risk_event_ready):
        body = json.dumps(
            {
                "action": "halt", "secret": SECRET, "reason": "test",
                "source": "x" * 33,
            }
        )
        request = _FakeRequest(body.encode())
        with pytest.raises(HTTPException) as e:
            await main_module.risk_event(request)
        assert e.value.status_code == 422

    async def test_ttl_minutes_out_of_range_422(self, monkeypatch, _risk_event_ready):
        body = json.dumps(
            {"action": "halt", "secret": SECRET, "reason": "test", "ttl_minutes": 5000}
        )
        request = _FakeRequest(body.encode())
        with pytest.raises(HTTPException) as e:
            await main_module.risk_event(request)
        assert e.value.status_code == 422

    async def test_halt_without_reason_422(self, monkeypatch, _risk_event_ready):
        body = json.dumps({"action": "halt", "secret": SECRET})
        request = _FakeRequest(body.encode())
        with pytest.raises(HTTPException) as e:
            await main_module.risk_event(request)
        assert e.value.status_code == 422

    async def test_flatten_without_reason_422(self, monkeypatch, _risk_event_ready):
        body = json.dumps({"action": "flatten", "secret": SECRET})
        request = _FakeRequest(body.encode())
        with pytest.raises(HTTPException) as e:
            await main_module.risk_event(request)
        assert e.value.status_code == 422

    async def test_status_and_resume_do_not_require_reason(
        self, monkeypatch, _risk_event_ready
    ):
        for action in ("status", "resume"):
            body = json.dumps({"action": action, "secret": SECRET})
            request = _FakeRequest(body.encode())
            result = await main_module.risk_event(request)
            assert result["ok"] is True

    async def test_engine_not_ready_503(self, monkeypatch):
        monkeypatch.setattr(main_module.settings, "risk_event_secret", SECRET)
        monkeypatch.setattr(main_module, "scalper_engine", None)
        request = _FakeRequest(json.dumps({"action": "status", "secret": SECRET}).encode())
        with pytest.raises(HTTPException) as e:
            await main_module.risk_event(request)
        assert e.value.status_code == 503


class TestRiskEventEndpointDispatch:
    async def test_status_action_dispatches(self, monkeypatch, _risk_event_ready):
        body = json.dumps({"action": "status", "secret": SECRET})
        request = _FakeRequest(body.encode())
        result = await main_module.risk_event(request)
        assert result["ok"] is True
        assert result["action"] == "status"
        _risk_event_ready.risk_event_status.assert_called_once()

    async def test_halt_action_dispatches_with_defaults(
        self, monkeypatch, _risk_event_ready
    ):
        body = json.dumps({"action": "halt", "secret": SECRET, "reason": "test"})
        request = _FakeRequest(body.encode())
        result = await main_module.risk_event(request)
        assert result["ok"] is True
        assert result["halted_until"] == 123.0
        assert result["flattened"] == []
        _risk_event_ready.risk_event_halt.assert_awaited_once_with(
            reason="test", source=None, ttl_minutes=120
        )

    async def test_halt_action_dispatches_with_explicit_fields(
        self, monkeypatch, _risk_event_ready
    ):
        body = json.dumps(
            {
                "action": "halt", "secret": SECRET, "reason": "savaş",
                "source": "newsbot", "ttl_minutes": 30,
            }
        )
        request = _FakeRequest(body.encode())
        await main_module.risk_event(request)
        _risk_event_ready.risk_event_halt.assert_awaited_once_with(
            reason="savaş", source="newsbot", ttl_minutes=30
        )

    async def test_resume_action_dispatches(self, monkeypatch, _risk_event_ready):
        body = json.dumps({"action": "resume", "secret": SECRET})
        request = _FakeRequest(body.encode())
        result = await main_module.risk_event(request)
        assert result["ok"] is True
        _risk_event_ready.risk_event_resume.assert_called_once()

    async def test_flatten_action_dispatches(self, monkeypatch, _risk_event_ready):
        body = json.dumps({"action": "flatten", "secret": SECRET, "reason": "acil"})
        request = _FakeRequest(body.encode())
        result = await main_module.risk_event(request)
        assert result["ok"] is True
        assert result["flattened"] == ["BTCUSDT"]
        assert result["halted_until"] == 456.0
        _risk_event_ready.risk_event_flatten.assert_awaited_once_with(
            reason="acil", source=None, ttl_minutes=120
        )


# ---------------------------------------------------------------------------
# Düşmanca inceleme düzeltmeleri (2026-08-21, A–J) — regresyon testleri
# ---------------------------------------------------------------------------

class TestRiskEventFlattenHaltFirst:
    """A: halt, kapanış turundan ÖNCE kurulur; tur boyunca giriş kapısı
    tamamen kapalı kalır ve turla eşzamanlı dolan bir pozisyon ikinci
    taramada yakalanır."""

    async def test_entry_gate_closed_throughout_loop_and_second_sweep_catches_concurrent_fill(
        self, tmp_path
    ):
        halt_path = tmp_path / "risk_event_halt.json"
        tracked = {"BTCUSDT": _fake_sp("BTCUSDT", "LONG")}
        engine = _make_engine(halt_path, tracked=tracked)

        gate_samples: list[bool] = []
        injected = {"done": False}

        async def fake_get_position_risk(symbol, force_fresh=False):
            # Doğrulama/boyutlama borsa okumasının HER anında giriş kapısı
            # kapalı olmalı (A) — kapatma turu onlarca saniye sürebilir ve
            # tarama döngüsü bağımsız bir task'tır.
            gate_samples.append(engine._entries_ready())
            if symbol == "BTCUSDT" and not injected["done"]:
                injected["done"] = True
                # Tarama döngüsünün flatten'in ORTASINDA açtığı YENİ pozisyonu
                # simüle eder — ilk anlık görüntüde (tracked_symbols()) yoktu.
                engine.exits._positions["XRPUSDT"] = _fake_sp("XRPUSDT", "SHORT")
            return {"positionAmt": "0"}

        engine.client.get_position_risk = AsyncMock(side_effect=fake_get_position_risk)

        result = await engine.risk_event_flatten(
            reason="test", source=None, ttl_minutes=10
        )

        assert gate_samples, "get_position_risk hiç çağrılmadı — test bir şey doğrulamıyor"
        assert all(sample is False for sample in gate_samples), (
            "giriş kapısı flatten döngüsü sırasında en az bir kez AÇIK bulundu"
        )
        # Turla eşzamanlı dolan XRPUSDT ikinci taramada yakalanmış olmalı.
        assert set(result["flattened"]) == {"BTCUSDT", "XRPUSDT"}
        assert result["errors"] == []
        assert result["halt"]["active"] is True

    async def test_pending_cancel_runs_under_halt_not_before(self, tmp_path):
        # risk_event_halt zaten bekleyen maker'ları halt ALTINDA iptal eder;
        # flatten'ın kendi başına AYRI bir _cancel_pending_for_risk_event
        # çağrısı yapmasına gerek yoktur (A'nın bir parçası olarak kaldırıldı).
        halt_path = tmp_path / "risk_event_halt.json"
        engine = _make_engine(halt_path, tracked={})
        engine.executor.pending_symbols = MagicMock(return_value={"ETHUSDT"})

        await engine.risk_event_flatten(reason="test", source=None, ttl_minutes=10)

        # cancel_all_pending yalnız BİR kez çağrılmış olmalı (risk_event_halt
        # içinden) — iki kez değil.
        assert engine.executor.cancel_all_pending.await_count == 1


class TestClosePositionMarketForceFresh:
    """B: doğrulama döngüsü force_fresh=True okumayla ilerler — bayat 5sn'lik
    pozisyon önbelleği retry'ları öldürmez."""

    async def test_verification_uses_force_fresh_and_detects_close_immediately(
        self, tmp_path
    ):
        halt_path = tmp_path / "risk_event_halt.json"
        sp = _fake_sp("BTCUSDT", "LONG", qty=1.0)
        engine = _make_engine(halt_path, tracked={"BTCUSDT": sp})

        force_fresh_flags: list[bool] = []
        state = {"real_amt": 1.0}

        async def fake_get_position_risk(symbol, force_fresh=False):
            force_fresh_flags.append(force_fresh)
            if force_fresh:
                return {"positionAmt": str(state["real_amt"])}
            # force_fresh=False HER ZAMAN bayat, sıfır-olmayan bir kayıt
            # döndürür — force_fresh kullanılmazsa doğrulama ASLA ilerlemez
            # (5sn'lik snapshot önbelleğinin taklidi).
            return {"positionAmt": "1.0"}

        async def fake_request_with_retry(method, path, params=None, signed=False):
            state["real_amt"] = 0.0  # emir borsada gerçekleşti
            return {}

        engine.client.get_position_risk = AsyncMock(side_effect=fake_get_position_risk)
        engine.client._request_with_retry = AsyncMock(
            side_effect=fake_request_with_retry
        )

        closed = await engine._close_position_market("BTCUSDT", sp)

        assert closed is True
        assert force_fresh_flags, "get_position_risk hiç çağrılmadı"
        assert all(force_fresh_flags), (
            "force_fresh=False ile çağrıldı — bayat önbellek kapanışı asla doğrulayamaz"
        )
        engine.exits._handle_closed.assert_awaited_once()


class TestClosePositionMarketLiveSize:
    """E: reduce-only MARKET boyutu CANLI positionAmt'tan alınır, girişteki
    (kısmi TP sonrası bayatlaşan) sp.position.quantity'den DEĞİL."""

    async def test_partial_tp1_runner_closes_with_live_amount_not_entry_qty(
        self, tmp_path
    ):
        halt_path = tmp_path / "risk_event_halt.json"
        # Giriş dolumu 1.0 idi; TP1 sonrası canlı miktar 0.6 — sp.position.quantity
        # HİÇBİR ZAMAN güncellenmez (exits.py bunu bilerek "filled" referansı
        # olarak kullanır).
        sp = _fake_sp("BTCUSDT", "LONG", qty=1.0)
        engine = _make_engine(halt_path, tracked={"BTCUSDT": sp})

        call_count = {"n": 0}

        async def fake_get_position_risk(symbol, force_fresh=False):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return {"positionAmt": "0.6"}  # boyutlama okuması: canlı miktar
            return {"positionAmt": "0"}  # kapanış sonrası doğrulama

        engine.client.get_position_risk = AsyncMock(side_effect=fake_get_position_risk)

        closed = await engine._close_position_market("BTCUSDT", sp)

        assert closed is True
        submitted = engine.client._request_with_retry.await_args.kwargs["params"]
        assert submitted["quantity"] == pytest.approx(0.6)
        assert submitted["side"] == "SELL"

    async def test_already_flat_position_skips_order_and_finalizes(self, tmp_path):
        # Canlı positionAmt zaten 0 ise emir GÖNDERİLMEMELİ — yalnız normal
        # kapanış yolu (SL/TP temizliği + ledger) çalışmalı.
        halt_path = tmp_path / "risk_event_halt.json"
        sp = _fake_sp("BTCUSDT", "LONG", qty=1.0)
        engine = _make_engine(halt_path, tracked={"BTCUSDT": sp})
        engine.client.get_position_risk = AsyncMock(
            return_value={"positionAmt": "0"}
        )

        closed = await engine._close_position_market("BTCUSDT", sp)

        assert closed is True
        engine.client._request_with_retry.assert_not_awaited()
        engine.exits._handle_closed.assert_awaited_once()


class TestRiskEventHaltRamLatch:
    """D: _persist_risk_event_halt diske YAZAMASA bile halt RAM'de otoriter
    kalır — _entries_ready() False, snapshot active True, persisted False."""

    async def test_halt_survives_unwritable_state_dir(self, tmp_path, monkeypatch):
        halt_path = tmp_path / "risk_event_halt.json"
        engine = _make_engine(halt_path)
        assert engine._entries_ready() is True

        def boom(*args, **kwargs):
            raise OSError(28, "No space left on device (simüle)")

        monkeypatch.setattr("src.strategies.scalper.engine.os.replace", boom)

        snap = await engine.risk_event_halt(
            reason="disk dolu senaryosu", source=None, ttl_minutes=5
        )

        assert snap["active"] is True
        assert snap["persisted"] is False
        assert not halt_path.exists()
        assert engine._entries_ready() is False

    async def test_halt_ram_latch_expires_with_ttl_even_without_file(
        self, tmp_path, monkeypatch
    ):
        halt_path = tmp_path / "risk_event_halt.json"
        engine = _make_engine(halt_path)

        def boom(*args, **kwargs):
            raise OSError("simulated")

        monkeypatch.setattr("src.strategies.scalper.engine.os.replace", boom)

        await engine.risk_event_halt(reason="test", source=None, ttl_minutes=1)
        assert engine._entries_ready() is False

        # RAM latch'i geçmişe çek (gerçek 1dk beklemeden TTL testini yap).
        engine._risk_event_halt_ram["until_ts"] = time.time() - 1.0
        engine._risk_event_halt_cache = None
        assert engine._entries_ready() is True

    async def test_halt_endpoint_reports_active_true_persisted_false_on_write_failure(
        self, monkeypatch, tmp_path
    ):
        # I: main.py yanıtı gerçekliği yansıtmalı — halt RAM'de etkilidir
        # (ok=True) ama restart'ta kaybolacağı görünür olmalı (persisted=False).
        monkeypatch.setattr(main_module.settings, "risk_event_secret", SECRET)
        halt_path = tmp_path / "risk_event_halt.json"
        engine = _make_engine(halt_path)
        monkeypatch.setattr(main_module, "scalper_engine", engine)

        def boom(*args, **kwargs):
            raise OSError("simulated")

        monkeypatch.setattr("src.strategies.scalper.engine.os.replace", boom)

        body = json.dumps({"action": "halt", "secret": SECRET, "reason": "test"})
        request = _FakeRequest(body.encode())
        result = await main_module.risk_event(request)

        assert result["ok"] is True
        assert result["persisted"] is False
        assert engine._entries_ready() is False


class TestRiskEventLoguruBraceSafety:
    """F: reason/source loguru format string'e KWARG olarak GEÇMEZ —
    süslü parantez içeren metin halt/flatten'i 500'e düşürmemeli."""

    async def test_engine_halt_logs_braces_without_crash(self, tmp_path):
        halt_path = tmp_path / "risk_event_halt.json"
        engine = _make_engine(halt_path)
        # Gerçek loguru logger'ı bağla — MagicMock .format() regresyonunu
        # YAKALAMAZ (herhangi bir arg/kwarg'ı sessizce kabul eder).
        engine.logger = app_logger

        snap = await engine.risk_event_halt(
            reason="BTC {halving} haberi", source="bot{1}", ttl_minutes=5
        )

        assert snap["active"] is True

    async def test_endpoint_halt_with_braces_returns_200_not_500(
        self, monkeypatch, _risk_event_ready
    ):
        # main.py modülündeki app_logger GERÇEK loguru logger'ıdır (yalnız
        # scalper_engine mock'lanır) — bu test main.py:870 sitesini de kapsar.
        body = json.dumps(
            {
                "action": "halt", "secret": SECRET,
                "reason": "BTC {halving} haberi", "source": "bot{1}",
            }
        )
        request = _FakeRequest(body.encode())
        result = await main_module.risk_event(request)
        assert result["ok"] is True
        _risk_event_ready.risk_event_halt.assert_awaited_once()

    async def test_endpoint_flatten_with_braces_returns_200_not_500(
        self, monkeypatch, _risk_event_ready
    ):
        body = json.dumps(
            {
                "action": "flatten", "secret": SECRET,
                "reason": "ETF {x} onayı", "source": None,
            }
        )
        request = _FakeRequest(body.encode())
        result = await main_module.risk_event(request)
        assert result["ok"] is True
        _risk_event_ready.risk_event_flatten.assert_awaited_once()


class TestRiskEventNonAsciiSecret:
    """G: secret karşılaştırması UTF-8-güvenli — ASCII-dışı sağlanan secret
    403 döndürmeli, TypeError ile 500 DEĞİL."""

    async def test_non_ascii_provided_secret_returns_403_not_500(self, monkeypatch):
        monkeypatch.setattr(main_module.settings, "risk_event_secret", SECRET)
        body = json.dumps({"action": "status", "secret": "şifre"})
        request = _FakeRequest(body.encode())
        with pytest.raises(HTTPException) as e:
            await main_module.risk_event(request)
        assert e.value.status_code == 403

    async def test_non_ascii_configured_secret_with_matching_secret_succeeds(
        self, monkeypatch
    ):
        # Yapılandırılan secret'ın kendisi ASCII-dışı olabilir (Türkçe
        # operatör) — eşleşen çağrı ASLA TypeError ile patlamamalı.
        monkeypatch.setattr(main_module.settings, "risk_event_secret", "gizli-şifre")
        fake_engine = MagicMock()
        fake_engine.risk_event_status = MagicMock(
            return_value={
                "active": False, "reason": None, "source": None,
                "until_ts": None, "open_positions": 0,
            }
        )
        monkeypatch.setattr(main_module, "scalper_engine", fake_engine)
        body = json.dumps({"action": "status", "secret": "gizli-şifre"})
        request = _FakeRequest(body.encode())
        result = await main_module.risk_event(request)
        assert result["ok"] is True


class TestRiskEventTtlOverflow:
    """H: ttl_minutes=Infinity → int(float('inf')) OverflowError'dır (Type/
    ValueError DEĞİL) — yakalanmazsa halt hiç çalışmadan 500'e düşer."""

    async def test_ttl_minutes_infinity_returns_422_not_500(
        self, monkeypatch, _risk_event_ready
    ):
        # json.dumps float('inf')'u standart-dışı "Infinity" literaline
        # yazar; json.loads de bunu varsayılan olarak kabul eder — gerçek
        # bir çağıranın gönderebileceği payload budur.
        body = json.dumps(
            {
                "action": "halt", "secret": SECRET, "reason": "test",
                "ttl_minutes": float("inf"),
            }
        )
        request = _FakeRequest(body.encode())
        with pytest.raises(HTTPException) as e:
            await main_module.risk_event(request)
        assert e.value.status_code == 422

    async def test_ttl_minutes_negative_infinity_returns_422_not_500(
        self, monkeypatch, _risk_event_ready
    ):
        body = json.dumps(
            {
                "action": "halt", "secret": SECRET, "reason": "test",
                "ttl_minutes": float("-inf"),
            }
        )
        request = _FakeRequest(body.encode())
        with pytest.raises(HTTPException) as e:
            await main_module.risk_event(request)
        assert e.value.status_code == 422


class TestRiskEventOkReflectsReality:
    """I: yanıttaki `ok` gerçeği yansıtmalı — flatten kısmen/tamamen
    başarısızsa, ya da resume halt dosyasını silemezse ok=False olmalı."""

    async def test_flatten_ok_false_when_errors_present(
        self, monkeypatch, _risk_event_ready
    ):
        _risk_event_ready.risk_event_flatten = AsyncMock(
            return_value={
                "flattened": [],
                "errors": ["BTCUSDT: kapanış borsa üzerinde doğrulanamadı"],
                "halt": {
                    "active": True, "reason": "acil", "source": None,
                    "until_ts": 1.0, "persisted": True,
                },
            }
        )
        body = json.dumps({"action": "flatten", "secret": SECRET, "reason": "acil"})
        request = _FakeRequest(body.encode())
        result = await main_module.risk_event(request)
        assert result["ok"] is False
        assert result["errors"]

    async def test_flatten_ok_true_when_no_errors(
        self, monkeypatch, _risk_event_ready
    ):
        body = json.dumps({"action": "flatten", "secret": SECRET, "reason": "acil"})
        request = _FakeRequest(body.encode())
        result = await main_module.risk_event(request)
        assert result["ok"] is True  # varsayılan fixture: errors=[]

    async def test_resume_ok_false_when_halt_file_could_not_be_deleted(
        self, monkeypatch, _risk_event_ready
    ):
        _risk_event_ready.risk_event_resume = MagicMock(
            return_value={
                "active": True, "reason": "hâlâ aktif — dosya silinemedi",
                "source": None, "until_ts": 999.0,
            }
        )
        body = json.dumps({"action": "resume", "secret": SECRET})
        request = _FakeRequest(body.encode())
        result = await main_module.risk_event(request)
        assert result["ok"] is False

    async def test_halt_ok_true_but_persisted_false_surfaces_to_caller(
        self, monkeypatch, _risk_event_ready
    ):
        _risk_event_ready.risk_event_halt = AsyncMock(
            return_value={
                "active": True, "reason": "test", "source": "newsbot",
                "until_ts": 123.0, "persisted": False,
            }
        )
        body = json.dumps({"action": "halt", "secret": SECRET, "reason": "test"})
        request = _FakeRequest(body.encode())
        result = await main_module.risk_event(request)
        assert result["ok"] is True
        assert result["persisted"] is False


# ---------------------------------------------------------------------------
# C: exits.py tek-finalizer kapısı (_closing) — _handle_closed AYNI sembol
# için İKİ eşzamanlı yoldan (risk-event flatten + safety turu) finalize
# edilemez.
# ---------------------------------------------------------------------------

def _exit_manager_cfg():
    return SimpleNamespace(
        scalper_tp1_roi=10.0,
        scalper_tp2_roi=25.0,
        scalper_breakeven_buffer_pct=0.05,
        scalper_tp1_fraction=0.4,
        scalper_tp2_fraction=0.3,
        scalper_chandelier_atr_mult=3.0,
        scalper_chandelier_atr_period=22,
    )


def _exit_scalp_position(symbol: str = "BTCUSDT", quantity: float = 2.0):
    return SimpleNamespace(
        trade_id=7,
        signal=SimpleNamespace(direction=Direction.LONG),
        position=SimpleNamespace(
            symbol=symbol,
            entry_price=100.0,
            quantity=quantity,
            current_price=100.0,
            opened_at=datetime.now(timezone.utc),
            entry_order_id="123",
            sl_order_id=None,
        ),
        plan=SimpleNamespace(
            initial_stop=95.0,
            tp1_price=110.0,
            tp1_algo_id=None,
            tp2_algo_id=None,
            entry_fee_rate=0.0004,
        ),
        trailing_active=False,
        mae_pct=-2.0,
        mfe_pct=4.0,
    )


class TestHandleClosedSingleFinalizerGuard:
    async def test_concurrent_handle_closed_finalizes_once_and_keeps_first_reason(
        self,
    ):
        cancel_calls: list[str] = []

        async def slow_cancel(symbol):
            # Gerçek REST gecikmesini taklit eder: ilk çağrı (flatten,
            # RISK_EVENT) burada askıya alınırken ikinci çağrı (safety turu)
            # event loop'a girer ve _closing kapısını AÇIK bulmalı.
            cancel_calls.append(symbol)
            await asyncio.sleep(0.02)

        client = SimpleNamespace(
            cancel_all_open_orders=AsyncMock(side_effect=slow_cancel),
            get_current_price=AsyncMock(return_value=110.0),
            get_order=AsyncMock(
                return_value={"updateTime": int(time.time() * 1000) - 5000}
            ),
            get_income_history=AsyncMock(return_value=[]),
        )
        tracker = SimpleNamespace(record_close=AsyncMock())
        manager = ExitManager(
            client=client,
            pm=SimpleNamespace(),
            tracker=tracker,
            cfg=_exit_manager_cfg(),
            kline_fetch=AsyncMock(return_value=[]),
        )
        manager.INCOME_RETRY_DELAYS = (0.0,)
        sp = _exit_scalp_position()
        manager._positions["BTCUSDT"] = sp

        # Flatten (RISK_EVENT etiketli) İLK çağrılır; safety turu (etiketsiz,
        # normal SL/TP/trailing çıkarımı) HEMEN ardından — gerçek dünyada
        # ikisi de bağımsız task'lardan AYNI event loop'ta yarışır.
        await asyncio.gather(
            manager._handle_closed("BTCUSDT", sp, forced_exit_reason="RISK_EVENT"),
            manager._handle_closed("BTCUSDT", sp),
        )

        assert cancel_calls == ["BTCUSDT"], (
            "ikinci yol da cancel_all_open_orders'a girdi — kapı kapanmadı"
        )
        assert tracker.record_close.await_count == 1
        assert tracker.record_close.await_args.kwargs["exit_reason"] == "RISK_EVENT"
        assert "BTCUSDT" not in manager._positions
        assert manager._closing == set()

    async def test_handle_closed_still_untracks_after_single_call(self):
        """Guard, mevcut tek-çağrı davranışını (pop) bozmamalı (regresyon)."""
        client = SimpleNamespace(
            cancel_all_open_orders=AsyncMock(),
            get_current_price=AsyncMock(return_value=110.0),
            get_order=AsyncMock(
                return_value={"updateTime": int(time.time() * 1000) - 5000}
            ),
            get_income_history=AsyncMock(return_value=[]),
        )
        tracker = SimpleNamespace(record_close=AsyncMock())
        manager = ExitManager(
            client=client,
            pm=SimpleNamespace(),
            tracker=tracker,
            cfg=_exit_manager_cfg(),
            kline_fetch=AsyncMock(return_value=[]),
        )
        manager.INCOME_RETRY_DELAYS = (0.0,)
        sp = _exit_scalp_position()
        manager._positions["BTCUSDT"] = sp

        await manager._handle_closed("BTCUSDT", sp)

        assert tracker.record_close.await_count == 1
        assert "BTCUSDT" not in manager._positions


class TestStepOneStaleObjectGuard:
    """Safety turu (_step_one) await sırasında flatten aynı pozisyonu bitirirse
    bayat nesneyle ikinci finalize YAPILMAMALI (tek-finalizer kilidinin tamamlayıcısı)."""

    async def test_step_one_skips_when_position_finalized_during_await(self):
        tracker = SimpleNamespace(record_close=AsyncMock())
        manager_ref = {}

        async def get_position_risk(symbol, **_kw):
            # await sırasında "flatten" pozisyonu bitirip listeden çıkardı
            manager_ref["m"]._positions.pop(symbol, None)
            return {"positionAmt": "0"}

        client = SimpleNamespace(
            get_position_risk=AsyncMock(side_effect=get_position_risk),
            cancel_all_open_orders=AsyncMock(),
            get_current_price=AsyncMock(return_value=110.0),
            get_order=AsyncMock(return_value={"updateTime": int(time.time() * 1000) - 5000}),
            get_income_history=AsyncMock(return_value=[]),
        )
        manager = ExitManager(
            client=client,
            pm=SimpleNamespace(),
            tracker=tracker,
            cfg=_exit_manager_cfg(),
            kline_fetch=AsyncMock(return_value=[]),
        )
        manager.INCOME_RETRY_DELAYS = (0.0,)
        manager_ref["m"] = manager
        sp = _exit_scalp_position()
        manager._positions["BTCUSDT"] = sp

        await manager._step_one("BTCUSDT", sp)

        client.cancel_all_open_orders.assert_not_awaited()
        assert tracker.record_close.await_count == 0, "bayat nesneyle ikinci finalize yapıldı"
        assert "BTCUSDT" not in manager._positions
