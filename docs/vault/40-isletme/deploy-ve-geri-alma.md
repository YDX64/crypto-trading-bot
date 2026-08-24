---
tags: [isletme, deploy, geri-alma, restart]
guncelleme: 2026-08-24
kaynak: docs/RUNBOOK.md "Deploy ve geri alma" (satir 100-215), scripts/deploy.sh, scripts/restart_safe.sh
---
# Deploy ve geri alma

## Altin kural

**Tek gercek kaynak GitHub `YDX64/crypto-trading-bot` `main`.**
Sunucu repo'su onu izler. **`scp` ile dosya kopyalamak YASAK.**

## Komutlar

```bash
scripts/deploy.sh awa                        # testnet halkasi (varsayilan)
DEPLOY_NO_RESTART=1 scripts/deploy.sh awa    # yalniz kod/test; sureci baslatma
scripts/deploy.sh awa <commit>               # elle geri alma
scripts/deploy.sh awa --ring follower        # takipci halkasi
scripts/deploy.sh awa v1.2.0 --ring mainnet  # yalniz etiket + elle 'MAINNET' onayi
```

**Deploy on kosullari (script kendisi denetler):**
entry-halt dosyasi YOK · son 15 dk ban izi YOK · temiz agac ·
yerel HEAD == `origin/main` · `RING` ile `.env`'deki `BOT_MODE` uyumlu.

Geri alma mantigi **UC halkada ORTAKTIR** (`scripts/server_deploy.sh`);
her halka kendi `backups/commit.prev-<tarih>`ine doner.

## `.env` degisikligi = AYRI bir adim

⛔ **Ciplak `supervisorctl restart` KULLANILMAZ** — ban penceresini, entry-halt
kilidini, `.env` yedegini ve saglik yoklamasini ATLAR.

```bash
RESTART_LABEL=<etiket> scripts/restart_safe.sh testnet|follower|mainnet
```
Sirasiyla: halka↔`BOT_MODE` kontrolu → entry-halt kontrolu → ban penceresi
(son 15 dk `HTTP 418|banned`) → **saniye damgali** `.env` yedegi →
`.env` parse dogrulamasi → restart → saglik yoklamasi (240 sn'ye kadar).
Herhangi biri basarisizsa **restart YAPILMAZ**.

## Restart'i KANITLA

```bash
ssh awa 'supervisorctl status tradingbot_v2; ps -o etimes= -p $(supervisorctl pid tradingbot_v2)'
```
**Acilis suresi ~90 sn** (Binance init + pozisyon devralma); deploy 240 sn'ye
kadar yoklar. 2026-08-21'de 30 sn'lik sabit bekleme yanlis alarmla otomatik
geri alma tetikledi — mekanizma dogru calisti, **esik duzeltildi**.

## Halkalar arasi `.env` farki (salt okunur, secret maskeli)

```bash
scripts/ring_env_diff.sh awa                                   # v2 ↔ mainnet
MAIN_ENV=/opt/tradingbot-ap/.env scripts/ring_env_diff.sh awa  # v2 ↔ takipci
```
Kapsam: `BINANCE_*`, `SCALPER_*`, `TV_*`, `RISK_*`, `FOLLOWER_*`, `BOT_MODE`.
⚠️ **Kapsam DISI:** `API_PORT`, `DATABASE_URL`, `TELEGRAM_*`, `APP_ENV`,
`LOG_LEVEL` — bunlara ELLE bak.

## Testler

```bash
python3 -m pytest tests -q          # 2251 passed, 2 skipped (~66 sn)
cp env.example .env                 # .env yoksa (CI de boyle yapar)
```

## Yasaklar

1. Binance ban aktifken restart.
2. Entry-halt dosyasi varken deploy.
3. Mainnet'e `origin/main` ucu.
4. Ayni oturumda iki halkayi birden degistirmek.
5. Container ile supervisord'u ayni anda calistirmak
   ([[40-isletme/halka-yonetimi]]).

ILGILI: [[40-isletme/halka-yonetimi]] · [[20-kararlar/mainnet-plani]] · [[10-mimari/guvenlik-kilitleri]] · [[90-ai-icin/dogrulama-receteleri]]
