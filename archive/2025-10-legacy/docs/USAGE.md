# 🎮 Kullanım Rehberi

## Telegram Sinyal Formatı

Bot, aşağıdaki formattaki mesajları otomatik algılar:

### Standart Format

```
BTC/USDT LONG
MARGIN: 10X
ENTRY: <45000-46000>
TARGETS:
1. [47000]
2. [48000]
3. [49000]
4. [50000]
STOPLOSS: [44000]
```

### Kısa Format

```
ETH/USDT SHORT
MARGIN: 5X
ENTRY: <3000-3100>
TARGETS:
1. [2800]
2. [2600]
STOPLOSS: [3200]
```

## İş Akışı

### 1. Sinyal Alındığında

Bot şunları yapar:

1. ✅ Mesajı parse eder
2. 🤖 3 farklı AI analiziyle değerlendirir
3. 📊 Trend uyumluluğunu kontrol eder
4. ✅/❌ Onaylar veya reddeder

### 2. Onaylanırsa

1. 💰 Pozisyon hesaplanır (bakiyenin %10'u)
2. ⚡ Leverage ayarlanır
3. 🚀 Market order açılır
4. 🛡️ Stop loss konur
5. 🎯 İlk TP konur (%25)

### 3. İlk TP Vurduğunda

1. 🎯 İlk TP tetiklenir (%25 kar al)
2. 🔄 Stop loss break-even'e taşınır
3. 🔒 Artık zarar riski YOK
4. 📈 Trailing mekanizma aktif olur

### 4. Trailing Modunda

Bot her 30 saniyede bir:

1. 💹 Güncel fiyatı kontrol eder
2. 📈 Fiyat yükselirse SL'yi yukarı çeker
3. 📉 Fiyat düşerse SL sabit kalır
4. 🔄 Sürekli güncelleme yapar

### 5. Pozisyon Kapanınca

1. 📊 P&L hesaplanır
2. 🏁 Veritabanına kaydedilir
3. 📢 Bildirim gönderilir

## Telegram Bot Komutları

### `/start`

Bot'u başlatır ve özellikleri gösterir.

```
/start
```

Yanıt:
```
🤖 VIP Trading Bot Aktif

✅ Sinyaller otomatik işleniyor
✅ AI analizi aktif
✅ Trailing SL/TP aktif
✅ Break-even mekanizması aktif
```

### `/status`

Bot durumunu gösterir.

```
/status
```

Yanıt:
```
📊 Bot Durumu

🟢 Aktif
📈 Açık Pozisyon: 2
💰 Hesap: 10000 USDT
⚡ Risk: %10
🎯 İlk TP: %25
🔄 Trailing SL: %1.5
```

### `/positions`

Açık pozisyonları listeler.

```
/positions
```

Yanıt:
```
📊 Açık Pozisyonlar

BTCUSDT
└ Yön: LONG
└ Giriş: $45234.50
└ Güncel SL: $45234.50
└ Durum: BREAK_EVEN
└ P&L: 📈 3.45%
└ Break-Even: ✅
└ Trailing: ✅

ETHUSDT
└ Yön: SHORT
└ Giriş: $3045.20
└ Güncel SL: $3045.20
└ Durum: TRAILING
└ P&L: 📈 2.12%
└ Break-Even: ✅
└ Trailing: ✅
```

### `/stats`

İstatistikleri gösterir.

```
/stats
```

## API Kullanımı

### Sağlık Kontrolü

```bash
curl http://localhost:8000/health
```

Yanıt:
```json
{
  "status": "healthy",
  "telegram_bot": "running",
  "database": "connected"
}
```

### Açık Pozisyonları Listele

```bash
curl http://localhost:8000/positions
```

Yanıt:
```json
{
  "count": 2,
  "positions": [
    {
      "symbol": "BTCUSDT",
      "side": "LONG",
      "entry_price": 45234.50,
      "current_price": 46789.30,
      "quantity": 0.0442,
      "leverage": 10,
      "current_stoploss": 45234.50,
      "status": "BREAK_EVEN",
      "is_break_even": true,
      "is_trailing": true,
      "unrealized_pnl": 68.72,
      "pnl_percentage": 3.44
    }
  ]
}
```

### Manuel Sinyal Gönder

```bash
curl -X POST "http://localhost:8000/signal" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "BTC/USDT LONG\nMARGIN: 10X\nENTRY: <45000-46000>\nTARGETS:\n1. [47000]\n2. [48000]\nSTOPLOSS: [44000]"
  }'
```

### İstatistikler

```bash
curl http://localhost:8000/stats
```

### Konfigürasyon

```bash
curl http://localhost:8000/config
```

## Risk Yönetimi

### Otomatik Hesaplamalar

#### Pozisyon Büyüklüğü

```
Bakiye: 10,000 USDT
Risk: %10
Pozisyon Büyüklüğü: 1,000 USDT
```

#### Quantity

```
Entry: 45,500 USDT
Pozisyon: 1,000 USDT
Quantity: 0.02198 BTC
```

#### İlk TP Quantity

```
Total Quantity: 0.02198 BTC
İlk TP %25: 0.00549 BTC
Kalan: 0.01648 BTC
```

### Risk Seviyeleri

1. **Minimum Risk**: Sinyal reddedilir
   - AI konsensüsü yok
   - Trend uyumsuz

2. **Kontrollü Risk**: Pozisyon açılır
   - AI onayı var
   - Trend uyumlu
   - Stop loss aktif

3. **Sıfır Risk**: Break-even sonrası
   - İlk TP vurdu
   - SL break-even'de
   - Kar garantili

4. **Maksimum Kar**: Trailing aktif
   - Fiyat yükselirse SL yükselir
   - Kar artar, risk artmaz

## Bildirimler

Bot şu durumlarda bildirim gönderir:

### 1. Pozisyon Açıldı

```
✅ Pozisyon Açıldı

Coin: BTCUSDT
Yön: LONG
Leverage: 10x
Giriş: $45234.50
Miktar: 0.0442
Stop Loss: $44000.00
İlk TP: $47000.00 (%25)

🔄 Trailing aktif olacak!
```

### 2. İlk TP Vurdu

```
🎯 İlk TP Vurdu!

Coin: BTCUSDT
Yön: LONG
Giriş: $45234.50
TP: $47000.00
Kar: %3.90

🔄 SL break-even'e taşındı!
```

### 3. Trailing Güncellendi

```
📈 Trailing SL Güncellendi

Coin: BTCUSDT
Eski SL: $45234.50
Yeni SL: $46500.00
P&L: +5.23%
```

### 4. Pozisyon Kapandı

```
🏁 Pozisyon Kapandı

Coin: BTCUSDT
Yön: LONG
Giriş: $45234.50
Çıkış: $48123.40
Kar: %6.38
Süre: 4h 23m
```

## Önemli Notlar

### ⚠️ Dikkat Edilmesi Gerekenler

1. **Sinyal Kalitesi**
   - Sadece güvenilir kaynaklardan sinyal al
   - AI analizi red ederse açma
   - Manuel müdahale etme

2. **Bakiye Yönetimi**
   - Her işlem bakiyenin %10'u
   - Maksimum 3-4 pozisyon aç
   - Likidite koru

3. **Leverage Kullanımı**
   - Yüksek leverage = yüksek risk
   - 5-10x ideal
   - 20x'in üstüne çıkma

4. **Stop Loss**
   - Asla manuel kaldırma
   - Break-even'den önce dokunma
   - Trailing'e güven

5. **Take Profit**
   - İlk TP %25 sabit
   - Trailing mekanizması otomatik
   - Erken çıkma

### ✅ En İyi Pratikler

1. **Test Et**
   - Önce testnet'te dene
   - Küçük miktarlarla başla
   - Stratejini optimize et

2. **İzle**
   - Logları kontrol et
   - Performansı ölç
   - Ayarları güncelle

3. **Öğren**
   - Hangi sinyaller kar etti?
   - AI hangi durumlarda red etti?
   - Trailing nasıl çalışıyor?

4. **Güvenlik**
   - API anahtarlarını koru
   - IP whitelist kullan
   - Withdrawal kapat

## Örnek Senaryolar

### Senaryo 1: Başarılı Trade

```
1. 📨 Sinyal: BTC/USDT LONG @ 45000
2. 🤖 AI: 3/3 BULLISH
3. ✅ Trend Uyumlu
4. 🚀 Pozisyon Açıldı: 0.0222 BTC
5. 🎯 İlk TP Vurdu: 47000 (+4.4%)
6. 🔄 SL Break-Even: 45000
7. 📈 Trailing Aktif
8. 📊 Fiyat: 48500 → SL: 47800
9. 📊 Fiyat: 49200 → SL: 48500
10. 🏁 SL Vurdu: 48500 (+7.8% Kar)
```

### Senaryo 2: Stop Loss

```
1. 📨 Sinyal: ETH/USDT SHORT @ 3000
2. 🤖 AI: 2/3 BEARISH
3. ✅ Trend Uyumlu
4. 🚀 Pozisyon Açıldı: 0.6667 ETH
5. 📉 Fiyat düştü: 2850
6. 🎯 İlk TP Vurdu: 2800 (+6.7%)
7. 🔄 SL Break-Even: 3000
8. 📈 Fiyat yükseldi: 3020
9. 🛡️ SL Tetiklendi: 3000 (Break-Even)
10. 🏁 Zarar: 0% ✅
```

### Senaryo 3: AI Red

```
1. 📨 Sinyal: XRP/USDT LONG @ 0.50
2. 🤖 AI: 0/3 BULLISH, 3/3 BEARISH
3. ❌ Trend Uyumsuz
4. 🚫 Pozisyon Açılmadı
5. 📊 Fiyat düştü: 0.42 (-16%)
6. ✅ AI kurtardı!
```

## Yardım

Sorunuz mu var?

1. `logs/bot.log` - Genel loglar
2. `logs/trades.log` - Trade logları
3. `logs/errors.log` - Hata logları
4. GitHub Issues
5. Telegram Destek Grubu

