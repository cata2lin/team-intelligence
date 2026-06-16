# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31"]
# ///
"""Add (or remove) a `noindex, nofollow` <meta robots> on a SPECIFIC Shopify page,
so it drops out of Google search — without touching any other page.

Shopify has no per-page noindex toggle in admin; the only API way is a conditional in
the theme layout. This injects, into `layout/theme.liquid`'s <head>:

    {%- if request.path contains '<PATH>' -%}<meta name="robots" content="noindex, nofollow">{%- endif -%}

Works for normal pages (`/pages/...`), policy pages (`/policies/contact-information`,
the auto-generated contact/ARONA-SRL page), collections, etc. `contains` matches the
path, so keep <PATH> specific.

Usage:
    uv run noindex_page.py --prefix BON --path /policies/contact-information           # dry-run
    uv run noindex_page.py --prefix BON --path /policies/contact-information --apply
    uv run noindex_page.py --prefix BON --path /policies/contact-information --remove --apply

Resolves shop+token from the KB secret SHOPIFY_STORES_CSV (never printed). For OAuth
token-rotation stores (e.g. Nubra/NUB) the CSV token is stale by design → run on the
VPS via `core.stores.get_store` instead (see SKILL.md §3). Backs the file up first.
Always leave the page crawlable (do NOT robots.txt-disallow it) or Google can't see the noindex.
"""
import os, sys, argparse, subprocess, datetime
from pathlib import Path
import requests

KB = Path.home()/".claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"

def kb_get(key):
    return subprocess.run(["uv","run",str(KB),"secret-get",key],capture_output=True,text=True).stdout.strip()

def resolve(prefix):
    csv = kb_get("SHOPIFY_STORES_CSV")
    for line in csv.splitlines():
        p = line.split(",")
        if len(p) >= 3 and p[0].strip() == prefix:
            return p[1].strip(), p[2].strip()
    sys.exit(f"prefix {prefix} negăsit în SHOPIFY_STORES_CSV")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", required=True, help="store prefix in stores.csv (e.g. BON, EST)")
    ap.add_argument("--path", required=True, help="path to noindex, e.g. /policies/contact-information")
    ap.add_argument("--remove", action="store_true", help="remove the snippet instead of adding it")
    ap.add_argument("--apply", action="store_true", help="actually PUT (default: dry-run)")
    a = ap.parse_args()
    shop, token = resolve(a.prefix)
    api = kb_get("SHOPIFY_API_VERSION") or "2026-01"
    H = {"X-Shopify-Access-Token": token}
    base = f"https://{shop}/admin/api/{api}"
    print(f"shop {shop} | api {api} | path {a.path}")

    themes = requests.get(f"{base}/themes.json", headers=H, timeout=30).json().get("themes", [])
    main_t = next((t for t in themes if t.get("role") == "main"), None)
    if not main_t: sys.exit("nicio temă main")
    tid = main_t["id"]; print(f"main theme: {tid} ({main_t.get('name')})")

    val = requests.get(f"{base}/themes/{tid}/assets.json", headers=H,
                       params={"asset[key]": "layout/theme.liquid"}, timeout=30).json()["asset"]["value"]
    snip = ("{%- if request.path contains '" + a.path +
            "' -%}<meta name=\"robots\" content=\"noindex, nofollow\">{%- endif -%}")
    present = snip in val

    if a.remove:
        if not present: print("snippet absent — nimic de scos"); return
        new = val.replace("\n    " + snip, "").replace(snip, "")
        action = "REMOVE"
    else:
        if present: print("✓ deja prezent (idempotent) — nimic de făcut"); return
        i = val.lower().find("<head>")
        if i == -1: sys.exit("no <head> în theme.liquid")
        ins = i + len("<head>")
        new = val[:ins] + "\n    " + snip + val[ins:]
        action = "ADD"

    print(f"{action}: {snip}")
    if not a.apply:
        print("DRY-RUN — rulează cu --apply"); return
    bak = Path.cwd()/f"theme.liquid.{a.prefix}.{datetime.datetime.now():%Y%m%d-%H%M%S}.bak"
    bak.write_text(val); print(f"backup: {bak}")
    r = requests.put(f"{base}/themes/{tid}/assets.json", headers={**H, "Content-Type": "application/json"},
                     json={"asset": {"key": "layout/theme.liquid", "value": new}}, timeout=40)
    print("PUT:", r.status_code, "✓" if r.status_code in (200, 201) else r.text[:300])
    if r.status_code in (200, 201):
        print(f"verifică: curl -s 'https://{shop.replace('.myshopify.com','')}…{a.path}' | grep robots")

if __name__ == "__main__":
    main()
