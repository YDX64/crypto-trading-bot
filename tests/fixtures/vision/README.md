# data.binance.vision test fixture'ları

Gerçek arşiv satırlarından **6 mumluk kırpılmış** kopyalardır (BTCUSDT başlıklı, ETHUSDT başlıksız CSV).
Zip'ler yeniden paketlendiği için `.zip.CHECKSUM` dosyaları Binance'in değil, **yerelde üretilmiş**
sha256'lardır. Satırların her biri canlı arşivdeki CSV'de birebir bulunur. Ağ gerektiren gerçek
parite testi: `VISION_NETWORK_TESTS=1 python -m pytest tests/test_fill_klines_cache_from_vision.py`.
