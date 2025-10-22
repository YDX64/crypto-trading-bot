# 🚀 TRADING BOT BAŞLATMA KILAVUZU

## ⚡ HIZLI BAŞLATMA (TEK KOMUT)

Terminal'i aç ve şu komutu çalıştır:
```bash
cd /Users/max/Downloads/Downloads/TRADINGBOT && python3 src/main.py
```

## 📋 DETAYLI BAŞLATMA

### 1. Terminal'de Proje Klasörüne Git
```bash
cd /Users/max/Downloads/Downloads/TRADINGBOT
```

### 2. Ana Sistemi Başlat
```bash
python3 src/main.py
```

### 3. Alternatif: Uvicorn ile Başlat (Daha Detaylı Loglar)
```bash
python3 -m uvicorn src.main:app --host 0.0.0.0 --port 8080 --reload
```

## 🖥️ KONTROL PANELLERİ

### Web Dashboard (Browser'da Aç)
```
http://localhost:8080/dashboard
```

### API Durumu Kontrol
```bash
curl http://localhost:8080/api/status
```

### Konfigürasyon Kontrol
```bash
curl http://localhost:8080/config | python3 -m json.tool
```

## 🔍 İZLEME KOMUTLARI

### 1. Basit İzleme
```bash
python3 simple_monitor.py
```

### 2. Detaylı Dashboard İzleme
```bash
python3 monitor_dashboard.py
```

### 3. Log Dosyasını Canlı İzle
```bash
tail -f trading_bot.log
```

### 4. Sadece Önemli Logları İzle
```bash
tail -f trading_bot.log | grep -E "Telegram|Parse|AI|Pozisyon"
```

## 🧪 TEST KOMUTLARI

### Test Sinyali Gönder
```bash
# LONG Sinyal Testi
curl -X POST http://localhost:8080/signal \
  -H "Content-Type: application/json" \
  -d '{
    "message": "BTC/USDT LONG\nLeverage: 10x\nEntry: 100000-101000\nTargets: 102000, 103000\nStoploss: 99000"
  }'
```

## 🛑 DURDURMA

### Sistemi Durdur
- Terminal'de `Ctrl + C` tuşlarına bas

## 📝 ÖNEMLİ NOTLAR

1. **Waiting Mode**: Şu an KAPALI (WAITING_MODE_ENABLED=false)
2. **Risk**: Her işlem için %2 risk
3. **Leverage**: Max 20x
4. **API Port**: 8080
5. **Telegram Bot**: Otomatik başlar

## 🔧 SORUN GİDERME

### Port Meşgulse (8080 kullanılıyor)
```bash
# Portu kullanan process'i bul ve kapat
lsof -i :8080
kill -9 [PID]
```

### Database Hatası Alırsan
```bash
# Database'i sıfırla
rm tradingbot.db
python3 src/main.py  # Yeniden başlat, otomatik oluşur
```

### Test Data Temizleme
```bash
python3 clear_test_data.py
```

## 📊 DURUM KONTROL

### Sistemin Çalıştığını Doğrula
```bash
# API yanıt veriyorsa sistem çalışıyor
curl http://localhost:8080/health
```

### Açık Pozisyonları Gör
```bash
curl http://localhost:8080/positions | python3 -m json.tool
```

## 🔄 YENİDEN BAŞLATMA

```bash
# 1. Terminal'de Ctrl+C ile durdur
# 2. Tekrar başlat
python3 src/main.py
```

## 📱 TELEGRAM KONTROL

Bot otomatik olarak Telegram'a bağlanır ve şu kanalı dinler:
- Channel ID: `-2367944506`
- Bot Token: Ayarlanmış durumda

---

**NOT**: Sistem başladığında şunu göreceksin:
```
🚀 TRADING BOT BAŞLATILIYOR
✅ Veritabanı hazır
✅ Telegram bot başlatıldı
✅ Trading Orchestrator başlatıldı
📊 API Server: http://0.0.0.0:8080
🤖 Telegram Bot: Aktif
```