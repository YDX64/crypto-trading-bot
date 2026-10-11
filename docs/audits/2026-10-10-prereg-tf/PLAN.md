# ÖN-KAYITLI DENEY PLANI (E17, v2 + orkestratör onayı): C stratejisi büyük grafikte — "dipten long, tepeden short", 5m / 15m / 30m (1h açıklayıcı), kaldıraç coin oynaklığına göre

Taslak tarihi 2026-10-10. Depo salt-okunur (HEAD `15fd740`). Bu sürüm, yargıç-sentezli v1 taslağının (istatistik / mekanik / sadakat merceği) üç düşmanca incelemesinden (leakage-stats, mechanics, feasibility-honesty) sonra yazıldı; her bulgu depoya karşı doğrulanıp uygulandı veya somut gerekçeyle reddedildi (bkz. §13, §14). E16 PLAN.md yapısını izler ve `docs/audits/2026-10-10-prereg-exits/README.md` "Bilinen sınırlar" tablosundaki her zayıflığı düzeltir. **Henüz kilitlenmemiştir** (kilit §9'daki usulle, sonuç görülmeden yapılacak). Sunucu `SCALPER_*` env özeti yalnız yerelde tutulur, yalnız sha256'sı kilide girer, içeriği hiçbir yere kopyalanmaz.

Kanıt/ölçüm kökü (karalama; kilit adımında depoya `docs/audits/2026-10-10-prereg-tf/evidence/` altına kopyalanıp hash'lenir, bkz. Ek B): `SP=/tmp/claude-0/-home-user-crypto-trading-bot/b53b3a36-e126-5de6-b27f-ca558d8408f8/scratchpad/e17` — `stat/` (CAL sayım ve ATR ölçümleri, PnL basılmadı), `stat2/` (uçtan-uca güç simülasyonu ve bootstrap boyut ölçümü), `rev_sim/` (indirme sayımı, aynı-bar belirsizlik sayımı), `fix/` (variants üretici ve doğrulayıcı).

## 0. Önce bunu oku (teknik olmayan özet + sözlük)

**Soru.** "Dipten al, tepeden sat" mantığı (strateji C: fiyat aşırı düştüğünde long, aşırı yükseldiğinde short) 1 dakikalık yerine **5, 15, 30 dakikalık** grafikte (ve yalnız bilgi olarak 1 saatlik), kaldıraç coinin oynaklığına göre ayarlanınca, komisyonlar çıktıktan sonra para kazandırıyor mu?

**Sözlük.**
- **PF** = kazançlar toplamı ÷ kayıplar toplamı: 1.00 başabaş; 1.20 = her 1 TL kayba karşılık 1.20 TL kazanç. Hep komisyon ve kayma düşüldükten sonra.
- **R**: bir işlemde göze alınan zarar (stop mesafesi). Kazanç/zarar R katı olarak yazılır; farklı kaldıraç/stop kuran arm'lar böylece kıyaslanır.
- **DEV / VAL / FINAL**: eleme dönemi (2025), bağımsız onay dönemi (2023 başı – 2024 Kasım ortası, hiç dokunulmamış) ve son kontrol dönemi (2026 Eylül–Ekim, yalnız "hayır" deme hakkı).
- **Kapılar**: sonuç görülmeden yazılmış geç/kal kuralları.

**Dört dürüstlük uyarısı (bu belgenin geri kalanı bunları sayılarla açar).**
1. Bu deney canlı botun güvenlik kapılarını (rejim filtresi, BTC günlük hareket kapısı, hafta sonu long yasağı) **KAPATIP çıplak stratejiyi** sınar. Aşağıdaki işlem sayıları ("yılda ≈1760 / 640 / 335") buna göredir; kapılar açıkken 5 dakikada ≈670, 15 dakikada ≈150, 30 dakikada ≈80 işlem çıkar ve 15m/30m test edilemez. Kapıların etkisi yalnız 5 dakikada, iki kontrol arm'ıyla (S5G, T5G) ölçülür. "Canlı botum sadece 5 dakikalıkta" sorusunun tam karşılığı T5G'dir.
2. Backtest hem iyimser hem kötümser yönlerde yanlıdır (maker dolumu ve çıkış fiyatı iyimser, aynı mumda stop-önce kuralı kötümser) ve bu yanlılık grafik büyüdükçe artar. Aynı dönemde backtest PF 0.90, canlı testnet PF 0.47 çıktı. **Backtest'te kazanç, canlıda kazanç demek değildir**; kazanan çıksa bile yalnız "mühendisliğe uygun" denir (§10).
3. **1 saatlik** bu 8 coinle karara bağlanamaz (yılda yalnız ≈140 işlem; PF ≈1.9'un altı görünmez). Yerine karar verilebilir en yakın komşu **30 dakika** sınanır. Bu, sizin istediğinizden bir sapmadır; 1 saat açıklayıcı olarak koşulur ve raporlanır, kazanamaz.
4. Beklenti: geçmiş deneyler (E16: bugünkü ayar 8 taze pencerede PF 0.77) zor olduğunu gösteriyor; "hayır" çıkması muhtemel. "Hayır", bu yöntemin yalnız **büyük** bir avantajı yakalayabildiği (net PF ≈ 1.3–1.45 ve üstü, §11) anlamına gelir; "avantaj yok" demek değildir.

**Özet karar akışı.** Önce 2025'te 6 arm elenir (en çok 2'si ilerler); ilerleyenler hiç dokunulmamış 2023–2024'te onaylanır (eleme geçilmeden bu veri indirilmez bile); onaylanan en çok 1 arm 2026 Eylül–Ekim'de yalnız veto için bir kez koşulur. Kazanan çıksa bile canlı için kod değişikliği + testler + en az 5 gün testnet gerekir; mainnet kapsam dışıdır.

## 0.1 Bu deney ne söyler, ne söylemez

- "Kazanan yok" yalnızca şunu söyler: **net PF ≈ 1.3–1.45 (arm'a göre, §11 tablosu) ve üstü bir kenar bulunamadı**. Daha küçük bir kenarı bu tasarım yakalayamaz (bilinçli: önsel olasılık düşük, yanlış-pozitif maliyeti yüksek).
- Kazanan çıksa bile **yalnız harness kanıtıdır** (bar kapanışında karar, yaş 0). Canlıya geçmek için kod değişikliği (mum-yaşı/dedupe kapısı) + parite testleri + DECISIONS kaydı + testnet ≥ 5 gün gerekir (§10). **Mainnet kapsam dışı.**
- Önsel olasılık düşük: E16 B0 (canlı 1m) 8 taze 2025 penceresinde havuzlu PF 0.773 (1/8 pencere PF>1); aynı B0 klasik 2026 pencerelerinde 1.65 (aşırı uyum); canlı testnet PF 0.47; D28 (5m, denetlenemez) PF 0.36/0.65/0.89.
- Tasarım ölçütü: global null altında tüm kapı yığınının geçme olasılığı ≈ 0 (simülasyonda net PF 1.0'da ortak geçiş: 6 arm × 250 koşu = 1500 koşuda senaryo A'da 0, B'de 1, ağır-heterojen C'de 2 geçiş; §7.4) ve büyük bir kenar (net PF ≥ 1.5) için %60–95 geçiş (§11).

## 1. Hipotez

- **H1 (aile arm'ı a için):** Görülmemiş veride, harness maliyet modeliyle (maker 0.02 / taker 0.05 bacak başı; giriş kayması 0.02% yalnız taker girişte; çıkışlar taker) **net günlük R ortalaması > 0**. H0_a: ≤ 0.
- **E-açıklayıcı-1 (test edilmez):** S (ölçekli) arm'ı aynı TF'nin T (zamanlama) arm'ından iyidir. *(Önceki taslakta "H2/H3" adıyla geçiyordu; harness testi "H3" ile karışmasın diye yeniden adlandırıldı.)*
- **E-açıklayıcı-2 (test edilmez):** TF büyüdükçe ücret/R düşer (medyan stop %0.42 / 0.75 / 1.08 / 1.43 → ücret ≈ 0.17R / 0.09R / 0.065R / 0.05R) ama işlem sayısı ≈ 1/5, 1/17, 1/35, 1/65 azalır. Net etki deneyle ölçülür.
- "Kaldıraç oynaklığa göre" = S geometrisi (§3.1 R4). Sunucu geometrisinde (T) kaldıraç majörlerde 20x'e sıkışık kalır, oynaklığa göre DEĞİLDİR.

## 2. Veri setleri, bölümleme, sızıntı duvarı

| Set | Aralık (UTC, bitiş hariç) | Gün | Rol | Ne zaman dokunulur |
|---|---|---|---|---|
| CAL | 2026-03-01 → 2026-06-01 | 92 | YALNIZ kalibrasyon ve duman testi (S2, S3): işlem sayısı, sinyal-anı ATR%, kaldıraç/süre dağılımı. Karar vermez, DEV/VAL/FINAL'e ait değil | bitti (S2/S3 hariç) |
| DEV | 2025-01-01 → 2026-01-01 | 365 | eleme; E16 V1–V8'i içerir (B0 PF 0.773 biliniyor), **şimdi DEV ilan edilir** | her zaman |
| VAL | **2023-01-01 → 2024-11-18** (§9.5'teki boşluk kuralı saklı) | 687 | bağımsız onay, ilerleyen ≤ 2 arm için tek atış | YALNIZ DEV birini ilerletirse; veri o zamana kadar **çok-sembollü olarak indirilmez** (BTC 1d etiket verisi hariç, aşağıya bak) |
| FINAL | 2026-09-03 → 2026-10-08 | 35 | tek atış, yalnız VETO: 1 kazanan + B0 | YALNIZ VAL kazanan çıkarırsa |

**Orkestratör çerçevesinden sapma (somut kusur, VAL):** VAL = "tüm 2024" olursa DEV'in 4h rejim ısınması (250 bar = 41.67 gün) 2024-11-20 08:00'den itibaren VAL'in son haftalarını girdi olarak okur. Bu yüzden **VAL 2024-11-18'de biter** ve **2023'e geriye uzatılır**; kayıp 43 gün. (İnceleme: gerçek en erken DEV 4h barı 2024-11-20 08:00'dir; v1 taslağındaki "11-19" bir günlük hesap hatasıydı. 11-18 sınırı 2.3 günlük güvenlik payıyla bilerek tutulur; VAL'i 11-20'ye uzatmak 2 gün kazandırır ama hiçbir şeyi değiştirmez, reddedildi.)

**Duvarın otomatik doğrulaması (düzeltildi):** `lock` anında henüz DEV koşusu yoktur; bu yüzden `lock`, harness'in kendi `resolve_backtest_window` ve `_WARMUP_BY_ROLE` fonksiyonlarıyla her (üçlü × rol) için en erken barı **analitik** hesaplar ve `min(en erken bar) > VAL bitişi (2024-11-18)` değilse kilitlemeyi reddeder. Sonra her DEV koşusunun `check-run`'ı `provenance.data_windows`'tan **gerçek** en erken barı okuyup aynı eşitsizliği yeniden doğrular; ayrıca lider (BTC 1d) serisi yalnızca birkaç gün geriye uzar (`_LEADER_DAILY_EXTRA_DAYS = 3` + `run_days`; `backtest.py:406,506`) ve yalnız market gate AÇIK arm'larda (B0, S5G, T5G) okunur.

**VAL "dokunulmamış" kanıtı (grep, 2026-10-10; mekanik ve istatistik incelemecileri bağımsız yeniden doğruladı):** 2023/2024 anışı `docs/` içinde yalnız `docs/EXPERIMENTS.md:1650` ("2024 ve öncesi arşivde var"); `tests/test_dashboard_data.py` bir tarih fixture'ı; `src/trading/binance_testnet_client.py` bir yorum; `archive/2025-10-legacy/n8n/*` başka bir eski bot. `data/klines_cache` en erken 2024-12-31 (E16 V1 ısınması). Artık risk: yazarın 2023–2025 BTC fiyat yolunu bilmesi (açıklanır).

**Pre-lock piyasa-verisi okuması (düzeltilmiş ifade):** rejim etiketleri (§2.3) için **BTCUSDT 1d** serisi VAL dönemi dahil lock'tan ÖNCE okunur (piyasa verisi, strateji sonucu değildir; planlama sırasında aylık getiriler hesaplandı). VAL'in **çok-sembollü 1m–4h verisi** ise lock'tan önce indirilmez. Ayrıca VAL arşiv varlığı lock'tan önce yalnız HTTP HEAD ile yoklanır (içerik okunmaz; §4.7).

**FINAL notu:** E16 son sınavı bu pencere için ayırmıştı ama kazanan çıkmadığı için `final` hiç koşmadı; pencerede **yalnız B0 baz çizgisi** görüldü (320 işlem, WR %72.5, PF 0.90; canlı testnet 124 işlem, PF 0.47; `docs/audits/2026-10-10-prereg-exits/PLAN.md:69-70`). E17 arm'ları bu pencerede görülmemiştir. B0 yeniden koşulur ve **320 işlemi birebir, PF'i iki hanede 0.90 (0.895 ≤ PF < 0.905)** üretmelidir (parite çapası); üretmezse FINAL GEÇERSİZdir ve kazanan ilan edilemez (§8).

### 2.1 Isınma (warm-up) ve rol-anahtarı çakışması

Isınma barları sinyal üretmez, yalnız gösterge durumu taşır (rol başına: giriş 150 / bağlam 100 / rejim 250 bar, o rolün TF'sinde; `backtest.py:317`). En erken bar = pencere başı − max(rol ısınması). Aşağıdaki tarihler UTC saat dahil, hesapla ve `rev_sim`/CAL ölçümüyle (2026-03-01 − 1000 sa = 2026-01-18 08:00) doğrulandı:

| Üçlü (giriş/bağlam/rejim) | En uzun ısınma | DEV en erken bar | VAL en erken bar | FINAL en erken bar |
|---|---|---|---|---|
| 5m/30m/1h | rejim 10.4 g | 2024-12-21 14:00 | 2022-12-21 14:00 | 2026-08-23 14:00 |
| 15m/1h/4h | rejim 41.7 g | 2024-11-20 08:00 | 2022-11-20 08:00 | 2026-07-23 08:00 |
| 30m/4h/4h | rejim 41.7 g | 2024-11-20 08:00 | 2022-11-20 08:00 | 2026-07-23 08:00 |
| 1h/4h/4h (açıklayıcı) | rejim 41.7 g | 2024-11-20 08:00 | 2022-11-20 08:00 | 2026-07-23 08:00 |
| 1m/5m/15m (B0, R1) | rejim 2.6 g; lider BTC 1d ≈ 4 g | 2024-12-28 | koşulmaz | 2026-08-30 |

- FINAL ısınması 2026-07-23'e değer: E16'nın Temmuz–Ağustos "yanık" penceresini **yalnız gösterge geçmişi olarak** okur (açıklanır; bu pencerede sinyal üretilmez). VAL'in ısınma tarafı (2022-11…12) hiçbir DEV/FINAL verisiyle kesişmez.
- **Rol-anahtarı çakışması (kritik engel 1):** `gather_symbol_data` `out[interval]` ile yazar; iki rol aynı TF'deyse sonraki seri ezer. Kural: **giriş ≠ bağlam ve giriş ≠ rejim** (koşucu ve analizci `provenance.data_windows`'tan doğrular, aksi halde koşuyu reddeder). `bağlam == rejim` yalnız 30m/4h/4h ve 1h/4h/4h'ta vardır; rejim serisi (+250) bağlam serisini (+100) ezer = zararsız fazla-ısınma (bağlam rolü yalnız son 100 barı okur). Bu üçlülerde **iki ayrı 4h önbellek dosyası** (+100 ve +250 anahtarları) oluşur; `MANIFEST.data` ikisini de bekler. Kanıt: harness testi **H3** (§5.1).

### 2.2 Bölümleme kararı: her (arm, set) için TEK SÜREKLİ KOŞU

1. EOD kesmesi: bir koşuda tek sınır (en çok 8 işlem; sembol başına bir) vs 8–12 pencerede 8–12 zorla kapatma; kesilme TF ve tutma süresiyle büyür (ölçülen: 21 günlük 5m pencerede 2/25 işlem EOD, %8).
2. Cooldown, REAPER saati ve pozisyon-kapasite kapısı (`MAX_POSITIONS=5`, post-hoc kronolojik) kesintisiz kalır.
3. Günlük PnL serisi boşluksuzdur, blok bootstrap geçerlidir (E16'nın 8 ayrık 21 günü = 168 gün, blok yapısı anlamsızdı).
4. Isınma: 4h rejim 41.7 gün ister; aylık pencereler 30 test gününe 42 gün ısınma yakar.
5. Çalışma süresi/bellek engel değil: 5m tam yıl ≈ 3–4 dk (CAL: 92 günde 44–56 sn, 98 MB tepe RSS), 15m/30m/1h ≈ 1–2 dk; semboller sırayla işlenir. 687 günlük VAL ≈ 6 dk/koşu.
6. Blok başına istatistik (ay/rejim/sembol) **işlem listesinden** türetilir (giriş ayına göre), ayrı koşu gerekmez. Bedel: koşu başına tek `bar_max_drawdown`.
- **İstisna: 1m referanslar (B0, R1) takvim çeyreği bazında 4 koşu** (tam-yıl 1m bellek/süre ölçülmemiş; ≈ 3 sn/gün; 1m tutma ≤ ~9 sa olduğundan EOD etkisi birkaç işlem). Referanslar hiçbir kapıyı beslemez. Günlük seri çeyrekler birleştirilerek kurulur.

### 2.3 Rejim etiketleri (strateji koşulmadan, yalnız piyasa verisinden, kilide girer)

Kural: BTCUSDT 1d, **takvim ayı**, getiri = son günün kapanışı / ilk günün açılışı − 1; **UP ≥ +%5, DOWN ≤ −%5, aksi FLAT**. ŞOK bayrağı: ayda herhangi bir günlük |kapanış/açılış − 1| ≥ %6 (raporlanır, kullanılmaz). VAL'in son kısmi ayı (Kasım 1–17) kendi getirisiyle etiketlenir. **Ay ataması:** işlem, **giriş (dolum) barının `open_time`'ının UTC ayının** etiketine atanır (`BacktestTrade.entry_time`; sinyal barı veya çıkış ayı DEĞİL). Ay sınırını aşan işlemler giriş ayında kalır (uzun tutuşlarda etiket gecikir; açıklanır). Kısmi Kasım-2024 işlemleri G10 grup tablolarına girer, G11'deki M'ye (22 tam ay) girmez. `analyze_e17.py freeze` etiketleri 1d serisinden üretir, `labels_frozen.json` hash'i kilide girer.

Kör planlama kontrolü (yalnız piyasa verisi; `rev_btc/` ile bağımsız yeniden hesaplandı, ay-sonu-önceki-kapanış tanımıyla sıfır etiket değişimi): DEV 2025 = 5 UP / 4 FLAT / 3 DOWN: Oca +9.4 UP, Şub −17.7 DOWN, Mar −2.1 FLAT, Nis +14.1 UP, May +11.1 UP, Haz +2.4 FLAT, Tem +8.0 UP, Ağu −6.5 DOWN, Eyl +5.3 UP, Eki −3.9 FLAT, Kas −17.6 DOWN, Ara −3.0 FLAT. VAL 2023-01…2024-10 = 11 UP / 6 FLAT / 5 DOWN. `freeze` bu sayımları üretmezse aşama durur. Eşiğe 1 puan içindeki iki ay: 2023-07 (−4.07%) ve 2025-09 (+5.34%); kural mekanik olduğundan etiket değişmez.

## 3. Varyant ailesi

### 3.1 Sabit kurallar (her arm için; kilitten sonra değişmez)

Her varyant = sunucu `SCALPER_*` snapshot'ı + YALNIZ `variants.json`'da listelenen çözülmüş override'lar. Her ad `src/core/config.py`'de alan olarak doğrulandı (TF_ENTRY/CONTEXT/REGIME 365–367, REGIME_FILTER 237, MARKET_GATE 254, ENTRY_BLOCK_WEEKDAYS_UTC 540, MAX_HOLD_HOURS 216, LOSS_COOLDOWN_MINUTES 328, MAKER_FILL_TIMEOUT_CANDLES 451, STALE_TP_HOURS 230, DYN_LEV_STOP_ATR_MULT 442, FIXED_STOP_ROI_PCT 340, TP1_ROI 278, TP2_ROI 280, ENTRY_MODE 299, MAKER/TAKER_FEE_PCT 300–301, SLIPPAGE_RATE 308, MIN/MAX_STOP_PCT, DYN_LEV_MIN/MAX). Düzeltme turunda **her varyant ve her varyant×kaplama bileşimi** gerçek `Settings(_env_file=None)` doğrulayıcılarından geçirildi (`fix/verify_variants2.py`: 12 varyant + 18 kaplama bileşimi, 0 hata; en uç varyant S60: 1h/4h/4h, hold 480, cooldown 3600, S=16). Lock öncesi `scripts/e17/verify_variants.py` aynısını yapar ve çözülmüş değerleri `variants.json` ile karşılaştırır.

**R1 — Rol üçlüsü kuralı (canlı 1m/5m/15m'i birebir üretir):** bağlam = ≥ 4× giriş olan en küçük desteklenen TF, rejim = ≥ 12× giriş olan en küçük desteklenen TF, 4h ile sınırlı (1d harness'te reddedilir). 5m → 30m/1h; 15m → 1h/4h; 30m → 4h/4h; 1h → 4h/4h; 1m → 5m/15m.

**R2 — Kapı paketi BG (tüm aile arm'ları):** `SCALPER_REGIME_FILTER=false`, `SCALPER_MARKET_GATE=false`, `SCALPER_ENTRY_BLOCK_WEEKDAYS_UTC=` (boş). Diğer her şey snapshot'ta aynen: maker giriş, `MAX_POSITIONS=5`, `MAX_MARGIN_PCT=10`, `RISK_PERCENTAGE=10`, denge (equilibrium) filtresi (varsayılan açık), RSI 30/70, diverjans şart, chandelier 3.5, TP1/TP2 oranları 0.40/0.20, BE tamponu 0.05, `MIN_RR=0`, `MAKER_FILL_TIMEOUT_CANDLES=3` (kodda bar bazlı), `STALE_TP_HOURS=0`, `DYN_LEV_MIN=3`, `DYN_LEV_MAX=20`, `MAX_STOP_PCT=5.5`, `MIN_STOP_PCT` varsayılan 0.15, `STOP_MODE=fixed_roi`, `DYNAMIC_LEVERAGE=true`.
- **Orkestratör çerçevesi #3'ten sapma (somut kusur = güç):** sunucu kapıları karşı-trend sinyallerin %60–88'ini eler ve 2026 1m verisinde ayarlandı. CAL'de kapılarla yıllık işlem: 5m ≈ 670 (S5G), **15m ≈ 150, 30m ≈ 80, 1h ≈ 36** → 15m ve üstü test edilemez (tespit edilebilir PF ≥ 1.7). Kapılar kapalı: 5m ≈ 1760, 15m ≈ 640, 30m ≈ 335. Bu yüzden aile "çıplak C" (BG) üzerinde kurulur — bu aynı zamanda kullanıcının fikrinin literal halidir. Kapıların etkisi **kontrollü olarak** yalnız 5m'de ölçülür (S5G, T5G; DEV; kazanamazlar).
- Etiket düzeltmesi: S5G/T5G'de "sunucu kapıları" ifadesi tam doğru değildir. Rejim filtresi bu arm'larda **varsayılan açık** ve 5m/30m/1h üçlüsünde **1h EMA50/200** ile çalışır (sunucuda 1m girişle 15m EMA); market gate ve hafta sonu LONG bloğu snapshot'tan gelir ve sunucudakiyle aynıdır. Bu yüzden "kapılar korunmuş" denir, "sunucu kapıları" denmez.

**R3 — Süre düğmeleri: TEK kural TB ("bar sayısı"), S ve T arm'larına aynı:** sunucunun 1m değerleri giriş-TF barı olarak ifade edilir: `SCALPER_MAX_HOLD_HOURS` = 480 bar × TF, `SCALPER_LOSS_COOLDOWN_MINUTES` = 60 bar × TF, `SCALPER_MAKER_FILL_TIMEOUT_CANDLES=3` (zaten bar), `SCALPER_STALE_TP_HOURS=0`.

| TF | MAX_HOLD_HOURS | LOSS_COOLDOWN_MINUTES |
|---|---|---|
| 5m | 40 | 300 |
| 15m | 120 | 900 |
| 30m | 240 | 1800 |
| 1h | 480 | 3600 |

- Sonuç, baştan açık: REAPER (E16 B0'da 224 işlem, −33.6k) bu arm'larda fiilen **devre dışı** (CAL'de S5/S15/S30/S60/T15/T30/T60'ta limite ulaşan işlem 0; T5'te 394'te 5; REAPER yalnız TP1 öncesi). Karşı kutup (duvar saati 8 sa / 60 dk, canlı değer) **zorunlu dayanıklılık kapısı G15d**'dir (`ROB_WALL`), ama düzeltme turunda **yalnız "Σ R > 0"** olarak gevşetildi: CAL'de 8 sa/60 dk 30m'de işlemlerin %17'sini (S30: 16/94), 1h'te %32'sini REAPER'a çevirir ve işlem sayısını da değiştirir; yani ≥ 30m'de bu bir "hafif sarsıntı" değil farklı bir stratejidir ve PF ≥ 1.00 şartı keyfî bir ek öldürme kapısı olurdu. (Mekanik taslaktaki "S'de bar, T'de duvar saati" iki-dallı kural ve sadakat taslağındaki "ölçekleme yok" kuralı reddedildi: ilki tek kural değil ve T'yi S'ye karıştırır; ikincisi 1h'de REAPER'ı 9. barda tetikleyip 5m'de cooldown'ı 11 bar atlatarak grafik ile süre ilişkisini bozar.)
- Cooldown semantiği kodda (`backtest.py:1444`: `exit_reason == "SL" or pnl < 0`) bar/dakika bağımsız; REAPER'ın `REAPER` etiketi (D27) ve kayıp-cooldown'ın eski etiket uzayını okuması parite gereği olduğu gibi kalır.

**R4 — Ölçekli geometri "S":** stop = `k × ATR(14, giriş TF)`, `SCALPER_DYN_LEV_STOP_ATR_MULT=2`; `SCALPER_FIXED_STOP_ROI_PCT=S`; `SCALPER_TP1_ROI=S` (TP1 = 1R); `SCALPER_TP2_ROI=2S`. Doğrulanan mekanik (`setups.py:96–112`, `backtest.py:808–810`): `L = clamp(round(S/(k·ATR%)), 3, 20)`, stop% = S/L, TP1% = TP1_ROI/L ⇒ TP1 (R cinsinden) = TP1_ROI/S, L'den bağımsız. Koşan %40 chandelier (3.5 ATR giriş-TF), TP2 %20. `trail_relax_roi1_pct` varsayılanı 0 (ROI-paydalı iz-sürme karışıklığı yok).
- **S sabitleri kilitlidir (serbest parametre değil):** `S = min(round(20·k·P10), floor(3·MAX_STOP_PCT) = 16)`, P10 = CAL sinyal-anı ATR% (8 sembol havuzlu) onuncu yüzdelik. İki bağımsız CAL ölçümü: 5m 0.113 / 0.121 / (inceleme 0.112), 15m 0.226 / 0.228, 30m 0.319 / 0.327, 1h 0.503 / 0.508 → **S = 5, 9, 13, 16**. **Dürüstlük notu:** 5m'de formül 4.48–4.84 verir ve yuvarlama bıçak sırtındadır; **S5 = 5 donmuş bir seçimdir** (S=4 de geçerliydi: 4/20 = 0.20 ≥ MIN_STOP 0.15). Diğer üç sabit (9, 13, 16) iki ölçümde de aynı. 1h'de formül 20 verir, 16'ya kırpılır; böylece L=3'te bile stop% ≤ 5.33 < 5.5 ve sessiz max-stop düşüşü imkânsız. Sabitler `cal_constants.json` dosyasına sabit sayı olarak yazılır (kilide girer), formül kökendir; CAL yeniden okunmaz.
- Bant kontrolü: S/20 ≥ 0.25 > MIN_STOP 0.15; S/3 ≤ 5.33 < MAX_STOP 5.5 → S arm'ları stop-band'dan düşemez (`verify_variants2.py` doğruladı: S5 0.25–1.67, S15 0.45–3.0, S30 0.65–4.33, S60 0.80–5.33). CAL'de ölçülen sessiz düşüş: S5/S15/S30'da 0. T arm'ları yalnız giriş-TF ATR% ≥ 1.75 iken (L ≤ 9, stop > 5.5%) düşer; H1 sayaçları bunu sayar.
- Ölçülen etki (CAL): S5 medyan L ≈ 11, ~%16–25 işlem 20x'te; S15 ~%12; S30 ~%10; hepsinde L=3 payı ≈ 0. Ücret/R: S5 ≈ 0.17, S15 ≈ 0.09, S30 ≈ 0.065, S60 ≈ 0.05.
- Marj sınırı: S < 50'de %10 marj sınırı HER işlemde bağlar (CAL'de `margin_usdt` her S arm işleminde 1000.0), işlem başına USD riski = 10·S (S5: 50, S15: 90, S30: 130 USD); T'de ≈ 500. **USD PnL arm'lar arası karşılaştırılamaz** → tüm kapılar R birimindedir (§7.1). (Sabit-risk kontrolü maker modda boş bir kontroldür: yalnız `ROB_TAKER`'da `margin_usdt` değişebilir; kanıt sayılmaz, yalnız bütünlük kontrolüdür.)
- **T "yalnız zamanlama":** sunucu geometrisi (`S=50, k=3.0, TP1_ROI=10, TP2_ROI=25`). **Düzeltme:** "%96–100 işlem 20x'te" yalnız **5m ve 15m** için doğrudur. CAL'de 20x payı: T5 %100, T15 %96, **T30 %85, T60 %68** (L = round(50/(3·ATR%)) ATR% > 0.83 olunca 20'nin altına düşer). Stop %2.5, TP1 %0.5 (0.2R) ancak 20x'te geçerlidir.

**R5 — Hiçbir yerde değişmeyen:** `DYN_LEV_MIN/MAX`, `MAX_STOP_PCT`, `MIN_STOP_PCT`, `MAX_MARGIN_PCT` (risk parametresi), ücretler, kayma, 8 canlı sembol (BTC, ETH, SOL, XRP, DOGE, BNB, ADA, LTC; **`--symbols` bu sırayla sabit**: kapasite kapısı eşitlikte `symbols` sırasına bakar, `backtest.py:2090`; koşucu ve analizci sıranın eşitliğini doğrular).

### 3.2 Aile (m = 6, hepsi hipotez testi; BH bu altısı üzerinde)

`BG` = R2, `TB` = R3. Çözülmüş env'ler `variants.json`'dadır.

| id | giriş/bağlam/rejim | geometri | Override'lar (BG'ye ek) | CAL işlem/92g | DEV N beklenen | VAL N beklenen | FINAL N beklenen (35g) |
|---|---|---|---|---|---|---|---|
| S5 | 5m/30m/1h | ölçekli | TB(40, 300) + k=2, S=5, TP1=5, TP2=10 | 444 | ~1760 | ~3300 | ~169 |
| T5 | 5m/30m/1h | sunucu | TB(40, 300) | 394 | ~1560 | ~2940 | ~150 |
| S15 | 15m/1h/4h | ölçekli | TB(120, 900) + k=2, S=9, TP1=9, TP2=18 | 162 | ~640 | ~1210 | ~62 |
| T15 | 15m/1h/4h | sunucu | TB(120, 900) | 169 | ~670 | ~1260 | ~64 |
| S30 | 30m/4h/4h | ölçekli | TB(240, 1800) + k=2, S=13, TP1=13, TP2=26 | 84 | ~335 | ~630 | ~32 |
| T30 | 30m/4h/4h | sunucu | TB(240, 1800) | 94 | ~375 | ~700 | ~36 |

**Test edilebilirlik kuralı (CAL'de, performansa kör kilitli):** aile üyesi ⇔ CAL işlem × 365/92 ≥ 300 **ve** §11'deki uçtan-uca simülasyonda "net PF 1.5'te ortak geçme olasılığı ≥ 0.5" (senaryo B; değerler §11'de: en düşük 0.64 S5 ve 0.70 T30, tüm altı arm uygun). DEV adımında ek olarak G1 (gerçek N ≥ 200); başarısız üye m'deki yerini korur ve p := 1.0 alır.

**Açıklayıcı (DEV'de; VAL'e ulaşılırsa VAL'de de; p-değeri yok, BH dışı, kazanamaz):** S60, T60 (1h/4h/4h; BG; TB(480, 3600); S60: k=2, S=16, TP1=16, TP2=32). DEV ≈ 140–150, VAL ≈ 260–275 işlem: **"YETERSİZ GÜÇ — PF ≈ 1.9'un altı tespit edilemez"** diye güven aralığıyla raporlanır. 1h'yi gerçekten test etmek ≈ 20 sembol veya 3+ yıl havuz ister (E17b seçeneği, kapsam dışı).

**Referanslar (hipotez değil, m'de yok, kazanamaz):**
- **B0** = sunucu snapshot'ı aynen (1m/5m/15m, tüm kapılar, 8 sa / 60 dk). DEV'de 4 takvim çeyreği koşusu + FINAL'de bir koşu. Soru: "büyük grafik canlı konfigürasyondan iyi mi?"
- **R1** = B0 + yalnız BG (1m). DEV çeyrek bazında. Kapı değişikliğini TF değişikliğinden ayıran birebir kontrol.
- **S5G / T5G** = S5 / T5 ama kapılar KORUNMUŞ (BG yok; yukarıdaki etiket düzeltmesine bak). Yalnız DEV. T5G = kullanıcının harfi harfine sorusu ("canlı bot, sadece 5m grafik"). CAL: S5G 168, T5G 153 işlem/92g. DEV'de güçlü çıkarlarsa "E18 için ipucu" yazılır.

**Kaplamalar (aile üyesi değil; `variants.json` `overlays`):**
- `ROB_FEE2` = `--fee-stress` (komisyon + kayma 2×; betik `--fee-stress-multiplier` GEÇMEZ, varsayılan 2.0 kilitli).
- `ROB_DLY1` = `--entry-delay-candles 1`. **Yeniden adlandırma notu:** bu bayrak canlıdaki "aynı kapanmış barı her taramada yeniden değerlendirme" farkını yoklamaz; yalnız maker dolum penceresini bir bar kaydırır (limit fiyatı aynı; `backtest.py:718–732`). Canlı yeniden-değerlendirme farkı §10'da ele alınır.
- `ROB_TAKER` = `SCALPER_ENTRY_MODE=taker`.
- `ROB_WALL` = `SCALPER_MAX_HOLD_HOURS=8 SCALPER_LOSS_COOLDOWN_MINUTES=60` (yalnız VAL, yalnız Σ R > 0).
- `DIAG_Z0` = `SCALPER_MAKER_FEE_PCT=0 SCALPER_TAKER_FEE_PCT=0 SCALPER_SLIPPAGE_RATE=0` — teşhis, `DEV_DECISION.json` yazıldıktan SONRA, karar vermez.
- `DIAG_SBO` = `--same-bar-order optimistic` (H2; teşhis, karar vermez; §5.1 ve §8 "BAR-ORDER-BELİRSİZ").
- `DIAG_FEE15` = `--fee-stress --fee-stress-multiplier 1.5` (teşhis: FEE2'yi geçemeyip FEE1.5'i geçen arm "MARJİNAL-ÜCRETE-DUYARLI").

**Bilerek dışarıda (serbestlik derecesi eklememek için):** her TF için ikinci k, aile içinde duvar-saati arm'ları, taker arm'ları, farklı RSI eşikleri, "sinyal gücüne göre TP" (E16 M0 önkoşulu 1m'de başarısız: Spearman ρ −0.0155, p 0.621; E18 ipucu).

## 4. Veri doldurma

1. Betik: `scripts/fill_klines_cache_from_vision.py` (sha256 doğrulamalı, boşluksuz, atomik, boşlukta HİÇBİR şey yazmaz; ayrı bir "yalnız doğrula" modu YOKTUR — doldurma kendisi bütünlük geçişidir). **Arm'ın kendi `SCALPER_*` env'iyle** çalıştırılır; **`--tf-*` bayrakları ASLA** (backtest'in okumadığı anahtarlar yazabilir). Geometri env'i önbellek anahtarını değiştirmez → aynı üçlü için tek doldurma. BG arm'larında `--market-gate` yazılmaz (kapalı); B0/R1/S5G/T5G için lider serisi (BTC giriş-TF + 15m gün-içi + 1d) de yazılır.
2. Önbellek dizini depo klonunun DIŞINDA: `E17_ROOT/cache` (harness `git status --porcelain` untracked'i kirli sayar). `E17_ROOT` karalama dizini DEĞİLDİR (süreç yeniden başlayınca kaybolur; zincir 6–10 sa sürer): varsayılan `$HOME/e17_root`; aşama bittikçe `LOCK.json`, `ledger.jsonl`, `MANIFEST.*`, `DEV_DECISION.json` vb. oturumun dalına (`main-18w3qz`) `docs/audits/2026-10-10-prereg-tf/run/` altına commit'lenir (önbellek ve ham raporlar hariç, bkz. Ek A). E16 anahtarlarıyla yeniden kullanım yok (yeni gün sayıları = yeni anahtarlar).
3. **Doldurma envanteri (düzeltilmiş; v1'de duman testi pencereleri eksikti ve P2 "Binance'ten çekiliyor" ile durabilirdi):** önbellek anahtarı (sembol, TF, bitiş − ihtiyaç·adım) olduğundan her (başlangıç, bitiş, üçlü) ayrı anahtardır.
   - **P1b (duman, kilitten sonra DEV'den önce):** (a) S1: B0 üçlüsü 1m/5m/15m (+lider) için 2026-01-23→2026-02-13 ve 2025-01-06→2025-01-27; (b) S2/S3: `SMOKE_W` = 2026-04-01→2026-05-01 için dört üçlü (5m/30m/1h, 15m/1h/4h, 30m/4h/4h, 1h/4h/4h); (c) S3-R1: 1m/5m/15m BG için `SMOKE_1M` = 2026-04-01→2026-04-08. Hepsi `MANIFEST.data`'ya yazılır.
   - **P1 (DEV):** 2025-01-01→2026-01-01 için beş üçlü (5m/30m/1h, 15m/1h/4h, 30m/4h/4h, 1h/4h/4h, 1m/5m/15m) — 1m üçlüsü çeyrek pencereleri için de (4 × ayrı anahtar) — ve lider.
   - **P6 (VAL, yalnız DEV bir arm ilerletirse):** 2023-01-01→2024-11-18 için yalnız ilerleyen üçlüler + (varsayılan) 1h/4h/4h açıklayıcı; 1m üçlüsü YOK.
   - **P8 (FINAL, yalnız VAL kazanan çıkarırsa):** 2026-09-03→2026-10-08 için kazananın üçlüsü + B0 (1m/5m/15m + lider).
4. **İstek sayıları (v1'de yoktu; `rev_sim/dlcount.py` ve `uniq.py` ile yeniden üretildi, ağsız sahte kaynakla filler'ın kendi `_ensure` mantığı üzerinde):** sayfa sınırı 1500 mum (`backtest.py:138`) olduğundan aylık zip yalnız tam ay tek sayfaya sığınca kullanılır; 1m, 5m, 15m neredeyse tamamen **günlük** zip'tir. Her zip bir `.CHECKSUM` isteği daha ister.

   | Set | Benzersiz günlük zip | Aylık zip | İstek (zip + checksum) | Tek akımda süre (0.85 sn/çift) |
   |---|---|---|---|---|
   | DEV (5 üçlü) | 13 368 | 160 | 27 056 | ≈ 3.2 sa |
   | VAL (4 üçlü) | 19 968 | 272 | 40 480 | ≈ 4.8 sa |
   | FINAL (5m üçlüsü + 1m üçlüsü) | 1 312 | 8 | 2 640 | ≈ 0.4 sa |

   Üçlü bazında (üçlüler arası paylaşım düşülmeden) istekler: DEV 5m 13 952 / 15m 9 312 / 30m 6 752 / 1h 3 952 / 1m 17 600; VAL 27 168 / 18 256 / 12 880 / 7 744; FINAL 1 456 / 1 056 / 1 392 / 976 / 1 760.
5. **Hız ve paralellik (v1'de "≈1.2 istek/sn" yanlış etiketlenmişti: bu zip/sn'dir):** ölçüm: ardışık 10 zip+checksum çifti 8.5 sn; sekiz paralel `curl` indirmesi 1.06 sn (ağ paralelliğe izin veriyor). Tek akım süreleri yukarıda; 1–3 sa/set iddiası ancak bölünmüş doldurmayla geçerlidir. **Karar:** doldurma sembol × üçlü bazında bölünür (`--symbols X`, en çok 8 işçi), ortak `--download-cache` (zip yazımı pid-bazlı atomik); **P0'da CAL'de bir aylık pencerede 8 işçilik prova** yapılır (3'ü denendi, 4+ "kodda doğrulanamadı"); hata/çakışma varsa 4 işçiye inilir; VAL için hedef ≤ 1.5 sa duvar süresi.
6. Herhangi bir doldurma hatası zinciri durdurur; eksik gün seti iptal eder; **pencere/sembol ikamesi YOK** (VAL boşluk kuralı §9.5 hariç: ortak, tüm semboller için ay adımıyla kısaltma).
7. **Arşiv varlık yoklaması (lock öncesi, yalnız HTTP HEAD, içerik okunmaz):** VAL için 4h (2022-11…2024-11), 1h/30m (2022-12…), 15m/5m (her ay en az bir günlük zip) × 8 sembol; sonuç `archive_probe.json` (hash kilide girer). Planlama yoklaması: 8 sembol × 5 TF × 2022-11/12 için 80/80 HEAD 200; günlük zip'ler 2026-10-09'a, 2026-09 aylık zip'i 200. (v1 §4.5'te örnek aylar yanlış yazılmıştı: 5m/15m için gereken ay Aralık 2022'dir.) İçerik geçerliliğini yalnız doldurucu kanıtlar.
8. Her doldurmadan sonra `analyze_e17.py manifest` önbellek dosyalarının sha256'sını `MANIFEST.data`'ya yazar. **VAL ve FINAL çok-sembollü verisi aşama kapısına kadar fiziksel olarak yoktur.**

## 5. Harness, analizci, betik işleri (lock'tan ÖNCE; E16 zayıflıklarının düzeltmesi)

Kod yeri (hepsi tek commit **H**, main'e): `src/strategies/scalper/backtest.py` (H1–H2), `scripts/e17/` (analizci ve betikler), `tests/test_e17_harness.py`, `tests/test_e17_analyzer.py`. Plan ve veri dosyaları `docs/audits/2026-10-10-prereg-tf/` altında ayrı **kilit commit'i L**'dedir (§9.1).

### 5.1 Harness (commit H; her E17 koşusu tam H'yi, temiz ağaçta kullanır)

Ölçüm-yalnız değişiklikler; karar yolu DEĞİŞMEZ, harness canlıyla P1 paritesinde kalır. **Önceki taslağın "hazır yama dosyaları" (`SP/mech/e17_harness_src.patch`, `e17_harness_tests.patch`) ATILIR:** işlem alanları (`stop_price`, `atr_pct`) ekliyor (`to_dict()` şemasını değiştirir; plan bunu bilerek eklememe kararı almıştı), H2'yi içermiyor ve H3'ü yanlış üçlülere (5m/15m/1h, 15m/1h/4h, 1h/4h/4h) parametreliyor. H1–H3 bu plana göre sıfırdan yazılır.

- **H1 (zorunlu, yalnız ölçüm):** `open_position` içindeki TÜM sessiz `None` dönüşleri sayılır (`backtest.py` satır 765 stop-band; 792 `price_distance <= 0`; 809 taker modda "sonraki mum yok"; 833 `qty <= 0`; 788'deki `min_rr_rejected` ve 804'teki `maker_missed` zaten sayılıyor): anahtarlar `missed_counter["stop_band_low"|"stop_band_high"|"price_distance_zero"|"qty_zero"|"no_next_candle"]`; anahtar yalnız red olunca oluşur → `tests/test_golden_backtest.py` baytı baytına aynı. **"Ham sinyal" tanımı (v1'de tanımsızdı):** `ham_sinyal = N_kapasite_sonrası + capacity + maker_missed + min_rr_rejected + stop_band_low + stop_band_high + price_distance_zero + qty_zero + no_next_candle` (yani `open_position`'a ulaşan sinyaller); `sessiz_düşüş = stop_band_low + stop_band_high + price_distance_zero + qty_zero + no_next_candle`. Birim testler: stop %6 olan sinyal `stop_band_high`, %0.1 olan `stop_band_low` sayılır; her diğer sayaç için bir test.
- **H2 (zorunlu; v1'in H2'si yanlış nesneyi sayıyordu ve düzeltildi):** CAL ölçümü (`rev_sim/amb.py`, 2026-03-01→06-01, 8 sembol, yalnız sayım):

  | Arm | TP1 dolumu | TP1 sonrası AYNI barda BE/trail stopu | Pay | "SL ve TP1 aynı barda" | Dolum barında kapanan işlem payı |
  |---|---|---|---|---|---|
  | T5 | 328 | 94 | %29 | 0 | %5.6 |
  | S5 | 222 | 55 | %25 | 0 | %14.9 |
  | T15 | 148 | 81 | %55 | 0 | %14.8 |
  | S15 | 77 | 21 | %27 | 0 | %15.4 |
  | T30 | 82 | 65 | %79 | 0 | %25.5 |
  | S30 | 41 | 8 | %20 | 1 | %11.9 |

  `_process_candle_exits` AŞAMA B'si TP1 dolumundan sonra BE stopunu AYNI mumda değerlendirir (satır ~935–945; TP2 sonrası taban kontrolü ~951–964; `manage_position` dolum barını da `entry_idx`'ten başlayarak işler). Yani baskın belirsizlik "SL ve TP1 aynı barda" (≈ 0) DEĞİL, "TP1 sonra BE aynı barda" ve "dolum barında çıkış"tır ve TF ile büyür. Canlıda BE stopu TP1 dolumu GÖRÜLDÜKTEN sonra konur; yani harness'in kötümser BE'si canlıda yoktur, öte yandan maker dolumu tüm barı kullanır (iyimser). Net işaret bilinmiyor.
  - `--diagnostics` (varsayılan KAPALI): rapora `diagnostics` bloğu yazar: `tp1_fills`, `sl_tp1_same_bar`, `tp1_then_stop_same_bar`, `tp2_then_floor_same_bar`, `fill_bar_exit`, `fill_bar_total`.
  - `--same-bar-order {pessimistic|optimistic}` (varsayılan `pessimistic` = bugünkü davranış, bayt aynı). `optimistic`: (i) TP1'in dolduğu mumda BE stopu değerlendirilmez, (ii) TP2'nin dolduğu mumda taban kontrolü yapılmaz, (iii) dolum barında (`idx == pos.entry_idx`) çıkış işlenmez (pozisyon sonraki bardan itibaren aktif; MAE/MFE ve EOD aynı). Tüm E17 kapıları `pessimistic` ile ölçülür; `optimistic` yalnız `DIAG_SBO` teşhisidir.
  - Raporda yeni anahtarlar yalnız bayrak verilince yazılır (`diagnostics`, `provenance.sim_options`); varsayılan rapor (zaman damgası hariç) aynı kalır. Bayrak ve durum `run_backtest → simulate_symbol → manage_position → _process_candle_exits` zincirinde `missed_counter` gibi geçirilir; permütasyon yolu etkilenmez.
  - Testler: (a) pessimistic varsayılan altında golden aynı; (b) her dal için (i)/(ii)/(iii) birim testi; (c) sayaçların elle kurulmuş mum dizilerinde doğruluğu.
  - **Analizci sonucu:** her arm için PF `[kötümser, iyimser]` aralığı basılır. Kötümser PF G2'yi geçemeyip iyimser PF ≥ 1.15 ve p kapısını geçiyorsa etiket "BAR-ORDER-BELİRSİZ" (karar vermez, arm İLERLEMEZ; "kenar yok" yerine "bar çözünürlüğünde kesin değil" yazılır). Sayaçlar yoksa (H2 uygulanamazsa) ≥ 15m'deki her "yok" sonucu "bar sırası konusunda kesin değil" etiketlenir.
- **H3 (test):** `TestRoleTriplesE17`, **beş gerçek üçlü** için parametreli: 5m/30m/1h, 15m/1h/4h, 30m/4h/4h, 1h/4h/4h, 1m/5m/15m. Doğrulananlar: pencereler 150/100/250 bar, look-ahead yok, giriş = bağlam veya giriş = rejim ise koşucunun reddi, `bağlam==rejim` (30m/4h/4h) için **iki ayrı 4h önbellek anahtarı (+100 ve +250)** ve rejim serisinin kazanması, bağlam kuyruğunun eşitliği.
- **Bilinçli olarak EKLENMEYENLER:** işlem alanları (`stop_price`, `atr_pct`, `score`): R, `margin_usdt × S/100` formülüyle mevcut alanlardan türetilir (§7.1; `BacktestTrade.margin_usdt` ve `leverage` var), gerçekleşen k_eff kapısı yerine kaldıraç payı kapısı kullanılır.
- Kabul: `python3 -m pytest tests -q` yeşil (sahte gizliler env'de), golden aynı, duman testleri (S1, S2, S3). **Ek (uygulama sonrası):** ağır koşu betiği testleri (`tests/test_e17_scripts.py`, ≈160 sn) varsayılan pakette atlanır, çünkü sunucu deploy kapısı paketi `timeout 300` ile koşar (`scripts/server_deploy.sh:190`); lock'tan önce `E17_SCRIPTS_TESTS=1 E17_SLOW=1` (ve gerçek `variants.json`/snapshot ile `E17_REAL_VARIANTS`/`E17_REAL_SNAPSHOT`) ile **zorunlu** koşulur ve sonucu `evidence/`'a yazılır. Varsayılan pakette `tests/test_e17_scripts_fast.py` (bash -n, py_compile) kalır.
- **Uygulama notları (H, inceleme turundan; sonuç görülmeden):** (a) H1, `open_position`'daki `entry_hint <= 0` dönüşünü saymaz (ham veri bozukluğu; plan beş anahtar tanımlar; test sabitler). (b) H2 `optimistic` (iii): dolum barında YALNIZ MAE/MFE ve özkaynak işareti işlenir; SL/TP/trail **ve** yapı/CHOCH, STALE_TP, REAPER çıkışları da atlanır. (c) `--permutations` ile `--same-bar-order optimistic` birlikte verilirse harness hata verir (iyimser gözlem vs kötümser null karışmasın). (d) Harness `run_backtest` içinde `check_timeframe_roles` giriş = bağlam veya giriş = rejim üçlüsünü reddeder (bağlam = rejim serbest).

### 5.2 Koşu betikleri (`scripts/e17/lib17.sh`, `run_smoke.sh`, `run_dev.sh`, `run_val.sh`, `run_final.sh`)

- `set -Eeuo pipefail`, `trap ... ERR`, `die()` ile açık `exit 97`, `PYTHONHASHSEED=0`; **`|| echo` yok**; herhangi bir doldurma/backtest hatası tüm zinciri sıfırdan farklı kodla durdurur (E16: hata yalnız loglanıyordu).
- `run_bt` önce `runs/<etiket>.json` ve `logs/<etiket>.log` dosyalarını SİLER (bayat çıktı olamaz); koşuyu **temiz bir `git clone`'da (H commit'i), taze geçici cwd'de, `env -i` ile ve YALNIZ şu geçişlerle** çalıştırır: `PATH`, `HOME=<geçici>`, `PYTHONPATH=<klon>`, `PYTHONHASHSEED=0`, `TRADINGBOT_LOG_DIR=<geçici>`, o arm'ın snapshot+`env` satırları, sahte gizliler (`BINANCE_API_KEY`, `BINANCE_API_SECRET`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `DEEPSEEK_API_KEY`, `JWT_SECRET`); `.env` YOK (Settings `.env`'i cwd'ye göre okur, `config.py:54`; taze cwd bunu çözer). `ENTRY_BLOCK_WEEKDAYS_UTC=` boş değerin `env -i` ile aktarımı koşucu testinde doğrulanır (`verify_variants2.py` Settings düzeyinde doğruladı). `logs/` ve `data/klines_cache/` gitignored olduğundan klon kirli sayılmaz; `.env` ve başka untracked dosya kirli sayar. `--symbols` sırası sabit (§3.1 R5).
- **Mühürlü çıktı (v1 usulü yalnız prosedürdü):** harness'in tam raporu stdout'a PnL basar; koşu betiği stdout/stderr'i `E17_ROOT/sealed/logs/<etiket>.log`'a yönlendirir ve **konsola yalnız `BT OK <etiket> <sn>` / `BT FAIL <etiket>` basar**; hiçbir PnL, PF veya işlem sayısı yankılanmaz (E16 `run_bt` `TOPLAM` satırını yankılıyordu). Raporlar `E17_ROOT/sealed/runs/` altında `chmod 0400` yazılır; `check-run` yalnız geçti/kaldı basar; mühür yalnız `DEV_DECISION.json` yazıldıktan sonra kalkar (insanlar o zamana kadar okumaz; prosedür dürüstlük-sistemidir, makine zorlaması değildir ve öyle ilan edilir).
- Koşu sonrası `analyze_e17.py check-run <etiket>`: `provenance.git_sha == H`, `git_dirty == false`, test penceresi == kilitli, TF üçlüsü == kilitli, **`provenance.scalper_config`'te `variants.json`'daki HER anahtarın çözülmüş değeri beklenene eşit** (rapor yüzlerce `scalper_*` anahtarı taşır; kural "whitelist'teki her anahtar eşit"tir), `cost_model.stress_multiplier`/`entry_delay_candles` beklenen, `sim_options` beklenen, `missed_signals` ve `overall.bar_max_drawdown` var ve sonlu, `overall.trades == len(trades)`, `sum(pnl) == total_pnl`, log'da "Binance'ten çekiliyor" satırı YOK (önbellek ıskası = iptal), her sembol/TF için `candles_in_test_window == gün × bar/gün` (boşluk), rol-anahtarı çakışması yok, **gerçek en erken bar > VAL bitişi** (§2). Harness `overall.profit_factor` `inf` iken JSON'a `null` yazılır (`_strict_json_value`, `backtest.py:2034`); 1e-6 çapraz kontrolü `null`'u `inf` sayar. Başarısızlık çıktıyı siler ve zinciri durdurur; başarı `ledger.jsonl`'a (etiket, UTC, süre, sha256) ekler; analizci ledger'da olmayan `runs/*.json`'ı reddeder.
- **Sıra:** koşular ardışık yürütülür. Gerekçe düzeltildi: "paralel = Binance 429" yalnız DOLDURMA için geçerlidir; backtest yalnız önbelleği okur (ıska = iptal), yani paralel koşmak teknik olarak mümkündür, ama ledger sırası, belirlenimcilik ve basitlik için ardışık tutulur (her koşu ≤ 6 dk). Tamamlanmış koşu asla tekrarlanmaz; yalnız altyapı hatası (rapor üretilmeden rc ≠ 0) yeniden denenir ve `CHAIN.log`'a yazılır.
- **Otomatik duman testleri (engelleyici; lock'tan sonra DEV'den önce; DEV/VAL/FINAL SONUCU okunmaz):**
  - **S1 parite:** B0 AYI (2026-01-23→02-13, DEV DIŞI) = 149 işlem / WR 87.25 / PF 2.0284 / PnL 5703.2322 / `bar_max_drawdown` 1428.4953; B0 V1 (2025-01-06→01-27) = 194 işlem / WR 79.38 / PF 0.8630 / PnL −1868.1712 (E16 `results.csv`). **Dürüstlük:** V1 penceresi DEV'in içindedir; ama bu koşuda yalnız E16'dan önceden BİLİNEN sayıların eşleşmesi kontrol edilir ve çıktı yalnız "eşleşti/eşleşmedi"dir. Uyuşmazlık = H davranışı değiştirdi → dur. (`--diagnostics` açıkken de aynı olmalıdır.)
  - **S2 belirlenimcilik (CAL penceresi, DEV DEĞİL):** S15 `SMOKE_W` (2026-04-01→05-01) ayrı iki süreçte, `PYTHONHASHSEED` 0 ve 1; `trades` kanonik-JSON sha256 aynı olmalı. (v1 DEV'den 2025-03'ü kullanıyordu; düzeltildi.)
  - *(Uygulama notu: H1 sayaç anahtarları yalnız red olunca oluşur — golden bayt aynılığı için; S3 ve analizci anahtar YOKSA değeri 0 sayar, şema hatası saymaz.)*
  - **S3 konfigürasyon (CAL penceresi, 30 gün; v1'in 7 günü gürültüydü: S30 haftada ≈ 6, S60 ≈ 3 işlem):** altı aile arm'ı + S60/T60 `SMOKE_W`'da, R1 `SMOKE_1M`'de `ValueError`'sız biter; `diagnostics` bloğu ve H1 sayaç anahtarları (rapor şeması) var, her arm ≥ 1 işlem üretir; 20x/3x kaldıraç payları **yalnız basılır, kapı DEĞİLDİR** (v1'in "T arm'larında ≥ %90 20x" eşiği ölçümle çelişiyordu: T30 %85, T60 %68 ölçüldü; 30 günde S30 ≈ 28 işlem olduğundan pay kapısı gürültüdür; asıl kapı DEV'deki G0'dır). CAL çıktıları karar için kullanılmaz.
- Düzeltme izni: duman testi başarısızlığı yalnız altyapı düzeltmesine izin verir; usul §9.1.

### 5.3 Analizci `scripts/e17/analyze_e17.py` (+ `tests/test_e17_analyzer.py`, ikisi de hash'lenir)

`analyze_prereg.py` DEĞİŞTİRİLMEZ. Alt komutlar, dosya şemaları, çıkış kodları Ek A'dadır.

- **Sıkı-hata kilitleri:** `LOCK.json` şunları tutar: PLAN.md, `variants.json`, `labels_frozen.json`, `cal_constants.json`, `archive_probe.json`, kanıt betikleri (Ek B) dosyalarının sha256'sı; **harness commit'i H'nin tam sha'sı ve `git rev-parse H^{tree}`**; H ağacındaki `src/strategies/scalper/*.py` (yalnız `backtest.py`, `setups.py`, `indicators.py`, `regime.py`, `types.py`, `market_gate.py`, `entry_gates.py`, `kline_cache.py` değil: **`structure.py`, `permutation.py`, `scanner.py`, `data.py` dahil tüm içe aktarılan modüller**; ağaç hash'i hepsini kapsar), `src/core/config.py`, `scripts/fill_klines_cache_from_vision.py`, `scripts/e17/*`, `tests/test_e17_*.py` dosyalarının `git show H:<yol>` blob hash'leri; `requirements` kilit dosyasının hash'i ve Python sürümü; snapshot hash'i (yalnız hash). **Kaynak çalışma ağacı değil, H blob'larıdır:** ilgisiz bir main birleşmesi `config.py`'yi değiştirse bile (CLAUDE.md'ye göre neredeyse her canlı değişiklikte olur) deney yarıda kırılmaz; koşular H'nin temiz klonunda yapıldığından ve `check-run` `git_sha == H` zorladığından sızma da olmaz. **Her alt komut** (`final` ve `decide` dahil) işe başlamadan hepsini yeniden hesaplar; uyuşmazlıkta **çıkış kodu 3**, hiçbir dosya yazılmaz (E16: yalnız raporlanıyordu, `final` denetlemiyordu). Doğrulama referansı çalışma kopyası değil `git show <L>:<yol>/LOCK.json`'dır (§9.1).
- **İstatistik parametreleri CLI'dan değiştirilemez:** argparse yalnız `--root` ve aşama adını sunar. `BOOT_N`, `BLOCK_DAYS`, seed şeması, `ALPHA`, `Q`, tüm eşikler modül sabitidir; env değişkenleri yalnız yol için okunur. Test `parser._actions`'ı sayar ve fazlası varsa kırmızıya düşer. Üretim fonksiyonunun dahili `boot_n` parametresi vardır (CLI'dan GÖRÜNMEZ; yalnız testler B=399 ile çağırır).
- **Hassasiyet:** PF ve tüm oranlar tam float saklanır, **4 ondalık** yazılır (CSV'de 6); kapı satırları "değer vs eşik"i yuvarlamadan 6 hane basar ve eşiğe 0.005 içindeki değerleri `SINIRDA` işaretler (E16: PF 1.0043 "1.00" basılıyordu). PF `trades[].pnl`'den R'ye çevrilerek yeniden hesaplanır (pozitif toplam / |negatif toplam|), `inf` 5'e kırpılır.
- **BH** analizci içinde yerel uygulanır; kilitli commit'teki `src.strategies.scalper.multitest.benjamini_hochberg` ile 3 sabit vektörde q-değerleri birebir aynı olmalı (test).
- **Zorunlu testler:** günlük vektör inşası; merkezleme özelliği (sıfır-ortalamalı veride p ≈ 0.5); **null boyutu: 1000 simüle null seri × `boot_n=399`, D=120, üretim fonksiyonuyla red oranı ≤ 0.078 @ α 0.05** (sınır = 0.05 + 4·√(0.05·0.95/1000); v1'in 400 seri × B=19 999 testi ≈ 9 dk sürüyordu ve repo CI'ının 60 sn bütçesine sığmazdı, ayrıca 0.075 sınırı yalnız 2.3 standart hata idi → kararlı sabit tohumda bile sahte kırmızı üretebilirdi); tam B=19 999 / 600 seri boyut testi yalnız `E17_SLOW=1` ile (CI dışı) ve lock anında bir kez koşulur, sonucu `evidence/`'a yazılır; p belirlenimciliği; BH paritesi; rejim etiketi kuralı; ay ataması (`entry_time`); yoğunlaşma fonksiyonları; leave-one-out PF; **her kapı için eşiğin hemen üstünde/altında iki fikstür** (G0–G17 doğru/yanlış sınırları); kilit uyuşmazlığı exit 3 ve sıfır dosya; aşama kapıları (`val` DEV_DECISION olmadan, `final` VAL kazananı olmadan, `DEV_DECISION.json` ikinci kez yazılırken reddedilir); bayat çıktı silme; PF hassasiyet gösterimi; parser'da istatistik bayrağı yok; sabit-risk kontrolü; `null` PF = `inf`.
- **"Mutasyon-denetimli" mekanizması (v1'de tanımsızdı):** eşikler `THRESH` sözlüğünde modül sabitidir; `tests/test_e17_analyzer.py::test_mutation_*` her kapı için `THRESH[gate]`'i gevşetilmiş değerle monkeypatch eder ve o kapının "eşiğin hemen altı" fikstürünün FAIL→PASS'e döndüğünü doğrular (dönmüyorsa kapı fiilen testlenmiyor demektir → kırmızı).
- **Çıktılar (arm × set):** işlemler, WR, PF (4 hane) ve PF `[kötümser, iyimser]` (H2), net R, ay tablosu (giriş ayı), rejim grubu tablosu, **yön × rejim tablosu (yalnız rapor; E16'da LONG −18.8k, SHORT −10.6k çıkmıştı; kullanıcının sorusu uzun/kısa)**, sembol tablosu, çıkış nedeni karışımı (TRAIL/SL/REAPER/EOD), kaldıraç histogramı ve 20x/3x payları, sinyal vs işlem (stop_band, rejim/market/hafta, maker_missed, capacity), EOD sayısı, `diagnostics` sayaçları, p (b=7, b=28; bilgi için b=14), t, q, PF %90 GA, DD_R ve USD DD (bilgi).
- **Sabit-risk kontrolü:** işlemlerin ≥ %95'i medyan riskin ±%5'inde (aksi halde arm GEÇERSİZ; maker modda boş kontrol, yalnız bütünlük).

## 6. Yürütme sırası

| Faz | İş | Durma koşulu |
|---|---|---|
| P0 | (a) H1–H3 + analizci + testler + betikler + `scripts/e17/verify_variants.py` main'e (commit H); pytest yeşil, golden aynı; (b) CAL sabitleri dosyası; `freeze` (etiketler, VAL BTC 1d dahil); arşiv HEAD yoklaması; 8 işçilik doldurma provası (CAL ayı); (c) `lock` → kilit commit'i L **uzak dala (`origin/main-18w3qz`) itilir**, `sha256(LOCK.json)` ve L başka yerlere de kaydedilir (§9.1) | herhangi bir test kırmızı veya push yapılamıyorsa → kilit yok |
| P1b | duman doldurmaları (§4.3) + `manifest` | doldurma hatası → dur |
| P2 | Duman testleri S1, S2, S3 | hata → yalnız altyapı düzeltilir (§9.1 yeniden-kilit usulü); henüz hiçbir DEV sonucu yok |
| P1 | DEV verisini doldur (5 üçlü) + `manifest` | doldurma hatası → dur |
| P3 | DEV koşuları, ardışık, mühürlü: S5 T5 S15 T15 S30 T30 → S60 T60 → S5G T5G → R1 (4 çeyrek) → B0 (4 çeyrek). **Tümü bitmeden hiçbir sonuç basılmaz.** | harness hatası → dur |
| P4a | `analyze_e17.py dev-screen` → `DEV_SCREEN.json` (G0–G5; yalnız hangi arm'lara G6 koşusu gerektiği basılır) | hiçbiri G0–G5'i geçmezse **DUR** (VAL verisi hiç indirilmez, FINAL dokunulmaz) |
| P4b | G6 kaplama koşuları: **G0–G5'i geçen HER arm** için ROB_FEE2, ROB_DLY1, ROB_TAKER (≤ 18 koşu, her biri 1–3 dk) | altyapı hatası → dur |
| P4c | `analyze_e17.py dev-decide` → **`DEV_DECISION.json` TEK KEZ yazılır** (ikinci yazım reddedilir): G6 sonuçları, sıralama, ilerleyenler | ilerleyen yoksa **DUR** |
| P5 | Teşhis koşuları (karar vermez): DIAG_Z0, DIAG_SBO tüm aile; DIAG_FEE15; açıklayıcı tablolar; mühür kalkar | |
| P6 | **VAL doldur** (yalnız ilerleyen üçlüler + 1h/4h/4h açıklayıcı; §9.5 boşluk kuralı); VAL koşuları (ilerleyen başına: temel + FEE2 + DLY1 + TAKER + WALL); S60/T60 VAL; `analyze_e17.py val` (G7–G16) | kazanan yoksa DUR (FINAL dokunulmaz) |
| P7 | FINAL: doldur, **tek kazanan + B0** bir kez koş, `final` (G17); `FINAL.json` bir kez yazılır | tek atış |
| P8 | `decide` → `DECISION.md`; ana oturum `docs/EXPERIMENTS.md` E17 ve `docs/DECISIONS.md` kaydı | |

Süre tahmini (düzeltilmiş): doldurma DEV ≈ 3.2 sa tek akım / 8 işçiyle hedef ≈ 0.5–1 sa; VAL ≈ 4.8 sa tek akım / hedef ≤ 1.5 sa; FINAL ≈ 0.4 sa. DEV koşuları ≈ 2 sa (1m referanslar baskın: 8 çeyrek × ≈ 4–5 dk + 5m arm'ları ≈ 3–4 dk); G6 ≈ 40 dk; VAL koşuları ≈ 1 sa; bootstrap ≈ 3 sn (D=365) / 6 sn (D=687) p başına.

## 7. İstatistik

### 7.1 Birim: net R

`R_i = pnl_i / (margin_usdt_i × S_arm / 100)`; `S_arm` = o arm'ın `SCALPER_FIXED_STOP_ROI_PCT` değeri (T ve referanslarda 50). `fixed_roi`'de stop% = S/L ⇒ risk = marj × S/100 (doğrulandı: `BacktestTrade.margin_usdt`, `leverage` alanları var; CAL'de her S arm işleminde `margin_usdt` = 1000.0, SL çıkışlarının R medyanı −1.03…−1.17 = ücret yükü kadar). `pnl` ücret ve kayma sonrası nettir. Arm'lar arası USD PnL ve USD drawdown KARŞILAŞTIRILMAZ; tüm kapılar R'dedir. `DD_R = overall.bar_max_drawdown (USD, bar-bazlı MtM) / medyan(margin_usdt × S/100)`. `Calmar_R = (Σ R × 365/gün) / DD_R`. Bakiye sabit (bileşik yok). **Mutlak DD_R üst sınırı kapı DEĞİLDİR** (aşağıda G13 gerekçesi): yalnız bildirilir ve USD karşılığıyla (sanal 10 000 bakiye) birlikte basılır.

### 7.2 Birincil test (arm × set)

- Günlük seri `x_d` = o **çıkış günü** (UTC, `exit_time` = çıkış barının `close_time`) kapanan işlemlerin net R toplamı; işlemsiz günler 0; D = set gün sayısı (365 / 687 / 35); EOD'de kapananlar dahil ve sayılır.
- `t_obs = mean(x) / (sd(x, ddof=1)/√D)`; sd = 0 veya işlem yoksa p := 1.0.
- **Null-merkezli, tek-yönlü, studentize dairesel blok bootstrap:** `x̃ = x − mean(x)` (H0: ortalama = 0 yeniden örneklem dağılımına dayatılır); blok uzunluğu **b = 7 gün birincil, b = 28 gün ikincil (G9)**, b = 14 yalnız bilgi (önceden kayıtlı, veriye uyarlanmaz); B = 19 999 yineleme, `ceil(D/b)` blok başlangıcı 0..D−1 düzgün (sarmalı), birleştir, D'ye kırp, `t* = mean*/(sd*/√D)`. `p = (1 + #{t* ≥ t_obs}) / (B + 1)`. Tohum = `sha256("E17|<aşama>|<arm>|b<b>")`'ın ilk 8 baytı; RNG stdlib `random.Random`, indeks `int(random()*D)` (Python/NumPy sürümleri arası kararlı). ≈ 1.4 sn (D=365) / 2.8 sn (D=687) p başına (saf Python, ölçüldü: `rev_sim/boot.py`).
- **Monte-Carlo hatası kuralı (v1'de yoktu):** B = 19 999'da p'nin standart hatası ≈ 0.0005–0.003. G3 (BH) veya G9 kararı p ± 3·SE(p) (SE = √(p(1−p)/B)) aralığında ters dönebiliyorsa o p aynı tohum şemasıyla (`…|hi` eki) B = 199 999 ile yeniden hesaplanır ve karar onunla verilir.
- **Neden iki blok uzunluğu (v1'in yalnız b=7'si düzeltildi):** aylık rastgele etki (rejim/ay ortalama kayması) varken b=7 liberaldir. `stat2/mc_size.py` (500 simülasyon, B=999, kalın kuyruklu t(4) günlük getiri, aylık etki sd = τ·σ_gün; SE ≈ 0.01); nominal 5% / 2.5% / 1%'de gerçek red:

  | D | τ/σ | b = 7 | b = 28 |
  |---|---|---|---|
  | 687 | 0 | 0.072 / 0.032 / 0.010 | 0.074 / 0.038 / 0.014 |
  | 687 | 0.15 | 0.108 / 0.052 / 0.028 | 0.084 / 0.054 / 0.016 |
  | 687 | 0.25 | 0.118 / 0.072 / 0.042 | 0.068 / 0.034 / 0.020 |
  | 365 | 0 | 0.040 / 0.018 / 0.012 | 0.056 / 0.020 / 0.014 |
  | 365 | 0.15 | 0.090 / 0.054 / 0.030 | 0.072 / 0.040 / 0.022 |
  | 365 | 0.25 | 0.154 / 0.094 / 0.060 | 0.104 / 0.068 / 0.044 |

  (Kanıt: `stat2/mc_size_687.txt`, `stat2/mc_size_365.txt`; ilk iki taslağın tasarım etkisi 1.3 varsayımı ölçülmemişti: τ = 0.25σ'da etki ≈ 2.9.) Bu yüzden VAL'de **her iki p birden** gerekir (G9) ve DEV yalnız eleme sayılır. p-değerleri tek başına nominal düzeyi tutmaz; yanlış-pozitif sınırı kapı yığınının BİRLİKTE ölçülmüş geçme olasılığıyla (§7.4) verilir.
- Yalnız bilgi (karar vermez): b=14 ile p; aynı blok yeniden örneklemesinden PF %90 GA ((günlük pozitif toplam, günlük negatif toplam) çiftleri); ay düzeyinde (12/22 gözlem) tek-yönlü t testi; harness `--permutations` kullanılmaz.

### 7.3 Çoklu test

- DEV: aile (m = 6) üzerinde **BH, q ≤ 0.20** (eleme düzeyi; DEV onaylayıcı set değildir). Açıklayıcı arm'lar ve referanslar m'de değil. G0/G1'i geçemeyen arm'ın p'si := 1.0 (m = 6 sabit kalır → muhafazakâr).
- VAL: ilerleyen k ≤ 2 arm'a **Bonferroni α = 0.05/k** (bağımsız veri).
- Önceki 46+ bakış: E2–E16 hepsi 2026 1m pencereleri ve E16'nın 2025 pencerelerinde; E17'nin hipotezleri farklı nesneler (giriş TF × geometri) ve onaylayıcı p-değeri hiçbir önceki bakışa ve hiçbir E17 tasarım kararına dokunmamış VAL'de yaşar. Önceki bakışlar **önsel olasılığı** düşürür, VAL p-değerinin geçerliliğini değil; buna karşı: (i) onay VAL'de (DEV'de değil), (ii) DEV = E16'nın gördüğü 2025 yalnız eleme, (iii) sıkı ekonomik kapı yığını.
- **Tasarım kararlarının körlüğü (düzeltildi, v1 bunu abartıyordu):** E17'nin tasarımı PnL-KÖR DEĞİLDİR. CAL ölçümleri PnL basmadı, ama (i) kapı paketi BG ve S sabitleri, drafter'ların klasik pencerelerde (AYI/YATAY/BOĞA) koştuğu 60 ön-taslak raporu ve T15 fee/gecikme/sıfır-ücret koşularını gördükten SONRA kesinleşti (§12.12 yollar ve PF'lerle listeler; bu pencereler DEV/VAL/FINAL dışındadır, yani veri sızıntısı yok, ama tasarım körlüğü de yok) ve (ii) CAL'in kendi JSON'larında PnL vardır (kimse basmadı; dürüstlük sistemi). Çıkarım: önsel olasılık, "5m/15m/60m'de klasik-benzeri varyantlar 1m'in kazandığı yerde kaybetti" yönünde düşüktür; hiçbir eşik veya sabit bu sonuçlara göre ayarlanmamıştır (S sabitleri formülden, kapı paketi trade-sayısı argümanından).

### 7.4 Uçtan-uca güç ve yanlış-pozitif simülasyonu (model tabanlı; ölçülmüş PnL yok)

Kaynak: `stat2/pipe_power3.py` (+ `res3_*.json`, `out3_*.txt`). v1'in güç tablosu (`stat/arm_power.py`: iid +1.2R/−1R ikili getiri, z-testi, ölçülmemiş tasarım etkisi 1.3, yalnız G2/G3/G8/G9) **atıldı**. Yeni model:

- **Getiri modeli (geometriden, ölçülmüş PnL'den değil):** S arm'ı: kayıp −1R; kazançlar {TP1 sonrası BE: +0.4R; TP1+TP2 sonrası koşan BE'de: +0.8R; koşan: 0.8 + 0.4·Üstel(1.5)} (olasılıklar 0.20 / 0.15 / kalan); T arm'ı: kayıp −1R, kazanç Gama(2, ws/2) ortalama ws = 0.195 (5m) / 0.25 (15m) / 0.31 (30m) (E16 1m: TRAIL ort. +0.164R, SL ort. −1.03R ile tutarlı). Kayıp olasılığı hedef net PF'ye kalibre edilir. Ücret/R: S5 0.17, S15 0.09, S30 0.065, T ≈ 0.03; `ROB_TAKER` ek yükü S5 0.12 … T 0.02; `ROB_DLY1` kaybı 0.03…0.005; `ROB_WALL` kaybı yalnız 30m'de 0.02–0.04 (DLY1 ve WALL MODELLENMEDİ, ceza olarak eklendi: varsayım).
- **Bağımlılık:** gün aktivitesi Gama(3) aşırı dağılımlı Poisson; aylık rastgele etki (kayıp olasılığına ekli), sembol etkisi, rejim etkisi (DOWN daha kötü). Senaryolar: **A** homojen; **B** (kayıt altı, ılımlı: aylık kenar sd ≈ 0.04R/işlem, tasarım etkisi 1.4–3.7); **C** ağır (2× B).
- Kapılar: bu planın §8'i (G0–G16; G14 ve G0 dışındakiler) birebir uygulanır; DEV ve VAL aynı yinelemede eşlenmiş (havuzlu DOWN kuralı için), k = 2, p'ler B = 599 ile.
- **Null altında (net PF = 1.0):** 6 arm × 250 = 1500 koşuda ortak (DEV ve VAL) geçiş **A'da 0, B'de 1 (%0.07), C'de 2 (%0.13)**. DEV tek başına "geçer": A %0–4, B %5–9 (S5 hariç), C %8–15 (eleme düzeyi; VAL bunu eler).
- Sonuçlar §11'de.

## 8. Karar kuralı (tüm sayılar kilitli, R birimi, ücret sonrası)

Tanımlar: PF net, 4 hane; N = kapasite kapısı sonrası kapanan işlem; ay = giriş ayı (§2.3); grup = donmuş UP/FLAT/DOWN etiketi; LOO = bir-çıkar; "tam ay" sayısı M (VAL'de 22); T = toplam net R.

**DEV — arm ilerler ⇔ G0–G6 hepsi (yalnız aile arm'ları).**
- **G0 geçerlilik** (başarısız → arm GEÇERSİZ, p := 1.0, ilerlemez; bütünlük kapısı): (a) `sessiz_düşüş / ham_sinyal ≤ %10` (H1 sayaçları, §5.1); (b) EOD işlem sayısı ≤ 8 (sembol başına en çok bir); (c) S arm'larında **3x kaldıraç payı ≤ %10 ve 20x payı ≤ %50** — 20x tavanı YALNIZ DEV'de kapıdır (DEV oynaklığı CAL'e yakın; ölçüm: sakin dönemde 20x payı yükselir [2026-08 pencerelerinde barların %54–56'sı 20x kelepçesine düşer]; 2023 çok sakin bir yıl olabilir, bu yüzden VAL'de aynı tavan kapı değil rapordur ve ihlalde arm "kelepçe-yönetimli" etiketi alır); (d) sabit-risk kontrolü (maker modda boş, yalnız bütünlük).
- **G1** N ≥ 200.
- **G2** PF ≥ 1.15.
- **G3** BH q ≤ 0.20 (m = 6; p(b=7)).
- **G4** yoğunlaşma: en iyi ay çıkarılınca PF ≥ 1.00 **ve** en iyi 5 işlem çıkarılınca Σ R > 0.
- **G5** rejim tabanı: ≥ 40 işlemli her grupta PF ≥ 0.80.
- **G6** DEV dayanıklılık (**G0–G5'i geçen HER arm'a** koşulur — v1'in "önce en çok 2'ye indir, sonra G6" sırası G6'da elenen iki arm yüzünden G6'yı geçecek üçüncü arm'ı hiç denemiyordu ve 5m çiftine yapısal yatkındı): (a) `ROB_FEE2` PF ≥ 1.00; (b) `ROB_DLY1` Σ R > 0; (c) `ROB_TAKER` PF ≥ 1.00.
- **İlerleme seçimi:** G0–G6'nın hepsini geçenler `t_obs`'a (b=7, DEV) göre sıralanır; **her giriş TF'den (5m/15m/30m) en çok biri** (S ve T'nin `t_obs`'u yüksek olanı), toplamda **en çok 2 arm**. `t_obs` √N ile büyüdüğünden ham sıralama hep 5m çiftini seçerdi; TF başına tek temsilci bunu önler, yine de 5m geçerse 15m/30m'nin önüne geçebilir (bilinen taraflılık; açıklanır). Eşitlikte küçük TF (daha çok işlem) yerine büyük TF seçilir. k = ilerleyen sayısı. `DEV_DECISION.json` **tek kez** yazılır.
- Hiçbiri → **DUR** (2023–2024 hiç indirilmez).

**VAL (2023-01-01 → 2024-11-18) — arm DOĞRULANIR ⇔ G7–G16 hepsi (k = ilerleyen sayısı).**
- **G7** N ≥ 250.
- **G8** PF ≥ 1.15.
- **G9** tek-yönlü `p(b=7) ≤ 0.05/k` **ve** `p(b=28) ≤ 0.10/k` (§7.2; Monte-Carlo hatası kuralı geçerli).
- **G10** rejim: (a) ≥ 40 işlemli her VAL grubunda PF ≥ 0.90 **ve** en az iki grup ≥ 40 işlemli (15–39 raporlanır, kapı değil); (b) **CLAUDE.md yasak #1 (ayı rejimi PF ≥ 1.1) kapıya çevrildi (v1 bunu bilgi satırı bırakıyordu ve "testnet adayı" ifadesiyle çelişiyordu):** DEV+VAL'in **DOWN etiketli ayları havuzlu** (DEV 3 + VAL 5 ay) PF ≥ 1.10 ve n ≥ 100 (n < 100 → "TEST EDİLEMEDİ" = başarısız). Havuzlama bilinçli: tek başına 5 VAL DOWN ayı çok az işlem taşır. Yasak #1'in ikinci maddesi (boğada PnL kaybı ≤ %20, B0'a göre) §10 madde 5'te ayrı bir testnet-öncesi koşuldur.
- **G11** genişlik: Σ R > 0 olan ay sayısı ≥ ceil(0.55 × M) (M = 22 → ≥ 13); işlemsiz ay pozitif sayılmaz.
- **G12** yoğunlaşma (T > 0 olmalı, değilse başarısız; **v1'in pay sınırları gevşek N'de ince kenarlarda geçerli arm'ları yapısal olarak düşürüyordu**: `stat2/g12probe.py` ve `rev_g12.py`: 30m'de PF 1.3 gerçek kenarda en iyi-sembol ≤ %40 payı %19–24, en iyi-5-işlem ≤ %25 payı %17–41 oranında başarısız oluyordu; kaldırma testleri bunların yerini alır, pay sınırları yalnız bariz tek-kaynak vakalarını yakalayacak kadar gevşetildi): en iyi sembol ≤ %50 T; en iyi ay ≤ %30 T; en iyi işlem ≤ %15 T; en iyi 5 işlem ≤ %40 T; en iyi sembol hariç PF ≥ 1.05; en iyi ay hariç PF ≥ 1.05; LOO-sembol PF ≥ 1.00 (her sembol için).
- **G13** `Calmar_R ≥ 0.75` (ölçeksiz). Mutlak DD üst sınırı kapı değil (v1'in `DD_R ≤ 25` kapısı 5m'de √N büyümesi yüzünden net PF 1.3'te geçerli S5'i A senaryosunda %46, B'de %78 oranında eliyordu: `stat2/pipe_power2.py` çıktıları 92/200 ve 157/200; Calmar_R tek başına yeterli ve ölçeksizdir) — `DD_R` ve USD karşılığı bildirilir; gerçek hesap boyutlandırması bir risk parametresidir (§10 madde 6).
- **G14** geçerlilik (VAL): `sessiz_düşüş / ham_sinyal ≤ %10`; EOD ≤ 8; S arm'larında 3x payı ≤ %10; 20x payı yalnız rapor ("kelepçe-yönetimli" etiketi, kapı değil); sabit-risk kontrolü.
- **G15** VAL dayanıklılık: (a) `ROB_FEE2` PF ≥ 1.00; (b) `ROB_DLY1` Σ R > 0; (c) `ROB_TAKER` PF ≥ 1.00 (maker dolum modelinin kuyruk/adverse-selection iyimserliğini kaldırır); (d) `ROB_WALL` **Σ R > 0** (süre kuralının karşı kutbu; gevşetme gerekçesi §3.1 R3).
- **G16** tutarlılık (v1'in "DEV ve VAL ortalama R > 0 ve havuzlu PF ≥ 1.15" kapısı G2/G8'in mantıksal sonucuydu = gereksiz; gerçek bir koşulla değiştirildi): VAL'in tam ayları zaman sırasıyla iki eşit yarıya bölünür (11 + 11 ay); **her yarıda** ≥ 100 işlem ve PF ≥ 1.00. (Boşluk kuralıyla VAL kısalırsa yarılar kısalan tam ay sayısının yarısıdır.)
- Kazanan = geçenler arasında en yüksek VAL `t_obs`. Hiçbiri → **DUR** (FINAL dokunulmaz).

**FINAL (2026-09-03 → 2026-10-08) — yalnız VETO, kazanan + B0 bir kez.**
- **B0 çapası:** B0, E16'nın 320 işlem ve PF 0.90 (iki hane) sonucunu birebir üretmelidir; üretmezse **FINAL GEÇERSİZ** ve kazanan ilan edilemez (v1'de bu durumun eylemi tanımsızdı).
- **G17** (R birimi; v1'in "`bar_max_drawdown ≤ 2500 USD`" kapısı arm'a göre farklı test üretiyordu — S5'te ≈ 50R, T'de ≈ 5R — ve "PF > PF(B0)" B0'ın PF'i zaten 0.90 bilindiğinden anlamsızdı; ikisi de atıldı): **N < 20 → SONUÇSUZ ve arm KAZANAN OLAMAZ** (v1'de "veto etmez" ifadesi sıfır kanıtla kazanan ilan edilmesine izin veriyordu). N ≥ 20: `DD_R ≤ 25` ve `PF ≥ 0.90` yoksa VETO; `PF ≥ 1.00` ve Σ R > 0 ise "FINAL-TUTARLI"; arası (0.90 ≤ PF < 1.00) "FINAL-BELİRSİZ" (veto yok, doğrulama da yok). Beklenen N: 5m ≈ 150–170, 15m ≈ 62–64, 30m ≈ 32–36 (yani 30m için veto bile istatistiksel olarak zayıftır; bu bir son akıl-sağlığı kapısıdır).

**Sonuç etiketleri:** **KAZANAN-HARNESS** = G0–G6 (DEV) + G7–G16 (VAL) + FINAL N ≥ 20 ve veto yok (TUTARLI veya BELİRSİZ; belirsizse "KAZANAN (FINAL doğrulamadı)" yazılır). FINAL N < 20 veya B0 çapası tutmadı → **ADAY-FINAL-SONUÇSUZ** (kazanan değil). Yalnız aile S/T arm'ları kazanabilir; açıklayıcı ve referanslar kazanamaz. Aksi: DEV-YOK / VAL-YOK / FINAL-VETO, her biri PF %90 GA ile ("kenar X'in altında" iddiası açık olsun). Ek etiketler (karar vermez): BAR-ORDER-BELİRSİZ (§5.1), MARJİNAL-ÜCRETE-DUYARLI (DIAG_FEE15), kelepçe-yönetimli.

**Her koşulda raporlanır:** altı aile PF'i, p, q (DEV); S-vs-T ve TF trendi (açıklayıcı); B0 ve R1 PF'leri; S5G/T5G ile kapı etkisi; Z0 brüt-vs-net satırı (Z0 brüt PF ≥ 1.05 ve net < 1 ⇒ "sinyalin ham kenarı var, ücret yiyor"; Z0 < 1.05 ⇒ "ücretten önce de kenar yok"); PF [kötümser, iyimser] aralığı (H2); yön × rejim tabloları; CLAUDE.md kural #1 satırları (DOWN havuzlu PF; boğa maddesi).

Önceden yazılmış cümle: KAZANAN-HARNESS → "Testnet adayı (kod değişikliği ve §10 koşulları sonrası), gerçek para için kanıtlanmış değil." ADAY-FINAL-SONUÇSUZ → "Onay döneminde geçti, son kontrol istatistiksel olarak yetersiz; kazanan sayılmaz." Aksi → "Büyük grafikte C'nin para kazandırdığına dair kanıt yok." 1h: her zaman "bu veriyle karara bağlanamaz."

## 9. Durdurma ve bütünlük kuralları

1. **Kilit yönetimi (v1'de kilit, DEV sonuçlarını gören kişi tarafından yeniden verilebiliyordu = tasarım körlüğünü bozan engelleyici açık; düzeltildi):**
   - `lock` alt komutu `LOCK.json` üretir, kilit commit'i **L**'yi `docs/audits/2026-10-10-prereg-tf/` altına yapar ve **L'yi oturuma ayrılmış uzak dala (`origin/main-18w3qz`) iter** (commit mesajı: `H`, `sha256(LOCK.json)`). L uzağa itilemezse kilit YOKTUR ve ilk doldurma başlamaz. `L`, `sha256(LOCK.json)` ve `H` ayrıca (i) `E17_ROOT/LOCK_REF.json`'a, (ii) ana oturumun kaydına, (iii) E17 PR açıklamasına yazılır. *(Orkestratör değişikliği: taslaktaki `e17-lock` etiketi yerine dala itilmiş commit kullanılır; oturum yalnız kendine ayrılmış dala itebilir. Koruma eşdeğerdir: GitHub'a itilmiş commit sha'sı değiştirilemez ve PR referanslarında kalır.)*
   - Her alt komut doğrulamayı çalışma kopyasından değil **`git show <L>:<yol>/LOCK.json`** içeriğinden yapar (L, `LOCK_REF.json`'dan okunur ve sha256'sı oradaki kayıtla eşleşmelidir); çalışma kopyası farklıysa exit 3. Ağ varsa `git fetch origin <L>` ile L'nin uzakta kayıtlı olduğu doğrulanır (dal sonradan birleşip sıfırlansa bile PR referansından erişilebilir).
   - Dürüstlük notu: analizci kendi dosya hash'ini kendisi doğruluyor; bu kazayı yakalar, kötü niyetli değiştirmeyi değil. Gerçek güvence itilmiş etiket ve dış kayıtlardır.
   - Ledger'a ilk `dev` koşusu girdikten sonra `lock` devre dışıdır (exit 4). S1–S3 duman testi başarısızlığı için (hâlâ DEV sonucu yokken) yalnız altyapı düzeltmesiyle yeni kilit commit'i L2 serbesttir (aynı usulle itilir, `LOCK_REF.json` geçmişi tutar); eski L silinmez, fark listesi yazılır.
   - **DEV sonuçları varken bulunan hata** (harness/analizci/betik): sessiz düzeltme YOK ve "tüm DEV'i yeniden koştur" da YOK (bu bir yeniden-yazı mekanizmasıdır). Usul: ayrı, itilmiş yama commit'i (`LOCK_REF.json`'a `patch-N` olarak kaydedilir); DEV sonucuna bakılmadan yazılmış, hatayı ve düzeltmeyi gösteren **diferansiyel test**; hem düzeltilmemiş hem düzeltilmiş sürümün kararı `DECISION.md`'de iki sütun olarak raporlanır; **daha muhafazakâr olan bağlayıcıdır**. Eşik, kapı, pencere, tohum, varyant değişikliği hiçbir koşulda "hata düzeltmesi" sayılmaz.
2. DEV'de geçen yok → DUR; VAL'i indirme, FINAL'e dokunma. VAL geçmezse DUR; FINAL'e dokunma.
3. VAL/FINAL verisi üzerinde zincir dışı elle backtest YOK; önbellek dosyaları ilgili aşamaya kadar var olmaz.
4. Sonuçlar yalnız `DECISION.md`/`results.csv` üzerinden okunur; DEV tamamlanana (= `DEV_DECISION.json` yazılana) kadar ham rapor/log bakışı yok (mühür usulü §5.2). *(v1'de "lock öncesi" yazıyordu; lock zaten DEV'den öncedir, ifade anlamsızdı.)*
5. **VAL arşiv boşluğu kuralı (v1'de "yeniden dene" tek seçenekti; kalıcı arşiv deliği geçici hata değildir):** VAL doldurması bir sembol/TF'de boşluk (`ArchiveGapError`/doğrulama hatası) bulursa önce 1 kez yeniden denenir (altyapı). Süren boşlukta VAL başlangıcı **tüm semboller için ortak olarak ay adımıyla ileri** kaydırılır (boşluğu dışarıda bırakan ilk takvim ayı başı; en çok 6 ay), yeni başlangıç `VAL_WINDOW_CUT.json`'a yazılır, tüm VAL doldurmaları o pencereyle yeniden yapılır ve M, G11 ve G16 yarıları otomatik güncellenir. Kalan VAL < 365 gün ise **VAL = ALTYAPI-HATASI**, "VAL-YAPILAMADI" etiketi, FINAL dokunulmaz. Sembol/TF ikamesi YOK. Bu karar yalnız veri varlığına bağlıdır, performansa değil.
6. Ardışık koşu; hız sınırı atlatma yok; canlı uç nokta/sunucu erişimi yok (yalnız arşiv).
7. Hiçbir kazanan sonucu görmeden "ince ayar" yok; yeni fikir = E18, yeni veri.
8. **Geçersizlik eylemleri (v1'de tanımsızdı):** G0/G1'i geçemeyen arm'ın DEV p'si := 1.0 (m = 6 kalır); sabit-risk kontrolü başarısız arm GEÇERSİZ (G0); FINAL B0 çapası tutmazsa FINAL GEÇERSİZ.

## 10. Kazanırsa ne gerekir (ve ne gerekmez)

KAZANAN-HARNESS yalnız "mühendisliğe uygun" demektir. Canlıdan (testnet dahil) ÖNCE sırayla:
1. **Önce mevcut canlı veriyi oku (düzeltildi: v1 D21'in kapıyı "reddettiğini" yazıyordu; `docs/DECISIONS.md:167`: bayat-mum kapısı "EKLENMEDİ → önce gözlem (`entry.candle_age_sec` etiketi)" = ERTELEME):** forensics'e zaten yazılan `entry.candle_age_sec` (`engine.py:3203-3216`) dağılımı canlı testnet DB'sinden çıkarılır; kapı kararı bu kanıta dayanır.
2. **Karar yolu kod değişikliği** (CLAUDE.md kural 1–2: DECISIONS kaydı + P1 parite): canlı motor aynı kapanmış barı her taramada (60 sn: 5m'de ≈ 5, 15m'de ≈ 15, 30m'de ≈ 30 kez) yeniden değerlendirir ve maker emir GÜNCEL defterden verilir; harness ise yaş 0'da sinyal kapanışından. Çözüm: **iç C taraması** için sembol başına "son değerlendirilen giriş-mumu `close_time`" notu veya `SCALPER_ENTRY_MAX_CANDLE_AGE_SEC` (varsayılan 0 = kapalı, canlı parite değişmez). Yerleşim kısıtları: (a) `_scan_tick` (`engine.py` ~2034) içinde, `_evaluate_symbol` çağrısından önce kontrol, ama not **yalnız başarılı bir değerlendirmeden SONRA** yazılır (`MarketDataUnavailable` geçici hatası sinyali kalıcı düşürmesin); (b) `_evaluate_symbol` İÇİNE konmaz: TV dış yolu onu mum ortasında doğrudan çağırır (~4374/4379) ve bastırılırdı; (c) `_evaluate_symbol` her turda `_regimes`, `_counterfactual_resolve`, yapı snapshot'ı gibi bookkeeping de yapar (`engine.py` 2189–2232); bunların bar başına bir kez mi yoksa her turda mı çalışacağı kod değişikliğinde açıkça yazılır.
3. **TF tüketicileri (v1 yalnız TF tablolarını anıyordu):** `tv_confluence` 420 sn penceresi, D27 karşı-olgu ufku (`COUNTERFACTUAL_CANDLE_COUNT = 150`, `engine.py:4420`: 5m'de 12.5 sa, 30m'de 75 sa), AI gate bağlamı, maker zaman aşımı TF tabloları (dört kopya; haritalanmamış TF 300 sn'ye düşer), `data._TTL_BY_INTERVAL`, `exits._update_trailing` `since_index` yedeği (`exits.py` ~988–994: canlıda sinyal mumundan sonraki ilk mum, harness dolum barı `entry_idx`; maker dolum 3 bar geç olabileceğinden 1h arm'da 2 barlık high farkı), tek paylaşılan TF tablosu + rol-sırası doğrulayıcısı. BG kapılarını kapatmak canlı bot için ayrı bir risk kararıdır ve **gömülü takipçi (D20b) aynı süreçte** olduğundan onu da etkiler (`/risk-event` iki motoru da kapsar).
4. **Seçilen TF'de parite testleri** (bugün yalnız 1m/5m): `tests/test_entry_gates.py` (~1250), `tests/test_market_gate.py` (~863), `tests/test_structure.py` (~509) TF'ye parametrelenir; aynı-mum yeniden değerlendirme dedupe'u için motor testleri (`_run_engine` yanında); maker zaman aşımı 15m/30m (`tests/test_scalper_setups.py` ~1374); trailing yedeği (`tests/test_trailing_market_exit.py`); golden aynı.
5. **CLAUDE.md yasak #1 tam testi:** ayı rejimi PF ≥ 1.1 G10(b)'de kapı olarak yapıldı; **boğa maddesi** ("boğada PnL kaybı ≤ %20") için: DEV'de UP etiketli aylarda kazananın toplam USD PnL'i (aynı sanal hesap ve marj kuralı) ≥ 0.8 × B0'ın aynı aylardaki USD PnL'i (B0'ınki ≤ 0 ise madde boş) hesabı `DECISION.md`'de raporlanır; bu rapor testnet öncesi koşuldur, 3-rejim backtest şartı böylece DEV+VAL'de sağlanmış olur.
6. **Yapılandırma ve boyutlandırma:** BG kapılarının kapatılması + S geometrisi + TB süre kuralı bir `.env` değişikliğidir (CLAUDE.md: `backups/env.bak-<tarih>-<etiket>` yedeği), ardından `RESTART_LABEL=<etiket> scripts/restart_safe.sh testnet`. Hesap boyutlandırması (`MAX_MARGIN_PCT`) bir risk parametresidir ve bu deneyde DEĞİŞMEDİ; USD DD'si (B0 bar-bazlı DD'si E16 pencerelerinde 1.4–9.5k USD/21 gün = sanal 10 000'in %14–95'i) testnet öncesi ayrıca değerlendirilir.
7. **Testnet ≥ 5 gün** (kural 1) — ≈ 2–4 giriş/gün (5m'de ≈ 35 işlem) istatistik değil MEKANİK doğrular: aynı bara çift giriş yok, her girişte `candle_age_sec` ≤ kapı, her dolumda koruyucu emir, aynı günlerin mainnet-mum harness tekrarı canlı girişlerin ≥ %95'ini (sembol, yön, bar) üretir. **Ek (H2'nin canlı karşılığı):** kaç TP1 dolumunun BE stopuna AYNI barda çarptığı kaydedilir ve harness PF aralığıyla [kötümser, iyimser] karşılaştırılır. Öldürme kriteri başlamadan yazılır. Hakem: `/scalper/forensics/summary` + karşı-olgu defteri.
8. Deploy kullanıcının Mac'inden (`scripts/deploy.sh awa`; bulut awa'ya erişemez). **Mainnet bu deneyin kapsamı dışıdır.**

**Kazanmazsa:** `docs/EXPERIMENTS.md` E17 (artifacts, `DECISION.md`, 4 haneli PF'li `results.csv`), reddedilenler tablosuna "C giriş TF 5m/15m/30m (S: ATR-ölçekli, T: sunucu geometrisi)" sayılarla eklenir; 1h'nin test edilemediği açıkça yazılır; VAL (hiç indirilmediyse) ve FINAL harcanmamış kalır; sonraki soru (sinyal kaynağı veya daha geniş evren, ör. TOP_N=20 ≈ 2.5× işlem; ya da rastgele-giriş null'u) adlandırılır, yeniden ayar yapılmaz.

## 11. Güç ve işlem sayısı tahminleri (model tabanlı; §7.4 varsayımları geçerlidir)

Sayımlar: CAL (92 gün, BG kapıları, bar-bazlı süre, maker) × 365/92 (DEV) ve × 687/92 (VAL) ve × 35/92 (FINAL); Poisson belirsizliği ±%10 (S30: 84 ± 9) + 2026-bahar / 2025 / 2023 oynaklık farkı. G1 (N ≥ 200) ve G7 (N ≥ 250) güvencedir.

**Uçtan-uca ortak geçme olasılığı** (DEV G0–G6 **ve** VAL G7–G16, k = 2, FEE2 kapısı, B = 599, 250 simülasyon/hücre; hücre = A / **B** / C senaryosu): kaynak `stat2/out3_fine.txt`, `stat2/res3_*.json`.

| Arm | DEV N | VAL N | FINAL N | gerçek net PF 1.15 (A/**B**/C) | 1.3 (A/**B**/C) | 1.5 (A/**B**/C) | %50 güçle tespit edilen PF (B) |
|---|---|---|---|---|---|---|---|
| S5 | 1760 | 3300 | ≈169 | 0.00 / **0.00** / 0.00 | 0.00 / **0.00** / 0.06 | 0.48 / **0.64** / 0.62 | ≈ 1.47 |
| T5 | 1560 | 2940 | ≈150 | 0.02 / **0.07** / 0.04 | 0.42 / **0.26** / 0.10 | 1.00 / **0.72** / 0.29 | ≈ 1.40 |
| S15 | 640 | 1210 | ≈62 | 0.06 / **0.08** / 0.06 | 0.63 / **0.55** / 0.30 | 0.99 / **0.95** / 0.74 | ≈ 1.29 |
| T15 | 670 | 1260 | ≈64 | 0.10 / **0.11** / 0.06 | 0.58 / **0.41** / 0.17 | 0.92 / **0.74** / 0.40 | ≈ 1.35 |
| S30 | 335 | 630 | ≈32 | 0.06 / **0.05** / 0.04 | 0.52 / **0.49** / 0.33 | 0.90 / **0.84** / 0.65 | ≈ 1.30 |
| T30 | 375 | 700 | ≈36 | 0.09 / **0.10** / 0.10 | 0.36 / **0.29** / 0.17 | 0.82 / **0.70** / 0.44 | ≈ 1.41 |
| S60 / T60 (açıklayıcı) | 140–150 | 260–275 | ≈ 14 | YETERSİZ GÜÇ: PF ≈ 1.9'un altı tespit edilemez | | | |

- **Uygunluk kuralı (kilitli, §3.2):** aile üyesi ⇔ (B, net PF 1.5) ortak geçme ≥ 0.5. Tüm altı arm uygundur (en düşük 0.64 S5, 0.70 T30). Sonuç (C senaryosu, ağır aylık heterojenlik): T arm'larının gücü belirgin düşer (T5 0.29, T15 0.40, T30 0.44 @ PF 1.5).
- **Dürüst okuma:** (i) 5m'de ücret yükü (0.17R) ve `FEE2`/`TAKER` kapıları yüzünden arm'ın kazanması için **net PF ≈ 1.45+** gerekir; 1.2–1.3 civarı bir kenar 5m'de görünmez ve "kazanan yok" der. (ii) 15m/30m için eşik ≈ 1.3–1.4. (iii) Gerçek PF = 1.0'da ortak geçiş ≈ 0 (§7.4). (iv) v1'in tablosu (0.73 @ 1.2 S5) yalnız 4 kapıyı içeriyordu ve S5 için ≈ 0.00 gerçeğe karşılık geliyordu; kaldırıldı.
- Bir "yok" sonucunda arm başına PF GA üst sınırı yazılır: "5m ≈ 1.1'den iyi olamaz" denebilir, "30m'de kenar yok" denemez.
- Sunucu kapılı sayılarla (işlem/yıl: S5G ≈ 670, T5G ≈ 610, 15m kapılı ≈ 150, 30m kapılı ≈ 80) yalnız 5m anlamlıdır; bu yüzden kapılı referanslar yalnız 5m.
- FINAL'de (35 gün) beklenen N: 5m ≈ 150–170, 15m ≈ 62–64, 30m ≈ 32–36. (v1'in "≈ 7–170" aralığı hatalıydı; alt uç FINAL'e ulaşabilecek arm'lar için geçerli değildir.)
- Bilinen kısıt: DLY1 ve WALL güç simülasyonunda modellenmemiş, ceza olarak eklenmiştir; G14/G0 modellenmemiştir (yalnız ek elenme getirir).

## 12. Bilinen sınırlar (önceden ilan)

1. **Maker modeli:** sinyal barı kapanışında limit, sonraki 3 barın low/high'ı dokunursa dolum; bar önceki kapanışta açıldığından dolum ≈ %100 (CAL: 446'da 2 kaçak), kuyruk ve adverse selection yok; ayrıca dolum barının tamamı (dokunuş öncesi dahil) kullanılır ve dolum barında SL/TP1 de işlenir (CAL'de işlemlerin %5.6–25.5'i dolum barında kapanır, §5.1 H2 tablosu). Bu yüzden `ROB_TAKER` (G6c/G15c) ve `DIAG_SBO`.
2. **Bar çözünürlüğü ve sıra:** bar içi yol yok. Kötümser yönde: SL ve TP1 aynı barda ise SL önce (CAL'de ≈ 0 olay), **TP1 sonrası BE/trail stopu aynı barda değerlendirilir** (TP1 dolumlarının %20–79'u; canlıda BE stopu TP1 dolumu görüldükten sonra konur). İyimser yönde: çıkışlar tam seviyede dolar, boşluk kayması yok (şok barlarda, özellikle 1h), maker dolumu tüm bar; `--fee-stress` yalnız ücret + giriş kaymasını 2×'ler. REAPER/trailing/STALE bar kapanışında (canlıda ≈ 2 sn tick); aşım 1/5/15/30/60 dk. **Net işaret BİLİNMİYOR; E16'nın B0 boşluğu (sim 0.90 vs canlı 0.47) ≥ 15m'ye ekstrapole edilemez.** H2 yalnız sınırlar; kapılar kötümser sırada kalır, PF aralığı basılır. (v1 "harness kazancı üst sınırdır" diyordu; bu doğrulanmamış bir iddiaydı, kaldırıldı.)
3. **Modellenmeyenler:** fonlama (medyan tutuşta küçük: 30m'de 6.5 sa ≈ 0.01R; ama 240 saate varan tutuşlarda ve 2023–2024 boğa yıllarında long taraf lehine/aleyhine yönlü; aşağı yönlü risk), likidasyon, günlük zarar kesici (canlı %1), bekleyen-maker slot doluluğu (3 × TF), TV sinyal/çıkışları, sanal sabit 10 000 bakiye.
4. **Canlı farklar:** harness yaş 0 vs canlı her taramada yeniden değerlendirme (`ROB_DLY1` bunu YOKLAMAZ; yalnız dolum penceresini kaydırır); canlı maker fiyatı güncel defter; mainnet kline vs testnet defteri; trailing `since_index` yedeği: canlıda sinyal mumundan sonraki ilk mum (`exits.py` 988–994), harness dolum barı (`entry_idx`) — maker 3 bar geç dolabilir, 1h arm'da 2 bar fark.
5. Geometri TF başına tek noktadır (k=2, r=1); başka noktalar sınanmadı. S sabitleri CAL'den (35–444 sinyal) formülle türedi ve gürültülüdür; **S5 = 5 bıçak sırtı bir yuvarlamadır** (4 de geçerliydi, §3.1 R4); formül hiçbir sonucu görmeden donduruldu.
6. BG kapıları 2026 1m verisinde ayarlanmış kapıları kaldırır; E17 sonucu "çıplak C + denge filtresi" içindir, canlı kapı paketi için değil. S5G/T5G'nin rejim filtresi 1h EMA50/200'dür (sunucudaki 15m değil); bu referanslar yalnız bilgi.
7. BH PRDS varsayar; S/T çiftleri neredeyse aynı sinyaller. Studentize bootstrap aylık kenar heterojenliğinde liberaldir (b=7: nominal 5% → 11–15%); G9'un çift p şartı ve kapı yığını bunu azaltır ama p-değerleri tek başına nominal değildir (§7.2, §7.4).
8. DEV E16'ya kör değil (365 günün 168'i E16'nın gördüğü pencere); C'nin sabitleri (RSI 30/70, diverjans) 2026 verisinde seçildi. VAL (2023-01 → 2024-11-18) gerçekten dokunulmamış ve 2023 düşük oynaklık / 11 UP ay → kısa-taraf veya DOWN zayıflığı G10 ile denetlenir, garanti edilmez. 2023 sakin bir yılsa S arm'larında 20x payı yükselir (VAL'de rapor).
9. EOD: koşu başına tek kesme; N_EOD ≤ 8 (G0/G14).
10. FINAL istatistiksel çıkarım için çok az işlem içerir (veto); 1h bu evrenle test edilemez.
11. 5m arm'ları yapısal olarak ücret ve BE maliyetine bağlı (BE tamponu sabit %0.05; medyan stop %0.42, ücret 0.17R); bu test edilen şeyin parçasıdır, artefakt değil, ve 5m'in kazanması için gereken net PF'yi ≈ 1.45'e çıkarır (§11).
12. **Tasarım PnL-körü değildir (düzeltme; v1 "CAL'de PnL basılmadı" diye yetinmişti).** (a) CAL ölçümlerinde (`stat/`, `stat2/`) PnL basılmadı ama CAL JSON dosyalarında PnL vardır (dürüstlük sistemi; bu betiklere PnL çıktısı ekleyen olmamalı; arşivlenen raporlardan E16'daki gibi `provenance.scalper_config` çıkarılır). (b) Drafter'lar ön-taslak varyantlarını klasik pencerelerde (AYI 2026-01-23→02-13, YATAY 2026-07-01→07-21, BOĞA 2026-08-07→08-21; DEV/VAL/FINAL DIŞI) koştu ve sonuçları gördü: `SP/runs/` 60 rapor, n ≥ 10 olan 36'nın 3'ü PF > 1: R0 (B0 AYI parite, PF 2.03, 149 işlem), `m_A15_YATAY` (10 işlem, PF 1.47), `m_A5b_YATAY` (20 işlem, PF 1.46); kalan 33 ön-taslak A/B/P/Q 5m/15m/60m varyantının 31'i PF < 1 (örn. `m_A5_*` 25–26 işlem PF 0.61–0.67, `m_B5_*` PF 0.61–0.84, `m_A15_BOGA` 10 işlem 0.47); aynı pencerelerde B0 PF 2.03 / 1.50 / 1.33 (E16 `results.csv`). `SP/lens-minimal/t_runs3/T_B15_{Z0,dly,fee}` (BOĞA, 12 işlem): PF 0.817 / 0.593 / 0.249 (sıfır-ücret, gecikme, ücret-stres). BG kapı paketi seçimi CAL işlem sayılarına, S sabitleri CAL ATR'ye dayanır; bu sonuçlar hiçbir eşiği değiştirmedi ama yazarlar onları GÖRDÜ. **Ön-kayıtlı beklenti:** 5m/15m klasik-benzeri varyantlar 1m'in kazandığı yerde kaybetti; E17'nin "kazanan yok" çıkması en olası sonuçtur.
13. Rejim etiketleri BTC-tabanlı ve aylık (kaba); şok alt-yapısı modellenmez.
14. **Evren yanlılığı:** 8 sembol 2026'da canlı olanlara göre seçildi; 2023 VAL dönemine uygulandığında hayatta kalma yanlılığı (düşen ve delist edilen coinler yok) dip-alım (long) yönü lehine çarpıtabilir. Sembol ikamesi yok; kabul edilir ve raporlanır.
15. **Güç simülasyonu model tabanlıdır:** getiri şekli geometriden varsayıldı, DLY1/WALL cezaları varsayım, tasarım etkisi senaryolara bağlı; gerçek veri dağılımları farklı olabilir. Sonuç eşiklerini değil, "hangi arm'ın hangi PF'te görünür olduğu"nun mertebesini verir.
16. `ROB_DLY1` yalnız dolum penceresini kaydırır; canlı aynı-bar yeniden değerlendirme farkını yoklamaz (§10).

## 13. İzlenebilirlik

**Eleştirmen engelleri (understand.json):**

| Engel | Nerede / nasıl |
|---|---|
| 1 Rol-anahtarı çakışması | §2.1: giriş≠bağlam/rejim zorunlu, koşucu+analizci doğrular; bağlam==rejim iki ayrı 4h anahtarı (+100/+250) zararsız; H3 testi beş üçlüde |
| 2 Karıştırılmış geometri | §3: her TF'de T (yalnız zamanlama) ve S (ölçekli) ayrı arm; 20x/3x payları G0/G14 (T30 %85, T60 %68 düzeltmesi dahil) |
| 3 REAPER / cooldown ölçekleme | §3.1 R3 tek kural TB (bar sayısı) + G15d karşı kutup (Σ R > 0) |
| 4 Sessiz stop-band düşüşleri | §5.1 H1 (beş sayaç, "ham sinyal" tanımlı), G0/G14; S arm'larında yapısal olarak imkânsız (R4 bant aritmetiği) |
| 5 Canlı aynı-bar yeniden değerlendirme / yaş kapısı yok | §10 madde 1–3 (D21 ertelemesi, yerleşim kısıtları, TF tüketicileri); `ROB_DLY1`'in YOKLAMADIĞI açıkça yazıldı (§3.2, §12.16) |
| 6 15m/1h parite testleri yok | §10 madde 4 (canlı kullanım önkoşulu); harness tarafı H3 |
| 7 Set arası ısınma çakışması | §2 tablo, VAL'in 2024-11-18'de bitmesi; `lock` analitik, `check-run` gerçek en erken barı doğrular |
| 8 Veri eksik | §4 (envanter, istek sayıları, hata=dur, VAL boşluk kuralı §9.5) |
| 9 Giriş modu | tüm arm'larda maker; taker dayanıklılık G6c/G15c |
| 10 Çoklu test | §7.3 |

**E16 analizci zayıflıkları:** merkezlenmemiş yüzdelik bootstrap, 1 günlük blok → §7.2 (null-merkezli, studentize, b=7 ve b=28, MC hata kuralı). Kilit uyuşmazlığı yalnız rapor → §5.3/§9.1 (sıkı-hata, exit 3, `final` dahil, `git show <L>:` referansı). `--n-boot/--seed` bayrakları → §5.3 sabitler, parser testi. Betikler hatayı yutuyor, bayat JSON → §5.2. Belirlenimcilik duman testi elle → §5.2 S2 otomatik (CAL penceresi). PF 2 hane → §5.3 4 hane + SINIRDA. Testler CI dışı → `tests/` altında, hızlı (`boot_n`).

**Düşmanca inceleme bulguları (v1 → v2):** `leakage-stats` (LS), `mechanics` (ME), `feasibility-honesty` (FH).

| Bulgu | Karar | Nerede |
|---|---|---|
| LS-A1 kilit yeniden verilebilir (BLOCKER) | uygulandı | §9.1: itilmiş kilit commit'i, `git show` referansı, DEV sonrası `lock` kapalı, yama usulü |
| LS-A2 ilerleme kuralı belirsiz + 5m'e yatkın (BLOCKER) | uygulandı (sapma: en çok 2, TF başına 1) | §8 DEV; G6 tüm G0–G5 geçenlere; `DEV_DECISION.json` tek kez |
| LS-A3 / ME-M4 / FH-A2,A3 duman testi DEV'e dokunuyor, doldurma envanteri eksik | uygulandı | §4.3 P1b, §5.2 (S2/S3 CAL, S1 dürüstlük notu) |
| LS-A4 "tüm DEV'i yeniden koş" yeniden-yazı | uygulandı | §9.1 yama usulü, iki karar, muhafazakâr bağlayıcı |
| LS-A5 geçersiz arm / B0 çapası eylemi | uygulandı | §9.8, §8 FINAL |
| LS-A6 PF görünürlüğü | uygulandı (mühür usulü, makine zorlaması değil) | §5.2 |
| LS-A7 VAL BTC 1d lock öncesi | uygulandı (ifade düzeltildi) | §2 |
| LS-A8 / FH-D6 FINAL N ve N<20 | uygulandı | §8 G17, §11 |
| LS-B1 PnL-körlük abartısı | uygulandı | §7.3, §12.12 |
| LS-B2 / ME-m9 / FH-B4 kilit kapsamı, H blob'ları | uygulandı | §5.3 |
| LS-B3 arşiv ayı yanlış | uygulandı | §4.7 |
| LS-B4 evren yanlılığı | uygulandı | §12.14 |
| LS-C1 güç tablosu desteksiz | uygulandı (yeni uçtan-uca simülasyon) | §7.4, §11 |
| LS-C2 G15 FEE2/WALL ulaşılamaz | KISMEN: FEE2 çerçeve gereği korundu, WALL gevşetildi, DIAG_FEE15 eklendi, MDE dürüstçe yazıldı | §8 G15, §11 (bkz. §14 gerekçe) |
| LS-C3 G12 30m | uygulandı (pay sınırları gevşetildi, kaldırma testleri) | §8 G12 |
| LS-C4 / ME-M3 / FH-B7 G14 20x payı | uygulandı (ME-M3 çözümü: DEV'de kapı, VAL'de rapor); LS-C4'ün VAL ön-uçuşu reddedildi | §8 G0/G14 |
| LS-D1 b=7 liberal | uygulandı (b=28 ikincil + çift p) | §7.2, G9 |
| LS-D2 MC hatası | uygulandı | §7.2 |
| LS-D3 / FH-A5 kanıt yolları | uygulandı | Ek B, §7.2, §7.4 |
| LS-E1 G16 gereksiz | uygulandı (yarı-dönem PF) | §8 G16 |
| LS-E2 / ME-m1 / FH-D5 G17 USD | uygulandı | §8 G17 |
| LS-E3 / FH-C1 yasak #1 | uygulandı (G10b + §10.5) | §8 G10, §10 |
| LS-E4 G13 Calmar 5m'de anlamsız | uygulandı (mutlak DD kapısı kalktı) | §8 G13 |
| LS-F1,F2 etiket ayrıntıları, yön tabloları | uygulandı | §2.3, §5.3 |
| LS-G ROB_DLY1 sözü, fonlama | uygulandı | §3.2, §12 |
| ME-M1 S3 / T 20x iddiası | uygulandı | §3.1 R4, §5.2 S3 |
| ME-M2 / FH-B2 yama dosyaları çelişkili | uygulandı (yamalar atıldı) | §5.1 |
| ME-M5 VAL boşluk kuralı | uygulandı | §9.5 |
| ME-m2,m3,m7,m8 tanımlar | uygulandı | §5.1, §5.2, §3.1 R5 |
| ME-m4 S5 bıçak sırtı | uygulandı | §3.1 R4 |
| ME-m5 ROB_WALL | uygulandı (Σ R > 0) | §3.1 R3, G15d |
| ME-m6 `env -i` listesi | uygulandı | §5.2 |
| ME-m10 S5G/T5G etiketi | uygulandı | §3.1 R2 |
| ME-m11 tarih notları | uygulandı (VAL bitişi 11-18 korundu) | §2, §2.1 |
| ME-m12 chandelier since_index | uygulandı | §10.3, §12.4 |
| FH-A1 istek sayıları, paralellik | uygulandı | §4.4–4.5 |
| FH-A4 429 argümanı bayat | uygulandı | §5.2 |
| FH-A5 geçici dizin | uygulandı | §4.2 |
| FH-B1 H2 yanlış nesneyi sayıyor (BLOCKER) | uygulandı (`--diagnostics`, `--same-bar-order`, `DIAG_SBO`, PF aralığı) | §5.1 H2, §12.2 |
| FH-B3 sayaç anahtar adları | uygulandı | §5.1 H1 |
| FH-B5 analizci/koşucu sözleşmesi %60 | uygulandı | Ek A |
| FH-B6 analizci testi yavaş | uygulandı | §5.3 |
| FH-B8 tarih tablosu | uygulandı | §2.1 |
| FH-C2 §10.1 D21 yanlış tanım, yerleşim | uygulandı | §10.1–10.2 |
| FH-C3 diğer §10 boşlukları | uygulandı | §10.3 |
| FH-C4 / FH-B1 canlı BE sırası | uygulandı | §10.7, §12.2 |
| FH-D1–D4,D7 kullanıcı metni, terimler, yapı | uygulandı (özet başa taşındı, "kâr çarpanı" → "kazançlar toplamı ÷ kayıplar toplamı", PF eşikleri tek yerde) | §0, §11 |

## 14. Taslak sentezi: çözülen anlaşmazlıklar ve çerçeveden sapmalar (v2)

- Taban: istatistik taslağı (skor 40/50); mekanik taslaktan (39) R-birimi kapıları, DEV dayanıklılığı, kanıt ölçüleri; sadakat taslağından (36) 1m referansların çeyrek bölümü, harness'e işlem alanı eklememe, Z0 teşhisi, önceden yazılmış cümleler, FINAL'de B0'ın zaten görüldüğü notu.
- **Çerçeveden sapmalar (hepsi somut kusura dayanır):** (1) **Kapı paketi BG** (çerçeve #3: sunucu kapıları aynen): 15m ≈ 150, 30m ≈ 80, 1h ≈ 36 işlem/yıl → test edilemez; kapı etkisi yalnız 5m'de S5G/T5G ile ölçülür. (2) **VAL = 2023-01-01 → 2024-11-18** (çerçeve: 2024 tamamı): DEV 4h ısınması 2024-11-20'den VAL'i okur. (3) **30m, 1h'nin yerine** (kullanıcı 1h istedi): 1h ≈ 140 işlem/yıl → karar verilemez; 1h açıklayıcı. (4) **Süre düğmesi tek kural TB (bar)**, karşı kutup G15d; çerçeve "tek tutarlı kural" der — uyuldu. (5) **Ayı-rejimi kapısı DOWN havuzlu** DEV+VAL (tek başına VAL yetersiz işlem taşır).
- **Çerçeveye uyup incelemeci önerisinden ayrılınan:** `ROB_FEE2` kapı olarak kaldı (çerçeve #6 "fee x2"); incelemeci FEE1.5 veya "S5 kazanamaz" öneriyordu. Gerekçe: simülasyon (§11) FEE2 altında S5'in net PF 1.5'te %64 (B) geçtiğini gösteriyor, yani ulaşılamaz değil; yalnız yüksek eşik. Kapı kaldırılıp eşik dürüstçe (S5 ≈ 1.45+) yazıldı ve `DIAG_FEE15` etiketi eklendi.
- **İlerleme kuralının sonucu:** TF başına en çok 1 arm ve toplam 2 (LS-A2) — 30m'nin VAL'e ulaşması için 5m ve 15m'den en az birinin elenmesi gerekir (açıklanır).
- **Aile:** altı karar-verilebilir arm (S/T × 5m/15m/30m), 1h açıklayıcı; mekanik m=4 (30m yok, B15 neredeyse test edilemez), sadakat m=3 (A15 yok, T/S kontrastı eksik).
- **S sabitleri:** CAL P10 formülü (büyük örnek) → 5, 9, 13, (1h için 16); mekanik/sadakat klasik pencerelerden (n=6–76) 5, 6, 16.
- **Rol üçlüsü:** canlı oranları üreten R1 kuralı (5m→30m/1h); mekanik 5m/15m/1h.
- **p-değeri:** studentize dairesel blok b=7 ve b=28 (çift p); mekanik/sadakat ortalama-bazlı (stud. olmayan, simülasyonda daha liberal).
- **DEV BH q:** 0.20 (eleme); mekanik 0.25; sadakat 0.05. Karar VAL'de.
- **Birim:** R, harness alanı eklemeden `margin_usdt×S/100`.
- **Rejim etiket ataması:** giriş ayı (istatistik, sadakat); mekanik çıkış ayı.
- **Segmentasyon:** üçü de tek sürekli koşu; 1m referanslar çeyrek (sadakat).
- **FINAL:** üçü de yalnız veto; eşik N ≥ 20; v2: üç durumlu (VETO / BELİRSİZ / TUTARLI), PF > B0 kaldırıldı.
- Sadakat taslağı yazarının kök dizindeki `/t_runs2` ve `/t_logs2` yan etkisi (karalama çıktıları) elle silinmelidir (E17 planıyla ilgisi yok; v2 yazılırken de duruyorlardı).

### 14.1 Orkestratör onayları (2026-10-10, sonuç görülmeden)

Ana oturum (orkestratör) aşağıdaki beş çerçeve sapmasını, gerekçeleri okunarak ve hiçbir DEV/VAL/FINAL sonucu yokken **onaylar**:
1. Aile BG kapı paketiyle (rejim filtresi, market gate, hafta sonu bloğu kapalı) koşar; kapı etkisi yalnız 5m'de S5G/T5G ile ölçülür. Gerekçe: kapılarla 15m/30m'de yılda ≈150/80 işlem → test edilemez.
2. VAL = 2023-01-01 → 2024-11-18 (DEV 4h ısınması 2024-11-20 08:00'den okur).
3. 1h yerine 30m aile üyesi; 1h yalnız açıklayıcı (≈140 işlem/yıl, PF ≈1.9 altı görünmez).
4. CLAUDE.md yasak #1 ayı maddesi G10(b) kapısı.
5. `ROB_FEE2` kapı olarak kalır.
Ek orkestratör değişikliği: kilit, etiket yerine `origin/main-18w3qz`'ye itilmiş commit L ile sabitlenir (§9.1).

## 15. Kilit öncesi doğrulanacak açık noktalar (kodda/ölçümle doğrulanamadı)

1. 8 işçilik paralel doldurma güvenliği (3'e kadar denendi); P0 provası sonuçlanmalı. 15m'nin günlük zip kullandığı ÖLÇÜLDÜ (çözüldü).
2. **H1/H2/H3 henüz yazılmadı**; `--diagnostics`, `--same-bar-order` bayrakları yok; golden'ın birebir kalması ve her dal için birim test yazılacak (§5.1). Atılan yamalar yeniden kullanılmayacak.
3. `ENTRY_BLOCK_WEEKDAYS_UTC=` boş değerin `env -i` ile aktarımı koşucu testinde doğrulanmalı (CAL'de ve `verify_variants2.py`'de Settings düzeyinde çalıştı).
4. 1m tam-yıl belleği ölçülmedi (bu yüzden çeyrek bölümü).
5. CAL sinyal-anı ATR P10 değerleri iki ölçümde küçük farklı (5m 0.113 / 0.121; üçüncü ölçüm 0.112); S sabitleri yeniden hesaplanmaz, sabit olarak yazılır.
6. `lock` adımının L'yi `origin/main-18w3qz`'ye itebildiği doğrulanmalı (itilemezse deney başlamaz).
7. VAL arşivinde kalıcı boşluk olup olmadığı bilinmiyor (HEAD örneklemesi temiz); §9.5 kuralı hazır.
8. Güç simülasyonunun DLY1/WALL cezaları ve getiri şekli varsayımdır (§12.15).
9. `/t_runs2` ve `/t_logs2` kök dizin artıkları elle silinmeli.

## Ek A. Dosya ve şema sözleşmesi

**A.1 Yerleşim.** (i) Depoda (commit H): `scripts/e17/analyze_e17.py`, `scripts/e17/lib17.sh`, `scripts/e17/run_smoke.sh`, `run_dev.sh`, `run_val.sh`, `run_final.sh`, `scripts/e17/verify_variants.py`, `tests/test_e17_harness.py`, `tests/test_e17_analyzer.py`, `src/strategies/scalper/backtest.py` (H1–H2). (ii) Depoda (kilit commit'i L): `docs/audits/2026-10-10-prereg-tf/{PLAN.md, variants.json, labels_frozen.json, cal_constants.json, archive_probe.json, LOCK.json, evidence/*}`. (iii) Depo dışı `E17_ROOT` (varsayılan `$HOME/e17_root`): `cache/`, `dl/`, `sealed/runs/<etiket>.json` (0400), `sealed/logs/<etiket>.log`, `ledger.jsonl`, `MANIFEST.data.json`, `MANIFEST.runs.json`, `DEV_SCREEN.json`, `DEV_DECISION.json`, `DIAG.json`, `VAL_WINDOW_CUT.json` (varsa), `VAL_DECISION.json`, `FINAL.json`, `DECISION.md`, `results.csv`, `CHAIN.log`. Aşama bittikçe `ledger.jsonl`, `MANIFEST.*`, `*_DECISION.json`, `FINAL.json`, `DECISION.md`, `results.csv` oturumun dalına (`main-18w3qz`) `docs/audits/2026-10-10-prereg-tf/run/` altına commit'lenir (ham rapor ve önbellek hariç; ham raporlar E16'daki gibi `provenance.scalper_config` çıkarılarak `artifacts.tgz` yapılır).

**A.2 `variants.json` (şema `e17-variants/2`).** Anahtarlar: `snapshot{policy,sha256}`, `common_cli` (her koşuda: `--strategies C --symbols <sabit sıra> --diagnostics`), `constants` (S sabitleri, TB tablosu, BG), `family[6]`, `descriptive[2]`, `references[4]` (S5G, T5G, B0, R1), `overlays[7]` (her biri `env{}` + `cli[]` + `gate`), `overlay_applicability`, `sets`, `run_matrix_counts`. Her varyantın `env`'i **çözülmüş tam override'tır** (koşu anında bileşim yok); kaplama = arm env'i üstüne kaplama env'i + kaplama cli'sı. Koşu etiketi: `<SET>_<varyant>[_<kaplama>][_Q<n>]`, ör. `DEV_S15`, `DEV_S15_ROB_FEE2`, `DEV_B0_Q1`, `VAL_S30_ROB_WALL`, `FINAL_B0`, `SMOKE_S15_h0`.

**A.3 Alt komutlar** (`analyze_e17.py <komut> [--root DIR]`; başka bayrak yok): `freeze` (BTC 1d'den `labels_frozen.json`; DEV 5/4/3 ve VAL 11/6/5 doğrular), `lock` (§9.1), `verify-lock`, `manifest <set>`, `smoke` (S1/S2/S3 karşılaştırmaları; yalnız PASS/FAIL), `check-run <etiket>`, `dev-screen`, `dev-decide`, `diag`, `val`, `final`, `decide`. Çıkış kodları: 0 başarılı; 2 kullanım hatası; 3 kilit uyuşmazlığı (dosya yazılmaz); 4 aşama ön koşulu ihlali (sıra, ikinci yazım, `lock` kapalı); 5 koşu/veri bütünlüğü. **Kapı başarısızlığı çıkış kodu değildir** (sonuç dosyada).

**A.4 Şemalar (alan listeleri).**
- `LOCK.json`: `h_commit`, `h_tree`, `l_note`, `python_version`, `requirements_sha256`, `files{yol→sha256}` (PLAN.md, variants.json, labels_frozen.json, cal_constants.json, archive_probe.json, evidence/*), `code_blobs{yol→sha256 of git show H:yol}`, `snapshot_sha256`, `thresholds_sha256` (analizci `THRESH` serileştirmesi), `earliest_bar_check{üçlü→ISO}`, `created_utc`.
- `MANIFEST.data.json`: `{cache_dosyası: {sha256, bytes, symbol, interval, n_candles, first_open_time, last_open_time, set, fill_phase}}`. `MANIFEST.runs.json`: `ledger.jsonl` satırlarından türetilir: `{etiket, utc, saniye, sha256, git_sha}`.
- `labels_frozen.json`: `{ay("YYYY-MM"): {ret_pct, label, shock}}` + `counts` + `rule`.
- `DEV_SCREEN.json`: `{arm: {G0..G5: {value, threshold, pass, borderline}, p7, t_obs, n, validity{...}}}`. `DEV_DECISION.json`: `{g6{arm→{fee2,dly1,taker}}, ranked[arm,t_obs], advanced[arm], k, bh{p,q}}`; **bir kez yazılır**.
- `VAL_DECISION.json`: `{arm→{G7..G16 satırları, p7, p28, winner}}`; `FINAL.json`: `{b0_anchor{trades,pf,ok}, arm{G17 durumu, n, pf, dd_r}}`. `results.csv` sütunları: `label,variant,overlay,set,quarter,trades,wr,pf,pf_pess,pf_opt,net_r,total_pnl_usd,bar_max_dd_usd,dd_r,s_arm`.

**A.5 `check-run`** (§5.2 listesi) ve **koşucu konsol politikası** (yalnız `BT OK/FAIL`, PnL yankısı YOK) kilitlidir. **Seçim bağları:** `t_obs` eşitliğinde büyük TF; p := 1.0 kuralları §9.8; sıralama kararlı.

## Ek B. Kanıt betikleri ve çıktıları (kilitte hash'lenir; `evidence/` altına kopyalanır)

`SP` = §başı karalama kökü. İddia → dosya:
- Uçtan-uca güç ve null yanlış-pozitif (§7.4, §11): `SP/stat2/pipe_power3.py`, `SP/stat2/out3_fine.txt`, `SP/stat2/res3_{A,B,C}_2.0_250_.json`, `SP/stat2/out3_main.txt` (FEE1.5/2.0 karşılaştırması), önceki model `SP/stat2/pipe_power2.py`, `SP/stat2/res_*_200.json`.
- G12 pay sınırı ölçümü (§8): `SP/stat2/g12probe.py`, `SP/rev_g12.py`. FEE2 gerekli PF: `SP/rev_fee2.py`.
- Bootstrap boyutu (§7.2): `SP/stat2/mc_size.py`, `mc_size_687.txt`, `mc_size_365.txt`, `SP/rev_mc.py`; bootstrap süresi `SP/e17/rev_sim/boot.py`.
- İndirme sayıları (§4): `SP/e17/rev_sim/dlcount.py`, `uniq.py`. Aynı-bar belirsizlik sayımı (§5.1 H2): `SP/e17/rev_sim/amb.py`.
- CAL ölçümleri (§3, §11): `SP/e17/stat/measure.py`, `atrsig.py`, `btc_months.py`, `m_*.json` / `mlog_*` (PnL İÇERİR, bakılmaz).
- Rejim etiketleri bağımsız hesap: `SP/rev_btc/`. Varyant üretici ve doğrulayıcı: `SP/e17/fix/gen_variants.py`, `SP/e17/fix/verify_variants2.py`. Ön-taslak PnL'li koşular (§12.12): `SP/e17/runs/`, `SP/e17/lens-minimal/t_runs3/`.
