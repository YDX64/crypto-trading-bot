"""Madde 10 — REAPER / STALE_TP kapanışının dayanıklılığı.

Düzeltilen iki kusur (kapanış KURALI — yaş, ROI eşiği — DEĞİŞMEDİ):

  1. SIRA: `return` try/except dışındaydı; başarısız ilk deneme turu
     bitiriyordu. `tracked_symbols()` sırası sabit olduğundan kalıcı
     reddedilen ilk yaşlı pozisyon arkasındakileri aç bırakıyordu.
     Şimdi: hata dalı `continue`, tur başına başarısız deneme tavanı (2),
     sembol başına kısa backoff; `return` yalnız BAŞARILI gönderimden sonra.
  2. BAYAT MİKTAR: `sp.position.quantity` giriş dolumudur ve kısmi TP'den
     sonra güncellenmez. Şimdi canlı |positionAmt|
     (`get_position_risk(force_fresh=True)`) gönderilir; 0 ise emir YOK;
     okuma başarısızsa plan miktarına dönülür.

Mock yalnız borsa/ağ sınırında (`client._request_with_retry`,
`client.get_position_risk`, `client.quantize_quantity`); gerçek motor kodu
(`_reap_aged_positions`, `_close_stale_profitable_positions`,
`_submit_aged_close`, `_submit_reduce_only_market_close`) çalışır.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from src.core.logger import app_logger
from src.models.position import PositionSide
from src.strategies.scalper.engine import ScalperEngine
from src.strategies.scalper.types import Direction


def _sp(
    *,
    age_hours: float = 9.0,
    side: PositionSide = PositionSide.LONG,
    quantity: float = 1.5,
    current_price: float = 100.3,
    entry_price: float = 100.0,
):
    direction = Direction.LONG if side == PositionSide.LONG else Direction.SHORT
    return SimpleNamespace(
        position=SimpleNamespace(
            side=side, quantity=quantity,
            opened_at=datetime.now(timezone.utc) - timedelta(hours=age_hours),
            entry_price=entry_price, current_price=current_price, leverage=10,
        ),
        signal=SimpleNamespace(direction=direction),
        trailing_active=False,
        tp1_done=False,
        stale_tp_close_at=None,
        reaper_close_at=None,
        price_ts=time.monotonic(),
    )


class FakeClient:
    """Borsa sınırı: emir POST'ları kaydedilir; belirli semboller reddedilir."""

    def __init__(self, *, fail_symbols=(), amounts: Optional[Dict[str, Any]] = None,
                 risk_raises: bool = False, no_risk_reader: bool = False):
        self.posts: List[Dict[str, Any]] = []
        self.attempts: List[str] = []
        self.fail_symbols = set(fail_symbols)
        self.amounts = amounts or {}
        self.risk_calls: List[Dict[str, Any]] = []
        self._risk_raises = risk_raises
        if not no_risk_reader:
            self.get_position_risk = self._get_position_risk

    async def _get_position_risk(self, symbol, *, force_fresh=False):
        self.risk_calls.append({"symbol": symbol, "force_fresh": force_fresh})
        if self._risk_raises:
            raise RuntimeError("positionRisk 500")
        if symbol not in self.amounts:
            return None
        return {"symbol": symbol, "positionAmt": str(self.amounts[symbol])}

    async def quantize_quantity(self, symbol, qty):
        return round(float(qty), 6)

    async def _request_with_retry(self, method, path, params=None, signed=False):
        symbol = params["symbol"]
        self.attempts.append(symbol)
        if symbol in self.fail_symbols:
            raise RuntimeError(f"-2022 ReduceOnly Order is rejected ({symbol})")
        self.posts.append(dict(params))
        return {"orderId": 1}


def _engine(positions: dict, client: FakeClient, *, stale: bool = False):
    engine = ScalperEngine.__new__(ScalperEngine)
    engine.cfg = SimpleNamespace(
        scalper_max_hold_hours=8.0,
        scalper_stale_tp_hours=2.0 if stale else 0.0,
        scalper_stale_tp_min_roi_pct=2.0,
        scalper_safety_interval_seconds=8.0,
    )
    engine.logger = app_logger
    order = list(positions.keys())  # sabit, öngörülebilir sıra
    engine.exits = SimpleNamespace(
        tracked_symbols=lambda: list(order),
        _positions=positions,
    )
    engine.client = client
    return engine


def _three(**kwargs):
    return {
        "AAAUSDT": _sp(**kwargs),
        "BBBUSDT": _sp(**kwargs),
        "CCCUSDT": _sp(**kwargs),
    }


# --------------------------------------------------------------------------
# 1) Sıra kusuru — REAPER
# --------------------------------------------------------------------------

class TestReaperOrdering:
    @pytest.mark.asyncio
    async def test_basarisiz_ilk_deneme_turu_bitirmez_siradaki_kapatilir(self):
        positions = _three()
        client = FakeClient(fail_symbols={"AAAUSDT"})
        engine = _engine(positions, client)

        await engine._reap_aged_positions()

        assert client.attempts == ["AAAUSDT", "BBBUSDT"]
        assert [p["symbol"] for p in client.posts] == ["BBBUSDT"]
        assert positions["AAAUSDT"].reaper_close_at is None, "başarısıza damga yok"
        assert positions["BBBUSDT"].reaper_close_at, "başarılı olan damgalanır"
        assert positions["CCCUSDT"].reaper_close_at is None, "tur başına tek BAŞARILI"

    @pytest.mark.asyncio
    async def test_tur_basina_basarisiz_deneme_tavani_ikidir(self):
        positions = _three()
        client = FakeClient(fail_symbols={"AAAUSDT", "BBBUSDT", "CCCUSDT"})
        engine = _engine(positions, client)

        await engine._reap_aged_positions()

        assert client.attempts == ["AAAUSDT", "BBBUSDT"], "3. sembol denenmez"
        assert all(sp.reaper_close_at is None for sp in positions.values())

    @pytest.mark.asyncio
    async def test_basarisiz_sembol_backoff_icinde_yeniden_denenmez(self):
        positions = _three()
        client = FakeClient(fail_symbols={"AAAUSDT"})
        engine = _engine(positions, client)

        await engine._reap_aged_positions()           # AAA hata, BBB gitti
        client.attempts.clear()
        positions.pop("BBBUSDT")                      # exits.step kapanışı gördü
        await engine._reap_aged_positions()           # AAA backoff'ta → CCC

        assert "AAAUSDT" not in client.attempts
        assert client.attempts == ["CCCUSDT"]

    @pytest.mark.asyncio
    async def test_backoff_dolunca_sembol_yeniden_denenir(self):
        positions = {"AAAUSDT": _sp()}
        client = FakeClient(fail_symbols={"AAAUSDT"})
        engine = _engine(positions, client)

        await engine._reap_aged_positions()
        assert client.attempts == ["AAAUSDT"]
        # Backoff'u geçmişe çek (zaman geçti).
        engine._aged_close_backoff["AAAUSDT"] = time.monotonic() - 1.0
        await engine._reap_aged_positions()
        assert client.attempts == ["AAAUSDT", "AAAUSDT"]

    @pytest.mark.asyncio
    async def test_basari_uzun_hata_backoffunu_birakmaz_kisa_gonderim_beklemesi_koyar(self):
        """Niyet (eski ad: `test_basari_backoff_kaydini_temizler`): BAŞARILI
        gönderimden sonra önceki HATA backoff'u (uzun/bayat) kalmamalı.

        Davranış değişti (cubic/PR #7): kayıt artık tamamen silinmiyor —
        gönderimden sonra `exits.step` kapanışı uzlaştırana kadar ikinci bir
        reduce-only MARKET gitmesin diye KISA bir gönderim-sonrası bekleme
        (`_AGED_CLOSE_BACKOFF_SEC`) konur. Burada hata backoff'u çok uzun
        (1 saat) kurulur: başarıdan sonra kalan süre bu uzun değer değil,
        en çok gönderim beklemesi olmalıdır.
        """
        positions = {"AAAUSDT": _sp()}
        client = FakeClient(fail_symbols={"AAAUSDT"})
        engine = _engine(positions, client)
        await engine._reap_aged_positions()
        assert "AAAUSDT" in engine._aged_close_backoff

        client.fail_symbols.clear()
        # Uzun bir hata backoff'u simüle et, sonra süresi dolmuş say.
        engine._aged_close_backoff["AAAUSDT"] = time.monotonic() - 1.0
        await engine._reap_aged_positions()
        assert positions["AAAUSDT"].reaper_close_at
        remaining = engine._aged_close_backoff["AAAUSDT"] - time.monotonic()
        assert 0.0 < remaining <= engine._AGED_CLOSE_BACKOFF_SEC

    @pytest.mark.asyncio
    async def test_basari_sonrasi_ikinci_post_gitmez_ayni_ve_sonraki_tur(self):
        """REAPER 'sent' sonrası `exits.step` uzlaştırana kadar (pozisyon hâlâ
        izleniyor) aynı sembole ikinci reduce-only MARKET GİTMEZ."""
        positions = {"AAAUSDT": _sp()}
        client = FakeClient()
        engine = _engine(positions, client)

        await engine._reap_aged_positions()
        assert [p["symbol"] for p in client.posts] == ["AAAUSDT"]
        await engine._reap_aged_positions()  # sonraki safety turu
        await engine._reap_aged_positions()
        assert [p["symbol"] for p in client.posts] == ["AAAUSDT"]

    @pytest.mark.asyncio
    async def test_gonderim_beklemesi_dolunca_hala_aciksa_canli_miktarla_yeniden_gider(self):
        """Takılı kalma YOK: bekleme dolduğunda pozisyon hâlâ izleniyorsa
        (kapanış uzlaşmadı) canlı |positionAmt| ile yeniden denenir."""
        positions = {"AAAUSDT": _sp(quantity=1.5)}
        client = FakeClient(amounts={"AAAUSDT": 0.7})
        engine = _engine(positions, client)

        await engine._reap_aged_positions()
        assert len(client.posts) == 1
        engine._aged_close_backoff["AAAUSDT"] = time.monotonic() - 1.0  # süre doldu
        await engine._reap_aged_positions()

        assert len(client.posts) == 2
        assert client.posts[1]["quantity"] == 0.7
        assert client.posts[1]["reduceOnly"] == "true"

    @pytest.mark.asyncio
    async def test_gonderim_beklemesindeki_sembol_sirayi_tikamaz_siradaki_kapanir(self):
        """Tur başına tek BAŞARILI kapanış korunur ama gönderim beklemesindeki
        ilk sembol sonraki turda arkasındakileri aç bırakmaz."""
        positions = _three()
        client = FakeClient()
        engine = _engine(positions, client)

        await engine._reap_aged_positions()
        await engine._reap_aged_positions()
        assert [p["symbol"] for p in client.posts] == ["AAAUSDT", "BBBUSDT"]

    @pytest.mark.asyncio
    async def test_backoftaki_sembol_tavana_sayilmaz(self):
        """Backoff'taki sembol atlanır (`continue`); ardındakiler denenir."""
        positions = _three()
        client = FakeClient(fail_symbols={"BBBUSDT"})
        engine = _engine(positions, client)
        engine._aged_close_backoff = {"AAAUSDT": time.monotonic() + 60.0}

        await engine._reap_aged_positions()

        assert client.attempts == ["BBBUSDT", "CCCUSDT"]
        assert positions["CCCUSDT"].reaper_close_at

    @pytest.mark.asyncio
    async def test_kapanis_kurali_degismedi_genc_ve_trailing_dokunulmaz(self):
        positions = {
            "YOUNGUSDT": _sp(age_hours=2.0),
            "RUNNERUSDT": _sp(age_hours=50.0),
        }
        positions["RUNNERUSDT"].trailing_active = True
        client = FakeClient()
        engine = _engine(positions, client)

        await engine._reap_aged_positions()

        assert client.attempts == []


# --------------------------------------------------------------------------
# 2) Sıra kusuru — STALE_TP
# --------------------------------------------------------------------------

class TestStaleTpOrdering:
    @pytest.mark.asyncio
    async def test_basarisiz_ilk_deneme_turu_bitirmez(self):
        positions = _three(age_hours=3.0)
        client = FakeClient(fail_symbols={"AAAUSDT"})
        engine = _engine(positions, client, stale=True)

        await engine._close_stale_profitable_positions()

        assert client.attempts == ["AAAUSDT", "BBBUSDT"]
        assert positions["AAAUSDT"].stale_tp_close_at is None
        assert positions["BBBUSDT"].stale_tp_close_at
        assert positions["CCCUSDT"].stale_tp_close_at is None

    @pytest.mark.asyncio
    async def test_tavan_ve_backoff(self):
        positions = _three(age_hours=3.0)
        client = FakeClient(fail_symbols={"AAAUSDT", "BBBUSDT", "CCCUSDT"})
        engine = _engine(positions, client, stale=True)

        await engine._close_stale_profitable_positions()
        assert client.attempts == ["AAAUSDT", "BBBUSDT"]

        client.attempts.clear()
        await engine._close_stale_profitable_positions()
        # AAA/BBB backoff'ta; yalnız CCC denenir.
        assert client.attempts == ["CCCUSDT"]

    @pytest.mark.asyncio
    async def test_basari_sonrasi_ikinci_post_gitmez_ve_bekleme_konur(self):
        """STALE_TP 'sent' sonrası aynı sembole ikinci POST gitmez; sembol
        gönderim-sonrası beklemeye alınır (backoff haritasında kalır)."""
        positions = {"AAAUSDT": _sp(age_hours=3.0)}
        client = FakeClient()
        engine = _engine(positions, client, stale=True)

        await engine._close_stale_profitable_positions()
        await engine._close_stale_profitable_positions()
        await engine._reap_aged_positions()

        assert [p["symbol"] for p in client.posts] == ["AAAUSDT"]
        assert positions["AAAUSDT"].stale_tp_close_at
        remaining = engine._aged_close_backoff["AAAUSDT"] - time.monotonic()
        assert 0.0 < remaining <= engine._AGED_CLOSE_BACKOFF_SEC

    @pytest.mark.asyncio
    async def test_reaper_ve_stale_ayni_backoff_haritasini_paylasir(self):
        """Aynı sembolde aynı reddi iki yoldan art arda tekrarlamak anlamsız."""
        positions = {"AAAUSDT": _sp(age_hours=9.0)}
        client = FakeClient(fail_symbols={"AAAUSDT"})
        engine = _engine(positions, client, stale=True)

        await engine._close_stale_profitable_positions()
        await engine._reap_aged_positions()

        assert client.attempts == ["AAAUSDT"]


# --------------------------------------------------------------------------
# 3) Bayat miktar — canlı |positionAmt|
# --------------------------------------------------------------------------

class TestLiveQuantity:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", ["reaper", "stale"])
    async def test_canli_miktar_plan_miktarinin_yerine_gider(self, path):
        # Plan 1.5 (giriş dolumu), TP1 sonrası borsada yalnız 0.4 kaldı.
        positions = {"AAAUSDT": _sp(age_hours=9.0, quantity=1.5)}
        client = FakeClient(amounts={"AAAUSDT": "0.4"})
        engine = _engine(positions, client, stale=(path == "stale"))

        if path == "stale":
            await engine._close_stale_profitable_positions()
            stamp = positions["AAAUSDT"].stale_tp_close_at
        else:
            await engine._reap_aged_positions()
            stamp = positions["AAAUSDT"].reaper_close_at

        assert [p["quantity"] for p in client.posts] == [0.4]
        assert client.posts[0]["side"] == "SELL"
        assert client.posts[0]["reduceOnly"] == "true"
        assert stamp
        # Geri alınamaz karar: taze okuma ZORUNLU.
        assert client.risk_calls == [{"symbol": "AAAUSDT", "force_fresh": True}]

    @pytest.mark.asyncio
    async def test_canli_yon_isaretinden_alinir(self):
        positions = {"AAAUSDT": _sp(side=PositionSide.SHORT, quantity=3.0)}
        client = FakeClient(amounts={"AAAUSDT": "-2.0"})
        engine = _engine(positions, client)

        await engine._reap_aged_positions()

        assert client.posts[0]["side"] == "BUY"
        assert client.posts[0]["quantity"] == 2.0

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", ["reaper", "stale"])
    async def test_borsada_flat_ise_emir_ve_damga_yok_siradakine_gecilir(self, path):
        positions = {"AAAUSDT": _sp(age_hours=9.0), "BBBUSDT": _sp(age_hours=9.0)}
        client = FakeClient(amounts={"AAAUSDT": "0", "BBBUSDT": "1.5"})
        engine = _engine(positions, client, stale=(path == "stale"))

        if path == "stale":
            await engine._close_stale_profitable_positions()
            stamps = [sp.stale_tp_close_at for sp in positions.values()]
        else:
            await engine._reap_aged_positions()
            stamps = [sp.reaper_close_at for sp in positions.values()]

        assert [p["symbol"] for p in client.posts] == ["BBBUSDT"]
        assert stamps[0] is None and stamps[1]
        # flat başarısızlık DEĞİL (tavana sayılmaz, BBB yine kapatıldı) ama
        # yeniden okumayı geciktiren KISA bekleme alır — uzun hata backoff'u değil.
        left = engine._aged_close_backoff["AAAUSDT"] - time.monotonic()
        assert 0 < left <= engine._AGED_CLOSE_FLAT_BACKOFF_SEC < engine._AGED_CLOSE_BACKOFF_SEC

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", ["reaper", "stale"])
    async def test_flat_sembol_kisa_beklemede_positionrisk_yeniden_okunmaz(self, path):
        """D40 denetim notu: flat dönen sembol her safety turunda (weight 5)
        yeniden okunmamalı; bekleme dolunca tekrar okunur."""
        positions = {"AAAUSDT": _sp(age_hours=9.0)}
        client = FakeClient(amounts={"AAAUSDT": "0"})
        engine = _engine(positions, client, stale=(path == "stale"))
        run = (engine._close_stale_profitable_positions if path == "stale"
               else engine._reap_aged_positions)

        await run()
        await run()
        assert [c["symbol"] for c in client.risk_calls] == ["AAAUSDT"], "bekleme içinde ikinci okuma yok"
        assert client.posts == []

        engine._aged_close_backoff["AAAUSDT"] = time.monotonic() - 1.0  # bekleme doldu
        await run()
        assert len(client.risk_calls) == 2

    @pytest.mark.asyncio
    async def test_okuma_basarisizsa_plan_miktarina_donulur(self):
        positions = {"AAAUSDT": _sp(quantity=1.5)}
        client = FakeClient(risk_raises=True)
        engine = _engine(positions, client)

        await engine._reap_aged_positions()

        assert [p["quantity"] for p in client.posts] == [1.5]
        assert positions["AAAUSDT"].reaper_close_at

    @pytest.mark.asyncio
    async def test_okuyucu_yoksa_getattr_ile_plan_miktarina_donulur(self):
        positions = {"AAAUSDT": _sp(quantity=1.5)}
        client = FakeClient(no_risk_reader=True)
        engine = _engine(positions, client)

        await engine._reap_aged_positions()

        assert [p["quantity"] for p in client.posts] == [1.5]

    @pytest.mark.asyncio
    async def test_borsa_kayit_dondurmezse_plan_miktarina_donulur(self):
        """None = "kayıt yok", `flat` kanıtı DEĞİL: yanlışlıkla pozisyonu
        açık bırakmak yerine reduce-only plan miktarı denenir."""
        positions = {"AAAUSDT": _sp(quantity=1.5)}
        client = FakeClient(amounts={})          # get_position_risk → None
        engine = _engine(positions, client)

        await engine._reap_aged_positions()

        assert [p["quantity"] for p in client.posts] == [1.5]

    @pytest.mark.asyncio
    async def test_yuvarlama_sifira_dusurse_basarisizlik_sayilir_emir_yok(self):
        positions = {"AAAUSDT": _sp(quantity=1.5)}
        client = FakeClient(amounts={"AAAUSDT": "0.0000004"})

        async def zero(symbol, qty):
            return 0.0

        client.quantize_quantity = zero
        engine = _engine(positions, client)

        await engine._reap_aged_positions()

        assert client.posts == []
        assert positions["AAAUSDT"].reaper_close_at is None
        assert "AAAUSDT" in engine._aged_close_backoff

    @pytest.mark.asyncio
    async def test_close_position_market_dogrudan_cagrilmaz(self):
        """Senkron doğrulama safety turunu uzatır (rapor: YAPMA)."""
        positions = {"AAAUSDT": _sp()}
        client = FakeClient(amounts={"AAAUSDT": "1.5"})
        engine = _engine(positions, client)

        async def forbidden(*a, **k):
            raise AssertionError("_close_position_market çağrılmamalı")

        engine._close_position_market = forbidden
        await engine._reap_aged_positions()
        assert len(client.posts) == 1
