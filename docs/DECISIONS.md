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
### D15 — Lider piyasa kapısı ("ters-gün kapısı") · 2026-08-23 · ADAY, UYGULANMADI (varsayılan KAPALI)
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
