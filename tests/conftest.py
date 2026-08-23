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

import pytest

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
