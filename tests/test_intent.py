"""D24/madde 7 — üç-aşamalı niyet kaydı testleri.

Kapsam:
  1. `build_intent` saflığı: IO yok, savunmalı okuma, `detail` kırpma.
  2. Sayaçlar: doğru kova, bilinmeyen gerekçenin `_diger_`e düşmesi,
     `counters_snapshot` şekli ve `reset_counters`.
  3. JSONL köprüsü: `record` → `forensics_log.append_soon("intent", ...)`.
  4. FAIL-SAFE: `append_soon` patlasa bile `record` İSTİSNA SIZDIRMAZ.
  5. `summarize_intents` dağılımı (bilinen vektör, `share_pct` ≈ %100).
  6. `engine._record_intent`: adli kayıt KAPALIYKEN hiç çağrılmaz; açıkken
     bile hiçbir istisna sızdırmaz (gözlem ≠ güvenlik kilidi).
  7. `main.py`: TV sağlaması DOLMAYAN oy kayda düşer (yanıt gövdesi AYNI
     kalır) ve `/scalper/forensics/summary` sayaçları taşır.
"""

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from src.strategies.scalper import forensics_log
from src.strategies.scalper import intent


@pytest.fixture(autouse=True)
def _clean_counters():
    """Sayaçlar SÜREÇ-İÇİDİR: testler arası sızmasın."""
    intent.reset_counters()
    yield
    intent.reset_counters()


# --------------------------------------------------------------------------
# 1. build_intent — SAF
# --------------------------------------------------------------------------

class TestBuildIntent:
    def test_normalizes_and_keeps_every_key(self):
        row = intent.build_intent(
            at="2026-08-24T00:00:00+00:00",
            symbol=" btcusdt ",
            direction=SimpleNamespace(value="long"),
            stage=intent.STAGE_DECIDED,
            decision=intent.DECISION_DENY,
            strategy="C",
            source="tv",
            reason="REGIME_GATE",
            detail="rejim DOWN",
            intent_id="abc",
            extra={"regime": "DOWN"},
        )
        assert row["symbol"] == "BTCUSDT"
        assert row["direction"] == "LONG"
        assert row["reason"] == "regime_gate"
        assert row["stage"] == "decided"
        assert row["decision"] == "deny"
        assert row["extra"] == {"regime": "DOWN"}
        # Şema sabit: her anahtar HER ZAMAN vardır.
        assert set(row) == {
            "at", "intent_id", "symbol", "direction", "stage", "decision",
            "strategy", "source", "reason", "detail", "extra",
            # D27/B: karşı-olgu defterinin KALICI girdisi (dördü de None
            # olabilir = "ölçülmedi").
            "price", "stop_price", "tp1_price", "leverage",
        }

    def test_detail_is_trimmed_to_limit(self):
        row = intent.build_intent(
            at="t", symbol="X", direction="LONG",
            stage=intent.STAGE_EXECUTED, decision=intent.DECISION_ERROR,
            reason=intent.REASON_ORDER_ERROR, detail="y" * 500,
        )
        assert len(row["detail"]) == intent.DETAIL_MAX == 200

    def test_broken_inputs_never_raise(self):
        row = intent.build_intent(
            at=None, symbol=None, direction=None, stage=None, decision=None,
            reason=None, detail=None, intent_id=None, extra="sözlük değil",
        )
        assert row["symbol"] is None and row["direction"] is None
        assert row["reason"] is None and row["detail"] is None
        # Sözlük OLMAYAN `extra` sessizce boş sözlüğe düşer (patlamaz).
        assert row["extra"] == {}

    def test_extra_is_shallow_copied(self):
        payload = {"votes": 1}
        row = intent.build_intent(
            at="t", symbol="X", direction="LONG",
            stage=intent.STAGE_PROPOSED, decision=intent.DECISION_ALLOW,
            extra=payload,
        )
        payload["votes"] = 99
        assert row["extra"]["votes"] == 1

    def test_labels_cover_every_known_reason(self):
        assert intent.KNOWN_REASONS <= set(intent.REASON_LABELS)
        # Özel kovalar "bilinen gerekçe" DEĞİLDİR ama etiketleri vardır.
        assert intent.REASON_OTHER_BUCKET not in intent.KNOWN_REASONS
        assert intent.REASON_NONE_BUCKET not in intent.KNOWN_REASONS
        assert intent.REASON_LABELS[intent.REASON_OTHER_BUCKET]
        for name in intent.KNOWN_REASONS:
            assert intent.REASON_LABELS[name].strip()


# --------------------------------------------------------------------------
# 2 + 3 + 4. record / sayaçlar / JSONL köprüsü
# --------------------------------------------------------------------------

class TestRecord:
    def test_counters_increment_per_bucket(self, monkeypatch):
        monkeypatch.setattr(forensics_log, "append_soon", lambda *a, **k: True)
        intent.record(
            at="t", symbol="BTCUSDT", direction="LONG",
            stage=intent.STAGE_PROPOSED, decision=intent.DECISION_ALLOW,
        )
        intent.record(
            at="t", symbol="BTCUSDT", direction="LONG",
            stage=intent.STAGE_DECIDED, decision=intent.DECISION_DENY,
            reason=intent.REASON_CAPACITY,
        )
        intent.record(
            at="t", symbol="ETHUSDT", direction="SHORT",
            stage=intent.STAGE_DECIDED, decision=intent.DECISION_DENY,
            reason=intent.REASON_CAPACITY,
        )
        snap = intent.counters_snapshot()
        assert snap["total"] == 3
        assert snap["by_stage"] == {"proposed": 1, "decided": 2}
        assert snap["by_decision"] == {"allow": 1, "deny": 2}
        assert snap["by_reason"] == {"_yok_": 1, "capacity": 2}
        # Kova toplamı `total` ile TUTAR (sessiz kayıp yok).
        assert sum(snap["by_reason"].values()) == snap["total"]
        assert snap["logged"] == 3 and snap["log_dropped"] == 0

    def test_unknown_reason_falls_into_other_bucket(self, monkeypatch):
        monkeypatch.setattr(forensics_log, "append_soon", lambda *a, **k: True)
        intent.record(
            at="t", symbol="X", direction="LONG",
            stage="uydurma_asama", decision="uydurma_karar",
            reason="hic_boyle_bir_gerekce_yok",
        )
        snap = intent.counters_snapshot()
        assert snap["by_reason"] == {intent.REASON_OTHER_BUCKET: 1}
        # Aşama/karar kovaları da SINIRLIDIR (sınırsız kova büyümesi yok).
        assert snap["by_stage"] == {intent.REASON_OTHER_BUCKET: 1}
        assert snap["by_decision"] == {intent.REASON_OTHER_BUCKET: 1}

    def test_writes_intent_event_to_forensics_log(self, monkeypatch):
        seen = []
        monkeypatch.setattr(
            forensics_log,
            "append_soon",
            lambda event, payload, **k: seen.append((event, payload)) or True,
        )
        intent.record(
            at="2026-08-24T00:00:00+00:00", symbol="SOLUSDT",
            direction="SHORT", stage=intent.STAGE_DECIDED,
            decision=intent.DECISION_DENY,
            reason=intent.REASON_TV_CONFLUENCE,
            extra={"votes": 1, "required": 2},
        )
        assert len(seen) == 1
        event, payload = seen[0]
        assert event == "intent"
        assert payload["symbol"] == "SOLUSDT"
        assert payload["reason"] == "tv_confluence"
        assert payload["extra"] == {"votes": 1, "required": 2}

    def test_dropped_line_is_counted_not_raised(self, monkeypatch):
        monkeypatch.setattr(forensics_log, "append_soon", lambda *a, **k: False)
        intent.record(
            at="t", symbol="X", direction="LONG",
            stage=intent.STAGE_DECIDED, decision=intent.DECISION_DENY,
            reason=intent.REASON_CAPACITY,
        )
        snap = intent.counters_snapshot()
        assert snap["logged"] == 0 and snap["log_dropped"] == 1
        # Satır düşse bile sayaç TUTAR: "kaç niyet doğdu" bilgisi kaybolmaz.
        assert snap["total"] == 1

    def test_append_soon_exception_never_escapes(self, monkeypatch):
        def _explode(*args, **kwargs):
            raise RuntimeError("disk yok")

        monkeypatch.setattr(forensics_log, "append_soon", _explode)
        row = intent.record(
            at="t", symbol="X", direction="LONG",
            stage=intent.STAGE_EXECUTED, decision=intent.DECISION_ERROR,
            reason=intent.REASON_ORDER_ERROR,
        )
        assert row is not None and row["reason"] == "order_error"
        snap = intent.counters_snapshot()
        assert snap["total"] == 1 and snap["log_dropped"] == 1

    def test_snapshot_shape_and_reset(self, monkeypatch):
        monkeypatch.setattr(forensics_log, "append_soon", lambda *a, **k: True)
        snap = intent.counters_snapshot()
        assert set(snap) == {
            "since", "window", "total", "by_stage", "by_decision",
            "by_reason", "logged", "log_dropped",
        }
        # DÜRÜSTLÜK: pencere süreç başlangıcıdır, restart'ta sıfırlanır.
        assert snap["window"] == "process_start"
        assert snap["total"] == 0

        intent.record(
            at="t", symbol="X", direction="LONG",
            stage=intent.STAGE_PROPOSED, decision=intent.DECISION_ALLOW,
        )
        assert intent.counters_snapshot()["total"] == 1

        before = intent.counters_snapshot()["since"]
        intent.reset_counters()
        after = intent.counters_snapshot()
        assert after["total"] == 0 and after["by_reason"] == {}
        assert after["since"] >= before

    def test_snapshot_is_a_copy(self, monkeypatch):
        monkeypatch.setattr(forensics_log, "append_soon", lambda *a, **k: True)
        intent.record(
            at="t", symbol="X", direction="LONG",
            stage=intent.STAGE_DECIDED, decision=intent.DECISION_DENY,
            reason=intent.REASON_CAPACITY,
        )
        snap = intent.counters_snapshot()
        snap["by_reason"]["capacity"] = 999
        assert intent.counters_snapshot()["by_reason"]["capacity"] == 1


# --------------------------------------------------------------------------
# 5. summarize_intents — çevrimdışı dağılım
# --------------------------------------------------------------------------

class TestSummarizeIntents:
    def test_known_vector(self):
        rows = (
            [{"event": "intent", "stage": "decided", "decision": "deny",
              "reason": "regime_gate"}] * 6
            + [{"event": "intent", "stage": "decided", "decision": "deny",
                "reason": "capacity"}] * 3
            + [{"event": "intent", "stage": "executed", "decision": "allow",
                "reason": "opened"}]
        )
        out = intent.summarize_intents(rows)
        assert out["total"] == 10
        assert [r["reason"] for r in out["by_reason"]] == [
            "regime_gate", "capacity", "opened",
        ]
        assert [r["count"] for r in out["by_reason"]] == [6, 3, 1]
        assert [r["share_pct"] for r in out["by_reason"]] == [60.0, 30.0, 10.0]
        assert abs(sum(r["share_pct"] for r in out["by_reason"]) - 100.0) < 0.5
        assert out["by_decision"] == {"deny": 9, "allow": 1}
        assert out["by_stage"] == {"decided": 9, "executed": 1}
        assert out["by_reason"][0]["label"] == intent.REASON_LABELS["regime_gate"]

    def test_skips_other_events_and_broken_rows(self):
        out = intent.summarize_intents([
            {"event": "entry", "reason": "opened"},
            {"event": "exit", "reason": "capacity"},
            None,
            "satır değil",
            {"event": "intent", "stage": "decided", "decision": "deny",
             "reason": "capacity"},
            # `event` alanı OLMAYAN satır niyet sayılır (tracker/rapor yolu).
            {"stage": "proposed", "decision": "allow"},
        ])
        assert out["total"] == 2
        assert out["by_reason"][0]["count"] == 1

    def test_empty_input_is_a_stable_shape(self):
        out = intent.summarize_intents([])
        assert out == {
            "total": 0, "by_reason": [], "by_decision": {}, "by_stage": {},
        }


# --------------------------------------------------------------------------
# 6. engine._record_intent — kanca fail-safe
# --------------------------------------------------------------------------

@dataclass
class _Cfg:
    scalper_forensics_enabled: bool = True


def _engine(**attrs):
    """`ScalperEngine`i __init__ çalıştırmadan kur (repo konvansiyonu)."""
    from src.core.logger import app_logger
    from src.strategies.scalper.engine import ScalperEngine

    engine = ScalperEngine.__new__(ScalperEngine)
    engine.cfg = _Cfg()
    engine.logger = app_logger
    engine._forensics_error_logged = False
    for key, value in attrs.items():
        setattr(engine, key, value)
    return engine


class TestEngineRecordIntent:
    def test_disabled_forensics_records_nothing(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            intent, "record", lambda **kw: calls.append(kw)
        )
        engine = _engine()
        engine.cfg = _Cfg(scalper_forensics_enabled=False)
        engine._record_intent(
            symbol="BTCUSDT", direction="LONG",
            stage=intent.STAGE_DECIDED, decision=intent.DECISION_DENY,
            reason=intent.REASON_CAPACITY,
        )
        assert calls == []

    def test_enabled_forensics_passes_utc_timestamp(self, monkeypatch):
        calls = []
        monkeypatch.setattr(intent, "record", lambda **kw: calls.append(kw))
        engine = _engine()
        engine._record_intent(
            symbol="BTCUSDT", direction="LONG",
            stage=intent.STAGE_DECIDED, decision=intent.DECISION_DENY,
            reason=intent.REASON_REGIME_GATE, extra={"regime": "DOWN"},
        )
        assert len(calls) == 1
        assert calls[0]["reason"] == intent.REASON_REGIME_GATE
        # `at` çağıran tarafından geçilir (modül SAF kalır) ve UTC'dir.
        assert calls[0]["at"].endswith("+00:00")
        assert calls[0]["extra"] == {"regime": "DOWN"}

    def test_record_failure_never_escapes(self, monkeypatch):
        def _explode(**kwargs):
            raise RuntimeError("kayıt bozuk")

        monkeypatch.setattr(intent, "record", _explode)
        engine = _engine()
        # Bir teşhis kaydı giriş/ret akışını ASLA kesmemeli.
        engine._record_intent(
            symbol="BTCUSDT", direction="LONG",
            stage=intent.STAGE_EXECUTED, decision=intent.DECISION_ERROR,
            reason=intent.REASON_ORDER_ERROR,
        )

    def test_half_built_engine_never_raises(self, monkeypatch):
        """`cfg`si HİÇ kurulmamış bir motor bile tarama turunu düşürmemeli."""
        calls = []
        monkeypatch.setattr(intent, "record", lambda **kw: calls.append(kw))
        from src.core.logger import app_logger
        from src.strategies.scalper.engine import ScalperEngine

        engine = ScalperEngine.__new__(ScalperEngine)   # cfg YOK, logger YOK
        engine.logger = app_logger
        engine._record_intent(
            symbol="X", direction="LONG",
            stage=intent.STAGE_DECIDED, decision=intent.DECISION_DENY,
            reason=intent.REASON_CAPACITY,
        )
        assert calls == []

    def test_missing_cfg_flag_defaults_to_enabled(self, monkeypatch):
        calls = []
        monkeypatch.setattr(intent, "record", lambda **kw: calls.append(kw))
        engine = _engine()
        engine.cfg = SimpleNamespace()  # bayrak HİÇ YOK
        engine._record_intent(
            symbol="X", direction="SHORT",
            stage=intent.STAGE_PROPOSED, decision=intent.DECISION_ALLOW,
        )
        assert len(calls) == 1


# --------------------------------------------------------------------------
# 7. main.py bağlantı noktaları (D24/madde 7.3)
# --------------------------------------------------------------------------

TV_SECRET = "tv-s3cr3t"
TV_BUY = (
    "🟢 BUY | BINANCE:BTCUSDT | TF: 1 | Price: 77126.08 | TQI: .45 | Score: 8 "
    "| SL: 77000.00 | TP1: 77200.00 | TP2: 77300.00 | TP3: 77400.00 "
    "| TP: fixed ×1.00"
)


class _FakeRequest:
    """tests/test_follower_endpoint.py ile AYNI desen."""

    def __init__(self, body: bytes, query=None, headers=None):
        self._body = body
        self.query_params = query or {}
        self.headers = headers or {}

    async def body(self) -> bytes:
        return self._body


class TestMainIntentHooks:
    async def test_unfilled_confluence_is_recorded_without_changing_body(
        self, monkeypatch
    ):
        import src.main as main_module

        monkeypatch.setattr(main_module.settings, "tv_webhook_secret", TV_SECRET)
        monkeypatch.setattr(main_module.settings, "tv_confluence_required", 2)
        monkeypatch.setattr(main_module, "_maybe_forward_to_follower",
                            lambda *a, **k: None)

        engine = SimpleNamespace()
        called = []

        async def _external(*args, **kwargs):
            called.append((args, kwargs))
            return {"accepted": True}

        engine.external_signal = _external
        monkeypatch.setattr(main_module, "scalper_engine", engine)

        seen = []
        monkeypatch.setattr(
            main_module.scalp_intent, "record", lambda **kw: seen.append(kw)
        )

        body = f"{TV_BUY} secret={TV_SECRET}".encode()
        out = await main_module.tradingview_webhook(_FakeRequest(body, {}))

        # Yanıt gövdesi DEĞİŞMEZ ve motor ÇAĞRILMAZ (sağlama dolmadı).
        assert out["accepted"] is False
        assert out["confluence"]["triggered"] is False
        assert called == []
        # Ama artık iz VAR.
        assert len(seen) == 1
        assert seen[0]["reason"] == intent.REASON_TV_CONFLUENCE
        assert seen[0]["stage"] == intent.STAGE_DECIDED
        assert seen[0]["decision"] == intent.DECISION_DENY
        assert seen[0]["extra"]["required"] == 2
        assert seen[0]["extra"]["votes"] == 1
        assert isinstance(seen[0]["extra"]["sources"], list)

    async def test_record_failure_never_breaks_the_webhook(self, monkeypatch):
        import src.main as main_module

        monkeypatch.setattr(main_module.settings, "tv_webhook_secret", TV_SECRET)
        monkeypatch.setattr(main_module.settings, "tv_confluence_required", 2)
        monkeypatch.setattr(main_module, "_maybe_forward_to_follower",
                            lambda *a, **k: None)
        monkeypatch.setattr(main_module, "scalper_engine", SimpleNamespace())

        def _explode(**kwargs):
            raise RuntimeError("kayıt bozuk")

        monkeypatch.setattr(main_module.scalp_intent, "record", _explode)
        body = f"{TV_BUY} secret={TV_SECRET}".encode()
        out = await main_module.tradingview_webhook(_FakeRequest(body, {}))
        assert out["accepted"] is False

    async def test_summary_endpoint_carries_process_counters(self, monkeypatch):
        import src.main as main_module

        async def _summary(since=None, until=None, **kwargs):
            return {"tags": [], "trades": 0}

        monkeypatch.setattr(
            main_module, "ScalpTracker",
            lambda: SimpleNamespace(forensics_summary=_summary),
        )
        intent.record(
            at="t", symbol="BTCUSDT", direction="LONG",
            stage=intent.STAGE_DECIDED, decision=intent.DECISION_DENY,
            reason=intent.REASON_CAPACITY,
        )
        out = await main_module.scalper_forensics_summary()
        # DÜRÜSTLÜK: pencere süreç başlangıcıdır — `since` bu bloğa GEÇMEZ.
        assert out["intents"]["window"] == "process_start"
        assert out["intents"]["by_reason"]["capacity"] == 1
        assert out["tags"] == []

    async def test_summary_endpoint_survives_counter_failure(self, monkeypatch):
        import src.main as main_module

        async def _summary(since=None, until=None, **kwargs):
            return {"tags": []}

        def _explode():
            raise RuntimeError("sayaç bozuk")

        monkeypatch.setattr(
            main_module, "ScalpTracker",
            lambda: SimpleNamespace(forensics_summary=_summary),
        )
        monkeypatch.setattr(
            main_module.scalp_intent, "counters_snapshot", _explode
        )
        out = await main_module.scalper_forensics_summary()
        # Anahtar HER ZAMAN vardır; sayaç arızasında değeri None olur.
        assert out["intents"] is None
