"""
Geliştirilmiş Binance Futures API client.
Daha iyi bağlantı yönetimi, hata toleransı ve retry mekanizması ile.
"""

import hmac
import hashlib
import time
import asyncio
from typing import Dict, Any, Optional, List
from decimal import Decimal, ROUND_DOWN
import httpx

from src.core.config import settings
from src.core.logger import app_logger
from src.core.rate_limiter import rate_limiter


class ImprovedBinanceClient:
    """Geliştirilmiş Binance Futures API istemcisi"""

    def __init__(self):
        self.api_key = settings.binance_api_key
        self.api_secret = settings.binance_api_secret
        self.base_url = settings.binance_base_url
        self.logger = app_logger

        # HTTP client'ı daha iyi yapılandır
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=10.0),  # Daha uzun timeout
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
            follow_redirects=True
        )

        # Connection pooling ve retry settings
        self.max_retries = 3
        self.retry_delay = 2.0  # saniye

    def _generate_signature(self, params: Dict[str, Any]) -> str:
        """API signature oluştur"""
        query_string = "&".join([f"{k}={v}" for k, v in params.items()])
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        return signature

    def _get_headers(self) -> Dict[str, str]:
        """API headers - geliştirilmiş"""
        return {
            "X-MBX-APIKEY": self.api_key,
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Mozilla/5.0 TradingBot/2.0"  # User agent ekle
        }

    async def _request_with_retry(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        signed: bool = False
    ) -> Dict[str, Any]:
        """API isteği yap - retry mekanizması ile"""

        for attempt in range(self.max_retries):
            try:
                # Rate limiting
                await rate_limiter.wait_for_binance()

                url = f"{self.base_url}{endpoint}"
                params = params or {}

                if signed:
                    # Timestamp - server sync için çok yakın zaman kullan
                    params["timestamp"] = int(time.time() * 1000)
                    # Binance testnet'te timestamp sync problemi için ufak düzeltme
                    if settings.is_testnet:
                        params["timestamp"] -= 500  # Testnet için 500ms geri
                    params["signature"] = self._generate_signature(params)

                headers = self._get_headers() if signed else {}

                # İsteği gönder
                if method == "GET":
                    response = await self.client.get(url, params=params, headers=headers)
                elif method == "POST":
                    response = await self.client.post(url, params=params, headers=headers)
                elif method == "DELETE":
                    response = await self.client.delete(url, params=params, headers=headers)
                else:
                    raise ValueError(f"Desteklenmeyen HTTP metodu: {method}")

                response.raise_for_status()
                return response.json()

            except httpx.HTTPStatusError as e:
                error_msg = f"Binance API hatası [{e.response.status_code}]: {e.response.text}"
                self.logger.error(error_msg)

                # Özel hata durumları
                if e.response.status_code == 429:  # Rate limit
                    wait_time = 60  # 1 dakika bekle
                    self.logger.warning(f"Rate limit hit, {wait_time} saniye bekleniyor...")
                    await asyncio.sleep(wait_time)
                    continue

                elif e.response.status_code == 418:  # IP ban
                    self.logger.critical("IP ban algılandı! Yönetici müdahalesi gerekli.")
                    raise

                elif e.response.status_code == 403:  # Invalid API key
                    self.logger.critical("Geçersiz API anahtarı!")
                    raise

                elif e.response.status_code in [500, 502, 503, 504]:  # Server hatası
                    if attempt < self.max_retries - 1:
                        wait_time = self.retry_delay * (attempt + 1)
                        self.logger.warning(f"Server hatası, {wait_time}s sonra tekrar denenecek...")
                        await asyncio.sleep(wait_time)
                        continue

                # Diğer hatalar için exception fırlat
                raise

            except (httpx.ConnectTimeout, httpx.ReadTimeout) as e:
                if attempt < self.max_retries - 1:
                    wait_time = self.retry_delay * (attempt + 1)
                    self.logger.warning(f"Timeout hatası, {wait_time}s sonra tekrar denenecek...")
                    await asyncio.sleep(wait_time)
                    continue
                self.logger.error(f"Timeout hatası (tüm denemeler tükendi): {e}")
                raise

            except httpx.ConnectError as e:
                if attempt < self.max_retries - 1:
                    wait_time = self.retry_delay * (attempt + 1)
                    self.logger.warning(f"Bağlantı hatası, {wait_time}s sonra tekrar denenecek...")
                    await asyncio.sleep(wait_time)
                    continue
                self.logger.error(f"Bağlantı hatası (tüm denemeler tükendi): {e}")
                raise

            except Exception as e:
                self.logger.error(f"Beklenmeyen hata: {e}")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(self.retry_delay)
                    continue
                raise

        # Tüm denemeler başarısız
        raise Exception(f"API isteği {self.max_retries} denemeden sonra başarısız oldu")

    async def test_connection(self) -> bool:
        """Bağlantıyı test et"""
        try:
            self.logger.info("🔍 Binance bağlantısı test ediliyor...")

            # 1. Ping testi
            await self._request_with_retry("GET", "/fapi/v1/ping")
            self.logger.info("✅ Ping başarılı")

            # 2. Server zamanını kontrol et
            response = await self._request_with_retry("GET", "/fapi/v1/time")
            server_time = response.get("serverTime", 0)
            local_time = int(time.time() * 1000)
            time_diff = abs(server_time - local_time)

            if time_diff > 5000:  # 5 saniyeden fazla fark
                self.logger.warning(f"⚠️ Server zaman farkı yüksek: {time_diff}ms")
            else:
                self.logger.info(f"✅ Server zaman senkronu iyi: {time_diff}ms fark")

            # 3. API key kontrolü
            await self._request_with_retry("GET", "/fapi/v2/account", signed=True)
            self.logger.info("✅ API anahtarı geçerli")

            return True

        except Exception as e:
            self.logger.error(f"❌ Bağlantı testi başarısız: {e}")
            return False

    # Mevcut metodları yeni request metoduyla kullan
    async def get_account_balance(self) -> float:
        """Hesap bakiyesini getir (USDT)"""
        try:
            response = await self._request_with_retry("GET", "/fapi/v2/account", signed=True)

            for asset in response.get("assets", []):
                if asset["asset"] == "USDT":
                    balance = float(asset["availableBalance"])
                    self.logger.info(f"💰 Hesap bakiyesi: {balance:.2f} USDT")
                    return balance

            return 0.0
        except Exception as e:
            self.logger.error(f"Bakiye sorgusu hatası: {e}")
            return 0.0

    async def set_leverage(self, symbol: str, leverage: int) -> Dict[str, Any]:
        """Leverage ayarla"""
        params = {
            "symbol": symbol,
            "leverage": leverage
        }

        try:
            response = await self._request_with_retry("POST", "/fapi/v1/leverage", params=params, signed=True)
            self.logger.info(f"⚡ {symbol} leverage {leverage}x olarak ayarlandı")
            return response
        except Exception as e:
            self.logger.error(f"Leverage ayarlama hatası: {e}")
            raise

    async def set_margin_type(self, symbol: str, margin_type: str = "ISOLATED") -> Dict[str, Any]:
        """Margin type ayarla"""
        params = {
            "symbol": symbol,
            "marginType": margin_type.upper()
        }

        try:
            response = await self._request_with_retry("POST", "/fapi/v1/marginType", params=params, signed=True)
            self.logger.info(f"🔧 {symbol} margin type {margin_type} olarak ayarlandı")
            return response
        except httpx.HTTPStatusError as e:
            if "No need to change margin type" in e.response.text:
                self.logger.debug(f"Margin type zaten {margin_type}")
                return {"msg": "Already set"}
            raise

    async def get_symbol_precision(self, symbol: str) -> tuple[int, int]:
        """Symbol precision bilgisini getir (quantity, price)"""
        try:
            response = await self._request_with_retry("GET", "/fapi/v1/exchangeInfo")

            for s in response.get("symbols", []):
                if s["symbol"] == symbol:
                    quantity_precision = s["quantityPrecision"]
                    price_precision = s["pricePrecision"]
                    return quantity_precision, price_precision

            return 3, 2  # Default
        except Exception as e:
            self.logger.error(f"Precision sorgusu hatası: {e}")
            return 3, 2

    def round_quantity(self, quantity: float, precision: int) -> float:
        """Quantity'yi precision'a göre yuvarla"""
        return float(Decimal(str(quantity)).quantize(
            Decimal(f"0.{'0' * precision}"),
            rounding=ROUND_DOWN
        ))

    def round_price(self, price: float, precision: int) -> float:
        """Price'ı precision'a göre yuvarla"""
        return float(Decimal(str(price)).quantize(
            Decimal(f"0.{'0' * precision}"),
            rounding=ROUND_DOWN
        ))

    async def open_market_order(
        self,
        symbol: str,
        side: str,  # BUY or SELL
        quantity: float
    ) -> Dict[str, Any]:
        """Market order aç"""
        qty_precision, _ = await self.get_symbol_precision(symbol)
        quantity = self.round_quantity(quantity, qty_precision)

        params = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": quantity
        }

        try:
            response = await self._request_with_retry("POST", "/fapi/v1/order", params=params, signed=True)
            self.logger.info(
                f"🚀 Market order açıldı: {symbol} {side} {quantity}",
                extra={"trade": True}
            )
            return response
        except Exception as e:
            self.logger.error(f"Market order hatası: {e}")
            raise

    async def place_stop_loss(
        self,
        symbol: str,
        side: str,
        stop_price: float,
        close_position: bool = True
    ) -> Dict[str, Any]:
        """Stop loss order koy"""
        _, price_precision = await self.get_symbol_precision(symbol)
        stop_price = self.round_price(stop_price, price_precision)

        params = {
            "symbol": symbol,
            "side": side,
            "type": "STOP_MARKET",
            "stopPrice": stop_price,
            "closePosition": str(close_position).lower()
        }

        try:
            response = await self._request_with_retry("POST", "/fapi/v1/order", params=params, signed=True)
            self.logger.info(
                f"🛡️ Stop Loss kondu: {symbol} @ {stop_price}",
                extra={"trade": True}
            )
            return response
        except Exception as e:
            self.logger.error(f"Stop loss hatası: {e}")
            raise

    async def place_take_profit(
        self,
        symbol: str,
        side: str,
        stop_price: float,
        quantity: float
    ) -> Dict[str, Any]:
        """Take profit order koy"""
        qty_precision, price_precision = await self.get_symbol_precision(symbol)
        quantity = self.round_quantity(quantity, qty_precision)
        stop_price = self.round_price(stop_price, price_precision)

        params = {
            "symbol": symbol,
            "side": side,
            "type": "TAKE_PROFIT_MARKET",
            "stopPrice": stop_price,
            "quantity": quantity
        }

        try:
            response = await self._request_with_retry("POST", "/fapi/v1/order", params=params, signed=True)
            self.logger.info(
                f"🎯 Take Profit kondu: {symbol} {quantity} @ {stop_price}",
                extra={"trade": True}
            )
            return response
        except Exception as e:
            self.logger.error(f"Take profit hatası: {e}")
            raise

    async def cancel_order(self, symbol: str, order_id: int) -> Dict[str, Any]:
        """Order iptal et"""
        params = {
            "symbol": symbol,
            "orderId": order_id
        }

        try:
            response = await self._request_with_retry("DELETE", "/fapi/v1/order", params=params, signed=True)
            self.logger.info(f"❌ Order iptal edildi: {symbol} #{order_id}")
            return response
        except Exception as e:
            self.logger.error(f"Order iptal hatası: {e}")
            raise

    async def get_open_orders(self, symbol: str) -> List[Dict[str, Any]]:
        """Açık orderları getir"""
        params = {"symbol": symbol}

        try:
            response = await self._request_with_retry("GET", "/fapi/v1/openOrders", params=params, signed=True)
            return response
        except Exception as e:
            self.logger.error(f"Açık order sorgusu hatası: {e}")
            return []

    async def get_position_risk(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Pozisyon bilgilerini getir"""
        params = {"symbol": symbol}

        try:
            response = await self._request_with_retry("GET", "/fapi/v2/positionRisk", params=params, signed=True)

            if isinstance(response, list) and len(response) > 0:
                return response[0]
            return None
        except Exception as e:
            self.logger.error(f"Pozisyon sorgusu hatası: {e}")
            return None

    async def get_current_price(self, symbol: str) -> Optional[float]:
        """Güncel fiyatı getir"""
        params = {"symbol": symbol}

        try:
            response = await self._request_with_retry("GET", "/fapi/v1/ticker/price", params=params)
            price = float(response["price"])
            return price
        except Exception as e:
            self.logger.error(f"Fiyat sorgusu hatası: {e}")
            return None

    async def get_server_time(self) -> int:
        """Binance server zamanını getir"""
        try:
            response = await self._request_with_retry("GET", "/fapi/v1/time")
            return response.get("serverTime", 0)
        except Exception as e:
            self.logger.error(f"Server zamanı sorgusu hatası: {e}")
            return 0

    async def close(self):
        """HTTP client'ı kapat"""
        await self.client.aclose()