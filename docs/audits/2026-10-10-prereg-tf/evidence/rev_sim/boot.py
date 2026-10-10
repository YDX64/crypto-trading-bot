import random, math, time, sys
def p(x,b=7,B=19999,seed=1):
    D=len(x); m=sum(x)/D; xs=[v-m for v in x]
    sd=math.sqrt(sum((v-m)**2 for v in x)/(D-1)); t=m/(sd/math.sqrt(D))
    rnd=random.Random(seed); nb=-(-D//b); cnt=0
    for _ in range(B):
        s=[]
        for _ in range(nb):
            st=int(rnd.random()*D)
            s.extend(xs[(st+j)%D] for j in range(b))   # circular
        s=s[:D]; mm=sum(s)/D; v=sum((q-mm)**2 for q in s)/(D-1)
        if v>0 and mm/math.sqrt(v/D)>=t: cnt+=1
    return (1+cnt)/(B+1)
for D in (365,687):
    r=random.Random(5); x=[r.gauss(0.0,3) for _ in range(D)]
    t0=time.time(); p(x); print(D,'one p',round(time.time()-t0,1),'s')
