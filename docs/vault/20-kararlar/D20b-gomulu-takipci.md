---
tags: [karar, canli, follower, algopro, sanal-defter]
guncelleme: 2026-08-24
kaynak: docs/DECISIONS.md D20b (satir 995), docs/RUNBOOK.md "Gomulu takipciyi acma"
---
# D20b — GOMULU AlgoPro takipcisi (`FOLLOWER_EMBEDDED`) · CANLI (testnet) · kanit: **YOK**

**Kullanici karari (baglayici, 2026-08-23):** *"Yeni hesap yok, yeni panel yok."*
Takipci AYNI testnet hesabinda, AYNI surecte (`tradingbot_v2`, :9091), AYNI
panoda ve **1000 USD'lik SANAL defterle** calisir.

## Yedi parca (ozet)

1. **Ayni surec**: `BOT_MODE=scalper` + `FOLLOWER_EMBEDDED=true` → lifespan
   scalper'in YANINDA `FollowerEngine` baslatir (`src/main.py:390`).
2. **Sanal defter**: equity = `FOLLOWER_VIRTUAL_CAPITAL_USDT` +
   `scalp_trades` AP net PnL.
3. **Surec-ici teslim**: AlgoPro govdesi HTTP koprusu yerine dogrudan
   (`src/main.py:1277`) ve ana botun saglamasina **oy VERMEZ**.
4. Sembol cakismasi surec-ici `src/trading/symbol_reservations.py:21` ile
   engellenir.
5. `FOLLOWER_SYMBOLS` scalper evreninden **otomatik dusulur**.
6. `/risk-event` **iki motoru da** kapsar.
7. Defter ayni `tradingbot.db`, `strategy="AP"`.

## Iki kapi gomulu modda DEGISTI (ayri halkada aynen korunur)

- **"yetim = entry-halt" KOSULLU**: hesap paylasildigi icin hicbir motorun
  rezerve etmedigi pozisyon MESRU olabilir (elle acilmis) → WARNING + sayac.
  Takipcinin KENDI rezervasyonunu tasiyan yetim → CRITICAL + kalici entry-halt.
- **Kapasite tavani motor-BASINA** (D20a'da hesap geneliydi).

**Canli durum (2026-08-24 00:45 UTC).** `FOLLOWER_SYMBOLS=TUTUSDT,ZECUSDT`,
`FOLLOWER_MAX_POSITIONS=1`, `FOLLOWER_DAILY_LOSS_LIMIT_PCT=10`.
Ilk AP islemi (#211 TUTUSDT SHORT) **+2.07 USDT** net — Binance income ile
birebir dogrulandi; sanal defter 1002.07.
**Kanit.** YOK — testnet olcumu kanit olacak.
**Geri alma (SIRA ONEMLI).** (1) AP pozisyonlarini KAPAT (bayrak hala
ACIKKEN), (2) defterde acik AP satiri kalmadigini dogrula, (3) bayragi kapat +
korumali restart.
**Mainnet.** CIKAMAZ.

> ⚠️ **Sembol secimi kodda SABIT DEGILDIR**, yalniz `.env`'e yazilir.
> TUT/ZEC secimi olcumle belirlendi; yeni bir sembolu **olcmeden** eklemek
> [[90-ai-icin/sik-yapilan-hatalar]] listesindedir.

ILGILI: [[10-mimari/takipci-algopro]] · [[20-kararlar/D20a-takipci-duzeltmeleri]] · [[40-isletme/halka-yonetimi]]
