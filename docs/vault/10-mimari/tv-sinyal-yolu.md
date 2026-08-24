---
tags: [mimari, tradingview, webhook, confluence]
guncelleme: 2026-08-24
kaynak: src/main.py, src/services/tv_confluence.py, src/services/tv_events.py, docs/INTEGRATIONS.md, docs/DECISIONS.md D9/D19/D19a
---

# TradingView sinyal yolu

## NE

TradingView alarmlari tek bir HTTP ucuna gelir: `POST /tv-signal`.
Bu ucun **iki ayri yolu** vardir ve karistirilmalari sistemi bozar:

| Yol | Tetik | Nereye gider | Motor etkisi |
|---|---|---|---|
| **GIRIS** (`kind` yok ya da `kind=entry`) | LuxAlgo OSC / S&O, AlgoPro, BotV3 | `TvConfluence.vote` → `engine.external_signal` | pozisyon acabilir |
| **OLAY** (`kind=exit\|choch\|trend\|tp1`) | S&O Exit, Trend Catcher/Tracer, PAC CHoCH, AlgoPro TP1 | `TvEvents` defteri (`src/services/tv_events.py:340`) | varsayilan **GOLGE** = davranis degismez |

## NEREDE

| Ne | Yer |
|---|---|
| Webhook uc noktasi | `src/main.py:1814` (handler `src/main.py:1815`) |
| Kaynak (`src=`) cozumu | `src/main.py:1230` |
| Giris sinyali cozumu | `src/main.py:1768` |
| Olay yonlendirme | `src/main.py:2019` |
| Olay kaynagi giris oyu VEREMEZ (422) | `src/main.py:1575` |
| Saglama (confluence) motoru | `src/services/tv_confluence.py:45` |
| Olay defteri | `src/services/tv_events.py:159` |
| Defter sifirlama ucu | `src/main.py:2125` |
| Motor girisi | `src/strategies/scalper/engine.py:3661` |
| AlgoPro koprusu (ayri halka) | `src/main.py:1255` → `src/services/follower_forwarder.py:191` |
| AlgoPro sürec-ici teslim (gomulu) | `src/main.py:1277` |

## NASIL CALISIR

### Saglama kurali (yalniz GIRIS oylari)

Oy = **(sembol, yon, kaynak)**. `TV_CONFLUENCE_WINDOW_SECONDS` icinde
`TV_CONFLUENCE_REQUIRED` kadar **FARKLI** kaynak ayni yonde oy vermeli.
Ayni kaynagin tekrar oyu sayiyi ARTIRMAZ, yalniz zaman damgasini tazeler.
**Ters yon oyu gelirse HER IKI tarafin oylari silinir** — gostergeler
anlasamiyorsa sinyal temiz degildir (`src/services/tv_confluence.py:45`).

Canli sozlesme (CLAUDE.md): 2 farkli kaynak / 420 sn.

### Kaynak etiketi

`?src=` serbest metindir ama D9'dan beri `TV_SOURCE_ALLOWLIST`'e karsi
dogrulanir: bilinmeyen deger **reddedilmez**, jenerik `tv`'ye eslenir ve
WARNING loglanir ([[20-kararlar/D9-webhook-sertlestirme]]).
`?src=` yoksa kaynak AlgoPro'nun mesaj parmak izinden tahmin edilir.

### Olay yolu (D19/D19a)

- Belirtecler (`src=`, `kind=`) **mesajin BASINDAKI baslik kosusundan** okunur
  — govdenin ortasindaki kullanici metni bir alarmin kimligini degistiremesin
  diye (D19a/G1).
- `TV_EVENT_SOURCES` listesindeki bir kaynak **giris oyu veremez → 422**
  (D19a/A). Bu tek yonlu ayrimdir ve pazarliga kapalidir.
- Uc kademe: `SCALPER_TV_EVENTS_MODE=off|shadow|active`, varsayilan **shadow**.
- MIXED (kapi kaynaklari celisiyor) → **kapi UYGULANMAZ** (celiski
  "bilinmiyor"dur, "her iki yon de yasak" degil) (D19a/F).
- `active` modda cikis aksiyonu `be` YALNIZ pozisyon **kardayken** uygulanir;
  zarardaysa `SCALPER_TV_EVENTS_EXIT_LOSING` (vars. `skip`) karar verir
  (D19a/B — aksi halde `-2021` → acil piyasa kapanisi).

## TUZAKLAR

- **`src=` / `kind=` mesajin BASINDA olmali.** Ortada yazilirsa okunmaz.
- **Olay kaynaklari giris oyu veremez.** `kind` belirteci duserse D19a
  oncesinde bir CIKIS alarmi GIRIS OYUNA donusup pozisyon actirabiliyordu.
- **Sunucu `.env`'i `TV_SOURCE_ALLOWLIST`'i set ederse kod varsayilani
  devreye girmez** → olay etiketleri sessizce eski etikete duserdi (D19a/E).
  Bu yuzden olay yolu allowlist'ten BAGIMSIZ calisir; teshis:
  `/scalper/status` → `tv_events.allowlist_ok`.
- **`MAX_AGE_MIN=0` ve bos `GATE_SOURCES` = KAPALI demektir**, "sinirsiz"
  degil (D19a/G5). `active` + bos `GATE_SOURCES` startup'ta `ValueError`.
- **Backtest paritesi yoktur ve bilinclidir**: TV olaylari yalniz canli motoru
  etkiler (`docs/INTEGRATIONS.md` §7.6).
- **2026-09-14'te 49 TV alarmi expire oluyor** — takvim maddesi
  ([[40-isletme/gunluk-kontrol]]).
- **Golge halka TV webhook'u ALMAZ** ([[20-kararlar/D26-golge-halkasi]]),
  bu yuzden karsilastirma yalniz C tarayici yolunu kapsar.

## ILGILI

[[10-mimari/motor-scalper]] · [[10-mimari/takipci-algopro]] ·
[[20-kararlar/D7-tv-sembol-allowlist]] · [[20-kararlar/D19-tv-olay-kanali]] ·
[[20-kararlar/D19a-tv-olay-duzeltmeleri]] · [[40-isletme/panel-erisimi]]
