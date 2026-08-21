#!/usr/bin/env python3
"""
Sistem Test Script
Tüm bileşenlerin çalıştığını kontrol eder
"""

import asyncio
import sys
from colorama import init, Fore, Style

# Colorama'yı başlat
init(autoreset=True)

async def test_deepseek():
    """DeepSeek AI bağlantısını test et"""
    print(f"{Fore.YELLOW}🧠 DeepSeek AI test ediliyor...{Style.RESET_ALL}")
    try:
        from openai import AsyncOpenAI
        from src.core.config import settings

        client = AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url
        )

        response = await client.chat.completions.create(
            model=settings.deepseek_model,
            messages=[
                {"role": "user", "content": "Say 'DeepSeek is working!' in 5 words or less"}
            ],
            max_tokens=20
        )

        result = response.choices[0].message.content
        print(f"{Fore.GREEN}✅ DeepSeek çalışıyor: {result}{Style.RESET_ALL}")
        return True

    except Exception as e:
        print(f"{Fore.RED}❌ DeepSeek hatası: {e}{Style.RESET_ALL}")
        return False


async def test_binance():
    """Binance bağlantısını test et"""
    print(f"{Fore.YELLOW}💱 Binance bağlantısı test ediliyor...{Style.RESET_ALL}")
    try:
        from src.trading.binance_client_improved import ImprovedBinanceClient

        client = ImprovedBinanceClient()
        success = await client.test_connection()

        if success:
            balance = await client.get_account_balance()
            print(f"{Fore.GREEN}✅ Binance bağlantısı başarılı")
            print(f"   💰 Bakiye: {balance:.2f} USDT{Style.RESET_ALL}")
        else:
            print(f"{Fore.RED}❌ Binance bağlantısı başarısız{Style.RESET_ALL}")

        await client.close()
        return success

    except Exception as e:
        print(f"{Fore.RED}❌ Binance hatası: {e}{Style.RESET_ALL}")
        return False


async def test_telegram():
    """Telegram bağlantısını test et"""
    print(f"{Fore.YELLOW}📱 Telegram bağlantısı test ediliyor...{Style.RESET_ALL}")
    try:
        from src.core.config import settings

        if settings.telegram_bot_token and settings.telegram_chat_id:
            print(f"{Fore.GREEN}✅ Telegram yapılandırması mevcut")
            print(f"   Bot Token: {settings.telegram_bot_token[:20]}...")
            print(f"   Chat ID: {settings.telegram_chat_id}{Style.RESET_ALL}")
            return True
        else:
            print(f"{Fore.RED}❌ Telegram yapılandırması eksik{Style.RESET_ALL}")
            return False

    except Exception as e:
        print(f"{Fore.RED}❌ Telegram hatası: {e}{Style.RESET_ALL}")
        return False


async def test_database():
    """Database bağlantısını test et"""
    print(f"{Fore.YELLOW}🗄️ Database bağlantısı test ediliyor...{Style.RESET_ALL}")
    try:
        from src.core.database import DatabaseManager

        db = DatabaseManager()
        await db.init_db()

        async with db.get_session() as session:
            # Basit bir sorgu yap
            result = await session.execute("SELECT 1")
            if result:
                print(f"{Fore.GREEN}✅ Database bağlantısı başarılı{Style.RESET_ALL}")
                return True

    except Exception as e:
        print(f"{Fore.RED}❌ Database hatası: {e}{Style.RESET_ALL}")
        return False


async def test_signal_parser():
    """Sinyal parser'ı test et"""
    print(f"{Fore.YELLOW}📝 Sinyal parser test ediliyor...{Style.RESET_ALL}")
    try:
        from src.parsers.telegram_parser import TelegramSignalParser

        parser = TelegramSignalParser()

        # Test sinyali
        test_signal = """
        📈 BTC/USDT LONG

        Entry: 42000-42500
        Targets: 43000, 44000, 45000
        Stop Loss: 41000
        Leverage: 10x
        """

        parsed = parser.parse_signal(test_signal)

        if parsed:
            print(f"{Fore.GREEN}✅ Parser çalışıyor")
            print(f"   Coin: {parsed.coin}")
            print(f"   Direction: {parsed.direction}")
            print(f"   Entry: {parsed.entry}")
            print(f"   Leverage: {parsed.leverage}x{Style.RESET_ALL}")
            return True
        else:
            print(f"{Fore.RED}❌ Sinyal parse edilemedi{Style.RESET_ALL}")
            return False

    except Exception as e:
        print(f"{Fore.RED}❌ Parser hatası: {e}{Style.RESET_ALL}")
        return False


async def main():
    """Ana test fonksiyonu"""
    print(f"\n{Fore.CYAN}{'='*60}")
    print(f"🤖 TRADING BOT SİSTEM TESTİ")
    print(f"{'='*60}{Style.RESET_ALL}\n")

    results = []

    # Testleri çalıştır
    results.append(("Database", await test_database()))
    results.append(("DeepSeek AI", await test_deepseek()))
    results.append(("Binance", await test_binance()))
    results.append(("Telegram", await test_telegram()))
    results.append(("Signal Parser", await test_signal_parser()))

    # Sonuçları özetle
    print(f"\n{Fore.CYAN}{'='*60}")
    print(f"📊 TEST SONUÇLARI")
    print(f"{'='*60}{Style.RESET_ALL}\n")

    success_count = 0
    for name, success in results:
        if success:
            print(f"{Fore.GREEN}✅ {name}: BAŞARILI{Style.RESET_ALL}")
            success_count += 1
        else:
            print(f"{Fore.RED}❌ {name}: BAŞARISIZ{Style.RESET_ALL}")

    # Genel değerlendirme
    print(f"\n{Fore.CYAN}{'='*60}{Style.RESET_ALL}")

    if success_count == len(results):
        print(f"{Fore.GREEN}🎉 TÜM TESTLER BAŞARILI! Sistem hazır.{Style.RESET_ALL}")
        return 0
    elif success_count >= 3:
        print(f"{Fore.YELLOW}⚠️ Bazı testler başarısız. Sistem kısıtlı çalışabilir.{Style.RESET_ALL}")
        return 1
    else:
        print(f"{Fore.RED}❌ Kritik hatalar var. Lütfen yapılandırmayı kontrol edin.{Style.RESET_ALL}")
        return 2


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}Test iptal edildi.{Style.RESET_ALL}")
        sys.exit(1)
    except Exception as e:
        print(f"{Fore.RED}Test hatası: {e}{Style.RESET_ALL}")
        sys.exit(2)