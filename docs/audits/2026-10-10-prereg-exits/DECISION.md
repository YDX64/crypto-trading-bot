# KARAR (ön-kayıtlı plan, otomatik üretim)

- Üretim: 2026-10-10 06:59:20Z
- PLAN.md kilidi: UYUŞUYOR (fe9b83548ff8…)
- analyze_prereg.py kilidi: UYUŞUYOR (32df0bc1c622…)
- Kullanılabilir doğrulama penceresi: 8/8 = V1, V2, V3, V4, V5, V6, V7, V8

## Dondurulmuş rejim etiketleri
| Pencere | Aralık | BTC getirisi | Etiket | Şok | Maks |günlük| |
|---|---|---|---|---|---|
| V1 | 2025-01-06→2025-01-27 | +4.29% | FLAT | - | 5.15% (2025-01-07) |
| V2 | 2025-02-17→2025-03-10 | -16.01% | DOWN | EVET | 9.55% (2025-03-02) |
| V3 | 2025-04-14→2025-05-05 | +12.56% | UP | EVET | 6.79% (2025-04-22) |
| V4 | 2025-05-12→2025-06-02 | +1.45% | FLAT | - | 3.93% (2025-05-23) |
| V5 | 2025-07-07→2025-07-28 | +9.42% | UP | - | 4.30% (2025-07-10) |
| V6 | 2025-08-18→2025-09-08 | -5.35% | DOWN | - | 3.94% (2025-08-22) |
| V7 | 2025-10-06→2025-10-27 | -7.23% | DOWN | EVET | 7.29% (2025-10-10) |
| V8 | 2025-11-03→2025-11-24 | -21.44% | DOWN | - | 5.36% (2025-11-20) |

## Taban B0
- işlem 1619, WR %76.0, ΣPnL -29477, havuz PF 0.773, medyan PF 0.807, en kötü PF 0.632, Calmar -0.616
- G1 [YANLIŞ] PF>1 olan pencere >= 6/8 — 1/8 pencere PF>1; PF'ler V1=0.86, V2=1.02, V3=0.81, V4=0.63, V5=0.80, V6=0.65, V7=0.66, V8=0.91
- G2 [YANLIŞ] Havuz PF >= 1.15 — havuz PF=0.773
- G3 [YANLIŞ] Medyan PF >= 1.1 — medyan=0.807
- G4 [YANLIŞ] En kötü pencere PF >= 0.7 — min=0.632
- G5 [YANLIŞ] B0: pencere DD <= 2000 ve Calmar > 0 — maks DD=9484.8; Calmar=-0.616
- G6 [DOĞRU] Toplam işlem >= 400 ve >= B0'ın 60%'ı — işlem=1619 (eşik max(400, 971); B0=1619)
- G7 [YANLIŞ] Her rejim grubunda havuz PF >= 0.9 — UP[V3,V5] havuz PF=0.807; DOWN[V2,V6,V7,V8] havuz PF=0.787; FLAT[V1,V4] havuz PF=0.720
- **B0 G1-G7: GEÇMEDİ**

## Özet
| Varyant | Durum | İşlem | Havuz PF | Medyan | Min | Calmar | G1 | G2 | G3 | G4 | G5 | G6 | G7 | G8 | G9 | G10 | Sonuç |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| E1 | BOOTSTRAP | 1513 | 0.849 | 0.830 | 0.691 | -0.526 | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ | ✗ | ✓ | – | ✗ | KALDI |
| E2 | BOOTSTRAP | 1327 | 0.811 | 0.777 | 0.656 | -0.527 | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ | ✗ | ✗ | – | ✗ | KALDI |
| E3 | BOOTSTRAP | 1104 | 0.825 | 0.783 | 0.624 | -0.497 | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ | ✗ | ✓ | – | ✗ | KALDI |
| E4 | BOOTSTRAP | 1214 | 0.787 | 0.746 | 0.547 | -0.553 | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ | ✗ | ✗ | – | ✗ | KALDI |
| E5 | BOOTSTRAP | 1196 | 0.773 | 0.734 | 0.559 | -0.554 | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ | ✗ | ✗ | – | ✗ | KALDI |
| E6 | BOOTSTRAP | 616 | 0.708 | 0.715 | 0.344 | -0.568 | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | – | ✗ | KALDI |
| E7 | BOOTSTRAP | 1707 | 0.777 | 0.768 | 0.695 | -0.673 | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ | ✗ | ✗ | – | ✗ | KALDI |
| E8 | BOOTSTRAP | 1327 | 0.810 | 0.766 | 0.661 | -0.535 | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ | ✗ | ✗ | – | ✗ | KALDI |
| C1 | ELENDI_DEV | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | ELENDİ (dev havuz PF 0.781 < 1.0) |
| C2 | BOOTSTRAP | 1497 | 0.749 | 0.702 | 0.586 | -0.616 | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ | ✗ | ✗ | – | ✗ | KALDI |
| C3 | KODLANMADI | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | ELENDİ (kodlanmadı (önkoşul sağlanmadı / kod yok)) |

## Varyant varyant kapılar (G1–G10)
### E1 — KALDI
- G1 [YANLIŞ] PF>1 olan pencere >= 6/8 — 1/8 pencere PF>1; PF'ler V1=0.69, V2=1.17, V3=0.87, V4=0.76, V5=0.92, V6=0.76, V7=0.79, V8=0.97
- G2 [YANLIŞ] Havuz PF >= 1.15 — havuz PF=0.849
- G3 [YANLIŞ] Medyan PF >= 1.1 — medyan=0.830
- G4 [YANLIŞ] En kötü pencere PF >= 0.7 — min=0.691
- G5 [YANLIŞ] bar_max_drawdown <= 2000/pencere; Calmar>0 ve > B0 — maks pencere DD=4043.0 (aşan: V1=3712, V3=2305, V4=4043, V5=2090, V6=2633, V7=2573, V8=2549); Calmar=-0.526 (B0=-0.616)
- G6 [DOĞRU] Toplam işlem >= 400 ve >= B0'ın 60%'ı — işlem=1513 (eşik max(400, 971); B0=1619)
- G7 [YANLIŞ] Her rejim grubunda havuz PF >= 0.9 — UP[V3,V5] havuz PF=0.893; DOWN[V2,V6,V7,V8] havuz PF=0.899; FLAT[V1,V4] havuz PF=0.725
- G8 [DOĞRU] Havuz PF >= B0 + 0.05 — havuz PF=0.849 vs B0=0.773 (eşik 0.823)
- G9 [UYGULANMAZ] fee-stress ve gecikme-1 havuz PF >= 1.05 — G1-G8 geçmedi; G9 yalnız geçenlere uygulanır
- G10 [YANLIŞ] BH rejected (q <= 0.1) — p=0.981251, q=1.000000, durum=BOOTSTRAP

### E2 — KALDI
- G1 [YANLIŞ] PF>1 olan pencere >= 6/8 — 1/8 pencere PF>1; PF'ler V1=0.67, V2=1.35, V3=0.99, V4=0.71, V5=0.80, V6=0.66, V7=0.75, V8=0.86
- G2 [YANLIŞ] Havuz PF >= 1.15 — havuz PF=0.811
- G3 [YANLIŞ] Medyan PF >= 1.1 — medyan=0.777
- G4 [YANLIŞ] En kötü pencere PF >= 0.7 — min=0.656
- G5 [YANLIŞ] bar_max_drawdown <= 2000/pencere; Calmar>0 ve > B0 — maks pencere DD=5143.3 (aşan: V1=4237, V3=2746, V4=5143, V5=2951, V6=4247, V7=3965, V8=3252); Calmar=-0.527 (B0=-0.616)
- G6 [DOĞRU] Toplam işlem >= 400 ve >= B0'ın 60%'ı — işlem=1327 (eşik max(400, 971); B0=1619)
- G7 [YANLIŞ] Her rejim grubunda havuz PF >= 0.9 — UP[V3,V5] havuz PF=0.889; DOWN[V2,V6,V7,V8] havuz PF=0.848; FLAT[V1,V4] havuz PF=0.689
- G8 [YANLIŞ] Havuz PF >= B0 + 0.05 — havuz PF=0.811 vs B0=0.773 (eşik 0.823)
- G9 [UYGULANMAZ] fee-stress ve gecikme-1 havuz PF >= 1.05 — G1-G8 geçmedi; G9 yalnız geçenlere uygulanır
- G10 [YANLIŞ] BH rejected (q <= 0.1) — p=0.990450, q=1.000000, durum=BOOTSTRAP

### E3 — KALDI
- G1 [YANLIŞ] PF>1 olan pencere >= 6/8 — 1/8 pencere PF>1; PF'ler V1=0.78, V2=1.62, V3=0.94, V4=0.71, V5=0.75, V6=0.79, V7=0.62, V8=0.93
- G2 [YANLIŞ] Havuz PF >= 1.15 — havuz PF=0.825
- G3 [YANLIŞ] Medyan PF >= 1.1 — medyan=0.783
- G4 [YANLIŞ] En kötü pencere PF >= 0.7 — min=0.624
- G5 [YANLIŞ] bar_max_drawdown <= 2000/pencere; Calmar>0 ve > B0 — maks pencere DD=5016.4 (aşan: V1=2565, V3=3017, V4=4218, V5=3172, V6=2767, V7=5016, V8=2173); Calmar=-0.497 (B0=-0.616)
- G6 [DOĞRU] Toplam işlem >= 400 ve >= B0'ın 60%'ı — işlem=1104 (eşik max(400, 971); B0=1619)
- G7 [YANLIŞ] Her rejim grubunda havuz PF >= 0.9 — UP[V3,V5] havuz PF=0.843; DOWN[V2,V6,V7,V8] havuz PF=0.868; FLAT[V1,V4] havuz PF=0.736
- G8 [DOĞRU] Havuz PF >= B0 + 0.05 — havuz PF=0.825 vs B0=0.773 (eşik 0.823)
- G9 [UYGULANMAZ] fee-stress ve gecikme-1 havuz PF >= 1.05 — G1-G8 geçmedi; G9 yalnız geçenlere uygulanır
- G10 [YANLIŞ] BH rejected (q <= 0.1) — p=0.975701, q=1.000000, durum=BOOTSTRAP

### E4 — KALDI
- G1 [YANLIŞ] PF>1 olan pencere >= 6/8 — 1/8 pencere PF>1; PF'ler V1=0.97, V2=1.23, V3=0.93, V4=0.68, V5=0.72, V6=0.55, V7=0.69, V8=0.77
- G2 [YANLIŞ] Havuz PF >= 1.15 — havuz PF=0.787
- G3 [YANLIŞ] Medyan PF >= 1.1 — medyan=0.746
- G4 [YANLIŞ] En kötü pencere PF >= 0.7 — min=0.547
- G5 [YANLIŞ] bar_max_drawdown <= 2000/pencere; Calmar>0 ve > B0 — maks pencere DD=6799.9 (aşan: V1=2606, V2=2107, V3=3195, V4=6800, V5=4317, V6=6354, V7=3824, V8=4310); Calmar=-0.553 (B0=-0.616)
- G6 [DOĞRU] Toplam işlem >= 400 ve >= B0'ın 60%'ı — işlem=1214 (eşik max(400, 971); B0=1619)
- G7 [YANLIŞ] Her rejim grubunda havuz PF >= 0.9 — UP[V3,V5] havuz PF=0.816; DOWN[V2,V6,V7,V8] havuz PF=0.762; FLAT[V1,V4] havuz PF=0.804
- G8 [YANLIŞ] Havuz PF >= B0 + 0.05 — havuz PF=0.787 vs B0=0.773 (eşik 0.823)
- G9 [UYGULANMAZ] fee-stress ve gecikme-1 havuz PF >= 1.05 — G1-G8 geçmedi; G9 yalnız geçenlere uygulanır
- G10 [YANLIŞ] BH rejected (q <= 0.1) — p=0.996950, q=1.000000, durum=BOOTSTRAP

### E5 — KALDI
- G1 [YANLIŞ] PF>1 olan pencere >= 6/8 — 2/8 pencere PF>1; PF'ler V1=1.00, V2=1.33, V3=0.86, V4=0.77, V5=0.59, V6=0.56, V7=0.61, V8=0.70
- G2 [YANLIŞ] Havuz PF >= 1.15 — havuz PF=0.773
- G3 [YANLIŞ] Medyan PF >= 1.1 — medyan=0.734
- G4 [YANLIŞ] En kötü pencere PF >= 0.7 — min=0.559
- G5 [YANLIŞ] bar_max_drawdown <= 2000/pencere; Calmar>0 ve > B0 — maks pencere DD=6157.0 (aşan: V1=2774, V2=2028, V3=3500, V4=5938, V5=5573, V6=6157, V7=5000, V8=4845); Calmar=-0.554 (B0=-0.616)
- G6 [DOĞRU] Toplam işlem >= 400 ve >= B0'ın 60%'ı — işlem=1196 (eşik max(400, 971); B0=1619)
- G7 [YANLIŞ] Her rejim grubunda havuz PF >= 0.9 — UP[V3,V5] havuz PF=0.714; DOWN[V2,V6,V7,V8] havuz PF=0.746; FLAT[V1,V4] havuz PF=0.873
- G8 [YANLIŞ] Havuz PF >= B0 + 0.05 — havuz PF=0.773 vs B0=0.773 (eşik 0.823)
- G9 [UYGULANMAZ] fee-stress ve gecikme-1 havuz PF >= 1.05 — G1-G8 geçmedi; G9 yalnız geçenlere uygulanır
- G10 [YANLIŞ] BH rejected (q <= 0.1) — p=0.997350, q=1.000000, durum=BOOTSTRAP

### E6 — KALDI
- G1 [YANLIŞ] PF>1 olan pencere >= 6/8 — 2/8 pencere PF>1; PF'ler V1=0.71, V2=1.34, V3=0.72, V4=0.79, V5=0.64, V6=1.03, V7=0.34, V8=0.62
- G2 [YANLIŞ] Havuz PF >= 1.15 — havuz PF=0.708
- G3 [YANLIŞ] Medyan PF >= 1.1 — medyan=0.715
- G4 [YANLIŞ] En kötü pencere PF >= 0.7 — min=0.344
- G5 [YANLIŞ] bar_max_drawdown <= 2000/pencere; Calmar>0 ve > B0 — maks pencere DD=5446.4 (aşan: V1=2901, V3=3869, V4=2871, V5=2822, V7=5446); Calmar=-0.568 (B0=-0.616)
- G6 [YANLIŞ] Toplam işlem >= 400 ve >= B0'ın 60%'ı — işlem=616 (eşik max(400, 971); B0=1619)
- G7 [YANLIŞ] Her rejim grubunda havuz PF >= 0.9 — UP[V3,V5] havuz PF=0.683; DOWN[V2,V6,V7,V8] havuz PF=0.697; FLAT[V1,V4] havuz PF=0.757
- G8 [YANLIŞ] Havuz PF >= B0 + 0.05 — havuz PF=0.708 vs B0=0.773 (eşik 0.823)
- G9 [UYGULANMAZ] fee-stress ve gecikme-1 havuz PF >= 1.05 — G1-G8 geçmedi; G9 yalnız geçenlere uygulanır
- G10 [YANLIŞ] BH rejected (q <= 0.1) — p=0.995750, q=1.000000, durum=BOOTSTRAP

### E7 — KALDI
- G1 [YANLIŞ] PF>1 olan pencere >= 6/8 — 0/8 pencere PF>1; PF'ler V1=0.72, V2=0.91, V3=0.90, V4=0.70, V5=0.82, V6=0.71, V7=0.71, V8=0.82
- G2 [YANLIŞ] Havuz PF >= 1.15 — havuz PF=0.777
- G3 [YANLIŞ] Medyan PF >= 1.1 — medyan=0.768
- G4 [YANLIŞ] En kötü pencere PF >= 0.7 — min=0.695
- G5 [YANLIŞ] bar_max_drawdown <= 2000/pencere; Calmar>0 ve > B0 — maks pencere DD=6734.7 (aşan: V1=4662, V2=5165, V3=2739, V4=6735, V5=3725, V6=4910, V7=4858, V8=6144); Calmar=-0.673 (B0=-0.616)
- G6 [DOĞRU] Toplam işlem >= 400 ve >= B0'ın 60%'ı — işlem=1707 (eşik max(400, 971); B0=1619)
- G7 [YANLIŞ] Her rejim grubunda havuz PF >= 0.9 — UP[V3,V5] havuz PF=0.862; DOWN[V2,V6,V7,V8] havuz PF=0.778; FLAT[V1,V4] havuz PF=0.705
- G8 [YANLIŞ] Havuz PF >= B0 + 0.05 — havuz PF=0.777 vs B0=0.773 (eşik 0.823)
- G9 [UYGULANMAZ] fee-stress ve gecikme-1 havuz PF >= 1.05 — G1-G8 geçmedi; G9 yalnız geçenlere uygulanır
- G10 [YANLIŞ] BH rejected (q <= 0.1) — p=0.999850, q=1.000000, durum=BOOTSTRAP

### E8 — KALDI
- G1 [YANLIŞ] PF>1 olan pencere >= 6/8 — 2/8 pencere PF>1; PF'ler V1=0.67, V2=1.34, V3=1.01, V4=0.72, V5=0.80, V6=0.66, V7=0.73, V8=0.84
- G2 [YANLIŞ] Havuz PF >= 1.15 — havuz PF=0.810
- G3 [YANLIŞ] Medyan PF >= 1.1 — medyan=0.766
- G4 [YANLIŞ] En kötü pencere PF >= 0.7 — min=0.661
- G5 [YANLIŞ] bar_max_drawdown <= 2000/pencere; Calmar>0 ve > B0 — maks pencere DD=5106.8 (aşan: V1=4198, V3=2705, V4=5107, V5=2858, V6=4188, V7=3951, V8=3283); Calmar=-0.535 (B0=-0.616)
- G6 [DOĞRU] Toplam işlem >= 400 ve >= B0'ın 60%'ı — işlem=1327 (eşik max(400, 971); B0=1619)
- G7 [YANLIŞ] Her rejim grubunda havuz PF >= 0.9 — UP[V3,V5] havuz PF=0.897; DOWN[V2,V6,V7,V8] havuz PF=0.836; FLAT[V1,V4] havuz PF=0.695
- G8 [YANLIŞ] Havuz PF >= B0 + 0.05 — havuz PF=0.810 vs B0=0.773 (eşik 0.823)
- G9 [UYGULANMAZ] fee-stress ve gecikme-1 havuz PF >= 1.05 — G1-G8 geçmedi; G9 yalnız geçenlere uygulanır
- G10 [YANLIŞ] BH rejected (q <= 0.1) — p=0.992300, q=1.000000, durum=BOOTSTRAP

### C1 — ELENDI
- p=1.0, ELENDI_DEV: dev havuz PF 0.781 < 1.0

### C2 — KALDI
- G1 [YANLIŞ] PF>1 olan pencere >= 6/8 — 1/8 pencere PF>1; PF'ler V1=0.72, V2=1.41, V3=0.94, V4=0.67, V5=0.69, V6=0.59, V7=0.61, V8=0.83
- G2 [YANLIŞ] Havuz PF >= 1.15 — havuz PF=0.749
- G3 [YANLIŞ] Medyan PF >= 1.1 — medyan=0.702
- G4 [YANLIŞ] En kötü pencere PF >= 0.7 — min=0.586
- G5 [YANLIŞ] bar_max_drawdown <= 2000/pencere; Calmar>0 ve > B0 — maks pencere DD=4199.3 (aşan: V1=2562, V3=2508, V4=4196, V5=3079, V6=4199, V7=3153); Calmar=-0.616 (B0=-0.616)
- G6 [DOĞRU] Toplam işlem >= 400 ve >= B0'ın 60%'ı — işlem=1497 (eşik max(400, 971); B0=1619)
- G7 [YANLIŞ] Her rejim grubunda havuz PF >= 0.9 — UP[V3,V5] havuz PF=0.807; DOWN[V2,V6,V7,V8] havuz PF=0.752; FLAT[V1,V4] havuz PF=0.690
- G8 [YANLIŞ] Havuz PF >= B0 + 0.05 — havuz PF=0.749 vs B0=0.773 (eşik 0.823)
- G9 [UYGULANMAZ] fee-stress ve gecikme-1 havuz PF >= 1.05 — G1-G8 geçmedi; G9 yalnız geçenlere uygulanır
- G10 [YANLIŞ] BH rejected (q <= 0.1) — p=0.999350, q=1.000000, durum=BOOTSTRAP

### C3 — ELENDI
- p=1.0, KODLANMADI: kodlanmadı (önkoşul sağlanmadı / kod yok)

## SONUÇ
**KAZANAN YOK — kayıp-kazanç yapısı değişikliği bu veri üzerinde kanıtlanamadı; üretim ayarı değişmez.**
