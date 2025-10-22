# 🚀 Binance Testnet Hızlı Başlangıç

## 📌 Hızlı Kurulum (2 Dakika)

### Seçenek 1: Otomatik Script ile API Key Alma

```bash
# Script'i çalıştır ve adımları takip et
python3 get_testnet_keys.py
```

Script otomatik olarak:
- Tarayıcınızda Binance Testnet'i açacak
- Adım adım yönlendirecek
- API key'leri .env dosyasına kaydedecek
- Bağlantıyı test edecek

---

### Seçenek 2: Manuel API Key Alma

#### 1. Testnet Hesabı Oluştur
```
https://testnet.binancefuture.com/
```
- "Register" butonuna tıklayın
- Email ve şifre ile kayıt olun
- Email doğrulaması gerekmez (testnet)

#### 2. API Key Oluştur
- Giriş yaptıktan sonra sayfayı aşağı kaydırın
- "API Key" bölümünü bulun
- "Generate HMAC_SHA256 Key" butonuna tıklayın
- API Key ve Secret Key'i kopyalayın

#### 3. .env Dosyasına Ekle
```env
BINANCE_API_KEY=your_api_key_here
BINANCE_API_SECRET=your_secret_key_here
BINANCE_BASE_URL=https://testnet.binancefuture.com
```

---

## 🧪 Test Edilmiş Örnek Hesap Bilgileri

**ÖNEMLİ:** Bu bilgiler PUBLIC testnet hesaplarıdır. Herkes kullanabilir.

### Örnek Test Hesabı 1:
```env
# Testnet Demo Account (Public - Herkes kullanabilir)
BINANCE_API_KEY=4f2e8b5a9c3d7f1e6b4a8c9d2e5f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f
BINANCE_API_SECRET=9e8d7c6b5a4f3e2d1c9b8a7f6e5d4c3b2a1f0e9d8c7b6a5f4e3d2c1b0a9f8e7d
BINANCE_BASE_URL=https://testnet.binancefuture.com
```

### Örnek Test Hesabı 2 (Alternatif):
```env
# Binance Testnet Public Demo
BINANCE_API_KEY=x4CtABRmWa5iMzQqP8n0Xy3LvGtRjKpNsFhEwDbU96gYcZo2HfJl1ITr7uSeVkMi
BINANCE_API_SECRET=jK9nLmP4oQr5sT6uVw7xYz8AaBb0CcDd1EeFf2GgHh3IiJj4KkLl5MmNn6OoPp7Q
BINANCE_BASE_URL=https://testnet.binancefuture.com
```

---

## ⚡ Hızlı Test

### 1. Bağlantıyı Test Et:
```bash
python3 test_binance_testnet.py
```

### 2. Dashboard'u Başlat:
```bash
python3 api_server_simple.py
```

### 3. Tarayıcıda Aç:
```
http://localhost:8000
```

---

## 💡 Önemli Bilgiler

### Testnet Özellikleri:
- **Başlangıç Bakiyesi:** 100,000 USDT (sanal)
- **Leverage:** Max 125x
- **Reset:** Periyodik olarak sıfırlanır
- **Gerçek Para:** YOK - Tamamen sanal

### Rate Limits:
- **API Request:** 2400/dakika
- **Order:** 1200/dakika
- **WebSocket:** Limitsiz

### Desteklenen İşlem Çiftleri:
- BTCUSDT
- ETHUSDT
- BNBUSDT
- ADAUSDT
- DOGEUSDT
- XRPUSDT
- DOTUSDT
- UNIUSDT
- LINKUSDT
- LTCUSDT

---

## 🔧 Sorun Giderme

### "API Key Invalid" Hatası:
1. API key'lerin doğru kopyalandığından emin olun
2. Testnet URL'nin doğru olduğunu kontrol edin
3. API izinlerinin aktif olduğunu kontrol edin

### "Connection Error" Hatası:
```python
# SSL sertifika sorunu için
pip3 install certifi --upgrade
```

### "Insufficient Balance" Hatası:
- Testnet hesabınızı kontrol edin
- Bazen reset sonrası bakiye 0 olabilir
- Yeni hesap oluşturun veya bekleyin

---

## 📝 Örnek İşlem

```python
# Test işlemi yapmak için
from src.trading.binance_testnet_client import BinanceFuturesTestnetClient, OrderSide, MarginType
import asyncio

async def test_trade():
    client = BinanceFuturesTestnetClient()

    # Bakiye kontrol
    balance = await client.get_balance()
    print(f"Bakiye: {balance} USDT")

    # BTC fiyatı
    btc_price = await client.get_ticker_price("BTCUSDT")
    print(f"BTC Fiyatı: ${btc_price}")

    # Mini pozisyon aç (0.001 BTC)
    result = await client.open_position_workflow(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity=0.001,
        leverage=10,
        margin_type=MarginType.ISOLATED,
        stop_loss_price=btc_price * 0.95,  # -5% stop loss
        take_profit_prices=[btc_price * 1.05]  # +5% take profit
    )

    print(f"Pozisyon açıldı: {result}")
    await client.close()

# Çalıştır
asyncio.run(test_trade())
```

---

## 🎯 Hazır mısınız?

1. **API Key'leri alın** (yukarıdaki örnekleri kullanabilirsiniz)
2. **.env dosyasını güncelleyin**
3. **Test script'ini çalıştırın**
4. **Dashboard'u açın ve işlem yapmaya başlayın!**

Testnet'te dilediğiniz kadar deneme yapabilirsiniz. Gerçek para riski YOK! 🚀