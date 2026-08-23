"""Takipçi seviye motoru (D20) — `src/strategies/follower/levels.py`.

Sözleşme (kullanıcı kararı "AlgoPro ne diyorsa"):
  (a) BİRİNCİL: mesajdaki mutlak SL/TP1/TP2/TP3 aynen kullanılır.
  (b) YEDEK: mesajda SL yoksa `SL = giriş ∓ k×ATR`, `TPk = giriş ± RRk × mesafe`
      ve bu durum `warnings` ile yüzeye çıkar.
Fail-closed: seviye üretilemiyorsa (ATR yok, bant dışı) giriş YAPILMAZ.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.strategies.follower.levels import calibration_record, resolve_levels
from src.strategies.follower.types import (
    LEVEL_SOURCE_ATR,
    LEVEL_SOURCE_MESSAGE,
    LEVEL_SOURCE_MIXED,
    FollowerRejected,
    MessageLevels,
)
from src.strategies.scalper.types import Direction


def _cfg(**overrides):
    base = dict(
        follower_sl_atr_mult=3.0,
        follower_atr_len=14,
        follower_tp_rr1=0.5,
        follower_tp_rr2=1.0,
        follower_tp_rr3=1.5,
        follower_min_sl_pct=0.02,
        follower_max_sl_pct=5.0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class TestMessageLevelsArePrimary:
    def test_real_algopro_short_levels_used_verbatim(self):
        """TV'den ölçülen gerçek SELL örneği — seviyeler AYNEN korunur."""
        levels = resolve_levels(
            entry=77126.08,
            direction=Direction.SHORT,
            message=MessageLevels(
                sl=77167.77, tp1=77105.23, tp2=77084.39, tp3=77063.54
            ),
            atr_value=None,
            cfg=_cfg(),
        )
        assert levels.source == LEVEL_SOURCE_MESSAGE
        assert levels.stop == pytest.approx(77167.77)
        assert levels.tps == pytest.approx((77105.23, 77084.39, 77063.54))
        assert levels.stop_distance == pytest.approx(41.69, abs=1e-6)
        assert levels.sl_pct == pytest.approx(0.0540543, abs=1e-6)
        assert levels.warnings == ()

    def test_real_example_matches_rr_half_one_onehalf(self):
        """AlgoPro'nun kendi TP'leri SL mesafesinin 0.5/1.0/1.5 katıdır.

        ÖLÇÜM NOTU: gerçek gövdede TP1 mesafesi 20.85, 0.5×41.69 = 20.845'tir
        (fark 0.005) — AlgoPro seviyeleri sembolün fiyat hassasiyetine (BTC'de
        2 ondalık) YUVARLIYOR. Bu yüzden tolerans yarım tick'tir; ilişki yine
        de RR 0.5/1.0/1.5'tir (panelin "Live RR .5/1.0/1.5" ifadesiyle
        tutarlı). Bizim tarafımızda bu fark önemsizdir: mesaj seviyeleri
        AYNEN kullanılır, yeniden hesaplanmaz.
        """
        entry, sl = 77126.08, 77167.77
        distance = sl - entry
        assert 77126.08 - 77105.23 == pytest.approx(0.5 * distance, abs=0.01)
        assert 77126.08 - 77084.39 == pytest.approx(1.0 * distance, abs=0.01)
        assert 77126.08 - 77063.54 == pytest.approx(1.5 * distance, abs=0.01)

    def test_message_sl_with_missing_tps_is_mixed(self):
        levels = resolve_levels(
            entry=100.0,
            direction=Direction.LONG,
            message=MessageLevels(sl=99.0),
            atr_value=None,
            cfg=_cfg(),
        )
        assert levels.source == LEVEL_SOURCE_MIXED
        assert levels.stop == pytest.approx(99.0)
        assert levels.tps == pytest.approx((100.5, 101.0, 101.5))

    def test_atr_ignored_when_message_has_sl(self):
        levels = resolve_levels(
            entry=100.0,
            direction=Direction.LONG,
            message=MessageLevels(sl=99.0, tp1=100.5, tp2=101.0, tp3=101.5),
            atr_value=5.0,  # k×ATR çok daha geniş olurdu
            cfg=_cfg(),
        )
        assert levels.stop == pytest.approx(99.0)
        assert levels.source == LEVEL_SOURCE_MESSAGE


class TestAtrFallback:
    def test_atr_rule_long(self):
        levels = resolve_levels(
            entry=100.0,
            direction=Direction.LONG,
            message=MessageLevels(),
            atr_value=0.2,
            cfg=_cfg(),
        )
        assert levels.source == LEVEL_SOURCE_ATR
        assert levels.stop == pytest.approx(99.4)  # 100 - 3*0.2
        assert levels.tps == pytest.approx((100.3, 100.6, 100.9))
        assert any("k×ATR" in w for w in levels.warnings)

    def test_atr_rule_short_is_mirror(self):
        levels = resolve_levels(
            entry=100.0,
            direction=Direction.SHORT,
            message=MessageLevels(),
            atr_value=0.2,
            cfg=_cfg(),
        )
        assert levels.stop == pytest.approx(100.6)
        assert levels.tps == pytest.approx((99.7, 99.4, 99.1))

    def test_rr_multipliers_configurable(self):
        levels = resolve_levels(
            entry=100.0,
            direction=Direction.LONG,
            message=MessageLevels(),
            atr_value=0.2,
            cfg=_cfg(follower_tp_rr1=1.0, follower_tp_rr2=2.0, follower_tp_rr3=3.0),
        )
        assert levels.tps == pytest.approx((100.6, 101.2, 101.8))

    def test_no_atr_and_no_message_rejects(self):
        with pytest.raises(FollowerRejected) as exc:
            resolve_levels(
                entry=100.0,
                direction=Direction.LONG,
                message=MessageLevels(),
                atr_value=None,
                cfg=_cfg(),
            )
        assert exc.value.code == "no_levels"


class TestGuards:
    def test_wrong_side_message_sl_falls_back_to_atr(self):
        levels = resolve_levels(
            entry=100.0,
            direction=Direction.LONG,
            message=MessageLevels(sl=101.0),  # LONG'da stop girişin ÜSTÜNDE
            atr_value=0.2,
            cfg=_cfg(),
        )
        assert levels.stop == pytest.approx(99.4)
        assert any("yanlış tarafında" in w for w in levels.warnings)

    def test_wrong_side_tp_replaced_by_computed(self):
        levels = resolve_levels(
            entry=100.0,
            direction=Direction.LONG,
            message=MessageLevels(sl=99.0, tp1=98.0),
            atr_value=None,
            cfg=_cfg(),
        )
        assert levels.tp1 == pytest.approx(100.5)
        assert any("tp1" in w for w in levels.warnings)

    def test_out_of_order_tp_replaced(self):
        levels = resolve_levels(
            entry=100.0,
            direction=Direction.LONG,
            message=MessageLevels(sl=99.0, tp1=101.0, tp2=100.5, tp3=102.0),
            atr_value=None,
            cfg=_cfg(),
        )
        assert levels.tp1 == pytest.approx(101.0)
        assert levels.tp2 == pytest.approx(101.0)  # hesaplanan (1.0 × 1.0)
        assert any("sıralamayı bozuyor" in w for w in levels.warnings)

    def test_stop_band_too_tight_rejected(self):
        with pytest.raises(FollowerRejected) as exc:
            resolve_levels(
                entry=100.0,
                direction=Direction.LONG,
                message=MessageLevels(sl=99.999),  # %0.001
                atr_value=None,
                cfg=_cfg(),
            )
        assert exc.value.code == "stop_band"

    def test_stop_band_too_wide_rejected(self):
        with pytest.raises(FollowerRejected) as exc:
            resolve_levels(
                entry=100.0,
                direction=Direction.LONG,
                message=MessageLevels(sl=90.0),  # %10
                atr_value=None,
                cfg=_cfg(),
            )
        assert exc.value.code == "stop_band"

    def test_invalid_entry_rejected(self):
        with pytest.raises(FollowerRejected):
            resolve_levels(
                entry=0.0,
                direction=Direction.LONG,
                message=MessageLevels(sl=1.0),
                atr_value=None,
                cfg=_cfg(),
            )


class TestCalibrationRecord:
    def test_deviation_between_message_and_atr_rule(self):
        levels = resolve_levels(
            entry=100.0,
            direction=Direction.LONG,
            message=MessageLevels(sl=99.0),
            atr_value=0.2,  # k×ATR = 0.6 → mesaj mesafesi 1.0 → %66.7 sapma
            cfg=_cfg(),
        )
        record = calibration_record(
            symbol="BTCUSDT",
            direction=Direction.LONG,
            kind="entry",
            ts="2026-08-23T01:00:00Z",
            levels=levels,
            cfg=_cfg(),
        )
        assert record["symbol"] == "BTCUSDT"
        assert record["used"]["sl"] == pytest.approx(99.0)
        assert record["computed"]["sl"] == pytest.approx(99.4)
        assert record["sl_distance_deviation_pct"] == pytest.approx(66.6667, abs=1e-3)

    def test_no_atr_means_no_deviation(self):
        levels = resolve_levels(
            entry=100.0,
            direction=Direction.LONG,
            message=MessageLevels(sl=99.0),
            atr_value=None,
            cfg=_cfg(),
        )
        record = calibration_record(
            symbol="BTCUSDT",
            direction=Direction.LONG,
            kind="entry",
            ts="",
            levels=levels,
            cfg=_cfg(),
        )
        assert record["sl_distance_deviation_pct"] is None
