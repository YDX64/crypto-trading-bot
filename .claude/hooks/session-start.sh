#!/bin/bash
# Claude Code BULUT oturumu başlangıç kancası (D39): Python 3.12 venv kurar,
# bağımlılıkları yükler ve testlerin koşabilmesi için .env'yi env.example'dan
# üretir (CI ile aynı yol). Yalnız bulutta çalışır; yerel Mac ve sunucu (awa)
# ETKİLENMEZ. Gerçek secret'lar buluta GİRMEZ — env.example yalnız yer tutucu içerir.
#
# NEDEN 3.12 venv: sunucu venv'i ve CI Python 3.12'dir (parite). Bulut
# konteynerinin sistem Python'u daha yeni olabilir; requirements.txt'deki
# sabit pin'lerin (pandas/numpy/pydantic) o sürüm için tekerleği yoktur ve
# kurulum derleme aşamasında çöker.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(pwd)}"
VENV="$PWD/.venv"   # .gitignore'da; deploy temiz-ağaç kapısını etkilemez

if ! command -v uv >/dev/null 2>&1; then
  python3 -m pip install -q --disable-pip-version-check uv
fi

# İdempotent: venv varsa ve 3.12 ise yeniden kurma, yalnız eksik paketleri tamamla.
if [ ! -x "$VENV/bin/python" ] || ! "$VENV/bin/python" -c 'import sys; sys.exit(sys.version_info[:2] != (3, 12))'; then
  uv venv -q --python 3.12 "$VENV"
fi
VIRTUAL_ENV="$VENV" uv pip install -q -r requirements.txt pyyaml ruff

# Oturumun geri kalanında `python3`/`pytest`/`ruff` bu venv'den gelsin.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  {
    echo "export VIRTUAL_ENV=\"$VENV\""
    echo "export PATH=\"$VENV/bin:\$PATH\""
  } >> "$CLAUDE_ENV_FILE"
fi

# Settings zorunlu alanları için test ortamı (.github/workflows/ci.yml ile aynı).
# Var olan .env'ye dokunma.
if [ ! -f .env ]; then
  cp env.example .env
fi
