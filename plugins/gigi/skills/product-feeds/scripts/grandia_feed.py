#!/usr/bin/env python3
# Generator feed Grandia direct din Shopify (fara Omega). Emite Google Shopping RSS (Favi+Google) si Compari XML.
# Deps: doar stdlib. Tokenul: env SHOPIFY_GRAN_TOKEN + SHOPIFY_GRAN_SHOP, sau arg.
import os, sys, json, time, html, re, urllib.request, urllib.parse, xml.sax.saxutils as sx

SHOP  = os.environ.get("SHOPIFY_GRAN_SHOP", "n12w89-yy.myshopify.com")
TOKEN = os.environ.get("SHOPIFY_GRAN_TOKEN", "")
API   = f"https://{SHOP}/admin/api/2026-01/graphql.json"

Q = """
query($cursor: String) {
  products(first: 100, after: $cursor, query: "status:active") {
    pageInfo { hasNextPage endCursor }
    edges { node {
      id title handle productType vendor onlineStoreUrl descriptionHtml
      featuredImage { url }
      images(first: 6) { edges { node { url } } }
      variants(first: 1) { edges { node { sku barcode price compareAtPrice inventoryQuantity availableForSale } } }
    } }
  }
}"""

def gql(cursor=None):
    body = json.dumps({"query": Q, "variables": {"cursor": cursor}}).encode()
    req = urllib.request.Request(API, data=body, headers={
        "X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"})
    for attempt in range(5):
        try:
            return json.load(urllib.request.urlopen(req, timeout=60))
        except Exception as e:
            if attempt==4: raise
            time.sleep(2*(attempt+1))

def fetch_all():
    out=[]; cursor=None
    while True:
        d=gql(cursor)
        if "errors" in d: sys.exit("GraphQL: "+json.dumps(d["errors"])[:300])
        p=d["data"]["products"]
        for e in p["edges"]:
            n=e["node"]; v=(n["variants"]["edges"] or [{}])
            v=v[0]["node"] if v and "node" in v[0] else {}
            imgs=[x["node"]["url"] for x in n["images"]["edges"]] if n.get("images") else []
            out.append(dict(
                id=n["id"].split("/")[-1], title=(n["title"] or "").strip(),
                desc=re.sub("<[^>]+>","", n.get("descriptionHtml") or "").strip(),
                url=n.get("onlineStoreUrl") or f"https://grandia.ro/products/{n['handle']}",
                image=(n["featuredImage"] or {}).get("url","") or (imgs[0] if imgs else ""),
                images=imgs, brand=n.get("vendor") or "Grandia",
                ptype=n.get("productType") or "", sku=v.get("sku") or "",
                ean=v.get("barcode") or "", price=v.get("price") or "0",
                compare=v.get("compareAtPrice") or "",
                instock=bool(v.get("availableForSale")) or (v.get("inventoryQuantity") or 0)>0,
            ))
        if not p["pageInfo"]["hasNextPage"]: break
        cursor=p["pageInfo"]["endCursor"]
    return out

def money(x):  # "79.90" -> "79.90 RON"
    try: return f"{float(x):.2f} RON"
    except: return f"{x} RON"

def cdata(s): return f"<![CDATA[{s}]]>"

def google_feed(prods):
    L=['<?xml version="1.0" encoding="UTF-8"?>',
       '<rss xmlns:g="http://base.google.com/ns/1.0" version="2.0"><channel>',
       '<title>grandia.ro</title><link>https://grandia.ro</link><description>Grandia product feed</description>']
    for p in prods:
        reg, sale = p["price"], ""
        if p["compare"] and float(p["compare"])>float(p["price"]):
            reg, sale = p["compare"], p["price"]   # g:price=regular, g:sale_price=current
        it=['<item>',
            f'<g:id>{sx.escape(p["sku"] or p["id"])}</g:id>',
            f'<g:title>{cdata(p["title"])}</g:title>',
            f'<g:description>{cdata(p["desc"][:5000])}</g:description>',
            f'<g:link>{sx.escape(p["url"])}</g:link>',
            f'<g:image_link>{sx.escape(p["image"])}</g:image_link>']
        for u in p["images"][1:4]:
            it.append(f'<g:additional_image_link>{sx.escape(u)}</g:additional_image_link>')
        it+=[f'<g:price>{money(reg)}</g:price>']
        if sale: it.append(f'<g:sale_price>{money(sale)}</g:sale_price>')
        it+=[f'<g:availability>{"in_stock" if p["instock"] else "out_of_stock"}</g:availability>',
             f'<g:condition>new</g:condition>',
             f'<g:brand>{cdata(p["brand"])}</g:brand>']
        if p["sku"]: it.append(f'<g:mpn>{sx.escape(p["sku"])}</g:mpn>')
        if p["ean"]: it.append(f'<g:gtin>{sx.escape(p["ean"])}</g:gtin>')
        if p["ptype"]: it.append(f'<g:product_type>{cdata(p["ptype"])}</g:product_type>')
        it.append('</item>')
        L.append("".join(it))
    L.append('</channel></rss>')
    return "\n".join(L)

def compari_feed(prods):
    # Format Compari.ro: <products><product>...  preturi gross (cu TVA), numar simplu; doar produse pe stoc
    L=['<?xml version="1.0" encoding="UTF-8"?>','<products>']
    for p in prods:
        if not p["instock"]:
            continue
        it=['<product>',
            f'<identifier>{sx.escape(p["sku"] or p["id"])}</identifier>',
            f'<name>{cdata(p["title"])}</name>',
            f'<manufacturer>{cdata(p["brand"])}</manufacturer>',
            (f'<category>{cdata(p["ptype"])}</category>' if p["ptype"] else ''),
            f'<producturl>{sx.escape(p["url"])}</producturl>',
            f'<imageurl>{sx.escape(p["image"])}</imageurl>',
            f'<price>{float(p["price"]):.2f}</price>',
            f'<description>{cdata(p["desc"][:3000])}</description>',
            '<delivery_time>3</delivery_time>']
        if p["ean"]: it.append(f'<ean>{sx.escape(p["ean"])}</ean>')
        it.append('</product>')
        L.append("".join(x for x in it if x))
    L.append('</products>')
    return "\n".join(L)

if __name__=="__main__":
    if not TOKEN: sys.exit("lipseste SHOPIFY_GRAN_TOKEN")
    prods=fetch_all()
    outdir=sys.argv[1] if len(sys.argv)>1 else "."
    g=google_feed(prods)
    # feed-uri servite la feed.grandia.ro/<nume>
    feeds={"favi.xml": g, "google.xml": g}   # Favi acceptă formatul Google Shopping
    try:
        feeds["compari.xml"]=compari_feed(prods)   # dacă funcția e definită
    except NameError:
        pass
    for name,content in feeds.items():
        open(os.path.join(outdir,name),"w",encoding="utf-8").write(content)
    print(f"produse: {len(prods)} | in_stock: {sum(1 for p in prods if p['instock'])} | feed-uri: {', '.join(feeds)}")
