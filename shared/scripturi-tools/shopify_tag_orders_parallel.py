#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Shopify Tag Orders — PARALLEL (workers + throttling + GraphQL tagsAdd)

• Concurență configurabilă: --workers (default 4)
• GraphQL `tagsAdd` by default (mai puține request-uri/comandă)
• Respectă 429 Retry-After + X-Shopify-Api-Call-Limit (REST) + throttleStatus (GraphQL) cu backoff
• Căutare după name (default GraphQL); fallback REST suportat
• Doar TAG — nu anulează comenzi, nu atinge plăți/inventar

Scopes: read_orders, write_orders (recomandat read_all_orders)
Python 3.8+
"""

import argparse, csv, json, re, sys, time, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote
import requests

DEFAULT_API_VERSION = "2024-10"

# ----------------- Rate limiting primitives -----------------
class RateLimiter:
    """Global min spacing between calls across threads."""
    def __init__(self, min_interval: float = 0.20):
        self.min_interval = float(min_interval)
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self):
        with self._lock:
            now = time.time()
            delta = now - self._last
            if delta < self.min_interval:
                time.sleep(self.min_interval - delta)
            self._last = time.time()

def post_resp_sleep(resp, base_sleep: float):
    """Adaptive small sleep based on call-limit headers (REST)."""
    hdr = resp.headers.get("X-Shopify-Shop-Api-Call-Limit") or resp.headers.get("X-Shopify-Api-Call-Limit")
    if hdr and "/" in hdr:
        try:
            cur, cap = [int(x.strip()) for x in hdr.split("/", 1)]
            ratio = cur / max(cap, 1)
            if ratio >= 0.9: time.sleep(max(1.5, base_sleep))
            elif ratio >= 0.75: time.sleep(max(0.9, base_sleep))
            elif ratio >= 0.6: time.sleep(max(0.5, base_sleep))
            else: time.sleep(base_sleep)
            return
        except Exception:
            pass
    time.sleep(base_sleep)

def graphql_throttle_sleep(gql_json: dict, safety_threshold: int = 20):
    """Sleep based on GraphQL throttleStatus; tries to keep a small buffer of points."""
    try:
        ts = ((gql_json or {}).get("extensions") or {}).get("cost", {}).get("throttleStatus") or {}
        cur = int(ts.get("currentlyAvailable", 0))
        mx  = int(ts.get("maximumAvailable", 0))
        rr  = float(ts.get("restoreRate", 0.0))  # points per second
        if rr <= 0:
            return
        if cur <= safety_threshold:
            # seconds needed to reach threshold
            need = max(0, safety_threshold - cur)
            sleep_s = need / rr
            # clamp to a reasonable window
            time.sleep(min(max(sleep_s, 0.0), 3.0))
    except Exception:
        return

# ----------------- HTTP with retries -----------------
def request_with_retry(method: str, url: str, headers: Dict[str, str], payload: Optional[dict], rl: RateLimiter,
                       timeout=30, max_retries=7, base_sleep=0.20, is_graphql=False) -> requests.Response:
    last_exc = None
    for attempt in range(1, max_retries + 1):
        rl.wait()
        try:
            if method == "GET":
                resp = requests.get(url, headers=headers, timeout=timeout)
            elif method == "POST":
                resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
            elif method == "PUT":
                resp = requests.put(url, headers=headers, json=payload, timeout=timeout)
            else:
                raise ValueError("Unsupported method")
        except requests.RequestException as e:
            last_exc = e
            if attempt == max_retries:
                raise
            time.sleep(min(2**attempt, 20))
            continue

        if resp.status_code == 429:
            ra = resp.headers.get("Retry-After")
            time.sleep(int(ra) if ra and ra.isdigit() else min(2**attempt, 20))
            continue

        if resp.status_code in (500, 502, 503, 504):
            if attempt == max_retries:
                return resp
            time.sleep(min(2**attempt, 20))
            continue

        # normal path: small adaptive pause
        if is_graphql:
            try:
                data = resp.json()
            except Exception:
                data = None
            graphql_throttle_sleep(data)
            time.sleep(base_sleep)  # small baseline even for GraphQL
        else:
            post_resp_sleep(resp, base_sleep)
        return resp

    if last_exc:
        raise last_exc
    return resp

# ----------------- Helpers -----------------
def make_name_candidates(raw: str) -> List[str]:
    s = (raw or "").strip()
    cands, seen = [], set()
    def add(x):
        if x and x not in seen:
            cands.append(x); seen.add(x)
    if not s: return cands
    add(s)
    if s.startswith("#"): add(s[1:])
    else: add("#"+s)
    m = re.search(r"(\d+)$", s.lstrip("#"))
    if m:
        num = m.group(1); add(num); add("#"+num)
    return cands

def gid_from_numeric(numeric_id: str) -> str:
    return f"gid://shopify/Order/{numeric_id}"

# ----------------- Shopify API calls -----------------
def gql_search(shop: str, token: str, api_version: str, order_key: str, rl: RateLimiter, base_sleep: float):
    url = f"https://{shop}/admin/api/{api_version}/graphql.json"
    headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json", "Accept": "application/json"}
    q = """
    query ($q: String!) {
      orders(first: 1, query: $q) { edges { node { id name } } }
    }"""
    last_json, last_status = None, 0
    for cand in make_name_candidates(order_key):
        for expr in (f'status:any name:"{cand}"', f'status:any "{cand}"'):
            payload = {"query": q, "variables": {"q": expr}}
            resp = request_with_retry("POST", url, headers, payload, rl, base_sleep=base_sleep, is_graphql=True)
            last_status = resp.status_code
            try: last_json = resp.json()
            except Exception: last_json = {"http_status": resp.status_code, "body": resp.text[:500]}
            if resp.status_code != 200 or "errors" in (last_json or {}): continue
            edges = (((last_json or {}).get("data") or {}).get("orders") or {}).get("edges", [])
            if edges:
                node = edges[0]["node"]
                return node["id"], node.get("name") or order_key, last_json, last_status
    return None, None, last_json, last_status

def gql_tags_add(shop: str, token: str, api_version: str, gid: str, tag: str, rl: RateLimiter, base_sleep: float):
    url = f"https://{shop}/admin/api/{api_version}/graphql.json"
    headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json", "Accept": "application/json"}
    m = """
    mutation tagsAdd($id: ID!, $tags: [String!]!) {
      tagsAdd(id: $id, tags: $tags) { userErrors { field message } }
    }"""
    payload = {"query": m, "variables": {"id": gid, "tags": [tag]}}
    resp = request_with_retry("POST", url, headers, payload, rl, base_sleep=base_sleep, is_graphql=True)
    status = resp.status_code
    try: data = resp.json()
    except Exception: data = {"http_status": status, "body": resp.text[:500]}
    return status, data

def rest_search_by_name(shop: str, token: str, api_version: str, order_key: str, rl: RateLimiter, base_sleep: float):
    headers = {"X-Shopify-Access-Token": token, "Accept": "application/json"}
    last_json, last_status = None, 0
    for name in make_name_candidates(order_key):
        url = f"https://{shop}/admin/api/{api_version}/orders.json?status=any&name={quote(name, safe='')}"
        resp = request_with_retry("GET", url, headers, None, rl, base_sleep=base_sleep, is_graphql=False)
        last_status = resp.status_code
        try: data = resp.json()
        except Exception: data = {"http_status": resp.status_code, "body": resp.text[:500]}
        last_json = data
        if resp.status_code != 200: continue
        orders = data.get("orders", [])
        if orders:
            o = orders[0]
            return str(o.get("id")), o.get("name") or order_key, last_json, last_status
    return None, None, last_json, last_status

def rest_get_order(shop: str, token: str, api_version: str, numeric_id: str, rl: RateLimiter, base_sleep: float):
    url = f"https://{shop}/admin/api/{api_version}/orders/{numeric_id}.json"
    headers = {"X-Shopify-Access-Token": token, "Accept": "application/json"}
    resp = request_with_retry("GET", url, headers, None, rl, base_sleep=base_sleep, is_graphql=False)
    try: data = resp.json()
    except Exception: data = {"http_status": resp.status_code, "body": resp.text[:500]}
    return resp.status_code, data

def rest_put_tags(shop: str, token: str, api_version: str, numeric_id: str, tags_csv: str, rl: RateLimiter, base_sleep: float):
    url = f"https://{shop}/admin/api/{api_version}/orders/{numeric_id}.json"
    headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json", "Accept": "application/json"}
    payload = {"order": {"id": int(numeric_id), "tags": tags_csv}}
    resp = request_with_retry("PUT", url, headers, payload, rl, base_sleep=base_sleep, is_graphql=False)
    status = resp.status_code
    try: msg = json.dumps(resp.json())
    except Exception: msg = resp.text[:500]
    return status, msg

# ----------------- Worker logic -----------------
class Runner:
    def __init__(self, args):
        self.shop = args.shop.strip()
        self.token = args.token.strip()
        self.api_version = args.api_version.strip()
        self.tag = (args.tag or "cancel").strip()
        self.search_mode = args.search
        self.method = args.method
        self.do_exec = bool(args.execute)
        self.base_sleep = float(args.min_sleep)
        self.rl = RateLimiter(self.base_sleep)
        self.print_lock = threading.Lock()
        self.results_lock = threading.Lock()
        self.stats = {"found":0, "not_found":0, "tag_added":0, "already":0, "errors":0}
        self.rows = []

    def log(self, msg):
        with self.print_lock:
            print(msg, flush=True)

    def record(self, row: dict):
        with self.results_lock:
            self.rows.append(row)

    def bump(self, key: str, inc: int = 1):
        with self.results_lock:
            self.stats[key] = self.stats.get(key, 0) + inc

    def process_key(self, idx: int, total: int, key: str):
        # lookup
        numeric_id = None; display = key
        raw_gql = raw_rest = None; gql_status = rest_status = 0

        if self.search_mode in ("graphql","both"):
            gid, dname, raw_gql, gql_status = gql_search(self.shop, self.token, self.api_version, key, self.rl, self.base_sleep)
            if gid:
                numeric_id = gid.split("/")[-1]
                display = dname or key

        if not numeric_id and self.search_mode in ("rest","both"):
            rid, rname, raw_rest, rest_status = rest_search_by_name(self.shop, self.token, self.api_version, key, self.rl, self.base_sleep)
            if rid:
                numeric_id = rid; display = rname or key

        if not numeric_id:
            self.log(f"[{idx}/{total}] {key} ... NOT FOUND")
            self.bump("not_found")
            self.record({"order": key, "id":"", "action":"not_found", "message":""})
            return

        self.log(f"[{idx}/{total}] {key} ... FOUND id={numeric_id} ({display})")
        self.bump("found")

        if not self.do_exec:
            self.log(f"    -> DRY-RUN: would add tag '{self.tag}'")
            self.record({"order": display, "id": numeric_id, "action":"would_tag", "message":self.tag})
            return

        if self.method == "graphql":
            status, data = gql_tags_add(self.shop, self.token, self.api_version, gid_from_numeric(numeric_id), self.tag, self.rl, self.base_sleep)
            errs = (((data or {}).get("data") or {}).get("tagsAdd") or {}).get("userErrors") or []
            if status == 200 and not errs:
                self.log(f"    -> TAG ADDED ('{self.tag}') [GraphQL]")
                self.bump("tag_added"); self.record({"order": display, "id": numeric_id, "action":"tag_added", "message":"graphql"})
            else:
                self.log(f"    -> ERROR tagsAdd: HTTP {status} | {errs or data}")
                self.bump("errors"); self.record({"order": display, "id": numeric_id, "action":"error", "message":str(errs or data)[:200]})
        else:
            # REST: GET tags then PUT
            gs, gb = rest_get_order(self.shop, self.token, self.api_version, numeric_id, self.rl, self.base_sleep)
            if gs != 200:
                self.log(f"    -> ERROR GET /orders/{numeric_id}.json: HTTP {gs}")
                self.bump("errors"); self.record({"order": display, "id": numeric_id, "action":"error_get", "message":str(gs)})
                return
            current = ((gb or {}).get("order", {}).get("tags") or "").strip()
            tag_list = [t.strip() for t in current.split(",") if t.strip()]
            if self.tag.lower() in (t.lower() for t in tag_list):
                self.log(f"    -> ALREADY '{self.tag}'")
                self.bump("already"); self.record({"order": display, "id": numeric_id, "action":"already", "message":""})
                return
            new_csv = ", ".join(tag_list + [self.tag])
            ps, pm = rest_put_tags(self.shop, self.token, self.api_version, numeric_id, new_csv, self.rl, self.base_sleep)
            if ps in (200,201):
                self.log(f"    -> TAG ADDED ('{self.tag}') [REST]")
                self.bump("tag_added"); self.record({"order": display, "id": numeric_id, "action":"tag_added", "message":"rest"})
            else:
                self.log(f"    -> ERROR PUT tags: HTTP {ps}")
                self.bump("errors"); self.record({"order": display, "id": numeric_id, "action":"error_put", "message":str(ps)})

# ----------------- Main -----------------
def load_orders_from_csv(path: str) -> List[str]:
    keys: List[str] = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        r = csv.DictReader(f)
        if "orders" not in r.fieldnames:
            print("[ERROR] CSV must contain a column named 'orders'.", file=sys.stderr); sys.exit(2)
        for row in r:
            v = (row.get("orders") or "").strip()
            if v: keys.append(v)
    # de-dup keep order
    seen, out = set(), []
    for k in keys:
        if k not in seen:
            seen.add(k); out.append(k)
    return out

def main():
    ap = argparse.ArgumentParser(description="Tag Shopify orders in parallel (workers + throttling).")
    ap.add_argument("--shop", required=True)
    ap.add_argument("--token", required=True)
    ap.add_argument("--csv", help="CSV cu coloana 'orders'")
    ap.add_argument("--single", help="Un singur nume de comandă")
    ap.add_argument("--api-version", default=DEFAULT_API_VERSION)

    ap.add_argument("--tag", default="cancel")
    ap.add_argument("--search", choices=["graphql","rest","both"], default="graphql")
    ap.add_argument("--method", choices=["graphql","rest"], default="graphql")
    ap.add_argument("--min-sleep", type=float, default=0.20, help="Pauză minimă globală între request-uri")
    ap.add_argument("--workers", type=int, default=4, help="Numărul de worker threads (default 4)")
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--report", help="Raport CSV (optional)")

    args = ap.parse_args()

    if not args.single and not args.csv:
        print("[ERROR] Provide --csv or --single.", file=sys.stderr); sys.exit(2)

    if args.single:
        orders = [args.single.strip()]
    else:
        orders = load_orders_from_csv(args.csv)

    runner = Runner(args)

    print("="*80)
    print("Shopify Tag Orders — PARALLEL")
    print(f"Shop: {args.shop} | API: {args.api_version}")
    print(f"Items: {len(orders)} | Workers: {args.workers}")
    print(f"Mode: {'EXECUTE' if args.execute else 'DRY-RUN'} | search={args.search} | method={args.method} | min_sleep={args.min_sleep}")
    print("="*80)

    total = len(orders)
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futures = []
        for idx, key in enumerate(orders, 1):
            futures.append(ex.submit(runner.process_key, idx, total, key))
        for _ in as_completed(futures):
            pass

    print("\n"+"="*80)
    print("Summary")
    for k, v in runner.stats.items():
        print(f"{k:>12}: {v}")
    print("="*80)
    if args.report:
        try:
            with open(args.report, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=["order","id","action","message"])
                w.writeheader(); w.writerows(runner.rows)
            print(f"[INFO] Wrote report to {args.report}")
        except Exception as e:
            print(f"[WARN] Could not write report: {e}", file=sys.stderr)

if __name__ == "__main__":
    main()
