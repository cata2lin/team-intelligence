# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31"]
# ///
"""Deploy ROSSI dip-powder landing as a Shopify PAGE (section + page template on live theme)."""
import csv, json, subprocess, sys, requests, pathlib

APPLY="--apply" in sys.argv
HERO="https://cdn.shopify.com/s/files/1/0764/0547/3623/files/Instagrampost-1_2_2.jpg"
API="2026-01"
HANDLE="kit-pudra-unghii"; TITLE="Kit Pudră de Unghii ROSSI"; SUFFIX="rdp-landing"

def kb(k):
    return subprocess.run(["uv","run","/Users/gheorghebeschea/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py","secret-get",k],capture_output=True,text=True).stdout
shop=tok=None
for r in csv.reader(kb("SHOPIFY_STORES_CSV").splitlines()):
    if r and r[0]=="ROSSI": shop,tok=r[1],r[2]
H={"X-Shopify-Access-Token":tok,"Content-Type":"application/json"}
base=f"https://{shop}/admin/api/{API}"

# live theme
themes=requests.get(f"{base}/themes.json",headers=H,timeout=30).json()["themes"]
live=next(t for t in themes if t["role"]=="main")
print("live theme:", live["id"], live["name"])

# build section content
here=pathlib.Path(__file__).parent
colors=json.load(open(here/"colors.json"))
reviews=json.load(open(here/"reviews.json")) if (here/"reviews.json").exists() else []
html=(here/"landing.html").read_text()
html=(html.replace("__COLORS__", json.dumps(colors, ensure_ascii=False))
          .replace("__REVIEWS__", json.dumps(reviews, ensure_ascii=False))
          .replace("__HERO__", HERO))
section="{% raw %}\n"+html+"\n{% endraw %}\n{% schema %}\n"+json.dumps({"name":"RDP Landing"})+"\n{% endschema %}\n"
template=json.dumps({"sections":{"main":{"type":"rdp-landing"}},"order":["main"]}, indent=2)

if not APPLY:
    print(f"DRY — section {len(section)}b, template {len(template)}b, {len(colors)} culori")
    print("ar scrie: sections/rdp-landing.liquid + templates/page.rdp-landing.json pe tema live + Page /"+HANDLE)
    sys.exit(0)

def put_asset(key, val):
    r=requests.put(f"{base}/themes/{live['id']}/assets.json",headers=H,
        json={"asset":{"key":key,"value":val}},timeout=60)
    print(f"  {key}: {r.status_code}", "" if r.status_code<300 else r.text[:200])
put_asset("sections/rdp-landing.liquid", section)
put_asset("templates/page.rdp-landing.json", template)

# create or update page
pages=requests.get(f"{base}/pages.json?handle={HANDLE}",headers=H,timeout=30).json().get("pages",[])
body={"page":{"title":TITLE,"handle":HANDLE,"body_html":"","template_suffix":SUFFIX,"published":True}}
if pages:
    pid=pages[0]["id"]; r=requests.put(f"{base}/pages/{pid}.json",headers=H,json=body,timeout=30)
    print("  page UPDATED:", r.status_code)
else:
    r=requests.post(f"{base}/pages.json",headers=H,json=body,timeout=30)
    print("  page CREATED:", r.status_code)
pg=r.json().get("page",{})
print(f"\n✅ LIVE: https://rossinails.ro/pages/{pg.get('handle',HANDLE)}")
