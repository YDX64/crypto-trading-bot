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

# --- GERÇEK LONG dizisi (TV Desktop sonda alarmı, 2026-08-23) --------------
# Kullanıcı doğrulaması: BUY girişi → TP1 → TP2 → TP3 HIT; ayrı bir BUY ise
# SL HIT ile bitti. Seviye sırası LONG'da SL < Price < TP1 < TP2 < TP3.
REAL_BUY = (
    "🟢 BUY | BINANCE:BTCUSDT | TF: 1 | Price: 76556.52 | TQI: .54 | Score: 17 "
    "| SL: 76501.73 | TP1: 76583.92 | TP2: 76611.32 | TP3: 76638.72 | TP: fixed ×1.00"
)
REAL_BUY_TP1_HIT = "🎯 TP1 HIT | BINANCE:BTCUSDT | TF: 1 | Price: 76583.92"
REAL_BUY_TP2_HIT = "🎯 TP2 HIT | BINANCE:BTCUSDT | TF: 1 | Price: 76611.32"
REAL_BUY_TP3_HIT = "🏆 TP3 HIT | BINANCE:BTCUSDT | TF: 1 | Price: 76638.72"
# Başka bir BUY'ın sonu (aynı sonda, farklı işlem).
REAL_BUY_SL_HIT = "🛑 SL HIT | BINANCE:BTCUSDT | TF: 1 | Price: 76497.98"

# EXIT gövdesi TV'de HENÜZ GÖRÜLMEDİ (2026-08-23). Aşağıdaki aynı kalıptan
# VARSAYIMDIR; ayrıştırma emoji'ye değil anahtar kelimeye (EXIT) dayandığı
# için biçim küçük farklarla gelse de çalışır.

# --- Türetilmiş örnekler (aynı kalıp, başka sembol) ------------------------
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
            "🟢 BUY | BINANCE:SOLUSDT.P | TF: 1 | Price: 150.5 | SL: 149.0 "
            "| TP1: 151.25 | TP2: 152.0 | TP3: 152.75"
        )
        assert event.symbol == "SOLUSDT"

    def test_case_and_spacing_tolerant(self):
        event = parse_follower_event(
            "sell|binance:btcusdt|tf:1|price:100|sl:101|tp1:99|tp2:98|tp3:97".upper()
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

    def test_negative_or_zero_levels_are_ignored_on_hit_events(self):
        """HIT olaylarında bozuk seviye alanı yok sayılır (giriş DEĞİL)."""
        event = parse_follower_event(
            "🎯 TP1 HIT | BINANCE:BTCUSDT | TF: 1 | Price: 100 | SL: 0 | TP1: -5"
        )
        assert event.levels.sl is None
        assert event.levels.tp1 is None

    def test_negative_or_zero_levels_reject_an_entry(self):
        """GİRİŞTE bozuk seviye "eksik" demektir → 422 (katı tanıyıcı)."""
        with pytest.raises(FollowerParseError, match="zorunlu seviye"):
            parse_follower_event(
                "🟢 BUY | BINANCE:BTCUSDT | TF: 1 | Price: 100 | SL: 0 "
                "| TP1: -5 | TP2: 110 | TP3: 115"
            )

    def test_nan_level_rejects_an_entry(self):
        with pytest.raises(FollowerParseError, match="zorunlu seviye"):
            parse_follower_event(
                "🟢 BUY | BINANCE:BTCUSDT | TF: 1 | Price: 100 | SL: nan "
                "| TP1: 101 | TP2: 102 | TP3: 103"
            )


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
        levels = " sl=101 tp1=99 tp2=98 tp3=97" if kind == "entry" else ""
        event = parse_follower_event(
            f"src=algopro kind={kind} {direction} BTCUSDT tf=1 px=100{levels}"
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


class TestRealLongSequence:
    """GERÇEK LONG dizisi: BUY → TP1 → TP2 → TP3 HIT; ayrı bir BUY → SL HIT."""

    def test_real_buy_entry_full_levels(self):
        event = parse_follower_event(REAL_BUY)
        assert event.kind == "entry"
        assert event.direction == Direction.LONG
        assert event.symbol == "BTCUSDT"
        assert event.timeframe == "1"
        assert event.price == pytest.approx(76556.52)
        assert event.levels.sl == pytest.approx(76501.73)
        assert event.levels.tp1 == pytest.approx(76583.92)
        assert event.levels.tp2 == pytest.approx(76611.32)
        assert event.levels.tp3 == pytest.approx(76638.72)
        assert event.tqi == pytest.approx(0.54)
        assert event.score == pytest.approx(17.0)

    def test_real_buy_levels_ascend_for_long(self):
        """LONG'da ölçülen sıra: SL < Price < TP1 < TP2 < TP3."""
        e = parse_follower_event(REAL_BUY)
        assert (
            e.levels.sl < e.price < e.levels.tp1 < e.levels.tp2 < e.levels.tp3
        )

    def test_real_buy_rr_is_half_one_onehalf(self):
        """LONG bacağında da RR 0.5/1.0/1.5.

        Tolerans 2 tick (0.02): AlgoPro her seviyeyi AYRI AYRI tick'e
        yuvarlıyor, bu yüzden TP3 sapması tek tick'i aşabiliyor
        (ölçülen: 82.20 vs 1.5 × 54.79 = 82.185).
        """
        e = parse_follower_event(REAL_BUY)
        distance = e.price - e.levels.sl
        assert e.levels.tp1 - e.price == pytest.approx(0.5 * distance, abs=0.02)
        assert e.levels.tp2 - e.price == pytest.approx(1.0 * distance, abs=0.02)
        assert e.levels.tp3 - e.price == pytest.approx(1.5 * distance, abs=0.02)

    @pytest.mark.parametrize(
        "body,kind",
        [
            (REAL_BUY_TP1_HIT, "tp1"),
            (REAL_BUY_TP2_HIT, "tp2"),
            (REAL_BUY_TP3_HIT, "tp3"),
            (REAL_BUY_SL_HIT, "sl"),
        ],
    )
    def test_long_sequence_hit_kinds(self, body, kind):
        event = parse_follower_event(body)
        assert event.kind == kind
        assert event.symbol == "BTCUSDT"
        assert event.direction is None
        assert event.levels.has_any is False

    def test_hit_prices_match_entry_levels(self):
        entry = parse_follower_event(REAL_BUY)
        assert parse_follower_event(REAL_BUY_TP1_HIT).price == pytest.approx(
            entry.levels.tp1
        )
        assert parse_follower_event(REAL_BUY_TP2_HIT).price == pytest.approx(
            entry.levels.tp2
        )
        assert parse_follower_event(REAL_BUY_TP3_HIT).price == pytest.approx(
            entry.levels.tp3
        )


class TestEntryLevelOrdering:
    """Giriş seviyelerinin yöne göre sırası bozuksa gövde REDDEDİLİR (422).

    Gerekçe: sıra bozuksa mesaj AlgoPro V1.6 girişi değildir (biçim değişmiş
    ya da alanlar yer değiştirmiştir); "SL"yi TP sanıp ters tarafa emir
    koymaktansa reddetmek doğrudur.
    """

    def test_long_with_stop_above_price_rejected(self):
        with pytest.raises(FollowerParseError, match="Seviye sırası"):
            parse_follower_event(
                "🟢 BUY | BINANCE:BTCUSDT | TF: 1 | Price: 100 | SL: 101 "
                "| TP1: 105 | TP2: 110 | TP3: 115"
            )

    def test_short_with_stop_below_price_rejected(self):
        with pytest.raises(FollowerParseError, match="Seviye sırası"):
            parse_follower_event(
                "🔴 SELL | BINANCE:BTCUSDT | TF: 1 | Price: 100 | SL: 99 "
                "| TP1: 95 | TP2: 90 | TP3: 85"
            )

    def test_long_with_tp_below_price_rejected(self):
        with pytest.raises(FollowerParseError, match="Seviye sırası"):
            parse_follower_event(
                "🟢 BUY | BINANCE:BTCUSDT | TF: 1 | Price: 100 | SL: 99 "
                "| TP1: 98 | TP2: 110 | TP3: 115"
            )

    def test_swapped_tp2_tp3_rejected(self):
        """TP2/TP3 yer değiştirmişse 3 parça çıkışın anlamı bozulur."""
        with pytest.raises(FollowerParseError, match="Seviye sırası"):
            parse_follower_event(
                "🟢 BUY | BINANCE:BTCUSDT | TF: 1 | Price: 100 | SL: 99 "
                "| TP1: 101 | TP2: 103 | TP3: 102"
            )

    def test_short_swapped_tp1_tp2_rejected(self):
        with pytest.raises(FollowerParseError, match="Seviye sırası"):
            parse_follower_event(
                "🔴 SELL | BINANCE:BTCUSDT | TF: 1 | Price: 100 | SL: 101 "
                "| TP1: 97 | TP2: 99 | TP3: 95"
            )

    def test_equal_levels_rejected(self):
        """Sıfır mesafeli seviye emir olarak konulamaz — eşitlik tutarsızlıktır."""
        with pytest.raises(FollowerParseError, match="Seviye sırası"):
            parse_follower_event(
                "🟢 BUY | BINANCE:BTCUSDT | TF: 1 | Price: 100 | SL: 100 "
                "| TP1: 101 | TP2: 102 | TP3: 103"
            )

    def test_missing_level_rejects_the_entry(self):
        """TP2 yoksa merdiven eksiktir → giriş 422 (eski davranış: türetilirdi)."""
        with pytest.raises(FollowerParseError, match="zorunlu seviye"):
            parse_follower_event(
                "🟢 BUY | BINANCE:BTCUSDT | TF: 1 | Price: 100 | SL: 99 "
                "| TP1: 101 | TP3: 103"
            )

    def test_price_only_entry_is_rejected(self):
        """Seviyesiz giriş ARTIK kabul edilmez (katı AlgoPro tanıyıcısı)."""
        with pytest.raises(FollowerParseError, match="zorunlu seviye"):
            parse_follower_event("🟢 BUY | BINANCE:BTCUSDT | TF: 1 | Price: 100")

    def test_ordering_applies_to_key_value_template_too(self):
        with pytest.raises(FollowerParseError, match="Seviye sırası"):
            parse_follower_event(
                "kind=entry buy BTCUSDT tf=1 px=100 sl=101 tp1=105 tp2=110 tp3=115"
            )

    def test_hit_events_are_not_order_checked(self):
        """HIT/EXIT olaylarında yön yoktur; sıra doğrulaması UYGULANMAZ."""
        assert parse_follower_event(REAL_BUY_SL_HIT).kind == "sl"
        assert parse_follower_event(EXIT).kind == "exit"


class TestStrictAlgoproRecognizer:
    """Düşmanca inceleme bulgu 2 + 5: FAIL-OPEN kapatıldı.

    Eski davranış: tanınmayan bir gövdede yalnız bir YÖN KELİMESİ geçmesi
    (LuxAlgo şablonu, serbest metin, bozulmuş AlgoPro mesajı) `kind=entry`
    üretiyor ve POZİSYON açtırabiliyordu. Bu testler düzeltme olmadan
    KIRMIZIDIR (o gövdeler eskiden `entry` dönerdi).
    """

    FAIL_OPEN_BODIES = [
        # Serbest metin + yön kelimesi (eski: entry).
        "Bullish reversal detected on BTCUSDT",
        # LuxAlgo şablonu (borsa niteliği ve TF/Price alanları yok).
        "src=luxosc BTCUSDT long confirmation 4h",
        # AlgoPro'ya benzeyen ama borsa niteliği OLMAYAN gövde.
        "🟢 BUY | BTCUSDT | TF: 1 | Price: 100 | SL: 99 | TP1: 101 | TP2: 102 | TP3: 103",
        # Başka bir borsa (takipçi Binance futures'ta işlem yapar).
        "🟢 BUY | BYBIT:BTCUSDT | TF: 1 | Price: 100 | SL: 99 | TP1: 101 | TP2: 102 | TP3: 103",
        # TF alanı yok.
        "🟢 BUY | BINANCE:BTCUSDT | Price: 100 | SL: 99 | TP1: 101 | TP2: 102 | TP3: 103",
        # Price alanı yok.
        "🟢 BUY | BINANCE:BTCUSDT | TF: 1 | SL: 99 | TP1: 101 | TP2: 102 | TP3: 103",
        # Olay anahtarı BAŞLIKTA değil, gövdenin ortasında (eski: son çare taraması).
        "BINANCE:BTCUSDT | TF: 1 | Price: 100 | note: SL HIT",
    ]

    @pytest.mark.parametrize("body", FAIL_OPEN_BODIES)
    def test_non_algopro_bodies_are_rejected(self, body):
        with pytest.raises(FollowerParseError):
            parse_follower_event(body)

    @pytest.mark.parametrize("body", FAIL_OPEN_BODIES)
    def test_recognizer_says_not_algopro(self, body):
        from src.strategies.follower.parser import algopro_alert_kind

        assert algopro_alert_kind(body) is None

    @pytest.mark.parametrize(
        "body,kind",
        [
            (REAL_SELL, "entry"),
            (REAL_BUY, "entry"),
            (REAL_TP1_HIT_2, "tp1"),
            (REAL_TP2_HIT_2, "tp2"),
            (REAL_TP3_HIT_2, "tp3"),
            (REAL_BUY_SL_HIT, "sl"),
            (EXIT, "exit"),
        ],
    )
    def test_real_bodies_are_recognized(self, body, kind):
        from src.strategies.follower.parser import algopro_alert_kind

        assert algopro_alert_kind(body) == kind

    def test_recognizer_never_raises(self):
        from src.strategies.follower.parser import algopro_alert_kind

        for body in ("", "   ", None, "|" * 200, "🟢 BUY |" + "x" * 5000):
            assert algopro_alert_kind(body) is None or isinstance(
                algopro_alert_kind(body), str
            )

    def test_template_form_is_not_recognized_as_algopro(self):
        """`kind=` şablonu ELLE test yoludur — köprü onu İLETMEZ."""
        from src.strategies.follower.parser import algopro_alert_kind

        assert (
            algopro_alert_kind(
                "src=algopro kind=entry buy BTCUSDT tf=1 px=100 sl=99 "
                "tp1=101 tp2=102 tp3=103"
            )
            is None
        )
