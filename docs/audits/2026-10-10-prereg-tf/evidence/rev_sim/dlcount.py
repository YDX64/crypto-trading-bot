import sys, asyncio, calendar
sys.path.insert(0,'/home/user/crypto-trading-bot'); sys.path.insert(0,'/home/user/crypto-trading-bot/scripts')
import fill_klines_cache_from_vision as F
from src.strategies.scalper import backtest as B, kline_cache as K
from src.strategies.scalper.types import Candle
from datetime import date, datetime, timezone
class Src(F.ArchiveKlineSource):
    def __init__(s,*a,**k):
        super().__init__(None, today=date(2026,10,10)); s.daily=set(); s.monthly=set()
    def _load_daily(s,sym,iv,day):
        s.daily.add((sym,iv,day)); s._fill(sym,iv,F._ms_of_date(day),F._ms_of_date(day)+86400000)
    def _load_monthly(s,sym,iv,y,m):
        a,b=F._month_bounds(y,m); s.monthly.add((sym,iv,y,m)); s._fill(sym,iv,F._ms_of_date(a),F._ms_of_date(b)+86400000); return True
    def _fill(s,sym,iv,lo,hi):
        st=K._INTERVAL_MS[iv]; store=s._store.setdefault((sym,iv),{})
        for o in range(lo,hi,st): store[o]=Candle(open_time=o,open=1,high=1,low=1,close=1,volume=1,close_time=o+st-1)
def ms(d): y,m,dd=map(int,d.split('-')); return int(datetime(y,m,dd,tzinfo=timezone.utc).timestamp()*1000)
def count(tf, start, end, syms=8):
    days=max(1,-(-(ms(end)-ms(start))//86400000)); e=ms(end)
    src=Src(); 
    async def run():
        for si in range(syms):
            sym=f"S{si}"
            for iv,w in zip(tf,B._WARMUP_BY_ROLE):
                need=days*B._CANDLES_PER_DAY[iv]+w
                await B.fetch_paginated(src,sym,iv,need,end_time=e)
    asyncio.run(run())
    return len(src.daily),len(src.monthly)
if __name__=='__main__':
    sets={'DEV':('2025-01-01','2026-01-01'),'VAL':('2023-01-01','2024-11-18'),'FINAL':('2026-09-03','2026-10-08')}
    tfs=[('5m','30m','1h'),('15m','1h','4h'),('30m','4h','4h'),('1h','4h','4h'),('1m','5m','15m')]
    tot={}
    for n,(s,e) in sets.items():
        for tf in tfs:
            if n=='VAL' and tf==('1m','5m','15m'): continue
            if n=='FINAL' and tf not in [('1m','5m','15m')]+tfs[:0]: pass
            d,m=count(tf,s,e); print(n,tf,'daily',d,'monthly',m,'requests',2*(d+m)); tot[n]=tot.get(n,0)+2*(d+m)
    print(tot)
