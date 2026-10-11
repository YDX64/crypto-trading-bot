import urllib.request,zipfile,io,csv,datetime as dt,sys,json
def month_zip(y,m):
    u=f"https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1d/BTCUSDT-1d-{y}-{m:02d}.zip"
    b=urllib.request.urlopen(u,timeout=30).read()
    z=zipfile.ZipFile(io.BytesIO(b)); rows=list(csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]))))
    return [r for r in rows if r and r[0].isdigit()]
out={}
for y,m in [(y,m) for y in (2023,2024,2025) for m in range(1,13)]:
    try: rows=month_zip(y,m)
    except Exception as e: print(y,m,'ERR',e); continue
    o=float(rows[0][1]); c=float(rows[-1][4]); ret=(c/o-1)*100
    mx=max(abs(float(r[4])/float(r[1])-1)*100 for r in rows)
    lab='UP' if ret>=5 else 'DOWN' if ret<=-5 else 'FLAT'
    out[f"{y}-{m:02d}"]=(round(ret,2),lab,round(mx,1),len(rows))
for k,v in out.items(): print(k,v)
import collections
for per,(a,b) in {'VAL(2023-01..2024-10 + Nov1-17 partial excluded)':('2023-01','2024-10'),'DEV 2025':('2025-01','2025-12')}.items():
    c=collections.Counter(v[1] for k,v in out.items() if a<=k<=b); print(per,dict(c))
