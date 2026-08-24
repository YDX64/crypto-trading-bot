---
tags: [karar, arastirma, kaldirac]
guncelleme: 2026-08-24
kaynak: docs/DECISIONS.md D13 (satir 167), docs/EXPERIMENTS.md E4i/E4j/E5
---
# D13 — Kaldirac tavani bulgusu (E4i/E4j) · ARASTIRMA

**Bulgu.** `fixed_roi` modunda kaldirac ↑ = stop mesafesi ↓ → gurultu stop'u.
`SCALPER_DYN_LEV_MAX` 20→10: AYI PF **1.68** / +5624 (taban +886) ama
BOGA yalniz 55 islem (**orneklem yetersiz**). 15: ayi +3293, boga **−%39**.
**Okuma.** Genis stop (dusuk kaldirac) ayida SL'leri keskin azaltiyor,
bogayi olduruyor → rejim-bagimli.
**Durum.** ARASTIRMA — kural olmadi.
**Parite notu.** E4h'te harness `SCALPER_MAX_POSITIONS` kapasitesini
modellemiyordu; bu bosluk sonradan `_apply_capacity_gate` ile kapatildi
([[20-kararlar/P1-harness-parite]]).
**Sonraki aday.** DYN_LEV_MAX 12-15 + TP1 8 birlesimi — E5a/E5b/E5c'de
denendi ve **reddedildi** ([[20-kararlar/reddedilen-kararlar]]).

ILGILI: [[20-kararlar/D8-stop-modu-fixed-roi]] · [[30-deneyler/E4-autoresearch]]
