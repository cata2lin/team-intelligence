# /// script
# requires-python = ">=3.9"
# dependencies = ["requests>=2.31"]
# ///
"""
Apply listing/SEO fixes to a Shopify product — SEO title/description, body HTML,
and metafields (e.g. cross-sell). Reuses shopify_lib.Store (ARONA app auth).

SAFE BY DESIGN: DRY-RUN by default — shows before → after for each fix you pass and
writes NOTHING. Add --apply to execute. You approve selectively by choosing which
fix flags to pass (pass only the ones you approve). Same posture as gigi:cs-actions.

Usage:
    uv run product_fix.py --store grandia --product raft-depozitare-masina-de-spalat \\
        --seo-title "Raft Depozitare Baie 160cm – Organizator Mașină de Spălat | Grandia" \\
        --seo-description "..." --body-file new_desc.html            # DRY-RUN (default)
    ... add --apply to actually write.
    uv run product_fix.py --store grandia --product <handle> --metafield "custom.bought_together=gid://..." --apply
"""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shopify_lib import Store

def _get(store, product):
    if product.startswith("gid://"):
        q = '{node(id:"%s"){... on Product{id title handle descriptionHtml seo{title description}}}}' % product
        return store.gql(q)["node"]
    q = 'query($h:String!){productByHandle(handle:$h){id title handle descriptionHtml seo{title description}}}'
    return store.gql(q, {"h": product})["productByHandle"]

def _diff(label, old, new):
    if new is None: return False
    old = old or ""
    print(f"\n  ▸ {label}")
    print(f"       era : {(old[:160] + '…') if len(old) > 160 else old or '(gol)'}")
    print(f"      nou : {(new[:160] + '…') if len(new) > 160 else new}")
    return old != new

def main():
    ap = argparse.ArgumentParser(description="Apply listing/SEO fixes to a Shopify product (dry-run default).")
    ap.add_argument("--store", required=True); ap.add_argument("--product", required=True, help="handle or gid")
    ap.add_argument("--app", default="SHOPIFY_ARONA", help="app prefix: SHOPIFY_ARONA (esteban/gt/nubra/labnoir) or SHOPIFY (n12w89-yy.myshopify.com = Grandia etc.)")
    ap.add_argument("--seo-title", dest="seo_title"); ap.add_argument("--seo-description", dest="seo_desc")
    ap.add_argument("--body"); ap.add_argument("--body-file")
    ap.add_argument("--metafield", action="append", default=[], help="ns.key=value (repeatable)")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    body = a.body
    if a.body_file: body = open(a.body_file, encoding="utf-8").read()

    st = Store(a.store, a.app)
    p = _get(st, a.product)
    if not p: sys.exit(f"Produs negăsit: {a.product} pe {a.store}")
    print(f"Produs: {p['title']}  ({p['handle']})\n{'='*64}")
    mode = "APLIC (--apply)" if a.apply else "DRY-RUN (nimic nu se scrie — adaugă --apply)"
    print(f"  mod: {mode}")

    changed = False
    pinput = {"id": p["id"]}; seo = {}
    if a.seo_title is not None and _diff("SEO title", (p.get("seo") or {}).get("title"), a.seo_title): seo["title"] = a.seo_title; changed = True
    if a.seo_desc is not None and _diff("SEO description", (p.get("seo") or {}).get("description"), a.seo_desc): seo["description"] = a.seo_desc; changed = True
    if body is not None and _diff("descriere (body HTML)", p.get("descriptionHtml"), body): pinput["descriptionHtml"] = body; changed = True
    if seo: pinput["seo"] = seo
    mfs = []
    for m in a.metafield:
        nskey, _, val = m.partition("="); ns, _, key = nskey.partition(".")
        print(f"\n  ▸ metafield {ns}.{key}\n      nou : {val[:120]}")
        mfs.append({"ownerId": p["id"], "namespace": ns, "key": key, "type": "single_line_text_field", "value": val}); changed = True

    if not changed:
        print("\n  (nimic de schimbat / niciun fix dat)"); return
    if not a.apply:
        print(f"\n  → DRY-RUN. Confirmă cu --apply (sau doar fix-urile pe care le aprobi)."); return

    if len(pinput) > 1:
        r = st.gql("mutation($i:ProductInput!){productUpdate(input:$i){userErrors{field message}}}", {"i": pinput})
        errs = r["productUpdate"]["userErrors"]
        print("\n  productUpdate:", "OK" if not errs else f"ERORI {errs}")
    if mfs:
        r = st.gql("mutation($m:[MetafieldsSetInput!]!){metafieldsSet(metafields:$m){userErrors{field message}}}", {"m": mfs})
        errs = r["metafieldsSet"]["userErrors"]
        print("  metafieldsSet:", "OK" if not errs else f"ERORI {errs}")

if __name__ == "__main__":
    main()
