---
tags: [giris, zorunlu, ai-icin]
guncelleme: 2026-08-24
kaynak: CLAUDE.md, AGENTS.md, docs/ARCHITECTURE.md, docs/RUNBOOK.md, docs/DECISIONS.md
---

# 00 — BASLA BURADAN

> Bu not **giris kapisidir**. Once bunu oku, sonra `CLAUDE.md`'yi oku, sonra ne
> yapacaksan onun notuna git. Baska sira yok.

## Sistem tek cumlede

Binance USDⓈ-M Futures **TESTNET**'inde calisan, tek FastAPI surecinde yasayan
bir scalping botu; aktif strateji **C** (RSI ucu + Bollinger tasmasi + RSI
diverjansi → **ters yonde** giris), sinyaller hem botun kendi taramasindan hem
TradingView webhook'larindan gelir, cikis TP1/TP2 + break-even + chandelier
trailing merdivenidir.

**Gercek para YOK.** Testnet. Mainnet halkasi henuz kurulmadi
([[20-kararlar/mainnet-plani]]).

## ASLA YAPMA (CLAUDE.md yasaklari — ihlal = sistemi bozma)

| # | Yasak | Neden |
|---|---|---|
| 1 | **Kanitsiz parametre degisikligi.** Her `SCALPER_*` degisikligi once 3 rejim penceresinde backtest (AYI/YATAY/BOGA), karar kurali: AYI PF ≥ 1.1 **ve** BOGA PnL kaybi ≤ %20; sonra testnet ≥5 gun; sonra mainnet. | Bir yanlis parametre gercek para kaybi. Bkz. [[20-kararlar/P2-karar-kurali]] |
| 2 | **Harness ile canli motoru ayirmak.** Birini degistiren digerini ve parite testini de degistirir. | [[20-kararlar/P1-harness-parite]] |
| 3 | **Binance ban (418) aktifken restart**; entry-halt dosyasi varken deploy. | Ban suresini uzatir / kilidi atlar. [[10-mimari/guvenlik-kilitleri]] |
| 4 | `indicator_set_inputs` (TradingView MCP) kullanmak. | LuxAlgo script'lerini bozar. |
| 5 | **Secret'i log/cikti/commit'e yazmak.** `logs/supervisor.log` erisim logu secret icerir — dokme. | [[50-veri/loglar]] |
| 6 | Log/rapor yolu olmayan bir sonucu "kanit" saymak; restart'i dogrulamadan "calisiyor" demek. | [[90-ai-icin/dogrulama-receteleri]] |
| 7 | **Ayni Binance hesabinda iki motor calistirmak** (supervisord + container, ayri halka + gomulu takipci, canli + golge halka). | Cift SL/TP, yarisan devralma. [[40-isletme/halka-yonetimi]] |
| 8 | **scp ile sunucuya dosya kopyalamak.** Tek gercek kaynak GitHub `main`. | [[40-isletme/deploy-ve-geri-alma]] |
| 9 | Ciplak `supervisorctl restart`. | Ban penceresini/entry-halt'i/`.env` yedegini/saglik yoklamasini atlar. `scripts/restart_safe.sh` kullan. |

## 5 dakikalik yonelim

1. **Nerede calisiyor?** Sunucu `awa`, dizin `/opt/tradingbot-v2`, supervisord
   programi `tradingbot_v2`, uvicorn `src.main:app` port **9091**.
   ⚠️ `systemctl`'deki `live-bot.service` **futbol botudur**, bu bot degil.
2. **Ne kosuyor?** Tek surecte: ScalperEngine + TradingOrchestrator (eski
   Telegram akisi) + TelegramBotService + `/tv-signal` webhook koprusu; opsiyonel
   olarak **gomulu AlgoPro takipcisi** (`FOLLOWER_EMBEDDED`).
   Giris noktasi: `src/main.py:298` (`lifespan`).
3. **Karar nasil veriliyor?** `engine._scan_tick` (`src/strategies/scalper/engine.py:1605`)
   → `_evaluate_symbol` (`src/strategies/scalper/engine.py:1820`) → strateji C
   (`src/strategies/scalper/setups.py:459`) → kapilar → `ScalpExecutor.try_open`
   (`src/strategies/scalper/executor.py:820`).
4. **Nerede duruyor?** `scalp_trades` tablosu (`tradingbot.db`),
   `state/*.json` (cooldown, entry-halt, risk-event halt, pending journal),
   `logs/bot.log` + `logs/trades.jsonl`.
5. **Nasil test edilir?** `python3 -m pytest tests -q` — **2251 passed, 2 skipped**
   (~66 sn). `.env` yoksa `cp env.example .env` (CI de boyle yapar).

## Hangi soruda hangi nota gidiyorum

| Soru | Not |
|---|---|
| Motor nasil sinyal uretiyor, hangi kapilardan geciyor? | [[10-mimari/motor-scalper]] |
| TradingView'den ne geliyor, nasil isleniyor? | [[10-mimari/tv-sinyal-yolu]] |
| Emir nasil gidiyor, boyutlama nasil? | [[10-mimari/emir-yurutme]] |
| TP/BE/trailing/reaper nasil calisiyor? | [[10-mimari/cikis-yonetimi]] |
| Kapanis PnL'i nereden dogrulaniyor? | [[10-mimari/defter-ve-muhasebe]] |
| Entry-halt / 418 / kill-switch nedir? | [[10-mimari/guvenlik-kilitleri]] |
| AlgoPro takipcisi nedir, kac cesidi var? | [[10-mimari/takipci-algopro]] |
| Forensics / niyet kaydi / AI kapisi ne ise yarar? | [[10-mimari/gozlem-katmanlari]] |
| "Bu ayar neden boyle?" | [[20-kararlar/00-karar-indeksi]] |
| "Bu sayi nereden geldi?" | [[30-deneyler/00-deney-indeksi]] |
| Bot gunluk saglikli mi? | [[40-isletme/gunluk-kontrol]] |
| Nasil deploy/geri alma yapilir? | [[40-isletme/deploy-ve-geri-alma]] |
| Hangi halka nerede calisir, cakisma yasagi? | [[40-isletme/halka-yonetimi]] |
| Degraded / UNKNOWN rejim / 418 / entry-halt | [[40-isletme/sorun-giderme]] |
| Panoya nasil erisilir, yeni uc nasil eklenir? | [[40-isletme/panel-erisimi]] |
| DB semasi, kolonlar, `positions` tuzagi | [[50-veri/veritabani-semasi]] |
| Hangi log nerede, hangisi secret icerir? | [[50-veri/loglar]] |
| Hangi sayi hangi uctan gelir? | [[50-veri/metrikler]] |
| AI olarak calisma sozlesmem ne? | [[90-ai-icin/calisma-kurallari]] |
| Baskalari hangi hatalari yapti? | [[90-ai-icin/sik-yapilan-hatalar]] |
| Bir iddiayi nasil kanitlarim? | [[90-ai-icin/dogrulama-receteleri]] |

## Yanlis varsayim uyarilari (en sik dusulen 8 tuzak)

1. `README.md` **eskidir** (Ekim 2025). Gercek: `CLAUDE.md` + `docs/`.
2. `src/api_server.py` **kullanilmiyor**; tek app `src/main.py`.
3. `/scalper/status` acik pozisyonlari **`tracked`** anahtarinda dondurur,
   `positions` degil (`src/strategies/scalper/engine.py:4999`). `positions`
   sadece `/positions` ucunda ve **ayri bir DB tablosu** olarak vardir.
4. Rejim zaman dilimi kod varsayilaninda `4h`, canli `.env`'de **15m**.
5. Cok sayida ozellik **varsayilan KAPALI** ya da **GOLGE**: yapi kapisi (D18),
   TV olay kanali (D19), AI kapisi (D23), agirlik geri cekilmesi (D22).
   "Kodda var" ≠ "canlida acik".
6. Backtest **daima sunucu `.env`'iyle** kosulur; yerel `.env` bayattir.
7. Kazanma orani **%88** ama basabas orani **≈%85** — kenar incedir ve kazanma
   oranindan gelmez ([[30-deneyler/E10-permutasyon]]).
8. Bu defterdeki tum backtest sayilari **ayni uc pencerede** olculdu; OOS
   degildir ([[30-deneyler/00-metodoloji-uyarisi]]).
