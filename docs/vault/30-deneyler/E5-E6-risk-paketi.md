---
tags: [deneyler, E5, E6, risk, boyutlama]
guncelleme: 2026-08-24
kaynak: docs/EXPERIMENTS.md Autoresearch 2026-08-23 (satir 278), docs/superpowers/specs/2026-08-22-reversal-day-loss-design.md
---
# E5 / E6 — kaldirac ve risk paketi

## Sorun (kanit: canli defter 12–22 Agu)

22 Agustos donus gunu **−133** (4 SL × ≈−83). Kok:
1. **Odeme asimetrisi** — defter TRAIL ort. +%10.9 / SL ort. −%48 ROI →
   basabas WR **%81.5**, yalniz UP rejimi (%88.6) ustunde.
2. **Boyut** — `fixed_roi` stopta nominal tavan her islemde baglayici →
   pozisyon = sermayenin %10'u, **SL = %5 sermaye** (17→22 Agu marj 36→162).
3. Stop mesafesi **sorun degil**: 4 kaybin hicbiri stop sonrasi 4 saatte
   girise donmedi.

## E6 sonuclari

| Varyant | Ne | AYI | YATAY | BOGA | Hukum |
|---|---|---|---|---|---|
| E6a | `TF_REGIME=5m` | 1.01 / +136 | 0.99 / −64 | 1.52 / +1944 (−%50) | ❌ **gercek RED** |
| E6b | marj %5 (negatif kontrol) | 1.04 / +292 | 1.29 / +1196 | 2.43 / +1951 | ❌ mekanik (olcek) |
| E6c | marj %5 + TP1 %8 | 1.12 / +708 | 1.38 / +1302 | 3.71 / +2299 | ❌ mekanik (olcek) |
| E6d | stop ROI %40 tek | 1.20 / +2665 | 1.17 / +1575 | 1.67 / +2764 (−%29) | ❌ |
| **E6e** | **stop %40 + TP1 %8** | **1.40 / +3923 (DD 1937)** | **1.43 / +2888** | **1.99 / +3280 (−%16)** | ✅ **ADAY** |

## Kritik okuma (elle, 2026-08-23)

- **E6b bir NEGATIF KONTROLDUR ve GECTI**: marj %5 boyutlamayi dogrusal
  yarilar → PF tabanla **birebir ayni** (1.04/1.29/2.43), maxDD **yari**
  (1841/1614/367 vs 3683/3229/735). Yani "risk katmani sinyali degistirmez"
  dogrulandi ve P2'nin "BOGA −%20" hukmu burada bir **OLCEK ARTEFAKTIDIR**
  ([[20-kararlar/P2-karar-kurali]] olcek notu).
- **Kaybi kucultmek yalniz erken BE ile birlikte calisir**: E6d (stop %40 tek)
  bogayi −%29 bozdu, E3a (%30) felaketti; ama E6e (stop %40 **+ TP1 %8**)
  P2'yi gecti.
- E5 serisi (kaldirac tavani kombinasyonlari) **tamamen reddedildi** —
  kaldirac kisiti bogayi olduruyor.

## Sonuc

E6e → [[20-kararlar/D16-a-plus-risk-paketi]] olarak uygulandi ve **14 dakika
sonra kullanici karariyla GERI ALINDI**: cozum ayar degil sinyaldir
([[20-kararlar/karar-sinyal-oncelik]]). **Olcum bilgi olarak kalir.**

⚠️ Gunluk kesici harness'ta **modellenmez** (koruma katmani, Binance income
tabanli).

ILGILI: [[20-kararlar/D16-a-plus-risk-paketi]] · [[20-kararlar/D12-tp1-8]] · [[30-deneyler/E4-autoresearch]]
