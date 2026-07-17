# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary"]
# ///
"""Completează metrics.public.bg_localities cu codurile poștale GeoNames BG (4359 coduri vs OSM 1209).
Adăugare idempotentă (source='geonames'): (name_norm, postcode) care nu există deja. Îmbunătățește corecția
'oraș din cod postal' + acoperirea localităților. Rerulabil (șterge întâi rândurile geonames)."""
import re, io, csv, unicodedata, subprocess
import psycopg2, urllib.parse as up
KB="/Users/gheorghebeschea/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"
def secret(k): return subprocess.run(["uv","run",KB,"secret-get",k],capture_output=True,text=True).stdout.strip()
_TR={'а':'a','б':'b','в':'v','г':'g','д':'d','е':'e','ж':'zh','з':'z','и':'i','й':'y','к':'k','л':'l','м':'m',
'н':'n','о':'o','п':'p','р':'r','с':'s','т':'t','у':'u','ф':'f','х':'h','ц':'ts','ч':'ch','ш':'sh','щ':'sht',
'ъ':'a','ь':'y','ю':'yu','я':'ya'}
def translit(s): return "".join(_TR.get(c,c) for c in (s or "").lower())
def norm_cyr(s):
    s=(s or "").lower(); s=re.sub(r"[^0-9a-zа-я ]+"," ",s); return re.sub(r"\s+"," ",s).strip()
def norm_lat(s):
    s=translit(s); s=unicodedata.normalize("NFD",s); s="".join(c for c in s if unicodedata.category(c)!="Mn")
    s=re.sub(r"[^a-z0-9 ]+"," ",s); return re.sub(r"\s+"," ",s).strip()

rows=[]
with open("geonames_bg/BG.txt",encoding="utf-8") as f:
    for line in f:
        p=line.rstrip("\n").split("\t")
        if len(p)<4: continue
        pc=p[1].strip(); nm=p[2].split("/")[0].strip()   # 'Айтос / Ajtos' -> 'Айтос'
        if re.fullmatch(r"\d{4}",pc) and nm:
            rows.append((nm,pc))
print("GeoNames randuri utile:",len(rows))

dsn=secret("DATABASE_URL_METRICS"); pp=up.urlsplit(dsn)
cn=psycopg2.connect(up.urlunsplit((pp.scheme,pp.netloc,pp.path,"",""))); cn.autocommit=False; cur=cn.cursor()
cur.execute("SELECT count(*), count(DISTINCT postcode) FILTER (WHERE postcode<>'') FROM public.bg_localities")
b_all,b_pc=cur.fetchone(); print("INAINTE: %d randuri, %d coduri postale"%(b_all,b_pc))
cur.execute("DELETE FROM public.bg_localities WHERE place_type='geonames'")  # idempotent
cur.execute("SELECT DISTINCT name_norm, postcode FROM public.bg_localities")
seen=set((a,b) for a,b in cur.fetchall())
buf=io.StringIO(); w=csv.writer(buf,delimiter="\t"); added=0
for nm,pc in rows:
    nn=norm_cyr(nm)
    if not nn or (nn,pc) in seen: continue
    seen.add((nn,pc))
    w.writerow([nm,nn,norm_lat(nm),"geonames",pc,1]); added+=1
buf.seek(0)
cur.copy_expert("COPY public.bg_localities FROM STDIN WITH (FORMAT csv, DELIMITER E'\\t', NULL '')",buf)
cn.commit()
cur.execute("SELECT count(*), count(DISTINCT postcode) FILTER (WHERE postcode<>'') FROM public.bg_localities")
a_all,a_pc=cur.fetchone()
print("ADAUGAT: %d randuri geonames"%added)
print("DUPA: %d randuri, %d coduri postale (%+d coduri)"%(a_all,a_pc,a_pc-b_pc))
cn.close(); print("DONE")
