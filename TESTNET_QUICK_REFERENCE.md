# Binance Futures Testnet - Quick Reference Card

**Print-friendly reference for common tasks**

---

## URLs

| Service | URL |
|---------|-----|
| **Testnet Platform** | https://testnet.binancefuture.com |
| **REST API** | https://testnet.binancefuture.com |
| **WebSocket** | wss://fstream.binancefuture.com |
| **API Key Creation** | Via testnet account interface |

---

## Setup Commands

### 1. Get Exchange Info
```bash
curl "https://testnet.binancefuture.com/fapi/v1/exchangeInfo?symbol=BTCUSDT"
```

### 2. Set Margin Type
```bash
curl -X POST "https://testnet.binancefuture.com/fapi/v1/marginType" \
  -H "X-MBX-APIKEY: YOUR_KEY" \
  -d "symbol=BTCUSDT&marginType=ISOLATED&timestamp=TIMESTAMP&signature=SIGNATURE"
```

### 3. Set Leverage
```bash
curl -X POST "https://testnet.binancefuture.com/fapi/v1/leverage" \
  -H "X-MBX-APIKEY: YOUR_KEY" \
  -d "symbol=BTCUSDT&leverage=5&timestamp=TIMESTAMP&signature=SIGNATURE"
```

### 4. Place Order
```bash
curl -X POST "https://testnet.binancefuture.com/fapi/v1/order" \
  -H "X-MBX-APIKEY: YOUR_KEY" \
  -d "symbol=BTCUSDT&side=BUY&type=LIMIT&quantity=0.1&price=50000&recvWindow=5000&timestamp=TIMESTAMP&signature=SIGNATURE"
```

---

## Common Trading Pairs

```
Major Pairs:
- BTCUSDT (Bitcoin)
- ETHUSDT (Ethereum)
- BNBUSDT (Binance Coin)
- ADAUSDT (Cardano)
- SOLAUSDT (Solana)
- XRPUSDT (Ripple)

All available pairs via:
GET /fapi/v1/exchangeInfo
```

---

## Python Quick Start

### Install Dependencies
```bash
pip install binance-sdk-derivatives-trading-usds-futures aiohttp websockets
```

### Create Client
```python
from src.trading.binance_client import BinanceFuturesTestnetClient

client = BinanceFuturesTestnetClient(
    api_key="YOUR_API_KEY",
    secret_key="YOUR_SECRET_KEY"
)
```

### Setup Position
```python
import asyncio

async def main():
    # Set margin type and leverage
    await client.setup_position(
        symbol="BTCUSDT",
        leverage=5,
        margin_type="ISOLATED"
    )

    # Place order
    order = await client.place_market_order(
        symbol="BTCUSDT",
        side="BUY",
        quantity=0.1
    )

    print(f"Order placed: {order['orderId']}")

asyncio.run(main())
```

---

## WebSocket User Stream

### Create Listen Key
```bash
curl -X POST "https://testnet.binancefuture.com/fapi/v1/listenKey" \
  -H "X-MBX-APIKEY: YOUR_KEY"

# Response: {"listenKey":"xs8NvZvAHNYrqn..."}
```

### Connect WebSocket
```bash
wscat -c "wss://fstream.binancefuture.com/ws/{listenKey}"
```

### Keep Alive (Every 30 Min)
```bash
curl -X PUT "https://testnet.binancefuture.com/fapi/v1/listenKey" \
  -H "X-MBX-APIKEY: YOUR_KEY"
```

---

## Error Codes

| Code | Issue | Solution |
|------|-------|----------|
| -1121 | Invalid symbol | Check symbol name (BTCUSDT format) |
| -1022 | Bad signature | Verify API key and secret |
| -2015 | Invalid API key | Check API key and IP whitelist |
| -4049 | Insufficient balance | Add margin or reduce size |
| 429 | Rate limited | Implement backoff strategy |
| 418 | IP banned | Wait 2 min - 3 days |

---

## Rate Limits

| Type | Limit |
|------|-------|
| **Request Weight** | 2,400/min |
| **Orders/Minute** | 1,200/min |
| **Orders/10 Seconds** | 300/10s |

**Check Response Headers:**
```
X-MBX-USED-WEIGHT-1m: 1200
X-MBX-ORDER-COUNT-1m: 450
```

---

## Signature Generation

### Linux/Mac
```bash
echo -n "symbol=BTCUSDT&side=BUY&type=LIMIT&quantity=1&price=9000&timeInForce=GTC&recvWindow=5000&timestamp=1591702613943" \
  | openssl dgst -sha256 -hmac "YOUR_SECRET_KEY"
```

### Python
```python
import hmac
import hashlib

message = "symbol=BTCUSDT&side=BUY&type=LIMIT&quantity=1&price=9000&timeInForce=GTC&recvWindow=5000&timestamp=1591702613943"
signature = hmac.new(
    b"YOUR_SECRET_KEY",
    message.encode(),
    hashlib.sha256
).hexdigest()
print(signature)
```

---

## Position Info Format

```json
{
  "symbol": "BTCUSDT",
  "positionAmt": "1.3",
  "entryPrice": "6563.665",
  "markPrice": "6589.8",
  "unRealizedProfit": "2359.51",
  "marginType": "cross",
  "leverage": "5",
  "marginRatio": "0.0456"
}
```

---

## Order Types

```
LIMIT
- Requires: price, timeInForce (GTC, IOC, FOK, GTX)

MARKET
- No price needed
- Executes immediately

STOP_LOSS / TAKE_PROFIT
- Requires: stopPrice

TRAILING_STOP_MARKET
- Requires: callbackRate

POST_ONLY / REDUCE_ONLY
- Modifiers for orders
```

---

## Margin Types

```
ISOLATED
- Separate margin for LONG and SHORT
- Risk limited to margin per side
- Recommended for conservative trading

CROSS
- Shared margin across all positions
- More efficient margin usage
- Higher risk if not managed
```

---

## Position Sides

```
BOTH
- One-way mode
- Single LONG or SHORT position
- Default mode

LONG
- Hedge mode
- Can have LONG and SHORT simultaneously

SHORT
- Hedge mode
- Can have LONG and SHORT simultaneously
```

---

## Common Errors

### "No need to change margin type"
- Margin type already set to that value
- Safe to ignore, position is ready

### "Request occur unknown error"
- Temporary server issue
- Retry with exponential backoff

### "Service Unavailable"
- Server maintenance or overload
- Retry with exponential backoff

### "Reduce only Order is rejected"
- Trying to add to position with reduce-only flag
- Use normal order without reduce-only flag

---

## WebSocket Event Types

```
ORDER_TRADE_UPDATE
- Sent when order status changes
- Contains: symbol, side, status, quantity, price

ACCOUNT_UPDATE
- Sent when position or balance changes
- Contains: positions, balances

balanceUpdate
- Asset balance changed
- Contains: asset, delta

outboundAccountPosition
- Full account position update
- Contains: all balances and positions
```

---

## Testing Checklist

- [ ] API keys generated
- [ ] Can query exchange info
- [ ] Can set margin type
- [ ] Can set leverage
- [ ] Can place test order
- [ ] Can cancel order
- [ ] Can query positions
- [ ] WebSocket listen key created
- [ ] Receiving user data events
- [ ] Rate limits monitored
- [ ] Error handling working
- [ ] 24-hour stability verified

---

## Useful Endpoints

```
Market Data:
GET /fapi/v1/ping                    # Check connectivity
GET /fapi/v1/time                    # Get server time
GET /fapi/v1/exchangeInfo            # Get trading rules
GET /fapi/v1/depth?symbol=BTCUSDT    # Get order book
GET /fapi/v1/ticker/price            # Get latest price

Account:
GET /fapi/v2/account                 # Account info
GET /fapi/v2/positionRisk            # Position info
GET /fapi/v1/openOrders              # Open orders
GET /fapi/v1/allOrders               # Order history

Trading:
POST /fapi/v1/order                  # Place order
POST /fapi/v1/cancel                 # Cancel order
POST /fapi/v1/batchOrders            # Batch orders

Position Management:
POST /fapi/v1/marginType             # Set margin
POST /fapi/v1/leverage               # Set leverage
GET /fapi/v2/positionRisk            # Position risk

User Stream:
POST /fapi/v1/listenKey              # Create key
PUT /fapi/v1/listenKey               # Keep alive
DELETE /fapi/v1/listenKey            # Close stream
```

---

## Environment Variables Template

```bash
# .env file
BINANCE_TESTNET_API_KEY=your_api_key_here
BINANCE_TESTNET_SECRET_KEY=your_secret_key_here
DEFAULT_SYMBOL=BTCUSDT
DEFAULT_LEVERAGE=1
DEFAULT_MARGIN_TYPE=ISOLATED
LOG_LEVEL=INFO
```

---

## Python Async Example

```python
import asyncio
from src.trading.binance_client import BinanceFuturesTestnetClient

async def main():
    client = BinanceFuturesTestnetClient("KEY", "SECRET")

    try:
        # Setup
        await client.setup_position("BTCUSDT", leverage=5)

        # Place order
        order = await client.place_market_order("BTCUSDT", "BUY", 0.1)
        print(f"Order: {order['orderId']}")

        # Get position
        position = await client.get_position_info("BTCUSDT")
        print(f"Position: {position}")

        # Close position
        close_order = await client.place_market_order("BTCUSDT", "SELL", 0.1)
        print(f"Closed: {close_order['orderId']}")

    finally:
        await client.close()

asyncio.run(main())
```

---

## Monitoring Commands

### Check Current Balance
```bash
curl -H "X-MBX-APIKEY: YOUR_KEY" \
  "https://testnet.binancefuture.com/fapi/v2/account?timestamp=TIMESTAMP&signature=SIGNATURE"
```

### Check Position
```bash
curl -H "X-MBX-APIKEY: YOUR_KEY" \
  "https://testnet.binancefuture.com/fapi/v2/positionRisk?symbol=BTCUSDT&timestamp=TIMESTAMP&signature=SIGNATURE"
```

### Check Open Orders
```bash
curl -H "X-MBX-APIKEY: YOUR_KEY" \
  "https://testnet.binancefuture.com/fapi/v1/openOrders?symbol=BTCUSDT&timestamp=TIMESTAMP&signature=SIGNATURE"
```

---

## Notes

- All timestamps in **milliseconds**
- All prices in **quote asset** (USDT)
- All quantities in **base asset** (BTC, ETH, etc)
- Testnet data **not real** - practice only
- Testnet **periodic resets** - don't rely on persistence
- Always use **small quantities** when testing
- Monitor **WebSocket connection** for real-time updates
- Keep **listen key alive** every 30 minutes

---

## Support Resources

- **Official Docs:** https://developers.binance.com/
- **Community:** https://dev.binance.vision/
- **GitHub Issues:** Search "binance-api"
- **Python SDK:** https://github.com/binance/binance-connector-python

