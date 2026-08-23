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

### D15 — Lider piyasa kapısı ("ters-gün kapısı") · 2026-08-23 · ADAY, UYGULANMADI (varsayılan KAPALI)
**Ne:** `SCALPER_MARKET_GATE` (varsayılan `false`) ile iki BAĞIMSIZ alt-kapı
(`src/strategies/scalper/market_gate.py`, saf fonksiyon, IO yok):
1. **gün-içi** (`SCALPER_MARKET_GATE_DAY_PCT`, varsayılan 1.0): lider sembolün
   (`SCALPER_MARKET_GATE_SYMBOL`, varsayılan BTCUSDT) son kapanışı gün açılışının ≥%X
   ALTINDAYSA yeni LONG, ≥%X ÜSTÜNDEYSE yeni SHORT açılmaz.
2. **uzama** (`SCALPER_MARKET_GATE_RUN_PCT`=15 / `_RUN_DAYS`=3): lider son N TAMAMLANMIŞ günde
   ≥+%Y koştuysa LONG, ≤−%Y düştüyse SHORT açılmaz (koşu = `kapanış[-1]/kapanış[-1-N] − 1`).
Her alt-kapı kendi yüzdesi 0 yapılarak ayrı ayrı kapatılır. Rejim kapısından (D5) FARKLIDIR:
D5 sembolün KENDİ EMA50/200 trendine bakar, bu kapı yalnız LİDERE bakıp kararı tüm evrene uygular.

**Nerede:** `engine._market_gate_reason`, rejim kapısının HEMEN yanında, `_evaluate_symbol`
içinde — yani C taraması VE TV `external_signal` AYNI tek giriş noktasından geçer (D5'teki gibi
ayrı bir TV muafiyet bayrağı YOKTUR; TV zaten aynı fonksiyondan geçiyor). Harness tarafı
`backtest.simulate_symbol` + `LeaderSeries` — İKİ TARAF AYNI FONKSİYON NESNESİNİ çağırır (P1).

**Girdi türetme paritesi (bilinçli tasarım kararı):** "gün açılışı" iki tarafta da SON
TAMAMLANMIŞ GÜNLÜK KAPANIŞ'tır (`day_open_from_daily_closes`). Gerekçe: `KlineFetcher.
_drop_unclosed` oluşmakta olan mumu HER ZAMAN atar (repaint koruması), bu yüzden canlı motor
oluşmakta olan GÜNLÜK mumu hiç göremez — gerçek "bugünün open'ı" canlıda TÜRETİLEMEZ.
Harness'ta türetilebilirdi ama o zaman iki taraf farklı bir büyüklük hesaplar ve parite bozulurdu.
7/24 açık bir piyasada günlük open ile önceki close arasındaki fark tik mertebesindedir (eşik %1).

**REST ağırlığı:** lider BAŞINA ~60 sn TTL önbellek (sembol başına DEĞİL) — tarama turu başına
en çok 2 istek (`1d` limit N+2 ve giriş TF limit 3; ikisi de ağırlık 1). Kapı kapalıyken TEK
istek bile gitmez (`test_gate_off_makes_no_request_at_all`). Lider verisi alınamazsa kapı
UYGULANMAZ (fail-open) + WARNING — lider verisi eksikliği bir risk olayı değildir (spec §C).

**Kanıt (E7, `docs/EXPERIMENTS.md` "2026-08-23 — Lider piyasa kapısı"):** 8 varyant × 3 pencere,
loglar `logs/market_gate/<varyant>_<pencere>.log`. V0 (kapı kapalı) mevcut tabanı BİREBİR üretti
(AYI 1.04/DD 3683 · YATAY 1.29/3229 · BOĞA 2.43/735).
- **V1 (gün-içi %1.0)** — AYI 1.04→**1.33** (+584→+2999, DD 3683→2956) · YATAY 1.29→**1.36**
  (+2392→+2593) · BOĞA 2.43→2.37 (+3902→+3725, −%4.5). P2'nin HER İKİ kolunu da geçer.
  Mekanizma doğrulandı: AYI'da SL 29→17, SL zararı −14907→−8738; LONG −956→−121 (düşen-bıçak),
  SHORT +1541→+3120 (rahatlama-rallisi) — kapı simetrik çalışıyor; RANGE günleri −1029→+425.
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

**Çapraz kontrol (E8 sinyal otopsisi, aynı gün):** E8 kapıyı bağımsız olarak harness JSON'u
üzerinde POST-HOC ölçtü. Yöntem farkı önemli: E8'de engellenen sinyal kapasiteyi serbest
BIRAKMIYOR, bu yüzden sayıları motor-içi kapının ALT SINIRI (E8 bunu kendi de işaretledi).
YATAY %1.0'da işaret bile farklı (E7 +201 / E8 −487) — fark tam olarak kapasite yeniden
tahsisinden geliyor. İki ölçüm ÇELİŞMİYOR; E8 muhafazakâr taraftan bakıyor ve eşik önerisi (%1.3)
motor-içi kapıyla doğrulandı → benimsendi. Bacak-ayrık eşik (SHORT %1.0 / LONG %1.3) E8'in
önerisiydi ama UYGULANMADI: ayrı bir tasarım kararıdır (kendi spec'i + onayı gerekir) ve E7 verisi
"LONG bacağı işe yaramıyor" iddiasını desteklemiyor (LONG bacağı tabana göre −956→+180 iyileşiyor).

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
Varsayılan `SCALPER_MARKET_GATE_RUN_PCT=15` spec §C'de onaylandığı için **sessizce
değiştirilmedi**; bunun yerine motor açılışta AÇIKÇA uyarıyor
(`ScalperEngine._maybe_log_market_gate_banner` — kapı açık + `RUN_PCT>0` ise ikinci bir WARNING).
Varsayılanı 0'a çekmek kullanıcı kararıdır.

**Bilinen sapma — "gün açılışı" vekili ve testnet.** Fark ÖLÇÜLDÜ (BTCUSDT, 70 gün): mainnet'te
(harness veri kaynağı) gerçek `1d` open ile önceki close arasındaki fark ort. %0.000082 / maks
%0.0006 — eşiğin binde 6'sı, yani E7 sonuçları E8'in "gerçek open" tanımıyla da geçerli.
TESTNET'te (canlı motorun kaynağı, `data.py` → `settings.binance_base_url`) fark ~200× büyük:
ort. %0.013 / maks %0.152 = eşiğin %15'i. Yani testnet soak'unda kapı, harness'ın ölçtüğünden
MARJİNAL günlerde farklı karar verebilir. Gerçek open'a geçmenin ucuz ve parite-korur bir yolu
bulunamadı: `1h` mumu da 00:00-01:00 UTC arasında kapanmamış olduğu için düşer (saat başında
referans değiştiren, harness'ın taklit edemeyeceği canlı-only süreksizlik) ve `_drop_unclosed`'ı
gevşetmek tüm motorun paylaştığı repaint korumasını zayıflatır. Mainnet'te — gerçek paranın
çalışacağı yer — sapma ihmal edilebilir olduğu için vekil bilinçle korundu.

**Kanıt (kod):** `tests/test_market_gate.py` — 67 test (saf fonksiyon: her alt-kapı/yön/eşik
sınırı/eksik-geçersiz veri; motor: önbellek, fail-open+WARNING, ret sayaçları, `/scalper/status`
`market_gate` alanı, GERÇEK `_evaluate_symbol` üzerinden C ve TV yolunun ikisi de; harness:
look-ahead yasağı, `missed_counter` anahtarları; PARİTE: iki modülün aynı fonksiyon nesnesini
aynı argümanlarla çağırdığı; Settings env parse). `python3 -m pytest tests -q` → 743 passed,
1 skipped (önceki: 676). `tests/test_golden_backtest.py` DEĞİŞMEDEN geçer.

**Geri alma:** `.env`'den `SCALPER_MARKET_GATE`'i kaldır/`false` yap — varsayılan zaten `false`,
davranış değişmez, kod geri alınmasına gerek yok. Tam geri alma gerekirse commit `ece8bd8`
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
Tek pencerede parlayan reddedilir. Terfi: backtest → testnet ≥5 gün (en az 1 düşüş günü) → mainnet.

### P3 — Simülatör ölçeği · 2026-08-21
Boğa penceresinde canlı defterin şeklini birebir üretir (LONG baskın), ölçek ~3× (boyutlama).
Kararlar göreli farkla; canlı defter nihai hakem.
