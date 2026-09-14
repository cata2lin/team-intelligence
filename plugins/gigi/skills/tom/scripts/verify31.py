# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary"]
# ///
"""Verifica cele ramase autoreferentiale, cu DOUA metode independente. READ-ONLY."""
import subprocess, psycopg2, collections, json
KB="/Users/gheorghebeschea/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"
def dsn(k): return subprocess.run(["uv","run",KB,"secret-get",k],capture_output=True,text=True).stdout.strip().split("?")[0]
def q(k,s,p=None):
    cn=psycopg2.connect(dsn(k)); cn.set_session(readonly=True); c=cn.cursor(); c.execute(s,p); r=c.fetchall(); cn.close(); return r

LINE={}; POOL=collections.defaultdict(set)
def add(src, rows, uniq_lineid):
    for po,lid,sku in rows:
        if uniq_lineid and lid: LINE.setdefault(str(lid), (src,str(po)))
        if sku: POOL[(src,str(po))].add(sku.upper())
add("SCENTUM", q("DATABASE_URL_SCENTUM","""SELECT po.number, i.id::text, i.product_code
      FROM purchase_orders po JOIN purchase_order_items i ON i.purchase_order_id=po.id"""), True)
add("GRANDIA", q("DATABASE_URL_GRANDIA",'''SELECT po.id::text, i.id::text, i.sku
      FROM po_purchase_orders po JOIN po_purchase_order_items i ON i."purchaseOrderId"=po.id'''), True)
add("ARONA-BI", q("DATABASE_URL_ARONA_BI","""SELECT po.id::text, i.id::text, i.sku
      FROM purchase_orders po JOIN purchase_order_items i ON i.purchase_order_id=po.id"""), True)
add("VIGO", q("DATABASE_URL_AWBPRINT","""SELECT po.po_number, NULL, i.sku
      FROM purchase_orders po JOIN purchase_order_items i ON i.purchase_order_id=po.id"""), False)

bad=q("DATABASE_URL_TOM","""SELECT po."tomNumber", po.id, po."sourcePoId" FROM purchase_orders po
      JOIN source_apps sa ON sa.id=po."sourceAppId"
      WHERE right(po.id,8)=right(po."sourcePoId",8) ORDER BY po."tomNumber" """)
print(f"{'PO':<10}{'linii(cuid)':<22}{'SKU-set':<24}{'verdict'}")
print("-"*78)
plan=[]; nesigur=[]
for tn,pid,spid in bad:
    its=q("DATABASE_URL_TOM",'SELECT i."sourceLineId", i."externalSku" FROM purchase_order_items i WHERE i."poId"=%s',(pid,))
    votes=collections.Counter(LINE[str(l)] for l,_ in its if l and str(l) in LINE)
    skus={s.upper() for _,s in its if s}
    best=max((( len(skus&v)/len(skus|v), src, po) for (src,po),v in POOL.items() if v), default=(0,None,None)) if skus else (0,None,None)
    lv = votes.most_common(1)[0] if votes else None
    lstr = f"{lv[0][0]}/{lv[0][1]}({lv[1]}/{len(its)})" if lv else "-"
    sstr = f"{best[1]}/{best[2]} {best[0]:.0%}" if best[1] else "-"
    # PRIORITATE: votul unanim pe cuid bate potrivirea pe SKU (cuid = unic global;
    # mulțimile de SKU se pot suprapune între PO-uri ale aceleiași surse)
    cand = None
    if lv and lv[1] == len(its) and len(its) > 0:
        cand = lv[0]
    elif not votes and best[0] >= 0.95:
        cand = (best[1], best[2])
    if cand: plan.append((tn,pid,cand[0],cand[1])); v="OK"
    else: nesigur.append((tn,lstr,sstr)); v="NESIGUR"
    print(f"{tn:<10}{lstr:<22}{sstr:<24}{v}")
print("-"*78)
print(f"sigure={len(plan)}  nesigure={len(nesigur)}")
json.dump(plan, open("plan31.json","w"), indent=1)
print("\ndistributie corectii:", dict(collections.Counter(p[2] for p in plan)))
