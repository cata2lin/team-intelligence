# /// script
# requires-python = ">=3.9"
# dependencies = ["requests>=2.31"]
# ///
"""Create 'Parfumuri inspirate din [Brand]' smart collections from product titles
(... by [Brand]). DRY-RUN default; --apply creates smart collections (rule: title
contains 'by [Brand]'). SEO hub + internal-link booster for the dupe products."""
import argparse, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shopify_lib import Store

# normalize a few brand variants to a clean public name
CLEAN = {"maison francis kurkdjian": "Maison Francis Kurkdjian", "mfk": "Maison Francis Kurkdjian",
         "yves saint laurent": "Yves Saint Laurent", "ysl": "Yves Saint Laurent"}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="esteban"); ap.add_argument("--min", type=int, default=3)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    st = Store(a.store)
    prods = st.gql_all("products", "title")
    brands = {}
    for p in prods:
        m = re.search(r"\bby\s+([A-Za-zÀ-ÿ'&\.\- ]+?)\s*$", p["title"] or "")
        if not m: continue
        raw = re.sub(r"\s+", " ", m.group(1)).strip(" .")
        key = CLEAN.get(raw.lower(), raw)
        brands.setdefault(key, []).append(p["title"])
    elig = {b: t for b, t in brands.items() if len(t) >= a.min}
    print(f"{len(prods)} produse · {len(brands)} branduri detectate · {len(elig)} cu ≥{a.min} produse\n")
    print(f"  {'#':>3}  Brand  →  colecție propusă (rule: title contains 'by <brand>')")
    for b, t in sorted(elig.items(), key=lambda x: -len(x[1])):
        handle = "parfumuri-inspirate-" + re.sub(r"[^a-z0-9]+", "-", b.lower()).strip("-")
        print(f"  {len(t):>3}  {b:<28} /collections/{handle}")
    skipped = {b: len(t) for b, t in brands.items() if len(t) < a.min}
    if skipped: print(f"\n  sărite (<{a.min} produse): " + ", ".join(f"{b}({n})" for b, n in sorted(skipped.items(), key=lambda x:-x[1])[:20]))

    if not a.apply:
        print("\n  DRY-RUN — nimic creat. Adaugă --apply ca să creez colecțiile smart."); return

    print("\n  CREEZ colecții smart:")
    # GOLDEN RULE #6: colecțiile noi default la ZERO canale → 404 pe storefront + invizibile pe Google/Shop.
    # Publicăm pe TOATE canalele imediat după creare.
    pubs = [{"publicationId": p["id"]} for p in st.gql("{ publications(first:25){ nodes{ id name } } }")["publications"]["nodes"]]
    M = """mutation($i:CollectionInput!){collectionCreate(input:$i){collection{id handle}userErrors{field message}}}"""
    P = """mutation($id:ID!,$pubs:[PublicationInput!]!){publishablePublish(id:$id,input:$pubs){userErrors{field message}}}"""
    for b, t in sorted(elig.items(), key=lambda x: -len(x[1])):
        handle = "parfumuri-inspirate-" + re.sub(r"[^a-z0-9]+", "-", b.lower()).strip("-")
        inp = {"title": f"Parfumuri inspirate din {b}", "handle": handle,
               "descriptionHtml": f"<p>Alternative accesibile inspirate din parfumurile {b} — aceleași note olfactive, persistență 12h+, la o fracțiune din preț. 2+1 gratis, plata la livrare.</p>",
               "seo": {"title": f"Parfumuri inspirate din {b} | Maison d'Esteban", "description": f"Alternative la parfumurile {b}: aceleași note, 12h+, fracțiune din preț. Livrare rapidă, plata la livrare."[:160]},
               "ruleSet": {"appliedDisjunctively": False, "rules": [{"column": "TITLE", "relation": "CONTAINS", "condition": f"by {b}"}]}}
        r = st.gql(M, {"i": inp})["collectionCreate"]
        errs = r["userErrors"]
        if errs:
            print(f"    {b:<28} ERR {str(errs)[:80]}"); continue
        cid = r["collection"]["id"]
        perr = st.gql(P, {"id": cid, "pubs": pubs})["publishablePublish"]["userErrors"]
        print(f"    {b:<28} OK /collections/{r['collection']['handle']}  {'· publicat pe toate canalele ✅' if not perr else '· PUBLICARE ERR '+str(perr)[:60]}")

if __name__ == "__main__":
    main()
