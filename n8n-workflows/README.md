# n8n Workflows for Crypto Trading Bot

This directory contains n8n workflow JSON files that replicate the functionality of the Python-based crypto trading bot.

## Workflows Overview

### 01-telegram-signal-receiver.json
**Purpose:** Receives and filters Telegram signals

**Trigger:** Telegram Bot Webhook
**Outputs:** Filtered signals to main orchestrator

**Key Features:**
- Filters profit notifications
- Validates trading pair presence
- Sends confirmation messages
- Queues signals for processing

---

### 02-signal-parser.json
**Purpose:** Parses raw Telegram messages into structured data

**Trigger:** Webhook (called by orchestrator)
**Outputs:** Parsed signal object

**Extracts:**
- Coin symbol (BTC, ETH, etc.)
- Direction (LONG/SHORT)
- Leverage (1-125x)
- Margin type (CROSS/ISOLATED)
- Entry range
- Take profit targets
- Stop loss

**Validation:**
- LONG: SL < Entry < Targets
- SHORT: Targets < Entry < SL
- Leverage within bounds
- All required fields present

---

### 03-ai-analyzer.json
**Purpose:** Performs 3-perspective AI analysis

**Trigger:** Webhook (called by orchestrator)
**Outputs:** AI verdict and trend alignment

**Analysis Perspectives:**
1. **Technical Analysis**
   - Support/resistance levels
   - Risk/reward ratio
   - Momentum indicators
   - Volume analysis

2. **Risk Analysis**
   - Position sizing
   - Leverage appropriateness
   - Stop loss placement
   - Volatility assessment

3. **Sentiment Analysis**
   - Market sentiment
   - News and events
   - Funding rates
   - Whale activity

**Consensus Logic:**
- Counts BULLISH vs BEARISH votes
- Determines final verdict
- Checks trend alignment
- Calculates confidence level

---

### 04-position-manager.json
**Purpose:** Opens positions on Binance Futures

**Trigger:** Webhook (called by orchestrator)
**Outputs:** Position details

**Steps:**
1. Calculate position size (10% of balance)
2. Set margin type (ISOLATED/CROSS)
3. Set leverage
4. Open market order
5. Place stop loss
6. Place first take profit (25%)
7. Save to database

**Position Calculation:**
```
riskAmount = accountBalance * (riskPercentage / 100)
positionSize = riskAmount
quantity = positionSize / entryPrice
```

---

### 05-trailing-stop-monitor.json
**Purpose:** Monitors and updates trailing stops

**Trigger:** Schedule (every 30 seconds)
**Outputs:** Updated positions

**Monitoring Logic:**
1. Get all active positions
2. Check if still open on Binance
3. If closed → Mark as closed
4. If first TP hit → Move to breakeven
5. If trailing → Update stop loss

**Breakeven Logic:**
- Triggered when 25% of position closes
- Moves SL to entry price
- Activates trailing mode

**Trailing Logic:**
- LONG: SL = currentPrice * (1 - trailingPct)
- SHORT: SL = currentPrice * (1 + trailingPct)
- Only updates if new SL is better

---

### 06-main-orchestrator.json
**Purpose:** Coordinates the entire workflow

**Trigger:** Webhook (called by signal receiver)
**Outputs:** Final result

**Flow:**
1. Check max positions limit
2. Parse signal
3. Perform AI analysis
4. Check trend alignment
5. Open position OR add to waiting queue
6. Send notifications

**Decision Points:**
- Max positions check
- Parse validation
- Trend alignment
- Waiting mode enabled

**Notifications:**
- Position opened
- Signal rejected
- Added to waiting mode
- Parse failed
- Max positions reached

---

## Setup Guide

### Prerequisites

1. **n8n Installation**
   ```bash
   npm install -g n8n
   # or use Docker
   docker run -it --rm --name n8n -p 5678:5678 n8nio/n8n
   ```

2. **Telegram Bot**
   - Create bot via @BotFather
   - Get bot token
   - Get chat ID

3. **Binance Testnet Account**
   - Sign up at testnet.binancefuture.com
   - Generate API keys

4. **AI API Keys**
   - DeepSeek API key
   - Gemini API key (fallback)

### Installation Steps

#### 1. Import Workflows

1. Open n8n at http://localhost:5678
2. Click "Workflows" → "Import from File"
3. Import each JSON file in order (01 through 06)

#### 2. Configure Credentials

**Telegram Bot API:**
1. Go to "Credentials" → "Add Credential"
2. Select "Telegram API"
3. Enter your bot token
4. Save as "Telegram Bot API"

**DeepSeek/OpenAI API:**
1. Add "OpenAI API" credential
2. Enter DeepSeek API key
3. Set custom base URL: https://api.deepseek.com
4. Save as "DeepSeek API"

**SQLite Database:**
1. Add "SQLite" credential
2. Set database file path
3. Save as "Trading Bot DB"

#### 3. Set Environment Variables

Create `.env` file or set in n8n settings:

```bash
# Telegram
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# Binance
BINANCE_API_KEY=your_api_key
BINANCE_API_SECRET=your_api_secret
BINANCE_BASE_URL=https://testnet.binancefuture.com

# AI
DEEPSEEK_API_KEY=your_deepseek_key
DEEPSEEK_MODEL=deepseek-reasoner
GEMINI_API_KEY=your_gemini_key

# Trading
ACCOUNT_BALANCE=10000.0
RISK_PERCENTAGE=10.0
FIRST_TP_PERCENTAGE=25.0
TRAILING_STOP_PERCENTAGE=1.5
TRAILING_PROFIT_PERCENTAGE=0.5
MAX_POSITIONS=5
MARGIN_TYPE=ISOLATED
MAX_LEVERAGE=20

# Waiting Mode
WAITING_MODE_ENABLED=false
WAITING_MODE_MAX_POSITIONS=3
WAITING_MODE_MAX_HOURS=24

# URLs
N8N_BASE_URL=http://localhost:5678
API_BASE_URL=http://localhost:8000
```

#### 4. Initialize Database

Run this SQL to create tables:

```sql
-- Signals table
CREATE TABLE IF NOT EXISTS signals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  raw_message TEXT NOT NULL,
  coin TEXT,
  direction TEXT,
  leverage INTEGER,
  entry_min REAL,
  entry_max REAL,
  entry REAL,
  targets TEXT,
  stoploss REAL,
  status TEXT DEFAULT 'RECEIVED',
  ai_verdict TEXT,
  trend_aligned BOOLEAN DEFAULT 0,
  error_message TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Positions table
CREATE TABLE IF NOT EXISTS positions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  signal_id INTEGER,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL,
  leverage INTEGER NOT NULL,
  margin_type TEXT DEFAULT 'ISOLATED',
  entry_price REAL NOT NULL,
  current_price REAL,
  quantity REAL NOT NULL,
  position_size REAL NOT NULL,
  initial_stoploss REAL NOT NULL,
  current_stoploss REAL NOT NULL,
  first_tp_price REAL NOT NULL,
  first_tp_quantity REAL NOT NULL,
  targets TEXT,
  status TEXT DEFAULT 'OPEN',
  is_break_even BOOLEAN DEFAULT 0,
  is_trailing BOOLEAN DEFAULT 0,
  first_tp_hit_at DATETIME,
  unrealized_pnl REAL DEFAULT 0,
  pnl_percentage REAL DEFAULT 0,
  entry_order_id TEXT,
  sl_order_id TEXT,
  tp_order_id TEXT,
  highest_price REAL,
  lowest_price REAL,
  opened_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  closed_at DATETIME,
  last_checked_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (signal_id) REFERENCES signals(id)
);

-- Waiting signals table
CREATE TABLE IF NOT EXISTS waiting_signals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  signal_id INTEGER,
  symbol TEXT NOT NULL,
  direction TEXT NOT NULL,
  original_entry_min REAL,
  original_entry_max REAL,
  current_price REAL,
  ai_verdict TEXT,
  last_score INTEGER DEFAULT 0,
  conditions_met_count INTEGER DEFAULT 0,
  total_checks INTEGER DEFAULT 0,
  wait_time_hours REAL DEFAULT 0,
  status TEXT DEFAULT 'WAITING',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  last_checked_at DATETIME,
  executed_at DATETIME,
  executed_price REAL,
  FOREIGN KEY (signal_id) REFERENCES signals(id)
);

-- Create indexes
CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status);
CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at);
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
CREATE INDEX IF NOT EXISTS idx_positions_symbol ON positions(symbol);
CREATE INDEX IF NOT EXISTS idx_waiting_status ON waiting_signals(status);
```

#### 5. Activate Workflows

1. **Activate Triggers:**
   - 01-telegram-signal-receiver (Telegram trigger)
   - 05-trailing-stop-monitor (Schedule trigger)

2. **Keep Webhooks Ready:**
   - 02-signal-parser
   - 03-ai-analyzer
   - 04-position-manager
   - 06-main-orchestrator

---

## Testing

### Test Signal

Send this to your Telegram bot:

```
🟢BTC/USDT LONG
Margin: Cross, 10X
ENTRY: <50000-51000>
🎯TARGETS:
1. [52000] 2. [53000] 3. [54000]
❌STOPLOSS: [48000]
```

### Expected Flow

1. **Telegram Receiver:**
   - Receives message
   - Filters (not a profit message, has /USDT)
   - Sends to orchestrator
   - Confirms receipt

2. **Orchestrator:**
   - Checks max positions (5)
   - Calls parser

3. **Parser:**
   - Extracts: BTC, LONG, 10x, 50500 entry, [52000, 53000, 54000] targets, 48000 SL
   - Validates: SL < Entry < Targets ✓
   - Returns parsed signal

4. **AI Analyzer:**
   - Technical analysis → BULLISH
   - Risk analysis → BULLISH
   - Sentiment analysis → BULLISH
   - Consensus: 3 BULLISH vs 0 BEARISH
   - Trend aligned: BULLISH + LONG ✓

5. **Position Manager:**
   - Calculates: 10000 * 0.10 = 1000 USDT position
   - Quantity: 1000 / 50500 = 0.0198 BTC
   - Sets margin: CROSS
   - Sets leverage: 10x
   - Opens market order: BUY 0.0198 BTC
   - Places SL: SELL @ 48000
   - Places TP: SELL 0.00495 BTC @ 52000 (25%)
   - Saves to database

6. **Notification:**
   ```
   ✅ Position Opened!
   
   🪙 Symbol: BTCUSDT
   📊 Direction: LONG
   💰 Entry: 50500
   🎯 First TP: 52000
   ❌ Stop Loss: 48000
   ⚡ Leverage: 10x
   📈 Size: 0.0198 (1000 USDT)
   
   🤖 AI Verdict: BULLISH
   📊 Consensus: 3 BULLISH vs 0 BEARISH
   🎯 Confidence: High
   ```

7. **Monitoring (every 30s):**
   - Checks position on Binance
   - If first TP hit → Moves SL to 50500 (breakeven)
   - If price rises → Updates trailing SL
   - If position closes → Marks as closed

---

## Monitoring

### Execution Logs

1. Go to "Executions" in n8n
2. Filter by workflow
3. Check for errors
4. Review execution time

### Database Queries

```sql
-- Active positions
SELECT * FROM positions WHERE status IN ('OPEN', 'BREAK_EVEN', 'TRAILING');

-- Recent signals
SELECT * FROM signals ORDER BY created_at DESC LIMIT 10;

-- Position performance
SELECT 
  symbol,
  side,
  entry_price,
  current_price,
  pnl_percentage,
  status
FROM positions
WHERE closed_at IS NOT NULL
ORDER BY closed_at DESC
LIMIT 20;

-- Waiting signals
SELECT * FROM waiting_signals WHERE status = 'WAITING';
```

### Performance Metrics

- **Signal Processing Time:** ~15-20 seconds (with AI)
- **Position Opening Time:** ~2-3 seconds
- **Monitoring Interval:** 30 seconds
- **Max Concurrent Positions:** 5 (configurable)

---

## Troubleshooting

### Issue: Telegram not receiving messages

**Check:**
1. Bot token is correct
2. Chat ID is correct
3. Bot has permission to read channel messages
4. Webhook is active

**Fix:**
```bash
# Test bot token
curl https://api.telegram.org/bot<TOKEN>/getMe

# Get chat ID
curl https://api.telegram.org/bot<TOKEN>/getUpdates
```

---

### Issue: AI analysis failing

**Check:**
1. DeepSeek API key is valid
2. Rate limits not exceeded
3. Prompt format is correct

**Fix:**
- Check API key in credentials
- Add longer delays between calls
- Verify base URL is correct

---

### Issue: Binance orders failing

**Check:**
1. API keys have futures trading permission
2. Testnet is being used (not mainnet)
3. Timestamp is synchronized
4. Symbol format is correct (BTCUSDT not BTC/USDT)

**Fix:**
```bash
# Test API connection
curl -H "X-MBX-APIKEY: <KEY>" \
  "https://testnet.binancefuture.com/fapi/v2/account?timestamp=$(date +%s)000"
```

---

### Issue: Trailing stop not updating

**Check:**
1. Schedule trigger is active
2. Database connection is working
3. Position status is correct
4. Binance API is responding

**Fix:**
- Check execution logs for errors
- Verify database has active positions
- Test Binance API manually

---

## Advanced Configuration

### Custom Risk Management

Modify environment variables:

```bash
# Conservative (5% risk)
RISK_PERCENTAGE=5.0
FIRST_TP_PERCENTAGE=50.0
TRAILING_STOP_PERCENTAGE=2.0

# Aggressive (15% risk)
RISK_PERCENTAGE=15.0
FIRST_TP_PERCENTAGE=15.0
TRAILING_STOP_PERCENTAGE=1.0
```

### Multiple Telegram Channels

1. Duplicate "01-telegram-signal-receiver"
2. Configure different channel IDs
3. Both feed into same orchestrator

### Custom AI Models

1. Edit "03-ai-analyzer"
2. Change model in OpenAI node
3. Adjust prompts as needed
4. Test with sample signals

### Additional Exchanges

1. Duplicate "04-position-manager"
2. Replace Binance API calls
3. Adjust order parameters
4. Update database schema

---

## Best Practices

### Security

1. **Never commit API keys**
   - Use environment variables
   - Rotate keys regularly
   - Use testnet for development

2. **Limit API permissions**
   - Futures trading only
   - No withdrawal permission
   - IP whitelist if possible

3. **Monitor executions**
   - Check logs daily
   - Set up alerts
   - Review failed executions

### Performance

1. **Optimize database**
   - Create indexes
   - Archive old data
   - Vacuum regularly

2. **Rate limiting**
   - Respect API limits
   - Add delays between calls
   - Use batch operations

3. **Error handling**
   - Add retry logic
   - Log all errors
   - Send notifications

### Maintenance

1. **Regular updates**
   - Update n8n version
   - Review workflow logic
   - Optimize slow nodes

2. **Backup**
   - Export workflows regularly
   - Backup database
   - Document changes

3. **Testing**
   - Test with small amounts
   - Use testnet first
   - Verify all scenarios

---

## Support

For issues or questions:

1. Check execution logs in n8n
2. Review database entries
3. Test API connections
4. Consult n8n documentation: https://docs.n8n.io

---

## License

Same as main project (MIT)

---

## Contributing

To contribute improvements:

1. Test changes on testnet
2. Document modifications
3. Export updated workflow JSON
4. Submit pull request

---

## Changelog

### v1.0.0 (2024-01-01)
- Initial n8n conversion
- All 6 workflows implemented
- Full feature parity with Python version
- Comprehensive documentation
