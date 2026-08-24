"""D27/A1 — 8 saatlik yaş kesmesi (reaper, D4) AYRI etiket: "REAPER".

Bugüne kadar `engine._reap_aged_positions`ın reduce-only MARKET kapanışı
deftere **"SL"** yazılıyordu; ölçüldü (2026-08-24 kök-neden analizi): 43 kesme
= −172.3 USDT = brüt zararın %27'si ve bunların **12'si ARTIDA** kesilmişti.
Bu etiket kirliliği her SL analizini bozuyordu.

Bu paketin sözleşmesi: **etiket ayrışır, MOTOR DAVRANIŞI DEĞİŞMEZ.** Testler
sırasıyla şunu kanıtlar:

  1. `TestInferExitReason` — kaba çıkarım damga varsa "REAPER", yoksa D27
     ÖNCESİ etiketi bit düzeyinde aynı döndürür.
  2. `TestCooldownParity` (EN ÖNEMLİSİ) — kayıp-cooldown kapısı ESKİ etiket
     uzayını okur; yeni etiket kapıya SIZMAZ, yani cooldown kararı değişmez.
  3. `TestReaperMarksPosition` — damga yalnız emir BORSAYA GİTTİKTEN sonra
     konur; hata/muafiyet/kapalı-limit hâllerinde konmaz.
  4. `TestFamilyMaps` — "REAPER" KENDİ ailesidir (SL ailesine karışmaz), eski
     eşlemeler bozulmamıştır.
  5. `TestLedgerReportNote` — geriye dönük veri düzeltmesi YAPILMADIĞI rapor
     yüzeylerinde (json/text/md) okunabilir; eski "SL" sayımı bozulmaz.
  6. `TestForensicsPostmortemLosing` — aile ayrımının KASITLI yan etkisi:
     REAPER artık yalnız NET NEGATİFSE "kayıplı" sayılır.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core.logger import app_logger
from src.models.position import PositionSide
from src.strategies.scalper import forensics as fx
from src.strategies.scalper.exits import EXIT_REASON_REAPER, ExitManager
from src.strategies.scalper.types import Candle

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import ledger_report as lr  # noqa: E402  (sys.path eklemesinden sonra import)


# --------------------------------------------------------------------------
# Yardımcılar — repo konvansiyonu: nesneler `__new__` ile kurulur, gereken
# alanlar elle doldurulur (bkz. tests/test_forensics.py::_exit_manager).
# --------------------------------------------------------------------------

def _position(
    *,
    trailing_active: bool = False,
    initial_stop: float = 99.0,
    tp1_price: float = 101.0,
    reaper_close_at=None,
):
    """`_infer_exit_reason`ın OKUDUĞU asgari pozisyon çifti.

    Kaba çıkarım yalnız `trailing_active`, `plan.initial_stop`,
    `plan.tp1_price` ve (D27/A1) `reaper_close_at` alanlarına bakar.
    """
    return SimpleNamespace(
        trailing_active=trailing_active,
        plan=SimpleNamespace(initial_stop=initial_stop, tp1_price=tp1_price),
        reaper_close_at=reaper_close_at,
    )


def _cooldown_manager():
    """`ExitManager`ı __init__ çalıştırmadan kur; cooldown çağrılarını yakala.

    `(manager, cagrilanlar)` döner — `cagrilanlar` cb'ye geçen sembollerin
    listesidir (boş liste = cooldown BAŞLAMADI).
    """
    calls: list = []
    manager = ExitManager.__new__(ExitManager)
    manager.logger = app_logger
    manager._loss_cooldown_cb = calls.append
    return manager, calls


async def _quantize_identity(symbol: str, qty: float) -> float:
    """Sahte `client.quantize_quantity`: miktarı AYNEN döndürür."""
    return qty


def _reaper_position(
    *,
    age_hours: float = 9.0,
    trailing_active: bool = False,
    side: PositionSide = PositionSide.LONG,
    quantity: float = 1.5,
    aware: bool = True,
):
    """`_reap_aged_positions`ın okuduğu asgari canlı pozisyon çifti."""
    now = datetime.now(timezone.utc)
    opened = now - timedelta(hours=age_hours)
    if not aware:
        opened = opened.replace(tzinfo=None)
    return SimpleNamespace(
        position=SimpleNamespace(side=side, quantity=quantity, opened_at=opened),
        trailing_active=trailing_active,
        reaper_close_at=None,
    )


def _reaper_engine(positions: dict, *, max_hold_hours: float = 8.0, submit=None):
    """`ScalperEngine`i __init__ çalıştırmadan kur (ağ yok, DB yok).

    `(engine, gonderilen_emirler)` döner. `submit` verilmezse emirleri kaydeden
    varsayılan sahte kullanılır; istisna sınaması için `submit` geçilir.
    """
    from src.strategies.scalper.engine import ScalperEngine

    sent: list = []

    async def _default_submit(symbol, close_side, qty):
        sent.append((symbol, close_side, qty))

    engine = ScalperEngine.__new__(ScalperEngine)
    engine.cfg = SimpleNamespace(scalper_max_hold_hours=max_hold_hours)
    engine.logger = app_logger
    engine.exits = SimpleNamespace(
        tracked_symbols=lambda: set(positions.keys()),
        _positions=positions,
    )
    engine.client = SimpleNamespace(quantize_quantity=_quantize_identity)
    engine._submit_reduce_only_market_close = submit or _default_submit
    return engine, sent


def _pm_entry(**overrides):
    base = {"direction": "LONG", "fill_price": 100.0}
    base.update(overrides)
    return base


def _pm_exit(**overrides):
    base = {"reason": "SL", "realized_pnl": -10.0, "direction": "LONG"}
    base.update(overrides)
    return base


def _candle(close_time_ms: int, high: float, low: float) -> Candle:
    return Candle(
        open_time=close_time_ms - 60_000, open=(high + low) / 2,
        high=high, low=low, close=(high + low) / 2, volume=1.0,
        close_time=close_time_ms,
    )


def _closed_trade(tid: int, reason: str, pnl: float, direction: str = "LONG"):
    day = "2026-08-24"
    return lr.ClosedTrade(
        id=tid, strategy="C", symbol="BTCUSDT", direction=direction,
        realized_pnl=pnl, exit_reason=reason,
        closed_at=datetime.strptime(day, "%Y-%m-%d"), day=day,
    )


# --------------------------------------------------------------------------
# 1) Kaba çıkarım — damga varsa REAPER, yoksa D27 ÖNCESİ davranış
# --------------------------------------------------------------------------

class TestInferExitReason:
    """`_infer_exit_reason` yaş kesmesini artık "SL" ile karıştırmıyor."""

    def test_reaper_damgasi_varken_kayipta_reaper_doner(self):
        sp = _position(reaper_close_at="2026-08-24T09:00:00+00:00")
        assert ExitManager._infer_exit_reason(sp, 99.05, -18.0) == "REAPER"
        assert EXIT_REASON_REAPER == "REAPER"

    def test_reaper_damgasi_varken_artida_da_reaper_doner(self):
        """"Artıda kesilen 12 pozisyon" vakası: PnL POZİTİF ama etiket REAPER.

        Eski davranışta bu kapanış "SL" yazılıyordu ve her SL analizini
        kirletiyordu (43 kesmenin 12'si artıdaydı).
        """
        sp = _position(reaper_close_at="2026-08-24T09:00:00+00:00")
        assert ExitManager._infer_exit_reason(sp, 99.4, +5.0) == "REAPER"

    def test_damga_yokken_sl_yakini_kapanis_sl_kalir(self):
        sp = _position()
        assert ExitManager._infer_exit_reason(sp, 99.05, -18.0) == "SL"

    def test_damga_yokken_tp1_yakini_pozitif_pnl_tp_ladder_kalir(self):
        sp = _position()
        assert ExitManager._infer_exit_reason(sp, 100.95, +12.0) == "TP_LADDER"

    def test_damga_yokken_tp1_yakini_negatif_pnl_sl_olur(self):
        """Mantık kapısı (2026-08-13 ADAUSDT vakası) bozulmadı: negatif net PnL
        asla "kâr merdiveni" diye etiketlenemez."""
        sp = _position()
        assert ExitManager._infer_exit_reason(sp, 100.95, -3.0) == "SL"

    def test_damga_yokken_trailing_aktif_trail_kalir(self):
        sp = _position(trailing_active=True)
        assert ExitManager._infer_exit_reason(sp, 99.05, -1.0) == "TRAIL"

    def test_legacy_reaper_damgasi_varken_bile_eski_etiketi_dondurur(self):
        """Kapının okuduğu etiket uzayı BOZULMADI: `_infer_exit_reason_legacy`
        damgayı hiç görmez."""
        stamped = _position(reaper_close_at="2026-08-24T09:00:00+00:00")
        assert ExitManager._infer_exit_reason_legacy(stamped, 99.05, -18.0) == "SL"
        assert ExitManager._infer_exit_reason_legacy(stamped, 100.95, +12.0) == "TP_LADDER"
        assert ExitManager._infer_exit_reason_legacy(stamped, 99.4, +5.0) == "SL"

        trailing = _position(
            trailing_active=True, reaper_close_at="2026-08-24T09:00:00+00:00"
        )
        assert ExitManager._infer_exit_reason_legacy(trailing, 99.05, -1.0) == "TRAIL"
        # Aynı çift YENİ yoldan "REAPER"dır — iki uzay ayrı, ikisi de sağlam.
        assert ExitManager._infer_exit_reason(trailing, 99.05, -1.0) == "REAPER"

    def test_damga_alani_hic_olmayan_cift_patlatmaz(self):
        """`getattr(..., None)` konvansiyonu: eski test çiftleri ve restart
        kurtarmasından gelen kısmi nesneler alanı taşımayabilir."""
        sp = SimpleNamespace(
            trailing_active=False,
            plan=SimpleNamespace(initial_stop=99.0, tp1_price=101.0),
        )
        assert not hasattr(sp, "reaper_close_at")
        assert ExitManager._infer_exit_reason(sp, 99.05, -18.0) == "SL"
        assert ExitManager._infer_exit_reason(sp, 100.95, +12.0) == "TP_LADDER"

    def test_bos_damga_reaper_saymaz(self):
        """Damga BOŞ string ise (kısmi/kirli kayıt) eski yola düşülür — "REAPER"
        yalnız gerçekten gönderilmiş bir kesme emrinin adıdır."""
        sp = _position(reaper_close_at="")
        assert ExitManager._infer_exit_reason(sp, 99.05, -18.0) == "SL"


# --------------------------------------------------------------------------
# 2) PARİTE — kayıp-cooldown kapısı ESKİ etiket uzayını okur
# --------------------------------------------------------------------------

class TestCooldownParity:
    """D27/A1 sözleşmesi: etiket ayrışır, KARAR YOLU bayt bayt aynı kalır.

    Kapı gövdesi: ``if exit_reason != "SL" and realized_pnl >= threshold:
    return``. Yani "SL" etiketi PnL ARTIDA olsa bile cooldown BAŞLATIR. Yeni
    "REAPER" etiketi bu kapıya sızsaydı, artıda kesilen 12 pozisyonda cooldown
    kararı SESSİZCE değişirdi.
    """

    def test_artida_sl_cooldown_baslatir_reaper_baslatmaz(self):
        """Davranış farkının TEK yönü budur — ve bu yüzden kapıya ESKİ etiket
        verilir."""
        manager, calls = _cooldown_manager()

        manager._maybe_start_loss_cooldown("BTCUSDT", "SL", 5.0, 0.0)
        assert calls == ["BTCUSDT"], "eski etiket ('SL') cooldown BAŞLATMALI"

        calls.clear()
        manager._maybe_start_loss_cooldown("BTCUSDT", "REAPER", 5.0, 0.0)
        assert calls == [], "yeni etiket kapıya sızarsa cooldown KAYBOLURDU"

    def test_artida_kesilen_reaper_pozisyonunda_zincir_dogru_ucu_kullanir(self):
        """Somut vaka: 8 saati dolmuş, stop tarafına yakın ama fonlama geliriyle
        NET ARTIDA kapanan bir pozisyon.

        Defterе/adli kayda YENİ etiket ("REAPER") yazılır; cooldown kapısına
        `_infer_exit_reason_legacy`nin ürettiği ESKİ etiket ("SL") gider ve
        cooldown D27 ÖNCESİYLE AYNI şekilde başlar.
        """
        sp = _position(reaper_close_at="2026-08-24T09:00:00+00:00")
        exit_price, realized_pnl = 99.4, +5.0

        defter_etiketi = ExitManager._infer_exit_reason(sp, exit_price, realized_pnl)
        kapi_etiketi = ExitManager._infer_exit_reason_legacy(
            sp, exit_price, realized_pnl
        )
        assert defter_etiketi == "REAPER"
        assert kapi_etiketi == "SL"

        manager, calls = _cooldown_manager()
        manager._maybe_start_loss_cooldown("BTCUSDT", kapi_etiketi, realized_pnl, 0.0)
        assert calls == ["BTCUSDT"], "parite: cooldown D27 öncesiyle aynı başlar"

    def test_kayipta_iki_etiket_de_cooldown_baslatir(self):
        """Kayıpta (`realized_pnl < loss_threshold`) kapı zaten etiketten
        BAĞIMSIZDIR — iki uzayda da aynı sonuç."""
        for reason in ("SL", "REAPER", "TP_LADDER", "TRAIL"):
            manager, calls = _cooldown_manager()
            manager._maybe_start_loss_cooldown("ETHUSDT", reason, -7.0, 0.0)
            assert calls == ["ETHUSDT"], f"{reason}: kayıpta cooldown başlamalı"

    def test_brut_esikle_de_iki_etiket_ayni_davranir(self):
        """PnL kaynağı BRÜT tahminse çağıran komisyon eşiği geçer; kapının
        etiketten bağımsız kolu bu eşikte de aynı kalır."""
        for reason in ("SL", "REAPER"):
            manager, calls = _cooldown_manager()
            # brüt +0.4 ama eşik (gidiş-dönüş komisyonu) 1.0 → net eksi sayılır
            manager._maybe_start_loss_cooldown("SOLUSDT", reason, 0.4, 1.0)
            assert calls == ["SOLUSDT"], f"{reason}: brüt eşikte cooldown başlamalı"

    def test_tp_ladder_ve_reaper_artida_ayni_sonucu_verir(self):
        """Sapma yalnız "SL"/artı köşesindedir: TP1'e yakın ARTIDA kesilen bir
        pozisyonda iki uzay da cooldown BAŞLATMAZ (yani orada parite kendiliğinden
        korunur)."""
        for reason in ("TP_LADDER", "REAPER"):
            manager, calls = _cooldown_manager()
            manager._maybe_start_loss_cooldown("XRPUSDT", reason, 5.0, 0.0)
            assert calls == [], f"{reason}: artıda cooldown başlamamalı"

    def test_cb_yoksa_kapi_sessizce_gecer(self):
        """Cooldown geri çağrısı opsiyoneldir (eski kurulum/testler) — kapanış
        yolu ASLA bozulmaz."""
        manager = ExitManager.__new__(ExitManager)
        manager.logger = app_logger
        manager._loss_cooldown_cb = None
        manager._maybe_start_loss_cooldown("BTCUSDT", "REAPER", -9.0, 0.0)


# --------------------------------------------------------------------------
# 3) Motor tarafı — damga YALNIZ emir gittikten sonra konur
# --------------------------------------------------------------------------

class TestReaperMarksPosition:
    """`engine._reap_aged_positions` (D4) + D27/A1 damgası."""

    async def test_yasli_pozisyon_damgalanir_ve_emir_gonderilir(self):
        sp = _reaper_position(age_hours=9.0)
        engine, sent = _reaper_engine({"BTCUSDT": sp})

        await engine._reap_aged_positions()

        assert sp.reaper_close_at, "kapatılan pozisyon damgalanmalı"
        assert sent == [("BTCUSDT", "SELL", 1.5)], "LONG → reduce-only SELL, bir kez"
        # Damga `_infer_exit_reason`ı doğrudan yönlendirir.
        assert (
            ExitManager._infer_exit_reason(
                _position(reaper_close_at=sp.reaper_close_at), 99.05, -18.0
            )
            == "REAPER"
        )

    async def test_naive_opened_at_da_yas_hesabini_bozmaz(self):
        """`opened_at` tz'siz gelirse UTC varsayılır (restart kurtarması yolu)."""
        sp = _reaper_position(age_hours=9.0, aware=False)
        engine, sent = _reaper_engine({"BTCUSDT": sp})

        await engine._reap_aged_positions()

        assert sp.reaper_close_at
        assert len(sent) == 1

    async def test_short_pozisyon_buy_ile_kapatilir(self):
        sp = _reaper_position(age_hours=12.0, side=PositionSide.SHORT, quantity=3.0)
        engine, sent = _reaper_engine({"ETHUSDT": sp})

        await engine._reap_aged_positions()

        assert sent == [("ETHUSDT", "BUY", 3.0)]
        assert sp.reaper_close_at

    async def test_emir_hata_verirse_damga_konulmaz(self):
        """Kapanmayan bir pozisyon "REAPER" diye etiketlenmemelidir; motor da
        patlamamalıdır (sonraki turda yeniden denenir)."""
        async def _explode(symbol, close_side, qty):
            raise RuntimeError("borsa -1021")

        sp = _reaper_position(age_hours=9.0)
        engine, _ = _reaper_engine({"BTCUSDT": sp}, submit=_explode)

        await engine._reap_aged_positions()   # istisna DIŞARI sızmamalı

        assert sp.reaper_close_at is None

    async def test_quantize_hatasi_da_damga_birakmaz(self):
        """Emir hiç gönderilmediyse (miktar yuvarlama patladı) damga yok."""
        async def _explode_quantize(symbol, qty):
            raise RuntimeError("exchangeInfo yok")

        sp = _reaper_position(age_hours=9.0)
        engine, sent = _reaper_engine({"BTCUSDT": sp})
        engine.client = SimpleNamespace(quantize_quantity=_explode_quantize)

        await engine._reap_aged_positions()

        assert sp.reaper_close_at is None
        assert sent == []

    async def test_trailing_aktif_pozisyon_muaftir(self):
        """D4/2026-08-21: TP1 dolmuş koşucuyu yalnız stop/trailing durdurur,
        saat DEĞİL — emir de damga da yok."""
        sp = _reaper_position(age_hours=48.0, trailing_active=True)
        engine, sent = _reaper_engine({"BTCUSDT": sp})

        await engine._reap_aged_positions()

        assert sent == []
        assert sp.reaper_close_at is None

    async def test_yas_limiti_altindaki_pozisyona_dokunulmaz(self):
        sp = _reaper_position(age_hours=2.0)
        engine, sent = _reaper_engine({"BTCUSDT": sp})

        await engine._reap_aged_positions()

        assert sent == []
        assert sp.reaper_close_at is None

    async def test_max_hold_hours_sifirsa_reaper_hic_calismaz(self):
        sp = _reaper_position(age_hours=100.0)
        engine, sent = _reaper_engine({"BTCUSDT": sp}, max_hold_hours=0)

        await engine._reap_aged_positions()

        assert sent == []
        assert sp.reaper_close_at is None

    async def test_tur_basina_en_fazla_bir_kapanis(self):
        """2026-08-14 dersi: 5 eşzamanlı kapanış safety turunu şişirip watchdog
        restart'ı tetiklemişti."""
        positions = {
            "BTCUSDT": _reaper_position(age_hours=9.0),
            "ETHUSDT": _reaper_position(age_hours=11.0),
        }
        engine, sent = _reaper_engine(positions)

        await engine._reap_aged_positions()

        assert len(sent) == 1, "tur başına EN FAZLA bir reduce-only kapanış"
        stamped = [s for s, sp in positions.items() if sp.reaper_close_at]
        assert len(stamped) == 1, "yalnız emri gönderilen pozisyon damgalanır"
        assert stamped[0] == sent[0][0]


# --------------------------------------------------------------------------
# 4) Aile eşlemeleri — REAPER kendi ailesidir
# --------------------------------------------------------------------------

class TestFamilyMaps:
    """Etiket ayrımı rapor katmanında yeniden karıştırılmıyor."""

    def test_forensics_reaper_kendi_ailesidir(self):
        assert fx.exit_reason_family("REAPER") == "REAPER"
        assert fx.exit_reason_family("REAPER") != fx.exit_reason_family("SL")

    def test_forensics_eski_eslemeler_bozulmadi(self):
        assert fx.exit_reason_family("SL") == "SL"
        assert fx.exit_reason_family("TP_LADDER") == "TP_LADDER"
        assert fx.exit_reason_family("TRAIL") == "TRAIL"
        assert fx.exit_reason_family("TRAIL_MARKET") == "TRAIL"
        assert fx.exit_reason_family("BE_MARKET") == "TRAIL"
        assert fx.exit_reason_family("TV_EVENT") == "MANUAL"
        assert fx.exit_reason_family("RISK_EVENT") == "MANUAL"
        assert fx.exit_reason_family("MANUAL") == "MANUAL"
        assert fx.exit_reason_family("") == "UNKNOWN"

    def test_ledger_report_reaper_ailesi_ve_sirasi(self):
        assert lr.exit_reason_family("REAPER") == "REAPER"
        assert lr.exit_reason_family("reaper") == "REAPER"   # normalize
        assert "REAPER" in lr.EXIT_REASON_ORDER
        assert (
            lr.EXIT_REASON_ORDER.index("REAPER")
            == lr.EXIT_REASON_ORDER.index("SL") + 1
        ), "rapor sırasında REAPER, SL'nin hemen ardından gelir"

    def test_iki_esleme_reaper_konusunda_ayni_seyi_soyler(self):
        """Adli kayıt ve defter raporu AYNI aileyi kullanmalı — biri REAPER'ı
        SL'ye katarsa iki yüzey çelişir."""
        for reason in ("SL", "REAPER", "TP_LADDER", "TRAIL", "TRAIL_MARKET",
                       "BE_MARKET", "TV_EVENT", "RISK_EVENT", "MANUAL"):
            assert fx.exit_reason_family(reason) == lr.exit_reason_family(reason)


# --------------------------------------------------------------------------
# 5) Defter raporu — "geriye dönük veri düzeltmesi YAPILMADI" notu
# --------------------------------------------------------------------------

class TestLedgerReportNote:
    """Not olmadan rapor okuyucusu eski "SL"leri yeni ayrımla karıştırırdı."""

    def _bos_rapor(self):
        since = datetime(2026, 8, 20)
        until = datetime(2026, 8, 24, 23, 59, 59)
        return lr.build_report([], {}, since, until, [], [])

    def test_json_raporu_notu_ayri_anahtarda_tasir(self):
        report = self._bos_rapor()
        assert report["exit_reason_note"] == lr.REAPER_SPLIT_NOTE

    def test_notta_ayrimin_baslangic_tarihi_yazili(self):
        assert "2026-08-24" in lr.REAPER_SPLIT_NOTE
        # Geriye dönük düzeltme YAPILMADIĞI da okunabilir olmalı.
        assert "düzeltmesi YAPILMADI" in lr.REAPER_SPLIT_NOTE

    def test_metin_raporunda_not_gorunur(self):
        text = lr.render_text(self._bos_rapor())
        assert lr.REAPER_SPLIT_NOTE in text
        assert "ÇIKIŞ NEDENİ x YÖN" in text

    def test_md_raporunda_not_alinti_satiri_olarak_gorunur(self):
        md = lr.render_md(self._bos_rapor())
        assert lr.REAPER_SPLIT_NOTE in md
        assert f"> {lr.REAPER_SPLIT_NOTE}" in md

    def test_eski_sl_sayimi_bozulmadi(self):
        """SL ve REAPER AYRI satırlardır; SL satırı yalnız SL'leri sayar."""
        trades = [
            _closed_trade(1, "SL", -20.0),
            _closed_trade(2, "SL", -15.0),
            _closed_trade(3, "REAPER", -8.0),
            _closed_trade(4, "REAPER", +5.0),
        ]
        rows = lr.build_exit_reason_direction_table(trades)
        by_reason = {row["exit_reason"]: row for row in rows}

        assert set(by_reason) == {"SL", "REAPER"}
        assert by_reason["SL"]["trades"] == 2
        assert by_reason["SL"]["pnl"] == pytest.approx(-35.0)
        assert by_reason["REAPER"]["trades"] == 2
        assert by_reason["REAPER"]["pnl"] == pytest.approx(-3.0)
        assert by_reason["REAPER"]["wins"] == 1      # artıda kesilen kesme
        assert by_reason["SL"]["exit_family"] == "SL"
        assert by_reason["REAPER"]["exit_family"] == "REAPER"
        # Sıra: SL satırı REAPER'dan önce gelir.
        assert [row["exit_reason"] for row in rows] == ["SL", "REAPER"]

    def test_reaper_satiri_yon_kirilimini_korur(self):
        trades = [
            _closed_trade(1, "REAPER", -8.0, direction="LONG"),
            _closed_trade(2, "REAPER", +5.0, direction="SHORT"),
        ]
        rows = lr.build_exit_reason_direction_table(trades)
        assert {(r["exit_reason"], r["direction"]) for r in rows} == {
            ("REAPER", "LONG"), ("REAPER", "SHORT"),
        }


# --------------------------------------------------------------------------
# 6) Post-mortem — aile ayrımının KASITLI yan etkisi
# --------------------------------------------------------------------------

class TestForensicsPostmortemLosing:
    """`losing = exit_reason_family(reason) == "SL" or (net is not None and net < 0)`.

    REAPER artık SL ailesinde OLMADIĞI için yalnız NET NEGATİFSE kayıplı
    sayılır. Artıda kesilen bir pozisyonda "stop sonrası fiyat girişe döndü"
    (`noise_stop`) sorusunun zaten anlamı yoktur.
    """

    _CLOSED_MS = 1_000_000

    def _postmortem(self, reason: str, realized_pnl: float):
        candles = [
            _candle(self._CLOSED_MS + 60_000, high=99.0, low=98.0),
            _candle(self._CLOSED_MS + 120_000, high=101.5, low=99.0),  # girişi geçti
        ]
        return fx.postmortem_from_candles(
            entry=_pm_entry(fill_price=100.0),
            exit_=_pm_exit(reason=reason, realized_pnl=realized_pnl),
            candles=candles,
            closed_at_ms=self._CLOSED_MS,
        )

    def test_artida_kesilen_reaper_noise_stop_uretmez(self):
        out = self._postmortem("REAPER", +5.0)
        assert out["returned_to_entry"] is True   # fiyat girişe DÖNDÜ
        assert out["tags"] == []                  # ama kayıplı değil → etiket yok

    def test_negatif_reaper_eski_gibi_davranir(self):
        out = self._postmortem("REAPER", -8.0)
        assert out["returned_to_entry"] is True
        assert out["tags"] == [fx.TAG_NOISE_STOP]

    def test_sl_ailesi_artida_bile_kayipli_sayilmaya_devam_eder(self):
        """Asimetri KASITLIDIR: SL ailesi etiketin kendisiyle kayıplı sayılır;
        REAPER ise NET PnL ile. Bu test iki kolun ayrıldığını sabitler."""
        sl_out = self._postmortem("SL", +5.0)
        assert sl_out["tags"] == [fx.TAG_NOISE_STOP]

        reaper_out = self._postmortem("REAPER", +5.0)
        assert reaper_out["tags"] == []

    def test_reaper_pnl_olculemediyse_kayipli_sayilmaz(self):
        """`realized_pnl` yoksa (None) REAPER için karar verilemez — etiket
        ATILMAZ (fail-safe: uydurma bulgu üretme)."""
        out = fx.postmortem_from_candles(
            entry=_pm_entry(fill_price=100.0),
            exit_=_pm_exit(reason="REAPER", realized_pnl=None),
            candles=[_candle(self._CLOSED_MS + 60_000, high=101.5, low=99.0)],
            closed_at_ms=self._CLOSED_MS,
        )
        assert out["returned_to_entry"] is True
        assert out["tags"] == []
