---
tags: [karar, aktif, follower, algopro]
guncelleme: 2026-08-24
kaynak: docs/DECISIONS.md D20 (satir 669), src/strategies/follower/engine.py
---
# D20 — AlgoPro takipci halkasi (`BOT_MODE=follower`) · AKTIF · kanit: **YOK**

**Karar.** Ikinci ve BAGIMSIZ bir testnet sistemi: ayni kod tabani,
`BOT_MODE=follower` ile ayri surec (`/opt/tradingbot-ap`, supervisord
`tradingbot_ap`, :9093), ayri `.env`/DB/state/log ve **ayri Binance testnet
hesabi**. Scanner, strateji C ve TV saglamasi **KAPALI**; giris/cikis yalniz
AlgoPro V1.6 alarmlarindan.

- **Giris** `BUY`/`SELL` → MARKET · **Cikis** `EXIT`/ters sinyal → reduce-only
  MARKET; `FOLLOWER_FLIP=true` ise ters sinyalde kapat + yeni yone gir.
- **Seviyeler**: AlgoPro mesajindaki `SL/TP1/TP2/TP3` **birincil**; yedek
  `k×ATR` + RR katlari (0.5/1.0/1.5) — yedege dusmek WARNING loglar.
- **3 parca cikis**, TP1 dogrulaninca SL ucret-farkinda BE'ye.
  **Chandelier YOK** — kosucuyu AlgoPro yonetir.
- **Boyutlama (kullanici karari):** marj = bakiyenin `%FOLLOWER_MARGIN_PCT`'i
  (vars. %10); `lev = clamp(round(FOLLOWER_SL_ROI_TARGET / sl_pct), 3, 100)` →
  stop daima marjin ~%30'u. Zorunlu kapilar yalniz DUSURUR: borsa kaldirac
  dilimi (**okunamazsa giris YOK**), `lev × sl_pct ≤ 50`,
  `1/lev − mmr > 2 × sl_pct/100`, 3 parcaya bolunemeyen pozisyon ACILMAZ.

**Yeniden kullanilanlar (yeniden YAZILMADI):** `ImprovedBinanceClient`,
`PositionManager`, `ScalpTracker`/`scalp_trades`, kapanis dogrulama merdiveni,
`_confirmed_algo_fill`, `fee_aware_breakeven_price`, `/risk-event` kanali.

**Kanit.** **YOK** — testnet olcumu kanit olacak.
**Durum.** AKTIF (2026-08-23) ama [[20-kararlar/D20b-gomulu-takipci]]
**tercih edilen kurulumdur**.
**Geri alma.** Halkayi duzlestir → `supervisorctl stop tradingbot_ap` →
ana bottaki `FOLLOWER_FORWARD_URL`'i bosalt.
**Mainnet.** CIKAMAZ — kural kodda zorlanir (`src/core/config.py:1275`).

**Nerede.** `src/strategies/follower/engine.py:79`,
kopru `src/services/follower_forwarder.py:191`,
uc `src/main.py:2430`.

ILGILI: [[10-mimari/takipci-algopro]] · [[20-kararlar/D20a-takipci-duzeltmeleri]] · [[40-isletme/halka-yonetimi]]
