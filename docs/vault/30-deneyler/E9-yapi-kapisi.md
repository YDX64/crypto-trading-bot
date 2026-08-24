---
tags: [deneyler, E9, yapi, choch, red]
guncelleme: 2026-08-24
kaynak: docs/EXPERIMENTS.md "Piyasa yapisi (CHoCH/BOS) kapisi (E9)" (satir 686)
---
# E9 — Piyasa yapisi (CHoCH/BOS) kapisi · **7/7 varyant RED**

24 kosu, 7 varyant (S1, S2, S3, S4, S1p3, S1p8, S2p8). Loglar
`logs/structure/*.log`. Hukum: **hicbiri P2'yi gecemedi**.

## Giris kapisi (yapiya ters islem yasagi)

| Varyant | Ne | AYI | YATAY | BOGA |
|---|---|---|---|---|
| S1 | 5m, pivot 5 | 0.85 / −1057 | 0.93 / −356 | **−%67** |
| S2 / S2p8 | 15m, pivot 5 / 8 | en iyi hal PF **1.00** | −%91 | −%61 |

**Kok neden.** Strateji C **kontr-trend**tir; "yapiya ters islem yasagi" tam
da kar kaynagini yasaklar. Pivot buyudukce sonuc tabana yakinsiyor =
"en iyisi hicbir sey yapmamak".

## Cikis tetikleyicisi (ters CHoCH → BE / market kapanis)

| Varyant | WR | AYI |
|---|---|---|
| S3 (BE) | %85 → **%48** | −1589 |
| S4 (market kapanis) | %85 → **%34** | −2442 |

**Mekanizma.** SL sayisi 29 → 1'e duser (kayip kesiliyor **gibi gorunur**)
ama **TRAIL kazananlari 182 → 29** cokuyor. Odeme asimetrisi yuzunden bu
takas net NEGATIFTIR: bir SL'yi onlemek icin ~6 kazananı kesiyorsun.

## Gecikme analizi (E9.4)

Yapi kirilimi rejim kapisindan **daha erken** haber verir — bu dogrudur ve
telemetride gorunur. Ama erken haber **bu strateji icin faydali degildir**;
"daha hizli sinyal" ile "daha iyi sonuc" ayni sey degildir.

## Sonuc

Kod repoda, **her sey varsayilan KAPALI**, telemetri olarak
`/scalper/status.structure` alaninda yayinlaniyor.
Karar: [[20-kararlar/D18-yapi-kapisi]] — **ADAY/REDDEDILDI, kayit icin**.

ILGILI: [[20-kararlar/D18-yapi-kapisi]] · [[20-kararlar/D5-rejim-kapisi]] · [[30-deneyler/rejim-referanslari]]
