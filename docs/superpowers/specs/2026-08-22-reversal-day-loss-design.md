# Dönüş-günü kayıp paketi — tasarım (2026-08-22, kullanıcı onaylı)

## Sorun (kanıt: canlı defter 12–22 Ağu, `scripts/ledger_report.py`)
- Ödeme asimetrisi: TRAIL ort. **+%10.9 ROI**, SL ort. **−%48 ROI** → başabaş WR ≈ **%81.5**.
  Defter WR %70.7; yalnız UP rejim (%88.6) başabaşın üstünde. FLAT −41 (67 işlem),
  DOWN −128 (15 işlem).
- Boyut: `risk_amount = bakiye × SCALPER_RISK_PERCENTAGE(%10)`; `fixed_roi` stop'ta nominal
  tavan (`bakiye × kaldıraç × MAX_MARGIN_PCT`) her işlemde bağlayıcı → pozisyon = sermayenin
  %10'u, **her SL = sermayenin %5'i** (compounding ile büyür; 22 Ağu 4 SL = −%16).
- Giriş zamanlaması: 22 Ağu (DOWN günü, 3 günlük +%21 koşu sonrası) 4 LONG SL; hiçbiri stop
  sonrası 4 saatte girişe dönmedi → geniş stop çözüm değil. İlk iki kayıp (01:07, 04:30) yalnız
  çok-günlük uzamayla, son ikisi (08:26, 14:40) BTC gün-açılışı sapması (−1.33/−1.68%) ile
  işaretlenebilir.
- Yön: short azlığı tasarım (rejim kapısı UP'ta SHORT kapalı). C SHORT PF 1.84 (+143);
  **TV SHORT PF 0.15 (−55, 15 işlem)** zayıf halka.

## A — Risk katmanı (env; kod yok)
| Ayar | Eski | Yeni | Etki |
|---|---|---|---|
| `SCALPER_MAX_MARGIN_PCT` | 10 | 5 | SL = %2.5 sermaye; sinyal/PF değişmez (E6b negatif kontrol) |
| `SCALPER_DAILY_LOSS_LIMIT_PCT` | 10 | 6 | ~2.4 net SL sonrası gün kapanır (harness'ta YOK — koruma katmanı) |
| `SCALPER_TP1_ROI` | 10 | 8 | D12: 3 rejimde PF↑ DD↓ |
Uygulama: `backups/env.bak-<tarih>-riskpaketi` + `supervisorctl restart tradingbot_v2` + 240 sn
sağlık + config read-back. Soak: D6+A demet olarak (atıf bilinçli olarak ayrılmıyor).
**UYGULANDI 2026-08-23 02:56 UTC (D16) — A-plus biçimi: ek olarak `SCALPER_FIXED_STOP_ROI_PCT 50→40` (E6e kanıtı).**

## B — Hızlı rejim (env): `SCALPER_TF_REGIME=5m` — E6a: **RED** (AYI PF 1.01, BOĞA >%20 kayıp).

## C — Ters-gün kapısı (kod; motor + harness paritesi)
Ayarlar (varsayılan KAPALI): `SCALPER_MARKET_GATE=false`, `SCALPER_MARKET_GATE_SYMBOL=BTCUSDT`,
`SCALPER_MARKET_GATE_DAY_PCT=1.0` (lider gün açılışının ≥%X altındayken LONG yok / üstündeyken
SHORT yok), `SCALPER_MARKET_GATE_RUN_PCT=15`, `SCALPER_MARKET_GATE_RUN_DAYS=3` (lider N günde
≥%Y koştuysa o yöne yeni giriş yok). Her iki alt-kapı ayrı ayrı kapatılabilir (0 = kapalı).
- Motor: `engine.py` rejim kapısının yanında, tek giriş kapısında (C + TV aynı anda).
  Lider serisi (1d açılış + 5m son kapanış) önbellekli; veri yoksa **fail-open değil,
  fail-closed DEĞİL**: kapı yalnız veri doğrulanınca uygulanır, yoksa log + kapı atlanır
  (giriş hattı mevcut davranışı korur) — gerekçe: lider verisi eksikliği bir risk olayı değil.
- Harness: lider serisi pencere başında bir kez çekilir, `simulate_symbol`'a verilir; aynı saf
  fonksiyon (`market_gate.py`) iki tarafta kullanılır; parite testi + golden test değişmez
  (varsayılan kapalı).
- Ölçüm: 3 pencere × {gün-içi, uzama, ikisi}; tetik sayısı raporlanır (az tetik = zayıf kanıt).
  Karar: P2 kuralı. Sonra 3 mercek (regresyon/semantik/parite) + çürütme turu.
- TV SHORT: harness'ta TV yok; tek kanıt defter (15 işlem). Önce **gölge** önerisi; karar kullanıcının.

## Kapsam dışı
Mainnet; TV alarm değişikliği; stop modeli değişikliği (E3a kanıtı olumsuz; E6d/E6e ölçülüyor).
