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
