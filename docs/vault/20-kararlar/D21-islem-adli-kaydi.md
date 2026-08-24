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

**Nerede.** `src/strategies/scalper/forensics.py:358` / `:518` / `:679`,
`src/strategies/scalper/forensics_log.py:208`,
`src/strategies/scalper/engine.py:2656`, uclar `src/main.py:3041`.

## D27 duzeltmeleri (2026-08-24) — belge alanlari DEGISTI

Cikis belgesine dort yeni alan girdi ve ikisi artik **`None` olabilir**:

| Alan | Anlami |
|---|---|
| `gross_source` | `ledger_legs` (borsa fill'leri, merdiven dahil) · `single_leg_estimate` · `unmeasured_ladder` |
| `fee_estimate_source` | `unmeasured` / `inconsistent` ise `fee_estimate` **`None`**'dir |
| `mae_roi_pct_sampled` + `mae_source` | `corrected` ise yoklama fiziksel kelepceyi ihlal etmisti; ham deger burada durur |
| `mae_samples` | MAE kac kez yoklandi (cozunurluk) |

`fee_dominated` etiketi artik yalniz OLCULMUS komisyonla atilir. `FORENSICS_VERSION`
**bump EDILMEDI** (alanlar eklemeli, migration YOK). Ayrinti:
[[20-kararlar/D27-olcum-borcu-karsi-olgu]].

ILGILI: [[10-mimari/gozlem-katmanlari]] · [[50-veri/loglar]] · [[40-isletme/gunluk-kontrol]] · [[20-kararlar/D27-olcum-borcu-karsi-olgu]]
