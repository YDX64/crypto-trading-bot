---
tags: [karar, aktif, stop, boyutlama]
guncelleme: 2026-08-24
kaynak: docs/DECISIONS.md D8 (satir 44), src/strategies/scalper/setups.py
---
# D8 — Stop modu `fixed_roi` %50, dinamik kaldirac 3-20x, ATR tabani 0.5, kayip cooldown 60 dk · AKTIF

**Karar.** `SCALPER_STOP_MODE=fixed_roi`, `SCALPER_FIXED_STOP_ROI_PCT=50`,
dinamik kaldirac 3-20x, `SCALPER_STOP_ATR_FLOOR_MULT=0.5`,
`SCALPER_LOSS_COOLDOWN_MINUTES=60`.
**Gerekce.** BEAT cokusu (7 dakikada 4 SL): yapisal stop dibe yapisiyordu ve
yeniden giris engeli yoktu.
**Kanit.** Canli olay analizi (2026-08-11).
**Durum.** AKTIF.
**Geri alma.** `SCALPER_STOP_MODE=structural` — ama `min_rr`/`min_stop_pct`/
`max_stop_pct` tutarliligini startup dogrular (`src/core/config.py:1084`).
**Nerede.** `src/strategies/scalper/setups.py:87`; ATR tabani
`src/strategies/scalper/setups.py:55`; cooldown
`src/strategies/scalper/executor.py:702`.

ILGILI: [[10-mimari/emir-yurutme]] · [[20-kararlar/reddedilen-kararlar]] · [[20-kararlar/D13-kaldirac-tavani]]
