#!/usr/bin/env bash
# scripts/run_windows.sh <etiket> [VAR=deger ...]   (D33 / E13 aracı)
# Sunucu env snapshot'ı üzerine verilen override'larla C stratejisini 7 pencerede SIRALI koşar,
# her pencere için JSON yolu + n/pnl/PF/maxDD satırı basar. SEÇİM (AYI/YATAY/BOĞA) + DOKUNULMAMIŞ
# (Mart/Çöküş/Mayıs/Haziran) pencereleri koşar. Taze son-sınav pencereleri (2026-02-13→03-01,
# 2026-07-21→08-07) BİLEREK dışarıda: yalnız FRESH=1 ile ve tek atış için açılır.
set -uo pipefail
cd /Users/max/TRADINGBOT/v2 || exit 1
LABEL="${1:?etiket}"; shift
SP="${RUN_WINDOWS_OUT:-logs/run_windows}"; mkdir -p "$SP"
OUT="$SP/run7_${LABEL}.txt"; : > "$OUT"
# snapshot'taki anahtarlardan override edilenleri düş
ENVLINES=$(grep -E '^(SCALPER_|TV_)' scripts/.scalper_env_snapshot.txt)
for kv in "$@"; do k="${kv%%=*}"; ENVLINES=$(printf '%s\n' "$ENVLINES" | grep -v "^${k}="); done
SYMS=BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,BNBUSDT,ADAUSDT,LTCUSDT
while read -r S E N; do
  LOG="$SP/run7_${LABEL}_${N}.log"
  env $(printf '%s\n' "$ENVLINES" | xargs) "$@" timeout 900 python3 -m src.strategies.scalper.backtest \
    --strategies C --symbols "$SYMS" --start "$S" --end "$E" --cache-dir data/klines_cache > "$LOG" 2>&1
  F=$(grep -oE "logs/backtest_[0-9_]+\.json" "$LOG" | tail -1)
  if [ -n "$F" ]; then
    python3 - "$N" "$F" >> "$OUT" <<'PY'
import json,sys
n,f=sys.argv[1],sys.argv[2]; d=json.load(open(f)); o=d["overall"]; mc=d.get("missed_signals",{})
print(f"{n:9} {f} n={o['trades']:3} pnl={o['total_pnl']:8.1f} PF={o['profit_factor']:.2f} maxDD={o['max_drawdown']:.0f} wr={o['winrate']:.1f}")
PY
  else
    echo "$N HATA (bkz. $LOG)" >> "$OUT"
  fi
done <<EOF
2026-01-23 2026-02-13 AYI
2026-07-01 2026-07-21 YATAY
2026-08-07 2026-08-21 BOGA
2026-03-01 2026-04-01 OOS_MART
2026-08-21 2026-09-03 COKUS
2026-05-04 2026-05-25 HOLD_MAY
2026-06-08 2026-06-29 HOLD_JUN
$( [ "${FRESH:-0}" = 1 ] && printf '%s\n' '2026-02-13 2026-03-01 FRESH_SUBAT' '2026-07-21 2026-08-07 FRESH_TEMMUZ' )
EOF
cat "$OUT"
