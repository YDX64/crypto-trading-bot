---
tags: [karar, aktif, kapi, rejim]
guncelleme: 2026-08-24
kaynak: docs/DECISIONS.md D5 (satir 26), src/strategies/scalper/engine.py, src/strategies/scalper/regime.py
---
# D5 — Rejim kapisi: DOWN'da LONG / UP'ta SHORT yasak · AKTIF

**Karar.** `SCALPER_REGIME_FILTER=true`, TV sinyallerine de uygulanir
(`SCALPER_TV_REGIME_FILTER=true`). Rejim = EMA50/200,
`SCALPER_TF_REGIME=15m`.
**Gerekce.** C kontr-trend bir stratejidir; dusen bicak LONG'lari en buyuk
kayip kaynagiydi.
**Kanit (AYI penceresi).** kapi ACIK PF 0.97 / −2042 · kapi KAPALI PF 0.68 /
**−36506**; 377 dusen-bicak LONG engellendi; maxDD 41.7k → 11.9k.
TV'ye de uygulandi cunku TV sinyalleri 2 gunde −41 USDT etmisti.
**Durum.** AKTIF (2026-08-16/19). Bu kapinin degeri ~34.5k'dir — **kapatma**.
**Geri alma.** `SCALPER_REGIME_FILTER=false` (onerilmez).
**Nerede.** `src/strategies/scalper/engine.py:1932`; rejim
`src/strategies/scalper/regime.py:19`. Harness paritesi zorunlu
([[20-kararlar/P1-harness-parite]]).

ILGILI: [[10-mimari/motor-scalper]] · [[20-kararlar/D15-lider-kapisi]] · [[30-deneyler/rejim-referanslari]]
