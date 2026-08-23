# RUNBOOK — işletme el kitabı (testnet canlı süreç)

## Kimlik kartı
| | |
|---|---|
| Sunucu | `awa` (ssh alias), `/opt/tradingbot-v2` |
| Süreç | supervisord `tradingbot_v2` → `.venv/bin/python -m uvicorn src.main:app --host 127.0.0.1 --port 9091` |
| Ağ | Binance Futures **TESTNET** (mainnet halkası henüz yok) |
| Komutlar | `supervisorctl status|restart|pid tradingbot_v2` |
| Loglar | `logs/bot.log` (uygulama) · `logs/supervisor.log` (erişim — **secret içerir**) · `logs/deploy.log` |
| Cron | `tradingbot-v2-watchdog.sh` (her dk) · `tradingbot-v2-backup.sh` (05:17) |
| Dashboard | `ssh -L 9091:127.0.0.1:9091 awa` → http://127.0.0.1:9091 (launchd `com.awa.tradingbot.tunnel`) |
| ⚠️ Tuzak | `systemctl` altındaki `live-bot.service` **futbol botudur**; `info-bot.service` Telegram info botu. Trading botu systemd'de değil. |

## Günlük kontrol (2 dk)
```bash
ssh awa 'supervisorctl status tradingbot_v2; tail -3 /opt/tradingbot-v2/logs/bot.log | cut -c1-160'
ssh awa 'cd /opt/tradingbot-v2 && ./.venv/bin/python - <<PY
import sqlite3; c=sqlite3.connect("tradingbot.db").cursor()
print("açık:", c.execute("SELECT symbol,direction FROM scalp_trades WHERE status=\"OPEN\"").fetchall())
print("bugün:", c.execute("SELECT COUNT(*),SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END),ROUND(SUM(realized_pnl),2) FROM scalp_trades WHERE status=\"CLOSED\" AND date(closed_at)=date(\"now\")").fetchone())
PY'
```
Haftalık: `scripts/ledger_report.py` canlı defteri rejime böler (BTC günlük % → UP/FLAT/DOWN),
yön/çıkış-nedeni/sembol/gün kırılımlarını ve `docs/MAINNET_PLAN.md` §2.3 soak kontrol listesini
(PASS/FAIL — hüküm vermez, insan karar verir) yazdırır. Elle SQL yazmaya gerek yok:
```bash
python3 scripts/ledger_report.py --since "2026-08-14 00:00" --format md   # son 7 gün varsayılan
```
Sunucuda (ör. D6 soak, 2026-08-21 12:35 UTC'den beri):
```bash
ssh awa 'cd /opt/tradingbot-v2 && .venv/bin/python scripts/ledger_report.py --since "2026-08-21 12:35" --format md'
```
Ağ erişimi yoksa/istenmiyorsa `--btc-klines-json <dosya>` ile Binance kline dizisi biçiminde
çevrimdışı veri verilebilir. Çıktı biçimleri `text|md|json` (`--format`), dosyaya yazmak için
`--out`. "Kazanıyor" yalnız üç rejimde de doğruysa (checklist'in rejim satırları PASS/N/A)
söylenir — bkz. `docs/EXPERIMENTS.md` "rejim analizi" kalıbı (script bu kalıbı otomatikleştirir).

## Deploy ve geri alma
```bash
scripts/deploy.sh awa                    # push edilmiş main → test → restart → sağlık → başarısızsa otomatik geri al
DEPLOY_NO_RESTART=1 scripts/deploy.sh awa    # dokümantasyon/harness değişikliği (süreç etkilenmez)
scripts/deploy.sh awa <commit>           # elle geri alma; önceki commit backups/commit.prev-<tarih>
```
Deploy ön koşulları (script kendisi denetler): entry-halt dosyası yok, son 15 dk ban izi yok,
temiz ağaç, yerel HEAD == origin/main. `.env` değişikliği deploy'dan AYRI bir adımdır:
```bash
ssh awa 'cd /opt/tradingbot-v2 && cp .env backups/env.bak-$(date +%Y%m%d)-<etiket> && sed -i "s/^ANAHTAR=.*/ANAHTAR=deger/" .env && ./.venv/bin/python -c "from src.core.config import settings as s; print(s.<alan>)" && supervisorctl restart tradingbot_v2'
```
Restart'ı kanıtla: `ps -o etimes= -p $(supervisorctl pid tradingbot_v2)` küçük olmalı.
**Açılış süresi:** port 9091, restart'tan ~90 sn sonra açılır (Binance init + pozisyon devralma); deploy
script'i bu yüzden 240 sn'ye kadar yoklar (`HEALTH_TIMEOUT`). 2026-08-21'de 30 sn'lik sabit bekleme
yanlış alarmla otomatik geri alma tetikledi — mekanizma doğru çalıştı, eşik düzeltildi.

### Mainnet halkası
Durum: pipeline hazır, dizin/program HENÜZ YOK (`/opt/tradingbot-main` bir insan tarafından
kurulmadan mainnet deploy'u çalışmaz — bkz. `docs/MAINNET_PLAN.md` §1, §5). `scripts/deploy.sh`
`--ring mainnet` ile çağrıldığında testnet'ten AYRI dizin/program/port/sağlık uç noktasını
kullanır ve yalnız etiketli (`vX.Y.Z`) sürümleri kabul eder:
```bash
scripts/deploy.sh awa v1.2.0 --ring mainnet
# ── MAİNNET DEPLOY ONAYI ──────────────────────────────
#   halka  : mainnet
#   host   : awa
#   hedef  : v1.2.0
#   repo   : /opt/tradingbot-main
#   program: tradingbot_main
# Onaylamak için 'MAINNET' yazın: MAINNET
```
Otomasyon (ör. CI) için onay istemini atla: `DEPLOY_CONFIRM=MAINNET scripts/deploy.sh awa v1.2.0 --ring mainnet`.
Reddedilen durumlar: hedef `origin/main` veya `vX.Y.Z` biçiminde olmayan çıplak bir commit ise,
ya da etiket origin'e push edilmemişse — script deploy'u başlatmadan iptal eder.

`scripts/server_deploy.sh` tarafında `RING=mainnet` iki şey yapar: log satırlarına `[ring=mainnet]`
ekler (rollback mantığı iki halka için ORTAK, değişmez) ve ekstra bir ön kontrol çalıştırır — `.env`
içinde `RISK_EVENT_SECRET` ve `TV_WEBHOOK_SECRET` dolu, `SCALPER_ENTRY_HALT_ENABLED=true` değilse
deploy Türkçe bir hata mesajıyla reddedilir (bkz. `docs/MAINNET_PLAN.md` §3).

İki halkanın `.env`'i arasındaki `SCALPER_*`/`TV_*`/`RISK_*` farkını (secret DEĞERLERİ maskelenerek)
görmek için salt okunur yardımcı:
```bash
scripts/ring_env_diff.sh awa
```

**Mainnet dizini/programı kurulduğunda** (insan tarafından, bkz. `docs/MAINNET_PLAN.md` §5 madde 4),
supervisord'a eklenecek program tanımı (testnet'in `tradingbot_v2` programının eşleniği — port 9092,
ayrı log yolu):
```ini
[program:tradingbot_main]
directory=/opt/tradingbot-main
command=/opt/tradingbot-main/.venv/bin/python -m uvicorn src.main:app --host 127.0.0.1 --port 9092
autostart=false        ; ilk kurulumda kapalı — go/no-go sonrası elle açılır
autorestart=true
stopsignal=TERM
stopasgroup=true
killasgroup=true
user=<sunucu-kullanıcısı>
environment=PYTHONUNBUFFERED="1"
stdout_logfile=/opt/tradingbot-main/logs/supervisor.log
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=5
redirect_stderr=true
```
Uygulamanın kendi log dosyası (`logs/bot.log`, testnet'teki gibi) çalışma dizini `/opt/tradingbot-main`
olduğu için otomatik olarak ayrı olur — testnet'in `logs/`'ıyla KARIŞMAZ.

## Arızalar
**Binance 418 / ban:** `logs/bot.log`'da `HTTP 418|banned|devre kesici`. Ban aktifken restart
**YASAK** (ban süresini uzatır). Kök nedenler ve çözümler: rate limiter kilidi (mevcut), dashboard
force-fresh açlığı (düzeltildi), ağırlık başlığı testnet'te tutarsız (ortalama ~2.7k görünür,
gerçek 429 yoksa gürültü). Bekle; ban bitince önce `wait_for_binance` loglarını izle.

**Entry-halt (`state/scalper_entry_halt.json`):** güvenlik kilidi, fail-closed. Açmak = nedeni
anla → dosyayı `.cleared-<tarih>` diye yeniden adlandır → restart. Recover sonrası kapanış
kayıtları (`exit_reason=UNKNOWN`) güvenilmezdir; PnL'i `binance_income_net` ile doğrula.
⚠️ Bununla KARIŞTIRMA: `state/risk_event_halt.json` AYRI bir dosyadır (haber/olay botu kanalı,
D10, bkz. "Risk-olayı kanalı" bölümü) — restart GEREKMEZ, `POST /risk-event action=resume`
veya dosyayı silmek yeterlidir.

**Degraded ama hata yok + tarama bayat:** dashboard açıkken /api/status force-fresh çağrısı
limiter'ı doyuruyordu (düzeltildi). Belirti imzası aynıysa önce dashboard'u kapat.

**Pozisyon korumasız uyarısı / -4120:** koşullu emirler `/fapi/v1/algoOrder`'da; `openOrders`
onları göstermez, `allOpenOrders` iptal etmez.

**Yanlış servis restart'ı:** `systemctl restart live-bot` trading botuna dokunmaz (futbol botu).
Daima `supervisorctl`.

## Takvim
- **2026-09-14:** TradingView alarmları (49 adet) expire oluyor → yenile; aynı anda webhook
  secret rotasyonu + erişim logu maskesi + alan adı/TLS işiyle birleştir.
- **2026-11-21:** LuxAlgo aboneliği "iptal ediliyor" görünüyor — kullanıcı kararı.
- Her .env değişikliği → `docs/DECISIONS.md` satırı + yedek dosyası.

## Sinyal hattı (TradingView)
49 alarm → `POST /tv-signal?secret=…&src=<kaynak>` (luxosc / luxso / botv3; AlgoPro gövde
parmak iziyle) → `TvConfluence.vote` (2 farklı kaynak, 420 sn, ters oy sıfırlar) →
`engine.external_signal` → aynı risk/çıkış kuralları. `?src` serbest metindir; 2026-08-21'den
(D9) beri `TV_SOURCE_ALLOWLIST`'e karşı doğrulanır — bilinmeyen değer "tv"ye eşlenir ve
WARNING loglanır (sessiz hayalet kaynak artık yok, ama sinyal yine de reddedilmez).
TV Desktop MCP ile ölçüm reçetesi: awa-brain `ops-gotchas/tradingview-desktop-mcp-luxalgo.md`.

## Risk-olayı kanalı (haber/olay botları, D10)
`POST /risk-event` — TV webhook'undan AYRI amaç (yön DEĞİL, giriş durdur/devam et/her-şeyi-
kapat). Ayrı secret: `.env`'de `RISK_EVENT_SECRET` (boş = 503 ile kapalı). Durum dosyası
`state/risk_event_halt.json` — `state/scalper_entry_halt.json`'dan AYRI, `SCALPER_ENTRY_HALT_ENABLED`
bayrağından bağımsız her zaman uygulanır (ayrıntı: `docs/INTEGRATIONS.md` §3).

**Durumu gör:**
```bash
curl -sS -X POST "http://127.0.0.1:9091/risk-event?secret=$RISK_EVENT_SECRET" \
  -H 'Content-Type: application/json' -d '{"action": "status"}' | python3 -m json.tool
```

**Girişleri durdur (manuel/haber botu — 60 dk, sebep zorunlu):**
```bash
curl -sS -X POST "http://127.0.0.1:9091/risk-event?secret=$RISK_EVENT_SECRET" \
  -H 'Content-Type: application/json' \
  -d '{"action": "halt", "reason": "manuel bakım", "source": "ops", "ttl_minutes": 60}'
```

**Durdurmayı kaldır (halt dosyası süresinden ÖNCE kalksın):**
```bash
curl -sS -X POST "http://127.0.0.1:9091/risk-event?secret=$RISK_EVENT_SECRET" \
  -H 'Content-Type: application/json' -d '{"action": "resume"}'
```

**Acil: tüm açık scalper pozisyonlarını kapat + girişleri durdur:**
```bash
curl -sS -X POST "http://127.0.0.1:9091/risk-event?secret=$RISK_EVENT_SECRET" \
  -H 'Content-Type: application/json' \
  -d '{"action": "flatten", "reason": "borsa arızası", "source": "ops", "ttl_minutes": 240}'
```
`flatten` yanıtındaki `flattened`/`errors` listelerini kontrol et — `errors`'a düşen sembol
borsa üzerinde kapanışı DOĞRULANAMADI demektir (SL/TP dokunulmadan izlemede kalır), elle
`get_position_risk`/dashboard ile bak. Halt her durumda (pozisyon olsun olmasın) kurulur.

**Elle kurtarma (API'ye erişilemiyorsa):** `state/risk_event_halt.json`'ı sil (veya
`.cleared-<tarih>` diye yeniden adlandır) — restart GEREKMEZ, motor dosyayı ~1sn TTL
önbellekle her okuduğunda taze değerlendirir. Bozuk/parse edilemeyen dosya fail-closed HALT
sayılır (dosyayı silmek = resume; sil-öncesi neden loga bakılmalı).

## Gölge modu (`SCALPER_SHADOW_MODE`, D14)
Yeni bir parametreyi veya (ileride) mainnet'in kendisini gerçek parayla riske girmeden
gözlemlemek için: sinyaller BUGÜNKÜ GİBİ tüm kapılardan (cooldown/rejim/kapasite/confluence/
stop-R:R/boyutlama) geçer ama `ScalpExecutor.try_open` margin/leverage ayarından ve emir
göndermeden ÖNCE döner — borsaya HİÇBİR istek gitmez. Defter satırları artık canlıyla
KIYASLANABİLİR (adversarial review sonrası düzeltme, aşağıdaki "Ne görürsün"e bakın): aynı
sembol tekilleştirme penceresi (`SCALPER_SHADOW_DEDUP_MINUTES`, boşsa
`SCALPER_LOSS_COOLDOWN_MINUTES`'e, o da yoksa 60 dk'ya düşer) içinde ikinci kez yazılmaz VE
gölge kapasitesi `SCALPER_MAX_POSITIONS`'a karşı sayılır — yani bir sinyal olayı ≈ bir satır,
tıpkı canlının bir işlem açması gibi.

⚠️ **`sed -i` eşleşme bulamazsa 0 ile çıkar** — `sed ... || echo ... >> .env` şeklindeki eski
tek satırlık komut bu yüzden YANLIŞTI: `.env`'de `SCALPER_SHADOW_MODE=` satırı YOKSA `sed`
sessizce hiçbir şey değiştirmez (exit 0), `||`'nin sağı hiç ÇALIŞMAZ ve `&&` zinciri restart'a
kadar devam eder — bot GERÇEK emir göndermeye devam ederken operatör gölge modun açıldığını
sanır (adversarial review, CONFIRMED, sunucuda tekrar üretildi). Aşağıdaki komutlar `{ grep -q
... && sed ... || echo ...; }` grubu + restart'tan ÖNCE `assert`'li bir config geri-okuması ile
bu sınıfı kapatır — RUNBOOK'un genel `.env` değişikliği kalıbıyla (yukarıdaki "Deploy ve geri
alma" bölümü) aynı disiplin.

**Açmak:**
```bash
ssh awa 'cd /opt/tradingbot-v2 && cp .env backups/env.bak-$(date +%Y%m%d)-shadow && { grep -q "^SCALPER_SHADOW_MODE=" .env && sed -i "s/^SCALPER_SHADOW_MODE=.*/SCALPER_SHADOW_MODE=true/" .env || echo "SCALPER_SHADOW_MODE=true" >> .env; } && ./.venv/bin/python -c "from src.core.config import settings as s; assert s.scalper_shadow_mode, \"GÖLGE AÇILMADI — .env yazılmadı\"; print(\"shadow_mode=\", s.scalper_shadow_mode)" && supervisorctl restart tradingbot_v2'
```
**ZORUNLU doğrulama — soak bu ikisi geçmeden BAŞLAMIŞ SAYILMAZ:**
1. Yukarıdaki komutun kendi `assert`'i restart'tan ÖNCE `shadow_mode= True` basmalı (basmazsa
   komut zaten `AssertionError` ile durur ve restart hiç çalışmaz — bu satırın amacı tam olarak
   bu).
2. Restart sonrası (bkz. "Açılış süresi" ~90 sn): `curl -sS http://127.0.0.1:9091/scalper/status
   | python3 -c "import sys,json; assert json.load(sys.stdin)['shadow_mode'] is True"` — sunucu
   tarafında ayrı bir doğrulama; adım 1 yalnız `.env`'in doğru yazıldığını kanıtlar, bunu
   çalışan süreç doğrular.

**Ne görürsün:**
- Restart loglarında `⚠️ GÖLGE MODU AÇIK — emir gönderilmez` (yüksek sesle, WARNING).
- Her gölge sinyalinde `logs/bot.log`'da `👻 GÖLGE: <SEMBOL> <YÖN> @<fiyat> (<gerekçe>)`.
- `GET /scalper/status` → `shadow_mode: true`.
- `sqlite3 tradingbot.db "SELECT symbol,direction,entry_price FROM scalp_trades WHERE status='SHADOW' ORDER BY id DESC LIMIT 20"` — gerçek emir YOK, yalnız kayıt.
- `GET /scalper/stats` ve `GET /scalper/trades` (varsayılan) SHADOW satırlarını GÖSTERMEZ
  (istatistik/PnL anlamı yok — hiç emir gitmedi); görmek için `GET /scalper/trades?include_shadow=1`.
- Cooldown ETKİLENMEZ: gölge sinyaller yeni cooldown BAŞLATMAZ (mevcut bir cooldown varsa onu
  yine de bugünkü gibi RESPECT eder). Kapasite ETKİLENİR (yukarıya bakın): tekilleştirme
  penceresindeki sembol sayısı `SCALPER_MAX_POSITIONS`'a karşı `open + shadow_active` olarak
  sayılır — dolunca `👻 <SEMBOL>: GÖLGE kapasite dolu` loglanır ve o sinyal deftere yazılmaz.

**Kapatmak:**
```bash
ssh awa 'cd /opt/tradingbot-v2 && cp .env backups/env.bak-$(date +%Y%m%d)-shadow-off && { grep -q "^SCALPER_SHADOW_MODE=" .env && sed -i "s/^SCALPER_SHADOW_MODE=.*/SCALPER_SHADOW_MODE=false/" .env || echo "SCALPER_SHADOW_MODE=false" >> .env; } && ./.venv/bin/python -c "from src.core.config import settings as s; assert not s.scalper_shadow_mode, \"GÖLGE HÂLÂ AÇIK — .env yazılmadı\"; print(\"shadow_mode=\", s.scalper_shadow_mode)" && supervisorctl restart tradingbot_v2'
```
Varsayılan zaten kapalı (satırı silmek de eşdeğerdir). Restart sonrası aynı iki doğrulamayı
(python `assert` + `GET /scalper/status`) `shadow_mode: false` bekleyerek tekrarla. ⚠️
Mainnet'te (testnet DEĞİLKEN) gölge KAPALIYSA `RISK_EVENT_SECRET`, `TV_WEBHOOK_SECRET` ve
`SCALPER_SYMBOL_ALLOWLIST` boş (veya yalnız boşluk/virgül) OLAMAZ — `_validate_binance_environment`
startup'ta reddeder (docs/MAINNET_PLAN.md §5.3); doldurmadan kapatamazsın.

## Kline kaynağını mainnet'e alma (`SCALPER_MARKET_DATA_BASE_URL`, D17)
Ne yapar: YALNIZ public `/fapi/v1/klines` çekimi verilen host'tan yapılır. Emir, bakiye,
pozisyon, evren taraması (`ticker/24hr`), `exchangeInfo` ve income `BINANCE_BASE_URL`'de
KALIR — API anahtarı bu host'a asla gitmez. Amaç: testnet'te işlem yaparken RSI/Bollinger/
diverjans/rejim/ATR'yi GERÇEK piyasa mumlarından hesaplamak ve backtest harness'iyle (zaten
mainnet) aynı veriye oturmak. Ayrıntı: `docs/DECISIONS.md` D17.

⚠️ **Bu bir soak değişikliğidir**, ayar değil sinyal etkiler: D6+D16 soak'ı sürerken AÇMA
(değişiklikler üst üste bindirilirse atıf bulanıklaşır — bkz. D11 notu). Ayrı host
kullanırken `SCALPER_SYMBOL_ALLOWLIST` dolu olsun (işlem host'unda olup mainnet'te olmayan
bir sembol her taramada kline hatası üretir).

⚠️ `sed -i` eşleşme bulamazsa 0 ile çıkar (gölge modu bölümündeki tuzağın aynısı) — bu yüzden
`{ grep -q ... && sed ... || echo ...; }` grubu + restart'tan ÖNCE `assert`'li geri-okuma:
```bash
ssh awa 'cd /opt/tradingbot-v2 && cp .env backups/env.bak-$(date -u +%Y%m%d-%H%M%S)-klinesrc && { grep -q "^SCALPER_MARKET_DATA_BASE_URL=" .env && sed -i "s#^SCALPER_MARKET_DATA_BASE_URL=.*#SCALPER_MARKET_DATA_BASE_URL=https://fapi.binance.com#" .env || echo "SCALPER_MARKET_DATA_BASE_URL=https://fapi.binance.com" >> .env; } && ./.venv/bin/python -c "from src.core.config import settings as s; assert s.kline_source == \"separate\", \"KLINE KAYNAĞI DEĞİŞMEDİ — .env yazılmadı\"; print(\"market_data=\", s.market_data_base_url, \"| trading=\", s.binance_base_url)" && supervisorctl restart tradingbot_v2'
```
(Yedek damgası saat-dakika-saniye içerir — `server_deploy.sh`'nin `STAMP` deseni:
`date +%Y%m%d` kullanılsaydı aynı gün ikinci koşu TEMİZ yedeği ezerdi ve ertesi gün
"aynı-gün yedeği" hiç bulunmazdı.)
**ZORUNLU doğrulama — üçü geçmeden değişiklik YAPILMIŞ SAYILMAZ:**
1. Yukarıdaki komutun kendi `assert`'i restart'tan ÖNCE `market_data= https://fapi.binance.com |
   trading= https://testnet.binancefuture.com` basmalı (basmazsa komut `AssertionError` ile durur,
   restart hiç çalışmaz).
2. Restart sonrası (~90 sn) log satırı:
   `ssh awa 'grep "📡 Kline kaynağı" /opt/tradingbot-v2/logs/bot.log | tail -1'` (SON satır —
   `-m1` kullanma, dosyadaki İLK/eski restart'ı gösterir) →
   `📡 Kline kaynağı: fapi.binance.com (AYRI — emirler: testnet.binancefuture.com)`.
3. Çalışan süreçten:
   ```bash
   curl -sS http://127.0.0.1:9091/scalper/status | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['kline_source']=='separate'; print(d['market_data_base_url'], d['trading_base_url'])"
   ```
İlk saat: `logs/bot.log`'da `Kline çekme hatası` (bilinmeyen sembol) ve `🚫 Piyasa verisi IP ban`
satırı OLMAMALI. Ban görülürse ayarı geri al — mainnet IP banı gelecekteki mainnet ticaretini de
vurur.
ℹ️ Yan etki (bilinçli): public ban satırı `HTTP 418` içerdiği için `scripts/server_deploy.sh`'nin
"son 15 dk'da ban izi" kilidi MAİNNET VERİ banında da deploy'u reddeder — testnet emirleri
etkilenmemiş olsa bile. Yanlış-pozitif tarafta kalmak bilinçli tercihtir; acil deploy gerekiyorsa
önce ayarı geri al, 15 dk bekle.

**Geri alma (tek satır, restart dahil — YEDEK DOSYASINA BAĞLI DEĞİL):**
```bash
ssh awa 'cd /opt/tradingbot-v2 && cp .env backups/env.bak-$(date -u +%Y%m%d-%H%M%S)-klinesrc-off && { grep -q "^SCALPER_MARKET_DATA_BASE_URL=" .env && sed -i "s#^SCALPER_MARKET_DATA_BASE_URL=.*#SCALPER_MARKET_DATA_BASE_URL=#" .env || true; } && ./.venv/bin/python -c "from src.core.config import settings as s; assert s.kline_source == \"trading_host\", \"KLINE KAYNAĞI HÂLÂ AYRI — .env yazılmadı\"; print(\"kline_source=\", s.kline_source)" && supervisorctl restart tradingbot_v2'
```
Bilinçli olarak `cp backups/env.bak-...` KULLANILMAZ: soak günlerce sürer, "bugünün"
yedeği ertesi gün yoktur ve acil geri alma tam da o anda `cp: No such file` ile ölürdü.
Satırı boşaltmak = varsayılan (kapalı); silmek de eşdeğerdir. Restart sonrası aynı üç
doğrulamayı `trading_host` bekleyerek tekrarla.

## Güvenlik borçları
1. Webhook düz HTTP + IP, secret sorgu dizesinde. Erişim logu kısmı ÇÖZÜLDÜ (D9,
   2026-08-21): `uvicorn.access`/`uvicorn.error` logger'larına `secret=...`'ü `secret=***`
   yapan bir filtre eklendi (`src/main.py`). Açık kalan: TLS + log rotasyonu.
2. Futbol botu kodunda gömülü RapidAPI anahtarı (`/root/bots/LIVE1/main.py`) — ayrı proje, bilgi.
3. ~~Sunucu venv'i requirements.txt'in gerisinde~~ ÇÖZÜLDÜ (2026-08-21 17:46): yeni venv yanına kuruldu,
   541 testle doğrulandı, `mv` ile değiştirildi (cryptography 50, python-multipart 0.0.31, pytest 9).
   Geri alma: `mv .venv .venv-failed && mv .venv-old .venv && supervisorctl restart tradingbot_v2`.
   `.venv-old` 1 hafta sonra silinebilir. Reçete: yeni venv → test → swap → restart → sağlık yokla.

## A-plus risk paketi (D16) — 02:56 sunucu saati (00:56 UTC) uygulandı, **03:10 sunucu saati (01:10 UTC) GERİ ALINDI** (kullanıcı kararı: ayar değil sinyal)
Canlı taban yeniden 10/50/10/10. Aşağıdaki satırlar tarihçe içindir; geçerli değerler `.env`.
`SCALPER_MAX_MARGIN_PCT=5`, `SCALPER_FIXED_STOP_ROI_PCT=40`, `SCALPER_TP1_ROI=8`,
`SCALPER_DAILY_LOSS_LIMIT_PCT=6` (önce 10/50/10/10). Yeni giriş: stop = 20x'te %2.0 fiyat, SL =
sermayenin %2'si; günlük kesici ≈ 3 net SL. Geri alma:
`cp backups/env.bak-20260823-025623-riskpaketi .env && supervisorctl restart tradingbot_v2`
(+240 sn sağlık). Soak raporu: `scripts/ledger_report.py --since "2026-08-23 02:57"`.
Backtest/autoresearch tabanı: `scripts/.scalper_env_snapshot.txt` güncellendi — eski sayılarla
(E4/E5/E6 tabanı) karşılaştırırken ölçek farkını (marj %10→5 = PnL/DD ×0.5) hesaba kat.
