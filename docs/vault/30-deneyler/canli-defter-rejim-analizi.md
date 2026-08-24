---
tags: [deneyler, canli-defter, rejim]
guncelleme: 2026-08-24
kaynak: docs/EXPERIMENTS.md "canli defter rejim analizi" (satir 97), scripts/ledger_report.py
---
# Canli defter rejim analizi (7-21 Agu, testnet)

BTC gunluk rejim tanimi: **UP > +%1.5**, **DOWN < −%1.5**, arasi FLAT.

| Rejim | Gun | LONG | SHORT |
|---|---|---|---|
| UP | 4 | 76 islem, %89, **+652** | 13 islem, %23, −88 |
| FLAT | 11 | 40 islem, %52, +90 | 42 islem, %50, +179 |
| DOWN | **0** | — | — |

## ⚠️ Uc baglayici okuma

1. **Kazancin %68'i 4 yukselis gununden geliyor.**
2. **7-20 Agu, son 240 gunun EN IYI 14 gunuydu** (+%13.5). Yani bu pencere
   temsili degildir.
3. **DOWN gunu HIC olculmedi.** "Bot kazaniyor" iddiasi bu yuzden
   **rejime bolunmeden kabul edilemez**.

FLAT SHORT'un +179'unun buyuk kismi **tek gunden** geliyor — konsantrasyon
uyarisi ([[30-deneyler/D24-olcumleri]]).

## Otomatiklestirildi

Bu kalip artik elle SQL gerektirmez:
```bash
python3 scripts/ledger_report.py --since "2026-08-14 00:00" --format md
```
Rapor rejim/yon/cikis-nedeni/sembol/gun kirilimlarini ve mainnet soak
kontrol listesini PASS/FAIL yazdirir — **hukum vermez**.

ILGILI: [[10-mimari/defter-ve-muhasebe]] · [[40-isletme/gunluk-kontrol]] · [[20-kararlar/mainnet-plani]] · [[20-kararlar/P3-simulator-olcegi]]
