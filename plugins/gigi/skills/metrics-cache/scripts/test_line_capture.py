# /// script
# requires-python = ">=3.10"
# dependencies = ["httpx>=0.27"]
# ///
"""
TEST read-only (rulează pe VPS): captură PER-LINIE (sku, qty, venit-pe-linie via discountedTotalSet) pentru
UN magazin / o lună, reutilizând list_stores() + costurile de variantă din engine. Validează că suma pe linii
≈ totalul comenzii (diferența = transport/discount comandă) și agregă revenue+COGS per SKU.
NU scrie nimic, NU atinge engine-ul de producție.

  cd /root/Scripturi && /root/Scripturi/.venv/bin/python test_line_capture.py list
  cd /root/Scripturi && /root/Scripturi/.venv/bin/python test_line_capture.py <PREFIX> 2026-05
"""
import sys, os, asyncio
sys.path.insert(0, "/root/Scripturi"); os.chdir("/root/Scripturi")
from datetime import datetime, timezone, timedelta
import httpx
from core.stores import list_stores
from api.profitability import _fetch_variant_costs, DEFAULT_API_VERSION

EXT_GQL = """query($q:String!,$cursor:String){
  orders(first:100, query:$q, after:$cursor){
    pageInfo{hasNextPage endCursor}
    edges{node{ name createdAt
      totalPriceSet{shopMoney{amount currencyCode}}
      lineItems(first:100){edges{node{ quantity sku
        discountedTotalSet{shopMoney{amount}} variant{id} }}}
    }}
  }
}"""


async def run(prefix, month):
    y, m = month.split("-"); m = int(m)
    start = datetime(int(y), m, 1, tzinfo=timezone.utc)
    end = datetime(int(y) + (1 if m == 12 else 0), 1 if m == 12 else m + 1, 1, tzinfo=timezone.utc) - timedelta(seconds=1)
    qs = (start - timedelta(days=1)).isoformat().replace("+00:00", "Z")
    qe = (end + timedelta(days=1)).isoformat().replace("+00:00", "Z")
    q = f"status:any created_at:>={qs} created_at:<{qe}"
    st = next((s for s in list_stores() if s["prefix"] == prefix), None)
    if not st:
        print("prefix necunoscut. disponibile:", [s["prefix"] for s in list_stores()]); return
    shop, token = st["shop"], st["token"]
    url = f"https://{shop}/admin/api/{DEFAULT_API_VERSION}/graphql.json"
    H = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
    ym = f"{y}-{str(m).zfill(2)}"
    orders = []; cursor = None; vids = set()
    async with httpx.AsyncClient() as cl:
        while True:
            r = await cl.post(url, headers=H, json={"query": EXT_GQL, "variables": {"q": q, "cursor": cursor}}, timeout=60)
            d = (r.json().get("data") or {}).get("orders") or {}
            for ed in d.get("edges") or []:
                n = ed.get("node") or {}
                if (n.get("createdAt", "")[:7]) != ym:
                    continue
                tot = float(((n.get("totalPriceSet") or {}).get("shopMoney") or {}).get("amount") or 0)
                lines = []
                for le in (n.get("lineItems") or {}).get("edges") or []:
                    ln = le.get("node") or {}
                    lr = float(((ln.get("discountedTotalSet") or {}).get("shopMoney") or {}).get("amount") or 0)
                    vid = str((ln.get("variant") or {}).get("id") or "")
                    sku = (ln.get("sku") or "").strip()
                    if vid: vids.add(vid)
                    lines.append((sku, int(ln.get("quantity") or 0), lr, vid))
                orders.append((n.get("name"), tot, lines))
            pi = d.get("pageInfo") or {}
            if not pi.get("hasNextPage"): break
            cursor = pi.get("endCursor")
        vcost = await _fetch_variant_costs(cl, shop, token, list(vids)) if vids else {}

    # validate revenue reconciliation
    n_ord = len(orders)
    tot_order = sum(t for _, t, _ in orders)
    tot_lines = sum(lr for _, _, ls in orders for _, _, lr, _ in ls)
    persku = {}
    for _, _, ls in orders:
        for sku, qty, lr, vid in ls:
            key = sku or (vcost.get(vid, {}).get("sku") or "?")
            uc = vcost.get(vid, {}).get("unit_cost")
            d = persku.setdefault(key, [0, 0.0, 0.0])
            d[0] += qty; d[1] += lr; d[2] += (uc * qty if uc is not None else 0)
    print(f"Magazin {prefix} ({shop}) — {ym}: {n_ord} comenzi")
    print(f"  Σ total comenzi = {tot_order:,.0f}   Σ venit-pe-linie = {tot_lines:,.0f}   diff = {tot_order-tot_lines:,.0f} ({(tot_order-tot_lines)/tot_order*100 if tot_order else 0:.1f}% = transport/disc comandă)")
    print(f"  SKU-uri distincte: {len(persku)}")
    print(f"\n  {'sku':30}{'buc':>6}{'venit':>11}{'cogs':>10}{'marja':>10}")
    for sku, (qty, rev, cg) in sorted(persku.items(), key=lambda x: -x[1][1])[:15]:
        print(f"  {sku[:30]:30}{qty:>6}{rev:>11.0f}{cg:>10.0f}{rev-cg:>10.0f}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "list":
        for s in list_stores(): print(s["prefix"], "->", s["shop"])
    else:
        asyncio.run(run(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "2026-05"))
