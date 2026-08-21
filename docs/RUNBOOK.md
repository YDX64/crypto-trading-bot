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
Haftalık: canlı defteri rejime böl (BTC günlük % → UP/FLAT/DOWN) ve yön/çıkış kırılımı çıkar
(bkz. `docs/EXPERIMENTS.md` "rejim analizi" kalıbı). "Kazanıyor" yalnız üç rejimde de doğruysa söylenir.

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

## Güvenlik borçları
1. Webhook düz HTTP + IP, secret sorgu dizesinde. Erişim logu kısmı ÇÖZÜLDÜ (D9,
   2026-08-21): `uvicorn.access`/`uvicorn.error` logger'larına `secret=...`'ü `secret=***`
   yapan bir filtre eklendi (`src/main.py`). Açık kalan: TLS + log rotasyonu.
2. Futbol botu kodunda gömülü RapidAPI anahtarı (`/root/bots/LIVE1/main.py`) — ayrı proje, bilgi.
3. ~~Sunucu venv'i requirements.txt'in gerisinde~~ ÇÖZÜLDÜ (2026-08-21 17:46): yeni venv yanına kuruldu,
   541 testle doğrulandı, `mv` ile değiştirildi (cryptography 50, python-multipart 0.0.31, pytest 9).
   Geri alma: `mv .venv .venv-failed && mv .venv-old .venv && supervisorctl restart tradingbot_v2`.
   `.venv-old` 1 hafta sonra silinebilir. Reçete: yeni venv → test → swap → restart → sağlık yokla.
