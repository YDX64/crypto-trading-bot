"""A1 — backtest çıkış dolum gerçekçiliği (gap_fills, exit_slippage,
intrabar_path). Sentetik mumlar, elle doğrulanabilir aritmetik; komisyon 0."""

from __future__ import annotations

import pytest

from src.core.config import settings
from src.strategies.scalper.backtest import (
    REALISM_FEATURES,
    OpenPosition,
    _build_arg_parser,
    _process_candle_exits,
    build_exit_realism,
    intrabar_path,
    manage_position,
    normalize_disabled_realism,
    realism_report,
    resolve_disabled_realism,
    slippage_rate,
    stressed_cfg,
)
from src.strategies.scalper.types import Candle, Direction

S = 0.001  # test kayması


def _c(o, h, l, c, t=1) -> Candle:
    return Candle(open_time=t * 1000, open=o, high=h, low=l, close=c,
                  volume=1.0, close_time=t * 1000 + 999)


def _real(disabled=(), slip=S):
    return build_exit_realism(settings, disabled, slip)


def _long(realism) -> OpenPosition:
    return OpenPosition(
        strategy="C", symbol="BTCUSDT", direction=Direction.LONG,
        stop_price=90.0, entry_idx=0, entry_price=100.0, entry_time=0,
        qty_total=2.0, leverage=10, tp1_price=110.0, tp2_price=120.0,
        tp1_qty=1.0, tp2_qty=0.5, runner_qty=0.5, breakeven_price=100.5,
        current_stop=90.0, regime="UP",
        entry_commission_rate=0.0, exit_commission_rate=0.0,
        exit_realism=realism,
    )


def _short(realism) -> OpenPosition:
    return OpenPosition(
        strategy="C", symbol="BTCUSDT", direction=Direction.SHORT,
        stop_price=110.0, entry_idx=0, entry_price=100.0, entry_time=0,
        qty_total=2.0, leverage=10, tp1_price=90.0, tp2_price=80.0,
        tp1_qty=1.0, tp2_qty=0.5, runner_qty=0.5, breakeven_price=99.5,
        current_stop=110.0, regime="DOWN",
        entry_commission_rate=0.0, exit_commission_rate=0.0,
        exit_realism=realism,
    )


# ---- gap_fills -------------------------------------------------------------

def test_gap_through_stop_long_fills_at_open():
    pos = _long(_real(disabled=("exit_slippage",)))
    assert _process_candle_exits(pos, _c(85, 86, 80, 82), Direction.LONG)
    assert pos.exit_reason == "SL" and pos.exit_price == 85.0
    assert pos.legs[0]["pnl"] == pytest.approx((85.0 - 100.0) * 2.0)


def test_gap_through_stop_short_fills_at_open():
    pos = _short(_real(disabled=("exit_slippage",)))
    assert _process_candle_exits(pos, _c(115, 120, 114, 118), Direction.SHORT)
    assert pos.exit_price == 115.0
    assert pos.legs[0]["pnl"] == pytest.approx((100.0 - 115.0) * 2.0)


def test_normal_stop_with_slippage_long_and_short():
    pl = _long(_real(disabled=("gap_fills",)))
    _process_candle_exits(pl, _c(95, 96, 89, 91), Direction.LONG)
    assert pl.exit_price == pytest.approx(90.0 * (1 - S))
    ps = _short(_real(disabled=("gap_fills",)))
    _process_candle_exits(ps, _c(105, 111, 104, 109), Direction.SHORT)
    assert ps.exit_price == pytest.approx(110.0 * (1 + S))


def test_stop_not_gapped_when_open_inside_fills_at_stop():
    pos = _long(_real(disabled=("exit_slippage",)))
    _process_candle_exits(pos, _c(95, 96, 89, 91), Direction.LONG)
    assert pos.exit_price == 90.0


def test_tp_on_gap_bar_fills_at_open_with_slippage():
    pl = _long(_real())
    assert not _process_candle_exits(pl, _c(112, 113, 111, 112), Direction.LONG)
    assert pl.legs[0]["label"] == "TP1"
    assert pl.legs[0]["price"] == pytest.approx(112.0 * (1 - S))
    assert pl.remaining_qty == pytest.approx(1.0)
    ps = _short(_real())
    _process_candle_exits(ps, _c(88, 89, 87, 88), Direction.SHORT)
    assert ps.legs[0]["price"] == pytest.approx(88.0 * (1 + S))


def test_tp_at_level_without_gap_uses_level():
    pl = _long(_real(disabled=("exit_slippage",)))
    _process_candle_exits(pl, _c(105, 111, 104, 108), Direction.LONG)
    assert pl.legs[0]["price"] == 110.0


# ---- exit_slippage on bar-close exits ---------------------------------------

def test_bar_close_exit_slippage_eod_long_and_short():
    flat = [_c(100, 100, 100, 100, t) for t in (1, 2, 3)]
    pl = _long(_real())
    manage_position(pl, flat, settings)
    assert pl.exit_reason == "EOD" and pl.exit_price == pytest.approx(100.0 * (1 - S))
    ps = _short(_real())
    manage_position(ps, flat, settings)
    assert ps.exit_price == pytest.approx(100.0 * (1 + S))


# ---- intrabar_path ----------------------------------------------------------

def test_intrabar_path_order():
    assert intrabar_path(_c(100, 111, 99, 108)) == (100, 99, 111, 108)
    assert intrabar_path(_c(100, 111, 99, 99.5)) == (100, 111, 99, 99.5)
    assert intrabar_path(_c(100, 101, 99, 100)) == (100, 99, 101, 100)


def test_tp1_on_entry_bar_long_rising_keeps_runner():
    pos = _long(_real(disabled=("exit_slippage",)))
    closed = _process_candle_exits(pos, _c(100, 111, 99, 108), Direction.LONG)
    assert not closed and pos.tp1_filled and pos.remaining_qty == pytest.approx(1.0)
    assert pos.current_stop == 100.5


def test_tp1_on_entry_bar_long_falling_loses_runner_at_be():
    pos = _long(_real(disabled=("exit_slippage",)))
    closed = _process_candle_exits(pos, _c(100, 111, 99, 99.5), Direction.LONG)
    assert closed and pos.exit_reason == "TRAIL"
    assert pos.exit_price == 100.5  # BE bu mumda doğdu: açılışa düşülmez


def test_tp1_on_entry_bar_short_falling_keeps_runner():
    pos = _short(_real(disabled=("exit_slippage",)))
    closed = _process_candle_exits(pos, _c(100, 101, 89, 92), Direction.SHORT)
    assert not closed and pos.tp1_filled and pos.remaining_qty == pytest.approx(1.0)


def test_tp1_on_entry_bar_short_rising_loses_runner_at_be():
    pos = _short(_real(disabled=("exit_slippage",)))
    closed = _process_candle_exits(pos, _c(100, 101, 89, 100.5), Direction.SHORT)
    assert closed and pos.exit_price == 99.5


def test_tp2_floor_tested_only_after_tp2_touch():
    # TP1 önceki mumda doldu; bu mumda TP2 (120) dolar, taban TP1=110.
    def long_after_tp1(real):
        p = _long(real)
        p.tp1_filled = True
        p.trailing_active = True
        p.remaining_qty = 1.0
        p.current_stop = 100.5
        return p
    rising = _c(105, 121, 104, 119)   # yolu O,L,H,C: TP2 sonrası min=119 > 110
    p = long_after_tp1(_real(disabled=("exit_slippage",)))
    assert not _process_candle_exits(p, rising, Direction.LONG)
    assert p.tp2_filled and p.current_stop == 110.0
    falling = _c(105, 121, 104, 108)  # yolu O,H,L,C: TP2 sonrası low 104 <= 110
    p = long_after_tp1(_real(disabled=("exit_slippage",)))
    assert _process_candle_exits(p, falling, Direction.LONG)
    assert p.exit_reason == "TRAIL" and p.exit_price == 110.0
    # kapalıyken eski tam-mum testi: yükselen mum da düşük 104 <= 110 ile kapanır
    p = long_after_tp1(_real(disabled=("exit_slippage", "intrabar_path")))
    assert _process_candle_exits(p, rising, Direction.LONG)


# ---- her özellik tek tek kapalı ---------------------------------------------

def test_each_feature_disabled_restores_old_behaviour():
    gap_bar = _c(85, 86, 80, 82)
    p = _long(_real(disabled=("gap_fills",)))
    _process_candle_exits(p, gap_bar, Direction.LONG)
    assert p.exit_price == pytest.approx(90.0 * (1 - S))        # kayma var, boşluk yok
    p = _long(_real(disabled=("exit_slippage",)))
    _process_candle_exits(p, gap_bar, Direction.LONG)
    assert p.exit_price == 85.0                                  # boşluk var, kayma yok
    p = _long(_real(disabled=("intrabar_path", "exit_slippage")))
    assert _process_candle_exits(p, _c(100, 111, 99, 108), Direction.LONG)  # eski: runner kaybı


def test_legacy_model_reproduces_old_numbers():
    off = normalize_disabled_realism(REALISM_FEATURES)
    r = build_exit_realism(settings, off, S)
    assert (r.gap_fills, r.exit_slippage_rate, r.intrabar_path) == (False, 0.0, False)
    p = _long(r)
    _process_candle_exits(p, _c(85, 86, 80, 82), Direction.LONG)
    assert p.exit_price == 90.0 and p.legs[0]["price"] == 90.0
    p = _long(r)
    assert _process_candle_exits(p, _c(100, 111, 99, 108), Direction.LONG)
    assert p.exit_price == 100.5
    flat = [_c(100, 100, 100, 100, t) for t in (1, 2)]
    p = _long(r)
    manage_position(p, flat, settings)
    assert p.exit_price == 100.0
    # doğrudan kurulan OpenPosition varsayılanı da eski model
    d = OpenPosition(
        strategy="C", symbol="X", direction=Direction.LONG, stop_price=90.0,
        entry_idx=0, entry_price=100.0, entry_time=0, qty_total=1.0, leverage=10,
        tp1_price=110.0, tp2_price=120.0, tp1_qty=0.5, tp2_qty=0.25,
        runner_qty=0.25, breakeven_price=100.5, current_stop=90.0, regime="UP",
        entry_commission_rate=0.0, exit_commission_rate=0.0,
    )
    _process_candle_exits(d, _c(85, 86, 80, 82), Direction.LONG)
    assert d.exit_price == 90.0


# ---- anahtarlar / CLI / rapor -----------------------------------------------

def test_unknown_feature_rejected():
    with pytest.raises(ValueError):
        normalize_disabled_realism("gap_fills,nope")
    with pytest.raises(ValueError):
        build_exit_realism(settings, ["bogus"])


def test_cli_flags():
    parser = _build_arg_parser()
    a = parser.parse_args(["--legacy-model"])
    assert resolve_disabled_realism(a) == frozenset(REALISM_FEATURES)
    a = parser.parse_args(["--disable-realism", "gap_fills,exit_slippage"])
    assert resolve_disabled_realism(a) == {"gap_fills", "exit_slippage"}
    a = parser.parse_args([])
    assert resolve_disabled_realism(a) == frozenset()
    assert a.exit_slippage_pct is None
    with pytest.raises(ValueError):
        resolve_disabled_realism(parser.parse_args(["--disable-realism", "xx"]))


def test_default_exit_slippage_equals_entry_and_scales_with_stress():
    assert build_exit_realism(settings).exit_slippage_rate == slippage_rate(settings)
    st = stressed_cfg(settings, 2.0)
    assert build_exit_realism(st).exit_slippage_rate == pytest.approx(
        2.0 * slippage_rate(settings)
    )


def test_realism_report_shape():
    rep = realism_report(settings, ["gap_fills"], 0.0005)
    assert rep["disabled"] == ["gap_fills"]
    # A2 üç maker özelliğini REALISM_FEATURES'a ekledi; hepsi varsayılan AÇIK.
    assert rep["enabled"] == [
        "exit_slippage", "intrabar_path",
        "maker_trade_through", "maker_fill_bar", "single_pending",
        # A3 funding + likidasyon da varsayılan AÇIK (yalnız beklenti güncellemesi).
        "funding", "liquidation",
        # A4 günlük kayıp mandalı da varsayılan AÇIK (yalnız beklenti güncellemesi).
        "daily_latch",
    ]
    assert rep["parameters"]["exit_slippage_rate"] == 0.0005


# ---- aynı mumda TP1 sonrası TP2 ile BE stopunun yol sırası (denetim bulgusu) ----

def test_same_bar_tp1_then_tp2_before_be_stop_falling_bar_long():
    # LONG giriş 100, TP1 110, TP2 120, BE 100.5. Düşen mum: yol O(105)→H(125)
    # →L(99)→C(100). 110'u ve 120'yi H'ye giderken geçer, BE'ye (100.5) ANCAK
    # sonra düşer: TP1 + TP2 dolar, taban TP1'e (110) çıkar ve H→L'de 110'dan
    # TRAIL ile kapanır. Eskiden TP2 hiç dolmaz, hepsi BE'de kapanırdı.
    pos = _long(_real(slip=0.0))
    assert _process_candle_exits(pos, _c(105, 125, 99, 100), Direction.LONG)
    assert [(l["label"], l["price"]) for l in pos.legs] == [
        ("TP1", 110.0), ("TP2", 120.0), ("TRAIL", 110.0),
    ]
    assert pos.legs[2]["quantity"] == pytest.approx(0.5)


def test_same_bar_tp1_then_tp2_before_be_stop_falling_bar_short():
    pos = _short(_real(slip=0.0))
    # close>=open → yol O(95)→L(75)→H(101)→C(100): dibe giderken TP1 (90) ve
    # TP2 (80) geçilir, sonra tepeye çıkarken taban 90'a (TP1) dokunulur.
    assert _process_candle_exits(pos, _c(95, 101, 75, 100), Direction.SHORT)
    labels = [(l["label"], l["price"]) for l in pos.legs]
    assert labels == [("TP1", 90.0), ("TP2", 80.0), ("TRAIL", 90.0)]


def test_same_bar_rising_bar_fills_tp1_tp2_and_runner_survives():
    # Yükselen mum LONG: yol O(105)→L(99)→H(125)→C(120). TP1 (110) yalnız
    # L'den H'ye giderken değer; 99 TP1'den ÖNCE olduğundan BE sınanmaz. TP2
    # dolar, H→C'de 110 tabanına inilmez: runner açık kalır.
    pos = _long(_real(slip=0.0))
    _process_candle_exits(pos, _c(105, 125, 99, 120), Direction.LONG)
    assert [l["label"] for l in pos.legs] == ["TP1", "TP2"]
    assert pos.remaining_qty == pytest.approx(0.5)


def test_same_bar_tp1_then_be_stop_before_tp2_keeps_pessimistic_order():
    # Düşen mum: O(111)→H(112)→L(99)→C(100). TP1 (110) açılışta zaten
    # geçilmiş; yol 112→99 BE'ye (100.5) iner ve TP2'ye (120) hiç varmaz.
    pos = _long(_real(slip=0.0))
    assert _process_candle_exits(pos, _c(111, 112, 99, 100), Direction.LONG)
    assert [(l["label"], l["price"]) for l in pos.legs] == [
        ("TP1", 111.0), ("TRAIL", 100.5),
    ]


# ---- STALE_TP / CHOCH / chandelier trail: gerçekçilik AÇIKKEN kayma/boşluk ----

from types import SimpleNamespace  # noqa: E402

MIN_MS = 60_000


def _stale_cfg():
    return SimpleNamespace(
        scalper_stale_tp_hours=2.0, scalper_stale_tp_min_roi_pct=2.0,
        scalper_max_hold_hours=0.0, scalper_chandelier_atr_period=14,
        scalper_chandelier_atr_mult=2.5,
    )


def _flat(closes, step=30 * MIN_MS):
    return [Candle(open_time=i * step, open=c, high=c + 0.05, low=c - 0.05,
                   close=c, volume=1.0, close_time=(i + 1) * step)
            for i, c in enumerate(closes)]


def test_stale_tp_exit_gets_slippage_long_and_short_when_realism_on():
    # 4. mum kapanışı %3 ROI (kaldıraç 10) → STALE_TP kapanıştan, kayma aleyhte.
    pl = _long(_real())
    t = manage_position(pl, _flat([100.3] * 6), _stale_cfg())
    assert t.exit_reason == "STALE_TP"
    assert t.exit_price == pytest.approx(100.3 * (1 - S))
    ps = _short(_real())
    t = manage_position(ps, _flat([99.7] * 6), _stale_cfg())
    assert t.exit_reason == "STALE_TP"
    assert t.exit_price == pytest.approx(99.7 * (1 + S))
    # kayma kapalıyken tam kapanış (test_stale_tp_exit.py'nin eski beklentisi)
    p0 = _long(_real(disabled=("exit_slippage",)))
    assert manage_position(p0, _flat([100.3] * 6), _stale_cfg()).exit_price == pytest.approx(100.3)


def test_choch_exit_gets_slippage_long_and_short_when_realism_on(monkeypatch):
    import src.strategies.scalper.backtest as bt
    monkeypatch.setattr(bt, "structure_exit_action", lambda *a, **k: "close")
    feed = SimpleNamespace(state_at=lambda close_time: None)
    cfg = _stale_cfg()
    cfg.scalper_stale_tp_hours = 0.0
    pl = _long(_real())
    t = manage_position(pl, _flat([100.2] * 3), cfg, structure_feed=feed)
    assert t.exit_reason == "CHOCH"
    assert t.exit_price == pytest.approx(100.2 * (1 - S))
    ps = _short(_real())
    t = manage_position(ps, _flat([99.8] * 3), cfg, structure_feed=feed)
    assert t.exit_reason == "CHOCH"
    assert t.exit_price == pytest.approx(99.8 * (1 + S))


def test_trailing_stop_gap_through_fills_at_open_with_slippage():
    # TP1 görmüş, chandelier ile 105'e yükselmiş stop; sonraki mum 103'te AÇILIR
    # (stopun ötesi) → tetik = açılış 103, kayma aleyhte (LONG satar).
    pos = _long(_real())
    pos.tp1_filled = True
    pos.trailing_active = True
    pos.remaining_qty = 1.0
    pos.current_stop = 105.0
    assert _process_candle_exits(pos, _c(103, 104, 101, 102), Direction.LONG)
    assert pos.exit_reason == "TRAIL"
    assert pos.exit_price == pytest.approx(103.0 * (1 - S))
    # SHORT aynası: stop 95, mum 97'de açılır
    ps = _short(_real())
    ps.tp1_filled = True
    ps.trailing_active = True
    ps.remaining_qty = 1.0
    ps.current_stop = 95.0
    assert _process_candle_exits(ps, _c(97, 99, 96, 98), Direction.SHORT)
    assert ps.exit_price == pytest.approx(97.0 * (1 + S))
