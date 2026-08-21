#!/usr/bin/env bash
# Sunucu tarafı deploy: /opt/tradingbot-v2 içinde çalışır (supervisord program: tradingbot_v2).
# Akış: ön kontroller → .env yedeği → hedef commit'e geç → testler → restart → sağlık → başarısızsa GERİ AL.
# Kullanım: scripts/server_deploy.sh [hedef-ref]   (varsayılan: origin/main)
#   DEPLOY_SKIP_TESTS=1   testleri atla (acil geri alma için)
#   DEPLOY_NO_RESTART=1   yalnız kodu güncelle, süreci yeniden başlatma
set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/tradingbot-v2}"
PROGRAM="${PROGRAM:-tradingbot_v2}"
TARGET="${1:-origin/main}"
PY="$REPO_DIR/.venv/bin/python"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:9091/api/status}"
LOG="$REPO_DIR/logs/deploy.log"
STAMP="$(date -u +%Y%m%d-%H%M%S)"

log() { echo "[$(date -u '+%F %T')] $*" | tee -a "$LOG"; }
die() { log "HATA: $*"; exit 1; }

cd "$REPO_DIR" || die "repo dizini yok: $REPO_DIR"
mkdir -p backups logs

# ── Ön kontroller ──────────────────────────────────────────────────────────
[ -f state/scalper_entry_halt.json ] && die "entry-halt aktif — önce nedenini çöz (deploy iptal)"
if grep -qE 'HTTP 418|banned' <(tail -n 2000 logs/bot.log 2>/dev/null | awk -v s="$(date -u -d '15 minutes ago' '+%Y-%m-%d %H:%M')" '($1" "substr($2,1,5))>=s'); then
  die "son 15 dk'da Binance ban izi var — ban aktifken restart YASAK"
fi
[ -n "$(git status --porcelain --untracked-files=no)" ] && die "çalışma ağacında commit'lenmemiş değişiklik var; deploy yalnız temiz ağaçta"

PREV="$(git rev-parse HEAD)"
log "deploy başlıyor: $PREV → $TARGET"

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
sleep 30
if ! supervisorctl status "$PROGRAM" | grep -q RUNNING; then rollback; fi
if ! curl -fsS -m 10 "$HEALTH_URL" | grep -q '"status"'; then log "sağlık uç noktası cevap vermedi: $HEALTH_URL"; rollback; fi
PID="$(supervisorctl pid "$PROGRAM")"
log "TAMAM: $PROGRAM RUNNING pid=$PID commit=$NEW"
