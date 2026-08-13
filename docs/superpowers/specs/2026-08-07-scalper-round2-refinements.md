# Scalper 2. Tur Keskinleştirme Planı (araştırma raporundan)

Kaynak: LuxAlgo resmî dokümantasyonu + AlgoPro v1.6 dokümanı üzerinde yapılan
araştırma (2026-08-07). Telifli kod KOPYALANMADI — yalnız kamuya açık kavram
tanımları. Uygulama, backtest baz çizgisi alındıktan SONRA yapılır ki her
iyileştirmenin katkısı ölçülebilsin.

## Öncelik 1 — Premium/Discount Equilibrium filtresi (çok düşük maliyet)

Son anlamlı swing-low↔swing-high aralığının ortası: `eq = (sh + sl) / 2`.
Fiyat > eq → premium (satış bölgesi), < eq → discount (alış bölgesi).
Uygulama: TÜM stratejilere ortak AND filtresi — LONG yalnız discount'ta,
SHORT yalnız premium'da. `indicators.py`'a `equilibrium(candles, left, right)`
+ setups'ta ortak kapı. Beklenti: false-positive azalması.
Kaynak: docs.luxalgo.com/docs/luxalgo-toolkits/price-action-concepts/pdzones

## Öncelik 2 — Yapısal pivot + ATR tamponlu stop (AlgoPro deseni, düşük maliyet)

Mevcut: yapısal SL = swing ± %0,1. İyileştirme: `stop = swing_low − ATR×buffer`
(LONG). Sabit yüzde yerine volatiliteye adaptif tampon → dar piyasada gereksiz
geniş, oynak piyasada gereksiz dar stop sorunu çözülür. Ayrıca runner çıkışına
alternatif mod: chandelier yerine "yapısal pivot + ATR tamponu takibi"
(config bayrağıyla A/B testi yapılabilir).
Kaynak: docs.algopro.us/tradingview-indicators/v1.6

## Öncelik 3 — EQH/EQL kümelenmiş likidite (orta maliyet)

Sweep hedeflerini rastgele swing uçlarından, % eşitlik eşiğiyle kümelenmiş
pivot gruplarına (equal highs/lows = gerçek stop kümeleri) daralt. İki aşamalı
sweep teyidi: (1) run — fitil seviyeyi geçer, (2) failure — kapanış aralığın
içine döner. StrategyD'nin `liquidity_sweep` girdisini bu kümelere bağla.

## Sonraki adaylar (2. turdan sonra değerlendir)

- **CHoCH derecelendirmesi**: leading vs supported (CHoCH+ = kırılım öncesi
  başarısız HH/LL) — CHoCH+ sinyaline yüksek skor.
- **BOS durum makinesi**: BOS yalnız bir CHoCH'tan sonra geçerli sayılır
  (her kırılımı bağımsız BOS saymak yanlış pozitif üretir).
- **Order block mitigasyon modları** (close/wick/average) + mitigate olan
  bloğun breaker block'a dönüşmesi (ters yön S/R).
- **İki katmanlı yapı**: internal (5-49 bar) vs swing (50-100 bar) ayrımı.
- **Overflow benzeri tükenme uyarısı**: hacim/trend-süresi normalizasyonu →
  aşırı geç katılım tespitinde trailing'i sıkılaştır (ATR çarpanı 2.5→1.5).
- **Ters diverjans → trailing sıkılaştırma**: pozisyon aleyhine RSI/MFI
  diverjansı görülünce chandelier çarpanını düşür.

## Doğrulanamayanlar (uygulamaya SOKMA — formül yok, tahmin üretme)

HyperWave normalizasyon formülü; Smart Money Flow "overflow" eşikleri;
Reversal Zones S1-S3/R1-R3 hesabı; AlgoPro trend cloud kesişim kuralları.
Bunlar ancak kendi tanımlarımızla "esinlenmiş" olarak yazılabilir, birebir
"LuxAlgo'daki gibi" iddiasıyla değil.

## Ölçüm protokolü

Her iyileştirme AYRI config bayrağıyla girer; 30 günlük backtest üç kez koşulur:
(a) baz, (b) yalnız bu iyileştirme, (c) hepsi. Kazanma oranı + profit factor +
maks. drawdown karşılaştırılır. İyileştirme (b)'de baz çizgiyi geçemiyorsa
varsayılanı kapalı kalır.
