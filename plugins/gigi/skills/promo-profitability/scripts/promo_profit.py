# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9"]
# ///
"""promo_profit.py — promo-ul COD chiar face bani? Segmentează comenzile LIVRATE după
unități/comandă (1 / 2 / 3 = „2+1" / 4+) și arată contribuția NETĂ medie per comandă pe fiecare
nivel, ex-TVA, cu COGS pe TOATE unitățile fizice (inclusiv cea gratis) + transport real.

Sursă implicită (24-oct-2026): ORDER HUB — comenzile livrate + totalul + transportul real fără TVA din
`/api/cs/livrabilitate`, bucățile pe comandă din `/api/cs/comenzi-deschise` (≤120 zile). OH nu are prețul pe
linie, deci gross = Σ(qty × prețul de listă ACTUAL al variantei din Shopify) — unitatea gratis intră tot la
preț de listă ⇒ COGS=cogs_pct×gross taxează fiecare unitate. disc = (gross − total)/gross.
Întoarcere explicită: --source awb (sau env PROMO_PROFIT_SURSA=awb) = AWBprint, doar de pe server; acolo
gross = Σ(price×qty) din line_items, transport = orders.transport_cost (gol din 28-aug).

  contribuție/comandă = total_price/vat − cogs_pct×gross/vat − transport/vat   (ex-TVA)

  uv run promo_profit.py --store georgetalent.ro
  uv run promo_profit.py --store esteban.ro --cogs-pct 0.28 --days 120
"""
import os, sys, argparse, subprocess, csv, io, json, time, urllib.request
from datetime import date, timedelta
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
CORE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", "core", "scripts")

# domeniul public (cum îl dai la --store) → domeniul myshopify (cum îl știe Order Hub și Shopify Admin)
PUBLIC_TO_SHOP = {
    "esteban.ro": "6f9e22-9d.myshopify.com", "georgetalent.ro": "ix5bxc-hr.myshopify.com",
    "ofertelezilei.ro": "ofertelezilei.myshopify.com", "casaofertelor.ro": "bonhaus.myshopify.com",
    "belasil.ro": "dvk4hu-dq.myshopify.com", "nocturnalux.ro": "de51c5-b8.myshopify.com",
    "nocturna.ro": "1eee37-2d.myshopify.com", "gento.ro": "cn54vk-uz.myshopify.com",
    "rossinails.ro": "1d2bce-2.myshopify.com", "apreciat.ro": "8e3700-d9.myshopify.com",
    "reduceribune.ro": "audusp-rf.myshopify.com", "carpetto.ro": "nxfer1-n4.myshopify.com",
    "cepatai.ro": "ce-pat-ai.myshopify.com", "grandia.ro": "n12w89-yy.myshopify.com",
    "magdeal.ro": "covoareauto-ro.myshopify.com", "covoria.ro": "bb4nmc-pb.myshopify.com",
    "nubra.ro": "bmuwvv-jy.myshopify.com", "bonhaus.cz": "vthuzq-7j.myshopify.com",
    "bonhaus.pl": "f0yrmh-ia.myshopify.com", "bonhaus.bg": "ux1x6n-n2.myshopify.com",
}

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
    import psycopg2
    url=os.getenv("DATABASE_URL_AWBPRINT") or kb("DATABASE_URL_AWBPRINT")
    if not url: sys.exit("Lipsește DATABASE_URL_AWBPRINT — --source awb e doar pentru servere; implicit e Order Hub.")
    try:
        c=psycopg2.connect(clean(url))
    except Exception as e:
        sys.exit(f"AWBprint nu e accesibil de aici ({type(e).__name__}) — e doar pentru servere; implicit e Order Hub (--source oh).")
    c.set_session(readonly=True); return c

def shop_for(store):
    m=[v for k,v in PUBLIC_TO_SHOP.items() if store.lower() in k or store.lower()==v]
    if len(m)!=1: sys.exit(f"--store {store!r}: {'nicio potrivire' if not m else 'ambiguu'} în PUBLIC_TO_SHOP")
    return m[0]

def shopify_prices(shop):
    """sku(lower) → prețul de listă ACTUAL al variantei (Shopify Admin GraphQL, tokenul din stores.csv)."""
    txt=os.getenv("SHOPIFY_STORES_CSV") or kb("SHOPIFY_STORES_CSV")
    if txt and "\n" not in txt and os.path.exists(txt): txt=open(txt,encoding="utf-8-sig").read()
    tok=next((r.get("token","").strip() for r in csv.DictReader(io.StringIO(txt or ""))
              if (r.get("shop") or "").strip().replace("https://","").strip("/")==shop), "")
    if not tok: sys.exit(f"Fără token Shopify pentru {shop} în stores.csv (prețul de listă e necesar pt COGS).")
    q="query($c:String){products(first:250,after:$c){pageInfo{hasNextPage endCursor} edges{node{variants(first:100){edges{node{sku price}}}}}}}"
    out,cur={},None
    while True:
        req=urllib.request.Request(f"https://{shop}/admin/api/2024-10/graphql.json",
            data=json.dumps({"query":q,"variables":{"c":cur}}).encode(),
            headers={"X-Shopify-Access-Token":tok,"Content-Type":"application/json"})
        for i in range(5):
            try:
                with urllib.request.urlopen(req,timeout=60) as r: j=json.loads(r.read()); break
            except Exception as e:
                if i==4: sys.exit(f"Shopify {shop} nu răspunde ({type(e).__name__})")
                time.sleep(2+i)
        d=(j.get("data") or {}).get("products") or {}
        for ed in d.get("edges") or []:
            for ve in ((ed.get("node") or {}).get("variants") or {}).get("edges") or []:
                n=ve.get("node") or {}; sku=str(n.get("sku") or "").strip().lower()
                if sku and n.get("price") is not None: out.setdefault(sku,float(n["price"]))
        if not (d.get("pageInfo") or {}).get("hasNextPage"): return out
        cur=d["pageInfo"]["endCursor"]

def rows_oh(store, days, vat, azi=None):
    """(oid, total, transport BRUT, gross listă, bucăți, disc) pe comenzile LIVRATE din Order Hub."""
    sys.path.insert(0, CORE); import oh_citire as oh
    if days>120: sys.exit("Order Hub dă bucățile pe comandă pe cel mult 120 de zile (--days ≤ 120).")
    shop=shop_for(store); azi=azi or date.today(); start=(azi-timedelta(days=days)).isoformat()
    try:
        livrate={}
        y,m=int(start[:4]),int(start[5:7])
        while (y,m)<=(azi.year,azi.month):
            for r in oh.livrabilitate("%04d-%02d"%(y,m), shop):
                if r["stare"]=="Livrata" and r["zi"]>=start and float(r["total"] or 0)>0:
                    livrate[r["comanda"]]=(float(r["total"]), float(r["transport_fara_tva"] or 0)*vat)
            y,m=(y+1,1) if m==12 else (y,m+1)
        linii=oh.comenzi_deschise(days+2)
    except oh.OhEroare as e:
        sys.exit(f"EROARE: {e}")
    pret=shopify_prices(shop)
    acc,fara_pret={},0
    for l in linii:
        if l["comanda"] not in livrate: continue
        u,g=acc.get(l["comanda"],(0.0,0.0)); p=pret.get(l["sku"])
        if p is None: fara_pret+=1; p=0.0
        acc[l["comanda"]]=(u+l["buc"], g+l["buc"]*p)
    rows=[(o, livrate[o][0], livrate[o][1], g, u, max(0.0,g-livrate[o][0])) for o,(u,g) in acc.items()]
    print(f"(Order Hub: {len(livrate)} comenzi livrate, {len(acc)} cu linii; {fara_pret} linii fără preț Shopify)",file=sys.stderr)
    return rows

def tier(u):
    u=round(u)
    return "1 buc" if u<=1 else ("2 buc" if u==2 else ("3 buc (2+1)" if u==3 else "4+ buc"))

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--store", required=True, help="magazin (ILIKE), ex: georgetalent.ro / esteban.ro")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--cogs-pct", type=float, default=0.32, help="COGS ca %% din valoarea de listă (default 32%%)")
    ap.add_argument("--vat", type=float, default=1.21, help="divizor TVA (RO=1.21; CZ/PL diferă)")
    ap.add_argument("--source", choices=["oh","awb"], default=(os.getenv("PROMO_PROFIT_SURSA") or "oh").lower(),
                    help="oh = Order Hub (implicit); awb = AWBprint, doar pe server (sau env PROMO_PROFIT_SURSA=awb)")
    a=ap.parse_args()
    rows = rows_oh(a.store, a.days, a.vat) if a.source=="oh" else rows_awb(a)
    report(a, rows)

def rows_awb(a):
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
    return rows

def report(a, rows):
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
    print(f"\n=== Promo profitability — {a.store} · livrate ultimele {a.days}z · COGS {cp:.0%} listă · TVA /{vat} · sursă {'Order Hub' if a.source=='oh' else 'AWBprint'} ===")
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
