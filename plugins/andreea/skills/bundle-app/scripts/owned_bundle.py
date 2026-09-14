# /// script
# requires-python = ">=3.10"
# dependencies = ["requests"]
# ///
"""
owned_bundle.py — seturi cadou pe care le PUTEM edita prin API.

Componentele unui bundle pot fi gestionate DOAR de app-ul care le-a creat. Seturile făcute din
Shopify Bundles ne sunt inaccesibile (swap manual). Seturile create cu scriptul ăsta sunt ALE
NOASTRE, deci swap-ul de componentă e un apel API — fără nicio atingere manuală.

  create  — creează un set DRAFT deținut de noi, cu componentele date
  show    — arată componentele curente ale unui set
  swap    — înlocuiește o componentă cu alta (ce e imposibil pe seturile Shopify Bundles)

Exemple:
  uv run owned_bundle.py create --store EST --title "Set Cadou Dulce" --skus 48,cutie-cadou,83
  uv run owned_bundle.py show   --store EST --product 16446374936921
  uv run owned_bundle.py swap   --store EST --product 16446374936921 --out 83 --in 88 --apply
"""
import argparse, csv, io, json, os, subprocess, sys

import requests

for _s in (sys.stdout, sys.stderr):                     # Windows / depozit: diacritice
    try: _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass

API_VERSION = "2026-01"
KB = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "core", "scripts", "kb.py")


def store(prefix):
    """(shop, token) din secretul KB SHOPIFY_STORES_CSV — tokenul nu se printează niciodată."""
    raw = os.getenv("SHOPIFY_STORES_CSV")
    if not raw:
        raw = subprocess.run(["uv", "run", os.path.abspath(KB), "secret-get", "SHOPIFY_STORES_CSV"],
                             capture_output=True, text=True, check=True).stdout.strip()
    for row in csv.DictReader(io.StringIO(raw)):
        if (row.get("prefix") or "").strip().upper() == prefix.upper():
            return row["shop"].strip(), row["token"].strip()
    sys.exit(f"Magazin {prefix!r} negăsit în SHOPIFY_STORES_CSV")


def gql(shop, tok, q, v=None, label=""):
    r = requests.post(f"https://{shop}/admin/api/{API_VERSION}/graphql.json",
                      headers={"X-Shopify-Access-Token": tok, "Content-Type": "application/json"},
                      json={"query": q, "variables": v or {}}, timeout=60)
    d = r.json()
    if d.get("errors"):
        sys.exit(f"⛔ {label}: {json.dumps(d['errors'])[:400]}")
    return d.get("data") or {}


REL = """mutation($input:[ProductVariantRelationshipUpdateInput!]!){
  productVariantRelationshipBulkUpdate(input:$input){
    parentProductVariants{ id productVariantComponents(first:30){ nodes{ quantity productVariant{ id sku } } } }
    userErrors{ code field message } } }"""


def rel(shop, tok, payload, label):
    res = gql(shop, tok, REL, {"input": [payload]}, label)["productVariantRelationshipBulkUpdate"]
    errs = res.get("userErrors") or []
    if errs:
        codes = [(e.get("code"), e["message"]) for e in errs]
        if any(c == "PRODUCT_EXPANDER_APP_OWNERSHIP_ALREADY_EXISTS" for c, _ in codes):
            sys.exit("⛔ Setul e deținut de ALT app (Shopify Bundles) — componentele NU se pot edita "
                     "prin API. Proprietatea se stabilește la crearea produsului și nu se transferă. "
                     "Fă swap-ul manual, sau creează setul cu `owned_bundle.py create`.")
        sys.exit(f"⛔ {label}: {codes}")
    return res


def variants_by_sku(shop, tok, skus):
    """SKU → variant gid (o interogare per SKU; SKU-urile sunt unice pe magazin)."""
    out = {}
    for sku in skus:
        d = gql(shop, tok, """query($q:String!){ productVariants(first:5, query:$q){ nodes{ id sku } } }""",
                {"q": f"sku:{sku}"}, f"lookup {sku}")
        v = next((n for n in d.get("productVariants", {}).get("nodes", []) if n["sku"] == sku), None)
        if not v: sys.exit(f"⛔ SKU {sku!r} negăsit pe magazin")
        out[sku] = v["id"]
    return out


def comps_of(shop, tok, product_id):
    d = gql(shop, tok, """query($id:ID!){ product(id:$id){ title status
          variants(first:5){ nodes{ id sku productVariantComponents(first:30){ nodes{ quantity productVariant{ id sku } } } } } } }""",
            {"id": f"gid://shopify/Product/{product_id}"}, "show")
    p = d.get("product") or sys.exit(f"⛔ produsul {product_id} nu există")
    var = next((v for v in p["variants"]["nodes"] if v["productVariantComponents"]["nodes"]), p["variants"]["nodes"][0])
    return p, var


def show(a):
    shop, tok = store(a.store)
    p, var = comps_of(shop, tok, a.product)
    print(f"{p['title']}  [{p['status']}]  variantă părinte {var['id'].split('/')[-1]}")
    for n in var["productVariantComponents"]["nodes"]:
        print(f"   {n['productVariant']['sku']:16} x{n['quantity']}")


def create(a):
    shop, tok = store(a.store)
    skus = [s.strip() for s in a.skus.split(",") if s.strip()]
    vmap = variants_by_sku(shop, tok, skus)
    if not a.apply:
        print(f"DRY-RUN — aș crea DRAFT {a.title!r} cu: {', '.join(skus)}\n  adaugă --apply")
        return
    M = """mutation($product:ProductCreateInput!){ productCreate(product:$product){
      product{ id title status variants(first:1){ nodes{ id } } } userErrors{ field message } } }"""
    res = gql(shop, tok, M, {"product": {"title": a.title, "status": "DRAFT", "vendor": a.vendor,
                                         "claimOwnership": {"bundles": True}}}, "productCreate")["productCreate"]
    if res.get("userErrors"): sys.exit(f"⛔ productCreate: {res['userErrors']}")
    p = res["product"]; parent = p["variants"]["nodes"][0]["id"]
    r = rel(shop, tok, {"parentProductVariantId": parent,
                        "productVariantRelationshipsToCreate": [{"id": vmap[s], "quantity": 1} for s in skus]},
            "atașare componente")
    got = [(n["productVariant"]["sku"], n["quantity"]) for n in r["parentProductVariants"][0]["productVariantComponents"]["nodes"]]
    print(f"✅ creat DRAFT {p['id'].split('/')[-1]} „{p['title']}\" — componente {got}")
    print("   Proprietar = app-ul nostru ⇒ swap prin API de-acum înainte.")


def swap(a):
    shop, tok = store(a.store)
    p, var = comps_of(shop, tok, a.product)
    cur = {n["productVariant"]["sku"]: n["productVariant"]["id"] for n in var["productVariantComponents"]["nodes"]}
    if a.out_sku not in cur: sys.exit(f"⛔ {a.out_sku!r} nu e componentă a setului (are: {list(cur)})")
    new_id = variants_by_sku(shop, tok, [a.in_sku])[a.in_sku]
    if not a.apply:
        print(f"DRY-RUN — {p['title']}: SCOATE {a.out_sku} → PUNE {a.in_sku}\n  adaugă --apply")
        return
    r = rel(shop, tok, {"parentProductVariantId": var["id"],
                        "productVariantRelationshipsToRemove": [cur[a.out_sku]],
                        "productVariantRelationshipsToCreate": [{"id": new_id, "quantity": a.qty}]}, "swap")
    got = [(n["productVariant"]["sku"], n["quantity"]) for n in r["parentProductVariants"][0]["productVariantComponents"]["nodes"]]
    print(f"✅ {p['title']}: {a.out_sku} → {a.in_sku}. Componente acum: {got}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("create", create), ("show", show), ("swap", swap)):
        s = sub.add_parser(name); s.add_argument("--store", default="EST"); s.set_defaults(fn=fn)
        if name == "create":
            s.add_argument("--title", required=True); s.add_argument("--skus", required=True)
            s.add_argument("--vendor", default="Maison d'Esteban"); s.add_argument("--apply", action="store_true")
        else:
            s.add_argument("--product", required=True)
            if name == "swap":
                s.add_argument("--out", dest="out_sku", required=True)
                s.add_argument("--in", dest="in_sku", required=True)
                s.add_argument("--qty", type=int, default=1); s.add_argument("--apply", action="store_true")
    a = ap.parse_args(); a.fn(a)


if __name__ == "__main__":
    main()
