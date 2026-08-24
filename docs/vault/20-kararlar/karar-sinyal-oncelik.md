---
tags: [karar, kullanici-karari, baglayici, risk]
guncelleme: 2026-08-24
kaynak: docs/DECISIONS.md D16 geri alma gerekcesi (satir 284), docs/DECISIONS.md D21
---
# Sinyal-oncelik kurali (KULLANICI KARARI, 2026-08-23) · BAGLAYICI

> *"Yuzde 10'u kullanacaksin her islem icin ve TP1 yuksek olacak; yapman
> gereken ayarlardan ziyade dogru sinyali bulmak veya uretmek."*

## Ne demek

| Yasak | Serbest |
|---|---|
| Boyut (marj %), TP1, stop ile **kaybi kucultmek** | Sinyal kalitesini olcmek ve artirmak |
| `SCALPER_MAX_MARGIN_PCT`'i dusurmek | Yeni sinyal kaynagi eklemek |
| TP1'i dusurmek | Kapi eklemek (kanitla) |
| Gunluk kesiciyi sikilastirmayi "cozum" saymak | Kaydi/olcumu gelistirmek |

**Korunacak degerler:** islem basina **%10** marj, **yuksek TP1**.

## Neden onemli

Bu kural [[20-kararlar/D16-a-plus-risk-paketi]]'ni geri aldirdi ve
[[20-kararlar/D21-islem-adli-kaydi]] · [[20-kararlar/D23-ai-kapisi]] ·
[[20-kararlar/D24-olcum-paketi]] kararlarinin **gerekcesidir**: kaybi
ayarla kucultemiyorsak geriye tek yol kalir — sinyali OLCMEK.

## Ajanlara not

[[20-kararlar/D12-tp1-8]] backtest'te en guclu adaydir **ama bu kurala
tabidir**. "Backtest boyle diyor" gerekcesiyle uygulamak kullanici kararini
ihlal eder; once kullaniciya sor.

ILGILI: [[90-ai-icin/calisma-kurallari]] · [[20-kararlar/00-karar-indeksi]]
