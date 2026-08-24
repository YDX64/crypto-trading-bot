---
tags: [kararlar, indeks]
guncelleme: 2026-08-24
kaynak: docs/DECISIONS.md (2883 satir)
---

# Karar indeksi (ADR)

Kaynak: `docs/DECISIONS.md`. Bu notlar **ozet + isaretcidir**; celiskide
`docs/DECISIONS.md` baglayicidir. Her notta karar / gerekce / kanit / durum /
geri alma tek satir bulunur.

## Durum lejandi

| Durum | Anlami |
|---|---|
| **AKTIF** | canlida (testnet) uygulanan |
| **ADAY** | backtest gecti, canliya alinmadi |
| **GOLGE** | kod canlida ama davranisi degistirmiyor |
| **RED** | kanit reddetti |
| **GERI ALINDI** | uygulandi, sonra geri alindi |
| **ARASTIRMA** | henuz bir hukum yok |

## Aktif kararlar

| # | Karar | Durum |
|---|---|---|
| D1 | [[20-kararlar/D1-yalniz-strateji-c\|Yalniz strateji C]] | AKTIF |
| D2 | [[20-kararlar/D2-chandelier-carpani\|Chandelier ATR 2.5→3.5]] | AKTIF |
| D3 | [[20-kararlar/D3-runner-payi\|Runner payi %40]] | AKTIF |
| D4 | [[20-kararlar/D4-reaper\|Reaper 8 saat, trailing muaf]] | AKTIF |
| D5 | [[20-kararlar/D5-rejim-kapisi\|Rejim kapisi]] | AKTIF |
| D6 | [[20-kararlar/D6-diverjans-sarti\|C diverjans sarti]] | AKTIF |
| D7 | [[20-kararlar/D7-tv-sembol-allowlist\|TV sembol allowlist]] | AKTIF |
| D8 | [[20-kararlar/D8-stop-modu-fixed-roi\|fixed_roi stop + dinamik kaldirac]] | AKTIF |
| D9 | [[20-kararlar/D9-webhook-sertlestirme\|Webhook sertlestirme]] | AKTIF |
| D10 | [[20-kararlar/D10-risk-olayi-kanali\|Risk-olayi kanali]] | AKTIF |
| D14 | [[20-kararlar/D14-golge-modu\|Golge modu]] | AKTIF |
| D15 | [[20-kararlar/D15-lider-kapisi\|Lider piyasa kapisi]] | AKTIF (testnet) |
| D20 | [[20-kararlar/D20-takipci-halkasi\|AlgoPro takipci halkasi]] | AKTIF |
| D20a | [[20-kararlar/D20a-takipci-duzeltmeleri\|Takipci duzeltmeleri]] | AKTIF |
| D20b | [[20-kararlar/D20b-gomulu-takipci\|Gomulu takipci]] | CANLI |
| D21 | [[20-kararlar/D21-islem-adli-kaydi\|Islem adli kaydi]] | AKTIF (gozlem) |
| D22 | [[20-kararlar/D22-acil-kapanis-kaydi\|-2021 kaydi + agirlik telemetrisi]] | AKTIF (daraltilmis) |
| D24 | [[20-kararlar/D24-olcum-paketi\|Olcum/kanit paketi]] | AKTIF (yalniz olcum) |
| D25 | [[20-kararlar/D25-container-yolu\|Tek container dagitim yolu]] | AKTIF (ek yol) |
| D26 | [[20-kararlar/D26-golge-halkasi\|Golge halkasi + orchestrator kapisi]] | AKTIF |
| D27 | [[20-kararlar/D27-olcum-borcu-karsi-olgu\|Olcum borcu + karsi-olgu defteri]] | AKTIF (yalniz olcum) |

## Aday / golge / arastirma

| # | Karar | Durum |
|---|---|---|
| D11 | [[20-kararlar/D11-chandelier-3-0\|Chandelier 3.0]] | ADAY, uygulanmadi |
| D12 | [[20-kararlar/D12-tp1-8\|TP1 %10→%8]] | ADAY (en guclu), uygulanmadi |
| D13 | [[20-kararlar/D13-kaldirac-tavani\|Kaldirac tavani bulgusu]] | ARASTIRMA |
| D17 | [[20-kararlar/D17-ayri-market-data-host\|Ayri market-data host]] | ADAY, testnet'te acildi |
| D19 | [[20-kararlar/D19-tv-olay-kanali\|TV olay kanali]] | GOLGE |
| D19a | [[20-kararlar/D19a-tv-olay-duzeltmeleri\|D19 duzeltmeleri (24 bulgu)]] | GOLGE |
| D23 | [[20-kararlar/D23-ai-kapisi\|AI karar katmani]] | GOLGE (`off`) |

## Reddedilen / geri alinan

| # | Karar | Durum |
|---|---|---|
| D16 | [[20-kararlar/D16-a-plus-risk-paketi\|A-plus risk paketi]] | **GERI ALINDI** |
| D18 | [[20-kararlar/D18-yapi-kapisi\|Piyasa yapisi (CHoCH/BOS) kapisi]] | **RED** (kanit) |
| — | [[20-kararlar/reddedilen-kararlar\|Reddedilen kararlar tablosu]] | kayit |

## Metodoloji ve baglayici kullanici kararlari

| # | Karar |
|---|---|
| P1 | [[20-kararlar/P1-harness-parite\|Harness = canli motor]] |
| P2 | [[20-kararlar/P2-karar-kurali\|Karar kurali (3 pencere)]] |
| P3 | [[20-kararlar/P3-simulator-olcegi\|Simulator olcegi]] |
| — | [[20-kararlar/karar-sinyal-oncelik\|Sinyal-oncelik kurali (kullanici)]] |
| — | [[20-kararlar/mainnet-plani\|Mainnet plani ve terfi olcutleri]] |

## ILGILI

[[00-BASLA-BURADAN]] · [[30-deneyler/00-deney-indeksi]] ·
[[90-ai-icin/calisma-kurallari]]
