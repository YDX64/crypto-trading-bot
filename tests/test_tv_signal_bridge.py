"""TradingView webhook köprüsü (2026-08-12).

Sözleşme: TV header gönderemez → secret gövdede, sabit-zamanlı doğrulanır.
Parse toleranslı: JSON ({"secret","symbol","side"}) veya LuxAlgo/AlgoPro
varsayılan alert metni ("Bullish Confirmation ... BTCUSDT.P ... secret=X").
Kabul edilen sinyal scalper'ın KENDİ giriş hattından geçer (stop/TP/BE/
cooldown/kapasite atlanmaz) — bkz. test_runtime_liveness'taki akış testi.
"""

import json

import pytest
from fastapi import HTTPException

from src.main import resolve_tv_signal
from src.strategies.scalper.types import Direction

SECRET = "sup3r-gizli-t0ken"


def _resolve(raw: str):
    return resolve_tv_signal(raw, SECRET)


class TestSecretValidation:
    def test_valid_json_secret_accepted(self):
        raw = json.dumps({"secret": SECRET, "symbol": "BTCUSDT", "side": "buy"})
        assert _resolve(raw) == ("BTCUSDT", Direction.LONG)

    def test_wrong_secret_rejected_403(self):
        raw = json.dumps({"secret": "yanlis", "symbol": "BTCUSDT", "side": "buy"})
        with pytest.raises(HTTPException) as e:
            _resolve(raw)
        assert e.value.status_code == 403

    def test_missing_secret_rejected_403(self):
        with pytest.raises(HTTPException) as e:
            _resolve(json.dumps({"symbol": "BTCUSDT", "side": "buy"}))
        assert e.value.status_code == 403

    def test_plain_text_secret_equals_form(self):
        raw = f"LuxAlgo Bullish Confirmation BTCUSDT.P secret={SECRET}"
        assert _resolve(raw) == ("BTCUSDT", Direction.LONG)

    def test_url_secret_accepted_for_any_alert_mode(self):
        # LuxAlgo "Any alert() function call": gövdeyi script doldurur,
        # secret URL'de taşınır.
        raw = "Confirmation Bullish | LTCUSDT.P | 1"
        assert resolve_tv_signal(raw, SECRET, url_secret=SECRET) == (
            "LTCUSDT", Direction.LONG,
        )

    def test_wrong_url_secret_rejected(self):
        with pytest.raises(HTTPException) as e:
            resolve_tv_signal("Bullish BTCUSDT", SECRET, url_secret="yanlis")
        assert e.value.status_code == 403

    def test_body_secret_wins_over_url_secret(self):
        # Gövdede YANLIŞ secret varsa URL doğru olsa bile red — gövde
        # secret'ı açıkça verilmişse o doğrulanır (karışık yapılandırma
        # sessizce kabul edilmez).
        raw = json.dumps({"secret": "yanlis", "symbol": "BTCUSDT", "side": "buy"})
        with pytest.raises(HTTPException) as e:
            resolve_tv_signal(raw, SECRET, url_secret=SECRET)
        assert e.value.status_code == 403


class TestSymbolResolution:
    def test_exchange_prefix_and_perp_suffix_stripped(self):
        raw = json.dumps(
            {"secret": SECRET, "symbol": "BINANCE:LTCUSDT.P", "side": "sell"}
        )
        assert _resolve(raw) == ("LTCUSDT", Direction.SHORT)

    def test_symbol_from_free_text(self):
        raw = f"Bearish Reversal DOGEUSDT.P 1m secret:{SECRET}"
        assert _resolve(raw) == ("DOGEUSDT", Direction.SHORT)

    def test_unresolvable_symbol_422(self):
        with pytest.raises(HTTPException) as e:
            _resolve(f"Bullish signal secret={SECRET}")
        assert e.value.status_code == 422


class TestDirectionResolution:
    def test_payload_side_wins_over_text(self):
        raw = json.dumps({
            "secret": SECRET, "symbol": "BTCUSDT", "side": "sell",
            "message": "buy the dip",  # payload side açıkça sell diyor
        })
        assert _resolve(raw)[1] == Direction.SHORT

    def test_luxalgo_bullish_text_maps_long(self):
        raw = f"Confirmation Bullish+ | BNBUSDT | 1 secret={SECRET}"
        assert _resolve(raw)[1] == Direction.LONG

    def test_ambiguous_text_422(self):
        raw = f"bullish then bearish XRPUSDT secret={SECRET}"
        with pytest.raises(HTTPException) as e:
            _resolve(raw)
        assert e.value.status_code == 422

    def test_no_direction_words_422(self):
        raw = f"XRPUSDT alert secret={SECRET}"
        with pytest.raises(HTTPException) as e:
            _resolve(raw)
        assert e.value.status_code == 422

    def test_secret_containing_direction_word_not_scanned(self):
        # Secret "sell" içeriyor — yön taramasından çıkarılmalı.
        tricky = "sell-me-not-123"
        raw = f"Bullish ETHUSDT secret={tricky}"
        symbol, direction = resolve_tv_signal(raw, tricky)
        assert (symbol, direction) == ("ETHUSDT", Direction.LONG)
