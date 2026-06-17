# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
"""Product Matrix / PMax Labelizer — label every product Scale / Hold / Trim / Cut / Test
by **margin-aware** ad performance (POAS = ROAS × margin), spend, conversions and stock.

Live product performance comes from the Google Ads API (shopping_performance_view, any MCC
account); margin (price vs costPerItem) + stock come from the metrics DB `variants`/`products`
(joined on the variant id inside `productItemId`). Read-only.

    uv run product_matrix.py --customer 5229815058 --brand esteban
    uv run product_matrix.py --customer 5229815058 --brand esteban --days 14 --min-spend 60
    uv run product_matrix.py --customer 5229815058 --brand esteban --format csv > matrix.csv

Output: per-product label + metrics, sorted by spend, plus a summary (how much spend sits in
each bucket — i.e. how much you're wasting on CUT and how much headroom SCALE has).
Why POAS not ROAS: an 80%-margin dupe at ROAS 2 prints money; a 15%-margin item at ROAS 4 may
lose money. Labels act on profit, not revenue. Next step (v2): write the label to
`custom_label_0` (Merchant feed / Shopify metafield) so PMax can split asset groups by label.
"""
import os, sys, argparse, collections, json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2, psycopg2.extras
# shared Google Ads MCC client (Ads creds+OAuth+search) — google-ads-mcc/gads.py.
# cx below stays for the variants/margin query (metrics DB), which gads doesn't cover.
_here = Path(__file__).resolve()
for _up in range(1, 6):
    _cand = _here.parents[_up] / "google-ads-mcc"
    if (_cand / "gads.py").exists():
        sys.path.insert(0, str(_cand)); break
import gads
_PG_OK={"host","port","dbname","user","password","sslmode","sslrootcert","sslcert","sslkey","connect_timeout","application_name","options","channel_binding"}
def clean(d):
    p=urlsplit(d)
    return d if not p.query else urlunsplit((p.scheme,p.netloc,p.path,urlencode([(x,y) for x,y in parse_qsl(p.query,keep_blank_values=True) if x.lower() in _PG_OK]),p.fragment))

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--customer",required=True,help="Google Ads CID")
    ap.add_argument("--brand",help="brand slug for margin/stock join (e.g. esteban). Omit = ROAS-only.")
    ap.add_argument("--days",type=int,default=30)
    ap.add_argument("--target-roas",type=float,help="override; default = breakeven from margin (POAS=1)")
    ap.add_argument("--bundle",help="free offer like '2+1' (pay 2, get 3) -> effective margin = 1-((pay+free)/pay)*(cogs/price). Esteban=2+1.")
    ap.add_argument("--min-spend",type=float,default=40.0,help="RON spend floor for a confident verdict")
    ap.add_argument("--min-conv",type=float,default=3.0,help="conversions floor; TEST only when BOTH spend<min-spend AND conv<min-conv")
    ap.add_argument("--scale",type=float,default=1.5,help="profit ratio (POAS or roas/target) at/above which = SCALE")
    ap.add_argument("--cut",type=float,default=0.8,help="profit ratio below which (with spend) = CUT")
    ap.add_argument("--low-stock",type=int,default=5)
    ap.add_argument("--format",choices=["table","csv","json"],default="table")
    ap.add_argument("--top",type=int,default=0,help="show only top N by spend (0=all)")
    a=ap.parse_args()
    bundle_ratio=1.0
    if a.bundle:
        try:
            pay,free=(int(x) for x in a.bundle.lower().split("+")); bundle_ratio=(pay+free)/pay
        except Exception: sys.exit("--bundle format: '2+1'")
    cx=psycopg2.connect(clean(os.environ["DATABASE_URL_METRICS"])); cx.set_session(readonly=True)
    c=cx.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    conn=gads.get_connection()

    # 1) live product performance
    q=(f"SELECT segments.product_item_id, segments.product_title, metrics.cost_micros, metrics.conversions, "
       f"metrics.conversions_value, metrics.clicks, metrics.impressions FROM shopping_performance_view "
       f"WHERE segments.date DURING LAST_{a.days}_DAYS")
    agg={}
    for row in gads.search(conn, a.customer, q):
        s=row["segments"]; m=row["metrics"]; pid=s.get("productItemId","")
        d=agg.setdefault(pid,{"title":s.get("productTitle",""),"spend":0.0,"conv":0.0,"val":0.0,"clicks":0,"impr":0})
        d["spend"]+=float(m.get("costMicros",0))/1e6; d["conv"]+=float(m.get("conversions",0))
        d["val"]+=float(m.get("conversionsValue",0)); d["clicks"]+=int(m.get("clicks",0)); d["impr"]+=int(m.get("impressions",0))

    # 2) margin/stock from metrics, by brand
    vmap={}
    if a.brand:
        c.execute("""SELECT v."shopifyNumericId"::text vid, v.price, v."costPerItem" cogs, v."inventoryQuantity" stock, p."productType" ptype
                     FROM variants v JOIN products p ON p.id=v."productId"
                     WHERE v."brandId"=(SELECT id FROM brands WHERE slug=%s) AND v."deletedAt" IS NULL""",(a.brand,))
        for v in c.fetchall(): vmap[v["vid"]]=v

    # 3) join + label
    rows=[]
    for pid,d in agg.items():
        vid=pid.split("_")[-1]; v=vmap.get(vid,{})
        price=float(v["price"]) if v.get("price") is not None else None
        cogs=float(v["cogs"]) if v.get("cogs") is not None else None
        # effective margin accounts for the free units in a bundle (e.g. 2+1: ship 3, paid 2)
        margin=(1.0 - bundle_ratio*(cogs/price)) if (price and cogs is not None and price>0) else None
        spend,conv,val=d["spend"],d["conv"],d["val"]
        roas=val/spend if spend>0 else 0.0
        poas=roas*margin if margin is not None else None
        target=a.target_roas if a.target_roas else (1.0/margin if margin else 3.0)
        ratio=poas if poas is not None else (roas/target if target else 0)   # >=1 = breakeven on profit
        stock=v.get("stock")
        if spend < a.min_spend and conv < a.min_conv:   # genuinely thin data, can't judge
            label="ZOMBIE" if d["impr"]==0 else "TEST"
        elif conv==0:                                    # spent enough, never converted
            label="CUT"
        elif ratio>=a.scale: label="SCALE"
        elif ratio>=1.0:     label="HOLD"
        elif ratio>=a.cut:   label="TRIM"
        else:                label="CUT"
        flag = " ⚠STOCK" if (stock is not None and stock<=a.low_stock and label in ("SCALE","HOLD")) else ""
        rows.append({"label":label+flag,"lab":label,"product":d["title"][:42],"spend":round(spend),"roas":round(roas,2),
                     "margin":round(margin*100) if margin is not None else None,"poas":round(poas,2) if poas is not None else None,
                     "conv":round(conv,1),"stock":stock,"ptype":v.get("ptype")})
    rows.sort(key=lambda x:-x["spend"])
    if a.top: rows=rows[:a.top]

    if a.format=="json": print(json.dumps(rows,ensure_ascii=False,indent=1)); return
    if a.format=="csv":
        import csv; w=csv.writer(sys.stdout); w.writerow(["label","product","spend_ron","roas","margin_pct","poas","conv","stock","type"])
        for x in rows: w.writerow([x["lab"],x["product"],x["spend"],x["roas"],x["margin"],x["poas"],x["conv"],x["stock"],x["ptype"]]);
        return
    # table
    print(f"\n=== Product Matrix · {a.customer} · ultimele {a.days} zile · {len(rows)} produse cu impresii ===")
    print(f"{'LABEL':<12}{'spend':>6} {'roas':>5} {'mrg%':>5} {'poas':>5} {'conv':>5} {'stoc':>6}  produs")
    for x in rows:
        print(f"{x['label']:<12}{x['spend']:>6} {x['roas']:>5} {str(x['margin'] or '-'):>5} {str(x['poas'] or '-'):>5} {x['conv']:>5} {str(x['stock'] if x['stock'] is not None else '-'):>6}  {x['product']}")
    # summary
    by=collections.defaultdict(lambda:[0,0.0,0.0])  # label -> [count, spend, value→via conv? keep spend]
    tot_spend=0
    for x in rows:
        by[x["lab"]][0]+=1; by[x["lab"]][1]+=x["spend"]; tot_spend+=x["spend"]
    print(f"\n--- SUMAR ({tot_spend:.0f} RON spend total) ---")
    order=["SCALE","HOLD","TRIM","CUT","TEST","ZOMBIE"]
    act={"SCALE":"urcă buget/prioritate, grup propriu","HOLD":"menține","TRIM":"scade bid/buget, marginal",
         "CUT":"exclude din listing group / negativ — risipă","TEST":"prea puține date, lasă să învețe","ZOMBIE":"în feed dar nu servește — fix feed/preț"}
    for k in order:
        if k in by: print(f"  {k:<7} {by[k][0]:3d} produse · {by[k][1]:6.0f} RON · {act[k]}")

if __name__=="__main__":
    main()
