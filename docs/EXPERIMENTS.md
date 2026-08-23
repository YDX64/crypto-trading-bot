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

## 2026-08-23 — Lider piyasa kapısı (E7, spec §C / D15)

Kod: `src/strategies/scalper/market_gate.py` (saf kural, motor + harness ORTAK), commit `ece8bd8`.
Env tabanı: `scripts/.scalper_env_snapshot.txt` (sunucu env kopyası; TP1 10, fixed_roi 50,
max_margin 10, TF 1m/5m/15m, entry maker, dyn lev 3-20, max_positions 5, divergence açık).
D16 paketi bu ölçümlerin ORTASINDA uygulanıp GERİ ALINDI; tablodaki tüm koşular geri
alınmış (orijinal) tabanla YENİDEN koşuldu — her log dosyasının başında o koşuda fiilen
kullanılan tam env yazılıdır (`# taban env:` bloğu).
Komut:
```bash
env $(cat scripts/.scalper_env_snapshot.txt | xargs) SCALPER_MARKET_GATE=true \
  SCALPER_MARKET_GATE_DAY_PCT=<X> SCALPER_MARKET_GATE_RUN_PCT=<Y> SCALPER_MARKET_GATE_RUN_DAYS=3 \
  python3 -m src.strategies.scalper.backtest --strategies C \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,BNBUSDT,ADAUSDT,LTCUSDT \
  --start <YYYY-MM-DD> --end <YYYY-MM-DD> --cache-dir data/klines_cache
```
Loglar: `logs/market_gate/<varyant>_<pencere>.log` (24 dosya, sıralı koşuldu).

### Varyantlar
| Varyant | Ayar | Pencere | İşlem | WR% | PnL | PF | maxDD | ΔPnL | day tetik | run tetik |
|---|---|---|---|---|---|---|---|---|---|---|
| **V0** | kapı KAPALI (taban) | AYI | 213 | 85.4 | +584 | 1.04 | 3683 | — | 0 | 0 |
| V0 | | YATAY | 145 | 86.9 | +2392 | 1.29 | 3229 | — | 0 | 0 |
| V0 | | BOĞA | 90 | 93.3 | +3902 | 2.43 | 735 | — | 0 | 0 |
| **V1** | gün-içi %1.0 | AYI | 149 | 87.2 | **+2999** | **1.33** | 2956 | +2415 | 196 | 0 |
| V1 | | YATAY | 135 | 87.4 | +2593 | 1.36 | 2882 | +201 | 62 | 0 |
| V1 | | BOĞA | 88 | 93.2 | +3725 | 2.37 | 735 | −177 (−%4.5) | 15 | 0 |
| **V1a** | gün-içi %0.7 | AYI | 138 | 88.4 | +3897 | 1.52 | 2442 | +3312 | 219 | 0 |
| V1a | | YATAY | 124 | 85.5 | +873 | 1.11 | 3517 | **−1520 (−%63.5)** | 81 | 0 |
| V1a | | BOĞA | 83 | 92.8 | +3384 | 2.24 | 813 | −518 (−%13.3) | 18 | 0 |
| **V1b** | gün-içi %1.5 | AYI | 158 | 86.1 | +1832 | 1.17 | 3348 | +1247 | 162 | 0 |
| V1b | | YATAY | 138 | 87.7 | +2962 | 1.41 | 2840 | +570 | 30 | 0 |
| V1b | | BOĞA | 90 | 93.3 | +3902 | 2.43 | 735 | 0 (tetik yok) | 0 | 0 |
| **V1c** | gün-içi %1.3 (E8 önerisi) | AYI | 158 | 88.0 | **+3812** | **1.43** | 2956 | **+3228** | 179 | 0 |
| V1c | | YATAY | 137 | 87.6 | **+2791** | **1.38** | 2840 | **+399** | 32 | 0 |
| V1c | | BOĞA | 89 | 93.3 | **+3798** | 2.39 | 735 | **−104 (−%2.7)** | 3 | 0 |
| **V2** | uzama %15 / 3g | AYI | 196 | 87.2 | +2909 | 1.24 | 3083 | +2324 | 0 | 60 |
| V2 | | YATAY | 145 | 86.9 | +2392 | 1.29 | 3229 | 0 (tetik yok) | 0 | 0 |
| V2 | | BOĞA | 90 | 93.3 | +3902 | 2.43 | 735 | 0 (tetik yok) | 0 | 0 |
| **V2a** | uzama %10 / 3g | AYI | 192 | 87.0 | +2563 | 1.21 | 3083 | +1979 | 0 | 70 |
| V2a | | YATAY | 145 | 86.9 | +2392 | 1.29 | 3229 | 0 | 0 | 0 |
| V2a | | BOĞA | 76 | 93.4 | +2941 | 2.14 | 735 | **−960 (−%24.6)** | 0 | 40 |
| **V2b** | uzama %20 / 3g | AYI | 196 | 87.2 | +2909 | 1.24 | 3083 | +2324 | 0 | 60 |
| V2b | | YATAY/BOĞA | = V0 | | | | | 0 | 0 | 0 |
| **V3** | ikisi (%1.0 + %15/3g) | AYI/YATAY/BOĞA | **V1 ile BİREBİR AYNI** | | | | | | 196/62/15 | 0/0/0 |

V0, `docs/DECISIONS.md`'deki mevcut tabanı **birebir** üretti (AYI 1.04/DD 3683 · YATAY 1.29/3229 ·
BOĞA 2.43/735) → kapı kapalıyken harness çıktısı değişmemiştir (parite negatif kontrolü).
**V3 ≡ V1:** uzama alt-kapısının engelleyeceği her sinyal gün-içi alt-kapısı tarafından zaten
engelleniyor (V3'te `market_gate_run` sayacı üç pencerede de **0**) — iki alt-kapı toplamsal DEĞİL.

### P2 hükmü (AYI PF ≥ 1.1 **veya** AYI+YATAY PnL birlikte ↑, **VE** BOĞA PnL kaybı ≤ %20)
| Varyant | AYI PF | AYI+YATAY birlikte ↑ | BOĞA kaybı | Hüküm |
|---|---|---|---|---|
| V1 (gün-içi %1.0) | 1.33 ✓ | ✓ (+2415 / +201) | −%4.5 ✓ | **GEÇTİ** (her iki kol) |
| V1a (%0.7) | 1.52 ✓ | ✗ (YATAY −%63.5) | −%13.3 ✓ | GEÇTİ ama YATAY'ı yıkıyor → red |
| V1b (%1.5) | 1.17 ✓ | ✓ (+1247 / +570) | %0 ✓ | GEÇTİ (daha muhafazakâr) |
| **V1c (%1.3)** | **1.43 ✓** | **✓ (+3228 / +399)** | **−%2.7 ✓** | **GEÇTİ — üç pencerede de V1'i domine ediyor** |
| V2 (%15/3g) | 1.24 ✓ | ✗ (YATAY değişmedi) | %0 ✓ | GEÇTİ ama kanıt tek olaya dayanıyor |
| V2a (%10/3g) | 1.21 ✓ | ✗ | **−%24.6 ✗** | **KALDI** |
| V2b (%20/3g) | 1.24 ✓ | ✗ | %0 ✓ | V2 ile birebir aynı |
| V3 (ikisi) | 1.33 ✓ | ✓ | −%4.5 ✓ | GEÇTİ (= V1) |

### Hangi kaybı kesiyor? (yön/çıkış kırılımı — kapının asıl iddiası)
**AYI penceresi, V0 → V1 (gün-içi %1.0):**
- SL sayısı **29 → 17** (−%41), SL toplam zararı **−14907 → −8738** (+6169 kurtarıldı).
- LONG: 79 işlem / **−956** → 61 işlem / **−121** (düşen-bıçak LONG'ları kesiliyor).
- SHORT: 134 / +1541 → 88 / **+3120** (rahatlama-rallisi SHORT'ları da kesiliyor — kapı simetrik).
- Rejim kırılımı: RANGE günleri **−1029 → +425** (asıl düzelme burada; DOWN 1758 → 2686).

**AYI penceresi, V0 → V2 (uzama %15/3g):** SL 29 → 23, SHORT +1541 → **+3598**, LONG −956 → −689.

**Lider (BTCUSDT) tetik istatistiği** (pencere içi günler, `1d` serisinden türetildi):
| Pencere | gün-içi %1 LONG-blok günü | gün-içi %1 SHORT-blok günü | uzama %15/3g LONG-blok | uzama %15/3g SHORT-blok |
|---|---|---|---|---|
| AYI | 11 | 5 | 0 | **1** (2026-02-06, koşu −%20.1) |
| YATAY | 4 | 7 | 0 | 0 |
| BOĞA | 1 | 3 | 0 | 0 |
(Gün sayıları gün-SONU ölçümüdür; kapı dakika bazında değerlendirildiği için aynı gün içinde
iki yön de tetiklenebilir — bu yüzden tetik sayısı gün sayısından çok daha büyüktür.)

### Kanıtın gücü — dürüst değerlendirme
- **Gün-içi alt-kapısı (V1): kanıt orta-güçlü.** Üç pencerede de tetikleniyor (196/62/15),
  AYI'da 16 farklı güne yayılıyor, ve etkisi mekanizmayla tutarlı (SL sayısı düşüyor, hem
  düşen-bıçak LONG hem rahatlama-rallisi SHORT kesiliyor). Yine de kanıt **tek lider**
  (BTCUSDT) ve **tek 21 günlük ayı penceresi** üzerinden; AYI'daki +2415'in büyük kısmı
  02-05/02-06 çöküş-toparlanma çiftinden geliyor.
- **Uzama alt-kapısı (V2): kanıt ZAYIF.** 60 tetiğin tamamı AYI penceresinde ve **TEK bir
  lider olayından** (2026-02-06, 3 günlük −%20.1 koşu) geliyor; %15 ile %20 eşikleri
  **birebir aynı** sonucu veriyor (arada hiç olay yok), YATAY ve BOĞA'da hiç tetiklenmiyor.
  n=1 olay = istatistik değil, anekdot. Ayrıca %10'a gevşetmek BOĞA'yı −%24.6 ile P2'den
  düşürüyor (08-20'de BTC'nin +%10.2'lik 3 günlük koşusu LONG'ları vetoluyor) — yani eşik
  duyarlılığı yüksek ve yanlış tarafa ayarlanırsa doğrudan zarar veriyor.
- **V3 ≡ V1** olduğu için uzama alt-kapısını gün-içi ile BİRLİKTE açmanın ölçülebilir hiçbir
  faydası yok; tek başına açmanın da (V2) faydası tek olaya dayanıyor.
- Simülatörün mutlak sayıları rejime duyarlıdır (P3); yukarıdaki hüküm **göreli** farklara
  dayanır ve canlı defter nihai hakemdir.

### E8 (sinyal otopsisi) ile çapraz kontrol — 2026-08-23
E8 ajanı kapıyı BAĞIMSIZ olarak, harness JSON'u üzerinde **post-hoc** ölçtü (her işlemi giriş
zamanıyla zenginleştirip filtreleyerek). İki yöntem farklı şeyler ölçüyor ve bu fark önemli:

| | E7 (bu bölüm) | E8 (post-hoc) |
|---|---|---|
| Kapı nerede | Motor-içi, gerçek `simulate_symbol` kapısı | İşlem listesi üzerinde filtre |
| Kapasite | Engellenen sinyal slotu SERBEST bırakır (P1 kapasite paritesi) | Bırakmaz → **alt sınır** |
| YATAY %1.0 sonucu | **+201** | **−487** |
İşaret farkının kaynağı kapasite yeniden tahsisi: gerçek motorda engellenen bir sinyal slotu
boşaltır ve sonraki (çoğu kez daha iyi) sinyal girebilir. E8 bunu kendisi de "gerçek motor-içi
kapının ALT SINIRI" diye işaretledi — iki ölçüm çelişmiyor, E8 muhafazakâr taraftan bakıyor.

**E8'in eşik önerisi (%1.3) motor-içi kapıyla DOĞRULANDI ve benimsendi:** V1c üç pencerede de
V1'i (%1.0) domine ediyor — AYI +2999→**+3812** (PF 1.33→1.43), YATAY +2593→**+2791**
(1.36→1.38), BOĞA kaybı −%4.5→**−%2.7**; maxDD hiçbir pencerede kötüleşmiyor.
V1c AYI yön kırılımı: LONG 64/**+180** (PF 1.04), SHORT 94/**+3632** (1.84), SL 17 (V0: 29).
E8'in "kazancın tamamına yakını SHORT bacağından" gözlemi burada da görünüyor; ancak E7'de LONG
bacağı da tabana göre iyileşiyor (V0 79/−956 → V1c 64/+180), yani E7 LONG bacağını E8'den daha
değerli buluyor — yine kapasite etkisi. **Bacak-ayrık eşik (SHORT %1.0 / LONG %1.3) uygulanmadı:**
ayrı bir tasarım kararı, kendi spec'i ve onayı gerekir; E7 verisi LONG bacağının işe yaramadığı
iddiasını DESTEKLEMİYOR.

**Uzama alt-kapısı — ikinci bağımsız RED.** E8 canlı defterde net **negatif** ölçtü (−152.7;
LONG eşiği %12'de −382.9, 50 kazanan engelleniyor) ve spec'in hipotezinin İŞARETİNİ ters buldu
(kazananların `align_btc_run_3d` ort. 7.50 / kaybedenlerin 2.28; AUC 0.292, p<0.001 — yani koşuyla
AYNI yönde açılan işlemler kazanıyor). E7'de zaten "n=1 olay, gün-içinin üstüne katkı yok"
demiştik. İki bağımsız kanıt aynı yöne işaret ediyor → **`SCALPER_MARKET_GATE_RUN_PCT=0` kalmalı.**
Varsayılan spec'te 15 onaylandığı için sessizce değiştirilmedi; bunun yerine motor açılışta
uyarıyor (`ScalperEngine._maybe_log_market_gate_banner`).

### "Gün açılışı" tanımı — ölçülmüş eşdeğerlik ve testnet uyarısı
E8 ölçümünü gerçek `1d` mumu **open**'ı ile yaptı; bu uygulama son tamamlanmış günlük
**close**'u vekil kullanıyor (gerekçe: `KlineFetcher._drop_unclosed` oluşmakta olan günlük mumu
her zaman atar → canlıda "bugünün open'ı" ELDE EDİLEMEZ; bkz. D15). İki tanım arasındaki fark
ÖLÇÜLDÜ (BTCUSDT, üç pencere, 70 gün, mainnet — harness'ın veri kaynağı):

| Kaynak | Ortalama \|open−önceki close\| | Maksimum | Eşiğin (%1.0) kaçta kaçı |
|---|---|---|---|
| **Mainnet** (harness) | %0.000082 | %0.000597 | %0.06 |
| **Testnet** (canlı motor) | %0.013 | %0.152 | **%15.2** |

Mainnet'te iki tanım pratikte AYNI (fark eşiğin binde 6'sı) → E7 tablosu E8'in tanımıyla da
geçerlidir. Testnet'te fark ~200× büyük: en kötü günde eşiğin %15'i kadar. **Bilinen sapma:**
testnet soak'unda kapı, harness'ın ölçtüğünden marjinal günlerde farklı karar verebilir. Gerçek
open'a geçmek için ucuz ve parite-korur bir yol bulunamadı (`1h` mumu da 00:00-01:00 UTC arası
kapanmamış olduğu için düşer → saat başında referans değiştiren, harness'ın taklit edemeyeceği
canlı-only bir süreksizlik doğardı; `_drop_unclosed`'ı gevşetmek ise tüm motorun paylaştığı
repaint korumasını zayıflatır). Mainnet'te — gerçek paranın çalışacağı yer — sapma ihmal
edilebilir olduğu için mevcut vekil bilinçle korundu.
