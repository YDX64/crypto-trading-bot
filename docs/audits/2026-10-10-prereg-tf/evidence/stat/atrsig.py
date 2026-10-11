import json,sys,statistics as st
def q(v,p):
    v=sorted(v); k=(len(v)-1)*p; f=int(k); c=min(f+1,len(v)-1); return v[f]+(v[c]-v[f])*(k-f)
for f in sys.argv[1:]:
    d=json.load(open(f)); r=d['rec']
    a=[x['atr_pct'] for x in r]
    print(f, 'calls',len(r),'trades',d['n_trades'],'missed',d['missed'])
    print('  pooled ATRpct P10 %.3f P25 %.3f P50 %.3f P75 %.3f P90 %.3f'%tuple(q(a,p) for p in (.1,.25,.5,.75,.9)))
    bys={}
    for x in r: bys.setdefault(x['sym'],[]).append(x['atr_pct'])
    print('  by sym n/median:',' '.join(f"{s[:-4]}:{len(v)}/{st.median(v):.3f}" for s,v in sorted(bys.items())))
    print('  dirs', d['trades_by_dir'], 'regimes', d['trades_by_regime'], 'EOD', d['exit_reasons_only_eod'])
