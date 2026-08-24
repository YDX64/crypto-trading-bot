---
tags: [mimari, forensics, gozlem, d21, d23, d24, d27]
guncelleme: 2026-08-24
kaynak: src/strategies/scalper/forensics.py, src/strategies/scalper/forensics_log.py, src/strategies/scalper/intent.py, src/strategies/scalper/counterfactual.py, src/strategies/scalper/ai_gate.py, docs/DECISIONS.md D21/D23/D24/D27
---

# Gozlem katmanlari — forensics (D21), niyet (D24), karsi-olgu (D27), AI kapisi (D23)

## NE

Dort ayri kayit katmani. **DORDU DE YALNIZ GOZLEMDIR**: hicbir kapi,
boyutlama, stop/TP seviyesi ya da cikis karari bunlari OKUMAZ. Emir akisi
bunlar olmadigindaki gibidir.

| Katman | Karar | Ne kaydeder | Varsayilan |
|---|---|---|---|
| **Forensics** | [[20-kararlar/D21-islem-adli-kaydi]] | gerceklesen islemin "neden girildi / nasil cikildi / ne ters gitti"si | ACIK |
| **Niyet kaydi** | [[20-kararlar/D24-olcum-paketi]] | gerceklesMEyen niyet (kapi reddi, saglama dolmadi, emir hatasi) | ACIK, sayaclar surec-ici |
| **Karsi-olgu defteri** | [[20-kararlar/D27-olcum-borcu-karsi-olgu]] | reddedilen niyet H saat sonra "girilseydi ne olurdu" | ACIK, kuyruk surec-ici |
| **AI kapisi** | [[20-kararlar/D23-ai-kapisi]] | dil modelinin "bu giris alinmali miydi" hukmu | **`off`** |

## NEREDE

| Ne | Yer |
|---|---|
| Forensics saf katman | `src/strategies/scalper/forensics.py:358` (giris) · `src/strategies/scalper/forensics.py:518` (cikis) |
| Etiket kurallari | `src/strategies/scalper/forensics.py:679` · `src/strategies/scalper/forensics.py:719` |
| MAE fiziksel kelepcesi (D27/A3) | `src/strategies/scalper/forensics.py:632` |
| Post-mortem | `src/strategies/scalper/forensics.py:777` |
| Ozet | `src/strategies/scalper/forensics.py:877` |
| Olay akisi (`logs/trades.jsonl`) | `src/strategies/scalper/forensics_log.py:171` · `src/strategies/scalper/forensics_log.py:208` |
| JSONL okuma (yalniz istek uzerine) | `src/strategies/scalper/forensics_log.py:115` |
| Giris baglami (motor) | `src/strategies/scalper/engine.py:2656` |
| Niyet kaydi | `src/strategies/scalper/intent.py:213` · `src/strategies/scalper/intent.py:292` |
| Niyet cagrisi (motor) | `src/strategies/scalper/engine.py:2513` |
| Karsi-olgu saf cekirdek | `src/strategies/scalper/counterfactual.py:343` (simulasyon) · `src/strategies/scalper/counterfactual.py:621` (ozet) |
| Karsi-olgu durum katmani | `src/strategies/scalper/counterfactual_store.py:186` · `src/strategies/scalper/counterfactual_store.py:329` |
| Karsi-olgu motor kancalari | `src/strategies/scalper/engine.py:2591` · `src/strategies/scalper/engine.py:2638` |
| AI kapisi | `src/strategies/scalper/ai_gate.py:857` (`should_block` `:943`, `observe` `:962`) |
| AI gozlem cagrisi | `src/strategies/scalper/engine.py:2441` |
| Okuma uclari | `src/main.py:3041` · `src/main.py:3054` · `src/main.py:3060` · `src/main.py:3102` |

## NASIL CALISIR

### Forensics belgesi

`scalp_trades.forensics` (TEXT/JSON; eski satirlarda NULL):

```json
{"v":1, "entry":{...}, "exit":{...}, "verdict":["counter_drift_long"], "postmortem":{...}}
```

Etiketler (kural tabanli, saf fonksiyonlar):
`counter_drift_long` · `relief_rally_short` · `late_entry_after_run` ·
`tv_single_family` · `stale_signal` · `gate_bypassed` (giris) ·
`fee_dominated` · `mfe_giveback` (cikis) · `noise_stop` (post-mortem).

**Look-ahead yoktur**: `entry`/`exit` yalniz o an bilinen degerleri tasir;
`noise_stop` ancak kapanistan sonra olculebildigi icin AYRI `postmortem`
alanindadir.

**REST maliyeti:** giris/cikis tarafinda **sifir** ek istek. Tek ek istek
post-mortem turudur: dakikada en fazla bir kez, tur basina EN FAZLA BIR sembol,
5 sn timeout, ust sinir saatte 60 istek / 120 agirlik.

### AI kapisi sozlesmesi (pazarliga kapali)

1. Yalniz `deny` bir etki uretebilir; `allow` hicbir sey ACMAZ.
2. Motor **0 ms bekler** — kanca `_entry_lock` DISINDA, atesle-unut.
3. Her ariza **fail-OPEN**.
4. **Prompt injection savunmasi**: alarmin HAM METNI prompt'a asla girmez;
   yalniz sayilar ve kapali listeden gelen belirtecler.
5. **Sifir REST agirligi**.
6. `active` **config validator tarafindan REDDEDILIR**
   (`src/core/config.py:1204`) — go_live olcutleri kanitlanmadan acilmaz.

## TUZAKLAR

- **"Etiketsiz" ≠ "temiz".** D21 oncesi kapanan islemlerin `forensics` sutunu
  NULL'dur; raporda `_etiketsiz_` satirina duserler.
- **Post-mortem gecikmelidir**: `noise_stop` kapanistan
  `SCALPER_FORENSICS_POSTMORTEM_MIN` (vars. 60) dk SONRA belirir. Yoklugu
  "yok" degil "henuz olculmedi" demektir.
- **`postmortem.note = "olculemedi"`** bir motor arizasi DEGILDIR, olcum
  eksigidir.
- **Niyet sayaclari surec-icidir ve restart'ta SIFIRLANIR**
  (`window: "process_start"`); kalici tarihce `logs/trades.jsonl`'dedir.
- **`horizon_end_at` / `invalid_if` / `confidence` / `model_version` alanlari
  semaya girdi ama DOLDURULMUYOR** → rapor `with_expectation: 0` doner. Bu
  DOGRU sonuctur: null = "olculmedi" ([[30-deneyler/D24-olcumleri]]).
- **Kayit bir kanit degil, kanit KAYNAGIDIR.** Bir etiketin PnL'i kotu diye
  parametre degistirmek CLAUDE.md yasak #1'i ihlal eder.
- **AI kapisi motora KABLOLANMAMISTIR.** `active`e gecmek validator satirini
  silmek degil, kancayi karar yoluna tasimak VE harness paritesini kurmaktir.

## ILGILI

[[10-mimari/defter-ve-muhasebe]] · [[50-veri/loglar]] ·
[[40-isletme/gunluk-kontrol]] · [[90-ai-icin/dogrulama-receteleri]]
