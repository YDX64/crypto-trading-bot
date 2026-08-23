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


@pytest.fixture(autouse=True)
def _reset_market_data_guard():
    MarketDataGuard.reset()
    yield
    MarketDataGuard.reset()
