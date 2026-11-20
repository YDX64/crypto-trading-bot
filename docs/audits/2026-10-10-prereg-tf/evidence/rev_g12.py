import numpy as np
rng=np.random.default_rng(7)
def trades(N,ps,fee,rscale):
    # outcome model (R units, S geometry): stop, BE-after-TP1, TP1+TP2, runner winners
    u=rng.random(N)
    pb=0.20; pt=0.15
    r=np.where(u<ps,-1.0,
      np.where(u<ps+pb,0.4,
      np.where(u<ps+pb+pt,0.8,
               0.8+0.4*rng.exponential(rscale,size=N))))
    return r-fee
def pf(r):
    w=r[r>0].sum(); l=-r[r<0].sum(); return w/l if l>0 else 9
def calibrate(target,N,fee,rscale):
    lo,hi=0.02,0.9
    for _ in range(40):
        mid=(lo+hi)/2
        r=trades(400000,mid,fee,rscale)
        if pf(r)>target: lo=mid
        else: hi=mid
    return mid
def g12(r,sym,month):
    T=r.sum()
    if T<=0: return False,'T<=0'
    best_sym=max(r[sym==s].sum() for s in range(8))
    if best_sym>0.40*T: return False,'sym40'
    bs=np.argmax([r[sym==s].sum() for s in range(8)])
    if pf(r[sym!=bs])<1.05: return False,'pf_ex_sym'
    bm=np.argmax([r[month==m].sum() for m in range(month.max()+1)])
    if pf(r[month!=bm])<1.05: return False,'pf_ex_month'
    if r.max()>0.08*T: return False,'best8'
    if np.sort(r)[-5:].sum()>0.25*T: return False,'top5_25'
    # LOO symbol PF>=1.0
    for s in range(8):
        if pf(r[sym!=s])<1.0: return False,'loo'
    return True,'ok'
for name,N,fee,rscale,months in [('S30 VAL',630,0.065,1.5,22),('S15 VAL',1210,0.09,1.5,22),('S5 VAL',3300,0.17,1.5,22),('S30 DEV',335,0.065,1.5,12)]:
    for tpf in (1.15,1.2,1.3,1.5):
        ps=calibrate(tpf,N,fee,rscale)
        ok=0;reasons={}
        M=800
        for _ in range(M):
            r=trades(N,ps,fee,rscale)
            sym=rng.integers(0,8,size=N); month=rng.integers(0,months,size=N)
            a,why=g12(r,sym,month)
            ok+=a; reasons[why]=reasons.get(why,0)+1
        print(name,'true PF',tpf,'G12-pass(given T>0 path)',ok/M, dict(sorted(reasons.items(),key=lambda x:-x[1])[:4]))
