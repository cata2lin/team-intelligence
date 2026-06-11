#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31", "psycopg2-binary>=2.9"]
# ///
"""
Add a 'Blog' link (type BLOG, resource-linked so it follows handle changes) to a
store's footer navigation menu, idempotently. menuUpdate is a full replace, so
we re-send every existing item (preserving its id) plus the new Blog item.

Per-store target menu (verified against the live footer 'Suport' column):
  gt      -> footer       (already has Blog; no-op)
  esteban -> footer
  nubra   -> footer-menu

Usage:
  uv run blog_add_to_footer.py --store esteban --dry-run
  uv run blog_add_to_footer.py --store esteban
"""
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path
import requests

sys.path.insert(0, str(Path(__file__).parent))
from kb_env import load_secrets_into_env  # noqa: E402
load_secrets_into_env()

VERSION = os.environ["SHOPIFY_ARONA_API_VERSION"]
CID = os.environ["SHOPIFY_ARONA_CLIENT_ID"]; CSEC = os.environ["SHOPIFY_ARONA_CLIENT_SECRET"]

STORES = {
    "gt":      {"domain_env": "SHOPIFY_ARONA_GT_DOMAIN",      "blog_id": "gid://shopify/Blog/116880474435", "menu": "footer"},
    "esteban": {"domain_env": "SHOPIFY_ARONA_ESTEBAN_DOMAIN", "blog_id": "gid://shopify/Blog/110902477145", "menu": "footer"},
    "nubra":   {"domain_env": "SHOPIFY_ARONA_NUBRA_DOMAIN",   "blog_id": "gid://shopify/Blog/102386696425", "menu": "footer-menu"},
}

def mint(d):
    return requests.post(f"https://{d}/admin/oauth/access_token", json={"client_id": CID, "client_secret": CSEC, "grant_type": "client_credentials"}, timeout=20).json()["access_token"]
def gql(d, t, q, v=None):
    r = requests.post(f"https://{d}/admin/api/{VERSION}/graphql.json", headers={"X-Shopify-Access-Token": t}, json={"query": q, "variables": v or {}}, timeout=40)
    r.raise_for_status(); o = r.json()
    if "errors" in o: raise RuntimeError(json.dumps(o["errors"], indent=2))
    return o["data"]

MENUS_Q = """
{ menus(first: 30) { edges { node { id handle title
  items { id title type url resourceId tags
    items { id title type url resourceId tags
      items { id title type url resourceId tags } } } } } } }"""
MENU_UPDATE = """
mutation($id: ID!, $title: String!, $handle: String!, $items: [MenuItemUpdateInput!]!) {
  menuUpdate(id: $id, title: $title, handle: $handle, items: $items) {
    menu { id handle items { title type url } } userErrors { field message } } }"""

def to_input(node):
    """Convert a fetched menu item to MenuItemUpdateInput, preserving id + children."""
    it = {"id": node["id"], "title": node["title"], "type": node["type"], "tags": node.get("tags") or []}
    if node.get("resourceId"):
        it["resourceId"] = node["resourceId"]
    elif node.get("url") is not None:
        it["url"] = node["url"]
    if node.get("items"):
        it["items"] = [to_input(c) for c in node["items"]]
    return it

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True, choices=list(STORES))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cfg = STORES[args.store]
    domain = os.environ[cfg["domain_env"]]
    token = mint(domain)

    menus = {e["node"]["handle"]: e["node"] for e in gql(domain, token, MENUS_Q)["menus"]["edges"]}
    menu = menus.get(cfg["menu"])
    if not menu:
        print(f"menu '{cfg['menu']}' not found on {args.store}. Available: {list(menus)}"); sys.exit(1)

    print(f"store={args.store} menu='{menu['handle']}' ('{menu['title']}') items={len(menu['items'])}")
    for it in menu["items"]:
        print(f"   - {it['title']} [{it['type']}] {it.get('url')}")

    # idempotency: already has a BLOG-type item pointing at our blog?
    has_blog = any(it["type"] == "BLOG" and (it.get("resourceId") == cfg["blog_id"] or "/blogs/" in (it.get("url") or "")) for it in menu["items"])
    if has_blog:
        print("\n✓ Blog link already present in this menu — nothing to do.")
        return

    items = [to_input(it) for it in menu["items"]]
    blog_item = {"title": "Blog", "type": "BLOG", "resourceId": cfg["blog_id"], "tags": []}
    # insert right after a SEARCH item if present (mirrors GT), else append
    pos = next((i + 1 for i, it in enumerate(menu["items"]) if it["type"] == "SEARCH"), len(items))
    items.insert(pos, blog_item)

    print(f"\nWill insert 'Blog' (BLOG -> {cfg['blog_id']}) at position {pos}. New count={len(items)}.  mode={'DRY-RUN' if args.dry_run else 'LIVE'}")
    if args.dry_run:
        print("DRY-RUN: no writes."); return

    d = gql(domain, token, MENU_UPDATE, {"id": menu["id"], "title": menu["title"], "handle": menu["handle"], "items": items})
    res = d["menuUpdate"]
    if res["userErrors"]:
        print("ERRORS:", res["userErrors"]); sys.exit(1)
    print("✓ updated. New items:")
    for it in res["menu"]["items"]:
        print(f"   - {it['title']} [{it['type']}] {it['url']}")

if __name__ == "__main__":
    main()
