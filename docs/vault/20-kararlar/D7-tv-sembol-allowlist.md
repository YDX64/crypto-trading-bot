---
tags: [karar, aktif, tradingview, allowlist]
guncelleme: 2026-08-24
kaynak: docs/DECISIONS.md D7 (satir 40), docs/EXPERIMENTS.md TV/LuxAlgo olcumleri
---
# D7 — TV sembol allowlist BTC/ETH/XRP/SOL/BNB · AKTIF

**Karar.** `SCALPER_TV_SYMBOL_ALLOWLIST` = BTC/ETH/XRP/SOL/BNB.
**Gerekce.** LuxAlgo backtester olcumleri sembol bazinda ayrisiyordu.
**Kanit.** 5m varsayilan backtester: ETH/XRP/BTC uc pakette de pozitif;
**LTC ucunde de negatif**; BNB/ADA/DOGE karisik.
**Durum.** AKTIF (2026-08-20). Yedek `env.bak-20260821-tvallow`.
**Geri alma.** `.env`'de listeyi bosalt (= filtre yok) + korumali restart.
**Not.** D19a/F'ten beri **TV OLAY yolu da** bu allowlist'i uygular.

ILGILI: [[10-mimari/tv-sinyal-yolu]] · [[30-deneyler/tradingview-olcumleri]]
