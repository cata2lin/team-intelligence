#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31", "psycopg2-binary>=2.9"]
# ///
"""
Set SEO meta (title_tag / description_tag) on the blog AND each article, and
optionally rename the blog handle (e.g. news -> blog) with article redirects,
for the ARONA perfume stores (GT / Esteban / Nubra).

SEO is stored as `global.title_tag` / `global.description_tag` metafields
(single_line_text_field) — the mechanism the Online Store theme renders into
<title> and <meta name="description">. Article GIDs are resolved by handle.

Reads SEO from  blog-rollout/seo/seo.json :
  { "<store>": { "blog_seo": {title, description},
                 "articles": [ {slug, title, description}, ... ] } }

Usage:
  uv run blog_seo_and_handle.py --store gt --dry-run
  uv run blog_seo_and_handle.py --store gt --new-handle blog   # rename + SEO, live
  uv run blog_seo_and_handle.py --store gt                     # SEO only, keep handle
"""
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path
import requests

sys.path.insert(0, str(Path(__file__).parent))
from kb_env import load_secrets_into_env  # noqa: E402
load_secrets_into_env()

BASE = Path("/Users/gheorghebeschea/Downloads/Scripturi/blog-rollout")
VERSION = os.environ["SHOPIFY_ARONA_API_VERSION"]
CID = os.environ["SHOPIFY_ARONA_CLIENT_ID"]
CSEC = os.environ["SHOPIFY_ARONA_CLIENT_SECRET"]

STORES = {
    "gt":      {"domain_env": "SHOPIFY_ARONA_GT_DOMAIN",      "blog_id": "gid://shopify/Blog/116880474435"},
    "esteban": {"domain_env": "SHOPIFY_ARONA_ESTEBAN_DOMAIN", "blog_id": "gid://shopify/Blog/110902477145"},
    "nubra":   {"domain_env": "SHOPIFY_ARONA_NUBRA_DOMAIN",   "blog_id": "gid://shopify/Blog/102386696425"},
}

def mint(domain):
    r = requests.post(f"https://{domain}/admin/oauth/access_token",
                      json={"client_id": CID, "client_secret": CSEC, "grant_type": "client_credentials"}, timeout=20)
    r.raise_for_status(); return r.json()["access_token"]

def gql(domain, token, q, variables=None):
    r = requests.post(f"https://{domain}/admin/api/{VERSION}/graphql.json",
                      headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
                      json={"query": q, "variables": variables or {}}, timeout=40)
    r.raise_for_status(); out = r.json()
    if "errors" in out: raise RuntimeError(json.dumps(out["errors"], indent=2))
    return out["data"]

ARTICLES_Q = """
query($id: ID!, $cursor: String) {
  blog(id: $id) { handle articles(first: 100, after: $cursor) {
    pageInfo { hasNextPage endCursor } edges { node { id handle } } } }
}"""
BLOG_UPDATE = """
mutation($id: ID!, $blog: BlogUpdateInput!) {
  blogUpdate(id: $id, blog: $blog) { blog { id handle } userErrors { field message } } }"""
METAFIELDS_SET = """
mutation($mf: [MetafieldsSetInput!]!) {
  metafieldsSet(metafields: $mf) { metafields { key namespace ownerType } userErrors { field message code } } }"""

def fetch_articles(domain, token, blog_id):
    out, cursor = {}, None
    while True:
        d = gql(domain, token, ARTICLES_Q, {"id": blog_id, "cursor": cursor})
        b = d["blog"]; conn = b["articles"]
        for e in conn["edges"]:
            out[e["node"]["handle"]] = e["node"]["id"]
        if conn["pageInfo"]["hasNextPage"]: cursor = conn["pageInfo"]["endCursor"]
        else: return b["handle"], out

def mf(owner, key, value):
    return {"ownerId": owner, "namespace": "global", "key": key,
            "type": "single_line_text_field", "value": value}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True, choices=list(STORES))
    ap.add_argument("--seo", help="seo json (default blog-rollout/seo/seo.json)")
    ap.add_argument("--new-handle", help="rename blog handle to this (e.g. blog)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = STORES[args.store]
    domain = os.environ[cfg["domain_env"]]
    seo = json.load(open(Path(args.seo) if args.seo else BASE / "seo" / "seo.json"))[args.store]
    token = mint(domain)
    cur_handle, by_handle = fetch_articles(domain, token, cfg["blog_id"])
    print(f"store={args.store} domain={domain} blog={cfg['blog_id']} handle='{cur_handle}' articles={len(by_handle)}")

    # build metafields: blog + each article
    metas = [mf(cfg["blog_id"], "title_tag", seo["blog_seo"]["title"]),
             mf(cfg["blog_id"], "description_tag", seo["blog_seo"]["description"])]
    print(f"\nBLOG SEO:\n  title({len(seo['blog_seo']['title'])}): {seo['blog_seo']['title']}\n  desc ({len(seo['blog_seo']['description'])}): {seo['blog_seo']['description']}")
    missing = []
    print("\nARTICLE SEO:")
    for a in seo["articles"]:
        gid = by_handle.get(a["slug"])
        flag = "OK " if gid else "MISS"
        if not gid: missing.append(a["slug"])
        lt, ld = len(a["title"]), len(a["description"])
        wt = "" if lt <= 60 else " ⚠>60"
        wd = "" if 120 <= ld <= 160 else f" ⚠{ld}"
        print(f"  [{flag}] /{a['slug'][:46]:<46} T{lt}{wt} D{ld}{wd}")
        if gid:
            metas.append(mf(gid, "title_tag", a["title"]))
            metas.append(mf(gid, "description_tag", a["description"]))
    if missing:
        print(f"\n⚠ slugs not found on store: {missing}")

    if args.new_handle and args.new_handle != cur_handle:
        print(f"\nHANDLE: '{cur_handle}' -> '{args.new_handle}' (redirectArticles=true)")
    print(f"\nmetafields to set: {len(metas)}  mode={'DRY-RUN' if args.dry_run else 'LIVE'}")
    if args.dry_run:
        print("DRY-RUN: no writes."); return

    # 1) rename handle (+ redirect) first
    if args.new_handle and args.new_handle != cur_handle:
        d = gql(domain, token, BLOG_UPDATE, {"id": cfg["blog_id"],
                "blog": {"handle": args.new_handle, "redirectArticles": True,
                         "redirectNewHandle": True}})
        res = d["blogUpdate"]
        if res["userErrors"]:
            print("BLOG UPDATE ERRORS:", res["userErrors"]);
        else:
            print(f"✓ blog handle -> {res['blog']['handle']}")

    # 2) metafields in batches of 25
    done = 0
    for i in range(0, len(metas), 25):
        batch = metas[i:i+25]
        d = gql(domain, token, METAFIELDS_SET, {"mf": batch})
        res = d["metafieldsSet"]
        if res["userErrors"]:
            print("METAFIELD ERRORS:", res["userErrors"])
        done += len(res["metafields"])
    print(f"✓ set {done} metafields on {args.store}.")

if __name__ == "__main__":
    main()
