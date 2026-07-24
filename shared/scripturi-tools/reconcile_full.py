# /// script
# requires-python = ">=3.10"
# dependencies = ["httpx>=0.27", "psycopg2-binary>=2.9"]
# ///
"""Reconciliere EXHAUSTIVA per comanda Shopify <-> AWBprint, Bonhaus CZ/PL/BG iunie 2026.
Fiecare comanda: status AWBprint (livrat/refuzat/intors/tranzit/anulat) × are AWB real × status Shopify."""
import os, sys, re, httpx, psycopg2
from collections import defaultdict
sys.path.insert(0, "/root/Scripturi")
from core.stores import list_stores

def clean(d): return re.sub(r"[?&]+(&|$)", r"\1", re.sub(r"([?&])(schema|channel_binding|pgbouncer|connection_limit)=[^&]*", r"\1", d)).rstrip("?&")
def norm(s): return re.sub(r"[^a-z0-9]", "", (s or "").lower())

awb = psycopg2.connect(clean(os.environ["DATABASE_URL_AWBPRINT"])); awb.set_session(readonly=True)
stores = {s["prefix"]: s for s in list_stores()}
STORE = {"CZ": "bonhaus.cz", "PL": "bonhaus.pl", "BONBG": "bonhaus.bg"}
SHIPPED_NODELIV = {"back_to_sender", "refused", "unsuccessful_delivery", "in_transit", "waiting_for_courier", "customer_pickup"}

for pfx, domain in STORE.items():
    st = stores.get(pfx)
    if not st: continue
    ac = awb.cursor()
    ac.execute("""SELECT o.order_number, o.tracking_number, o.aggregated_status, o.total_price
                  FROM orders o JOIN stores s ON s.uid=o.store_uid
                  WHERE s.name=%s AND o.frisbo_created_at>=%s AND o.frisbo_created_at<%s""",
               (domain, "2026-06-01", "2026-07-01"))
    A = {norm(n): {"awb": bool(t), "st": stt, "p": float(p or 0)} for n, t, stt, p in ac.fetchall()}
    shop, token = st["shop"], st["token"]
    H = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
    q = '''query($c:String){ orders(first:250, after:$c, query:"created_at:>=2026-06-01 created_at:<=2026-06-30") {
             pageInfo{hasNextPage endCursor} nodes{ name cancelledAt displayFinancialStatus } } }'''
    S = {}; cur = None
    with httpx.Client(timeout=60) as cl:
        while True:
            r = cl.post(f"https://{shop}/admin/api/2024-10/graphql.json", headers=H, json={"query": q, "variables": {"c": cur}}).json()
            d = (r.get("data") or {}).get("orders") or {}
            for o in d.get("nodes") or []:
                S[norm(o.get("name"))] = "ANULAT" if o.get("cancelledAt") else (o.get("displayFinancialStatus") or "?")
            pi = d.get("pageInfo") or {}
            if not pi.get("hasNextPage"): break
            cur = pi.get("endCursor")
    # matrice: AWB status -> {n, awb_da, rev, shopify buckets}
    M = defaultdict(lambda: {"n": 0, "awb": 0, "rev": 0.0, "sh": defaultdict(int)})
    for name, a in A.items():
        m = M[a["st"]]; m["n"] += 1; m["awb"] += int(a["awb"]); m["rev"] += a["p"]
        m["sh"][S.get(name, "—lipsa Shopify—")] += 1
    tot = len(A); deliv = M.get("delivered", {}).get("n", 0)
    shipped = sum(m["n"] for stt, m in M.items() if stt == "delivered" or stt in SHIPPED_NODELIV)
    print(f"\n===== {pfx} ({domain}) — {tot} comenzi | LIVRATE {deliv} | plecate-cu-AWB {shipped} =====")
    print(f"  {'status AWB':22}{'#':>5}{'cu_AWB':>7}{'venit(loc)':>11}  shopify")
    for stt, m in sorted(M.items(), key=lambda kv: -kv[1]["n"]):
        sh = ", ".join(f"{k}:{v}" for k, v in sorted(m["sh"].items(), key=lambda x: -x[1]))
        rev = f"{m['rev']:,.0f}" if stt == "delivered" else "0"
        print(f"  {stt:22}{m['n']:>5}{m['awb']:>7}{rev:>11}  [{sh}]")
awb.close()
