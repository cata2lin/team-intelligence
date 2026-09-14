# /// script
# requires-python = ">=3.10"
# dependencies = ["requests"]
# ///
"""
shopify_apps.py — acces read+write la magazinele Shopify servite de cele 2 app-uri
`client_credentials` (secret KB `SHOPIFY_READ_APPS`). Emite tokenul la cerere (~24h), rulează
query/mutation prin Admin GraphQL. NU printează niciodată secretul sau tokenul întreg.

  uv run shopify_apps.py stores                       # ce magazine + care app + scope
  uv run shopify_apps.py token  --store oriceredus     # emite un token (arată doar …last4)
  uv run shopify_apps.py query  --store bonhaus-hu --gql '{ shop { name } }'
  uv run shopify_apps.py query  --store rossi --file q.graphql --vars '{"after":null}'
  uv run shopify_apps.py scopes --store casa-ofertelor # ce scopes are efectiv tokenul (read/write)
"""
import argparse, json, os, subprocess, sys
import requests

for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass

KB = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "core", "scripts", "kb.py")


def _config():
    """Secretul SHOPIFY_READ_APPS (env-first, altfel din KB). Nu se printează."""
    raw = os.environ.get("SHOPIFY_READ_APPS")
    if not raw:
        raw = subprocess.run(["uv", "run", os.path.abspath(KB), "secret-get", "SHOPIFY_READ_APPS"],
                             capture_output=True, text=True, check=True).stdout.strip()
    return json.loads(raw)


def _resolve(cfg, store):
    """store (nume scurt SAU domeniu myshopify) → (app, shop_domain)."""
    for app in cfg["apps"]:
        for name, dom in app["stores"].items():
            if store.lower() in (name.lower(), dom.lower()):
                return app, dom
    sys.exit(f"Magazin {store!r} negăsit. Rulează `stores` pt listă.")


def mint(app, shop):
    r = requests.post(f"https://{shop}/admin/oauth/access_token",
                      json={"client_id": app["client_id"], "client_secret": app["client_secret"],
                            "grant_type": "client_credentials"}, timeout=20)
    if r.status_code != 200:
        sys.exit(f"⛔ mint {r.status_code} pe {shop}: {r.text[:150]}")
    return r.json()["access_token"]


def gql(cfg, shop, token, query, variables=None):
    r = requests.post(f"https://{shop}/admin/api/{cfg['api_version']}/graphql.json",
                      headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
                      json={"query": query, "variables": variables or {}}, timeout=45)
    r.raise_for_status()
    out = r.json()
    if "errors" in out:
        sys.exit("⛔ GraphQL: " + json.dumps(out["errors"], ensure_ascii=False)[:400])
    return out["data"]


def cmd_stores(a):
    cfg = _config()
    for app in cfg["apps"]:
        print(f"■ app {app['name']} · {app.get('scope','?')} ({app.get('n_scopes','?')} scopes)")
        for name, dom in app["stores"].items():
            print(f"    {name:16} {dom}")
    if cfg.get("pending_install"):
        print("pending (app neinstalat):", ", ".join(cfg["pending_install"]))


def cmd_token(a):
    cfg = _config(); app, shop = _resolve(cfg, a.store)
    tok = mint(app, shop)
    print(f"{shop} (app {app['name']}): token …{tok[-4:]}  valabil ~24h")


def cmd_scopes(a):
    cfg = _config(); app, shop = _resolve(cfg, a.store)
    tok = mint(app, shop)
    sc = requests.get(f"https://{shop}/admin/oauth/access_scopes.json",
                      headers={"X-Shopify-Access-Token": tok}, timeout=20).json()
    h = [x["handle"] for x in sc.get("access_scopes", [])]
    w = [x for x in h if x.startswith("write_")]
    print(f"{shop}: {len(h)} scopes · {len(w)} write ({'READ+WRITE' if w else 'READ-ONLY'})")
    if a.verbose:
        print("  " + ", ".join(h))


def cmd_query(a):
    cfg = _config(); app, shop = _resolve(cfg, a.store)
    q = open(a.file, encoding="utf-8").read() if a.file else a.gql
    if not q:
        sys.exit("Dă --gql '<query>' sau --file q.graphql")
    variables = json.loads(a.vars) if a.vars else None
    tok = mint(app, shop)
    print(json.dumps(gql(cfg, shop, tok, q, variables), ensure_ascii=False, indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stores").set_defaults(fn=cmd_stores)
    for name in ("token", "scopes"):
        s = sub.add_parser(name); s.add_argument("--store", required=True)
        if name == "scopes": s.add_argument("--verbose", action="store_true")
        s.set_defaults(fn=cmd_token if name == "token" else cmd_scopes)
    s = sub.add_parser("query"); s.add_argument("--store", required=True)
    s.add_argument("--gql"); s.add_argument("--file"); s.add_argument("--vars")
    s.set_defaults(fn=cmd_query)
    a = ap.parse_args(); a.fn(a)


if __name__ == "__main__":
    main()
