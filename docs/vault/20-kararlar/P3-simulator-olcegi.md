---
tags: [karar, metodoloji, backtest]
guncelleme: 2026-08-24
kaynak: docs/DECISIONS.md P3 (satir 2881)
---
# P3 — Simulator olcegi · BAGLAYICI

**Kural.** Simulator boga penceresinde canli defterin **seklini** birebir
uretir (LONG baskin) ama **olcek ~3×** buyuktur (boyutlama farki).

**Sonuc.** Kararlar **mutlak** sayilarla degil **goreli farklarla** verilir;
**canli defter nihai hakemdir**.

**Pratik.** "Backtest +4598 dedi" cumlesi tek basina bir sey ifade etmez;
onemli olan taban ile varyant arasindaki fark ve DD/PF orani.

ILGILI: [[20-kararlar/P2-karar-kurali]] · [[10-mimari/defter-ve-muhasebe]] · [[30-deneyler/00-metodoloji-uyarisi]]
