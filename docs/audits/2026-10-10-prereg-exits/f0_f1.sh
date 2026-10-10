#!/bin/bash
set -u; source /tmp/claude-0/-home-user-crypto-trading-bot/b53b3a36-e126-5de6-b27f-ca558d8408f8/scratchpad/prereg/lib.sh
echo "== F0 doldurma $(date -u +%T)"
for w in SMOKE V1 V2 V3 V4 V5 V6 V7 V8; do fill_win $w; done
echo "== F0 duman: B0 AYI belirlenimcilik (taban 149 / 87.2 / 2.03) $(date -u +%T)"
run_bt B0 AYI
echo "== F0 duman: E1-E8 x SMOKE $(date -u +%T)"
for v in E1 E2 E3 E4 E5 E6 E7 E8; do run_bt $v SMOKE; done
echo "== F1 DEV: B0+E1..E8 x AYI/YATAY/BOGA $(date -u +%T)"
for w in AYI YATAY BOGA; do for v in B0 E1 E2 E3 E4 E5 E6 E7 E8; do [ "$v/$w" = "B0/AYI" ] && continue; run_bt $v $w; done; done
echo "== F0+F1 bitti $(date -u +%T)"
