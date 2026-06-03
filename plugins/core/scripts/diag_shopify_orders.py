#!/usr/bin/env python3
"""
Diagnostic: exhaustive Shopify orders pull for Grandia, April 2026.
Compares created_at vs processed_at, logs every page, groups by status.
"""
from __future__ import annotations
import os, sys, time, requests
from collections import Counter
from datetime import date, timedelta, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from grandia_pnl import load_env, pg, shopify_credentials, shopify_mint_token, GRANDIA_SHOPIFY_DOMAIN

load_env()
met = pg("DATABASE_URL_METRICS")
creds = shopify_credentials(met, GRANDIA_SHOPIFY_DOMAIN)
access = shopify_mint_token(creds)

URL = f"https://{creds['shopifyDomain']}/admin/api/{creds['shopifyApiVersion']}/graphql.json"
HEADERS = {"X-Shopify-Access-Token": access, "Content-Type": "application/json"}

# Minimal query — only what's needed for the audit
Q = """
query($cursor: String, $q: String!) {
  orders(first: 250, after: $cursor, query: $q, sortKey: CREATED_AT) {
    pageInfo { hasNextPage endCursor }
    edges { node {
      id name createdAt processedAt cancelledAt
      displayFinancialStatus
      test
      app { name }
      currentTotalPriceSet { shopMoney { amount } }
    } }
  }
}
"""

def fetch_all(query_filter: str, label: str):
    print(f"\n=== {label}  query: '{query_filter}' ===", flush=True)
    cursor = None
    page = 0
    rows: list[dict] = []
    while True:
        page += 1
        r = requests.post(URL, headers=HEADERS,
            json={"query": Q, "variables": {"cursor": cursor, "q": query_filter}},
            timeout=60)
        if r.status_code != 200:
            print(f"  page {page}: HTTP {r.status_code} {r.text[:300]}", flush=True)
            break
        payload = r.json()
        if "errors" in payload:
            print(f"  page {page}: GraphQL errors {payload['errors']}", flush=True)
            break
        data = payload["data"]["orders"]
        edges = data["edges"]
        for e in edges:
            rows.append(e["node"])
        cost = payload.get("extensions", {}).get("cost", {})
        print(f"  page {page:>3}: +{len(edges):>3} (cum {len(rows):>5})  "
              f"hasNext={data['pageInfo']['hasNextPage']}  "
              f"cost={cost.get('actualQueryCost')}/{cost.get('throttleStatus', {}).get('maximumAvailable')}",
              flush=True)
        if not data["pageInfo"]["hasNextPage"]:
            break
        cursor = data["pageInfo"]["endCursor"]
        time.sleep(0.25)
    return rows


def summarize(rows: list[dict], label: str):
    by_status = Counter(r["displayFinancialStatus"] for r in rows)
    cancelled = sum(1 for r in rows if r.get("cancelledAt"))
    test_orders = sum(1 for r in rows if r.get("test"))
    total = sum(float(r["currentTotalPriceSet"]["shopMoney"]["amount"]) for r in rows)
    apps = Counter((r.get("app") or {}).get("name") for r in rows)
    print(f"\n--- {label} ---")
    print(f"  total orders : {len(rows)}")
    print(f"  cancelledAt  : {cancelled}")
    print(f"  test=true    : {test_orders}")
    print(f"  total value  : {total:,.2f}")
    print(f"  by status    : {dict(by_status)}")
    print(f"  by app       : {dict(apps)}")
    # date range sanity
    if rows:
        dates = sorted(r["createdAt"] for r in rows)
        print(f"  earliest createdAt : {dates[0]}")
        print(f"  latest   createdAt : {dates[-1]}")


# Run both variants
start = date(2026, 4, 1)
end_excl = date(2026, 5, 1)

# 1) created_at, strict half-open interval, same as the main script
r1 = fetch_all(
    f"created_at:>={start.isoformat()} created_at:<{end_excl.isoformat()}",
    "created_at half-open")
summarize(r1, "created_at >=2026-04-01 <2026-05-01")

# 2) created_at, inclusive end date
r2 = fetch_all(
    f"created_at:>={start.isoformat()} created_at:<=2026-04-30T23:59:59+03:00",
    "created_at inclusive with TZ")
summarize(r2, "created_at >=2026-04-01 <=2026-04-30T23:59:59+03:00")

# 3) processed_at (this is what Shopify Analytics often filters on)
r3 = fetch_all(
    f"processed_at:>={start.isoformat()} processed_at:<{end_excl.isoformat()}",
    "processed_at half-open")
summarize(r3, "processed_at >=2026-04-01 <2026-05-01")

# Cross-diff: which orders are in r1 but not r3 and vice versa
ids1 = {r["id"] for r in r1}
ids3 = {r["id"] for r in r3}
only1 = ids1 - ids3
only3 = ids3 - ids1
print(f"\n--- diff created_at vs processed_at ---")
print(f"  only in created_at  : {len(only1)}")
print(f"  only in processed_at: {len(only3)}")
print(f"  in both             : {len(ids1 & ids3)}")
