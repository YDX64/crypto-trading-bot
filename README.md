> **GÜNCEL DURUM (2026-08-21):** Bu README Ekim 2025'ten kalmadır ve kısmen eskidir.
> Sistemi anlamak için önce [`CLAUDE.md`](CLAUDE.md) (çalışma sözleşmesi), sonra
> [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), [`docs/DECISIONS.md`](docs/DECISIONS.md),
> [`docs/RUNBOOK.md`](docs/RUNBOOK.md), [`docs/EXPERIMENTS.md`](docs/EXPERIMENTS.md) okunur.
> Eski araştırma/n8n dosyaları `archive/2025-10-legacy/` altındadır.

# 🤖 AI-Powered Crypto Trading Bot

Otomatik kripto para trading botu - Telegram sinyallerini AI analizi ile işler ve Binance'de pozisyon açar.

## ✨ Özellikler

### 🎯 Trading Özellikleri
- ✅ Telegram kanal entegrasyonu (otomatik sinyal okuma)
- ✅ Dual AI sistemi (GPT-4o + Gemini fallback)
- ✅ 3 perspektiften AI analizi (Technical, Risk, Sentiment)
- ✅ Otomatik trend uyumluluğu kontrolü
- ✅ Break-even stop loss
- ✅ Trailing stop loss (%1.5)
- ✅ Trailing profit (%0.5)
- ✅ İlk TP %25 sabit
- ✅ Max 5 pozisyon limiti
- ✅ Sıralı sinyal işleme (rate limit korumalı)

### 🤖 AI Özellikleri
- **Primary AI**: GPT-4o
- **Fallback AI**: Gemini 2.0 Flash
- **Sequential Analysis**: Her analiz arası 2 saniye bekleme
- **Rate Limiting**: OpenAI 3s, Gemini 1s

### 📊 Risk Yönetimi
- Risk: %2 (hesap başına)
- Position Size: %10 (kasanın)
- Leverage: Sinyalden alınır (yoksa config'den)
- Margin Type: Sinyalden alınır (CROSS/ISOLATED)

## 🚀 Kurulum

### Gereksinimler
- Python 3.9+
- Binance Testnet hesabı
- Telegram Bot Token
- OpenAI API Key
- Gemini API Key (fallback için)

### Kurulum Adımları

```bash
# Repository'yi klonlayın
git clone https://github.com/YDX64/trading-bot.git
cd trading-bot

# Sanal ortam oluşturun
python -m venv venv
source venv/bin/activate  # Linux/Mac
# veya
.\venv\Scripts\activate  # Windows

# Bağımlılıkları yükleyin
pip install -r requirements.txt

# .env dosyasını oluşturun
cp env.example .env

# .env dosyasını düzenleyin
# Telegram, Binance ve AI API anahtarlarınızı ekleyin
```

### .env Konfigürasyonu

```env
# Binance Testnet
BINANCE_API_KEY=your_testnet_api_key
BINANCE_SECRET_KEY=your_testnet_secret_key
BINANCE_BASE_URL=https://testnet.binancefuture.com

# Telegram
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
TELEGRAM_VIP_CHANNEL_ID=your_channel_id

# AI Models
OPENAI_API_KEY=your_openai_key
OPENAI_MODEL=gpt-4o
GEMINI_API_KEY=your_gemini_key
GEMINI_MODEL=gemini-2.0-flash-exp

# Trading Parameters
ACCOUNT_BALANCE=10000.0
RISK_PERCENTAGE=2.0
MAX_LEVERAGE=20
MAX_POSITIONS=5
```

## 📖 Kullanım

### Botu Başlatma

```bash
# Development modda
python -m src.main

# Container ile (EK dağıtım yolu — taşınabilir tek container)
scripts/docker_run.sh
```

> ⛔ Çıplak `docker compose up` KULLANMAYIN: entry-halt kilidini, Binance 418 ban
> penceresini ve "supervisord ile aynı anda çalışma" kapısını ATLAR.
> `scripts/docker_run.sh` bunların hepsini uygular.
> Ayrıntı: `docs/RUNBOOK.md` → "Container ile çalıştırma / başka sunucuya taşıma".

### API Endpoints

```bash
# Sistem durumu
curl http://localhost:8000/health

# İstatistikler
curl http://localhost:8000/stats

# Konfigürasyon
curl http://localhost:8000/config

# Açık pozisyonlar
curl http://localhost:8000/positions

# Sinyaller
curl http://localhost:8000/signals
```

## 🔧 Sinyal Formatı

Telegram kanalınızdan gelen sinyaller şu formatta olmalı:

```
🟢BTC/USDT LONG
Margin: Cross, 10X
ENTRY: <50000-51000>
🎯TARGETS:
1. [52000] 2. [53000] 3. [54000]
❌STOPLOSS: [48000]
```

## 📊 İş Akışı

```
1. Telegram Sinyali Alınır
   ↓
2. Parse Edilir (Coin, Direction, Leverage, Entry, TP, SL)
   ↓
3. AI Analizi (3 perspektif)
   ├─ Technical Analysis
   ├─ Risk Analysis  
   └─ Sentiment Analysis
   ↓
4. Konsensüs & Trend Kontrolü
   ↓
5. Pozisyon Açılır (Binance Testnet)
   ├─ Set Margin Type
   ├─ Set Leverage
   ├─ Open Order
   ├─ Set Stop Loss
   └─ Set Take Profits
   ↓
6. Trailing Management Başlar
   ├─ First TP → Breakeven
   ├─ Trailing SL
   └─ Trailing Profit
```

## 🏗️ Proje Yapısı

```
trading-bot/
├── src/
│   ├── analyzers/      # AI analiz modülleri
│   ├── core/           # Config, logger, database
│   ├── models/         # Data models
│   ├── parsers/        # Telegram parser
│   ├── services/       # Orchestrator, queue, telegram
│   └── trading/        # Binance client, position manager
├── docs/               # Dokümantasyon
├── logs/               # Log dosyaları
├── .env                # Ortam değişkenleri
├── requirements.txt    # Python dependencies
├── docker-compose.yml  # Docker config
└── README.md           # Bu dosya
```

## ⚙️ Konfigürasyon

### Rate Limiting
```python
OPENAI_RATE_LIMIT_SECONDS=3.0
BINANCE_RATE_LIMIT_SECONDS=0.5
SIGNAL_QUEUE_DELAY_SECONDS=2.0
```

### Trading Parameters
```python
FIRST_TP_PERCENTAGE=25.0
TRAILING_STOP_PERCENTAGE=1.5
TRAILING_PROFIT_PERCENTAGE=0.5
CHECK_INTERVAL_SECONDS=30
```

## 🐛 Troubleshooting

### Binance API Hataları
```bash
# Timestamp hatası
# Çözüm: Sistem saatinizi senkronize edin

# Rate limit hatası
# Çözüm: BINANCE_RATE_LIMIT_SECONDS değerini artırın
```

### AI Hataları
```bash
# GPT-4o hatası
# Çözüm: Otomatik olarak Gemini'ye geçer

# Rate limit hatası
# Çözüm: OPENAI_RATE_LIMIT_SECONDS değerini artırın
```

## 📝 Önemli Notlar

- ⚠️ **Testnet kullanın!** Production için ek güvenlik önlemleri gereklidir
- ⚠️ Risk yönetimi ayarlarını kendinize göre optimize edin
- ⚠️ API key'lerinizi asla paylaşmayın
- ⚠️ .env dosyasını git'e commit etmeyin

## 🔒 Güvenlik

```bash
# .env dosyasını .gitignore'a ekleyin
echo ".env" >> .gitignore

# API key'leri environment variables olarak kullanın
export BINANCE_API_KEY="your_key"
```

## 📈 Performans

- **Sinyal İşleme**: ~15 saniye (AI analizi dahil)
- **Pozisyon Açma**: ~2 saniye
- **Trailing Kontrol**: Her 30 saniyede bir
- **Max Concurrent Positions**: 5

## 🤝 Katkıda Bulunma

1. Fork edin
2. Feature branch oluşturun (`git checkout -b feature/amazing`)
3. Commit edin (`git commit -m 'feat: Add amazing feature'`)
4. Push edin (`git push origin feature/amazing`)
5. Pull Request açın

## 📜 Lisans

MIT License - Detaylar için [LICENSE](LICENSE) dosyasına bakın.

## 🙏 Teşekkürler

- OpenAI (GPT-4o)
- Google (Gemini AI)
- Binance (API)
- Telegram (Bot API)

## 📞 İletişim

- GitHub: [@YDX64](https://github.com/YDX64)
- Issues: [GitHub Issues](https://github.com/YDX64/trading-bot/issues)

---

**⚠️ UYARI**: Bu bot eğitim amaçlıdır. Gerçek parayla kullanmadan önce kapsamlı testler yapın!
