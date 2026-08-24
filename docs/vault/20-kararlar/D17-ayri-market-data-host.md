---
tags: [karar, aday, veri, kline, d17]
guncelleme: 2026-08-24
kaynak: docs/DECISIONS.md D17 (satir 1487), docs/ARCHITECTURE.md §2, src/strategies/scalper/data.py
---
# D17 — Piyasa verisi ayri host (`SCALPER_MARKET_DATA_BASE_URL`) · ADAY, varsayilan KAPALI (testnet'te ACILDI)

**Karar.** Doluyken **yalniz public `/fapi/v1/klines`** o host'tan cekilir;
emir, bakiye, pozisyon, `ticker/24hr`, `exchangeInfo`, `income` ve **tum imzali
yollar** `BINANCE_BASE_URL`'de KALIR — API anahtari o host'a asla gitmez.

**Kok bulgu.** `KlineFetcher()` argumansiz kuruluyordu → canli bot testnet'te
oldugu icin RSI / Bollinger / diverjans / rejim / ATR'nin TAMAMI **testnet
mumlarindan** uretiliyordu; harness ise mainnet okuyordu.
**P1 paritesinin veri tarafindaki acigi.**

**Kanit (E8.0).** Motorun yazdigi giris RSI'i 143 C isleminde **testnet** 1m
serisiyle uyusuyor (medyan |Δ| **2.8**), mainnet ile uyusmuyor (**7.4**);
`vol_ratio_5m` hic tasinmiyor (r = **−0.04**). Fiyat SEVIYESI yakin
(medyan sapma %0.054) ama C bir **UC esiginde** karar verir.

**Ikinci kusur (ayni commit'te duzeltildi).** Public kline yolu **ban
koruydu**: `rate_limiter`'i, ban kesicisini ve agirlik basligini HIC
kullanmiyordu → 418 alan cagri 3 kez tekrar deniyor (yasagi uzatiyor) ve
deploy'un `HTTP 418|banned` kilidine gorunmuyordu. `MarketDataGuard`
(`src/strategies/scalper/data.py:372`) bunu host BASINA kapatti.

**Iki katmanli chandelier kalkani (yalniz AYRI host'ta; ayni host'ta NO-OP):**
1. **Dinamik, like-for-like baz cevirisi** — her turda
   `baz = islem_host_canli − veri_host_canli`; olculemezse **tur atlanir**.
2. **Koruma-tarafi kapisi** — LONG stop guncel fiyatin %0.05 altinda olmali;
   degilse emir hic gonderilmez.

**Durum.** Kod varsayilani **KAPALI**; testnet'te 2026-08-23 13:17 UTC acildi
(`https://fapi.binance.com`), `kline_source=separate`.
**Geri alma.** RUNBOOK "Kline kaynagini mainnet'e alma" kapatma komutu
(ayari bosalt + `scripts/restart_safe.sh testnet`).

**Nerede.** Ayar `src/core/config.py:100`, validator `src/core/config.py:761`,
izinli hostlar `src/core/config.py:35`.

ILGILI: [[30-deneyler/E8-sinyal-otopsisi]] · [[10-mimari/cikis-yonetimi]] · [[40-isletme/sorun-giderme]] · [[20-kararlar/P1-harness-parite]]
