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

## 2026-08-21 — Autoresearch (scripts/autoresearch.py)
Kaynak: `python3 scripts/autoresearch.py` — otomatik uretildi, elle duzenlemeyin.
| Varyant | Pencere | Islem | WR% | PnL | PF | maxDD | Karar |
|---|---|---|---|---|---|---|---|
| E4a | AYI | 345 | 83.2 | -5960.31 | 0.79 | 8752.80 | - |
| E4a | YATAY | 237 | 86.1 | +2009.74 | 1.13 | 2584.77 | - |
| E4a | BOGA | 119 | 90.8 | +4523.75 | 1.85 | 1448.26 | - |
| E4a | KARAR | - | - | -6888.98 | - | - | REDDEDILDI (AYI PF 0.79<1.1 ve AYI/YATAY birlikte iyilesmedi) |
| E4a | hipotez | Gevsek RSI esigi (35/65) daha fazla ve daha erken sinyal yakalar; asiri filtrelemeyi hafifletir. | | | | | |
| E4b | AYI | 216 | 85.6 | +1134.34 | 1.07 | 3604.81 | - |
| E4b | YATAY | 154 | 87.0 | +2891.65 | 1.35 | 3169.06 | - |
| E4b | BOGA | 95 | 92.6 | +3867.42 | 2.20 | 724.00 | - |
| E4b | KARAR | - | - | +431.26 | - | - | ADAY (AYI&YATAY PnL birlikte iyilesti) |
| E4b | hipotez | Chandelier carpanini 3.0'a daraltmak stop'u sikilastirir, trail kazancini erken kilitler. | | | | | |
| E4c | AYI | 216 | 85.6 | +640.40 | 1.04 | 3743.00 | - |
| E4c | YATAY | 150 | 86.7 | +2811.15 | 1.34 | 3105.95 | - |
| E4c | BOGA | 95 | 92.6 | +4317.10 | 2.33 | 720.97 | - |
| E4c | KARAR | - | - | +306.49 | - | - | REDDEDILDI (AYI PF 1.04<1.1 ve AYI/YATAY birlikte iyilesmedi) |
| E4c | hipotez | Chandelier carpanini 4.0'a genisletmek trail'e daha fazla alan birakir, erken cikislari azaltir. | | | | | |
| E4d | AYI | 216 | 85.6 | +951.37 | 1.06 | 3728.66 | - |
| E4d | YATAY | 150 | 86.7 | +2717.42 | 1.33 | 3165.33 | - |
| E4d | BOGA | 96 | 92.7 | +3830.44 | 2.18 | 734.59 | - |
| E4d | KARAR | - | - | +37.08 | - | - | REDDEDILDI (AYI PF 1.06<1.1 ve AYI/YATAY birlikte iyilesmedi) |
| E4d | hipotez | TP2 hedefini 20'ye dusurmek runner payini daha sik gerceklestirir. | | | | | |
| E4e | AYI | 216 | 85.6 | +884.75 | 1.06 | 3549.39 | - |
| E4e | YATAY | 150 | 86.7 | +2776.61 | 1.33 | 3171.59 | - |
| E4e | BOGA | 96 | 92.7 | +3845.53 | 2.19 | 734.59 | - |
| E4e | KARAR | - | - | +44.74 | - | - | REDDEDILDI (AYI PF 1.06<1.1 ve AYI/YATAY birlikte iyilesmedi) |
| E4e | hipotez | TP2 hedefini 30'a yukseltmek runner'a daha fazla alan birakir. | | | | | |
| E4f | AYI | 229 | 89.1 | +1572.66 | 1.13 | 2604.45 | - |
| E4f | YATAY | 162 | 89.5 | +2744.44 | 1.40 | 2153.57 | - |
| E4f | BOGA | 104 | 95.2 | +4203.43 | 2.90 | 514.25 | - |
| E4f | KARAR | - | - | +1058.37 | - | - | ADAY (AYI PF=1.13) |
| E4f | hipotez | TP1 hedefini 8'e dusurmek break-even'e daha erken gecer, whipsaw'da SL'den korur. | | | | | |
| E4g | AYI | 212 | 84.0 | +1909.73 | 1.11 | 4106.57 | - |
| E4g | YATAY | 140 | 85.0 | +3578.56 | 1.39 | 2468.54 | - |
| E4g | BOGA | 85 | 88.2 | +3687.24 | 1.77 | 1247.03 | - |
| E4g | KARAR | - | - | +1713.37 | - | - | ADAY (AYI PF=1.11) |
| E4g | hipotez | TP1 hedefini 12'ye yukseltmek erken kismi kar alimini geciktirir, runner payini buyutur. | | | | | |
| E4h | AYI | 216 | 85.6 | +885.60 | 1.06 | 3574.40 | - |
| E4h | YATAY | 150 | 86.7 | +2745.14 | 1.33 | 3180.54 | - |
| E4h | BOGA | 96 | 92.7 | +3831.41 | 2.18 | 734.59 | - |
| E4h | KARAR | - | - | +0.00 | - | - | REDDEDILDI (AYI PF 1.06<1.1 ve AYI/YATAY birlikte iyilesmedi) |
| E4h | hipotez | Maksimum pozisyon sayisini 3'e sabitlemek (sunucu farkliysa) slot rekabetini azaltir. | | | | | |
| E4i | AYI | 160 | 88.8 | +5624.12 | 1.68 | 3002.71 | - |
| E4i | YATAY | 84 | 83.3 | +1073.09 | 1.20 | 1421.00 | - |
| E4i | BOGA | 55 | 92.7 | +3552.58 | 3.22 | 590.14 | - |
| E4i | KARAR | - | - | +0.00 | - | - | REDDEDILDI (asiri filtreleme (islem<60: ['BOGA'])) |
| E4i | hipotez | Dinamik kaldirac tavanini 20'den 10'a dusurmek stop mesafesini buyutup gurultu stop'unu azaltir. | | | | | |
| E4j | AYI | 201 | 87.1 | +3292.72 | 1.27 | 3953.33 | - |
| E4j | YATAY | 124 | 84.7 | +1185.88 | 1.14 | 1883.94 | - |
| E4j | BOGA | 68 | 89.7 | +2353.05 | 1.74 | 1057.14 | - |
| E4j | KARAR | - | - | -630.52 | - | - | REDDEDILDI (BOGA PnL kaybi >%20) |
| E4j | hipotez | Dinamik kaldirac tavanini 20'den 15'e dusurmek orta yol olarak stop mesafesini biraz buyutur. | | | | | |
| E4k | AYI | 190 | 85.8 | +855.43 | 1.07 | 3696.49 | - |
| E4k | YATAY | 137 | 85.4 | +1600.60 | 1.19 | 3482.42 | - |
| E4k | BOGA | 87 | 93.1 | +3430.98 | 2.11 | 734.59 | - |
| E4k | KARAR | - | - | -1575.14 | - | - | REDDEDILDI (AYI PF 1.07<1.1 ve AYI/YATAY birlikte iyilesmedi) |
| E4k | hipotez | Baglam TF'sini 15m'de sabitlemek (sunucu farkliysa) sinyal baglamini rejim TF'sinden ayristirir. | | | | | |
| E5a | AYI | 186 | 89.8 | +2937.02 | 1.33 | 3094.20 | - |
| E5a | YATAY | 117 | 87.2 | +1409.71 | 1.24 | 959.67 | - |
| E5a | BOGA | 67 | 89.6 | +1218.40 | 1.39 | 1160.12 | - |
| E5a | KARAR | - | - | -1897.02 | - | - | REDDEDILDI (BOGA PnL kaybi >%20) |
| E5a | hipotez | TP1 %8 + kaldirac tavani 12: erken BE + genis stop — ayi dayanikliligi, boga islem sayisi korunur mu? | | | | | |
| E5b | AYI | 204 | 88.7 | +1406.30 | 1.13 | 4522.34 | - |
| E5b | YATAY | 141 | 88.7 | +2312.85 | 1.36 | 2047.77 | - |
| E5b | BOGA | 87 | 92.0 | +2100.32 | 1.66 | 737.55 | - |
| E5b | KARAR | - | - | -1642.69 | - | - | REDDEDILDI (BOGA PnL kaybi >%20) |
| E5b | hipotez | TP1 %8 + kaldirac tavani 15: E5a'nin daha az kisitli hali. | | | | | |
| E5c | AYI | 177 | 87.6 | +3725.78 | 1.35 | 3582.47 | - |
| E5c | YATAY | 108 | 85.2 | +1771.05 | 1.28 | 1227.05 | - |
| E5c | BOGA | 60 | 86.7 | +1617.87 | 1.44 | 1276.94 | - |
| E5c | KARAR | - | - | -347.45 | - | - | REDDEDILDI (BOGA PnL kaybi >%20) |
| E5c | hipotez | Kaldirac tavani 12 tek basina: E4i (10) ile E4j (15) arasi — boga islem sayisi >=60 kalir mi? | | | | | |
| E5d | AYI | 229 | 89.1 | +1488.87 | 1.12 | 2549.93 | - |
| E5d | YATAY | 163 | 89.6 | +2719.06 | 1.40 | 2127.93 | - |
| E5d | BOGA | 103 | 95.1 | +4239.47 | 2.92 | 514.25 | - |
| E5d | KARAR | - | - | +985.23 | - | - | ADAY (AYI PF=1.12) |
| E5d | hipotez | TP1 %8 + chandelier 3.0: iki bagimsiz adayin birlesimi toplamsal mi? | | | | | |

## 2026-08-22 — Autoresearch (scripts/autoresearch.py)
Kaynak: `python3 scripts/autoresearch.py` — otomatik uretildi, elle duzenlemeyin.
| Varyant | Pencere | Islem | WR% | PnL | PF | maxDD | Karar |
|---|---|---|---|---|---|---|---|
| E4b | AYI | 213 | 85.4 | +823.15 | 1.05 | 3717.82 | - |
| E4b | YATAY | 149 | 87.2 | +2519.27 | 1.30 | 3223.76 | - |
| E4b | BOGA | 90 | 93.3 | +3984.39 | 2.46 | 724.00 | - |
| E4b | KARAR | - | - | +448.48 | - | - | ADAY (AYI&YATAY PnL birlikte iyilesti) |
| E4b | hipotez | Chandelier carpanini 3.0'a daraltmak stop'u sikilastirir, trail kazancini erken kilitler. | | | | | |
| E4f | AYI | 226 | 88.9 | +1415.12 | 1.12 | 2604.45 | - |
| E4f | YATAY | 159 | 89.9 | +2603.52 | 1.38 | 2153.57 | - |
| E4f | BOGA | 101 | 96.0 | +4597.63 | 3.71 | 514.25 | - |
| E4f | KARAR | - | - | +1737.94 | - | - | ADAY (AYI PF=1.12) |
| E4f | hipotez | TP1 hedefini 8'e dusurmek break-even'e daha erken gecer, whipsaw'da SL'den korur. | | | | | |
| E4g | AYI | 205 | 83.4 | +1175.76 | 1.07 | 4106.57 | - |
| E4g | YATAY | 134 | 85.1 | +3014.92 | 1.33 | 2555.06 | - |
| E4g | BOGA | 78 | 91.0 | +4891.83 | 2.51 | 755.33 | - |
| E4g | KARAR | - | - | +2204.18 | - | - | ADAY (AYI&YATAY PnL birlikte iyilesti) |
| E4g | hipotez | TP1 hedefini 12'ye yukseltmek erken kismi kar alimini geciktirir, runner payini buyutur. | | | | | |
| E5c | AYI | 157 | 86.6 | +2646.69 | 1.26 | 3073.82 | - |
| E5c | YATAY | 96 | 84.4 | +710.47 | 1.11 | 1408.39 | - |
| E5c | BOGA | 52 | 86.5 | +1578.66 | 1.50 | 1510.41 | - |
| E5c | KARAR | - | - | +0.00 | - | - | REDDEDILDI (asiri filtreleme (islem<60: ['BOGA'])) |
| E5c | hipotez | Kaldirac tavani 12 tek basina: E4i (10) ile E4j (15) arasi — boga islem sayisi >=60 kalir mi? | | | | | |

## 2026-08-23 — Autoresearch (scripts/autoresearch.py)
Kaynak: `python3 scripts/autoresearch.py` — otomatik uretildi, elle duzenlemeyin.
| Varyant | Pencere | Islem | WR% | PnL | PF | maxDD | Karar |
|---|---|---|---|---|---|---|---|
| E6a | AYI | 190 | 83.7 | +136.30 | 1.01 | 5051.74 | - |
| E6a | YATAY | 128 | 84.4 | -63.93 | 0.99 | 3324.57 | - |
| E6a | BOGA | 80 | 90.0 | +1944.25 | 1.52 | 1029.05 | - |
| E6a | KARAR | - | - | -4861.70 | - | - | REDDEDILDI (AYI PF 1.01<1.1 ve AYI/YATAY birlikte iyilesmedi; BOGA PnL kaybi >%20) |
| E6a | hipotez | Rejim TF'sini 15m->5m'e hizlandirmak donus gunlerinde DOWN'a saatler degil dakikalar icinde gecer; ters-gun LONG'lari keser (bedel: bogada dip alimlari). | | | | | |
| E6b | AYI | 213 | 85.4 | +292.19 | 1.04 | 1841.30 | - |
| E6b | YATAY | 145 | 86.9 | +1196.14 | 1.29 | 1614.42 | - |
| E6b | BOGA | 90 | 93.3 | +1950.83 | 2.43 | 367.29 | - |
| E6b | KARAR | - | - | -3439.16 | - | - | REDDEDILDI (AYI PF 1.04<1.1 ve AYI/YATAY birlikte iyilesmedi; BOGA PnL kaybi >%20) |
| E6b | hipotez | Marj tavani %10->%5: boyutlama dogrusal oldugundan PF/WR AYNI kalmali, PnL ve maxDD yarilanmali (kanit: risk katmani sinyali degistirmez). | | | | | |
| E6c | AYI | 226 | 88.9 | +707.56 | 1.12 | 1302.23 | - |
| E6c | YATAY | 159 | 89.9 | +1301.76 | 1.38 | 1076.79 | - |
| E6c | BOGA | 101 | 96.0 | +2298.81 | 3.71 | 257.13 | - |
| E6c | KARAR | - | - | -2570.20 | - | - | REDDEDILDI (BOGA PnL kaybi >%20) |
| E6c | hipotez | A paketi birlesik: marj %5 + TP1 %8 (D12). Beklenti: D12'nin PF/DD iyilesmesi + yarim olcek. | | | | | |

**E6 okuması (elle, 2026-08-23):** E6b/E6c'nin "REDDEDİLDİ" hükmü P2'nin "BOĞA PnL kaybı ≤ %20"
maddesinin **ölçek** artefaktıdır — marj tavanı %5 boyutlamayı doğrusal yarılar: E6b'de PF
tabanla birebir aynı (AYI 1.04 / YATAY 1.29 / BOĞA 2.43), maxDD yarı (1841/1614/367 vs
3683/3229/735) → negatif kontrol GEÇTİ (risk katmanı sinyali değiştirmez). E6c = E4f (TP1 %8)
yarım ölçekte: PF 1.12/1.38/3.71, DD 1302/1077/257. E6a (`TF_REGIME=5m`) gerçek RED: AYI 1.01,
YATAY 0.99, BOĞA +1944 (−%50). Karar/kapsam: `docs/superpowers/specs/2026-08-22-reversal-day-loss-design.md`.
| E6d | AYI | 219 | 84.5 | +2664.58 | 1.20 | 2911.99 | - |
| E6d | YATAY | 155 | 83.9 | +1574.64 | 1.17 | 2396.01 | - |
| E6d | BOGA | 97 | 89.7 | +2764.36 | 1.67 | 683.56 | - |
| E6d | KARAR | - | - | +125.25 | - | - | REDDEDILDI (BOGA PnL kaybi >%20) |
| E6d | hipotez | Sabit ROI stop %50->%40: kayip/islem kucultur; E3a (%30) SL sayisini 2x yapmisti, %40 ara nokta. | | | | | |
| E6e | AYI | 231 | 89.2 | +3923.22 | 1.40 | 1936.70 | - |
| E6e | YATAY | 166 | 88.6 | +2888.13 | 1.43 | 1733.67 | - |
| E6e | BOGA | 109 | 92.7 | +3280.18 | 1.99 | 653.34 | - |
| E6e | KARAR | - | - | +3213.20 | - | - | ADAY (AYI PF=1.40) |
| E6e | hipotez | Stop %40 + TP1 %8 birlikte: odeme orani (kazanc/kayip) 0.22'den ~0.25'e, BE daha erken. | | | | | |

## 2026-08-23 — Sinyal otopsisi (E8): hangi girişler yanlıştı, giriş anında nasıl bilinebilirdi?

Salt-okunur analiz (kod DEĞİŞTİRİLMEDİ). Soru kullanıcı kararıyla daraltıldı: boyut/TP1/stop
ayarı YOK; yalnız **giriş sinyali kalitesi**. Yöntem: canlı defterin 202 kapanmış işlemini
giriş zaman damgasından ÖNCE kapanmış mumlardan türetilen 40+ özellikle zenginleştirmek,
kaybeden/kazanan ayrımını ölçmek, tek-eşikli kapıları hem defterde hem harness'ta simüle etmek.

**Veri ve yollar** (hepsi yeniden üretilebilir):
- Defter: `awa:/opt/tradingbot-v2/tradingbot.db` → `scalp_trades` (203 satır, 202 CLOSED,
  2026-08-07 09:20 → 2026-08-22 23:52 UTC). Kopya: scratchpad `signal_autopsy/ledger_raw.json`.
- TV kaynak eşlemesi: `awa:/opt/tradingbot-v2/logs/bot.log` + `bot.log.{1..7}.gz`,
  `✅ Sağlama tamam: SEM YÖN — kaynaklar [...]` satırları (bot.log yerel saat = **UTC+2**;
  doğrulama: sağlama→dolum gecikmesi medyan 75 sn, p90 246 sn — saat kayması olsaydı ±7200 sn olurdu).
  Kopya: `signal_autopsy/tv_votes.log` (4451 satır). 58 TV işleminin **49'u** eşleşti, 9'u "bilinmiyor".
- Piyasa verisi: Binance public `/fapi/v1/klines`, İKİ ayrı çekim —
  `klines/` (mainnet, 2026-06-25→08-23, 1m/5m/15m/1h/1d), `klines_testnet/` (testnet, aynı pencere),
  `klines_bear/` (mainnet, 2025-12-15→2026-02-15, harness AYI penceresi için).
- Script'ler: `signal_autopsy/{fetch_klines,enrich,analyze,run_analysis,rules,enrich_harness}.py`;
  çıktılar `trades_enriched[_testnet].csv/json`, `analysis_report[_testnet].txt`,
  `rules_{mainnet,testnet}.txt`, `harness_rules.txt`, `harness_daygate.txt`.
  Scratchpad kökü: `/private/tmp/claude-501/-Users-max-Downloads-Downloads-TRADINGBOT/7dda3fb5-.../scratchpad/signal_autopsy/`.
- Gösterge matematiği repo'nun kendi saf modüllerinden (`indicators.rsi_series/bollinger/atr/ema`,
  `regime.detect_regime`) — motorla birebir; pencere boyları da motorunkiyle aynı
  (rejim 250×15m, bağlam 100×5m, giriş 150×1m — `engine.py:1167-1172`).

### E8.0 — Veri kaynağı bulgusu: canlı motor TESTNET, harness MAINNET mumu okuyor
`ScalperEngine` `KlineFetcher()`'ı parametresiz kurar → `settings.binance_base_url`
(`data.py:60-61`, `engine.py:129`) = **testnet** (`.env`: `BINANCE_BASE_URL=https://testnet.binancefuture.com`).
Harness ise mainnet'e sabitlenmiştir (`backtest.py:1309` `base_url="https://fapi.binance.com"`).
Testnet 1m mumları neredeyse durağan: 2026-08-22 23:50-23:53 BTCUSDT kapanışları
testnet `77079.20 / 77079.20 / 77079.10` (hacim 3.6 / 1.5) — mainnet `77108.0 / 77111.8 / 77073.8`
(hacim 39.0 / 21.0). Sonuçlar:
- Motorun `signal_reason`'a yazdığı giriş RSI'ı (143 C işlemi) **testnet** 1m serisiyle uyuşuyor
  (medyan |Δ| **2.8**, %52'si ≤3), mainnet 1m ile uyuşmuyor (medyan |Δ| **7.4**, %18'i ≤3).
  Gecikme taraması (0–30 dk) en iyi uyumu 0–1 dk'da veriyor → zamanlama doğru, fark veri kaynağı.
- **Hacim tabanlı hiçbir özellik iki taraf arasında taşınmaz**: `vol_ratio_5m` korelasyon
  r = **−0.04**, testnet ortalaması 206 (mainnet 1.73). Hacim kapısı önerilmemeli.
- Makro/orta-vade özellikler taşınır (mainnet↔testnet Pearson r): BTC gün-açılışı sapması
  **0.998**, BTC 3g koşu **1.000**, RSI(15m) **0.975**, RSI(5m) **0.953**, ATR persentili **0.955**,
  EMA50 mesafesi (ATR birimi) **0.883**. Bu yüzden aşağıdaki kurallar bu ailelerden seçildi.
- Fiyat SEVİYESİ testnet≈mainnet: defterdeki dolum fiyatı ile mainnet 5m kapanışı arasında
  medyan sapma %0.054 (n=199; >%1 sapan yalnız 4 satır: 3× BEATUSDT + XRP #199).

**Parite kontrolü (rejim kapısı D5).** Motorun kuralı "DOWN'da LONG / UP'ta SHORT yasak".
Testnet-pariteli yeniden hesapta 25/202 satır kuralı ihlal ediyor görünüyor; hepsi açıklanıyor:
**20'si** kapının canlıya alınmasından (2026-08-16, `engine.py` yorumu) ÖNCE; kalan 5'ten
2'si (#91 LTC, #92 SOL, 08-18) TV muafiyetinin kaldırıldığı gün açılmış TV SHORT'ları;
son 3'ü (#52, #55, #73) kıl payı — EMA50/EMA200 farkı sırasıyla %−0.026 / %−0.096 / %0.000,
kapanış/EMA50 farkı %−0.40 / %−0.04 / %+0.14. 5 dk'lık rejim önbelleği
(`engine.py:103 _REGIME_CACHE_TTL=300`) bu üçünü tek başına açıklar.
**2026-08-18'den sonra tek bir ihlal yok** — D5 paritesi doğrulandı.

### E8.1 — Popülasyon (opened_at'e göre; 202 CLOSED)
| Kesit | n | SL | SL% | WR% | PnL | PF |
|---|---|---|---|---|---|---|
| Tümü | 202 | 54 | 26.7 | 67.8 | +849.1 | 2.11 |
| `pnl_source=binance_income_net` (güvenilir) | 179 | 51 | 28.5 | 70.4 | +596.2 | 1.93 |
| C | 144 | 44 | 30.6 | 63.2 | +516.4 | 1.86 |
| TV | 58 | 10 | 17.2 | 79.3 | +332.7 | 3.03 |
| LONG | 144 | 27 | 18.8 | 77.1 | +761.4 | 2.44 |
| SHORT | 58 | 27 | 46.6 | 44.8 | +87.7 | 1.37 |
| C-LONG | 101 | 22 | 21.8 | 73.3 | +373.5 | 1.87 |
| C-SHORT | 43 | 22 | 51.2 | 39.5 | +142.9 | 1.84 |
| TV-LONG | 43 | 5 | 11.6 | 86.0 | +387.9 | 4.92 |
| **TV-SHORT** | **15** | **5** | **33.3** | **60.0** | **−55.2** | **0.15** |

BTC gün tipi (post-hoc, UP>+1.5% / DOWN<−1.5%): UP 97 işlem +705.0 (PF 4.57, SL %16.5) ·
FLAT 88 +257.9 (1.86, %37.5) · DOWN 17 −113.8 (0.58, %29.4).
Ters-gün kovaları: **DOWN günü LONG** n=15, 4 SL, −110.4 (PF 0.58) · **UP günü SHORT** n=13,
**10 SL**, −85.6 (PF 0.22).

**Ödeme asimetrisi — spec'teki rakam üretilemedi (düzeltme).** `roi_pct` alanı
`realized_pnl / margin_usdt × 100`'dür (`tracker.py:132`). Defterde SL çıkışlarının ROI'ı
yalnız 6 satırda tam −%50'dir; kalanlar TP1 sonrası BE'ye çekilmiş stoplardır
(dağılım −54.8 … −0.1, medyan **−6.4**). Ölçüm:
| Kesit | kazanan n / ort | kaybeden n / ort | başabaş WR | gerçek WR |
|---|---|---|---|---|
| Tüm defter (USDT) | 137 / +11.77 | 64 / −11.94 | **%50.3** | %67.8 |
| Güvenilir alt küme (USDT) | 126 / +9.82 | 53 / −12.10 | **%55.2** | %70.4 |
| 12 Ağu sonrası (USDT) | 133 / +9.30 | 54 / −12.92 | **%58.2** | %71.1 |
| Tüm defter (ROI%) | +9.93% | −12.13% | **%55.0** | %67.8 |
`docs/superpowers/specs/2026-08-22-reversal-day-loss-design.md`'deki "SL ort −%48 ROI →
başabaş WR %81.5" bu veriden **yeniden üretilemedi**; en olası kaynak, ortalama USDT kaybının
dönem-ortalaması bir marja bölünmesi (marj 17→22 Ağu arası 36→162 büyüdü, D16). CLAUDE.md'deki
"başabaş ≈ %85" ise **simülatör** rakamıdır (SL −514 / TRAIL +88 birim, "rejim referansları"
tablosu) — canlı defterle karıştırılmamalı. Kenar sanılandan geniştir; bu, ayar değil sinyal
odaklı çözümü zayıflatmaz, yalnız aciliyet çerçevesini düzeltir.

### E8.2 — Özellik kümesi (hepsi giriş anından ÖNCE kapanmış mumlardan)
Sembol: `sym_chg_1h/4h/24h`, `sym_run_3d`, `sym_day_open_dev`, `atr_pct_5m`, `atr_pctile_30d`
(30 günlük 5m ATR% persentili), `dist_ema50_15m_atr/_pct`, `rsi_1m/5m/15m`, `bb_pctb_1m/5m/15m`,
`vol_ratio_5m`, `sym_regime_15m`. Motor-pariteli: `eng_rsi_entry`, `eng_bb_pctb_entry`,
`eng_atr_pct_entry`, `eng_regime_sym/btc` (motorun çektiği pencere boylarıyla).
Lider: `btc_chg_1h/3h/24h`, `btc_run_3d`, `btc_day_open_dev`, `btc_regime_15m`.
Yön-işaretli (`align_* > 0` = işlem yönü hareketle AYNI yönde): `align_btc_day/1h/3h/24h/run_3d`,
`align_sym_day/1h/run_3d`, `align_dist_ema50_atr`, `abs_dist_ema50_atr`,
`rsi_extremity_1m/5m/15m/eng` ( = (50−RSI)×yön ), `bb_overshoot_1m/5m/eng`.
Zaman: `hour_utc`, `dow`. Kümelenme: `cluster_60m_same_dir(_sym)`.
TV: `tv_sources`, `tv_confluence_lag_s`, `tv_vote_span_s`.
**Post-hoc (kapı özelliği DEĞİL, yalnız tanı):** `mae_pct`, `mfe_pct`, `btc_day_type_posthoc`.

### E8.3 — Kaybeden (exit_reason=SL, n=54) vs kalanlar — en ayırt edici 5 özellik
AUC = P(SL değeri > SL-olmayan değeri); 0.5 = bilgi yok. p = permütasyon (2000 tur).
Sayılar testnet mumlarıyla (motorun gördüğü); mainnet karşılıklarıyla fark ≤0.02 AUC
(mainnet tablosu `signal_autopsy/analysis_report.txt`, testnet `analysis_report_testnet.txt`).
| Özellik | SL ort (med) | SL-olmayan ort (med) | AUC | p |
|---|---|---|---|---|
| `align_btc_run_3d` (BTC 3g koşusu, yön işaretli) | 2.28 (0.62) | 7.50 (5.56) | 0.292 | <0.001 |
| `btc_chg_24h` | 0.86 (0.22) | 3.77 (1.33) | 0.299 | <0.001 |
| `sym_run_3d` | −1.11 (−0.46) | 10.49 (5.06) | 0.312 | <0.001 |
| `atr_pctile_30d` | 41.1 (25.1) | 62.8 (79.2) | 0.322 | <0.001 |
| `rsi_extremity_15m` ( = (50−RSI₁₅ₘ)×yön ) | 2.79 (1.14) | −4.58 (−5.05) | 0.644 | <0.001 |
Yalnız **LONG** (n=144, 27 SL): `rsi_5m` 41.2 vs 49.1 (AUC 0.333, p 0.006) · `rsi_15m` 48.0 vs
57.1 (0.342, p 0.012) · `dist_ema50_15m_atr` −0.47 vs **+1.69** (0.325, p 0.004) ·
`btc_chg_24h` 1.70 vs 4.72 (0.302, p 0.001) · `sym_chg_4h` −3.18 vs 0.73 (0.332, p 0.009).
Yalnız **SHORT** (n=58, 27 SL): giriş anında ölçülen HİÇBİR özellik anlamlı ayırmıyor
(`atr_pctile_30d` p 0.44, `align_btc_day` p 0.29, `align_dist_ema50_atr` p 0.39; tek "anlamlı"
olan `mae_pct` post-hoc). SHORT tarafında kayıp **öngörülemiyor**; aşağıdaki SHORT kuralı
ayrım değil, **ödeme asimetrisi** üzerinden çalışıyor (kazançlar ≈0, kayıplar tam stop).

**C-LONG için bağlam-TF RSI'ı tek başına monoton** (ledger, testnet mumu):
| RSI(14, 5m=bağlam TF) | n | SL | SL% | PnL |
|---|---|---|---|---|
| <30 | 15 | 6 | 40.0 | −51.1 |
| 30–35 | 11 | 4 | 36.4 | −90.3 |
| 35–40 | 12 | 3 | 25.0 | +24.0 |
| 40–45 | 24 | 5 | 20.8 | +58.5 |
| 45–50 | 18 | 1 | 5.6 | +142.6 |
| ≥50 | 21 | 3 | 14.3 | +289.8 |
Eşik 40'ta: engellenen 38 C-LONG (13 SL, −117.5), kalan 63 işlem +490.9 / PF 4.42.
Monotonluk eşik-uydurmasına karşı en güçlü kanıttır.

### E8.4 — İki kayıp arketipi (en büyük 12 kayıp incelendi)
(değerler mainnet / testnet mumu — ikisi de veriliyor, sonuç aynı)
- **(A) Düşen bıçak** — işlem yönü ÜST zaman dilimine karşı: LONG'da RSI(5m/15m)<50 ve fiyat
  15m EMA50'nin altında. Örnek #199 XRP C-LONG 22 Ağu 14:40 (−84.0): rsi5 25.5/33.6,
  rsi15 41.7/42.2, EMA50 mesafesi −1.67/−1.25 ATR, BTC gün-açılışına göre −1.68%.
- **(B) Tepe kovalama** — işlem yönünde AŞIRI uzama: #187 ETH TV-LONG 22 Ağu 01:07 (−89.3):
  EMA50'nin **+3.56/+2.01 ATR** üstünde, ATR persentili 96.8/90.1, BTC 3 günde +20.9%.
  #152 XRP C-LONG 20 Ağu (−70.0): +3.35/+4.45 ATR, rsi15 71.1/73.4, ATR persentili 100.
  **(B) kapatılamıyor:** aynı yönde uzama filtreleri defterde net NEGATİF
  (`align_dist_ema50_atr > 2.0` engelle → tüm defterde **−559.3**, TV-LONG'da −302.1).
  Uzama trend sürüşünün ta kendisi; (B) kayıpları trendden kazanmanın bedeli.

### E8.5 — Tek-eşikli kapı simülasyonu (DEFTER üstünde)
Ölçüt: Δ = kapı uygulansaydı toplam PnL değişimi; ayrıca güvenilir alt küme, tarih ortasından
bölünmüş iki yarı, BTC gün tipi kırılımı ve bootstrap %90 aralık (2000 tur). İki veri kaynağında
da ölçüldü; "Δ güvenilir", "yarı-1/yarı-2", "ci90" ve "PF" sütunları **mainnet** koşusundan
(testnet koşusunun karşılıkları `signal_autopsy/rules_testnet.txt`'te, işaret ve mertebe aynı).
`align_btc_day` ailesi iki kaynakta birebir aynıdır (r=0.998).
| Kural (kapsam) | blok / SL | Δ mainnet | Δ testnet | Δ güvenilir | yarı-1 / yarı-2 | ci90 | PF |
|---|---|---|---|---|---|---|---|
| `RSI(5m) < 40` → C-LONG yok | 35–38 / 12–13 | +179.7 | +117.5 | +112.4 | +89.0 / +90.7 | [+4.7, +393.8] | 2.11→3.15 |
| `RSI(15m) < 50` → C-LONG yok | 41–42 / 13–14 | +176.3 | +172.3 | +103.5 | +70.8 / +105.4 | [−14.8, +422.6] | 2.11→3.32 |
| `fiyat < EMA50(15m) − 1.0·ATR` → C-LONG yok | 27–30 / 10 | +124.0 | +122.5 | +76.8 | +49.4 / +74.5 | [−12.9, +298.6] | 2.11→2.72 |
| `ATR persentili < 40` → SHORT yok | 36–39 / 19–20 | +110.3 | +109.8 | +90.3 | +59.0 / +51.3 | [−1.2, +235.0] | 2.11→2.63 |
| `BTC gün açılışına göre ≥1.3% ters` → giriş yok | 11 / 2 | −27.8 | −27.8 | **+99.2** | −129.6 / +101.8 | [−314.4, +242.6] | 2.11→2.38 |
Reddedilenler (defterde net negatif): `align_btc_run_3d > 15` (tüm defter **−152.7**;
LONG'da eşik 12 → **−382.9**, 50 kazanan engelliyor) · `cluster_60m_same_dir > 1` (**−509.2**;
kümelenme trendin kendisi) · aynı yönde uzama filtreleri (yukarıda) · `vol_ratio_5m`
(testnet'te ölçülemez, E8.0) · gün/saat kalıpları (Cts SL %51.4, Pzt %60.0 — 16 günde
2-3 örnek; **rejim artefaktı, kural yapılmamalı**).

### E8.6 — HARNESS ölçümü (3 pencere, mevcut env; P2 kuralı)
Taban koşular (sunucu env kopyası `scripts/.scalper_env_snapshot.txt`, C-only, 8 majör,
`--cache-dir data/klines_cache`; log `signal_autopsy/bt_{AYI,YATAY,BOGA}.log`, ham JSON
`bt_{AYI,YATAY,BOGA}.json`; kaynak `logs/backtest_20260823_{015145,015330,015400}.json`
— `logs/` gitignore'da, commit'lenmez):
AYI 213 işlem +584.4 / PF 1.04 (D12'nin "yeni taban 1.04/DD 3683"i ile birebir) ·
YATAY 145 +2392.3 / 1.29 · BOĞA 90 +3901.7 / 2.43.

**Yöntem sınırı (önemli):** kapı **post-hoc** uygulandı — harness JSON'undaki her işlem giriş
zamanıyla yeniden zenginleştirilip filtrelendi. Engellenen bir işlem harness'ta
kapasite/sembol-içi tekillik kısıtını serbest BIRAKMAZ, yani yerine başka bir sinyal geçmez.
Bu yüzden sayılar gerçek motor-içi kapının **alt sınırı**dır; kapı kodlandığında (D15) tekrar
ölçülmelidir. Script: `signal_autopsy/enrich_harness.py`.

**Lider gün-açılışı kapısı (D15 `SCALPER_MARKET_GATE_DAY_PCT`) — eşik taraması**
(blok/SL → ΔPnL; sonuç PnL / PF):
| Eşik | Kapsam | AYI | YATAY | BOĞA | P2 |
|---|---|---|---|---|---|
| 0.5% | iki yönlü | 84/15 → **+2421.9** (3006.2 / **1.40**) | 39/3 → −2002.4 (389.9 / 1.06) | 17/0 → −1294.8 (2606.8 / 1.96) | ❌ BOĞA −%33 |
| 1.0% | iki yönlü | 69/12 → +1751.8 (2336.2 / 1.26) | 16/2 → −487.3 (1905.0 / 1.26) | 3/0 → −241.3 (3660.3 / 2.34) | ✅ (BOĞA −%6.2) |
| **1.3%** | **iki yönlü** | 66/12 → **+2010.4** (2594.8 / **1.29**) | 9/1 → −186.6 (2205.7 / 1.28) | 1/0 → −104.0 (3797.6 / 2.39) | **✅ (AYI PF 1.29, BOĞA −%2.7)** |
| **1.0%** | **yalnız SHORT** | 45/9 → +1675.7 (2260.1 / **1.22**) | 11/2 → **+138.1** (2530.4 / **1.35**) | 3/0 → −241.3 (3660.3 / 2.34) | **✅ (3 pencerede 2'si pozitif)** |
| 1.3% | yalnız SHORT | 42/9 → +1934.3 (2518.7 / 1.24) | 7/1 → −16.6 (2375.7 / 1.30) | 1/0 → −104.0 (3797.6 / 2.39) | ✅ |
| 1.3% | yalnız LONG | 24/3 → +76.1 (660.5 / 1.05) | 2/0 → −170.0 | 0 | ❌ (AYI PF 1.05) |
| 2.0% | iki yönlü | 38/8 → +1695.3 (2279.7 / 1.21) | 2/0 → −230.1 | 0 | ✅ |
Okuma: kazancın tamamına yakını **SHORT bacağından** geliyor (AYI'da BTC gün-açılışının
%1+ ÜSTÜNDEyken açılan 45 SHORT −1675.7 etmiş). LONG bacağının harness kanıtı zayıf
(AYI +76), ama canlı defterin 22 Ağu kaybı tam oradan geldi (8 işlem, +102.1) —
iki kanıt çelişmiyor, farklı rejimlerde farklı bacak çalışıyor.

**Diğer kuralların 3-pencere hükmü (aynı post-hoc yöntem, `signal_autopsy/harness_rules.txt`):**
| Kural | AYI | YATAY | BOĞA | P2 |
|---|---|---|---|---|
| `RSI(5m)<40` → LONG yok | +98.5 (PF 1.06) | −1244.1 | −771.6 (−%20) | ❌ AYI PF<1.1 |
| `RSI(15m)<50` → LONG yok | −454.8 | −2802.4 | −1166.6 | ❌ |
| `RSI(15m)<50` → iki yönlü | +227.1 (1.12) | −2488.7 | −1719.9 (−%44) | ❌ BOĞA |
| `fiyat < EMA50(15m)−1·ATR` → iki yönlü | +786.3 (1.14) | −3555.1 | −1394.8 (−%36) | ❌ BOĞA |
| `ATR persentili<40` → SHORT yok | −1037.5 | −565.3 | +323.5 | ❌ AYI |
| `align_btc_run_3d>15` → giriş yok | +2453.3 (1.25) | 0 tetik | 0 tetik | ⚠ tek pencere |
**Yani: defterde en güçlü olan bağlam-TF kuralları harness'ta P2'yi GEÇMİYOR.** Defter
16 günlük ve LONG-ağırlıklı (144/202) bir boğa örneklemi; harness'ın AYI/YATAY pencereleri
SHORT-ağırlıklı. Bağlam-TF kuralı "üst TF'ye karşı işlem açma" demek olduğundan, C'nin
kârının ters-trend dip alımından geldiği pencerelerde (YATAY: `RSI(15m)<50` 54 LONG'un
50'si kazanan) doğrudan kârı kesiyor. **Hüküm: kanıt yetersiz — uygulanmaz.**

### E8.7 — TV SHORT kaynak kalitesi (15 işlem, PF 0.15)
Kaynak eşlemesi `bot.log` "Sağlama tamam" satırlarından. Sunucudaki log tutma penceresi
**2026-08-16 00:05**'te başlıyor (`bot.log.7.gz`), TV işlemleri ise 08-12'de başlamış: bu yüzden
9 TV işleminin kaynağı "bilinmiyor" — 7'si (08-12…08-15) log tutma penceresinin dışında,
2'si (#60 08-16 LONG, #89 08-18 SHORT) pencere içinde olduğu hâlde ±10 dk'da eşleşen
"Sağlama tamam" satırı bulunamadı (nedeni **kodda/logda doğrulanamadı**).
| Kaynak çifti | Yön | n | SL | PnL | PF | WR% |
|---|---|---|---|---|---|---|
| luxosc+luxso | SHORT | 5 | 3 | **−50.9** | **0.00** | 40.0 |
| bilinmiyor | SHORT | 7 | 1 | −3.5 | 0.67 | 71.4 |
| algopro+luxosc | SHORT | 2 | 1 | −1.0 | 0.72 | 50.0 |
| algopro+luxso | SHORT | 1 | 0 | +0.2 | ∞ | 100.0 |
| bilinmiyor | LONG | 2 | 1 | +11.1 | 57.96 | 50.0 |
| algopro+luxso | LONG | 14 | 2 | +62.4 | 1.69 | 85.7 |
| algopro+luxosc | LONG | 12 | 1 | +141.5 | 19.54 | 91.7 |
| luxosc+luxso | LONG | 15 | 1 | +172.9 | 133.93 | 86.7 |
Mekanizma: 15 TV SHORT'un 9 kazananından **8'i +1 USDT'nin altında** (TP1 dolup BE'ye
çekilen TRAIL: +0.1 … +0.7; tek istisna #29 +5.6). Brüt kâr toplamı 10.2, brüt kayıp 65.2 →
PF 0.156. Kaybın **%88'i 3 satırdan** geliyor (#92 −30.6, #75 −18.4, #30 −8.6) ve üçü de
DÜŞÜK oynaklık rejiminde (ATR persentili 19.9 / 33.7 / 45.6). Yani "PF 0.15" bir
ayırt-edicilik değil **ödeme asimetrisi** problemidir: TV SHORT'un yukarı tarafı yok,
aşağı tarafı tam stop. Kaynak bazında hüküm vermek için örneklem yetersiz (kaynak başına
1–5 işlem); `luxosc+luxso` SHORT'un brüt kârı **tam sıfır** olması yine de en zayıf halka işareti.
TV sağlama süresi (ilk oy→tamam) medyan 148 sn ve sonucu AYIRMIYOR (SL ort 152 sn,
SL-olmayan 150 sn) → sağlama penceresi (420 sn) bir kalite kaldıracı değil.
TV LONG'da hiçbir kaynak çifti negatif değil.

### E8.8 — Kod gerektiren kapılar (bir sonraki ajana kesin tarif)
Mevcut `SCALPER_*` anahtarlarıyla ölçülemeyenler ve neden:
1. **Lider gün-açılışı kapısı** — D15 `SCALPER_MARKET_GATE` / `_DAY_PCT`. Ölçüm yukarıda:
   **iki yönlü, eşik %1.3** (spec'teki varsayılan %1.0 da P2'yi geçiyor ama YATAY'da −%20).
   Alt-kapı önerisi: **SHORT bacağı %1.0, LONG bacağı %1.3** (kanıt ayrık — E8.6).
2. **Lider N-günlük koşu alt-kapısı** (`_RUN_PCT/_RUN_DAYS`) — **varsayılan 0 (KAPALI) kalsın.**
   Canlı defterde net **−152.7** (LONG eşik 12'de −382.9; 50 kazanan engelliyor), harness'ta
   yalnız AYI'da tetikleniyor (+2453, 13 işlem), YATAY/BOĞA'da hiç tetiklenmiyor. Tek pencerede
   parlayan aday = P2'ye göre red. Spec'teki "lider koştuysa o yöne girme" hipotezinin
   İŞARETİ defterde TERS: kazananların `align_btc_run_3d` ortalaması 7.50, kaybedenlerin 2.28.
3. **Bağlam-TF trend uyumu** (`passes_context_trend`, öneri): C LONG için
   `RSI(14, scalper_tf_context) >= X`, SHORT için `<= 100−X`; X=0 kapalı. Veri hazır —
   `ctx.candles_15m` (bağlam rolü, 100 mum) zaten çekiliyor, YENİ REST çağrısı YOK.
   Defter kanıtı güçlü ve monoton (E8.3), **harness kanıtı P2'yi geçmiyor (E8.6) → şu an
   uygulanmamalı**; ancak D15 kapısı canlıya girip soak bittikten sonra tekrar ölçülmeye değer.
   Mevcut `SCALPER_C_REQUIRE_FLOW_CONFIRM` AYNI niyeti taşıyor ("düşen bıçakta dip alma")
   ama MFI'yi **giriş TF'sinde** (canlıda 1m) okuyor (`setups.py:144-162`) ve E2b'de
   YATAY'da düşmüştü — farklı bir kaldıraç, karıştırılmamalı.
   `SCALPER_USE_EQUILIBRIUM_FILTER` ise TERS kutupludur (LONG'u yalnız *discount*ta geçirir,
   `setups.py:194-217`) — bu bulgunun tam tersini yapar, açılmamalı.
4. **Uzama kapısı** (`fiyat − EMA50(tf_regime)` / ATR) — kodlanabilir (`ctx.candles_4h`
   zaten 250 mum) ama **iki taraflı da red**: ters yön eşiği harness'ta BOĞA'yı −%36 bozuyor,
   aynı yön eşiği defterde −559. Önerilmiyor.
5. **ATR persentili** (`atr_pctile_30d`) — 30 günlük 5m geçmişi gerekir (sembol başına
   ~8640 mum = 6 ek REST çağrısı, günlük önbelleklenebilir). Defterde SHORT için +110 ama
   harness'ta AYI −1037 → **önerilmiyor**; ayrıca SHORT tarafında ayırt-edicilik yok (p 0.44).
6. **Hacim tabanlı kapı** — testnet'te ÖLÇÜLEMEZ (E8.0, r=−0.04). Mainnet'e geçmeden
   denenmemeli; harness'ta iyi görünse bile canlıda (testnet) anlamsız çalışır.

**Örneklem uyarısı.** Defter 202 işlem / 16 gün, 54 SL. Yön×strateji kırılımında hücre başına
15–101 işlem kalıyor; TV-SHORT 15, DOWN-günü LONG 15, UP-günü SHORT 13. Bu boyutta
çok-parametreli kural aramak aşırı uydurmadır; yukarıdaki taramada bilinçli olarak yalnız
TEK eşikli, ön-kayıtlı (spec'ten veya mekanizmadan türeyen) kurallar denendi ve her biri
4 ayrı kesitte (tümü / güvenilir / yarı-1 / yarı-2) + gün tipi kırılımında + bootstrap
aralığıyla raporlandı. Yine de tek bir kuralın bile defter kanıtı **tek başına** terfi
gerekçesi değildir (P2: 3 rejim penceresi + testnet soak).

## 2026-08-23 — Piyasa yapısı (CHoCH/BOS) kapısı (E9)

**Soru (kullanıcı):** "sistem dönüşleri tespit edemiyor" — rejim kapısı (D5, 15m
EMA50/200) dönüş günlerinde saatler geç kalıyor; LuxAlgo Price Action Concepts'in
CHoCH/BOS yapı sinyalleri bunu daha erken görüyor mu? Çözüm **sinyal** olmalı
(boyut/TP/stop ayarı D16'da kullanıcı kararıyla reddedildi).

**Ne kodlandı:** `src/strategies/scalper/structure.py` — saf (IO'suz, deterministik,
look-ahead'siz) yapı durum makinesi: fraktal pivot → son onaylanmış swing seviyesi →
kapanışla kırılım → BOS (devam) / CHoCH (karakter değişimi, yapı yönü döner). Motorda
`_evaluate_symbol`'de rejim kapısının HEMEN ARDINDA tek giriş kapısı (C ve TV aynı
yerden geçer), harness'ta `simulate_symbol` AYNI saf fonksiyon çiftini AYNI pencerelerle
çağırır. Opsiyonel çıkış tetikleyicisi (`SCALPER_STRUCTURE_EXIT=be|close`) canlıda
`engine._apply_structure_exits`, harness'ta `manage_position`. **Hepsi varsayılan KAPALI**
(`tests/test_structure.py` 51 test + `tests/test_golden_backtest.py` DEĞİŞMEDEN geçer).

**Komut kalıbı** (env tabanı `scripts/.scalper_env_snapshot.txt` = canlı 10/50/10/10,
divergence=true; `--cache-dir data/klines_cache` → AĞSIZ; C-only, 8 majör):
```bash
env $(cat scripts/.scalper_env_snapshot.txt | xargs) SCALPER_STRUCTURE_GATE=true \
  SCALPER_STRUCTURE_TF=context SCALPER_STRUCTURE_PIVOT=5 \
  python3 -m src.strategies.scalper.backtest --strategies C \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,BNBUSDT,ADAUSDT,LTCUSDT \
  --start 2026-01-23 --end 2026-02-13 --cache-dir data/klines_cache
```
Loglar: `logs/structure/<varyant>_<pencere>.log` (24 koşu; `logs/` gitignore'da).
Varyantlar: **S0** kapalı (taban) · **S1** giriş kapısı 5m/pivot5 · **S2** giriş kapısı
15m/pivot5 · **S3** = S1 + `EXIT=be` · **S4** = S1 + `EXIT=close` · **S1p3/S1p8/S2p8**
pivot duyarlılığı.

### E9.1 — Sonuç tablosu (işlem / WR% / PnL / PF / maxDD · kapı tetiği)
| Varyant | AYI | YATAY | BOĞA | Yapı kapısı tetiği (A/Y/B) |
|---|---|---|---|---|
| **S0 taban** | 213 / 85.4 / **+584.4** / **1.04** / 3683 | 145 / 86.9 / **+2392.3** / 1.29 / 3229 | 90 / 93.3 / **+3901.7** / 2.43 / 735 | — |
| S1 (5m, p5) | 84 / 83.3 / −1057.1 / 0.85 / 2140 | 71 / 81.7 / −356.4 / 0.93 / 2077 | 36 / 91.7 / +1289.4 / 1.84 / 1113 | 344 / 258 / 188 |
| S2 (15m, p5) | 129 / 84.5 / −396.8 / 0.96 / 3314 | 104 / 80.8 / −1943.3 / 0.78 / 3260 | 59 / 89.8 / +1525.9 / 1.56 / 1295 | 239 / 149 / 103 |
| S3 (S1+be) | 84 / 47.6 / −1589.3 / 0.70 / 2438 | 77 / 39.0 / −755.0 / 0.77 / 1566 | 40 / 52.5 / +1172.0 / 3.00 / 514 | 356 / 289 / 220 |
| S4 (S1+close) | 89 / 33.7 / −2442.3 / 0.53 / 2821 | 81 / 33.3 / −898.9 / 0.69 / 1084 | 42 / 38.1 / +409.9 / 1.43 / 417 | 373 / 304 / 229 |
| S1p3 (5m, p3) | 63 / 82.5 / −1240.9 / 0.78 / 2499 | 55 / 83.6 / +576.4 / 1.18 / 1282 | 26 / 96.2 / +1739.6 / 4.38 / 514 | 394 / 314 / 245 |
| S1p8 (5m, p8) | 113 / 83.2 / −825.7 / 0.91 / 3311 | 84 / 83.3 / +227.6 / 1.04 / 2738 | 46 / 87.0 / +598.9 / 1.22 / 1423 | 252 / 208 / 165 |
| S2p8 (15m, p8) | 137 / 83.9 / −11.3 / 1.00 / 2720 | 112 / 84.8 / +209.8 / 1.03 / 2352 | 66 / 89.4 / +1535.8 / 1.47 / 1454 | 215 / 118 / 89 |

S0, E8.6/D12'nin tabanını **birebir** yeniden üretti (AYI 213 / +584.4 / PF 1.04 /
DD 3682.60) → ölçüm hattı doğru.

**P2 hükmü: 7 varyantın 7'si de REDDEDİLDİ.** Hiçbiri "AYI PF ≥ 1.1" ya da "AYI ve
YATAY birlikte iyileşir" kolunu geçmiyor; BOĞA PnL kaybı her varyantta ≥ %55
(kural: ≤ %20). En "iyi" varyant (S2p8) tabanı yalnızca **hiçbir şey yapmamaya
yaklaşarak** yakalıyor: pivot büyüdükçe (yapı yavaşladıkça) tetik sayısı düşüyor ve
sonuç tabana yakınsıyor — AYI PF 0.78 (p3) → 0.85 (p5) → 0.91 (p8) [5m] ve
0.96 (p5) → 1.00 (p8) [15m]. **Monotonluk, bunun bir eşik/pivot ayarı sorunu
OLMADIĞININ en güçlü kanıtıdır** (aynı desen E4a/E2ab'de de görülmüştü).

### E9.2 — Kesilen kayıp mı, kesilen kâr mı? (çıkış nedeni kırılımı)
| Pencere | Varyant | SL (n / PnL) | TRAIL (n / PnL) | Kesilen kayıp | Kesilen kâr | oran |
|---|---|---|---|---|---|---|
| AYI | S0 | 29 / −14907 | 182 / +15721 | — | — | — |
| AYI | S1 | 14 / −7196 | 70 / +6139 | 7711 | 9582 | **0.80** |
| AYI | S2 | 19 / −9766 | 109 / +9563 | 5141 | 6158 | **0.83** |
| YATAY | S0 | 16 / −8224 | 125 / +10628 | — | — | — |
| YATAY | S1 | 10 / −5140 | 57 / +4785 | 3084 | 5843 | **0.53** |
| YATAY | S2 | 17 / −8738 | 83 / +6807 | **−514** | 3821 | **<0** |
| BOĞA | S0 | 5 / −2570 | 84 / +6624 | — | — | — |
| BOĞA | S1 | 3 / −1543 | 33 / +2832 | 1027 | 3792 | **0.27** |
| BOĞA | S2 | 5 / −2571 | 53 / +4249 | −1 | 2375 | **0.00** |
Yani kapı, her pencerede kestiği her 1 birim kayba karşılık **1.2–3.7 birim kâr**
kesiyor; YATAY/BOĞA'da 15m kapısı **hiç kayıp kesmiyor**, yalnız kâr kesiyor.

**Yön kırılımı — kullanıcının "düşen bıçak LONG" hipotezi doğrudan sınandı.** AYI
penceresinde taban LONG bacağı 79 işlem / −956; 15m yapı kapısı 30 LONG'u engelledikten
SONRA kalan LONG bacağı **49 işlem / −2050 (PF 0.84 → 0.60)** — yani kapı düşen-bıçak
kayıplarını KESMEDİ, tersine **kârlı dip alımlarını** kesip başarısız sıçrama alımlarını
bıraktı. (S1'de LONG −956 → −587 ama işlem 79 → 36; kayıp/işlem AYNI mertebede.)
Mekanizma: C tanımı gereği ters-trend bir ortalamaya-dönüş stratejisidir (RSI ucu +
BB taşması); "yapıya ters işlem açma" kuralı C'nin kâr kaynağını doğrudan yasaklar.
Bu, E8.6'nın bağlam-TF kuralları için verdiği hükmün (defterde güçlü, harness'ta P2'yi
geçmiyor) BAĞIMSIZ ikinci bir doğrulamasıdır.

### E9.3 — Çıkış tetikleyicisi (S3/S4): en zararlı varyant
| Pencere | Varyant | CHOCH/STRUCT_BE çıkışı (n / PnL) | TRAIL (n / PnL) | WR% |
|---|---|---|---|---|
| AYI | S3 (be) | STRUCT_BE 34 / −136 | 40 / +3687 | 47.6 |
| AYI | S4 (close) | CHOCH 59 / −4682 | 29 / +2754 | 33.7 |
| YATAY | S4 (close) | CHOCH 56 / −2835 | 23 / +1861 | 33.3 |
| BOĞA | S4 (close) | CHOCH 27 / −939 | 15 / +1349 | 38.1 |
S4, AYI'da SL sayısını 29'dan **1**'e düşürdü (kayıp −14907 → −514) — yani "dönüşte
kes" mekanik olarak ÇALIŞIYOR — ama TRAIL kazananları 182'den **29**'a indirdi
(+15721 → +2754). Kazanma oranı %85 → %34. S3 (BE'ye çek) daha yumuşak: 34 işlem
başabaşa çekildi (−136 toplam) ama TRAIL 182 → 40. **Sonuç: açık bir C pozisyonuna
ters CHoCH bir dönüş uyarısı değil, C'nin zaten fade ettiği gürültünün ta kendisidir.**
Kullanıcının bilmesi gereken sayı: sistemin başabaş kazanma oranı ≈ %85 (SL ort −514,
TRAIL ort +88); WR'yi %34'e düşüren her kural, kayıpları kesse bile matematiksel
olarak kaybettirir.

### E9.4 — Gecikme analizi (yapı vs rejim kapısı)
Betik: scratchpad `structure_delay.py` (AĞSIZ, `data/klines_cache` üzerinde; 8 sembol ×
3 pencere).
- **Pivot onayı gecikmesi yapı SİNYALİNİ geciktirmez.** Bir pivot ancak sağındaki N mum
  kapanınca onaylanır, ama o seviyeyi kıracak bir mum pivotu zaten geçersiz kılar
  (`high[p] > high[j]` şartı) — yani kırılım olayı en erken `pivot+right+1`'de doğar ve
  bu ASLA bir kaçırılmış kırılım demek değildir. Özellik testiyle sabitlendi
  (`test_pivot_confirmation_delay_is_modelled`, `test_no_lookahead_prefix_stability`).
- Kırılan seviyenin YAŞI (pivot → kırılım) medyan **11 mum** (pivot 5; p3'te 7, p8'te
  17). Olay, kıran mumun KAPANIŞINDA doğar → 5m'de ≤5 dk, 15m'de ≤15 dk gecikme.
- **CHoCH, EMA50/200 rejim dönüşünden çok daha erken:** 15m'de aynı yönlü son CHoCH,
  rejim dönüşünden medyan **45 mum ≈ 11 saat** önce gerçekleşiyor (n=2128 dönüş;
  pivot 3'te 28 mum, pivot 8'de 66 mum). **Ama bu "öngörü" değil FREKANStır:** aynı
  veride 15m/pivot5 yapı, sembol-gün başına ≈2.4 CHoCH (5m'de ≈6.8) üretiyor — o kadar
  sık dönen bir göstergenin her rejim dönüşünden önce "haber vermiş" olması kaçınılmaz.
  Kapının 3 pencerede kaybettirmesinin nedeni de tam olarak budur: erken ama gürültülü.
- Yapı olay sayıları (8 sembol × pencere, kapanış bazlı): 5m/p5 AYI 1111 BOS + 1138
  CHoCH · YATAY 951/1044 · BOĞA 662/675; 15m/p5 AYI 402/410 · YATAY 363/404 ·
  BOĞA 251/292.

### E9.5 — Parite ve bilinen sapma
- Motor ve harness **aynı saf fonksiyonları** çağırır (`structure_state_for` →
  `structure_gate_blocks`; çıkışta `detect_structure` → `structure_exit_action`);
  fonksiyon kimliği testle sabitlendi (`test_engine_and_harness_call_the_same_pure_function`).
- **Bilinen 1 mumluk sapma (yeni değil, tüm göstergeler için geçerli):** canlı
  `get_klines(limit=N)` oluşmakta olan mumu attığı için genelde N−1 KAPALI mum döndürür;
  harness tam N mum diler. Yapı durum makinesi geçmişe bağımlı olduğundan bu ölçüldü
  (scratchpad `structure_window_parity.py`, gerçek veri, 8 sembol × 3 pencere):
  **5m/100 mum: yön farkı %0.06 (6520 örnekte 4), son-olay farkı %0.23; 15m/250 mum:
  %0.00**. Yani sapma ölçüm sonuçlarını taşıyacak mertebede değil.
- Kapı **motor-içi**dir (E8.6'nın post-hoc yönteminin aksine): engellenen sinyal
  kapasite/sembol-içi tekilliği serbest bırakır, yerine başka sinyal geçebilir. Bu
  yüzden E9 sayıları E8.6'nın "alt sınır" uyarısına tabi DEĞİLDİR.
