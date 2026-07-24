#!/usr/bin/env python3
"""Corectie DPD pe TOATE magazinele: comenzile ne-livrate-cu-AWB (dpd-ro) verificate live in DPD;
unde DPD zice LIVRAT -> status_category='Livrata' (venitul original e deja in profit_orders).
DRY by default (raporteaza + salveaza flips.json); --apply aplica din JSON."""
import os, sys, json, asyncio, httpx, sqlite3
from collections import defaultdict
sys.path.insert(0, "/root/Scripturi/team-intelligence/plugins/gigi/skills/awb-track")
import awb_track
DB = "/root/Scripturi/data/profitability.db"
MONTHS = tuple(os.environ.get("FLIP_MONTHS", "2026-06,2026-07").split(","))
JSONF = "/root/Scripturi/dpd_flips_" + "_".join(m.replace("-", "") for m in MONTHS) + ".json"
CREDS = {"username": os.environ.get("DPD_RO_USERNAME", ""), "password": os.environ.get("DPD_RO_PASSWORD", "")}

def apply_from_json():
    flips = json.load(open(JSONF))
    c = sqlite3.connect(DB); n = 0
    for f in flips:
        cur = c.execute("UPDATE profit_orders SET status_category='Livrata', courier_status='Delivered (DPD verif 2026-07-24)' "
                        "WHERE month=? AND order_name=? AND awb=? AND status_category<>'Livrata'",
                        (f["month"], f["order_name"], f["awb"])); n += cur.rowcount
    c.commit(); c.close()
    print(f"✅ APLICAT: {n} comenzi -> Livrata")

async def dry():
    c = sqlite3.connect(DB)
    ph = ",".join("?" * len(MONTHS))
    rows = c.execute(f"SELECT month, prefix, order_name, awb, revenue, currency, status_category FROM profit_orders "
                     f"WHERE month IN ({ph}) AND awb!='' AND courier_key='dpd-ro' AND status_category<>'Livrata'", MONTHS).fetchall()
    c.close()
    awbs = list(dict.fromkeys([r[3] for r in rows]))
    print(f"{len(rows)} comenzi ne-livrate cu AWB DPD | {len(awbs)} AWB unice -> interoghez DPD...", flush=True)
    async with httpx.AsyncClient() as cl:
        raw = await awb_track.track_dpd(cl, awbs, CREDS)
    norm = {a: awb_track.normalize_status(s) for a, s in raw.items()}
    flips = []; by_old = defaultdict(int); by_pfx = defaultdict(lambda: [0, 0.0])
    for m, pfx, on, awb, rev, ccy, st in rows:
        if norm.get(awb) == "delivered":
            flips.append({"month": m, "prefix": pfx, "order_name": on, "awb": awb, "revenue": rev, "ccy": ccy, "old": st})
            by_old[st] += 1; by_pfx[pfx][0] += 1; by_pfx[pfx][1] += float(rev or 0)
    json.dump(flips, open(JSONF, "w"))
    print(f"\n🔴 DPD zice LIVRAT dar engine ne-livrat: {len(flips)} comenzi", flush=True)
    print(f"  pe status vechi: {dict(by_old)}", flush=True)
    print(f"  pe magazin (comenzi | venit in moneda mag):", flush=True)
    for pfx, (n, rev) in sorted(by_pfx.items(), key=lambda x: -x[1][0]):
        print(f"    {pfx:6} {n:5} comenzi   {rev:>12,.0f}", flush=True)
    print(f"\n(DRY — salvat {JSONF}. Ruleaza cu --apply ca sa aplici.)", flush=True)

if __name__ == "__main__":
    if "--apply" in sys.argv: apply_from_json()
    else: asyncio.run(dry())
