# /// script
# requires-python = ">=3.9"
# dependencies = ["requests>=2.31"]
# ///
"""Add the 'parfumuri-inspirate-*' brand collections as submenu items under
'Toate parfumurile' in the main menu. Preserves the whole existing menu tree.
DRY-RUN default; --apply runs menuUpdate."""
import argparse, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shopify_lib import Store

PARENT = "toate parfumurile"

def ser(node):
    """MenuItem node -> MenuItemUpdateInput (preserve id/title/type/resourceId/url + children)."""
    out = {"id": node["id"], "title": node["title"], "type": node["type"]}
    if node.get("resourceId"): out["resourceId"] = node["resourceId"]
    if node.get("url") and not node.get("resourceId"): out["url"] = node["url"]
    kids = node.get("items") or []
    if kids: out["items"] = [ser(k) for k in kids]
    return out

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--store", default="esteban"); ap.add_argument("--apply", action="store_true")
    ap.add_argument("--top", type=int, default=8, help="câte branduri (top după nr. produse) în meniu")
    a = ap.parse_args()
    st = Store(a.store)
    d = st.gql("""{ menus(first:20){ nodes{ id handle title
      items{ id title type url resourceId
        items{ id title type url resourceId
          items{ id title type url resourceId } } } } } }""")
    menu = next((m for m in d["menus"]["nodes"] if m["handle"] == "main-menu"), None)
    if not menu: sys.exit("main-menu negăsit")
    allcols = st.gql_all("collections", "id title handle productsCount{count}")
    cols = [c for c in allcols if c["handle"].startswith("parfumuri-inspirate-")]
    toate = next((c["id"] for c in allcols if c["handle"] == "toate-parfumurile"), None)
    # MENU: only the top brands by product count (the rest stay as collections for SEO/links)
    cols = sorted(cols, key=lambda x: -((x.get("productsCount") or {}).get("count", 0)))[:a.top]
    new_kids = [{"title": c["title"].replace("Parfumuri inspirate din ", ""), "type": "COLLECTION", "resourceId": c["id"]}
                for c in cols]

    items = [ser(it) for it in menu["items"]]
    # idempotent: reuse an existing "După Brand" top-level item if present
    dupa = next((it for it in items if it["title"].strip().lower() in ("după brand", "dupa brand", "după brand 🏷️")), None)
    if dupa:
        dupa["items"] = new_kids; dupa["type"] = "COLLECTION"; dupa["resourceId"] = toate; dupa.pop("url", None)
    else:
        node = {"title": "După Brand", "type": "COLLECTION", "resourceId": toate, "items": new_kids}
        # insert right after "Toate parfumurile" (or at front)
        idx = next((i for i, it in enumerate(items) if it["title"].strip().lower() == PARENT), -1)
        items.insert(idx + 1, node)

    print(f"Main menu — nou punct top-level 'După Brand' cu {len(new_kids)} subitemuri (branduri):")
    print("  ", ", ".join(k["title"] for k in new_kids))
    print("  top-level după modificare:", " | ".join(it["title"] for it in items))
    if not a.apply:
        print("\n  DRY-RUN — meniul NU s-a schimbat. Adaugă --apply."); return
    r = st.gql("mutation($id:ID!,$t:String!,$h:String,$i:[MenuItemUpdateInput!]!){menuUpdate(id:$id,title:$t,handle:$h,items:$i){menu{id}userErrors{field message}}}",
               {"id": menu["id"], "t": menu["title"], "h": menu["handle"], "i": items})
    errs = r["menuUpdate"]["userErrors"]
    print("\n  menuUpdate:", "OK ✅" if not errs else f"ERORI {errs}")

if __name__ == "__main__":
    main()
