#!/usr/bin/env bash
# Sunucu tarafı deploy: /opt/tradingbot-v2 içinde çalışır (supervisord program: tradingbot_v2).
# Üç halka destekler (bkz. docs/MAINNET_PLAN.md §1, docs/RUNBOOK.md "AlgoPro takipçi halkası"):
# RING=testnet (varsayılan, davranış DEĞİŞMEDİ), RING=follower (/opt/tradingbot-ap,
# tradingbot_ap, port 9093) ve RING=mainnet (ek .env ön kontrolü).
# Akış: ön kontroller → .env yedeği → hedef commit'e geç → testler → restart → sağlık → başarısızsa GERİ AL.
# Kullanım: scripts/server_deploy.sh [hedef-ref]   (varsayılan: origin/main)
#   DEPLOY_SKIP_TESTS=1   testleri atla (acil geri alma için)
#   DEPLOY_NO_RESTART=1   yalnız kodu güncelle, süreci yeniden başlatma
#   RING=testnet|follower|mainnet   HALKA. REPO_DIR/PROGRAM/HEALTH_URL/HALT_FILE
#                                   ve .env BOT_MODE beklentisi BUNDAN TÜRER.
#   REPO_DIR / PROGRAM / HEALTH_URL   açık override — halka ile TUTARLI olmalı;
#                                   PROGRAM/HEALTH_URL uyuşmazlığı = HATA,
#                                   REPO_DIR uyuşmazlığı için DEPLOY_REPO_DIR_OVERRIDE=1
#                                   (yalnız test koşumu / kurtarma)
set -euo pipefail

RING="${RING:-testnet}"
TARGET="${1:-origin/main}"
STAMP="$(date -u +%Y%m%d-%H%M%S)"

# ── Halka → dizin/program/sağlık uç noktası (TEK GERÇEK KAYNAK) ────────────
# Düşmanca inceleme (2026-08-23): RING yalnız log satırlarını ve HALT_FILE
# adını etkiliyordu; REPO_DIR/PROGRAM/HEALTH_URL bağımsız env'lerdi. Sonuç:
# `RING=follower` ile scalper programı (`tradingbot_v2`) yeniden başlatılabilir
# ve o halkanın entry-halt kilidi ATLANABİLİRDİ. Artık üçü de halkadan türer;
# açık override yalnız halka ile TUTARLIYSA kabul edilir.
case "$RING" in
  testnet)
    RING_REPO_DIR="/opt/tradingbot-v2";   RING_PROGRAM="tradingbot_v2"
    RING_HEALTH_URL="http://127.0.0.1:9091/api/status"
    RING_HALT_FILE="state/scalper_entry_halt.json" ;;
  follower)
    RING_REPO_DIR="/opt/tradingbot-ap";   RING_PROGRAM="tradingbot_ap"
    RING_HEALTH_URL="http://127.0.0.1:9093/api/status"
    RING_HALT_FILE="state/follower_entry_halt.json" ;;
  mainnet)
    RING_REPO_DIR="/opt/tradingbot-main"; RING_PROGRAM="tradingbot_main"
    RING_HEALTH_URL="http://127.0.0.1:9092/api/status"
    RING_HALT_FILE="state/scalper_entry_halt.json" ;;
  *)
    echo "HATA: geçersiz RING: '$RING' (testnet|follower|mainnet olmalı)" >&2
    exit 1 ;;
esac

REPO_DIR="${REPO_DIR:-$RING_REPO_DIR}"
PROGRAM="${PROGRAM:-$RING_PROGRAM}"
HEALTH_URL="${HEALTH_URL:-$RING_HEALTH_URL}"
PY="$REPO_DIR/.venv/bin/python"
LOG="$REPO_DIR/logs/deploy.log"

log() { echo "[$(date -u '+%F %T')] $*" | tee -a "$LOG"; }
die() { log "HATA: $*"; exit 1; }

# Yeniden başlatılacak PROGRAM ve yoklanacak SAĞLIK UCU halkaya kilitlidir:
# yanlış program = yanlış motoru yeniden başlatmak.
if [ "$PROGRAM" != "$RING_PROGRAM" ]; then
  echo "HATA: RING=$RING ile PROGRAM='$PROGRAM' uyuşmuyor (beklenen '$RING_PROGRAM')" >&2
  exit 1
fi
if [ "$HEALTH_URL" != "$RING_HEALTH_URL" ]; then
  echo "HATA: RING=$RING ile HEALTH_URL='$HEALTH_URL' uyuşmuyor (beklenen '$RING_HEALTH_URL')" >&2
  exit 1
fi
# REPO_DIR farklıysa BİLİNÇLİ onay şart (test koşumu / kurtarma).
if [ "$REPO_DIR" != "$RING_REPO_DIR" ] && [ "${DEPLOY_REPO_DIR_OVERRIDE:-0}" != "1" ]; then
  echo "HATA: RING=$RING ile REPO_DIR='$REPO_DIR' uyuşmuyor (beklenen '$RING_REPO_DIR'); bilinçliyse DEPLOY_REPO_DIR_OVERRIDE=1" >&2
  exit 1
fi

cd "$REPO_DIR" || die "repo dizini yok: $REPO_DIR"
mkdir -p backups logs

# ── Ön kontroller ──────────────────────────────────────────────────────────
# Halka ↔ BOT_MODE bağı (D20a bulgu 4): `.env` hangi motoru başlatacağını
# söyler. `RING=testnet` ama `.env`'de `BOT_MODE=follower` ise deploy,
# scalper halkası sanılan bir dizinde TAKİPÇİ motorunu yeniden başlatır
# (ve tersi: takipçi halkasına scalper .env'i ile deploy, AlgoPro
# olaylarını hiç işlemeyen bir süreç bırakır). Fail-closed: uyuşmazlıkta
# deploy YAPILMAZ.
ENV_BOT_MODE=""
if [ -f .env ]; then
  # `|| true` ZORUNLU: `set -o pipefail` altında eşleşmeyen `grep` (BOT_MODE
  # satırı olmayan bugünkü scalper .env'i) tüm boru hattını başarısız yapar ve
  # `set -e` script'i SESSİZCE düşürürdü.
  ENV_BOT_MODE="$(grep -E '^[[:space:]]*BOT_MODE[[:space:]]*=' .env | tail -1 | cut -d= -f2- | tr -d '[:space:]"'"'"'' | tr 'A-Z' 'a-z' || true)"
fi
if [ "$RING" = "follower" ]; then
  [ -f .env ] || die "RING=follower ama .env yok ($REPO_DIR) — BOT_MODE doğrulanamıyor"
  [ "$ENV_BOT_MODE" = "follower" ] || die "RING=follower ama .env'de BOT_MODE=follower yok (bulunan: '${ENV_BOT_MODE:-yok}') — yanlış dizine deploy ediliyor olabilir"
else
  if [ "$ENV_BOT_MODE" = "follower" ]; then
    die "RING=$RING ama .env BOT_MODE=follower diyor — takipçi halkasına 'RING=follower' ile deploy edilir (scripts/deploy.sh --ring follower)"
  fi
fi

# Takipçi halkasının (D20) giriş kilidi AYRI dosyadadır; scalper/mainnet
# halkalarında dosya adı ve davranış DEĞİŞMEDİ.
HALT_FILE="$RING_HALT_FILE"
[ -f "$HALT_FILE" ] && die "entry-halt aktif ($HALT_FILE) — önce nedenini çöz (deploy iptal)"
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
if [ "$RING" = "testnet" ]; then
  log "deploy başlıyor: $PREV → $TARGET"
else
  log "deploy başlıyor [ring=$RING, repo=$REPO_DIR, program=$PROGRAM]: $PREV → $TARGET"
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
  # Süreç oturması için bekleme; testler bunu kısaltır (varsayılan DEĞİŞMEDİ).
  sleep "${ROLLBACK_SETTLE_SECONDS:-20}"
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
# RESTART HATASI DA GERİ ALINIR (düşmanca inceleme): `set -e` + `pipefail`
# altında çıplak `supervisorctl restart | tee` başarısız olursa script
# rollback'i ÇAĞIRMADAN ölürdü — sunucuda YENİ kod, ÇALIŞMAYAN süreç kalırdı.
if ! supervisorctl restart "$PROGRAM" 2>&1 | tee -a "$LOG"; then
  log "supervisorctl restart BAŞARISIZ ($PROGRAM) — geri alınıyor"
  rollback
fi
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
PID="$(supervisorctl pid "$PROGRAM" || echo '?')"
if [ "$RING" = "testnet" ]; then
  log "TAMAM: $PROGRAM RUNNING pid=$PID commit=$NEW (sağlık ${waited}s sonra)"
else
  log "TAMAM [ring=$RING]: $PROGRAM RUNNING pid=$PID commit=$NEW (sağlık ${waited}s sonra)"
fi
