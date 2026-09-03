"""D30 — bayat-kâr kapanışı ("STALE_TP"): TP1 görmemiş, yaşlı ve KÂRDA pozisyon.

Sözleşme:
  1. Saf karar (`types.stale_tp_should_close`) canlı motor ve backtest
     harness'i tarafından ORTAK kullanılır; kapalıyken (saat = 0) her yerde
     False döner → varsayılan davranış bit düzeyinde aynı kalır
     (golden backtest testi bunu ayrıca sabitler).
  2. Harness (`backtest.manage_position`): yaş ≥ H ve mum kapanışı ROI ≥ min
     ise kalan miktar `STALE_TP` etiketiyle kapanır; ZARARDA pozisyona
     dokunulmaz (REAPER/EOD yolu sürer); TP1 görmüş koşucu MUAF; aynı mumda
     REAPER ile çakışırsa STALE_TP önce gelir.
  3. Canlı motor (`engine._close_stale_profitable_positions`): reaper ile aynı
     disiplin — tek emir yolu, tur başına en fazla bir kapanış, damga yalnız
     emir borsaya gittikten sonra, bayat fiyatla karar YOK, reaper damgalı
     pozisyona ikinci emir göndermez.
  4. Etiket: `_infer_exit_reason` damga varsa "STALE_TP"; eski (cooldown)
     çıkarım DEĞİŞMEZ; rapor/adli aile eşlemelerinde KENDİ ailesi.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core.logger import app_logger
from src.models.position import PositionSide
from src.strategies.scalper import forensics as fx
from src.strategies.scalper.backtest import OpenPosition, manage_position
from src.strategies.scalper.exits import EXIT_REASON_STALE_TP, ExitManager
from src.strategies.scalper.types import (
    Candle,
    Direction,
    position_roi_pct,
    stale_tp_should_close,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import ledger_report as lr  # noqa: E402

HOUR_MS = 3_600_000
MIN_MS = 60_000


# --------------------------------------------------------------------------
# 1) Saf karar ve ROI yardımcıları
# --------------------------------------------------------------------------

class TestPureHelpers:
    def test_kapaliyken_her_zaman_false(self):
        cfg = SimpleNamespace(scalper_stale_tp_hours=0.0, scalper_stale_tp_min_roi_pct=0.0)
        assert not stale_tp_should_close(cfg, age_ms=99 * HOUR_MS, roi_pct=50.0)
        cfg_missing = SimpleNamespace()  # alan yok → kapalı sayılır
        assert not stale_tp_should_close(cfg_missing, age_ms=99 * HOUR_MS, roi_pct=50.0)

    def test_yas_ve_roi_esikleri_dahil(self):
        cfg = SimpleNamespace(scalper_stale_tp_hours=2.0, scalper_stale_tp_min_roi_pct=2.0)
        assert stale_tp_should_close(cfg, age_ms=2 * HOUR_MS, roi_pct=2.0)      # tam eşik: kapat
        assert not stale_tp_should_close(cfg, age_ms=2 * HOUR_MS - 1, roi_pct=9.0)  # genç
        assert not stale_tp_should_close(cfg, age_ms=5 * HOUR_MS, roi_pct=1.99)     # kâr yetersiz
        assert not stale_tp_should_close(cfg, age_ms=5 * HOUR_MS, roi_pct=-3.0)     # zararda: dokunma

    def test_roi_long_short_ve_gecersiz_giris(self):
        # 20x: %0.1 fiyat = %2 ROI
        assert position_roi_pct(100.0, 100.1, 20, Direction.LONG) == pytest.approx(2.0)
        assert position_roi_pct(100.0, 99.9, 20, Direction.SHORT) == pytest.approx(2.0)
        assert position_roi_pct(100.0, 99.9, 20, Direction.LONG) == pytest.approx(-2.0)
        assert position_roi_pct(0.0, 99.9, 20, Direction.LONG) == 0.0
        # kaldıraç 0/None → 1x kabul (sıfıra bölme yok)
        assert position_roi_pct(100.0, 101.0, 0, Direction.LONG) == pytest.approx(1.0)


# --------------------------------------------------------------------------
# 2) Backtest harness — manage_position
# --------------------------------------------------------------------------

def _pos(direction: Direction = Direction.LONG) -> OpenPosition:
    """LONG: giriş 100, 10x, TP1 101 (%10 ROI), stop 95 (%50 ROI), BE 100.1."""
    if direction == Direction.LONG:
        tp1, tp2, stop, be = 101.0, 102.5, 95.0, 100.1
    else:
        tp1, tp2, stop, be = 99.0, 97.5, 105.0, 99.9
    return OpenPosition(
        strategy="C", symbol="BTCUSDT", direction=direction,
        stop_price=stop, entry_idx=0, entry_price=100.0, entry_time=0,
        qty_total=2.0, leverage=10, tp1_price=tp1, tp2_price=tp2,
        tp1_qty=0.8, tp2_qty=0.4, runner_qty=0.8, breakeven_price=be,
        current_stop=stop, regime="RANGE", remaining_qty=2.0,
        entry_commission_rate=0.0002, exit_commission_rate=0.0005,
    )


def _flat_candles(closes, step_ms: int = 30 * MIN_MS):
    """Her mum `step_ms` sürer; high/low kapanıştan ±0.05 (TP1/SL'e değmez)."""
    out = []
    for i, c in enumerate(closes):
        out.append(Candle(
            open_time=i * step_ms, open=c, high=c + 0.05, low=c - 0.05,
            close=c, volume=1.0, close_time=(i + 1) * step_ms,
        ))
    return out


def _cfg(hours: float = 2.0, min_roi: float = 2.0, max_hold: float = 0.0):
    return SimpleNamespace(
        scalper_stale_tp_hours=hours,
        scalper_stale_tp_min_roi_pct=min_roi,
        scalper_max_hold_hours=max_hold,
        scalper_chandelier_atr_period=14,
        scalper_chandelier_atr_mult=2.5,
    )


class TestHarness:
    def test_karda_ve_yasli_pozisyon_stale_tp_ile_kapanir(self):
        # 30 dk'lık mumlar: ilk 4 mum 2 saati doldurur; 4. mumun kapanışı
        # 100.3 → %3 ROI ≥ %2 → o mumda STALE_TP (kapanış fiyatından).
        closes = [100.4, 100.35, 100.3, 100.3, 100.9, 100.9, 100.9]
        trade = manage_position(_pos(), _flat_candles(closes), _cfg())
        assert trade.exit_reason == EXIT_REASON_STALE_TP == "STALE_TP"
        assert trade.exit_price == pytest.approx(100.3)
        assert trade.exit_time == 4 * 30 * MIN_MS
        assert trade.duration_minutes == pytest.approx(120.0)
        assert trade.pnl > 0

    def test_iki_saatten_once_kapatmaz(self):
        # 3. mum (90 dk) kârda ama genç → kapanmaz; 4. mumda (120 dk) kapanır.
        closes = [100.9, 100.9, 100.9, 100.9, 100.9]
        trade = manage_position(_pos(), _flat_candles(closes), _cfg())
        assert trade.exit_reason == "STALE_TP"
        assert trade.exit_time == 4 * 30 * MIN_MS

    def test_zararda_pozisyona_dokunmaz_eod_e_gider(self):
        closes = [99.8] * 8  # −%2 ROI, TP/SL değmez
        trade = manage_position(_pos(), _flat_candles(closes), _cfg())
        assert trade.exit_reason == "EOD"

    def test_kar_esigin_altindaysa_bekler(self):
        closes = [100.1] * 8  # %1 ROI < %2
        trade = manage_position(_pos(), _flat_candles(closes), _cfg(min_roi=2.0))
        assert trade.exit_reason == "EOD"
        # eşik %0 → aynı seri kapanır
        trade0 = manage_position(_pos(), _flat_candles(closes), _cfg(min_roi=0.0))
        assert trade0.exit_reason == "STALE_TP"

    def test_kapaliyken_hic_calismaz(self):
        closes = [100.9] * 8
        trade = manage_position(_pos(), _flat_candles(closes), _cfg(hours=0.0))
        assert trade.exit_reason == "EOD"

    def test_short_simetrik(self):
        closes = [99.7] * 6  # SHORT: %3 ROI
        trade = manage_position(_pos(Direction.SHORT), _flat_candles(closes), _cfg())
        assert trade.exit_reason == "STALE_TP"
        assert trade.exit_price == pytest.approx(99.7)
        assert trade.pnl > 0

    def test_tp1_gormus_kosucu_muaf(self):
        pos = _pos()
        # TP1 dolmuş, BE korumalı koşucu gibi kur.
        pos.tp1_filled = True
        pos.trailing_active = True
        pos.remaining_qty = 1.2
        pos.current_stop = pos.breakeven_price
        closes = [100.9] * 8  # kârda ve yaşlı ama TP1 görmüş → STALE_TP yok
        trade = manage_position(pos, _flat_candles(closes), _cfg())
        assert trade.exit_reason != "STALE_TP"

    def test_ayni_mumda_reaper_ile_cakisirsa_stale_tp_once(self):
        closes = [100.9] * 6
        trade = manage_position(_pos(), _flat_candles(closes), _cfg(hours=2.0, max_hold=2.0))
        assert trade.exit_reason == "STALE_TP"
        # Kapalıyken aynı seri REAPER ile kapanır (kontrol).
        trade_r = manage_position(_pos(), _flat_candles(closes), _cfg(hours=0.0, max_hold=2.0))
        assert trade_r.exit_reason == "REAPER"

    def test_intrabar_sl_tp_her_zaman_once(self):
        # 4. mum hem 2 saati doldurur hem TP1'e değer → TP1 dolar, STALE_TP
        # yerine trailing yolu; kapanış STALE_TP OLAMAZ.
        candles = _flat_candles([100.5, 100.5, 100.5, 100.5, 100.5, 100.5])
        c = candles[3]
        candles[3] = Candle(
            open_time=c.open_time, open=c.open, high=101.2, low=c.low,
            close=c.close, volume=c.volume, close_time=c.close_time,
        )
        trade = manage_position(_pos(), candles, _cfg())
        assert trade.exit_reason != "STALE_TP"
        assert any(leg["label"] == "TP1" for leg in trade.legs)


# --------------------------------------------------------------------------
# 3) Canlı motor — _close_stale_profitable_positions
# --------------------------------------------------------------------------

async def _quantize_identity(symbol: str, qty: float) -> float:
    return qty


def _live_position(
    *,
    age_hours: float = 3.0,
    current_price: float = 100.3,
    entry_price: float = 100.0,
    leverage: int = 10,
    side: PositionSide = PositionSide.LONG,
    direction: Direction = Direction.LONG,
    trailing_active: bool = False,
    tp1_done: bool = False,
    price_age_s: float = 0.0,
    quantity: float = 1.5,
):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        position=SimpleNamespace(
            side=side, quantity=quantity, opened_at=now - timedelta(hours=age_hours),
            entry_price=entry_price, current_price=current_price, leverage=leverage,
        ),
        signal=SimpleNamespace(direction=direction),
        trailing_active=trailing_active,
        tp1_done=tp1_done,
        stale_tp_close_at=None,
        reaper_close_at=None,
        price_ts=time.monotonic() - price_age_s,
    )


def _engine(positions: dict, *, hours: float = 2.0, min_roi: float = 2.0,
            max_hold: float = 8.0, submit=None):
    from src.strategies.scalper.engine import ScalperEngine

    sent: list = []

    async def _default_submit(symbol, close_side, qty):
        sent.append((symbol, close_side, qty))

    engine = ScalperEngine.__new__(ScalperEngine)
    engine.cfg = SimpleNamespace(
        scalper_stale_tp_hours=hours,
        scalper_stale_tp_min_roi_pct=min_roi,
        scalper_max_hold_hours=max_hold,
        scalper_safety_interval_seconds=8.0,
    )
    engine.logger = app_logger
    engine.exits = SimpleNamespace(
        tracked_symbols=lambda: set(positions.keys()),
        _positions=positions,
    )
    engine.client = SimpleNamespace(quantize_quantity=_quantize_identity)
    engine._submit_reduce_only_market_close = submit or _default_submit
    return engine, sent


class TestEngine:
    @pytest.mark.asyncio
    async def test_karda_yasli_long_sell_reduce_only_gonderir_ve_damgalar(self):
        sp = _live_position()  # 3 sa, %3 ROI
        engine, sent = _engine({"BTCUSDT": sp})
        await engine._close_stale_profitable_positions()
        assert sent == [("BTCUSDT", "SELL", 1.5)]
        assert sp.stale_tp_close_at  # emir gittikten sonra damga

    @pytest.mark.asyncio
    async def test_short_icin_buy_gonderir(self):
        sp = _live_position(side=PositionSide.SHORT, direction=Direction.SHORT, current_price=99.7)
        engine, sent = _engine({"ETHUSDT": sp})
        await engine._close_stale_profitable_positions()
        assert sent == [("ETHUSDT", "BUY", 1.5)]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "kwargs",
        [
            dict(age_hours=1.5),                       # genç
            dict(current_price=100.1),                 # %1 ROI < %2
            dict(current_price=99.5),                  # zararda
            dict(trailing_active=True),                # TP1 görmüş koşucu
            dict(tp1_done=True),                       # TP1 doğrulanmış
            dict(price_age_s=60.0),                    # bayat fiyat (8 sn × 3 = 24 sn sınırı)
            dict(current_price=0.0),                   # fiyat yok
        ],
    )
    async def test_kosul_saglanmayinca_emir_yok_damga_yok(self, kwargs):
        sp = _live_position(**kwargs)
        engine, sent = _engine({"BTCUSDT": sp})
        await engine._close_stale_profitable_positions()
        assert sent == []
        assert sp.stale_tp_close_at is None

    @pytest.mark.asyncio
    async def test_kapaliyken_hic_calismaz(self):
        sp = _live_position()
        engine, sent = _engine({"BTCUSDT": sp}, hours=0.0)
        await engine._close_stale_profitable_positions()
        assert sent == [] and sp.stale_tp_close_at is None

    @pytest.mark.asyncio
    async def test_tur_basina_tek_kapanis(self):
        a, b = _live_position(), _live_position()
        engine, sent = _engine({"AAAUSDT": a, "BBBUSDT": b})
        await engine._close_stale_profitable_positions()
        assert len(sent) == 1
        assert sum(1 for sp in (a, b) if sp.stale_tp_close_at) == 1
        await engine._close_stale_profitable_positions()  # sonraki tur: diğeri
        assert len(sent) == 2
        assert all(sp.stale_tp_close_at for sp in (a, b))
        await engine._close_stale_profitable_positions()  # damgalılara tekrar emir yok
        assert len(sent) == 2

    @pytest.mark.asyncio
    async def test_emir_hata_verirse_damga_konmaz(self):
        async def _boom(symbol, close_side, qty):
            raise RuntimeError("borsa reddetti")

        sp = _live_position()
        engine, _ = _engine({"BTCUSDT": sp}, submit=_boom)
        await engine._close_stale_profitable_positions()  # istisna yutulur
        assert sp.stale_tp_close_at is None

    @pytest.mark.asyncio
    async def test_reaper_stale_damgali_pozisyona_ikinci_emir_gondermez(self):
        sp = _live_position(age_hours=9.0)  # reaper yaşında VE kârda
        engine, sent = _engine({"BTCUSDT": sp}, max_hold=8.0)
        await engine._close_stale_profitable_positions()
        assert len(sent) == 1
        await engine._reap_aged_positions()
        assert len(sent) == 1  # ikinci reduce-only MARKET YOK
        assert sp.reaper_close_at is None


# --------------------------------------------------------------------------
# 4) Etiket çıkarımı ve aile eşlemeleri
# --------------------------------------------------------------------------

def _label_position(**overrides):
    base = dict(
        trailing_active=False,
        plan=SimpleNamespace(initial_stop=95.0, tp1_price=101.0),
        reaper_close_at=None,
        stale_tp_close_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class TestLabel:
    def test_damga_varsa_stale_tp(self):
        sp = _label_position(stale_tp_close_at="2026-09-03T12:00:00+00:00")
        assert ExitManager._infer_exit_reason(sp, exit_price=100.3, realized_pnl=0.5) == "STALE_TP"

    def test_stale_damgasi_reaper_damgasindan_once(self):
        sp = _label_position(
            stale_tp_close_at="2026-09-03T12:00:00+00:00",
            reaper_close_at="2026-09-03T12:00:08+00:00",
        )
        assert ExitManager._infer_exit_reason(sp, exit_price=100.3, realized_pnl=0.5) == "STALE_TP"

    def test_damga_yoksa_eski_davranis(self):
        sp = _label_position()
        # Çıkış girişe (TP tarafına) yakın ve kârda → eski etiket TP_LADDER
        assert ExitManager._infer_exit_reason(sp, exit_price=100.3, realized_pnl=0.5) == "TP_LADDER"

    def test_eski_cooldown_cikarimi_damgayi_okumaz(self):
        sp = _label_position(stale_tp_close_at="2026-09-03T12:00:00+00:00")
        legacy = ExitManager._infer_exit_reason_legacy(sp, exit_price=100.3, realized_pnl=0.5)
        assert legacy in ("TP_LADDER", "SL")
        assert legacy != "STALE_TP"

    def test_aile_eslemeleri_kendi_ailesi(self):
        assert lr.exit_reason_family("STALE_TP") == "STALE_TP"
        assert fx.exit_reason_family("stale_tp") == "STALE_TP"
        assert "STALE_TP" in lr.EXIT_REASON_ORDER
        # Eski eşlemeler bozulmadı
        assert lr.exit_reason_family("REAPER") == "REAPER"
        assert lr.exit_reason_family("TRAIL_MARKET") == "TRAIL"
