#!/usr/bin/env bash
# E17 FINAL zinciri (PLAN §6 P7/P8, §4.3 P8): TEK atış, yalnız VETO; kazanan + B0.
#
# BAŞLAMAZ: `VAL_DECISION.json` yoksa veya tam olarak bir VAL kazananı içermiyorsa.
# `FINAL.json` bir kez yazılır; varsa betik reddeder.
set -Eeuo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib17.sh
source "$HERE/lib17.sh"

e17_init
e17_verify_lock

VDEC="$E17_ROOT/VAL_DECISION.json"
e17_require_file "$VDEC" "önce run_val.sh bitmeli (val)"
[ ! -f "$E17_ROOT/FINAL.json" ] || die "FINAL.json zaten yazılmış (tek atış)"
WINNER=$(et winner "$VDEC") || die "VAL kazananı yok (veya birden fazla): FINAL'e dokunulmaz"
e17_family_ids
case " ${E17_TMPARR[*]} " in *" $WINNER "*) ;; *) die "kazanan aile arm'ı değil: $WINNER" ;; esac

e17_window FINAL
FS=$E17_WS
FE=$E17_WE

e17_phase "P8 FINAL doldurma"
fill_or_die "FINAL_$WINNER" "$WINNER" "$FS" "$FE"
fill_or_die FINAL_B0 B0 "$FS" "$FE"            # 1m/5m/15m + lider
e17_manifest FINAL

e17_phase "P8 FINAL kosulari"
run_bt "FINAL_$WINNER" "$WINNER" "$FS" "$FE"
run_bt FINAL_B0 B0 "$FS" "$FE"

e17_phase "FINAL karar"
e17_analyzer final
e17_analyzer decide
