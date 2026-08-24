---
tags: [karar, red, kapi, yapi, choch]
guncelleme: 2026-08-24
kaynak: docs/DECISIONS.md D18 (satir 315), docs/EXPERIMENTS.md E9, src/strategies/scalper/structure.py
---
# D18 — Piyasa yapisi (CHoCH/BOS) kapisi · **ADAY, UYGULANMADI — kanit REDDETTI**

**Karar onerisi.** `src/strategies/scalper/structure.py` — fraktal pivot →
son onaylanmis swing → kapanisla kirilim; ayni yon = **BOS**, ters = **CHoCH**.
Uzerine (a) giris kapisi `SCALPER_STRUCTURE_GATE`, (b) cikis tetikleyicisi
`SCALPER_STRUCTURE_EXIT=off|be|close`.

**Neden denendi.** Kullanici: *"sistem donusleri tespit edemiyor"*; rejim
kapisi (EMA50/200) donusleri saatler gec goruyor.

**Kanit (E9, 24 kosu — 7 varyantin HEPSI P2'yi geciremedi).**
- Giris kapisi 5m/pivot5 (S1): AYI 0.85/−1057 · YATAY 0.93/−356 · BOGA **−%67**.
- 15m pivot 5/8 (S2): en iyi hal AYI PF **1.00** → "en iyisi hicbir sey
  yapmamak"a yakinsiyor.
- CHoCH cikisi (S3/S4): WR %85 → %48 / %34; SL 29→1 dusuyor ama **TRAIL
  kazananlari 182→29 cokuyor** (odeme asimetrisi).

**Kok neden (kavramsal).** C **kontr-trend** bir stratejidir; "yapiya ters
islem yasagi" tam da kar kaynagini yasaklar.

**Durum.** Kod repoya girdi, **HER SEY VARSAYILAN KAPALI**; `.env`'de hicbir
`SCALPER_STRUCTURE_*` anahtari yok. Altin backtest degismeden geciyor.
**Geri alma.** Gerekmez (kapali). Acmak isteyen once E9'u okumali.
**Kayit degeri.** Ayni fikri ikinci kez denemeden once bu notu oku.

**Nerede.** `src/strategies/scalper/structure.py:175` (tarama),
`:358` (durum), `src/strategies/scalper/structure.py:373` (kapi),
`src/strategies/scalper/structure.py:396` (cikis).
Kapi kapaliyken de her turda hesaplanir ve `/scalper/status.structure`
alaninda **telemetri** olarak yayinlanir.

ILGILI: [[30-deneyler/E9-yapi-kapisi]] · [[20-kararlar/D5-rejim-kapisi]] · [[20-kararlar/D19-tv-olay-kanali]]
