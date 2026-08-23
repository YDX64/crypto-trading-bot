#!/usr/bin/env bash
# Sunucu tarafı deploy: /opt/tradingbot-v2 içinde çalışır (supervisord program: tradingbot_v2).
# İki halka destekler (bkz. docs/MAINNET_PLAN.md §1): RING=testnet (varsayılan, davranış DEĞİŞMEDİ)
# ve RING=mainnet (yalnız log satırlarında ve aşağıdaki ekstra ön kontrolde etkili).
# Akış: ön kontroller → .env yedeği → hedef commit'e geç → testler → restart → sağlık → başarısızsa GERİ AL.
# Kullanım: scripts/server_deploy.sh [hedef-ref]   (varsayılan: origin/main)
#   DEPLOY_SKIP_TESTS=1   testleri atla (acil geri alma için)
#   DEPLOY_NO_RESTART=1   yalnız kodu güncelle, süreci yeniden başlatma
#   REPO_DIR / PROGRAM / HEALTH_URL   halka için dizin/program/sağlık uç noktası (deploy.sh --ring mainnet ayarlar)
#   RING=testnet|mainnet   yalnız log satırları + mainnet'e özel .env ön kontrolü için (rollback mantığı ortak)
set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/tradingbot-v2}"
PROGRAM="${PROGRAM:-tradingbot_v2}"
TARGET="${1:-origin/main}"
PY="$REPO_DIR/.venv/bin/python"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:9091/api/status}"
RING="${RING:-testnet}"
LOG="$REPO_DIR/logs/deploy.log"
STAMP="$(date -u +%Y%m%d-%H%M%S)"

log() { echo "[$(date -u '+%F %T')] $*" | tee -a "$LOG"; }
die() { log "HATA: $*"; exit 1; }

case "$RING" in
  testnet|mainnet) ;;
  *) die "geçersiz RING: '$RING' (testnet|mainnet olmalı)" ;;
esac

cd "$REPO_DIR" || die "repo dizini yok: $REPO_DIR"
mkdir -p backups logs

# ── Ön kontroller ──────────────────────────────────────────────────────────
[ -f state/scalper_entry_halt.json ] && die "entry-halt aktif — önce nedenini çöz (deploy iptal)"
# Ban kilidi: son 15 dk'da `HTTP 418` ya da `banned` izi varsa deploy/restart YASAK.
# ⚠️ ZAMAN DİLİMİ (düşmanca inceleme bulgusu): `logs/bot.log` damgaları loguru'nun
# `{time:YYYY-MM-DD HH:mm:ss}` biçimidir ve SUNUCUNUN YEREL saatini kullanır
# (src/core/logger.py). Kesim noktası `date -u` ile hesaplanırsa UTC+X bir sunucuda
# pencere "15 dk" değil "15 dk + X saat" olur: saatler önce sönmüş bir ban deploy'u
# kilitlemeye devam eder (UTC−X'te ise pencere kapanır ve AKTİF ban görülmez —
# tehlikeli yön). Bu yüzden kesim noktası da YEREL saatle üretilir; karşılaştırma
# aynı ölçekte iki dize arasındadır. `date -d` GNU coreutils gerektirir (sunucu
# Debian/Ubuntu); yoksa fail-closed davranıp deploy'u reddet.
BAN_SINCE="$(date -d '15 minutes ago' '+%Y-%m-%d %H:%M' 2>/dev/null || true)"
[ -n "$BAN_SINCE" ] || die "ban kilidi hesaplanamadı (date -d desteklenmiyor) — fail-closed, deploy iptal"
if grep -qE 'HTTP 418|banned' <(tail -n 2000 logs/bot.log 2>/dev/null | awk -v s="$BAN_SINCE" '($1" "substr($2,1,5))>=s'); then
  die "son 15 dk'da Binance ban izi var (yerel saat penceresi: >= $BAN_SINCE) — ban aktifken restart YASAK"
fi
[ -n "$(git status --porcelain --untracked-files=no)" ] && die "çalışma ağacında commit'lenmemiş değişiklik var; deploy yalnız temiz ağaçta"

# ── Mainnet'e özel ön kontrol (bkz. docs/MAINNET_PLAN.md §3) ────────────────
if [ "$RING" = "mainnet" ]; then
  [ -f .env ] || die "mainnet ön kontrolü başarısız: .env yok ($REPO_DIR)"
  for KEY in RISK_EVENT_SECRET TV_WEBHOOK_SECRET; do
    VAL="$(grep -E "^${KEY}=" .env | tail -1 | cut -d= -f2-)" || true
    if [ -z "$VAL" ]; then
      die "mainnet ön kontrolü başarısız: .env içinde ${KEY} boş veya yok — mainnet'te zorunlu"
    fi
  done
  if ! grep -qE '^SCALPER_ENTRY_HALT_ENABLED=true$' .env; then
    die "mainnet ön kontrolü başarısız: .env içinde SCALPER_ENTRY_HALT_ENABLED=true olmalı"
  fi
  log "mainnet ön kontrolü geçti: RISK_EVENT_SECRET + TV_WEBHOOK_SECRET dolu, entry-halt aktif"
fi

PREV="$(git rev-parse HEAD)"
if [ "$RING" = "mainnet" ]; then
  log "deploy başlıyor [ring=mainnet, repo=$REPO_DIR, program=$PROGRAM]: $PREV → $TARGET"
else
  log "deploy başlıyor: $PREV → $TARGET"
fi

# ── Yedekler ───────────────────────────────────────────────────────────────
cp .env "backups/env.bak-$STAMP-deploy"
echo "$PREV" > "backups/commit.prev-$STAMP"
log ".env yedeği: backups/env.bak-$STAMP-deploy ; önceki commit: $PREV"

# ── Kodu güncelle ──────────────────────────────────────────────────────────
git fetch -q origin
git checkout -q --detach "$TARGET" 2>/dev/null || git checkout -q "$TARGET"
NEW="$(git rev-parse HEAD)"
if [ "$TARGET" = "origin/main" ]; then git checkout -q -B main origin/main; fi
log "kod: $NEW ($(git log -1 --format=%s | cut -c1-80))"

rollback() {
  log "GERİ ALINIYOR → $PREV"
  git checkout -q -B main "$PREV" || git checkout -q "$PREV"
  cp "backups/env.bak-$STAMP-deploy" .env
  supervisorctl restart "$PROGRAM" >/dev/null || true
  sleep 20
  supervisorctl status "$PROGRAM" | tee -a "$LOG"
  die "deploy başarısız; önceki sürüme dönüldü"
}

# ── Testler (sunucu venv'i ile) ────────────────────────────────────────────
if [ "${DEPLOY_SKIP_TESTS:-0}" != "1" ]; then
  log "testler koşuyor..."
  if ! timeout 300 "$PY" -m pytest tests -q -x -p no:cacheprovider >"logs/deploy-tests-$STAMP.log" 2>&1; then
    tail -15 "logs/deploy-tests-$STAMP.log" | tee -a "$LOG"
    rollback
  fi
  log "testler geçti: $(tail -1 "logs/deploy-tests-$STAMP.log")"
fi

# ── Ayar doğrulaması (.env parse) ──────────────────────────────────────────
"$PY" -c "from src.core.config import settings as s; print('env ok | strategies', s.scalper_strategies, '| divergence', s.scalper_c_require_divergence)" | tee -a "$LOG" || rollback

# ── Restart + sağlık ───────────────────────────────────────────────────────
if [ "${DEPLOY_NO_RESTART:-0}" = "1" ]; then log "restart atlandı (DEPLOY_NO_RESTART=1)"; exit 0; fi
supervisorctl restart "$PROGRAM" | tee -a "$LOG"
# Açılış (Binance init + pozisyon devralma) 1-3 dk sürebilir: süreç RUNNING kaldığı sürece
# portu sabırla yokla; süreç düşerse ya da HEALTH_TIMEOUT dolarsa geri al.
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-240}"
waited=0; healthy=0
while [ "$waited" -lt "$HEALTH_TIMEOUT" ]; do
  if ! supervisorctl status "$PROGRAM" | grep -q RUNNING; then log "süreç RUNNING değil ($(supervisorctl status "$PROGRAM" | awk '{print $2}'))"; rollback; fi
  if curl -fsS -m 5 "$HEALTH_URL" 2>/dev/null | grep -q '"status"'; then healthy=1; break; fi
  sleep 5; waited=$((waited+5))
done
if [ "$healthy" != "1" ]; then log "sağlık uç noktası ${HEALTH_TIMEOUT}s içinde cevap vermedi: $HEALTH_URL"; rollback; fi
PID="$(supervisorctl pid "$PROGRAM")"
if [ "$RING" = "mainnet" ]; then
  log "TAMAM [ring=mainnet]: $PROGRAM RUNNING pid=$PID commit=$NEW (sağlık ${waited}s sonra)"
else
  log "TAMAM: $PROGRAM RUNNING pid=$PID commit=$NEW (sağlık ${waited}s sonra)"
fi
