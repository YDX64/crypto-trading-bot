---
tags: [veri, log, secret, guvenlik]
guncelleme: 2026-08-24
kaynak: src/core/logger.py, src/strategies/scalper/forensics_log.py, docs/RUNBOOK.md
---
# Loglar — hangisi nerede, hangisi SECRET icerir

## Dosyalar

| Dosya | Ne | Rotasyon / saklama | Secret? |
|---|---|---|---|
| `logs/bot.log` | uygulama (DEBUG+) | 100 MB / 30 gun | hayir |
| `logs/trades.log` | insan-okur **denetim izi** (trade kayitlari) | 50 MB / 90 gun | hayir |
| `logs/errors.log` | ERROR+ (backtrace + diagnose) | 50 MB / 60 gun | hayir |
| **`logs/supervisor.log`** | supervisord'un yakaladigi **erisim logu** | conf'ta 10 MB × 5 | ⚠️ **EVET** |
| `logs/trades.jsonl` | D21 **makine sozlesmesi** olay akisi | gunluk rotasyon / 30 gun | hayir |
| `logs/deploy.log` | deploy izleri | — | hayir |
| `logs/autoresearch/<tarih>/` | autoresearch ham loglari | — | hayir |

Loguru kurulumu `src/core/logger.py:30` blogundadir.
Dizin `TRADINGBOT_LOG_DIR` ile degistirilebilir
(`src/core/logger.py:54`) — testler prod izini kirletmesin diye.

## ⛔ `logs/supervisor.log` SECRET ICERIR

TradingView webhook secret'i `?secret=...` query'sinde tasinabiliyor
(LuxAlgo "Any alert" modu) ve uvicorn erisim logu tam istek satirini yaziyordu.
D9 ile `uvicorn.access`/`uvicorn.error` logger'larina bir filtre eklendi
(`src/main.py:72`, kurulum `src/main.py:93`) → `secret=***`.
**Yine de bu dosyayi cikti/rapor/commit'e DOKME.**

## `logs/trades.jsonl` (D21)

Append-only, **satir basina TEK JSON**; `event` = `entry` / `exit` /
`postmortem` / `intent` / `ai_verdict`.
`trades.log`'tan **AYRIDIR**: o insan-okur bir denetim izidir, bu bir makine
sozlesmesidir. Secret ICERMEZ.

```bash
jq -c 'select(.event=="exit" and (.verdict|index("noise_stop")))' logs/trades.jsonl | tail -5
jq -r 'select(.trade_id==152)' logs/trades.jsonl
```

Yazici: `src/strategies/scalper/forensics_log.py:147` (`append_soon` — disk
yazimi olay dongusunun DISINDA), yol `src/strategies/scalper/forensics_log.py:66`.

## Aranacak kaliplar

| Belirti | Kalip |
|---|---|
| Ban | `HTTP 418`, `banned`, `devre kesici` |
| Acil kapanis | `piyasa tarafindan gecilmis (-2021)`, `ACIL KAPANIS GERCEKLESTI` |
| Entry-halt | `entry halt state okunamadi`, `fail-closed` |
| Golge modu | `GOLGE MODU ACIK — emir gonderilmez` |
| Kline kaynagi | `📡 Kline kaynagi:` |

## Tuzaklar

- **Bir sonucu log/rapor YOLU olmadan "kanit" sayma** (CLAUDE.md kural 6).
- `logs/` **gitignore'dadir** — backtest JSON raporlari commit'lenmez.
- Container ve host **farkli saat dilimlerinde** damga basabilir
  ([[40-isletme/halka-yonetimi]]).

ILGILI: [[50-veri/veritabani-semasi]] · [[10-mimari/gozlem-katmanlari]] · [[40-isletme/sorun-giderme]] · [[20-kararlar/D9-webhook-sertlestirme]]
