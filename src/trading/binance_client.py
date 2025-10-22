"""
Binance Futures API client.
Tüm Binance işlemleri bu modül üzerinden yapılır.
"""

import hmac
import hashlib
import time
from typing import Dict, Any, Optional, List
from decimal import Decimal, ROUND_DOWN
import httpx

from src.core.config import settings
from src.core.logger import app_logger
from src.core.rate_limiter import rate_limiter


class BinanceClient:
    """Binance Futures API istemcisi"""
    
    def __init__(self):
        self.api_key = settings.binance_api_key
        self.api_secret = settings.binance_api_secret
        self.base_url = settings.binance_base_url
        self.logger = app_logger
        self.client = httpx.AsyncClient(timeout=30.0)
    
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
        """API headers"""
        return {
            "X-MBX-APIKEY": self.api_key,
            "Content-Type": "application/x-www-form-urlencoded"
        }
    
    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        signed: bool = False
    ) -> Dict[str, Any]:
        """API isteği yap"""
        # Rate limiting
        await rate_limiter.wait_for_binance()
        
        url = f"{self.base_url}{endpoint}"
        params = params or {}
        
        if signed:
            # Timestamp 1000ms geriden gönder (server ahead hatasını önler)
            params["timestamp"] = int(time.time() * 1000) - 1000
            params["signature"] = self._generate_signature(params)
        
        headers = self._get_headers() if signed else {}
        
        try:
            if method == "GET":
                response = await self.client.get(url, params=params, headers=headers)
            elif method == "POST":
                # Binance POST için params olarak gönder (query string)
                response = await self.client.post(url, params=params, headers=headers)
            elif method == "DELETE":
                response = await self.client.delete(url, params=params, headers=headers)
            else:
                raise ValueError(f"Desteklenmeyen HTTP metodu: {method}")
            
            response.raise_for_status()
            return response.json()
        
        except httpx.HTTPStatusError as e:
            self.logger.error(f"Binance API hatası [{e.response.status_code}]: {e.response.text}")
            raise
        except Exception as e:
            self.logger.error(f"API istek hatası: {e}")
            raise
    
    async def get_account_balance(self) -> float:
        """Hesap bakiyesini getir (USDT)"""
        try:
            response = await self._request("GET", "/fapi/v2/account", signed=True)
            
            for asset in response.get("assets", []):
                if asset["asset"] == "USDT":
                    balance = float(asset["availableBalance"])
                    self.logger.info(f"💰 Hesap bakiyesi: {balance} USDT")
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
            response = await self._request("POST", "/fapi/v1/leverage", params=params, signed=True)
            self.logger.info(f"⚡ {symbol} leverage {leverage}x olarak ayarlandı")
            return response
        except Exception as e:
            self.logger.error(f"Leverage ayarlama hatası: {e}")
            raise
    
    async def set_margin_type(self, symbol: str, margin_type: str = "ISOLATED") -> Dict[str, Any]:
        """Margin type ayarla"""
        params = {
            "symbol": symbol,
            "marginType": margin_type.upper()  # ISOLATED veya CROSS
        }
        
        try:
            response = await self._request("POST", "/fapi/v1/marginType", params=params, signed=True)
            self.logger.info(f"🔧 {symbol} margin type {margin_type} olarak ayarlandı")
            return response
        except httpx.HTTPStatusError as e:
            # Margin type zaten ayarlıysa hata atmaz
            if "No need to change margin type" in e.response.text:
                self.logger.debug(f"Margin type zaten {margin_type}")
                return {"msg": "Already set"}
            raise
    
    async def get_symbol_precision(self, symbol: str) -> tuple[int, int]:
        """Symbol precision bilgisini getir (quantity, price)"""
        try:
            response = await self._request("GET", "/fapi/v1/exchangeInfo")
            
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
            response = await self._request("POST", "/fapi/v1/order", params=params, signed=True)
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
        side: str,  # SELL (LONG için) veya BUY (SHORT için)
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
            response = await self._request("POST", "/fapi/v1/order", params=params, signed=True)
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
            response = await self._request("POST", "/fapi/v1/order", params=params, signed=True)
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
            response = await self._request("DELETE", "/fapi/v1/order", params=params, signed=True)
            self.logger.info(f"❌ Order iptal edildi: {symbol} #{order_id}")
            return response
        except Exception as e:
            self.logger.error(f"Order iptal hatası: {e}")
            raise
    
    async def get_open_orders(self, symbol: str) -> List[Dict[str, Any]]:
        """Açık orderları getir"""
        params = {"symbol": symbol}
        
        try:
            response = await self._request("GET", "/fapi/v1/openOrders", params=params, signed=True)
            return response
        except Exception as e:
            self.logger.error(f"Açık order sorgusu hatası: {e}")
            return []
    
    async def get_position_risk(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Pozisyon bilgilerini getir"""
        params = {"symbol": symbol}
        
        try:
            response = await self._request("GET", "/fapi/v2/positionRisk", params=params, signed=True)
            
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
            response = await self._request("GET", "/fapi/v1/ticker/price", params=params)
            price = float(response["price"])
            return price
        except Exception as e:
            self.logger.error(f"Fiyat sorgusu hatası: {e}")
            return None
    
    async def close(self):
        """HTTP client'ı kapat"""
        await self.client.aclose()

