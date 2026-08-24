---
tags: [ai-icin, dogrulama, kanit, receteler]
guncelleme: 2026-08-24
kaynak: CLAUDE.md kural 6, docs/RUNBOOK.md, docs/EXPERIMENTS.md kanit kurali
---
# Dogrulama receteleri — bir iddiayi nasil kanitlarsin

> **Kural.** Bir satirin kanit sayilmasi icin
> **komut + pencere + env kaynagi + log yolu** gerekir.
> Log/rapor yolu olmayan bir sonuc kanit **degildir**.

## 1. "Kod calisiyor / testler yesil"

```bash
cp env.example .env                    # .env yoksa
python3 -m pytest tests -q
```
**Beklenen cikti:** `2251 passed, 2 skipped`.
Tek dosya: `python3 -m pytest tests/test_vault.py -q`.

## 2. "Bot calisiyor / restart oldu"

```bash
ssh awa 'supervisorctl status tradingbot_v2'
ssh awa 'ps -o etimes= -p $(supervisorctl pid tradingbot_v2)'
```
**Beklenen:** `RUNNING` + kucuk `etimes` (saniye).
⛔ Sadece "restart ettim" demek yeterli **degildir** (CLAUDE.md kural 6).
Acilis ~90 sn surer; saglik yoklamasi 240 sn'ye kadar bekler.

## 3. "Bir ayar gercekten uygulandi"

```bash
ssh awa 'cd /opt/tradingbot-v2 && ./.venv/bin/python -c "from src.core.config import settings as s; print(s.<alan>)"'
```
**`.env`'i grep'lemek yetmez** — pydantic donusumu/validator'lar araya girer.
Ikinci kanit: `/scalper/status` ilgili alani.

## 4. "Kapi gercekten koruyor"

```bash
curl -s localhost:9091/scalper/status | python3 -c 'import json,sys; g=json.load(sys.stdin)["market_gate"]; print(g["enabled"], g["gate_effective"], g.get("stale_reason"), g["thresholds"], g["day_open_source"])'
```
**`enabled=true` KANIT DEGILDIR** — `gate_effective=true` gerekir
([[20-kararlar/D15-lider-kapisi]]).

## 5. "Girisler neden durdu"

```bash
curl -s localhost:9091/scalper/status | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["entries_blocked_by"], d["scan_status"], d["kill_switch_active"], d["entry_halted"])'
```
Karar agaci: [[40-isletme/sorun-giderme]].

## 6. "Bu islem neden kaybetti"

```bash
ssh awa 'curl -s localhost:9091/scalper/trades/<ID>/forensics | python3 -m json.tool'
ssh awa 'cd /opt/tradingbot-v2 && jq -r "select(.trade_id==<ID>)" logs/trades.jsonl'
```
Pano yolu daha hizli: **Son Islemler → satira tikla**.
⚠️ `postmortem` kapanistan ~60 dk sonra dolar; yoklugu "yok" degil
**"henuz olculmedi"** demektir.

## 7. "Bu parametre daha iyi"

```bash
env $(ssh awa grep ^SCALPER_ /opt/tradingbot-v2/.env | xargs) <VAR=deger> \
  python3 -m src.strategies.scalper.backtest --strategies C \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,BNBUSDT,ADAUSDT,LTCUSDT \
  --start <YYYY-MM-DD> --end <YYYY-MM-DD>
```
**Uc pencerede** kos (AYI/YATAY/BOGA), **sirali**, ~4 dk/kosu.
Hukum: [[20-kararlar/P2-karar-kurali]]. Log yolunu rapora yaz.
Sanstan ayirt etmek icin `--permutations N`; cok-varyant taramasinda
`python3 -m src.strategies.scalper.multitest` (q-degeri).

## 8. "Motor davranisi degismedi"

```bash
python3 -m pytest tests/test_golden_backtest.py -q
```
Altin degerler: **2 islem / `total_pnl` 26.77 / `regime_gate` 4**.
Degistiyse degisiklik **davranissaldir** ([[30-deneyler/altin-backtest]]).

## 9. "Halkalar ayrisiyor"

```bash
scripts/ring_env_diff.sh awa
MAIN_ENV=/opt/tradingbot-ap/.env scripts/ring_env_diff.sh awa
```
⚠️ Kapsam disi (`API_PORT`, `DATABASE_URL`, `TELEGRAM_*`, `APP_ENV`,
`LOG_LEVEL`) icin **elle** bak.

## 10. "Iki motor ayni anda calismiyor"

```bash
ssh awa 'supervisorctl status | grep -E "tradingbot_(v2|ap|shadow|main)"'
ssh awa 'pgrep -af "uvicorn.*src\.main:app"'
ssh awa 'grep -n autostart /etc/supervisor/conf.d/tradingbot-v2.conf'
```
Ucuncu komut **tasima oncesi zorunludur**
([[40-isletme/halka-yonetimi]]).

## 11. "Defter kazaniyor"

```bash
python3 scripts/ledger_report.py --since "<baslangic>" --format md
```
"Kazaniyor" **yalniz uc rejimde de dogruysa** soylenir; DOWN gunu yoksa
**iddia edilemez** ([[30-deneyler/canli-defter-rejim-analizi]]).

## 12. "Bir dosya:satir referansi hala dogru"

```bash
sed -n '<SATIR>p' <DOSYA>
python3 -m pytest tests/test_vault.py -q     # kasadaki tum referanslari tarar
```

## 13. Dogrulanamiyorsa

Aynen yaz: **"kodda dogrulanamadi"**. Tahmin uretme.

ILGILI: [[90-ai-icin/calisma-kurallari]] · [[90-ai-icin/sik-yapilan-hatalar]] · [[50-veri/metrikler]] · [[40-isletme/gunluk-kontrol]]
