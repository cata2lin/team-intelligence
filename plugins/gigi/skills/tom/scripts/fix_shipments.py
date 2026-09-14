# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary"]
# ///
"""Pune starea REALA pe expedierile din TOM (erau toate DRAFT, fara nicio data).

Adevarul containerelor (owner, 26-aug-2026): au sosit toate pana la C50, exceptie C49.
Datele de receptie vin din NUMELE taburilor din sheet-ul 'Tom - receptii containere'.

Marchez ARRIVED doar grupurile INTEGRAL sosite; `arrivedAt` = data ultimului container din grup.
Grupurile mixte si cele nesosite raman neatinse — IN_TRANSIT ar insemna „a plecat", iar asta
nu-l stim din foaie. READ-ONLY fara --apply.
"""
import subprocess, psycopg2, json, sys, datetime, re
KB = "/Users/gheorghebeschea/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"
def dsn(k):
    return subprocess.run(["uv","run",KB,"secret-get",k],capture_output=True,text=True).stdout.strip().split("?")[0]
APPLY = "--apply" in sys.argv
OUT = "/Users/gheorghebeschea/Downloads/Scripturi/team-intelligence/plugins/gigi/skills/tom/backups"
STAMP = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

# data de receptie per container, din titlurile taburilor (vezi containers.py)
DATE = {37:"2026-06-16",38:"2026-06-12",39:"2026-07-03",40:"2026-07-02",41:"2026-07-09",42:"2026-07-08",
        43:"2026-07-27",44:"2026-07-25",45:"2026-07-27",46:"2026-08-04",47:"2026-08-05",48:"2026-08-05",
        50:"2026-08-25"}
sosit = lambda n: n <= 50 and n != 49

cn = psycopg2.connect(dsn("DATABASE_URL_TOM")); c = cn.cursor()
c.execute('SELECT id, name, status::text, "arrivedAt" FROM shipments ORDER BY name')
plan, sarite = [], []
for sid, name, st, arr in c.fetchall():
    nums = [int(x) for x in re.findall(r"\d+", name or "")]
    # "Container 37-40" e un INTERVAL (37,38,39,40), nu doua numere; "43-44-45" e o lista
    if len(nums) == 2 and nums[1] - nums[0] > 1:
        nums = list(range(nums[0], nums[1] + 1))
    if not nums:
        sarite.append((name, "fara numar de container")); continue
    s_ = [sosit(n) for n in nums]
    if all(s_):
        d = max((DATE[n] for n in nums if n in DATE), default=None)
        if d is None:
            sarite.append((name, "sosit dar fara data")); continue
        plan.append((sid, name, st, d))
    elif any(s_):
        sarite.append((name, "MIXT: %s sosite, %s nu" % ([n for n in nums if sosit(n)], [n for n in nums if not sosit(n)])))
    else:
        sarite.append((name, "inca nu a sosit"))

print("DE MARCAT ARRIVED: %d" % len(plan))
for _, name, st, d in plan: print("   %-22s %-6s -> ARRIVED  sosit %s" % (name, st, d))
print("\nNEATINSE: %d" % len(sarite))
for name, why in sarite: print("   %-22s %s" % (name, why))

if not APPLY:
    cn.close(); print("\n(dry-run)"); sys.exit(0)

bak = [{"id": s, "name": n, "status": st, "arrivedAt": None} for s, n, st, _ in plan]
fn = "%s/tom_shipments_%s.json" % (OUT, STAMP)
open(fn, "w").write(json.dumps(bak, indent=1, ensure_ascii=False))
n = 0
for sid, name, st, d in plan:
    c.execute('UPDATE shipments SET status=%s::"ShipmentStatus", "arrivedAt"=%s WHERE id=%s AND status::text=%s', ("ARRIVED", d, sid, st))
    n += c.rowcount
cn.commit(); cn.close()
print("\nCOMMIT: %d expedieri -> %s" % (n, fn.split("/")[-1]))
