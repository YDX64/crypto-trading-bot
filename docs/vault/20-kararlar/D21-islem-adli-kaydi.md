---
tags: [karar, aktif, forensics, gozlem]
guncelleme: 2026-08-24
kaynak: docs/DECISIONS.md D21 (satir 2006), docs/ARCHITECTURE.md §5.1
---
# D21 — Islem adli kaydi (trade forensics) · AKTIF · **YALNIZ GOZLEM**

**Karar.** Her scalp isleminin giris ve cikis ANINDA bilinen baglaminin tamami
tek bir JSON belgesine yazilir (`scalp_trades.forensics` + append-only
`logs/trades.jsonl`), kural tabanli etiketlerle (`verdict`) siniflandirilir ve
uc HTTP ucundan + panodaki "adli kart"tan okunur.

**Neden.** Kullanici: *"kayiplar/kazanclar: hangi coin, hangi hareket, hangi
sinyal, hangi giris/cikis, neler etkiliyor — %100 gorursek onler, duzeltir,
gelistiririz."* [[20-kararlar/karar-sinyal-oncelik]] boyut/TP/stop ayariyla
kayip kucultmeyi YASAKLAR; geriye kalan tek yol sinyal kalitesini OLCMEKTIR,
olcum icin once KAYIT gerekir.

**Kapsam siniri (baglayici).**
- Hicbir kapi, boyutlama, stop/TP seviyesi ya da cikis karari bu alani OKUMAZ.
  `BOT_MODE=scalper` emir akisi **byte-for-byte** aynidir.
- Kayit kurulumundaki her hata yutulur ve TEK SEFER WARNING'e duser
  (`tests/test_forensics.py::TestForensicsNeverBlocksTrading`).
- Backtest harness'ina DOKUNULMADI (adli kayit bir karar kurali degildir).

**Durum.** AKTIF (canli 2026-08-23 13:34 UTC).
**Geri alma.** `SCALPER_FORENSICS_ENABLED=false` — yalniz kaydi durdurur,
motor davranisi her iki durumda da aynidir.

## D21-R3 duzeltmeleri (restart davranisi)
- `exits.recover()` DB'deki `forensics.entry` blogunu **belege geri yukler**,
  boylece restart sonrasi kapanan islemin `verdict`i giris etiketlerini tasir.
- Bellekte tutulan cikis zaman cizgisi (TP1/BE ani, trailing sayaci) restart'ta
  **gercekten kaybolur**: belge bunlari `null` birakir ve
  `exit.path.restart_gap=true` der — `0` yazmak uydurma olurdu.
- Disk yazimi olay dongusunun DISINDA (`append_soon`).
- Post-mortem turu AYRI task'ta, 5 sn timeout.

**Nerede.** `src/strategies/scalper/forensics.py:351` / `:511` / `:584`,
`src/strategies/scalper/forensics_log.py:147`,
`src/strategies/scalper/engine.py:2490`, uclar `src/main.py:2984`.

ILGILI: [[10-mimari/gozlem-katmanlari]] · [[50-veri/loglar]] · [[40-isletme/gunluk-kontrol]]
