import json, os, sys, collections
SP="/tmp/claude-0/-home-user-crypto-trading-bot/b53b3a36-e126-5de6-b27f-ca558d8408f8/scratchpad"; E=SP+"/e17"
arm,start,end=sys.argv[1:4]
for line in open(f"{SP}/server-scalper-env.txt"):
    line=line.strip()
    if line.startswith("SCALPER_") and "=" in line:
        k,v=line.split("=",1); os.environ[k]=v
for line in open(f"{E}/dummy.env"):
    line=line.strip()
    if "=" in line:
        k,v=line.split("=",1); os.environ[k]=v
V={v["id"]:v for v in json.load(open(f"{E}/variants_draft.json"))}
for k,v in V[arm]["env"].items(): os.environ[k]=v
os.environ["TRADINGBOT_LOG_DIR"]=f"{E}/rev_sim/lg"
sys.path.insert(0,"/home/user/crypto-trading-bot")
from src.strategies.scalper import backtest as B
C=collections.Counter()
orig=B._process_candle_exits
def wrap(pos,c,direction):
    pre=pos.tp1_filled
    if not pre:
        sl=B._is_hit(c,pos.current_stop,direction,is_stop=True); tp=B._is_hit(c,pos.tp1_price,direction,is_stop=False)
        if sl and tp: C["SL_and_TP1_same_bar"]+=1
        elif sl: C["SL_only"]+=1
        elif tp: C["TP1_hit"]+=1
    r=orig(pos,c,direction)
    if not pre and pos.tp1_filled:
        C["tp1_filled_events"]+=1
        if r and pos.exit_reason=="TRAIL" and pos.exit_time==c.close_time: C["TP1_then_BE_same_bar"]+=1
    return r
B._process_candle_exits=wrap
sys.argv=["bt","--strategies","C","--symbols","BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,BNBUSDT,ADAUSDT,LTCUSDT","--start",start,"--end",end,"--cache-dir",f"{E}/cal_cache"]
os.makedirs(f"{E}/rev_sim/w_{arm}",exist_ok=True); os.chdir(f"{E}/rev_sim/w_{arm}")
import io, contextlib
buf=io.StringIO()
with contextlib.redirect_stdout(buf):
    B.main()
print(arm,dict(C))
