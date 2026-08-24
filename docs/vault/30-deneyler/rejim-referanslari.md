---
tags: [deneyler, taban, rejim]
guncelleme: 2026-08-24
kaynak: docs/EXPERIMENTS.md "2026-08-21 — rejim referanslari" (satir 73)
---
# Rejim referanslari (taban, divergence=false iken)

| Pencere | Islem | WR | PnL | PF | maxDD | LONG | SHORT | SL n/ort | TRAIL n/ort |
|---|---|---|---|---|---|---|---|---|---|
| AYI **kapi acik** | 814 | 84.6 | −2042 | 0.97 | 11857 | 259/−3732/0.83 | 555/+1689/1.04 | 120/−514 | 687/+88 |
| AYI **kapi KAPALI** | 1171 | 80.8 | **−36506** | 0.68 | 41691 | 636/−37814/0.49 | 535/+1308/1.03 | 220/−514 | 944/+82 |
| YATAY | 449 | 84.4 | −2289 | 0.93 | 8254 | 257/+3047/1.19 | 192/−5336/0.71 | 67/−514 | 375/+86 |
| BOGA | 191 | 88.0 | +2798 | 1.24 | 3610 | 101/+3182/1.69 | 90/−385/0.95 | 23/−514 | 168/+87 |

## Okuma (uc cumle)

1. **Basabas kazanma orani ≈ %85.4** — SL ort. −514, TRAIL ort. +88.
2. **Rejim kapisi ayida ~34.5k kurtariyor** ([[20-kararlar/D5-rejim-kapisi]]).
3. **Kayip daima ters-trend tarafta**: ayida LONG, yatayda SHORT.

## Neden onemli

Bu tablo tum sonraki varyantlarin **karsilastirma tabanidir**. Bir varyant
"iyi" demek icin bu satirlara gore fark uretmelidir
([[20-kararlar/P3-simulator-olcegi]]).

⚠️ Bu taban `SCALPER_C_REQUIRE_DIVERGENCE=false` iken olculdu; D6 sonrasi
"yeni taban" AYI 1.04 / DD 3683 · YATAY 1.29 · BOGA 2.43'tur
(kapasite-kapili harness, 2026-08-22).

ILGILI: [[30-deneyler/E2-E3-varyantlari]] · [[30-deneyler/00-metodoloji-uyarisi]] · [[10-mimari/cikis-yonetimi]]
