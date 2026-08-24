---
tags: [karar, golge, tradingview, olay]
guncelleme: 2026-08-24
kaynak: docs/DECISIONS.md D19 (satir 1742), docs/INTEGRATIONS.md §7, src/services/tv_events.py
---
# D19 — TV olay kanali: CIKIS + YAPI/DONUS olaylari (`kind=exit|choch|trend|tp1`) · GOLGE

**Karar.** TradingView'den bota bugune kadar YALNIZ "gir" oyu geliyordu.
Bu kanal gostergelerin **CIKIS ve YAPI** bilgisini de sokar: LuxAlgo S&O
`Exit Signal`, `Trend Catcher/Tracer`, PAC `S-CHOCH`, AlgoPro `TP1 Hit`.

- **Yonlendirme GOVDEDEN yapilir** (URL degismez) — kullanici yeni alarmlari
  MEVCUT alarmlari klonlayarak kuruyor.
- **`kind` yoksa `entry`** → mevcut 49 alarmin davranisi BIREBIR korunur.
  Taninmayan `kind` **422** ile reddedilir, "entry"ye DUSURULMEZ.
- Defter motorun IKI yerinde okunur: `_evaluate_symbol`'deki yapi kapisi ve
  `_safety_tick`'teki BE/kapanis tetikleyicisi.
- Uc kademe: `SCALPER_TV_EVENTS_MODE=off|shadow|active`, **varsayilan
  `shadow` = motor davranisi degismez**.

**Durum.** GOLGE (aktif DEGIL). Kod canlida (2026-08-23 13:17 UTC).
Alarm klonlama kullaniciya birakildi (`docs/INTEGRATIONS.md` §7.2 sablonlari).
**Geri alma.** `SCALPER_TV_EVENTS_MODE=off` + korumali restart.

> ⚠️ **Bu karar D19a ile GUNCELLENDI.** MIXED kurali, `be`nin zararda
> uygulanmamasi, olay kaynaklarinin giris oyu verememesi ve tuketim
> imleclerinin kaliciligi D19a'da degisti — **celiskide D19a baglayicidir**.

**Nerede.** `src/services/tv_events.py:159`, ingest
`src/services/tv_events.py:340`, kapi durumu
`src/services/tv_events.py:570`, bekleyen cikis
`src/services/tv_events.py:586`; motor
`src/strategies/scalper/engine.py:3331`.

ILGILI: [[20-kararlar/D19a-tv-olay-duzeltmeleri]] · [[10-mimari/tv-sinyal-yolu]] · [[20-kararlar/D18-yapi-kapisi]]
