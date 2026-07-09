# /// script
# requires-python=">=3.9"
# dependencies=["requests"]
# ///
import os,sys,json,time
sys.stdout.reconfigure(encoding="utf-8",errors="replace")
sys.path.insert(0,os.path.expanduser("~/team-intelligence-main/plugins/gigi/skills/shopify-stores/scripts"))
os.environ.setdefault("KB_PY",os.path.expanduser("~/team-intelligence-main/plugins/core/scripts/kb.py"))
from shopify_gql import resolve_store
import requests
API="2026-01"; D=os.path.dirname(os.path.abspath(__file__))
def gql(shop,token,q,v=None):
    for a in range(6):
        r=requests.post(f"https://{shop}/admin/api/{API}/graphql.json",
          headers={"X-Shopify-Access-Token":token,"Content-Type":"application/json"},
          json={"query":q,"variables":v or {}},timeout=60); d=r.json()
        if "data" not in d: return d
        if d.get("errors") and any("throttl" in str(e).lower() for e in d["errors"]): time.sleep(2+a); continue
        return d
    return d
QV=('query($c:String){ productVariants(first:100, after:$c){ pageInfo{hasNextPage endCursor} '
   'edges{ node{ sku inventoryQuantity product{ status productType title } } } } }')
def pull(pfx):
    shop,token=resolve_store(pfx); m={}
    cur=None
    while True:
        d=gql(shop,token,QV,{"c":cur})
        if "data" not in d: raise SystemExit(f"{pfx} err {json.dumps(d)[:150]}")
        pv=d["data"]["productVariants"]
        for e in pv["edges"]:
            v=e["node"]
            if v["product"]["status"]!="ACTIVE": continue
            if (v["inventoryQuantity"] or 0)<=0: continue
            s=(v["sku"] or "").strip()
            if s: m.setdefault(s,(v["inventoryQuantity"],v["product"]["title"]))
        if pv["pageInfo"]["hasNextPage"]: cur=pv["pageInfo"]["endCursor"]
        else: break
    return m
# SKU-uri deja in foaie (toate store_*.json)
sheet=set()
for fn in os.listdir(D):
    if fn.startswith("store_") and fn.endswith(".json"):
        for r in json.load(open(os.path.join(D,fn),encoding="utf-8")):
            if len(r)>=4 and r[2]: sheet.add(str(r[2]).strip())
print(f"SKU in foaie: {len(sheet)}",flush=True)
shops=["ROSSI","NOC","GEN","CARP","COV","BON","MAG","OFER","RED","GRAN"]
alllive={}
for p in shops:
    m=pull(p); alllive[p]=m; print(f"  {p}: {len(m)} active+stoc",flush=True)
json.dump({p:{s:list(v) for s,v in m.items()} for p,m in alllive.items()}, open("live_all.json","w",encoding="utf-8"),ensure_ascii=False)
# union of all live SKUs
union={}
for p,m in alllive.items():
    for s,v in m.items(): union.setdefault(s,(p,)+tuple(v))
missing=[(s,)+union[s] for s in union if s not in sheet]
print(f"\n=== SKU active cu stoc care NU sunt in foaie: {len(missing)}")
for s,p,q,t in sorted(missing)[:80]:
    print(f"  [{p:5}] {s:34} q={q:>5}  {str(t)[:40]}")
