# TRADINGBOT — Mimari Doküman

Bu dosya koddan okunarak yazılmıştır (`grep`/`Read`, tahmin yok). Belirsiz kalan
her nokta "kodda doğrulanamadı" diye işaretlidir. Anchor formatı: `dosya:satır`.

## 1. Tek bakışta

- Python/FastAPI kripto vadeli işlem botu; borsa **Binance USDⓈ-M Futures**,
  ağ **TESTNET** (`binance_base_url` varsayılanı `testnet.binancefuture.com`,
  `src/core/config.py:45`; mainnet açık onay ister — §9).
- Canlıda aktif strateji: **scalper motorunun "C" varyantı** (yerel `.env`:
  `SCALPER_STRATEGIES=C`; kod varsayılanı `"A,B,C,D"`, `config.py:142`).
- Tek süreç, tek FastAPI app (`src/main.py:215`, `uvicorn src.main:app`).
  Süreç içinde eşzamanlı çalışan bileşenler (`lifespan`, `main.py:127-213`):
  1. **ScalperEngine** — otonom tarama/giriş/çıkış motoru (`scalper_enabled`
     ise), 3 arka plan task'ı: scan, safety, exchange-readiness
     (`src/strategies/scalper/engine.py:218-252`).
  2. **TradingOrchestrator** — eski Telegram-VIP-sinyal akışı; scalper'ın
     sahiplenmediği açık pozisyonları kurtarır/izler (`src/services/
     orchestrator.py:43`).
  3. **TelegramBotService** — VIP kanal mesajlarını ayrıştırıp orchestrator'a
     besler + `/status /positions` bilgi komutları (`src/services/
     telegram_bot.py:27`); supervisor ile bounded backoff'la yeniden başlatılır
     (`main.py:102-125`).
  4. **TradingView webhook köprüsü** — `/tv-signal` (`main.py:613`), harici
     yön sinyalini scalper'ın kendi giriş hattına sokar.
- Deprecated: `src/api_server.py` (kendi docstring'i: "artık aktif kullanılmıyor",
  `src/api_server.py:5`) — kullanılmamalı, `src/main.py` tektir.
- Sunucu dağıtımı (2026-08-21 sunucuda doğrulandı): `awa:/opt/tradingbot-v2`,
  **supervisord** programı `tradingbot_v2` → `.venv/bin/python -m uvicorn
  src.main:app --host 127.0.0.1 --port 9091`. Sunucu repo'su bu GitHub
  repo'sunun `main`'ini izler (`scripts/deploy.sh`). `.env` sunucuda ayrı,
  commit'lenmez. ⚠️ systemd'deki `live-bot.service` trading botu DEĞİLDİR
  (futbol botu) — bkz. `docs/RUNBOOK.md`.

## 2. Veri/karar akışı

```mermaid
flowchart TD
    A["Binance /fapi/v1/klines\n(5m/15m/tf_regime)"] --> B["KlineFetcher\ndata.py"]
    B --> C["StrategyContext\ntypes.py:59"]
    C --> D["StrategyC.evaluate\nsetups.py:459"]
    D -->|ScalpSignal| E["engine._evaluate_symbol\nengine.py:769"]
    E --> F{"Rejim kapısı\nengine.py:815-834"}
    F -->|blokla| X1["log + atla"]
    F -->|geç| G["apply_stop_policy\nsetups.py:87"]
    G --> H{"Kapasite/cooldown/\nallowlist/entry_lock\nengine.py:900-931"}
    H -->|reddet| X2["log + atla"]
    H -->|geç| I["ScalpExecutor.try_open\nexecutor.py:678"]
    I --> J["Maker LIMIT GTX\nveya Market\nexecutor.py:805-820"]
    J --> K["SL + TP1/TP2 algo emirleri\n_finalize_position\nexecutor.py:1263"]
    K --> L["ExitManager.track\nexits.py:122"]
    L --> M["TP ladder / BE / chandelier\n/ reaper\nexits.py, engine.py:602"]
    M --> N["_CloseLedger doğrulaması\nexits.py:525"]
    N --> O["ScalpTracker.record_close\ntracker.py:70"]
    O --> P["scalp_trades tablosu\nscalp_trade.py"]
    P --> Q["/scalper/stats,/scalper/trades\nmain.py:797,853"]

    TV["TradingView alert"] --> TVW["/tv-signal\nmain.py:613"]
    TVW --> RS["resolve_tv_signal\n(?src= etiketi)\nmain.py:544"]
    RS --> CONF["TvConfluence.vote\ntv_confluence.py:45\n(required>1 ise)"]
    CONF -->|eşik dolmadı| X3["accepted=False"]
    CONF -->|eşik doldu| EXT["engine.external_signal\nengine.py:968"]
    EXT --> E
```

Zaman dilimi rolleri (`engine.py:775-791`, config varsayılanı 5m/15m/4h):
`scalper_tf_entry` (giriş mumu, varsayılan 5m) → `scalper_tf_context`
(bağlam/equilibrium, 15m) → `scalper_tf_regime` (rejim; kod varsayılanı 4h,
canlı sunucu `.env`'i **15m** — 2026-08-21'de `.venv` içinden
`settings.scalper_tf_regime` okunarak doğrulandı; 4h varyantı backtest'te
boğayı yok ettiği için reddedildi, bkz. `docs/DECISIONS.md`).

### Kline veri kaynağı ve ağırlık bütçesi (D17)

Mum verisi `KlineFetcher` (`src/strategies/scalper/data.py`) üzerinden public
(imzasız) `/fapi/v1/klines`'tan gelir. Host seçimi:

| Ayar | Kline (+ baz referansı) host'u | Emir/bakiye/pozisyon/evren-ticker host'u |
|---|---|---|
| `SCALPER_MARKET_DATA_BASE_URL` boş (varsayılan) | `BINANCE_BASE_URL` | `BINANCE_BASE_URL` |
| dolu (ör. `https://fapi.binance.com`) | o host | `BINANCE_BASE_URL` (DEĞİŞMEZ) |

Ayrı host'a giden İKİ public çağrı vardır: kline çekimi ve (yalnız ayrı
host'ta) chandelier bazının veri-tarafı referansı olan tek sembollük
`/fapi/v1/ticker/price` (D17-R3, aşağıdaki 1. kalkan). `UniverseScanner`
(24s ticker → evren) BİLİNÇLİ olarak işlem host'unda kalır — evren, işlem
yapılamayan sembollerle dolmamalı; emir/bakiye/pozisyon ve `get_current_price`
de İŞLEM host'undadır.
`ExitManager` motorla AYNI fetcher örneğini kullanır (`engine.py`'de
`self.fetcher.get_klines` callback'i), yani giriş ve trailing mumları hep aynı
kaynaktan gelir. Mainnet'te işlem yapılırken market-data host'u testnet olamaz
(`config._validate_binance_environment`).

**Emir FİYATLARI işlem borsasının uzayındadır** (kodda doğrulandı): maker LIMIT
fiyatı işlem host'unun `bookTicker`'ından gelir (`executor.py:1529-1534`), SL/TP
sinyal fiyatından değil GERÇEK dolumdan hesaplanır ve `_delay_adjusted_stop`
(`executor.py:1280-1345`) stop'u dolum kaymasına göre öteleyip giriş–stop
MESAFESİNİ korur. **Chandelier trailing** tek istisnaydı: mumlardan MUTLAK bir
seviye üretip doğrudan emre çeviriyordu — ayrı host'ta bu, yabancı bir defterin
fiyatını borsaya stop olarak göndermek demekti (baz farkı `k×ATR`'yi aşarsa
Binance -2021 → `position_manager` pozisyonu ACİL KAPATIR).

İki katmanlı kalkan (`exits._update_trailing`, yalnız ayrı host'ta; aynı host'ta
ikisi de NO-OP → canlı davranış birebir korunur):

1. **Dinamik, LIKE-FOR-LIKE baz çevirisi** (`_to_trading_price_space`). Her
   çıkış turunda `baz = işlem_host_CANLI_fiyat − veri_host_CANLI_fiyat`;
   chandelier seviyesi `+ baz` ile işlem uzayına taşınır, MESAFE (birim risk)
   korunur. İki referans da AYNI turda ve AYNI TÜRDEN okunur:
   `sp.position.current_price` `_step_one`'da işlem host'unun ticker'ından
   tazelenir, veri tarafı `exits._data_host_price` → `KlineFetcher.get_price`
   (public `/fapi/v1/ticker/price`, ağırlık 1, `MarketDataGuard`'dan geçer,
   sembol başına TTL = safety turu). Baz ölçülemezse (işlem fiyatı bayat/eksik,
   veri host'u fiyatı okunamadı, |baz| > %2) çeviri `None` döner ve **tur
   atlanır** — yabancı uzaydan emir gönderilmez.
   *Neden dinamik:* ilk sürüm bazı yalnız GİRİŞ anında ölçüyordu
   (`position.entry_price − signal.entry_price`) ve pozisyon ömrü boyunca sabit
   uyguluyordu; ayrıca `recover()` iki fiyatı da `trade.entry_price`'tan
   kurduğu için restart sonrası baz 0 çıkıp düzeltme sessizce no-op oluyordu
   (DB'de sinyal-anı fiyatı kolonu yok). Dinamik baz her turda yeniden
   ölçüldüğü için restart'ta ek alan/migrasyon gerektirmez.
   *Neden like-for-like (D17-R3, bütünleşme incelemesi):* ikinci sürüm veri
   tarafı için `candles[-1].close` (son KAPANMIŞ mum) kullanıyordu. Bu iki
   büyüklük aynı türden DEĞİLDİR: fark, borsa-arası bazın ÜSTÜNE **mum-içi
   sürüklenmeyi** de bindirir. Etki sistematiktir — fiyat pozisyonun lehine
   gittikçe sürüklenme büyür, chandelier mandalı (`new_stop > current_sl`) her
   turda biraz daha sıkışır ve stop fiilen CANLI FİYATI izler, chandelier
   MESAFESİNİ değil (ters yönde ise koruma-tarafı kapısı turu boşa atlatır).
   Mum kapanışı artık yalnız chandelier SEVİYESİNİ üretir, bazı değil.
2. **Koruma-tarafı kapısı** (`_is_protective_side`). Çeviriden ve BE tabanından
   (`floor`, işlem uzayındadır) sonra: LONG stop güncel fiyatın `%0.05` altında,
   SHORT stop üstünde olmalıdır. Değilse emir **hiç gönderilmez**. Gönderilseydi
   Binance -2021 verir ve `position_manager._replace_stop_loss` bunu bir çıkış
   kararı sayıp pozisyonu PİYASA emriyle kapatırdı.
   **Kapı YALNIZ ayrı market-data host'unda uygulanır** (tur atlanır, eski SL
   yerinde kalır, `trailing_skips.protective_gate_skips`): oradaki "yanlış
   taraf" borsalar-arası bir BAZ ÖLÇÜM hatası olabilir ve kârlı bir koşucuyu
   ölçüm hatasıyla kapatmak yanlıştır. **Aynı host'ta kapı YOKTUR ve
   olmamalıdır** (D22 daraltması): stop borsaya gönderilir, hükmü BORSA verir
   (`-2021`) ve mevcut `_emergency_close` çalışır — bot kendi fiyat okumasına
   dayanarak geri alınamaz bir piyasa emri göndermez. Kapanışın deftere nasıl
   yazıldığı §5.0'dadır.

`_delay_adjusted_stop` ile **desen** aynıdır (mutlak seviyeyi ötele, mesafeyi
koru) ama referansları farklıdır ve olmalıdır: oradaki öteleme TEK SEFERLİK bir
gecikme telafisidir (sinyal anı → gerçek dolum, aynı host) ve koruma tarafını
GİRİŞ fiyatına göre denetler; buradaki SÜREKLİ bir borsa-arası baz çevirisidir
ve koruma tarafını GÜNCEL fiyata göre denetler.

Böylece ayrı host YALNIZ gösterge girdisidir (RSI/BB/diverjans/rejim/ATR); iki
borsa arasındaki küçük fiyat farkı (E8.0: medyan %0.054) boyutlamayı ya da
koruma seviyelerini kaydırmaz.

**Kesinti davranışı — hata KAPSAMI belirleyicidir:**

| Yanıt | Tip | Kapsam | Tekrar | Kesici |
|---|---|---|---|---|
| 418 / `-1003` / "banned until" | `MarketDataBanError` | host | yok | **hard ban** 180 sn (deploy kilidi kapanır) |
| 429 (tek başına) | `MarketDataBanError` | host | yok | soft: `Retry-After` → ağırlık başlığı → 30 sn (deploy kilidi kapanmaz) |
| 401 / 403 / 451 / diğer 4xx | `MarketDataHostError` | host | yok | soft: `Retry-After` → 60 sn |
| 400 / 404 (`-1121 Invalid symbol`) | `MarketDataRequestError` | **sembol** | yok | yok |
| 5xx / ağ | (3 deneme) → `MarketDataHostError` | host | 3× | yok |

Host geneli tipler `MarketDataUnavailable` alt sınıfıdır: tarama turunu tek
WARNING ile keser (`_scan_tick` → `scan_status=degraded:market_data`), safety
turunda trailing'i tur başına tek satırla atlar (`exits.step`) ve
`/tv-signal`'i 500 yerine yapısal ret'e çevirir (`external_signal`). SEMBOL
kapsamlı tip turu KESMEZ (yalnız o sembol atlanır) ama `/tv-signal`'de yine
yapısal ret üretir. Harness (`backtest.py`) guard'ı `batch` modunda kullanır —
bütçe dolarsa koşu ölmez, en eski ağırlık girdisi pencereden düşene kadar
bekler (canlıdan daha gevşek bütçe/aralık; ban koruması aynen sürer).

**Kesilen tur "başarılı" DEĞİLDİR:** `_scan_success_count` artmaz,
`last_scan_at` tazelenmez, önceki hata serisi silinmez; `/scalper/status` →
`scan_status` = `"degraded:market_data"` + `scan_degraded_count`/`_reason`/
`_at`. Freshness alanları (`_scan_last_success_monotonic`) BİLİNÇLİ tazelenir:
ban sırasında "unhealthy" göstermek watchdog restart'ını davet eder, ki bu
2026-08-14 felaket yoludur.

Teşhis: `GET /scalper/status` →
`market_data_base_url` / `trading_base_url` / `kline_source`
("trading_host"|"separate"), `market_data_guard` (host/banned/**hard_ban**/
blocked_until/ağırlık), `scan_status`, `trailing_skips`; başlangıçta tek satır
`📡 Kline kaynağı: <host>`.

**Ağırlık hesabı** (Binance USDⓈ-M `/fapi/v1/klines` ağırlığı limit'e göre:
<100→1, 100-499→2, 500-1000→5, >1000→10; IP bütçesi 2400/dk). Tablo **CANLI
profile** göredir: `SCALPER_TF_ENTRY=1m`, `TF_CONTEXT=5m`, `TF_REGIME=15m`,
tarama turu 30 sn, safety turu 2 sn. TTL'ler `data._TTL_BY_INTERVAL`
(1m→5 sn, 5m→20 sn, 15m→60 sn, 4h→300 sn); TTL mum periyodunun ~%7-8'idir —
`1m` girdisi D17 sonrası eklendi, yoksa varsayılan 60 sn'ye düşüp giriş dilimi
TAM BİR MUM bayat kalıyordu.

| Kaynak | İstek | TTL | İstek/dk | Ağırlık/dk |
|---|---|---|---|---|
| scan, giriş TF | `1m` limit 150 | 5 sn (< 30 sn tur) | 2 × sembol | 4 × sembol |
| scan, bağlam TF | `5m` limit 100 | 20 sn (< 30 sn tur) | 2 × sembol | 4 × sembol |
| scan, rejim TF | `15m` limit 250 | 60 sn | 1 × sembol | 2 × sembol |
| exits trailing | `1m` limit 200 | 5 sn (safety 2 sn → TTL bağlar) | 10 × açık poz. | 20 × açık poz. |
| exits baz referansı **(yalnız AYRI host)** | `ticker/price` (tek sembol) | 2 sn = safety turu | 30 × açık poz. | 30 × açık poz. |

8 sembollük allowlist + 3 açık pozisyon → **40 + 30 = 70 istek/dk ≈ 140
ağırlık/dk**; `SCALPER_TOP_N=12` ile 90 istek/dk ≈ **180 ağırlık/dk** (IP
bütçesinin ~%7.5'i). Varsayılan (yavaş) profil 5m/15m/4h ise bunun yaklaşık
yarısıdır.

**Ayrı market-data host'unda EK yük** (D17-R3 baz referansı; ayar boşken bu
satır YOKTUR — aynı host'ta hiç istek atılmaz): safety turu 2 sn → 30 tur/dk,
TTL = tur süresi → sembol başına **tur başına en fazla 1 istek**, ağırlığı 1.
`SCALPER_MAX_POSITIONS=3` ile **90 istek/dk ≈ 90 ağırlık/dk**; toplam
**160 istek/dk ≈ 230 ağırlık/dk** (IP bütçesinin ~%9.6'sı, kendi 600'lük
bütçemizin ~%38'i). Kuramsal tavan — 8 sembolün HEPSİNDE açık pozisyon —
8 × 30 = **240 istek/dk ≈ 240 ağırlık/dk**; buna trailing mumları da eklenirse
40 + 80 + 240 = 360 istek/dk ≈ 80 + 160 + 240 = **480 ağırlık/dk**, yani
600'lük bütçenin %80'i. Bu tavana `scalper_max_positions=3` yüzünden bugün
ULAŞILAMAZ; tavanı yükseltmek isteyen önce bu satırı yeniden hesaplamalı.

⚠️ Bu bir HESAPTIR (TTL/tur aritmetiği), ölçüm değil:
`X-MBX-USED-WEIGHT-1M` telemetrisi D17'de eklendi, gerçek okuma canlıda henüz
yapılmadı (D17 terfi adımı (c)). Harness AYRI bir profildir: `limit=1500`
sayfaları ağırlık 10 eder (8 sembol × 21 gün ≈ 472, × 30 gün ≈ 656) — bu yüzden
`batch` modunda hem bekler hem de daha gevşek bir bütçe (1200/dk) ve aralık
(0.05 sn) kullanır.
`MarketDataGuard` bu yola host BAŞINA bir tavan koyar: asgari istek aralığı
0.15 sn + KAYAN 60 sn'de 600 ağırlık bütçesi (deque; sabit sınırlı "tumbling"
pencere değil — orada sınır anında sayaç sıfırlandığı için 60 sn'lik herhangi
bir kayan aralığa bütçenin iki katı sığabiliyordu) (hesaplananın ~4 katı — normal
işletmede bağlamaz; bağlarsa istek ATILMAZ ve BEKLENMEZ — `MarketDataBudgetError`
yükselir, çağıran turu atlar. Bilinçli: kilit altında 60 sn beklemek safety
turunun 30 sn'lik tazelik limitini aşıp watchdog restart'ı tetikleyebilirdi
(restart, 2026-08-14 felaket yolunun kendisi). İmzalı
yolun küresel `rate_limiter`'ı (0.5 sn) BİLİNÇLİ paylaşılmaz: public veri emir
akışını bloklamamalı (12 sembol × 3 TF × 0.5 sn ≈ 18 sn/tur, safety turunu da
aynı kuyrukta bekletirdi — bkz. §9 dashboard force-fresh açlığı).

**Ban semantiği (host başına):** 418/429/-1003 → fail-closed kesici (tekrar
YOK; ban sırasında istek atmak yasağı uzatır) + `_scan_tick` turu keser
(`MarketDataUnavailable` → tek WARNING, kalan semboller denenmez) + `HTTP 418` içeren CRITICAL log
(`scripts/server_deploy.sh` deploy'dan önce tam bu kalıbı arar). Ağırlık sayacı
Binance'te host+IP başınadır: mainnet fapi ile testnet AYRI kümelerdir, bu
yüzden ayrı host kullanılırken kesiciler de ayrıdır. Aynı host'ta ilişki TEK
YÖNLÜDÜR: imzalı yolun banı public çekimi durdurur; public ban imzalı kesiciyi
KURMAZ (`KlineFetcher` `BINANCE_BIND_IP`'ye bind edilmez → farklı çıkış IP'si
olabilir; public ban, imzalı yolun banlı olduğunun kanıtı değildir ve emir
yönetimini kanıtsız durdurmak en pahalı hatadır — bkz. `docs/DECISIONS.md` D17).

TV yolunda sağlama kuralı (`src/services/tv_confluence.py:29-89`): oy =
(sembol, yön, kaynak); `tv_confluence_window_seconds` (varsayılan 180s)
içinde `tv_confluence_required` (varsayılan 1) farklı kaynak aynı yönde oy
vermeli; ters yön oyu geldiğinde HER İKİ tarafın oyları silinir (`vote()`,
satır 56-63). `?src=` yoksa kaynak, AlgoPro'nun varsayılan mesaj biçiminden
("`| TF:`"/"`| Price:`") tahmin edilir, yoksa `"tv"` (`main.py:645-647`).

**İKİNCİ TV yolu (D19/D19a, 2026-08-23):** aynı `/tv-signal` uç noktası, gövdesinde
`kind=exit|choch|trend|tp1` taşıyan istekleri **sağlamaya HİÇ sokmadan**
`src/services/tv_events.py` defterine yazar (`_handle_tv_event`). O defter
motorun İKİ yerinde okunur: `_evaluate_symbol`'deki yapı kapısı (rejim
kapısının hemen yanında) ve `_safety_tick`'teki BE/kapanış tetikleyicisi
(`_apply_tv_event_exits`, `exits.step()` ile reaper arasında).
Üç kademeli (`SCALPER_TV_EVENTS_MODE=off|shadow|active`, varsayılan `shadow` =
davranış değişmez). İki yön arasındaki ayrım **tek yönlüdür**: `TV_EVENT_SOURCES`
listesindeki bir kaynak GİRİŞ OYU VEREMEZ (422) ve `kind != entry` bir istek
sağlamaya giremez. Yardımcı uçlar: `POST /tv-signal?dry_run=1` (doğrula, yazma) ve
`POST /tv-events/reset?secret=` (defteri RAM+diskte sıfırla).
Ayrıntı: `docs/INTEGRATIONS.md` §7, `docs/DECISIONS.md` D19 + **D19a** (24 düşmanca
inceleme bulgusu; çelişki görürsen D19a bağlayıcıdır).

### İmzalı REST ağırlık bütçesi ve istek önceliği (D22 — varsayılan KAPALI)

Yukarıdaki tablo **public** (imzasız) kline yolunu anlatır; imzalı yolun
(`ImprovedBinanceClient`) kendi bütçesi vardır: Binance IP ağırlık sınırı
**2400/dk** ve sayaç **IP GENELİDİR** — aynı çıkış IP'sindeki başka süreçler de
tüketir (`BINANCE_BIND_IP` bu yüzden vardır). Her yanıtın
`X-MBX-USED-WEIGHT-1M` başlığı `_note_used_weight` ile işlenir; ölçüm eşiği
aşarsa **o takvim dakikasının sonuna kadar** geri çekilme penceresi açılır
(Binance 1M sayacı orada sıfırlanır).

**Geri çekilme VARSAYILAN OLARAK KAPALIDIR** (`BINANCE_WEIGHT_SOFT_LIMIT=0`,
`BINANCE_WEIGHT_HARD_LIMIT=0`); ölçüm ve telemetri her zaman çalışır. Gerekçe
ölçümdür: testnet'te bu başlığın günlük MEDYANI 2373'tü (>2000), yani ilk
tasarımın eşikleriyle tarama KALICI dururdu. Eşik önce ölçülür, sonra açılır
(`docs/RUNBOOK.md`).

| Kademe | Eşik | Kritik OLMAYAN istek | Kritik istek |
|---|---|---|---|
| off (varsayılan) | eşik 0 = kapalı, ya da ölçüm < soft | gider | gider |
| soft | ≥ `BINANCE_WEIGHT_SOFT_LIMIT` | **gitmez**; önbellek varsa BAYAT servis | gider |
| hard | ≥ `BINANCE_WEIGHT_HARD_LIMIT` | **gitmez** + CRITICAL ≤1/dk | gider |

`_request_with_retry(..., priority=...)` varsayılanı `"critical"`tir: bir çağrı
yolu işaretlenmeyi unutursa güvenli tarafta kalır. Kritik olmayan olarak
işaretlenenler: `/api/status` pano beslemesi (bakiye, BTC fiyatı, pozisyon
sayısı — `priority="background"`), tarama turu (`_scan_tick` geri çekilmede
HİÇ başlamaz → `scan_status="degraded:rest_weight"`) ve adli kayıt post-mortem
turu (`_forensics_postmortem_blocked`). **Emir, SL/TP, positionRisk koruma
turu, kapanış doğrulaması ve günlük risk income'ı DAİMA kritiktir** — bir
dakikalık bütçe uğruna korumasız/ölçülmemiş pozisyon bırakılmaz.

Geri çekilme sırasında önbellekten servis KOŞULLUDUR ve yalnız kritik olmayan
yola açıktır (`_get_account`, `get_current_price`): bayat bir bakiye
göstermek, bütçeyi 418'e taşımaktan iyidir; koruma yolu bayat veri görmez.

Pencere ASLA `max()` ile kilitlenmez: daima içinde bulunulan takvim
dakikasının sonudur (`min(..., now+60)` ikinci kemer) ve okuma tarafında bir
dakikadan uzağa işaret eden bir damga geçersiz sayılıp temizlenir — ileri bir
saat sıçraması (NTP/VM suspend) botu süresiz durdurmamalıdır.

Pano tarafı: `/api/status` ve `/scalper/status` sunucuda **5 sn**
önbelleklenir (pano da 5 sn'de bir yokluyor) ve pano yolundan `force_fresh`
İSTENMEZ (2026-08-18 rate-limiter açlığı). Yanıttaki `as_of` gövdenin
KURULDUĞU andır — pano "son güncelleme"yi ondan yazar, yoksa bayat bir tablo
her tikte taze görünürdü. Durum DEĞİŞTİREN uçlar (`/risk-event`,
`/tv-events/reset`) önbelleği düşürür; sorgu dizesi anahtarın parçasıdır.
Motor YOKKEN `/scalper/status` önbelleklenmez (REST yapmaz, olay defteri taze
olmalıdır). Telemetri: `/scalper/status.rest_weight` — `max_1m` DAKİKA
DİLİMLİDİR (içinde bulunulan takvim dakikasının tepesi).

## 3. Modül haritası

| Dosya | Sorumluluk | Anahtar semboller |
|---|---|---|
| `src/main.py` | FastAPI app, lifespan, tüm HTTP endpoint'leri | `lifespan:128`, `resolve_tv_signal:544`, `tradingview_webhook:613`, `risk_event` (POST `/risk-event`, D10, hemen `tradingview_webhook` sonrası), `health_check:264`, `api_status:332`, `scalper_stats:798` |
| `src/core/config.py` | Tüm ayarlar (pydantic `Settings`), testnet/mainnet fail-safe | `Settings:28`, `is_testnet:313`, `_validate_binance_environment:392` (mainnet+halt_enabled+prod uyarı zinciri) |
| `src/core/database.py` | Async SQLAlchemy engine/session, SQLite WAL, idempotent migration | `init_db:88`, `_ensure_schema_migrations:76` (entry_order_id kolonu sonradan eklendi) |
| `src/core/rate_limiter.py` | Küresel Binance/OpenAI hız sınırlayıcı | `RateLimiter.wait_for_binance:49` (asyncio.Lock ile atomik slot rezervi) |
| `src/strategies/scalper/engine.py` | Orkestrasyon: scan/safety/exchange döngüleri, kapılar, kill switch, risk-olayı kanalı | `ScalperEngine:100`, `_scan_tick:699`, `_evaluate_symbol:769`, rejim kapısı `815-834`, `_reap_aged_positions:602`, `_update_kill_switch:1343`, `external_signal:968`, `health_snapshot:1241`, `_risk_event_halt_snapshot`/`risk_event_halt`/`risk_event_resume`/`risk_event_flatten`/`risk_event_status` (risk-olayı bölümü, `_persist_entry_halt` sonrası) |
| `src/strategies/scalper/setups.py` | Saf strateji mantığı (A/B/C/D/E), stop politikası, ortak kapılar | `StrategyC:431` (`evaluate:459`), `apply_stop_policy:87`, `passes_equilibrium:194`, `get_enabled:931` |
| `src/strategies/scalper/data.py` | Public kline çekimi, TTL önbelleği, host başına oran/kayan-ağırlık/ban koruması, hata kapsamı sınıflandırması (D17) | `KlineFetcher`, `MarketDataGuard`, `MarketDataBanError`, `MarketDataHostError`, `MarketDataRequestError`, `klines_weight`, `host_of`, `retry_after_seconds` |
| `src/strategies/scalper/regime.py` | 4h/tf_regime rejim tespiti (EMA50/200) | `detect_regime:19` |
| `src/strategies/scalper/executor.py` | Giriş boyutlama, risk kapıları, maker/taker giriş, SL/TP algo emirleri, cooldown | `try_open:678`, `_finalize_position:1263`, `_open_maker_entry_locked:1430`, `_set_cooldown:548`, `start_loss_cooldown:586` |
| `src/strategies/scalper/exits.py` | TP1/TP2 doğrulama, BE, chandelier trailing, kapanış doğrulama | `ExitManager.step:133`, `_check_tp1:185`, `_check_tp2:232`, `_update_trailing:359`, `_handle_closed:428`, `_verified_close_ledger:525`, `recover:1095` |
| `src/strategies/scalper/tracker.py` | `scalp_trades` DB yazımı, istatistik/PnL kaynak sınıflandırması | `record_open:32`, `record_close:70`, `_pnl_source:199`, `stats:288` |
| `src/strategies/scalper/types.py` | Ortak veri sözleşmesi (saf, IO'suz) | `Regime:21`, `StrategyContext:59`, `ScalpSignal:77`, `ExitPlan:101`, `resolve_trail_mult:152`, `fee_aware_breakeven_price:176` |
| `src/strategies/scalper/backtest.py` | Tarihsel simülasyon + CLI | `_build_arg_parser:1348`, `main_async:1372`, `print_report:902` |
| `src/strategies/scalper/indicators.py` | Saf gösterge fonksiyonları (RSI/BB/ATR/MFI/chandelier/OB/EQH-EQL...) | `chandelier_stop:280`, `equilibrium:549`, `rsi_series:58` |
| `src/services/tv_confluence.py` | Çoklu-kaynak TV sağlama motoru (yalnız GİRİŞ oyları) | `TvConfluence.vote:45` |
| `src/services/tv_events.py` | TV ÇIKIŞ + YAPI/DÖNÜŞ olay defteri (D19/D19a; sağlamaya GİRMEZ) | `TvEvents.ingest`, `fresh_gate_structures`, `structure_verdict` (MIXED), `pending_exit`, `consumed_seq`/`mark_consumed`/`note_attempt` (kalıcı tüketim), `protect` (budama muafiyeti), `config_health`, `snapshot`, `reset`; süreç-tekili `tv_events` |
| `src/trading/binance_client_improved.py` | İmzalı/imzasız REST istemcisi, okuma önbellekleri, ağırlık telemetrisi | `_get_account:711`, `get_position_risk:1360`, `get_all_positions:1424`, `_request_with_retry:329` (weight header, satır 391-415), `_invalidate_read_caches:161` |
| `src/trading/position_manager.py` | Güvenli pozisyon açma/kapama, boşluksuz SL değişimi, acil kapatma | `UnprotectedPositionError:32`, `open_position:63`, `_emergency_close:416`, `replace_stop_loss:803` |
| `src/strategies/scalper/forensics.py` | İşlem adli kaydı — SAF katman (D21): etiket kuralları, belge kurucuları, özet | `classify_entry`, `classify_exit`, `classify`, `build_entry`, `build_exit`, `postmortem_from_candles`, `summarize`, `TAG_LABELS` |
| `src/strategies/scalper/forensics_log.py` | `logs/trades.jsonl` append-only olay akışı (günlük rotasyon, 30 gün); motor yolu kuyruğa yazar, disk yazımı ayrı iş parçacığında | `append_soon`, `append`, `drain`, `log_path` |
| `src/models/scalp_trade.py` | `scalp_trades` ORM modeli | `ScalpTradeModel:16`, `forensics` (D21, JSON metni) |

### `config.py` — öne çıkan `SCALPER_*` ayarları (varsayılan → anlam)

| Ayar | Varsayılan | Anlam |
|---|---|---|
| `scalper_strategies` | `"A,B,C,D"` | Aktif strateji CSV'si (`get_enabled`, `setups.py:931`); yerelde `.env` ile `C`'ye daraltılmış |
| `scalper_symbol_allowlist` | `""` (boş=scanner top_n) | Doluysa tarama SADECE bu sembollerle sınırlanır |
| `scalper_tv_symbol_allowlist` | `""` | TV dış sinyaline sembol filtresi (OSC kanıtı olan sembollerle sınırlamak için) |
| `scalper_regime_filter` | `True` | Rejim kapısını (§4) aç/kapat |
| `scalper_tv_regime_filter` | `True` | Rejim kapısının TV sinyaline de uygulanıp uygulanmayacağı |
| `scalper_market_gate` | `False` | Lider piyasa kapısı (§4.1) — ters-gün kapısı, varsayılan KAPALI |
| `scalper_market_gate_symbol` | `BTCUSDT` | Kapının baktığı lider sembol |
| `scalper_market_gate_day_pct` | `1.3` | Gün-içi alt-kapısı eşiği (%; 0 = kapalı) — E7+E8 ölçümü |
| `scalper_market_gate_run_pct` / `_run_days` | `0.0` / `3` | Uzama alt-kapısı — **varsayılan KAPALI**, iki bağımsız ölçüm çürüttü (D15) |
| `scalper_market_gate_retry_sec` | `60.0` | Lider verisi alınamazsa negatif önbellek (sn; 0 = kapalı) |
| `scalper_market_data_base_url` | `""` | Boş = kline'lar işlem host'undan; dolu = YALNIZ public kline o host'tan (D17, bkz. §2 "Kline veri kaynağı") |
| `scalper_tv_events_mode` | `"shadow"` | TV olay kanalı (D19): `off`/`shadow`/`active` — `shadow` motor davranışını DEĞİŞTİRMEZ |
| `scalper_tv_events_exit` | `"be"` | `active` modda açık pozisyona uygulanan aksiyon: `off`/`be`/`close` (`be` YALNIZ pozisyon kârdayken) |
| `scalper_tv_events_exit_losing` | `"skip"` | `be` seçiliyken pozisyon ZARARDAYSA: `skip` (dokunma) / `close` (reduce-only MARKET) — D19a B |
| `scalper_tv_events_be_margin_pct` | `0.05` | BE hedefinin piyasadan güvenli uzaklığı (%, tek yönlü pay) |
| `scalper_tv_events_max_age_min` | `240.0` | Olayın tazelik penceresi (dk) — kapı ve çıkış tetiği için; **0 = KAPALI** |
| `scalper_tv_events_gate_sources` | `"pac_choch,luxso_trend"` | Yapı durumunu KARARA sokan kaynaklar (diğerleri yalnız telemetri); **boş = hiçbiri** |
| `tv_event_sources` | `"luxso_exit,luxso_trend,pac_choch,algopro_tp1"` | Bu `src` etiketleri GİRİŞ OYU VEREMEZ (422) — D19a A |
| `scalper_tf_entry/context/regime` | `5m/15m/4h` | Giriş/bağlam/rejim zaman dilimleri |
| `scalper_c_rsi_long_max/short_min` | `25.0/75.0` | C'nin RSI uç eşiği |
| `scalper_c_require_divergence` | `True` | C'de RSI diverjans şartı |
| `scalper_c_allowed_regimes` | `"UP,DOWN,RANGE"` | C'nin çalıştığı rejim kümesi (UNKNOWN her zaman kapalı) |
| `scalper_c_blocked_cells` | `""` | Rejim×yön hücresi yasağı (`"RANGE:SHORT,UP:LONG"`), rejim kapısının ÜSTÜNE ek yasak; sinyal doğduktan sonra, niyet defterine `cell_gate` yazar — `entry_gates.py`, **varsayılan KAPALI**, post-hoc tarama adayı; motor + harness AYNI saf fonksiyon (P1, `tests/test_entry_gates.py`); C taraması VE TV dış sinyali için geçerli (ayrı TV anahtarı YOK), harness yalnız C'yi ölçer |
| `scalper_entry_block_hours_utc` | `""` | UTC saat penceresi yasağı (`"0-6,22-24"`; başlangıç dahil, bitiş hariç, `"22-3"` gece yarısını sarar); saat = son KAPANMIŞ giriş mumunun `close_time`'ı (duvar saati DEĞİL) — `hour_gate`, **varsayılan KAPALI**; C taraması VE TV dış sinyali için geçerli |
| `scalper_entry_block_weekdays_utc` / `scalper_entry_block_weekdays_direction` | `""` / `"BOTH"` | Hafta günü × yön yasağı (`"5,6"`; virgülle ayrılmış 0-6, Pazartesi=0 … Pazar=6, saat kapısıyla AYNI `close_time_ms` → `datetime.fromtimestamp(ms/1000,UTC).weekday()`) + yön `LONG\|SHORT\|BOTH`; kapı YALNIZ hafta günü alanı doluyken çalışır, yön eşleşmezse (BOTH dışında) serbest — `weekday_gate`, **varsayılan KAPALI**; C taraması VE TV dış sinyali için geçerli, harness yalnız C'yi ölçer (D33) |
| `scalper_symbol_direction_block` | `""` | Sembol × yön yasağı (`"ADAUSDT:LONG,DOGEUSDT:LONG"`; sembol büyük harfe normalize, yön `LONG\|SHORT` — BOTH YOK); cell gate'in rejim yerine sembol kullanan eşleniği, o sembolün o yönde girişini tamamen keser — `symbol_dir_gate`, **varsayılan KAPALI**; C taraması VE TV dış sinyali için geçerli (D33) |
| `scalper_min_atr_pct` / `scalper_max_atr_pct` | `0.0` / `0.0` | ATR% bandı (ATR(14)/giriş fiyatı×100, HAM sinyal, `apply_stop_policy` ÖNCESİ); 0 = o uç kapalı, `<min` / `>max` → `atr_gate` (eşitlik serbest), ATR yoksa fail-open — **varsayılan KAPALI**; C taraması VE TV dış sinyali için geçerli |
| `scalper_stop_mode` | `"structural"` | `structural` (yapısal+ATR taban) veya `fixed_roi` (marj-yüzdesi stop) |
| `scalper_fixed_stop_roi_pct` | `50.0` | `fixed_roi` modunda SL'nin vurduğu marj kaybı yüzdesi |
| `scalper_stop_atr_floor_mult` | `0.5` | Yapısal stop girişe çok yakınsa ATR×mult tabanına genişlet |
| `scalper_min_stop_pct/max_stop_pct` | `0.15/3.0` | İzin verilen stop mesafesi bandı (fiyat %) |
| `scalper_min_rr` | `1.2` | Beklenen harman ROI / SL riski alt sınırı (0=kapalı) |
| `scalper_tp1_roi/fraction` | `20.0/0.40` | TP1 ROI hedefi ve dolum oranı |
| `scalper_tp2_roi/fraction` | `50.0/0.30` | TP2 ROI hedefi ve dolum oranı (kalan %30 runner) |
| `scalper_chandelier_atr_mult/period` | `2.5/14` | Chandelier trailing çarpanı/ATR periyodu |
| `scalper_trail_relax_roi1/2_pct` | `0.0/150.0` | Kademeli gevşeyen iz eşikleri (`resolve_trail_mult`, `types.py:152`) |
| `scalper_max_hold_hours` | `0.0` | Reaper yaş limiti (0=kapalı) |
| `scalper_loss_cooldown_minutes` | `60` | SL/negatif kapanış sonrası sembol giriş kilidi |
| `scalper_stop_atr_floor_mult` | `0.5` | (yukarıda) |
| `scalper_max_margin_pct` | `10.0` | İşlem başına marj tavanı (kasanın %'si) |
| `scalper_daily_loss_limit_pct` | `15.0` | Günlük zarar kesici (0=kapalı) |
| `scalper_entry_halt_enabled` | `True` | Fail-closed giriş kilidi; mainnet'te kapatılamaz (`config.py:420-429`) |
| `scalper_entry_mode` | `"taker"` | `"maker"` = LIMIT GTX iki fazlı giriş |
| `scalper_max_positions` | `3` | Eşzamanlı scalp pozisyon tavanı |
| `scalper_dynamic_leverage` | `False` | Coin-bazlı ATR'ye göre kaldıraç çözümü (`fixed_roi` modunda) |

## 4. Rejim ve kapılar

`detect_regime` (`src/strategies/scalper/regime.py:19-44`), saf fonksiyon,
girdi `candles_4h` (veya konfigüre edilen `scalper_tf_regime`):

- `< 200` mum → `UNKNOWN` (EMA200 seed'i için yetersiz veri; **hiçbir**
  strateji işlem açmaz — `StrategyC.evaluate`, `setups.py:462-463`).
- `EMA50 > EMA200` ve son kapanış `> EMA50` → `UP`.
- `EMA50 < EMA200` ve son kapanış `< EMA50` → `DOWN`.
- Aksi halde → `RANGE`.

Rejim kapısı (`engine.py:815-834`, `_evaluate_symbol` içinde, sinyal
üretildikten SONRA): `DOWN` rejiminde `LONG`, `UP` rejiminde `SHORT`
sinyali engeller; `RANGE`/`UNKNOWN`'da (UNKNOWN zaten yukarıda elenir)
serbest. `gate_on = scalper_regime_filter AND (iç sinyal OR
scalper_tv_regime_filter)` — yani TV sinyalleri ayrı bir bayrakla muaf
tutulabilir ama varsayılan `True`'dur (`engine.py:822-825`).

`StrategyC.evaluate` (`setups.py:459-565`) kendi içinde ek bir rejim
daraltması yapar: `scalper_c_allowed_regimes` CSV'sinde olmayan rejimlerde
sinyal üretmez (`setups.py:462-463`); varsayılan `"UP,DOWN,RANGE"` = eski
davranışla birebir (yalnız UNKNOWN engelli).

### 4.1 Piyasa yapısı kapısı (BOS/CHoCH) — `structure.py`, varsayılan KAPALI

`src/strategies/scalper/structure.py` (2026-08-23, E9/D18 adayı) saf bir yapı
durum makinesidir: fraktal pivot (`SCALPER_STRUCTURE_PIVOT`, varsayılan 5, her
iki taraf) → son onaylanmış swing seviyesi → **kapanışla** (varsayılan;
`SCALPER_STRUCTURE_USE_CLOSE=false` ile fitil) kırılım. Kırılım mevcut yapı
yönüyle aynıysa **BOS** (devam), tersineyse **CHoCH** (karakter değişimi) ve
yapı yönü döner. Yön henüz NONE iken ilk kırılım BOS'tur. Her seviye YALNIZ
BİR KEZ olay üretir; yeni pivot onaylanınca seviye güncellenir.

- **Neden:** rejim kapısı (§4, EMA50/200) dönüşleri saatler geç görür; yapı
  kırılımı aynı soruyu mumun kapanışında yanıtlar (`docs/EXPERIMENTS.md` E9).
- **Veri:** `StrategyContext`'te ZATEN çekilmiş seriler
  (`SCALPER_STRUCTURE_TF` = rol adı `entry|context|regime` ya da doğrudan
  zaman dilimi metni). **Yeni REST çağrısı yoktur.**
- **Giriş kapısı** (`SCALPER_STRUCTURE_GATE`, `_BLOCK_COUNTER`): yapı BEAR
  iken LONG, BULL iken SHORT açılmaz. Motorda rejim kapısının HEMEN ARDINDA,
  `_evaluate_symbol` içinde — yani C ve TV sinyalleri AYNI kapıdan geçer.
  Harness (`backtest.simulate_symbol`) AYNI saf fonksiyon çiftini
  (`structure_state_for` → `structure_gate_blocks`) AYNI pencerelerle çağırır
  (P1 paritesi; `tests/test_structure.py`).
- **Çıkış tetikleyicisi** (`SCALPER_STRUCTURE_EXIT=off|be|close`, varsayılan
  `off`): açık pozisyonun TERSİNE ve GİRİŞTEN SONRA bir CHoCH gelirse stop
  BE'ye çekilir (`be`) ya da pozisyon reduce-only MARKET ile kapatılır
  (`close`, `exit_reason="STRUCT_CHOCH"`). Canlı tarafta
  `engine._apply_structure_exits` safety turunda çalışır: mum isteği
  (sembol, aralık, limit) tarama turununkiyle BİREBİR aynıdır (KlineFetcher
  TTL önbelleğine düşer), tur başına EN FAZLA BİR aksiyon uygulanır (2026-08-14
  watchdog dersi), kapanış `_close_position_market` ile borsada DOĞRULANIR
  (fail-closed) ve `be` aksiyonu stopu piyasanın yanlış tarafına koyacaksa
  (borsada -2021) uygulanmaz. Harness'ta aynı kural `manage_position` içinde.
- **Gözlem:** kapı kapalıyken de her tarama turunda hesaplanır ve
  `/scalper/status` → `structure` (sembol → `direction/last_event/age_bars`)
  alanında yayınlanır; hesap hatası taramayı DÜŞÜRMEZ (fail-open, tek sefer
  loglanır) — bir sinyal filtresi, güvenlik kilidi değildir.
### 4.1 Lider piyasa kapısı ("ters-gün kapısı", D15 — varsayılan KAPALI)

Rejim kapısının **hemen yanında**, aynı tek giriş noktasında
(`engine._market_gate_reason`, `_evaluate_symbol` içinde — yani C taraması
VE TV `external_signal` aynı anda). Rejim kapısından farkı: rejim kapısı
sembolün **kendi** EMA50/200 trendine bakar; bu kapı yalnız **lider**
sembole (`scalper_market_gate_symbol`, varsayılan BTCUSDT) bakar ve kararı
tüm evrene uygular. Saf kural `src/strategies/scalper/market_gate.py`
(`evaluate_market_gate`, IO yok) — canlı motor ve backtest harness'ı
(`backtest.simulate_symbol` + `LeaderSeries`) **aynı fonksiyon nesnesini**
çağırır (parite: `tests/test_market_gate.py::TestEngineHarnessParity`).

İki bağımsız alt-kapı (her biri kendi yüzdesi `0` yapılarak kapatılır):

- **gün-içi** (`scalper_market_gate_day_pct`): lider son kapanışı gün
  açılışının ≥%X **altındaysa** yeni LONG, ≥%X **üstündeyse** yeni SHORT
  açılmaz.
- **uzama** (`scalper_market_gate_run_pct` / `_run_days`): lider son N
  **tamamlanmış** günde ≥+%Y koştuysa LONG, ≤−%Y düştüyse SHORT açılmaz
  (koşu = `kapanış[-1]/kapanış[-1-N] − 1`, yani N+1 kapanış gerekir).
  **Kullanılmamalı:** iki bağımsız ölçüm (E7 harness + E8 canlı defter) bu alt-kapıyı
  desteklemiyor; `RUN_PCT>0` ile açılırsa motor başlangıçta WARNING basar (D15).

"Gün açılışı" iki tarafta da `market_gate.resolve_day_open` ile bulunur:
önce **gerçek açılış** — o günün 00:00 UTC `15m` mumunun `open`'ı, ki `1d`
mumunun `open`'ına birebir eşittir (ikisi de aralığın ilk işlem fiyatı;
ölçüldü: BTCUSDT mainnet+testnet, 76 gün sınırı, 0 uyuşmazlık). Bu yol
`_drop_unclosed`'a hiç dokunmaz (o 15m mumu çoktan kapanmıştır), yani
oluşmakta olan GÜNLÜK mumu görmeye gerek kalmaz. Günün ilk 15 dakikasında
(mum henüz kapanmamış — look-ahead yasak) **iki taraf da** son tamamlanmış
günlük kapanış vekiline düşer; hangisinin kullanıldığı `/scalper/status`
`market_gate.day_open_source` alanındadır.

REST ağırlığı: lider **başına** ~60 sn TTL önbellek (`_MARKET_GATE_CACHE_TTL`),
sembol başına değil — tarama turu başına en çok **3 istek**: `1d`
(limit `RUN_DAYS+5`, tavan 100), giriş TF (limit 3) ve `15m` (limit 100);
üçü de limit ≤ 100 olduğu için ağırlık 1. Kapı AÇIKKEN anlık görüntü her
tarama turunun başında tazelendiği için maliyet **≈3 ağırlık/dakika**
(bütçe 2400/dk); kapalıyken **tek istek bile gitmez** — yani alt sınır
0'dan 3'e çıkar, "değişmez" değil.
Lider verisi alınamazsa kapı **uygulanmaz** (fail-open) ve oran-sınırlı WARNING
loglanır — lider verisinin gelmemesi bir risk olayı değildir. Fail-open GÖRÜNÜR:
lider sembolü başlangıçta exchangeInfo'da doğrulanır (yoksa ERROR + "degraded"),
başarısızlık `SCALPER_MARKET_GATE_RETRY_SEC` boyunca negatif önbelleğe alınır
(boşa REST + paylaşılan kline kilidi), ve `/scalper/status` →
`market_gate.gate_effective` kapının GERÇEKTEN koruyup korumadığını söyler —
`enabled` bunu söylemez (D15). `gate_effective` BEŞ şartı birden ister:
`enabled` + lider doğrulandı + en az bir BAŞARILI anlık görüntü + görüntü
BAYAT değil (yaş ≤ 2 × tarama aralığı, UTC günü dönmemiş) + en az bir eşik > 0. Anlık görüntü
tarama turu başında bir kez tazelenir (tur içi tüm semboller aynı görüntüyü
kullanır) ve önbellek UTC gün damgasıyla anahtarlanır. `/scalper/status`
`market_gate` alt-sözlüğü (enabled/gate_effective/leader/leader_ok/
leader_source_host/thresholds/stale/snapshot_age_sec/day_drift_pct/
run_drift_pct/day_open_source/last_ok_at/last_error/last_failure_at/
consecutive_failures/failures_total/last_reason/last_block_at/rejects)
teşhis için dışa verilir; harness'ta engellenen sinyaller
`missed_counter["market_gate_day"/"market_gate_run"]` altında raporlanır.
Ölçüm ve P2 hükmü: `docs/EXPERIMENTS.md` "2026-08-23 — Lider piyasa kapısı (E7)".

## 5. Çıkış mimarisi

Tümü `src/strategies/scalper/exits.py`'de, `ExitManager.step()` her `symbol`
için `_step_one` çağırır (`exits.py:133-181`):

1. **TP1** (`_check_tp1:185`): canlı miktar `filled*(1-tp1_frac*0.9)`
   eşiğinin altına düşerse, gerçek algo child-order fill'i
   `_confirmed_algo_fill` (`exits.py:288-357`) ile borsa `userTrades`
   satırlarından kanıtlanır (miktar tahmini SAYILMAZ) → SL,
   `fee_aware_breakeven_price` (`types.py:176-230`, komisyon+buffer'ı
   cebirsel karşılayan seviye) hedefine çekilir, `trailing_active=True`.
2. **TP2** (`_check_tp2:232-275`): aynı doğrulama deseni; onaylanınca runner
   tabanı `runner_floor_price` (=TP1 fiyatı) seviyesine yükseltilir.
3. **Chandelier trailing** (`_update_trailing:359-417`, yalnız
   `trailing_active`): `chandelier_stop` (`indicators.py:280-301`) —
   LONG: `max(high[since_entry:]) - atr_mult*ATR(14)`; çarpan
   `resolve_trail_mult(cfg, sp.mfe_pct)` (`types.py:152-173`) — tepe ROI
   `scalper_trail_relax_roi1/2_pct` eşiklerini geçtikçe kademeli büyür
   (tek yönlü, geri çekilmede sıkılaşmaz). Stop yalnız lehte kayar (`floor`
   ile `max`/`min`, `exits.py:398-404`); değişim önce yeni SL sonra eski
   iptal deseniyle boşluksuz uygulanır (`pm.replace_stop_loss`).
4. **Reaper** (`_reap_aged_positions`, `engine.py:602-663`): `sp.
   trailing_active=True` olan pozisyonlar MUAF (BE korumalı koşucu — "bugün
   kesilen trend yarın devam edebilir"). Yalnız BE'ye hiç ulaşmamış, `age_h
   >= scalper_max_hold_hours` (0=kapalı) pozisyonları reduce-only MARKET ile
   kapatır; tur başına en fazla 1 kapanış (`engine.py:661`, watchdog
   restart'ı önlemek için).
5. **Stop modları** (`setups.apply_stop_policy:87-142`): `structural`
   (yapısal swing + ATR taban `apply_stop_atr_floor:55-79`) veya `fixed_roi`
   (mesafe `= fixed_stop_roi_pct/kaldıraç`, likidasyon tamponu için `%70`
   tavanla kırpılır — `_FIXED_STOP_ROI_CAP_PCT`, `setups.py:82,123-128`).
   `config.py:342-390` startup'ta `fixed_roi` + `min_rr`/`min_stop_pct`/
   `max_stop_pct` tutarsızlığını fail-fast reddeder.
6. **min_rr kapısı**: `executor.try_open` adım 3 (`executor.py:733-753`) —
   beklenen harman ROI / (stop_mesafesi×kaldıraç) `< scalper_min_rr` ise
   sinyal reddedilir (`0` = kapalı).
7. **Kayıp cooldown**: `_maybe_start_loss_cooldown` (`exits.py:86-107`) →
   `executor.start_loss_cooldown` (`executor.py:586-605`) — SL veya net
   negatif kapanışta sembolü `scalper_loss_cooldown_minutes` süre kilitler;
   mevcut daha uzun bir cooldown asla kısaltılmaz (`_set_cooldown:548-560`).

### 5.0 `-2021` sonrası acil kapanışın kaydı: `TRAIL_MARKET` / `BE_MARKET` (D22)

Koruyucu stop bir SEVİYE üretir (chandelier izi, runner tabanı, break-even);
bu seviyeyi borsaya `STOP_MARKET` olarak koymak her zaman mümkün değildir.
Piyasa seviyeyi çoktan geçmişse Binance `-2021 Order would immediately
trigger` döner ve `position_manager._replace_stop_loss` bunu bir çıkış kararı
sayıp `_emergency_close` ile pozisyonu reduce-only MARKET ile kapatır — **bu
davranış D22'den ÖNCE de vardı ve korunmuştur.** Kusur KAYITTAYDI: exits bunu
`False` diye okuyup "eski SL korunuyor" logluyor, kapanış ise sonraki turda
`TRAIL` olarak deftere giriyordu (2026-08-23, 3 olay).

Akış artık şudur (`exits._update_trailing`, `_check_tp1`, `_check_tp2`,
`force_breakeven`, `force_stop_to`, takipçi `_check_tp1_breakeven`):

```
seviye → [ayrı host] koruma tarafında mı?  hayır → tur atla (emir YOK)
       → pm.replace_stop_loss_result(...)
            replaced         → SL güncellendi
            no_position      → sessiz (pozisyon zaten yok)
            failed           → "eski SL korunuyor" (TEK doğru yer)
            emergency_closed → pozisyon ZATEN kapandı (pm, MARKET):
                 1. sayaç market_exits++, log "ACİL KAPANIŞ GERÇEKLEŞTİ"
                 2. etiket sp.pending_exit_reason'a ÇİVİLENİR
                    (TRAIL_MARKET | BE_MARKET)
                 3. kapanış emrinin kimliği/fiyatı sp'ye yazılır
                 4. _finalize_market_exit: SADECE flat doğrulaması
                    (get_position_risk force_fresh) → _handle_closed
                    ** İKİNCİ MARKET EMRİ YOK ** (-2022 yarışı)
                    doğrulanamazsa: SL/TP'ye DOKUNULMAZ, etiket kalır,
                    sonraki safety turu aynı etiketle finalize eder
```

`StopReplaceResult` ve `EmergencyCloseResult` (`position_manager.py`)
`__bool__` ile eski `bool` sözleşmesini korur — tüm mevcut çağıranlar ve test
çiftleri değişmeden çalışır. Kapanış FİYATI da tahmin edilmez:
`_verified_close_ledger` yalnız ALGO adaylarına baktığı için düz MARKET
kapanışını göremez; `_market_close_exit_price` emrin `userTrades` VWAP'ını
(yoksa `avgPrice`i) okur ve notu `exit_fill=market_close_order` olur. Income
doğrulama merdiveni DEĞİŞMEDİ.

`TRAIL_MARKET`/`BE_MARKET` **TRAIL ailesindendir**
(`forensics.exit_reason_family`) ama defter/raporda **ayrı sayılır**;
sayılarının artması "stop kararı piyasa hızının gerisinde" demektir.
Telemetri: `/scalper/status.trailing_skips` =
`{price_space_skips, protective_gate_skips, market_exits}`.

### 5.1 İşlem adli kaydı (trade forensics, D21) — YALNIZ GÖZLEM

Her scalp işleminin "neden girildi / nasıl çıkıldı / ne ters gitti" kaydı tek
bir JSON belgesinde toplanır. **Hiçbir kapı, boyutlama ya da çıkış kararı bu
belgeyi OKUMAZ**; emir akışı D21 öncesiyle birebir aynıdır (bkz.
`docs/DECISIONS.md` D21).

Belge şekli (`scalp_trades.forensics`, TEXT/JSON; eski satırlarda NULL):

```json
{"v":1, "entry":{...}, "exit":{...}, "verdict":["counter_drift_long"], "postmortem":{...}}
```

| Bölüm | Ne zaman yazılır | İçerik (özet) |
|---|---|---|
| `entry` | `executor._finalize_position` (GERÇEK dolumdan sonra) | zaman, kaynak (`C`/`TV` + oy veren TV kaynakları/oy yaşları/pencere), `signal_reason`, C girdileri (RSI giriş/bağlam, BB %B, diverjans, ATR%), rejim + EMA50/200, lider kapısı anlık görüntüsü (gün sapması %, üç-durumlu `verdict`), yapı/TV-yapı durumu, geçilen-atlanan kapılar, kline kaynağı, sinyal fiyatı vs dolum (kayma %), kaldıraç/marj/nominal, stop mesafesi % ve ROI, TP1/TP2, R:R, o anki açık pozisyon sayısı, günlük PnL, BTC fiyatı |
| `exit` | `exits._finalize_close` | zaman, neden, çıkış yolu (TP1/TP2 anı, BE anı+fiyatı, trailing güncelleme sayısı ve son stop, ilk/son stop, yaş), MAE/MFE (ROI ve fiyat %), süre, net/brüt PnL + ücret tahmini, `pnl_source`, kapanış anındaki sembol rejimi ve lider gün sapması |
| `verdict` | girişte (giriş etiketleri) → kapanışta (tam liste) | kural tabanlı etiketler, aşağıdaki tablo |
| `postmortem` | kapanıştan `SCALPER_FORENSICS_POSTMORTEM_MIN` dk SONRA | pencerede fiyat girişe döndü mü, kaç dakikada, pencerede en iyi hareket |

**Etiket kuralları** (`forensics.classify_*`, saf fonksiyonlar; her biri için
pozitif ve negatif test vardır):

| Etiket | Aşama | Kural |
|---|---|---|
| `counter_drift_long` | giriş | lider gün sapması ≤ −`COUNTER_DRIFT_PCT` iken LONG |
| `relief_rally_short` | giriş | lider gün sapması ≥ +`COUNTER_DRIFT_PCT` iken SHORT |
| `late_entry_after_run` | giriş | lider çok-günlük koşusu ≥ `RUN_PCT` ve giriş AYNI yönde |
| `tv_single_family` | giriş | sağlama ≥2 oyla doldu ama tüm kaynaklar AYNI aileden (`luxso_*` → `luxalgo`) |
| `stale_signal` | giriş | sinyal → dolum > `STALE_SIGNAL_SEC` |
| `gate_bypassed` | giriş | lider kapısı AÇIK ama `gate_effective=false` (fail-open) iken girildi |
| `fee_dominated` | çıkış | brüt > 0 ve net < `FEE_RATIO` × brüt |
| `mfe_giveback` | çıkış | tepe ROI ≥ TP1 hedefini gördü ama net < 0 |
| `noise_stop` | post-mortem | zararla/SL ile kapandı VE pencerede fiyat girişe LEHTE geri döndü |

**Look-ahead yoktur.** `entry`/`exit` yalnız o anda bilinen değerleri taşır.
`noise_stop` ancak kapanış SONRASI ölçülebilir; bu yüzden AYRI `postmortem`
alanındadır, `forensics.postmortem_from_candles` yalnız `closed_at`'ten SONRA
kapanmış mumlara bakar (daha eskiler açıkça elenir) ve sonuç hiçbir karar
yolunda okunmaz.

**Restart davranışı (D21-R3):** `exits.recover()` DB'deki `forensics.entry`
bölümünü belleğe GERİ YÜKLER (`_restore_forensics_entry`), bu yüzden restart
sonrası kapanan bir işlemin `verdict`i giriş etiketlerini de taşır. Yalnız
BELLEKTE tutulan çıkış zaman çizgisi damgaları (TP1/BE anı, trailing sayacı ve
son trail stopu) restart'ta gerçekten kaybolmuştur: kapanış belgesi bunları
`null` bırakır ve `exit.path.restart_gap = true` ile nedenini söyler — `0`
yazmak "hiç olmadı" demek olurdu, yani uydurma. `path.initial_stop` de
kurtarmadaki CANLI stop yerine giriş belgesindeki GERÇEK ilk stoptan gelir.
`price_ts` KASITLI geri yüklenmez (karar-yolu tazelik damgası, D19a-2).
`tracker.record_close` `verdict`i ÜZERİNE YAZMAZ, mevcutla BİRLEŞTİRİR. Maker
modunda giriş bağlamı dolum anına kadar bellekte bekler
(`executor._pending_forensics`, `sembol|yön|created_at_ms` kimliğiyle
damgalıdır — kimlik tutmazsa bağlam atılır); restart'ta kaybolur ve o tek
işlemin kaydı eksik kalır — işlem akışı ETKİLENMEZ.

**REST maliyeti:** giriş/çıkış tarafında **sıfır** ek istek — bağlam yalnız
senkron anlık görüntülerden (`_market_gate_status`, `tv_events.snapshot`,
`_kline_source_snapshot`) ve `StrategyContext`'te ZATEN çekilmiş serilerden
türetilir (`structure.py` ile aynı ilke). Tek ek istek post-mortem turundadır:
`engine._forensics_postmortem_tick` safety turundan TETİKLENİR ama **AYRI bir
task'ta** koşar (D21-R3 — tur onu beklemez), **dakikada en fazla bir kez** ve
**tur başına EN FAZLA BİR sembol** için `SCALPER_TF_ENTRY` (varsayılan `5m`)
limit 150 → **ağırlık 2** çeker; istek `asyncio.wait_for` ile **5 sn**'de
kesilir. Üst sınır **saatte 60 istek / 120 ağırlık** (ortalama 2 ağırlık/dk);
ölçülecek kapanış yoksa sıfır istek, yani §2 ağırlık tablosunu anlamlı biçimde
değiştirmez. Host geneli bir piyasa-verisi kesintisinde tur hiç başlatılmaz
(`exits._market_data_down_reason` ya da `MarketDataGuard.blocked_until`).
`SCALPER_FORENSICS_POSTMORTEM_MIN=0` bu turu tamamen kapatır.

**Okuma yolları:** `GET /scalper/trades/{id}/forensics` (tek işlem),
`GET /scalper/forensics/recent?limit=` (liste),
`GET /scalper/forensics/summary?since=7d` (etiket × sonuç), pano "Son
İşlemler" satırındaki adli kart + "Neler Etkiliyor" paneli,
`scripts/ledger_report.py --forensics`, ve `logs/trades.jsonl`
(satır başına tek JSON: `entry`/`exit`/`postmortem`; günlük rotasyon, 30 gün).

## 6. Kalıcı durum ve dosyalar

- **DB**: `settings.database_url` (varsayılan `sqlite:///./tradingbot.db`,
  `config.py:91`) — WAL modu, `busy_timeout=5000` (`database.py:38-42`).
  Ana tablo `scalp_trades` (`src/models/scalp_trade.py:18`); create_all sonrası
  eksik kolon idempotent tamamlanır (`database.py:76-85`, örn.
  `entry_order_id`).
- **state/**: `scalper_cooldown_state_path` (varsayılan
  `state/scalper_cooldowns.json`, atomik tmp+fsync+replace yazım,
  `executor.py:520-546`), `scalper_entry_halt_path` (varsayılan
  `state/scalper_entry_halt.json`, fail-closed kalıcı giriş kilidi —
  `engine.py:298-371`), `scalper_pending_journal_path` (maker LIMIT
  niyetlerinin restart-güvenli journal'ı, `executor.py:198-287`),
  `risk_event_halt_path` (varsayılan `state/risk_event_halt.json`, TTL'li
  fail-closed risk-olayı kilidi — bkz. altta ve `docs/INTEGRATIONS.md` §3).

**Risk-olayı kanalı** (`POST /risk-event`, D10, `docs/DECISIONS.md`): haber/olay
botlarının giriş kapılarını durdurup/devam ettirebildiği veya tüm izlenen pozisyonları
acilen düzleştirebildiği yol, motor mantığına DOKUNMADAN eklendi. `risk_event_halt_path`,
`scalper_entry_halt_path`'ten BİLİNÇLİ olarak AYRI bir dosyadır ve `scalper_entry_halt_enabled`
bayrağından (yalnız `UnprotectedPositionError` otomatik latch'ini gater; canlı sunucuda
`false`) TAMAMEN BAĞIMSIZ, her zaman uygulanır — `_entries_ready()` (`engine.py:483-501`,
scanner + `external_signal`'in TEK ortak kapısı) `_risk_event_halt_snapshot()`'ı (~1sn TTL
önbellekli, dosya bozuksa/parse edilemezse fail-closed HALT AKTİF) sorar. `flatten`
aksiyonu, reaper'ın (`_reap_aged_positions`) kullandığı reduce-only MARKET emrini
(`_submit_reduce_only_market_close`) yeniden kullanır — yeni bir emir yolu yoktur — ve her
sembolün kapanışını borsa üzerinde doğruladıktan SONRA `exits._handle_closed`'ı
`forced_exit_reason="RISK_EVENT"` ile çağırır; doğrulanamayan sembol izlemede kalır
(SL/TP asla doğrulanmadan iptal edilmez). Backtest harness'ına BİLİNÇLİ olarak
dokunulmadı — risk-olayları yalnız canlı motoru etkiler.
- **logs/trades.jsonl** (D21): işlem adli kaydının append-only olay akışı —
  satır başına TEK JSON (`event` = `entry`/`exit`/`postmortem`). Loguru'dan
  AYRIDIR (`trades.log` insan-okur bir denetim izidir, makine sözleşmesi
  değildir), günlük rotasyonludur (`trades-<YYYY-MM-DD>.jsonl`) ve 30 gün
  saklanır. Secret İÇERMEZ. Dizin `TRADINGBOT_LOG_DIR` ile değiştirilir
  (testler prod izini kirletmesin).
- **logs/**: `logs/` dizini repoda mevcut (115 girdi görüldü); loguru
  yapılandırması `src/core/logger.py`'dedir — içerik bu görevde satır bazlı
  incelenmedi (kapsam dışı, ana odak scalper akışı).
- **backups/**: bu repoda `.env.bak.20260807_104143` gibi zaman damgalı `.env`
  yedekleri kök dizinde bulundu; sunucuda ayrıca `/opt/tradingbot-v2/backups/`
  klasörü vardır (`env.bak-<tarih>-<etiket>`, `commit.prev-<tarih>`; deploy
  script'i ve her elle .env değişikliği buraya yazar — 2026-08-21 doğrulandı).
- **.env**: asla commit edilmez (`.gitignore`); `Settings` bunu okur
  (`config.py:31-35`, `env_file=".env"`). Sunucudaki `/opt/tradingbot-v2/.env`
  bu repodan bağımsızdır (bkz. §7 "sunucu env'i" kuralı).

## 7. Backtest harness

`src/strategies/scalper/backtest.py`, CLI (`_build_arg_parser:1348-1367`):

```
python -m src.strategies.scalper.backtest --days 30 \
    --symbols BTCUSDT,ETHUSDT,SOLUSDT --strategies A,B,C
python -m src.strategies.scalper.backtest --start 2026-01-23 --end 2026-02-13 \
    --symbols BTCUSDT --strategies C
```

`--symbols auto` → `UniverseScanner` mainnet ilk 8 (top_n); `--start/--end`
verilirse `--days`'i geçersiz kılar (`main_async:1372-1381`).

Simüle edilenler: maker giriş (`_find_maker_fill`, satır 367-, `scalper_
entry_mode=maker` iken LIMIT dolum + timeout iptali), `fixed_roi` stop modu ve
coin-bazlı dinamik kaldıraç (canlı `apply_stop_policy` AYNEN çağrılır —
`backtest.py:49`), rejim kapısı paritesi (canlı `_evaluate_symbol` ile aynı
mantık — commit `7640c0a "rejim kapısı paritesi"`). Sembol başına backtest'te
**tek eşzamanlı pozisyon**; semboller-ARASI `scalper_max_positions` kapasite
kapısı ise `run_backtest`'te tüm sembollerin adayları birleştikten SONRA,
kronolojik tek bir geçişle uygulanır (`_apply_capacity_gate`, parite ile
canlı `_evaluate_symbol` kapasite kuralı — post-hoc, sembol-içi değil).

Çıktı: konsol tablosu (strateji × işlem/kazanma%/PnL/profit factor/max
drawdown/…, `print_report:902-948`) + iki kırılım tablosu: **REJİM
KIRILIMI** (`_print_regime_breakdown:957-987`) ve **YÖN/ÇIKIŞ NEDENİ
KIRILIMI** (`_print_grouped_breakdown` çağrıları, `print_report:948-955`);
ayrıca JSON rapor (`write_json_report`, `run_metadata["regime_breakdown"]`
`backtest.py:1118-1153`).

Kural: backtest sunucu `.env`'iyle koşulmalı (yerel `.env` bayat olabilir).
Kullanıcı bağlamındaki komut deseni:
```
env $(ssh awa grep ^SCALPER_ /opt/tradingbot-v2/.env | xargs) \
    python3 -m src.strategies.scalper.backtest --days 30 --symbols BTCUSDT --strategies C
```
Bu SSH/ortam deseni `CLAUDE.md` ve `docs/EXPERIMENTS.md`'de standarttır;
pencere tarihleri ve karar kuralı `docs/DECISIONS.md` P2'de.

## 8. Dış entegrasyonlar

- **Binance Futures REST/WS**: `ImprovedBinanceClient`
  (`src/trading/binance_client_improved.py:88`). Testnet/mainnet anahtarı
  `binance_base_url` + `TESTNET_HOSTS` listesi (`config.py:16-21,313-323`);
  `is_testnet=False` iken `allow_mainnet=True` açıkça gerekir yoksa
  `Settings()` `ValueError` fırlatır (`config.py:407-418`).
  Koşullu emirler (STOP_MARKET/TAKE_PROFIT_MARKET) 2025-12-09'dan itibaren
  `/fapi/v1/algoOrder` üzerinden gider, eski `/fapi/v1/order` -4120 ile
  reddeder (`binance_client_improved.py:958-972`).
- **TradingView alertleri**: kullanıcı TV'de alert kurar; webhook `/tv-signal`
  (`main.py:613`), secret gövdede veya `?secret=` query'de (TV header
  gönderemez). Kaynak etiketi `?src=` — kullanıcı bağlamına göre
  `luxosc`/`luxso`/`algopro`/`botv3` gibi değerler kullanılıyor; bu isimler
  kodda sabit/whitelist DEĞİL, serbest string olarak `TvConfluence`'a
  kaynak kimliği olarak geçiyor (`main.py:643-648`, `tv_confluence.py:52`).
- **Telegram**: `python-telegram-bot` tabanlı `TelegramBotService`
  (`telegram_bot.py:27`) — VIP kanal sinyali ayrıştırma (`handle_channel_
  post:110`) + `/status /positions` bilgi komutları (satır 70-108); ayrıca
  `send_notification` ile bildirim gönderir (satır 159-169).
- **Dashboard**: `GET /dashboard` (`main.py:232-239`) `static/dashboard.html`
  dosyasını servis eder; FastAPI süreci `settings.api_port`'ta dinler
  (yerel `.env`: `8080`; `config.py` varsayılanı `8000`). Sunucuda
  port **9091** supervisord komut satırında açıkça verilir (`--port 9091`,
  2026-08-21 doğrulandı) ve yalnız 127.0.0.1'e bağlıdır; dashboard'a
  `ssh -L 9091:127.0.0.1:9091 awa` tüneliyle erişilir (bkz. RUNBOOK).

## 9. Bilinen tuzaklar (kod seviyesinde)

- **algoOrder geçişi**: STOP/TAKE_PROFIT emirleri `/fapi/v1/order` yerine
  `/fapi/v1/algoOrder`'a taşınmış; yanıtta `orderId` yerine `algoId` gelir,
  istemci bunu `orderId` takma adıyla eşler (`binance_client_improved.py:
  958-972`).
- **Rate limiter yarışı**: eski kilitsiz check-then-act, N coroutine'in aynı
  beklemeyi hesaplayıp aynı anda istek atmasına yol açıp 418 ban'ı
  tetikliyordu; `asyncio.Lock` ile slot atomik rezerve edilir
  (`src/core/rate_limiter.py:5-11,49-59`, commit `8321ddf`).
- **Dashboard force-fresh açlığı**: `/api/status`'un her 5 sn'de bir
  `force_fresh=True` ile pozisyon sorgusu ataması, rate-limiter kuyruğunu
  doyurup scan döngüsünü açlığa itiyordu (2026-08-18 watchdog restart kökeni);
  düzeltme `force_fresh=False` + 15sn account önbelleği
  (`main.py:412-419`, `binance_client_improved.py:1424-1441`).
- **Public kline yolu ban körlüğüydü** (D17'de düzeltildi): `KlineFetcher` ve
  `UniverseScanner` `rate_limiter`'ı, ban kesicisini ve ağırlık başlığını HİÇ
  kullanmıyordu — 418 alan bir kline çağrısı 3 kez tekrar deniyor (yasağı
  uzatıyor) ve deploy'un `HTTP 418|banned` kilidine görünmüyordu.
  `MarketDataGuard` (data.py) bunu host başına kapattı; `UniverseScanner`
  hâlâ guard DIŞINDADIR (saatte 1 istek, `ticker/24hr` ağırlık 40).
- **Ayrı market-data host'unda sembol kapsamı**: evren işlem host'undan gelir;
  işlem host'unda olup market-data host'unda OLMAYAN bir sembol her taramada
  TEK bir kline hatası üretir (`MarketDataRequestError`, tekrarsız — sinyal
  üretilmez, tur devam eder). Ayrı host kullanılırken `SCALPER_SYMBOL_ALLOWLIST`
  önerilir. Not: evren sıralaması işlem host'unun 24s hacmine göre yapılır —
  testnet'te bu hacim gerçekçi DEĞİLDİR (E8.0), yani allowlist boşken sembol
  SEÇİMİ sahte likiditeyle, KARAR gerçek piyasa mumlarıyla verilir.
- **Testnet ağırlık başlığı tutarsız**: `X-MBX-USED-WEIGHT-1M` testnet'te
  "edge-bazlı" (aynı dakikada 1912→375 görülmüş); uyarı eşiği bu yüzden
  gerçek 2400 sınırına yakın (`1800`) tutuluyor, mutlak değer değil trend
  sinyali olarak kullanılıyor (`binance_client_improved.py:398-415`,
  commit `eb96ec9`).
- **Kurtarılan (recovery) kapanış kayıtları güvenilmez olabilir**: restart
  sırasında borsada zaten kapanmış bulunan pozisyonlar için income→
  userTrades ledger→tahmini brüt PnL merdiveni uygulanır; hiçbiri
  doğrulanamazsa `exit_reason="UNKNOWN"` ve `notes` içine
  `exit_fill=unverified`/`close_verification=unverified` etiketi eklenir —
  "bilinmiyor" asla "kapandı"/"TP" diye maskelenmez (`exits.py:1000-1080`,
  `1061`).
- **"Bilinmiyor" asla "kapandı" sayılmaz**: `_step_one` pozisyon sorgusu
  hata verirse izleme O TUR atlanır, pozisyon "kapandı" varsayılmaz
  (`exits.py:147-155`).
- **Fail-closed giriş kilidi (entry halt)**: `UnprotectedPositionError`
  görülünce (`position_manager.py:32-33`) tüm yeni scalper girişleri kalıcı
  dosyaya (`state/scalper_entry_halt.json`) yazılıp durdurulur; mainnet'te
  bu kilit `False` yapılamaz — `config.py:420-429` startup'ta reddeder.
- **Reaper `trailing_active` muafiyeti**: BE'ye ulaşmış (TP1 dolmuş)
  pozisyonlar yaş limitine bakılmaksızın açık kalabilir; yalnız BE'siz
  yaşlı pozisyonlar reduce-only kapatılır (`engine.py:611-623`).
- **Tur başına tek reaper kapanışı**: 5 eşzamanlı reduce-only kapanışın
  safety turunu şişirip watchdog restart tetiklediği 2026-08-14 olayından
  sonra eklendi (`engine.py:661` yorum + `return`).

## 9b. İkinci çalışma modu: AlgoPro takipçisi (D20 ayrı halka / **D20b gömülü**)

> **D20b (TERCİH EDİLEN, kullanıcı kararı 2026-08-23):** `FOLLOWER_EMBEDDED=true`
> ile takipçi AYRI süreç/hesap OLMADAN, scalper'ın YANINDA aynı süreçte ve aynı
> Binance hesabında çalışır. Farklar: boyutlama **SANAL deftere** dayanır
> (`FOLLOWER_VIRTUAL_CAPITAL_USDT`, equity = taban + `scalp_trades` AP net PnL),
> AlgoPro gövdesi `/tv-signal`'dan HTTP köprüsü yerine **süreç içi** teslim edilir
> (ve ana botun sağlamasına oy VERMEZ), sembol çakışması süreç-içi
> `symbol_reservations` ile engellenir, `FOLLOWER_SYMBOLS` scalper'ın evreninden
> otomatik düşülür ve `/risk-event` iki motoru da kapsar. Aşağıdaki akış AYRI
> halka içindir; gömülü modda `F`/`E` adımlarının yerini tek bir süreç-içi çağrı
> alır ve defter `tradingbot.db`'dir. Ayrıntı: `docs/DECISIONS.md` D20b.

`BOT_MODE=follower` (varsayılan `scalper`) AYNI kod tabanını AYRI bir süreç/hesap
olarak çalıştırır: **scanner, strateji C ve TV sağlaması KAPALIDIR**; giriş ve çıkış
yalnız AlgoPro V1.6 alarmlarından gelir. `src/main.py` lifespan'ında erken bir dal
(`settings.is_follower_mode`) ScalperEngine/TradingOrchestrator/Telegram-VIP akışını
HİÇ kurmaz — orchestrator açık pozisyonları sahiplendiği için takipçiyle çakışırdı.

```mermaid
flowchart TD
    TV["TradingView\nAlgoPro V1.6 alert()"] --> W["ana bot :9091\n/tv-signal (secret)"]
    W -->|"src=algopro"| F["follower_forwarder\nfire-and-forget, 2sn"]
    W -->|"BUY/SELL"| SC["scalper: TvConfluence → external_signal\n(BUGÜNKÜ davranış)"]
    F --> E["takipçi :9093\n/follower/event (ayrı secret)"]
    E --> P["parser.parse_follower_event"]
    P --> L["levels.resolve_levels\n(mesaj > k×ATR)"]
    L --> PL["plan.build_plan\nmarj %10 + dinamik kaldıraç"]
    PL --> X["FollowerExecutor\nMARKET → SL → 3× TP"]
    X --> XM["FollowerExitManager\nTP1→BE, kapanış defteri"]
    XM --> DB["scalp_trades (strategy=AP)\ntradingbot_ap.db"]
```

| Dosya | Sorumluluk |
|---|---|
| `src/strategies/follower/types.py` | veri sözleşmesi (`FollowerEvent`, `FollowerLevels`, `FollowerPlan`, `LeverageBracket`) |
| `src/strategies/follower/parser.py` | AlgoPro alert gövdesi → olay (saf); birincil biçim `\|` ayraçlı `Anahtar: değer`, ikincil `kind=…` şablonu |
| `src/strategies/follower/levels.py` | SL/TP çözümü (saf): **birincil** mesaj seviyeleri, **yedek** `k×ATR` + RR katları |
| `src/strategies/follower/plan.py` | marj/kaldıraç/miktar (saf) + borsa dilimi & likidasyon kapıları |
| `src/strategies/follower/brackets.py` | `/fapi/v1/leverageBracket` TTL önbelleği (fail-closed) |
| `src/strategies/follower/executor.py` | korumalı açılış: MARKET → `pm.place_stop_loss_or_close` → 3× reduce-only TP → defter |
| `src/strategies/follower/exits.py` | `ExitManager` alt sınıfı: TP1→BE, TP2/TP3 telemetri, kapanış defteri, restart kurtarma |
| `src/strategies/follower/risk_halt.py` | D10 risk-olayı halt'ının takipçi kopyası (TTL, fail-closed, RAM latch) |
| `src/strategies/follower/engine.py` | kapılar, kill switch, safety/readiness döngüleri, `/follower/status` |
| `src/services/follower_forwarder.py` | ana bottaki köprü (yalnız `src=algopro`, secret başlıkta) |

**Yeniden kullanılanlar (yeniden YAZILMADI):** `ImprovedBinanceClient`,
`PositionManager` (SL kurulamazsa acil kapatma), `ScalpTracker`/`scalp_trades`,
`ExitManager`'ın kapanış doğrulama merdiveni (income → userTrades → tahmini),
`_confirmed_algo_fill` fill kanıtı, `fee_aware_breakeven_price`, `/risk-event` kanalı.

**Boyutlama (scalper'dan FARKLI, kullanıcı kararı 2026-08-23):** marj = bakiyenin
`%FOLLOWER_MARGIN_PCT`'i; kaldıraç `clamp(round(FOLLOWER_SL_ROI_TARGET / sl_pct),
LEV_MIN, LEV_MAX)` — yani stop DAİMA marjın ~%30'u. Üstüne borsa kaldıraç dilimi,
`lev × sl_pct ≤ 50` ve `1/lev − mmr > 2 × sl_pct/100` kapıları (hepsi yalnız DÜŞÜRÜR).

**Scalper halkasına dokunulan yerler (davranış NÖTR):** `ExitPlan`'a varsayılanı
0/None olan `tp3_*` alanları; `_verified_close_ledger`'a opsiyonel `tp3_algo_id`
adayı; `ScalpTradeModel.tp3_algo_id` sütunu (idempotent migration) ve
`record_open(tp3_algo_id=None)`. Scalper bu alanları HİÇ doldurmaz.

## 10. Sözlük

| Terim | Anlam |
|---|---|
| **C** | Strateji varyantı "Saf Uç Avcısı": rejim filtresiz, RSI ucu + Bollinger taşması + diverjans → ters yönde giriş, `risk_multiplier=0.5` (`setups.py:431-565`) |
| **Rejim kapısı** | `DOWN`'da LONG, `UP`'ta SHORT sinyalini engelleyen kural (`engine.py:815-834`) |
| **Lider piyasa kapısı** | Liderin (BTCUSDT) gün-içi sapmasına ve çok-günlük koşusuna bakıp o yöne yeni giriş kapatan kural (§4.1, `market_gate.py`; varsayılan KAPALI) |
| **Sağlama / confluence** | Birden çok farklı TV göstergesinin aynı yönde oy vermesi şartı (`tv_confluence.py`) |
| **Reaper** | Yaş limitini aşan, BE korumasız pozisyonları reduce-only MARKET ile kapatan görev (`engine.py:602`) |
| **Chandelier** | ATR tabanlı, yalnız lehte kayan trailing stop (`indicators.py:280`) |
| **BE (break-even)** | TP1 dolunca SL'nin komisyon+buffer karşılayan sıfır-net seviyeye çekilmesi (`types.py:176`) |
| **TP ladder** | TP1 (%40, +20% ROI) → TP2 (%30, +50% ROI) → runner (%30, chandelier) merdiveni |
| **Entry halt** | `UnprotectedPositionError` sonrası kalıcı, dosya-tabanlı, fail-closed yeni-giriş kilidi |
| **Close ledger** | `_CloseLedger` — borsa `userTrades` satırlarıyla kanıtlanmış kapanış özeti (`exits.py:45-51`) |
| **LUCID** | Bu kod tabanında bir referans/isim olarak **bulunamadı** — kodda doğrulanamadı |
| **luxosc / luxso** | TV alert kaynak etiketleri (`?src=`); kodda sabit tanım/whitelist yok, serbest string (bkz. §8) |
| **fixed_roi** | Stop modu: mesafe = `fixed_stop_roi_pct / kaldıraç` (marj-yüzdesi stop) |
| **Maker entry** | `scalper_entry_mode=maker`: LIMIT GTX (post-only) iki fazlı giriş, `check_pending` ile dolum takibi |
| **Allowlist** | `scalper_symbol_allowlist` (evren) / `scalper_tv_symbol_allowlist` (TV) — CSV sembol filtresi |
| **MAE/MFE** | Maximum Adverse/Favorable Excursion — pozisyon ömrü boyunca en kötü/en iyi ROI% ucu (`exits.py:942-953`) |
