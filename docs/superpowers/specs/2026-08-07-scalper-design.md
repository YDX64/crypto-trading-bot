# Swing/Trend Scalper — Tasarım (2026-08-07)

## Amaç

Kendi sinyalini üreten, 20x kaldıraçlı, ROI tabanlı kademeli TP + trailing SL
kullanan bir scalping motoru. Üç strateji varyantı **aynı anda** çalışır ve her
işlem strateji etiketiyle kaydedilir; backtest + canlı takip ile kâr/zarar
karşılaştırması yapılır.

Kullanıcı kararları:
- **TP tanımı**: marj getirisi (ROI). %20 ROI = 20x'te ~%1 fiyat hareketi.
- **Ritim**: dakikalar–saatler, günde 3-10 işlem. 4h rejim + 5m/15m giriş.
- **Entegrasyon**: bağımsız modül; mevcut PositionManager/istemci yeniden
  kullanılır, sembol çakışması borsa pozisyonu kontrolüyle önlenir.
- **Giriş felsefesi**: uç yakalama — "dipten long, tepeden short".
- **Evren**: 24s hacme göre ilk N (varsayılan 12) USDT paritesi, saatlik yenilenir.
- **Üç strateji birden**: A, B, C paralel; performans etiketli izlenir.

## Stratejiler

Hepsi aynı arayüzü uygular: `evaluate(ctx) -> ScalpSignal | None`.

- **A — Trend kırılması**: 4h rejim yönünde, 5m'de momentum kırılması
  (Donchian(20) kırılımı + hacim onayı). Referans/karşılaştırma stratejisi.
- **B — Trend içi uç avcısı (ana strateji)**: 4h rejim YUKARI ise 5m/15m geri
  çekilme dibinde LONG: RSI(14) < 35 **ve** fiyat Bollinger(20,2) alt bandının
  altında/bitişiğinde **ve** swing-low yapısı oluşmuş **ve** dönüş mumu teyidi
  (kapanış önceki mumun gövdesinin üstünde). Rejim AŞAĞI ise aynası SHORT.
  RANGE'de işlem yok (C devralır).
- **C — Saf uç avcısı (trend filtresiz)**: her rejimde bant ucu + RSI ucu +
  momentum diverjansı üst üste binince ters yönde girer. Risk yarıya düşürülür
  (counter-trend cezası). B'nin trend filtresinin değer katıp katmadığını
  ölçmek için var.

## Çıkış sistemi (tüm stratejiler ortak)

- **SL (yapısal)**: LONG'da tetikleyen swing-low'un %0,1 altı; SHORT'ta
  swing-high'ın %0,1 üstü. Sınırlar: fiyat mesafesi [%0,15 – %3]; %3'ü aşarsa
  sinyal reddedilir (20x'te likidasyon tamponu).
- **TP merdiveni** (reduceOnly TAKE_PROFIT_MARKET, Algo API):
  TP1 = +%20 ROI'de miktarın %40'ı; TP2 = +%50 ROI'de %30.
- **Runner + trailing**: kalan %30'un sabit TP'si yok. TP1 dolunca SL
  break-even'e; sonrasında chandelier trailing: LONG'da
  `max(BE, izlenen_en_yüksek − 2.5×ATR(14, 5m))`, yalnız lehte kayar.
  Değişimler boşluksuz desenle (önce yeni reduceOnly SL, sonra eski iptal).
- ROI→fiyat çevrimi: `fiyat_delta% = ROI% / kaldıraç`.

## Risk

- İşlem başına risk: `scalper_risk_percentage` (varsayılan mevcut %2; C yarısı).
- Kaldıraç: `scalper_leverage` (varsayılan 20), ISOLATED.
- Eşzamanlı scalper pozisyonu: `scalper_max_positions` (varsayılan 3).
- Sembol kilidi: girişten önce borsadan `positionAmt != 0` kontrolü (kaynak
  borsa; Telegram botuyla çakışmayı da kapsar).
- Günlük zarar kesici: `scalper_daily_loss_limit_pct` (varsayılan %15;
  0 = kapalı — kullanıcı limitsiz çalıştırmayı seçebilir). Aşılırsa gün sonuna
  kadar yeni giriş yok, açık pozisyon yönetimi sürer.

## Mimari

```
src/strategies/scalper/
  types.py       — Candle, Regime, ScalpSignal, StrategyContext (sözleşme)
  data.py        — kline çekme + TTL önbelleği (public /fapi/v1/klines)
  indicators.py  — EMA, ATR, Donchian, swing noktaları, diverjans, chandelier
                   (RSI/Bollinger waiting_mode'dan import)
  regime.py      — 4h rejim: EMA50/200 + swing yapısı → UP/DOWN/RANGE
  setups.py      — StrategyA/B/C (saf fonksiyonlar, IO'suz → test edilebilir)
  scanner.py     — hacim bazlı evren (24hr ticker), saatlik yenileme
  executor.py    — giriş: boyutlama → market → gerçek dolum → SL-yoksa-kapat
                   → TP merdiveni (PositionManager'ın güvenlik akışı yeniden
                   kullanılır; gerekli metodlar publicleştirilir)
  exits.py       — TP dolum takibi, BE geçişi, chandelier trailing döngüsü
  tracker.py     — ScalpTradeModel yazımı + istatistik özetleri
  engine.py      — ana async döngü (30s tarama), main.py lifespan'e bağlanır
  backtest.py    — tarihsel kline üzerinde A/B/C simülasyonu + rapor CLI
src/models/scalp_trade.py — strateji etiketli işlem kaydı (MAE/MFE dahil)
```

- Kline verisi **public** endpoint'ten (imza gerekmez); backtest tarihsel
  veriyi mainnet public API'den çeker (emir değil, salt veri).
- Ağırlık bütçesi: 12 sembol × 2 aralık / 30s ≈ dakikada ~50 istek — limitin
  çok altında.

## Veri akışı (canlı)

engine → scanner (evren) → data (klines) → regime → setups (A,B,C sırayla;
ilk geçerli sinyal kazanır, strateji etiketi taşır) → risk kapısı (limitler,
sembol kilidi, günlük kesici) → executor (pozisyon + koruma) → exits
(TP/BE/trailing) → tracker (kapanışta kayıt).

## Hata yönetimi

Bugünkü onarımların ilkeleri geçerli: SL konulamazsa pozisyon acil kapatılır;
"bilinmiyor" ≠ "kapandı"; API hataları kod bazında ayrıştırılır; restart'ta
borsadaki scalper pozisyonları (notes etiketiyle) exits döngüsüne geri alınır.

## Test

- Birim: indikatörler, rejim, üç setup (sentetik mum serileriyle), ROI çevrimi.
- Entegrasyon (testnet, gerçek emir): executor'ın tam koruma döngüsü.
- Backtest: 30 gün 5m verisi, komisyon %0,05/taraf + 1 tick kayma modeliyle;
  strateji başına: işlem sayısı, kazanma oranı, ort. ROI, profit factor,
  maks. düşüş, MAE/MFE.

## Kapsam dışı (YAGNI)

WebSocket akışı, funding-rate optimizasyonu, hedge mode, ML skorlama.
