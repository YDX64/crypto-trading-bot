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
- **Ayarlar:** `/opt/tradingbot-v2/.env` (commit'lenmez; her değişiklikte
  `backups/env.bak-<tarih>-<etiket>` yedeği). Varsayılanlar `src/core/config.py`.
  Kapalı duran kanallar: `RISK_EVENT_SECRET` (boş = /risk-event 503), `SCALPER_SHADOW_MODE=false`.
- **Veri:** `tradingbot.db` (sqlite, `scalp_trades`), `state/` (cooldown, entry-halt),
  `logs/bot.log` (uygulama), `logs/supervisor.log` (erişim logu — **secret içerir, dökme**).

## Nasıl çalıştırılır / test edilir / deploy edilir
```bash
python3 -m pytest tests -q                      # 640+ test, ~20 sn — her değişiklikten önce
scripts/deploy.sh awa                           # push edilmiş main'i sunucuya uygula (test + restart + sağlık + otomatik geri alma)
DEPLOY_NO_RESTART=1 scripts/deploy.sh awa       # yalnız kod/test; süreci yeniden başlatma
scripts/deploy.sh awa <önceki-commit>           # geri alma (backups/commit.prev-*)
```
Backtest DAİMA sunucu env'iyle:
```bash
env $(ssh awa grep ^SCALPER_ /opt/tradingbot-v2/.env | xargs) python3 -m src.strategies.scalper.backtest \
  --strategies C --symbols BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,BNBUSDT,ADAUSDT,LTCUSDT \
  --start 2026-01-23 --end 2026-02-13        # ayı | yatay: 2026-07-01→07-21 | boğa: 2026-08-07→08-21
```
Koşuları sıralı yap (paralel = Binance 429). Sonuç `docs/EXPERIMENTS.md`'ye log yoluyla girer.

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
