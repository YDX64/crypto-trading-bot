# TRADINGBOT — Mimari Doküman

Bu dosya koddan okunarak yazılmıştır (`grep`/`Read`, tahmin yok). Belirsiz kalan
her nokta "kodda doğrulanamadı" diye işaretlidir. Anchor formatı: `dosya:satır`.

## 1. Tek bakışta

- Python/FastAPI kripto vadeli işlem botu; borsa **Binance USDⓈ-M Futures**,
  ağ **TESTNET** (`binance_base_url` varsayılanı `testnet.binancefuture.com`,
  `src/core/config.py:45`; mainnet açık onay ister — §9).
- Canlıda aktif strateji: **scalper motorunun "C" varyantı** (yerel `.env`:
  `SCALPER_STRATEGIES=C`; kod varsayılanı `"A,B,C,D"`, `config.py:142`).
- Tek süreç, tek FastAPI app (`src/main.py:215`, `uvicorn src.main:app`).
  Süreç içinde eşzamanlı çalışan bileşenler (`lifespan`, `main.py:127-213`):
  1. **ScalperEngine** — otonom tarama/giriş/çıkış motoru (`scalper_enabled`
     ise), 3 arka plan task'ı: scan, safety, exchange-readiness
     (`src/strategies/scalper/engine.py:218-252`).
  2. **TradingOrchestrator** — eski Telegram-VIP-sinyal akışı; scalper'ın
     sahiplenmediği açık pozisyonları kurtarır/izler (`src/services/
     orchestrator.py:43`).
  3. **TelegramBotService** — VIP kanal mesajlarını ayrıştırıp orchestrator'a
     besler + `/status /positions` bilgi komutları (`src/services/
     telegram_bot.py:27`); supervisor ile bounded backoff'la yeniden başlatılır
     (`main.py:102-125`).
  4. **TradingView webhook köprüsü** — `/tv-signal` (`main.py:613`), harici
     yön sinyalini scalper'ın kendi giriş hattına sokar.
- Deprecated: `src/api_server.py` (kendi docstring'i: "artık aktif kullanılmıyor",
  `src/api_server.py:5`) — kullanılmamalı, `src/main.py` tektir.
- Sunucu dağıtımı (2026-08-21 sunucuda doğrulandı): `awa:/opt/tradingbot-v2`,
  **supervisord** programı `tradingbot_v2` → `.venv/bin/python -m uvicorn
  src.main:app --host 127.0.0.1 --port 9091`. Sunucu repo'su bu GitHub
  repo'sunun `main`'ini izler (`scripts/deploy.sh`). `.env` sunucuda ayrı,
  commit'lenmez. ⚠️ systemd'deki `live-bot.service` trading botu DEĞİLDİR
  (futbol botu) — bkz. `docs/RUNBOOK.md`.

## 2. Veri/karar akışı

```mermaid
flowchart TD
    A["Binance /fapi/v1/klines\n(5m/15m/tf_regime)"] --> B["KlineFetcher\ndata.py"]
    B --> C["StrategyContext\ntypes.py:59"]
    C --> D["StrategyC.evaluate\nsetups.py:459"]
    D -->|ScalpSignal| E["engine._evaluate_symbol\nengine.py:769"]
    E --> F{"Rejim kapısı\nengine.py:815-834"}
    F -->|blokla| X1["log + atla"]
    F -->|geç| G["apply_stop_policy\nsetups.py:87"]
    G --> H{"Kapasite/cooldown/\nallowlist/entry_lock\nengine.py:900-931"}
    H -->|reddet| X2["log + atla"]
    H -->|geç| I["ScalpExecutor.try_open\nexecutor.py:678"]
    I --> J["Maker LIMIT GTX\nveya Market\nexecutor.py:805-820"]
    J --> K["SL + TP1/TP2 algo emirleri\n_finalize_position\nexecutor.py:1263"]
    K --> L["ExitManager.track\nexits.py:122"]
    L --> M["TP ladder / BE / chandelier\n/ reaper\nexits.py, engine.py:602"]
    M --> N["_CloseLedger doğrulaması\nexits.py:525"]
    N --> O["ScalpTracker.record_close\ntracker.py:70"]
    O --> P["scalp_trades tablosu\nscalp_trade.py"]
    P --> Q["/scalper/stats,/scalper/trades\nmain.py:797,853"]

    TV["TradingView alert"] --> TVW["/tv-signal\nmain.py:613"]
    TVW --> RS["resolve_tv_signal\n(?src= etiketi)\nmain.py:544"]
    RS --> CONF["TvConfluence.vote\ntv_confluence.py:45\n(required>1 ise)"]
    CONF -->|eşik dolmadı| X3["accepted=False"]
    CONF -->|eşik doldu| EXT["engine.external_signal\nengine.py:968"]
    EXT --> E
```

Zaman dilimi rolleri (`engine.py:775-791`, config varsayılanı 5m/15m/4h):
`scalper_tf_entry` (giriş mumu, varsayılan 5m) → `scalper_tf_context`
(bağlam/equilibrium, 15m) → `scalper_tf_regime` (rejim; kod varsayılanı 4h,
canlı sunucu `.env`'i **15m** — 2026-08-21'de `.venv` içinden
`settings.scalper_tf_regime` okunarak doğrulandı; 4h varyantı backtest'te
boğayı yok ettiği için reddedildi, bkz. `docs/DECISIONS.md`).

TV yolunda sağlama kuralı (`src/services/tv_confluence.py:29-89`): oy =
(sembol, yön, kaynak); `tv_confluence_window_seconds` (varsayılan 180s)
içinde `tv_confluence_required` (varsayılan 1) farklı kaynak aynı yönde oy
vermeli; ters yön oyu geldiğinde HER İKİ tarafın oyları silinir (`vote()`,
satır 56-63). `?src=` yoksa kaynak, AlgoPro'nun varsayılan mesaj biçiminden
("`| TF:`"/"`| Price:`") tahmin edilir, yoksa `"tv"` (`main.py:645-647`).

## 3. Modül haritası

| Dosya | Sorumluluk | Anahtar semboller |
|---|---|---|
| `src/main.py` | FastAPI app, lifespan, tüm HTTP endpoint'leri | `lifespan:128`, `resolve_tv_signal:544`, `tradingview_webhook:613`, `risk_event` (POST `/risk-event`, D10, hemen `tradingview_webhook` sonrası), `health_check:264`, `api_status:332`, `scalper_stats:798` |
| `src/core/config.py` | Tüm ayarlar (pydantic `Settings`), testnet/mainnet fail-safe | `Settings:28`, `is_testnet:313`, `_validate_binance_environment:392` (mainnet+halt_enabled+prod uyarı zinciri) |
| `src/core/database.py` | Async SQLAlchemy engine/session, SQLite WAL, idempotent migration | `init_db:88`, `_ensure_schema_migrations:76` (entry_order_id kolonu sonradan eklendi) |
| `src/core/rate_limiter.py` | Küresel Binance/OpenAI hız sınırlayıcı | `RateLimiter.wait_for_binance:49` (asyncio.Lock ile atomik slot rezervi) |
| `src/strategies/scalper/engine.py` | Orkestrasyon: scan/safety/exchange döngüleri, kapılar, kill switch, risk-olayı kanalı | `ScalperEngine:100`, `_scan_tick:699`, `_evaluate_symbol:769`, rejim kapısı `815-834`, `_reap_aged_positions:602`, `_update_kill_switch:1343`, `external_signal:968`, `health_snapshot:1241`, `_risk_event_halt_snapshot`/`risk_event_halt`/`risk_event_resume`/`risk_event_flatten`/`risk_event_status` (risk-olayı bölümü, `_persist_entry_halt` sonrası) |
| `src/strategies/scalper/setups.py` | Saf strateji mantığı (A/B/C/D/E), stop politikası, ortak kapılar | `StrategyC:431` (`evaluate:459`), `apply_stop_policy:87`, `passes_equilibrium:194`, `get_enabled:931` |
| `src/strategies/scalper/regime.py` | 4h/tf_regime rejim tespiti (EMA50/200) | `detect_regime:19` |
| `src/strategies/scalper/executor.py` | Giriş boyutlama, risk kapıları, maker/taker giriş, SL/TP algo emirleri, cooldown | `try_open:678`, `_finalize_position:1263`, `_open_maker_entry_locked:1430`, `_set_cooldown:548`, `start_loss_cooldown:586` |
| `src/strategies/scalper/exits.py` | TP1/TP2 doğrulama, BE, chandelier trailing, kapanış doğrulama | `ExitManager.step:133`, `_check_tp1:185`, `_check_tp2:232`, `_update_trailing:359`, `_handle_closed:428`, `_verified_close_ledger:525`, `recover:1095` |
| `src/strategies/scalper/tracker.py` | `scalp_trades` DB yazımı, istatistik/PnL kaynak sınıflandırması | `record_open:32`, `record_close:70`, `_pnl_source:199`, `stats:288` |
| `src/strategies/scalper/types.py` | Ortak veri sözleşmesi (saf, IO'suz) | `Regime:21`, `StrategyContext:59`, `ScalpSignal:77`, `ExitPlan:101`, `resolve_trail_mult:152`, `fee_aware_breakeven_price:176` |
| `src/strategies/scalper/backtest.py` | Tarihsel simülasyon + CLI | `_build_arg_parser:1348`, `main_async:1372`, `print_report:902` |
| `src/strategies/scalper/indicators.py` | Saf gösterge fonksiyonları (RSI/BB/ATR/MFI/chandelier/OB/EQH-EQL...) | `chandelier_stop:280`, `equilibrium:549`, `rsi_series:58` |
| `src/services/tv_confluence.py` | Çoklu-kaynak TV sağlama motoru | `TvConfluence.vote:45` |
| `src/trading/binance_client_improved.py` | İmzalı/imzasız REST istemcisi, okuma önbellekleri, ağırlık telemetrisi | `_get_account:711`, `get_position_risk:1360`, `get_all_positions:1424`, `_request_with_retry:329` (weight header, satır 391-415), `_invalidate_read_caches:161` |
| `src/trading/position_manager.py` | Güvenli pozisyon açma/kapama, boşluksuz SL değişimi, acil kapatma | `UnprotectedPositionError:32`, `open_position:63`, `_emergency_close:416`, `replace_stop_loss:803` |
| `src/models/scalp_trade.py` | `scalp_trades` ORM modeli | `ScalpTradeModel:16` |

### `config.py` — öne çıkan `SCALPER_*` ayarları (varsayılan → anlam)

| Ayar | Varsayılan | Anlam |
|---|---|---|
| `scalper_strategies` | `"A,B,C,D"` | Aktif strateji CSV'si (`get_enabled`, `setups.py:931`); yerelde `.env` ile `C`'ye daraltılmış |
| `scalper_symbol_allowlist` | `""` (boş=scanner top_n) | Doluysa tarama SADECE bu sembollerle sınırlanır |
| `scalper_tv_symbol_allowlist` | `""` | TV dış sinyaline sembol filtresi (OSC kanıtı olan sembollerle sınırlamak için) |
| `scalper_regime_filter` | `True` | Rejim kapısını (§4) aç/kapat |
| `scalper_tv_regime_filter` | `True` | Rejim kapısının TV sinyaline de uygulanıp uygulanmayacağı |
| `scalper_market_gate` | `False` | Lider piyasa kapısı (§4.1) — ters-gün kapısı, varsayılan KAPALI |
| `scalper_market_gate_symbol` | `BTCUSDT` | Kapının baktığı lider sembol |
| `scalper_market_gate_day_pct` | `1.3` | Gün-içi alt-kapısı eşiği (%; 0 = kapalı) — E7+E8 ölçümü |
| `scalper_market_gate_run_pct` / `_run_days` | `0.0` / `3` | Uzama alt-kapısı — **varsayılan KAPALI**, iki bağımsız ölçüm çürüttü (D15) |
| `scalper_market_gate_retry_sec` | `60.0` | Lider verisi alınamazsa negatif önbellek (sn; 0 = kapalı) |
| `scalper_tf_entry/context/regime` | `5m/15m/4h` | Giriş/bağlam/rejim zaman dilimleri |
| `scalper_c_rsi_long_max/short_min` | `25.0/75.0` | C'nin RSI uç eşiği |
| `scalper_c_require_divergence` | `True` | C'de RSI diverjans şartı |
| `scalper_c_allowed_regimes` | `"UP,DOWN,RANGE"` | C'nin çalıştığı rejim kümesi (UNKNOWN her zaman kapalı) |
| `scalper_stop_mode` | `"structural"` | `structural` (yapısal+ATR taban) veya `fixed_roi` (marj-yüzdesi stop) |
| `scalper_fixed_stop_roi_pct` | `50.0` | `fixed_roi` modunda SL'nin vurduğu marj kaybı yüzdesi |
| `scalper_stop_atr_floor_mult` | `0.5` | Yapısal stop girişe çok yakınsa ATR×mult tabanına genişlet |
| `scalper_min_stop_pct/max_stop_pct` | `0.15/3.0` | İzin verilen stop mesafesi bandı (fiyat %) |
| `scalper_min_rr` | `1.2` | Beklenen harman ROI / SL riski alt sınırı (0=kapalı) |
| `scalper_tp1_roi/fraction` | `20.0/0.40` | TP1 ROI hedefi ve dolum oranı |
| `scalper_tp2_roi/fraction` | `50.0/0.30` | TP2 ROI hedefi ve dolum oranı (kalan %30 runner) |
| `scalper_chandelier_atr_mult/period` | `2.5/14` | Chandelier trailing çarpanı/ATR periyodu |
| `scalper_trail_relax_roi1/2_pct` | `0.0/150.0` | Kademeli gevşeyen iz eşikleri (`resolve_trail_mult`, `types.py:152`) |
| `scalper_max_hold_hours` | `0.0` | Reaper yaş limiti (0=kapalı) |
| `scalper_loss_cooldown_minutes` | `60` | SL/negatif kapanış sonrası sembol giriş kilidi |
| `scalper_stop_atr_floor_mult` | `0.5` | (yukarıda) |
| `scalper_max_margin_pct` | `10.0` | İşlem başına marj tavanı (kasanın %'si) |
| `scalper_daily_loss_limit_pct` | `15.0` | Günlük zarar kesici (0=kapalı) |
| `scalper_entry_halt_enabled` | `True` | Fail-closed giriş kilidi; mainnet'te kapatılamaz (`config.py:420-429`) |
| `scalper_entry_mode` | `"taker"` | `"maker"` = LIMIT GTX iki fazlı giriş |
| `scalper_max_positions` | `3` | Eşzamanlı scalp pozisyon tavanı |
| `scalper_dynamic_leverage` | `False` | Coin-bazlı ATR'ye göre kaldıraç çözümü (`fixed_roi` modunda) |

## 4. Rejim ve kapılar

`detect_regime` (`src/strategies/scalper/regime.py:19-44`), saf fonksiyon,
girdi `candles_4h` (veya konfigüre edilen `scalper_tf_regime`):

- `< 200` mum → `UNKNOWN` (EMA200 seed'i için yetersiz veri; **hiçbir**
  strateji işlem açmaz — `StrategyC.evaluate`, `setups.py:462-463`).
- `EMA50 > EMA200` ve son kapanış `> EMA50` → `UP`.
- `EMA50 < EMA200` ve son kapanış `< EMA50` → `DOWN`.
- Aksi halde → `RANGE`.

Rejim kapısı (`engine.py:815-834`, `_evaluate_symbol` içinde, sinyal
üretildikten SONRA): `DOWN` rejiminde `LONG`, `UP` rejiminde `SHORT`
sinyali engeller; `RANGE`/`UNKNOWN`'da (UNKNOWN zaten yukarıda elenir)
serbest. `gate_on = scalper_regime_filter AND (iç sinyal OR
scalper_tv_regime_filter)` — yani TV sinyalleri ayrı bir bayrakla muaf
tutulabilir ama varsayılan `True`'dur (`engine.py:822-825`).

`StrategyC.evaluate` (`setups.py:459-565`) kendi içinde ek bir rejim
daraltması yapar: `scalper_c_allowed_regimes` CSV'sinde olmayan rejimlerde
sinyal üretmez (`setups.py:462-463`); varsayılan `"UP,DOWN,RANGE"` = eski
davranışla birebir (yalnız UNKNOWN engelli).

### 4.1 Piyasa yapısı kapısı (BOS/CHoCH) — `structure.py`, varsayılan KAPALI

`src/strategies/scalper/structure.py` (2026-08-23, E9/D18 adayı) saf bir yapı
durum makinesidir: fraktal pivot (`SCALPER_STRUCTURE_PIVOT`, varsayılan 5, her
iki taraf) → son onaylanmış swing seviyesi → **kapanışla** (varsayılan;
`SCALPER_STRUCTURE_USE_CLOSE=false` ile fitil) kırılım. Kırılım mevcut yapı
yönüyle aynıysa **BOS** (devam), tersineyse **CHoCH** (karakter değişimi) ve
yapı yönü döner. Yön henüz NONE iken ilk kırılım BOS'tur. Her seviye YALNIZ
BİR KEZ olay üretir; yeni pivot onaylanınca seviye güncellenir.

- **Neden:** rejim kapısı (§4, EMA50/200) dönüşleri saatler geç görür; yapı
  kırılımı aynı soruyu mumun kapanışında yanıtlar (`docs/EXPERIMENTS.md` E9).
- **Veri:** `StrategyContext`'te ZATEN çekilmiş seriler
  (`SCALPER_STRUCTURE_TF` = rol adı `entry|context|regime` ya da doğrudan
  zaman dilimi metni). **Yeni REST çağrısı yoktur.**
- **Giriş kapısı** (`SCALPER_STRUCTURE_GATE`, `_BLOCK_COUNTER`): yapı BEAR
  iken LONG, BULL iken SHORT açılmaz. Motorda rejim kapısının HEMEN ARDINDA,
  `_evaluate_symbol` içinde — yani C ve TV sinyalleri AYNI kapıdan geçer.
  Harness (`backtest.simulate_symbol`) AYNI saf fonksiyon çiftini
  (`structure_state_for` → `structure_gate_blocks`) AYNI pencerelerle çağırır
  (P1 paritesi; `tests/test_structure.py`).
- **Çıkış tetikleyicisi** (`SCALPER_STRUCTURE_EXIT=off|be|close`, varsayılan
  `off`): açık pozisyonun TERSİNE ve GİRİŞTEN SONRA bir CHoCH gelirse stop
  BE'ye çekilir (`be`) ya da pozisyon reduce-only MARKET ile kapatılır
  (`close`, `exit_reason="STRUCT_CHOCH"`). Canlı tarafta
  `engine._apply_structure_exits` safety turunda çalışır: mum isteği
  (sembol, aralık, limit) tarama turununkiyle BİREBİR aynıdır (KlineFetcher
  TTL önbelleğine düşer), tur başına EN FAZLA BİR aksiyon uygulanır (2026-08-14
  watchdog dersi), kapanış `_close_position_market` ile borsada DOĞRULANIR
  (fail-closed) ve `be` aksiyonu stopu piyasanın yanlış tarafına koyacaksa
  (borsada -2021) uygulanmaz. Harness'ta aynı kural `manage_position` içinde.
- **Gözlem:** kapı kapalıyken de her tarama turunda hesaplanır ve
  `/scalper/status` → `structure` (sembol → `direction/last_event/age_bars`)
  alanında yayınlanır; hesap hatası taramayı DÜŞÜRMEZ (fail-open, tek sefer
  loglanır) — bir sinyal filtresi, güvenlik kilidi değildir.
### 4.1 Lider piyasa kapısı ("ters-gün kapısı", D15 — varsayılan KAPALI)

Rejim kapısının **hemen yanında**, aynı tek giriş noktasında
(`engine._market_gate_reason`, `_evaluate_symbol` içinde — yani C taraması
VE TV `external_signal` aynı anda). Rejim kapısından farkı: rejim kapısı
sembolün **kendi** EMA50/200 trendine bakar; bu kapı yalnız **lider**
sembole (`scalper_market_gate_symbol`, varsayılan BTCUSDT) bakar ve kararı
tüm evrene uygular. Saf kural `src/strategies/scalper/market_gate.py`
(`evaluate_market_gate`, IO yok) — canlı motor ve backtest harness'ı
(`backtest.simulate_symbol` + `LeaderSeries`) **aynı fonksiyon nesnesini**
çağırır (parite: `tests/test_market_gate.py::TestEngineHarnessParity`).

İki bağımsız alt-kapı (her biri kendi yüzdesi `0` yapılarak kapatılır):

- **gün-içi** (`scalper_market_gate_day_pct`): lider son kapanışı gün
  açılışının ≥%X **altındaysa** yeni LONG, ≥%X **üstündeyse** yeni SHORT
  açılmaz.
- **uzama** (`scalper_market_gate_run_pct` / `_run_days`): lider son N
  **tamamlanmış** günde ≥+%Y koştuysa LONG, ≤−%Y düştüyse SHORT açılmaz
  (koşu = `kapanış[-1]/kapanış[-1-N] − 1`, yani N+1 kapanış gerekir).
  **Kullanılmamalı:** iki bağımsız ölçüm (E7 harness + E8 canlı defter) bu alt-kapıyı
  desteklemiyor; `RUN_PCT>0` ile açılırsa motor başlangıçta WARNING basar (D15).

"Gün açılışı" iki tarafta da `market_gate.resolve_day_open` ile bulunur:
önce **gerçek açılış** — o günün 00:00 UTC `15m` mumunun `open`'ı, ki `1d`
mumunun `open`'ına birebir eşittir (ikisi de aralığın ilk işlem fiyatı;
ölçüldü: BTCUSDT mainnet+testnet, 76 gün sınırı, 0 uyuşmazlık). Bu yol
`_drop_unclosed`'a hiç dokunmaz (o 15m mumu çoktan kapanmıştır), yani
oluşmakta olan GÜNLÜK mumu görmeye gerek kalmaz. Günün ilk 15 dakikasında
(mum henüz kapanmamış — look-ahead yasak) **iki taraf da** son tamamlanmış
günlük kapanış vekiline düşer; hangisinin kullanıldığı `/scalper/status`
`market_gate.day_open_source` alanındadır.

REST ağırlığı: lider **başına** ~60 sn TTL önbellek (`_MARKET_GATE_CACHE_TTL`),
sembol başına değil — tarama turu başına en çok **3 istek**: `1d`
(limit `RUN_DAYS+5`, tavan 100), giriş TF (limit 3) ve `15m` (limit 100);
üçü de limit ≤ 100 olduğu için ağırlık 1. Kapı AÇIKKEN anlık görüntü her
tarama turunun başında tazelendiği için maliyet **≈3 ağırlık/dakika**
(bütçe 2400/dk); kapalıyken **tek istek bile gitmez** — yani alt sınır
0'dan 3'e çıkar, "değişmez" değil.
Lider verisi alınamazsa kapı **uygulanmaz** (fail-open) ve oran-sınırlı WARNING
loglanır — lider verisinin gelmemesi bir risk olayı değildir. Fail-open GÖRÜNÜR:
lider sembolü başlangıçta exchangeInfo'da doğrulanır (yoksa ERROR + "degraded"),
başarısızlık `SCALPER_MARKET_GATE_RETRY_SEC` boyunca negatif önbelleğe alınır
(boşa REST + paylaşılan kline kilidi), ve `/scalper/status` →
`market_gate.gate_effective` kapının GERÇEKTEN koruyup korumadığını söyler —
`enabled` bunu söylemez (D15). `gate_effective` BEŞ şartı birden ister:
`enabled` + lider doğrulandı + en az bir BAŞARILI anlık görüntü + görüntü
BAYAT değil (yaş ≤ 2 × tarama aralığı, UTC günü dönmemiş) + en az bir eşik > 0. Anlık görüntü
tarama turu başında bir kez tazelenir (tur içi tüm semboller aynı görüntüyü
kullanır) ve önbellek UTC gün damgasıyla anahtarlanır. `/scalper/status`
`market_gate` alt-sözlüğü (enabled/gate_effective/leader/leader_ok/
leader_source_host/thresholds/stale/snapshot_age_sec/day_drift_pct/
run_drift_pct/day_open_source/last_ok_at/last_error/last_failure_at/
consecutive_failures/failures_total/last_reason/last_block_at/rejects)
teşhis için dışa verilir; harness'ta engellenen sinyaller
`missed_counter["market_gate_day"/"market_gate_run"]` altında raporlanır.
Ölçüm ve P2 hükmü: `docs/EXPERIMENTS.md` "2026-08-23 — Lider piyasa kapısı (E7)".

## 5. Çıkış mimarisi

Tümü `src/strategies/scalper/exits.py`'de, `ExitManager.step()` her `symbol`
için `_step_one` çağırır (`exits.py:133-181`):

1. **TP1** (`_check_tp1:185`): canlı miktar `filled*(1-tp1_frac*0.9)`
   eşiğinin altına düşerse, gerçek algo child-order fill'i
   `_confirmed_algo_fill` (`exits.py:288-357`) ile borsa `userTrades`
   satırlarından kanıtlanır (miktar tahmini SAYILMAZ) → SL,
   `fee_aware_breakeven_price` (`types.py:176-230`, komisyon+buffer'ı
   cebirsel karşılayan seviye) hedefine çekilir, `trailing_active=True`.
2. **TP2** (`_check_tp2:232-275`): aynı doğrulama deseni; onaylanınca runner
   tabanı `runner_floor_price` (=TP1 fiyatı) seviyesine yükseltilir.
3. **Chandelier trailing** (`_update_trailing:359-417`, yalnız
   `trailing_active`): `chandelier_stop` (`indicators.py:280-301`) —
   LONG: `max(high[since_entry:]) - atr_mult*ATR(14)`; çarpan
   `resolve_trail_mult(cfg, sp.mfe_pct)` (`types.py:152-173`) — tepe ROI
   `scalper_trail_relax_roi1/2_pct` eşiklerini geçtikçe kademeli büyür
   (tek yönlü, geri çekilmede sıkılaşmaz). Stop yalnız lehte kayar (`floor`
   ile `max`/`min`, `exits.py:398-404`); değişim önce yeni SL sonra eski
   iptal deseniyle boşluksuz uygulanır (`pm.replace_stop_loss`).
4. **Reaper** (`_reap_aged_positions`, `engine.py:602-663`): `sp.
   trailing_active=True` olan pozisyonlar MUAF (BE korumalı koşucu — "bugün
   kesilen trend yarın devam edebilir"). Yalnız BE'ye hiç ulaşmamış, `age_h
   >= scalper_max_hold_hours` (0=kapalı) pozisyonları reduce-only MARKET ile
   kapatır; tur başına en fazla 1 kapanış (`engine.py:661`, watchdog
   restart'ı önlemek için).
5. **Stop modları** (`setups.apply_stop_policy:87-142`): `structural`
   (yapısal swing + ATR taban `apply_stop_atr_floor:55-79`) veya `fixed_roi`
   (mesafe `= fixed_stop_roi_pct/kaldıraç`, likidasyon tamponu için `%70`
   tavanla kırpılır — `_FIXED_STOP_ROI_CAP_PCT`, `setups.py:82,123-128`).
   `config.py:342-390` startup'ta `fixed_roi` + `min_rr`/`min_stop_pct`/
   `max_stop_pct` tutarsızlığını fail-fast reddeder.
6. **min_rr kapısı**: `executor.try_open` adım 3 (`executor.py:733-753`) —
   beklenen harman ROI / (stop_mesafesi×kaldıraç) `< scalper_min_rr` ise
   sinyal reddedilir (`0` = kapalı).
7. **Kayıp cooldown**: `_maybe_start_loss_cooldown` (`exits.py:86-107`) →
   `executor.start_loss_cooldown` (`executor.py:586-605`) — SL veya net
   negatif kapanışta sembolü `scalper_loss_cooldown_minutes` süre kilitler;
   mevcut daha uzun bir cooldown asla kısaltılmaz (`_set_cooldown:548-560`).

## 6. Kalıcı durum ve dosyalar

- **DB**: `settings.database_url` (varsayılan `sqlite:///./tradingbot.db`,
  `config.py:91`) — WAL modu, `busy_timeout=5000` (`database.py:38-42`).
  Ana tablo `scalp_trades` (`src/models/scalp_trade.py:18`); create_all sonrası
  eksik kolon idempotent tamamlanır (`database.py:76-85`, örn.
  `entry_order_id`).
- **state/**: `scalper_cooldown_state_path` (varsayılan
  `state/scalper_cooldowns.json`, atomik tmp+fsync+replace yazım,
  `executor.py:520-546`), `scalper_entry_halt_path` (varsayılan
  `state/scalper_entry_halt.json`, fail-closed kalıcı giriş kilidi —
  `engine.py:298-371`), `scalper_pending_journal_path` (maker LIMIT
  niyetlerinin restart-güvenli journal'ı, `executor.py:198-287`),
  `risk_event_halt_path` (varsayılan `state/risk_event_halt.json`, TTL'li
  fail-closed risk-olayı kilidi — bkz. altta ve `docs/INTEGRATIONS.md` §3).

**Risk-olayı kanalı** (`POST /risk-event`, D10, `docs/DECISIONS.md`): haber/olay
botlarının giriş kapılarını durdurup/devam ettirebildiği veya tüm izlenen pozisyonları
acilen düzleştirebildiği yol, motor mantığına DOKUNMADAN eklendi. `risk_event_halt_path`,
`scalper_entry_halt_path`'ten BİLİNÇLİ olarak AYRI bir dosyadır ve `scalper_entry_halt_enabled`
bayrağından (yalnız `UnprotectedPositionError` otomatik latch'ini gater; canlı sunucuda
`false`) TAMAMEN BAĞIMSIZ, her zaman uygulanır — `_entries_ready()` (`engine.py:483-501`,
scanner + `external_signal`'in TEK ortak kapısı) `_risk_event_halt_snapshot()`'ı (~1sn TTL
önbellekli, dosya bozuksa/parse edilemezse fail-closed HALT AKTİF) sorar. `flatten`
aksiyonu, reaper'ın (`_reap_aged_positions`) kullandığı reduce-only MARKET emrini
(`_submit_reduce_only_market_close`) yeniden kullanır — yeni bir emir yolu yoktur — ve her
sembolün kapanışını borsa üzerinde doğruladıktan SONRA `exits._handle_closed`'ı
`forced_exit_reason="RISK_EVENT"` ile çağırır; doğrulanamayan sembol izlemede kalır
(SL/TP asla doğrulanmadan iptal edilmez). Backtest harness'ına BİLİNÇLİ olarak
dokunulmadı — risk-olayları yalnız canlı motoru etkiler.
- **logs/**: `logs/` dizini repoda mevcut (115 girdi görüldü); loguru
  yapılandırması `src/core/logger.py`'dedir — içerik bu görevde satır bazlı
  incelenmedi (kapsam dışı, ana odak scalper akışı).
- **backups/**: bu repoda `.env.bak.20260807_104143` gibi zaman damgalı `.env`
  yedekleri kök dizinde bulundu; sunucuda ayrıca `/opt/tradingbot-v2/backups/`
  klasörü vardır (`env.bak-<tarih>-<etiket>`, `commit.prev-<tarih>`; deploy
  script'i ve her elle .env değişikliği buraya yazar — 2026-08-21 doğrulandı).
- **.env**: asla commit edilmez (`.gitignore`); `Settings` bunu okur
  (`config.py:31-35`, `env_file=".env"`). Sunucudaki `/opt/tradingbot-v2/.env`
  bu repodan bağımsızdır (bkz. §7 "sunucu env'i" kuralı).

## 7. Backtest harness

`src/strategies/scalper/backtest.py`, CLI (`_build_arg_parser:1348-1367`):

```
python -m src.strategies.scalper.backtest --days 30 \
    --symbols BTCUSDT,ETHUSDT,SOLUSDT --strategies A,B,C
python -m src.strategies.scalper.backtest --start 2026-01-23 --end 2026-02-13 \
    --symbols BTCUSDT --strategies C
```

`--symbols auto` → `UniverseScanner` mainnet ilk 8 (top_n); `--start/--end`
verilirse `--days`'i geçersiz kılar (`main_async:1372-1381`).

Simüle edilenler: maker giriş (`_find_maker_fill`, satır 367-, `scalper_
entry_mode=maker` iken LIMIT dolum + timeout iptali), `fixed_roi` stop modu ve
coin-bazlı dinamik kaldıraç (canlı `apply_stop_policy` AYNEN çağrılır —
`backtest.py:49`), rejim kapısı paritesi (canlı `_evaluate_symbol` ile aynı
mantık — commit `7640c0a "rejim kapısı paritesi"`). Sembol başına backtest'te
**tek eşzamanlı pozisyon**; semboller-ARASI `scalper_max_positions` kapasite
kapısı ise `run_backtest`'te tüm sembollerin adayları birleştikten SONRA,
kronolojik tek bir geçişle uygulanır (`_apply_capacity_gate`, parite ile
canlı `_evaluate_symbol` kapasite kuralı — post-hoc, sembol-içi değil).

Çıktı: konsol tablosu (strateji × işlem/kazanma%/PnL/profit factor/max
drawdown/…, `print_report:902-948`) + iki kırılım tablosu: **REJİM
KIRILIMI** (`_print_regime_breakdown:957-987`) ve **YÖN/ÇIKIŞ NEDENİ
KIRILIMI** (`_print_grouped_breakdown` çağrıları, `print_report:948-955`);
ayrıca JSON rapor (`write_json_report`, `run_metadata["regime_breakdown"]`
`backtest.py:1118-1153`).

Kural: backtest sunucu `.env`'iyle koşulmalı (yerel `.env` bayat olabilir).
Kullanıcı bağlamındaki komut deseni:
```
env $(ssh awa grep ^SCALPER_ /opt/tradingbot-v2/.env | xargs) \
    python3 -m src.strategies.scalper.backtest --days 30 --symbols BTCUSDT --strategies C
```
Bu SSH/ortam deseni `CLAUDE.md` ve `docs/EXPERIMENTS.md`'de standarttır;
pencere tarihleri ve karar kuralı `docs/DECISIONS.md` P2'de.

## 8. Dış entegrasyonlar

- **Binance Futures REST/WS**: `ImprovedBinanceClient`
  (`src/trading/binance_client_improved.py:88`). Testnet/mainnet anahtarı
  `binance_base_url` + `TESTNET_HOSTS` listesi (`config.py:16-21,313-323`);
  `is_testnet=False` iken `allow_mainnet=True` açıkça gerekir yoksa
  `Settings()` `ValueError` fırlatır (`config.py:407-418`).
  Koşullu emirler (STOP_MARKET/TAKE_PROFIT_MARKET) 2025-12-09'dan itibaren
  `/fapi/v1/algoOrder` üzerinden gider, eski `/fapi/v1/order` -4120 ile
  reddeder (`binance_client_improved.py:958-972`).
- **TradingView alertleri**: kullanıcı TV'de alert kurar; webhook `/tv-signal`
  (`main.py:613`), secret gövdede veya `?secret=` query'de (TV header
  gönderemez). Kaynak etiketi `?src=` — kullanıcı bağlamına göre
  `luxosc`/`luxso`/`algopro`/`botv3` gibi değerler kullanılıyor; bu isimler
  kodda sabit/whitelist DEĞİL, serbest string olarak `TvConfluence`'a
  kaynak kimliği olarak geçiyor (`main.py:643-648`, `tv_confluence.py:52`).
- **Telegram**: `python-telegram-bot` tabanlı `TelegramBotService`
  (`telegram_bot.py:27`) — VIP kanal sinyali ayrıştırma (`handle_channel_
  post:110`) + `/status /positions` bilgi komutları (satır 70-108); ayrıca
  `send_notification` ile bildirim gönderir (satır 159-169).
- **Dashboard**: `GET /dashboard` (`main.py:232-239`) `static/dashboard.html`
  dosyasını servis eder; FastAPI süreci `settings.api_port`'ta dinler
  (yerel `.env`: `8080`; `config.py` varsayılanı `8000`). Sunucuda
  port **9091** supervisord komut satırında açıkça verilir (`--port 9091`,
  2026-08-21 doğrulandı) ve yalnız 127.0.0.1'e bağlıdır; dashboard'a
  `ssh -L 9091:127.0.0.1:9091 awa` tüneliyle erişilir (bkz. RUNBOOK).

## 9. Bilinen tuzaklar (kod seviyesinde)

- **algoOrder geçişi**: STOP/TAKE_PROFIT emirleri `/fapi/v1/order` yerine
  `/fapi/v1/algoOrder`'a taşınmış; yanıtta `orderId` yerine `algoId` gelir,
  istemci bunu `orderId` takma adıyla eşler (`binance_client_improved.py:
  958-972`).
- **Rate limiter yarışı**: eski kilitsiz check-then-act, N coroutine'in aynı
  beklemeyi hesaplayıp aynı anda istek atmasına yol açıp 418 ban'ı
  tetikliyordu; `asyncio.Lock` ile slot atomik rezerve edilir
  (`src/core/rate_limiter.py:5-11,49-59`, commit `8321ddf`).
- **Dashboard force-fresh açlığı**: `/api/status`'un her 5 sn'de bir
  `force_fresh=True` ile pozisyon sorgusu ataması, rate-limiter kuyruğunu
  doyurup scan döngüsünü açlığa itiyordu (2026-08-18 watchdog restart kökeni);
  düzeltme `force_fresh=False` + 15sn account önbelleği
  (`main.py:412-419`, `binance_client_improved.py:1424-1441`).
- **Testnet ağırlık başlığı tutarsız**: `X-MBX-USED-WEIGHT-1M` testnet'te
  "edge-bazlı" (aynı dakikada 1912→375 görülmüş); uyarı eşiği bu yüzden
  gerçek 2400 sınırına yakın (`1800`) tutuluyor, mutlak değer değil trend
  sinyali olarak kullanılıyor (`binance_client_improved.py:398-415`,
  commit `eb96ec9`).
- **Kurtarılan (recovery) kapanış kayıtları güvenilmez olabilir**: restart
  sırasında borsada zaten kapanmış bulunan pozisyonlar için income→
  userTrades ledger→tahmini brüt PnL merdiveni uygulanır; hiçbiri
  doğrulanamazsa `exit_reason="UNKNOWN"` ve `notes` içine
  `exit_fill=unverified`/`close_verification=unverified` etiketi eklenir —
  "bilinmiyor" asla "kapandı"/"TP" diye maskelenmez (`exits.py:1000-1080`,
  `1061`).
- **"Bilinmiyor" asla "kapandı" sayılmaz**: `_step_one` pozisyon sorgusu
  hata verirse izleme O TUR atlanır, pozisyon "kapandı" varsayılmaz
  (`exits.py:147-155`).
- **Fail-closed giriş kilidi (entry halt)**: `UnprotectedPositionError`
  görülünce (`position_manager.py:32-33`) tüm yeni scalper girişleri kalıcı
  dosyaya (`state/scalper_entry_halt.json`) yazılıp durdurulur; mainnet'te
  bu kilit `False` yapılamaz — `config.py:420-429` startup'ta reddeder.
- **Reaper `trailing_active` muafiyeti**: BE'ye ulaşmış (TP1 dolmuş)
  pozisyonlar yaş limitine bakılmaksızın açık kalabilir; yalnız BE'siz
  yaşlı pozisyonlar reduce-only kapatılır (`engine.py:611-623`).
- **Tur başına tek reaper kapanışı**: 5 eşzamanlı reduce-only kapanışın
  safety turunu şişirip watchdog restart tetiklediği 2026-08-14 olayından
  sonra eklendi (`engine.py:661` yorum + `return`).

## 9b. İkinci çalışma modu: AlgoPro takipçi halkası (D20)

`BOT_MODE=follower` (varsayılan `scalper`) AYNI kod tabanını AYRI bir süreç/hesap
olarak çalıştırır: **scanner, strateji C ve TV sağlaması KAPALIDIR**; giriş ve çıkış
yalnız AlgoPro V1.6 alarmlarından gelir. `src/main.py` lifespan'ında erken bir dal
(`settings.is_follower_mode`) ScalperEngine/TradingOrchestrator/Telegram-VIP akışını
HİÇ kurmaz — orchestrator açık pozisyonları sahiplendiği için takipçiyle çakışırdı.

```mermaid
flowchart TD
    TV["TradingView\nAlgoPro V1.6 alert()"] --> W["ana bot :9091\n/tv-signal (secret)"]
    W -->|"src=algopro"| F["follower_forwarder\nfire-and-forget, 2sn"]
    W -->|"BUY/SELL"| SC["scalper: TvConfluence → external_signal\n(BUGÜNKÜ davranış)"]
    F --> E["takipçi :9093\n/follower/event (ayrı secret)"]
    E --> P["parser.parse_follower_event"]
    P --> L["levels.resolve_levels\n(mesaj > k×ATR)"]
    L --> PL["plan.build_plan\nmarj %10 + dinamik kaldıraç"]
    PL --> X["FollowerExecutor\nMARKET → SL → 3× TP"]
    X --> XM["FollowerExitManager\nTP1→BE, kapanış defteri"]
    XM --> DB["scalp_trades (strategy=AP)\ntradingbot_ap.db"]
```

| Dosya | Sorumluluk |
|---|---|
| `src/strategies/follower/types.py` | veri sözleşmesi (`FollowerEvent`, `FollowerLevels`, `FollowerPlan`, `LeverageBracket`) |
| `src/strategies/follower/parser.py` | AlgoPro alert gövdesi → olay (saf); birincil biçim `\|` ayraçlı `Anahtar: değer`, ikincil `kind=…` şablonu |
| `src/strategies/follower/levels.py` | SL/TP çözümü (saf): **birincil** mesaj seviyeleri, **yedek** `k×ATR` + RR katları |
| `src/strategies/follower/plan.py` | marj/kaldıraç/miktar (saf) + borsa dilimi & likidasyon kapıları |
| `src/strategies/follower/brackets.py` | `/fapi/v1/leverageBracket` TTL önbelleği (fail-closed) |
| `src/strategies/follower/executor.py` | korumalı açılış: MARKET → `pm.place_stop_loss_or_close` → 3× reduce-only TP → defter |
| `src/strategies/follower/exits.py` | `ExitManager` alt sınıfı: TP1→BE, TP2/TP3 telemetri, kapanış defteri, restart kurtarma |
| `src/strategies/follower/risk_halt.py` | D10 risk-olayı halt'ının takipçi kopyası (TTL, fail-closed, RAM latch) |
| `src/strategies/follower/engine.py` | kapılar, kill switch, safety/readiness döngüleri, `/follower/status` |
| `src/services/follower_forwarder.py` | ana bottaki köprü (yalnız `src=algopro`, secret başlıkta) |

**Yeniden kullanılanlar (yeniden YAZILMADI):** `ImprovedBinanceClient`,
`PositionManager` (SL kurulamazsa acil kapatma), `ScalpTracker`/`scalp_trades`,
`ExitManager`'ın kapanış doğrulama merdiveni (income → userTrades → tahmini),
`_confirmed_algo_fill` fill kanıtı, `fee_aware_breakeven_price`, `/risk-event` kanalı.

**Boyutlama (scalper'dan FARKLI, kullanıcı kararı 2026-08-23):** marj = bakiyenin
`%FOLLOWER_MARGIN_PCT`'i; kaldıraç `clamp(round(FOLLOWER_SL_ROI_TARGET / sl_pct),
LEV_MIN, LEV_MAX)` — yani stop DAİMA marjın ~%30'u. Üstüne borsa kaldıraç dilimi,
`lev × sl_pct ≤ 50` ve `1/lev − mmr > 2 × sl_pct/100` kapıları (hepsi yalnız DÜŞÜRÜR).

**Scalper halkasına dokunulan yerler (davranış NÖTR):** `ExitPlan`'a varsayılanı
0/None olan `tp3_*` alanları; `_verified_close_ledger`'a opsiyonel `tp3_algo_id`
adayı; `ScalpTradeModel.tp3_algo_id` sütunu (idempotent migration) ve
`record_open(tp3_algo_id=None)`. Scalper bu alanları HİÇ doldurmaz.

## 10. Sözlük

| Terim | Anlam |
|---|---|
| **C** | Strateji varyantı "Saf Uç Avcısı": rejim filtresiz, RSI ucu + Bollinger taşması + diverjans → ters yönde giriş, `risk_multiplier=0.5` (`setups.py:431-565`) |
| **Rejim kapısı** | `DOWN`'da LONG, `UP`'ta SHORT sinyalini engelleyen kural (`engine.py:815-834`) |
| **Lider piyasa kapısı** | Liderin (BTCUSDT) gün-içi sapmasına ve çok-günlük koşusuna bakıp o yöne yeni giriş kapatan kural (§4.1, `market_gate.py`; varsayılan KAPALI) |
| **Sağlama / confluence** | Birden çok farklı TV göstergesinin aynı yönde oy vermesi şartı (`tv_confluence.py`) |
| **Reaper** | Yaş limitini aşan, BE korumasız pozisyonları reduce-only MARKET ile kapatan görev (`engine.py:602`) |
| **Chandelier** | ATR tabanlı, yalnız lehte kayan trailing stop (`indicators.py:280`) |
| **BE (break-even)** | TP1 dolunca SL'nin komisyon+buffer karşılayan sıfır-net seviyeye çekilmesi (`types.py:176`) |
| **TP ladder** | TP1 (%40, +20% ROI) → TP2 (%30, +50% ROI) → runner (%30, chandelier) merdiveni |
| **Entry halt** | `UnprotectedPositionError` sonrası kalıcı, dosya-tabanlı, fail-closed yeni-giriş kilidi |
| **Close ledger** | `_CloseLedger` — borsa `userTrades` satırlarıyla kanıtlanmış kapanış özeti (`exits.py:45-51`) |
| **LUCID** | Bu kod tabanında bir referans/isim olarak **bulunamadı** — kodda doğrulanamadı |
| **luxosc / luxso** | TV alert kaynak etiketleri (`?src=`); kodda sabit tanım/whitelist yok, serbest string (bkz. §8) |
| **fixed_roi** | Stop modu: mesafe = `fixed_stop_roi_pct / kaldıraç` (marj-yüzdesi stop) |
| **Maker entry** | `scalper_entry_mode=maker`: LIMIT GTX (post-only) iki fazlı giriş, `check_pending` ile dolum takibi |
| **Allowlist** | `scalper_symbol_allowlist` (evren) / `scalper_tv_symbol_allowlist` (TV) — CSV sembol filtresi |
| **MAE/MFE** | Maximum Adverse/Favorable Excursion — pozisyon ömrü boyunca en kötü/en iyi ROI% ucu (`exits.py:942-953`) |
