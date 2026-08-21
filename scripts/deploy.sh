#!/usr/bin/env bash
# Yerelden deploy: push edilmiş GitHub main'i sunucuya uygular (testnet halkası).
# Kullanım: scripts/deploy.sh [ssh-host] [hedef-ref]
#   scripts/deploy.sh              → awa, origin/main
#   scripts/deploy.sh awa v1.2.0   → belirli etiket/commit
# Geri alma: scripts/deploy.sh awa <önceki-commit>   (backups/commit.prev-* dosyalarında)
set -euo pipefail
HOST="${1:-awa}"
TARGET="${2:-origin/main}"
REPO_DIR="${REPO_DIR:-/opt/tradingbot-v2}"

# Yerel ön koşullar: temiz ağaç, main push edilmiş olmalı
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "yerel ağaçta commit'lenmemiş değişiklik var — önce commit/push" >&2; exit 1
fi
git fetch -q origin
if [ "$TARGET" = "origin/main" ] && [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]; then
  echo "yerel HEAD origin/main ile aynı değil — önce 'git push' (ya da hedef commit'i açıkça ver)" >&2; exit 1
fi

FLAGS="DEPLOY_SKIP_TESTS=${DEPLOY_SKIP_TESTS:-0} DEPLOY_NO_RESTART=${DEPLOY_NO_RESTART:-0}"
echo "→ $HOST:$REPO_DIR deploy ($TARGET) [$FLAGS]"
# Not: server_deploy.sh önce GitHub'dan çekilir ki script'in kendisi de güncel olsun.
ssh -o BatchMode=yes "$HOST" "cd $REPO_DIR && git fetch -q origin && git show origin/main:scripts/server_deploy.sh > /tmp/server_deploy.sh && env $FLAGS bash /tmp/server_deploy.sh '$TARGET'"
