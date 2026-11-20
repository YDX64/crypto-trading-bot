# 2026-11-20 ölçüm denetimi — "backtest sayılarına güvenebilir miyiz?"

Kapsam: yalnız ölçüm zinciri (harness, kabul kuralları, canlı defterin okunması). Strateji ayarı ve sunucu `.env` DEĞİŞMEDİ; kod aynı gün TESTNET'e deploy edildi
(2026-11-20 07:41 UTC, sağlık yeşil; kayıt: D43 "Deploy"). Tüm "canlı" sayılar TESTNET defterinden (simülasyon parası, gerçek para değil); backtest sayıları geçmiş
piyasa verisi üzerinde simülasyondur. Kararlar: D41, D42 (sonuç eklendi), D43. Deneyler: EXPERIMENTS E19-E22.
Denetim, 2026-10-10/11 tarihli iki commit'ten (E16, E17) yoksun bir main'den başladı; bunlar sonradan birleştirildi ve bu raporla
uyuşur (bölüm 7).

## 1. Özet

1. Denetim ölçüm zincirini 10 boyutta düşmanca doğrulamayla tarattı: 63 bulgu, 46'sı ayakta, 16'sı olası, 1'i çürütüldü.
2. Ayakta kalan 46'nın 31'i "iyimser" yönde, 3'ü kötümser, 4'ü nötr, 8'inin yönü bilinmiyor; 6'sı yüksek önemde.
3. D41 ölçüm aracını düzeltti (gerçekçilik anahtarları, P2-v2, walk-forward, saklı pencere kaydı). Canlı davranış değişmedi.
4. Gerçekçi modelde C'nin yedi bilinen penceredeki sonucu 1124 işlem / +2679.50'den 751 işlem / +107.10'a indi (PF 1.04'ten 1.00'a).
5. Görülmemiş doğrulama penceresinde (2026-09-04..10-15) C: 231 işlem, PF 0.58, net −8631 (10 000 taban). Kesintisiz geçmişte
   (2026-01-23..08-21, 191 gün; örneklem-içi sayılır) PF 0.85. Bu dönem yanık standart pencerelerin altısını içerir; C'nin
   ayarları için bağımsız DEĞİLDİR, yalnız D42 koruma hipotezlerinden bağımsızdır.
6. Canlı testnet defteri, C id ≥ 278: PF 0.43, sanal sermaye 1000'den yaklaşık 169'a indi.
7. main'de zaten duran iki ön-kayıtlı deney (E16, E17; eski model, 2025 verisi) bağımsız olarak kenar bulamamıştı: bugünkü ayarlar 8 taze
   pencerede PF 0.773, 2025'in tamamında PF 0.777; hiçbir çıkış varyantı ve hiçbir giriş zaman dilimi kabul kuralını geçmedi.
8. Başka hiçbir kurulum (A, B, D, E) tek başına kenar göstermedi (PF 0.76-0.84, yanık pencerelerde); sekiz giriş koruması
   P2-v2'yi geçmedi.
9. Hüküm: örneklem dışında kenar gösterilemedi; hiçbir ayar değişmez; mainnet NO-GO teyit.
10. Bu raporda yapılmayanlar ve doğrulanmayanlar bölüm 9'da, açık araştırma yönleri bölüm 10'da.

## 2. Ölçüm zincirinde ne yanlıştı

Gruplama bu rapora aittir (konuya göre); denetimin kendi 10 boyutu bölüm sonundaki notta. Sayılar bulgu adedi.

| Grup | Ayakta | Olası | Ne yanlıştı (kısa) |
|---|---:|---:|---|
| Dolumlar ve maliyetler | 8 | 1 | Stop/trail çıkışı tam tetik fiyatında doluyordu (boşluk ve kayma yok); piyasa tipi çıkışta kayma sıfır; borsa filtreleri (adım, minimum notional) yok; TP1 mumunda koşucu BE'de öldürülüyordu (bu tek bulgu kötümser yönde) |
| Maker giriş | 5 | 1 | Limit emir fiyata "değdi" diye doluyordu (kuyruk/trade-through yok, gecikme sıfır, ~%97-99 dolum); dolum mumunun dolum öncesi yolu da sayılıyordu; aynı sembolde üst üste bekleyen emir |
| Fonlama ve likidasyon | 2 | 3 | Backtest net PnL'inde fonlama maliyeti hiç yoktu; likidasyon fiyatı / bakım marjı kontrolü yoktu (olası bulgular) |
| Walk-forward ve saklı pencere | 9 | 1 | Gerçek örneklem-dışı prosedür yok; "dokunulmamış" pencereler zaten yanıktı (300'den fazla değerlendirme); "tek atış" sınavı iki kez açıldı; evren bugünün 8 majörü |
| Parametre taraması | 7 | 2 | Aynı gün önbelleği commit/env değişse de eski sonucu kullanıyordu; yerel `.env` sızıyordu; P2 kapısı epsilon'u kabul ediyor, çoklu test düzeltmesi yok; tek-nokta, tek-faktör tarama; yerel varsayılan sunucudan farklı |
| Permütasyon / Monte Carlo | 3 | 3 | +inf PF çekilişleri atılıyordu (p-değeri aşağı yanlı); permüte sıfır dağılımı temiz "kenarsız" sıfır değildi; sembol bağımsızlığı tutarsız. 1 bulgu çürütüldü (`montecarlo-6`) |
| Metrikler | 4 | 2 | Drawdown'ın yüzde tabanı yoktu; defter raporu doğrulanmamış PnL kaynaklarını ve takipçiyi karıştırıyordu; sonsuz PF JSON'da `null` olup otomasyonu kırıyordu; soak kısmi günleri sayıyordu |
| Portföy paritesi, canlı risk, sinyal mantığı | 8 | 3 | Günlük kesici harness'te yoktu; semboller bağımsız simüle edilip kapasite sonradan uygulanıyordu; toplam açık risk / korele yön tavanı yok; denge filtresi kırılım stratejisi A ile çelişiyor |
| **Toplam** | **46** | **16** | + 1 çürütülen |

Not: denetimin kendi boyutları (ayakta): `walkforward-oos` 7, `parity` 7, `param-sweep` 6, `cost-model` 5, `metrics` 5,
`exit-logic` 4, `lookahead` 3, `montecarlo` 3, `live-risk` 3, `signal-logic` 3. Madde madde liste ve kanıt:
`logs/audit_20261120/audit_result.json`.

## 3. Ne düzeltildi (D41) ve eski sayılar nasıl üretilir

| Düzeltme | Etki |
|---|---|
| Dokuz gerçekçilik anahtarı (varsayılan AÇIK): boşluk dolumu, çıkış kayması, mum içi yol, maker trade-through, dolum mumu, tek bekleyen emir, fonlama, likidasyon, günlük kesici | işlem sayısı ve PnL gerçekçi |
| P2-v2 kabul kuralı | örneklem ≥ 60, eşleşmiş bootstrap + BH q ≤ 0.10, pencere zararı, DD, maliyet 2× ve 1 mum gecikme stresi |
| `scripts/walk_forward.py` | örneklem-dışı katlar, embargo; yalnız önbellekle |
| `docs/holdout_registry.json` | D41'de 12 pencere: 10 yanık, 1 doğrulama, 1 mühürlü; birleştirmeden sonra 14 (+ yanık `DEV-2025`, + mühürlü `SEAL-2023-2024`); mühürlüye `--open-sealed` olmadan koşu reddedilir |
| Permütasyon, Monte Carlo, defter raporu düzeltmeleri | p-değeri yanlılığı ve metrik kaynak karışıklığı giderildi |

Eski sayıları üretmek için `--legacy-model` (tüm anahtarlar kapalı): 7 pencerede işlem listesi ve net PnL eski koşuyla birebir
özdeş doğrulandı (D41 "Kanıt"). Tek özelliği kapatmak için `--disable-realism <ad>`.

## 4. Eski ve gerçekçi sayılar (C, 7 pencere, sunucu env'i, 8 majör)

| Pencere | Eski: işlem / WR / PF / net | Gerçekçi: işlem / WR / PF / net |
|---|---|---|
| AYI 01-23→02-13 | 149 / %87.3 / 2.03 / +5703.23 | 92 / %84.8 / 1.62 / +2808.05 |
| MART 03-01→04-01 | 296 / %79.7 / 0.98 / −408.85 | 204 / %77.0 / 0.84 / −2371.44 |
| MAYIS 05-04→05-25 | 168 / %68.5 / 0.60 / −6110.74 | 107 / %71.0 / 0.70 / −2640.89 |
| HAZİRAN 06-08→06-29 | 158 / %75.3 / 1.02 / +175.49 | 109 / %75.2 / 0.98 / −135.39 |
| YATAY 07-01→07-21 | 136 / %72.8 / 1.50 / +2764.10 | 95 / %72.6 / 1.96 / +2804.45 |
| BOĞA 08-07→08-21 | 101 / %70.3 / 1.33 / +1276.58 | 61 / %63.9 / 0.76 / −856.76 |
| ÇÖKÜŞ 08-21→09-03 | 116 / %75.0 / 0.90 / −720.32 | 83 / %77.1 / 1.10 / +499.09 |
| **Toplam (JSON'lardan yeniden hesaplandı)** | **1124 / – / 1.04 / +2679.50** | **751 / – / 1.00 / +107.10** |

En büyük iki etken `daily_latch` (günlük kesici) ve `maker_trade_through` (etki ayrıştırması yedi pencerenin üçünde koşuldu: AYI, BOĞA, ÇÖKÜŞ; diğer dördünde
ayrıştırma yok); ikisi de fiyatlamadan çok HANGİ işlemlerin yapıldığını
değiştirir. Etkiler pencereye göre işaret değiştirir (gerçekçi = hep kötümser değil). Ayrıntı: E19. Yedi pencerenin tamamı yanıktır;
bu sayılar örneklem-dışı kanıt değildir.

## 5. Canlı defter gerçekleri (TESTNET, simülasyon parası; salt-okunur kopya, 2026-08-07→2026-11-20)

692 kapalı işlem: C 546 (−437.2), TV 124 (+208.7), takipçi (AP) 22 (−9.7). Toplam net −238.29.

| Ay (açılış) | İşlem | Net USDT | | Yön | İşlem | Net USDT |
|---|---:|---:|---|---|---:|---:|
| Ağustos | 300 | +505.7 | | LONG | 317 | +254.1 |
| Eylül | 169 | −224.9 | | SHORT | 375 | −492.4 |
| Ekim | 137 | −443.4 | | | | |
| Kasım (20'sine kadar) | 86 | −75.7 | | | | |

| Çıkış tipi | İşlem | Net USDT |
|---|---:|---:|
| TRAIL | 366 | +2042.9 |
| TP_LADDER | 26 | +421.8 |
| TRAIL_MARKET | 9 | +29.3 |
| SL | 125 | −2108.9 |
| REAPER (8 sa) | 147 | −528.2 |
| UNKNOWN / BE_MARKET / RISK_EVENT / PROTECTION_FAILED_FLAT | 7 / 5 / 5 / 2 | −74.5 / −12.4 / −7.8 / −0.6 |

(Çıkış tablosu doğrudan veritabanından yeniden toplandı: toplam 692 işlem, −238.29. Forensics notundaki "diğer 28 işlem −102"
yanlış toplamdı; doğrusu −66.0.)

- **Sanal sermaye:** 1000'den ~913 (Ağustos sonu), ~688 (Eylül), ~245 (Ekim), ~169 (19 Kasım) (yeniden kurulmuş değerler).
  Marj sermayenin %10'u olduğundan USDT toplamlarını aylar arası kıyaslamak yanıltır: medyan marj Ağustos 96, Eylül 74,
  Ekim 29, Kasım 18 USDT. ROI toplamı: Ağustos +49, Eylül −493, Ekim −1213, Kasım −357.
- **Kâr bir olaydan:** 2026-08-19/20/21'de (BTC +%7.1/+5.3/+7.3) 83 işlem +727 USDT; bu günler ve ilk 8 işlem çıkarılınca 601 işlem,
  −1307.5, PF 0.52.
- **Ödeme asimetrisi:** kazançların ort. ROI'si +9.0, kayıpların −20.5; başabaş kazanma oranı %69.4, gerçek %59.5. İşlemlerin
  %44'ü TP1'e (ROI +10) hiç ulaşmıyor ve −2662 USDT veriyor; ulaşanlar +2423.
- **TradingView kaynağı 2026-09-13'ten beri sessiz** (son TV işlemi 2026-09-13 01:36 UTC). Sunucuya `/tv-signal` isteği
  ulaşmıyor: bot erişim günlüğünde (2026-11-13..20) ve nginx günlüklerinde (2026-11-06..20) sıfır POST; son 30 günün 945
  niyetinin hepsi `source=scan`. Bot ve nginx yolu sağlam (secret'sız yoklama uygulamadan 403 alıyor). RUNBOOK takvimindeki
  '2026-09-14: 49 alarm expire oluyor → yenile' işi yapılmamış; en olası neden alarmların süresinin dolması (kesin teyit
  TradingView Alarmlar panelinden, bu incelemede açılmadı). Kanıt: `logs/audit_20261120/tv_silence/README.txt`; teşhis
  adımları RUNBOOK 'TV kaynağı sessizse'.
- **`/health` mandalı:** `Orchestrator._recovery_ready` yalnız `start()`'ta bir kez atanıyor; başlangıç kurtarması `False` dönerse
  hiçbir yol onu geri çevirmiyor. Sonuç: süreç 2026-11-17 06:34 UTC'de yeniden başladığından beri `/health` HTTP 503
  degraded ve orkestratörün (Telegram sinyal) giriş yolu reddediyor; scalper giriş yolu etkilenmiyor (kendi bayrağı `True`).
  Kök neden `logs/bot.log`'da gözlendi: o başlangıçta Binance bağlantı testi başarısız oldu ve `recover_open_positions` "Açık
  pozisyonlar borsadan sorgulanamadı" yazdı (sunucu saatiyle 07:35-07:36, UTC+1). Kod düzeltmesi YAPILMADI (yeniden başlatma
  bayrağı temizler); `restart_safe.sh` ve `server_deploy.sh` aynı sağlık koşulunu kullandığı için deploy sağlık kapısı da buna
  bağlı. Bu hata kipi RUNBOOK "Sorun giderme"de belgelidir (D41 ile eklendi). Güncelleme: 2026-11-20 07:41 UTC deploy'undaki
  yeniden başlatma bayrağı temizledi (`recovery_ready=true`, `/health` 200 healthy); kod düzeltmesi hâlâ yok, aynı durum bir sonraki
  başarısız açılışta yeniden oluşabilir.

## 6. Para nerede kaybediliyor

Kümeler birbiriyle örtüşür; toplanmaz. Tümü örneklem-içi (692 testnet işlemi, yaklaşık 25 kesit incelendi), hipotez niteliğinde.

| Küme | İşlem | Net USDT |
|---|---:|---:|
| SL çıkışları (−50 ROI stop; 83'ü Ağustos'un eski stop rejiminden: −1281) | 125 | −2108.9 |
| REAPER 8 saat zaman çıkışı (%14 kazanç) | 147 | −528.2 |
| 15m RANGE rejiminde C girişleri | 259 | −530.6 |
| 5m RSI zaten fade yönünde aşırı (bağlam RSI ≥ 60 SHORT / ≤ 40 LONG) | 199 | −651.5 |
| Aynı sembol-yönde bir önceki işlem kayıp | 266 | −602.2 |
| C SHORT (tüm rejimler) | 317 | −408.9 |
| EMA50/200 farkına göre trendle AYNI yönde açılan işlemler (fark > %0.5) | 223 | −550.0 |
| En kötü 10 gün (çok sembollü aynı yön stop günleri) | 69 | −857.5 |
| 09-28..10-18 çöküş penceresi (kazanma %40) | 94 | −553.7 |
| BTC 24 sa ≤ −%0.5 iken SHORT | 143 | −384.7 |
| Zaten süren hareketin aleyhine giriş (60 dk hareket ≥ 6× medyan) | 134 | −310.9 |
| Yüksek oynaklık girişleri (ATR% ≥ 0.12) | 116 | −402.3 |
| Eylül sonrası LONG (≥ 10-01) | 70 | −258.9 |

(Aynı sembol-yönde bir önceki işlem kayıp satırı: bu satır bağımsız yeniden hesaptan; ilk analiz 268 / −602.8 vermişti.)

**Ani hareket bulgusu.** Sembolün kendi 15m "şok" çubuğundan (|hareket| > 3× önceki 24 saatin medyanı) sonraki 30 dakikada,
şokun AKSİNE (fade) açılan 189 işlem −720 USDT, toplam brüt zararın %42.5'ini taşıyor (1223 / 2876; küme içi payda ile %45.5);
şokla aynı yönde 35 işlem +216. Uyarılar: tanım şok çubuğunun içindeki girişleri de kapsıyor ve çubuğun tamamlanmış yönünü
kullanıyor (kısmi ileri-bakış); yalnız çubuk kapandıktan sonra açılanlarda 43 işlem −114. Bu, gelecekte kullanılabilir bir kural
değil, kaybın nerede toplandığının tarifidir. **Kaba "şoku takip et" sondası kenar göstermedi:** şok çubuğunun kapanışında şok yönünde
giriş, 7646 olay (K=3, 60 dk): PF 0.49, brüt R ≈ 0 (−0.008); K=8 (664 olay): PF 0.58, brüt R ≈ −0.01; iki yönde ve iki yarıda
brüt ≈ 0. Yani çubuk kapandıktan sonra ne devam ne ters yönde ham kenar var; ücretler (≈ 0.37R) farkı net negatife çeviriyor. Ayna fade
sondası da brüt ≈ 0.

Diğer ölçülen sonuçlar: REAPER'ı kısaltma varyantlarının (1-6 sa) biri dışında hepsi kaybettiriyor (4 sa: −171 USDT; tek istisna
+9 USDT, gürültü düzeyi); erken break-even
ratchet'ı kayıpları düzeltmiyor; mevcut SL gerçekleşmesi temiz (kayma ort. +%0.02, Eylül-Kasım SL'leri, n=42; kayıp tasarım, yürütme değil); aynı yönde eşzamanlı
çok pozisyon kendi başına zarar göstermedi (kayıp, KÜMELENMİŞ stop günlerinde).

## 7. Görülmemiş veride testler

| Test | Pencere | Sonuç | Ayrıntı |
|---|---|---|---|
| Doğrulama penceresi, C | VAL-2026Q4a 09-04..10-15 | 231 işlem, WR %67.1, **PF 0.58**, net −8631; GA PF [0.34, 0.98] | E21 |
| Aynı tarihler, canlı C | id 337-528 | 174 işlem, WR %53.4, PF 0.41 | E21 |
| Harness-canlı farkı | | kazanma oranında 9-19 puan iyimser; TP1'e ulaşan %66 vs %54; çıkış başına ROI ±1 içinde | E21 |
| Kurulum sayımı | 7 standart pencere | A 0.79 / B 0.81 / D 0.84 / E 0.80 PF; denge filtresi kapalıyken de negatif; LONG 10 koşunun hepsinde negatif | E20 |
| Koruma adayları K1..K8 | 2026-01-23..08-21, 191 gün | taban PF 0.85; en iyi p 0.143, q 0.349; hiçbiri geçmedi | E22 |
| Ön-kayıtlı çıkış deneyi (eski model; main) | 2025, 8 taze pencere (V1..V8) | 1619 işlem, PF **0.773**; 11 çıkış varyantından 0'ı geçti | E16 |
| Ön-kayıtlı giriş zaman dilimi deneyi (eski model; main) | 2025'in tamamı | altı kolun net PF 0.42-0.71, komisyonsuz bile < 1; canlı 1m ayarı 3370 işlem, PF 0.777 | E17 |

Mevcut boyutlamada (marj %10, stop −%5 sermaye) 90 günlük yollarda tepeden ≥ %50 düşüş olasılığı: harness havuzu 0.36 (7 pencere) /
0.65 (7 pencere + VAL); canlı defter C için 0.86 (E21). Bu üç değer bileşik boyutlama modelindendir; toplamsal modelde aynı
sırayla 0.39 / 0.68 / 0.96.

## 8. Hüküm

- Strateji C'nin örneklem-dışı kenarı GÖSTERİLEMEDİ: örneklem-dışı kanıt yalnız doğrulama penceresi VAL-2026Q4a (PF 0.58) ve
  canlı testnet defteri (PF 0.43); ikisi de negatif. Kesintisiz geçmişte (örneklem-içi sayılır) PF 0.85.
- Yanık 7 pencerede (örneklem-içi) A, B, D, E'nin hiçbiri kârlı değil (PF 0.76-0.84; yön kırılımında en iyiler B SHORT PF 1.00
  ve D SHORT PF 1.02, başabaş); kayıp-örüntü korumaları kabul kuralını geçmedi.
- Aynı sonuca E16 ve E17 de bağımsız olarak, ön-kayıtlı tasarımlarla ve 2025 verisinde (eski model) vardı.
- Hiçbir ayar değişmez; mainnet NO-GO teyit (D43, `docs/MAINNET_PLAN.md`).

## 9. Yapılmayanlar ve doğrulanmayanlar

- Harness-canlı TP1 farkının NEDENİ ölçülmedi (gecikme, testnet fiyat tabanı, TV slotları, %87 maker dolum varsayımı: hipotez).
- Mühürlü `SEAL-2026Q4b` açılmadı.
- Bu denetim, çekilmemiş (fetch yapılmamış) eski bir yerel main'den başladı; E16/E17 sonradan birleştirildi.
- E16/E17 gerçekçi modelde yeniden koşulmadı (2025 verisi yerel önbellekte yok).
- VAL-2026Q4a, E16'nın CANLI / E17'nin FINAL penceresiyle örtüşür (EXPERIMENTS E21 örtüşme notu).
- E22'da 19/210 gün kline önbelleği deliği nedeniyle kapsanmadı; E22 gücü sınırlı (asgari tespit edilebilir fark ≈ 5300-17 500).
  Delikler sonradan dolduruldu (önbellek 2026-01-23..11-18 arası 8 sembolde kesintisiz; dosya sayısı 409 → 508); E22 bu 19 günle
  YENİDEN KOŞULMADI.
- Canlı defter bulguları örneklem-içi ve çoklu kesit taramasından; ileri-yönlü doğrulama yok. Aynı örneklem tek rejim parçalarına
  (Ağustos rallisi, Ekim-Kasım düşüşü) yaslanıyor.
- Forensics analizlerinin bir kısmı bağımsız yeniden hesapla tekrarlandı; bir kısmı (rastgele giriş ızgarası, ileri-sürüklenme tabanı,
  çıkış sonrası hareket) tekrarlanmadı. Bir bulgu bağımsız hesapla uyuşmadı (TP1 sonrası artık: 3.66 vs 4.08 ROI).
- `/health` mandalının kod düzeltmesi yapılmadı (yeniden başlatma bayrağı temizler). TV sessizliğinin sunucu tarafı incelendi
  (bölüm 5); TradingView'in Alarmlar paneli açılmadı, alarmlar yeniden kurulmadı.
- Test paketi (origin/main birleştirildikten sonraki son ağaç): depoda 4046 geçti / 85 atlandı; temiz kopyada sunucunun secret olmayan
  ayarlarıyla (`scripts/preflight_server_env.sh awa`, 66 ayar satırı) 4045 geçti / 86 atlandı; temiz kopyada yalnız `env.example` ile
  4045 geçti / 86 atlandı. Atlananlar isteğe bağlı testlerdir (E17'nin yavaş betik testleri, ağ ve testnet testleri; kopyada `.git`
  olmadığı için bir test daha). Birleştirmeden önce ilk sunucu-ayarlı koşuda iki test kalmıştı (yapılandırmaları `.env`'den stop modunu
  ve dinamik kaldıracı devralıyordu); düzeltildi (D43 "Deploy öncesi ön sınav").

## 10. Açık araştırma yönleri

Hepsi hipotez; her biri kullanılmadan önce walk-forward / P2-v2 ile yargılanır.

1. Trend-yönlü (with-trend) giriş ailesi. 4 saatlik rejim tanımıyla trendle aynı yönlü işlemler (UP-LONG + DOWN-SHORT, n=308)
   PF 1.29, +340; kârın tamamı Ağustos UP-LONG'undan. EMA50/200 farkı tanımıyla trendle aynı yönlü işlemler (n=223) ise −550. İki
   tanım TERS sonuç veriyor: 'trend yönlü giriş daha iyi' kanıtı sağlam değil, hipotez düzeyinde. E17'nin "Yorum" 6. maddesi aynı
   ipucunu verir (karşı-trend girişler 15m/30m'de ücretsiz bile güçlü kaybediyor; sınanmadı). E18 numarası ve dokunulmamış
   2023-01-01 → 2024-11-18 verisi yeni bir fikrin ön-kayıtlı sınaması için ayrıldı.
2. Maker (limit) kâr-al emirleri: ücretler brüt kârın %31.5'i; harness çıkış tarafında maker modeli yok.
3. Giriş gecikmesi / kovalama-yok kuralı: kötü maker dolumlar (198 işlem, −525); emrin dolduğunun gözlenmesi medyan 43 sn
   (ort. 61 sn, n=321); sinyalden giriş kaydına medyan 60 sn (ort. 79 sn, n=399).
4. TradingView kaynağını canlandırmak: alarmların yeniden kurulması gerekir (kullanıcı işi: alarm URL'si secret taşır; RUNBOOK
   takvimine göre LuxAlgo aboneliği 2026-11-21'de 'iptal ediliyor' görünüyor). TV-LONG PF 2.09 (n=75), TV-SHORT PF 0.45 (n=49);
   kanıt tek rejime (2026-08-12..09-13) dayanıyor.
5. Korele aynı-yön maruziyet bütçesi (SL zararının %25'i 15 kümeden).
6. Korumalar için gölge mod (ileri-yönlü kanıt; mevcut değil).

## 11. Yeniden üretme

```bash
# Gerçekçi model (varsayılan) ve eski model, aynı pencere; sunucu SCALPER_* env'iyle, ağ kapalı
env <SCALPER_* satırları> .venv/bin/python -m src.strategies.scalper.backtest --strategies C \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,BNBUSDT,ADAUSDT,LTCUSDT \
  --start 2026-01-23 --end 2026-02-13 --cache-dir data/klines_cache [--legacy-model]
# Doğrulama penceresi (E21): --start 2026-09-04 --end 2026-10-16   (önbellek için scripts/walk_forward.ensure_cache)
# Kurulum sayımı (E20): --strategies A|B|D|E  (+ SCALPER_USE_EQUILIBRIUM_FILTER=false)
# Koruma adayları (E22): adayın SCALPER_C_* ayarı + P2-v2 (scripts/autoresearch.py p2v2_*)
.venv/bin/python -m pytest tests/test_walk_forward.py -q -p no:cacheprovider
```

Kanıt klasörü (YEREL, git'te izlenmez; `logs/` .gitignore'da): `logs/audit_20261120/`

| Yol | İçerik |
|---|---|
| `baseline_pre/`, `baseline_post/` | E19 eski / gerçekçi 7 pencere JSON ve metin raporları, `RECIPE.txt`, `timings.txt` |
| `eval_out/census/` | E20 koşuları (`raw/<KOŞU>/`), `analyze.py`, `census.sh`, `batch1.sh` |
| `eval_out/val/` | E21 `VALQ4a.json`, `analyze.py`, `mc.py`, `mc2.py`, çıktılar |
| `eval_out/guards/` | E22 117 koşunun raporları, `analyze.py`, `report.py`, `jobs.sh`, `configs.json`, `segments.json` |
| `legacy_eq/` | eski modelin birebir yeniden üretimi, 7 pencere |
| `attrib/` | özellik başına etki koşuları: AYI, BOĞA, ÇÖKÜŞ |
| `merge_eq/` | birleştirme sonrası eşdeğerlik koşuları: origin/main, birleşik eski model, birleşik gerçekçi model, ölçüm bayrakları |
| `tv_silence/README.txt` | TradingView kaynağının sessizliği: salt-okunur inceleme, secret içermez |
| `run_bt.sh` | koşu çalıştırıcısı (komut içindeki yollar oturum çalışma alanına işaret eder) |
| `audit_result.json`, `impl_result.json`, `forensics_result.json`, `measure_result.json`, `guards_result.json` | denetim, uygulama, canlı defter incelemesi, ölçümler, koruma sınavı |
| `ledger_report_full.md` | canlı defter rejim raporu |

Canlı veritabanı ve sunucu env dosyası kanıt klasörüne KONMADI (gizlilik); canlı defter sayıları bu yüzden yalnız yukarıdaki
raporlardan ve JSON özetlerinden okunur.
