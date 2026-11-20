#!/usr/bin/env bash
# E17 duman testleri (PLAN §4.3 P1b + §5.2 S1/S2/S3). Lock'tan SONRA, DEV'den ÖNCE.
#
#   S1 parite         : B0 AYI (2026-01-23..02-13) ve B0 V1 (2025-01-06..01-27)
#   S2 belirlenimcilik: S15 SMOKE_W, PYTHONHASHSEED 0 ve 1 (ayrı süreçler)
#   S3 konfigürasyon  : 8 aile/açıklayıcı arm SMOKE_W'da (SMOKE_<arm>_W) + R1 SMOKE_1M'de (SMOKE_R1_M1)
# Etiket dilbilgisi analizcinin parse_label'ıyla aynıdır: SMOKE_B0_AYI|V1, SMOKE_S15_h0|h1,
# SMOKE_<arm>_W, SMOKE_R1_M1. Konsola PnL/PF basılmaz; karşılaştırmayı `analyze_e17.py smoke` yapar
# (SMOKE.json {pass}; run_dev.sh bunu okur).
# Kilit öncesi deneme: E17_PRELOCK=1 E17_H_COMMIT=<sha> (analizci çağrılmaz; bkz. lib17.sh).
set -Eeuo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib17.sh
source "$HERE/lib17.sh"

e17_init
e17_verify_lock

e17_phase "P1b smoke doldurma"
e17_window S1_AYI
fill_or_die SMOKE_B0_AYI B0 "$E17_WS" "$E17_WE"
e17_window S1_V1
fill_or_die SMOKE_B0_V1 B0 "$E17_WS" "$E17_WE"
e17_window SMOKE_W
# Dört üçlü için birer BG arm'ı (T arm'ları aynı önbellek anahtarlarını kullanır).
for arm in S5 S15 S30 S60; do
  fill_or_die "SMOKE_W_$arm" "$arm" "$E17_WS" "$E17_WE"
done
e17_window SMOKE_1M
fill_or_die SMOKE_1M_R1 R1 "$E17_WS" "$E17_WE"
e17_manifest SMOKE

e17_phase "S1 parite"
e17_window S1_AYI
run_bt SMOKE_B0_AYI B0 "$E17_WS" "$E17_WE"
e17_window S1_V1
run_bt SMOKE_B0_V1 B0 "$E17_WS" "$E17_WE"

e17_phase "S2 belirlenimcilik"
e17_window SMOKE_W
E17_RUN_HASHSEED=0 run_bt SMOKE_S15_h0 S15 "$E17_WS" "$E17_WE"
E17_RUN_HASHSEED=1 run_bt SMOKE_S15_h1 S15 "$E17_WS" "$E17_WE"

e17_phase "S3 konfigurasyon"
for arm in S5 T5 S15 T15 S30 T30 S60 T60; do
  run_bt "SMOKE_${arm}_W" "$arm" "$E17_WS" "$E17_WE"
done
e17_window SMOKE_1M
run_bt SMOKE_R1_M1 R1 "$E17_WS" "$E17_WE"

e17_phase "smoke karar"
if [ "$E17_DRY_RUN" != 1 ]; then rm -f "$E17_ROOT/SMOKE.json"; fi   # bayat hüküm olamaz; analizci yeniden yazar
e17_analyzer smoke
e17_manifest runs
