import dlcount as D, asyncio
from src.strategies.scalper import backtest as B
def uniq(tfs,s,e):
    days=max(1,-(-(D.ms(e)-D.ms(s))//86400000)); en=D.ms(e)
    dl=set(); mo=set()
    for tf in tfs:
        src=D.Src()
        async def run():
            for si in range(8):
                for iv,w in zip(tf,B._WARMUP_BY_ROLE):
                    await B.fetch_paginated(src,f"S{si}",iv,days*B._CANDLES_PER_DAY[iv]+w,end_time=en)
        asyncio.run(run()); dl|=src.daily; mo|=src.monthly
    return len(dl),len(mo)
tfs=[('5m','30m','1h'),('15m','1h','4h'),('30m','4h','4h'),('1h','4h','4h'),('1m','5m','15m')]
for n,(s,e),t in [('DEV',('2025-01-01','2026-01-01'),tfs),('VAL',('2023-01-01','2024-11-18'),tfs[:4]),('FINAL',('2026-09-03','2026-10-08'),[tfs[0],tfs[4]])]:
    d,m=uniq(t,s,e); print(n,'unique daily',d,'monthly',m,'requests(zip+sum)',2*(d+m))
