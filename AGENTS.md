# AGENTS.md — bu repoda çalışan her ajan için

**Önce `CLAUDE.md` oku** (çalışma sözleşmesi: ne bu, nerede çalışır, nasıl test/deploy edilir,
yasaklar, karar kuralı). Bu dosya yalnız ajan/model politikasını özetler.

## Model ve efor politikası (KULLANICI KARARI, 2026-08-22 — pazarlığa kapalı)
- **En yüksek model, en yüksek efor. HER ZAMAN.** Ana oturum: **Opus 5 max** veya
  **Fable 5 max** (ultracode açık).
- Alt ajanlarda (`Agent` / `Workflow`) **Sonnet KULLANILMAZ**: `model: 'opus'` (veya fable),
  `effort: 'max'` / `'xhigh'`. Haiku yalnız gerçekten mekanik salt-okuma işlerinde;
  kod/strateji/analiz/incelemede asla.
- Gerekçe: bu bot gerçek parayla çalışacak. Bir yanlış parametre ya da gözden kaçan yarış
  koşulu, tasarruf edilen tüm token'lardan pahalıdır. Maliyet bir mazeret değildir.

## Çalışma disiplini (özet — ayrıntı CLAUDE.md ve docs/)
1. Kanıtsız değişiklik yok: 3 rejim penceresinde backtest (`docs/DECISIONS.md` P2) →
   testnet soak ≥5 gün → insan onayı → mainnet.
2. Harness = canlı motor (parite). Birini değiştiren diğerini ve parite testini de değiştirir.
3. Motor değişikliği = düşmanca inceleme (3+ mercek + çürütme turu) zorunlu.
4. Canlıya giren her şey aynı commit'te `docs/DECISIONS.md`'ye (ne/neden/kanıt/geri alma).
5. Secret'lar yalnız `.env`'de; log/çıktı/commit'e asla.
