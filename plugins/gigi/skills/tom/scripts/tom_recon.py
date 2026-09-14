# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary"]
# ///
"""Reconciliere TOM dupa restaurare. READ-ONLY: propune corectii, nu scrie nimic."""
import subprocess, psycopg2, collections
KB="/Users/gheorghebeschea/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"
def dsn(k): return subprocess.run(["uv","run",KB,"secret-get",k],capture_output=True,text=True).stdout.strip().split("?")[0]
def q(key, sql):
    cn=psycopg2.connect(dsn(key)); cn.set_session(readonly=True); c=cn.cursor()
    c.execute(sql); r=c.fetchall(); cn.close(); return r

# 1. starea din TOM
apps={code:i for i,code in q("DATABASE_URL_TOM","SELECT id,code FROM source_apps")}
tom={}
for tn,src,spid,st,pid in q("DATABASE_URL_TOM","""
        SELECT po."tomNumber", sa.code, po."sourcePoId", po.status::text, po.id
        FROM purchase_orders po LEFT JOIN source_apps sa ON sa.id=po."sourceAppId" """):
    tom[tn]=dict(src=src, spid=spid, st=st, pid=pid)

# 2. ce spune fiecare sursa ca a trimis:  tom_number -> (cod_sursa, id_corect_in_sursa)
adevar={}
for tn,sid in q("DATABASE_URL_SCENTUM","SELECT tom_number, number FROM purchase_orders WHERE tom_number IS NOT NULL"):
    adevar.setdefault(tn,[]).append(("SCENTUM",sid))
for tn,sid in q("DATABASE_URL_GRANDIA",'SELECT "tomNumber", id::text FROM po_purchase_orders WHERE "tomNumber" IS NOT NULL'):
    adevar.setdefault(tn,[]).append(("GRANDIA",sid))
for tn,sid in q("DATABASE_URL_AWBPRINT","SELECT tom_number, po_number FROM purchase_orders WHERE tom_number IS NOT NULL"):
    adevar.setdefault(tn,[]).append(("VIGO",sid))
for tn,sid in q("DATABASE_URL_ARONA_BI","SELECT tom_number, id::text FROM purchase_orders WHERE tom_number IS NOT NULL"):
    adevar.setdefault(tn,[]).append(("ARONA-BI",sid))

gresite, lipsa, conflicte, orfane = [], [], [], []
for tn,cl in sorted(adevar.items()):
    if len(cl)>1: conflicte.append((tn,cl)); continue
    src,sid = cl[0]
    if tn not in tom: lipsa.append((tn,src,sid)); continue
    t=tom[tn]
    if t["src"]!=src or t["spid"]!=sid:
        gresite.append((tn,t["src"],t["spid"],src,sid,t["pid"],t["st"]))
for tn,t in sorted(tom.items()):
    if tn not in adevar: orfane.append((tn,t["src"],t["spid"],t["st"]))

print("="*74)
print(f"A. ATRIBUIRE GRESITA — {len(gresite)} PO-uri")
print("="*74)
for tn,ts,tsp,cs,csp,pid,st in gresite:
    print(f"  {tn:<10} TOM zice {ts:<9} / {str(tsp)[:26]:<26} → corect {cs} / {csp}   [{st}]")
print(f"\n{'='*74}\nB. LIPSESC DIN TOM — {len(lipsa)} PO-uri\n{'='*74}")
for tn,src,sid in lipsa: print(f"  {tn:<10} exista in {src}, id {sid} — de recreat")
print(f"\n{'='*74}\nC. ORFANE IN TOM (nicio sursa nu le revendica) — {len(orfane)}\n{'='*74}")
for tn,src,spid,st in orfane: print(f"  {tn:<18} atribuit {src:<9} / {str(spid)[:26]:<26} [{st}]")
print(f"\n{'='*74}\nD. REVENDICATE DE 2 SURSE — {len(conflicte)}\n{'='*74}")
for tn,cl in conflicte:
    t=tom.get(tn,{})
    print(f"  {tn:<10} revendicat de {', '.join(f'{s}(id {i})' for s,i in cl)} | TOM are {t.get('src')} / {t.get('spid')}")

print(f"\n{'='*74}\nSQL PROPUS (NEEXECUTAT) — corectarea atribuirii\n{'='*74}")
for tn,ts,tsp,cs,csp,pid,st in gresite:
    print(f"""UPDATE purchase_orders SET "sourceAppId"='{apps.get(cs)}', "sourcePoId"='{csp}' WHERE id='{pid}';  -- {tn}: {ts}→{cs}""")
