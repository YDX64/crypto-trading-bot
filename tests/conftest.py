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
from src.strategies.scalper import counterfactual_store
from src.strategies.scalper.data import MarketDataGuard
from src.trading.binance_client_improved import ImprovedBinanceClient


@pytest.fixture(autouse=True)
def _reset_market_data_guard():
    MarketDataGuard.reset()
    yield
    MarketDataGuard.reset()


@pytest.fixture(autouse=True)
def _reset_counterfactual_store():
    """D27/B karşı-olgu defteri de SÜREÇ-GENELİ MODÜL durumudur.

    D27 düşmanca incelemesi-2 (bulgu 5): GERÇEK bir `ScalpEngine` kuran her
    test (`test_market_data_source.py`, `test_tv_events.py`,
    `test_market_gate.py`, `test_shadow_mode.py`, `test_runtime_liveness.py`)
    defteri `enabled=True` yapıyor ve HİÇBİR YERDE sıfırlanmıyordu; tam
    paket sonunda `enabled=True, pending=4, registered=4` kalıyordu.
    Bugün zararsız, ama `counters_snapshot()` okuyan HERHANGİ bir yeni test
    sıra-bağımlı (dolayısıyla sahte-yeşil) olurdu. `MarketDataGuard` ve
    `reset_weight_state` ile AYNI gerekçe.
    """
    counterfactual_store.reset()
    counterfactual_store.configure(enabled=False)
    yield
    counterfactual_store.reset()
    counterfactual_store.configure(enabled=False)


@pytest.fixture(autouse=True)
def _reset_rest_weight_state():
    """D22: imzalı REST ağırlık geri çekilmesi de SINIF düzeyi durumdur.

    Yukarıdakiyle aynı gerekçe: bir testte kurulan geri çekilme penceresi
    sonraki testte ilgisiz bir "background" isteğini sessizce engellerdi.
    """
    ImprovedBinanceClient.reset_weight_state()
    yield
    ImprovedBinanceClient.reset_weight_state()


#: Takipçi/mod ayarlarının TEST İZOLASYONU (D20b).
#:
#: NEDEN (düşmanca inceleme bulgu 29 + doğrulayıcı bulguları Y2/Y3/Y4 — KRİTİK):
#: `Settings.model_config` `env_file=".env"` kullanır ve yol ÇALIŞMA DİZİNİNE
#: görelidir. `scripts/server_deploy.sh` testleri halkanın KENDİ dizininde
#: koşturur → pytest CANLI `.env`'i okur. Üç ölçülmüş kırılma:
#:   * `FOLLOWER_EMBEDDED=true` → `/tv-signal` testleri takipçiye yönlenir;
#:   * `BOT_MODE=follower` (takipçi halkasının ZORUNLU ayarı) → lifespan erken
#:     dala girer, `/health` testleri kırmızıya döner;
#:   * herhangi bir `FOLLOWER_*` ayarı (ör. `FOLLOWER_MAX_POSITIONS=9`) tekile
#:     sızar.
#: İlk kırmızıda `pytest -x` durur ve deploy KODU GERİ ALIP çalışan süreci
#: yeniden başlatır: bir AYAR dosyası kod kapısını kırar.
#:
#: Çözüm iki katmanlıdır (ikisi de gerekli):
#:   1. süreç ortamındaki ilgili değişkenleri SİL — testlerin kurduğu
#:      `Settings(...)` örnekleri sunucu ortamından etkilenmesin;
#:   2. süreç-geneli `settings` tekilinde TÜM `follower_*` alanlarını ve
#:      `bot_mode`u SINIF VARSAYILANINA sabitle — gömülü/ayrı halka davranışının
#:      pozitif testleri onları kendi içinde açıkça açar
#:      (tests/test_follower_embedded.py, tests/test_follower_mode.py).
#:
#: `.env` DOSYASINDAN gelen sızıntıyı yalnız (2) durdurur; ortam değişkeni
#: sızıntısını yalnız (1). Bu yüzden ikisi birlikte durur.
#: D23 (AI karar katmanı) AYNI GEREKÇEYLE izolasyona alınır ve dahası vardır:
#: `SCALPER_AI_GATE_MODE=shadow` bir `.env`/ortam değişkeninden sızarsa
#: testlerde kurulan motorlar arka planda GERÇEK bir sağlayıcıya (DeepSeek/
#: Gemini/OpenAI) HTTP isteği açmaya çalışırdı. Test paketi ağ yapmaz: mod
#: her testte sınıf varsayılanına (`off`) sabitlenir ve katmanın pozitif
#: testleri kendi sahte sağlayıcılarıyla açıkça açar
#: (tests/test_ai_gate.py).
_ISOLATED_ENV_PREFIXES = ("FOLLOWER_", "SCALPER_AI_GATE_")
_ISOLATED_ENV_NAMES = (
    "BOT_MODE",
    "TRADING_ACCOUNT_LOCK_ENABLED",
    "TV_ENTRY_SOURCE_BLOCKLIST",
)


def _isolated_settings_fields():
    """`follower_*` + `scalper_ai_gate_*` alanları + `bot_mode` → varsayılan.

    Tek tek listelemek yerine model alanlarından TÜRETİLİR: ileride eklenen
    bir `FOLLOWER_*` / `SCALPER_AI_GATE_*` ayarı otomatik olarak izolasyona
    dahil olur (doğrulayıcı bulgusu Y4: "koruma tek assert uzaklıkta").
    """
    from pydantic_core import PydanticUndefined

    pinned = {}
    for name, field in type(settings).model_fields.items():
        if not (
            name.startswith("follower_")
            or name.startswith("scalper_ai_gate_")
            or name == "bot_mode"
            or name == "tv_entry_source_blocklist"
            or name == "trading_account_lock_enabled"
        ):
            continue
        default = field.default
        if default is PydanticUndefined:
            continue
        pinned[name] = default
    # Deploy testleri çalışan canlı süreçle AYNI `.env` API anahtarını okur.
    # Yaşam döngüsü testleri gerçek hesap kilidini almaya çalışmamalı; kilidin
    # kendisi `test_account_lock.py` içinde doğrudan ve izole dizinde sınanır.
    pinned["trading_account_lock_enabled"] = False
    return pinned


@pytest.fixture(autouse=True)
def _isolate_follower_settings(monkeypatch):
    for key in list(os.environ):
        upper = key.upper()
        if upper in _ISOLATED_ENV_NAMES or any(
            upper.startswith(prefix) for prefix in _ISOLATED_ENV_PREFIXES
        ):
            monkeypatch.delenv(key, raising=False)
    for field, value in _isolated_settings_fields().items():
        monkeypatch.setattr(settings, field, value, raising=True)
    yield
