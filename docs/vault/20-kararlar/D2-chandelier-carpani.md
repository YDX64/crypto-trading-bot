---
tags: [karar, aktif, cikis, trailing]
guncelleme: 2026-08-24
kaynak: docs/DECISIONS.md D2 (satir 15), src/strategies/scalper/indicators.py
---
# D2 — Chandelier ATR carpani 2.5 → 3.5 · AKTIF

**Karar.** `SCALPER_CHANDELIER_ATR_MULT=3.5` (kod varsayilani 2.5).
**Gerekce.** Dar trailing kazananlari erken kesiyordu.
**Kanit.** C-only 14 gun: **−2401 → +1092**, kazanma orani yukseldi.
**Durum.** AKTIF (2026-08-19). Yedek `backups/env.bak-20260819-chandelier`.
**Geri alma.** Yedegi kopyala + korumali restart.
**Nerede.** `src/strategies/scalper/indicators.py:280`,
uygulama `src/strategies/scalper/exits.py:753`.

ILGILI: [[10-mimari/cikis-yonetimi]] · [[20-kararlar/D11-chandelier-3-0]] · [[20-kararlar/reddedilen-kararlar]]
