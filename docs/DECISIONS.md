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
uyar). Ortada kalırlarsa istek ya yine olay yoluna gider ya **422** alır — ama
**hiçbir koşulda giriş oyuna dönüşmez**. 422 alan alarm TV'de "webhook failed"
görünür; çözüm mesajı düzeltmektir, alarmı silmek değil.

**Kanıt:** `python3 -m pytest tests -q` → **877 passed, 1 skipped**
(D19 tabanı: 744 passed, 1 skipped → +133 test; `tests/test_tv_events.py` 68 → 201).
`tests/test_tv_signal_bridge.py` TEK SATIR değişmeden geçiyor (49 alarmın regresyonu).
Ayrıca `TestRoutingInvariants` iki DEĞİŞMEZ kuralı tohumlanmış rastgele gövdelerle
tarar: (1) hiçbir GİRİŞ alarmı olay-kaynağı koruması yüzünden yanlışlıkla 422 almaz,
(2) hiçbir OLAY alarmı — belirteç nereye yazılırsa yazılsın — `external_signal`'a ya
da `TvConfluence.vote()`'a ULAŞMAZ.

**Geri alma:** D19 ile aynı — `.env`'den `SCALPER_TV_EVENTS_*` kaldır (varsayılan
`shadow`), tamamen kapatmak için `SCALPER_TV_EVENTS_MODE=off`. Kod düzeyinde geri
almak gerekirse bu commit'teki `src/main.py`, `src/core/config.py`,
`src/services/tv_events.py`, `src/strategies/scalper/engine.py`,
`src/strategies/scalper/exits.py` değişikliklerini revert et; D19'un kendisi
bağımsız olarak ayakta kalır (ama A/B/C bulguları geri gelir — **önerilmez**).


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
