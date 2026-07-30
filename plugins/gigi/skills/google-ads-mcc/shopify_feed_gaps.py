# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31"]
# ///
"""shopify_feed_gaps.py — workflow: produse Shopify cu ATRIBUTE LIPSĂ → (opțional) feed_attr_filler direct.

Trage produsele active dintr-un magazin, găsește-le pe cele cu feed slab (fără `productType`) = exact cele care
devin „zombie" în PMax (0 clicuri). Cu --enrich rulează feed_attr_filler ca să genereze titlu+atribute mai bune.
Leagă `shopify_gql.py` (magazin+token din KB) cu `feed_attr_filler.py`. READ-ONLY pe Shopify (doar citește; scrierea
înapoi o faci manual după review).

Usage:
  uv run shopify_feed_gaps.py --prefix GRAND --limit 40                 # doar listează golurile
  uv run shopify_feed_gaps.py --prefix GRAND --limit 20 --enrich        # + rulează feed_attr_filler
"""
import os, sys, json, re, argparse, subprocess, tempfile
HERE=os.path.dirname(os.path.abspath(__file__))
SHOPIFY_GQL=os.path.join(HERE,"..","shopify-stores","scripts","shopify_gql.py")
FILLER=os.path.join(HERE,"feed_attr_filler.py")
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass
# uv nested: copilul moștenește VIRTUAL_ENV-ul părintelui → warning/mismatch. Îl scoatem.
def _env():
    e=dict(os.environ); e.pop("VIRTUAL_ENV",None); return e
ap=argparse.ArgumentParser()
ap.add_argument("--prefix",required=True,help="prefix magazin (GRAND/GT/EST/...)")
ap.add_argument("--limit",type=int,default=40,help="câte produse cu goluri să iau")
ap.add_argument("--enrich",action="store_true",help="rulează feed_attr_filler pe goluri")
ap.add_argument("--brand",default="",help="context brand pt enrichment (altfel = prefix)")
a=ap.parse_args()
def strip_html(s): return re.sub(r"<[^>]+>"," ",s or "").strip()
QUERY=('query($cursor:String){ products(first:100, after:$cursor, query:"status:active"){ '
       'edges{ cursor node{ id title productType descriptionHtml } } pageInfo{ hasNextPage } } }')
gaps=[]; cursor=None; pages=0
while pages<15 and len(gaps)<a.limit:
    pages+=1
    variables=json.dumps({"cursor":cursor})
    r=subprocess.run(["uv","run",SHOPIFY_GQL,"--prefix",a.prefix,"--query",QUERY,"--vars",variables],
                     capture_output=True,text=True,timeout=120,env=_env())
    if r.returncode!=0: print(f"❌ shopify_gql: {(r.stderr or r.stdout)[:300]}"); sys.exit(1)
    try: d=json.loads(r.stdout)
    except Exception: print(f"❌ răspuns ne-JSON: {r.stdout[:300]}"); sys.exit(1)
    prod=(((d.get("data") or {}).get("products")) or {})
    edges=prod.get("edges",[])
    if not edges: break
    for e in edges:
        n=e.get("node",{})
        if not (n.get("productType") or "").strip():   # gol = feed slab
            gaps.append({"id":(n.get("id") or "").split("/")[-1],"title":n.get("title",""),
                         "description":strip_html(n.get("descriptionHtml"))[:300],"type":""})
        if len(gaps)>=a.limit: break
    if not prod.get("pageInfo",{}).get("hasNextPage"): break
    cursor=edges[-1].get("cursor")
print(f"═══ FEED GAPS — {a.prefix}: {len(gaps)} produse ACTIVE fără productType (candidați zombie PMax) ═══")
for g in gaps[:a.limit]:
    print(f"  [{g['id']}] {g['title'][:64]}")
if not gaps:
    print("  ✅ toate produsele active au productType setat"); sys.exit(0)
if a.enrich:
    print(f"\n→ Rulez feed_attr_filler pe {len(gaps)} produse…\n")
    tf=tempfile.NamedTemporaryFile("w",suffix=".json",delete=False,encoding="utf-8")
    json.dump(gaps,tf,ensure_ascii=False); tf.close()
    out=subprocess.run(["uv","run",FILLER,"--input",tf.name,"--brand",a.brand or a.prefix],
                       capture_output=True,text=True,timeout=300,env=_env())
    print(out.stdout or out.stderr[:500])
    os.unlink(tf.name)
else:
    print(f"\n→ Adaugă --enrich ca să generezi titluri/atribute cu feed_attr_filler.")
