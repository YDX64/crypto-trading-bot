---
tags: [isletme, gunluk, kontrol]
guncelleme: 2026-08-24
kaynak: docs/RUNBOOK.md "Kimlik karti" + "Gunluk kontrol" (satir 3-45)
---
# Gunluk kontrol (2 dakika)

## Kimlik karti

| | |
|---|---|
| Sunucu | `awa` (ssh alias), `/opt/tradingbot-v2` |
| Surec | supervisord `tradingbot_v2` → `.venv/bin/python -m uvicorn src.main:app --host 127.0.0.1 --port 9091` |
| Ag | Binance Futures **TESTNET** |
| Komutlar | `supervisorctl status\|restart\|pid tradingbot_v2` |
| Loglar | `logs/bot.log` · `logs/supervisor.log` (**secret icerir**) · `logs/deploy.log` |
| Cron | `tradingbot-v2-watchdog.sh` (her dk) · `tradingbot-v2-backup.sh` (05:17) |
| Pano | `ssh -L 9091:127.0.0.1:9091 awa` → http://127.0.0.1:9091 |
| ⚠️ Tuzak | `systemctl`'deki `live-bot.service` **FUTBOL BOTU**; trading botu systemd'de DEGIL |

## En hizli yol: panonun ust seridi

**Sistem durumu** (D22): Kapi · Kline kaynagi · Gunluk kesici · REST agirligi ·
TV olaylari · Post-mortem kuyrugu — tek satirda. Serit MEVCUT
`/scalper/status` cagrisindan beslenir, **yeni istek acmaz**.

Terminalden ayni bilgi:
```bash
curl -s localhost:9091/scalper/status | python3 -c 'import json,sys; d=json.load(sys.stdin); print({k:d.get(k) for k in ("entries_blocked_by","kill_switch_active","kline_source","scan_status")}, d.get("rest_weight"), d.get("market_gate",{}).get("stale_reason"))'
```

## Surec + defter

```bash
ssh awa 'supervisorctl status tradingbot_v2; tail -3 /opt/tradingbot-v2/logs/bot.log | cut -c1-160'
```

Acik pozisyonlar ve bugunku sonuc icin `scalp_trades` sorgusu
(`docs/RUNBOOK.md` satir 15-45'teki hazir blok).

## Haftalik

```bash
python3 scripts/ledger_report.py --since "2026-08-14 00:00" --format md
python3 scripts/ledger_report.py --since "2026-08-23" --forensics --format md
```
Rapor canli defteri **BTC gunluk %'sine gore UP/FLAT/DOWN'a boler** ve mainnet
soak kontrol listesini PASS/FAIL yazdirir. **Hukum vermez.**
Ag yoksa `--btc-klines-json <dosya>`.

> **"Kazaniyor" yalniz uc rejimde de dogruysa soylenir.**
> ([[30-deneyler/canli-defter-rejim-analizi]])

## Bir islemi inceleme (3 yol)

1. **Pano** → "Son Islemler" satirina TIKLA → adli kart (neden girildi /
   nasil cikildi / ne ters gitti).
2. **Uclar:** `/scalper/trades/{id}/forensics`,
   `/scalper/forensics/recent?limit=20`,
   `/scalper/forensics/summary?since=7d`.
3. **Olay akisi:** `logs/trades.jsonl` + `jq`.

## Takvim

- **2026-09-14** — 49 TV alarmi expire; secret rotasyonu + TLS ile birlestir.
- **2026-11-21** — LuxAlgo aboneligi kararı.
- Her `.env` degisikligi → `docs/DECISIONS.md` satiri + yedek dosyasi.

ILGILI: [[40-isletme/sorun-giderme]] · [[50-veri/metrikler]] · [[10-mimari/gozlem-katmanlari]] · [[40-isletme/panel-erisimi]]
