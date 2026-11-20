#!/usr/bin/env bash
# E17 VAL zinciri (PLAN §6 P6, §4.3 P6, §9.5 boşluk kuralı).
#
# BAŞLAMAZ: analizci aşama dosyası `DEV_DECISION.json` yoksa veya ilerleyen arm yoksa
# (VAL verisi o zamana kadar fiziksel olarak indirilmez — PLAN §4.8, §9.2-3).
# Doldurma yalnız ilerleyen üçlüler + 1h/4h/4h açıklayıcı (S60); 1m üçlüsü YOK.
set -Eeuo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib17.sh
source "$HERE/lib17.sh"

e17_forbid_prelock
e17_init
e17_verify_lock

DECISION="$E17_ROOT/DEV_DECISION.json"
e17_require_file "$DECISION" "önce run_dev.sh bitmeli (dev-decide)"
[ ! -f "$E17_ROOT/FINAL.json" ] || die "FINAL.json zaten var: VAL sonrası aşamaya geri dönülmez"
e17_lines et advanced "$DECISION" --variants "$E17_VARIANTS"     # aile dışı id (B0, S5G, R1, T60 ...) reddedilir
ADV=(${E17_TMPARR[@]+"${E17_TMPARR[@]}"})
[ "${#ADV[@]}" -gt 0 ] || die "DEV'de ilerleyen arm yok: VAL başlamaz (VAL verisi indirilmez)"
[ "${#ADV[@]}" -le 2 ] || die "ilerleyen arm sayısı 2'yi aşıyor (${#ADV[@]})"

# Doldurulacak üçlüler: ilerleyen arm'lar + açıklayıcı S60 (aynı üçlü tekrar doldurulmaz).
REPS=()
SEEN=" "
for arm in "${ADV[@]}" S60; do
  trip=$(e17_triple_of "$arm") || die "üçlü çözülemedi: $arm"
  case "$SEEN" in *" ${trip// //} "*) continue ;; esac
  SEEN="$SEEN${trip// //} "
  REPS+=("$arm")
done
TRIPLES=""
for arm in "${REPS[@]}"; do
  trip=$(e17_triple_of "$arm") || die "üçlü çözülemedi: $arm"
  TRIPLES="$TRIPLES${trip// //};"
done

e17_val_window() {
  local out
  out=$(et val-window --variants "$E17_VARIANTS" --root "$E17_ROOT") || die "VAL penceresi çözülemedi (VAL-YAPILAMADI olabilir)"
  read -r VS VE <<< "$out"
}

val_fill_all() {
  local a
  rm -f "$E17_ROOT"/fill_logs/VAL_*.w*.log
  for a in "${REPS[@]}"; do
    fill_window "VAL_$a" "$a" "$VS" "$VE" || return 1
  done
  return 0
}

e17_phase "P6 VAL doldurma"
SHIFTS=0
while :; do
  e17_val_window
  if val_fill_all; then break; fi
  # Önce 1 kez yeniden dene (altyapı); sonra boşluk kuralı (PLAN §9.5).
  e17_chain "VAL doldurma hatası ($VS..$VE); 1 kez yeniden deneniyor"
  if val_fill_all; then break; fi
  GAPS=$(et gaps "$E17_ROOT"/fill_logs/VAL_*.w*.log) || die "boşluk ayrıştırılamadı"
  [ -n "$GAPS" ] || die "VAL doldurma hatası arşiv boşluğu değil (ağ/doğrulama): zincir durdu"
  LAST=$(printf '%s\n' "$GAPS" | tail -n 1)
  read -r gsym gint gdate <<< "$LAST"
  SHIFTS=$((SHIFTS + 1))
  [ "$SHIFTS" -le 6 ] || die "VAL-YAPILAMADI: 6 kaydırma yetmedi"
  CUTRC=0
  et val-cut --variants "$E17_VARIANTS" --root "$E17_ROOT" --repo "${E17_WINDOW_REPO:-$E17_CLONE}" --python "$E17_PY" \
     --gap-date "$gdate" --gap-interval "$gint" --gap-source "$gsym $gint" --triples "${TRIPLES%;}" || CUTRC=$?
  if [ "$CUTRC" -eq 0 ]; then
    e17_chain "VAL penceresi kesildi: boşluk $gsym $gint $gdate (VAL_WINDOW_CUT.json)"
  elif [ "$CUTRC" -eq 4 ]; then
    # envtool yalnız GERÇEK VAL-YAPILAMADI'da 4 döner (kalan < 365 gün / 6 aylık kaydırma yetmedi).
    e17_chain "VAL-YAPILAMADI: boşluk $gsym $gint $gdate"
    die "VAL-YAPILAMADI (VAL_WINDOW_CUT.json); FINAL'e dokunulmaz"
  else
    # Araç çökmesi / zaman aşımı: kurtarılabilir altyapı hatası, terminal etiket YAZILMAZ.
    e17_chain "VAL kesme hesabı altyapı hatası rc=$CUTRC (boşluk $gsym $gint $gdate); VAL-YAPILAMADI DEĞİL"
    die "VAL pencere kesme hesabı başarısız (rc=$CUTRC): altyapı hatası, düzeltip yeniden başlatın"
  fi
done
e17_manifest VAL

e17_phase "P6 VAL kosulari"
for arm in "${ADV[@]}"; do
  run_bt "VAL_$arm" "$arm" "$VS" "$VE"
  for ov in ROB_FEE2 ROB_DLY1 ROB_TAKER ROB_WALL; do
    run_bt "VAL_${arm}_$ov" "$arm" "$VS" "$VE" "$ov"
  done
done
for arm in S60 T60; do
  run_bt "VAL_$arm" "$arm" "$VS" "$VE"
done

e17_phase "VAL karar"
e17_analyzer val
e17_manifest runs
