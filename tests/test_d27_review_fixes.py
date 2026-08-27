"""D27 düşmanca incelemelerinin (2026-08-24) bulguları — KIRMIZI-ÖNCE testler.

İKİ bağımsız inceleme yapıldı ve bulguları burada TEK yerde sabitlenir:

* **inceleme-1** (kod okuma + çalıştırılabilir probe): K1, Y1–Y6, O1–O10,
  D1–D10, B2.
* **inceleme-2** (MUTASYON testi): sahte-yeşil testler ve global durum
  sızıntısı — bulgu 1–14.

Her test incelemenin ÇALIŞTIRILABİLİR bir probunu sabitler. Testler
düzeltmeden ÖNCE yazıldı ve düzeltmeden ÖNCE KIRMIZIydı.

Kapsam (numaralar incelemelerin tablolarıyla aynıdır):
  K1  `forensics_log.read_events` — satır bütçesi süzgeçten ÖNCE tükeniyordu
  Y1  kısmi mum penceresi sessizce `measured=True` yazıyordu
  Y2  tarama evreninden çıkan sembolün bekleyenleri hiç sona ermiyordu (=i2/7)
  Y6  TV kapısı karşı-olgusunun plan kaynağı raporda ayırt edilemiyordu
  O1  `pnl_source="estimated_gross"` iken `fee_estimate: 0.0` uyduruluyordu
  O3  `price_at` alt sınır uygulamıyordu (niyet ÖNCESİ mum)
  O4/D4/D5 örneklem boyutu, PF kenarları, ağırlıklı görünüm
  O5  API katmanı REAPER sınır notunu basmıyordu
  O6  `reconcile_mae` kayma kaynaklı yanlış-pozitif üretiyordu
  O7  `_place_tp_safely` dört farklı nedeni tek sayaca yazıyordu
  O9/O10 rapor katmanı: hata görünmüyordu, pencere `ts`ye uygulanıyordu
  D2  kapasite reddinde kalıcı boş kova kalıyordu (=i2/13)
  D3  geri-yazım atomik değildi
  D6  ufuk ile mum penceresi arasında doğrulama yoktu
  D10 `TRADINGBOT_LOG_DIR` kalıcı değiştiriliyordu
  i2/8  `dup_count` kelepçesi test edilmiyordu
  i2/11 "tüm ROI'ler 0" senaryosu test edilmiyordu
  i2/12 `simulate()` sırasız mumla yanlış sonuç veriyordu
  i2/14 `resolve` içinde `leverage=None` doğrudan test edilmiyordu

Not: inceleme-2'nin KRİTİK bulgusu (sahte cooldown parite testi)
`tests/test_reaper_exit_label.py::TestCooldownParityEndToEnd`tedir — orası
gerçek `_finalize_close` yolunu koşar.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from src.strategies.scalper import counterfactual as cf
from src.strategies.scalper import counterfactual_store as store
from src.strategies.scalper import forensics as fx
from src.strategies.scalper import forensics_log
from src.strategies.scalper import intent
from src.strategies.scalper.types import Candle

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import ledger_report as lr  # noqa: E402

BASE = 1_700_000_000.0
HOUR = 3600.0


def mum(
    baslangic_sn: float,
    *,
    high: float,
    low: float,
    close: float,
    acilis: Optional[float] = None,
    dakika: float = 5.0,
) -> Candle:
    return Candle(
        open_time=int(baslangic_sn * 1000),
        open=close if acilis is None else acilis,
        high=high,
        low=low,
        close=close,
        volume=1.0,
        close_time=int((baslangic_sn + dakika * 60.0) * 1000),
    )


def kur(**kwargs: Any) -> None:
    params: Dict[str, Any] = {
        "enabled": True,
        "horizons_h": (1.0,),
        "max_pending": 500,
        "dedup_sec": 300.0,
        "max_age_h": 48.0,
    }
    params.update(kwargs)
    store.configure(**params)


def kaydet(**kwargs: Any) -> Optional[Dict[str, Any]]:
    params: Dict[str, Any] = {
        "at": "2026-08-24T00:00:00+00:00",
        "at_epoch": BASE,
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "reason": intent.REASON_REGIME_GATE,
        "price": 100.0,
        "stop_price": 99.0,
        "tp1_price": 102.0,
        "leverage": 20,
        "strategy": "C",
        "source": "scanner",
    }
    params.update(kwargs)
    return store.register(**params)


@pytest.fixture(autouse=True)
def _temiz(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGBOT_LOG_DIR", str(tmp_path))
    forensics_log.reset_error_state()
    store.reset()
    kur()
    yield
    forensics_log.drain(2.0)
    store.reset()
    store.configure(enabled=False)


def jsonl_yaz(tmp_path, satirlar: List[Any], *, ad: str = "trades.jsonl") -> Path:
    path = Path(tmp_path) / ad
    with path.open("a", encoding="utf-8") as handle:
        for satir in satirlar:
            if isinstance(satir, str):
                handle.write(satir + "\n")
            else:
                handle.write(json.dumps(satir, ensure_ascii=False) + "\n")
    return path


def _pending(**kwargs: Any) -> Dict[str, Any]:
    params: Dict[str, Any] = {
        "at": "t",
        "at_epoch": BASE,
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "reason": intent.REASON_REGIME_GATE,
        "price": 100.0,
        "stop_price": 99.0,
        "tp1_price": 102.0,
        "leverage": 10,
        "horizons_h": [1.0],
    }
    params.update(kwargs)
    return cf.build_pending(**params)


# --------------------------------------------------------------------------
# K1 (KRİTİK) — okuma bütçesi süzgeçten ÖNCE tükeniyordu
# --------------------------------------------------------------------------

class TestK1OkumaButcesi:
    """İnceleme probu: 10 eski + 5 yeni satır, `since` + `limit=8` → BOŞ liste.

    Bu, "kapıları sorgulamak için yazılmış defterin" güvenle YANLIŞ
    "kanıt yok" üretmesi demekti: veri diskte duruyordu.
    """

    def _on_eski_bes_yeni(self, tmp_path) -> None:
        jsonl_yaz(tmp_path, [
            {"ts": f"2026-08-1{i}T00:00:00+00:00", "event": "counterfactual",
             "symbol": f"OLD{i}", "reason": "capacity"}
            for i in range(10)
        ], ad="trades-2026-08-19.jsonl")
        jsonl_yaz(tmp_path, [
            {"ts": f"2026-08-2{i}T00:00:00+00:00", "event": "counterfactual",
             "symbol": f"NEW{i}", "reason": "capacity"}
            for i in range(1, 6)
        ])

    def test_tavan_dolunca_YENI_satirlar_KAYBOLMAZ(self, tmp_path):
        self._on_eski_bes_yeni(tmp_path)
        satirlar = forensics_log.read_events(
            "counterfactual",
            since_iso="2026-08-20T00:00:00+00:00",
            limit=8,
        )
        assert [r["symbol"] for r in satirlar] == [
            "NEW1", "NEW2", "NEW3", "NEW4", "NEW5"
        ]

    def test_tavan_dolunca_truncated_bayragi_doner(self, tmp_path):
        self._on_eski_bes_yeni(tmp_path)
        sonuc = forensics_log.read_events_detailed("counterfactual", limit=3)
        assert sonuc.truncated is True
        assert len(sonuc.rows) == 3
        # Tavan dolduğunda EN YENİ satırlar korunur (rapor bugüne bakar).
        assert [r["symbol"] for r in sonuc.rows] == ["NEW3", "NEW4", "NEW5"]

    def test_tavan_dolmadiysa_truncated_FALSE_ve_sira_ESKI_ONCE(self, tmp_path):
        self._on_eski_bes_yeni(tmp_path)
        sonuc = forensics_log.read_events_detailed("counterfactual", limit=500)
        assert sonuc.truncated is False
        assert len(sonuc.rows) == 15
        assert sonuc.rows[0]["symbol"] == "OLD0"
        assert sonuc.rows[-1]["symbol"] == "NEW5"

    def test_directory_parametresi_env_i_KIRLETMEZ(self, tmp_path):
        """D10: `TRADINGBOT_LOG_DIR` kalıcı değiştirilmemeli."""
        baska = tmp_path / "baska"
        baska.mkdir()
        jsonl_yaz(baska, [
            {"ts": "2026-08-24T00:00:00+00:00", "event": "counterfactual",
             "symbol": "XUSDT"}
        ])
        satirlar = forensics_log.read_events(
            "counterfactual", directory=str(baska)
        )
        assert [r["symbol"] for r in satirlar] == ["XUSDT"]
        assert os.environ["TRADINGBOT_LOG_DIR"] == str(tmp_path)

    def test_rapor_katmani_truncated_bayragini_GORUNUR_kilar(self, tmp_path):
        """`--counterfactual` raporu "kayıt yok" ile "okuyamadık"ı ayırmalı."""
        jsonl_yaz(tmp_path, [
            {"ts": "2026-08-24T0%d:00:00+00:00" % i, "at": "2026-08-24T00:00:00+00:00",
             "event": "counterfactual", "symbol": f"S{i}", "reason": "capacity",
             "measured": True, "pnl_roi_pct": 1.0, "sim": {"outcome": "tp1"}}
            for i in range(1, 6)
        ])
        _rows, notes = lr.load_counterfactual_rows(
            datetime(2026, 8, 24), datetime(2026, 8, 25), log_dir=str(tmp_path)
        )
        # Tavan dolmadı → uyarı YOK.
        assert not any("tavanı doldu" in n for n in notes)

        section = lr.build_counterfactual_report([], truncated=True)
        satirlar = lr._counterfactual_error_lines(section)
        assert any("SATIR TAVANI DOLDU" in s for s in satirlar)


# --------------------------------------------------------------------------
# Y1 — kısmi pencere sessizce `measured=True`
# --------------------------------------------------------------------------

class TestY1KismiPencere:
    def test_kismi_pencere_OLCULDU_demez(self):
        """Probe: 20sa önceki niyet, 12.5sa mum → `measured=True` idi."""
        satir = cf.resolve(
            pending=_pending(horizons_h=[8.0]),
            # Pencere niyetten 7.5 SAAT SONRA başlıyor: ufkun yalnız kuyruğu.
            candles=[
                mum(BASE + 7.5 * HOUR + i * 300, high=100.5, low=99.6,
                    close=100.1)
                for i in range(6)
            ],
            now_epoch=BASE + 20 * HOUR,
        )
        assert satir is not None
        assert satir["measured"] is False
        assert satir["sim"]["outcome"] == cf.OUTCOME_NO_DATA
        assert satir["sim"]["gap"] == cf.GAP_PARTIAL_WINDOW
        assert satir["pnl_roi_pct"] is None

    def test_tam_pencere_HALA_olculur(self):
        satir = cf.resolve(
            pending=_pending(),
            candles=[
                mum(BASE + i * 300, high=100.5, low=99.6, close=100.1)
                for i in range(12)
            ],
            now_epoch=BASE + 2 * HOUR,
        )
        assert satir is not None
        assert satir["measured"] is True
        assert satir["sim"].get("gap") is None

    def test_BIR_MUMLUK_bosluk_KABUL_edilir(self):
        """Niyet anını içeren yarım mum look-ahead yüzünden dışlanır — bu
        bir mumluk boşluk NORMALDİR ve ölçümü engellememelidir."""
        satir = cf.resolve(
            pending=_pending(),
            candles=[
                mum(BASE + 300 + i * 300, high=100.5, low=99.6, close=100.1)
                for i in range(11)
            ],
            now_epoch=BASE + 2 * HOUR,
        )
        assert satir is not None
        assert satir["measured"] is True

    def test_IKI_mumluk_bosluk_KABUL_EDILMEZ(self):
        satir = cf.resolve(
            pending=_pending(),
            candles=[
                mum(BASE + 900 + i * 300, high=100.5, low=99.6, close=100.1)
                for i in range(11)
            ],
            now_epoch=BASE + 2 * HOUR,
        )
        assert satir is not None
        assert satir["measured"] is False
        assert satir["sim"]["gap"] == cf.GAP_PARTIAL_WINDOW


# --------------------------------------------------------------------------
# Y2 / i2-7 — tarama evreninden çıkan sembolün bekleyenleri
# --------------------------------------------------------------------------

class TestY2BekleyenSizintisi:
    def test_evrenden_cikan_sembolun_kaydi_yas_sinirinda_duser(self):
        """Probe: 100 saatlik 5 kayıt, `max_age_h=48` → `expired: 0` idi."""
        kur(horizons_h=(8.0,), max_age_h=48.0, dedup_sec=0.0)
        for i in range(5):
            kaydet(symbol="GONEUSDT", at_epoch=BASE + i)
        assert store.counters_snapshot()["pending"] == 5

        # Sembol bir daha HİÇ taranmıyor; BAŞKA bir sembol taranıyor.
        store.resolve_symbol("BTCUSDT", [], BASE + 100 * HOUR)

        snap = store.counters_snapshot()
        assert snap["expired"] == 5
        assert snap["pending"] == 0
        assert store.pending_for("GONEUSDT") == []
        assert store.bucket_keys() == []

    def test_kapasite_dolu_defter_kilitli_KALMAZ(self):
        """`max_pending` dolduğunda defter TÜM semboller için ölçmeyi bırakıyordu."""
        kur(horizons_h=(8.0,), max_age_h=48.0, dedup_sec=0.0, max_pending=3)
        for i in range(3):
            kaydet(symbol="GONEUSDT", at_epoch=BASE + i)
        # Kapasite dolu ama YAŞI GEÇMİŞ kayıtlar var: yeni kayıt için önce
        # süpürülür, sonra yer açılır.
        yeni = kaydet(symbol="FRESHUSDT", at_epoch=BASE + 100 * HOUR)
        assert yeni is not None
        assert store.counters_snapshot()["expired"] == 3
        assert store.counters_snapshot()["pending"] == 1

    def test_yas_sinirini_asmamis_kayit_SUPURULMEZ(self):
        kur(horizons_h=(8.0,), max_age_h=48.0, dedup_sec=0.0)
        kaydet(symbol="GONEUSDT")
        store.sweep_expired(BASE + 2 * HOUR)
        assert store.counters_snapshot()["pending"] == 1

    def test_supurme_cozumden_SONRA_kosar(self):
        """Yaşı geçmiş ama MUMLARI ELDE olan satır önce ÖLÇÜLÜR.

        Ölçebilecekken ölçmemek, sessizce düşürmekten daha kötüdür.
        """
        kur(horizons_h=(1.0,), max_age_h=1.0, dedup_sec=0.0)
        kaydet(symbol="BTCUSDT")
        cikti = store.resolve_symbol(
            "BTCUSDT",
            [mum(BASE + i * 300, high=103.0, low=100.0, close=102.5)
             for i in range(12)],
            BASE + 5 * HOUR,
        )
        assert len(cikti) == 1
        assert cikti[0]["measured"] is True
        assert store.counters_snapshot()["expired"] == 0


# --------------------------------------------------------------------------
# Y6 — TV kapısı karşı-olgusunun plan kaynağı
# --------------------------------------------------------------------------

class TestY6PlanKaynagiKirilimi:
    def _satir(self, plan_source: Optional[str], roi: float) -> Dict[str, Any]:
        return {
            "reason": intent.REASON_TV_CONFLUENCE,
            "measured": True,
            "dup_count": 1,
            "pnl_roi_pct": roi,
            "sim": {"outcome": cf.OUTCOME_TP1},
            "extra": {} if plan_source is None else {"plan_source": plan_source},
        }

    def test_summarize_plan_source_kirilimi_dondurur(self):
        ozet = cf.summarize([
            self._satir("roi_policy", 10.0),
            self._satir("roi_policy", 12.0),
            self._satir("signal", -4.0),
        ])
        kirilim = {row["plan_source"]: row for row in ozet["by_plan_source"]}
        assert set(kirilim) == {"roi_policy", "signal"}
        assert kirilim["roi_policy"]["n"] == 2
        assert kirilim["roi_policy"]["avg_roi_pct"] == 11.0
        assert kirilim["signal"]["n"] == 1
        assert kirilim["signal"]["avg_roi_pct"] == -4.0

    def test_plan_source_yoksa_ayri_kovada(self):
        ozet = cf.summarize([self._satir(None, 3.0)])
        kirilim = {row["plan_source"]: row for row in ozet["by_plan_source"]}
        assert cf.PLAN_SOURCE_UNKNOWN in kirilim

    def test_rapor_roi_policy_satirini_AYRI_basar(self):
        rapor = {"counterfactual": lr.build_counterfactual_report([
            self._satir("roi_policy", 10.0),
            self._satir("signal", -4.0),
        ])}
        satirlar = lr._counterfactual_plan_source_rows(rapor)
        assert [r[0] for r in satirlar] == ["roi_policy", "signal"]

    def test_metin_raporunda_plan_kaynagi_bolumu_ve_UYARI_vardir(self):
        section = lr.build_counterfactual_report([
            self._satir("roi_policy", 10.0),
        ])
        rapor = _bos_rapor(section)
        text = lr.render_text(rapor)
        assert "PLAN KAYNAĞI KIRILIMI" in text
        assert "roi_policy" in text
        assert "ROI politikasından YAKLAŞIKLANMIŞTIR" in text


def _bos_rapor(section: Dict[str, Any]) -> Dict[str, Any]:
    """`render_text`/`render_md`in beklediği asgari rapor — GERÇEK kurucudan."""
    return lr.build_report(
        [], {},
        datetime(2026, 8, 24), datetime(2026, 8, 25), ["2026-08-24"], [],
        counterfactual=section,
    )


# --------------------------------------------------------------------------
# O1 — `estimated_gross` yolunda `fee_estimate: 0.0` uyduruluyordu
# --------------------------------------------------------------------------

class TestO1KomisyonUydurmasi:
    def test_dogrulanmamis_brut_komisyon_OLCULMEDI_der(self):
        from src.strategies.scalper.exits import ExitManager

        sp = SimpleNamespace(tp1_done=False, tp2_done=False, trailing_active=False)
        brut, kaynak = ExitManager._forensics_gross(
            sp=sp, ledger=None, estimated_gross=12.5,
            pnl_source="estimated_gross",
        )
        assert brut is None
        assert kaynak == ExitManager.GROSS_SOURCE_SELF_REFERENTIAL

        belge = fx.build_exit(
            at="t", reason="SL", exit_price=99.0, entry_price=100.0,
            quantity=1.0, leverage=10, direction="LONG",
            realized_pnl=12.5, gross_pnl=brut, gross_source=kaynak,
            pnl_source="estimated_gross", mae_roi_pct=None, mfe_roi_pct=None,
            duration_sec=60.0, path={}, verification_notes=[],
        )
        assert belge["fee_estimate"] is None
        assert belge["fee_estimate_source"] == "unmeasured"

    def test_income_ile_dogrulanmis_net_komisyonu_OLCER(self):
        from src.strategies.scalper.exits import ExitManager

        sp = SimpleNamespace(tp1_done=False, tp2_done=False, trailing_active=False)
        brut, kaynak = ExitManager._forensics_gross(
            sp=sp, ledger=None, estimated_gross=12.5,
            pnl_source="binance_income_net",
        )
        assert brut == 12.5
        assert kaynak == ExitManager.GROSS_SOURCE_SINGLE


# --------------------------------------------------------------------------
# O3 — `price_at` alt sınır uygulamıyordu
# --------------------------------------------------------------------------

class TestO3PriceAtAltSinir:
    def test_niyet_ONCESI_mum_fiyat_olarak_DONMEZ(self):
        """Probe: hedef +1sa, dönen fiyat niyet ÖNCESİ mumdan 55.5 idi."""
        oncesi = mum(BASE - 600, high=60.0, low=50.0, close=55.5)
        assert cf.price_at([oncesi], BASE + HOUR, min_epoch=BASE) is None
        # Alt sınır verilmezse eski davranış korunur (geriye uyumluluk).
        assert cf.price_at([oncesi], BASE + HOUR) == 55.5

    def test_resolve_niyet_ONCESI_fiyati_ufka_yazmaz(self):
        satir = cf.resolve(
            pending=_pending(),
            candles=[mum(BASE - 600, high=60.0, low=50.0, close=55.5)],
            now_epoch=BASE + 2 * HOUR,
        )
        assert satir is not None
        assert satir["horizons"][0]["price"] is None
        assert satir["horizons"][0]["roi_pct"] is None


# --------------------------------------------------------------------------
# O4 / D4 / D5 / i2-11 — rapor kenarları
# --------------------------------------------------------------------------

class TestO4D4D5RaporKenarlari:
    def _satir(self, roi: Optional[float], outcome: str = cf.OUTCOME_TP1,
               dup: int = 1) -> Dict[str, Any]:
        return {
            "reason": intent.REASON_REGIME_GATE,
            "measured": True,
            "dup_count": dup,
            "pnl_roi_pct": roi,
            "sim": {"outcome": outcome},
        }

    def test_roi_ornekleminin_boyutu_raporlanir(self):
        """O4: `measured=True` + `pnl_roi_pct=None` mümkündür."""
        ozet = cf.summarize([self._satir(5.0), self._satir(None)])
        satir = ozet["by_reason"][0]
        assert satir["measured"] == 2
        assert satir["roi_n"] == 1          # ROI'si olan YALNIZ bir satır

    def test_kayip_yoksa_PF_isareti_ayrilir(self):
        ozet = cf.summarize([self._satir(5.0), self._satir(3.0)])
        satir = ozet["by_reason"][0]
        assert satir["profit_factor"] is None
        assert satir["profit_factor_note"] == cf.PF_NOTE_NO_LOSS

    def test_hicbir_olcum_yoksa_PF_notu_FARKLIDIR(self):
        ozet = cf.summarize([{
            "reason": intent.REASON_REGIME_GATE, "measured": False,
            "sim": {"outcome": cf.OUTCOME_NO_DATA},
        }])
        assert ozet["by_reason"][0]["profit_factor_note"] == cf.PF_NOTE_NO_SAMPLE

    def test_hepsi_kayipsa_PF_sifir_ve_not_yok(self):
        ozet = cf.summarize([self._satir(-5.0), self._satir(-3.0)])
        satir = ozet["by_reason"][0]
        assert satir["profit_factor"] == 0.0
        assert satir["profit_factor_note"] is None

    def test_TUM_roiler_sifirken_sahte_kesinlik_URETILMEZ(self):
        """i2/11: `PF=None`, `avg=0.0`, `ci=[0,0]` senaryosu test edilmiyordu."""
        ozet = cf.summarize([self._satir(0.0), self._satir(0.0)])
        satir = ozet["by_reason"][0]
        assert satir["avg_roi_pct"] == 0.0
        assert satir["roi_n"] == 2
        assert satir["profit_factor"] is None
        assert satir["profit_factor_note"] == cf.PF_NOTE_NO_LOSS
        # SIFIR varyansta `[0.0, 0.0]` "kesin biliyoruz" gibi okunurdu.
        assert satir["ci95_roi_pct"] is None

    def test_agirlikli_gorunum_yeniden_kurulabilir(self):
        ozet = cf.summarize([
            self._satir(5.0, cf.OUTCOME_TP1, dup=3),
            self._satir(-5.0, cf.OUTCOME_STOP, dup=2),
        ])
        satir = ozet["by_reason"][0]
        assert satir["collapsed"] == 5
        assert satir["collapsed_tp1"] == 3
        assert satir["collapsed_stop"] == 2

    @pytest.mark.parametrize("bozuk", [0, -5, "x", None, False, 0.4])
    def test_dup_count_KELEPCESI_bir_altina_inmez(self, bozuk):
        """i2/8: `out < 1` kelepçesini kaldıran mutasyon HAYATTA KALMIŞTI.

        `dup_count=0`/negatif/bozuk bir satır `collapsed` toplamını sessizce
        DÜŞÜRÜR — "kaç ham niyet toplandı" yanlış raporlanırdı.
        """
        assert cf._dup_count(bozuk) == 1
        ozet = cf.summarize([self._satir(5.0, dup=bozuk)])
        assert ozet["by_reason"][0]["collapsed"] == 1


# --------------------------------------------------------------------------
# O5 — API katmanı REAPER sınır notunu basmıyordu
# --------------------------------------------------------------------------

class TestO5ReaperNotu:
    def test_summarize_exit_reason_note_dondurur(self):
        ozet = fx.summarize([{"tags": [], "pnl": 1.0, "exit_reason": "REAPER"}])
        assert ozet["exit_reason_note"] == fx.REAPER_SPLIT_NOTE
        assert "2026-08-24" in ozet["exit_reason_note"]

    def test_rapor_ve_api_notu_AYNI_metindir(self):
        """İki katman ayrışırsa okuyucu iki farklı sınır duyar."""
        assert lr.REAPER_SPLIT_NOTE == fx.REAPER_SPLIT_NOTE


# --------------------------------------------------------------------------
# O6 — `reconcile_mae` kayma kaynaklı yanlış-pozitif
# --------------------------------------------------------------------------

class TestO6MaeKelepcesi:
    def test_kucuk_kayma_farki_DUZELTME_sayilmaz(self):
        """Probe: `mae=-5.0`, `exit_roi=-6.0` (10x) → `corrected` idi.

        İhlal 1.0 ROI puanı = **%0.1 fiyat** — bu bir yoklama kusuru değil,
        kayma/mark-vs-fill farkıdır.
        """
        deger, kaynak = fx.reconcile_mae(
            mae_roi_pct=-5.0, price_move_pct=-0.6, leverage=10
        )
        assert kaynak == fx.MAE_SOURCE_SLIPPAGE
        assert deger == -6.0        # kelepçe YİNE uygulanır (fizik değişmez)

    def test_buyuk_ihlal_HALA_corrected(self):
        deger, kaynak = fx.reconcile_mae(
            mae_roi_pct=-1.0, price_move_pct=-6.0, leverage=10
        )
        assert kaynak == fx.MAE_SOURCE_CORRECTED
        assert deger == -60.0

    def test_esik_FIYAT_tabanlidir_kaldiracla_buyumez(self):
        """Aynı %0.1 fill farkı 20x'te 2.0, 1x'te 0.1 ROI puanı eder."""
        _, k20 = fx.reconcile_mae(
            mae_roi_pct=-4.0, price_move_pct=-0.3, leverage=20
        )
        _, k1 = fx.reconcile_mae(
            mae_roi_pct=-1.0, price_move_pct=-2.0, leverage=1
        )
        assert k20 == fx.MAE_SOURCE_SLIPPAGE      # ihlal 2.0 ROI = %0.1 fiyat
        assert k1 == fx.MAE_SOURCE_CORRECTED      # ihlal 1.0 ROI = %1.0 fiyat


# --------------------------------------------------------------------------
# O7 — `_place_tp_safely` dört nedeni tek sayaca yazıyordu
# --------------------------------------------------------------------------

class TestO7EmirKimligi:
    def _executor(self):
        from src.strategies.scalper.executor import ScalpExecutor

        ex = object.__new__(ScalpExecutor)
        ex._order_health = {
            "tp1_missing": 0, "tp1_unidentified": 0, "tp2_missing": 0,
            "tp2_unidentified": 0, "tp_wrong_side": 0, "partial_fill_split": 0,
            "last_symbol": None, "last_at": None,
        }
        ex.logger = SimpleNamespace(
            warning=lambda *a, **k: None,
            error=lambda *a, **k: None,
            critical=lambda *a, **k: None,
        )
        return ex

    async def test_kimliksiz_kabul_AYRI_neden_dondurur(self):
        """Emir borsaca KABUL EDİLDİ ama `algoId`/`orderId` okunamadı."""
        from unittest.mock import AsyncMock

        from src.strategies.scalper.executor import ScalpExecutor

        ex = self._executor()
        ex.client = SimpleNamespace(place_take_profit=AsyncMock(return_value={}))
        algo_id, neden = await ScalpExecutor._place_tp_safely(
            ex, "BTCUSDT", "SELL", 102.0, 1.0, "TP1"
        )
        assert algo_id is None
        assert neden == ScalpExecutor.TP_FAIL_UNIDENTIFIED

    async def test_basarili_emir_neden_DONDURMEZ(self):
        from unittest.mock import AsyncMock

        from src.strategies.scalper.executor import ScalpExecutor

        ex = self._executor()
        ex.client = SimpleNamespace(
            place_take_profit=AsyncMock(return_value={"algoId": "a1"})
        )
        assert await ScalpExecutor._place_tp_safely(
            ex, "BTCUSDT", "SELL", 102.0, 1.0, "TP1"
        ) == ("a1", None)

    async def test_sifir_miktar_AYRI_neden_dondurur(self):
        from src.strategies.scalper.executor import ScalpExecutor

        ex = self._executor()
        ex.client = SimpleNamespace()
        assert await ScalpExecutor._place_tp_safely(
            ex, "BTCUSDT", "SELL", 102.0, 0.0, "TP1"
        ) == (None, ScalpExecutor.TP_FAIL_ZERO_QTY)

    def test_kimliksiz_kabul_AYRI_sayilir(self):
        from src.strategies.scalper.executor import ScalpExecutor

        ex = self._executor()
        ex._count_order_health("tp1_unidentified", "BTCUSDT")
        snap = ScalpExecutor.order_health_snapshot(ex)
        assert snap["tp1_unidentified"] == 1
        assert snap["tp1_missing"] == 0


# --------------------------------------------------------------------------
# O9 / O10 — rapor katmanı
# --------------------------------------------------------------------------

class TestO9O10Rapor:
    def test_karsi_olgu_hatasi_metin_raporda_GORUNUR(self):
        """"Ölçüm yok" ile "ölçüm bozuldu" aynı şey değildir."""
        section = {"error": "ImportError: yok", "by_reason": [], "overall": {}}
        satirlar = lr._counterfactual_error_lines(section)
        assert any("ImportError" in s for s in satirlar)

        text = lr.render_text(_bos_rapor(section))
        assert "ImportError" in text
        md = lr.render_md(_bos_rapor(section))
        assert "ImportError" in md

    def test_pencere_suzgeci_NIYET_anina_uygulanabilir(self, tmp_path):
        """Probe: 8sa ufukla gece 23:00'teki ret ERTESİ GÜNE düşüyordu."""
        jsonl_yaz(tmp_path, [
            {"ts": "2026-08-25T07:00:00+00:00", "at": "2026-08-24T23:00:00+00:00",
             "event": "counterfactual", "symbol": "BTCUSDT", "reason": "capacity"},
        ])
        satirlar, _ = lr.load_counterfactual_rows(
            datetime(2026, 8, 24), datetime(2026, 8, 25),
            log_dir=str(tmp_path), stamp_field="at",
        )
        assert [r["symbol"] for r in satirlar] == ["BTCUSDT"]

        # Varsayılan (`ts`) davranışı DEĞİŞMEDİ: satır pencerenin DIŞINDA.
        varsayilan, _ = lr.load_counterfactual_rows(
            datetime(2026, 8, 24), datetime(2026, 8, 25), log_dir=str(tmp_path)
        )
        assert varsayilan == []


# --------------------------------------------------------------------------
# D2 / D3 / i2-13 — defter hijyeni
# --------------------------------------------------------------------------

class TestD2D3DefterHijyeni:
    def test_kapasite_reddi_BOS_KOVA_birakmaz(self):
        kur(horizons_h=(1.0,), max_pending=1, dedup_sec=0.0, max_age_h=0.0)
        kaydet(symbol="BTCUSDT")
        assert kaydet(symbol="ETHUSDT") is None
        assert store.pending_for("ETHUSDT") == []
        assert store.bucket_keys() == ["BTCUSDT"]

    def test_yazim_istisnasi_kovayi_sayacla_TUTARSIZ_birakmaz(self):
        """D3: geri-yazım `finally`de olmasaydı çözülmüş satır kovada kalırdı.

        Sonuç: bir sonraki turda YENİDEN çözülüp YENİDEN loglanır ve
        `_pending_count` bucket ile tutarsız kalırdı — o sayaç `register`
        kapasite kapısını besliyor.
        """
        kur(horizons_h=(1.0,), dedup_sec=0.0)
        kaydet(symbol="BTCUSDT", at_epoch=BASE)
        kaydet(symbol="BTCUSDT", at_epoch=BASE + 600)

        class _PatlayanHalka:
            def append(self, _row):
                raise RuntimeError("bozuk halka")

        orijinal = store._recent
        store._recent = _PatlayanHalka()
        try:
            store.resolve_symbol(
                "BTCUSDT",
                [mum(BASE + i * 300, high=103.0, low=100.0, close=101.0)
                 for i in range(24)],
                BASE + 2 * HOUR,
            )
        finally:
            store._recent = orijinal

        snap = store.counters_snapshot()
        # İlk satır çözülmüş sayıldı; kova ve sayaç TUTARLI.
        assert snap["pending"] == len(store.pending_for("BTCUSDT"))
        assert snap["pending"] >= 0


# --------------------------------------------------------------------------
# D6 — ufuk / mum penceresi doğrulaması
# --------------------------------------------------------------------------

class TestD6UfukUyarisi:
    def _engine(self, tf: str, uyarilar: List[str]):
        from src.strategies.scalper.engine import ScalperEngine as ScalpEngine

        motor = object.__new__(ScalpEngine)
        motor.cfg = SimpleNamespace(scalper_tf_entry=tf)
        motor.logger = SimpleNamespace(warning=uyarilar.append)
        return motor

    def test_1m_diliminde_8_saatlik_ufuk_RESTART_RISKINI_UYARIR(self):
        kur(horizons_h=(8.0,))
        uyarilar: List[str] = []
        self._engine("1m", uyarilar)._warn_counterfactual_horizon_fit()
        assert len(uyarilar) == 1
        assert "rolling mum tamponu" in uyarilar[0]
        assert "restart" in uyarilar[0]
        assert "Ufku küçültmek gerekmez" in uyarilar[0]
        assert "no_data" not in uyarilar[0]

    def test_5m_diliminde_8_saatlik_ufuk_UYARMAZ(self):
        kur(horizons_h=(8.0,))
        uyarilar: List[str] = []
        self._engine("5m", uyarilar)._warn_counterfactual_horizon_fit()
        assert uyarilar == []

    def test_defter_kapaliyken_UYARMAZ(self):
        kur(enabled=False, horizons_h=(8.0,))
        uyarilar: List[str] = []
        self._engine("1m", uyarilar)._warn_counterfactual_horizon_fit()
        assert uyarilar == []


# --------------------------------------------------------------------------
# i2-12 / i2-14 — `simulate` sırasızlığı ve kaldıraçsız `resolve`
# --------------------------------------------------------------------------

class TestSimulateSiralamaVeKaldirac:
    def test_sirasiz_mum_listesi_SONUCU_DEGISTIRMEZ(self):
        """i2/12: sıralamayı yalnız `window()` yapıyordu; `simulate` doğrudan
        çağrıldığında sırasız liste "önce hangi seviye vuruldu" sorusuna
        SESSİZCE yanlış cevap veriyordu."""
        stop_mumu = mum(BASE + 600, high=100.4, low=98.5, close=98.8)
        tp_mumu = mum(BASE, high=102.5, low=99.8, close=102.2)

        sirali = cf.simulate(
            direction="LONG", entry_price=100.0, stop_price=99.0,
            tp1_price=102.0, candles=[tp_mumu, stop_mumu],
        )
        sirasiz = cf.simulate(
            direction="LONG", entry_price=100.0, stop_price=99.0,
            tp1_price=102.0, candles=[stop_mumu, tp_mumu],
        )
        assert sirali["outcome"] == cf.OUTCOME_TP1
        assert sirali == sirasiz

    def test_kaldirac_yoksa_pnl_roi_UYDURULMAZ(self):
        """i2/14: `resolve` düzeyinde doğrudan test edilmiyordu."""
        satir = cf.resolve(
            pending=_pending(leverage=None),
            candles=[mum(BASE + i * 300, high=100.5, low=99.6, close=100.1)
                     for i in range(12)],
            now_epoch=BASE + 2 * HOUR,
        )
        assert satir is not None
        assert satir["leverage"] is None
        assert satir["pnl_roi_pct"] is None
        assert all(h["roi_pct"] is None for h in satir["horizons"])
        # Fiyat hareketi YİNE ölçülür — ölçülemeyen yalnız ROI'dir.
        assert satir["sim"]["price_move_pct"] is not None
        assert satir["measured"] is True


# --------------------------------------------------------------------------
# D1 — plan hesabı defter bayrağından bağımsız koşuyordu
# --------------------------------------------------------------------------

class TestD1PlanBayragi:
    def test_defter_kapaliyken_plan_HESAPLANMAZ(self, monkeypatch):
        from src.strategies.scalper.engine import ScalperEngine as ScalpEngine

        cagrildi: List[Any] = []
        motor = object.__new__(ScalpEngine)
        motor._forensics_enabled = lambda: True
        motor._counterfactual_plan = lambda signal: (
            cagrildi.append(signal) or (1.0, 2.0, 3.0, 4)
        )
        kur(enabled=False)

        kaydedilen: List[Dict[str, Any]] = []
        monkeypatch.setattr(
            intent, "record", lambda **kw: kaydedilen.append(kw)
        )
        ScalpEngine._record_intent(
            motor, symbol="BTCUSDT", direction="LONG", stage="gate",
            decision=intent.DECISION_DENY, reason=intent.REASON_REGIME_GATE,
            signal=SimpleNamespace(),
        )
        assert cagrildi == []
        assert kaydedilen and kaydedilen[0]["price"] is None

    def test_defter_ACIKKEN_plan_hesaplanir(self, monkeypatch):
        from src.strategies.scalper.engine import ScalperEngine as ScalpEngine

        cagrildi: List[Any] = []
        motor = object.__new__(ScalpEngine)
        motor._forensics_enabled = lambda: True
        motor._counterfactual_plan = lambda signal: (
            cagrildi.append(signal) or (1.0, 2.0, 3.0, 4)
        )
        kur(enabled=True)

        monkeypatch.setattr(intent, "record", lambda **kw: None)
        ScalpEngine._record_intent(
            motor, symbol="BTCUSDT", direction="LONG", stage="gate",
            decision=intent.DECISION_DENY, reason=intent.REASON_REGIME_GATE,
            signal=SimpleNamespace(),
        )
        assert len(cagrildi) == 1
