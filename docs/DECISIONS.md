# Karar günlüğü (ADR) — her canlı ayarın NEDEN'i ve kanıtı

Biçim: **Karar** · Tarih · Durum · Kanıt (pencere, komut, sonuç, log yolu) · Geri alma.
"Denendi, reddedildi" kararları da buradadır — aynı fikri ikinci kez denemeden önce bak.
Tüm backtest'ler: C-only, 8 majör allowlist, sunucu env'i, kapı-pariteli harness (≥ 7640c0a).
Pencereler: AYI 2026-01-23→02-13 (BTC −30%) · YATAY 2026-07-01→07-21 · BOĞA 2026-08-07→08-21.

## Aktif kararlar

### D1 — Yalnız strateji C aktif (`SCALPER_STRATEGIES=C`) · 2026-08-19 · AKTİF
A (trend kırılması) PF 0.35, B örneklemsiz, D (EQH/EQL) −660 ve C'yi zehirliyor (slot
işgali). Kanıt: 14g×8 majör sweep'leri (kapı öncesi harness; yön bilgisi geçerli, mutlak
sayılar değil). Geri alma: env.

### D2 — Chandelier ATR çarpanı 2.5→3.5 · 2026-08-19 · AKTİF
C-only 14g: −2401→+1092, kazanma ↑. Yedek `backups/env.bak-20260819-chandelier`.

### D3 — Runner payı %40 (`SCALPER_TP2_FRACTION=0.20`) · 2026-08-21 · AKTİF
+1608 / PF 1.08 (chandelier 3.5 üstüne). TP2'yi tamamen kaldırmak eşdeğer (+1551).
Yedek `env.bak-20260821-runner40`.

### D4 — Reaper: TP1 görmemiş pozisyonu 8 saatte kapat; trailing_active muaf · 2026-08-21 · AKTİF
Kullanıcı kararı ("tek durduracak şey stop loss"): BE korumalı pozisyonlara üst kapak yok.
`engine.py:_reap_aged_positions`, `SCALPER_MAX_HOLD_HOURS`.

### D5 — Rejim kapısı (C + TV): DOWN'da LONG / UP'ta SHORT yasak · 2026-08-16/19 · AKTİF
Rejim = EMA50/200 (`SCALPER_TF_REGIME=15m`, `regime.py`). Kanıt (ayı penceresi):
kapı açık PF 0.97 / −2042, kapı kapalı PF 0.68 / **−36506**; 377 düşen-bıçak LONG engellendi,
maxDD 41.7k→11.9k. Log: scratchpad `W_BEAR_gate_on.log`/`W_BEAR_gate_off.log` (2026-08-21).
TV'ye de uygulandı çünkü TV sinyalleri 2 günde −41 USDT etmişti.

### D6 — C diverjans şartı AÇIK (`SCALPER_C_REQUIRE_DIVERGENCE=true`) · 2026-08-21 14:3x · AKTİF
24 koşuluk E2/E3 setinde üç pencerede birden kazanan tek varyant:
AYI 0.97→**1.06** (+886) · BOĞA 1.24→**2.18** (+3831) · YATAY 0.93→**1.33** (+2745);
işlem 814/191/449 → 216/96/150; maxDD 11857/3610/8254 → 3574/735/3181; SL 120→29.
Yedek `env.bak-20260821-divergence`. Restart `supervisorctl restart tradingbot_v2`.
Beklenti: günlük işlem ~4× az, işlem başına kalite ↑. İzleme: 5 gün sonra canlı defter.
Geri alma: yedeği kopyala + restart.

### D7 — TV sembol allowlist BTC/ETH/XRP/SOL/BNB · 2026-08-20 · AKTİF
LuxAlgo backtester'ları (5m, varsayılan): ETH/XRP/BTC üç pakette de pozitif, LTC üçünde de
negatif, BNB/ADA/DOGE karışık. Yedek `env.bak-20260821-tvallow`.

### D8 — Stop modu fixed_roi %50, dinamik kaldıraç 3-20x, ATR tabanı 0.5, kayıp cooldown 60 dk · 2026-08-11 · AKTİF
BEAT çöküşü (7 dk'da 4 SL): yapısal stop dibe yapışıyordu + yeniden giriş engeli yoktu.

### D9 — Webhook sertleştirme: `?src=` allowlist + erişim logu secret redaksiyonu · 2026-08-21 · AKTİF
Ne: (1) `TV_SOURCE_ALLOWLIST` (varsayılan `luxosc,luxso,algopro,botv3,tv`) — `/tv-signal`
`?src=` artık normalize edilip (küçük harf/trim) bu kümeye karşı doğrulanır; bilinmeyen
değer REDDEDİLMEZ, "tv" jenerik kaynağına eşlenir ve WARNING loglanır, yanıt
`source_raw_rejected: true` taşır. (2) `uvicorn.access`/`uvicorn.error` logger'larına
modül import anında (`src/main.py`, idempotent) bir `logging.Filter` eklendi —
`secret=<değer>` kalıbını msg VE args içinde `secret=***`'e çevirir.
Neden: `?src=` serbest metin olduğu için bir yazım hatası (ör. "algpro") sessizce
hayalet bir kaynak yaratıyordu — TvConfluence'ta asla farklı kaynak sayısını
dolduramayan, hiç fark edilmeyen bir sinyal kaybı (bkz. D5/TV notu). Ayrıca webhook
secret'ı `?secret=...` query'sinde taşınabiliyor (LuxAlgo "Any alert" modu, bkz.
`resolve_tv_signal`) ve uvicorn'un erişim logu tam istek satırını düz metin yazıyordu
(Güvenlik borçları #1 — RUNBOOK.md). Kanıt: `tests/test_tv_signal_bridge.py`
(`TestTvSourceAllowlist`, `TestTvWebhookSourceLogging`) ve
`tests/test_access_log_redaction.py` — `python3 -m pytest tests -q` → 487 passed, 1 skipped.
Geri alma: `src/main.py`/`src/core/config.py`'deki bu değişiklikleri revert et; davranışsal
risk yok (kabul mantığı gevşetildi, hiçbir sinyal reddedilmiyor).

### D10 — Risk-olayı kanalı: `POST /risk-event` (halt/resume/flatten/status) · 2026-08-21 · AKTİF
**Ne:** `docs/INTEGRATIONS.md` §3'te planlanan risk-olayı kanalı uygulandı. Haber/olay
botlarının strateji mantığına DOKUNMADAN scalper girişlerini durdurup/devam ettirebildiği
veya tüm açık pozisyonları acilen düzleştirebildiği ayrı bir uç nokta:
- `RISK_EVENT_SECRET` — TV webhook secret'ından AYRI, boş = 503 ile kapalı (aynı desen).
- `state/risk_event_halt.json` — `state/scalper_entry_halt.json`'dan (koruma-hatası
  otomatik latch'i) BİLİNÇLİ olarak AYRI dosya; `SCALPER_ENTRY_HALT_ENABLED`
  bayrağından TAMAMEN BAĞIMSIZ her zaman uygulanır (o bayrak yalnız
  `UnprotectedPositionError` latch'ini gater — canlı sunucu bunu `false` tutuyor).
  Motor'un TEK giriş kapısı `_entries_ready()`'ye eklendi; bu yüzden hem scanner'ın C
  stratejisi (`_evaluate_symbol`) hem TV dış sinyali (`external_signal`) aynı anda kapanır.
  Fail-closed: dosya bozuk/parse edilemezse HALT AKTİF sayılır (`_load_entry_halt` ile
  aynı ilke). TTL ile kendiliğinden süresi dolar (varsayılan 120dk, azami 1440dk).
- `flatten`: reaper'ın (`_reap_aged_positions`) kullandığı AYNI reduce-only MARKET emir
  çağrısını (`_submit_reduce_only_market_close`) yeniden kullanır — YENİ bir emir yolu
  YAZILMADI. Halt, kapatma turundan **ÖNCE** kurulur (bkz. aşağıdaki "Düşmanca inceleme
  düzeltmeleri" — ilk sürümde SONRA kuruluyordu, bu bir kusurdu). Her sembolün kapanışı
  borsa üzerinde (`positionAmt==0`, `force_fresh=True` ile) doğrulanmadan
  `exits._handle_closed` ÇAĞRILMAZ (fail-closed — aksi halde SL/TP iptal edilip pozisyon
  korumasız kalabilirdi). Doğrulanan kapanışlar `exit_reason="RISK_EVENT"` ile kaydedilir
  (`exits._handle_closed`'a yeni `forced_exit_reason` parametresi eklendi — PnL/fiyat
  doğrulaması AYNI, yalnız etiket zorlanıyor). Kapatma turu bittikten sonra `tracked_symbols()`
  **İKİNCİ kez** taranır — halt kurulmasıyla eşzamanlı (ör. WS fill yarışı) dolan bir pozisyon
  varsa o da düzleştirilir.
- Açık pozisyonların SL/TP/trailing yönetimi bu kanaldan HİÇ etkilenmez.

**Düşmanca inceleme düzeltmeleri (aynı gün, commit'e girmeden önce):** 3 mercekli (21 ajan)
düşmanca bir inceleme 6 gerçek kusur buldu; hepsi AYNI gün düzeltildi ve `docs/DECISIONS.md`
committen ÖNCE güncellendi (bu commit hiçbir zaman kusurlu haliyle canlıya çıkmadı):
1. **Halt sırası (yukarıda anlatıldı):** `risk_event_flatten` halt'ı kapatma turundan SONRA
   kuruyordu — tur onlarca saniye sürebildiğinden (`scalper_max_positions` kadar sembol ×
   ~4sn + ledger REST'i) tarama döngüsü bu pencerede YENİ pozisyon açabilirdi ve o pozisyon
   tur başındaki tek-atımlık `tracked_symbols()` anlık görüntüsüne girmediği için asla
   kapanmazdı. Düzeltme: `risk_event_halt` (bekleyen maker'ları da halt ALTINDA iptal eder)
   turdan ÖNCE çağrılır; tur sonrası ikinci bir tarama eşzamanlı dolumu yakalar.
2. **Ölü retry döngüsü:** kapanış doğrulaması `get_position_risk`'i `force_fresh=True` OLMADAN
   çağırıyordu — 5sn'lik pozisyon snapshot önbelleği ilk okumadan sonraki 4 denemeyi aynı
   bayat (sıfır olmayan) kayda düşürüyor, flatten fiilen kapanmış pozisyonları "doğrulanamadı"
   diye `errors`'a yazıyordu. Düzeltme: boyutlama VE doğrulama okumaları `force_fresh=True`.
3. **Bayat miktar:** reduce-only MARKET, `sp.position.quantity` (giriş dolumu, kısmi TP
   sonrası ASLA güncellenmez) ile boyutlanıyordu — TP1/TP2 dolmuş bir koşucuda canlının
   1.6-3.3 katı miktar göndermek -2022 (NON_RETRYABLE) reddi riskiydi. Düzeltme: miktar CANLI
   `positionAmt`'tan (`force_fresh=True`) alınır; `position_manager._emergency_close` ile aynı
   desen.
4. **Çift finalize:** flatten'ın doğrudan çağırdığı `exits._handle_closed` ile safety
   döngüsünün (`exits.step()`) AYNI sembolü eşzamanlı finalize edebilmesi — `cancel_all_open_
   orders`/ledger/income REST'i iki katına çıkarıyor, `record_close`'u üzerine yazıp
   `RISK_EVENT` etiketini kaybettirebiliyordu. Düzeltme: `ExitManager._closing: Set[str]`
   tek-finalizer kapısı (check+add arasında await yok, tek event-loop'ta atomik).
5. **Sessiz fail-open:** `state/risk_event_halt.json` yazılamazsa (disk dolu/salt-okunur)
   halt hiç RAM'de tutulmuyordu — `_entries_ready()` True kalıyor, endpoint yine de
   `ok:true` dönüyordu. Düzeltme: `_risk_event_halt_ram` latch'i persist'ten ÖNCE kurulur ve
   `_risk_event_halt_snapshot`'a max(RAM, dosya) olarak katılır; yanıt artık `persisted: bool`
   alanı taşır, `ok` gerçeği yansıtır (halt: `active`; flatten: `not errors`; resume:
   `not active`).
6. **Loguru/hmac ikincil kusurlar:** `reason`/`source` içindeki `{...}` loguru'nun
   `.format()`'unu (kwarg'lı `critical(..., extra=...)` çağrısı yüzünden) tetikleyip
   KeyError/IndexError ile 500'e düşürüyordu (`.bind(trade=True).critical(...)`'a geçildi);
   `hmac.compare_digest(str, str)` ASCII-dışı secret'ta TypeError ile 500 veriyordu
   (`_constant_time_equals`'a geçildi, UTF-8 encode eder); `ttl_minutes: Infinity` →
   `int(float('inf'))` `OverflowError` fırlatıyordu, `except (TypeError, ValueError)`
   yakalamıyordu (tuple'a eklendi).

**Neden:** `docs/INTEGRATIONS.md` §2.4 — "savaş çıktı, her şeyi kapat" tipi olaylar yön
sinyali DEĞİL, ayrı bir risk-olayı kanalı gerektiriyordu; TV webhook'u yalnız yön önerir
ve sağlamadan (tv_confluence) geçer, tek bir haber botu tek başına giriş açtıramaz — ama
"her şeyi durdur" kararı sağlamaya TABİ OLMAMALI (bir kaynağın acil müdahalesi yeterli
olmalı). `SCALPER_ENTRY_HALT_ENABLED=false` canlı sunucuda aktif olduğu için mevcut
`scalper_entry_halt` latch'i bu amaca uygun değildi (bkz. `engine.py` `_load_entry_halt`/
`_latch_entry_halt` — bayrak kapalıyken hem yükleme hem yeni latch ATLANIYOR); o yüzden
YENİ ve bağımsız bir dosya/bayrak gerekti.

**Kanıt:** `tests/test_risk_event.py` — 50 test (29 orijinal auth/doğrulama/dispatch +
motor-seviyesi halt/resume/ttl-expiry/fail-closed/flatten, + yukarıdaki 6 düşmanca-inceleme
düzeltmesi için 21 regresyon testi, aynı gün commit'e girmeden eklendi).
`python3 -m pytest tests -q` → 540 passed, 1 skipped (önceki: 490 passed). Backtest'e
DOKUNULMADI — risk-olayları yalnız canlı motoru etkiler (bkz. `docs/INTEGRATIONS.md` §3
"Backtest paritesi").

**Geri alma:** `RISK_EVENT_SECRET`'i `.env`'den kaldır/boş bırak → endpoint 503 ile
kendiliğinden kapanır (kod geri alınmasına gerek yok). Tam geri alma gerekirse: bu
commit'teki `src/main.py` (`/risk-event`), `src/strategies/scalper/engine.py`
(risk-olayı bölümü + `_entries_ready`/`_evaluate_symbol`/`external_signal` içindeki
risk-event dalları + `_reap_aged_positions` refactor'u), `src/strategies/scalper/exits.py`
(`_handle_closed` `forced_exit_reason` parametresi), `src/core/config.py`
(`risk_event_secret`/`risk_event_halt_path`) değişikliklerini revert et.

### D11 — Chandelier 3.0 (autoresearch E4b) · 2026-08-21 · ADAY, UYGULANMADI
Tur-1: AYI 1.06→1.07 (+248), YATAY 1.33→1.35 (+147), BOĞA 2.18→2.20 (+36); toplam +431 (≈%5.8), DD benzer.
P2'yi "AYI ve YATAY birlikte iyileşti" koluyla geçti, AYI PF hâlâ <1.1. Kenar ince; D6'nın testnet soak'u
(≥5 gün) bitmeden uygulanmaz — değişiklikler üst üste bindirilmez (soak kirlenir). Yeniden değerlendirme:
D6 soak raporuyla birlikte; o zaman da boğa kazancını korumalı. Log: logs/autoresearch/2026-08-21/.

### D12 — TP1 %10→%8 (autoresearch E4f) · 2026-08-21 · ADAY (en güçlü), UYGULANMADI
Tur-2: AYI 1.06→1.13 (+1573), YATAY 1.33→1.40 (+2744), BOĞA 2.18→2.90 (+4203); maxDD üç pencerede
↓ (3574→2604, 3181→2154, 735→514); WR ↑; işlem 229/162/104. Mekanizma: BE'ye daha erken geçiş → daha az SL.
Alternatif E4g TP1 %12 (+1713) boğa PF'yi düşürüp ayı DD'yi artırıyor → E4f tercih.
Kapasite-kapılı harness ile yeniden doğrulama (2026-08-22 01:5x; yeni taban AYI 1.04/DD 3683, YATAY 1.29/3229,
BOĞA 2.43/735): E4f AYI 1.12/+1415/DD 2604 · YATAY 1.38/+2604/2154 · BOĞA 3.71/+4598/514 (PF↑ DD↓ her yerde);
E4g +2204 toplam ama AYI DD 4107 (tabanın üstü), PF 1.07 → risk-ayarlı tercih E4f değişmedi. Uygulama zamanı:
D6 soak'u (≥5 gün) bittikten sonra; kullanıcı isterse daha erken (atıf bulanıklaşır). Log: logs/autoresearch/2026-08-21/.

### D13 — Kaldıraç tavanı bulgusu (E4i/E4j) · 2026-08-21 · ARAŞTIRMA
DYN_LEV_MAX 20→10: AYI PF 1.68 / +5624 (taban +886), BOĞA 55 işlem (örneklem yetersiz); 15: ayı +3293, boğa −%39.
Geniş stop (düşük kaldıraç) ayıda SL'leri keskin azaltıyor. Tur-3 adayı: DYN_LEV_MAX 12-15 + TP1 8 birleşimi.
Not (P1): harness SCALPER_MAX_POSITIONS kapasitesini modellemiyor (E4h taban ile birebir aynı) — parite boşluğu.

### D14 — Gölge modu (`SCALPER_SHADOW_MODE`) · 2026-08-22 · AKTİF
**Ne:** `SCALPER_SHADOW_MODE=true` iken `ScalperEngine`/`ScalpExecutor.try_open` sinyali
BUGÜNKÜ GİBİ tüm kapılardan geçirir (cooldown, bakiye, stop-mesafesi/R:R, boyutlama, borsa
filtresi doğrulaması — leverage/margin bu GERÇEK hesaplamadan gelir) ama adım 6'dan (margin
type + leverage ayarı) itibaren HİÇBİR borsa isteği göndermez: margin/leverage AYARLANMAZ,
emir GÖNDERİLMEZ, SL/TP YOKTUR, pozisyon izlenmez. Bunun yerine sinyal `scalp_trades`'e
`status="SHADOW"`, `entry_price=sinyal fiyatı` (gerçek dolum değil), `notes="shadow_mode"`
olarak yazılır ve `try_open` `None` döner — engine bunu normal bir "sinyal reddedildi"
sonucu gibi ele alır (rezervasyon serbest kalır, `tracked`/`pending`'e hiç girmez).
`tracker.stats()`/`open_trades()` yalnız `CLOSED`/`OPEN` sorguladığı için SHADOW satırları
istatistiklerden ve restart kurtarmasından KENDİLİĞİNDEN dışlanır — ayrı bir filtre eklemeye
gerek kalmadı. Kapasite sayımı ayrı bir mekanizmayla (`shadow_active_count`, aşağıdaki "Review
düzeltmesi") sınırlanır — SHADOW satırları oraya KENDİLİĞİNDEN girmez, bilinçli sayılır.
`/scalper/trades` varsayılanı da aynı nedenle SHADOW'u
gösterMEZ; `?include_shadow=1` ile görülebilir. Cooldown/loss-cooldown gölge girişiyle
TETİKLENMEZ (hiçbir risk alınmadı) ama MEVCUT cooldown gölge sinyalini de engellemeye devam
eder (kapı bugünküyle AYNI). Başlangıçta `⚠️ GÖLGE MODU AÇIK — emir gönderilmez` YÜKSEK
SESLE loglanır (`ScalperEngine._maybe_log_shadow_mode_banner`); `/scalper/status`
`shadow_mode` alanını dışa verir.

**Neden:** docs/MAINNET_PLAN.md §3/§5.2 — mainnet'e geçişte yeni bir parametreyi veya
mainnet'in kendisini gerçek parayla riske girmeden 3 gün gözlemlemek için. Ayrıca
`_validate_binance_environment` (config.py) artık mainnet'te (testnet DEĞİLKEN) gölge
KAPALIYSA `RISK_EVENT_SECRET`, `TV_WEBHOOK_SECRET` ve `SCALPER_SYMBOL_ALLOWLIST`'in dolu
olmasını ZORUNLU kılıyor (docs/MAINNET_PLAN.md §5.3) — gölge modu bu üç korumayı BYPASS
edebilen TEK istisna, çünkü emir zaten gitmiyor. `SCALPER_ENTRY_HALT_ENABLED=false` kontrolü
gölge modundan bağımsız her zaman uygulanır (bypass edilmez).

**Kapsam dışı bırakılan tasarım kararı:** margin/leverage ayarı (adım 6, `set_margin_type`/
`set_leverage`) da gölge modda ATLANIR — bunlar borsa hesabının GERÇEK leverage/margin type
ayarını değiştiren mutasyon çağrılarıdır; bir gölge sinyalin canlı hesabı sessizce
değiştirmesi (paralel çalışan gerçek executor'la çakışma riski) kabul edilemezdi. Yalnız
adım 1-5 (bakiye okuma + yerel/önbellekli borsa filtresi doğrulaması) çalışır — bunlar
mutasyon değildir ve gerçekçi leverage/margin sayıları için gereklidir.

**Kanıt:** `tests/test_shadow_mode.py` — 19 test: executor'da emir/margin/leverage çağrısı
gitmediği + SHADOW kaydı yazıldığı + kapasite/cooldown etkilenmediği (gölge açık), bugünkü
yolun değişmediği (gölge kapalı), gerçek (geçici) SQLite üzerinde stats/open_trades'in SHADOW
satırını dışladığı, mainnet doğrulamasının (bypass + eksik secret reddi) ve başlangıç
bannerının davranışı. `python3 -m pytest tests -q` → 592 passed, 1 skipped (önceki: 573).
Backtest harness'e DOKUNULMADI — gölge modu yalnız canlı `try_open`'ı etkiler, harness zaten
borsaya hiç çıkmaz (bkz. docs/MAINNET_PLAN.md §5 madde 2 "canlı-only").

**Review düzeltmesi (2026-08-22, aynı gün — adversarial review, 11 ajan, 9 bulgu/5 doğrulandı):**
Dal ilk halinde bir soak'ı SAYILAMAZ kılan bir HIGH bulgu vardı: gölge dalı hiçbir occupancy
bırakmadığı (yukarıdaki "Ne" — `tracked`/`pending`'e hiç girmez) için AYNI sinyal her tarama
turunda yeniden yazılıyordu — gerçek repro'da bir sinyal olayı 1.7-3.0×, kalıcı bir koşulda
sınırsız satıra şişiyordu (bkz. bulgu detayı: `docs/AUTORESEARCH.md`'ye değil, incelemenin ham
çıktısına — `/private/tmp/.../wo03lnjit.output`'ta arşivlendi, bu repoda değil). İki düzeltme:
1. **Tekilleştirme penceresi** — `ScalpExecutor._shadow_recent: Dict[str,float]`, `_cooldowns`'a
   DOKUNMADAN (gerçek girişlerin cooldown semantiği aynı kalır — `test_cooldown_not_started_
   by_shadow_entry` hâlâ geçer). Aynı sembol `SCALPER_SHADOW_DEDUP_MINUTES` (boşsa
   `SCALPER_LOSS_COOLDOWN_MINUTES`'e, o da yoksa 60 dk'ya düşer) içinde ikinci kez SHADOW
   satırı yazmaz. Mevcut cooldown budama noktasında (`_prune_cooldowns`) birlikte temizlenir.
2. **Kapasite sayımı** — `ScalpExecutor.shadow_active_count()` (pencere içindeki sembol sayısı)
   `ScalperEngine._evaluate_symbol`'ün kapasite kapısında (`engine.py` ~1253) gölge modda
   `open + shadow_active` olarak `SCALPER_MAX_POSITIONS`'a karşı sayılır — dolunca `👻 GÖLGE
   kapasite dolu` loglanır, sinyal deftere yazılmaz.

   Not (dürüstlük payı): incelemenin AYRI bir bulgusu — başlığı "Capacity gate never engages in
   shadow mode" — doğrulama turunda REFUTED edildi; gerekçesi kapasiteyi SHADOW satırlarının
   KALICI işgaliyle sayan bir tasarımın (satır hiç kapanmadığı için) `SCALPER_MAX_POSITIONS`
   sonrası defteri sonsuza dek SUSTURACAĞI idi — bu, gölge modun var oluş amacını yok ederdi.
   Buradaki uygulama KALICI değil, PENCERELİ (`shadow_active_count`, madde 1'deki tekilleştirme
   penceresiyle aynı süre) — pencere dolunca sembol tekrar sayılabilir hale gelir, defter
   susmaz. Bu, ayrı ve DOĞRULANMIŞ bir bulgunun ("Shadow ledger over-counts entries... bir
   canlı işlem N SHADOW satırına dönüşüyor") istediği "gölge satır sayısı canlıyla
   kıyaslanabilir olsun" hedefini, refute edilen bulgunun uyardığı kalıcı-susma tuzağına
   düşmeden karşılar.
3. **Mainnet koruma kapısı** (`config.py` ~465, HIGH) — `risk_event_secret`/`tv_webhook_secret`/
   `scalper_symbol_allowlist` bare truthiness ile kontrol ediliyordu; tırnaklı boşluk
   (`RISK_EVENT_SECRET="   "`) veya `SCALPER_SYMBOL_ALLOWLIST=","` GEÇERLİ sayılıp korumaları
   sessizce devre dışı bırakabiliyordu — tüketiciler (`main.py`, `engine.py`) zaten `.strip()`
   uyguluyordu, validator uygulamıyordu. Üç kontrol de artık tüketicilerle BİREBİR aynı süzgeci
   kullanıyor.
4. **RUNBOOK "Açmak" tek satırlığı** (`docs/RUNBOOK.md` ~127, HIGH) — `sed -i` eşleşme
   bulamazsa exit 0 verir, bu yüzden `|| echo ... >> .env` yedeği hiç ÇALIŞMIYORDU ve zincir
   restart'a kadar devam ediyordu: `.env`'de `SCALPER_SHADOW_MODE=` satırı yokken (canlı
   sunucudaki gerçek durum) operatör "gölge açıldı" sanırken bot GERÇEK emir göndermeye devam
   ediyordu. Sunucuda tekrar üretilerek doğrulandı. Düzeltme: `{ grep -q ... && sed ... ||
   echo ...; }` grubu + restart'tan ÖNCE `assert`'li config geri-okuması + restart'tan SONRA
   `GET /scalper/status` doğrulaması — ikisi de geçmeden soak BAŞLAMIŞ SAYILMAZ. "Kapatmak" için
   simetrik komut eklendi (öncesinde yalnız düz yazıydı).

**Bilinçli sınır (kapsam dışı bırakıldı):** tam bir simüle-pozisyon/çıkış modeli (gölge
girişin SL/TP/max-hold ile "kapanması") kurulmadı — bu, harness paritesini de gerektirecek çok
daha büyük bir değişiklik olurdu (CLAUDE.md kural 2) ve incelemenin kendisi de bunu "yalnız
şişmeyi gider, daha büyük değişiklik gerektirir" diye ayırdı. Pencereli tekilleştirme + pencereli
kapasite, göreli büyüklük mertebesini doğru kılar; gölge PnL/exit_reason hâlâ YOKTUR (RUNBOOK
"istatistik/PnL anlamı yok" — bilinçli, değişmedi).

**Kanıt (review düzeltmesi):** `tests/test_shadow_mode.py` — dedup penceresi (aynı executor'da
art arda iki `try_open`ın TEK `record_shadow` yazdığı, pencere sonrası ikincinin yazıldığı),
kapasite kapısı (3 farklı sembol + `SCALPER_MAX_POSITIONS=2` → yalnız 2 satır, gerçek
`ScalperEngine._evaluate_symbol` üzerinden), config whitespace/virgül (`" "`, `",,"` mainnet'te
hâlâ reddedilir). `tests/test_scalper_backtest.py` DOKUNULMADI — bu düzeltme yalnız canlı
`try_open`/`_evaluate_symbol`'ı etkiler.

**Geri alma:** `.env`'den `SCALPER_SHADOW_MODE`'u kaldır/`false` yap (varsayılan zaten
`false` — davranış değişmez). Tam geri alma gerekirse bu commit'teki `src/core/config.py`
(`scalper_shadow_mode` + `scalper_shadow_dedup_minutes` alanları + `_validate_binance_
environment` mainnet bloğu — strip/allowlist düzeltmesi dahil), `src/strategies/scalper/
executor.py` (`try_open` gölge dalı + `_shadow_recent`/`_shadow_dedup_seconds`/
`shadow_active_count`), `src/strategies/scalper/tracker.py` (`record_shadow`),
`src/strategies/scalper/engine.py` (`_maybe_log_shadow_mode_banner` + `snapshot()`
`shadow_mode` alanı + `_evaluate_symbol`'daki gölge kapasite dalı), `src/main.py`
(`_EMPTY_SCALPER_STATUS`/`scalper/trades` `include_shadow`) değişikliklerini revert et.
Yalnız review düzeltmesini geri almak gerekirse (D14'ün kendisi kalsın): executor.py'deki
tekilleştirme bloğu + `shadow_active_count`, engine.py'deki kapasite dalı, config.py'deki
`.strip()`/allowlist filtresi ve RUNBOOK.md "Gölge modu" bölümünü bu commit'ten önceki
haline döndür — üçü de bağımsız, birbirine muhtaç değil.

### D16 — A-plus risk paketi (marj %5 · stop ROI %40 · TP1 %8 · günlük kesici %6) · 2026-08-23 · **GERİ ALINDI 03:10 sunucu saati (01:10 UTC) (kullanıcı kararı)**
**Geri alma gerekçesi (kullanıcı, 2026-08-23):** "yüzde 10'u kullanacaksın her işlem için ve TP1 yüksek olacak;
yapman gereken ayarlardan ziyade doğru sinyali bulmak veya üretmek." Boyut/TP/stop ayarlarıyla kaybı
küçültmek KABUL EDİLMEZ; çözüm giriş SİNYALİ kalitesidir (lider-kapısı D15 adayı, yeni sinyal kaynakları).
`cp backups/env.bak-20260823-025623-riskpaketi .env` + restart (pid 1528089, sağlık 40 sn, read-back
10/50/10/10). E6e ölçümü bilgi olarak kalır; uygulanmaz.

**Ne:** sunucu `.env`: `SCALPER_MAX_MARGIN_PCT 10→5`, `SCALPER_FIXED_STOP_ROI_PCT 50→40`,
`SCALPER_TP1_ROI 10→8`, `SCALPER_DAILY_LOSS_LIMIT_PCT 10→6`; 02:56 sunucu saati (00:56 UTC) `supervisorctl restart
tradingbot_v2` (pid 1401284, sağlık 80 sn, read-back 5/40/8/6, 3 açık pozisyon `recover()` ile
devralındı — eski pozisyonlar eski SL'lerini korur).
**Neden:** 22 Ağu dönüş günü −133 (4 SL × ≈−83). Kök: (1) ödeme asimetrisi — defter TRAIL ort.
+%10.9 / SL ort. −%48 ROI → başabaş WR %81.5, yalnız UP rejimi (%88.6) üstünde; (2) boyut —
`fixed_roi` stopta nominal tavan her işlemde bağlayıcı → pozisyon = sermayenin %10'u, **SL =
%5 sermaye** (compounding ile büyüdü: 17→22 Ağu marj 36→162). Stop mesafesi sorun değil: 4
kaybın hiçbiri stop sonrası 4 saatte girişe dönmedi.
**Kanıt:** E6b (marj %5): PF tabanla birebir (1.04/1.29/2.43), DD yarı → boyutlama doğrusal,
P2'nin "boğa −%20" hükmü ölçek artefaktı. **E6e (stop %40 + TP1 %8): P2 GEÇTİ** — AYI
1.04→1.40 (+584→+3923, DD 3683→1937), YATAY 1.29→1.43 (+2392→+2888), BOĞA 2.43→1.99
(+3902→+3280, −%16). E6d (stop %40 tek) boğayı −%29 bozdu; E3a (%30) felaketti → kaybı
küçültmek yalnız erken BE ile birlikte çalışır. Günlük kesici harness'ta modellenmez
(koruma katmanı; `_update_kill_switch`, Binance income tabanlı). `docs/EXPERIMENTS.md` E6,
`docs/superpowers/specs/2026-08-22-reversal-day-loss-design.md`.
**Beklenti:** SL = sermayenin %2'si; kazançlar yarı ölçek; 22 Ağu benzeri gün ≈ −50; her rejimde
PF > 1.4 ama boğada toplam PnL tabanın ~%42'si (bilinçli tercih: "her rejimde ayakta kal").
**Soak:** D6+D16 DEMET; başlangıç 2026-08-23 02:57 sunucu saati (00:57 UTC) → `scripts/ledger_report.py --since
"2026-08-23 02:57"`; değerlendirme ≥28 Ağu + ≥1 DOWN günü.
**Geri alma:** `cp backups/env.bak-20260823-025623-riskpaketi .env && supervisorctl restart tradingbot_v2`.

### D17 — Piyasa verisi ayrı host: `SCALPER_MARKET_DATA_BASE_URL` · 2026-08-23 · **ADAY, VARSAYILAN KAPALI** (canlıda uygulanmadı)
**Ne:** Yeni ayar `SCALPER_MARKET_DATA_BASE_URL` (boş = bugünkü davranış, birebir).
Doluyken YALNIZ public `/fapi/v1/klines` çekimi o host'tan yapılır; emir, bakiye, pozisyon,
`ticker/24hr` (evren taraması), `exchangeInfo`, `income` ve tüm imzalı yollar
`BINANCE_BASE_URL`'de KALIR — API anahtarı bu host'a asla gitmez. Kablolama tek satır:
`ScalperEngine.__init__` → `KlineFetcher(base_url=settings.scalper_market_data_base_url or None)`.
`ExitManager` aynı fetcher örneğini kullandığı için giriş ve trailing mumları AYNI kaynaktan gelir.
**Emir fiyatları etkilenmez (kodda doğrulandı):** maker LIMIT fiyatı işlem host'unun
`bookTicker`'ından alınır (`executor.py:1529-1534`), SL/TP gerçek dolumdan hesaplanır ve
`_delay_adjusted_stop` (`executor.py:1280-1345`) stop'u dolum kaymasına göre öteleyerek
giriş–stop MESAFESİNİ korur — iki borsa arasındaki küçük fiyat farkı (E8.0: medyan %0.054)
boyutlamayı veya koruma seviyelerini kaydırmaz. Ayrı host YALNIZ gösterge girdisidir.

**Neden (kök bulgu):** `engine.py:129` `KlineFetcher()`i argümansız kuruyordu →
`data.py` `base_url or settings.binance_base_url` → canlı bot TESTNET'te olduğu için
RSI / Bollinger / RSI-diverjansı / rejim (EMA50-200) / ATR hesaplarının TAMAMI **testnet
mumlarından** üretiliyordu. Backtest harness'i (`backtest.py:1309`) ise mainnet
`https://fapi.binance.com` okuyor: canlı motor ile harness AYNI mumları görmüyor
(**P1 paritesinin veri tarafındaki açığı** — kural tarafı kapatılmıştı, veri tarafı açıktı)
ve canlı sinyal kalitesi ölçülen backtest kalitesini temsil etmiyor.
**Ölçülmüş kanıt (depo içi):** `docs/EXPERIMENTS.md` **E8.0** (sinyal otopsisi, commit
`4d460db`): motorun `signal_reason`'a yazdığı giriş RSI'ı 143 C işleminde **testnet** 1m
serisiyle uyuşuyor (medyan |Δ| **2.8**), mainnet 1m ile uyuşmuyor (medyan |Δ| **7.4**);
`vol_ratio_5m` iki taraf arasında HİÇ taşınmıyor (Pearson r = **−0.04**, testnet ort. 206 vs
mainnet 1.73); testnet 1m mumları neredeyse durağan (23:50-23:53 BTCUSDT testnet
77079.20/77079.20/77079.10 vs mainnet 77108.0/77111.8/77073.8). Fiyat SEVİYESİ yakın
(medyan sapma %0.054) ve makro özellikler yüksek korelasyonlu (RSI 15m r=0.975) — ama C
stratejisi bir UÇ eşiğinde (RSI ≤ 25 / ≥ 75) karar verir; orada 2.8 puanlık medyan fark
sinyali doğrudan çevirir, hacim türevli hiçbir kapı ise testnet'te ölçülemez.
⚠️ Bu oturumda ayrıca aynı sınıf bir ölçüm RAPOR EDİLDİ (mainnet L 77100.0 / C 77126.8 vs
testnet L 77143.8 / C 77182.6; hacim 84 vs 1494) ama bu worktree'de ağ erişimiyle YENİDEN
ÜRETİLMEDİ — birincil kanıt E8.0'dır.

**Ağırlık/ban (kod okumasıyla bulunan ikinci kusur, aynı commit'te düzeltildi):**
`KlineFetcher` ve `UniverseScanner` bugüne kadar `rate_limiter.wait_for_binance`'i,
`_ensure_rest_allowed` ban kesicisini, `X-MBX-USED-WEIGHT-1M` telemetrisini ve 418/429
işlemesini HİÇ kullanmıyordu: 418 alan bir kline çağrısı 3 kez tekrar deniyor (yasağı
uzatıyor) ve `scripts/server_deploy.sh`'nin `HTTP 418|banned` deploy kilidine GÖRÜNMÜYORDU.
`data.py`'ye host BAŞINA `MarketDataGuard` eklendi: asyncio.Lock altında slot rezervi
(kilitsiz check-then-act yarışı yok — `rate_limiter` 2026-08-14 düzeltmesiyle aynı desen),
asgari istek aralığı 0.15 sn, kayan 60 sn ağırlık bütçesi 600/dk (IP sınırı 2400/dk; ölçülen
canlı kullanım 64-114 ağırlık/dk — hesap `docs/ARCHITECTURE.md` §2; bütçe dolarsa BEKLENMEZ,
`MarketDataBudgetError` ile tur atlanır — kilit altında beklemek safety turunun 30 sn'lik
tazelik limitini aşıp watchdog restart'ını tetikleyebilirdi), 418/429/-1003 →
fail-closed kesici + tekrar YOK + `HTTP 418` içeren CRITICAL log (deploy kilidi artık görür).
**Bilinçli asimetri:** imzalı yolun AYNI host'taki banı public çekimi DURDURUR; public banı
imzalı kesiciyi KURMAZ — `KlineFetcher` `BINANCE_BIND_IP`'ye bind edilmez (yalnız
`ImprovedBinanceClient` edilir), yani iki yol aynı host'a farklı çıkış IP'sinden gidebilir ve
public ban imzalı yolun banlı olduğunun KANITI değildir; emir/çıkış yönetimini kanıtsız
durdurmak (SL değişimi, kapanış doğrulaması) para tarafında en pahalı hatadır. Ayrı host'ta
sayaçlar/kesiciler zaten tamamen ayrıdır (Binance ağırlığı host+IP başınadır).
Motor tarafı: `_scan_tick` host-geneli bir `MarketDataUnavailable` görürse turu KESER (tek
WARNING; aksi halde 12 sembol × traceback'li ERROR basılırdı) — sinyal üretilmemesi
fail-closed'dır, açık pozisyonların SL/TP'si borsada yerinde durur.
Küresel `rate_limiter` (0.5 sn, imzalı yol) BİLİNÇLİ paylaşılmadı: 12 sembol × 3 TF × 0.5 sn
≈ 18 sn'lik tarama turu safety/exits çağrılarını aynı kuyrukta bekletirdi (dashboard
force-fresh açlığı olayının aynısı) ve `data.py`'nin 1. tasarım ilkesi "public veri emir
akışını asla bloklamamalı" der.

**Düşmanca inceleme düzeltmeleri (aynı gün, commit'e girmeden ÖNCE):** 2 mercekli (eşzamanlılık
+ para/işletme, en yüksek model) bir inceleme 3 HIGH + 8 MED/LOW gerçek kusur buldu; hepsi bu
commit'e girmeden düzeltildi — bu kod hiçbir zaman kusurlu haliyle canlıya çıkmadı:
1. **Trailing stop YABANCI fiyat uzayından emir gönderiyordu (HIGH, para riski).** `exits.
   _update_trailing` chandelier'ı market-data mumlarından hesaplar ve MUTLAK seviyeyi
   `pm.replace_stop_loss` ile İŞLEM borsasına yollar. Ayrı host'ta baz farkı `k×ATR`'yi aşarsa
   Binance -2021 verir ve `position_manager._replace_stop_loss` bunu "piyasa stop'u geçti" sayıp
   pozisyonu ACİL KAPATIR — kârlı bir koşucu borsalar arası fiyat farkı yüzünden piyasa emriyle
   kapanabilirdi; ters yönde de gerçekleşen risk boyutlamadan sapıp canlı defteri kirletirdi.
   Düzeltme: `_to_trading_price_space` — girişte ölçülen fark kadar ÖTELE (mesafe korunur),
   `executor._delay_adjusted_stop` ile AYNI desen; aynı host'ta (varsayılan) NO-OP.
2. **Ağırlık bütçesi backtest harness'ini öldürüyordu (HIGH, araştırma aracı).** Harness
   `limit=1500` (ağırlık 10) ile sayfalar: 8 sembol × 30 gün ≈ 656 > 600 → koşu ortada
   `MarketDataBudgetError` ile düşerdi (altın backtest testleri ağsız olduğu için görmezdi).
   Düzeltme: guard modu — canlı "live" (hata), harness "batch" (pencere sonuna kadar bekler;
   tek tüketici, safety döngüsü yok).
3. **`/tv-signal` HTTP 500 (HIGH).** `external_signal` → `_evaluate_symbol` sarılmamıştı; ban
   sırasında istisna FastAPI'ye sızıp 500 üretirdi ve TradingView alarmı TEKRAR gönderirdi
   (her tekrar yine 500, sağlama oyu boşa). Düzeltme: yapısal ret (`accepted: false`).
4. **Deploy ban kilidi kördü (MED).** `server_deploy.sh` `HTTP 418|banned` arıyor; İLK ban
   sinyali tipik olarak 429/-1003'tür ve satırda "banned" yoktu; SÜREN ban boyunca tek satır
   15 dk sonra pencereden düşüp kilidi açıyordu. Düzeltme: hem trip hem periyodik kesici satırı
   artık `IP banned until <iso>` içerir.
5. **Ban/bütçe durumu görünmüyordu (MED).** `_scan_tick` turu keser ama tur "başarılı" sayılır →
   sağlık YEŞİL, `/scalper/status` sessiz. Düzeltme: `market_data_guard` alanı (host/banned/
   blocked_until/ağırlık). `health_snapshot` BİLİNÇLİ değiştirilmedi — ban sırasında "unhealthy"
   watchdog restart'ını davet ederdi (2026-08-14 felaket yolu).
6. **Safety turunda log seli (MED).** Ayrı host banında imzalı yol sağlam olduğu için akış her
   2 sn'de `_update_trailing`'e ulaşıp sembol başına WARNING basardı (180 sn'de ~240 satır).
   Düzeltme: tur başına tek satır + turun kalanında trailing atlanır (TP/kapanış tespiti
   imzalı yoldan devam eder).
7. **Head-of-line blocking (MED).** Tek paylaşılan `_cache_lock`, yavaş bir host'ta (15 sn ×3)
   BAŞKA sembollerin çekimini de bloklardı → safety turu 30 sn tazelik limitini aşabilirdi.
   Düzeltme: anahtar başına kilit.
8. **Kalıcı 4xx 3 kez deneniyordu (MED).** `-1121 Invalid symbol` kendiliğinden düzelmez;
   sembol başına ~3 sn + 2 gereksiz istek. Düzeltme: `MarketDataRequestError` (tekrarsız,
   SEMBOL bazlı — host geneli `MarketDataUnavailable` DEĞİL, tur kesilmez).
9. **Pozitif host allowlist'i yoktu (MED).** İmzasız yolda `https://fapi.binance.com.evil.tld`
   sessizce KABUL ediliyordu (kimlik doğrulama hatası üretmez). Düzeltme: `MARKET_DATA_ALLOWED_HOSTS`
   TAM netloc eşleşmesi.
10. **RUNBOOK yedek damgası ve geri alma (MED).** `date +%Y%m%d` aynı gün ikinci koşuda temiz
    yedeği eziyordu ve ertesi gün "bugünün yedeği" hiç yoktu → acil geri alma komutu tam o anda
    `cp: No such file` ile ölürdü. Düzeltme: `date -u +%Y%m%d-%H%M%S` + geri alma artık yedek
    dosyasına DEĞİL, `sed` ile satırı boşaltmaya dayanıyor.
11. **`ring_env_diff.sh` yarım kalkandı (MED).** `BINANCE_` prefiksi kapsam dışıydı; "hangi halka
    nereye işlem yapıyor" görünmediği için MAINNET_PLAN'ın çapraz kontrolü yapılamıyordu.
    Düzeltme: prefiks eklendi (secret'lar zaten maskeli).
12. **"Ölçülen" iddiası (MED, dürüstlük).** 64-114 ağırlık/dk bir ÖLÇÜM değil HESAPTIR
    (telemetri bu commit'te yeni eklendi). Metinler "HESAPLANAN" olarak düzeltildi ve terfi
    yoluna kalibrasyon adımı eklendi.
13. **Test/altyapı (LOW).** Küresel `asyncio.sleep` yaması yerine `data._sleep` dolaylaması;
    `tests/conftest.py` ile guard durumu her testte sıfırlanır (sızan `asyncio.Lock` farklı
    event loop'ta `RuntimeError` üretebilirdi); vacuous "secret" iddiası gerçek anahtar
    kontrolüyle değiştirildi.

**Bilinçli sınırlar:** (1) `UniverseScanner` (24s ticker) İŞLEM host'unda kaldı — evrenin
işlem yapılamayan sembollerle dolması kabul edilemez; guard'a da bağlanmadı (saatte 1 istek).
(2) Harness'a `--market-data-url` bayrağı EKLENMEDİ; zaten mainnet varsayılanıyla çalışıyor —
parite testi iki tarafın AYNI host'a getirilebildiğini kilitler
(`tests/test_market_data_source.py::TestHarnessParity`). (3) `BINANCE_BIND_IP` public
istemciye uygulanmadı (ayrı bir karar; bugünkü canlı davranış değişmesin).

**Kanıt:** `tests/test_market_data_source.py` — 65 test: ayar/doğrulama (https zorunlu,
sondaki `/` ve yol reddi, boşluk = boş, mainnet'te testnet URL'i REDDEDİLİR), motor
kablolaması (boş ayar → bugünkü yol; dolu ayar → fetcher; `client`/`scanner` işlem host'unda
kalır; `ExitManager` aynı fetcher'ı paylaşır), harness paritesi, teşhis (status alanları +
tek satır başlangıç logu), ağırlık/oran (her iki host için guard çağrılır, host başına
aralık — ayrı host'lar aralığı PAYLAŞMAZ, bütçe dolunca beklemeden hata + pencere sonrası
toparlanma + isteğin ağa hiç çıkmaması, ağırlık tablosu, başlık telemetrisi) ve ban semantiği
(418 tekrarsız kesici, `HTTP 418` log kalıbı, ikinci istek ağa çıkmaz, imzalı ban public'i
durdurur, public ban imzalıyı durdurmaz, ayrı host izolasyonu, 429 yumuşak ban,
`banned until` ayrıştırma, 429/-1003'ün deploy kalıbına uyması, süren banın periyodik satırı,
5xx'te 3 denemenin korunması, kalıcı 4xx'in TEKRARSIZ ve SEMBOL bazlı olması, `_scan_tick`'in
turu tek WARNING ile kesmesi, TTL önbelleğinin korunması, anahtar başına kilit) + inceleme
düzeltmeleri (trailing fiyat-uzayı ötelemesi ve aynı-host no-op'u, batch/live guard modları,
`/tv-signal` yapısal reti, exits tur-başına tek uyarısı, host allowlist'i, guard durumunun
status'ta görünmesi).
`python3 -m pytest tests -q` → **741 passed, 1 skipped** (önceki taban: 676 passed, 1 skipped).
Backtest ÇALIŞTIRILMADI ve P2 kuralı bu adaya doğrudan UYGULANAMAZ: harness'ın "testnet
mumu" modu yoktur, yani "kapalı vs açık" farkı simüle edilemez — bu bir strateji parametresi
değil, veri KAYNAĞI değişikliğidir. Terfi yolu: (a) tek sembolde mum sapmasını yeniden ölç,
(b) `SCALPER_SHADOW_MODE` ile ya da mevcut D6+D16 soak'ı BİTTİKTEN sonra testnet'te ≥5 gün
(değişiklikler üst üste bindirilmez — soak kirlenir), (c) bir tarama turu boyunca
`X-MBX-USED-WEIGHT-1M`'i GERÇEKTEN oku (telemetri artık var) ve 600'lük bütçeyi ölçüme göre
kalibre et, (d) insan onayı.

**Geri alma:** `.env`'den `SCALPER_MARKET_DATA_BASE_URL` satırını sil (veya boşalt) + restart —
kod geri alınmasına gerek yok, davranış bugünküyle birebir aynıya döner. Tam geri alma
gerekirse bu commit'teki `src/core/config.py` (alan + `market_data_base_url`/`kline_source`/
`market_data_is_testnet` property'leri + biçim validatörü + mainnet testnet-URL reddi),
`src/strategies/scalper/data.py` (`MarketDataGuard` + `MarketDataUnavailable`/
`MarketDataBanError`/`MarketDataBudgetError`/`MarketDataRequestError` + `klines_weight`/
`host_of` + guard modları + anahtar başına kilit + `_fetch` kablolaması),
`src/strategies/scalper/engine.py` (fetcher kablolaması +
`_kline_source_snapshot`/`_log_kline_source` + `snapshot()` alanları + `_scan_tick`'teki
`MarketDataUnavailable` dalı + `external_signal` yapısal reti),
`src/strategies/scalper/exits.py` (`_to_trading_price_space`/`_market_data_is_separate` +
tur-başına kesinti bayrağı), `src/strategies/scalper/backtest.py` (`guard_mode="batch"`),
`scripts/ring_env_diff.sh` (`BINANCE_` prefiksi),
`src/main.py` (`_EMPTY_SCALPER_STATUS`) değişikliklerini revert et. Yalnız ağırlık/ban
guard'ını geri almak (ayarı korumak) da mümkündür — `data.py`'deki `MarketDataGuard.acquire`/
`note_response`/`_raise_if_banned` çağrılarını `_fetch`'ten çıkarmak yeterlidir; üçü de
bağımsızdır.

## Reddedilen kararlar (kanıtla)

| Fikir | Tarih | Sonuç | Neden reddedildi |
|---|---|---|---|
| Kaldıraç tavanı 50x | 08-19 | +9.4k → −18k | fixed_roi'de kaldıraç ↑ = stop mesafesi ↓ → gürültü stop'u |
| TP1 %10→%15 | 08-21 | −3974, WR −10pp | SL'lerin 38/39'u %10'u görmeden ölüyor; BE@%10 dokunulmaz |
| Stop ROI 50→30 | 08-21 | AYI −6108 (baz −2042), SL 120→224 | dar stop trail'e ulaşacak işlemleri kesiyor |
| Rejim TF 15m→4h | 08-21 | BOĞA +2798→−63, YATAY −9090 | yavaş rejim boğayı yok ediyor |
| RANGE'de C kapalı | 08-21 | YATAY −7170 | yatay pencere RANGE'den ibaret değil |
| Flow-confirm filtresi | 08-21 | AYI 1.19 ✓, YATAY 0.81 ✗ | tek pencerede iyi |
| Divergence + flow_confirm birlikte (E2ab) | 08-21 | AYI 3.35/+1498 ama 31 işlem; BOĞA 0.85/−243; YATAY 0.78/−692 | aşırı filtreleme — tek başına divergence (D6) doğru kalibrasyon |
| Reversal-zone filtresi | 08-21 | AYI 0.68 | — |
| RSI 25/75 (sıkı eşik) | 08-21 | nötr | — |
| Strateji D, C+D | 08-21 | −660 / −4353 | C'yi zehirliyor |
| Danny ETH-15m LUCID reçetesi | 08-21 | ort PF 1.12 (yalın S&O 1.60) | çoklu onay + sabit TP/trailing SL kriptoda zarar |
| LuxAlgo AI / Discord LUCID script'leri (13 adet) | 08-21 | en iyi D6-BNB 2.26, S3-ETH 1.73; hepsi yalın OSC/S&O (2.2-2.5) altında | TV kaynakları değişmedi |
| RSI 35/65 (gevşek eşik, autoresearch E4a) | 08-21 | AYI 0.79/−5960 | gevşetme ayıda felaket — aktivite ≠ kâr (ikinci kez) |
| Chandelier 4.0 (E4c) | 08-21 | AYI 1.04/+640, BOĞA 2.33/+4317 | ayıda kötüleşiyor, boğada iyileşiyor → rejim-bağımlı, red |
| TP2 %20 / %30 (E4d/E4e) | 08-21 | ±40 | etkisiz — işlemlerin azı TP2'yi görüyor (ikinci kez) |
| Bağlam TF 15m (E4k) | 08-21 | −1575 | — |
| TP1 8 + lev tavanı 12 / 15 (E5a/E5b) | 08-21 | −1897 / −1643 | ayı iyi, boğa >%20 kayıp — kaldıraç kısıtı boğayı öldürüyor |
| Lev tavanı 12 tek (E5c) | 08-21 | −347 | aynı |
| TP1 8 + chandelier 3.0 (E5d) | 08-21 | +985 (< E4f tek +1058) | toplamsal değil; E4f tek başına tercih |
| AlgoPro V1.6 + yüksek kaldıraç TP1 | 08-21 | TP1 kazanma ort %40.5 (başabaş %50) | repaint yok ama beklenti −0.19R |

## Metodoloji kararları

### P1 — Harness = canlı motor (parite) · 2026-08-21
Harness rejim kapısını uygulamıyordu; tüm eski sayılar geçersiz sayıldı. `simulate_symbol`
artık `cfg.scalper_regime_filter` ile canlıyla aynı kuralı uygular; testler
`tests/test_scalper_backtest.py`. Motorda kapı/filtre değişirse harness da değişir.
Parite listesi: rejim kapısı (`simulate_symbol`) + **kapasite kapısı**
(`scalper_max_positions`, `run_backtest._apply_capacity_gate` — semboller
bağımsız simüle edildiği için post-hoc kronolojik geçiş, sembol-içi değil;
kanıt: E4h/E5 varyantlarının AYNI sonucu vermesi — `docs/EXPERIMENTS.md`
"Autoresearch" bölümü, `SCALPER_MAX_POSITIONS=3` sunucunun 5'ine karşı hiç
fark yaratmamıştı). Bilinen sapmalar `_apply_capacity_gate` docstring'inde.

### P2 — Karar kuralı · 2026-08-21
Aday = AYI PF ≥ 1.1 (veya AYI ve YATAY PnL birlikte iyileşir) VE BOĞA PnL kaybı ≤ %20.
Tek pencerede parlayan reddedilir. **Ölçek notu (2026-08-23):** boyutlama gibi doğrusal değişikliklerde (marj/risk yüzdesi) PnL kuralı mekanik olarak RED verir; bu adaylar PF/DD oranıyla okunur (E6b negatif kontrol). Terfi: backtest → testnet ≥5 gün (en az 1 düşüş günü) → mainnet.

### P3 — Simülatör ölçeği · 2026-08-21
Boğa penceresinde canlı defterin şeklini birebir üretir (LONG baskın), ölçek ~3× (boyutlama).
Kararlar göreli farkla; canlı defter nihai hakem.
