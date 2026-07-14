#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["requests"]
# ///
"""bi.py — operate the BI Grandia app (bi.grandia.ro — repo contact546/grandia-inventory) from the CLI.

An internal ERP/BI platform for Grandia.ro: Shopify sync, inventory, products, RMA/returns
(+ DPD AWB + refunds), purchase orders (+ TOM ERP), pricing engine, AI catalog-quality,
GA4/Google-Ads/Meta-Ads syncs, forecasts, dev-requests, team-tasks, users. 190 HTTP routes,
ZERO server actions → the CLI can do everything the UI can, over a `grandia_session` cookie.

  bi.py routes                                   # the route map (read first)
  bi.py get /api/admin/purchase-orders
  bi.py po approve <id>                           # DRY-RUN via preview-approve (real API preview!)
  bi.py po approve <id> --yes                     # execute (writes incoming inventory to Shopify)
  bi.py rma approve <id> --service <sid> --weight 2 --yes
  bi.py sync incremental --yes
  bi.py call POST /api/admin/... --json '{...}' --yes
  bi.py sql "select status,count(*) from purchase_orders group by 1"   # read-only

Safety: any non-GET is DRY-RUN unless --yes. PO/reception dry-runs use the app's own preview-* actions.
Cookie/secret are never printed.
"""
import argparse, json, os, subprocess, sys, urllib.parse as up
from pathlib import Path

try:
    import requests
    requests.packages.urllib3.disable_warnings()
except ImportError:
    sys.exit("run me with:  uv run --no-project --with requests bi.py ...")

BASE = os.environ.get("BIGRANDIA_BASE", "https://bi.grandia.ro")
STATE = Path(os.path.expanduser("~/.config/arona-bi-grandia"))
COOKIE_FILE = STATE / "cookie"
COOKIE_NAME = "grandia_session"
EMAIL_KEY, PASS_KEY = "BIGRANDIA_EMAIL", "BIGRANDIA_PASSWORD"
DB_SECRET = "DATABASE_URL_GRANDIA"

def kb_path():
    for p in [Path.home()/".claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py",
              Path(__file__).resolve().parents[4]/"core/scripts/kb.py"]:
        if p.exists(): return str(p)
    return None

def secret(key):
    kb = kb_path()
    if not kb: return os.environ.get(key)
    r = subprocess.run(["uv", "run", kb, "secret-get", key], capture_output=True, text=True)
    return r.stdout.strip() or os.environ.get(key)

def load_cookie():
    return COOKIE_FILE.read_text().strip() if COOKIE_FILE.exists() else None

def save_cookie(tok):
    STATE.mkdir(parents=True, exist_ok=True)
    COOKIE_FILE.write_text(tok); os.chmod(COOKIE_FILE, 0o600)

def login():
    email, pw = secret(EMAIL_KEY), secret(PASS_KEY)
    if not email or not pw:
        sys.exit(f"No creds. Set them in the team KB: kb.py secret-set {EMAIL_KEY} … / {PASS_KEY} …")
    r = requests.post(f"{BASE}/api/auth/login",
                      json={"email": email, "password": pw, "rememberMe": True}, timeout=30, verify=False)
    if not r.ok: sys.exit(f"login failed: HTTP {r.status_code} {r.text[:200]}")
    tok = r.cookies.get(COOKIE_NAME)
    if not tok: sys.exit("login ok but no session cookie returned")
    save_cookie(tok); return tok

def req(method, path, body=None, query=None, _retry=True):
    tok = load_cookie() or login()
    url = BASE + path
    if query: url += ("&" if "?" in url else "?") + up.urlencode(query)
    r = requests.request(method, url, cookies={COOKIE_NAME: tok}, json=body, timeout=180, verify=False)
    if r.status_code == 401 and _retry:
        login(); return req(method, path, body, query, _retry=False)
    return r

def show(r):
    print(f"HTTP {r.status_code}")
    ct = r.headers.get("content-type", "")
    if "application/json" in ct: print(json.dumps(r.json(), indent=2, ensure_ascii=False)[:8000])
    else: print(r.text[:2000])
    sys.exit(0 if r.ok else 1)

def mutate(method, path, body=None, query=None, yes=False, preview=None):
    if not yes:
        # If the app exposes a real preview action, call it — a live dry-run.
        if preview is not None:
            print(f"DRY-RUN (live preview) {method} {path}")
            r = req(method, path, preview, query)
            print(f"  preview HTTP {r.status_code}")
            try: print("  " + json.dumps(r.json(), ensure_ascii=False)[:1500])
            except Exception: print("  " + r.text[:800])
            print("  → re-run with --yes to execute for real.")
            return
        print(f"DRY-RUN {method} {path}")
        if query: print("  query:", query)
        if body is not None: print("  body:", json.dumps(body, ensure_ascii=False))
        print("  → add --yes to execute.")
        return
    show(req(method, path, body, query))

def sql(query):
    import importlib
    try: psycopg2 = importlib.import_module("psycopg2")
    except ImportError: sys.exit("run with:  uv run --no-project --with requests --with psycopg2-binary bi.py sql ...")
    dsn = secret(DB_SECRET); p = up.urlsplit(dsn); dsn = up.urlunsplit((p.scheme, p.netloc, p.path, "", ""))
    cn = psycopg2.connect(dsn); cn.set_session(readonly=True); c = cn.cursor()
    c.execute(query)
    cols = [d[0] for d in c.description] if c.description else []
    print(" | ".join(cols))
    for row in c.fetchall(): print(" | ".join(str(x)[:60] for x in row))

ROUTES = """\
PURCHASE ORDERS (+ TOM ERP)                              [authenticated]
  GET/POST /api/admin/purchase-orders     POST {locationId, items:[{variantId,quantityOrdered,unitCost}], ...}
  PATCH /api/admin/purchase-orders/{id}   {action: approve|cancel|complete|preview-approve|preview-cancel}
        approve/cancel WRITE incoming inventory to Shopify. (field-edit: no action, {orderDate?,items?,...})
  POST  /api/admin/purchase-orders/auto-generate           build a PO from restock needs
  POST  /api/admin/purchase-orders/{id}/send-to-tom | amend-tom | refresh-from-tom
  GET/POST /api/admin/purchase-orders/receptions           POST {locationId, items:[{variantId,quantityReceived}]}
  PATCH /api/admin/purchase-orders/receptions/{id}         {action: complete|allocate|preview-complete}
RETURNS / RMA "tickets"                                  [authenticated]
  POST  /api/admin/returns/requests/{id}/approve           {generateAwb?,serviceId,totalWeight,parcelsCount?}
  POST  /api/admin/returns/requests/{id}/generate-awb      issue DPD AWB
  POST  /api/admin/returns/requests/{id}/awb/{awbId}/cancel
  POST  /api/admin/returns/requests/{id}/actions           {action: deliver|close}
  POST  /api/admin/returns/requests/{id}/cancel            {reason}
  PATCH /api/admin/returns/requests/{id}/refund-amount|bank-details|pickup-address|invoice-number
  POST  /api/admin/returns/requests/{id}/send-to-payment | mark-paid {amount} | refund-shopify (REAL refund)
  GET/POST /api/admin/returns/requests/bulk-pay
SYNC / JOBS
  POST  /api/admin/actions                {action: bootstrap|incremental|snapshot|fulfillments}
  POST  /api/admin/scheduler              {action:"trigger", jobId}   (12 jobs: incremental-sync, ga4-daily-sync, …)
  POST  /api/admin/{fbads,gads,ga4}/sync  {action:"sync-yesterday"|"sync-date"|"sync-range", date?/startDate?}
  POST  /api/admin/{fbads,gads,ga4}/refresh-views
COURIER (DPD)
  POST  /api/admin/courier/awb            {orderId, type:"RETURN"|"SWAP", serviceId, ...}   DELETE ?id=
PRICING / CATALOG-QUALITY / IMAGES (all write to Shopify)
  POST  /api/admin/pricing/{productId}/apply       {newPrice, userId}
  POST  /api/admin/pricing/run-pipeline
  POST  /api/admin/catalog-quality/audit|improve|push-improvements|generate-images|push-images
  POST  /api/admin/image-optimization/analyze|optimize|alt-text   {productId | imageId}
PRODUCTS / REPORTS (reads)
  GET   /api/admin/products/reports · /overview · /funnel-overview · /search?q=
  GET   /api/admin/reports/marketing-performance · category-roi · dead-stock · slow-movers
FORECASTS / DEV-REQUESTS / TEAM-TASKS / USERS / SETTINGS  (see reference/routes.md)
NOTE: ~130 of 190 routes have NO auth in the handler (middleware waves /api/admin/* through).
  This CLI still logs in so the ~60 session-guarded routes (PO, RMA, forecasts, users, dev/tasks) work."""

def main():
    ap = argparse.ArgumentParser(description="Operate bi.grandia.ro from the CLI.")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("routes"); sub.add_parser("login")
    g = sub.add_parser("get"); g.add_argument("path"); g.add_argument("--query", nargs="*", default=[])
    c = sub.add_parser("call"); c.add_argument("method"); c.add_argument("path")
    c.add_argument("--json", dest="body"); c.add_argument("--query", nargs="*", default=[]); c.add_argument("--yes", action="store_true")
    # PO
    po = sub.add_parser("po"); po.add_argument("op", choices=["approve","cancel","complete"]); po.add_argument("id"); po.add_argument("--yes", action="store_true")
    # reception
    rc = sub.add_parser("reception"); rc.add_argument("op", choices=["complete","allocate"]); rc.add_argument("id"); rc.add_argument("--yes", action="store_true")
    # RMA
    rm = sub.add_parser("rma"); rm.add_argument("op", choices=["approve","awb","deliver","close","cancel","send-to-payment","mark-paid","refund-shopify"]); rm.add_argument("id")
    rm.add_argument("--service"); rm.add_argument("--weight", type=float); rm.add_argument("--parcels", type=int); rm.add_argument("--amount", type=float); rm.add_argument("--reason"); rm.add_argument("--yes", action="store_true")
    # sync
    sy = sub.add_parser("sync"); sy.add_argument("action", choices=["bootstrap","incremental","snapshot","fulfillments"]); sy.add_argument("--yes", action="store_true")
    jb = sub.add_parser("job"); jb.add_argument("jobId"); jb.add_argument("--yes", action="store_true")
    sq = sub.add_parser("sql"); sq.add_argument("query")

    a = ap.parse_args()
    def q(lst): return dict(x.split("=", 1) for x in lst) if lst else None

    if a.cmd == "routes": print(ROUTES)
    elif a.cmd == "login": login(); print("logged in, cookie cached.")
    elif a.cmd == "get": show(req("GET", a.path, query=q(a.query)))
    elif a.cmd == "call":
        body = json.loads(a.body) if a.body else None
        if a.method.upper() == "GET": show(req("GET", a.path, query=q(a.query)))
        else: mutate(a.method.upper(), a.path, body, q(a.query), a.yes)
    elif a.cmd == "po":
        path = f"/api/admin/purchase-orders/{a.id}"
        mutate("PATCH", path, {"action": a.op}, yes=a.yes,
               preview={"action": f"preview-{a.op}"} if a.op in ("approve","cancel") else None)
    elif a.cmd == "reception":
        path = f"/api/admin/purchase-orders/receptions/{a.id}"
        mutate("PATCH", path, {"action": a.op}, yes=a.yes,
               preview={"action": "preview-complete"} if a.op == "complete" else None)
    elif a.cmd == "rma":
        p = f"/api/admin/returns/requests/{a.id}"
        if a.op == "approve":
            body = {"generateAwb": bool(a.service)}
            if a.service: body["serviceId"] = a.service
            if a.weight: body["totalWeight"] = a.weight
            if a.parcels: body["parcelsCount"] = a.parcels
            mutate("POST", p+"/approve", body, yes=a.yes)
        elif a.op == "awb":
            body = {}
            if a.service: body["serviceId"] = a.service
            if a.weight: body["totalWeight"] = a.weight
            mutate("POST", p+"/generate-awb", body, yes=a.yes)
        elif a.op in ("deliver","close"): mutate("POST", p+"/actions", {"action": a.op}, yes=a.yes)
        elif a.op == "cancel": mutate("POST", p+"/cancel", {"reason": a.reason or "manual"}, yes=a.yes)
        elif a.op == "send-to-payment": mutate("POST", p+"/send-to-payment", {}, yes=a.yes)
        elif a.op == "mark-paid": mutate("POST", p+"/mark-paid", {"amount": a.amount}, yes=a.yes)
        elif a.op == "refund-shopify": mutate("POST", p+"/refund-shopify", {}, yes=a.yes)
    elif a.cmd == "sync": mutate("POST", "/api/admin/actions", {"action": a.action}, yes=a.yes)
    elif a.cmd == "job": mutate("POST", "/api/admin/scheduler", {"action": "trigger", "jobId": a.jobId}, yes=a.yes)
    elif a.cmd == "sql": sql(a.query)
    else: ap.print_help()

if __name__ == "__main__":
    main()
