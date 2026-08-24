---
tags: [deneyler, E7, market-gate, kapi]
guncelleme: 2026-08-24
kaynak: docs/EXPERIMENTS.md "Lider piyasa kapisi (E7)" (satir 813)
---
# E7 — Lider piyasa kapisi (D15'in kaniti)

## P2 hukmu

| Varyant | AYI PF | AYI+YATAY birlikte ↑ | BOGA kaybi | Hukum |
|---|---|---|---|---|
| V1 (gun-ici %1.0) | 1.33 ✓ | ✓ (+2415 / +201) | −%4.5 ✓ | GECTI |
| V1a (%0.7) | 1.52 ✓ | ✗ (YATAY −%63.5) | −%13.3 ✓ | red — YATAY'i yikiyor |
| V1b (%1.5) | 1.17 ✓ | ✓ | %0 ✓ | GECTI (muhafazakar) |
| **V1c (%1.3)** | **1.43 ✓** | **✓ (+3228 / +399)** | **−%2.7 ✓** | **GECTI — V1'i domine ediyor** |
| V2 (%15/3g uzama) | 1.24 ✓ | ✗ | %0 ✓ | kanit tek olaya dayaniyor |
| V2a (%10/3g) | 1.21 ✓ | ✗ | **−%24.6 ✗** | **KALDI** |
| V3 (ikisi) | 1.33 ✓ | ✓ | −%4.5 ✓ | = V1 |

→ Benimsenen: **gun-ici %1.3, uzama KAPALI** ([[20-kararlar/D15-lider-kapisi]]).

## Hangi kaybi kesiyor (AYI, V0 → V1)

- SL sayisi **29 → 17** (−%41); SL zarari −14907 → −8738.
- LONG 79/−956 → 61/−121 · SHORT 134/+1541 → 88/**+3120**.
- Rejim kirilimi: **RANGE gunleri −1029 → +425** (asil duzelme burada).

## ⚠️ Bu satirlar ATIF DEGILDIR

Onceki surum LONG satirina "dusen-bicak LONG'lar kesiliyor" yorumunu
iliistiriyordu. **Ayristirma bunu curuttu**: ΔPnL iki etkinin toplamidir —
**(a) engelleme** (vetolanan islemler) + **(b) yeniden tahsis** (bosalan
slota giren YENI islemler). LONG duzelmesinin cogu (b)'den geliyor.

Betik: `scripts/decompose_gate_runs.py` (rapor yollarini log dosyalarindan
turetir). Ortak islemlerin PnL'i iki kosuda **birebir ayni** (0 uyusmazlik) →
atif temiz. **Sonuc esige duyarlidir ve benimsenen esikte tersine doner.**

## Gun acilisi turetmesi (parite)

Iki tarafta da `src/strategies/scalper/market_gate.py:160`: once **gercek
acilis** (o gunun 00:00 UTC `15m` mumunun `open`'i — `1d` mumunun `open`'ina
birebir esit, olculdu: 76 gun, 0 uyusmazlik); gunun ilk 15 dakikasinda son
tamamlanmis gunluk kapanis vekiline duser. Hangisi kullanildi:
`/scalper/status` → `market_gate.day_open_source`.

ILGILI: [[20-kararlar/D15-lider-kapisi]] · [[30-deneyler/E8-sinyal-otopsisi]] · [[20-kararlar/P1-harness-parite]]
