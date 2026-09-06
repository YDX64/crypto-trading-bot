"""D27/B — karşı-olgu defterinin DURUM katmanı + kancalarının testleri.

`tests/test_counterfactual.py` SAF çekirdeği (`counterfactual.py`) sınar;
burada onun ÜSTÜNDEKİ her şey sınanır ve çekirdek testleri TEKRARLANMAZ:

  1. `configure` / `parse_horizons` — ufuk ayrıştırma ve KAPALI defterin
     hiçbir şey yapmaması.
  2. `register` — dedup penceresi (`dup_count`), kapasite tavanı (YENİ düşer,
     eski korunur), bozuk girdinin kayıt açmaması.
  3. `resolve_symbol` — olgunlaşma, TP1/STOP, mumsuz pencere (`measured=False`
     ve TÜM sayılar `None`), `max_age_h` düşürmesi, kapalı defter, kova ayrımı.
  4. `_fill_plan` — planı OLMAYAN niyet (TV sağlaması yolu) için referans
     girişin niyet anından SONRAKİ İLK mumun `open`'ı olması (look-ahead YOK)
     ve ROI politikası eksikken plan UYDURULMAMASI.
  5. JSONL kalıcılığı — `forensics_log.read_events` süzgeçleri ve bozuk
     satıra dayanıklılık.
  6. `engine` kancaları — `_counterfactual_plan` saflığı (sinyal DEĞİŞMEZ),
     `_record_intent`in yalnız deny/error'da defter açması, adli kayıt
     KAPALIYKEN hiçbir şey yapmaması, arızanın motora SIZMAMASI.
  7. `intent.build_intent`in yeni dört alanı (D27/B kalıcı izi).
  8. `scripts/ledger_report.py --counterfactual` bölümü.
  9. `/scalper/counterfactual` ucu ve `/api/status` gövdesindeki blok.

Tüm testler DETERMİNİSTİKtir: sabit epoch'lar kullanılır ve `at_epoch`/
`now_epoch` DAİMA elle geçilir. Modül düzeyi durum HER testte sıfırlanır.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from src.strategies.scalper import counterfactual as cf
from src.strategies.scalper import counterfactual_store as store
from src.strategies.scalper import forensics_log
from src.strategies.scalper import intent
from src.strategies.scalper.types import Candle, Direction, Regime, ScalpSignal

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import ledger_report as lr  # noqa: E402  (sys.path eklemesinden sonra import)


#: Sabit referans an (saniye). Bugünün tarihine bağlı DEĞİLDİR.
BASE = 1_700_000_000.0
HOUR = 3600.0


# --------------------------------------------------------------------------
# Ortak yardımcılar
# --------------------------------------------------------------------------

def mum(
    baslangic_sn: float,
    *,
    high: float,
    low: float,
    close: float,
    acilis: Optional[float] = None,
    dakika: float = 5.0,
) -> Candle:
    """Test mumu. `Candle` zamanları MİLİSANİYEdir; burada saniyeden çevrilir."""
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
    """`configure` için testlere uygun varsayılanlar (defter AÇIK)."""
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
    """`register` için testlere uygun varsayılanlar (planı OLAN niyet)."""
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
def _temiz_defter(tmp_path, monkeypatch):
    """Defter MODÜL DÜZEYİ durumdur: testler arası SIZMAMALI.

    JSONL yazımı da tmp'ye alınır — bir teşhis testi repo `logs/`ini
    kirletmemeli. Sıra önemlidir: `drain()` env geri alınmadan ÖNCE koşar,
    böylece yazıcı iş parçacığı satırı hâlâ tmp dizinine yazar.
    """
    monkeypatch.setenv("TRADINGBOT_LOG_DIR", str(tmp_path))
    forensics_log.reset_error_state()
    store.reset()
    kur()
    yield
    forensics_log.drain(2.0)
    store.reset()
    store.configure(enabled=False)


def jsonl_yaz(tmp_path, satirlar: List[Any], *, ad: str = "trades.jsonl") -> Path:
    """JSONL dosyasını ELLE kur: sözlükler serileştirilir, metinler ham yazılır."""
    path = Path(tmp_path) / ad
    with path.open("a", encoding="utf-8") as handle:
        for satir in satirlar:
            if isinstance(satir, str):
                handle.write(satir + "\n")
            else:
                handle.write(json.dumps(satir, ensure_ascii=False) + "\n")
    return path


# --------------------------------------------------------------------------
# 1. configure / parse_horizons
# --------------------------------------------------------------------------

class TestConfigure:
    def test_virgullu_metin_ufka_cevrilir(self):
        assert store.parse_horizons("1,4,8") == (1.0, 4.0, 8.0)

    def test_noktali_virgul_ve_sira_normalize_edilir(self):
        # ";" da ayraçtır ve sonuç DAİMA artan sıralıdır.
        assert store.parse_horizons("8;4;1") == (1.0, 4.0, 8.0)

    def test_liste_girdisi_kabul_edilir(self):
        assert store.parse_horizons([8, 4, 1]) == (1.0, 4.0, 8.0)
        assert store.parse_horizons((2, 2, 1)) == (1.0, 2.0)  # tekilleştirme

    def test_bozuk_ve_bos_girdi_varsayilana_duser(self):
        assert store.parse_horizons("abc") == store.DEFAULT_HORIZONS
        assert store.parse_horizons("") == store.DEFAULT_HORIZONS
        assert store.parse_horizons(None) == store.DEFAULT_HORIZONS
        # Tamamı süzülen bir liste de varsayılana düşer ("ufuk yok" ≠ "0 saat").
        assert store.parse_horizons([0, -1, "x", None]) == store.DEFAULT_HORIZONS

    def test_negatif_ve_sifir_ufuklar_suzulur(self):
        # Bir ölçüm ufku ancak GELECEKTE bir an olabilir.
        assert store.parse_horizons([4, 0, -2, 1]) == (1.0, 4.0)

    def test_configure_ufku_ve_bayragi_saklar(self):
        kur(horizons_h=(2.0, 1.0))
        assert store.enabled() is True
        assert store.horizons() == (1.0, 2.0)

    def test_bos_ufuk_listesi_varsayilani_korur(self):
        kur(horizons_h=[])
        assert store.horizons() == store.DEFAULT_HORIZONS

    def test_kapali_defter_hicbir_sey_yapmaz(self):
        """KAPALI defter: `register` None döner ve HİÇBİR sayaç artmaz."""
        kur(enabled=False)
        assert kaydet() is None
        snap = store.counters_snapshot()
        assert snap["enabled"] is False
        assert snap["registered"] == 0
        assert snap["pending"] == 0
        assert snap["dedup_hits"] == 0
        assert snap["dropped_full"] == 0
        assert store.pending_for("BTCUSDT") == []


# --------------------------------------------------------------------------
# 2. register — dedup, kapasite, savunmalı okuma
# --------------------------------------------------------------------------

class TestRegister:
    def test_acik_defter_kaydi_kuyruga_alir(self):
        row = kaydet()
        assert row is not None
        assert row["symbol"] == "BTCUSDT" and row["direction"] == "LONG"
        assert row["dup_count"] == 1
        snap = store.counters_snapshot()
        assert snap["pending"] == 1 and snap["registered"] == 1

    def test_dedup_penceresinde_ikinci_kayit_acilmaz(self):
        kaydet(at_epoch=BASE)
        ikinci = kaydet(at_epoch=BASE + 10.0)
        assert ikinci is None
        snap = store.counters_snapshot()
        # Kayıt AÇILMAZ ama ağırlık kaybolmaz: `dup_count` artar.
        assert snap["pending"] == 1 and snap["registered"] == 1
        assert snap["dedup_hits"] == 1
        bekleyen = store.pending_for("BTCUSDT")
        assert len(bekleyen) == 1 and bekleyen[0]["dup_count"] == 2

    def test_farkli_yon_ayri_kayit_acar(self):
        kaydet(direction="LONG")
        assert kaydet(direction="SHORT", at_epoch=BASE + 10.0) is not None
        assert store.counters_snapshot()["pending"] == 2
        assert store.counters_snapshot()["dedup_hits"] == 0

    def test_farkli_gerekce_ayri_kayit_acar(self):
        kaydet(reason=intent.REASON_REGIME_GATE)
        assert kaydet(
            reason=intent.REASON_TV_CONFLUENCE, at_epoch=BASE + 10.0
        ) is not None
        assert store.counters_snapshot()["pending"] == 2

    def test_dedup_penceresi_disinda_yeni_kayit_acilir(self):
        kur(dedup_sec=300.0)
        kaydet(at_epoch=BASE)
        assert kaydet(at_epoch=BASE + 301.0) is not None
        snap = store.counters_snapshot()
        assert snap["pending"] == 2 and snap["dedup_hits"] == 0

    def test_kapasite_dolunca_YENI_kayit_duser(self):
        """Tavan dolduğunda ESKİLER korunur, YENİ düşer (forensics_log ilkesi)."""
        kur(max_pending=2, dedup_sec=0.0)
        kaydet(at_epoch=BASE)
        kaydet(at_epoch=BASE + 1.0)
        assert kaydet(at_epoch=BASE + 2.0) is None
        snap = store.counters_snapshot()
        assert snap["pending"] == 2 and snap["dropped_full"] == 1
        assert snap["registered"] == 2
        kalanlar = [row["at_epoch"] for row in store.pending_for("BTCUSDT")]
        assert kalanlar == [BASE, BASE + 1.0]

    def test_bozuk_girdi_kayit_acmaz_ve_patlatmaz(self):
        assert kaydet(symbol=None) is None
        assert kaydet(direction=None) is None
        assert kaydet(at_epoch=None) is None
        snap = store.counters_snapshot()
        assert snap["registered"] == 0 and snap["pending"] == 0
        assert snap["dedup_hits"] == 0 and snap["dropped_full"] == 0

    def test_sembol_buyuk_harfe_indirgenir(self):
        row = kaydet(symbol="btcusdt")
        assert row is not None and row["symbol"] == "BTCUSDT"
        # `pending_for` de aynı kuralı uygular (tek kova).
        assert len(store.pending_for("btcusdt")) == 1

    def test_plan_source_extraya_islenir(self):
        row = kaydet(plan_source="signal")
        assert row is not None and row["extra"]["plan_source"] == "signal"

    def test_pending_for_kopya_dondurur(self):
        kaydet()
        kopya = store.pending_for("BTCUSDT")[0]
        kopya["dup_count"] = 999
        assert store.pending_for("BTCUSDT")[0]["dup_count"] == 1


# --------------------------------------------------------------------------
# 3. resolve_symbol
# --------------------------------------------------------------------------

class TestResolveSymbol:
    def test_1m_kayan_pencereler_8saatlik_ufku_rest_eklemeden_biriktirir(self):
        """D28: 150 x 1m tek başına 8h ufku kapsamaz; ardışık motor
        turları aynı bekleyen niyet için birleşince ölçüm tam olmalıdır."""
        kur(horizons_h=(8.0,))
        kaydet(price=100.0, stop_price=90.0, tp1_price=110.0)

        candles = [
            mum(
                BASE + minute * 60.0,
                high=100.5,
                low=99.5,
                close=100.0,
                dakika=1.0,
            )
            for minute in range(480)
        ]
        for end in (150, 300, 450):
            out = store.resolve_symbol(
                "BTCUSDT",
                candles[max(0, end - 150):end],
                BASE + end * 60.0,
            )
            assert out == []

        out = store.resolve_symbol(
            "BTCUSDT", candles[-150:], BASE + 8 * HOUR + 1.0
        )
        assert len(out) == 1
        assert out[0]["measured"] is True
        assert out[0]["sim"]["outcome"] == "open"
        assert out[0]["sim"]["bars"] == 480
        snap = store.counters_snapshot()
        assert snap["measured"] == 1
        assert snap["candle_buffer_symbols"] == 0
        assert snap["candle_buffer_bars"] == 0

    def test_olgunlasmamis_kayit_kuyrukta_kalir(self):
        kur(horizons_h=(1.0,))
        kaydet()
        cikti = store.resolve_symbol(
            "BTCUSDT", [mum(BASE + 300, high=103, low=100, close=101)],
            BASE + 600.0,
        )
        assert cikti == []
        snap = store.counters_snapshot()
        assert snap["pending"] == 1 and snap["resolved"] == 0

    def test_olgunlasmis_tp1_cozulur_ve_kuyruktan_cikar(self):
        kur(horizons_h=(1.0,))
        kaydet(price=100.0, stop_price=99.0, tp1_price=102.0)
        cikti = store.resolve_symbol(
            "BTCUSDT",
            [
                mum(BASE + 300, high=101.0, low=100.0, close=100.5),
                mum(BASE + 600, high=103.0, low=100.2, close=102.5),
            ],
            BASE + 2 * HOUR,
        )
        assert len(cikti) == 1
        satir = cikti[0]
        assert satir["sim"]["outcome"] == "tp1"
        assert satir["measured"] is True
        snap = store.counters_snapshot()
        assert snap["resolved"] == 1 and snap["measured"] == 1
        assert snap["pending"] == 0
        assert store.pending_for("BTCUSDT") == []

    def test_olgunlasmis_stop_cozulur(self):
        kur(horizons_h=(1.0,))
        kaydet(price=100.0, stop_price=99.0, tp1_price=102.0)
        cikti = store.resolve_symbol(
            "BTCUSDT",
            [mum(BASE + 300, high=100.5, low=98.0, close=98.5)],
            BASE + 2 * HOUR,
        )
        assert len(cikti) == 1
        assert cikti[0]["sim"]["outcome"] == "stop"
        assert cikti[0]["measured"] is True

    def test_bos_mum_penceresi_olculemedi_der_ve_sayi_uydurmaz(self):
        kur(horizons_h=(1.0,))
        kaydet(price=100.0, stop_price=99.0, tp1_price=102.0)
        cikti = store.resolve_symbol("BTCUSDT", [], BASE + 2 * HOUR)
        assert len(cikti) == 1
        satir = cikti[0]
        assert satir["measured"] is False
        assert satir["sim"]["outcome"] == "no_data"
        # UYDURMA SAYI YOK: sayısal alanların hepsi None.
        assert satir["sim"]["exit_price"] is None
        assert satir["sim"]["price_move_pct"] is None
        assert satir["pnl_roi_pct"] is None
        assert all(h["price"] is None for h in satir["horizons"])
        assert all(h["roi_pct"] is None for h in satir["horizons"])
        snap = store.counters_snapshot()
        assert snap["resolved"] == 1 and snap["measured"] == 0
        assert snap["pending"] == 0

    def test_max_age_asilinca_kayit_dusurulur(self):
        # Ufuk 8 saat, yaş tavanı 1 saat: kayıt ASLA olgunlaşamadan düşer.
        kur(horizons_h=(8.0,), max_age_h=1.0)
        kaydet()
        cikti = store.resolve_symbol("BTCUSDT", [], BASE + 2 * HOUR)
        assert cikti == []
        snap = store.counters_snapshot()
        assert snap["expired"] == 1 and snap["pending"] == 0
        assert snap["resolved"] == 0
        assert store.pending_for("BTCUSDT") == []

    def test_max_age_asilmamis_kayit_kuyrukta_kalir(self):
        kur(horizons_h=(8.0,), max_age_h=48.0)
        kaydet()
        assert store.resolve_symbol("BTCUSDT", [], BASE + 2 * HOUR) == []
        snap = store.counters_snapshot()
        assert snap["expired"] == 0 and snap["pending"] == 1

    def test_kapali_defter_kuyruga_dokunmaz(self):
        kur(horizons_h=(1.0,))
        kaydet()
        kur(enabled=False, horizons_h=(1.0,))
        cikti = store.resolve_symbol(
            "BTCUSDT", [mum(BASE + 300, high=103, low=100, close=101)],
            BASE + 2 * HOUR,
        )
        assert cikti == []
        snap = store.counters_snapshot()
        assert snap["pending"] == 1 and snap["resolved"] == 0
        assert len(store.pending_for("BTCUSDT")) == 1

    def test_baska_sembolun_mumlari_kaydi_cozmez(self):
        """Kova ayrımı: ETH mumları BTC kaydını ölçemez."""
        kur(horizons_h=(1.0,))
        kaydet(symbol="BTCUSDT")
        cikti = store.resolve_symbol(
            "ETHUSDT", [mum(BASE + 300, high=103, low=100, close=101)],
            BASE + 2 * HOUR,
        )
        assert cikti == []
        assert store.counters_snapshot()["pending"] == 1
        assert len(store.pending_for("BTCUSDT")) == 1

    def test_recent_en_yeni_once_dondurur(self):
        kur(horizons_h=(1.0,), dedup_sec=0.0)
        kaydet(at_epoch=BASE, symbol="BTCUSDT")
        kaydet(at_epoch=BASE + 60.0, symbol="BTCUSDT")
        store.resolve_symbol(
            "BTCUSDT", [mum(BASE + 300, high=103, low=100, close=101)],
            BASE + 2 * HOUR,
        )
        son = store.recent(limit=5)
        assert len(son) == 2
        # En yeni önce: ikinci kayıt (BASE+60) başta.
        assert son[0]["at_epoch"] == BASE + 60.0
        assert store.recent(limit=1) == son[:1]

    def test_summary_surec_ici_halkadan_okur(self):
        kur(horizons_h=(1.0,))
        kaydet(reason=intent.REASON_TV_CONFLUENCE)
        store.resolve_symbol(
            "BTCUSDT", [mum(BASE + 300, high=103, low=100, close=102.5)],
            BASE + 2 * HOUR,
        )
        ozet = store.summary()
        assert ozet["total"] == 1
        assert {row["reason"] for row in ozet["by_reason"]} == {
            intent.REASON_TV_CONFLUENCE
        }
        assert ozet["overall"]["n"] == 1


# --------------------------------------------------------------------------
# 4. _fill_plan — planı OLMAYAN niyet (TV sağlaması yolu)
# --------------------------------------------------------------------------

class TestFillPlan:
    """`/tv-signal` reddinde `ScalpSignal` YOKTUR: plan ROI politikasından kurulur."""

    def _kur_politika(self, **kwargs: Any) -> None:
        params: Dict[str, Any] = {
            "horizons_h": (1.0,),
            "tp1_roi_pct": 20.0,
            "stop_roi_pct": 50.0,
            "policy_leverage": 20,
        }
        params.update(kwargs)
        kur(**params)

    def _plansiz(self, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return kaydet(
            price=None, stop_price=None, tp1_price=None, leverage=None,
            reason=intent.REASON_TV_CONFLUENCE, **kwargs
        )

    def test_cok_gec_ilk_mum_referans_giris_SAYILMAZ(self):
        """Gecikme kelepçesi: saatler sonraki bir mum "giriş fiyatı" değildir.

        Mum penceresi ~12.5 saatliktir. Kayıt bundan eskiyse (sembol bir süre
        tarama evreninden çıkmış olabilir) bulunan "ilk mum" niyet anından
        saatlerce sonra olabilir; onun açılışını referans giriş saymak
        UYDURMA olurdu. Böyle bir kayıt plansız kalır ve `measured=False`
        ile kapanır.
        """
        self._kur_politika()
        self._plansiz()
        gec = store.PLAN_REF_MAX_LAG_SEC + 60.0
        cikti = store.resolve_symbol(
            "BTCUSDT",
            [mum(BASE + gec, high=100.4, low=99.8, close=100.2, acilis=100.0)],
            BASE + 2 * HOUR,
        )
        assert len(cikti) == 1
        assert cikti[0]["price"] is None
        assert cikti[0]["measured"] is False
        assert cikti[0]["pnl_roi_pct"] is None
        # İşaret de YAZILMAZ: plan hiç kurulmadı.
        assert "plan_source" not in (cikti[0].get("extra") or {})

    def test_kelepce_sinirindaki_mum_KABUL_edilir(self):
        """Sınır dahildir: tam `PLAN_REF_MAX_LAG_SEC` gecikme hâlâ geçerli."""
        self._kur_politika()
        self._plansiz()
        cikti = store.resolve_symbol(
            "BTCUSDT",
            [
                mum(
                    BASE + store.PLAN_REF_MAX_LAG_SEC,
                    high=100.4, low=99.8, close=100.2, acilis=100.0,
                )
            ],
            BASE + 2 * HOUR,
        )
        assert cikti[0]["price"] == pytest.approx(100.0)
        assert cikti[0]["extra"]["plan_source"] == "roi_policy"

    def test_referans_giris_niyetten_SONRAKI_ilk_mumun_acilisidir(self):
        self._kur_politika()
        self._plansiz()
        cikti = store.resolve_symbol(
            "BTCUSDT",
            [
                mum(BASE + 300, high=100.4, low=99.8, close=100.2, acilis=100.0),
                mum(BASE + 600, high=100.6, low=99.9, close=100.3, acilis=100.2),
            ],
            BASE + 2 * HOUR,
        )
        assert len(cikti) == 1
        assert cikti[0]["price"] == pytest.approx(100.0)

    def test_plan_source_bekleyen_kayda_islenir(self):
        """`_fill_plan` OLGUNLAŞMADAN da çalışır ve yaklaşıklığı işaretler."""
        self._kur_politika(horizons_h=(8.0,))
        self._plansiz()
        # Henüz olgunlaşmadı (8 saatlik ufuk): kayıt kuyrukta KALIR ama plan
        # zaten takılmıştır — referans giriş, ilk mum hâlâ elimizdeyken alınır.
        assert store.resolve_symbol(
            "BTCUSDT",
            [mum(BASE + 300, high=100.4, low=99.8, close=100.2, acilis=100.0)],
            BASE + HOUR,
        ) == []
        bekleyen = store.pending_for("BTCUSDT")[0]
        assert bekleyen["price"] == pytest.approx(100.0)
        assert bekleyen["extra"]["plan_source"] == "roi_policy"
        assert bekleyen["extra"]["plan_ref_epoch"] == pytest.approx(BASE + 300)

    def test_plan_source_cozulen_satira_TASINIR(self):
        """`extra.plan_source` ÇÖZÜLEN satırda da durur (dürüstlük etiketi).

        `_fill_plan` bekleyen kayda `extra.plan_source="roi_policy"` yazar.
        Bu işaret JSONL'e ve `/scalper/counterfactual` çıktısına ULAŞMALIDIR:
        aksi hâlde ROI politikasıyla YAKLAŞIK kurulmuş planlar (TV sağlaması
        yolu — asıl soruyu taşıyan küme) ile sinyalden gelen GERÇEK planlar
        rapor tarafında ayrılamazdı. `counterfactual.resolve` bu yüzden
        `extra`yı aynen taşır.
        """
        self._kur_politika()
        self._plansiz()
        satir = store.resolve_symbol(
            "BTCUSDT",
            [mum(BASE + 300, high=100.4, low=99.8, close=100.2, acilis=100.0)],
            BASE + 2 * HOUR,
        )[0]
        assert satir["price"] == pytest.approx(100.0)   # plan TAKILDI
        assert satir["extra"]["plan_source"] == "roi_policy"
        assert satir["extra"]["plan_ref_epoch"] == pytest.approx(BASE + 300)

    def test_plansiz_olmayan_satirda_extra_bos_kalir(self):
        """Plan sinyalden geldiyse `roi_policy` işareti YAZILMAZ."""
        self._kur_politika()
        store.register(
            at="t", at_epoch=BASE, symbol="BTCUSDT", direction="LONG",
            reason="regime_gate", price=100.0, stop_price=97.5,
            tp1_price=101.0, leverage=20, plan_source="signal",
        )
        satir = store.resolve_symbol(
            "BTCUSDT",
            [mum(BASE + 300, high=100.4, low=99.8, close=100.2, acilis=100.0)],
            BASE + 2 * HOUR,
        )[0]
        assert satir["extra"]["plan_source"] == "signal"

    def test_niyet_ONCESI_mum_sonucu_DEGISTIRMEZ(self):
        """Look-ahead yok: niyetten önce açılmış mum plana da simülasyona da girmez."""
        sonra = [
            mum(BASE + 300, high=100.4, low=99.8, close=100.2, acilis=100.0),
            mum(BASE + 600, high=100.6, low=99.9, close=100.3, acilis=100.2),
        ]
        once = mum(BASE - 300, high=500.0, low=1.0, close=400.0, acilis=400.0)

        self._kur_politika()
        self._plansiz()
        temiz = store.resolve_symbol("BTCUSDT", sonra, BASE + 2 * HOUR)

        store.reset()
        self._kur_politika()
        self._plansiz()
        kirli = store.resolve_symbol("BTCUSDT", [once] + sonra, BASE + 2 * HOUR)

        assert len(temiz) == 1 and len(kirli) == 1
        for alan in ("price", "stop_price", "tp1_price", "leverage", "pnl_roi_pct"):
            assert temiz[0][alan] == kirli[0][alan]
        assert temiz[0]["sim"]["outcome"] == kirli[0]["sim"]["outcome"]

    def test_long_planinda_tp1_giris_stop_sirasi_dogrudur(self):
        self._kur_politika()
        self._plansiz(direction="LONG")
        satir = store.resolve_symbol(
            "BTCUSDT",
            [mum(BASE + 300, high=100.4, low=99.8, close=100.2, acilis=100.0)],
            BASE + 2 * HOUR,
        )[0]
        # 20x'te +%20 ROI = fiyatın lehte %1'i; -%50 ROI = %2.5 aleyhte.
        assert satir["price"] == pytest.approx(100.0)
        assert satir["tp1_price"] == pytest.approx(101.0)
        assert satir["stop_price"] == pytest.approx(97.5)
        assert satir["tp1_price"] > satir["price"] > satir["stop_price"]
        assert satir["leverage"] == 20

    def test_short_planinda_sira_terstir(self):
        self._kur_politika()
        self._plansiz(direction="SHORT")
        satir = store.resolve_symbol(
            "BTCUSDT",
            [mum(BASE + 300, high=100.4, low=99.8, close=100.2, acilis=100.0)],
            BASE + 2 * HOUR,
        )[0]
        assert satir["price"] == pytest.approx(100.0)
        assert satir["tp1_price"] == pytest.approx(99.0)
        assert satir["stop_price"] == pytest.approx(102.5)
        assert satir["stop_price"] > satir["price"] > satir["tp1_price"]

    def test_tp1_roi_yoksa_plan_UYDURULMAZ(self):
        self._kur_politika(tp1_roi_pct=0.0)
        self._plansiz()
        satir = store.resolve_symbol(
            "BTCUSDT",
            [mum(BASE + 300, high=100.4, low=99.8, close=100.2, acilis=100.0)],
            BASE + 2 * HOUR,
        )[0]
        assert satir["price"] is None
        assert satir["stop_price"] is None and satir["tp1_price"] is None
        assert satir["measured"] is False
        assert satir["sim"]["outcome"] == "no_data"

    def test_kaldirac_yoksa_plan_UYDURULMAZ(self):
        self._kur_politika(policy_leverage=0)
        self._plansiz()
        satir = store.resolve_symbol(
            "BTCUSDT",
            [mum(BASE + 300, high=100.4, low=99.8, close=100.2, acilis=100.0)],
            BASE + 2 * HOUR,
        )[0]
        assert satir["price"] is None and satir["measured"] is False

    def test_mum_yoksa_plan_takilmaz(self):
        self._kur_politika()
        self._plansiz()
        satir = store.resolve_symbol("BTCUSDT", [], BASE + 2 * HOUR)[0]
        assert satir["price"] is None and satir["measured"] is False

    def test_niyet_ONCESI_mum_tek_basina_plan_kurmaz(self):
        """Look-ahead yok: yalnız niyetten ÖNCEKİ mumlar varsa plan TAKILMAZ."""
        self._kur_politika(horizons_h=(8.0,))
        self._plansiz()
        assert store.resolve_symbol(
            "BTCUSDT",
            [mum(BASE - 300, high=500.0, low=1.0, close=400.0, acilis=400.0)],
            BASE + HOUR,
        ) == []
        bekleyen = store.pending_for("BTCUSDT")[0]
        assert bekleyen["price"] is None
        assert bekleyen["extra"] == {}


# --------------------------------------------------------------------------
# 5. JSONL kalıcılığı (forensics_log.read_events)
# --------------------------------------------------------------------------

class TestJsonlPersistence:
    def test_ARSIVLER_de_okunur_ve_ESKI_ONCE_gelir(self, tmp_path):
        """30 günlük tarihçe arşiv dosyalarındadır — okuma onları ATLAMAMALI.

        `forensics_log` günlük rotasyon yapar (`trades-<GG>.jsonl`); yalnız
        güncel dosyayı okumak, bir haftalık pencerede son günü GÖSTERİP
        öncesini sessizce düşürürdü.
        """
        jsonl_yaz(
            tmp_path,
            [{"ts": "2026-08-20T10:00:00+00:00", "event": "counterfactual",
              "symbol": "OLDUSDT", "reason": "regime_gate", "measured": False}],
            ad="trades-2026-08-20.jsonl",
        )
        jsonl_yaz(
            tmp_path,
            [{"ts": "2026-08-24T10:00:00+00:00", "event": "counterfactual",
              "symbol": "NEWUSDT", "reason": "regime_gate", "measured": False}],
        )
        satirlar = forensics_log.read_events("counterfactual")
        assert [row["symbol"] for row in satirlar] == ["OLDUSDT", "NEWUSDT"]
        # `since_iso` arşiv satırını da eler.
        taze = forensics_log.read_events(
            "counterfactual", since_iso="2026-08-22T00:00:00+00:00"
        )
        assert [row["symbol"] for row in taze] == ["NEWUSDT"]

    def test_okuma_limiti_satir_sayisini_kirpar(self, tmp_path):
        """Sınırsız okuma bir HTTP isteğini dakikalara uzatabilir."""
        jsonl_yaz(
            tmp_path,
            [{"ts": "2026-08-24T10:00:00+00:00", "event": "counterfactual",
              "symbol": f"S{i}USDT", "reason": "capacity"} for i in range(10)],
        )
        assert len(forensics_log.read_events("counterfactual", limit=4)) == 4

    def test_cozulen_satir_jsonl_e_yazilir(self, tmp_path):
        kur(horizons_h=(1.0,))
        kaydet(reason=intent.REASON_TV_CONFLUENCE)
        store.resolve_symbol(
            "BTCUSDT", [mum(BASE + 300, high=103, low=100, close=102.5)],
            BASE + 2 * HOUR,
        )
        assert forensics_log.drain(5.0) is True
        satirlar = forensics_log.read_events("counterfactual")
        assert len(satirlar) == 1
        assert satirlar[0]["symbol"] == "BTCUSDT"
        assert satirlar[0]["reason"] == intent.REASON_TV_CONFLUENCE
        assert satirlar[0]["event"] == "counterfactual"
        assert store.counters_snapshot()["logged"] == 1

    def test_since_iso_daha_eski_satiri_eler(self, tmp_path):
        jsonl_yaz(tmp_path, [
            {"ts": "2026-08-23T12:00:00.000+00:00", "event": "counterfactual",
             "symbol": "OLD"},
            {"ts": "2026-08-24T12:00:00.000+00:00", "event": "counterfactual",
             "symbol": "NEW"},
        ])
        hepsi = forensics_log.read_events("counterfactual")
        assert [r["symbol"] for r in hepsi] == ["OLD", "NEW"]
        suzulmus = forensics_log.read_events(
            "counterfactual", since_iso="2026-08-24T00:00:00+00:00"
        )
        assert [r["symbol"] for r in suzulmus] == ["NEW"]

    def test_bozuk_satir_okumayi_dusurmez(self, tmp_path):
        jsonl_yaz(tmp_path, [
            {"ts": "2026-08-24T10:00:00.000+00:00", "event": "counterfactual",
             "symbol": "A"},
            "{bu JSON degil",
            "",
            "12345",  # geçerli JSON ama sözlük DEĞİL
            {"ts": "2026-08-24T11:00:00.000+00:00", "event": "counterfactual",
             "symbol": "B"},
        ])
        satirlar = forensics_log.read_events("counterfactual")
        assert [r["symbol"] for r in satirlar] == ["A", "B"]

    def test_baska_olay_turleri_suzulur(self, tmp_path):
        jsonl_yaz(tmp_path, [
            {"ts": "2026-08-24T10:00:00.000+00:00", "event": "intent",
             "symbol": "I"},
            {"ts": "2026-08-24T10:01:00.000+00:00", "event": "exit",
             "symbol": "E"},
            {"ts": "2026-08-24T10:02:00.000+00:00", "event": "counterfactual",
             "symbol": "C"},
        ])
        assert [r["symbol"] for r in forensics_log.read_events("counterfactual")] == ["C"]
        assert [r["symbol"] for r in forensics_log.read_events("intent")] == ["I"]

    def test_dosya_yoksa_bos_liste(self, tmp_path):
        assert forensics_log.read_events("counterfactual") == []


# --------------------------------------------------------------------------
# 6. engine kancaları
# --------------------------------------------------------------------------

def _cfg(**kwargs: Any) -> SimpleNamespace:
    params: Dict[str, Any] = {
        "scalper_forensics_enabled": True,
        "scalper_leverage": 20,
        "scalper_tp1_roi": 20.0,
        "scalper_stop_mode": "fixed_roi",
        "scalper_fixed_stop_roi_pct": 50.0,
        "scalper_dynamic_leverage": False,
        "scalper_stop_atr_floor_mult": 0.0,
    }
    params.update(kwargs)
    return SimpleNamespace(**params)


def _engine(**attrs: Any):
    """`ScalperEngine`i __init__ çalıştırmadan kur (repo konvansiyonu)."""
    from src.core.logger import app_logger
    from src.strategies.scalper.engine import ScalperEngine

    engine = ScalperEngine.__new__(ScalperEngine)
    engine.cfg = _cfg()
    engine.logger = app_logger
    engine._forensics_error_logged = False
    for key, value in attrs.items():
        setattr(engine, key, value)
    return engine


def _sinyal(
    direction: Direction = Direction.LONG,
    entry: float = 100.0,
    stop: float = 95.0,
) -> ScalpSignal:
    return ScalpSignal(
        strategy="C",
        symbol="BTCUSDT",
        direction=direction,
        entry_price=entry,
        stop_price=stop,
        reason="test sinyali",
        regime=Regime.RANGE,
        atr_5m=1.0,
    )


class TestEngineHooks:
    def test_sinyalsiz_plan_dort_none_dondurur(self):
        engine = _engine()
        assert engine._counterfactual_plan(None) == (None, None, None, None)

    def test_long_plani_tp1_giris_stop_sirasini_korur(self):
        engine = _engine()
        entry, stop, tp1, lev = engine._counterfactual_plan(_sinyal(Direction.LONG))
        assert entry == pytest.approx(100.0)
        assert stop == pytest.approx(97.5)   # -%50 ROI @20x = %2.5 fiyat
        assert tp1 == pytest.approx(101.0)   # +%20 ROI @20x = %1 fiyat
        assert lev == 20
        assert tp1 > entry > stop

    def test_short_planinda_sira_terstir(self):
        engine = _engine()
        entry, stop, tp1, lev = engine._counterfactual_plan(_sinyal(Direction.SHORT))
        assert stop > entry > tp1
        assert stop == pytest.approx(102.5) and tp1 == pytest.approx(99.0)
        assert lev == 20

    def test_orijinal_sinyal_DEGISMEZ(self):
        """`apply_stop_policy` SAFtır: motorun kendi sinyali dokunulmaz kalır."""
        engine = _engine()
        sig = _sinyal(Direction.LONG, stop=95.0)
        engine._counterfactual_plan(sig)
        assert sig.stop_price == pytest.approx(95.0)
        assert sig.entry_price == pytest.approx(100.0)
        assert sig.leverage is None

    def test_plan_hesabi_patlarsa_dort_none_doner(self):
        engine = _engine()
        engine.cfg = SimpleNamespace()  # hiçbir ayar yok
        bozuk = SimpleNamespace(entry_price="hayır")
        assert engine._counterfactual_plan(bozuk) == (None, None, None, None)

    def test_deny_defteri_acar(self):
        engine = _engine()
        engine._record_intent(
            symbol="BTCUSDT",
            direction=Direction.LONG,
            stage=intent.STAGE_DECIDED,
            decision=intent.DECISION_DENY,
            reason=intent.REASON_REGIME_GATE,
            signal=_sinyal(),
        )
        snap = store.counters_snapshot()
        assert snap["registered"] == 1 and snap["pending"] == 1
        satir = store.pending_for("BTCUSDT")[0]
        assert satir["reason"] == intent.REASON_REGIME_GATE
        assert satir["price"] == pytest.approx(100.0)
        assert satir["tp1_price"] == pytest.approx(101.0)
        assert satir["extra"]["plan_source"] == "signal"

    def test_error_de_defteri_acar(self):
        engine = _engine()
        engine._record_intent(
            symbol="BTCUSDT",
            direction=Direction.LONG,
            stage=intent.STAGE_EXECUTED,
            decision=intent.DECISION_ERROR,
            reason=intent.REASON_ORDER_ERROR,
            signal=_sinyal(),
        )
        assert store.counters_snapshot()["registered"] == 1

    def test_allow_defteri_ACMAZ(self):
        engine = _engine()
        engine._record_intent(
            symbol="BTCUSDT",
            direction=Direction.LONG,
            stage=intent.STAGE_EXECUTED,
            decision=intent.DECISION_ALLOW,
            reason=intent.REASON_OPENED,
            signal=_sinyal(),
        )
        snap = store.counters_snapshot()
        assert snap["registered"] == 0 and snap["pending"] == 0

    def test_adli_kayit_kapaliyken_hicbir_sey_olmaz(self, monkeypatch):
        cagrilar: List[Dict[str, Any]] = []
        monkeypatch.setattr(intent, "record", lambda **kw: cagrilar.append(kw))
        engine = _engine()
        engine.cfg = _cfg(scalper_forensics_enabled=False)
        engine._record_intent(
            symbol="BTCUSDT",
            direction=Direction.LONG,
            stage=intent.STAGE_DECIDED,
            decision=intent.DECISION_DENY,
            reason=intent.REASON_CAPACITY,
            signal=_sinyal(),
        )
        assert cagrilar == []
        snap = store.counters_snapshot()
        assert snap["registered"] == 0 and snap["pending"] == 0

    def test_register_patlarsa_motor_patlamaz(self, monkeypatch):
        def _patla(**kwargs):
            raise RuntimeError("defter bozuk")

        monkeypatch.setattr(store, "register", _patla)
        engine = _engine()
        # Bir teşhis kaydı bir REDDİ ASLA değiştirmemeli.
        engine._record_intent(
            symbol="BTCUSDT",
            direction=Direction.LONG,
            stage=intent.STAGE_DECIDED,
            decision=intent.DECISION_DENY,
            reason=intent.REASON_CAPACITY,
            signal=_sinyal(),
        )

    def test_resolve_mumsuz_ctx_te_sessizce_doner(self):
        engine = _engine()
        kaydet()
        engine._counterfactual_resolve("BTCUSDT", SimpleNamespace())
        engine._counterfactual_resolve(
            "BTCUSDT", SimpleNamespace(candles_5m=[], candles_15m=[])
        )
        assert store.counters_snapshot()["pending"] == 1

    def test_resolve_istisnasi_motora_SIZMAZ(self, monkeypatch):
        def _patla(*args, **kwargs):
            raise RuntimeError("mum yok")

        monkeypatch.setattr(store, "resolve_symbol", _patla)
        engine = _engine()
        ctx = SimpleNamespace(
            candles_5m=[mum(BASE + 300, high=103, low=100, close=101)]
        )
        engine._counterfactual_resolve("BTCUSDT", ctx)

    def test_resolve_ctx_mumlariyla_cozer(self):
        kur(horizons_h=(1.0,))
        kaydet(at_epoch=BASE, price=100.0, stop_price=99.0, tp1_price=102.0)
        engine = _engine()
        ctx = SimpleNamespace(
            candles_5m=[mum(BASE + 300, high=103.0, low=100.0, close=102.5)]
        )
        # `now_epoch` motorda `time.time()`tır; kayıt 2023'te açıldığı için
        # bugünkü saat DAİMA olgunlaşmayı geçer (deterministik).
        engine._counterfactual_resolve("BTCUSDT", ctx)
        snap = store.counters_snapshot()
        assert snap["resolved"] == 1 and snap["pending"] == 0

    def test_snapshot_sozluk_dondurur(self):
        engine = _engine()
        snap = engine._counterfactual_snapshot()
        assert isinstance(snap, dict)
        assert {"enabled", "pending", "registered", "resolved"} <= set(snap)

    def test_snapshot_arizada_bile_sozluk_dondurur(self, monkeypatch):
        def _patla():
            raise RuntimeError("sayaç yok")

        monkeypatch.setattr(store, "counters_snapshot", _patla)
        engine = _engine()
        snap = engine._counterfactual_snapshot()
        assert isinstance(snap, dict) and "error" in snap


# --------------------------------------------------------------------------
# 7. intent şeması — D27/B'nin KALICI izi
# --------------------------------------------------------------------------

class TestIntentSchema:
    def test_dort_yeni_alan_tasinir(self):
        row = intent.build_intent(
            at="2026-08-24T00:00:00+00:00",
            symbol="BTCUSDT",
            direction=Direction.LONG,
            stage=intent.STAGE_DECIDED,
            decision=intent.DECISION_DENY,
            reason=intent.REASON_REGIME_GATE,
            price=100.0,
            stop_price=97.5,
            tp1_price=101.0,
            leverage=20,
        )
        assert row["price"] == pytest.approx(100.0)
        assert row["stop_price"] == pytest.approx(97.5)
        assert row["tp1_price"] == pytest.approx(101.0)
        assert row["leverage"] == 20

    def test_verilmeyen_alanlar_none_kalir(self):
        row = intent.build_intent(
            at="t", symbol="X", direction="LONG",
            stage=intent.STAGE_PROPOSED, decision=intent.DECISION_ALLOW,
        )
        assert row["price"] is None
        assert row["stop_price"] is None
        assert row["tp1_price"] is None
        assert row["leverage"] is None

    def test_bozuk_sayilar_none_a_duser(self):
        row = intent.build_intent(
            at="t", symbol="X", direction="LONG",
            stage=intent.STAGE_DECIDED, decision=intent.DECISION_DENY,
            price="abc",
            stop_price=float("inf"),
            tp1_price=float("nan"),
            leverage=True,   # bool bir kaldıraç DEĞİLDİR
        )
        assert row["price"] is None
        assert row["stop_price"] is None
        assert row["tp1_price"] is None
        assert row["leverage"] is None

    def test_ondalikli_kaldirac_tam_sayiya_iner(self):
        row = intent.build_intent(
            at="t", symbol="X", direction="LONG",
            stage=intent.STAGE_DECIDED, decision=intent.DECISION_DENY,
            leverage=20.0,
        )
        assert row["leverage"] == 20 and isinstance(row["leverage"], int)


# --------------------------------------------------------------------------
# 8. scripts/ledger_report.py --counterfactual
# --------------------------------------------------------------------------

_CF_SINCE = datetime(2026, 8, 24, 0, 0, 0)
_CF_UNTIL = datetime(2026, 8, 24, 23, 59, 59)


def _cf_satir(
    *,
    reason: str = intent.REASON_TV_CONFLUENCE,
    ts: str = "2026-08-24T12:00:00.000+00:00",
    outcome: str = "tp1",
    roi: Optional[float] = 20.0,
    measured: bool = True,
    dup: int = 1,
) -> Dict[str, Any]:
    return {
        "ts": ts,
        "event": "counterfactual",
        "at": ts,
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "reason": reason,
        "dup_count": dup,
        "measured": measured,
        "pnl_roi_pct": roi,
        "sim": {"outcome": outcome, "model": cf.MODEL_ID},
    }


class TestLedgerReportCounterfactual:
    def test_bos_defter_aciklayici_not_doner(self, tmp_path):
        rows, notes = lr.load_counterfactual_rows(
            _CF_SINCE, _CF_UNTIL, log_dir=str(tmp_path)
        )
        assert rows == []
        assert notes and "Karşı-olgu" in notes[0]

    def test_satirlar_jsonl_den_okunur(self, tmp_path):
        jsonl_yaz(tmp_path, [
            _cf_satir(ts="2026-08-23T12:00:00.000+00:00"),   # pencereden ÖNCE
            _cf_satir(ts="2026-08-24T12:00:00.000+00:00"),
            {"ts": "2026-08-24T12:30:00.000+00:00", "event": "intent"},
        ])
        rows, notes = lr.load_counterfactual_rows(
            _CF_SINCE, _CF_UNTIL, log_dir=str(tmp_path)
        )
        assert len(rows) == 1
        assert rows[0]["ts"] == "2026-08-24T12:00:00.000+00:00"
        assert notes == []

    def test_pencere_sonrasi_satir_elenir(self, tmp_path):
        jsonl_yaz(tmp_path, [_cf_satir(ts="2026-08-25T01:00:00.000+00:00")])
        rows, notes = lr.load_counterfactual_rows(
            _CF_SINCE, _CF_UNTIL, log_dir=str(tmp_path)
        )
        assert rows == [] and notes

    def test_rapor_by_reason_overall_ve_not_tasir(self):
        section = lr.build_counterfactual_report([
            _cf_satir(roi=20.0, outcome="tp1"),
            _cf_satir(roi=-50.0, outcome="stop"),
        ])
        assert "by_reason" in section and "overall" in section
        assert section["note"] == lr._COUNTERFACTUAL_NOTE
        assert section["total"] == 2 and section["measured"] == 2
        satir = section["by_reason"][0]
        assert satir["reason"] == intent.REASON_TV_CONFLUENCE
        assert satir["tp1"] == 1 and satir["stop"] == 1

    def test_bos_girdi_cokmez(self):
        section = lr.build_counterfactual_report([])
        assert section["total"] == 0
        assert section["by_reason"] == []
        assert section["note"]

    def _rapor(self, section: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {}
        if section is not None:
            kwargs["counterfactual"] = section
        return lr.build_report(
            [], {}, _CF_SINCE, _CF_UNTIL, ["2026-08-24"], [], **kwargs
        )

    def test_bayrak_verilmezse_anahtar_HIC_YOK(self):
        """Mevcut JSON tüketicileri bozulmamalı: bölüm istenmediyse anahtar yok."""
        report = self._rapor()
        assert "counterfactual" not in report
        assert "KARŞI-OLGU" not in lr.render_text(report)
        assert "5d)" not in lr.render_md(report)

    def test_bayrak_verilince_anahtar_ve_bolum_var(self):
        section = lr.build_counterfactual_report([
            _cf_satir(roi=20.0), _cf_satir(roi=-50.0, outcome="stop"),
        ])
        report = self._rapor(section)
        assert report["counterfactual"] is section
        text = lr.render_text(report)
        assert "5d) KARŞI-OLGU DEFTERİ — REDDEDİLEN NİYETLER (D27/B)" in text
        assert "RetGerekçesi" in text
        assert "MODELLENMEZ" in text  # NOT metni tabloyla birlikte basılır
        md = lr.render_md(report)
        assert "## 5d) Karşı-olgu defteri — reddedilen niyetler (D27/B)" in md
        assert "| RetGerekçesi | n |" in md

    def test_profit_factor_none_satiri_0_basmaz(self):
        """PF hesaplanamayan satır 0.0 DEĞİL — biçim çökmemeli.

        D27 incelemesi (D4): `profit_factor is None` iki ZIT anlama
        gelebiliyordu. Tek pozitif ROI'li bu satırda payda 0'dır ("kayıp
        yok"), "hiç ölçüm yok" DEĞİL — rapor ikisini artık ayırt eder.
        """
        section = lr.build_counterfactual_report([_cf_satir(roi=20.0)])
        satir_sozluk = section["by_reason"][0]
        assert satir_sozluk["profit_factor"] is None
        assert satir_sozluk["profit_factor_note"] == "no_loss"
        text = lr.render_text(self._rapor(section))
        satir = next(
            line for line in text.splitlines()
            if line.startswith(intent.REASON_TV_CONFLUENCE)
        )
        assert "∞(kayıpsız)" in satir
        # n<2 olduğu için %95 GA yoktur — o '—' basılır.
        assert "—" in satir

    def test_olculemeyen_satir_ortalamaya_girmez(self):
        section = lr.build_counterfactual_report([
            _cf_satir(roi=20.0),
            _cf_satir(roi=None, outcome="no_data", measured=False),
        ])
        overall = section["overall"]
        assert overall["n"] == 2 and overall["measured"] == 1
        assert overall["no_data"] == 1
        assert overall["avg_roi_pct"] == pytest.approx(20.0)

    def test_cli_bayragi_varsayilan_kapali(self):
        assert lr.parse_args([]).counterfactual is False
        assert lr.parse_args(["--counterfactual"]).counterfactual is True


# --------------------------------------------------------------------------
# 9. HTTP yüzeyi
# --------------------------------------------------------------------------

class TestApiSurface:
    async def test_uc_bos_defterde_bile_sekil_dondurur(self, tmp_path):
        import src.main as main_module

        payload = await main_module.scalper_counterfactual(since=None)
        assert {"since", "reason", "summary", "counters", "rows"} <= set(payload)
        assert payload["rows"] == []
        assert payload["summary"]["total"] == 0
        assert payload["counters"]["window"] == "process_start"

    async def test_uc_jsonl_satirlarini_ozetler(self, tmp_path):
        import src.main as main_module
        # `_log_dir` parametresi test izolasyonu için: scalper_counterfactual
        # gerçek log dizini yerine tmp_path'i okur (env kalıcı değişmez).

        jsonl_yaz(tmp_path, [
            _cf_satir(roi=20.0, outcome="tp1"),
            _cf_satir(
                reason=intent.REASON_REGIME_GATE, roi=-50.0, outcome="stop",
                ts="2026-08-24T13:00:00.000+00:00",
            ),
        ])
        payload = await main_module.scalper_counterfactual(
            since=_CF_SINCE.isoformat(), _log_dir=str(tmp_path)
        )
        assert payload["summary"]["total"] == 2
        assert len(payload["rows"]) == 2
        # En yeni önce.
        assert payload["rows"][0]["reason"] == intent.REASON_REGIME_GATE

    async def test_uc_gerekce_suzgeci(self, tmp_path):
        import src.main as main_module

        jsonl_yaz(tmp_path, [
            _cf_satir(reason=intent.REASON_TV_CONFLUENCE),
            _cf_satir(
                reason=intent.REASON_REGIME_GATE,
                ts="2026-08-24T13:00:00.000+00:00",
            ),
        ])
        payload = await main_module.scalper_counterfactual(
            since=_CF_SINCE.isoformat(), reason="regime_gate", _log_dir=str(tmp_path)
        )
        assert payload["reason"] == intent.REASON_REGIME_GATE
        assert len(payload["rows"]) == 1
        assert payload["summary"]["total"] == 1

    async def test_uc_limit_kirpar(self, tmp_path):
        import src.main as main_module

        jsonl_yaz(tmp_path, [_cf_satir() for _ in range(5)])
        payload = await main_module.scalper_counterfactual(
            since=_CF_SINCE.isoformat(), limit=2, _log_dir=str(tmp_path)
        )
        assert len(payload["rows"]) == 2
        # Özet TÜM satırları görür; `limit` yalnız ham satırları kırpar.
        assert payload["summary"]["total"] == 5

    def test_status_govdesinde_counterfactual_blogu_var(self):
        """Pano "alan yok" ile "ölçüm kapalı"yı karıştırmamalı (D27/B)."""
        from src.main import _EMPTY_SCALPER_STATUS

        blok = _EMPTY_SCALPER_STATUS["counterfactual"]
        assert isinstance(blok, dict)
        # D27 incelemesi-2 (bulgu 4): beklenen anahtar kümesi LİTERALDİR.
        # Eskiden `set(blok) == set(store.counters_snapshot())` yazıyordu ve
        # bu bir TAUTOLOJİYDİ — `main.py` zaten aynı çağrıyı yapıyordu, yani
        # `counters_snapshot()`tan bir anahtar SİLEN mutasyon 2483 testin
        # hepsini geçiyordu. Literal küme 13+1 anahtarın hepsini korur.
        beklenen = {
            "enabled", "window", "horizons_h", "dedup_sec", "max_pending",
            "pending", "registered", "dedup_hits", "dropped_full", "expired",
            "resolved", "measured", "logged", "log_dropped",
            "candle_buffer_symbols", "candle_buffer_bars",
        }
        assert set(blok) == beklenen
        assert set(store.counters_snapshot()) == beklenen
        assert blok["window"] == "process_start"
        # D27 incelemesi-2 (bulgu 6): blok IMPORT ANINDA donmamalı. Defter o
        # sırada AÇIK ve dolu olsa bile motorsuz gövde SIFIR demelidir.
        assert blok["enabled"] is False
        for sayac in (
            "pending", "registered", "dedup_hits", "dropped_full", "expired",
            "resolved", "measured", "logged", "log_dropped",
        ):
            assert blok[sayac] == 0, sayac

    def test_motorsuz_govde_defter_DOLUYKEN_de_sifir_der(self):
        """D27 incelemesi-2 (bulgu 6): import anında donmuş sözlük YALAN söylerdi."""
        import importlib

        import src.main as main_module

        kur(horizons_h=(1.0,), dedup_sec=0.0)
        for i in range(7):
            kaydet(at_epoch=BASE + i)
        assert store.counters_snapshot()["registered"] == 7

        yeniden = importlib.reload(main_module)
        try:
            blok = yeniden._EMPTY_SCALPER_STATUS["counterfactual"]
            assert blok["enabled"] is False
            assert blok["registered"] == 0
            assert blok["pending"] == 0
        finally:
            importlib.reload(main_module)
