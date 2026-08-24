---
tags: [deneyler, indeks]
guncelleme: 2026-08-24
kaynak: docs/EXPERIMENTS.md (1287 satir)
---
# Deney indeksi (E-serisi)

Kaynak: `docs/EXPERIMENTS.md`. Bir satirin **kanit sayilmasi icin
komut + pencere + env kaynagi + log yolu** gerekir.

> ⚠️ **ONCE BUNU OKU:** [[30-deneyler/00-metodoloji-uyarisi]] —
> bu defterdeki 36 varyantin TAMAMI ayni uc pencerede olculdu.

## Seriler

| Seri | Konu | Not |
|---|---|---|
| — | [[30-deneyler/rejim-referanslari\|Rejim referanslari (taban)]] | basabas WR ≈ %85.4 |
| E2/E3 | [[30-deneyler/E2-E3-varyantlari\|Tek-degisken varyantlar]] | D6 buradan cikti |
| E4 | [[30-deneyler/E4-autoresearch\|Autoresearch turlari]] | E4b/E4f/E4g ADAY |
| E5/E6 | [[30-deneyler/E5-E6-risk-paketi\|Kaldirac/risk paketi]] | E6e P2'yi gecti, D16 geri alindi |
| E7 | [[30-deneyler/E7-lider-kapisi\|Lider piyasa kapisi]] | D15'in kaniti |
| E8 | [[30-deneyler/E8-sinyal-otopsisi\|Sinyal otopsisi (202 islem)]] | E8.0 kritik veri bulgusu |
| E9 | [[30-deneyler/E9-yapi-kapisi\|Piyasa yapisi kapisi]] | 7/7 varyant RED |
| E10 | [[30-deneyler/E10-permutasyon\|Permutasyon testi]] | kenar sanstan ayirt edilebiliyor |
| D24 | [[30-deneyler/D24-olcumleri\|Olcum paketi olcumleri]] | bar-bazli cokus, kelepce |
| — | [[30-deneyler/altin-backtest\|Altin (golden) backtest]] | deterministik regresyon kilidi |
| — | [[30-deneyler/canli-defter-rejim-analizi\|Canli defter rejim analizi]] | rejim bagimliligi |
| — | [[30-deneyler/tradingview-olcumleri\|TradingView / LuxAlgo olcumleri]] | D7'nin kaniti |

## Sabit pencereler

| Pencere | Tarih |
|---|---|
| AYI | 2026-01-23 → 02-13 (BTC −%30) |
| YATAY | 2026-07-01 → 07-21 |
| BOGA | 2026-08-07 → 08-21 |

## Otomatik dongu

`scripts/autoresearch.py` — bir degisiklik oner → sabit degerlendirmeyle kos →
tut/at → logla. **Sunucuya ASLA dokunmaz** (ssh/scp yok), `.env` YAZMAZ,
deploy ETMEZ, motor koduna DOKUNMAZ. Yalnizca ONERI uretir.
Ayrinti: `docs/AUTORESEARCH.md`.

ILGILI: [[20-kararlar/00-karar-indeksi]] · [[20-kararlar/P2-karar-kurali]] · [[90-ai-icin/dogrulama-receteleri]]
