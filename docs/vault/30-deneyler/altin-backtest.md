---
tags: [deneyler, golden, regresyon, test]
guncelleme: 2026-08-24
kaynak: docs/EXPERIMENTS.md "Altin (golden) backtest regresyonu" (satir 108), tests/test_golden_backtest.py
---
# Altin (golden) backtest regresyonu

**Amac.** `src/strategies/scalper/backtest.py` motorunu **AGSIZ ve
deterministik** bir sonuca kilitlemek — davranis degisince (kasitli ya da
kaza) test kirilir.

**Kod.** `src/strategies/scalper/kline_cache.py` (+`--cache-dir`/`--refresh`
CLI bayraklari), fixture `tests/fixtures/klines/` (6 dosya, ~104 KB, gzip
JSON, BTCUSDT+ETHUSDT × 5m/15m).
**Pencere.** 2026-08-07 → 2026-08-10 UTC (3 gun). 1m HIC kullanilmaz.

## Altin degerler

| Metrik | Deger |
|---|---|
| Toplam islem | **2** (ikisi de BTCUSDT) |
| Net PnL | **+26.77** |
| Yon | LONG 2 |
| Cikis nedeni | TRAIL 2 |
| Kapi reddi | `regime_gate` **4** |
| Kosum suresi | ~2 sn (3 test, AGSIZ) |

ETHUSDT bu dar pencerede **hic ham sinyal uretmedi** (RSI 30/70 + BB tasmasi +
zorunlu diverjans uclusu ust uste binmedi) — **hata DEGIL**, gercek veri.

## ⚠️ Kayitli bulgu: `SCALPER_MIN_RR` varsayilani kullanilamaz

`fixed_roi` modunda RR kapisi `sl_risk_roi`'yi HER ZAMAN tam
`SCALPER_FIXED_STOP_ROI_PCT`'e sadeler (kaldirac iptal olur), yani
`rr = (10×0.8 + 25×0.20) / 50 = 0.26` — **piyasa verisinden bagimsiz SABIT**.
Sinif varsayilani **1.2** ile HICBIR C sinyali gecemez. Golden test
`SCALPER_MIN_RR=0.0` varsayimiyla yazildi; **canli `.env` MIN_RR=0**'dir.

## Kosma

```bash
python3 -m pytest tests/test_golden_backtest.py -q
```
Iki ayri surecte birebir ayni sonuc (determinism testi fingerprint
karsilastirir). Harness zincirinde `time.time()`/rastgelelik/siralamaya-duyarli
set **yok**.

## Kural

Altin sayilari degistiren bir degisiklik **bilincli** olmalidir ve
`docs/EXPERIMENTS.md`'ye not dusulmelidir. D24 paketinde degismedi —
bu, "yalniz olcum" iddiasinin kanitidir.

ILGILI: [[20-kararlar/D24-olcum-paketi]] · [[20-kararlar/P1-harness-parite]] · [[90-ai-icin/dogrulama-receteleri]]
