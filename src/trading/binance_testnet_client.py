"""
Binance Futures Testnet Client - Production Ready
Based on official Binance documentation (2024-2025)
"""

import hmac
import hashlib
import time
import json
import asyncio
import aiohttp
import websockets
from typing import Optional, Dict, Any, List, Callable
from datetime import datetime, timedelta
import logging
from enum import Enum
import ssl
import certifi

from src.core.config import settings
from src.core.logger import app_logger


class OrderType(Enum):
    """Binance order types"""
    LIMIT = "LIMIT"
    MARKET = "MARKET"
    STOP_MARKET = "STOP_MARKET"
    TAKE_PROFIT_MARKET = "TAKE_PROFIT_MARKET"
    TRAILING_STOP_MARKET = "TRAILING_STOP_MARKET"


class OrderSide(Enum):
    """Order sides"""
    BUY = "BUY"
    SELL = "SELL"


class PositionSide(Enum):
    """Position sides for hedge mode"""
    BOTH = "BOTH"  # One-way mode
    LONG = "LONG"  # Hedge mode
    SHORT = "SHORT"  # Hedge mode


class MarginType(Enum):
    """Margin types"""
    ISOLATED = "ISOLATED"
    CROSSED = "CROSSED"


class BinanceFuturesTestnetClient:
    """
    Enhanced Binance Futures Testnet client with:
    - WebSocket streaming
    - Listen Key management
    - Position management
    - Rate limit protection
    - Error recovery
    """

    def __init__(self):
        # API Configuration
        self.api_key = settings.binance_api_key
        self.secret_key = settings.binance_api_secret
        self.base_url = "https://testnet.binancefuture.com"
        self.ws_url = "wss://fstream.binancefuture.com"

        # WebSocket Management
        self.listen_key = None
        self.listen_key_created_at = None
        self.websocket = None
        self.ws_running = False
        self.ws_callbacks = {}

        # HTTP Session
        self.session = None

        # Rate Limiting
        self.request_weight = 0
        self.request_weight_reset_time = time.time() + 60
        self.order_count = 0
        self.order_count_reset_time = time.time() + 60

        # Logging
        self.logger = app_logger

    def _sign_request(self, params: Dict[str, Any]) -> str:
        """Generate HMAC SHA256 signature"""
        query_string = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
        signature = hmac.new(
            self.secret_key.encode(),
            query_string.encode(),
            hashlib.sha256
        ).hexdigest()
        return signature

    async def _check_rate_limit(self, weight: int = 1):
        """Check and update rate limits"""
        current_time = time.time()

        # Reset counters if time window passed
        if current_time > self.request_weight_reset_time:
            self.request_weight = 0
            self.request_weight_reset_time = current_time + 60

        # Check if we're approaching limits
        if self.request_weight + weight > 2400:  # 2400 per minute limit
            wait_time = self.request_weight_reset_time - current_time
            self.logger.warning(f"Rate limit approaching, waiting {wait_time:.1f}s")
            await asyncio.sleep(wait_time)
            self.request_weight = 0
            self.request_weight_reset_time = time.time() + 60

        self.request_weight += weight

    async def _make_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        signed: bool = False,
        weight: int = 1
    ) -> Dict[str, Any]:
        """Make HTTP request to Binance API with retry logic"""

        # Rate limit check
        await self._check_rate_limit(weight)

        # Initialize session if needed
        if not self.session:
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

            connector = aiohttp.TCPConnector(ssl=ssl_context)
            self.session = aiohttp.ClientSession(connector=connector)

        if params is None:
            params = {}

        # Add timestamp for signed requests
        if signed:
            params["timestamp"] = int(time.time() * 1000)
            params["signature"] = self._sign_request(params)

        headers = {"X-MBX-APIKEY": self.api_key} if signed else {}

        url = f"{self.base_url}{endpoint}"

        # Retry logic
        max_retries = 3
        retry_delay = 1

        for attempt in range(max_retries):
            try:
                if method == "GET":
                    async with self.session.get(url, params=params, headers=headers) as resp:
                        response_data = await resp.json()
                elif method == "POST":
                    async with self.session.post(url, params=params, headers=headers) as resp:
                        response_data = await resp.json()
                elif method == "PUT":
                    async with self.session.put(url, params=params, headers=headers) as resp:
                        response_data = await resp.json()
                elif method == "DELETE":
                    async with self.session.delete(url, params=params, headers=headers) as resp:
                        response_data = await resp.json()
                else:
                    raise ValueError(f"Invalid method: {method}")

                # Check for API errors
                if "code" in response_data and response_data["code"] != 200:
                    error_code = response_data["code"]
                    error_msg = response_data.get("msg", "Unknown error")

                    # Handle specific errors
                    if error_code == -1021:  # Timestamp error
                        self.logger.warning("Timestamp sync issue, retrying...")
                        await asyncio.sleep(retry_delay)
                        continue
                    elif error_code == -1003:  # Rate limit
                        self.logger.warning("Rate limited, waiting 60s...")
                        await asyncio.sleep(60)
                        continue
                    else:
                        raise Exception(f"API Error {error_code}: {error_msg}")

                return response_data

            except Exception as e:
                self.logger.error(f"Request failed (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay * (attempt + 1))
                else:
                    raise

    # === Account & Balance Methods ===

    async def get_account_info(self) -> Dict[str, Any]:
        """Get account information including balances"""
        return await self._make_request(
            "GET",
            "/fapi/v2/account",
            signed=True,
            weight=5
        )

    async def get_balance(self, asset: str = "USDT") -> float:
        """Get balance for specific asset"""
        account = await self.get_account_info()
        for balance in account.get("assets", []):
            if balance["asset"] == asset:
                return float(balance["availableBalance"])
        return 0.0

    # === Symbol & Market Info ===

    async def get_exchange_info(self) -> Dict[str, Any]:
        """Get exchange information"""
        return await self._make_request("GET", "/fapi/v1/exchangeInfo")

    async def get_symbol_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get specific symbol information"""
        info = await self.get_exchange_info()
        for sym in info.get("symbols", []):
            if sym["symbol"] == symbol:
                return sym
        return None

    async def get_ticker_price(self, symbol: str) -> float:
        """Get current ticker price"""
        response = await self._make_request(
            "GET",
            "/fapi/v1/ticker/price",
            params={"symbol": symbol}
        )
        return float(response["price"])

    async def get_order_book(self, symbol: str, limit: int = 20) -> Dict[str, Any]:
        """Get order book depth"""
        return await self._make_request(
            "GET",
            "/fapi/v1/depth",
            params={"symbol": symbol, "limit": limit},
            weight=2 if limit <= 100 else 5
        )

    # === Position Management ===

    async def set_margin_type(self, symbol: str, margin_type: MarginType) -> Dict[str, Any]:
        """Set margin type (MUST be called before opening position)"""
        try:
            return await self._make_request(
                "POST",
                "/fapi/v1/marginType",
                params={
                    "symbol": symbol,
                    "marginType": margin_type.value
                },
                signed=True
            )
        except Exception as e:
            if "No need to change margin type" in str(e):
                self.logger.info(f"Margin type already set to {margin_type.value}")
                return {"msg": "Already set"}
            raise

    async def set_leverage(self, symbol: str, leverage: int) -> Dict[str, Any]:
        """Set leverage (MUST be called before opening position)"""
        return await self._make_request(
            "POST",
            "/fapi/v1/leverage",
            params={
                "symbol": symbol,
                "leverage": leverage
            },
            signed=True
        )

    async def get_position_risk(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get position information"""
        params = {}
        if symbol:
            params["symbol"] = symbol

        positions = await self._make_request(
            "GET",
            "/fapi/v2/positionRisk",
            params=params,
            signed=True,
            weight=5
        )

        # Filter out positions with 0 quantity
        return [pos for pos in positions if float(pos["positionAmt"]) != 0]

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all open orders"""
        params = {}
        if symbol:
            params["symbol"] = symbol

        return await self._make_request(
            "GET",
            "/fapi/v1/openOrders",
            params=params,
            signed=True,
            weight=1 if symbol else 40
        )

    # === Order Placement ===

    async def place_market_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        position_side: PositionSide = PositionSide.BOTH,
        reduce_only: bool = False
    ) -> Dict[str, Any]:
        """Place a market order"""
        params = {
            "symbol": symbol,
            "side": side.value,
            "type": OrderType.MARKET.value,
            "quantity": quantity,
            "positionSide": position_side.value
        }

        if reduce_only:
            params["reduceOnly"] = "true"

        return await self._make_request(
            "POST",
            "/fapi/v1/order",
            params=params,
            signed=True
        )

    async def place_limit_order(
        self,
        symbol: str,
        side: OrderSide,
        price: float,
        quantity: float,
        position_side: PositionSide = PositionSide.BOTH,
        time_in_force: str = "GTC"
    ) -> Dict[str, Any]:
        """Place a limit order"""
        params = {
            "symbol": symbol,
            "side": side.value,
            "type": OrderType.LIMIT.value,
            "price": price,
            "quantity": quantity,
            "timeInForce": time_in_force,
            "positionSide": position_side.value
        }

        return await self._make_request(
            "POST",
            "/fapi/v1/order",
            params=params,
            signed=True
        )

    async def place_stop_market_order(
        self,
        symbol: str,
        side: OrderSide,
        stop_price: float,
        quantity: float,
        position_side: PositionSide = PositionSide.BOTH,
        close_position: bool = False
    ) -> Dict[str, Any]:
        """Place a stop market order (stop loss)"""
        params = {
            "symbol": symbol,
            "side": side.value,
            "type": OrderType.STOP_MARKET.value,
            "stopPrice": stop_price,
            "positionSide": position_side.value
        }

        if close_position:
            params["closePosition"] = "true"
        else:
            params["quantity"] = quantity

        return await self._make_request(
            "POST",
            "/fapi/v1/order",
            params=params,
            signed=True
        )

    async def place_take_profit_order(
        self,
        symbol: str,
        side: OrderSide,
        stop_price: float,
        quantity: float,
        position_side: PositionSide = PositionSide.BOTH
    ) -> Dict[str, Any]:
        """Place a take profit order"""
        params = {
            "symbol": symbol,
            "side": side.value,
            "type": OrderType.TAKE_PROFIT_MARKET.value,
            "stopPrice": stop_price,
            "quantity": quantity,
            "positionSide": position_side.value
        }

        return await self._make_request(
            "POST",
            "/fapi/v1/order",
            params=params,
            signed=True
        )

    async def cancel_order(self, symbol: str, order_id: int) -> Dict[str, Any]:
        """Cancel an order"""
        return await self._make_request(
            "DELETE",
            "/fapi/v1/order",
            params={
                "symbol": symbol,
                "orderId": order_id
            },
            signed=True
        )

    async def cancel_all_orders(self, symbol: str) -> Dict[str, Any]:
        """Cancel all open orders for a symbol"""
        return await self._make_request(
            "DELETE",
            "/fapi/v1/allOpenOrders",
            params={"symbol": symbol},
            signed=True
        )

    # === WebSocket Methods ===

    async def create_listen_key(self) -> str:
        """Create a listen key for user data stream"""
        response = await self._make_request(
            "POST",
            "/fapi/v1/listenKey",
            signed=False
        )
        self.listen_key = response["listenKey"]
        self.listen_key_created_at = datetime.now()
        self.logger.info(f"Listen key created: {self.listen_key[:10]}...")
        return self.listen_key

    async def keepalive_listen_key(self) -> None:
        """Keep listen key alive (must be called every 30 minutes)"""
        if not self.listen_key:
            await self.create_listen_key()
            return

        await self._make_request(
            "PUT",
            "/fapi/v1/listenKey",
            signed=False
        )
        self.logger.debug("Listen key keepalive sent")

    async def delete_listen_key(self) -> None:
        """Delete listen key"""
        if self.listen_key:
            await self._make_request(
                "DELETE",
                "/fapi/v1/listenKey",
                signed=False
            )
            self.listen_key = None
            self.logger.info("Listen key deleted")

    async def start_websocket(self, callback: Callable = None):
        """Start WebSocket connection for user data stream"""
        if not self.listen_key:
            await self.create_listen_key()

        self.ws_running = True

        # Keepalive task
        async def keepalive():
            while self.ws_running:
                await asyncio.sleep(1800)  # 30 minutes
                await self.keepalive_listen_key()

        keepalive_task = asyncio.create_task(keepalive())

        # WebSocket connection
        ws_url = f"{self.ws_url}/ws/{self.listen_key}"

        try:
            async with websockets.connect(ws_url) as websocket:
                self.websocket = websocket
                self.logger.info("WebSocket connected")

                while self.ws_running:
                    try:
                        message = await asyncio.wait_for(
                            websocket.recv(),
                            timeout=30
                        )

                        data = json.loads(message)

                        # Process different event types
                        event_type = data.get("e")

                        if event_type == "ACCOUNT_UPDATE":
                            await self._handle_account_update(data)
                        elif event_type == "ORDER_TRADE_UPDATE":
                            await self._handle_order_update(data)
                        elif event_type == "MARGIN_CALL":
                            await self._handle_margin_call(data)

                        # Call custom callback if provided
                        if callback:
                            await callback(data)

                    except asyncio.TimeoutError:
                        # Send ping to keep connection alive
                        await websocket.ping()

        except Exception as e:
            self.logger.error(f"WebSocket error: {e}")
        finally:
            self.ws_running = False
            keepalive_task.cancel()
            self.logger.info("WebSocket disconnected")

    async def _handle_account_update(self, data: Dict[str, Any]):
        """Handle account update events"""
        self.logger.info(f"Account update: {data.get('a', {}).get('B', [])[0] if data.get('a', {}).get('B') else 'N/A'}")

    async def _handle_order_update(self, data: Dict[str, Any]):
        """Handle order update events"""
        order = data.get("o", {})
        self.logger.info(
            f"Order update: {order.get('s')} {order.get('S')} "
            f"{order.get('q')} @ {order.get('p')} - Status: {order.get('X')}"
        )

    async def _handle_margin_call(self, data: Dict[str, Any]):
        """Handle margin call events"""
        self.logger.critical(f"MARGIN CALL: {data}")

    async def subscribe_ticker(self, symbol: str, callback: Callable):
        """Subscribe to ticker updates"""
        stream_name = f"{symbol.lower()}@ticker"
        ws_url = f"{self.ws_url}/ws/{stream_name}"

        async with websockets.connect(ws_url) as websocket:
            while True:
                message = await websocket.recv()
                data = json.loads(message)
                await callback(data)

    async def subscribe_orderbook(self, symbol: str, callback: Callable):
        """Subscribe to orderbook updates"""
        stream_name = f"{symbol.lower()}@depth20@100ms"
        ws_url = f"{self.ws_url}/ws/{stream_name}"

        async with websockets.connect(ws_url) as websocket:
            while True:
                message = await websocket.recv()
                data = json.loads(message)
                await callback(data)

    # === Utility Methods ===

    async def test_connection(self) -> bool:
        """Test API connection"""
        try:
            response = await self._make_request("GET", "/fapi/v1/ping")

            # Test server time sync
            server_time = await self._make_request("GET", "/fapi/v1/time")
            local_time = int(time.time() * 1000)
            time_diff = abs(server_time["serverTime"] - local_time)

            if time_diff > 5000:
                self.logger.warning(f"Time sync issue: {time_diff}ms difference")
            else:
                self.logger.info(f"Connection OK, time sync: {time_diff}ms")

            return True
        except Exception as e:
            self.logger.error(f"Connection test failed: {e}")
            return False

    async def close(self):
        """Clean up connections"""
        if self.ws_running:
            self.ws_running = False

        if self.websocket:
            await self.websocket.close()

        if self.listen_key:
            await self.delete_listen_key()

        if self.session:
            await self.session.close()

        self.logger.info("Client closed")

    # === Advanced Position Management ===

    async def open_position_workflow(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        leverage: int = 1,
        margin_type: MarginType = MarginType.ISOLATED,
        stop_loss_price: Optional[float] = None,
        take_profit_prices: Optional[List[float]] = None
    ) -> Dict[str, Any]:
        """
        Complete workflow for opening a position
        1. Set margin type
        2. Set leverage
        3. Place market order
        4. Set stop loss
        5. Set take profits
        """
        result = {
            "success": False,
            "position": None,
            "orders": []
        }

        try:
            # Step 1: Set margin type
            self.logger.info(f"Setting margin type to {margin_type.value}")
            await self.set_margin_type(symbol, margin_type)

            # Step 2: Set leverage
            self.logger.info(f"Setting leverage to {leverage}x")
            await self.set_leverage(symbol, leverage)

            # Step 3: Place market order
            self.logger.info(f"Opening {side.value} position: {quantity} {symbol}")
            position_order = await self.place_market_order(
                symbol=symbol,
                side=side,
                quantity=quantity
            )
            result["position"] = position_order

            # Step 4: Set stop loss if provided
            if stop_loss_price:
                sl_side = OrderSide.SELL if side == OrderSide.BUY else OrderSide.BUY
                self.logger.info(f"Setting stop loss at {stop_loss_price}")
                sl_order = await self.place_stop_market_order(
                    symbol=symbol,
                    side=sl_side,
                    stop_price=stop_loss_price,
                    close_position=True
                )
                result["orders"].append(sl_order)

            # Step 5: Set take profits if provided
            if take_profit_prices:
                tp_side = OrderSide.SELL if side == OrderSide.BUY else OrderSide.BUY

                # Calculate quantities for each TP
                tp_quantity = quantity / len(take_profit_prices)

                for i, tp_price in enumerate(take_profit_prices):
                    self.logger.info(f"Setting TP{i+1} at {tp_price}")
                    tp_order = await self.place_take_profit_order(
                        symbol=symbol,
                        side=tp_side,
                        stop_price=tp_price,
                        quantity=tp_quantity
                    )
                    result["orders"].append(tp_order)

            result["success"] = True
            self.logger.info(f"Position opened successfully with {len(result['orders'])} orders")

        except Exception as e:
            self.logger.error(f"Failed to open position: {e}")
            result["error"] = str(e)

        return result

    async def close_position(self, symbol: str) -> Dict[str, Any]:
        """Close an existing position"""
        positions = await self.get_position_risk(symbol)

        if not positions:
            return {"success": False, "error": "No position found"}

        position = positions[0]
        position_amt = float(position["positionAmt"])

        if position_amt == 0:
            return {"success": False, "error": "No active position"}

        # Determine side to close
        side = OrderSide.SELL if position_amt > 0 else OrderSide.BUY
        quantity = abs(position_amt)

        # Cancel all open orders first
        await self.cancel_all_orders(symbol)

        # Place market order to close
        order = await self.place_market_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            reduce_only=True
        )

        return {"success": True, "order": order}