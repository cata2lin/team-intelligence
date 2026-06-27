# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9"]
# ///
"""reorder_planner.py — cât și când reaprovizionezi fiecare SKU, ca să NU rămâi fără stoc pe winneri.
Sursă: metrics.inventory_daily_snapshots (FRESH zilnic) — viteză de vânzare din scăderile zilnice de
stoc, sold = SUM(max(prev_onHand - onHand, 0)) pe fereastră / zile. NU presupune lead time: îl dai tu.

  reorder_qty = viteză/zi × (lead_time + safety_days) − onHand − incoming   (rotunjit în sus, ≥0)
  stockout în = onHand / viteză/zi (zile)

  uv run reorder_planner.py --brand Grandia
  uv run reorder_planner.py --brand Esteban --lead-days 21 --safety-days 10 --top 30
  uv run reorder_planner.py --brand "George Talent" --days 28 --only-reorder
"""
import os, sys, math, argparse, subprocess
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

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--brand", required=True, help="nume brand (ILIKE) ex: Grandia / Esteban / 'George Talent'")
    ap.add_argument("--days", type=int, default=28, help="fereastra de viteză (zile snapshot)")
    ap.add_argument("--lead-days", type=int, default=30, help="lead time furnizor (zile până sosește marfa)")
    ap.add_argument("--safety-days", type=int, default=14, help="stoc de siguranță (zile tampon)")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--only-reorder", action="store_true", help="doar SKU-urile care trebuie comandate ACUM")
    a=ap.parse_args()
    cx=conn()
    q="""
    WITH daily AS (
      SELECT s.sku, s."snapshotDate" d,
             SUM(s."quantityOnHand") oh, SUM(s."quantityIncoming") inc,
             MAX(s."costPerItem") cost, MAX(s.price) price, MAX(s."productTitle") title
      FROM inventory_daily_snapshots s JOIN brands b ON b.id=s."brandId"
      WHERE TRIM(b.name) ILIKE %s AND s."snapshotDate" >= CURRENT_DATE - %s
      GROUP BY s.sku, s."snapshotDate"),
    lagged AS (SELECT sku,d,oh,inc,cost,price,title,
             LAG(oh) OVER (PARTITION BY sku ORDER BY d) prev FROM daily),
    vel AS (SELECT sku, SUM(GREATEST(prev-oh,0)) sold, COUNT(*) FILTER (WHERE prev IS NOT NULL) ndays
            FROM lagged GROUP BY sku),
    latest AS (SELECT DISTINCT ON (sku) sku,oh,inc,cost,price,title FROM daily ORDER BY sku,d DESC)
    SELECT l.sku,l.title,l.oh,l.inc,l.cost,l.price,v.sold,v.ndays
    FROM latest l JOIN vel v USING(sku)
    """
    with cx.cursor() as c:
        c.execute(q,[f"%{a.brand.strip()}%", a.days]); rows=c.fetchall()
    if not rows: print(f"Niciun SKU pt brand ILIKE '{a.brand}'. Verifică numele (brands.name)."); return
    horizon=a.lead_days+a.safety_days
    out=[]
    for sku,title,oh,inc,cost,price,sold,ndays in rows:
        oh=float(oh or 0); inc=float(inc or 0); price=float(price or 0); cost=float(cost or 0)
        vday=(float(sold or 0)/ndays) if ndays else 0.0
        cover=(oh/vday) if vday>0 else (999 if oh>0 else 0)
        need=vday*horizon-oh-inc
        reorder=max(0, math.ceil(need)) if vday>0 else 0
        rev_risk=vday*price            # venit/zi în joc dacă rămâi fără stoc
        po_cost=reorder*cost           # cât te costă comanda (COGS)
        out.append((sku,title or "",oh,inc,vday,cover,reorder,rev_risk,po_cost))
    # rank: produsele care se termină cel mai repede ȘI vând (rev_risk mare), reorder>0 sus
    out.sort(key=lambda r:(0 if r[6]>0 else 1, r[5], -r[7]))
    if a.only_reorder: out=[r for r in out if r[6]>0]
    print(f"\n=== Reorder planner — {a.brand} (viteză {a.days}z · lead {a.lead_days}z · safety {a.safety_days}z) ===")
    print(f"{'SKU':22} {'onHand':>7} {'incom':>6} {'vânz/zi':>8} {'cover(z)':>9} {'COMANDĂ':>8} {'venit/zi risc':>13}")
    urgent=0
    for sku,title,oh,inc,vday,cover,reorder,rev_risk,po in out[:a.top]:
        flag="🔴" if cover<a.lead_days and vday>0 else ("🟡" if cover<horizon and vday>0 else "  ")
        if flag=="🔴": urgent+=1
        cov=f"{cover:.0f}" if cover<999 else "∞"
        print(f"{flag}{sku[:20]:20} {oh:>7.0f} {inc:>6.0f} {vday:>8.2f} {cov:>9} {reorder:>8} {rev_risk:>13,.0f}")
        if title: print(f"   └ {title[:70]}")
    tot_reorder=sum(r[6] for r in out); tot_po=sum(r[8] for r in out)
    print(f"\n🔴 = stoc se termină ÎNAINTE să sosească marfa (cover<lead) → comandă ACUM · 🟡 = sub pragul de siguranță")
    print(f"Total: {urgent} SKU urgente · {len([r for r in out if r[6]>0])} de comandat · {tot_reorder:,} buc · cost PO ≈ {tot_po:,.0f} (moneda brand)")
    print("Cover ∞ = nu s-a vândut în fereastră (viteză 0). Lead/safety ajustabile cu --lead-days/--safety-days.")

if __name__=="__main__": main()
