---
tags: [karar, aktif, golge, mainnet]
guncelleme: 2026-08-24
kaynak: docs/DECISIONS.md D14 (satir 172), docs/RUNBOOK.md "Golge modu"
---
# D14 — Golge modu (`SCALPER_SHADOW_MODE`) · AKTIF

**Karar.** `SCALPER_SHADOW_MODE=true` iken sinyal BUGUNKU GIBI tum kapilardan
gecer (cooldown, bakiye, stop mesafesi/R:R, boyutlama, borsa filtresi) ama
adim 6'dan itibaren **hicbir borsa istegi gitmez**: margin/leverage
AYARLANMAZ, emir GONDERILMEZ, SL/TP YOK, pozisyon izlenmez.
Kayit `scalp_trades`'e `status="SHADOW"`, `entry_price=sinyal fiyati`,
`notes="shadow_mode"` olarak duser; `try_open` `None` doner.
**Gerekce.** Yeni bir parametreyi (ya da mainnet'in kendisini) gercek parayla
riske girmeden gozlemlemek (`docs/MAINNET_PLAN.md` §3/§5.2).
**Kanit.** `tests/test_shadow_mode.py` (19 test).
**Durum.** AKTIF (2026-08-22).
**Geri alma.** `SCALPER_SHADOW_MODE=false` + korumali restart.

**Yan etki (D14 sozlesmesi).** Mainnet'te golge KAPALIYSA `RISK_EVENT_SECRET`,
`TV_WEBHOOK_SECRET` ve `SCALPER_SYMBOL_ALLOWLIST` **zorunludur**; golge modu
bu ucunu bypass edebilen TEK istisnadir. `SCALPER_ENTRY_HALT_ENABLED=false`
kontrolu golgeden bagimsiz **her zaman** uygulanir.

**⚠️ D26 duzeltmesi.** Golge modu **orchestrator'i kapsamiyordu** — golge
halkasi canli halkanin pozisyonlarini "yetim" sanip sahiplendi. Artik golge
modunda orchestrator **HIC baslatilmaz** (`src/main.py:371`).

ILGILI: [[20-kararlar/D26-golge-halkasi]] · [[10-mimari/guvenlik-kilitleri]] · [[20-kararlar/mainnet-plani]]
