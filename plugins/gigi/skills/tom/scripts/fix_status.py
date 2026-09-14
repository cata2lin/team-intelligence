# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary"]
# ///
"""Reface ANTETUL PO-urilor TOM dupa restaurare: status + orderedAt + cancelledAt.

Restaurarea a pastrat LINIILE (status + cantitati) si JURNALUL de evenimente, dar a resetat
antetul: status=NEW pe tot, orderedAt=NULL pe toate cele 67.

  status      <- dedus din statusul liniilor. Regula reprodusa din cele 23 de PO-uri cu status
                 intact (23/23) si confirmata independent de `tom_status` din Scentum (4/4).
  orderedAt   <- primul eveniment ORDERED al PO-ului (po_item_events)
  cancelledAt <- ultimul eveniment CANCELLED, doar pt PO-urile care ies CANCELLED

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
    if not act:
        return "CANCELLED"
    has = lambda *v: any(s in v for s in act)
    allis = lambda *v: all(s in v for s in act)
    if allis("SHIPPED"):                        return "SHIPPED"
    if has("SHIPPED", "PARTIALLY_SHIPPED"):     return "PARTIALLY_SHIPPED"
    if allis("RECEIVED"):                       return "RECEIVED"
    if has("RECEIVED", "PARTIALLY_RECEIVED"):   return "PARTIALLY_RECEIVED"
    if allis("ORDERED"):                        return "ORDERED"
    return "NEW"

cn = psycopg2.connect(dsn("DATABASE_URL_TOM")); c = cn.cursor()
c.execute('''SELECT p.id, p."tomNumber", p.status::text, p."orderedAt", p."cancelledAt",
                    array_agg(i.status::text)
             FROM purchase_orders p LEFT JOIN purchase_order_items i ON i."poId"=p.id
             GROUP BY p.id, p."tomNumber", p.status::text, p."orderedAt", p."cancelledAt"''')
rows = c.fetchall()

c.execute('''SELECT i."poId", min(e."createdAt") FILTER (WHERE e."toStatus"::text='ORDERED'),
                    max(e."createdAt") FILTER (WHERE e."toStatus"::text='CANCELLED')
             FROM po_item_events e JOIN purchase_order_items i ON i.id=e."poItemId" GROUP BY i."poId"''')
ev = {p: (o, x) for p, o, x in c.fetchall()}

# validare: regula trebuie sa reproduca statusurile care au supravietuit
mart = [r for r in rows if r[2] != "NEW"]
gres = [(tn, cur, derive([s for s in sts if s])) for _, tn, cur, _, _, sts in mart
        if derive([s for s in sts if s]) != cur]
print("VALIDARE regula: %d/%d statusuri intacte reproduse%s"
      % (len(mart) - len(gres), len(mart), "" if not gres else "   <-- NU APLICA"))
for tn, cur, d in gres: print("   x %s: real=%s dedus=%s" % (tn, cur, d))
if gres: sys.exit(1)

plan = []
for pid, tn, cur, oat, cat, sts in sorted(rows, key=lambda r: r[1]):
    sts = [s for s in sts if s]
    if not sts: continue
    d = derive(sts)
    o, x = ev.get(pid, (None, None))
    new_o = o if oat is None and d != "NEW" else None
    new_c = x if cat is None and d == "CANCELLED" else None
    if d != cur or new_o or new_c:
        plan.append((pid, tn, cur, d, new_o, new_c, collections.Counter(sts)))

print("\n%-9s %-6s -> %-19s %-12s %-12s %s" % ("PO","acum","status corect","comandat","anulat","linii"))
for _, tn, cur, d, o, x, cnt in plan:
    print("%-9s %-6s -> %-19s %-12s %-12s %s" % (tn, cur, d if d != cur else "(neschimbat)",
          o.date() if o else "-", x.date() if x else "-", dict(cnt)))
print("\nPO-uri atinse: %d   status schimbat: %d   orderedAt completat: %d   cancelledAt completat: %d"
      % (len(plan), sum(1 for p in plan if p[2] != p[3]), sum(1 for p in plan if p[4]), sum(1 for p in plan if p[5])))
print("distributie noua:", dict(collections.Counter(
    derive([s for s in r[5] if s]) if [s for s in r[5] if s] else r[2] for r in rows)))

if not APPLY:
    cn.close(); sys.exit(0)

bak = [{"id": p, "tomNumber": t, "status": cur, "orderedAt": None, "cancelledAt": None} for p, t, cur, _, _, _, _ in plan]
fn = "%s/tom_status_%s.json" % (OUT, STAMP)
open(fn, "w").write(json.dumps(bak, indent=1, ensure_ascii=False))
n = 0
for pid, tn, cur, d, o, x, _ in plan:
    c.execute('UPDATE purchase_orders SET status=%s::"PoStatus", "orderedAt"=coalesce("orderedAt",%s), "cancelledAt"=coalesce("cancelledAt",%s) WHERE id=%s', (d, o, x, pid))
    n += c.rowcount
cn.commit(); cn.close()
print("\nCOMMIT: %d randuri -> %s" % (n, fn.split("/")[-1]))
