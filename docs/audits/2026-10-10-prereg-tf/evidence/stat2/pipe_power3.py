"""E17 uçtan-uca boru hattı güç simülasyonu (MODEL TABANLI; ölçülmüş PnL YOK).
Her arm için: geometri-tabanlı R getiri karışımı (S: TP1=1R %40, TP2=2R %20, koşan; T: TP1=0.2R, 1:~6 getiri oranı),
gün kümelenmesi, aylık rastgele etki, sembol/rejim heterojenliği; sonra DEV (G1-G6) ve VAL (G7-G16) kapıları.
Ücret/R: S5 .17 S15 .09 S30 .065; T ~.03.  Kullanım: python pipe_power2.py <senaryo> <FEEMULT_GATE>
"""
import numpy as np, sys, json, math
from multiprocessing import Pool

DEV_MONTH_DAYS=[31,28,31,30,31,30,31,31,30,31,30,31]
DEV_LABELS=['UP','DOWN','FLAT','UP','UP','FLAT','UP','DOWN','UP','FLAT','DOWN','FLAT']
VAL_MONTH_DAYS=[31,28,31,30,31,30,31,31,30,31,30,31, 31,29,31,30,31,30,31,31,30,31, 17]
_val_lab=['UP']*11+['FLAT']*6+['DOWN']*5
rr=np.random.default_rng(5); rr.shuffle(_val_lab)
VAL_LABELS=_val_lab+['FLAT']   # kısmi Kasım (G11'de M'ye sayılmaz)

ARMS={ # kind, N_dev(yıl), fee_R, taker_R extra, dly cost, wall cost, ws (T win gross mean)
 'S5': dict(kind='S',Nd=1760,fee=.17,tk=.12,dly=.03,wall=.0),
 'T5': dict(kind='T',Nd=1560,fee=.03,tk=.02,dly=.005,wall=.0,ws=.195),
 'S15':dict(kind='S',Nd=640 ,fee=.09,tk=.067,dly=.02,wall=.01),
 'T15':dict(kind='T',Nd=670 ,fee=.03,tk=.02,dly=.005,wall=.005,ws=.25),
 'S30':dict(kind='S',Nd=335 ,fee=.065,tk=.046,dly=.015,wall=.04),
 'T30':dict(kind='T',Nd=375 ,fee=.03,tk=.02,dly=.005,wall=.02,ws=.31),
}

def gross_draw(arm,n,ploss,rng):
    """ploss: n-vektör. Döner: brüt R (ücret öncesi)."""
    u=rng.random(n)
    if arm['kind']=='S':
        pb,pt=.20,.15
        run=0.8+0.4*rng.exponential(1.5,size=n)
        return np.where(u<ploss,-1.0,np.where(u<ploss+pb,0.4,np.where(u<ploss+pb+pt,0.8,run)))
    else:
        ws=arm['ws']; win=rng.gamma(2.0,ws/2.0,size=n)
        return np.where(u<ploss,-1.0,win)

def pf_of(r):
    w=r[r>0].sum(); l=-r[r<0].sum()
    return (w/l) if l>0 else 5.0

def calibrate(arm,target,rng):
    lo,hi=0.01,0.98
    for _ in range(40):
        mid=(lo+hi)/2
        g=gross_draw(arm,300000,np.full(300000,mid),rng)-arm['fee']
        if pf_of(g)>target: lo=mid
        else: hi=mid
    return (lo+hi)/2

def pboot(x,b,B,rng):
    D=len(x); m=x.mean(); sd=x.std(ddof=1)
    if sd==0: return 1.0
    tobs=m/(sd/np.sqrt(D)); xt=x-m
    nb=-(-D//b)
    starts=rng.integers(0,D,size=(B,nb))
    idx=((starts[:,:,None]+np.arange(b))%D).reshape(B,-1)[:,:D]
    s=xt[idx]; mm=s.mean(1); ss=s.std(1,ddof=1); ss[ss==0]=1e-12
    t=mm/(ss/np.sqrt(D))
    return (1+(t>=tobs).sum())/(B+1)

def make_set(arm,p0,ndays,month_days,labels,scn,rng,Nyear):
    nm=len(month_days); dom=np.repeat(np.arange(nm),month_days)  # gün->ay
    D=ndays
    lam=Nyear/365.0
    act=rng.gamma(3.0,1/3.0,size=D)       # gün aktivitesi (aşırı dağılım)
    n_d=rng.poisson(lam*act)
    N=int(n_d.sum())
    day=np.repeat(np.arange(D),n_d)
    month=dom[day]
    sym=rng.integers(0,8,size=N)
    e_m=rng.normal(0,scn['tauS'] if arm['kind']=='S' else scn['tauT'],size=nm)[month]
    e_s=rng.normal(0,scn['sym_sd'],size=8)[sym]
    lab=np.array(labels)[month]
    e_r=np.where(lab=='UP',-scn['rho'],np.where(lab=='DOWN',scn['rho'],0.0))
    ploss=np.clip(p0+e_m+e_s+e_r,0.01,0.97)
    g=gross_draw(arm,N,ploss,rng)
    return dict(g=g,day=day,month=month,sym=sym,lab=lab,D=D,nm=nm,N=N)

def net(s,arm,extra=0.0,mult=1.0):
    return s['g']-arm['fee']*mult-extra

def daily(s,r):
    return np.bincount(s['day'],weights=r,minlength=s['D'])

def grp_pf(r,lab,label):
    sel=lab==label
    return int(sel.sum()),(pf_of(r[sel]) if sel.sum()>0 else float('nan'))

def dev_gates(s,arm,scn,rng,fee_gate_mult,B=599):
    r=net(s,arm); N=s['N']; out={}
    out['G1']=N>=200
    pf=pf_of(r); out['G2']=pf>=1.15
    x=daily(s,r); p7=pboot(x,7,B,rng)
    out['G3']=p7<=0.2/6   # BH, tek-arm muhafazakâr
    bm=np.argmax(np.bincount(s['month'],weights=r,minlength=s['nm']))
    out['G4']=(pf_of(r[s['month']!=bm])>=1.0) and (np.sort(r)[:-5].sum()>0)
    ok=True
    for L in ('UP','FLAT','DOWN'):
        n,p=grp_pf(r,s['lab'],L)
        if n>=40 and p<0.80: ok=False
    out['G5']=ok
    rf=net(s,arm,mult=fee_gate_mult); rd=net(s,arm,extra=arm['dly'])+rng.normal(0,.15,size=N)*0.0
    rd=rd+rng.normal(0,0.12,size=N)  # farklı dolum kümesi gürültüsü
    rt=net(s,arm,extra=arm['tk'])
    out['G6']=(pf_of(rf)>=1.0) and (rd.sum()>0) and (pf_of(rt)>=1.0)
    out['_pf']=pf; out['_p7']=p7
    return out

def val_gates(s,arm,scn,rng,fee_mult,sd_dev,k=2,B=599,cfg=None):
    cfg=cfg or {}
    r=net(s,arm); N=s['N']; out={}
    out['G7']=N>=250
    pf=pf_of(r); out['G8']=pf>=1.15
    x=daily(s,r)
    p7=pboot(x,7,B,rng); p28=pboot(x,28,B,rng)
    out['G9']=(p7<=0.05/k) and ((p28<=0.10/k) if cfg.get('p28',True) else True)
    ok=True; cnt=0
    for L in ('UP','FLAT','DOWN'):
        n,p=grp_pf(r,s['lab'],L)
        if n>=40:
            cnt+=1
            if p<0.90: ok=False
    # kural 1: DOWN etiketli aylar DEV+VAL havuzlu PF>=1.10 (n>=100)
    rd_dev=net(sd_dev,arm)[sd_dev['lab']=='DOWN']; rd_val=r[s['lab']=='DOWN']
    pool=np.concatenate([rd_dev,rd_val])
    if cfg.get('rule1',True):
        if len(pool)<100 or pf_of(pool)<1.10: ok=False
    out['G10']=ok and cnt>=2
    mon=np.bincount(s['month'],weights=r,minlength=s['nm'])[:s['nm']-1]
    out['G11']=(mon>0).sum()>=math.ceil(0.55*(s['nm']-1))
    T=r.sum()
    if T<=0: out['G12']=False
    else:
        ssum=np.bincount(s['sym'],weights=r,minlength=8)
        bs=np.argmax(ssum); monall=np.bincount(s['month'],weights=r,minlength=s['nm']); bmo=np.argmax(monall)
        c=cfg.get('conc',dict(sym=.5,mon=.3,t1=.15,t5=.4))
        ok=True
        ok&= ssum[bs]<=c['sym']*T
        ok&= monall[bmo]<=c['mon']*T
        ok&= r.max()<=c['t1']*T and np.sort(r)[-5:].sum()<=c['t5']*T
        ok&= pf_of(r[s['sym']!=bs])>=1.05
        ok&= pf_of(r[s['month']!=bmo])>=1.05
        ok&= all(pf_of(r[s['sym']!=q])>=1.0 for q in range(8))
        out['G12']=bool(ok)
    cum=np.cumsum(x); dd=np.max(np.maximum.accumulate(cum)-cum)*1.25
    ann=T*365.0/s['D']
    out['G13']=(ann/max(dd,1e-9)>=0.75)
    rf=net(s,arm,mult=fee_mult); rdl=net(s,arm,extra=arm['dly'])+rng.normal(0,.12,size=N)
    rt=net(s,arm,extra=arm['tk']); rw=net(s,arm,extra=arm['wall'])
    out['G15']=(pf_of(rf)>=1.0) and (rdl.sum()>0) and (pf_of(rt)>=1.0) and (rw.sum()>0)
    y1=s['month']<12
    out['G16']=(pf_of(r[y1])>=1.0) and (pf_of(r[~y1])>=1.0)
    out['_pf']=pf; out['_p7']=p7; out['_p28']=p28; out['_dd_r']=dd
    return out

SCN={ 'A':dict(tauS=0.0,tauT=0.0,sym_sd=0.0,rho=0.0),
      'B':dict(tauS=0.02,tauT=0.03,sym_sd=0.02,rho=0.02),
      'C':dict(tauS=0.04,tauT=0.06,sym_sd=0.03,rho=0.04)}

def job(args):
    name,pf_t,scn_k,nsim,fee_mult,cfg,seed=args
    arm=ARMS[name]; scn=SCN[scn_k]
    rng=np.random.default_rng(seed)
    p0=calibrate(arm,pf_t,rng)
    dev_pass=0; val_pass=0; joint=0; dev_fail={}; val_fail={}
    for i in range(nsim):
        sd=make_set(arm,p0,365,DEV_MONTH_DAYS,DEV_LABELS,scn,rng,arm['Nd'])
        o=dev_gates(sd,arm,scn,rng,fee_mult)
        okd=all(v for kk,v in o.items() if kk.startswith('G'))
        dev_pass+=okd
        for kk,v in o.items():
            if kk.startswith('G') and not v: dev_fail[kk]=dev_fail.get(kk,0)+1
        sv=make_set(arm,p0,687,VAL_MONTH_DAYS,VAL_LABELS,scn,rng,arm['Nd'])
        o=val_gates(sv,arm,scn,rng,fee_mult,sd,cfg=cfg)
        okv=all(v for kk,v in o.items() if kk.startswith('G'))
        val_pass+=okv; joint+=(okd and okv)
        for kk,v in o.items():
            if kk.startswith('G') and not v: val_fail[kk]=val_fail.get(kk,0)+1
    return dict(arm=name,pf=pf_t,scn=scn_k,p0=round(p0,3),dev=dev_pass/nsim,val=val_pass/nsim,joint=joint/nsim,dev_fail=dev_fail,val_fail=val_fail)

PFS=tuple(float(v) for v in __import__("os").environ.get("PFS","1.0,1.15,1.3,1.5").split(","))
if __name__=="__main__":
    scn_k=sys.argv[1]; fee_mult=float(sys.argv[2]); nsim=int(sys.argv[3]); cfgs=sys.argv[4] if len(sys.argv)>4 else '{}'
    cfg=json.loads(cfgs)
    jobs=[]
    for i,name in enumerate(ARMS):
        for pf_t in PFS:
            jobs.append((name,pf_t,scn_k,nsim,fee_mult,cfg,1000+17*i+int(pf_t*100)))
    with Pool(4) as p: res=p.map(job,jobs)
    print(f"# senaryo={scn_k} FEEGATE={fee_mult} nsim={nsim} cfg={cfgs}")
    for name in ARMS:
        rows=[r for r in res if r['arm']==name]
        print(f"{name:4s}",' | '.join(f"PF{r['pf']}: dev {r['dev']:.2f} val {r['val']:.2f} JOINT {r['joint']:.2f}" for r in rows))
    tag=cfgs.replace(' ','').replace('"','').replace('{','').replace('}','').replace(',','_').replace(':','')[:40]
    json.dump(res,open(f"res3_{scn_k}_{fee_mult}_{nsim}_{tag}.json",'w'))
