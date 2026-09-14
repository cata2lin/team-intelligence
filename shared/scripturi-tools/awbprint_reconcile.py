# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary"]
# ///
"""awbprint_reconcile.py — dezgheață comenzile blocate în AWBprint din adevărul Scripturi.

DE CE: AWBprint are o SINGURĂ sursă de status = Frisbo, care ÎNGHEAȚĂ comenzi într-un status
non-terminal (waiting_for_courier / fulfilled / in_transit) chiar după ce curierul a livrat/returnat.
Re-pull din Frisbo nu ajută. AWBprint n-are credențiale de curier și nici conexiune la feed-ul
Scripturi, deci nu se poate auto-vindeca. Modulul `stuck_reconciliation.py` din AWBprint e scris exact
pentru asta (adoptă statusul terminal rezolvat de „sister Scripturi app") DAR nu-l apela nimic —
era cod mort. Fleet-wide ~1.700 comenzi / ~490k RON erau ascunse DOAR în view-ul AWBprint
(rată livrare/deliverability); P&L-ul de profit NU e afectat (folosește Shopify PAID). Vezi
memoria [[sameday-tracking-gap-awbprint]].

CE FACE: rulează pe VPS-ul de profit (unde e `profit_orders` local + acces la DB-ul AWBprint prin
`scraper`, care are drept UPDATE). Ia comenzile blocate din AWBprint (non-terminal + AWB + >min_age),
citește statusul terminal din `profit_orders` (Shopify PAID = adevăr livrare) și corectează DOAR
upgrade-urile non-terminal→terminal, DOAR unde `profit_orders` confirmă terminal. NU forțează nimic
neconfirmat (regula de aur: dacă sursa de adevăr nu confirmă livrarea, nu marca livrat). Idempotent:
scrie doar unde statusul chiar se schimbă. Auditare în `awb_reconcile_audit`. Regula don't-downgrade
din sync_service AWBprint păstrează ce setăm (un Frisbo ulterior nu re-îngheață).

RULARE (VPS): `.venv/bin/python awbprint_reconcile.py` (DRY) · `... --apply` (scrie).
CRON propus (după profit_orders_sync 02:30 → profit_orders e proaspăt):
  15 3 * * * cd /root/Scripturi && set -a && . /root/Scripturi/.env && set +a && \
    /usr/bin/flock -n /tmp/awb_reconcile.lock /root/Scripturi/.venv/bin/python /root/Scripturi/awbprint_reconcile.py --apply \
    >> /root/Scripturi/logs/awb_reconcile.log 2>&1 && /root/Scripturi/.venv/bin/python /root/Scripturi/heartbeat.py awbprint_reconcile
Secrete din env: DATABASE_URL_AWBPRINT (din .env, populat de deploy).
"""
import os
import re
import sys
import sqlite3
from datetime import date
from collections import Counter, defaultdict

import psycopg2

NONTERMINAL = (
    "fulfilled", "waiting_for_courier", "processing", "not_fulfilled", "ready_for_pickup",
    "new", "errors_incorrect_shipping_address", "awaiting_shipment_generation_initialization",
    "in_transit", "out_for_delivery", "on_hold", "sending", "redirected", "deferred_delivery",
)
SC_TO_AWB = {"Livrata": "delivered", "Refuzata": "refused", "Anulata": "cancelled"}
PROFIT_DB = os.environ.get("PROFIT_DB", "/root/Scripturi/data/profitability.db")
MIN_AGE_DAYS = int(os.environ.get("AWB_RECONCILE_MIN_AGE_DAYS", "14"))
LOOKBACK_MONTHS = 5  # profit_orders terminal statuses to load (acoperă coada 14-90z)


def _clean(d):
    d = re.sub(r"([?&])(schema|channel_binding|pgbouncer|connection_limit)=[^&]*", r"\1", d)
    return re.sub(r"[?&]+(&|$)", r"\1", d).rstrip("?&")


def _recent_months(n):
    y, m = date.today().year, date.today().month
    out = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return out


def load_profit_terminal():
    """{order_name: terminal_aggregated_status} din profit_orders (Livrata/Refuzata/Anulata)."""
    months = _recent_months(LOOKBACK_MONTHS)
    ph = ",".join("?" * len(months))
    con = sqlite3.connect(PROFIT_DB)
    rows = con.execute(
        f"SELECT order_name, status_category FROM profit_orders "
        f"WHERE month IN ({ph}) AND status_category IN ('Livrata','Refuzata','Anulata')",
        months,
    ).fetchall()
    con.close()
    return {on: SC_TO_AWB[sc] for on, sc in rows if sc in SC_TO_AWB}


def load_stuck(cur):
    cur.execute(
        """SELECT o.order_number, o.aggregated_status, o.courier_name, COALESCE(o.total_price,0)
           FROM orders o
           WHERE o.aggregated_status = ANY(%s) AND o.tracking_number IS NOT NULL AND o.awb_count>=1
             AND o.frisbo_created_at < now() - (%s || ' days')::interval""",
        (list(NONTERMINAL), str(MIN_AGE_DAYS)),
    )
    return cur.fetchall()


def main():
    apply = "--apply" in sys.argv
    prof = load_profit_terminal()
    if not prof:
        print("EROARE: profit_orders gol (verifică PROFIT_DB / sync).", flush=True)
        return 1

    cx = psycopg2.connect(_clean(os.environ["DATABASE_URL_AWBPRINT"]))
    cur = cx.cursor()
    stuck = load_stuck(cur)

    resolved = {}
    by_new = Counter()
    by_cour = defaultdict(lambda: [0, 0.0])
    for onum, agg, cour, price in stuck:
        new = prof.get(onum)
        if not new or new == agg:
            continue
        resolved[onum] = new
        by_new[new] += 1
        by_cour[cour or "null"][0] += 1
        by_cour[cour or "null"][1] += float(price)

    print(f"[{date.today()}] AWBprint blocate: {len(stuck)} | rezolvabile din profit_orders: {len(resolved)}", flush=True)
    print(f"  pe status nou: {dict(by_new)}", flush=True)
    for cour, (n, rv) in sorted(by_cour.items(), key=lambda x: -x[1][0]):
        print(f"    {cour:12} {n:5}   {rv:>12,.0f}", flush=True)

    if not apply:
        print("(DRY — nimic scris. Rulează cu --apply.)", flush=True)
        cx.close()
        return 0

    if not resolved:
        print("Nimic de aplicat.", flush=True)
        cx.close()
        return 0

    items = list(resolved.items())
    cur.execute(
        """CREATE TABLE IF NOT EXISTS awb_reconcile_audit(
             order_number text, old_status text, new_status text, applied_at timestamptz DEFAULT now())"""
    )
    ons = [i[0] for i in items]
    sts = [i[1] for i in items]
    # audit rândurile care CHIAR se schimbă (înainte de UPDATE, în aceeași tranzacție)
    cur.execute(
        """INSERT INTO awb_reconcile_audit(order_number, old_status, new_status)
           SELECT o.order_number, o.aggregated_status, v.st
           FROM orders o JOIN (SELECT unnest(%s::text[]) on_, unnest(%s::text[]) st) v
             ON o.order_number = v.on_
           WHERE o.aggregated_status <> v.st""",
        (ons, sts),
    )
    cur.execute(
        """UPDATE orders o SET aggregated_status = v.st, synced_at = now()
           FROM (SELECT unnest(%s::text[]) on_, unnest(%s::text[]) st) v
           WHERE o.order_number = v.on_ AND o.aggregated_status <> v.st""",
        (ons, sts),
    )
    n = cur.rowcount
    cx.commit()
    cx.close()
    print(f"✅ APLICAT: {n} comenzi corectate în AWBprint (audit în awb_reconcile_audit).", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
