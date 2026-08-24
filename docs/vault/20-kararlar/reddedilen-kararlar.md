---
tags: [karar, red, kayit]
guncelleme: 2026-08-24
kaynak: docs/DECISIONS.md "Reddedilen kararlar" (satir 2803)
---
# Reddedilen kararlar (kanitla)

> **Ayni fikri ikinci kez denemeden once bu tabloya bak.** Bu listede olan bir
> fikri "yeni bir fikir" gibi onermek zaman ve token israfidir.

| Fikir | Tarih | Sonuc | Neden reddedildi |
|---|---|---|---|
| Kaldirac tavani 50x | 08-19 | +9.4k → **−18k** | `fixed_roi`de kaldirac ↑ = stop mesafesi ↓ → gurultu stopu |
| TP1 %10→%15 | 08-21 | −3974, WR −10pp | SL'lerin 38/39'u %10'u gormeden oluyor |
| Stop ROI 50→30 | 08-21 | AYI −6108, SL 120→**224** | dar stop trail'e ulasacak islemleri kesiyor |
| Rejim TF 15m→4h | 08-21 | BOGA +2798→−63, YATAY −9090 | yavas rejim bogayi yok ediyor |
| RANGE'de C kapali | 08-21 | YATAY −7170 | yatay pencere RANGE'den ibaret degil |
| Flow-confirm filtresi | 08-21 | AYI 1.19 ✓, YATAY 0.81 ✗ | tek pencerede iyi |
| Divergence + flow_confirm (E2ab) | 08-21 | AYI 3.35 ama **31 islem**; BOGA 0.85; YATAY 0.78 | asiri filtreleme |
| Reversal-zone filtresi | 08-21 | AYI 0.68 | — |
| RSI 25/75 (siki esik) | 08-21 | notr | — |
| RSI 35/65 (gevsek, E4a) | 08-21 | AYI 0.79 / −5960 | **aktivite ≠ kar** (ikinci kez) |
| Strateji D, C+D | 08-21 | −660 / −4353 | C'yi zehirliyor |
| Chandelier 4.0 (E4c) | 08-21 | AYI 1.04, BOGA 2.33 | rejim-bagimli |
| TP2 %20 / %30 (E4d/E4e) | 08-21 | ±40 | etkisiz — islemlerin azi TP2'yi goruyor |
| Baglam TF 15m (E4k) | 08-21 | −1575 | — |
| TP1 8 + lev tavani 12/15 (E5a/E5b) | 08-21 | −1897 / −1643 | kaldirac kisiti bogayi olduruyor |
| Lev tavani 12 tek (E5c) | 08-21 | −347 (BOGA 52 islem) | orneklem yetersiz |
| TP1 8 + chandelier 3.0 (E5d) | 08-21 | +985 (< E4f tek +1058) | **toplamsal degil** |
| Rejim TF 15m→5m (E6a) | 08-23 | AYI 1.01, YATAY 0.99, BOGA −%50 | gercek RED |
| Danny ETH-15m LUCID recetesi | 08-21 | ort PF 1.12 (yalin S&O 1.60) | coklu onay + sabit TP kriptoda zarar |
| LuxAlgo AI / Discord LUCID (13 script) | 08-21 | en iyi 2.26; hepsi yalin OSC/S&O (2.2-2.5) altinda | — |
| AlgoPro V1.6 + yuksek kaldirac TP1 | 08-21 | TP1 kazanma ort **%40.5** (basabas %50) | beklenti −0.19R |
| Yapi (CHoCH/BOS) giris kapisi 5m (E9/S1) | 08-23 | AYI 0.85, BOGA **−%67** | C ters-trend; kar kaynagini yasakliyor |
| Yapi giris kapisi 15m (E9/S2) | 08-23 | en iyi AYI PF 1.00 | "en iyisi hicbir sey yapmamak"a yakinsiyor |
| Yapi CHoCH cikisi BE/market (E9/S3,S4) | 08-23 | WR %85→%48/%34 | SL 29→1 ama TRAIL kazananlari 182→29 |
| **D22 ilk hali — ayni host'ta ON-KAPANIS** | 08-23 | 12 ajan, **4 yuksek bulgu** | asagida |
| **D22 ilk hali — agirlik geri cekilmesi varsayilan ACIK** (2000/2300) | 08-23 | testnet medyani **2373** | esik medyanin ALTINDA → tarama kalici durur |
| Uzama alt-kapisi (`MARKET_GATE_RUN_PCT`) | 08-23 | V2a BOGA −%24.6 | iki bagimsiz olcum desteklemedi |

## D22'nin reddedilen on-kapanis tasarimi (kayit icin)

**Oneri:** chandelier seviyesini borsaya gondermeden once botun kendi canli
fiyat okumasiyla karsilastir; "yanlis taraftaysa" emri hic gonderme ve
pozisyonu kendiliginden reduce-only MARKET ile kapat.

**4 yuksek bulgu:**
1. **Yetki genislemesi** — "piyasa emriyle kapat" karari bugune kadar yalniz
   BORSA (`-2021`), reaper ya da operator (`flatten`) verebiliyordu.
2. **Bayat/yanlis fiyat riski** — tazelik DOGRULUK degildir; tek hatali ticker
   okumasi karli bir kosucuyu piyasadan cikarabilirdi.
3. **Cift emir / `-2022` yarisi.**
4. **Kazanc yok** — kapanisi engellemiyordu; kayit kusuru on-kapanis OLMADAN da
   duzeltilebilirdi (nitekim oyle yapildi).

ILGILI: [[20-kararlar/00-karar-indeksi]] · [[20-kararlar/D22-acil-kapanis-kaydi]] · [[20-kararlar/D18-yapi-kapisi]] · [[30-deneyler/00-deney-indeksi]]
