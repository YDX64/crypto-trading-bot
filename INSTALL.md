# 📦 Kurulum Rehberi

## Hızlı Başlangıç (Docker ile - Önerilen)

### 1. Projeyi İndir

```bash
git clone <repository-url>
cd TRADINGBOT
```

### 2. Environment Dosyasını Oluştur

```bash
cp env.example .env
```

`.env` dosyasını düzenle ve kendi API anahtarlarını gir:

```env
BINANCE_API_KEY=your_actual_key
BINANCE_API_SECRET=your_actual_secret
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
OPENAI_API_KEY=your_openai_key
```

### 3. Docker Container'ları Başlat

```bash
docker-compose up -d
```

### 4. Logları Kontrol Et

```bash
docker-compose logs -f trading-bot
```

### 5. API'yi Test Et

```bash
curl http://localhost:8000/health
```

## Manuel Kurulum (Docker Olmadan)

### 1. Python Kurulumu

Python 3.11 veya üzeri gerekli.

```bash
python --version
# Python 3.11.x olmalı
```

### 2. Virtual Environment Oluştur

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# Linux/Mac
source venv/bin/activate
```

### 3. Bağımlılıkları Yükle

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Environment Variables

```bash
cp env.example .env
# .env dosyasını düzenle
```

### 5. Veritabanını Başlat

SQLite kullanıyorsan (varsayılan), otomatik oluşur.

PostgreSQL kullanacaksan:

```bash
# PostgreSQL kur
sudo apt-get install postgresql

# Database oluştur
sudo -u postgres createdb tradingbot
sudo -u postgres createuser trading -P

# DATABASE_URL'i .env'de güncelle
DATABASE_URL=postgresql://trading:password@localhost:5432/tradingbot
```

### 6. Botu Çalıştır

```bash
python src/main.py
```

## Testnet'te Test Et

**ÖNEMLİ:** Gerçek para ile test etmeyin!

### 1. Binance Futures Testnet

1. https://testnet.binancefuture.com adresine git
2. Hesap oluştur ve API anahtarlarını al
3. `.env` dosyasını güncelle:

```env
BINANCE_BASE_URL=https://testnet.binancefuture.com
BINANCE_API_KEY=testnet_key
BINANCE_API_SECRET=testnet_secret
```

### 2. Test USDT Al

Testnet hesabına otomatik olarak 100,000 USDT verilir.

### 3. Test Sinyali Gönder

Telegram bot'una şu mesajı gönder:

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

## API Endpoint'leri Test Et

### Health Check

```bash
curl http://localhost:8000/health
```

### Açık Pozisyonlar

```bash
curl http://localhost:8000/positions
```

### İstatistikler

```bash
curl http://localhost:8000/stats
```

### Manuel Sinyal

```bash
curl -X POST "http://localhost:8000/signal?message=YOUR_SIGNAL_HERE"
```

## Docker Komutları

### Logları İzle

```bash
docker-compose logs -f trading-bot
```

### Container'ı Yeniden Başlat

```bash
docker-compose restart trading-bot
```

### Container'ları Durdur

```bash
docker-compose down
```

### Verileri Sil (Dikkat!)

```bash
docker-compose down -v
```

### İmajı Yeniden Oluştur

```bash
docker-compose up -d --build
```

## Sorun Giderme

### Bot Çalışmıyor

1. Logları kontrol et:

```bash
tail -f logs/bot.log
tail -f logs/errors.log
```

2. Environment variables doğru mu?

```bash
cat .env
```

3. Port kullanımda mı?

```bash
# Linux/Mac
lsof -i :8000

# Windows
netstat -ano | findstr :8000
```

### Database Hatası

```bash
# SQLite dosyasını sil ve yeniden başlat
rm tradingbot.db
python src/main.py
```

### Binance API Hatası

- API anahtarlarını kontrol et
- IP whitelist'e eklenmiş mi?
- Futures trading aktif mi?
- Testnet URL'si doğru mu?

### Telegram Bot Cevap Vermiyor

- Bot token doğru mu?
- Chat ID doğru mu?
- Bot kanala eklenmiş mi?
- Bot'un admin yetkisi var mı?

### OpenAI API Hatası

- API key geçerli mi?
- Kredi var mı?
- Model ismi doğru mu? (gpt-4o)

## Güvenlik Önerileri

1. ✅ **Testnet'te test et!**
2. ✅ API anahtarlarına IP kısıtlaması koy
3. ✅ Withdrawal iznini kapat
4. ✅ `.env` dosyasını git'e ekleme
5. ✅ Küçük miktarlarla başla
6. ✅ Logları sürekli izle

## Production Deployment

### 1. Güvenlik

```bash
# Güvenli şifreler oluştur
openssl rand -hex 32  # JWT_SECRET için
openssl rand -hex 16  # API_KEY için
```

### 2. Environment

```env
APP_ENV=production
DEBUG=false
LOG_LEVEL=INFO
```

### 3. Database Backup

```bash
# PostgreSQL backup
pg_dump tradingbot > backup_$(date +%Y%m%d).sql

# SQLite backup
cp tradingbot.db backup_$(date +%Y%m%d).db
```

### 4. Monitoring

- Prometheus metrics: http://localhost:9090
- Logs: `logs/` dizini
- Database admin: http://localhost:8080 (Adminer)

### 5. Auto-restart (Systemd)

```bash
sudo nano /etc/systemd/system/trading-bot.service
```

```ini
[Unit]
Description=VIP Trading Bot
After=network.target

[Service]
Type=simple
User=trading
WorkingDirectory=/opt/trading-bot
ExecStart=/opt/trading-bot/venv/bin/python /opt/trading-bot/src/main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable trading-bot
sudo systemctl start trading-bot
sudo systemctl status trading-bot
```

## Yardım

Sorun yaşıyorsan:

1. `logs/errors.log` dosyasını kontrol et
2. GitHub Issues'a bak
3. Telegram destek grubuna sor

## İletişim

- GitHub: [Repository Link]
- Telegram: [Support Group]
- Email: [Support Email]

