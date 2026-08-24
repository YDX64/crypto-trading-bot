"""D24 — backtest ölçüm eklentileri (A3 bar-bazlı çöküş, A4 konsantrasyon,
A5 maliyet stresi + giriş gecikmesi).

TEMEL SÖZLEŞME: bu eklentilerin HİÇBİRİ varsayılan davranışı değiştirmez.
`tests/test_golden_backtest.py` nöbetçidir; buradaki testler ayrıca
"varsayılan = eskisiyle birebir" iddiasını doğrudan sınar.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.strategies.scalper.backtest import (
    _SLIPPAGE_RATE,
    BacktestTrade,
    OpenPosition,
    _mark_equity,
    bar_drawdown,
    bar_equity_series,
    compute_stats,
    concentration_stats,
    open_position,
    slippage_rate,
    stressed_cfg,
)
from src.strategies.scalper.types import Candle, Direction, Regime, ScalpSignal

_DAY = 86_400_000


def _trade(
    *, pnl: float, exit_time: int, symbol: str = "BTCUSDT",
    marks=None, roi: float = 1.0,
) -> BacktestTrade:
    return BacktestTrade(
        strategy="C", symbol=symbol, direction="LONG",
        entry_price=100.0, entry_time=exit_time - 600_000,
        exit_price=101.0, exit_time=exit_time,
        quantity=1.0, leverage=20, margin_usdt=5.0, pnl=pnl, roi_pct=roi,
        exit_reason="TRAIL", mae_pct=-5.0, mfe_pct=10.0,
        duration_minutes=10.0, exit_idx=0,
        equity_marks=list(marks or []),
    )


# --------------------------------------------------------------------------
# A3 — bar-bazlı mark-to-market özkaynak eğrisi
# --------------------------------------------------------------------------

class TestBarEquitySeries:
    def test_merges_overlapping_trades_chronologically(self):
        a = _trade(pnl=10.0, exit_time=300, marks=[(100, -5.0), (200, 3.0), (300, 10.0)])
        b = _trade(pnl=-4.0, exit_time=400, symbol="ETHUSDT",
                   marks=[(200, -1.0), (300, -6.0), (400, -4.0)])
        series = bar_equity_series([a, b])
        assert series == [
            (100, -5.0),          # yalnız A açık
            (200, 3.0 - 1.0),     # A + B
            (300, 10.0 - 6.0),    # A kapandı (10 sabit), B dipte
            (400, 10.0 - 4.0),    # A gerçekleşmiş katkısı KORUNUR
        ]

    def test_closed_trade_contribution_is_sticky(self):
        a = _trade(pnl=7.0, exit_time=100, marks=[(100, 7.0)])
        b = _trade(pnl=1.0, exit_time=300, symbol="XRPUSDT",
                   marks=[(200, -3.0), (300, 1.0)])
        series = bar_equity_series([a, b])
        assert series[1] == (200, 4.0)
        assert series[2] == (300, 8.0)

    def test_empty_when_no_marks(self):
        assert bar_equity_series([_trade(pnl=1.0, exit_time=1)]) == []
        assert bar_equity_series([]) == []

    def test_bar_drawdown_peak_starts_at_zero(self):
        worst, at = bar_drawdown([(1, -3.0), (2, 5.0), (3, 1.0), (4, 6.0)])
        assert worst == pytest.approx(4.0)   # tepe 5 -> dip 1
        assert at == 3

    def test_bar_drawdown_empty(self):
        assert bar_drawdown([]) == (0.0, None)


class TestBarDrawdownIsDeeperThanCloseBased:
    def test_intrabar_trough_invisible_to_close_based_metric(self):
        """Bugünkü `max_drawdown` yalnız KAPANIŞLARDA örnekleniyor. İki
        kazançlı işlemde kümülatif PnL hiç düşmez → 0.00 çıkar; oysa
        pozisyonlar bar-içinde belirgin biçimde su altındaydı."""
        a = _trade(pnl=10.0, exit_time=300,
                   marks=[(100, -12.0), (200, -4.0), (300, 10.0)])
        b = _trade(pnl=5.0, exit_time=600, symbol="ETHUSDT",
                   marks=[(400, -8.0), (500, -2.0), (600, 5.0)])
        stats = compute_stats([a, b])
        assert stats["max_drawdown"] == pytest.approx(0.0)
        assert stats["bar_max_drawdown"] == pytest.approx(12.0)
        assert stats["bar_max_drawdown"] > stats["max_drawdown"]
        assert stats["bar_max_drawdown_at"] == 100
        assert stats["bar_equity_points"] == 6

    def test_bar_drawdown_never_shallower_than_close_based(self):
        """Değişmez: aynı taban + daha sık örnekleme → bar-bazlı çöküş
        kapanış-bazlıdan KÜÇÜK OLAMAZ."""
        trades = [
            _trade(pnl=20.0, exit_time=100, marks=[(100, 20.0)]),
            _trade(pnl=-30.0, exit_time=200, marks=[(150, -10.0), (200, -30.0)]),
            _trade(pnl=15.0, exit_time=300, marks=[(250, -2.0), (300, 15.0)]),
        ]
        stats = compute_stats(trades)
        assert stats["max_drawdown"] == pytest.approx(30.0)
        assert stats["bar_max_drawdown"] >= stats["max_drawdown"]

    def test_stats_shape_stable_when_marks_missing(self):
        stats = compute_stats([_trade(pnl=1.0, exit_time=1)])
        assert stats["bar_max_drawdown"] == 0.0
        assert stats["bar_max_drawdown_at"] is None
        assert stats["bar_equity_points"] == 0

    def test_empty_stats_carry_new_keys(self):
        stats = compute_stats([])
        for key in (
            "bar_max_drawdown", "bar_max_drawdown_at", "bar_equity_points",
            "top_symbol", "top_symbol_pnl_share", "top_trade_pnl_share",
            "top_day", "top_day_pnl_share", "distinct_symbols", "distinct_days",
        ):
            assert key in stats


class TestMarkEquity:
    def _pos(self) -> OpenPosition:
        return OpenPosition(
            strategy="C", symbol="BTCUSDT", direction=Direction.LONG,
            stop_price=90.0, entry_idx=0, entry_price=100.0, entry_time=0,
            qty_total=2.0, leverage=10, tp1_price=110.0, tp2_price=120.0,
            tp1_qty=1.0, tp2_qty=0.5, runner_qty=0.5, breakeven_price=100.5,
            current_stop=90.0, regime="UP",
            entry_commission_rate=0.0005, exit_commission_rate=0.0005,
        )

    def _candle(self, close: float, close_time: int) -> Candle:
        return Candle(
            open_time=close_time - 1, open=close, high=close, low=close,
            close=close, volume=1.0, close_time=close_time,
        )

    def test_mark_includes_unrealized_minus_entry_commission(self):
        pos = self._pos()
        _mark_equity(pos, self._candle(105.0, 1000))
        # (105-100)*2 = 10 brüt, giriş komisyonu 0.0005*2*100 = 0.1
        assert pos.equity_marks == [(1000, pytest.approx(9.9))]

    def test_same_close_time_replaces_not_appends(self):
        pos = self._pos()
        _mark_equity(pos, self._candle(105.0, 1000))
        _mark_equity(pos, self._candle(103.0, 1000))
        assert len(pos.equity_marks) == 1
        assert pos.equity_marks[0][1] == pytest.approx(5.9)

    def test_mark_does_not_touch_decision_state(self):
        pos = self._pos()
        before = (pos.current_stop, pos.remaining_qty, pos.tp1_filled,
                  pos.trailing_active, pos.exit_reason, len(pos.legs))
        _mark_equity(pos, self._candle(150.0, 1))
        after = (pos.current_stop, pos.remaining_qty, pos.tp1_filled,
                 pos.trailing_active, pos.exit_reason, len(pos.legs))
        assert before == after


# --------------------------------------------------------------------------
# A4 — konsantrasyon
# --------------------------------------------------------------------------

class TestConcentration:
    def test_symbol_trade_and_day_shares(self):
        base = 1_700_000_000_000 - (1_700_000_000_000 % _DAY)
        trades = [
            _trade(pnl=60.0, exit_time=base + 1000, symbol="BTCUSDT"),
            _trade(pnl=20.0, exit_time=base + 2000, symbol="ETHUSDT"),
            _trade(pnl=20.0, exit_time=base + _DAY + 1000, symbol="ETHUSDT"),
        ]
        out = concentration_stats(trades)
        assert out["top_symbol"] == "BTCUSDT"
        assert out["top_symbol_pnl"] == pytest.approx(60.0)
        assert out["top_symbol_pnl_share"] == pytest.approx(60.0)
        assert out["top_trade_pnl_share"] == pytest.approx(60.0)
        assert out["top_day_pnl"] == pytest.approx(80.0)
        assert out["top_day_pnl_share"] == pytest.approx(80.0)
        assert out["distinct_symbols"] == 2
        assert out["distinct_days"] == 2

    def test_share_undefined_when_total_pnl_not_positive(self):
        trades = [
            _trade(pnl=10.0, exit_time=1_700_000_000_000, symbol="BTCUSDT"),
            _trade(pnl=-30.0, exit_time=1_700_000_100_000, symbol="ETHUSDT"),
        ]
        out = concentration_stats(trades)
        assert out["top_symbol_pnl_share"] is None
        assert out["top_trade_pnl_share"] is None
        assert out["top_day_pnl_share"] is None
        # MUTLAK katkı yine raporlanır — sayı kaybolmasın
        assert out["top_symbol_pnl"] == pytest.approx(10.0)
        assert out["top_trade_pnl"] == pytest.approx(10.0)

    def test_empty(self):
        out = concentration_stats([])
        assert out["top_symbol"] is None
        assert out["distinct_days"] == 0

    def test_wired_into_compute_stats(self):
        trades = [_trade(pnl=5.0, exit_time=1_700_000_000_000, symbol="SOLUSDT")]
        stats = compute_stats(trades)
        assert stats["top_symbol"] == "SOLUSDT"
        assert stats["top_symbol_pnl_share"] == pytest.approx(100.0)


# --------------------------------------------------------------------------
# A5 — maliyet stresi + giriş gecikmesi
# --------------------------------------------------------------------------

@dataclass
class _Cfg:
    scalper_taker_fee_pct: float = 0.05
    scalper_maker_fee_pct: float = 0.02
    scalper_slippage_rate: float = 0.0002
    scalper_min_stop_pct: float = 0.15
    scalper_max_stop_pct: float = 3.0
    scalper_min_rr: float = 0.0
    scalper_entry_mode: str = "taker"
    scalper_maker_fill_timeout_candles: int = 3
    scalper_risk_percentage: float = 2.0
    scalper_leverage: int = 20
    scalper_max_margin_pct: float = 10.0
    scalper_tp1_roi: float = 10.0
    scalper_tp2_roi: float = 25.0
    scalper_tp1_fraction: float = 0.40
    scalper_tp2_fraction: float = 0.20
    scalper_breakeven_buffer_pct: float = 0.05


class TestCostStress:
    def test_multiplier_one_returns_same_object(self):
        cfg = _Cfg()
        assert stressed_cfg(cfg, 1.0) is cfg

    def test_multiplier_scales_fees_and_slippage(self):
        cfg = _Cfg()
        out = stressed_cfg(cfg, 2.0)
        assert out is not cfg
        assert out.scalper_taker_fee_pct == pytest.approx(0.10)
        assert out.scalper_maker_fee_pct == pytest.approx(0.04)
        assert out.scalper_slippage_rate == pytest.approx(0.0004)
        # ORİJİNAL nesne DEĞİŞMEDİ
        assert cfg.scalper_taker_fee_pct == pytest.approx(0.05)

    def test_rejects_non_positive_multiplier(self):
        with pytest.raises(ValueError):
            stressed_cfg(_Cfg(), 0.0)

    def test_works_on_pydantic_settings(self):
        from src.core.config import settings
        out = stressed_cfg(settings, 2.0)
        assert out.scalper_taker_fee_pct == pytest.approx(
            settings.scalper_taker_fee_pct * 2
        )
        assert settings.scalper_taker_fee_pct != out.scalper_taker_fee_pct

    def test_slippage_rate_falls_back_to_constant(self):
        class _Bare:
            pass

        assert slippage_rate(_Bare()) == _SLIPPAGE_RATE
        assert slippage_rate(_Cfg(scalper_slippage_rate=0.001)) == pytest.approx(0.001)

    def test_settings_default_slippage_unchanged(self):
        """Varsayılan DEĞİŞMEDİ — golden backtest ve D#P1 paritesi bu sayıya
        bağlı."""
        from src.core.config import Settings

        assert Settings.model_fields["scalper_slippage_rate"].default == _SLIPPAGE_RATE


class TestEntryDelay:
    def _signal(self) -> ScalpSignal:
        return ScalpSignal(
            strategy="C", symbol="BTCUSDT", direction=Direction.LONG,
            entry_price=100.0, stop_price=99.0, reason="test",
            regime=Regime.UP, atr_5m=1.0, score=1.0,
        )

    def _candles(self) -> list:
        prices = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0]
        out = []
        for i, p in enumerate(prices):
            out.append(Candle(
                open_time=i * 1000, open=p, high=p + 0.5, low=p - 0.5,
                close=p, volume=1.0, close_time=i * 1000 + 999,
            ))
        return out

    def test_default_zero_fills_next_candle_open(self):
        pos = open_position(self._signal(), self._candles(), 0, _Cfg())
        assert pos is not None
        assert pos.entry_idx == 1
        assert pos.entry_price == pytest.approx(101.0 * (1 + _SLIPPAGE_RATE))

    def test_delay_shifts_fill_candle(self):
        pos = open_position(
            self._signal(), self._candles(), 0, _Cfg(), entry_delay_candles=2,
        )
        assert pos is not None
        assert pos.entry_idx == 3
        assert pos.entry_price == pytest.approx(103.0 * (1 + _SLIPPAGE_RATE))

    def test_delay_past_end_of_series_returns_none(self):
        assert open_position(
            self._signal(), self._candles(), 0, _Cfg(), entry_delay_candles=10,
        ) is None

    def test_slippage_comes_from_cfg(self):
        pos = open_position(
            self._signal(), self._candles(), 0, _Cfg(scalper_slippage_rate=0.01),
        )
        assert pos is not None
        assert pos.entry_price == pytest.approx(101.0 * 1.01)

    def test_maker_mode_delay_shifts_scan_window(self):
        cfg = _Cfg(scalper_entry_mode="maker", scalper_maker_fill_timeout_candles=1)
        candles = self._candles()
        # Limit = sinyal mumunun kapanışı (100.0). 1. mum low=100.5 → değmez;
        # gecikmesiz timeout=1 ile dolum YOK.
        assert open_position(self._signal(), candles, 0, cfg) is None
        # 3 mum gecikmeyle tarama 4. muma kayar; orada da low=103.5 → yine yok.
        assert open_position(
            self._signal(), candles, 0, cfg, entry_delay_candles=3,
        ) is None

    def test_maker_mode_delay_can_find_later_touch(self):
        cfg = _Cfg(scalper_entry_mode="maker", scalper_maker_fill_timeout_candles=1)
        candles = list(self._candles())
        # 3. mumu limite değecek biçimde düşür
        candles[3] = Candle(
            open_time=3000, open=103.0, high=103.5, low=99.0, close=103.0,
            volume=1.0, close_time=3999,
        )
        assert open_position(self._signal(), candles, 0, cfg) is None
        pos = open_position(
            self._signal(), candles, 0, cfg, entry_delay_candles=2,
        )
        assert pos is not None
        assert pos.entry_idx == 3
        assert pos.entry_price == pytest.approx(100.0)  # limit, kaymasız


# --------------------------------------------------------------------------
# CLI kablolaması — "bayrak eklendi ama hiç bağlanmadı" hatasını yakalar
# --------------------------------------------------------------------------

class TestCliWiring:
    """`main_async` yolunu AĞSIZ koşar (golden fixture + sahte fetcher).

    Bu testler `_build_arg_parser` ile `run_backtest`/`run_permutation_study`
    arasındaki kabloyu sınar: bir bayrağın parser'a eklenip motora hiç
    geçirilmemesi, birim testleriyle görünmeyen ama raporu SESSİZCE yanlış
    yapan bir hatadır.
    """

    def _args(self, monkeypatch, tmp_path, **overrides):
        import dataclasses

        import src.strategies.scalper.backtest as bt
        from src.core.config import settings
        from test_golden_backtest import (  # type: ignore
            _FIXTURE_DIR, _GoldenCfg, _NetworkForbiddenKlineFetcher,
            _apply_golden_settings,
        )

        _apply_golden_settings(monkeypatch)
        # `main_async` cfg olarak TEKİL `settings`i kullanır; altın koşuyla
        # aynı sonucu üretmesi için altın ayar kümesi o nesneye taşınır
        # (fixture yalnız 5m + 15m taşıyor → tf_regime 15m olmalı, aksi halde
        # sahte fetcher "AĞA düşme" iddiasıyla patlar).
        golden = _GoldenCfg()
        for field in dataclasses.fields(golden):
            value = getattr(golden, field.name)
            current = getattr(settings, field.name, None)
            # Tip uyumu: `scalper_loss_cooldown_minutes` settings'te int,
            # golden dataclass'ta float — model_dump() sırasında pydantic
            # serileştirme uyarısı üretmesin.
            if isinstance(current, int) and not isinstance(current, bool) \
                    and isinstance(value, float):
                value = int(value)
            monkeypatch.setattr(settings, field.name, value, raising=False)
        # `_GoldenCfg`de OLMAYAN ama harness'ın okuduğu alanlar: altın koşuda
        # bunlar `getattr` varsayılanına düşer, burada TEKİL `settings`ten
        # gelirdi → sunucu `.env`'i (ör. SCALPER_MARKET_GATE=true) testi ağa
        # düşürürdü. Açıkça sabitlenir: test operatörün .env'inden BAĞIMSIZ.
        for name, value in (
            ("scalper_market_gate", False),
            ("scalper_structure_gate", False),
            ("scalper_structure_exit", "off"),
            ("scalper_max_positions", 3),
            ("scalper_slippage_rate", _SLIPPAGE_RATE),
        ):
            monkeypatch.setattr(settings, name, value, raising=False)
        monkeypatch.setattr(bt, "KlineFetcher", _NetworkForbiddenKlineFetcher)
        monkeypatch.chdir(tmp_path)
        argv = [
            "--symbols", "BTCUSDT,ETHUSDT", "--strategies", "C",
            "--start", "2026-08-07", "--end", "2026-08-10",
            "--cache-dir", str(_FIXTURE_DIR),
        ]
        for key, value in overrides.items():
            flag = "--" + key.replace("_", "-")
            argv.append(flag)
            if value is not None:
                argv.append(str(value))
        return bt._build_arg_parser().parse_args(argv)

    def _report(self, tmp_path):
        import json
        from pathlib import Path

        reports = sorted(Path(tmp_path, "logs").glob("backtest_*.json"))
        assert reports, "JSON rapor yazılmadı"
        return json.loads(reports[-1].read_text(encoding="utf-8"))

    @pytest.mark.asyncio
    async def test_default_run_records_neutral_cost_model(self, monkeypatch, tmp_path):
        import src.strategies.scalper.backtest as bt

        await bt.main_async(self._args(monkeypatch, tmp_path))
        report = self._report(tmp_path)
        cost = report["provenance"]["cost_model"]
        assert cost["stress_multiplier"] == 1.0
        assert cost["entry_delay_candles"] == 0
        assert cost["slippage_rate"] == pytest.approx(_SLIPPAGE_RATE)
        assert report["permutation"] is None
        # Varsayılan koşu ALTIN sayıları üretmeli (CLI yolu da parite içinde)
        assert report["overall"]["trades"] == 2
        assert round(report["overall"]["total_pnl"], 2) == 26.77
        assert report["overall"]["bar_max_drawdown"] > report["overall"]["max_drawdown"]

    @pytest.mark.asyncio
    async def test_fee_stress_flag_reaches_the_engine(self, monkeypatch, tmp_path):
        import src.strategies.scalper.backtest as bt
        from src.core.config import settings

        args = self._args(monkeypatch, tmp_path, fee_stress=None)
        await bt.main_async(args)
        report = self._report(tmp_path)
        cost = report["provenance"]["cost_model"]
        assert cost["stress_multiplier"] == 2.0
        assert cost["slippage_rate"] == pytest.approx(_SLIPPAGE_RATE * 2)
        assert cost["taker_fee_pct"] == pytest.approx(settings.scalper_taker_fee_pct * 2)
        # Provenance'taki ayar anlık görüntüsü de STRES değerini taşımalı
        assert report["provenance"]["scalper_config"]["scalper_taker_fee_pct"] == (
            pytest.approx(settings.scalper_taker_fee_pct * 2)
        )
        # Canlı `settings` TEKİL nesnesi DEĞİŞMEDİ
        assert settings.scalper_slippage_rate == pytest.approx(_SLIPPAGE_RATE)

    @pytest.mark.asyncio
    async def test_entry_delay_flag_reaches_the_engine(self, monkeypatch, tmp_path):
        import src.strategies.scalper.backtest as bt

        await bt.main_async(
            self._args(monkeypatch, tmp_path, entry_delay_candles=2)
        )
        report = self._report(tmp_path)
        assert report["provenance"]["cost_model"]["entry_delay_candles"] == 2
        # Gecikme dolum fiyatını değiştirir → altın sayılar artık geçerli DEĞİL
        assert round(report["overall"]["total_pnl"], 2) != 26.77

    @pytest.mark.asyncio
    async def test_permutations_flag_produces_report_block(self, monkeypatch, tmp_path):
        import src.strategies.scalper.backtest as bt

        args = self._args(
            monkeypatch, tmp_path, permutations=3, permutation_seed=42,
            permutation_metrics="total_pnl,bar_max_drawdown",
        )
        await bt.main_async(args)
        study = self._report(tmp_path)["permutation"]
        assert study["permutations"] == 3
        assert study["seed"] == 42
        assert study["metrics"] == ["total_pnl", "bar_max_drawdown"]
        assert study["result"]["metrics"]["total_pnl"]["n"] == 3
        assert 0.0 < study["result"]["metrics"]["total_pnl"]["p_value"] <= 1.0
        # Kelepçe ÖLÇÜLDÜ ve gerçekten iş yaptı
        assert study["clamp"]["violated_bar_pct"] > 0.0
        assert study["clamp"]["mean_abs_adjust_pct"] > 0.0
        assert "KOŞULLU null" in study["null_scope"]
        assert "clamp_audit" not in study

    @pytest.mark.asyncio
    async def test_clamp_audit_flag_adds_shift_table(self, monkeypatch, tmp_path):
        import src.strategies.scalper.backtest as bt

        args = self._args(
            monkeypatch, tmp_path, permutations=3, permutation_seed=42,
            permutation_metrics="total_pnl", permutation_clamp_audit=None,
        )
        await bt.main_async(args)
        study = self._report(tmp_path)["permutation"]
        audit = study["clamp_audit"]
        assert audit["shift"]["rows"][0]["metric"] == "total_pnl"
        # Kelepçesiz koşu AYNI ihlalleri sayar ama DÜZELTMEZ
        assert audit["unclamped_violation_stats"]["violated_bar_pct"] == (
            pytest.approx(study["clamp"]["violated_bar_pct"])
        )
        assert audit["unclamped_violation_stats"]["mean_abs_adjust_pct"] == 0.0

    def test_permutation_study_is_noop_when_disabled(self):
        from src.strategies.scalper.backtest import run_permutation_study

        out = run_permutation_study({}, [], "C", None, {}, permutations=0)
        assert out["permutations"] == 0
        assert "koşulmadı" in out["note"]

    def test_report_prints_risk_concentration_for_empty_run(self, capsys):
        from src.strategies.scalper.backtest import print_report

        print_report([])
        out = capsys.readouterr().out
        assert "RİSK YOĞUNLUĞU" in out
        assert "bar-bazlı ölçüm YOK" in out
