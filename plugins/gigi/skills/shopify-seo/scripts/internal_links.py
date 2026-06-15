# /// script
# requires-python = ">=3.9"
# dependencies = ["requests>=2.31"]
# ///
"""Internal-link builder for 'inspired-by' / dupe catalogs (Shopify Admin API).

Distributes internal PageRank and de-orphans content by adding contextual links.
DRY-RUN by default; --apply writes. NO EMOJI in inserted copy (team convention).
All inserts are idempotent (a marker regex strips the prior block before re-adding),
and every collectionUpdate re-sends the existing SEO title+description (golden rule
#1: seo{} REPLACES, it doesn't merge).

Modes:
  cluster    Interlink the top-N brand collections (handle prefix `parfumuri-inspirate-`)
             circularly: each links the next 3 siblings -> a tight hub cluster.
  pdp-brand  Append a link to its brand collection on each product of the top-N brands
             (anchor = "parfumurile inspirate din <Brand>"). Top-only by default.
  deorphan   Bidirectional collection<->blog-article links from a --map JSON file:
             the collection gets a "Ghiduri utile:" block, each article a "Vezi colecția:" CTA.

Examples:
  uv run internal_links.py cluster   --store esteban --top 8
  uv run internal_links.py pdp-brand --store esteban --top 8            # DRY-RUN: lists products
  uv run internal_links.py pdp-brand --store esteban --top 8 --apply
  uv run internal_links.py deorphan  --store esteban --map deorphan_esteban.json --apply
"""
import argparse, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shopify_lib import Store

PREFIX = "parfumuri-inspirate-"
CM = """mutation($i:CollectionInput!){collectionUpdate(input:$i){collection{handle}userErrors{field message}}}"""
AM = """mutation($id:ID!,$body:HTML!){articleUpdate(id:$id,article:{body:$body}){article{handle}userErrors{field message}}}"""
PM = """mutation($id:ID!,$body:String!){productUpdate(input:{id:$id,descriptionHtml:$body}){product{handle}userErrors{field message}}}"""


def label_of(c):
    return (c.get("title") or "").replace("Parfumuri inspirate din ", "").strip() or c["handle"]


def set_collection_body(st, c, new_body, apply):
    if not apply:
        return True
    seo = c.get("seo") or {}
    r = st.gql(CM, {"i": {"id": c["id"], "descriptionHtml": new_body,
                          "seo": {"title": seo.get("title"), "description": seo.get("description")}}})["collectionUpdate"]
    return not r["userErrors"]


def top_brand_collections(st, top):
    cols = [c for c in st.gql_all("collections", "id handle title productsCount{count} seo{title description} descriptionHtml")
            if c["handle"].startswith(PREFIX)]
    cols.sort(key=lambda x: -((x.get("productsCount") or {}).get("count", 0)))
    return cols[:top] if top else cols


def cmd_cluster(st, a):
    cols = top_brand_collections(st, a.top)
    n = len(cols)
    print(f"CLUSTER — interlink top {n} colecții de brand (fiecare -> 3 frați):\n")
    for i, c in enumerate(cols):
        sibs = [cols[(i + k) % n] for k in (1, 2, 3)]
        links = " · ".join(f'<a href="/collections/{s["handle"]}">{label_of(s)}</a>' for s in sibs)
        body = re.sub(r'<p>Descoperă și parfumuri inspirate din:.*?</p>', '', c["descriptionHtml"] or "", flags=re.S).strip()
        body += f'<p>Descoperă și parfumuri inspirate din: {links}.</p>'
        ok = set_collection_body(st, c, body, a.apply)
        print(f"  {label_of(c):<22} -> {', '.join(label_of(s) for s in sibs):<45} {'OK' if ok else 'ERR'}")
    if not a.apply:
        print("\n  DRY-RUN — nimic scris. Adaugă --apply.")


def cmd_pdp_brand(st, a):
    cols = top_brand_collections(st, a.top)
    print(f"PDP-BRAND — link spre colecția de brand pe produsele top {len(cols)} branduri:\n")
    total = 0
    for c in cols:
        lab = label_of(c)
        q = '{ collectionByHandle(handle:"%s"){ products(first:60){ nodes{ id title handle descriptionHtml } } } }' % c["handle"]
        prods = st.gql(q)["collectionByHandle"]["products"]["nodes"]
        link = f'<p>Vezi toate <a href="/collections/{c["handle"]}">parfumurile inspirate din {lab}</a>.</p>'
        marker = re.compile(r'<p>Vezi toate <a href="/collections/' + re.escape(PREFIX) + r'[^"]*">parfumurile inspirate din[^<]*</a>\.</p>', re.S)
        print(f"  ● {lab} ({len(prods)} produse) -> /collections/{c['handle']}")
        for p in prods:
            body = marker.sub('', p["descriptionHtml"] or "").strip()
            new_body = body + link
            total += 1
            if a.apply:
                r = st.gql(PM, {"id": p["id"], "body": new_body})["productUpdate"]
                tag = "OK" if not r["userErrors"] else f"ERR {r['userErrors']}"
            else:
                tag = "ar primi link"
            print(f"      {p['title'][:52]:<52} {tag}")
    print(f"\n  {total} produse{' actualizate' if a.apply else ''}." + ("" if a.apply else " DRY-RUN — adaugă --apply."))


def cmd_deorphan(st, a):
    mp = json.load(open(a.map, encoding="utf-8"))  # [{collection, label, articles:[{handle,title}]}]
    allcols = {c["handle"]: c for c in st.gql_all("collections", "id handle seo{title description} descriptionHtml")}
    arts = {x["handle"]: x for x in st.gql("{articles(first:100){nodes{id handle title body}}}")["articles"]["nodes"]}
    print(f"DEORPHAN — {len(mp)} colecții <-> articole (bidirecțional):\n")
    for m in mp:
        c = allcols.get(m["collection"])
        if not c:
            print(f"  ⚠️ colecție lipsă: {m['collection']}"); continue
        links = " · ".join(f'<a href="/blogs/blog/{x["handle"]}">{x["title"]}</a>' for x in m["articles"])
        cbody = re.sub(r'<p>Ghiduri utile:.*?</p>', '', c["descriptionHtml"] or "", flags=re.S).strip()
        cbody += f'<p>Ghiduri utile: {links}.</p>'
        ok = set_collection_body(st, c, cbody, a.apply)
        arts_done = []
        for x in m["articles"]:
            art = arts.get(x["handle"])
            if not art:
                arts_done.append(f"⚠️{x['handle']}"); continue
            abody = re.sub(r'<p>Vezi colecția:.*?</p>', '', art["body"] or "", flags=re.S).strip()
            abody += f'<p>Vezi colecția: <a href="/collections/{m["collection"]}">{m["label"]}</a>.</p>'
            if a.apply:
                r = st.gql(AM, {"id": art["id"], "body": abody})["articleUpdate"]
                arts_done.append("OK" if not r["userErrors"] else "ERR")
            else:
                arts_done.append("ar fi legat")
        print(f"  {m['label']:<26} coll {'OK' if ok else 'ERR'} · {len(m['articles'])} art {' '.join(arts_done)}")
    if not a.apply:
        print("\n  DRY-RUN — nimic scris. Adaugă --apply.")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("cluster", "pdp-brand", "deorphan"):
        sp = sub.add_parser(name)
        sp.add_argument("--store", default="esteban")
        sp.add_argument("--apply", action="store_true")
        if name in ("cluster", "pdp-brand"):
            sp.add_argument("--top", type=int, default=8, help="câte branduri de top (0 = toate)")
        if name == "deorphan":
            sp.add_argument("--map", required=True, help="fișier JSON cu maparea colecție<->articole")
    a = ap.parse_args()
    st = Store(a.store)
    {"cluster": cmd_cluster, "pdp-brand": cmd_pdp_brand, "deorphan": cmd_deorphan}[a.cmd](st, a)


if __name__ == "__main__":
    main()
