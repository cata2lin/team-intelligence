#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["requests"]
# ///
"""metrics.py — operate the team Marketing-metrics app (metrics.arona.ro) from the CLI.

The app is a Next.js panel with a complete HTTP API (47 routes, ZERO server actions),
guarded by a `metrics_session` cookie. This CLI logs in once (creds from the team KB),
caches the cookie, and drives every route. Reads are free; MUTATIONS need --yes.

  metrics.py routes                     # list every known route (the map)
  metrics.py get /api/brands            # any GET
  metrics.py call POST /api/brands/<id>/sync --json '{"entities":["orders"]}' --yes
  metrics.py brands                     # convenience read
  metrics.py sync-google --account <cuid> --days 7 --yes
  metrics.py map-google add --brand <id> --account <cuid> --yes
  metrics.py sql "select slug from \\"Brand\\" limit 5"   # read-only, via postgres

Safety: any non-GET is DRY-RUN unless --yes. Cookie/secret are never printed.
"""
import argparse, json, os, subprocess, sys, urllib.parse as up
from pathlib import Path

try:
    import requests
    requests.packages.urllib3.disable_warnings()
except ImportError:
    sys.exit("run me with:  uv run --no-project --with requests metrics.py ...")

BASE = os.environ.get("METRICS_BASE", "https://metrics.arona.ro")
STATE = Path(os.path.expanduser("~/.config/arona-metrics"))
COOKIE_FILE = STATE / "cookie"
COOKIE_NAME = "metrics_session"
EMAIL_KEY, PASS_KEY = "METRICS_ADMIN_EMAIL", "METRICS_ADMIN_PASSWORD"
DB_SECRET = "DATABASE_URL_METRICS"

# ── team KB secret access (never prints the value) ──────────────────────
def kb_path():
    for p in [Path.home()/".claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py",
              Path(__file__).resolve().parents[4]/"core/scripts/kb.py"]:
        if p.exists():
            return str(p)
    return None

def secret(key):
    kb = kb_path()
    if not kb:
        return os.environ.get(key)
    r = subprocess.run(["uv", "run", kb, "secret-get", key], capture_output=True, text=True)
    return r.stdout.strip() or os.environ.get(key)

# ── session ─────────────────────────────────────────────────────────────
def load_cookie():
    return COOKIE_FILE.read_text().strip() if COOKIE_FILE.exists() else None

def save_cookie(tok):
    STATE.mkdir(parents=True, exist_ok=True)
    COOKIE_FILE.write_text(tok)
    os.chmod(COOKIE_FILE, 0o600)

def login():
    email, pw = secret(EMAIL_KEY), secret(PASS_KEY)
    if not email or not pw:
        sys.exit(f"No creds. Set them in the team KB: kb.py secret-set {EMAIL_KEY} … / {PASS_KEY} …")
    r = requests.post(f"{BASE}/api/auth/login",
                      json={"email": email, "password": pw, "rememberMe": True},
                      timeout=30, verify=False)
    if not r.ok:
        sys.exit(f"login failed: HTTP {r.status_code} {r.text[:200]}")
    tok = r.cookies.get(COOKIE_NAME)
    if not tok:
        sys.exit("login ok but no session cookie returned")
    save_cookie(tok)
    return tok

def req(method, path, body=None, query=None, _retry=True):
    tok = load_cookie() or login()
    url = BASE + path
    if query:
        url += ("&" if "?" in url else "?") + up.urlencode(query)
    r = requests.request(method, url, cookies={COOKIE_NAME: tok},
                         json=body, timeout=120, verify=False)
    if r.status_code == 401 and _retry:
        login()
        return req(method, path, body, query, _retry=False)
    return r

def show(r):
    print(f"HTTP {r.status_code}")
    ct = r.headers.get("content-type", "")
    if "application/json" in ct:
        print(json.dumps(r.json(), indent=2, ensure_ascii=False)[:8000])
    else:
        print(r.text[:2000])
    sys.exit(0 if r.ok else 1)

def mutate(method, path, body=None, query=None, yes=False):
    if not yes:
        print(f"DRY-RUN {method} {path}")
        if query: print("  query:", query)
        if body is not None: print("  body:", json.dumps(body, ensure_ascii=False))
        print("  → add --yes to execute.")
        return
    show(req(method, path, body, query))

# ── read via postgres (read-only) ───────────────────────────────────────
def sql(query):
    import importlib
    try: psycopg2 = importlib.import_module("psycopg2")
    except ImportError: sys.exit("run with:  uv run --no-project --with requests --with psycopg2-binary metrics.py sql ...")
    dsn = secret(DB_SECRET)
    p = up.urlsplit(dsn); dsn = up.urlunsplit((p.scheme, p.netloc, p.path, "", ""))
    cn = psycopg2.connect(dsn); cn.set_session(readonly=True); c = cn.cursor()
    c.execute(query)
    cols = [d[0] for d in c.description] if c.description else []
    print(" | ".join(cols))
    for row in c.fetchall():
        print(" | ".join(str(x)[:60] for x in row))

ROUTES = """\
AD-PLATFORM SYNC (mutations — highest value)
  POST /api/meta/sync              {adAccountId?, daysBack?}   run Meta insights sync NOW
  POST /api/tiktok/sync            {adAccountId?, daysBack?}   run TikTok sync NOW
  POST /api/google-ads/sync        {customerAccountId?, daysBack?}  run Google Ads sync NOW
  POST /api/google-ads/discover    {}                          MCC → upsert child accounts
  POST /api/brands/{id}/sync       {entities?,bootstrap?,bootstrapDays?}  Shopify sync (async, Inngest)
  POST /api/tiktok/oauth/refresh   {tokenId, rediscover?}      refresh a TikTok token
  PATCH /api/meta/tokens/{id}      {action:verify|rediscover-accounts|toggle-active|update-label}
BRAND↔AD-ACCOUNT MAPPING (attribution — the important writes)
  GET/POST/PATCH/DELETE /api/brands/{id}/meta-accounts        {adAccountId, campaignFilter?}
  GET/POST/PATCH/DELETE /api/brands/{id}/tiktok-accounts      {adAccountId, campaignFilter?}
  GET/POST/PATCH/DELETE /api/brands/{id}/google-ads-accounts  {customerAccountId, campaignFilter?}
BRANDS / STORES
  GET/POST /api/brands             POST {name, slug, notes?}
  GET/PATCH/DELETE /api/brands/{id}   PATCH {name?,isActive?,isPaused?,color?,logoUrl?,categoryId?}
  POST/DELETE /api/brands/{id}/connect-shopify  {domain,clientId,clientSecret,apiVersion?}
  GET/POST /api/stores · GET/PATCH/DELETE /api/stores/{id}
CONNECTIONS / TOKENS
  GET/POST /api/meta/tokens        POST {label, accessToken}
  GET/DELETE /api/tiktok/tokens (?id=)  · POST /api/tiktok/discover-bcs {tokenId}
  GET/PUT/DELETE /api/google-ads/connection   PUT {label,developerToken,loginCustomerId,...}
READS
  GET /api/brands · /api/categories · /api/stores · /api/meta/tokens
  GET /api/google-ads/accounts-list · /api/tiktok/yesterday-spend
  GET /api/integrations/shopify/export?entity=&brandId=   (CSV, ≤10k rows)
ID GOTCHA: link/sync routes want the INTERNAL cuid (MetaAdAccount.id / TikTokAdAccount.id /
  GoogleAdsCustomerAccount.id), NOT the platform id. Resolve via GET .../{meta,tiktok,google-ads}-accounts.
NOTE: /api/{meta,tiktok,google-ads}/sync run SYNCHRONOUSLY in-request — big backfills time out.
  Loop per-account with a modest --days, or run locally via the repo's scripts/*.ts."""

def main():
    ap = argparse.ArgumentParser(description="Operate metrics.arona.ro from the CLI.")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("routes")
    sub.add_parser("login")
    g = sub.add_parser("get");  g.add_argument("path"); g.add_argument("--query", nargs="*", default=[])
    c = sub.add_parser("call")
    c.add_argument("method"); c.add_argument("path")
    c.add_argument("--json", dest="body"); c.add_argument("--query", nargs="*", default=[])
    c.add_argument("--yes", action="store_true")
    sub.add_parser("brands")
    for name, path, idflag, acctflag in [("sync-meta","/api/meta/sync","","--account"),
                                          ("sync-tiktok","/api/tiktok/sync","","--account"),
                                          ("sync-google","/api/google-ads/sync","","--account")]:
        s = sub.add_parser(name); s.add_argument("--account"); s.add_argument("--days", type=int); s.add_argument("--yes", action="store_true")
    s = sub.add_parser("discover-google"); s.add_argument("--yes", action="store_true")
    for verb in ("map-meta","map-tiktok","map-google"):
        s = sub.add_parser(verb); s.add_argument("op", choices=["add","remove"])
        s.add_argument("--brand", required=True); s.add_argument("--account", required=True)
        s.add_argument("--filter"); s.add_argument("--yes", action="store_true")
    s = sub.add_parser("brand-pause"); s.add_argument("--brand", required=True); s.add_argument("--yes", action="store_true")
    s = sub.add_parser("brand-unpause"); s.add_argument("--brand", required=True); s.add_argument("--yes", action="store_true")
    s = sub.add_parser("sql"); s.add_argument("query")

    a = ap.parse_args()
    def q(lst): return dict(x.split("=", 1) for x in lst) if lst else None

    if a.cmd == "routes": print(ROUTES)
    elif a.cmd == "login": login(); print("logged in, cookie cached.")
    elif a.cmd == "get": show(req("GET", a.path, query=q(a.query)))
    elif a.cmd == "call":
        body = json.loads(a.body) if a.body else None
        if a.method.upper() == "GET": show(req("GET", a.path, query=q(a.query)))
        else: mutate(a.method.upper(), a.path, body, q(a.query), a.yes)
    elif a.cmd == "brands": show(req("GET", "/api/brands"))
    elif a.cmd in ("sync-meta","sync-tiktok","sync-google"):
        key = {"sync-meta":"adAccountId","sync-tiktok":"adAccountId","sync-google":"customerAccountId"}[a.cmd]
        path = {"sync-meta":"/api/meta/sync","sync-tiktok":"/api/tiktok/sync","sync-google":"/api/google-ads/sync"}[a.cmd]
        body = {}
        if a.account: body[key] = a.account
        if a.days: body["daysBack"] = a.days
        mutate("POST", path, body, yes=a.yes)
    elif a.cmd == "discover-google": mutate("POST", "/api/google-ads/discover", {}, yes=a.yes)
    elif a.cmd in ("map-meta","map-tiktok","map-google"):
        path = f"/api/brands/{a.brand}/{ {'map-meta':'meta','map-tiktok':'tiktok','map-google':'google-ads'}[a.cmd] }-accounts"
        idk = "customerAccountId" if a.cmd == "map-google" else "adAccountId"
        if a.op == "add":
            body = {idk: a.account};
            if a.filter is not None: body["campaignFilter"] = a.filter
            mutate("POST", path, body, yes=a.yes)
        else:
            mutate("DELETE", path, {idk: a.account}, yes=a.yes)
    elif a.cmd == "brand-pause":   mutate("PATCH", f"/api/brands/{a.brand}", {"isPaused": True}, yes=a.yes)
    elif a.cmd == "brand-unpause": mutate("PATCH", f"/api/brands/{a.brand}", {"isPaused": False}, yes=a.yes)
    elif a.cmd == "sql": sql(a.query)
    else: ap.print_help()

if __name__ == "__main__":
    main()
