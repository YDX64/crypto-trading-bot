---
tags: [karar, geri-alindi, risk, boyutlama]
guncelleme: 2026-08-24
kaynak: docs/DECISIONS.md D16 (satir 284), docs/EXPERIMENTS.md E6
---
# D16 — A-plus risk paketi · **GERI ALINDI** (kullanici karari)

**Karar (uygulandi 02:56, geri alindi 03:10 sunucu saati, 2026-08-23).**
`SCALPER_MAX_MARGIN_PCT` 10→5 · `SCALPER_FIXED_STOP_ROI_PCT` 50→40 ·
`SCALPER_TP1_ROI` 10→8 · `SCALPER_DAILY_LOSS_LIMIT_PCT` 10→6.

**Geri alma gerekcesi (kullanici, baglayici).** *"Yuzde 10'u kullanacaksin her
islem icin ve TP1 yuksek olacak; yapman gereken ayarlardan ziyade dogru
sinyali bulmak veya uretmek."* → [[20-kararlar/karar-sinyal-oncelik]].

**Kanit (bilgi olarak KALIR, uygulanmaz).**
- E6b (marj %5): PF tabanla birebir (1.04/1.29/2.43), DD yari → boyutlama
  dogrusal; P2'nin "boga −%20" hukmu burada **olcek artefaktidir**.
- **E6e (stop %40 + TP1 %8): P2 GECTI** — AYI 1.04→**1.40** (+584→+3923,
  DD 3683→1937), YATAY 1.29→1.43, BOGA 2.43→1.99 (−%16).
- E6d (stop %40 tek) bogayi −%29 bozdu; E3a (%30) felaketti → **kaybi
  kucultmek yalniz erken BE ile birlikte calisir**.

**Durum.** GERI ALINDI. Yuruyen tek soak **D6**'dir; metinlerde "D6+D16 soak"
gorursen eskimistir.
**Geri alma komutu (uygulandi).**
`cp backups/env.bak-20260823-025623-riskpaketi .env` + restart.

ILGILI: [[30-deneyler/E5-E6-risk-paketi]] · [[20-kararlar/D12-tp1-8]] · [[20-kararlar/P2-karar-kurali]]
