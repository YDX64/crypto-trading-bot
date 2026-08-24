---
tags: [karar, aktif, kapi, market-gate]
guncelleme: 2026-08-24
kaynak: docs/DECISIONS.md D15 (satir 365), docs/EXPERIMENTS.md E7, src/strategies/scalper/market_gate.py
---
# D15 — Lider piyasa kapisi ("ters-gun kapisi") · AKTIF (testnet)

**Karar.** `SCALPER_MARKET_GATE=true` (kod varsayilani **false**, sunucu
`.env` ile acik), lider `BTCUSDT`, `_DAY_PCT=1.3`, `_RUN_PCT=0` (kapali).
Iki bagimsiz alt-kapi:
1. **gun-ici**: lider son kapanisi gun acilisinin ≥%X ALTINDAYSA yeni LONG,
   ≥%X USTUNDEYSE yeni SHORT acilmaz.
2. **uzama** (`_RUN_PCT`/`_RUN_DAYS`): **KULLANILMAMALI** — iki bagimsiz
   olcum (E7 harness + E8 canli defter) desteklemedi; acilirsa motor
   startup'ta WARNING basar.

**Rejim kapisindan farki.** D5 sembolun KENDI EMA50/200 trendine bakar;
bu kapi yalniz **lidere** bakip karari tum evrene uygular.

**Kanit (E7, P2 hukmu).** V1c (%1.3): AYI PF **1.43** ✓ · AYI+YATAY birlikte ↑
(+3228 / +399) ✓ · BOGA kaybi **−%2.7** ✓ → **GECTI, uc pencerede V1'i
domine ediyor**. Uzama kolu (V2a %10/3g) BOGA −%24.6 ile **KALDI**.
**Uyari.** Kapinin katkisi **engelleme + yeniden tahsis** toplamidir ve
ayristirma esige duyarlidir; `scripts/decompose_gate_runs.py` bunu ayirir.

**Durum.** AKTIF (2026-08-23 11:14 UTC). `gate_effective=true`,
`day_open_source=intraday_open`. Yedek `backups/env.bak-20260823-1311*-marketgate`.
**Geri alma.** RUNBOOK "Lider piyasa kapisi" kapatma komutu
(`SCALPER_MARKET_GATE=false` + `scripts/restart_safe.sh testnet`).

## Bilinmesi zorunlu iki incelik
- **Fail-OPEN'dir ve GORUNUR olmali.** Lider verisi gelmezse kapi UYGULANMAZ.
  `enabled` bunu SOYLEMEZ; `gate_effective` **bes sarti** birden ister:
  enabled + lider dogrulandi + en az bir BASARILI goruntu + goruntu BAYAT degil
  + en az bir esik > 0.
- **REST alt siniri 0 → ~3 agirlik/dk'ya CIKAR.** Kapi kapaliyken tek istek
  bile gitmez; acikken her tarama turu basinda goruntu tazelenir.
  ("maliyet degismez" ifadesi YANLISTI — 2026-08-23 inceleme bulgusu.)

**Nerede.** Saf kural `src/strategies/scalper/market_gate.py:185`;
gun acilisi `src/strategies/scalper/market_gate.py:160`;
motor `src/strategies/scalper/engine.py:1961`; durum
`src/strategies/scalper/engine.py:4541`. Harness AYNI fonksiyon nesnesini
cagirir ([[20-kararlar/P1-harness-parite]]).

ILGILI: [[10-mimari/motor-scalper]] · [[30-deneyler/E7-lider-kapisi]] · [[30-deneyler/E8-sinyal-otopsisi]] · [[20-kararlar/D26-golge-halkasi]]
