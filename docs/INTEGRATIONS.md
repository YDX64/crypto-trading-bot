# Yeni sinyal kaynağı ekleme sözleşmesi (haber botları, dış modeller, başka göstergeler)

Bu sistemde **motor tektir**: giriş kapıları (rejim, kapasite, cooldown, equilibrium), risk
boyutlama, stop/TP/BE/trailing/reaper ve kapanış defteri yalnız `src/strategies/scalper/`
içindedir. Dışarıdan eklenen her şey — TradingView alarmı, haber botu, LLM yorumcusu —
motora **yalnız bir yön önerisi** verir. Bu sayede yeni bir bot eklemek motoru değiştirmez,
bozmaz; bozarsa tek bir yerde görünür.

## 1. Kaynak = webhook + `src` etiketi (değişiklik gerektirmez)
```
POST http://<sunucu>/tv-signal?secret=<TV_WEBHOOK_SECRET>&src=<kaynak>
Content-Type: application/json
{"symbol": "BTCUSDT", "side": "buy"}          # side: buy|long|bull  /  sell|short|bear
```
- `src`: `TV_SOURCE_ALLOWLIST` içinde olmalı (varsayılan `luxosc,luxso,algopro,botv3,tv`).
  Yeni kaynak = bu listeye bir isim eklemek (`.env`), başka hiçbir kod değişmez. Listede
  olmayan `src` uyarıyla `tv`'ye eşlenir (sinyal düşmez, ama ayrı kaynak sayılmaz).
- Sağlama (`src/services/tv_confluence.py`): aynı sembol + aynı yön için **2 FARKLI kaynak**,
  420 sn penceresi; ters yön oyu gelince önceki oylar sıfırlanır. Tek başına hiçbir kaynak
  işlem açamaz — haber botu da. Bu, yeni kaynağın sistemi tek başına bozamaması demektir.
- Kabul edilen sinyal `engine.external_signal` → aynı kapılar, aynı risk, aynı çıkışlar.
- Yanıt JSON'u: `accepted`, `confluence` (oy durumu), `source`. 403 = secret yanlış,
  422 = sembol/yön çözülemedi, 503 = motor hazır değil.

## 2. Haber/olay botları için kurallar
1. Bot **asla** Binance'e doğrudan emir vermez; yalnız webhook'a yön gönderir.
2. Her bot kendi `src` adıyla gelir (`news_macro`, `news_llm`…). Aynı botun iki farklı
   modeli iki ayrı kaynak SAYILMAZ (aynı veriden türeyen oylar sağlamayı yanıltır) —
   tek `src` kullan.
3. Yön önerisi yalnız allowlist sembolleri için anlamlıdır (`SCALPER_TV_SYMBOL_ALLOWLIST`).
4. "Savaş çıktı, her şeyi kapat" tipi olaylar için yön sinyali DEĞİL, risk olayı gerekir
   (bkz. 3). Şimdilik bu kanal yok; eklenene kadar haber botları yalnız yön önerir.
5. Bot kendi loglarını tutar; motor loglarında `Sağlama oyu: … ← <src>` satırıyla izlenir.

## 3. Planlanan: risk-olayı kanalı (`POST /risk-event`) — HENÜZ YOK
Amaç: haber botu "giriş durdur / devam et / her şeyi düzleştir" diyebilsin. Tasarım:
`{action: "halt"|"resume"|"flatten", reason, ttl_minutes}` → mevcut entry-halt mekanizması
(`state/scalper_entry_halt.json`, fail-closed) ve reduce-only kapanış yolu üzerinden.
Eklenmeden önce: testler, RUNBOOK güncellemesi, `docs/DECISIONS.md` kaydı. Bu kanal
**yön sinyali göndermez** ve sağlamadan geçmez — o yüzden ayrı secret/allowlist ister.

## 4. Yeni kaynağı canlıya alma sırası (terfi hattı)
1. Gölge: kaynak `src`'yi allowlist'e ekleme; 3-5 gün yalnız logla ("tv"ye eşlenir, sağlamaya
   ayrı oy vermez) → kaç sinyal, hangi yön, mevcut kaynaklarla ne kadar örtüşüyor?
2. Backtest mümkünse: sinyal zaman damgalarını harness'a dış-sinyal olarak ver (yol: TODO).
3. Testnet: allowlist'e ekle → ≥5 gün → canlı defterde kaynak bazlı PF (`signal_reason`/`src`).
4. Mainnet: yalnız etiketli sürümle (bkz. CLAUDE.md terfi kuralı).

## 5. Değiştirilmeyecekler (bir bot eklerken dokunma)
`engine.py` kapıları, `exits.py`, `executor.py`, `config.py` varsayılanları, `tv_confluence.py`
oy kuralı. Bunlardan birini değiştirmek "yeni bot" değil "motor değişikliği"dir: backtest +
DECISIONS + parite testi ister.
