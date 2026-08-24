---
tags: [mimari, defter, pnl, muhasebe]
guncelleme: 2026-08-24
kaynak: src/strategies/scalper/tracker.py, src/strategies/scalper/exits.py, src/models/scalp_trade.py, scripts/ledger_report.py
---

# Defter ve muhasebe — kapanis dogrulama merdiveni

## NE

Bir islemin PnL'i **tahmin edilmez, dogrulanir**. Uc kademeli merdiven vardir
ve hicbiri tutmazsa sonuc acikca "bilinmiyor" olarak kaydedilir. Bu, botun en
onemli durustluk sozlesmesidir: *"bilinmiyor" asla "kapandi"/"TP" diye
maskelenmez.*

## NEREDE

| Ne | Yer |
|---|---|
| DB yazimi (acilis) | `src/strategies/scalper/tracker.py:69` |
| DB yazimi (kapanis) | `src/strategies/scalper/tracker.py:158` |
| PnL kaynagi siniflandirmasi | `src/strategies/scalper/tracker.py:311` |
| Istatistik | `src/strategies/scalper/tracker.py:476` |
| AI blogu ekleme (D23) | `src/strategies/scalper/tracker.py:596` |
| Kapanis defteri (userTrades) | `src/strategies/scalper/exits.py:1702` |
| Kapanis finalize | `src/strategies/scalper/exits.py:1306` |
| ORM modeli | `src/models/scalp_trade.py:16` |
| Rapor betigi | `scripts/ledger_report.py` |

## NASIL CALISIR

### Dogrulama merdiveni (yukaridan asagi)

1. **Binance `income`** — gercek net PnL (komisyon dahil). En guclu kanit.
2. **`userTrades` close ledger** (`_verified_close_ledger`) — borsa satirlariyla
   kanitlanmis kapanis ozeti; ALGO adaylarina bakar.
3. **Tahmini brut PnL** — yalniz ilk ikisi yoksa.
4. Hicbiri tutmazsa → `exit_reason="UNKNOWN"` + `notes` icine
   `exit_fill=unverified` / `close_verification=unverified`.

Duz MARKET kapanisi (acil kapanis) close ledger'da gorunmez; o durumda
kapanis fiyati emrin `userTrades` VWAP'indan okunur ve not
`exit_fill=market_close_order` olur (D22).

### Rapor

```bash
python3 scripts/ledger_report.py --since "2026-08-14 00:00" --format md
python3 scripts/ledger_report.py --since "2026-08-23" --forensics --format md
```
Rapor canli defteri **BTC gunluk %'sine gore UP/FLAT/DOWN rejimlerine boler**,
yon/cikis-nedeni/sembol/gun kirilimlarini ve `docs/MAINNET_PLAN.md` §2 soak
kontrol listesini PASS/FAIL yazdirir. **Hukum vermez** — insan karar verir.

### `exit_reason` degerleri

`SL` · `TP_LADDER` · `TRAIL` · `TRAIL_MARKET` · `BE_MARKET` · `RISK_EVENT` ·
`TV_EVENT` · `STRUCT_CHOCH` · `MANUAL` · `UNKNOWN`
(`src/models/scalp_trade.py:16` blogundaki yorum kanoniktir).

## TUZAKLAR

- **`UNKNOWN` orani < %5 olmali** — mainnet terfi olcutlerinden biridir.
- **`entry_order_id` KALICIDIR**: restart sonrasi income/userTrades
  dogrulamasi calisabilsin diye DB'ye yazilir.
- **SHADOW satirlari istatistiklerden kendiliginden dislanir**: `stats()` ve
  `open_trades()` yalniz `CLOSED`/`OPEN` sorgular.
- **AlgoPro takipci islemleri ayni tabloda** ama `strategy="AP"` etiketiyle
  durur ([[10-mimari/takipci-algopro]]).
- **Kazanma orani tek basina kanit degildir**: basabas ≈%85, kenar kayip
  buyuklugunun kontrolunden gelir ([[30-deneyler/E10-permutasyon]]).
- **Konsantrasyon**: kazancin buyuk bolumu az sayida gun/islemden gelebilir;
  rapor bunu "Yogunluk" satirinda gosterir ([[30-deneyler/D24-olcumleri]]).

## ILGILI

[[50-veri/veritabani-semasi]] · [[50-veri/metrikler]] ·
[[10-mimari/cikis-yonetimi]] · [[10-mimari/gozlem-katmanlari]] ·
[[40-isletme/gunluk-kontrol]]
