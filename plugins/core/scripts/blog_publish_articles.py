#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31", "psycopg2-binary>=2.9"]
# ///
"""
Multi-store editorial blog publisher for the ARONA perfume stores
(GT / Esteban / Nubra). Generalized from labnoir_publish_articles.py.

Per store it resolves the Shopify domain + blog id, validates that every
product CTA (/products/HANDLE) in each article exists and is in stock (against
the recon index), resolves the hero image from the main product, and creates
the articles via the Shopify Admin GraphQL `articleCreate` mutation.

Auth: ARONA Assistant custom app (client_credentials), secrets via kb_env.

Usage:
  uv run blog_publish_articles.py --store gt --dry-run     # validate + print, no writes
  uv run blog_publish_articles.py --store gt --draft       # create as UNPUBLISHED (review in admin)
  uv run blog_publish_articles.py --store gt               # create PUBLISHED (live)

Articles are read from  blog-rollout/articles/<store>.json  (list of objects:
title, slug, summary, tags[], body_html, main_product_handle,
cross_sell_handle, handles_used[], optional image_url/image_alt).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from kb_env import load_secrets_into_env  # noqa: E402
load_secrets_into_env()

# Publish-time data (articles/<store>.json + index/index_<store>.json) is bundled
# alongside this script in blog_data/ so the skill runs on any teammate's machine.
# Set BLOG_DATA_DIR to point at a working blog-rollout dir when regenerating content.
BASE = Path(os.environ.get("BLOG_DATA_DIR") or (Path(__file__).parent / "blog_data"))

STORES = {
    "gt": {
        "domain_env": "SHOPIFY_ARONA_GT_DOMAIN",
        "blog_id": "gid://shopify/Blog/116880474435",
        "author": "GT Parfumuri by George Talent",
    },
    "esteban": {
        "domain_env": "SHOPIFY_ARONA_ESTEBAN_DOMAIN",
        "blog_id": "gid://shopify/Blog/110902477145",
        "author": "Maison d'Esteban",
    },
    "nubra": {
        "domain_env": "SHOPIFY_ARONA_NUBRA_DOMAIN",
        "blog_id": "gid://shopify/Blog/102386696425",
        "author": "Nubra",
    },
}

VERSION = os.environ["SHOPIFY_ARONA_API_VERSION"]
CLIENT_ID = os.environ["SHOPIFY_ARONA_CLIENT_ID"]
CLIENT_SECRET = os.environ["SHOPIFY_ARONA_CLIENT_SECRET"]

HREF_RE = re.compile(r'/products/([a-z0-9\-]+)', re.IGNORECASE)
BANNED = re.compile(r'\b(cop[iî]e|clon[aă]|fake|replic[aă])\b', re.IGNORECASE)

ARTICLE_CREATE = """
mutation articleCreate($article: ArticleCreateInput!) {
  articleCreate(article: $article) {
    article { id handle title isPublished publishedAt tags blog { handle } image { url } }
    userErrors { field message code }
  }
}
"""


def mint(domain: str) -> str:
    r = requests.post(
        f"https://{domain}/admin/oauth/access_token",
        json={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
              "grant_type": "client_credentials"},
        timeout=20)
    r.raise_for_status()
    return r.json()["access_token"]


def gql(domain, token, query, variables=None):
    r = requests.post(
        f"https://{domain}/admin/api/{VERSION}/graphql.json",
        headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
        json={"query": query, "variables": variables or {}}, timeout=40)
    r.raise_for_status()
    out = r.json()
    if "errors" in out:
        raise RuntimeError(json.dumps(out["errors"], indent=2))
    return out["data"]


def load_index(store):
    idx = json.load(open(BASE / "index" / f"index_{store}.json"))
    by_handle = {p["handle"]: p for p in idx["products"]}
    return by_handle


def validate(article, by_handle):
    """Return (errors, warnings). Errors block publish; warnings are noted."""
    errs, warns = [], []
    handles = set(HREF_RE.findall(article.get("body_html", "")))
    handles |= {h for h in article.get("handles_used", [])}
    for h in [article.get("main_product_handle"), article.get("cross_sell_handle")]:
        if h:
            handles.add(h)
    for h in sorted(handles):
        p = by_handle.get(h)
        if not p:
            errs.append(f"handle not in catalog: /products/{h}")
        elif (p.get("stock") or 0) <= 0:
            warns.append(f"OOS product linked: /products/{h} (stock={p.get('stock')})")
    if BANNED.search(article.get("body_html", "")):
        errs.append("forbidden word (copie/clona/fake/replica) in body")
    n = len(article.get("body_html", ""))
    if n < 2500:
        warns.append(f"body short ({n} chars)")
    if "<hr" in article.get("body_html", "").lower():
        warns.append("contains <hr> (labnoir style drops these)")
    return errs, warns


def resolve_image(article, by_handle):
    if article.get("image_url"):
        return article["image_url"], article.get("image_alt") or article["title"]
    main = by_handle.get(article.get("main_product_handle"))
    if main and main.get("image"):
        return main["image"], article["title"]
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True, choices=list(STORES))
    ap.add_argument("--articles", help="path to articles json (default blog-rollout/articles/<store>.json)")
    ap.add_argument("--dry-run", action="store_true", help="validate + print only, no API writes")
    ap.add_argument("--draft", action="store_true", help="create as UNPUBLISHED (review in admin first)")
    ap.add_argument("--limit", type=int, default=0, help="only first N articles")
    args = ap.parse_args()

    cfg = STORES[args.store]
    domain = os.environ[cfg["domain_env"]]
    path = Path(args.articles) if args.articles else BASE / "articles" / f"{args.store}.json"
    articles = json.load(open(path))
    if isinstance(articles, dict) and "articles" in articles:
        articles = articles["articles"]
    if args.limit:
        articles = articles[: args.limit]
    by_handle = load_index(args.store)

    print(f"store={args.store}  domain={domain}  blog={cfg['blog_id']}")
    print(f"articles file={path}  count={len(articles)}")
    print(f"mode={'DRY-RUN' if args.dry_run else ('DRAFT' if args.draft else 'PUBLISH-LIVE')}\n")

    # validate all first
    blocking = 0
    for i, a in enumerate(articles, 1):
        errs, warns = validate(a, by_handle)
        img, _ = resolve_image(a, by_handle)
        flag = "OK " if not errs else "ERR"
        print(f"[{flag}] {i:>2}. {a['title'][:62]:<62} ({len(a.get('body_html',''))} ch)")
        print(f"        slug=/{a['slug']}  img={'yes' if img else 'NONE'}  CTA=/{a.get('main_product_handle')}")
        for e in errs:
            print(f"        ✗ {e}")
            blocking += 1
        for w in warns:
            print(f"        ! {w}")
    if blocking:
        print(f"\n{blocking} blocking error(s). Fix before publishing.")
        if not args.dry_run:
            sys.exit(1)
    if args.dry_run:
        print("\nDRY-RUN complete (no API writes).")
        return

    token = mint(domain)
    print(f"\ntoken minted ({token[:12]}...). Creating articles...\n")
    created = []
    for i, a in enumerate(articles, 1):
        img, alt = resolve_image(a, by_handle)
        art_in = {
            "blogId": cfg["blog_id"],
            "title": a["title"],
            "handle": a["slug"],
            "body": a["body_html"],
            "summary": a.get("summary", ""),
            "tags": a.get("tags", []),
            "author": {"name": cfg["author"]},
            "isPublished": (not args.draft),
        }
        if img:
            art_in["image"] = {"url": img, "altText": alt}
        data = gql(domain, token, ARTICLE_CREATE, {"article": art_in})
        res = data["articleCreate"]
        if res["userErrors"]:
            print(f"[{i}] USER ERRORS for '{a['title'][:50]}':")
            for e in res["userErrors"]:
                print(f"     {e['field']} [{e.get('code')}]: {e['message']}")
            continue
        art = res["article"]
        created.append(art)
        print(f"[{i}] ✓ /{art['blog']['handle']}/{art['handle']}  published={art['isPublished']}")
    print(f"\nDONE: {len(created)}/{len(articles)} articles created on {args.store}.")


if __name__ == "__main__":
    main()
