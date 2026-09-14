# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary"]
# ///
import subprocess, psycopg2, collections
KB="/Users/gheorghebeschea/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"
def dsn(k): return subprocess.run(["uv","run",KB,"secret-get",k],capture_output=True,text=True).stdout.strip().split("?")[0]
def q(k,s,p=None):
    cn=psycopg2.connect(dsn(k)); cn.set_session(readonly=True); c=cn.cursor(); c.execute(s,p); r=c.fetchall(); cn.close(); return r

pools=collections.defaultdict(set)
for num,tn,sku in q("DATABASE_URL_GRANDIA",'''SELECT po.id::text, po."tomNumber", i.sku
     FROM po_purchase_orders po JOIN po_purchase_order_items i ON i."purchaseOrderId"=po.id WHERE i.sku IS NOT NULL'''):
    pools[("GRANDIA",num,tn)].add(sku.upper())
for num,tn,sku in q("DATABASE_URL_AWBPRINT","""SELECT po.po_number, po.tom_number, i.sku
     FROM purchase_orders po JOIN purchase_order_items i ON i.purchase_order_id=po.id WHERE i.sku IS NOT NULL"""):
    pools[("VIGO",num,tn)].add(sku.upper())
for num,tn,sku in q("DATABASE_URL_ARONA_BI","""SELECT po.id::text, po.tom_number, i.sku
     FROM purchase_orders po JOIN purchase_order_items i ON i.purchase_order_id=po.id WHERE i.sku IS NOT NULL"""):
    pools[("ARONA-BI",num,tn)].add(sku.upper())

for target in ["TOM-009","TOM-REC-na52cs","TOM-051","TOM-054","TOM-066","TOM-067","TOM-052"]:
    t={r[0].upper() for r in q("DATABASE_URL_TOM",
       'SELECT i."externalSku" FROM purchase_orders po JOIN purchase_order_items i ON i."poId"=po.id WHERE po."tomNumber"=%s AND i."externalSku" IS NOT NULL',(target,))}
    if not t: print(f"\n{target}: fara SKU-uri"); continue
    sc=sorted(((len(t&s)/len(t|s), len(t&s), src, num, tn, len(s)) for (src,num,tn),s in pools.items() if s), reverse=True)
    print(f"\n{target}  ({len(t)} SKU)")
    for j,i2,src,num,tn,n in sc[:2]:
        print(f"   {src:<9} {str(num)[:14]:<15} tom={str(tn):<9} comune={i2}/{n:<4} potrivire={j:.0%}")
