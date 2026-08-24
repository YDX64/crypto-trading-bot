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
**Nerede.** `src/strategies/scalper/engine.py:1479`. Tur basina **en fazla 1
kapanis** — 2026-08-14 watchdog dersi.

## Olculen maliyet + etiket ayrimi (D27/A1, 2026-08-24)

Reaper kapanisi duz bir reduce-only MARKET emridir; ledger dogrulamasi onu
GOREMEZ ve kaba cikarim (`src/strategies/scalper/exits.py:2202`) yas kesmesini
**"SL" diye etiketliyordu**. Olculen: **43 kesme = −172.3 USDT = brut zararin
%27'si**, ve bunlarin **12'si ARTIDA** kesilmisti — yani her SL analizi
bozuktu. Artik ayri `REAPER` etiketi var
([[20-kararlar/D27-olcum-borcu-karsi-olgu]]); **geriye donuk veri duzeltmesi
YOKTUR** ve damga yalniz bellekte oldugu icin restart ayrimi EKSIK saydirir.

Analizin **kosullu reaper** onerisi (yas ≥8sa **VE** gerceklesmemis ROI < −25
ise kapat) HENUZ UYGULANMADI — o bir DAVRANIS degisikligidir ve uc rejim
penceresi + #P1 parite testi ister.

ILGILI: [[10-mimari/cikis-yonetimi]] · [[40-isletme/sorun-giderme]] · [[20-kararlar/D27-olcum-borcu-karsi-olgu]]
