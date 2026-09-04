# Deney defteri — backtest ve ölçüm kayıtları

> ## ⚠️ ÖNCE BUNU OKU — bu defterdeki sayılar OOS DEĞİLDİR (D24, 2026-08-24)
>
> **Olgu.** Bu dosyadaki E2…E9 varyantlarının **TAMAMI** aynı üç pencerede ölçüldü:
> AYI `2026-01-23→02-13`, YATAY `2026-07-01→07-21`, BOĞA `2026-08-07→08-21`.
> Sayılabilir: 29 harf etiketli varyant (`E2a`…`E6e`) + E9'un 7 yapı varyantı
> (S1, S2, S3, S4, S1p3, S1p8, S2p8) = **36 varyant, 3 pencere**. Bu üç pencere
> dışında ölçülmüş **hiçbir** varyantımız yok.
>
> **Bu neden bir sorun?** Aynı pencerede arka arkaya varyant denemek
> *tekrarlı holdout*'tur: her yeni deneme o pencereyi biraz daha bir EĞİTİM
> kümesine çevirir. Canlıya giren D6/`E2a` bu pencerelerde **seçildi** —
> dolayısıyla onun ölçülen kenarı, gerçek beklenen kenarın tarafsız bir
> tahmini değil, bir **ÜST SINIRIDIR**. Elimizde hiç dokunulmamış bir
> doğrulama penceresi **YOKTUR**.
>
> **Bu ne DEMEK DEĞİL.** "Sonuçlar yanlış" ya da "D6 kötü" demek değildir.
> Yalnızca şu demektir: bu tablolardaki hiçbir sayı örneklem-dışı (OOS) bir
> tahmin olarak okunamaz; göreli karşılaştırma için hâlâ geçerlidirler
> (CLAUDE.md "Karar verirken" maddesi zaten göreli farkları şart koşar).
>
> ### Bundan sonrası için önerilen kural: **SAKLI PENCERE**
> (Öneri — bu bölüm bir ÖLÇÜM DEĞİL, bir yöntem kuralıdır. Henüz hiçbir saklı
> pencere koşulmadı ve bu kural henüz bir karara uygulanmadı.)
>
> 1. **Bir kere, önceden, kör seçilir.** Saklı pencere tarihleri BURAYA yazılır
>    ve sonuçlara BAKILMADAN belirlenir. Aday alan: `2026-02-15 → 2026-06-25`
>    — bu aralıkta bugüne kadar hiçbir deney koşulmadı ve `data/klines_cache`
>    ile `data/klines*` altına indirilmedi bile (dosya adları defterde: `klines/`
>    2026-06-25→08-23, `klines_bear/` 2025-12-15→2026-02-15). Rejim eşleşmesi
>    gerekiyorsa üç saklı pencere, YALNIZ BTC günlük getirisinden türeyen
>    önceden yazılmış bir eşikle seçilir — strateji sonucuna asla bakılmaz.
> 2. **Arama sırasında AÇILMAZ.** Parametre araması (`scripts/autoresearch.py`
>    dahil) yalnız üç açık pencerede koşar. Saklı pencerede koşu yapan bir
>    komut, bu dosyada gerekçesiyle kayıtlıysa geçerlidir; kayıtsızsa pencere
>    yakılmış sayılır.
> 3. **Aday başına EN FAZLA BİR kez açılır.** Açık pencerelerdeki karar
>    kuralını (ayıda PF ≥ 1.1 **ve** boğada PnL kaybı ≤ %20) zaten geçmiş bir
>    aday için, tek seferlik doğrulama olarak.
> 4. **Sonuç NE ÇIKARSA ÇIKSIN buraya yazılır** — özellikle başarısızlıklar.
>    Sessizce atlanan bir saklı pencere koşusu, pencereyi ikinci bir eğitim
>    kümesine çevirir; kuralın tek yaptırımı bu kayıttır.
> 5. **Saklı pencere yalnız VETO eder, terfi ETTİRMEZ.** Reddedebilir; tek
>    başına "canlıya al" diyemez. (Fail-closed ilkemizle aynı yön.)
> 6. **Açıldıktan sonra o parametre ailesi için YANIK sayılır**; yeni bir saklı
>    pencere ilan edilmeden aynı aile yeniden doğrulanamaz. Açılma sayacı bu
>    kutuda tutulur — **bugüne kadar: 0**.
> 7. **Çok-varyant taramasında p/q raporlanır.** Tek bir varyantın "kazanması"
>    N denemede beklenen bir olaydır; `--permutations` (D24/A1) p-değerini,
>    `python3 -m src.strategies.scalper.multitest` (D24/A2, Benjamini-Hochberg)
>    q-değerini verir. Bunlar saklı pencerenin yerini TUTMAZ, yalnız şişkinliği
>    görünür kılar.
>
> **Kuralın kendi sınırları (dürüstlük).** (a) Pencerelerimiz 3 hafta; saklı
> pencere de kısa olacağından tek başına istatistiksel güç vermez. (b) Kripto
> rejimi hızlı değişir: farklı bir dönemden seçilen saklı pencere, yalnız
> "aşırı uyum" değil aynı zamanda "rejim genellemesi" de sınar — iki etki
> ayrışmaz ve başarısızlık ikisinden hangisinden geldiğini söylemez.
> (c) Bu kural, dış bir inceleme sırasında bu ölçüm boşluğunun teşhis
> edilmesiyle yazıldı; ilgili depoda lisans YOKTUR, hiçbir metin kopyalanmadı
> — buradaki formülasyon bize aittir.

### Saklı pencere açılışı 1 — D28 TP1 %8 adayı (2026-08-27, SONUÇTAN ÖNCE İLAN)

**Durum: KOŞULDU — ADAY VETO EDİLDİ.** Bu paragraf sonuç verisi indirilmeden
ve komut çalıştırılmadan yazıldı. TP1 ailesinin tek kullanımlık saklı penceresi
`2026-03-01 → 2026-04-01` UTC (`[start,end)`) olarak sabitlendi; aday alanın
(`2026-02-15 → 2026-06-25`) içindedir ve daha önce hiçbir backtest/cache koşusunda
kullanılmamıştır.

Karşılaştırma yalnız `SCALPER_TP1_ROI=10` tabanı ile `=8` adayıdır. İkisinde de:
1m/5m/15m, C-only, fixed stop ROI %50, market day gate %1.3, run gate kapalı,
diverjans açık, 8 majör sembol, marj %1 ve kapasite 5 aynı kalır. **Veto kuralı:**
aday PF < 1.10 ise, aday toplam PnL'i tabandan >%20 düşükse veya aday maxDD'si
tabandan yüksekse RED. Saklı pencere yalnız veto eder; iyi sonuç testnet soak
şartını kaldırmaz. Sonuç ve tam komut bu başlığın altına koşudan sonra, sonucu
ne olursa olsun eklenecek. Bu açılışla sayaç `0 → 1` olur; TP1 ailesi için bu
pencere yeniden kullanılamaz.

**Sonuç (aynı gün, ilan satırı commit edilmeden ama dosyaya yazıldıktan SONRA
koşuldu):** taban TP10 = 291 işlem / WR %85.6 / PnL **−349.87** / PF **0.84** /
maxDD **529.43**; aday TP8 = 317 işlem / WR %89.3 / PnL **−263.11** / PF
**0.85** / maxDD **530.13**. TP8 kaybı azalttı ama PF 1.10 eşiğini geçmedi ve
maxDD tabandan 0.70 daha yüksek kaldı → iki ayrı veto koşuluyla **RED**. Açık
üç penceredeki üstünlük OOS genellenmedi; TP8 testnet ayarına UYGULANMAYACAK.

Komut (iki koşuda yalnız `SCALPER_TP1_ROI=10|8` değişti):
```bash
env <sunucu-SCALPER-paritesi> SCALPER_TF_ENTRY=1m SCALPER_TF_CONTEXT=5m \
  SCALPER_TF_REGIME=15m SCALPER_STOP_MODE=fixed_roi \
  SCALPER_FIXED_STOP_ROI_PCT=50 SCALPER_TP1_ROI=<10|8> \
  SCALPER_MAX_MARGIN_PCT=1 SCALPER_MAX_POSITIONS=5 \
  python3 -m src.strategies.scalper.backtest --strategies C \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,BNBUSDT,ADAUSDT,LTCUSDT \
  --start 2026-03-01 --end 2026-04-01 --cache-dir data/klines_cache
```
Loglar: `.test-logs/d28/hidden_BASE.log`, `.test-logs/d28/hidden_TP8.log`;
JSON: `logs/backtest_20260827_182535.json`,
`logs/backtest_20260827_182641.json`. Bu pencere TP1 ailesi için **YANIKTIR**.

### D29 — Kullanıcı profili U50: TP1 ROI %50 / pozisyonun tamamı (2026-08-28, SONUÇTAN ÖNCE İLAN)

**Durum: KOŞULDU — KÂR PROFİLİ RED; SAKLI PENCERE AÇILMADI.** Kullanıcının kesinleştirdiği tek
aday aranmayacak/taranmayacaktır: 1000 USDT sanal sermayede işlem başına
yaklaşık 100 USDT marjin ve TP1'de tamamının kapanması. Canlı env karşılığı:
`SCALPER_MAX_MARGIN_PCT=10`, `SCALPER_TP1_ROI=50`,
`SCALPER_TP1_FRACTION=1.0`, `SCALPER_TP2_FRACTION=0.0`. Sabit stop ROI %50
olduğundan teorik brüt sonuç TP veya SL'de yaklaşık `+50 / -50 USDT`'dir;
komisyon sonrası oran 1:1'den biraz kötüdür. Günlük %1 kayıp kilidi ilk
`-50` USDT stopu önceden engelleyemez; yalnız gerçekleşen kayıptan SONRA yeni
girişleri durdurur. Bu nedenle U50, kanıt çıksa bile **yalnız testnet** adayıdır.

**A/B karşılaştırması.** B10 tabanı da marjin %10 ile koşulur; yalnız çıkış
ekonomisi farklıdır: B10 = TP1 ROI10/fraksiyon0.40 + TP2 ROI25/fraksiyon0.20
ve runner0.40; U50 = TP1 ROI50/fraksiyon1.00 + TP2 fraksiyon0. Aynı kalan
ayarlar: 1m/5m/15m, C-only, fixed stop ROI50, market day gate %1.3, run gate
kapalı, diverjans açık, 8 majör sembol, kapasite5, maker timeout3 giriş mumu,
max-hold/reaper8 saat, ücret ve kayma modeli aynı. Harness koşudan ÖNCE iki
canlı-parite düzeltmesi taşımalıdır: maker bekleme süresi gerçek giriş
zaman-dilimine göre hesaplanmalı (1m × 3 = 180 sn; sabit 900 sn değil) ve TP1
görmeyen pozisyonun 8 saatlik REAPER kapanışı modellenmelidir.

**Açık aşama ve önceden kilitli veto:** AYI `2026-01-23→02-13`, YATAY
`2026-07-01→07-21`, BOĞA `2026-08-07→08-21`. U50 şu koşullardan herhangi
birinde kâr profili olarak RED: herhangi bir pencerede PF < 1.00; üç pencere
birleşik PF < 1.10; birleşik net PnL ≤ 0; ya da tek-pencere maxDD başlangıç
sermayesinin %20'sini aşarsa. Ayrıca U50'nin birleşik PF'si B10'dan düşükse
"büyük hedef küçük kazanç sorununu çözdü" denemez. Harness başlangıç bakiyesi
10,000 USDT ve marjin %10 olduğundan nominal sonuçlar kullanıcının 1000 USDT
profili için yaklaşık 10'a bölünerek okunur; PF/WR değişmez.

**Saklı aşama (yalnız açık aşama geçerse):** `2026-04-01→2026-05-01` UTC
(`[start,end)`) D29/U50 için sonuç görülmeden seçildi; defter/log/cache aramasında
bu pencereye ait önceki koşu bulunmadı. Açık veto geçilmezse bu pencere
AÇILMAYACAK ve yanmayacaktır. Açılırsa yalnız veto eder: PF < 1.10, net PnL ≤0
veya maxDD > başlangıç sermayesinin %20'si RED; sonuç ne olursa olsun buraya
yazılır. Hiçbir backtest sonucu 5+ günlük canlı testnet soak ve gerçek fill
ekonomisi doğrulamasının yerini tutmaz.

**Açık aşama sonucu (aynı gün):** Harness önce 1m maker timeout paritesi
(3 mum = 180 sn) ve max-hold8/REAPER modeliyle düzeltildi; ilgili birim testler
ve tüm paket (`2561 passed, 2 skipped`) geçti. Env kaynağı AWA
`/opt/tradingbot-v2/.env` içindeki hassas olmayan `SCALPER_*` snapshot'ıdır;
iki profilde yalnız aşağıdaki exit değişkenleri ve ortak marjin %10 override'ı
uygulandı.

| Profil / pencere | İşlem | WR | Net PnL | PF | maxDD | REAPER | Tam TP |
|---|---:|---:|---:|---:|---:|---:|---:|
| B10 AYI | 161 | %86.3 | +6687.65 | 2.07 | 1599.77 | 12 | 0 |
| B10 YATAY | 156 | %76.3 | +4812.62 | 1.92 | 848.21 | 31 | 0 |
| B10 BOĞA | 118 | %69.5 | +1585.95 | 1.37 | 1227.11 | 43 | 0 |
| U50 AYI | 134 | %58.2 | +9649.63 | 1.71 | **2146.65** | 82 | 34 |
| U50 YATAY | 127 | %49.6 | +1957.68 | 1.22 | **2098.67** | 108 | 8 |
| U50 BOĞA | 108 | %58.3 | +6661.28 | 2.29 | **2373.31** | 89 | 13 |

Birleşik B10: 435 işlem / 340W / net +13086.21 / PF **1.829** / 86 REAPER.
Birleşik U50: 369 işlem / 204W / net +18268.60 / PF **1.658** / 279 REAPER /
55 tam TP. Yani U50 nominal neti büyüttü fakat kaliteyi düşürdü: PF tabandan
düşük, işlemlerin %75.6'sı TP'ye ulaşmadan REAPER oldu ve üç pencerenin
üçünde de önceden kilitlenen 2000 USDT (%20) maxDD tavanını aştı. 1000 USDT
kullanıcı ölçeğinde yaklaşık maxDD'ler 214.67 / 209.87 / 237.33 USDT'dir
(harness 10,000 başlangıcının 10'a ölçeklenmiş okuması). Bu iki bağımsız veto
nedeniyle U50 **kâr profili olarak RED**; `2026-04-01→05-01` saklı penceresi
AÇILMADI ve yanmadı. Kullanıcının açık talebi nedeniyle yalnız TESTNET'te
kontrollü gerçek-fill deneyi yapılabilir; bu sonuç MAINNET terfisi değildir.

Komut kalıbı (B10/U50 ve üç pencere sırayla; paralel koşulmadı):
```bash
env <awa-.env-hassas-olmayan-SCALPER-snapshot> \
  SCALPER_MAX_MARGIN_PCT=10 SCALPER_MAX_HOLD_HOURS=8 \
  SCALPER_TP1_ROI=<10|50> SCALPER_TP1_FRACTION=<0.40|1.0> \
  SCALPER_TP2_ROI=<25|100> SCALPER_TP2_FRACTION=<0.20|0.0> \
  python3 -m src.strategies.scalper.backtest --strategies C \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,BNBUSDT,ADAUSDT,LTCUSDT \
  --start <pencere-başı> --end <pencere-sonu> --cache-dir data/klines_cache
```
JSON logları sırayla B10 AYI/YATAY/BOĞA:
`logs/backtest_20260828_152748.json`, `logs/backtest_20260828_152920.json`,
`logs/backtest_20260828_153030.json`; U50 AYI/YATAY/BOĞA:
`logs/backtest_20260828_153146.json`, `logs/backtest_20260828_153316.json`,
`logs/backtest_20260828_153420.json`.

Kural: bir satırın kanıt sayılması için **komut + pencere + env kaynağı + log yolu** gerekir.
Harness ≥ 7640c0a (kapı-pariteli). Pencereler: AYI 2026-01-23→02-13 · YATAY 07-01→07-21 · BOĞA 08-07→08-21.
Komut kalıbı:
```bash
env $(ssh awa grep ^SCALPER_ /opt/tradingbot-v2/.env | xargs) <VARYANT=deger> python3 -m src.strategies.scalper.backtest \
  --strategies C --symbols BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,BNBUSDT,ADAUSDT,LTCUSDT --start <YYYY-MM-DD> --end <YYYY-MM-DD>
```
Sıralı koş (paralel = 429). ~4 dk/koşu (8 sembol, 3 hafta).

## 2026-08-21 — rejim referansları (baz = canlı env, divergence=false iken)
| Pencere | İşlem | WR | PnL | PF | maxDD | LONG (n/PnL/PF) | SHORT (n/PnL/PF) | SL n/ort | TRAIL n/ort |
|---|---|---|---|---|---|---|---|---|---|
| AYI kapı açık | 814 | 84.6 | −2042 | 0.97 | 11857 | 259/−3732/0.83 | 555/+1689/1.04 | 120/−514 | 687/+88 |
| AYI kapı KAPALI | 1171 | 80.8 | −36506 | 0.68 | 41691 | 636/−37814/0.49 | 535/+1308/1.03 | 220/−514 | 944/+82 |
| YATAY | 449 | 84.4 | −2289 | 0.93 | 8254 | 257/+3047/1.19 | 192/−5336/0.71 | 67/−514 | 375/+86 |
| BOĞA | 191 | 88.0 | +2798 | 1.24 | 3610 | 101/+3182/1.69 | 90/−385/0.95 | 23/−514 | 168/+87 |
Loglar: oturum scratchpad `W_BEAR_gate_on.log`, `W_BEAR_gate_off.log`, `W_FLAT.log`, `W_BULL.log`; özet `regime_backtest.md`.
Okuma: başabaş WR ≈ %85.4; kapı ayıda ~34.5k kurtarıyor; kayıp daima ters-trend tarafta.

## 2026-08-21 — E2/E3 varyantları (tek değişiklik, 3 pencere)
| Varyant | AYI PF/PnL | BOĞA PF/PnL | YATAY PF/PnL | Karar |
|---|---|---|---|---|
| E2a `SCALPER_C_REQUIRE_DIVERGENCE=true` | 1.06 / +886 | 2.18 / +3831 | 1.33 / +2745 | ✅ CANLI (D6) |
| E2b `SCALPER_C_REQUIRE_FLOW_CONFIRM=true` | 1.19 / +2933 | 1.50 / +2610 | 0.81 / −2996 | aday → E2ab ile test edildi, reddedildi |
| E2ab divergence+flow_confirm | 3.35 / +1498 (31 işlem) | 0.85 / −243 (22) | 0.78 / −692 (34) | ❌ aşırı filtreleme (loglar E2ab_*.log) |
| E2c `SCALPER_C_REQUIRE_REVERSAL_ZONE=true` | 0.68 / −5596 | 2.03 / +2636 | 0.75 / −2916 | ❌ |
| E2d RSI 25/75 | 0.96 / −1493 | 1.34 / +2613 | 0.85 / −4478 | ❌ |
| E3a `SCALPER_FIXED_STOP_ROI_PCT=30` | 0.91 / −6108 | 1.28 / +4092 | 0.91 / −3894 | ❌ SL 120→224 |
| E3b `SCALPER_TF_REGIME=4h` | 1.02 / +1227 | 1.00 / −63 | 0.79 / −9090 | ❌ |
| E3c `SCALPER_C_ALLOWED_REGIMES=DOWN,UP` | 1.03 / +1263 | 1.24 / +2567 | 0.79 / −7170 | ❌ |
| E3d E3a+E3b | 0.93 / −4148 | 1.04 / +702 | 0.81 / −10346 | ❌ |
Loglar: `E<id>_<pencere>.log` (24 dosya), özet `regime_experiments.md`. E2a detay: işlem 216/96/150, maxDD 3574/735/3181.

## 2026-08-21 — canlı defter rejim analizi (7-21 Ağu, testnet)
BTC günlük rejim (UP>+1.5%, DOWN<−1.5%): UP 4 gün LONG 76 işlem %89 +652 / SHORT 13 %23 −88;
FLAT 11 gün LONG 40 %52 +90 / SHORT 42 %50 +179 (+268'i tek günden); DOWN 0 gün.
7-20 Ağu = son 240 günün en iyi 14 günü (+%13.5). Kalıp: `scalp_trades` × Binance 1d klines.

## 2026-08-21 — TradingView / LuxAlgo ölçümleri (TV Desktop MCP, 5m, son ~1 ay, komisyonsuz)
- Varsayılan backtester PF: OSC ETH 2.48 · BTC 1.89 · XRP 2.20 | S&O ETH 2.30 · XRP 2.25 · BTC 1.91 · SOL 1.78 · BNB 0.91 | PAC XRP 7.86 · ETH 2.01.
- S&O sensitivity haritası (dashboard): ETH/XRP 16/16 pozitif; optimum sembol+TF'ye göre kayar (BTC 15m→10, 5m→13).
- 13 LUCID script'i (Discord + LuxAlgo AI): en iyi D6-BNB 2.26, S3-ETH 1.73, D1-ETH 1.53 — hiçbiri yalın toolkit'i geçmedi. Dosyalar: `lucid_bench_results.md`, `discord_bench_results.md`.
- AlgoPro V1.6: repaint yok; panel TP1 kazanma ort %40.5 (RR 1). `algopro_v16_audit.md`.

## 2026-08-21 — Altın (golden) backtest regresyonu (offline, deterministik)

Amaç: `src/strategies/scalper/backtest.py` motorunu (simulate_symbol →
open_position → manage_position) AĞSIZ ve sabit bir sonuca kilitlemek —
`tests/test_golden_backtest.py`, davranış değişince (kasıtlı ya da kaza)
kırılır. Kod: `src/strategies/scalper/kline_cache.py` (yeni, `--cache-dir`/
`--refresh` CLI bayrakları backtest.py'ye eklendi) + `tests/fixtures/klines/`
(6 dosya, ~104 KB, gzip JSON, BTCUSDT+ETHUSDT × 5m/15m).

Fixture penceresi: 2026-08-07→2026-08-10 UTC (3 gün, `[start,end)`).
Yalnız 5m (giriş) ve 15m (bağlam VE rejim) serileri gerekir — golden
config `SCALPER_TF_REGIME=15m` kullandığından rejim serisi de "15m"
anahtarını paylaşır (bkz. `gather_symbol_data`'nın aralık-adıyla
anahtarlanan sözlüğü); 1m HİÇ kullanılmaz (dolum `candles_5m[idx].open`
ile yapılır, tik-bazlı değil).

Sabit ayarlar (`tests/test_golden_backtest.py::_GoldenCfg` — .env OKUNMAZ,
`src.core.config.settings` tekil nesnesi monkeypatch ile ayrıca sabitlenir
çünkü StrategyC bunu cfg'den değil doğrudan global'den okur):
strategies=C · `SCALPER_STOP_MODE=fixed_roi` · `SCALPER_FIXED_STOP_ROI_PCT=50`
· `SCALPER_DYNAMIC_LEVERAGE=true` (3-20) · `SCALPER_CHANDELIER_ATR_MULT=3.5`
· `SCALPER_TP1_ROI=10` `SCALPER_TP2_ROI=25` `SCALPER_TP2_FRACTION=0.20`
· `SCALPER_REGIME_FILTER=true` · `SCALPER_TF_REGIME=15m` ·
`SCALPER_C_RSI_LONG_MAX=30` `SCALPER_C_RSI_SHORT_MIN=70` ·
`SCALPER_C_REQUIRE_DIVERGENCE=true` · `SCALPER_LOSS_COOLDOWN_MINUTES=60` ·
`SCALPER_STOP_ATR_FLOOR_MULT=0.5` (yalnız kayıt — fixed_roi modunda inert)
· `SCALPER_MAX_HOLD_HOURS=8` (yalnız kayıt — backtest harness'i max-hold
hiç UYGULAMAZ, yalnız canlı engine.py'de var) · sembol BTCUSDT,ETHUSDT.

**Bulgu (raporlanmalı, gerçek `.env` ile doğrulanmalı):** `SCALPER_MIN_RR`
sınıf varsayılanı (1.2) bu kombinasyonla (tp1=10/tp2=25/tp2_frac=0.20,
fixed_roi=50) KULLANILAMAZ — `open_position()`'ın RR kapısı `fixed_roi`
modunda `sl_risk_roi`'yi HER ZAMAN tam `SCALPER_FIXED_STOP_ROI_PCT`'e
sadeler (kaldıraç iptal olur), yani `rr = (10×0.8+25×0.20) / 50 = 13/50 =
0.26` — piyasa verisinden bağımsız SABİT bir değer. 1.2 eşiğiyle HİÇBİR
C sinyali (ne backtest'te ne canlıda) asla geçemez; bu CLAUDE.md'nin
belgelediği canlı işlem geçmişiyle (başabaş WR ≈%85) çelişir. Golden test
`SCALPER_MIN_RR=0.0` (kapı kapalı) varsayımıyla yazıldı — gerçek sunucu
`.env`'i kontrol edilip ya bu not güncellenmeli ya da `docs/DECISIONS.md`'ye
girilmeli.

**Altın değerler** (ETHUSDT bu dar 3 günlük pencerede hiç ham sinyal
üretmedi — RSI 30/70 + BB taşması + zorunlu diverjans üçlüsü hiç üst üste
binmedi; hata DEĞİL, gerçek piyasa verisi):

| Metrik | Değer |
|---|---|
| Toplam işlem | 2 (ikisi de BTCUSDT) |
| Net PnL | +26.77 |
| Yön | LONG 2 |
| Çıkış nedeni | TRAIL 2 |
| Kapı reddi | `regime_gate` 4 (rejime ters C sinyali) |
| Koşum süresi | ~2 sn (3 test, AĞSIZ) |

Doğrulama: `python3 -m pytest tests/test_golden_backtest.py -q` iki ayrı
süreçte birebir aynı sonucu verdi (determinism testi ayrıca fingerprint
karşılaştırır). Harness'te bulunan/gerekli nondeterminizm düzeltmesi:
YOK — `simulate_symbol` zincirinde (`build_context`, `indicators.py`,
`regime.py`) `time.time()`/`datetime.now()`/rastgelelik/sıralamaya-duyarlı
set yok; tek zaman kaynağı `data.py`'nin `KlineFetcher._drop_unclosed`'ı,
golden test ona hiç dokunmuyor (fixture zaten kapanmış mumlar).

## Eski (kapı ÖNCESİ harness — yalnız yön bilgisi için; mutlak sayılar geçersiz)
2026-08-19/21 sweep'leri: chandelier 2.5→3.5 (+), TP1 15 (−), TP2 40 (nötr), runner %40 (+), strateji D (−), 50x (−−).

## 2026-08-21 — Autoresearch (scripts/autoresearch.py)
Kaynak: `python3 scripts/autoresearch.py` — otomatik uretildi, elle duzenlemeyin.
| Varyant | Pencere | Islem | WR% | PnL | PF | maxDD | Karar |
|---|---|---|---|---|---|---|---|
| E4a | AYI | 345 | 83.2 | -5960.31 | 0.79 | 8752.80 | - |
| E4a | YATAY | 237 | 86.1 | +2009.74 | 1.13 | 2584.77 | - |
| E4a | BOGA | 119 | 90.8 | +4523.75 | 1.85 | 1448.26 | - |
| E4a | KARAR | - | - | -6888.98 | - | - | REDDEDILDI (AYI PF 0.79<1.1 ve AYI/YATAY birlikte iyilesmedi) |
| E4a | hipotez | Gevsek RSI esigi (35/65) daha fazla ve daha erken sinyal yakalar; asiri filtrelemeyi hafifletir. | | | | | |
| E4b | AYI | 216 | 85.6 | +1134.34 | 1.07 | 3604.81 | - |
| E4b | YATAY | 154 | 87.0 | +2891.65 | 1.35 | 3169.06 | - |
| E4b | BOGA | 95 | 92.6 | +3867.42 | 2.20 | 724.00 | - |
| E4b | KARAR | - | - | +431.26 | - | - | ADAY (AYI&YATAY PnL birlikte iyilesti) |
| E4b | hipotez | Chandelier carpanini 3.0'a daraltmak stop'u sikilastirir, trail kazancini erken kilitler. | | | | | |
| E4c | AYI | 216 | 85.6 | +640.40 | 1.04 | 3743.00 | - |
| E4c | YATAY | 150 | 86.7 | +2811.15 | 1.34 | 3105.95 | - |
| E4c | BOGA | 95 | 92.6 | +4317.10 | 2.33 | 720.97 | - |
| E4c | KARAR | - | - | +306.49 | - | - | REDDEDILDI (AYI PF 1.04<1.1 ve AYI/YATAY birlikte iyilesmedi) |
| E4c | hipotez | Chandelier carpanini 4.0'a genisletmek trail'e daha fazla alan birakir, erken cikislari azaltir. | | | | | |
| E4d | AYI | 216 | 85.6 | +951.37 | 1.06 | 3728.66 | - |
| E4d | YATAY | 150 | 86.7 | +2717.42 | 1.33 | 3165.33 | - |
| E4d | BOGA | 96 | 92.7 | +3830.44 | 2.18 | 734.59 | - |
| E4d | KARAR | - | - | +37.08 | - | - | REDDEDILDI (AYI PF 1.06<1.1 ve AYI/YATAY birlikte iyilesmedi) |
| E4d | hipotez | TP2 hedefini 20'ye dusurmek runner payini daha sik gerceklestirir. | | | | | |
| E4e | AYI | 216 | 85.6 | +884.75 | 1.06 | 3549.39 | - |
| E4e | YATAY | 150 | 86.7 | +2776.61 | 1.33 | 3171.59 | - |
| E4e | BOGA | 96 | 92.7 | +3845.53 | 2.19 | 734.59 | - |
| E4e | KARAR | - | - | +44.74 | - | - | REDDEDILDI (AYI PF 1.06<1.1 ve AYI/YATAY birlikte iyilesmedi) |
| E4e | hipotez | TP2 hedefini 30'a yukseltmek runner'a daha fazla alan birakir. | | | | | |
| E4f | AYI | 229 | 89.1 | +1572.66 | 1.13 | 2604.45 | - |
| E4f | YATAY | 162 | 89.5 | +2744.44 | 1.40 | 2153.57 | - |
| E4f | BOGA | 104 | 95.2 | +4203.43 | 2.90 | 514.25 | - |
| E4f | KARAR | - | - | +1058.37 | - | - | ADAY (AYI PF=1.13) |
| E4f | hipotez | TP1 hedefini 8'e dusurmek break-even'e daha erken gecer, whipsaw'da SL'den korur. | | | | | |
| E4g | AYI | 212 | 84.0 | +1909.73 | 1.11 | 4106.57 | - |
| E4g | YATAY | 140 | 85.0 | +3578.56 | 1.39 | 2468.54 | - |
| E4g | BOGA | 85 | 88.2 | +3687.24 | 1.77 | 1247.03 | - |
| E4g | KARAR | - | - | +1713.37 | - | - | ADAY (AYI PF=1.11) |
| E4g | hipotez | TP1 hedefini 12'ye yukseltmek erken kismi kar alimini geciktirir, runner payini buyutur. | | | | | |
| E4h | AYI | 216 | 85.6 | +885.60 | 1.06 | 3574.40 | - |
| E4h | YATAY | 150 | 86.7 | +2745.14 | 1.33 | 3180.54 | - |
| E4h | BOGA | 96 | 92.7 | +3831.41 | 2.18 | 734.59 | - |
| E4h | KARAR | - | - | +0.00 | - | - | REDDEDILDI (AYI PF 1.06<1.1 ve AYI/YATAY birlikte iyilesmedi) |
| E4h | hipotez | Maksimum pozisyon sayisini 3'e sabitlemek (sunucu farkliysa) slot rekabetini azaltir. | | | | | |
| E4i | AYI | 160 | 88.8 | +5624.12 | 1.68 | 3002.71 | - |
| E4i | YATAY | 84 | 83.3 | +1073.09 | 1.20 | 1421.00 | - |
| E4i | BOGA | 55 | 92.7 | +3552.58 | 3.22 | 590.14 | - |
| E4i | KARAR | - | - | +0.00 | - | - | REDDEDILDI (asiri filtreleme (islem<60: ['BOGA'])) |
| E4i | hipotez | Dinamik kaldirac tavanini 20'den 10'a dusurmek stop mesafesini buyutup gurultu stop'unu azaltir. | | | | | |
| E4j | AYI | 201 | 87.1 | +3292.72 | 1.27 | 3953.33 | - |
| E4j | YATAY | 124 | 84.7 | +1185.88 | 1.14 | 1883.94 | - |
| E4j | BOGA | 68 | 89.7 | +2353.05 | 1.74 | 1057.14 | - |
| E4j | KARAR | - | - | -630.52 | - | - | REDDEDILDI (BOGA PnL kaybi >%20) |
| E4j | hipotez | Dinamik kaldirac tavanini 20'den 15'e dusurmek orta yol olarak stop mesafesini biraz buyutur. | | | | | |
| E4k | AYI | 190 | 85.8 | +855.43 | 1.07 | 3696.49 | - |
| E4k | YATAY | 137 | 85.4 | +1600.60 | 1.19 | 3482.42 | - |
| E4k | BOGA | 87 | 93.1 | +3430.98 | 2.11 | 734.59 | - |
| E4k | KARAR | - | - | -1575.14 | - | - | REDDEDILDI (AYI PF 1.07<1.1 ve AYI/YATAY birlikte iyilesmedi) |
| E4k | hipotez | Baglam TF'sini 15m'de sabitlemek (sunucu farkliysa) sinyal baglamini rejim TF'sinden ayristirir. | | | | | |
| E5a | AYI | 186 | 89.8 | +2937.02 | 1.33 | 3094.20 | - |
| E5a | YATAY | 117 | 87.2 | +1409.71 | 1.24 | 959.67 | - |
| E5a | BOGA | 67 | 89.6 | +1218.40 | 1.39 | 1160.12 | - |
| E5a | KARAR | - | - | -1897.02 | - | - | REDDEDILDI (BOGA PnL kaybi >%20) |
| E5a | hipotez | TP1 %8 + kaldirac tavani 12: erken BE + genis stop — ayi dayanikliligi, boga islem sayisi korunur mu? | | | | | |
| E5b | AYI | 204 | 88.7 | +1406.30 | 1.13 | 4522.34 | - |
| E5b | YATAY | 141 | 88.7 | +2312.85 | 1.36 | 2047.77 | - |
| E5b | BOGA | 87 | 92.0 | +2100.32 | 1.66 | 737.55 | - |
| E5b | KARAR | - | - | -1642.69 | - | - | REDDEDILDI (BOGA PnL kaybi >%20) |
| E5b | hipotez | TP1 %8 + kaldirac tavani 15: E5a'nin daha az kisitli hali. | | | | | |
| E5c | AYI | 177 | 87.6 | +3725.78 | 1.35 | 3582.47 | - |
| E5c | YATAY | 108 | 85.2 | +1771.05 | 1.28 | 1227.05 | - |
| E5c | BOGA | 60 | 86.7 | +1617.87 | 1.44 | 1276.94 | - |
| E5c | KARAR | - | - | -347.45 | - | - | REDDEDILDI (BOGA PnL kaybi >%20) |
| E5c | hipotez | Kaldirac tavani 12 tek basina: E4i (10) ile E4j (15) arasi — boga islem sayisi >=60 kalir mi? | | | | | |
| E5d | AYI | 229 | 89.1 | +1488.87 | 1.12 | 2549.93 | - |
| E5d | YATAY | 163 | 89.6 | +2719.06 | 1.40 | 2127.93 | - |
| E5d | BOGA | 103 | 95.1 | +4239.47 | 2.92 | 514.25 | - |
| E5d | KARAR | - | - | +985.23 | - | - | ADAY (AYI PF=1.12) |
| E5d | hipotez | TP1 %8 + chandelier 3.0: iki bagimsiz adayin birlesimi toplamsal mi? | | | | | |

## 2026-08-22 — Autoresearch (scripts/autoresearch.py)
Kaynak: `python3 scripts/autoresearch.py` — otomatik uretildi, elle duzenlemeyin.
| Varyant | Pencere | Islem | WR% | PnL | PF | maxDD | Karar |
|---|---|---|---|---|---|---|---|
| E4b | AYI | 213 | 85.4 | +823.15 | 1.05 | 3717.82 | - |
| E4b | YATAY | 149 | 87.2 | +2519.27 | 1.30 | 3223.76 | - |
| E4b | BOGA | 90 | 93.3 | +3984.39 | 2.46 | 724.00 | - |
| E4b | KARAR | - | - | +448.48 | - | - | ADAY (AYI&YATAY PnL birlikte iyilesti) |
| E4b | hipotez | Chandelier carpanini 3.0'a daraltmak stop'u sikilastirir, trail kazancini erken kilitler. | | | | | |
| E4f | AYI | 226 | 88.9 | +1415.12 | 1.12 | 2604.45 | - |
| E4f | YATAY | 159 | 89.9 | +2603.52 | 1.38 | 2153.57 | - |
| E4f | BOGA | 101 | 96.0 | +4597.63 | 3.71 | 514.25 | - |
| E4f | KARAR | - | - | +1737.94 | - | - | ADAY (AYI PF=1.12) |
| E4f | hipotez | TP1 hedefini 8'e dusurmek break-even'e daha erken gecer, whipsaw'da SL'den korur. | | | | | |
| E4g | AYI | 205 | 83.4 | +1175.76 | 1.07 | 4106.57 | - |
| E4g | YATAY | 134 | 85.1 | +3014.92 | 1.33 | 2555.06 | - |
| E4g | BOGA | 78 | 91.0 | +4891.83 | 2.51 | 755.33 | - |
| E4g | KARAR | - | - | +2204.18 | - | - | ADAY (AYI&YATAY PnL birlikte iyilesti) |
| E4g | hipotez | TP1 hedefini 12'ye yukseltmek erken kismi kar alimini geciktirir, runner payini buyutur. | | | | | |
| E5c | AYI | 157 | 86.6 | +2646.69 | 1.26 | 3073.82 | - |
| E5c | YATAY | 96 | 84.4 | +710.47 | 1.11 | 1408.39 | - |
| E5c | BOGA | 52 | 86.5 | +1578.66 | 1.50 | 1510.41 | - |
| E5c | KARAR | - | - | +0.00 | - | - | REDDEDILDI (asiri filtreleme (islem<60: ['BOGA'])) |
| E5c | hipotez | Kaldirac tavani 12 tek basina: E4i (10) ile E4j (15) arasi — boga islem sayisi >=60 kalir mi? | | | | | |

## 2026-08-23 — Autoresearch (scripts/autoresearch.py)
Kaynak: `python3 scripts/autoresearch.py` — otomatik uretildi, elle duzenlemeyin.
| Varyant | Pencere | Islem | WR% | PnL | PF | maxDD | Karar |
|---|---|---|---|---|---|---|---|
| E6a | AYI | 190 | 83.7 | +136.30 | 1.01 | 5051.74 | - |
| E6a | YATAY | 128 | 84.4 | -63.93 | 0.99 | 3324.57 | - |
| E6a | BOGA | 80 | 90.0 | +1944.25 | 1.52 | 1029.05 | - |
| E6a | KARAR | - | - | -4861.70 | - | - | REDDEDILDI (AYI PF 1.01<1.1 ve AYI/YATAY birlikte iyilesmedi; BOGA PnL kaybi >%20) |
| E6a | hipotez | Rejim TF'sini 15m->5m'e hizlandirmak donus gunlerinde DOWN'a saatler degil dakikalar icinde gecer; ters-gun LONG'lari keser (bedel: bogada dip alimlari). | | | | | |
| E6b | AYI | 213 | 85.4 | +292.19 | 1.04 | 1841.30 | - |
| E6b | YATAY | 145 | 86.9 | +1196.14 | 1.29 | 1614.42 | - |
| E6b | BOGA | 90 | 93.3 | +1950.83 | 2.43 | 367.29 | - |
| E6b | KARAR | - | - | -3439.16 | - | - | REDDEDILDI (AYI PF 1.04<1.1 ve AYI/YATAY birlikte iyilesmedi; BOGA PnL kaybi >%20) |
| E6b | hipotez | Marj tavani %10->%5: boyutlama dogrusal oldugundan PF/WR AYNI kalmali, PnL ve maxDD yarilanmali (kanit: risk katmani sinyali degistirmez). | | | | | |
| E6c | AYI | 226 | 88.9 | +707.56 | 1.12 | 1302.23 | - |
| E6c | YATAY | 159 | 89.9 | +1301.76 | 1.38 | 1076.79 | - |
| E6c | BOGA | 101 | 96.0 | +2298.81 | 3.71 | 257.13 | - |
| E6c | KARAR | - | - | -2570.20 | - | - | REDDEDILDI (BOGA PnL kaybi >%20) |
| E6c | hipotez | A paketi birlesik: marj %5 + TP1 %8 (D12). Beklenti: D12'nin PF/DD iyilesmesi + yarim olcek. | | | | | |

**E6 okuması (elle, 2026-08-23):** E6b/E6c'nin "REDDEDİLDİ" hükmü P2'nin "BOĞA PnL kaybı ≤ %20"
maddesinin **ölçek** artefaktıdır — marj tavanı %5 boyutlamayı doğrusal yarılar: E6b'de PF
tabanla birebir aynı (AYI 1.04 / YATAY 1.29 / BOĞA 2.43), maxDD yarı (1841/1614/367 vs
3683/3229/735) → negatif kontrol GEÇTİ (risk katmanı sinyali değiştirmez). E6c = E4f (TP1 %8)
yarım ölçekte: PF 1.12/1.38/3.71, DD 1302/1077/257. E6a (`TF_REGIME=5m`) gerçek RED: AYI 1.01,
YATAY 0.99, BOĞA +1944 (−%50). Karar/kapsam: `docs/superpowers/specs/2026-08-22-reversal-day-loss-design.md`.

Aynı koşunun kalan varyantları (E6d/E6e) — yukarıdaki paragraf tabloyu böldüğü için
başlık burada TEKRAR edilir (yalnız biçim; sayılar değişmedi):

| Varyant | Pencere | Islem | WR% | PnL | PF | maxDD | Karar |
|---|---|---|---|---|---|---|---|
| E6d | AYI | 219 | 84.5 | +2664.58 | 1.20 | 2911.99 | - |
| E6d | YATAY | 155 | 83.9 | +1574.64 | 1.17 | 2396.01 | - |
| E6d | BOGA | 97 | 89.7 | +2764.36 | 1.67 | 683.56 | - |
| E6d | KARAR | - | - | +125.25 | - | - | REDDEDILDI (BOGA PnL kaybi >%20) |
| E6d | hipotez | Sabit ROI stop %50->%40: kayip/islem kucultur; E3a (%30) SL sayisini 2x yapmisti, %40 ara nokta. | | | | | |
| E6e | AYI | 231 | 89.2 | +3923.22 | 1.40 | 1936.70 | - |
| E6e | YATAY | 166 | 88.6 | +2888.13 | 1.43 | 1733.67 | - |
| E6e | BOGA | 109 | 92.7 | +3280.18 | 1.99 | 653.34 | - |
| E6e | KARAR | - | - | +3213.20 | - | - | ADAY (AYI PF=1.40) |
| E6e | hipotez | Stop %40 + TP1 %8 birlikte: odeme orani (kazanc/kayip) 0.22'den ~0.25'e, BE daha erken. | | | | | |

## 2026-08-23 — Sinyal otopsisi (E8): hangi girişler yanlıştı, giriş anında nasıl bilinebilirdi?

Salt-okunur analiz (kod DEĞİŞTİRİLMEDİ). Soru kullanıcı kararıyla daraltıldı: boyut/TP1/stop
ayarı YOK; yalnız **giriş sinyali kalitesi**. Yöntem: canlı defterin 202 kapanmış işlemini
giriş zaman damgasından ÖNCE kapanmış mumlardan türetilen 40+ özellikle zenginleştirmek,
kaybeden/kazanan ayrımını ölçmek, tek-eşikli kapıları hem defterde hem harness'ta simüle etmek.

**Veri ve yollar** (hepsi yeniden üretilebilir):
- Defter: `awa:/opt/tradingbot-v2/tradingbot.db` → `scalp_trades` (203 satır, 202 CLOSED,
  2026-08-07 09:20 → 2026-08-22 23:52 UTC). Kopya: scratchpad `signal_autopsy/ledger_raw.json`.
- TV kaynak eşlemesi: `awa:/opt/tradingbot-v2/logs/bot.log` + `bot.log.{1..7}.gz`,
  `✅ Sağlama tamam: SEM YÖN — kaynaklar [...]` satırları (bot.log yerel saat = **UTC+2**;
  doğrulama: sağlama→dolum gecikmesi medyan 75 sn, p90 246 sn — saat kayması olsaydı ±7200 sn olurdu).
  Kopya: `signal_autopsy/tv_votes.log` (4451 satır). 58 TV işleminin **49'u** eşleşti, 9'u "bilinmiyor".
- Piyasa verisi: Binance public `/fapi/v1/klines`, İKİ ayrı çekim —
  `klines/` (mainnet, 2026-06-25→08-23, 1m/5m/15m/1h/1d), `klines_testnet/` (testnet, aynı pencere),
  `klines_bear/` (mainnet, 2025-12-15→2026-02-15, harness AYI penceresi için).
- Script'ler: `signal_autopsy/{fetch_klines,enrich,analyze,run_analysis,rules,enrich_harness}.py`;
  çıktılar `trades_enriched[_testnet].csv/json`, `analysis_report[_testnet].txt`,
  `rules_{mainnet,testnet}.txt`, `harness_rules.txt`, `harness_daygate.txt`.
  Scratchpad kökü: `/private/tmp/claude-501/-Users-max-Downloads-Downloads-TRADINGBOT/7dda3fb5-.../scratchpad/signal_autopsy/`.
- Gösterge matematiği repo'nun kendi saf modüllerinden (`indicators.rsi_series/bollinger/atr/ema`,
  `regime.detect_regime`) — motorla birebir; pencere boyları da motorunkiyle aynı
  (rejim 250×15m, bağlam 100×5m, giriş 150×1m — `engine.py:1167-1172`).

### E8.0 — Veri kaynağı bulgusu: canlı motor TESTNET, harness MAINNET mumu okuyor
`ScalperEngine` `KlineFetcher()`'ı parametresiz kurar → `settings.binance_base_url`
(`data.py:60-61`, `engine.py:129`) = **testnet** (`.env`: `BINANCE_BASE_URL=https://testnet.binancefuture.com`).
Harness ise mainnet'e sabitlenmiştir (`backtest.py:1309` `base_url="https://fapi.binance.com"`).
Testnet 1m mumları neredeyse durağan: 2026-08-22 23:50-23:53 BTCUSDT kapanışları
testnet `77079.20 / 77079.20 / 77079.10` (hacim 3.6 / 1.5) — mainnet `77108.0 / 77111.8 / 77073.8`
(hacim 39.0 / 21.0). Sonuçlar:
- Motorun `signal_reason`'a yazdığı giriş RSI'ı (143 C işlemi) **testnet** 1m serisiyle uyuşuyor
  (medyan |Δ| **2.8**, %52'si ≤3), mainnet 1m ile uyuşmuyor (medyan |Δ| **7.4**, %18'i ≤3).
  Gecikme taraması (0–30 dk) en iyi uyumu 0–1 dk'da veriyor → zamanlama doğru, fark veri kaynağı.
- **Hacim tabanlı hiçbir özellik iki taraf arasında taşınmaz**: `vol_ratio_5m` korelasyon
  r = **−0.04**, testnet ortalaması 206 (mainnet 1.73). Hacim kapısı önerilmemeli.
- Makro/orta-vade özellikler taşınır (mainnet↔testnet Pearson r): BTC gün-açılışı sapması
  **0.998**, BTC 3g koşu **1.000**, RSI(15m) **0.975**, RSI(5m) **0.953**, ATR persentili **0.955**,
  EMA50 mesafesi (ATR birimi) **0.883**. Bu yüzden aşağıdaki kurallar bu ailelerden seçildi.
- Fiyat SEVİYESİ testnet≈mainnet: defterdeki dolum fiyatı ile mainnet 5m kapanışı arasında
  medyan sapma %0.054 (n=199; >%1 sapan yalnız 4 satır: 3× BEATUSDT + XRP #199).

**Parite kontrolü (rejim kapısı D5).** Motorun kuralı "DOWN'da LONG / UP'ta SHORT yasak".
Testnet-pariteli yeniden hesapta 25/202 satır kuralı ihlal ediyor görünüyor; hepsi açıklanıyor:
**20'si** kapının canlıya alınmasından (2026-08-16, `engine.py` yorumu) ÖNCE; kalan 5'ten
2'si (#91 LTC, #92 SOL, 08-18) TV muafiyetinin kaldırıldığı gün açılmış TV SHORT'ları;
son 3'ü (#52, #55, #73) kıl payı — EMA50/EMA200 farkı sırasıyla %−0.026 / %−0.096 / %0.000,
kapanış/EMA50 farkı %−0.40 / %−0.04 / %+0.14. 5 dk'lık rejim önbelleği
(`engine.py:103 _REGIME_CACHE_TTL=300`) bu üçünü tek başına açıklar.
**2026-08-18'den sonra tek bir ihlal yok** — D5 paritesi doğrulandı.

### E8.1 — Popülasyon (opened_at'e göre; 202 CLOSED)
| Kesit | n | SL | SL% | WR% | PnL | PF |
|---|---|---|---|---|---|---|
| Tümü | 202 | 54 | 26.7 | 67.8 | +849.1 | 2.11 |
| `pnl_source=binance_income_net` (güvenilir) | 179 | 51 | 28.5 | 70.4 | +596.2 | 1.93 |
| C | 144 | 44 | 30.6 | 63.2 | +516.4 | 1.86 |
| TV | 58 | 10 | 17.2 | 79.3 | +332.7 | 3.03 |
| LONG | 144 | 27 | 18.8 | 77.1 | +761.4 | 2.44 |
| SHORT | 58 | 27 | 46.6 | 44.8 | +87.7 | 1.37 |
| C-LONG | 101 | 22 | 21.8 | 73.3 | +373.5 | 1.87 |
| C-SHORT | 43 | 22 | 51.2 | 39.5 | +142.9 | 1.84 |
| TV-LONG | 43 | 5 | 11.6 | 86.0 | +387.9 | 4.92 |
| **TV-SHORT** | **15** | **5** | **33.3** | **60.0** | **−55.2** | **0.15** |

BTC gün tipi (post-hoc, UP>+1.5% / DOWN<−1.5%): UP 97 işlem +705.0 (PF 4.57, SL %16.5) ·
FLAT 88 +257.9 (1.86, %37.5) · DOWN 17 −113.8 (0.58, %29.4).
Ters-gün kovaları: **DOWN günü LONG** n=15, 4 SL, −110.4 (PF 0.58) · **UP günü SHORT** n=13,
**10 SL**, −85.6 (PF 0.22).

**Ödeme asimetrisi — spec'teki rakam üretilemedi (düzeltme).** `roi_pct` alanı
`realized_pnl / margin_usdt × 100`'dür (`tracker.py:132`). Defterde SL çıkışlarının ROI'ı
yalnız 6 satırda tam −%50'dir; kalanlar TP1 sonrası BE'ye çekilmiş stoplardır
(dağılım −54.8 … −0.1, medyan **−6.4**). Ölçüm:
| Kesit | kazanan n / ort | kaybeden n / ort | başabaş WR | gerçek WR |
|---|---|---|---|---|
| Tüm defter (USDT) | 137 / +11.77 | 64 / −11.94 | **%50.3** | %67.8 |
| Güvenilir alt küme (USDT) | 126 / +9.82 | 53 / −12.10 | **%55.2** | %70.4 |
| 12 Ağu sonrası (USDT) | 133 / +9.30 | 54 / −12.92 | **%58.2** | %71.1 |
| Tüm defter (ROI%) | +9.93% | −12.13% | **%55.0** | %67.8 |
`docs/superpowers/specs/2026-08-22-reversal-day-loss-design.md`'deki "SL ort −%48 ROI →
başabaş WR %81.5" bu veriden **yeniden üretilemedi**; en olası kaynak, ortalama USDT kaybının
dönem-ortalaması bir marja bölünmesi (marj 17→22 Ağu arası 36→162 büyüdü, D16). CLAUDE.md'deki
"başabaş ≈ %85" ise **simülatör** rakamıdır (SL −514 / TRAIL +88 birim, "rejim referansları"
tablosu) — canlı defterle karıştırılmamalı. Kenar sanılandan geniştir; bu, ayar değil sinyal
odaklı çözümü zayıflatmaz, yalnız aciliyet çerçevesini düzeltir.

### E8.2 — Özellik kümesi (hepsi giriş anından ÖNCE kapanmış mumlardan)
Sembol: `sym_chg_1h/4h/24h`, `sym_run_3d`, `sym_day_open_dev`, `atr_pct_5m`, `atr_pctile_30d`
(30 günlük 5m ATR% persentili), `dist_ema50_15m_atr/_pct`, `rsi_1m/5m/15m`, `bb_pctb_1m/5m/15m`,
`vol_ratio_5m`, `sym_regime_15m`. Motor-pariteli: `eng_rsi_entry`, `eng_bb_pctb_entry`,
`eng_atr_pct_entry`, `eng_regime_sym/btc` (motorun çektiği pencere boylarıyla).
Lider: `btc_chg_1h/3h/24h`, `btc_run_3d`, `btc_day_open_dev`, `btc_regime_15m`.
Yön-işaretli (`align_* > 0` = işlem yönü hareketle AYNI yönde): `align_btc_day/1h/3h/24h/run_3d`,
`align_sym_day/1h/run_3d`, `align_dist_ema50_atr`, `abs_dist_ema50_atr`,
`rsi_extremity_1m/5m/15m/eng` ( = (50−RSI)×yön ), `bb_overshoot_1m/5m/eng`.
Zaman: `hour_utc`, `dow`. Kümelenme: `cluster_60m_same_dir(_sym)`.
TV: `tv_sources`, `tv_confluence_lag_s`, `tv_vote_span_s`.
**Post-hoc (kapı özelliği DEĞİL, yalnız tanı):** `mae_pct`, `mfe_pct`, `btc_day_type_posthoc`.

### E8.3 — Kaybeden (exit_reason=SL, n=54) vs kalanlar — en ayırt edici 5 özellik
AUC = P(SL değeri > SL-olmayan değeri); 0.5 = bilgi yok. p = permütasyon (2000 tur).
Sayılar testnet mumlarıyla (motorun gördüğü); mainnet karşılıklarıyla fark ≤0.02 AUC
(mainnet tablosu `signal_autopsy/analysis_report.txt`, testnet `analysis_report_testnet.txt`).
| Özellik | SL ort (med) | SL-olmayan ort (med) | AUC | p |
|---|---|---|---|---|
| `align_btc_run_3d` (BTC 3g koşusu, yön işaretli) | 2.28 (0.62) | 7.50 (5.56) | 0.292 | <0.001 |
| `btc_chg_24h` | 0.86 (0.22) | 3.77 (1.33) | 0.299 | <0.001 |
| `sym_run_3d` | −1.11 (−0.46) | 10.49 (5.06) | 0.312 | <0.001 |
| `atr_pctile_30d` | 41.1 (25.1) | 62.8 (79.2) | 0.322 | <0.001 |
| `rsi_extremity_15m` ( = (50−RSI₁₅ₘ)×yön ) | 2.79 (1.14) | −4.58 (−5.05) | 0.644 | <0.001 |
Yalnız **LONG** (n=144, 27 SL): `rsi_5m` 41.2 vs 49.1 (AUC 0.333, p 0.006) · `rsi_15m` 48.0 vs
57.1 (0.342, p 0.012) · `dist_ema50_15m_atr` −0.47 vs **+1.69** (0.325, p 0.004) ·
`btc_chg_24h` 1.70 vs 4.72 (0.302, p 0.001) · `sym_chg_4h` −3.18 vs 0.73 (0.332, p 0.009).
Yalnız **SHORT** (n=58, 27 SL): giriş anında ölçülen HİÇBİR özellik anlamlı ayırmıyor
(`atr_pctile_30d` p 0.44, `align_btc_day` p 0.29, `align_dist_ema50_atr` p 0.39; tek "anlamlı"
olan `mae_pct` post-hoc). SHORT tarafında kayıp **öngörülemiyor**; aşağıdaki SHORT kuralı
ayrım değil, **ödeme asimetrisi** üzerinden çalışıyor (kazançlar ≈0, kayıplar tam stop).

**C-LONG için bağlam-TF RSI'ı tek başına monoton** (ledger, testnet mumu):
| RSI(14, 5m=bağlam TF) | n | SL | SL% | PnL |
|---|---|---|---|---|
| <30 | 15 | 6 | 40.0 | −51.1 |
| 30–35 | 11 | 4 | 36.4 | −90.3 |
| 35–40 | 12 | 3 | 25.0 | +24.0 |
| 40–45 | 24 | 5 | 20.8 | +58.5 |
| 45–50 | 18 | 1 | 5.6 | +142.6 |
| ≥50 | 21 | 3 | 14.3 | +289.8 |
Eşik 40'ta: engellenen 38 C-LONG (13 SL, −117.5), kalan 63 işlem +490.9 / PF 4.42.
Monotonluk eşik-uydurmasına karşı en güçlü kanıttır.

### E8.4 — İki kayıp arketipi (en büyük 12 kayıp incelendi)
(değerler mainnet / testnet mumu — ikisi de veriliyor, sonuç aynı)
- **(A) Düşen bıçak** — işlem yönü ÜST zaman dilimine karşı: LONG'da RSI(5m/15m)<50 ve fiyat
  15m EMA50'nin altında. Örnek #199 XRP C-LONG 22 Ağu 14:40 (−84.0): rsi5 25.5/33.6,
  rsi15 41.7/42.2, EMA50 mesafesi −1.67/−1.25 ATR, BTC gün-açılışına göre −1.68%.
- **(B) Tepe kovalama** — işlem yönünde AŞIRI uzama: #187 ETH TV-LONG 22 Ağu 01:07 (−89.3):
  EMA50'nin **+3.56/+2.01 ATR** üstünde, ATR persentili 96.8/90.1, BTC 3 günde +20.9%.
  #152 XRP C-LONG 20 Ağu (−70.0): +3.35/+4.45 ATR, rsi15 71.1/73.4, ATR persentili 100.
  **(B) kapatılamıyor:** aynı yönde uzama filtreleri defterde net NEGATİF
  (`align_dist_ema50_atr > 2.0` engelle → tüm defterde **−559.3**, TV-LONG'da −302.1).
  Uzama trend sürüşünün ta kendisi; (B) kayıpları trendden kazanmanın bedeli.

### E8.5 — Tek-eşikli kapı simülasyonu (DEFTER üstünde)
Ölçüt: Δ = kapı uygulansaydı toplam PnL değişimi; ayrıca güvenilir alt küme, tarih ortasından
bölünmüş iki yarı, BTC gün tipi kırılımı ve bootstrap %90 aralık (2000 tur). İki veri kaynağında
da ölçüldü; "Δ güvenilir", "yarı-1/yarı-2", "ci90" ve "PF" sütunları **mainnet** koşusundan
(testnet koşusunun karşılıkları `signal_autopsy/rules_testnet.txt`'te, işaret ve mertebe aynı).
`align_btc_day` ailesi iki kaynakta birebir aynıdır (r=0.998).
| Kural (kapsam) | blok / SL | Δ mainnet | Δ testnet | Δ güvenilir | yarı-1 / yarı-2 | ci90 | PF |
|---|---|---|---|---|---|---|---|
| `RSI(5m) < 40` → C-LONG yok | 35–38 / 12–13 | +179.7 | +117.5 | +112.4 | +89.0 / +90.7 | [+4.7, +393.8] | 2.11→3.15 |
| `RSI(15m) < 50` → C-LONG yok | 41–42 / 13–14 | +176.3 | +172.3 | +103.5 | +70.8 / +105.4 | [−14.8, +422.6] | 2.11→3.32 |
| `fiyat < EMA50(15m) − 1.0·ATR` → C-LONG yok | 27–30 / 10 | +124.0 | +122.5 | +76.8 | +49.4 / +74.5 | [−12.9, +298.6] | 2.11→2.72 |
| `ATR persentili < 40` → SHORT yok | 36–39 / 19–20 | +110.3 | +109.8 | +90.3 | +59.0 / +51.3 | [−1.2, +235.0] | 2.11→2.63 |
| `BTC gün açılışına göre ≥1.3% ters` → giriş yok | 11 / 2 | −27.8 | −27.8 | **+99.2** | −129.6 / +101.8 | [−314.4, +242.6] | 2.11→2.38 |
Reddedilenler (defterde net negatif): `align_btc_run_3d > 15` (tüm defter **−152.7**;
LONG'da eşik 12 → **−382.9**, 50 kazanan engelliyor) · `cluster_60m_same_dir > 1` (**−509.2**;
kümelenme trendin kendisi) · aynı yönde uzama filtreleri (yukarıda) · `vol_ratio_5m`
(testnet'te ölçülemez, E8.0) · gün/saat kalıpları (Cts SL %51.4, Pzt %60.0 — 16 günde
2-3 örnek; **rejim artefaktı, kural yapılmamalı**).

### E8.6 — HARNESS ölçümü (3 pencere, mevcut env; P2 kuralı)
Taban koşular (sunucu env kopyası `scripts/.scalper_env_snapshot.txt`, C-only, 8 majör,
`--cache-dir data/klines_cache`; log `signal_autopsy/bt_{AYI,YATAY,BOGA}.log`, ham JSON
`bt_{AYI,YATAY,BOGA}.json`; kaynak `logs/backtest_20260823_{015145,015330,015400}.json`
— `logs/` gitignore'da, commit'lenmez):
AYI 213 işlem +584.4 / PF 1.04 (D12'nin "yeni taban 1.04/DD 3683"i ile birebir) ·
YATAY 145 +2392.3 / 1.29 · BOĞA 90 +3901.7 / 2.43.

> **SONRADAN GÜNCELLENDİ (aynı gün, D15 ajanı — bkz. `docs/EXPERIMENTS.md` §E7 ve D15):**
> aşağıdaki gün-kapısı sayıları MOTOR-İÇİ kapıyla yeniden ölçüldü ve bu bölümdeki post-hoc
> sayıları **geçersiz kılar**. Eşik %1.3 önerisi doğrulandı (AYI +2999→+3812, PF 1.33→1.43;
> BOĞA kaybı −%4.5→−%2.7), ama YATAY %1.0'da işaret bile değişti (post-hoc **−487.3** →
> motor-içi **+201**): engellenen sinyal motor-içi kapıda kapasiteyi serbest bırakıyor ve
> boşalan slota sonraki sinyal giriyor. Aşağıdaki "alt sınır" uyarısı tam da bu yüzden
> konmuştu; **karar için §E7'nin sayıları kullanılmalı**, buradakiler yöntem karşılaştırması
> olarak bırakıldı.
>
> **Yöntem sonucu (iki ölçüm birleştirilince — ileride işe yarar):** post-hoc Δ ile motor-içi Δ
> çelişmiyor, biri diğerinin BİR TERİMİ. D15 ajanının bacak atfı (AYI, eşik %1.3, işlemleri
> `symbol+entry_time+direction` ile eşleştirerek) benim post-hoc sayılarımla birebir toplanıyor:
>
> | Bacak | motor-içi Δ | = post-hoc Δ (**kapı etkisi**) | + yeni giren işlemler (**kapasite**) |
> |---|---|---|---|
> | LONG | +1136.1 | **+76.1** (engellenen 24 işlem, ≈başabaş) | +1060.0 (9 yeni işlem) → ~%93 kapasite |
> | SHORT | +2091.8 | **+1934.3** (engellenen 42 işlem, gerçek kaybedenler) | +157.5 (2 yeni işlem) → ~%92 KAPI |
>
> Artık kalan yok (LONG 1136.1−76.1 = 1060.0; SHORT 2091.8−1934.3 = 157.5, ikisi de tam),
> ortak işlemlerin PnL'i iki koşuda aynı. Yani: **post-hoc filtre = saf kapı etkisi; motor-içi
> koşu = kapı + ikinci-derece etkiler; fark = ikinci-derece terim.** Bu, E8.5'teki defter-üstü
> simülasyonların da nasıl okunacağını belirler — onlar da saf kapı etkisidir.
>
> **İkinci-derece terim ÜÇE ayrılır** (üçü de "bir işlemi kaldırmanın yan etkisi", kapı etkisi
> DEĞİL) — ve bu koşuda paylar ölçüldü (D15 ajanı, V1c AYI, 11 yeni giren işlemin her biri
> hangi pencereye düşüyor; script `scratchpad/mechanism_split.py`):
>
> | # | Mekanizma | Kod yolu | V1c AYI payı |
> |---|---|---|---|
> | (a) | semboller-arası **kapasite yeniden tahsisi** | `_apply_capacity_gate` | **0 işlem / 0.0** |
> | (b) | sembol-içi **kayıp-cooldown serbest kalması** | `backtest.py:849-850` (SL olmayınca cooldown kurulmaz) | **0 işlem / 0.0** |
> | (c) | sembol-içi **işgal penceresi serbest kalması** | `backtest.py:851` `i = trade.exit_idx + 1` (kabul) ↔ `805/821/837/844` `i += 1` (ret) | **11 işlem / +1217.4 (%100)** |
>
> (c) şu demek: KABUL edilen bir işlem tarama imlecini tüm tutma penceresinin sonuna atlatıyor,
> ENGELLENEN sinyalde imleç yalnız bir mum ilerliyor — yani bir işlemi engellemek o sembolde
> ortalama bir tutma penceresi kadar (ayı penceresinde ~190 dk) arama alanı serbest bırakıyor.
> Yeni girenlerin HEPSİ, aynı sembolde engellenen bir işlemin `[entry_time, exit_time]`
> penceresinin İÇİNE düşüyor. Toplam +1217.4, benim bağımsız hesabımla (+1217.5) ve bacak
> atfıyla (LONG +1060.0 + SHORT +157.5) birebir tutuyor — üç hesap aynı.
>
> **Yani "kapasite terimi" adı YANLIŞTI** (önce ben koydum, D15 ajanı kabul etti, sonra ölçüp
> ikimizi de çürüttü): `_apply_capacity_gate` bu koşuda TEK bir işlem bile üretmemiş. Bu bir
> tesadüf de değil — repo'nun kendi kanıtı aynı yöne bakıyor: **E4h** (`SCALPER_MAX_POSITIONS`
> 5→3) üç pencerede de tabanla BİREBİR aynı sonucu vermişti (toplam Δ **+0.00**), ve P1 bunu
> "kapasite kapısı hiç fark yaratmadı" diye zaten not etmişti. 8 sembol / 5 slot ile kapasite
> kapısı pratikte hiç bağlamıyor. (b) gerçek bir kod yolu ama burada sıfır katkı verdi.
>
> Bu bir harness artefaktı DEĞİL: canlı motorda da sembolde açık pozisyon varken ikinci giriş
> yoktur. Büyüklüğü ≈ ortalama tutma süresi / giriş TF oranına bağlıdır — canlıdaki 1m girişte
> yapısal olarak büyüktür.
>
> **ÖN-ELEME KURALI — ilk yazdığım hâli YANLIŞTI, D15 ajanının çürütmesiyle düzeltildi.**
> "Post-hoc negatifse aday elenir" GÜVENLİ DEĞİL: ikinci-derece terim saf kapı etkisinden
> bağımsızdır ve onu rahatlıkla domine eder. Karşı-örnek bizim kendi verimiz — LONG bacağında
> saf etki +76.1 (gürültü), ikinci-derece +1060.0; saf etki **−76.1 olsaydı bile** motor-içi
> sonuç +983.9 çıkardı ve iyi bir aday koşulmadan elenirdi. Doğru, asimetrik hâli:
>
> | Post-hoc terim | Hüküm |
> |---|---|
> | GÜÇLÜ POZİTİF | motor-içi koşu bunu genelde büyütür — aday güçlü |
> | ≈ SIFIR veya HAFİF NEGATİF | **hüküm verilemez**, motor-içi koşu ŞART |
> | GÜÇLÜ NEGATİF | muhtemelen kötü; ama eşik "sıfır" değil, büyüklüğü makul bir ikinci-derece kazançla kıyaslanmalı |
>
> Pozitif kol da bir GARANTİ değil: ikinci-derece terim ilkesel olarak negatif de olabilir
> (boşalan slota daha kötü işlemler girerse). Ölçtüğümüz üç pencerede ≥0 çıktı (eşik %1.3
> iki yönlü: AYI +1217.5, YATAY +585.7, BOĞA ≈0 — BOĞA'da zaten yalnız 1 işlem engelleniyor).
> Pozitif çıkma EĞİLİMİNİN yapısal nedeni (mekanizma (c) belli olunca daha net): yerine geçen
> işlemler hem kapıdan geçiyor (tabandan değil FİLTRELENMİŞ dağılımdan çekiliyorlar) hem de
> AYNI SEMBOLDE, engellenen işlemin kendi penceresinde açılıyorlar. Yine de **eğilim, kural
> değil**: işgal penceresinin serbest kalması ilkesel olarak daha kötü işlemler de getirebilir.
>
> **Ayrışmanın geçerlilik koşulu:** "kalan = 0" bir ÖZDEŞLİK DEĞİL, bu koşu çiftinde çıkan
> ampirik bir sonuçtur. Burada tutmasının nedeni: harness boyutlaması sabit `initial_balance`
> kullanıyor (işlem başına compounding YOK — `backtest.py:763,842`; değişken döngü içinde hiç
> yeniden atanmıyor) ve ortak adayların hiçbiri kapasite kapısında sıra değiştirmemiş, hiçbir
> cooldown penceresi kaymamış. Başka bir adayda bunlardan biri bozulursa kalan ≠ 0 olur ve üç
> terim birbirine karışır. **Kural: ayrışmayı kullanmadan önce kalanı hesapla; ≈0 değilse
> ayrışma geçersizdir.**
>
> **Bacak hükmü (çözüldü):** aşağıdaki "kazancın tamamına yakını SHORT bacağından" okuması
> motor-içi veriyle DOĞRULANDI (SHORT ~%92 kapı / LONG ~%7 kapı). D15 ajanının "LONG bacağı da
> değerli" itirazı bu atıfla geri alındı — LONG'daki +1136'nın ~%93'ü boşalan slota giren YENİ
> işlemlerdi, kapının kendisi değil. Bacak-ayrık eşik (SHORT %1.0 / LONG %1.3) artık iki
> ölçümle de destekleniyor ama uygulanmadı: ayrı tasarım kararı + kullanıcı onayı gerektirir.

**Yöntem sınırı (önemli):** kapı **post-hoc** uygulandı — harness JSON'undaki her işlem giriş
zamanıyla yeniden zenginleştirilip filtrelendi. Engellenen bir işlem harness'ta
kapasite/sembol-içi tekillik kısıtını serbest BIRAKMAZ, yani yerine başka bir sinyal geçmez.
Bu yüzden sayılar gerçek motor-içi kapının **alt sınırı**dır; kapı kodlandığında (D15) tekrar
ölçülmelidir. Script: `signal_autopsy/enrich_harness.py`.

**Lider gün-açılışı kapısı (D15 `SCALPER_MARKET_GATE_DAY_PCT`) — eşik taraması**
(blok/SL → ΔPnL; sonuç PnL / PF):
| Eşik | Kapsam | AYI | YATAY | BOĞA | P2 |
|---|---|---|---|---|---|
| 0.5% | iki yönlü | 84/15 → **+2421.9** (3006.2 / **1.40**) | 39/3 → −2002.4 (389.9 / 1.06) | 17/0 → −1294.8 (2606.8 / 1.96) | ❌ BOĞA −%33 |
| 1.0% | iki yönlü | 69/12 → +1751.8 (2336.2 / 1.26) | 16/2 → −487.3 (1905.0 / 1.26) | 3/0 → −241.3 (3660.3 / 2.34) | ✅ (BOĞA −%6.2) |
| **1.3%** | **iki yönlü** | 66/12 → **+2010.4** (2594.8 / **1.29**) | 9/1 → −186.6 (2205.7 / 1.28) | 1/0 → −104.0 (3797.6 / 2.39) | **✅ (AYI PF 1.29, BOĞA −%2.7)** |
| **1.0%** | **yalnız SHORT** | 45/9 → +1675.7 (2260.1 / **1.22**) | 11/2 → **+138.1** (2530.4 / **1.35**) | 3/0 → −241.3 (3660.3 / 2.34) | **✅ (3 pencerede 2'si pozitif)** |
| 1.3% | yalnız SHORT | 42/9 → +1934.3 (2518.7 / 1.24) | 7/1 → −16.6 (2375.7 / 1.30) | 1/0 → −104.0 (3797.6 / 2.39) | ✅ |
| 1.3% | yalnız LONG | 24/3 → +76.1 (660.5 / 1.05) | 2/0 → −170.0 | 0 | ❌ (AYI PF 1.05) |
| 2.0% | iki yönlü | 38/8 → +1695.3 (2279.7 / 1.21) | 2/0 → −230.1 | 0 | ✅ |
Okuma: kazancın tamamına yakını **SHORT bacağından** geliyor (AYI'da BTC gün-açılışının
%1+ ÜSTÜNDEyken açılan 45 SHORT −1675.7 etmiş). LONG bacağının harness kanıtı zayıf
(AYI +76), ama canlı defterin 22 Ağu kaybı tam oradan geldi (8 işlem, +102.1) —
iki kanıt çelişmiyor, farklı rejimlerde farklı bacak çalışıyor.

**Diğer kuralların 3-pencere hükmü (aynı post-hoc yöntem, `signal_autopsy/harness_rules.txt`):**
| Kural | AYI | YATAY | BOĞA | P2 |
|---|---|---|---|---|
| `RSI(5m)<40` → LONG yok | +98.5 (PF 1.06) | −1244.1 | −771.6 (−%20) | ❌ AYI PF<1.1 |
| `RSI(15m)<50` → LONG yok | −454.8 | −2802.4 | −1166.6 | ❌ |
| `RSI(15m)<50` → iki yönlü | +227.1 (1.12) | −2488.7 | −1719.9 (−%44) | ❌ BOĞA |
| `fiyat < EMA50(15m)−1·ATR` → iki yönlü | +786.3 (1.14) | −3555.1 | −1394.8 (−%36) | ❌ BOĞA |
| `ATR persentili<40` → SHORT yok | −1037.5 | −565.3 | +323.5 | ❌ AYI |
| `align_btc_run_3d>15` → giriş yok | +2453.3 (1.25) | 0 tetik | 0 tetik | ⚠ tek pencere |
**Yani: defterde en güçlü olan bağlam-TF kuralları harness'ta P2'yi GEÇMİYOR.** Defter
16 günlük ve LONG-ağırlıklı (144/202) bir boğa örneklemi; harness'ın AYI/YATAY pencereleri
SHORT-ağırlıklı. Bağlam-TF kuralı "üst TF'ye karşı işlem açma" demek olduğundan, C'nin
kârının ters-trend dip alımından geldiği pencerelerde (YATAY: `RSI(15m)<50` 54 LONG'un
50'si kazanan) doğrudan kârı kesiyor. **Hüküm: kanıt yetersiz — uygulanmaz.**


### E8.7 — TV SHORT kaynak kalitesi (15 işlem, PF 0.15)
Kaynak eşlemesi `bot.log` "Sağlama tamam" satırlarından. Sunucudaki log tutma penceresi
**2026-08-16 00:05**'te başlıyor (`bot.log.7.gz`), TV işlemleri ise 08-12'de başlamış: bu yüzden
9 TV işleminin kaynağı "bilinmiyor" — 7'si (08-12…08-15) log tutma penceresinin dışında,
2'si (#60 08-16 LONG, #89 08-18 SHORT) pencere içinde olduğu hâlde ±10 dk'da eşleşen
"Sağlama tamam" satırı bulunamadı (nedeni **kodda/logda doğrulanamadı**).
| Kaynak çifti | Yön | n | SL | PnL | PF | WR% |
|---|---|---|---|---|---|---|
| luxosc+luxso | SHORT | 5 | 3 | **−50.9** | **0.00** | 40.0 |
| bilinmiyor | SHORT | 7 | 1 | −3.5 | 0.67 | 71.4 |
| algopro+luxosc | SHORT | 2 | 1 | −1.0 | 0.72 | 50.0 |
| algopro+luxso | SHORT | 1 | 0 | +0.2 | ∞ | 100.0 |
| bilinmiyor | LONG | 2 | 1 | +11.1 | 57.96 | 50.0 |
| algopro+luxso | LONG | 14 | 2 | +62.4 | 1.69 | 85.7 |
| algopro+luxosc | LONG | 12 | 1 | +141.5 | 19.54 | 91.7 |
| luxosc+luxso | LONG | 15 | 1 | +172.9 | 133.93 | 86.7 |
Mekanizma: 15 TV SHORT'un 9 kazananından **8'i +1 USDT'nin altında** (TP1 dolup BE'ye
çekilen TRAIL: +0.1 … +0.7; tek istisna #29 +5.6). Brüt kâr toplamı 10.2, brüt kayıp 65.2 →
PF 0.156. Kaybın **%88'i 3 satırdan** geliyor (#92 −30.6, #75 −18.4, #30 −8.6) ve üçü de
DÜŞÜK oynaklık rejiminde (ATR persentili 19.9 / 33.7 / 45.6). Yani "PF 0.15" bir
ayırt-edicilik değil **ödeme asimetrisi** problemidir: TV SHORT'un yukarı tarafı yok,
aşağı tarafı tam stop. Kaynak bazında hüküm vermek için örneklem yetersiz (kaynak başına
1–5 işlem); `luxosc+luxso` SHORT'un brüt kârı **tam sıfır** olması yine de en zayıf halka işareti.
TV sağlama süresi (ilk oy→tamam) medyan 148 sn ve sonucu AYIRMIYOR (SL ort 152 sn,
SL-olmayan 150 sn) → sağlama penceresi (420 sn) bir kalite kaldıracı değil.
TV LONG'da hiçbir kaynak çifti negatif değil.

### E8.8 — Kod gerektiren kapılar (bir sonraki ajana kesin tarif)
Mevcut `SCALPER_*` anahtarlarıyla ölçülemeyenler ve neden:
1. **Lider gün-açılışı kapısı** — D15 `SCALPER_MARKET_GATE` / `_DAY_PCT`. Ölçüm yukarıda:
   **iki yönlü, eşik %1.3** (spec'teki varsayılan %1.0 da P2'yi geçiyor ama YATAY'da −%20).
   Alt-kapı önerisi: **SHORT bacağı %1.0, LONG bacağı %1.3** (kanıt ayrık — E8.6).
2. **Lider N-günlük koşu alt-kapısı** (`_RUN_PCT/_RUN_DAYS`) — **varsayılan 0 (KAPALI) kalsın.**
   (D15 ajanının bağımsız ölçümü aynı yöne çıktı: harness'ta yalnız AYI'da ve TEK lider
   olayında tetikleniyor, %15 ile %20 birebir aynı sonucu veriyor, gün-kapısıyla birlikte
   katkısı sıfır — V3 ≡ V1. **Ek uyarı (E8):** kod varsayılanı hâlâ 15; bu, kapı açıldığında
   ÖLÇÜLMEMİŞ değil, canlı defterde NEGATİF ölçülmüş bir alt-kapıyı devreye sokar — 7–22 Ağu
   defterinde %15 eşiği **35 işlemde tetiklenir ve net −152.7 eder** (12 DOWN-günü işlemi
   engelleyip +137.9 kurtarır, 23 UP-günü kazananı engelleyip −290.6 kaybettirir).
   Harness'ın "üç pencerede inert" hükmü bugünkü piyasaya taşınmıyor.)
   Canlı defterde net **−152.7** (LONG eşik 12'de −382.9; 50 kazanan engelliyor), harness'ta
   yalnız AYI'da tetikleniyor (+2453, 13 işlem), YATAY/BOĞA'da hiç tetiklenmiyor. Tek pencerede
   parlayan aday = P2'ye göre red. Spec'teki "lider koştuysa o yöne girme" hipotezinin
   İŞARETİ defterde TERS: kazananların `align_btc_run_3d` ortalaması 7.50, kaybedenlerin 2.28.
3. **Bağlam-TF trend uyumu** (`passes_context_trend`, öneri): C LONG için
   `RSI(14, scalper_tf_context) >= X`, SHORT için `<= 100−X`; X=0 kapalı. Veri hazır —
   `ctx.candles_15m` (bağlam rolü, 100 mum) zaten çekiliyor, YENİ REST çağrısı YOK.
   Defter kanıtı güçlü ve monoton (E8.3), **harness kanıtı P2'yi geçmiyor (E8.6) → şu an
   uygulanmamalı**; ancak D15 kapısı canlıya girip soak bittikten sonra tekrar ölçülmeye değer.
   Mevcut `SCALPER_C_REQUIRE_FLOW_CONFIRM` AYNI niyeti taşıyor ("düşen bıçakta dip alma")
   ama MFI'yi **giriş TF'sinde** (canlıda 1m) okuyor (`setups.py:144-162`) ve E2b'de
   YATAY'da düşmüştü — farklı bir kaldıraç, karıştırılmamalı.
   `SCALPER_USE_EQUILIBRIUM_FILTER` ise TERS kutupludur (LONG'u yalnız *discount*ta geçirir,
   `setups.py:194-217`) — bu bulgunun tam tersini yapar, açılmamalı.
4. **Uzama kapısı** (`fiyat − EMA50(tf_regime)` / ATR) — kodlanabilir (`ctx.candles_4h`
   zaten 250 mum) ama **iki taraflı da red**: ters yön eşiği harness'ta BOĞA'yı −%36 bozuyor,
   aynı yön eşiği defterde −559. Önerilmiyor.
5. **ATR persentili** (`atr_pctile_30d`) — 30 günlük 5m geçmişi gerekir (sembol başına
   ~8640 mum = 6 ek REST çağrısı, günlük önbelleklenebilir). Defterde SHORT için +110 ama
   harness'ta AYI −1037 → **önerilmiyor**; ayrıca SHORT tarafında ayırt-edicilik yok (p 0.44).
6. **Hacim tabanlı kapı** — testnet'te ÖLÇÜLEMEZ (E8.0, r=−0.04). Mainnet'e geçmeden
   denenmemeli; harness'ta iyi görünse bile canlıda (testnet) anlamsız çalışır.

**Örneklem uyarısı.** Defter 202 işlem / 16 gün, 54 SL. Yön×strateji kırılımında hücre başına
15–101 işlem kalıyor; TV-SHORT 15, DOWN-günü LONG 15, UP-günü SHORT 13. Bu boyutta
çok-parametreli kural aramak aşırı uydurmadır; yukarıdaki taramada bilinçli olarak yalnız
TEK eşikli, ön-kayıtlı (spec'ten veya mekanizmadan türeyen) kurallar denendi ve her biri
4 ayrı kesitte (tümü / güvenilir / yarı-1 / yarı-2) + gün tipi kırılımında + bootstrap
aralığıyla raporlandı. Yine de tek bir kuralın bile defter kanıtı **tek başına** terfi
gerekçesi değildir (P2: 3 rejim penceresi + testnet soak).

## 2026-08-23 — Piyasa yapısı (CHoCH/BOS) kapısı (E9)

**Soru (kullanıcı):** "sistem dönüşleri tespit edemiyor" — rejim kapısı (D5, 15m
EMA50/200) dönüş günlerinde saatler geç kalıyor; LuxAlgo Price Action Concepts'in
CHoCH/BOS yapı sinyalleri bunu daha erken görüyor mu? Çözüm **sinyal** olmalı
(boyut/TP/stop ayarı D16'da kullanıcı kararıyla reddedildi).

**Ne kodlandı:** `src/strategies/scalper/structure.py` — saf (IO'suz, deterministik,
look-ahead'siz) yapı durum makinesi: fraktal pivot → son onaylanmış swing seviyesi →
kapanışla kırılım → BOS (devam) / CHoCH (karakter değişimi, yapı yönü döner). Motorda
`_evaluate_symbol`'de rejim kapısının HEMEN ARDINDA tek giriş kapısı (C ve TV aynı
yerden geçer), harness'ta `simulate_symbol` AYNI saf fonksiyon çiftini AYNI pencerelerle
çağırır. Opsiyonel çıkış tetikleyicisi (`SCALPER_STRUCTURE_EXIT=be|close`) canlıda
`engine._apply_structure_exits`, harness'ta `manage_position`. **Hepsi varsayılan KAPALI**
(`tests/test_structure.py` 51 test + `tests/test_golden_backtest.py` DEĞİŞMEDEN geçer).

**Komut kalıbı** (env tabanı `scripts/.scalper_env_snapshot.txt` = canlı 10/50/10/10,
divergence=true; `--cache-dir data/klines_cache` → AĞSIZ; C-only, 8 majör):
```bash
env $(cat scripts/.scalper_env_snapshot.txt | xargs) SCALPER_STRUCTURE_GATE=true \
  SCALPER_STRUCTURE_TF=context SCALPER_STRUCTURE_PIVOT=5 \
  python3 -m src.strategies.scalper.backtest --strategies C \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,BNBUSDT,ADAUSDT,LTCUSDT \
  --start 2026-01-23 --end 2026-02-13 --cache-dir data/klines_cache
```
Loglar: `logs/structure/<varyant>_<pencere>.log` (24 koşu; `logs/` gitignore'da).
Varyantlar: **S0** kapalı (taban) · **S1** giriş kapısı 5m/pivot5 · **S2** giriş kapısı
15m/pivot5 · **S3** = S1 + `EXIT=be` · **S4** = S1 + `EXIT=close` · **S1p3/S1p8/S2p8**
pivot duyarlılığı.

### E9.1 — Sonuç tablosu (işlem / WR% / PnL / PF / maxDD · kapı tetiği)
| Varyant | AYI | YATAY | BOĞA | Yapı kapısı tetiği (A/Y/B) |
|---|---|---|---|---|
| **S0 taban** | 213 / 85.4 / **+584.4** / **1.04** / 3683 | 145 / 86.9 / **+2392.3** / 1.29 / 3229 | 90 / 93.3 / **+3901.7** / 2.43 / 735 | — |
| S1 (5m, p5) | 84 / 83.3 / −1057.1 / 0.85 / 2140 | 71 / 81.7 / −356.4 / 0.93 / 2077 | 36 / 91.7 / +1289.4 / 1.84 / 1113 | 344 / 258 / 188 |
| S2 (15m, p5) | 129 / 84.5 / −396.8 / 0.96 / 3314 | 104 / 80.8 / −1943.3 / 0.78 / 3260 | 59 / 89.8 / +1525.9 / 1.56 / 1295 | 239 / 149 / 103 |
| S3 (S1+be) | 84 / 47.6 / −1589.3 / 0.70 / 2438 | 77 / 39.0 / −755.0 / 0.77 / 1566 | 40 / 52.5 / +1172.0 / 3.00 / 514 | 356 / 289 / 220 |
| S4 (S1+close) | 89 / 33.7 / −2442.3 / 0.53 / 2821 | 81 / 33.3 / −898.9 / 0.69 / 1084 | 42 / 38.1 / +409.9 / 1.43 / 417 | 373 / 304 / 229 |
| S1p3 (5m, p3) | 63 / 82.5 / −1240.9 / 0.78 / 2499 | 55 / 83.6 / +576.4 / 1.18 / 1282 | 26 / 96.2 / +1739.6 / 4.38 / 514 | 394 / 314 / 245 |
| S1p8 (5m, p8) | 113 / 83.2 / −825.7 / 0.91 / 3311 | 84 / 83.3 / +227.6 / 1.04 / 2738 | 46 / 87.0 / +598.9 / 1.22 / 1423 | 252 / 208 / 165 |
| S2p8 (15m, p8) | 137 / 83.9 / −11.3 / 1.00 / 2720 | 112 / 84.8 / +209.8 / 1.03 / 2352 | 66 / 89.4 / +1535.8 / 1.47 / 1454 | 215 / 118 / 89 |

S0, E8.6/D12'nin tabanını **birebir** yeniden üretti (AYI 213 / +584.4 / PF 1.04 /
DD 3682.60) → ölçüm hattı doğru.

**P2 hükmü: 7 varyantın 7'si de REDDEDİLDİ.** Hiçbiri "AYI PF ≥ 1.1" ya da "AYI ve
YATAY birlikte iyileşir" kolunu geçmiyor; BOĞA PnL kaybı her varyantta ≥ %55
(kural: ≤ %20). En "iyi" varyant (S2p8) tabanı yalnızca **hiçbir şey yapmamaya
yaklaşarak** yakalıyor: pivot büyüdükçe (yapı yavaşladıkça) tetik sayısı düşüyor ve
sonuç tabana yakınsıyor — AYI PF 0.78 (p3) → 0.85 (p5) → 0.91 (p8) [5m] ve
0.96 (p5) → 1.00 (p8) [15m]. **Monotonluk, bunun bir eşik/pivot ayarı sorunu
OLMADIĞININ en güçlü kanıtıdır** (aynı desen E4a/E2ab'de de görülmüştü).

### E9.2 — Kesilen kayıp mı, kesilen kâr mı? (çıkış nedeni kırılımı)
| Pencere | Varyant | SL (n / PnL) | TRAIL (n / PnL) | Kesilen kayıp | Kesilen kâr | oran |
|---|---|---|---|---|---|---|
| AYI | S0 | 29 / −14907 | 182 / +15721 | — | — | — |
| AYI | S1 | 14 / −7196 | 70 / +6139 | 7711 | 9582 | **0.80** |
| AYI | S2 | 19 / −9766 | 109 / +9563 | 5141 | 6158 | **0.83** |
| YATAY | S0 | 16 / −8224 | 125 / +10628 | — | — | — |
| YATAY | S1 | 10 / −5140 | 57 / +4785 | 3084 | 5843 | **0.53** |
| YATAY | S2 | 17 / −8738 | 83 / +6807 | **−514** | 3821 | **<0** |
| BOĞA | S0 | 5 / −2570 | 84 / +6624 | — | — | — |
| BOĞA | S1 | 3 / −1543 | 33 / +2832 | 1027 | 3792 | **0.27** |
| BOĞA | S2 | 5 / −2571 | 53 / +4249 | −1 | 2375 | **0.00** |
Yani kapı, her pencerede kestiği her 1 birim kayba karşılık **1.2–3.7 birim kâr**
kesiyor; YATAY/BOĞA'da 15m kapısı **hiç kayıp kesmiyor**, yalnız kâr kesiyor.

**Yön kırılımı — kullanıcının "düşen bıçak LONG" hipotezi doğrudan sınandı.** AYI
penceresinde taban LONG bacağı 79 işlem / −956; 15m yapı kapısı 30 LONG'u engelledikten
SONRA kalan LONG bacağı **49 işlem / −2050 (PF 0.84 → 0.60)** — yani kapı düşen-bıçak
kayıplarını KESMEDİ, tersine **kârlı dip alımlarını** kesip başarısız sıçrama alımlarını
bıraktı. (S1'de LONG −956 → −587 ama işlem 79 → 36; kayıp/işlem AYNI mertebede.)
Mekanizma: C tanımı gereği ters-trend bir ortalamaya-dönüş stratejisidir (RSI ucu +
BB taşması); "yapıya ters işlem açma" kuralı C'nin kâr kaynağını doğrudan yasaklar.
Bu, E8.6'nın bağlam-TF kuralları için verdiği hükmün (defterde güçlü, harness'ta P2'yi
geçmiyor) BAĞIMSIZ ikinci bir doğrulamasıdır.

### E9.3 — Çıkış tetikleyicisi (S3/S4): en zararlı varyant
| Pencere | Varyant | CHOCH/STRUCT_BE çıkışı (n / PnL) | TRAIL (n / PnL) | WR% |
|---|---|---|---|---|
| AYI | S3 (be) | STRUCT_BE 34 / −136 | 40 / +3687 | 47.6 |
| AYI | S4 (close) | CHOCH 59 / −4682 | 29 / +2754 | 33.7 |
| YATAY | S4 (close) | CHOCH 56 / −2835 | 23 / +1861 | 33.3 |
| BOĞA | S4 (close) | CHOCH 27 / −939 | 15 / +1349 | 38.1 |
S4, AYI'da SL sayısını 29'dan **1**'e düşürdü (kayıp −14907 → −514) — yani "dönüşte
kes" mekanik olarak ÇALIŞIYOR — ama TRAIL kazananları 182'den **29**'a indirdi
(+15721 → +2754). Kazanma oranı %85 → %34. S3 (BE'ye çek) daha yumuşak: 34 işlem
başabaşa çekildi (−136 toplam) ama TRAIL 182 → 40. **Sonuç: açık bir C pozisyonuna
ters CHoCH bir dönüş uyarısı değil, C'nin zaten fade ettiği gürültünün ta kendisidir.**
Kullanıcının bilmesi gereken sayı: sistemin başabaş kazanma oranı ≈ %85 (SL ort −514,
TRAIL ort +88); WR'yi %34'e düşüren her kural, kayıpları kesse bile matematiksel
olarak kaybettirir.

### E9.4 — Gecikme analizi (yapı vs rejim kapısı)
Betik: scratchpad `structure_delay.py` (AĞSIZ, `data/klines_cache` üzerinde; 8 sembol ×
3 pencere).
- **Pivot onayı gecikmesi yapı SİNYALİNİ geciktirmez.** Bir pivot ancak sağındaki N mum
  kapanınca onaylanır, ama o seviyeyi kıracak bir mum pivotu zaten geçersiz kılar
  (`high[p] > high[j]` şartı) — yani kırılım olayı en erken `pivot+right+1`'de doğar ve
  bu ASLA bir kaçırılmış kırılım demek değildir. Özellik testiyle sabitlendi
  (`test_pivot_confirmation_delay_is_modelled`, `test_no_lookahead_prefix_stability`).
- Kırılan seviyenin YAŞI (pivot → kırılım) medyan **11 mum** (pivot 5; p3'te 7, p8'te
  17). Olay, kıran mumun KAPANIŞINDA doğar → 5m'de ≤5 dk, 15m'de ≤15 dk gecikme.
- **CHoCH, EMA50/200 rejim dönüşünden çok daha erken:** 15m'de aynı yönlü son CHoCH,
  rejim dönüşünden medyan **45 mum ≈ 11 saat** önce gerçekleşiyor (n=2128 dönüş;
  pivot 3'te 28 mum, pivot 8'de 66 mum). **Ama bu "öngörü" değil FREKANStır:** aynı
  veride 15m/pivot5 yapı, sembol-gün başına ≈2.4 CHoCH (5m'de ≈6.8) üretiyor — o kadar
  sık dönen bir göstergenin her rejim dönüşünden önce "haber vermiş" olması kaçınılmaz.
  Kapının 3 pencerede kaybettirmesinin nedeni de tam olarak budur: erken ama gürültülü.
- Yapı olay sayıları (8 sembol × pencere, kapanış bazlı): 5m/p5 AYI 1111 BOS + 1138
  CHoCH · YATAY 951/1044 · BOĞA 662/675; 15m/p5 AYI 402/410 · YATAY 363/404 ·
  BOĞA 251/292.

### E9.5 — Parite ve bilinen sapma
- Motor ve harness **aynı saf fonksiyonları** çağırır (`structure_state_for` →
  `structure_gate_blocks`; çıkışta `detect_structure` → `structure_exit_action`);
  fonksiyon kimliği testle sabitlendi (`test_engine_and_harness_call_the_same_pure_function`).
- **Bilinen 1 mumluk sapma (yeni değil, tüm göstergeler için geçerli):** canlı
  `get_klines(limit=N)` oluşmakta olan mumu attığı için genelde N−1 KAPALI mum döndürür;
  harness tam N mum diler. Yapı durum makinesi geçmişe bağımlı olduğundan bu ölçüldü
  (scratchpad `structure_window_parity.py`, gerçek veri, 8 sembol × 3 pencere):
  **5m/100 mum: yön farkı %0.06 (6520 örnekte 4), son-olay farkı %0.23; 15m/250 mum:
  %0.00**. Yani sapma ölçüm sonuçlarını taşıyacak mertebede değil.
- Kapı **motor-içi**dir (E8.6'nın post-hoc yönteminin aksine): engellenen sinyal
  kapasite/sembol-içi tekilliği serbest bırakır, yerine başka sinyal geçebilir. Bu
  yüzden E9 sayıları E8.6'nın "alt sınır" uyarısına tabi DEĞİLDİR.
## 2026-08-23 — Lider piyasa kapısı (E7, spec §C / D15)

Kod: `src/strategies/scalper/market_gate.py` (saf kural, motor + harness ORTAK), commit `ece8bd8`.
Env tabanı: `scripts/.scalper_env_snapshot.txt` (sunucu env kopyası; TP1 10, fixed_roi 50,
max_margin 10, TF 1m/5m/15m, entry maker, dyn lev 3-20, max_positions 5, divergence açık).
⚠️ Atıf düzeltmesi (2026-08-23): bu paragraf önce "D16 paketi" diyordu — `docs/DECISIONS.md`'de
**D16 diye bir karar YOKTUR** (aday paket geri alındı, numara hiç kullanılmadı), yani okuyucuyu
var olmayan bir kayda yönlendiriyordu. Olgu şu: ölçümlerin ORTASINDA bir parametre paketi
(chandelier/TP1 adayları, D11-D12 ailesi) uygulanıp GERİ ALINDI; tablodaki tüm koşular geri
alınmış (orijinal) tabanla YENİDEN koşuldu — her log dosyasının başında o koşuda fiilen
kullanılan tam env yazılıdır (`# taban env:` bloğu), gerçeğin kaynağı odur.
Komut:
```bash
env $(cat scripts/.scalper_env_snapshot.txt | xargs) SCALPER_MARKET_GATE=true \
  SCALPER_MARKET_GATE_DAY_PCT=<X> SCALPER_MARKET_GATE_RUN_PCT=<Y> SCALPER_MARKET_GATE_RUN_DAYS=3 \
  python3 -m src.strategies.scalper.backtest --strategies C \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,BNBUSDT,ADAUSDT,LTCUSDT \
  --start <YYYY-MM-DD> --end <YYYY-MM-DD> --cache-dir data/klines_cache
```
Loglar: `logs/market_gate/<varyant>_<pencere>.log` (24 dosya, sıralı koşuldu).

### Varyantlar
| Varyant | Ayar | Pencere | İşlem | WR% | PnL | PF | maxDD | ΔPnL | day tetik | run tetik |
|---|---|---|---|---|---|---|---|---|---|---|
| **V0** | kapı KAPALI (taban) | AYI | 213 | 85.4 | +584 | 1.04 | 3683 | — | 0 | 0 |
| V0 | | YATAY | 145 | 86.9 | +2392 | 1.29 | 3229 | — | 0 | 0 |
| V0 | | BOĞA | 90 | 93.3 | +3902 | 2.43 | 735 | — | 0 | 0 |
| **V1** | gün-içi %1.0 | AYI | 149 | 87.2 | **+2999** | **1.33** | 2956 | +2415 | 196 | 0 |
| V1 | | YATAY | 135 | 87.4 | +2593 | 1.36 | 2882 | +201 | 62 | 0 |
| V1 | | BOĞA | 88 | 93.2 | +3725 | 2.37 | 735 | −177 (−%4.5) | 15 | 0 |
| **V1a** | gün-içi %0.7 | AYI | 138 | 88.4 | +3897 | 1.52 | 2442 | +3312 | 219 | 0 |
| V1a | | YATAY | 124 | 85.5 | +873 | 1.11 | 3517 | **−1520 (−%63.5)** | 81 | 0 |
| V1a | | BOĞA | 83 | 92.8 | +3384 | 2.24 | 813 | −518 (−%13.3) | 18 | 0 |
| **V1b** | gün-içi %1.5 | AYI | 158 | 86.1 | +1832 | 1.17 | 3348 | +1247 | 162 | 0 |
| V1b | | YATAY | 138 | 87.7 | +2962 | 1.41 | 2840 | +570 | 30 | 0 |
| V1b | | BOĞA | 90 | 93.3 | +3902 | 2.43 | 735 | 0 (tetik yok) | 0 | 0 |
| **V1c** | gün-içi %1.3 (E8 önerisi) | AYI | 158 | 88.0 | **+3812** | **1.43** | 2956 | **+3228** | 179 | 0 |
| V1c | | YATAY | 137 | 87.6 | **+2791** | **1.38** | 2840 | **+399** | 32 | 0 |
| V1c | | BOĞA | 89 | 93.3 | **+3798** | 2.39 | 735 | **−104 (−%2.7)** | 3 | 0 |
| **V2** | uzama %15 / 3g | AYI | 196 | 87.2 | +2909 | 1.24 | 3083 | +2324 | 0 | 60 |
| V2 | | YATAY | 145 | 86.9 | +2392 | 1.29 | 3229 | 0 (tetik yok) | 0 | 0 |
| V2 | | BOĞA | 90 | 93.3 | +3902 | 2.43 | 735 | 0 (tetik yok) | 0 | 0 |
| **V2a** | uzama %10 / 3g | AYI | 192 | 87.0 | +2563 | 1.21 | 3083 | +1979 | 0 | 70 |
| V2a | | YATAY | 145 | 86.9 | +2392 | 1.29 | 3229 | 0 | 0 | 0 |
| V2a | | BOĞA | 76 | 93.4 | +2941 | 2.14 | 735 | **−960 (−%24.6)** | 0 | 40 |
| **V2b** | uzama %20 / 3g | AYI | 196 | 87.2 | +2909 | 1.24 | 3083 | +2324 | 0 | 60 |
| V2b | | YATAY/BOĞA | = V0 | | | | | 0 | 0 | 0 |
| **V3** | ikisi (%1.0 + %15/3g) | AYI/YATAY/BOĞA | **V1 ile BİREBİR AYNI** | | | | | | 196/62/15 | 0/0/0 |

V0, `docs/DECISIONS.md`'deki mevcut tabanı **birebir** üretti (AYI 1.04/DD 3683 · YATAY 1.29/3229 ·
BOĞA 2.43/735) → kapı kapalıyken harness çıktısı değişmemiştir (parite negatif kontrolü).
**V3 ≡ V1:** uzama alt-kapısının engelleyeceği her sinyal gün-içi alt-kapısı tarafından zaten
engelleniyor (V3'te `market_gate_run` sayacı üç pencerede de **0**) — iki alt-kapı toplamsal DEĞİL.

### P2 hükmü (AYI PF ≥ 1.1 **veya** AYI+YATAY PnL birlikte ↑, **VE** BOĞA PnL kaybı ≤ %20)
| Varyant | AYI PF | AYI+YATAY birlikte ↑ | BOĞA kaybı | Hüküm |
|---|---|---|---|---|
| V1 (gün-içi %1.0) | 1.33 ✓ | ✓ (+2415 / +201) | −%4.5 ✓ | **GEÇTİ** (her iki kol) |
| V1a (%0.7) | 1.52 ✓ | ✗ (YATAY −%63.5) | −%13.3 ✓ | GEÇTİ ama YATAY'ı yıkıyor → red |
| V1b (%1.5) | 1.17 ✓ | ✓ (+1247 / +570) | %0 ✓ | GEÇTİ (daha muhafazakâr) |
| **V1c (%1.3)** | **1.43 ✓** | **✓ (+3228 / +399)** | **−%2.7 ✓** | **GEÇTİ — üç pencerede de V1'i domine ediyor** |
| V2 (%15/3g) | 1.24 ✓ | ✗ (YATAY değişmedi) | %0 ✓ | GEÇTİ ama kanıt tek olaya dayanıyor |
| V2a (%10/3g) | 1.21 ✓ | ✗ | **−%24.6 ✗** | **KALDI** |
| V2b (%20/3g) | 1.24 ✓ | ✗ | %0 ✓ | V2 ile birebir aynı |
| V3 (ikisi) | 1.33 ✓ | ✓ | −%4.5 ✓ | GEÇTİ (= V1) |

### Hangi kaybı kesiyor? (yön/çıkış kırılımı — kapının asıl iddiası)
**AYI penceresi, V0 → V1 (gün-içi %1.0)** — DEFTER farkları (engelleme + yeniden tahsis birlikte):
- SL sayısı **29 → 17** (−%41), SL toplam zararı **−14907 → −8738** (+6169 kurtarıldı).
- LONG: 79 işlem / **−956** → 61 işlem / **−121**.
- SHORT: 134 / +1541 → 88 / **+3120**.
- Rejim kırılımı: RANGE günleri **−1029 → +425** (asıl düzelme burada; DOWN 1758 → 2686).

⚠️ **Bu satırlar ATIF DEĞİLDİR** (2026-08-23 inceleme düzeltmesi). Önceki sürüm LONG satırına
"düşen-bıçak LONG'ları kesiliyor", SHORT satırına "kapı simetrik" yorumunu iliştiriyordu; bu iki
cümle de defter farkını kapının ENGELLEMESİNE atfediyordu. Ayrıştırma (aşağıda) bunu çürüttü:
LONG defter düzelmesinin çoğu, kapının boşalttığı slota giren YENİ işlemlerden geliyor.

### Ayrıştırma — engelleme mi, yeniden tahsis mi? (2026-08-23)
ΔPnL iki etkinin toplamıdır: **(a) engelleme** (vetolanan işlemlerin gerçekleşmemesi) ve
**(b) yeniden tahsis** (boşalan slota giren YENİ işlemler). İki koşunun işlem listeleri
`(symbol, entry_time, direction)` üçlüsüyle eşleştirilerek ayrıldı — **yeni backtest yok**.
Betik: `scripts/decompose_gate_runs.py` (rapor yollarını `logs/market_gate/<varyant>_<pencere>.log`
içinden türetir, elle yol girilmez). Ortak işlemlerin PnL'i iki koşuda **birebir aynı**
(V1 ve V1c için ayrı ayrı **0 uyuşmazlık**) — yani atıf temiz.

⚠️ **Sonuç EŞİĞE DUYARLIDIR ve benimsenen eşikte TERSİNE DÖNER.** İlk ayrıştırma yalnız V1
(%1.0) üzerinde yapılmıştı; oysa varsayılan olarak benimsenen eşik **V1c (%1.3)**. Her ikisi de
aşağıda.

| Ölçü (V0 →) | **V1 (%1.0)** | **V1c (%1.3) — VARSAYILAN** |
|---|---|---|
| Toplam ΔPnL (3 pencere) | +2438.96 | +3522.89 |
| **Yalnız-engelleme** bakiyesi | **+224.82** | **+2063.05** |
| — AYI / YATAY / BOĞA | +1297.71 / **−831.56** / −241.34 | +2010.41 / **+156.69** / −104.05 |
| Yeniden tahsis payı | **%90.8** | **%41.4** |
| AYI yalnız-engelleme PF | 1.039 → **1.210** | 1.039 → **1.289** |
| AYI maxDD (yalnız-engelleme) | 3682.60 → **2956.08** | 3682.60 → **2956.08** |
| AYI LONG bacağı, engelleme | **−224.87** (n=27) | **+76.12** (n=24) |
| AYI SHORT bacağı, engelleme | **+1522.59** (n=47) | **+1934.29** (n=42) |
| AYI engellenen SL | 12 adet / −6169.50 | 12 adet / −6169.50 |
| Küme boyu (engellenen / yeni) | AYI 74/10 · YATAY 21/11 · BOĞA 3/1 | AYI 66/11 · YATAY 11/3 · BOĞA 1/0 |

**Okunuşu:**
- **%1.0'da** kazancın **%91'i** kapasite yeniden tahsisinden gelir ve YATAY (−832) ile BOĞA
  (−241) pencerelerinde engellemenin KENDİSİ net negatiftir.
- **%1.3'te (benimsenen) tablo tersine döner:** kazancın **%59'u doğrudan engellemedendir** ve
  YATAY'da bile engelleme net **pozitiftir** (+156.69). Yani "kapı ağırlıklı olarak bir slot
  yeniden tahsis mekanizmasıdır" hükmü **yalnız %1.0 için** geçerlidir, varsayılan eşikte DEĞİL.
  Daha sıkı eşik daha AZ ama daha İSABETLİ engelleme yapıyor (AYI 74 → 66 engelleme, bakiye
  +1298 → +2010).
- **İki eşikte de değişmeyen tek nitel bulgu:** LONG bacağının engellemesi ≈başabaş
  (−225 / +76), koruma **SHORT bacağındadır** (+1523 / +1934).
- **Bacak başına atıf (V1c, AYI penceresi) — iki bacak İKİ FARKLI mekanizmadır:**

  | Bacak | Defter iyileşmesi | engelleme | ikinci-derece |
  |---|---|---|---|
  | SHORT | +2091.75 | **+1934.29 (%92)** | +157.46 (%8) |
  | LONG | +1136.11 | +76.12 (%7) | **+1059.99 (%93)** |

  Yani kapının GERÇEK koruması SHORT bacağında ve doğrudan engellemedendir (lider ralli
  gününde short açmamak); LONG bacağının düzelmesinin **%93'ü kapının kendisi değil,
  ikinci-derece terimdir**. Bu yüzden "düşen-bıçak LONG'ları kesiliyor" cümlesi yanlıştı.
- **AYI maxDD iyileşmesinin tamamı engellemeden:** ölçüldü — yalnız-engelleme kümesinin maxDD'si
  (2956.08) tam koşunun maxDD'si ile aynı, V0 ise 3682.60. Bu bir ÖLÇÜM sonucudur, teorem değil:
  yeniden tahsis, drawdown penceresine kâr eklerse maxDD'yi pekâlâ düşürebilirdi. (Nitekim V1
  YATAY'da yalnız-engelleme DD'si 3032.30'da kalıyor, tam koşu 2882.04'e iniyor — orada yeniden
  tahsis DD'yi de düşürüyor.)
- **Engellenen 12 AYI SL'i = −6169.50** rakamı, 20 satır yukarıdaki defter farkıyla (29→17 SL,
  −14907→−8738) **tesadüfen** birebir aynıdır: kapının açtığı koşuda yeni giren hiçbir işlem SL
  olmamıştır, bu yüzden iki büyüklük çakışır. Atıf hatası DEĞİLDİR.
- **P2 KRİTERLERİ yalnız-engelleme kümesinde de sağlanıyor** — AYI PF 1.210 (V1) / 1.289 (V1c)
  ≥ 1.1 ✓, BOĞA kaybı −%6.19 (V1) / −%2.67 (V1c) ≤ %20 ✓. Bu yeni bir P2 **hükmü değildir**
  (P2 gerçek bir koşu üzerinde tanımlıdır; bu küme V0'dan işlem çıkarılarak kurulmuş sentetik bir
  kümedir, kapasite kapısı yeniden koşulmamıştır) — bir **dayanıklılık kontrolüdür**.
- **İkinci-derece terimin MEKANİZMASI ölçüldü — "slot boşaldı" DEĞİL.** Önceki sürüm bunu
  küresel kapasiteye (`scalper_max_positions`) atfediyordu; ölçüm bunu ÇÜRÜTTÜ
  (`scripts/decompose_gate_runs.py --mechanism`, her yeni işlem EN DAR açıklamaya atanır):

  | Mekanizma | V1c AYI | V1c YATAY |
  |---|---|---|
  | sembol-içi **işgal penceresi** | **11 işlem / +1217.45 (%100)** | **3 / +242.39 (%100)** |
  | kayıp-cooldown'u | 0 / 0.00 | 0 / 0.00 |
  | kapasite / diğer | 0 / 0.00 | 0 / 0.00 |

  Yani yeni işlemlerin TAMAMI, kapının engellediği işlemin AYNI SEMBOLDEKİ
  `[giriş, çıkış]` penceresinin İÇİNDE açılıyor: `simulate_symbol` bir sembolde tek pozisyon
  tutar (`i = trade.exit_idx + 1`), bu yüzden o işlemler taban koşuda kapasiteye HİÇ SIRA
  GELMEDEN imkânsızdı. Kapasite fiilen bağlayıcı değil (V0 `capacity` sayacı 3, V1c 2 —
  8 sembol × `max_positions` 5). Bu, E8.6'nın bağımsız ölçümüyle aynı sonuçtur
  (işgal penceresi %100, kapasite 0, cooldown 0).
- **Yine de bir ölçüm eseri DEĞİL:** sembol-içi işgal penceresi canlıda da gerçektir (motor da
  bir sembolde tek pozisyon tutar), yani kazanca sayılır — yalnız ATFI doğru yapmak gerekir.
  ⚠️ Bu tarihsel E7 koşularında tam da bu kanal harness'ın en zayıf modellediği yerdi: 8
  saatlik reaper canlıda pencereyi ERKEN kapatıyor, o koşuların harness'ı kapatmıyordu
  (aşağıdaki reaper notu). D29'dan sonraki koşular bu boşluğu modellemektedir.
- Not: **+225** (üç pencere yalnız-engelleme toplamı, +224.82) ile **−225** (AYI LONG bacağı,
  −224.87) FARKLI büyüklüklerdir — yakınlıkları tesadüf.

⚠️ **Tarihsel reaper sapması — büyüklük DEĞİL, MARUZ KALAN KÜME ölçüldü.** E7 harness'ı
`SCALPER_MAX_HOLD_HOURS`'ü (D4, canlıda 8 sa) hiç uygulamıyordu; pozisyon SL/TP/trail'e kadar açık
kalıyordu. Reaper'ın gerçek popülasyonu (süre > 480 dk **ve** TP1 görmemiş — `_reap_aged_positions`
şartı) AYI penceresinde: V0'da **13 işlem / −6681.25**, V1 ve V1c'de **9 işlem / −4624.75**.
Kapının engellediği işlemlerin **4'ü** bu tanıma girer ve **−2056.50** taşır — yani V1 AYI
Δ'sının (+2415) **%85'i**, V1c'nin (+3228) **%64'ü**, canlıda 8 saatte MARKET ile kapanacak
pozisyonların harness'ta SL'ye kadar taşınmasına dayanıyor. Ayrıca sapmanın ikinci kolu (slotun
canlıda erken boşalması) tam olarak **yeniden tahsis kanalını** vurur — yani yukarıdaki tablonun
(b) sütunu harness'ın en zayıf modellediği mekanizmadır. **Net işaret ÖLÇÜLMEDİ ve tek bir
yüzdeyle özetlenemez**; ama etkinin dokunduğu taban Δ'nın çoğunluğudur → **E7'nin AYI sayıları
yukarı yanlı kabul edilmelidir.** D29 (2026-08-28) harness'a aynı sıra ile max-hold/REAPER
kapanışını ekledi ve kendi parite testiyle sabitledi; bu düzeltme tarihsel E7 sayılarını geriye
dönük değiştirmez. D29 ve sonraki koşular reaper-paritelidir. Betik:
`scripts/decompose_gate_runs.py --reaper`.

**Lider (BTCUSDT) tetik istatistiği** (pencere içi günler, `1d` serisinden türetildi):
| Pencere | gün-içi %1 LONG-blok günü | gün-içi %1 SHORT-blok günü | uzama %15/3g LONG-blok | uzama %15/3g SHORT-blok |
|---|---|---|---|---|
| AYI | 11 | 5 | 0 | **1** (2026-02-06, koşu −%20.1) |
| YATAY | 4 | 7 | 0 | 0 |
| BOĞA | 1 | 3 | 0 | 0 |
(Gün sayıları gün-SONU ölçümüdür; kapı dakika bazında değerlendirildiği için aynı gün içinde
iki yön de tetiklenebilir — bu yüzden tetik sayısı gün sayısından çok daha büyüktür.)

### Kanıtın gücü — dürüst değerlendirme
(2026-08-23 incelemesinde GÜNCELLENDİ: mekanizma cümleleri ayrıştırmanın ölçtüğüyle
değiştirildi, reaper sapması eklendi. Bölümün kendisi bir ara SİLİNMİŞTİ — geri kondu:
ölçüm tablosunu kanıt-gücü değerlendirmesi olmadan bırakmak CLAUDE.md'nin "kenar incedir,
rejime bölünmeden kabul edilmez" ilkesine aykırı.)
- **Gün-içi alt-kapısı (V1/V1c): kanıt orta-güçlü.** Üç pencerede de tetikleniyor (196/62/15),
  AYI'da 16 farklı güne yayılıyor. Mekanizma **ayrıştırmayla** doğrulandı: koruma SHORT
  bacağında ve doğrudan engellemeden (V1c AYI: SHORT defter iyileşmesinin %92'si), LONG
  bacağının düzelmesi ise %93 ikinci-derece. Kanıt yine de **tek lider** (BTCUSDT) ve **tek
  21 günlük ayı penceresi** üzerinden; AYI'daki +2415/+3228'in büyük kısmı 02-05/02-06
  çöküş-toparlanma çiftinden geliyor. ⚠️ Üstüne **reaper sapması** biniyor (yukarıda): AYI
  Δ'sının çoğunluğuna dokunan bir küme, canlıda 8 saatte MARKET ile kapanacak pozisyonların
  harness'ta SL'ye taşınmasına dayanıyor → **AYI sayıları yukarı yanlı**.
- **Uzama alt-kapısı (V2): kanıt ZAYIF.** 60 tetiğin tamamı AYI penceresinde ve **TEK bir
  lider olayından** (2026-02-06, 3 günlük −%20.1 koşu) geliyor; %15 ile %20 eşikleri
  **birebir aynı** sonucu veriyor (arada hiç olay yok), YATAY ve BOĞA'da hiç tetiklenmiyor.
  n=1 olay = istatistik değil, anekdot. Ayrıca %10'a gevşetmek BOĞA'yı −%24.6 ile P2'den
  düşürüyor (08-20'de BTC'nin +%10.2'lik 3 günlük koşusu LONG'ları vetoluyor) — yani eşik
  duyarlılığı yüksek ve yanlış tarafa ayarlanırsa doğrudan zarar veriyor.
  **Ek (E8): harness'ın "üç pencerede inert" hükmü BUGÜNKÜ piyasaya taşınmıyor.** O pencerelerde
  BTC 3 günde %15 koşmadığı için kapı hiç tetiklenmiyordu; botun ŞU AN soak ettiği dönemde
  koşuyor — 7–22 Ağu canlı defterinde `RUN_PCT=15` **202 işlemin 35'inde tetikleniyor ve net
  −152.7 ediyor** (12 DOWN-günü işlemini engelleyip +137.9 kurtarıyor, 23 UP-günü KAZANANINI
  engelleyip −290.6 kaybettiriyor). "Harness'ta zararsız" ≠ "canlıda zararsız".
  Bu iki bağımsız gerekçe yüzünden varsayılan **0'a çekildi** (D15 "Varsayılanlar").
- **V3 ≡ V1** olduğu için uzama alt-kapısını gün-içi ile BİRLİKTE açmanın ölçülebilir hiçbir
  faydası yok; tek başına açmanın da (V2) faydası tek olaya dayanıyor.
- Simülatörün mutlak sayıları rejime duyarlıdır (P3); yukarıdaki hüküm **göreli** farklara
  dayanır ve canlı defter nihai hakemdir.

### Kapı sertleştirmesi sonrası regresyon — 2026-08-23
Kapının GÖRÜNÜRLÜK/tazelik sertleştirmesi (lider doğrulaması, negatif önbellek, oran-sınırlı
WARNING, tur başı tazeleme, UTC gün damgalı önbellek, `run_days+5`, rapor provenance'ı — D15)
kapı KURALINI değiştirmez; bunu kanıtlamak için V1c AYI penceresi aynı env tabanı ve aynı kline
önbelleğiyle YENİDEN koşuldu.

| | E7 kaydı (V1c AYI) | Sertleştirme sonrası | |
|---|---|---|---|
| İşlem | 158 | **158** | ✓ |
| Kazanma % | 88.0 | **88.0** | ✓ |
| PnL | +3812 | **+3812.25** | ✓ |
| PF | 1.43 | **1.43** | ✓ |
| maxDD | 2956 | **2956.08** | ✓ |
| `market_gate_day` tetiği | 179 | **179** | ✓ |
| LONG / SHORT | 64 / 94 | **64 (+179.82) / 94 (+3632.43)** | ✓ |

Log: `logs/market_gate/v1c_ayi_hardening.log`; inceleme düzeltmeleri TAMAMLANDIKTAN sonra bir
kez daha koşuldu → `logs/market_gate/v1c_ayi_verify.log` (rapor
`logs/backtest_20260823_105946.json`), **yedi satırın hepsi yine birebir aynı** ve
`missed_signals` = `{'regime_gate': 558, 'market_gate_day': 179, 'maker_missed': 7,
'capacity': 2}`. Komut E7'nin komutuyla aynı (env tabanı
`scripts/.scalper_env_snapshot.txt`, `--cache-dir data/klines_cache`).
`python3 -m pytest tests -q` → **825 passed, 1 skipped**; `tests/test_golden_backtest.py`
değişmeden geçer.

⚠️ **Bu koşuda bir HATA da yakalandı** (uçtan uca doğrulamanın değeri): `metadata["market_gate"]`
üretiliyor ama JSON rapora HİÇ ulaşmıyordu. Kök neden `run_backtest`'te `run_metadata.update(
metadata)`'nın SIĞ bir kopya olması — iç içe sözlüklere yapılan yerinde değişiklikler
(`data_windows`) dışarı ulaşıyor, SONRADAN eklenen yeni anahtar kayboluyordu. Düzeltildi
(`metadata` artık `run_metadata`'nın kendisi) + regresyon testi
(`TestRunMetadataPropagation`). Yalnız birim testiyle bakılsaydı fark edilmezdi.

### E8 (sinyal otopsisi) ile çapraz kontrol — 2026-08-23
E8 ajanı kapıyı BAĞIMSIZ olarak, harness JSON'u üzerinde **post-hoc** ölçtü (her işlemi giriş
zamanıyla zenginleştirip filtreleyerek). İki yöntem farklı şeyler ölçüyor ve bu fark önemli:

| | E7 (bu bölüm) | E8 (post-hoc) |
|---|---|---|
| Kapı nerede | Motor-içi, gerçek `simulate_symbol` kapısı | İşlem listesi üzerinde filtre |
| Kapasite | Engellenen sinyal slotu SERBEST bırakır (P1 kapasite paritesi) | Bırakmaz → **alt sınır** |
| YATAY %1.0 sonucu | **+201** | **−487** |
İşaret farkının kaynağı kapasite yeniden tahsisi: gerçek motorda engellenen bir sinyal slotu
boşaltır ve sonraki (çoğu kez daha iyi) sinyal girebilir. E8 bunu kendisi de "gerçek motor-içi
kapının ALT SINIRI" diye işaretledi — iki ölçüm çelişmiyor, E8 muhafazakâr taraftan bakıyor.

**E8'in eşik önerisi (%1.3) motor-içi kapıyla DOĞRULANDI ve benimsendi:** V1c üç pencerede de
V1'i (%1.0) domine ediyor — AYI +2999→**+3812** (PF 1.33→1.43), YATAY +2593→**+2791**
(1.36→1.38), BOĞA kaybı −%4.5→**−%2.7**; maxDD hiçbir pencerede kötüleşmiyor.
V1c AYI yön kırılımı: LONG 64/**+180** (PF 1.04), SHORT 94/**+3632** (1.84), SL 17 (V0: 29).
**Bacak atfı — ilk yorumum YANLIŞTI, E8 haklıydı.** "E7'de LONG bacağı da iyileşiyor (79/−956 →
64/+180), demek ki LONG kapısı da değerli" demiştim. E8 bunun kapasite etkisinden ayrılmadığını
söyledi ve ayrıştırmayı önerdi; iki JSON raporunu (symbol, entry_time, direction) üzerinden
eşleştirerek koştum (yeni backtest YOK):

| AYI, V0 → V1c | Δ PnL | (i) engellenen | (ii) yeni giren | Atıf |
|---|---|---|---|---|
| **LONG** | +1136.1 | 24 işlem, PnL **−76.1** (≈başabaş) | 9 işlem, **+1060.0** | **~%7 kapı, ~%93 kapasite** |
| **SHORT** | +2091.8 | 42 işlem, PnL **−1934.3** (gerçek kaybedenler) | 2 işlem, +157.5 | **~%92 kapı, ~%8 kapasite** |

(Ortak 55 LONG / 92 SHORT işlemin PnL'i iki koşuda birebir aynı — atıf temiz.)
Yani kapının KENDİSİ neredeyse tamamen SHORT bacağından kazandırıyor; LONG bacağının engellediği
24 işlem toplamda −76.1, yani gürültü. LONG'daki +1136'nın %93'ü boşalan kapasiteye giren 9 YENİ
işlemden geliyor — kapıya değil, kapasite yeniden tahsisine ait bir kazanç.
**Bacak-ayrık eşik artık İKİ ölçümle de destekleniyor** ama yine de UYGULANMADI: ayrı bir tasarım
kararıdır (kendi spec'i + kullanıcı onayı gerekir). Karşı argüman kayda geçsin: canlı defterin
22 Ağu kaybı tam olarak LONG bacağından geldi (8 işlem, +102.1 kurtarırdı), yani harness'ta nötr
görünen LONG bacağı canlıda hâlâ işe yarayabilir — bacakları ayrı kapatılabilir tutan bugünkü
tasarım bu yüzden doğru.

**Uzama alt-kapısı — ikinci bağımsız RED.** E8 canlı defterde net **negatif** ölçtü (−152.7;
LONG eşiği %12'de −382.9, 50 kazanan engelleniyor) ve spec'in hipotezinin İŞARETİNİ ters buldu
(kazananların `align_btc_run_3d` ort. 7.50 / kaybedenlerin 2.28; AUC 0.292, p<0.001 — yani koşuyla
AYNI yönde açılan işlemler kazanıyor). E7'de zaten "n=1 olay, gün-içinin üstüne katkı yok"
demiştik. İki bağımsız kanıt aynı yöne işaret ediyor → **`SCALPER_MARKET_GATE_RUN_PCT=0` kalmalı.**
Varsayılan spec'te 15 onaylandığı için sessizce değiştirilmedi; bunun yerine motor açılışta
uyarıyor (`ScalperEngine._maybe_log_market_gate_banner`).

### "Gün açılışı" tanımı — ÇÖZÜLDÜ (E8'in 15m yolu), önceki sapma kapatıldı
E8 ölçümünü gerçek `1d` mumu **open**'ı ile yaptı; bu uygulama son tamamlanmış günlük
**close**'u vekil kullanıyor (gerekçe: `KlineFetcher._drop_unclosed` oluşmakta olan günlük mumu
her zaman atar → canlıda "bugünün open'ı" ELDE EDİLEMEZ; bkz. D15). İki tanım arasındaki fark
ÖLÇÜLDÜ (BTCUSDT, üç pencere, 70 gün, mainnet — harness'ın veri kaynağı):

| Kaynak | Ortalama \|open−önceki close\| | Maksimum | Eşiğin (%1.0) kaçta kaçı |
|---|---|---|---|
| **Mainnet** (harness) | %0.000082 | %0.000597 | %0.06 |
| **Testnet** (canlı motor) | %0.013 | %0.152 | **%15.2** |

Mainnet'te iki tanım pratikte AYNI (fark eşiğin binde 6'sı); testnet'te fark ~200× büyüktü
(en kötü günde eşiğin %15'i, dağılım kuyruklu: medyan %0.000167, p95 %0.106) ve bu bir süre
"bilinen sapma" olarak kaydedildi.

**E8 bu sapmayı kapatan yolu buldu ve uygulandı:** `1d` mumunun `open`'ı, o günün **00:00 UTC
15m mumunun `open`'ına BİREBİR eşittir** (ikisi de aralığın ilk işlem fiyatıdır). Bağımsız
doğruladım — BTCUSDT, mainnet (3 pencere önbelleği) + testnet, **76 gün sınırı, 0 uyuşmazlık,
maks fark %0.00000000**. Böylece gerçek gün açılışı `_drop_unclosed`'a HİÇ dokunmadan (o 15m
mumu çoktan kapanmıştır) okunabiliyor:
- Motor: lider için `15m` limit 100 (= 25 saat, ağırlık 1). Kapı açıkken lider başına toplam
  **3 istek / 60 sn** (`1d` + giriş TF + `15m`, üçü de ağırlık 1) — sembol başına DEĞİL.
- Harness: `gather_leader_series` aynı 15m serisini `gather_symbol_data` ile AYNI önbellek
  anahtarıyla çeker → lider evrende zaten varsa **ek ağ isteği YOK** (loglarda
  `💾 BTCUSDT 15m: önbellekten yüklendi`).
- Ortak kural `market_gate.resolve_day_open`; günün ilk 15 dakikasında (mum henüz kapanmamış,
  look-ahead yasak) **iki taraf da** eski vekile düşer — `day_open_source` alanı hangisinin
  kullanıldığını `/scalper/status`'te gösterir.
- Neden liderin 15m'si AYRI çekiliyor (motorun zaten çektiği 15m serisi kullanılamaz mı?):
  motorun `_evaluate_symbol` içinde çektiği seri DEĞERLENDİRİLEN SEMBOLE aittir, lidere değil;
  ayrıca o serinin zaman dilimi `scalper_tf_regime`/`tf_context` ile konfigüre edilebilir
  (sunucuda 15m ama garanti değil) ve kapı anlık görüntüsü lider başına önbelleklenir, hangi
  sembolün tetiklediğinden bağımsızdır. Sembol==lider durumunda yeniden kullanmak yalnız 8
  sembolün 1'inde işe yarar, konfigürasyona bağımlı ve kırılgan olurdu.

**Regresyon kontrolü:** V1 ve V1c üç pencerede de yeniden koşuldu; sonuçlar önceki (vekil)
koşularla **bit düzeyinde AYNI** (V1c AYI 158 işlem / WR 88.0 / +3812.25 / PF 1.43 / DD 2956.08,
179 tetik · YATAY 137 / 87.6 / +2791.37 / 1.38 / 2840.06, 32 tetik · BOĞA 89 / 93.3 / +3797.60 /
2.39 / 734.59, 3 tetik). Mainnet verisinde değişim inert — E7 tablosu her iki tanım altında da
geçerli — ama testnet soak'undaki belirsizlik artık YOK.
Eski (vekil) koşu logları `logs/market_gate_prevclose/` altında saklandı.

## 2026-08-24 — D24 ölçüm/kanıt paketi (motor davranışı DEĞİŞMEDİ)

Dört harici deponun (IAF · AI-Trader · jane-street-skills · OpenTrade) incelemesinden
çıkan, **kâr değil kanıt kalitesi** getiren yedi madde. Hiçbiri motorun karar yoluna
girmez; hepsi ya salt-rapor ya varsayılan-kapalı bayraktır. `tests/test_golden_backtest.py`
altın sayıları **DEĞİŞMEDİ** (2 işlem / `total_pnl` 26.77 / `{"regime_gate": 4}`).

### D24.1 — Bar-bazlı çöküş, kapanış-bazlının GÖRMEDİĞİ çukuru gösteriyor
Altın koşuda (BTCUSDT+ETHUSDT, 2026-08-07→08-10, `SCALPER_TF_REGIME=15m`):

| Metrik | Değer |
|---|---|
| `max_drawdown` (bugünkü, yalnız işlem KAPANIŞLARINDA örneklenir) | **0.00** |
| `bar_max_drawdown` (yeni, her 5m barında mark-to-market) | **11.46** |
| Toplam PnL | 26.77 |
| Bar-bazlı çukurun toplam kâra oranı | **%42.8** |
| Bar işareti sayısı / çukur zamanı | 80 / `2026-08-07T09:34:59Z` |
| İşlem başına en derin bar-içi çukur | −4.92 (işlem #1) · −7.82 (işlem #2) |

Okuma: bu pencerede iki işlem de kazandı, bu yüzden kümülatif PnL hiç düşmedi ve
bugünkü metrik **sıfır risk** raporluyordu; oysa portföy bar-içinde kârın %43'ü kadar
su altındaydı. 1000 USD sermaye ve %10/işlem sabit boyutta bu doğrudan hayatta kalma
sorusudur. Değişmez (test edildi): aynı taban + daha sık örnekleme → `bar_max_drawdown`
`max_drawdown`'dan **küçük olamaz**.
Kod: `backtest.bar_equity_series` / `bar_drawdown` / `_mark_equity`.
Test: `tests/test_backtest_measurement.py::TestBarDrawdownIsDeeperThanCloseBased`.

### D24.2 — Monte-Carlo permütasyon: kelepçe ZORUNLU, ölçüldü
Altın fixture'ı üzerinde 60 tur × 2 sembol, tohum 12345, `--permutation-clamp-audit`
(AĞ YOK — veri `series_out` ile ilk koşudan devralınır):

| Metrik | Yön | Gerçek | Null ort | Null p05 | Null p95 | p |
|---|---|---|---|---|---|---|
| `total_pnl` | büyük | 26.77 | −4.72 | −78.66 | 45.06 | 0.180 |
| `profit_factor` | büyük | ∞ (hiç kayıp yok) | 1.45 | 0.00 | 3.68 | **üretilmedi** |
| `winrate` | büyük | 100.0 | 69.94 | 0.00 | 100.0 | 0.295 |
| `max_drawdown` | **küçük** | 0.00 | 32.08 | 0.00 | 87.82 | 0.295 |
| `bar_max_drawdown` | **küçük** | 11.46 | 58.24 | 12.49 | 107.24 | **0.066** |

**Kelepçe ölçümü (planın 1. zorunlu düzeltmesi).** Upstream dört göreli bileşeni
bağımsız karıştırdığı için permüte barlarda OHLC tanımı bozuluyor. Ölçtük
(103.680 permüte bar): **High < max(O,C) → %28.2** · **Low > min(O,C) → %29.4** ·
en az bir ihlal taşıyan bar **%57.6**. Kelepçenin düzeltme büyüklüğü: ortalama
%0.0416, en büyük %0.5953 (fiyat cinsinden).
**Kelepçenin null'u kaydırması** (kelepçeli − kelepçesiz, AYNI tohumlar):
`winrate` null ortalaması **+7.61 puan** · `bar_max_drawdown` **+2.84** ·
`total_pnl` **+2.21** · `max_drawdown` **+0.58** · `profit_factor` **+0.66**.
Yani kelepçesiz null, permüte dünyayı sistematik olarak TP/trail aleyhine bozuyor ve
gerçek sonucu olduğundan anlamlı gösteriyordu — kelepçe kozmetik değil.
(p-değeri farkları N=60'ta ±0.05 mertebesinde, yani 3 permütasyon; bu ölçekte
gürültüdür — güvenilir sinyal null ORTALAMASINDAKİ kaymadır.)

**Yön ölçümü (2. zorunlu düzeltme).** `max_drawdown`/`bar_max_drawdown`'da küçük olan
iyidir. Upstream'in sabit `mean(dist >= real)` yönü, sentetik doğrulama vektöründe
p=0.05 yerine **p=0.96** üretiyor (`tests/test_permutation.py::
test_lower_is_better_direction_is_inverted`). Yönü tanımlı olmayan metrik için
p-değeri hiç üretilmez; `profit_factor=∞` gibi sonlu olmayan gerçek değerde de
üretilmez (yukarıdaki tabloda "üretilmedi").

**Null'un kapsamı (dürüstlük).** Permütasyon YALNIZ giriş dilimine uygulanır; bağlam
ve rejim dilimleri permüte seriden `aggregate_from` ile TÜRETİLİR, permüte serinin
kapsamadığı daha eski rejim barları GERÇEK kalır. Yani p-değeri şu DAR soruyu
yanıtlar: *"rejim arka planı aynıyken, giriş sinyalinin kendisi şanstan ayırt
edilebilir mi?"* Koşulsuz bir null DEĞİLDİR.

Komut:
```bash
env $(ssh awa grep ^SCALPER_ /opt/tradingbot-v2/.env | xargs) python3 -m src.strategies.scalper.backtest \
  --strategies C --symbols BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,BNBUSDT,ADAUSDT,LTCUSDT \
  --start 2026-01-23 --end 2026-02-13 --cache-dir data/klines_cache \
  --permutations 200 --permutation-clamp-audit
```
Maliyet uyarısı: permütasyon **N× simülasyon** demektir (audit ile 2N). Altın
fixture'da 60×2 tur 23 sn sürdü; 8 sembol × 3 hafta ölçeğinde tur başına dakikalar
beklenir — sıralı koşun.

### D24.3 — Konsantrasyon (altın koşu)
`top_symbol` BTCUSDT %100 (bu pencerede ETHUSDT hiç işlem üretmiyor — bilinen ve
belgeli) · `top_trade_pnl_share` **%60.5** · `top_day` 2026-08-07 **%60.5** ·
2 gün / 1 sembol. Canlı defter tarafında aynı tanım `scripts/ledger_report.py`
"5) ÖZET" bloğunda ("Yoğunluk/sembol · /işlem · /gün"). Pay YALNIZ toplam PnL
POZİTİFKEN tanımlıdır; değilse `—` (tanımsız) — "ölçülmedi" değil.
**Eşik DEĞİL, bilgi satırı**: soak kontrol listesine girmez (D#P1 paritesi).

### D24.4 — Maliyet stresi / giriş gecikmesi: ölçülmedi (yalnız araç hazır)
`--fee-stress` (komisyon+kayma 2×) ve `--entry-delay-candles N` bayrakları eklendi;
`SCALPER_SLIPPAGE_RATE` env'e taşındı (varsayılan **0.0002 = DEĞİŞMEDİ**, config
fail-fast: oran, yüzde değil; üst sınır 0.01). Üç rejim penceresinde stres koşusu
**bu commit'te KOŞULMADI** — koşulduğunda sonucu buraya, kendi başlığı altında yazın.
Beklenen okuma: başabaş WR ≈ %85 olan bir kenarda kaymayı 2× yapmak sonucu tersine
çeviriyorsa, canlı kenarın kaymaya duyarlılığı belgelenmiş olur.

### D24.5 — Niyet kaydı ve beklenti alanları: kapsama bugün SIFIR
`logs/trades.jsonl`'e `event="intent"` satırı (proposed → decided → executed) ve
`/scalper/forensics/summary` yanıtına `intents` bloğu eklendi. Sayaçlar **süreç
başlangıcından beridir** ve restart'ta sıfırlanır (`window: "process_start"`).
`horizon_end_at`/`invalid_if`/`confidence`/`model_version` alanları şemaya girdi ama
onları DOLDURAN bir yol henüz yok (D23 ajanı bağlayacak) → rapor bugün
`with_expectation: 0` döner. Bu **doğru** sonuçtur: null = "ölçülmedi".

## E10 — Permütasyon testi: C stratejisinin girişi şanstan ayırt edilebiliyor mu? (2026-08-24)

**Soru.** Bugüne kadar hiç yanıtlanmadı: bir backtest sonucunun (PF 1.43) şans eseri
olma olasılığı nedir? `compute_stats` tek koşunun sayısını verir, null dağılım yoktu.

**Yöntem.** D24 ile gelen Monte-Carlo permütasyon testi (`--permutations`), AYI
penceresi (2026-01-23→02-13), C, 8 sembol, 50 tur, tohum 12345, süre 2177 sn.
Null KOŞULLU: yalnız giriş dilimi permüte edilir, bağlam/rejim ondan türetilir →
soru "rejim arka planı aynıyken giriş sinyalinin kendisi şanstan ayırt edilebilir mi".
OHLC tutarlılığı için High/Low kelepçesi zorunlu (barların %53.7'si düzeltildi;
kelepçesiz null TP/trail aleyhine sistematik bozuk — bkz. D24).

| Metrik | Yön | Gerçek | Null ort. | Null p05 | Null p95 | p |
|---|---|---|---|---|---|---|
| total_pnl | büyük iyi | **3812.25** | −7476.28 | −13439.68 | −1569.01 | **0.0196** |
| profit_factor | büyük iyi | **1.43** | 0.75 | 0.60 | 0.93 | **0.0196** |
| winrate | büyük iyi | 87.97 | 86.03 | 83.44 | 88.58 | 0.1373 |
| max_drawdown | küçük iyi | 2956.08 | 9161.00 | 5013.71 | 14046.27 | 0.0392 |
| bar_max_drawdown | küçük iyi | 2973.70 | 9330.25 | 5199.42 | 14373.33 | 0.0392 |

**Sonuç.** Ayı penceresinde giriş sinyali şanstan ayırt edilebilir: 50 turun HİÇBİRİ
gerçek PnL'i geçemedi (p = 1/51 = bu tur sayısının tabanı; gerçek p daha küçük olabilir,
daha fazla tur daraltır). Rastgele girişle aynı kurallar ortalama **−7476** kaybediyor.

**Kritik ayrıntı — kenar NEREDEN gelmiyor:** kazanma oranı anlamlı DEĞİL (p=0.137).
Rastgele girişler de %86 kazanıyor, çünkü %85 başabaş oranı TP/SL asimetrisinin
YAPISAL sonucu. Kenar kazanma oranından değil, kayıp büyüklüğünün kontrolünden
(PnL + düşüş) geliyor. "Kazanma oranımız %88" cümlesi bu yüzden tek başına kanıt değildir.

**Çekinceler.** (1) Null KOŞULLUDUR, koşulsuz değil. (2) Tek pencere — YATAY ve BOĞA
ayrıca koşulmalı (koşuluyor). (3) 50 tur p tabanını 0.0196'ya kilitler. (4) Bu test
stratejinin CANLIDA kâr edeceğini söylemez; yalnız backtest sonucunun rastgelelikle
açıklanamadığını söyler. (5) Aynı üç pencere sorunu (bkz. yukarıdaki metodoloji kutusu)
burada da geçerlidir.

**Yan ölçümler (aynı pencere, tek koşu):**
| Senaryo | İşlem | WR% | PnL | PF |
|---|---|---|---|---|
| Taban | 158 | 88.0 | 3812.25 | 1.43 |
| Maliyet 2× (`--fee-stress`) | 158 | 88.0 | 1602.18 | 1.17 |
| Giriş 1 mum geç (`--entry-delay-candles 1`) | 133 | 85.7 | 1923.37 | 1.21 |

Kenar iki strese de dayanıyor ama ince: maliyet iki katına çıkarsa kârın %58'i, giriş
bir mum gecikirse %50'si gidiyor. Mainnet kayması testnet'ten yüksektir — bu tablo
canlı para kararında bağlayıcıdır.

## E11 — Çıkış tarafı: REAPER kaybı ve bayat-kâr kapanışı `STALE_TP` (2026-09-03, D30)

**Soru.** 2026-08-22'den beri canlı defter bozuldu. Çıkış tarafında pencereler arasında
tutarlı bir düzeltme var mı? (Karar: `docs/DECISIONS.md` D30.)

**Kurulum.** C-only, 8 majör, sunucu env'i (`SCALPER_*`/`TV_*` — gizli değerler
yok), mainnet mumları. Beş pencere: AYI 01-23→02-13 · YATAY 07-01→07-21 · BOĞA
08-07→08-21 · OOS Mart 03-01→04-01 · ÇÖKÜŞ 08-21→09-03. Baz koşular
`logs/backtest_20260903_122725/123053/123200/123333/130010.json`.

**Bulgu.** 886 işlemde `REAPER` 165 işlem / WR %14.5 / **−958**; SL 83 / −1.9k;
TRAIL +3.2k. Süre kovaları: 30–240 dk PF 1.6–7, >360 dk PF < 0.3. Kaybeden REAPER
işlemlerinin ~%40'ı bir noktada MFE ≥ %5 ROI görmüştü (TP1 = %10).

**Varyantlar (toplam net / OOS Mart):** hold 3 sa +305/−76 · stop %30 +256/−191 ·
rejim TF 1h +362/−51 · UP-LONG yasak +377/−77 · **STALE_TP 2 sa/%2 +448/+11**
(ızgara 1.5–2 sa × %0–2 düz: +420–450). Deney koşucusu harness'i monkeypatch
etmişti; kural koda alındıktan sonra AYNI sayılar `SCALPER_STALE_TP_HOURS=2
SCALPER_STALE_TP_MIN_ROI_PCT=2` ile birebir yeniden üretildi (parite):
ÇÖKÜŞ −121.60 → `logs/backtest_20260903_140948.json`, OOS +10.57 →
`logs/backtest_20260903_141053.json`.

**Holdout (seçimde kullanılmamış, sonuçtan önce kilitlendi):**

| Pencere | Baz | STALE_TP 2 sa/%2 | Log |
|---|---|---|---|
| 05-04→05-25 | −336.80 / PF 0.61 / DD 359 · REAPER 50/−423 · TRAIL 125/+523 | −381.61 / PF 0.51 / DD 403 · REAPER 28/−335 · STALE_TP 83/+67 · TRAIL 78/+323 | `141831` / `141907` |
| 06-08→06-29 | +70.64 / PF 1.13 / DD 98 · REAPER 38/−301 · TRAIL 140/+608 | +58.55 / PF 1.13 / DD 108 · REAPER 24/−273 · STALE_TP 67/+77 · TRAIL 101/+439 | `142233` / `142313` |

**Sonuç.** İki holdout penceresinde de net düştü ve düşüş büyüdü → **REDDEDİLDİ**
(P2: OOS kötüleşmesi tek başına veto). Mekanizma açık: kural REAPER zararının
küçük bir kısmını kesiyor ama TP1'e varacak koşucuların TRAIL kazancını daha çok
kesiyor. Seçim pencerelerindeki +109'luk kazanç o pencerelere uyumdu. Ders:
5 pencerelik ızgara bile holdout'un yerini tutmuyor; bundan sonra her çıkış
kuralı adayı seçimden ÖNCE kilitlenmiş en az iki taze pencerede koşulmalı.

**Komut (holdout, harness'e dokunmadan `settings` alanı ile):**
```
python -m src.strategies.scalper.backtest --strategies C \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,BNBUSDT,ADAUSDT,LTCUSDT \
  --start 2026-05-04 --end 2026-05-25     # SCALPER_STALE_TP_HOURS=0 | =2
```

## E12 — "Daha çok işlem" için diverjans şartını gevşetmek (2026-09-03 gece) — RED

**Soru.** Kullanıcı daha fazla işlem istiyor. Canlı defterde kârlı hücre UP-günü LONG dip alımları;
diverjans şartı (D6) işlem sayısını ~3-4× azaltıyor. Şart kaldırılınca (özellikle UP/LONG hücresinde)
kazanç artar mı?

**Kurulum.** Aynı kod (ed80e4e harness), sunucu env (D28 profili, marj 50, 1m/5m/15m, lider kapısı açık),
8 majör, 7 pencere; tek fark `SCALPER_C_REQUIRE_DIVERGENCE=false`. Pencere sınıfları: SEÇİM (AYI/YATAY/BOĞA)
ve DOKUNULMAMIŞ (Mart/Çöküş/Mayıs/Haziran).

| Pencere | div=AÇIK (taban) | div=KAPALI | Log |
|---|---|---|---|
| AYI 01-23→02-13 | 161 / +334.4 / PF 2.07 / DD 80 | 593 / +345.3 / PF 1.19 / DD 315 | `logs/backtest_20260903_213418.json` |
| YATAY 07-01→07-21 | 156 / +240.6 / 1.92 / 42 | 432 / −180.1 / 0.88 / 459 | `…_213507.json` |
| BOĞA 08-07→08-21 | 118 / +79.3 / 1.37 / 61 | 254 / +115.5 / 1.21 / 152 | `…_213528.json` |
| MART 03-01→04-01 | 316 / −130.4 / 0.88 / 190 | 791 / −486.7 / 0.84 / 584 | `…_213719.json` |
| ÇÖKÜŞ 08-21→09-03 | 135 / −185.1 / 0.67 / 268 | 381 / −229.6 / 0.84 / 503 | `…_213744.json` |
| MAYIS 05-04→05-25 | 192 / −336.8 / 0.61 / 359 | 410 / −822.1 / 0.55 / 916 | `…_213825.json` |
| HAZİRAN 06-08→06-29 | 190 / +70.6 / 1.13 / 98 | 499 / −144.0 / 0.92 / 333 | `…_213920.json` |

**Hücre bazında (KAPALI − AÇIK, 7 pencere toplamı):** UP LONG **−765.6**, RANGE SHORT −697.3, DOWN SHORT −39.5,
RANGE LONG +28.1. Yani "kârlı hücrede diverjanssız daha çok giriş" fikri de çürüdü: UP LONG hücresi
diverjanssız yalnız BOĞA'da (+84) kazanıyor, dokunulmamış dört pencerenin dördünde daha kötü
(Mart −366, Mayıs −249, Haziran −175, Çöküş −25).

**Sonuç.** Diverjans şartı her rejimde ve her yönde kalır; işlem sayısını filtre gevşeterek artırmak
her pencerede maksimum düşüşü 2-3× büyütüyor. "Daha çok işlem" ancak YENİ bilgi taşıyan bir sinyal
kaynağıyla (mevcut filtreyi gevşeterek değil) düşünülebilir. Komut:
`env $(grep ^SCALPER_ scripts/.scalper_env_snapshot.txt | xargs) SCALPER_C_REQUIRE_DIVERGENCE=false
python3 -m src.strategies.scalper.backtest --strategies C --symbols <8 majör> --start … --end …`.

## E13 — Deterministik giriş-filtresi taraması (post-hoc, 7 pencere, 2026-09-04 gece)

**Soru.** AI olmadan, yalnız giriş anında bilinen bilgiyle (saat/gün, rejim×yön, sembol, volatilite),
C'nin dokunulmamış pencerelerdeki bozulmasını tutarlı biçimde düzelten bir kural var mı?

**Kurulum.** 7 taban JSON (E11 ile aynı kod/env; taban `logs/backtest_20260903_{123200,123053,122725,
123333,130010,141831,142233}.json`), 1.268 işlem. Pencere sınıfı: SEÇİM (AYI/YATAY/BOĞA — 21 Ağu'dan beri
ayar seçiminde kullanıldı) vs DOKUNULMAMIŞ (Mart/Çöküş/Mayıs/Haziran). 5 mercek (saat-gün, rejim×yön,
sembol, volatilite-süre, kapasite) ~305 kural taradı; yargıç + bağımsız yeniden hesap (aşağıdaki tablo).
Karar kuralı: dokunulmamış 4'te toplam net ↑ ve ≥3/4 pencere kötüleşmez (tolerans brütün %5'i); seçim
toplam kaybı ≤ %20; her pencerede kalan işlem ≥ %60; yalnız giriş-anı bilgisi; komşuluk sağlam.

| Kural (post-hoc) | Δ dokunulmamış | Δ seçim | kötüleşen dok. | min kalan | Mart/Çöküş/May/Haz |
|---|---|---|---|---|---|
| Hafta sonu LONG yasak | **+232.7** | −35.4 (%5) | 1 (Haz −59) | %81 | +99 / +163 / +29 / −59 |
| ADA+DOGE LONG yasak | +204.2 | −24.2 | 0 | %85 | +75 / +22 / +79 / +28 |
| UP rejim LONG yasak | +279.6 | −110.1 (%17; BOĞA −84 = tüm kârı) | 0 | %68 | +131 / +70 / +77 / +1 |
| Saat 11-14 UTC yasak | +289.5 | −37.7 | 0 | %85 | +105 / +36 / +137 / +11 |
| Saat 00-06 UTC yasak | +338.1 | −273.6 (%42 → RED) | 0 | %68 | +86 / +137 / +79 / +35 |
| RANGE SHORT yasak | +183.9 | −161.1 (%25 → RED) | 1 | %61 | −13 / +25 / +230 / −59 |
| Hafta sonu hepsi yasak | +306.6 | −51.1 | 1 | %69 | +29 / +235 / +104 / −61 |
| DOGE LONG yasak | +109.8 | +1.2 | 1 | %91 | +55 / −23 / +48 / +30 |

**Çoklu-test gerçeği (yargıç).** ~305 kural için Bonferroni eşiği 1.6e−4, BH q=0.10 için ≤3.3e−4;
gözlenen en küçük OOS p ≈ 0.004. Küresel sıfır altında ~15 kural p<0.05 beklenir; bulunan 3.
**Taramanın bütünü gürültüden ayırt edilemez.** Yalnız a priori mekanizması olan kurallar (hafta sonu
likidite → LONG dip alımları 8 saatte çürüyor: engellenen hafta sonu LONG'ların REAPER/SL ağırlığı)
"koşullu aday" sayılır; sembol-özgü kurallar (ADA/DOGE) madenciliktir.

**Yapısal teşhis.** Dokunulmamış dört pencerede LONG tarafı sistematik zararda (UP LONG ve RANGE LONG
ikisi de); DOWN SHORT 7 pencerenin 5'inde pozitif, en tutarlı hücre. Bu bir 2026 Mar–Ağu rejim
özelliği olabilir; kural değil, izleme konusu.

**Sonraki adım.** Post-hoc tarama kapasite/cooldown ikamesini görmez (E8.6). Genel kapılar
(`SCALPER_C_BLOCKED_CELLS`, `SCALPER_ENTRY_BLOCK_HOURS_UTC`, `SCALPER_MIN/MAX_ATR_PCT`; D33) ile gerçek
harness koşuları + hiç kullanılmamış iki taze pencere (2026-02-13→03-01, 2026-07-21→08-07) son sınav.
Nisan 2026 (`…_213004.json`, −470/PF 0.61) yargıç tarafından teyit için açıldığından artık taze sayılmaz.
Canlıda: kapı açılırsa D27 karşı-olgu defteri engellenen girişlerin "girilseydi" sonucunu ölçer —
canlı A/B bu yolla yapılır. Diverjans gevşetme (E12) ve "daha çok işlem" fikirleri kapalıdır.

### E13.1 — Gerçek harness teyidi: post-hoc kapasite ikamesini gösteriyor (2026-09-04 01:00)

Aynı sunucu env + genel kapılar (D33 kod, `logs/backtest_20260904_0056xx–0110xx.json`):

| Kural | Mart | Çöküş | Mayıs | Haziran | AYI | YATAY | BOĞA |
|---|---|---|---|---|---|---|---|
| Taban | −130.4 | −185.1 | −336.8 | +70.6 | +334.4 | +240.6 | +79.3 |
| `SCALPER_C_BLOCKED_CELLS=UP:LONG` (post-hoc +131/+70/+77/+1) | −77.1 (+53) | −121.7 (+63) | −235.2 (+102) | +91.7 (+21) | +340.5 | +238.3 | **−2.7 (−82)** |
| `SCALPER_ENTRY_BLOCK_HOURS_UTC=11-14` (post-hoc +105/+36/+137/+11) | −10.8 (+120) | −115.7 (+69) | −227.1 (+110) | +85.5 (+15) | +338.8 | +202.7 | +94.1 |

Okuma: (1) UP-LONG yasağı gerçek koşuda post-hoc'un ~yarısını veriyor (boşalan slot başka işlemle
doluyor) ve BOĞA'nın kârını siliyor → RED kesinleşti. (2) 11-14 saat yasağı gerçek koşuda da dört
dokunulmamış pencerede pozitif; ama bu dört pencere kuralın SEÇİM kümesidir ve taze Nisan'da
rastgelenin altında kaldı (doğrulayıcı hükmü) → kabul için ön-kayıtlı taze pencere şart. (3) Post-hoc
tarama artık yalnız ön eleme; her aday gerçek harness'ta koşulur.

### E13.2 — Gerçek harness: hafta sonu LONG ve ADA LONG yasağı (2026-09-04 03:30, D33b kapıları)

`scripts/run_windows.sh` ile 7 pencere (`logs/run_windows/run7_{hs_long,ada_long,combo}.txt`):

| Kural | Mart | Çöküş | Mayıs | Haziran | Δ dok. | AYI | YATAY | BOĞA | Δ seçim | min kalan | Karar |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `SCALPER_ENTRY_BLOCK_WEEKDAYS_UTC=5,6` + `…_DIRECTION=LONG` | −16.9 (+114) | −22.0 (+163) | −300.8 (+36) | +18.4 (−52) | **+260** | −13 | **−96** | −16 | −125 (%19.1) | %83 | GEÇER (sınırda) |
| `SCALPER_SYMBOL_DIRECTION_BLOCK=ADAUSDT:LONG` | −121.9 (+9) | −138.4 (+47) | −305.4 (+31) | +71.8 (+1) | +88 | +14 | −23 | −16 | −25 (%3.9) | %93 | GEÇER (küçük etki) |
| ikisi birlikte | −22.8 (+108) | −28.3 (+157) | −269.5 (+67) | +18.4 (−52) | +280 | −24 | −108 | −35 | −168 (%25.7) | %81 | GEÇMEZ (seçim >%20) |

Okuma: hafta sonu LONG yasağının YATAY'daki gerçek kaybı (−96) post-hoc'un (−9) 10 katı — boşalan slotlar
hafta içi daha kötü işlemlerle doldu (E8.6 ters yönde). Dokunulmamış etkinin %63'ü ÇÖKÜŞ (23/30 Ağu Pazar
düşüşleri); Haziran kötüleşiyor. ADA LONG etkisi küçük ve sembol-özgü.

**Son sınav (ÖN KAYIT, sonuç görülmeden yazıldı):** iki taze pencere 2026-02-13→03-01 ve 2026-07-21→08-07
(taban `logs/backtest_20260904_011327.json`, `…_011624.json`; sayılarına bakılmadı). GEÇER = iki pencere
toplam Δ > 0 VE hiçbir pencere brüt kârının %5'inden fazla kötüleşmez. Tek atış; sonuç ne olursa olsun
tekrar aday seçilmez.

### E13.3 — Son sınav sonucu ve hüküm (2026-09-04 04:10)

| Kural | Şubat 13→Mar 1 (taban −189.4 / PF 0.71) | Temmuz 21→Ağu 7 (taban −162.3 / PF 0.66) | Toplam Δ | Ön-kayıtlı ölçüt |
|---|---|---|---|---|
| Hafta sonu LONG yasak | −188.5 (+0.9, PF 0.66, DD 381→346) | −146.2 (+16.1, PF 0.67, DD 208→192) | **+17.0** | GEÇER |
| ADA LONG yasak | −174.2 (+15.2, PF 0.72) | −158.0 (+4.4, PF 0.65, DD 208→221) | +19.5 | GEÇER |

Loglar: `logs/backtest_20260904_0205xx–0207xx.json`. **Hüküm:** iki kural da ölçütü geçiyor ama etki
dokunulmamış pencerelerdeki büyüklüğün (+260) onda biri; taze aylar taban olarak zararlı ve işaret
değişmiyor. Bu bir "kenar" değil, küçük bir fren. Kanıt disiplini gereği (P2 → testnet ≥5 gün)
**yalnız hafta sonu LONG yasağı** (a priori mekanizma: hafta sonu likidite/8 sa REAPER) testnet'e
ÖLÇÜM DENEYİ olarak alınır; D27 karşı-olgu defteri engellenen hafta sonu LONG'ların "girilseydi"
sonucunu ölçer. ADA LONG (sembol-özgü, ikisi birlikte seçim kaybı %26) alınmaz. Asıl sonuç
değişmedi: C'nin 2026 Mar–Ağu penceresinde kalıcı kenarı yok; giriş filtreleri kaybı küçültür, kâra
çevirmez. "Daha çok işlem" ancak yeni bilgi taşıyan sinyalle.
