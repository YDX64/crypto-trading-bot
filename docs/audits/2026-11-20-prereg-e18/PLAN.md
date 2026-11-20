# ÖN-KAYITLI DENEY PLANI (E18): trend yönünde (momentum) giriş, komisyon ve kayma sonrası kenar üretir mi?

Tarih 2026-11-20. Dal `prereg/e18-momentum` (origin/main `2ce462e`). Bu belge, hiçbir E18 kolunun DEV, VAL ya da FINAL
sonucu görülmeden yazıldı ve §9'daki usulle kilitlenir (sha256 + itilmiş commit). Kilitten sonra bu dosya DEĞİŞMEZ;
sonradan yazılacak her şey `run/DECISION.md` ve `docs/EXPERIMENTS.md` E18 kaydına girer.
Şablon: `docs/audits/2026-10-10-prereg-tf/PLAN.md` (E17). Ölçüm modeli: D41 gerçekçi model (varsayılan).

## 0. Önce bunu oku

**Soru.** Bot bugün fiyatın aşırı uzadığı yerde TERS yöne giriyor (strateji C) ve görülmemiş veride para kaybediyor
(D43). Trendle AYNI yönde giren basit bir giriş kuralı, aynı çıkış makinesiyle, komisyon ve kayma düşüldükten sonra
para kazandırıyor mu? LONG ve SHORT ayrı, büyük BTC hareket günleri ayrı raporlanır.

**Sözlük.**
- **PF**: kazançlar toplamı / kayıplar toplamı; 1.00 başabaş. Hep komisyon, kayma ve fonlama düşüldükten sonra.
- **R**: işlem başına göze alınan zarar (stop mesafesi). Kazanç ve zarar R katı olarak yazılır.
- **DEV / VAL / FINAL**: eleme (2025), bağımsız onay (2023-01-01 → 2024-11-18, hiç indirilmemiş) ve yalnız veto hakkı
  olan son kontrol (2026-10-16 → 2026-11-20).
- **Kol**: önceden yazılmış tek bir giriş kuralı + zaman dilimi bileşimi. **Kapı**: önceden yazılmış geç/kal kuralı.

**Dürüstlük uyarıları.**
1. Önsel olasılık düşük. Bu projede trend yönlü girişe dair eldeki ölçümler olumsuz ya da çelişkili: kırılım kurulumu A
   1 dakikalıkta PF 0.79 / 0.81 (E20, yanık pencereler); kaba "şok mumunu takip et" sondasında brüt R ≈ 0
   (`docs/audits/2026-11-20-olcum-denetimi.md` §6); iki trend tanımı ters işaret verdi (D43 yön 1). Tek olumlu ipucu
   E17 "Yorum" 6'dır (15m/30m'de ters yön girişler komisyonsuz bile kaybediyor). "Kazanan yok" çıkması muhtemeldir.
2. Backtest canlıdan iyimserdir (E21: TP1'e ulaşma %66, canlı %54). Kazanan çıksa bile yalnız "testnet ölçüm adayı"dır.
3. Bu deney 15 ve 30 dakikalık grafikleri sınar. 5 dakikalık ve 1 saatlik dışarıdadır (gerekçe §3.5).
4. Çıkış geometrisi (stop, TP1) bu deneyde DEĞİŞKEN DEĞİLDİR; bütün kollar aynı sabit geometriyi kullanır (§3.2 R4).
   Bu geometri canlı ayardan farklıdır; kazanan çıkarsa canlıya taşımak ayrı bir kullanıcı kararıdır (§10).

**Karar akışı.** 2025'te 6 kol elenir; en çok 2'si ilerler. İlerleyen olursa 2023–2024 mührü bir kez açılır ve onay
koşulur; onaylanan en çok 1 kol 2026 Ekim–Kasım'da yalnız veto için bir kez koşulur. İlerleyen olmazsa 2023–2024
verisi indirilmez ve mühür kapalı kalır.

## 1. Hipotez

- **H1 (kol a için):** görülmemiş veride, gerçekçi maliyet modeliyle, günlük net R ortalaması > 0. H0_a: ≤ 0.
- Açıklayıcı (test edilmez): hızlı trend tanımı yavaş tanımdan iyidir; 30m 15m'den iyidir (ücret/R düşer); geri
  çekilme tetikleyicisi kırılımdan iyidir.

## 2. Veri setleri ve pencereler

| Set | Aralık (UTC, bitiş hariç) | Gün | Rol | Kayıt (`docs/holdout_registry.json`) |
|---|---|---|---|---|
| CAL | 2026-03-01 → 2026-06-01 | 92 | yalnız işlem SAYIMI ve duman testi; PnL okunmaz | yanık pencereler (OOS_MART, SAKLI_NISAN, HOLD_MAY) |
| DEV | 2025-01-01 → 2026-01-01 | 365 | eleme | `DEV-2025`, yanık (C için); M kolları burada hiç koşulmadı |
| VAL | 2023-01-01 → 2024-11-18 | 687 | onay, tek atış | `SEAL-2023-2024`, mühürlü, hiç indirilmedi |
| FINAL | 2026-10-16 → 2026-11-20 | 35 | yalnız veto, tek atış | `SEAL-2026Q4b`, mühürlü |

- **Pencere** = giriş (dolum) zamanının UTC takvim çeyreği. DEV: 2025Q1..Q4 (4 pencere). VAL: 2023Q1..2024Q3 (7 tam
  pencere) + 2024-10-01 → 11-18 kısmi dilimi (havuza girer, pencere sayım kapısına girmez).
- Her (kol, set) TEK kesintisiz koşudur; pencere, ay, yön ve sembol kırılımları işlem listesinden türetilir.
- Sızıntı duvarı: DEV'in en erken ısınma mumu 2025-01-01 − 250 × 4s = 2024-11-20 08:00 > VAL bitişi (E17 §2.1 ile aynı
  hesap). FINAL ısınması 2026-09-04'e kadar iner ve `VAL-2026Q4a`'yı yalnız gösterge geçmişi olarak okur.
- **Rejim etiketleri (ay):** E17'nin donmuş dosyası aynen kullanılır
  (`docs/audits/2026-10-10-prereg-tf/labels_frozen.json`, 2026-10-10'da donduruldu; BTC 1d, ay getirisi ≥ +%5 UP,
  ≤ −%5 DOWN, aksi FLAT). İşlem, giriş ayının etiketini alır.
- **Şok günü:** BTCUSDT 1d mumunda |kapanış/açılış − 1| ≥ %3 olan UTC günü (E20 tanımı). Liste depodaki BTC 1d
  arşivinden (`docs/audits/2026-10-10-prereg-tf/evidence/rev_btc/`, 2023-01..2025-12) üretilir ve `shock_days.json`
  olarak kilide girer. İşlem, giriş gününe göre atanır. Bu ayrım günün KAPANIŞINI kullanır (ileri bakış); yalnız
  betimleyicidir, kapı değildir.

## 3. Kol ailesi

### 3.1 Giriş kurulumu M (kod: `src/strategies/scalper/setups.py`, `StrategyM`; varsayılan KAPALI)

Canlı motor ve harness AYNI `evaluate` fonksiyonunu çağırır. `SCALPER_STRATEGIES` varsayılanı ("A,B,C,D") ve sunucu
değeri ("C") değişmez; M yalnız `--strategies M` ile koşar. Kurulum saftır (IO yok). Gösterim: `mumlar` = giriş rolü
serisi (son eleman son KAPANMIŞ mum), `son` = son mum, `önceki` = ondan önceki, `ATR` = giriş serisinin ATR(14)'ü.

**Trend tanımı (`SCALPER_M_TREND`).**
- `regime` (**yavaş**, kısaltma R): botun kendi rejimi. Rejim rolü serisinde EMA50 > EMA200 ve kapanış > EMA50 ise UP
  → yalnız LONG; EMA50 < EMA200 ve kapanış < EMA50 ise DOWN → yalnız SHORT; aksi (RANGE, UNKNOWN) işlem yok.
- `context` (**hızlı**, kısaltma B): bağlam rolü serisinde (en az 51 mum) kapanış > EMA20 > EMA50 ise yalnız LONG;
  kapanış < EMA20 < EMA50 ise yalnız SHORT; aksi işlem yok. Bu kipte rejim okunmaz.

**Tetikleyici (`SCALPER_M_TRIGGER`).**
- `breakout` (**kırılım**, K): `son.kapanış` > son mum hariç önceki 20 mumun en yükseği (SHORT: en düşüğünün altı) VE
  `son.hacim` / (önceki 20 mumun ortalama hacmi) > 1.5. Eşikler mevcut kurulum A ile aynıdır.
- `pullback` (**geri çekilme-devam**, G), LONG için hepsi: giriş serisinde EMA20 > EMA50; son mumdan önceki 5 mumun
  en az birinde `düşük ≤ EMA20` (o mumun EMA20 değeri); `son.kapanış > son.açılış`; `son.kapanış > önceki.yüksek`;
  `son.kapanış > EMA20`; `son.kapanış − EMA20 ≤ 1.0 × ATR`. SHORT tam aynasıdır.

Sabitler (20, 1.5, 20/50, 5, 1.0) ders kitabı varsayılanlarıdır; tek değer koşulur, tarama YOKTUR. Yapısal stop
(yalnız `structural` kipte kullanılır) giriş ∓ 2 × ATR'dir; bu deneyde stop §3.2 R4'ten gelir.

### 3.2 Sabit kurallar (her kol; kilitten sonra değişmez)

Her kol = sunucu `SCALPER_*` özeti (55 satır, sha256 `2a4748a3…841a2`, içerik depoya girmez) + YALNIZ `variants.json`
içindeki override'lar.

- **R1 Rol üçlüsü (E17 R1 kuralı):** 15m girişte 15m / 1h / 4h; 30m girişte 30m / 4h / 4h (giriş / bağlam / rejim).
- **R2 Kapılar kapalı:** `SCALPER_REGIME_FILTER=false`, `SCALPER_MARKET_GATE=false`,
  `SCALPER_ENTRY_BLOCK_WEEKDAYS_UTC=` (boş). Gerekçe: bu kapılar C için ve 2026 verisinde ayarlandı; trend tanımı
  kolun kendi kuralıdır. D42 korumaları yalnız C'ye uygulanır; denge filtresini M çağırmaz.
- **R3 Giriş kipi taker:** `SCALPER_ENTRY_MODE=taker`. Dolum = sinyal mumundan sonraki mumun açılışı ± %0.02 kayma,
  %0.05 komisyon. Gerekçe: limit emir kırılımda yalnız fiyat geri gelirse dolar (ters seçilim) ve harness'in limit
  modeli iyimserdir (D41 "bilinen boşluklar"); taker'da dolum varsayımı yoktur. Limit giriş yalnız teşhistir.
- **R4 Çıkış geometrisi (E17'nin donmuş "S" geometrisi, `cal_constants.json`):** stop = 2 × ATR(14, giriş TF)
  (`SCALPER_DYN_LEV_STOP_ATR_MULT=2`), `SCALPER_FIXED_STOP_ROI_PCT = SCALPER_TP1_ROI = S`, `SCALPER_TP2_ROI = 2S`;
  **S = 9 (15m), 13 (30m)**. Yani TP1 = 1R (%40), TP2 = 2R (%20), kalan %40 chandelier 3.5 ATR ile izler; TP1 sonrası
  stop başabaşa gelir. Kaldıraç `clamp(round(S / (2 × ATR%)), 3, 20)`. Bu sabitler E17'de formülden türetildi ve
  E18'de yeniden ölçülmedi. Marj sınırı (%10) her işlemde bağlar; işlem başına risk = 10 × S USD (sanal 10 000 kasa).
- **R5 Snapshot'tan AYNEN gelenler:** `MAX_POSITIONS=5`, `MAX_MARGIN_PCT=10`, `RISK_PERCENTAGE=10`,
  `DAILY_LOSS_LIMIT_PCT=1` (günlük kesici: gün içi gerçekleşmiş zarar −100 USD'ye inince o UTC gününün kalan
  sinyalleri düşer; S geometrisinde yaklaşık bir tam stop), `MAX_HOLD_HOURS=8`, `LOSS_COOLDOWN_MINUTES=60`,
  TP oranları 0.40 / 0.20, başabaş tamponu 0.05, `MIN_RR=0`, `DYN_LEV_MIN=3`, `DYN_LEV_MAX=20`, `MAX_STOP_PCT=5.5`,
  komisyon 0.02 / 0.05, kayma varsayılanı %0.02, 8 sembol (BTC, ETH, SOL, XRP, DOGE, BNB, ADA, LTC; bu sırayla).
  Süre düğmeleri E17'deki gibi mum sayısına ÖLÇEKLENMEZ: 8 saat ve 60 dakika canlı botun duvar-saati değerleridir
  ve "scalping" tanımının parçasıdır.
- **R6 Model:** D41 gerçekçi modelin dokuz anahtarı da açık; fonlama gerçek oranlarla (`fill_funding_cache.py`).

### 3.3 Aile (m = 6; Benjamini-Hochberg bu altısı üzerinde)

| Kol | Tetikleyici | Trend | Giriş / bağlam / rejim | S |
|---|---|---|---|---|
| K15R | kırılım | yavaş (rejim, 4h EMA50/200) | 15m / 1h / 4h | 9 |
| K30R | kırılım | yavaş (rejim, 4h EMA50/200) | 30m / 4h / 4h | 13 |
| K15B | kırılım | hızlı (bağlam, 1h EMA20/50) | 15m / 1h / 4h | 9 |
| K30B | kırılım | hızlı (bağlam, 4h EMA20/50) | 30m / 4h / 4h | 13 |
| G15B | geri çekilme-devam | hızlı (bağlam, 1h EMA20/50) | 15m / 1h / 4h | 9 |
| G30B | geri çekilme-devam | hızlı (bağlam, 4h EMA20/50) | 30m / 4h / 4h | 13 |

**Test edilebilirlik kuralı (performansa kör, kilitten önce uygulanır):** kol aile üyesidir ⇔ CAL işlem sayısı ×
91.25 / 92 ≥ 90 (çeyrek başına 60 kapısının 1.5 katı). Kuralı geçemeyen kol kilitten ÖNCE aileden çıkarılır, yerine
kol konmaz ve m küçülür. CAL sayımı: §3.6.

### 3.4 Kaplamalar (aile üyesi değil)

| Kaplama | Ne | Rol |
|---|---|---|
| `ROB_FEE2` | `--fee-stress` (komisyon + kayma 2×) | kapı G6 / G12 |
| `ROB_DLY1` | `--entry-delay-candles 1` (dolum bir mum daha geç) | kapı G6 / G12 |
| `DIAG_Z0` | komisyon 0, kayma 0 (fonlama kalır) | teşhis: ücretten önce kenar var mı |
| `DIAG_MAKER` | `SCALPER_ENTRY_MODE=maker` | teşhis: limit giriş |
| `DIAG_T` | sunucu çıkış geometrisi (stop ROI 50, TP1 ROI 10, k = 3) | teşhis: sonuç geometriye bağlı mı |
| `DIAG_NOLATCH` | `SCALPER_DAILY_LOSS_LIMIT_PCT=0` (günlük kesici düşürmez) | teşhis: sonuç günlük kesiciye bağlı mı |

Teşhis koşuları yalnız `DEV_DECISION.json` yazıldıktan SONRA, altı kolun hepsi için DEV'de koşulur; karar vermez.

### 3.5 Bilerek dışarıda

- **5 dakika:** medyan stop ≈ %0.42 (E17 CAL) iken taker gidiş-dönüş maliyeti %0.14 = 0.33R; E17'de 5m ters yön
  girişlerin komisyonsuz PF'si 0.93 / 0.88 idi, yani aynalanmış brüt kenar en fazla ≈ 1.1. Aritmetik olarak umutsuz.
- **1 saat:** 8 saatlik süre kuralında yalnız 8 mum; kural değiştirilmeden sınanamaz ve tutuş scalping olmaktan çıkar.
- **Şok mumu tetikleyicisi:** kaba sondada brüt R ≈ 0 (denetim §6). Şok günleri her kol için ayrıca raporlanır.
- BTC'yi lider alan trend tanımı (kurulum bağlamında BTC verisi yok), ikinci parametre değerleri, kol başına ikinci
  geometri, sembol seçimi: serbestlik derecesi eklememek için yok.

### 3.6 CAL sayımı (kilitten önce doldurulur; `cal_counts.json` kilide girer)

CAL koşuları (6 kol, 92 gün) yalnız SAYIM için okunur: işlem sayısı, LONG/SHORT sayısı, kaldıraç payları, düşen sinyal
sayaçları. Sayım betiği PnL alanlarını okumaz ve basmaz; ham CAL raporları açılmaz (dürüstlük sistemidir, makine
zorlaması değildir).

Kod commit'i **H = `425e82a6092ba0614071b6858538331093ed3a83`** ile koşuldu (2026-11-20). Kaynak: `cal_counts.json`.

| Kol | N (92 gün) | LONG | SHORT | Çeyrek izdüşümü | Test edilebilir | 20x payı | 3x payı | Kapasiteden düşen | Günlük kesiciden düşen |
|---|---|---|---|---|---|---|---|---|---|
| K15R | 294 | 164 | 130 | 291.6 | evet | %16.7 | %0 | 22 | 318 |
| K30R | 233 | 128 | 105 | 231.1 | evet | %18.0 | %0 | 9 | 102 |
| K15B | 408 | 219 | 189 | 404.7 | evet | %16.7 | %0 | 59 | 560 |
| K30B | 286 | 145 | 141 | 283.7 | evet | %13.6 | %0 | 33 | 116 |
| G15B | 510 | 236 | 274 | 505.8 | evet | %14.7 | %0 | 68 | 708 |
| G30B | 326 | 158 | 168 | 323.3 | evet | %13.2 | %0 | 28 | 253 |

- Altı kolun hepsi kuralı geçti; aile m = 6 olarak kilitlenir. Sessiz düşüş sayaçlarının hepsi 0.
- Günlük kesici (R5) açılabilecek işlemlerin %27–55'ini düşürüyor (kol başına: düşen / (N + kapasite + kesici)). Bu,
  canlı botun günlük zarar sınırının aynasıdır ve kolların parçasıdır; kilitten sonra değişmez. Sonuçlar bu kuralla
  birlikte okunur: "kesicisiz M" bu deneyde sınanmıyor.
- Duman testleri (H ile): S1 parite eşleşti (C, AYI: 92 işlem, net +2808.05); S2 belirlenimcilik eşleşti (K15R, CAL,
  PYTHONHASHSEED 0 ve 1); S3 altı CAL koşusu check-run'dan geçti; altı kaplamanın her biri K30R ile CAL'de bir kez
  denendi ve check-run'dan geçti (sonuçları okunmadı). Tam test paketi H'de yeşil (4171 geçti, 85 atlandı).

## 4. Veri doldurma

- Betik: `scripts/fill_klines_cache_from_vision.py` (data.binance.vision, sha256 doğrulamalı, boşlukta hiçbir şey
  yazmaz). Kolun TF ortam değişkenleriyle, sembol başına 8 işçi, `--market-gate` YOK. Önbellek depo dışında
  (`E18_ROOT/cache`).
- Fonlama: `scripts/fill_funding_cache.py` (`E18_ROOT/funding`), set aralığı − 1 gün başlangıçla.
- Sıra: CAL (kilitten önce) → DEV (kilitten SONRA) → VAL (yalnız DEV bir kolu ilerletirse) → FINAL (yalnız VAL kazanan
  çıkarırsa). Doldurma hatası zinciri durdurur; pencere ya da sembol ikamesi yoktur.
- VAL arşiv boşluğu: doldurma bir kez yeniden denenir; süren boşlukta sonuç VAL-YAPILAMADI olur (pencere kaydırma ve
  sembol ikamesi yok; E17 §9.5'teki ay adımıyla kaydırma kuralı burada YOKTUR, çünkü karar betiği sabit pencereyle
  çalışır). VAL'de sıra: önce doldurma, sonra mühür açılışının kaydı, sonra koşu. Doldurma başarısızsa açılış
  kaydedilmez ve hiçbir VAL koşusu yapılmaz.
- Her doldurmadan sonra önbellek dosyalarının sha256'sı `MANIFEST.data.json`'a yazılır.

## 5. Koşu usulü

- Koşucu `scripts/e18/run_e18.py`. Her koşu, kod commit'i **H**'nin temiz klonunda (`E18_ROOT/clone_H`), taze geçici
  dizinde, yalnız şu ortamla çalışır: PATH, HOME (geçici), PYTHONPATH (klon), PYTHONHASHSEED=0, TRADINGBOT_LOG_DIR
  (geçici), sahte zorunlu gizliler, ölü vekil (`HTTP(S)_PROXY=http://127.0.0.1:9`), snapshot + kol + kaplama
  satırları. `.env` yok. Komut: `python -m src.strategies.scalper.backtest --strategies M --symbols <8> --diagnostics
  --start S --end E --cache-dir E18_ROOT/cache --funding-cache-dir E18_ROOT/funding [kaplama bayrakları]`.
- **Mühürlü çıktı:** stdout/stderr `E18_ROOT/sealed/logs/<etiket>.log`'a gider; rapor `E18_ROOT/sealed/runs/<etiket>.json`
  (0400). Konsola yalnız `BT OK|FAIL <etiket> <sn>` basılır; PnL, PF, işlem sayısı basılmaz. DEV raporları ve logları
  `DEV_DECISION.json` yazılana kadar açılmaz.
- **check-run** (her koşudan sonra; başarısızsa çıktı silinir ve zincir durur): `git_sha == H` ve `git_dirty == false`;
  pencere ve TF üçlüsü kilitli değerlerde; `variants.json`'daki her anahtarın `provenance.scalper_config` karşılığı
  beklenen değerde; `cost_model` kaplamayla uyumlu; gerçekçilik anahtarlarının dokuzu da açık; `overall.trades ==
  len(trades)` ve PnL toplamı tutarlı; her işlemin kurulumu M; her sembolde giriş TF mum sayısı = gün × mum/gün; logda
  "Binance'ten çekiliyor" yok; koşu önbelleğe dosya eklemedi. Başarı `ledger.jsonl`'a yazılır; tamamlanmış koşu
  tekrarlanmaz.
- Koşular ardışıktır.
- **Duman testleri (kilitten önce; DEV değil):** **S1 parite**: H klonunda C, AYI penceresi (2026-01-23 → 02-13,
  snapshot aynen, ana depo önbelleği) = 92 işlem ve net +2808.05 (E19); çıktı yalnız "eşleşti / eşleşmedi". **S2
  belirlenimcilik**: K15R, CAL, PYTHONHASHSEED 0 ve 1; `trades` kanonik-JSON sha256'sı aynı. **S3**: altı kol CAL'de
  hatasız biter ve check-run'dan geçer.

## 6. Yürütme sırası

| Faz | İş | Durma koşulu |
|---|---|---|
| P0 | Kod (M + ayarlar + testler + `scripts/e18/` + testleri) → commit **H**; pytest yeşil | test kırmızı → kilit yok |
| P0b | CAL doldur; S1, S2, S3; `cal-counts`; `freeze-shock` | hata → yalnız altyapı düzeltilir (yeni H) |
| P0c | `lock` → kilit commit'i **L** uzak dala itilir | itilemezse kilit yok |
| P1 | DEV doldur + fonlama + manifest | hata → dur |
| P2 | DEV taban koşuları (6 kol), mühürlü | harness hatası → dur |
| P3 | `dev-screen` (G0–G5) → hangi kollara stres gerekir | hiçbiri geçmezse P5'e |
| P4 | Stres koşuları: G0–G5'i geçen HER kol için `ROB_FEE2`, `ROB_DLY1` | |
| P5 | `dev-decide` → `DEV_DECISION.json` TEK KEZ yazılır | ilerleyen yoksa VAL yok |
| P6 | Teşhis: `DIAG_Z0`, `DIAG_MAKER`, `DIAG_T`, `DIAG_NOLATCH` (6 kol, DEV); mühür kalkar | |
| P7 | (yalnız ilerleyen varsa) VAL doldur; mühür açılışı kayda yazılır; ilerleyen başına taban + FEE2 + DLY1; `val-decide` | doldurma hatası → VAL-YAPILAMADI; kazanan yoksa FINAL yok |
| P8 | (yalnız VAL kazananı varsa) FINAL mührü açılır; kazanan bir kez koşar; `final` | tek atış |
| P9 | `report` → `run/DECISION.md`, `results.csv`; EXPERIMENTS E18, DECISIONS, kayıt defteri | |

## 7. İstatistik

- **Birim:** `R_i = pnl_i / (margin_usdt_i × S / 100)`; S kolun `SCALPER_FIXED_STOP_ROI_PCT` değeri (DIAG_T'de 50).
  `pnl` komisyon, kayma ve fonlama sonrası nettir. PF = pozitif R toplamı / |negatif R toplamı|.
- **Günlük seri:** `x_d` = o UTC günü KAPANAN işlemlerin net R toplamı (çıkış zamanına göre); işlemsiz gün 0; D = set
  gün sayısı (365 / 687 / 35).
- **Birincil test (tek yönlü):** `t_obs = ort(x) / (sd(x, ddof=1) / √D)`. Sıfır-merkezli, studentize, dairesel gün-blok
  bootstrap: `x̃ = x − ort(x)`; blok uzunluğu **b = 7 gün** (birincil) ve **b = 28 gün** (ikincil); B = 19 999;
  `ceil(D/b)` blok başlangıcı 0..D−1 düzgün (sarmalı), D'ye kırpılır; `t* = ort* / (sd* / √D)`;
  `p = (1 + #{t* ≥ t_obs}) / (B + 1)`. sd = 0 ya da işlem yoksa p = 1. Tohum = `sha256("E18|<set>|<kol>|b<b>")`'ın ilk
  8 baytı; RNG stdlib `random.Random`, indeks `int(random() × D)`.
- **Monte Carlo hatası:** bir kapı kararı `p ± 3 × √(p(1−p)/B)` aralığında ters dönebiliyorsa o p, tohum eki `|hi` ve
  B = 199 999 ile yeniden hesaplanır ve karar onunla verilir.
- **Çoklu test:** DEV'de aile üzerinde Benjamini-Hochberg, **q ≤ 0.10** (p = p(b=7); G0 ya da G1'i geçemeyen kolun
  p'si 1.0 sayılır, m sabit kalır). VAL'de ilerleyen k ≤ 2 kola Bonferroni (bağımsız veri).
- Yalnız bilgi: PF %90 güven aralığı (aynı blok yeniden örneklemesinden), pencere / ay / yön / sembol / şok tabloları.
- Parametreler betikte modül sabitidir; komut satırından değiştirilemez.

## 8. Karar kuralı (tüm sayılar kilitli; R birimi; maliyet sonrası)

N = kapasite ve günlük kesici sonrası kapanan işlem. "Tam ay" VAL'de 22'dir.

**DEV: kol ilerler ⇔ G0–G6 hepsi.**
- **G0 bütünlük:** check-run geçti; sessiz düşüş / ham sinyal ≤ %10 (sessiz düşüş = `stop_band_low + stop_band_high +
  price_distance_zero + qty_zero + no_next_candle`; ham sinyal = N + `capacity + daily_latch + maker_missed +
  min_rr_rejected` + sessiz düşüş); pencere sonu (EOD) işlemi ≤ 8. Başarısız kol GEÇERSİZ, p = 1.0.
- **G1 örneklem:** dört DEV penceresinin her birinde N ≥ 60. Başarısız → YETERSİZ-ÖRNEK, p = 1.0.
- **G2 havuz PF ≥ 1.15.**
- **G3 anlamlılık:** BH q ≤ 0.10 (m = aile boyu).
- **G4 genişlik:** Σ R > 0 olan pencere sayısı ≥ 3 / 4.
- **G5 yoğunlaşma:** en iyi ay çıkarılınca PF ≥ 1.00 VE en iyi 5 işlem çıkarılınca Σ R > 0.
- **G6 stres (G0–G5'i geçen her kol):** `ROB_FEE2` Σ R > 0 VE `ROB_DLY1` Σ R > 0. Eksik koşu = başarısız.
- **İlerleme seçimi:** hepsini geçenler `t_obs`'a (b = 7) göre sıralanır; aynı (tetikleyici, giriş TF) çiftinden en çok
  biri (trend tanımı farkı tek başına ikinci yer açmaz); toplam en çok 2 kol. k = ilerleyen sayısı.

**VAL: kol DOĞRULANIR ⇔ G0 (VAL koşuları için) ve G7–G14 hepsi.**
- **G7** yedi tam VAL penceresinin her birinde N ≥ 60.
- **G8** havuz PF ≥ 1.15.
- **G9** `p(b=7) ≤ 0.05 / k` VE `p(b=28) ≤ 0.10 / k`.
- **G10 genişlik:** Σ R > 0 olan tam pencere ≥ 5 / 7; VE 22 tam ay zaman sırasıyla iki eşit yarıya bölünür (11 + 11),
  her yarıda N ≥ 100 ve PF ≥ 1.00.
- **G11 yoğunlaşma:** en iyi ay hariç PF ≥ 1.05; en iyi sembol hariç PF ≥ 1.05; her sembol için bir-çıkar PF ≥ 1.00.
- **G12 stres:** `ROB_FEE2` Σ R > 0 VE `ROB_DLY1` Σ R > 0.
- **G13 ayı rejimi (CLAUDE.md yasak #1):** DEV + VAL'in DOWN etiketli ayları havuzlu (3 + 5 ay) PF ≥ 1.10 ve n ≥ 100;
  n < 100 → TEST EDİLEMEDİ = başarısız.
- **G14** `Calmar_R = (Σ R × 365 / D) / DD_R ≥ 0.75`; `DD_R = overall.bar_max_drawdown / medyan(marj × S / 100)`.
- Kazanan = geçenler arasında en yüksek VAL `t_obs`. Hiçbiri → DUR.

**FINAL (yalnız veto): G15.** N < 20 → SONUÇSUZ (kol kazanan olamaz). N ≥ 20: PF < 0.90 ya da DD_R > 25 → VETO; PF ≥ 1.00
ve Σ R > 0 → TUTARLI; arası → BELİRSİZ.

**Bayraklar (karar vermez, raporlanır):** TEK-YÖNLÜ = VAL'de (yoksa DEV'de) n ≥ 100 olan LONG ya da SHORT'un PF'si
< 1.00. ŞOK-ZAYIF = şok günlerinde n ≥ 30 ve Σ R < 0. KELEPÇE = işlemlerin %50'sinden fazlası 20x'te. SINIRDA = kapı
değeri eşiğe 0.005 içinde.

**Sonuç etiketleri.** KAZANAN-HARNESS (DEV + VAL geçti, FINAL veto yok ve N ≥ 20; G15 BELİRSİZ ise etiket
"KAZANAN-HARNESS (FINAL doğrulamadı)" yazılır) · ADAY-FINAL-SONUÇSUZ · VAL-YOK · VAL-YAPILAMADI · DEV-YOK · FINAL-VETO.

**Önceden yazılmış cümleler.** KAZANAN-HARNESS: "Yalnız testnet ölçüm adayı; gerçek para için kanıt değil; §10
koşulları ve kullanıcı kararı gerekir." Aksi: "Bu ailede (kırılım ve geri çekilme, 15m ve 30m, iki trend tanımı) trend
yönlü girişin komisyon ve kayma sonrası kenar ürettiğine dair kanıt yok." Her durumda LONG ve SHORT ayrı, şok günleri
ayrı, `DIAG_Z0` satırı (brüt PF ≥ 1.05 ve net < 1 ise "ham kenar var, maliyet yiyor"; < 1.05 ise "maliyetten önce de
kenar yok") ve kol başına PF %90 güven aralığı raporlanır.

## 9. Durdurma ve bütünlük kuralları

1. **Kilit.** `analyze_e18.py lock`, `LOCK.json` üretir: bu dosyanın, `variants.json`, `shock_days.json`,
   `cal_counts.json` ve E17 etiket dosyasının sha256'sı; H commit'i ve ağaç sha'sı; H'deki ilgili kod dosyalarının blob
   sha'ları; snapshot sha256'sı. Kilit commit'i **L** `origin/prereg/e18-momentum` dalına itilir; itilemezse kilit
   yoktur ve DEV verisi indirilmez. Her karar alt komutu, çalışma kopyasını `git show L:<yol>/LOCK.json` ile ve dosya
   özetleriyle karşılaştırır; ayrıca koşuların yapıldığı klonun commit'inin (`H.txt`) ve her ledger satırının
   kilitteki H'ye eşit olduğunu doğrular; uyuşmazlıkta çıkış kodu 3 ve hiçbir dosya yazılmaz. `lock`, `variants.json`
   ailesinin CAL sayımındaki test edilebilir kollarla aynı olmasını şart koşar.
2. İlk DEV koşusu ledger'a girdikten sonra kilit yenilenmez. DEV sonuçları varken bulunan altyapı hatası: ayrı, itilmiş
   yama commit'i + hatayı gösteren test; hem eski hem yeni karar `DECISION.md`'de yan yana, daha muhafazakâr olan
   bağlayıcı. Eşik, kapı, pencere, tohum, kol değişikliği hiçbir koşulda "hata düzeltmesi" sayılmaz.
3. DEV'de ilerleyen yok → DUR: VAL indirilmez, `SEAL-2023-2024` kapalı kalır. VAL'de kazanan yok → FINAL'e dokunulmaz.
4. Mühür açılışı: `python scripts/walk_forward.py registry-check --start <S> --end <E> --by E18 --open-sealed "<amaç>"`
   ile `docs/holdout_registry.json` `opens`'a yazılır; kayıt yazılamazsa koşu yapılmaz.
5. VAL ve FINAL verisi üzerinde zincir dışı elle backtest yok.
6. `DEV_DECISION.json`, `VAL_DECISION.json`, `FINAL.json` birer kez yazılır; ikinci yazım reddedilir.
7. Sonuç görüldükten sonra ince ayar yok. Yeni fikir = yeni numara, yeni veri.
8. Canlı ayar, `.env`, deploy, restart, mainnet: bu deneyin hiçbir aşamasında yok.

## 10. Kazanırsa ne gerekir

KAZANAN-HARNESS yalnız ölçüm adayıdır. Sırayla: (1) kullanıcı kararı: çıkış geometrisi (R4) canlı ayardan farklıdır ve
"boyut/TP1/stop ile oynama" kuralına dokunur; karar kullanıcınındır; (2) canlı yol: motor aynı kapanmış mumu her
taramada yeniden değerlendirir, harness mum kapanışında bir kez; mum-yaşı / tekrar-giriş koruması ve 15m / 30m rol
üçlüsü için parite testleri (E17 §10 madde 2–4); (3) `docs/DECISIONS.md` kaydı ve `.env` yedeği; (4) testnet ≥ 5 gün,
öldürme ölçütü önceden yazılı; (5) mainnet kapsam dışı.

## 11. Güç (yaklaşık; uçtan uca simülasyon YAPILMADI)

CAL sayımından izdüşüm (× 365 / 92 ve × 687 / 92; ±%10–20 belirsizlik, 2026 baharı ile 2025 ve 2023–2024 oynaklığı
farklıdır):

| Kol | DEV N (beklenen) | VAL N (beklenen) | %80 güçle görülebilen ortalama R / işlem (DEV) | Buna karşılık gelen kaba PF |
|---|---|---|---|---|
| K15R | ≈ 1170 | ≈ 2200 | ≈ 0.11 | ≈ 1.20 |
| K30R | ≈ 925 | ≈ 1740 | ≈ 0.12 | ≈ 1.22 |
| K15B | ≈ 1620 | ≈ 3050 | ≈ 0.09 | ≈ 1.17 |
| K30B | ≈ 1135 | ≈ 2140 | ≈ 0.11 | ≈ 1.20 |
| G15B | ≈ 2025 | ≈ 3810 | ≈ 0.08 | ≈ 1.15 |
| G30B | ≈ 1295 | ≈ 2430 | ≈ 0.10 | ≈ 1.19 |

Hesap: tek yönlü test, ailede tek gerçek etki varsayımıyla BH eşiği p ≤ 0.10 / 6 (z ≈ 2.13), %80 güç (+0.84), günlük
kümelenme için tasarım etkisi 1.3, işlem başına R standart sapması 1.1, işlem başına brüt kayıp ≈ 0.55R. Son üç sayı
VARSAYIMDIR, ölçülmedi. Okuma: bu tasarım net PF ≈ 1.2 ve üstü bir kenarı büyük olasılıkla görür; 1.05–1.10 düzeyinde
ince bir kenarı göremez ve "kazanan yok" der (PF ≥ 1.15 kapısı zaten bunu ister). Bir "yok" sonucunda kol başına PF %90
güven aralığı yazılır. Gerçek PF = 1.0 iken tüm kapı yığınını (DEV + VAL) geçme olasılığı hesaplanmadı; E17'nin aynı
türden yığını için simülasyonla ≈ %0.1 bulunmuştu (E17 PLAN §7.4), burada yalnız kıyas olarak anılır.

## 12. Tasarımın gördüğü ön bilgi (körlük beyanı)

- Kilitten önce M kurulumu yalnız CAL'de (2026 Mart–Mayıs) koştu ve yalnız sayımı okundu. DEV, VAL, FINAL'de hiçbir M
  koşusu yok.
- Tasarım şu SONUÇLARI görerek yapıldı: E16 / E17 (C, 2025), E19–E22, denetim raporu §6 (şok sondası, canlı defter
  kesitleri), D43 yön 1. Bunların hiçbiri M'nin 2025 ya da 2023–2024 sonucu değildir; önsel olasılığı düşürürler.
- 2025'te A, B, D, E kurulumları hiç koşulmadı (E20 yalnız 2026 yanık pencereleriydi).
- Eşiklerin kaynağı: görev tarifi (pencere başına ≥ 60, gün-blok bootstrap, BH q ≤ 0.10, maliyet 2× ve 1 mum gecikme),
  E17 planı (PF 1.15, p kuralları, yoğunlaşma, Calmar_R, FINAL vetosu), CLAUDE.md yasak #1 (ayı PF ≥ 1.10). Hiçbiri
  M sonucuna göre ayarlanmadı.
- Kalan risk: yazar 2023–2025 BTC fiyat yolunu genel hatlarıyla biliyor.

## Ek A. Dosyalar ve komutlar

- `scripts/e18/run_e18.py`: `init-clone`, `fill`, `fill-funding`, `manifest`, `run`, `smoke`.
- `scripts/e18/analyze_e18.py`: `freeze-shock`, `cal-counts`, `lock`, `verify-lock`, `check-run`, `dev-screen`,
  `dev-decide`, `val-decide`, `final`, `report`, `package`.
- Etiketler: taban koşu `<SET>_<KOL>`, kaplama `<SET>_<KOL>__<KAPLAMA>`, duman `S1_AYI_C`, `S2_K15R_h0`, `S2_K15R_h1`.
- Bu klasör: `PLAN.md`, `variants.json`, `shock_days.json`, `cal_counts.json`, `LOCK.json`; `run/` altında
  `LOCK_REF.json`, `ledger.jsonl`, `MANIFEST.data.json`, `DEV_SCREEN.json`, `DEV_DECISION.json`, (varsa
  `VAL_DECISION.json`, `FINAL.json`), `DECISION.md`, `results.csv`, `artifacts.tgz` (raporlar; `provenance.scalper_config`
  çıkarılmış).

## Ek B. Görev tarifinden ve E17'den sapmalar

| Konu | Burada | Neden |
|---|---|---|
| "Pencere" | takvim çeyreği (giriş zamanı) | kesintisiz koşu + pencere başına 60 kapısı birlikte |
| Süre kuralı | canlı duvar-saati (8 sa, 60 dk); E17'nin mum ölçeklemesi yok | scalping tanımı; yeni düğme yok |
| Giriş kipi | taker birincil; E17'de maker birincildi | kırılımda limit dolum ters seçilimli ve modeli iyimser |
| VAL anlamlılık | Bonferroni (k ≤ 2), iki blok uzunluğu; BH yalnız DEV ailesinde | VAL'de çoklu test yalnız k kol; daha katı |
| Fonlama | gerçek oranlar; E17'de sabit yedek orandı | D41 modeli |
| Çalışma yeri | ayrı git çalışma ağacı | aynı klasörde başka oturum çalışıyordu |
