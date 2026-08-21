# ⚡ Hızlı Başlangıç (5 Dakika)

## 1️⃣ Gereksinimler

- Docker ve Docker Compose (ÖNERİLEN)
- VEYA Python 3.11+

## 2️⃣ Kurulum

### Docker ile (Kolay)

```bash
# Projeyi indir
git clone <repo-url>
cd TRADINGBOT

# Environment dosyasını oluştur
cp env.example .env

# API anahtarlarını .env dosyasına ekle
nano .env

# Başlat
docker-compose up -d

# Logları izle
docker-compose logs -f trading-bot
```

### Manuel (Python ile)

```bash
# Virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# veya
venv\Scripts\activate  # Windows

# Bağımlılıklar
pip install -r requirements.txt

# Environment
cp env.example .env
nano .env

# Çalıştır
python src/main.py
```

## 3️⃣ .env Dosyasını Düzenle

**ÖNEMLİ:** Önce testnet ile test edin!

```env
# Binance TESTNET (güvenli)
BINANCE_API_KEY=testnet_key_buraya
BINANCE_API_SECRET=testnet_secret_buraya
BINANCE_BASE_URL=https://testnet.binancefuture.com

# Telegram
TELEGRAM_BOT_TOKEN=bot_token_buraya
TELEGRAM_CHAT_ID=chat_id_buraya

# OpenAI
OPENAI_API_KEY=openai_key_buraya

# Diğerleri varsayılan bırakılabilir
```

### API Anahtarlarını Nereden Alırım?

#### Binance Testnet

1. https://testnet.binancefuture.com
2. GitHub ile giriş yap
3. API Management → Create API
4. Anahtarları kopyala

#### Telegram Bot

1. Telegram'da @BotFather'a mesaj at
2. `/newbot` komutunu gönder
3. İsim ver
4. Token'ı kopyala

#### Chat ID

1. Bot'una mesaj at
2. https://api.telegram.org/bot<TOKEN>/getUpdates
3. `chat.id` değerini kopyala

#### OpenAI

1. https://platform.openai.com
2. API Keys → Create new key
3. Anahtarı kopyala

## 4️⃣ İlk Test

### Bot'unuza şu mesajı gönderin:

```
BTC/USDT LONG
MARGIN: 5X
ENTRY: <45000-46000>
TARGETS:
1. [47000]
2. [48000]
3. [49000]
STOPLOSS: [44000]
```

### Bot şunları yapacak:

1. ✅ Mesajı parse eder
2. 🤖 AI analizi yapar (3 farklı perspektif)
3. 📊 Trend uyumluluğunu kontrol eder
4. 🚀 Pozisyon açar
5. 🎯 Stop loss ve take profit koyar
6. 📢 Bildirim gönderir

## 5️⃣ API ile Test

```bash
# Sağlık kontrolü
curl http://localhost:8000/health

# Açık pozisyonlar
curl http://localhost:8000/positions

# İstatistikler
curl http://localhost:8000/stats
```

## 6️⃣ Telegram Komutları

- `/start` - Bot'u başlat
- `/status` - Durum bilgisi
- `/positions` - Açık pozisyonlar

## 7️⃣ Logları İzle

```bash
# Docker
docker-compose logs -f trading-bot

# Manuel
tail -f logs/bot.log
tail -f logs/trades.log
```

## 8️⃣ Önemli Notlar

### ⚠️ Güvenlik

1. **TESTNET ile başla!**
2. Gerçek para ile test etme
3. API anahtarlarına IP kısıtlaması koy
4. Withdrawal iznini kapat

### 💰 Risk Ayarları

Varsayılan değerler:

- Risk: %10 (bakiyenin 1/10'u)
- İlk TP: %25 (pozisyonun 1/4'ü)
- Trailing SL: %1.5
- Check interval: 30 saniye

### 🎯 İş Akışı

1. **Sinyal gelir** → Parse edilir
2. **AI analizi** → 3 farklı perspektif
3. **Onay veya red** → Trend kontrolü
4. **Pozisyon açılır** → SL/TP konur
5. **İlk TP vurur** → Break-even aktif
6. **Trailing başlar** → Kar artar, risk sıfır

## 9️⃣ Sorun Giderme

### Bot başlamıyor?

```bash
# Logları kontrol et
docker-compose logs trading-bot

# Environment değişkenlerini kontrol et
cat .env

# Port kullanımda mı?
lsof -i :8000
```

### API hatası?

- Anahtarlar doğru mu?
- Testnet URL'si doğru mu?
- IP whitelist var mı?

### Telegram bot cevap vermiyor?

- Token doğru mu?
- Chat ID doğru mu?
- Bot kanala eklenmiş mi?

## 🔟 Gerçek Para ile Geçiş

**Testnet'te en az 1 hafta test ettikten sonra:**

1. Binance Futures hesabı aç
2. Gerçek API anahtarları al
3. `.env` dosyasını güncelle:

```env
BINANCE_BASE_URL=https://fapi.binance.com
BINANCE_API_KEY=gerçek_key
BINANCE_API_SECRET=gerçek_secret
```

4. **Küçük bakiye ile başla** (örn: 100 USDT)
5. Risk parametrelerini düşür:

```env
RISK_PERCENTAGE=2  # %2 yerine %10
```

6. İlk 10 işlemi dikkatle izle
7. Stratejini optimize et

## 📚 Daha Fazla Bilgi

- [INSTALL.md](INSTALL.md) - Detaylı kurulum
- [USAGE.md](USAGE.md) - Kullanım rehberi
- [README.md](README.md) - Genel bilgi

## 🆘 Yardım

Sorun mu yaşıyorsun?

1. `logs/errors.log` dosyasını kontrol et
2. GitHub Issues'a bak
3. Telegram destek grubuna sor

## ✅ Kontrol Listesi

- [ ] Docker veya Python kurulu
- [ ] Testnet hesabı oluşturuldu
- [ ] API anahtarları alındı
- [ ] Telegram bot oluşturuldu
- [ ] OpenAI API key alındı
- [ ] `.env` dosyası düzenlendi
- [ ] Bot başlatıldı
- [ ] İlk test sinyali gönderildi
- [ ] Loglar kontrol edildi
- [ ] Pozisyon açıldı
- [ ] Break-even çalıştı
- [ ] Trailing aktif oldu

Hepsi ✅ ise **hazırsın!** 🚀

