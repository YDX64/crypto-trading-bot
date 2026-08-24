"""D24/A1 — Monte-Carlo permütasyon modülü testleri.

Odak, planın "İKİ ZORUNLU DÜZELTME" maddeleri:
  1) High/Low kelepçesi GERÇEKTEN uygulanıyor ve kelepçe KAPALIYKEN ihlal
     ÖLÇÜLEBİLİR biçimde çıkıyor (yani kelepçe kozmetik değil),
  2) p-değeri metrik başına YÖN kullanıyor — "küçük olan iyi" metriklerde
     upstream'in sabit yönü YANLIŞ sonuç verirdi.
"""

from __future__ import annotations

import math

import pytest

from src.strategies.scalper.permutation import (
    DEFAULT_METRICS,
    HIGHER_IS_BETTER,
    LOWER_IS_BETTER,
    METRIC_DIRECTION,
    aggregate_from,
    clamp_shift_report,
    compute_p_values,
    direction_of,
    merge_clamp_stats,
    permute_candles,
)
from src.strategies.scalper.types import Candle


def _series(n: int = 400, start: float = 100.0, step_ms: int = 300_000) -> list:
    """Deterministik ama gerçekçi bir OHLC serisi (gövde + fitil değişken)."""
    candles = []
    price = start
    for i in range(n):
        drift = math.sin(i / 7.0) * 0.6 + math.cos(i / 3.0) * 0.3
        open_p = price
        close_p = max(1.0, price + drift)
        wick = abs(math.sin(i / 5.0)) * 0.15 + 0.01
        high = max(open_p, close_p) + wick
        low = min(open_p, close_p) - wick
        open_time = 1_700_000_000_000 + i * step_ms
        candles.append(Candle(
            open_time=open_time,
            open=open_p, high=high, low=low, close=close_p,
            volume=10.0 + i,
            close_time=open_time + step_ms - 1,
        ))
        price = close_p
    return candles


class TestPermuteCandles:
    def test_clamp_enforces_ohlc_invariant(self):
        candles = _series()
        out, stats = permute_candles(candles, start_index=50, seed=7, clamp=True)
        assert len(out) == len(candles)
        for c in out:
            assert c.high >= max(c.open, c.close) - 1e-12
            assert c.low <= min(c.open, c.close) + 1e-12
            assert c.high >= c.low
        assert stats["clamp_applied"] is True

    def test_unclamped_run_produces_measurable_violations(self):
        """Kelepçe KAPALIYKEN ihlal çıkmalı — çıkmıyorsa kelepçe gereksizdir
        ve bu testin kendisi anlamsızlaşır (plan bunu kanıt olarak istedi)."""
        candles = _series()
        out, stats = permute_candles(candles, start_index=50, seed=7, clamp=False)
        violations = sum(
            1 for c in out[51:]
            if c.high < max(c.open, c.close) - 1e-12
            or c.low > min(c.open, c.close) + 1e-12
        )
        assert violations > 0
        assert stats["violated_bars"] == violations
        assert stats["violated_bar_pct"] > 0.0
        # Kelepçeli koşu AYNI tohumla AYNI ihlalleri SAYAR ama düzeltir.
        _, clamped_stats = permute_candles(
            candles, start_index=50, seed=7, clamp=True
        )
        assert clamped_stats["violated_bars"] == stats["violated_bars"]
        assert clamped_stats["mean_abs_adjust_pct"] > 0.0
        assert stats["mean_abs_adjust_pct"] == 0.0

    def test_prefix_before_start_index_is_untouched(self):
        candles = _series()
        out, _ = permute_candles(candles, start_index=120, seed=3)
        assert out[:121] == list(candles[:121])
        assert out[121:] != list(candles[121:])

    def test_deterministic_for_same_seed(self):
        candles = _series()
        a, sa = permute_candles(candles, start_index=10, seed=99)
        b, sb = permute_candles(candles, start_index=10, seed=99)
        c, _ = permute_candles(candles, start_index=10, seed=100)
        assert a == b
        assert sa == sb
        assert a != c

    def test_timestamps_and_volume_preserved(self):
        candles = _series()
        out, _ = permute_candles(candles, start_index=5, seed=1)
        assert [x.open_time for x in out] == [x.open_time for x in candles]
        assert [x.close_time for x in out] == [x.close_time for x in candles]
        assert [x.volume for x in out] == [x.volume for x in candles]

    def test_total_drift_is_preserved(self):
        """rel_open ve rel_close AYNI indeks kümesinde karıştırıldığı için
        toplamları korunur → permüte serinin SON kapanışı orijinaliyle aynıdır.
        (Kelepçe yalnız high/low'a dokunur, close'a değil.)"""
        candles = _series()
        out, _ = permute_candles(candles, start_index=0, seed=11)
        assert out[-1].close == pytest.approx(candles[-1].close, rel=1e-9)

    def test_rejects_non_positive_prices(self):
        candles = _series(10)
        broken = list(candles)
        broken[3] = Candle(
            open_time=broken[3].open_time, open=0.0, high=1.0, low=0.0,
            close=1.0, volume=1.0, close_time=broken[3].close_time,
        )
        with pytest.raises(ValueError, match="pozitif OHLC"):
            permute_candles(broken, seed=1)

    def test_too_short_window_returns_copy(self):
        candles = _series(5)
        out, stats = permute_candles(candles, start_index=4, seed=1)
        assert out == list(candles)
        assert stats["permuted_bars"] == 0

    def test_negative_start_index_raises(self):
        with pytest.raises(ValueError):
            permute_candles(_series(10), start_index=-1)

    def test_empty_series(self):
        out, stats = permute_candles([], seed=1)
        assert out == []
        assert stats["bars"] == 0


class TestAggregateFrom:
    def _target_from(self, source, group: int):
        out = []
        for i in range(0, len(source) - group + 1, group):
            chunk = source[i:i + group]
            out.append(Candle(
                open_time=chunk[0].open_time,
                open=chunk[0].open,
                high=max(c.high for c in chunk),
                low=min(c.low for c in chunk),
                close=chunk[-1].close,
                volume=sum(c.volume for c in chunk),
                close_time=chunk[-1].close_time,
            ))
        return out

    def test_exact_aggregation_roundtrip(self):
        source = _series(60)
        target = self._target_from(source, 3)
        rebuilt = aggregate_from(source, target)
        assert rebuilt == target

    def test_uses_permuted_source_values(self):
        source = _series(60)
        target = self._target_from(source, 3)
        permuted, _ = permute_candles(source, start_index=0, seed=5)
        rebuilt = aggregate_from(permuted, target)
        assert rebuilt != target
        assert rebuilt[1].open == pytest.approx(permuted[3].open)
        assert rebuilt[1].close == pytest.approx(permuted[5].close)
        assert rebuilt[1].high == pytest.approx(max(c.high for c in permuted[3:6]))

    def test_uncovered_target_bars_kept_as_is(self):
        source = _series(60)
        target = self._target_from(source, 3)
        # Kaynağın YALNIZ kuyruğu varmış gibi davran: baştaki hedef barlar
        # kapsanmadığı için GERÇEK kalmalı.
        rebuilt = aggregate_from(source[30:], target)
        assert rebuilt[:10] == target[:10]
        assert rebuilt[10:] == target[10:]

    def test_empty_inputs(self):
        assert aggregate_from([], _series(3)) == _series(3)
        assert aggregate_from(_series(3), []) == []


class TestComputePValues:
    def test_higher_is_better_direction(self):
        null = [{"total_pnl": float(v)} for v in range(0, 100)]  # 0..99
        out = compute_p_values({"total_pnl": 95.0}, null, ["total_pnl"])
        row = out["metrics"]["total_pnl"]
        assert row["direction"] == HIGHER_IS_BETTER
        # >= 95 olanlar: 95..99 = 5 tane
        assert row["count_at_least_as_extreme"] == 5
        assert row["p_raw"] == pytest.approx(0.05)
        assert row["p_value"] == pytest.approx(6 / 101, rel=1e-4)

    def test_lower_is_better_direction_is_inverted(self):
        """UPSTREAM HATASI: `p = mean(dist >= real)` max_drawdown'da TERS.
        Gerçek çöküş null'un en iyi (en küçük) %5'indeyse p KÜÇÜK olmalı;
        sabit yönle 0.95 çıkardı."""
        null = [{"max_drawdown": float(v)} for v in range(0, 100)]
        out = compute_p_values({"max_drawdown": 4.0}, null, ["max_drawdown"])
        row = out["metrics"]["max_drawdown"]
        assert row["direction"] == LOWER_IS_BETTER
        assert row["count_at_least_as_extreme"] == 5  # 0..4
        assert row["p_raw"] == pytest.approx(0.05)
        # Sabit (upstream) yönle hesaplansaydı:
        wrong = sum(1 for v in range(0, 100) if v >= 4.0) / 100.0
        assert wrong == pytest.approx(0.96)
        assert row["p_raw"] < wrong

    def test_p_value_never_zero(self):
        null = [{"total_pnl": 0.0} for _ in range(50)]
        row = compute_p_values({"total_pnl": 1000.0}, null, ["total_pnl"])["metrics"]["total_pnl"]
        assert row["p_raw"] == 0.0
        assert row["p_value"] > 0.0
        assert row["p_value"] == pytest.approx(1 / 51, rel=1e-4)

    def test_non_finite_real_gets_no_p_value(self):
        null = [{"profit_factor": 1.0 + i / 10.0} for i in range(20)]
        row = compute_p_values(
            {"profit_factor": float("inf")}, null, ["profit_factor"]
        )["metrics"]["profit_factor"]
        assert "p_value" not in row
        assert "sonlu değil" in row["note"]

    def test_non_finite_null_values_dropped(self):
        null = [{"profit_factor": 1.0}, {"profit_factor": float("inf")}]
        row = compute_p_values(
            {"profit_factor": 2.0}, null, ["profit_factor"]
        )["metrics"]["profit_factor"]
        assert row["n"] == 1
        assert row["non_finite_dropped"] == 1

    def test_unknown_metric_is_skipped_not_guessed(self):
        out = compute_p_values({"avg_duration_min": 5.0}, [{"avg_duration_min": 1.0}],
                               ["avg_duration_min"])
        assert out["metrics"] == {}
        assert out["skipped"][0]["metric"] == "avg_duration_min"
        assert direction_of("avg_duration_min") is None

    def test_missing_metric_in_real_is_skipped(self):
        out = compute_p_values({}, [{"total_pnl": 1.0}], ["total_pnl"])
        assert out["skipped"][0]["reason"] == "gerçek koşuda yok"

    def test_empty_null_distribution(self):
        out = compute_p_values({"total_pnl": 1.0}, [], ["total_pnl"])
        assert out["metrics"]["total_pnl"]["note"].startswith("null dağılımı boş")

    def test_default_metrics_all_have_direction(self):
        for metric in DEFAULT_METRICS:
            assert metric in METRIC_DIRECTION


class TestClampReporting:
    def test_clamp_shift_report_computes_deltas(self):
        clamped = compute_p_values(
            {"total_pnl": 5.0},
            [{"total_pnl": float(v)} for v in range(10)],
            ["total_pnl"],
        )
        unclamped = compute_p_values(
            {"total_pnl": 5.0},
            [{"total_pnl": float(v) - 2.0} for v in range(10)],
            ["total_pnl"],
        )
        rows = clamp_shift_report(clamped, unclamped)["rows"]
        assert rows[0]["metric"] == "total_pnl"
        assert rows[0]["null_mean_delta"] == pytest.approx(2.0)
        assert rows[0]["p_value_delta"] is not None

    def test_merge_clamp_stats_weights_by_bars(self):
        merged = merge_clamp_stats([
            {"permuted_bars": 100, "high_violations": 20, "low_violations": 10,
             "violated_bars": 25, "mean_abs_adjust_pct": 1.0, "max_abs_adjust_pct": 3.0},
            {"permuted_bars": 300, "high_violations": 60, "low_violations": 30,
             "violated_bars": 75, "mean_abs_adjust_pct": 2.0, "max_abs_adjust_pct": 5.0},
        ])
        assert merged["runs"] == 2
        assert merged["permuted_bars"] == 400
        assert merged["high_violation_pct"] == pytest.approx(20.0)
        assert merged["violated_bar_pct"] == pytest.approx(25.0)
        assert merged["mean_abs_adjust_pct"] == pytest.approx(1.75)
        assert merged["max_abs_adjust_pct"] == pytest.approx(5.0)

    def test_merge_clamp_stats_empty(self):
        merged = merge_clamp_stats([])
        assert merged["permuted_bars"] == 0
        assert merged["violated_bar_pct"] == 0.0
