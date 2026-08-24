---
tags: [karar, aktif, olcum, metodoloji]
guncelleme: 2026-08-24
kaynak: docs/DECISIONS.md D24 (satir 2643), docs/EXPERIMENTS.md D24 bolumu
---
# D24 — Olcum/kanit paketi (dort harici depodan ALINACAKLAR) · AKTIF · **YALNIZ OLCUM**

Dort harici deponun (investing-algorithm-framework · AI-Trader ·
jane-street-secret-ai-trading-skills · OpenTrade) dusmanca incelemesinden cikan
**yedi madde**. Hepsinin ortak ozelligi: **kar iddiasi YOK**, katki yalniz
kanit kalitesine. Dordu de "baglanabilir servis" olarak REDDEDILDI.

| # | Ne | Nerede |
|---|---|---|
| A1 | Monte-Carlo permutasyon + yon-farkindalikli p-degeri | `src/strategies/scalper/permutation.py:311`, `--permutations N` |
| A2 | Benjamini-Hochberg FDR duzeltmesi | `src/strategies/scalper/multitest.py:64` |
| A3 | **Bar-bazli** mark-to-market ozkaynak egrisi + gercek cokus | `src/strategies/scalper/backtest.py:1320` |
| A4 | Konsantrasyon (tek sembol / islem / gun kar payi) | `src/strategies/scalper/backtest.py:1390` |
| A5 | Maliyet stresi + giris gecikmesi | `--fee-stress`, `--entry-delay-candles`, `SCALPER_SLIPPAGE_RATE` |
| A6 | Karar kaydi sema alanlari (`horizon_end_at`/`invalid_if`/`confidence`/`model_version`) | `src/strategies/scalper/forensics.py:481` |
| A7 | Uc-asamali niyet kaydi (proposed → decided → executed) | `src/strategies/scalper/intent.py:195` |
| A8 | Metodoloji uyarisi: tekrarli holdout + "sakli pencere" kurali | `docs/EXPERIMENTS.md` bas kutusu |

## En carpici iki olcum
- **A3:** altin kosuda kapanis-bazli `max_drawdown` **0.00**, bar-bazli
  **11.46** (toplam karin **%42.8**'i). Bugunku metrik "sifir risk"
  raporluyordu; portfoy bar-icinde karin %43'u kadar su altindaydi.
- **A1 kelepce:** permute barlarin **%57.6**'si OHLC tutarsizligi tasiyordu;
  kelepcesiz null TP/trail aleyhine sistematik bozuktu ve gercek sonucu
  **oldugundan anlamli** gosteriyordu. Kelepce kozmetik degildir.

**Durum.** AKTIF — motor davranisi **DEGISMEDI**; altin backtest sayilari
degismedi (2 islem / `total_pnl` 26.77).
**Bilinen bosluk (durustluk).** A5 uc rejim penceresinde **kosulmadi**
(yalniz arac hazir); A6 alanlari **doldurulmuyor** → `with_expectation: 0`.
**Geri alma.** Bayraklar varsayilan kapali; kaldirmak gerekmez.

ILGILI: [[30-deneyler/D24-olcumleri]] · [[30-deneyler/00-metodoloji-uyarisi]] · [[30-deneyler/E10-permutasyon]] · [[10-mimari/gozlem-katmanlari]]
