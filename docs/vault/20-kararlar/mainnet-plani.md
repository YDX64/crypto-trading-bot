---
tags: [karar, mainnet, terfi, plan]
guncelleme: 2026-08-24
kaynak: docs/MAINNET_PLAN.md, docs/RUNBOOK.md "Mainnet halkasi"
---
# Mainnet plani — sartlar ve mimari

**Durum (2026-08-24): mainnet halkasi YOK.** Testnet botu (`tradingbot_v2`)
tek canli surectir. Bu plan **sartlari** sabitler; **tarih vermez — tarih
kanitla gelir.**

## Uc halka

| Halka | Ne | Nasil girer |
|---|---|---|
| A — Yerel + CI | kod, testler, altin backtest, autoresearch | `git push` → CI yesil |
| B — Testnet (bugunku canli) | `awa:/opt/tradingbot-v2`, `tradingbot_v2` | `scripts/deploy.sh awa`; her degisiklik burada **≥5 gun soak** |
| C — Mainnet (dizin/program YOK, pipeline hazir) | `/opt/tradingbot-main`, `tradingbot_main`, :9092, AYRI `.env`/anahtar/DB/state/log/Telegram | `scripts/deploy.sh awa vX.Y.Z --ring mainnet` |

Halka C **asla `origin/main`'in ucunu almaz**; yalniz B'de soak olmus
**etiketli** surumu alir.

## Terfi olcutleri (hepsi)

1. CI yesil + altin backtest degismedi (ya da bilincli guncellendi).
2. **P2 kurali** uc pencerede ([[20-kararlar/P2-karar-kurali]]).
3. **B soak ≥5 gun**, icinde en az **1 dusus gunu** (BTC gunluk < −%1.5);
   rejime bolunmus rapor pozitif/basabas; `exit_reason=UNKNOWN` orani **< %5**;
   418/429 yok.
4. RUNBOOK guncel, DECISIONS satiri var, **geri alma komutu yazili ve
   DENENMIS**.
5. **Insan onayi** — AI tek basina terfi ETMEZ.

## Mainnet'e ozel koruma katmani

- Boyut tavani testnetin ALTINDA baslar (oneri: ilk 2 hafta marj %3, pozisyon
  2, kaldirac tavani 5x).
- `SCALPER_DAILY_LOSS_LIMIT_PCT` = %3.
- `RISK_EVENT_SECRET` **ZORUNLU** (bos birakilamaz).
- `SCALPER_ENTRY_HALT_ENABLED=true` — mainnet dogrulamasi `false`'u reddeder.
- Yeni parametre once **3 gun golge** ([[20-kararlar/D14-golge-modu]]).
- Gunluk mutabakat: `scalp_trades` ↔ Binance income farki > %1 → uyari + halt.
- Kline kaynagi mainnet host'u ya da bos olmali — **testnet host'u startup'ta
  reddedilir** (`src/core/config.py:1275`).

## Gerceklik notu

Testnet dolumlari **iyimserdir**. Boga penceresinde testnet +832 / 171 islem
≈ islem basina +4.9 USDT; mainnet'te ayni sinyal setinin kenari **daha
incedir**. Olculdu: maliyet 2× → karin %58'i gider; giris 1 mum gecikirse
%50'si ([[30-deneyler/E10-permutasyon]]).

## ASLA

- Mainnet'e `origin/main` ucu deploy etmek.
- `.env`'i testnet'ten kopyalamak (anahtarlar ayri).
- Golge modu olmadan yeni parametre; kill-switch secret'siz mainnet.
- Tek oturumda iki halkayi birden degistirmek.
- **AlgoPro takipcisini mainnet'e almak** — kod fail-fast reddeder
  ([[20-kararlar/D20-takipci-halkasi]]).

ILGILI: [[40-isletme/deploy-ve-geri-alma]] · [[40-isletme/halka-yonetimi]] · [[10-mimari/guvenlik-kilitleri]]
