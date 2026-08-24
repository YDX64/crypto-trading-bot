"""D27/A2 + D27/A3 — adli kayıttaki ÖLÇÜM kusurlarının testleri.

Bu dosya YALNIZ iki düzeltmeyi kanıtlar; ikisi de motor davranışını
DEĞİŞTİRMEZ (D21 ilkesi: adli kayıt gözlemdir, karar yolu değildir):

A2 — `forensics.fee_estimate` merdiven-çıkış düzeltmesi.
     `exits._estimate_gross_pnl` brütü TEK çıkış fiyatıyla hesaplar; oysa
     takipçi/merdiven çıkışı üç ayrı fiyattan (TP1/TP2/runner) dolar.
     Ölçüldü (2026-08-24): 22 işlemin 8'inde tahmini komisyon teorik
     değerin 2 katından fazla, **5'inde NEGATİF** → `fee_dominated`
     etiketi geçersizdi. Artık brüt üç KAYNAKTAN birine bağlanır
     (`ledger_legs` / `single_leg_estimate` / `unmeasured_ladder`) ve
     ölçülemeyen ya da fiziksel olarak imkânsız komisyon için sayı
     YAZILMAZ.

A3 — MAE yoklama kusuru. `mae_roi_pct` safety turunda ÖRNEKLENİR (≈2 sn);
     iki yoklama arasındaki fitil görülmez. Ölçüldü: 6 stop-out'ta
     `mae_roi` fiziksel olarak imkânsızdı (id 217: mae_roi −7.16 ama
     gerçekleşen −24.72). `reconcile_mae` fiziksel kelepçeyi uygular;
     düzeltme SESSİZ DEĞİLDİR (ham örneklem ayrı alanda durur).

Repo konvansiyonu: sınıflar `__new__` ile kurulur ve alanlar elle
doldurulur (bkz. `tests/test_forensics.py::_exit_manager`).
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.strategies.scalper import forensics as fx
from src.strategies.scalper.exits import ExitManager, _CloseLedger
from src.strategies.scalper.types import Direction


# --------------------------------------------------------------------------
# Yardımcılar
# --------------------------------------------------------------------------

def _exit_manager(client=None, cfg=None):
    """`ExitManager`ı __init__ çalıştırmadan kur (repo konvansiyonu)."""
    from src.core.logger import app_logger

    manager = ExitManager.__new__(ExitManager)
    manager.client = client
    manager.cfg = cfg or SimpleNamespace(
        scalper_taker_fee_pct=0.05, scalper_maker_fee_pct=0.02
    )
    manager.logger = app_logger
    return manager


def _ladder_position(**overrides):
    """Merdiven durumunu taşıyan asgari pozisyon çifti."""
    sp = SimpleNamespace(tp1_done=False, tp2_done=False, trailing_active=False)
    for key, value in overrides.items():
        setattr(sp, key, value)
    return sp


def _ledger(gross_pnl=None, legs=0):
    return _CloseLedger(
        exit_price=98.0,
        exit_reason="SL",
        net_pnl_estimate=-1.78,
        close_fills=1,
        flatten_kind="SL",
        gross_pnl=gross_pnl,
        legs=legs,
    )


def _exit_doc(**overrides):
    """`fx.build_exit` için zorunlu argümanların TAMAMI (LONG, 20x)."""
    kwargs = dict(
        at="2026-08-24T10:00:00+00:00",
        reason="SL",
        exit_price=98.764,
        entry_price=100.0,
        quantity=1.0,
        leverage=20,
        direction=Direction.LONG,
        realized_pnl=-10.0,
        gross_pnl=-9.0,
        pnl_source="binance_income_net",
        mae_roi_pct=-30.0,
        mfe_roi_pct=4.0,
        duration_sec=600.0,
    )
    kwargs.update(overrides)
    return fx.build_exit(**kwargs)


# --------------------------------------------------------------------------
# 1) D27/A2 — brütün KAYNAĞI (saf, IO yok)
# --------------------------------------------------------------------------

class TestForensicsGross:
    """`ExitManager._forensics_gross` üç durumu AYRI raporlar."""

    def test_ledger_gross_wins_over_the_single_price_estimate(self):
        """Borsa fill'leri varsa brüt ONLARDAN gelir — merdiven dahil."""
        gross, source = ExitManager._forensics_gross(
            sp=_ladder_position(tp1_done=True, trailing_active=True),
            ledger=_ledger(gross_pnl=-1.6, legs=2),
            estimated_gross=-4.0,        # tek fiyatlı YANLIŞ tahmin
        )
        assert gross == pytest.approx(-1.6)
        assert source == ExitManager.GROSS_SOURCE_LEDGER
        assert source == "ledger_legs"

    def test_old_ledger_without_gross_falls_back_to_the_single_leg(self):
        """`gross_pnl=None` (D27 ÖNCESİ kayıt) + merdiven YOK → tek bacak."""
        gross, source = ExitManager._forensics_gross(
            sp=_ladder_position(),
            ledger=_ledger(gross_pnl=None),
            estimated_gross=-4.0,
        )
        assert gross == pytest.approx(-4.0)
        assert source == ExitManager.GROSS_SOURCE_SINGLE
        assert source == "single_leg_estimate"

    @pytest.mark.parametrize(
        "field", ["tp1_done", "tp2_done", "trailing_active"]
    )
    def test_ladder_without_a_ledger_is_declared_unmeasured(self, field):
        """Merdiven kısmen dolduysa ve ledger yoksa BRÜT YAZILMAZ.

        Uydurma sayı YASAK: tek çıkış fiyatıyla hesaplanan brüt yanlıştır
        (ölçüldü: 5 işlemde komisyon NEGATİF çıkmıştı).
        """
        gross, source = ExitManager._forensics_gross(
            sp=_ladder_position(**{field: True}),
            ledger=None,
            estimated_gross=-4.0,
        )
        assert gross is None
        assert source == ExitManager.GROSS_SOURCE_UNMEASURED
        assert source == "unmeasured_ladder"

    def test_single_leg_close_without_a_ledger_keeps_the_estimate(self):
        """Hiç kısmi dolum yoksa kapanış TEK bacaktır — tahmin GEÇERLİ."""
        gross, source = ExitManager._forensics_gross(
            sp=_ladder_position(),
            ledger=None,
            estimated_gross=12.5,
        )
        assert gross == pytest.approx(12.5)
        assert source == ExitManager.GROSS_SOURCE_SINGLE

    def test_a_position_double_without_any_ladder_field_does_not_explode(self):
        """Eski/kısıtlı test çiftleri (alan YOK) AttributeError üretmez."""
        gross, source = ExitManager._forensics_gross(
            sp=SimpleNamespace(),
            ledger=None,
            estimated_gross=3.0,
        )
        assert gross == pytest.approx(3.0)
        assert source == ExitManager.GROSS_SOURCE_SINGLE


# --------------------------------------------------------------------------
# 2) D27/A2 — `_CloseLedger` iki bacaklı kapanışta brütü GÖRÜNÜR kılar
# --------------------------------------------------------------------------

class TestCloseLedgerGross:
    """`_verified_close_ledger` yeni alanları doldurur, neti DEĞİŞTİRMEZ."""

    @staticmethod
    def _client(now_ms):
        """TP1 + SL iki bacaklı kapanışı taklit eden borsa istemcisi."""
        algo_to_order = {55: 9001, 77: 9002}
        fills = {
            # SL bacağı — SONRA doldu (kapanışı yapan bacak).
            9001: [{
                "orderId": 9001, "buyer": False, "qty": "1.2", "price": "98.0",
                "realizedPnl": "-2.4", "commission": "0.05",
                "commissionAsset": "USDT", "time": now_ms, "id": 20,
            }],
            # TP1 bacağı — ÖNCE doldu, KENDİ fiyatından.
            9002: [{
                "orderId": 9002, "buyer": False, "qty": "0.8", "price": "101.0",
                "realizedPnl": "0.8", "commission": "0.03",
                "commissionAsset": "USDT", "time": now_ms - 60_000, "id": 10,
            }],
        }

        async def _get_algo_order(algo_id=None):
            return {"algoId": algo_id, "actualOrderId": algo_to_order[int(algo_id)]}

        async def _get_account_trades(symbol, order_id=None, limit=500):
            return list(fills[int(order_id)])

        return SimpleNamespace(
            get_algo_order=_get_algo_order,
            get_account_trades=_get_account_trades,
        )

    async def _build_ledger(self):
        now = datetime.now(timezone.utc)
        now_ms = int(now.timestamp() * 1000)
        manager = _exit_manager(client=self._client(now_ms))
        return await manager._verified_close_ledger(
            symbol="BTCUSDT",
            direction=Direction.LONG,
            quantity=2.0,
            entry_price=100.0,
            opened_at=now - timedelta(hours=1),
            sl_order_id="55",
            tp1_algo_id="77",
            tp2_algo_id=None,
            trailing_active=False,
            entry_fee_rate=0.0005,
        )

    async def test_gross_is_the_sum_of_all_legs(self):
        """Brüt İKİ bacağın Σ(realizedPnl)'idir — tek fiyat DEĞİL."""
        ledger = await self._build_ledger()
        assert ledger is not None
        assert ledger.gross_pnl == pytest.approx(0.8 + (-2.4))
        assert ledger.legs == 2
        # Tek çıkış fiyatıyla (98.0) hesaplanan brüt bambaşkadır — düzeltilen
        # kusur tam olarak budur.
        single_leg = ExitManager._estimate_gross_pnl(
            Direction.LONG, 100.0, 98.0, 2.0
        )
        assert single_leg == pytest.approx(-4.0)
        assert abs(ledger.gross_pnl - single_leg) > 1.0

    async def test_net_estimate_is_unchanged_by_the_new_fields(self):
        """Net hesabı D27 ÖNCESİ formülle BİREBİR aynı: gross−fees−giriş.

        D27 incelemesi-2 (bulgu 10): beklenen değer LİTERALDİR. Formülü test
        içinde yeniden uygulamak, kodda değişse bile testin yeşil kalmasına
        yol açabilirdi. Aritmetik: (0.8 − 2.4) − (0.05 + 0.03) − 0.1 = −1.78.
        """
        ledger = await self._build_ledger()
        assert ledger.net_pnl_estimate == pytest.approx(-1.78)
        # Ve net, yeni GÖRÜNÜR brütten türetilebilir olmalı (tutarlılık).
        assert ledger.net_pnl_estimate == pytest.approx(
            ledger.gross_pnl - 0.08 - 0.1
        )

    async def test_closing_leg_still_drives_price_reason_and_fill_count(self):
        """Kapanışı yapan bacak (en geç dolum) etiketi belirlemeye devam eder."""
        ledger = await self._build_ledger()
        assert ledger.exit_price == pytest.approx(98.0)
        assert ledger.exit_reason == "SL"
        assert ledger.flatten_kind == "SL"
        assert ledger.close_fills == 1        # yalnız kapatan emrin fill'leri

    async def test_the_ledger_feeds_the_forensics_gross_source(self):
        """Uçtan uca: iki bacaklı ledger → `ledger_legs` kaynağı."""
        ledger = await self._build_ledger()
        gross, source = ExitManager._forensics_gross(
            sp=_ladder_position(tp1_done=True),
            ledger=ledger,
            estimated_gross=-4.0,
        )
        assert gross == pytest.approx(-1.6)
        assert source == ExitManager.GROSS_SOURCE_LEDGER

    def test_new_dataclass_fields_default_to_unmeasured(self):
        """Eski KONUMSAL kullanım bozulmadı; yeni alanlar None/0 varsayar."""
        legacy = _CloseLedger(98.0, "SL", -1.78, 1, "SL")
        assert legacy.gross_pnl is None
        assert legacy.legs == 0


# --------------------------------------------------------------------------
# 3) D27/A2 — `fee_estimate` ve KAYNAĞI
# --------------------------------------------------------------------------

class TestFeeEstimateSource:
    """Komisyon tahmini brütün kaynağına bağlıdır; imkânsız değer YAZILMAZ."""

    def test_measured_gross_yields_a_fee_with_its_source(self):
        doc = _exit_doc(
            gross_pnl=12.0,
            realized_pnl=10.0,
            gross_source=ExitManager.GROSS_SOURCE_LEDGER,
        )
        assert doc["fee_estimate"] == pytest.approx(2.0)
        assert doc["fee_estimate_source"] == "ledger_legs"
        assert doc["gross_source"] == "ledger_legs"

    def test_unmeasured_gross_writes_no_number(self):
        """Brüt ölçülemediyse komisyon da ölçülmemiştir — uydurma YASAK."""
        doc = _exit_doc(
            gross_pnl=None,
            realized_pnl=10.0,
            gross_source=ExitManager.GROSS_SOURCE_UNMEASURED,
        )
        assert doc["fee_estimate"] is None
        assert doc["fee_estimate_source"] == "unmeasured"
        # Kaynak yine de RAPOR EDİLİR: "neden ölçülemedi" görünsün.
        assert doc["gross_source"] == "unmeasured_ladder"

    def test_negative_fee_is_impossible_and_is_flagged_inconsistent(self):
        """net > brüt → fiziksel olarak imkânsız komisyon.

        Raporda "5 işlemde NEGATİF" bulgusunun düzeltmesi budur: sayı
        yazmak yerine tutarsızlık İLAN EDİLİR.
        """
        doc = _exit_doc(
            gross_pnl=5.0,
            realized_pnl=7.0,
            gross_source=ExitManager.GROSS_SOURCE_SINGLE,
        )
        assert doc["fee_estimate"] is None
        assert doc["fee_estimate_source"] == "inconsistent"

    def test_missing_net_also_leaves_the_fee_unmeasured(self):
        doc = _exit_doc(gross_pnl=5.0, realized_pnl=None, gross_source="x")
        assert doc["fee_estimate"] is None
        assert doc["fee_estimate_source"] == "unmeasured"

    def test_measured_fee_without_a_source_is_named_unknown(self):
        """Kaynak geçilmediyse tahmin YAZILIR ama kaynağı "unknown" der."""
        doc = _exit_doc(gross_pnl=12.0, realized_pnl=10.0)
        assert doc["fee_estimate"] == pytest.approx(2.0)
        assert doc["fee_estimate_source"] == "unknown"
        assert doc["gross_source"] is None

    def test_fee_dominated_requires_a_measured_fee(self):
        """Etiket YALNIZ `fee_estimate` ölçüldüyse atılır."""
        measured = {
            "realized_pnl": 1.0,        # net < 0.5 × brüt
            "gross_pnl": 12.0,
            "fee_estimate": 11.0,
            "fee_estimate_source": "ledger_legs",
        }
        assert fx.TAG_FEE_DOMINATED in fx.classify_exit(None, measured)

        unmeasured = dict(measured, fee_estimate=None,
                          fee_estimate_source="unmeasured")
        # AYNI belge, tek fark ölçümün yokluğu → etiket ATILMAZ.
        assert fx.TAG_FEE_DOMINATED not in fx.classify_exit(None, unmeasured)

    def test_inconsistent_fee_does_not_earn_the_tag_either(self):
        """Negatif komisyon üretmiş bir kayıt da etiketlenemez."""
        doc = {
            "realized_pnl": 1.0,
            "gross_pnl": 12.0,
            "fee_estimate": None,
            "fee_estimate_source": "inconsistent",
        }
        assert fx.classify_exit(None, doc) == []


# --------------------------------------------------------------------------
# 4) D27/A3 — MAE fiziksel kelepçesi (SAF)
# --------------------------------------------------------------------------

class TestReconcileMae:
    """`fx.reconcile_mae`: örneklem çıkış ROI'sinden İYİ olamaz."""

    def test_impossible_sample_is_corrected_to_the_exit_roi(self):
        """id 217 vakası: mae_roi −7.16 iken gerçekleşen −24.72."""
        value, source = fx.reconcile_mae(
            mae_roi_pct=-7.16, price_move_pct=-1.236, leverage=20
        )
        assert value == pytest.approx(-24.72, abs=1e-6)
        assert source == fx.MAE_SOURCE_CORRECTED
        assert source == "corrected"

    def test_a_worse_sample_than_the_exit_is_kept(self):
        """MAE çıkıştan DAHA KÖTÜ olabilir — bu ihlal değildir."""
        value, source = fx.reconcile_mae(
            mae_roi_pct=-30.0, price_move_pct=-1.236, leverage=20
        )
        assert value == pytest.approx(-30.0)
        assert source == fx.MAE_SOURCE_SAMPLED

    def test_exactly_equal_is_not_a_violation(self):
        value, source = fx.reconcile_mae(
            mae_roi_pct=-24.72, price_move_pct=-1.236, leverage=20
        )
        assert value == pytest.approx(-24.72)
        assert source == fx.MAE_SOURCE_SAMPLED

    def test_missing_sample_is_unmeasured(self):
        assert fx.reconcile_mae(
            mae_roi_pct=None, price_move_pct=-1.236, leverage=20
        ) == (None, fx.MAE_SOURCE_UNMEASURED)

    def test_missing_price_move_returns_the_sample_untouched(self):
        """Kıyas tabanı yoksa kelepçe UYGULANAMAZ — örneklem aynen döner."""
        value, source = fx.reconcile_mae(
            mae_roi_pct=-7.16, price_move_pct=None, leverage=20
        )
        assert value == pytest.approx(-7.16)
        assert source == fx.MAE_SOURCE_SAMPLED

    def test_short_uses_the_trade_favourable_move(self):
        """SHORT'ta `_pct_move` zaten işlem LEHİNE hesaplanır."""
        # Fiyat DÜŞTÜ → SHORT lehine (+%1.236) → çıkış ROI +24.72.
        favourable = fx._pct_move(Direction.SHORT, 100.0, 98.764)
        assert favourable == pytest.approx(1.236)
        value, source = fx.reconcile_mae(
            mae_roi_pct=-30.0, price_move_pct=favourable, leverage=20
        )
        assert value == pytest.approx(-30.0)
        assert source == fx.MAE_SOURCE_SAMPLED

    def test_short_stop_out_is_corrected_like_a_long(self):
        # Fiyat YÜKSELDİ → SHORT aleyhine (−%1.236) → çıkış ROI −24.72.
        adverse = fx._pct_move(Direction.SHORT, 100.0, 101.236)
        assert adverse == pytest.approx(-1.236)
        value, source = fx.reconcile_mae(
            mae_roi_pct=-7.16, price_move_pct=adverse, leverage=20
        )
        assert value == pytest.approx(-24.72, abs=1e-6)
        assert source == fx.MAE_SOURCE_CORRECTED

    @pytest.mark.parametrize("leverage", [0, None])
    def test_absent_leverage_behaves_like_one(self, leverage):
        """`int(leverage or 0) or 1` — 0/None kaldıraçsız (1x) sayılır."""
        value, source = fx.reconcile_mae(
            mae_roi_pct=-1.0, price_move_pct=-2.0, leverage=leverage
        )
        # 1x ile çıkış ROI −2.0 → −1.0 ihlaldir → düzeltilir.
        # (Kaldıraç 0 sayılsaydı çıkış ROI 0 olur, ihlal GÖRÜLMEZDİ.)
        assert value == pytest.approx(-2.0)
        assert source == fx.MAE_SOURCE_CORRECTED


# --------------------------------------------------------------------------
# 5) D27/A3 — `build_exit` MAE alanları (sessiz düzeltme YOK)
# --------------------------------------------------------------------------

class TestBuildExitMaeFields:
    """Ham örneklem her zaman AYRI alanda korunur."""

    def test_violating_sample_is_corrected_and_preserved(self):
        doc = _exit_doc(mae_roi_pct=-7.16)     # LONG 100 → 98.764, 20x
        assert doc["price_move_pct"] == pytest.approx(-1.236)
        assert doc["mae_roi_pct"] == pytest.approx(-24.72)
        assert doc["mae_roi_pct_sampled"] == pytest.approx(-7.16)
        assert doc["mae_source"] == "corrected"
        # Fiyat yüzdesi DÜZELTİLMİŞ değerden türer (mae / kaldıraç).
        assert doc["mae_price_pct"] == pytest.approx(-1.236)

    def test_valid_sample_stays_sampled_and_identical(self):
        doc = _exit_doc(mae_roi_pct=-30.0)
        assert doc["mae_roi_pct"] == pytest.approx(-30.0)
        assert doc["mae_roi_pct_sampled"] == pytest.approx(-30.0)
        assert doc["mae_source"] == "sampled"
        assert doc["mae_price_pct"] == pytest.approx(-1.5)

    def test_missing_sample_is_reported_as_unmeasured(self):
        doc = _exit_doc(mae_roi_pct=None)
        assert doc["mae_roi_pct"] is None
        assert doc["mae_roi_pct_sampled"] is None
        assert doc["mae_source"] == "unmeasured"
        assert doc["mae_price_pct"] is None

    def test_sample_count_is_none_when_not_measured(self):
        """`mae_samples=None` = "ölçülmedi"; `0` yazmak uydurma olurdu."""
        assert _exit_doc()["mae_samples"] is None
        assert _exit_doc(mae_samples=None)["mae_samples"] is None

    def test_sample_count_is_carried_verbatim(self):
        """Yoklama sayısı düzeltmenin ÇÖZÜNÜRLÜĞÜNÜ okuyana bildirir."""
        assert _exit_doc(mae_samples=317)["mae_samples"] == 317
        assert _exit_doc(mae_samples=0)["mae_samples"] == 0

    def test_mfe_is_untouched_by_the_mae_clamp(self):
        """Kelepçe YALNIZ MAE'yi ilgilendirir — tepe ROI aynen kalır."""
        doc = _exit_doc(mae_roi_pct=-7.16, mfe_roi_pct=26.0)
        assert doc["mfe_roi_pct"] == pytest.approx(26.0)
        assert doc["mfe_price_pct"] == pytest.approx(1.3)


# --------------------------------------------------------------------------
# 6) D27/A3 — yoklama sayacı (`_update_mae_mfe`)
# --------------------------------------------------------------------------

class _SlottedPosition:
    """`mae_samples` alanı OLMAYAN katı çift (setattr REDDEDİLİR)."""

    __slots__ = ("position", "signal", "mae_pct", "mfe_pct")

    def __init__(self, position, signal):
        self.position = position
        self.signal = signal
        self.mae_pct = 0.0
        self.mfe_pct = 0.0


def _live_position(entry_price=100.0, leverage=20,
                   direction=Direction.LONG, with_counter=True):
    position = SimpleNamespace(entry_price=entry_price, leverage=leverage)
    signal = SimpleNamespace(direction=direction)
    if not with_counter:
        return _SlottedPosition(position, signal)
    return SimpleNamespace(
        position=position, signal=signal,
        mae_pct=0.0, mfe_pct=0.0, mae_samples=0,
    )


class TestMaeSampleCounter:
    """Sayaç artar; MAE/MFE uçları D27 ÖNCESİ gibi min/max kalır."""

    def test_counter_increments_on_every_poll(self):
        manager = _exit_manager()
        sp = _live_position()
        for price in (101.0, 99.0, 100.5):
            manager._update_mae_mfe(sp, price)
        assert sp.mae_samples == 3

    def test_edges_are_still_min_and_max(self):
        manager = _exit_manager()
        sp = _live_position()
        for price in (101.0, 99.0, 100.5):
            manager._update_mae_mfe(sp, price)
        assert sp.mfe_pct == pytest.approx(20.0)    # +%1 × 20x
        assert sp.mae_pct == pytest.approx(-20.0)   # −%1 × 20x

    def test_short_edges_are_mirrored(self):
        manager = _exit_manager()
        sp = _live_position(direction=Direction.SHORT)
        for price in (99.0, 101.0):
            manager._update_mae_mfe(sp, price)
        assert sp.mfe_pct == pytest.approx(20.0)
        assert sp.mae_pct == pytest.approx(-20.0)
        assert sp.mae_samples == 2

    def test_a_double_without_the_field_gains_it_silently(self):
        """`SimpleNamespace` çifti alanı taşımasa da akış KESİLMEZ."""
        manager = _exit_manager()
        sp = SimpleNamespace(
            position=SimpleNamespace(entry_price=100.0, leverage=20),
            signal=SimpleNamespace(direction=Direction.LONG),
            mae_pct=0.0, mfe_pct=0.0,
        )
        manager._update_mae_mfe(sp, 99.0)           # AttributeError YOK
        assert sp.mae_pct == pytest.approx(-20.0)
        assert sp.mae_samples == 1

    def test_a_double_that_rejects_the_field_still_updates_the_edges(self):
        """Sayaç YAZILAMASA bile MAE/MFE güncellenir (gözlem ≠ kilit)."""
        manager = _exit_manager()
        sp = _live_position(with_counter=False)
        manager._update_mae_mfe(sp, 99.0)           # AttributeError YOK
        manager._update_mae_mfe(sp, 101.0)
        assert sp.mae_pct == pytest.approx(-20.0)
        assert sp.mfe_pct == pytest.approx(20.0)
        assert not hasattr(sp, "mae_samples")

    def test_invalid_entry_price_returns_before_counting(self):
        """`entry_price <= 0` → erken dönüş; sayaç ARTMAZ."""
        manager = _exit_manager()
        for entry in (0.0, -5.0):
            sp = _live_position(entry_price=entry)
            manager._update_mae_mfe(sp, 100.0)
            assert sp.mae_samples == 0
            assert sp.mae_pct == pytest.approx(0.0)
            assert sp.mfe_pct == pytest.approx(0.0)
