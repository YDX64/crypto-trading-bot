# Binance Futures Testnet Implementation Guide

**Focus:** Practical setup and integration for the TRADINGBOT project

---

## Quick Setup (5 Minutes)

### 1. Create Testnet Account

```
Navigate to: https://testnet.binancefuture.com/en/futures/BTCUSDT
Click: "Create" if needed
```

### 2. Generate API Keys

```
1. Log in to testnet account
2. Scroll to "API Key" section
3. Click "Generate HMAC_SHA256 Key"
4. Save both API Key and Secret Key (shown only once!)
5. Store in .env file:

BINANCE_TESTNET_API_KEY=your_api_key_here
BINANCE_TESTNET_SECRET_KEY=your_secret_key_here
```

### 3. Verify Connection

```bash
# Test with curl
curl "https://testnet.binancefuture.com/fapi/v1/exchangeInfo?symbol=BTCUSDT"

# Should return JSON with BTCUSDT pair info
```

---

## Configuration for TRADINGBOT

### Update `src/core/config.py`

```python
import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Binance Testnet Configuration
    BINANCE_TESTNET_BASE_URL = "https://testnet.binancefuture.com"
    BINANCE_TESTNET_WS_URL = "wss://fstream.binancefuture.com"

    # API Keys
    BINANCE_API_KEY = os.getenv("BINANCE_TESTNET_API_KEY")
    BINANCE_SECRET_KEY = os.getenv("BINANCE_TESTNET_SECRET_KEY")

    # Trading Configuration
    DEFAULT_LEVERAGE = 1  # Start conservative in testnet
    DEFAULT_MARGIN_TYPE = "ISOLATED"  # More predictable

    # Position Management
    MAX_POSITION_SIZE = 0.5  # Max units per symbol
    MAX_POSITIONS = 3  # Max simultaneous positions

    # WebSocket
    WEBSOCKET_TIMEOUT = 30  # seconds
    LISTEN_KEY_REFRESH_INTERVAL = 1800  # 30 minutes

    # Rate Limiting
    MAX_REQUESTS_PER_MINUTE = 2400
    MAX_ORDERS_PER_MINUTE = 1200
    MAX_ORDERS_PER_10S = 300
```

### Update `.env` File

```env
# Binance Testnet Credentials
BINANCE_TESTNET_API_KEY=your_api_key
BINANCE_TESTNET_SECRET_KEY=your_secret_key

# Trading Settings
DEFAULT_SYMBOL=BTCUSDT
DEFAULT_TIMEFRAME=1h

# Logging
LOG_LEVEL=INFO
LOG_FILE=logs/trading_bot.log

# WebSocket
WEBSOCKET_DEBUG=false
```

---

## Enhanced Binance Client for Testnet

### Update `src/trading/binance_client.py`

```python
import hmac
import hashlib
import time
import json
import asyncio
import aiohttp
import websockets
from typing import Optional, Dict, Any
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class BinanceFuturesTestnetClient:
    """Enhanced Binance Futures Testnet client with position management"""

    def __init__(self, api_key: str, secret_key: str):
        self.api_key = api_key
        self.secret_key = secret_key
        self.base_url = "https://testnet.binancefuture.com"
        self.ws_url = "wss://fstream.binancefuture.com"
        self.listen_key = None
        self.listen_key_created_at = None
        self.websocket = None
        self.session = None

    def _sign_request(self, params: Dict[str, Any]) -> str:
        """Generate HMAC SHA256 signature"""
        query_string = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
        signature = hmac.new(
            self.secret_key.encode(),
            query_string.encode(),
            hashlib.sha256
        ).hexdigest()
        return signature

    async def _make_request(
        self,
        method: str,
        endpoint: str,
        params: Dict[str, Any] = None,
        signed: bool = False
    ) -> Dict[str, Any]:
        """Make HTTP request to Binance API"""
        if params is None:
            params = {}

        if signed:
            params['timestamp'] = int(time.time() * 1000)
            params['recvWindow'] = 5000
            params['signature'] = self._sign_request(params)

        headers = {'X-MBX-APIKEY': self.api_key} if signed else {}

        if not self.session:
            self.session = aiohttp.ClientSession()

        try:
            async with self.session.request(
                method,
                f"{self.base_url}{endpoint}",
                params=params,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                data = await response.json()

                # Log rate limit info
                if 'X-MBX-USED-WEIGHT-1m' in response.headers:
                    weight = response.headers['X-MBX-USED-WEIGHT-1m']
                    logger.debug(f"Current weight usage: {weight}/2400")

                if response.status != 200:
                    raise Exception(f"API Error {response.status}: {data}")

                return data

        except Exception as e:
            logger.error(f"Request failed: {str(e)}")
            raise

    # ==================== Position Management ====================

    async def set_margin_type(self, symbol: str, margin_type: str) -> Dict:
        """Set margin type (ISOLATED or CROSS)"""
        params = {
            'symbol': symbol,
            'marginType': margin_type.upper()
        }
        return await self._make_request('POST', '/fapi/v1/marginType', params, signed=True)

    async def set_leverage(self, symbol: str, leverage: int) -> Dict:
        """Set initial leverage (1-125)"""
        if not 1 <= leverage <= 125:
            raise ValueError("Leverage must be between 1 and 125")

        params = {
            'symbol': symbol,
            'leverage': leverage
        }
        return await self._make_request('POST', '/fapi/v1/leverage', params, signed=True)

    async def get_position_info(self, symbol: str = None) -> Dict:
        """Get position information"""
        params = {}
        if symbol:
            params['symbol'] = symbol

        return await self._make_request('GET', '/fapi/v2/positionRisk', params, signed=True)

    async def get_account_info(self) -> Dict:
        """Get full account information"""
        return await self._make_request('GET', '/fapi/v2/account', signed=True)

    async def setup_position(
        self,
        symbol: str,
        leverage: int = 1,
        margin_type: str = "ISOLATED"
    ) -> Dict:
        """Setup position with margin type and leverage"""
        logger.info(f"Setting up {symbol}: leverage={leverage}, margin={margin_type}")

        try:
            # Set margin type
            margin_result = await self.set_margin_type(symbol, margin_type)
            logger.info(f"Margin type set: {margin_result}")
        except Exception as e:
            # Might already be set
            if "No need to change margin type" not in str(e):
                logger.warning(f"Margin type setup warning: {e}")

        try:
            # Set leverage
            leverage_result = await self.set_leverage(symbol, leverage)
            logger.info(f"Leverage set: {leverage_result}")
        except Exception as e:
            if "No need to change leverage" not in str(e):
                logger.warning(f"Leverage setup warning: {e}")

        return {"symbol": symbol, "leverage": leverage, "marginType": margin_type}

    # ==================== Order Management ====================

    async def place_limit_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        position_side: str = "BOTH"
    ) -> Dict:
        """Place a limit order"""
        params = {
            'symbol': symbol,
            'side': side.upper(),
            'type': 'LIMIT',
            'quantity': quantity,
            'price': price,
            'timeInForce': 'GTC',
            'positionSide': position_side
        }

        logger.info(f"Placing order: {symbol} {side} {quantity} @ {price}")
        result = await self._make_request('POST', '/fapi/v1/order', params, signed=True)

        logger.info(f"Order placed: {result['orderId']}")
        return result

    async def place_market_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        position_side: str = "BOTH"
    ) -> Dict:
        """Place a market order"""
        params = {
            'symbol': symbol,
            'side': side.upper(),
            'type': 'MARKET',
            'quantity': quantity,
            'positionSide': position_side
        }

        logger.info(f"Placing market order: {symbol} {side} {quantity}")
        result = await self._make_request('POST', '/fapi/v1/order', params, signed=True)

        logger.info(f"Market order placed: {result['orderId']}")
        return result

    async def cancel_order(self, symbol: str, order_id: int) -> Dict:
        """Cancel an order"""
        params = {
            'symbol': symbol,
            'orderId': order_id
        }

        return await self._make_request('POST', '/fapi/v1/cancel', params, signed=True)

    async def get_open_orders(self, symbol: str = None) -> list:
        """Get open orders"""
        params = {}
        if symbol:
            params['symbol'] = symbol

        return await self._make_request('GET', '/fapi/v1/openOrders', params, signed=True)

    # ==================== WebSocket User Data Stream ====================

    async def create_listen_key(self) -> str:
        """Create listen key for user data stream"""
        headers = {'X-MBX-APIKEY': self.api_key}

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/fapi/v1/listenKey",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=5)
            ) as response:
                data = await response.json()
                self.listen_key = data['listenKey']
                self.listen_key_created_at = time.time()
                logger.info(f"Listen key created: {self.listen_key[:10]}...")
                return self.listen_key

    async def keep_alive_listen_key(self):
        """Keep listen key alive by sending PUT every 30 minutes"""
        while self.listen_key:
            await asyncio.sleep(1800)  # 30 minutes

            try:
                headers = {'X-MBX-APIKEY': self.api_key}
                async with aiohttp.ClientSession() as session:
                    async with session.put(
                        f"{self.base_url}/fapi/v1/listenKey",
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=5)
                    ) as response:
                        if response.status == 200:
                            logger.debug("Listen key refreshed")
            except Exception as e:
                logger.error(f"Failed to refresh listen key: {e}")

    async def connect_user_stream(self, on_message_callback):
        """Connect to user data stream with callback"""
        if not self.listen_key:
            await self.create_listen_key()

        # Start keep-alive task
        keep_alive_task = asyncio.create_task(self.keep_alive_listen_key())

        try:
            ws_url = f"{self.ws_url}/ws/{self.listen_key}"
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=10) as websocket:
                self.websocket = websocket
                logger.info("Connected to user data stream")

                async for message in websocket:
                    try:
                        data = json.loads(message)
                        await on_message_callback(data)
                    except Exception as e:
                        logger.error(f"Error processing message: {e}")

        except Exception as e:
            logger.error(f"WebSocket error: {e}")
        finally:
            keep_alive_task.cancel()

    async def close(self):
        """Close connections and cleanup"""
        if self.websocket:
            await self.websocket.close()
        if self.session:
            await self.session.close()


class PositionManager:
    """Manage trading positions"""

    def __init__(self, client: BinanceFuturesTestnetClient):
        self.client = client
        self.positions = {}

    async def open_position(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: Optional[float] = None,
        leverage: int = 1
    ) -> Dict:
        """Open a new position"""
        # Setup position parameters
        await self.client.setup_position(symbol, leverage, "ISOLATED")

        # Place order
        if price:
            order = await self.client.place_limit_order(symbol, side, quantity, price)
        else:
            order = await self.client.place_market_order(symbol, side, quantity)

        # Track position
        self.positions[symbol] = {
            'side': side,
            'quantity': quantity,
            'entry_price': price if price else order.get('avgPrice', 0),
            'order_id': order['orderId'],
            'opened_at': datetime.now()
        }

        return order

    async def close_position(self, symbol: str) -> Dict:
        """Close an existing position"""
        if symbol not in self.positions:
            raise ValueError(f"No position found for {symbol}")

        position = self.positions[symbol]
        close_side = 'SELL' if position['side'] == 'BUY' else 'BUY'

        order = await self.client.place_market_order(symbol, close_side, position['quantity'])

        del self.positions[symbol]
        return order

    async def get_position_pnl(self, symbol: str) -> Dict:
        """Get position P&L"""
        position_info = await self.client.get_position_info(symbol)

        if not position_info:
            return None

        pos = position_info[0]
        return {
            'symbol': symbol,
            'position_amount': float(pos['positionAmt']),
            'entry_price': float(pos['entryPrice']),
            'mark_price': float(pos['markPrice']),
            'unrealized_pnl': float(pos['unRealizedProfit']),
            'margin_type': pos['marginType'],
            'leverage': int(pos['leverage'])
        }
```

---

## Testing Position Management

### Create `tests/test_testnet_positions.py`

```python
import asyncio
import os
import pytest
from dotenv import load_dotenv
from src.trading.binance_client import (
    BinanceFuturesTestnetClient,
    PositionManager
)

load_dotenv()

API_KEY = os.getenv("BINANCE_TESTNET_API_KEY")
SECRET_KEY = os.getenv("BINANCE_TESTNET_SECRET_KEY")


@pytest.fixture
def client():
    """Create test client"""
    return BinanceFuturesTestnetClient(API_KEY, SECRET_KEY)


@pytest.fixture
def position_manager(client):
    """Create position manager"""
    return PositionManager(client)


@pytest.mark.asyncio
async def test_exchange_info(client):
    """Test getting exchange info"""
    info = await client._make_request('GET', '/fapi/v1/exchangeInfo', {'symbol': 'BTCUSDT'})
    assert 'symbols' in info
    assert len(info['symbols']) > 0


@pytest.mark.asyncio
async def test_setup_position(client):
    """Test position setup"""
    result = await client.setup_position('BTCUSDT', leverage=5, margin_type='ISOLATED')
    assert result['symbol'] == 'BTCUSDT'
    assert result['leverage'] == 5


@pytest.mark.asyncio
async def test_get_account_info(client):
    """Test getting account info"""
    info = await client.get_account_info()
    assert 'assets' in info
    assert 'positions' in info


@pytest.mark.asyncio
async def test_listen_key_creation(client):
    """Test listen key creation"""
    listen_key = await client.create_listen_key()
    assert listen_key is not None
    assert len(listen_key) > 0


@pytest.mark.asyncio
async def test_open_position_limit_order(position_manager, client):
    """Test opening position with limit order"""
    # Get current price first
    ticker = await client._make_request('GET', '/fapi/v1/ticker/price', {'symbol': 'BTCUSDT'})
    current_price = float(ticker['price'])

    # Open position at lower price
    limit_price = current_price * 0.99

    order = await position_manager.open_position(
        symbol='BTCUSDT',
        side='BUY',
        quantity=0.001,
        price=limit_price,
        leverage=2
    )

    assert order['symbol'] == 'BTCUSDT'
    assert order['side'] == 'BUY'

    # Cleanup - cancel order if not filled
    try:
        await client.cancel_order('BTCUSDT', order['orderId'])
    except:
        pass


@pytest.mark.asyncio
async def test_market_order(client):
    """Test market order placement"""
    order = await client.place_market_order(
        symbol='BTCUSDT',
        side='BUY',
        quantity=0.001
    )

    assert order['symbol'] == 'BTCUSDT'
    assert order['side'] == 'BUY'
    assert order['type'] == 'MARKET'


@pytest.mark.asyncio
async def test_get_position_pnl(position_manager, client):
    """Test getting position P&L"""
    # First place a market order
    await client.place_market_order('BTCUSDT', 'BUY', 0.001)

    # Get P&L info
    pnl = await position_manager.get_position_pnl('BTCUSDT')

    assert pnl is not None
    assert 'unrealized_pnl' in pnl
    assert 'mark_price' in pnl
```

### Run Tests

```bash
# Install pytest and dependencies
pip install pytest pytest-asyncio python-dotenv aiohttp websockets

# Run specific test
pytest tests/test_testnet_positions.py::test_exchange_info -v

# Run all testnet tests
pytest tests/test_testnet_positions.py -v

# Run with asyncio debug
pytest tests/test_testnet_positions.py -v --asyncio-mode=auto
```

---

## Integration with TRADINGBOT

### Update `src/services/orchestrator.py`

```python
import asyncio
import logging
from typing import Optional
from src.trading.binance_client import BinanceFuturesTestnetClient, PositionManager
from src.core.config import Config

logger = logging.getLogger(__name__)


class TradingOrchestrator:
    """Orchestrate trading operations with position management"""

    def __init__(self):
        self.client = BinanceFuturesTestnetClient(
            Config.BINANCE_API_KEY,
            Config.BINANCE_SECRET_KEY
        )
        self.position_manager = PositionManager(self.client)
        self.active_positions = {}

    async def initialize(self):
        """Initialize trading orchestrator"""
        logger.info("Initializing Trading Orchestrator...")

        try:
            # Test connection
            account_info = await self.client.get_account_info()
            logger.info(f"Connected to Binance Testnet. Account balance: {account_info}")

            # Create listen key for user data stream
            listen_key = await self.client.create_listen_key()
            logger.info("User data stream listen key created")

            return True
        except Exception as e:
            logger.error(f"Initialization failed: {e}")
            return False

    async def execute_trade(self, symbol: str, signal: dict):
        """Execute trade based on signal"""
        logger.info(f"Executing trade for {symbol}: {signal}")

        try:
            side = signal.get('side', 'BUY')
            quantity = signal.get('quantity', 0.1)
            leverage = signal.get('leverage', 1)

            # Setup position
            await self.client.setup_position(symbol, leverage)

            # Place market order
            order = await self.client.place_market_order(symbol, side, quantity)

            self.active_positions[symbol] = {
                'order_id': order['orderId'],
                'side': side,
                'quantity': quantity,
                'entry_price': order.get('avgPrice', 0)
            }

            logger.info(f"Trade executed for {symbol}: Order ID {order['orderId']}")
            return order

        except Exception as e:
            logger.error(f"Trade execution failed: {e}")
            return None

    async def close_trade(self, symbol: str):
        """Close an active trade"""
        if symbol not in self.active_positions:
            logger.warning(f"No active position for {symbol}")
            return None

        try:
            order = await self.position_manager.close_position(symbol)
            logger.info(f"Position closed for {symbol}")
            return order
        except Exception as e:
            logger.error(f"Failed to close position: {e}")
            return None

    async def get_positions(self):
        """Get all active positions"""
        try:
            return await self.client.get_position_info()
        except Exception as e:
            logger.error(f"Failed to get positions: {e}")
            return []

    async def listen_user_stream(self):
        """Listen to user data stream"""
        async def on_message(data):
            event_type = data.get('e')

            if event_type == 'ORDER_TRADE_UPDATE':
                logger.info(f"Order update: {data['o']}")
            elif event_type == 'ACCOUNT_UPDATE':
                logger.info(f"Account update: {data}")

        await self.client.connect_user_stream(on_message)

    async def shutdown(self):
        """Shutdown orchestrator"""
        logger.info("Shutting down Trading Orchestrator...")
        await self.client.close()
```

---

## Best Practices Checklist

### Before Going Live with Real Money

- [ ] Test all position management functions with testnet
- [ ] Verify WebSocket connections and message handling
- [ ] Test error handling (insufficient balance, invalid orders, etc.)
- [ ] Verify rate limit handling and backoff strategy
- [ ] Test position liquidation scenarios
- [ ] Verify P&L calculations
- [ ] Test order cancellation workflow
- [ ] Verify leverage and margin type changes
- [ ] Test position closing procedures
- [ ] Monitor logs for errors over 24+ hours

### Production Checklist

- [ ] Update base URLs to production endpoints
- [ ] Use production API keys (separate from testnet)
- [ ] Implement strict risk limits
- [ ] Set up monitoring and alerts
- [ ] Enable IP whitelist on API keys
- [ ] Use small position sizes initially
- [ ] Implement position size limits
- [ ] Test with paper trading mode first (if available)
- [ ] Set up automatic position closure at daily loss limit
- [ ] Monitor WebSocket connection stability

---

## Troubleshooting

### Issue: "Invalid API-key"
```
Solution:
1. Verify API key is correct and copied fully
2. Check that API key has trading permissions enabled
3. Verify using testnet credentials for testnet
```

### Issue: "Signature for this request is not valid"
```
Solution:
1. Verify secret key is correct
2. Check timestamp is not too far from server time
3. Ensure parameters are sorted correctly
4. Verify HMAC SHA256 implementation
```

### Issue: WebSocket disconnects after 24 hours
```
Solution:
This is expected behavior. Implement:
1. Automatic reconnection with new listen key
2. Maintain position state locally
3. Verify positions on reconnect
```

### Issue: "No need to change margin type"
```
Solution:
Catch this error - it means margin type is already set correctly
No action needed, just log and continue
```

### Issue: Rate limit exceeded (429)
```
Solution:
1. Implement exponential backoff
2. Use WebSocket for real-time data instead of polling
3. Batch API calls where possible
4. Check X-MBX-USED-WEIGHT-1m header in responses
```

---

## Files Modified/Created

```
/Users/max/Downloads/Downloads/TRADINGBOT/
├── src/
│   ├── trading/
│   │   ├── binance_client.py (UPDATED with testnet support)
│   │   └── position_manager.py (NEW - for position management)
│   ├── services/
│   │   └── orchestrator.py (UPDATED with position management)
│   └── core/
│       └── config.py (UPDATED with testnet config)
├── tests/
│   └── test_testnet_positions.py (NEW - position tests)
├── BINANCE_TESTNET_RESEARCH.md (NEW - comprehensive reference)
└── TESTNET_IMPLEMENTATION_GUIDE.md (THIS FILE)
```

---

## Next Steps

1. **Setup Testnet Account** - Follow "Quick Setup" section
2. **Run Tests** - Verify connection and basic functionality
3. **Test Position Management** - Open, close, and monitor positions
4. **Test WebSocket** - Verify user data stream works
5. **Monitor 24 Hours** - Check system stability
6. **Document Results** - Create test report
7. **Prepare for Production** - Update endpoints when ready

