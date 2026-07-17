# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary"]
# ///
"""Construiește metrics.public.cz_addresses din RÚIAN (6258 CSV-uri cp1250). Agregă la (obec, district, cast_obce,
ulice, psc) + interval numar (orientační dacă există, altfel domovní) + count. Normalizează pt căutare. COPY bulk."""
import os, io, glob, csv, unicodedata, subprocess
import psycopg2, urllib.parse as up
KB="/Users/gheorghebeschea/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"
def secret(k): return subprocess.run(["uv","run",KB,"secret-get",k],capture_output=True,text=True).stdout.strip()
CSVDIR="/private/tmp/claude-501/-Users-gheorghebeschea-Downloads-Scripturi/7b10a6e2-9830-47d9-9ea1-eeffacb4cbb4/scratchpad/cz/extracted"

def strip_dia(s):
    if not s: return ""
    s=unicodedata.normalize("NFD",s)
    return "".join(ch for ch in s if unicodedata.category(ch)!="Mn")
def norm(s):
    import re
    s=strip_dia(s or "").lower()
    s=re.sub(r"[^a-z0-9 ]+"," ",s)
    return re.sub(r"\s+"," ",s).strip()

files=glob.glob(os.path.join(CSVDIR,"**","*.csv"),recursive=True)
print("fișiere:",len(files))
agg={}  # key -> [num_min, num_max, cnt]
rows_read=0
for i,fp in enumerate(files):
    with open(fp,encoding="cp1250") as f:
        r=csv.reader(f,delimiter=";")
        header=next(r,None)
        for row in r:
            if len(row)<16: continue
            rows_read+=1
            obec=row[2].strip(); momc=row[4].strip(); obvod=row[6].strip()
            cast=row[8].strip(); ulice=row[10].strip()
            cislo_dom=row[12].strip(); cislo_or=row[13].strip(); psc=row[15].strip().replace(" ","")
            if not (obec and psc.isdigit() and len(psc)==5): continue
            district=momc or obvod
            numtxt=cislo_or or cislo_dom
            n=None
            m=numtxt.split("/")[0]
            digits="".join(c for c in m if c.isdigit())
            if digits: n=int(digits)
            key=(obec,district,cast,ulice,psc)
            a=agg.get(key)
            if a is None:
                agg[key]=[n,n,1]
            else:
                a[2]+=1
                if n is not None:
                    if a[0] is None or n<a[0]: a[0]=n
                    if a[1] is None or n>a[1]: a[1]=n
    if (i+1)%1500==0: print("  ...%d fișiere, %d rânduri, %d chei"%(i+1,rows_read,len(agg)))
print("TOTAL: %d rânduri citite → %d chei agregate"%(rows_read,len(agg)))

# COPY în metrics
dsn=secret("DATABASE_URL_METRICS"); p=up.urlsplit(dsn)
cn=psycopg2.connect(up.urlunsplit((p.scheme,p.netloc,p.path,"",""))); cn.autocommit=False; cur=cn.cursor()
cur.execute("DROP TABLE IF EXISTS public.cz_addresses")
cur.execute("""CREATE TABLE public.cz_addresses(
  obec text, district text, cast_obce text, ulice text, psc text,
  num_min int, num_max int, cnt int, obec_norm text, ulice_norm text)""")
buf=io.StringIO()
w=csv.writer(buf,delimiter="\t",quoting=csv.QUOTE_MINIMAL)
for (obec,district,cast,ulice,psc),(nmin,nmax,cnt) in agg.items():
    w.writerow([obec,district,cast,ulice,psc, nmin if nmin is not None else "", nmax if nmax is not None else "",
                cnt, norm(obec), norm(ulice)])
buf.seek(0)
cur.copy_expert("COPY public.cz_addresses FROM STDIN WITH (FORMAT csv, DELIMITER E'\\t', NULL '')",buf)
cur.execute("CREATE INDEX ix_cz_psc ON public.cz_addresses(psc)")
cur.execute("CREATE INDEX ix_cz_loc ON public.cz_addresses(obec_norm, ulice_norm)")
cn.commit()
cur.execute("SELECT count(*) FROM public.cz_addresses"); print("încărcate în cz_addresses:",cur.fetchone()[0])
cur.execute("SELECT count(DISTINCT psc) FROM public.cz_addresses"); print("PSČ distincte:",cur.fetchone()[0])
cur.execute("SELECT count(*) FROM public.cz_addresses WHERE ulice<>''"); print("chei CU stradă:",cur.fetchone()[0])
cn.close()
print("DONE")
