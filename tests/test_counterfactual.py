"""D27 — karşı-olgu defterinin SAF çekirdeğinin testleri.

Kapsam:
  1. `build_pending` savunmalı normalizasyonu (bozuk fiyat, bozuk/tekrarlı
     ufuk, küçük harfli sembol/yön).
  2. `price_at` — tam denk gelen an, arada kalan an, mum yokluğu, SIRASIZ liste.
  3. `window` — look-ahead koruması (niyet anını İÇEREN yarım mumun dışlanması).
  4. `simulate` — LONG/SHORT stop ve tp1, aynı mumda beraberlikte STOP'un
     kazanması, hiçbiri vurmayınca `open`, boş mum, devre dışı bacak, bozuk giriş.
  5. Look-ahead DÜŞMANCA testi — `resolve` penceresine niyet öncesi bir mum
     sokulduğunda sonucun DEĞİŞMEMESİ.
  6. `resolve` — olgunlaşmama, olgunlaşma, veri yokluğu.
  7. `summarize` — PF, ortalama, %95 GA, `collapsed`, bilinmeyen gerekçe
     kovası, boş girdi.

Tüm testler DETERMİNİSTİKtir: sabit epoch'lar kullanılır, `datetime.now`
ÇAĞRILMAZ (modül zaten saat okumaz).
"""

from typing import Any, Dict, List, Optional

from src.strategies.scalper import counterfactual as cf
from src.strategies.scalper import intent
from src.strategies.scalper.types import Candle, Direction

#: Sabit referans an (saniye). Gerçek bir tarihe bağlı değildir; testlerin
#: bugünün tarihinden bağımsız kalması için seçilmiştir.
BASE = 1_700_000_000.0


def mum(
    baslangic_sn: float,
    *,
    high: float,
    low: float,
    close: float,
    acilis: Optional[float] = None,
    dakika: float = 5.0,
) -> Candle:
    """Test mumu kur. `Candle` zamanları MİLİSANİYEdir; burada saniyeden çevrilir.

    Kapanış zamanı `baslangic + dakika` ile TAM olarak eşittir; böylece
    "tam denk gelen an" testleri kesin sayılarla yazılabilir.
    """
    return Candle(
        open_time=int(baslangic_sn * 1000),
        open=close if acilis is None else acilis,
        high=high,
        low=low,
        close=close,
        volume=1.0,
        close_time=int((baslangic_sn + dakika * 60.0) * 1000),
    )


# --------------------------------------------------------------------------
# 1. build_pending
# --------------------------------------------------------------------------

class TestBuildPending:
    def test_normalizes_symbol_direction_and_horizons(self):
        row = cf.build_pending(
            at="2026-08-24T00:00:00+00:00",
            at_epoch=BASE,
            symbol="btcusdt",
            direction="long",
            reason=intent.REASON_REGIME_GATE,
            price=100.0,
            stop_price=99.0,
            tp1_price=102.0,
            leverage=10,
            strategy="C",
            source="scanner",
            horizons_h=[2, 1.0, 1, -3, 0, "x", None],
            intent_id="abc",
            extra={"note": "test"},
        )
        assert row["symbol"] == "BTCUSDT"
        assert row["direction"] == "LONG"
        # Pozitif + tekil + ARTAN sıralı.
        assert row["horizons_h"] == [1.0, 2.0]
        assert row["dup_count"] == 1
        assert row["model"] == cf.MODEL_ID
        assert row["version"] == cf.COUNTERFACTUAL_VERSION
        assert row["extra"] == {"note": "test"}
        assert row["at_epoch"] == BASE

    def test_direction_enum_is_accepted(self):
        row = cf.build_pending(
            at="t", at_epoch=BASE, symbol="ethusdt",
            direction=Direction.SHORT, reason="tv_confluence",
            price=10.0, stop_price=None, tp1_price=None, leverage=None,
            horizons_h=[1],
        )
        assert row["direction"] == "SHORT"
        assert row["leverage"] is None

    def test_broken_numbers_become_none(self):
        row = cf.build_pending(
            at="t", at_epoch=BASE, symbol="  ", direction=None,
            reason=None, price=None, stop_price="abc", tp1_price=float("nan"),
            leverage=0, horizons_h=[-1, 0], extra="sözlük değil",
        )
        assert row["price"] is None
        assert row["stop_price"] is None
        assert row["tp1_price"] is None
        # 0/negatif kaldıraç ROI'yi anlamsız kılar → None (1 UYDURULMAZ).
        assert row["leverage"] is None
        assert row["symbol"] is None
        assert row["direction"] is None
        assert row["reason"] is None
        assert row["horizons_h"] == []
        assert row["extra"] == {}


# --------------------------------------------------------------------------
# 2. price_at
# --------------------------------------------------------------------------

class TestPriceAt:
    def _mumlar(self) -> List[Candle]:
        return [
            mum(BASE, high=101.5, low=99.5, close=101.0),
            mum(BASE + 300, high=102.5, low=100.5, close=102.0),
        ]

    def test_exact_close_time_uses_that_candle(self):
        # Mum tam BASE+300'de KAPANIR → o an itibarıyla kapanmış son fiyat 101.
        assert cf.price_at(self._mumlar(), BASE + 300) == 101.0
        assert cf.price_at(self._mumlar(), BASE + 600) == 102.0

    def test_between_candles_uses_last_closed(self):
        # BASE+450 anında ikinci mum HENÜZ kapanmadı → 101 (look-ahead yok).
        assert cf.price_at(self._mumlar(), BASE + 450) == 101.0

    def test_no_candle_before_target_returns_none(self):
        assert cf.price_at(self._mumlar(), BASE + 299) is None
        assert cf.price_at([], BASE) is None
        assert cf.price_at(self._mumlar(), None) is None

    def test_unsorted_input_gives_same_answer(self):
        tersi = list(reversed(self._mumlar()))
        assert cf.price_at(tersi, BASE + 450) == 101.0
        assert cf.price_at(tersi, BASE + 600) == 102.0


# --------------------------------------------------------------------------
# 3. window — look-ahead koruması
# --------------------------------------------------------------------------

class TestWindow:
    def test_window_look_ahead_yok(self):
        """Niyet anını İÇEREN yarım mum pencereye GİRMEMELİDİR.

        Gerekçe: o mumun `high`/`low`'u niyet anından ÖNCEKİ fiyatları da
        kapsar. Pencereye girseydi simülasyon, niyet anında henüz olmamış
        (ya da niyetten ÖNCE olmuş) bir hareketi "girseydik başımıza
        gelirdi" diye sayardı — özellikle stop bacağı sistematik olarak
        yanlı ölçülürdü. Kural: mum TAMAMEN niyet anından sonra açılmalı.
        """
        yarim = mum(BASE - 120, high=999.0, low=1.0, close=100.0)  # niyeti İÇERİR
        sonraki = mum(BASE, high=101.0, low=99.0, close=100.5)
        tasan = mum(BASE + 300, high=101.0, low=99.0, close=100.6, dakika=120.0)

        secilen = cf.window([yarim, sonraki, tasan], BASE, BASE + 600)

        assert secilen == [sonraki]
        assert yarim not in secilen          # niyet anını içeren yarım mum
        assert tasan not in secilen          # ufkun DIŞINDA kapanan mum

    def test_window_is_time_ordered_and_defensive(self):
        birinci = mum(BASE, high=1.0, low=1.0, close=1.0)
        ikinci = mum(BASE + 300, high=1.0, low=1.0, close=1.0)
        assert cf.window([ikinci, birinci], BASE, BASE + 600) == [birinci, ikinci]
        assert cf.window([birinci], None, BASE + 600) == []
        assert cf.window(None, BASE, BASE + 600) == []


# --------------------------------------------------------------------------
# 4. simulate
# --------------------------------------------------------------------------

class TestSimulate:
    def test_long_stop_hit(self):
        sonuc = cf.simulate(
            direction=Direction.LONG, entry_price=100.0,
            stop_price=99.0, tp1_price=102.0,
            candles=[
                mum(BASE, high=100.5, low=99.5, close=100.2),
                mum(BASE + 300, high=101.0, low=98.9, close=99.1),
            ],
        )
        assert sonuc["outcome"] == cf.OUTCOME_STOP
        assert sonuc["exit_price"] == 99.0
        assert sonuc["bars"] == 2
        assert sonuc["at_epoch"] == BASE + 600
        assert sonuc["price_move_pct"] == -1.0
        assert sonuc["model"] == cf.MODEL_ID

    def test_long_tp1_hit(self):
        sonuc = cf.simulate(
            direction="LONG", entry_price=100.0,
            stop_price=99.0, tp1_price=102.0,
            candles=[
                mum(BASE, high=100.5, low=99.5, close=100.2),
                mum(BASE + 300, high=102.5, low=99.5, close=102.2),
            ],
        )
        assert sonuc["outcome"] == cf.OUTCOME_TP1
        assert sonuc["exit_price"] == 102.0
        assert sonuc["bars"] == 2
        assert sonuc["price_move_pct"] == 2.0

    def test_short_stop_hit(self):
        sonuc = cf.simulate(
            direction=Direction.SHORT, entry_price=100.0,
            stop_price=101.0, tp1_price=98.0,
            candles=[mum(BASE, high=101.2, low=99.8, close=100.9)],
        )
        assert sonuc["outcome"] == cf.OUTCOME_STOP
        assert sonuc["exit_price"] == 101.0
        # SHORT'ta lehe hareket TERS işaretlidir: yukarı gitmek zarardır.
        assert sonuc["price_move_pct"] == -1.0

    def test_short_tp1_hit(self):
        sonuc = cf.simulate(
            direction="short", entry_price=100.0,
            stop_price=101.0, tp1_price=98.0,
            candles=[mum(BASE, high=100.4, low=97.5, close=97.9)],
        )
        assert sonuc["outcome"] == cf.OUTCOME_TP1
        assert sonuc["exit_price"] == 98.0
        assert sonuc["price_move_pct"] == 2.0

    def test_same_candle_tie_goes_to_stop(self):
        """Aynı mumda hem stop hem TP1 görülürse KARAMSAR taraf seçilir."""
        ortak = [mum(BASE, high=102.5, low=98.5, close=101.0)]

        uzun = cf.simulate(
            direction="LONG", entry_price=100.0,
            stop_price=99.0, tp1_price=102.0, candles=ortak,
        )
        assert uzun["outcome"] == cf.OUTCOME_STOP
        assert uzun["exit_price"] == 99.0

        kisa = cf.simulate(
            direction="SHORT", entry_price=100.0,
            stop_price=101.0, tp1_price=99.0, candles=ortak,
        )
        assert kisa["outcome"] == cf.OUTCOME_STOP
        assert kisa["exit_price"] == 101.0

    def test_no_level_hit_marks_open_at_last_close(self):
        sonuc = cf.simulate(
            direction="LONG", entry_price=100.0,
            stop_price=95.0, tp1_price=105.0,
            candles=[
                mum(BASE, high=100.5, low=99.5, close=100.2),
                mum(BASE + 300, high=101.0, low=99.8, close=100.9),
            ],
        )
        assert sonuc["outcome"] == cf.OUTCOME_OPEN
        assert sonuc["exit_price"] == 100.9          # mark-to-market
        assert sonuc["bars"] == 2
        assert sonuc["price_move_pct"] == 0.9

    def test_empty_candles_is_no_data(self):
        sonuc = cf.simulate(
            direction="LONG", entry_price=100.0,
            stop_price=99.0, tp1_price=102.0, candles=[],
        )
        assert sonuc["outcome"] == cf.OUTCOME_NO_DATA
        assert sonuc["exit_price"] is None
        assert sonuc["at_epoch"] is None
        assert sonuc["price_move_pct"] is None
        assert sonuc["bars"] == 0

    def test_missing_level_disables_that_leg(self):
        mumlar = [mum(BASE, high=102.5, low=98.5, close=101.0)]

        # Stop YOK → yalnız TP1 bacağı aranır (dip 98.5 görülse bile).
        yalniz_tp1 = cf.simulate(
            direction="LONG", entry_price=100.0,
            stop_price=None, tp1_price=102.0, candles=mumlar,
        )
        assert yalniz_tp1["outcome"] == cf.OUTCOME_TP1

        # TP1 YOK → yalnız stop bacağı aranır.
        yalniz_stop = cf.simulate(
            direction="LONG", entry_price=100.0,
            stop_price=99.0, tp1_price=0.0, candles=mumlar,
        )
        assert yalniz_stop["outcome"] == cf.OUTCOME_STOP

        # İkisi de yok → sonuç kaçınılmaz olarak `open`.
        ikisi_de_yok = cf.simulate(
            direction="LONG", entry_price=100.0,
            stop_price=None, tp1_price=None, candles=mumlar,
        )
        assert ikisi_de_yok["outcome"] == cf.OUTCOME_OPEN
        assert ikisi_de_yok["exit_price"] == 101.0

    def test_broken_entry_or_direction_is_no_data(self):
        mumlar = [mum(BASE, high=102.5, low=98.5, close=101.0)]
        for bozuk in (0.0, -5.0, None, "abc"):
            sonuc = cf.simulate(
                direction="LONG", entry_price=bozuk,
                stop_price=99.0, tp1_price=102.0, candles=mumlar,
            )
            assert sonuc["outcome"] == cf.OUTCOME_NO_DATA, bozuk
        yonsuz = cf.simulate(
            direction=None, entry_price=100.0,
            stop_price=99.0, tp1_price=102.0, candles=mumlar,
        )
        assert yonsuz["outcome"] == cf.OUTCOME_NO_DATA


# --------------------------------------------------------------------------
# 5. Look-ahead DÜŞMANCA testi
# --------------------------------------------------------------------------

def _pending(**kwargs: Any) -> Dict[str, Any]:
    varsayilan = dict(
        at="2026-08-24T00:00:00+00:00",
        at_epoch=BASE,
        symbol="BTCUSDT",
        direction=Direction.LONG,
        reason=intent.REASON_REGIME_GATE,
        price=100.0,
        stop_price=99.0,
        tp1_price=102.0,
        leverage=10,
        strategy="C",
        source="scanner",
        horizons_h=[1.0],
    )
    varsayilan.update(kwargs)
    return cf.build_pending(**varsayilan)


#: Niyet anından SONRA açılan, hiçbir seviyeyi vurmayan üç mum (3 × 20 dk = 1 sa).
TEMIZ_MUMLAR = [
    mum(BASE, high=100.5, low=99.5, close=100.2, dakika=20.0),
    mum(BASE + 1200, high=100.8, low=99.8, close=100.5, dakika=20.0),
    mum(BASE + 2400, high=101.0, low=100.0, close=100.9, dakika=20.0),
]


class TestLookAheadAdversarial:
    def test_pre_intent_candle_cannot_change_the_result(self):
        """Niyetten ÖNCE açılmış bir mum sonucu DEĞİŞTİRMEMELİDİR.

        Kirli sette, niyet anını içeren yarım mum hem stop (98.0) hem TP1
        (103.0) seviyesini kapsar. Look-ahead koruması olmasaydı sonuç
        `open`'dan `stop`'a dönerdi — yani kapı, aslında olmamış bir zararla
        haklı çıkarılırdı. İki setin `sim` bloğu BİREBİR aynı olmalıdır.
        """
        kirli_mum = mum(BASE - 600, high=103.0, low=98.0, close=100.0, dakika=20.0)
        temiz = cf.resolve(
            pending=_pending(), candles=list(TEMIZ_MUMLAR), now_epoch=BASE + 3600
        )
        kirli = cf.resolve(
            pending=_pending(),
            candles=[kirli_mum] + list(TEMIZ_MUMLAR),
            now_epoch=BASE + 3600,
        )

        assert temiz["sim"]["outcome"] == cf.OUTCOME_OPEN
        assert temiz["sim"] == kirli["sim"]
        assert temiz["horizons"] == kirli["horizons"]
        assert temiz["pnl_roi_pct"] == kirli["pnl_roi_pct"]

    def test_simulated_candles_are_all_after_the_intent(self):
        """`resolve`'un simülasyona verdiği pencerede niyet öncesi mum YOKTUR."""
        kirli_mum = mum(BASE - 600, high=103.0, low=98.0, close=100.0, dakika=20.0)
        pencere = cf.window(
            [kirli_mum] + list(TEMIZ_MUMLAR), BASE, BASE + 3600
        )
        assert pencere == TEMIZ_MUMLAR
        assert all(candle.open_time >= int(BASE * 1000) for candle in pencere)


# --------------------------------------------------------------------------
# 6. resolve
# --------------------------------------------------------------------------

class TestResolve:
    def test_not_matured_returns_none(self):
        # En büyük ufuk 1 saat; 1 saniye eksikle kayıt HÂLÂ bekliyor.
        assert cf.resolve(
            pending=_pending(horizons_h=[0.5, 1.0]),
            candles=list(TEMIZ_MUMLAR),
            now_epoch=BASE + 3599,
        ) is None

    def test_matured_row_carries_horizons_and_sim(self):
        satir = cf.resolve(
            pending=_pending(horizons_h=[0.5, 1.0]),
            candles=list(TEMIZ_MUMLAR),
            now_epoch=BASE + 3600,
        )
        assert satir is not None
        assert satir["measured"] is True
        assert satir["resolved_at_epoch"] == BASE + 3600
        assert satir["symbol"] == "BTCUSDT"
        assert satir["direction"] == "LONG"
        assert satir["reason"] == intent.REASON_REGIME_GATE
        assert satir["leverage"] == 10
        assert satir["dup_count"] == 1
        assert satir["model"] == cf.MODEL_ID
        assert satir["version"] == cf.COUNTERFACTUAL_VERSION

        yarim, tam = satir["horizons"]
        assert yarim["h"] == 0.5
        assert yarim["price"] == 100.2          # BASE+1800'de kapanmış son mum
        assert yarim["move_pct"] == 0.2
        assert yarim["roi_pct"] == 2.0          # %0.2 × 10x
        assert tam["h"] == 1.0
        assert tam["price"] == 100.9
        assert tam["move_pct"] == 0.9
        assert tam["roi_pct"] == 9.0

        assert satir["sim"]["outcome"] == cf.OUTCOME_OPEN
        assert satir["sim"]["bars"] == 3
        assert satir["sim"]["horizon_h"] == 1.0
        assert satir["sim"]["price_move_pct"] == 0.9
        assert satir["pnl_roi_pct"] == 9.0

    def test_stop_is_reported_with_negative_roi(self):
        vuran = list(TEMIZ_MUMLAR[:1]) + [
            mum(BASE + 1200, high=100.4, low=98.5, close=98.8, dakika=20.0)
        ]
        satir = cf.resolve(
            pending=_pending(), candles=vuran, now_epoch=BASE + 7200
        )
        assert satir["sim"]["outcome"] == cf.OUTCOME_STOP
        assert satir["sim"]["exit_price"] == 99.0
        assert satir["pnl_roi_pct"] == -10.0     # %-1 × 10x

    def test_no_candles_means_unmeasured_and_all_numbers_none(self):
        satir = cf.resolve(
            pending=_pending(), candles=[], now_epoch=BASE + 3600
        )
        assert satir is not None
        assert satir["measured"] is False
        assert satir["pnl_roi_pct"] is None
        assert satir["sim"]["outcome"] == cf.OUTCOME_NO_DATA
        assert satir["sim"]["exit_price"] is None
        assert satir["sim"]["price_move_pct"] is None
        assert satir["sim"]["at_epoch"] is None
        assert satir["horizons"][0]["price"] is None
        assert satir["horizons"][0]["move_pct"] is None
        assert satir["horizons"][0]["roi_pct"] is None

    def test_broken_now_epoch_keeps_the_record_pending(self):
        assert cf.resolve(
            pending=_pending(), candles=list(TEMIZ_MUMLAR), now_epoch=None
        ) is None

    def test_record_without_at_epoch_is_closed_as_unmeasured(self):
        bozuk = _pending()
        bozuk["at_epoch"] = None
        satir = cf.resolve(
            pending=bozuk, candles=list(TEMIZ_MUMLAR), now_epoch=BASE + 3600
        )
        assert satir is not None            # kuyrukta sonsuza dek birikmez
        assert satir["measured"] is False
        assert satir["horizons"][0]["price"] is None

    def test_empty_horizons_matures_immediately_as_no_data(self):
        satir = cf.resolve(
            pending=_pending(horizons_h=[]),
            candles=list(TEMIZ_MUMLAR),
            now_epoch=BASE,
        )
        assert satir is not None
        assert satir["horizons"] == []
        assert satir["sim"]["horizon_h"] == 0.0
        assert satir["measured"] is False


# --------------------------------------------------------------------------
# 7. summarize
# --------------------------------------------------------------------------

def _cozulmus(
    reason: Any,
    outcome: str,
    roi: Optional[float],
    *,
    dup: int = 1,
    measured: bool = True,
) -> Dict[str, Any]:
    """Çözülmüş satırın özet için gereken en küçük biçimi."""
    return {
        "reason": reason,
        "dup_count": dup,
        "measured": measured,
        "sim": {"outcome": outcome},
        "pnl_roi_pct": roi,
    }


class TestSummarize:
    def _satirlar(self) -> List[Dict[str, Any]]:
        return [
            _cozulmus(intent.REASON_REGIME_GATE, cf.OUTCOME_TP1, 20.0, dup=3),
            _cozulmus(intent.REASON_REGIME_GATE, cf.OUTCOME_STOP, -10.0),
            _cozulmus(intent.REASON_TV_CONFLUENCE, cf.OUTCOME_TP1, 30.0, dup=2),
            _cozulmus(intent.REASON_TV_CONFLUENCE, cf.OUTCOME_OPEN, 5.0),
            _cozulmus("zurna", cf.OUTCOME_NO_DATA, None, measured=False),
        ]

    def test_totals_and_ordering(self):
        ozet = cf.summarize(self._satirlar())
        assert ozet["total"] == 5
        assert ozet["measured"] == 4
        assert ozet["unmeasured"] == 1
        # n çoktan aza; eşitlikte ada göre.
        assert [g["reason"] for g in ozet["by_reason"]] == [
            intent.REASON_REGIME_GATE,
            intent.REASON_TV_CONFLUENCE,
            intent.REASON_OTHER_BUCKET,
        ]

    def test_profit_factor_average_and_ci(self):
        ozet = cf.summarize(self._satirlar())
        rejim = ozet["by_reason"][0]
        assert rejim["n"] == 2
        assert rejim["measured"] == 2
        assert rejim["collapsed"] == 4          # 3 + 1 ham niyet
        assert rejim["tp1"] == 1 and rejim["stop"] == 1
        assert rejim["avg_roi_pct"] == 5.0
        assert rejim["sum_roi_pct"] == 10.0
        assert rejim["profit_factor"] == 2.0    # 20 / |−10|
        assert rejim["ci95_roi_pct"] == [-24.4, 34.4]

        tv = ozet["by_reason"][1]
        assert tv["collapsed"] == 3
        assert tv["tp1"] == 1 and tv["open"] == 1
        assert tv["avg_roi_pct"] == 17.5
        # Hiç zarar yok → payda 0. JSON'da sonsuz YOKTUR → None.
        assert tv["profit_factor"] is None
        assert tv["ci95_roi_pct"] == [-7.0, 42.0]

    def test_unknown_reason_falls_into_other_bucket(self):
        ozet = cf.summarize(self._satirlar())
        diger = ozet["by_reason"][2]
        assert diger["reason"] == intent.REASON_OTHER_BUCKET
        assert diger["label"] == intent.REASON_LABELS[intent.REASON_OTHER_BUCKET]
        assert diger["n"] == 1
        assert diger["measured"] == 0
        assert diger["no_data"] == 1
        # Ölçülemeyen satır ortalamayı/PF'yi KİRLETMEZ.
        assert diger["avg_roi_pct"] is None
        assert diger["sum_roi_pct"] is None
        assert diger["profit_factor"] is None
        assert diger["ci95_roi_pct"] is None

    def test_overall_row(self):
        toplam = cf.summarize(self._satirlar())["overall"]
        assert toplam["reason"] == cf.REASON_TOTAL
        assert toplam["n"] == 5
        assert toplam["measured"] == 4
        assert toplam["collapsed"] == 8
        assert toplam["tp1"] == 2
        assert toplam["stop"] == 1
        assert toplam["open"] == 1
        assert toplam["no_data"] == 1
        assert toplam["sum_roi_pct"] == 45.0
        assert toplam["avg_roi_pct"] == 11.25
        assert toplam["profit_factor"] == 5.5   # 55 / |−10|
        assert toplam["ci95_roi_pct"] == [-5.9, 28.4]

    def test_non_dict_and_unmeasured_rows_are_counted_but_excluded(self):
        ozet = cf.summarize([
            "sözlük değil",
            None,
            _cozulmus(intent.REASON_CAPACITY, cf.OUTCOME_TP1, 40.0, measured=False),
        ])
        assert ozet["total"] == 3
        assert ozet["measured"] == 0
        assert ozet["unmeasured"] == 3
        assert ozet["overall"]["no_data"] == 3
        assert ozet["overall"]["avg_roi_pct"] is None
        # Gerekçesiz satırlar `_yok_` kovasına düşer (intent ile AYNI kural).
        kovalar = {g["reason"]: g for g in ozet["by_reason"]}
        assert kovalar[intent.REASON_NONE_BUCKET]["n"] == 2
        assert kovalar[intent.REASON_CAPACITY]["n"] == 1
        assert kovalar[intent.REASON_CAPACITY]["measured"] == 0

    def test_single_sample_has_no_confidence_interval(self):
        ozet = cf.summarize(
            [_cozulmus(intent.REASON_CAPACITY, cf.OUTCOME_TP1, 12.0)]
        )
        grup = ozet["by_reason"][0]
        assert grup["avg_roi_pct"] == 12.0
        assert grup["ci95_roi_pct"] is None     # ddof=1 → tek gözlemde GA yok

    def test_empty_input(self):
        ozet = cf.summarize([])
        assert ozet == {
            "total": 0,
            "measured": 0,
            "unmeasured": 0,
            "by_reason": [],
            "by_plan_source": [],
            "by_measurement": [],
            "mixed_measurements": False,
            "overall": {
                "reason": cf.REASON_TOTAL,
                "label": cf.REASON_TOTAL_LABEL,
                "n": 0,
                "measured": 0,
                "collapsed": 0,
                "collapsed_tp1": 0,
                "collapsed_stop": 0,
                "tp1": 0,
                "stop": 0,
                "open": 0,
                "no_data": 0,
                "roi_n": 0,
                "avg_roi_pct": None,
                "sum_roi_pct": None,
                "profit_factor": None,
                "profit_factor_note": cf.PF_NOTE_NO_SAMPLE,
                "ci95_roi_pct": None,
            },
        }
        assert cf.summarize(None)["total"] == 0
