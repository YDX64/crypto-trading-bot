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

## Lider piyasa kapısı (`SCALPER_MARKET_GATE`, D15) — ADAY, HENÜZ ONAYLANMADI
Liderin (varsayılan BTCUSDT) gün-içi sapmasına bakıp o yöne yeni giriş kapatan kapı
(ayrıntı: `docs/ARCHITECTURE.md` §4.1, ölçüm: `docs/EXPERIMENTS.md` E7). **Varsayılan
kapalı ve canlıya UYGULANMADI** — bu bölüm, kullanıcı onayı geldiğinde doğru değerlerle
açılabilmesi içindir; onaysız açma.

⚠️ **Yine de çıplak varsayılanlara GÜVENME.** `config.py` varsayılanları 2026-08-23'te
ölçümün önerdiği çifte çekildi (`DAY_PCT=1.3`, `RUN_PCT=0` — bkz. D15 "Varsayılanlar"), yani
`SCALPER_MARKET_GATE=true`'yu tek başına yazmak artık ÖNCEKİ kadar tehlikeli değil. Ama
**varsayılan bir KONTROL değildir**: sunucudaki `.env` eski bir değer taşıyabilir (ör. daha önce
elle yazılmış `SCALPER_MARKET_GATE_RUN_PCT=15`) ve o zaman varsayılan hiç devreye girmez.
Aşağıdaki komut bu yüzden üç değişkeni de AÇIKÇA yazar ve restart'tan önce `assert` ile
geri-okur — okuduğun değer `.env`'in gerçeği, varsayılanın değil.

⚠️ **Uzama alt-kapısı (`RUN_PCT`) KAPALI kalmalı** — E7 (harness): yalnız ayı penceresinde
ve tek lider olayında tetikleniyor, gün-içi kapısının üstüne katkısı YOK. E8 (canlı defter,
7–22 Ağu): `RUN_PCT=15` 202 işlemin 35'inde tetikleniyor ve net **−152.7** ediyor (12 DOWN-günü
işlemini engelleyip +137.9 kurtarıyor, 23 UP-günü KAZANANINI engelleyip −290.6 kaybettiriyor).
Harness'ın "üç pencerede inert" hükmü BUGÜNKÜ piyasaya taşınmıyor: o pencerelerde BTC 3 günde
%15 koşmuyordu, şimdi koşuyor. Motor açılışta ayrıca WARNING basar ama **log bir KONTROL
değildir** (D14 review bulgusu #4 emsali) — değeri komutta açıkça `0` yaz.

**Açmak (yalnız kullanıcı onayıyla):**
```bash
ssh awa 'cd /opt/tradingbot-v2 && cp .env backups/env.bak-$(date +%Y%m%d)-marketgate && for kv in "SCALPER_MARKET_GATE=true" "SCALPER_MARKET_GATE_SYMBOL=BTCUSDT" "SCALPER_MARKET_GATE_DAY_PCT=1.3" "SCALPER_MARKET_GATE_RUN_PCT=0"; do k="${kv%%=*}"; { grep -q "^$k=" .env && sed -i "s|^$k=.*|$kv|" .env || echo "$kv" >> .env; }; done && ./.venv/bin/python -c "from src.core.config import settings as s; assert s.scalper_market_gate, \"KAPI AÇILMADI\"; assert s.scalper_market_gate_day_pct == 1.3, f\"DAY_PCT={s.scalper_market_gate_day_pct}\"; assert s.scalper_market_gate_run_pct == 0, f\"RUN_PCT={s.scalper_market_gate_run_pct} — uzama alt-kapısı KAPALI olmalı\"; print(\"gate=\", s.scalper_market_gate, \"day=\", s.scalper_market_gate_day_pct, \"run=\", s.scalper_market_gate_run_pct)" && supervisorctl restart tradingbot_v2'
```
`sed -i` eşleşme bulamazsa 0 ile çıkar — bu yüzden her anahtar `{ grep -q && sed || echo; }`
grubuyla yazılır ve restart'tan ÖNCE `assert`'li config geri-okuması yapılır (D14 review
bulgusu #4 ile aynı disiplin).

**ZORUNLU doğrulama — soak bu ÜÇÜ geçmeden BAŞLAMIŞ SAYILMAZ:**
1. Komutun kendi `assert`'leri restart'tan ÖNCE `gate= True day= 1.3 run= 0.0` basmalı
   (basmazsa `AssertionError` ile durur ve restart hiç çalışmaz).
   ⚠️ **Bu geri-okuma TEK BAŞINA yetmez.** `DAY_PCT`/`RUN_PCT` varsayılanları artık zaten
   `1.3`/`0`; yani `sed` sessizce HİÇBİR ŞEY yazmasa bile geri-okuma aynı değerleri basar ve
   YEŞİL görünür — D14 bulgusu #4'ün (sessizce başarısız olan `sed`) tam olarak bu biçimi.
   Bu yüzden `.env` SATIRLARININ varlığı ayrıca `grep` ile doğrulanır (tam satır eşleşmesi,
   dört anahtar):
```bash
ssh awa 'cd /opt/tradingbot-v2 && for kv in "SCALPER_MARKET_GATE=true" "SCALPER_MARKET_GATE_SYMBOL=BTCUSDT" "SCALPER_MARKET_GATE_DAY_PCT=1.3" "SCALPER_MARKET_GATE_RUN_PCT=0"; do grep -qxF "$kv" .env || { echo "EKSİK/YANLIŞ .env satırı: $kv"; exit 1; }; done && echo ".env 4/4 satır DOĞRU"'
```
2. Restart sonrası (~90 sn) — **`enabled` DEĞİL, `gate_effective` bak.** Kapı fail-open'dır:
   lider verisi gelmiyorsa (yanlış sembol, ağ) `enabled: true` görünür ama HİÇBİR KORUMA YOKTUR.
   `stale` da açıkça `false` olmalı: bayat bir görüntü kapının ÖLÜ olduğunu gizler.
```bash
ssh awa 'curl -sS http://127.0.0.1:9091/scalper/status' | python3 -c "
import sys, json
g = json.load(sys.stdin)['market_gate']
assert g['gate_effective'] is True, f'KAPI ETKİSİZ: {g}'
assert g['stale'] is False, f'LİDER GÖRÜNTÜSÜ BAYAT: {g}'
assert g['leader_ok'] is True and g['leader'] == 'BTCUSDT', g
assert g['thresholds'] == {'day_pct': 1.3, 'run_pct': 0.0, 'run_days': 3}, g
print('gate_effective=', g['gate_effective'], 'stale=', g['stale'],
      'age=', g['snapshot_age_sec'], 'leader=', g['leader'],
      'host=', g['leader_source_host'], 'day_open_source=', g['day_open_source'])"
```
   (`stale` ve `leader_ok` mantıken `gate_effective`'in İÇİNDEDİR — ayrı yazılmalarının sebebi
   arıza anında HANGİ şartın düştüğünü hata mesajından okuyabilmek. `thresholds` ise ayrı bir
   kontrol: `gate_effective` yalnız "en az bir eşik > 0" ister, DOĞRU eşikler olduğunu değil.)
3. Açılış logunda `🧭 Piyasa kapısı lideri doğrulandı: BTCUSDT` satırı olmalı; `⛔ PİYASA KAPISI
   DOĞRULANAMADI (degraded)` satırı OLMAMALI:
```bash
ssh awa "grep -E 'PİYASA KAPISI|kapısı lideri' /opt/tradingbot-v2/logs/bot.log | tail -5"
```

**Ne görürsün:**
- Restart loglarında `🧭 PİYASA KAPISI AÇIK — lider BTCUSDT, gün-içi %1.3, uzama %0.0/3g`.
  `RUN_PCT>0` bırakıldıysa ayrıca sert bir WARNING — görürsen komutu yanlış çalıştırmışsın.
- Engellenen her sinyalde `⛔ <SEMBOL>: piyasa kapısı — gün-içi sapma (BTCUSDT gün −1.42%,
  koşu +2.10%) nedeniyle LONG girişi engellendi (SCALPER_MARKET_GATE)`.
- `GET /scalper/status` → `market_gate`. Alanlar ve NE İŞE YARADIKLARI:

| Alan | Anlamı / ne zaman bakılır |
|---|---|
| `enabled` | `.env`'de kapı açık mı. **Koruma garantisi DEĞİLDİR.** |
| `gate_effective` | **Kapı GERÇEKTEN koruyor mu — tek bakılacak alan.** BEŞ şart birden: `enabled` + `leader_ok` + en az bir BAŞARILI görüntü (`last_ok_at`) + `stale: false` + en az bir eşik > 0. |
| `leader` / `leader_ok` | Lider sembol ve son veri denemesinin sonucu (`null` = hiç denenmedi). |
| `leader_source_host` | Lider mumlarının geldiği host. Testnet'te `testnet.binancefuture.com` — E7 mainnet'ten ölçtü, soak sayıları birebir kıyaslanamaz (D15 "Veri kaynağı paritesi"). |
| `thresholds` | Yürürlükteki EŞİKLER (`day_pct`/`run_pct`/`run_days`). Log banner'ı bir KONTROL değildir; eşiklerin doğruluğu buradan doğrulanır. |
| `stale` / `snapshot_age_sec` | Görüntü 2 × tarama aralığından eskiyse (ya da UTC günü döndüyse) `true` — o an kapı KÖRDÜR. Yaş saniye cinsindendir. |
| `day_drift_pct` / `run_drift_pct` | Kapının ŞU AN ÖLÇTÜĞÜ iki büyüklük (`null` = hesaplanamadı ya da BAYAT — 0.0 ile karıştırma). Eşik değil: eşikler `thresholds` altındadır. |
| `day_open_source` | `intraday_open` (gerçek 00:00 UTC açılışı) ya da günün ilk 15 dakikasında `prev_daily_close` — beklenen davranış, hata değil. Bayat görüntüde `null`. |
| `last_ok_at` | Son BAŞARILI lider çekimi (UTC). Uzun süre eskiyorsa kapı sessizce ölmüş demektir. |
| `last_error` / `last_failure_at` / `consecutive_failures` | Son hata metni, zamanı ve ÜST ÜSTE hata sayısı (0 = şu an sağlıklı). |
| `failures_total` | Süreç ömrü boyunca TOPLAM hata — toparlanmada SIFIRLANMAZ. Dönüşümlü (flapping) arıza yalnız `consecutive_failures`'a bakınca tertemiz görünür; soak değerlendirmesi bu sayaç olmadan yapılamaz. |
| `last_reason` / `last_block_at` | **SON ENGELLEME** — serbest geçişler bunu silmez. `null` ve `rejects` boşsa kapı hiç tetiklenmemiştir. |
| `rejects` | Süreç ömrü boyunca `market_gate_day` / `market_gate_run` sayaçları — soak sonunda tetik sayısını buradan oku. |

**Arıza: kapı açık ama `gate_effective: false`.** Kapı fail-open olduğu için girişler devam eder;
KORUMA yoktur. Sırayla bak:
1. `last_error` ne diyor? "bulunamadı" → `SCALPER_MARKET_GATE_SYMBOL` yanlış yazılmış
   (`BTCUSD` vb.). Düzelt + restart.
2. Ağ/418 hatası → `consecutive_failures` artıyor. Kapı `SCALPER_MARKET_GATE_RETRY_SEC`
   (vars. 60 sn) boyunca yeniden DENEMEZ (bilerek: boşa REST isteği + paylaşılan kline kilidi).
   Ban aktifken **restart YASAK** (CLAUDE.md yasak #3) — banın geçmesini bekle, kapı kendiliğinden
   toparlar (`leader_ok` true'ya döner, `last_error` null olur).
3. Log'da uyarılar tür başına dakikada en çok bir satırdır — az satır görmek "az hata" demek
   DEĞİLDİR; sayı `consecutive_failures`'tadır.

**Kapatmak:**
```bash
ssh awa 'cd /opt/tradingbot-v2 && cp .env backups/env.bak-$(date +%Y%m%d)-marketgate-off && { grep -q "^SCALPER_MARKET_GATE=" .env && sed -i "s/^SCALPER_MARKET_GATE=.*/SCALPER_MARKET_GATE=false/" .env || echo "SCALPER_MARKET_GATE=false" >> .env; } && ./.venv/bin/python -c "from src.core.config import settings as s; assert not s.scalper_market_gate, \"KAPI HÂLÂ AÇIK\"; print(\"gate=\", s.scalper_market_gate)" && supervisorctl restart tradingbot_v2'
```
Varsayılan zaten kapalı (satırı silmek de eşdeğerdir); kod geri alınmasına gerek yok.

## Güvenlik borçları
1. Webhook düz HTTP + IP, secret sorgu dizesinde. Erişim logu kısmı ÇÖZÜLDÜ (D9,
   2026-08-21): `uvicorn.access`/`uvicorn.error` logger'larına `secret=...`'ü `secret=***`
   yapan bir filtre eklendi (`src/main.py`). Açık kalan: TLS + log rotasyonu.
2. Futbol botu kodunda gömülü RapidAPI anahtarı (`/root/bots/LIVE1/main.py`) — ayrı proje, bilgi.
3. ~~Sunucu venv'i requirements.txt'in gerisinde~~ ÇÖZÜLDÜ (2026-08-21 17:46): yeni venv yanına kuruldu,
   541 testle doğrulandı, `mv` ile değiştirildi (cryptography 50, python-multipart 0.0.31, pytest 9).
   Geri alma: `mv .venv .venv-failed && mv .venv-old .venv && supervisorctl restart tradingbot_v2`.
   `.venv-old` 1 hafta sonra silinebilir. Reçete: yeni venv → test → swap → restart → sağlık yokla.
