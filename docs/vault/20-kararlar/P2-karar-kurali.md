---
tags: [karar, metodoloji, backtest, baglayici]
guncelleme: 2026-08-24
kaynak: docs/DECISIONS.md P2 (satir 2877), docs/EXPERIMENTS.md
---
# P2 — Karar kurali (3 rejim penceresi) · BAGLAYICI

**Aday sayilmak icin:**
> **AYI PF ≥ 1.1** (VEYA AYI ve YATAY PnL **birlikte** iyilesir)
> **VE** **BOGA PnL kaybi ≤ %20**

Tek pencerede parlayan **reddedilir**. Ayrica autoresearch her pencerede
**≥60 islem** ister; altinda kalan varyant "asiri filtreleme" ile reddedilir.

## Uc pencere (sabit)

| Pencere | Tarih | Not |
|---|---|---|
| **AYI** | 2026-01-23 → 02-13 | BTC −%30 |
| **YATAY** | 2026-07-01 → 07-21 | — |
| **BOGA** | 2026-08-07 → 08-21 | son 240 gunun en iyi 14 gunu icerir |

## Komut kalibi (sunucu env'i ZORUNLU)

```bash
env $(ssh awa grep ^SCALPER_ /opt/tradingbot-v2/.env | xargs) \
  python3 -m src.strategies.scalper.backtest --strategies C \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,BNBUSDT,ADAUSDT,LTCUSDT \
  --start 2026-01-23 --end 2026-02-13
```
**Sirali kos** (paralel = Binance 429). ~4 dk/kosu.

## Olcek notu (2026-08-23)
Boyutlama gibi **dogrusal** degisikliklerde (marj/risk yuzdesi) PnL kurali
mekanik olarak RED verir; bu adaylar **PF/DD oraniyla** okunur — E6b negatif
kontrolu bunu kanitladi ([[20-kararlar/D16-a-plus-risk-paketi]]).

## Terfi hatti
backtest (P2) → **testnet ≥5 gun** (en az 1 dusus gunu) → insan onayi → mainnet.

⚠️ **Bu uc pencere OOS DEGILDIR** — [[30-deneyler/00-metodoloji-uyarisi]].

ILGILI: [[20-kararlar/P1-harness-parite]] · [[20-kararlar/P3-simulator-olcegi]] · [[20-kararlar/mainnet-plani]] · [[90-ai-icin/calisma-kurallari]]
