---
tags: [karar, golge, ai, kapi]
guncelleme: 2026-08-24
kaynak: docs/DECISIONS.md D23 (satir 2325), src/strategies/scalper/ai_gate.py, docs/RUNBOOK.md
---
# D23 — AI karar katmani (`SCALPER_AI_GATE_MODE`) · GOLGE · kod varsayilani `off`

**Karar.** Motor TUM kapilarini gecirip pozisyonu ACTIKTAN sonra, acilan
islemin baglami bir dil modeline tek soruyla sorulur: *"bu giris alinmali
miydi?"*. Yanit KATI bir JSON semasidir (`schema_version=d23.1`) ve iki yere
yazilir: `logs/trades.jsonl` (`event="ai_verdict"`) ve
`scalp_trades.forensics` JSON'unun `document["ai"]` blogu (**MIGRATION YOK**).

**Neden.** Kural tabanli etiketlerin (D21) goremedigi sey baglamin
BIRLESIMIDIR: "TV SHORT + lider yukari + stop ATR'ye gore dar + saglama tek
aile" gibi bir cakisma tek esikli hicbir kuralda gorunmez ama defterde odeme
asimetrisi olarak durur (E8.7: TV SHORT 15 islem, PF 0.15).

**Kanit.** **YOK** — bu bir strateji kaniti degil, kanit URETME altyapisidir.
Hukum golge olcumunden sonra eklenecek.

**Neden GOLGE ve varsayilan `off`:**
1. Bir dil modelinin hukmu de bir PARAMETRE degisikligidir; CLAUDE.md yasak #1.
2. `off` iken `AiGate` **hic ornEklenmez** → sifir cagri, sifir maliyet.
3. `active` **config validator tarafindan REDDEDILIR**
   (`src/core/config.py:1204` → startup `ValueError`). Kod yolu
   (`src/strategies/scalper/ai_gate.py:943`) hazirdir ama **motora
   KABLOLANMAMISTIR**: `active`e gecmek validator satirini silmek degil,
   kancayi karar yoluna tasimak **VE** harness/motor paritesini kurmaktir
   ([[20-kararlar/P1-harness-parite]]).

**Sozlesme (pazarliga kapali).** Yalniz `deny` etki uretir · motor 0 ms bekler
(`_entry_lock` DISINDA) · her ariza **fail-OPEN** · **prompt injection
savunmasi**: alarmin ham metni prompt'a asla girmez · **sifir REST agirligi**.

**Durum.** GOLGE, canlida ACILMADI.
**Geri alma.** `SCALPER_AI_GATE_MODE=off` (zaten varsayilan).
**Izleme.** `/scalper/status.ai_gate` (mod, kapsama, gecikme, butce, red orani);
pano karti **yeni uc acmaz** — `/api/status` govdesindeki `ai_gate` blogunu
okur, nginx beyaz listesine ekleme gerekmez.

ILGILI: [[10-mimari/gozlem-katmanlari]] · [[20-kararlar/D21-islem-adli-kaydi]] · [[30-deneyler/E8-sinyal-otopsisi]]
