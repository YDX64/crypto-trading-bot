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
4. "Savaş çıktı, her şeyi kapat" tipi olaylar için yön sinyali DEĞİL, risk olayı gerekir —
   `POST /risk-event` (bkz. 3, AKTİF).
5. Bot kendi loglarını tutar; motor loglarında `Sağlama oyu: … ← <src>` satırıyla izlenir.

## 3. Risk-olayı kanalı (`POST /risk-event`) — 2026-08-21'den beri AKTİF (D10)
Amaç: haber/olay botu "giriş durdur / devam et / her şeyi düzleştir" diyebilsin. Bu kanal
**yön sinyali göndermez** ve sağlamadan (tv_confluence) hiç geçmez — o yüzden TV
webhook'undan AYRI secret ister (`RISK_EVENT_SECRET`, boş = 503 ile kapalı, aynen TV
webhook deseni).

```
POST http://<sunucu>/risk-event?secret=<RISK_EVENT_SECRET>
Content-Type: application/json
{"action": "halt", "reason": "savaş çıktı, X borsası durdu", "source": "news_macro", "ttl_minutes": 60}
```
- `action`: `halt` | `resume` | `flatten` | `status`.
- `reason` (≤200 karakter): `halt`/`flatten`'da ZORUNLU; `resume`/`status`'ta opsiyonel.
- `source` (opsiyonel, ≤32 karakter): botun etiketi (log/teşhis için).
- `ttl_minutes` (opsiyonel, varsayılan 120, azami 1440): halt bu kadar dakika sonra
  KENDİLİĞİNDEN sona erer (ayrıca `resume` ile erken kaldırılabilir).
- secret gövdede (`{"secret": "..."}`) VEYA `?secret=` query'sinde taşınır, sabit-zamanlı
  karşılaştırılır (aynı desen: `resolve_tv_signal`/erişim logu redaksiyonu burada da geçerli).

**Nasıl çalışır (bkz. `src/strategies/scalper/engine.py` risk-olayı bölümü):**
- `halt`: `state/risk_event_halt.json`'a `{reason, source, until_ts, created_at}` yazar.
  Bu dosya **`state/scalper_entry_halt.json`'dan (koruma-hatası latch'i) AYRIDIR** ve
  `SCALPER_ENTRY_HALT_ENABLED` bayrağından **TAMAMEN BAĞIMSIZ** her zaman uygulanır — o
  bayrak yalnız `UnprotectedPositionError` otomatik latch'ini gater (canlı sunucu bunu
  `false` tutuyor), risk-olayı halt'ını DEĞİL. Motor'un TEK giriş kapısı olan
  `_entries_ready()` (böylece scanner'ın C stratejisi VE TV dış sinyali `external_signal`
  aynı anda kapanır) bu dosyayı ~1sn TTL önbellekle okur; bozuk/parse edilemeyen dosya
  **fail-closed HALT AKTİF** sayılır (`_load_entry_halt` ile aynı ilke). Açık pozisyonların
  SL/TP/trailing yönetimi (exits.py) bu kanaldan HİÇ etkilenmez.
- `resume`: yalnız `state/risk_event_halt.json`'ı siler — `scalper_entry_halt.json`'a
  dokunmaz (o ayrı, yalnız kanıtlanmış manuel müdahaleyle kalkar).
- `flatten`: `halt`'ı ÖNCE kurar (kapatma turu onlarca saniye sürebilir; halt turdan sonraya
  bırakılırsa tarama döngüsü bu pencerede yeni pozisyon açabilir ve o pozisyon tur başındaki
  tek-atımlık anlık görüntüye girmediği için asla kapanmaz), sonra TÜM izlenen scalper
  pozisyonlarını reduce-only MARKET ile kapatır (reaper'ın kullandığı AYNI emir çağrısı —
  yeni bir emir yolu yazılmadı; miktar CANLI `positionAmt`'tan alınır, girişteki bayat
  miktardan DEĞİL), her sembol için kapanışı borsa üzerinde taze (`force_fresh=True`) okuma
  ile doğrular (doğrulanamayan sembol `errors`'a düşer, izlemede KALIR — SL/TP asla
  doğrulanmadan iptal edilmez), kapanan pozisyonları `exit_reason="RISK_EVENT"` ile kaydeder.
  Kapatma turu bitince `tracked_symbols()` İKİNCİ kez taranır — halt kurulmasıyla eşzamanlı
  dolan bir pozisyon varsa o da düzleştirilir. İdempotenttir (izlenen pozisyon kalmayınca
  `flattened=[]` döner).
- `status`: halt durumu (`active`, `reason`, `until_ts`) + açık pozisyon sayısını döndürür.
- Yanıt JSON'u: `{ok, action, halted_until, reason, flattened: [symbol...], errors: [...],
  persisted}` (`persisted` yalnız `halt`/`flatten`'da; `status`'ta yok).
  - `ok` **gerçeği yansıtır**, sabit `true` DEĞİLDİR: `halt` → `active` (halt dosyaya
    yazılamasa bile RAM latch'i sayesinde etkilidir, `ok:true` + `persisted:false` — restart'ta
    kaybolur); `flatten` → `not errors` (bir sembol bile doğrulanamazsa `ok:false`, HTTP yine
    200 — `errors`'u incele); `resume` → halt kaldırıldıktan sonra `not active` (dosya
    silinemezse halt aktif KALIR, `ok:false`); `status` → her zaman `true` (salt-okunur sorgu).
  - `persisted:false` ile `ok:true` birlikte görülebilir (halt RAM'de etkili, diske yazılamadı)
    — otomasyon bunu görürse operatöre eskale etmeli (restart öncesi tekrar dener/insan
    müdahalesi ister).
- 403 = secret yanlış, 422 = geçersiz `action`/alan uzunluğu/gövde >4KB/`ttl_minutes` sonlu
  tamsayı değil (ör. `Infinity`), 503 = secret boş (kanal kapalı) veya scalper motoru hazır
  değil.

**Backtest paritesi:** risk-olayları yalnız CANLI motoru etkiler; `backtest.py`'ye
bilinçli olarak DOKUNULMADI — geçmiş bir haber olayının backtest'te simüle edilmesi bu
kanalın kapsamı dışındadır (bkz. D10, `docs/DECISIONS.md`).

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
