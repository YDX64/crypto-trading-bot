"""Genel deterministik giriş kapıları (2026-09-03, `entry_gates.py`) testleri.

Beş katman (tests/test_market_gate.py ile aynı düzen):
  1. SAF fonksiyonlar — hücre / UTC saat / ATR% bandı: kapalı = ASLA
     bloklamaz; her kapının sınır değerleri (dahil/hariç); gece yarısını
     saran saat aralığı; ATR yok → fail-open; sabit sıra (hücre → saat → ATR).
  2. `Settings` doğrulayıcısı — geçersiz token YALNIZ alan doluyken fail-fast;
     varsayılanlar KAPALI; env parse.
  3. Canlı motor — `_evaluate_symbol` (C taraması VE TV dış sinyali AYNI
     kapıdan geçer): ret + niyet kaydı (`intent.REASON_*_GATE`), kapalıyken
     akış `apply_stop_policy`'ye ulaşır, istisna fail-OPEN.
  4. Harness — `simulate_symbol`: `missed_counter` anahtarları yalnız kapı
     AÇIKKEN ve reddettiğinde; kapalıyken sözlüğe anahtar bile eklenmez ve
     çıktı, alanları hiç olmayan bir cfg ile BİREBİR aynıdır (fail-CLOSED:
     hatalı ayar istisna yükseltir).
  5. **Parite (CLAUDE.md kural 2, DECISIONS P1)** — motor ve harness AYNI
     fonksiyon NESNESİNİ (`is`), AYNI argümanlarla (spy) çağırır ve aynı
     kararı üretir: zaman kaynağı = son KAPANMIŞ giriş mumunun close_time'ı,
     ATR% = HAM sinyal (apply_stop_policy ÖNCESİ).

Ek: env.example bu değişkenleri YALNIZ yorumlu (kapalı) taşır — golden
backtest'in (tests/test_golden_backtest.py) değişmeden geçmesinin ön koşulu.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import pytest

import src.strategies.scalper.backtest as backtest_module
import src.strategies.scalper.engine as engine_module
from src.core.config import Settings
from src.strategies.scalper import entry_gates as entry_gates_module
from src.strategies.scalper import intent
from src.strategies.scalper.backtest import simulate_symbol
from src.strategies.scalper.engine import ScalperEngine
from src.strategies.scalper.entry_gates import (
    REASON_ATR,
    REASON_CELL,
    REASON_ENV_VARS,
    REASON_HOUR,
    atr_gate_blocks,
    atr_gate_enabled,
    atr_pct_of,
    cell_gate_blocks,
    cell_gate_enabled,
    entry_gate_detail,
    entry_gates_enabled,
    evaluate_entry_gates,
    format_entry_gate_detail,
    hour_gate_blocks,
    hour_gate_enabled,
    hour_in_ranges,
    parse_blocked_cells,
    parse_hour_ranges,
    utc_hour_of,
    validate_entry_gate_settings,
)
from src.strategies.scalper.types import (
    Candle,
    Direction,
    Regime,
    ScalpSignal,
    StrategyContext,
)
from src.trading.symbol_reservations import symbol_reservations

_DAY_MS = 86_400_000
_HOUR_MS = 3_600_000
_MIN_MS = 60_000

_ENV_KEYS = (
    "SCALPER_C_BLOCKED_CELLS",
    "SCALPER_ENTRY_BLOCK_HOURS_UTC",
    "SCALPER_MIN_ATR_PCT",
    "SCALPER_MAX_ATR_PCT",
)


@dataclass
class _GateCfg:
    """Kapı ayarlarını taşıyan minimal cfg (pydantic'e gerek yok).
    Varsayılanlar = üretim varsayılanları = HEPSİ KAPALI."""

    scalper_c_blocked_cells: str = ""
    scalper_entry_block_hours_utc: str = ""
    scalper_min_atr_pct: float = 0.0
    scalper_max_atr_pct: float = 0.0


def _ms(day: int, hour: int, minute: int = 0, second: int = 0, millis: int = 0) -> int:
    return day * _DAY_MS + hour * _HOUR_MS + minute * _MIN_MS + second * 1000 + millis


# ==========================================================================
# 0) Sabitler — entry_gates ↔ intent aynı dizeler
# ==========================================================================

class TestConstants:
    def test_reason_strings_match_intent_module(self):
        """Motor niyet defterine `intent.REASON_*_GATE` yazar, harness
        `missed_counter`a `entry_gates.REASON_*` — ikisi AYNI dize olmalı."""
        assert REASON_CELL == intent.REASON_CELL_GATE == "cell_gate"
        assert REASON_HOUR == intent.REASON_HOUR_GATE == "hour_gate"
        assert REASON_ATR == intent.REASON_ATR_GATE == "atr_gate"

    def test_reasons_are_known_and_labelled(self):
        for name in (REASON_CELL, REASON_HOUR, REASON_ATR):
            assert name in intent.KNOWN_REASONS
            assert intent.REASON_LABELS[name].strip()
            assert REASON_ENV_VARS[name]

    def test_all_gates_off_by_default_in_minimal_cfg(self):
        assert entry_gates_enabled(_GateCfg()) is False
        assert entry_gates_enabled(SimpleNamespace()) is False


# ==========================================================================
# 1) SAF fonksiyonlar
# ==========================================================================

class TestCellGate:
    def test_parse_normalises_case_and_whitespace(self):
        cells = parse_blocked_cells(" range : short , UP:long ,, ")
        assert cells == frozenset({("RANGE", "SHORT"), ("UP", "LONG")})

    @pytest.mark.parametrize("raw", ["", "   ", None])
    def test_parse_empty_is_empty(self, raw):
        assert parse_blocked_cells(raw) == frozenset()

    @pytest.mark.parametrize(
        "raw",
        ["RANGE:SHRT", "RANG:SHORT", "RANGE", "RANGE:SHORT:LONG", "RANGE-SHORT",
         "UP:LONG,SIDEWAYS:LONG", ":LONG", "UP:"],
    )
    def test_parse_invalid_token_raises(self, raw):
        with pytest.raises(ValueError):
            parse_blocked_cells(raw)

    def test_all_four_regimes_accepted(self):
        cells = parse_blocked_cells("UP:LONG,DOWN:LONG,RANGE:LONG,UNKNOWN:LONG")
        assert {r for r, _ in cells} == {"UP", "DOWN", "RANGE", "UNKNOWN"}

    def test_off_never_blocks(self):
        cfg = _GateCfg()
        assert cell_gate_enabled(cfg) is False
        for regime in Regime:
            for direction in Direction:
                assert cell_gate_blocks(regime, direction, cfg) is False

    def test_missing_field_is_off(self):
        assert cell_gate_blocks(Regime.RANGE, Direction.SHORT, SimpleNamespace()) is False

    def test_blocks_only_listed_cell(self):
        cfg = _GateCfg(scalper_c_blocked_cells="RANGE:SHORT,UP:LONG")
        assert cell_gate_enabled(cfg) is True
        assert cell_gate_blocks(Regime.RANGE, Direction.SHORT, cfg) is True
        assert cell_gate_blocks(Regime.UP, Direction.LONG, cfg) is True
        # Listede olmayan hücreler serbest — rejim kapısının işi burada YOK.
        assert cell_gate_blocks(Regime.RANGE, Direction.LONG, cfg) is False
        assert cell_gate_blocks(Regime.UP, Direction.SHORT, cfg) is False
        assert cell_gate_blocks(Regime.DOWN, Direction.SHORT, cfg) is False
        assert cell_gate_blocks(Regime.UNKNOWN, Direction.LONG, cfg) is False

    def test_enum_and_string_inputs_are_equivalent(self):
        cfg = _GateCfg(scalper_c_blocked_cells="range:short")
        assert cell_gate_blocks("RANGE", "SHORT", cfg) is True
        assert cell_gate_blocks(Regime.RANGE, "short", cfg) is True
        assert cell_gate_blocks("range", Direction.SHORT, cfg) is True

    def test_invalid_config_raises_at_gate_time(self):
        """Harness fail-CLOSED gerekçesi: hatalı ayar sessiz 'kapalı' olmaz."""
        with pytest.raises(ValueError):
            cell_gate_blocks(Regime.RANGE, Direction.SHORT, _GateCfg(scalper_c_blocked_cells="RANGE:SHRT"))


class TestHourGate:
    def test_utc_hour_of(self):
        assert utc_hour_of(0) == 0
        assert utc_hour_of(_HOUR_MS) == 1
        assert utc_hour_of(_HOUR_MS - 1) == 0
        assert utc_hour_of(_DAY_MS - 1) == 23
        assert utc_hour_of(_DAY_MS) == 0
        # 2026-08-07 13:37:00 UTC = 1785764220000 ms
        assert utc_hour_of(1_785_764_220_000) == 13

    def test_five_minute_candle_close_time_maps_to_its_own_hour(self):
        """04:55-05:00 mumunun close_time'ı 04:59:59.999 → saat 4 (5 DEĞİL).
        Post-hoc taramanın aynı tabanı kullanması gerekir (modül docstring)."""
        close_time = _ms(0, 5) - 1
        assert utc_hour_of(close_time) == 4

    def test_parse_ranges(self):
        assert parse_hour_ranges("0-6,22-24") == ((0, 6), (22, 24))
        assert parse_hour_ranges(" 22 - 3 ") == ((22, 3),)
        assert parse_hour_ranges("0-24") == ((0, 24),)

    @pytest.mark.parametrize("raw", ["", "  ", None])
    def test_parse_empty_is_empty(self, raw):
        assert parse_hour_ranges(raw) == ()

    @pytest.mark.parametrize(
        "raw",
        ["6", "6-6", "24-3", "25-26", "0-25", "abc", "-1-3", "3-", "-3", "0:6", "1-2-3"],
    )
    def test_parse_invalid_raises(self, raw):
        with pytest.raises(ValueError):
            parse_hour_ranges(raw)

    def test_start_inclusive_end_exclusive(self):
        ranges = parse_hour_ranges("0-6,22-24")
        assert hour_in_ranges(0, ranges) is True
        assert hour_in_ranges(5, ranges) is True
        assert hour_in_ranges(6, ranges) is False
        assert hour_in_ranges(21, ranges) is False
        assert hour_in_ranges(22, ranges) is True
        assert hour_in_ranges(23, ranges) is True

    def test_midnight_wrap(self):
        ranges = parse_hour_ranges("22-3")
        for hour in (22, 23, 0, 1, 2):
            assert hour_in_ranges(hour, ranges) is True, hour
        for hour in (3, 4, 12, 21):
            assert hour_in_ranges(hour, ranges) is False, hour

    def test_off_never_blocks(self):
        cfg = _GateCfg()
        assert hour_gate_enabled(cfg) is False
        for hour in range(24):
            assert hour_gate_blocks(_ms(3, hour, 4, 59, 999), cfg) is False

    def test_blocks_by_close_time_hour(self):
        cfg = _GateCfg(scalper_entry_block_hours_utc="0-6,22-24")
        assert hour_gate_enabled(cfg) is True
        # Gün 3, 05:04:59.999 (05:00-05:05 mumu) → saat 5 → yasak.
        assert hour_gate_blocks(_ms(3, 5, 4, 59, 999), cfg) is True
        # 05:55-06:00 mumu → close 05:59:59.999 → saat 5 → hâlâ yasak.
        assert hour_gate_blocks(_ms(3, 6) - 1, cfg) is True
        # 06:00-06:05 mumu → close 06:04:59.999 → saat 6 → serbest (bitiş HARİÇ).
        assert hour_gate_blocks(_ms(3, 6, 4, 59, 999), cfg) is False
        assert hour_gate_blocks(_ms(3, 21, 59, 59, 999), cfg) is False
        assert hour_gate_blocks(_ms(3, 22, 4, 59, 999), cfg) is True
        assert hour_gate_blocks(_ms(3, 23, 59, 59, 999), cfg) is True

    def test_midnight_wrap_blocks_across_day_boundary(self):
        cfg = _GateCfg(scalper_entry_block_hours_utc="22-3")
        assert hour_gate_blocks(_ms(3, 23, 59, 59, 999), cfg) is True
        assert hour_gate_blocks(_ms(4, 0, 4, 59, 999), cfg) is True
        assert hour_gate_blocks(_ms(4, 2, 59, 59, 999), cfg) is True
        assert hour_gate_blocks(_ms(4, 3, 4, 59, 999), cfg) is False

    @pytest.mark.parametrize("bad", [None, "abc", float("nan"), True])
    def test_unresolvable_time_is_fail_open(self, bad):
        cfg = _GateCfg(scalper_entry_block_hours_utc="0-24")
        assert hour_gate_blocks(bad, cfg) is False

    def test_invalid_config_raises_at_gate_time(self):
        with pytest.raises(ValueError):
            hour_gate_blocks(_ms(3, 5), _GateCfg(scalper_entry_block_hours_utc="25-26"))


class TestAtrGate:
    def test_atr_pct_formula_matches_setups(self):
        # setups.apply_stop_policy: atr_pct = atr_5m / entry_price * 100
        assert atr_pct_of(1.0, 100.0) == pytest.approx(1.0)
        assert atr_pct_of(0.25, 50.0) == pytest.approx(0.5)

    @pytest.mark.parametrize(
        "atr,price",
        [(None, 100.0), (0.0, 100.0), (-1.0, 100.0), (float("nan"), 100.0),
         (float("inf"), 100.0), (1.0, 0.0), (1.0, None), (1.0, -5.0), ("x", 100.0),
         (True, 100.0)],
    )
    def test_unmeasurable_atr_is_none_not_zero(self, atr, price):
        assert atr_pct_of(atr, price) is None

    def test_off_never_blocks(self):
        cfg = _GateCfg()
        assert atr_gate_enabled(cfg) is False
        assert atr_gate_blocks(0.0001, 100.0, cfg) is False   # %0.0001
        assert atr_gate_blocks(50.0, 100.0, cfg) is False     # %50

    def test_min_only(self):
        cfg = _GateCfg(scalper_min_atr_pct=0.5)
        assert atr_gate_enabled(cfg) is True
        assert atr_gate_blocks(0.4, 100.0, cfg) is True    # %0.4 < %0.5 → RED
        assert atr_gate_blocks(0.5, 100.0, cfg) is False   # tam eşik serbest
        assert atr_gate_blocks(0.6, 100.0, cfg) is False
        assert atr_gate_blocks(50.0, 100.0, cfg) is False  # max kapalı → üst sınır yok

    def test_max_only(self):
        cfg = _GateCfg(scalper_max_atr_pct=2.0)
        assert atr_gate_blocks(2.1, 100.0, cfg) is True    # %2.1 > %2.0 → RED
        assert atr_gate_blocks(2.0, 100.0, cfg) is False   # tam eşik serbest
        assert atr_gate_blocks(1.9, 100.0, cfg) is False
        assert atr_gate_blocks(0.0001, 100.0, cfg) is False  # min kapalı → alt sınır yok

    def test_band(self):
        cfg = _GateCfg(scalper_min_atr_pct=0.5, scalper_max_atr_pct=2.0)
        assert atr_gate_blocks(0.4, 100.0, cfg) is True
        assert atr_gate_blocks(1.0, 100.0, cfg) is False
        assert atr_gate_blocks(2.5, 100.0, cfg) is True

    @pytest.mark.parametrize("atr", [None, 0.0, -1.0, float("nan")])
    def test_missing_atr_is_fail_open(self, atr):
        cfg = _GateCfg(scalper_min_atr_pct=0.5, scalper_max_atr_pct=2.0)
        assert atr_gate_blocks(atr, 100.0, cfg) is False

    def test_missing_price_is_fail_open(self):
        cfg = _GateCfg(scalper_min_atr_pct=0.5)
        assert atr_gate_blocks(1.0, 0.0, cfg) is False
        assert atr_gate_blocks(1.0, None, cfg) is False


class TestEvaluateEntryGates:
    def test_all_off_returns_none_without_touching_inputs(self):
        # Girdiler bilerek ANLAMSIZ: kapalıyken hiçbiri okunmamalı.
        assert evaluate_entry_gates(object(), object(), "x", "y", "z", _GateCfg()) is None
        assert evaluate_entry_gates(Regime.RANGE, Direction.SHORT, 0, 1.0, 100.0, SimpleNamespace()) is None

    def test_order_cell_then_hour_then_atr(self):
        cfg = _GateCfg(
            scalper_c_blocked_cells="RANGE:SHORT",
            scalper_entry_block_hours_utc="0-24",
            scalper_min_atr_pct=5.0,
        )
        # Üçü de tetikte → hücre kazanır.
        assert evaluate_entry_gates(Regime.RANGE, Direction.SHORT, _ms(3, 5), 1.0, 100.0, cfg) == REASON_CELL
        # Hücre geçerse saat.
        assert evaluate_entry_gates(Regime.RANGE, Direction.LONG, _ms(3, 5), 1.0, 100.0, cfg) == REASON_HOUR
        # Saat de geçerse ATR.
        cfg2 = _GateCfg(scalper_c_blocked_cells="RANGE:SHORT", scalper_entry_block_hours_utc="0-6", scalper_min_atr_pct=5.0)
        assert evaluate_entry_gates(Regime.RANGE, Direction.LONG, _ms(3, 12), 1.0, 100.0, cfg2) == REASON_ATR
        # Hepsi geçerse None.
        assert evaluate_entry_gates(Regime.RANGE, Direction.LONG, _ms(3, 12), 10.0, 100.0, cfg2) is None

    def test_detail_and_format(self):
        cfg = _GateCfg(scalper_entry_block_hours_utc="0-6", scalper_min_atr_pct=0.5, scalper_max_atr_pct=2.0)
        d = entry_gate_detail(REASON_CELL, Regime.RANGE, Direction.SHORT, _ms(3, 5), 1.0, 100.0, cfg)
        assert d == {"gate": REASON_CELL, "regime": "RANGE", "direction": "SHORT"}
        assert "RANGE:SHORT" in format_entry_gate_detail(d)
        d = entry_gate_detail(REASON_HOUR, Regime.RANGE, Direction.SHORT, _ms(3, 5, 4, 59, 999), 1.0, 100.0, cfg)
        assert d["hour_utc"] == 5 and d["block_hours"] == "0-6"
        assert "5" in format_entry_gate_detail(d)
        d = entry_gate_detail(REASON_ATR, Regime.RANGE, Direction.SHORT, _ms(3, 5), 0.25, 100.0, cfg)
        assert d["atr_pct"] == pytest.approx(0.25) and d["min_atr_pct"] == 0.5 and d["max_atr_pct"] == 2.0
        assert "0.250" in format_entry_gate_detail(d)
        d = entry_gate_detail(REASON_ATR, Regime.RANGE, Direction.SHORT, _ms(3, 5), None, 100.0, cfg)
        assert d["atr_pct"] is None  # "ölçülmedi" ≠ 0.0
        assert "?" in format_entry_gate_detail(d)


class TestValidateSettingsPure:
    def test_defaults_pass(self):
        validate_entry_gate_settings(_GateCfg())
        validate_entry_gate_settings(SimpleNamespace())

    def test_valid_values_pass(self):
        validate_entry_gate_settings(_GateCfg(
            scalper_c_blocked_cells="RANGE:SHORT,UP:LONG",
            scalper_entry_block_hours_utc="0-6,22-3",
            scalper_min_atr_pct=0.3, scalper_max_atr_pct=3.0,
        ))

    def test_only_one_atr_bound_is_fine(self):
        validate_entry_gate_settings(_GateCfg(scalper_min_atr_pct=0.3))
        validate_entry_gate_settings(_GateCfg(scalper_max_atr_pct=3.0))

    @pytest.mark.parametrize(
        "kw",
        [dict(scalper_c_blocked_cells="RANGE:SHRT"),
         dict(scalper_entry_block_hours_utc="25-26"),
         dict(scalper_entry_block_hours_utc="6"),
         dict(scalper_min_atr_pct=2.0, scalper_max_atr_pct=1.0),
         dict(scalper_min_atr_pct=1.0, scalper_max_atr_pct=1.0),
         dict(scalper_min_atr_pct=-0.1),
         dict(scalper_max_atr_pct=-0.1),
         dict(scalper_min_atr_pct="abc")],
    )
    def test_invalid_raises(self, kw):
        with pytest.raises(ValueError):
            validate_entry_gate_settings(_GateCfg(**kw))


# ==========================================================================
# 2) Settings — env parse + varsayılanlar KAPALI + yalnız doluyken fail-fast
# ==========================================================================

_REQUIRED_SETTINGS: Dict[str, str] = dict(
    binance_api_key="x", binance_api_secret="x",
    telegram_bot_token="x", telegram_chat_id="x",
    openai_api_key="x", gemini_api_key="x", deepseek_api_key="x",
    jwt_secret="x",
    binance_base_url="https://testnet.binancefuture.com",
)


class TestEntryGateSettings:
    @staticmethod
    def _settings(monkeypatch, env: Optional[Dict[str, str]] = None, **fields: Any) -> Settings:
        """Env değişkenlerinden parse — süreç env'i (sunucuda .env kapı AÇIK
        olabilir) varsayılan testini kirletmesin (tests/test_market_gate.py
        deseni)."""
        for key in list(os.environ):
            if key.upper() in _ENV_KEYS:
                monkeypatch.delenv(key, raising=False)
        for key, value in (env or {}).items():
            monkeypatch.setenv(key, value)
        return Settings(_env_file=None, **_REQUIRED_SETTINGS, **fields)

    def test_defaults_are_off(self, monkeypatch):
        s = self._settings(monkeypatch)
        assert s.scalper_c_blocked_cells == ""
        assert s.scalper_entry_block_hours_utc == ""
        assert s.scalper_min_atr_pct == 0.0
        assert s.scalper_max_atr_pct == 0.0
        assert entry_gates_enabled(s) is False
        assert evaluate_entry_gates(Regime.RANGE, Direction.SHORT, _ms(3, 2), 0.01, 100.0, s) is None

    def test_env_overrides_are_parsed(self, monkeypatch):
        s = self._settings(monkeypatch, env={
            "SCALPER_C_BLOCKED_CELLS": "RANGE:SHORT,UP:LONG",
            "SCALPER_ENTRY_BLOCK_HOURS_UTC": "0-6,22-24",
            "SCALPER_MIN_ATR_PCT": "0.3",
            "SCALPER_MAX_ATR_PCT": "2.5",
        })
        assert s.scalper_c_blocked_cells == "RANGE:SHORT,UP:LONG"
        assert s.scalper_entry_block_hours_utc == "0-6,22-24"
        assert s.scalper_min_atr_pct == 0.3
        assert s.scalper_max_atr_pct == 2.5
        assert evaluate_entry_gates(Regime.RANGE, Direction.SHORT, _ms(3, 12), 1.0, 100.0, s) == REASON_CELL
        assert evaluate_entry_gates(Regime.RANGE, Direction.LONG, _ms(3, 2), 1.0, 100.0, s) == REASON_HOUR
        assert evaluate_entry_gates(Regime.RANGE, Direction.LONG, _ms(3, 12), 0.1, 100.0, s) == REASON_ATR
        assert evaluate_entry_gates(Regime.RANGE, Direction.LONG, _ms(3, 12), 1.0, 100.0, s) is None

    @pytest.mark.parametrize(
        "env",
        [{"SCALPER_C_BLOCKED_CELLS": "RANGE:SHRT"},
         {"SCALPER_C_BLOCKED_CELLS": "SIDEWAYS:LONG"},
         {"SCALPER_ENTRY_BLOCK_HOURS_UTC": "25-26"},
         {"SCALPER_ENTRY_BLOCK_HOURS_UTC": "6-6"},
         {"SCALPER_MIN_ATR_PCT": "2", "SCALPER_MAX_ATR_PCT": "1"},
         {"SCALPER_MIN_ATR_PCT": "1", "SCALPER_MAX_ATR_PCT": "1"},
         {"SCALPER_MIN_ATR_PCT": "-1"}],
    )
    def test_invalid_value_fails_fast_when_set(self, monkeypatch, env):
        with pytest.raises(ValueError):
            self._settings(monkeypatch, env=env)

    def test_field_kwargs_are_validated_too(self, monkeypatch):
        with pytest.raises(ValueError):
            self._settings(monkeypatch, scalper_c_blocked_cells="RANGE:SHRT")
        s = self._settings(monkeypatch, scalper_c_blocked_cells="RANGE:SHORT")
        assert cell_gate_blocks(Regime.RANGE, Direction.SHORT, s) is True

    def test_env_example_ships_disabled(self):
        """env.example bu değişkenleri YALNIZ yorumlu taşır: `.env =
        env.example` ile koşan testler ve golden backtest değişmez."""
        text = Path("env.example").read_text(encoding="utf-8")
        for key in _ENV_KEYS:
            assert key in text, f"{key} env.example'da belgelenmeli"
            for line in text.splitlines():
                stripped = line.strip()
                assert not stripped.startswith(f"{key}="), f"{key} env.example'da AÇIK bırakılmış: {line!r}"
        s = Settings(_env_file="env.example")
        assert entry_gates_enabled(s) is False


# ==========================================================================
# Ortak veri kurgusu — motor ve harness AYNI mumları görür
# ==========================================================================

def _minute_candles(closes: List[float], start_ms: int, step_ms: int = _MIN_MS) -> List[Candle]:
    out: List[Candle] = []
    for n, close in enumerate(closes):
        start = start_ms + n * step_ms
        out.append(Candle(
            open_time=start, open=close, high=close, low=close,
            close=close, volume=1.0, close_time=start + step_ms - 1,
        ))
    return out


# Gün 4, 03:00 UTC'den başlayan 1 dakikalık mumlar → tüm close_time'lar saat 3.
_ENTRY_START = _ms(4, 3)
_CANDLES = _minute_candles([100.0, 100.5, 101.0, 100.5, 100.0, 100.5], _ENTRY_START)
_DECISION_IDX = len(_CANDLES) - 2            # son mum dolum mumu (harness kuralı)
_DECISION_MS = _CANDLES[_DECISION_IDX].close_time
_ATR_5M = 1.0                                 # stub sinyal: ATR% = 1.0 / ~100


class _FlatFetcher:
    """`KlineFetcher` yerine: her (sembol, dilim) için AYNI seriyi verir."""

    def __init__(self, candles: List[Candle]):
        self.candles = list(candles)

    async def get_klines(self, symbol, interval, limit=200, end_time=None):
        return list(self.candles)


class _AlwaysSignalStrategy:
    """Kapıya kadar gelen her turda sinyal üretir (HAM sinyal: atr_5m sabit)."""

    name = "C"

    def __init__(self, direction: Direction, atr_5m: float = _ATR_5M):
        self.direction = direction
        self.atr_5m = atr_5m

    def evaluate(self, ctx: StrategyContext) -> ScalpSignal:
        return ScalpSignal(
            strategy="C", symbol=ctx.symbol, direction=self.direction,
            entry_price=ctx.current_price, stop_price=ctx.current_price * 0.99,
            reason="test", regime=ctx.regime, atr_5m=self.atr_5m,
        )


class _ExternalSignalLike(_AlwaysSignalStrategy):
    """`is_external` tespiti SINIF ADINA bakar (engine.py) — TV yolunun da
    aynı kapıdan geçtiğini kanıtlamak için adı birebir taklit edilir."""


_ExternalSignalLike.__name__ = "_ExternalSignalStrategy"


class _OnlyAtDecision(_AlwaysSignalStrategy):
    """Yalnız karar mumunda (close_time == cutoff) sinyal üretir — parite."""

    def __init__(self, direction: Direction, cutoff_ms: int):
        super().__init__(direction)
        self.cutoff_ms = cutoff_ms

    def evaluate(self, ctx: StrategyContext):
        if ctx.candles_5m[-1].close_time != self.cutoff_ms:
            return None
        return super().evaluate(ctx)


class _NeverBlockedExecutor:
    def is_entry_blocked(self, symbol: str) -> bool:
        return False

    def pending_symbols(self):
        return set()

    async def try_open(self, sig, ctx):  # pragma: no cover - çağrılmamalı
        raise AssertionError("Kapı engellemeliydi; try_open ÇAĞRILMAMALI")


class _NoPositionsExits:
    def tracked_symbols(self):
        return set()


class _ReachedStopPolicy(Exception):
    """Sinyal kapılardan GEÇTİ ve apply_stop_policy'ye ulaştı (nöbetçi)."""


@dataclass
class _EngineCfg:
    """`_evaluate_symbol`'ün yeni kapıya GELENE kadar okuduğu alanlar.
    Diğer kapılar KAPALI → izole test."""

    scalper_c_blocked_cells: str = ""
    scalper_entry_block_hours_utc: str = ""
    scalper_min_atr_pct: float = 0.0
    scalper_max_atr_pct: float = 0.0
    scalper_market_gate: bool = False
    scalper_regime_filter: bool = False
    scalper_tv_regime_filter: bool = False
    scalper_structure_gate: bool = False
    scalper_structure_exit: str = "off"
    scalper_tf_entry: str = "1m"
    scalper_tf_context: str = "5m"
    scalper_tf_regime: str = "15m"
    scalper_leverage: int = 10
    scalper_max_positions: int = 5
    scalper_shadow_mode: bool = False
    scalper_forensics_enabled: bool = True


def _bare_engine(cfg: Any, fetcher: Any, regime: Optional[Regime] = None) -> Tuple[ScalperEngine, List[str], List[Dict[str, Any]]]:
    """__init__ ATLANIR (ağ/DB yok) — yalnız kapıya kadar okunan alanlar
    kurulur (tests/test_market_gate.py::_bare_engine deseni). Döner:
    (motor, info log satırları, niyet kayıtları)."""
    engine = ScalperEngine.__new__(ScalperEngine)
    engine.cfg = cfg
    engine.fetcher = fetcher
    engine.executor = _NeverBlockedExecutor()
    engine.exits = _NoPositionsExits()
    engine._entry_lock = asyncio.Lock()
    engine._opening_symbols = set()
    engine._regimes = {}
    engine._regime_cache = {}
    engine._market_gate_cache = {}
    engine._market_gate_rejects = {}
    engine._market_gate_retry_after = 0.0
    engine._market_gate_warn_at = {}
    infos: List[str] = []
    warnings: List[str] = []
    engine.logger = SimpleNamespace(
        info=lambda msg, *a, **kw: infos.append(str(msg)),
        warning=lambda msg, *a, **kw: warnings.append(str(msg)),
        error=lambda *a, **kw: None,
        debug=lambda *a, **kw: None,
    )
    engine._test_warnings = warnings
    intents: List[Dict[str, Any]] = []
    engine._record_intent = lambda **kw: intents.append(kw)
    if regime is not None:
        # 6 mumla rejim UNKNOWN çıkar; hücre testleri için rejim sabitlenir.
        engine._get_cached_regime = lambda symbol, candles: regime
    return engine, infos, intents


async def _run_engine(cfg: Any, strategy: Any, regime: Optional[Regime] = None,
                      candles: Optional[List[Candle]] = None):
    symbol_reservations.clear()
    try:
        engine, infos, intents = _bare_engine(cfg, _FlatFetcher(candles or _CANDLES), regime)
        await engine._evaluate_symbol("ETHUSDT", [strategy])
        return engine, infos, intents
    finally:
        symbol_reservations.clear()


def _denied(intents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        row for row in intents
        if row.get("stage") == intent.STAGE_DECIDED and row.get("decision") == intent.DECISION_DENY
    ]


# ==========================================================================
# 3) Canlı motor — `_evaluate_symbol` (C taraması + TV dış sinyali ortak)
# ==========================================================================

class TestEngineEntryGates:
    async def test_cell_gate_blocks_scanner_signal_and_records_intent(self):
        cfg = _EngineCfg(scalper_c_blocked_cells="RANGE:SHORT")
        _, infos, intents = await _run_engine(cfg, _AlwaysSignalStrategy(Direction.SHORT), regime=Regime.RANGE)
        assert any("hücre kapısı" in m and "SHORT girişi engellendi" in m for m in infos), infos
        denied = _denied(intents)
        assert len(denied) == 1
        assert denied[0]["reason"] == intent.REASON_CELL_GATE
        assert denied[0]["source"] == "scan"
        assert denied[0]["extra"]["regime"] == "RANGE" and denied[0]["extra"]["direction"] == "SHORT"

    async def test_cell_gate_blocks_external_tv_signal_by_same_gate(self):
        """TV dış sinyali de AYNI tek giriş noktasından geçer — ayrı muafiyet YOK."""
        cfg = _EngineCfg(scalper_c_blocked_cells="RANGE:SHORT")
        _, infos, intents = await _run_engine(cfg, _ExternalSignalLike(Direction.SHORT), regime=Regime.RANGE)
        assert any("hücre kapısı" in m and "SHORT TV sinyali engellendi" in m for m in infos), infos
        denied = _denied(intents)
        assert len(denied) == 1 and denied[0]["reason"] == intent.REASON_CELL_GATE
        assert denied[0]["source"] == "tv"

    async def test_cell_gate_lets_unlisted_cell_through(self, monkeypatch):
        cfg = _EngineCfg(scalper_c_blocked_cells="RANGE:SHORT")
        monkeypatch.setattr(engine_module, "apply_stop_policy", lambda sig, c: (_ for _ in ()).throw(_ReachedStopPolicy()))
        with pytest.raises(_ReachedStopPolicy):
            await _run_engine(cfg, _AlwaysSignalStrategy(Direction.LONG), regime=Regime.RANGE)

    async def test_hour_gate_uses_last_closed_entry_candle_close_time(self):
        """Saat kaynağı = ctx.candles_5m[-1].close_time (saat 3) — duvar
        saati DEĞİL. "3-4" engeller, "4-5" engellemez."""
        cfg = _EngineCfg(scalper_entry_block_hours_utc="3-4")
        _, infos, intents = await _run_engine(cfg, _AlwaysSignalStrategy(Direction.LONG))
        assert any("saat kapısı" in m and "LONG girişi engellendi" in m for m in infos), infos
        denied = _denied(intents)
        assert len(denied) == 1 and denied[0]["reason"] == intent.REASON_HOUR_GATE
        assert denied[0]["extra"]["hour_utc"] == 3

    async def test_hour_gate_off_window_lets_signal_through(self, monkeypatch):
        cfg = _EngineCfg(scalper_entry_block_hours_utc="4-5")
        monkeypatch.setattr(engine_module, "apply_stop_policy", lambda sig, c: (_ for _ in ()).throw(_ReachedStopPolicy()))
        with pytest.raises(_ReachedStopPolicy):
            await _run_engine(cfg, _AlwaysSignalStrategy(Direction.LONG))

    async def test_hour_gate_external_tv_signal(self):
        cfg = _EngineCfg(scalper_entry_block_hours_utc="22-4")  # gece yarısını sarar, 3'ü kapsar
        _, infos, intents = await _run_engine(cfg, _ExternalSignalLike(Direction.SHORT))
        assert any("saat kapısı" in m and "TV sinyali engellendi" in m for m in infos), infos
        assert _denied(intents)[0]["reason"] == intent.REASON_HOUR_GATE

    async def test_atr_gate_uses_raw_signal_before_stop_policy(self, monkeypatch):
        """ATR% HAM sinyalden hesaplanır; apply_stop_policy'ye HİÇ ulaşılmaz."""
        cfg = _EngineCfg(scalper_min_atr_pct=5.0)  # %1 < %5 → RED
        monkeypatch.setattr(engine_module, "apply_stop_policy", lambda sig, c: (_ for _ in ()).throw(_ReachedStopPolicy()))
        _, infos, intents = await _run_engine(cfg, _AlwaysSignalStrategy(Direction.LONG))
        assert any("ATR kapısı" in m and "LONG girişi engellendi" in m for m in infos), infos
        denied = _denied(intents)
        assert len(denied) == 1 and denied[0]["reason"] == intent.REASON_ATR_GATE
        # `extra.atr_pct` 4 haneye yuvarlanır (JSONL şişmesin) — abs toleransı.
        assert denied[0]["extra"]["atr_pct"] == pytest.approx(_ATR_5M / _CANDLES[-1].close * 100.0, abs=1e-4)

    async def test_atr_gate_max_blocks_external(self):
        cfg = _EngineCfg(scalper_max_atr_pct=0.5)  # %1 > %0.5 → RED
        _, infos, intents = await _run_engine(cfg, _ExternalSignalLike(Direction.LONG))
        assert any("ATR kapısı" in m and "TV sinyali engellendi" in m for m in infos), infos
        assert _denied(intents)[0]["reason"] == intent.REASON_ATR_GATE

    async def test_atr_gate_missing_atr_is_fail_open_and_logged(self, monkeypatch):
        cfg = _EngineCfg(scalper_min_atr_pct=5.0)
        monkeypatch.setattr(engine_module, "apply_stop_policy", lambda sig, c: (_ for _ in ()).throw(_ReachedStopPolicy()))
        symbol_reservations.clear()
        try:
            engine, infos, intents = _bare_engine(cfg, _FlatFetcher(_CANDLES))
            with pytest.raises(_ReachedStopPolicy):
                await engine._evaluate_symbol("ETHUSDT", [_AlwaysSignalStrategy(Direction.LONG, atr_5m=0.0)])
        finally:
            symbol_reservations.clear()
        assert _denied(intents) == []
        assert any("ATR% hesaplanamadı" in m and "fail-open" in m for m in engine._test_warnings)

    async def test_all_gates_off_reaches_stop_policy_without_intent_denial(self, monkeypatch):
        """Kapalıyken hiçbir ret, hiçbir log, hiçbir sayaç — akış aynen sürer."""
        cfg = _EngineCfg()
        monkeypatch.setattr(engine_module, "apply_stop_policy", lambda sig, c: (_ for _ in ()).throw(_ReachedStopPolicy()))
        symbol_reservations.clear()
        try:
            engine, infos, intents = _bare_engine(cfg, _FlatFetcher(_CANDLES), Regime.RANGE)
            with pytest.raises(_ReachedStopPolicy):
                await engine._evaluate_symbol("ETHUSDT", [_AlwaysSignalStrategy(Direction.SHORT)])
        finally:
            symbol_reservations.clear()
        assert _denied(intents) == []
        assert not any("kapısı" in m for m in infos)
        assert engine._test_warnings == []

    async def test_engine_is_fail_open_on_invalid_config(self, monkeypatch):
        """Motor fail-OPEN: hatalı ayar tarama turunu düşürmez, kapı o
        sinyalde uygulanmaz, BİR KEZ uyarılır (harness'ın tersi)."""
        cfg = _EngineCfg(scalper_c_blocked_cells="RANGE:SHRT")
        monkeypatch.setattr(engine_module, "apply_stop_policy", lambda sig, c: (_ for _ in ()).throw(_ReachedStopPolicy()))
        symbol_reservations.clear()
        try:
            engine, infos, intents = _bare_engine(cfg, _FlatFetcher(_CANDLES), Regime.RANGE)
            with pytest.raises(_ReachedStopPolicy):
                await engine._evaluate_symbol("ETHUSDT", [_AlwaysSignalStrategy(Direction.SHORT)])
            with pytest.raises(_ReachedStopPolicy):
                await engine._evaluate_symbol("ETHUSDT", [_AlwaysSignalStrategy(Direction.SHORT)])
        finally:
            symbol_reservations.clear()
        assert _denied(intents) == []
        errs = [m for m in engine._test_warnings if "giriş kapıları değerlendirilemedi" in m]
        assert len(errs) == 1, engine._test_warnings

    async def test_reject_counter_is_written_when_available(self):
        cfg = _EngineCfg(scalper_c_blocked_cells="RANGE:SHORT")
        symbol_reservations.clear()
        try:
            engine, _, _ = _bare_engine(cfg, _FlatFetcher(_CANDLES), Regime.RANGE)
            counted: List[str] = []
            engine.executor._count_reject = lambda reason: counted.append(reason)
            await engine._evaluate_symbol("ETHUSDT", [_AlwaysSignalStrategy(Direction.SHORT)])
        finally:
            symbol_reservations.clear()
        assert counted == [REASON_CELL]


# ==========================================================================
# 4) Harness — `simulate_symbol`
# ==========================================================================

@dataclass
class _SimCfg:
    """`simulate_symbol`'ün kapıya gelene kadar okuduğu minimum alan kümesi
    (tests/test_market_gate.py::_SimCfg + yeni alanlar)."""

    scalper_c_blocked_cells: str = ""
    scalper_entry_block_hours_utc: str = ""
    scalper_min_atr_pct: float = 0.0
    scalper_max_atr_pct: float = 0.0
    scalper_market_gate: bool = False
    scalper_regime_filter: bool = False
    scalper_structure_gate: bool = False
    scalper_structure_exit: str = "off"
    scalper_leverage: int = 10
    scalper_loss_cooldown_minutes: float = 0.0
    scalper_min_rr: float = 0.0
    scalper_stop_mode: str = "fixed_roi"
    scalper_fixed_stop_roi_pct: float = 40.0
    scalper_dynamic_leverage: bool = False
    scalper_min_stop_pct: float = 0.15
    scalper_max_stop_pct: float = 5.5
    scalper_risk_percentage: float = 10.0
    scalper_max_margin_pct: float = 5.0
    scalper_entry_mode: str = "taker"
    scalper_taker_fee_pct: float = 0.05
    scalper_maker_fee_pct: float = 0.02
    scalper_maker_fill_timeout_candles: int = 3
    scalper_tp1_roi: float = 8.0
    scalper_tp1_fraction: float = 0.40
    scalper_tp2_roi: float = 25.0
    scalper_tp2_fraction: float = 0.20
    scalper_breakeven_buffer_pct: float = 0.05
    scalper_chandelier_atr_mult: float = 3.5
    scalper_chandelier_atr_period: int = 14
    scalper_trail_relax_roi1_pct: float = 0.0
    scalper_trail_relax_mult1: float = 5.0
    scalper_trail_relax_roi2_pct: float = 150.0
    scalper_trail_relax_mult2: float = 7.0
    scalper_stop_atr_floor_mult: float = 0.5
    scalper_dyn_lev_stop_atr_mult: float = 3.0
    scalper_dyn_lev_min: int = 3
    scalper_dyn_lev_max: int = 20


def _cfg_without_gate_fields(cfg: _SimCfg) -> SimpleNamespace:
    """Yeni alanları HİÇ taşımayan cfg (eski test çiftleri / _GoldenCfg gibi)."""
    fields = {
        k: v for k, v in cfg.__dict__.items()
        if k not in {"scalper_c_blocked_cells", "scalper_entry_block_hours_utc",
                     "scalper_min_atr_pct", "scalper_max_atr_pct"}
    }
    return SimpleNamespace(**fields)


def _simulate(cfg: Any, strategy: Any, candles: Optional[List[Candle]] = None):
    missed: Dict[str, int] = {}
    candles = candles or _CANDLES
    trades = simulate_symbol(
        "ETHUSDT", candles, candles, candles, [strategy], cfg, missed_counter=missed,
    )
    return trades, missed


def _fingerprint(trades) -> list:
    return [
        (t.symbol, t.direction, round(t.entry_price, 8), t.entry_time,
         round(t.exit_price, 8), t.exit_time, t.exit_reason, round(t.pnl, 8))
        for t in trades
    ]


class TestHarnessEntryGates:
    def test_hour_gate_counts_into_missed_counter(self):
        cfg = _SimCfg(scalper_entry_block_hours_utc="3-4")
        trades, missed = _simulate(cfg, _AlwaysSignalStrategy(Direction.LONG))
        assert trades == []
        assert missed.get(REASON_HOUR, 0) == _DECISION_IDX + 1  # her karar mumu reddedildi
        assert REASON_CELL not in missed and REASON_ATR not in missed

    def test_atr_gate_counts_into_missed_counter(self):
        cfg = _SimCfg(scalper_min_atr_pct=5.0)  # %1 < %5
        trades, missed = _simulate(cfg, _AlwaysSignalStrategy(Direction.LONG))
        assert trades == []
        assert missed.get(REASON_ATR, 0) > 0
        assert REASON_HOUR not in missed and REASON_CELL not in missed

    def test_cell_gate_counts_into_missed_counter(self):
        # 6 mumla rejim UNKNOWN → hücre UNKNOWN:LONG (TV sinyali için anlamlı;
        # C stratejisi UNKNOWN'da sinyal üretmez ama stub üretir).
        cfg = _SimCfg(scalper_c_blocked_cells="UNKNOWN:LONG")
        trades, missed = _simulate(cfg, _AlwaysSignalStrategy(Direction.LONG))
        assert trades == []
        assert missed.get(REASON_CELL, 0) > 0
        # Listede olmayan yön geçer.
        trades2, missed2 = _simulate(cfg, _AlwaysSignalStrategy(Direction.SHORT))
        assert REASON_CELL not in missed2

    def test_gates_off_add_no_key_and_are_byte_identical(self):
        """Kapalıyken sözlüğe anahtar bile eklenmez ve çıktı, alanları hiç
        olmayan cfg ile BİREBİR aynıdır (golden backtest'in ön koşulu)."""
        cfg_off = _SimCfg()
        cfg_absent = _cfg_without_gate_fields(cfg_off)
        for direction in (Direction.LONG, Direction.SHORT):
            trades_off, missed_off = _simulate(cfg_off, _AlwaysSignalStrategy(direction))
            trades_abs, missed_abs = _simulate(cfg_absent, _AlwaysSignalStrategy(direction))
            assert _fingerprint(trades_off) == _fingerprint(trades_abs)
            assert missed_off == missed_abs
            for key in (REASON_CELL, REASON_HOUR, REASON_ATR):
                assert key not in missed_off

    def test_hour_gate_outside_window_is_inert(self):
        cfg = _SimCfg(scalper_entry_block_hours_utc="4-5")  # mumlar saat 3'te
        trades, missed = _simulate(cfg, _AlwaysSignalStrategy(Direction.LONG))
        trades_abs, missed_abs = _simulate(_cfg_without_gate_fields(cfg), _AlwaysSignalStrategy(Direction.LONG))
        assert _fingerprint(trades) == _fingerprint(trades_abs)
        assert missed == missed_abs and REASON_HOUR not in missed

    def test_harness_is_fail_closed_on_invalid_config(self):
        """Harness fail-CLOSED: hatalı ayar sessizce 'kapı kapalı' ölçümü
        üretmektense patlar (yapı kapısı gerekçesi)."""
        with pytest.raises(ValueError):
            _simulate(_SimCfg(scalper_c_blocked_cells="RANGE:SHRT"), _AlwaysSignalStrategy(Direction.LONG))
        with pytest.raises(ValueError):
            _simulate(_SimCfg(scalper_entry_block_hours_utc="25-26"), _AlwaysSignalStrategy(Direction.LONG))

    def test_gate_keys_are_visible_in_report(self, capsys):
        backtest_module.print_report([], missed_counter={REASON_HOUR: 2, REASON_ATR: 1, REASON_CELL: 3})
        out = capsys.readouterr().out
        assert REASON_HOUR in out and REASON_ATR in out and REASON_CELL in out


# ==========================================================================
# 5) PARİTE — motor ve harness aynı fonksiyonu aynı argümanlarla çağırır
# ==========================================================================

class TestEngineHarnessParity:
    def test_both_modules_reference_the_same_function_object(self):
        """İki taraf da `entry_gates` modülünün AYNI nesnesini kullanır —
        birinde yapılan bir değişiklik diğerini de kapsar (CLAUDE.md #2)."""
        assert engine_module.evaluate_entry_gates is entry_gates_module.evaluate_entry_gates
        assert backtest_module.evaluate_entry_gates is entry_gates_module.evaluate_entry_gates

    @staticmethod
    def _spy(monkeypatch, module) -> List[tuple]:
        recorded: List[tuple] = []

        def _record(regime, direction, close_time_ms, atr_5m, entry_price, cfg):
            recorded.append((
                str(getattr(regime, "value", regime)),
                str(getattr(direction, "value", direction)),
                int(close_time_ms),
                float(atr_5m),
                float(entry_price),
            ))
            return entry_gates_module.evaluate_entry_gates(
                regime, direction, close_time_ms, atr_5m, entry_price, cfg
            )

        monkeypatch.setattr(module, "evaluate_entry_gates", _record)
        return recorded

    @pytest.mark.parametrize(
        "engine_kw,sim_kw,expected",
        [
            (dict(scalper_entry_block_hours_utc="3-4"), dict(scalper_entry_block_hours_utc="3-4"), REASON_HOUR),
            (dict(scalper_min_atr_pct=5.0), dict(scalper_min_atr_pct=5.0), REASON_ATR),
            (dict(scalper_c_blocked_cells="UNKNOWN:LONG"), dict(scalper_c_blocked_cells="UNKNOWN:LONG"), REASON_CELL),
        ],
    )
    async def test_identical_arguments_and_verdict(self, monkeypatch, engine_kw, sim_kw, expected):
        """AYNI mum verisi + AYNI karar anı → motor ve harness kapıya BİREBİR
        aynı argümanları verir (rejim, yön, close_time, HAM atr, HAM giriş
        fiyatı) ve aynı kararı üretir.

        Kanıtlanan parite noktaları:
          * zaman kaynağı: motor `ctx.candles_5m[-1].close_time`, harness
            `close_times_5m[i]` — aynı epoch ms;
          * ATR/fiyat: `apply_stop_policy` ÖNCESİ ham sinyal (dinamik kaldıraç
            AÇIK olsa da değişmez — harness cfg'sinde açık bırakıldı);
          * rejim: iki taraf da kendi rejim tespitini yapar, sonuç aynı.
        """
        engine_calls = self._spy(monkeypatch, engine_module)
        harness_calls = self._spy(monkeypatch, backtest_module)

        # --- canlı motor: karar mumuna KADAR olan seri (son mum = kapanmış) --
        engine_candles = _CANDLES[: _DECISION_IDX + 1]
        assert engine_candles[-1].close_time == _DECISION_MS
        _, _, intents = await _run_engine(
            _EngineCfg(**engine_kw), _AlwaysSignalStrategy(Direction.LONG), candles=engine_candles,
        )
        denied = _denied(intents)
        assert len(denied) == 1 and denied[0]["reason"] == expected

        # --- harness: tam seri, yalnız karar mumunda sinyal ------------------
        missed: Dict[str, int] = {}
        simulate_symbol(
            "ETHUSDT", _CANDLES, _CANDLES, _CANDLES,
            [_OnlyAtDecision(Direction.LONG, _DECISION_MS)],
            _SimCfg(scalper_dynamic_leverage=True, **sim_kw),
            missed_counter=missed,
        )

        assert len(engine_calls) == 1, engine_calls
        assert len(harness_calls) == 1, harness_calls
        assert engine_calls[0] == harness_calls[0]
        assert engine_calls[0][2] == _DECISION_MS  # zaman = karar mumunun close_time'ı
        assert engine_calls[0][3] == _ATR_5M       # HAM atr (stop politikası dokunmadı)
        assert missed == {expected: 1}

    async def test_disabled_gate_is_evaluated_identically_and_allows_both(self, monkeypatch):
        """Kapalıyken de iki taraf aynı argümanla çağırır ve ikisi de None
        (serbest) döner — yani kapalı kapı her iki tarafta da inerttir."""
        engine_calls = self._spy(monkeypatch, engine_module)
        harness_calls = self._spy(monkeypatch, backtest_module)
        monkeypatch.setattr(engine_module, "apply_stop_policy", lambda sig, c: (_ for _ in ()).throw(_ReachedStopPolicy()))

        engine_candles = _CANDLES[: _DECISION_IDX + 1]
        with pytest.raises(_ReachedStopPolicy):
            await _run_engine(_EngineCfg(), _AlwaysSignalStrategy(Direction.LONG), candles=engine_candles)

        missed: Dict[str, int] = {}
        simulate_symbol(
            "ETHUSDT", _CANDLES, _CANDLES, _CANDLES,
            [_OnlyAtDecision(Direction.LONG, _DECISION_MS)], _SimCfg(), missed_counter=missed,
        )
        assert engine_calls == harness_calls and len(engine_calls) == 1
        for key in (REASON_CELL, REASON_HOUR, REASON_ATR):
            assert key not in missed
