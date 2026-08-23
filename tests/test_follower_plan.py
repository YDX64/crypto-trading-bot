"""Takipçi boyutlama planı (D20) — `src/strategies/follower/plan.py`.

KULLANICI KARARI (2026-08-23): marj = sermayenin %10'u; kaldıraç volatiliteye
göre `clamp(round(30 / sl_pct), 3, 100)`; üstüne borsa kaldıraç dilimi,
likidasyon (`lev × sl_pct ≤ 50`) ve bakım marjı (`1/lev − mmr > 2 × sl_pct/100`)
kapıları. Kullanıcının verdiği üç örnek burada BİREBİR doğrulanır.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.strategies.follower.levels import resolve_levels
from src.strategies.follower.plan import (
    build_plan,
    parse_brackets,
    roundtrip_fee_roi_pct,
    raw_target_leverage,
    resolve_leverage,
    select_bracket,
    split_three_quantities,
    target_leverage,
    with_exchange_quantity,
)
from src.strategies.follower.types import (
    FollowerRejected,
    LeverageBracket,
    MessageLevels,
    format_price,
    parse_ledger_levels,
)
from src.strategies.scalper.types import Direction


def _cfg(**overrides):
    base = dict(
        follower_margin_pct=10.0,
        follower_sl_roi_target=30.0,
        follower_lev_min=3,
        follower_lev_max=100,
        follower_lev_liq_guard_pct=50.0,
        follower_mmr_safety_mult=2.0,
        follower_tp_rr1=0.5,
        follower_tp_rr2=1.0,
        follower_tp_rr3=1.5,
        follower_min_sl_pct=0.02,
        follower_max_sl_pct=5.0,
        follower_sl_atr_mult=3.0,
        follower_atr_len=14,
        # Boyutlama testleri SAF FORMÜLÜ ölçer; ücret eşiği kapısı (varsayılan
        # 1.0 = AÇIK) ayrı bir sınıfta test edilir (TestFeeThreshold).
        follower_min_tp1_fee_ratio=0.0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# Kapıların bağlamadığı "saf formül" dilimi: borsa tavanı 125x, mmr 0.
FREE_BRACKET = [
    LeverageBracket(
        max_leverage=125,
        maint_margin_ratio=0.0,
        notional_floor=0.0,
        notional_cap=float("inf"),
    )
]


def _levels(sl_pct: float, entry: float = 100_000.0, cfg=None):
    """Verilen stop yüzdesini üreten LONG seviye kümesi."""
    return resolve_levels(
        entry=entry,
        direction=Direction.LONG,
        message=MessageLevels(sl=entry * (1.0 - sl_pct / 100.0)),
        atr_value=None,
        cfg=cfg or _cfg(),
    )


class TestUserExamples:
    """Kullanıcının verdiği üç örnek — BİREBİR."""

    @pytest.mark.parametrize(
        "sl_pct,expected_lev,expected_sl_roi,expected_tp1_roi",
        [
            (0.08, 100, 8.0, 4.0),    # BTC 1m
            (0.30, 100, 30.0, 15.0),  # DOGE
            (0.60, 50, 30.0, 15.0),
        ],
    )
    def test_target_leverage_and_roi(
        self, sl_pct, expected_lev, expected_sl_roi, expected_tp1_roi
    ):
        cfg = _cfg()
        lev = target_leverage(sl_pct, cfg)
        assert lev == expected_lev
        assert lev * sl_pct == pytest.approx(expected_sl_roi)
        assert 0.5 * lev * sl_pct == pytest.approx(expected_tp1_roi)

    @pytest.mark.parametrize(
        "sl_pct,expected_lev", [(0.08, 100), (0.30, 100), (0.60, 50)]
    )
    def test_full_pipeline_with_free_bracket(self, sl_pct, expected_lev):
        cfg = _cfg()
        leverage, target, reason, mmr = resolve_leverage(
            sl_pct=sl_pct, margin_usdt=100.0, brackets=FREE_BRACKET, cfg=cfg
        )
        assert leverage == expected_lev
        assert mmr == 0.0
        assert leverage * sl_pct <= cfg.follower_lev_liq_guard_pct

    def test_btc_example_end_to_end(self):
        """SL %0.08 → lev 100 → marj 100 USDT → nominal 10.000 USDT."""
        cfg = _cfg()
        levels = resolve_levels(
            entry=100_000.0,
            direction=Direction.LONG,
            message=MessageLevels(sl=99_920.0),  # %0.08
            atr_value=None,
            cfg=cfg,
        )
        assert levels.sl_pct == pytest.approx(0.08)
        plan = build_plan(
            symbol="BTCUSDT",
            direction=Direction.LONG,
            levels=levels,
            equity_usdt=1000.0,
            brackets=FREE_BRACKET,
            cfg=cfg,
            step_size=0.001,
        )
        assert plan.leverage == 100
        # Formül clamp'i İÇERİR: round(30/0.08)=375 → LEV_MAX=100'e kırpılır;
        # hangi kapının bağladığı `leverage_cap_reason`'da görünür.
        assert plan.leverage_target == 100
        assert plan.leverage_cap_reason == "lev_max"
        assert raw_target_leverage(0.08, cfg) == 375
        assert plan.margin_usdt == pytest.approx(100.0)
        assert plan.notional_usdt == pytest.approx(10_000.0)
        assert plan.quantity == pytest.approx(0.1)
        assert plan.sl_roi_pct == pytest.approx(8.0)
        assert plan.tp_roi_pct == pytest.approx((4.0, 8.0, 12.0))

    def test_ledger_note_carries_lev_slpct_slroi_margin(self):
        cfg = _cfg()
        levels = resolve_levels(
            entry=100_000.0,
            direction=Direction.LONG,
            message=MessageLevels(sl=99_920.0),
            atr_value=None,
            cfg=cfg,
        )
        plan = build_plan(
            symbol="BTCUSDT",
            direction=Direction.LONG,
            levels=levels,
            equity_usdt=1000.0,
            brackets=FREE_BRACKET,
            cfg=cfg,
        )
        note = plan.ledger_note()
        assert "lev=100" in note
        assert "sl_pct=0.0800" in note
        assert "sl_roi=8.00" in note
        assert "margin=100.0000" in note


class TestExchangeAndLiquidationGuards:
    def test_exchange_bracket_caps_leverage(self):
        cfg = _cfg()
        brackets = [
            LeverageBracket(
                max_leverage=75, maint_margin_ratio=0.0065, notional_cap=50_000
            )
        ]
        leverage, target, reason, mmr = resolve_leverage(
            sl_pct=0.30, margin_usdt=100.0, brackets=brackets, cfg=cfg
        )
        assert target == 100
        assert leverage == 75
        assert reason == "exchange_bracket"
        assert mmr == pytest.approx(0.0065)

    def test_bracket_selected_by_notional(self):
        brackets = [
            LeverageBracket(125, 0.004, 0.0, 50_000.0),
            LeverageBracket(100, 0.005, 50_000.0, 250_000.0),
        ]
        assert select_bracket(brackets, 10_000).max_leverage == 125
        assert select_bracket(brackets, 100_000).max_leverage == 100
        # Tüm dilimlerin üstünde → SON dilim (en muhafazakâr)
        assert select_bracket(brackets, 10_000_000).max_leverage == 100

    def test_missing_brackets_is_fail_closed(self):
        with pytest.raises(FollowerRejected) as exc:
            resolve_leverage(
                sl_pct=0.30, margin_usdt=100.0, brackets=[], cfg=_cfg()
            )
        assert exc.value.code == "no_bracket"

    def test_mmr_guard_derates_leverage(self):
        """mmr payı yetmeyince kaldıraç DÜŞÜRÜLÜR (giriş iptal edilmez)."""
        cfg = _cfg()
        brackets = [LeverageBracket(125, 0.004, 0.0, float("inf"))]
        leverage, _, reason, _ = resolve_leverage(
            sl_pct=0.30, margin_usdt=100.0, brackets=brackets, cfg=cfg
        )
        # 1/100 − 0.004 = 0.006, eşik 2×0.003 = 0.006 → ">" sağlanmaz → 99
        assert leverage == 99
        assert reason == "mmr_guard"
        assert (1.0 / leverage - 0.004) > 2 * 0.30 / 100.0

    def test_liq_guard_never_exceeded(self):
        cfg = _cfg(follower_sl_roi_target=200.0)  # kasıtlı agresif hedef
        leverage, _, _, _ = resolve_leverage(
            sl_pct=1.0, margin_usdt=100.0, brackets=FREE_BRACKET, cfg=cfg
        )
        assert leverage * 1.0 <= cfg.follower_lev_liq_guard_pct

    def test_guard_failure_at_min_leverage_rejects(self):
        cfg = _cfg(follower_lev_min=50, follower_lev_max=50)
        with pytest.raises(FollowerRejected):
            resolve_leverage(
                sl_pct=4.0,  # 50 × 4 = 200 > 50
                margin_usdt=100.0,
                brackets=FREE_BRACKET,
                cfg=cfg,
            )

    def test_leverage_bounds_respected(self):
        cfg = _cfg(follower_lev_min=3, follower_lev_max=10)
        assert target_leverage(0.01, cfg) == 10
        assert target_leverage(30.0, cfg) == 3


class TestQuantitySplit:
    def test_three_equal_parts_with_remainder_on_last(self):
        parts = split_three_quantities(0.1234, 0.001)
        assert parts[0] == parts[1] == pytest.approx(0.041)
        assert parts[2] == pytest.approx(0.0414)
        assert sum(parts) == pytest.approx(0.1234)

    def test_sum_never_exceeds_total(self):
        for total in (1.0, 0.7, 12.345, 0.003):
            parts = split_three_quantities(total, 0.001)
            assert sum(parts) <= total + 1e-12

    def test_integer_step(self):
        parts = split_three_quantities(100.0, 1.0)
        assert parts == (33.0, 33.0, 34.0)

    def test_too_small_to_split(self):
        parts = split_three_quantities(0.002, 0.001)
        assert parts[0] == 0.0 and parts[1] == 0.0
        assert parts[2] == pytest.approx(0.002)

    def test_zero_total(self):
        assert split_three_quantities(0.0, 0.001) == (0.0, 0.0, 0.0)

    def test_with_exchange_quantity_rebuilds_parts(self):
        cfg = _cfg()
        levels = resolve_levels(
            entry=100.0,
            direction=Direction.LONG,
            message=MessageLevels(sl=99.7),
            atr_value=None,
            cfg=cfg,
        )
        plan = build_plan(
            symbol="XRPUSDT",
            direction=Direction.LONG,
            levels=levels,
            equity_usdt=1000.0,
            brackets=FREE_BRACKET,
            cfg=cfg,
            step_size=0.1,
        )
        updated = with_exchange_quantity(plan, 30.0, 0.1)
        assert updated.quantity == pytest.approx(30.0)
        assert sum(updated.tp_quantities) == pytest.approx(30.0)
        assert updated.notional_usdt == pytest.approx(3000.0)


class TestMarginAndEquity:
    def test_margin_is_percentage_of_equity(self):
        cfg = _cfg(follower_margin_pct=10.0)
        levels = resolve_levels(
            entry=100.0,
            direction=Direction.LONG,
            message=MessageLevels(sl=99.7),
            atr_value=None,
            cfg=cfg,
        )
        plan = build_plan(
            symbol="XRPUSDT",
            direction=Direction.LONG,
            levels=levels,
            equity_usdt=2000.0,
            brackets=FREE_BRACKET,
            cfg=cfg,
        )
        assert plan.margin_usdt == pytest.approx(200.0)

    def test_zero_equity_rejected(self):
        cfg = _cfg()
        levels = resolve_levels(
            entry=100.0,
            direction=Direction.LONG,
            message=MessageLevels(sl=99.7),
            atr_value=None,
            cfg=cfg,
        )
        with pytest.raises(FollowerRejected) as exc:
            build_plan(
                symbol="XRPUSDT",
                direction=Direction.LONG,
                levels=levels,
                equity_usdt=0.0,
                brackets=FREE_BRACKET,
                cfg=cfg,
            )
        assert exc.value.code == "no_equity"


class TestParseBrackets:
    def test_binance_list_response(self):
        payload = [
            {
                "symbol": "BTCUSDT",
                "brackets": [
                    {
                        "bracket": 1,
                        "initialLeverage": 125,
                        "notionalCap": 50000,
                        "notionalFloor": 0,
                        "maintMarginRatio": 0.004,
                        "cum": 0.0,
                    },
                    {
                        "bracket": 2,
                        "initialLeverage": 100,
                        "notionalCap": 250000,
                        "notionalFloor": 50000,
                        "maintMarginRatio": 0.005,
                        "cum": 50.0,
                    },
                ],
            }
        ]
        rows = parse_brackets(payload)
        assert len(rows) == 2
        assert rows[0].max_leverage == 125
        assert rows[1].maint_margin_ratio == pytest.approx(0.005)

    def test_dict_response_also_accepted(self):
        payload = {
            "symbol": "BTCUSDT",
            "brackets": [
                {
                    "initialLeverage": 20,
                    "notionalCap": 100,
                    "notionalFloor": 0,
                    "maintMarginRatio": 0.01,
                }
            ],
        }
        assert len(parse_brackets(payload)) == 1

    def test_broken_rows_dropped(self):
        payload = [{"symbol": "X", "brackets": [{"initialLeverage": "abc"}, "junk"]}]
        assert parse_brackets(payload) == []

    def test_non_list_payload(self):
        assert parse_brackets("boom") == []

    def test_zero_maint_margin_ratio_is_invalid(self):
        """Bulgu 9: mmr==0 bir dilim DEĞİL, bozuk bir satırdır.

        Sıfır bakım marjı `_guards_ok`'un mmr kapısını dişsiz bırakır
        (1/lev − 0 hep büyük çıkar) ve 100x'te likidasyon mesafesini
        olduğundan uzak gösterirdi. Düzeltme olmadan bu test KIRMIZIDIR.
        """
        payload = [
            {
                "symbol": "BTCUSDT",
                "brackets": [
                    {
                        "initialLeverage": 125,
                        "notionalCap": 50000,
                        "notionalFloor": 0,
                        "maintMarginRatio": 0,
                    }
                ],
            }
        ]
        assert parse_brackets(payload) == []

    def test_all_zero_mmr_rows_leave_the_list_empty_and_entry_is_refused(self):
        """Boş dilim listesi = fail-closed (giriş yok) — zincir korunur."""
        with pytest.raises(FollowerRejected) as exc:
            build_plan(
                symbol="BTCUSDT",
                direction=Direction.LONG,
                levels=_levels(0.30),
                equity_usdt=1000.0,
                brackets=[],
                cfg=_cfg(),
            )
        assert exc.value.code == "no_bracket"


class TestFeeThreshold:
    """ÜCRET EŞİĞİ — kapı VARSAYILAN AÇIK (ratio 1.0, bkz. D20/D20a).

    `sl_roi = lev × sl_pct`, `tp1_roi = RR1 × sl_roi`. Kaldıraç LEV_MAX'e
    KIRPILDIĞINDA `tp1_roi` gidiş-dönüş komisyonun altına düşer. Bu bir
    boyutlama tercihi değil, aritmetik bir sonuçtur; testler kullanıcının
    kendi örnekleriyle sayısal olarak kilitler.
    """

    def test_fee_roi_scales_with_leverage(self):
        cfg = _cfg()
        assert roundtrip_fee_roi_pct(100, cfg) == pytest.approx(10.0)
        assert roundtrip_fee_roi_pct(50, cfg) == pytest.approx(5.0)
        assert roundtrip_fee_roi_pct(3, cfg) == pytest.approx(0.3)

    @pytest.mark.parametrize(
        "sl_pct,lev,tp1_roi,fee_roi,covers",
        [
            # BTC örneği: hedef 375 → tavan 100 → TP1 %4 < komisyon %10
            (0.08, 100, 4.0, 10.0, False),
            # DOGE örneği: hedef tam 100 → TP1 %15 > komisyon %10
            (0.30, 100, 15.0, 10.0, True),
            # Daha geniş stop: 50x → TP1 %15 > komisyon %5
            (0.60, 50, 15.0, 5.0, True),
        ],
    )
    def test_user_examples_against_the_fee_floor(
        self, sl_pct, lev, tp1_roi, fee_roi, covers
    ):
        cfg = _cfg()
        plan = build_plan(
            symbol="BTCUSDT",
            direction=Direction.LONG,
            levels=_levels(sl_pct),
            equity_usdt=1000.0,
            brackets=FREE_BRACKET,
            cfg=cfg,
        )
        assert plan.leverage == lev
        assert plan.tp_roi_pct[0] == pytest.approx(tp1_roi)
        assert plan.roundtrip_fee_roi_pct == pytest.approx(fee_roi)
        assert plan.as_dict()["tp1_covers_fees"] is covers

    def test_gate_is_enabled_by_default(self):
        """Bulgu 3: VARSAYILAN 1.0 — BTC örneği (sl %0.08) artık AÇILMAZ.

        Düzeltme olmadan bu test KIRMIZIDIR (eski varsayılan 0.0 = kapalı,
        plan sorunsuz kurulurdu).
        """
        cfg = SimpleNamespace(
            **{
                k: v
                for k, v in vars(_cfg()).items()
                if k != "follower_min_tp1_fee_ratio"
            }
        )
        with pytest.raises(FollowerRejected) as exc:
            build_plan(
                symbol="BTCUSDT",
                direction=Direction.LONG,
                levels=_levels(0.08),
                equity_usdt=1000.0,
                brackets=FREE_BRACKET,
                cfg=cfg,
            )
        assert exc.value.code == "fee_gate"

    def test_gate_can_be_disabled_by_user_decision(self):
        """`FOLLOWER_MIN_TP1_FEE_RATIO=0` kapıyı KAPATIR (kullanıcı kararı)."""
        plan = build_plan(
            symbol="BTCUSDT",
            direction=Direction.LONG,
            levels=_levels(0.08),
            equity_usdt=1000.0,
            brackets=FREE_BRACKET,
            cfg=_cfg(follower_min_tp1_fee_ratio=0.0),
        )
        assert plan.tp_roi_pct[0] < plan.roundtrip_fee_roi_pct

    def test_gate_rejects_when_enabled(self):
        with pytest.raises(FollowerRejected) as exc:
            build_plan(
                symbol="BTCUSDT",
                direction=Direction.LONG,
                levels=_levels(0.08),
                equity_usdt=1000.0,
                brackets=FREE_BRACKET,
                cfg=_cfg(follower_min_tp1_fee_ratio=1.0),
            )
        assert exc.value.code == "fee_gate"

    @pytest.mark.parametrize("sl_pct,accepted", [(0.19, False), (0.21, True)])
    def test_threshold_is_leverage_independent(self, sl_pct, accepted):
        """Aritmetik: sl_pct ≥ ratio × 2 × oran × 100 / RR1 = %0.20.

        Kaldıraç EŞİTLİĞİN İKİ TARAFINDA da çarpandır; eşik yalnız stop
        mesafesine bağlıdır (D20 "ücret eşiği" notu).
        """
        cfg = _cfg(follower_min_tp1_fee_ratio=1.0)
        if accepted:
            plan = build_plan(
                symbol="BTCUSDT",
                direction=Direction.LONG,
                levels=_levels(sl_pct),
                equity_usdt=1000.0,
                brackets=FREE_BRACKET,
                cfg=cfg,
            )
            assert plan.tp_roi_pct[0] >= plan.roundtrip_fee_roi_pct
        else:
            with pytest.raises(FollowerRejected) as exc:
                build_plan(
                    symbol="BTCUSDT",
                    direction=Direction.LONG,
                    levels=_levels(sl_pct),
                    equity_usdt=1000.0,
                    brackets=FREE_BRACKET,
                    cfg=cfg,
                )
            assert exc.value.code == "fee_gate"

    def test_real_exchange_fee_rate_is_used_when_given(self):
        """Bulgu 3: kapı GERÇEK taker oranıyla çalışır (VIP indirimi vb.).

        Borsadan okunan oran %0.02 ise komisyon ROI'si %4'e düşer ve
        BTC örneği (TP1 %4) eşiği ZAR ZOR geçer; config'in %0.05'iyle
        reddedilirdi.
        """
        cfg = _cfg(follower_min_tp1_fee_ratio=1.0)
        plan = build_plan(
            symbol="BTCUSDT",
            direction=Direction.LONG,
            levels=_levels(0.08),
            equity_usdt=1000.0,
            brackets=FREE_BRACKET,
            cfg=cfg,
            fee_rate=0.0002,
        )
        assert plan.roundtrip_fee_roi_pct == pytest.approx(4.0)
        assert roundtrip_fee_roi_pct(100, cfg, 0.0002) == pytest.approx(4.0)

    def test_gate_passes_when_tp1_covers_fees(self):
        plan = build_plan(
            symbol="BTCUSDT",
            direction=Direction.LONG,
            levels=_levels(0.30),
            equity_usdt=1000.0,
            brackets=FREE_BRACKET,
            cfg=_cfg(follower_min_tp1_fee_ratio=1.0),
        )
        assert plan.leverage == 100

    def test_ledger_note_carries_the_fee_ratio(self):
        plan = build_plan(
            symbol="BTCUSDT",
            direction=Direction.LONG,
            levels=_levels(0.08),
            equity_usdt=1000.0,
            brackets=FREE_BRACKET,
            cfg=_cfg(),
        )
        note = plan.ledger_note()
        assert "tp1_roi=4.00" in note
        assert "fee_roi=10.00" in note


class TestLedgerNoteCarriesAlgoProLevels:
    """D20a bulgu 9: defter notu MUTLAK seviyeleri de taşır.

    Restart kurtarması TP fiyatlarını canlı emirlerden okur; düşmüş bir
    emrin fiyatı BAŞKA HİÇBİR YERDE yoktu → eksik bacak yeniden konulamıyordu.
    """

    def test_note_carries_absolute_levels(self):
        plan = build_plan(
            symbol="BTCUSDT",
            direction=Direction.LONG,
            levels=_levels(0.30),
            equity_usdt=1000.0,
            brackets=FREE_BRACKET,
            cfg=_cfg(),
        )
        note = plan.ledger_note()
        for key in ("ap_sl=", "ap_tp1=", "ap_tp2=", "ap_tp3="):
            assert key in note
        parsed = parse_ledger_levels(note)
        assert parsed["sl"] == pytest.approx(plan.levels.stop)
        assert parsed["tp1"] == pytest.approx(plan.levels.tp1)
        assert parsed["tp3"] == pytest.approx(plan.levels.tp3)

    def test_note_stays_within_the_ledger_field(self):
        """`signal_reason` 480 karakterle kırpılır — not sığmalı."""
        plan = build_plan(
            symbol="BTCUSDT",
            direction=Direction.LONG,
            levels=_levels(0.30),
            equity_usdt=1000.0,
            brackets=FREE_BRACKET,
            cfg=_cfg(),
        )
        assert len(plan.ledger_note()) < 300

    @pytest.mark.parametrize(
        "value", [77167.77, 0.00012345, 1.0, 123456.789012, 0.5]
    )
    def test_price_formatting_is_lossless(self, value):
        """`:g` 6 anlamlı haneye kırpar — bir stop seviyesinde bu KAYIPTIR."""
        assert float(format_price(value)) == pytest.approx(value, rel=1e-9)

    def test_broken_note_yields_no_levels(self):
        assert parse_ledger_levels("saçma;metin=1") == {
            "sl": None,
            "tp1": None,
            "tp2": None,
            "tp3": None,
        }
        assert parse_ledger_levels(None)["sl"] is None
