---
tags: [karar, metodoloji, parite, baglayici]
guncelleme: 2026-08-24
kaynak: docs/DECISIONS.md P1 (satir 2866), src/strategies/scalper/backtest.py
---
# P1 — Harness = canli motor (parite) · BAGLAYICI

**Kural.** Backtest harness'i ile canli motor **AYNI kurallari** uygular.
Motorda bir kapi/filtre degisirse harness da degisir **ve parite testi
guncellenir**. Aksi halde iki defter kiyaslanamaz hale gelir.

**Tarihce.** Harness rejim kapisini uygulamiyordu → o tarihe kadarki **tum eski
sayilar gecersiz sayildi**. Harness ≥ `7640c0a` (kapi-pariteli).

## Parite listesi

| Kural | Canli | Harness |
|---|---|---|
| Rejim kapisi | `src/strategies/scalper/engine.py:1932` | `src/strategies/scalper/backtest.py:1152` |
| Kapasite kapisi (`SCALPER_MAX_POSITIONS`) | `_evaluate_symbol` | `src/strategies/scalper/backtest.py:1915` (post-hoc kronolojik gecis) |
| Lider piyasa kapisi | `src/strategies/scalper/engine.py:1961` | **ayni fonksiyon nesnesi** `src/strategies/scalper/market_gate.py:185` |
| Yapi kapisi | `src/strategies/scalper/structure.py:373` | ayni saf fonksiyon cifti |
| Stop politikasi | `src/strategies/scalper/setups.py:87` | ayni fonksiyon |

**Bilinen sapmalar.**
- Kapasite kapisi harness'ta **post-hoc**tur (semboller bagimsiz simule edilir,
  sonra kronolojik tek gecis) — **sembol-ici degil**.
- Sembol basina backtest'te **tek escanli pozisyon**.
- **VERI TARAFI** paritesi D17'ye kadar acikti: canli testnet, harness mainnet
  mumu okuyordu ([[20-kararlar/D17-ayri-market-data-host]]).
- Risk-olaylari (D10), TV olaylari (D19) ve adli kayit (D21) **bilincli olarak**
  harness'a girmez — karar kurali degildirler.

**Ihlal ornegi.** [[20-kararlar/D23-ai-kapisi]] `active`e gecerse harness
paritesi kurulmadan canliya alinamaz.

ILGILI: [[20-kararlar/P2-karar-kurali]] · [[90-ai-icin/calisma-kurallari]] · [[10-mimari/motor-scalper]]
