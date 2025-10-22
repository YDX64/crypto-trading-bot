# 🤖 Trading Bot Kurulum ve Kullanım Kılavuzu

## 🚀 DeepSeek Reasoner v3.2 ile Güçlendirilmiş Trading Bot

Bu bot, **DeepSeek Reasoner v3.2** AI modeli kullanarak gelişmiş kripto trading sinyalleri analiz eder ve otomatik işlemler yapar.

---

## ✨ Yenilikler ve İyileştirmeler

### 🧠 AI Sistemi
- **DeepSeek Reasoner v3.2** entegrasyonu tamamlandı
- Chain-of-Thought (CoT) reasoning ile derin analiz
- 3 farklı perspektiften analiz (Teknik, Risk, Sentiment)
- Gemini fallback desteği

### 🔗 Binance Bağlantısı
- Geliştirilmiş bağlantı yönetimi
- Otomatik retry mekanizması
- Rate limit koruması
- Testnet/Mainnet desteği

### 📊 Canlı İzleme Dashboard'u
- Real-time WebSocket bağlantısı
- Pozisyon takibi
- P&L grafikleri
- AI analiz durumu
- Sistem logları

---

## 📋 Gereksinimler

- Python 3.8 veya üstü
- pip paket yöneticisi
- İnternet bağlantısı

---

## 🛠️ Kurulum

### 1. Bağımlılıkları Yükleyin

```bash
# Virtual environment oluştur
python3 -m venv venv

# Aktif et (Linux/Mac)
source venv/bin/activate

# Aktif et (Windows)
venv\Scripts\activate

# Paketleri yükle
pip install -r requirements.txt
```

### 2. API Anahtarlarını Yapılandırın

`.env` dosyasında aşağıdaki anahtarları düzenleyin:

```env
# DeepSeek AI (ANA MODEL)
DEEPSEEK_API_KEY=sk-0926d476d2eb41f3be08f37596b4d9f5
DEEPSEEK_MODEL=deepseek-reasoner
DEEPSEEK_BASE_URL=https://api.deepseek.com

# Binance (Testnet için)
BINANCE_API_KEY=your_binance_api_key
BINANCE_API_SECRET=your_binance_api_secret
BINANCE_BASE_URL=https://testnet.binancefuture.com

# Telegram
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id

# Gemini (Yedek AI)
GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-2.0-flash-exp
```

---

## 🚀 Sistemi Başlatma

### Otomatik Başlatma (Önerilen)

#### Linux/Mac:
```bash
./start_system.sh
```

#### Windows:
```cmd
start_system.bat
```

### Manuel Başlatma

1. **API Server'ı başlat:**
```bash
python -m src.api_server
```

2. **Yeni terminal açın ve ana botu başlatın:**
```bash
python -m src.main
```

3. **Dashboard'a erişin:**
```
http://localhost:8000
```

---

## 📊 Dashboard Kullanımı

### Ana Özellikler:

1. **Hesap Durumu**
   - Anlık bakiye
   - Toplam kar/zarar
   - Başarı oranı
   - Açık pozisyonlar

2. **AI Analiz Durumu**
   - DeepSeek aktif/pasif
   - Son analiz sonucu
   - Konsensüs oranı

3. **Pozisyon Takibi**
   - Aktif pozisyonlar
   - Giriş/çıkış fiyatları
   - P&L hesaplaması
   - ROI yüzdesi

4. **Kontrol Paneli**
   - Bot başlat/durdur
   - Bağlantı testi
   - Risk ayarları
   - Leverage limiti

---

## 🔧 Yapılandırma

### Risk Yönetimi

`.env` dosyasında:

```env
ACCOUNT_BALANCE=10000       # Hesap bakiyesi
RISK_PERCENTAGE=2            # İşlem başına risk %
MAX_LEVERAGE=20              # Maximum kaldıraç
MAX_POSITIONS=5              # Maksimum açık pozisyon
```

### AI Ayarları

```env
# DeepSeek rate limiting
OPENAI_RATE_LIMIT_SECONDS=3.0

# Signal analiz gecikmesi
SIGNAL_QUEUE_DELAY_SECONDS=2.0
```

---

## 🔍 Sorun Giderme

### DeepSeek Bağlantı Hatası

1. API anahtarınızın doğru olduğundan emin olun
2. DeepSeek hesap bakiyenizi kontrol edin
3. Rate limit'e takılmış olabilirsiniz, birkaç dakika bekleyin

### Binance Bağlantı Hatası

1. API anahtarı ve secret'ın doğru olduğundan emin olun
2. Futures trading izinlerini kontrol edin
3. IP whitelist ayarlarını kontrol edin
4. Testnet kullanıyorsanız URL'nin doğru olduğundan emin olun:
   - Testnet: `https://testnet.binancefuture.com`
   - Mainnet: `https://fapi.binance.com`

### Dashboard Açılmıyor

1. Port 8000'in boş olduğundan emin olun
2. Firewall ayarlarını kontrol edin
3. API server'ın çalıştığından emin olun

---

## 📝 Loglar

Tüm loglar `logs/` klasöründe saklanır:

- `trading.log` - Ana bot logları
- `api_server.log` - API server logları
- `positions.log` - Pozisyon logları

---

## ⚠️ Güvenlik Uyarıları

1. **ASLA** `.env` dosyasını paylaşmayın
2. API anahtarlarınızı güvende tutun
3. Testnet'te test edin önce
4. Küçük miktarlarla başlayın
5. Stop-loss kullanmayı unutmayın

---

## 🆘 Destek

Sorunlar için:
1. Log dosyalarını kontrol edin
2. Dashboard'daki hata mesajlarına bakın
3. Bağlantı testlerini çalıştırın

---

## 📈 Performans İpuçları

1. **AI Optimizasyonu**
   - DeepSeek'in reasoning özelliğini kullanır
   - Yoğun zamanlarda rate limit'e dikkat edin
   - Gemini fallback otomatik devreye girer

2. **Binance Optimizasyonu**
   - Testnet'te önce test edin
   - Rate limit'lere dikkat edin
   - Leverage'ı dikkatli kullanın

3. **Risk Yönetimi**
   - Maksimum %2-3 risk alın
   - Çeşitlendirme yapın
   - Trailing stop kullanın

---

## 🎯 Başlatma Komutları Özeti

```bash
# 1. Hızlı başlatma (tüm sistem)
./start_system.sh

# 2. Sadece Dashboard
python -m src.api_server
# Tarayıcıda: http://localhost:8000

# 3. Sadece Bot
python -m src.main

# 4. Bağlantı Testi
python -c "from src.trading.binance_client_improved import ImprovedBinanceClient; import asyncio; asyncio.run(ImprovedBinanceClient().test_connection())"
```

---

## 📊 Sistem Durumu Kontrol

Dashboard üzerinden canlı takip edin:
- Bot durumu (çalışıyor/durduruldu)
- Binance bağlantısı
- DeepSeek AI durumu
- Açık pozisyonlar
- P&L grafiği

---

**İyi Kazançlar! 🚀💰**