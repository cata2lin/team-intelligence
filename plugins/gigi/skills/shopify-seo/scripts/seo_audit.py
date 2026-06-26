# /// script
# requires-python = ">=3.10"
# dependencies = ["requests"]
# ///
"""Comprehensive Shopify SEO audit for ONE store. Read-only.

    uv run seo_audit.py --store esteban.ro

Auto-discovers a sample product/collection/article, then checks (API + live):
meta coverage, structured data validity & presence, og/twitter, canonical, H1,
breadcrumbs, brand-doubling in <title>, homepage SEO text, sitemap inclusion,
new-collection channel publication. Prints a pass/fail matrix; no writes.

Pitfalls this audit deliberately guards against (see reference/pitfalls.md):
- attribute-order false negatives (canonical / meta description) -> tolerant regex
- edge cache -> cache-buster on every live fetch (still: re-check fails in a browser)
- brand-doubling -> flags only when the brand token appears TWICE in <title>
"""
import sys, os, re, json, html, argparse
sys.path.insert(0, os.path.dirname(__file__))
from shopify_lib import Store, fetch_live

ap = argparse.ArgumentParser()
ap.add_argument("--store", help="store domain/key, e.g. esteban.ro (ARONA-app stores)")
ap.add_argument("--app-prefix", default="SHOPIFY_ARONA")
ap.add_argument("--csv-prefix", help="stores.csv prefix for any team shop (OFER, ROSSI, CARP, GEN, …)")
A = ap.parse_args()
OK = lambda b: "OK " if b else "** FAIL **"

s = Store.from_csv(A.csv_prefix) if A.csv_prefix else Store(A.store, A.app_prefix)
pub = s.public


def jlds(h):
    out = []
    for b in re.findall(r'<script type="application/ld\+json">(.*?)</script>', h, re.S):
        try: out.append(json.loads(b))
        except Exception: out.append("__INVALID__")
    return out

def types(h):
    t, bad = set(), 0
    for d in jlds(h):
        if d == "__INVALID__": bad += 1; continue
        for it in (d if isinstance(d, list) else [d]):
            if isinstance(it, dict):
                tt = it.get("@type")
                if isinstance(tt, list): t.update(tt)
                elif tt: t.add(tt)
                for x in (it.get("@graph") or []):
                    if isinstance(x, dict) and isinstance(x.get("@type"), str): t.add(x["@type"])
    return t, bad

def metatag(h, key, attr="name"):
    for m in re.findall(r'<meta\b[^>]*>', h):
        if re.search(rf'{attr}\s*=\s*["\']{re.escape(key)}["\']', m):
            c = re.search(r'content\s*=\s*"([^"]*)"', m)
            if c: return html.unescape(c.group(1))
    return None

def title_of(h):
    m = re.search(r"<title>(.*?)</title>", h, re.S)
    return html.unescape(m.group(1).strip()) if m else ""

def brand_doubled(t):
    # a token repeated 2+ times in the <title> signals theme-append doubling
    for tok in re.findall(r"[A-Za-zĂÂÎȘȚăâîșț][\w'&. ]{2,30}", t):
        tok = tok.strip()
        if len(tok) >= 4 and t.count(tok) >= 2:
            return True, tok
    return False, None

# ---- discover sample handles ----
prod = (s.gql('{products(first:1,query:"status:active"){nodes{handle}}}')["products"]["nodes"] or [{}])[0].get("handle")
_junk = ("frontpage", "ultimate-search-do-not-delete")
coll = (s.gql('{collections(first:20){nodes{handle productsCount{count}}}}')["collections"]["nodes"])
coll = next((c["handle"] for c in coll if c["productsCount"]["count"] > 0 and c["handle"] not in _junk),
            (coll[0]["handle"] if coll else None))
blogs = s.gql('{blogs(first:1){nodes{handle articles(first:1){nodes{handle}}}}}')["blogs"]["nodes"]
art = blog = None
if blogs and blogs[0]["articles"]["nodes"]:
    blog, art = blogs[0]["handle"], blogs[0]["articles"]["nodes"][0]["handle"]

print(f"{'='*30} AUDIT {A.store}  (produs={prod} colectie={coll} articol={art})")

# ---- API: meta coverage ----
def cover(root):
    nodes = s.gql_all(root, "seo{title description}", "status:active" if root == "products" else "")
    tot = len(nodes)
    wt = sum(1 for n in nodes if (n["seo"]["title"] or "").strip())
    wd = sum(1 for n in nodes if (n["seo"]["description"] or "").strip())
    return tot, wt, wd
pt, ptt, ptd = cover("products")
ct, ctt, ctd = cover("collections")
print(f"[API] produse={pt}: SEO title {ptt}/{pt} {OK(ptt==pt)} | meta desc {ptd}/{pt} {OK(ptd==pt)}")
print(f"[API] colectii={ct}: SEO title {ctt}/{ct} {OK(ctt>=ct-2)} | meta desc {ctd}/{ct} {OK(ctd>=ct-2)}")

# ---- API: a collection's sales-channel publication breadth ----
c0 = s.gql('{collections(first:1){nodes{handle resourcePublicationsV2(first:20){nodes{isPublished}}}}}')["collections"]["nodes"]
if c0:
    nch = sum(1 for p in c0[0]["resourcePublicationsV2"]["nodes"] if p["isPublished"])
    print(f"[API] colectie publicata pe {nch} canale {OK(nch>=2)}  (publica colectiile noi pe TOATE canalele!)")

# ---- LIVE: product ----
if prod:
    h = fetch_live(f"https://{pub}/products/{prod}")
    tp, badp = types(h); t = title_of(h); db, tok = brand_doubled(t)
    print(f"[LIVE produs] <title>={t[:60]!r}")
    print(f"   brand_dublat={OK(not db)}{(' ('+tok+')') if db else ''} | meta_desc={OK(bool(metatag(h,'description')))} | canonical={OK(bool(re.search(r'rel=.canonical',h)))} | H1x{len(re.findall(r'<h1',h))}={OK(len(re.findall(r'<h1',h))==1)}")
    print(f"   og:image https={OK((metatag(h,'og:image','property') or '').startswith('https'))} | twitter:image={OK(bool(metatag(h,'twitter:image')))}")
    print(f"   JSON-LD {sorted(tp)} invalid={badp}{OK(badp==0)} | Product={OK('Product' in tp)} Offer={OK('offers' in h or 'Offer' in tp)} Breadcrumb={OK('BreadcrumbList' in tp)} Org={OK('Organization' in tp)}")

# ---- LIVE: collection ----
if coll:
    h = fetch_live(f"https://{pub}/collections/{coll}")
    tc, _ = types(h)
    print(f"[LIVE colectie] <title>={title_of(h)[:55]!r} | Breadcrumb={OK('BreadcrumbList' in tc)} | meta_desc={OK(bool(metatag(h,'description')))}")

# ---- LIVE: homepage ----
h = fetch_live(f"https://{pub}/")
th, _ = types(h)
print(f"[LIVE home] WebSite/SearchAction={OK('WebSite' in th)} | Organization={OK('Organization' in th)} | text_SEO={OK(h.lower().count('parfum')>3 or 'inspirat' in h.lower())}")

# ---- LIVE: article ----
if art:
    h = fetch_live(f"https://{pub}/blogs/{blog}/{art}")
    ta, _ = types(h); t = title_of(h); db, tok = brand_doubled(t)
    print(f"[LIVE articol] <title>={t[:55]!r} | brand_dublat={OK(not db)}{(' ('+tok+')') if db else ''} | Article={OK('Article' in ta or 'BlogPosting' in ta)}")

# ---- sitemap ----
idx = fetch_live(f"https://{pub}/sitemap.xml", bust=False)
subs = [html.unescape(u) for u in re.findall(r'<loc>(https://[^<]*sitemap_collections_\d+\.xml[^<]*)</loc>', idx)]
allc = "".join(fetch_live(u, bust=False) for u in subs)
ncols = len(re.findall(r'/collections/[^<?]+</loc>', allc))
nblog = idx.count("sitemap_blogs")
print(f"[SITEMAP] index_ok={OK(bool(idx))} | colectii_in_sitemap={ncols} | blog_sitemap={OK(nblog>0)}  (NB: sub-sitemap URLs carry ?from&to params)")
print(f"{'='*30} gata. Reverifica orice FAIL intr-un browser (cache).")
