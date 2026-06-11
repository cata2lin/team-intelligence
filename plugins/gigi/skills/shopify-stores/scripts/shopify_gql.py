#!/usr/bin/env python3
"""
shopify_gql.py — talk to any team Shopify store from the CLI.

Resolves a store's (shop, token) by prefix, then runs an Admin GraphQL op
(or a REST GET) with 429 / throttle-aware backoff. Never prints the token.

Token resolution order:
  1. $SHOPIFY_STORES_CSV  (a CSV *path* OR the raw CSV text)
  2. ./stores.csv in the current working dir
  3. KB secret SHOPIFY_STORES_CSV  (via kb.py secret-get — canonical/freshest)

⚠ OAuth-rotation stores (e.g. NUB/Nubra) have a DEAD static token by design.
   For those, run this on the production dashboard server using core.stores
   instead (see the skill's §3) — the CSV token will 401.

Usage:
  shopify_gql.py --list
  shopify_gql.py --prefix GT --query 'query{ shop{ name } }'
  shopify_gql.py --prefix EST --query-file q.graphql --vars '{"q":"name:EST100"}'
  shopify_gql.py --prefix GT --rest themes.json
"""
import argparse, csv, io, json, os, subprocess, sys, time, urllib.request, urllib.error

API_VERSION = "2026-01"
OAUTH_PREFIXES = {"NUB"}  # static token is dead by design — resolve on the server


def _csv_text():
    env = os.getenv("SHOPIFY_STORES_CSV")
    if env:
        return env if "\n" in env else open(env, encoding="utf-8-sig").read()
    if os.path.exists("stores.csv"):
        return open("stores.csv", encoding="utf-8-sig").read()
    # fall back to the KB secret (canonical, freshest)
    kb = _find_kb()
    if kb:
        out = subprocess.run(["uv", "run", kb, "secret-get", "SHOPIFY_STORES_CSV"],
                             capture_output=True, text=True)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout
    raise SystemExit("Could not resolve stores.csv (env, cwd, or KB).")


def _find_kb():
    if os.getenv("KB_PY") and os.path.exists(os.getenv("KB_PY")):
        return os.getenv("KB_PY")
    d = os.getcwd()
    for _ in range(8):
        cand = os.path.join(d, "team-intelligence", "plugins", "core", "scripts", "kb.py")
        if os.path.exists(cand):
            return cand
        d = os.path.dirname(d)
    return None


def resolve_store(prefix):
    for row in csv.DictReader(io.StringIO(_csv_text())):
        if (row.get("prefix") or "").strip().lstrip("﻿").upper() == prefix.upper():
            shop = (row.get("shop") or "").strip().replace("https://", "").strip("/")
            token = (row.get("token") or "").strip()
            return shop, token
    raise SystemExit(f"prefix {prefix!r} not found in stores.csv")


def _request(url, headers, data=None):
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method="POST" if data else "GET")
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = float(e.headers.get("Retry-After", 2)) + attempt
                time.sleep(wait); continue
            body = e.read().decode()[:300]
            raise SystemExit(f"HTTP {e.code}: {body}")
    raise SystemExit("Gave up after repeated 429s")


def gql(shop, token, query, variables):
    url = f"https://{shop}/admin/api/{API_VERSION}/graphql.json"
    headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
    data = json.dumps({"query": query, "variables": variables or {}}).encode()
    res = _request(url, headers, data)
    # be a good citizen: ease off when the bucket is low
    ts = ((res.get("extensions") or {}).get("cost") or {}).get("throttleStatus") or {}
    if ts.get("currentlyAvailable", 999) < 100:
        time.sleep(1.0)
    return res


def rest_get(shop, token, path):
    url = f"https://{shop}/admin/api/{API_VERSION}/{path.lstrip('/')}"
    return _request(url, {"X-Shopify-Access-Token": token})


def main():
    ap = argparse.ArgumentParser(description="Run Shopify Admin API calls by store prefix.")
    ap.add_argument("--prefix")
    ap.add_argument("--query")
    ap.add_argument("--query-file")
    ap.add_argument("--vars", default="{}", help="JSON variables")
    ap.add_argument("--rest", help="REST GET path, e.g. themes.json")
    ap.add_argument("--list", action="store_true", help="list prefixes (no tokens)")
    a = ap.parse_args()

    if a.list:
        for row in csv.DictReader(io.StringIO(_csv_text())):
            p = (row.get("prefix") or "").strip().lstrip("﻿")
            if p:
                flag = "  (OAuth — static token dead, use server)" if p.upper() in OAUTH_PREFIXES else ""
                print(f"{p:8} {row.get('shop','').strip()}{flag}")
        return

    if not a.prefix:
        ap.error("--prefix is required (or use --list)")
    shop, token = resolve_store(a.prefix)
    if a.prefix.upper() in OAUTH_PREFIXES:
        print(f"# WARNING: {a.prefix} is an OAuth-rotation store — the CSV token is "
              f"likely dead. Run on the server via core.stores.get_store(). Trying anyway…",
              file=sys.stderr)

    if a.rest:
        print(json.dumps(rest_get(shop, token, a.rest), indent=2, ensure_ascii=False)); return

    query = a.query or (open(a.query_file, encoding="utf-8").read() if a.query_file else None)
    if not query:
        ap.error("provide --query, --query-file, or --rest")
    res = gql(shop, token, query, json.loads(a.vars))
    print(json.dumps(res, indent=2, ensure_ascii=False))
    if res.get("errors"):
        sys.exit(1)


if __name__ == "__main__":
    main()
