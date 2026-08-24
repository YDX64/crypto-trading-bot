---
tags: [isletme, halka, cakisma-yasagi, kritik]
guncelleme: 2026-08-24
kaynak: docs/RUNBOOK.md (halka tablosu, container bolumu), docs/DECISIONS.md D20b/D25/D26
---
# Halka yonetimi — hangisi ne zaman, CAKISMA YASAGI

## ⛔⛔ EN ONEMLI KURAL

**AYNI Binance hesabinda AYNI ANDA IKI YONETICI CALISAMAZ.**
Sonuc: cift SL/TP, yarisan devralma, `state/*.json`'da son-yazan-kazanir,
"yetim" sanilan canli pozisyonlarin sahiplenilmesi.

Bu kural **dort** cakisma sinifini kapsar:
1. supervisord `tradingbot_v2` **+** container ([[20-kararlar/D25-container-yolu]])
2. Ayri takipci halkasi (`tradingbot_ap`) **+** gomulu takipci
   ([[20-kararlar/D20b-gomulu-takipci]])
3. Golge halkasi **+** canli halka ([[20-kararlar/D26-golge-halkasi]])
4. Scalper **+** orchestrator ayni sembolde
   (`src/trading/symbol_reservations.py:21` engeller)

## Halka tablosu

| Halka | `--ring` | Dizin | Program | Port | Durum |
|---|---|---|---|---|---|
| Scalper (TESTNET) | *(vars.)* `testnet` | `/opt/tradingbot-v2` | `tradingbot_v2` | 9091 | **AKTIF** |
| Mainnet | `mainnet` | `/opt/tradingbot-main` | `tradingbot_main` | 9092 | pipeline hazir, **dizin/program YOK** |
| AlgoPro takipci (TESTNET) | `follower` | `/opt/tradingbot-ap` | `tradingbot_ap` | 9093 | AKTIF (D20) |
| Golge (olcum) | — | `/opt/tradingbot-shadow` | `tradingbot_shadow` | 9092 | olcum icin (D26) |

> ⚠️ Mainnet ve golge halkasi ikisi de **9092** olarak belgelenmis; ikisi
> ayni anda kurulacaksa port catismasi kontrol edilmelidir —
> **kodda dogrulanamadi** (port supervisord conf'unda, repoda degil).

## Gomulu takipciyi acmadan once (SIRA ZORUNLU)

1. **Ayri halkayi DUZLESTIR** (acik pozisyon kalmasin).
2. **DURDUR** (`supervisorctl stop tradingbot_ap`) — `STOPPED` gormeden devam
   etme.
3. **Ana bottaki kopruyu BOSALT** (`FOLLOWER_FORWARD_URL`) — dolu kalirsa
   startup CRITICAL uyarir.
4. `FOLLOWER_EMBEDDED=true` + korumali restart.

## Gomulu takipciyi KAPATMA (SIRA ONEMLI)

1. AP pozisyonlarini **KAPAT** (bayrak hala ACIKKEN).
2. Defterde acik AP satiri **KALMADIGINI DOGRULA**.
3. Bayragi kapat + korumali restart.

## Container'a tasima

⛔ **`autostart=false` YAPMADAN GECME.** `supervisorctl stop` yalniz SU ANI
durdurur; `autostart=true` ise **sunucu yeniden baslayinca motor geri gelir**
ve hedef makinedeki container `restart: unless-stopped` ile zaten ayaktadir →
**IKI MOTOR, AYNI HESAP, kimse fark etmeden.**

**Olculdu (2026-08-24, awa):** conf dosyasi `tradingbot-v2.conf` (tire!) ve
icinde `autostart=true` + `autorestart=true` var — bu adim **teorik degil**.
Sunucu saat dilimi **Europe/Stockholm (UTC+2)**, container `TZ=UTC` sabitler →
ayni `logs/bot.log` icinde iki damga olcegi olusur.

⛔ Ciplak `docker compose up` **KULLANMAYIN** → `scripts/docker_run.sh`.
⛔ `docker compose down -v` **KULLANMAYIN**.

## Golge halkasi sinirlari

- TradingView webhook'larini **ALMAZ** → karsilastirma yalniz **C tarayici
  yolunu** kapsar (~%70).
- Golge modunda orchestrator **HIC baslatilmaz** (`src/main.py:371`).

ILGILI: [[40-isletme/deploy-ve-geri-alma]] · [[10-mimari/takipci-algopro]] · [[20-kararlar/D26-golge-halkasi]] · [[90-ai-icin/sik-yapilan-hatalar]]
