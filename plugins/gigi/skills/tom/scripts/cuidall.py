# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary"]
# ///
import subprocess, psycopg2, datetime
KB="/Users/gheorghebeschea/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"
def dsn(k): return subprocess.run(["uv","run",KB,"secret-get",k],capture_output=True,text=True).stdout.strip().split("?")[0]
cn=psycopg2.connect(dsn("DATABASE_URL_TOM")); cn.set_session(readonly=True); c=cn.cursor()
c.execute("""SELECT p."tomNumber", sa.code, p."sourcePoId", p.id
             FROM purchase_orders p LEFT JOIN source_apps sa ON sa.id=p."sourceAppId" """)
rows=sorted((int(pid[1:9],36),tn,src,sp) for tn,src,sp,pid in c.fetchall())
num=lambda tn:int(tn[4:]) if tn[4:].isdigit() else None
seq=[(i,num(tn),tn,src,sp,ts) for i,(ts,tn,src,sp) in enumerate(rows,1)]
bad=[r for r in seq if r[1] is not None and r[1]!=r[0]]
print(f"total PO: {len(seq)}   pozitii unde numarul != ordinea reala de creare: {len(bad)}")
for i,n,tn,src,sp,ts in bad:
    d=datetime.datetime.fromtimestamp(ts/1000,datetime.UTC).strftime("%Y-%m-%d %H:%M")
    print(f"  poz {i:>2} (creat {d})  are {tn:<15} {src:<9} {sp}")
print("\nfara numar valid:")
for i,n,tn,src,sp,ts in seq:
    if n is None:
        d=datetime.datetime.fromtimestamp(ts/1000,datetime.UTC).strftime("%Y-%m-%d %H:%M")
        print(f"  poz {i:>2} (creat {d})  {tn:<15} {src:<9} {sp}")
