---
tags: [deneyler, E8, otopsi, sinyal-kalitesi]
guncelleme: 2026-08-24
kaynak: docs/EXPERIMENTS.md "Sinyal otopsisi (E8)" (satir 321)
---
# E8 — Sinyal otopsisi: hangi girisler yanlisti, giris aninda nasil bilinebilirdi?

Salt-okunur analiz (**kod DEGISTIRILMEDI**). Canli defterin **202 kapanmis
islemi**, giris damgasindan ONCE kapanmis mumlardan turetilen 40+ ozellikle
zenginlestirildi.

## E8.0 — EN KRITIK BULGU: veri kaynagi ayrismasi

**Canli motor TESTNET, harness MAINNET mumu okuyor.**
- Motorun yazdigi giris RSI'i 143 C isleminde **testnet** 1m ile uyusuyor
  (medyan |Δ| **2.8**), mainnet ile uyusmuyor (**7.4**).
- `vol_ratio_5m` iki taraf arasinda **HIC tasinmiyor** (Pearson r = **−0.04**;
  testnet ort. 206 vs mainnet 1.73).
- Testnet 1m mumlari neredeyse duragan; fiyat SEVIYESI yakin (medyan sapma
  **%0.054**) ve makro ozellikler yuksek korelasyonlu (RSI 15m r=0.975).
- **Ama C bir UC esiginde karar verir** (RSI ≤25 / ≥75); orada 2.8 puanlik
  medyan fark sinyali dogrudan cevirir.

→ [[20-kararlar/D17-ayri-market-data-host]] bu bulgudan dogdu.

## E8.3 — En ayirt edici ozellikler (SL vs kalanlar, n=54 SL)

| Ozellik | SL ort | SL-olmayan ort | AUC | p |
|---|---|---|---|---|
| `align_btc_run_3d` | 2.28 | 7.50 | 0.292 | <0.001 |
| `btc_chg_24h` | 0.86 | 3.77 | 0.299 | <0.001 |
| `sym_run_3d` | −1.11 | 10.49 | 0.312 | <0.001 |
| `atr_pctile_30d` | 41.1 | 62.8 | 0.322 | <0.001 |
| `rsi_extremity_15m` | 2.79 | −4.58 | 0.644 | <0.001 |

**SHORT tarafinda giris aninda olculen HICBIR ozellik anlamli ayirmiyor** —
orada kayip **ongorulemiyor**; SHORT kurali ayrim degil **odeme asimetrisi**
uzerinden calisir.

**C-LONG icin baglam-TF RSI'i tek basina MONOTON** (eşik 40'ta: engellenen
38 C-LONG / 13 SL / −117.5; kalan 63 islem +490.9 / PF 4.42). Monotonluk
esik-uydurmasina karsi **en guclu kanittir**.

## E8.4 — Iki kayip arketipi

- **(A) Dusen bicak** — islem yonu UST zaman dilimine karsi (LONG'da
  RSI<50 ve fiyat 15m EMA50 altinda). Kapatilabilir.
- **(B) Tepe kovalama** — islem yonunde ASIRI uzama.
  **(B) KAPATILAMIYOR**: ayni yonde uzama filtreleri defterde net NEGATIF
  (`align_dist_ema50_atr > 2.0` engelle → **−559.3**). *Uzama trend
  surusunun ta kendisidir; (B) kayiplari trendden kazanmanin bedelidir.*

## E8.5 — Tek-esikli kapi simulasyonu (defter uzerinde)

| Kural | Δ mainnet | Δ guvenilir | ci90 | PF |
|---|---|---|---|---|
| `RSI(5m) < 40` → C-LONG yok | +179.7 | +112.4 | [+4.7, +393.8] | 2.11→3.15 |
| `RSI(15m) < 50` → C-LONG yok | +176.3 | +103.5 | [−14.8, +422.6] | 2.11→3.32 |
| `fiyat < EMA50(15m) − 1·ATR` → C-LONG yok | +124.0 | +76.8 | [−12.9, +298.6] | 2.11→2.72 |
| `ATR persentili < 40` → SHORT yok | +110.3 | +90.3 | [−1.2, +235.0] | 2.11→2.63 |

**Reddedilenler (defterde net negatif):** `align_btc_run_3d > 15` (−152.7),
`cluster_60m_same_dir > 1` (−509.2 — kumelenme trendin kendisi),
gun/saat kaliplari (**rejim artefakti, kural yapilmamali**).

## E8.6 — ⚠️ POST-HOC sayilari GECERSIZ

E8.6'daki gun-kapisi sayilari motor-ici kapiyla yeniden olculdu
([[30-deneyler/E7-lider-kapisi]]) ve **gecersiz kilindi**: YATAY %1.0'da
**isaret bile degisti** (post-hoc −487.3 → motor-ici **+201**), cunku
engellenen sinyal motor-ici kapida kapasiteyi serbest birakiyor ve bosalan
slota sonraki sinyal giriyor. **Karar icin E7'nin sayilari kullanilmalidir.**

## E8.7 — TV SHORT kaynak kalitesi

15 islem, **PF 0.15**. [[20-kararlar/D23-ai-kapisi]]'nin gerekcelerinden biri.

ILGILI: [[20-kararlar/D17-ayri-market-data-host]] · [[30-deneyler/E7-lider-kapisi]] · [[10-mimari/gozlem-katmanlari]] · [[30-deneyler/00-metodoloji-uyarisi]]
