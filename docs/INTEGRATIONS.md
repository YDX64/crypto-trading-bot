# Yeni sinyal kaynağı ekleme sözleşmesi (haber botları, dış modeller, başka göstergeler)

Bu sistemde **motor tektir**: giriş kapıları (rejim, kapasite, cooldown, equilibrium), risk
boyutlama, stop/TP/BE/trailing/reaper ve kapanış defteri yalnız `src/strategies/scalper/`
içindedir. Dışarıdan eklenen her şey — TradingView alarmı, haber botu, LLM yorumcusu —
motora **yalnız bir yön önerisi** verir. Bu sayede yeni bir bot eklemek motoru değiştirmez,
bozmaz; bozarsa tek bir yerde görünür.

## 1. Kaynak = webhook + `src` etiketi (değişiklik gerektirmez)
```
POST http://<sunucu>/tv-signal?secret=<TV_WEBHOOK_SECRET>&src=<kaynak>
Content-Type: application/json
{"symbol": "BTCUSDT", "side": "buy"}          # side: buy|long|bull  /  sell|short|bear
```
- `src`: `TV_SOURCE_ALLOWLIST` içinde olmalı (varsayılan `luxosc,luxso,algopro,botv3,tv` +
  D19 olay kaynakları `luxso_exit,luxso_trend,pac_choch,algopro_tp1` — bkz. §7).
  Yeni kaynak = bu listeye bir isim eklemek (`.env`), başka hiçbir kod değişmez. Listede
  olmayan `src` uyarıyla `tv`'ye eşlenir (sinyal düşmez, ama ayrı kaynak sayılmaz).
- Sağlama (`src/services/tv_confluence.py`): aynı sembol + aynı yön için **2 FARKLI kaynak**,
  420 sn penceresi; ters yön oyu gelince önceki oylar sıfırlanır. Tek başına hiçbir kaynak
  işlem açamaz — haber botu da. Bu, yeni kaynağın sistemi tek başına bozamaması demektir.
- Kabul edilen sinyal `engine.external_signal` → aynı kapılar, aynı risk, aynı çıkışlar.
- Yanıt JSON'u: `accepted`, `confluence` (oy durumu), `source`. 403 = secret yanlış,
  422 = sembol/yön çözülemedi, 503 = motor hazır değil.

## 2. Haber/olay botları için kurallar
1. Bot **asla** Binance'e doğrudan emir vermez; yalnız webhook'a yön gönderir.
2. Her bot kendi `src` adıyla gelir (`news_macro`, `news_llm`…). Aynı botun iki farklı
   modeli iki ayrı kaynak SAYILMAZ (aynı veriden türeyen oylar sağlamayı yanıltır) —
   tek `src` kullan.
3. Yön önerisi yalnız allowlist sembolleri için anlamlıdır (`SCALPER_TV_SYMBOL_ALLOWLIST`).
4. "Savaş çıktı, her şeyi kapat" tipi olaylar için yön sinyali DEĞİL, risk olayı gerekir —
   `POST /risk-event` (bkz. 3, AKTİF).
5. Bot kendi loglarını tutar; motor loglarında `Sağlama oyu: … ← <src>` satırıyla izlenir.

## 3. Risk-olayı kanalı (`POST /risk-event`) — 2026-08-21'den beri AKTİF (D10)
Amaç: haber/olay botu "giriş durdur / devam et / her şeyi düzleştir" diyebilsin. Bu kanal
**yön sinyali göndermez** ve sağlamadan (tv_confluence) hiç geçmez — o yüzden TV
webhook'undan AYRI secret ister (`RISK_EVENT_SECRET`, boş = 503 ile kapalı, aynen TV
webhook deseni).

```
POST http://<sunucu>/risk-event?secret=<RISK_EVENT_SECRET>
Content-Type: application/json
{"action": "halt", "reason": "savaş çıktı, X borsası durdu", "source": "news_macro", "ttl_minutes": 60}
```
- `action`: `halt` | `resume` | `flatten` | `status`.
- `reason` (≤200 karakter): `halt`/`flatten`'da ZORUNLU; `resume`/`status`'ta opsiyonel.
- `source` (opsiyonel, ≤32 karakter): botun etiketi (log/teşhis için).
- `ttl_minutes` (opsiyonel, varsayılan 120, azami 1440): halt bu kadar dakika sonra
  KENDİLİĞİNDEN sona erer (ayrıca `resume` ile erken kaldırılabilir).
- secret gövdede (`{"secret": "..."}`) VEYA `?secret=` query'sinde taşınır, sabit-zamanlı
  karşılaştırılır (aynı desen: `resolve_tv_signal`/erişim logu redaksiyonu burada da geçerli).

**Nasıl çalışır (bkz. `src/strategies/scalper/engine.py` risk-olayı bölümü):**
- `halt`: `state/risk_event_halt.json`'a `{reason, source, until_ts, created_at}` yazar.
  Bu dosya **`state/scalper_entry_halt.json`'dan (koruma-hatası latch'i) AYRIDIR** ve
  `SCALPER_ENTRY_HALT_ENABLED` bayrağından **TAMAMEN BAĞIMSIZ** her zaman uygulanır — o
  bayrak yalnız `UnprotectedPositionError` otomatik latch'ini gater (canlı sunucu bunu
  `false` tutuyor), risk-olayı halt'ını DEĞİL. Motor'un TEK giriş kapısı olan
  `_entries_ready()` (böylece scanner'ın C stratejisi VE TV dış sinyali `external_signal`
  aynı anda kapanır) bu dosyayı ~1sn TTL önbellekle okur; bozuk/parse edilemeyen dosya
  **fail-closed HALT AKTİF** sayılır (`_load_entry_halt` ile aynı ilke). Açık pozisyonların
  SL/TP/trailing yönetimi (exits.py) bu kanaldan HİÇ etkilenmez.
- `resume`: yalnız `state/risk_event_halt.json`'ı siler — `scalper_entry_halt.json`'a
  dokunmaz (o ayrı, yalnız kanıtlanmış manuel müdahaleyle kalkar).
- `flatten`: `halt`'ı ÖNCE kurar (kapatma turu onlarca saniye sürebilir; halt turdan sonraya
  bırakılırsa tarama döngüsü bu pencerede yeni pozisyon açabilir ve o pozisyon tur başındaki
  tek-atımlık anlık görüntüye girmediği için asla kapanmaz), sonra TÜM izlenen scalper
  pozisyonlarını reduce-only MARKET ile kapatır (reaper'ın kullandığı AYNI emir çağrısı —
  yeni bir emir yolu yazılmadı; miktar CANLI `positionAmt`'tan alınır, girişteki bayat
  miktardan DEĞİL), her sembol için kapanışı borsa üzerinde taze (`force_fresh=True`) okuma
  ile doğrular (doğrulanamayan sembol `errors`'a düşer, izlemede KALIR — SL/TP asla
  doğrulanmadan iptal edilmez), kapanan pozisyonları `exit_reason="RISK_EVENT"` ile kaydeder.
  Kapatma turu bitince `tracked_symbols()` İKİNCİ kez taranır — halt kurulmasıyla eşzamanlı
  dolan bir pozisyon varsa o da düzleştirilir. İdempotenttir (izlenen pozisyon kalmayınca
  `flattened=[]` döner).
- `status`: halt durumu (`active`, `reason`, `until_ts`) + açık pozisyon sayısını döndürür.
- Yanıt JSON'u: `{ok, action, halted_until, reason, flattened: [symbol...], errors: [...],
  persisted}` (`persisted` yalnız `halt`/`flatten`'da; `status`'ta yok).
  - `ok` **gerçeği yansıtır**, sabit `true` DEĞİLDİR: `halt` → `active` (halt dosyaya
    yazılamasa bile RAM latch'i sayesinde etkilidir, `ok:true` + `persisted:false` — restart'ta
    kaybolur); `flatten` → `not errors` (bir sembol bile doğrulanamazsa `ok:false`, HTTP yine
    200 — `errors`'u incele); `resume` → halt kaldırıldıktan sonra `not active` (dosya
    silinemezse halt aktif KALIR, `ok:false`); `status` → her zaman `true` (salt-okunur sorgu).
  - `persisted:false` ile `ok:true` birlikte görülebilir (halt RAM'de etkili, diske yazılamadı)
    — otomasyon bunu görürse operatöre eskale etmeli (restart öncesi tekrar dener/insan
    müdahalesi ister).
- 403 = secret yanlış, 422 = geçersiz `action`/alan uzunluğu/gövde >4KB/`ttl_minutes` sonlu
  tamsayı değil (ör. `Infinity`), 503 = secret boş (kanal kapalı) veya scalper motoru hazır
  değil.

**Backtest paritesi:** risk-olayları yalnız CANLI motoru etkiler; `backtest.py`'ye
bilinçli olarak DOKUNULMADI — geçmiş bir haber olayının backtest'te simüle edilmesi bu
kanalın kapsamı dışındadır (bkz. D10, `docs/DECISIONS.md`).

## 4. Yeni kaynağı canlıya alma sırası (terfi hattı)
1. Gölge: kaynak `src`'yi allowlist'e ekleme; 3-5 gün yalnız logla ("tv"ye eşlenir, sağlamaya
   ayrı oy vermez) → kaç sinyal, hangi yön, mevcut kaynaklarla ne kadar örtüşüyor?
2. Backtest mümkünse: sinyal zaman damgalarını harness'a dış-sinyal olarak ver (yol: TODO).
3. Testnet: allowlist'e ekle → ≥5 gün → canlı defterde kaynak bazlı PF (`signal_reason`/`src`).
4. Mainnet: yalnız etiketli sürümle (bkz. CLAUDE.md terfi kuralı).

## 5. Değiştirilmeyecekler (bir bot eklerken dokunma)
`engine.py` kapıları, `exits.py`, `executor.py`, `config.py` varsayılanları, `tv_confluence.py`
oy kuralı. Bunlardan birini değiştirmek "yeni bot" değil "motor değişikliği"dir: backtest +
DECISIONS + parite testi ister.

## 6. Örnek istemci (`examples/`)

Yeni bir haber/olay botu yazarken sıfırdan başlama — `examples/` altındaki iki dosyayı
kendi bot deponuza kopyalayın (bu dosyalar motora import EDİLMEZ, motordan bağımsız
yaşarlar, gerçek bir dış bot gibi):

- `examples/news_bot_client.py` — bağımlılıksız (yalnız stdlib `urllib`) istemci:
  `TradingBotClient(base_url, tv_secret, risk_secret=None, source="news_macro")`.
  `signal()` secret'ı QUERY string'de taşır (`?secret=...`, LuxAlgo alarmlarının aynı
  yolu kullanmasıyla aynı sebep — dosyanın docstring'inde ayrıntı), `halt()`/`resume()`/
  `flatten()`/`status()` secret'ı GÖVDEDE taşır. 4xx asla retry edilmez (yanlış secret
  kendi kendine düzelmez); 5xx/bağlantı hatası 2 kez exponential backoff ile retry edilir.
  `dry_run=True` hiç ağa çıkmadan (secret'ı redakte ederek) ne gönderileceğini basar.
  CLI:
  ```bash
  python examples/news_bot_client.py --base http://127.0.0.1:9091 signal BTCUSDT sell --dry-run
  python examples/news_bot_client.py --base http://127.0.0.1:9091 --tv-secret <TV_WEBHOOK_SECRET> signal BTCUSDT sell
  python examples/news_bot_client.py --base http://127.0.0.1:9091 --risk-secret <RISK_EVENT_SECRET> halt "savaş çıktı" --ttl-minutes 60
  ```
- `examples/news_bot_skeleton.py` — `ingest(headline) → classify(headline) → de-dup →
  rate-limit → send` akışının iskeleti. `classify()` bilinçli olarak bir STUB'tır
  (`# TODO: LLM/kural tabanlı sınıflandırma buraya`) — de-dup, rate-limit (sembol başına
  10 dakikada azami N sinyal, `NEWS_BOT_MAX_SIGNALS_PER_10MIN`), allowlist zorlaması
  (`NEWS_BOT_SYMBOL_ALLOWLIST`) ve `TradingBotClient` üzerinden teslimat GERÇEK ve
  çalışır durumdadır — kopyalayan yalnız `classify()`'ı doldurur.

Testler: `tests/test_news_bot_client.py` (yerel sahte `http.server` ile — gerçek ağ yok):
secret'ın doğru kanalda taşındığını, 4xx'te retry olmadığını, 5xx'te retry olduğunu,
`dry_run`'ın ağa çıkmadığını ve secret'ın hiçbir çıktıda (dry-run/CLI) görünmediğini
doğrular.

**Yeni bot kontrol listesi** (bkz. §4 terfi hattı):
1. `src` adı seç (tek isim, `news_macro` gibi — bir modelin iki varyantı iki `src`
   SAYILMAZ, bkz. §2.2).
2. Gölge: `src`'yi `TV_SOURCE_ALLOWLIST`'e EKLEME, 3-5 gün yalnız logla (fiilen "tv"ye
   eşlenir, sağlamaya oy vermez) — kaç sinyal, hangi yön, mevcut kaynaklarla örtüşme.
3. Testnet: allowlist'e ekle → ≥5 gün → canlı defterde kaynak bazlı PF.
4. Mainnet: yalnız etiketli sürümle (CLAUDE.md terfi kuralı) — ayıda PF ≥ 1.1 **ve**
   boğada PnL kaybı ≤ %20 kanıtı olmadan yok.

## 7. TV olay kanalı — ÇIKIŞ ve YAPI/DÖNÜŞ olayları (D19, 2026-08-23)

Bugüne kadar TradingView'den bota YALNIZ "gir" oyu geliyordu (§1). Göstergelerin
asıl bilgisi ise çoğu zaman **çıkışta ve yapıda**: LuxAlgo S&O "Exit Signal",
S&O "Trend Catcher/Tracer Up|Down", Price Action Concepts "Bullish/Bearish S-CHOCH",
AlgoPro "🎯 TP1 Hit". Bu kanal onları motora sokar.

### 7.1 Yönlendirme GÖVDEDEN yapılır (URL değişmez)
Kullanıcı yeni alarmları TV'de **mevcut alarmları klonlayarak** kuruyor: webhook
URL'si (secret ve eski `?src=luxso`) aynen kalıyor, yalnız **alarm koşulu ve mesaj
gövdesi** değişiyor. Bu yüzden yönlendirme gövdeden okunur:

- **JSON gövde** → `src` (veya `source`) ve `kind` **alanları**.
- **Düz metin gövde** → `src=<token>` ve `kind=<token>` **belirteçleri**
  (büyük/küçük harf duyarsız; ayırıcı boşluk, virgül veya `|`).
- `kind` **yoksa** → `entry`: **bugünkü davranış birebir korunur** (mevcut 49 alarm
  hiç etkilenmez, sağlamaya girer, `external_signal`'a gider).
- `kind ∈ {entry, exit, choch, trend, tp1}`. **Tanınmayan `kind` 422 ile REDDEDİLİR**
  — "entry"ye düşürülmez: bir çıkış alarmının yazım hatası yüzünden pozisyon açması
  kabul edilemez. (Bu, `?src=` allowlist'inin "reddetme, `tv`ye eşle" davranışının
  bilinçli tersidir; orada en kötü sonuç bir oyun sayılmamasıdır.)
- **`src` önceliği:** gövdedeki `src` **allowlist'teyse** URL'deki `?src=`'i
  GEÇERSİZ KILAR (klon senaryosunun tamamı bu). Allowlist dışındaysa geçersiz kılma
  YAPILMAZ: WARNING loglanır ve bugünkü `?src=` davranışı sürer
  (yanıt `body_src_rejected: true` taşır).
- `kind != entry` olan istek **sağlamaya (TvConfluence) HİÇ girmez** ve
  `engine.external_signal` **çağrılmaz** — yalnız `src/services/tv_events.py`
  defterine yazılır. Motor hazır olmasa bile olay kaydedilir (503 dönmez).

Yeni kaynak etiketleri: `luxso_exit`, `luxso_trend`, `pac_choch`, `algopro_tp1`.
Kod varsayılanı (`tv_source_allowlist`) bunları içerir; ⚠️ **sunucu `.env`'i
`TV_SOURCE_ALLOWLIST`'i AÇIKÇA set ediyorsa varsayılan devreye girmez** — o satıra
da eklenmeli (bkz. `docs/RUNBOOK.md` "TV olay kanalı").

### 7.2 Alarm mesaj şablonları (TV'de klonlarken yapıştır)
Her biri **tek satır düz metin**tir; `{{ticker}}` **zorunludur** (sembol ondan
çözülür — `BINANCE:BTCUSDT.P` biçimi de kabul edilir). Koşul adları TradingView
alarm diyaloğundan okunmuştur; mevcut alarmlar 5 dakikalık grafiklerdedir.

**LuxAlgo® — Signals & Overlays™ [7.3.1]**

| Alarm koşulu | Mesaj |
|---|---|
| `Exit Signal` (mavi X, **yönsüz**) | `src=luxso_exit kind=exit {{ticker}}` |
| `Trend Catcher Up` | `src=luxso_trend kind=trend up {{ticker}}` |
| `Trend Catcher Down` | `src=luxso_trend kind=trend down {{ticker}}` |
| `Trend Tracer Up` | `src=luxso_trend kind=trend up {{ticker}}` |
| `Trend Tracer Down` | `src=luxso_trend kind=trend down {{ticker}}` |

**LuxAlgo® — Price Action Concepts™ [2.3.3]**

| Alarm koşulu | Mesaj |
|---|---|
| `Bullish S-CHOCH` (swing) | `src=pac_choch kind=choch bullish {{ticker}}` |
| `Bearish S-CHOCH` (swing) | `src=pac_choch kind=choch bearish {{ticker}}` |
| `Bullish I-CHOCH` (internal, opsiyonel) | `src=pac_choch kind=choch bullish {{ticker}}` |
| `Bearish I-CHOCH` (internal, opsiyonel) | `src=pac_choch kind=choch bearish {{ticker}}` |

**AlgoPro V1.6**

| Alarm koşulu | Mesaj |
|---|---|
| `🎯 TP1 Hit` (**yönsüz**) | `src=algopro_tp1 kind=tp1 {{ticker}}` |
| `⚪ Exit Signal` (**yönsüz**, opsiyonel) | `src=algopro_tp1 kind=exit {{ticker}}` |
| `🎯 TP2 Hit` / `🏆 TP3 Hit` (opsiyonel) | `src=algopro_tp1 kind=tp1 {{ticker}}` |

Notlar:
- **`src` bir KAYNAK kimliğidir, kind değildir.** `algopro_tp1`, AlgoPro'nun çıkış
  ailesinin (TP1/TP2/TP3/Exit) tek etiketidir. Ayrı saymak istenirse yeni bir isim
  `TV_SOURCE_ALLOWLIST`'e eklenmeli (kod değişmez, §1'deki kural).
- `🛑 Stop Loss Hit` **bağlanmaz**: kendi SL'imiz zaten borsada duruyor.
- Mevcut GİRİŞ alarmları (S&O `Bullish/Bearish Confirmation(+)`, `Any Bullish/Bearish
  Contrarian`, AlgoPro `🟢 Buy Signal` / `🔴 Sell Signal`) **değiştirilmez** — onların
  mesajında `kind` yoktur, bugünkü yolda kalırlar.
- `I-BOS`/`S-BOS` (kırılım) **bağlanmaz**: BOS trendin devamıdır, CHoCH ise dönüştür;
  kapının anlamı dönüş bilgisidir.

### 7.3 Yön semantiği (iki farklı şey)
- `choch` / `trend` → olayın yönü **YAPININ** yönüdür: `bullish`/`up` → `BULL`,
  `bearish`/`down` → `BEAR`. **Yön ZORUNLUDUR**, çözülemezse 422.
- `exit` / `tp1` → olayın yönü (varsa) **KAPATILACAK POZİSYONUN** yönüdür.
  "Bullish Exit" = *LONG pozisyon için çıkış*; **yapı yukarı döndü demek DEĞİLDİR**
  ve yapı durumunu güncellemez. Gerçek alarm koşulları (`Exit Signal`, `🎯 TP1 Hit`)
  **yönsüzdür**: yön yoksa sembolde açık pozisyon hangi yöndeyse ona uygulanır;
  yön VARSA ve açık pozisyonla uyuşmuyorsa **uygulanmaz + loglanır**.

Yön sözlüğü: `buy|long|bull|bullish|up` ↔ `sell|short|bear|bearish|down`
(**sözcük sınırıyla** — `up` alt-dize olarak "SETUP"/"SUPPORT" içinde geçer;
giriş yolunun alt-dize taraması DEĞİŞMEDİ).

### 7.4 Motor etkisi — üç mod
`SCALPER_TV_EVENTS_MODE=off|shadow|active` (varsayılan **shadow**):

| Mod | Giriş kapısı | Çıkış tetiği | Emir/stop |
|---|---|---|---|
| `off` | yok | yok | değişmez |
| `shadow` | "ne olurdu" logu + `would_block` | "ne olurdu" logu + `would_exit` | **değişmez** |
| `active` | ters yapıda giriş ENGELLENİR | BE veya kapanış UYGULANIR | değişir |

- **Giriş kapısı (`active`):** sembolün yapı durumu sinyale tersse
  (`BEAR` iken `LONG`, `BULL` iken `SHORT`) ve olay `SCALPER_TV_EVENTS_MAX_AGE_MIN`
  (varsayılan 240 dk) içindeyse giriş engellenir. Kapı **rejim kapısının hemen
  yanındadır** (`engine._evaluate_symbol`) — yani C stratejisi ve TV dış sinyali
  AYNI tek giriş noktasından geçer. Ret sayacı: `tv_structure_gate`
  (`/scalper/status` → `entry_rejects`).
- Kapıyı hangi kaynakların besleyeceğini `SCALPER_TV_EVENTS_GATE_SOURCES`
  (varsayılan `pac_choch,luxso_trend`) seçer; **listede olmayan kaynaklar yalnız
  telemetride görünür**, karar vermez. Kapı kaynakları çelişirse (biri BULL biri
  BEAR) **ters olan engeller** (TvConfluence'ın ters-oy kuralıyla aynı ruh);
  telemetride `structure: MIXED` görünür.
- **Çıkış tetikleyicisi (`active`, `SCALPER_TV_EVENTS_EXIT=off|be|close`,
  varsayılan `be`):** açık pozisyonla aynı sembolde ters CHoCH/trend (yalnız kapı
  kaynakları) ya da `exit`/`tp1` olayı gelince
  - `be` → stop BE'ye çekilir: **mevcut BE mekanizması**
    (`ExitManager.force_breakeven` → `pm.replace_stop_loss` boşluksuz deseni;
    `_is_at_least_as_protective` gevşetmeyi yasaklar). **Yeni emir yolu yazılmadı.**
    `tp1_done`/`trailing_active` bayrakları **bilinçli olarak değiştirilmez**
    (aksi halde pozisyon D4 reaper muafiyetine girer ve chandelier izi TP1 dolmadan
    başlardı — sessiz davranış değişikliği olurdu).
  - `close` → reaper/risk-olayı `flatten` ile **AYNI** reduce-only MARKET çağrısı
    (`_submit_reduce_only_market_close`), **aynı** `force_fresh=True` doğrulaması ve
    **aynı** tek-finalizer kilidi (`ExitManager._closing`), `exit_reason="TV_EVENT"`.
- **Kaynak kapsamı farkı:** `gate_sources` YALNIZ yapı olaylarını (`choch`/`trend`)
  süzer. `exit`/`tp1` olayları **kaynak ayrımı yapılmadan** uygulanır — bunlar açık bir
  "çık" komutudur ve zaten webhook secret'ıyla kimliklenmiştir.
- `SCALPER_TV_EVENTS_EXIT=off` çıkış tetiğini **gölgede de** kapatır (`would_exit`
  sayılmaz); giriş kapısı bundan etkilenmez.
- **Bir olay bir kez tetikler** (sembol başına olay sırası imleci) ve
  **pozisyon açılışından ÖNCEKİ olaylar sayılmaz** — aksi halde 3 saat önce gelmiş
  bir "exit" alarmı yeni açılan pozisyonu doğduğu anda kapatırdı.
- **FAIL-OPEN:** bu bir SİNYAL kanalıdır. Defter bozuksa/okunamıyorsa girişler
  DURMAZ ve pozisyon KAPANMAZ; bugünkü davranış aynen sürer. (Risk kapıları —
  risk-olayı halt, kill switch, entry latch — fail-CLOSED'dır; bu değil.)

### 7.5 Telemetri ve kalıcılık
- `GET /scalper/status` → `tv_events`: `mode`, `exit_action`, `max_age_minutes`,
  `gate_sources`, `counters` (`ingested`/`would_block`/`would_exit`/`blocked`/
  `exits_applied`) ve `symbols` (sembol → `structure`, `structure_source`,
  `structure_age_s`, kaynak bazlı `structures`, `last_event`, `last_exit`).
  **Secret içermez.**
- Log satırları: `🧭 TV olayı: SYMBOL kind=… dir=… ← src` (alma),
  `⛔ … TV yapı kapısı` (aktif ret), `👻 … GÖLGE` (gölge),
  `🛡️ … SL ücret-dahil BE'ye çekildi` (BE).
- Durum dosyası `state/tv_events.json` (atomik yazım). **Bozuk dosya = boş durum +
  WARNING** (fail-closed DEĞİL, yukarıdaki gerekçe).

### 7.6 Backtest paritesi — bilinçli boşluk
TV olayları geçmişte YOKTUR (alarm geçmişi indirilemez, gösterge çıktısı harness'ta
yeniden üretilmez). Bu yüzden `backtest.py`'ye **DOKUNULMADI** ve bu kanal
**"yalnız canlı"**dır — D10 risk-olayı kanalıyla aynı gerekçe ve aynı kabul.
Terfi hattı bu yüzden §4'ün 2. adımını (backtest) ATLAR ve kanıt yükünü gölge
ölçümüne yıkar:

1. **Gölge** (varsayılan): `SCALPER_TV_EVENTS_MODE=shadow`, alarmlar kurulu.
   ≥5 gün. Ölçüm: `would_block` sayacı vs aynı pencerede açılan işlemlerin
   sonucu — "engellenecek olan sinyaller gerçekten kaybettirdi mi?"
   (`scripts/ledger_report.py` + `logs/bot.log` `👻 TV yapı kapısı` satırları).
2. **Defter ölçümü:** engellenecek girişlerin gerçekleşen PnL'i ve BE'ye
   çekilecek pozisyonların nihai `exit_reason`/PnL'i; rejime (UP/FLAT/DOWN gün)
   bölünmeden hüküm verilmez (CLAUDE.md "Karar verirken").
3. **Aktif:** önce `SCALPER_TV_EVENTS_EXIT=be` (geri alınabilir, yalnız stop
   sıkışır), sonra gerekirse `close`. `active` kararı `docs/DECISIONS.md`'ye
   yeni bir satır olarak girer (D19 gölge kararının üstüne).
