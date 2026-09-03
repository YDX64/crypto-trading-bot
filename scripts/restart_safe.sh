#!/usr/bin/env bash
# Güvenli yeniden başlatma — KOD DEĞİŞTİRMEZ, yalnız süreci yeniden başlatır.
#
# NEDEN (düşmanca inceleme, 2026-08-23): docs/RUNBOOK.md'deki `.env` reçeteleri
# (lider kapısı, kline kaynağı, TV olayları, köprü açma/kapama) çıplak
# `supervisorctl restart` ile bitiyordu. O yol `server_deploy.sh`'nin
# ön kontrollerini ATLAR:
#   * Binance ban (HTTP 418) penceresinde restart YASAKTIR (CLAUDE.md kural 3),
#   * entry-halt dosyası varken süreç yeniden başlatılmaz (kilit incelenmeli),
#   * `.env` yedeği alınmadan yapılan bir restart geri alınamaz,
#   * sağlık yoklanmazsa "restart ettim" ile "çalışıyor" karıştırılır.
#
# Kullanım (SUNUCUDA):
#   scripts/restart_safe.sh                 # halka: testnet (varsayılan)
#   scripts/restart_safe.sh follower        # AlgoPro takipçi halkası
#   scripts/restart_safe.sh mainnet
#   RESTART_LABEL=tv-events scripts/restart_safe.sh   # .env yedek etiketi
#
# Halka → dizin/program/sağlık ucu eşlemesi `server_deploy.sh` ile AYNIDIR;
# uyumsuzluk `tests/test_deploy_scripts.py` ile kilitlidir.
set -euo pipefail

RING="${1:-${RING:-testnet}}"
LABEL="${RESTART_LABEL:-restart}"
# Saniye damgası ZORUNLU: aynı gün ikinci bir uygulama, ilk (temiz) yedeği
# EZERSE geri dönülecek nokta kaybolur.
STAMP="$(date -u +%Y%m%d-%H%M%S)"

case "$RING" in
  testnet)
    RING_REPO_DIR="/opt/tradingbot-v2";   RING_PROGRAM="tradingbot_v2"
    RING_HEALTH_URL="http://127.0.0.1:9091/health"
    RING_HALT_FILE="state/scalper_entry_halt.json" ;;
  follower)
    RING_REPO_DIR="/opt/tradingbot-ap";   RING_PROGRAM="tradingbot_ap"
    RING_HEALTH_URL="http://127.0.0.1:9093/health"
    RING_HALT_FILE="state/follower_entry_halt.json" ;;
  mainnet)
    RING_REPO_DIR="/opt/tradingbot-main"; RING_PROGRAM="tradingbot_main"
    RING_HEALTH_URL="http://127.0.0.1:9092/health"
    RING_HALT_FILE="state/scalper_entry_halt.json" ;;
  *)
    echo "HATA: geçersiz halka: '$RING' (testnet|follower|mainnet olmalı)" >&2
    exit 1 ;;
esac

REPO_DIR="${REPO_DIR:-$RING_REPO_DIR}"
PROGRAM="${PROGRAM:-$RING_PROGRAM}"
HEALTH_URL="${HEALTH_URL:-$RING_HEALTH_URL}"
LOG="$REPO_DIR/logs/deploy.log"

log() { echo "[$(date -u '+%F %T')] restart_safe: $*" | tee -a "$LOG"; }
die() { log "HATA: $*"; exit 1; }

if [ "$PROGRAM" != "$RING_PROGRAM" ]; then
  echo "HATA: RING=$RING ile PROGRAM='$PROGRAM' uyuşmuyor (beklenen '$RING_PROGRAM')" >&2
  exit 1
fi
if [ "$HEALTH_URL" != "$RING_HEALTH_URL" ]; then
  echo "HATA: RING=$RING ile HEALTH_URL='$HEALTH_URL' uyuşmuyor (beklenen '$RING_HEALTH_URL')" >&2
  exit 1
fi
if [ "$REPO_DIR" != "$RING_REPO_DIR" ] && [ "${DEPLOY_REPO_DIR_OVERRIDE:-0}" != "1" ]; then
  echo "HATA: RING=$RING ile REPO_DIR='$REPO_DIR' uyuşmuyor (beklenen '$RING_REPO_DIR'); bilinçliyse DEPLOY_REPO_DIR_OVERRIDE=1" >&2
  exit 1
fi

cd "$REPO_DIR" || die "repo dizini yok: $REPO_DIR"
mkdir -p backups logs

# server_deploy.sh ile ayni repo-kapsamli, nonblocking kilit. Ayni anda iki
# restart ya da deploy+restart calisamaz; ikinci islem mevcut olani beklemez.
command -v flock >/dev/null 2>&1 || die "flock bulunamadi — deploy/restart kilidi kurulamiyor (fail-closed)"
exec 9>"$REPO_DIR/logs/deploy-restart.lock" || die "deploy/restart kilit dosyasi acilamadi"
flock -n 9 || die "başka bir deploy/restart işlemi aktif — restart iptal"

# ── Ön kontroller (server_deploy.sh ile AYNI) ──────────────────────────────
ENV_BOT_MODE=""
if [ -f .env ]; then
  # `|| true` ZORUNLU: `set -o pipefail` altında eşleşmeyen `grep` (BOT_MODE
  # satırı olmayan bugünkü scalper .env'i) tüm boru hattını başarısız yapar ve
  # `set -e` script'i SESSİZCE düşürürdü.
  ENV_BOT_MODE="$(grep -E '^[[:space:]]*BOT_MODE[[:space:]]*=' .env | tail -1 | cut -d= -f2- | tr -d '[:space:]"'"'"'' | tr 'A-Z' 'a-z' || true)"
fi
if [ "$RING" = "follower" ]; then
  [ -f .env ] || die "RING=follower ama .env yok ($REPO_DIR)"
  [ "$ENV_BOT_MODE" = "follower" ] || die "RING=follower ama .env'de BOT_MODE=follower yok (bulunan: '${ENV_BOT_MODE:-yok}')"
else
  if [ "$ENV_BOT_MODE" = "follower" ]; then
    die "RING=$RING ama .env BOT_MODE=follower diyor — takipçi halkası için 'restart_safe.sh follower'"
  fi
fi

[ -f "$RING_HALT_FILE" ] && die "entry-halt aktif ($RING_HALT_FILE) — önce nedenini çöz (restart iptal)"
# D20b: GÖMÜLÜ takipçi (FOLLOWER_EMBEDDED=true) scalper halkasının İÇİNDE
# koşar ve KENDİ fail-closed giriş kilidini AYNI dizinde tutar. O kilit varken
# restart serbest bırakılırsa kilidin nedeni incelenmeden pozisyon devralınır
# (CLAUDE.md yasak #3). Dosya yoksa maliyeti sıfırdır.
ENV_FOLLOWER_EMBEDDED=""
if [ -f .env ]; then
  ENV_FOLLOWER_EMBEDDED="$(grep -E '^[[:space:]]*FOLLOWER_EMBEDDED[[:space:]]*=' .env | tail -1 | cut -d= -f2- | tr -d '[:space:]"'"'"'' | tr 'A-Z' 'a-z' || true)"
fi
case "$ENV_FOLLOWER_EMBEDDED" in
  1|true|yes|on)
    [ -f "state/follower_entry_halt.json" ] && die "gömülü takipçi entry-halt aktif (state/follower_entry_halt.json) — önce nedenini çöz (restart iptal)"
    ;;
esac


# Ban kilidi: son 15 dk'da `HTTP 418` ya da `banned` izi varsa restart YASAK.
# Kesim noktası YEREL saatle üretilir (loguru damgaları yerel saattir —
# server_deploy.sh'daki aynı gerekçe).
BAN_SINCE="$(date -d '15 minutes ago' '+%Y-%m-%d %H:%M' 2>/dev/null || true)"
[ -n "$BAN_SINCE" ] || die "ban kilidi hesaplanamadı (date -d desteklenmiyor) — fail-closed, restart iptal"
if grep -qE 'HTTP 418|banned' <(tail -n 2000 logs/bot.log 2>/dev/null | awk -v s="$BAN_SINCE" '($1" "substr($2,1,5))>=s'); then
  die "son 15 dk'da Binance ban izi var (yerel saat penceresi: >= $BAN_SINCE) — ban aktifken restart YASAK"
fi

# ── .env yedeği (geri dönüş noktası) ───────────────────────────────────────
if [ -f .env ]; then
  cp .env "backups/env.bak-$STAMP-$LABEL"
  log ".env yedeği: backups/env.bak-$STAMP-$LABEL"
fi

# ── Ayar doğrulaması: bozuk .env ile restart, süreci ölü bırakır ───────────
PY="$REPO_DIR/.venv/bin/python"
[ -x "$PY" ] || die "venv Python yok: $PY — ayar ve /health JSON'i doğrulanamayacağı için restart YAPILMADI"
"$PY" -c "from src.core.config import settings as s; print('env ok | bot_mode', s.bot_mode)" | tee -a "$LOG" \
  || die ".env parse edilemedi — restart YAPILMADI (yedek: backups/env.bak-$STAMP-$LABEL)"

# ── Restart + sağlık ───────────────────────────────────────────────────────
log "restart [$RING] $PROGRAM"
if ! supervisorctl restart "$PROGRAM" 2>&1 | tee -a "$LOG"; then
  die "supervisorctl restart BAŞARISIZ ($PROGRAM) — .env yedeği: backups/env.bak-$STAMP-$LABEL"
fi

HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-240}"
waited=0; healthy=0
health_is_strictly_healthy() {
  local payload
  payload="$(curl -fsS -m 5 "$HEALTH_URL" 2>/dev/null)" || return 1
  printf '%s' "$payload" | "$PY" -c '
import json
import sys

try:
    body = json.load(sys.stdin)
except (json.JSONDecodeError, UnicodeDecodeError):
    raise SystemExit(1)
raise SystemExit(0 if body.get("status") == "healthy" and body.get("core_healthy") is True else 1)
' >/dev/null 2>&1
}
while [ "$waited" -lt "$HEALTH_TIMEOUT" ]; do
  if ! supervisorctl status "$PROGRAM" | grep -q RUNNING; then
    die "süreç RUNNING değil ($(supervisorctl status "$PROGRAM" | awk '{print $2}')) — .env yedeği: backups/env.bak-$STAMP-$LABEL"
  fi
  if health_is_strictly_healthy; then healthy=1; break; fi
  sleep 5; waited=$((waited+5))
done
[ "$healthy" = "1" ] || die "/health ${HEALTH_TIMEOUT}s içinde status=healthy ve core_healthy=true vermedi: $HEALTH_URL — .env yedeği: backups/env.bak-$STAMP-$LABEL"

PID="$(supervisorctl pid "$PROGRAM" || echo '?')"
log "TAMAM [ring=$RING]: $PROGRAM RUNNING pid=$PID (sağlık ${waited}s sonra)"
