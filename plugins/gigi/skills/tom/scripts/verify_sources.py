# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary"]
# ///
"""Verifica legatura sursa->TOM pe cele 3 surse: numarul si cuid-ul trebuie sa coincida."""
import subprocess, psycopg2
KB="/Users/gheorghebeschea/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"
def dsn(k): return subprocess.run(["uv","run",KB,"secret-get",k],capture_output=True,text=True).stdout.strip().split("?")[0]
def q(k,s):
    cn=psycopg2.connect(dsn(k)); cn.set_session(readonly=True); c=cn.cursor(); c.execute(s)
    r=c.fetchall(); cn.close(); return r
tom={pid:(tn,code,sp) for pid,tn,code,sp in q("DATABASE_URL_TOM",
  'SELECT p.id,p."tomNumber",sa.code,p."sourcePoId" FROM purchase_orders p LEFT JOIN source_apps sa ON sa.id=p."sourceAppId"')}
bynum={v[0]:k for k,v in tom.items()}
src=[("GRANDIA","DATABASE_URL_GRANDIA",'SELECT number::text,"tomNumber","tomPoId" FROM po_purchase_orders WHERE "tomNumber" IS NOT NULL'),
     ("VIGO/AWBprint","DATABASE_URL_AWBPRINT","SELECT po_number,tom_number,tom_po_id FROM purchase_orders WHERE tom_number IS NOT NULL"),
     ("ARONA-BI","DATABASE_URL_ARONA_BI","SELECT id::text,tom_number,tom_po_id FROM purchase_orders WHERE tom_number IS NOT NULL")]
bad=0
for name,key,sql in src:
    rows=q(key,sql); print("\n%s: %d legaturi" % (name,len(rows)))
    for ref,num,pid in rows:
        t=tom.get(pid)
        if not t: print("   ✗ %-8s %s -> cuid inexistent in TOM" % (ref,num)); bad+=1
        elif t[0]!=num: print("   ✗ %-8s zice %s dar cuid-ul e %s" % (ref,num,t[0])); bad+=1
    print("   toate corecte" if all(tom.get(p) and tom[p][0]==n for _,n,p in rows) else "   ^ probleme")
dup=[n for n in [v[0] for v in tom.values()] if [v[0] for v in tom.values()].count(n)>1]
print("\nnumere duplicate in TOM: %s" % (sorted(set(dup)) or "niciunul"))
print("numere neconforme:      %s" % (sorted(v[0] for v in tom.values() if not v[0].startswith("TOM-") or not v[0][4:].isdigit()) or "niciunul"))
print("\nTOTAL nepotriviri sursa->TOM: %d" % bad)
