# Ön-kayıtlı deney çalıştırıcı kitaplığı (PLAN.md §4; python = .venv 3.12)
SP=/tmp/claude-0/-home-user-crypto-trading-bot/b53b3a36-e126-5de6-b27f-ca558d8408f8/scratchpad
ENVF=$SP/server-scalper-env.txt; CACHE=/home/user/crypto-trading-bot/data/klines_cache
PY=/home/user/crypto-trading-bot/.venv/bin/python
SYMS=BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,BNBUSDT,ADAUSDT,LTCUSDT
WTX=${WTX:-$SP/wt-exp}
declare -A WIN=( [AYI]="2026-01-23 2026-02-13" [YATAY]="2026-07-01 2026-07-21" [BOGA]="2026-08-07 2026-08-21"
  [V1]="2025-01-06 2025-01-27" [V2]="2025-02-17 2025-03-10" [V3]="2025-04-14 2025-05-05" [V4]="2025-05-12 2025-06-02"
  [V5]="2025-07-07 2025-07-28" [V6]="2025-08-18 2025-09-08" [V7]="2025-10-06 2025-10-27" [V8]="2025-11-03 2025-11-24"
  [SMOKE]="2026-08-07 2026-08-14" [CANLI]="2026-09-03 2026-10-08" )
declare -A VAR=( [B0]=""
  [E1]="SCALPER_FIXED_STOP_ROI_PCT=10 SCALPER_DYN_LEV_STOP_ATR_MULT=10 SCALPER_TP1_ROI=10 SCALPER_TP2_ROI=25"
  [E2]="SCALPER_FIXED_STOP_ROI_PCT=20 SCALPER_DYN_LEV_STOP_ATR_MULT=20 SCALPER_TP1_ROI=10 SCALPER_TP2_ROI=25"
  [E3]="SCALPER_FIXED_STOP_ROI_PCT=30 SCALPER_DYN_LEV_STOP_ATR_MULT=30 SCALPER_TP1_ROI=10 SCALPER_TP2_ROI=25"
  [E4]="SCALPER_FIXED_STOP_ROI_PCT=20 SCALPER_DYN_LEV_STOP_ATR_MULT=20 SCALPER_TP1_ROI=20 SCALPER_TP2_ROI=40 SCALPER_TP1_FRACTION=0.30 SCALPER_TP2_FRACTION=0.30"
  [E5]="SCALPER_FIXED_STOP_ROI_PCT=20 SCALPER_DYN_LEV_STOP_ATR_MULT=20 SCALPER_TP1_ROI=30 SCALPER_TP2_ROI=60 SCALPER_TP1_FRACTION=0.30 SCALPER_TP2_FRACTION=0.30"
  [E6]="SCALPER_FIXED_STOP_ROI_PCT=50 SCALPER_DYN_LEV_STOP_ATR_MULT=50 SCALPER_TP1_ROI=10 SCALPER_TP2_ROI=25"
  [E7]="SCALPER_FIXED_STOP_ROI_PCT=20 SCALPER_DYNAMIC_LEVERAGE=false SCALPER_LEVERAGE=20 SCALPER_TP1_ROI=10 SCALPER_TP2_ROI=25"
  [E8]="SCALPER_FIXED_STOP_ROI_PCT=20 SCALPER_DYN_LEV_STOP_ATR_MULT=20 SCALPER_TP1_ROI=10 SCALPER_TP2_ROI=25 SCALPER_TP1_FRACTION=0.60 SCALPER_TP2_FRACTION=0.20"
  # Kod gerektiren varyantlar (wt-code dalı; varsayılan KAPALI bayraklar burada AÇILIR). B0 TP2_FRACTION=0.20 olduğundan
  # fraksiyonlar AÇIKÇA verilir (PLAN §2). NOT: C2 stopu E2 ile aynı ama fraksiyonlar 0.50/0.25; E2 ise 0.40/0.20 (B0) kullanır.
  [C1]="SCALPER_STOP_MODE=swing_atr SCALPER_TP_PLAN_MODE=r SCALPER_TP1_R=1.0 SCALPER_TP2_R=2.0 SCALPER_TP1_FRACTION=0.40 SCALPER_TP2_FRACTION=0.30"
  [C2]="SCALPER_FIXED_STOP_ROI_PCT=20 SCALPER_DYN_LEV_STOP_ATR_MULT=20 SCALPER_TP1_ROI=10 SCALPER_TP2_ROI=25 SCALPER_TP_PLAN_MODE=bb SCALPER_TP1_FRACTION=0.50 SCALPER_TP2_FRACTION=0.25" )

fill_win () {  # fill_win <PENCERE_ADI>
  set -- ${WIN[$1]} "$1"
  ( cd $WTX && env $(grep -E '^SCALPER_' $ENVF | xargs) $PY scripts/fill_klines_cache_from_vision.py \
      --symbols $SYMS --start $1 --end $2 --cache-dir $CACHE --download-cache $SP/prereg/vision_cache \
  ) > $SP/prereg/logs/fill_$3.log 2>&1 && echo "FILL OK $3" || echo "FILL HATA $3"
}

run_bt () {  # run_bt <VARYANT> <PENCERE> [ek backtest bayrakları...]   etiket = VARYANT_PENCERE[_fee|_dly]
  local v=$1 w=$2; shift 2; local extra=("$@") sfx=""
  [[ " ${extra[*]} " == *" --fee-stress "* ]] && sfx="_fee"; [[ " ${extra[*]} " == *"--entry-delay-candles"* ]] && sfx="_dly"
  local label=${v}_${w}${sfx} base ov; base=$(grep -E '^SCALPER_' $ENVF); ov=(${VAR[$v]})
  for kv in "${ov[@]}"; do base=$(printf '%s\n' "$base" | grep -v "^${kv%%=*}="); done
  set -- ${WIN[$w]}
  sleep 1.2  # rapor dosya adı saniye çözünürlüklü (çakışma önlemi)
  ( cd $WTX && env $(printf '%s\n' "$base" | xargs) "${ov[@]}" $PY -m src.strategies.scalper.backtest \
      --strategies C --symbols $SYMS --start $1 --end $2 --cache-dir $CACHE "${extra[@]}" ) > $SP/prereg/logs/$label.log 2>&1
  local f; f=$(grep -oE 'logs/backtest_[0-9_]+\.json' $SP/prereg/logs/$label.log | tail -1)
  if [ -n "$f" ] && [ -f "$WTX/$f" ]; then cp "$WTX/$f" $SP/prereg/runs/$label.json; echo "BT OK $label $(grep -E '^TOPLAM' $SP/prereg/logs/$label.log | tr -s ' ' | cut -c1-90)"; else echo "BT HATA $label"; tail -3 $SP/prereg/logs/$label.log; fi
}
