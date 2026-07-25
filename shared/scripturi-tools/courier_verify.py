# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary", "httpx>=0.27"]
# ///
"""courier_verify.py — verifică comenzile blocate din AWBprint DIRECT la curier (DPD/Sameday)
și scrie statusul terminal ÎN AWBprint (durabil via regula don't-downgrade din sync_service AWBprint).

DE CE (complement la awbprint_reconcile.py): reconcilierea din profit_orders (Shopify PAID) NU acoperă
comenzile pe care engine-ul de profit nu le poate urmări — cele fără AWB legat în profit_orders („Lipsă
awb"/„Netrimisa"), deși AWBprint ARE numărul AWB. Aici mergem la sursa ultimă de adevăr = CURIERUL.
Backlog verificat 2026-07-25: din 1.206 blocate → 349 livrate + 23 refuzate/retur recuperate (curier
confirmă), 129 chiar în tranzit, 534 „generat" (AWB făcut dar curierul nu l-a ridicat NICIODATĂ =
colet care n-a plecat efectiv — semnal operațional separat). Vezi [[sameday-tracking-gap-awbprint]].

⚠️ NU scrie în profit_orders — engine-ul îl re-derivă din courier_status la fiecare sync (2:30), deci
un flip acolo se pierde. AWBprint don't-downgrade păstrează statusul terminal → scriem DOAR acolo.

RULARE (VPS): `.venv/bin/python courier_verify.py` (DRY) · `... --apply` (scrie) · `--limit=N` (test).
CRON (03:30, după awbprint_reconcile 03:15). ⚠️ Cost API: ~1 cerere DPD /10 AWB-uri → mărginit prin
AWB_MAX_AGE_DAYS (implicit 90; strânge-l la 45 dacă vrei mai puține apeluri — livratele se rezolvă în
câteva zile de la expediere, deci fereastra 14-45z prinde ~tot ce se poate recupera).
  30 3 * * * cd /root/Scripturi && set -a && . /root/Scripturi/.env && set +a && \
    /usr/bin/flock -n /tmp/courier_verify.lock /root/Scripturi/.venv/bin/python /root/Scripturi/courier_verify.py --apply \
    >> /root/Scripturi/logs/courier_verify.log 2>&1 && /root/Scripturi/.venv/bin/python /root/Scripturi/heartbeat.py courier_verify
Secrete: DATABASE_URL_AWBPRINT (.env) + COURIER_CREDS_JSON (KB, via awb_track.load_creds)."""
import os
import sys
import re
import json
import asyncio
from collections import Counter, defaultdict

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
    cur.execute(
        """SELECT o.order_number, o.tracking_number, o.aggregated_status, COALESCE(o.total_price,0)
           FROM orders o
           WHERE o.aggregated_status = ANY(%s) AND o.tracking_number IS NOT NULL AND o.awb_count>=1
             AND o.frisbo_created_at < now() - (%s || ' days')::interval
             AND o.frisbo_created_at > now() - (%s || ' days')::interval""",
        (list(NONTERMINAL), str(MIN_AGE), str(MAX_AGE)),
    )
    rows = cur.fetchall()
    if limit:
        rows = rows[:limit]

    dpd_awbs, sd_awbs, meta = [], [], {}
    for onum, awb, agg, price in rows:
        awb = (awb or "").strip()
        if not awb:
            continue
        meta[awb] = (onum, agg, float(price))
        ck = awb_track.guess_courier(awb)
        if ck == "dpd-ro":
            dpd_awbs.append(awb)
        elif ck == "sameday":
            sd_awbs.append(awb)
    print(f"AWBprint blocate cu AWB ({MIN_AGE}-{MAX_AGE}z): {len(rows)} | "
          f"DPD {len(dpd_awbs)} · Sameday {len(sd_awbs)} · alt {len(rows)-len(dpd_awbs)-len(sd_awbs)}", flush=True)

    async def verify():
        raw = {}
        async with httpx.AsyncClient(timeout=40) as cl:
            if dpd_awbs:
                raw.update(await awb_track.track_dpd(cl, dpd_awbs, dpd))
            for awb in sd_awbs:
                try:
                    raw[awb] = await awb_track.track_sameday(cl, awb, sd)
                except Exception as e:
                    raw[awb] = f"eroare {type(e).__name__}"
        return raw

    raw = asyncio.run(verify())

    resolved, by_norm, by_new = {}, Counter(), defaultdict(lambda: [0, 0.0])
    for awb, st in raw.items():
        norm = awb_track.normalize_status(st)
        by_norm[norm] += 1
        new = TERMINAL.get(norm)
        onum, agg, price = meta[awb]
        if new and new != agg:
            resolved[onum] = new
            by_new[new][0] += 1
            by_new[new][1] += price
    print(f"Status real la curier: {dict(by_norm)}", flush=True)
    print(f"🔧 REZOLVABILE (terminal): {len(resolved)}", flush=True)
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
