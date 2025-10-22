# Değişiklik Günlüğü

## v2.0.0 - Çoklu Sinyal Yönetimi ve Yeni Format Desteği

### 🎉 Yeni Özellikler

#### 1. Yeni Telegram Mesaj Formatı Desteği
- ✅ Emoji temizleme (`🟢`, `🔴`, `⚡️`, vb.)
- ✅ URL temizleme (`(https://...)`)
- ✅ Margin type parse (`Cross` veya `Isolated`)
- ✅ Esnek pattern matching

**Örnek Mesaj:**
```
🟢AERO/USDT (https://bingx.com/invite/KOZYQ1) LONG (buy)
Margin: Cross, 50X
ENTRY: <0.84515-0.85365>
TARGETS:
1. [0.85789] 2. [0.86639]
3. [0.87488] 4. [0.89187]
STOPLOSS: [0.81542]
```

#### 2. Profit Mesajı Filtreleme
- ✅ Otomatik profit mesajı tespiti
- ✅ Keywords: PROFIT, TARGET HIT, TP HIT, TAKE PROFIT, CLOSED, vb.
- ✅ Profit mesajları queue'ya eklenmez

#### 3. Sinyal Queue Sistemi
- ✅ Asenkron kuyruk yönetimi
- ✅ Sıralı işleme (FIFO)
- ✅ Rate limiting koruması (2 saniye aralar)
- ✅ Hata durumunda diğer sinyallere geçiş

#### 4. Rate Limiting
- ✅ OpenAI API: Minimum 3 saniye aralar
- ✅ Binance API: Minimum 0.5 saniye aralar
- ✅ Otomatik bekleme mekanizması

#### 5. Maksimum Pozisyon Limiti
- ✅ Varsayılan: 5 pozisyon
- ✅ Config'den ayarlanabilir
- ✅ Limit aşımında sinyal atlanır

#### 6. Margin Type Desteği
- ✅ Mesajdan Cross/Isolated parse
- ✅ Config fallback
- ✅ Her pozisyon için farklı margin type

### 📝 Değişen Dosyalar

#### Core
- `src/core/config.py` - Yeni parametreler eklendi
- `src/core/rate_limiter.py` - Yeni dosya

#### Models
- `src/models/signal.py` - margin_type alanı eklendi

#### Parsers
- `src/parsers/telegram_parser.py` - Yeni format desteği, profit filter

#### Services
- `src/services/signal_queue.py` - Yeni dosya
- `src/services/orchestrator.py` - Max pozisyon kontrolü
- `src/services/telegram_bot.py` - Queue entegrasyonu

#### Trading
- `src/analyzers/ai_analyzer.py` - Rate limiter entegrasyonu
- `src/trading/binance_client.py` - Rate limiter entegrasyonu
- `src/trading/position_manager.py` - Margin type desteği

#### Configuration
- `env.example` - N8n bilgileri eklendi, yeni parametreler

### 🔧 Yeni Config Parametreleri

```env
MAX_POSITIONS=5
OPENAI_RATE_LIMIT_SECONDS=3.0
BINANCE_RATE_LIMIT_SECONDS=0.5
SIGNAL_QUEUE_DELAY_SECONDS=2.0
```

### 📊 İş Akışı Değişiklikleri

**Eski:**
```
Telegram Mesaj → Parse → AI Analiz → Pozisyon Aç
```

**Yeni:**
```
Telegram Mesaj
  ↓
Profit Mesajı? → [Skip]
  ↓ (Hayır)
Queue'ya Ekle
  ↓
Sırayla İşle (Rate Limit ile)
  ↓
Parse (Yeni Format + Margin Type)
  ↓
AI Analiz (3x, Rate Limit ile)
  ↓
Max Pozisyon? → [Skip]
  ↓ (Hayır)
Pozisyon Aç (Margin Type ile)
```

### 🧪 Test Senaryosu

1. **10 Sinyal Gönder:**
   - 5'i profit mesajı → Filtrelenir
   - 5'i yeni sinyal → Queue'ya eklenir

2. **Queue İşleme:**
   - Sırayla işlenir (2s aralarla)
   - AI analizi yapılır (3s aralarla)
   - Rate limit korunur

3. **Pozisyon Limiti:**
   - En fazla 5 pozisyon açılır
   - 6. sinyal atlanır (log'da uyarı)

### 🚀 Kullanım

#### Queue Durumu Kontrol

```bash
curl http://localhost:8000/stats
```

Yanıt:
```json
{
  "active_positions": 3,
  "max_positions": 5,
  "queue_size": 2
}
```

#### Manuel Sinyal (Queue'ya Ekler)

```bash
curl -X POST "http://localhost:8000/signal" \
  -H "Content-Type: application/json" \
  -d '{"message": "🟢BTC/USDT LONG\nMargin: Cross, 10X\n..."}'
```

### ⚠️ Breaking Changes

- Telegram mesajları artık direkt işlenmez, queue'ya eklenir
- Profit mesajları otomatik filtrelenir
- Max 5 pozisyon limiti vardır
- Rate limiting nedeniyle işlemler daha yavaştır (güvenlik için)

### 📚 Yeni Komutlar

Telegram Bot:
- `/status` - Queue ve pozisyon durumu gösterir

### 🐛 Düzeltilen Hatalar

- N8n workflow'larında signature hatası (Date.now() iki kez çağrılıyordu)
- Emoji ve URL'ler parse edilmiyordu
- Çoklu sinyal aynı anda geldiğinde API rate limit aşımı
- Margin type mesajdan parse edilmiyordu

### 🔒 Güvenlik

- API rate limiting ile hesap koruması
- Max pozisyon limiti ile risk yönetimi
- Queue ile kontrollü işlem akışı

### 📖 Dokümantasyon

- README.md - Güncel
- USAGE.md - Güncel
- QUICKSTART.md - Güncel
- Bu dosya (CHANGELOG.md) - Yeni

### 🎯 Sonraki Adımlar

1. Testnet'te test edin
2. 10-15 sinyal gönderin
3. Queue ve rate limiting'i gözlemleyin
4. Log dosyalarını kontrol edin
5. Production'a geçmeden önce en az 1 hafta test edin

### 💡 İpuçları

- `logs/bot.log` - Genel işlem akışı
- `logs/trades.log` - Sadece trade logları
- `logs/errors.log` - Hatalar

### 📞 Destek

Sorun yaşarsanız:
1. Logları kontrol edin
2. GitHub Issues açın
3. Telegram destek grubuna yazın

