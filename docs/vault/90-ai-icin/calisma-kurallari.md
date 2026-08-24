---
tags: [ai-icin, kurallar, zorunlu]
guncelleme: 2026-08-24
kaynak: CLAUDE.md, AGENTS.md, docs/DECISIONS.md P1/P2, docs/MAINNET_PLAN.md
---
# AI icin calisma kurallari (zorunlu)

## 0. Model ve efor (KULLANICI KARARI, pazarliga kapali)

Bu proje **gercek parayla** ilgilidir. Ucuz/hizli model ile "idare etme"
**YASAK**.
- Ana oturum: **en yuksek model + en yuksek efor**.
- Alt ajanlar: **Sonnet KULLANILMAZ** (`model: opus`/`fable`, `effort: max`).
  Haiku yalniz gercekten mekanik salt-okuma islerinde; strateji/kod/analiz/
  incelemede **ASLA**.
- Token maliyeti gerekce degildir: yanlis bir parametre, tasarruf edilen her
  token'dan pahalidir.

## 1. Kanitsiz parametre degisikligi YASAK

Her `SCALPER_*` degisikligi:
```
3 rejim penceresinde backtest (P2)  →  testnet soak ≥5 gun (≥1 dusus gunu)
   →  insan onayi  →  mainnet
```
Karar kurali: **AYI PF ≥ 1.1** (veya AYI+YATAY birlikte ↑) **VE BOGA PnL kaybi
≤ %20**, her pencerede **≥60 islem**.
Ayrinti: [[20-kararlar/P2-karar-kurali]].

## 2. Harness = canli motor (parite)

Motorda bir kapi/filtre degisirse harness da degisir **ve parite testi
guncellenir**. Ayrinti: [[20-kararlar/P1-harness-parite]].

## 3. Backtest DAIMA sunucu env'iyle

```bash
env $(ssh awa grep ^SCALPER_ /opt/tradingbot-v2/.env | xargs) python3 -m src.strategies.scalper.backtest ...
```
Yerel `.env` **bayattir**. Kosulari **sirali** yap (paralel = Binance 429).

## 4. Sinyal-oncelik kurali (kullanici karari)

Boyut/TP1/stop ile kayip kucultmek **YASAK**. %10/islem ve yuksek TP1 korunur.
Cozum **sinyal kalitesidir**. Ayrinti: [[20-kararlar/karar-sinyal-oncelik]].

## 5. Dusmanca inceleme zorunlulugu

Motor degisikligi, backtest yorumu, risk karari: **3+ mercekli inceleme +
curutme turu**. Bu repoda her buyuk karar boyle olgunlasti:
D10 (6 kusur) · D19a (24 kusur) · D20a (19 ajan) · D22 (4 yuksek bulgu ile
REDDEDILDI ve daraltildi).

## 6. Belgeleme sozlesmesi

Canliya giren **her** degisiklik AYNI commit'te `docs/DECISIONS.md`'ye
islenir: **ne / neden / kanit / geri alma**. Gerekiyorsa `CLAUDE.md` ve bu kasa
guncellenir. Hafiza dosyalari yalniz **isaretcidir**.

## 7. Durustluk sozlesmesi

- Dogrulayamadigin seyi **"kodda dogrulanamadi"** diye yaz. Uydurma yok.
- `null` = **"olculmedi"**; `0` yazmak uydurmadir.
- "Bilinmiyor" asla "kapandi"/"TP" diye maskelenmez.
- Bir sonucu **log/rapor yolu olmadan** kanit sayma.
- "Calisiyor" demeden once restart'i **kanitla**
  ([[90-ai-icin/dogrulama-receteleri]]).

## 8. Once bak, sonra one sur

Surpriz bir sey gorursen sirasiyla:
1. `docs/DECISIONS.md` — **denenmis mi?**
   ([[20-kararlar/reddedilen-kararlar]])
2. `docs/RUNBOOK.md` tuzaklari
3. kodun kendisi

## 9. Testler

```bash
cp env.example .env      # .env yoksa (CI de boyle yapar)
python3 -m pytest tests -q
```
Beklenen: **2251 passed, 2 skipped**. Paket **yesil kalmalidir**.

## 10. Guvenlik

Secret'lar yalniz `.env`'de; log/cikti/commit'e **asla**.
`logs/supervisor.log` erisim logu secret icerir — **dokme**
([[50-veri/loglar]]).

ILGILI: [[00-BASLA-BURADAN]] · [[90-ai-icin/sik-yapilan-hatalar]] · [[30-deneyler/00-metodoloji-uyarisi]] · [[20-kararlar/mainnet-plani]]
