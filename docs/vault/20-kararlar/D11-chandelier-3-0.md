---
tags: [karar, aday, cikis]
guncelleme: 2026-08-24
kaynak: docs/DECISIONS.md D11 (satir 152), docs/EXPERIMENTS.md E4b
---
# D11 — Chandelier 3.0 (autoresearch E4b) · ADAY, UYGULANMADI

**Karar onerisi.** `SCALPER_CHANDELIER_ATR_MULT` 3.5 → 3.0.
**Kanit (tur-1).** AYI 1.06→1.07 (+248) · YATAY 1.33→1.35 (+147) ·
BOGA 2.18→2.20 (+36); toplam +431 (≈%5.8), DD benzer.
Tur-2 tekrari: +448 (ADAY, "AYI ve YATAY birlikte iyilesti" kolu).
**Durum.** ADAY. **AYI PF hala < 1.1** — kenar ince.
**Neden uygulanmadi.** D6'nin testnet soak'u (≥5 gun) bitmeden uygulanmaz;
degisiklikler ust uste bindirilmez (soak kirlenir).
**Geri alma.** Uygulanmadi — gerekmiyor.
Log: `logs/autoresearch/2026-08-21/`.

ILGILI: [[20-kararlar/D2-chandelier-carpani]] · [[30-deneyler/E4-autoresearch]] · [[20-kararlar/P2-karar-kurali]]
