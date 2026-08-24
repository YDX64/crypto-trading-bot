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
**Soak (PLANLANMIŞTI, GEÇERSİZ):** D6+D16 DEMET; başlangıç 2026-08-23 02:57 sunucu saati
(00:57 UTC) → `scripts/ledger_report.py --since "2026-08-23 02:57"`; değerlendirme ≥28 Ağu +
≥1 DOWN günü. ⚠️ Bu demet 03:10'da geri alma ile DAĞILDI — yürüyen tek soak **D6**'dır;
başka kararların metinlerinde "D6+D16 soak" görülürse eskimiştir.
**Geri alma:** `cp backups/env.bak-20260823-025623-riskpaketi .env && supervisorctl restart tradingbot_v2`.

### D18 — Piyasa yapısı (CHoCH/BOS) kapısı · 2026-08-23 · **ADAY, UYGULANMADI (kanıt REDDETTİ)**
**Ne:** `src/strategies/scalper/structure.py` — LuxAlgo Price Action Concepts'in yayınlanmış
BOS/CHoCH tanımına yakın, saf ve deterministik bir piyasa-yapısı durum makinesi; üzerine iki
entegrasyon: (a) giriş kapısı `SCALPER_STRUCTURE_GATE` (+`_TF`/`_PIVOT`/`_USE_CLOSE`/
`_BLOCK_COUNTER`) — yapı BEAR iken LONG, BULL iken SHORT açılmaz; (b) çıkış tetikleyicisi
`SCALPER_STRUCTURE_EXIT=off|be|close` — açık pozisyonun tersine CHoCH gelince stop BE'ye
çekilir ya da reduce-only MARKET ile kapatılır. **Kod repoya girdi ama HER ŞEY VARSAYILAN
KAPALI**: `.env`'de hiçbir `SCALPER_STRUCTURE_*` anahtarı yok, davranış bugünküyle birebir
aynı (altın backtest `tests/test_golden_backtest.py` DEĞİŞMEDEN geçiyor).

**Neden denendi:** kullanıcı kararı (2026-08-23) — "sistem dönüşleri tespit edemiyor";
rejim kapısı (D5) 15m EMA50/200 ile dönüşleri saatler geç görüyor, dönüş günlerinde
düşen-bıçak LONG / rahatlama-rallisi SHORT kayıpları oradan geliyor. Çözüm ayar değil
SİNYAL olmalı (D16 geri alma gerekçesi).

**Kanıt (E9, `docs/EXPERIMENTS.md`; loglar `logs/structure/*.log`, 24 koşu):**
7 varyantın 7'si de P2'yi REDDETTİ. Taban (S0) E8.6/D12 ile birebir (AYI 213/+584.4/PF 1.04/
DD 3683). En iyi varyant S2p8 (15m, pivot 8): AYI PF **1.00** (<1.1), YATAY +210 (taban
+2392, **−%91**), BOĞA +1536 (taban +3902, **−%61**). 5m/pivot5 (S1): AYI 0.85 / −1057.
Çıkış tetikleyicisi daha da kötü: `close` modunda SL sayısı AYI'da 29→1 düştü (kayıp
−14907 → −514) ama TRAIL kazananları 182→29 çöktü, WR %85 → **%34**.
**Mekanizma:** C ters-trend bir ortalamaya-dönüş stratejisidir; "yapıya ters işlem açma"
kuralı onun kâr kaynağını yasaklar. Kapı, kestiği her 1 birim kayba karşılık 1.2–3.7 birim
kâr kesiyor (E9.2); AYI'da 30 LONG engelledikten sonra LONG bacağı −956 → **−2050**'ye
KÖTÜLEŞTİ — yani düşen bıçağı değil, kârlı dip alımını kesiyor. Pivot 3/5/8 taraması
monoton: yapı yavaşladıkça sonuç tabana yakınsıyor, yani kapının ulaşabildiği en iyi hâl
"hiçbir şey yapmamak" (eşik uydurmasıyla kurtarılamaz).

**Gecikme bulgusu (kullanıcının hipotezinin ölçülmüş hâli):** CHoCH gerçekten çok erken —
15m'de rejim (EMA50/200) dönüşünden medyan **45 mum ≈ 11 saat** önce. Ama bu öngörü değil
FREKANS: aynı veride 15m yapı sembol-gün başına ≈2.4 (5m'de ≈6.8) CHoCH üretiyor. "Erken"
ile "doğru" aynı şey değil; kaybın kaynağı bu.

**Ne kaldı (neden geri alınmadı):** modül saf, testli (51 test) ve KAPALI; iki şeye yarıyor:
(1) `/scalper/status` → `structure` alanı canlı yapıyı sembol bazında yayınlıyor (kapı
kapalıyken de) — operatör/soak gözlemi için ücretsiz telemetri, ek REST çağrısı yok;
(2) gelecekteki bir sinyal kaynağı (ör. yapı + likidite süpürmesi birleşimi, ya da yalnız
TV/haber sinyallerine uygulanan dar bir kapı) için hazır, parite-güvenli bir zemin.
**Bu commit bir terfi ÖNERİSİ DEĞİLDİR** — kanıt kuralı P2'ye göre bu fikir REDDEDİLMİŞTİR.

**Geri alma:** `.env`'de hiçbir şey yok, dolayısıyla "kapatma" gerekmez. Kodu tamamen
geri almak gerekirse: `src/strategies/scalper/structure.py` (yeni dosya) +
`src/core/config.py` (`scalper_structure_*` alanları + `_validate_structure_gate`) +
`src/strategies/scalper/engine.py` (import, `_structure` sözlüğü, `_evaluate_symbol`
yapı bloğu, `_apply_structure_exits`, `_close_position_market`'ın `forced_exit_reason`
parametresi, snapshot `structure` alanı) + `src/strategies/scalper/exits.py`
(`force_stop_to`) + `src/strategies/scalper/backtest.py` (import, `_StructureFeed`,
`OpenPosition.signal_close_time`/`structure_be_applied`, `simulate_symbol` yapı kapısı,
`manage_position` `structure_feed` parametresi, `_process_candle_exits` STRUCT_BE etiketi)
+ `src/main.py` (`_EMPTY_SCALPER_STATUS["structure"]`) + `tests/test_structure.py`.
### D15 — Lider piyasa kapısı ("ters-gün kapısı") · 2026-08-23 · **AKTİF (testnet, 11:14 UTC)** — kod varsayılanı KAPALI, sunucu `.env` ile açık
**Uygulama:** kullanıcının tam yetkisiyle (2026-08-23, "kendin onayladıktan sonra yayına al"): merge 6f54c3a (876 test, CI yeşil) →
`scripts/deploy.sh awa` (pid 2473476, sağlık 75 sn) → `.env`: `SCALPER_MARKET_GATE=true`, `_SYMBOL=BTCUSDT`, `_DAY_PCT=1.3`,
`_RUN_PCT=0` (yedek `backups/env.bak-20260823-1311*-marketgate`) → restart pid 2491624, sağlık 65 sn → `/scalper/status.market_gate`:
`gate_effective=true, stale=false, leader_ok=true, day_open_source=intraday_open, thresholds={1.3,0,3}`; 3 açık pozisyon `recover()` ile
devralındı. Lider verisi şimdilik testnet host'undan (`leader_source_host=testnet.binancefuture.com`) — D17 açılınca mainnet.
Geri alma: RUNBOOK "Lider piyasa kapısı" kapatma komutu.

**Ne:** `SCALPER_MARKET_GATE` (varsayılan `false`) ile iki BAĞIMSIZ alt-kapı
(`src/strategies/scalper/market_gate.py`, saf fonksiyon, IO yok):
1. **gün-içi** (`SCALPER_MARKET_GATE_DAY_PCT`, varsayılan **1.3**): lider sembolün
   (`SCALPER_MARKET_GATE_SYMBOL`, varsayılan BTCUSDT) son kapanışı gün açılışının ≥%X
   ALTINDAYSA yeni LONG, ≥%X ÜSTÜNDEYSE yeni SHORT açılmaz.
2. **uzama** (`SCALPER_MARKET_GATE_RUN_PCT`, varsayılan **0 = KAPALI** / `_RUN_DAYS`=3): lider son
   N TAMAMLANMIŞ günde ≥+%Y koştuysa LONG, ≤−%Y düştüyse SHORT açılmaz
   (koşu = `kapanış[-1]/kapanış[-1-N] − 1`).
Her alt-kapı kendi yüzdesi 0 yapılarak ayrı ayrı kapatılır. Rejim kapısından (D5) FARKLIDIR:
D5 sembolün KENDİ EMA50/200 trendine bakar, bu kapı yalnız LİDERE bakıp kararı tüm evrene uygular.

**Nerede:** `engine._market_gate_reason`, rejim kapısının HEMEN yanında, `_evaluate_symbol`
içinde — yani C taraması VE TV `external_signal` AYNI tek giriş noktasından geçer (D5'teki gibi
ayrı bir TV muafiyet bayrağı YOKTUR; TV zaten aynı fonksiyondan geçiyor). Harness tarafı
`backtest.simulate_symbol` + `LeaderSeries` — İKİ TARAF AYNI FONKSİYON NESNESİNİ çağırır (P1).

**Girdi türetme paritesi:** "gün açılışı" iki tarafta da `market_gate.resolve_day_open` ile
bulunur — önce GERÇEK açılış (o günün 00:00 UTC `15m` mumunun `open`'ı), o elde edilemezse
(günün ilk 15 dakikası) son tamamlanmış günlük kapanış vekili. Ayrıntı ve ölçüm aşağıda
"**'Gün açılışı' türetmesi**" bölümünde. (Bu paragraf ilk sürümde "gerçek open canlıda
TÜRETİLEMEZ" diyordu — o gerekçe E8 tarafından çürütüldü ve düzeltildi.)

**Tazelik paritesi (2026-08-23 inceleme bulgusu):** harness kararı verirken liderin TAM O ANKİ
mumunu kullanır; canlı motor ise lider anlık görüntüsünü önbelleğe alır. İlk sürümde görüntü
60 sn'ye kadar BAYAT olabiliyordu (harness'ta böyle bir gecikme yok). Düzeltme: görüntü TARAMA
TURU BAŞINDA bir kez zorla tazelenir (`engine._refresh_leader_snapshot`, `_scan_tick`'in ilk
adımı) ve tur içindeki TÜM semboller AYNI görüntüyü kullanır — sapma artık TTL'e değil TUR
SÜRESİNE bağlı. Tur dışında gelebilen TV `external_signal` yolu için azami yaş
`min(TTL, SCALPER_SCAN_INTERVAL_SECONDS)` ile sınırlanır, yani TV hiçbir zaman bir tarama
turundan daha bayat bir liderle karar vermez. REST maliyetinin ÜST sınırı değişmez (tur başına
yine en çok 3 istek) ama ALT sınırı **0'dan ~3 ağırlık/dakikaya ÇIKAR**: kapı eskiden yalnız bir
sinyal geldiğinde veri çekiyordu, sinyalsiz turda hiç istek gitmiyordu; artık kapı AÇIKSA her tur
tazelenir ("maliyet değişmez" ifadesi bu yüzden yanlıştı — 2026-08-23 inceleme bulgusu).
Ayrıca önbellek anahtarına **UTC gün damgası** eklendi: gün sınırında
(00:00 UTC) "gün açılışı" değişir, TTL'i sınıra taşan bir görüntü DÜNÜN açılışıyla karar verirdi
(gün başına ~1 dakikalık pencere).

**REST ağırlığı:** lider BAŞINA ~60 sn TTL önbellek (sembol başına DEĞİL) — tarama turu başına
en çok **3 istek**: `1d` (limit `RUN_DAYS+5`, tavan 100 — asgari N+2'nin üstüne PAY; bkz.
`_LEADER_DAILY_LIMIT_MARGIN`), giriş TF (limit 3) ve `15m` (limit 100, gerçek gün açılışı için —
aşağıya bakın); ÜÇÜ DE limit ≤ 100 olduğu için ağırlık 1, toplam ~3 ağırlık/dakika
(bütçe 2400/dk). Kapı kapalıyken TEK istek bile gitmez (`test_gate_off_makes_no_request_at_all`);
yani kapıyı açmanın REST maliyeti "sıfır" değil, **alt sınırı 0 → ~3 ağırlık/dakika**dır.

**Fail-open GÖRÜNÜR olmalı (2026-08-23 inceleme bulgusu — high).** Lider verisi alınamazsa kapı
UYGULANMAZ (fail-open, spec §C: lider verisi eksikliği bir risk OLAYI değildir). Sorun semantikte
değil görünürlükteydi: ilk sürümde yanlış yazılmış bir lider sembolü
(`SCALPER_MARKET_GATE_SYMBOL=BTCUSD`) ya da kalıcı bir ağ arızası kapıyı **sessizce** devre dışı
bırakıyordu — operatör `/scalper/status` → `enabled: true` görüp korunduğunu sanıyordu — ve
üstelik başarısızlık ÖNBELLEĞE ALINMADIĞI için her sinyal denemesi 3 seri × `KlineFetcher`'ın 3
iç denemesi kadar boşa REST isteği açıyor, `KlineFetcher`'ın **paylaşılan** önbellek kilidini
saniyelerce tutuyordu (o sırada tüm sembollerin mum çekimi bekliyor). Dört düzeltme:
1. **Başlangıçta lider doğrulaması** (`engine._validate_market_gate_leader`, `start()` içinde
   `_probe_exchange()` başarılıysa): `get_symbol_filters` ile exchangeInfo'da aranır; yoksa
   **ERROR** (`⛔ PİYASA KAPISI DOĞRULANAMADI (degraded)`) ve `leader_ok=false`; mesaj KALICI
   (yanlış sembol) ile GEÇİCİ (ağ/timeout/418) hatayı ayırır — ikincisinde restart TAVSİYE
   EDİLMEZ (CLAUDE.md yasak #3). Kapı yine de girişleri
   engellemez — fail-open korunur. Çağrı `asyncio.wait_for` ile 15 sn'ye bağlıdır: istemci 3
   deneme × 60 sn timeout yaptığı için sınırsız bırakılsaydı ulaşılamayan bir borsada motor
   AÇILIŞINI dakikalarca bloke ederdi. Probe başarısızsa `leader_ok` `null` kalır
   ("henüz denenmedi", `gate_effective` false) ve ilk tarama turu kendiliğinden çözer.
2. **Negatif önbellek** `SCALPER_MARKET_GATE_RETRY_SEC` (vars. 60 sn): başarısızlıktan sonra bu
   süre boyunca YENİDEN DENENMEZ. Ölçüldü (test): 20 sinyal denemesi → 60 istek yerine **1**.
3. **Oran-sınırlı WARNING** (tür başına dakikada en çok 1). Tür başına, çünkü tek küresel sayaçla
   önce basılan önemsiz bir tavsiye ("uzama serisi kısa") hemen ardından gelen ÖNEMLİ fail-open
   uyarısını susturuyordu.
4. **`/scalper/status.market_gate` teşhis alanları:** `gate_effective` — **operatörün ve
   dashboard'un bakması gereken alan budur**, `enabled` değil. BEŞ şart birden: (1) `enabled`,
   (2) lider doğrulandı (`leader_ok is True`), (3) en az BİR başarılı anlık görüntü alındı
   (`last_ok_at is not None`), (4) görüntü BAYAT değil (yaş ≤ 2 × tarama aralığı ve UTC günü
   dönmemiş), (5) en az bir alt-kapı eşiği > 0. (2) ve (5) inceleme sırasında eklendi: yalnız
   `enabled AND leader_ok` bakmak, tek bir mum bile çekilmeden (sadece exchangeInfo
   doğrulamasıyla) ve `DAY_PCT=0 RUN_PCT=0` iken bile `true` veriyordu — ikisi de RUNBOOK'un
   ZORUNLU doğrulamasını yanlış-yeşil yapardı. Yanında: `leader_ok`, `last_ok_at`, `last_error`,
   `last_failure_at`, `consecutive_failures`, `failures_total` (toparlanmada SIFIRLANMAZ —
   dönüşümlü arıza görünür kalsın), `leader_source_host`, `thresholds` (yürürlükteki eşikler),
   `stale` + `snapshot_age_sec`, `last_block_at`. Türetilmiş metrikler (`day_drift_pct`,
   `run_drift_pct`, `day_open_source`) BAYAT görüntüde `null` verilir. `run_drift_pct` adı
   bilerek eşikten (`thresholds.run_pct`) FARKLIDIR: ikisine de `run_pct` demek "uzama kapısı
   %4.2'de açık kalmış" gibi gerçekçi bir yanlış-teşhis üretiyordu.
   `src/main.py::_EMPTY_SCALPER_STATUS` aynı şekli motorsuz yolda da verir ve bir sözleşme
   testi ikisinin AYRIŞMASINI engeller (bu ayrışma fiilen olmuştu: `day_open_source` motora
   eklenmiş, boş sözlüğe eklenmemişti).

Ayrıca `last_reason` eskiden SERBEST geçişlerde de yazılıyordu; her serbest sinyal onu `null`'a
döndürdüğü için status pratikte HER ZAMAN `null` gösteriyordu ("kapı hiç tetiklenmedi"
yanılsaması). Artık yalnız ENGELLEMEDE yazılır ve yanına `last_block_at` (UTC) eklenir.

**Veri kaynağı paritesi (bilinen sapma).** Kapı lider serisini motorun KENDİ `KlineFetcher`'ından
alır (ayrı istemci YOK) — yani canlıda `settings.binance_base_url` = **TESTNET**, E7'de ise
harness **mainnet**'ten okur. İki taraf aynı kuralı aynı kodla uygular ama VERİ farklıdır;
testnet soak'u E7 sayılarıyla birebir kıyaslanamaz. Kapı tek noktadan beslendiği için
`SCALPER_MARKET_DATA_BASE_URL` (D17 — AYRI dal, bu dalda YOK) ile piyasa verisi mainnet'e
alındığında parite kendiliğinden sağlanır; kapı için ek bir iş gerekmez, çünkü lider serisi
motorun tek `KlineFetcher`'ından gelir (`test_leader_series_comes_from_the_engine_kline_fetcher`). Hangi hostla karar verildiği `/scalper/status` → `market_gate.leader_source_host`
alanındadır (yalnız ana bilgisayar adı — secret yok).

**Kanıt (E7, `docs/EXPERIMENTS.md` "2026-08-23 — Lider piyasa kapısı"):** 8 varyant × 3 pencere,
loglar `logs/market_gate/<varyant>_<pencere>.log`. V0 (kapı kapalı) mevcut tabanı BİREBİR üretti
(AYI 1.04/DD 3683 · YATAY 1.29/3229 · BOĞA 2.43/735).
- **V1 (gün-içi %1.0)** — AYI 1.04→**1.33** (+584→+2999, DD 3683→2956) · YATAY 1.29→**1.36**
  (+2392→+2593) · BOĞA 2.43→2.37 (+3902→+3725, −%4.5). P2'nin HER İKİ kolunu da geçer.
  Mekanizma doğrulandı: AYI'da SL 29→17, SL zararı −14907→−8738; LONG −956→−121,
  SHORT +1541→+3120; RANGE günleri −1029→+425. (Bunlar DEFTER farkları, atıf DEĞİL — aşağıdaki
  "AYRIŞTIRMA" bölümü: yalnız-engelleme bakiyesi AYI'da SHORT +1523 / LONG −225.)
- **V2 (uzama %15/3g)** — AYI 1.04→1.24 (+2909), YATAY/BOĞA hiç tetiklenmiyor. P2'yi geçer AMA
  60 tetiğin TAMAMI TEK bir lider olayından (2026-02-06, 3 günlük −%20.1). %15 ve %20 eşikleri
  BİREBİR aynı sonucu veriyor (arada olay yok) → n=1, kanıt ZAYIF.
- **V3 (ikisi) ≡ V1 birebir** (V3'te `market_gate_run` üç pencerede de 0) → alt-kapılar toplamsal
  değil; uzamayı gün-içiyle birlikte açmanın ölçülebilir faydası YOK.
- **V2a (uzama %10/3g) P2'den KALDI**: BOĞA −%24.6 (08-20'de BTC'nin +%10.2 3-günlük koşusu
  LONG'ları vetoluyor, LONG 60→46) — uzama eşiği gevşetilmemeli.
- **V1a (%0.7) reddedildi**: AYI en iyi (1.52) ama YATAY +2392→+873 (−%63.5) — tek pencerede parlıyor.
- **V1b (%1.5)** muhafazakâr alternatif: AYI 1.17, YATAY +570, BOĞA hiç tetiklenmiyor (%0 kayıp).
- **V1c (gün-içi %1.3) — TERCİH EDİLEN EŞİK** (E8 ajanının bağımsız post-hoc taramasının önerisi,
  motor-içi kapıyla doğrulandı): AYI 1.04→**1.43** (+584→+3812, DD 3683→2956) · YATAY 1.29→**1.38**
  (+2392→+2791) · BOĞA 2.43→2.39 (−%2.7). Üç pencerede de V1'i (%1.0) DOMİNE ediyor, maxDD hiçbir
  yerde kötüleşmiyor. AYI yön kırılımı: LONG 79/−956 → 64/**+180**, SHORT 134/+1541 → 94/**+3632**,
  SL 29→17.

**AYRIŞTIRMA — kazanç NEREDEN geliyor? (2026-08-23 inceleme, dürüstlük notu).** E7'nin ΔPnL'i
İKİ ayrı etkinin toplamıdır ve karıştırılırsa kapıya hak etmediği bir güç atfedilir:
(a) **engelleme** — vetolanan işlemlerin GERÇEKLEŞMEMESİ; (b) **yeniden tahsis** — vetolanan
sinyalin boşalttığı slota giren YENİ işlemler. İki koşunun işlem listeleri
`(symbol, entry_time, direction)` ile eşleştirilerek ayrıldı; **yeni backtest YOK**. Araç:
`scripts/decompose_gate_runs.py` (rapor yollarını log dosyalarından türetir; aritmetiği
`tests/test_market_gate.py::TestDecomposeGateRuns` ile çivilenmiştir). Ortak işlemlerin PnL'i iki
koşuda birebir aynı → atıf temiz (V1 ve V1c için ayrı ayrı 0 uyuşmazlık).

⚠️ **Sonuç EŞİĞE DUYARLI ve benimsenen eşikte TERSİNE DÖNÜYOR.** İlk ayrıştırma yalnız V1 (%1.0)
üzerinde yapılmıştı; varsayılan olarak benimsenen eşik ise **V1c (%1.3)**:

| Ölçü (V0 →) | V1 (%1.0) | **V1c (%1.3) — VARSAYILAN** |
|---|---|---|
| Yalnız-engelleme bakiyesi (3 pencere) | +224.82 | **+2063.05** |
| — AYI / YATAY / BOĞA | +1297.71 / **−831.56** / −241.34 | +2010.41 / **+156.69** / −104.05 |
| Yeniden tahsis payı | %90.8 | **%41.4** |
| AYI yalnız-engelleme PF | 1.039 → 1.210 | 1.039 → **1.289** |
| AYI LONG / SHORT bacağı (engelleme) | −224.87 / +1522.59 | **+76.12 / +1934.29** |

- **%1.0'da** kazancın ~%91'i yeniden tahsisten gelir, YATAY ve BOĞA'da engellemenin KENDİSİ net
  negatiftir. **%1.3'te** kazancın **%59'u doğrudan engellemedendir** ve YATAY'da bile engelleme
  net POZİTİFTİR. Daha sıkı eşik daha AZ ama daha İSABETLİ engelliyor (AYI 74 → 66 engelleme,
  bakiye +1298 → +2010). "Kapı ağırlıklı olarak bir slot yeniden tahsis mekanizmasıdır" hükmü
  **yalnız %1.0 için** doğrudur — varsayılan eşikte DEĞİL.
- **İki eşikte de değişmeyen tek nitel bulgu:** LONG bacağının engellemesi ≈başabaş, koruma
  **SHORT bacağında**. Bu yüzden ilk sürümdeki "düşen-bıçak LONG'ları kesiliyor" cümlesi
  kaldırıldı (defter farkını engellemeye atfediyordu; LONG düzelmesinin çoğu YENİ işlemlerden).
  Bacak başına atıf (V1c, AYI): SHORT defter iyileşmesinin (+2091.75) **%92'si engelleme**
  (+1934.29), LONG'unkinin (+1136.11) **%93'ü ikinci-derece** (+1059.99). İki bacak İKİ FARKLI
  mekanizmadır; "kapı LONG'ları koruyor" demek ölçüme aykırıdır.
- **İkinci-derece terimin MEKANİZMASI: sembol-içi işgal penceresi (%100), kapasite 0,
  cooldown 0.** İlk sürüm bunu küresel kapasiteye (`scalper_max_positions`) atfediyordu;
  `scripts/decompose_gate_runs.py --mechanism` bunu çürüttü: V1c'de yeni işlemlerin **14/14'ü**
  (AYI 11/+1217.45, YATAY 3/+242.39) kapının engellediği işlemin AYNI SEMBOLDEKİ
  `[giriş, çıkış]` penceresinin İÇİNDE açılıyor — `simulate_symbol` bir sembolde tek pozisyon
  tutar (`i = trade.exit_idx + 1`), yani bunlar taban koşuda kapasiteye SIRA GELMEDEN
  imkânsızdı. Kapasite fiilen bağlayıcı değil (`capacity` sayacı V0'da 3, V1c'de 2). E8.6'nın
  bağımsız ölçümüyle aynı sonuç. Sınıflandırıcının aritmetiği
  `tests/test_market_gate.py::TestDecomposeGateRuns` ile çivilenmiştir.
- **AYI maxDD iyileşmesinin tamamı engellemeden** — ölçüldü (yalnız-engelleme kümesi 3682.60 →
  2956.08 = tam koşunun DD'si). Teorem DEĞİL: V1 YATAY'da yeniden tahsis DD'yi ayrıca düşürüyor
  (3032.30 → 2882.04).
- **Engellenen 12 AYI SL'i = −6169.50**, defter farkıyla (29→17 SL) **tesadüfen** birebir aynıdır:
  kapılı koşuda yeni giren hiçbir işlem SL olmamıştır. Atıf hatası değildir.
- **P2 KRİTERLERİ yalnız-engelleme kümesinde de sağlanıyor** (AYI PF 1.210/1.289 ≥ 1.1; BOĞA
  −%6.19/−%2.67 ≤ %20). Bu yeni bir P2 **hükmü değil**, dayanıklılık kontrolüdür — P2 gerçek bir
  koşu üzerinde tanımlıdır, bu küme ise V0'dan işlem çıkarılarak kurulmuş sentetiktir (kapasite
  kapısı yeniden koşulmadı).
- **İkinci-derece terim bir ölçüm eseri DEĞİL:** sembol-içi işgal penceresi canlıda da gerçektir
  (motor da bir sembolde tek pozisyon tutar) — kazanca sayılır, yalnız ATFI doğru yapmak gerekir.
  Ama harness'ın en zayıf modellediği kanal tam da budur: 8 saatlik reaper canlıda pencereyi
  ERKEN kapatır, harness'ta hiç kapatmaz (aşağıdaki reaper maddesi).

**Çapraz kontrol (E8 sinyal otopsisi, aynı gün):** E8 kapıyı bağımsız olarak harness JSON'u
üzerinde POST-HOC ölçtü. Yöntem farkı önemli: E8'de engellenen sinyal kapasiteyi serbest
BIRAKMIYOR, bu yüzden sayıları motor-içi kapının ALT SINIRI (E8 bunu kendi de işaretledi).
YATAY %1.0'da işaret bile farklı (E7 +201 / E8 −487) — fark tam olarak kapasite yeniden
tahsisinden geliyor. İki ölçüm ÇELİŞMİYOR; E8 muhafazakâr taraftan bakıyor ve eşik önerisi (%1.3)
motor-içi kapıyla doğrulandı → benimsendi. Bacak-ayrık eşik (SHORT %1.0 / LONG %1.3) E8'in
önerisiydi; UYGULANMADI (ayrı tasarım kararı, kendi spec'i + onayı gerekir) ama artık İKİ ölçüm
tarafından da destekleniyor — **ilk itirazım YANLIŞTI ve geri alındı**: "LONG bacağı da iyileşiyor
(−956→+180)" demiştim; E8'in önerdiği atıf testini iki JSON raporu üzerinde koşunca bu
iyileşmenin **~%93'ünün kapasite etkisi** olduğu çıktı (kapının engellediği 24 LONG toplam −76.1
= başabaş; +1060'ı boşalan slota giren 9 YENİ işlemden). SHORT bacağında tam tersi: engellenen 42
işlem −1934.3 (gerçek kaybedenler), atıf ~%92 kapının kendisi. Karşı argüman kayda geçsin: canlı
defterin 22 Ağu kaybı tam olarak LONG bacağından geldi (8 işlem, +102.1 kurtarırdı) — bacakları
ayrı kapatılabilir tutan bugünkü tasarım bu yüzden doğru.

**Neden UYGULANMADI:** (1) CLAUDE.md kural 1 zinciri backtest → testnet ≥5 gün → onay ister ve
D6'nın soak'u sürüyor — üst üste binen değişiklik atfı kirletir. (2) Kanıt tek lider (BTCUSDT) ve
tek 21 günlük ayı penceresinden geliyor; AYI kazancının büyük kısmı 02-05/02-06 çöküş-toparlanma
çiftinden. (3) Uzama alt-kapısı için kanıt açıkça yetersiz (n=1 olay) ve E8 tarafından
BAĞIMSIZ olarak ÇÜRÜTÜLDÜ (aşağı). Açılacaksa YALNIZ gün-içi alt-kapısı
(`SCALPER_MARKET_GATE=true`, `DAY_PCT=1.3`, `RUN_PCT=0`) ve tercihen önce gölge modunda (D14).

**Uzama alt-kapısı — İKİ BAĞIMSIZ RED, kullanılmamalı.** (1) E7: yalnız AYI penceresinde ve TEK
lider olayında (2026-02-06) tetikleniyor; %15 ile %20 birebir aynı sonucu veriyor; gün-içi
alt-kapısının üstüne HİÇBİR katkısı yok (V3 ≡ V1). (2) E8 canlı defterde net **NEGATİF** ölçtü
(−152.7; LONG eşiği %12'de −382.9, 50 kazanan engelleniyor) ve spec'in hipotezinin İŞARETİNİ ters
buldu: kazananların `align_btc_run_3d` ortalaması 7.50, kaybedenlerin 2.28 (AUC 0.292, p<0.001) —
yani lider koşusuyla AYNI yönde açılan işlemler KAZANIYOR, kapının varsaydığının tersi.
Varsayılan bir süre `15` (spec §C'de onaylı) bırakıldı ve sessizce değiştirilmedi; bunun yerine
motor açılışta AÇIKÇA uyarıyordu (`ScalperEngine._maybe_log_market_gate_banner` — kapı açık +
`RUN_PCT>0` ise ikinci bir WARNING). **2026-08-23'te varsayılan 0'a çekildi** (aşağıdaki
"Varsayılanlar" maddesi); banner uyarısı KALDI, çünkü `.env`'de elle yazılmış bir `RUN_PCT>0`
varsayılanı hiç devreye sokmaz.
E8'in ek gerekçesi (bende olmayan bir ölçüm): harness'ın "üç pencerede inert" hükmü BUGÜNKÜ
piyasaya taşınmıyor. O pencerelerde BTC 3 günde %15 koşmadığı için kapı hiç tetiklenmiyordu;
botun ŞU AN soak ettiği dönemde koşuyor — 7–22 Ağu canlı defterinde `RUN_PCT=15` 202 işlemin
35'inde tetikleniyor ve **net −152.7** ediyor. Yani (o günkü) çıplak varsayılanlarla açılsaydı
uzama alt-kapısı inert DEĞİL, aktif ve negatif olurdu — ve `DAY_PCT` varsayılanı 1.0 iken tüm
kanıt 1.3 diyordu; yani çıplak varsayılanlar hiçbir ölçümün önermediği (1.0 + 15) çiftini
veriyordu. **Bu tuzak varsayılanlar 1.3 / 0'a çekilerek kapatıldı**; yine de `docs/RUNBOOK.md` "Lider piyasa kapısı" bölümündeki açma
komutu üç değişkeni de AÇIKÇA yazar ve restart'tan önce `assert` ile doğrular — log'daki WARNING
bir KONTROL DEĞİLDİR (D14 review bulgusu #4 emsali: sessizce başarısız olan `sed`).

**"Gün açılışı" türetmesi — E8'in yolu uygulandı, önceki sapma KAPATILDI.** İlk sürüm gün
açılışı olarak son tamamlanmış günlük KAPANIŞ'ı vekil kullanıyordu (gerekçe: `_drop_unclosed`
oluşmakta olan günlük mumu attığı için gerçek open canlıda görünmüyor). Vekilin hatası ölçüldü:
mainnet ort. %0.000082 / maks %0.0006 (ihmal edilebilir) ama TESTNET'te — canlı motorun kaynağı
(`data.py` → `settings.binance_base_url`) — ort. %0.013 / maks %0.152 = eşiğin %15'i, kuyruklu
dağılımla (medyan %0.000167, p95 %0.106). Bu bir süre "bilinen sapma" olarak kaydedildi.
E8 bedelsiz çözümü buldu: **`1d` mumunun `open`'ı, o günün 00:00 UTC `15m` mumunun `open`'ına
BİREBİR eşittir** (ikisi de aralığın ilk işlem fiyatı). Bağımsız doğrulandı — BTCUSDT, mainnet +
testnet, **76 gün sınırı, 0 uyuşmazlık, maks fark %0.00000000**. Uygulandı
(`market_gate.resolve_day_open` + `day_open_from_intraday`): motor lider için `15m` limit 100
çeker (25 saat, ağırlık 1), harness aynı seriyi `gather_symbol_data` ile AYNI önbellek
anahtarından okur (ek ağ isteği YOK). `_drop_unclosed`'a HİÇ dokunulmadı — o 15m mumu çoktan
kapanmıştır, repaint riski yok. Günün ilk 15 dakikasında (mum henüz kapanmamış, look-ahead yasak)
İKİ TARAF DA eski vekile düşer; hangisinin kullanıldığı `/scalper/status` →
`market_gate.day_open_source` alanında görünür.
**Regresyon:** V1 ve V1c üç pencerede yeniden koşuldu, sonuçlar önceki koşularla BİT DÜZEYİNDE
AYNI (V1c AYI 158/+3812.25/PF 1.43/DD 2956.08 · YATAY 137/+2791.37/1.38/2840.06 · BOĞA
89/+3797.60/2.39/734.59) — E7 tablosu her iki tanım altında geçerli, testnet belirsizliği kalktı.
Eski loglar `logs/market_gate_prevclose/`.

**Varsayılanlar — 1.3 / 0 (KARAR: ana oturum, 2026-08-23).** `SCALPER_MARKET_GATE_DAY_PCT`
1.0 → **1.3**, `SCALPER_MARKET_GATE_RUN_PCT` 15 → **0**. Gerekçe: **iki bağımsız ölçüm uyuşuyor**
— gün-içi eşiği için E7 (motor-içi kapı, 3 pencere: V1c üç pencerede de V1'i domine ediyor,
maxDD hiçbir yerde kötüleşmiyor) ve E8 (canlı defter post-hoc taraması aynı eşiği önerdi);
uzama alt-kapısı için E7 (n=1 olay, gün-içinin üstüne katkı YOK) ve E8 (canlı defterde −152.7,
hipotezin işareti ters). Önceki varsayılanlar spec §C'den geliyordu ve HİÇBİR ölçümün önermediği
(1.0 + 15) çiftini veriyordu — "çıplak" `SCALPER_MARKET_GATE=true` yazan operatörün eline geçen
şey buydu. Değişiklik **canlı davranışı DEĞİŞTİRMEZ**: `SCALPER_MARKET_GATE` varsayılanı `false`
ve `.env`'de tanımlı değil; değişen tek şey, kapı açıldığında hangi eşiklerin devreye gireceği.
Uzama alt-kapısının başlangıç WARNING'i KALDI (`RUN_PCT>0` bırakan operatör yine uyarılır) ve
RUNBOOK'un açma komutu üç değişkeni de açıkça yazmaya devam ediyor — varsayılana güvenmek bir
kontrol değildir. `env.example` uyumlu. Geri alma: `.env`'de eski değerleri açıkça yaz.

**Bilinen sapma — 8 saatlik reaper harness'ta MODELLENMİYOR (2026-08-23 inceleme).** Canlı motor
`SCALPER_MAX_HOLD_HOURS` (D4, sunucuda 8) dolan ve TP1 görmemiş pozisyonu reduce-only MARKET ile
kapatır (`engine._reap_aged_positions`); `backtest.simulate_symbol`'de böyle bir çıkış YOKTUR —
pozisyon SL/TP/trail'e kadar açık kalır. İki ters etki: (a) uzun sürünen kaybedenler harness'ta
tam SL'ye taşınır (kapının "kestiği zarar" olduğundan büyük görünür), (b) slot canlıda 8 saatte
boşalırken harness'ta daha uzun dolu kalır — ki bu tam olarak yukarıdaki **yeniden tahsis**
kanalını vurur.
**Büyüklük değil, MARUZ KALAN KÜME ölçüldü** (`scripts/decompose_gate_runs.py --reaper`): AYI
penceresinde reaper'ın gerçek popülasyonu (>8 sa **ve** TP1 görmemiş) V0'da 13 işlem / −6681.25,
V1 ve V1c'de 9 işlem / −4624.75. Kapının engellediği işlemlerin **4'ü** bu tanıma girer ve
**−2056.50** taşır → V1 AYI Δ'sının (+2415) **%85'i**, V1c'nin (+3228) **%64'ü** bu 4 işlemin
harness'ta SL'ye kadar taşınmasına dayanıyor. **Net işaret ÖLÇÜLMEDİ ve tek bir yüzdeyle
özetlenemez**; ama etkinin dokunduğu taban Δ'nın çoğunluğu olduğu için **E7'nin AYI sayıları
yukarı yanlı kabul edilmelidir.** (Bu not önce "~%17" diyordu — kaynağı ve yöntemi olmayan,
ham veriden yeniden üretilemeyen ve etkiyi KÜÇÜK gösteren bir sayıydı; CLAUDE.md yasak #6 gereği
çıkarıldı.) Kod tarafındaki karşılığı: `backtest.py` `_apply_capacity_gate` "BİLİNEN SAPMALAR"
madde 3. Reaper'ı harness'a eklemek AYRI bir iştir (kendi parite testiyle).

**Kanıt (kod):** `tests/test_market_gate.py` — 149 test (saf fonksiyon: her alt-kapı/yön/eşik
sınırı/eksik-geçersiz veri; gün açılışı türetmesi: gerçek 00:00 UTC açılışı + vekil yolu; motor:
önbellek, fail-open+WARNING, ret sayaçları, `/scalper/status` `market_gate` alanı, GERÇEK
`_evaluate_symbol` üzerinden C ve TV yolunun ikisi de; **görünürlük**: lider doğrulaması,
negatif önbellek (20 deneme → 1 istek), oran-sınırlı WARNING, `gate_effective`, status şekli
sözleşmesi (`main.py` ile ayrışmaya karşı); **tazelik**: tur başı tazeleme, tur içi paylaşım,
TV azami yaşı, UTC gün sınırı; harness: look-ahead yasağı, `missed_counter` anahtarları,
strateji zaman dilimi doğrulaması, rapor `market_gate` provenance'ı ve
`run_metadata` yayılımı (bu ikincisi UÇTAN UCA koşuda yakalanan gerçek bir hatanın regresyonu:
`run_metadata.update()` sığ kopya olduğu için sonradan eklenen `market_gate` anahtarı rapora
HİÇ ulaşmıyordu); PARİTE: iki modülün aynı
fonksiyon nesnesini aynı argümanlarla çağırdığı VE harness'ın KENDİ türetme fonksiyonuyla
(`gather_leader_series`) kurulan serinin canlı motorun ürettiği üçlüyle birebir aynı olduğu;
Settings env parse; ayrıca `scripts/decompose_gate_runs.py`'nin ARİTMETİĞİ ve ikinci-derece
MEKANİZMA sınıflandırıcısı — belgedeki atıf iddiasının kanıtı).
`python3 -m pytest tests -q` → **825 passed, 1 skipped** (önceki: 761 passed, 1 skipped).
`tests/test_golden_backtest.py` DEĞİŞMEDEN geçer; kapı kapalıyken harness çıktısı bit düzeyinde
aynıdır. V1c AYI penceresi kapı sertleştirmesinden SONRA yeniden koşuldu ve değişmedi
(aşağıdaki E7 kaydına bakın).

**Geri alma:** `.env`'den `SCALPER_MARKET_GATE`'i kaldır/`false` yap — varsayılan zaten `false`,
davranış değişmez, kod geri alınmasına gerek yok. Tek satırlık reçete ve ZORUNLU doğrulama
(`gate_effective`) `docs/RUNBOOK.md` "Lider piyasa kapısı" bölümünde. Tam geri alma gerekirse commit `ece8bd8`
(`market_gate.py`, `engine.py` kapı bloğu + `_market_gate_*` metodları + snapshot alanı,
`backtest.py` `LeaderSeries`/`gather_leader_series`/`_fetch_series_cached`/`simulate_symbol`
`leader` parametresi, `kline_cache.py` "1d" girdisi, `config.py` 5 alan, `main.py`
`_EMPTY_SCALPER_STATUS`) revert edilir.

### D20 — AlgoPro takipçi halkası (`BOT_MODE=follower`, ayrı testnet hesabı) · 2026-08-23 · AKTİF (kanıt: YOK — testnet ölçümü kanıt olacak)
**Ne:** İKİNCİ ve BAĞIMSIZ bir testnet sistemi. Aynı kod tabanı, `BOT_MODE=follower`
ile AYRI süreç (`/opt/tradingbot-ap`, supervisord `tradingbot_ap`, port 9093, ayrı
`.env`/DB/state/log/Telegram ve **ayrı Binance testnet hesabı** — anahtarları KULLANICI
girer). Bu modda scanner, strateji C ve TV sağlaması KAPALIDIR; giriş/çıkış **yalnız
AlgoPro V1.6** alarmlarından gelir:
- **Giriş:** `BUY`/`SELL` → MARKET (1m sinyalde maker beklemek sinyali kaçırır).
- **Çıkış:** `EXIT` ya da ters sinyal → kalan miktar reduce-only MARKET; `FOLLOWER_FLIP=true`
  (varsayılan) ise ters sinyalde kapat + yeni yöne gir (flip cooldown'u BİLİNÇLİ atlar,
  aksi halde özellik ölü olurdu).
- **Seviyeler:** AlgoPro'nun kendi mesajındaki `SL/TP1/TP2/TP3` — **birincil kaynak**.
  Yedek (mesajda seviye yoksa): `SL = giriş ∓ FOLLOWER_SL_ATR_MULT×ATR(14)` 1m'den,
  `TPk = giriş ± RRk × SL_mesafesi` (0.5/1.0/1.5); yedek yola düşmek WARNING loglar.
- **3 parça çıkış:** TP1/TP2/TP3 reduce-only `TAKE_PROFIT_MARKET`, 1/3'er (yuvarlama
  artığı SON parçada); TP1 doğrulanınca SL ücret-farkında break-even'e çekilir.
  Chandelier trailing YOKTUR — koşucuyu AlgoPro yönetir.
- **Evren:** 8 majör × **1 dakika**; sembol başına tek pozisyon, azami 4 eşzamanlı.

**Boyutlama (KULLANICI KARARI, gün içinde "risk %2"nin YERİNE geçti):** marj =
bakiyenin `%FOLLOWER_MARGIN_PCT`'i (vars. %10); kaldıraç volatiliteye göre
`lev = clamp(round(FOLLOWER_SL_ROI_TARGET / sl_pct), 3, 100)` (vars. hedef %30) →
stop DAİMA marjın ~%30'u. ZORUNLU güvenlik kapıları (yalnız DÜŞÜRÜR):
(a) borsa kaldıraç dilimi `/fapi/v1/leverageBracket` (gerçek değer okunur, 6 sa
önbellek; **okunamazsa giriş YOK** — fail-closed); (b) `lev × sl_pct ≤ 50` ve
`1/lev − mmr > 2 × sl_pct/100`; (c) nominal = marj × lev, qty borsa filtreleriyle
doğrulanır, 3 parçaya bölünemeyen pozisyon AÇILMAZ; (d) TP ROI'leri SL ROI'sinin RR
katıdır. Her işlemin `lev`, `sl_pct`, `sl_roi`, `margin` değerleri deftere
(`signal_reason`) ve `/follower/status`'a yazılır.
Doğrulanan örnekler (tests/test_follower_plan.py): SL %0.08 → 100x → SL = marjın %8'i,
TP1 %4 · SL %0.30 → 100x → %30 / %15 · SL %0.60 → 50x → %30 / %15.

**Sinyal yolu (TV alarm URL'leri DEĞİŞMEZ):** ana bot (`tradingbot_v2`, :9091) TEK TV
girişi olarak kalır; `resolve_tv_source` "algopro" derse gövde
`FOLLOWER_FORWARD_URL`'e İLETİLİR (fire-and-forget, 2 sn timeout, ayrı
`FOLLOWER_FORWARD_SECRET`, secret `X-Follower-Secret` BAŞLIĞINDA — URL'de değil).
Köprü `/tv-signal`'ın **422'sinden ÖNCE** çalışır (AlgoPro'nun EXIT/TP HIT/SL HIT
mesajları yön kelimesi taşımaz ve ana botta 422 alır) ama **403'te asla iletmez**
(`resolve_tv_signal` secret'ı sembol/yön çözümünden ÖNCE doğrular — kimliği
doğrulanmamış gövde takipçiye enjekte edilemez).

**AlgoPro mesaj gerçeği (TV Desktop sondasıyla ÖLÇÜLDÜ, 2026-08-23):** "Any alert()
function call" modunda mesajı script üretir ve seviyeleri İÇERİR:
`🔴 SELL | BINANCE:BTCUSDT | TF: 1 | Price: 77126.08 | TQI: .45 | Score: 8 | SL: 77167.77
| TP1: 77105.23 | TP2: 77084.39 | TP3: 77063.54 | TP: fixed ×1.00`; `🎯 TP1 HIT | … | Price: …`;
`🛑 SL HIT | … | Price: …`. LONG bacağı da AYNI kalıptadır (ölçüldü):
`🟢 BUY | BINANCE:BTCUSDT | TF: 1 | Price: 76556.52 | TQI: .54 | Score: 17 | SL: 76501.73
| TP1: 76583.92 | TP2: 76611.32 | TP3: 76638.72 | TP: fixed ×1.00` → `🎯 TP1 HIT` →
`🎯 TP2 HIT` → `🏆 TP3 HIT`; başka bir BUY `🛑 SL HIT | … | Price: 76497.98` ile bitti.
TP'ler SL mesafesinin 0.5/1.0/1.5 katıdır (her seviye AYRI yuvarlandığı için ölçülen
sapma ≤ 2 tick). Bu yüzden sembol başına TEK alarm yeter (mesaj şablonu yazılmaz) ve
ayrıştırma emoji'ye DEĞİL anahtar kelimelere (`BUY/SELL/EXIT/TPn HIT/SL HIT`) ve
`Anahtar: değer` çiftlerine dayanır. `TQI`/`Score` telemetriye yazılır; opsiyonel
`FOLLOWER_MIN_SCORE` (vars. 0 = kapalı). **EXIT gövdesi henüz ÖLÇÜLMEDİ** — `⚪ EXIT`
anahtar kelimesi varsayımdır (kod anahtar kelimeye dayandığı için biçim küçük
farklarla gelse de çalışır; gelmezse olay 422 alır ve loglanır, sessiz kalmaz).

**Seviye SIRASI kapısı (parser, 2026-08-23 kullanıcı kararı):** ölçülen gerçek
gövdelerde sıra DAİMA `LONG: SL < Price < TP1 < TP2 < TP3` (SHORT tersi). Giriş
olaylarında bu sıra bozuksa (ya da iki seviye EŞİTSE) gövde AlgoPro V1.6 girişi
DEĞİLDİR ve `FollowerParseError` → **HTTP 422** ile REDDEDİLİR: bir "SL"yi TP sanıp
ters tarafa emir koymaktansa işlemi kaçırmak doğrudur. Doğrulama yalnız mesajda VAR
OLAN alanlar üzerinde yapılır (eksik alan zinciri kırmaz) ve yalnız `entry`
olaylarında çalışır (HIT/EXIT mesajları yön taşımaz). `levels.resolve_levels`'deki
"yanlış taraf → hesaplanana düş" kuralı KALDIRILMADI: giriş fiyatı mesajdakinden
farklı olabilir (mesajda `Price` yoksa canlı fiyat kullanılır), o yüzden ikinci bir
savunma katmanı olarak durur.

**Neden AYRI halka:** takipçinin ölçülmüş bir kenarı YOKTUR ve boyutlaması scalper'ın
risk-tabanlı boyutlamasından tamamen farklıdır (marj %10 + ≤100x). Aynı süreçte
çalıştırmak scalper'ın soak'unu kirletir ve iki motor aynı hesapta çakışırdı. Bu yüzden
ayrı hesap/DB/süreç ve `config.py`'de fail-fast: **`BOT_MODE=follower` + mainnet =
startup HATASI** (docs/MAINNET_PLAN.md §6).

**Scalper halkasına etkisi (byte-for-byte korunması gereken taraf):** varsayılan
`BOT_MODE=scalper`; köprü yalnız `FOLLOWER_FORWARD_URL`+`SECRET` doluyken çalışır ve
`src=algopro` DIŞINDAKİ hiçbir olayı iletmez. Kod tarafında yalnız NÖTR eklemeler:
`ExitPlan.tp3_*` (varsayılan 0/None), `_verified_close_ledger`'a opsiyonel
`tp3_algo_id` adayı (scalper'da DAİMA None → aday listesi aynı),
`ScalpTradeModel.tp3_algo_id` sütunu (idempotent migration, scalper'da NULL),
`record_open(tp3_algo_id=None)`, `/risk-event`'in aktif motora yönlendirilmesi
(scalper modunda `scalper_engine` — aynı yol), `/health` ve `/api/status`'a yalnız
takipçi modunda çalışan erken dallar.

**Kanıt:** 259 takipçi testi — `tests/test_follower_parser.py` (52, TV'den alınan
GERÇEK gövdeler: SELL dizisi, BUY→TP1→TP2→TP3 dizisi, SL HIT + seviye sırası
kapısı), `test_follower_levels.py` (18, mesaj>ATR önceliği + onarılamayan TP
merdiveninin fail-closed reddi), `test_follower_plan.py` (35, kullanıcının üç örneği
birebir + ücret eşiği aritmetiği), `test_follower_brackets.py` (16, kaldıraç dilimi
önbelleği: fail-closed / bayat kayıt / tek uçuş), `test_follower_executor.py` (25,
korumalı açılış disiplini + mmr'li yeniden çapalama bütçesi + kayma sıkılaştırması +
TP1 yeniden denemesi + ücret telemetrisi), `test_follower_engine.py` (53,
kapılar/flip/exit/HIT çapraz doğrulaması + İDEMPOTANS + EŞZAMANLILIK kapıları +
ulaşılamayan break-even + gerçek `FollowerExitManager` ile uçtan uca
giriş→TP1→BE→EXIT), `test_follower_recovery.py` (6, restart kurtarmasında parça
aritmetiği ve BE uzlaştırması), `test_follower_forwarder.py` (19),
`test_follower_endpoint.py` (19, 403/422/503 + `?secret=` reddi + köprü çağrı yeri),
`test_follower_mode.py` (16, BOT_MODE fail-fast + mainnet yasağı + RISK_EVENT_SECRET
zorunluluğu + scalper nötrlüğü); ayrıca `test_scalper_pnl_recovery.py` (+2
tek-finalizer), `test_deploy_scripts.py` (+6 halka) ve `test_ledger_report.py`
(+4 `--strategy`).
`python3 -m pytest tests -q` → **947 passed, 1 skipped** (önceki: 676 passed, 1 skipped).
Backtest harness'e DOKUNULMADI — takipçi yalnız canlı olay hattında çalışır ve strateji
C'yi hiç kullanmaz (CLAUDE.md kural 2 kapsamı dışında).

**Kendi-incelemesi düzeltmeleri (commit'ten sonra, aynı dalda):** (1) `pm.place_stop_loss_or_close`
yeniden çapalama bütçesi `FOLLOWER_MAX_SL_PCT` (%5) yerine
`min(band, LIQ_GUARD/kaldıraç)` ile sınırlandı — 100x'te %5 mesafe likidasyonun
ötesindeydi; (2) girişten hemen önce `get_position_risk(force_fresh=True)` ile
"izlenmeyen ama borsada açık pozisyon" kapısı eklendi (fail-closed) — aksi halde
`record_open` DB hatası sonrası ikinci bir giriş pozisyonu ikiye katlardı.

**Düşmanca inceleme düzeltmeleri (çok-mercekli, 2026-08-23; her biri
regresyon testiyle kilitli — testlerin düzeltme olmadan KIRMIZI olduğu
doğrulandı):**

*Yarış koşulları:* (a) `ExitManager._handle_closed`'un `finally`'si koşulsuz
`_positions.pop` yapıyordu; `_finalize_close` saniyeler sürdüğü için (cancel_all
+ userTrades + income merdiveni) flip yolunun izlemeye aldığı YENİ pozisyon
siliniyordu → borsada açık, motorun bilmediği pozisyon. Artık pop KİMLİK
kontrollüdür (scalper davranışı aynı: izlenen sembole ikinci giriş yapmaz).
(b) Aynı yarışın emir-iptali tarafı için yeni kapı: sembol `exits._closing`
içindeyse GİRİŞ YOK (`close_in_flight`). (c) `risk_event_flatten` artık
`_entry_lock` altında çalışır — halt anında UÇUŞTA olan giriş henüz
`tracked_symbols()`'a girmemiş olur ve "hiç pozisyon yok" raporundan sonra
aktif halt altında açık pozisyon kalırdı. (d) `_close_tracked` emir reddini
artık "pozisyon açık" saymıyor: TP3/SL eşzamanlı dolduğunda gelen -2022'de bir
kez taze okunur. (e) `_update_kill_switch` ayrı `try`'a alındı ve
`_entries_ready()` artık safety turunun TAZELİĞİNİ de arar (fail-closed).

*Koruma aritmetiği:* (f) **Break-even artık ULAŞILAMIYORSA gönderilmiyor.**
`pm._replace_stop_loss` `-2021` alırsa pozisyonu ACİL KAPATIR; takipçide bu
İSTİSNA DEĞİL KURALDI: ücret-farkında BE mesafesi ≈ giriş+çıkış+tampon ≈ %0.15
iken TP1 mesafesi `RR1 × sl_pct`tir ve kaldıraç tavana dayanan HER işlemde
(sl_pct < ~%0.30) TP1 bu payın içinde kalır → her TP1 dolumu kalan 2/3'ü zorla
düzleştirirdi. Artık BE piyasanın yanlış tarafındaysa emir hiç gönderilmez,
AlgoPro stopu yerinde kalır, pozisyon başına BİR KEZ uyarılır. (g) Yeniden
çapalama bütçesine mmr kapısının fiyat karşılığı eklendi
(`(1/lev − mmr)/MMR_SAFETY_MULT`): 100x + mmr 0.004'te liq_guard %0.50'ye izin
verirken likidasyon mesafesi yalnız %0.60'tı. (h) Stop, GERÇEK dolum fiyatına
göre yeniden ölçülüp bütçe aşılıyorsa SIKILAŞTIRILIR (asla genişletilmez) —
MARKET girişteki kayma planlanan riski sessizce likidasyon bölgesine
kaydırabiliyordu; deftere `sl_pct_fill` yazılır. (i) TP merdiveni onarımı artık
DOĞRULANIYOR: hesaplanan değeri koymak sıralamayı garanti etmiyordu (TP1==TP2
kalabiliyordu) → onarılamayan merdivenle GİRİŞ YAPILMAZ (`tp_order`).
(j) TP1 emri konulamazsa bir kez yeniden denenir; olmazsa `tp1_missing` sayacı
+ CRITICAL (TP1 yoksa BE hiç kurulamaz). (k) Kurtarma yolu parçaları artık
canlı yolla AYNI kuralla (`split_three_quantities` + `stepSize`) kurulur —
`quantity/3` varsayımı küçük adım sayılarında BE eşiğini hiç geçirmiyordu; ve
`tp1_done=True` kurtarılırken canlı stop BE'den gevşekse bayrak düşürülür
(takipçide trailing yok, telafi eden ikinci yol yoktu).

*İşletim/güvenlik:* (l) `BOT_MODE=follower` + boş `RISK_EVENT_SECRET` artık
TESTNET'te de startup HATASI — halkanın tek uzaktan durdurma yolu odur
(Telegram yok; köprüyü kapatmak açık pozisyonu kapatmaz). (m) `/follower/event`
`?secret=` KABUL ETMİYOR (403): erişim logu query string'i düz metin yazar.
(n) `FOLLOWER_FORWARD_TIMEOUT_SECONDS` 2 → 20 sn: yanıt olay işlendikten sonra
döndüğü için 2 sn her BAŞARILI girişte sahte "iletemedi" uyarısı üretiyordu
(`_post` yeniden deneme yapmaz — çift giriş riski yoktu).

**Ücret eşiği (ÖLÇÜM — kapı varsayılan KAPALI, kullanıcı kararı gerekir):**
`sl_roi = lev × sl_pct`, `tp1_roi = RR1 × sl_roi`, gidiş-dönüş komisyon
ROI'si = `lev × 2 × oran × 100`. Kaldıraç LEV_MAX'e KIRPILDIĞINDA (raw hedef
> 100, yani `sl_pct < ~%0.30`) TP1 ROI komisyonun ALTINA düşer. Kullanıcının
kendi BTC örneğiyle: `sl_pct %0.08 → lev 100 → TP1 = marjın %4'ü`, komisyon
(taker %0.05 × 2 × 100) = **marjın %10'u** → üç TP de dolsa (%4+%8+%12)/3 = %8
brüt < %10 komisyon, yani **yapısal negatif beklenti**; SL ise −%8 −%10 = −%18.
DOGE örneği (`sl_pct %0.30 → TP1 %15`) ve `sl_pct %0.60 → 50x → TP1 %15` bu
eşiğin ÜSTÜNDEDİR. Bu bir boyutlama tercihi değil aritmetiktir, ama düzeltmesi
TP1/kaldıraç/boyut değiştirmeyi gerektirir — kullanıcının 2026-08-23 kararı
(*"boyut/TP1/stop ile kayıp küçültme YASAK; çözüm sinyal kalitesi"*) bunu
YASAKLAR. Bu yüzden davranış DEĞİŞMEDİ; yapılanlar: her girişte WARNING,
deftere (`signal_reason`: `tp1_roi=`, `fee_roi=`, `fee_roi_real=`) ve
`/follower/status`'a (`roundtrip_fee_roi_pct`, `tp1_covers_fees`) yazım, ve
VARSAYILAN KAPALI `FOLLOWER_MIN_TP1_FEE_RATIO` kapısı. Canlı defter hakemdir:
`ledger_report.py --db tradingbot_ap.db --strategy AP` sonucu bu aritmetiği
doğrular ya da çürütür.

**Beklenti:** YOK. Bu halka bir hipotez testidir: "AlgoPro'nun kendi seviyeleriyle,
kendi giriş/çıkış komutlarıyla, 1m'de kâr edilebilir mi?" Kanıt canlı defterden
gelecek: `scripts/ledger_report.py --db tradingbot_ap.db --strategy AP`. Terfi kuralı
scalper'ınkiyle AYNI değildir — takipçi mainnet'e KENDİ BAŞINA ÇIKMAZ (D20 config
kapısı), ancak ayrı bir kullanıcı kararıyla ve ayrı bir kanıt setiyle değerlendirilir.

**Geri alma:** ana bottan `FOLLOWER_FORWARD_URL`'i boşalt + `supervisorctl restart
tradingbot_v2` → köprü kapanır, takipçi sinyal ALMAZ (açık pozisyonlarını yönetmeye
devam eder). Takipçiyi tamamen durdurmak: `supervisorctl stop tradingbot_ap`
(önce `POST /risk-event {"action":"flatten"}` ile düzleştir). Kod geri alması
gerekirse: bu commit'teki `src/strategies/follower/*`, `src/services/follower_forwarder.py`,
`src/main.py` (takipçi dalları + köprü satırı), `src/core/config.py` (`bot_mode` +
`FOLLOWER_*`), `src/models/scalp_trade.py` + `src/core/database.py` (`tp3_algo_id`),
`src/strategies/scalper/{types,exits,tracker}.py` (nötr eklemeler),
`scripts/{deploy.sh,server_deploy.sh,ledger_report.py}` değişikliklerini revert et.
### D20a — Takipçi halkası düşmanca inceleme düzeltmeleri (19 ajan) · 2026-08-23 · AKTİF
**Bağlayıcılık:** D20 ile çelişirse **D20a geçerlidir**. D20'nin mimarisi (ayrı süreç,
ayrı hesap, AlgoPro'nun kendi seviyeleri, 3 parça çıkış) DEĞİŞMEDİ; değişenler aşağıdaki
KAPILAR ve VARSAYILANLARDIR. `BOT_MODE=scalper` davranışı byte-for-byte korunmuştur
(scalper motoru/kapıları/`.env` varsayılanları değişmedi; ana bottaki TEK dokunuş
`/tv-signal`'ın köprü satırıdır — aşağıda 5).

**1. [high] Dolum stopu ZATEN geçmişse stop UYDURULUYORDU.**
`executor._finalize`, stop mesafesini `abs(entry − stop)` ile ölçüyordu. İşaretsiz bu
ölçüm, stopun gerçek dolumun YANLIŞ tarafında kalmasını GİZLER: LONG'da dolum 100 iken
AlgoPro stopu 101 ise mesafe "%1" görünür, bütçe kapısı onu %0.15'e "sıkıştırır" ve
AlgoPro'nun HİÇ SEÇMEDİĞİ bir stop konur; sıkışmazsa SL emri `-2021` alır ve
`pm._reanchor_stop_price` stopu canlı fiyatın buffer'ına (%0.15) çapalar — 100x'te marjın
%15'i. İkisi de "AlgoPro'yu takip et" sözleşmesinin ihlalidir.
*Düzeltme:* (a) MARKET emrinden ÖNCE canlı fiyatla **taraf kontrolü** (`stop_on_correct_side`)
+ **sapma kapısı** `FOLLOWER_MAX_SIGNAL_DRIFT_PCT` (vars. 0 = SL mesafesinin %50'si);
(b) dolumdan SONRA **işaretli** taraf kontrolü — stop ters taraftaysa tez GEÇERSİZDİR:
`pm.emergency_close` ile reduce-only MARKET kapanış, defter satırı
(`follower_stop_already_passed`), cooldown, `stop_already_passed` sayacı; kapatılamazsa
`UnprotectedPositionError` (motor entry-halt latch'ler). **Yeniden çapalama YOK.**
(c) `sl_pct_fill` telemetrisi artık GERÇEKTEN KONAN stoptan (`effectiveStopPrice` sonrası)
yazılır.

**2. [high] Parser FAIL-OPEN'dı.** Tanınmayan bir gövdede yalnız bir yön kelimesi
("bullish", "long") geçmesi `kind=entry` üretiyor ve POZİSYON açtırabiliyordu (gövde
taraması son çare olarak duruyordu). *Düzeltme:* takipçi girişi YALNIZ tam AlgoPro V1.6
alert() biçimini kabul eder: **başlıkta** olay anahtarı (`BUY`/`SELL`/`EXIT`/`TPn HIT`/
`SL HIT`) + `| BINANCE:<SEMBOL>USDT |` + `| TF:` + `| Price:` + girişlerde DÖRT seviyenin
hepsi (`SL/TP1/TP2/TP3`) + yön sıralaması. Eksik alan → 422 + WARNING. Gövde-ortası
anahtar taraması KALDIRILDI. `kind=` şablonu (elle/curl) korunur ama girişte aynı alan
şartına tabidir. **Sonuç:** `levels.py`'deki k×ATR yedek kuralı GİRİŞLER için artık
ULAŞILAMAZ (mesajda SL olmayan giriş 422 alır); kural, mesaj seviyesi girişin yanlış
tarafında kaldığında ikinci savunma katmanı olarak KORUNUR.

**3. [high] Ücret eşiği kapısı VARSAYILAN AÇIK (`FOLLOWER_MIN_TP1_FEE_RATIO=1.0`).**
Ölçülen AlgoPro seviyeleriyle (BTC 1m, SL ≈ %0.07, TP1 = 0.5×SL) her sonuç negatiftir:
`tp1_roi = RR1 × lev × sl_pct`, komisyon ROI'si = `lev × 2 × oran × 100`. Kaldıraç
eşitliğin İKİ TARAFINDA da çarpandır → eşik kaldıraçtan BAĞIMSIZDIR:
`sl_pct ≥ ratio × 2 × oran × 100 / RR1` = **%0.20** (ratio 1.0, taker %0.05, RR1 0.5).
Kapı boyut/TP1/stop DEĞİŞTİRMEZ (kullanıcının 2026-08-23 yasağı korunur) — yalnız
komisyonu ödeyemeyeceği aritmetik olarak kanıtlı bir işleme HİÇ girmez.
*Kullanıcı kararı:* `FOLLOWER_MIN_TP1_FEE_RATIO=0` ile KAPATILABİLİR (o hâlde her girişte
WARNING loglanır). Taker oranı `/fapi/v1/commissionRate`'ten okunur (1 sa önbellek),
okunamazsa muhafazakâr config oranı (`max(taker, maker)`, vars. %0.05). Kapı EMİRDEN
ÖNCE çalışır; reddedilen giriş `reject_counters.fee_gate` sayacında ve kalibrasyon
defterinde (`state/follower_levels.jsonl`, `rejected` alanı) görünür.

**4. [high] Deploy halkası ↔ `BOT_MODE` bağı yoktu.** `RING` ile `.env` arasında hiçbir
doğrulama yoktu: takipçi `.env`'i olan bir dizine `RING=testnet` ile deploy edilebiliyor
(scalper sanılan halkada TAKİPÇİ motoru yeniden başlıyor) ve tersi mümkündü.
*Düzeltme (`scripts/server_deploy.sh` + `scripts/restart_safe.sh`):* `RING=testnet|mainnet`
iken `.env`'de `BOT_MODE=follower` varsa `die`; `RING=follower` iken `BOT_MODE=follower`
yoksa `die`. Ayrıca **RING artık `REPO_DIR`/`PROGRAM`/`HEALTH_URL`/`HALT_FILE`in TEK
GERÇEK KAYNAĞIDIR**: `PROGRAM`/`HEALTH_URL` override'ı halka ile uyuşmazsa `die`,
`REPO_DIR` override'ı `DEPLOY_REPO_DIR_OVERRIDE=1` ister. `supervisorctl restart`
BAŞARISIZ olursa artık GERİ ALINIR (eskiden `set -e` script'i rollback ÇAĞRILMADAN
düşürüyordu: sunucuda yeni kod + çalışmayan süreç kalırdı).

**5. [high] Köprü parmak izi yanlış pozitifi.** `resolve_tv_source`, `?src=` yoksa
gövdede `"| TF:"` ya da `"| Price:"` görmesini "algopro" saymaya yetiyordu — elle yazılmış
bir LuxAlgo/BotV3 şablonu bu damgayı taşıyabilir ve takipçide sonucu POZİSYON açmaktır.
*Düzeltme:* iletim kararı GÖVDEYE bakan katı tanıyıcıya (`parser.algopro_alert_kind`,
bulgu 2 ile AYNI kural) taşındı ve `TV_SOURCE_ALLOWLIST`'ten BAĞIMSIZ hâle geldi
(`?src=` yalnız telemetri). LuxAlgo/BotV3/serbest metin ASLA iletilmez; gerçek bir AlgoPro
gövdesi `?src=` yanlış olsa bile iletilir. İletilmeyen gövdeler sayaçlara işlenir
(`GET /follower/forwarder`; `last_skipped.body_head` secret MASKELİDİR) ve başarısız
iletim uyarıları **dakikada 1** ile oran-sınırlıdır (bastırılanlar `suppressed_warnings`).
Timeout artık bölünmüştür: bağlantı/yazma 2 sn (erişilemeyen halka task biriktirmesin),
okuma `FOLLOWER_FORWARD_TIMEOUT_SECONDS` (20 sn — yanıt olay işlendikten sonra döner).

**6. [medium] Boyutlama/seviyeler bayat alarm fiyatından; giriş kilidi uzun bekletiyor.**
`_handle_entry` `event.price` varsa canlı fiyatı HİÇ okumuyordu; `sl_pct` kaldıraç
formülünün paydasıdır ve bayat fiyat kaldıracı yanlış ölçekler. *Düzeltme:* kilit
alındıktan SONRA (a) **olay yaşı** kapısı `FOLLOWER_MAX_EVENT_AGE_SEC` (vars. 20 sn;
yaş HTTP'de alım anından ölçülür), (b) DAİMA canlı fiyat, (c) sapma kapısı, (d) taraf
kontrolü. Aynı kapılar `executor`da emirden hemen önce TEKRAR uygulanır.

**7. [medium] Terminal HIT + borsada AÇIK pozisyon yalnız telemetriydi.** AlgoPro
"SL HIT" derken pozisyon borsada duruyorsa stop dolmamış ya da hiç konulamamıştır —
eski davranış WARNING'ti ve 100x'lik pozisyon korumasız taşınıyordu. *Düzeltme:* SL HIT →
kalan miktar reduce-only MARKET (`exit_reason=ALGOPRO_SL`), TP3 HIT → `ALGOPRO_TP3`;
kapanış borsadan DOĞRULANIR (doğrulanamazsa `accepted:false`, izleme sürer). Terminal
olmayan HIT'lerde (TP1/TP2) merdiven emirleri kontrol edilir ve EKSİK TP bacakları
yeniden konur (`exits.ensure_tp_orders`; `tp_repair` sayaçları `/follower/status`ta).

**8. [medium] Yetim pozisyonlar görünmezdi.** `recover()` yalnız DB'deki OPEN satırlarına
bakar; `record_open` DB hatası sonrası açık kalan bir pozisyon ne EXIT'e, ne flip'e, ne
`flatten`a görünürdü. *Düzeltme:* başlangıçta ve HER safety turunda borsa `positionRisk`
ile izlenenler karşılaştırılır (`get_all_positions`, 15 sn hesap önbelleği; şüphe TAZE
okumayla doğrulanır). Yetim bulunursa **ENTRY-HALT + CRITICAL**; `/risk-event flatten`
artık yetimleri de kapatır. Yanlış pozitif korumaları: `_entry_lock` tutuluyorsa (uçuşta
giriş) tur atlanır, `_closing` sembolleri hariç tutulur, kurtarma tamamlanmadıysa denetim
yapılmaz.

**9. [kapak dışı, aynı dalda]**
(a) TP2/TP3 doğrulaması `tp1_done`un ARKASINDAYDI; `tp1_done` "stop BE'ye taşındı"
demektir ve ücret-farkında BE takipçide çoğu zaman ULAŞILAMAZ → merdivenin geri kalanı
YAPISAL OLARAK ölüydü. Yeni `tp1_filled` bayrağı dolum OLGUSUNU taşır; fill kanıtı artık
BE denemesinden ÖNCE alınır. (b) TP seviyeleri GERÇEK doluma göre doğrulanır (LONG: TP >
dolum) — yanlış tarafta kalan bacak KONULMAZ (`tp_wrong_side`), anında tetiklenip zararla
kapatırdı. (c) `min_score`/allowlist/TF kapıları artık YALNIZ girişte — çıkış/HIT
olaylarını bloklamaz ("riskten çıkma" hiçbir kapıya takılmaz). (d) `_handle_hit` kimlik
kontrolü: pozisyon nesnesi değiştiyse `accepted:false` (hiçbir şey yapılmadı).
(e) Restart kurtarması kayıp TP emirlerini yeniden koyar; bunun için defter notu artık
AlgoPro'nun MUTLAK seviyelerini taşır (`ap_sl`/`ap_tp1..3`, kayıpsız biçim — `:g` 6
haneye kırpıyordu). (f) Boyutlama ve günlük risk kapısı AYNI bakiye tanımını kullanır
(**availableBalance**; eskiden risk kapısı `totalWalletBalance` okuyordu). (g)
`parse_brackets` `mmr == 0` satırını GEÇERSİZ sayar (sıfır bakım marjı mmr kapısını
dişsiz bırakıyordu). (h) `/follower/event` `?secret=` kabul etmez — INTEGRATIONS'taki
yanlış satır düzeltildi. (i) `/follower/status` scalper modunda **404** (mod izolasyonu;
eskiden boş bir "takipçi durumu" dönüp operatörü yanıltıyordu). (j) `.env` reçeteleri
için `scripts/restart_safe.sh <halka>`: ban penceresi + entry-halt + BOT_MODE ön
kontrolleri, saniye damgalı `.env` yedeği, ayar doğrulaması, restart, sağlık yoklaması.

**Kanıt:** `python3 -m pytest tests -q` → **1580 passed, 1 skipped** (önceki: 1457
passed, 1 skipped; +123 test). Her bulgu için regresyon testi yazıldı ve **düzeltme
olmadan KIRMIZI olduğu `git stash` ile doğrulandı** (parser/executor kümesi 32/35 kırmızı,
motor/exits kümesi 30/34, deploy+köprü kümesi 8 kırmızı + 27 hata). Backtest harness'e
DOKUNULMADI (takipçi strateji C'yi hiç kullanmaz).

**Değişen varsayılanlar:** `FOLLOWER_MIN_TP1_FEE_RATIO` 0.0 → **1.0**; yeni
`FOLLOWER_MAX_SIGNAL_DRIFT_PCT=0` (türetilmiş: SL mesafesinin %50'si) ve
`FOLLOWER_MAX_EVENT_AGE_SEC=20`. Diğer tüm `FOLLOWER_*`/`SCALPER_*` varsayılanları AYNI.

**Doğrulanamadı (dürüst kayıt):** (i) `⚪ EXIT` gövdesi TV'de HÂLÂ ÖLÇÜLMEDİ — katı
tanıyıcı `EXIT` anahtar kelimesi + `| BINANCE: | TF: | Price:` bekler; AlgoPro farklı bir
biçim üretirse olay 422 alır ve loglanır (sessiz kalmaz). (ii) Ücret eşiğinin canlı
etkisi (kaç girişin reddedileceği) TESTNET DEFTERİYLE ölçülecek — aritmetik kesindir,
"kaç işlem kalır" değildir. (iii) ~~Yetim denetimi ayrı bir Binance hesabı varsayar (D20);
aynı hesapta başka bir bot çalışırsa her pozisyonu yetim sayar.~~ **D20b ile
GİDERİLDİ:** gerçek yetim artık "hiçbir motorun izlemediği pozisyon"dur —
`symbol_reservations`ta BAŞKA bir sahibi olan semboller denetimden düşülür. (iv) Sunucuda
çalıştırılmadı: bu dalda deploy YAPILMADI (worktree; canlıya/`.env`'e dokunulmadı).

### D20b — GÖMÜLÜ AlgoPro takipçisi (`FOLLOWER_EMBEDDED`, aynı hesap + 1000 USD SANAL defter) · 2026-08-23 · **CANLI (testnet, 2026-08-23 23:24 UTC)** — kanıt: YOK (testnet ölçümü kanıt olacak)
> **Canlı durum (2026-08-23 23:24 UTC, commit c2f9849, pid 257848):** `.env`'de
> `FOLLOWER_EMBEDDED=true FOLLOWER_SYMBOLS=TUTUSDT FOLLOWER_VIRTUAL_CAPITAL_USDT=1000`
> `FOLLOWER_MAX_POSITIONS=1 FOLLOWER_DAILY_LOSS_LIMIT_PCT=10` (yedek:
> `backups/env.bak-20260823-232144-deploy` ve `-232402-embedded-follower-tut`).
> Coin seçimi: 40-coin 1m taraması + TV AlgoPro panel okumaları + 3 hakem + çürütücü →
> **TUTUSDT** (yedek ZEC); testnet bracket TUT 25x/5k$ mmr %2 → motor ~20x. TV alarmı:
> mevcut 1dk any-alert() AlgoPro alarmı (eski BTCUSDT testi) TUTUSDT.P'ye yeniden bağlandı
> (URL'ye dokunulmadı); alarm serisi **Heikin Ashi** grafikten (kullanıcının grafiği HA —
> panel ölçümleriyle tutarlı; seviyeler mesajdan geldiği için motor tarafı etkilenmez, not).

**Bağlayıcılık:** D20/D20a ile çelişen her noktada **D20b geçerlidir**; takipçi
artık ayrı hesap/süreç/panel GEREKTİRMEZ. D20a'nın kapılarından şunlar
**DEĞİŞMEDEN** yürürlüktedir: katı AlgoPro tanıyıcısı, ücret eşiği
`FOLLOWER_MIN_TP1_FEE_RATIO=1.0`, "dolum stopu geçmişse kapat", olay yaşı/sapma
kapıları, boyutlama aritmetiği, `/follower/event` secret sözleşmesi.

**İKİ KAPI GÖMÜLÜ MODDA DEĞİŞTİ** (ayrı halkada AYNEN korunur — `BOT_MODE=
follower`'da bu paragraf hiç uygulanmaz):
* **"yetim = entry-halt" ARTIK KOŞULLU.** Gömülü modda hesap PAYLAŞILIR;
  hiçbir motorun izlemediği ve **rezerve etmediği** bir pozisyon MEŞRU olabilir
  (elle/Telegram açılmış). Böyle bir pozisyon entry-halt kurmaz ve flatten
  kapsamına girmez — WARNING + sayaç + pano satırı. Takipçinin KENDİ
  rezervasyonunu taşıyan yetimi (ör. `record_open` DB hatası) D20a davranışını
  AYNEN korur: CRITICAL + kalıcı entry-halt. Ayrıntı: aşağıda §5 ve
  "Düşmanca inceleme" YÜKSEK-5.
* **Kapasite tavanı artık motor-başınadır** (D20a'da hesap-geneliydi) — bkz. §4
  ve YÜKSEK-7.

Ayrı halka (`BOT_MODE=follower`) desteği KALDIRILMADI; RUNBOOK'ta **tercih
edilen kurulum artık gömülü moddur**.

**Kullanıcı kararı (2026-08-23, bağlayıcı — önceki "ayrı hesap" kararının yerine
geçer):** *"Yeni hesap yok, yeni panel yok."* AlgoPro takipçisi AYNI testnet
hesabında, AYNI süreçte, AYNI dashboard'da, **1000 USD'lik SANAL defterle**
çalışır; AlgoPro alarmları doğrudan ona bağlanır; kullanıcıdan hiçbir ek adım
istenmez. Ek kararlar (aynı gün): takipçi **TEK coin** ile çalışabilsin
(`FOLLOWER_SYMBOLS`) ve o coin **ana sistemden tamamen çıkarılsın**; kaldıraç
formülünün payı ayarlanabilir olsun (`FOLLOWER_SL_MARGIN_PCT`). **Sembol KODDA
SABİT DEĞİLDİR** — seçim ayrı bir ölçümle belirlenir ve yalnız `.env`'e yazılır.

**Ne (yedi parça):**

1. **Aynı süreç.** `BOT_MODE=scalper` + `FOLLOWER_EMBEDDED=true` iken lifespan
   scalper motorunun YANINDA bir `FollowerEngine` başlatır: aynı
   `ImprovedBinanceClient`/anahtarlar, aynı `tradingbot.db` (`scalp_trades`,
   `strategy="AP"`), aynı erişim-logu redaksiyonu. `FOLLOWER_EMBEDDED=false`
   (varsayılan) → bu blok HİÇ çalışmaz ve bugünkü davranış birebir korunur.
   `BOT_MODE=follower` + `FOLLOWER_EMBEDDED=true` = startup HATASI (çelişki).
   `FOLLOWER_EMBEDDED=true` + mainnet = startup HATASI (D20'nin mainnet yasağı
   `is_follower_mode` yerine `follower_active` üzerinden okunur).

2. **1000 USD SANAL defter.** `FOLLOWER_VIRTUAL_CAPITAL_USDT` (vars. 1000, YALNIZ
   gömülü modda uygulanır). `equity = taban + AP işlemlerinin gerçekleşmiş net
   PnL'i`; toplam RAM'de değil **DB'den** hesaplanır (`compounding_snapshot`,
   `strategies=("AP",)`) → **restart'a dayanıklıdır**. Muhafazakâr kural scalper'ın
   sanal kasasıyla AYNIdır: Binance'ın doğruladığı net PnL iki işaretiyle sayılır,
   tahmini (fallback) satır YALNIZ negatifse sayılır, legacy satır hiç sayılmaz —
   sermaye doğrulanmamış kârla ŞİŞMEZ. Marj = `equity × FOLLOWER_MARGIN_PCT` (%10).
   Günlük kesici `FOLLOWER_DAILY_LOSS_LIMIT_PCT` (varsayılan **15 → 10**) bu SANAL
   sermayeye göre ölçülür. **Hesabın gerçek `availableBalance`'ı gereken marjı
   karşılamıyorsa giriş YAPILMAZ** (`insufficient_balance` sayacı + ERROR log) —
   scalper'ın açık marjı hesabı doldurmuş olabilir. Defter okunamazsa giriş
   fail-closed reddedilir (`virtual_equity`).

3. **İki defter BİRBİRİNİ KİRLETMEZ.** Scalper'ın sanal kasası
   `exclude_strategies=("AP",)` ile hesaplanır (ayrı halkada DB'de AP satırı
   yoktur → davranış birebir aynı). **Günlük kesiciler: gömülü modda HER MOTOR
   KENDİ DEFTERİNDEN beslenir** — takipçi `strategy='AP'`, scalper AP HARİÇ
   (`realized_pnl` komisyon düşülmüş nettir). Binance `/fapi/v1/income` iki
   defteri birlikte raporladığı için o kaynak gömülü modda KULLANILMAZ.
   `/scalper/status → daily_pnl_source` hangi kaynağın etkin olduğunu söyler
   (`scalper_ledger` ↔ `binance_account_income`). *Bilinçli bedel:* defter
   yalnız KAPANAN işlemleri sayar; kısmi TP dolumları gün içinde iki motorda da
   görünmez. `FOLLOWER_EMBEDDED=false` → income yolu birebir korunur.
   > İlk tasarım income'dan AP'yi DÜŞÜYORDU; düşmanca inceleme bunu
   > çürüttü (aşağıdaki bölüm, KRİTİK-2): income 120 sn önbelleklidir ve AP
   > kapanışları scalper'ın `close_seq`'ini artırmadığı için düzeltme
   > çağrıların ~%98'inde ATLANIYORDU; ayrıca AP merdiveninin kısmi TP
   > dolumları hiç defter satırı yazmadığı için eşiği DARALTMIYOR,
   > GEVŞETİYORDU.

4. **Çakışma: aynı sembolde tek motor.** Mevcut süreç-içi
   `symbol_reservations` kaydı (owner `"scalper"` / `"follower"` /
   orchestrator) İKİ YÖNDE de kapıdır: takipçi başka sahibi olan sembole
   girmez (`reserved_by_other`), scalper `_evaluate_symbol`'ün İLK satırında
   yabancı sahipli sembolü atlar. Rezervasyon emirden HEMEN ÖNCE alınır
   (`_entry_lock` yalnız takipçinin kendi girişlerini sıraya sokar, scalper'ın
   tarama turunu değil) ve safety turunda izlenmeyen semboller bırakılır —
   ancak `_entry_halted` ya da `_entry_lock` tutuluyorken **hiçbir sahiplik
   bırakılmaz** (uçuşta giriş `track()` edilene kadar `tracked_symbols()`ta
   görünmez). **Kapasiteler GERÇEKTEN AYRIDIR:** her motorun tavanı YALNIZ
   kendi rezervasyonlarını sayar (`symbol_reservations.reserve(...,
   capacity_owners=...)`); takipçinin 4 pozisyonu scalper'ın hesap-geneli
   `MAX_POSITIONS` slotlarını YEMEZ ve takipçi de kendi tavanını (girişte
   atomik olarak) AŞAMAZ.
   > İlk metin bunu *"yeni bir daralma değil, aynı hesabın gerçeğidir"*
   > diyerek geçiştiriyordu; düşmanca inceleme ÇÜRÜTTÜ (aşağıdaki bölüm,
   > YÜKSEK-7): varsayılanlarla (MAX_POSITIONS=5, SCALPER_MAX_POSITIONS=3,
   > FOLLOWER_MAX_POSITIONS=4) takipçi 4 pozisyon taşırken scalper 3 yerine
   > 1 slota düşüyordu ve ters yönde HİÇBİR sınır yoktu. Bunun bilinçli
   > sonucu: hesapta eşzamanlı `SCALPER_MAX_POSITIONS + FOLLOWER_MAX_POSITIONS`
   > pozisyon olabilir; `MAX_POSITIONS` bu toplamı TEMSİL ETMEZ.

5. **Yetim denetimi ÜÇ SINIFA ayrıldı (D20a bulgu 8, not (iii)).** Gömülü
   modda borsadaki her açık pozisyon şu üç kümeden birindedir:
   * **izlenen** — takipçinin kendi `exits._positions`'ı (ya da `_closing`);
   * **yabancı** — başka bir motorun GERÇEKTEN izlediği (`foreign_tracked_cb`:
     scalper `tracked|pending|opening`, orchestrator `active_positions`) ya da
     `symbol_reservations`ta başka sahibi olan sembol → DOKUNULMAZ;
   * **kalan** — ikisi de değil. Burada AYRIM yapılır:
     - takipçinin KENDİ rezervasyonunu taşıyorsa **YETİM**dir → CRITICAL +
       kalıcı entry-halt + `/risk-event flatten` onu KAPATIR (D20a birebir);
     - hiçbir rezervasyon yoksa **SAHİPSİZ**tir (elle/Telegram açılmış
       olabilir) → yalnız WARNING + `unknown_positions` + pano satırı; halt
       KURULMAZ, flatten DOKUNMAZ.

   Rezervasyon kaydı tek kaynak DEĞİLDİR: scalper entry-halt'a düştüğünde
   rezervasyonlarını DONDURUR, o yüzden diğer motorların gerçek izleme listesi
   birincil kaynaktır. Sıra da düzeltildi: safety turunda önce yetim denetimi,
   SONRA sahiplik senkronu; denetim borsa hatasıyla çalışamadıysa senkron
   HİÇBİR sahipliği bırakmaz. Ayrı halkada (`BOT_MODE=follower`) "yabancı" ve
   "sahipsiz" kümeleri boştur → D20a birebir. Kaldıraç: her motor girişten ÖNCE
   kendi kaldıracını ayarlar; aynı sembolde sıralı kullanım sorun çıkarmaz
   çünkü sembol aynı anda tek motordadır.

6. **Köprü: süreç içi teslim.** `/tv-signal`'a gelen gövde **katı AlgoPro
   alert() tanıyıcısından** (D20a bulgu 5 ile AYNI kural: `BUY/SELL/EXIT/TPn
   HIT/SL HIT` + `| BINANCE:` + `| TF:` + `| Price:`, girişte dört seviye)
   geçerse gömülü modda **doğrudan takipçiye** verilir ve istek ORADA biter:
   yanıt `200 {"routed":"follower", …}`, ana botun sağlamasına **OY YAZILMAZ**,
   `external_signal` ÇAĞRILMAZ, HTTP köprüsü kullanılmaz (çift teslim yok).
   Ana bot davranışı DEĞİŞMEZ: eski özel mesaj biçimi (`BUY on {{ticker}} |
   TF: 5 | Price: x`) katı tanıyıcıdan GEÇMEZ ve eskisi gibi oy vermeye devam
   eder. Karar `TV_SOURCE_ALLOWLIST`/`?src=`ten BAĞIMSIZDIR; secret doğrulaması
   AYNEN önce çalışır (403'te takipçiye hiçbir şey ulaşmaz) ve `?dry_run=1`
   yine YAN ETKİSİZDİR. Ayrı halka modunda HTTP köprüsü olduğu gibi kalır.

7. **Tek coin + otomatik dışlama.** `FOLLOWER_SYMBOLS` (virgüllü, vars. BOŞ):
   doluysa takipçi evreni ODUR ve **gömülü modda** o semboller scalper'ın
   tarama evreninden (`_exclude_follower_symbols`, restart'ta hesaplanır + loga
   yazılır: *"Tarama evreninden çıkarıldı — AlgoPro takipçisine ayrılmış: …"*)
   ve TV giriş oylamasından (`external_signal`, `SCALPER_TV_SYMBOL_ALLOWLIST`'ten
   BAĞIMSIZ kapı) OTOMATİK çıkarılır. Boş bırakılırsa hiçbir sembol çıkarılmaz
   ve takipçi evreni bugünkü 8 majördür. `.env`'deki allowlist'ler ayrıca
   daraltılmalıdır (kullanıcı deploy'da yapar) — koddaki dışlama, o adım
   unutulursa devreye giren ikinci savunmadır; rezervasyon kilidi üçüncüsü.

**Kaldıraç formülünün payı ayarlanabilir:** `FOLLOWER_SL_MARGIN_PCT`
("stop, marjın yüzde kaçı olsun?", vars. **30**, geçerli aralık **10–50**,
dışı = startup HATASI) →
`lev = clamp(round(FOLLOWER_SL_MARGIN_PCT / sl_pct), FOLLOWER_LEV_MIN,
FOLLOWER_MAX_LEVERAGE)`. Eski ad `FOLLOWER_SL_ROI_TARGET` ÇALIŞMAYA DEVAM EDER
(ikisi startup'ta eşitlenir); ikisi birden ve FARKLI verilirse startup HATASI —
sessiz galip yok. `FOLLOWER_MAX_LEVERAGE` = `FOLLOWER_LEV_MAX`ın eş anlamlısı.

**Gözlemlenebilirlik:** (a) panoda yeni **"AlgoPro Takipçi"** kartı — coin(ler),
sanal sermaye/güncel equity/toplam K/Z/günlük K/Z, işlem başı marj, açık
pozisyonlar (giriş/SL/TP1-3/kaldıraç/marj/**ROI**/TP-BE durumu), komisyon kapısı
ret sayacı, alarm olayı sayısı + son olay saati, giriş kilidi/kill-switch/yetim
uyarıları; kart **MEVCUT `/api/status` gövdesinden** beslenir (yeni yoklama YOK —
2026-08-18 pano-açlığı dersi) ve `dashboard_snapshot()` hiç REST yapmaz.
(b) "Son İşlemler" tablosunda AP satırları altın renkli şerit + `AP` rozetiyle
ayrılır. (c) **D21 adli kaydı takipçi girişlerinde de doldurulur**: `source="AlgoPro"`,
`tv.sources=["algopro"]`, `algopro{tqi, score, alarm_price, levels_source, sl,
tp1..3}`, `sl_pct_plan/sl_pct_fill/tp_roi_pct/fee_roi_real_pct`; strateji
göstergesi/rejim/lider kapısı UYDURULMAZ, `off` yazılır. (d) `/follower/status`
gömülü modda da çalışır (eskiden `BOT_MODE=scalper`'da 404'tü). (e) `/health`
takipçiyi AYRI bileşen olarak raporlar ama `core_healthy`'yi ETKİLEMEZ —
takipçinin bayat safety turu için doğru yanıt süreci restart etmek değil,
takipçinin kendi entry-halt kilididir (2026-08-14 dersi). (f)
`ledger_report.py`'ye **"3b) Strateji bazında"** bölümü eklendi (`--strategy AP`
zaten vardı).

**Acil durdurma:** `/risk-event` gömülü modda **İKİ motora da** uygulanır
(`_risk_engines()`): `halt` önce takipçiyi durdurur, `flatten` iki motorun
pozisyonlarını da kapatır ve `flattened`/`errors` listelerini birleştirir,
`status` "aktif mi?" için HERHANGİ birine bakar ve açık pozisyonları TOPLAR.
`RISK_EVENT_SECRET` gömülü modda ZORUNLU DEĞİLDİR (scalper'ın Telegram/
supervisor yolları vardır) ama boşsa startup'ta WARNING loglanır. Entry-halt
dosyaları AYRIDIR (`state/scalper_entry_halt.json` ↔
`state/follower_entry_halt.json`): biri diğerinin kilidini açmaz/kapatmaz.

**Düşmanca inceleme (çok-mercekli, 2026-08-23): 41 bulgu → 35 doğrulandı (iki
bağımsız çürütücü) → TAMAMI DÜZELTİLDİ.** Mercekler: yetim-güvenlik,
defter-ayrımı, köprü-güvenlik, rezervasyon-yarışı, pano/REST, config-deploy.
Her düzeltme önce KIRMIZI testle kilitlendi. Bulgular tekilleştirilmiş
başlıklarla:

**KRİTİK-1 — Restart kurtarması iki defteri KARIŞTIRIYORDU.**
`ExitManager.recover()` → `tracker.open_trades()` `strategy` sütununa hiç
bakmıyordu; gömülü modda scalper takipçinin AP satırını, takipçi de scalper'ın
C satırını KENDİ pozisyonu sanıp izlemeye alıyordu → aynı net pozisyonun İKİ
yöneticisi (iki stop taşıma, iki `cancel_all_open_orders`, iki kapanış defteri)
ve AlgoPro pozisyonuna scalper'ın chandelier/reaper/TP1-oranı kuralları.
*Düzeltme:* `open_trades(strategies=…, exclude_strategies=…)` (aynı desen
`compounding_snapshot`'taki gibi) + `ExitManager.recovery_strategies()`
kancası (scalper AP hariç, `FollowerExitManager` yalnız AP) + `recover()`
içinde satır bazlı İKİNCİ SAVUNMA (beklenen küme dışı satır atlanır + WARNING).
Parite testi: iki motorun kurtardığı kümelerin kesişimi BOŞ.

**KRİTİK-2 — Scalper'ın günlük PnL'i başkasının zararıyla kirleniyordu.**
Ayrıntı yukarıda §3'te. *Düzeltme:* gömülü modda kaynak Binance income değil
KENDİ DEFTERİ (`_ledger_daily_pnl`, önbeleksiz tek `SUM()`); önbellek-kirliliği
ve kısmi-dolum sınıflarının İKİSİ de kökten kapandı.
`/scalper/status → daily_pnl_source` yeni alan.

**KRİTİK-3 — `FOLLOWER_EMBEDDED=true` deploy test kapısını KIRIYORDU.**
`Settings` `env_file=".env"`i ÇALIŞMA DİZİNİNE göre okur; `server_deploy.sh`
testleri `/opt/tradingbot-v2` içinde koşturur → pytest CANLI `.env`'i görür.
Bayrak açıldığı an 5 test kırmızıya döner, `pytest -x` durur ve deploy KODU
GERİ ALIP çalışan süreci yeniden başlatır: gömülü mod açıldıktan sonra hiçbir
değişiklik canlıya giremezdi. *Düzeltme:* `tests/conftest.py`'de autouse
izolasyon — süreç ortamındaki `FOLLOWER_*` değişkenleri silinir ve `settings`
tekilinde gömülü mod KAPALI sabitlenir; pozitif testler onu kendi içinde açar.
*Kanıt:* `.env`'e `FOLLOWER_EMBEDDED=true FOLLOWER_SYMBOLS=TUTUSDT` yazılıp tüm
paket koşuldu — YEŞİL; izolasyon fixture'ı `git stash` ile kaldırıldığında
5 KIRMIZI.

**YÜKSEK-4 — Gömülü takipçi restart'ta KALICI entry-halt'a düşüyordu.**
Lifespan takipçiyi scalper'dan sonra başlatıyordu; scalper kurtardığı sembolleri
zaten rezerve ettiği için takipçinin sahiplik döngüsü ÇAKIŞIYOR ve
`state/follower_entry_halt.json` yazılıyordu — hesapta bir açık pozisyon varsa
DETERMİNİSTİK. `/health` bunu yakalamıyordu (`core_healthy` kasten takipçiden
etkilenmiyor). *Düzeltme:* KRİTİK-1 ile kök neden kalktı; ayrıca sahiplik
döngüsü `break` yerine `continue` ile TÜM sembolleri dener ve çakışmada artık
KALICI DOSYA YAZMAZ — RAM'de girişleri kapatır + CRITICAL loglar.

**YÜKSEK-5 — Yetim tanımı paylaşılan hesapta YANLIŞTI.**
Gömülü modda Telegram orchestrator'ın, elle açılmış bir pozisyonun ya da
scalper'ın uçuştaki girişinin "takipçinin izlemediği" olması MEŞRUDUR; eski
tanım her birini kalıcı entry-halt'a çeviriyordu (üstelik takipçi
`orchestrator.start()`'tan ÖNCE başlıyordu → her restart). *Düzeltme (kullanıcı
kararı):* gömülü modda entry-halt YOK, flatten YOK — WARNING + sayaç
(`unknown_position`) + `/follower/status → unknown_positions` + panoda
"SAHİPSİZ POZİSYON" satırı. **Ayrı halka (`BOT_MODE=follower`) D20a davranışını
AYNEN korur.** Takipçi artık `orchestrator.start()`'tan SONRA başlar.

**YÜKSEK-6 — `/risk-event flatten` hesabın TAMAMINI kapatabiliyordu.**
`_flatten_orphans` rezervasyonlara hiç bakmıyordu: gömülü modda operatörün
"takipçiyi düzleştir" komutu Telegram'ın VIP pozisyonlarını da kapatırdı.
*Düzeltme:* yabancı sahipli + sahipsiz semboller KORUNUR, atlananlar CRITICAL
loglanır ("hesap TAMAMEN düz DEĞİLDİR").

**YÜKSEK-7 — Kapasite asimetrikti.** Ayrıntı yukarıda §4'te.
*Düzeltme:* `symbol_reservations.reserve(..., capacity_owners=…)` (varsayılan
`None` = bugünkü hesap-geneli davranış birebir); scalper takipçiyi saymaz,
takipçi girişte kendi tavanına atomik olarak takılır.

**YÜKSEK-8 — Takipçi evreni dışındaki AlgoPro alarmları YUTULUYORDU.**
`FOLLOWER_SYMBOLS=<tek coin>` iken diğer 7 sembolün AlgoPro girişleri
takipçiye yönlenip "evrende değil" ile 200 alıyor, ana botun sağlamasına da
hiç ulaşmıyordu — sinyal TAMAMEN kayboluyordu ve hiçbir sayaçta görünmüyordu.
*Düzeltme (kullanıcı kararı):* giriş/oy yoluna DÜŞMEZ (ana bot değişmez) ama
SESSİZ de kalmaz: 200 `{routed:"follower", accepted:false,
reason:"symbol_not_in_follower_universe"}` + WARNING +
`reject_counters.symbol_not_in_follower_universe`. ÇIKIŞ/HIT olayları evren
kapısına TAKILMAZ (D20a bulgu 9 ilkesi).

**YÜKSEK-9 — Gömülü mod, çalışan ayrı halkayı sessizce alarmsız bırakıyordu.**
*Düzeltme:* `FOLLOWER_FORWARD_URL` doluyken startup CRITICAL uyarısı +
`/follower/status → forward_bridge_conflict` bayrağı + panoda uyarı satırı +
RUNBOOK'a "adım 0: ayrı halkayı flatten et, durdur, köprüyü boşalt" ve simetrik
geri dönüş adımı.

**YÜKSEK-10 — Gömülü takipçinin entry-halt kilidi deploy'da GÖRÜNMÜYORDU.**
`RING=testnet` yalnız `state/scalper_entry_halt.json`e bakıyordu (CLAUDE.md
yasak #3 ihlali). *Düzeltme:* `server_deploy.sh` + `restart_safe.sh`,
`.env`'de `FOLLOWER_EMBEDDED=true` görünce `state/follower_entry_halt.json`i de
kontrol eder.

**YÜKSEK-11 — `FOLLOWER_VIRTUAL_CAPITAL_USDT<=0` sanal defteri SESSİZCE
kapatıyordu** (boyutlama gerçek bakiyeye düşüyor, marj hesabın %10'u oluyordu).
*Düzeltme:* gömülü modda startup HATASI.

**ORTA/DÜŞÜK bulgular (hepsi düzeltildi):** ertelenmiş kurtarma yolunda
sahiplik alınmıyordu (döngü `_attempt_recovery` İÇİNE taşındı); yetim denetimi
patladığında rezervasyon senkronu sahipliği bırakıyordu (üçüncü fail-closed
koşul); yetim tanımı yalnız rezervasyona dayanıyordu — scalper halt'ta
rezervasyonlar DONAR, gerçek yetim görünmez olurdu (yeni `foreign_tracked_cb`
ile diğer motorların GERÇEK izleme listesi birincil kaynak); yönlendirici ile
yürütücü FARKLI ayrıştırıcı kullanıyordu (dry-run "entry" derken gerçek istek
EXIT çalıştırabiliyordu → TEK `parse_follower_event`, dry-run artık
symbol/direction da raporlar); `FOLLOWER_EMBEDDED=true` + `SCALPER_ENABLED=false`
sessizce her alarmı 503'e düşürüyordu (startup HATASI);
`FOLLOWER_SL_MARGIN_PCT` bandı takipçi KAPALIYKEN de fail-fast'ti — kapalı bir
özelliğin ayarı ana süreci düşüremez (artık `follower_active` iken hata, aksi
hâlde WARNING); `validation_alias` yüzünden `follower_lev_max` alan adıyla
kurulamıyordu (`populate_by_name=True`; ayrıca `FOLLOWER_LEV_MAX` ile
`FOLLOWER_MAX_LEVERAGE` birlikte ve FARKLI verilirse artık startup HATASI —
sessiz galip yok); `FOLLOWER_SYMBOLS` scalper evrenini
TAMAMEN boşaltabiliyordu (startup HATASI); `/scalper/stats combined` ve
`/scalper/forensics/summary` AP satırlarını karıştırıyordu (varsayılan AP
HARİÇ + `?strategy=` ile erişim, panoda "TOPLAM — Scalper defteri (AP hariç)"
etiketi ve ayrı `AP` kartı); takipçinin safety turu her 2 sn'de önbeleksiz iki
DB toplaması yapıyordu (30 sn TTL + `close_seq` geçersizleştirme); AP girişleri
`logs/trades.jsonl`'e yazılmıyordu (adli defterin yarısı eksikti);
`virtual_ledger.exchange_available_usdt` ilk girişe kadar null kalıyordu;
scalper'ın sizing anlık görüntüsüne `follower_embedded` teşhis alanı eklendi
(takipçinin marjı `availableBalance` üzerinden scalper'ın tabanını küçültür —
bu ETKİ KALDIRILMADI, GÖRÜNÜR yapıldı); RUNBOOK reçetesi repo'nun güvenli
`.env` kalıbına çevrildi ve doğrulama assert'i `FOLLOWER_SYMBOLS` +
sanal sermaye değerini de doğruluyor.

**Çürütülen 6 bulgu** (kayıt için, düzeltilmedi): iki bağımsız çürütücüden
geçemedikleri için uygulanmadı; gerekçeleri inceleme çıktısındadır.

**Bağımsız doğrulama turu (iki doğrulayıcı, 2026-08-24): üç kritik sınıf
AYAKTA; 14 kalıntı bulundu ve TAMAMI düzeltildi.** Doğrulayıcılar
`recover/open_trades` filtresini (7 saldırı vektörü), günlük PnL kaynağını ve
test izolasyonunu ÇÜRÜTEMEDİ. Bulunan kalıntılar:

* **[YÜKSEK · REGRESYON] `_attempt_recovery` istisna yollarında sahiplik
  ALINMIYORDU.** `exits.recover()` bir satırı izlemeye ALIP sonraki satırda
  `UnprotectedPositionError` yükseltirse erken `return False` çalışıyor ve o
  sembol "izleniyor ama rezerve DEĞİL" kalıyordu → scalper aynı net pozisyona
  girebiliyordu (doğrulayıcı repro: `scalper TUTUSDT alabildi mi: True`).
  Yeniden deneme 30 sn'de bir; kalıcı hatada pencere SÜRESİZ. *Düzeltme:*
  sahiplik döngüsü `try/except`ten SONRA KOŞULSUZ çalışır (scalper deseni).
* **[ORTA] `_raw_env_value` `_env_file=None`'ı EZİYORDU** → sunucu `.env`'inde
  iki kaldıraç adı birden varken izole `Settings(_env_file=None)` kurulumları
  patlıyordu; yani bulgu 29'un sınıfı yeni bir kapıdan geri gelmişti.
  *Düzeltme:* çelişki kontrolü YALNIZ örneğin kendi kaynaklarına bakar
  (`__init__` bağlamında yakalanan `_env_file` + gerçek ortam değişkeni);
  `_env_file=None` → HİÇBİR dosya okunmaz. Ayrıca çelişki artık yalnız takipçi
  AKTİFKEN fail-fast'tir (kapalıyken WARNING) — kapalı bir özelliğin ayarı ANA
  süreci düşüremez.
* **[ORTA] Takipçi HALKASININ deploy test kapısı kendi `.env`'iyle
  kırılıyordu** (`BOT_MODE=follower` → lifespan erken dal → 2 kırmızı).
  *Düzeltme:* conftest izolasyonu artık `BOT_MODE`u da scrub eder ve
  `follower_*` alanlarının TAMAMINI (4 değil) model alanlarından TÜRETEREK
  sabitler — ileride eklenen bir ayar otomatik kapsanır.
* **[ORTA] "FOLLOWER_SYMBOLS evreni boşaltırsa startup hatası" koruması CANLIDA
  ÖLÜYDÜ:** kontrol `SCALPER_SYMBOL_ALLOWLIST`e bakıyordu, canlı `.env`'de o
  satır YOK ve gerçek evren `scanner.get_universe()`ten geliyor. *Düzeltme:*
  motor `start()`'ında GERÇEK evrenle doğrulanır (boşalma → startup HATASI);
  evren o an okunamazsa kontrol ATLANIR + WARNING (ban/ağ kesintisinde botu
  başlatmamazlık etmek 2026-08-12 dersine aykırıdır) ve tarama turunda
  `scan_status=degraded:universe_empty` ile görünür kalır.
* **[ORTA] Evren dışı AlgoPro alarmları PANODA görünmüyordu:** ret yalnız
  `reject_counters`a yazılıyordu; pano "Alarm olayı"/"Son olay" satırları
  `event_counters`/`last_event_at`ten beslenir. *Düzeltme:* köprü reddi artık
  olay sayacına + olay geçmişine de işlenir ve panoda "Evren dışı alarm"
  sayacı basılır. Ayrıca **iki yol TEK ret adı** kullanır
  (`symbol_not_in_follower_universe`; insan-okur metin `detail` alanında).
* **[ORTA] Sınıflandırma kapıyla bire bir DEĞİLDİ:** `parse_follower_event`
  gövdenin HERHANGİ bir yerindeki `kind=` belirtecinde şablon yolunu seçiyordu;
  köprünün katı tanıyıcısı "entry" derken yürütme EXIT olabilirdi (pozisyonu
  kapatırdı). *Düzeltme:* katı AlgoPro biçimi ÖNCE denenir; şablon yolu yalnız
  katı biçim tutmadığında çalışır.
* **[DÜŞÜK] `unknown_position` sayacı TUR başına (2 sn) artıyordu** → olay
  başına (sembol kümesi değişiminde) artar.
* **[DÜŞÜK] `/risk-event resume` takipçiyi kontrol etmiyordu** (halt dalında
  vardı): takipçinin halt dosyası silinemezse yanıt `ok:true` diyordu. Artık
  `ok` iki motoru da kapsar + CRITICAL log.
* **[DÜŞÜK] `ScalpExecutor._recover_pending_locked` filtresizdi** → AP satırları
  scalper'ın maker journal uzlaşmasında "bu sembol DB'de OPEN" sayılıyordu.
  Artık `exclude_strategies=("AP",)`.
* **[DÜŞÜK] Scalper'ın kurtarma-çakışma yolu asimetrikti** (hâlâ `break` +
  KALICI halt): gömülü modda sahibi TAKİPÇİ olan sembol artık atlanır +
  WARNING (`continue`); diğer tüm sahipler ve ayrı halka AYNEN eski davranışta.
* **[DÜŞÜK] `STRAT_LABELS.AP` ölü koddu** — AP'nin işlem sayısı/winrate/PF
  panoda hiç görünmüyordu. Artık `/scalper/stats.strategies.AP` verisi kendi
  strateji kartında render edilir (TOPLAM'a girmez).
* **[DÜŞÜK] "SAHİPSİZ POZİSYON" satırı else-if zincirinde kayboluyordu**
  (`entry_halted`/`kill_switch`/`orphan_positions` aktifken) → BAĞIMSIZ satır.
* **[DÜŞÜK] Gömülü moddan `FOLLOWER_EMBEDDED=false`'a dönüş + DB'de OPEN AP
  satırı = pozisyon HİÇBİR motor tarafından yönetilmez.** *Düzeltme:* startup'ta
  CRITICAL log + `/health → follower="disabled_with_open_trades"` (hard fail
  YOK — 418/ban kuralı) + RUNBOOK "Gömülü takipçiyi kapatma" reçetesi.
* **[DÜŞÜK] Gün-başı sermaye yaklaşıklığı iki defteri karıştırıyordu:**
  `balance − pnl`'de `balance` PAYLAŞILAN cüzdan, `pnl` yalnız scalper defteri.
  Gömülü mod + gerçek cüzdan (sanal kasa kapalı) hâlinde AP'nin bugünkü defter
  PnL'i de düşülür.

**Kanıt:** `python3 -m pytest tests -q` → **1960 passed, 1 skipped**
(D20b öncesi: 1844; ilk uygulama 1899; düşmanca inceleme düzeltmeleriyle
**+116 test toplam**, çekirdeği `tests/test_follower_embedded.py` = 113 test).
**BEŞ ortam varyantında da YEŞİL** (hepsi bu dalda koşuldu, her biri
1960 passed / 1 skipped):
1. temiz `.env` (`cp env.example .env`);
2. `.env` içinde `FOLLOWER_EMBEDDED=true` + `FOLLOWER_SYMBOLS=TUTUSDT`
   (sunucudaki gömülü kurulumun birebir taklidi — KRİTİK-3'ün kapısı);
3. `.env` içinde `BOT_MODE=follower` + `RISK_EVENT_SECRET=…` (TAKİPÇİ
   HALKASININ kendi `.env`'i — deploy testleri o dizinde koşar);
4. sunucu env'i `SCALPER_MARKET_GATE=true
   SCALPER_MARKET_DATA_BASE_URL=https://fapi.binance.com`;
5. `.env` içinde `FOLLOWER_LEV_MAX=50` + `FOLLOWER_MAX_LEVERAGE=25`
   (çelişen eş adlar; takipçi kapalıyken WARNING, aktifken startup HATASI).

Kırmızı doğrulaması: ilk uygulamanın testleri `git stash push -- src static
scripts` ile **45 failed / 3 passed / 7 errors**; düşmanca inceleme
düzeltmelerinin testleri aynı yöntemle **32 failed / 107 passed** (yeni
`tests/test_deploy_scripts.py` vakaları dahil); doğrulama turunun testleri aynı
yöntemle **21 failed / 92 passed**; test izolasyonu fixture'ı tek başına
kaldırıldığında **5 failed** (gömülü `.env` ile) ve **2 failed**
(`BOT_MODE=follower` `.env` ile — takipçi halkasının deploy kapısı).

Kapsam: ayar katmanı (varsayılan kapalı, evren/dışlama, mod çelişkisi, mainnet
yasağı, SL_MARGIN_PCT alias/band/çelişki + koşullu fail-fast, MAX_LEVERAGE eş
adı + `populate_by_name`, sanal sermaye>0, SCALPER_ENABLED, evren boşaltma),
sanal defter (DB'den equity, kayıp, yetersiz bakiye, fail-closed, kapalı
defter, risk equity, günlük PnL kaynağı, kesici eşiği, 30 sn önbellek +
`close_seq` geçersizleştirme), defter ayrışması (kurtarma filtresi + parite +
ikinci savunma + ertelenmiş kurtarma + çakışmada kalıcı dosya YAZILMAMASI,
günlük PnL kaynağı üç yönde), sembol sahipliği (iki yön, bırakma,
halt/uçuş/denetim-hatası koruması, kapasite iki yönde), yetim/sahipsiz
ayrımı (gömülü vs ayrı halka, diğer motorun gerçek izleme listesi, flatten
koruması), `FOLLOWER_SYMBOLS` (evren + scalper dışlama + TV oyu reddi +
takipçi reddi), köprü (AlgoPro→takipçi, HIT, eski biçim→oy, kapalı bayrak,
dry-run == gerçek yol, evren kapısı, 403, 503), pano/rapor (kart alanları,
bellekten ROI, `/api/status` bloğu, `/follower/status`, HTML işaretleri,
`combined` kapsamı, forensics filtresi), `/risk-event` motor listesi, deploy
ön kontrolleri (gömülü halt dosyası: deploy + restart) ve `ledger_report`
strateji bölümü. **Backtest harness'e DOKUNULMADI** (takipçi strateji C'yi hiç
kullanmaz — CLAUDE.md kural 2 kapsamı dışında).

**Değişen varsayılanlar:** `FOLLOWER_DAILY_LOSS_LIMIT_PCT` 15 → **10**
(muhafazakâr yön). Yeni ayarlar: `FOLLOWER_EMBEDDED=false`,
`FOLLOWER_VIRTUAL_CAPITAL_USDT=1000` (yalnız gömülü modda uygulanır),
`FOLLOWER_SYMBOLS=` (boş), `FOLLOWER_SL_MARGIN_PCT=30`. Diğer tüm
`FOLLOWER_*`/`SCALPER_*` varsayılanları AYNI.

Düşmanca inceleme sonrası eklenen **fail-fast'ler** (hepsi YALNIZ
`FOLLOWER_EMBEDDED=true` iken): `FOLLOWER_VIRTUAL_CAPITAL_USDT<=0`,
`SCALPER_ENABLED=false`, `FOLLOWER_SYMBOLS` scalper evrenini tamamen
boşaltıyorsa → startup HATASI. `FOLLOWER_FORWARD_URL` doluysa → startup
CRITICAL (ayrı halka alarmsız kalır). `FOLLOWER_SL_MARGIN_PCT` bandı artık
takipçi KAPALIYKEN yalnız WARNING üretir.

⚠️ **Mevcut `.env` uyarısı:** 10–50 bandı `FOLLOWER_SL_ROI_TARGET` üzerinden de
zorlanır. Ayrı halkanın `.env`'inde bant DIŞINDA bir değer varsa (ör. 60) süreç
artık BAŞLAMAZ — deploy öncesi
`ssh awa grep '^FOLLOWER_SL_' /opt/tradingbot-ap/.env` ile bak. Bant, likidasyon
kapısının (`lev × sl_pct ≤ FOLLOWER_LEV_LIQ_GUARD_PCT`=50) zaten dayattığı üst
sınırla aynı mertebededir; 50 üstü bir pay sessizce kırpılıyordu.

**Doğrulanamadı (dürüst kayıt):** (i) **Sunucuda çalıştırılmadı** — bu dalda
deploy YAPILMADI, `.env`'e/canlıya dokunulmadı; gömülü modun canlı davranışı
(iki motorun aynı hesapta REST ağırlığı, marj rekabeti, gerçek çakışma sıklığı)
ÖLÇÜLMEDİ. (i-b) Gömülü modda günlük kesici artık **yalnız KAPANAN işlemleri**
sayar; kısmi TP dolumlarının gün içi etkisi (eşiğin ne kadar geç tetikleneceği)
ölçülmedi — bu, income kaynağının önbellek kirliliğine karşı bilinçli takastır.
(i-c) Hesapta artık `SCALPER_MAX_POSITIONS + FOLLOWER_MAX_POSITIONS` pozisyon
olabilir; toplam marj baskısının canlı etkisi ölçülmedi.

**(i-d) SINIRLILIK — gömülü kesici AÇIK pozisyonun funding/komisyonunu GÖRMEZ.**
Defter yalnız KAPANAN işlemleri sayar; `FUNDING_FEE` ve açık pozisyonun
komisyonu günlük eşiğe girmez. Doğrulayıcı ÖLÇÜMÜ: aynı gün defter −50 iken
hesap income −380 (FUNDING_FEE −300, COMMISSION −30). Yön **fail-open**'dır
(eşik hesabın gerçeğinden GEVŞEK). Bu, income kaynağının önbellek kirliliğine
karşı bilinçli takastır — ama sessiz kalmaması için hesabın ham günlük income'ı
`/scalper/status → daily_income_account` alanında BİLGİ AMAÇLI raporlanır
(kesici bu değeri KULLANMAZ, davranış değişikliği YOKTUR). Operatör iki sayının
farkını görebilir.

**(i-e) SINIRLILIK — restart, GERÇEK yetimin "benim" işaretini kaybeder.**
`symbol_reservations` süreç-içi bir kayıttır; restart'ta takipçinin sahiplik
işareti YALNIZ `strategy='AP'` OPEN DB satırlarından yeniden kurulur. Klasik
yetim (dolum oldu, `record_open` PATLADI → DB satırı YOK) restart sonrası
rezervasyonsuzdur ve §5'teki sınıflandırmada "SAHİPSİZ" görünür: kalıcı
entry-halt yerine yalnız WARNING alır ve `flatten` kapsamına girmez.
GELECEK ÇÖZÜM (uygulanmadı): scalper'ın maker journal'ı gibi kalıcı bir
"açılan pozisyon" günlüğü — dolum ile `record_open` arasındaki pencereyi diske
yazar ve restart'ta sahiplik işaretini geri getirir. (ii) `⚪ EXIT` gövdesi TV'de HÂLÂ ölçülmedi (D20a ile aynı kayıt).
(iii) Takipçinin kenarı YOKTUR: bu halka hâlâ bir hipotez testidir; hakem canlı
defterdir (`scripts/ledger_report.py --strategy AP`). (iv) Gerçek bakiye
yetersizliği kapısının canlı sıklığı (scalper'ın marjı hesabı ne sıklıkla
doldurur) ölçülmedi — sayaç `insufficient_balance` ile görünür olacak.
(v) AP FUNDING_FEE kalıntısının scalper eşiğine etkisi ölçülmedi (yukarıda
madde 3'teki dürüst sınır).

**Geri alma:** `.env`'de `FOLLOWER_EMBEDDED=false` + `scripts/restart_safe.sh
testnet` → takipçi hiç başlamaz, `/tv-signal` bugünkü yolunu izler, pano kartı
kaybolur, scalper evreni tam listeye döner. AÇIK AP pozisyonu varsa ÖNCE
`POST /risk-event {"action":"flatten"}` ile düzleştir (bayrağı kapatmak açık
pozisyonu KAPATMAZ, yalnız yöneticisini ortadan kaldırır). Kod geri alması:
bu commit'teki `src/core/config.py` (`FOLLOWER_EMBEDDED`/`_VIRTUAL_CAPITAL_USDT`/
`_SYMBOLS`/`_SL_MARGIN_PCT` + doğrulayıcılar), `src/main.py` (lifespan dalı,
`_maybe_route_embedded_follower`, `_risk_engines`, `/api/status` + `/health`
blokları, `/follower/status` kapısı), `src/strategies/follower/{engine,executor,
plan}.py`, `src/strategies/scalper/{engine,executor,tracker,types}.py`,
`static/dashboard.html`, `scripts/ledger_report.py` değişikliklerini revert et.

### D17 — Piyasa verisi ayrı host: `SCALPER_MARKET_DATA_BASE_URL` · 2026-08-23 · **ADAY, VARSAYILAN KAPALI** (canlıda uygulanmadı)
**UYGULANDI (testnet, 2026-08-23 13:17 UTC, kullanıcı yetkisi):** deploy ce29e2f (1625 test) → `.env` `SCALPER_MARKET_DATA_BASE_URL=https://fapi.binance.com` (yedek `env.bak-20260823-131644-klinesrc`) → `scripts/restart_safe.sh testnet` (pid 3520054, sağlık 60 sn) → `/scalper/status`: `kline_source=separate`, guard `fapi.binance.com` ağırlık 15/dk, `scan_status=ok`, `trailing_skips=0`; lider kapısı `leader_source_host=fapi.binance.com`; log `📡 Kline kaynağı: fapi.binance.com (AYRI — emirler: testnet…)`. Geri alma: RUNBOOK "Kline kaynağını mainnet'e alma" kapatma komutu.
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
   İlk düzeltme `_to_trading_price_space` ile GİRİŞTE ölçülen farkı öteliyordu; **ikinci tur
   incelemesi bunu yetersiz buldu ve DİNAMİK baza çevirdi** — aşağıya bak (D17-R2 #1/#2).
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

**Kanıt:** `tests/test_market_data_source.py` — **109 test** (ilk tur 65, ikinci tur +44): ayar/doğrulama (https zorunlu,
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
status'ta görünmesi). **İkinci tur (+44 test):** dinamik baz (giriş fiyatlarından BAĞIMSIZ →
`recover()` no-op'u kapandı, bayat/absürt bazda tur atlanır), koruma-tarafı kapısı (LONG/SHORT
simetrik, pay), aynı-host'ta BİREBİR aynı stop (uçtan uca `_update_trailing`), atlama
sayaçları + oran-sınırlı uyarı, hata kapsamı (401/403/451 → host, 400/404 → sembol, tükenmiş
5xx → host, `Retry-After`), soft 429 (süre + deploy kalıbına UYMAYAN log, hard ban'ın aynen
kilitlemesi), `/tv-signal`'in sembol kapsamlı hatada da yapısal ret dönmesi,
`scan_status=degraded:market_data` muhasebesi, kayan pencerenin tumbling OLMADIĞI, `1m` TTL
profili, batch bütçe/aralık gevşemesi (ban korumasının moddan bağımsızlığı), SPOT testnet'in
allowlist'ten çıkması, `ring_env_diff.sh` IP maskesi, deploy ban penceresinin yerel saat
kullanması.
`python3 -m pytest tests -q` → **741 passed, 1 skipped** (önceki taban: 676 passed, 1 skipped);
ikinci tur incelemesinden sonra **785 passed, 1 skipped**.
Backtest ÇALIŞTIRILMADI ve P2 kuralı bu adaya doğrudan UYGULANAMAZ: harness'ın "testnet
mumu" modu yoktur, yani "kapalı vs açık" farkı simüle edilemez — bu bir strateji parametresi
değil, veri KAYNAĞI değişikliğidir. Terfi yolu: (a) tek sembolde mum sapmasını yeniden ölç,
(b) `SCALPER_SHADOW_MODE` ile ya da yürüyen soak BİTTİKTEN sonra testnet'te ≥5 gün
(değişiklikler üst üste bindirilmez — soak kirlenir). ⚠️ Bu metin önce "D6+D16 soak" diyordu;
**D16 risk paketi 2026-08-23 03:10 sunucu saatinde kullanıcı kararıyla GERİ ALINDI**, yani
bugün geçerli demet yalnız **D6**'dır. (c) bir tarama turu boyunca
`X-MBX-USED-WEIGHT-1M`'i GERÇEKTEN oku (telemetri artık var) ve 600'lük bütçeyi ölçüme göre
kalibre et — CANLI profil (1m/5m/15m) için hesap 140-180 ağırlık/dk'dır, (d) insan onayı.

**İkinci tur düşmanca inceleme (19 ajan, 2 tur) — hepsi bu commit'te düzeltildi (D17-R2):**
1. **Fiyat-uzayı bazı YALNIZ giriş anında ölçülüyordu (HIGH).** `position.entry_price −
   signal.entry_price` pozisyon ömrü boyunca SABİT uygulanıyordu; iki defter arasındaki baz ise
   saatler içinde kayar. Koruma-tarafı kapısı da yoktu → baz kayınca chandelier stop'u işlem
   host'unda piyasanın YANLIŞ tarafına gönderilir, Binance -2021 verir ve
   `position_manager._emergency_close` kârlı koşucuyu PİYASA emriyle kapatır (log "eski SL
   korunuyor" derken kayıt TRAIL etiketlenir).
2. **`recover()` düzeltmeyi sessizce no-op yapıyordu (HIGH).** `_recover_one` hem
   `signal.entry_price`'ı hem `position.entry_price`'ı AYNI değerden (`trade.entry_price`)
   kuruyor — DB'de sinyal-anı fiyatı kolonu yok — yani restart sonrası baz **0** çıkıyor ve
   çeviri hiç uygulanmıyordu.
   **Tek tasarım düzeltmesi ikisini birlikte kapatır:** baz artık DİNAMİKTİR —
   `baz = işlem_host_güncel_fiyat − veri_host_son_kapanış`, her çıkış turunda yeniden ölçülür
   (`sp.position.current_price` `_step_one`'da işlem host'undan tazelenir; veri referansı
   chandelier'ı besleyen serinin son KAPANMIŞ mumudur). Ölçülemezse (bayat işlem fiyatı,
   |baz| > %2) çeviri `None` döner ve TUR ATLANIR. Ardından **koruma-tarafı kapısı**: LONG stop
   güncel fiyatın %0.05 altında (SHORT: üstünde) değilse emir HİÇ gönderilmez (eski SL kalır +
   oran-sınırlı WARNING + `trailing_skips` sayacı). BE tabanı (`floor`) işlem uzayında kalır.
   Restart'ta ek alan/migrasyon GEREKMEZ (dinamik). Aynı host'ta (varsayılan) ikisi de NO-OP:
   test `TestTrailingRoundIntegration::test_same_host_stop_is_byte_for_byte_unchanged` gönderilen
   stop'un çeviri/kapı eklenmeden önceki değerle BİREBİR aynı olduğunu kilitler.
   `executor._delay_adjusted_stop` ile DESEN aynıdır ama referansları farklıdır (tek seferlik
   gecikme telafisi + giriş fiyatına göre kapı ↔ sürekli borsa-arası baz + güncel fiyata göre
   kapı); docstring bunu artık doğru anlatıyor.
3. **Host geneli 4xx SEMBOL bazlı sayılıyordu (HIGH).** 401/403 (kimlik/WAF) ve 451 (coğrafi
   engel) `MarketDataRequestError` üretiyordu: 12 sembolün 12'si de aynı yanıtı alıyor, tur
   kesilmiyor, kesici kurulmuyor, deploy ban kilidi kör kalıyordu. Düzeltme: yalnız **400/404**
   sembol kapsamlı; 401/403/451 ve diğer 4xx'ler yeni `MarketDataHostError`
   (`MarketDataUnavailable` alt sınıfı) + kısa kesici; tükenmiş 5xx denemeleri de host geneli
   sayılır. `Retry-After` başlığı okunur, `X-MBX-USED-WEIGHT-1M` yorumlanır.
   ⚠️ **Bilinen ödünç:** tek bir sembol İNATLA 5xx döndürürse tur her seferinde o sembolde
   kesilir ve allowlist'te ondan SONRAKİ semboller taranmaz (önce yalnız o sembol atlanırdı).
   Binance 5xx'i tanım gereği sunucu tarafıdır ve tek sembole özgü kalıcı 5xx gerçekçi değildir;
   ayrıca bu durum artık SESSİZ değil: `scan_status=degraded:market_data` + sayaç + oran-sınırlı
   uyarı. Kalıcı sembol hataları Binance'te 400/`-1121` gelir ve SEMBOL kapsamlı kalmıştır.
4. **Tek bir 429 küresel kesici + deploy kilidi doğuruyordu (MED).** Ayar BOŞKEN (kline'lar
   işlem host'undan) tek bir "yavaşla" yanıtı 90-180 sn sinyal üretimini durduruyor ve
   `server_deploy.sh`'nin `HTTP 418|banned` kilidini 15 dk kapatıyordu. Düzeltme: 429 tek
   başına **soft throttle**'dır — süre `Retry-After` → ağırlık başlığı (sınır aşıldıysa pencere
   sonu) → 30 sn; log satırı "banned"/"HTTP 418" İÇERMEZ. Gerçek ban (418 / `-1003` /
   "banned until") aynen hard kalır; `MarketDataGuard` artık `hard_ban` bayrağını taşır ve
   `/scalper/status.market_data_guard`'ta gösterir.
5. **`/tv-signal` hâlâ 500 üretebiliyordu (MED).** `external_signal` yalnız
   `MarketDataUnavailable`'ı yakalıyordu; `MarketDataRequestError` (sembol veri host'unda yok —
   ayrı host'ta GERÇEKÇİ) FastAPI'ye sızıyordu. Düzeltme: yapısal ret + log.
6. **RUNBOOK doğrulaması olmayan bir dizeyi arıyordu (MED).** İlk-saat kontrolü
   `Kline çekme hatası` diyordu; bilinmeyen-sembol yolu `Kline çekme kalıcı hata` basar.
   RUNBOOK adımları koddan doğrulanan dizelerle yeniden yazıldı (banner satırı,
   `/scalper/status.kline_source`+`scan_status`+`market_data_guard`, `.env` satır grep'i).
7. **Kesilen tarama turu "başarılı" sayılıyordu (MED).** `success_count` artıyor,
   `consecutive_errors` sıfırlanıyor, `last_scan_at` tazeleniyordu → sağlık YEŞİL, tek iz bir
   log satırı. Düzeltme: ayrı sayaç + `scan_status="degraded:market_data"` + oran-sınırlı (60 sn)
   uyarı. Freshness alanları BİLİNÇLİ tazelenmeye devam eder (ban sırasında unhealthy göstermek
   watchdog restart'ını davet eder — 2026-08-14 felaket yolu). Motorda Telegram istemcisi
   olmadığı için uyarı log tarafındadır.
8. **Ağırlık penceresi "kayan" belgelenmiş, "tumbling" kodlanmıştı (MED).** Sabit sınırda sayaç
   sıfırlandığı için 60 sn'lik herhangi bir kayan aralığa bütçenin İKİ KATI sığabiliyordu.
   Düzeltme: gerçek kayan pencere (deque, `prune`/`add`/`seconds_until_free`).
9. **`_TTL_BY_INTERVAL`'de `1m` yoktu (MED).** CANLI profil `SCALPER_TF_ENTRY=1m`'dir; giriş
   dilimi `_DEFAULT_TTL`=60 sn'ye düşüyor, yani trailing/giriş TAM BİR MUM bayat veriyle karar
   veriyordu. Düzeltme: `1m → 5 sn`. Ağırlık hesabı canlı profile göre yeniden yapıldı
   (8 sembol + 3 pozisyon → 70 istek/dk ≈ **140 ağırlık/dk**; TOP_N=12 → 180) —
   `docs/ARCHITECTURE.md` §2.
10. **Harness `batch` modu ~3× yavaşlıyordu (MED).** `limit=1500` sayfaları ağırlık 10 eder;
    canlı 600/dk tavanı araştırma koşusunu pencere beklemeleriyle uzatıyordu. Düzeltme: batch
    profili (bütçe 1200/dk, aralık 0.05 sn) + sonsuz döngü kalkanı. Ban koruması moddan
    BAĞIMSIZ. Golden backtest testleri ağsızdır, süreleri değişmedi.
11. **`testnet.binance.vision` allowlist'teydi (MED).** Orası Binance SPOT testnet'idir,
    `/fapi/...` sunmaz: ayar kabul edilir, her kline isteği 404 alır ve operatör "URL geçerli"
    diye çalıştığını sanardı. Demetten çıkarıldı.
12. **`ring_env_diff.sh` `BINANCE_BIND_IP` değerini basıyordu (MED).** Ban/ağırlık muhasebesi IP
    başınadır; değer maskelendi (`*BIND_IP*`).
13. **Deploy ban kilidi penceresi TZ ofseti kadar kayıyordu (MED).** `logs/bot.log` damgaları
    loguru `{time}` = YEREL saattir, kesim noktası ise `date -u` ile üretiliyordu: UTC+2 bir
    sunucuda pencere "15 dk" değil "2 sa 15 dk" oluyordu (ve negatif ofsette AKTİF ban
    görünmeyebilirdi — tehlikeli yön). Düzeltme: kesim noktası da yerel saatle (`date -d`),
    `date -d` yoksa fail-closed.
14. **D17 terfi yolundaki "D6+D16 soak" ifadesi (LOW, dürüstlük).** D16 GERİ ALINDI; metin
    düzeltildi (bu bölümün başı).

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
### D19 — TV olay kanalı: ÇIKIŞ + YAPI/DÖNÜŞ olayları (`kind=exit|choch|trend|tp1`) · 2026-08-23 · **GÖLGE (aktif DEĞİL)**
**CANLI (gölge, 2026-08-23 13:17 UTC):** deploy ce29e2f ile kod canlıda; `SCALPER_TV_EVENTS_MODE` varsayılan `shadow`, `TV_SOURCE_ALLOWLIST` .env'de set değil → kod varsayılanı (olay kaynakları dahil), `allowlist_ok=true`. Alarm klonlama (5 sembol × 6 koşul, gövdede `src=… kind=…`) kullanıcıya bırakıldı (TV Desktop MCP'de Klonla menü aksiyonu programatik tetiklenemedi) — INTEGRATIONS §7.2 şablonları.
> ⚠️ **Bu bölüm D19a ile GÜNCELLENDİ** (aynı gün, 14 düşmanca-inceleme düzeltmesi).
> Aşağıdaki metin ilk tasarımı anlatır; **MIXED kuralı, `be`nin zararda
> uygulanmaması, olay kaynaklarının giriş oyu verememesi ve tüketim
> imleçlerinin kalıcılığı D19a'da değişti** — çelişki görürsen D19a bağlayıcıdır.
**Ne:** TradingView'den bota bugüne kadar YALNIZ "gir" oyu geliyordu. Bu değişiklik
göstergelerin ÇIKIŞ ve YAPI bilgisini de sokar: LuxAlgo S&O `Exit Signal` ve
`Trend Catcher/Tracer Up|Down`, Price Action Concepts `Bullish/Bearish S-CHOCH`,
AlgoPro `🎯 TP1 Hit`.
- **Yönlendirme GÖVDEDEN** (`src/main.py`): JSON gövdede `src`/`source` + `kind` ALANLARI,
  düz metinde `src=<token>` / `kind=<token>` BELİRTEÇLERİ (boşluk/virgül/`|` ayırıcı,
  büyük-küçük harf duyarsız). Gerekçe: kullanıcı yeni alarmları MEVCUT alarmları
  KLONLAYARAK kuruyor — webhook URL'si (secret + eski `?src=luxso`) değişmiyor.
  Gövdedeki `src` **allowlist'teyse** `?src=`'i GEÇERSİZ KILAR; değilse WARNING +
  bugünkü davranış (`body_src_rejected: true`).
- **`kind` yoksa `entry`** → mevcut 49 alarmın davranışı BİREBİR korunur
  (`tests/test_tv_signal_bridge.py` DEĞİŞMEDEN geçiyor). Tanınmayan `kind`
  **422 ile reddedilir**, "entry"ye DÜŞÜRÜLMEZ: bir çıkış alarmının yazım hatası
  yüzünden pozisyon açması kabul edilemez (bilinçli olarak `?src=` allowlist'inin
  "reddetme, tv'ye eşle" davranışının tersi).
- **`kind != entry` sağlamaya (TvConfluence) HİÇ girmez**, `external_signal` çağrılmaz;
  `src/services/tv_events.py` defterine yazılır (RAM + `state/tv_events.json`, atomik;
  bozuk dosya = boş durum + WARNING).
- **Yön semantiği iki farklı şey:** `choch`/`trend` → yön YAPININ yönü
  (`bullish`/`up`→BULL, `bearish`/`down`→BEAR, ZORUNLU); `exit`/`tp1` → yön (varsa)
  KAPATILACAK POZİSYONUN yönü, yapı bilgisi DEĞİL. Gerçek alarm koşulları
  (`Exit Signal`, `🎯 TP1 Hit`) YÖNSÜZDÜR → yön yoksa sembolde ne varsa ona uygulanır;
  yön varsa ve uyuşmuyorsa uygulanmaz + loglanır. Yön sözlüğü up/down ile genişledi ve
  bu yüzden olay tarafında **sözcük sınırıyla** eşleşir ("up" alt-dize olarak
  "SETUP"/"SUPPORT" içinde geçer); GİRİŞ yolunun alt-dize taraması DEĞİŞMEDİ.
- **Motor etkisi üç kademeli** (`SCALPER_TV_EVENTS_MODE=off|shadow|active`,
  varsayılan **shadow**): `shadow` hiçbir emri/stopu değiştirmez, yalnız "ne olurdu"yu
  loglar ve `would_block`/`would_exit` sayaçlarını artırır.
  - Giriş kapısı (`active`): ters yapı + `SCALPER_TV_EVENTS_MAX_AGE_MIN` (240 dk) içinde
    → giriş engellenir. Kapı **rejim kapısının hemen yanında**, `_evaluate_symbol`'ün TEK
    giriş noktasında (C + TV sinyalleri aynı yerden geçer). Ret sayacı `tv_structure_gate`.
    Kapıyı besleyen kaynaklar `SCALPER_TV_EVENTS_GATE_SOURCES` (varsayılan
    `pac_choch,luxso_trend`); diğer kaynaklar yalnız telemetride görünür.
  - Çıkış tetikleyicisi (`active`, `SCALPER_TV_EVENTS_EXIT=off|be|close`, varsayılan `be`):
    `be` → `ExitManager.force_breakeven` (MEVCUT BE mekanizması: `pm.replace_stop_loss`
    boşluksuz deseni + `_is_at_least_as_protective` gevşetme yasağı; YENİ EMİR YOLU YOK,
    `tp1_done`/`trailing_active` BİLİNÇLİ olarak değiştirilmez — aksi halde pozisyon D4
    reaper muafiyetine girer ve chandelier izi TP1 dolmadan başlardı).
    `close` → reaper/risk-olayı `flatten` ile AYNI `_submit_reduce_only_market_close`
    çağrısı, AYNI `force_fresh=True` doğrulaması, AYNI tek-finalizer kilidi (`_closing`,
    D10 dersi #4), `exit_reason="TV_EVENT"` (`_close_position_market`'a `exit_reason`
    parametresi eklendi; varsayılanı `RISK_EVENT` — D10 davranışı değişmedi).
  - Bir olay **bir kez** tetikler (sembol başına olay-sırası imleci) ve **pozisyon
    açılışından ÖNCEKİ olaylar sayılmaz** — aksi halde saatler önce gelmiş bir "exit"
    alarmı yeni açılan pozisyonu doğduğu anda kapatırdı.
- **FAIL-OPEN (bilinçli):** bu bir SİNYAL kanalıdır — defter bozuk/okunamaz olsa bile
  girişler DURMAZ, pozisyon KAPANMAZ. Risk kapıları (risk-olayı halt, kill switch, entry
  latch) fail-CLOSED'dır; bu değil.
- **Telemetri:** `/scalper/status` → `tv_events` (mode/exit_action/max_age/gate_sources/
  counters/symbols; secret YOK). Log: `🧭 TV olayı: SYMBOL kind=… dir=… ← src`.
- `TV_SOURCE_ALLOWLIST` kod varsayılanına dört olay kaynağı EKLENDİ
  (`luxso_exit,luxso_trend,pac_choch,algopro_tp1`) — salt genişletme, mevcut alarmların
  davranışı değişmez. ⚠️ Sunucu `.env`'i bu değişkeni AÇIKÇA set ediyorsa varsayılan
  devreye girmez; `docs/RUNBOOK.md` "TV olay kanalı" reçetesi bunu kontrol ettiriyor.

**Neden:** D16 geri alınırken kullanıcı kararı netti — "ayarlardan ziyade doğru sinyali
bulmak veya üretmek". E8 sinyal otopsisi de aynı yere işaret etti (giriş kalitesi).
Bugüne kadar göstergelerin ÇIKIŞ bilgisi (S&O mavi X, PAC CHoCH, AlgoPro TP1) hiç
kullanılmıyordu; kanal onu ölçülebilir hale getiriyor.

**Kanıt:** `tests/test_tv_events.py` — 68 test: gövde yönlendirme (JSON/düz metin,
ayırıcılar, gömülü benzer belirteç, secret sıyırma, query-vs-gövde önceliği, allowlist
dışı gövde src, `kind` yok = mevcut davranış, tanınmayan `kind` = 422), olay alma/yapı
haritalama/tazelik/kalıcılık/bozuk dosya, gölgede motor davranışının DEĞİŞMEDİĞİ
(gerçek `_evaluate_symbol` üzerinden `try_open` çağrıldı), aktifte kapının engellemesi +
`tv_structure_gate` sayacı, BE ve reduce-only kapanış yollarının doğru çağrıları
(`forced_exit_reason="TV_EVENT"`, `force_fresh=True`), yön uyuşmazlığı, pozisyon
öncesi olay, max-age, secret'ın hiçbir log/status/yanıtta görünmediği.
`python3 -m pytest tests -q` → **744 passed, 1 skipped** (önceki taban: 676 passed,
1 skipped — `tests/test_tv_signal_bridge.py` tek satır değişmeden geçiyor).

**Backtest paritesi — bilinçli boşluk:** TV olayları geçmişte YOKTUR (alarm geçmişi
indirilemez); `backtest.py`'ye DOKUNULMADI. Bu kanal **"yalnız canlı"**dır — D10
risk-olayı kanalıyla AYNI gerekçe ve aynı kabul. Terfi hattı bu yüzden
`docs/INTEGRATIONS.md` §7.6'da yeniden yazıldı: gölge (≥5 gün) → defter ölçümü
(rejime bölünmüş) → `active` + `be` → gerekirse `close`. **Bu commit hiçbir kapıyı
aktif etmez.**

**Geri alma:** `.env`'den `SCALPER_TV_EVENTS_*`'i kaldır → varsayılan `shadow` (motor
davranışı zaten değişmez); tamamen kapatmak için `SCALPER_TV_EVENTS_MODE=off`.
Kodun tamamını geri almak gerekirse bu commit'teki `src/services/tv_events.py` (yeni),
`src/main.py` (gövde yönlendirme + `_handle_tv_event` + `/scalper/status` tv_events),
`src/core/config.py` (`scalper_tv_events_*`, `tv_events_state_path`,
`_validate_tv_events_settings`, `tv_source_allowlist` genişletmesi),
`src/strategies/scalper/engine.py` (TV olay bölümü + `_evaluate_symbol` kapısı +
`_safety_tick` çağrısı + `_close_position_market` `exit_reason` parametresi),
`src/strategies/scalper/exits.py` (`force_breakeven`) değişikliklerini revert et.

### D19a — D19 düşmanca inceleme düzeltmeleri (24 bulgu, iki tur) · 2026-08-23 · **GÖLGE (aktif DEĞİL)**
**Ne:** D19 commit'i (7178077) canlıya çıkmadan önce 19 ajanlık düşmanca bir inceleme
yapıldı; **14 gerçek kusur** (3 high, 5 medium, 6 low) bulundu ve hepsi AYNI dalda
düzeltildi. Düzeltmeler uygulandıktan sonra ikinci bir düşmanca tur daha koşuldu ve
**10 kusur daha** çıktı (aşağıdaki ikinci tablo) — birincisi, birinci turun G1
düzeltmesinin A bulgusunu geri getirmesiydi. Toplam **24 kusur**. Kanal hâlâ
`shadow` varsayılanındadır — bu commit de hiçbir kapıyı aktif etmez. Aşağıdaki
"bulgu → düzeltme → test" eşlemesi bağlayıcıdır; birini değiştiren diğerini de
değiştirir.

| # | Bulgu (mekanizma) | Düzeltme | Regresyon testi |
|---|---|---|---|
| **A** [high] | `kind` belirteci düşerse (yazım hatası, iç içe JSON, `kind:` ayracı) bir ÇIKIŞ alarmı GİRİŞ OYUNA dönüşür; üstelik gövdedeki `src` (`pac_choch` vb.) allowlist'te olduğu için **YENİ BİR SAĞLAMA KAYNAĞI** sayılır — `TV_CONFLUENCE_REQUIRED=2` iken LuxAlgo ailesi tek başına 2/2 kotayı doldurup **pozisyon açtırabilir**. | Yeni ayar `TV_EVENT_SOURCES` (vars. `luxso_exit,luxso_trend,pac_choch,algopro_tp1`): bu kümedeki bir kaynak `kind=entry` ile gelirse **422**. Kontrol ÇÖZÜLEN kaynağa değil isteğin TÜM kaynak adaylarına (gövde `src`, `?src=`) uygulanır — allowlist dışı bir ad `tv`ye eşlenip sıyrılamasın. Ayrıca ayraç `=` **VEYA** `:`; JSON'da üst düzey **ve** `data` alt nesnesi okunur. | `TestEventSourceCannotVoteEntry` (4 kaynak × 2 yol, açık `kind=entry`, allowlist düşürmesi, **`TvConfluence.vote()` HİÇ çağrılmaz**, mevcut 5 giriş kaynağının yanlış-pozitif almadığı) |
| **B** [high] | `SCALPER_TV_EVENTS_EXIT=be` **zararda** pozisyonda stop'u piyasanın TERS tarafına koyar → Binance `-2021` → `position_manager._replace_stop_loss` bunu "koruma kararı" sayıp **`_emergency_close`** çağırır (kapatma başarısızsa `UnprotectedPositionError`). Yani "yalnız stop sıkışır, geri alınabilir" sanılan ayar fiilen **piyasa emriyle kapanış**tı. | `ExitManager.breakeven_side_ok()` (True/False/None) — BE yalnız pozisyon **kârdayken** (tek yönlü `SCALPER_TV_EVENTS_BE_MARGIN_PCT`=%0.05 payıyla) uygulanır. Kontrol hem motorda hem `force_breakeven` içinde (çift kapı). Zarardaki pozisyonun kaderi yeni `SCALPER_TV_EVENTS_EXIT_LOSING=skip\|close` (vars. **skip**). `None` (fiyat/BE okunamadı) = `close` bile UYGULANMAZ. Mevcut stop BE'den iyiyse gevşetme zaten yasaktı (`_is_at_least_as_protective`, değişmedi). | `TestLosingPositionNeverBreakeven` (LONG/SHORT zarar, pay sınırı, bilinmeyen fiyat, `close` politikası, kârda hâlâ çalışır, **`pm.replace_stop_loss` ASLA çağrılmaz → -2021 yolu dışlandı**, `breakeven_side_ok` matrisi) |
| **C** [high] | TV çıkış dalında `UnprotectedPositionError` genel `except Exception`'a düşüyor, yalnız loglanıyordu — safety yolunun DİĞER her dalında entry-halt latch'ini tetikleyen olay TV dalında **sessizdi** (D10 dersi ihlali). | Ayrı `except UnprotectedPositionError` → `await self._latch_entry_halt(e, source="TV olay çıkışı")`; olay tüketilir (sonsuz yeniden deneme yok). Diğer istisnalar fail-open kalır. | `TestUnprotectedPositionLatch` (latch çağrısı + `source`, ikinci turda tekrar etmemesi, sıradan hatanın latch TETİKLEMEMESİ) |
| **D** [medium] | Tüketim imleçleri yalnız RAM'deydi, defter ise diskte: her **restart** tüketilmiş bir çıkış olayını `max_age` (240 dk) boyunca YENİDEN tetikliyordu. Ayrıca **başarısız** bir aksiyon olayı "tüketilmiş" sayıyordu ve gölge sayaçları kalıcı değildi. | İmleçler (`consumed`) ve deneme sayaçları (`attempts`) defterle AYNI dosyada, atomik (`_STATE_VERSION=2`; v1 dosyası **atılmaz**, yükseltilir). Aksiyon başarısızsa olay tüketilmez, `_MAX_EXIT_ATTEMPTS=3` denemeye kadar tekrarlanır, sonra bırakılır (pozisyon normal SL/TP korumasında kalır). Sayaçlar + `counters_since` kalıcı. | `TestPersistentConsumption` (restart'ta yeniden tetiklenmeme, 3 deneme sonra bırakma, denemenin restart'ta sürmesi, sayaç/`since` kalıcılığı, v1 yükseltmesi, `EXIT=off`'ta imlecin kalıcı ilerlemesi) |
| **E** [medium] | Sunucu `.env`'i `TV_SOURCE_ALLOWLIST`'i AÇIKÇA set ediyorsa kod varsayılanı devreye girmez → `src=pac_choch` sessizce eski `?src=luxso` etiketine düşer, kapı hiç eşleşmez: kanal **"kurulu görünüp ölü"**. | Olay yolu **allowlist'ten BAĞIMSIZ** (istek `TV_WEBHOOK_SECRET` ile kimlikli ve sağlamaya girmiyor): etiket gövdedeki değer olarak KALIR, allowlist dışıysa yalnız WARNING. `kind != entry` bir istek ASLA giriş yoluna düşmez. Startup'ta `TvEvents.log_config_health()` WARNING üretir; durum `/scalper/status` → `tv_events.allowlist_ok` / `allowlist_missing` / `gate_enabled` / `window_open`. | `TestAllowlistIndependenceAndHealth` (eksik allowlist teşhisi, varsayılanda sessizlik, `off` modda susma, gövde etiketinin korunması, dört `kind`in giriş yoluna DÜŞMEMESİ, status alanları) |
| **F** [medium] | MIXED (kapı kaynakları çelişiyor) davranışı "ters olan engeller"di — PAC BULL + S&O trend BEAR gibi bir çelişki sembolü **İKİ YÖNE DE** 240 dk kilitliyordu: hiçbir kanıt üretmeyen durum en sert kararı veriyordu. Ayrıca olay yolu `SCALPER_TV_SYMBOL_ALLOWLIST`'i (D7) tanımıyordu. | MIXED → **kapı UYGULANMAZ** (çelişki "bilinmiyor"dur, "her iki yön de yasak" değil) + `mixed_skipped` sayacı + log; telemetride `structure: MIXED` görünmeye devam eder. Olay yolu D7 sembol allowlist'ini uygular (dışındaki sembol deftere YAZILMAZ; yanıt R1-4'ten sonra **200 + `applied:false`**). | `TestMixedAndSymbolAllowlist` (MIXED girişi engellemez / çıkışı tetiklemez, telemetride görünür, biri bayatlayınca kapı geri gelir, sembol allowlist'i kabul/ret) |
| **G1** [medium] | `src=`/`kind=` gövdenin HER YERİNDE aranıyordu: TradingView'in `{{strategy.order.alert_message}}` gibi **kullanıcı metnini** gövdenin ortasına basan alanları mevcut bir alarmın kimliğini/yolunu değiştirebilirdi. | Belirteçler yalnız **başlık koşusu**ndan okunur (satır başından itibaren kesintisiz `anahtar=değer`; ilk serbest metin belirtecinde biter, ilk 5 satır). JSON'da yalnız üst düzey + `data`. İç içe JSON ve serbest metin ARANMAZ. | `TestHeaderRunScanning` (gerçek BotV3/AlgoPro/LuxAlgo gövdeleri, satır başı, koşunun bitişi, `:` ayracı, `data` sarmalayıcı, derin iç içe JSON, patolojik 8 KB gövdede doğrusal süre) |
| **G2** [medium] | Kimliksiz bir istek `kind` doğrulamasına ulaşıp 422 mesajından geçerli `kind` listesini (kanalın varlığını ve sözleşmesini) öğrenebiliyordu. | Secret doğrulaması **gövde ayrıştırmasından ve HER 422'den ÖNCE**, sabit zamanlı (`_constant_time_equals`); saf çözücüler kendi içlerinde tekrar doğrular. `POST /tv-events/reset` aynı disiplinde. | `TestSecretBeforeParsing` (403 > geçersiz `kind` 422, > sembol 422, > olay-kaynağı 422; reset 403) |
| **G3** [medium] | Olay yolu sembolü yalnız "USDT ile bitiyor mu" diye süzüyordu → defterde `"'; DROP--USDT"` gibi anahtar; defter ayrıca sınırsız büyüyebiliyordu. | `_TV_SYMBOL_RE.fullmatch` (giriş yolunun davranışı BİLİNÇLİ olarak değişmedi) + defter budaması: `_MAX_SYMBOLS=64`, `_MAX_STRUCTURE_SOURCES=16`, `_MAX_ATTEMPT_KEYS=16` (en eskiler düşer, aktif sembol korunur). | `TestEventSymbolValidationAndPruning` (bozuk sembol 422 + deftere yazılmaz, `BINANCE:ETHUSDT.P`/`1000PEPEUSDT` kabul, sembol ve kaynak sınırları) |
| **G4** [low] | S&O "Trend Catcher" ile "Trend Tracer" aynı `src` etiketini (`luxso_trend`) paylaşır — kural belirsizdi. | Durum anahtarı `src`tir → iki alt-kaynak birbirini MIXED'e DÜŞÜRMEZ, **son olay kazanır**. Yeni `via=` alt-anahtarı YALNIZ TELEMETRİDİR (yön taramasından da çıkarılır). Kural INTEGRATIONS §7.3'te yazılı. | `TestViaSubSource` (MIXED oluşmaması, `via` ayrıştırma + yön, `via` yokluğu) |
| **G5** [low] | `SCALPER_TV_EVENTS_MAX_AGE_MIN=0` "süresiz taze", boş `GATE_SOURCES` "tüm kaynaklar" gibi okunabiliyordu. | **SIFIR/BOŞ = KAPALI**: 0 → pencere kapalı, boş liste → hiçbir kaynak karar vermez. `MODE=active` + boş `GATE_SOURCES` **startup'ta ValueError** (kaynaksız kapı kesinlikle yazım hatasıdır). | `TestZeroMeansClosed` (pencere/kapı kapanışı, girişin engellenmemesi, validator: `active`+boş, geçersiz `EXIT_LOSING`, negatif `BE_MARGIN`, 0 max-age'in GEÇERLİ olması) |
| **G6** [low] | Bir turda birden çok TV kapanışı safety turunu şişirip 30 sn'lik tazelik eşiğini aşabilirdi (reaper'ın 2026-08-14 dersi). | `_TV_EXIT_MAX_ACTIONS_PER_TICK=1`; kalan olaylar **tüketilmez**, sonraki turda ele alınır. | `TestPerTickActionLimit` (turda tek aksiyon, ikinci turda diğeri, ertelenen olayın tüketilmemesi) |
| **G7** [low] | `state/tv_events.json`'u silmek ÇALIŞAN süreci temizlemez (RAM otoritedir, sonraki yazımda dosyayı geri yazar); kalıcılık hatası log seli üretebilirdi; RUNBOOK'un doğrulama adımı canlı deftere gerçek olay yazıyordu. | `POST /tv-events/reset?secret=` (RAM + disk), kalıcılık WARNING'i dakikada bir (`_PERSIST_WARN_INTERVAL_S=60`) + `/scalper/status` → `tv_events.persist{ok,errors,last_error,path}`, ve `POST /tv-signal?dry_run=1` (doğrular, DEFTERE YAZMAZ). RUNBOOK reçetesi buna göre yazıldı. | `TestResetAndPersistHealth` (reset RAM+disk, dry-run'ın defteri kirletmemesi, yazılamayan yolda tek WARNING + sayaç + fail-open, reset'in `persisted` raporu) |
| **G8** [low] | `would_block` (gölge) ile `blocked` (aktif) farklı yerlerde sayılıyordu; gölge ölçümü aktif ölçümle birebir kıyaslanamıyordu. | `gate_hits` HER İKİ modda artar → sözleşme `gate_hits == would_block + blocked`. Çıkış tarafında `exit_hits` aynı rolü oynar. | `TestCounterContract` |

**İKİNCİ TUR (2 ajanlık düşmanca inceleme, aynı gün):** ilk tur düzeltmeleri
uygulandıktan sonra kod yeniden saldırıya uğradı ve **10 kusur daha** bulundu
(2 high, 6 medium, 2 low). Hepsi bu commit'te kapalıdır:

| # | Bulgu | Düzeltme | Test |
|---|---|---|---|
| **R1-1** [high] | G1 daraltması bulgu A'yı GERİ getiriyordu: `BTCUSDT.P src=pac_choch kind=choch bearish` gibi bir gövdede belirteçler okunmaz → `kind` yokluğu "entry" → `bearish` yönü çözer → **CHoCH alarmı pozisyon açar** (uçtan uca `external_signal` çağrıldığı ölçüldü). | Gövdenin TAMAMINDA `src=` taraması (`_tv_body_event_source_mentions`) — **yalnız `tv_event_sources` içindeki adlar** guard'a EK ADAY olarak verilir. Yönlendirme DEĞİŞMEZ (G1 korunur), ama okunamayan bir olay alarmı 422 ile GÖRÜNÜR biçimde ölür ("mesajın BAŞINDA değil" ipucuyla). | `TestMidMessageTokensFailLoud` — değişmez kural: olay alarmı ya olay yoluna gider ya 422 alır, **`external_signal` ASLA çağrılmaz**; serbest metinde `kind=exit` geçen meşru AlgoPro girişi etkilenmez |
| **R1-2** [medium] | `kind`e `:` ayracı eklenmesi YENİ bir sert 422 yaratıyordu: `Kind: Bullish Reversal BTCUSDT.P` ile başlayan masum bir GİRİŞ alarmı bugün kabul edilirken 422 alırdı. | Ayraç YAKALANIR: `=` kasıtlı belirteçtir (tanınmayan değer → 422, D19 kuralı korunur); `:` düz yazı noktalamasıdır → değer TANINAN bir küme içinde değilse belirteç YOK SAYILIR. | `TestColonSeparatorIsProseSafe` (4 düz yazı biçimi yok sayılır, `Kind:` ile başlayan giriş alarmı hâlâ açar, `kind:exit` hâlâ yönlendirir, `=` hâlâ sert) |
| **R1-3** [medium] | `config.py` yorumu koduyla çelişiyordu ve `active` + boş `GATE_SOURCES` MEŞRU bir "yalnız-çıkış" yapılandırmasını başlatılamaz kılıyordu (`pending_exit` `gate_sources`a bakmaz). Ayrıca `max_age=0` aynı sessiz ölümü üretirken fail-fast DEĞİLDİ (asimetri). | Kural gerçek niyete çevrildi: **`active` iken kanal HİÇBİR ŞEY yapamıyorsa** ValueError (`can_gate or can_exit`). Yorum kodla hizalandı. | `TestZeroMeansClosed` (kapı+çıkış ölü → hata, `max_age=0` → hata, yalnız-çıkış → GEÇERLİ) |
| **R1-4** [medium] | Sembol allowlist'i olay yolunda 422, giriş yolunda 200 döndürüyordu: aynı sembolde kurulu iki alarmdan biri TV'de yeşil, diğeri kırmızı. | Olay yolu da **200 + `applied:false` + `reason:"symbol_allowlist"`**; 422 yalnız BİÇİM hataları için. | `TestMixedAndSymbolAllowlist` (200 + applied:false, defter boş, sayaç 1) |
| **R1-5** [low] | `dry_run=1`, sembol allowlist reddinde `note()` çağırdığı için CANLI deftere kalıcı bir sayaç yazıyordu (docstring'in aksine). | Sayaç `if not dry_run` altında. | `test_dry_run_does_not_count_symbol_allowlist_rejection` (sayaç sözlüğü byte-aynı) |
| **R1-9** [low] | Yön taramasında `src` sıyırması JSON gövdede ÇALIŞMIYORDU (`"src": "…"` — anahtarla ayraç arasında tırnak var); tireli bir kaynak adı (`pac-bull`, `luxso-down`) yön sanılırdı. | Regex sıyırmasına ek olarak ÇÖZÜLMÜŞ değerler (üst düzey + `data`) metinden çıkarılır. | `TestDirectionScanStripsResolvedValues` |
| **R2-1** [high] | Motor, zarar kontrolünü `force_breakeven`dan ÖNCE yapıyordu: stopu ZATEN BE'de olan (TP1 dolmuş, D4 reaper muafiyetindeki) bir koşucu, fiyat geri çekildiğinde `EXIT_LOSING=close` ile **piyasadan kapatılıyordu** (ölçüldü: 1 reduce-only MARKET). | SIRA tersine çevrildi: önce `force_breakeven` (kendi içinde `_closing` → hedef → "zaten koruyucu" → zarar kapılarını uygular ve zararda EMİR GÖNDERMEZ), sonra "neden olmadı" teşhisi. | `test_stop_already_at_breakeven_is_never_market_closed` |
| **R2-2** [medium] | `side_ok is None` (geçici ticker hatası) olayı KALICI olarak yutuyordu: fiyat geri gelip pozisyon kâra geçse bile olay bir daha değerlendirilmiyordu. | `None` → `"failed"`: olay tüketilmez, `_MAX_EXIT_ATTEMPTS` kadar yeniden denenir. | `test_unknown_price_is_treated_as_unsafe_and_retried` |
| **R2-3/4** [medium] | `exits_applied`, borsaya HİÇBİR isteğin gitmediği durumları da sayıyordu ("zararda skip" ve "stop zaten BE'de"); gölge modu ise aktifte hiçbir şey olmayacak olayı ayırt edemiyordu — G8 parite iddiası çıkış tarafında tutmuyordu. Bu sayı terfi kararının GİRDİSİ. | Üç durumlu dönüş (`applied`/`noop`/`failed`): `applied` yalnız stop gerçekten taşındıysa ya da pozisyon kapandıysa. Yeni sayaçlar `exits_noop` + `would_exit_noop`; gölge tahmini yan etkisiz `ExitManager.breakeven_would_act()` ile yapılır (`force_breakeven`ın kapılarını AYNI sırayla, hiçbir şeyi değiştirmeden uygular). | `TestShadowPredictsActive` (zararda no-op, stop zaten BE'de no-op, `close` politikasında no-op DEĞİL, kârlıda no-op değil, gölge `would_exit_noop` ↔ aktif `exits_noop`), `TestCounterAlgebra` (kimlikler) |
| **R2-5** [medium] | `position.current_price` yalnız ticker okuması BAŞARILI olduğunda yazılıyor ve zaman damgası taşımıyordu → birkaç tur hata verirse BAYAT fiyat "kârda" hükmü verip **tam da engellenmek istenen** -2021 → `_emergency_close` yolunu açıyordu (gerçek `step()` ile ölçüldü). | `ScalpPosition.price_ts` (monotonic) `step()` içinde basılır; `breakeven_side_ok` damgasız ya da `_BE_PRICE_MAX_AGE_S=30 sn`'den eski fiyatta **None** ("bilinmiyor") döner. | `test_stale_price_is_not_treated_as_profit` |
| **R2-6** [medium] | `_prune`, AÇIK POZİSYONU ve bekleyen tüketilmemiş olayı olan sembolü bir alarm selinde defterden düşürebiliyordu (80 sembollük selde BTCUSDT düştü). | `TvEvents.protect(symbols)` — motor her safety turunda `exits.tracked_symbols()`'ı bildirir; korunan semboller eviction adayı DEĞİLDİR (`_MAX_PROTECTED_SYMBOLS=32`). | `TestPruningProtectsOpenPositions` |
| **R2-7** [low] | Pencere KAPALIYKEN (`max_age=0`) imleçler ilerlemiyordu → operatör pencereyi açınca birikmiş olaylar ANINDA toplu tetikliyordu (INTEGRATIONS §7.4'ün vaadinin tersi). | `_advance_tv_seen()` kapısına `not window_open()` eklendi. | `TestClosedWindowAdvancesCursors` |
| **R2-8** [low] | `note()` her sayaç artışında tam JSON + 2 fsync yazıyordu (~2.5 ms, event-loop üzerinde senkron). | Sayaç yazımı saniyede bire debounce edildi (`_COUNTER_PERSIST_MIN_INTERVAL_S`); olay/tüketim yazımları ANINDA kalıcı kalır ve bekleyen sayaçları da diske indirir. | `TestLedgerRobustness::test_counter_writes_are_debounced` / `test_ingest_is_never_debounced` |
| **R2-9** [low] | `attempts` budaması LEKSİKOGRAFİK sıralıyordu (`"exit:10" < "exit:2"`) → EN YENİ denemenin sayacı düşebilirdi (latent). | `(grup, int(seq))` ile sayısal sıralama (`_attempt_sort_key`), `_load`'da da. | `test_attempt_keys_are_pruned_numerically` |
| **R2-10** [low] | Bozuk/eksik `structure` alanı `""` hükmü üretip `BULL\|BEAR\|MIXED\|NONE` sözleşmesini bozuyordu. | `_load` yalnız `BULL`/`BEAR` satırlarını geri yükler. | `test_corrupt_structure_row_is_dropped` |

**Kusur bulunmadığı DOĞRULANAN alanlar** (iki tur, hepsi çalıştırılarak): `_TV_HEADER_RUN_RE`
ReDoS yok (17 düşmanca 8 KB desen, azami 0.26 ms); JSON gövdede `kind:`/`src:` yanlış
eşleşmesi yok; `_tv_event_symbol` kalibrasyonu (`1000PEPEUSDT`, `BINANCE:BTCUSDT.P`
geçer; `'; DROP--USDT`, `BTC\x00USDT`, `BTCUSDT\nEVIL` reddedilir); secret disiplini
(403 her 422'den önce, sabit zamanlı, hiçbir yanıtta/logda yok); mevcut 49 giriş
alarmının gerçek gövdeleri aynen kabul; `ok`/`status` değişkeni her dalda tanımlı
(UnboundLocalError yok); `UnprotectedPositionError` latch'i tam bir kez; restart'ta
imleç davranışı; v1 durum dosyası yükseltmesi; `_MAX_EXIT_ATTEMPTS` ve kalıcı açlık
yokluğu; `gate_hits == would_block + blocked`; `breakeven_side_ok` işaret/pay mantığı
(LONG/SHORT simetrik); `force_breakeven` iç sıralaması; `_persist` atomikliği;
`luxso_trend` alt-kaynaklarının MIXED üretmemesi; giriş tarafı fail-open sözleşmesi.

**Neden bu kadar sıkı:** kanal `shadow` olsa bile kod yolu canlıda çalışır (`/tv-signal`
her istekte gövdeyi ayrıştırır). Bulgu A ve B `shadow`da bile **gerçek para** etkisi
taşıyordu: A giriş yolundadır (moddan bağımsız), B ise `active`e geçildiği ilk gün
sessizce piyasa emri gönderirdi.

**Yeni ayarlar (hepsi geriye uyumlu varsayılan):** `TV_EVENT_SOURCES`,
`SCALPER_TV_EVENTS_EXIT_LOSING=skip`, `SCALPER_TV_EVENTS_BE_MARGIN_PCT=0.05`
(`env.example` güncellendi).

**Davranış değişiklikleri (D19'a göre):** (1) olay kaynağından gelen giriş oyu 422
(gövdenin herhangi bir yerinde geçse bile); (2) `be` yalnız pozisyon kârdayken —
zararda `SCALPER_TV_EVENTS_EXIT_LOSING` karar verir, fiyat bayat/okunamazsa hiçbir
şey yapılmaz ve olay yeniden denenir; (3) MIXED artık ENGELLEMEZ (D19'da
engelliyordu); (4) olay yolu allowlist yerine TV sembol allowlist'ini uygular
(200 + `applied:false`); (5) `src`/`kind` yalnız başlık koşusundan okunur, `:`
ayracı yalnız TANINAN değerlerde sayılır; (6) tüketim kalıcı, başarısız aksiyon
tüketmez; (7) tur başına 1 çıkış aksiyonu; (8) `active` iken kanal hiçbir şey
yapamıyorsa süreç başlamaz; (9) `exits_applied` yalnız borsaya gerçekten istek
gidince artar (`exits_noop` ayrı).
Mevcut 49 GİRİŞ alarmının davranışı DEĞİŞMEDİ — `tests/test_tv_signal_bridge.py`
tek satır değişmeden geçiyor.

⚠️ **Operatör notu (alarm kurulumu):** yönlendirme belirteçleri (`src=`, `kind=`)
alarm mesajının **BAŞINDA** olmalıdır (`docs/INTEGRATIONS.md` §7.2 şablonları buna
uyar). Ortada kalırlarsa istek ya yine olay yoluna gider ya **422** alır. 422 alan
alarm TV'de "webhook failed" görünür; çözüm mesajı düzeltmektir, alarmı silmek
değil.
> ⚠️ **Bu maddedeki "hiçbir koşulda giriş oyuna dönüşmez" ifadesi 2026-08-23
> bütünleşme incelemesinde DARALTILDI:** kalkan `src=<olay kaynağı>` adına
> bağlıydı; `src=` hiç yoksa (ya da yanlış yazıldıysa) ve belirteçler başlık
> koşusu dışındaysa istek GİRİŞ oyu oluyordu (ölçüldü: 5/5 yerleşimde
> `external_signal`). Aynı commit'te ikinci bir kalkan eklendi
> (`reject_entry_vote_from_kind_mention`): gövdenin herhangi bir yerinde
> `kind[=:]<exit|choch|trend|tp1>` varsa ve gövde tanınan bir GİRİŞ biçimi
> değilse 422. Güncel ve tam kural: `docs/INTEGRATIONS.md` §7.1 (JSON'da derin
> iç içe `kind` hâlâ okunmaz ve 422 üretmez — bilinçli kapsam sınırı).

**Kanıt:** `python3 -m pytest tests -q` → **877 passed, 1 skipped**
(D19 tabanı: 744 passed, 1 skipped → +133 test; `tests/test_tv_events.py` 68 → 201).
`tests/test_tv_signal_bridge.py` TEK SATIR değişmeden geçiyor (49 alarmın regresyonu).
Ayrıca `TestRoutingInvariants` iki DEĞİŞMEZ kuralı tohumlanmış rastgele gövdelerle
tarar: (1) hiçbir GİRİŞ alarmı olay-kaynağı koruması yüzünden yanlışlıkla 422 almaz,
(2) **gövdesinde `src=<olay kaynağı>` taşıyan** hiçbir OLAY alarmı — belirteç nereye
yazılırsa yazılsın — `external_signal`'a ya da `TvConfluence.vote()`'a ULAŞMAZ.
> ⚠️ (2) 2026-08-23 bütünleşme incelemesinde DARALTILDI: özellik testi her gövdeye
> bir `src=<olay kaynağı>` koyuyordu, yani iddia yalnız o koşul altında
> ölçülmüştü. `src=` YOKKEN kural TUTMUYORDU — düzeltmesi ve yeni özellik testi
> (`test_event_alarm_without_src_never_reaches_the_entry_path`, 5 yerleşim ×
> 400 tohumlanmış gövde) aynı incelemenin commit'indedir.

**Geri alma:** D19 ile aynı — `.env`'den `SCALPER_TV_EVENTS_*` kaldır (varsayılan
`shadow`), tamamen kapatmak için `SCALPER_TV_EVENTS_MODE=off`. Kod düzeyinde geri
almak gerekirse bu commit'teki `src/main.py`, `src/core/config.py`,
`src/services/tv_events.py`, `src/strategies/scalper/engine.py`,
`src/strategies/scalper/exits.py` değişikliklerini revert et; D19'un kendisi
bağımsız olarak ayakta kalır (ama A/B/C bulguları geri gelir — **önerilmez**).

### D17-R3 / D19a-R3 — bütünleşme incelemesi: dalların KESİŞİMİNDEKİ dört kusur · 2026-08-23
**Ne:** D15/D17/D18/D19/D20 dalları ayrı ayrı incelenmişti; bu tur dalların BİRLİKTE
oluşturduğu yüzeyi (aynı endpoint'in iki yolu, aynı host'un iki fiyat türü, aynı
payload'ın iki şekli) denetledi. Dördü de DOĞRULANDI ve bu commit'te düzeltildi;
her birinin düzeltmesiz KIRMIZI olan bir regresyon testi vardır.

| # | Bulgu (mekanizma) | Düzeltme | Regresyon testi |
|---|---|---|---|
| **1** [high] | D19a'nın "hiçbir olay alarmı giriş oyuna dönüşmez" kalkanı `src=<olay kaynağı>` ADINA bağlıydı. Olay alarmının mesajında `src=` HİÇ YOKSA (ya da yanlış yazıldıysa) ve belirteçler başlık koşusu DIŞINDAysa (`BTCUSDT.P kind=choch bullish`, `Bullish S-CHOCH kind=choch BTCUSDT.P`) hiçbir şey okunmaz → `kind` yokluğu "entry" → gövdedeki `bullish` yönü çözer → istek GİRİŞ OYU olur. `TV_CONFLUENCE_REQUIRED=1` ile bu DOĞRUDAN `external_signal`dır. **Ölçüldü: 5/5 yerleşimde pozisyon açıldı.** D19a'nın özellik testi kusuru göremiyordu çünkü ürettiği HER gövdeye bir `src=<olay kaynağı>` koyuyordu. | İkinci kalkan `reject_entry_vote_from_kind_mention`: gövdenin herhangi bir yerinde `kind[=:]<exit\|choch\|trend\|tp1>` varsa **ve** gövde tanınan bir GİRİŞ biçimi DEĞİLSE → 422 "olay alarmı yanlış şablon". Tanınan giriş biçimleri: JSON giriş gövdesi (`symbol`/`side`), AlgoPro/BotV3 `\| TF:`/`\| Price:` parmak izi (kaynak tahminiyle AYNI fonksiyon), `{{ticker}} BUY\|SELL`. EVENT_KINDS dışı değerler yok sayılır. Yönlendirme DEĞİŞMEZ (G1 korunur). Yeni sayaç `rejected_entry_kind_mention`. | `TestKindMentionWithoutSourceFailsLoud` (5 yerleşim × 422 + sağlamaya oy yok + sayaç; başta duran `kind=` hâlâ OLAY yoluna gider; AlgoPro serbest metnindeki `kind=exit` hâlâ AÇAR), `TestRoutingInvariants::test_event_alarm_without_src_never_reaches_the_entry_path` (5 yerleşim × 400 tohumlanmış gövde) |
| **2** [medium] | `exits._to_trading_price_space` bazı `işlem_host_CANLI − veri_host_son_KAPANIŞ` olarak ölçüyordu. İki büyüklük AYNI TÜRDEN DEĞİLDİR: fark, borsa-arası bazın ÜSTÜNE MUM-İÇİ sürüklenmeyi bindirir. Etki sistematiktir — fiyat lehe gittikçe sürüklenme büyür, chandelier mandalı (`new_stop > current_sl`) her turda biraz daha sıkışır ve stop fiilen CANLI FİYATI izler, chandelier MESAFESİNİ değil (ters yönde koruma-tarafı kapısı turu boşa atlatır). | Baz LIKE-FOR-LIKE: `işlem_host_CANLI − veri_host_CANLI`. Veri tarafı yeni `KlineFetcher.get_price` (public `/fapi/v1/ticker/price`, tek sembol → ağırlık 1, `MarketDataGuard`'dan geçer, TTL = safety turu = 2 sn, TEKRAR YOK). Fiyat okunamazsa çeviri `None` → tur atlanır (mevcut fail-safe); host geneli kesintide turun kalanı susar. Aynı host'ta byte-for-byte no-op (fiyat HİÇ istenmez). | `TestLikeForLikeBasis` (mum-içi sürüklenme stopu SIKIŞTIRMAZ, baz iki canlı fiyatın farkıdır, okunamayan fiyat turu atlar, ban tur genelini susturur, fetcher yoksa fail-closed, log oran sınırı, engine kablolaması), `TestDataHostPriceFetch` (ağırlık 1 + guard, TTL, ban ağa çıkmaz, eksik `price` alanı 0 DEĞİL hata, tekrar yok), `test_same_host_stop_is_byte_for_byte_unchanged` (+ `data_price_calls == []`) |
| **3** [medium] | `?dry_run=1` yalnız OLAY dalına geçiriliyordu; GİRİŞ yolunda SESSİZCE yok sayılıyordu. Yani RUNBOOK'un doğrulama komutu — ki çağrılma SEBEBİ tam da "`kind=` düştü mü" sorusudur — sağlamaya GERÇEK oy yazıp `external_signal` üzerinden GERÇEK EMİR açabiliyordu; ayrıca takipçi köprüsünü tetikliyordu. | Giriş yolunda `dry_run` → oy YOK, `external_signal` YOK, takipçi köprüsü YOK (422 dalında da), motor gerekmez; yanıt `{"dry_run": true, "would": {symbol, direction, source}}`. RUNBOOK doğrulama örnekleri her iki yol için de `dry_run` ile yazıldı. | `TestDryRunHasNoSideEffects` (7 test: yanıt şekli, oy yok, motor gerekmez, kaynak raporu, 422'de köprü yok; negatif kontroller: bayraksız istek AÇAR ve köprü ÇALIŞIR) |
| **4** [low] | `/scalper/status` İKİ farklı şekil döndürüyordu: `_EMPTY_SCALPER_STATUS` (motor yokken) `market_data_guard`, `risk_event`, `tv_events`, `entry_rejects`, `stop_mode`, `symbol_reservations` anahtarlarını HİÇ taşımıyordu. Panelde "alan yok" sessizce "kanal yok" diye okunur — özellikle `market_data_guard` (ban durumu) için tehlikeli. | Eksik anahtarlar sözlüğe eklendi; dinamik olanlar (`market_data_guard`, `tv_events`, `symbol_reservations`) istek anında tazelenir. Sözleşme sözlüğün üstüne yazıldı. | `TestStatusPayloadShape` (iki payload'ın ANAHTAR KÜMESİ eşit; endpoint dinamik alanları gerçek değerle doldurur) |

**Kapak dışı, aynı commit (yalnız belge/kayıt):** `env.example`'a `SCALPER_STRUCTURE_*`
(D18, yorumlu — kanıt REDDETTİ, açmak yeni kanıt ister) ve `TV_EVENTS_STATE_PATH`;
`docs/RUNBOOK.md` "Deploy ve geri alma" ÜÇ halka tablosu + `ring_env_diff` kapsamı
(ve kapsam DIŞI anahtarlar); `docs/ARCHITECTURE.md` ağırlık bütçesi tablosuna baz
referansı satırı; `docs/INTEGRATIONS.md` §7.1 mutlak ifadesinin daraltılması + §7.2'ye
"`src=` ASLA düşürülmez" uyarısı; D19a "Kanıt"(2) ve operatör notuna çekince;
`docs/EXPERIMENTS.md` E6d/E6e satırları tablo başlığına bağlandı (biçim).

**Kanıt:** `python3 -m pytest tests -q` → **1500 passed, 1 skipped** (taban 4c227b3:
1457 passed, 1 skipped → +43 test). `tests/test_tv_signal_bridge.py` **TEK SATIR
değişmeden** geçiyor (49 giriş alarmının regresyonu). Düzeltmesiz kırmızı ölçümleri:
bulgu 1 → 12 test FAILED (5/5 yerleşimde `external_signal` çağrıldı), bulgu 2 → 6 test
FAILED, bulgu 3 → 5 test FAILED.

**Ağırlık hesabı (bulgu 2'nin bedeli, YALNIZ ayrı host'ta):** safety turu 2 sn →
30 tur/dk; TTL = tur süresi → sembol başına tur başına en fazla 1 istek × ağırlık 1 =
**30 ağırlık/dk / açık pozisyon**. `SCALPER_MAX_POSITIONS=3` → **90 ağırlık/dk**;
toplam 140 + 90 = **230 ağırlık/dk** (IP bütçesi 2400'ün ~%9.6'sı, kendi 600'lük
tavanımızın ~%38'i). Kuramsal tavan (8 sembolde de açık pozisyon) 8 × 30 = 240, trailing
mumlarıyla birlikte 480 ağırlık/dk — bugün `scalper_max_positions=3` yüzünden
ULAŞILAMAZ. Ayrıntı: `docs/ARCHITECTURE.md` §"Kline ağırlık bütçesi".

**DOĞRULANAMADI (kod okumasıyla kapatılmadı):** hiçbir ölçüm canlıda yapılmadı —
`SCALPER_MARKET_DATA_BASE_URL` canlıda BOŞ olduğu için bulgu 2'nin düzeltmesi
testnet'te henüz hiç çalışmadı; ticker ağırlığı HESAPtır, `X-MBX-USED-WEIGHT-1M`
ölçümü yapılmadı. Bulgu 1'in kapsam sınırı bilinçlidir: JSON gövdede `"kind": "choch"`
biçimi (anahtarla ayraç arasında tırnak) hiçbir taramaya takılmaz, yani `data`'dan
DAHA DERİN bir JSON `kind` bugün de okunmaz ve 422 üretmez — bu D19a G1'in bilerek
bıraktığı kör noktadır ve DEĞİŞTİRİLMEDİ.

**Geri alma:** bulgu 1 → `src/main.py`'de `reject_entry_vote_from_kind_mention`
çağrısını kaldır (D19a davranışına döner, kusur geri gelir). Bulgu 2 →
`exits._update_trailing`'de `data_reference`'ı `candles[-1].close`'a döndür ve
`engine`'de `data_price_fetch=` bağlamasını kaldır (ayar boşken zaten no-op'tur, yani
canlı testnet davranışı HER İKİ durumda da aynıdır). Bulgu 3 → giriş yolundaki
`if dry_run:` bloğunu kaldır. Bulgu 4 → `_EMPTY_SCALPER_STATUS`'tan eklenen anahtarları
çıkar. Hiçbiri `.env` değişikliği gerektirmez.


### D21 — İşlem adli kaydı (trade forensics) · 2026-08-23 · **AKTİF (yalnız gözlemlenebilirlik — DAVRANIŞ DEĞİŞİKLİĞİ YOK)**
**CANLI (testnet, 2026-08-23 13:34 UTC):** merge 17d2eee (1751 test) → `scripts/deploy.sh awa` (pid 490631, sağlık 45 sn). `/scalper/forensics/summary` ve `/recent` yanıt veriyor, `scalp_trades.forensics` kolonu var; kayıt bir sonraki açılan işlemle başlar (23 Ağu günlük kesici aktif → ilk kayıtlar 24 Ağu 00:00 UTC sonrası).

**Ne:** her scalp işlemi için giriş ve çıkış ANINDA bilinen bağlamın tamamı tek
bir JSON belgesine yazılır (`scalp_trades.forensics` + append-only
`logs/trades.jsonl`), kural tabanlı etiketlerle (`verdict`) sınıflandırılır ve
üç HTTP ucundan + panodaki "adli kart"tan okunur.

**Neden (kullanıcı talebi, 2026-08-23):** "kayıplar/kazançlar: hangi coin, hangi
hareket, hangi sinyal, hangi giriş/çıkış, neler etkiliyor — %100 görürsek
önler, düzeltir, geliştiririz." Bugüne kadar bir kaybın nedeni ancak
`logs/bot.log` içinde elle arayarak, çoğu zaman da hiç bulunamayarak
çıkarılıyordu (XRP #152 analizi bunun tipik örneğidir). Sinyal-öncelik kuralı
(kullanıcı kararı, 2026-08-23) boyut/TP/stop ayarıyla kayıp küçültmeyi YASAKLAR;
geriye kalan tek yol sinyal kalitesini ÖLÇMEKTİR ve ölçüm için önce KAYIT gerekir.

**Kapsam sınırı (bağlayıcı):** bu bir GÖZLEM katmanıdır, güvenlik kilidi değildir.
- Hiçbir kapı, boyutlama, stop/TP seviyesi ya da çıkış kararı `forensics`
  alanını OKUMAZ. `BOT_MODE=scalper` emir akışı byte-for-byte aynıdır.
- Kayıt kurulumundaki her hata yutulur ve TEK SEFER WARNING'e düşer
  (`_forensics_warn`); işlem açılmaya/kapanmaya devam eder
  (`tests/test_forensics.py::TestForensicsNeverBlocksTrading`).
- Backtest harness'ına DOKUNULMADI (P1 paritesi bu maddede gerekmez: harness
  zaten borsaya çıkmaz ve adli kayıt bir karar kuralı değildir).
- **AlgoPro takipçi halkası (D20):** `FollowerExitManager`, `ExitManager`ın
  `_finalize_close`'unu MİRAS ALIR, bu yüzden takipçi kapanışları da bir `exit`
  belgesi + çıkış etiketleri yazar (kendi DB'sine, kendi `logs/trades.jsonl`'ine).
  `entry` bölümü orada YOKTUR (takipçinin kendi executor'ı bağlam üretmez) ve
  BE damgası da yoktur (takipçinin ayrı `_check_tp1_breakeven` yolu). Davranışı
  ETKİLEMEZ; halkanın giriş/çıkış mantığına dokunulmadı.

**Nerede:**
- `src/strategies/scalper/forensics.py` — SAF katman: etiket kuralları
  (`classify_entry`/`classify_exit`), belge kurucuları, `summarize`. IO/saat yok.
- `src/strategies/scalper/forensics_log.py` — `logs/trades.jsonl` (günlük
  rotasyon, 30 gün saklama, secret YOK).
- `engine._forensics_entry_context` (giriş bağlamı; YENİ REST ÇAĞRISI YOK — yalnız
  `_market_gate_status`, `tv_events` ve `ctx`'teki hazır seriler),
  `executor._build_entry_forensics` (gerçek dolum sayılarıyla birleştirir),
  `exits._build_exit_forensics` (zaman çizgisi + etiketler),
  `engine._forensics_postmortem_tick` (kapanıştan N dk SONRA).
- `tracker.record_open/record_close/record_postmortem` + okuma yardımcıları;
  şema `database._ensure_schema_migrations` (idempotent `ALTER TABLE`).
- Uçlar: `GET /scalper/trades/{id}/forensics`, `/scalper/forensics/recent`,
  `/scalper/forensics/summary?since=`. Pano: "Son İşlemler" satırına tıklayınca
  açılan adli kart + "Neler Etkiliyor" paneli.
- Rapor: `scripts/ledger_report.py --forensics` (etiket × sonuç tablosu).

**Etiketler (kural tabanlı, dürüst):** `counter_drift_long`,
`relief_rally_short`, `late_entry_after_run`, `tv_single_family`,
`stale_signal`, `gate_bypassed` (giriş anı); `fee_dominated`, `mfe_giveback`
(kapanış anı); `noise_stop` (post-mortem). Her etiket için pozitif VE negatif
test vardır. Eşikler `SCALPER_FORENSICS_*` ile ayarlanır.

**Look-ahead:** `entry`/`exit` yalnız o anda bilinen değerleri taşır.
Kapanıştan SONRA ölçülebilen tek büyüklük (`noise_stop` — "stop yedikten sonra
fiyat girişe döndü mü") AYRI bir `postmortem` alanındadır, kapanış zamanından
SONRAKİ mumlarla ve pencere DOLDUKTAN sonra hesaplanır, hiçbir karar yolunda
okunmaz (`tests/test_forensics.py::TestPostmortem::
test_candles_before_close_are_ignored`).

**Maliyet:** giriş/çıkış tarafında SIFIR ek REST isteği. Post-mortem safety
turundan TETİKLENİR ama AYRI bir task'ta koşar (tur onu beklemez, bkz. D21-R3);
dakikada en fazla bir tur ve tur başına EN FAZLA BİR sembol çalışır. İstek
`SCALPER_TF_ENTRY` (varsayılan `5m`) limit 150 → **ağırlık 2**, `asyncio.wait_for`
ile 5 sn'de kesilir. Üst sınır: dakikada 1 istek = **tepe saatte 60 istek /
120 ağırlık** (ortalama 2 ağırlık/dk); ölçülmemiş kapanış yoksa sıfır istek —
pratikte kapanış sayısı kadar, yani günde birkaç düzine.
`SCALPER_FORENSICS_POSTMORTEM_MIN=0` bu turu tamamen kapatır.

**Kanıt:** `tests/test_forensics.py` (97 test) + tüm paket yeşil (1722 test).
Canlı defterden bir "etiket → PnL" hükmü HENÜZ YOKTUR — kayıt bugün başlıyor;
ilk hüküm en az bir haftalık testnet verisiyle `--forensics` raporundan
çıkarılacaktır. Yani bu karar bir STRATEJİ kanıtı değil, kanıt ÜRETME
altyapısıdır.

**Geri alma:** `.env`'de `SCALPER_FORENSICS_ENABLED=false` (kayıt durur, motor
aynen çalışır). Tam geri alma: bu commit'i revert et; `scalp_trades.forensics`
sütunu kalırsa zararsızdır (hiçbir kod yolu okumaz).

#### D21-R3 — düşmanca inceleme düzeltmeleri · 2026-08-23

D21 ile ÇELİŞİRSE **D21-R3 bağlayıcıdır**. Beş bulgu, hepsi regresyon testli
(`tests/test_forensics.py`); hiçbiri karar yolunu (emir/kapı/çıkış) değiştirmez.

1. **[medium] Post-mortem safety turunu bloklayabiliyordu.** `_safety_tick`
   içinde `await`lenen `fetcher.get_klines` yavaş/5xx bir veri host'unda
   `KlineFetcher`'ın 3 deneme × 15 sn'si yüzünden ~48 sn askıda kalır; bu süre
   boyunca TP1→BE, trailing, reaper, rezervasyon senkronu ve kill-switch
   gecikir, `/health` 503'e düşer ve watchdog restart eder (2026-08-14 yolu).
   **Düzeltme:** tur `engine._forensics_postmortem_schedule()` ile AYRI bir
   task'a alındı (safety turu beklemez); istek `asyncio.wait_for(..., 5 sn)`
   ile kesiliyor; piyasa-verisi kesintisinde tur hiç başlatılmıyor (iki
   bağımsız sinyal: `exits._market_data_down_reason` VE
   `MarketDataGuard.blocked_until` — ikincisi açık pozisyon yokken ban'ı gören
   tek sinyaldir, böylece geçici bir ban ölçülebilir bir kapanışı
   "ölçülemedi"ye çevirmez); eşzamanlı EN FAZLA BİR post-mortem; başarısız
   ölçüm en fazla 3 kez denenip `postmortem.note="ölçülemedi (…)"` ile
   kapatılıyor (sonsuz yeniden deneme yok). `stop()` task'ı iptal eder.
2. **[medium] `recover()` D21 damgalarını geri yüklemiyordu.** Restart sonrası
   kapanan işlemin ÇIKIŞ zaman çizgisi yanlış okunuyordu (`trail_updates=0` =
   "hiç trail olmadı" gibi) ve giriş etiketleri kayboluyordu. **Düzeltme:**
   `exits._restore_forensics_entry` DB'deki `forensics.entry`'yi belleğe geri
   alır (gerçek ölçüm), kapanış belgesi `path.restart_gap=true` taşır,
   yalnız bellekte tutulan damgalar `null` kalır (UYDURMA değer yok) ve
   `path.initial_stop` kurtarmadaki CANLI stop yerine giriş belgesindeki
   GERÇEK ilk stoptan gelir. `price_ts` KASITLI geri yüklenmez (karar-yolu
   tazelik damgası, D19a-2).
3. **[low] JSONL yazımı `_entry_lock` altında senkrondu.** **Düzeltme:**
   `forensics_log.append_soon` yalnız kuyruğa koyar; gerçek `write()` ayrı bir
   daemon yazıcı iş parçacığındadır (`forensics-jsonl-writer`). Kuyruk üst
   sınırı 2000 satır; taşarsa satır düşer ve tek sefer WARNING'e yazılır.
   Senkron `append` yalnız test/araç yolunda kalır.
4. **[low] Maker modunda yetim adli bağlam başka sinyale iliştirilebilirdi.**
   **Düzeltme:** bağlam artık `PendingEntry` kurulduktan SONRA saklanır ve
   `sembol|yön|created_at_ms` kimliğiyle damgalanır; dolumda kimlik yeniden
   hesaplanıp karşılaştırılır, uyuşmazsa bağlam ATILIR ve WARNING düşer.
5. **[low] Küçük sertleştirmeler.** `executor._forensics_warn` artık `getattr`
   savunmalı; `/scalper/forensics/summary` `since` yokken varsayılan `7d`
   kullanır, üst sınır 365 gündür ve aralık dışı/taşan değer **400** döner
   (eskiden `9999999999d` → `timedelta` taşması → 500);
   `tracker.postmortem_candidates` LIMIT'i "ölçülmüş" filtresinden SONRA
   uygular (aksi hâlde en yeni 20 kapanış ölçülmüşse kuyruk sonsuza dek boş
   görünüyordu); pano "okunamadı" ile "kayıt yok"u AYRI mesajla gösterir ve
   hatayı önbelleğe almaz; `ledger_report.build_report` tek gövdeye indi.

### D22 — `-2021` sonrası acil kapanışın DÜRÜST kaydı + REST ağırlık telemetrisi + durum netliği · 2026-08-23 · **AKTİF (daraltılmış)**
**CANLI (testnet, 2026-08-23 18:29 UTC):** merge 5985582 (1844 test) → `scripts/deploy.sh awa` (pid 1960198, sağlık 25 sn). Doğrulama: `entries_blocked_by=kill_switch`, `stale_reason=entries_blocked`, `rest_weight.enabled=false, last=21` (botun kendi ağırlığı küçük; 23 Ağu'daki 4059/dk tepe IP-geneli başlık → aynı sunucudaki DİĞER süreçler), `as_of` alanı var. Geri alma: `scripts/deploy.sh awa 17d2eee`.
**Kanıt kaynağı:** 2026-08-23 canlı testnet logu (kod okumasıyla doğrulandı).
**Kapsam uyarısı:** bu kararın İLK hâli daha genişti; 12 ajanlık düşmanca
inceleme onu 4 yüksek bulguyla REDDETTİ ve karar aşağıdaki GÜVENLİ ALT KÜMEYE
daraltıldı. Reddedilen tasarım aşağıda "Reddedilenler"e işlendi — tekrar
önerilmemesi için gerekçesiyle birlikte.

**Tek cümlelik özet:** bot artık kendi fiyat okumasına dayanarak piyasa emri
GÖNDERMİYOR; yalnızca borsanın ZATEN yaptırdığı acil kapanışı deftere DOĞRU
yazıyor, REST ağırlığını ÖLÇÜYOR (davranış değiştirmeden) ve panoyu yanlış
teşhise sürükleyen alanları düzeltiyor.

---

#### 1) `-2021` sonrası acil kapanış → `TRAIL_MARKET` / `BE_MARKET`

**Kusur (bugün 3 olay: DOGE, BNB, ETH).** `exits._update_trailing` chandelier
seviyesini gönderiyor, Binance `-2021 Order would immediately trigger`
dönüyor, `position_manager._replace_stop_loss` bunu bilinçli bir çıkış kararı
sayıp `_emergency_close` ile pozisyonu reduce-only MARKET ile KAPATIYOR —
**bu davranış D22'den ÖNCE de vardı ve korunmuştur.** Kusur KAYITTAYDI:
fonksiyon `False` döndüğü için `_update_trailing` "trailing SL güncellenemedi,
**eski SL korunuyor**" logluyordu — pozisyon YOKKEN. Bir sonraki safety turu
kapanışı tespit edip deftere `exit_reason=TRAIL` yazıyordu. Sonuç: defter "iz
tetiklendi" derken bot piyasa emriyle çıkmıştı; canlı defter NİHAİ HAKEM
olduğu için (CLAUDE.md) bu, kanıt tabanını kirletir.

**Düzeltme — YALNIZ kayıt katmanı:**
- **(a) Yapılandırılmış sonuç.** `position_manager._replace_stop_loss` artık
  `StopReplaceResult` döner (`replaced` | `emergency_closed` | `no_position` |
  `failed`; `__bool__` eski sözleşmeyi korur, tüm çağıranlar değişmeden
  çalışır). `emergency_closed` gelirse exits **"ACİL KAPANIŞ GERÇEKLEŞTİ"**
  loglar. **"Eski SL korunuyor" YALNIZ `failed` durumunda yazılır** — yani
  pozisyon gerçekten açık ve eski koruma yerindeyken.
- **(b) Dürüst etiket, TÜM stop yollarında.** `_update_trailing` ve TP2 runner
  tabanı → `TRAIL_MARKET`; TP1 break-even, `force_breakeven` (TV olayı) ve
  takipçi TP1 → `BE_MARKET`; `force_stop_to` (yapı çıkışı) → `TRAIL_MARKET`.
  İkisi de TRAIL AİLESİNDENDİR (`forensics.exit_reason_family`) ama
  `scripts/ledger_report.py`'de AYRI SATIRDA sayılır ve panoda aynı TRAIL
  rengini kullanır. Sayılarının artması ANLAMLIDIR: stop kararı piyasa hızının
  gerisinde kalıyordur.
- **(c) ÇİFT EMİR YOK.** Kapanışı yapan emir zaten `_emergency_close`
  tarafından gönderilmiştir. `_finalize_market_exit` **ikinci bir MARKET
  emri GÖNDERMEZ**; yaptığı tek şey `get_position_risk(force_fresh=True)` ile
  FLAT DOĞRULAMASI ve `_handle_closed` çağrısıdır. Doğrulanamazsa koruma
  emirlerine DOKUNULMAZ (fail-closed) — ikinci bir kapanış emri `-2022
  ReduceOnly rejected` yarışı üretirdi.
- **(d) Etiket sigortası.** Etiket, finalize edilmeden ÖNCE
  `sp.pending_exit_reason`a çivilenir. O tur doğrulama başarısız olsa bile
  (`-2022`, REST hatası, borsa hâlâ miktar gösteriyor) kapanışı sonraki turda
  hangi yol yakalarsa yakalasın `_handle_closed` AYNI etiketi kullanır.
  Etiketin kaybolması, D22'nin düzeltmek için var olduğu kusurdur.
- **(e) Kapanış fiyatı GERÇEK dolumdan.** `_emergency_close` artık
  `EmergencyCloseResult` döner (emir kimliği + `avgPrice`; `__bool__` eski
  sözleşmeyi korur). `_verified_close_ledger` yalnız ALGO adaylarına
  (SL/TP1/TP2/TP3) baktığı için düz MARKET kapanışını GÖREMEZ; artık defter
  fiyatı `userTrades` VWAP'ından (yoksa `avgPrice`ten) okur ve notu
  `exit_fill=market_close_order` olur. Satırlarda en ufak anormallik varsa TÜM
  sonuç atılır ve eski tahmin yoluna düşülür. **Income doğrulama merdiveni
  DEĞİŞMEDİ** (income → trades → tahmini brüt).

**AYNI HOST'ta koruma-tarafı kapısı YOKTUR** (bkz. "Reddedilenler"): stop
borsaya gönderilir, hükmü BORSA verir. Ayrı market-data host'unda (D17) kapı
AYNEN durur — orada yanlış taraf borsalar-arası BAZ hatası olabilir, tur
atlanır ve borsadaki SL yerinde kalır.

**Nerede:** `src/strategies/scalper/exits.py` (`_apply_stop`,
`_on_emergency_closed`, `_note_market_close`, `_finalize_market_exit`,
`_market_close_exit_price`, `_fill_vwap`, `_handle_closed`),
`src/strategies/follower/exits.py` (`_check_tp1_breakeven`),
`src/trading/position_manager.py` (`StopReplaceResult`,
`EmergencyCloseResult`, `_replace_stop_loss_result`, `_emergency_close`),
`src/strategies/scalper/executor.py` (`ScalpPosition` alanları),
`scripts/ledger_report.py`, `src/strategies/scalper/forensics.py`.
**Test:** `tests/test_trailing_market_exit.py` (40 test).
**Geri alma:** commit'i revert et. Kısmi geri alma gereksizdir — emir yolu
değişmedi, yalnız kayıt katmanı eklendi.

---

#### 2) REST ağırlık TELEMETRİSİ (geri çekilme **varsayılan KAPALI**)

**Kusur.** `X-MBX-USED-WEIGHT-1M` ≥ 1800 için YALNIZ bir WARNING vardı; bugün
276 uyarı satırı, tepe **4059/dk** (sınır 2400). Sayaç **IP GENELİDİR** — aynı
çıkış IP'sindeki başka süreçler de tüketir. 418 = koruma turunun körleşmesi;
repodaki en pahalı arıza sınıfı (2026-08-12, 2026-08-15).

**Düzeltme — ÖLÇÜM açık, DAVRANIŞ kapalı.** İstemci katmanında kademeli geri
çekilme mekanizması vardır (`_weight_gate`, `priority` parametresi) ama
`BINANCE_WEIGHT_SOFT_LIMIT=0` / `BINANCE_WEIGHT_HARD_LIMIT=0` ile **VARSAYILAN
OLARAK KAPALIDIR**.

> **Neden kapalı — ölçüm.** Testnet'te `X-MBX-USED-WEIGHT-1M` başlığı IP
> GENELİ bir sayaçtır ve 2026-08-23 ölçümünde günün **MEDYANI 2373**'tü
> (>2000). İlk tasarımın 2000/2300 eşikleriyle açık olsaydı `_scan_tick`
> KALICI olarak durur ve bot hiç işlem açmazdı. Eşik, önce telemetriyle
> ölçülmeli, sonra o dağılımın belirgin ÜSTÜNE konmalıdır
> (`docs/RUNBOOK.md` "REST ağırlık bütçesi").

Açıldığında sözleşme şudur:
- **≥ soft:** KRİTİK OLMAYAN istekler takvim dakikasının sonuna kadar ağa
  ÇIKMAZ (Binance 1M sayacı orada sıfırlanır). Önbelleği olan okumalar BAYAT
  servis edilir (`_get_account`, `get_current_price`).
- **≥ hard:** aynısı + dakikada BİR CRITICAL satır.
- **KRİTİK istekler HER ZAMAN geçer:** emir, SL/TP, positionRisk koruma turu,
  kapanış doğrulaması, günlük risk income'ı. Varsayılan `priority="critical"`
  — bir çağrı yolu unutulursa güvenli tarafta kalır.
- Görünürlük: `entries_blocked_by="rest_weight"` ve
  `scan_status="degraded:rest_weight"`.

**Pencere `max()` ile KİLİTLENMEZ** (düşmanca inceleme bulgusu): ileri bir
saat sıçraması (NTP düzeltmesi, VM suspend) `max()` yüzünden saatlerce
sürecek bir geri çekilme çivileyebilirdi. Pencere daima içinde bulunulan
takvim dakikasının sonudur (`min(..., now+60)` ikinci kemer) ve okuma
tarafında bir dakikadan uzağa işaret eden damga GEÇERSİZ sayılıp temizlenir.

**Telemetri (eşiklerden BAĞIMSIZ):** `/scalper/status.rest_weight` =
`{last, last_at, max_1m, peak_at, soft_backoffs, hard_backoffs, soft_limit,
hard_limit, enabled, backoff, backoff_seconds_left}`. **`max_1m` DAKİKA
DİLİMLİDİR** — içinde bulunulan takvim dakikasının tepesidir ve dakika
başında sıfırlanır. Süreç ömrü boyu tutulan bir tepe farklı dakikaları tek
sayıya katlar ve RUNBOOK'un "`max_1m` > 3000 ise araştır" kuralını okunamaz
kılardı. Ağırlık uyarı satırı dakikada en fazla birdir (276 satır/gün, gerçek
arızayı gömüyordu).

**Nerede:** `src/trading/binance_client_improved.py`, `src/core/config.py`,
`env.example`, `src/main.py`, `src/strategies/scalper/engine.py`.
**Test:** `tests/test_rest_weight_backoff.py` (53 test).
**Geri alma:** zaten kapalıdır; açmak `.env`'de eşik vermekle olur ve `.env`
yedeği + bu dosyaya bir satır ister.

---

#### 3) Pano önbelleği ve `as_of` damgası

`/api/status` ve `/scalper/status` sunucu tarafında **5 sn** önbelleklenir
(pano da 5 sn'de bir yokluyor: her tik EN FAZLA bir kez gerçek iş yapar) ve
pano yolundan `force_fresh` İSTENMEZ — 2026-08-18'de panonun force-fresh
çağrısı rate-limiter'ı doyurup tarama döngüsünü aç bırakmıştı. Motor YOKKEN
`/scalper/status` önbelleklenmez (o yol REST yapmaz ve olay defteri taze
olmalıdır).

Önbelleğin üç kuralı vardır:
1. **`as_of`** (ISO) gövdenin KURULDUĞU andır, isteğin geldiği an değil; pano
   "son güncelleme"yi ondan yazar. Aksi halde önbellekten servis edilen bayat
   bir tablo her tikte TAZE görünürdü.
2. **Durum DEĞİŞTİREN uçlar önbelleği düşürür:** `/risk-event`
   (halt/resume/flatten), `/tv-events/reset`. Aksi halde operatör komuttan
   sonra 5 sn boyunca "komut yutuldu" diye okurdu.
3. **Sorgu dizesi anahtarın parçasıdır** (`?include_shadow=1` gibi bir
   varyant ileride eklenirse yanlış gövde servis edilmesin).

#### 4) Durum netliği: `entries_blocked_by` + `market_gate.stale_reason`

**Kusur.** Kill switch/entry-halt açıkken `_scan_tick` lider anlık görüntüsünü
TAZELEMEZ; `/scalper/status.market_gate` bir süre sonra `stale=true,
gate_effective=false` gösterir. Bu, "lider piyasa kapısı bozuldu" gibi okunur —
gerçek neden ise "tarama zaten durmuş"tur.

**Düzeltme.**
- `/scalper/status.entries_blocked_by` = `null` | `"entry_halt"` |
  `"kill_switch"` | `"risk_event"` | `"exchange_readiness"` | `"rest_weight"`
  (bu öncelik sırasıyla; sonuncusu yalnız geri çekilme AÇIKKEN dolabilir).
- `market_gate.stale_reason` = `"entries_blocked"` (tarama durdu) vs
  `"leader_stale"` (veri gelmiyor).
- `/scalper/status.forensics_queue` (yazıcı kuyruğu + post-mortem durumu) da
  yayımlanır; `_EMPTY_SCALPER_STATUS` ile şekil paritesi test edilir.
- Pano üst şeridinde tek satır **"Sistem durumu"**: Kapı, Kline kaynağı,
  Günlük kesici, Ağırlık, TV olayları, Post-mortem kuyruğu. **YENİ İSTEK
  AÇMAZ** — verisi zaten çekilen `/scalper/status` gövdesinden okunur.
  **"Kline" rozeti düzeltildi:** eskiden `kline_source === "mainnet"`
  karşılaştırılıyordu, oysa alanın gerçek değerleri `"separate"` /
  `"trading_host"`tur — rozet DOĞRU kurulumda bile asla yeşil olmuyordu.
  Karar artık gerçek host adından verilir (testnet → sarı, mainnet → yeşil).

#### 5) Küçük: beklenen durumlar ERROR/WARNING olarak loglanmaz
`-2011 Order does not exist` (zaten dolmuş/iptal edilmiş emrin iptali) beklenen
bir yarıştır → **INFO** (`is_benign_cancel_error`; `cancel_all_open_orders`,
`position_manager._cancel_stale_stops`, eski SL iptali). **DEBUG DEĞİL:**
üretimde DEBUG kapalıdır ve bu satır defter sapmasının (emir aradaki
milisaniyelerde dolmuş) tek izidir. Maker kısmi dolum uyarıları WARNING → INFO
(akış onları zaten doğru ele alıyor: kalan iptal, dolan miktar derhal korunur).

**Kapsam sınırı (bağlayıcı):** hiçbir `SCALPER_*` strateji parametresi
değişmedi; giriş kuralları, boyutlama, TP/stop seviyeleri ve backtest harness'ı
BİREBİR aynıdır (CLAUDE.md yasak #1 ve #2 kapsamı dışında). **Emir yolu da
değişmedi** — hiçbir yeni emir gönderilmez, mevcut bir emir de bastırılmaz.
Değişen: (i) kapanışın deftere yazılan ETİKETİ ve FİYAT KAYNAĞI, (ii) log
seviyeleri, (iii) durum alanları ve pano önbelleği, (iv) varsayılan KAPALI bir
ağırlık telemetrisi/geri çekilmesi.

### D25 — Tek container dağıtım yolu (taşınabilirlik) · 2026-08-24 · **AKTİF (EK YOL — canlı supervisord DEĞİŞMEDİ)**

**Ne.** Bot tek bir `python:3.12-slim` görüntüsüne paketlendi: `Dockerfile`,
`docker-compose.yml`, `.dockerignore`, `scripts/docker_run.sh`,
`tests/test_container.py` (61 sözleşme testi + opt-in duman testi),
`docs/RUNBOOK.md` → "Container ile çalıştırma / başka sunucuya taşıma",
CI'da `docker-build` işi (build-only, push YOK).

**Neden.** Kullanıcı isteği: *"hepsi aynı container'da olsun, sonra başka
sunucuya taşıyacağım."* Bugünkü canlı yol (supervisord `tradingbot_v2`,
`/opt/tradingbot-v2`, `.venv`, uvicorn :9091) **hiç değişmedi** —
`scripts/deploy.sh` / `server_deploy.sh` / `restart_safe.sh` dosyalarına
DOKUNULMADI (`git diff --stat -- scripts/deploy.sh scripts/server_deploy.sh
scripts/restart_safe.sh` boş).

**En kritik kural.** ⛔ **supervisord ile container AYNI ANDA ÇALIŞAMAZ** —
aynı Binance hesabı, aynı pozisyonlar → çift SL/TP, yarışan devralma,
`state/*.json`'da son-yazan-kazanır. Bu, D20b'deki "ayrı halka + gömülü
takipçi aynı anda" kritik sınıfının aynısıdır. `docker_run.sh` bunu İKİ
bağımsız sinyalle yoklar (supervisorctl + `pgrep -af 'uvicorn.*src\.main:app'`);
bilinçli istisna `DOCKER_ALLOW_ALONGSIDE=1`.

**Kanıt (ölçüldü, yerel docker 27.4.0 / linux-aarch64, 2026-08-24).**
| İddia | Ölçüm |
|---|---|
| Görüntü derleniyor | 715 MB, ~2 dk; `user=bot` (uid 10001), Python 3.12.14, `TZ=UTC` |
| Uygulama ayağa kalkıyor | `env.example` ile `/health` **503 degraded** (beklenen: `Binance [401] -2014 API-key format invalid`), `/dashboard` 200 (88 KB) |
| Defter kalıcı | sqlite `data/` mount'unda oluştu (WAL kardeşleri aynı dizinde) |
| Zarif kapanış | `docker stop -t 120` → **1 sn, exit 0**; lifespan `finally` zinciri loga düştü |
| entry-halt kalıcı + fail-closed | host'a yazılan halt dosyası container'da görüldü; **bozuk** dosyada da `🚨 entry halt state okunamadı … fail-closed kapalı` → `entry_halted=true` |
| Secret sızıntısı yok | `?secret=<değer>` isteği `docker logs`'ta `secret=***`; ham değer bulunamadı |
| Container içi tam paket | **2021 passed, 2 skipped** |
| Host tam paket | **2021 passed, 2 skipped** (2 skip = mevcut 1 + opt-in duman testi) |

**Bilinçli sapmalar (gerekçeleriyle).**
* **Python 3.12** (3.11 değil): CI ve sunucu venv'i 3.12'dir
  (`.github/workflows/ci.yml`). Aksi hâlde "container'da testler geçti" ile
  "sunucuda testler geçti" aynı şey olmaz ve container bir deploy kapısı sayılmazdı.
  Parite `test_dockerfile_python_version_matches_ci_and_server` ile kilitli.
* **Defter `data/` altına alındı** (`DATABASE_URL=sqlite:///./data/tradingbot.db`):
  `journal_mode=WAL` `-wal`/`-shm` kardeşlerini AYNI dizinde üretir; tek DOSYA
  bind-mount'u onları container katmanında bırakır ve container silinince
  **checkpoint edilmemiş kayıtlar kaybolurdu**. Süreç ortamı `.env`i ezdiği için
  taşınan `.env` düzenlenmeden çalışır.
* **418 ban penceresi UTC hesaplanır** (`restart_safe.sh` YEREL kullanır):
  container `TZ=UTC` yazar, host UTC+2 olabilir; yerel kesim noktasıyla pencere
  "15 dk − 2 saat" olur ve AKTİF ban SESSİZCE görülmezdi (ölçüldü: CEST host'ta
  taze `HTTP 418` satırı filtreden 0 satır geçti).
* **`stop_grace_period: 300s`**: tek bir REST çağrısı 429'da 2×60 sn sürebilir
  (`binance_client_improved.py`), `cancel_all_pending` emir başına çağrı yapar;
  120 sn tavanı tek 429'da dolar → SIGKILL → iptal edilmemiş LIMIT emirleri
  borsada asılı kalırdı.
* **`restart: unless-stopped`** (418 kuralıyla gerilim, bilinçli): alternatifi
  açık pozisyonlu botu süresiz kapalı bırakır. Emniyet, uygulamanın KENDİ
  fail-closed kapılarıdır (yukarıda ölçüldü) + sağlık yoklaması otomatik restart
  TETİKLEMEZ + autoheal kullanılmaz.
* **İkinci halka (`BOT_MODE=follower`, :9093) `profiles: [follower]` ile
  VARSAYILAN KAPALI** — D20b gömülü mod tercih edilendir.

**Düşmanca inceleme (2 ajan, 39 bulgu).** Düzeltilen kritikler: (a) `.dockerignore`
`.github/`i dışlıyordu ama `tests/test_container.py` `ci.yml`i okuyor → container içi
tam paket KIRMIZI dönüyordu (`!.github/workflows/ci.yml` istisnası eklendi, sonra
2021 passed ölçüldü); (b) supervisord kapısı fail-open'dı (pgrep ikinci sinyali);
(c) 418 penceresi TZ uyumsuzluğu; (d) duman testi host bind-mount'a bağlıydı ve
yayınlanan portta duran BAŞKA bir container'ın cevabıyla YANLIŞ GEÇMİŞTİ →
`docker create` + `docker cp` + isimli volume + `docker exec` ile yeniden yazıldı;
(e) redaksiyon 8 sızıntı sınıfını kaçırıyordu (hepsi maskelendi, testle kilitli);
(f) `archive/` görüntüye giriyordu (API-anahtarı biçimli dizeler); (g) README/INSTALL
çıplak `docker-compose up -d` öğretiyordu (kapısız); (h) reboot sonrası supervisord
`autostart=true` + container `unless-stopped` = iki motor (taşıma reçetesine
`autostart=false` adımı ZORUNLU olarak eklendi); (i) `.env` `chmod 600` + root
sahipliği → container uid okuyamaz → sonsuz çökme döngüsü.

**Geri alma.** Container yolu tamamen ek'tir: `scripts/docker_run.sh --down` yeter;
dosyalar silinse bile canlı supervisord yolu etkilenmez. Karşı yön (container'dan
supervisord'a dönüş) RUNBOOK adım 6'dadır — **iki defteri BİRLEŞTİRME**, hangisinin
geçerli olduğuna karar ver (aynı `entry_order_id` iki kez girer).

**Açık kalanlar.** `forensics_log.drain()` üretim kapanış yolunda çağrılmıyor
(kapanışta adli kayıt satırı kaybı; D21 gereği yalnız gözlem, işlem riski yok) —
ayrı bir değişiklikte. `ccxt==3.1.60` hiç import edilmiyor; kaldırılırsa görüntü
küçülür — ayrı bir commit'te.

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
| Yapı (CHoCH/BOS) giriş kapısı — 5m pivot 5 (E9/S1) | 08-23 | AYI 0.85/−1057, YATAY 0.93/−356, BOĞA −%67 | C ters-trend; yapıya ters işlem yasağı kâr kaynağını yasaklıyor |
| Yapı giriş kapısı — 15m pivot 5/8 (E9/S2, S2p8) | 08-23 | En iyi hâl AYI PF 1.00, YATAY −%91, BOĞA −%61 | pivot büyüdükçe tabana yakınsıyor = "en iyisi hiçbir şey yapmamak" |
| Yapı CHoCH çıkışı — BE / market kapanış (E9/S3, S4) | 08-23 | WR %85 → %48 / %34; AYI −1589 / −2442 | SL 29→1 düşüyor ama TRAIL kazananları 182→29 çöküyor (ödeme asimetrisi) |
| **D22 ilk hâli — aynı host'ta ÖN-KAPANIŞ** (koruma-tarafı kapısı + `_trailing_market_exit`) | 08-23 | 12-ajan düşmanca incelemesi: 4 YÜKSEK bulgu | aşağıda |
| **D22 ilk hâli — ağırlık geri çekilmesi varsayılan AÇIK** (2000/2300) | 08-23 | testnet `X-MBX-USED-WEIGHT-1M` günlük MEDYANI 2373 | eşik medyanın ALTINDA: tarama KALICI durur, bot hiç işlem açmazdı; varsayılan 0/0'a alındı, eşik önce ölçülür |

### D22'nin reddedilen ön-kapanış tasarımı (08-23, kayıt için)

**Öneri neydi.** `_update_trailing`, chandelier seviyesini borsaya göndermeden
ÖNCE botun kendi canlı fiyat okumasıyla karşılaştıracak; seviye "yanlış
taraftaysa" koşullu emri HİÇ göndermeyip pozisyonu kendiliğinden reduce-only
MARKET ile kapatacaktı (`_trailing_market_exit` → `engine._close_position_market`).

**Neden reddedildi (12 ajanlık düşmanca inceleme, 4 yüksek bulgu):**
1. **Yetki genişlemesi.** Bugüne kadar "pozisyonu piyasa emriyle kapat"
   kararını yalnız BORSA (`-2021`), reaper (yaş limiti) ya da operatör
   (`/risk-event flatten`) verebiliyordu. Öneri, bu geri alınamaz kararı
   rutin bir safety turuna ve botun KENDİ fiyat okumasına bağlıyordu.
2. **Bayat/yanlış fiyat riski.** Kapı `sp.position.current_price` üzerine
   kuruluydu. Öneri buna 30 sn'lik bir tazelik kontrolü ekliyordu ama tazelik
   DOĞRULUK değildir: tek bir hatalı ticker okuması kârlı bir koşucuyu
   piyasadan çıkarabilirdi. Kapı kaldırılınca bu risk de ortadan kalktı.
3. **Çift emir / `-2022` yarışı.** `-2021` yarışı gerçekleştiğinde
   `position_manager` zaten MARKET emri göndermişken `_finalize_market_exit`
   ikinci bir MARKET daha gönderiyordu; pozisyon snapshot'ı bir an geride
   kalırsa `-2022 ReduceOnly rejected` ve etiket kaybı.
4. **Kazanç yok.** Öneri, `-2021` sonrası kapanışı ENGELLEMİYORDU (o zaten
   oluyordu); yalnız aynı kapanışı BİR TUR ÖNCE ve daha zayıf bir kanıtla
   yapıyordu. Kayıt kusuru ise ön-kapanış OLMADAN da tamamen düzeltilebilirdi
   — nitekim öyle yapıldı.

**Kalan davranış:** stop borsaya gönderilir, hükmü borsa verir, mevcut
`_emergency_close` çalışır ve kapanış `TRAIL_MARKET`/`BE_MARKET` etiketiyle
deftere yazılır. Ayrı market-data host'unda (D17) kapı AYNEN durur — orada
"yanlış taraf" bir baz ÖLÇÜM hatası olabilir ve doğru cevap turu atlamaktır.


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
