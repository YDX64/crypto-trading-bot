import numpy as np, sys
rng=np.random.default_rng(1)
def cbb_p(x,b,B,rng):
    D=len(x); xt=x-x.mean()
    sd=x.std(ddof=1)
    if sd==0: return 1.0
    t_obs=x.mean()/(sd/np.sqrt(D))
    nb=-(-D//b)
    starts=rng.integers(0,D,size=(B,nb))
    idx=(starts[:,:,None]+np.arange(b)[None,None,:])%D
    idx=idx.reshape(B,-1)[:,:D]
    s=xt[idx]
    m=s.mean(1); sdv=s.std(1,ddof=1)
    t=m/(sdv/np.sqrt(D))
    return (1+(t>=t_obs).sum())/(B+1)
def sim(D,tau,nsim,B,b_list,seed_shift=0,month=30,skew=True):
    rej={b:[0,0,0] for b in b_list}
    for s in range(nsim):
        mu=np.repeat(rng.normal(0,tau,size=D//month+1),month)[:D]
        e=rng.standard_t(4,size=D)/np.sqrt(2.0)   # unit var, fat tails
        x=mu+e
        x=x-0  # H0: E[x]=0
        for b in b_list:
            p=cbb_p(x,b,B,rng)
            rej[b][0]+=p<=0.05; rej[b][1]+=p<=0.025; rej[b][2]+=p<=0.01
    return {b:[r/nsim for r in v] for b,v in rej.items()}
import sys
D0=int(sys.argv[1])
for tau in (0.0,0.15,0.25):
    print('D',D0,'tau',tau, sim(D0,tau,500,999,[7,14,28,56]))
    sys.stdout.flush()
