@echo off
REM Trading Bot Başlatma Scripti (Windows)
REM DeepSeek Reasoner v3.2 ile güçlendirilmiş

echo =========================================
echo 🤖 Trading Bot Sistemi Başlatılıyor...
echo =========================================

REM Python kontrolü
echo 📌 Python kontrolü yapılıyor...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ Python bulunamadı! Lütfen Python 3.8+ yükleyin.
    pause
    exit /b 1
)
echo ✅ Python bulundu

REM Virtual environment kontrolü
if not exist "venv" (
    echo 📦 Virtual environment oluşturuluyor...
    python -m venv venv
)

REM Virtual environment'ı aktif et
echo 🔄 Virtual environment aktif ediliyor...
call venv\Scripts\activate.bat

REM Gerekli paketleri yükle
echo 📚 Gerekli paketler kontrol ediliyor...
python -m pip install --quiet --upgrade pip

REM Requirements.txt kontrolü ve yükleme
if exist "requirements.txt" (
    python -m pip install --quiet -r requirements.txt
    echo ✅ Tüm paketler yüklendi
) else (
    echo ❌ requirements.txt bulunamadı!
    pause
    exit /b 1
)

REM .env dosyası kontrolü
if not exist ".env" (
    echo ❌ .env dosyası bulunamadı!
    echo 📝 env.example dosyasından kopyalanıyor...
    copy env.example .env
    echo ⚠️ Lütfen .env dosyasını düzenleyin ve API anahtarlarınızı girin!
    pause
    exit /b 1
)

REM Log klasörünü oluştur
if not exist "logs" (
    mkdir logs
    echo ✅ Log klasörü oluşturuldu
)

REM Database kontrolü
echo 🗄️ Database kontrol ediliyor...
if exist "tradingbot.db" (
    echo ✅ Database mevcut
) else (
    echo 📊 Yeni database oluşturuluyor...
)

echo.
echo =========================================
echo 🚀 Sistem başlatılıyor...
echo =========================================

REM API Server'ı başlat (yeni pencerede)
echo 🌐 API Server başlatılıyor...
start "API Server" /min python -m src.api_server

REM Birkaç saniye bekle
timeout /t 3 /nobreak >nul

REM Dashboard'u aç
echo 📊 Dashboard açılıyor...
start http://localhost:8000

REM Ana bot'u başlat
echo 🤖 Trading Bot başlatılıyor...
echo.
echo =========================================
echo Bot çalışıyor! Dashboard: http://localhost:8000
echo Durdurmak için: Ctrl+C veya pencereyi kapatın
echo =========================================
echo.

REM Ana bot'u çalıştır
python -m src.main

pause