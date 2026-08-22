#!/usr/bin/env bash
# Yerelden deploy: push edilmiş GitHub kodunu sunucuya uygular (iki halka: testnet | mainnet).
# Kullanım: scripts/deploy.sh [ssh-host] [hedef-ref] [--ring testnet|mainnet]
#   scripts/deploy.sh                          → awa, origin/main, testnet halkası (varsayılan)
#   scripts/deploy.sh awa v1.2.0                → belirli etiket/commit, testnet halkası
#   scripts/deploy.sh awa v1.2.0 --ring mainnet → mainnet halkası (bkz. aşağı)
# Geri alma: scripts/deploy.sh awa <önceki-commit>   (backups/commit.prev-* dosyalarında)
#
# Mainnet halkası (bkz. docs/MAINNET_PLAN.md §1, §5):
#   - hedef MUTLAKA "vX.Y.Z" biçiminde bir git TAG olmalı — `origin/main` ya da çıplak commit RED edilir
#   - etiket origin'de bulunmalı (yalnız yerelde var olan/push edilmemiş etiket RED edilir)
#   - REPO_DIR=/opt/tradingbot-main, PROGRAM=tradingbot_main, HEALTH_URL=http://127.0.0.1:9092/api/status
#   - onay istemi: ekrana ring/host/hedef özeti basılır, "MAINNET" kelimesi yazılmalı;
#     otomasyon için DEPLOY_CONFIRM=MAINNET env'i ile onay istemi atlanabilir
# Testnet halkasının davranışı bu değişiklikten ETKİLENMEZ (varsayılan yol aynı kalır).
set -euo pipefail

RING="testnet"
ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --ring)
      if [ $# -lt 2 ]; then echo "--ring bir değer bekliyor (testnet|mainnet)" >&2; exit 1; fi
      RING="$2"
      shift 2
      ;;
    --ring=*)
      RING="${1#--ring=}"
      shift
      ;;
    *)
      ARGS+=("$1")
      shift
      ;;
  esac
done

case "$RING" in
  testnet|mainnet) ;;
  *) echo "geçersiz --ring: '$RING' (testnet|mainnet olmalı)" >&2; exit 1 ;;
esac

HOST="${ARGS[0]:-awa}"
TARGET="${ARGS[1]:-origin/main}"

if [ "$RING" = "mainnet" ]; then
  REPO_DIR="${REPO_DIR:-/opt/tradingbot-main}"
  PROGRAM="${PROGRAM:-tradingbot_main}"
  HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:9092/api/status}"
else
  REPO_DIR="${REPO_DIR:-/opt/tradingbot-v2}"
fi

# Yerel ön koşullar: temiz ağaç, main push edilmiş olmalı
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "yerel ağaçta commit'lenmemiş değişiklik var — önce commit/push" >&2; exit 1
fi
git fetch -q origin

if [ "$RING" = "mainnet" ]; then
  if ! printf '%s' "$TARGET" | grep -Eq '^v[0-9]+\.[0-9]+\.[0-9]+$'; then
    echo "mainnet halkası yalnız 'vX.Y.Z' biçiminde bir etiket kabul eder (verilen: '$TARGET') — 'origin/main' ya da çıplak commit YASAK" >&2
    exit 1
  fi
  if ! git ls-remote --exit-code --tags origin "refs/tags/$TARGET" >/dev/null 2>&1; then
    echo "etiket origin'de yok: $TARGET — önce 'git tag $TARGET && git push origin $TARGET'" >&2
    exit 1
  fi

  echo "── MAİNNET DEPLOY ONAYI ──────────────────────────────"
  echo "  halka  : mainnet"
  echo "  host   : $HOST"
  echo "  hedef  : $TARGET"
  echo "  repo   : $REPO_DIR"
  echo "  program: $PROGRAM"
  echo "───────────────────────────────────────────────────────"
  if [ "${DEPLOY_CONFIRM:-}" = "MAINNET" ]; then
    echo "DEPLOY_CONFIRM=MAINNET ile onay istemi atlandı (otomasyon)"
  else
    CONFIRM=""
    read -r -p "Onaylamak için 'MAINNET' yazın: " CONFIRM || true
    if [ "$CONFIRM" != "MAINNET" ]; then
      echo "onay verilmedi — iptal" >&2
      exit 1
    fi
  fi
else
  if [ "$TARGET" = "origin/main" ] && [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]; then
    echo "yerel HEAD origin/main ile aynı değil — önce 'git push' (ya da hedef commit'i açıkça ver)" >&2; exit 1
  fi
fi

FLAGS="DEPLOY_SKIP_TESTS=${DEPLOY_SKIP_TESTS:-0} DEPLOY_NO_RESTART=${DEPLOY_NO_RESTART:-0}"

if [ "$RING" = "mainnet" ]; then
  FLAGS="REPO_DIR=$REPO_DIR PROGRAM=$PROGRAM HEALTH_URL=$HEALTH_URL RING=mainnet $FLAGS"
  echo "→ $HOST:$REPO_DIR deploy ($TARGET) [ring=mainnet] [$FLAGS]"
else
  echo "→ $HOST:$REPO_DIR deploy ($TARGET) [$FLAGS]"
fi
# Not: server_deploy.sh önce GitHub'dan çekilir ki script'in kendisi de güncel olsun.
ssh -o BatchMode=yes "$HOST" "cd $REPO_DIR && git fetch -q origin && git show origin/main:scripts/server_deploy.sh > /tmp/server_deploy.sh && env $FLAGS bash /tmp/server_deploy.sh '$TARGET'"
