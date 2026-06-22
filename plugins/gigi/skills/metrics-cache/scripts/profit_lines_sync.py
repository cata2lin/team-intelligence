# /// script
# requires-python = ">=3.10"
# dependencies = ["httpx>=0.27"]
# ///
"""
Populează `profit_order_lines` (SQLite profitability.db) cu datele PER-LINIE care lipsesc din engine:
(month, prefix, order_name, sku, qty, line_revenue, line_cogs). Venit pe linie = discountedTotalSet (net,
gestionează 2+1 free → linia gratis = 0). status_category NU se stochează aici — se ia prin JOIN din
profit_orders la raportare (nu recalculăm logica de status). ADITIV: tabel nou, nu atinge profit_orders/engine.

  cd /root/Scripturi && .venv/bin/python profit_lines_sync.py 2026-05 [PREFIX|all]
"""
import sys, os, asyncio, sqlite3
from collections import defaultdict
sys.path.insert(0, "/root/Scripturi"); os.chdir("/root/Scripturi")
from datetime import datetime, timezone, timedelta
import httpx
from core.stores import list_stores
from api.profitability import _fetch_variant_costs, DEFAULT_API_VERSION, DATA_DIR

DB = str(DATA_DIR / "profitability.db")
EXT_GQL = """query($q:String!,$cursor:String){
  orders(first:100, query:$q, after:$cursor){
    pageInfo{hasNextPage endCursor}
    edges{node{ name createdAt
      lineItems(first:100){edges{node{ quantity sku
        discountedTotalSet{shopMoney{amount}} variant{id} }}}
    }}
  }
}"""


def _ensure():
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS profit_order_lines (
        month TEXT, prefix TEXT, order_name TEXT, sku TEXT,
        qty INTEGER, line_revenue REAL, line_cogs REAL,
        PRIMARY KEY (month, prefix, order_name, sku))""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_pol_msku ON profit_order_lines(month, sku)")
    c.commit(); c.close()


async def sync_store(cl, st, ym, start, end):
    qs = (start - timedelta(days=1)).isoformat().replace("+00:00", "Z")
    qe = (end + timedelta(days=1)).isoformat().replace("+00:00", "Z")
    q = f"status:any created_at:>={qs} created_at:<{qe}"
    shop, token, prefix = st["shop"], st["token"], st["prefix"]
    url = f"https://{shop}/admin/api/{DEFAULT_API_VERSION}/graphql.json"
    H = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
    cursor = None; vids = set(); raw = []; ok = True
    while True:
        r = None
        for _ in range(5):
            try:
                r = await cl.post(url, headers=H, json={"query": EXT_GQL, "variables": {"q": q, "cursor": cursor}}, timeout=60)
                if r.status_code == 429: await asyncio.sleep(2); continue
                break
            except Exception: await asyncio.sleep(1)
        else:
            ok = False; break        # toate retry-urile au eșuat → fetch INCOMPLET
        try:
            d = (r.json().get("data") or {}).get("orders") or {}
        except Exception:
            ok = False; break
        for ed in d.get("edges") or []:
            n = ed.get("node") or {}
            if (n.get("createdAt", "")[:7]) != ym: continue
            agg = {}
            for le in (n.get("lineItems") or {}).get("edges") or []:
                ln = le.get("node") or {}
                vid = str((ln.get("variant") or {}).get("id") or "");
                if vid: vids.add(vid)
                sku = (ln.get("sku") or "").strip()
                lr = float(((ln.get("discountedTotalSet") or {}).get("shopMoney") or {}).get("amount") or 0)
                qty = int(ln.get("quantity") or 0)
                agg.setdefault((sku, vid), [0, 0.0]);  agg[(sku, vid)][0] += qty; agg[(sku, vid)][1] += lr
            raw.append((n.get("name"), agg))
        pi = d.get("pageInfo") or {}
        if not pi.get("hasNextPage"): break
        cursor = pi.get("endCursor")
    if not ok:
        return None                  # NU scrie luna parțial (evită pierderea silențioasă — ca build_cache)
    vcost = await _fetch_variant_costs(cl, shop, token, list(vids)) if vids else {}
    # G1: agregă pe (comandă, SKU) — același SKU pe 2 variante NU mai face coliziune PK; linii fără SKU → UNKNOWN_SKU.
    perkey = defaultdict(lambda: [0, 0.0, 0.0])   # (name,key) -> qty, line_revenue, line_cogs
    for name, agg in raw:
        for (sku, vid), (qty, lr) in agg.items():
            key = sku or (vcost.get(vid, {}).get("sku") or "") or "UNKNOWN_SKU"
            uc = vcost.get(vid, {}).get("unit_cost")
            d = perkey[(name, key)]; d[0] += qty; d[1] += lr; d[2] += (uc or 0) * qty
    return [(ym, prefix, name, key, q, round(lr, 2), round(cg, 2)) for (name, key), (q, lr, cg) in perkey.items()]


async def main(ym, which):
    y, m = ym.split("-"); m = int(m)
    start = datetime(int(y), m, 1, tzinfo=timezone.utc)
    end = datetime(int(y) + (1 if m == 12 else 0), 1 if m == 12 else m + 1, 1, tzinfo=timezone.utc) - timedelta(seconds=1)
    stores = list_stores()
    if which != "all":
        stores = [s for s in stores if s["prefix"] == which]
    _ensure()
    async with httpx.AsyncClient() as cl:
        for st in stores:
            try:
                rows = await sync_store(cl, st, ym, start, end)
            except Exception as e:
                print(f"  {st['prefix']}: ERR {type(e).__name__} {str(e)[:80]}"); continue
            if rows is None:
                print(f"  {st['prefix']}: ⚠ fetch INCOMPLET — păstrez datele existente (NU șterg/scriu)"); continue
            c = sqlite3.connect(DB)
            c.execute("DELETE FROM profit_order_lines WHERE month=? AND prefix=?", (ym, st["prefix"]))
            c.executemany("INSERT OR REPLACE INTO profit_order_lines VALUES (?,?,?,?,?,?,?)", rows)
            c.commit(); c.close()
            print(f"  {st['prefix']}: {len(rows)} linii")
    print(f"DONE {ym} ({which})")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "all"))
