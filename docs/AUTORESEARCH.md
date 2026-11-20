
# Autoresearch — otomatik parametre arastirma dongusu

Karpathy tarzi dongu: **bir degisiklik oner -> sabit degerlendirmeyle kos ->
tut/at -> logla**. Strateji C (scalper) parametreleri icindir. Kod:
`scripts/autoresearch.py`, adaylar: `scripts/autoresearch_candidates.json`.
Olcum modeli ve kabul kurali D41'de (`docs/DECISIONS.md`) degisti; asagisi
2026-11-20 davranisidir.

## Ne yapar

1. `--env-file`'daki taban env'i (`SCALPER_*`) okur, ustune tek-degisken
   override uygular (aday listesindeki her satir). `--env-file` ZORUNLUDUR ve
   en az bir `SCALPER_` anahtari icermelidir; yoksa/bossa script 1 ile cikar
   (`--dry-run` dahil). Yerel `.env`'e veya ust surecin ortamina GUVENILMEZ.
2. Her varyanti 3 sabit pencerede (AYI/YATAY/BOGA, bkz. `docs/DECISIONS.md`
   #P2) SIRALI (`subprocess`, paralel degil — Binance 429) calistirir:
   `python3 -m src.strategies.scalper.backtest --strategies C ...`.
   Backtest GERCEKCI modelle (D41, varsayilan) kosar; `--legacy-model`
   autoresearch tarafindan GECIRILMEZ (resmi sayi gerceklidir).
3. **P2-v2** karar kuralini uygular (asagida). Eski P2 karari de hesaplanir ve
   yan yana basilir (`P2-eski: ...`; `summary.json` alanlari `old_decision`,
   `old_reason`, `old_score`; `EXPERIMENTS.md` satirinda ` / P2-eski: X`).
4. Sonuclari `docs/EXPERIMENTS.md`'ye tarihli bir "Autoresearch" bolumune
   ekler (her yeni kosulan aday icin bir kez, son kararla), ham loglari
   `logs/autoresearch/<tarih>/` altina yazar, `summary.json` uretir.

## Sonuc onbellegi: TAM anahtar

Eski davranis ayni gun icinde taban/aday sonuclarini commit, env veya override
degisse de yeniden kullaniyordu; artik YAPMAZ. Her taban/aday kaydi bir anahtar
tasir: sha256( git HEAD + kirli-agac parmak izi (`git diff HEAD -- src scripts`
+ izlenmeyen `src`/`scripts` dosyalari) + env dosyasi icerik ozeti + siralanmis
override'lar + WINDOWS + backtest bayraklari + fonlama onbellegi ozeti
(`data/funding_cache/*.json`) + taban kaynagi (kosu / `--baseline-json` ozeti) ).
Yalniz TAM esleşen anahtar yeniden kullanilir; eslesmeyen veya anahtarsiz (eski)
kayit YENIDEN kosulur. Islem serisi (`trade_pnls`) olmayan eski kayitlar da
yeniden kosulur. Devam ettirme (resume) bu kurala tabidir.

## Izole ortam

Alt surec ortami: izin listesi (PATH, HOME, LANG, PYTHONPATH, VIRTUAL_ENV, SSL ve
proxy degiskenleri) + env dosyasi + aday override'lari + `TRADINGBOT_LOG_DIR` +
zararsiz sahte degerler (Binance/Telegram/AI/JWT zorunlu alanlari). Ust surecin
`SCALPER_*` ve gercek gizlileri GECMEZ; yerel `.env` (cwd'deki) calistirma
oncesi devre disi birakilir (`config.py`'ye dokunmadan). Rapor, backtest'in
yazdigi "Backtest raporu yazildi: <yol>" satirindan alinir (en yeni dosyadan
degil). `profit_factor=null` (sonsuz PF) `+inf` okunur.

## P2-v2 karar kurali

Aday ancak BESI de saglanirsa **ADAY**:

1. **Ornek:** her 3 pencerede taban VE aday >= 60 islem; degilse `YETERSIZ-ORNEK`
   (reddedilmis sayilir ama etiketi ayridir).
2. **Anlamlilik:** havuzlanmis gunluk PnL farkinda (aday - taban; 3 pencerenin
   55 gunu) eslesmis gun-blok bootstrap (blok 3, 5000 yol, tohum 12345);
   `delta > 0` VE tum sweep adaylari uzerinden Benjamini-Hochberg `q <= 0.10`
   (yetersiz-ornekli adaylar da ailede sayilir).
3. **Pencere zarari yok:** hicbir pencerede aday tabandan `0.20 x scale_w`'den
   fazla geride degil; `scale_w = max(|taban net|, 0.25 x (brut kar + |brut zarar|))`.
4. **Drawdown:** havuz DD (pencere bar-DD toplami; yoksa islem duzeyi) <= 1.2 x taban.
5. **Stres:** `--fee-stress` (maliyet 2x) VE `--entry-delay-candles 1`
   kosularinda havuz net farki (aday - ayni stresli taban) > 0; eksik sonuc =
   dusurulur (fail-closed).

Etiketler: `ADAY`, `REDDEDILDI`, `YETERSIZ-ORNEK`, `HATA` (islem serisi yok).
Siralama puani `delta / bootstrap SE`. Akis: A) adaylar kosar, B) BH tum sweep
uzerinden + sart 1-4, C) yalniz gecenler icin stres kosulari (taban stresi bir
kez), D) kararlar yazilir. Stres maliyeti: taban icin 2 x 3 kosu + her ADAY icin
2 x 3 kosu (kosu basina ~dakikalar). `--baseline-json` islem serisi
(`trades[].exit_time/pnl`) icermiyorsa her aday `HATA` olur.

Eski P2 (AYI PF>=1.1 veya AYI+YATAY birlikte iyilesir, BOGA kaybi <=%20)
yalnizca "P2-eski" olarak basilir ve KARAR VERMEZ.

## Saklı pencere kaydi (`docs/holdout_registry.json`)

Her kosu, WINDOWS'taki pencerelerin her birini kayda karsi kontrol eder
(`--registry` ile baska dosya; varsayilan `docs/holdout_registry.json`):

- **burned (yanik):** secim kumesi; kullanim serbest ama kullanim sayilir
  (gercek kosuda `opens`'a yazilir, `--dry-run` yazmaz).
- **validation:** walk-forward OOS katina izin; parametre secimi YAPILMAZ.
- **sealed (muhurlu):** `--open-sealed "<amac>"` OLMADAN reddedilir (cikis 2,
  hicbir backtest baslamaz); acilis `{at, by, purpose}` olarak `opens`'a yazilir.
  Muhurlu pencere yalniz VETO eder, terfi ettirmez; aday paketi basina BIR kez.
- Kayit yoksa/okunamiyorsa kosu REDDEDILIR (fail-closed). Kayitta `end`
  KAPSAYICIDIR (backtest pencereleri `[start,end)`). Rapor, dokunulan
  pencereleri acilis sayilariyla ve kayit ozetiyle basar.
- Su an: AYI/YATAY/BOGA/OOS_MART/COKUS/HOLD_MAY/HOLD_JUN/FRESH_SUBAT/
  FRESH_TEMMUZ/SAKLI_NISAN **yanik**; `VAL-2026Q4a` 2026-09-04..2026-10-15
  **validation**; `SEAL-2026Q4b` 2026-10-16..2026-11-19 **sealed**. Bu iki yeni
  pencere icin kline onbellegi YOKTUR; once normal bir backtest ile veri
  cekilmeden walk-forward kosamaz.

`scripts/run_windows.sh` ayni kayda uyar: `OPEN_SEALED="<amac>"` ve opsiyonel
`HOLDOUT_REGISTRY` ortam degiskenleri; muhurlu pencereye degen kosu 2 ile cikar.

## Walk-forward (`scripts/walk_forward.py`)

Parametre secimini ORNEKLEM-DISI katlarla sinar. Ayni `--env-file` ve aday
listesi; yalniz KLINE ONBELLEGIYLE kosar (ag YOK): eksik seri varsa cikis 3 ve
eksik liste; tam dosya yok ama kapsayan dosya varsa tam-anahtarli dosyayi dilimleyerek yazar.

```bash
python3 scripts/walk_forward.py --env-file scalper_env.txt \
  --start 2026-01-23 --end 2026-09-03 \
  --train-days 21 --test-days 7 [--step-days 7] [--mode rolling|anchored] \
  [--embargo-hours 24] [--candidates yol.json] [--symbols ...] [--jobs 4] \
  [--out-dir logs/walk_forward/<zaman>] [--registry yol] [--open-sealed "<amac>"]

# yalniz saklı pencere denetimi (kosu yok)
python3 scripts/walk_forward.py registry-check --start 2026-10-16 --end 2026-11-20
```

- Her katta taban ve her aday EGITIMDE kosar; aday ancak egitimde >= 60 islem ve
  eslesmis puan (`delta/SE`, blok 3, 5000 yol) >= 1.0 ise secilebilir; en yuksek
  puan secilir, yoksa TABAN. Secilen ve taban DONDURULMUS parametreyle TEST'te kosar.
- `--step-days < --test-days` reddedilir (OOS gunleri cift sayilirdi). `--embargo-hours`
  tam gune yukari yuvarlanir (pencereler tarih granulerlidir). Pencereler bagimsiz
  yeniden simulasyondur, pencere sonunda zorla kapanir.
- Cikti `report.json` + `report.md`: kat basina tarihler/secim/puanlar, egitim ve
  test (islem/PF/net), havuzlu OOS islem/WR/PF/net/islem-duzeyi DD, LONG/SHORT,
  WFE (OOS net/gun / egitim net/gun), eslesmis bootstrap delta + %95 GA + p, secim
  sayilari, dokunulan saklı pencereler. Fonlama onbellegi yoksa sembol listesiyle uyarir.

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

# Yarim kalan kosumu devam ettir — yalniz TAM anahtari eslesen kayitlar atlanir
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
Muhurlu pencere ADAY'i yalniz veto edebilir; "canliya al" demez.
