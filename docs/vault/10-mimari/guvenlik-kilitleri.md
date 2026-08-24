---
tags: [mimari, guvenlik, entry-halt, kill-switch, 418]
guncelleme: 2026-08-24
kaynak: src/strategies/scalper/engine.py, src/trading/position_manager.py, src/strategies/scalper/data.py, src/core/config.py, docs/RUNBOOK.md
---

# Guvenlik kilitleri — entry-halt, risk-event, kill-switch, 418

## NE

Bes bagimsiz kilit vardir. **Hepsi fail-CLOSED'dir**: suphede giris DURDURULUR.
Bunlari "gecici olarak kapatmak" en pahali hatadir.

| Kilit | Dosya / kaynak | Kim kurar | Nasil acilir |
|---|---|---|---|
| **Entry-halt** | `state/scalper_entry_halt.json` | `UnprotectedPositionError` (koruma kurulamadi) | nedeni anla → dosyayi `.cleared-<tarih>` diye yeniden adlandir → **restart** |
| **Risk-event halt** | `state/risk_event_halt.json` | `POST /risk-event` (haber/olay botu, operator) | `action=resume` **ya da** dosyayi sil — restart GEREKMEZ |
| **Kill-switch (gunluk zarar)** | RAM + gunluk income | `SCALPER_DAILY_LOSS_LIMIT_PCT` asilinca | UTC gun donunce kendiliginden |
| **Binance ban (418/-1003)** | `MarketDataGuard` + istemci kesicisi | Borsa | beklenir; **ban aktifken restart YASAK** |
| **Exchange readiness** | motor ici | borsa erisilemiyor | erisim gelince |

## NEREDE

| Ne | Yer |
|---|---|
| Entry-halt yukleme / yazma | `src/strategies/scalper/engine.py:530` · `src/strategies/scalper/engine.py:565` |
| Risk-event anlik goruntusu | `src/strategies/scalper/engine.py:622` |
| `halt` / `resume` / `flatten` | `src/strategies/scalper/engine.py:779` · `:809` · `src/strategies/scalper/engine.py:837` |
| **Tek giris kapisi** | `src/strategies/scalper/engine.py:1009` |
| Girisleri kim durdurdu | `src/strategies/scalper/engine.py:1030` |
| Kill-switch guncelleme | `src/strategies/scalper/engine.py:4644` |
| Korumasiz pozisyon istisnasi | `src/trading/position_manager.py:34` |
| Acil kapatma | `src/trading/position_manager.py:476` |
| Market-data ban tipleri | `src/strategies/scalper/data.py:197` · `:205` · `:234` |
| Host basina koruma | `src/strategies/scalper/data.py:372` |
| Mainnet fail-fast dogrulamasi | `src/core/config.py:1275` |
| HTTP ucu | `src/main.py:2177` |

## NASIL CALISIR

### `_entries_ready` — tek ortak kapi

Scanner'in C stratejisi ve TV `external_signal` **AYNI** kapidan gecer
(`src/strategies/scalper/engine.py:1009`). Yeni bir kilit eklemek isteyen
oraya ekler; iki ayri yere eklemek D10'un ihlalidir.

### Entry-halt ile risk-event halt AYRI dosyalardir

Bu bilinclidir ([[20-kararlar/D10-risk-olayi-kanali]]):
- `scalper_entry_halt_enabled` bayragi **yalniz** `UnprotectedPositionError`
  latch'ini gater ve canli sunucuda `false`'tur.
- Risk-event halt bu bayraktan **TAMAMEN BAGIMSIZ**, her zaman uygulanir.
- Bozuk/parse edilemeyen dosya = **HALT AKTIF** (fail-closed).

### `flatten`

Reaper'in kullandigi AYNI reduce-only MARKET cagrisini yeniden kullanir; halt
kapatma turundan **ONCE** kurulur ve tur sonrasi **ikinci** bir tarama escanli
dolan pozisyonu yakalar. Kapanis borsada dogrulanmadan `_handle_closed`
CAGRILMAZ — aksi halde SL/TP iptal edilip pozisyon korumasiz kalabilirdi.

### Mainnet fail-fast (`src/core/config.py:1275`)

Testnet DEGILKEN startup'ta reddedilenler:
- `allow_mainnet` acikca verilmemis
- `SCALPER_ENTRY_HALT_ENABLED=false`
- (golge modu kapaliysa) bos `RISK_EVENT_SECRET` / `TV_WEBHOOK_SECRET` /
  `SCALPER_SYMBOL_ALLOWLIST`
- market-data host'u testnet iken islem host'u mainnet
- **AlgoPro takipcisi aktifken testnet-olmayan `BINANCE_BASE_URL`**
  ([[20-kararlar/D20-takipci-halkasi]])

### Hata kapsami — 418 vs 429 vs 404

| Yanit | Kapsam | Kesici | Deploy kilidi |
|---|---|---|---|
| 418 / `-1003` / "banned until" | host | **hard ban** 180 sn | **kapanir** |
| 429 tek basina | host | soft ~30 sn | kapanmaz |
| 401/403/451 | host | soft ~60 sn | kapanmaz |
| 400/404 (`-1121`) | **sembol** | yok | kapanmaz |

## TUZAKLAR

- **Ban aktifken restart YASAK** — yasagi uzatir (CLAUDE.md kural 3).
- **`entry_halted=true` gormek panik degil, teshis konusudur**; once
  `entries_blocked_by` alanina bak.
- **Freshness alanlari ban sirasinda BILINCLI tazelenir**: "unhealthy"
  gostermek watchdog restart'ini davet ederdi — bu 2026-08-14 felaket yoludur.
- **`RISK_EVENT_SECRET` bossa `/risk-event` 503 doner** (kanal kapali).
- **Golge modu bu kilitleri BYPASS etmez** — yalniz emir gondermeyi durdurur.
- Kill-switch **Binance income tabanlidir**, harness'ta modellenmez.

## ILGILI

[[10-mimari/motor-scalper]] · [[40-isletme/sorun-giderme]] ·
[[20-kararlar/D10-risk-olayi-kanali]] · [[20-kararlar/D14-golge-modu]] ·
[[20-kararlar/mainnet-plani]] · [[90-ai-icin/dogrulama-receteleri]]
