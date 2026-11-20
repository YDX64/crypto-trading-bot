# E17 KARAR RAPORU (otomatik; analyze_e17.py decide)

- Kilit: L=`c00772ae32bd817c1703f7e97e2199acff445606` H=`a3168cda7fcaa7e3a6d5276be03ee5bb96cec27a`
- **Sonuç etiketi: DEV-YOK**
- Büyük grafikte C'nin para kazandırdığına dair kanıt yok.
- 1h (S60/T60): bu veriyle karara bağlanamaz (YETERSİZ GÜÇ: PF ≈ 1.9'un altı tespit edilemez).

## DEV (2025) — altı aile arm'ı
| arm | N | WR% | PF | PF[kötümser,iyimser] | ΣR | p(b=7) | q | t_obs | PF %90 GA | G0-G5 | G6 | karar |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S5 | 1829 | 48.93 | 0.6655 | [0.6655, 0.7676] | -356.24 | 1.0000 | 1.0000 | -6.5771 | [0.6095, 0.7273] | ++---- | G6 - |  |
| T5 | 1778 | 83.63 | 0.7109 | [0.7109, 0.8903] | -83.93 | 0.9999 | 1.0000 | -3.5233 | [0.6231, 0.8186] | ++---- | G6 - |  |
| S15 | 623 | 45.75 | 0.6194 | [0.6194, 0.7754] | -138.14 | 1.0000 | 1.0000 | -4.3328 | [0.5237, 0.7338] | ++---- | G6 - |  |
| T15 | 668 | 81.59 | 0.4370 | [0.4370, 0.7570] | -71.12 | 1.0000 | 1.0000 | -4.8826 | [0.3582, 0.5398] | ++---- | G6 - |  |
| S30 | 326 | 45.40 | 0.6553 | [0.6553, 0.7407] | -64.37 | 0.9996 | 1.0000 | -2.9343 | [0.5166, 0.8197] | ++---- | G6 - |  |
| T30 | 362 | 81.22 | 0.4210 | [0.4210, 0.5900] | -40.34 | 1.0000 | 1.0000 | -4.1760 | [0.3331, 0.5368] | -+---- | G6 - |  |

### Kapı satırları (değer vs eşik, 6 hane; SINIRDA = eşiğe 0.005 içinde)
**S5**
- G0a sessiz düşüş / ham sinyal: 0.000000 <= 0.100000 -> GEÇTİ
- G0b EOD işlem sayısı: 1 <= 8 -> GEÇTİ
- G0c1 3x kaldıraç payı: 0.036085 <= 0.100000 -> GEÇTİ
- G0c2 20x kaldıraç payı: 0.106069 <= 0.500000 -> GEÇTİ
- G0d sabit-risk payı: 1.000000 >= 0.950000 -> GEÇTİ
- G1 N (kapasite sonrası): 1829 >= 200 -> GEÇTİ
- G2 PF: 0.665502 >= 1.150000 -> KALDI
- G3 BH q (m=6, p[b=7]): 1.000000 <= 0.200000 -> KALDI
- G4a PF, en iyi ay (2025-11) hariç: 0.639299 >= 1.000000 -> KALDI
- G4b Σ R, en iyi 5 işlem hariç: -383.250207 > 0.000000 -> KALDI
- G5 rejim tabanı: uygun (n>=40) grupların en düşük PF'i: 0.594737 >= 0.800000 -> KALDI

**T5**
- G0a sessiz düşüş / ham sinyal: 0.004415 <= 0.100000 -> GEÇTİ
- G0b EOD işlem sayısı: 1 <= 8 -> GEÇTİ
- G0d sabit-risk payı: 1.000000 >= 0.950000 -> GEÇTİ
- G1 N (kapasite sonrası): 1778 >= 200 -> GEÇTİ
- G2 PF: 0.710913 >= 1.150000 -> KALDI
- G3 BH q (m=6, p[b=7]): 1.000000 <= 0.200000 -> KALDI
- G4a PF, en iyi ay (2025-08) hariç: 0.690303 >= 1.000000 -> KALDI
- G4b Σ R, en iyi 5 işlem hariç: -90.616531 > 0.000000 -> KALDI
- G5 rejim tabanı: uygun (n>=40) grupların en düşük PF'i: 0.614803 >= 0.800000 -> KALDI

**S15**
- G0a sessiz düşüş / ham sinyal: 0.000000 <= 0.100000 -> GEÇTİ
- G0b EOD işlem sayısı: 0 <= 8 -> GEÇTİ
- G0c1 3x kaldıraç payı: 0.048154 <= 0.100000 -> GEÇTİ
- G0c2 20x kaldıraç payı: 0.060995 <= 0.500000 -> GEÇTİ
- G0d sabit-risk payı: 1.000000 >= 0.950000 -> GEÇTİ
- G1 N (kapasite sonrası): 623 >= 200 -> GEÇTİ
- G2 PF: 0.619418 >= 1.150000 -> KALDI
- G3 BH q (m=6, p[b=7]): 1.000000 <= 0.200000 -> KALDI
- G4a PF, en iyi ay (2025-02) hariç: 0.609722 >= 1.000000 -> KALDI
- G4b Σ R, en iyi 5 işlem hariç: -151.375037 > 0.000000 -> KALDI
- G5 rejim tabanı: uygun (n>=40) grupların en düşük PF'i: 0.558451 >= 0.800000 -> KALDI

**T15**
- G0a sessiz düşüş / ham sinyal: 0.024602 <= 0.100000 -> GEÇTİ
- G0b EOD işlem sayısı: 0 <= 8 -> GEÇTİ
- G0d sabit-risk payı: 1.000000 >= 0.950000 -> GEÇTİ
- G1 N (kapasite sonrası): 668 >= 200 -> GEÇTİ
- G2 PF: 0.437031 >= 1.150000 -> KALDI
- G3 BH q (m=6, p[b=7]): 1.000000 <= 0.200000 -> KALDI
- G4a PF, en iyi ay (2025-03) hariç: 0.416624 >= 1.000000 -> KALDI
- G4b Σ R, en iyi 5 işlem hariç: -74.355452 > 0.000000 -> KALDI
- G5 rejim tabanı: uygun (n>=40) grupların en düşük PF'i: 0.346367 >= 0.800000 -> KALDI

**S30**
- G0a sessiz düşüş / ham sinyal: 0.000000 <= 0.100000 -> GEÇTİ
- G0b EOD işlem sayısı: 0 <= 8 -> GEÇTİ
- G0c1 3x kaldıraç payı: 0.030675 <= 0.100000 -> GEÇTİ
- G0c2 20x kaldıraç payı: 0.042945 <= 0.500000 -> GEÇTİ
- G0d sabit-risk payı: 1.000000 >= 0.950000 -> GEÇTİ
- G1 N (kapasite sonrası): 326 >= 200 -> GEÇTİ
- G2 PF: 0.655332 >= 1.150000 -> KALDI
- G3 BH q (m=6, p[b=7]): 1.000000 <= 0.200000 -> KALDI
- G4a PF, en iyi ay (2025-01) hariç: 0.616696 >= 1.000000 -> KALDI
- G4b Σ R, en iyi 5 işlem hariç: -79.002704 > 0.000000 -> KALDI
- G5 rejim tabanı: uygun (n>=40) grupların en düşük PF'i: 0.577883 >= 0.800000 -> KALDI

**T30**
- G0a sessiz düşüş / ham sinyal: 0.118644 <= 0.100000 -> KALDI
- G0b EOD işlem sayısı: 1 <= 8 -> GEÇTİ
- G0d sabit-risk payı: 1.000000 >= 0.950000 -> GEÇTİ
- G1 N (kapasite sonrası): 362 >= 200 -> GEÇTİ
- G2 PF: 0.420989 >= 1.150000 -> KALDI
- G3 BH q (m=6, p[b=7]): 1.000000 <= 0.200000 -> KALDI
- G4a PF, en iyi ay (2025-02) hariç: 0.406600 >= 1.000000 -> KALDI
- G4b Σ R, en iyi 5 işlem hariç: -43.658410 > 0.000000 -> KALDI
- G5 rejim tabanı: uygun (n>=40) grupların en düşük PF'i: 0.333300 >= 0.800000 -> KALDI

### S – T karşılaştırması ve TF eğilimi (yalnız rapor; karar vermez; PF 5'te kırpılı)
| giriş TF | PF S | PF T | PF S−T | ΣR S | ΣR T |
|---|---|---|---|---|---|
| 5m | 0.6655 | 0.7109 | -0.0454 | -356.24 | -83.93 |
| 15m | 0.6194 | 0.4370 | +0.1824 | -138.14 | -71.12 |
| 30m | 0.6553 | 0.4210 | +0.2343 | -64.37 | -40.34 |

- TF eğilimi ölçekli (S): PF 5m→15m→30m = 0.6655 → 0.6194 → 0.6553 (monoton değil)
- TF eğilimi yalnız zamanlama (T): PF 5m→15m→30m = 0.7109 → 0.4370 → 0.4210 (monoton azalan)

## Açıklayıcı (S60/T60) ve referanslar (B0, R1, S5G, T5G)
| arm | N | PF | ΣR | PnL USD | PF %90 GA |
|---|---|---|---|---|---|
| S60 | 184 | 0.6203 | -40.52 | -6483.08 | [0.4647, 0.8137] |
| T60 | 186 | 0.4161 | -21.50 | -10750.33 | [0.3104, 0.5838] |
| S5G | 662 | 0.6091 | -156.81 | -7840.47 | — |
| T5G | 639 | 0.6968 | -32.52 | -16260.15 | — |
| R1 | 6676 | 0.7630 | -253.65 | -126823.99 | — |
| B0 | 3370 | 0.7774 | -116.70 | -58348.02 | — |

- Kapı etkisi S5 → S5G: PF 0.6655 → 0.6091; N 1829 → 662
- Kapı etkisi T5 → T5G: PF 0.7109 → 0.6968; N 1778 → 639
- B0 (canlı 1m) PF 0.7774 (N 3370); R1 (B0+BG) PF 0.7630 (N 6676)
- Z0 S5: brüt PF 0.9302 vs net 0.6655 → ücretten önce de kenar yok
- Z0 T5: brüt PF 0.8824 vs net 0.7109 → ücretten önce de kenar yok
- Z0 S15: brüt PF 0.7261 vs net 0.6194 → ücretten önce de kenar yok
- Z0 T15: brüt PF 0.5503 vs net 0.4370 → ücretten önce de kenar yok
- Z0 S30: brüt PF 0.7272 vs net 0.6553 → ücretten önce de kenar yok
- Z0 T30: brüt PF 0.5247 vs net 0.4210 → ücretten önce de kenar yok

## CLAUDE.md kural #1 satırları

## Ayrıntı tabloları (giriş ayı ataması; rejim = donmuş BTC 1d etiketi)
### S5
N=1829 WR=48.93% PF=0.6655 ΣR=-356.2381 DD_R=360.1843 (USD DD 18009.22) Calmar_R=-0.9890 EOD=1 3x=0.036 20x=0.106
- çıkış karışımı: EOD=1, SL=934, TRAIL=894
- kaldıraç histogramı: 3x=66, 4x=98, 5x=162, 6x=180, 7x=197, 8x=183, 9x=158, 10x=118, 11x=90, 12x=86, 13x=64, 14x=65, 15x=50, 16x=38, 17x=33, 18x=22, 19x=25, 20x=194
- sinyal vs işlem: ham=1843 işlem=1829 sessiz düşüş=0 (0.0000); missed={'maker_missed': 13, 'capacity': 1}
- diagnostics: {'tp1_fills': 896, 'sl_tp1_same_bar': 6, 'tp1_then_stop_same_bar': 167, 'tp2_then_floor_same_bar': 32, 'fill_bar_exit': 171, 'fill_bar_total': 1830}
- yön x rejim: LONG/UP: n=333 PF=0.6528 ΣR=-70.61; LONG/FLAT: n=328 PF=0.8509 ΣR=-24.30; LONG/DOWN: n=234 PF=0.7511 ΣR=-34.06; SHORT/UP: n=434 PF=0.5504 ΣR=-119.94; SHORT/FLAT: n=324 PF=0.5467 ΣR=-91.86; SHORT/DOWN: n=176 PF=0.8325 ΣR=-15.46
- rejim grupları: UP: n=767 PF=0.5947 ΣR=-190.55; FLAT: n=652 PF=0.6823 ΣR=-116.16; DOWN: n=410 PF=0.7839 ΣR=-49.52
- semboller: ADAUSDT: n=195 ΣR=-28.48; BNBUSDT: n=271 ΣR=-62.79; BTCUSDT: n=241 ΣR=-80.97; DOGEUSDT: n=241 ΣR=-61.46; ETHUSDT: n=215 ΣR=-32.46; LTCUSDT: n=199 ΣR=-39.76; SOLUSDT: n=238 ΣR=-37.16; XRPUSDT: n=229 ΣR=-13.17
| ay | etiket | N | ΣR | PF |
|---|---|---|---|---|
| 2025-01 | UP | 172 | -52.813 | 0.5060 |
| 2025-02 | DOWN | 147 | -31.197 | 0.6392 |
| 2025-03 | FLAT | 168 | -25.880 | 0.7201 |
| 2025-04 | UP | 115 | -11.808 | 0.8140 |
| 2025-05 | UP | 163 | -43.153 | 0.5645 |
| 2025-06 | FLAT | 171 | -22.741 | 0.7539 |
| 2025-07 | UP | 158 | -41.205 | 0.5877 |
| 2025-08 | DOWN | 141 | -26.424 | 0.6990 |
| 2025-09 | UP | 159 | -41.573 | 0.5876 |
| 2025-10 | FLAT | 154 | -22.173 | 0.7464 |
| 2025-11 | DOWN | 122 | 8.097 | 1.1474 |
| 2025-12 | FLAT | 159 | -45.367 | 0.5139 |
- p(b=7)=1.0000 p(b=28)=1.0000 p(b=14, bilgi)=1.0000 t=-6.5771
- ay düzeyinde tek-yönlü t (bilgi, M=12 tam ay): t=-6.1108 p=1.0000

### T5
N=1778 WR=83.63% PF=0.7109 ΣR=-83.9276 DD_R=89.2804 (USD DD 44640.22) Calmar_R=-0.9400 EOD=1 3x=0.000 20x=0.977
- çıkış karışımı: EOD=1, REAPER=15, SL=277, TRAIL=1485
- kaldıraç histogramı: 12x=6, 13x=7, 14x=2, 15x=3, 16x=4, 17x=4, 18x=9, 19x=6, 20x=1737
- sinyal vs işlem: ham=1812 işlem=1778 sessiz düşüş=8 (0.0044); missed={'stop_band_high': 8, 'maker_missed': 11, 'capacity': 15}
- diagnostics: {'tp1_fills': 1499, 'sl_tp1_same_bar': 2, 'tp1_then_stop_same_bar': 570, 'tp2_then_floor_same_bar': 19, 'fill_bar_exit': 187, 'fill_bar_total': 1793}
- yön x rejim: LONG/UP: n=337 PF=0.8985 ΣR=-5.13; LONG/FLAT: n=313 PF=0.6468 ΣR=-19.76; LONG/DOWN: n=240 PF=0.7026 ΣR=-10.46; SHORT/UP: n=400 PF=0.4558 ΣR=-49.06; SHORT/FLAT: n=308 PF=0.9901 ΣR=-0.37; SHORT/DOWN: n=180 PF=1.0393 ΣR=0.85
- rejim grupları: UP: n=737 PF=0.6148 ΣR=-54.19; FLAT: n=621 PF=0.7833 ΣR=-20.13; DOWN: n=420 PF=0.8307 ΣR=-9.61
- semboller: ADAUSDT: n=200 ΣR=-14.88; BNBUSDT: n=244 ΣR=-2.99; BTCUSDT: n=200 ΣR=-13.88; DOGEUSDT: n=243 ΣR=-17.03; ETHUSDT: n=213 ΣR=-13.87; LTCUSDT: n=200 ΣR=-8.98; SOLUSDT: n=246 ΣR=-8.43; XRPUSDT: n=232 ΣR=-3.87
| ay | etiket | N | ΣR | PF |
|---|---|---|---|---|
| 2025-01 | UP | 173 | -13.736 | 0.5764 |
| 2025-02 | DOWN | 150 | -7.148 | 0.6726 |
| 2025-03 | FLAT | 175 | -1.729 | 0.9199 |
| 2025-04 | UP | 113 | 0.494 | 1.0321 |
| 2025-05 | UP | 154 | -13.504 | 0.5511 |
| 2025-06 | FLAT | 149 | -5.961 | 0.7497 |
| 2025-07 | UP | 144 | -15.503 | 0.5257 |
| 2025-08 | DOWN | 142 | 0.572 | 1.0327 |
| 2025-09 | UP | 153 | -11.937 | 0.6030 |
| 2025-10 | FLAT | 151 | -6.850 | 0.7305 |
| 2025-11 | DOWN | 128 | -3.038 | 0.8261 |
| 2025-12 | FLAT | 146 | -5.587 | 0.7466 |
- p(b=7)=0.9999 p(b=28)=1.0000 p(b=14, bilgi)=1.0000 t=-3.5233
- ay düzeyinde tek-yönlü t (bilgi, M=12 tam ay): t=-4.3296 p=0.9994

### S15
N=623 WR=45.75% PF=0.6194 ΣR=-138.1387 DD_R=139.8250 (USD DD 12584.25) Calmar_R=-0.9879 EOD=0 3x=0.048 20x=0.061
- çıkış karışımı: SL=338, TRAIL=285
- kaldıraç histogramı: 3x=30, 4x=32, 5x=67, 6x=70, 7x=71, 8x=55, 9x=51, 10x=51, 11x=33, 12x=26, 13x=21, 14x=23, 15x=18, 16x=8, 17x=9, 18x=12, 19x=8, 20x=38
- sinyal vs işlem: ham=629 işlem=623 sessiz düşüş=0 (0.0000); missed={'maker_missed': 2, 'capacity': 4}
- diagnostics: {'tp1_fills': 287, 'sl_tp1_same_bar': 2, 'tp1_then_stop_same_bar': 44, 'tp2_then_floor_same_bar': 13, 'fill_bar_exit': 81, 'fill_bar_total': 627}
- yön x rejim: LONG/UP: n=114 PF=0.6770 ΣR=-19.96; LONG/FLAT: n=123 PF=0.6891 ΣR=-20.63; LONG/DOWN: n=77 PF=0.4817 ΣR=-26.88; SHORT/UP: n=150 PF=0.4821 ΣR=-49.71; SHORT/FLAT: n=87 PF=0.6412 ΣR=-18.10; SHORT/DOWN: n=72 PF=0.9217 ΣR=-2.86
- rejim grupları: UP: n=264 PF=0.5585 ΣR=-69.67; FLAT: n=210 PF=0.6684 ΣR=-38.73; DOWN: n=149 PF=0.6635 ΣR=-29.74
- semboller: ADAUSDT: n=72 ΣR=-17.46; BNBUSDT: n=75 ΣR=-22.76; BTCUSDT: n=86 ΣR=-23.16; DOGEUSDT: n=81 ΣR=-18.13; ETHUSDT: n=83 ΣR=-17.67; LTCUSDT: n=64 ΣR=-14.02; SOLUSDT: n=78 ΣR=-10.83; XRPUSDT: n=84 ΣR=-14.10
| ay | etiket | N | ΣR | PF |
|---|---|---|---|---|
| 2025-01 | UP | 60 | -14.927 | 0.5894 |
| 2025-02 | DOWN | 40 | -4.341 | 0.7845 |
| 2025-03 | FLAT | 57 | -5.756 | 0.7943 |
| 2025-04 | UP | 56 | -5.704 | 0.7939 |
| 2025-05 | UP | 46 | -15.093 | 0.4570 |
| 2025-06 | FLAT | 42 | -18.577 | 0.3905 |
| 2025-07 | UP | 52 | -17.193 | 0.5019 |
| 2025-08 | DOWN | 55 | -5.343 | 0.8347 |
| 2025-09 | UP | 50 | -16.757 | 0.4671 |
| 2025-10 | FLAT | 61 | -4.509 | 0.8594 |
| 2025-11 | DOWN | 54 | -20.052 | 0.4417 |
| 2025-12 | FLAT | 50 | -9.886 | 0.6234 |
- p(b=7)=1.0000 p(b=28)=1.0000 p(b=14, bilgi)=1.0000 t=-4.3328
- ay düzeyinde tek-yönlü t (bilgi, M=12 tam ay): t=-6.4911 p=1.0000

### T15
N=668 WR=81.59% PF=0.4370 ΣR=-71.1241 DD_R=72.6007 (USD DD 36300.35) Calmar_R=-0.9797 EOD=0 3x=0.000 20x=0.829
- çıkış karışımı: SL=123, TRAIL=545
- kaldıraç histogramı: 10x=2, 11x=10, 12x=3, 13x=9, 14x=8, 15x=10, 16x=14, 17x=16, 18x=19, 19x=23, 20x=554
- sinyal vs işlem: ham=691 işlem=668 sessiz düşüş=17 (0.0246); missed={'stop_band_high': 17, 'maker_missed': 2, 'capacity': 4}
- diagnostics: {'tp1_fills': 547, 'sl_tp1_same_bar': 4, 'tp1_then_stop_same_bar': 404, 'tp2_then_floor_same_bar': 9, 'fill_bar_exit': 194, 'fill_bar_total': 672}
- yön x rejim: LONG/UP: n=122 PF=0.4561 ΣR=-10.62; LONG/FLAT: n=132 PF=0.3805 ΣR=-15.25; LONG/DOWN: n=88 PF=0.5147 ΣR=-6.47; SHORT/UP: n=154 PF=0.3011 ΣR=-33.04; SHORT/FLAT: n=95 PF=0.6377 ΣR=-4.84; SHORT/DOWN: n=77 PF=0.8900 ΣR=-0.91
- rejim grupları: UP: n=276 PF=0.3464 ΣR=-43.65; FLAT: n=227 PF=0.4711 ΣR=-20.09; DOWN: n=165 PF=0.6579 ΣR=-7.38
- semboller: ADAUSDT: n=87 ΣR=-9.34; BNBUSDT: n=76 ΣR=-5.72; BTCUSDT: n=86 ΣR=-14.31; DOGEUSDT: n=89 ΣR=-9.62; ETHUSDT: n=88 ΣR=-8.91; LTCUSDT: n=66 ΣR=-10.96; SOLUSDT: n=85 ΣR=-4.65; XRPUSDT: n=91 ΣR=-7.62
| ay | etiket | N | ΣR | PF |
|---|---|---|---|---|
| 2025-01 | UP | 65 | -9.033 | 0.4141 |
| 2025-02 | DOWN | 43 | -1.060 | 0.7938 |
| 2025-03 | FLAT | 59 | -0.420 | 0.9183 |
| 2025-04 | UP | 54 | -11.238 | 0.2698 |
| 2025-05 | UP | 49 | -8.025 | 0.2900 |
| 2025-06 | FLAT | 46 | -8.983 | 0.2714 |
| 2025-07 | UP | 54 | -10.987 | 0.2874 |
| 2025-08 | DOWN | 63 | -2.911 | 0.6455 |
| 2025-09 | UP | 54 | -4.369 | 0.5279 |
| 2025-10 | FLAT | 66 | -4.994 | 0.5123 |
| 2025-11 | DOWN | 59 | -3.408 | 0.5852 |
| 2025-12 | FLAT | 56 | -5.696 | 0.4458 |
- p(b=7)=1.0000 p(b=28)=1.0000 p(b=14, bilgi)=1.0000 t=-4.8826
- ay düzeyinde tek-yönlü t (bilgi, M=12 tam ay): t=-5.5597 p=0.9999

### S30
N=326 WR=45.40% PF=0.6553 ΣR=-64.3726 DD_R=74.8848 (USD DD 9735.03) Calmar_R=-0.8596 EOD=0 3x=0.031 20x=0.043
- çıkış karışımı: SL=178, TRAIL=148
- kaldıraç histogramı: 3x=10, 4x=23, 5x=33, 6x=31, 7x=41, 8x=43, 9x=33, 10x=29, 11x=16, 12x=11, 13x=6, 14x=11, 15x=7, 16x=8, 17x=5, 18x=3, 19x=2, 20x=14
- sinyal vs işlem: ham=327 işlem=326 sessiz düşüş=0 (0.0000); missed={'maker_missed': 1}
- diagnostics: {'tp1_fills': 148, 'sl_tp1_same_bar': 1, 'tp1_then_stop_same_bar': 15, 'tp2_then_floor_same_bar': 3, 'fill_bar_exit': 38, 'fill_bar_total': 326}
- yön x rejim: LONG/UP: n=66 PF=1.1021 ΣR=2.80; LONG/FLAT: n=53 PF=0.7222 ΣR=-8.44; LONG/DOWN: n=49 PF=0.4565 ΣR=-18.60; SHORT/UP: n=72 PF=0.5798 ΣR=-18.48; SHORT/FLAT: n=53 PF=0.4496 ΣR=-18.73; SHORT/DOWN: n=33 PF=0.8262 ΣR=-2.91
- rejim grupları: UP: n=138 PF=0.7803 ΣR=-15.69; FLAT: n=106 PF=0.5782 ΣR=-27.18; DOWN: n=82 PF=0.5779 ΣR=-21.51
- semboller: ADAUSDT: n=45 ΣR=-12.90; BNBUSDT: n=41 ΣR=-19.38; BTCUSDT: n=41 ΣR=4.56; DOGEUSDT: n=38 ΣR=-7.39; ETHUSDT: n=39 ΣR=-3.85; LTCUSDT: n=44 ΣR=-17.84; SOLUSDT: n=42 ΣR=4.80; XRPUSDT: n=36 ΣR=-12.37
| ay | etiket | N | ΣR | PF |
|---|---|---|---|---|
| 2025-01 | UP | 28 | 2.371 | 1.1875 |
| 2025-02 | DOWN | 32 | -0.047 | 0.9970 |
| 2025-03 | FLAT | 27 | -7.163 | 0.5715 |
| 2025-04 | UP | 32 | -6.451 | 0.6129 |
| 2025-05 | UP | 22 | -1.495 | 0.8401 |
| 2025-06 | FLAT | 23 | -12.131 | 0.2844 |
| 2025-07 | UP | 29 | -7.050 | 0.6055 |
| 2025-08 | DOWN | 24 | -6.498 | 0.5614 |
| 2025-09 | UP | 27 | -3.062 | 0.7940 |
| 2025-10 | FLAT | 26 | -6.223 | 0.5473 |
| 2025-11 | DOWN | 26 | -14.965 | 0.2761 |
| 2025-12 | FLAT | 30 | -1.660 | 0.9024 |
- p(b=7)=0.9996 p(b=28)=0.9999 p(b=14, bilgi)=0.9997 t=-2.9343
- ay düzeyinde tek-yönlü t (bilgi, M=12 tam ay): t=-3.7554 p=0.9984

### T30
N=362 WR=81.22% PF=0.4210 ΣR=-40.3397 DD_R=42.1841 (USD DD 21092.04) Calmar_R=-0.9563 EOD=1 3x=0.000 20x=0.550
- çıkış karışımı: EOD=1, SL=68, TRAIL=293
- kaldıraç histogramı: 10x=6, 11x=18, 12x=13, 13x=21, 14x=14, 15x=20, 16x=14, 17x=19, 18x=16, 19x=22, 20x=199
- sinyal vs işlem: ham=413 işlem=362 sessiz düşüş=49 (0.1186); missed={'stop_band_high': 49, 'maker_missed': 1, 'capacity': 1}
- diagnostics: {'tp1_fills': 295, 'sl_tp1_same_bar': 1, 'tp1_then_stop_same_bar': 244, 'tp2_then_floor_same_bar': 7, 'fill_bar_exit': 154, 'fill_bar_total': 363}
- yön x rejim: LONG/UP: n=68 PF=0.3808 ΣR=-8.27; LONG/FLAT: n=61 PF=0.3832 ΣR=-7.58; LONG/DOWN: n=50 PF=0.2892 ΣR=-8.71; SHORT/UP: n=93 PF=0.5691 ΣR=-6.62; SHORT/FLAT: n=56 PF=0.4538 ΣR=-5.61; SHORT/DOWN: n=34 PF=0.4213 ΣR=-3.55
- rejim grupları: UP: n=161 PF=0.4815 ΣR=-14.89; FLAT: n=117 PF=0.4154 ΣR=-13.19; DOWN: n=84 PF=0.3333 ΣR=-12.26
- semboller: ADAUSDT: n=54 ΣR=-6.70; BNBUSDT: n=48 ΣR=-6.17; BTCUSDT: n=47 ΣR=0.47; DOGEUSDT: n=37 ΣR=-7.49; ETHUSDT: n=43 ΣR=-6.16; LTCUSDT: n=46 ΣR=-8.30; SOLUSDT: n=43 ΣR=-1.15; XRPUSDT: n=44 ΣR=-4.84
| ay | etiket | N | ΣR | PF |
|---|---|---|---|---|
| 2025-01 | UP | 33 | -3.990 | 0.4447 |
| 2025-02 | DOWN | 29 | -0.814 | 0.7340 |
| 2025-03 | FLAT | 29 | -4.934 | 0.3112 |
| 2025-04 | UP | 30 | -1.401 | 0.6590 |
| 2025-05 | UP | 27 | -2.241 | 0.4530 |
| 2025-06 | FLAT | 23 | -3.523 | 0.3129 |
| 2025-07 | UP | 40 | -3.054 | 0.5026 |
| 2025-08 | DOWN | 26 | -1.670 | 0.5926 |
| 2025-09 | UP | 31 | -4.208 | 0.4152 |
| 2025-10 | FLAT | 30 | -2.240 | 0.5632 |
| 2025-11 | DOWN | 29 | -9.772 | 0.1293 |
| 2025-12 | FLAT | 35 | -2.492 | 0.5152 |
- p(b=7)=1.0000 p(b=28)=1.0000 p(b=14, bilgi)=1.0000 t=-4.1760
- ay düzeyinde tek-yönlü t (bilgi, M=12 tam ay): t=-4.9343 p=0.9998

### S60
N=184 WR=44.02% PF=0.6203 ΣR=-40.5192 DD_R=43.5309 (USD DD 6964.94) Calmar_R=-0.9308 EOD=0 3x=0.076 20x=0.016
- çıkış karışımı: SL=103, TRAIL=81
- kaldıraç histogramı: 3x=14, 4x=15, 5x=24, 6x=24, 7x=29, 8x=20, 9x=18, 10x=8, 11x=8, 12x=7, 13x=3, 14x=2, 15x=2, 16x=2, 17x=3, 18x=1, 19x=1, 20x=3
- sinyal vs işlem: ham=185 işlem=184 sessiz düşüş=0 (0.0000); missed={'capacity': 1}
- diagnostics: {'tp1_fills': 82, 'sl_tp1_same_bar': 0, 'tp1_then_stop_same_bar': 11, 'tp2_then_floor_same_bar': 3, 'fill_bar_exit': 27, 'fill_bar_total': 185}
- yön x rejim: LONG/UP: n=25 PF=0.8160 ΣR=-2.12; LONG/FLAT: n=30 PF=0.6574 ΣR=-7.03; LONG/DOWN: n=28 PF=0.4401 ΣR=-9.78; SHORT/UP: n=54 PF=0.6046 ΣR=-11.48; SHORT/FLAT: n=30 PF=0.8173 ΣR=-3.24; SHORT/DOWN: n=17 PF=0.3408 ΣR=-6.86
- rejim grupları: UP: n=79 PF=0.6647 ΣR=-13.60; FLAT: n=60 PF=0.7316 ΣR=-10.28; DOWN: n=45 PF=0.4031 ΣR=-16.64
- semboller: ADAUSDT: n=18 ΣR=-0.33; BNBUSDT: n=23 ΣR=-7.59; BTCUSDT: n=19 ΣR=-9.70; DOGEUSDT: n=27 ΣR=0.27; ETHUSDT: n=21 ΣR=-7.13; LTCUSDT: n=22 ΣR=-7.82; SOLUSDT: n=29 ΣR=-0.94; XRPUSDT: n=25 ΣR=-7.28
| ay | etiket | N | ΣR | PF |
|---|---|---|---|---|
| 2025-01 | UP | 13 | -5.355 | 0.3503 |
| 2025-02 | DOWN | 17 | -10.131 | 0.1823 |
| 2025-03 | FLAT | 13 | -6.453 | 0.3738 |
| 2025-04 | UP | 8 | 2.466 | 2.1687 |
| 2025-05 | UP | 19 | -5.934 | 0.4813 |
| 2025-06 | FLAT | 18 | 2.490 | 1.2668 |
| 2025-07 | UP | 19 | -0.809 | 0.9134 |
| 2025-08 | DOWN | 19 | -6.235 | 0.4526 |
| 2025-09 | UP | 20 | -3.972 | 0.5790 |
| 2025-10 | FLAT | 16 | -6.672 | 0.4144 |
| 2025-11 | DOWN | 9 | -0.274 | 0.9332 |
| 2025-12 | FLAT | 13 | 0.359 | 1.0495 |
- p(b=7)=0.9980 p(b=28)=1.0000 p(b=14, bilgi)=0.9989 t=-2.5127
- ay düzeyinde tek-yönlü t (bilgi, M=12 tam ay): t=-2.8627 p=0.9923

### T60
N=186 WR=80.65% PF=0.4161 ΣR=-21.5007 DD_R=22.3863 (USD DD 11193.13) Calmar_R=-0.9604 EOD=0 3x=0.000 20x=0.242
- çıkış karışımı: SL=36, TRAIL=150
- kaldıraç histogramı: 10x=12, 11x=13, 12x=17, 13x=13, 14x=24, 15x=17, 16x=15, 17x=10, 18x=11, 19x=9, 20x=45
- sinyal vs işlem: ham=244 işlem=186 sessiz düşüş=57 (0.2336); missed={'stop_band_high': 57, 'capacity': 1}
- diagnostics: {'tp1_fills': 151, 'sl_tp1_same_bar': 1, 'tp1_then_stop_same_bar': 132, 'tp2_then_floor_same_bar': 5, 'fill_bar_exit': 87, 'fill_bar_total': 187}
- yön x rejim: LONG/UP: n=25 PF=0.5770 ΣR=-1.30; LONG/FLAT: n=29 PF=0.5344 ΣR=-2.84; LONG/DOWN: n=22 PF=0.4725 ΣR=-2.69; SHORT/UP: n=53 PF=0.2519 ΣR=-10.73; SHORT/FLAT: n=36 PF=0.4176 ΣR=-3.59; SHORT/DOWN: n=21 PF=0.8295 ΣR=-0.35
- rejim grupları: UP: n=78 PF=0.3093 ΣR=-12.03; FLAT: n=65 PF=0.4757 ΣR=-6.43; DOWN: n=43 PF=0.5743 ΣR=-3.04
- semboller: ADAUSDT: n=19 ΣR=-2.54; BNBUSDT: n=27 ΣR=-1.07; BTCUSDT: n=21 ΣR=-1.01; DOGEUSDT: n=22 ΣR=-2.63; ETHUSDT: n=25 ΣR=-7.77; LTCUSDT: n=20 ΣR=-2.78; SOLUSDT: n=28 ΣR=-2.47; XRPUSDT: n=24 ΣR=-1.22
| ay | etiket | N | ΣR | PF |
|---|---|---|---|---|
| 2025-01 | UP | 11 | -2.258 | 0.2643 |
| 2025-02 | DOWN | 13 | -1.386 | 0.5488 |
| 2025-03 | FLAT | 13 | -2.256 | 0.2632 |
| 2025-04 | UP | 6 | -0.315 | 0.6916 |
| 2025-05 | UP | 21 | 0.584 | 1.5676 |
| 2025-06 | FLAT | 19 | -1.159 | 0.6224 |
| 2025-07 | UP | 17 | -5.253 | 0.1444 |
| 2025-08 | DOWN | 24 | -2.138 | 0.4748 |
| 2025-09 | UP | 23 | -4.789 | 0.2226 |
| 2025-10 | FLAT | 18 | -2.318 | 0.4319 |
| 2025-11 | DOWN | 6 | 0.484 | 5.0000 |
| 2025-12 | FLAT | 15 | -0.695 | 0.6605 |
- p(b=7)=1.0000 p(b=28)=0.9999 p(b=14, bilgi)=1.0000 t=-3.1748
- ay düzeyinde tek-yönlü t (bilgi, M=12 tam ay): t=-3.4045 p=0.9971

### S5G
N=662 WR=47.43% PF=0.6091 ΣR=-156.8094 DD_R=161.9542 (USD DD 8097.71) Calmar_R=-0.9682 EOD=1 3x=0.027 20x=0.127
- çıkış karışımı: EOD=1, SL=348, TRAIL=313
- kaldıraç histogramı: 3x=18, 4x=20, 5x=45, 6x=56, 7x=54, 8x=66, 9x=67, 10x=53, 11x=37, 12x=37, 13x=24, 14x=27, 15x=24, 16x=19, 17x=13, 18x=6, 19x=12, 20x=84
- sinyal vs işlem: ham=665 işlem=662 sessiz düşüş=0 (0.0000); missed={'regime_gate': 2029, 'market_gate_day': 631, 'weekday_gate': 209, 'maker_missed': 3}
- diagnostics: {'tp1_fills': 314, 'sl_tp1_same_bar': 1, 'tp1_then_stop_same_bar': 56, 'tp2_then_floor_same_bar': 10, 'fill_bar_exit': 54, 'fill_bar_total': 662}
- yön x rejim: LONG/UP: n=138 PF=0.6193 ΣR=-32.99; LONG/FLAT: n=82 PF=0.7502 ΣR=-11.41; LONG/DOWN: n=55 PF=0.5603 ΣR=-14.26; SHORT/UP: n=143 PF=0.5436 ΣR=-40.03; SHORT/FLAT: n=158 PF=0.5544 ΣR=-44.06; SHORT/DOWN: n=86 PF=0.7175 ΣR=-14.06
- rejim grupları: UP: n=281 PF=0.5812 ΣR=-73.02; FLAT: n=240 PF=0.6163 ΣR=-55.47; DOWN: n=141 PF=0.6555 ΣR=-28.32
- semboller: ADAUSDT: n=73 ΣR=-16.51; BNBUSDT: n=112 ΣR=-34.23; BTCUSDT: n=81 ΣR=-29.87; DOGEUSDT: n=81 ΣR=-32.07; ETHUSDT: n=83 ΣR=-4.11; LTCUSDT: n=70 ΣR=-23.79; SOLUSDT: n=83 ΣR=-7.78; XRPUSDT: n=79 ΣR=-8.45
| ay | etiket | N | ΣR | PF |
|---|---|---|---|---|
| 2025-01 | UP | 55 | -17.345 | 0.5064 |
| 2025-02 | DOWN | 52 | -11.664 | 0.6137 |
| 2025-03 | FLAT | 60 | -9.871 | 0.6958 |
| 2025-04 | UP | 47 | -3.064 | 0.8793 |
| 2025-05 | UP | 48 | -4.913 | 0.7972 |
| 2025-06 | FLAT | 71 | -9.952 | 0.7479 |
| 2025-07 | UP | 58 | -16.898 | 0.5463 |
| 2025-08 | DOWN | 47 | -14.131 | 0.5424 |
| 2025-09 | UP | 73 | -30.798 | 0.4120 |
| 2025-10 | FLAT | 48 | -7.259 | 0.7515 |
| 2025-11 | DOWN | 42 | -2.526 | 0.8805 |
| 2025-12 | FLAT | 61 | -28.389 | 0.3461 |

### T5G
N=639 WR=82.63% PF=0.6968 ΣR=-32.5203 DD_R=33.7017 (USD DD 16850.87) Calmar_R=-0.9649 EOD=1 3x=0.000 20x=0.984
- çıkış karışımı: EOD=1, REAPER=11, SL=100, TRAIL=527
- kaldıraç histogramı: 12x=2, 13x=2, 14x=2, 16x=2, 18x=1, 19x=1, 20x=629
- sinyal vs işlem: ham=648 işlem=639 sessiz düşüş=4 (0.0062); missed={'regime_gate': 1996, 'market_gate_day': 628, 'weekday_gate': 201, 'stop_band_high': 4, 'maker_missed': 3, 'capacity': 2}
- diagnostics: {'tp1_fills': 530, 'sl_tp1_same_bar': 0, 'tp1_then_stop_same_bar': 166, 'tp2_then_floor_same_bar': 3, 'fill_bar_exit': 41, 'fill_bar_total': 641}
- yön x rejim: LONG/UP: n=133 PF=1.3131 ΣR=4.75; LONG/FLAT: n=80 PF=0.4408 ΣR=-11.17; LONG/DOWN: n=54 PF=0.6808 ΣR=-2.30; SHORT/UP: n=134 PF=0.4261 ΣR=-18.70; SHORT/FLAT: n=152 PF=0.8855 ΣR=-2.29; SHORT/DOWN: n=86 PF=0.7723 ΣR=-2.81
- rejim grupları: UP: n=267 PF=0.7079 ΣR=-13.95; FLAT: n=232 PF=0.6632 ΣR=-13.46; DOWN: n=140 PF=0.7387 ΣR=-5.10
- semboller: ADAUSDT: n=75 ΣR=-9.74; BNBUSDT: n=103 ΣR=1.45; BTCUSDT: n=73 ΣR=-4.72; DOGEUSDT: n=81 ΣR=-6.93; ETHUSDT: n=80 ΣR=-4.34; LTCUSDT: n=67 ΣR=-3.30; SOLUSDT: n=82 ΣR=0.54; XRPUSDT: n=78 ΣR=-5.49
| ay | etiket | N | ΣR | PF |
|---|---|---|---|---|
| 2025-01 | UP | 54 | -4.981 | 0.5405 |
| 2025-02 | DOWN | 51 | -1.698 | 0.7641 |
| 2025-03 | FLAT | 60 | -0.489 | 0.9207 |
| 2025-04 | UP | 47 | 0.554 | 1.0858 |
| 2025-05 | UP | 46 | 3.774 | 2.0716 |
| 2025-06 | FLAT | 67 | -3.106 | 0.7107 |
| 2025-07 | UP | 52 | -5.712 | 0.4932 |
| 2025-08 | DOWN | 47 | -2.393 | 0.6674 |
| 2025-09 | UP | 68 | -7.585 | 0.5161 |
| 2025-10 | FLAT | 47 | -1.184 | 0.8560 |
| 2025-11 | DOWN | 42 | -1.014 | 0.8028 |
| 2025-12 | FLAT | 58 | -8.685 | 0.4152 |

### R1
N=6676 WR=76.51% PF=0.7630 ΣR=-253.6480 DD_R=75.1211 (USD DD 37560.54) Calmar_R=-3.3765 EOD=11 3x=0.000 20x=0.998
- çıkış karışımı: EOD=11, REAPER=858, SL=785, TRAIL=5022
- kaldıraç histogramı: 14x=2, 16x=4, 17x=5, 18x=1, 19x=4, 20x=6660
- sinyal vs işlem: ham=7057 işlem=6676 sessiz düşüş=2 (0.0003); missed={'maker_missed': 92, 'stop_band_high': 2, 'capacity': 287}
- diagnostics: {'tp1_fills': 5239, 'sl_tp1_same_bar': 0, 'tp1_then_stop_same_bar': 552, 'tp2_then_floor_same_bar': 13, 'fill_bar_exit': 147, 'fill_bar_total': 6963}
- yön x rejim: LONG/UP: n=1327 PF=0.8551 ΣR=-28.51; LONG/FLAT: n=1132 PF=0.7212 ΣR=-52.25; LONG/DOWN: n=936 PF=0.6410 ΣR=-62.73; SHORT/UP: n=1402 PF=0.6695 ΣR=-81.52; SHORT/FLAT: n=1047 PF=0.7649 ΣR=-38.20; SHORT/DOWN: n=832 PF=1.0935 ΣR=9.55
- rejim grupları: UP: n=2729 PF=0.7518 ΣR=-110.02; FLAT: n=2179 PF=0.7415 ΣR=-90.44; DOWN: n=1768 PF=0.8079 ΣR=-53.18
- semboller: ADAUSDT: n=837 ΣR=-34.77; BNBUSDT: n=747 ΣR=-32.83; BTCUSDT: n=723 ΣR=-27.39; DOGEUSDT: n=923 ΣR=-42.65; ETHUSDT: n=824 ΣR=-24.69; LTCUSDT: n=861 ΣR=-20.49; SOLUSDT: n=882 ΣR=-45.57; XRPUSDT: n=879 ΣR=-25.26
| ay | etiket | N | ΣR | PF |
|---|---|---|---|---|
| 2025-01 | UP | 595 | -26.034 | 0.7365 |
| 2025-02 | DOWN | 606 | -20.457 | 0.7878 |
| 2025-03 | FLAT | 606 | -21.951 | 0.7703 |
| 2025-04 | UP | 524 | -22.019 | 0.7404 |
| 2025-05 | UP | 587 | -27.522 | 0.7314 |
| 2025-06 | FLAT | 483 | -12.229 | 0.8268 |
| 2025-07 | UP | 559 | -21.553 | 0.7598 |
| 2025-08 | DOWN | 562 | -24.221 | 0.7384 |
| 2025-09 | UP | 464 | -12.897 | 0.8089 |
| 2025-10 | FLAT | 611 | -26.598 | 0.7378 |
| 2025-11 | DOWN | 600 | -8.501 | 0.9033 |
| 2025-12 | FLAT | 479 | -29.666 | 0.6394 |

### B0
N=3370 WR=74.48% PF=0.7774 ΣR=-116.6960 DD_R=44.5298 (USD DD 22264.90) Calmar_R=-2.6206 EOD=10 3x=0.000 20x=0.999
- çıkış karışımı: EOD=10, REAPER=558, SL=341, TRAIL=2461
- kaldıraç histogramı: 14x=1, 17x=1, 20x=3368
- sinyal vs işlem: ham=3473 işlem=3370 sessiz düşüş=0 (0.0000); missed={'regime_gate': 8052, 'weekday_gate': 1394, 'market_gate_day': 1152, 'maker_missed': 39, 'capacity': 64}
- diagnostics: {'tp1_fills': 2505, 'sl_tp1_same_bar': 0, 'tp1_then_stop_same_bar': 170, 'tp2_then_floor_same_bar': 6, 'fill_bar_exit': 35, 'fill_bar_total': 3434}
- yön x rejim: LONG/UP: n=645 PF=0.8931 ΣR=-9.72; LONG/FLAT: n=462 PF=0.6754 ΣR=-26.31; LONG/DOWN: n=300 PF=0.5777 ΣR=-25.86; SHORT/UP: n=765 PF=0.6633 ΣR=-43.41; SHORT/FLAT: n=656 PF=0.7110 ΣR=-31.40; SHORT/DOWN: n=542 PF=1.3739 ΣR=20.00
- rejim grupları: UP: n=1410 PF=0.7584 ΣR=-53.13; FLAT: n=1118 PF=0.6958 ΣR=-57.71; DOWN: n=842 PF=0.9490 ΣR=-5.86
- semboller: ADAUSDT: n=395 ΣR=-23.76; BNBUSDT: n=416 ΣR=-10.56; BTCUSDT: n=395 ΣR=-16.86; DOGEUSDT: n=451 ΣR=-18.19; ETHUSDT: n=436 ΣR=-12.07; LTCUSDT: n=418 ΣR=-12.85; SOLUSDT: n=428 ΣR=-17.62; XRPUSDT: n=431 ΣR=-4.78
| ay | etiket | N | ΣR | PF |
|---|---|---|---|---|
| 2025-01 | UP | 285 | -5.698 | 0.8605 |
| 2025-02 | DOWN | 291 | 6.974 | 1.2060 |
| 2025-03 | FLAT | 271 | -9.500 | 0.7674 |
| 2025-04 | UP | 256 | -5.608 | 0.8409 |
| 2025-05 | UP | 319 | -19.920 | 0.6572 |
| 2025-06 | FLAT | 287 | -10.793 | 0.7584 |
| 2025-07 | UP | 292 | -17.632 | 0.6576 |
| 2025-08 | DOWN | 289 | -11.687 | 0.7375 |
| 2025-09 | UP | 258 | -4.273 | 0.8750 |
| 2025-10 | FLAT | 310 | -11.161 | 0.7865 |
| 2025-11 | DOWN | 262 | -1.144 | 0.9685 |
| 2025-12 | FLAT | 250 | -26.255 | 0.4940 |

_Güç notu:_ PF %90 GA üst sınırı: aile arm'ları arasında en yükseği 0.8197 (S30); bu veriyle bunun üstünde bir gerçek PF savunulamaz (kenar yokluğunun kanıtı değil, güç sınırıdır).
_Önceden yazılmış cümle:_ Büyük grafikte C'nin para kazandırdığına dair kanıt yok.
