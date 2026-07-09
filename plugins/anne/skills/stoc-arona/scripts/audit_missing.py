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
from collections import defaultdict
API="2026-01"; D=os.path.dirname(os.path.abspath(__file__))
def gql(shop,token,q,v=None):
    for a in range(6):
        r=requests.post(f"https://{shop}/admin/api/{API}/graphql.json",headers={"X-Shopify-Access-Token":token,"Content-Type":"application/json"},json={"query":q,"variables":v or {}},timeout=60); d=r.json()
        if "data" not in d: return d
        if d.get("errors") and any("throttl" in str(e).lower() for e in d["errors"]): time.sleep(2+a); continue
        return d
    return d
Q=('query($c:String){ productVariants(first:100, after:$c){ pageInfo{hasNextPage endCursor} '
   'edges{ node{ sku inventoryQuantity product{ status title } } } } }')
def pull(pfx):
    shop,token=resolve_store(pfx); m={}; cur=None
    while True:
        d=gql(shop,token,Q,{"c":cur})
        if "data" not in d: break
        pv=d["data"]["productVariants"]
        for e in pv["edges"]:
            v=e["node"]
            if v["product"]["status"]!="ACTIVE": continue
            q=v["inventoryQuantity"] or 0
            if q<=0: continue
            s=(v["sku"] or "").strip()
            if s and not s.upper().startswith("HA"): m.setdefault(s,(q,v["product"]["title"]))
        if pv["pageInfo"]["hasNextPage"]: cur=pv["pageInfo"]["endCursor"]
        else: break
    return m
# union non-HA across 4 deals stores
union={}
for pfx in ["BON","MAG","OFER","RED"]:
    for s,(q,t) in pull(pfx).items(): union.setdefault(s,(q,t))
# what's already in stock (any store_*.json)
instock=set()
for fn in os.listdir(D):
    if fn.startswith("store_") and fn.endswith(".json"):
        for row in json.load(open(os.path.join(D,fn),encoding="utf-8")): instock.add(str(row[2]).strip())
# gento genti skus (excluded from Facebook by design)
gento={r[2] for r in json.load(open(os.path.join(D,"store_Gento.json"),encoding="utf-8"))}
covoria={r[2] for r in json.load(open(os.path.join(D,"store_Covoria.json"),encoding="utf-8"))}
def is_genti(s): return s.startswith("set-") and ("genti" in s or "piele" in s or "sarpe" in s or "eco" in s or "dungi" in s or "croco" in s or "textil" in s or "chevron" in s or "granulat" in s or "impletit" in s or "lucios" in s or "texturat" in s)
missed=[]
for s,(q,t) in union.items():
    if s in instock: continue
    if s=="surpriza": continue
    if s in gento or s in covoria: continue
    if is_genti(s): continue  # deja la Gento
    missed.append((s,q,t[:45]))
missed.sort(key=lambda x:-x[1])
print(f"Non-HA active pe deals (union): {len(union)} | deja în stoc: {len([s for s in union if s in instock])}")
print(f"\n=== NON-HA cu stoc care NU-s nicăieri în 1 iulie (posibil OMISE): {len(missed)} ===")
for s,q,t in missed: print(f"   {s:34} {q:>6}  {t}")
