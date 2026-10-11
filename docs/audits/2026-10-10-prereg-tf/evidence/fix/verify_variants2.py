import json,os,subprocess,sys
SP="/tmp/claude-0/-home-user-crypto-trading-bot/b53b3a36-e126-5de6-b27f-ca558d8408f8/scratchpad"
def load(p):
    d={}
    for l in open(p):
        l=l.strip()
        if l and '=' in l and not l.startswith('#'):
            k,v=l.split('=',1); d[k]=v
    return d
snap=load(f"{SP}/server-scalper-env.txt"); dummy=load(f"{SP}/e17/dummy.env")
V=json.load(open(f"{SP}/e17/variants.json"))
code='''
import json
from src.core.config import Settings
s=Settings(_env_file=None)
keys=["scalper_tf_entry","scalper_tf_context","scalper_tf_regime","scalper_regime_filter","scalper_market_gate","scalper_entry_block_weekdays_utc","scalper_max_hold_hours","scalper_loss_cooldown_minutes","scalper_dyn_lev_stop_atr_mult","scalper_fixed_stop_roi_pct","scalper_tp1_roi","scalper_tp2_roi","scalper_entry_mode","scalper_maker_fill_timeout_candles","scalper_stale_tp_hours"]
print(json.dumps({k:getattr(s,k,"<<MISSING>>") for k in keys}))
'''
def run(env_over):
    env=dict(dummy); env.update(snap); env.update(env_over)
    env['PATH']=os.environ['PATH']; env['PYTHONPATH']='/home/user/crypto-trading-bot'
    r=subprocess.run(['/home/user/crypto-trading-bot/.venv/bin/python','-c',code],env=env,cwd='/home/user/crypto-trading-bot',capture_output=True,text=True)
    return r
ovmap={o['id']:o for o in V['overlays']}
fam=[v for v in V['family']]
allv=V['family']+V['descriptive']+[x for x in V['references']]
bad=0
for v in allv:
    r=run(v['env'])
    ok=r.returncode==0 and '<<MISSING>>' not in r.stdout
    print(v['id'],'OK' if ok else 'FAIL', r.stdout.strip()[:330] if ok else r.stderr.strip()[-300:]); bad+= (not ok)
for v in V['family']:
    for oid in ('ROB_TAKER','ROB_WALL','DIAG_Z0'):
        env=dict(v['env']); env.update(ovmap[oid]['env'])
        r=run(env); ok=r.returncode==0
        if not ok: print(v['id'],oid,'FAIL',r.stderr.strip()[-200:]); bad+=1
print('overlay-compositions checked; failures:',bad)
# TF rol kurali: giris != baglam, giris != rejim
for v in V['family']+V['descriptive']:
    e,c,rg=v['triple']; assert e!=c and e!=rg, v['id']
# S aritmetik: stop% = S/L icinde [0.15,5.5]
for v in V['family']+V['descriptive']:
    if v['geometry'].startswith('scaled'):
        S=v['S_arm']; assert S/20>=0.15 and S/3<=5.5, (v['id'],S/20,S/3)
        print(v['id'],'stop band', round(S/20,3),'..',round(S/3,3))
