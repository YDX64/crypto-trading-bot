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

## 4b. AlgoPro takipçi kanalı (`POST /follower/event`) — D20, 2026-08-23'ten beri AKTİF
Yukarıdaki sözleşme "motor tektir" der; takipçi halkası bunun İSTİSNASI DEĞİL,
**ikinci bir motorun ayrı bir süreçte** çalıştırılmasıdır (`BOT_MODE=follower`,
`/opt/tradingbot-ap`, port 9093, AYRI Binance testnet hesabı). Scalper halkasının
motoru, kapıları ve `.env`'i DEĞİŞMEZ.

```
POST http://127.0.0.1:9093/follower/event
X-Follower-Secret: <FOLLOWER_FORWARD_SECRET>
Content-Type: text/plain; charset=utf-8

🔴 SELL | BINANCE:BTCUSDT | TF: 1 | Price: 77126.08 | TQI: .45 | Score: 8 | SL: 77167.77 | TP1: 77105.23 | TP2: 77084.39 | TP3: 77063.54
```
- Secret: `X-Follower-Secret` başlığı (tercih) · gövdede `secret=…`.
  **`?secret=` KABUL EDİLMEZ (403)** — uvicorn erişim logu query string'i düz metin
  yazar (CLAUDE.md kural 5). `TV_WEBHOOK_SECRET`/`RISK_EVENT_SECRET`'tan AYRIDIR;
  boş = 503 (fail-closed).
- Olay türü gövdeden çözülür: `BUY`/`SELL` → giriş, `EXIT` → çıkış,
  `TP1|TP2|TP3 HIT`, `SL HIT` → borsa çapraz doğrulaması (**terminal HIT'te pozisyon
  hâlâ açıksa kalan miktar reduce-only MARKET ile KAPATILIR** — D20a bulgu 7).
- **KATI BİÇİM (D20a bulgu 2).** Gövde ancak şunların HEPSİNİ taşıyorsa kabul edilir:
  başlıkta olay anahtarı + `| BINANCE:<SEMBOL>USDT |` + `| TF:` + `| Price:`;
  GİRİŞLERDE ayrıca `SL`, `TP1`, `TP2`, `TP3` seviyelerinin DÖRDÜ ve yön sıralaması.
  Eksik alan → 422 + WARNING. "Tanınmayan gövde + yön kelimesi = giriş" davranışı
  (fail-open) KALDIRILDI.
  Alternatif açık şablon (yalnız ELLE test; köprü bunu İLETMEZ):
  `src=algopro kind=entry buy BTCUSDT tf=1 px=… sl=… tp1=… tp2=… tp3=…` — girişte
  `px`+`sl`+`tp1`+`tp2`+`tp3` ZORUNLUDUR.
- Yanıt: `{ok, kind, symbol, direction, accepted, reason, ...}`. `accepted=false`
  bir HATA DEĞİLDİR (kapasite/cooldown/kapı reddi) — HTTP 200 döner.
- 403 = secret yanlış · 422 = gövde çözülemedi/>4KB · 503 = kanal kapalı ya da motor
  hazır değil (`/risk-event` ile AYNI semantik).
- **Kaynak:** olaylar ana bottan (`/tv-signal`) İLETİLİR; TV alarm URL'leri ve
  secret'ları DEĞİŞMEZ. Köprü kararını **GÖVDEYE** verir (yukarıdaki katı biçim;
  `parser.algopro_alert_kind`) — `?src=` ve `TV_SOURCE_ALLOWLIST` iletim kararına
  GİRMEZ (D20a bulgu 5). LuxAlgo/BotV3/serbest metin, `?src=algopro` yazsa bile
  İLETİLMEZ; gerçek bir AlgoPro gövdesi `?src=` yanlış olsa da iletilir.
  Fire-and-forget'tir (bağlantı 2 sn, okuma `FOLLOWER_FORWARD_TIMEOUT_SECONDS`=20 sn)
  ve ana motoru ASLA etkilemez. Kimliği doğrulanmamış gövde (403) İLETİLMEZ.
  Telemetri: `GET /follower/forwarder` (iletilen/atlanan sayaçları; secret İÇERMEZ).
- **Ücret eşiği kapısı VARSAYILAN AÇIK** (`FOLLOWER_MIN_TP1_FEE_RATIO=1.0`, D20a
  bulgu 3): TP1 ROI'si gidiş-dönüş komisyonun altındaysa giriş HİÇ açılmaz
  (stop ≥ ~%0.20 gerekir). `accepted=false`, `reject_counters.fee_gate`.
- **`GET /follower/status` yalnız takipçi halkasında** (`BOT_MODE=follower`);
  scalper halkasında **404** döner (mod izolasyonu).
- **Backtest paritesi:** takipçi harness'ta modellenmez (strateji C'yi hiç kullanmaz);
  `backtest.py`'ye DOKUNULMADI.

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

- **JSON gövde** → **üst düzey** `src` (veya `source`), `kind`, `via` alanları;
  yaygın webhook sarmalayıcısı olduğu için **üst düzey `data` nesnesi** de okunur.
  ⚠️ **Daha derin iç içe JSON ARANMAZ** (D19a G1) — okunmayan `kind` "entry"ye
  düşer ve aşağıdaki olay-kaynağı koruması devreye girer.
- **Düz metin gövde** → `src=<token>` / `kind=<token>` / `via=<token>` **belirteçleri**;
  ayraç `=` **veya** `:`, ayırıcı boşluk/virgül/`|`, büyük-küçük harf duyarsız.
  ⚠️ **Belirteçler YALNIZ "başlık koşusu"ndan okunur** (D19a G1): satır başından
  itibaren kesintisiz `anahtar=değer` dizisi; ilk serbest metin belirtecinde biter
  (ilk 5 satır taranır). Yani mesaj `src=… kind=… {{ticker}} …` diye BAŞLAMALIDIR.
  Gerekçe: TradingView'in `{{strategy.order.alert_message}}` gibi alanları kullanıcı
  metnini gövdenin ortasına basar; serbest metin taransaydı o metin mevcut bir
  alarmın kimliğini (`src`) ya da yolunu (`kind`) değiştirebilirdi.
  🔒 **Belirteçler ortada kalırsa istek SESSİZCE giriş oyuna DÖNÜŞMEZ** (D19a-2 R1-1
  + bütünleşme incelemesi 2026-08-23). Gövdenin tamamı **iki** kanıt için taranır:
  (a) `src=<olay kaynağı>` adı, (b) `kind[=:]<exit|choch|trend|tp1>` belirteci. (a)
  bulunursa istek **422**'dir. (b) bulunursa istek **yalnız gövde tanınan bir GİRİŞ
  biçimi DEĞİLSE** 422'dir — tanınan giriş biçimleri: JSON giriş gövdesi
  (`symbol`/`side`), AlgoPro/BotV3 tek satır biçimi (`| TF:` / `| Price:`) ve
  `{{ticker}} BUY|SELL`. Bu asimetri bilinçlidir: AlgoPro'nun serbest metin alanında
  (`msg: … kind=exit`) tesadüfen geçen bir belirteç meşru bir girişi 422'ye
  düşürmemeli. Yani yanlış yerleştirilmiş bir olay alarmı ya doğru yola gider ya
  GÖRÜNÜR biçimde ölür; pozisyon açmaz.
  ⚠️ **Kapsam sınırı (kodda doğrulandı):** her iki tarama da DÜZ METİN
  belirteçlerine bakar. **JSON** gövdede `"kind": "choch"` biçimi (anahtarla ayraç
  arasında tırnak var) hiçbir taramaya takılmaz — bu yüzden `data`'dan DAHA DERİN
  bir JSON alanına yazılmış `kind` bugün de okunmaz ve 422 üretmez; o gövde
  bugünkü gibi GİRİŞ yolunda kalır. Derin iç içe JSON kullanma; `src`/`kind`i
  üst düzeye ya da `data` altına yaz.
  ⚠️ **`=` ile `:` aynı sertlikte DEĞİLDİR** (D19a-2 R1-2): `=` kasıtlı bir
  belirteçtir, tanınmayan değeri 422'dir. `:` düz yazı noktalamasıdır ("Kind: Bullish
  Reversal") — bu yüzden `:` ile gelen `src`/`kind` YALNIZ tanınan bir değer
  taşıyorsa sayılır, aksi halde YOK SAYILIR.
- `kind` **yoksa** → `entry`: **bugünkü davranış birebir korunur** (mevcut 49 alarm
  hiç etkilenmez, sağlamaya girer, `external_signal`'a gider).
- `kind ∈ {entry, exit, choch, trend, tp1}`. **Tanınmayan `kind` 422 ile REDDEDİLİR**
  — "entry"ye düşürülmez: bir çıkış alarmının yazım hatası yüzünden pozisyon açması
  kabul edilemez. (Bu, `?src=` allowlist'inin "reddetme, `tv`ye eşle" davranışının
  bilinçli tersidir; orada en kötü sonuç bir oyun sayılmamasıdır.)
- 🔒 **OLAY KAYNAĞI GİRİŞ OYU VEREMEZ** (D19a A): `TV_EVENT_SOURCES` listesindeki
  bir kaynak (`luxso_exit`, `luxso_trend`, `pac_choch`, `algopro_tp1`) `kind=entry`
  ile gelirse istek **422**'dir. Kontrol isteğin TÜM kaynak adaylarına uygulanır:
  başlık koşusundaki gövde `src`i, `?src=` ve **gövdenin herhangi bir yerinde**
  geçen `src=<olay kaynağı>` (D19a-2 R1-1). Böylece allowlist dışı bir ad `tv`ye
  eşlenip sıyrılamaz. Gerekçe: `kind` düşerse istek sessizce bir OY olur ve gövdedeki
  `src` **yeni bir sağlama kaynağı** sayılır — `TV_CONFLUENCE_REQUIRED=2` iken
  LuxAlgo ailesi tek başına kotayı doldurup pozisyon açtırabilirdi.
- **`src` önceliği (GİRİŞ yolu):** gövdedeki `src` **allowlist'teyse** URL'deki
  `?src=`'i GEÇERSİZ KILAR (klon senaryosunun tamamı bu). Allowlist dışındaysa
  geçersiz kılma YAPILMAZ: WARNING + bugünkü `?src=` davranışı
  (yanıt `body_src_rejected: true`).
- **`src` (OLAY yolu) allowlist'ten BAĞIMSIZDIR** (D19a E): istek
  `TV_WEBHOOK_SECRET` ile kimliklidir ve sağlamaya hiç girmez, dolayısıyla bir
  "hayalet kaynak" giriş açtıramaz. Gövdedeki etiket **KORUNUR**; allowlist dışıysa
  yalnız WARNING + yanıtta `source_allowlisted: false`. (Eski davranışta etiket
  sessizce eski `?src=luxso`ya düşüyor ve kapı hiç eşleşmiyordu — kanal "kurulu
  görünüp ölü" oluyordu.)
- **Sembol allowlist'i (D7) olay yolunda da uygulanır** (D19a F):
  `SCALPER_TV_SYMBOL_ALLOWLIST` doluysa dışındaki sembolün olayı deftere YAZILMAZ (OSC kanıtı
  olmayan sembolde kapı/çıkış kanıtsız karar vermesin). Yanıt **200 +
  `applied: false, reason: "symbol_allowlist"`**tir — 422 DEĞİL: aynı ayar GİRİŞ
  yolunda da sessizce reddeder (`accepted: false`, 200) ve aynı sembolde kurulu iki
  alarmdan biri TV'de yeşil diğeri kırmızı görünmemelidir (D19a-2 R1-4). 422 yalnız
  BİÇİM hataları içindir (secret, `kind`, sembol biçimi, eksik yön).
- Sembol biçimi olay yolunda **tam eşleşmeyle** doğrulanır (`_TV_SYMBOL_RE`,
  D19a G3); giriş yolunun daha gevşek davranışı bilinçli olarak DEĞİŞMEDİ.
- **Secret her şeyden ÖNCE doğrulanır** (D19a G2, sabit zamanlı): kimliksiz bir
  istek 422 mesajlarından kanalın sözleşmesini öğrenemez.
- `kind != entry` olan istek **sağlamaya (TvConfluence) HİÇ girmez** ve
  `engine.external_signal` **çağrılmaz** — yalnız `src/services/tv_events.py`
  defterine yazılır. Motor hazır olmasa bile olay kaydedilir (503 dönmez).

Yeni kaynak etiketleri: `luxso_exit`, `luxso_trend`, `pac_choch`, `algopro_tp1`.
Kod varsayılanı (`tv_source_allowlist`) bunları içerir; sunucu `.env`'i
`TV_SOURCE_ALLOWLIST`'i AÇIKÇA set ediyorsa **olay yolu yine çalışır** ama giriş
yolundaki aynı etiket `tv`ye eşlenir — startup'ta WARNING ve
`/scalper/status` → `tv_events.allowlist_ok=false` bunu görünür kılar
(bkz. `docs/RUNBOOK.md` "TV olay kanalı" adım 2).

### 7.2 Alarm mesaj şablonları (TV'de klonlarken yapıştır)
Her biri **tek satır düz metin**tir; `{{ticker}}` **zorunludur** (sembol ondan
çözülür — `BINANCE:BTCUSDT.P` biçimi de kabul edilir). Koşul adları TradingView
alarm diyaloğundan okunmuştur; mevcut alarmlar 5 dakikalık grafiklerdedir.

⚠️ **Belirteçler mesajın BAŞINDA olmalı** (D19a G1): `src=` ve `kind=` satırın ilk
belirteçleri olacak, `{{ticker}}` ve serbest metin SONRA gelecek. Aşağıdaki
şablonlar bu kurala uyar; kendi metnini eklerken sıralamayı bozma.

🔒 **`src=` ASLA DÜŞÜRÜLMEZ.** "Zaten `?src=` var" ya da "kaynak belli" diye
gövdedeki `src=`'i atma. `src=` iki işe birden yarar: kaynak kimliği (kapı/çıkış
`SCALPER_TV_EVENTS_GATE_SOURCES` ile bunu eşler) **ve** yanlış yerleştirilmiş bir
belirteçte devreye giren birinci kalkan (§7.1, D19a-2 R1-1). `src=` düşerse ikinci
kalkan (`kind=` taraması) devreye girer, ama o **yalnız gövde tanınan bir giriş
biçimi değilse** koruma sağlar — yani `src=`'siz bir olay alarmının kaza yüzeyi
daha geniştir. `kind=` de düşerse hiçbir kalkan kalmaz ve alarm bugünkü gibi bir
GİRİŞ OYU olur (D19a bulgu A'nın senaryosu).

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

**Aynı `src`i paylaşan alt-kaynaklar — "SON OLAY KAZANIR"** (D19a G4): S&O
"Trend Catcher" ile "Trend Tracer" ikisi de `luxso_trend` etiketlidir. Durum
anahtarı `src` olduğu için bu ikisi birbirini **MIXED'e DÜŞÜRMEZ**: sembolün
`luxso_trend` yapısı her zaman en son gelen olayın yönüdür. Hangisinin geldiğini
görmek istersen mesaja isteğe bağlı `via=catcher` / `via=tracer` ekle — `via`
**yalnız telemetridir** (karara girmez, yön taramasından çıkarılır,
`/scalper/status` → `structures.<src>.via`). İki alt-kaynağı AYRI saymak istersen
doğru yol `via` değil, yeni bir `src` etiketidir (§1'deki kural).

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
  telemetride görünür**, karar vermez.
- **ÇELİŞKİ (MIXED) → KAPI UYGULANMAZ** (D19a F): kapı kaynakları farklı yön
  söylüyorsa (PAC BULL + S&O trend BEAR) çelişki **"bilinmiyor"** demektir,
  "her iki yön de yasak" DEĞİL. Eski kural ("ters olan engeller") sembolü
  `max_age` boyunca **iki yöne de** kilitliyordu — hiçbir kanıt üretmeyen bir durum
  en sert kararı veriyordu. Artık sayaç (`mixed_skipped`) + log üretilir, giriş
  bugünkü davranışla sürer; telemetride `structure: MIXED` görünmeye devam eder.
- **SIFIR/BOŞ = KAPALI** (D19a G5): `SCALPER_TV_EVENTS_MAX_AGE_MIN=0` "süresiz
  taze" DEĞİL, **pencere kapalı**; boş `SCALPER_TV_EVENTS_GATE_SOURCES` "tüm
  kaynaklar" DEĞİL, **hiçbir kaynak karar vermez**. Durum `/scalper/status` →
  `tv_events.gate_enabled` / `window_open`. Pencere kapalıyken tüketim imleçleri
  YİNE DE ilerletilir (D19a-2 R2-7) — operatör pencereyi açtığında birikmiş
  olaylar toplu tetiklemesin.
- **`active` iken kanal HİÇBİR ŞEY yapamıyorsa süreç BAŞLAMAZ** (D19a-2 R1-3):
  `MODE=active` + (`MAX_AGE_MIN=0` **veya** (boş `GATE_SOURCES` **ve** `EXIT=off`))
  → startup'ta `ValueError`. `active` bilinçli bir karardır; sessizce ölü bir kanal
  operatörü yanıltır. **Boş `GATE_SOURCES` + `EXIT=be|close` GEÇERLİDİR** ("giriş
  kapısı yok ama açık çık komutlarına uy") — çünkü `gate_sources` yalnız yapı
  olaylarını süzer, `exit`/`tp1` ondan bağımsızdır (aşağıdaki "kaynak kapsamı farkı").
- **Çıkış tetikleyicisi (`active`, `SCALPER_TV_EVENTS_EXIT=off|be|close`,
  varsayılan `be`):** açık pozisyonla aynı sembolde ters CHoCH/trend (yalnız kapı
  kaynakları; MIXED'te hiçbir şey yapılmaz) ya da `exit`/`tp1` olayı gelince
  - `be` → stop BE'ye çekilir: **mevcut BE mekanizması**
    (`ExitManager.force_breakeven` → `pm.replace_stop_loss` boşluksuz deseni;
    `_is_at_least_as_protective` gevşetmeyi yasaklar). **Yeni emir yolu yazılmadı.**
    `tp1_done`/`trailing_active` bayrakları **bilinçli olarak değiştirilmez**
    (aksi halde pozisyon D4 reaper muafiyetine girer ve chandelier izi TP1 dolmadan
    başlardı — sessiz davranış değişikliği olurdu).
    🔒 **YALNIZ POZİSYON KÂRDAYKEN** (D19a B): fiyat BE'nin koruyucu tarafında ve
    `SCALPER_TV_EVENTS_BE_MARGIN_PCT` (%0.05) payı kadar uzakta olmalıdır. Zararda
    BE, stop'u piyasanın TERS tarafına koymaktır → Binance `-2021` →
    `position_manager._emergency_close`: "geri alınabilir" sanılan ayar fiilen
    **piyasa emriyle kapanış** olurdu. Kontrol hem motorda hem `force_breakeven`
    içinde yapılır (çift kapı).
    **SIRA:** motor ÖNCE `force_breakeven`ı çağırır, SONRA "neden olmadı" diye
    bakar (D19a-2 R2-1). Tersi, stopu ZATEN BE'de olan (TP1 dolmuş, D4 reaper
    muafiyetindeki) bir koşucuyu fiyat geri çekildiğinde `EXIT_LOSING=close` ile
    piyasadan kapattırıyordu.
    **BAYAT FİYAT = BİLİNMİYOR** (D19a-2 R2-5): `position.current_price` yalnız
    ticker okuması başarılı olduğunda yazılır; `price_ts` damgası yoksa ya da
    30 sn'den eskiyse karar verilmez, hiçbir emir gönderilmez ve olay **tüketilmez**
    (sonraki turlarda yeniden denenir).
  - `close` → reaper/risk-olayı `flatten` ile **AYNI** reduce-only MARKET çağrısı
    (`_submit_reduce_only_market_close`), **aynı** `force_fresh=True` doğrulaması ve
    **aynı** tek-finalizer kilidi (`ExitManager._closing`), `exit_reason="TV_EVENT"`.
- **`SCALPER_TV_EVENTS_EXIT_LOSING=skip|close`** (varsayılan `skip`, D19a B):
  `be` seçiliyken pozisyon ZARARDAYSA ne yapılacağı. `skip` = hiçbir şey (logla +
  `exits_skipped_losing` ve `exits_noop` say; pozisyon normal SL/TP/trailing
  korumasında kalır). `close` = reduce-only MARKET kapanış (bilinçli, geri alınamaz
  karar). Fiyat ya da BE **okunamıyorsa/bayatsa** `close` bile UYGULANMAZ.
- **Kaynak kapsamı farkı:** `gate_sources` YALNIZ yapı olaylarını (`choch`/`trend`)
  süzer. `exit`/`tp1` olayları **kaynak ayrımı yapılmadan** uygulanır — bunlar açık bir
  "çık" komutudur ve zaten webhook secret'ıyla kimliklenmiştir.
- `SCALPER_TV_EVENTS_EXIT=off` çıkış tetiğini **gölgede de** kapatır (`would_exit`
  sayılmaz); giriş kapısı bundan etkilenmez. İmleçler yine de ilerletilir ki mod
  sonradan açılınca birikmiş olaylar toplu tetiklenmesin.
- **TUR BAŞINA EN FAZLA 1 çıkış aksiyonu** (D19a G6, `_TV_EXIT_MAX_ACTIONS_PER_TICK`):
  reaper'ın 2026-08-14 dersiyle aynı — eşzamanlı çoklu kapanış safety turunu şişirip
  borsa tazelik eşiğini (30 sn) aştırıyordu. Ertelenen olaylar **tüketilmez**,
  sonraki turda ele alınır.
- **Bir olay bir kez tetikler** (sembol başına olay sırası imleci) ve
  **pozisyon açılışından ÖNCEKİ olaylar sayılmaz** — aksi halde 3 saat önce gelmiş
  bir "exit" alarmı yeni açılan pozisyonu doğduğu anda kapatırdı. İmleç **kalıcıdır**
  (`state/tv_events.json`, D19a D): restart tüketilmiş bir olayı YENİDEN tetiklemez.
  **Başarısız** bir aksiyon olayı tüketmez; en fazla 3 denemede uygulanamazsa olay
  bırakılır (pozisyon normal korumasında kalır) — sonsuz yeniden deneme yok.
- **FAIL-OPEN:** bu bir SİNYAL kanalıdır. Defter bozuksa/okunamıyorsa girişler
  DURMAZ ve pozisyon KAPANMAZ; bugünkü davranış aynen sürer. (Risk kapıları —
  risk-olayı halt, kill switch, entry latch — fail-CLOSED'dır; bu değil.)
  **TEK İSTİSNA:** TV çıkış dalında `UnprotectedPositionError` görülürse entry-halt
  latch'i tetiklenir (D10 deseni, D19a C) — korunamayan pozisyon her yolda aynı
  ciddiyettedir.

### 7.5 Telemetri, kalıcılık ve bakım uçları
- `GET /scalper/status` → `tv_events`: `mode`, `exit_action`, `exit_losing`,
  `max_age_minutes`, `window_open`, `gate_enabled`, `gate_sources`,
  `event_sources`, `allowlist_ok`, `allowlist_missing`, `symbol_allowlist`,
  `persist` (`ok`/`errors`/`last_error`/`last_error_at`/`path`), `counters`,
  `counters_since` ve `symbols` (sembol → `structure`, `structure_source`,
  `structure_age_s`, kaynak bazlı `structures` (+`via`), `last_event`, `last_exit`,
  `consumed`). **Secret içermez.**
- **Sayaç sözleşmesi** (D19a G8): `gate_hits == would_block + blocked` ve
  `exit_hits == would_exit + exits_attempted`, `exits_attempted == exits_applied +
  exits_noop + exits_failed`. `gate_hits`/`exit_hits` HER İKİ modda artar, böylece
  gölge ölçümü aktif ölçümle birebir kıyaslanabilir.
  ⚠️ **`exits_applied` yalnız BORSAYA GERÇEKTEN İSTEK GİTTİĞİNDE artar**
  (D19a-2 R2-3): stop gerçekten taşındı ya da pozisyon kapandı. Hiçbir isteğin
  gitmediği durumlar (`stop zaten en az BE kadar koruyucu`, `zararda + skip`)
  **`exits_noop`**tur. Gölge tarafındaki karşılığı **`would_exit_noop`** = "aktifte
  borsaya hiçbir istek GİTMEZDİ" (`ExitManager.breakeven_would_act`, yan etkisiz).
  Terfi kararında `would_exit`in ham sayısı değil, `would_exit - would_exit_noop`
  okunur.
  Diğerleri: `ingested`, `mixed_skipped`, `exits_skipped_losing`,
  `exits_closed_losing`, `rejected_entry_from_event_source`,
  `rejected_symbol_allowlist`.
- Log satırları: `🧭 TV olayı: SYMBOL kind=… dir=… ← src` (alma),
  `⛔ … TV yapı kapısı` (aktif ret), `👻 … GÖLGE` (gölge),
  `🤷 … ÇELİŞİYOR` (MIXED), `🛑 … pozisyon zararda` (BE atlandı),
  `🚫 TV olayı uygulanmadı: … TV sembol allowlist'i dışında`,
  `⛔ TV webhook: olay kaynağı … GİRİŞ OYU gönderdi` (422),
  `🧪 TV olayı (DRY-RUN, deftere YAZILMADI)`, `🧹 TV olay defteri sıfırlandı`,
  `⚠️ TV olay kanalı: …` (startup yapılandırma sağlığı),
  `🛡️ … SL ücret-dahil BE'ye çekildi` (BE).
- Durum dosyası `state/tv_events.json` (atomik yazım: tmp + `os.replace` + fsync).
  Olaylar, tüketim imleçleri, deneme sayaçları ve telemetri sayaçları AYNI dosyada.
  Olay ve tüketim yazımları ANINDA kalıcıdır; telemetri sayaçları saniyede bir
  yazılır (D19a-2 R2-8 — `_persist` ~2.5 ms'lik senkron bir işlemdir ve alarm
  hacmiyle orantılı olarak event-loop'u bloklardı). Açık pozisyonlu semboller
  defter budamasından MUAFTIR (`TvEvents.protect`, D19a-2 R2-6).
  **Bozuk dosya = boş durum + WARNING** (fail-closed DEĞİL, yukarıdaki gerekçe).
  v1 (ilk D19) dosyası atılmaz, imleçsiz olarak yükseltilir. Yazılamayan bir yol
  kanalı durdurmaz: defter RAM'de çalışır, WARNING **dakikada bir** (log seli
  koruması) yazılır, sağlık `status.tv_events.persist`'te görünür.
- **`POST /tv-signal?dry_run=1`** — isteği doğrular ve yönlendirme kararını döndürür
  ama **DEFTERE YAZMAZ** (kurulum doğrulaması canlı defteri kirletmesin).
- **`POST /tv-events/reset?secret=<TV_WEBHOOK_SECRET>`** — defteri RAM **ve** diskte
  sıfırlar. ⚠️ Dosyayı elle silmek ÇALIŞAN süreci temizlemez: RAM otoritedir ve bir
  sonraki yazımda dosyayı geri yazar. Doğru reçete bu uç ya da restart'tır
  (`docs/RUNBOOK.md`). Defteri boşaltmak bir RİSK kapısını açmaz — en kötü sonucu
  kapı/çıkış tetiğinin veri gelene kadar sessizleşmesidir.

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
