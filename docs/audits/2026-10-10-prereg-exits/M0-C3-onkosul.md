# M0 kapısı (C3 `TP_PLAN_MODE=strength`) — SONUÇ: GEÇMEDİ, C3 KODLANMADI

Plan §2 C3: dev pencerelerinde tüm işlemler için Spearman rho(score, MFE_R) >= 0,10 VE tek-yanlı p < 0,05
VE tercile ortalama MFE_R monoton artan. Üçü birden gerekir.

## Koşul
- Kod: wt-code (exp/exit-variants, taban 1fa17f1 + C1/C2 + M0 ölçüm alanları), YENİ MOD YOK (varsayılan).
- Env: server-scalper-env.txt (B0, override yok). Python .venv 3.12. Cache: data/klines_cache. Sıralı koşu.
- Pencereler (lib.sh WIN): AYI 2026-01-23→02-13, YATAY 2026-07-01→07-21, BOGA 2026-08-07→08-21.
- Belirlenimcilik: B0_AYI = 149 işlem / %87,2 / PF 2,03 (taban logla birebir). YATAY 136 işlem, BOGA 101 işlem.
- Ham çıktı: prereg/m0/B0_{AYI,YATAY,BOGA}.json (+ .log), analiz: m0_analyze.py -> m0_result.json, run_m0.sh.
- MFE_R = mfe_pct / stop_ROI; stop_ROI = |entry - stop_price| / entry * leverage * 100 (stop_price = dolum-çapalı
  ilk stop). B0'da stop_ROI her işlemde 50,0 (min=medyan=maks=50,0) => MFE_R = mfe_pct/50.
- p-değeri: tek-yanlı permütasyon testi, 200 000 permütasyon, seed 20261010, (sayım+1)/(K+1).

## Sonuç (n = 386 işlem)
| Ölçü | Değer | Şart | Durum |
|---|---|---|---|
| Spearman rho(score, MFE_R) | -0,0155 | >= 0,10 | GEÇMEDİ |
| Tek-yanlı p | 0,621 | < 0,05 | GEÇMEDİ |
| Tercile ort. MFE_R (düşük / orta / yüksek skor) | 0,2913 / 0,2676 / 0,2814 | monoton artan | GEÇMEDİ (düşük > orta) |

Tercile kesimleri (bilgi; DONDURULMADI çünkü kapı geçmedi): q1 = 1,929, q2 = 4,224; n = 129 / 128 / 129.
Tercile medyan MFE_R: 0,2386 / 0,2242 / 0,2520.
Pencere bazında rho: AYI +0,025 (n=149), YATAY +0,029 (n=136), BOGA -0,063 (n=101). Hiçbiri 0,10'a yakın değil.
Skor aralığı: min 0,012, medyan 2,916, maks 20,14.

## Karar
Şart sağlanmadı: skorun MFE_R ile ilişkisi sıfırdan ayırt edilemiyor (rho ~ 0, işareti bile tutarsız).
PLAN'a göre C3 KODLANMAZ, doğrulama tablosuna p = 1,0 girilir. Kodda yalnız ölçüm alanları kaldı:
`BacktestTrade.score` ve `BacktestTrade.stop_price` (to_dict'e yazılır; hiçbir karar yolu okumaz).

Not (karar kuralını değiştirmez): stop_ROI tüm işlemlerde sabit olduğundan MFE_R, mfe_pct'nin sabit bir katıdır;
Spearman rank-tabanlı olduğu için E2 stopuyla (S=20) da rho aynı kalırdı (aynı işlem kümesi için).
Sinyal kümesi E2'de farklı olabilir, ama M0 B0 env ile tanımlıdır (görev talimatı).
