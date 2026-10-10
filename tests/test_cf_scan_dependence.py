"""D27/B madde 5 — karşı-olgu ölçümünün tarama turuna bağımlılığı.

Kapsam (YENİ REST ağırlığı SIFIR kalır; mum çeken çözüm turu EKLENMEDİ):

  1. Sayaçlar: `unmeasured_gap` / `unmeasured_no_data` (ölçülemeyen çözüm
     NEDENİ) ve `unscanned_symbol` (yaş sınırında, sembolü hiç taranmadan
     düşen) ayrı sayılır.
  2. Evren dışı sembol: `register` satırı YİNE kaydeder ama kısa son
     kullanma verir (ufuk + birkaç saat) ve `unresolvable_symbol` sayar;
     evren içi satıra 48 saatlik varsayılan yaş geçerli kalır.
  3. Engine: yaş süpürmesi `_scan_tick` başında, `_entries_ready()`
     kapısından ÖNCE koşar (girişler kapalıyken defter kilitlenmez).
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any, Dict, Optional

import pytest

from src.strategies.scalper import counterfactual_store as store
from src.strategies.scalper.types import Candle

BASE = 1_700_000_000.0
HOUR = 3600.0


def _kur(**kwargs: Any) -> None:
    params: Dict[str, Any] = {
        "enabled": True,
        "horizons_h": (1.0,),
        "max_pending": 500,
        "dedup_sec": 0.0,
        "max_age_h": 48.0,
    }
    params.update(kwargs)
    store.configure(**params)


def _kaydet(**kwargs: Any) -> Optional[Dict[str, Any]]:
    params: Dict[str, Any] = {
        "at": "2026-08-24T00:00:00+00:00",
        "at_epoch": BASE,
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "reason": "regime_gate",
        "price": 100.0,
        "stop_price": 99.0,
        "tp1_price": 102.0,
        "leverage": 20,
    }
    params.update(kwargs)
    return store.register(**params)


def _mum(i: int) -> Candle:
    start = BASE + i * 60
    return Candle(
        open_time=int(start * 1000), open=100.0, high=100.5, low=99.5,
        close=100.0, volume=1.0, close_time=int((start + 60) * 1000) - 1,
    )


@pytest.fixture(autouse=True)
def _temiz():
    store.reset()
    _kur()
    yield
    store.reset()
    store.configure(enabled=False)


class TestUnresolvableSymbol:
    def test_evren_disi_satir_kaydedilir_ve_kisa_son_kullanma_alir(self):
        store.set_scan_universe(["BTCUSDT", "ETHUSDT"])
        row = _kaydet(symbol="AVAXUSDT")
        assert row is not None, "hiç kaydetmemek seçicilik ölçümünü yanlılaştırır"
        assert store.counters_snapshot()["unresolvable_symbol"] == 1
        assert store.counters_snapshot()["pending"] == 1
        # ufuk (1 sa) + pay; 48 saat DEĞİL.
        assert row["_expire_epoch"] == BASE + HOUR + store.UNRESOLVABLE_GRACE_SEC
        assert row["extra"]["unresolvable_symbol"] is True

    def test_evren_ici_satir_etiketlenmez_ve_varsayilan_yasi_korur(self):
        store.set_scan_universe(["BTCUSDT"])
        row = _kaydet(symbol="BTCUSDT")
        assert "_expire_epoch" not in row
        assert "unresolvable_symbol" not in row["extra"]
        assert store.counters_snapshot()["unresolvable_symbol"] == 0

    def test_evren_bilinmiyorsa_hicbir_sembol_evren_disi_sayilmaz(self):
        # `set_scan_universe` hiç çağrılmadı (ya da boş verildi).
        assert "_expire_epoch" not in _kaydet(symbol="AVAXUSDT")
        store.set_scan_universe([])
        assert "_expire_epoch" not in _kaydet(symbol="DOGEUSDT")
        assert store.counters_snapshot()["unresolvable_symbol"] == 0

    def test_sembol_adi_buyuk_kucuk_harf_duyarsiz(self):
        store.set_scan_universe(["btcusdt"])
        assert "_expire_epoch" not in _kaydet(symbol="BTCUSDT")

    def test_evren_disi_satir_kisa_surede_duser_evren_ici_kalir(self):
        store.set_scan_universe(["BTCUSDT"])
        _kaydet(symbol="BTCUSDT")
        _kaydet(symbol="AVAXUSDT")
        son = BASE + HOUR + store.UNRESOLVABLE_GRACE_SEC
        # Süre dolmadan: ikisi de bekliyor.
        assert store.sweep_expired(son - 1.0) == 0
        assert store.counters_snapshot()["pending"] == 2
        # Süre dolunca YALNIZ evren dışı satır düşer (48 saat dolmadı).
        assert store.sweep_expired(son + 1.0) == 1
        snap = store.counters_snapshot()
        assert snap["pending"] == 1 and snap["expired"] == 1
        assert store.bucket_keys() == ["BTCUSDT"]

    def test_evren_disi_sembol_sonradan_evrene_girerse_normal_cozulur(self):
        store.set_scan_universe(["BTCUSDT"])
        _kaydet(symbol="AVAXUSDT")
        store.set_scan_universe(["BTCUSDT", "AVAXUSDT"])
        out = store.resolve_symbol(
            "AVAXUSDT", [_mum(i) for i in range(60)], BASE + HOUR + 60
        )
        assert len(out) == 1 and out[0]["measured"] is True
        assert out[0]["extra"]["unresolvable_symbol"] is True  # tarihçe korunur

    def test_pending_kapasitesi_evren_disi_satirlarca_kalici_islgal_edilmez(self):
        store.set_scan_universe(["BTCUSDT"])
        _kur(max_pending=2)
        _kaydet(symbol="AAAUSDT", at_epoch=BASE)
        _kaydet(symbol="BBBUSDT", at_epoch=BASE)
        # Kısa son kullanma geçtikten SONRA yeni kayıt: kapasite süpürmesi
        # (48 saat beklemeden) yer açar ve kayıt düşmez.
        later = BASE + HOUR + store.UNRESOLVABLE_GRACE_SEC + 10
        assert _kaydet(symbol="BTCUSDT", at_epoch=later) is not None
        snap = store.counters_snapshot()
        assert snap["dropped_full"] == 0
        assert snap["expired"] == 2


class TestUnscannedAndUnmeasuredCounters:
    def test_taranmadan_yasa_takilan_unscanned_sayilir(self):
        _kur(horizons_h=(8.0,), max_age_h=1.0)
        _kaydet()
        # Çözüm kancası (resolve_symbol) bu sembol için HİÇ çalışmadı.
        assert store.sweep_expired(BASE + 2 * HOUR) == 1
        snap = store.counters_snapshot()
        assert snap["expired"] == 1
        assert snap["unscanned_symbol"] == 1

    def test_taranmis_sembolun_yasa_takilan_satiri_unscanned_degildir(self):
        _kur(horizons_h=(8.0,), max_age_h=1.0)
        _kaydet()
        # Kanca çalıştı (olgunlaşmamış → kuyrukta kalır)...
        store.resolve_symbol("BTCUSDT", [_mum(0)], BASE + 600)
        # ...sonra yaş sınırı doldu.
        assert store.sweep_expired(BASE + 2 * HOUR) == 1
        snap = store.counters_snapshot()
        assert snap["expired"] == 1
        assert snap["unscanned_symbol"] == 0

    def test_kayittan_ONCE_taranmis_sembol_yine_unscanned_sayilir(self):
        """Tarama kaydın ESKİSİ ise (aralıkta kesinti) satırı ölçmüş sayılmaz."""
        _kur(horizons_h=(8.0,), max_age_h=1.0)
        _kaydet(symbol="ETHUSDT", at_epoch=BASE)
        _kaydet(symbol="BTCUSDT", at_epoch=BASE)
        store.resolve_symbol("BTCUSDT", [_mum(0)], BASE + 600)
        # BTC için yeni kayıt, taramadan SONRA geldi ve bir daha taranmadı.
        _kaydet(symbol="BTCUSDT", at_epoch=BASE + 900, reason="hour_gate")
        store.sweep_expired(BASE + 900 + 2 * HOUR)
        snap = store.counters_snapshot()
        assert snap["expired"] == 3
        # ETH hiç taranmadı; BTC'nin ikinci satırı kayıttan beri taranmadı.
        assert snap["unscanned_symbol"] == 2

    def test_kapsam_bosluklu_olcumsuz_cozum_unmeasured_gap_sayar(self):
        store.set_scan_universe(["BTCUSDT"])
        _kaydet()
        assert store.resolve_symbol(
            "BTCUSDT", [_mum(i) for i in range(10)], BASE + 600
        ) == []
        out = store.resolve_symbol(
            "BTCUSDT", [_mum(i) for i in range(40, 60)], BASE + HOUR + 1
        )
        assert len(out) == 1 and out[0]["measured"] is False
        assert out[0]["sim"]["gap"] == "interior_gap"
        snap = store.counters_snapshot()
        assert snap["unmeasured_gap"] == 1
        assert snap["unmeasured_no_data"] == 0

    def test_mumsuz_olcumsuz_cozum_unmeasured_no_data_sayar(self):
        _kaydet()
        out = store.resolve_symbol("BTCUSDT", [], BASE + 2 * HOUR)
        assert len(out) == 1 and out[0]["measured"] is False
        snap = store.counters_snapshot()
        assert snap["unmeasured_gap"] + snap["unmeasured_no_data"] == 1

    def test_olculen_cozum_ne_gap_ne_no_data_sayar(self):
        _kaydet()
        out = store.resolve_symbol(
            "BTCUSDT", [_mum(i) for i in range(60)], BASE + HOUR + 60
        )
        assert out[0]["measured"] is True
        snap = store.counters_snapshot()
        assert snap["measured"] == 1
        assert snap["unmeasured_gap"] == 0 and snap["unmeasured_no_data"] == 0

    def test_resolved_esittir_measured_artı_olcumsuz_nedenler(self):
        _kaydet(symbol="BTCUSDT")
        _kaydet(symbol="ETHUSDT")
        store.resolve_symbol("BTCUSDT", [_mum(i) for i in range(60)], BASE + HOUR + 60)
        store.resolve_symbol("ETHUSDT", [], BASE + HOUR + 60)
        s = store.counters_snapshot()
        assert s["resolved"] == s["measured"] + s["unmeasured_gap"] + s["unmeasured_no_data"]

    def test_reset_yeni_sayaclari_ve_evreni_sifirlar(self):
        store.set_scan_universe(["BTCUSDT"])
        _kaydet(symbol="AVAXUSDT")
        store.reset()
        _kur()
        snap = store.counters_snapshot()
        for key in ("unmeasured_gap", "unmeasured_no_data", "unscanned_symbol",
                    "unresolvable_symbol"):
            assert snap[key] == 0, key
        assert "_expire_epoch" not in _kaydet(symbol="AVAXUSDT")  # evren sıfırlandı


class TestEngineSweepBeforeEntriesGate:
    """`_scan_tick`: süpürme `_entries_ready()` kapısından ÖNCE koşar."""

    def _engine(self, *, entries_ready: bool, backoff: str = "off"):
        from src.strategies.scalper.engine import ScalperEngine
        from src.core.logger import app_logger

        engine = ScalperEngine.__new__(ScalperEngine)
        engine.cfg = SimpleNamespace(scalper_symbol_allowlist="BTCUSDT,ETHUSDT")
        engine.logger = app_logger
        engine._universe = []
        engine._rest_weight_backoff_level = lambda: backoff
        engine._exclude_follower_symbols = lambda universe: list(universe)
        engine._entries_ready = lambda: entries_ready
        engine._mark_scan_degraded = lambda *a, **k: None
        engine._rest_weight_snapshot = lambda: {}
        return engine

    @pytest.mark.asyncio
    async def test_girisler_kapaliyken_yas_supurmesi_yine_calisir(self):
        _kur(horizons_h=(1.0,), max_age_h=48.0)
        old = time.time() - 100 * HOUR
        _kaydet(at_epoch=old)
        assert store.counters_snapshot()["pending"] == 1

        engine = self._engine(entries_ready=False)
        await engine._scan_tick()

        snap = store.counters_snapshot()
        assert snap["pending"] == 0 and snap["expired"] == 1
        assert snap["unscanned_symbol"] == 1

    @pytest.mark.asyncio
    async def test_rest_geri_cekilmesinde_de_supurme_calisir(self):
        _kur(horizons_h=(1.0,), max_age_h=48.0)
        _kaydet(at_epoch=time.time() - 100 * HOUR)

        engine = self._engine(entries_ready=True, backoff="soft")
        await engine._scan_tick()

        assert store.counters_snapshot()["expired"] == 1

    @pytest.mark.asyncio
    async def test_tarama_evreni_deftere_bildirilir_giris_kapali_olsa_da(self):
        _kur()
        engine = self._engine(entries_ready=False)
        await engine._scan_tick()
        # allowlist = BTCUSDT,ETHUSDT → AVAX evren dışı.
        row = _kaydet(symbol="AVAXUSDT", at_epoch=time.time())
        assert "_expire_epoch" in row
        assert "_expire_epoch" not in _kaydet(symbol="ETHUSDT", at_epoch=time.time())

    @pytest.mark.asyncio
    async def test_supurme_REST_cagrisi_yapmaz(self):
        """Sıfır REST: istemci/fetcher/scanner'a hiç dokunulmaz."""
        _kur()
        engine = self._engine(entries_ready=False)

        class _Boom:
            def __getattr__(self, name):
                raise AssertionError(f"REST'e dokunuldu: {name}")

        engine.client = _Boom()
        engine.fetcher = _Boom()
        engine.scanner = _Boom()
        await engine._scan_tick()  # allowlist dolu → scanner çağrılmaz

    def test_supurme_hatasi_tarama_turunu_dusurmez(self, monkeypatch):
        engine = self._engine(entries_ready=False)

        def _boom(*a, **k):
            raise RuntimeError("defter bozuk")

        monkeypatch.setattr(store, "sweep_expired", _boom)
        engine._counterfactual_sweep()  # istisna sızmamalı
