# Crypto Trading Bot - n8n Conversion Guide

## Overview

This document provides a comprehensive guide for converting the Python-based crypto trading bot to n8n workflows. The conversion maintains all functionality while leveraging n8n's visual workflow capabilities.

## Architecture Comparison

### Original Python Architecture

```
┌─────────────────┐
│  Telegram Bot   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Signal Queue   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Orchestrator   │
└────────┬────────┘
         │
    ┌────┴────┬────────────┬──────────────┐
    ▼         ▼            ▼              ▼
┌────────┐ ┌────────┐ ┌──────────┐ ┌──────────┐
│ Parser │ │   AI   │ │ Position │ │ Monitor  │
│        │ │Analyzer│ │ Manager  │ │          │
└────────┘ └────────┘ └──────────┘ └──────────┘
```

### n8n Workflow Architecture

```
┌──────────────────────────────────────────────────────┐
│  01-telegram-signal-receiver.json                    │
│  - Telegram Trigger                                  │
│  - Message Filtering                                 │
│  - Queue Management                                  │
└────────────────────┬─────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────────┐
│  06-main-orchestrator.json                           │
│  - Max Position Check                                │
│  - Workflow Coordination                             │
│  - Error Handling                                    │
└─┬──────────┬──────────┬──────────┬───────────────────┘
  │          │          │          │
  ▼          ▼          ▼          ▼
┌────────┐ ┌────────┐ ┌────────┐ ┌────────────────────┐
│02-     │ │03-     │ │04-     │ │05-                 │
│signal- │ │ai-     │ │position│ │trailing-stop-      │
│parser  │ │analyzer│ │-manager│ │monitor             │
└────────┘ └────────┘ └────────┘ └────────────────────┘
```

## Workflow Breakdown

### 1. Telegram Signal Receiver (01-telegram-signal-receiver.json)

**Purpose:** Receives and filters Telegram messages

**Key Components:**
- **Telegram Trigger:** Listens for channel posts and messages
- **Message Filter:** Filters out profit notifications and non-trading messages
- **Signal Queue:** Sends valid signals to the main orchestrator

**Flow:**
1. Receive Telegram message
2. Check if it's a profit notification (skip if yes)
3. Check if it contains a trading pair (/USDT)
4. Send to signal queue if valid
5. Send confirmation to user

**Environment Variables:**
- `TELEGRAM_BOT_TOKEN`
- `API_BASE_URL`

---

### 2. Signal Parser (02-signal-parser.json)

**Purpose:** Parses raw Telegram messages into structured signal data

**Key Components:**
- **Webhook Trigger:** Receives signal parsing requests
- **Parse Function:** Extracts coin, direction, leverage, entry, targets, stop loss
- **Validation:** Validates signal logic (LONG/SHORT rules)
- **Database Save:** Stores parsed signal

**Parsing Logic:**
```javascript
// Extracts:
- Coin: BTC, ETH, etc.
- Direction: LONG or SHORT
- Leverage: 1-125x
- Margin Type: CROSS or ISOLATED
- Entry Range: <min-max>
- Targets: [TP1, TP2, TP3, ...]
- Stop Loss: SL price
```

**Validation Rules:**
- LONG: Stop loss < Entry < Targets
- SHORT: Targets < Entry < Stop loss
- Leverage: 1-125x
- All required fields present

---

### 3. AI Analyzer (03-ai-analyzer.json)

**Purpose:** Performs 3-perspective AI analysis using DeepSeek/GPT-4o

**Key Components:**
- **Technical Analysis:** Support/resistance, R/R ratio, momentum
- **Risk Analysis:** Position sizing, leverage, volatility
- **Sentiment Analysis:** Market sentiment, news, funding rates
- **Consensus Calculator:** Votes and determines final verdict

**Analysis Flow:**
1. Technical Analysis → Wait 1.5s (rate limit)
2. Risk Analysis → Wait 1.5s
3. Sentiment Analysis
4. Calculate consensus (BULLISH vs BEARISH)
5. Check trend alignment

**AI Prompts:**
- Each analysis gets a specific prompt
- Responses must include "VERDICT: BULLISH/BEARISH"
- Reasoning must be 3-4 sentences

**Consensus Logic:**
```javascript
bullishCount > bearishCount ? 'BULLISH' : 'BEARISH'
trendAligned = (verdict === 'BULLISH' && direction === 'LONG') ||
               (verdict === 'BEARISH' && direction === 'SHORT')
```

**Confidence Levels:**
- High: Unanimous (3/3)
- Medium: 2/3 majority
- Low: Split decision

---

### 4. Position Manager (04-position-manager.json)

**Purpose:** Opens positions on Binance Futures

**Key Components:**
- **Position Calculator:** Calculates size based on risk
- **Binance API Calls:**
  1. Set Margin Type (ISOLATED/CROSS)
  2. Set Leverage
  3. Open Market Order
  4. Place Stop Loss
  5. Place First Take Profit (25%)
- **Database Save:** Records position details

**Position Calculation:**
```javascript
accountBalance = 10000 USDT (configurable)
riskPercentage = 10% (configurable)
riskAmount = accountBalance * riskPercentage
positionSize = riskAmount
quantity = positionSize / entryPrice
```

**Order Sequence:**
1. Set margin type (handles "already set" error)
2. Set leverage
3. Open market order (BUY for LONG, SELL for SHORT)
4. Place stop loss (opposite side, closePosition=true)
5. Place first TP (25% of quantity)

**Database Fields:**
- symbol, side, leverage, margin_type
- entry_price, quantity, position_size
- initial_stoploss, current_stoploss
- first_tp_price, first_tp_quantity
- status, opened_at

---

### 5. Trailing Stop Monitor (05-trailing-stop-monitor.json)

**Purpose:** Monitors positions and updates trailing stops

**Key Components:**
- **Schedule Trigger:** Runs every 30 seconds
- **Position Checker:** Checks if positions are still open
- **Breakeven Logic:** Moves SL to entry after first TP hit
- **Trailing Logic:** Updates SL as price moves favorably

**Monitoring Flow:**
1. Get all active positions from database
2. For each position:
   - Check if still open on Binance
   - If closed → Mark as closed in DB
   - If first TP hit (80% remaining) → Move to breakeven
   - If trailing → Calculate and update SL

**Breakeven Logic:**
```javascript
// When first TP hits (25% closed)
remainingPct = (currentQty / originalQty) * 100
if (remainingPct <= 80) {
  // Cancel old SL orders
  // Place new SL at entry price
  // Update status to BREAK_EVEN
}
```

**Trailing Stop Logic:**
```javascript
// LONG positions
newStopLoss = currentPrice * (1 - trailingPct)
shouldUpdate = newStopLoss > currentStopLoss

// SHORT positions
newStopLoss = currentPrice * (1 + trailingPct)
shouldUpdate = newStopLoss < currentStopLoss
```

**States:**
- OPEN: Initial state
- BREAK_EVEN: First TP hit, SL at entry
- TRAILING: Actively trailing
- CLOSED: Position closed

---

### 6. Main Orchestrator (06-main-orchestrator.json)

**Purpose:** Coordinates the entire signal processing workflow

**Key Components:**
- **Max Position Check:** Ensures position limit not exceeded
- **Workflow Coordination:** Calls parser → analyzer → position manager
- **Waiting Mode:** Handles trend-misaligned signals
- **Notifications:** Sends Telegram updates at each stage

**Processing Flow:**
```
1. Check max positions (default: 5)
   ├─ At limit → Reject signal
   └─ Has capacity → Continue

2. Parse signal
   ├─ Parse failed → Notify and reject
   └─ Parse success → Continue

3. AI Analysis (3 perspectives)
   └─ Get verdict and trend alignment

4. Check trend alignment
   ├─ Aligned → Open position
   │   └─ Notify success
   │
   └─ Not aligned
       ├─ Waiting mode enabled → Add to queue
       │   └─ Notify waiting
       │
       └─ Waiting mode disabled → Reject
           └─ Notify rejected
```

**Decision Points:**
1. **Max Positions:** Current < MAX_POSITIONS
2. **Parse Valid:** All required fields present and valid
3. **Trend Aligned:** AI verdict matches signal direction
4. **Waiting Mode:** WAITING_MODE_ENABLED = true

**Notifications:**
- ✅ Position opened (with details)
- 🕐 Added to waiting mode
- ❌ Signal rejected
- ⚠️ Parse failed
- ⚠️ Max positions reached

---

## Environment Variables

### Required Variables

```bash
# Telegram
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# Binance
BINANCE_API_KEY=your_api_key
BINANCE_API_SECRET=your_api_secret
BINANCE_BASE_URL=https://testnet.binancefuture.com

# AI Models
DEEPSEEK_API_KEY=your_deepseek_key
DEEPSEEK_MODEL=deepseek-reasoner
GEMINI_API_KEY=your_gemini_key (fallback)

# Trading Parameters
ACCOUNT_BALANCE=10000.0
RISK_PERCENTAGE=10.0
FIRST_TP_PERCENTAGE=25.0
TRAILING_STOP_PERCENTAGE=1.5
MAX_POSITIONS=5
MARGIN_TYPE=ISOLATED
MAX_LEVERAGE=20

# Waiting Mode
WAITING_MODE_ENABLED=false

# n8n URLs
N8N_BASE_URL=http://localhost:5678
API_BASE_URL=http://localhost:8000
```

---

## Database Schema

### Signals Table
```sql
CREATE TABLE signals (
  id INTEGER PRIMARY KEY,
  raw_message TEXT NOT NULL,
  coin TEXT,
  direction TEXT,
  leverage INTEGER,
  entry_min REAL,
  entry_max REAL,
  entry REAL,
  targets TEXT, -- JSON array
  stoploss REAL,
  status TEXT,
  ai_verdict TEXT,
  trend_aligned BOOLEAN,
  error_message TEXT,
  created_at DATETIME,
  updated_at DATETIME
);
```

### Positions Table
```sql
CREATE TABLE positions (
  id INTEGER PRIMARY KEY,
  signal_id INTEGER,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL,
  leverage INTEGER NOT NULL,
  margin_type TEXT,
  entry_price REAL NOT NULL,
  current_price REAL,
  quantity REAL NOT NULL,
  position_size REAL NOT NULL,
  initial_stoploss REAL NOT NULL,
  current_stoploss REAL NOT NULL,
  first_tp_price REAL NOT NULL,
  first_tp_quantity REAL NOT NULL,
  targets TEXT,
  status TEXT,
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
  opened_at DATETIME,
  closed_at DATETIME,
  last_checked_at DATETIME
);
```

### Waiting Signals Table
```sql
CREATE TABLE waiting_signals (
  id INTEGER PRIMARY KEY,
  signal_id INTEGER,
  symbol TEXT NOT NULL,
  direction TEXT NOT NULL,
  original_entry_min REAL,
  original_entry_max REAL,
  current_price REAL,
  ai_verdict TEXT,
  last_score INTEGER,
  conditions_met_count INTEGER DEFAULT 0,
  total_checks INTEGER DEFAULT 0,
  wait_time_hours REAL DEFAULT 0,
  status TEXT,
  created_at DATETIME,
  last_checked_at DATETIME,
  executed_at DATETIME,
  executed_price REAL
);
```

---

## Setup Instructions

### 1. Install n8n

```bash
npm install -g n8n
# or
docker run -it --rm --name n8n -p 5678:5678 n8nio/n8n
```

### 2. Import Workflows

1. Open n8n (http://localhost:5678)
2. Go to Workflows
3. Click "Import from File"
4. Import each workflow JSON file in order:
   - 01-telegram-signal-receiver.json
   - 02-signal-parser.json
   - 03-ai-analyzer.json
   - 04-position-manager.json
   - 05-trailing-stop-monitor.json
   - 06-main-orchestrator.json

### 3. Configure Credentials

#### Telegram Bot API
1. Go to Credentials
2. Add "Telegram API"
3. Enter bot token

#### DeepSeek/OpenAI API
1. Add "OpenAI API" credential
2. Enter DeepSeek API key
3. Set base URL to DeepSeek endpoint

#### SQLite Database
1. Add "SQLite" credential
2. Set database path

### 4. Set Environment Variables

In n8n settings or .env file:
```bash
# Copy all variables from section above
```

### 5. Activate Workflows

1. Activate "01-telegram-signal-receiver" (trigger)
2. Activate "05-trailing-stop-monitor" (schedule)
3. Keep others inactive (webhook-based)

---

## Testing

### Test Signal Format

```
🟢BTC/USDT LONG
Margin: Cross, 10X
ENTRY: <50000-51000>
🎯TARGETS:
1. [52000] 2. [53000] 3. [54000]
❌STOPLOSS: [48000]
```

### Test Flow

1. Send test signal to Telegram bot
2. Check n8n execution logs
3. Verify database entries
4. Check Binance testnet for orders
5. Monitor trailing stop updates

---

## Advantages of n8n Version

### 1. **Visual Workflow**
- Easy to understand and modify
- No coding required for changes
- Clear flow visualization

### 2. **Built-in Error Handling**
- Retry mechanisms
- Error notifications
- Execution history

### 3. **Scalability**
- Easy to add new workflows
- Parallel execution
- Queue management

### 4. **Monitoring**
- Execution logs
- Performance metrics
- Real-time status

### 5. **Flexibility**
- Easy to add new exchanges
- Simple to modify logic
- Quick to test changes

---

## Limitations

### 1. **Complex Logic**
Some Python logic is harder to replicate in n8n:
- Complex data transformations
- Advanced error handling
- Stateful operations

### 2. **Performance**
- HTTP overhead between workflows
- Slower than native Python
- More resource intensive

### 3. **Debugging**
- Harder to debug complex issues
- Limited logging capabilities
- No IDE support

---

## Migration Strategy

### Phase 1: Parallel Running
1. Run both Python and n8n versions
2. Compare results
3. Fix discrepancies

### Phase 2: Gradual Cutover
1. Start with signal parsing
2. Add AI analysis
3. Enable position management
4. Activate monitoring

### Phase 3: Full Migration
1. Disable Python version
2. Monitor n8n performance
3. Optimize workflows
4. Add enhancements

---

## Maintenance

### Regular Tasks

1. **Monitor Executions**
   - Check for failed workflows
   - Review error logs
   - Optimize slow nodes

2. **Update Credentials**
   - Rotate API keys
   - Update tokens
   - Refresh connections

3. **Database Cleanup**
   - Archive old signals
   - Clean up closed positions
   - Optimize queries

4. **Performance Tuning**
   - Adjust rate limits
   - Optimize queries
   - Cache frequently used data

---

## Troubleshooting

### Common Issues

#### 1. Telegram Not Receiving Messages
- Check bot token
- Verify webhook URL
- Check firewall rules

#### 2. AI Analysis Failing
- Verify API keys
- Check rate limits
- Review prompt format

#### 3. Binance Orders Failing
- Check API permissions
- Verify timestamp sync
- Review order parameters

#### 4. Trailing Stop Not Updating
- Check schedule trigger
- Verify database connection
- Review position status

---

## Support

For issues or questions:
1. Check execution logs in n8n
2. Review database entries
3. Check Binance API logs
4. Consult n8n documentation

---

## Conclusion

The n8n conversion provides a visual, maintainable alternative to the Python implementation while preserving all core functionality. The modular workflow design makes it easy to understand, modify, and extend the trading bot system.
