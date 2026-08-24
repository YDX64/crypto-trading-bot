---
tags: [karar, aktif, follower, dusmanca-inceleme]
guncelleme: 2026-08-24
kaynak: docs/DECISIONS.md D20a (satir 861), docs/RUNBOOK.md
---
# D20a — Takipci halkasi dusmanca inceleme duzeltmeleri (19 ajan) · AKTIF · **BAGLAYICI**

**D20 ile celiskide D20a gecerlidir.** Baslica sertlestirmeler:

| Konu | Kural |
|---|---|
| Alarm tanima | Kopru ve giris **yalniz KATI AlgoPro bicimini** kabul eder |
| Ucret esigi | `FOLLOWER_MIN_TP1_FEE_RATIO=1.0` **VARSAYILAN ACIK** → stop ≥ ~%0.20 olmayan sinyal alinmaz |
| Dolum stopu gectiyse | Pozisyon **kapatilir** — yeniden capalama YOK |
| Yetim pozisyon | **entry-halt** (ayri halkada kosulsuz) |
| Deploy uyumu | `RING` ile `.env`'deki `BOT_MODE` uyusmazsa deploy **REDDEDILIR** |
| Restart | Ciplak `supervisorctl restart` **YASAK** → `scripts/restart_safe.sh` |

**Durum.** AKTIF. Gomulu modda (D20b) **iki kapi degisti**: "yetim =
entry-halt" kosullu oldu ve kapasite tavani motor-basina indi; **kalan tum
kapilar DEGISMEDEN** yururluktedir.

**Oncelik sirasi.** D20b > D20a > D20.

ILGILI: [[20-kararlar/D20-takipci-halkasi]] · [[20-kararlar/D20b-gomulu-takipci]] · [[40-isletme/deploy-ve-geri-alma]]
