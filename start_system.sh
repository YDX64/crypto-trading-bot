#!/bin/bash

# Trading Bot Başlatma Scripti
# DeepSeek Reasoner v3.2 ile güçlendirilmiş

echo "========================================="
echo "🤖 Trading Bot Sistemi Başlatılıyor..."
echo "========================================="

# Renkleri tanımla
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Python kontrolü
echo -e "${YELLOW}📌 Python kontrolü yapılıyor...${NC}"
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}❌ Python3 bulunamadı! Lütfen Python 3.8+ yükleyin.${NC}"
    exit 1
fi
echo -e "${GREEN}✅ Python3 bulundu: $(python3 --version)${NC}"

# Virtual environment kontrolü
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}📦 Virtual environment oluşturuluyor...${NC}"
    python3 -m venv venv
fi

# Virtual environment'ı aktif et
echo -e "${YELLOW}🔄 Virtual environment aktif ediliyor...${NC}"
source venv/bin/activate

# Gerekli paketleri yükle
echo -e "${YELLOW}📚 Gerekli paketler kontrol ediliyor...${NC}"
pip install --quiet --upgrade pip

# Requirements.txt kontrolü ve yükleme
if [ -f "requirements.txt" ]; then
    pip install --quiet -r requirements.txt
    echo -e "${GREEN}✅ Tüm paketler yüklendi${NC}"
else
    echo -e "${RED}❌ requirements.txt bulunamadı!${NC}"
    exit 1
fi

# .env dosyası kontrolü
if [ ! -f ".env" ]; then
    echo -e "${RED}❌ .env dosyası bulunamadı!${NC}"
    echo -e "${YELLOW}📝 env.example dosyasından kopyalanıyor...${NC}"
    cp env.example .env
    echo -e "${YELLOW}⚠️ Lütfen .env dosyasını düzenleyin ve API anahtarlarınızı girin!${NC}"
    exit 1
fi

# Log klasörünü oluştur
if [ ! -d "logs" ]; then
    mkdir logs
    echo -e "${GREEN}✅ Log klasörü oluşturuldu${NC}"
fi

# Database kontrolü
echo -e "${YELLOW}🗄️ Database kontrol ediliyor...${NC}"
if [ -f "tradingbot.db" ]; then
    echo -e "${GREEN}✅ Database mevcut${NC}"
else
    echo -e "${YELLOW}📊 Yeni database oluşturuluyor...${NC}"
fi

# Binance bağlantı testi
echo -e "${YELLOW}🔍 Binance bağlantısı test ediliyor...${NC}"
python3 -c "
import asyncio
from src.trading.binance_client_improved import ImprovedBinanceClient

async def test():
    client = ImprovedBinanceClient()
    success = await client.test_connection()
    await client.close()
    return success

result = asyncio.run(test())
exit(0 if result else 1)
" 2>/dev/null

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✅ Binance bağlantısı başarılı${NC}"
else
    echo -e "${RED}⚠️ Binance bağlantısı başarısız - Testnet kullanılacak${NC}"
fi

# DeepSeek AI testi
echo -e "${YELLOW}🧠 DeepSeek AI bağlantısı test ediliyor...${NC}"
python3 -c "
import asyncio
from openai import AsyncOpenAI
from src.core.config import settings

async def test():
    try:
        client = AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url
        )
        response = await client.chat.completions.create(
            model=settings.deepseek_model,
            messages=[{'role': 'user', 'content': 'test'}],
            max_tokens=10
        )
        return True
    except:
        return False

result = asyncio.run(test())
exit(0 if result else 1)
" 2>/dev/null

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✅ DeepSeek AI bağlantısı başarılı${NC}"
else
    echo -e "${YELLOW}⚠️ DeepSeek AI bağlanamadı - Gemini fallback kullanılacak${NC}"
fi

echo ""
echo "========================================="
echo -e "${GREEN}🚀 Sistem başlatılıyor...${NC}"
echo "========================================="

# API Server'ı başlat (arka planda)
echo -e "${YELLOW}🌐 API Server başlatılıyor...${NC}"
nohup python3 -m src.api_server > logs/api_server.log 2>&1 &
API_PID=$!
echo -e "${GREEN}✅ API Server başlatıldı (PID: $API_PID)${NC}"

# Birkaç saniye bekle
sleep 3

# Dashboard'u aç
echo -e "${YELLOW}📊 Dashboard açılıyor...${NC}"
if command -v xdg-open &> /dev/null; then
    xdg-open http://localhost:8000 2>/dev/null &
elif command -v open &> /dev/null; then
    open http://localhost:8000 2>/dev/null &
else
    echo -e "${YELLOW}📌 Dashboard'a erişmek için tarayıcıda açın: http://localhost:8000${NC}"
fi

# Ana bot'u başlat
echo -e "${YELLOW}🤖 Trading Bot başlatılıyor...${NC}"
echo ""
echo "========================================="
echo -e "${GREEN}Bot çalışıyor! Dashboard: http://localhost:8000${NC}"
echo -e "${YELLOW}Durdurmak için: Ctrl+C${NC}"
echo "========================================="
echo ""

# Ana bot'u çalıştır
python3 -m src.main

# Cleanup on exit
trap "echo -e '${YELLOW}Sistem kapatılıyor...${NC}'; kill $API_PID 2>/dev/null; exit" INT TERM