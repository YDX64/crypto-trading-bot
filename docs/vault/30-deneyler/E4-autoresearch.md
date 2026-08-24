---
tags: [deneyler, E4, autoresearch]
guncelleme: 2026-08-24
kaynak: docs/EXPERIMENTS.md Autoresearch bolumleri (satir 173, 253), docs/AUTORESEARCH.md
---
# E4 — Autoresearch turlari (otomatik parametre aramasi)

`scripts/autoresearch.py` her varyanti uc sabit pencerede **SIRALI** kosar ve
[[20-kararlar/P2-karar-kurali]]'ni otomatik uygular.

## Tur-1 / Tur-2 sonuclari (secme)

| Varyant | Hipotez | AYI PF/PnL | YATAY | BOGA | Hukum |
|---|---|---|---|---|---|
| E4a | RSI 35/65 (gevsek) | 0.79 / −5960 | 1.13 | 1.85 | ❌ **aktivite ≠ kar** |
| E4b | Chandelier 3.0 | 1.05-1.07 / +823…+1134 | 1.30-1.35 | 2.20-2.46 | **ADAY** (D11) |
| E4c | Chandelier 4.0 | 1.04 / +640 | 1.34 | 2.33 | ❌ rejim-bagimli |
| E4d/E4e | TP2 %20 / %30 | 1.06 | 1.33 | 2.18-2.19 | ❌ etkisiz (±40) |
| **E4f** | **TP1 %8** | **1.12 / +1415 (DD 2604)** | **1.38 / +2604** | **3.71 / +4598** | **ADAY (en guclu)** (D12) |
| E4g | TP1 %12 | 1.07 / +1176 (DD **4106**) | 1.33 | 2.51 | ADAY ama DD tabanin ustu |
| E5c | Lev tavani 12 | 1.26 | 1.11 | 1.50 (**52 islem**) | ❌ asiri filtreleme |

## Kural detaylari

- Bir varyant her pencerede **≥60 islem** uretmelidir; altinda "asiri
  filtreleme" ile reddedilir (E5c boyle dustu).
- Hukum iki kolludur: **AYI PF ≥ 1.1** VEYA **AYI+YATAY PnL birlikte ↑**,
  VE **BOGA kaybi ≤ %20**.
- Sonuclar `docs/EXPERIMENTS.md`'ye **otomatik** eklenir; ham loglar
  `logs/autoresearch/<tarih>/`, ozet `summary.json`.

## Sinirlar (bilincli)

Script sunucuya **ASLA dokunmaz** (ssh/scp yok), `.env` **YAZMAZ**, **deploy
ETMEZ**, motor koduna **DOKUNMAZ**, yalnizca commit'lenmis koda karsi
degerlendirir. Uretilen sey **ONERI**dir; canliya gecis insan karari +
testnet soak ister.

ILGILI: [[20-kararlar/D11-chandelier-3-0]] · [[20-kararlar/D12-tp1-8]] · [[20-kararlar/D13-kaldirac-tavani]] · [[30-deneyler/00-metodoloji-uyarisi]]
