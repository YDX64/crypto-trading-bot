---
tags: [deneyler, E10, permutasyon, istatistik]
guncelleme: 2026-08-24
kaynak: docs/EXPERIMENTS.md "E10 — Permutasyon testi" (satir 1243)
---
# E10 — Permutasyon testi: C'nin girisi sanstan ayirt edilebiliyor mu?

**Soru.** Bugune kadar hic yanitlanmadi: bir backtest sonucunun (PF 1.43)
sans eseri olma olasiligi nedir?

**Yontem.** Monte-Carlo permutasyon (`--permutations`), **AYI** penceresi,
C, 8 sembol, **50 tur**, tohum 12345, sure 2177 sn. Null **KOSULLU**: yalniz
giris dilimi permute edilir, baglam/rejim ondan turetilir.
OHLC tutarliligi icin High/Low **kelepcesi zorunlu** (barlarin %53.7'si
duzeltildi).

| Metrik | Yon | Gercek | Null ort. | Null p05 | Null p95 | p |
|---|---|---|---|---|---|---|
| total_pnl | buyuk iyi | **3812.25** | −7476.28 | −13439.68 | −1569.01 | **0.0196** |
| profit_factor | buyuk iyi | **1.43** | 0.75 | 0.60 | 0.93 | **0.0196** |
| winrate | buyuk iyi | 87.97 | 86.03 | 83.44 | 88.58 | **0.1373** |
| max_drawdown | kucuk iyi | 2956.08 | 9161.00 | 5013.71 | 14046.27 | 0.0392 |
| bar_max_drawdown | kucuk iyi | 2973.70 | 9330.25 | 5199.42 | 14373.33 | 0.0392 |

## Sonuc

Ayi penceresinde giris sinyali **sanstan ayirt edilebiliyor**: 50 turun
HICBIRI gercek PnL'i gecemedi. Rastgele girisle ayni kurallar ortalama
**−7476** kaybediyor.

## ⚠️ KRITIK: kenar NEREDEN GELMIYOR

**Kazanma orani anlamli DEGIL (p=0.137).** Rastgele girisler de %86 kazaniyor,
cunku %85 basabas orani **TP/SL asimetrisinin YAPISAL sonucudur**.
Kenar kazanma oranindan degil, **kayip buyuklugunun kontrolunden** (PnL +
dusus) geliyor.
> *"Kazanma oranimiz %88" cumlesi tek basina kanit DEGILDIR.*

## Cekinceler

1. Null **KOSULLUDUR**, kosulsuz degil.
2. **Tek pencere** — YATAY ve BOGA ayrica kosulmali.
3. 50 tur p tabanini 0.0196'ya kilitler.
4. Bu test stratejinin **canlida kar edecegini SOYLEMEZ**; yalniz backtest
   sonucunun rastgelelikle aciklanamadigini soyler.
5. Ayni uc pencere sorunu burada da gecerlidir
   ([[30-deneyler/00-metodoloji-uyarisi]]).

## Yan olcumler (ayni pencere, tek kosu) — **mainnet kararinda BAGLAYICI**

| Senaryo | Islem | WR% | PnL | PF |
|---|---|---|---|---|
| Taban | 158 | 88.0 | 3812.25 | 1.43 |
| **Maliyet 2×** (`--fee-stress`) | 158 | 88.0 | 1602.18 | 1.17 |
| **Giris 1 mum gec** (`--entry-delay-candles 1`) | 133 | 85.7 | 1923.37 | 1.21 |

Kenar iki strese de dayaniyor **ama ince**: maliyet iki katina cikarsa karin
**%58'i**, giris bir mum gecikirse **%50'si** gidiyor. Mainnet kaymasi
testnet'ten yuksektir.

ILGILI: [[20-kararlar/D24-olcum-paketi]] · [[20-kararlar/mainnet-plani]] · [[10-mimari/defter-ve-muhasebe]]
