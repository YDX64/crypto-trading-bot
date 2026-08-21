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

## 2026-08-21 — Altın (golden) backtest regresyonu (offline, deterministik)

Amaç: `src/strategies/scalper/backtest.py` motorunu (simulate_symbol →
open_position → manage_position) AĞSIZ ve sabit bir sonuca kilitlemek —
`tests/test_golden_backtest.py`, davranış değişince (kasıtlı ya da kaza)
kırılır. Kod: `src/strategies/scalper/kline_cache.py` (yeni, `--cache-dir`/
`--refresh` CLI bayrakları backtest.py'ye eklendi) + `tests/fixtures/klines/`
(6 dosya, ~104 KB, gzip JSON, BTCUSDT+ETHUSDT × 5m/15m).

Fixture penceresi: 2026-08-07→2026-08-10 UTC (3 gün, `[start,end)`).
Yalnız 5m (giriş) ve 15m (bağlam VE rejim) serileri gerekir — golden
config `SCALPER_TF_REGIME=15m` kullandığından rejim serisi de "15m"
anahtarını paylaşır (bkz. `gather_symbol_data`'nın aralık-adıyla
anahtarlanan sözlüğü); 1m HİÇ kullanılmaz (dolum `candles_5m[idx].open`
ile yapılır, tik-bazlı değil).

Sabit ayarlar (`tests/test_golden_backtest.py::_GoldenCfg` — .env OKUNMAZ,
`src.core.config.settings` tekil nesnesi monkeypatch ile ayrıca sabitlenir
çünkü StrategyC bunu cfg'den değil doğrudan global'den okur):
strategies=C · `SCALPER_STOP_MODE=fixed_roi` · `SCALPER_FIXED_STOP_ROI_PCT=50`
· `SCALPER_DYNAMIC_LEVERAGE=true` (3-20) · `SCALPER_CHANDELIER_ATR_MULT=3.5`
· `SCALPER_TP1_ROI=10` `SCALPER_TP2_ROI=25` `SCALPER_TP2_FRACTION=0.20`
· `SCALPER_REGIME_FILTER=true` · `SCALPER_TF_REGIME=15m` ·
`SCALPER_C_RSI_LONG_MAX=30` `SCALPER_C_RSI_SHORT_MIN=70` ·
`SCALPER_C_REQUIRE_DIVERGENCE=true` · `SCALPER_LOSS_COOLDOWN_MINUTES=60` ·
`SCALPER_STOP_ATR_FLOOR_MULT=0.5` (yalnız kayıt — fixed_roi modunda inert)
· `SCALPER_MAX_HOLD_HOURS=8` (yalnız kayıt — backtest harness'i max-hold
hiç UYGULAMAZ, yalnız canlı engine.py'de var) · sembol BTCUSDT,ETHUSDT.

**Bulgu (raporlanmalı, gerçek `.env` ile doğrulanmalı):** `SCALPER_MIN_RR`
sınıf varsayılanı (1.2) bu kombinasyonla (tp1=10/tp2=25/tp2_frac=0.20,
fixed_roi=50) KULLANILAMAZ — `open_position()`'ın RR kapısı `fixed_roi`
modunda `sl_risk_roi`'yi HER ZAMAN tam `SCALPER_FIXED_STOP_ROI_PCT`'e
sadeler (kaldıraç iptal olur), yani `rr = (10×0.8+25×0.20) / 50 = 13/50 =
0.26` — piyasa verisinden bağımsız SABİT bir değer. 1.2 eşiğiyle HİÇBİR
C sinyali (ne backtest'te ne canlıda) asla geçemez; bu CLAUDE.md'nin
belgelediği canlı işlem geçmişiyle (başabaş WR ≈%85) çelişir. Golden test
`SCALPER_MIN_RR=0.0` (kapı kapalı) varsayımıyla yazıldı — gerçek sunucu
`.env`'i kontrol edilip ya bu not güncellenmeli ya da `docs/DECISIONS.md`'ye
girilmeli.

**Altın değerler** (ETHUSDT bu dar 3 günlük pencerede hiç ham sinyal
üretmedi — RSI 30/70 + BB taşması + zorunlu diverjans üçlüsü hiç üst üste
binmedi; hata DEĞİL, gerçek piyasa verisi):

| Metrik | Değer |
|---|---|
| Toplam işlem | 2 (ikisi de BTCUSDT) |
| Net PnL | +26.77 |
| Yön | LONG 2 |
| Çıkış nedeni | TRAIL 2 |
| Kapı reddi | `regime_gate` 4 (rejime ters C sinyali) |
| Koşum süresi | ~2 sn (3 test, AĞSIZ) |

Doğrulama: `python3 -m pytest tests/test_golden_backtest.py -q` iki ayrı
süreçte birebir aynı sonucu verdi (determinism testi ayrıca fingerprint
karşılaştırır). Harness'te bulunan/gerekli nondeterminizm düzeltmesi:
YOK — `simulate_symbol` zincirinde (`build_context`, `indicators.py`,
`regime.py`) `time.time()`/`datetime.now()`/rastgelelik/sıralamaya-duyarlı
set yok; tek zaman kaynağı `data.py`'nin `KlineFetcher._drop_unclosed`'ı,
golden test ona hiç dokunmuyor (fixture zaten kapanmış mumlar).

## Eski (kapı ÖNCESİ harness — yalnız yön bilgisi için; mutlak sayılar geçersiz)
2026-08-19/21 sweep'leri: chandelier 2.5→3.5 (+), TP1 15 (−), TP2 40 (nötr), runner %40 (+), strateji D (−), 50x (−−).
