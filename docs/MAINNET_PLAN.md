# Mainnet planı — "ana parada hata ve kayıp olmasın" için üç halka

Durum (2026-08-21): mainnet halkası YOK. Testnet botu (`tradingbot_v2`) tek canlı süreç.
Bu doküman mainnet'e geçişin **şartlarını** ve **mimarisini** sabitler; tarih vermez — tarih
kanıtla gelir.

## 1. Üç halka
| Halka | Ne | Kim değiştirir | Nasıl girer |
|---|---|---|---|
| A — Yerel + CI | kod, testler (566), altın backtest, autoresearch | geliştirici/AI | `git push` → CI yeşil |
| B — Testnet (bugünkü canlı) | `awa:/opt/tradingbot-v2`, supervisord `tradingbot_v2` | `scripts/deploy.sh awa` | her değişiklik burada ≥5 gün soak |
| C — Mainnet (henüz yok) | AYRI dizin `/opt/tradingbot-main`, AYRI supervisord programı `tradingbot_main`, AYRI `.env`/anahtar/DB/state/log/Telegram | `scripts/deploy.sh awa <etiket> --ring mainnet` (yazılacak) | yalnız **etiketli sürüm** (`vX.Y.Z`), açık onay |

Aynı kod, farklı env. Halka C asla `origin/main`'in ucunu almaz; yalnız B'de soak olmuş etiketi alır.

## 2. Terfi ölçütleri (bir değişikliğin B→C geçmesi için hepsi)
1. A: CI yeşil + altın backtest değişmediyse ya da bilinçli güncellendiyse (EXPERIMENTS notu).
2. Backtest: 3 rejim penceresinde P2 kuralı (`docs/DECISIONS.md`): AYI PF ≥ 1.1 **ve** BOĞA PnL kaybı ≤ %20, ≥60 işlem/pencere.
3. B soak: ≥5 gün, içinde en az **1 düşüş günü** (BTC günlük < −%1.5); canlı defter rejime bölünmüş rapor
   (UP/FLAT/DOWN × LONG/SHORT) pozitif veya başabaş; `exit_reason=UNKNOWN` oranı < %5; 418/429 yok.
4. Operasyon: RUNBOOK güncel, DECISIONS satırı var, geri alma komutu yazılı ve denenmiş.
5. İnsan onayı: kullanıcı "mainnet'e al" der; AI tek başına terfi ETMEZ.

## 3. Mainnet'e özel koruma katmanı (kod + env; çoğu zaten var)
- **Boyut tavanı:** `SCALPER_MAX_MARGIN_PCT` ve `SCALPER_MAX_POSITIONS` testnet'in altında başlar
  (öneri: ilk 2 hafta marj %3, pozisyon 2, kaldıraç tavanı 5x — `SCALPER_DYN_LEV_MAX=5`).
- **Günlük zarar kesicisi:** `SCALPER_DAILY_LOSS_LIMIT_PCT` (var) — mainnet'te %3.
- **Kill-switch:** `POST /risk-event {action: flatten}` (D10) — ayrı secret, süreli halt. Mainnet'te
  `RISK_EVENT_SECRET` ZORUNLU (boş bırakılamaz; başlangıç doğrulamasına eklenecek).
- **Entry-halt otomatiği:** `SCALPER_ENTRY_HALT_ENABLED=true` (mainnet doğrulaması zaten `false`'u reddeder).
- **Gölge modu (D14, YAZILDI):** `SCALPER_SHADOW_MODE=true` → sinyaller üretilir, loglanır, defterde
  "SHADOW" olarak kaydedilir, emir GÖNDERİLMEZ. Yeni parametre mainnet'te önce 3 gün gölge.
  Ayrıntı: `docs/DECISIONS.md` D14, `docs/RUNBOOK.md` "Gölge modu".
- **Mutabakat:** her gün `scalp_trades` ↔ Binance income (close ledger var) farkı > %1 ise Telegram uyarı + halt.
- **Ağırlık/ban:** mainnet'te gerçek X-MBX-USED-WEIGHT; eşik 1800 → gerçek değere göre kalibre; ban = halt.
- **Ayrı IP bağlama:** `BINANCE_BIND_IP` mainnet için ayrı (testnet ile aynı IP'den ban riskini ayırmak
  mümkün değilse en azından kota farkındalığı).

## 4. Gerçekçilik notu (beklenti yönetimi)
Testnet dolumları iyimser: komisyon (maker 0.02 / taker 0.05), kayma, funding, likidite mainnet'te
gerçek. Boğa penceresinde testnet +832 / 171 işlem ≈ işlem başına +4.9 USDT; mainnet'te aynı
sinyal setinin kenarı daha ince olacak. Bu yüzden ilk faz **küçük sermaye + düşük kaldıraç**,
ve mainnet defteri 2 hafta sonra aynı rejim-bölünmüş tabloyla testnet'le karşılaştırılır.

## 5. Yapılacaklar (sıra)
1. `scripts/deploy.sh --ring mainnet` + `server_deploy.sh` parametreleri (dizin/program/port/secret).
2. ~~Gölge modu (`SCALPER_SHADOW_MODE`) + testleri + harness paritesi gerektirmez (canlı-only).~~
   ✅ YAPILDI (D14, 2026-08-22): `docs/DECISIONS.md` D14, `tests/test_shadow_mode.py` (19 test).
3. ~~Mainnet başlangıç doğrulaması: `RISK_EVENT_SECRET` zorunlu, `TV_WEBHOOK_SECRET` zorunlu,
   allowlist boş olamaz, `SCALPER_ENTRY_HALT_ENABLED=true`.~~ ✅ YAPILDI (D14,
   `Settings._validate_binance_environment`) — `SCALPER_ENTRY_HALT_ENABLED` kontrolü zaten
   vardı; `RISK_EVENT_SECRET`/`TV_WEBHOOK_SECRET`/allowlist zorunluluğu bu turda eklendi,
   TEK istisna `SCALPER_SHADOW_MODE=true` (emir gitmediği için bu üçü henüz kurulu olmasa da
   riske girmez).
4. Ayrı supervisord programı + ayrı port (9092) + ayrı tünel + ayrı Telegram kanalı.
5. TV alarmları: mainnet için ayrı webhook URL'si (`?ring=main` DEĞİL — ayrı secret ve yol `/tv-signal` aynı,
   host/port farklı; 14 Eylül yenilemesiyle birlikte kurulur) + alan adı + TLS.
6. Go/no-go toplantısı: §2'deki 5 madde tek tek işaretlenir; kullanıcı onayı; küçük sermaye.

## 6. Asla
- Mainnet'e `origin/main` ucu deploy etmek; `.env`'i testnet'ten kopyalamak (anahtarlar ayrı);
  gölge modu olmadan yeni parametre; kill-switch secret'sız mainnet; tek oturumda iki halkayı birden değiştirmek.
