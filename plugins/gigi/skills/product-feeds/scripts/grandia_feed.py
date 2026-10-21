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
      carac: metafield(namespace: "custom", key: "caracteristici_produs") { value }
      gpc1: metafield(namespace: "mm-google-shopping", key: "google_product_category") { value }
      gpc2: metafield(namespace: "mc-facebook", key: "google_product_category") { value }
      category { fullName }
    } }
  }
}"""

# Tarifele de livrare se citesc din Shopify (profilurile de livrare), ca feed-ul sa nu ramana cu praguri vechi.
QSHIP = """
{
  deliveryProfiles(first: 10) { edges { node { id default
    profileLocationGroups { locationGroupZones(first: 10) { edges { node {
      zone { countries { code { countryCode } } }
      methodDefinitions(first: 20) { edges { node { active
        rateProvider { ... on DeliveryRateDefinition { price { amount } } }
        methodConditions { field operator conditionCriteria { ... on MoneyV2 { amount } } } } } } } } } }
  } } }
}"""
QPROF = """
query($id: ID!, $cursor: String) {
  deliveryProfile(id: $id) { profileItems(first: 100, after: $cursor) {
    pageInfo { hasNextPage endCursor } edges { node { product { id } } } } }
}"""

def gql(cursor=None, query=None, variables=None):
    body = json.dumps({"query": query or Q, "variables": variables if variables is not None else {"cursor": cursor}}).encode()
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
                params=parse_params((n.get("carac") or {}).get("value")),
                # categoria Google: ID-ul din metafield; altfel calea din taxonomia standard Shopify (aliniată cu cea Google)
                gpc=((n.get("gpc1") or {}).get("value") or (n.get("gpc2") or {}).get("value")
                     or ((n.get("category") or {}).get("fullName") or "").replace("Uncategorized","") or "").strip(),
            ))
        if not p["pageInfo"]["hasNextPage"]: break
        cursor=p["pageInfo"]["endCursor"]
    return out

# Produse respinse de Favi ("products-not-included-in-favi") pt ca productType-ul nostru nu se potriveste cu nicio
# categorie Favi (lista: favi.ro/partner-dashboard/category-list). Le trimitem in favi.xml cu calea categoriei Favi.
# Doar unde categoria chiar se potriveste - nu fortam produse auto/beauty/jucarii in categorii false (21.10.2026).
FAVI_TYPE = {
    "HA-2046-1": "decorațiuni > accesorii pentru animale de companie",
    "HA-0829":   "grădină > grădinărit > unelte de grădină",
    "HA-161":    "textile de uz casnic > perne și fețe de perne",
    "HA-1163":   "bucătărie > organizare și curățenie > organizatoare pentru bucătărie",
    "HA-1297":   "bucătărie > organizare și curățenie > organizatoare pentru bucătărie",
    "HA-0189":   "bucătărie > ajutoare în bucătărie > ustensile de bucătărie",
    "HA-0134":   "echipament pentru baie > curățenie > produse de curățenie",
    "HA-0936":   "echipament pentru baie > curățenie > mașini de spălat rufe",
    "HA-0416":   "camera copiilor > pentru nou-născuți > accesorii baie copii",
    "HA-0967":   "camera copiilor > pentru nou-născuți > accesorii baie copii",
    "SET-BEBE":  "camera copiilor > pentru nou-născuți > accesorii baie copii",
    # încadrate greșit de Favi (CPC scump): căpșuni -> „clapete rezervor wc”, coș scurgere -> „chiuvete de bucătărie”
    "HA-0397":   "bucătărie > ajutoare în bucătărie > ustensile de bucătărie",
    "HA-0437":   "bucătărie > organizare și curățenie > site chiuvetă",
    "2303219":   "grădină > wellness pentru acasă > accesorii pentru saună",
    "GD-IL-EXT-51703": "grădină > iluminat exterior",
    "10346":     "corpuri de iluminat > plafoniere",
    "10344":     "corpuri de iluminat > plafoniere",
    "14202":     "birou > seifuri",
    "GD-GA-2048277": "hol și coridor > suporturi pentru chei",
    "GD-EL-1294269": "construcție și reconstrucție > comutatoare și prize",
    "GD-CS-1874586": "dormitor > cutii și coșuri pentru depozitare > cutii și organizatoare pentru haine",
    "GD-CS-20700": "echipament pentru baie > curățenie > produse de curățenie",
    "GD-CS-20055": "echipament pentru baie > curățenie > produse de curățenie",
    "GD-PET-1682626": "decorațiuni > accesorii pentru animale de companie",
    "GD-PET-1470344": "decorațiuni > accesorii pentru animale de companie",
    "GD-PET-16536":   "decorațiuni > accesorii pentru animale de companie",
    "GD-PET-6338":    "decorațiuni > accesorii pentru animale de companie",
    "SET-CAINI":      "decorațiuni > accesorii pentru animale de companie",
    "12747":     "grădină > grădinărit > unelte de grădină",
    "11200":     "grădină > grădinărit > unelte de grădină",
    "GD-BR-20823": "grădină > grădinărit > unelte de grădină",
    "GD-GA-16559": "grădină > grădinărit > unelte de grădină",
}

# --- parametri Favi: din metafield-ul custom.caracteristici_produs (rich text cu "Cheie: valoare") ---
SKIP_PARAMS={"în colet","întreținere","atenționări","utilizare","utilizare recomandată","conținut","conținut pachet",
             "conținut set + acoperire","se folosește la","folosire","proprietăți cheie","funcții","bun de știut"}

def _rt_blocks(node):
    if node.get("type") in ("list-item","paragraph","heading"):
        def txt(x):
            return x.get("value","") if x.get("type")=="text" else "".join(txt(c) for c in x.get("children",[]))
        yield txt(node); return
    for c in node.get("children",[]): yield from _rt_blocks(c)

def parse_params(raw):
    """[(cheie, valoare)] doar pt randurile scurte de tip 'Cheie: valoare'; restul (texte lungi, avertismente) nu sunt parametri."""
    if not raw: return []
    try: root=json.loads(raw)
    except Exception: return []
    out=[]; seen=set()
    # unele produse au cate un rand pe list-item, altele toate randurile intr-un paragraf separate prin \n
    for line in (l for b in _rt_blocks(root) for l in b.split("\n")):
        line=" ".join(line.split())
        if ":" not in line: continue
        k,v=(x.strip() for x in line.split(":",1))
        if not k or not v or len(k)>40 or len(v)>150 or k.lower() in SKIP_PARAMS or k.lower() in seen: continue
        seen.add(k.lower()); out.append((k,v.rstrip(".;")))
    return out[:20]

def valid_gtin(s):
    s=(s or "").strip()
    if not s.isdigit() or len(s) not in (8,12,13,14): return False
    d=[int(c) for c in s]; body=d[:-1][::-1]
    return (10-sum(x*(3 if i%2==0 else 1) for i,x in enumerate(body))%10)%10==d[-1]

def fetch_shipping():
    """-> (reguli_default, {product_id: reguli}); reguli = [(pret_livrare, [(operator, prag)])] doar pt zonele cu RO."""
    d=gql(query=QSHIP, variables={})
    if "errors" in d: return None, {}
    default=None; by_prod={}
    for e in d["data"]["deliveryProfiles"]["edges"]:
        n=e["node"]; rules=[]
        for g in n["profileLocationGroups"]:
            for z in g["locationGroupZones"]["edges"]:
                z=z["node"]
                if "RO" not in [c["code"]["countryCode"] for c in z["zone"]["countries"]]: continue
                for m in z["methodDefinitions"]["edges"]:
                    m=m["node"]; price=(m.get("rateProvider") or {}).get("price")
                    if not m["active"] or not price: continue
                    conds=[(c["operator"],float(c["conditionCriteria"]["amount"])) for c in m["methodConditions"]
                           if c["field"]=="TOTAL_PRICE" and c.get("conditionCriteria")]
                    if len(conds)!=len(m["methodConditions"]): continue   # conditii pe greutate etc. -> nu ghicim
                    rules.append((float(price["amount"]),conds))
        if n["default"]: default=rules; continue
        cursor=None
        while True:
            x=gql(query=QPROF, variables={"id":n["id"],"cursor":cursor})
            if "errors" in x: break
            pi=x["data"]["deliveryProfile"]["profileItems"]
            for it in pi["edges"]: by_prod[it["node"]["product"]["id"].split("/")[-1]]=rules
            if not pi["pageInfo"]["hasNextPage"]: break
            cursor=pi["pageInfo"]["endCursor"]
    return default, by_prod

def ship_price(rules, price):
    ok={"GREATER_THAN_OR_EQUAL_TO":lambda a,b:a>=b,"LESS_THAN_OR_EQUAL_TO":lambda a,b:a<=b,
        "GREATER_THAN":lambda a,b:a>b,"LESS_THAN":lambda a,b:a<b}
    c=[r for r,conds in (rules or []) if all(o in ok and ok[o](price,t) for o,t in conds)]
    return min(c) if c else None

def money(x):  # "79.90" -> "79.90 RON"
    try: return f"{float(x):.2f} RON"
    except: return f"{x} RON"

def cdata(s): return f"<![CDATA[{s}]]>"

def google_feed(prods, favi=False, ship=None):
    # favi=True: + parametri (g:color/g:material/g:product_detail), g:shipping, g:google_product_category, doar GTIN valid.
    # google.xml ramane exact ca inainte (g:shipping ar suprascrie setarile de livrare din Merchant Center).
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
        if p["ean"] and (not favi or valid_gtin(p["ean"])): it.append(f'<g:gtin>{sx.escape(p["ean"])}</g:gtin>')
        ptype=(FAVI_TYPE.get(p["sku"]) if favi else None) or p["ptype"]
        if ptype: it.append(f'<g:product_type>{cdata(ptype)}</g:product_type>')
        if favi:
            if p["gpc"]: it.append(f'<g:google_product_category>{sx.escape(p["gpc"])}</g:google_product_category>')
            pm=dict((k.lower(),v) for k,v in p["params"])
            if pm.get("culoare"): it.append(f'<g:color>{cdata(pm["culoare"])}</g:color>')
            if pm.get("material"): it.append(f'<g:material>{cdata(pm["material"])}</g:material>')
            for k,v in p["params"]:
                it.append(f'<g:product_detail><g:section_name>Specificații</g:section_name>'
                          f'<g:attribute_name>{cdata(k)}</g:attribute_name><g:attribute_value>{cdata(v)}</g:attribute_value></g:product_detail>')
            if ship:
                sp=ship_price(ship[1].get(p["id"], ship[0]), float(p["price"]))
                if sp is not None: it.append(f'<g:shipping><g:country>RO</g:country><g:price>{money(sp)}</g:price></g:shipping>')
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
    instock=[p for p in prods if p["instock"]]
    # Garda: un raspuns Shopify ciudat (0 produse / stoc 0 aproape peste tot) nu are voie sa goleasca feed-urile.
    # Iesim cu eroare -> nu vine heartbeat -> data_health alerteaza; feed-urile vechi raman pe loc.
    if len(prods)<100 or len(instock)<0.5*len(prods):
        sys.exit(f"GARDA: produse={len(prods)} in_stock={len(instock)} - nu suprascriu feed-urile")
    g=google_feed(prods)
    # feed-uri servite la feed.grandia.ro/<nume>
    # Favi taxeaza click si pe out_of_stock -> in favi.xml intra DOAR produsele cu stoc (21.10.2026).
    ship=fetch_shipping()
    feeds={"favi.xml": google_feed(instock, favi=True, ship=ship), "google.xml": g}   # Favi acceptă formatul Google Shopping
    try:
        feeds["compari.xml"]=compari_feed(prods)   # dacă funcția e definită
    except NameError:
        pass
    # scriere atomica: Favi/Google nu trebuie sa prinda niciodata un fisier scris pe jumatate (cron la 15 min)
    for name,content in feeds.items():
        tmp=os.path.join(outdir,"."+name+".tmp")
        open(tmp,"w",encoding="utf-8").write(content)
        os.chmod(tmp,0o644)
        os.replace(tmp,os.path.join(outdir,name))
    print(f"produse: {len(prods)} | in_stock: {len(instock)} | feed-uri: {', '.join(feeds)}")
