#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""
shopify_theme.py — read / search / WRITE Shopify theme asset files for any team store.

Companion to shopify_gql.py (reuses its store/token resolution). Adds the write verbs
shopify_gql.py lacks (it only does REST GET): theme asset PUT, and arbitrary POST/PUT/DELETE.

Verbs (all need --prefix <STORE> and --theme <ID>, except `themes`):
  themes                       list a store's themes (id, role, name) — find the main/live + copies
  list                         list every asset key in the theme
  get   KEY                    print one asset's contents (e.g. snippets/foo.liquid, templates/product.json)
  grep  REGEX [--keys RE]      search REGEX across .liquid assets (default), print file:line
  put   KEY --file PATH        upsert an asset from a local file   (WRITES)
  put   KEY --value '...'      upsert an asset from a string       (WRITES)

Examples:
  uv run shopify_theme.py themes --prefix GRAN
  uv run shopify_theme.py get snippets/meta-tags.liquid --prefix GRAN --theme 194991620440
  uv run shopify_theme.py grep "BreadcrumbList" --prefix GRAN --theme 194991620440
  uv run shopify_theme.py put sections/foo.liquid --file /tmp/foo.liquid --prefix GRAN --theme 194991620440

SAFETY: editing the MAIN theme edits the LIVE storefront. Prefer duplicating the live theme
in the admin (Online Store → Themes → Duplicate), editing the COPY, previewing with
?preview_theme_id=<ID>, then publishing. Never publish a theme without explicit confirmation.
"""
import argparse, json, re, sys, time, urllib.request, urllib.error
from shopify_gql import resolve_store, API_VERSION  # reuse token resolution + version


def rest(shop, token, method, path, body=None):
    """REST call with any verb (GET/POST/PUT/DELETE), 429-aware."""
    url = f"https://{shop}/admin/api/{API_VERSION}/{path.lstrip('/')}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read() or "{}")
        except urllib.error.HTTPError as e:
            if e.code in (429, 503):
                time.sleep(1.5 * (attempt + 1)); continue
            raise SystemExit(f"HTTP {e.code}: {e.read().decode()[:400]}")
    raise SystemExit("rate-limited too long")


def list_assets(shop, token, theme):
    d = rest(shop, token, "GET", f"themes/{theme}/assets.json")
    return [a["key"] for a in d.get("assets", [])]


def get_asset(shop, token, theme, key):
    import urllib.parse
    q = urllib.parse.urlencode({"asset[key]": key})
    d = rest(shop, token, "GET", f"themes/{theme}/assets.json?{q}")
    return d["asset"].get("value", "")


def put_asset(shop, token, theme, key, value):
    d = rest(shop, token, "PUT", f"themes/{theme}/assets.json",
             {"asset": {"key": key, "value": value}})
    return d.get("asset", {}).get("key")


def main():
    ap = argparse.ArgumentParser(description="Read/search/write Shopify theme assets.")
    ap.add_argument("cmd", choices=["themes", "list", "get", "grep", "put"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--prefix", required=True, help="store prefix from SHOPIFY_STORES_CSV")
    ap.add_argument("--theme", help="theme id (required for all cmds except `themes`)")
    ap.add_argument("--file"); ap.add_argument("--value")
    ap.add_argument("--keys", default=r"\.liquid$", help="grep: which asset keys to scan")
    a = ap.parse_args()
    shop, token = resolve_store(a.prefix)

    if a.cmd == "themes":
        for t in rest(shop, token, "GET", "themes.json").get("themes", []):
            print(f"id={t['id']:<14} role={t['role']:<11} {t['name']}")
        return

    if not a.theme:
        ap.error("--theme is required for this command (use `themes` to find it)")

    if a.cmd == "list":
        for k in list_assets(shop, token, a.theme):
            print(k)
    elif a.cmd == "get":
        sys.stdout.write(get_asset(shop, token, a.theme, a.arg))
    elif a.cmd == "grep":
        pat = re.compile(a.arg, re.I); keysel = re.compile(a.keys)
        for k in list_assets(shop, token, a.theme):
            if not keysel.search(k):
                continue
            try:
                v = get_asset(shop, token, a.theme, k)
            except SystemExit:
                continue
            for i, line in enumerate(v.splitlines(), 1):
                if pat.search(line):
                    print(f"{k}:{i}: {line.strip()[:160]}")
            time.sleep(0.05)
    elif a.cmd == "put":
        val = open(a.file, encoding="utf-8").read() if a.file else a.value
        if val is None:
            ap.error("provide --file or --value")
        print("upsert OK:", put_asset(shop, token, a.theme, a.arg, val))


if __name__ == "__main__":
    main()
