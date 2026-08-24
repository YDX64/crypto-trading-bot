#!/usr/bin/env bash
# TRADINGBOT container başlatıcı — build + up + sağlık + güvenlik kapıları.
#
# NEDEN BU SCRIPT VAR: çıplak `docker compose up -d`, `scripts/restart_safe.sh`
# ve `scripts/server_deploy.sh`'nin ön kontrollerini ATLAR:
#   * entry-halt dosyası varken motor yeniden başlatılmaz (kilit incelenmeli),
#   * Binance ban (HTTP 418) penceresinde restart YASAKTIR (CLAUDE.md kural 3),
#   * AYNI hesaba bağlı İKİNCİ bir motor (supervisord `tradingbot_v2`)
#     çalışıyorsa container BAŞLATILMAZ,
#   * sağlık yoklanmazsa "başlattım" ile "çalışıyor" karıştırılır (kural 6).
#
# Kullanım:
#   scripts/docker_run.sh                 # build + up + sağlık bekle
#   scripts/docker_run.sh --no-build      # yalnız up (mevcut görüntüyle)
#   scripts/docker_run.sh --build-only    # yalnız build (container başlatma)
#   scripts/docker_run.sh --down          # düzgün durdur (stop_grace_period'a saygı)
#   scripts/docker_run.sh --logs          # son logları redaksiyonlu dök
#
# Ortam anahtarları:
#   DOCKER_ALLOW_ALONGSIDE=1   supervisord/port kapısını BİLİNÇLİ olarak geç
#                              (yalnız hedef makinede AYRI bir Binance
#                              hesabının .env'i kullanılıyorsa)
#   DOCKER_PEER_SSH_HOST=awa   ek olarak UZAK bir sunucuda supervisord yokla
#   HEALTH_TIMEOUT=300         sağlık bekleme tavanı (sn)
#   TRADINGBOT_UID / _GID      container kullanıcısı (vars. 10001 = görüntüdeki `bot`)
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

SERVICE="tradingbot"
COMPOSE_PROJECT="${COMPOSE_PROJECT_NAME:-tradingbot}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-300}"
HOST_PORT="${TRADINGBOT_PORT:-9091}"

log()  { echo "[$(date -u '+%F %T')] docker_run: $*"; }
die()  { echo "[$(date -u '+%F %T')] docker_run: HATA: $*" >&2; exit 1; }
warn() { echo "[$(date -u '+%F %T')] docker_run: UYARI: $*" >&2; }

# ── Secret redaksiyonu ──────────────────────────────────────────────────────
# Log dökümü İKİ sızıntı sınıfını maskeler:
#   1) uvicorn erişim logundaki `?secret=<değer>` (CLAUDE.md kural 5),
#   2) ortam/ayar yankısı gibi `ANAHTAR=<değer>` satırları (KEY/SECRET/TOKEN/PASS).
# Kurallar BÜYÜK/küçük harf DUYARSIZ (`I` bayrağı) ve değerin başındaki tek/çift
# tırnağı da yutar — düşmanca inceleme, maskelenmeyen sekiz örnek buldu:
# lowercase JSON (`"secret": "x"`), tırnaklı değer (`KEY="x"`), tek tırnak,
# `x-mbx-apikey:` (küçük harf başlık), `&signature=…`, `password=…`.
# ⚠️ SINIR: `sed` SATIR BAZLIDIR — çok satırlı JSON (`"apiSecret":\n  "x"`)
# maskelenemez. Bu yüzden redaksiyon SON savunmadır, tek savunma değil:
# asıl kapı uygulamadaki `_SecretRedactionLogFilter`tır (src/main.py).
redact() {
  sed -E \
    -e 's/([Ss][Ee][Cc][Rr][Ee][Tt]=)[^\&[:space:]"'"'"']+/\1***/g' \
    -e 's/(secret=)[^\&[:space:]"'"'"']+/\1***/gI' \
    -e 's/([A-Za-z0-9_-]*(KEY|SECRET|TOKEN|PASSWORD|PASSWD|PASS|SIGNATURE|APIKEY)[A-Za-z0-9_-]*"?[[:space:]]*[=:][[:space:]]*"?'"'"'?)[^[:space:],}"'"'"']+/\1***/gI' \
    -e 's/(Bearer[[:space:]]+)[A-Za-z0-9._-]+/\1***/gI'
}

# ── docker compose ikilisi ──────────────────────────────────────────────────
if docker compose version >/dev/null 2>&1; then
  DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  DC=(docker-compose)
else
  die "docker compose bulunamadı (docker compose v2 ya da docker-compose gerekli)"
fi
docker info >/dev/null 2>&1 || die "docker daemon çalışmıyor (docker info başarısız)"

MODE="up"
BUILD=1
while [ $# -gt 0 ]; do
  case "$1" in
    --no-build)   BUILD=0 ;;
    --build-only) MODE="build-only" ;;
    --down)       MODE="down" ;;
    --logs)       MODE="logs" ;;
    *) die "bilinmeyen argüman: '$1' (--no-build|--build-only|--down|--logs)" ;;
  esac
  shift
done

dump_logs() {
  echo "───────── container loglari (secret redaksiyonlu, son 200 satir) ─────────" >&2
  "${DC[@]}" -p "$COMPOSE_PROJECT" logs --tail=200 "$SERVICE" 2>&1 | redact >&2 || true
  echo "──────────────────────────────────────────────────────────────────────────" >&2
}

if [ "$MODE" = "logs" ]; then
  dump_logs
  exit 0
fi

if [ "$MODE" = "down" ]; then
  # `stop` + `down` sırası bilinçlidir: `stop` stop_grace_period'a saygı duyar,
  # motorun bekleyen MAKER girişlerini iptal etmesine zaman tanır.
  # `--profile follower` DAHİL EDİLİR: takipçi container'ı çalışıyorsa çıplak
  # `down` onu docker'ın 10 sn'lik VARSAYILANIYLA öldürür ve `stop_grace_period`
  # devre dışı kalır → iptal edilmemiş LIMIT emirleri borsada asılı kalır.
  log "durduruluyor (graceful, stop_grace_period'a kadar beklenir)..."
  "${DC[@]}" -p "$COMPOSE_PROJECT" --profile follower stop -t 120 || true
  "${DC[@]}" -p "$COMPOSE_PROJECT" --profile follower down -t 120
  log "TAMAM: container durduruldu (kalıcı veri state/ logs/ backups/ data/ içinde durur)"
  exit 0
fi

# ═══════════════════════════════════════════════════════════════════════════
# GÜVENLİK KAPILARI (restart_safe.sh ile aynı mantık)
# ═══════════════════════════════════════════════════════════════════════════

# ── Kapı 0: .env mount edilebilir olmalı (görüntüye gömülü DEĞİL) ───────────
[ -f .env ] || die ".env yok ($REPO_DIR/.env) — container onu MOUNT eder, görüntüye gömmez. Sunucudan elle taşıyın (docs/RUNBOOK.md)."

ENV_GET() {  # ENV_GET <ANAHTAR> → küçük harfe indirgenmiş değer ("" = yok)
  # `|| true` ZORUNLU: pipefail altında eşleşmeyen grep tüm boruyu düşürür ve
  # `set -e` script'i SESSİZCE öldürürdü (restart_safe.sh'daki aynı tuzak).
  grep -E "^[[:space:]]*$1[[:space:]]*=" .env | tail -1 | cut -d= -f2- \
    | tr -d '[:space:]"'"'"'' | tr 'A-Z' 'a-z' || true
}

# ── Kapı 1: İKİ MOTOR AYNI HESAPTA ─────────────────────────────────────────
# supervisord programı çalışıyorsa container AYNI pozisyonları ikinci kez
# yönetmeye başlar (çift SL/TP, yarışan devralma, state dosyalarında
# son-yazan-kazanır). D20b incelemesindeki kritik sınıfın birebir aynısı.
# İKİ BAĞIMSIZ SİNYAL kullanılır. Düşmanca inceleme, tek başına supervisorctl
# yoklamasının FAIL-OPEN olduğunu ölçtü: supervisorctl PATH'te yoksa, sokete
# yetki yoksa, supervisord başka kullanıcıdaysa ya da program adı farklıysa
# ("tradingbot-v2"), kapı "TEMİZ" deyip container'ı BAŞLATIYORDU. Motor
# systemd/nohup ile koşuyorsa supervisorctl zaten hiçbir şey göstermez.
# Bu yüzden ikinci sinyal doğrudan SÜRECİN KENDİSİDİR.
check_engine_running() {
  local hits=""
  if command -v supervisorctl >/dev/null 2>&1; then
    local out
    out="$(supervisorctl status 2>/dev/null || true)"
    if [ -n "$out" ]; then
      hits="$(printf '%s\n' "$out" | grep -iE '^tradingbot[_-]' | grep RUNNING || true)"
    fi
  fi
  # İkinci sinyal: bu makinede uvicorn ile koşan bir `src.main:app` var mı?
  # `docker` süreçleri hariç tutulur (bizim container'ımızın uvicorn'u host
  # süreç tablosunda görünmez, ama --network host senaryosunda dikkatli ol).
  if command -v pgrep >/dev/null 2>&1; then
    local procs
    procs="$(pgrep -af 'uvicorn.*src\.main:app' 2>/dev/null | grep -v 'docker' || true)"
    [ -n "$procs" ] && hits="$hits${hits:+$'\n'}$procs"
  fi
  if [ -n "$hits" ]; then
    echo "$hits"
    return 1
  fi
  return 0
}

# Canlı halkaların dizinlerinde ASLA çalışma: aşağıdaki `chown -R` canlı
# `state/` + `logs/` sahipliğini supervisord kullanıcısının altından alırdı.
case "$REPO_DIR" in
  /opt/tradingbot-v2|/opt/tradingbot-ap|/opt/tradingbot-main|/opt/tradingbot-*/*)
    die "bu script CANLI halka dizininde ($REPO_DIR) çalıştırılamaz — container yolu AYRI bir çalışma kopyası kullanır (docs/RUNBOOK.md taşıma reçetesi adım 3)" ;;
esac

if [ "${DOCKER_ALLOW_ALONGSIDE:-0}" = "1" ]; then
  warn "DOCKER_ALLOW_ALONGSIDE=1 — supervisord/port kapısı BİLİNÇLİ olarak atlandı."
  warn "Bu YALNIZ container AYRI bir Binance hesabının .env'ini kullanıyorsa güvenlidir."
else
  if ! LOCAL_RUNNING="$(check_engine_running)"; then
    echo "" >&2
    echo "DUR: BU MAKİNEDE ZATEN BİR TRADING MOTORU ÇALIŞIYOR" >&2
    echo "$LOCAL_RUNNING" >&2
    echo "" >&2
    echo "SUPERVISORD YOLU İLE CONTAINER YOLU AYNI ANDA ÇALIŞTIRILAMAZ." >&2
    echo "İkisi AYNI Binance hesabına bağlanır ve AYNI pozisyonları İKİ motor" >&2
    echo "yönetir: çift SL/TP, çift kapanış, yarışan pozisyon devralma," >&2
    echo "state/*.json dosyalarında son-yazan-kazanır." >&2
    echo "" >&2
    echo "Yapılacak: önce supervisord programını durdurun" >&2
    echo "  supervisorctl stop tradingbot_v2" >&2
    echo "sonra bu script'i tekrar çalıştırın." >&2
    echo "Container AYRI bir Binance hesabı kullanıyorsa: DOCKER_ALLOW_ALONGSIDE=1" >&2
    exit 1
  fi

  # Uzak sunucu yoklaması (opsiyonel): kod başka makinede container'a taşınırken
  # eski sunucudaki süreç UNUTULMUŞ olabilir.
  if [ -n "${DOCKER_PEER_SSH_HOST:-}" ]; then
    PEER_RUNNING="$(ssh -o BatchMode=yes -o ConnectTimeout=10 "$DOCKER_PEER_SSH_HOST" \
      "supervisorctl status 2>/dev/null | grep -E '^(tradingbot_v2|tradingbot_ap|tradingbot_main)[[:space:]]' | grep RUNNING || true" 2>/dev/null || echo "__SSH_FAIL__")"
    if [ "$PEER_RUNNING" = "__SSH_FAIL__" ]; then
      # Fail-closed: yoklayamadığımız bir sunucuda motor ÇALIŞIYOR OLABİLİR.
      die "DOCKER_PEER_SSH_HOST='$DOCKER_PEER_SSH_HOST' yoklanamadı (ssh başarısız) — fail-closed, başlatma iptal. Elle doğrulayın ya da DOCKER_ALLOW_ALONGSIDE=1."
    fi
    if [ -n "$PEER_RUNNING" ]; then
      echo "" >&2
      echo "DUR: UZAK SUNUCUDA ($DOCKER_PEER_SSH_HOST) MOTOR ÇALIŞIYOR" >&2
      echo "$PEER_RUNNING" >&2
      echo "Önce orada durdurun: ssh $DOCKER_PEER_SSH_HOST 'supervisorctl stop tradingbot_v2'" >&2
      exit 1
    fi
    log "uzak sunucu temiz: $DOCKER_PEER_SSH_HOST"
  fi

  # Port zaten dinleniyorsa: supervisorctl olmayan bir makinede de ikinci
  # motoru yakalayan ikinci bir işaret (container kendi portunu kullanır).
  if command -v lsof >/dev/null 2>&1; then
    if lsof -nP -iTCP:"$HOST_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
      if ! docker ps --format '{{.Names}} {{.Ports}}' 2>/dev/null | grep -q ":$HOST_PORT->"; then
        die "port $HOST_PORT zaten dinleniyor ve sahibi bu compose projesi DEĞİL — ikinci motor olabilir (bilinçliyse DOCKER_ALLOW_ALONGSIDE=1)"
      fi
    fi
  fi
fi

# ── Kapı 2: entry-halt (fail-closed giriş kilidi) ───────────────────────────
# Kilit VARKEN başlatmak, kilidin nedeni (yetim/korumasız pozisyon)
# incelenmeden pozisyonun devralınması demektir — CLAUDE.md yasak #3.
[ -f "state/scalper_entry_halt.json" ] && \
  die "entry-halt aktif (state/scalper_entry_halt.json) — önce nedenini çöz (başlatma iptal)"

# D20b: gömülü takipçi AYNI dizinde KENDİ kilidini tutar.
case "$(ENV_GET FOLLOWER_EMBEDDED)" in
  1|true|yes|on)
    [ -f "state/follower_entry_halt.json" ] && \
      die "gömülü takipçi entry-halt aktif (state/follower_entry_halt.json) — önce nedenini çöz (başlatma iptal)"
    ;;
esac

# ── Kapı 3: Binance ban (HTTP 418) penceresi ────────────────────────────────
# ⚠️ ZAMAN DİLİMİ — BURASI `restart_safe.sh`TEN BİLİNÇLİ OLARAK AYRILIR.
# `restart_safe.sh`/`server_deploy.sh` kesim noktasını YEREL saatle üretir,
# çünkü orada logu YAZAN da OKUYAN da aynı TZ'dedir (supervisord süreci
# sunucunun TZ'sini kullanır). Container yolunda bu varsayım BOZULUR:
# `Dockerfile` `TZ=UTC` sabitler, yani logs/bot.log damgaları **UTC**'dir;
# host ise UTC+2 olabilir. Yerel kesim noktası kullanılsaydı pencere
# "15 dk" değil "15 dk − 2 saat" olurdu ve AKTİF bir ban SESSİZCE
# görülmezdi (ölçüldü: taze bir `HTTP 418` satırı CEST host'ta filtreden
# 0 satır geçirdi → kapı sessizce devre dışı). CLAUDE.md kural 3 ihlali.
# Bu yüzden kesim noktası UTC üretilir; container'ın yazdığı damgalarla
# AYNI ölçekte karşılaştırılır.
if [ -f logs/bot.log ]; then
  BAN_SINCE="$(date -u -d '15 minutes ago' '+%Y-%m-%d %H:%M' 2>/dev/null \
            || date -u -v-15M '+%Y-%m-%d %H:%M' 2>/dev/null || true)"
  [ -n "$BAN_SINCE" ] || die "ban kilidi hesaplanamadı (date -u -d/-v desteklenmiyor) — fail-closed, başlatma iptal"
  if tail -n 2000 logs/bot.log 2>/dev/null \
     | awk -v s="$BAN_SINCE" '($1" "substr($2,1,5))>=s' \
     | grep -qE 'HTTP 418|banned'; then
    die "son 15 dk'da Binance ban izi var (UTC penceresi: >= $BAN_SINCE) — ban aktifken restart YASAK"
  fi
  # ⚠️ TAŞIMA NOTU: eski sunucudan gelen bot.log damgaları O SUNUCUNUN yerel
  # saatiyle yazılmıştır. Karışık bir dosyada bu kapı güvenilmezdir — taşıma
  # sonrası ilk başlatmada ban durumunu KAYNAK sunucuda elle doğrulayın.
else
  warn "logs/bot.log yok → 418 ban penceresi DOĞRULANAMADI (taze kurulumda normal)."
  warn "Kaynak sunucu hâlâ ayaktaysa orada doğrulayın ya da DOCKER_PEER_SSH_HOST kullanın."
fi

# ── Kapı 4: taşıma tuzakları (uyarı; başlatmayı engellemez) ─────────────────
BIND_IP="$(ENV_GET BINANCE_BIND_IP)"
if [ -n "$BIND_IP" ] && ! grep -qE '^[[:space:]]*network_mode:[[:space:]]*host' docker-compose.yml; then
  warn "BINANCE_BIND_IP='$BIND_IP' dolu ama container BRIDGE ağında."
  warn "Container'ın ağ ad alanında o yerel IP YOKTUR → Binance REST soketi"
  warn "EADDRNOTAVAIL ile düşebilir. Ya .env'de BINANCE_BIND_IP'i boşaltın,"
  warn "ya da (yalnız Linux) docker-compose.yml'de 'network_mode: host' açın."
fi
if [ "$(ENV_GET BOT_MODE)" = "follower" ]; then
  die "'.env' BOT_MODE=follower diyor — bu, AYRI takipçi halkasının .env'idir. Takipçi için: docker compose --profile follower up -d (.env.follower ile)"
fi
if [ "$(ENV_GET BINANCE_TESTNET)" = "false" ] || [ "$(ENV_GET ALLOW_MAINNET)" = "true" ]; then
  warn ".env MAINNET (GERÇEK PARA) işaret ediyor. Devam ediyorsanız bunu bilerek yapın."
fi

# ── Kalıcı dizinler + sahiplik ──────────────────────────────────────────────
mkdir -p state logs backups data data/klines_cache
UID_WANT="${TRADINGBOT_UID:-10001}"
GID_WANT="${TRADINGBOT_GID:-10001}"
export TRADINGBOT_UID="$UID_WANT" TRADINGBOT_GID="$GID_WANT"
if [ "$UID_WANT" = "10001" ]; then
  # Görüntüdeki `bot` kullanıcısı yazabilsin. chown yetkisi yoksa (root
  # değiliz ve host chown'a izin vermiyor) host kullanıcısına düş.
  if ! chown -R "$UID_WANT:$GID_WANT" state logs backups data 2>/dev/null; then
    warn "chown $UID_WANT:$GID_WANT başarısız — container host kullanıcınızla ($(id -u):$(id -g)) koşacak."
    export TRADINGBOT_UID="$(id -u)" TRADINGBOT_GID="$(id -g)"
  fi
fi
log "container kullanıcısı: ${TRADINGBOT_UID}:${TRADINGBOT_GID} (root DEĞİL)"

# `.env` HEDEF UID TARAFINDAN OKUNABİLİR OLMALI.
# Tuzak (düşmanca inceleme): RUNBOOK `chmod 600 .env` diyor. Dosya root'a
# aitse ve container uid 10001 ile koşuyorsa `.env` OKUNAMAZ; `settings`
# modül düzeyinde kurulduğu için uygulama IMPORT'ta ValidationError ile ölür
# ve `restart: unless-stopped` sonsuz bir çökme döngüsü kurar.
# Çözüm: sahipliği/grubu container uid'sine ver, 640 yeterlidir.
if [ ! -O .env ] || [ "$(stat -f '%u' .env 2>/dev/null || stat -c '%u' .env 2>/dev/null)" != "$TRADINGBOT_UID" ]; then
  chown "$TRADINGBOT_UID:$TRADINGBOT_GID" .env 2>/dev/null \
    || chgrp "$TRADINGBOT_GID" .env 2>/dev/null \
    || warn ".env sahipliği ayarlanamadı — container uid $TRADINGBOT_UID dosyayı okuyamayabilir"
fi
chmod 640 .env 2>/dev/null || true
ENV_MODE="$(stat -f '%Sp %Su:%Sg' .env 2>/dev/null || stat -c '%A %U:%G' .env 2>/dev/null || echo '?')"
log ".env izinleri: $ENV_MODE (container uid ${TRADINGBOT_UID} okuyabilmeli)"

# ═══════════════════════════════════════════════════════════════════════════
# BUILD + UP + SAĞLIK
# ═══════════════════════════════════════════════════════════════════════════
if [ "$BUILD" = "1" ] || [ "$MODE" = "build-only" ]; then
  log "görüntü derleniyor..."
  "${DC[@]}" -p "$COMPOSE_PROJECT" build "$SERVICE" || die "docker build başarısız"
  log "derleme TAMAM"
fi
if [ "$MODE" = "build-only" ]; then exit 0; fi

log "container başlatılıyor (port $HOST_PORT)..."
"${DC[@]}" -p "$COMPOSE_PROJECT" up -d "$SERVICE" || { dump_logs; die "docker compose up başarısız"; }

# `.env` mount'u GERÇEKTEN dosya olarak indi mi? Docker, kaynak yol daemon
# tarafından görülemiyorsa (uzak/VM daemon, paylaşılmayan dizin) SESSİZCE BOŞ
# BİR DİZİN yaratır; uygulama o zaman import'ta ValidationError ile ölür ve
# `restart: unless-stopped` sonsuz döngü kurar. Erken ve NET hata ver.
if ! "${DC[@]}" -p "$COMPOSE_PROJECT" exec -T "$SERVICE" test -s /app/.env 2>/dev/null; then
  warn "container içinde /app/.env okunabilir bir DOSYA değil (boş ya da dizin)."
  warn "Neden: docker daemon '$REPO_DIR/.env' yolunu göremiyor olabilir"
  warn "(uzak daemon ya da VM'e paylaşılmayan dizin). Repo'yu daemon'ın"
  warn "paylaştığı bir yola taşıyın ya da mount kaynağını düzeltin."
  dump_logs
  die ".env mount'u başarısız — motor başlatılmadı"
fi

log "sağlık yoklanıyor (en fazla ${HEALTH_TIMEOUT}s; açılış ~90 sn sürebilir)..."
# ⚠️ Sağlık, container'ın İÇİNDEN yoklanır (`exec` + `curl`), host portundan
# DEĞİL. Neden: host portunda BAŞKA bir motor (supervisord botu ya da eski bir
# container) dinliyorsa onun `/health` cevabı bu container'ı sağlıklı
# gösterirdi — "TAMAM" yazan ama bozuk bir container. `exec` yolunda cevap
# kesinlikle BU container'dan gelir.
probe_health() {  # stdout: gövde; çıkış kodu: 0 = HTTP cevabı alındı
  "${DC[@]}" -p "$COMPOSE_PROJECT" exec -T "$SERVICE" \
    curl -sS -m 5 -w '\nHTTP_CODE=%{http_code}' http://127.0.0.1:9091/health 2>/dev/null
}

waited=0; healthy=0; BODY=""
while [ "$waited" -lt "$HEALTH_TIMEOUT" ]; do
  CID="$( "${DC[@]}" -p "$COMPOSE_PROJECT" ps -q "$SERVICE" 2>/dev/null || true )"
  STATE="missing"
  if [ -n "$CID" ]; then
    STATE="$(docker inspect -f '{{.State.Status}}' "$CID" 2>/dev/null || echo missing)"
  fi
  if [ "$STATE" != "running" ]; then
    dump_logs
    die "container 'running' değil (durum: $STATE) — yukarıdaki loglara bakın"
  fi
  BODY="$(probe_health || true)"
  if printf '%s' "$BODY" | grep -q '"status"'; then healthy=1; break; fi
  sleep 5; waited=$((waited+5))
done

if [ "$healthy" != "1" ]; then
  warn "container ÇALIŞIYOR ama /health ${HEALTH_TIMEOUT}s içinde cevap vermedi."
  warn "Container DURDURULMADI (açık pozisyon olabilir). İncelemek için:"
  warn "  scripts/docker_run.sh --logs      # redaksiyonlu log"
  warn "  scripts/docker_run.sh --down      # graceful durdur"
  dump_logs
  exit 1
fi

# HTTP 503 = degraded ama AYAKTA. "Cevap yok" ile aynı şey DEĞİLDİR; ayır.
HTTP_CODE="$(printf '%s' "$BODY" | sed -n 's/.*HTTP_CODE=\([0-9]*\).*/\1/p' | tail -1)"
if [ "${HTTP_CODE:-000}" != "200" ]; then
  warn "uygulama AYAKTA ama /health HTTP ${HTTP_CODE:-?} (DEGRADED) döndürüyor:"
  printf '%s\n' "$BODY" | redact | head -20 >&2
  warn "Genelde eksik/geçersiz Binance anahtarı ya da ağ erişimi demektir."
  warn "Container ÇALIŞMAYA DEVAM EDİYOR — bilinçli karar verin."
  exit 1
fi

log "TAMAM: container sağlıklı (${waited}s sonra) — http://127.0.0.1:${HOST_PORT}/dashboard"
log "Testleri container İÇİNDE koşmak (deploy kapısı):"
log "  ${DC[*]} -p $COMPOSE_PROJECT exec $SERVICE python -m pytest tests -q -p no:cacheprovider"
