---
tags: [karar, aktif, strateji]
guncelleme: 2026-08-24
kaynak: docs/DECISIONS.md D1 (satir 10), src/strategies/scalper/setups.py
---
# D1 — Yalniz strateji C aktif · AKTIF

**Karar.** `SCALPER_STRATEGIES=C`. A/B/D kapali.
**Gerekce.** A (trend kirilmasi) PF 0.35; B orneklemsiz; D (EQH/EQL) −660 ve
slot isgaliyle **C'yi zehirliyor**.
**Kanit.** 14 gun × 8 major sweep (kapi ONCESI harness — yon bilgisi gecerli,
mutlak sayilar degil). `docs/DECISIONS.md:10`.
**Durum.** AKTIF (2026-08-19).
**Geri alma.** `.env` → `SCALPER_STRATEGIES=...` + `scripts/restart_safe.sh testnet`.
**Not.** Aktif strateji secimi `src/strategies/scalper/setups.py:931`;
kod varsayilani hala `"A,B,C,D"` (`src/core/config.py:201`) — canli daralttiginda
yerelde backtest kosarken sunucu env'ini kullan.

ILGILI: [[10-mimari/motor-scalper]] · [[20-kararlar/D6-diverjans-sarti]] · [[30-deneyler/00-deney-indeksi]]
