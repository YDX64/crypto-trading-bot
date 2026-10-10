#!/usr/bin/env bash
# E17 DEV zinciri (PLAN §6: P1 doldurma, P3 koşular, P4a-c eleme/G6/karar, P5 teşhis).
# Koşular ardışık ve mühürlüdür; konsola PnL/PF basılmaz. Tamamlanmış koşu (ledger'da)
# yeniden koşulmaz; `DEV_DECISION.json` varsa P4a-c atlanır (TEK KEZ yazılır).
set -Eeuo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib17.sh
source "$HERE/lib17.sh"

e17_init
e17_verify_lock

# Duman testleri bitmeden DEV başlamaz (PLAN §5.2 "engelleyici").
if [ "$E17_DRY_RUN" != 1 ]; then
  for lbl in SMOKE_B0_AYI SMOKE_B0_V1 SMOKE_S15_h0 SMOKE_S15_h1 SMOKE_R1; do
    e17_in_ledger "$lbl" || die "duman testi tamamlanmadı ($lbl ledger'da yok): önce run_smoke.sh"
  done
fi

e17_window DEV
DEV_S=$E17_WS
DEV_E=$E17_WE

e17_phase "P1 DEV doldurma"
fill_or_die DEV_5m_BG S5 "$DEV_S" "$DEV_E"
fill_or_die DEV_5m_GATE S5G "$DEV_S" "$DEV_E"   # S5G/T5G: lider serisi de yazılır
fill_or_die DEV_15m S15 "$DEV_S" "$DEV_E"
fill_or_die DEV_30m S30 "$DEV_S" "$DEV_E"
fill_or_die DEV_1h S60 "$DEV_S" "$DEV_E"
e17_lines et quarters --variants "$E17_VARIANTS"
QUARTERS=("${E17_TMPARR[@]}")
for q in "${QUARTERS[@]}"; do
  read -r qn qs qe <<< "$q"
  fill_or_die "DEV_1m_$qn" B0 "$qs" "$qe"        # 1m yalnız çeyrek pencereleri
done
e17_manifest DEV

e17_phase "P3 DEV kosulari"
for arm in S5 T5 S15 T15 S30 T30 S60 T60 S5G T5G; do
  run_bt "DEV_$arm" "$arm" "$DEV_S" "$DEV_E"
done
for arm in R1 B0; do
  for q in "${QUARTERS[@]}"; do
    read -r qn qs qe <<< "$q"
    run_bt "DEV_${arm}_$qn" "$arm" "$qs" "$qe"
  done
done

DECISION="$E17_ROOT/DEV_DECISION.json"
if [ -f "$DECISION" ] && [ "$E17_DRY_RUN" != 1 ]; then
  e17_phase "P4 atlandi (DEV_DECISION.json zaten yazilmis)"
else
  e17_phase "P4a dev-screen"
  e17_analyzer dev-screen
  if [ "$E17_DRY_RUN" != 1 ]; then e17_require_file "$E17_ROOT/DEV_SCREEN.json" "dev-screen çıktısı"; fi
  e17_phase "P4b G6 kaplama kosulari"
  G6ARMS=()
  if [ -f "$E17_ROOT/DEV_SCREEN.json" ]; then
    e17_lines et screen-arms --variants "$E17_VARIANTS" "$E17_ROOT/DEV_SCREEN.json"
    G6ARMS=(${E17_TMPARR[@]+"${E17_TMPARR[@]}"})
  fi
  printf 'G6 arm sayisi: %s\n' "${#G6ARMS[@]}"
  for arm in ${G6ARMS[@]+"${G6ARMS[@]}"}; do
    for ov in ROB_FEE2 ROB_DLY1 ROB_TAKER; do
      run_bt "DEV_${arm}_$ov" "$arm" "$DEV_S" "$DEV_E" "$ov"
    done
  done
  e17_phase "P4c dev-decide"
  e17_analyzer dev-decide
fi
if [ "$E17_DRY_RUN" != 1 ]; then e17_require_file "$DECISION" "dev-decide çıktısı"; fi

e17_phase "P5 teshis kosulari (karar vermez)"
e17_family_ids
FAMILY=("${E17_TMPARR[@]}")
FEE15=()
if [ -f "$DECISION" ]; then
  e17_lines et fee2-failed "$DECISION"
  FEE15=(${E17_TMPARR[@]+"${E17_TMPARR[@]}"})
  e17_lines et advanced "$DECISION"
  for arm in ${E17_TMPARR[@]+"${E17_TMPARR[@]}"}; do
    case " ${FEE15[*]-} " in *" $arm "*) ;; *) FEE15+=("$arm") ;; esac
  done
fi
for ov in DIAG_Z0 DIAG_SBO; do
  for arm in "${FAMILY[@]}"; do
    run_bt "DEV_${arm}_$ov" "$arm" "$DEV_S" "$DEV_E" "$ov"
  done
done
for arm in ${FEE15[@]+"${FEE15[@]}"}; do
  run_bt "DEV_${arm}_DIAG_FEE15" "$arm" "$DEV_S" "$DEV_E" DIAG_FEE15
done
e17_analyzer diag
e17_phase "DEV tamam"
