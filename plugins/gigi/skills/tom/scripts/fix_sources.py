# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary"]
# ///
"""Repara cele 3 erori din SURSE (nu din TOM), fiecare in tranzactie proprie, cu backup JSON."""
import subprocess, psycopg2, json, sys, datetime
KB = "/Users/gheorghebeschea/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"
def dsn(k):
    return subprocess.run(["uv","run",KB,"secret-get",k],capture_output=True,text=True).stdout.strip().split("?")[0]
STAMP = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
OUT = "/Users/gheorghebeschea/Downloads/Scripturi/team-intelligence/plugins/gigi/skills/tom/backups"
APPLY = "--apply" in sys.argv
NEW = "TOM-067"
PID = "cmor4t9ds0001l504t7na52cs"

NOTE = ("Renumerotat " + NEW + " la 2026-08-26: TOM emisese TOM-009 de doua ori "
        "(28-apr Grandia PO#10, 04-mai VIGO PO-0007). Restaurarea a parcat acest PO "
        "ca TOM-REC-na52cs. Numarul istoric emis a fost TOM-009.")

def run(key, label, backup_sql, stmts):
    cn = psycopg2.connect(dsn(key)); c = cn.cursor()
    c.execute(backup_sql); cols = [d[0] for d in c.description]
    rows = [dict(zip(cols, map(str, r))) for r in c.fetchall()]
    print("\n=== %s  (backup: %d randuri)" % (label, len(rows)))
    for r in rows: print("   ", r)
    if not APPLY:
        cn.close(); return
    fn = "%s/src_%s_%s.json" % (OUT, label, STAMP)
    open(fn, "w").write(json.dumps(rows, indent=1, ensure_ascii=False))
    tot = 0
    for s in stmts:
        c.execute(s); tot += c.rowcount
        print("    %2d rand(uri): %s..." % (c.rowcount, s[:60]))
    cn.commit(); cn.close()
    print("    COMMIT %d randuri -> %s" % (tot, fn.split("/")[-1]))

# 1. TOM: numar valid in locul placeholder-ului pus de restaurare
run("DATABASE_URL_TOM", "tom_renumber",
    'SELECT id,"tomNumber",notes FROM purchase_orders WHERE id=%r' % PID,
    ['UPDATE purchase_orders SET "tomNumber"=%r, notes=coalesce(notes||chr(10),%r)||%r WHERE id=%r' % (NEW, "", NOTE, PID)])

# 2. AWBprint: legatura pe cuid e corecta, doar numarul era cel duplicat
run("DATABASE_URL_AWBPRINT", "awbprint_po0007",
    "SELECT po_number,tom_number,tom_po_id FROM purchase_orders WHERE po_number='PO-0007'",
    ["UPDATE purchase_orders SET tom_number=%r WHERE po_number='PO-0007' AND tom_po_id=%r" % (NEW, PID)])

# 3. ARONA-BI: doua PO-uri ANULATE care revendica numere ale altora si nu exista in TOM
run("DATABASE_URL_ARONA_BI", "aronabi_7_8",
    """SELECT p.id,p.status::text,p.tom_number,p.tom_po_id,
              (SELECT count(*) FROM purchase_order_items i WHERE i.purchase_order_id=p.id) AS linii
       FROM purchase_orders p WHERE p.id IN (7,8)""",
    ["UPDATE purchase_order_items SET tom_item_id=NULL, tom_status=NULL WHERE purchase_order_id IN (7,8)",
     "UPDATE purchase_orders SET tom_number=NULL, tom_po_id=NULL, tom_status=NULL WHERE id IN (7,8)"])
