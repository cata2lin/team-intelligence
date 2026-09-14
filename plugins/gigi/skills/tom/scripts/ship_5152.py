# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary"]
# ///
"""C51 si C52 se receptioneaza pe 27-aug-2026 (decizia ownerului), deci grupul
`Container 50-51-52` devine INTEGRAL sosit si poate fi marcat ARRIVED.

`arrivedAt` = 2026-08-27, ziua receptiei reale — NU azi. C50 sosise deja pe 25-aug.
READ-ONLY fara --apply.
"""
import subprocess, psycopg2, json, sys, datetime
KB = "/Users/gheorghebeschea/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"
def dsn(k):
    return subprocess.run(["uv","run",KB,"secret-get",k],capture_output=True,text=True).stdout.strip().split("?")[0]
APPLY = "--apply" in sys.argv
OUT = "/Users/gheorghebeschea/Downloads/Scripturi/team-intelligence/plugins/gigi/skills/tom/backups"
STAMP = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
DATA = "2026-08-27"

cn = psycopg2.connect(dsn("DATABASE_URL_TOM")); c = cn.cursor()
c.execute('''SELECT s.id, s.name, s.status::text, s."arrivedAt", count(sl.*)
             FROM shipments s LEFT JOIN shipment_lines sl ON sl."shipmentId"=s.id
             WHERE s.name = 'Container 50-51-52' GROUP BY s.id, s.name, s.status::text, s."arrivedAt"''')
row = c.fetchone()
if not row:
    print("nu gasesc expedierea"); sys.exit(1)
sid, name, st, arr, nlin = row
print("%s  status=%s  arrivedAt=%s  linii=%s  ->  ARRIVED %s" % (name, st, arr, nlin, DATA))
if not APPLY:
    cn.close(); print("(dry-run)"); sys.exit(0)

fn = "%s/tom_ship_5152_%s.json" % (OUT, STAMP)
open(fn, "w").write(json.dumps([{"id": sid, "name": name, "status": st, "arrivedAt": str(arr)}], indent=1))
c.execute('UPDATE shipments SET status=%s::"ShipmentStatus", "arrivedAt"=%s WHERE id=%s', ("ARRIVED", DATA, sid))
n = c.rowcount
cn.commit(); cn.close()
print("COMMIT: %d expediere -> %s" % (n, fn.split("/")[-1]))
