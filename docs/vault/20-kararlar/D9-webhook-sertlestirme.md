---
tags: [karar, aktif, guvenlik, tradingview]
guncelleme: 2026-08-24
kaynak: docs/DECISIONS.md D9 (satir 47), src/main.py
---
# D9 — Webhook sertlestirme: `?src=` allowlist + erisim logu secret redaksiyonu · AKTIF

**Karar.** (1) `TV_SOURCE_ALLOWLIST` (vars. `luxosc,luxso,algopro,botv3,tv`) —
`?src=` normalize edilip bu kumeye karsi dogrulanir; bilinmeyen deger
**REDDEDILMEZ**, `tv`'ye eslenir + WARNING, yanit `source_raw_rejected: true`.
(2) `uvicorn.access`/`uvicorn.error` logger'larina `secret=<deger>` →
`secret=***` filtresi.
**Gerekce.** `?src=` serbest metin oldugu icin bir yazim hatasi ("algpro")
sessizce **hayalet kaynak** yaratiyordu — saglamada asla farkli kaynak sayisini
dolduramayan, fark edilmeyen sinyal kaybi. Ayrica webhook secret'i `?secret=`
query'sinde tasinabiliyor ve erisim logu tam istek satirini duz metin yaziyordu.
**Kanit.** `tests/test_tv_signal_bridge.py`, `tests/test_access_log_redaction.py`.
**Durum.** AKTIF (2026-08-21).
**Geri alma.** `src/main.py`/`src/core/config.py` degisikliklerini revert
(davranissal risk yok — kabul mantigi gevsetildi).
**Nerede.** `src/main.py:72` (filtre), `src/main.py:93` (kurulum),
`src/main.py:1230` (kaynak cozumu).

ILGILI: [[10-mimari/tv-sinyal-yolu]] · [[50-veri/loglar]] · [[40-isletme/panel-erisimi]]
