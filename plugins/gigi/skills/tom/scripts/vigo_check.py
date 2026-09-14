# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary"]
# ///
import subprocess, psycopg2, collections
KB="/Users/gheorghebeschea/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"
def dsn(k): return subprocess.run(["uv","run",KB,"secret-get",k],capture_output=True,text=True).stdout.strip().split("?")[0]
def q(k,s):
    cn=psycopg2.connect(dsn(k)); cn.set_session(readonly=True); c=cn.cursor(); c.execute(s); r=c.fetchall(); cn.close(); return r

awb=collections.defaultdict(set)
for num,sku,tn in q("DATABASE_URL_AWBPRINT","""SELECT po.po_number, i.sku, po.tom_number
        FROM purchase_orders po JOIN purchase_order_items i ON i.purchase_order_id=po.id WHERE i.sku IS NOT NULL"""):
    awb[(num,tn)].add(sku.upper())

for target in ["TOM-REC-w3qxny","TOM-REC-na52cs","TOM-054","TOM-066","TOM-051"]:
    tom_skus={r[0].upper() for r in q("DATABASE_URL_TOM",
        f"""SELECT i."externalSku" FROM purchase_orders po JOIN purchase_order_items i ON i."poId"=po.id
            WHERE po."tomNumber"='{target}' AND i."externalSku" IS NOT NULL""")}
    sc=[]
    for (num,tn),s in awb.items():
        if not s: continue
        j=len(tom_skus & s)/len(tom_skus | s)
        sc.append((j, len(tom_skus & s), num, tn, len(s)))
    sc.sort(reverse=True)
    print(f"\n{target}  ({len(tom_skus)} SKU-uri in TOM)")
    for j,inter,num,tn,n in sc[:3]:
        print(f"   {num:<12} tom_number={str(tn):<9} comune={inter:<3} din {n:<3} SKU  potrivire={j:.0%}")
