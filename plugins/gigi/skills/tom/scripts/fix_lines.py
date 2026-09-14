# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary"]
# ///
"""Repara liniile pe care restaurarea le-a dat un pas inapoi fata de JURNAL.

Autoritatea = `po_item_events`: 18 linii (TOM-021, TOM-039) au ultimul eveniment SHIPPED
(9-aug-2026, lant complet NEW→ORDERED→RECEIVED→SHIPPED) dar statusul ramas RECEIVED si
`shippedQty`=0, desi `shippedAt` era pastrat. Restul bazei e in acord cu jurnalul.

Conventie verificata pe cele 56 de linii deja SHIPPED din aceleasi doua PO-uri: shippedQty = receivedQty (56/56).
READ-ONLY fara --apply.
"""
import subprocess, psycopg2, json, sys, datetime, collections
KB = "/Users/gheorghebeschea/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"
def dsn(k):
    return subprocess.run(["uv","run",KB,"secret-get",k],capture_output=True,text=True).stdout.strip().split("?")[0]
APPLY = "--apply" in sys.argv
OUT = "/Users/gheorghebeschea/Downloads/Scripturi/team-intelligence/plugins/gigi/skills/tom/backups"
STAMP = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

def derive(sts):
    act = [s for s in sts if s != "CANCELLED"]
    if not act: return "CANCELLED"
    has = lambda *v: any(s in v for s in act)
    allis = lambda *v: all(s in v for s in act)
    if allis("SHIPPED"): return "SHIPPED"
    if has("SHIPPED", "PARTIALLY_SHIPPED"): return "PARTIALLY_SHIPPED"
    if allis("RECEIVED"): return "RECEIVED"
    if has("RECEIVED", "PARTIALLY_RECEIVED"): return "PARTIALLY_RECEIVED"
    if allis("ORDERED"): return "ORDERED"
    return "NEW"

SEL = '''WITH ult AS (
  SELECT DISTINCT ON (e."poItemId") e."poItemId", e."toStatus"::text AS ev
  FROM po_item_events e WHERE e."toStatus" IS NOT NULL
  ORDER BY e."poItemId", e."createdAt" DESC, e.id DESC)
SELECT i.id, p."tomNumber", i."externalSku", i.status::text, u.ev, i."receivedQty", i."shippedQty"
FROM purchase_order_items i JOIN ult u ON u."poItemId"=i.id JOIN purchase_orders p ON p.id=i."poId"
WHERE i.status::text <> u.ev'''

cn = psycopg2.connect(dsn("DATABASE_URL_TOM")); c = cn.cursor()
c.execute(SEL); rows = c.fetchall()
print("linii in dezacord cu jurnalul: %d" % len(rows))
by = collections.Counter((r[3], r[4]) for r in rows)
for (cur, ev), n in by.items(): print("   %-10s -> %-10s %d linii" % (cur, ev, n))
if any(ev != "SHIPPED" for _, _, _, _, ev, _, _ in rows):
    print("STOP: exista tranzitii pe care scriptul asta nu le acopera."); sys.exit(1)
for r in rows[:20]:
    print("   %-9s %-24s %s -> %s  (primit %s)" % (r[1], r[2], r[3], r[4], r[5]))
if not APPLY:
    cn.close(); print("\n(dry-run)"); sys.exit(0)

bak = [{"id": r[0], "tomNumber": r[1], "sku": r[2], "status": r[3], "shippedQty": r[6]} for r in rows]
fn = "%s/tom_linii_jurnal_%s.json" % (OUT, STAMP)
open(fn, "w").write(json.dumps(bak, indent=1, ensure_ascii=False))
n = 0
for rid, tn, sku, cur, ev, recv, shp in rows:
    c.execute('UPDATE purchase_order_items SET status=%s::"PoItemStatus", "shippedQty"=%s WHERE id=%s AND status::text=%s', (ev, recv, rid, cur))
    n += c.rowcount

# antetele celor doua PO-uri, cu aceeasi regula ca la reparatia anterioara
pos = sorted({r[1] for r in rows})
h = 0
for tn in pos:
    c.execute('''SELECT p.id, p.status::text, array_agg(i.status::text)
                 FROM purchase_orders p JOIN purchase_order_items i ON i."poId"=p.id
                 WHERE p."tomNumber"=%s GROUP BY p.id, p.status::text''', (tn,))
    pid, cur, sts = c.fetchone()
    d = derive([s for s in sts if s])
    if d != cur:
        c.execute('UPDATE purchase_orders SET status=%s::"PoStatus" WHERE id=%s', (d, pid)); h += c.rowcount
        print("   antet %s: %s -> %s" % (tn, cur, d))
    else:
        print("   antet %s: ramane %s" % (tn, cur))
cn.commit(); cn.close()
print("\nCOMMIT: %d linii + %d antete -> %s" % (n, h, fn.split("/")[-1]))
