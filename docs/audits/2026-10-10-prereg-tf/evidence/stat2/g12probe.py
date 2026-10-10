import numpy as np, sys
from pipe_power2 import *
rng=np.random.default_rng(11)
def probe(name,pf_t,scn_k,nsim=150):
    arm=ARMS[name]; scn=SCN[scn_k]
    p0=calibrate(arm,pf_t,rng)
    c={}
    for i in range(nsim):
        s=make_set(arm,p0,687,VAL_MONTH_DAYS,VAL_LABELS,scn,rng,arm['Nd'])
        r=net(s,arm); T=r.sum()
        if T<=0: c['T<=0']=c.get('T<=0',0)+1; continue
        ssum=np.bincount(s['sym'],weights=r,minlength=8); bs=np.argmax(ssum)
        mo=np.bincount(s['month'],weights=r,minlength=s['nm']); bmo=np.argmax(mo)
        tests={'sym40':ssum[bs]<=0.40*T,'sym50':ssum[bs]<=0.50*T,
         'pf_ex_sym1.05':pf_of(r[s['sym']!=bs])>=1.05,'pf_ex_sym1.0':pf_of(r[s['sym']!=bs])>=1.0,
         'pf_ex_mo1.05':pf_of(r[s['month']!=bmo])>=1.05,'pf_ex_mo1.0':pf_of(r[s['month']!=bmo])>=1.0,
         'best8':r.max()<=0.08*T,'best15':r.max()<=0.15*T,'top5_25':np.sort(r)[-5:].sum()<=0.25*T,'top5_40':np.sort(r)[-5:].sum()<=0.40*T,
         'loo1.0':all(pf_of(r[s['sym']!=q])>=1.0 for q in range(8)),
         'loo0.97':all(pf_of(r[s['sym']!=q])>=0.97 for q in range(8))}
        for k,v in tests.items():
            if not v: c[k]=c.get(k,0)+1
    return {k:round(v/nsim,2) for k,v in c.items()}
for name in ('S5','S15','S30','T30'):
    for pf_t in (1.3,):
        print(name,pf_t,'A',probe(name,pf_t,'A'))
        print(name,pf_t,'B',probe(name,pf_t,'B'))
