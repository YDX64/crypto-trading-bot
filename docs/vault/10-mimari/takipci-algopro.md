---
tags: [mimari, follower, algopro, d20]
guncelleme: 2026-08-24
kaynak: src/strategies/follower/, src/services/follower_forwarder.py, docs/DECISIONS.md D20/D20a/D20b, docs/RUNBOOK.md
---

# AlgoPro takipcisi — iki kurulum, bir motor

## NE

Scalper'dan **tamamen ayri** bir strateji: scanner yok, strateji C yok, TV
saglamasi yok. Giris ve cikis **yalnizca AlgoPro V1.6 alarmlarindan** gelir.
Iki kurulumu vardir; **tercih edilen GOMULU moddur**.

| Kurulum | Bayrak | Surec | Hesap | Defter |
|---|---|---|---|---|
| Ayri halka (D20) | `BOT_MODE=follower` | `/opt/tradingbot-ap`, `tradingbot_ap`, :9093 | AYRI testnet hesabi | `tradingbot_ap.db` |
| **Gomulu (D20b — TERCIH EDILEN)** | `FOLLOWER_EMBEDDED=true` | scalper ile AYNI surec (:9091) | AYNI hesap | ayni `tradingbot.db`, `strategy="AP"` |

Gomulu modda boyutlama **gercek bakiyeye degil 1000 USD'lik SANAL deftere**
dayanir (`FOLLOWER_VIRTUAL_CAPITAL_USDT`).

## NEREDE

| Ne | Yer |
|---|---|
| Motor | `src/strategies/follower/engine.py:79` |
| Olay isleme | `src/strategies/follower/engine.py:1035` |
| Giris | `src/strategies/follower/engine.py:1113` |
| Cikis | `src/strategies/follower/engine.py:1554` |
| Yetim pozisyon denetimi | `src/strategies/follower/engine.py:550` |
| Alarm ayristirma (saf) | `src/strategies/follower/parser.py:461` |
| SL/TP cozumu (saf) | `src/strategies/follower/levels.py:135` |
| Marj/kaldirac/miktar (saf) | `src/strategies/follower/plan.py:236` |
| Kaldirac dilimi onbellegi | `src/strategies/follower/brackets.py:30` |
| Korumali acilis | `src/strategies/follower/executor.py:82` |
| Cikis yoneticisi | `src/strategies/follower/exits.py:58` |
| Risk-event halt kopyasi | `src/strategies/follower/risk_halt.py` |
| HTTP koprusu (ayri halka) | `src/services/follower_forwarder.py:191` |
| `/follower/event` · `/follower/status` | `src/main.py:2430` · `src/main.py:2496` |
| Gomulu surec-ici teslim | `src/main.py:1277` |
| Lifespan dallanmasi | `src/main.py:320` (ayri halka) · `src/main.py:390` (gomulu) |

## NASIL CALISIR

- **Giris:** `BUY`/`SELL` → MARKET (1m sinyalde maker beklemek sinyali kacirir).
- **Cikis:** `EXIT` ya da ters sinyal → kalan miktar reduce-only MARKET;
  `FOLLOWER_FLIP=true` ise ters sinyalde kapat + yeni yone gir.
- **Seviyeler:** AlgoPro mesajindaki `SL/TP1/TP2/TP3` **birincil kaynaktir**;
  mesajda seviye yoksa `k×ATR` + RR katlari yedegi kullanilir (WARNING loglar).
- **3 parca cikis:** TP1/TP2/TP3 reduce-only `TAKE_PROFIT_MARKET`, 1/3'er;
  TP1 dogrulaninca SL ucret-farkinda break-even'e cekilir.
  **Chandelier trailing YOKTUR** — kosucuyu AlgoPro yonetir.
- **Boyutlama:** marj = bakiyenin `%FOLLOWER_MARGIN_PCT`'i;
  `lev = clamp(round(FOLLOWER_SL_ROI_TARGET / sl_pct), LEV_MIN, LEV_MAX)` →
  stop daima marjin ~%30'u. Ustune yalniz DUSUREN kapilar: borsa kaldirac
  dilimi (okunamazsa **giris YOK**), `lev × sl_pct ≤ 50`,
  `1/lev − mmr > 2 × sl_pct/100`, borsa filtreleri.

## TUZAKLAR

- ⛔ **Ayri halka ile gomulu mod AYNI ANDA CALISAMAZ.** Gomulu acildigi an
  AlgoPro govdeleri surec icinde tuketilir; ayri halka tek alarm almaz ve
  ACIK pozisyonlari yetim kalir. Once ayri halkayi duzlestir + durdur + kopruyu
  bosalt ([[40-isletme/halka-yonetimi]]).
- ⛔ **Takipci MAINNET'e CIKAMAZ.** Kural kodda zorlanir
  (`src/core/config.py:1275`): takipci aktifken testnet-olmayan
  `BINANCE_BASE_URL` startup'ta `ValueError`.
- **Gomulu modda "yetim = entry-halt" KOSULLUDUR** (D20b): hesap paylasildigi
  icin hicbir motorun rezerve etmedigi bir pozisyon MESRU olabilir (elle
  acilmis) → WARNING + sayac. Takipcinin KENDI rezervasyonunu tasiyan yetim
  ise D20a davranisini korur: CRITICAL + kalici entry-halt.
- **Kapasite tavani gomulu modda motor-BASINADIR** (D20a'da hesap geneliydi).
- **Ucret esigi kapisi varsayilan ACIK**: `FOLLOWER_MIN_TP1_FEE_RATIO=1.0`
  (stop ≥ ~%0.20 olmayan sinyal alinmaz).
- **`FOLLOWER_SYMBOLS` scalper evreninden otomatik dusulur** — iki motor ayni
  sembolu yonetmez.
- **Kanit YOK.** Bu halkanin olculmus bir kenari yoktur; testnet defteri kanit
  olacaktir. `strategy="AP"` satirlarini scalper istatistigine karistirma.
- **D20/D20a/D20b celiskisinde SIRA: D20b > D20a > D20.**

## ILGILI

[[10-mimari/tv-sinyal-yolu]] · [[20-kararlar/D20-takipci-halkasi]] ·
[[20-kararlar/D20a-takipci-duzeltmeleri]] · [[20-kararlar/D20b-gomulu-takipci]] ·
[[40-isletme/halka-yonetimi]]
