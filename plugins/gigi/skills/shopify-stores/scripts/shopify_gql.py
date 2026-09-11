#!/usr/bin/env python3
"""
shopify_gql.py — talk to any team Shopify store from the CLI.

Resolves a store's (shop, token) by prefix, then runs an Admin GraphQL op
(or a REST GET) with 429 / throttle-aware backoff. Never prints the token.

Token resolution order:
  1. $SHOPIFY_STORES_CSV  (a CSV *path* OR the raw CSV text)
  2. ./stores.csv in the current working dir
  3. KB secret SHOPIFY_STORES_CSV  (via kb.py secret-get — canonical/freshest)

⚠ Two kinds of store do NOT carry a usable token in the CSV:
   - client_credentials stores (BUC, ORC, SK, HU, MD, DUPBG) hold a MARKER
     `OAUTH:<NAME>_CLIENT_ID+SECRET` naming the two KB secrets — a token is minted
     here on demand (~24h), so these now work from any machine that can reach kb.py.
   - refresh-rotation stores (NUB/Nubra) have a DEAD static token by design: run
     this on the production dashboard server using core.stores (see the skill's §3).

Usage:
  shopify_gql.py --list
  shopify_gql.py --prefix GT --query 'query{ shop{ name } }'
  shopify_gql.py --prefix EST --query-file q.graphql --vars '{"q":"name:EST100"}'
  shopify_gql.py --prefix GT --rest themes.json
"""
import argparse, csv, io, json, os, subprocess, sys, time, urllib.request, urllib.error

API_VERSION = "2026-01"
OAUTH_PREFIXES = {"NUB"}  # refresh-rotation: static token dead by design — resolve on the server


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
    sb = _sb_secret("SHOPIFY_STORES_CSV")   # stations: no KB, secrets via the SB API
    if sb:
        return sb
    raise SystemExit("Could not resolve stores.csv (env, cwd, KB, or Second Brain API).")


def _find_kb():
    if os.getenv("KB_PY") and os.path.exists(os.getenv("KB_PY")):
        return os.getenv("KB_PY")
    d = os.getcwd()
    for _ in range(8):
        cand = os.path.join(d, "team-intelligence", "plugins", "core", "scripts", "kb.py")
        if os.path.exists(cand):
            return cand
        d = os.path.dirname(d)
    for cand in (os.path.expanduser(
                     "~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"),
                 "/opt/second-brain/vault/company/team-intelligence/plugins/core/scripts/kb.py"):
        if os.path.exists(cand):
            return cand
    return None


_MINTED = {}


def _sb_secret(name):
    """Second Brain API fallback for a secret.

    Stations (CS / depozit / colleagues) deliberately have NO KB_DATABASE_URL — their secrets
    come from the Second Brain API instead, with the credentials already on disk in
    ~/.claude/second-brain.env. Without this, this script only works on a machine with direct
    KB access.
    """
    conf = {}
    p = os.path.expanduser("~/.claude/second-brain.env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                conf[k.strip()] = v.strip()
    base = (os.getenv("SB_SERVER_URL") or conf.get("SECOND_BRAIN_URL") or "").rstrip("/")
    user = os.getenv("SB_USERNAME") or conf.get("SECOND_BRAIN_USER")
    pw = os.getenv("SB_PASSWORD") or conf.get("SECOND_BRAIN_PASS")
    if not (base and user and pw):
        return ""
    try:
        req = urllib.request.Request(
            f"{base}/api/auth/login",
            data=json.dumps({"username": user, "password": pw}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            jwt = json.loads(r.read().decode())["access_token"]
        req = urllib.request.Request(f"{base}/api/secrets/{name}/value",
                                     headers={"Authorization": f"Bearer {jwt}"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return (json.loads(r.read().decode()).get("value") or "").strip()
    except Exception:
        return ""


def _kb_secret(name):
    kb = _find_kb()
    if not kb:
        return ""
    out = subprocess.run(["uv", "run", kb, "secret-get", name], capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else ""


def _mint_client_credentials(shop, marker):
    """Mint a token for a store whose app grants client_credentials.

    stores.csv deliberately holds a MARKER (`OAUTH:<NAME>_CLIENT_ID+SECRET`) instead of a
    token for these: the token is short-lived (~24h), so a copy in the CSV would be dead by
    tomorrow — exactly the stale-token trap this skill exists to avoid. The marker names the
    two KB secrets, so any machine that can reach kb.py can mint one.
    """
    if shop in _MINTED:
        return _MINTED[shop]
    id_key = marker.split(":", 1)[1].split("+", 1)[0].strip()
    secret_key = id_key.replace("_CLIENT_ID", "_CLIENT_SECRET")
    cid = os.getenv(id_key) or _kb_secret(id_key) or _sb_secret(id_key)
    sec = os.getenv(secret_key) or _kb_secret(secret_key) or _sb_secret(secret_key)
    if not (cid and sec):
        raise SystemExit(f"{shop}: cannot mint a token — set {id_key} / {secret_key} in the "
                         f"env, or make kb.py reachable (KB_DATABASE_URL).")
    req = urllib.request.Request(
        f"https://{shop}/admin/oauth/access_token",
        data=json.dumps({"client_id": cid, "client_secret": sec,
                         "grant_type": "client_credentials"}).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            tok = json.loads(r.read().decode())["access_token"]
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{shop}: client_credentials mint failed — "
                         f"HTTP {e.code} {e.read().decode()[:200]}")
    _MINTED[shop] = tok
    return tok


def resolve_store(prefix):
    for row in csv.DictReader(io.StringIO(_csv_text())):
        if (row.get("prefix") or "").strip().lstrip("﻿").upper() == prefix.upper():
            shop = (row.get("shop") or "").strip().replace("https://", "").strip("/")
            token = (row.get("token") or "").strip()
            if token.upper().startswith("OAUTH:") and "+SECRET" in token.upper():
                token = _mint_client_credentials(shop, token)
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
            if e.code == 404:
                return None   # resursa lipseste in ACEST magazin — raspuns valid
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
                tok = (row.get("token") or "").strip().upper()
                flag = ("  (OAuth — static token dead, use server)" if p.upper() in OAUTH_PREFIXES
                        else "  (client_credentials — token minted on demand)"
                        if tok.startswith("OAUTH:") else "")
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
        res = rest_get(shop, token, a.rest)
        if res is None:
            print(f"NU EXISTA in magazinul {a.prefix}: {a.rest}")
        else:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        return

    query = a.query or (open(a.query_file, encoding="utf-8").read() if a.query_file else None)
    if not query:
        ap.error("provide --query, --query-file, or --rest")
    res = gql(shop, token, query, json.loads(a.vars))
    print(json.dumps(res, indent=2, ensure_ascii=False))
    if res.get("errors"):
        sys.exit(1)


if __name__ == "__main__":
    main()
