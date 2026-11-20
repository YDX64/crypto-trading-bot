# ÖN-KAYITLI DENEY PLANI: kayıp-kazanç yapısı (stop / TP / kaldıraç)

Plan tarihi 2026-10-10. Kod tabanı wt-research @1f3da26.

Aşağıdaki her şey **hiçbir doğrulama/son-sınav sonucuna bakılmadan** sabitlenmiştir. Çalıştırıcı bu metnin sha256'sını `prereg/PLAN.sha256` olarak kaydeder. Pencere, varyant, eşik veya karar kuralını sonradan değiştirmek yasaktır. Hata düzeltmesi (bug) serbesttir, parametre değişikliği değildir.

Kısaltmalar: `SP` = `/tmp/claude-0/-home-user-crypto-trading-bot/b53b3a36-e126-5de6-b27f-ca558d8408f8/scratchpad`. `WT` = `$SP/wt-research`.

## 0. Teşhis düzeltmesi (varyant tasarımının dayanağı)

Bunlar kodda doğrulandı. Her biri varyantların neden bu biçimde kurulduğunu açıklar.

- **ATR 1m'dir, 5m değil.** `ctx.candles_5m` aslında giriş TF'sidir ve sunucuda `SCALPER_TF_ENTRY=1m`. Baseline logu da "1m=30390 5m=6148 15m=2266" diyor.
- **Majörlerde kaldıraç 20x'e kelepçelenir.** Formül `raw = roi_pct/(mult·atr_pct)` (setups.py:116-117, kod okundu), sonra [3,20]'ye kırpılır.
  - Majörlerde 1m ATR% ≈ 0,05 olduğundan raw yüzlerce çıkar.
  - Baseline'ın gerçek yapısı: stop ≈ %2,5 fiyat (3 ATR değil), TP1 ≈ %0,5 (0,2R), TP2 ≈ %1,25 (0,5R).
  - Probe (4 majör, 1 hafta, 23 sinyal) hepsinde lev=20 gösterdi. Bu küçük örneklem, strateji kanıtı değildir.
- **Boyutlama.** Boyutlama kodunda `qty = risk/stop_mesafesi`, sonra `nominal ≤ bakiye·lev·MAX_MARGIN_PCT` kapısı vardır (backtest.py:735-870, `_DEFAULT_VIRTUAL_BALANCE=10_000` @214, bakiye sabit).
  - Marj kapısı (%10) her varyantta bağlayıcıdır. Stop daraldıkça işlem başına dolar riski düşer (baseline %5, S=20 için %2).
  - **Sonuç:** dolar PnL/DD varyantlar arasında risk-eşit DEĞİLDİR. Karar PF, ΣPnL/ΣbarDD (Calmar) ve oranlarla verilir.
- **ROI cinsinden TP, kaldıraçla ölçeklenir.** `price_at_roi` mesafesi = ROI/lev. Stop da ROI/lev olduğundan **R oranı TP_ROI/STOP_ROI'dir, kaldıraçtan bağımsız**.
  - `S/M = 1` iken kaldıraç = `clamp(1/ATR%, 3, 20)`. Stop = `M × max(ATR%, 0,05%)`. TP1 = `TP1_ROI × max(ATR%, 0,05%)`.
  - Yani bu varyantlarda stop ve hedefler gerçekten "ATR katı"dır ve kaldıraç oynaklığa göre değişir. Kullanıcının "kaldıraç oynaklığa göre" hedefi buradan gelir.
- **Config doğrulayıcısı** (config.py:~1203-1252) mesafeyi `FIXED_STOP_ROI/SCALPER_LEVERAGE(=10)` ile [0,15; 5,5] bandında arar. Bu yüzden S ≤ 55. Aşağıdaki tüm varyantlar geçer: 1,0 / 2,0 / 3,0 / 2,0 / 2,0 / 5,0 / 1,0 / 2,0.
- **Harness kapı retlerini saymaz.** Stop-bandı retleri sessizce `None` döner (backtest.py:763-765). İşlem sayısı düşüşü tek göstergedir (kural G6).
- **Geçmiş kanıt.** Bugüne dek hiçbir çıkış değişikliği holdout'ta ayakta kalmadı: D28 TP8 vetosu, D30 STALE_TP vetosu, D29 U50 reddi, E3a/E6d/E6e hiç OOS'a girmedi. Önsel olasılık "kazanan yok" yönündedir.

## 1. PENCERELER

### (a) Geliştirme: yalnız fikir elemesi, karar vermez

| Ad | Aralık (UTC, bitiş hariç) | Taban sonucu |
|---|---|---|
| AYI | 2026-01-23 → 2026-02-13 | 149 işlem, WR %87,2, PF 2,03 |
| YATAY | 2026-07-01 → 2026-07-21 | 136 işlem, WR %72,8, PF 1,50 |
| BOĞA | 2026-08-07 → 2026-08-21 | 101 işlem, WR %70,3, PF 1,33 |

Önbellek hazır. Baseline'ın tuning'i bu pencerelerde yapıldığı için buradaki PF'ler baseline lehine şişiktir.

### (b) DOĞRULAMA: 8 pencere, 21 gün, çakışmasız, hepsi 2025

**Seçim kuralı:**
- 2025 takvim yılı.
- Pazartesi başlangıç, tam 21 gün.
- Klasik 2026 pencereleriyle ve birbiriyle test dönemi çakışmaz.
- Rejim çeşitliliği: 3 düşüş, 2 yükseliş, 3 yatay (şok içerenler dahil).

**"Hiç kullanılmamış" kanıtı:** `grep` ile docs/EXPERIMENTS.md, DECISIONS.md, RUNBOOK.md ve scripts/ içinde tek 2025 anışı `klines_bear/ 2025-12-15→2026-02-15` test fixture'ıdır. Ben 2025-12 / 2026-01 öncesini bu yüzden seçmedim. 2026'nın "taze" pencereleri (Şubat, Mart, Mayıs, Haziran, Temmuz-Ağustos) başka deneylerde (D28, D30, E13/E14) kullanıldığından dışarıda bırakıldı.

| Kod | Aralık | Beklenen rejim (yalnız BTC fiyat hareketi, bellek; yaklaşık) | Neden seçildi |
|---|---|---|---|
| V1 | 2025-01-06 → 2025-01-27 | ~98k → 109k zirve → ~102k. FLAT, ani yukarı iğne ve geri dönüş | Yüksek oynaklıkta yatay |
| V2 | 2025-02-17 → 2025-03-10 | ~96k → ~80k. DOWN, sert | Ayı/çöküş |
| V3 | 2025-04-14 → 2025-05-05 | ~84k → ~94k. UP, toparlanma | Boğa/toparlanma |
| V4 | 2025-05-12 → 2025-06-02 | ~104k → 111k → ~105k. FLAT, zirvede | Yüksek seviyede yatay |
| V5 | 2025-07-07 → 2025-07-28 | ~108k → ~118k. UP, düşük oynaklık | Sakin trend |
| V6 | 2025-08-18 → 2025-09-08 | ~116k → ~108k → ~111k. FLAT/hafif aşağı | Testere/yatay |
| V7 | 2025-10-06 → 2025-10-27 | ~124k → 10 Eki çöküşü → ~114k. DOWN + şok | Likidasyon şoku (stres) |
| V8 | 2025-11-03 → 2025-11-24 | ~106k → ~82k. DOWN, süregelen | Uzun düşüş |

**Adım 0 (performansa bakmadan):** BTC 1d kapanışlarından etiket DONDURULUR. Kural: pencere getirisi ≥ +%5 ise UP, ≤ −%5 ise DOWN, aksi FLAT. Ayrıca herhangi bir günlük |getiri| ≥ %6 ise ŞOK bayrağı konur. Etiketler yukarıdaki tahminden farklı çıkarsa **pencere tarihleri DEĞİŞMEZ**, yalnız rejim tabloları donmuş etikete göre gruplanır.

**Arşiv doğrulama (bu oturumda yapıldı, 2026-10-10):**
- Her pencere için başlangıçtan 8 gün öncesi → bitiş−1 aralığında, 8 sembolün 1m günlük zip'i ve BTCUSDT 1d zip'i için HEAD isteği atıldı.
- 2070/2070 istek HTTP 200 döndü.
- 5m/15m ve alt-sembol 1d için bu kontrol yapılmadı. Bunların gerçek kontrolü doldurucudur: boşluk, sha256 veya mum sayısı sorununda hata verir ve önbelleğe hiçbir şey yazmaz.
- Çalıştırıcı yine de Faz 0'da `fill_win` çıkış kodunu denetler. Boşluk çıkan pencere yerine konmaz, rapora "PENCERE EKSİK" yazılır ve kalan pencerelerle devam edilir. Kural G1'de 8 yerine kullanılabilir pencere sayısı üzerinden orantılanır (6/8 → 3/4). 6'nın altına düşerse deney geçersizdir.

### (c) SON SINAV: tek atış, en sonda
- **CANLI: 2026-09-03 → 2026-10-08.** Önbellek hazır (`…1788384600000_1791417600000`). Taban: 320 işlem, WR %72,5, PF 0,90; canlı testnet 124 işlem, PF 0,47.
- En fazla BİR varyantla (+ baseline karşılaştırması) bir kez koşulur. Sonuç ne olursa olsun yeniden ayar yapılmaz.

## 2. VARYANTLAR (toplam aile m = 11; hepsi baştan sabit)

Taban B0 = `server-scalper-env.txt` aynen. Aşağıdaki "override" listeleri o dosyanın üzerine yazılır. Belirtilmeyen her şey tabandır (SCALPER_DYNAMIC_LEVERAGE=true, DYN_LEV_MIN=3, MAX=20, STOP_MODE=fixed_roi, SCALPER_LEVERAGE=10, MIN_RR=0, MAX_HOLD_HOURS=8, CHANDELIER 3,5 vb.). Aşağıda `FIXED_STOP_ROI_PCT` kısaca S, `DYN_LEV_STOP_ATR_MULT` kısaca M ile yazılmıştır.

### Kod gerektirmeyen (8)

| Kod | Override | Yorum (calm majör için) |
|---|---|---|
| E1 | S=10, M=10, TP1_ROI=10, TP2_ROI=25 | stop ≈ 10 ATR ≈ %0,5; TP1 = 1R, TP2 = 2,5R. Dar stop, 1R hedef |
| E2 | S=20, M=20, TP1_ROI=10, TP2_ROI=25 | stop ≈ %1; TP fiyatları baseline ile aynı (%0,5 / %1,25); TP1 = 0,5R |
| E3 | S=30, M=30, TP1_ROI=10, TP2_ROI=25 | stop ≈ %1,5; aynı TP fiyatları |
| E4 | S=20, M=20, TP1_ROI=20, TP2_ROI=40, TP1_FRACTION=0.30, TP2_FRACTION=0.30 | stop %1; TP1 = 1R, TP2 = 2R; runner %40 |
| E5 | S=20, M=20, TP1_ROI=30, TP2_ROI=60, TP1_FRACTION=0.30, TP2_FRACTION=0.30 | TP1 = 1,5R, TP2 = 3R. Büyük hedef |
| E6 | S=50, M=50, TP1_ROI=10, TP2_ROI=25 | calm majörde baseline ile özdeş (%2,5). Oynak coin'de kaldıraç düşer, stop genişler (maks %5; >%5,5 olursa harness sinyali sessizce atar). **Saf "kaldıraç oynaklığa göre" testi** |
| E7 | S=20, SCALPER_DYNAMIC_LEVERAGE=false, SCALPER_LEVERAGE=20, TP1_ROI=10, TP2_ROI=25 | **Kontrol:** sabit kaldıraç 20 ve tüm coinlerde sabit %1 stop. E2 ile fark = oynaklık ölçekli kaldıraç etkisi |
| E8 | E2 + TP1_FRACTION=0.60, TP2_FRACTION=0.20 | kâr erken kilitlenir, runner %20. TRAIL bağımlılığı testi |

Beklenen kaldıraç davranışı (smoke testinde doğrulanacak): S/M=1 için lev ≈ 1/ATR%, yani BTC ~20, DOGE ~10.

Bu aileye alınmayanlar (gerekçeleriyle):
- Chandelier ve TP2-ROI: daha önce denenmiş (D11, E4d/e).
- `DYN_LEV_MAX` düşürme: D13'te reddedildi.
- REAPER süresi: D30'da denendi.

### Kod gerektiren (3). Paylaşılan fonksiyon taslağı + parite testi

**Ortak altyapı (üçü için tek sefer, `types.py`'de bağımlılıksız):**

```python
@dataclass(frozen=True)
class TpPlan: tp1_price; tp2_price; tp1_fraction; tp2_fraction; source  # "roi"|"r"|"bb"|"strength"
def plan_take_profits(*, entry, stop, direction, leverage, cfg, signal) -> TpPlan
```

- Varsayılan mod `SCALPER_TP_PLAN_MODE=roi` bugünkü `price_at_roi` sonuçlarını aynen verir (golden test bayt-bayt: 2 işlem, PnL 23,79, `{TRAIL:2}`).
- `grep scalper_tp1_roi|scalper_tp2_roi` ile bulunan, yeni fonksiyona bağlanması/denetlenmesi gereken yerler:
  - **Fiyat hesabı (TP fiyatları plan'dan gelmeli):** executor.py:1671-1672, backtest.py:844-845, exits.py:2771-2772 (restart kurtarma `_recover_one`), engine.py:2932 (karşı-olgu planı).
  - **MIN_RR kopyaları (üç ayrı):** executor.py:1080-1082, backtest.py:775-777, config.py:~1240.
  - **Başka tüketiciler (anlamını denetle):** executor.py:1755 ve exits.py:2850 (`trailing_profit_distance=scalper_tp1_roi`), engine.py:301 (`tp1_roi_pct` yedek politika), forensics.py:121 ve 463.
  - Okuyucu "5 çağrı sitesi" demişti. Grep ile en az 12 kullanım çıktı. Her biri ya plan'a bağlanır ya da "mod=roi'de değişmez" gerekçesiyle belgelenir.
- Plan kalıcılaştırılır: `scalp_trades.forensics` JSON'una `document["tp_plan"]` yazılır (migration yok). `_recover_one` varsa onu okur, yoksa cfg'ye düşer. Aksi halde restart'ta R/BB/güç tabanlı TP'ler sessizce yanlışlaşır.
- Pending-journal'a yeni sinyal alanları da yazılır (`_serialize_signal`/`_deserialize_signal`, executor.py:~441-470). Aynı yerde bilinen `leverage` yazılmama açığı kapatılır.
- Çıkış durum makinesi (TP1→BE, TP2→taban, chandelier) iki ayrı uygulamadır: harness `_process_candle_exits`/`_update_trailing` (backtest.py:~914,1008,1136), canlı `exits._check_tp1/_check_tp2/_update_trailing` (exits.py:~503,573,943). Bu tasarımlar durum makinesini değiştirmez, yalnız fiyatları verir. Kodun fiyatı `pos.tp1_price` üzerinden okuduğu **doğrulanamadı** (yalnız oluşturma noktaları okundu). Uygulayıcı bunu ilk iş doğrular.
- **Parite testi** (kalıp: tests/test_entry_gates.py:~1250, tests/test_market_gate.py:~863): rastgele sinyal × her mod için `executor` TP listesi == `backtest.open_position` TP listesi == `exits._recover_one` TP listesi. Ek olarak mod=roi için golden ve mevcut `test_scalper_*` testleri değişmeden geçer (2543 test).

**C1 — Dar yapısal stop + büyük hedef** (`SCALPER_STOP_MODE=swing_atr`)
- `apply_stop_policy`'ye (setups.py:~87) yeni dal. Hem engine (engine.py:~2334) hem harness (backtest.py:~1431) onu çağırdığı için stop/kaldıraç paritesi otomatik gelir.
- StrategyC (setups.py:501/534) sinyale `swing_extreme` alanını taşır (40 mumun dibi/tepesi). Stop = `uç ∓ 0,5×ATR`.
- Mesafe d: alt sınır `max(%0,30; 2×ATR%)`, üst sınır %1,5. Aşarsa sinyal reddedilir (sayaç).
- Gerekçe: yapısal modun ham medyan stopu %0,126'dır (fee ≈ 0,55R); D20a'ya göre stop ≥ ~%0,20 gerekir.
- Kaldıraç: `lev = clamp(floor(20 / d%), 3, 20)`, yani yapı genişledikçe kaldıraç düşer.
- Hedef: `TP_PLAN_MODE=r`, `SCALPER_TP1_R=1.0`, `SCALPER_TP2_R=2.0`, fraksiyonlar 0,40/0,30, runner 0,30 (chandelier 3,5 ATR, BE tamponu aynı). TP1 mesafesi < %0,25 ise sinyal atlanır (ücret tabanı).
- TV tohumlu dış sinyaller (engine.py:129-173, tohum stop = 1×ATR) uç taşımaz: bu modda `fixed_roi` yoluna düşer (belgelenir).
- Parite: aynı `StrategyContext` için engine ve `simulate_symbol` aynı `(stop, leverage, tp)` verir.
- C1'in yaklaşık E2'ye karşılığı: ikisi de ≈ %1 stoplu; fark yapısal stop ve R-hedefleri.

**C2 — BB orta / karşı bant hedefi** (`TP_PLAN_MODE=bb`)
- StrategyC şu an orta bandı atıyor (setups.py:~481). Sinyale `bb_levels=(mid, karşı_bant)` SİNYAL ANINDA DONDURULUR. Canlıda TP sabit fiyatlı reduceOnly emir olduğundan dondurulmuş seviye canlı ve sim için aynıdır.
- TP1 = orta bant (%50), TP2 = karşı bant (%25), runner %25. Stop = E2 ile aynı (S=20, M=20), böylece **C2 vs E2 yalnız TP kuralını izole eder**.
- Dolum fiyatı seviyeyi geçmişse, ya da seviye mesafesi < %0,25 ise o bacak ROI yedeğine düşer (E2'nin TP1/TP2'si). Trade kaydına `tp_source` yazılır.
- **Ön-kayıtlı kapsam şartı:** doğrulamada işlemlerin <%40'ı `bb` kaynaklıysa C2 "yetersiz kapsam" ilan edilir, p=1,0 girilir. 1m'de orta banda mesafe çoğu zaman <%0,25 olabilir, bu gerçek bir risktir.

**C3 — Sinyal gücüne göre TP** (`TP_PLAN_MODE=strength`)
- Sinyalde zaten `score` var (types.py:93; setups.py:516/549) ve hiçbir yerde tüketilmiyor (yalnız executor.py:451 serileştirme). Güç = bu skor, tercile'a bölünmüş.
- **Önkoşul M0 (yalnız GELİŞTİRME pencerelerinde):** `BacktestTrade`'e `score` alanı eklenir (`to_dict` tek satır; golden testin anahtar karşılaştırıp karşılaştırmadığı doğrulanamadı, gerekirse güncellenir). Dev'deki tüm işlemler için Spearman ρ(score, MFE_R) hesaplanır; MFE_R = mfe_pct / stop ROI. **Şart: ρ ≥ 0,10 ve tek-yanlı p < 0,05, tercile ortalama MFE_R monoton artan.** Sağlanmazsa C3 KODLANMAZ ve p=1,0 girilir.
- Tercile kesim noktaları dev pencerelerinden hesaplanır ve DONDURULUR.
- Eşleme: zayıf ×0,7, orta ×1,0, güçlü ×1,5; hem TP1_ROI hem TP2_ROI'ye çarpan. Stop = E2. Boyut/kaldıraç gücü kullanmaz (marj tavanı bağlayıcı olduğu için yukarı ölçekleme için tavanın yükseltilmesi gerekirdi; bu P2'ye göre ayrı risk, kapsam dışı).
- Dolayısıyla C3 vs E2 yalnız güç ölçeklemesini izole eder.

**Kod varyantlarının parametreleri yukarıda sabittir, doğrulama başladıktan sonra değişmez.** Kod dallı çalışır (`wt-exp` içinde `exp/c1-swing`, `exp/c2-bb`, `exp/c3-strength`), varsayılan kapalı. İmplementasyon CLAUDE.md'ye uygun: sonnet / effort high, `python3 -m pytest tests -q` yeşil, golden bayt-bayt aynı.

## 3. KARAR KURALI (sayısal, önceden)

**Ölçüler** (her varyant, 8 doğrulama penceresi, `overall`/`trades[]` alanlarından):
- Pencere PF'si JSON `overall.profit_factor` (inf ise 5'e kırpılır).
- **Havuz PF'si** = Σ(pozitif pnl) / |Σ(negatif pnl)| tüm pencerelerin tüm işlemlerinden (PF'lerin ortalaması değil).
- Medyan PF = 8 pencere PF'sinin medyanı.
- `bar_max_drawdown` JSON `overall.bar_max_drawdown`.
- Calmar = ΣPnL / Σ(8 pencerenin bar_max_drawdown'u).

**Elemeler ve kapılar (hepsi VE):**

| Kapı | Kural |
|---|---|
| **DEV elemesi** | Havuz dev PF < 1,0 VEYA dev işlem sayısı < B0 dev işleminin %50'si (386'nın 193'ü) → elenir (doğrulamaya girmez; yine de aile m'de sayılır, p=1,0) |
| G1 | PF>1 olan pencere sayısı ≥ 6/8 (%75) |
| G2 | Havuz PF ≥ 1,15 |
| G3 | Medyan PF ≥ 1,10 |
| G4 | En kötü pencere PF ≥ 0,70 |
| G5 | Her pencerede `bar_max_drawdown` ≤ 2000 (bakiyenin %20'si); VE Calmar > 0 ve Calmar > B0'ınki |
| G6 | Toplam işlem ≥ 400 VE ≥ B0 işlem sayısının %60'ı (stop-bandı retleri/sessiz kapıları yakalamak için) |
| G7 | Rejim grupları (donmuş UP/DOWN/FLAT etiketi): her grubun havuz PF'si ≥ 0,90 |
| G8 | B0'a göre iyileşme: havuz PF ≥ B0 havuz PF + 0,05 |
| G9 (dayanıklılık, yalnız G1-G8'i geçenlere) | `--fee-stress` (komisyon+kayma 2×) havuz PF ≥ 1,05 VE `--entry-delay-candles 1` havuz PF ≥ 1,05 |
| G10 (çoklu test) | Aşağıdaki BH testinde `rejected=True` (q ≤ 0,10) |

**p-değeri tanımı (G10).**
- Her varyant için 8 pencerenin işlemleri çıkış gününe (UTC, `exit_time`) göre günlük PnL toplamına dönüştürülür; 168 gün, işlemsiz günler 0.
- H0: ortalama günlük PnL ≤ 0 (mutlak kenar yok).
- Gün-bloklu bootstrap: 20000 yeniden örnekleme, tohum 20261010, `p = (#{ortalama* ≤ 0}+1)/(20000+1)`.
- Elenen, kodlanmayan veya "yetersiz kapsam" varyantlar için p = 1,0.
- Harness'in kendi `--permutations` p-değeri (koşullu null, rejim arka planı gerçek kalır; permutation.py başlığı) KARARDA KULLANILMAZ; isteğe bağlı bilgi.
- **BH komutu (CLI doğrulandı, multitest.py:215-262):**
  ```bash
  python3 -m src.strategies.scalper.multitest --json $SP/prereg/pvals.json --alpha 0.10 --effective-tests 6 --format json
  ```
  - `pvals.json`: `[{"name":"E1","p_value":0.012}, …]` (11 satır).
  - Karar m=11 sütunundaki `rejected`/`q_value` ile verilir. `--effective-tests 6` yalnız duyarlılık sütunudur ve kararı DEĞİŞTİRMEZ (CLI'ın kendi belgesi de öyle diyor).
  - Not: BH bağımsızlık/PRDS varsayar; varyantlar korelasyonludur, bu yüzden q hâlâ iyimserdir.

**Seçim.**
- Tüm kapıları geçen varyantlar arasından en yüksek Calmar kazanır. Eşitlikte az parametre.
- Hiçbiri geçmezse sonuç aynen şudur: **"KAZANAN YOK — kayıp-kazanç yapısı değişikliği bu veri üzerinde kanıtlanamadı; üretim ayarı değişmez."** Bu bir başarısız deney değil, geçerli bir sonuçtur.
- B0 kendisi G1-G7'yi geçerse "taban doğrulamada ayakta" diye ayrıca raporlanır.

**SON SINAV (yalnız seçilen tek varyant, 2026-09-03→2026-10-08, bir kez):** geçmek için şunların hepsi gerekir.
- PF ≥ 1,05.
- ΣPnL > 0.
- İşlem ≥ 100.
- `bar_max_drawdown` ≤ 2500 (%25).
- Havuz PF > B0'ın aynı penceredeki 0,90'ından en az 0,10 yüksek.

Geçerse sonraki adım testnet ≥ 5 gün (CLAUDE.md Yasaklar #1), mainnet DEĞİL; `docs/DECISIONS.md` yeni D-kaydı ve `.env` yedeği ana oturumda yapılır. Kalırsa "kazanan yok", yeniden ayar yok.

## 4. YÜRÜTME

**Hazırlık** (wt-research'e yazmamak için yerel klon; backtest `logs/` dizinini cwd'ye yazar):

```bash
SP=/tmp/claude-0/-home-user-crypto-trading-bot/b53b3a36-e126-5de6-b27f-ca558d8408f8/scratchpad
ENVF=$SP/server-scalper-env.txt; CACHE=/home/user/crypto-trading-bot/data/klines_cache
SYMS=BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,BNBUSDT,ADAUSDT,LTCUSDT
git clone -q $SP/wt-research $SP/wt-exp && git -C $SP/wt-exp checkout -q 1f3da26
mkdir -p $SP/prereg/runs $SP/prereg/logs
sha256sum <plan-metni> > $SP/prereg/PLAN.sha256

fill_win () {   # $1=start $2=end   (SCALPER_ env backtest ile AYNI olmalı; yoksa farklı önbellek anahtarı → cache-miss/451)
  ( cd $SP/wt-exp && env $(grep -E '^SCALPER_' $ENVF | xargs) python3 scripts/fill_klines_cache_from_vision.py \
      --symbols $SYMS --start $1 --end $2 --cache-dir $CACHE --download-cache $SP/prereg/vision_cache \
  ) > $SP/prereg/logs/fill_$1.log 2>&1 || echo "FILL HATA $1"
}

run_bt () {     # run_bt <etiket> <start> <end> [KEY=VAL ...] [-- ek backtest bayrakları]
  local label=$1 s=$2 e=$3; shift 3; local ov=() extra=(); local base; base=$(grep -E '^SCALPER_' $ENVF)
  while [ $# -gt 0 ]; do [ "$1" = "--" ] && { shift; extra=("$@"); break; }; ov+=("$1"); shift; done
  for kv in "${ov[@]}"; do base=$(printf '%s\n' "$base" | grep -v "^${kv%%=*}="); done
  ( cd $SP/wt-exp && env $(printf '%s\n' "$base" | xargs) "${ov[@]}" python3 -m src.strategies.scalper.backtest \
      --strategies C --symbols $SYMS --start $s --end $e --cache-dir $CACHE "${extra[@]}" ) > $SP/prereg/logs/$label.log 2>&1
  local f; f=$(grep -oE 'logs/backtest_[0-9_]+\.json' $SP/prereg/logs/$label.log | tail -1)
  [ -n "$f" ] && cp $SP/wt-exp/$f $SP/prereg/runs/$label.json || echo "BT HATA $label"
}
```

- **Sıralı çalıştırın, paralel koşmayın.** Rapor dosya adı saniye çözünürlüklüdür (`backtest_YYYYMMDD_HHMMSS.json`, backtest.py:2018), eşzamanlı iki koşu çakışabilir. Cache-miss'te hem 429 hem 451 riski de var.
- Örnek çağrılar:
  ```bash
  run_bt E2_V1 2025-01-06 2025-01-27 SCALPER_FIXED_STOP_ROI_PCT=20 SCALPER_DYN_LEV_STOP_ATR_MULT=20 SCALPER_TP1_ROI=10 SCALPER_TP2_ROI=25
  run_bt E2_V1_fee 2025-01-06 2025-01-27 <aynı override> -- --fee-stress
  run_bt E2_V1_dly 2025-01-06 2025-01-27 <aynı override> -- --entry-delay-candles 1
  ```
- Python yorumlayıcısı: baseline loglar `python3` ile üretildi. Farklıysa `.venv` (Python 3.12) kullanılır; hangisinin kullanıldığı `prereg/ENV.txt`'e yazılır.

**Faz sırası ve tahmini süre** (taban: doldurma ≈ 7-8 dk/pencere, backtest ≈ 70 sn):

| Faz | İş | Süre |
|---|---|---|
| F0 | `PLAN.sha256`; BTC 1d'den rejim etiketini dondur; 8 pencere doldur (`fill_win`); **duman testi:** B0 dev koşusunu baseline loglarıyla eşle (149/87,2/2,03 vb. belirleyici olmalı); E1-E8'in her biri 1 hafta (2026-08-07→08-14) koş, config ValueError olmadığını ve trade JSON `leverage` dağılımının beklenen (majör ~20, oynak coin <20) olduğunu doğrula | ~75 dk |
| F1 | DEV: B0 + E1-E8 × 3 pencere = 27 koşu; dev eleme kuralı | ~35 dk |
| F1b (paralel geliştirme) | C1, C2, C3 kodlama + parite testleri + M0; dev koşuları ve elemesi | kod süresi (bağımsız) |
| F2 | DOĞRULAMA: B0 + hayatta kalan en çok 11 varyant × 8 pencere ≤ 96 koşu | ~110 dk |
| F3 | G1-G8'i geçenler için fee-stress ve gecikme-1: ≤ 3 varyant × 2 × 8 = 48 koşu | ~55 dk |
| F4 | p-değerleri → `pvals.json` → BH CLI → karar tablosu | dakikalar |
| F5 | SON SINAV (seçilen tek varyant + B0) | ~3 dk |

Yaklaşık toplam: 4,5-5 saat duvar saati (kod geliştirme hariç).

**Çıktılar** (hepsi `$SP/prereg/` altında, repo dışı, wt-research'e DOKUNULMAZ):
- `runs/<etiket>.json`: ham backtest JSON'u (etiket = `<varyant>_<pencere>[_fee|_dly]`).
- `logs/<etiket>.log` ve `logs/fill_*.log`.
- `windows_frozen.json`: pencereler + dondurulmuş rejim etiketleri.
- `results.csv`: varyant × pencere (işlem, WR, ΣPnL, PF, bar_maxDD).
- `pvals.json`, `bh_out.json`, `DECISION.md`: tek sayfa karar tablosu, G1-G10 doğru/yanlış satır satır.
- `analyze_prereg.py`: JSON'lardan metrikleri, havuz PF'sini, medyanı, Calmar'ı, rejim kırılımını ve bootstrap p-değerini hesaplayan betik. Bu betik planın parçasıdır; ilk F2 koşusundan önce yazılıp donmalıdır.
- Kazanan çıkarsa `docs/EXPERIMENTS.md` ve `docs/DECISIONS.md` kayıtlarını ana oturum yazar (bu plan dosya yazmaz).

## 5. BİLİNEN TUZAKLAR (harness'in modelleyemedikleri ve yanlış okunabilecekler)

- **Sim ile canlı arasındaki boşluk büyük.** Aynı dönemde (2026-09-03→10-08) sim PF 0,90, canlı testnet PF 0,47. Sim'de PF 1,15 çıkan bir varyant canlıda 1'in altında kalabilir; bu yüzden son kapı "testnet ≥ 5 gün".
- **Modellenmeyenler:**
  - Testnet dolumu, maker gecikmesi ve kısmi dolum (harness idealize, slipaj yok, TP/SL tam fiyatta).
  - Günlük kayıp kesici `SCALPER_DAILY_LOSS_LIMIT_PCT=1`.
  - Funding.
  - TV sinyalleri ve TV çıkışları (backtest.py:~1189-1193).
- **Aynı mumda SL ve TP çakışması.** Harness SL'i önce sayar (pessimistic). Dar stoplarda (%0,5) bu çakışma sıklaşır ve dar varyantlar sistematik cezalanır. Öte yandan testnet'te ince emir defteri ve fitiller dar stoplara daha çok zarar verir, yani gerçek etki belirsiz yönlü.
- **Sessiz sinyal kaybı.** Stop-bandı retleri sayılmaz (E3 ve E6'da oynak coinlerde stop >%5,5 olabilir). Çözüm: G6 işlem sayısı kapısı, ayrıca duman testinde stop mesafesi dağılımı kontrol edilir.
- **Risk-eşit değil.** Marj tavanı nedeniyle dolar DD/PnL varyantlar arası kıyaslanamaz. Karar PF, Calmar ve oranlara dayanır.
- **Uç etkisi.** Pencere sonunda açık pozisyonlar `EOD` ile kapatılır; kısa pencerede küçük negatif yanlılık.
- **Pending-journal `leverage` açığı** (executor.py:~441-470): restart sonrası geri yüklenen maker emrinde TP hesabı yanlış kaldıraçla yapılabilir. Canlı etkisi ölçülmedi, harness'i etkilemez.
- **Çoklu test:** aile m = 11. Yine de aynı 8 sembol ve komşu parametreler korelasyonlu; BH q değerleri iyimser kalır.
- **Önbellek/anahtar:** TF veya lider kapısı (`SCALPER_TF_*`, `SCALPER_MARKET_GATE*`) env'i değiştirilirse farklı önbellek dosyası gerekir ve cache-miss 451 verir (RUNBOOK:~420-423). Hiçbir varyant bunları değiştirmez.
- **Doğrulanamadıklar:** majör sembollerin gerçek 1m ATR% dağılımı (yalnız probe ve fixture'dan), `exits._check_tp1`'in TP fiyatını yeniden hesaplayıp hesaplamadığı (yukarıda uyarıldı), golden testin JSON anahtar karşılaştırması, yeni `SCALPER_STOP_MODE=swing_atr` değerinin başka bir doğrulayıcıda reddedilip reddedilmeyeceği, 5m/15m ve alt-sembol 1d arşivinin 2025'te tam olduğu.
- **Kural yumuşatma yok.** Önceki "ayıda PF ≥ 1,1 ve boğada kayıp ≤ %20" kuralı (CLAUDE.md #1) bu iş için kullanıcı kararıyla yerini bu plana bırakır, ama canlıya geçiş yolu (backtest → testnet ≥ 5 gün → mainnet) aynen sürer.

## Teknik olmayan özet (Türkçe)

Bot şu an küçük kâr hedefleri için çok uzak bir stop kullanıyor. Çoğu coinde kaldıraç aslında hep 20 çıkıyor, oynaklığa göre değişmiyor. Bu yüzden "kaldıraç coine göre ayarlanıyor" demek bugün doğru değil. Yeni planda sekiz ayar kombinasyonu (stopu daraltmak, hedefleri büyütmek, kaldıracı gerçekten oynaklığa bağlamak) ve üç kod gerektiren fikir (yapıya dayalı dar stop, Bollinger ortası hedefi, sinyal gücüne göre hedef) sınanacak. Sınav 2025'ten seçilmiş 8 yeni dönemde yapılacak: 3 düşüş, 2 yükseliş, 3 yatay. Bu dönemlere daha önce hiç bakılmadı. Kuralları sonuçları görmeden yazdım: çoğu dönemde kârlı olmak, toplamda kârlı olmak, en kötü dönemde de çok kaybetmemek, masrafı iki katına çıkarınca da ayakta kalmak ve "şans eseri" olmamak gerekiyor. Bunları geçen olursa son kez bota ait en yeni 5 haftalık döneme tek seferlik bakılacak, sonra en az 5 gün testnet'te denenecek. Hiçbiri geçmezse açıkça "kazanan yok" denecek ve mevcut ayarlar değişmeyecek. Geçmiş deneyler bunun kolay olmadığını gösteriyor, bu yüzden "kazanan yok" makul bir olasılık.

Dosyalar (hepsi salt-okunur olarak incelendi):
- Kod: `WT/src/strategies/scalper/{setups.py, types.py, backtest.py, multitest.py, permutation.py, executor.py, exits.py, engine.py, forensics.py}`, `WT/src/core/config.py`.
- Betikler: `WT/scripts/{fill_klines_cache_from_vision.py, run_windows.sh}`.
- Belgeler: `WT/docs/{EXPERIMENTS.md, DECISIONS.md, RUNBOOK.md}`.
- Veri: `$SP/server-scalper-env.txt`, `$SP/baseline/*.bt.log`, `/home/user/crypto-trading-bot/data/klines_cache`.
- Yazılacak çıktı kökü: `$SP/prereg/`.