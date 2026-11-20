#!/usr/bin/env bash
# E17 P0 doldurma provası (PLAN §6 P0(b), §4.5: 8 işçilik doldurma, CAL ayı).
#
#   run_prova.sh [ARM [BAŞLANGIÇ BİTİŞ]]     varsayılan: S15, SMOKE_W (2026-04-01..2026-05-01)
#
# YALNIZ CAL penceresinde (2026-03-01..2026-06-01) doldurur; DEV/VAL/FINAL verisi ASLA. Backtest ve
# analizci çalışmaz. Kilit öncesi de çalışır (E17_PRELOCK=1 E17_H_COMMIT=<sha>). Amaç: işçi bölme,
# hız ve boşluk davranışını kilitten ÖNCE ölçmek; doldurma sonuçları karar için kullanılmaz.
set -Eeuo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib17.sh
source "$HERE/lib17.sh"

ARM=${1:-S15}
e17_init
e17_verify_lock
if [ "$#" -ge 3 ]; then
  PS=$2
  PE=$3
else
  e17_window SMOKE_W
  PS=$E17_WS
  PE=$E17_WE
fi
[[ $PS =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ && $PE =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || die "tarih biçimi YYYY-MM-DD olmalı"
if [[ $PS < 2026-03-01 || $PE > 2026-06-01 || ! $PS < $PE ]]; then
  die "prova yalnız CAL penceresinde (2026-03-01..2026-06-01) çalışır: $PS..$PE"
fi

e17_phase "P0 doldurma provasi"
fill_or_die "PROVA_$ARM" "$ARM" "$PS" "$PE"
