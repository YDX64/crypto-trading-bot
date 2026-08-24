---
tags: [mimari, scalper, motor, strateji-c]
guncelleme: 2026-08-24
kaynak: src/strategies/scalper/engine.py, src/strategies/scalper/setups.py, src/strategies/scalper/regime.py, docs/ARCHITECTURE.md
---

# Motor — ScalperEngine ve strateji C

## NE

Otonom tarama/giris/cikis motoru. Tek surecte 3+ arka plan task'i kosar
(scan, safety, exchange-readiness) ve **tek giris karar noktasi** vardir:
`_evaluate_symbol`. Hem kendi taramasi hem TradingView dis sinyali AYNI
kapilardan gecer — bu bilincli bir tasarimdir, kapi eklerken bozma.

## NEREDE

| Ne | Yer |
|---|---|
| Motor sinifi | `src/strategies/scalper/engine.py:153` |
| Baslatma / durdurma | `src/strategies/scalper/engine.py:424` · `src/strategies/scalper/engine.py:472` |
| Tarama dongusu | `src/strategies/scalper/engine.py:1201` |
| Tarama turu | `src/strategies/scalper/engine.py:1605` |
| **Tek giris karar noktasi** | `src/strategies/scalper/engine.py:1820` |
| Safety dongusu / turu | `src/strategies/scalper/engine.py:1247` · `src/strategies/scalper/engine.py:1277` |
| TV dis sinyali girisi | `src/strategies/scalper/engine.py:3661` |
| Durum anlik goruntusu | `src/strategies/scalper/engine.py:4863` |
| Strateji C | `src/strategies/scalper/setups.py:431` (`evaluate` → `src/strategies/scalper/setups.py:459`) |
| Aktif strateji secimi | `src/strategies/scalper/setups.py:931` |
| Rejim tespiti | `src/strategies/scalper/regime.py:19` |
| Lider piyasa kapisi (saf kural) | `src/strategies/scalper/market_gate.py:185` |
| Yapi (CHoCH/BOS) durumu | `src/strategies/scalper/structure.py:358` |

## NASIL CALISIR

### Strateji C — "Saf Uc Avcisi"

RSI ucu (`SCALPER_C_RSI_LONG_MAX=25` / `_SHORT_MIN=75`) + Bollinger tasmasi +
**RSI diverjansi sarti** (`SCALPER_C_REQUIRE_DIVERGENCE=true`, bkz.
[[20-kararlar/D6-diverjans-sarti]]) → **ters yonde** giris,
`risk_multiplier=0.5`. Yani C bir **kontr-trend** stratejisidir; bu, sonraki
her kapi kararini etkiler ([[20-kararlar/D18-yapi-kapisi]] tam bu yuzden
reddedildi).

### Zaman dilimi rolleri

`SCALPER_TF_ENTRY` (giris mumu) → `SCALPER_TF_CONTEXT` (baglam/equilibrium) →
`SCALPER_TF_REGIME` (rejim). Kod varsayilani `5m/15m/4h`
(`src/core/config.py:201` civari blok); **canli sunucu `.env`'i rejimi `15m`
tutar** — `4h` varyanti backtest'te bogayi yok ettigi icin reddedildi
([[30-deneyler/E2-E3-varyantlari]]).

### Rejim

`detect_regime` saf fonksiyondur (`src/strategies/scalper/regime.py:19`):
- `< 200` mum → **UNKNOWN** → hicbir strateji islem acmaz.
- `EMA50 > EMA200` ve son kapanis `> EMA50` → **UP**
- `EMA50 < EMA200` ve son kapanis `< EMA50` → **DOWN**
- aksi → **RANGE**

### Kapi sirasi (`_evaluate_symbol` icinde, sinyal URETILDIKTEN SONRA)

| # | Kapi | Yer | Varsayilan |
|---|---|---|---|
| 1 | Strateji-ici rejim daraltmasi (`SCALPER_C_ALLOWED_REGIMES`) | `src/strategies/scalper/setups.py:459` | `UP,DOWN,RANGE` |
| 2 | **Rejim kapisi** — DOWN'da LONG, UP'ta SHORT yasak | `src/strategies/scalper/engine.py:1932` | ACIK ([[20-kararlar/D5-rejim-kapisi]]) |
| 3 | **Lider piyasa kapisi** — BTC gun-ici sapmasi | `src/strategies/scalper/engine.py:1961` | kod KAPALI / canli ACIK ([[20-kararlar/D15-lider-kapisi]]) |
| 4 | Yapi kapisi (CHoCH/BOS) | `src/strategies/scalper/structure.py:373` | KAPALI ([[20-kararlar/D18-yapi-kapisi]]) |
| 5 | TV yapi kapisi (D19 olay defteri) | `src/strategies/scalper/engine.py:2021` | GOLGE ([[20-kararlar/D19-tv-olay-kanali]]) |
| 6 | Kapasite / cooldown / allowlist / entry_lock | `src/strategies/scalper/engine.py:2040` bloku | `SCALPER_MAX_POSITIONS=3` |
| 7 | Stop politikasi + `min_rr` + boyutlama | [[10-mimari/emir-yurutme]] | — |
| 8 | AI kapisi (D23) — **karar yolunun DISINDA**, gozlem | `src/strategies/scalper/engine.py:2370` | `off` ([[20-kararlar/D23-ai-kapisi]]) |

Girislerin toptan durdurulmasi tek yerdedir: `_entries_ready`
(`src/strategies/scalper/engine.py:1009`); kim durdurdugunu
`entries_blocked_by` (`src/strategies/scalper/engine.py:1030`) soyler.

## TUZAKLAR

- **`gate_on` formulu:** rejim kapisi `scalper_regime_filter AND (ic sinyal OR
  scalper_tv_regime_filter)` — yani TV sinyalleri ayri bir bayrakla muaf
  tutulabilir, ama varsayilan `True`'dur.
- **Lider kapisi fail-OPEN'dir.** Lider verisi gelmezse kapi UYGULANMAZ.
  `enabled=true` gormek kapinin koruduguna KANIT DEGILDIR; `gate_effective`
  alanina bak (`src/strategies/scalper/engine.py:4541`).
- **Yapi kapisi fail-open ve varsayilan kapali**; hesap hatasi taramayi
  dusurmez. Bir sinyal filtresidir, guvenlik kilidi degildir.
- **UNKNOWN rejim = hic islem yok.** "Bot islem acmiyor" sikayetinin en sik
  nedeni yetersiz mum (200 bar) ya da bayat kline'dir.
- **Kesilen tarama turu "basarili" degildir**: `scan_status` alani
  `degraded:market_data` ya da `degraded:rest_weight` olur; `last_scan_at`
  tazelenmez.
- **Harness paritesi:** rejim kapisi, kapasite kapisi, lider kapisi ve yapi
  kapisi backtest'te de AYNI saf fonksiyonlarla uygulanir. Motorda bir kapiyi
  degistiren `src/strategies/scalper/backtest.py:1152` (`simulate_symbol`)
  tarafini da degistirmek ZORUNDADIR ([[20-kararlar/P1-harness-parite]]).

## ILGILI

[[10-mimari/tv-sinyal-yolu]] · [[10-mimari/emir-yurutme]] ·
[[10-mimari/cikis-yonetimi]] · [[10-mimari/guvenlik-kilitleri]] ·
[[20-kararlar/D1-yalniz-strateji-c]] · [[30-deneyler/00-deney-indeksi]]
