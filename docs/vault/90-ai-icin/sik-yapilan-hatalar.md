---
tags: [ai-icin, hatalar, tuzaklar]
guncelleme: 2026-08-24
kaynak: docs/DECISIONS.md D19a/D22/D24/D26, docs/RUNBOOK.md, docs/EXPERIMENTS.md E8.6, oturum kayitlari
---
# Sik yapilan hatalar (GERCEKTEN yapildi)

> Bunlarin hepsi bu repoda **fiilen** yapilmis hatalardir. Listeyi
> kucumseme: cogu "mantikli gorunen" adimlardi.

## A. Olcum varken kanitsiz genisletme (coin ekleme)

**Ne oldu.** Gomulu takipcinin coin secimi ciddi bir olcumle yapildi:
40-coin 1m taramasi + TV AlgoPro panel okumalari + 3 hakem + curutucu →
**TUTUSDT** (yedek ZEC). Ertesi gun "islem akisini artirmak" icin evren
`TUTUSDT,ZECUSDT` yapildi; kayitta ZEC icin gerekce olarak **yalniz borsa
dilimi ve ucret kapisi** yazildi (50x/50k$ mmr %1.5, SL ~%0.6) —
**kenar olcumu tekrarlanmadi** (`docs/DECISIONS.md:1006`).

**Ders.** Bir secim olcumle yapildiysa, o secimi genisletmek de **ayni
olcumu** ister. "Ayni aileden, benzer coin" bir kanit degildir. Riski
sabit tutmak (`FOLLOWER_MAX_POSITIONS=1`) **secim kalitesini olcmez**.

## B. `/scalper/status`'ta `positions` alanini aramak

**Ne oldu.** Scalper pozisyonlari `tracked` anahtarindadir
(`src/strategies/scalper/engine.py:4999`); `positions` anahtari o yanitta
**YOKTUR**. `jq '.positions'` `null` doner ve "pozisyon yok" sanilir.

**Ders.** Alan adini **koddan** dogrula, benzer uctan analoji yapma.
`positions` yalniz `/positions` (orchestrator, `src/main.py:861`) ve
`/follower/status` yanitlarindadir.

## C. Golge modunun orchestrator'i kapsadigini varsaymak

**Ne oldu (D26, 2026-08-24).** Golge halkasi ayaga kalkti, golge banner'i
**dogru** basti — ama `SCALPER_SHADOW_MODE` yalniz **scalper motorunu**
kapsiyordu. Orchestrator ayri bir bilesendir ve `recover_open_positions()`
ile **CANLI halkanin 5 pozisyonunu "YETIM" sanip sahiplendi**
(10:44:10–10:44:23). Halka 5 dakikada durduruldu; **zarar yok** (5 pozisyon
ve 3'er koruma emri saglam, olculdu 10:49).

**Ders.** "Emir gonderilmez" bayragi **surecteki TUM bilesenler icin**
gecerli olmayabilir. Duzeltme `src/main.py:371`.

## D. `supervisorctl stop` ≠ "bir daha kalkmaz" (`autorestart` tuzagi)

**Ne oldu.** Container'a tasima reçetesinde `supervisorctl stop` yalniz SU ANI
durdurur. **Olculdu (2026-08-24, awa):** conf dosyasi `tradingbot-v2.conf`
(alt cizgi degil **tire**) ve icinde `autostart=true` + `autorestart=true` var
→ sunucu yeniden baslayinca motor **kendiliginden geri gelir**; hedef
makinedeki container `restart: unless-stopped` ile zaten ayaktadir →
**IKI MOTOR, AYNI HESAP.**

**Ders.** Tasimada `autostart=false` **yapilmadan gecilmez**; conf dosyasinin
adini **once oku** (varsayma).

## E. Nginx beyaz listesini unutmak

**Ne oldu (2026-08-24).** D21 adli kayit karti localhost'ta calisiyordu ama
kullanicida **404** aliyordu: nginx monitor proxy yalniz sayilan salt-okuma GET
uclarini gecirir, listelenmeyen her yol catch-all ile 404 doner.

**Ders.** Yeni pano karti yeni uc cagiriyorsa **proxy'ye eklenmeden
kullanicida CALISMAZ** ([[40-isletme/panel-erisimi]]).
Kars ornek: D23 karti `/api/status` govdesini okur → **ekleme gerekmez**.

## F. Post-hoc kapi simulasyonuna karar verdirmek

**Ne oldu (E8.6 → E7).** Defter uzerinde post-hoc simule edilen gun-kapisi,
motor-ici olcumle yeniden kosuldugunda **isaret degistirdi**
(YATAY %1.0: post-hoc **−487.3** → motor-ici **+201**). Neden: engellenen
sinyal motor-ici kapida **kapasiteyi serbest birakir** ve bosalan slota
sonraki sinyal girer.

**Ders.** Kapi kararlari **motor-ici** (ve harness-parite) olculmelidir;
defter uzerinde "sanki uygulasaydik" hesaplari **alt sinirdir**.
Ayrica bir kapinin ΔPnL'i **engelleme + yeniden tahsis** toplamidir.

## G. Esigi olcmeden koruma acmak

**Ne oldu (D22).** REST agirlik geri cekilmesi ilk tasarimda **varsayilan
ACIK** (2000/2300) gelecekti. Testnet'te `X-MBX-USED-WEIGHT-1M` gunluk
**MEDYANI 2373** olculdu → tarama **KALICI dururdu**, bot hic islem acmazdi.

**Ders.** **Esik once olculur, sonra acilir.** Varsayilan `0/0`'a alindi.

## H. Kayit kusurunu davranis degisikligiyle "cozmek"

**Ne oldu (D22 ilk hali).** `-2021` sonrasi yanlis etiketlenen kapanislari
duzeltmek icin **on-kapanis** onerildi (bot kendi fiyat okumasiyla pozisyonu
kapatsin). 12 ajanlik inceleme 4 yuksek bulguyla reddetti: yetki genislemesi ·
bayat fiyat riski · `-2022` yarisi · **kazanc yok**. Kayit kusuru on-kapanis
OLMADAN tamamen duzeltildi.

**Ders.** Once **en dar** duzeltmeyi ara. "Kaydi duzeltmek" ile "davranisi
degistirmek" ayri islerdir.

## I. Bir belirtecin dusebilecegini hesaba katmamak

**Ne oldu (D19a/A).** `kind` belirteci duserse bir **CIKIS** alarmi **GIRIS
OYUNA** donusuyordu; ustelik govdedeki `src` allowlist'te oldugu icin **yeni
bir saglama kaynagi** sayiliyor ve LuxAlgo ailesi tek basina 2/2 kotayi
doldurup **pozisyon actirabiliyordu**.

**Ders.** Fail-open bir ayristirma, guvenlik siniri olan yerlerde **fail-closed**
olmalidir. Ayrica "kullanici metni govdenin ortasina yazilabilir" (G1).

## J. Pano yolundan `force_fresh` istemek

**Ne oldu (2026-08-18).** `/api/status` her 5 sn'de `force_fresh=True` ile
pozisyon sorguluyordu → rate-limiter kuyrugu doydu → **scan dongusu acliga
dustu** → watchdog restart. Belirti imzasi: **hata yok + safety taze + scan
bayat**.

**Ders.** Okuma yolu, koruma yolunu bloklamamalidir. Duzeltme: 5 sn
sunucu-tarafi onbellek + `force_fresh=False`.

## K. Testleri `.env` olmadan kosmak

`Settings` zorunlu alanlar ister; `.env` yoksa pytest **collection** asamasinda
patlar. CI'nin yaptigi: `cp env.example .env`.

## L. Kucuk ama pahali varsayimlar

| Varsayim | Gercek |
|---|---|
| "README sistemi anlatir" | **Eskidir** (Ekim 2025) → `CLAUDE.md` |
| "`live-bot.service` trading botu" | **Futbol botu** |
| "`src/api_server.py` calisiyor" | Deprecated, tek app `src/main.py` |
| "Rejim TF 4h" | Canli `.env`'de **15m** |
| "Kodda var = canlida acik" | D18/D19/D23 **varsayilan KAPALI/GOLGE** |
| "Kazanma orani %88, demek ki iyi" | Basabas **%85**; WR anlamli **degil** (p=0.137) |
| "Backtest sayilari OOS" | **Degil** — 36 varyant ayni 3 pencerede |
| "`forensics` bos = temiz islem" | Bos = **olculmemis** |
| "`enabled=true` = kapi koruyor" | `gate_effective` bakilir |
| "`supervisorctl restart` yeterli" | `scripts/restart_safe.sh` |

ILGILI: [[90-ai-icin/calisma-kurallari]] · [[90-ai-icin/dogrulama-receteleri]] · [[40-isletme/halka-yonetimi]] · [[30-deneyler/00-metodoloji-uyarisi]]
