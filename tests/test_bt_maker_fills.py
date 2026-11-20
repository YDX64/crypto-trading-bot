"""A2 — backtest maker (limit) giriş gerçekçiliği: maker_trade_through,
maker_fill_bar, single_pending. Sentetik mumlar, elle doğrulanabilir aritmetik.

Sözleşme: limit L = sinyal mumunun kapanışı. Trade-through oranı tt (varsayılan
%0.01 = 1 baz puan): LONG yalnız low <= L*(1-tt), SHORT yalnız high >= L*(1+tt)
ise dolar; dolum fiyatı L'dir. Dolum mumunda TP/stop/MFE/MAE yalnız dolumdan
SONRAKİ mum-içi yolu sayar. Bekleyen maker emri varken aynı sembole ikinci emir
yok."""

from __future__ import annotations

import json

import pytest

from src.core.config import settings
from src.strategies.scalper.backtest import (
    DEFAULT_MAKER_TRADE_THROUGH_PCT,
    REALISM_FEATURES,
    _build_arg_parser,
    build_maker_realism,
    manage_position,
    maker_orders_report,
    normalize_disabled_realism,
    open_position,
    realism_report,
    resolve_disabled_realism,
    simulate_symbol,
    write_json_report,
)
from src.strategies.scalper.types import (
    Candle, Direction, Regime, ScalpSignal, StrategyProtocol,
)

MAKER_FEATURES = ("maker_trade_through", "maker_fill_bar", "single_pending")


def _c(i, o, h, l, c) -> Candle:
    ot = i * 300_000
    return Candle(open_time=ot, open=o, high=h, low=l, close=c,
                  volume=1.0, close_time=ot + 299_999)


def _cfg(mode="maker", timeout=3, **extra):
    updates = dict(
        scalper_entry_mode=mode,
        scalper_maker_fill_timeout_candles=timeout,
        scalper_min_rr=0.0,
        scalper_leverage=10,
        scalper_tp1_roi=10.0,       # 10x kaldıraç -> TP1 = giriş +-%1
        scalper_tp2_roi=50.0,
        scalper_min_stop_pct=0.1,
        scalper_max_stop_pct=5.0,
        scalper_regime_filter=False,
        scalper_market_gate=False,
        scalper_loss_cooldown_minutes=0,
        scalper_max_hold_hours=0.0,
    )
    updates.update(extra)
    return settings.model_copy(update=updates)


def _sig(direction=Direction.LONG, entry=100.0, stop=None) -> ScalpSignal:
    if stop is None:
        stop = 99.0 if direction == Direction.LONG else 101.0
    return ScalpSignal(
        strategy="X", symbol="TESTUSDT", direction=direction,
        entry_price=entry, stop_price=stop, reason="t",
        regime=Regime.UP, atr_5m=1.0, risk_multiplier=1.0,
    )


def _real(disabled=(), pct=None):
    return build_maker_realism(disabled, pct)


def _open(candles, direction=Direction.LONG, mr=None, timeout=3, stats=None,
          missed=None):
    return open_position(
        _sig(direction), candles, 0, _cfg(timeout=timeout),
        maker_realism=mr if mr is not None else _real(), maker_stats=stats,
        missed_counter=missed,
    )


# ---- maker_trade_through ---------------------------------------------------

def test_default_trade_through_is_one_basis_point():
    assert DEFAULT_MAKER_TRADE_THROUGH_PCT == 0.01
    assert _real().trade_through_rate == pytest.approx(0.0001)
    assert _real(pct=0.5).trade_through_rate == pytest.approx(0.005)
    assert _real(("maker_trade_through",)).trade_through_rate == 0.0
    with pytest.raises(ValueError):
        build_maker_realism(None, -0.1)


def test_touch_without_trade_through_does_not_fill_long():
    # limit 100; low 100.0 (tam temas) ve 99.995 (0.5 bp) -> tt=1bp altında: DOLMAZ
    candles = [_c(0, 100, 100, 100, 100), _c(1, 100.3, 100.5, 100.0, 100.2),
               _c(2, 100.3, 100.5, 99.995, 100.2)]
    miss = {}
    assert _open(candles, timeout=2, missed=miss) is None
    assert miss == {"maker_missed": 1}


def test_trade_through_fills_at_limit_long():
    candles = [_c(0, 100, 100, 100, 100), _c(1, 100.3, 100.5, 100.2, 100.3),
               _c(2, 100.1, 100.2, 99.98, 100.0)]   # 99.98 <= 99.99
    pos = _open(candles, timeout=3)
    assert pos is not None and pos.entry_idx == 2
    assert pos.entry_price == 100.0                   # dolum fiyatı = L
    assert pos.entry_commission_rate == pytest.approx(0.0002)  # maker fee


def test_trade_through_short_mirror():
    # SHORT: high >= 100.01 gerekir
    touch = [_c(0, 100, 100, 100, 100), _c(1, 99.7, 100.0, 99.5, 99.8),
             _c(2, 99.7, 100.005, 99.5, 99.8)]
    assert _open(touch, Direction.SHORT, timeout=2) is None
    thru = [_c(0, 100, 100, 100, 100), _c(1, 99.7, 99.9, 99.5, 99.8),
            _c(2, 99.9, 100.02, 99.6, 99.8)]
    pos = _open(thru, Direction.SHORT, timeout=3)
    assert pos is not None and pos.entry_idx == 2 and pos.entry_price == 100.0


def test_custom_trade_through_pct():
    candles = [_c(0, 100, 100, 100, 100), _c(1, 100.1, 100.2, 99.7, 100.0)]
    assert _open(candles, mr=_real(pct=0.5), timeout=1) is None      # 99.5 gerekir
    assert _open(candles, mr=_real(pct=0.2), timeout=1) is not None  # 99.8 gerekir


def test_trade_through_off_restores_touch_fill():
    candles = [_c(0, 100, 100, 100, 100), _c(1, 100.3, 100.5, 100.0, 100.2)]
    pos = _open(candles, mr=_real(("maker_trade_through",)), timeout=1)
    assert pos is not None and pos.entry_idx == 1 and pos.entry_price == 100.0


# ---- maker_fill_bar --------------------------------------------------------

def _run(candles, direction, mr):
    cfg = _cfg(timeout=1)
    pos = open_position(_sig(direction), candles, 0, cfg, maker_realism=mr)
    assert pos is not None
    return manage_position(pos, candles, cfg)


def test_high_before_fill_does_not_hit_tp1_long():
    # Düşen mum: yol O,H,L,C. Tepe 101.5 DOLUMDAN ÖNCE (101.5 -> 99.9 inerken limit).
    fill_bar = _c(1, 100.6, 101.5, 99.9, 100.0)
    nxt = _c(2, 100.0, 100.2, 99.95, 100.0)
    candles = [_c(0, 100, 100, 100, 100), fill_bar, nxt]
    t = _run(candles, Direction.LONG, _real())
    assert "TP1" not in [l["label"] for l in t.legs]
    assert t.mfe_pct == pytest.approx(2.0)     # yalnız sonraki mumun 100.2'si: %0.2*10x
    assert t.mae_pct == pytest.approx(-1.0)    # dolum mumunun dibi 99.9: %-0.1*10x
    # özellik kapalı -> eski: tam mumun tepesi TP1'i (101) sayar
    t_old = _run(candles, Direction.LONG, _real(("maker_fill_bar",)))
    assert "TP1" in [l["label"] for l in t_old.legs]
    assert t_old.mfe_pct == pytest.approx(15.0)


def test_high_after_fill_counts_on_rising_fill_bar_long():
    # Yükselen mum: yol O,L,H,C. Dip (dolum) ÖNCE, tepe SONRA -> TP1 sayılır.
    fill_bar = _c(1, 100.1, 101.5, 99.9, 101.0)
    candles = [_c(0, 100, 100, 100, 100), fill_bar, _c(2, 101, 101, 101, 101)]
    t = _run(candles, Direction.LONG, _real())
    assert "TP1" in [l["label"] for l in t.legs]
    assert t.mfe_pct == pytest.approx(15.0)


def test_low_before_fill_does_not_count_short_mirror():
    # SHORT, yükselen mum: yol O,L,H,C. Dip 98.9 dolumdan (100.01'e yükselirken) ÖNCE.
    fill_bar = _c(1, 99.4, 100.2, 98.9, 100.1)
    nxt = _c(2, 100.1, 100.1, 100.1, 100.1)
    candles = [_c(0, 100, 100, 100, 100), fill_bar, nxt]
    t = _run(candles, Direction.SHORT, _real())
    assert "TP1" not in [l["label"] for l in t.legs]    # TP1 = 99, dip öncesi
    assert t.mfe_pct == 0.0
    assert t.mae_pct == pytest.approx(-2.0)             # tepe 100.2: %0.2*10x
    t_old = _run(candles, Direction.SHORT, _real(("maker_fill_bar",)))
    assert "TP1" in [l["label"] for l in t_old.legs]
    assert t_old.mfe_pct == pytest.approx(11.0)


def test_open_at_or_beyond_trigger_whole_bar_counts():
    # Mum limitin ALTINDA açılır (gap): dolum açılışta, tüm yol sonrası.
    fill_bar = _c(1, 99.5, 101.2, 99.4, 101.0)     # 99.5 <= 99.99
    candles = [_c(0, 100, 100, 100, 100), fill_bar, _c(2, 101, 101, 101, 101)]
    t = _run(candles, Direction.LONG, _real())
    assert "TP1" in [l["label"] for l in t.legs]
    assert t.mae_pct == pytest.approx(-6.0)         # dip 99.4: %-0.6*10x


# ---- single_pending --------------------------------------------------------

class _FlatSignal(StrategyProtocol):
    """Her turda LONG sinyali (limit = kapanış = 100)."""
    name = "X"

    def evaluate(self, ctx):
        return _sig(Direction.LONG, ctx.current_price, ctx.current_price * 0.99)


def _flat_series(n=12):
    # low=100.0: eski modelde dolar (temas), trade-through'da ASLA dolmaz
    return [_c(i, 100.0, 100.5, 100.0, 100.0) for i in range(n)]


def _sim(candles, disabled=(), mode="maker", stats=None, missed=None, timeout=3):
    return simulate_symbol(
        "TESTUSDT", candles, [], [], [_FlatSignal()], _cfg(mode, timeout),
        missed_counter=missed, disabled_realism=disabled, maker_stats=stats,
    )


def test_pending_order_blocks_second_order_until_timeout():
    stats, miss = {}, {}
    assert _sim(_flat_series(), stats=stats, missed=miss) == []
    # emirler 0,4,8. mumlarda: her biri bar+1..bar+3 dolmadan bekler
    assert stats == {"placed": 3, "filled": 0, "timed_out": 3}
    assert miss["maker_missed"] == 3


def test_single_pending_off_reattempts_every_bar():
    stats = {}
    _sim(_flat_series(), disabled=("single_pending",), stats=stats)
    assert stats == {"placed": 11, "filled": 0, "timed_out": 11}  # bar 0..10


def test_legacy_model_fills_on_touch_and_matches_old_maker_behaviour():
    stats = {}
    trades = _sim(_flat_series(), disabled=REALISM_FEATURES, stats=stats)
    assert trades and trades[0].entry_price == 100.0
    assert stats["filled"] == len(trades) and stats["timed_out"] == 0
    # eski davranış: temas dolum + tam mum — üç maker özelliği kapalı da aynısı
    trades2 = _sim(_flat_series(), disabled=REALISM_FEATURES)
    assert [t.to_dict() for t in trades] == [t.to_dict() for t in trades2]
    # SABİTLENMİŞ main sayıları: aynı mumlar/yapılandırma main'in backtest.py'sinde
    # (git show main:...) koşturuldu → tek işlem, giriş 100.0, çıkış 100.0 EOD,
    # pnl -7.0, mfe 5.0, mae 0.0. Kendini kendisiyle kıyaslamak determinizmi
    # kanıtlar; bu sayılar ise eski maker davranışını.
    assert len(trades) == 1
    t = trades[0]
    assert (t.entry_price, t.exit_price, t.exit_reason) == (100.0, 100.0, "EOD")
    assert t.pnl == pytest.approx(-7.0)
    assert t.mfe_pct == pytest.approx(5.0) and t.mae_pct == pytest.approx(0.0)


def test_legacy_model_fill_bar_counts_whole_bar_like_main():
    # main: dolum mumunda DOLUMDAN ÖNCEKİ tepe (101.5) de sayılır → TP1 + MFE 15.0
    # (main'in manage_position'ı ile doğrulandı: ['TP1','TRAIL'], 15.0, -1.0).
    fill_bar = _c(1, 100.6, 101.5, 99.9, 100.0)
    candles = [_c(0, 100, 100, 100, 100), fill_bar, _c(2, 100.0, 100.2, 99.95, 100.0)]
    t = _run(candles, Direction.LONG, _real(REALISM_FEATURES))
    assert [l["label"] for l in t.legs] == ["TP1", "TRAIL"]
    assert t.mfe_pct == pytest.approx(15.0)
    assert t.mae_pct == pytest.approx(-1.0)


# ---- taker modu değişmez ---------------------------------------------------

def test_taker_mode_unchanged_by_maker_features():
    candles = [_c(i, 100 + i * 0.1, 100.6 + i * 0.1, 99.9 + i * 0.1, 100.2 + i * 0.1)
               for i in range(40)]
    stats = {}
    a = _sim(candles, mode="taker", stats=stats)
    b = _sim(candles, mode="taker", disabled=MAKER_FEATURES)
    assert a and [t.to_dict() for t in a] == [t.to_dict() for t in b]
    assert stats == {}          # taker emri maker sayacına yazılmaz


def test_taker_legacy_run_matches_main_pinned_numbers():
    # Aynı sentetik seri main'in simulate_symbol'ünde koşturuldu: tek işlem,
    # giriş 100.12002, çıkış 104.10000000000001 EOD, pnl 268.159064,
    # mfe 43.7473, mae -1.1988. Tüm gerçekçilik kapalıyken şube = main.
    candles = [_c(i, 100 + i * 0.1, 100.6 + i * 0.1, 99.9 + i * 0.1, 100.2 + i * 0.1)
               for i in range(40)]
    tr = _sim(candles, mode="taker", disabled=REALISM_FEATURES)
    assert len(tr) == 1
    t = tr[0]
    assert t.entry_price == pytest.approx(100.12002, abs=1e-9)
    assert t.exit_price == pytest.approx(104.1, abs=1e-9) and t.exit_reason == "EOD"
    assert t.pnl == pytest.approx(268.159064, abs=1e-6)
    assert t.mfe_pct == pytest.approx(43.7473, abs=1e-4)
    assert t.mae_pct == pytest.approx(-1.1988, abs=1e-4)


# ---- rapor / CLI -----------------------------------------------------------

def test_maker_orders_report_shape():
    assert maker_orders_report({"placed": 4, "filled": 3, "timed_out": 1}) == {
        "placed": 4, "filled": 3, "timed_out": 1, "fill_rate": 0.75}
    assert maker_orders_report(None) == {
        "placed": 0, "filled": 0, "timed_out": 0, "fill_rate": None}


def test_json_report_has_maker_orders(tmp_path):
    path = write_json_report(
        [], 1, ["X"], "C", cfg=_cfg(),
        run_metadata={"maker_orders": {"placed": 2, "filled": 1, "timed_out": 1,
                                       "fill_rate": 0.5}},
        output_dir=tmp_path,
    )
    assert json.load(open(path))["maker_orders"]["fill_rate"] == 0.5
    path2 = write_json_report([], 1, ["X"], "C", cfg=_cfg(), output_dir=tmp_path)
    assert json.load(open(path2))["maker_orders"]["placed"] == 0


def test_realism_report_lists_maker_features_and_param():
    assert set(MAKER_FEATURES) <= set(REALISM_FEATURES)
    rep = realism_report(settings, ["maker_fill_bar"], None, 0.05)
    assert "maker_fill_bar" in rep["disabled"]
    assert "maker_trade_through" in rep["enabled"]
    assert rep["parameters"]["maker_trade_through_pct"] == 0.05
    assert realism_report(settings)["parameters"]["maker_trade_through_pct"] == 0.01


def test_cli_flag_and_legacy_model():
    p = _build_arg_parser()
    assert p.parse_args([]).maker_trade_through_pct is None
    assert p.parse_args(["--maker-trade-through-pct", "0.05"]).maker_trade_through_pct == 0.05
    assert resolve_disabled_realism(p.parse_args(["--legacy-model"])) == frozenset(REALISM_FEATURES)
    assert resolve_disabled_realism(
        p.parse_args(["--disable-realism", "single_pending"])
    ) == frozenset({"single_pending"})
    with pytest.raises(ValueError):
        normalize_disabled_realism("maker_typo")
