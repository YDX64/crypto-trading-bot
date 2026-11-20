#!/bin/bash
# F1b (C1/C2 dev) → freeze → DEV elemesi → F2 doğrulama → (pvals + G1–G8) → F3 dayanıklılık → pvals + decide.
# Önceden kayıtlı kurala göre otomatik; SON SINAV (final) BİLEREK elle (tek atış, kazanan doğrulandıktan sonra).
set -u; source /tmp/claude-0/-home-user-crypto-trading-bot/b53b3a36-e126-5de6-b27f-ca558d8408f8/scratchpad/prereg/lib.sh
AN="$PY $SP/prereg/analyze_prereg.py --root $SP/prereg"
WTE=$SP/wt-exp; WTC=$SP/wt-code
wt_for () { case "$1" in C1|C2) echo $WTC ;; *) echo $WTE ;; esac; }
echo "== F1b: C1/C2 DEV (wt-code) $(date -u +%T)"
for w in AYI YATAY BOGA; do for v in C1 C2; do WTX=$WTC run_bt $v $w; done; done
echo "== freeze $(date -u +%T)"; $AN freeze --cache-dir $CACHE || { echo "FREEZE HATA — durdu"; exit 3; }
echo "== DEV elemesi $(date -u +%T)"; $AN dev --not-coded C3 || { echo "DEV HATA — durdu"; exit 3; }
SURV=$($PY -c "import json;d=json.load(open('$SP/prereg/dev_screen.json'));print(' '.join(v for v,r in d['variants'].items() if r.get('status')=='GECTI'))")
echo "DEV hayatta kalan: ${SURV:-(yok)}"
echo "== F2 DOĞRULAMA $(date -u +%T)"
for w in V1 V2 V3 V4 V5 V6 V7 V8; do
  WTX=$WTE run_bt B0 $w
  for v in $SURV; do WTX=$(wt_for $v) run_bt $v $w; done
done
echo "== pvals + ön-karar (G1–G8 geçenleri bul) $(date -u +%T)"
# DEV'de elenenler 'pvals'a ELENDI olarak girer; --not-coded yalnız C3
$AN pvals --not-coded C3 || echo "PVALS HATA"
$AN decide --not-coded C3 || echo "DECIDE HATA"
F3=$($PY -c "import json;g=json.load(open('$SP/prereg/gates.json'));print(' '.join(v for v,r in g['variants'].items() if r.get('state')=='BEKLIYOR'))")
echo "G1–G8 geçen (F3'e giren): ${F3:-(yok)}"
echo "== F3 DAYANIKLILIK $(date -u +%T)"
for v in $F3; do for w in V1 V2 V3 V4 V5 V6 V7 V8; do
  WTX=$(wt_for $v) run_bt $v $w --fee-stress
  WTX=$(wt_for $v) run_bt $v $w --entry-delay-candles 1
done; done
echo "== F4 pvals + decide $(date -u +%T)"
$AN pvals --not-coded C3 && $AN decide --not-coded C3
echo "== bitti $(date -u +%T) — SON SINAV elle (analyze final <KAZANAN>), yalnız KAZANAN varsa"
