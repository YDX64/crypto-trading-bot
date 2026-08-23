"""AlgoPro alarm ayrıştırıcısı (D20) — `src/strategies/follower/parser.py`.

Fixture'lar UYDURMA DEĞİLDİR: 2026-08-23'te TradingView Desktop'ta bir sonda
alarmıyla yakalanan GERÇEK AlgoPro V1.6 gövdeleridir (BTCUSDT 1dk, "Herhangi
bir alert() fonksiyonu çağrısı" modu). Diğer olay türleri aynı kalıptan
türetilmiştir (kullanıcı doğrulaması: emoji'ye değil anahtar kelimeye ve
`|` ayraçlı `Anahtar: değer` çiftlerine dayan).
"""

from __future__ import annotations

import pytest

from src.strategies.follower.parser import parse_follower_event
from src.strategies.follower.types import FollowerParseError
from src.strategies.scalper.types import Direction

# --- GERÇEK gövdeler (TV Desktop, 2026-08-23 04:52/04:53 UTC+2) ------------
REAL_SELL = (
    "🔴 SELL | BINANCE:BTCUSDT | TF: 1 | Price: 77126.08 | TQI: .45 | Score: 8 "
    "| SL: 77167.77 | TP1: 77105.23 | TP2: 77084.39 | TP3: 77063.54 | TP: fixed ×1.00"
)
REAL_SL_HIT = "🛑 SL HIT | BINANCE:BTCUSDT | TF: 1 | Price: 77167.77"

# --- GERÇEK dizi: giriş → TP1 HIT → TP2 HIT (aynı kanal, aynı sonda) -------
REAL_SELL_2 = (
    "🔴 SELL | BINANCE:BTCUSDT | TF: 1 | Price: 77062.10 | TQI: .19 | Score: 6 "
    "| SL: 77111.33 | TP1: 77037.49 | TP2: 77012.87 | TP3: 76988.26 | TP: fixed ×1.00"
)
REAL_TP1_HIT_2 = "🎯 TP1 HIT | BINANCE:BTCUSDT | TF: 1 | Price: 77037.49"
REAL_TP2_HIT_2 = "🎯 TP2 HIT | BINANCE:BTCUSDT | TF: 1 | Price: 77012.87"
REAL_TP3_HIT_2 = "🏆 TP3 HIT | BINANCE:BTCUSDT | TF: 1 | Price: 76988.26"

# BUY ve EXIT gövdeleri TV'de HENÜZ GÖRÜLMEDİ (2026-08-23). Aşağıdakiler aynı
# kalıptan VARSAYIMDIR; ayrıştırma emoji'ye değil anahtar kelimeye (BUY/EXIT)
# dayandığı için biçim küçük farklarla gelse de çalışır.

# --- Aynı kalıptan beklenen diğer olaylar (BUY/EXIT henüz ölçülmedi) -------
BUY = (
    "🟢 BUY | BINANCE:ETHUSDT | TF: 1 | Price: 3000.5 | TQI: .61 | Score: 7 "
    "| SL: 2985.5 | TP1: 3008.0 | TP2: 3015.5 | TP3: 3023.0 | TP: fixed ×1.00"
)
TP1_HIT = "🎯 TP1 HIT | BINANCE:BTCUSDT | TF: 1 | Price: 77105.23"
TP2_HIT = "🎯 TP2 HIT | BINANCE:BTCUSDT | TF: 1 | Price: 77084.39"
TP3_HIT = "🏆 TP3 HIT | BINANCE:BTCUSDT | TF: 1 | Price: 77063.54"
EXIT = "⚪ EXIT | BINANCE:BTCUSDT | TF: 1 | Price: 77100.00"


class TestRealAlgoProBodies:
    def test_sell_entry_full_levels(self):
        event = parse_follower_event(REAL_SELL)
        assert event.kind == "entry"
        assert event.direction == Direction.SHORT
        assert event.symbol == "BTCUSDT"
        assert event.timeframe == "1"
        assert event.price == pytest.approx(77126.08)
        assert event.levels.sl == pytest.approx(77167.77)
        assert event.levels.tp1 == pytest.approx(77105.23)
        assert event.levels.tp2 == pytest.approx(77084.39)
        assert event.levels.tp3 == pytest.approx(77063.54)
        # `TQI: .45` — baştaki sıfırsız ondalık
        assert event.tqi == pytest.approx(0.45)
        assert event.score == pytest.approx(8.0)
        assert event.source == "algopro"

    def test_tp_fixed_field_is_not_a_level(self):
        """`TP: fixed ×1.00` bir seviye DEĞİLDİR (yalnız tp1/tp2/tp3)."""
        event = parse_follower_event(REAL_SELL)
        assert event.levels.as_dict()["tp1"] == pytest.approx(77105.23)

    def test_sl_hit(self):
        event = parse_follower_event(REAL_SL_HIT)
        assert event.kind == "sl"
        assert event.symbol == "BTCUSDT"
        assert event.direction is None
        assert event.price == pytest.approx(77167.77)
        assert event.levels.has_any is False

    def test_buy_entry(self):
        event = parse_follower_event(BUY)
        assert event.kind == "entry"
        assert event.direction == Direction.LONG
        assert event.symbol == "ETHUSDT"
        assert event.levels.sl == pytest.approx(2985.5)

    @pytest.mark.parametrize(
        "body,kind",
        [(TP1_HIT, "tp1"), (TP2_HIT, "tp2"), (TP3_HIT, "tp3"), (EXIT, "exit")],
    )
    def test_hit_and_exit_kinds(self, body, kind):
        event = parse_follower_event(body)
        assert event.kind == kind
        assert event.symbol == "BTCUSDT"
        assert event.direction is None

    def test_entry_body_containing_tp_fields_is_not_a_hit(self):
        """Giriş mesajı 'TP1:' alanı taşır ama 'TP1 HIT' DEĞİLDİR."""
        assert parse_follower_event(REAL_SELL).kind == "entry"


class TestRealEntryThenTpHitSequence:
    """GERÇEK dizi: bir SELL girişi ve onu izleyen TP1/TP2 HIT olayları."""

    def test_second_real_sell_entry(self):
        event = parse_follower_event(REAL_SELL_2)
        assert event.kind == "entry"
        assert event.direction == Direction.SHORT
        assert event.price == pytest.approx(77062.10)
        assert event.levels.sl == pytest.approx(77111.33)
        assert event.levels.tp1 == pytest.approx(77037.49)
        assert event.levels.tp2 == pytest.approx(77012.87)
        assert event.levels.tp3 == pytest.approx(76988.26)
        assert event.tqi == pytest.approx(0.19)
        assert event.score == pytest.approx(6.0)

    def test_tp_hit_messages_carry_only_price(self):
        """TP HIT gövdesinde SEVİYE alanı yoktur — yalnız `Price`."""
        for body, kind in (
            (REAL_TP1_HIT_2, "tp1"),
            (REAL_TP2_HIT_2, "tp2"),
            (REAL_TP3_HIT_2, "tp3"),
        ):
            event = parse_follower_event(body)
            assert event.kind == kind
            assert event.levels.has_any is False
            assert event.price is not None
            assert event.direction is None

    def test_tp3_hit_uses_trophy_emoji_but_keyword_decides(self):
        """🏆 farklı bir emoji — sınıflandırma 'TP3 HIT' kelimesinden gelir."""
        event = parse_follower_event(REAL_TP3_HIT_2)
        assert event.kind == "tp3"
        assert event.price == pytest.approx(76988.26)
        # Aynı dizinin giriş mesajındaki TP3 seviyesiyle eşleşir.
        assert parse_follower_event(REAL_SELL_2).levels.tp3 == pytest.approx(
            event.price
        )

    def test_hit_price_matches_entry_level(self):
        """Çapraz doğrulama: HIT fiyatı, giriş mesajındaki TP seviyesidir."""
        entry = parse_follower_event(REAL_SELL_2)
        assert parse_follower_event(REAL_TP1_HIT_2).price == pytest.approx(
            entry.levels.tp1
        )
        assert parse_follower_event(REAL_TP2_HIT_2).price == pytest.approx(
            entry.levels.tp2
        )

    def test_second_entry_also_matches_rr_half_one_onehalf(self):
        """İkinci gerçek örnekte de RR 0.5/1.0/1.5 (yarım tick toleransla)."""
        entry = parse_follower_event(REAL_SELL_2)
        distance = entry.levels.sl - entry.price
        assert entry.price - entry.levels.tp1 == pytest.approx(0.5 * distance, abs=0.01)
        assert entry.price - entry.levels.tp2 == pytest.approx(1.0 * distance, abs=0.01)
        assert entry.price - entry.levels.tp3 == pytest.approx(1.5 * distance, abs=0.01)


class TestToleranceAndNormalization:
    def test_symbol_prefix_and_perp_suffix(self):
        event = parse_follower_event(
            "🟢 BUY | BINANCE:SOLUSDT.P | TF: 1 | Price: 150.5 | SL: 149.0"
        )
        assert event.symbol == "SOLUSDT"

    def test_case_and_spacing_tolerant(self):
        event = parse_follower_event(
            "sell|binance:btcusdt|tf:1|price:100|sl:101".upper()
        )
        assert event.kind == "entry"
        assert event.direction == Direction.SHORT
        assert event.symbol == "BTCUSDT"
        assert event.levels.sl == pytest.approx(101.0)

    def test_no_emoji_needed(self):
        event = parse_follower_event(
            "SL HIT | BINANCE:BTCUSDT | TF: 1 | Price: 77167.77"
        )
        assert event.kind == "sl"

    def test_negative_or_zero_levels_ignored(self):
        event = parse_follower_event(
            "🟢 BUY | BINANCE:BTCUSDT | TF: 1 | Price: 100 | SL: 0 | TP1: -5"
        )
        assert event.levels.sl is None
        assert event.levels.tp1 is None

    def test_nan_level_ignored(self):
        event = parse_follower_event(
            "🟢 BUY | BINANCE:BTCUSDT | TF: 1 | Price: 100 | SL: nan"
        )
        assert event.levels.sl is None


class TestKeyValueTemplate:
    def test_explicit_template(self):
        event = parse_follower_event(
            "src=algopro kind=entry buy BTCUSDT tf=1 px=100.5 t=2026-08-23T01:00:00Z "
            "sl=99.5 tp1=101.0 tp2=101.5 tp3=102.0"
        )
        assert event.kind == "entry"
        assert event.direction == Direction.LONG
        assert event.symbol == "BTCUSDT"
        assert event.timeframe == "1"
        assert event.price == pytest.approx(100.5)
        assert event.ts == "2026-08-23T01:00:00Z"
        assert event.levels.tp3 == pytest.approx(102.0)

    @pytest.mark.parametrize(
        "kind", ["entry", "exit", "tp1", "tp2", "tp3", "sl"]
    )
    def test_all_kinds(self, kind):
        direction = "sell" if kind == "entry" else ""
        event = parse_follower_event(
            f"src=algopro kind={kind} {direction} BTCUSDT tf=1 px=100"
        )
        assert event.kind == kind

    def test_template_without_levels(self):
        event = parse_follower_event("kind=exit BTCUSDT tf=1 px=100")
        assert event.levels.has_any is False

    def test_unknown_kind_rejected(self):
        with pytest.raises(FollowerParseError):
            parse_follower_event("kind=whatever BTCUSDT tf=1")

    def test_entry_without_direction_rejected(self):
        with pytest.raises(FollowerParseError):
            parse_follower_event("kind=entry BTCUSDT tf=1 px=100")


class TestBrokenBodies:
    def test_empty_body(self):
        with pytest.raises(FollowerParseError):
            parse_follower_event("")

    def test_no_symbol(self):
        with pytest.raises(FollowerParseError):
            parse_follower_event("🔴 SELL | TF: 1 | Price: 100")

    def test_no_kind_and_no_direction(self):
        with pytest.raises(FollowerParseError):
            parse_follower_event("BINANCE:BTCUSDT | TF: 1 | Price: 100")

    def test_ambiguous_direction(self):
        with pytest.raises(FollowerParseError):
            parse_follower_event("BUY SELL | BINANCE:BTCUSDT | TF: 1 | Price: 100")

    def test_junk_body(self):
        with pytest.raises(FollowerParseError):
            parse_follower_event("lorem ipsum dolor sit amet")
