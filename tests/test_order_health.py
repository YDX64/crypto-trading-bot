"""D27/A4 — `order_health` görünürlüğü: TP emri konulamadı SAYILIR ve SÖYLENİR.

ÖLÇÜM (2026-08-24): 3 işlemde TP1 emri konulamadı. TP1 emri yoksa
`exits._check_tp1_breakeven` TP1'in GERÇEK fill'ini aradığı için break-even
HİÇ kurulamaz ve pozisyon TAM RİSK stopuyla taşınır (doğrudan −18.4 USDT).
Bugüne kadar yalnız bir ERROR satırı vardı; kimse saymıyordu.

BU TESTLERİN SÖZLEŞMESİ (değişikliğin sınırı):
  * Girişe YENİ KAPI EKLENMEDİ — TP1 konulamasa da pozisyon kurulur.
  * YENİ REST ÇAĞRISI YOK — `_place_tp_safely` TP1 için YALNIZ BİR KEZ
    çağrılır (takipçideki 2. deneme scalper'a taşınmadı).
  * Pano AYRI bir uç ÇAĞIRMAZ — veri mevcut `/scalper/status` ve
    `/api/status` gövdelerinden okunur (nginx beyaz listesi yalnız mevcut
    uçları taşır; yeni bir uç 404 alırdı).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import src.models.waiting_signal  # noqa: F401 - SQLAlchemy mapper kurulumu
from src.strategies.scalper.engine import ScalperEngine
from src.strategies.scalper.executor import ScalpExecutor
from src.strategies.scalper.types import Direction, Regime, ScalpSignal

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Ortak yardımcılar
# ---------------------------------------------------------------------------

class _CapturingLogger:
    """`app_logger` (loguru) yerine geçer.

    NEDEN monkeypatch, `caplog` DEĞİL: `self.logger` modül düzeyindeki loguru
    `app_logger`'dır ve loguru pytest'in `caplog` (stdlib logging) handler'ına
    yazmaz. Örnek alanını değiştirmek en dolaysız ve en sağlam yakalamadır.
    `logger.critical(..., extra={"trade": True})` çağrıldığı için kwargs kabul
    edilmelidir.
    """

    def __init__(self) -> None:
        self.critical_messages: list = []
        self.error_messages: list = []
        self.warning_messages: list = []
        self.info_messages: list = []

    def critical(self, message, *args, **kwargs):
        self.critical_messages.append(str(message))

    def error(self, message, *args, **kwargs):
        self.error_messages.append(str(message))

    def warning(self, message, *args, **kwargs):
        self.warning_messages.append(str(message))

    def info(self, message, *args, **kwargs):
        self.info_messages.append(str(message))

    def debug(self, message, *args, **kwargs):  # pragma: no cover - savunma
        pass


def _cfg(**overrides):
    """`_finalize_position` yolunun okuduğu MİNİMAL ayar kümesi.

    Yol/persistence alanları (journal, cooldown state) BİLEREK yok: `__init__`
    onları `getattr` ile okur ve yoklarsa persistence kapalıdır — test diske
    hiç dokunmaz.
    """
    values = {
        "scalper_entry_mode": "maker",
        "scalper_maker_fee_pct": 0.02,
        "scalper_taker_fee_pct": 0.05,
        "scalper_breakeven_buffer_pct": 0.05,
        "scalper_leverage": 10,
        "scalper_tp1_roi": 20.0,
        "scalper_tp1_fraction": 0.4,
        "scalper_tp2_roi": 50.0,
        "scalper_tp2_fraction": 0.3,
        "scalper_max_stop_pct": 2.0,
        "scalper_chandelier_atr_mult": 2.5,
        "scalper_chandelier_atr_period": 14,
        "scalper_protection_failure_cooldown_minutes": 60,
        "scalper_virtual_capital_usdt": 0.0,
        "scalper_virtual_capital_start_trade_id": 0,
        # Adli kayıt (D21) bu testlerin konusu değil: kapalıyken belge hiç
        # kurulmaz ve `logs/trades.jsonl`'e satır düşmez.
        "scalper_forensics_enabled": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _signal(direction=Direction.LONG):
    return ScalpSignal(
        strategy="C",
        symbol="BTCUSDT",
        direction=direction,
        entry_price=100.0,
        stop_price=99.0 if direction == Direction.LONG else 101.0,
        reason="order-health-test",
        regime=Regime.UP,
        atr_5m=1.0,
    )


def _executor(cfg=None, *, tracker=None):
    """GERÇEK `__init__` ile kurulur — `__new__` + elle alan doldurma DEĞİL.

    GEREKÇE: D27/A4 değişikliğinin bir yarısı TAM DA `__init__`'tedir
    (`self._order_health` varsayılan sözlüğü). `__new__` ile kurup alanı elle
    doldursaydık, varsayılanın kodda gerçekten var olduğunu değil, testin
    kendi kurduğu sözlüğü doğrulamış olurduk — regresyon yakalamaz. `__init__`
    burada güvenlidir: ağ/DB'ye dokunmaz, cfg'de yol alanı olmadığı için
    journal/cooldown persistence'ı kapalıdır.
    """
    executor = ScalpExecutor(
        SimpleNamespace(),
        SimpleNamespace(),
        tracker or SimpleNamespace(),
        cfg or _cfg(),
    )
    executor.logger = _CapturingLogger()
    return executor


# ---------------------------------------------------------------------------
# 1) Sayacın kendisi
# ---------------------------------------------------------------------------

class TestScalperOrderHealthCounter:
    """`_count_order_health` / `order_health_snapshot` sözleşmesi."""

    def test_tek_cagri_sayaci_bir_yapar_ve_sembol_zaman_damgalar(self):
        executor = _executor()

        executor._count_order_health("tp1_missing", "BTCUSDT")

        assert executor._order_health["tp1_missing"] == 1
        assert executor._order_health["tp2_missing"] == 0
        assert executor._order_health["last_symbol"] == "BTCUSDT"

    def test_last_at_ayristirilabilir_utc_iso_metnidir(self):
        executor = _executor()
        before = datetime.now(timezone.utc)

        executor._count_order_health("tp1_missing", "ETHUSDT")

        raw = executor._order_health["last_at"]
        assert isinstance(raw, str)
        parsed = datetime.fromisoformat(raw)
        # UTC: tzinfo VAR ve ofset sıfır (naive damga panoda yanlış saat gösterir).
        assert parsed.tzinfo is not None
        assert parsed.utcoffset().total_seconds() == 0
        assert parsed >= before

    def test_iki_cagri_sayaci_ikiye_cikarir_ve_son_sembol_guncellenir(self):
        executor = _executor()

        executor._count_order_health("tp1_missing", "BTCUSDT")
        executor._count_order_health("tp1_missing", "SOLUSDT")

        assert executor._order_health["tp1_missing"] == 2
        assert executor._order_health["last_symbol"] == "SOLUSDT"

    def test_tp1_ve_tp2_ayri_kovalardir(self):
        executor = _executor()

        executor._count_order_health("tp1_missing", "BTCUSDT")
        executor._count_order_health("tp2_missing", "BTCUSDT")
        executor._count_order_health("tp2_missing", "BTCUSDT")

        assert executor._order_health["tp1_missing"] == 1
        assert executor._order_health["tp2_missing"] == 2

    def test_snapshot_kopya_doner_ve_ic_durum_disaridan_bozulamaz(self):
        executor = _executor()
        executor._count_order_health("tp1_missing", "BTCUSDT")

        snapshot = executor.order_health_snapshot()
        snapshot["tp1_missing"] = 999
        snapshot["last_symbol"] = "SAHTE"
        snapshot.pop("tp2_missing", None)

        assert executor._order_health["tp1_missing"] == 1
        assert executor._order_health["last_symbol"] == "BTCUSDT"
        assert executor._order_health["tp2_missing"] == 0
        # `window` yalnız snapshot'ın alanıdır; iç duruma sızmaz.
        assert "window" not in executor._order_health

    def test_snapshot_surec_penceresini_tasir(self):
        """DÜRÜSTLÜK: sayaç süreç-içidir, restart'ta SIFIRLANIR."""
        executor = _executor()

        snapshot = executor.order_health_snapshot()

        assert snapshot["window"] == "process_start"
        assert snapshot["tp1_missing"] == 0
        assert snapshot["tp2_missing"] == 0
        assert snapshot["last_symbol"] is None
        assert snapshot["last_at"] is None

    def test_bos_sozluk_bozuk_durumda_bile_sayac_patlamaz(self):
        """Eksik alanlar sayacı düşürmez — teşhis kodu akışı ASLA kesmez."""
        executor = _executor()
        executor._order_health = {}

        executor._count_order_health("tp1_missing", "BTCUSDT")

        assert executor._order_health["tp1_missing"] == 1
        assert executor.order_health_snapshot()["window"] == "process_start"

    def test_alan_tamamen_bozuksa_cagri_yine_de_patlamaz(self):
        """`None` (ya da başka bir tip): `except` yutar, işlem akışı sürer."""
        executor = _executor()
        executor._order_health = None

        executor._count_order_health("tp1_missing", "BTCUSDT")  # istisna FIRLATMAMALI

        assert executor._order_health is None


# ---------------------------------------------------------------------------
# 2) Gerçek yol: `_finalize_position` içindeki TP bloğu
# ---------------------------------------------------------------------------

class TestTp1MissingIsCountedOnPlacementFailure:
    """GERÇEK yol koşulur: `executor._finalize_position(...)`.

    `open_position` yerine `_finalize_position` seçildi çünkü D27/A4 bloğu
    TAM OLARAK oradadır (executor.py, "--- 10. TP merdiveni ---") ve iki giriş
    yolunun (maker dolumu + taker) ORTAK kod yoludur; `tests/
    test_scalper_fee_exit_safety.py` de aynı giriş noktasını kullanır. Yalnız
    `_place_tp_safely` sahteleşir: gerçek Binance emri gönderilmez, ama
    sayaç/log/karar bloğu birebir gerçek koddur.
    """

    @staticmethod
    def _prepare(executor, *, tp_results, tp_fail=None):
        """`_place_tp_safely`yi sahtele; çağrı etiketlerini ve miktarları kaydet.

        D27 incelemesi (O7): gerçek imza artık `(algo_id, fail_reason)`
        döndürür — sahte de aynı sözleşmeyi taşır.
        """
        calls: list = []
        fails = tp_fail or {}

        async def fake_place_tp(symbol, side, price, quantity, label):
            calls.append({"symbol": symbol, "label": label, "quantity": quantity})
            algo_id = tp_results.get(label)
            if algo_id is not None:
                return algo_id, None
            if quantity <= 0:
                return None, ScalpExecutor.TP_FAIL_ZERO_QTY
            return None, fails.get(label, ScalpExecutor.TP_FAIL_REJECTED)

        executor._place_tp_safely = fake_place_tp
        return calls

    @staticmethod
    def _wire(executor):
        """SL başarılı + komisyon + DB kaydı — TP bloğuna ULAŞMAK için gerekli."""
        executor.pm = SimpleNamespace(
            place_stop_loss_or_close=AsyncMock(return_value={"algoId": "sl-1"})
        )
        executor.client = SimpleNamespace(
            get_user_commission_rate=AsyncMock(
                return_value={
                    "makerCommissionRate": "0.0002",
                    "takerCommissionRate": "0.0005",
                }
            )
        )
        executor.tracker = SimpleNamespace(record_open=AsyncMock(return_value=77))

    async def _finalize(self, executor):
        return await executor._finalize_position(
            signal=_signal(),
            direction=Direction.LONG,
            sl_side="SELL",
            entry_price=100.0,
            filled_qty=1.0,
            entry_order_id="e-1",
            entry_candle_time=1_700_000_000_000,
        )

    async def test_tp1_konulamazsa_sayac_artar_ve_critical_basilir(self):
        executor = _executor()
        self._wire(executor)
        self._prepare(executor, tp_results={"TP1": None, "TP2": "tp2-id"})

        position = await self._finalize(executor)

        assert executor._order_health["tp1_missing"] == 1
        assert executor._order_health["last_symbol"] == "BTCUSDT"
        assert executor._order_health["tp2_missing"] == 0
        critical = executor.logger.critical_messages
        assert len(critical) == 1
        assert "TP1" in critical[0]
        assert "order_health.tp1_missing" in critical[0]
        # YENİ KAPI YOK: pozisyon yine de kurulur (SL zaten yerinde).
        assert position is not None
        assert position.plan.tp1_algo_id is None

    async def test_tp1_basarili_olursa_sayac_artmaz_ve_critical_yok(self):
        executor = _executor()
        self._wire(executor)
        self._prepare(executor, tp_results={"TP1": "tp1-id", "TP2": "tp2-id"})

        position = await self._finalize(executor)

        assert executor._order_health["tp1_missing"] == 0
        assert executor._order_health["tp2_missing"] == 0
        assert executor._order_health["last_symbol"] is None
        assert executor.logger.critical_messages == []
        assert position is not None
        assert position.plan.tp1_algo_id == "tp1-id"

    async def test_tp1_miktari_sifirken_sayac_artmaz_bu_ayri_bir_olaydir(self):
        """`tp1_qty == 0` = miktar bölünemedi; TP1 emrinin REDDİ ile aynı şey DEĞİL.

        Sayaç yalnız "emir konulmak İSTENDİ ama olmadı" hâlini ölçer; iki
        olay karışırsa sayı yorumlanamaz hâle gelir.
        """
        executor = _executor(_cfg(scalper_tp1_fraction=0.0))
        self._wire(executor)
        self._prepare(executor, tp_results={"TP1": None, "TP2": "tp2-id"})

        position = await self._finalize(executor)

        assert executor._order_health["tp1_missing"] == 0
        assert executor.logger.critical_messages == []
        assert position is not None

    async def test_tp2_konulamazsa_sayilir_ama_critical_basilmaz(self):
        """TP2 yokluğu break-even'ı ETKİLEMEZ — sayılır, ama alarm değildir."""
        executor = _executor()
        self._wire(executor)
        self._prepare(executor, tp_results={"TP1": "tp1-id", "TP2": None})

        await self._finalize(executor)

        assert executor._order_health["tp2_missing"] == 1
        assert executor._order_health["tp1_missing"] == 0
        assert executor._order_health["last_symbol"] == "BTCUSDT"
        assert executor.logger.critical_messages == []

    async def test_tp2_miktari_sifirken_sayac_artmaz(self):
        executor = _executor(_cfg(scalper_tp2_fraction=0.0))
        self._wire(executor)
        self._prepare(executor, tp_results={"TP1": "tp1-id", "TP2": None})

        await self._finalize(executor)

        assert executor._order_health["tp2_missing"] == 0

    async def test_yeni_deneme_yok_tp1_yalniz_bir_kez_denenir(self):
        """YENİ REST AĞIRLIĞI SIFIR sözleşmesi.

        Takipçideki "2. deneme" scalper'a EKLENMEDİ: başarısız TP1 aynı
        turda tekrar POST edilmez (418 ban riskine yeni ağırlık eklenmez).
        """
        executor = _executor()
        self._wire(executor)
        calls = self._prepare(executor, tp_results={"TP1": None, "TP2": None})

        await self._finalize(executor)

        labels = [c["label"] for c in calls]
        assert labels.count("TP1") == 1
        assert labels.count("TP2") == 1
        assert labels == ["TP1", "TP2"]

    async def test_sayac_ard_arda_iki_arizada_birikir(self):
        executor = _executor()
        self._wire(executor)
        self._prepare(executor, tp_results={"TP1": None, "TP2": "tp2-id"})

        await self._finalize(executor)
        await self._finalize(executor)

        assert executor._order_health["tp1_missing"] == 2
        assert len(executor.logger.critical_messages) == 2
        assert executor.order_health_snapshot()["tp1_missing"] == 2


# ---------------------------------------------------------------------------
# 3) Motor: `/scalper/status → order_health` geriye uyumlu okunur
# ---------------------------------------------------------------------------

class TestEngineSnapshotOrderHealth:
    """`ScalperEngine._executor_order_health_snapshot()` teşhis bloğu status'u DÜŞÜRMEZ."""

    @staticmethod
    def _engine(executor):
        engine = ScalperEngine.__new__(ScalperEngine)
        engine.executor = executor
        engine.logger = _CapturingLogger()
        return engine

    def test_executor_sayaclarini_oldugu_gibi_doner(self):
        payload = {
            "tp1_missing": 3,
            "tp2_missing": 1,
            "last_symbol": "XRPUSDT",
            "last_at": "2026-08-24T10:00:00+00:00",
            "window": "process_start",
        }
        engine = self._engine(
            SimpleNamespace(order_health_snapshot=MagicMock(return_value=payload))
        )

        result = engine._executor_order_health_snapshot()

        assert result == payload
        assert engine.logger.error_messages == []

    def test_donen_sozluk_kopyadir_status_ic_durumu_bozmaz(self):
        payload = {"tp1_missing": 1, "window": "process_start"}
        engine = self._engine(
            SimpleNamespace(order_health_snapshot=MagicMock(return_value=payload))
        )

        result = engine._executor_order_health_snapshot()
        result["tp1_missing"] = 42

        assert payload["tp1_missing"] == 1

    def test_metot_yoksa_ERROR_alani_doner(self):
        """D27 incelemesi (O8): alan yok → `{"error": ...}` (istisna DEĞİL).

        Eskiden `{}` dönüyordu ve pano onu **0** okuyordu, yani "TP sorunu
        yok" — oysa bu blok tam da sessiz kalmamak için yazıldı.
        """
        engine = self._engine(SimpleNamespace())

        snapshot = engine._executor_order_health_snapshot()
        assert snapshot.get("error")
        assert snapshot["window"] == "process_start"
        assert engine.logger.error_messages == []

    def test_metot_cagrilabilir_degilse_ERROR_alani_doner(self):
        engine = self._engine(SimpleNamespace(order_health_snapshot="metot değil"))

        assert engine._executor_order_health_snapshot().get("error")

    def test_metot_istisna_firlatirsa_ERROR_doner_ve_motor_patlamaz(self):
        engine = self._engine(
            SimpleNamespace(
                order_health_snapshot=MagicMock(side_effect=RuntimeError("kilit"))
            )
        )

        snapshot = engine._executor_order_health_snapshot()
        assert "RuntimeError" in snapshot["error"]
        assert len(engine.logger.error_messages) == 1
        assert "order_health" in engine.logger.error_messages[0]


# ---------------------------------------------------------------------------
# 4) Motor yokken de ŞEKİL aynı (`src/main.py` boş durum sözlükleri)
# ---------------------------------------------------------------------------

class TestStatusShapes:
    """Pano "alan yok" ile "hiç olmadı"yı karıştırmamalı.

    NOT: motorlu/motorsuz anahtar kümelerinin AYNI olması
    `tests/test_market_data_source.py::TestStatusPayloadShape`'in işidir;
    burada TEKRARLANMAZ — yalnız `order_health` bloğunun varlığı ve iç şekli
    doğrulanır.
    """

    def test_bos_scalper_durumunda_order_health_blogu_vardir(self):
        import src.main as main_module

        block = main_module._EMPTY_SCALPER_STATUS["order_health"]

        assert set(block) == {
            "tp1_missing",
            # D27 incelemesi (O7): "emir kabul edildi, kimliği okunamadı"
            # AYRI sayılır — `tp1_missing` durumu yanlış tarif ediyordu.
            "tp1_unidentified",
            "tp2_missing",
            "tp2_unidentified",
            # D27 incelemesi (D8): iki halkanın ORTAK alan kümesi.
            "tp_wrong_side",
            "partial_fill_split",
            "last_symbol",
            "last_at",
            "window",
        }
        assert all(block[key] == 0 for key in (
            "tp1_missing", "tp1_unidentified", "tp2_missing",
            "tp2_unidentified", "tp_wrong_side", "partial_fill_split",
        ))
        assert block["last_symbol"] is None
        assert block["last_at"] is None
        assert block["window"] == "process_start"

    def test_iki_halka_ORTAK_alan_kumesini_tasir(self):
        """D27 incelemesi (D8): pano tek uyarı satırıyla ikisini de göstersin."""
        import src.main as main_module

        ortak = {"tp1_missing", "tp_wrong_side", "partial_fill_split", "window"}
        assert ortak <= set(main_module._EMPTY_SCALPER_STATUS["order_health"])
        assert ortak <= set(main_module._EMPTY_FOLLOWER_STATUS["order_health"])

    def test_bos_takipci_durumunda_order_health_blogu_vardir(self):
        import src.main as main_module

        block = main_module._EMPTY_FOLLOWER_STATUS["order_health"]

        assert set(block) == {
            "tp1_missing",
            "tp_wrong_side",
            "partial_fill_split",
            "window",
        }
        assert block["tp1_missing"] == 0
        assert block["tp_wrong_side"] == 0
        assert block["partial_fill_split"] == 0
        assert block["window"] == "process_start"

    def test_iki_halkanin_ortak_anahtarlari_ayni_isimlidir(self):
        """Pano tek uyarı satırıyla iki halkayı da gösterebilmeli."""
        import src.main as main_module

        scalper = main_module._EMPTY_SCALPER_STATUS["order_health"]
        follower = main_module._EMPTY_FOLLOWER_STATUS["order_health"]

        assert "tp1_missing" in scalper and "tp1_missing" in follower
        assert scalper["window"] == follower["window"] == "process_start"


# ---------------------------------------------------------------------------
# 5) Takipçi halkası (D20): kaynak `_reject_counters` + `executor.reject_snapshot()`
# ---------------------------------------------------------------------------

class TestFollowerOrderHealth:
    """`FollowerEngine._order_health_snapshot()` — ret kovasından uyarı satırına."""

    @staticmethod
    def _engine(*, engine_rejects=None, executor_rejects=None, executor_raises=False):
        from src.strategies.follower.engine import FollowerEngine

        engine = object.__new__(FollowerEngine)
        engine.logger = _CapturingLogger()
        engine._reject_counters = dict(engine_rejects or {})
        if executor_raises:
            snapshot = MagicMock(side_effect=RuntimeError("executor yok"))
        else:
            snapshot = MagicMock(return_value=dict(executor_rejects or {}))
        engine.executor = SimpleNamespace(reject_snapshot=snapshot)
        return engine

    def test_iki_kaynak_birlesir_ve_executor_motoru_ezer(self):
        """Koddaki `**` sırası: `{**self._reject_counters, **executor.reject_snapshot()}`.

        Yani ÇAKIŞMADA executor kazanır — gerçek emir yolunu o sayar.
        """
        engine = self._engine(
            engine_rejects={"tp1_missing": 2, "tp_wrong_side": 5},
            executor_rejects={"tp1_missing": 7, "partial_fill_split": 1},
        )

        block = engine._order_health_snapshot()

        assert block["tp1_missing"] == 7  # executor motorunkini EZDİ
        assert block["tp_wrong_side"] == 5  # yalnız motorda var → korunur
        assert block["partial_fill_split"] == 1
        assert block["window"] == "process_start"

    def test_yalniz_motor_sayaci_varken_de_okunur(self):
        engine = self._engine(engine_rejects={"tp1_missing": 4}, executor_rejects={})

        assert engine._order_health_snapshot()["tp1_missing"] == 4

    def test_ilgisiz_ret_kovalari_sizmaz(self):
        """Şekil SABİT: `fee_gate` gibi kovalar order_health'a girmez."""
        engine = self._engine(
            engine_rejects={"fee_gate": 9, "max_positions": 3},
            executor_rejects={"tp1_missing": 1},
        )

        block = engine._order_health_snapshot()

        assert set(block) == {
            "tp1_missing",
            "tp_wrong_side",
            "partial_fill_split",
            "window",
        }
        assert block["tp1_missing"] == 1

    def test_executor_patlarsa_motorun_kendi_sayaci_KORUNUR(self):
        engine = self._engine(
            engine_rejects={"tp1_missing": 3}, executor_raises=True
        )

        block = engine._order_health_snapshot()  # istisna FIRLATMAMALI

        # DÜRÜSTLÜK (D27/A4 inceleme bulgusu): iki kaynak AYRI `try` içinde
        # okunur. Tek bir `try` ile sarmak, bir TEŞHİS arızasının gerçek bir
        # `tp1_missing`i **sıfır** (yani "sağlıklı") göstermesine yol açardı —
        # oysa bu blok tam da sessiz kalmamak için var.
        assert block == {
            "tp1_missing": 3,
            "tp_wrong_side": 0,
            "partial_fill_split": 0,
            "window": "process_start",
        }

    def test_motor_sayaci_patlarsa_executor_sayaci_KORUNUR(self):
        """Simetrik: bozuk motor sözlüğü executor kanıtını gizlememeli."""
        engine = self._engine(executor_rejects={"tp1_missing": 4})
        engine._reject_counters = None  # bozuk teşhis alanı

        block = engine._order_health_snapshot()

        assert block["tp1_missing"] == 4

    def test_dashboard_snapshot_order_health_tasir(self):
        """Pano kartı `f.order_health` okur — `snapshot()`tan geçirilir."""
        engine = self._engine(executor_rejects={"tp1_missing": 2})
        payload = {
            "reject_counters": {"tp1_missing": 2},
            "event_counters": {},
            "order_health": {
                "tp1_missing": 2,
                "tp_wrong_side": 0,
                "partial_fill_split": 0,
                "window": "process_start",
            },
        }
        engine.snapshot = lambda: dict(payload)

        card = engine.dashboard_snapshot()

        assert card["order_health"]["tp1_missing"] == 2
        assert card["order_health"]["window"] == "process_start"

    def test_dashboard_snapshot_alan_yoksa_bos_sozluk_verir(self):
        """Eski gövde: `None` DEĞİL `{}` — pano `f.order_health || {}` ile uyumlu."""
        engine = self._engine()
        engine.snapshot = lambda: {"reject_counters": {}, "event_counters": {}}

        assert engine.dashboard_snapshot()["order_health"] == {}


# ---------------------------------------------------------------------------
# 6) Pano kablolaması (metin üzerinden — tarayıcı çalıştırılmaz)
# ---------------------------------------------------------------------------

class TestDashboardWiring:
    """`static/dashboard.html` uyarı satırları MEVCUT gövdelerden beslenir."""

    def _html(self) -> str:
        return (REPO_ROOT / "static" / "dashboard.html").read_text(encoding="utf-8")

    def test_order_health_banner_css_sinifi_tanimli(self):
        html = self._html()

        assert ".order-health-banner{" in html
        assert ".order-health-note{" in html

    def test_tarama_paneli_scalper_order_health_okur(self):
        html = self._html()

        assert "d.order_health" in html
        assert "oh.tp1_missing" in html
        assert "oh.tp2_missing" in html

    def test_ap_karti_takipci_order_health_okur(self):
        html = self._html()

        assert "f.order_health" in html
        assert "apOh.tp1_missing" in html

    def test_uyari_satiri_yalniz_sayi_sifirdan_buyukken_eklenir(self):
        """Sağlıklı bir botta pano temiz kalmalı (uyarı yorgunluğu)."""
        html = self._html()
        condition = "if (tp1Missing > 0 || tp2Missing > 0 || tp1Unid > 0"
        assert condition in html

        branch_start = html.index(condition)
        banner_create = html.index(
            'el("div", "order-health-banner")', branch_start
        )
        # Rozet, koşul dalının İÇİNDE kurulur (kaba ama yeterli sıra kontrolü).
        assert branch_start < banner_create

        # AP kartındaki uyarı da sayı > 0 koşuluna bağlıdır.
        assert "if (apTp1Missing > 0){" in html

    def test_pano_yeni_emir_sagligi_alanlarini_okur(self):
        """D27 incelemesi (O7/O8/D8): kimliksiz emir, arıza ve ters yön."""
        html = self._html()

        assert "oh.tp1_unidentified" in html
        assert "oh.tp_wrong_side" in html
        assert "oh.partial_fill_split" in html
        assert "if (oh.error){" in html
        assert "apOh.tp_wrong_side" in html

    def test_pano_karsi_olgu_kayip_sayaclarini_gosterir(self):
        """D27 incelemesi (D9): "neden `measured` 0?" cevabı bu üç sayaçtadır."""
        html = self._html()

        assert "cfx.expired" in html
        assert "cfx.dropped_full" in html
        assert "cfx.log_dropped" in html

    def test_pano_yeni_bir_uc_cagirmaz(self):
        """nginx beyaz listesi yalnız MEVCUT uçları taşır; yeni uç 404 alırdı.

        Ayrıca 2026-08-18 pano-açlığı dersi: her yeni yoklama rate-limiter'ı
        doyurup tarama döngüsünü aç bırakır.
        """
        html = self._html()

        for forbidden in ("/scalper/order_health", "/scalper/counterfactual"):
            assert forbidden not in html, forbidden
