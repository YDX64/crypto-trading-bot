"""Pytest oturumu için log yönlendirmesi.

Neden: testler `src.core.logger`'ı import ettiği anda loguru handler'ları
kuruluyor ve TESTUSDT/orderId=555 gibi kurgu kayıtlar canlı denetim
dosyalarına (logs/trades.log, logs/errors.log) düşüyordu. Bu, gerçek bir
arıza aranırken yanlış ize götürüyor. Kök conftest, tests/ altındaki
conftest ve test modüllerinden ÖNCE yüklendiği için yönlendirme her
koşuda garanti devrededir.

setdefault kullanılır: dışarıdan TRADINGBOT_LOG_DIR verilmişse ona
dokunulmaz (CI'da farklı dizin isteyebilmek için).
"""

import os
from pathlib import Path

_TEST_LOG_DIR = Path(__file__).resolve().parent / ".test-logs"
_TEST_LOG_DIR.mkdir(exist_ok=True)
os.environ.setdefault("TRADINGBOT_LOG_DIR", str(_TEST_LOG_DIR))
