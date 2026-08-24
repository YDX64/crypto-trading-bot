---
tags: [deneyler, D24, olcum, drawdown, konsantrasyon]
guncelleme: 2026-08-24
kaynak: docs/EXPERIMENTS.md "D24 olcum/kanit paketi" (satir 1143)
---
# D24 olcumleri — bar-bazli cokus, kelepce, konsantrasyon

Motor davranisi **DEGISMEDI**; altin sayilar degismedi (2 islem /
`total_pnl` 26.77 / `{"regime_gate": 4}`).

## D24.1 — Bar-bazli cokus, kapanis-bazlinin GORMEDIGI cukur

Altin kosuda (BTCUSDT+ETHUSDT, 2026-08-07→08-10):

| Metrik | Deger |
|---|---|
| `max_drawdown` (bugunku, yalniz islem KAPANISLARINDA ornekleniyor) | **0.00** |
| `bar_max_drawdown` (her 5m barinda mark-to-market) | **11.46** |
| Toplam PnL | 26.77 |
| Cukurun toplam kara orani | **%42.8** |

**Okuma.** Bu pencerede iki islem de kazandi → kumulatif PnL hic dusmedi ve
bugunku metrik **"sifir risk"** raporluyordu; oysa portfoy bar-icinde karin
%43'u kadar su altindaydi. 1000 USD sermaye ve %10/islem sabit boyutta bu
**dogrudan hayatta kalma sorusudur**.
Degismez (test edildi): `bar_max_drawdown` ≥ `max_drawdown`.

## D24.2 — Permutasyonda kelepce ZORUNLU

Upstream dort goreli bileseni bagimsiz karistirdigi icin permute barlarda OHLC
tanimi bozuluyor. Olculdu (103.680 permute bar):
**High < max(O,C) → %28.2** · **Low > min(O,C) → %29.4** · en az bir ihlal
tasiyan bar **%57.6**.

**Kelepcenin null'u kaydirmasi** (ayni tohumlar): `winrate` null ortalamasi
**+7.61 puan** · `bar_max_drawdown` +2.84 · `total_pnl` +2.21.
→ Kelepcesiz null permute dunyayi **sistematik olarak TP/trail aleyhine**
bozuyor ve gercek sonucu **oldugundan anlamli** gosteriyordu.
**Kelepce kozmetik degildir.**

**Yon olcumu.** `max_drawdown`/`bar_max_drawdown`'da **kucuk olan iyidir**.
Upstream'in sabit yonu sentetik vektorde p=0.05 yerine **p=0.96** uretiyor.
Yonu tanimli olmayan metrik icin p-degeri **hic uretilmez**
(`profit_factor=∞` gibi sonlu olmayan degerde de).

## D24.3 — Konsantrasyon (altin kosu)

`top_symbol` BTCUSDT %100 · `top_trade_pnl_share` **%60.5** ·
`top_day` 2026-08-07 **%60.5**.
Pay YALNIZ toplam PnL POZITIFKEN tanimlidir; degilse `—` (tanimsiz) —
**"olculmedi" degil**. **Esik DEGIL, bilgi satiri**: soak kontrol listesine
girmez.

## D24.4 — Maliyet stresi: **arac hazir, uc pencerede KOSULMADI**

`--fee-stress`, `--entry-delay-candles N`, `SCALPER_SLIPPAGE_RATE`
(vars. 0.0002 = **DEGISMEDI**). E10 yalniz AYI penceresinde kostu.

## D24.5 — Niyet kaydi: kapsama bugun **SIFIR**

`event="intent"` satiri ve `/scalper/forensics/summary` → `intents` blogu
eklendi. Sayaclar **surec basindan beridir** ve restart'ta sifirlanir.
`horizon_end_at`/`invalid_if`/`confidence`/`model_version` semaya girdi ama
**DOLDURAN yol yok** → `with_expectation: 0`. Bu **DOGRU** sonuctur:
null = "olculmedi".

ILGILI: [[20-kararlar/D24-olcum-paketi]] · [[30-deneyler/E10-permutasyon]] · [[10-mimari/gozlem-katmanlari]]
