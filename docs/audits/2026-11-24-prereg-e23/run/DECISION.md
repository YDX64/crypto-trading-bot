# E23 karar raporu

## Sonuç etiketi: DEV-YOK

Bu ailede (BTC gün-içi şok tetikleyicisi %1.5 ve %3.0, yalnız BTC yönünde; 30m kırılım ve geri çekilme) tetikleyici açıkken yapılan momentum girişinin komisyon ve kayma sonrası kenar ürettiğine dair kanıt yok.

## Bütünlük

| Alan | Değer |
|---|---|
| H | 3672f29f41f83d62ee8bdbb95c603b042c9031d5 |
| L | 9a38da3d864e975d36214c8ae75123d49e263928 |
| LOCK.json sha256 | d9b3838b101c7da4e6f87ea49953f1d1dcd01818d3ec42b0835ffd4cc01d833f |
| ledger satırı | 18 |
| snapshot sha256 | 2a4748a3d0e822751c2da071a105e8a5b5391206b62d6e3f27515a7789e841a2 |

## Aile tablosu (DEV, 2025)

| Kol | N | WR % | PF | ΣR | ort R | t_obs | p(b=7) | p(b=28) | BH q | PF %90 GA | DD_R | Calmar_R |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| K30R-L | 496 | 48.1855 | 0.8872 | -26.5053 | -0.0534 | -0.7017 | 0.7348 | 0.7309 | 0.9995 | [0.6484, 1.1669] | 70.5585 | -0.3756 |
| K30B-L | 556 | 47.1223 | 0.8706 | -35.9211 | -0.0646 | -0.7983 | 0.7762 | 0.8176 | 0.9995 | [0.6381, 1.1311] | 60.3813 | -0.5949 |
| K30B-H | 223 | 47.0852 | 0.8933 | -12.1435 | -0.0545 | -0.4625 | 0.6690 | 0.7019 | 0.9995 | [0.5848, 1.2923] | 27.1201 | -0.4478 |
| G30B-L | 335 | 41.1940 | 0.5882 | -70.6866 | -0.2110 | -2.9453 | 0.9995 | 0.9977 | 0.9995 | [0.4356, 0.7763] | 72.0620 | -0.9809 |

## Kapı matrisi

| Kapı | DEV K30R-L | DEV K30B-L | DEV K30B-H | DEV G30B-L |
|---|---|---|---|---|
| G0 check-run geçti (ledger) | PASS 1 >= 1 | PASS 1 >= 1 | PASS 1 >= 1 | PASS 1 >= 1 |
| G0 sessiz düşüş / ham sinyal | PASS 0.000000 <= 0.100000 | PASS 0.000000 <= 0.100000 | PASS 0.000000 <= 0.100000 | PASS 0.000000 <= 0.100000 |
| G0 EOD işlem sayısı | PASS 0 <= 8 | PASS 0 <= 8 | PASS 0 <= 8 | PASS 0 <= 8 |
| G0 tetikleyici ihlali | PASS 0 <= 0 SINIRDA | PASS 0 <= 0 SINIRDA | PASS 0 <= 0 SINIRDA | PASS 0 <= 0 SINIRDA |
| G0 veri-yok karar noktası | PASS 0 <= 0 SINIRDA | PASS 0 <= 0 SINIRDA | PASS 0 <= 0 SINIRDA | PASS 0 <= 0 SINIRDA |
| G1 havuz N | PASS 496 >= 100 | PASS 556 >= 100 | PASS 223 >= 100 | PASS 335 >= 100 |
| G1 aktif çeyrek (N>=10) | PASS 4 >= 3 | PASS 4 >= 3 | PASS 4 >= 3 | PASS 4 >= 3 |
| G2 havuz PF | FAIL 0.887226 >= 1.150000 | FAIL 0.870550 >= 1.150000 | FAIL 0.893325 >= 1.150000 | FAIL 0.588223 >= 1.150000 |
| G3 BH q | FAIL 0.999500 <= 0.100000 | FAIL 0.999500 <= 0.100000 | FAIL 0.999500 <= 0.100000 | FAIL 0.999500 <= 0.100000 |
| G4 ΣR>0 olan aktif çeyrek | FAIL 1 >= 3 | FAIL 1 >= 3 | PASS 3 >= 3 SINIRDA | FAIL 1 >= 3 |
| G5 PF (en iyi ay hariç) | FAIL 0.814968 >= 1.000000 | FAIL 0.805759 >= 1.000000 | FAIL 0.766064 >= 1.000000 | FAIL 0.537525 >= 1.000000 |
| G5 ΣR (en iyi 5 işlem hariç) | FAIL -52.709149 > 0.000000 | FAIL -64.485338 > 0.000000 | FAIL -36.591844 > 0.000000 | FAIL -83.521998 > 0.000000 |
| G5 ΣR (en iyi 3 gün hariç) | FAIL -67.600730 > 0.000000 | FAIL -89.508210 > 0.000000 | FAIL -42.709314 > 0.000000 | FAIL -84.461157 > 0.000000 |

DEV kararı: DEV-YOK

## Ayrıntı tabloları

### DEV: tetikleyici etkinliği (eşik başına, lider verisinden)

| Eşik % | Kaynak kol | Tetikleyici günü | Şok günü | Kesişim | İsabet | Yön isabeti | Kapsama | İki yönde açılan gün | Kollar tutarlı |
|---|---|---|---|---|---|---|---|---|---|
| 1.5 | K30R-L | 242 | 52 | 52 | 0.2149 | 0.1818 | 1.0000 | 27 | EVET |
| 3.0 | K30B-H | 92 | 52 | 52 | 0.5652 | 0.5543 | 1.0000 | 2 | EVET |

İlk açılış saati (UTC, saat + dakika/60) ve 4 saatlik dilimler:

| Eşik % | Ortalama | Medyan | 00-04 | 04-08 | 08-12 | 12-16 | 16-20 | 20-24 |
|---|---|---|---|---|---|---|---|---|
| 1.5 | 11.4503 | 11.9833 | 40 | 45 | 37 | 60 | 45 | 15 |
| 3.0 | 14.7388 | 15.4833 | 5 | 5 | 14 | 30 | 26 | 12 |

Şok etiketi: DEV K30R-L: E18 listesiyle örtüşen 365 gün, uyuşmayan 0 gün (kapı değil).
Şok etiketi: DEV K30B-L: E18 listesiyle örtüşen 365 gün, uyuşmayan 0 gün (kapı değil).
Şok etiketi: DEV K30B-H: E18 listesiyle örtüşen 365 gün, uyuşmayan 0 gün (kapı değil).
Şok etiketi: DEV G30B-L: E18 listesiyle örtüşen 365 gün, uyuşmayan 0 gün (kapı değil).

### DEV K30R-L

Pencere (giriş çeyreği; aktif = N >= 10):

| Pencere | N | ΣR | PF | Aktif |
|---|---|---|---|---|
| 2025Q1 | 138 | -2.9668 | 0.9501 | evet |
| 2025Q2 | 106 | 9.2665 | 1.1697 | evet |
| 2025Q3 | 90 | -30.5067 | 0.4119 | evet |
| 2025Q4 | 162 | -2.2983 | 0.9668 | evet |

Ay (giriş ayı):

| Ay | N | WR % | PF | ΣR |
|---|---|---|---|---|
| 2025-01 | 42 | 42.8571 | 0.5989 | -8.8804 |
| 2025-02 | 45 | 57.7778 | 1.0729 | 1.2051 |
| 2025-03 | 51 | 58.8235 | 1.2272 | 4.7085 |
| 2025-04 | 32 | 50.0000 | 1.7037 | 10.7276 |
| 2025-05 | 41 | 51.2195 | 1.8296 | 13.8857 |
| 2025-06 | 33 | 36.3636 | 0.3220 | -15.3468 |
| 2025-07 | 42 | 35.7143 | 0.5243 | -11.9664 |
| 2025-08 | 32 | 37.5000 | 0.3068 | -12.1802 |
| 2025-09 | 16 | 37.5000 | 0.3050 | -6.3600 |
| 2025-10 | 55 | 43.6364 | 0.8448 | -4.1199 |
| 2025-11 | 64 | 59.3750 | 1.3257 | 7.2765 |
| 2025-12 | 43 | 48.8372 | 0.7307 | -5.4550 |

LONG ve SHORT:

| Yön | N | WR % | PF | ΣR |
|---|---|---|---|---|
| LONG | 154 | 40.2597 | 0.7240 | -23.8789 |
| SHORT | 342 | 51.7544 | 0.9823 | -2.6263 |

Şok günü ve normal gün (SİNYAL gününe göre; şok = koşunun lider 1d mumundan):

| Grup | N | ΣR | PF |
|---|---|---|---|
| şok günü tümü | 148 | 85.6992 | 2.8587 |
| şok günü LONG | 36 | 28.9304 | 3.2587 |
| şok günü SHORT | 112 | 56.7688 | 2.7048 |
| normal gün tümü | 348 | -112.2045 | 0.4061 |
| normal gün LONG | 118 | -52.8093 | 0.2835 |
| normal gün SHORT | 230 | -59.3952 | 0.4845 |
| şok günü, şokla AYNI yön | 145 | 88.9033 | 3.0722 |
| şok günü, şoka KARŞI yön | 3 | -3.2041 | 0.0000 |

Yoğunluk: tetikleyici günü başına işlem = 2.0496; takvim günü başına işlem = 1.3589; işlemli tetikleyici günü = 145 / 242

Rejim grubu (giriş ayı etiketi):

| Rejim | N | ΣR | PF |
|---|---|---|---|
| UP | 173 | -2.5936 | 0.9707 |
| FLAT | 182 | -20.2131 | 0.7758 |
| DOWN | 141 | -3.6986 | 0.9345 |

Sembol:

| Sembol | N | WR % | PF | ΣR |
|---|---|---|---|---|
| BTCUSDT | 100 | 40.0000 | 0.5815 | -22.6676 |
| ETHUSDT | 65 | 64.6154 | 2.0358 | 20.3566 |
| SOLUSDT | 70 | 42.8571 | 0.9579 | -1.4415 |
| XRPUSDT | 66 | 48.4848 | 0.8725 | -3.9709 |
| DOGEUSDT | 64 | 50.0000 | 0.8889 | -3.1607 |
| BNBUSDT | 55 | 50.9091 | 0.8633 | -3.9552 |
| ADAUSDT | 42 | 50.0000 | 0.8930 | -2.1485 |
| LTCUSDT | 34 | 41.1765 | 0.4807 | -9.5176 |

Çıkış nedeni karışımı: REAPER=95, SL=190, TRAIL=211

Kaldıraç payı: 20x=0.0262 3x=0.0343

Kaçan sinyal sayaçları: {"capacity": 79, "daily_latch": 135, "shock_trigger_dir": 24, "shock_trigger_off": 2191}; sessiz düşüş / ham sinyal = 0.0000 (0/710)

Bayraklar: TEK-YÖNLÜ, NORMAL-ZAYIF, SINIRDA

### DEV K30B-L

Pencere (giriş çeyreği; aktif = N >= 10):

| Pencere | N | ΣR | PF | Aktif |
|---|---|---|---|---|
| 2025Q1 | 146 | -5.7144 | 0.9176 | evet |
| 2025Q2 | 124 | 0.2261 | 1.0034 | evet |
| 2025Q3 | 104 | -21.8907 | 0.5968 | evet |
| 2025Q4 | 182 | -8.5421 | 0.9012 | evet |

Ay (giriş ayı):

| Ay | N | WR % | PF | ΣR |
|---|---|---|---|---|
| 2025-01 | 60 | 40.0000 | 0.5054 | -16.9379 |
| 2025-02 | 41 | 58.5366 | 2.0233 | 15.1107 |
| 2025-03 | 45 | 53.3333 | 0.8092 | -3.8871 |
| 2025-04 | 43 | 51.1628 | 1.5046 | 10.9837 |
| 2025-05 | 50 | 48.0000 | 1.2668 | 6.0489 |
| 2025-06 | 31 | 32.2581 | 0.2659 | -16.8065 |
| 2025-07 | 40 | 47.5000 | 1.0400 | 0.7913 |
| 2025-08 | 42 | 42.8571 | 0.3791 | -13.5488 |
| 2025-09 | 22 | 36.3636 | 0.2792 | -9.1332 |
| 2025-10 | 67 | 41.7910 | 1.0041 | 0.1392 |
| 2025-11 | 66 | 60.6061 | 1.2105 | 5.0209 |
| 2025-12 | 49 | 42.8571 | 0.5149 | -13.7022 |

LONG ve SHORT:

| Yön | N | WR % | PF | ΣR |
|---|---|---|---|---|
| LONG | 204 | 41.6667 | 0.6830 | -36.6873 |
| SHORT | 352 | 50.2841 | 1.0047 | 0.7662 |

Şok günü ve normal gün (SİNYAL gününe göre; şok = koşunun lider 1d mumundan):

| Grup | N | ΣR | PF |
|---|---|---|---|
| şok günü tümü | 158 | 73.4017 | 2.2847 |
| şok günü LONG | 50 | 29.9945 | 2.7162 |
| şok günü SHORT | 108 | 43.4072 | 2.0946 |
| normal gün tümü | 398 | -109.3228 | 0.5039 |
| normal gün LONG | 154 | -66.6818 | 0.3214 |
| normal gün SHORT | 244 | -42.6410 | 0.6508 |
| şok günü, şokla AYNI yön | 153 | 74.7840 | 2.3867 |
| şok günü, şoka KARŞI yön | 5 | -1.3823 | 0.5686 |

Yoğunluk: tetikleyici günü başına işlem = 2.2975; takvim günü başına işlem = 1.5233; işlemli tetikleyici günü = 162 / 242

Rejim grubu (giriş ayı etiketi):

| Rejim | N | ΣR | PF |
|---|---|---|---|
| UP | 215 | -8.2472 | 0.9258 |
| FLAT | 192 | -34.2566 | 0.6765 |
| DOWN | 149 | 6.5828 | 1.1089 |

Sembol:

| Sembol | N | WR % | PF | ΣR |
|---|---|---|---|---|
| BTCUSDT | 113 | 40.7080 | 0.5338 | -29.9839 |
| ETHUSDT | 68 | 54.4118 | 1.4516 | 11.5953 |
| SOLUSDT | 75 | 48.0000 | 0.8868 | -4.0497 |
| XRPUSDT | 76 | 44.7368 | 0.7082 | -12.0896 |
| DOGEUSDT | 72 | 58.3333 | 1.6488 | 17.4402 |
| BNBUSDT | 59 | 44.0678 | 0.8418 | -5.6427 |
| ADAUSDT | 50 | 50.0000 | 1.1044 | 2.4361 |
| LTCUSDT | 43 | 37.2093 | 0.3600 | -15.6268 |

Çıkış nedeni karışımı: REAPER=92, SL=230, TRAIL=234

Kaldıraç payı: 20x=0.0288 3x=0.0252

Kaçan sinyal sayaçları: {"capacity": 88, "daily_latch": 148, "shock_trigger_dir": 16, "shock_trigger_off": 2524}; sessiz düşüş / ham sinyal = 0.0000 (0/792)

Bayraklar: TEK-YÖNLÜ, NORMAL-ZAYIF, SINIRDA

### DEV K30B-H

Pencere (giriş çeyreği; aktif = N >= 10):

| Pencere | N | ΣR | PF | Aktif |
|---|---|---|---|---|
| 2025Q1 | 90 | -24.8707 | 0.5154 | evet |
| 2025Q2 | 45 | 7.9541 | 1.3543 | evet |
| 2025Q3 | 25 | 0.1801 | 1.0146 | evet |
| 2025Q4 | 63 | 4.5930 | 1.1658 | evet |

Ay (giriş ayı):

| Ay | N | WR % | PF | ΣR |
|---|---|---|---|---|
| 2025-01 | 31 | 41.9355 | 0.4032 | -10.4418 |
| 2025-02 | 28 | 53.5714 | 0.9971 | -0.0363 |
| 2025-03 | 31 | 29.0323 | 0.3240 | -14.3926 |
| 2025-04 | 23 | 52.1739 | 2.1233 | 11.9899 |
| 2025-05 | 14 | 42.8571 | 0.9574 | -0.2930 |
| 2025-06 | 8 | 37.5000 | 0.2349 | -3.7428 |
| 2025-07 | 11 | 72.7273 | 3.7315 | 7.7510 |
| 2025-08 | 9 | 44.4444 | 0.4221 | -2.6649 |
| 2025-09 | 5 | 0.0000 | 0.0000 | -4.9060 |
| 2025-10 | 12 | 25.0000 | 1.4635 | 4.4521 |
| 2025-11 | 32 | 59.3750 | 1.1068 | 1.2485 |
| 2025-12 | 19 | 68.4211 | 0.8271 | -1.1076 |

LONG ve SHORT:

| Yön | N | WR % | PF | ΣR |
|---|---|---|---|---|
| LONG | 57 | 52.6316 | 1.2371 | 5.9728 |
| SHORT | 166 | 45.1807 | 0.7956 | -18.1163 |

Şok günü ve normal gün (SİNYAL gününe göre; şok = koşunun lider 1d mumundan):

| Grup | N | ΣR | PF |
|---|---|---|---|
| şok günü tümü | 135 | 23.9615 | 1.4100 |
| şok günü LONG | 40 | 11.6301 | 1.7834 |
| şok günü SHORT | 95 | 12.3314 | 1.2829 |
| normal gün tümü | 88 | -36.1050 | 0.3483 |
| normal gün LONG | 17 | -5.6573 | 0.4529 |
| normal gün SHORT | 71 | -30.4477 | 0.3243 |
| şok günü, şokla AYNI yön | 131 | 22.2650 | 1.3879 |
| şok günü, şoka KARŞI yön | 4 | 1.6965 | 2.6264 |

Yoğunluk: tetikleyici günü başına işlem = 2.4239; takvim günü başına işlem = 0.6110; işlemli tetikleyici günü = 60 / 92

Rejim grubu (giriş ayı etiketi):

| Rejim | N | ΣR | PF |
|---|---|---|---|
| UP | 84 | 4.1001 | 1.0958 |
| FLAT | 70 | -14.7908 | 0.6495 |
| DOWN | 69 | -1.4528 | 0.9496 |

Sembol:

| Sembol | N | WR % | PF | ΣR |
|---|---|---|---|---|
| BTCUSDT | 42 | 42.8571 | 0.6760 | -7.3988 |
| ETHUSDT | 36 | 47.2222 | 0.7987 | -3.8041 |
| SOLUSDT | 28 | 57.1429 | 0.9911 | -0.1007 |
| XRPUSDT | 34 | 38.2353 | 0.6678 | -7.3704 |
| DOGEUSDT | 33 | 54.5455 | 1.7236 | 9.4649 |
| BNBUSDT | 21 | 38.0952 | 0.3818 | -8.7810 |
| ADAUSDT | 16 | 43.7500 | 2.0521 | 6.4103 |
| LTCUSDT | 13 | 61.5385 | 0.8932 | -0.5637 |

Çıkış nedeni karışımı: REAPER=32, SL=97, TRAIL=94

Kaldıraç payı: 20x=0.0045 3x=0.0807

Kaçan sinyal sayaçları: {"capacity": 50, "daily_latch": 40, "shock_trigger_off": 3510}; sessiz düşüş / ham sinyal = 0.0000 (0/313)

Bayraklar: TEK-YÖNLÜ, NORMAL-ZAYIF, SINIRDA

### DEV G30B-L

Pencere (giriş çeyreği; aktif = N >= 10):

| Pencere | N | ΣR | PF | Aktif |
|---|---|---|---|---|
| 2025Q1 | 119 | -45.1648 | 0.3841 | evet |
| 2025Q2 | 43 | -7.4952 | 0.6702 | evet |
| 2025Q3 | 70 | 1.7736 | 1.0751 | evet |
| 2025Q4 | 103 | -19.8002 | 0.6191 | evet |

Ay (giriş ayı):

| Ay | N | WR % | PF | ΣR |
|---|---|---|---|---|
| 2025-01 | 47 | 25.5319 | 0.2152 | -26.0723 |
| 2025-02 | 40 | 35.0000 | 0.5209 | -11.4641 |
| 2025-03 | 32 | 50.0000 | 0.5285 | -7.6284 |
| 2025-04 | 16 | 25.0000 | 0.2420 | -9.0406 |
| 2025-05 | 16 | 62.5000 | 1.8143 | 4.7062 |
| 2025-06 | 11 | 27.2727 | 0.3700 | -3.1607 |
| 2025-07 | 17 | 41.1765 | 0.5080 | -3.6646 |
| 2025-08 | 31 | 54.8387 | 0.8347 | -2.3455 |
| 2025-09 | 22 | 68.1818 | 4.9162 | 7.7837 |
| 2025-10 | 27 | 37.0370 | 0.7476 | -3.5116 |
| 2025-11 | 58 | 46.5517 | 0.8393 | -3.9565 |
| 2025-12 | 18 | 16.6667 | 0.0832 | -12.3321 |

LONG ve SHORT:

| Yön | N | WR % | PF | ΣR |
|---|---|---|---|---|
| LONG | 130 | 45.3846 | 0.7383 | -15.8591 |
| SHORT | 205 | 38.5366 | 0.5064 | -54.8275 |

Şok günü ve normal gün (SİNYAL gününe göre; şok = koşunun lider 1d mumundan):

| Grup | N | ΣR | PF |
|---|---|---|---|
| şok günü tümü | 95 | 19.0883 | 1.6747 |
| şok günü LONG | 27 | 4.5396 | 1.5484 |
| şok günü SHORT | 68 | 14.5487 | 1.7270 |
| normal gün tümü | 240 | -89.7749 | 0.3738 |
| normal gün LONG | 103 | -20.3987 | 0.6101 |
| normal gün SHORT | 137 | -69.3762 | 0.2381 |
| şok günü, şokla AYNI yön | 88 | 23.4800 | 2.0159 |
| şok günü, şoka KARŞI yön | 7 | -4.3916 | 0.1519 |

Yoğunluk: tetikleyici günü başına işlem = 1.3843; takvim günü başına işlem = 0.9178; işlemli tetikleyici günü = 115 / 242

Rejim grubu (giriş ayı etiketi):

| Rejim | N | ΣR | PF |
|---|---|---|---|
| UP | 118 | -26.2876 | 0.5645 |
| FLAT | 88 | -26.6328 | 0.4515 |
| DOWN | 129 | -17.7661 | 0.7168 |

Sembol:

| Sembol | N | WR % | PF | ΣR |
|---|---|---|---|---|
| BTCUSDT | 43 | 34.8837 | 0.3748 | -14.4958 |
| ETHUSDT | 33 | 33.3333 | 0.2631 | -13.5379 |
| SOLUSDT | 45 | 35.5556 | 0.4678 | -14.4132 |
| XRPUSDT | 49 | 44.8980 | 0.8519 | -2.9762 |
| DOGEUSDT | 38 | 42.1053 | 0.6778 | -6.0798 |
| BNBUSDT | 40 | 45.0000 | 0.8501 | -3.0509 |
| ADAUSDT | 39 | 56.4103 | 1.1170 | 1.6662 |
| LTCUSDT | 48 | 37.5000 | 0.3960 | -17.7989 |

Çıkış nedeni karışımı: REAPER=69, SL=144, TRAIL=122

Kaldıraç payı: 20x=0.0090 3x=0.0567

Kaçan sinyal sayaçları: {"capacity": 23, "daily_latch": 50, "shock_trigger_dir": 123, "shock_trigger_off": 4110}; sessiz düşüş / ham sinyal = 0.0000 (0/408)

Bayraklar: TEK-YÖNLÜ, NORMAL-ZAYIF, SINIRDA

## Stres koşuları

| Set kol | Kaplama | N | PF | ΣR |
|---|---|---|---|---|

## Teşhis koşuları (karar vermez)

| Kol | Koşu | N | PF | ΣR | Okuma |
|---|---|---|---|---|---|
| K30R-L | DIAG_Z0 | 493 | 1.1823 | 36.7258 | ham kenar var, maliyet yiyor |
| K30R-L | DIAG_NOLATCH | 606 | 0.9600 | -11.1938 |  |
| K30B-L | DIAG_Z0 | 581 | 1.1316 | 32.9161 | ham kenar var, maliyet yiyor |
| K30B-L | DIAG_NOLATCH | 676 | 0.8532 | -48.7451 |  |
| K30B-H | DIAG_Z0 | 224 | 1.0421 | 4.4292 | maliyetten önce de kenar yok |
| K30B-H | DIAG_NOLATCH | 257 | 0.8156 | -25.6107 |  |
| G30B-L | DIAG_Z0 | 333 | 0.7229 | -43.1662 | maliyetten önce de kenar yok |
| G30B-L | DIAG_NOLATCH | 380 | 0.5911 | -80.8939 |  |

## Sınırlar

1. DEV bu soru için KİRLİDİR (fikir E18'in 2025 şok-günü tablosu görülerek yazıldı): DEV'i geçmek kanıt değildir, kanıt yükü VAL'dedir. Önsel olasılık düşük; "kazanan yok" çıkması muhtemeldir.
2. Backtest canlıdan iyimserdir (TP1'e ulaşma %66, canlı %54). Kazanan çıksa bile yalnız "testnet ölçüm adayı"dır.

