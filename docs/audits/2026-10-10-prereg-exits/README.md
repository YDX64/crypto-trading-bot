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
| `artifacts.tgz` | 121 backtest + 9 veri doldurma logu, `windows_frozen/gates/pvals/bh_out/dev_screen.json`, C1/C2 kod yaması (`exit-variants-C1C2.patch`, taban `1fa17f1`, main'e GİRMEDİ), `m0/` (M0 girdileri: 3 B0 raporu, logları, `m0_analyze.py`, `m0_result.json`, `run_m0.sh`) |

**Bilerek dışarıda:** taban B0'ın `SCALPER_*` env dosyası (sunucu `.env`'inden, 2026-10-10).
Herkese açık repoya konmadı. Yeniden üretmek için:
`ssh awa "grep '^SCALPER_' /opt/tradingbot-v2/.env | grep -v '^SCALPER_AI_GATE_'"` çıktısını bir dosyaya yaz
ve `lib.sh`'teki `ENVF` yolunu ona çevir. Aynı nedenle `m0/B0_*.json` raporlarından
`provenance.scalper_config` (tam ayar dökümü) çıkarıldı; işlem verisi aynen duruyor. Betiklerdeki mutlak yollar
bulut oturumunun karalama dizinidir.

Veri: data.binance.vision 1m/5m/15m/1d arşivi, sha256 doğrulamalı
(`scripts/fill_klines_cache_from_vision.py`).

## Bilinen sınırlar — bu koşuyu etkilemedi (cubic incelemesi, PR #8)

Arşivdeki betikler deneyin **gerçekte çalışan** halidir; `analyze_prereg.py`'nin özeti kilitlidir.
Sonradan değiştirilmeleri kaydı bozar. Aşağıdaki sınırlar bu yüzden kodda değil burada kayıtlıdır.
Her biri için bu koşunun sonucunu etkilemediğinin kanıtı yanında. Betik yeniden kullanılacaksa
önce bunlar düzeltilmelidir.

| Sınır | Bu koşuda etkisi |
|---|---|
| `bootstrap_p` merkezlenmemiş yüzdelik bootstrap'tır: P*(ort.* ≤ 0); tam kalibre bir H0: ort. ≤ 0 testi değil. Blok uzunluğu tek gündür; günler arası bağımlılık p'yi iyimser gösterebilir | Yok. 9 varyantın gözlenen günlük ortalamasının **hepsi negatif**, p ≥ 0.976. Geçerli herhangi bir tek yönlü test negatif ortalamada p ≥ ~0.5 verir; q ≤ 0.10'a yaklaşamaz. Ayrıca G10'a gelmeden her varyant G1–G5 ve G7'den kaldı |
| Kilit uyuşmazlığı yalnız raporlanır, `decide` durmaz; `final` kilidi yeniden denetlemez | Yok. `DECISION.md`: iki kilit de UYUŞUYOR. KAZANAN çıkmadı, `final` hiç koşmadı |
| `pvals --n-boot/--seed` bayrakları p hesabını değiştirebilir | Yok. `f1b_f4.sh` `pvals`'ı bayraksız çağırdı, yani varsayılanlar 20000 / 20261010 |
| `fill_win`, `run_bt`, `f0_f1.sh`, `f1b_f4.sh` hatayı yalnız loglar, çıkış koduyla durmaz; başarısız tekrar koşu eski `runs/*.json`'ı yerinde bırakabilir | Yok. İki zincir çıktısında **0** `HATA` satırı; 9 doldurma ve 121 koşunun hepsi `OK` (`runs/` altında da 121 dosya). `runs/` altındaki en eski dosya 05:15Z, zincir 04:08Z'de başladı: bayat dosya yok |
| B0 AYI belirlenimcilik duman testi otomatik karşılaştırılmaz | Yok. Elle karşılaştırıldı: 149 / %87.2 / PF 2.03, önceki ölçümle birebir (`bt-logs/B0_AYI.log`) |
| C2 kısmi oranları da değiştirdi (%50/%25; E2 %40/%20) | C2 bir hedef + oran birleşik testidir; plan "yalnız TP kuralını izole eder" diyordu. Hüküm etkilenmez |
| `DECISION.md` PF'leri 2 haneye yuvarlar: E5 V1 "1.00" aslında 1.0043, PF>1 sayılır | Yalnız okunabilirlik. Tam değerler `results.csv` (`pf` sütunu, 4 hane) |
