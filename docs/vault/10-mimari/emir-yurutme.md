---
tags: [mimari, emir, boyutlama, executor]
guncelleme: 2026-08-24
kaynak: src/strategies/scalper/executor.py, src/strategies/scalper/setups.py, src/trading/position_manager.py, src/trading/binance_client_improved.py
---

# Emir yurutme — boyutlama, giris, koruma emirleri

## NE

Sinyal kapilardan gectikten sonra bu katman calisir: boyutlama → risk kapilari
→ giris emri (maker LIMIT GTX ya da MARKET) → **SL + TP1/TP2 algo emirleri** →
izlemeye alma. Fail-closed ilkesi: **SL kurulamazsa pozisyon acilmis sayilmaz**,
acil kapatilir ve giris kilidi (entry-halt) devreye girer.

## NEREDE

| Ne | Yer |
|---|---|
| Yurutucu | `src/strategies/scalper/executor.py:190` |
| Giris ana yolu | `src/strategies/scalper/executor.py:820` |
| Dolum sonrasi finalize (SL/TP kurulumu) | `src/strategies/scalper/executor.py:1468` |
| Maker LIMIT GTX girisi | `src/strategies/scalper/executor.py:1845` |
| Bekleyen emir takibi | `src/strategies/scalper/executor.py:2031` |
| Restart kurtarmasi (bekleyen) | `src/strategies/scalper/executor.py:2498` |
| Gecikme-duzeltmeli stop | `src/strategies/scalper/executor.py:1401` |
| Cooldown yazimi | `src/strategies/scalper/executor.py:630` |
| Kayip cooldown'u | `src/strategies/scalper/executor.py:702` |
| Stop politikasi (saf) | `src/strategies/scalper/setups.py:87` |
| ATR taban genisletmesi | `src/strategies/scalper/setups.py:55` |
| Guvenli pozisyon acma | `src/trading/position_manager.py:122` |
| Acil kapatma | `src/trading/position_manager.py:476` |
| SL degistirme (bosluksuz) | `src/trading/position_manager.py:654` |
| REST istemcisi | `src/trading/binance_client_improved.py:122` |
| Istek onceligi + agirlik | `src/trading/binance_client_improved.py:546` · `src/trading/binance_client_improved.py:253` |

## NASIL CALISIR

### Stop modlari (`SCALPER_STOP_MODE`)

| Mod | Mesafe |
|---|---|
| `structural` (kod varsayilani) | yapisal swing + ATR tabani (`SCALPER_STOP_ATR_FLOOR_MULT`) |
| `fixed_roi` (**canli**) | `SCALPER_FIXED_STOP_ROI_PCT / kaldirac`; likidasyon tamponu icin %70 tavanla kirpilir |

`Settings` startup'ta `fixed_roi` + `min_rr`/`min_stop_pct`/`max_stop_pct`
tutarsizligini **fail-fast reddeder** (`src/core/config.py:1084`).

### Boyutlama

Marj = kasanin `SCALPER_MAX_MARGIN_PCT`'i (varsayilan **%10** —
[[20-kararlar/karar-sinyal-oncelik]] geregi kucultulmez). Kaldirac
`SCALPER_DYNAMIC_LEVERAGE` acikken coin ATR'sine gore cozulur (3-20x bandi).
`min_rr` kapisi: beklenen harman ROI / (stop mesafesi × kaldirac) <
`SCALPER_MIN_RR` ise sinyal reddedilir (`0` = kapali).

### Giris modu

`SCALPER_ENTRY_MODE=taker` (varsayilan) MARKET; `maker` iki fazli LIMIT GTX
(post-only) — dolum `check_pending` ile izlenir, restart-guvenli journal
`state/scalper_pending.json` benzeri bir dosyada tutulur
(`scalper_pending_journal_path`).

### Koruma emirleri

STOP_MARKET / TAKE_PROFIT_MARKET emirleri **`/fapi/v1/algoOrder`** uzerinden
gider (2025-12-09'dan beri); yanitta `orderId` yerine `algoId` doner ve istemci
bunu takma adla esler (`src/trading/binance_client_improved.py:122` sinifi
icinde). SL/TP **sinyal fiyatindan degil GERCEK dolumdan** hesaplanir;
`_delay_adjusted_stop` stopu dolum kaymasina gore oteleyip giris–stop
MESAFESINI korur.

### REST oncelik sozlesmesi (D22)

`_request_with_retry(..., priority=...)` varsayilani **`critical`**'dir — bir
cagri yolu isaretlenmeyi unutursa guvenli tarafta kalir.
- **Kritik (daima gider):** emir, SL/TP, positionRisk koruma turu, kapanis
  dogrulamasi, gunluk risk income'i.
- **Kritik olmayan:** `/api/status` pano beslemesi, tarama turu, forensics
  post-mortem turu.

## TUZAKLAR

- **`openOrders` algo emirlerini GOSTERMEZ**, `allOpenOrders` onlari IPTAL
  ETMEZ. "Pozisyon korumasiz" alarmi cogu zaman bu yuzden yanlis okunur.
- **Ayni sembolde iki yonetici olamaz.** `src/trading/symbol_reservations.py:21`
  surec-ici rezervasyon defteri bunu engeller (scalper ↔ orchestrator ↔ gomulu
  takipci).
- **Cooldown asla kisaltilmaz** (`src/strategies/scalper/executor.py:630`):
  mevcut daha uzun bir cooldown yeni ve kisa bir cooldown'la ezilmez.
- **Golge modunda emir GITMEZ** ama boyutlama/kapilar aynen calisir; kayit
  `scalp_trades`'e `status="SHADOW"` olarak duser
  ([[20-kararlar/D14-golge-modu]]).
- **Testnet dolumlari iyimserdir.** Komisyon/kayma/funding mainnet'te gercek;
  `--fee-stress` olcumune bak ([[30-deneyler/E10-permutasyon]]).

## ILGILI

[[10-mimari/motor-scalper]] · [[10-mimari/cikis-yonetimi]] ·
[[10-mimari/guvenlik-kilitleri]] · [[20-kararlar/D8-stop-modu-fixed-roi]] ·
[[20-kararlar/D22-acil-kapanis-kaydi]]
