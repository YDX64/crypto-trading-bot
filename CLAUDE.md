# TRADINGBOT — çalışma sözleşmesi (önce bunu oku)

Bu dosya, bu repoya bakan her yapay zekâ/mühendis için **zorunlu** giriş kapısıdır.
Amaç: 10 dakikada sistemi doğru anlamak, uydurmamak, bozmamak.

## Ne bu?
Binance USDⓈ-M Futures için Python (FastAPI + asyncio) scalping botu. Aktif strateji
**C** (RSI ucu + Bollinger taşması + RSI diverjansı, ters yön giriş) — `src/strategies/scalper/`.
Sinyal kaynakları: botun kendi taraması + TradingView webhook'ları (LuxAlgo OSC/S&O,
AlgoPro) → `src/services/tv_confluence.py` sağlaması (2 farklı kaynak / 420 sn).
TV'nin ÇIKIŞ/YAPI alarmları (S&O Exit, Trend Catcher/Tracer, PAC CHoCH, AlgoPro TP1)
AYRI bir yoldan gelir: gövdede `kind=exit|choch|trend|tp1` → `src/services/tv_events.py`
(sağlamaya GİRMEZ). Varsayılan `SCALPER_TV_EVENTS_MODE=shadow` = motor davranışı
değişmez; bkz. `docs/DECISIONS.md` D19 + **D19a** (24 düşmanca inceleme bulgusu;
D19 ile çelişirse **D19a bağlayıcıdır**) + `docs/INTEGRATIONS.md` §7. Alarm mesajında
`src=`/`kind=` belirteçleri mesajın **BAŞINDA** olmalıdır; olay kaynakları
(`TV_EVENT_SOURCES`) giriş oyu VEREMEZ (422).
Ayrıntı: `docs/ARCHITECTURE.md`. Kararlar ve kanıtları: `docs/DECISIONS.md`.
İşletme: `docs/RUNBOOK.md`. Deney defteri: `docs/EXPERIMENTS.md`.
Yeni sinyal kaynağı (haber botu vb.) eklemek: `docs/INTEGRATIONS.md`. Otomatik deney döngüsü:
`docs/AUTORESEARCH.md`. Mainnet'e geçiş şartları ve mimarisi: `docs/MAINNET_PLAN.md`.

## Nerede çalışır? (gerçeğin kaynağı)
- **Kod:** GitHub `YDX64/crypto-trading-bot` `main` — tek gerçek. Sunucu repo'su
  (`awa:/opt/tradingbot-v2`) bunu izler; **scp ile dosya kopyalama YASAK**.
- **Canlı süreç (TESTNET):** supervisord programı `tradingbot_v2`
  (`/opt/tradingbot-v2/.venv/bin/python -m uvicorn src.main:app :9091`).
  `systemctl`'deki `live-bot.service` **trading botu DEĞİLDİR** (futbol botu).
- **İkinci halka (D20 + D20a, TESTNET):** `BOT_MODE=follower` — AlgoPro takipçisi,
  `/opt/tradingbot-ap`, supervisord `tradingbot_ap`, port 9093, AYRI Binance testnet
  hesabı/DB/state/log. Scanner ve strateji C KAPALI; giriş/çıkış yalnız AlgoPro V1.6
  alarmlarından. Deploy: `scripts/deploy.sh awa --ring follower`. Mainnet'e ÇIKAMAZ
  (config fail-fast). **D20 ile çelişirse D20a bağlayıcıdır** (19-ajan düşmanca
  inceleme): köprü ve giriş yalnız KATI AlgoPro biçimini kabul eder, ücret eşiği kapısı
  `FOLLOWER_MIN_TP1_FEE_RATIO=1.0` VARSAYILAN AÇIKTIR (stop ≥ ~%0.20), dolum stopu
  geçtiyse pozisyon kapatılır (yeniden çapalama YOK), yetim pozisyon = entry-halt.
  Ayrıntı: `docs/RUNBOOK.md` "AlgoPro takipçi halkası", D20/D20a.
- **GÖMÜLÜ takipçi (D20b, TERCİH EDİLEN — kullanıcı kararı "yeni hesap yok, yeni panel
  yok"):** `FOLLOWER_EMBEDDED=true` (varsayılan false) ile takipçi scalper ile AYNI
  süreçte/hesapta/panoda çalışır; boyutlaması **1000 USD'lik SANAL deftere** dayanır
  (`FOLLOWER_VIRTUAL_CAPITAL_USDT`, equity = taban + AP net PnL, DB'den). AlgoPro
  alert() gövdesi `/tv-signal`'dan **süreç içi** takipçiye gider ve ana botun
  sağlamasına OY VERMEZ (eski özel mesaj biçimi eskisi gibi oy verir).
  `FOLLOWER_SYMBOLS` doluysa o sembol(ler) scalper'ın tarama evreninden ve TV giriş
  oylamasından OTOMATİK çıkarılır; sembol KODDA SABİT DEĞİLDİR (yalnız `.env`).
  Çakışma koruması: süreç-içi `symbol_reservations` (sembol başına TEK motor).
  `/risk-event` gömülü modda İKİ motoru da kapsar. Açma reçetesi:
  `docs/RUNBOOK.md` "Gömülü takipçiyi açma"; karar+kanıt: D20b.
  **D20/D20a ile mimari çelişkide D20b bağlayıcıdır; D20a'nın KAPILARI aynen geçerlidir.**
- **Container yolu (D23, EK dağıtım — canlı DEĞİL):** botun tamamı tek bir
  `python:3.12-slim` görüntüsünde; başka sunucuya taşımak için. Başlatma
  `scripts/docker_run.sh` (çıplak `docker compose up` YASAK — entry-halt, 418 ban
  ve "supervisord ile aynı anda" kapılarını atlar). ⛔ **supervisord ile container
  AYNI ANDA ÇALIŞAMAZ** (aynı Binance hesabı/pozisyonlar → çift yönetim; D20b'deki
  kritik sınıfın aynısı). Reçete: `docs/RUNBOOK.md` "Container ile çalıştırma /
  başka sunucuya taşıma"; karar+kanıt: D23.
- **Ayarlar:** `/opt/tradingbot-v2/.env` (commit'lenmez; her değişiklikte
  `backups/env.bak-<tarih>-<etiket>` yedeği). Varsayılanlar `src/core/config.py`.
  Kapalı duran kanallar: `RISK_EVENT_SECRET` (boş = /risk-event 503), `SCALPER_SHADOW_MODE=false`,
  `SCALPER_MARKET_DATA_BASE_URL=` (boş = kline'lar da işlem host'undan; D17 adayı — doldurmadan
  önce `docs/RUNBOOK.md` "Kline kaynağını mainnet'e alma").
- **Veri:** `tradingbot.db` (sqlite, `scalp_trades`), `state/` (cooldown, entry-halt),
  `logs/bot.log` (uygulama), `logs/supervisor.log` (erişim logu — **secret içerir, dökme**).
- **Bir işlemi incelemek:** her işlemin "neden girildi / nasıl çıkıldı / ne ters gitti"
  kaydı `scalp_trades.forensics` + `logs/trades.jsonl`'dedir (D21, YALNIZ GÖZLEM —
  motor davranışını değiştirmez). Panoda "Son İşlemler" satırına tıkla; uçlar
  `/scalper/trades/{id}/forensics`, `/scalper/forensics/summary?since=7d`;
  rapor `scripts/ledger_report.py --forensics`. Reçete: `docs/RUNBOOK.md`
  "Bir işlemi nasıl incelerim".
<<<<<<< HEAD
- **AI karar katmanı (D23, GÖLGE — kod varsayılanı `off`):** `SCALPER_AI_GATE_MODE`
  `shadow` iken motor pozisyonu AÇTIKTAN sonra bağlam bir dil modeline sorulur
  ("bu giriş alınmalı mıydı?") ve karar YALNIZ kaydedilir
  (`logs/trades.jsonl` `ai_verdict` + `scalp_trades.forensics` → `document["ai"]`,
  migration YOK). Kanca `_entry_lock` DIŞINDA, ateşle-unut: motor 0 ms bekler ve
  karar yolu BAYT BAYT aynıdır. **Yalnız `deny` etkiler; `allow` hiçbir şey
  AÇMAZ** — bu bir kalite filtresidir, güvenlik cihazı DEĞİLDİR ve her arıza
  fail-OPEN'dır. `active` config validator ile REDDEDİLİR (go_live ölçütleri
  D23'te; ayrıca #P1 harness paritesi gerekir). Sağlayıcı zinciri DeepSeek →
  Gemini → OpenAI (yeni pip bağımlılığı yok). Rapor:
  `scripts/ledger_report.py --ai`; açma/kapama: `docs/RUNBOOK.md` "AI karar
  katmanını açma/kapama"; karar+kısıtlar: `docs/DECISIONS.md` D23.

## Nasıl çalıştırılır / test edilir / deploy edilir
```bash
python3 -m pytest tests -q                      # 2242 test, ~65 sn — her değişiklikten önce
=======
- **GerçekleşMEyen niyetler (D24):** kapı reddi / TV sağlaması dolmadı / emir hatası
  artık `logs/trades.jsonl`'e `event="intent"` olarak yazılır (niyet→karar→borsa
  sonucu). Ret gerekçesi dağılımı `/scalper/forensics/summary` yanıtındaki `intents`
  bloğundadır — **süreç başlangıcından beri** sayar, restart'ta sıfırlanır.

## Nasıl çalıştırılır / test edilir / deploy edilir
```bash
python3 -m pytest tests -q                      # 2072 test, ~50 sn — her değişiklikten önce
>>>>>>> worktree-agent-af27869057747ff76
scripts/deploy.sh awa                           # push edilmiş main'i sunucuya uygula (test + restart + sağlık + otomatik geri alma)
DEPLOY_NO_RESTART=1 scripts/deploy.sh awa       # yalnız kod/test; süreci yeniden başlatma
scripts/deploy.sh awa <önceki-commit>           # geri alma (backups/commit.prev-*)
# SUNUCUDA, yalnız .env değiştiyse (çıplak `supervisorctl restart` YASAK — D20a):
# RESTART_LABEL=<etiket> scripts/restart_safe.sh testnet|follower|mainnet
```
Backtest DAİMA sunucu env'iyle:
```bash
env $(ssh awa grep ^SCALPER_ /opt/tradingbot-v2/.env | xargs) python3 -m src.strategies.scalper.backtest \
  --strategies C --symbols BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,BNBUSDT,ADAUSDT,LTCUSDT \
  --start 2026-01-23 --end 2026-02-13        # ayı | yatay: 2026-07-01→07-21 | boğa: 2026-08-07→08-21
```
Koşuları sıralı yap (paralel = Binance 429). Sonuç `docs/EXPERIMENTS.md`'ye log yoluyla girer.

**Ölçüm/kanıt bayrakları (D24, hepsi VARSAYILAN KAPALI — motor davranışını değiştirmez):**
`--permutations N` (+`--permutation-clamp-audit`) Monte-Carlo permütasyon p-değeri;
`--fee-stress` komisyon+kayma 2×; `--entry-delay-candles N` giriş gecikmesi çürütme
koşusu. Çok-varyant taramasında yanlış-pozitif düzeltmesi:
`python3 -m src.strategies.scalper.multitest --json <tarama.json>` (Benjamini-Hochberg).
Rapor artık `bar_max_drawdown` (bar-bazlı mark-to-market çöküş — kapanış-bazlı
`max_drawdown` gerçek çukuru SIĞ gösterir) ve konsantrasyon (tek sembol/işlem/gün kâr
payı) satırlarını da içerir. Ayrıntı ve ölçülmüş etkiler: `docs/DECISIONS.md` D24.
**`docs/EXPERIMENTS.md`'nin BAŞINDAKİ uyarı kutusunu oku:** E2…E9'un 36 varyantının
tamamı aynı üç pencerede ölçüldü — dokunulmamış bir doğrulama penceremiz YOK.

## Yasaklar (ihlal = sistemi bozma)
1. Kanıtsız parametre değişikliği yok: her `SCALPER_*` değişikliği önce 3 rejim
   penceresinde (ayı/yatay/boğa) backtest, karar kuralı: ayıda PF ≥ 1.1 **ve** boğada
   PnL kaybı ≤ %20; sonra testnet'te ≥5 gün; sonra mainnet (henüz yok).
2. Harness ile canlı motor aynı kuralları uygular (rejim kapısı vb.) — birini
   değiştirirken diğerini de değiştir ve parite testini güncelle (bkz. DECISIONS #P1).
3. Binance ban (418) aktifken restart **yasak**; entry-halt dosyası varken deploy yasak.
4. `indicator_set_inputs` (TradingView MCP) LuxAlgo script'lerini bozar — kullanma.
5. Secret'lar yalnız `.env`'de; log/çıktı/commit'e asla. Erişim logu secret içerir.
6. Bir sonucu log/rapor yolu olmadan "kanıt" sayma; "çalışıyor" demeden önce
   `supervisorctl status tradingbot_v2` + `ps -o etimes=` ile restart'ı doğrula.

## Karar verirken
- Simülatörün mutlak sayıları rejime duyarlı; kararlar **göreli** farklarla ve canlı
  defter aritmetiğiyle verilir. Canlı defter nihai hakemdir.
- Başabaş kazanma oranı ≈ %85 (SL ort −514 vs TRAIL ort +88 birim); kenar incedir —
  "kazanıyor" iddiası rejime (UP/FLAT/DOWN gün) bölünmeden kabul edilmez.
- Sürpriz bir şey görürsen önce `docs/DECISIONS.md`'de denenmiş mi bak; sonra
  `docs/RUNBOOK.md` tuzaklarına; sonra kodu oku. Uydurma; "kodda doğrulanamadı" de.

## Yapay zekâ çalışma kuralı — model ve efor (KULLANICI KARARI, 2026-08-22)
Bu proje gerçek parayla ilgilidir; ucuz/hızlı model ile "idare etme" YASAK.
- **Her zaman en yüksek model + en yüksek efor.** Ana oturum: Opus 5 max / Fable 5 max
  (ultracode açık). Alt ajanlar (`Agent`, `Workflow`): **Sonnet KULLANILMAZ** — `model: 'opus'`
  (ya da fable) ve `effort: 'max'`/'xhigh'. Haiku yalnız salt-okuma dosya listeleme gibi
  gerçekten mekanik işlerde; strateji/kod/analiz/incelemede ASLA.
- Token maliyeti gerekçe değildir: yanlış bir parametre ya da gözden kaçan bir hata,
  tasarruf edilen her token'dan pahalıdır.
- Motor değişikliği, backtest yorumu, risk kararı, düşmanca inceleme: en yüksek model,
  çok-mercekli (3+) inceleme + çürütme turu.

## Bu dosyayı güncelleme kuralı
Canlıya giren her değişiklik aynı commit'te `docs/DECISIONS.md`'ye (ne/neden/kanıt/geri
alma) ve gerekiyorsa buraya işlenir. Hafıza dosyaları/awa-brain yalnız işaretçidir.
