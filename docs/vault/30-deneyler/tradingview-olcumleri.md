---
tags: [deneyler, tradingview, luxalgo, algopro]
guncelleme: 2026-08-24
kaynak: docs/EXPERIMENTS.md "TradingView / LuxAlgo olcumleri" (satir 102)
---
# TradingView / LuxAlgo olcumleri (TV Desktop MCP, 5m, ~1 ay, komisyonsuz)

## Backtester PF'leri (varsayilan ayarlar)

| Paket | Sonuclar |
|---|---|
| OSC | ETH **2.48** · BTC 1.89 · XRP 2.20 |
| S&O | ETH 2.30 · XRP 2.25 · BTC 1.91 · SOL 1.78 · **BNB 0.91** |
| PAC | XRP 7.86 · ETH 2.01 |

→ ETH/XRP/BTC uc pakette de pozitif, LTC ucunde de negatif → D7 sembol
allowlist'i ([[20-kararlar/D7-tv-sembol-allowlist]]).

## Reddedilen fikirler

- **13 LUCID script'i** (Discord + LuxAlgo AI): en iyi D6-BNB 2.26, S3-ETH
  1.73 — **hicbiri yalin toolkit'i (2.2-2.5) gecemedi**.
- **Danny ETH-15m LUCID recetesi**: ort PF 1.12 (yalin S&O 1.60) — coklu onay
  + sabit TP/trailing SL kriptoda **zarar**.

## AlgoPro V1.6 denetimi

- **Repaint YOK** (dogrulandi).
- Panel **TP1 kazanma ortalamasi %40.5** — RR 1'de basabas **%50**.
  → "kaldiracli TP1" fikri **reddedildi** (beklenti −0.19R).
- Trade kutusu tek slot.

## ⚠️ Arac tuzagi

`indicator_set_inputs` (TradingView MCP) **LuxAlgo script'lerini bozar** —
CLAUDE.md yasak #4. TV Desktop script'leri V1.6 input degisikligine dayanmaz;
kurtarma = davetli listeden taze ekleme.

## Takvim

- **2026-09-14:** 49 TV alarmi expire oluyor → yenile; ayni anda webhook
  secret rotasyonu + alan adi/TLS isiyle birlestir.
- **2026-11-21:** LuxAlgo aboneligi iptal goruniyor — kullanici karari.

ILGILI: [[10-mimari/tv-sinyal-yolu]] · [[10-mimari/takipci-algopro]] · [[40-isletme/gunluk-kontrol]]
