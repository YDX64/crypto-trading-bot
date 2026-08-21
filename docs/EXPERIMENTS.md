# Deney defteri — backtest ve ölçüm kayıtları

Kural: bir satırın kanıt sayılması için **komut + pencere + env kaynağı + log yolu** gerekir.
Harness ≥ 7640c0a (kapı-pariteli). Pencereler: AYI 2026-01-23→02-13 · YATAY 07-01→07-21 · BOĞA 08-07→08-21.
Komut kalıbı:
```bash
env $(ssh awa grep ^SCALPER_ /opt/tradingbot-v2/.env | xargs) <VARYANT=deger> python3 -m src.strategies.scalper.backtest \
  --strategies C --symbols BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,BNBUSDT,ADAUSDT,LTCUSDT --start <YYYY-MM-DD> --end <YYYY-MM-DD>
```
Sıralı koş (paralel = 429). ~4 dk/koşu (8 sembol, 3 hafta).

## 2026-08-21 — rejim referansları (baz = canlı env, divergence=false iken)
| Pencere | İşlem | WR | PnL | PF | maxDD | LONG (n/PnL/PF) | SHORT (n/PnL/PF) | SL n/ort | TRAIL n/ort |
|---|---|---|---|---|---|---|---|---|---|
| AYI kapı açık | 814 | 84.6 | −2042 | 0.97 | 11857 | 259/−3732/0.83 | 555/+1689/1.04 | 120/−514 | 687/+88 |
| AYI kapı KAPALI | 1171 | 80.8 | −36506 | 0.68 | 41691 | 636/−37814/0.49 | 535/+1308/1.03 | 220/−514 | 944/+82 |
| YATAY | 449 | 84.4 | −2289 | 0.93 | 8254 | 257/+3047/1.19 | 192/−5336/0.71 | 67/−514 | 375/+86 |
| BOĞA | 191 | 88.0 | +2798 | 1.24 | 3610 | 101/+3182/1.69 | 90/−385/0.95 | 23/−514 | 168/+87 |
Loglar: oturum scratchpad `W_BEAR_gate_on.log`, `W_BEAR_gate_off.log`, `W_FLAT.log`, `W_BULL.log`; özet `regime_backtest.md`.
Okuma: başabaş WR ≈ %85.4; kapı ayıda ~34.5k kurtarıyor; kayıp daima ters-trend tarafta.

## 2026-08-21 — E2/E3 varyantları (tek değişiklik, 3 pencere)
| Varyant | AYI PF/PnL | BOĞA PF/PnL | YATAY PF/PnL | Karar |
|---|---|---|---|---|
| E2a `SCALPER_C_REQUIRE_DIVERGENCE=true` | 1.06 / +886 | 2.18 / +3831 | 1.33 / +2745 | ✅ CANLI (D6) |
| E2b `SCALPER_C_REQUIRE_FLOW_CONFIRM=true` | 1.19 / +2933 | 1.50 / +2610 | 0.81 / −2996 | aday → E2ab ile test edildi, reddedildi |
| E2ab divergence+flow_confirm | 3.35 / +1498 (31 işlem) | 0.85 / −243 (22) | 0.78 / −692 (34) | ❌ aşırı filtreleme (loglar E2ab_*.log) |
| E2c `SCALPER_C_REQUIRE_REVERSAL_ZONE=true` | 0.68 / −5596 | 2.03 / +2636 | 0.75 / −2916 | ❌ |
| E2d RSI 25/75 | 0.96 / −1493 | 1.34 / +2613 | 0.85 / −4478 | ❌ |
| E3a `SCALPER_FIXED_STOP_ROI_PCT=30` | 0.91 / −6108 | 1.28 / +4092 | 0.91 / −3894 | ❌ SL 120→224 |
| E3b `SCALPER_TF_REGIME=4h` | 1.02 / +1227 | 1.00 / −63 | 0.79 / −9090 | ❌ |
| E3c `SCALPER_C_ALLOWED_REGIMES=DOWN,UP` | 1.03 / +1263 | 1.24 / +2567 | 0.79 / −7170 | ❌ |
| E3d E3a+E3b | 0.93 / −4148 | 1.04 / +702 | 0.81 / −10346 | ❌ |
Loglar: `E<id>_<pencere>.log` (24 dosya), özet `regime_experiments.md`. E2a detay: işlem 216/96/150, maxDD 3574/735/3181.

## 2026-08-21 — canlı defter rejim analizi (7-21 Ağu, testnet)
BTC günlük rejim (UP>+1.5%, DOWN<−1.5%): UP 4 gün LONG 76 işlem %89 +652 / SHORT 13 %23 −88;
FLAT 11 gün LONG 40 %52 +90 / SHORT 42 %50 +179 (+268'i tek günden); DOWN 0 gün.
7-20 Ağu = son 240 günün en iyi 14 günü (+%13.5). Kalıp: `scalp_trades` × Binance 1d klines.

## 2026-08-21 — TradingView / LuxAlgo ölçümleri (TV Desktop MCP, 5m, son ~1 ay, komisyonsuz)
- Varsayılan backtester PF: OSC ETH 2.48 · BTC 1.89 · XRP 2.20 | S&O ETH 2.30 · XRP 2.25 · BTC 1.91 · SOL 1.78 · BNB 0.91 | PAC XRP 7.86 · ETH 2.01.
- S&O sensitivity haritası (dashboard): ETH/XRP 16/16 pozitif; optimum sembol+TF'ye göre kayar (BTC 15m→10, 5m→13).
- 13 LUCID script'i (Discord + LuxAlgo AI): en iyi D6-BNB 2.26, S3-ETH 1.73, D1-ETH 1.53 — hiçbiri yalın toolkit'i geçmedi. Dosyalar: `lucid_bench_results.md`, `discord_bench_results.md`.
- AlgoPro V1.6: repaint yok; panel TP1 kazanma ort %40.5 (RR 1). `algopro_v16_audit.md`.

## Eski (kapı ÖNCESİ harness — yalnız yön bilgisi için; mutlak sayılar geçersiz)
2026-08-19/21 sweep'leri: chandelier 2.5→3.5 (+), TP1 15 (−), TP2 40 (nötr), runner %40 (+), strateji D (−), 50x (−−).
