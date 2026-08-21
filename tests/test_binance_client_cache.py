"""REST ağırlık diyeti: süreç-geneli okuma önbellekleri (2026-08-15).

Kök olay: positionRisk/account polling'i 2400 weight/dk sınırını düzenli
aşıp 418 ban döngüsü yarattı; ban körlüğünde 5 pozisyon günlerce "hayalet"
kaldı ve restart'ta toplu UNKNOWN kapanışıyla (−89 USDT) deftere indi.

Sözleşme:
- get_position_risk okumaları 5 sn'lik paylaşılan anlık görüntüden beslenir;
  sembolsüz tek weight-5 çağrı tüm sembolleri getirir.
- GÜVENLİK KURALI: önbellek "pozisyon sıfır / kayıt yok" diyorsa taze
  doğrulama ZORUNLU — bayat görüntüyle asla 'kapandı' kararı verilmez.
- force_fresh=True her zaman ağa çıkar (kurtarma akışları).
- Yazma istekleri (POST/DELETE) pozisyon + hesap önbelleklerini düşürür.
- get_account_balance/get_wallet_balance 15 sn'lik account önbelleğini,
  get_current_price 2.5 sn'lik fiyat önbelleğini paylaşır;
  get_all_positions (kurtarma) daima taze okur.
"""

from unittest.mock import MagicMock

import pytest

from src.trading.binance_client_improved import ImprovedBinanceClient


@pytest.fixture(autouse=True)
def _reset_read_caches():
    """Sınıf düzeyi önbellek durumu testler arasına sızmasın."""

    def _reset():
        ImprovedBinanceClient._write_generation = 0
        ImprovedBinanceClient._pos_snapshot = None
        ImprovedBinanceClient._pos_snapshot_ts = 0.0
        ImprovedBinanceClient._pos_snapshot_gen = -1
        ImprovedBinanceClient._pos_snapshot_lock = None
        ImprovedBinanceClient._account_cache = None
        ImprovedBinanceClient._account_cache_ts = 0.0
        ImprovedBinanceClient._account_cache_lock = None
        ImprovedBinanceClient._price_cache = {}
        ImprovedBinanceClient._rest_blocked_until = 0.0

    _reset()
    yield
    _reset()


def _bare_client() -> ImprovedBinanceClient:
    client = object.__new__(ImprovedBinanceClient)
    client.logger = MagicMock()
    client.base_url = "https://testnet.invalid"
    return client


def _pos(symbol: str, amt: str) -> dict:
    return {"symbol": symbol, "positionAmt": amt, "entryPrice": "100.0"}


def _install_position_feed(client, responses_log, feed):
    """_request_with_retry yerine sahte besleme: her çağrı log'a düşer."""

    async def fake_request(method, endpoint, params=None, signed=False):
        responses_log.append((method, endpoint))
        return list(feed)

    client._request_with_retry = fake_request


class TestPositionSnapshot:
    async def test_snapshot_shared_across_symbols_single_call(self):
        client = _bare_client()
        calls = []
        _install_position_feed(
            client, calls, [_pos("BTCUSDT", "0.5"), _pos("ETHUSDT", "-1.0")]
        )

        btc = await client.get_position_risk("BTCUSDT")
        eth = await client.get_position_risk("ETHUSDT")

        assert btc["positionAmt"] == "0.5"
        assert eth["positionAmt"] == "-1.0"
        # İki sembol okuması tek sembolsüz çağrıyı paylaştı
        assert calls == [("GET", "/fapi/v2/positionRisk")]
        # Sembol parametresiz çağrıldı (params yok → endpoint kaydı yeterli)

    async def test_cached_zero_forces_fresh_verification(self):
        """Bayat 'sıfır' ile kapandı kararı verilemez: sıfır görünen sembol
        önbellek taze bile olsa yeniden ağdan doğrulanır."""
        client = _bare_client()
        calls = []
        _install_position_feed(
            client, calls, [_pos("BTCUSDT", "0.5"), _pos("ETHUSDT", "0")]
        )

        await client.get_position_risk("BTCUSDT")   # snapshot kuruldu (1 çağrı)
        assert len(calls) == 1
        # Snapshot 1 sn'den taze — kilit içi kısa devre taze doğrulama sayılır
        eth = await client.get_position_risk("ETHUSDT")
        assert eth["positionAmt"] == "0"
        assert len(calls) == 1

        # Snapshot'ı 1 sn'lik taze penceresinden çıkar ama TTL içinde tut:
        ImprovedBinanceClient._pos_snapshot_ts -= 2.0
        nonzero = await client.get_position_risk("BTCUSDT")  # amt≠0 → önbellek
        assert nonzero["positionAmt"] == "0.5"
        assert len(calls) == 1
        zero = await client.get_position_risk("ETHUSDT")     # amt=0 → taze zorunlu
        assert zero["positionAmt"] == "0"
        assert len(calls) == 2

    async def test_missing_symbol_returns_none_after_fresh_fetch(self):
        client = _bare_client()
        calls = []
        _install_position_feed(client, calls, [_pos("BTCUSDT", "0.5")])

        assert await client.get_position_risk("DOGEUSDT") is None
        assert len(calls) == 1

    async def test_force_fresh_always_hits_network(self):
        client = _bare_client()
        calls = []
        _install_position_feed(client, calls, [_pos("BTCUSDT", "0.5")])

        await client.get_position_risk("BTCUSDT", force_fresh=True)
        await client.get_position_risk("BTCUSDT", force_fresh=True)
        assert len(calls) == 2

    async def test_mutation_invalidates_snapshot(self):
        client = _bare_client()
        calls = []
        _install_position_feed(client, calls, [_pos("BTCUSDT", "0.5")])

        await client.get_position_risk("BTCUSDT")
        assert len(calls) == 1
        ImprovedBinanceClient._invalidate_read_caches()  # sembolsüz yazma kancası
        await client.get_position_risk("BTCUSDT")
        assert len(calls) == 2

    async def test_symbol_write_invalidates_only_that_symbol(self):
        """Sembollü yazma yalnız o sembolü düşürür: A'ya stop replace atmak
        aynı turdaki B okumasına weight-5 taze fetch ödetmemeli."""
        client = _bare_client()
        calls = []
        _install_position_feed(
            client, calls, [_pos("BTCUSDT", "0.5"), _pos("ETHUSDT", "-1.0")]
        )

        await client.get_position_risk("BTCUSDT")
        assert len(calls) == 1
        ImprovedBinanceClient._invalidate_read_caches("BTCUSDT")  # sembollü yazma
        # ETH girdisi snapshot'ta ve nonzero: önbellekten servis edilir
        eth = await client.get_position_risk("ETHUSDT")
        assert eth["positionAmt"] == "-1.0"
        assert len(calls) == 1
        # Yazılan sembol snapshot'tan düştü VE jenerasyon arttı → taze fetch
        btc = await client.get_position_risk("BTCUSDT")
        assert btc["positionAmt"] == "0.5"
        assert len(calls) == 2

    async def test_write_during_fetch_prevents_fresh_stamp(self):
        """Fetch havadayken gelen yazma, emir-öncesi yanıtın 'taze' olarak
        damgalanmasını engeller (jenerasyon bekçisi)."""
        client = _bare_client()
        calls = []

        async def fake_request(method, endpoint, params=None, signed=False):
            calls.append((method, endpoint))
            # GET sürerken eşzamanlı bir POST tamamlanıyor (yarış simülasyonu)
            ImprovedBinanceClient._invalidate_read_caches("BTCUSDT")
            return [_pos("BTCUSDT", "0")]  # emir-öncesi durum: pozisyon yok

        client._request_with_retry = fake_request

        first = await client.get_position_risk("BTCUSDT")
        assert first["positionAmt"] == "0"  # yanıt çağırana yine de döner
        # Ama snapshot taze diye damgalanmadı
        assert ImprovedBinanceClient._pos_snapshot is None
        # Sonraki okuma yeniden ağa çıkar — bayat sıfır servis edilmez
        await client.get_position_risk("BTCUSDT")
        assert len(calls) == 2


class TestAccountCache:
    async def test_balance_reads_share_single_account_call(self):
        client = _bare_client()
        calls = []

        async def fake_request(method, endpoint, params=None, signed=False):
            calls.append((method, endpoint))
            return {
                "totalWalletBalance": "1000.0",
                "assets": [{"asset": "USDT", "availableBalance": "900.0",
                            "walletBalance": "1000.0"}],
                "positions": [],
            }

        client._request_with_retry = fake_request

        assert await client.get_account_balance() == 900.0
        assert await client.get_account_balance() == 900.0
        assert await client.get_wallet_balance() == 1000.0
        assert calls == [("GET", "/fapi/v2/account")]

    async def test_recovery_get_all_positions_always_fresh(self):
        client = _bare_client()
        calls = []

        async def fake_request(method, endpoint, params=None, signed=False):
            calls.append((method, endpoint))
            return {"assets": [], "positions": [_pos("BTCUSDT", "0.5")]}

        client._request_with_retry = fake_request

        await client.get_account_balance()          # önbelleği doldurur
        positions = await client.get_all_positions()  # yine de taze okur
        assert len(calls) == 2
        assert positions[0]["symbol"] == "BTCUSDT"

    async def test_mutation_invalidates_account_cache(self):
        client = _bare_client()
        calls = []

        async def fake_request(method, endpoint, params=None, signed=False):
            calls.append((method, endpoint))
            return {"assets": [{"asset": "USDT", "availableBalance": "1.0",
                                "walletBalance": "1.0"}]}

        client._request_with_retry = fake_request

        await client.get_account_balance()
        ImprovedBinanceClient._invalidate_read_caches()
        await client.get_account_balance()
        assert len(calls) == 2


class TestPriceCache:
    async def test_same_symbol_cached_distinct_symbols_fetch(self):
        client = _bare_client()
        calls = []

        async def fake_request(method, endpoint, params=None, signed=False):
            calls.append(params["symbol"])
            return {"price": "42.5"}

        client._request_with_retry = fake_request

        assert await client.get_current_price("BTCUSDT") == 42.5
        assert await client.get_current_price("BTCUSDT") == 42.5
        assert await client.get_current_price("ETHUSDT") == 42.5
        assert calls == ["BTCUSDT", "ETHUSDT"]
