---
tags: [karar, aktif, olcum, forensics, karsi-olgu]
guncelleme: 2026-08-24
kaynak: docs/DECISIONS.md D27 (satir 2800), docs/RUNBOOK.md "Yeni (D27, 2026-08-24)"
---
# D27 — Olcum borcu + karsi-olgu defteri · AKTIF · **YALNIZ OLCUM**

2026-08-24 kok-neden analizinin hukmu: sistem melt-up disinda zararda ve
**bugun yapilacak en degerli sey kod degil OLCUM**. Cunku raporun butun filtre
rakamlari "ust sinir tahmini" olarak kaliyordu: engellenen bir islemin yerine
kapasite/kayip-cooldown serbestligiyle BASKA bir islem acilir ve bu, kapali
islem defterinden cikarilamaz.

**Motor karar yolu BAYT BAYT AYNI.** Hicbir `SCALPER_*` parametresi degismedi;
giris kurallari, boyutlama, TP/stop seviyeleri, kapi sirasi ve emir yolu
birebir ayni. Altin backtest sayilari degismedi.

## A) Olcum borcu — dort etiket/hesap kusuru

| # | Kusur (olculen) | Duzeltme | Nerede |
|---|---|---|---|
| A1 | 8 saatlik yas kesmesi (D4) deftere **"SL"** yaziliyordu: **43 kesme = −172.3 USDT = brut zararin %27'si**, **12'si ARTIDA** | ayri `REAPER` etiketi | `src/strategies/scalper/exits.py:81` · `src/strategies/scalper/exits.py:2202` · `src/strategies/scalper/engine.py:1479` |
| A2 | brut TEK cikis fiyatiyla hesaplaniyordu; merdiven 3 parca → **22 islemin 8'inde** komisyon teorik degerin 2 kati, **5'inde NEGATIF** | merdiven-farkinda brut + `gross_source` | `src/strategies/scalper/exits.py:1565` · `src/strategies/scalper/forensics.py:518` |
| A3 | 6 stop-out'ta `mae_roi` **fiziksel olarak imkansiz** (#217: mae −7.16 iken cikis −24.72) | fiziksel kelepce + `mae_source="corrected"` | `src/strategies/scalper/forensics.py:632` |
| A4 | 3 islemde TP1 emri konulamadi → break-even HIC kurulamadi → tam risk stopu (**−18.4 USDT**) | sayac + CRITICAL + pano uyarisi | `src/strategies/scalper/executor.py:584` |

### A1'in gizli tuzagi — cooldown paritesi
`exits._maybe_start_loss_cooldown` kapisi ETIKETI OKUR:
`if exit_reason != "SL" and realized_pnl >= threshold: return`. Yani **"SL"
etiketi PnL ARTIDA olsa bile cooldown BASLATIR**. Etiketi dogrudan
degistirmek, artida kesilen 12 pozisyonda cooldown kararini sessizce
kaldirirdi. Bu yuzden `_infer_exit_reason_legacy`
(`src/strategies/scalper/exits.py:2231`) D27 ONCESI govdeyi **bit duzeyinde**
korur ve kapi ayri bir `cooldown_reason` degiskeniyle ESKI etiket uzayini
okur. Deftere/adli kayda YENI etiket yazilir.

**Geriye donuk veri duzeltmesi YOKTUR** — 2026-08-24 oncesi yas kesmeleri
defterde hala "SL"dir (`scripts/ledger_report.py:77` bunu not olarak basar).
Damga yalniz BELLEKTEDIR: restart, ayrimi EKSIK saydirir (asla fazla degil).

### "Uydurma sayi YASAK"
Brut olculemediginde `fee_estimate` **`None`**'dir ve `fee_dominated` etiketi
ATILMAZ; brut−net negatif cikarsa kaynak `"inconsistent"` olur. MAE duzeltmesi
SESSIZ DEGILDIR: ham orneklem `mae_roi_pct_sampled`ta durur, `mae_samples`
yoklama sikligini verir. Kiyas FIYAT tabanlidir (`price_move_pct × kaldirac`),
net PnL degil — komisyon yuzunden eksiye dusen basabas kapanislar
yanlis-pozitif uretirdi.

## B) Karsi-olgu defteri

Reddedilen (`deny`) ya da emir hatasi alan (`error`) her giris niyeti icin
niyet anindaki fiyat/stop/TP1/kaldirac kalici olarak kaydedilir, sonra H saat
sonra "girilseydi mevcut TP/SL kurallariyla ne olurdu" simule edilip
`logs/trades.jsonl`'e `event="counterfactual"` olarak yazilir.

| Katman | Yer |
|---|---|
| SAF cekirdek (pencere / simulasyon / cozum / ozet) | `src/strategies/scalper/counterfactual.py:289` · `:343` · `:441` · `:621` |
| Durum katmani (kuyruk, dedup, plan tamamlama) | `src/strategies/scalper/counterfactual_store.py:186` · `:268` · `:329` |
| Motor kancalari | `src/strategies/scalper/engine.py:2591` · `src/strategies/scalper/engine.py:2638` |
| Uc | `src/main.py:3102` (`/scalper/counterfactual`) |
| Rapor | `scripts/ledger_report.py:1310` · `scripts/ledger_report.py:1350` |
| JSONL okuma | `src/strategies/scalper/forensics_log.py:115` |

**Neden.** Raporun EN KRITIK acik sorusu TV saglamasidir: kapinin yon ICINDE
secicilik gucu olculebilir SIFIR (**LONG p=0.894, SHORT p=0.368**) ve en sik
cift ayni saticinin iki script'idir (`luxosc+luxso`, tetiklerin %54'u, PF
0.858 — tek kaybeden cift). "Reddedilen 150+ sinyal gercekten kotu muydu?"
sorusunun bugun SAYISAL cevabi yok.

**Yeni REST agirligi SIFIR.** Cozum, tarama turunun ZATEN cektigi
`ctx.candles_5m` ile yapilir (150 mum ≈ 12.5 saat; en buyuk varsayilan ufuk 8
saat). Mum yoksa hesap ERTELENIR.

**Look-ahead YOK.** Yalniz niyet anindan SONRA ACILMIS mumlar gorulur; ayni
mumda hem stop hem TP1 vurursa **STOP kazanir** (karamsar taraf). Plani
olmayan niyetlerde referans giris, niyet anindan SONRAKI ILK mumun `open`
fiyatidir.

**Modelin durust sinirlari.** Yalniz TP1 ya da ILK STOP modellenir. TP2,
chandelier trailing, break-even cekme, 8 saatlik reaper, komisyon, kayma ve
kismi dolum MODELLENMEZ. Yani tablo motorun gercek sonucu DEGIL, ayni
kurallarla kaba bir kiyas tabanidir.

## Bilinen bosluklar (durustluk)
- **Ikinci-derece etki hala olculmuyor**: defter "o sinyal iyi miydi"yi soyler,
  "engellenmeseydi hangi BASKA islem acilmazdi"yi soylemez.
- **Bekleyen kuyruk surec-icidir**; restart cozulmemis niyetleri dusurur.
  Kalici iz niyetin kendisidir (`event="intent"`).
- **`fee_estimate` aslinda "komisyon + funding"dir** (net, FUNDING_FEE'yi de
  icerir; brut icermez). Ayristirilmadi.
- **Defter bugun BOS baslar.** `measured: 0` = "olculmedi", "etki yok" DEGIL.

**Geri alma.** `SCALPER_COUNTERFACTUAL_ENABLED=false` (yalniz defter) ya da
`SCALPER_FORENSICS_ENABLED=false` (adli kayit + niyet + defter birlikte).
A1–A3 etiket/hesap duzeltmeleri kod yolundadir, bayrakla kapanmaz.

ILGILI: [[20-kararlar/D4-reaper]] · [[20-kararlar/D21-islem-adli-kaydi]] · [[20-kararlar/D24-olcum-paketi]] · [[10-mimari/gozlem-katmanlari]] · [[10-mimari/cikis-yonetimi]] · [[10-mimari/defter-ve-muhasebe]]
