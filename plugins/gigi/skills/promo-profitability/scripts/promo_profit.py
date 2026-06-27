# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9"]
# ///
"""promo_profit.py — promo-ul COD chiar face bani? Segmentează comenzile LIVRATE după
unități/comandă (1 / 2 / 3 = „2+1" / 4+) și arată contribuția NETĂ medie per comandă pe fiecare
nivel, ex-TVA, cu COGS pe TOATE unitățile fizice (inclusiv cea gratis) + transport real.

Sursă: AWBprint (delivered = venit COD real). În line_items unitatea gratis păstrează prețul de
listă cu discount 100% → gross=Σ(price×qty) include unitatea gratis ⇒ COGS=cogs_pct×gross taxează
fiecare unitate. Venit net = total_price (autoritativ). Transport = orders.transport_cost.

  contribuție/comandă = total_price/vat − cogs_pct×gross/vat − transport/vat   (ex-TVA)

  uv run promo_profit.py --store georgetalent.ro
  uv run promo_profit.py --store esteban.ro --cogs-pct 0.28 --days 120
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
    c=psycopg2.connect(clean(os.getenv("DATABASE_URL_AWBPRINT") or kb("DATABASE_URL_AWBPRINT"))); c.set_session(readonly=True); return c

def tier(u):
    u=round(u)
    return "1 buc" if u<=1 else ("2 buc" if u==2 else ("3 buc (2+1)" if u==3 else "4+ buc"))

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--store", required=True, help="magazin (ILIKE), ex: georgetalent.ro / esteban.ro")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--cogs-pct", type=float, default=0.32, help="COGS ca % din valoarea de listă (default 32%)")
    ap.add_argument("--vat", type=float, default=1.21, help="divizor TVA (RO=1.21; CZ/PL diferă)")
    a=ap.parse_args()
    cx=conn()
    q="""
    WITH ord AS (
      SELECT o.id oid, o.total_price tp, COALESCE(o.transport_cost,0) tr, o.line_items li
      FROM orders o JOIN stores s ON s.uid=o.store_uid
      WHERE s.name ILIKE %s AND o.aggregated_status='delivered'
        AND o.frisbo_created_at >= CURRENT_DATE - %s
        AND o.line_items IS NOT NULL AND json_typeof(o.line_items)='array' AND o.total_price>0),
    lines AS (
      SELECT oid, tp, tr,
        (e->>'price')::numeric price, (e->>'quantity')::numeric qty,
        COALESCE((SELECT SUM((d->>'amount')::numeric) FROM json_array_elements(e->'discount_allocations') d),0) disc
      FROM ord, json_array_elements(li) e)
    SELECT oid, MAX(tp) tp, MAX(tr) tr, SUM(price*qty) gross, SUM(qty) units, SUM(disc) disc
    FROM lines GROUP BY oid"""
    with cx.cursor() as c:
        c.execute(q,[f"%{a.store}%", a.days]); rows=c.fetchall()
    if not rows: print(f"Nicio comandă livrată pt '{a.store}' în {a.days}z."); return
    agg={}; vat=a.vat; cp=a.cogs_pct
    for oid,tp,tr,gross,units,disc in rows:
        tp=float(tp or 0); tr=float(tr or 0); gross=float(gross or 0); units=float(units or 0); disc=float(disc or 0)
        if units<=0 or gross<=0: continue
        t=tier(units)
        cogs=cp*gross/vat
        contrib = tp/vat - cogs - tr/vat
        d=agg.setdefault(t,{"n":0,"net":0.0,"gross":0.0,"disc":0.0,"tr":0.0,"cogs":0.0,"contrib":0.0,"units":0.0})
        d["n"]+=1; d["net"]+=tp; d["gross"]+=gross; d["disc"]+=disc; d["tr"]+=tr
        d["cogs"]+=cogs; d["contrib"]+=contrib; d["units"]+=units
    order=["1 buc","2 buc","3 buc (2+1)","4+ buc"]
    tot_n=sum(d["n"] for d in agg.values()) or 1
    print(f"\n=== Promo profitability — {a.store} · livrate ultimele {a.days}z · COGS {cp:.0%} listă · TVA /{vat} ===")
    print(f"{'Nivel':14} {'#cmd':>7} {'%':>4} {'net/cmd':>8} {'disc%':>6} {'COGS/cmd':>9} {'transp':>7} {'CONTRIB/cmd':>12} {'marjă':>6}")
    tot=dict(n=0,net=0.0,contrib=0.0)
    for t in order:
        if t not in agg: continue
        d=agg[t]; n=d["n"]
        netc=d["net"]/n; discp=d["disc"]/d["gross"] if d["gross"] else 0
        cogsc=d["cogs"]/n; trc=d["tr"]/n; conc=d["contrib"]/n
        marg=conc/(netc/vat) if netc else 0
        flag="🔴" if conc<0 else ("🟡" if marg<0.10 else "🟢")
        print(f"{flag}{t:12} {n:>7,} {100*n/tot_n:>3.0f}% {netc:>8,.0f} {discp:>5.0%} {cogsc:>9,.0f} {trc:>7,.0f} {conc:>12,.0f} {marg:>5.0%}")
        tot["n"]+=n; tot["net"]+=d["net"]; tot["contrib"]+=d["contrib"]
    print("-"*86)
    tcon=tot["contrib"]/tot["n"] if tot["n"] else 0
    print(f"{'TOTAL':14} {tot['n']:>7,} {'':>4} {tot['net']/tot['n']:>8,.0f} {'':>6} {'':>9} {'':>7} {tcon:>12,.0f} {tcon/(tot['net']/tot['n']/vat) if tot['n'] else 0:>5.0%}")
    print("\n🟢 marjă ≥10% · 🟡 sub 10% · 🔴 PIERDERE. Contribuție = venit net ex-TVA − COGS(toate unitățile) − transport ex-TVA.")
    print(f"COGS estimat ({cp:.0%} din listă) — pt COGS real per-SKU folosește profit_by_sku.py. Marja se citește pe nivel: dacă „3 buc (2+1)\" e 🔴/🟡, promo-ul mănâncă profitul.")

if __name__=="__main__": main()
