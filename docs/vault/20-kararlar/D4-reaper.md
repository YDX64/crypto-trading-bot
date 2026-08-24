---
tags: [karar, aktif, cikis, reaper]
guncelleme: 2026-08-24
kaynak: docs/DECISIONS.md D4 (satir 22), src/strategies/scalper/engine.py
---
# D4 — Reaper: TP1 gormemis pozisyonu 8 saatte kapat; `trailing_active` MUAF · AKTIF

**Karar.** `SCALPER_MAX_HOLD_HOURS=8`; BE'ye ulasmis (TP1 dolmus) pozisyonlar
yas limitine bakilmaksizin acik kalabilir.
**Gerekce.** Kullanici karari: *"tek durduracak sey stop loss"* — BE korumali
kosucuya ust kapak yok; "bugun kesilen trend yarin devam edebilir".
**Kanit.** Kullanici karari (backtest degil).
**Durum.** AKTIF (2026-08-21).
**Geri alma.** `SCALPER_MAX_HOLD_HOURS=0` (kapali) + korumali restart.
**Nerede.** `src/strategies/scalper/engine.py:1439`. Tur basina **en fazla 1
kapanis** — 2026-08-14 watchdog dersi.

ILGILI: [[10-mimari/cikis-yonetimi]] · [[40-isletme/sorun-giderme]]
