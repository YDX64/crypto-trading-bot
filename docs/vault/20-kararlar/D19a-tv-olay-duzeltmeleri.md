---
tags: [karar, golge, tradingview, dusmanca-inceleme]
guncelleme: 2026-08-24
kaynak: docs/DECISIONS.md D19a (satir 1836)
---
# D19a — D19 dusmanca inceleme duzeltmeleri (24 bulgu, iki tur) · GOLGE · **BAGLAYICI**

19 ajanlik dusmanca inceleme **14 kusur** buldu; duzeltmelerden sonra ikinci
tur **10 kusur daha** cikardi (birincisi, birinci turun G1 duzeltmesinin A
bulgusunu geri getirmesiydi). Toplam **24**. Kanal hala `shadow`.

## Yuksek (3)

| # | Bulgu | Duzeltme |
|---|---|---|
| **A** | `kind` belirteci duserse bir CIKIS alarmi GIRIS OYUNA doner; govdedeki `src` allowlist'te oldugu icin **YENI SAGLAMA KAYNAGI** sayilir → LuxAlgo ailesi tek basina 2/2 kotayi doldurup **pozisyon actirabilir**. | `TV_EVENT_SOURCES` kumesindeki kaynak `kind=entry` ile gelirse **422**. Kontrol istegin TUM kaynak adaylarina uygulanir. |
| **B** | `EXIT=be` **zararda** stopu piyasanin ters tarafina koyar → `-2021` → `_emergency_close`. "Geri alinabilir" sanilan ayar fiilen **piyasa emriyle kapanis**ti. | `breakeven_side_ok()` — BE yalniz **kardayken** (%0.05 pay). Zarardaki pozisyon icin `SCALPER_TV_EVENTS_EXIT_LOSING` (vars. **skip**). `None` = hicbir sey yapilmaz. |
| **C** | TV cikis dalinda `UnprotectedPositionError` genel `except`e dusuyor, entry-halt latch'i **sessiz** kaliyordu. | Ayri `except` → `_latch_entry_halt(..., source="TV olay cikisi")`. |

## Orta (5)

- **D** Tuketim imlecleri yalniz RAM'deydi → her restart tuketilmis olayi
  yeniden tetikliyordu. Artik defterle AYNI dosyada, atomik
  (`_STATE_VERSION=2`, v1 **yukseltilir**); basarisiz aksiyon tuketilmez,
  en fazla 3 deneme.
- **E** Sunucu `.env`'i `TV_SOURCE_ALLOWLIST`'i set ederse kanal
  **"kurulu gorunup olu"** kaliyordu. Olay yolu artik allowlist'ten BAGIMSIZ;
  teshis `/scalper/status` → `tv_events.allowlist_ok`.
- **F** MIXED (kaynaklar celisiyor) sembolu **IKI YONE DE** kilitliyordu.
  Artik MIXED → **kapi UYGULANMAZ**. Olay yolu D7 sembol allowlist'ini uygular.
- **G1** `src=`/`kind=` govdenin HER YERINDE araniyordu → kullanici metni bir
  alarmin kimligini degistirebilirdi. Artik yalniz **baslik kosusu** (ilk 5
  satir) ve JSON'da ust duzey + `data`.
- **G2** Kimliksiz istek 422 mesajindan gecerli `kind` listesini
  ogrenebiliyordu. Secret dogrulamasi artik **govde ayristirmasindan ve HER
  422'den ONCE**, sabit zamanli.

## Dusuk

- **G3** Sembol yalniz "USDT ile bitiyor mu" diye suzuluyordu → defterde bozuk
  anahtar; defter sinirsiz buyuyebiliyordu. Artik regex + budama.
- **G4** "Trend Catcher" ve "Trend Tracer" ayni `src`i paylasir → birbirini
  MIXED'e DUSURMEZ, **son olay kazanir**; `via=` yalniz telemetri.
- **G5** `MAX_AGE_MIN=0` ve bos `GATE_SOURCES` **KAPALI** demektir.
  `active` + bos `GATE_SOURCES` → startup `ValueError`
  (`src/core/config.py:1134`).

**Kural.** Bulgu → duzeltme → test eslemesi **baglayicidir**; birini
degistiren digerini de degistirir.

ILGILI: [[20-kararlar/D19-tv-olay-kanali]] · [[10-mimari/tv-sinyal-yolu]] · [[90-ai-icin/sik-yapilan-hatalar]]
