---
tags: [karar, aktif, golge, halka, olcum]
guncelleme: 2026-08-24
kaynak: docs/DECISIONS.md D26 (satir 2762), src/main.py
---
# D26 — Golge halkasi (`/opt/tradingbot-shadow`) + golge modunun orchestrator kapisi · AKTIF

**Karar.** Kullanici: *"4 gun once guzel calisan versiyonu da calistirsak …
su anki ayarlari da bozmak istemiyorum"*. Kurulan: AYRI dizin
(`/opt/tradingbot-shadow`), AYRI surec (supervisord `tradingbot_shadow`,
:9092), AYRI DB (`tradingbot_shadow.db`) / state / log; **kod BUGUNKU main**,
`.env` ise 21 Agustos yedegi + golge modu.

**Neden bu bicim (olcum).** 21 Agustos `.env`'i ile bugunku deger bazinda
karsilastirildi: **hicbir mevcut ayarin degeri degismemis**; fark yalniz
EKLENEN anahtarlar. Scalper davranisini etkileyen yalniz IKI tanesi:
`SCALPER_MARKET_GATE=true` (D15) ve `SCALPER_MARKET_DATA_BASE_URL` (D17).
Yani "4 gun onceki surum" ≡ bugunku kod + bu iki anahtar kapali; eski COMMIT'i
kosturmak gerekmiyor.

## ⚠️ BULGU — golge modu orchestrator'i KAPSAMIYORDU
Halka ilk kez ayaga kalkinca golge banner'i dogru basti **ama** orchestrator
`recover_open_positions()` calisti ve **CANLI halkanin 5 pozisyonunu "YETIM"
sanip izlemeye aldi** (10:44:10–10:44:23). Ayni Binance hesabinda ayni
pozisyonun IKI yoneticisi = D20b incelemesindeki kritik sinif.
Halka 5 dakikada durduruldu; **zarar yok** (5 pozisyon ve 3'er koruma emri
saglam kaldi, olculdu 10:49).
**Duzeltme:** `src/main.py:371` — `scalper_shadow_mode` dogruysa orchestrator
**HIC baslatilmaz** (WARNING loglar). Iki test kilitler
(`tests/test_runtime_liveness.py`).

**Sinirlilik (durust).** Golge halkasi TradingView webhook'larini **ALMAZ**
(alarmlar yalniz :9091'e gider; nginx `mirror` canli giris yoluna gecikme
riski ekler → bilincli olarak YAPILMADI). Karsilastirma yalniz **C tarayici
yolunu** kapsar (defterde islemlerin ~%70'i).

**Geri alma.** `supervisorctl stop tradingbot_shadow` (canliya etkisi yok);
kalici kaldirma: conf dosyasini sil + `supervisorctl update`.
**Kanit kapisi.** Halka ancak orchestrator kapisi deploy edildikten SONRA
yeniden baslatilir. Karsilastirma:
`scripts/ledger_report.py --db tradingbot_shadow.db`.

ILGILI: [[20-kararlar/D14-golge-modu]] · [[40-isletme/halka-yonetimi]] · [[90-ai-icin/sik-yapilan-hatalar]] · [[20-kararlar/D15-lider-kapisi]]
