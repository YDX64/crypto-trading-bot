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
⚠️ 2026-08-23'ten (D19) beri AYNI uç nokta ikinci bir yol taşıyor: gövdesinde
`kind=exit|choch|trend|tp1` olan istekler GİRİŞ OYU DEĞİLDİR, sağlamaya hiç girmez —
bkz. aşağıdaki "TV olay kanalı". Bu 49 alarmın hiçbirinin mesajında `kind` yoktur,
davranışları değişmedi.

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

## TV olay kanalı (`kind=exit|choch|trend|tp1`, D19/D19a) — allowlist, mod, doğrulama

**Ne yapar:** TradingView'in ÇIKIŞ ve YAPI/DÖNÜŞ alarmlarını (S&O `Exit Signal` /
`Trend Catcher-Tracer Up|Down`, PAC `Bullish/Bearish S-CHOCH`, AlgoPro `🎯 TP1 Hit`)
bota sokar. Bu alarmlar **giriş oyu DEĞİLDİR**: sağlamaya (TvConfluence) hiç girmez,
`state/tv_events.json` defterine yazılır. Alarm mesaj şablonları ve koşul adları:
`docs/INTEGRATIONS.md` §7.2. Varsayılan mod **`shadow`** — motor davranışı DEĞİŞMEZ.
⚠️ Mesaj biçimi kritiktir: `src=`/`kind=` belirteçleri mesajın **BAŞINDA** olmalı
(aşağıdaki "Tuzaklar"). D19a ile değişen semantikler için `docs/DECISIONS.md` D19a.

**1) Alarmları kur (TV'de, mevcut alarmı KLONLAYARAK):** webhook URL'sine DOKUNMA
(secret ve eski `?src=` kalsın); yalnız **koşulu** ve **mesajı** değiştir. Mesaj tek
satır düz metin olmalı ve `{{ticker}}` içermek ZORUNDA, ör.
`src=luxso_exit kind=exit {{ticker}}`.

**2) `src` allowlist'ini DOĞRULA:** kod varsayılanı dört olay kaynağını içerir, ama
`.env` bu değişkeni AÇIKÇA set ediyorsa varsayılan devreye GİRMEZ. ℹ️ **D19a'dan
sonra bu adım kanalı ÖLDÜRMEZ** — olay yolu allowlist'ten bağımsızdır (istek
`TV_WEBHOOK_SECRET` ile kimliklidir), eksik allowlist yalnız startup WARNING'i ve
`/scalper/status` → `tv_events.allowlist_ok=false` üretir. Yine de düzeltilmeli:
allowlist GİRİŞ yolunun sayım korumasıdır ve aynı etiket orada `tv`ye eşlenir.
```bash
ssh awa 'cd /opt/tradingbot-v2 && grep -n "^TV_SOURCE_ALLOWLIST=" .env || echo "SATIR YOK -> kod varsayilani gecerli, ekleme gerekmez"'
```
Satır VARSA (yedek + ekleme + restart öncesi geri-okuma):
```bash
ssh awa 'cd /opt/tradingbot-v2 && cp .env backups/env.bak-$(date +%Y%m%d)-tvevents && sed -i "s/^TV_SOURCE_ALLOWLIST=.*/&,luxso_exit,luxso_trend,pac_choch,algopro_tp1/" .env'
ssh awa 'cd /opt/tradingbot-v2 && ./.venv/bin/python -c "
from src.core.config import settings as s
have = {x.strip() for x in s.tv_source_allowlist.split(\",\")}
missing = {\"luxso_exit\",\"luxso_trend\",\"pac_choch\",\"algopro_tp1\"} - have
assert not missing, missing
print(\"allowlist OK\")"'
```

**3) Modu seç** (`off` | `shadow` | `active`) — gölge modun `sed` tuzağıyla AYNI grup
deseni (bkz. "Gölge modu"; `sed -i` eşleşme bulamazsa exit 0 verir, `||` sağı hiç
çalışmaz):
```bash
ssh awa 'cd /opt/tradingbot-v2 && cp .env backups/env.bak-$(date +%Y%m%d)-tvmode && { grep -q "^SCALPER_TV_EVENTS_MODE=" .env && sed -i "s/^SCALPER_TV_EVENTS_MODE=.*/SCALPER_TV_EVENTS_MODE=shadow/" .env || echo "SCALPER_TV_EVENTS_MODE=shadow" >> .env; }'
ssh awa 'cd /opt/tradingbot-v2 && ./.venv/bin/python -c "
from src.core.config import settings as s
assert s.scalper_tv_events_mode == \"shadow\", s.scalper_tv_events_mode
print(\"mode=\", s.scalper_tv_events_mode)" && supervisorctl restart tradingbot_v2'
```
`active`'e geçerken aynı komutlarda `shadow`→`active` yaz. ⚠️ **`active`'e yalnız gölge
ölçümü sonrası geçilir** (`docs/INTEGRATIONS.md` §7.6 terfi hattı) ve önce
`SCALPER_TV_EVENTS_EXIT=be` ile (yalnız stop sıkışır, geri alınabilir); `close`
(reduce-only MARKET kapanış) AYRI bir karardır.

Geçersiz değer startup'ta **ValueError** ile reddedilir
(`config._validate_tv_events_settings`) — yazım hatası sessizce `shadow`a DÜŞMEZ,
süreç hiç kalkmaz. Restart sonrası `supervisorctl status tradingbot_v2` FATAL
gösteriyorsa `logs/bot.log`'un ilk satırlarına bak.

**4) Doğrulama (restart'tan ~90 sn sonra):**
```bash
ssh awa 'curl -sS http://127.0.0.1:9091/scalper/status' | python3 -c "import sys,json; d=json.load(sys.stdin)['tv_events']; print('mode=',d['mode'],'exit=',d['exit_action'],'losing=',d['exit_losing']); print('gate_enabled=',d['gate_enabled'],'window_open=',d['window_open'],'allowlist_ok=',d['allowlist_ok'],d['allowlist_missing']); print('persist=',d['persist']['ok'],d['persist']['errors']); print(d['counters'])"
ssh awa 'grep -a "TV olayı\|TV yapı kapısı\|TV olay kanalı" /opt/tradingbot-v2/logs/bot.log | tail -20'
```
`allowlist_ok=false`, `gate_enabled=false`, `window_open=false` ya da
`persist.ok=false` görürsen kanal SESSİZ demektir — adım 2/3'e dön.

Elle sağlama **`?dry_run=1` ile** (secret'ı komut satırına YAZMA — `.env`'den oku).
⚠️ `dry_run` olmadan bu komut CANLI DEFTERE gerçek bir olay yazar ve `active` modda
açık pozisyonu etkileyebilir:
```bash
ssh awa 'cd /opt/tradingbot-v2 && S=$(grep ^TV_WEBHOOK_SECRET= .env | cut -d= -f2-) && curl -sS -X POST "http://127.0.0.1:9091/tv-signal?secret=$S&dry_run=1" -d "src=pac_choch kind=choch bearish BTCUSDT" | python3 -m json.tool'
```
Beklenen: `"routed": "event"`, `"kind": "choch"`, `"direction": "SHORT"`,
`"source": "pac_choch"`, `"dry_run": true`. **`"routed"` yoksa** (yanıtta `accepted`
varsa) istek GİRİŞ yoluna düşmüştür → mesajda `kind=` belirteci mesajın BAŞINDA
değildir (bkz. `docs/INTEGRATIONS.md` §7.1 "başlık koşusu").

**5) Olay defterini sıfırlama (yalnız gerektiğinde):** ⚠️ `state/tv_events.json`
dosyasını SİLMEK çalışan süreci temizlemez — defter RAM'de otoritedir ve bir
sonraki olayda dosyayı geri yazar. İki doğru reçete var:
```bash
# (a) süreci durdurmadan: reset uç noktası
ssh awa 'cd /opt/tradingbot-v2 && S=$(grep ^TV_WEBHOOK_SECRET= .env | cut -d= -f2-) && curl -sS -X POST "http://127.0.0.1:9091/tv-events/reset?secret=$S" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[\"reset\"], d[\"cleared_symbols\"], d[\"persisted\"])"'
# (b) bakım penceresinde: dosyayı sil + restart (SIRA ÖNEMLİ)
ssh awa 'cd /opt/tradingbot-v2 && rm -f state/tv_events.json && supervisorctl restart tradingbot_v2'
```
Defteri boşaltmak bir RİSK kapısını açmaz: en kötü sonucu kapı/çıkış tetiğinin
yeni olay gelene kadar sessizleşmesidir (fail-open, `docs/INTEGRATIONS.md` §7.4).

**Tuzaklar (D19a):**
- Bir çıkış alarmının mesajından `kind=` düşerse **ya da belirteçler mesajın
  BAŞINDA değilse** istek **422** alır (`olay kaynağı giriş oyu veremez`) — TV alarm
  günlüğünde "webhook failed" görürsün. Bu BİLİNÇLİDİR: alternatifi, o alarmın
  sessizce bir GİRİŞ OYUNA dönüşüp pozisyon açmasıydı. Çözüm: mesajı
  `src=… kind=… {{ticker}}` sırasına getir (şablonlar: INTEGRATIONS §7.2);
  alarmı SİLME.
- `Kind: ...` / `Source: ...` gibi düz yazı başlangıçları GİRİŞ alarmlarını
  bozmaz — `:` ayracı yalnız TANINAN bir değer taşıyorsa belirteç sayılır.
- `SCALPER_TV_EVENTS_MAX_AGE_MIN=0` ya da boş `SCALPER_TV_EVENTS_GATE_SOURCES`
  **KAPALI** demektir ("süresiz taze"/"tüm kaynaklar" DEĞİL). `MODE=active` iken
  kanal HİÇBİR ŞEY yapamıyorsa (pencere 0, ya da kapı kaynağı yok **ve** `EXIT=off`)
  süreç hiç kalkmaz (ValueError). Boş kapı kaynağı + `EXIT=be|close` GEÇERLİDİR
  ("giriş kapısı yok, yalnız açık çık komutlarına uy").
- `SCALPER_TV_EVENTS_EXIT=be` pozisyon **zarardayken uygulanmaz**; ne yapılacağını
  `SCALPER_TV_EVENTS_EXIT_LOSING=skip|close` seçer (varsayılan `skip`). Sayaçlar:
  `exits_skipped_losing` / `exits_closed_losing` / `exits_noop`.
- **Gölge ölçümünü okurken** `would_exit`in ham sayısı yanıltıcıdır: aktifte hiçbir
  şey olmayacak olaylar `would_exit_noop`tur. Gerçek etki `would_exit -
  would_exit_noop`tur (D19a-2). Aynı şekilde `exits_applied` yalnız borsaya istek
  gittiğinde artar; `exits_noop` dokunulmamış pozisyondur.
- Fiyat okuması bozulursa (`get_current_price` hatası) TV çıkışları **hiçbir şey
  yapmaz** ve olayı tüketmez — `exits_failed` artar, olay en fazla 3 turda yeniden
  denenir. Bu, bayat fiyatla stop'u ters tarafa koyup acil kapanış tetiklememek
  içindir.
Beklenen: `"routed": "event"`, `"kind": "choch"`, `"structure": "BEAR"` ve **hiçbir işlem
açılmaması** (yanıtta `accepted` alanı YOKTUR — olay yolu sağlamaya hiç girmez).

**Ne görürsün:**
- Her olayda: `🧭 TV olayı: <SEMBOL> kind=… dir=… ← <src>`
- Gölgede: `👻 <SEMBOL>: TV yapı kapısı GÖLGE — …; aktif olsaydı LONG girişi engellenecekti`
  ve `👻 <SEMBOL>: TV olay çıkışı GÖLGE — exit ← luxso_exit; aktif olsaydı 'be' uygulanacaktı`
- Aktifte: `⛔ … TV yapı kapısı — … engellendi` + `/scalper/status` →
  `entry_rejects.tv_structure_gate`; ve `🛡️ … SL ücret-dahil BE'ye çekildi`.

**Arıza/geri alma:**
- Kanalı anında sustur: `.env` `SCALPER_TV_EVENTS_MODE=off` + restart (alarmlar gelmeye
  devam eder, yalnız deftere yazılır; motor hiç bakmaz).
- `state/tv_events.json` bozulursa **fail-OPEN**: bot boş durumla devam eder ve WARNING
  loglar — girişler DURMAZ, pozisyon KAPANMAZ. ⚠️ `state/risk_event_halt.json`'ın
  fail-CLOSED davranışıyla KARIŞTIRMA. Dosyayı silmek güvenlidir ve restart gerektirmez.
- Olay geldiği halde kapı/çıkış çalışmıyorsa sırayla bak: (a) `tv_events.mode`,
  (b) `gate_sources` o kaynağı içeriyor mu, (c) olayın `age_s` < `max_age_minutes`,
  (d) `structures` altında beklenen `src` var mı — yoksa adım (2) atlanmıştır ve kaynak
  `tv`/eski `?src=` değerine eşlenmiştir.

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
