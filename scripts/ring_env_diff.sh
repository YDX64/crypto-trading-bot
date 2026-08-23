#!/usr/bin/env bash
# Salt okunur: halkaların .env'i arasında BINANCE_*/SCALPER_*/TV_*/RISK_*/FOLLOWER_*/BOT_MODE
# anahtarlarının hangilerinin FARKLI olduğunu gösterir. Hiçbir şeyi değiştirmez.
# BINANCE_ dahildir (D17): "hangi halka nereye işlem yapıyor" (BINANCE_BASE_URL) ile
# "kline'lar nereden geliyor" (SCALPER_MARKET_DATA_BASE_URL) çapraz kontrolü ancak ikisi
# birlikte görülünce yapılabilir — bkz. docs/MAINNET_PLAN.md §3. FOLLOWER_/BOT_MODE (D20).
# Secret benzeri anahtarların (adında SECRET/KEY/TOKEN/PASS geçen) DEĞERİNİ asla yazdırmaz —
# yalnız "değişti" (***) der; değer içermeyen anahtarlar olduğu gibi gösterilir.
# Kullanım: scripts/ring_env_diff.sh [ssh-host]
#   scripts/ring_env_diff.sh awa
#   V2_ENV=/opt/tradingbot-v2/.env MAIN_ENV=/opt/tradingbot-main/.env scripts/ring_env_diff.sh awa
#   MAIN_ENV=/opt/tradingbot-ap/.env scripts/ring_env_diff.sh awa   # AlgoPro takipçi halkası (D20)
set -euo pipefail

HOST="${1:-awa}"
V2_ENV="${V2_ENV:-/opt/tradingbot-v2/.env}"
MAIN_ENV="${MAIN_ENV:-/opt/tradingbot-main/.env}"

ssh -o BatchMode=yes "$HOST" bash -s -- "$V2_ENV" "$MAIN_ENV" <<'REMOTE'
set -euo pipefail
v2="$1"; main="$2"

mask_or_value() {
  # $1 = anahtar adı, $2 = değer
  # BINANCE_BIND_IP maskelidir (düşmanca inceleme bulgusu): sunucunun Binance'e
  # çıktığı IP adresidir — ban/ağırlık muhasebesi IP başınadır, yani bu değer
  # operasyonel olarak hassastır ve bir diff çıktısı (ekran görüntüsü, ticket,
  # sohbet) yoluyla dışarı sızmamalıdır. "Değişti mi?" sorusuna *** yeter.
  case "$1" in
    *SECRET*|*KEY*|*TOKEN*|*PASS*|*BIND_IP*) echo "***" ;;
    *) echo "$2" ;;
  esac
}

[ -f "$v2" ] || { echo "yok: $v2" >&2; exit 1; }
[ -f "$main" ] || { echo "yok: $main" >&2; exit 1; }

keys="$(grep -hE '^(BINANCE_|SCALPER_|TV_|RISK_|FOLLOWER_|BOT_MODE)[A-Za-z0-9_]*=' "$v2" "$main" | cut -d= -f1 | sort -u)" || true

diffcount=0
for k in $keys; do
  a="$(grep -E "^${k}=" "$v2" | tail -1 | cut -d= -f2-)" || true
  b="$(grep -E "^${k}=" "$main" | tail -1 | cut -d= -f2-)" || true
  if [ "$a" != "$b" ]; then
    diffcount=$((diffcount + 1))
    da="$(mask_or_value "$k" "${a:-<yok>}")"
    db="$(mask_or_value "$k" "${b:-<yok>}")"
    printf '%-40s v2=%-24s main=%-24s\n' "$k" "$da" "$db"
  fi
done

if [ "$diffcount" -eq 0 ]; then
  echo "fark yok (BINANCE_/SCALPER_/TV_/RISK_/FOLLOWER_/BOT_MODE anahtarları özdeş)"
fi
exit 0
REMOTE
