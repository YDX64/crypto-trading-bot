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

import src.main as main_module
from src.main import resolve_tv_signal, resolve_tv_source
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

    def test_awaxx_pine_coin_field_completed_to_usdt(self):
        # Kullanıcının kendi Pine alarmı (awaxx_scalp_alert*.pine, eski bot
        # sözleşmesi): parite `coin` alanında USDT'siz gelir; metinde de
        # geçmez. Eskiden 422 alıyordu — artık SOLUSDT/LONG çözülür.
        raw = json.dumps({
            "secret": SECRET, "coin": "SOL", "direction": "LONG",
            "entry": 99.5, "stoploss": 97.1, "targets": [100.1, 100.6],
            "leverage": 20, "source": "awaxx_scalp", "score": 80,
        })
        assert _resolve(raw) == ("SOLUSDT", Direction.LONG)

    def test_coin_field_accepts_full_pair_and_perp_suffix(self):
        raw = json.dumps({"secret": SECRET, "coin": "binance:xrpusdt.p", "side": "sell"})
        assert _resolve(raw) == ("XRPUSDT", Direction.SHORT)

    def test_symbol_field_wins_over_coin(self):
        raw = json.dumps({"secret": SECRET, "symbol": "BTCUSDT", "coin": "SOL", "side": "buy"})
        assert _resolve(raw) == ("BTCUSDT", Direction.LONG)

    def test_ticker_field_accepted(self):
        raw = json.dumps({"secret": SECRET, "ticker": "BINANCE:ETHUSDT.P", "side": "buy"})
        assert _resolve(raw) == ("ETHUSDT", Direction.LONG)

    def test_garbage_coin_field_still_422(self):
        # Coin alanı parite kodu değilse USDT EKLENMEZ (rastgele metin
        # pariteye çevrilmez) → sembol çözülemez → 422.
        raw = json.dumps({"secret": SECRET, "coin": "not a coin!", "side": "buy"})
        with pytest.raises(HTTPException) as e:
            _resolve(raw)
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


class TestTvSourceAllowlist:
    """?src= allowlist (2026-08-21): typo → hayalet kaynak sessizce
    sağlamayı asla dolduramazdı. Bilinmeyen değer reddedilmez, 'tv'ye
    eşlenir ve çağıran (webhook endpoint) WARNING loglar."""

    def test_unknown_src_maps_to_tv_and_flags_rejected(self):
        source, rejected = resolve_tv_source("algpro", "")  # yazım hatası
        assert source == "tv"
        assert rejected is True

    def test_known_src_passes_through_unrejected(self):
        source, rejected = resolve_tv_source("algopro", "")
        assert source == "algopro"
        assert rejected is False

    def test_case_and_whitespace_normalized(self):
        source, rejected = resolve_tv_source("  LuxOSC  ", "")
        assert source == "luxosc"
        assert rejected is False

    def test_missing_src_falls_back_to_algopro_fingerprint(self):
        raw_body = "BUY on BTCUSDT | TF: 5 | Price: 65000"
        source, rejected = resolve_tv_source(None, raw_body)
        assert source == "algopro"
        assert rejected is False

    def test_missing_src_falls_back_to_generic_tv(self):
        source, rejected = resolve_tv_source("", "Bullish Confirmation BTCUSDT.P")
        assert source == "tv"
        assert rejected is False

    def test_all_default_allowlist_entries_pass_through(self):
        for name in (
            "luxosc", "luxso", "algopro", "botv3", "tv",
            "awaxx_scalp", "awaxx_v2",
        ):
            source, rejected = resolve_tv_source(name, "")
            assert (source, rejected) == (name, False)

    def test_awaxx_scalp_is_not_collapsed_to_generic_tv(self):
        # Kullanıcının Pine alarmı (`source":"awaxx_scalp"`) artık kendi
        # kovasında sayılır — luxosc + awaxx_scalp = 2 farklı oy.
        source, rejected = resolve_tv_source("awaxx_scalp", "")
        assert source == "awaxx_scalp"
        assert rejected is False

    def test_custom_allowlist_setting_is_respected(self, monkeypatch):
        # tv_source_allowlist ayarı gerçekten okunuyor mu (yalnız varsayılan
        # değerle değil) — dar bir allowlist ile "algopro" bile reddedilmeli.
        monkeypatch.setattr(main_module.settings, "tv_source_allowlist", "luxosc,tv")
        source, rejected = resolve_tv_source("algopro", "")
        assert (source, rejected) == ("tv", True)


class _FakeRequest:
    """`Request`'in webhook'ta kullanılan iki yüzeyini taklit eden test çifti."""

    def __init__(self, body: bytes, query: dict):
        self._body = body
        self.query_params = query  # dict .get() ile QueryParams'a yeter

    async def body(self) -> bytes:
        return self._body


@pytest.fixture
def _tv_webhook_ready(monkeypatch):
    """Endpoint'in erken-dönüş koşullarını (secret/scalper hazır) aşacak
    minimum global durum. external_signal AsyncMock — gerçek işlem açmaz."""
    from unittest.mock import AsyncMock, MagicMock

    monkeypatch.setattr(main_module.settings, "tv_webhook_secret", SECRET)
    monkeypatch.setattr(main_module.settings, "tv_confluence_required", 1)
    fake_engine = MagicMock()
    fake_engine.external_signal = AsyncMock(return_value={"accepted": True})
    monkeypatch.setattr(main_module, "scalper_engine", fake_engine)
    return fake_engine


class TestTvWebhookSourceLogging:
    """Endpoint'in GERÇEK kod yolu: allowlist dışı ?src= WARNING loglar ve
    yanıt source_raw_rejected=True taşır; bilinen ?src= sessiz geçer."""

    async def test_unknown_src_logs_warning_and_flags_response(
        self, monkeypatch, _tv_webhook_ready
    ):
        warnings = []
        monkeypatch.setattr(
            main_module.app_logger, "warning", lambda msg: warnings.append(msg)
        )
        body = json.dumps({"secret": SECRET, "symbol": "BTCUSDT", "side": "buy"})
        request = _FakeRequest(body.encode(), {"src": "algpro"})  # yazım hatası

        result = await main_module.tradingview_webhook(request)

        assert result["source"] == "tv"
        assert result["source_raw_rejected"] is True
        assert len(warnings) == 1
        assert "algpro" in warnings[0]

    async def test_known_src_passes_through_without_warning(
        self, monkeypatch, _tv_webhook_ready
    ):
        warnings = []
        monkeypatch.setattr(
            main_module.app_logger, "warning", lambda msg: warnings.append(msg)
        )
        body = json.dumps({"secret": SECRET, "symbol": "BTCUSDT", "side": "buy"})
        request = _FakeRequest(body.encode(), {"src": "LuxOSC"})

        result = await main_module.tradingview_webhook(request)

        assert result["source"] == "luxosc"
        assert "source_raw_rejected" not in result
        assert warnings == []


class TestTvEntrySourceBlocklist:
    """D28: kötü performanslı basit TV kaynağı scalper oyundan kesilir;
    ret yine niyet/karşı-olgu defterine yazılır."""

    async def test_algopro_karantinasi_emir_oyu_ve_forward_acmaz(
        self, monkeypatch, _tv_webhook_ready
    ):
        recorded = []
        counterfactual = []
        forwarded = []
        monkeypatch.setattr(
            main_module.settings, "tv_entry_source_blocklist", " AlgoPro "
        )
        monkeypatch.setattr(
            main_module.scalp_intent,
            "record",
            lambda **kwargs: recorded.append(kwargs),
        )
        monkeypatch.setattr(
            main_module.counterfactual_store,
            "register",
            lambda **kwargs: counterfactual.append(kwargs),
        )
        monkeypatch.setattr(
            main_module,
            "_maybe_forward_to_follower",
            lambda *args, **kwargs: forwarded.append((args, kwargs)),
        )
        body = json.dumps(
            {"secret": SECRET, "symbol": "BTCUSDT", "side": "buy"}
        )

        result = await main_module.tradingview_webhook(
            _FakeRequest(body.encode(), {"src": "algopro"})
        )

        assert result == {
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "accepted": False,
            "blocked_by": "tv_source_blocked",
            "source": "algopro",
        }
        _tv_webhook_ready.external_signal.assert_not_awaited()
        assert forwarded == []
        assert recorded[0]["reason"] == "tv_source_blocked"
        assert counterfactual[0]["reason"] == "tv_source_blocked"

    async def test_lux_kaynagi_karantinadan_etkilenmez(
        self, monkeypatch, _tv_webhook_ready
    ):
        monkeypatch.setattr(
            main_module.settings, "tv_entry_source_blocklist", "algopro"
        )
        body = json.dumps(
            {"secret": SECRET, "symbol": "ETHUSDT", "side": "sell"}
        )
        result = await main_module.tradingview_webhook(
            _FakeRequest(body.encode(), {"src": "luxosc"})
        )
        assert result["accepted"] is True
        assert result["source"] == "luxosc"
        _tv_webhook_ready.external_signal.assert_awaited_once()

    async def test_dry_run_karantina_kararini_yan_etkisiz_gosterir(
        self, monkeypatch, _tv_webhook_ready
    ):
        monkeypatch.setattr(
            main_module.settings, "tv_entry_source_blocklist", "algopro"
        )
        body = json.dumps(
            {"secret": SECRET, "symbol": "SOLUSDT", "side": "buy"}
        )
        result = await main_module.tradingview_webhook(
            _FakeRequest(
                body.encode(), {"src": "algopro", "dry_run": "true"}
            )
        )
        assert result["dry_run"] is True
        assert result["would"]["accepted"] is False
        assert result["would"]["blocked_by"] == "tv_source_blocked"
        _tv_webhook_ready.external_signal.assert_not_awaited()
