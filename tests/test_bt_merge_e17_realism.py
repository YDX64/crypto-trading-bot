"""D41 (gerçekçilik paketi) x E17 (ölçüm eklentileri) birleşme sözleşmesi.

İki iş kolu `backtest.py`'de buluşur:
  * D41: gerçekçi model VARSAYILAN (gap_fills, exit_slippage, intrabar_path,
    maker_*, funding, liquidation, daily_latch); `--legacy-model` eski sayılar.
  * E17/H1-H3: yalnız ölçüm — `diag` sayaçları, `same_bar_order`, H1 sessiz-red
    sayaçları, `check_timeframe_roles`.

Bu dosya birleşmenin kurallarını sabitler:
  S1  eski modelde (tüm gerçekçilik kapalı) E17 sayaçları elle hesaplananla aynı;
  S3  diag HİÇBİR modelde işlemi değiştirmez; sayaçlar mum-içi DEĞME olaylarıdır
      (gerçekçi yol mantığının sonucundan bağımsız);
  S4  `optimistic` gerçekçi modelde de üç gevşetmeyi uygular ((i) TP1 mumunda BE
      stopu yok, (ii) TP2 mumunda taban yok, (iii) dolum mumunda çıkış adımı yok
      — likidasyon dahil) ve funding dolum mumunda yine tahsil edilir;
  S7  rapor: gerçekçilik bloğu her zaman, `diagnostics`/`sim_options` yalnız
      bayrakla.
Sentetik mumlar, komisyon ve kayma 0; hiçbir gerçek piyasa verisi/ağ yok.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

import pytest

import src.strategies.scalper.backtest as bt
from src.core.config import settings
from src.strategies.scalper.backtest import (
    DIAGNOSTIC_KEYS,
    LEGACY_EXIT_REALISM,
    REALISM_FEATURES,
    ExitRealism,
    OpenPosition,
    build_maker_realism,
    manage_position,
    new_diagnostics,
    open_position,
)
from src.strategies.scalper.funding import FUNDING_INTERVAL_MS, FundingSchedule
from src.strategies.scalper.types import Candle, Direction, Regime, ScalpSignal

# REAPER/STALE_TP yaş kesmeleri ve yapı çıkışı testi etkilemesin.
CFG = settings.model_copy(update={
    "scalper_max_hold_hours": 0.0, "scalper_stale_tp_hours": 0.0,
    "scalper_structure_exit": "off",
})

T0 = FUNDING_INTERVAL_MS   # 08:00 UTC, 1970-01-01 (funding anı, epoch'a hizalı)
BAR = 300_000              # 5 dk

REALISTIC = ExitRealism(gap_fills=True, exit_slippage_rate=0.0, intrabar_path=True)
LEGACY = LEGACY_EXIT_REALISM

ALL_REALISM_OFF = REALISM_FEATURES   # `disabled_realism` için "hepsi kapalı"


@pytest.fixture(autouse=True)
def _isolate_cwd(tmp_path, monkeypatch):
    """CLI rapor yazımı (`logs/`) test dizinine gitsin; depoyu kirletme."""
    monkeypatch.chdir(tmp_path)


def _c(i: int, o: float, h: float, l: float, c: float) -> Candle:
    t = T0 + i * BAR
    return Candle(open_time=t, open=o, high=h, low=l, close=c,
                  volume=1.0, close_time=t + BAR - 1)


def _pos(realism: ExitRealism, *, stop: float = 90.0, liq_price: Optional[float] = None,
         schedule: Optional[FundingSchedule] = None) -> OpenPosition:
    """LONG: giriş 100, stop 90, TP1 110 (1.0), TP2 120 (0.5), runner 0.5, BE 100.5.
    entry_time dolum mumundan BİR mum önce → funding anı T0 dolum mumunda vadeli."""
    return OpenPosition(
        strategy="C", symbol="BTCUSDT", direction=Direction.LONG,
        stop_price=stop, entry_idx=0, entry_price=100.0, entry_time=T0 - BAR,
        qty_total=2.0, leverage=10, tp1_price=110.0, tp2_price=120.0,
        tp1_qty=1.0, tp2_qty=0.5, runner_qty=0.5, breakeven_price=100.5,
        current_stop=stop, regime="UP",
        entry_commission_rate=0.0, exit_commission_rate=0.0,
        exit_realism=realism, liq_price=liq_price, funding_schedule=schedule,
    )


F = _c(0, 100.0, 100.4, 99.6, 100.0)   # sakin dolum mumu: hiçbir seviyeye değmez

# Her senaryo: (ad, mum listesi). Dolum mumu = idx 0.
SCEN_A = ("A: SL ve TP1 AYNI mumda (TP1 öncesi)", [F, _c(1, 100.0, 111.0, 89.0, 100.0)])
SCEN_B = ("B: TP1 mumunda BE stopuna da DEĞİYOR (yükselen mum)", [F, _c(1, 105.0, 111.0, 100.0, 105.0)])
SCEN_C = ("C: TP1+TP2 aynı mumda, TP1 tabanına da DEĞİYOR (yükselen mum)", [F, _c(1, 105.0, 121.0, 105.0, 120.0)])
SCEN_D = ("D: dolum mumunda SL", [_c(0, 100.0, 100.5, 89.0, 90.0)])
SCENARIOS = [SCEN_A, SCEN_B, SCEN_C, SCEN_D]


def _run(realism: ExitRealism, candles: List[Candle], *, diag=None,
         order: str = "pessimistic", **pos_kw):
    pos = _pos(realism, **pos_kw)
    trade = manage_position(pos, candles, CFG, diag=diag, same_bar_order=order)
    return pos, trade


def _sum_diag(realism: ExitRealism, order: str) -> Dict[str, int]:
    diag = new_diagnostics()
    for _name, candles in SCENARIOS:
        _run(realism, candles, diag=diag, order=order)
    return diag


# --------------------------------------------------------------------------
# S1 — eski modelde sayaçlar elle hesaplananla aynı
# --------------------------------------------------------------------------

def test_legacy_model_counters_match_hand_computed_case():
    """Eski model = `LEGACY_EXIT_REALISM` (alan varsayılanları). Dört mini
    senaryonun elle hesabı:
      A: SL+TP1 aynı mum → sl_tp1_same_bar 1                          (SL'de kapanır)
      B: TP1 + BE değmesi → tp1_fills 1, tp1_then_stop_same_bar 1      (BE'de TRAIL)
      C: TP1+TP2 + taban değmesi → tp1_fills 1, tp2_then_floor 1       (tabanda TRAIL)
      D: dolum mumunda SL → fill_bar_exit 1
    fill_bar_total = 4 (dört işlem)."""
    d = _sum_diag(LEGACY, "pessimistic")
    assert d == {
        "tp1_fills": 2, "sl_tp1_same_bar": 1, "tp1_then_stop_same_bar": 1,
        "tp2_then_floor_same_bar": 1, "fill_bar_exit": 1, "fill_bar_total": 4,
    }
    assert list(d) == list(DIAGNOSTIC_KEYS)
    reasons = {name[0]: _run(LEGACY, candles)[1].exit_reason for name, candles in SCENARIOS}
    assert reasons == {"A": "SL", "B": "TRAIL", "C": "TRAIL", "D": "SL"}
    # B: BE'de (100.5), C: TP1 tabanında (110) kapanır.
    assert _run(LEGACY, SCEN_B[1])[1].exit_price == pytest.approx(100.5)
    assert _run(LEGACY, SCEN_C[1])[1].exit_price == pytest.approx(110.0)


def test_legacy_model_optimistic_counters_and_relaxations_hand_computed():
    """Aynı dört senaryo `optimistic` ile (eski modelde, origin/main davranışı):
    B (i) ve C (ii) açık kalır, D (iii) dolum mumunda çıkış yok → hepsi EOD;
    A (TP1 öncesi eşitlik) DEĞİŞMEZ. Sayaçlar DEĞME olayı olduğundan aynı,
    yalnız fill_bar_exit 0 (D artık dolum mumunda kapanmıyor)."""
    d = _sum_diag(LEGACY, "optimistic")
    assert d == {
        "tp1_fills": 2, "sl_tp1_same_bar": 1, "tp1_then_stop_same_bar": 1,
        "tp2_then_floor_same_bar": 1, "fill_bar_exit": 0, "fill_bar_total": 4,
    }
    reasons = {name[0]: _run(LEGACY, candles, order="optimistic")[1].exit_reason
               for name, candles in SCENARIOS}
    assert reasons == {"A": "SL", "B": "EOD", "C": "EOD", "D": "EOD"}


# --------------------------------------------------------------------------
# S3 — diag işlemi değiştirmez; sayaçlar DEĞME olayı (yol mantığından bağımsız)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("order", ["pessimistic", "optimistic"])
def test_realistic_diag_on_vs_off_gives_identical_trades(order):
    for name, candles in SCENARIOS:
        _p_off, t_off = _run(REALISTIC, candles, diag=None, order=order)
        _p_on, t_on = _run(REALISTIC, candles, diag=new_diagnostics(), order=order)
        assert t_on.to_dict() == t_off.to_dict(), name


def test_realistic_counters_count_touches_independent_of_path_resolution():
    """Gerçekçi modelde yol mantığı B ve C'yi FARKLI çözer (BE/taban TP'den
    ÖNCE değdi → stop yok; pozisyon açık kalır), ama sayaçlar aynı DEĞME
    olaylarını eski modelle aynı noktalarda sayar."""
    legacy = _sum_diag(LEGACY, "pessimistic")
    realistic = _sum_diag(REALISTIC, "pessimistic")
    assert realistic == legacy
    # ... ve gerçekçi model işlemi gerçekten farklı çözdü (test ayırt edici):
    assert _run(LEGACY, SCEN_B[1])[1].exit_reason == "TRAIL"
    assert _run(REALISTIC, SCEN_B[1])[1].exit_reason == "EOD"
    assert _run(LEGACY, SCEN_C[1])[1].exit_reason == "TRAIL"
    assert _run(REALISTIC, SCEN_C[1])[1].exit_reason == "EOD"


# --------------------------------------------------------------------------
# S4 — optimistic gerçekçi modelin ÜSTÜNE biner
# --------------------------------------------------------------------------

def test_optimistic_i_no_be_stop_on_tp1_bar_in_realistic_model():
    """Düşen mum (O→H→L→C): TP1'e yükselirken değer, sonra BE'nin ALTINA iner.
    pessimistic: yol BE'yi TP1'den SONRA kestiği için TRAIL @100.5 (bu mumda).
    optimistic (i): TP1 mumunda BE değerlendirilmez → açık kalır."""
    candles = [F, _c(1, 105.0, 111.0, 99.0, 100.0)]
    diag_p, diag_o = new_diagnostics(), new_diagnostics()
    _pp, t_p = _run(REALISTIC, candles, diag=diag_p, order="pessimistic")
    _po, t_o = _run(REALISTIC, candles, diag=diag_o, order="optimistic")
    assert (t_p.exit_reason, t_p.exit_idx, t_p.exit_price) == ("TRAIL", 1, pytest.approx(100.5))
    assert t_o.exit_reason == "EOD" and t_o.exit_idx == 1
    assert [leg["label"] for leg in t_o.legs] == ["TP1", "EOD"]
    # Sayaç iki sırada da aynı DEĞME olayı:
    assert diag_p["tp1_then_stop_same_bar"] == diag_o["tp1_then_stop_same_bar"] == 1


def test_optimistic_ii_no_floor_check_on_tp2_bar_in_realistic_model():
    """TP2 mumu (düşen): TP2'ye yükselirken değer, sonra TP1 tabanının (110)
    altına iner. pessimistic: taban TP2'den SONRA değdi → TRAIL @110.
    optimistic (ii): TP2 mumunda taban kontrolü yok → açık kalır (EOD @112)."""
    candles = [
        F,
        _c(1, 105.0, 111.0, 105.0, 111.0),   # TP1 mumu: yükselen, BE'ye değmez
        _c(2, 115.0, 121.0, 109.0, 112.0),   # TP2 mumu: düşen (O→H→L→C)
    ]
    diag_p, diag_o = new_diagnostics(), new_diagnostics()
    _pp, t_p = _run(REALISTIC, candles, diag=diag_p, order="pessimistic")
    _po, t_o = _run(REALISTIC, candles, diag=diag_o, order="optimistic")
    assert (t_p.exit_reason, t_p.exit_idx, t_p.exit_price) == ("TRAIL", 2, pytest.approx(110.0))
    assert (t_o.exit_reason, t_o.exit_idx, t_o.exit_price) == ("EOD", 2, pytest.approx(112.0))
    assert diag_p["tp2_then_floor_same_bar"] == diag_o["tp2_then_floor_same_bar"] == 1


def test_optimistic_iii_fill_bar_has_no_exit_step_including_liquidation():
    """(iii): dolum mumunda HİÇBİR çıkış adımı yok — SL da, likidasyon da.
    Sonraki mumdan itibaren normal. fill_bar_exit yalnız pessimistic'te sayılır."""
    # (a) dolum mumunda SL
    d_p, d_o = new_diagnostics(), new_diagnostics()
    _pp, t_p = _run(REALISTIC, SCEN_D[1], diag=d_p, order="pessimistic")
    _po, t_o = _run(REALISTIC, SCEN_D[1], diag=d_o, order="optimistic")
    assert (t_p.exit_reason, t_p.exit_idx) == ("SL", 0)
    assert t_o.exit_reason == "EOD"
    assert d_p["fill_bar_exit"] == 1 and d_o["fill_bar_exit"] == 0
    assert d_p["fill_bar_total"] == d_o["fill_bar_total"] == 1

    # (b) dolum mumunda LİKİDASYON (stop likidasyonun ötesinde, low likidasyona değer)
    candles = [_c(0, 100.0, 100.5, 90.0, 95.0), _c(1, 95.0, 96.0, 94.0, 95.0)]
    _p1, lp = _run(REALISTIC, candles, order="pessimistic", stop=85.0, liq_price=91.0)
    _p2, lo = _run(REALISTIC, candles, order="optimistic", stop=85.0, liq_price=91.0)
    assert (lp.exit_reason, lp.exit_idx) == ("LIQUIDATION", 0)
    assert lo.exit_reason == "EOD" and lo.exit_idx == 1
    # Sonraki mumda likidasyon HÂLÂ değerlendirilir (yalnız dolum mumu atlanır):
    candles2 = [_c(0, 100.0, 100.4, 99.6, 100.0), _c(1, 95.0, 96.0, 90.0, 94.0)]
    _p3, l2 = _run(REALISTIC, candles2, order="optimistic", stop=85.0, liq_price=91.0)
    assert (l2.exit_reason, l2.exit_idx) == ("LIQUIDATION", 1)


def test_optimistic_still_charges_funding_on_the_fill_bar():
    """Funding zamana bağlıdır: dolum mumunda vadeli T0 anı optimistic (iii)
    altında da tahsil edilir (çıkış adımları atlanırken)."""
    sched = FundingSchedule("BTCUSDT", (), fallback_rate=0.0001)
    candles = [_c(0, 100.0, 100.4, 99.6, 100.0), _c(1, 100.0, 100.4, 99.6, 100.0)]
    p_p, t_p = _run(REALISTIC, candles, order="pessimistic", schedule=sched)
    p_o, t_o = _run(REALISTIC, candles, order="optimistic", schedule=sched)
    # LONG, oran +0.0001: 2.0 * 100 * 0.0001 = 0.02 ödenir (dolum mumu açılışında).
    for pos, trade in ((p_p, t_p), (p_o, t_o)):
        assert trade.funding == pytest.approx(0.02)
        assert trade.funding_settlements == 1
        assert pos.funding_events[0]["time"] == T0
        assert pos.funding_events[0]["price"] == pytest.approx(100.0)
    # Dolum mumunda likidasyon adımı atlansa da funding ÖNCE işlenir:
    liq = [_c(0, 100.0, 100.5, 90.0, 95.0), _c(1, 95.0, 96.0, 94.0, 95.0)]
    pl, tl = _run(REALISTIC, liq, order="pessimistic", schedule=sched, stop=85.0, liq_price=91.0)
    po, to = _run(REALISTIC, liq, order="optimistic", schedule=sched, stop=85.0, liq_price=91.0)
    assert tl.exit_reason == "LIQUIDATION" and to.exit_reason == "EOD"
    assert tl.funding == pytest.approx(0.02) and to.funding == pytest.approx(0.02)


# --------------------------------------------------------------------------
# Cilalama: likidasyon mumu sayaç üretmez, optimistic (iii) equity işareti,
# TP2 tüm kalanı tüketirse taban sayacı yok
# --------------------------------------------------------------------------

@pytest.mark.parametrize("order", ["pessimistic", "optimistic"])
def test_liquidation_bar_yields_no_touch_counters(order):
    """Karar: likidasyon ön kontrolü (A3) mumu bitirirse o mum için HİÇBİR
    DEĞME sayacı üretilmez. Tek mum stopa (80) VE TP1'e (110) değiyor VE
    likidasyon fiyatına (85) iniyor; stop likidasyonun ötesinde olduğundan
    LIQUIDATION kazanır. Sayaçlar önce işlenseydi `sl_tp1_same_bar` 1 olurdu."""
    diag = new_diagnostics()
    candles = [F, _c(1, 100.0, 111.0, 79.0, 90.0)]
    pos, trade = _run(REALISTIC, candles, diag=diag, order=order,
                      stop=80.0, liq_price=85.0)
    assert (trade.exit_reason, trade.exit_idx) == ("LIQUIDATION", 1)
    assert [leg["label"] for leg in trade.legs] == ["LIQUIDATION"]   # TP1 dolumu yok
    assert trade.legs[0]["pnl"] == pytest.approx(-20.0)              # 2.0 * 100 / 10
    assert diag == {
        "tp1_fills": 0, "sl_tp1_same_bar": 0, "tp1_then_stop_same_bar": 0,
        "tp2_then_floor_same_bar": 0, "fill_bar_exit": 0, "fill_bar_total": 1,
    }


def test_optimistic_iii_still_marks_equity_on_the_fill_bar():
    """(iii) dolum mumunda çıkış adımlarını atlar ama `_mark_equity` YİNE
    işler: equity serisi her mumu (dolum mumu dahil) kapsar ve dolum mumunun
    işareti o mumun kapanışındaki gerçekleşmemiş K/Z'dir."""
    candles = [_c(0, 100.0, 101.5, 99.6, 101.0), _c(1, 101.0, 101.5, 100.5, 101.0)]
    pos, _trade = _run(REALISTIC, candles, order="optimistic")
    assert [t for t, _v in pos.equity_marks] == [c.close_time for c in candles]
    assert [t for t, _v in pos.equity_marks_adverse] == [c.close_time for c in candles]
    # Dolum mumu işareti: 2.0 adet * (101 - 100), komisyon/funding 0.
    assert pos.equity_marks[0][1] == pytest.approx(2.0)
    # Dolum mumunda low 99.6 → aleyhte uç: 2.0 * (99.6 - 100) = -0.8.
    assert pos.equity_marks_adverse[0][1] == pytest.approx(-0.8)


def test_tp2_then_floor_not_counted_when_tp2_consumes_whole_remaining_qty():
    """Runner yok (TP1 1.0 + TP2 1.0 = tüm 2.0): TP2 mumu tabana (110) değse de
    kalan miktar 0 olduğundan 'taban değmesi' ANLAMSIZ → sayaç 0. Kontrol
    olarak runner'lı pozisyonda aynı mum sayacı 1 yapar."""
    candles = [
        F,
        _c(1, 105.0, 111.0, 105.0, 111.0),   # TP1 mumu: yükselen, BE'ye değmez
        _c(2, 115.0, 121.0, 109.0, 112.0),   # TP2 mumu: düşen, tabana (110) iner
    ]
    for realism in (LEGACY, REALISTIC):
        diag = new_diagnostics()
        pos = _pos(realism)
        pos.tp2_qty, pos.runner_qty = 1.0, 0.0
        trade = manage_position(pos, candles, CFG, diag=diag, same_bar_order="pessimistic")
        assert pos.remaining_qty == pytest.approx(0.0)
        assert [leg["label"] for leg in trade.legs] == ["TP1", "TP2"]
        assert diag["tp1_fills"] == 1
        assert diag["tp2_then_floor_same_bar"] == 0
        # Kontrol: runner varken aynı mum sayacı artırır.
        diag_runner = new_diagnostics()
        _run(realism, candles, diag=diag_runner)
        assert diag_runner["tp2_then_floor_same_bar"] == 1


# --------------------------------------------------------------------------
# S3 uçtan uca — golden fixture, VARSAYILAN (gerçekçi) model
# --------------------------------------------------------------------------

async def _golden_run(monkeypatch, **kwargs):
    from test_golden_backtest import (  # type: ignore
        _END, _FIXTURE_DIR, _START, _SYMBOLS, _GoldenCfg,
        _NetworkForbiddenKlineFetcher, _apply_golden_settings,
    )

    _apply_golden_settings(monkeypatch)
    monkeypatch.setattr(bt, "KlineFetcher", _NetworkForbiddenKlineFetcher)
    return await bt.run_backtest(
        days=3, symbols=_SYMBOLS, strategy_names="C", cfg=_GoldenCfg(),
        missed_counter={}, start_time_ms=bt._parse_utc_date(_START),
        end_time_ms=bt._parse_utc_date(_END), cache_dir=_FIXTURE_DIR,
        refresh=False, base_url="https://network-must-not-be-used.invalid",
        **kwargs,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("order", ["pessimistic", "optimistic"])
async def test_golden_default_model_diag_never_changes_a_trade(monkeypatch, order):
    off = await _golden_run(monkeypatch, same_bar_order=order)
    diag = new_diagnostics()
    on = await _golden_run(monkeypatch, same_bar_order=order, diag=diag)
    assert [t.to_dict() for t in on] == [t.to_dict() for t in off]
    assert diag["fill_bar_total"] == 2 and diag["tp1_fills"] == 2


@pytest.mark.asyncio
async def test_golden_counters_are_model_independent_touch_counts(monkeypatch):
    """Aynı fixture, iki model: işlemler FARKLI (23.79 vs 25.17), sayaçlar AYNI.
    `tp1_then_stop_same_bar` = 1: bir TP1 mumunda BE'ye tam-mum değme var;
    gerçekçi yol mantığı o mumu 'stop yok' çözse de sayaç 1 kalır."""
    d_real, d_legacy = new_diagnostics(), new_diagnostics()
    real = await _golden_run(monkeypatch, diag=d_real)
    legacy = await _golden_run(monkeypatch, diag=d_legacy, disabled_realism=ALL_REALISM_OFF)
    assert round(sum(t.pnl for t in real), 2) == 25.17
    assert round(sum(t.pnl for t in legacy), 2) == 23.79
    assert d_real == d_legacy
    assert d_real["tp1_then_stop_same_bar"] == 1


# --------------------------------------------------------------------------
# S6 / S7 — CLI bayrak birleşimi ve rapor şekli (golden fixture, AĞSIZ)
# --------------------------------------------------------------------------

class TestCliAndReportUnion:
    def _args(self, monkeypatch, tmp_path, **overrides):
        from test_backtest_measurement import TestCliWiring  # type: ignore

        return TestCliWiring()._args(monkeypatch, tmp_path, **overrides)

    @staticmethod
    def _report(tmp_path):
        reports = sorted(Path(tmp_path, "logs").glob("backtest_*.json"))
        assert reports, "JSON rapor yazılmadı"
        return json.loads(reports[-1].read_text(encoding="utf-8"))

    def test_parser_accepts_both_flag_families(self):
        args = bt._build_arg_parser().parse_args([
            "--legacy-model", "--disable-realism", "funding",
            "--exit-slippage-pct", "0.02", "--maker-trade-through-pct", "0.01",
            "--funding-fallback-pct", "0.01",
            "--diagnostics", "--same-bar-order", "optimistic",
        ])
        assert args.legacy_model is True and args.diagnostics is True
        assert args.same_bar_order == "optimistic"
        assert bt.resolve_disabled_realism(args) == frozenset(REALISM_FEATURES)

    @pytest.mark.asyncio
    async def test_default_report_has_realism_block_but_no_e17_keys(self, monkeypatch, tmp_path):
        await bt.main_async(self._args(monkeypatch, tmp_path))
        report = self._report(tmp_path)
        assert report["provenance"]["realism"]["disabled"] == []
        assert "diagnostics" not in report
        assert "sim_options" not in report["provenance"]
        # Gerçekçi varsayılan golden: 25.17 (eski model 23.79).
        assert round(report["overall"]["total_pnl"], 2) == 25.17

    @pytest.mark.asyncio
    async def test_flags_add_e17_keys_and_keep_realism_block(self, monkeypatch, tmp_path):
        await bt.main_async(self._args(
            monkeypatch, tmp_path, diagnostics=None, same_bar_order="optimistic",
        ))
        report = self._report(tmp_path)
        assert report["provenance"]["realism"]["disabled"] == []
        # Gerçekçi model + optimistic: sim_options uyarı anahtarlarını da taşır.
        assert report["provenance"]["sim_options"] == {
            "same_bar_order": "optimistic", "diagnostics": True,
            "optimistic_is_upper_bound": False,
            "note": bt.OPTIMISTIC_CAVEAT_NOTE,
        }
        assert list(report["diagnostics"]) == list(DIAGNOSTIC_KEYS)

    @pytest.mark.asyncio
    async def test_legacy_model_with_flags_keeps_both_blocks(self, monkeypatch, tmp_path):
        await bt.main_async(self._args(
            monkeypatch, tmp_path, legacy_model=None, diagnostics=None,
        ))
        report = self._report(tmp_path)
        assert report["provenance"]["realism"]["enabled"] == []
        assert report["provenance"]["sim_options"]["diagnostics"] is True
        assert round(report["overall"]["total_pnl"], 2) == 23.79

    @pytest.mark.asyncio
    async def test_permutations_still_need_pessimistic_in_both_models(self, monkeypatch, tmp_path):
        for extra in ({}, {"legacy_model": None}):
            with pytest.raises(ValueError, match="permutations"):
                await bt.main_async(self._args(
                    monkeypatch, tmp_path, same_bar_order="optimistic",
                    permutations=2, permutation_seed=7,
                    permutation_metrics="total_pnl", **extra,
                ))


    # ---- optimistic uyarısı: gerçekçilik açıkken ÜST SINIR DEĞİL ----

    @staticmethod
    def _capture_logs():
        from src.core.logger import app_logger

        messages: List[str] = []
        sink_id = app_logger.add(lambda m: messages.append(str(m)),
                                 level="DEBUG", format="{message}")
        return app_logger, sink_id, messages

    async def _run_cli(self, monkeypatch, tmp_path, **overrides):
        app_logger, sink_id, messages = self._capture_logs()
        try:
            await bt.main_async(self._args(monkeypatch, tmp_path, **overrides))
        finally:
            app_logger.remove(sink_id)
        warnings = [m for m in messages if bt.OPTIMISTIC_CAVEAT_NOTE in m]
        return self._report(tmp_path), warnings

    @pytest.mark.asyncio
    async def test_caveat_present_for_realistic_optimistic_with_one_warning_line(
        self, monkeypatch, tmp_path,
    ):
        report, warnings = await self._run_cli(
            monkeypatch, tmp_path, same_bar_order="optimistic",
        )
        sim = report["provenance"]["sim_options"]
        assert sim["optimistic_is_upper_bound"] is False
        assert "ÜST SINIR DEĞİL" in sim["note"]
        assert sim["same_bar_order"] == "optimistic" and sim["diagnostics"] is False
        assert len(warnings) == 1

    @pytest.mark.asyncio
    async def test_caveat_absent_for_legacy_optimistic_origin_main_parity(
        self, monkeypatch, tmp_path,
    ):
        report, warnings = await self._run_cli(
            monkeypatch, tmp_path, legacy_model=None, same_bar_order="optimistic",
        )
        assert report["provenance"]["sim_options"] == {
            "same_bar_order": "optimistic", "diagnostics": False,
        }
        assert warnings == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("extra", [{}, {"legacy_model": None}])
    async def test_caveat_absent_for_pessimistic_in_both_models(
        self, monkeypatch, tmp_path, extra,
    ):
        report, warnings = await self._run_cli(
            monkeypatch, tmp_path, same_bar_order="pessimistic", **extra,
        )
        assert report["provenance"]["sim_options"] == {
            "same_bar_order": "pessimistic", "diagnostics": False,
        }
        assert warnings == []

    @pytest.mark.asyncio
    async def test_caveat_follows_the_three_features_not_other_realism(
        self, monkeypatch, tmp_path,
    ):
        # Üçü de kapalı, diğer gerçekçilik açık → uyarı YOK.
        report, warnings = await self._run_cli(
            monkeypatch, tmp_path, same_bar_order="optimistic",
            disable_realism="gap_fills,intrabar_path,maker_fill_bar",
        )
        assert "optimistic_is_upper_bound" not in report["provenance"]["sim_options"]
        assert warnings == []
        # Biri açık (maker_fill_bar) → uyarı VAR.
        report, warnings = await self._run_cli(
            monkeypatch, tmp_path, same_bar_order="optimistic",
            disable_realism="gap_fills,intrabar_path",
        )
        assert report["provenance"]["sim_options"]["optimistic_is_upper_bound"] is False
        assert len(warnings) == 1

    def test_optimistic_caveat_applies_matrix(self):
        every = frozenset(REALISM_FEATURES)
        assert bt.optimistic_caveat_applies("optimistic", frozenset()) is True
        assert bt.optimistic_caveat_applies("optimistic", every) is False
        assert bt.optimistic_caveat_applies("pessimistic", frozenset()) is False
        for keep in bt.OPTIMISTIC_CAVEAT_FEATURES:
            assert bt.optimistic_caveat_applies("optimistic", every - {keep}) is True

    def test_same_bar_order_help_says_optimistic_is_not_an_upper_bound(self):
        parser = bt._build_arg_parser()
        action = next(a for a in parser._actions if "--same-bar-order" in a.option_strings)
        helptext = " ".join(action.help.split())
        assert "ÜST SINIR DEĞİL" in helptext
        for name in ("gap_fills", "intrabar_path", "maker_fill_bar"):
            assert name in helptext
        assert "--disable-realism intrabar_path" in helptext


# --------------------------------------------------------------------------
# H1 + H3 + maker gerçekçiliği birlikte
# --------------------------------------------------------------------------

class _MakerCfg:
    scalper_taker_fee_pct = 0.05
    scalper_maker_fee_pct = 0.02
    scalper_slippage_rate = 0.0002
    scalper_min_stop_pct = 0.15
    scalper_max_stop_pct = 3.0
    scalper_min_rr = 0.0
    scalper_entry_mode = "maker"
    scalper_maker_fill_timeout_candles = 3
    scalper_risk_percentage = 2.0
    scalper_leverage = 20
    scalper_max_margin_pct = 10.0
    scalper_tp1_roi = 10.0
    scalper_tp2_roi = 25.0
    scalper_tp1_fraction = 0.40
    scalper_tp2_fraction = 0.20
    scalper_breakeven_buffer_pct = 0.05


def _signal(entry=100.0, stop=99.0) -> ScalpSignal:
    return ScalpSignal(
        strategy="C", symbol="BTCUSDT", direction=Direction.LONG,
        entry_price=entry, stop_price=stop, reason="test",
        regime=Regime.UP, atr_5m=1.0, score=1.0,
    )


def _rising(n=6) -> List[Candle]:
    """Yükselen mumlar: limit (sinyal kapanışı) hiç ziyaret edilmez."""
    out = []
    for i in range(n):
        p = 100.0 + i
        out.append(Candle(open_time=i * 1000, open=p, high=p + 0.5, low=p - 0.5,
                          close=p, volume=1.0, close_time=i * 1000 + 999))
    return out


class TestH1H3WithMakerRealism:
    def test_h1_stop_band_reject_counts_and_places_no_maker_order(self):
        mc: Dict[str, int] = {}
        ms: Dict[str, int] = {}
        assert open_position(
            _signal(100.0, 94.0), _rising(), 0, _MakerCfg(), missed_counter=mc,
            maker_realism=build_maker_realism(), maker_stats=ms,
        ) is None
        assert mc == {"stop_band_high": 1}
        assert ms == {}   # emir hiç verilmedi: maker sayaçları dokunulmaz

    def test_maker_timeout_keeps_its_own_counters_and_adds_no_h1_key(self):
        mc: Dict[str, int] = {}
        ms: Dict[str, int] = {}
        assert open_position(
            _signal(), _rising(), 0, _MakerCfg(), missed_counter=mc,
            maker_realism=build_maker_realism(), maker_stats=ms,
        ) is None
        assert mc == {"maker_missed": 1}
        assert ms == {"placed": 1, "filled": 0, "timed_out": 1}

    def test_maker_fill_sets_fill_bar_trigger_and_leaves_counters_clean(self):
        candles = _rising()
        candles[1] = Candle(open_time=1000, open=100.2, high=100.6, low=99.5,
                            close=100.0, volume=1.0, close_time=1999)
        mc: Dict[str, int] = {}
        ms: Dict[str, int] = {}
        pos = open_position(
            _signal(), candles, 0, _MakerCfg(), missed_counter=mc,
            maker_realism=build_maker_realism(), maker_stats=ms,
        )
        assert pos is not None and pos.entry_idx == 1
        assert pos.fill_bar_trigger == pytest.approx(100.0 * (1.0 - 0.0001))
        assert mc == {}
        assert ms == {"placed": 1, "filled": 1, "timed_out": 0}

    def test_maker_fill_bar_path_and_optimistic_iii_compose(self):
        """maker_fill_bar kısaltılmış dolum mumunu üretir; optimistic (iii) o
        mumda yine çıkış adımı işletmez (ve SL, kısaltılmış yolda değse bile)."""
        candles = [
            Candle(open_time=0, open=100.0, high=100.1, low=99.9, close=100.0,
                   volume=1.0, close_time=999),
            # Dolum mumu: limit 100'e iner (low 98.5), SONRA stopun (99.0) altına
            # düşer ve kapanır → pessimistic'te dolum sonrası yolda SL.
            Candle(open_time=1000, open=100.2, high=100.4, low=98.5, close=98.8,
                   volume=1.0, close_time=1999),
            # Sonraki mum stopun üstüne toparlar: optimistic'te SL hiç oluşmaz.
            Candle(open_time=2000, open=100.0, high=100.2, low=99.6, close=100.0,
                   volume=1.0, close_time=2999),
        ]
        cfg = _MakerCfg()
        pos_p = open_position(_signal(), candles, 0, cfg,
                              maker_realism=build_maker_realism())
        pos_o = open_position(_signal(), candles, 0, cfg,
                              maker_realism=build_maker_realism())
        assert pos_p is not None and pos_p.entry_idx == 1
        assert pos_p.fill_bar_trigger is not None
        t_p = manage_position(pos_p, candles, CFG, same_bar_order="pessimistic")
        t_o = manage_position(pos_o, candles, CFG, same_bar_order="optimistic")
        assert (t_p.exit_idx, t_p.exit_reason) == (1, "SL")
        assert (t_o.exit_idx, t_o.exit_reason) == (2, "EOD")

    def test_check_timeframe_roles_unchanged_by_the_realism_package(self):
        bt.check_timeframe_roles(("5m", "15m", "4h"))
        bt.check_timeframe_roles(("30m", "4h", "4h"))
        for bad in (("5m", "5m", "4h"), ("5m", "15m", "5m")):
            with pytest.raises(ValueError, match="çakış"):
                bt.check_timeframe_roles(bad)

    @pytest.mark.asyncio
    async def test_run_backtest_rejects_colliding_roles_before_any_fetch(self, monkeypatch):
        from test_golden_backtest import _NetworkForbiddenKlineFetcher  # type: ignore

        monkeypatch.setattr(bt, "KlineFetcher", _NetworkForbiddenKlineFetcher)
        cfg = settings.model_copy(update={"scalper_tf_entry": "15m", "scalper_tf_context": "15m"})
        with pytest.raises(ValueError, match="çakış"):
            await bt.run_backtest(
                days=1, symbols=["BTCUSDT"], strategy_names="C", cfg=cfg,
                base_url="https://network-must-not-be-used.invalid",
            )
