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
