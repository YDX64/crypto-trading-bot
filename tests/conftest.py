"""tests/ ortak fixture'ları.

Neden: `MarketDataGuard` (D17) süreç-geneli SINIF düzeyi durum tutar — host
başına ban zamanı, ağırlık penceresi ve bir `asyncio.Lock`. Bu durum testler
arasında sızarsa iki ayrı sorun doğar:

1. Bir testte kurulan ban, sonraki testte ilgisiz bir çekimi engeller.
2. `asyncio.Lock` ilk ÇEKİŞMEDE o anki event loop'a bağlanır; pytest-asyncio
   her testte YENİ bir loop kurduğu için sızan bir kilit ileride
   "is bound to a different event loop" `RuntimeError`'ı üretebilir — ve bu
   `RuntimeError`, `MarketDataUnavailable` de bir `RuntimeError` olduğu için
   yanlış sınıflandırılabilir.

Bu yüzden guard durumu HER testten önce ve sonra sıfırlanır. Ücreti bir sözlük
ataması; faydası gelecekteki testlerin bu tuzağa hiç düşmemesi.
"""

import os

import pytest

from src.core.config import settings
from src.strategies.scalper.data import MarketDataGuard
from src.trading.binance_client_improved import ImprovedBinanceClient


@pytest.fixture(autouse=True)
def _reset_market_data_guard():
    MarketDataGuard.reset()
    yield
    MarketDataGuard.reset()


@pytest.fixture(autouse=True)
def _reset_rest_weight_state():
    """D22: imzalı REST ağırlık geri çekilmesi de SINIF düzeyi durumdur.

    Yukarıdakiyle aynı gerekçe: bir testte kurulan geri çekilme penceresi
    sonraki testte ilgisiz bir "background" isteğini sessizce engellerdi.
    """
    ImprovedBinanceClient.reset_weight_state()
    yield
    ImprovedBinanceClient.reset_weight_state()


#: Gömülü takipçi (D20b) ayarlarının TEST İZOLASYONU.
#:
#: NEDEN (düşmanca inceleme bulgu 29 — KRİTİK): `Settings.model_config`
#: `env_file=".env"` kullanır ve yol ÇALIŞMA DİZİNİNE görelidir. Sunucuda
#: `scripts/server_deploy.sh` testleri `/opt/tradingbot-v2` içinde koşturur →
#: pytest CANLI `.env`'i okur. Operatör `FOLLOWER_EMBEDDED=true` yazdığı an
#: gömülü mod TÜM testlerde açılır, `/tv-signal` testleri takipçiye yönlenir,
#: ilk kırmızı testte `pytest -x` durur ve deploy KODU GERİ ALIP çalışan
#: süreci yeniden başlatır. Yani bir AYAR dosyası kod kapısını kırar ve
#: gömülü mod açıldıktan sonra hiçbir değişiklik canlıya giremez.
#:
#: Çözüm iki katmanlıdır (ikisi de gerekli):
#:   1. süreç ortamındaki `FOLLOWER_*` değişkenlerini SİL — testlerin kurduğu
#:      `Settings(...)` örnekleri sunucu ortamından etkilenmesin;
#:   2. süreç-geneli `settings` tekilinde gömülü modu KAPALI sabitle — gömülü
#:      davranışın pozitif testleri onu kendi içinde açıkça açar
#:      (tests/test_follower_embedded.py).
#:
#: `.env` DOSYASINDAN gelen sızıntıyı yalnız (2) durdurur; ortam değişkeni
#: sızıntısını yalnız (1). Bu yüzden ikisi birlikte durur.
_FOLLOWER_SETTINGS_ISOLATION = {
    "follower_embedded": False,
    "follower_symbols": "",
    "follower_forward_url": "",
    "follower_forward_secret": "",
}


@pytest.fixture(autouse=True)
def _isolate_follower_settings(monkeypatch):
    for key in list(os.environ):
        if key.upper().startswith("FOLLOWER_"):
            monkeypatch.delenv(key, raising=False)
    for field, value in _FOLLOWER_SETTINGS_ISOLATION.items():
        monkeypatch.setattr(settings, field, value, raising=False)
    yield
