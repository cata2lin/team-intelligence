# /// script
# requires-python = ">=3.10"
# dependencies = ["httpx>=0.27"]
# ///
"""VERIFICARE COMPLETA Europa: fiecare comanda Shopify (CZ/PL/BG) iunie -> TOATE fulfillment-urile (incl
CANCELLED) -> fiecare AWB verificat DIRECT in DPD (api.dpd.ro). Reconciliere: DPD livrat dar necontat = leak."""
import os, sys, json, asyncio, httpx
from collections import Counter, defaultdict
sys.path.insert(0, "/root/Scripturi")
sys.path.insert(0, "/root/Scripturi/team-intelligence/plugins/gigi/skills/awb-track")
from core.stores import list_stores
import awb_track

CREDS = {"username": os.environ["DPD_RO_USERNAME"], "password": os.environ["DPD_RO_PASSWORD"]}

def pull_orders(shop, token):
    H = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
    q = '''query($c:String){ orders(first:100, after:$c, query:"created_at:>=2026-06-01 created_at:<2026-07-01") {
             pageInfo{hasNextPage endCursor}
             nodes{ name cancelledAt displayFinancialStatus currentTotalPriceSet{shopMoney{amount currencyCode}}
                    fulfillments(first:10){ status trackingInfo{ number } } } } }'''
    orders = []; cur = None
    with httpx.Client(timeout=90) as cl:
        while True:
            r = cl.post(f"https://{shop}/admin/api/2024-10/graphql.json", headers=H, json={"query": q, "variables": {"c": cur}}).json()
            d = (r.get("data") or {}).get("orders") or {}
            for o in d.get("nodes") or []:
                awbs = []
                for ff in (o.get("fulfillments") or []):
                    for ti in (ff.get("trackingInfo") or []):
                        if ti.get("number"): awbs.append(ti["number"].strip())
                m = (o.get("currentTotalPriceSet") or {}).get("shopMoney") or {}
                orders.append({"name": o["name"], "canc": bool(o.get("cancelledAt")),
                               "fin": o.get("displayFinancialStatus") or "?",
                               "price": float(m.get("amount") or 0), "ccy": m.get("currencyCode"),
                               "awbs": list(dict.fromkeys(awbs))})
            pi = d.get("pageInfo") or {}
            if not pi.get("hasNextPage"): break
            cur = pi.get("endCursor")
    return orders

async def main():
    stores = {s["prefix"]: s for s in list_stores()}
    report = {}
    async with httpx.AsyncClient() as client:
        for pfx in ["CZ", "PL", "BONBG"]:
            st = stores[pfx]
            orders = pull_orders(st["shop"], st["token"])
            all_awbs = list(dict.fromkeys([a for o in orders for a in o["awbs"]]))
            print(f"[{pfx}] {len(orders)} comenzi, {len(all_awbs)} AWB-uri unice -> interoghez DPD...", flush=True)
            raw = await awb_track.track_dpd(client, all_awbs, CREDS)
            norm = {a: awb_track.normalize_status(s) for a, s in raw.items()}
            dpd_state = Counter(norm.values())
            # per order: delivered daca ORICE awb livrat
            deliv_orders = 0; paid = 0; leak = []; overcount = []
            for o in orders:
                states = [norm.get(a, "unknown") for a in o["awbs"]]
                is_deliv = "delivered" in states
                if o["fin"] == "PAID": paid += 1
                if is_deliv:
                    deliv_orders += 1
                    if o["fin"] != "PAID":   # DPD livrat DAR nu e platit in Shopify = LEAK
                        leak.append({"order": o["name"], "fin": o["fin"], "canc": o["canc"], "price": o["price"], "awbs": o["awbs"]})
                elif o["fin"] == "PAID":      # platit dar DPD nu zice livrat
                    overcount.append({"order": o["name"], "states": states})
            leak_rev = sum(x["price"] for x in leak)
            report[pfx] = {"orders": len(orders), "awbs": len(all_awbs), "dpd": dict(dpd_state),
                           "dpd_delivered_orders": deliv_orders, "shopify_paid": paid,
                           "leak_count": len(leak), "leak_rev": round(leak_rev), "ccy": orders[0]["ccy"] if orders else "?",
                           "overcount": len(overcount), "leak_sample": leak[:15]}
            print(f"[{pfx}] DPD status AWB: {dict(dpd_state)}", flush=True)
            print(f"[{pfx}] comenzi DPD-LIVRATE={deliv_orders} | Shopify PAID={paid} | "
                  f"🔴 LEAK (DPD livrat DAR neplatit)={len(leak)} (venit {round(leak_rev):,} {report[pfx]['ccy']}) | "
                  f"paid-dar-DPD-nelivrat={len(overcount)}", flush=True)
            for x in leak[:15]:
                print(f"      LEAK {x['order']} fin={x['fin']} canc={x['canc']} price={x['price']:.0f} awb={x['awbs']}", flush=True)
    json.dump(report, open("/root/Scripturi/dpd_verify_report.json", "w"), indent=1)
    print("\n=== scris /root/Scripturi/dpd_verify_report.json ===", flush=True)

asyncio.run(main())
