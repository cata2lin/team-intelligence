#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31", "psycopg2-binary>=2.9"]
# ///
"""
Blog rollout recon — pull blogs, products and image files for GT / Esteban /
Nubra from Shopify (ARONA Assistant app), so articles can reference REAL
products with REAL handles and REAL featured images.

Writes one JSON per store to OUTDIR and prints a concise summary.
Read-only: only `blogs`, `products`, `files` queries. No mutations.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from kb_env import load_secrets_into_env  # noqa: E402
load_secrets_into_env()

VERSION = os.environ["SHOPIFY_ARONA_API_VERSION"]
CLIENT_ID = os.environ["SHOPIFY_ARONA_CLIENT_ID"]
CLIENT_SECRET = os.environ["SHOPIFY_ARONA_CLIENT_SECRET"]

OUTDIR = Path("/Users/gheorghebeschea/Downloads/Scripturi/blog-rollout/recon")
OUTDIR.mkdir(parents=True, exist_ok=True)

STORES = {
    "gt":      os.environ["SHOPIFY_ARONA_GT_DOMAIN"],
    "esteban": os.environ["SHOPIFY_ARONA_ESTEBAN_DOMAIN"],
    "nubra":   os.environ["SHOPIFY_ARONA_NUBRA_DOMAIN"],
}


def mint(domain: str) -> str:
    r = requests.post(
        f"https://{domain}/admin/oauth/access_token",
        json={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
              "grant_type": "client_credentials"},
        timeout=20)
    r.raise_for_status()
    return r.json()["access_token"]


def gql(domain: str, token: str, query: str, variables: dict | None = None) -> dict:
    r = requests.post(
        f"https://{domain}/admin/api/{VERSION}/graphql.json",
        headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
        json={"query": query, "variables": variables or {}},
        timeout=40)
    r.raise_for_status()
    out = r.json()
    if "errors" in out:
        raise RuntimeError(json.dumps(out["errors"], indent=2))
    return out["data"]


SHOP_Q = "{ shop { name primaryDomain { url } } }"

BLOGS_Q = """
{ blogs(first: 50) { edges { node {
  id handle title
  articles(first: 1) { edges { node { id } } }
} } } }
"""

PRODUCTS_Q = """
query($cursor: String) {
  products(first: 100, after: $cursor, query: "status:active") {
    pageInfo { hasNextPage endCursor }
    edges { node {
      handle title productType
      tags
      totalInventory
      featuredImage { url altText }
      description
    } }
  }
}
"""

FILES_Q = """
query($cursor: String) {
  files(first: 100, after: $cursor, query: "media_type:IMAGE") {
    pageInfo { hasNextPage endCursor }
    edges { node {
      ... on MediaImage { id alt image { url width height } }
    } }
  }
}
"""


def page_all(domain, token, query, root):
    cursor = None
    items = []
    while True:
        data = gql(domain, token, query, {"cursor": cursor})
        conn = data[root]
        for e in conn["edges"]:
            if e["node"]:
                items.append(e["node"])
        if conn["pageInfo"]["hasNextPage"]:
            cursor = conn["pageInfo"]["endCursor"]
        else:
            break
    return items


def main():
    for key, domain in STORES.items():
        print(f"\n{'='*70}\n{key.upper()}  ({domain})\n{'='*70}")
        token = mint(domain)
        shop = gql(domain, token, SHOP_Q)["shop"]
        print(f"shop: {shop['name']}  -  {shop['primaryDomain']['url']}")

        blogs = gql(domain, token, BLOGS_Q)["blogs"]["edges"]
        blogs = [b["node"] for b in blogs]
        print(f"\nBLOGS ({len(blogs)}):")
        for b in blogs:
            has = "has articles" if b["articles"]["edges"] else "EMPTY"
            print(f"  {b['id']}  /{b['handle']}  '{b['title']}'  [{has}]")

        products = page_all(domain, token, PRODUCTS_Q, "products")
        # trim description for the on-disk file
        for p in products:
            d = (p.get("description") or "").strip().replace("\n", " ")
            p["description"] = d[:400]
        print(f"\nPRODUCTS (active): {len(products)}")
        # product types histogram
        types = {}
        for p in products:
            t = p.get("productType") or "(none)"
            types[t] = types.get(t, 0) + 1
        for t, c in sorted(types.items(), key=lambda x: -x[1]):
            print(f"    {c:>4}  {t}")
        print("  sample handles:")
        for p in products[:12]:
            inv = p.get("totalInventory")
            print(f"    /products/{p['handle']:<32} {(p['title'] or '')[:46]}  (stock={inv})")

        files = page_all(domain, token, FILES_Q, "files")
        files = [f for f in files if f.get("image")]
        print(f"\nIMAGE FILES on store: {len(files)}")
        for f in files[:8]:
            print(f"    {f['image']['url']}")

        out = {
            "store": key,
            "domain": domain,
            "shop_name": shop["name"],
            "primary_domain": shop["primaryDomain"]["url"],
            "blogs": blogs,
            "products": products,
            "image_files": files,
        }
        path = OUTDIR / f"{key}.json"
        path.write_text(json.dumps(out, ensure_ascii=False, indent=2))
        print(f"\n  -> wrote {path}  ({len(products)} products, {len(files)} images)")


if __name__ == "__main__":
    main()
