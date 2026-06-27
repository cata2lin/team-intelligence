# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9"]
# ///
"""creative_fatigue.py — detectează SATURAȚIA de audiență/creative pe conturile Meta + TikTok:
frecvența urcă + CTR scade + CPM/CPA cresc = audiența s-a plictisit → refresh creative / lărgește
targetarea. Sursă: metrics.{meta,tiktok}_ad_insights_daily (la nivel de CONT/zi — nu per-creativ;
pt drill per-creativ folosește gigi:meta-ads / gigi:tiktok-ads pe contul flagat). Compară fereastra
RECENTĂ vs BASELINE (relativ la ultima zi cu date — robust la întârzieri de sync).

  uv run creative_fatigue.py                         # toate conturile, ambele platforme
  uv run creative_fatigue.py --platform meta --recent 7 --baseline 21
  uv run creative_fatigue.py --all                   # arată și conturile sănătoase
"""
import os, sys, argparse, subprocess
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import psycopg2

def kb(k):
    v=os.environ.get(k)
    if v: return v
    p=os.path.join(os.path.dirname(__file__),"..","..","..","..","core","scripts","kb.py")
    return subprocess.run(["uv","run",p,"secret-get",k],capture_output=True,text=True,timeout=60).stdout.strip()
def clean(url):
    p=urlsplit(url); OK={"host","port","dbname","user","password","sslmode","connect_timeout"}
    if p.query: url=urlunsplit((p.scheme,p.netloc,p.path,urlencode([(x,y) for x,y in parse_qsl(p.query,True) if x.lower() in OK]),p.fragment))
    return url
def conn():
    c=psycopg2.connect(clean(os.getenv("DATABASE_URL_METRICS") or kb("DATABASE_URL_METRICS"))); c.set_session(readonly=True); return c

def pct(new,old): return None if not old else (new/old-1)
def fetch(cx, tbl, brandmap, recent, baseline):
    q=f"""
    WITH mx AS (SELECT MAX(date) m FROM {tbl}),
    win AS (
      SELECT i."adAccountId" aid, a.name aname,
        CASE WHEN i.date > (SELECT m FROM mx) - %s THEN 'r' ELSE 'b' END seg,
        SUM(i."spendRon") spend, SUM(i.impressions) impr, SUM(i.clicks) clk,
        SUM(i.purchases) pur, SUM(i."purchaseValueRon") val,
        SUM(i.frequency*i.impressions) freqw
      FROM {tbl} i LEFT JOIN meta_ad_accounts a ON a.id=i."adAccountId"
      WHERE i.date > (SELECT m FROM mx) - %s
      GROUP BY 1,2,3)
    SELECT aid,aname,seg,spend,impr,clk,pur,val,freqw FROM win
    """
    # NOTE: meta_ad_accounts join also resolves tiktok names? no — handle name per platform below
    with cx.cursor() as c:
        c.execute(q,[recent, recent+baseline]); rows=c.fetchall()
    acc={}
    for aid,aname,seg,spend,impr,clk,pur,val,freqw in rows:
        d=acc.setdefault(aid,{"name":aname,"r":{},"b":{}})
        m={"spend":float(spend or 0),"impr":float(impr or 0),"clk":float(clk or 0),
           "pur":float(pur or 0),"val":float(val or 0),"freqw":float(freqw or 0)}
        d[seg]=m
    return acc

def derive(m):
    if not m or not m.get("impr"): return None
    return {"spend":m["spend"],"ctr":m["clk"]/m["impr"] if m["impr"] else 0,
            "cpm":m["spend"]/m["impr"]*1000 if m["impr"] else 0,
            "cpa":m["spend"]/m["pur"] if m["pur"] else None,
            "roas":m["val"]/m["spend"] if m["spend"] else 0,
            "freq":m["freqw"]/m["impr"] if m["impr"] else 0}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--platform", choices=["meta","tiktok","both"], default="both")
    ap.add_argument("--recent", type=int, default=7); ap.add_argument("--baseline", type=int, default=21)
    ap.add_argument("--all", action="store_true", help="arată și conturile fără semnal de fatigue")
    ap.add_argument("--min-spend", type=float, default=300, help="ignoră conturi cu spend recent sub prag (RON)")
    a=ap.parse_args()
    cx=conn()
    # brand map (pt etichetă) — meta + tiktok
    bm={}
    with cx.cursor() as c:
        for tbl in ("brand_meta_ad_accounts","brand_tiktok_ad_accounts"):
            try:
                c.execute(f'SELECT x."adAccountId", b.name FROM {tbl} x JOIN brands b ON b.id=x."brandId"')
                for aid,bn in c.fetchall(): bm[aid]=bn.strip()
            except Exception: pass
    plats=[("meta","meta_ad_insights_daily"),("tiktok","tiktok_ad_insights_daily")]
    if a.platform!="both": plats=[p for p in plats if p[0]==a.platform]
    # freshness
    with cx.cursor() as c:
        c.execute("SELECT MAX(date)::text FROM meta_ad_insights_daily"); fr=c.fetchone()[0]
    print(f"\n=== Creative / audience fatigue — recent {a.recent}z vs baseline {a.baseline}z ===")
    print(f"(date la nivel de CONT; ultima zi cu date ≈ {fr}. Drill per-creativ: gigi:meta-ads / gigi:tiktok-ads pe contul flagat)")
    flagged=0
    for pname,tbl in plats:
        # tiktok names live in tiktok_ad_accounts; meta in meta_ad_accounts — patch query per table
        acc=fetch(cx, tbl, bm, a.recent, a.baseline) if pname=="meta" else fetch_tt(cx, tbl, a.recent, a.baseline)
        scored=[]
        for aid,d in acc.items():
            r=derive(d.get("r")); b=derive(d.get("b"))
            if not r or not b or r["spend"]<a.min_spend: continue
            df=pct(r["freq"],b["freq"]); dctr=pct(r["ctr"],b["ctr"])
            dcpm=pct(r["cpm"],b["cpm"]); dcpa=pct(r["cpa"],b["cpa"]) if (r["cpa"] and b["cpa"]) else None
            fatigue = (df is not None and df>0.15) and ((dctr is not None and dctr<-0.10) or (dcpa is not None and dcpa>0.20))
            label=bm.get(aid) or d["name"] or aid[:10]
            scored.append((fatigue,r["spend"],label,df,dctr,dcpm,dcpa,r,b))
        scored.sort(key=lambda x:(0 if x[0] else 1, -x[1]))
        show=[s for s in scored if s[0]] or []
        if a.all: show=scored
        print(f"\n— {pname.upper()} — {sum(1 for s in scored if s[0])} cont(uri) cu fatigue / {len(scored)} active")
        if not show and not a.all: continue
        print(f"{'Cont/Brand':22} {'spend7':>8} {'Δfreq':>6} {'ΔCTR':>6} {'ΔCPM':>6} {'ΔCPA':>6} {'freq':>5} {'ROAS':>5}")
        for fatigue,spend,label,df,dctr,dcpm,dcpa,r,b in show[:20]:
            flag="🔥" if fatigue else "  "
            if fatigue: flagged+=1
            f=lambda x:(f"{x:+.0%}" if x is not None else "  —")
            print(f"{flag}{label[:20]:20} {spend:>8,.0f} {f(df):>6} {f(dctr):>6} {f(dcpm):>6} {f(dcpa):>6} {r['freq']:>5.1f} {r['roas']:>5.1f}")
    print(f"\n🔥 = frecvență ↑>15% ȘI (CTR ↓>10% SAU CPA ↑>20%) = audiență saturată → spune agenției: refresh creative / lărgește audiența.")
    print(f"Total {flagged} conturi de atins. Δ = recent vs baseline. freq = afișări/persoană în fereastra recentă.")

def fetch_tt(cx, tbl, recent, baseline):
    q=f"""WITH mx AS (SELECT MAX(date) m FROM {tbl})
    SELECT i."adAccountId", a.name,
      CASE WHEN i.date > (SELECT m FROM mx) - %s THEN 'r' ELSE 'b' END seg,
      SUM(i."spendRon"), SUM(i.impressions), SUM(i.clicks), SUM(i.purchases),
      SUM(i."purchaseValueRon"), SUM(i.frequency*i.impressions)
    FROM {tbl} i LEFT JOIN tiktok_ad_accounts a ON a.id=i."adAccountId"
    WHERE i.date > (SELECT m FROM mx) - %s GROUP BY 1,2,3"""
    with cx.cursor() as c: c.execute(q,[recent, recent+baseline]); rows=c.fetchall()
    acc={}
    for aid,aname,seg,spend,impr,clk,pur,val,freqw in rows:
        d=acc.setdefault(aid,{"name":aname,"r":{},"b":{}})
        d[seg]={"spend":float(spend or 0),"impr":float(impr or 0),"clk":float(clk or 0),
                "pur":float(pur or 0),"val":float(val or 0),"freqw":float(freqw or 0)}
    return acc

if __name__=="__main__": main()
