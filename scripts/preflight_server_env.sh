#!/usr/bin/env bash
# Deploy ÖNCESİ ön sınav (D43): çalışma ağacının TEMİZ bir kopyasında, sunucunun .env'indeki
# (secret olmayan) ayarlarla `pytest tests` koşturur. Neden: scripts/server_deploy.sh testleri
# sunucuda, sunucunun .env'iyle koşturur; test orada kalırsa rollback() canlı botu yeniden
# başlatır. Yerelde ve CI'da geçip sunucu ayarlarıyla kalan testler yalnız böyle yakalanır
# (2026-11-20: tests/test_bt_maker_fills.py'deki iki test .env'den stop modunu ve dinamik
# kaldıracı devralıyordu). Salt okunur: repoya ve sunucuya HİÇBİR ŞEY yazmaz.
#
# Kullanım: scripts/preflight_server_env.sh [ssh-host] [-- ek pytest argümanları...]
#   scripts/preflight_server_env.sh awa
#   scripts/preflight_server_env.sh awa -- -k maker_fills
#
# Ortam:
#   REMOTE_ENV          sunucudaki .env yolu (varsayılan /opt/tradingbot-v2/.env)
#   PREFLIGHT_ENV_FILE  ssh YERİNE kullanılacak yerel KEY=VALUE dosyası (çevrimdışı kullanım/test)
#   PREFLIGHT_PYTHON    pytest yorumlayıcısı (varsayılan: <repo>/.venv/bin/python, yoksa python3)
#   PREFLIGHT_KEEP=1    geçici kopyayı silme, yolunu yazdır
#   PREFLIGHT_REPO      YALNIZ TESTLER İÇİN: kopyalanacak repo kökü (varsayılan: bu script'in reposu)
#
# Secret kuralı (scripts/ring_env_diff.sh ile AYNI): yalnız BINANCE_/SCALPER_/TV_/RISK_/FOLLOWER_/
# BOT_MODE aileleri alınır; adında SECRET/KEY/TOKEN/PASS/BIND_IP geçen anahtarlar ATILIR. Süzme
# sunucuda, ssh komutunun İÇİNDE yapılır (secret değer sunucudan hiç çıkmaz); gelen veri yerelde
# bir kez daha süzülür. Hiçbir DEĞER yazdırılmaz.
set -euo pipefail

# Repo kökü script'in konumundan türetilir (deploy.sh gibi), çağıranın cwd'sinden değil.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO="${PREFLIGHT_REPO:-$(cd -- "$SCRIPT_DIR/.." && pwd -P)}"

die() { echo "HATA: $*" >&2; exit 2; }

# ── Argümanlar: [ssh-host] [-- ek pytest argümanları] ───────────────────────
HOST="awa"
EXTRA=()
if [ $# -gt 0 ] && [ "$1" != "--" ]; then
  HOST="$1"
  shift
fi
if [ $# -gt 0 ]; then
  [ "$1" = "--" ] || die "beklenmeyen argüman: '$1' (ek pytest argümanları '--' sonrasına yazılır)"
  shift
  EXTRA=("$@")
fi
# Host '-' ile başlayamaz: ssh onu SEÇENEK sanardı (ör. -oProxyCommand=...).
case "$HOST" in
  -*) die "ssh host '-' ile başlayamaz: '$HOST'" ;;
esac

REMOTE_ENV="${REMOTE_ENV:-/opt/tradingbot-v2/.env}"
ENV_FILE="${PREFLIGHT_ENV_FILE:-}"

# Süzgeç düzenli ifadeleri (ring_env_diff.sh ile aynı aileler / aynı secret-adı kuralı).
# Tek tırnak İÇERMEMELİ: uzak komuta tek tırnakla gömülür.
# BÜYÜK/küçük harf FARKSIZ süzülür (grep -i): bot .env'i case_sensitive=False okur (src/core/config.py),
# yani 'SCALPER_api_key' da gerçek bir secret'tır ve asla taşınmamalıdır.
KEY_RE='^(BINANCE_|SCALPER_|TV_|RISK_|FOLLOWER_|BOT_MODE)[A-Za-z0-9_]*='
SECRET_RE='^[A-Za-z0-9_]*(SECRET|KEY|TOKEN|PASS|BIND_IP)[A-Za-z0-9_]*='

# ── Yorumlayıcı ──────────────────────────────────────────────────────────────
if [ -n "${PREFLIGHT_PYTHON:-}" ]; then
  PY="$PREFLIGHT_PYTHON"
elif [ -x "$REPO/.venv/bin/python" ]; then
  PY="$REPO/.venv/bin/python"
else
  PY="python3"
fi

# ── Geçici dizin: yalnız BU koşunun yarattığı dizin silinir ─────────────────
# TMP boşken/ayarsızken ya da sahiplik işareti yokken rm ASLA çalışmaz.
TMP=""
cleanup() {
  if [ -n "${TMP:-}" ] && [ "$TMP" != "/" ] && [ -f "$TMP/.preflight-owned" ]; then
    if [ "${PREFLIGHT_KEEP:-0}" != "1" ]; then
      rm -rf -- "$TMP"
    fi
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

TMP="$(mktemp -d "${TMPDIR:-/tmp}/preflight-server-env.XXXXXX")"
[ -n "$TMP" ] && [ -d "$TMP" ] || die "geçici dizin yaratılamadı"
: > "$TMP/.preflight-owned"
COPY="$TMP/copy"
mkdir "$COPY"

# ── 1) Çalışma ağacını kopyala: izlenen + izlenmeyen-ama-ignore-edilmeyen ───
# .env, .venv, data/, logs/ git-ignore'dadır → kopyaya GİRMEZ; yerel önbellek de yoktur (sunucu gibi).
# Çalışma ağacında silinmiş izlenen dosyalar atlanır.
list_files() {
  git -C "$REPO" ls-files -z -co --exclude-standard | while IFS= read -r -d '' f; do
    [ "$f" = ".env" ] && continue
    if [ -e "$REPO/$f" ] || [ -L "$REPO/$f" ]; then
      printf '%s\0' "$f"
    fi
  done
}
list_files | tar -C "$REPO" --null -T - -cf - | tar -C "$COPY" -xf -
[ -f "$COPY/env.example" ] || die "kopyada env.example yok (repo kökü: $REPO)"

# ── 2) Sunucu ayar satırlarını al (ssh ya da yerel dosya) ───────────────────
RAW="$TMP/raw_settings.txt"
if [ -n "$ENV_FILE" ]; then
  [ -r "$ENV_FILE" ] || die "PREFLIGHT_ENV_FILE okunamadı: $ENV_FILE"
  SRC_DESC="dosya $ENV_FILE"
  # Dosya SÜZÜLMEDEN geçici dizine kopyalanmaz: aşağıda doğrudan kaynaktan süzülür.
  SRC_FILE="$ENV_FILE"
else
  case "$REMOTE_ENV" in
    *\'*) die "REMOTE_ENV tek tırnak içeremez" ;;
  esac
  SRC_DESC="host $HOST ($REMOTE_ENV)"
  # Süzme SUNUCUDA yapılır; son grep eşleşme bulamazsa 1 döner → '|| true' ile sıfır satır = hata değil
  # (sıfır satırı aşağıda ayrıca yakalarız).
  REMOTE_CMD="test -r '$REMOTE_ENV' || { echo 'yok/okunamıyor: $REMOTE_ENV' >&2; exit 3; }; grep -iE '$KEY_RE' '$REMOTE_ENV' | grep -viE '$SECRET_RE' || true"
  rc=0
  ssh -o BatchMode=yes "$HOST" "$REMOTE_CMD" > "$RAW" || rc=$?
  [ "$rc" -eq 0 ] || die "ssh başarısız (host=$HOST, çıkış=$rc) — sunucu .env ayarları alınamadı"
  SRC_FILE="$RAW"
fi

# Savunma katmanı: gelen her şey yerelde yeniden süzülür (CR temizlenir). Değer ASLA yazdırılmaz.
# RAW her zaman geçici dizindeki dosyadır (kullanıcının PREFLIGHT_ENV_FILE'ı ASLA silinmez).
SETTINGS="$TMP/settings.txt"
tr -d '\r' < "$SRC_FILE" | { grep -iE "$KEY_RE" || true; } | { grep -viE "$SECRET_RE" || true; } > "$SETTINGS"
rm -f -- "$RAW"
COUNT="$(wc -l < "$SETTINGS" | tr -d ' ')"
echo "ön sınav kaynağı: $SRC_DESC — $COUNT ayar satırı alındı"
if [ "$COUNT" -eq 0 ]; then
  echo "HATA: hiç ayar satırı alınamadı — boş içe aktarma yanlış yapılandırmayı sınar; pytest ÇALIŞTIRILMADI" >&2
  exit 3
fi

# ── 3) <kopya>/.env = env.example + sunucu satırları + iki sahte secret ─────
{
  cat -- "$COPY/env.example"
  # env.example sonda satır sonu bırakmamışsa ilk ayar satırı yapışmasın
  if [ -s "$COPY/env.example" ] && [ "$(tail -c 1 "$COPY/env.example" | wc -l | tr -d ' ')" -eq 0 ]; then
    printf '\n'
  fi
  cat -- "$SETTINGS"
  printf 'TV_WEBHOOK_SECRET=dummy-preflight\n'
  printf 'RISK_EVENT_SECRET=dummy-preflight\n'
} > "$COPY/.env"

# ── 4) pytest: çağıranın ortamındaki ayar değişkenleri .env'i EZERDİ → temizle ──
# (sunucu adımı da çıplak bir oturumda koşar; kabuğunda dışa aktarılmış SCALPER_* vb. sonucu bozmasın)
for v in $(compgen -e); do
  case "$v" in
    BINANCE_*|SCALPER_*|TV_*|RISK_*|FOLLOWER_*|BOT_MODE*) unset "$v" ;;
    # Dışa aktarılmışsa testler loglarını çağıranın dizinine (belki repoya) yazardı.
    TRADINGBOT_LOG_DIR) unset "$v" ;;
  esac
done
# Repoya (venv editable kurulumu dahil) bayt-kod yazılmasın; kopya zaten atılır.
export PYTHONDONTWRITEBYTECODE=1

if [ "${PREFLIGHT_KEEP:-0}" = "1" ]; then
  echo "geçici kopya KORUNDU: $COPY"
fi
echo "→ pytest tests -q -x -p no:cacheprovider (yorumlayıcı: $PY)"

cd "$COPY"
rc=0
"$PY" -m pytest tests -q -x -p no:cacheprovider ${EXTRA[@]+"${EXTRA[@]}"} || rc=$?

if [ "$rc" -eq 0 ]; then
  echo "ÖN SINAV GEÇTİ: sunucu ayarlarıyla testler yeşil — deploy edilebilir"
else
  echo "ÖN SINAV KALDI (çıkış kodu $rc): deploy ETME — sunucuda aynı test rollback() tetikler"
fi
exit "$rc"
