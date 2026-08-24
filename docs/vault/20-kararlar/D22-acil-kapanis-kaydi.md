---
tags: [karar, aktif, cikis, telemetri, rest-agirlik]
guncelleme: 2026-08-24
kaynak: docs/DECISIONS.md D22 (satir 2132), docs/RUNBOOK.md "REST agirlik butcesi"
---
# D22 — `-2021` sonrasi acil kapanisin DURUST kaydi + REST agirlik telemetrisi · AKTIF (daraltilmis)

**Tek cumle.** Bot artik kendi fiyat okumasina dayanarak piyasa emri
GONDERMIYOR; yalnizca **borsanin ZATEN yaptirdigi** acil kapanisi deftere
DOGRU yaziyor, REST agirligini OLCUYOR (davranis degistirmeden) ve panoyu
yanlis teshise surukleyen alanlari duzeltiyor.

## 1) `TRAIL_MARKET` / `BE_MARKET`
Koruyucu stop borsaya gonderilir; piyasa seviyeyi gectiyse Binance
`-2021` doner ve `position_manager._emergency_close` pozisyonu reduce-only
MARKET ile kapatir — **bu davranis D22'den ONCE de vardi**. Kusur KAYITTAYDI:
exits bunu `False` diye okuyup "eski SL korunuyor" logluyor, kapanis sonraki
turda `TRAIL` olarak deftere giriyordu (2026-08-23, 3 olay).
Artik etiket `sp.pending_exit_reason`'a **civilenir**, kapanis **SADECE flat
dogrulamasiyla** finalize edilir (**IKINCI MARKET EMRI YOK** — `-2022` yarisi)
ve kapanis fiyati emrin `userTrades` VWAP'indan okunur.

## 2) REST agirlik telemetrisi — **geri cekilme VARSAYILAN KAPALI**
`BINANCE_WEIGHT_SOFT_LIMIT=0`, `BINANCE_WEIGHT_HARD_LIMIT=0`.
**Neden kapali:** testnet'te `X-MBX-USED-WEIGHT-1M` gunluk **MEDYANI 2373**
olculdu; ilk tasarimin 2000/2300 esikleriyle **tarama KALICI dururdu**.
**Esik olcmeden acilmaz.** `max_1m` **dakika dilimlidir**.

## 3) Pano onbellegi ve `as_of`
`/api/status` ve `/scalper/status` sunucuda 5 sn onbelleklenir; yanittaki
`as_of` govdenin KURULDUGU andir — pano "son guncelleme"yi ondan yazar.

## 4) Durum netligi
`entries_blocked_by` + `market_gate.stale_reason` — "kapi bayat" ile "tarama
durdu" karismasin.

**Reddedilen ilk tasarim (12 ajan, 4 yuksek bulgu).** Ayni host'ta ON-KAPANIS:
(1) yetki genislemesi — geri alinamaz karari botun kendi fiyat okumasina
baglar; (2) bayat/yanlis fiyat riski — tazelik DOGRULUK degildir;
(3) cift emir / `-2022` yarisi; (4) **kazanc yok** — kapanisi engellemiyordu,
yalniz bir tur once ve daha zayif kanitla yapiyordu.

**Durum.** AKTIF (canli 2026-08-23 18:29 UTC).
**Geri alma.** `scripts/deploy.sh awa 17d2eee`.
**Telemetri.** `/scalper/status.trailing_skips` =
`{price_space_skips, protective_gate_skips, market_exits}`.

ILGILI: [[10-mimari/cikis-yonetimi]] · [[40-isletme/sorun-giderme]] · [[50-veri/metrikler]] · [[20-kararlar/reddedilen-kararlar]]
