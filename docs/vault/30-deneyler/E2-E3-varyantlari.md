---
tags: [deneyler, E2, E3, varyant]
guncelleme: 2026-08-24
kaynak: docs/EXPERIMENTS.md "E2/E3 varyantlari" (satir 83)
---
# E2 / E3 — tek-degisken varyantlar (3 pencere)

| Varyant | AYI PF/PnL | BOGA PF/PnL | YATAY PF/PnL | Karar |
|---|---|---|---|---|
| **E2a** `C_REQUIRE_DIVERGENCE=true` | **1.06 / +886** | **2.18 / +3831** | **1.33 / +2745** | ✅ **CANLI (D6)** |
| E2b `C_REQUIRE_FLOW_CONFIRM=true` | 1.19 / +2933 | 1.50 / +2610 | 0.81 / −2996 | aday → E2ab'de red |
| E2ab divergence+flow_confirm | 3.35 / +1498 (**31 islem**) | 0.85 / −243 (22) | 0.78 / −692 (34) | ❌ asiri filtreleme |
| E2c `C_REQUIRE_REVERSAL_ZONE=true` | 0.68 / −5596 | 2.03 / +2636 | 0.75 / −2916 | ❌ |
| E2d RSI 25/75 | 0.96 / −1493 | 1.34 / +2613 | 0.85 / −4478 | ❌ |
| E3a `FIXED_STOP_ROI_PCT=30` | 0.91 / −6108 | 1.28 / +4092 | 0.91 / −3894 | ❌ SL 120→**224** |
| E3b `TF_REGIME=4h` | 1.02 / +1227 | 1.00 / −63 | 0.79 / −9090 | ❌ |
| E3c `C_ALLOWED_REGIMES=DOWN,UP` | 1.03 / +1263 | 1.24 / +2567 | 0.79 / −7170 | ❌ |
| E3d E3a+E3b | 0.93 / −4148 | 1.04 / +702 | 0.81 / −10346 | ❌ |

## Cikarilan dersler

- **Uc pencerede birden kazanan tek varyant E2a'ydi** → D6.
  E2a detay: islem 216/96/150, maxDD 3574/735/3181.
- **Filtreleri birlestirmek toplamsal DEGILDIR** — E2ab iki iyi filtreyi
  birlestirdi ve orneklem 31 isleme dustu.
- **Rejim TF'sini yavaslatmak (4h) bogayi yok eder**; hizlandirmak (5m, E6a)
  da ise yaramadi.

Loglar: `E<id>_<pencere>.log` (24 dosya), ozet `regime_experiments.md`.

ILGILI: [[20-kararlar/D6-diverjans-sarti]] · [[20-kararlar/reddedilen-kararlar]] · [[30-deneyler/rejim-referanslari]]
