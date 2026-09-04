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
En hızlı yol panonun üst şeridi: **Sistem durumu** (D22) — Kapı, Kline kaynağı, Günlük kesici,
REST ağırlığı, TV olayları, Post-mortem kuyruğu tek satırda. Şerit MEVCUT `/scalper/status`
çağrısından beslenir, yeni istek açmaz. Terminalden aynı bilgi:
```bash
curl -s localhost:9091/scalper/status | python3 -c 'import json,sys; d=json.load(sys.stdin); print({k:d.get(k) for k in ("entries_blocked_by","kill_switch_active","kline_source","scan_status")}, d.get("rest_weight"), d.get("market_gate",{}).get("stale_reason"))'
```
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

## Bir işlemi nasıl incelerim (işlem adli kaydı, D21)

Soru: "bu kayıp neden oldu?" — artık `bot.log` taramaya gerek yok. Her işlem
için giriş ve çıkış ANINDA bilinen her şey kaydedilir (bkz.
`docs/ARCHITECTURE.md` §5.1, `docs/DECISIONS.md` D21). **Bu kayıt yalnız
gözlemdir; hiçbir kapı/boyutlama/çıkış kararını etkilemez.**

**1) Panodan (en hızlı yol).** `/dashboard` → "Son İşlemler" tablosunda bir
satıra TIKLA → altında adli kart açılır: üstte büyük K/Z + etiket rozetleri,
sonra üç blok — "Neden girildi" (sinyal, RSI/BB/diverjans/ATR, rejim,
EMA50/200, BTC gün sapması, kapılar ✓/✗), "Nasıl çıkıldı" (giriş → TP1 → BE →
trailing → çıkış zaman çizgisi + MAE/MFE çubuğu), "Ne ters gitti" (kayma,
sinyal→dolum gecikmesi, brüt/net, ücret, R:R, post-mortem). Satırın "Etiket"
sütunu boşsa kural tabanlı bir kusur bulunamamıştır.

**2) Uçlardan (JSON).**
```bash
ssh awa 'curl -s localhost:9091/scalper/trades/152/forensics | python3 -m json.tool'
ssh awa 'curl -s "localhost:9091/scalper/forensics/recent?limit=20"'
ssh awa 'curl -s "localhost:9091/scalper/forensics/summary?since=7d" | python3 -m json.tool'
```
`summary` "neler etkiliyor" sorusunun cevabıdır: her etiket için işlem
sayısı/WR/PnL. `_etiketsiz_` satırı KIYAS TABANIDIR; `without_forensics` ise
"ölçülmemiş" işlem sayısıdır (etiketsiz ≠ temiz).

**3) Olay akışından (`jq` ile).** `logs/trades.jsonl` append-only, satır başına
tek JSON (`event` = `entry`/`exit`/`postmortem`), günlük rotasyonlu, 30 gün:
```bash
ssh awa 'cd /opt/tradingbot-v2 && jq -c "select(.event==\"exit\" and (.verdict|index(\"noise_stop\")))" logs/trades.jsonl | tail -5'
ssh awa 'cd /opt/tradingbot-v2 && jq -r "select(.trade_id==152)" logs/trades.jsonl'
```

**4) Rapordan (haftalık).** `--forensics` etiket × sonuç bölümünü ekler:
```bash
ssh awa 'cd /opt/tradingbot-v2 && .venv/bin/python scripts/ledger_report.py --since "2026-08-23" --forensics --format md'
```

**Tuzaklar:**
- **"Etiketsiz" ≠ "temiz".** D21 öncesi kapanan işlemlerin `forensics` sütunu
  NULL'dur; raporda `_etiketsiz_` satırına düşerler. Notlar bölümü kaç işlemin
  ölçülmediğini yazar — hüküm verirken bunu düş.
- **Post-mortem gecikmelidir.** `noise_stop` etiketi kapanıştan
  `SCALPER_FORENSICS_POSTMORTEM_MIN` (vars. 60) dakika SONRA belirir; taze bir
  kapanışta yokluğu "yok" demek değildir, "henüz ölçülmedi" demektir.
- **`postmortem.note` "ölçülemedi" diyorsa** (D21-R3) o işlem için mum verisi
  3 denemede alınamamıştır (yavaş/erişilemez veri host'u ya da sembol o
  kaynakta yok). Bu bir motor arızası DEĞİLDİR — ölçüm eksiğidir; kapanışın
  kendisi ve `entry`/`exit` bölümleri etkilenmez. Ölçüm turu safety turunu
  bloklamaz: ayrı bir task'ta koşar ve istek 5 sn'de kesilir.
- **Kayıt bir kanıt değil, kanıt kaynağıdır.** Bir etiketin PnL'i kötü diye
  parametre değiştirmek CLAUDE.md yasak #1'i ihlal eder — önce 3 rejim
  penceresinde backtest.
- **Kapatmak güvenlidir:** `SCALPER_FORENSICS_ENABLED=false` yalnız kaydı
  durdurur; motor davranışı her iki durumda da aynıdır.

### Yeni (D27, 2026-08-24): REAPER etiketi, ücret/MAE dürüstlüğü, karşı-olgu

**REAPER ayrı çıkış etiketidir.** 8 saatlik yaş kesmesi (D4) artık deftere
"SL" değil `REAPER` yazılır (panoda mor rozet). **Geriye dönük düzeltme
YOKTUR**: 2026-08-24 ÖNCESİ kapanan yaş kesmeleri hâlâ "SL"dir — bir "SL
oranı" kıyaslaması yaparken pencereyi buna göre böl.
`scripts/ledger_report.py` bunu tablo altında not olarak yazar. Ayrımı
görmenin üç yolu:
```bash
ssh awa 'curl -s "localhost:9091/scalper/forensics/summary?since=7d" | python3 -c "import sys,json;[print(r) for r in json.load(sys.stdin)[\"exit_reasons\"]]"'
ssh awa 'cd /opt/tradingbot-v2 && .venv/bin/python scripts/ledger_report.py --since "2026-08-24" --format md'   # 2. tablo
# ...ve panodaki "Son İşlemler" rozeti (REAPER = mor).
```
⚠️ Damga yalnız BELLEKTEDİR (`sp.reaper_close_at`): reduce-only emir gittikten
sonra kapanış finalize edilmeden ÖNCE süreç yeniden başlarsa etiket "SL"ye
düşer. Ayrım asla FAZLA saymaz, ama restart'larda EKSİK sayabilir.

**`forensics.fee_estimate` artık `null` olabilir.** Merdiven (TP1/TP2/runner)
çıkışında borsa fill'leri doğrulanamadıysa brüt ÖLÇÜLEMEZ ve komisyon tahmini
YAZILMAZ (`gross_source="unmeasured_ladder"`, `fee_estimate_source="unmeasured"`).
Brüt−net negatif çıkarsa kaynak `"inconsistent"`tır. **`null` = "ölçülmedi",
"ücret yok" DEĞİL.** `fee_dominated` etiketi yalnız ölçülmüş komisyonla atılır.

**MAE düzeltilmiş olabilir.** `mae_source="corrected"` ise yoklama (safety
turu ≈2 sn) fitili kaçırmıştır ve değer çıkış ROI'siyle değiştirilmiştir; ham
örneklem `mae_roi_pct_sampled`ta durur, `mae_samples` kaç kez yoklandığını
söyler. DB'deki `scalp_trades.mae_pct` HAM örneklemdir (düzeltilmez).

**TP1 emri konulamadıysa pano kırmızı uyarı satırı gösterir.** Sayaç:
`/scalper/status → order_health.tp1_missing` ve
`/follower/status → order_health.tp1_missing`. Bu, o pozisyonda break-even'ın
HİÇ kurulamadığı ve işlemin tam risk stopuyla taşındığı anlamına gelir
(ölçüldü: 3 işlem, −18.4 USDT). Sayaçlar süreç-içidir; kalıcı iz
`logs/bot.log`'daki CRITICAL satırlarıdır.

**Karşı-olgu defteri: "reddettiğimiz sinyal iyi miydi?"**
```bash
# Pano özeti (yeni uç YOK): /api/status ve /scalper/status içindeki
# `counterfactual` bloğu — bekleyen/çözülen/ölçülen sayaçları.
ssh awa 'curl -s localhost:9091/scalper/counterfactual?since=7d | python3 -m json.tool | head -60'
ssh awa 'curl -s "localhost:9091/scalper/counterfactual?since=7d&reason=tv_confluence"'
ssh awa 'cd /opt/tradingbot-v2 && .venv/bin/python scripts/ledger_report.py --since "2026-08-24" --counterfactual --format md'
ssh awa 'cd /opt/tradingbot-v2 && jq -c "select(.event==\"counterfactual\")" logs/trades.jsonl | tail -5'
```
Tablo: ret gerekçesi × (n, ölçülen, tp1/stop/açık/veriyok, ort. ROI%, PF,
%95 GA, katlanan).

**Karşı-olgu tuzakları:**
- **Model yalnız TP1 ya da İLK STOP'u modeller.** TP2, chandelier trailing,
  break-even, 8 saatlik reaper, komisyon ve kayma MODELLENMEZ. Aynı mumda
  ikisi de vurursa STOP kazanır (karamsar). Yani bu tablo motorun gerçek
  sonucu DEĞİL, aynı kurallarla kaba bir kıyas tabanıdır.
- **"Veriyok" satırları ortalama/PF hesabına GİRMEZ.** `measured: 0` =
  "ölçülmedi", "etki yok" DEĞİL.
- **İkinci-derece etki ölçülmüyor.** Defter "o sinyal iyi miydi"yi söyler;
  "engellenmeseydi hangi BAŞKA işlem açılmazdı"yı söylemez (kapasite ve
  kayıp-cooldown serbestliği).
- **Bekleyen kuyruk süreç-içidir**: restart çözülmemiş niyetleri düşürür.
  Kalıcı iz niyetin kendisidir (`event="intent"` satırı, artık
  `price`/`stop_price`/`tp1_price`/`leverage` alanlarıyla).
- **Kapatma:** `SCALPER_COUNTERFACTUAL_ENABLED=false` (yalnız defter) ya da
  `SCALPER_FORENSICS_ENABLED=false` (adli kayıt + niyet + defter birlikte).
- **Hacim taşarsa** `/scalper/status → forensics_queue.dropped` artar;
  `SCALPER_COUNTERFACTUAL_DEDUP_SEC`i büyüt (varsayılan 300).

## Deploy ve geri alma

**Üç halka vardır** ve her biri AYRI dizin/süreç/port/`.env` taşır. `--ring`
verilmezse varsayılan `testnet`'tir (scalper halkası) — yani bir mainnet ya da
takipçi deploy'u ASLA kazara olmaz:

| Halka | `--ring` | Dizin | supervisord programı | Port | Durum |
|---|---|---|---|---|---|
| Scalper (TESTNET) | *(varsayılan)* `testnet` | `/opt/tradingbot-v2` | `tradingbot_v2` | 9091 | AKTİF |
| Mainnet | `mainnet` | `/opt/tradingbot-main` | `tradingbot_main` | 9092 | pipeline hazır, dizin/program YOK |
| AlgoPro takipçi (TESTNET) | `follower` | `/opt/tradingbot-ap` | `tradingbot_ap` | 9093 | AKTİF (D20) |

```bash
scripts/deploy.sh awa                        # testnet halkası: push edilmiş main → test → restart → sağlık → başarısızsa otomatik geri al
DEPLOY_NO_RESTART=1 scripts/deploy.sh awa    # dokümantasyon/harness değişikliği (süreç etkilenmez)
scripts/deploy.sh awa <commit>               # elle geri alma; önceki commit backups/commit.prev-<tarih>
scripts/deploy.sh awa --ring follower        # takipçi halkası (aynı repo, ayrı dizin/süreç)
scripts/deploy.sh awa v1.2.0 --ring mainnet  # yalnız etiketli sürüm + elle 'MAINNET' onayı
```

**Yerel çalışma alanı (TEK yapı, 2026-09-03 — D32).** Bu reponun yereldeki tek klonu
`/Users/max/TRADINGBOT/v2` (eski düz botun `/Users/max/TRADINGBOT` klasörü içinde; dış
repo `v2/`yi `.git/info/exclude` ile görmez). `~/Downloads/Downloads/TRADINGBOT` klonu
2026-09-03'te arşivlendi; oradaki commit'siz 31 Ağu sertleştirme yaması D32 ile buraya
alındı, `.env` / `scripts/.scalper_env_snapshot.txt` / `data/klines_cache` taşındı.
Claude proje hafızası da `/Users/max/TRADINGBOT` projesine birleştirildi. Başka yerel
kopya AÇMA: iki kopya = commit'siz iş kaybı (bu olayın kendisi).

**Deploy/restart sertleştirmesi (D32).** `scripts/deploy.sh`, `server_deploy.sh`,
`restart_safe.sh`: sağlık yoklaması `/health` ve katı JSON kontrolü (`status=="healthy"`
**ve** `core_healthy==true`; salt HTTP 200 yetmez — `/api/status` force-fresh çağrısı
REST ağırlığını yiyordu), aynı checkout için `logs/deploy-restart.lock` üzerinde
`flock -n` (ikinci deploy/restart beklemez, RED olur), venv yoksa fail-closed,
temiz-ağaç kapısı artık **untracked dosyaları da** sayar (`--untracked-files=all`).
⚠️ Sunucuda bu kapı başıboş dosyalara takılır (2026-09-03'te `.venv-old/` ve
`docs/.follower_note` bu yüzden temizlendi). Sunucuya elle dosya BIRAKMA; geçici
bir şey gerekiyorsa `backups/` altına koy (gitignore). `state/` ve `.venv*/`
`.gitignore`'dadır ve `state/` sunucuda ayrıca `.git/info/exclude`'dadır.

> **Container yolu (EK dağıtım).** Botu tek bir görüntüde başka sunucuya taşımak için
> `scripts/docker_run.sh` + "Container ile çalıştırma / başka sunucuya taşıma" bölümüne
> bakın. ⛔ supervisord ile container **AYNI ANDA ÇALIŞTIRILAMAZ** (aynı Binance hesabı,
> aynı pozisyonlar → çift yönetim).

Deploy ön koşulları (script kendisi denetler): entry-halt dosyası yok, son 15 dk ban izi yok,
temiz ağaç, yerel HEAD == origin/main. Geri alma mantığı ÜÇ halkada da ORTAKtır
(`scripts/server_deploy.sh`; `RING=` log etiketini, halka-özel entry-halt dosyasını
— takipçide `state/follower_entry_halt.json` — ve mainnet ön kontrollerini seçer),
yani her halka kendi `backups/commit.prev-<tarih>`ine döner.

**Halkalar arası `.env` farkı** (salt okunur, secret DEĞERLERİ maskeli). Karşılaştırma
İKİLİdir: `V2_ENV` ile `MAIN_ENV`. Takipçiyi görmek için `MAIN_ENV`'i takipçinin
`.env`'ine çevir:
```bash
scripts/ring_env_diff.sh awa                                   # v2 ↔ mainnet
MAIN_ENV=/opt/tradingbot-ap/.env scripts/ring_env_diff.sh awa  # v2 ↔ takipçi (D20)
```
Kapsam: `BINANCE_*`, `SCALPER_*`, `TV_*`, `RISK_*`, `FOLLOWER_*`, `BOT_MODE` ile
başlayan TÜM anahtarlar (yani `SCALPER_TV_EVENTS_*`, `SCALPER_STRUCTURE_*`,
`SCALPER_MARKET_DATA_BASE_URL`, `TV_EVENTS_STATE_PATH` de dahil). Adında
`SECRET`/`KEY`/`TOKEN`/`PASS`/`BIND_IP` geçen anahtarların değeri **hiç yazdırılmaz**
(`***`). ⚠️ Kapsam DIŞI kalanlar: `API_PORT`, `DATABASE_URL`, `TELEGRAM_*`,
`APP_ENV`, `LOG_LEVEL` — halkaların ayrıştığını doğrularken bunlara ELLE bak.

`.env` değişikliği deploy'dan AYRI bir adımdır:
temiz ağaç, yerel HEAD == origin/main, **`.env` `BOT_MODE`'u halkayla uyumlu** (D20a bulgu 4:
`RING=testnet/mainnet` + `BOT_MODE=follower` = deploy REDDEDİLİR; `RING=follower` +
`BOT_MODE=follower` YOKSA da reddedilir). `RING` artık `REPO_DIR`/`PROGRAM`/`HEALTH_URL`/
entry-halt dosyasının TEK KAYNAĞIDIR; uyumsuz override `die` eder.

### Güvenli yeniden başlatma (`scripts/restart_safe.sh`)
`.env` değişikliği deploy'dan AYRI bir adımdır ve **çıplak `supervisorctl restart`
KULLANILMAZ** — o yol ban penceresini, entry-halt kilidini, `.env` yedeğini ve sağlık
yoklamasını ATLAR. Bunun yerine:
```bash
ssh awa 'cd /opt/tradingbot-v2 && cp .env "backups/env.bak-$(date -u +%Y%m%d-%H%M%S)-<etiket>" && sed -i "s/^ANAHTAR=.*/ANAHTAR=deger/" .env && ./.venv/bin/python -c "from src.core.config import settings as s; print(s.<alan>)" && RESTART_LABEL=<etiket> scripts/restart_safe.sh testnet'
```
`restart_safe.sh <testnet|follower|mainnet>` sırayla: halka↔`BOT_MODE` kontrolü →
entry-halt kontrolü → ban penceresi (son 15 dk `HTTP 418|banned`) → **saniye damgalı**
`.env` yedeği (aynı gün ikinci uygulama temiz yedeği EZMEZ) → `.env` parse doğrulaması →
restart → sağlık yoklaması (240 sn'ye kadar). Herhangi biri başarısızsa restart YAPILMAZ
ve yedek yolu log satırında verilir.
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
ekler (rollback mantığı ÜÇ halka için ORTAK, değişmez) ve ekstra bir ön kontrol çalıştırır — `.env`
içinde `RISK_EVENT_SECRET` ve `TV_WEBHOOK_SECRET` dolu, `SCALPER_ENTRY_HALT_ENABLED=true` değilse
deploy Türkçe bir hata mesajıyla reddedilir (bkz. `docs/MAINNET_PLAN.md` §3).
Halkalar arası `.env` farkı için bölümün başındaki `scripts/ring_env_diff.sh` kutusuna bak.

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

### Pano erişimi (nginx monitor proxy) — yeni uç eklerken DİKKAT
Kullanıcı panoyu `https://<sunucu-ip>:9443/dashboard` üzerinden görür
(nginx `sites-available/tradingbot-monitor-ip`, HTTP Basic auth
`/etc/nginx/.htpasswd-tradingbot-ip`, kullanıcı `efe`). Bot yalnız
`127.0.0.1:9091`'e bağlıdır — proxy dışında dışarı AÇIK DEĞİLDİR.

**Beyaz liste kuralı:** proxy YALNIZ sayılan salt-okuma GET uçlarını geçirir;
listelenmeyen her yol `404` döner (catch-all). Yeni bir pano kartı yeni bir uç
çağırıyorsa proxy'ye EKLENMEDEN kullanıcıda ÇALIŞMAZ (localhost'ta çalışır —
bu tuzağa 2026-08-24'te düşüldü: D21 adli kayıt kartı proxy'de 404 alıyordu).

İzinli (2026-08-24 itibarıyla): `/dashboard`, `/health`, `/api/status`,
`/positions`, `/config`, `/waiting-mode/active`, `/scalper/status`,
`/scalper/stats`, `/scalper/trades` (query sabit `limit=30`),
`/scalper/forensics/(summary|recent)`, `/scalper/trades/<id>/forensics`,
`/follower/status`.
ℹ️ **D23 (AI karar katmanı) kartı YENİ UÇ AÇMAZ** — verisini `/api/status`
gövdesindeki `ai_gate` bloğundan okur, yani beyaz listeye EKLEME GEREKMEZ
(nginx'e bakman gerekmiyor).
ℹ️ **D27/B (karşı-olgu defteri) de pano tarafında YENİ UÇ AÇMAZ** — pano özeti
`/scalper/status → counterfactual` ve `/api/status → counterfactual`
bloklarındadır (ikisi de zaten izinli). Ayrıntılı tablo ucu
**`/scalper/counterfactual` BEYAZ LİSTEYE EKLENMEDİ ve EKLENMEMELİDİR**:
gerçek disk okuması yapar (JSONL + arşivler) ve panodan 5 sn'de bir
yoklanırsa 2026-08-18 pano-açlığı sınıfını geri getirir. Elle kullanım:
`ssh awa 'curl -s localhost:9091/scalper/counterfactual?since=7d' | jq`
ya da `scripts/ledger_report.py --counterfactual`.

**ASLA eklenmez:** `/tv-signal`, `/risk-event`, `/follower/event`,
`/tv-events/reset` ve tüm POST/kontrol uçları (secret taşırlar / durum değiştirir).
Değişiklikten sonra: `nginx -t` → `systemctl reload nginx` → kimliksiz `curl -k`
ile 401 (izinli uç) ve 404 (kontrol ucu) doğrulaması. Yedek:
`sites-available/tradingbot-monitor-ip.bak-<tarih>`.

### Gömülü takipçiyi açma (D20b — **TERCİH EDİLEN** kurulum)
Kullanıcı kararı (2026-08-23): *"Yeni hesap yok, yeni panel yok."* AlgoPro takipçisi
scalper ile **AYNI süreçte** (`tradingbot_v2`, :9091), **AYNI testnet hesabında** ve
**AYNI panoda** çalışır; boyutlaması gerçek bakiyeye değil **1000 USD'lik SANAL
deftere** dayanır. Aşağıdaki ayrı halka (`/opt/tradingbot-ap`) desteği KALDIRILMADI
ama artık gerekli değildir — yeni kurulumlarda **bunu** kullan.

**0) ÖNCE ayrı halkayı kapat** (D20 `tradingbot_ap` çalışıyorsa **ZORUNLU**).
Gömülü mod açıldığı an AlgoPro gövdeleri SÜREÇ İÇİNDE tüketilir ve HTTP köprüsü
HİÇ çağrılmaz: ayrı halka tek bir giriş/çıkış/TP/SL alarmı almaz ve AÇIK
pozisyonlarına EXIT/flip komutu ULAŞMAZ (sessizce kendi SL/TP merdiveniyle
taşınır). Startup bu durumu CRITICAL loglar ama kendiliğinden düzeltmez.
```bash
# a) ayrı halkayı DÜZLEŞTİR (açık pozisyon kalmasın)
curl -sS -X POST http://127.0.0.1:9093/risk-event -H 'Content-Type: application/json' \
  -d '{"secret":"<AP_RISK_EVENT_SECRET>","action":"flatten","reason":"gomulu-moda-gecis"}'
# b) durdur
ssh awa 'supervisorctl stop tradingbot_ap'
# c) ana bottaki köprüyü BOŞALT (dolu kalırsa startup CRITICAL uyarır)
ssh awa 'cd /opt/tradingbot-v2 && sed -i "s|^FOLLOWER_FORWARD_URL=.*|FOLLOWER_FORWARD_URL=|" .env'
```

**1) `.env` (sunucuda, yedek + doğrulama ile):**
```ini
FOLLOWER_EMBEDDED=true
FOLLOWER_VIRTUAL_CAPITAL_USDT=1000
FOLLOWER_SYMBOLS=<SEÇİLEN>          ; TEK coin (ör. ölçümle seçilen sembol)
                                    ; boş bırakılırsa evren 8 majör olur ve
                                    ; scalper'dan HİÇBİR sembol çıkarılmaz
```
Aynı `.env`'de scalper'ın evrenini de daralt (kod zaten otomatik dışlar; bu ikinci
kayıt operatör için açıklıktır): `SCALPER_SYMBOL_ALLOWLIST` ve
`SCALPER_TV_SYMBOL_ALLOWLIST` listelerinden `FOLLOWER_SYMBOLS`'daki sembolü ÇIKAR.

Opsiyoneller: `FOLLOWER_MIN_TP1_FEE_RATIO` **varsayılan 1.0 = AÇIK** (D20a bulgu 3) —
stop mesafesi ~%0.20'nin altındaki AlgoPro girişleri komisyonu ödeyemeyeceği için HİÇ
açılmaz; kapatmak KULLANICI KARARIDIR (`=0`, o hâlde her girişte ⚠️ WARNING).
`FOLLOWER_SL_MARGIN_PCT` (vars. 30, aralık 10–50) kaldıracın payıdır:
`lev = clamp(round(FOLLOWER_SL_MARGIN_PCT / sl_pct), FOLLOWER_LEV_MIN,
FOLLOWER_MAX_LEVERAGE)`. `RISK_EVENT_SECRET` gömülü modda ZORUNLU değildir ama
boşsa `/risk-event` 503 döner ve startup WARNING loglar — **doldurulması şiddetle
önerilir** (tek uzaktan `flatten` yolu odur).

Reçete repo'nun **güvenli `.env` kalıbını** kullanır (satır VARSA değiştirir,
YOKSA ekler). Körlemesine `>>` eklemek aynı anahtarı ikinci kez yazar ve
dosyanın son satırında newline yoksa iki ayarı BİRLEŞTİRİR:
```bash
ssh awa 'cd /opt/tradingbot-v2 && cp .env backups/env.bak-$(date -u +%Y%m%d-%H%M%S)-embedded \
  && for kv in "FOLLOWER_EMBEDDED=true" "FOLLOWER_VIRTUAL_CAPITAL_USDT=1000" "FOLLOWER_SYMBOLS=<SEÇİLEN>"; do \
       k="${kv%%=*}"; \
       { grep -q "^$k=" .env && sed -i "s|^$k=.*|$kv|" .env || printf "\n%s\n" "$kv" >> .env; }; \
     done \
  && ./.venv/bin/python -c "from src.core.config import settings as s; \
     assert s.follower_embedded, \"GÖMÜLÜ MOD AÇILMADI\"; \
     assert s.follower_reserved_symbols == [\"<SEÇİLEN>\"], s.follower_reserved_symbols; \
     assert float(s.follower_virtual_capital_usdt) == 1000.0; \
     print(\"evren=\", s.follower_universe, \"ayrılmış=\", s.follower_reserved_symbols)"'
```
> `follower_reserved_symbols` YALNIZ `FOLLOWER_EMBEDDED=true` **ve**
> `FOLLOWER_SYMBOLS` dolu iken doludur — tek assert iki ayarı birden doğrular.
> (Eski reçetedeki `assert s.follower_universe` FOLLOWER_SYMBOLS hiç
> yazılmasa da GEÇİYORDU: evren 8 majöre düşer ve hiçbir sembol scalper'dan
> çıkarılmazdı.) Şu hatalı bileşimleri config startup'ta REDDEDER:
> `FOLLOWER_VIRTUAL_CAPITAL_USDT<=0`, `SCALPER_ENABLED=false`,
> `BOT_MODE=follower` ile birlikte, ve `SCALPER_SYMBOL_ALLOWLIST` DOLUYKEN
> `FOLLOWER_SYMBOLS`'un o listeyi tamamen yutması.
> ⚠️ `SCALPER_SYMBOL_ALLOWLIST` BOŞSA (canlı `.env`'de varsayılan durum) evren
> `scanner`dan gelir ve config bunu göremez — o durumda kontrol **motor
> başlangıcında** GERÇEK evrenle yapılır ve boşalma startup HATASI verir
> (evren o an okunamazsa kontrol atlanır + WARNING; tarama turunda
> `/scalper/status → scan_status=degraded:universe_empty` ile görünür).
> ⚠️ Çıplak `supervisorctl restart` **YASAK** (D20a bulgu 4). Uygula:
> `RESTART_LABEL=embedded-follower scripts/restart_safe.sh testnet`

**2) Doğrulama (restart'tan sonra):**
```bash
ssh awa 'cd /opt/tradingbot-v2 && supervisorctl status tradingbot_v2 && \
  curl -sS http://127.0.0.1:9091/follower/status | python3 -m json.tool | head -40'
```
Beklenen: `"embedded": true`, `"universe": ["<SEÇİLEN>"]`,
`"virtual_ledger": {"enabled": true, "base_usdt": 1000.0, …}`,
`"entries_ready": true`. Log satırları: `🤖 AlgoPro takipçi motoru başlatılıyor`,
`🧩 GÖMÜLÜ mod (D20b): …`, scalper tarafında
`🤖 Tarama evreninden çıkarıldı — AlgoPro takipçisine ayrılmış: <SEÇİLEN>`.
Panoda (`/dashboard`) **"AlgoPro Takipçi"** kartı görünür: coin, sanal sermaye,
güncel equity, günlük K/Z, açık pozisyonlar (giriş/SL/TP1-3/ROI), komisyon kapısı
ret sayacı, son olay saati. "Son İşlemler" tablosunda AP satırları altın şeritlidir.

**3) TV alarmları (ana oturum yapar — bu adım BOT tarafında değil TV'dedir):**
Mevcut `AlgoPro SELL <COIN>` / `BUY` alarmlarının **URL'si AYNI KALIR**
(`…/tv-signal?secret=…`). Yalnız iki alan düzenlenir:
* **Koşul:** *"Herhangi bir alert() fonksiyonu çağrısı"* (Any alert() function call)
* **Sıklık/aralık:** **1 dakika**

Neden: alert() modunda mesajı script üretir ve **seviyeleri İÇERİR**
(`SL/TP1/TP2/TP3` + `TQI`/`Score`) — takipçinin katı tanıyıcısı bu biçimi bekler.
Eski özel mesaj biçimi (`BUY on {{ticker}} | TF: 5 | Price: …`) takipçiye GİTMEZ,
ana botun sağlamasına oy vermeye devam eder (davranış değişmedi).
> ⚠️ Alarm KLONLAMA deploy'dan ÖNCE yapılmaz: kod canlıda değilken gelen alarm
> kaybolur (ya da 422 alır).

**4) Günlük bakım / okuma:**
```bash
# durum + sanal defter
curl -sS http://127.0.0.1:9091/follower/status | python3 -m json.tool
# AP defteri (aynı DB, strateji etiketiyle ayrılır)
python3 scripts/ledger_report.py --db tradingbot.db --strategy AP --since "<başlangıç>" --format md
# iki defteri YAN YANA gör (3b bölümü)
python3 scripts/ledger_report.py --db tradingbot.db --since "<başlangıç>" --format md
```
Komisyon kapısı retleri: `/follower/status → reject_counters.fee_gate` ve
`state/follower_levels.jsonl` (`rejected: "fee_gate"`). Gerçek bakiye yetmediği için
atlanan girişler: `reject_counters.insufficient_balance` + `logs/bot.log`'ta
`⛔ Takipçi girişi atlandı: hesabın kullanılabilir bakiyesi …`.
Takipçi evreni dışında kalan AlgoPro alarmları:
`reject_counters.symbol_not_in_follower_universe` + `⚠️ AlgoPro girişi işlenmedi: …`
(o alarmlar ana botun oy yoluna DA düşmez — TV'de kapatılmaları gerekir).
Panoda **"Evren dışı alarm"** sayacı olarak görünür ve "Alarm olayı"/"Son olay"
satırlarını da hareket ettirir.

**Günlük kesici iki sayı gösterir (gömülü mod).** `/scalper/status →
daily_pnl` KENDİ defterinden gelir (`daily_pnl_source="scalper_ledger"`);
`daily_income_account` ise hesabın HAM günlük income'ıdır ve YALNIZ BİLGİDİR.
İkisi arasındaki fark açık pozisyonların funding/komisyonu ve takipçinin
işlemleridir — kesici o farkı GÖRMEZ (D20b sınırlılık i-d).

**Sahipsiz pozisyon uyarısı.** Gömülü modda hiçbir motorun izlemediği ve rezerve
etmediği bir açık pozisyon (elle ya da Telegram botuyla açılmış olabilir)
`/follower/status → unknown_positions` ve panoda **SAHİPSİZ POZİSYON** satırında
görünür. Takipçi ona **DOKUNMAZ**, girişlerini **DURDURMAZ** ve `/risk-event
flatten` onu **KAPATMAZ** (ayrı halkada davranış D20a'daki gibi entry-halt
olmaya devam eder). Kapatmak isteniyorsa ilgili motorun kendi flatten'ı ya da
elle kapatma gerekir.

**Kapasite.** Her motorun tavanı KENDİ pozisyonlarını sayar: scalper
`SCALPER_MAX_POSITIONS`, takipçi `FOLLOWER_MAX_POSITIONS`. Yani hesapta
eşzamanlı en çok `SCALPER_MAX_POSITIONS + FOLLOWER_MAX_POSITIONS` pozisyon
olabilir ve `MAX_POSITIONS` bunları TOPLAMAZ (takipçi scalper'ın/Telegram'ın
slotunu yemez, tersi de olmaz). Marj rekabeti gerçektir: takipçinin açık marjı
hesabın `availableBalance`'ından düşer ve scalper'ın boyutlama tabanını
küçültür — teşhis `/scalper/status → sizing.follower_embedded` ve takipçinin
`virtual_ledger.exchange_available_usdt` alanındadır.

**5) Acil durdurma / geri alma:**
```bash
# İKİ motoru da düzleştir (gömülü modda /risk-event ikisini de kapsar):
curl -sS -X POST http://127.0.0.1:9091/risk-event \
  -H 'Content-Type: application/json' \
  -d '{"secret":"<RISK_EVENT_SECRET>","action":"flatten","reason":"<neden>"}'
# Sonra kapat:
ssh awa 'cd /opt/tradingbot-v2 && sed -i "s/^FOLLOWER_EMBEDDED=.*/FOLLOWER_EMBEDDED=false/" .env && \
  RESTART_LABEL=embedded-off scripts/restart_safe.sh testnet'
```
> ⚠️ Bayrağı kapatmak AÇIK pozisyonu KAPATMAZ — yalnız yöneticisini ortadan
> kaldırır. Önce `flatten`, sonra kapat.

Ayrı halkaya (D20) GERİ dönülecekse sıra simetriktir: gömülü modu kapat →
`FOLLOWER_FORWARD_URL`/`FOLLOWER_FORWARD_SECRET`'i doldur →
`supervisorctl start tradingbot_ap` → `restart_safe.sh testnet`. İkisi AYNI
ANDA açık bırakılmaz (köprü çağrılmaz, ayrı halka alarmsız kalır).

#### Gömülü takipçiyi kapatma (SIRA ÖNEMLİ)
`FOLLOWER_EMBEDDED=false` yapıp restart etmek AÇIK AP pozisyonunu **yönetimsiz**
bırakır: takipçi hiç başlamaz, scalper da defter filtresi yüzünden `strategy=AP`
satırını KURTARMAZ. Borsadaki SL/TP emirleri durur ama TP1→BE, EXIT/flip ve
kapanış defteri ÇALIŞMAZ. Doğru sıra:
```bash
# 1) AP pozisyonlarını KAPAT (bayrak hâlâ AÇIKKEN)
curl -sS -X POST http://127.0.0.1:9091/risk-event -H 'Content-Type: application/json' \
  -d '{"secret":"<RISK_EVENT_SECRET>","action":"flatten","reason":"gomulu-kapatma"}'
# 2) defterde açık AP satırı KALMADIĞINI doğrula
ssh awa 'cd /opt/tradingbot-v2 && ./.venv/bin/python -c "
import asyncio; from src.strategies.scalper.tracker import ScalpTracker
rows = asyncio.run(ScalpTracker().open_trades(strategies=(\"AP\",)))
print(\"acik AP:\", [(r.id, r.symbol) for r in rows]); assert not rows"'
# 3) bayrağı kapat + korumalı restart
ssh awa 'cd /opt/tradingbot-v2 && sed -i "s/^FOLLOWER_EMBEDDED=.*/FOLLOWER_EMBEDDED=false/" .env && \
  RESTART_LABEL=embedded-off scripts/restart_safe.sh testnet'
```
Adım 2 atlanırsa startup CRITICAL loglar ve `/health` gövdesinde
`follower="disabled_with_open_trades"` + `follower_details.open_trades` görünür
(süreç KASTEN düşürülmez — hard fail pozisyonu kapatmaz, yalnız restart
döngüsü doğururdu). O hâlde çözüm: `FOLLOWER_EMBEDDED=true` ile yeniden
başlatıp adım 1'i uygulamak, ya da pozisyonları elle kapatıp `scalp_trades`
satırlarını kapanmış işaretlemek.

Takipçinin kendi fail-closed giriş kilidi `state/follower_entry_halt.json`'dur
(scalper'ınkinden AYRI dosya): yetim pozisyon ya da korumasız pozisyon şüphesinde
kurulur; açmak = dosyayı İNCELEYİP yeniden adlandırmak + restart.

### AlgoPro takipçi halkası — AYRI HALKA (D20, `BOT_MODE=follower`)
> **Not (D20b):** artık **tercih edilen kurulum GÖMÜLÜ moddur** (yukarı bak).
> Bu bölüm ayrı hesap/süreç isteyen kurulum için KORUNMUŞTUR; ikisi AYNI ANDA
> kullanılmaz (`BOT_MODE=follower` + `FOLLOWER_EMBEDDED=true` = startup HATASI).

İKİNCİ ve BAĞIMSIZ bir testnet sistemi: **yalnız AlgoPro V1.6 sinyallerini** izler
(scanner yok, strateji yok, TV sağlaması yok). Scalper halkası (`tradingbot_v2`) bundan
HİÇ etkilenmez — ayrı dizin, ayrı süreç, ayrı Binance testnet hesabı, ayrı DB/state/log.

| | |
|---|---|
| Dizin | `/opt/tradingbot-ap` |
| Süreç | supervisord `tradingbot_ap` → `.venv/bin/python -m uvicorn src.main:app --host 127.0.0.1 --port 9093` |
| Ağ | Binance Futures **TESTNET** (ayrı hesap/anahtar; mainnet startup'ta REDDEDİLİR) |
| Deploy | `scripts/deploy.sh awa --ring follower` |
| Durum | `curl -sS http://127.0.0.1:9093/follower/status \| python3 -m json.tool` |
| Defter | `tradingbot_ap.db` → `scalp_trades`, `strategy="AP"` |
| Rapor | `python3 scripts/ledger_report.py --db tradingbot_ap.db --strategy AP --since "<başlangıç>" --format md` |
| Kalibrasyon | `state/follower_levels.jsonl` (AlgoPro seviyeleri vs k×ATR kuralı sapması) |

**Kurulum (bir kez, insan yapar):**
1. `/opt/tradingbot-ap` dizinini oluştur, repo'yu klonla, `.venv` kur
   (`python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`).
2. `.env` yaz — **anahtarları KULLANICI girer** (scalper'ın `.env`'i KOPYALANMAZ):
   ```ini
   BOT_MODE=follower
   BINANCE_API_KEY=<İKİNCİ testnet hesabının anahtarı>
   BINANCE_API_SECRET=<İKİNCİ testnet hesabının secret'ı>
   BINANCE_BASE_URL=https://testnet.binancefuture.com
   DATABASE_URL=sqlite:///./tradingbot_ap.db
   API_PORT=9093
   FOLLOWER_FORWARD_SECRET=<ana bottakiyle AYNI güçlü rastgele değer>
   RISK_EVENT_SECRET=<AYRI güçlü rastgele değer>   ; ZORUNLU (aşağıya bak)
   TELEGRAM_BOT_TOKEN=x               ; TELEGRAM_CHAT_ID=x   (KULLANILMAZ)
   ```
   ⚠️ **`RISK_EVENT_SECRET` ZORUNLUDUR** — boşsa süreç BAŞLAMAZ (config
   fail-fast). Takipçinin tek uzaktan durdurma/flatten yolu `POST /risk-event`
   tir; köprüyü kapatmak yalnız YENİ sinyali keser, AÇIK pozisyonu kapatmaz.
   ⚠️ **Takipçi Telegram bildirimi GÖNDERMEZ.** `BOT_MODE=follower`'da
   `TelegramBotService` hiç başlatılmaz (orchestrator da yok); alanlar yalnız
   config doğrulaması için doldurulur — ayrı bir bot açmaya GEREK YOK. Durum
   `/follower/status`, `logs/bot.log` ve `logs/trades.log`'tadır.
   (Diğer `FOLLOWER_*` varsayılanları `env.example`'da; hepsi opsiyoneldir.)
3. supervisord program tanımı:
   ```ini
   [program:tradingbot_ap]
   directory=/opt/tradingbot-ap
   command=/opt/tradingbot-ap/.venv/bin/python -m uvicorn src.main:app --host 127.0.0.1 --port 9093
   autostart=true
   autorestart=true
   stopsignal=TERM
   stopasgroup=true
   killasgroup=true
   user=<sunucu-kullanıcısı>
   environment=PYTHONUNBUFFERED="1"
   stdout_logfile=/opt/tradingbot-ap/logs/supervisor.log
   stdout_logfile_maxbytes=10MB
   stdout_logfile_backups=5
   redirect_stderr=true
   ```
4. **Ana bota köprüyü aç** (scalper halkasının `.env`'i — deploy'dan AYRI adım):
   ```bash
   ssh awa 'cd /opt/tradingbot-v2 && cp .env backups/env.bak-$(date -u +%Y%m%d-%H%M%S)-follower && { grep -q "^FOLLOWER_FORWARD_URL=" .env && sed -i "s|^FOLLOWER_FORWARD_URL=.*|FOLLOWER_FORWARD_URL=http://127.0.0.1:9093/follower/event|" .env || echo "FOLLOWER_FORWARD_URL=http://127.0.0.1:9093/follower/event" >> .env; } && { grep -q "^FOLLOWER_FORWARD_SECRET=" .env && sed -i "s|^FOLLOWER_FORWARD_SECRET=.*|FOLLOWER_FORWARD_SECRET=<SECRET>|" .env || echo "FOLLOWER_FORWARD_SECRET=<SECRET>" >> .env; } && ./.venv/bin/python -c "from src.core.config import settings as s; assert s.follower_forward_url and s.follower_forward_secret, \"KÖPRÜ AÇILMADI\"; print(\"forward_url=\", s.follower_forward_url)" && RESTART_LABEL=bridge-on scripts/restart_safe.sh testnet'
   ```
   ⚠️ `sed -i` eşleşme bulamazsa 0 ile çıkar — bu yüzden `{ grep -q … && sed … || echo …; }`
   grubu ve restart'tan ÖNCE `assert`'li geri-okuma ZORUNLUDUR (bkz. "Gölge modu" uyarısı).

**TradingView alarmları (kullanıcı yapar, 8 alarm):**
AlgoPro V1.6 "Herhangi bir alert() fonksiyonu çağrısı" ("Any alert() function call")
modunda mesajı KENDİSİ üretir ve **seviyeleri içerir** — mesaj şablonu YAZILMAZ.
Bu yüzden sembol başına TEK alarm yeter; Buy/Sell/Exit/TP/SL olaylarının HEPSİ aynı
kanaldan gelir. 8 sembol (BTC, ETH, SOL, XRP, DOGE, BNB, ADA, LTC) × **1 dakika**:

| Alan | Değer |
|---|---|
| Koşul | AlgoPro V1.6 → **Herhangi bir alert() fonksiyonu çağrısı** |
| Grafik | ilgili sembol, **1 dakika** |
| Webhook URL | `http://<sunucu>:9091/tv-signal?secret=<TV_WEBHOOK_SECRET>&src=algopro` (BUGÜNKÜ URL — DEĞİŞMEZ) |
| Mesaj | boş bırak (script üretir) |

2026-08-23'te TV Desktop sondasıyla doğrulanan gerçek gövdeler (iki yön de):
```
🔴 SELL | BINANCE:BTCUSDT | TF: 1 | Price: 77126.08 | TQI: .45 | Score: 8 | SL: 77167.77 | TP1: 77105.23 | TP2: 77084.39 | TP3: 77063.54 | TP: fixed ×1.00
🟢 BUY  | BINANCE:BTCUSDT | TF: 1 | Price: 76556.52 | TQI: .54 | Score: 17 | SL: 76501.73 | TP1: 76583.92 | TP2: 76611.32 | TP3: 76638.72 | TP: fixed ×1.00
🎯 TP1 HIT | BINANCE:BTCUSDT | TF: 1 | Price: 76583.92
🎯 TP2 HIT | BINANCE:BTCUSDT | TF: 1 | Price: 76611.32
🏆 TP3 HIT | BINANCE:BTCUSDT | TF: 1 | Price: 76638.72
🛑 SL HIT | BINANCE:BTCUSDT | TF: 1 | Price: 76497.98
```
Ayrıştırma emoji'ye DEĞİL anahtar kelimeye dayanır: `BUY`/`SELL` → giriş,
`EXIT` → çıkış, `TP1|TP2|TP3 HIT` ve `SL HIT` → telemetri/çapraz doğrulama.
`⚪ EXIT` gövdesi TV'de HENÜZ görülmedi (varsayım); gelirse ayrıştırılır, biçim
beklenmedikse 422 + WARNING (sessiz kalmaz).

⚠️ **Seviye sırası kapısı:** giriş mesajında sıra `LONG: SL < Price < TP1 < TP2 < TP3`
(SHORT tersi) DEĞİLSE olay **422** ile reddedilir ve `logs/bot.log`'a
`Takipçi olayı ayrıştırılamadı: Seviye sırası …` yazılır. Bunu görürsen AlgoPro'nun
alert biçimi değişmiş demektir — alarmları çoğaltma, önce gövdeyi TV'den yeniden
ölç (bkz. D20).

⚠️ **Sıralama uyarısı:** bu alarmlar ana bota da düşer. `BUY`/`SELL` mesajları
BUGÜNKÜ gibi TV sağlamasına oy verir (davranış değişmedi); `EXIT`/`TP HIT`/`SL HIT`
mesajları yön kelimesi taşımadığı için ana botta **422** alır (işlem AÇTIRMAZ) — köprü
bu 422'den ÖNCE çalıştığı için takipçi olayı yine de alır. Ana botta `kind` bazlı
gövde-yönlendirme ayrı bir çalışmadadır; o merge edilmeden alarm sayısını artırma.

**Akış:** TV → ana bot `/tv-signal` (secret doğrulanır) → gövde **KATI AlgoPro V1.6
tanıyıcısından** geçerse `FOLLOWER_FORWARD_URL`'e İLETİLİR (ayrı task; bağlantı 2 sn,
okuma `FOLLOWER_FORWARD_TIMEOUT_SECONDS`=20 sn; hata yalnız loglanır) → takipçi
`/follower/event` (secret `X-Follower-Secret` başlığında) → `FollowerEngine`.

⚠️ **D20a: iletim kararı `?src=`'e DEĞİL GÖVDEYE bakar.** Bir gövdenin iletilmesi için
başlıkta olay anahtarı (`BUY`/`SELL`/`EXIT`/`TPn HIT`/`SL HIT`) + `| BINANCE:<SEMBOL> |`
+ `| TF:` + `| Price:` (girişlerde ayrıca DÖRT seviye) ŞARTTIR. LuxAlgo/BotV3/serbest
metin — `?src=algopro` yazsa bile — ASLA iletilmez. Sayaçlar:
`curl -sS http://127.0.0.1:9091/follower/forwarder | python3 -m json.tool`
(`counters.forwarded`, `counters.skipped_not_algopro`, `counters.transport_error`,
`suppressed_warnings`; `last_skipped.body_head` secret MASKELİDİR). Başarısız iletim
uyarıları dakikada 1 ile sınırlıdır — sayaç artıyorsa log satırı olmasa da arıza vardır.

⚠️ **`/follower/event` `?secret=` KABUL ETMEZ** (403). Erişim logu query string'i
düz metin yazar; secret yalnız `X-Follower-Secret` başlığında ya da gövdede
(`secret=… kind=…`) taşınır. Elle test:
`curl -sS -H 'X-Follower-Secret: <SECRET>' --data-binary '<gövde>' http://127.0.0.1:9093/follower/event`

✅ **(D20a'da KAPATILDI)** Eski uyarı: `?src=` taşımayan bir gövde `| TF:` / `| Price:`
damgalarıyla "algopro" sayılabiliyordu. Artık iletim için borsa nitelikli sembol
(`| BINANCE:<SEMBOL> |`) ve başlıkta olay anahtarı da şart — elle yazılmış bir LuxAlgo
şablonu bu kapıdan GEÇEMEZ. `?src=` etiketi TV sağlaması (TvConfluence) için hâlâ
anlamlıdır ve o tarafta DEĞİŞMEDİ.

**Ne görürsün:**
- `logs/bot.log`: `🤖 AlgoPro takipçi motoru başlatılıyor`, her girişte
  `🎯 <SEMBOL>: AlgoPro <YÖN> girişi açıldı (lev=..x, sl_pct=%.., sl_roi=%.., marj=..)`.
- 🚫 `TP1 ROI (%..) gidiş-dönüş komisyonun (1×%..) altında — giriş yapılmadı`:
  **ücret eşiği kapısı** (D20a bulgu 3, varsayılan AÇIK). Stop mesafesi %0.20'nin
  altındaysa (taker %0.05, RR1 0.5) işlem aritmetik olarak negatiftir ve HİÇ açılmaz.
  Sayaç: `/follower/status → reject_counters.fee_gate`; defter satırı:
  `state/follower_levels.jsonl` (`rejected: "fee_gate"`). Kapatmak KULLANICI
  KARARIDIR: `FOLLOWER_MIN_TP1_FEE_RATIO=0` (o hâlde her girişte ⚠️ WARNING
  loglanır ve break-even çoğu işlemde KURULAMAZ).
- 🚫 `AlgoPro stopu (..) canlı fiyatın (..) yanlış tarafında` / `sinyal fiyatı bayat`
  / `olay bayat (.. sn > 20 sn)`: sinyal ile emir arasında fiyat kaçmış demektir —
  giriş YAPILMADI (D20a bulgu 1/6). Sayaçlar: `stop_already_passed`, `signal_drift`,
  `event_age`.
- 🚨 `dolum (..) AlgoPro stopunu (..) ZATEN GEÇMİŞ`: MARKET dolumu stopun ötesine
  kaymış; pozisyon reduce-only MARKET ile KAPATILDI ve stop yeniden ÇAPALANMADI.
  Defterde `follower_stop_already_passed` notuyla görünür.
- 🚨 `TAKİPÇİ YETİM POZİSYON(LAR)`: borsada açık ama motor izlemiyor (D20a bulgu 8).
  Girişler DURDURULDU (entry-halt). Kapatmak: `POST /risk-event {"action":"flatten"}`
  — yetimleri de kapatır; sonra `state/follower_entry_halt.json` incelenip
  yeniden adlandırılır ve süreç yeniden başlatılır.
- 🔧 `EKSİK TPn yeniden kondu`: merdiven bacağı borsada yoktu (restart / iptal turu);
  yeniden konuldu. Sayaçlar: `/follower/status → tp_repair`.
- 🚨 `TP1 emri KONULAMADI`: o pozisyonda break-even hiç kurulamaz;
  `/follower/status → reject_counters.tp1_missing` sayacında görünür.
- `GET /follower/status` → izlenen pozisyonlar (lev/sl_pct/sl_roi/marj/TP1-2-3 durumu),
  cooldown'lar, kill switch, risk-olayı halt'ı, son 50 olay ve ret sayaçları.
- Defter: `sqlite3 tradingbot_ap.db "SELECT symbol,direction,leverage,signal_reason,exit_reason,realized_pnl FROM scalp_trades ORDER BY id DESC LIMIT 20"`
  (`signal_reason` boyutlamayı taşır: `algopro:entry;...;lev=100;sl_pct=..;sl_roi=..;margin=..`).
- Kalibrasyon: `tail state/follower_levels.jsonl` — AlgoPro seviyeleri ile k×ATR
  kuralının sapması (`sl_distance_deviation_pct`).

⚠️ **Dashboard uyarısı:** `static/dashboard.html` scalper halkası için yazılmıştır;
takipçi portunda (`ssh -L 9093:127.0.0.1:9093 awa`) açılırsa `/scalper/*` uçları BOŞ
görünür (motor yok — bu bir arıza DEĞİL). Takipçinin durumu `/follower/status`tadır.

**Arıza/durdurma:**
- Girişleri durdur / her şeyi kapat: `POST http://127.0.0.1:9093/risk-event`
  (D10 ile AYNI sözleşme, `RISK_EVENT_SECRET` takipçinin kendi `.env`'inden —
  ZORUNLU alan, bkz. kurulum adım 2). `action=flatten` halt'ı ÖNCE kurar, sonra
  `_entry_lock` altında tüm izlenen pozisyonları reduce-only MARKET ile kapatır;
  o anda uçuşta olan bir giriş de kilit sayesinde YAKALANIR.
- Köprüyü kapat (takipçi sinyal ALMASIN): ana bottan `FOLLOWER_FORWARD_URL`'i boşalt +
  `cd /opt/tradingbot-v2 && RESTART_LABEL=bridge-off scripts/restart_safe.sh testnet`.
  Takipçi süreci açık kalır, açık pozisyonları yönetmeye devam eder.
  ⚠️ Çıplak `supervisorctl restart` KULLANMA: ban penceresi, entry-halt ve `.env`
  yedeği kontrolleri atlanır (bkz. "Güvenli yeniden başlatma").
- Takipçi giriş kilidi: `state/follower_entry_halt.json` (fail-closed, `scalper_entry_halt`
  ile KARIŞTIRMA). Açmak = nedeni anla → dosyayı `.cleared-<tarih>` yap → restart.
- Deploy ön koşulu: bu dosya varken `scripts/deploy.sh awa --ring follower` REDDEDİLİR.
- Kod geri alma: `scripts/deploy.sh awa <önceki-commit> --ring follower`
  (önceki commit `/opt/tradingbot-ap/backups/commit.prev-*` dosyalarındadır;
  akış test + restart + sağlık + otomatik geri alma ile AYNI).
- İki halkanın `.env` farkını (secret DEĞERLERİ maskeli) görmek için:
  `MAIN_ENV=/opt/tradingbot-ap/.env scripts/ring_env_diff.sh awa`

## Container ile çalıştırma / başka sunucuya taşıma

Container, supervisord'un **YERİNE GEÇEN** değil **YANINA EKLENEN** ikinci bir dağıtım
yoludur. Bugün canlı olan yol supervisord'dur (`tradingbot_v2`, `/opt/tradingbot-v2`);
container yolu botu tek bir görüntüye paketler ki **başka bir sunucuya taşınabilsin**.

### ⛔⛔ EN ÖNEMLİ KURAL: SUPERVISORD İLE CONTAINER **AYNI ANDA ÇALIŞAMAZ** ⛔⛔

**AYNI `.env` = AYNI BİNANCE HESABI = AYNI POZİSYONLAR.** İki motor aynı anda
çalışırsa:

* ikisi de açılışta **aynı açık pozisyonları devralır** ve her biri kendi SL/TP
  merdivenini yerleştirir → **çift bracket**, biri diğerinin emrini "yetim" sanar;
* ikisi de `state/*.json` yazar (cooldown, entry-halt, pending journal) →
  **son yazan kazanır**, fail-closed giriş kilitleri sessizce kaybolur;
* ikisi de tarar ve emir açar → **çift giriş**, iki katı REST ağırlığı → **418 ban**;
* kapanış kayıtları iki motora bölünür → defter (`scalp_trades`) güvenilmez olur.

Bu, `docs/DECISIONS.md` D20b incelemesindeki **"ayrı halka + gömülü takipçi aynı anda"**
kritik sınıfının birebir aynısıdır. **Geçiş sırası her zaman: ÖNCE DURDUR, SONRA BAŞLAT.**

`scripts/docker_run.sh` bu kapıyı uygular: hedef makinede `supervisorctl status`
`tradingbot_v2|tradingbot_ap|tradingbot_main` programlarından birini RUNNING gösterirse
**başlatmaz**. Uzak bir sunucuyu da yoklatmak için `DOCKER_PEER_SSH_HOST=awa` verin
(ssh başarısız olursa fail-closed davranır: yoklanamayan sunucuda motor çalışıyor
olabilir). Bilinçli istisna — **yalnız container AYRI bir Binance hesabının `.env`'ini
kullanıyorsa** — `DOCKER_ALLOW_ALONGSIDE=1`.

### Ne çalıştırılır
```bash
scripts/docker_run.sh              # build + up + sağlık bekle (kapılar dahil)
scripts/docker_run.sh --no-build   # yalnız up
scripts/docker_run.sh --down       # graceful durdur (stop_grace_period'a saygı)
scripts/docker_run.sh --logs       # son 200 satır, secret REDAKSİYONLU
```
Çıplak `docker compose up` **KULLANILMAZ**: entry-halt kilidini, 418 ban penceresini ve
supervisord kapısını atlar (aynı gerekçe `restart_safe.sh` ile çıplak `supervisorctl
restart` arasındaki farkta yazılıdır).

Testleri **container içinde** koşmak (deploy kapısı — görüntü `tests/`, `conftest.py`,
`pytest.ini` ve `.github/workflows/ci.yml`'i taşır; taban Python sürümü CI/sunucu ile
aynıdır):
```bash
docker compose -p tradingbot exec tradingbot python -m pytest tests -q -p no:cacheprovider
# ölçüldü (2026-08-24): 2021 passed, 2 skipped — host ile AYNI
```
Görüntüyü + container'ı **uçtan uca** doğrulayan duman testi (build → ayağa kalk →
`/health` → defter kalıcı volume'de → zarif SIGTERM kapanışı). **Opt-in'dir**: her
`pytest tests` koşusuna ~715 MB'lık bir derleme eklememek için varsayılan KAPALI
(`server_deploy.sh` test adımı `timeout 300` ile sarılıdır — kazara tetiklenen bir
derleme deploy'u geri aldırırdı):
```bash
TRADINGBOT_DOCKER_SMOKE=1 python3 -m pytest \
  tests/test_container.py::test_smoke_build_and_health -q
```

### Kalıcı veri nerede
Container **durum tutmaz**; her şey compose dosyasının yanındaki dizinlere bind-mount
edilir:

| Host yolu | Container | İçerik |
|---|---|---|
| `./.env` | `/app/.env` *(salt okunur)* | ayarlar + **SIRLAR** — görüntüye GÖMÜLMEZ |
| `./state/` | `/app/state` | cooldown, entry-halt, pending journal, TV olay durumu |
| `./logs/` | `/app/logs` | `bot.log`, `trades.jsonl`, `deploy.log` |
| `./backups/` | `/app/backups` | `.env` yedekleri (**anahtar içerir**) |
| `./data/` | `/app/data` | **`tradingbot.db`** + `klines_cache/` |

> ⚠️ **Defterin yeri container'da DEĞİŞİR.** Sunucuda `sqlite:///./tradingbot.db` (repo
> kökü). Container'da repo kökü *görüntünün içidir* ve kalıcı değildir. Tek bir DOSYAYI
> mount etmek de çözüm değildir: `src/core/database.py` `PRAGMA journal_mode=WAL`
> uygular ve sqlite `tradingbot.db-wal` / `-shm` kardeşlerini **aynı dizinde** üretir;
> yalnız `.db` mount edilseydi WAL container katmanında kalır ve container silindiğinde
> **checkpoint edilmemiş işlem kayıtları kaybolurdu.** Bu yüzden `docker-compose.yml`
> `DATABASE_URL=sqlite:///./data/tradingbot.db` verir. Süreç ortamı `.env`'i EZER
> (pydantic-settings önceliği: env > dotenv), yani **taşınan `.env` düzenlenmeden
> çalışır**.

### Başka sunucuya taşıma reçetesi

**0) Hedef makinede hazırlık** — docker + docker compose v2 kurulu, saat doğru.

**1) KAYNAKTA motoru durdur VE bir daha kendiliğinden kalkmayacağından emin ol**
(pozisyon varken bile: kapanış pozisyonu KAPATMAZ, yalnız bekleyen MAKER girişlerini
iptal eder ve pozisyonları borsada bırakır — yeni motor devralır).
```bash
ssh awa 'supervisorctl stop tradingbot_v2 && supervisorctl status tradingbot_v2'
# STOPPED görmeden devam etme (CLAUDE.md kural 6)
```
> ⛔⛔ **`autostart=false` YAPMADAN GEÇME.** `supervisorctl stop` YALNIZ ŞU ANI
> durdurur. supervisord program tanımı `autostart=true` ise **sunucu yeniden
> başladığında motor kendiliğinden geri gelir** — ve hedef makinedeki container
> `restart: unless-stopped` ile zaten ayaktadır. Sonuç: **İKİ MOTOR, AYNI HESAP**,
> kimse fark etmeden. Bu, bu bölümün en başındaki felaketin sessiz hâlidir.
> ```bash
> ssh awa 'grep -n autostart /etc/supervisor/conf.d/tradingbot-v2.conf'   # önce oku
>
> **ÖLÇÜLDÜ (2026-08-24, awa):** dosya adı `tradingbot-v2.conf` (tire, alt çizgi
> DEĞİL) ve içinde `autostart=true` + `autorestart=true` var — yani bu adım
> teorik değil, GERÇEKTEN gerekli. Sunucu saat dilimi **Europe/Stockholm
> (CEST, UTC+2)**; container `TZ=UTC` sabitler → aynı `logs/bot.log` içinde iki
> farklı damga ölçeği oluşur. Geçişte log dosyasını DÖNDÜR (rotate) ya da
> karışık damgalı pencereyi elle yorumla; `docker_run.sh`'nin 418 kapısı UTC,
> `server_deploy.sh`/`restart_safe.sh` yerel saat kullanır (ikisi de doğru,
> gerekçeleri script başlıklarında).
> ssh awa 'sed -i "s/^autostart=.*/autostart=false/" /etc/supervisor/conf.d/tradingbot_v2.conf \
>          && supervisorctl reread && supervisorctl update && supervisorctl status'
> ```
> Geri dönüşte (adım 6) bunu `autostart=true` yapmayı unutma.

**2) Veriyi tarball'la** (durdurduktan SONRA — sqlite WAL yazarken kopyalama tutarsız
olabilir). Tarball **ANAHTAR İÇERİR** (`backups/env.bak-*` = `.env` tam kopyaları), bu
yüzden `umask 077` + erişim logu hariç + transfer sonrası **imha**:
```bash
ssh awa 'cd /opt/tradingbot-v2 && umask 077 && tar czf /tmp/tradingbot-data.tgz \
  --exclude="logs/supervisor.log" \
  tradingbot.db tradingbot.db-wal tradingbot.db-shm state logs backups data 2>/dev/null; \
  chmod 600 /tmp/tradingbot-data.tgz; ls -la /tmp/tradingbot-data.tgz'
scp awa:/tmp/tradingbot-data.tgz .
ssh awa 'shred -u /tmp/tradingbot-data.tgz 2>/dev/null || rm -f /tmp/tradingbot-data.tgz'
```
*(`logs/supervisor.log` erişim logudur ve **secret içerir** — CLAUDE.md kural 5; tarball'a
alınmaz. `-wal`/`-shm` yoksa `tar` uyarır, sorun değil: temiz kapanışta checkpoint
edilmiştir.)*
> Hedefe açtıktan sonra yerel kopyayı da imha et:
> `shred -u tradingbot-data.tgz` (yoksa `rm -P` / `rm -f`).

**3) Hedefte kodu al ve veriyi yerleştir** — kod **GitHub'dan** gelir, scp ile dosya
kopyalama YASAKTIR (CLAUDE.md):
```bash
git clone https://github.com/YDX64/crypto-trading-bot.git tradingbot && cd tradingbot
tar xzf ../tradingbot-data.tgz
mkdir -p data && [ -f tradingbot.db ] && mv tradingbot.db* data/   # defteri data/ altına al
```

**4) `.env`'i ELLE taşı** — tarball'da `backups/` içindeki `.env` yedekleri **anahtar
içerir**; `.env`in kendisi ayrıca taşınmalıdır. **Asla** log/çıktı/commit'e dökmeyin
(CLAUDE.md kural 5):
```bash
ssh awa 'cat /opt/tradingbot-v2/.env' > .env && chmod 600 .env
```
> ⚠️ **İZİN TUZAĞI.** Container non-root (`bot`, uid 10001) koşar. `.env` root'a ait
> ve `600` ise container onu **OKUYAMAZ**; `settings` modül düzeyinde kurulduğu için
> uygulama **import'ta** `ValidationError` ile ölür ve `restart: unless-stopped`
> sonsuz bir çökme döngüsü kurar. `scripts/docker_run.sh` bunu kendisi düzeltir
> (sahipliği container uid'sine verir, `640` yapar) ve sonucu log satırında basar.
> Elle yapıyorsanız: `sudo chown 10001:10001 .env && chmod 640 .env`.
Genel kural: **`.env` olduğu gibi taşınır**, yalnız aşağıdakiler elden geçirilir
(sırayla — 1. madde en sık unutulan ve en sinsi olanıdır):

1. **`BINANCE_BIND_IP` → BOŞALTIN.** Bu değer **eski sunucunun çıkış IP'sidir**.
   Yeni makinede o IP yoktur; `src/core/config.py` doğrulayıcısı geçerli bir IP
   gördüğü için ayar **kabul edilir** ve hata ancak ilk Binance çağrısında
   jenerik bir bağlantı hatası olarak görünür — teşhisi zordur.
2. **Binance API anahtarının IP allowlist'ini taşımadan ÖNCE güncelleyin.**
   Anahtar eski sunucunun IP'sine kilitliyse yeni makineden gelen imzalı her
   istek reddedilir. Bu, bot tarafında düzeltilemez (Binance hesap ayarı).
3. **Hedefte NTP açık olsun** (`timedatectl` / `chronyd`). İmzalı istekler
   `recvWindow` ile zaman damgası doğrular; saat kayarsa Binance
   **`-1021 Timestamp for this request is outside of the recvWindow`** döner ve
   bot hiç emir açamaz. Container host'un saatini kullanır (`TZ=UTC` yalnız
   biçimi sabitler, saati DÜZELTMEZ).
4. `TELEGRAM_*` webhook/tünel adresleri hedef makineye göre.
5. `FOLLOWER_FORWARD_URL` → gömülü modda (D20b) **boş olmalı**.

**5) Başlat ve doğrula:**
```bash
scripts/docker_run.sh
curl -s localhost:9091/health | python3 -m json.tool
curl -s localhost:9091/scalper/status | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("scan_status"), d.get("entries_blocked_by"), d.get("kline_source"))'
docker compose -p tradingbot exec tradingbot python -m pytest tests -q -p no:cacheprovider
```
Defterin taşındığını kanıtla (sayı KAYNAKTAKİYLE aynı olmalı):
```bash
docker compose -p tradingbot exec tradingbot \
  python -c "import sqlite3;print(sqlite3.connect('data/tradingbot.db').execute('select count(*) from scalp_trades').fetchone())"
```

**6) Geri dönüş** — container'ı durdur, eski sunucuda supervisord'u geri aç:
```bash
scripts/docker_run.sh --down
ssh awa 'cd /opt/tradingbot-v2 && RESTART_LABEL=container-rollback scripts/restart_safe.sh testnet'
```
Container'ın ürettiği yeni işlem kayıtları eski sunucunun defterinde **YOKTUR**; geri
dönerken hangi defterin geçerli olduğuna karar verin (ikisini birleştirmeyin — aynı
`entry_order_id` iki kez girer).

### Container yolunun tuzakları

| Tuzak | Neden | Ne yapılır |
|---|---|---|
| **`BINANCE_BIND_IP` dolu** | Sunucudaki NordVPN policy routing çözümü soketi belirli bir **yerel IP**'ye bind eder. Bridge ağında container'ın ağ ad alanı ayrıdır, o IP orada **yoktur** → `EADDRNOTAVAIL`. | `.env`'de boşaltın (418 weight riski geri gelir, bkz. "REST ağırlık bütçesi"), ya da **yalnız Linux'ta** `docker-compose.yml`'de `network_mode: host` açıp `ports:`i kaldırın. ⛔ O durumda `command:`i de `--host 127.0.0.1` yapın: host ağında `--host 0.0.0.0` panoyu **TÜM arayüzlere** açar ve `ports:`in sağladığı localhost kısıtı kalkar. `docker_run.sh` bunu UYARIR. |
| **`--workers 2` cazibesi** | Bot **tek asyncio sürecidir**; state dosyaları, cooldown, sembol rezervasyonu ve pozisyon devralma süreç-genelidir. worker>1 = iki bağımsız motor = yukarıdaki "iki motor" felaketi. | `--workers 1` `Dockerfile`'da sabittir ve `tests/test_container.py` ile kilitlidir. Değiştirmeyin. |
| **Pano dışarı açık** | Erişim logu `?secret=` içerir. | Varsayılan yayın `127.0.0.1:9091` — dışarı açmak için bilinçli `TRADINGBOT_BIND=0.0.0.0`. Uzaktan bakmak için ssh tüneli kullanın. |
| **`unhealthy` damgası** | Docker `unhealthy` container'ı **yeniden başlatmaz** — bu KASITLIDIR: açık pozisyonu olan motoru sağlık yoklaması yüzünden restart döngüsüne sokmak zararlıdır. | Damgayı teşhis olarak okuyun; `scripts/docker_run.sh --logs` ile bakın. **autoheal benzeri araç KURMAYIN.** |
| **`restart: unless-stopped` ↔ "418'de restart yasak"** | Süreç ÇÖKERSE docker onu `docker_run.sh` kapılarını (ban penceresi, entry-halt) atlayarak geri getirir — CLAUDE.md yasak #3 ile gerilim. | Bilinçli seçim: alternatifi (`restart: no`) açık pozisyonlu botu süresiz kapalı bırakır ki bu daha büyük zarardır. Emniyet, uygulamanın KENDİ fail-closed kapılarıdır (yukarıdaki `state/` satırı: kilit kalıcı ve bozuk dosya bile halt sayılıyor — ölçüldü). Tekrarlayan çökmede doğru tepki `docker_run.sh --down` ile DURDURUP nedeni incelemektir. |
| **Kapanış süresi** | SIGTERM → lifespan `finally` → bekleyen MAKER girişleri iptal edilir (ağ çağrısı). Süre yetmezse SIGKILL gelir, iptal edilmemiş LIMIT emirleri **borsada asılı kalır**. | `stop_grace_period: 120s` verilidir; `docker stop -t 120` ya da `docker_run.sh --down` kullanın, `docker kill` **kullanmayın**. |
| **İkinci halka kazara açılır** | `docker-compose.yml` `tradingbot-follower` servisini taşır. | `profiles: ["follower"]` ile **varsayılan kapalıdır**; `docker compose up` başlatmaz. Zaten tercih edilen kurulum gömülü moddur (D20b). |
| **Zaman dilimi** | Ban penceresi ve loguru damgaları **yerel** saattir; modellerde naive `utcnow` kullanılır, yani günlük PnL rollover'ı ve cooldown pencereleri TZ'ye duyarlıdır. | Container `TZ=UTC` sabitlenmiştir (ölçüldü: `date -u +%Z` → `UTC`), host'un TZ'sinden bağımsızdır. Ama **saatin DOĞRU olması** ayrı bir iştir — host'ta NTP açık olmalı (yoksa Binance `-1021`). |
| **`state/` kalıcı olmazsa** | `state/scalper_entry_halt.json`, `risk_event_halt.json`, `tv_events.json` **fail-closed güvenlik dosyalarıdır**. Kalıcı olmasalardı restart entry-halt kilidini SESSİZCE siler ve bot kilitten sonra işlem açmaya devam ederdi. | `./state` kalıcı bind-mount'tur. **Ölçüldü:** host'a yazılan halt dosyası container içinde görüldü, uygulama `entry_halted=true` raporladı; dosya BOZUK olduğunda da `🚨 entry halt state okunamadı … fail-closed kapalı` (CRITICAL) ile **halt aktif** sayıldı. |
| **sqlite'ı ağ dosya sistemine koymak** | NFS/SMB/bazı overlay katmanları POSIX kilitlerini doğru uygulamaz; sqlite WAL **bozulur**. | `./data` **yerel** disk üstünde olmalı. Uzak depolama gerekiyorsa yedeği oraya kopyalayın, canlı defteri değil. |
| **Adli kayıt (forensics) kuyruğu** | `src/strategies/scalper/forensics_log.py` satırları bir **daemon** yazıcı iş parçacığının kuyruğunda tutar; `drain()` fonksiyonu VARDIR ama üretim kapanış yolunda **ÇAĞRILMAZ** — kapanışta kuyrukta bekleyen satırlar kaybolabilir. | Container'a ÖZGÜ değildir (supervisord restart'ında da aynı). D21 gereği forensics **yalnız gözlemdir**, motor davranışını etkilemez → işlem riski yok, teşhis boşluğu var. Ayrı bir değişiklikte lifespan kapanışına `forensics_log.drain()` eklenmeli. |

### Container ↔ supervisord farkları (bilerek)
* **Bind adresi:** supervisord `--host 127.0.0.1`, container `--host 0.0.0.0` (container
  ağ ad alanının içi) + host'ta `127.0.0.1:9091` yayını. Dışarıya açıklık aynıdır.
* **Defter yolu:** `./tradingbot.db` → `./data/tradingbot.db` (yukarıdaki WAL gerekçesi).
* **Kullanıcı:** container root DEĞİL (`bot`, uid 10001). `docker_run.sh` bind-mount
  dizinlerinin sahipliğini buna göre ayarlar; ayarlayamazsa host kullanıcınıza düşer
  (her iki durumda da root değildir).
* **Python:** `python:3.12-slim` — CI ve sunucu venv'i ile **aynı minor sürüm**
  (`.github/workflows/ci.yml`: *"Sunucu venv'i Python 3.12 — CI aynı sürümü kullanır
  (parite)"*). Parite
  `tests/test_container.py::test_dockerfile_python_version_matches_ci_and_server` ile
  kilitlidir; aksi hâlde "container'da testler geçti" bir deploy kapısı sayılmazdı.
* **Erişim logu:** supervisord'da `logs/supervisor.log` (**secret içerir, dökme**).
  Container'da böyle bir dosya YOKTUR; uvicorn erişim logu stdout'a → docker
  `json-file` sürücüsüne gider. Sızıntı riski `src/main.py`'deki
  `_SecretRedactionLogFilter` ile kapalıdır. **Ölçüldü:** container'a
  `?secret=<değer>` içeren istek atıldı; `docker logs` çıktısında satır
  `"GET /scalper/status?secret=*** HTTP/1.1" 200 OK` olarak göründü, ham değer
  loglarda **bulunamadı**. Yine de `docker logs`u harici bir log toplayıcıya
  yönlendirmeden önce bunu kendi kurulumunuzda doğrulayın.

### Bu bölümdeki iddiaların kanıtı (2026-08-24, yerel docker 27.4.0 / aarch64)
`scripts/docker_run.sh`in ve compose'un iddiaları **ölçülerek** yazıldı:
görüntü derlendi (715 MB, ~2 dk), container `env.example` ile ayağa kalktı,
`/health` **503 degraded** döndü (beklenen: sahte anahtarla
`Binance [401] -2014 API-key format invalid`), `/dashboard` 200 (88 KB),
defter mount edilen `data/` içinde oluştu, `docker stop -t 120` **1 sn**'de
`exit=0` ile bitti ve kapanış zinciri loga düştü
(`🛑 Uygulama kapatılıyor… → Scalper motoru durduruldu → Orchestrator kapatıldı
→ Veritabanı bağlantıları kapatıldı → ✅ Uygulama kapatıldı`).
Aynı akış `tests/test_container.py::test_smoke_build_and_health` ile
tekrarlanabilir (docker varsa koşar, yoksa atlanır).

## REST ağırlık bütçesi (D22) — ölçüm AÇIK, geri çekilme **VARSAYILAN KAPALI**
Binance USDⓈ-M IP ağırlık sınırı **2400/dk** ve sayaç **IP GENELİDİR** (aynı çıkış IP'sindeki
başka süreçler de tüketir). Bot bu bütçeyi ÖLÇER ve raporlar; **davranışını varsayılan olarak
DEĞİŞTİRMEZ** (`BINANCE_WEIGHT_SOFT_LIMIT=0`, `BINANCE_WEIGHT_HARD_LIMIT=0`).

> **Neden kapalı.** Testnet'te bu başlığın 2026-08-23 günlük **MEDYANI 2373** ölçüldü. İlk
> tasarımın 2000/2300 eşikleriyle açık olsaydı tarama turu KALICI dururdu ve bot hiç işlem
> açmazdı. **Eşik ölçmeden açılmaz.**

**Nereye bakılır (telemetri eşiklerden bağımsızdır):**
```bash
curl -s localhost:9091/scalper/status | python3 -c 'import json,sys; print(json.load(sys.stdin)["rest_weight"])'
# {"last":…, "last_at":…, "max_1m":…, "peak_at":…, "soft_backoffs":…, "hard_backoffs":…,
#  "soft_limit":0.0, "hard_limit":0.0, "enabled":false, "backoff":"off", …}
```
Panoda üst şeritte **Sistem durumu → Ağırlık** (son/dk + "dk tepe"; sarı = soft, kırmızı = hard).

**`max_1m` DAKİKA DİLİMLİDİR.** İçinde bulunulan takvim dakikasının tepesidir ve dakika başında
sıfırlanır — Binance'in 1M sayacı da orada sıfırlanır. Süreç ömrü boyu tutulan bir tepe farklı
dakikaları tek sayıya katlar ve aşağıdaki kuralı okunamaz kılar.

**Ne zaman normal, ne zaman arıza:**
- Tekil yüksek okumalar (testnet başlığı edge-bazlı ve tutarsızdır) → gürültü.
- **Aynı dakika diliminde** `max_1m` > 3000 tekrar tekrar görülüyor → gerçek risk. Sırayla bak:
  1. Ağırlık uyarısındaki endpoint dökümü (`… | son uyarıdan beri istekler:
     /fapi/v2/positionRisk×N, …`) — kim yiyor?
  2. Aynı IP'de başka bir bot/süreç var mı (`BINANCE_BIND_IP` ayrı IP'ye bind eder).
  3. Panonun açık kalması maliyetli DEĞİL (`/api/status` ve `/scalper/status` 5 sn
     sunucu-tarafı önbellek; pano yolundan `force_fresh` istenmez).

**Geri çekilmeyi AÇMAK (ölçümle):**
1. Birkaç gün `rest_weight.max_1m` dağılımını topla (yukarıdaki `curl`, dakikada bir).
2. `soft` eşiğini gözlenen **medyanın belirgin ÜSTÜNE**, `hard`ı 2400 sınırının hemen altına koy.
   Medyanın altındaki bir eşik = kalıcı durma.
3. `.env` → `BINANCE_WEIGHT_SOFT_LIMIT=<soft>`, `BINANCE_WEIGHT_HARD_LIMIT=<hard>`;
   `.env` yedeği (`backups/env.bak-<tarih>-weight`) + `docs/DECISIONS.md` satırı ZORUNLU.
4. Açıkken sözleşme:

| Ağırlık | Kritik OLMAYAN istekler | Kritik istekler | Log |
|---|---|---|---|
| < soft | normal | normal | — |
| ≥ soft | takvim dakikası dolana kadar GÖNDERİLMEZ; önbellek varsa BAYAT servis | geçer | ağırlık uyarısı ≤1/dk |
| ≥ hard | tamamen durur | geçer | + CRITICAL ≤1/dk |

- **Kritik** = emir, SL/TP, positionRisk koruma turu, kapanış doğrulaması, günlük risk income'ı.
- **Kritik olmayan** = `/api/status` pano beslemesi, tarama turu (`_scan_tick` hiç başlamaz),
  adli kayıt post-mortem turu.
- Açıkken görünürlük: `entries_blocked_by="rest_weight"`, `scan_status="degraded:rest_weight"`.

## Arızalar
**Binance 418 / ban:** `logs/bot.log`'da `HTTP 418|banned|devre kesici`. Ban aktifken restart
**YASAK** (ban süresini uzatır). Kök nedenler ve çözümler: rate limiter kilidi (mevcut), dashboard
force-fresh açlığı (düzeltildi), ağırlık geri çekilmesi (D22, yukarıdaki bölüm), ağırlık başlığı
testnet'te tutarsız (ortalama ~2.7k görünür, gerçek 429 yoksa gürültü). Bekle; ban bitince önce
`wait_for_binance` loglarını izle.

**`exit_reason=TRAIL_MARKET` / `BE_MARKET` gördüm (D22):** arıza DEĞİL. Koruyucu stop
(trailing / runner tabanı / break-even) borsaya gönderildi, Binance `-2021 Order would
immediately trigger` dedi (seviye piyasa tarafından çoktan geçilmiş) ve
`position_manager._emergency_close` pozisyonu reduce-only MARKET ile kapattı — bu davranış
D22'den ÖNCE de vardı; D22 yalnız kapanışın deftere DOĞRU etiketle ve GERÇEK dolum fiyatıyla
yazılmasını sağlar. Bot kendiliğinden piyasa emri göndermez. TRAIL ailesindendir, AYRI sayılır.
**Sayıları artıyorsa** stop kararı piyasa hızının gerisinde kalıyordur — parametre değişikliği
CLAUDE.md yasak #1'e tabidir (3 rejim backtesti). Telemetri:
`/scalper/status.trailing_skips` = `{price_space_skips, protective_gate_skips, market_exits}`.

Logda görülecek satır: `🔻 <SEMBOL>: … piyasa tarafından geçilmiş (-2021); ACİL KAPANIŞ
GERÇEKLEŞTİ …`. **"eski SL korunuyor" satırı YALNIZ pozisyon gerçekten açıkken yazılır** —
ikisini bir arada görüyorsan bu bir regresyondur.

Kapanış o turda borsada doğrulanamazsa (`koruma emirleri iptal EDİLMEDİ` satırı) koruma
emirlerine DOKUNULMAZ ve etiket saklanır; bir sonraki safety turu kapanışı aynı etiketle
deftere yazar. Elle müdahale gerekmez, ama satır tekrar ediyorsa pozisyonu borsada kontrol et.

**"Kapı bayat / gate_effective=false" ama kapı sağlam (D22):** önce
`/scalper/status.market_gate.stale_reason` bak. `"entries_blocked"` = tarama zaten durmuş
(nedeni `/scalper/status.entries_blocked_by`: `entry_halt` | `kill_switch` | `risk_event` |
`exchange_readiness` | `rest_weight`) — kapıyı kurcalama, önce girişleri kim durdurduysa onu çöz.
`"leader_stale"` = lider verisi gerçekten gelmiyor (bkz. lider piyasa kapısı bölümü).

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
ssh awa 'cd /opt/tradingbot-v2 && cp .env backups/env.bak-$(date -u +%Y%m%d-%H%M%S)-shadow && { grep -q "^SCALPER_SHADOW_MODE=" .env && sed -i "s/^SCALPER_SHADOW_MODE=.*/SCALPER_SHADOW_MODE=true/" .env || echo "SCALPER_SHADOW_MODE=true" >> .env; } && ./.venv/bin/python -c "from src.core.config import settings as s; assert s.scalper_shadow_mode, \"GÖLGE AÇILMADI — .env yazılmadı\"; print(\"shadow_mode=\", s.scalper_shadow_mode)" && RESTART_LABEL=shadow scripts/restart_safe.sh testnet'
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
ssh awa 'cd /opt/tradingbot-v2 && cp .env backups/env.bak-$(date -u +%Y%m%d-%H%M%S)-shadow-off && { grep -q "^SCALPER_SHADOW_MODE=" .env && sed -i "s/^SCALPER_SHADOW_MODE=.*/SCALPER_SHADOW_MODE=false/" .env || echo "SCALPER_SHADOW_MODE=false" >> .env; } && ./.venv/bin/python -c "from src.core.config import settings as s; assert not s.scalper_shadow_mode, \"GÖLGE HÂLÂ AÇIK — .env yazılmadı\"; print(\"shadow_mode=\", s.scalper_shadow_mode)" && RESTART_LABEL=shadow-off scripts/restart_safe.sh testnet'
```
Varsayılan zaten kapalı (satırı silmek de eşdeğerdir). Restart sonrası aynı iki doğrulamayı
(python `assert` + `GET /scalper/status`) `shadow_mode: false` bekleyerek tekrarla. ⚠️
Mainnet'te (testnet DEĞİLKEN) gölge KAPALIYSA `RISK_EVENT_SECRET`, `TV_WEBHOOK_SECRET` ve
`SCALPER_SYMBOL_ALLOWLIST` boş (veya yalnız boşluk/virgül) OLAMAZ — `_validate_binance_environment`
startup'ta reddeder (docs/MAINNET_PLAN.md §5.3); doldurmadan kapatamazsın.

## Lider piyasa kapısı (`SCALPER_MARKET_GATE`, D15) — AKTİF (testnet, 2026-08-23 11:14 UTC; eşik 1.3 / uzama 0)
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
ssh awa 'cd /opt/tradingbot-v2 && cp .env backups/env.bak-$(date -u +%Y%m%d-%H%M%S)-marketgate && for kv in "SCALPER_MARKET_GATE=true" "SCALPER_MARKET_GATE_SYMBOL=BTCUSDT" "SCALPER_MARKET_GATE_DAY_PCT=1.3" "SCALPER_MARKET_GATE_RUN_PCT=0"; do k="${kv%%=*}"; { grep -q "^$k=" .env && sed -i "s|^$k=.*|$kv|" .env || echo "$kv" >> .env; }; done && ./.venv/bin/python -c "from src.core.config import settings as s; assert s.scalper_market_gate, \"KAPI AÇILMADI\"; assert s.scalper_market_gate_day_pct == 1.3, f\"DAY_PCT={s.scalper_market_gate_day_pct}\"; assert s.scalper_market_gate_run_pct == 0, f\"RUN_PCT={s.scalper_market_gate_run_pct} — uzama alt-kapısı KAPALI olmalı\"; print(\"gate=\", s.scalper_market_gate, \"day=\", s.scalper_market_gate_day_pct, \"run=\", s.scalper_market_gate_run_pct)" && RESTART_LABEL=marketgate scripts/restart_safe.sh testnet'
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
ssh awa 'cd /opt/tradingbot-v2 && cp .env backups/env.bak-$(date -u +%Y%m%d-%H%M%S)-marketgate-off && { grep -q "^SCALPER_MARKET_GATE=" .env && sed -i "s/^SCALPER_MARKET_GATE=.*/SCALPER_MARKET_GATE=false/" .env || echo "SCALPER_MARKET_GATE=false" >> .env; } && ./.venv/bin/python -c "from src.core.config import settings as s; assert not s.scalper_market_gate, \"KAPI HÂLÂ AÇIK\"; print(\"gate=\", s.scalper_market_gate)" && RESTART_LABEL=marketgate-off scripts/restart_safe.sh testnet'
```
Varsayılan zaten kapalı (satırı silmek de eşdeğerdir); kod geri alınmasına gerek yok.
## Kline kaynağını mainnet'e alma (`SCALPER_MARKET_DATA_BASE_URL`, D17)
Ne yapar: public `/fapi/v1/klines` çekimi ve (yalnız ayrı host'ta) chandelier bazının
veri-tarafı referansı olan tek sembollük public `/fapi/v1/ticker/price` verilen host'tan
yapılır. Emir, bakiye, pozisyon, `get_current_price`, evren taraması (`ticker/24hr`),
`exchangeInfo` ve income `BINANCE_BASE_URL`'de KALIR — API anahtarı bu host'a asla gitmez.
Ek yük: açık pozisyon başına ~30 istek/dk ≈ 30 ağırlık/dk (safety turu 2 sn, TTL = tur);
`SCALPER_MAX_POSITIONS=3` ile en çok 90 ağırlık/dk — hesap `docs/ARCHITECTURE.md`
§"Kline ağırlık bütçesi". Amaç: testnet'te işlem yaparken RSI/Bollinger/
diverjans/rejim/ATR'yi GERÇEK piyasa mumlarından hesaplamak ve backtest harness'iyle (zaten
mainnet) aynı veriye oturmak. Ayrıntı: `docs/DECISIONS.md` D17.

⚠️ **Bu bir soak değişikliğidir**, ayar değil sinyal etkiler: yürüyen bir soak varken AÇMA
(değişiklikler üst üste bindirilirse atıf bulanıklaşır — bkz. D11 notu). **D16 risk paketi
2026-08-23 03:10 sunucu saatinde GERİ ALINDI**, yani bugün geçerli olan tek demet **D6**'dır;
"D6+D16 soak" ifadesi geçen eski metinler bu yüzden düzeltildi. Ayrı host kullanırken
`SCALPER_SYMBOL_ALLOWLIST` dolu olsun: işlem host'unda olup veri host'unda olmayan bir sembol
her taramada `Kline çekme kalıcı hata ... code=-1121` satırı üretir (tur kesilmez, yalnız o
sembol atlanır).

⚠️ `sed -i` eşleşme bulamazsa 0 ile çıkar (gölge modu bölümündeki tuzağın aynısı) — bu yüzden
`{ grep -q ... && sed ... || echo ...; }` grubu + restart'tan ÖNCE `assert`'li geri-okuma:
```bash
ssh awa 'cd /opt/tradingbot-v2 && cp .env backups/env.bak-$(date -u +%Y%m%d-%H%M%S)-klinesrc && { grep -q "^SCALPER_MARKET_DATA_BASE_URL=" .env && sed -i "s#^SCALPER_MARKET_DATA_BASE_URL=.*#SCALPER_MARKET_DATA_BASE_URL=https://fapi.binance.com#" .env || echo "SCALPER_MARKET_DATA_BASE_URL=https://fapi.binance.com" >> .env; } && ./.venv/bin/python -c "from src.core.config import settings as s; assert s.kline_source == \"separate\", \"KLINE KAYNAĞI DEĞİŞMEDİ — .env yazılmadı\"; print(\"market_data=\", s.market_data_base_url, \"| trading=\", s.binance_base_url)" && RESTART_LABEL=klinesrc scripts/restart_safe.sh testnet'
```
(Yedek damgası saat-dakika-saniye içerir — `server_deploy.sh`'nin `STAMP` deseni:
`date +%Y%m%d` kullanılsaydı aynı gün ikinci koşu TEMİZ yedeği ezerdi ve ertesi gün
"aynı-gün yedeği" hiç bulunmazdı.)
**ZORUNLU doğrulama — dördü geçmeden değişiklik YAPILMIŞ SAYILMAZ:**
1. Yukarıdaki komutun kendi `assert`'i restart'tan ÖNCE `market_data= https://fapi.binance.com |
   trading= https://testnet.binancefuture.com` basmalı (basmazsa komut `AssertionError` ile durur,
   restart hiç çalışmaz).
2. `.env` satırının gerçekten yazıldığı (yorum satırı/çift satır tuzağı):
   `ssh awa "grep -n '^SCALPER_MARKET_DATA_BASE_URL=' /opt/tradingbot-v2/.env"` → **tek** satır
   ve değeri `https://fapi.binance.com` olmalı.
3. Restart sonrası (~90 sn) BANNER satırı — kaynak: `engine._log_kline_source()`:
   `ssh awa 'grep "📡 Kline kaynağı" /opt/tradingbot-v2/logs/bot.log | tail -1'` (SON satır —
   `-m1` kullanma, dosyadaki İLK/eski restart'ı gösterir) →
   `📡 Kline kaynağı: fapi.binance.com (AYRI — emirler: testnet.binancefuture.com)`.
4. Çalışan süreçten (`GET /scalper/status`, alanlar `engine._kline_source_snapshot()`):
   ```bash
   curl -sS http://127.0.0.1:9091/scalper/status | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['kline_source']=='separate', d['kline_source']; assert d['scan_status']=='ok', d['scan_status']; print(d['market_data_base_url'], d['trading_base_url'], d['market_data_guard'])"
   ```
**İlk saat — gerçekten basılan dizeler** (eski metin, bilinmeyen-sembol yolunun HİÇ basmadığı
bir dize arıyordu; aşağıdakiler koddan doğrulanmıştır):

| Ne aranır | Anlamı / eylem |
|---|---|
| `grep -c "Kline çekme kalıcı hata" logs/bot.log` | Sembol veri host'unda YOK (`code=-1121`). Allowlist'ten çıkar. |
| `grep -c "Piyasa verisi host geneli engel" logs/bot.log` | 401/403/451 — WAF/coğrafi engel. Ayarı geri al. |
| `grep -c "🚫 Piyasa verisi IP ban" logs/bot.log` | GERÇEK ban (418/-1003). Ayarı geri al; **ban aktifken restart YASAK**. |
| `grep -c "⏳ Piyasa verisi hız sınırı" logs/bot.log` | Tek başına 429 (soft throttle) — ban değil; tekrarlıyorsa bütçeyi/TOP_N'i düşür. |
| `grep -c "⚖️ Piyasa verisi ağırlık bütçesi doldu" logs/bot.log` | Kendi 600/dk tavanımız bağladı — hesap (ARCHITECTURE §2) ile gerçek arasında sapma var. |
| `grep -c "koruma tarafında değil" logs/bot.log` | Ötelenmiş trailing SL yanlış tarafa düştü, emir gönderilmedi. Sürekliyse iki defter arasındaki baz bozuk. |
| `grep -c "canlı fiyatı okunamadı" logs/bot.log` | Veri host'unun `ticker/price` okuması başarısız — baz ölçülemedi, trailing turu atlandı (SL yerinde). Sürekliyse veri host'u/ağ arızalıdır. |

Hepsi 0 olmalı. Çalışan süreçten aynı üç sayaç:
```bash
curl -sS http://127.0.0.1:9091/scalper/status | python3 -c "import sys,json; d=json.load(sys.stdin); print('kline_source=',d['kline_source'],'| scan_status=',d['scan_status'],'| guard=',d.get('market_data_guard'),'| trailing_skips=',d.get('trailing_skips'))"
```
`scan_status` `"ok"` olmalı; `"degraded:market_data"` = son tarama turu piyasa verisi
kesintisiyle YARIDA kesildi (sağlık yeşil kalır — bilinçli, watchdog restart'ı ban ortasında
felaket yoludur). `market_data_guard.hard_ban=true` = gerçek ban.

ℹ️ Yan etki (bilinçli): GERÇEK ban satırı `HTTP 418`/`banned` içerdiği için
`scripts/server_deploy.sh`'nin "son 15 dk'da ban izi" kilidi MAİNNET VERİ banında da deploy'u
reddeder — testnet emirleri etkilenmemiş olsa bile. Yanlış-pozitif tarafta kalmak bilinçli
tercihtir; acil deploy gerekiyorsa önce ayarı geri al, 15 dk bekle. Tek başına 429 bu kilidi
**tetiklemez** (satırda "banned" geçmez) — bir hız uyarısı deploy'u 15 dakika kilitlememeli.

**Geri alma (tek satır, restart dahil — YEDEK DOSYASINA BAĞLI DEĞİL):**
```bash
ssh awa 'cd /opt/tradingbot-v2 && cp .env backups/env.bak-$(date -u +%Y%m%d-%H%M%S)-klinesrc-off && { grep -q "^SCALPER_MARKET_DATA_BASE_URL=" .env && sed -i "s#^SCALPER_MARKET_DATA_BASE_URL=.*#SCALPER_MARKET_DATA_BASE_URL=#" .env || true; } && ./.venv/bin/python -c "from src.core.config import settings as s; assert s.kline_source == \"trading_host\", \"KLINE KAYNAĞI HÂLÂ AYRI — .env yazılmadı\"; print(\"kline_source=\", s.kline_source)" && RESTART_LABEL=klinesrc-off scripts/restart_safe.sh testnet'
```
Bilinçli olarak `cp backups/env.bak-...` KULLANILMAZ: soak günlerce sürer, "bugünün"
yedeği ertesi gün yoktur ve acil geri alma tam da o anda `cp: No such file` ile ölürdü.
Satırı boşaltmak = varsayılan (kapalı); silmek de eşdeğerdir. Restart sonrası aynı dört
doğrulamayı `trading_host` bekleyerek tekrarla.
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
ssh awa 'cd /opt/tradingbot-v2 && cp .env backups/env.bak-$(date -u +%Y%m%d-%H%M%S)-tvevents && sed -i "s/^TV_SOURCE_ALLOWLIST=.*/&,luxso_exit,luxso_trend,pac_choch,algopro_tp1/" .env'
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
ssh awa 'cd /opt/tradingbot-v2 && cp .env backups/env.bak-$(date -u +%Y%m%d-%H%M%S)-tvmode && { grep -q "^SCALPER_TV_EVENTS_MODE=" .env && sed -i "s/^SCALPER_TV_EVENTS_MODE=.*/SCALPER_TV_EVENTS_MODE=shadow/" .env || echo "SCALPER_TV_EVENTS_MODE=shadow" >> .env; }'
ssh awa 'cd /opt/tradingbot-v2 && ./.venv/bin/python -c "
from src.core.config import settings as s
assert s.scalper_tv_events_mode == \"shadow\", s.scalper_tv_events_mode
print(\"mode=\", s.scalper_tv_events_mode)" && RESTART_LABEL=tv-events scripts/restart_safe.sh testnet'
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

Elle sağlama **DAİMA `?dry_run=1` ile** (secret'ı komut satırına YAZMA — `.env`'den
oku). `dry_run` **her iki yolda da** yan etkisizdir: OLAY yolunda deftere yazmaz,
GİRİŞ yolunda sağlamaya oy yazmaz, `external_signal`'ı ve takipçi köprüsünü
ÇAĞIRMAZ. ⚠️ `dry_run` OLMADAN aynı komut canlı deftere gerçek bir olay yazar,
`active` modda açık pozisyonu etkiler ve giriş yoluna düşerse **GERÇEK EMİR AÇAR**:
```bash
# (a) OLAY yolu — yapı/çıkış alarmı şablonunu doğrula
ssh awa 'cd /opt/tradingbot-v2 && S=$(grep ^TV_WEBHOOK_SECRET= .env | cut -d= -f2-) && curl -sS -X POST "http://127.0.0.1:9091/tv-signal?secret=$S&dry_run=1" -d "src=pac_choch kind=choch bearish BTCUSDT" | python3 -m json.tool'
# (b) GİRİŞ yolu — mevcut 49 alarmdan birinin gövdesini doğrula
ssh awa 'cd /opt/tradingbot-v2 && S=$(grep ^TV_WEBHOOK_SECRET= .env | cut -d= -f2-) && curl -sS -X POST "http://127.0.0.1:9091/tv-signal?secret=$S&src=luxso&dry_run=1" -d "LuxAlgo Bullish Confirmation BTCUSDT.P" | python3 -m json.tool'
```
Beklenen (a): `"routed": "event"`, `"kind": "choch"`, `"direction": "SHORT"`,
`"source": "pac_choch"`, `"dry_run": true`.
Beklenen (b): `{"dry_run": true, "would": {"symbol": "BTCUSDT", "direction": "LONG",
"source": "luxso"}}` — `routed`/`accepted` alanı YOKTUR.
Teşhis: **(a) çağrısı `would` döndürüyorsa** istek GİRİŞ yoluna düşmüştür → `src=`/
`kind=` belirteçleri mesajın BAŞINDA değildir (bkz. `docs/INTEGRATIONS.md` §7.1
"başlık koşusu"). **422 + "yanlış şablon"** alıyorsan belirteçler ortadadır ve kanal
seni bilerek durdurmuştur — mesajı düzelt, alarmı silme.

**5) Olay defterini sıfırlama (yalnız gerektiğinde):** ⚠️ `state/tv_events.json`
dosyasını SİLMEK çalışan süreci temizlemez — defter RAM'de otoritedir ve bir
sonraki olayda dosyayı geri yazar. İki doğru reçete var:
```bash
# (a) süreci durdurmadan: reset uç noktası
ssh awa 'cd /opt/tradingbot-v2 && S=$(grep ^TV_WEBHOOK_SECRET= .env | cut -d= -f2-) && curl -sS -X POST "http://127.0.0.1:9091/tv-events/reset?secret=$S" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[\"reset\"], d[\"cleared_symbols\"], d[\"persisted\"])"'
# (b) bakım penceresinde: dosyayı sil + restart (SIRA ÖNEMLİ)
ssh awa 'cd /opt/tradingbot-v2 && rm -f state/tv_events.json && RESTART_LABEL=tv-events-reset scripts/restart_safe.sh testnet'
```
Defteri boşaltmak bir RİSK kapısını açmaz: en kötü sonucu kapı/çıkış tetiğinin
yeni olay gelene kadar sessizleşmesidir (fail-open, `docs/INTEGRATIONS.md` §7.4).

**Tuzaklar (D19a):**
- Bir çıkış alarmının mesajından `kind=` düşerse **ya da belirteçler mesajın
  BAŞINDA değilse** istek **422** alır — TV alarm günlüğünde "webhook failed"
  görürsün. İki farklı mesaj çıkabilir: gövdede `src=<olay kaynağı>` varsa
  `olay kaynağı giriş oyu veremez`, `src=` düşmüşse `olay alarmı yanlış şablon`.
  Bu BİLİNÇLİDİR: alternatifi, o alarmın sessizce bir GİRİŞ OYUNA dönüşüp
  pozisyon açmasıydı. Çözüm: mesajı `src=… kind=… {{ticker}}` sırasına getir
  (şablonlar: INTEGRATIONS §7.2); alarmı SİLME.
- **`src=`'i düşürme.** "Zaten `?src=` var" diye gövdeden çıkarırsan kaynak kimliği
  kaybolur (kapı `GATE_SOURCES` ile eşleşmez) VE birinci kalkan devre dışı kalır.
  İkinci kalkan (`kind=` taraması) yalnız gövde tanınan bir GİRİŞ biçimi değilse
  korur (INTEGRATIONS §7.1/§7.2). Sayaç: `tv_events.counters.rejected_entry_kind_mention`.
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

## AI karar katmanını açma/kapama (`SCALPER_AI_GATE_MODE`, D23) — GÖLGE

**Ne yapar:** motor pozisyonu AÇTIKTAN sonra, açılan işlemin bağlamını bir dil
modeline sorar: *"bu giriş alınmalı mıydı?"*. Karar `logs/trades.jsonl`
(`event="ai_verdict"`) ve `scalp_trades.forensics` içindeki `ai` bloğuna yazılır.
**Motor davranışı DEĞİŞMEZ** (gölgede karar yolu bayt bayt aynıdır) ve kanca
`_entry_lock` DIŞINDA, ateşle-unut çalışır — motor 0 ms bekler. Kod varsayılanı
**`off`**: katman `.env` ile BİLİNÇLİ açılır. Sözleşme, go_live ölçütleri ve
E8.6 uyarısı: `docs/DECISIONS.md` D23.

**1) Sağlayıcı anahtarını doğrula** (zincir: DeepSeek → Gemini → OpenAI; sunucuda
ANTHROPIC anahtarı YOKTUR). Anahtar yoksa ya da `your_...` yer tutucusuysa o
sağlayıcı ATLANIR:
```bash
ssh awa 'cd /opt/tradingbot-v2 && for k in DEEPSEEK_API_KEY GEMINI_API_KEY OPENAI_API_KEY; do v=$(grep "^$k=" .env | cut -d= -f2-); case "$v" in ""|your_*) echo "$k: YOK/yer tutucu -> atlanir";; *) echo "$k: var";; esac; done'
```

**2) `.env` satırlarını ekle** (yedek + ekleme; `sed` yerine "varsa değiştir,
yoksa ekle" kalıbı — `sed -i` eşleşme bulamazsa exit 0 verir ve `||` sağı hiç
çalışmaz):
```bash
ssh awa 'cd /opt/tradingbot-v2 && cp .env backups/env.bak-$(date -u +%Y%m%d-%H%M%S)-aigate && for line in "SCALPER_AI_GATE_MODE=shadow" "SCALPER_AI_GATE_PROVIDER=deepseek" "SCALPER_AI_GATE_DEEPSEEK_MODEL=deepseek-chat" "SCALPER_AI_GATE_MAX_CALLS_PER_DAY=200"; do k=${line%%=*}; grep -q "^$k=" .env && sed -i "s|^$k=.*|$line|" .env || echo "$line" >> .env; done && grep ^SCALPER_AI_GATE_ .env'
```
ℹ️ **`SCALPER_AI_GATE_DEEPSEEK_MODEL=deepseek-chat` gölge fazı için ÖNERİDİR**
(ucuz ve katı JSON'u iyi üretir). Satırı BOŞ bırakırsan ya da hiç yazmazsan genel
`DEEPSEEK_MODEL` kullanılır — o da `deepseek-reasoner`dır ve **pahalıdır**;
günlük 200 çağrıda maliyet ölçütünü ($2/gün, D23 go_live #9) zorlar.

**3) Yeniden başlat — ÇIPLAK `supervisorctl restart` YASAK** (D20a): `.env`
değiştiyse doğru reçete güvenli yeniden başlatmadır (entry-halt/418/açık pozisyon
kontrolleri + sağlık yoklaması + etiketli kayıt):
```bash
ssh awa 'cd /opt/tradingbot-v2 && RESTART_LABEL=d23-shadow-ac scripts/restart_safe.sh testnet'
```
Geçersiz bir değer startup'ta **ValueError** ile reddedilir
(`config._validate_ai_gate_settings`) — yazım hatası sessizce `off`a DÜŞMEZ,
süreç hiç kalkmaz. `supervisorctl status tradingbot_v2` FATAL gösteriyorsa
`logs/bot.log`'un ilk satırlarına bak.

**4) Doğrulama (restart'tan ~90 sn sonra):**
```bash
# (a) mod / kapsama / gecikme / bütçe / maliyet tahmini
ssh awa 'curl -s http://127.0.0.1:9091/scalper/status | jq .ai_gate'
# jq yoksa:
ssh awa 'curl -sS http://127.0.0.1:9091/scalper/status' | python3 -c "import sys,json; d=json.load(sys.stdin)['ai_gate']; print({k:d.get(k) for k in ('mode','effective_mode','applies_decisions','provider','providers_ready','candidates','verdicts_ok','coverage_pct','json_valid_pct','allow','deny','deny_ratio_pct','latency_ms','calls','max_calls_per_day','runaway','cost_estimate_usd_today','last_error')})"
# (b) tek tek kararlar (ai_skipped satırları = işleme dönüşmeyen adaylar)
ssh awa 'jq -c "select(.event==\"ai_verdict\")" /opt/tradingbot-v2/logs/trades.jsonl | tail -20'
# (c) gölge raporu (kapsama, deny kümesi ortalama PnL + %95 GA, allow kümesi PF,
#     eksen x PnL korelasyonu, maliyet)
ssh awa 'cd /opt/tradingbot-v2 && ./.venv/bin/python scripts/ledger_report.py --ai --since 2026-08-24'
```
Beklenen: `mode=shadow`, `effective_mode=shadow`, `applies_decisions=false`,
`providers_ready` en az bir sağlayıcı içeriyor. Panoda **"AI Karar Katmanı
(gölge)"** kartı görünür (kart `/api/status` gövdesinden beslenir, YENİ UÇ YOK).

`candidates>0` ama `verdicts_ok=0` ise sağlayıcı tarafına bak: `last_error` ve
`errors` sözlüğü (`ai_unavailable` / `ai_malformed` / `ai_stale` /
`ai_budget_exhausted`) nedeni söyler. Hiçbiri girişleri ETKİLEMEZ (fail-open).

**5) KAPATMA (tek satır geri alma):**
```bash
ssh awa 'cd /opt/tradingbot-v2 && cp .env backups/env.bak-$(date -u +%Y%m%d-%H%M%S)-aigate-off && sed -i "s/^SCALPER_AI_GATE_MODE=.*/SCALPER_AI_GATE_MODE=off/" .env && RESTART_LABEL=d23-shadow-kapat scripts/restart_safe.sh testnet'
```
Kod geri alma GEREKMEZ: `off` iken katman hiç örneklenmez, sıfır çağrı ve sıfır
maliyet üretir.

**Tuzaklar:**
- Katman **kapalıyken `/api/status` gövdesinde `ai_gate` anahtarı HİÇ olmaz** ve
  pano kartı gizlenir. Bu bir ARIZA DEĞİLDİR — "alan yok" ile "katman kapalı"yı
  ayırmak için bilinçli seçilmiş şekildir.
- **`SCALPER_AI_GATE_MODE=active` denenirse süreç AÇILMAZ** (config fail-fast:
  *"D23 canlı kapı henüz onaylanmadı — go_live ölçütleri docs/DECISIONS.md
  D23"*). Bu KASITLIDIR: kod yolu hazırdır ama ölçüm tamamlanmadan `.env`'de tek
  kelimeyle canlı bir kapı açılamaz. Ayrıca `active` motora kablolanmamıştır ve
  kablolanmadan önce harness/motor paritesi (DECISIONS #P1) gerekir.
- **Sağlayıcı anahtarı yoksa ya da `your_...` yer tutucusuysa** o sağlayıcı
  atlanır; zincirin hepsi atlanırsa karar `ai_unavailable` olur ve **giriş normal
  sürer** (fail-open). Katman sessizce "çalışıyor görünüp" bir şeyi durdurmaz.
- **`runaway` bayrağı yandıysa** (son 20 kararın %60'ından fazlası `deny`) katman
  kendini `shadow`a düşürmüştür ve bayrak yanık kalır. Sıfırlanması için süreç
  yeniden başlatılır (`restart_safe.sh`). `/scalper/status` → `ai_gate.runaway` /
  `runaway_at`.
- **Gölge sayılarını okurken E8.6 uyarısını hatırla:** engellenen bir girişin
  faydasının %100'ü boşalan işgal penceresine giren YENİ işlemlerden geliyordu ve
  **gölgede kapasite boşalmaz**. Gölge, faydanın muhtemelen en küçük parçasını
  ölçer (iki yönlü uyarı, `docs/DECISIONS.md` D23).

**Ne görürsün:**
- Katman kurulamazsa TEK SEFER: `⚠️ AI karar katmanı kurulamadı/çalışmadı (…) —
  bu uyarı bir kez loglanır, GİRİŞLER ETKİLENMEZ (D23 fail-open)`
- Kaçak korumasında: `⚠️ AI kapısı KAÇAK koruması: son 20 kararın N'i deny
  (> %60) — katman 'shadow'a düşürüldü, 'ai_runaway' bayrağı yandı (D23)`

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
`cp backups/env.bak-20260823-025623-riskpaketi .env && RESTART_LABEL=rollback scripts/restart_safe.sh testnet`
(+240 sn sağlık). Soak raporu: `scripts/ledger_report.py --since "2026-08-23 02:57"`.
Backtest/autoresearch tabanı: `scripts/.scalper_env_snapshot.txt` güncellendi — eski sayılarla
(E4/E5/E6 tabanı) karşılaştırırken ölçek farkını (marj %10→5 = PnL/DD ×0.5) hesaba kat.

## Yeni env anahtarı getiren kod deploy'u — SIRA (2026-09-04 dersi, D33)

`Settings` `extra_forbidden`'dır: `.env`'de ESKİ kodun tanımadığı bir anahtar varsa süreç BAŞLAMAZ.
2026-09-04 02:09'da env önce yazılıp sonra deploy edilince, deploy testte düşüp eski koda döndü ve eski
kod yeni anahtarlı `.env` ile açılamadı → `tradingbot_v2` 2,5 dk EXITED (env yedeği geri yüklenerek kalktı).
DOĞRU SIRA: (1) kodu `scripts/deploy.sh awa` ile deploy et (`.env` DEĞİŞMEDEN; yeni alanlar varsayılan
kapalı), sağlık yeşil; (2) `.env`'e anahtarı ekle (yedekle); (3) `RESTART_LABEL=<etiket>
scripts/restart_safe.sh testnet`. Geri alma gerekirse ÖNCE `.env`'den anahtarı çıkar, SONRA eski commit'e dön.
Ayrıca sunucu testleri canlı `.env` ile koşar: yeni bir kapı `.env`'de AÇIKKEN deploy testleri (ör.
`test_backtest_measurement::TestCliWiring`) sabit fixture'ları bloklayıp düşebilir — testler kapı
alanlarını `tests/conftest.py` autouse fixture'ıyla varsayılana sabitler (D33c).
