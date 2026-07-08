# /// script
# requires-python = ">=3.10"
# dependencies = ["requests"]
# ///
"""Create 3 automatic BXGY discounts: buy kit -> get N dip powders FREE (100% off). Apply with --apply."""
import sys, csv, subprocess, requests
from datetime import datetime, timezone
APPLY="--apply" in sys.argv
def kb(k): return subprocess.run(["uv","run","/Users/gheorghebeschea/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py","secret-get",k],capture_output=True,text=True).stdout
shop=tok=None
for r in csv.reader(kb("SHOPIFY_STORES_CSV").splitlines()):
    if r and r[0]=="ROSSI": shop,tok=r[1],r[2]
H={"X-Shopify-Access-Token":tok,"Content-Type":"application/json"}
def gql(q,v=None): return requests.post(f"https://{shop}/admin/api/2026-01/graphql.json",headers=H,json={"query":q,"variables":v or {}},timeout=40).json()

COLL="gid://shopify/Collection/609678754135"
KITS=[("Kit 1 Culoare — 1 pudră gratis","8544217268567",1),
      ("Kit 3 Culori — 3 pudre gratis","8544228671831",3),
      ("Kit 6 Culori — 6 pudre gratis","8544233292119",6)]
STARTS=datetime.now(timezone.utc).replace(microsecond=0).isoformat()

MUT="""mutation($d:DiscountAutomaticBxgyInput!){ discountAutomaticBxgyCreate(automaticBxgyDiscount:$d){
  automaticDiscountNode{ id automaticDiscount{ ... on DiscountAutomaticBxgy{ title status } } }
  userErrors{ field message } } }"""

# check existing to avoid dupes
ex=gql('{ automaticDiscountNodes(first:50){ nodes{ automaticDiscount{ ... on DiscountAutomaticBxgy{ title } } } } }')
existing={ (n.get("automaticDiscount") or {}).get("title") for n in ex.get("data",{}).get("automaticDiscountNodes",{}).get("nodes",[]) }

for title, pid, qty in KITS:
    if title in existing:
        print(f"  ⏭ '{title}' există deja"); continue
    d={"title":title,"startsAt":STARTS,
       "customerBuys":{"value":{"quantity":"1"},"items":{"products":{"productsToAdd":[f"gid://shopify/Product/{pid}"]}}},
       "customerGets":{"value":{"discountOnQuantity":{"quantity":str(qty),"effect":{"percentage":1.0}}},
                       "items":{"collections":{"add":[COLL]}}}}
    if not APPLY:
        print(f"  [DRY] ar crea: {title} (buy kit {pid} -> {qty}× din colecție 100% off)"); continue
    res=gql(MUT,{"d":d})
    errs=res.get("data",{}).get("discountAutomaticBxgyCreate",{}).get("userErrors") or res.get("errors")
    if errs: print(f"  ✗ {title}: {errs}")
    else:
        node=res["data"]["discountAutomaticBxgyCreate"]["automaticDiscountNode"]
        print(f"  ✓ {title}: {node['id']} [{node['automaticDiscount'].get('status')}]")
