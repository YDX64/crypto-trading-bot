# Autoresearch — otomatik parametre arastirma dongusu

Karpathy tarzi dongu: **bir degisiklik oner -> sabit degerlendirmeyle kos ->
tut/at -> logla**. Strateji C (scalper) parametreleri icindir. Kod:
`scripts/autoresearch.py`, adaylar: `scripts/autoresearch_candidates.json`.

## Ne yapar

1. `--env-file`'daki taban env'i (`SCALPER_*`) okur, ustune tek-degisken
   override uygular (aday listesindeki her satir).
2. Her varyanti 3 sabit pencerede (AYI/YATAY/BOGA, bkz. `docs/DECISIONS.md`
   #P2) SIRALI (`subprocess`, paralel degil — Binance 429) calistirir:
   `python3 -m src.strategies.scalper.backtest --strategies C ...`.
3. `docs/DECISIONS.md` #P2 karar kuralini uygular: AYI PF>=1.1 (veya AYI ve
   YATAY PnL birlikte iyilesir) VE BOGA PnL kaybi <=%20; her pencerede
   >=60 islem yoksa "asiri filtreleme" ile reddedilir.
4. Sonuclari `docs/EXPERIMENTS.md`'ye tarihli bir "Autoresearch" bolumune
   ekler, ham loglari `logs/autoresearch/<tarih>/` altina yazar,
   `summary.json` uretir.

## Nasil calistirilir

```bash
# 1) Taban env'i sunucudan cek (KULLANICI calistirir — script ASLA ssh cagirmaz)
ssh awa grep ^SCALPER_ /opt/tradingbot-v2/.env > scalper_env.txt

# 2) Plani gor (hicbir backtest kosmaz)
python3 scripts/autoresearch.py --env-file scalper_env.txt --dry-run

# 3) Ilk 3 adayi kos
python3 scripts/autoresearch.py --env-file scalper_env.txt --limit 3

# Belirli adaylari kos
python3 scripts/autoresearch.py --env-file scalper_env.txt --only E4a,E4b

# Yarim kalan kosumu devam ettir — daha once basariyla biten adaylar atlanir
python3 scripts/autoresearch.py --env-file scalper_env.txt
```

## Ne yapmaz (kasitli sinir)

- Sunucuya ASLA dokunmaz: ssh/scp yok.
- `.env` dosyasini ASLA yazmaz/degistirmez — yalnizca `--env-file`'i OKUR.
- ASLA deploy etmez, ASLA sureci yeniden baslatmaz (`supervisorctl` yok).
- `src/strategies/scalper/backtest.py` ve motor koduna DOKUNMAZ.
- Yalnizca commit'lenmis koda (`src/` temiz) karsi degerlendirir; kirliyse
  reddeder.
- Sonuc uretmez, yalniz ONERI: bir ADAY canliya gecmeden once insan karari ve
  testnet soak gerekir.

## ADAY -> canli gecis (insan karari)

`docs/CLAUDE.md` "Yasaklar #1": backtest'te ADAY olan bir varyant once
**testnet'te >=5 gun** (en az 1 dusus gunu dahil) izlenir, canli defter
kanit sayilir; ancak o zaman `.env`'e elle islenip `docs/DECISIONS.md`'ye
kanitla birlikte kaydedilir. Bu script hicbir asamada bunu otomatiklestirmez.
