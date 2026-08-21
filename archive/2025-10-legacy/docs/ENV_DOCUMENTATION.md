# Environment Variables Documentation

This document contains all environment variables needed to run the Crypto Trading Bot. Copy this configuration to your `.env` file.

## 🔐 Required Configuration

### Binance API Configuration (TESTNET)

```env
# Use Binance Testnet for safe testing without real money
BINANCE_API_KEY=your_testnet_api_key_here
BINANCE_API_SECRET=your_testnet_api_secret_here
BINANCE_BASE_URL=https://testnet.binancefuture.com
```

**How to get Testnet keys:**
1. Visit: https://testnet.binancefuture.com
2. Login with your Binance account
3. Generate API keys from the dashboard
4. **Important:** These are testnet keys - no real money involved!

**For Production (USE WITH CAUTION):**
```env
BINANCE_BASE_URL=https://fapi.binance.com
```

### Telegram Bot Configuration

```env
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
```

**How to set up:**
1. Create bot: Talk to [@BotFather](https://t.me/botfather) on Telegram
2. Use `/newbot` command and follow instructions
3. Copy the token provided
4. Get your chat ID: Talk to [@userinfobot](https://t.me/userinfobot)

### AI Model Configuration

The bot supports multiple AI providers for signal analysis:

#### OpenAI (GPT-4)
```env
OPENAI_API_KEY=sk-proj-your_openai_key_here
OPENAI_MODEL=gpt-4o
OPENAI_RATE_LIMIT_SECONDS=3.0
```

**Get API Key:** https://platform.openai.com/api-keys

#### Google Gemini
```env
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-2.0-flash-exp
```

**Get API Key:** https://makersuite.google.com/app/apikey

#### DeepSeek (Alternative)
```env
DEEPSEEK_API_KEY=sk-your_deepseek_key_here
DEEPSEEK_MODEL=deepseek-reasoner
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

**Get API Key:** https://platform.deepseek.com/

---

## 📊 Trading Parameters

### Account Configuration
```env
ACCOUNT_BALANCE=10000          # Initial testnet balance (USDT)
RISK_PERCENTAGE=2              # Risk per trade (2% of balance)
MAX_POSITIONS=5                # Maximum concurrent positions
MAX_LEVERAGE=20                # Maximum leverage allowed
MARGIN_TYPE=ISOLATED           # ISOLATED or CROSS
```

### Take Profit & Stop Loss
```env
FIRST_TP_PERCENTAGE=25         # Take 25% profit at first TP
TRAILING_STOP_PERCENTAGE=1.5   # Trailing stop distance (1.5%)
```

### Position Monitoring
```env
CHECK_INTERVAL_SECONDS=30      # How often to check positions
BINANCE_RATE_LIMIT_SECONDS=0.5 # Rate limit for Binance API
SIGNAL_QUEUE_DELAY_SECONDS=2.0 # Delay between processing signals
```

---

## 🎯 Waiting Mode Configuration

Waiting Mode analyzes market conditions before entering trades for better entry prices.

### Enable/Disable
```env
WAITING_MODE_ENABLED=false           # Set to true to enable
WAITING_MODE_MAX_POSITIONS=3         # Max positions in waiting mode
WAITING_MODE_MAX_HOURS=24            # Max hours to wait for entry
WAITING_MODE_CHECK_INTERVAL_MINUTES=5 # Check interval
```

### Technical Indicator Settings
```env
# RSI (Relative Strength Index)
WAITING_MODE_RSI_PERIOD=14
WAITING_MODE_RSI_OVERSOLD=30
WAITING_MODE_RSI_OVERBOUGHT=70

# MACD (Moving Average Convergence Divergence)
WAITING_MODE_MACD_FAST=12
WAITING_MODE_MACD_SLOW=26
WAITING_MODE_MACD_SIGNAL=9

# Bollinger Bands
WAITING_MODE_BB_PERIOD=20
WAITING_MODE_BB_STD_DEV=2.0
```

### Entry Conditions
```env
WAITING_MODE_MIN_CONDITIONS=2      # Minimum indicators that must align
WAITING_MODE_PRICE_IMPROVEMENT=0.5 # Required price improvement (%)
```

---

## 💾 Database Configuration

### SQLite (Default - Simple Setup)
```env
DATABASE_URL=sqlite:///./tradingbot.db
```

### PostgreSQL (Production Ready)
```env
DATABASE_URL=postgresql://user:password@localhost:5432/tradingbot
DB_PASSWORD=your_secure_password_here
```

### Redis (Optional - For Caching)
```env
REDIS_URL=redis://localhost:6379/0
USE_REDIS=false
```

---

## 🌐 API Server Configuration

```env
API_HOST=0.0.0.0  # Bind to all interfaces
API_PORT=8080     # API server port
```

### Security
```env
JWT_SECRET=change_this_to_random_string_in_production
API_KEY=your_api_key_for_protected_endpoints
```

**Generate secure secrets:**
```bash
# On Linux/Mac:
openssl rand -hex 32

# On Python:
python -c "import secrets; print(secrets.token_hex(32))"
```

---

## 📝 Application Settings

```env
APP_ENV=development    # development or production
LOG_LEVEL=INFO        # DEBUG, INFO, WARNING, ERROR
DEBUG=true            # Enable debug mode
```

---

## 📊 Monitoring & Metrics

```env
ENABLE_METRICS=true   # Enable Prometheus metrics
METRICS_PORT=9090     # Metrics endpoint port
```

---

## 🚀 Quick Start Configuration

### For Testing (Recommended for beginners)
```env
# Binance Testnet
BINANCE_BASE_URL=https://testnet.binancefuture.com

# Conservative settings
RISK_PERCENTAGE=1
MAX_POSITIONS=3
MAX_LEVERAGE=10
MARGIN_TYPE=ISOLATED

# Waiting mode disabled for faster testing
WAITING_MODE_ENABLED=false

# Development mode
APP_ENV=development
DEBUG=true
LOG_LEVEL=INFO
```

### For Production (Use with caution!)
```env
# Real Binance Futures
BINANCE_BASE_URL=https://fapi.binance.com

# More conservative for real money
RISK_PERCENTAGE=1
MAX_POSITIONS=2
MAX_LEVERAGE=5
MARGIN_TYPE=ISOLATED

# Enable waiting mode for better entries
WAITING_MODE_ENABLED=true

# Production settings
APP_ENV=production
DEBUG=false
LOG_LEVEL=WARNING

# Use PostgreSQL
DATABASE_URL=postgresql://user:password@localhost:5432/tradingbot

# Strong security
JWT_SECRET=<generate-strong-random-string>
```

---

## 📋 Complete Example .env File

```env
# === BINANCE CONFIGURATION ===
BINANCE_API_KEY=your_testnet_api_key_here
BINANCE_API_SECRET=your_testnet_secret_here
BINANCE_BASE_URL=https://testnet.binancefuture.com

# === TELEGRAM CONFIGURATION ===
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_CHAT_ID=-1234567890

# === AI CONFIGURATION ===
OPENAI_API_KEY=sk-proj-your_key_here
OPENAI_MODEL=gpt-4o
OPENAI_RATE_LIMIT_SECONDS=3.0

GEMINI_API_KEY=your_gemini_key_here
GEMINI_MODEL=gemini-2.0-flash-exp

DEEPSEEK_API_KEY=sk-your_deepseek_key
DEEPSEEK_MODEL=deepseek-reasoner
DEEPSEEK_BASE_URL=https://api.deepseek.com

# === TRADING PARAMETERS ===
ACCOUNT_BALANCE=10000
RISK_PERCENTAGE=2
FIRST_TP_PERCENTAGE=25
TRAILING_STOP_PERCENTAGE=1.5
CHECK_INTERVAL_SECONDS=30
MARGIN_TYPE=ISOLATED
MAX_LEVERAGE=20
MAX_POSITIONS=5

# === RATE LIMITING ===
BINANCE_RATE_LIMIT_SECONDS=0.5
SIGNAL_QUEUE_DELAY_SECONDS=2.0

# === WAITING MODE ===
WAITING_MODE_ENABLED=false
WAITING_MODE_MAX_POSITIONS=3
WAITING_MODE_MAX_HOURS=24
WAITING_MODE_CHECK_INTERVAL_MINUTES=5
WAITING_MODE_RSI_PERIOD=14
WAITING_MODE_RSI_OVERSOLD=30
WAITING_MODE_RSI_OVERBOUGHT=70
WAITING_MODE_MACD_FAST=12
WAITING_MODE_MACD_SLOW=26
WAITING_MODE_MACD_SIGNAL=9
WAITING_MODE_BB_PERIOD=20
WAITING_MODE_BB_STD_DEV=2.0
WAITING_MODE_MIN_CONDITIONS=2
WAITING_MODE_PRICE_IMPROVEMENT=0.5

# === DATABASE ===
DATABASE_URL=sqlite:///./tradingbot.db

# === API SERVER ===
API_HOST=0.0.0.0
API_PORT=8080
JWT_SECRET=change_this_in_production

# === APPLICATION ===
APP_ENV=development
LOG_LEVEL=INFO
DEBUG=true
```

---

## ⚠️ Security Best Practices

1. **Never commit .env file to git** - It's already in .gitignore
2. **Use testnet first** - Test everything before using real money
3. **Generate strong JWT_SECRET** - Use `openssl rand -hex 32`
4. **Rotate API keys regularly** - Especially for production
5. **Use ISOLATED margin** - Protects your account from liquidation
6. **Start with low leverage** - 5-10x max until you're comfortable
7. **Monitor your positions** - Use the monitoring dashboard
8. **Set up alerts** - Telegram notifications keep you informed

---

## 🆘 Troubleshooting

### "Invalid API Key" Error
- Check if you're using testnet keys with testnet URL
- Verify keys are copied correctly (no extra spaces)
- Ensure API key has futures trading permissions

### "Insufficient Balance" Error
- On testnet: Request funds from testnet faucet
- Check ACCOUNT_BALANCE matches your actual balance

### Telegram Bot Not Responding
- Verify bot token is correct
- Start conversation with bot first (send /start)
- Check chat ID is correct (use @userinfobot)

### AI Analysis Failing
- Check API key is valid and has credits
- Verify rate limits aren't exceeded
- Try alternative AI provider (Gemini or DeepSeek)

---

## 📞 Support

- GitHub Issues: Report bugs or request features
- Documentation: Check README.md for detailed setup
- Telegram: Monitor your bot's messages for insights

---

**Last Updated:** 2025-10-22
**Version:** 1.0.0
