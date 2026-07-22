#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["requests", "psycopg2-binary"]
# ///
"""tom.py — operate TOM (tom.arona.ro — repo contact546/tom), ARONA's purchase-order /
inbound-container tracker for the Guangzhou sourcing team.

TOM has TWO surfaces:
  • a signed HMAC API (`/api/v1`) that source apps use to push/amend/cancel POs — this CLI
    speaks it (keys from the team KB), so you can create/amend/cancel a PO AS a source app.
  • an internal line lifecycle (order→receive→ship, shipments) that lives ONLY in Next.js
    server actions — not reachable as a stable API. Those are Guangzhou-side and stay in the UI.

Reads go straight to the DB (read-only). One thing to always surface: a line can be CANCELLED
in TOM ("Use of tables in multiple sizes") yet still PRODUCED and shipped — TOM is a LOWER BOUND
on container contents, never the packing list (that's the KDocs file; see gigi:inbound-containers).

  tom.py pos [--source GRANDIA] [--status NEW]      # list POs (read)
  tom.py po TOM-039                                  # PO detail: lines, statuses, cancelNote
  tom.py ghost                                       # cancelled-but-maybe-produced lines (the trap)
  tom.py shipments · tom.py product GD-IL-6658 · tom.py events <lineItemId>
  tom.py po-get GRANDIA <sourcePoId>                 # read a PO via the signed API (proves signing)
  tom.py po-create GRANDIA --json '{...}' --yes      # create a PO as a source app (HMAC)
  tom.py po-cancel GRANDIA <sourcePoId> --scope ITEMS --lines l1,l2 --reason OUT_OF_STOCK --yes
  tom.py sql "select status,count(*) from purchase_order_items group by 1"

Safety: HMAC writes are DRY-RUN unless --yes. Reads are read-only. Secrets never printed.
"""
import argparse, hashlib, hmac, json, os, subprocess, sys, time, urllib.parse as up, uuid
from pathlib import Path
try:
    import requests, psycopg2
    requests.packages.urllib3.disable_warnings()
except ImportError:
    sys.exit("run me with:  uv run --no-project --with requests --with psycopg2-binary tom.py ...")

BASE = os.environ.get("TOM_BASE", "https://tom.arona.ro")
DB_SECRET = "DATABASE_URL_TOM"
SIG_HEADER = ("X-Tom-Key", "X-Tom-Timestamp", "X-Tom-Signature")
# source app code → KB key prefix
SRC_KEY = {"GRANDIA":"GRANDIA","SCENTUM":"PERFUME","PERFUME":"PERFUME",
           "ARONA-BI":"ARONA_BI","ARONA_BI":"ARONA_BI","VIGO":"VIGO"}

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

# ── DB (read-only) ──────────────────────────────────────────────────────
def db():
    dsn = secret(DB_SECRET); p = up.urlsplit(dsn)
    dsn = up.urlunsplit((p.scheme, p.netloc, p.path, "", ""))
    cn = psycopg2.connect(dsn); cn.set_session(readonly=True); return cn

def rows(cur):
    cols = [d[0] for d in cur.description]
    print(" | ".join(cols))
    for r in cur.fetchall(): print(" | ".join(str(x)[:52] for x in r))

def q_pos(source, status):
    cn = db(); c = cn.cursor()
    where, args = [], []
    if source: where.append('sa.code=%s'); args.append(source.upper())
    if status: where.append('po.status=%s'); args.append(status.upper())
    w = ("where " + " and ".join(where)) if where else ""
    c.execute(f"""select po."tomNumber", sa.code as source, po.type, po.status,
                  (select count(*) from purchase_order_items i where i."poId"=po.id) as lines,
                  po."createdAt"::date
                  from purchase_orders po join source_apps sa on sa.id=po."sourceAppId"
                  {w} order by po."createdAt" desc limit 60""", args)
    rows(c)

def q_po(tomnum):
    cn = db(); c = cn.cursor()
    c.execute("""select po.id, po."tomNumber", sa.code, po.type, po.status, po."shippingMode"
                 from purchase_orders po join source_apps sa on sa.id=po."sourceAppId"
                 where po."tomNumber"=%s""", (tomnum,))
    h = c.fetchone()
    if not h: sys.exit(f"no PO {tomnum}")
    print(f"{h[1]}  source={h[2]}  type={h[3]}  status={h[4]}  mode={h[5]}\n")
    c.execute("""select i."sourceLineId", left(i."externalTitle",40) as title, i.status,
                 i."orderedQty", i."receivedQty", i."shippedQty", i."cancelReason", i."cancelNote",
                 (sl.id is not null) as on_shipment
                 from purchase_order_items i left join shipment_lines sl on sl."poItemId"=i.id
                 where i."poId"=%s order by i."createdAt" """, (h[0],))
    rows(c)

def q_ghost():
    cn = db(); c = cn.cursor()
    print("Lines CANCELLED with a note — may have been PRODUCED anyway (TOM ≠ packing list):\n")
    c.execute("""select po."tomNumber", sa.code, i."cancelNote", count(*) as lines
                 from purchase_order_items i
                 join purchase_orders po on po.id=i."poId"
                 join source_apps sa on sa.id=po."sourceAppId"
                 where i.status='CANCELLED' and i."cancelNote" is not null and i."cancelNote"<>''
                 group by 1,2,3 order by 4 desc limit 40""")
    rows(c)

def q_shipments():
    cn = db(); c = cn.cursor()
    c.execute("""select s.code, s.name, s.status, s.carrier,
                 (select count(*) from shipment_lines sl where sl."shipmentId"=s.id) as lines
                 from shipments s order by s."createdAt" desc limit 40""")
    rows(c)

def q_product(term):
    cn = db(); c = cn.cursor()
    c.execute("""select sku, barcode, left(title,44) as title, "supplierName",
                 "lastPriceUsd", "awbUid" from products
                 where sku ilike %s or barcode ilike %s or title ilike %s limit 20""",
              (f"%{term}%", f"%{term}%", f"%{term}%"))
    rows(c)

def q_events(lineid):
    cn = db(); c = cn.cursor()
    c.execute("""select e."createdAt", e."eventType", e."fromStatus", e."toStatus", e."actorType"
                 from po_item_events e
                 join purchase_order_items i on i.id=e."poItemId"
                 where i."sourceLineId"=%s or i.id=%s order by e."createdAt" """, (lineid, lineid))
    rows(c)

def sql(query):
    cn = db(); c = cn.cursor(); c.execute(query); rows(c)

# ── HMAC /api/v1 (writes as a source app) ───────────────────────────────
def load_body(arg):
    """--json acceptă și `@fisier` — un PO de 100+ linii nu încape ca argument de shell."""
    if arg.startswith("@"):
        return json.loads(Path(arg[1:]).read_text(encoding="utf-8"))
    return json.loads(arg)


def signed(method, path, body_obj=None, source=None, yes=False):
    k = SRC_KEY.get(source.upper()) if source else None
    if not k: sys.exit(f"unknown source '{source}'. Known: {', '.join(sorted(set(SRC_KEY)))}")
    key_id, sec = secret(f"TOM_{k}_KEY_ID"), secret(f"TOM_{k}_SECRET")
    if not key_id or not sec: sys.exit(f"no HMAC key for {source} in KB (TOM_{k}_KEY_ID / _SECRET)")
    body = json.dumps(body_obj, separators=(",", ":")) if body_obj is not None else ""
    if method != "GET" and not yes:
        print(f"DRY-RUN {method} {path}  (as {source})")
        if body: print("  body:", body[:1500])
        print("  → add --yes to sign & send.")
        return
    ts = str(int(time.time()))
    canonical = "\n".join([method.upper(), path, ts, hashlib.sha256(body.encode()).hexdigest()])
    sig = hmac.new(sec.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    headers = {SIG_HEADER[0]: key_id, SIG_HEADER[1]: ts, SIG_HEADER[2]: sig,
               "Content-Type": "application/json"}
    if method != "GET": headers["Idempotency-Key"] = str(uuid.uuid4())
    r = requests.request(method, BASE + path, data=body.encode() if body else None,
                         headers=headers, timeout=60, verify=False)
    print(f"HTTP {r.status_code}")
    try: print(json.dumps(r.json(), indent=2, ensure_ascii=False)[:4000])
    except Exception: print(r.text[:2000])
    sys.exit(0 if r.ok else 1)

def main():
    ap = argparse.ArgumentParser(description="Operate tom.arona.ro from the CLI.")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("pos"); s.add_argument("--source"); s.add_argument("--status")
    s = sub.add_parser("po"); s.add_argument("tomNumber")
    sub.add_parser("ghost"); sub.add_parser("shipments")
    s = sub.add_parser("product"); s.add_argument("term")
    s = sub.add_parser("events"); s.add_argument("lineId")
    s = sub.add_parser("sql"); s.add_argument("query")
    # HMAC
    s = sub.add_parser("po-get"); s.add_argument("source"); s.add_argument("sourcePoId")
    s = sub.add_parser("po-create"); s.add_argument("source"); s.add_argument("--json", dest="body", required=True); s.add_argument("--yes", action="store_true")
    s = sub.add_parser("po-amend"); s.add_argument("source"); s.add_argument("sourcePoId"); s.add_argument("--json", dest="body", required=True); s.add_argument("--yes", action="store_true")
    s = sub.add_parser("po-cancel"); s.add_argument("source"); s.add_argument("sourcePoId")
    s.add_argument("--scope", choices=["PO","ITEMS"], default="PO"); s.add_argument("--lines"); s.add_argument("--reason", default="OTHER"); s.add_argument("--note"); s.add_argument("--yes", action="store_true")
    s = sub.add_parser("product-upsert"); s.add_argument("source"); s.add_argument("--json", dest="body", required=True); s.add_argument("--yes", action="store_true")

    a = ap.parse_args()
    if a.cmd == "pos": q_pos(a.source, a.status)
    elif a.cmd == "po": q_po(a.tomNumber)
    elif a.cmd == "ghost": q_ghost()
    elif a.cmd == "shipments": q_shipments()
    elif a.cmd == "product": q_product(a.term)
    elif a.cmd == "events": q_events(a.lineId)
    elif a.cmd == "sql": sql(a.query)
    elif a.cmd == "po-get":
        signed("GET", f"/api/v1/po/{a.source.upper()}/{a.sourcePoId}", source=a.source)
    elif a.cmd == "po-create":
        signed("POST", "/api/v1/po", load_body(a.body), a.source, a.yes)
    elif a.cmd == "po-amend":
        signed("POST", f"/api/v1/po/{a.source.upper()}/{a.sourcePoId}/amend", load_body(a.body), a.source, a.yes)
    elif a.cmd == "po-cancel":
        body = {"scope": a.scope, "reason": a.reason}
        if a.note: body["note"] = a.note
        if a.scope == "ITEMS": body["source_line_ids"] = (a.lines or "").split(",")
        signed("POST", f"/api/v1/po/{a.source.upper()}/{a.sourcePoId}/cancel", body, a.source, a.yes)
    elif a.cmd == "product-upsert":
        signed("POST", "/api/v1/products/upsert", load_body(a.body), a.source, a.yes)
    else: ap.print_help()

if __name__ == "__main__":
    main()
