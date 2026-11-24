# ÖN-KAYITLI DENEY PLANI (E23): momentum girişi yalnız gerçek zamanlı bir BTC şok tetikleyicisi açıkken yapılırsa komisyon ve kayma sonrası kenar üretir mi?

Tarih 2026-11-24. Dal `prereg/e23-shock-trigger` (`origin/prereg/e18-momentum` `17061f4` + `origin/main` `479f327`
birleştirmesi). Bu belge, hiçbir E23 kolunun DEV, VAL ya da FINAL sonucu görülmeden yazıldı ve §9'daki usulle kilitlenir
(sha256 + itilmiş commit). Kilitten sonra bu dosya DEĞİŞMEZ; sonradan yazılacak her şey `run/DECISION.md` ve
`docs/EXPERIMENTS.md` E23 kaydına girer. Şablon: `docs/audits/2026-11-20-prereg-e18/PLAN.md` (E18). Ölçüm modeli: D41
gerçekçi model (varsayılan).

## 0. Önce bunu oku

**Soru.** E18'de trend yönlü momentum girişi (kurulum M) 2025'in tamamında maliyet sonrası zararda çıktı (PF 0.64–0.88).
Aynı E18 raporunun betimleyici tablosunda, BTC'nin günü %3 ya da daha fazla hareketle KAPATTIĞI "şok" günlerinde altı kol
da artıdaydı (PF 1.11–2.09) ve şokla aynı yönlü işlemler PF 1.7–3.9 verdi. O ayrım günün kapanışını kullanır, yani
geleceği bilir. E23 şunu sorar: gün içinde, karar anına kadar kapanmış mumlardan hesaplanan bir tetikleyici açıkken ve
yalnız BTC hareketinin yönünde girilirse, kenar maliyetten sonra kalır mı? LONG ve SHORT ayrı raporlanır.

**Sözlük.**
- **PF**: kazançlar toplamı / kayıplar toplamı; 1.00 başabaş. Hep komisyon, kayma ve fonlama düşüldükten sonra.
- **R**: işlem başına göze alınan zarar (stop mesafesi). Kazanç ve zarar R katı olarak yazılır.
- **Tetikleyici**: BTC'nin UTC gün açılışından karar anına kadarki hareketi; eşiği aşınca o yönde giriş serbest (§3.2).
- **Şok günü**: BTCUSDT 1d mumunda |kapanış/açılış − 1| ≥ %3 olan UTC günü (E18/E20 tanımı). İleri bakışlıdır; yalnız
  betimleyicidir, kapı değildir.
- **DEV / VAL / FINAL**: eleme (2025), bağımsız onay (2023-01-01 → 2024-11-18, hiç indirilmemiş) ve yalnız veto hakkı olan
  son kontrol (2026-10-16 → 2026-11-20).
- **Kol**: önceden yazılmış tek bir (giriş kuralı, tetikleyici eşiği) bileşimi. **Kapı**: önceden yazılmış geç/kal kuralı.

**Dürüstlük uyarıları.**
1. **DEV bu soru için KİRLİDİR.** Bu deneyin fikri, taban kol seçimi ve yön kuralı E18'in 2025 şok-günü tablosu GÖRÜLEREK
   yazıldı. Bu yüzden DEV'i geçmek KANIT DEĞİLDİR; DEV yalnız açıkça kötü kolları eleyen bir süzgeçtir. Kanıt yükünün
   tamamı VAL'dedir (dokunulmamış 2023–2024 verisi).
2. Önsel olasılık düşük. Gerçek zamanlı tetikleyici, şok gününü ancak hareketin bir kısmı olduktan sonra görür; şoka
   dönüşmeyen günlerde de açılır. Kaba bir sonda (sembolün kendi 15m şok mumunun kapanışında şok yönünde giriş, 60 dk ufuk)
   brüt R ≈ 0 bulmuştu (`docs/audits/2026-11-20-olcum-denetimi.md` §6). "Kazanan yok" çıkması muhtemeldir.
3. Backtest canlıdan iyimserdir (E21: TP1'e ulaşma %66, canlı %54). Kazanan çıksa bile yalnız "testnet ölçüm adayı"dır.
4. Çıkış geometrisi (stop, TP1) bu deneyde DEĞİŞKEN DEĞİLDİR; E18'in sabit geometrisi aynen kullanılır (§3.3 R4). Boyut,
   TP1 ya da stop ile kayıp küçültme bu deneyin konusu değildir.
5. Tetikleyici seyrek açılır; işlem sayısı E18 kollarının küçük bir kesridir. Tasarım yalnız büyük bir kenarı görebilir
   (§11); ince bir kenarı göremez.

**Karar akışı.** 2025'te en çok 6 kol elenir; en çok 2'si ilerler. İlerleyen olursa 2023–2024 mührü bir kez açılır ve onay
koşulur; onaylanan en çok 1 kol 2026 Ekim–Kasım'da yalnız veto için bir kez koşulur. İlerleyen olmazsa 2023–2024 verisi
indirilmez ve mühür kapalı kalır.

## 1. Hipotez

- **H1 (kol a için):** görülmemiş veride, gerçekçi maliyet modeliyle, günlük net R ortalaması > 0. H0_a: ≤ 0.
- Açıklayıcı (test edilmez): yüksek eşik (%3.0) düşük eşikten (%1.5) daha yüksek PF verir ama daha az işlem; şoka dönüşmeyen
  tetikleyici günlerinde PF < 1'dir.

## 2. Veri setleri ve pencereler

| Set | Aralık (UTC, bitiş hariç) | Gün | Rol | Kayıt (`docs/holdout_registry.json`) |
|---|---|---|---|---|
| CAL | 2026-03-01 → 2026-06-01 | 92 | yalnız işlem ve tetikleyici SAYIMI, duman testi; PnL okunmaz | yanık pencereler |
| DEV | 2025-01-01 → 2026-01-01 | 365 | eleme; bu soru için KİRLİ (§0 uyarı 1) | `DEV-2025`, yanık |
| VAL | 2023-01-01 → 2024-11-18 | 687 | onay, tek atış | `SEAL-2023-2024`, mühürlü, hiç indirilmedi |
| FINAL | 2026-10-16 → 2026-11-20 | 35 | yalnız veto, tek atış | `SEAL-2026Q4b`, mühürlü |

- **Pencere** = giriş (dolum) zamanının UTC takvim çeyreği (E18 ile aynı). DEV: 2025Q1..Q4. VAL: 2023Q1..2024Q3 (7 tam
  pencere) + 2024-10-01 → 11-18 kısmi dilimi (havuza girer, pencere sayımına girmez).
- Her (kol, set) TEK kesintisiz koşudur; pencere, ay, yön, sembol ve gün kırılımları işlem listesinden türetilir.
- Sızıntı duvarı E18 §2 ile aynıdır (DEV'in en erken ısınma mumu 2024-11-20 08:00 > VAL bitişi). Lider (BTC) serilerinin
  ısınması daha kısadır (1d: gün + 6; 15m: 100 mum).
- **Rejim etiketleri (ay):** E17'nin donmuş dosyası aynen (`docs/audits/2026-10-10-prereg-tf/labels_frozen.json`).
- **Şok günü etiketi:** her koşu raporundaki lider 1d mumlarından (§3.2 "rapor") türetilir; DEV ve VAL günleri için E18'in
  kilitli listesiyle (`docs/audits/2026-11-20-prereg-e18/shock_days.json`, sha256 kilide girer) karşılaştırılır,
  uyuşmayan gün sayısı raporlanır (kapı değildir). İşlem, sinyal gününe (`signal_close_time`'ın UTC günü) göre atanır.

## 3. Kol ailesi

### 3.1 Taban kurulum M (E18 §3.1 AYNEN; kod değişmedi)

Kurulum M, E18'deki tanımıyla aynıdır (`src/strategies/scalper/setups.py`, `StrategyM`): tetikleyici `breakout` (K: son
kapanış önceki 20 mumun en yükseğinin üstünde ve hacim oranı > 1.5; SHORT aynası) ya da `pullback` (G: E18 §3.1); trend
tanımı `regime` (R: rejim rolü EMA50/200) ya da `context` (B: bağlam rolü EMA20/50). Bu deneyde yalnız 30 dakikalık giriş
kullanılır (gerekçe §3.7).

### 3.2 Gerçek zamanlı şok tetikleyicisi T (YENİ kod; varsayılan KAPALI)

**Tanım.** Karar anı `t` = giriş zaman diliminde son KAPANMIŞ mumun kapanış zamanı (harness'ta sinyal mumunun
`close_time`'ı; canlı motorda tarama turunun gördüğü son kapanmış mum). Lider = `SCALPER_MARKET_GATE_SYMBOL` (BTCUSDT).

1. `gün_açılışı(t)` = `market_gate.resolve_day_open`: BTC'nin o UTC gününün 00:00 15m mumunun `open`'ı (o mum `t`'de
   kapanmış olmalı); yoksa son tamamlanmış günlük kapanış (yalnız günün ilk 15 dakikasında; 30m kararlarında hiç olmaz).
2. `son_kapanış(t)` = BTC'nin giriş zaman diliminde (30m) `close_time ≤ t` olan son mumunun kapanışı.
3. `d(t) = (son_kapanış / gün_açılışı − 1) × 100`.
4. `yön(t)` = LONG eğer `d ≥ +X`; SHORT eğer `d ≤ −X`; aksi yok.

Bu üç girdi, mevcut lider piyasa kapısının (D15) zaten hesapladığı girdilerin aynısıdır (`LeaderSeries.inputs_at`
harness'ta, `_leader_market_snapshot` canlı motorda). Hiçbir girdi `t`'den sonra kapanan bir mumdan gelmez.

**Yön kuralı (önceden yazılı).** Bir sinyal (yönü D) yalnız `yön(t) == D` ise geçer. Yani giriş yalnız BTC hareketinin
yönünde yapılır: BTC gün açılışının en az %X üstündeyken yalnız LONG, en az %X altındayken yalnız SHORT. İki yönlü
(tetikleyici açıkken her iki yön) varyant sınanmaz. Gerekçe: E18 tablosunda şoka karşı yönlü işlemler PF 0.14–0.36 ve canlı
defterde günün BTC yönüne ters işlemler −504 (ikisi de ileri bakışlı betimleme; §12).

**Engelleme nedenleri** (`missed_counter` ve canlı niyet defteri anahtarları): `shock_trigger_off` (|d| < X),
`shock_trigger_dir` (tetikleyici açık ama ters yön), `shock_trigger_nodata` (girdi yok → giriş YOK; fail-closed. Mevcut
lider kapısı fail-open'dır; bu tetikleyici bir "izin" kapısı olduğu için veri yoksa izin de yoktur).

**Eşikler (önceden yazılı, tanımdan).** X ∈ {**1.5**, **3.0**} (yüzde).
- **H (yüksek) = 3.0**: şok günü tanımının eşiğinin kendisi. Gün içi hareket %3'e ulaştığında gün "şu ana kadar" bir şok
  günüdür.
- **L (düşük) = 1.5**: aynı eşiğin yarısı ("şoka yarı yolda"); daha erken açılır, daha çok yanlış alarm verir.
- İkisi de 2025 (ya da başka bir) sonucuna bakılarak seçilmedi; ara değer, ikinci tetikleyici türü ya da tarama yoktur.

**Kod.** `src/strategies/scalper/market_gate.py` `evaluate_shock_trigger` (saf fonksiyon). Harness
(`backtest.simulate_symbol`, lider kapısının hemen ardında) ve canlı motor (`engine._evaluate_symbol`, lider kapısının
hemen ardında) AYNI fonksiyonu AYNI girdilerle çağırır (CLAUDE.md yasak #2). Ayar `SCALPER_SHOCK_TRIGGER_PCT`
(varsayılan 0 = KAPALI; > 0 ise X). Tetikleyici açıkken lider serileri, lider piyasa kapısı kapalı olsa da yüklenir.

**Nedensellik kanıtı (birim testleri, H'de yeşil olmalı).** (a) `LeaderSeries.inputs_at(t)` + `evaluate_shock_trigger`:
`t`'den sonra kapanan bütün lider mumları (bugünün geri kalan 15m mumları, oluşmakta olan 1d mumu, sonraki 30m mumları)
değiştirildiğinde karar değişmez. (b) `simulate_symbol`: lider serisinin `T` anından sonraki kısmı değiştirildiğinde, sinyal
zamanı `≤ T` olan işlemler birebir aynıdır. (c) Canlı motor: aynı girdilerle harness ile aynı kararı verir (açık / kapalı /
ters yön / veri yok).

**Rapor (yalnız ölçüm).** Tetikleyici açıkken harness raporu `shock_trigger` bloğunu taşır: eşik, lider, zaman dilimi,
pencere, karar noktası sayısı, veri-yok sayısı, tetikleyicinin AÇIK olduğu karar noktaları (`[close_ms, LONG|SHORT]`) ve
pencere günlerinin lider 1d açılış/kapanışı. Bu blok karar vermez; analizci isabet oranını ve açılış saatini buradan türetir.

### 3.3 Sabit kurallar (her kol; kilitten sonra değişmez)

Her kol = sunucu `SCALPER_*` özeti (E18 ile AYNI dosya: 55 satır, sha256 `2a4748a3…841a2`, içerik depoya girmez) + YALNIZ
`variants.json` içindeki override'lar. E18 §3.2'nin R1–R6 kuralları 30m için aynen geçerlidir:

- **R1 Rol üçlüsü:** 30m / 4h / 4h (giriş / bağlam / rejim).
- **R2 Kapılar kapalı:** `SCALPER_REGIME_FILTER=false`, `SCALPER_MARKET_GATE=false`, `SCALPER_ENTRY_BLOCK_WEEKDAYS_UTC=`
  (boş). Mevcut lider kapısı (ters yönü engelleyen, %1.3) kapalıdır; tetikleyicinin yön kuralı onu zaten kapsar (ters
  yön her zaman engellenir). `SCALPER_MARKET_GATE_SYMBOL=BTCUSDT`, `SCALPER_MARKET_GATE_RUN_DAYS=3` açıkça yazılır (lider
  serisi önbellek anahtarı doldurma ve koşuda aynı olsun diye).
- **R3 Giriş kipi taker** (E18 R3).
- **R4 Çıkış geometrisi:** E18 R4'ün 30m satırı: stop = 2 × ATR(14, 30m), `SCALPER_FIXED_STOP_ROI_PCT = SCALPER_TP1_ROI = 13`,
  `SCALPER_TP2_ROI = 26`; kaldıraç `clamp(round(13 / (2 × ATR%)), 3, 20)`; işlem başına risk = 10 × 13 = 130 USD (sanal
  10 000 kasa).
- **R5 Snapshot'tan AYNEN gelenler** (E18 R5): 5 pozisyon, marj %10, günlük kesici %1, 8 saat tutma, 60 dk kayıp sonrası
  bekleme, 8 sembol (BTC, ETH, SOL, XRP, DOGE, BNB, ADA, LTC). Günlük kesici şok günlerinde ilk tam stoptan sonra o günün
  kalan sinyallerini düşürür; bu kolun parçasıdır (canlı aynası). Kesicisiz sonuç yalnız teşhistir (`DIAG_NOLATCH`).
- **R6 Model:** D41 gerçekçi modelin dokuz anahtarı da açık; fonlama gerçek oranlarla.

### 3.4 Aile (m ≤ 6; Benjamini-Hochberg bu aile üzerinde)

| Kol | Taban (E18 kolu) | Giriş kuralı | Trend | X (%) |
|---|---|---|---|---|
| K30R-L | K30R | kırılım | yavaş (rejim, 4h EMA50/200) | 1.5 |
| K30R-H | K30R | kırılım | yavaş (rejim, 4h EMA50/200) | 3.0 |
| K30B-L | K30B | kırılım | hızlı (bağlam, 4h EMA20/50) | 1.5 |
| K30B-H | K30B | kırılım | hızlı (bağlam, 4h EMA20/50) | 3.0 |
| G30B-L | G30B | geri çekilme-devam | hızlı (bağlam, 4h EMA20/50) | 1.5 |
| G30B-H | G30B | geri çekilme-devam | hızlı (bağlam, 4h EMA20/50) | 3.0 |

**Test edilebilirlik kuralı (performansa kör, kilitten önce uygulanır):** kol aile üyesidir ⇔ CAL işlem sayısı × 365 / 92 ≥
150 (DEV havuz tabanı 100'ün 1.5 katı; E18'deki 1.5 oranı). Kuralı geçemeyen kol kilitten ÖNCE aileden çıkarılır, yerine
kol konmaz ve m küçülür. CAL sayımı: §3.8.

### 3.5 Kaplamalar (aile üyesi değil)

| Kaplama | Ne | Rol |
|---|---|---|
| `ROB_FEE2` | `--fee-stress` (komisyon + kayma 2×) | kapı G6 / G12 |
| `ROB_DLY1` | `--entry-delay-candles 1` (dolum bir mum daha geç; tetikleyici sinyal anında değerlendirilir) | kapı G6 / G12 |
| `DIAG_Z0` | komisyon 0, kayma 0 (fonlama kalır) | teşhis: ücretten önce kenar var mı |
| `DIAG_NOLATCH` | `SCALPER_DAILY_LOSS_LIMIT_PCT=0` | teşhis: sonuç günlük kesiciye bağlı mı |

Teşhis koşuları yalnız `DEV_DECISION.json` yazıldıktan SONRA, ailedeki her kol için DEV'de koşulur; karar vermez.

### 3.6 (boş bırakıldı; numaralar E18 ile hizalı kalsın diye)

### 3.7 Bilerek dışarıda

- **15 dakikalık kollar:** taker gidiş-dönüş maliyeti R'nin daha büyük payıdır (E18 §3.5 aritmetiği); aileyi 6'da tutmak için
  çıkarıldı. E18'in 15m kollarının şok günü PF'si de daha düşüktü (1.11–1.24); bu bilgi seçimde görüldü (§12).
- **Son N saatlik hareket, oynaklık tetikleyicisi, gün içinde "kilitlenen" (bir kez açılınca gün sonuna kadar açık kalan)
  tetikleyici, iki yönlü giriş, başka eşikler, başka lider:** serbestlik derecesi eklememek için yok. UTC gün sınırı bir
  sınırlamadır: 23:00'te başlayan bir hareket 00:00'da sıfırlanır. Yeni fikir = yeni numara, yeni veri.
- **Lider kapısının ters-yön engeli (%1.3) ile birlikte:** tetikleyicinin yön kuralı ters yönü zaten engeller.

### 3.8 CAL sayımı (kilitten önce doldurulur; `cal_counts.json` kilide girer)

CAL koşuları (aile, 92 gün) yalnız SAYIM için okunur: işlem sayısı, LONG/SHORT sayısı, kaldıraç payları, düşen sinyal
sayaçları (tetikleyici nedenleri dahil), tetikleyici gün sayısı. Sayım betiği PnL alanlarını okumaz ve basmaz.

Kod commit'i **H = `<KİLİTTEN ÖNCE DOLDURULUR>`**. Kaynak: `cal_counts.json`.

<!-- CAL_TABLE -->

## 4. Veri doldurma

- Betik: `scripts/fill_klines_cache_from_vision.py` (data.binance.vision, sha256 doğrulamalı, boşlukta hiçbir şey yazmaz),
  30m üçlüsü, sembol başına 8 işçi, **`--market-gate`** (lider serileri: BTC 30m, 15m, 1d). Önbellek depo dışında
  (`E23_ROOT/cache`). E18'in indirme önbelleği (`/Users/max/TRADINGBOT/e18/root/vision`) salt-okunur kopya (APFS klonu)
  olarak yeniden kullanılabilir; önbellek dosyaları yine E23 koşucusunun yazdığı ve `MANIFEST.data.json`'a giren
  dosyalardır.
- Fonlama: `scripts/fill_funding_cache.py` (`E23_ROOT/funding`), set aralığı − 1 gün başlangıçla.
- Sıra: CAL (kilitten önce) → DEV (kilitten SONRA) → VAL (yalnız DEV bir kolu ilerletirse; indirmeden önce `df -h /` ölçülür
  ve tahmini boyut/süre kullanıcıya söylenir) → FINAL (yalnız VAL kazanan çıkarırsa). Doldurma hatası zinciri durdurur;
  pencere ya da sembol ikamesi yoktur. VAL arşiv boşluğu: bir kez yeniden denenir; süren boşlukta VAL-YAPILAMADI.
  VAL'de sıra: önce doldurma, sonra mühür açılışının kaydı, sonra koşu.
- Her doldurmadan sonra önbellek dosyalarının sha256'sı `MANIFEST.data.json`'a yazılır.

## 5. Koşu usulü

- Koşucu `scripts/e23/run_e23.py` (E18 koşucusunun kopyası; yalnız yol, etiket ve doldurma ortamı farklı). Her koşu, kod
  commit'i **H**'nin temiz klonunda (`E23_ROOT/clone_H`), taze geçici dizinde, yalnız E18 §5'teki asgari ortamla çalışır
  (`.env` yok, ölü vekil, sahte zorunlu gizliler, PYTHONHASHSEED=0). Komut: `python -m src.strategies.scalper.backtest
  --strategies M --symbols <8> --diagnostics --start S --end E --cache-dir E23_ROOT/cache --funding-cache-dir
  E23_ROOT/funding [kaplama bayrakları]`.
- **Mühürlü çıktı** (E18 §5): rapor `E23_ROOT/sealed/runs/<etiket>.json` (0400), log `E23_ROOT/sealed/logs/<etiket>.log`.
  Konsola yalnız `BT OK|FAIL <etiket> <sn>`. DEV raporları `DEV_DECISION.json` yazılana kadar açılmaz.
- **check-run** (her koşudan sonra; başarısızsa çıktı silinir ve zincir durur): E18 §5'teki bütün denetimler (H, temiz
  ağaç, pencere, TF üçlüsü, override'ların `provenance.scalper_config` karşılığı, maliyet modeli, dokuz gerçekçilik anahtarı,
  işlem/PnL tutarlılığı, her işlem M, mum sayısı, ağ çekimi yok, önbelleğe dosya eklenmedi) VE: `shock_trigger` bloğu var,
  eşiği kolun X'i, lider BTCUSDT, zaman dilimi 30m; karar noktası sayısı = gün × 48; veri-yok sayısı 0; her işlemin
  `signal_close_time`'ı tetikleyicinin aynı yönde AÇIK olduğu bir karar noktasıdır (ihlal 0); `missed` sayaçlarında
  `market_gate_*` yok.
- Koşular ardışıktır. Başarı `ledger.jsonl`'a yazılır; tamamlanmış koşu tekrarlanmaz.
- **Duman testleri (kilitten önce; DEV değil):** **S1 parite**: H klonunda C, AYI penceresi (2026-01-23 → 02-13, snapshot
  aynen, ana depo önbelleği) = 92 işlem ve net +2808.05 (E19); yalnız "eşleşti / eşleşmedi". **S2 belirlenimcilik**: K30B-L,
  CAL, PYTHONHASHSEED 0 ve 1; `trades` ve `shock_trigger` bloklarının kanonik-JSON sha256'sı aynı. **S3**: ailenin CAL
  koşuları hatasız biter ve check-run'dan geçer.

## 6. Yürütme sırası

| Faz | İş | Durma koşulu |
|---|---|---|
| P0 | Kod (tetikleyici + ayar + testler + `scripts/e23/` + testleri) → commit **H**; tam test paketi yeşil | test kırmızı → kilit yok |
| P0b | CAL doldur; S1, S2, S3; `cal-counts` | hata → yalnız altyapı düzeltilir (yeni H) |
| P0c | `lock` → kilit commit'i **L** uzak dala itilir | itilemezse kilit yok |
| P1 | DEV doldur + fonlama + manifest | hata → dur |
| P2 | DEV taban koşuları (aile), mühürlü | harness hatası → dur |
| P3 | `dev-screen` (G0–G5) | hiçbiri geçmezse P5'e |
| P4 | Stres koşuları: G0–G5'i geçen HER kol için `ROB_FEE2`, `ROB_DLY1` | |
| P5 | `dev-decide` → `DEV_DECISION.json` TEK KEZ yazılır | ilerleyen yoksa VAL yok |
| P6 | Teşhis: `DIAG_Z0`, `DIAG_NOLATCH` (aile, DEV); mühür kalkar | |
| P7 | (yalnız ilerleyen varsa) VAL doldur; mühür açılışı kayda yazılır; ilerleyen başına taban + FEE2 + DLY1; `val-decide` | doldurma hatası → VAL-YAPILAMADI; kazanan yoksa FINAL yok |
| P8 | (yalnız VAL kazananı varsa) FINAL mührü açılır; kazanan bir kez koşar; `final` | tek atış |
| P9 | `report` → `run/DECISION.md`, `results.csv`; EXPERIMENTS E23, DECISIONS D43 notu, kayıt defteri | |

## 7. İstatistik (E18 §7 AYNEN; yalnız tohum öneki değişir)

- **Birim:** `R_i = pnl_i / (margin_usdt_i × 13 / 100)`. PF = pozitif R toplamı / |negatif R toplamı|.
- **Günlük seri:** `x_d` = o UTC günü KAPANAN işlemlerin net R toplamı; işlemsiz gün 0; D = set gün sayısı (365 / 687 / 35).
- **Birincil test (tek yönlü):** `t_obs = ort(x) / (sd(x, ddof=1) / √D)`; sıfır-merkezli, studentize, dairesel gün-blok
  bootstrap; b = 7 (birincil) ve 28 (ikincil); B = 19 999; `p = (1 + #{t* ≥ t_obs}) / (B + 1)`; sd = 0 ya da işlem yoksa
  p = 1. Tohum = `sha256("E23|<set>|<kol>|b<b>")`'ın ilk 8 baytı; RNG stdlib `random.Random`.
- **Monte Carlo hatası:** E18 §7 kuralı (tohum eki `|hi`, B = 199 999).
- **Çoklu test:** DEV'de aile üzerinde Benjamini-Hochberg, **q ≤ 0.10** (G0 ya da G1'i geçemeyen kolun p'si 1.0; m sabit).
  VAL'de ilerleyen k ≤ 2 kola Bonferroni.
- Yalnız bilgi: PF %90 güven aralığı (aynı blok yeniden örneklemesi). Parametreler betikte modül sabitidir.

## 8. Karar kuralı (tüm sayılar kilitli; R birimi; maliyet sonrası)

N = kapasite ve günlük kesici sonrası kapanan işlem. **Aktif çeyrek** = N ≥ 10 olan pencere. "Tam ay" VAL'de 22'dir.
E18'den farklar Ek B'dedir (tetikleyici seyrek ve dönemsel olduğu için örneklem ve genişlik kapıları havuz + aktif çeyrek
üzerinden yazıldı; yoğunlaşma kapısına "en iyi günler" eklendi).

**DEV: kol ilerler ⇔ G0–G6 hepsi.**
- **G0 bütünlük:** check-run geçti; sessiz düşüş / ham sinyal ≤ %10 (E18 tanımı); pencere sonu (EOD) işlemi ≤ 8;
  tetikleyici ihlali (tetikleyici kapalı ya da ters yönken açılmış işlem) = 0; veri-yok karar noktası = 0. Başarısız kol
  GEÇERSİZ, p = 1.0.
- **G1 örneklem:** havuz N ≥ 100 VE aktif çeyrek ≥ 3 / 4. Başarısız → YETERSİZ-ÖRNEK, p = 1.0.
- **G2 havuz PF ≥ 1.15.**
- **G3 anlamlılık:** BH q ≤ 0.10 (m = aile boyu).
- **G4 genişlik:** Σ R > 0 olan aktif çeyrek sayısı ≥ ⌈0.75 × aktif çeyrek sayısı⌉.
- **G5 yoğunlaşma:** en iyi ay çıkarılınca PF ≥ 1.00; en iyi 5 işlem çıkarılınca Σ R > 0; en iyi 3 UTC günü (x_d'ye göre)
  çıkarılınca Σ R > 0.
- **G6 stres (G0–G5'i geçen her kol):** `ROB_FEE2` Σ R > 0 VE `ROB_DLY1` Σ R > 0. Eksik koşu = başarısız.
- **İlerleme seçimi:** hepsini geçenler `t_obs`'a (b = 7) göre sıralanır; aynı tabandan (K30R, K30B, G30B) en çok biri;
  toplam en çok 2 kol. k = ilerleyen sayısı.

**VAL: kol DOĞRULANIR ⇔ G0 (VAL koşuları için) ve G7–G14 hepsi.**
- **G7 örneklem:** havuz N ≥ 150 VE aktif tam çeyrek ≥ 5 / 7.
- **G8** havuz PF ≥ 1.15.
- **G9** `p(b=7) ≤ 0.05 / k` VE `p(b=28) ≤ 0.10 / k`.
- **G10 genişlik:** Σ R > 0 olan aktif tam çeyrek ≥ ⌈(5/7) × aktif tam çeyrek sayısı⌉; VE 22 tam ay zaman sırasıyla iki eşit
  yarıya bölünür (11 + 11), her yarıda N ≥ 50 ve PF ≥ 1.00.
- **G11 yoğunlaşma:** en iyi ay hariç PF ≥ 1.05; en iyi sembol hariç PF ≥ 1.05; her sembol için bir-çıkar PF ≥ 1.00; en iyi 5
  UTC günü hariç Σ R > 0.
- **G12 stres:** `ROB_FEE2` Σ R > 0 VE `ROB_DLY1` Σ R > 0.
- **G13 ayı rejimi (CLAUDE.md yasak #1):** DEV + VAL'in DOWN etiketli ayları havuzlu (3 + 5 ay) PF ≥ 1.10 ve n ≥ 50;
  n < 50 → TEST EDİLEMEDİ = başarısız.
- **G14** `Calmar_R = (Σ R × 365 / D) / DD_R ≥ 0.75`; `DD_R = overall.bar_max_drawdown / medyan(marj × 13 / 100)`.
- Kazanan = geçenler arasında en yüksek VAL `t_obs`. Hiçbiri → DUR.

**FINAL (yalnız veto): G15** (E18 ile aynı). N < 20 → SONUÇSUZ (kol kazanan olamaz). N ≥ 20: PF < 0.90 ya da DD_R > 25 →
VETO; PF ≥ 1.00 ve Σ R > 0 → TUTARLI; arası → BELİRSİZ.

**Bayraklar (karar vermez, raporlanır):** TEK-YÖNLÜ = VAL'de (yoksa DEV'de) n ≥ 50 olan LONG ya da SHORT'un PF'si < 1.00.
NORMAL-ZAYIF = şoka dönüşmeyen tetikleyici günlerindeki işlemlerde n ≥ 30 ve Σ R < 0. KELEPÇE = işlemlerin %50'sinden
fazlası 20x'te. SINIRDA = kapı değeri eşiğe 0.005 içinde.

**Zorunlu betimleyici tablolar (her set ve kol için; karar vermez).**
1. **Tetikleyici etkinliği (eşik başına, set başına; lider verisinden):** tetikleyici günü = en az bir 30m karar noktasında
   tetikleyicinin açık olduğu UTC günü. Sayılar: tetikleyici günü, şok günü, isabet = |tetikleyici ∩ şok| / |tetikleyici|,
   yön isabeti (ilk açılış yönü = şok yönü), kapsama = |tetikleyici ∩ şok| / |şok|, iki yönde de açılan gün sayısı.
2. **Açılış saati:** her tetikleyici gününde ilk açık karar noktasının UTC saati; ortalama, medyan ve 4 saatlik dilim dağılımı.
3. **İşlem kırılımı:** LONG ve SHORT ayrı (N, WR, PF, Σ R); sinyal günü şok günü olan ve OLMAYAN işlemler (N, Σ R, PF; LONG ve
   SHORT ayrı); şok gününde şokla aynı ve ters yön.
4. **Yoğunluk:** tetikleyici günü başına işlem, takvim günü başına işlem, işlemli tetikleyici günü sayısı.
5. Pencere, ay, rejim, sembol, çıkış nedeni, kaldıraç payı, kaçan sinyal sayaçları (E18 ile aynı).

**Sonuç etiketleri.** KAZANAN-HARNESS (DEV + VAL geçti, FINAL veto yok ve N ≥ 20; G15 BELİRSİZ ise "KAZANAN-HARNESS (FINAL
doğrulamadı)") · ADAY-FINAL-SONUÇSUZ · VAL-YOK · VAL-YAPILAMADI · DEV-YOK · FINAL-VETO.

**Önceden yazılmış cümleler.** KAZANAN-HARNESS: "Yalnız testnet ölçüm adayı; gerçek para için kanıt değil; §10 koşulları ve
kullanıcı kararı gerekir." Aksi: "Bu ailede (BTC gün-içi şok tetikleyicisi %1.5 ve %3.0, yalnız BTC yönünde; 30m kırılım ve
geri çekilme) tetikleyici açıkken yapılan momentum girişinin komisyon ve kayma sonrası kenar ürettiğine dair kanıt yok." Her
durumda LONG ve SHORT ayrı, §8'in betimleyici tabloları, `DIAG_Z0` satırı (brüt PF ≥ 1.05 ve net < 1 ise "ham kenar var,
maliyet yiyor"; < 1.05 ise "maliyetten önce de kenar yok") ve kol başına PF %90 güven aralığı raporlanır.

## 9. Durdurma ve bütünlük kuralları

1. **Kilit.** `analyze_e23.py lock`, `LOCK.json` üretir: bu dosyanın, `variants.json`, `cal_counts.json`, E18 şok listesi ve
   E17 etiket dosyasının sha256'sı; H commit'i ve ağaç sha'sı; H'deki ilgili kod dosyalarının blob sha'ları (kurulum M,
   tetikleyici, harness, motor, ayar, `scripts/e23/*`); snapshot sha256'sı. Kilit commit'i **L**
   `origin/prereg/e23-shock-trigger` dalına itilir; itilemezse kilit yoktur ve DEV verisi indirilmez. Her karar alt komutu
   kilidi `git show L:<yol>/LOCK.json` ile doğrular; koşuların yapıldığı klonun commit'i ve her ledger satırı kilitteki H'ye
   eşit olmalıdır; uyuşmazlıkta çıkış kodu 3 ve hiçbir dosya yazılmaz. `lock`, `variants.json` ailesinin CAL sayımındaki test
   edilebilir kollarla aynı olmasını şart koşar.
2. İlk DEV koşusu ledger'a girdikten sonra kilit yenilenmez. Altyapı hatası: ayrı, itilmiş yama commit'i + hatayı gösteren
   test; eski ve yeni karar yan yana, daha muhafazakâr olan bağlayıcı. Eşik, kapı, pencere, tohum, kol değişikliği "hata
   düzeltmesi" sayılmaz.
3. DEV'de ilerleyen yok → DUR: VAL indirilmez, `SEAL-2023-2024` kapalı kalır. VAL'de kazanan yok → FINAL'e dokunulmaz.
4. Mühür açılışı: `python scripts/walk_forward.py registry-check --start <S> --end <E> --by E23 --open-sealed "<amaç>"` ile
   `docs/holdout_registry.json` `opens`'a yazılır; kayıt yazılamazsa koşu yapılmaz.
5. VAL ve FINAL verisi üzerinde zincir dışı elle backtest yok.
6. `DEV_DECISION.json`, `VAL_DECISION.json`, `FINAL.json` birer kez yazılır; ikinci yazım reddedilir.
7. Sonuç görüldükten sonra ince ayar yok. Yeni fikir = yeni numara, yeni veri.
8. Canlı ayar, `.env`, deploy, restart, mainnet: bu deneyin hiçbir aşamasında yok. Yeni kod varsayılan KAPALI.

## 10. Kazanırsa ne gerekir

KAZANAN-HARNESS yalnız ölçüm adayıdır. Sırayla: (1) kullanıcı kararı: çıkış geometrisi (R4) canlı ayardan farklıdır; (2)
canlı yol: tetikleyici canlıda tarama turu başındaki lider görüntüsüyle karar verir (D15 paritesi); 30m rol üçlüsü ve mum-yaşı
/ tekrar-giriş koruması için parite testleri (E18 §10); lider verisi alınamazsa giriş yok (fail-closed) davranışının canlı
etkisi ayrıca ölçülür; (3) `docs/DECISIONS.md` kaydı ve `.env` yedeği; (4) testnet ≥ 5 gün, öldürme ölçütü önceden yazılı;
(5) mainnet kapsam dışı.

## 11. Güç (yaklaşık; uçtan uca simülasyon YAPILMADI)

<!-- POWER_TABLE -->

Hesap (E18 §11 ile aynı varsayımlar): tek yönlü test, ailede tek gerçek etki varsayımıyla BH eşiği p ≤ 0.10 / m (m = 6 için
z ≈ 2.13), %80 güç (+0.84), günlük kümelenme tasarım etkisi 1.3, işlem başına R standart sapması 1.1, işlem başına brüt kayıp
≈ 0.55R. Son üç sayı VARSAYIMDIR, ölçülmedi. Görülebilen ortalama R ≈ (z + 0.84) × 1.1 × 1.3 / √N; kaba PF ≈ 1 + R / 0.55.

## 12. Tasarımın gördüğü ön bilgi (körlük beyanı)

- **Görülen sonuçlar:** E18'in 2025 (DEV) tablosu: altı kolun şok günü / normal gün PF'leri (30m kollar şok gününde 1.64–2.09,
  15m kollar 1.11–1.24; şokla aynı yön 1.66–3.89, ters yön 0.14–0.36) ve maliyetsiz teşhisler. Bu fikir, taban kol seçimi
  (yalnız 30m) ve yön kuralı bu tablodan ETKİLENDİ. Bu yüzden DEV kirlidir ve geçmesi kanıt değildir (§0 uyarı 1).
- Ayrıca görülen: E16 / E17 (C, 2025), E19–E22, denetim raporu §6 (kaba şok sondası: brüt R ≈ 0), canlı defter kesitleri
  (2026-11-23: günün BTC yönüne ters işlemler −504, aynı yön +120; ileri bakışlı).
- **Görülmeyen:** hiçbir tetikleyicili M koşusu (CAL'de yalnız sayım okunacak); VAL (2023–2024) ve FINAL'de hiçbir M koşusu.
  VAL'in 8 sembol mum verisi hiç indirilmedi.
- **Bilinen fiyat yolu:** yazar 2023–2025 BTC fiyat yolunu genel hatlarıyla biliyor; E18'in kilitli şok listesi (2023-01 →
  2025-12, BTC 1d) depodadır, yani VAL'in BTC şok günleri (134 gün) tasarım sırasında erişilebilirdi. Eşikler (1.5, 3.0) bu
  listeye göre AYARLANMADI; şok tanımının eşiğinden ve yarısından gelir.
- Eşiklerin kaynağı: görev tarifi (havuz PF eşiği, işlem tabanı, gün-blok bootstrap, BH q ≤ 0.10, maliyet 2× ve 1 mum
  gecikme), E18 planı (PF 1.15, p kuralları, yoğunlaşma, Calmar_R, FINAL vetosu), CLAUDE.md yasak #1 (ayı PF ≥ 1.10).
  Örneklem ve genişlik kapılarındaki değişiklik (Ek B) tetikleyicinin seyrekliğinden gelir; hiçbir M sonucuna göre
  ayarlanmadı.

## Ek A. Dosyalar ve komutlar

- `scripts/e23/run_e23.py`: `init-clone`, `fill`, `fill-funding`, `manifest`, `run`, `smoke`.
- `scripts/e23/analyze_e23.py`: `cal-counts`, `lock`, `verify-lock`, `check-run`, `dev-screen`, `dev-decide`, `val-decide`,
  `final`, `report`, `package`.
- Etiketler: taban koşu `<SET>_<KOL>` (örnek `DEV_K30B-L`), kaplama `<SET>_<KOL>__<KAPLAMA>`, duman `S1_AYI_C`,
  `S2_K30B-L_h0`, `S2_K30B-L_h1`.
- Bu klasör: `PLAN.md`, `variants.json`, `cal_counts.json`, `LOCK.json`; `run/` altında `LOCK_REF.json`, `ledger.jsonl`,
  `MANIFEST.data.json`, `DEV_SCREEN.json`, `DEV_DECISION.json`, (varsa `VAL_DECISION.json`, `FINAL.json`), `DECISION.md`,
  `results.csv`, `artifacts.tgz` (raporlar; `provenance.scalper_config` çıkarılmış).
- Koşu kökü (depo dışı): `/Users/max/TRADINGBOT/e23/root`.

## Ek B. E18'den sapmalar

| Konu | E23 | E18 | Neden |
|---|---|---|---|
| Kollar | 3 taban × 2 eşik, yalnız 30m | 6 taban, 15m + 30m | soru tetikleyicidir; aile ≤ 6 |
| Örneklem kapısı | havuz N ≥ 100 (DEV) / 150 (VAL) + aktif çeyrek (N ≥ 10) ≥ 3/4 ve 5/7 | çeyrek başına N ≥ 60 | tetikleyici dönemsel; sakin çeyrekte işlem az olması kural gereğidir |
| Genişlik | aktif çeyreklerin ≥ %75'i (DEV) / ≥ 5/7'si (VAL) artıda; VAL yarı başına N ≥ 50 | 3/4 ve 5/7 tüm çeyrek; yarı başına N ≥ 100 | aynı neden |
| Yoğunlaşma | + en iyi 3 (DEV) / 5 (VAL) gün hariç Σ R > 0 | yok | işlemler aynı günlerde kümelenir |
| Ayı kapısı | n ≥ 50 | n ≥ 100 | işlem hacmi E18 kollarının küçük bir kesri |
| Test edilebilirlik | CAL × 365/92 ≥ 150 | CAL × 91.25/92 ≥ 90 | yeni havuz tabanının 1.5 katı |
| Teşhis | Z0, NOLATCH | Z0, MAKER, T, NOLATCH | giriş kipi ve geometri E18'de hükmü değiştirmedi |
| TEK-YÖNLÜ bayrağı | n ≥ 50 | n ≥ 100 | aynı neden |
| Şok etiketi | koşu raporunun lider 1d mumlarından; E18 listesiyle karşılaştırılır | E18 listesi | FINAL dönemi E18 listesinde yok |
