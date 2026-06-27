# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9"]
# ///
"""data_analytics.py — analitică de CLIENȚI pe datele ARONA (AWBprint = livrat, venit real, COD).
RFM segmentare · Cohort retention · LTV per cohortă · Forecast cerere. Identitate = customer_email,
venit = comenzi DELIVERED (refuzul COD nu e venit). Monetar în moneda magazinului (per-store).

  uv run data_analytics.py rfm --store esteban.ro
  uv run data_analytics.py cohort --store esteban.ro --months 12
  uv run data_analytics.py ltv --store esteban.ro
  uv run data_analytics.py forecast --store esteban.ro --weeks 8
  uv run data_analytics.py all --store esteban.ro
"""
import os, sys, argparse, subprocess, datetime as dt
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2, psycopg2.extras

def kb(k):
    v=os.environ.get(k)
    if v: return v
    p=os.path.join(os.path.dirname(__file__),"..","..","..","..","core","scripts","kb.py")
    return subprocess.run(["uv","run",p,"secret-get",k],capture_output=True,text=True,timeout=60).stdout.strip()
def conn():
    url=os.getenv("DATABASE_URL_AWBPRINT") or kb("DATABASE_URL_AWBPRINT")
    p=urlsplit(url); OK={"host","port","dbname","user","password","sslmode","connect_timeout"}
    if p.query: url=urlunsplit((p.scheme,p.netloc,p.path,urlencode([(x,y) for x,y in parse_qsl(p.query,True) if x.lower() in OK]),p.fragment))
    c=psycopg2.connect(url); c.set_session(readonly=True); return c

def store_clause(store): return ("AND s.name ILIKE %s", [f"%{store}%"]) if store else ("",[])

def rfm(cx, store):
    w,p=store_clause(store)
    q=f"""WITH d AS (
      SELECT o.customer_email ce, MAX(o.frisbo_created_at::date) last_o, COUNT(*) freq, COALESCE(SUM(o.total_price),0) mon
      FROM orders o JOIN stores s ON s.uid=o.store_uid
      WHERE o.aggregated_status='delivered' AND o.customer_email<>'' {w}
      GROUP BY 1),
    sc AS (SELECT ce, last_o, freq, mon,
      NTILE(5) OVER (ORDER BY last_o) r, NTILE(5) OVER (ORDER BY freq) f, NTILE(5) OVER (ORDER BY mon) m FROM d)
    SELECT r,f,m,COUNT(*) n, ROUND(SUM(mon)) val, ROUND(AVG(freq),2) afreq FROM sc GROUP BY 1,2,3"""
    with cx.cursor() as c: c.execute(q,p); rows=c.fetchall()
    seg={}
    for r,f,m,n,val,af in rows:
        fm=(f+m)/2
        if r>=4 and fm>=4: s="🏆 Champions"
        elif r>=3 and fm>=3: s="💚 Loyal"
        elif r>=4 and fm<3: s="✨ New/Promising"
        elif r<=2 and fm>=4: s="⚠️ At-Risk (valoroși, dispar)"
        elif r<=2 and fm<=2: s="💀 Lost/Hibernating"
        else: s="😐 Need attention"
        d=seg.setdefault(s,[0,0]); d[0]+=n; d[1]+=val or 0
    tot=sum(v[0] for v in seg.values()) or 1
    print(f"\n=== RFM — {store or 'toate'} (clienți delivered) ===")
    print(f"{'Segment':32} {'Clienți':>8} {'%':>5} {'Valoare':>12}")
    for s,(n,val) in sorted(seg.items(),key=lambda i:-i[1][1]):
        print(f"{s:32} {n:>8} {100*n/tot:>4.0f}% {val:>12,.0f}")

def cohort(cx, store, months):
    w,p=store_clause(store)
    q=f"""WITH o2 AS (SELECT o.customer_email ce, date_trunc('month',o.frisbo_created_at)::date m
      FROM orders o JOIN stores s ON s.uid=o.store_uid
      WHERE o.aggregated_status='delivered' AND o.customer_email<>'' {w}),
    first AS (SELECT ce, MIN(m) cohort FROM o2 GROUP BY 1)
    SELECT f.cohort, (EXTRACT(YEAR FROM age(o2.m,f.cohort))*12+EXTRACT(MONTH FROM age(o2.m,f.cohort)))::int off, COUNT(DISTINCT o2.ce) n
    FROM o2 JOIN first f USING(ce) GROUP BY 1,2 ORDER BY 1,2"""
    with cx.cursor() as c: c.execute(q,p); rows=c.fetchall()
    data={}; size={}
    for coh,off,n in rows:
        data[(coh,off)]=n
        if off==0: size[coh]=n
    cohs=sorted(size, reverse=True)[:months]
    print(f"\n=== Cohort retention — {store or 'toate'} (% clienți care recumpără) ===")
    hdr="Cohortă     mărime " + " ".join(f"M{i:>2}" for i in range(0,7))
    print(hdr)
    for coh in sorted(cohs):
        base=size.get(coh,0) or 1
        cells=" ".join(f"{100*data.get((coh,i),0)/base:>3.0f}" for i in range(0,7))
        print(f"{str(coh):12}{base:>6}  {cells}")
    print("(M0=100%; M1=% reveniți luna următoare, etc.)")

def ltv(cx, store):
    w,p=store_clause(store)
    q=f"""WITH d AS (
      SELECT o.customer_email ce, date_trunc('month',MIN(o.frisbo_created_at))::date cohort,
        COUNT(*) orders, COALESCE(SUM(o.total_price),0) rev
      FROM orders o JOIN stores s ON s.uid=o.store_uid
      WHERE o.aggregated_status='delivered' AND o.customer_email<>'' {w} GROUP BY 1)
    SELECT cohort, COUNT(*) custs, ROUND(AVG(orders),2) aov_orders, ROUND(AVG(rev)) ltv,
      ROUND(100.0*SUM((orders>1)::int)/COUNT(*),1) repeat_pct
    FROM d GROUP BY 1 ORDER BY 1 DESC LIMIT 12"""
    with cx.cursor() as c: c.execute(q,p); rows=c.fetchall()
    print(f"\n=== LTV per cohortă — {store or 'toate'} (delivered, moneda magazinului) ===")
    print(f"{'Cohortă':12} {'Clienți':>8} {'Cmd/client':>11} {'LTV':>10} {'Repeat%':>8}")
    for coh,cu,ao,lt,rp in rows:
        print(f"{str(coh):12} {cu:>8} {ao:>11} {lt:>10,.0f} {rp:>7}%")
    print("(LTV = venit mediu livrat/client; Repeat% = clienți cu 2+ comenzi)")

def forecast(cx, store, weeks):
    w,p=store_clause(store)
    q=f"""SELECT date_trunc('week',o.frisbo_created_at)::date wk, COUNT(*) n, ROUND(SUM(o.total_price)) rev
      FROM orders o JOIN stores s ON s.uid=o.store_uid
      WHERE o.aggregated_status='delivered' AND o.frisbo_created_at > now()-interval '52 weeks' {w}
      GROUP BY 1 ORDER BY 1"""
    with cx.cursor() as c: c.execute(q,p); rows=c.fetchall()
    if len(rows)<8: print("\nforecast: date insuficiente"); return
    series=rows[:-1]  # drop current partial week
    last8=series[-8:]
    avg_n=sum(r[1] for r in last8)/len(last8); avg_rev=sum(r[2] for r in last8)/len(last8)
    # trend: compara ultimele 4 vs precedentele 4
    a=sum(r[1] for r in series[-4:])/4; b=sum(r[1] for r in series[-8:-4])/4
    trend=(a-b)/b if b else 0
    print(f"\n=== Forecast cerere — {store or 'toate'} (delivered/săptămână) ===")
    print(f"  media ultimele 8 săpt: {avg_n:.0f} comenzi / {avg_rev:,.0f} venit")
    print(f"  trend 4săpt vs 4săpt anterioare: {trend:+.0%}")
    print(f"  prognoză următoarele {weeks} săptămâni (MA + trend):")
    for i in range(1,weeks+1):
        fn=avg_n*(1+trend*i*0.5); fr=avg_rev*(1+trend*i*0.5)
        print(f"    +{i}săpt: ~{fn:.0f} comenzi / ~{fr:,.0f} venit")
    print("(model simplu MA8 + trend liniar amortizat; pt sezonalitate fină = model dedicat)")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("analysis", choices=["rfm","cohort","ltv","forecast","all"])
    ap.add_argument("--store", default="esteban.ro", help="filtru magazin (ILIKE); '' = toate (atenție monedă)")
    ap.add_argument("--months", type=int, default=12); ap.add_argument("--weeks", type=int, default=8)
    a=ap.parse_args()
    store=None if a.store=="" else a.store
    cx=conn()
    if a.analysis in ("rfm","all"): rfm(cx,store)
    if a.analysis in ("cohort","all"): cohort(cx,store,a.months)
    if a.analysis in ("ltv","all"): ltv(cx,store)
    if a.analysis in ("forecast","all"): forecast(cx,store,a.weeks)

if __name__=="__main__":
    main()
