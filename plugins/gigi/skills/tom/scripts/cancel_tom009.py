# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary"]
# ///
"""Anuleaza TOM-009 in TOM (antet + cele 51 de linii). Sursa (Grandia PO#10) e deja CANCELLED."""
import subprocess, psycopg2, json, sys, datetime
KB = "/Users/gheorghebeschea/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"
def dsn(k):
    return subprocess.run(["uv","run",KB,"secret-get",k],capture_output=True,text=True).stdout.strip().split("?")[0]
APPLY = "--apply" in sys.argv
OUT = "/Users/gheorghebeschea/Downloads/Scripturi/team-intelligence/plugins/gigi/skills/tom/backups"
STAMP = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
NOTE = "Anulat 2026-08-26: Grandia PO#10 e CANCELLED la sursa; liniile erau pe cantitate 0 iar marfa a venit pe alte PO-uri (TOM-021 etc)."

cn = psycopg2.connect(dsn("DATABASE_URL_TOM")); c = cn.cursor()
c.execute('''SELECT p.id, p."tomNumber", p.status::text, count(i.*),
                    count(*) FILTER (WHERE i."shippedQty">0 OR i."receivedQty">0)
             FROM purchase_orders p JOIN purchase_order_items i ON i."poId"=p.id
             WHERE p."tomNumber"='TOM-009' GROUP BY p.id, p."tomNumber", p.status::text''')
pid, tn, st, nlin, cu_marfa = c.fetchone()
print("%s  status=%s  linii=%s  linii cu marfa primita/expediata=%s" % (tn, st, nlin, cu_marfa))
if cu_marfa:
    print("STOP: are linii cu marfa — nu anulez automat."); sys.exit(1)
if not APPLY:
    print("(dry-run)"); sys.exit(0)

c.execute('SELECT id, status::text FROM purchase_order_items WHERE "poId"=%s', (pid,))
bak = {"po": {"id": pid, "tomNumber": tn, "status": st},
       "linii": [{"id": i, "status": s} for i, s in c.fetchall()]}
fn = "%s/tom009_cancel_%s.json" % (OUT, STAMP)
open(fn, "w").write(json.dumps(bak, indent=1, ensure_ascii=False))

c.execute('UPDATE purchase_order_items SET status=%s::"PoItemStatus", "cancelledAt"=now(), "cancelReason"=%s::"CancelReason", "cancelNote"=%s WHERE "poId"=%s AND status::text <> %s', ("CANCELLED", "REQUESTER_CANCELLED", NOTE, pid, "CANCELLED"))
nl = c.rowcount
c.execute('UPDATE purchase_orders SET status=%s::"PoStatus", "cancelledAt"=now(), "cancelReason"=%s::"CancelReason", "cancelNote"=%s WHERE id=%s', ("CANCELLED", "REQUESTER_CANCELLED", NOTE, pid))
np = c.rowcount
cn.commit(); cn.close()
print("COMMIT: %d linii + %d antet -> %s" % (nl, np, fn.split("/")[-1]))
