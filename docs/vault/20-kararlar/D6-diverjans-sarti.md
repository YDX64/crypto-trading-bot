---
tags: [karar, aktif, strateji, sinyal]
guncelleme: 2026-08-24
kaynak: docs/DECISIONS.md D6 (satir 32), docs/EXPERIMENTS.md E2a
---
# D6 — C diverjans sarti ACIK · AKTIF

**Karar.** `SCALPER_C_REQUIRE_DIVERGENCE=true`.
**Gerekce.** 24 kosuluk E2/E3 setinde **uc pencerede birden kazanan tek
varyant**.
**Kanit.** AYI PF 0.97→**1.06** (+886) · BOGA 1.24→**2.18** (+3831) ·
YATAY 0.93→**1.33** (+2745). Islem 814/191/449 → 216/96/150.
maxDD 11857/3610/8254 → 3574/735/3181. SL 120→29.
**Durum.** AKTIF (2026-08-21). Yedek `env.bak-20260821-divergence`.
**Geri alma.** Yedegi kopyala + korumali restart.
**Uyari.** D6 canliya giren karardir ve **bu uc pencerede SECILDI** — olculen
kenari gercek kenarin tarafsiz tahmini degil, bir **UST SINIRIDIR**
([[30-deneyler/00-metodoloji-uyarisi]]).

ILGILI: [[20-kararlar/D1-yalniz-strateji-c]] · [[30-deneyler/E2-E3-varyantlari]] · [[20-kararlar/P2-karar-kurali]]
