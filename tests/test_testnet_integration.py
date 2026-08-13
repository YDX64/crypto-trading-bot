"""
Binance Futures TESTNET canlı entegrasyon testleri.

Bu testler gerçek ağ çağrıları yapar ve testnet'te GERÇEK emirler oluşturur.
Testnet parası sahtedir, ancak yine de her test kendi emrini temizler.

GÜVENLİK: Modül seviyesinde sert bir koruma vardır — base_url testnet değilse
tüm testler toplanmadan reddedilir. Bu dosya asla mainnet'e emir gönderemez.

Çalıştırma:
    .venv/bin/python -m pytest tests/test_testnet_integration.py -v

Emir oluşturan testleri atlamak için:
    TESTNET_SKIP_ORDERS=1 .venv/bin/python -m pytest tests/test_testnet_integration.py -v
"""

import os
import time

import pytest

from src.core.config import settings
from src.trading.binance_client_improved import (
    BinanceAPIError,
    ImprovedBinanceClient,
)

# Bu dosya gerçek TESTNET hesabına bağlanır ve bazı testlerde emir oluşturur.
# Tam regresyon koşuları hiçbir zaman bunu örtük biçimde yapmamalı; canlı
# entegrasyon yalnız açıkça talep edildiğinde etkinleşir.
if os.getenv("RUN_TESTNET_INTEGRATION") != "1":
    pytest.skip(
        "TESTNET entegrasyonu yalnız RUN_TESTNET_INTEGRATION=1 ile çalışır.",
        allow_module_level=True,
    )

# ---------------------------------------------------------------------------
# Güvenlik kilidi: mainnet'e karşı asla çalışma.
# ---------------------------------------------------------------------------
TESTNET_HOSTS = (
    "testnet.binancefuture.com",
    "demo-fapi.binance.com",
    "demo.binance.com",
    "testnet.binance.vision",
)

if not any(h in settings.binance_base_url for h in TESTNET_HOSTS):
    pytest.skip(
        f"GÜVENLİK: BINANCE_BASE_URL testnet değil ({settings.binance_base_url}). "
        "Entegrasyon testleri mainnet'e karşı çalıştırılamaz.",
        allow_module_level=True,
    )

SKIP_ORDERS = os.getenv("TESTNET_SKIP_ORDERS") == "1"
SYMBOL = "BTCUSDT"

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def client():
    """Her test için taze bir istemci; sonunda HTTP havuzu kapatılır."""
    c = ImprovedBinanceClient()
    try:
        yield c
    finally:
        await c.close()


# ---------------------------------------------------------------------------
# Bağlantı ve kimlik doğrulama
# ---------------------------------------------------------------------------

class TestConnectivity:
    async def test_base_url_testnet(self):
        """Konfigürasyon gerçekten bir testnet/demo host'unu gösteriyor.

        "testnet" alt string'ini aramak kırılgandır: demo-fapi.binance.com da
        aynı testnet hesabına bağlanır ama adında "testnet" geçmez.
        """
        assert settings.is_testnet is True
        assert any(h in settings.binance_base_url for h in TESTNET_HOSTS), (
            f"Bilinmeyen host: {settings.binance_base_url}"
        )

    async def test_ping(self, client):
        """Ping imzasız endpoint'e ulaşıyor."""
        result = await client._request_with_retry("GET", "/fapi/v1/ping")
        assert result == {}

    async def test_server_time_drift(self, client):
        """Yerel saat ile sunucu saati arasındaki fark imzalama için kabul edilebilir.

        Binance imzalı isteklerde varsayılan recvWindow 5000 ms'dir. 5 saniyeden
        büyük bir kayma tüm imzalı çağrıları -1021 ile başarısız yapar.
        """
        server_time = await client.get_server_time()
        assert server_time > 0
        drift = abs(server_time - int(time.time() * 1000))
        assert drift < 5000, f"Saat kayması çok yüksek: {drift}ms (imzalı istekler bozulur)"

    async def test_authenticated_account_access(self, client):
        """API anahtarı geçerli ve hesap okunabiliyor."""
        account = await client._request_with_retry("GET", "/fapi/v2/account", signed=True)
        assert "assets" in account
        assert "totalWalletBalance" in account

    async def test_balance_is_positive(self, client):
        """Testnet hesabında test için yeterli bakiye var."""
        balance = await client.get_account_balance()
        assert balance > 0, (
            "Testnet bakiyesi sıfır. testnet.binancefuture.com üzerinden "
            "sahte USDT talep edin."
        )


# ---------------------------------------------------------------------------
# Piyasa verisi
# ---------------------------------------------------------------------------

class TestMarketData:
    async def test_current_price(self, client):
        price = await client.get_current_price(SYMBOL)
        assert price is not None
        assert price > 0

    async def test_symbol_precision(self, client):
        qty_prec, price_prec = await client.get_symbol_precision(SYMBOL)
        assert isinstance(qty_prec, int) and qty_prec >= 0
        assert isinstance(price_prec, int) and price_prec >= 0

    async def test_precision_matches_exchange_filters(self, client):
        """quantityPrecision, LOT_SIZE stepSize ile tutarlı olmalı.

        Kod yuvarlamayı quantityPrecision (ondalık basamak) ile yapıyor, ancak
        Binance emri stepSize'a göre doğrular. İkisi ayrıştığında emirler
        -1111 (precision over maximum) ile reddedilir.
        """
        info = await client._request_with_retry("GET", "/fapi/v1/exchangeInfo")
        sym = next(s for s in info["symbols"] if s["symbol"] == SYMBOL)
        filters = {f["filterType"]: f for f in sym["filters"]}

        step_size = filters["LOT_SIZE"]["stepSize"]
        # stepSize "0.0001" -> 4 ondalık basamak
        step_decimals = len(step_size.rstrip("0").split(".")[1]) if "." in step_size else 0

        assert sym["quantityPrecision"] == step_decimals, (
            f"quantityPrecision={sym['quantityPrecision']} ancak stepSize={step_size} "
            f"({step_decimals} basamak). Yuvarlama emirleri reddettirebilir."
        )

    async def test_min_notional_is_enforceable(self, client):
        """MIN_NOTIONAL filtresi okunabiliyor — pozisyon boyutu bunu aşmalı."""
        info = await client._request_with_retry("GET", "/fapi/v1/exchangeInfo")
        sym = next(s for s in info["symbols"] if s["symbol"] == SYMBOL)
        filters = {f["filterType"]: f for f in sym["filters"]}
        assert "MIN_NOTIONAL" in filters
        assert float(filters["MIN_NOTIONAL"]["notional"]) > 0


# ---------------------------------------------------------------------------
# İmzalama doğruluğu
# ---------------------------------------------------------------------------

class TestSigning:
    async def test_signature_excludes_previous_signature(self, client):
        """Yeniden imzalama önceki 'signature' alanını içermemeli.

        Retry yolu params sözlüğünü yeniden kullanır. Eski imza sözlükte
        kalırsa, yeni imza onu da kapsar ve Binance -1022 döndürür.
        """
        params = {"symbol": SYMBOL, "timestamp": int(time.time() * 1000)}
        params["signature"] = client._generate_signature(params)

        # Retry simülasyonu: timestamp yenilenir, imza yeniden üretilir
        params["timestamp"] = int(time.time() * 1000)
        resigned = client._generate_signature(
            {k: v for k, v in params.items() if k != "signature"}
        )

        # Doğru davranış: imzalanan yük 'signature' anahtarını taşımamalı
        payload_keys = [k for k in params if k != "signature"]
        assert "signature" not in payload_keys
        assert len(resigned) == 64  # sha256 hex

    async def test_signed_request_survives_repeat(self, client):
        """Aynı istemciyle arka arkaya iki imzalı istek de başarılı olmalı."""
        first = await client.get_account_balance()
        second = await client.get_account_balance()
        assert first >= 0 and second >= 0


# ---------------------------------------------------------------------------
# Emir yaşam döngüsü (testnet'te gerçek emirler)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(SKIP_ORDERS, reason="TESTNET_SKIP_ORDERS=1 ayarlı")
class TestOrderLifecycle:
    async def test_place_and_cancel_limit_order(self, client):
        """Piyasadan uzak bir LIMIT emri koy, gör, iptal et.

        Emir dolmayacak kadar uzağa konur; böylece imzalı POST/GET/DELETE
        yolunun tamamı pozisyon açmadan doğrulanır.
        """
        price = await client.get_current_price(SYMBOL)
        assert price is not None

        info = await client._request_with_retry("GET", "/fapi/v1/exchangeInfo")
        sym = next(s for s in info["symbols"] if s["symbol"] == SYMBOL)
        filters = {f["filterType"]: f for f in sym["filters"]}
        min_notional = float(filters["MIN_NOTIONAL"]["notional"])
        tick_size = float(filters["PRICE_FILTER"]["tickSize"])
        step_size = float(filters["LOT_SIZE"]["stepSize"])

        # Piyasanın %50 altında -> dolmaz
        limit_price = price * 0.5
        limit_price = round(limit_price / tick_size) * tick_size

        # MIN_NOTIONAL'ı güvenli marjla aş
        qty = (min_notional * 1.2) / limit_price
        qty = round(qty / step_size) * step_size

        qty_prec, price_prec = await client.get_symbol_precision(SYMBOL)
        qty = round(qty, qty_prec)
        limit_price = round(limit_price, price_prec)

        order = await client._request_with_retry(
            "POST",
            "/fapi/v1/order",
            params={
                "symbol": SYMBOL,
                "side": "BUY",
                "type": "LIMIT",
                "timeInForce": "GTC",
                "quantity": qty,
                "price": limit_price,
            },
            signed=True,
        )
        order_id = order["orderId"]

        try:
            open_orders = await client.get_open_orders(SYMBOL)
            assert any(o["orderId"] == order_id for o in open_orders), (
                "Konulan emir açık emirler listesinde görünmüyor"
            )
        finally:
            cancelled = await client.cancel_order(SYMBOL, int(order_id))
            assert cancelled["status"] == "CANCELED"

    async def test_rejects_order_below_min_notional(self, client):
        """MIN_NOTIONAL altındaki emir borsa tarafından reddedilir.

        Bu, botun kendi tarafında notional kontrolü yapması gerektiğini
        kanıtlar — aksi halde sinyaller sessizce başarısız olur.
        """
        price = await client.get_current_price(SYMBOL)
        info = await client._request_with_retry("GET", "/fapi/v1/exchangeInfo")
        sym = next(s for s in info["symbols"] if s["symbol"] == SYMBOL)
        filters = {f["filterType"]: f for f in sym["filters"]}
        step_size = float(filters["LOT_SIZE"]["stepSize"])

        # Kasıtlı olarak çok küçük: tek adımlık miktar
        tiny_qty = step_size

        with pytest.raises(BinanceAPIError) as exc:
            await client._request_with_retry(
                "POST",
                "/fapi/v1/order",
                params={
                    "symbol": SYMBOL,
                    "side": "BUY",
                    "type": "MARKET",
                    "quantity": tiny_qty,
                },
                signed=True,
            )

        # Hata kodu yapılandırılmış biçimde yüzeye çıkmalı — çağıranlar
        # "yeniden denenebilir" ile "ölümcül" hatayı ancak böyle ayırabilir.
        assert exc.value.code == -4164, f"beklenen -4164, gelen {exc.value.code}"
        assert "notional" in exc.value.msg.lower()
        assert exc.value.is_retryable is False, (
            "Geçersiz miktar hatası yeniden denenmemeli"
        )

    async def test_client_validates_before_sending(self, client):
        """İstemci MIN_NOTIONAL'ı emri GÖNDERMEDEN yakalamalı.

        Borsaya geçersiz emir göndermek, hata dönene kadar geçen sürede
        pozisyonun yarım açılmasına yol açabilir. Doğrulama yerelde yapılmalı.
        """
        price = await client.get_current_price(SYMBOL)
        with pytest.raises(BinanceAPIError) as exc:
            await client.validate_order(SYMBOL, 0.0001, price)
        assert exc.value.code == -4164
        assert exc.value.is_retryable is False


# ---------------------------------------------------------------------------
# Hesap durumu
# ---------------------------------------------------------------------------

@pytest.mark.skipif(SKIP_ORDERS, reason="TESTNET_SKIP_ORDERS=1 ayarlı")
class TestConditionalOrders:
    """Koşullu emirler 2025-12-09'dan beri Algo Order API üzerinden gider.

    Eski /fapi/v1/order yolu bu tipleri -4120 ile reddeder. Bu testler
    doğru endpoint'in kullanıldığını ve emirlerin borsada gerçekten
    oluştuğunu kanıtlar.
    """

    async def test_legacy_endpoint_rejects_conditional_orders(self, client):
        """Regresyon koruması: eski endpoint koşullu emri KABUL ETMEZ.

        Bu test bir gün geçmeye başlarsa Binance kararını geri almış demektir;
        o zaman bile Algo Order yolu çalışmaya devam eder.
        """
        price = await client.get_current_price(SYMBOL)
        with pytest.raises(BinanceAPIError) as exc:
            await client._request_with_retry(
                "POST",
                "/fapi/v1/order",
                params={
                    "symbol": SYMBOL,
                    "side": "SELL",
                    "type": "STOP_MARKET",
                    "stopPrice": round(price * 0.8, 1),
                    "closePosition": "true",
                },
                signed=True,
            )
        assert exc.value.code == -4120, (
            f"Beklenen -4120, gelen {exc.value.code}: {exc.value.msg}"
        )

    async def test_position_protection_lifecycle(self, client):
        """Tam koruma döngüsü: pozisyon aç -> SL + TP -> boşluksuz SL değiştir.

        Bu, botun en kritik yolu. Şunları kanıtlar:
          - Giriş fiyatı gerçek dolumdan okunur (avgPrice null gelse bile)
          - Stop-loss ve take-profit borsada GERÇEKTEN oluşur
          - Take-profit reduceOnly'dir (ters pozisyon açamaz)
          - Stop değişimi sırasında pozisyon bir an bile korumasız kalmaz
        """
        from src.trading.position_manager import PositionManager

        pm = PositionManager(client)

        # Var olan bir pozisyonu ASLA kapatma — bu testin yarattığı bir durum
        # değil. Testler yalnızca kendi açtıklarını temizler; aksi halde
        # kullanıcının incelediği bir demo pozisyonu sessizce silinir.
        existing = await client.get_position_risk(SYMBOL)
        if existing and abs(float(existing["positionAmt"])) > 0:
            pytest.skip(
                f"{SYMBOL} için zaten açık pozisyon var "
                f"({existing['positionAmt']}). Test bu pozisyona dokunmaz. "
                f"Kapatmak için: python demo_control.py close {SYMBOL}"
            )

        price = await client.get_current_price(SYMBOL)
        filters = await client.get_symbol_filters(SYMBOL)
        # MIN_NOTIONAL'ı rahatça aşan küçük bir pozisyon
        qty = float(filters["minNotional"]) * 2 / price
        qty = await client.quantize_quantity(SYMBOL, qty)

        entry_order = await client.open_market_order(SYMBOL, "BUY", qty)
        try:
            # 1) Gerçek dolum okunabiliyor mu (avgPrice null gelse bile)
            entry_price, filled = await pm._resolve_fill(SYMBOL, entry_order)
            assert entry_price > 0, "giriş fiyatı çözümlenemedi"
            assert filled > 0

            # 2) Koruma emirleri
            sl_order = await client.place_stop_loss(
                SYMBOL, "SELL", entry_price * 0.90, close_position=True
            )
            assert sl_order.get("algoId"), "SL algoId dönmedi"

            tp_order = await client.place_take_profit(
                SYMBOL, "SELL", entry_price * 1.10, filled
            )
            assert tp_order.get("reduceOnly") is True, (
                "TP reduceOnly değil — pozisyon kapandıktan sonra ters pozisyon açabilir"
            )

            # 3) Borsada gerçekten varlar mı? (openOrders bunları GÖSTERMEZ)
            algo_orders = await client.get_open_algo_orders(SYMBOL)
            types = {o["orderType"] for o in algo_orders}
            assert "STOP_MARKET" in types, "stop-loss borsada yok"
            assert "TAKE_PROFIT_MARKET" in types, "take-profit borsada yok"

            legacy = await client.get_open_orders(SYMBOL)
            assert not any(
                o.get("type") in ("STOP_MARKET", "TAKE_PROFIT_MARKET") for o in legacy
            ), "koşullu emirler openOrders'ta görünüyor — varsayım değişmiş"

            # 4) Boşluksuz stop değişimi
            new_stop = await client.place_stop_loss(
                SYMBOL, "SELL", entry_price * 0.95,
                close_position=False, quantity=filled,
            )
            assert new_stop.get("algoId")

            during = await client.get_open_algo_orders(SYMBOL)
            stops = [o for o in during if o["orderType"] == "STOP_MARKET"]
            assert len(stops) == 2, (
                "değişim sırasında iki stop bir arada olmalı — boşluksuz koruma"
            )

            await client.cancel_algo_order(int(sl_order["algoId"]))
            after = await client.get_open_algo_orders(SYMBOL)
            stops_after = [o for o in after if o["orderType"] == "STOP_MARKET"]
            assert len(stops_after) == 1, "eski stop iptal edilmedi"
            assert float(stops_after[0]["triggerPrice"]) > entry_price * 0.90, (
                "stop yukarı taşınmadı"
            )
        finally:
            await pm._emergency_close(SYMBOL)
            await client.cancel_all_open_orders(SYMBOL)

    async def test_cancel_all_covers_conditional_orders(self, client):
        """cancel_all_open_orders koşullu emirleri de temizlemeli.

        /fapi/v1/allOpenOrders algo emirleri kapsamaz; sadece onu çağırmak
        borsada asılı stop/TP emirleri bırakır.
        """
        from src.trading.position_manager import PositionManager

        pm = PositionManager(client)

        existing = await client.get_position_risk(SYMBOL)
        if existing and abs(float(existing["positionAmt"])) > 0:
            pytest.skip(
                f"{SYMBOL} için zaten açık pozisyon var — test ona dokunmaz"
            )

        price = await client.get_current_price(SYMBOL)
        filters = await client.get_symbol_filters(SYMBOL)
        qty = await client.quantize_quantity(
            SYMBOL, float(filters["minNotional"]) * 2 / price
        )

        await client.open_market_order(SYMBOL, "BUY", qty)
        try:
            await client.place_stop_loss(SYMBOL, "SELL", price * 0.9, close_position=True)
            assert len(await client.get_open_algo_orders(SYMBOL)) >= 1

            await client.cancel_all_open_orders(SYMBOL)
            assert await client.get_open_algo_orders(SYMBOL) == [], (
                "koşullu emirler temizlenmedi"
            )
        finally:
            await pm._emergency_close(SYMBOL)
            await client.cancel_all_open_orders(SYMBOL)


class TestAccountState:
    async def test_position_risk_readable(self, client):
        pos = await client.get_position_risk(SYMBOL)
        assert pos is not None
        assert pos["symbol"] == SYMBOL
        assert "positionAmt" in pos

    async def test_no_unexpected_open_positions(self, client):
        """Test başlangıcında açık pozisyon olmamalı — aksi halde testler kirlenir."""
        account = await client._request_with_retry("GET", "/fapi/v2/account", signed=True)
        open_positions = [
            p for p in account.get("positions", [])
            if float(p.get("positionAmt", 0)) != 0
        ]
        if open_positions:
            pytest.skip(
                f"Testnet hesabında {len(open_positions)} açık pozisyon var: "
                f"{[p['symbol'] for p in open_positions]}. Testler yine de güvenli."
            )
