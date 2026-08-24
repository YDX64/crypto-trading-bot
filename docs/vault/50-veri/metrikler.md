---
tags: [veri, metrik, api, uclar]
guncelleme: 2026-08-24
kaynak: src/main.py, src/strategies/scalper/engine.py snapshot (satir 4863)
---
# Metrikler — hangi sayi nereden gelir

## HTTP uclari (hepsi `src/main.py`)

| Uc | Satir | Ne doner |
|---|---|---|
| `GET /health` | `src/main.py:538` | saglik ozeti (degraded/healthy) |
| `GET /api/status` | `src/main.py:721` | pano beslemesi (bakiye, BTC fiyati, pozisyon sayisi, `ai_gate`) |
| `GET /positions` | `src/main.py:861` | **orchestrator** aktif pozisyonlari → `{"count":…, "positions":[…]}` |
| `GET /config` | `src/main.py:903` | ayar ozeti |
| `GET /scalper/status` | `src/main.py:2754` | motor anlik goruntusu (asagida) |
| `GET /scalper/stats` | `src/main.py:2805` | defter istatistikleri |
| `GET /scalper/trades` | `src/main.py:2879` | islem listesi (SHADOW haric) |
| `GET /scalper/trades/{id}/forensics` | `src/main.py:2984` | tek islem adli kaydi |
| `GET /scalper/forensics/recent` | `src/main.py:2997` | son adli kayitlar |
| `GET /scalper/forensics/summary` | `src/main.py:3003` | etiket × sonuc + `intents` |
| `GET /follower/status` | `src/main.py:2496` | takipci motoru |
| `GET /follower/forwarder` | `src/main.py:2524` | kopru sayaclari |
| `POST /tv-signal` | `src/main.py:1814` | webhook (secret) |
| `POST /risk-event` | `src/main.py:2177` | halt/resume/flatten/status |
| `POST /tv-events/reset` | `src/main.py:2125` | olay defterini sifirla |
| `POST /follower/event` | `src/main.py:2430` | takipci kanali (ayri secret) |

## ⚠️ ALAN ADI TUZAGI: `tracked` vs `positions`

`/scalper/status` acik scalper pozisyonlarini **`tracked`** anahtarinda
dondurur (`src/strategies/scalper/engine.py:4999`) — **`positions` DEGIL**.
`positions` anahtari yalniz `/positions` (orchestrator) ve `/follower/status`
yanitlarinda vardir.

```bash
# DOGRU
curl -s localhost:9091/scalper/status | jq '.tracked | length'
# YANLIS (her zaman null doner, "pozisyon yok" sanirsin)
curl -s localhost:9091/scalper/status | jq '.positions | length'
```

## `/scalper/status` onemli alanlar

| Alan | Anlam |
|---|---|
| `as_of` | govdenin **KURULDUGU** an (yanit 5 sn onbellekli) |
| `entries_blocked_by` | girisleri **kim** durdurdu (`entry_halt`/`kill_switch`/`risk_event`/`exchange_readiness`/`rest_weight`) |
| `scan_status` | `ok` · `degraded:market_data` · `degraded:rest_weight` |
| `kline_source` | `trading_host` \| `separate` |
| `market_data_base_url` / `trading_base_url` / `market_data_guard` | D17 teshisi |
| `market_gate.gate_effective` | kapi **GERCEKTEN** koruyor mu (≠ `enabled`) |
| `market_gate.stale_reason` | `entries_blocked` \| `leader_stale` |
| `market_gate.day_open_source` | `intraday_open` \| gunluk kapanis vekili |
| `rest_weight` | `{last, max_1m, soft_backoffs, hard_backoffs, enabled, backoff}` — **`max_1m` DAKIKA DILIMLI** |
| `trailing_skips` | `{price_space_skips, protective_gate_skips, market_exits}` |
| `structure` | sembol → yapi durumu (**telemetri**, kapi kapali olsa da) |
| `tv_events` | mod, `allowlist_ok`, `gate_enabled`, `window_open`, sayaclar |
| `ai_gate` | D23 golge: mod, kapsama, gecikme, butce, red orani |
| `forensics_queue` | yazici kuyrugu + post-mortem turu |
| `shadow_mode` · `entry_halted` · `kill_switch_active` · `risk_event` | kilit durumlari |
| `sizing` · `virtual_capital_*` | boyutlama ve sanal defter |
| `symbol_reservations` | hangi motor hangi sembolu tutuyor |
| `cooldowns` · `pending_entries` · `entry_rejects` · `universe` · `regimes` | tarama durumu |

## Rapor betikleri

| Betik | Ne |
|---|---|
| `scripts/ledger_report.py` | canli defter, rejim/yon/cikis kirilimi, soak checklist, `--forensics`, `--db` |
| `scripts/autoresearch.py` | uc pencerede varyant taramasi (yalniz oneri) |
| `scripts/decompose_gate_runs.py` | kapi kosularini **engelleme vs yeniden tahsis** olarak ayirir |
| `python3 -m src.strategies.scalper.multitest` | Benjamini-Hochberg q-degeri |
| `python3 -m src.strategies.scalper.backtest --permutations N` | Monte-Carlo p-degeri |

## Tuzaklar

- **Panodan `force_fresh` ISTENMEZ** — 2026-08-18 acligi.
- **`max_1m` sureç omru tepesi DEGILDIR**, takvim dakikasinin tepesidir.
- `market_gate.enabled=true` kapinin **koruduguna kanit degildir**.
- Kesilen tarama turu `last_scan_at`'i **tazelemez** ama freshness alanlarini
  **bilincli tazeler** (watchdog restart'ini davet etmemek icin).

ILGILI: [[40-isletme/gunluk-kontrol]] · [[40-isletme/sorun-giderme]] · [[40-isletme/panel-erisimi]] · [[90-ai-icin/dogrulama-receteleri]]
