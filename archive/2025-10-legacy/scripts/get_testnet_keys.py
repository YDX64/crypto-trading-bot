#!/usr/bin/env python3
"""
Binance Testnet API Key Alma ve Yapılandırma Script'i
Otomatik olarak tarayıcı açar ve adım adım yönlendirir
"""

import webbrowser
import time
import os
from colorama import init, Fore, Style

# Initialize colorama
init(autoreset=True)


def print_header():
    """Print header"""
    print(f"\n{Fore.CYAN}{'='*60}")
    print(f"🔑 BINANCE TESTNET API KEY KURULUM ASISTANI")
    print(f"{'='*60}{Style.RESET_ALL}\n")


def print_step(step_num, title):
    """Print step header"""
    print(f"\n{Fore.YELLOW}ADIM {step_num}: {title}")
    print(f"{'-'*50}{Style.RESET_ALL}")


def wait_for_user():
    """Wait for user confirmation"""
    input(f"{Fore.GREEN}✓ Tamamladıktan sonra Enter'a basın...{Style.RESET_ALL}")


def main():
    print_header()

    print(f"{Fore.CYAN}Bu script size Binance Testnet API key'lerini")
    print(f"almanızda yardımcı olacak ve otomatik olarak")
    print(f"sisteme entegre edecektir.{Style.RESET_ALL}\n")

    # ADIM 1: Testnet sayfasını aç
    print_step(1, "TESTNET SAYFASINI AÇ")
    print(f"Tarayıcınızda Binance Futures Testnet açılacak...")
    print(f"{Fore.YELLOW}NOT: Eğer hesabınız yoksa 'Register' butonuna tıklayın{Style.RESET_ALL}")
    time.sleep(2)

    testnet_url = "https://testnet.binancefuture.com/en/futures/BTCUSDT"
    webbrowser.open(testnet_url)
    wait_for_user()

    # ADIM 2: Login/Register
    print_step(2, "GİRİŞ YAP VEYA KAYIT OL")
    print(f"1. Eğer hesabınız VARSA: Email ve şifre ile giriş yapın")
    print(f"2. Eğer hesabınız YOKSA: 'Register' butonuna tıklayın")
    print(f"   - Email adresi girin")
    print(f"   - Şifre oluşturun")
    print(f"   - 'Register' butonuna tıklayın")
    print(f"\n{Fore.GREEN}✅ Giriş yaptıktan sonra devam edin{Style.RESET_ALL}")
    wait_for_user()

    # ADIM 3: API Management sayfasına git
    print_step(3, "API MANAGEMENT SAYFASINA GİT")
    print(f"Sayfanın üst kısmında şunları yapın:")
    print(f"1. Sağ üstte profil ikonuna tıklayın")
    print(f"2. 'API Management' seçeneğine tıklayın")
    print(f"\nAlternatif olarak direkt bu linke gidin:")

    api_url = "https://testnet.binancefuture.com/en/futures/BTCUSDT"
    print(f"{Fore.BLUE}{api_url}{Style.RESET_ALL}")
    print(f"\n{Fore.YELLOW}NOT: API Management bölümü genelde sayfanın")
    print(f"alt kısmında da bulunabilir. Aşağı kaydırın.{Style.RESET_ALL}")
    wait_for_user()

    # ADIM 4: API Key oluştur
    print_step(4, "YENİ API KEY OLUŞTUR")
    print(f"API Management sayfasında:")
    print(f"1. {Fore.CYAN}'Create API' veya 'Generate API Key'{Style.RESET_ALL} butonuna tıklayın")
    print(f"2. API Label (isim) girin: örn. 'TradingBot'")
    print(f"3. {Fore.YELLOW}'Create' veya 'Generate'{Style.RESET_ALL} butonuna tıklayın")
    wait_for_user()

    # ADIM 5: API Key ve Secret'ı kaydet
    print_step(5, "API KEY VE SECRET'I KOPYALA")
    print(f"{Fore.RED}⚠️ ÇOK ÖNEMLİ: Secret Key sadece BİR KEZ gösterilir!{Style.RESET_ALL}")
    print(f"\nGörünen bilgileri buraya yapıştırın:\n")

    api_key = input(f"{Fore.CYAN}API Key: {Style.RESET_ALL}").strip()
    api_secret = input(f"{Fore.CYAN}Secret Key: {Style.RESET_ALL}").strip()

    if not api_key or not api_secret:
        print(f"{Fore.RED}❌ API Key veya Secret boş olamaz!{Style.RESET_ALL}")
        return

    # ADIM 6: API izinlerini kontrol et
    print_step(6, "API İZİNLERİNİ KONTROL ET")
    print(f"API Management sayfasında oluşturduğunuz API'nin yanında:")
    print(f"1. 'Edit restrictions' veya 'Manage' butonuna tıklayın")
    print(f"2. Şu izinlerin aktif olduğundan emin olun:")
    print(f"   {Fore.GREEN}✓ Enable Reading{Style.RESET_ALL}")
    print(f"   {Fore.GREEN}✓ Enable Spot & Margin Trading{Style.RESET_ALL}")
    print(f"   {Fore.GREEN}✓ Enable Futures{Style.RESET_ALL}")
    print(f"3. 'Save' veya 'Update' butonuna tıklayın")
    wait_for_user()

    # ADIM 7: .env dosyasını güncelle
    print_step(7, ".ENV DOSYASINI GÜNCELLE")
    print(f"API key'leriniz .env dosyasına yazılıyor...")

    env_file = ".env"
    env_backup = ".env.backup"

    # Backup oluştur
    if os.path.exists(env_file):
        with open(env_file, 'r') as f:
            content = f.read()
        with open(env_backup, 'w') as f:
            f.write(content)
        print(f"{Fore.GREEN}✅ Backup oluşturuldu: {env_backup}{Style.RESET_ALL}")

    # .env dosyasını güncelle
    try:
        # Mevcut .env'yi oku
        env_lines = []
        if os.path.exists(env_file):
            with open(env_file, 'r') as f:
                env_lines = f.readlines()

        # API key'leri güncelle veya ekle
        api_key_found = False
        api_secret_found = False
        base_url_found = False

        new_lines = []
        for line in env_lines:
            if line.startswith('BINANCE_API_KEY='):
                new_lines.append(f'BINANCE_API_KEY={api_key}\n')
                api_key_found = True
            elif line.startswith('BINANCE_API_SECRET='):
                new_lines.append(f'BINANCE_API_SECRET={api_secret}\n')
                api_secret_found = True
            elif line.startswith('BINANCE_BASE_URL='):
                new_lines.append('BINANCE_BASE_URL=https://testnet.binancefuture.com\n')
                base_url_found = True
            else:
                new_lines.append(line)

        # Eksik olanları ekle
        if not api_key_found:
            new_lines.append(f'BINANCE_API_KEY={api_key}\n')
        if not api_secret_found:
            new_lines.append(f'BINANCE_API_SECRET={api_secret}\n')
        if not base_url_found:
            new_lines.append('BINANCE_BASE_URL=https://testnet.binancefuture.com\n')

        # Dosyaya yaz
        with open(env_file, 'w') as f:
            f.writelines(new_lines)

        print(f"{Fore.GREEN}✅ .env dosyası başarıyla güncellendi!{Style.RESET_ALL}")

    except Exception as e:
        print(f"{Fore.RED}❌ .env güncelleme hatası: {e}{Style.RESET_ALL}")
        return

    # ADIM 8: Test et
    print_step(8, "BAĞLANTIYI TEST ET")
    print(f"API key'lerinizi test ediyorum...")

    # Test script'ini çalıştır
    os.system("python3 -c \"" + """
import asyncio
from src.trading.binance_testnet_client import BinanceFuturesTestnetClient

async def test():
    client = BinanceFuturesTestnetClient()
    success = await client.test_connection()
    if success:
        balance = await client.get_balance()
        print(f'✅ Bağlantı başarılı! Bakiye: {balance} USDT')
    else:
        print('❌ Bağlantı başarısız!')
    await client.close()
    return success

success = asyncio.run(test())
exit(0 if success else 1)
""" + "\" 2>/dev/null")

    # Final
    print(f"\n{Fore.CYAN}{'='*60}")
    print(f"🎉 KURULUM TAMAMLANDI!")
    print(f"{'='*60}{Style.RESET_ALL}\n")

    print(f"{Fore.GREEN}API Key'leriniz başarıyla sisteme entegre edildi!{Style.RESET_ALL}")
    print(f"\nŞimdi yapabilecekleriniz:")
    print(f"1. {Fore.CYAN}python3 test_binance_testnet.py{Style.RESET_ALL} - Sistemi test edin")
    print(f"2. {Fore.CYAN}python3 api_server_simple.py{Style.RESET_ALL} - Dashboard'u başlatın")
    print(f"3. {Fore.CYAN}http://localhost:8000{Style.RESET_ALL} - Dashboard'a erişin")

    print(f"\n{Fore.YELLOW}💰 Testnet hesabınızda 100,000 USDT sanal bakiye var!{Style.RESET_ALL}")
    print(f"{Fore.YELLOW}📊 Bu tamamen sanal paradır, gerçek değildir.{Style.RESET_ALL}")
    print(f"{Fore.YELLOW}🔄 Testnet periyodik olarak sıfırlanır.{Style.RESET_ALL}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}İşlem iptal edildi{Style.RESET_ALL}")
    except Exception as e:
        print(f"\n{Fore.RED}Hata: {e}{Style.RESET_ALL}")