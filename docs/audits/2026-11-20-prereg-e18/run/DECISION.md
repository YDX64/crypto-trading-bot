# E18 karar raporu

## Sonuç etiketi: DEV-YOK

Bu ailede (kırılım ve geri çekilme, 15m ve 30m, iki trend tanımı) trend yönlü girişin komisyon ve kayma sonrası kenar ürettiğine dair kanıt yok.

## Bütünlük

| Alan | Değer |
|---|---|
| H | 425e82a6092ba0614071b6858538331093ed3a83 |
| L | cab30bb70c65e2d4e475520c4bc27e2ab5a29508 |
| LOCK.json sha256 | 3607e392161efa0ca0584f73d44760fd2efd2936617d683d77f8a5cf84ce67e6 |
| ledger satırı | 42 |
| snapshot sha256 | 2a4748a3d0e822751c2da071a105e8a5b5391206b62d6e3f27515a7789e841a2 |

## Aile tablosu (DEV, 2025)

| Kol | N | WR % | PF | ΣR | ort R | t_obs | p(b=7) | p(b=28) | BH q | PF %90 GA | DD_R | Calmar_R |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| K15R | 1434 | 47.5593 | 0.7073 | -241.6481 | -0.1685 | -4.1614 | 0.9998 | 1.0000 | 1.0000 | [0.6028, 0.8193] | 249.2803 | -0.9694 |
| K30R | 1073 | 47.3439 | 0.8802 | -64.0588 | -0.0597 | -1.0898 | 0.8201 | 0.8313 | 1.0000 | [0.7008, 1.0813] | 119.6265 | -0.5355 |
| K15B | 1635 | 44.8930 | 0.6422 | -353.2071 | -0.2160 | -6.0400 | 1.0000 | 1.0000 | 1.0000 | [0.5436, 0.7533] | 361.0540 | -0.9783 |
| K30B | 1148 | 47.2997 | 0.8643 | -79.1334 | -0.0689 | -1.3071 | 0.8573 | 0.8758 | 1.0000 | [0.6867, 1.0642] | 107.4243 | -0.7366 |
| G15B | 2268 | 50.6614 | 0.8364 | -196.8925 | -0.0868 | -2.7772 | 0.9818 | 0.9594 | 1.0000 | [0.7275, 0.9541] | 251.0111 | -0.7844 |
| G30B | 1353 | 45.8241 | 0.7515 | -169.6345 | -0.1254 | -3.4088 | 0.9988 | 0.9993 | 1.0000 | [0.6410, 0.8722] | 171.4405 | -0.9895 |

## Kapı matrisi

| Kapı | DEV K15R | DEV K30R | DEV K15B | DEV K30B | DEV G15B | DEV G30B |
|---|---|---|---|---|---|---|
| G0 check-run geçti (ledger) | PASS 1 >= 1 | PASS 1 >= 1 | PASS 1 >= 1 | PASS 1 >= 1 | PASS 1 >= 1 | PASS 1 >= 1 |
| G0 sessiz düşüş / ham sinyal | PASS 0.000000 <= 0.100000 | PASS 0.000000 <= 0.100000 | PASS 0.000000 <= 0.100000 | PASS 0.000000 <= 0.100000 | PASS 0.000000 <= 0.100000 | PASS 0.000000 <= 0.100000 |
| G0 EOD işlem sayısı | PASS 0 <= 8 | PASS 0 <= 8 | PASS 0 <= 8 | PASS 0 <= 8 | PASS 0 <= 8 | PASS 0 <= 8 |
| G1 pencere başına en az N | PASS 297 >= 60 | PASS 242 >= 60 | PASS 373 >= 60 | PASS 272 >= 60 | PASS 517 >= 60 | PASS 312 >= 60 |
| G2 havuz PF | FAIL 0.707254 >= 1.150000 | FAIL 0.880184 >= 1.150000 | FAIL 0.642166 >= 1.150000 | FAIL 0.864262 >= 1.150000 | FAIL 0.836363 >= 1.150000 | FAIL 0.751466 >= 1.150000 |
| G3 BH q | FAIL 1.000000 <= 0.100000 | FAIL 1.000000 <= 0.100000 | FAIL 1.000000 <= 0.100000 | FAIL 1.000000 <= 0.100000 | FAIL 1.000000 <= 0.100000 | FAIL 1.000000 <= 0.100000 |
| G4 ΣR>0 olan tam pencere | FAIL 0 >= 3 | FAIL 1 >= 3 | FAIL 0 >= 3 | FAIL 0 >= 3 | FAIL 1 >= 3 | FAIL 0 >= 3 |
| G5 PF (en iyi ay hariç) | FAIL 0.672102 >= 1.000000 | FAIL 0.803198 >= 1.000000 | FAIL 0.601590 >= 1.000000 | FAIL 0.796280 >= 1.000000 | FAIL 0.775641 >= 1.000000 | FAIL 0.716922 >= 1.000000 |
| G5 ΣR (en iyi 5 işlem hariç) | FAIL -274.673578 > 0.000000 | FAIL -97.710145 > 0.000000 | FAIL -389.310873 > 0.000000 | FAIL -117.748831 > 0.000000 | FAIL -226.083931 > 0.000000 | FAIL -208.079656 > 0.000000 |

DEV kararı: DEV-YOK

## Ayrıntı tabloları

### DEV K15R

Pencere (giriş çeyreği):

| Pencere | N | ΣR | PF |
|---|---|---|---|
| 2025Q1 | 402 | -63.1446 | 0.7125 |
| 2025Q2 | 347 | -34.0934 | 0.8262 |
| 2025Q3 | 297 | -73.2389 | 0.5938 |
| 2025Q4 | 388 | -71.1712 | 0.6898 |

LONG ve SHORT:

| Yön | N | WR % | PF | ΣR |
|---|---|---|---|---|
| LONG | 524 | 46.9466 | 0.6929 | -94.1398 |
| SHORT | 910 | 47.9121 | 0.7157 | -147.5082 |

Şok günü ve normal gün (giriş gününe göre):

| Grup | N | ΣR | PF |
|---|---|---|---|
| şok günü | 258 | 14.4819 | 1.1051 |
| normal gün | 1176 | -256.1300 | 0.6275 |
| şok günü, şokla AYNI yön | 182 | 60.8127 | 1.7785 |
| şok günü, şoka KARŞI yön | 76 | -46.3307 | 0.2238 |

Rejim grubu (giriş ayı etiketi):

| Rejim | N | ΣR | PF |
|---|---|---|---|
| UP | 540 | -87.7708 | 0.7203 |
| FLAT | 475 | -83.9786 | 0.6995 |
| DOWN | 419 | -69.8987 | 0.6989 |

Sembol:

| Sembol | N | WR % | PF | ΣR |
|---|---|---|---|---|
| BTCUSDT | 183 | 41.5301 | 0.5416 | -55.0507 |
| ETHUSDT | 215 | 48.3721 | 0.8145 | -22.5807 |
| SOLUSDT | 189 | 51.3228 | 0.7698 | -23.2775 |
| XRPUSDT | 173 | 46.8208 | 0.6735 | -32.6078 |
| DOGEUSDT | 193 | 51.2953 | 0.9224 | -7.8266 |
| BNBUSDT | 175 | 49.1429 | 0.7142 | -29.4051 |
| ADAUSDT | 170 | 47.6471 | 0.6207 | -36.7719 |
| LTCUSDT | 136 | 42.6471 | 0.5834 | -34.1276 |

Çıkış nedeni karışımı: REAPER=73, SL=709, TRAIL=652

Kaldıraç payı: 20x=0.0718 3x=0.0258

Kaçan sinyal sayaçları: {"capacity": 139, "daily_latch": 1307}; sessiz düşüş / ham sinyal = 0.0000 (0/2880)

Bayraklar: TEK-YÖNLÜ

### DEV K30R

Pencere (giriş çeyreği):

| Pencere | N | ΣR | PF |
|---|---|---|---|
| 2025Q1 | 263 | 21.3266 | 1.1722 |
| 2025Q2 | 242 | -3.1062 | 0.9757 |
| 2025Q3 | 288 | -32.8410 | 0.7545 |
| 2025Q4 | 280 | -49.4382 | 0.6689 |

LONG ve SHORT:

| Yön | N | WR % | PF | ΣR |
|---|---|---|---|---|
| LONG | 408 | 49.7549 | 0.9485 | -10.0302 |
| SHORT | 665 | 45.8647 | 0.8410 | -54.0286 |

Şok günü ve normal gün (giriş gününe göre):

| Grup | N | ΣR | PF |
|---|---|---|---|
| şok günü | 179 | 70.7719 | 1.8752 |
| normal gün | 894 | -134.8308 | 0.7029 |
| şok günü, şokla AYNI yön | 130 | 108.2699 | 3.8919 |
| şok günü, şoka KARŞI yön | 49 | -37.4980 | 0.1364 |

Rejim grubu (giriş ayı etiketi):

| Rejim | N | ΣR | PF |
|---|---|---|---|
| UP | 432 | -33.7487 | 0.8486 |
| FLAT | 357 | -63.5096 | 0.6681 |
| DOWN | 284 | 33.1995 | 1.2759 |

Sembol:

| Sembol | N | WR % | PF | ΣR |
|---|---|---|---|---|
| BTCUSDT | 149 | 44.9664 | 0.8175 | -13.4281 |
| ETHUSDT | 142 | 53.5211 | 1.3348 | 20.5570 |
| SOLUSDT | 144 | 42.3611 | 0.7808 | -16.9316 |
| XRPUSDT | 128 | 46.0938 | 0.6771 | -22.0012 |
| DOGEUSDT | 140 | 47.1429 | 0.9303 | -4.8347 |
| BNBUSDT | 131 | 49.6183 | 0.9445 | -3.5434 |
| ADAUSDT | 136 | 47.0588 | 0.8014 | -13.5149 |
| LTCUSDT | 103 | 48.5437 | 0.8047 | -10.3620 |

Çıkış nedeni karışımı: REAPER=182, SL=441, TRAIL=450

Kaldıraç payı: 20x=0.0839 3x=0.0196

Kaçan sinyal sayaçları: {"capacity": 122, "daily_latch": 483}; sessiz düşüş / ham sinyal = 0.0000 (0/1678)

Bayraklar: TEK-YÖNLÜ

### DEV K15B

Pencere (giriş çeyreği):

| Pencere | N | ΣR | PF |
|---|---|---|---|
| 2025Q1 | 416 | -92.5784 | 0.6274 |
| 2025Q2 | 432 | -58.0586 | 0.7724 |
| 2025Q3 | 414 | -85.4704 | 0.6448 |
| 2025Q4 | 373 | -117.0998 | 0.5180 |

LONG ve SHORT:

| Yön | N | WR % | PF | ΣR |
|---|---|---|---|---|
| LONG | 772 | 44.6891 | 0.6156 | -181.2938 |
| SHORT | 863 | 45.0753 | 0.6665 | -171.9133 |

Şok günü ve normal gün (giriş gününe göre):

| Grup | N | ΣR | PF |
|---|---|---|---|
| şok günü | 270 | 25.9998 | 1.1857 |
| normal gün | 1365 | -379.2069 | 0.5523 |
| şok günü, şokla AYNI yön | 201 | 58.8732 | 1.6625 |
| şok günü, şoka KARŞI yön | 69 | -32.8734 | 0.3576 |

Rejim grubu (giriş ayı etiketi):

| Rejim | N | ΣR | PF |
|---|---|---|---|
| UP | 683 | -87.9954 | 0.7748 |
| FLAT | 525 | -151.1771 | 0.5395 |
| DOWN | 427 | -114.0346 | 0.5745 |

Sembol:

| Sembol | N | WR % | PF | ΣR |
|---|---|---|---|---|
| BTCUSDT | 222 | 41.4414 | 0.4247 | -82.3865 |
| ETHUSDT | 209 | 42.5837 | 0.5907 | -54.2730 |
| SOLUSDT | 238 | 46.6387 | 0.7635 | -32.4287 |
| XRPUSDT | 211 | 47.3934 | 0.7264 | -33.4230 |
| DOGEUSDT | 210 | 47.6190 | 0.7147 | -33.4518 |
| BNBUSDT | 196 | 46.9388 | 0.7260 | -33.1077 |
| ADAUSDT | 189 | 43.9153 | 0.5550 | -50.7353 |
| LTCUSDT | 160 | 41.8750 | 0.6656 | -33.4011 |

Çıkış nedeni karışımı: REAPER=88, SL=851, TRAIL=696

Kaldıraç payı: 20x=0.0777 3x=0.0263

Kaçan sinyal sayaçları: {"capacity": 196, "daily_latch": 2324}; sessiz düşüş / ham sinyal = 0.0000 (0/4155)

Bayraklar: TEK-YÖNLÜ

### DEV K30B

Pencere (giriş çeyreği):

| Pencere | N | ΣR | PF |
|---|---|---|---|
| 2025Q1 | 281 | -0.4400 | 0.9969 |
| 2025Q2 | 273 | -26.3204 | 0.8184 |
| 2025Q3 | 322 | -36.0576 | 0.7707 |
| 2025Q4 | 272 | -16.3154 | 0.8836 |

LONG ve SHORT:

| Yön | N | WR % | PF | ΣR |
|---|---|---|---|---|
| LONG | 511 | 47.9452 | 0.8438 | -40.4146 |
| SHORT | 637 | 46.7818 | 0.8806 | -38.7188 |

Şok günü ve normal gün (giriş gününe göre):

| Grup | N | ΣR | PF |
|---|---|---|---|
| şok günü | 196 | 91.2474 | 2.0945 |
| normal gün | 952 | -170.3808 | 0.6590 |
| şok günü, şokla AYNI yön | 153 | 111.9911 | 3.1154 |
| şok günü, şoka KARŞI yön | 43 | -20.7437 | 0.3183 |

Rejim grubu (giriş ayı etiketi):

| Rejim | N | ΣR | PF |
|---|---|---|---|
| UP | 494 | -35.9151 | 0.8576 |
| FLAT | 359 | -55.2520 | 0.7185 |
| DOWN | 295 | 12.0337 | 1.0894 |

Sembol:

| Sembol | N | WR % | PF | ΣR |
|---|---|---|---|---|
| BTCUSDT | 161 | 47.2050 | 0.7659 | -19.4149 |
| ETHUSDT | 141 | 49.6454 | 1.0562 | 3.7114 |
| SOLUSDT | 154 | 46.1039 | 0.8497 | -11.9714 |
| XRPUSDT | 148 | 44.5946 | 0.6830 | -25.4981 |
| DOGEUSDT | 140 | 49.2857 | 1.0861 | 5.6590 |
| BNBUSDT | 140 | 47.8571 | 0.8528 | -10.7865 |
| ADAUSDT | 150 | 51.3333 | 1.1835 | 12.3636 |
| LTCUSDT | 114 | 41.2281 | 0.5083 | -33.1964 |

Çıkış nedeni karışımı: REAPER=179, SL=484, TRAIL=485

Kaldıraç payı: 20x=0.0923 3x=0.0192

Kaçan sinyal sayaçları: {"capacity": 139, "daily_latch": 636}; sessiz düşüş / ham sinyal = 0.0000 (0/1923)

Bayraklar: TEK-YÖNLÜ

### DEV G15B

Pencere (giriş çeyreği):

| Pencere | N | ΣR | PF |
|---|---|---|---|
| 2025Q1 | 652 | 35.5325 | 1.1175 |
| 2025Q2 | 542 | -53.9970 | 0.8176 |
| 2025Q3 | 557 | -48.7327 | 0.8351 |
| 2025Q4 | 517 | -129.6952 | 0.5805 |

LONG ve SHORT:

| Yön | N | WR % | PF | ΣR |
|---|---|---|---|---|
| LONG | 1128 | 52.2163 | 0.8604 | -80.5383 |
| SHORT | 1140 | 49.1228 | 0.8142 | -116.3541 |

Şok günü ve normal gün (giriş gününe göre):

| Grup | N | ΣR | PF |
|---|---|---|---|
| şok günü | 339 | 38.4251 | 1.2348 |
| normal gün | 1929 | -235.3175 | 0.7736 |
| şok günü, şokla AYNI yön | 243 | 103.6102 | 2.2302 |
| şok günü, şoka KARŞI yön | 96 | -65.1851 | 0.1792 |

Rejim grubu (giriş ayı etiketi):

| Rejim | N | ΣR | PF |
|---|---|---|---|
| UP | 935 | -122.1314 | 0.7673 |
| FLAT | 727 | -73.2074 | 0.8083 |
| DOWN | 606 | -1.5536 | 0.9948 |

Sembol:

| Sembol | N | WR % | PF | ΣR |
|---|---|---|---|---|
| BTCUSDT | 287 | 46.3415 | 0.6347 | -61.1981 |
| ETHUSDT | 288 | 50.3472 | 0.8726 | -19.5982 |
| SOLUSDT | 325 | 52.9231 | 0.8454 | -25.1493 |
| XRPUSDT | 300 | 52.6667 | 0.9033 | -14.6152 |
| DOGEUSDT | 282 | 53.1915 | 1.0607 | 8.5321 |
| BNBUSDT | 260 | 46.1538 | 0.6974 | -48.4870 |
| ADAUSDT | 277 | 49.8195 | 0.7621 | -34.9344 |
| LTCUSDT | 249 | 53.4137 | 0.9880 | -1.4424 |

Çıkış nedeni karışımı: REAPER=128, SL=1032, TRAIL=1108

Kaldıraç payı: 20x=0.0661 3x=0.0428

Kaçan sinyal sayaçları: {"capacity": 338, "daily_latch": 2527}; sessiz düşüş / ham sinyal = 0.0000 (0/5133)

Bayraklar: TEK-YÖNLÜ

### DEV G30B

Pencere (giriş çeyreği):

| Pencere | N | ΣR | PF |
|---|---|---|---|
| 2025Q1 | 339 | -40.5738 | 0.7744 |
| 2025Q2 | 312 | -81.5959 | 0.5224 |
| 2025Q3 | 374 | -12.7590 | 0.9228 |
| 2025Q4 | 328 | -34.7058 | 0.7915 |

LONG ve SHORT:

| Yön | N | WR % | PF | ΣR |
|---|---|---|---|---|
| LONG | 623 | 48.3146 | 0.7830 | -66.0072 |
| SHORT | 730 | 43.6986 | 0.7261 | -103.6274 |

Şok günü ve normal gün (giriş gününe göre):

| Grup | N | ΣR | PF |
|---|---|---|---|
| şok günü | 163 | 44.4722 | 1.6401 |
| normal gün | 1190 | -214.1067 | 0.6508 |
| şok günü, şokla AYNI yön | 113 | 77.5025 | 3.6235 |
| şok günü, şoka KARŞI yön | 50 | -33.0303 | 0.1729 |

Rejim grubu (giriş ayı etiketi):

| Rejim | N | ΣR | PF |
|---|---|---|---|
| UP | 580 | -81.1018 | 0.7221 |
| FLAT | 403 | -84.5274 | 0.6138 |
| DOWN | 370 | -4.0052 | 0.9767 |

Sembol:

| Sembol | N | WR % | PF | ΣR |
|---|---|---|---|---|
| BTCUSDT | 182 | 36.8132 | 0.5367 | -46.7419 |
| ETHUSDT | 174 | 46.5517 | 0.6835 | -26.7360 |
| SOLUSDT | 175 | 50.8571 | 0.8529 | -11.9320 |
| XRPUSDT | 181 | 51.3812 | 1.1221 | 10.0939 |
| DOGEUSDT | 161 | 44.0994 | 0.7564 | -19.7434 |
| BNBUSDT | 150 | 42.0000 | 0.7001 | -25.3127 |
| ADAUSDT | 176 | 50.0000 | 0.8264 | -14.4254 |
| LTCUSDT | 154 | 44.1558 | 0.5893 | -34.8370 |

Çıkış nedeni karışımı: REAPER=263, SL=571, TRAIL=519

Kaldıraç payı: 20x=0.0746 3x=0.0214

Kaçan sinyal sayaçları: {"capacity": 127, "daily_latch": 902}; sessiz düşüş / ham sinyal = 0.0000 (0/2382)

Bayraklar: TEK-YÖNLÜ

## Stres koşuları

| Set kol | Kaplama | N | PF | ΣR |
|---|---|---|---|---|

## Teşhis koşuları (karar vermez)

| Kol | Koşu | N | PF | ΣR | Okuma |
|---|---|---|---|---|---|
| K15R | DIAG_Z0 | 1743 | 0.9872 | -10.7542 | maliyetten önce de kenar yok |
| K15R | DIAG_MAKER | 1497 | 0.7705 | -188.1003 |  |
| K15R | DIAG_T | 1882 | 0.7401 | -86.3136 |  |
| K15R | DIAG_NOLATCH | 2580 | 0.7722 | -326.7795 |  |
| K30R | DIAG_Z0 | 1089 | 1.1452 | 68.7202 | ham kenar var, maliyet yiyor |
| K30R | DIAG_MAKER | 1054 | 0.9299 | -35.9281 |  |
| K30R | DIAG_T | 1392 | 0.8336 | -40.3682 |  |
| K30R | DIAG_NOLATCH | 1480 | 0.9306 | -49.7260 |  |
| K15B | DIAG_Z0 | 2109 | 0.9609 | -41.1483 | maliyetten önce de kenar yok |
| K15B | DIAG_MAKER | 1793 | 0.7087 | -297.9925 |  |
| K15B | DIAG_T | 2458 | 0.7638 | -96.6659 |  |
| K15B | DIAG_NOLATCH | 3623 | 0.7484 | -513.6970 |  |
| K30B | DIAG_Z0 | 1186 | 1.0986 | 52.1987 | ham kenar var, maliyet yiyor |
| K30B | DIAG_MAKER | 1127 | 0.8651 | -76.2263 |  |
| K30B | DIAG_T | 1512 | 0.7399 | -68.1560 |  |
| K30B | DIAG_NOLATCH | 1705 | 0.8488 | -129.1055 |  |
| G15B | DIAG_Z0 | 2704 | 1.1302 | 161.2977 | ham kenar var, maliyet yiyor |
| G15B | DIAG_MAKER | 2380 | 0.9012 | -122.4247 |  |
| G15B | DIAG_T | 3166 | 0.6608 | -189.6618 |  |
| G15B | DIAG_NOLATCH | 4465 | 0.7254 | -687.1050 |  |
| G30B | DIAG_Z0 | 1358 | 0.9476 | -31.5041 | maliyetten önce de kenar yok |
| G30B | DIAG_MAKER | 1339 | 0.7991 | -130.5942 |  |
| G30B | DIAG_T | 1724 | 0.6690 | -107.4060 |  |
| G30B | DIAG_NOLATCH | 2181 | 0.7906 | -227.2741 |  |

## Sınırlar

1. Önsel olasılık düşük: bu projede trend yönlü girişe dair eldeki ölçümler olumsuz ya da çelişkili; "kazanan yok" çıkması muhtemeldir.
2. Backtest canlıdan iyimserdir (TP1'e ulaşma %66, canlı %54). Kazanan çıksa bile yalnız "testnet ölçüm adayı"dır.

