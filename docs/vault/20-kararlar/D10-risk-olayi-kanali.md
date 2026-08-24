---
tags: [karar, aktif, guvenlik, risk-event]
guncelleme: 2026-08-24
kaynak: docs/DECISIONS.md D10 (satir 65), docs/INTEGRATIONS.md §3, src/main.py, src/strategies/scalper/engine.py
---
# D10 — Risk-olayi kanali `POST /risk-event` (halt/resume/flatten/status) · AKTIF

**Karar.** Haber/olay botlarinin strateji mantigina DOKUNMADAN girisleri
durdurabildigi / devam ettirebildigi / tum pozisyonlari duzlestirebildigi
**ayri** bir uc nokta. Ayri secret (`RISK_EVENT_SECRET`, bos = 503),
ayri durum dosyasi (`state/risk_event_halt.json`).
**Gerekce.** "Savas cikti, her seyi kapat" tipi olay bir YON sinyali degildir.
TV webhook'u yon onerir ve saglamadan gecer; "her seyi durdur" karari
saglamaya TABI OLMAMALIDIR. Ayrica canli `SCALPER_ENTRY_HALT_ENABLED=false`
oldugu icin mevcut entry-halt latch'i bu amaca uygun degildi.
**Kanit.** `tests/test_risk_event.py` — 50 test (29 auth/dogrulama/dispatch +
21 dusmanca-inceleme regresyonu). Backtest'e DOKUNULMADI.
**Durum.** AKTIF (2026-08-21).
**Geri alma.** `RISK_EVENT_SECRET`'i bosalt → uc kendiliginden 503 olur
(kod geri alinmasina gerek yok).

## Dusmanca inceleme (21 ajan) — 6 kusur, ayni gun duzeltildi
1. **Halt sirasi**: `flatten` halt'i kapatma turundan SONRA kuruyordu → tur
   sirasinda YENI pozisyon acilabilirdi. Artik halt turdan ONCE; tur sonrasi
   ikinci tarama escanli dolumu yakalar.
2. **Olu retry**: kapanis dogrulamasi `force_fresh=True` olmadan cagriliyordu
   → 5 sn onbellek 4 denemeyi bayat kayda dusuruyordu.
3. **Bayat miktar**: reduce-only MARKET giris dolumuyla boyutlaniyordu →
   `-2022` riski. Artik canli `positionAmt`.
4. **Cift finalize**: `_closing: Set[str]` tek-finalizer kapisi eklendi.
5. **Sessiz fail-open**: dosya yazilamazsa halt RAM'de tutulmuyordu →
   `_risk_event_halt_ram` latch'i + yanitta `persisted: bool`.
6. **Loguru/hmac**: `{...}` iceren `reason` 500 veriyordu; `hmac.compare_digest`
   ASCII-disi secret'ta TypeError; `ttl_minutes: Infinity` OverflowError.

**Nerede.** `src/main.py:2177`; motor tarafi
`src/strategies/scalper/engine.py:779` / `:809` /
`src/strategies/scalper/engine.py:837`; tek giris kapisi
`src/strategies/scalper/engine.py:1009`.

ILGILI: [[10-mimari/guvenlik-kilitleri]] · [[40-isletme/sorun-giderme]] · [[20-kararlar/D20b-gomulu-takipci]]
