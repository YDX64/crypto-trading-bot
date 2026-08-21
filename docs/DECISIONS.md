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
Alternatif E4g TP1 %12 (+1713) boğa PF'yi düşürüp ayı DD'yi artırıyor → E4f tercih. Uygulama zamanı:
D6 soak'u (≥5 gün) bittikten sonra; kullanıcı isterse daha erken (atıf bulanıklaşır). Log: logs/autoresearch/2026-08-21/.

### D13 — Kaldıraç tavanı bulgusu (E4i/E4j) · 2026-08-21 · ARAŞTIRMA
DYN_LEV_MAX 20→10: AYI PF 1.68 / +5624 (taban +886), BOĞA 55 işlem (örneklem yetersiz); 15: ayı +3293, boğa −%39.
Geniş stop (düşük kaldıraç) ayıda SL'leri keskin azaltıyor. Tur-3 adayı: DYN_LEV_MAX 12-15 + TP1 8 birleşimi.
Not (P1): harness SCALPER_MAX_POSITIONS kapasitesini modellemiyor (E4h taban ile birebir aynı) — parite boşluğu.

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
| AlgoPro V1.6 + yüksek kaldıraç TP1 | 08-21 | TP1 kazanma ort %40.5 (başabaş %50) | repaint yok ama beklenti −0.19R |

## Metodoloji kararları

### P1 — Harness = canlı motor (parite) · 2026-08-21
Harness rejim kapısını uygulamıyordu; tüm eski sayılar geçersiz sayıldı. `simulate_symbol`
artık `cfg.scalper_regime_filter` ile canlıyla aynı kuralı uygular; testler
`tests/test_scalper_backtest.py`. Motorda kapı/filtre değişirse harness da değişir.

### P2 — Karar kuralı · 2026-08-21
Aday = AYI PF ≥ 1.1 (veya AYI ve YATAY PnL birlikte iyileşir) VE BOĞA PnL kaybı ≤ %20.
Tek pencerede parlayan reddedilir. Terfi: backtest → testnet ≥5 gün (en az 1 düşüş günü) → mainnet.

### P3 — Simülatör ölçeği · 2026-08-21
Boğa penceresinde canlı defterin şeklini birebir üretir (LONG baskın), ölçek ~3× (boyutlama).
Kararlar göreli farkla; canlı defter nihai hakem.
