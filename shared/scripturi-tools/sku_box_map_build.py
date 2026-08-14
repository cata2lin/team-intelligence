#!/usr/bin/env python3
"""Construieste map-ul central SKU->nr_cutii (nr colete/unitate) din metafield-urile setate pe TOATE magazinele deals
(consensus per SKU). order_parcel_count il foloseste ca fallback => setezi metafield-ul O DATA pe un magazin, merge peste tot.
`--fill` completeaza si metafield-ul `nr_produse` (integer) pe magazinele unde lipseste dar SKU-ul are consensus (vizibilitate).
Cron zilnic ruleaza FARA --fill (doar map). Ca `sku_station`/DEPOZIT_SKU_RULES dar pentru nr colete."""
import os, sys, json, collections
sys.path.insert(0,"/root/Scripturi/team-intelligence/plugins/gigi/skills/xconnector")
os.chdir("/root/Scripturi/team-intelligence/plugins/gigi/skills/xconnector")
import xconnector as X
FILL="--fill" in sys.argv
MAP_PATH="/root/Scripturi/data/sku_box_map.json"
sts={t.get("shopDomain"):t for t in X.load_shopify_tokens()}
STORES=["ofertelezilei.myshopify.com","audusp-rf.myshopify.com","bonhaus.myshopify.com","covoareauto-ro.myshopify.com",
        "oriceredus.myshopify.com","ux1x6n-n2.myshopify.com","vthuzq-7j.myshopify.com","63e901-2f.myshopify.com","16w7xv-0w.myshopify.com"]
Q='{ products(first:200%s){ pageInfo{ hasNextPage endCursor } edges{ node{ id variants(first:1){ edges{ node{ sku } } } c: metafield(namespace:"custom", key:"nr_cutii"){ value } pr: metafield(namespace:"custom", key:"nr_produse"){ value } } } } }'
rows=[]
for dom in STORES:
    st=sts.get(dom)
    if not st: continue
    cur=None
    for _ in range(25):
        after=(', after:"%s"'%cur) if cur else ""
        d=X.shopify_gql(st["shopDomain"], st["adminToken"], Q % after)
        conn=((d.get("data") or {}).get("products")) or {}
        for e in (conn.get("edges") or []):
            n=e["node"]; vs=((n.get("variants") or {}).get("edges") or []); sku=vs[0]["node"].get("sku") if vs else None
            if not sku: continue
            c=(n.get("c") or {}).get("value"); pr=(n.get("pr") or {}).get("value")
            rows.append((dom,n["id"],sku,c,pr))
        pi=conn.get("pageInfo") or {}
        if not pi.get("hasNextPage"): break
        cur=pi.get("endCursor")
def fnum(v):
    try: return float(v)
    except Exception: return None
bysku=collections.defaultdict(list)
for r in rows: bysku[r[2]].append(r)
mp={}
for sku,rs in bysku.items():
    eff=[]  # box efectiv per produs: nr_cutii pref, altfel nr_produse
    for r in rs:
        b=fnum(r[3]); 
        if b is None: b=fnum(r[4])
        if b is not None: eff.append(b)
    if eff:
        cons=collections.Counter(eff).most_common(1)[0][0]
        if cons > 0:            # box 0/negativ = zgomot, nu-l propaga
            mp[sku]=cons
# overlay AUTORITATIV: input depozit din parcel_density.db (pagina /colete) peste consensul Shopify
try:
    import sqlite3
    _dbc = sqlite3.connect("/root/Scripturi/data/parcel_density.db")
    _n = 0
    for _sku, _nr in _dbc.execute("select sku, nr_cutii from parcel_density where nr_cutii is not null and nr_cutii > 0"):
        mp[_sku] = _nr; _n += 1
    _dbc.close()
    print("overlay parcel_density (depozit): %d SKU-uri" % _n)
except Exception as _e:
    print("overlay parcel_density skip:", _e)
os.makedirs("/root/Scripturi/data", exist_ok=True)
json.dump(mp, open(MAP_PATH,"w"))
print("map SKU->box: %d SKU-uri (din %d produse, %d magazine)" % (len(mp), len(rows), len(STORES)))
# --fill: completeaza nr_produse (integer) unde lipseste dar exista consensus INTREG
if FILL:
    MSET='mutation($mf:[MetafieldsSetInput!]!){ metafieldsSet(metafields:$mf){ userErrors{ message } } }'
    perstore=collections.defaultdict(list)
    for dom,gid,sku,c,pr in rows:
        if (c not in (None,"")) or (pr not in (None,"")): continue   # are deja ceva
        box=mp.get(sku)
        if box is None: continue
        if abs(box-round(box))<1e-9:   # doar valori intregi -> nr_produse
            perstore[dom].append((gid,str(int(round(box)))))
    for dom,items in perstore.items():
        st=sts.get(dom)
        for i in range(0,len(items),25):
            chunk=items[i:i+25]
            mf=[{"ownerId":g,"namespace":"custom","key":"nr_produse","type":"number_integer","value":v} for g,v in chunk]
            r=X.shopify_gql(st["shopDomain"], st["adminToken"], MSET, {"mf":mf})
            ue=((r.get("data") or {}).get("metafieldsSet") or {}).get("userErrors") or r.get("errors")
            if ue: print("  FILL ERR %s: %s"%(dom.split(".")[0], json.dumps(ue,ensure_ascii=False)[:120]))
        print("  fill %s: %d produse" % (dom.split(".")[0], len(items)))
