# E16 — Ön-kayıtlı çıkış deneyi (2026-10-10) — kayıt klasörü

Sonuç ve yorum: `docs/EXPERIMENTS.md` → **E16**. Bu klasör kanıtın kendisidir.

| Dosya | Ne |
|---|---|
| `PLAN.md` (+`PLAN.sha256`) | Sonuçlar görülmeden kilitlenen plan: 11 varyant, 8 taze 2025 penceresi, G1–G10 kapıları |
| `analyze_prereg.py` (+`ANALYZE.sha256`, `ANALYZE_CHANGES.md`) | Karar betiği; doğrulama sonucu yokken düzeltilip yeniden kilitlendi (değişiklik notu ekte) |
| `test_analyze_prereg.py` | Karar betiğinin testleri (38). `tests/` dışında: CI bunları koşmaz |
| `M0-C3-onkosul.md` | C3 (sinyal gücüne göre TP) ön-koşul ölçümü — başarısız, C3 kodlanmadı |
| `lib.sh`, `f0_f1.sh`, `f1b_f4.sh` | Koşu zinciri (veri doldurma → DEV elemesi → doğrulama → karar) |
| `DECISION.md` | Otomatik karar raporu: **KAZANAN YOK** |
| `results.csv` | Her koşunun özeti (varyant × pencere) |
| `artifacts.tgz` | 131 backtest logu, `windows_frozen/gates/pvals/bh_out/dev_screen.json`, C1/C2 kod yaması (`exit-variants-C1C2.patch`, taban `1fa17f1`, main'e GİRMEDİ) |

**Bilerek dışarıda:** taban B0'ın `SCALPER_*` env dosyası (sunucu `.env`'inden, 2026-10-10).
Herkese açık repoya konmadı. Yeniden üretmek için: `ssh awa grep ^SCALPER_ /opt/tradingbot-v2/.env`
(AI_GATE satırları hariç) ve `lib.sh`'teki `ENVF` yolunu ona çevir. Betiklerdeki mutlak yollar
bulut oturumunun karalama dizinidir.

Veri: data.binance.vision 1m/5m/15m/1d arşivi, sha256 doğrulamalı
(`scripts/fill_klines_cache_from_vision.py`).
