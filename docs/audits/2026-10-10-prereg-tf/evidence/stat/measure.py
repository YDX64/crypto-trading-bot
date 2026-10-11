"""CAL measurement: counts + signal-time ATR% only. NEVER prints pnl/PF. Usage: measure.py <label> <start> <end> <cache_dir> <out.json>"""
import sys, os, json, asyncio, datetime as dt, collections
sys.path.insert(0, "/home/user/crypto-trading-bot")
from src.strategies.scalper import backtest as bt
from src.core.config import settings
label, start, end, cache, out = sys.argv[1:6]
def ms(d): return int(dt.datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()*1000)
rec = []          # every open_position call
orig = bt.open_position
def wrap(signal, candles, idx, cfg, balance=bt._DEFAULT_VIRTUAL_BALANCE, missed_counter=None, entry_delay_candles=0):
    pos = orig(signal, candles, idx, cfg, balance, missed_counter, entry_delay_candles)
    sd = abs(signal.entry_price - signal.stop_price) / signal.entry_price * 100.0
    rec.append(dict(sym=signal.symbol, dir=signal.direction.value, atr_pct=signal.atr_5m/signal.entry_price*100.0,
                    lev=int(signal.leverage or 0), stop_pct=sd, opened=pos is not None, t=candles[idx].close_time))
    return pos
bt.open_position = wrap
async def main():
    s, e = ms(start), ms(end)
    days = max(1, -(-(e - s) // 86_400_000))
    mc = {}
    trades = await bt.run_backtest(days, ["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","DOGEUSDT","BNBUSDT","ADAUSDT","LTCUSDT"], "C",
                                   cfg=settings, missed_counter=mc, end_time_ms=e, start_time_ms=s, cache_dir=cache)
    res = dict(label=label, days=days, n_calls=len(rec), n_opened_pre_capacity=sum(r["opened"] for r in rec),
               n_trades=len(trades), missed=mc,
               rec=rec,
               trades_by_sym=dict(collections.Counter(t.symbol for t in trades)),
               trades_by_dir=dict(collections.Counter(t.direction for t in trades)),
               trades_by_regime=dict(collections.Counter(t.regime for t in trades)),
               lev=[t.leverage for t in trades],
               dur_min=[t.duration_minutes for t in trades],
               exit_reasons_only_eod=sum(1 for t in trades if t.exit_reason == "EOD"))
    json.dump(res, open(out, "w"))
    print(label, "days", days, "signal_calls", len(rec), "trades", len(trades), "missed", mc)
asyncio.run(main())
