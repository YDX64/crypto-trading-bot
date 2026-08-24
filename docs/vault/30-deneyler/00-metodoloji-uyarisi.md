---
tags: [deneyler, metodoloji, uyari, baglayici]
guncelleme: 2026-08-24
kaynak: docs/EXPERIMENTS.md bas kutusu (D24/A8)
---
# ⚠️ METODOLOJI UYARISI — bu defterdeki sayilar OOS DEGILDIR

## Olgu

`docs/EXPERIMENTS.md`'deki **E2…E9 varyantlarinin TAMAMI ayni uc pencerede**
olculdu: AYI `2026-01-23→02-13` · YATAY `2026-07-01→07-21` ·
BOGA `2026-08-07→08-21`.

Sayilabilir: **29 harf etiketli varyant** (`E2a`…`E6e`) + **E9'un 7 yapi
varyanti** (S1, S2, S3, S4, S1p3, S1p8, S2p8) = **36 varyant, 3 pencere**.
Bu uc pencere disinda olculmus **hicbir** varyant yoktur.

## Neden sorun

Ayni pencerede arka arkaya varyant denemek **tekrarli holdout**'tur: her yeni
deneme o pencereyi biraz daha bir **egitim kumesine** cevirir. Canliya giren
D6 (`E2a`) bu pencerelerde **SECILDI** — dolayisiyla olculen kenari, gercek
beklenen kenarin tarafsiz tahmini degil bir **UST SINIRIDIR**.
Dokunulmamis bir dogrulama penceresi **YOKTUR**.

## Bu ne DEMEK DEGIL

"Sonuclar yanlis" ya da "D6 kotu" demek **degildir**. Yalniz sunu der:
bu tablolardaki hicbir sayi **ornekleme-disi (OOS) tahmin** olarak okunamaz;
**goreli karsilastirma** icin hala gecerlidirler.

## Onerilen kural: SAKLI PENCERE

> Bu bir OLCUM DEGIL, bir YONTEM kuralidir. **Henuz hicbir sakli pencere
> kosulmadi** ve bu kural henuz hicbir karara uygulanmadi.

1. **Bir kere, onceden, kor secilir.** Aday alan: `2026-02-15 → 2026-06-25`.
2. **Arama sirasinda ACILMAZ** (autoresearch dahil).
3. **Aday basina EN FAZLA BIR kez** acilir.
4. **Sonuc NE CIKARSA CIKSIN yazilir** — ozellikle basarisizliklar.
5. **Yalniz VETO eder, terfi ETTIRMEZ.**
6. Acildiktan sonra o parametre ailesi icin **YANIK** sayilir.
   **Acilma sayaci bugune kadar: 0.**
7. Cok-varyant taramasinda p/q raporlanir:
   `--permutations` (p) ve `python3 -m src.strategies.scalper.multitest` (q).

## Kuralin kendi sinirlari (durustluk)

(a) Pencerelerimiz 3 hafta; sakli pencere de kisa olacagindan tek basina
istatistiksel guc vermez. (b) Kripto rejimi hizli degisir: farkli donemden
secilen bir pencere "asiri uyum" ile "rejim genellemesini" birlikte sinar ve
**iki etki ayrismaz**.

ILGILI: [[30-deneyler/00-deney-indeksi]] · [[20-kararlar/P2-karar-kurali]] · [[20-kararlar/D24-olcum-paketi]] · [[90-ai-icin/calisma-kurallari]]
