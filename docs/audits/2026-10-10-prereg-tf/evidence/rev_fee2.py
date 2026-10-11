import numpy as np
rng=np.random.default_rng(3)
def trades(N,ps,fee,rscale=1.5):
    u=rng.random(N); pb=0.20; pt=0.15
    r=np.where(u<ps,-1.0,np.where(u<ps+pb,0.4,np.where(u<ps+pb+pt,0.8,0.8+0.4*rng.exponential(rscale,size=N))))
    return r-fee
def pf(r):
    w=r[r>0].sum(); l=-r[r<0].sum(); return w/l
def base_pf_needed(fee):
    lo,hi=0.02,0.95
    for _ in range(50):
        mid=(lo+hi)/2
        if pf(trades(600000,mid,2*fee))>1.0: lo=mid
        else: hi=mid
    return pf(trades(600000,mid,fee)), mid
for nm,fee in [('S5',0.17),('S15',0.09),('S30',0.065)]:
    print(nm,fee,base_pf_needed(fee))
