---
tags: [mimari, cikis, trailing, breakeven, reaper]
guncelleme: 2026-08-24
kaynak: src/strategies/scalper/exits.py, src/strategies/scalper/engine.py, src/strategies/scalper/types.py, src/strategies/scalper/indicators.py, docs/ARCHITECTURE.md §5
---

# Cikis yonetimi — TP merdiveni, break-even, chandelier, reaper

## NE

Acik pozisyonun kaderi safety turunda belirlenir: TP1 → break-even → TP2 →
chandelier trailing → (kosucu). Yasli ve BE'ye hic ulasmamis pozisyonlari
"reaper" kapatir. **Odeme asimetrigi buradan gelir:** SL ortalamasi ≈ −514,
TRAIL ortalamasi ≈ +88 birim → basabas kazanma orani **≈%85**.

## NEREDE

| Ne | Yer |
|---|---|
| Cikis yoneticisi | `src/strategies/scalper/exits.py:112` |
| Tur | `src/strategies/scalper/exits.py:225` → tek sembol `src/strategies/scalper/exits.py:237` |
| TP1 | `src/strategies/scalper/exits.py:317` |
| TP2 | `src/strategies/scalper/exits.py:387` |
| Dolum KANITI (userTrades) | `src/strategies/scalper/exits.py:461` |
| Chandelier trailing | `src/strategies/scalper/exits.py:753` |
| Koruma-tarafi kapisi | `src/strategies/scalper/exits.py:691` |
| Fiyat-uzayi cevirisi (ayri host) | `src/strategies/scalper/exits.py:546` |
| Acil kapanis kaydi | `src/strategies/scalper/exits.py:901` · `src/strategies/scalper/exits.py:957` |
| BE zorlama | `src/strategies/scalper/exits.py:1096` |
| Kapanis isleme | `src/strategies/scalper/exits.py:1238` → `src/strategies/scalper/exits.py:1306` |
| Restart kurtarmasi | `src/strategies/scalper/exits.py:2309` |
| Reaper | `src/strategies/scalper/engine.py:1439` |
| Reduce-only MARKET kapanis | `src/strategies/scalper/engine.py:1490` |
| Ucret-farkinda BE fiyati | `src/strategies/scalper/types.py:182` |
| Kademeli trail carpani | `src/strategies/scalper/types.py:158` |
| Chandelier hesabi | `src/strategies/scalper/indicators.py:280` |

## NASIL CALISIR

### 1. TP1 (`SCALPER_TP1_ROI` / `_FRACTION`)

Canli miktar esigin altina duserse **gercek algo child-order fill'i borsa
`userTrades` satirlariyla KANITLANIR** — miktar tahmini sayilmaz. Onaylaninca
SL, `fee_aware_breakeven_price` hedefine cekilir (komisyon + buffer'i cebirsel
karsilayan seviye) ve `trailing_active=True` olur.

### 2. TP2 (`SCALPER_TP2_ROI` / `_FRACTION`)

Ayni dogrulama deseni. Onaylaninca kosucu tabani TP1 fiyatina yukselir.

### 3. Chandelier trailing

LONG icin `max(high[girisden itibaren]) − atr_mult × ATR(14)`. Carpan
`resolve_trail_mult` ile tepe ROI esiklerini gectikce **kademeli buyur**
(tek yonlu — geri cekilmede sikilasmaz). **Stop yalniz lehte kayar**; degisim
"once yeni SL, sonra eskisini iptal" deseniyle **bosluksuz** uygulanir.

### 4. Reaper (`SCALPER_MAX_HOLD_HOURS`, 0 = kapali)

`trailing_active=True` olan pozisyonlar **MUAF** (BE korumali kosucu).
Yalniz BE'ye hic ulasmamis, yas limitini asmis pozisyonlar reduce-only MARKET
ile kapatilir; **tur basina en fazla 1 kapanis**
([[20-kararlar/D4-reaper]]).

### 5. Kayip cooldown'u

SL veya net negatif kapanista sembol `SCALPER_LOSS_COOLDOWN_MINUTES` sure
kilitlenir.

### `TRAIL_MARKET` / `BE_MARKET` (D22)

Koruyucu stop bir SEVIYE uretir; piyasa seviyeyi coktan gectiyse Binance
`-2021 Order would immediately trigger` doner ve `position_manager`
pozisyonu reduce-only MARKET ile kapatir. **Bu davranis D22'den ONCE de
vardi.** D22 yalnizca kapanisin deftere DOGRU etiketle ve GERCEK dolum
fiyatiyla yazilmasini sagladi. TRAIL ailesindendir, ayri sayilir.
Ayrinti: [[20-kararlar/D22-acil-kapanis-kaydi]].

## TUZAKLAR

- **"Bilinmiyor" asla "kapandi" sayilmaz.** Pozisyon sorgusu hata verirse o tur
  ATLANIR (`src/strategies/scalper/exits.py:237`).
- **`exit_reason=UNKNOWN` kayitlari guvenilmezdir** (restart sonrasi kurtarma);
  PnL'i `binance_income_net` ile dogrula.
- **Koruma-tarafi kapisi YALNIZ ayri market-data host'unda calisir.** Ayni
  host'ta kapi YOKTUR ve olmamalidir — hukmu borsa verir
  ([[20-kararlar/D22-acil-kapanis-kaydi]] "reddedilen on-kapanis").
- **Restart cikis zaman cizgisini kaybeder**: TP1/BE ani, trailing sayaci ve son
  trail stopu yalniz bellektedir; kapanis belgesi bunlari `null` birakir ve
  `exit.path.restart_gap=true` der — `0` yazmak uydurma olurdu (D21-R3).
- **Reaper turu basina 1 kapanis** siniri, 2026-08-14'te 5 escanli kapanisin
  safety turunu sisirip watchdog restart'i tetiklemesinden sonra kondu.
- **TP2'yi tamamen kaldirmak neredeyse esdegerdi** (+1551 vs +1608) — islemlerin
  azi TP2'yi goruyor ([[20-kararlar/D3-runner-payi]]).

## ILGILI

[[10-mimari/emir-yurutme]] · [[10-mimari/defter-ve-muhasebe]] ·
[[20-kararlar/D2-chandelier-carpani]] · [[20-kararlar/D12-tp1-8]] ·
[[30-deneyler/E9-yapi-kapisi]]
