# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary", "httpx>=0.27"]
# ///
"""courier_verify.py — verifică comenzile blocate din AWBprint DIRECT la curier (DPD/Sameday)
și scrie statusul terminal ÎN AWBprint (durabil via regula don't-downgrade din sync_service AWBprint).

DE CE (complement la awbprint_reconcile.py): reconcilierea din profit_orders (Shopify PAID) NU acoperă
comenzile pe care engine-ul de profit nu le poate urmări — cele fără AWB legat în profit_orders („Lipsă
awb"/„Netrimisa"), deși AWBprint ARE numărul AWB. Aici mergem la sursa ultimă de adevăr = CURIERUL.

VERIFICĂ TOATE AWB-URILE comenzii, nu doar cel principal: o comandă poate avea AWB-ul principal blocat
(„generat"/anulat, curierul nu l-a ridicat) DAR să fi plecat pe un AWB REFĂCUT (alt tracking din
`order_awbs`). Colectăm principal (`orders.tracking_number`) + toate alternativele non-retur/non-redirect
din `order_awbs`, verificăm fiecare, și dacă ORICARE e livrat → comanda e livrată. Backlog 2026-07-25:
main-AWB a recuperat 349 livrate + 23 refuzate; AWB-alternativ încă 4 (Grandia refăcute pe DPD etc.).
Rămân 534 „generat" (AWB făcut, curierul NU l-a ridicat niciodată = colet care n-a plecat — semnal
operațional, de investigat separat). Vezi [[sameday-tracking-gap-awbprint]].

⚠️ NU scrie în profit_orders — engine-ul re-derivă `status_category` din `courier_status` la fiecare
sync (2:30), deci un flip acolo se pierde. AWBprint don't-downgrade păstrează terminalul → scriem DOAR acolo.

RULARE (VPS): `.venv/bin/python courier_verify.py` (DRY) · `... --apply` (scrie) · `--limit=N` (test).
CRON (03:30, după awbprint_reconcile 03:15). ⚠️ Cost API DPD mărginit prin AWB_MAX_AGE_DAYS (implicit 90).
  30 3 * * * cd /root/Scripturi && set -a && . /root/Scripturi/.env && set +a && \
    /usr/bin/flock -n /tmp/courier_verify.lock /root/Scripturi/.venv/bin/python /root/Scripturi/courier_verify.py --apply \
    >> /root/Scripturi/logs/courier_verify.log 2>&1 && /root/Scripturi/.venv/bin/python /root/Scripturi/heartbeat.py courier_verify
Secrete: DATABASE_URL_AWBPRINT (.env) + COURIER_CREDS_JSON (KB, via awb_track.load_creds)."""
import os
import sys
import re
import asyncio
from collections import defaultdict

import psycopg2
import httpx

sys.path.insert(0, os.environ.get(
    "AWBTRACK_DIR", "/root/Scripturi/team-intelligence/plugins/gigi/skills/awb-track"))
import awb_track  # noqa: E402

NONTERMINAL = (
    "fulfilled", "waiting_for_courier", "processing", "not_fulfilled", "ready_for_pickup",
    "new", "errors_incorrect_shipping_address", "awaiting_shipment_generation_initialization",
    "in_transit", "out_for_delivery", "on_hold", "sending", "redirected", "deferred_delivery",
)
# normalize_status -> AWBprint terminal aggregated_status (returned & refused = pierdere transport)
TERMINAL = {"delivered": "delivered", "returned": "refused", "refused": "refused"}
RANK = {"delivered": 3, "refused": 2}  # la agregare pe comandă, livrarea are prioritate
MIN_AGE = int(os.environ.get("AWB_MIN_AGE_DAYS", "14"))
MAX_AGE = int(os.environ.get("AWB_MAX_AGE_DAYS", "90"))


def _clean(d):
    d = re.sub(r"([?&])(schema|channel_binding|pgbouncer|connection_limit)=[^&]*", r"\1", d)
    return re.sub(r"[?&]+(&|$)", r"\1", d).rstrip("?&")


def main():
    apply = "--apply" in sys.argv
    limit = next((int(a.split("=")[1]) for a in sys.argv if a.startswith("--limit=")), None)

    creds = awb_track.load_creds()
    dpd = (creds.get("dpd_creds") or {}).get("dpd-ro") or {}
    sd = creds.get("sameday_creds") or {}

    cx = psycopg2.connect(_clean(os.environ["DATABASE_URL_AWBPRINT"]))
    cur = cx.cursor()
    # o comandă blocată + TOATE AWB-urile ei non-retur (principal + alternative din order_awbs)
    cur.execute(
        """WITH stuck AS (
             SELECT o.id, o.order_number, o.tracking_number AS main_awb,
                    o.aggregated_status, COALESCE(o.total_price,0) AS price
             FROM orders o
             WHERE o.aggregated_status = ANY(%s) AND o.tracking_number IS NOT NULL AND o.awb_count>=1
               AND o.frisbo_created_at < now() - (%s || ' days')::interval
               AND o.frisbo_created_at > now() - (%s || ' days')::interval)
           SELECT st.order_number, st.aggregated_status, st.price, awb.tn
           FROM stuck st
           CROSS JOIN LATERAL (
             SELECT st.main_awb AS tn
             UNION
             SELECT oa.tracking_number FROM order_awbs oa
             WHERE oa.order_id=st.id AND oa.tracking_number IS NOT NULL
               AND COALESCE(oa.is_return_label,false)=false AND COALESCE(oa.is_redirect_label,false)=false
           ) awb
           WHERE awb.tn IS NOT NULL""",
        (list(NONTERMINAL), str(MIN_AGE), str(MAX_AGE)),
    )
    rows = cur.fetchall()

    orders = {}          # order_number -> {agg, price, awbs:set}
    for onum, agg, price, tn in rows:
        tn = (tn or "").strip()
        if not tn:
            continue
        o = orders.setdefault(onum, {"agg": agg, "price": float(price), "awbs": set()})
        o["awbs"].add(tn)
    if limit:
        orders = dict(list(orders.items())[:limit])

    allawb = {a for o in orders.values() for a in o["awbs"]}
    dpd_awbs = [a for a in allawb if awb_track.guess_courier(a) == "dpd-ro"]
    sd_awbs = [a for a in allawb if awb_track.guess_courier(a) == "sameday"]
    print(f"Comenzi blocate ({MIN_AGE}-{MAX_AGE}z): {len(orders)} | AWB-uri de verificat: {len(allawb)} "
          f"(DPD {len(dpd_awbs)} · Sameday {len(sd_awbs)} · alt {len(allawb)-len(dpd_awbs)-len(sd_awbs)})", flush=True)

    async def verify():
        raw = {}
        async with httpx.AsyncClient(timeout=40) as cl:
            if dpd_awbs:
                raw.update(await awb_track.track_dpd(cl, dpd_awbs, dpd))
            for a in sd_awbs:
                try:
                    raw[a] = await awb_track.track_sameday(cl, a, sd)
                except Exception as e:
                    raw[a] = f"eroare {type(e).__name__}"
        return raw

    raw = asyncio.run(verify())
    normmap = {a: awb_track.normalize_status(s) for a, s in raw.items()}

    resolved, by_new, on_alt = {}, defaultdict(lambda: [0, 0.0]), 0
    for onum, o in orders.items():
        best = None  # terminal aggregated_status
        for awb in o["awbs"]:
            nm = normmap.get(awb)
            if nm in TERMINAL:
                cand = TERMINAL[nm]
                if best is None or RANK[cand] > RANK[best]:
                    best = cand
        if best and best != o["agg"]:
            resolved[onum] = best
            by_new[best][0] += 1
            by_new[best][1] += o["price"]
            if len(o["awbs"]) > 1:
                on_alt += 1
    print(f"🔧 REZOLVABILE (terminal): {len(resolved)}  (din care pe AWB alternativ: {on_alt})", flush=True)
    for new, (n, rv) in sorted(by_new.items(), key=lambda x: -x[1][0]):
        print(f"    -> {new:10} {n:5}   {rv:>12,.0f} RON brut", flush=True)

    if not apply:
        print("(DRY — nimic scris. --apply ca să scrii în AWBprint.)", flush=True)
        cx.close()
        return 0
    if resolved:
        ons = list(resolved)
        sts = [resolved[o] for o in ons]
        cur.execute("""CREATE TABLE IF NOT EXISTS awb_courier_verify_audit(
            order_number text, old_status text, new_status text, applied_at timestamptz DEFAULT now())""")
        cur.execute(
            """INSERT INTO awb_courier_verify_audit(order_number, old_status, new_status)
               SELECT o.order_number, o.aggregated_status, v.st FROM orders o
               JOIN (SELECT unnest(%s::text[]) on_, unnest(%s::text[]) st) v ON o.order_number=v.on_
               WHERE o.aggregated_status <> v.st""", (ons, sts))
        cur.execute(
            """UPDATE orders o SET aggregated_status=v.st, synced_at=now()
               FROM (SELECT unnest(%s::text[]) on_, unnest(%s::text[]) st) v
               WHERE o.order_number=v.on_ AND o.aggregated_status <> v.st""", (ons, sts))
        n = cur.rowcount
        cx.commit()
        print(f"✅ APLICAT: {n} comenzi corectate în AWBprint (audit awb_courier_verify_audit).", flush=True)
    cx.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
