---
tags: [isletme, arizalar, sorun-giderme]
guncelleme: 2026-08-24
kaynak: docs/RUNBOOK.md "Arizalar" (satir 903-950) + "REST agirlik butcesi" (satir 854)
---
# Sorun giderme

## Karar agaci (once buraya bak)

```
Bot islem acmiyor
├─ /scalper/status → entries_blocked_by ?
│   ├─ entry_halt      → guvenlik kilidi: nedeni anla, dosyayi cleared yap, RESTART
│   ├─ kill_switch     → gunluk zarar kesici: UTC gun donene kadar bekle
│   ├─ risk_event      → POST /risk-event action=resume  (restart GEREKMEZ)
│   ├─ exchange_readiness → borsa erisimi yok
│   └─ rest_weight     → agirlik geri cekilmesi (varsayilan KAPALI olmali)
├─ scan_status = degraded:market_data → kline host'u / ban
├─ regimes = UNKNOWN → yetersiz mum (200 bar) ya da bayat kline
└─ cooldowns dolu / kapasite dolu → normal
```

## Sik arizalar

### Binance 418 / ban
`logs/bot.log`'da `HTTP 418|banned|devre kesici`. **Ban aktifken restart
YASAK** (suresini uzatir). Kok nedenler: rate limiter kilidi (duzeltildi),
dashboard force-fresh acligi (duzeltildi), agirlik basligi testnet'te tutarsiz.
Bekle; ban bitince `wait_for_binance` loglarini izle.

### `exit_reason=TRAIL_MARKET` / `BE_MARKET`
**ARIZA DEGIL.** Koruyucu stop borsaya gonderildi, `-2021` alindi ve
`position_manager._emergency_close` pozisyonu MARKET ile kapatti. Bot
kendiliginden piyasa emri **gondermez**. Sayilari **artiyorsa** stop karari
piyasa hizinin gerisindedir → parametre degisikligi CLAUDE.md yasak #1'e
tabidir. Telemetri: `/scalper/status.trailing_skips`.
⚠️ **"eski SL korunuyor" satiri YALNIZ pozisyon gercekten acikken yazilir** —
ikisini bir arada goruyorsan bu bir **regresyondur**.

### "Kapi bayat / `gate_effective=false`" ama kapi saglam
`/scalper/status.market_gate.stale_reason`:
- `"entries_blocked"` → tarama zaten durmus; **kapiyi kurcalama**, once
  `entries_blocked_by`'i coz.
- `"leader_stale"` → lider verisi gercekten gelmiyor.

### Degraded ama hata yok + tarama bayat
Belirti imzasi: hata yok + safety taze + **scan bayat**. Kok: dashboard acikken
`/api/status` force-fresh cagrisi rate-limiter'i doyuruyordu (**duzeltildi**).
Ayni imzayi gorursen **once panoyu kapat**.

### "Pozisyon korumasiz" / `-4120`
Kosullu emirler `/fapi/v1/algoOrder`'da; **`openOrders` onlari gostermez**,
**`allOpenOrders` iptal etmez**.

### Yanlis servis restart'i
`systemctl restart live-bot` trading botuna **dokunmaz** (futbol botu).
Daima `supervisorctl` (ve `scripts/restart_safe.sh`).

### Entry-halt acma
`state/scalper_entry_halt.json` → nedeni anla → `.cleared-<tarih>` diye yeniden
adlandir → **restart**. Recover sonrasi `exit_reason=UNKNOWN` kayitlari
guvenilmezdir; PnL'i `binance_income_net` ile dogrula.
⚠️ **KARISTIRMA:** `state/risk_event_halt.json` AYRI dosyadir —
restart GEREKMEZ.

## REST agirligi (D22)

Binance IP agirlik siniri **2400/dk**, sayac **IP GENELIDIR** (ayni cikis
IP'sindeki baska surecler de tuketir).

```bash
curl -s localhost:9091/scalper/status | python3 -c 'import json,sys; print(json.load(sys.stdin)["rest_weight"])'
```

- **`max_1m` DAKIKA DILIMLIDIR** (icinde bulunulan takvim dakikasinin tepesi).
- Tekil yuksek okumalar = **gurultu** (testnet basligi edge-bazli, tutarsiz).
- **Ayni dakika diliminde `max_1m` > 3000 tekrar tekrar** → gercek risk:
  (1) agirlik uyarisindaki endpoint dokumu, (2) ayni IP'de baska surec,
  (3) pano maliyeti **degildir** (5 sn sunucu onbellegi).

**Geri cekilmeyi ACMAK:** birkac gun dagilimi topla → `soft`u gozlenen
**medyanin belirgin USTUNE**, `hard`i 2400'un hemen altina koy.
**Medyanin altindaki esik = kalici durma** (testnet medyani 2373 olculdu).

ILGILI: [[10-mimari/guvenlik-kilitleri]] · [[40-isletme/gunluk-kontrol]] · [[50-veri/metrikler]] · [[20-kararlar/D22-acil-kapanis-kaydi]]
