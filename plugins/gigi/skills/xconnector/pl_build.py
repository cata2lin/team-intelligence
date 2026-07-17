# /// script
# requires-python = ">=3.10"
# dependencies = ["remotezip>=0.12", "psycopg2-binary", "requests"]
# ///
"""Construieste metrics.public.pl_addresses din OpenAddresses PL (16 CSV/voievodat, deriv din GUGiK PRG).
Model RO: ZIP-driven cu paritate. Agrega la (region,powiat,city,street,postcode)+interval numar+paritate+cnt.
Trage DOAR pl/*.csv prin HTTP range (remotezip), nu toata Europa. COPY bulk in metrics."""
import re, io, csv, sys, time, subprocess, unicodedata
import requests, psycopg2, urllib.parse as up
from remotezip import RemoteZip
KB="/Users/gheorghebeschea/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"
def secret(k): return subprocess.run(["uv","run",KB,"secret-get",k],capture_output=True,text=True).stdout.strip()

_PL={ord("ł"):"l",ord("Ł"):"l"}
def strip_dia(s):
    s=(s or "").translate(_PL)
    s=unicodedata.normalize("NFD",s)
    return "".join(c for c in s if unicodedata.category(c)!="Mn")
def norm(s):
    s=strip_dia(s).lower()
    s=re.sub(r"[^a-z0-9 ]+"," ",s)
    return re.sub(r"\s+"," ",s).strip()
def pc_norm(p):
    d=re.sub(r"\D","",p or "")
    return f"{d[:2]}-{d[2:5]}" if len(d)==5 else ""
def house_int(num):
    m=re.match(r"\s*(\d{1,4})",num or "")
    return int(m.group(1)) if m else None

init="https://data.openaddresses.io/openaddr-collected-europe.zip"
signed=requests.get(init,allow_redirects=False,timeout=30).headers["location"]
MEMBERS=["zachodniopomorskie","podkarpackie","wielkopolskie","slaskie","malopolskie","mazowieckie",
 "opolskie","podlaskie","lubuskie","swietokrzyskie","kujawsko-pomorskie","dolnoslaskie","lubelskie",
 "pomorskie","lodzkie","warminsko-mazurskie"]

agg={}   # key -> [nmin,nmax,odd,even,cnt]
rows=0; t0=time.time()
with RemoteZip(signed) as z:
    for w in MEMBERS:
        member=f"pl/{w}.csv"; wr=0
        with z.open(member) as f:
            r=csv.reader(io.TextIOWrapper(f,encoding="utf-8",errors="replace"))
            header=next(r,None)
            for row in r:
                if len(row)<9: continue
                num,street,city,district,region,postcode=row[2],row[3],row[5],row[6],row[7],row[8]
                if not (city and (street or postcode)): continue
                rows+=1; wr+=1
                n=house_int(num); pc=pc_norm(postcode)
                key=(region.strip(),district.strip(),city.strip(),street.strip(),pc)
                a=agg.get(key)
                odd = n is not None and n%2==1
                even= n is not None and n%2==0
                if a is None:
                    agg[key]=[n,n,odd,even,1]
                else:
                    a[4]+=1
                    if n is not None:
                        if a[0] is None or n<a[0]: a[0]=n
                        if a[1] is None or n>a[1]: a[1]=n
                    a[2]=a[2] or odd; a[3]=a[3] or even
        print(f"  {w:22} {wr:>8,} pts | total {rows:,} | {len(agg):,} chei | {time.time()-t0:.0f}s",flush=True)
print(f"TOTAL {rows:,} puncte -> {len(agg):,} chei agregate ({time.time()-t0:.0f}s)",flush=True)

dsn=secret("DATABASE_URL_METRICS"); p=up.urlsplit(dsn)
cn=psycopg2.connect(up.urlunsplit((p.scheme,p.netloc,p.path,"",""))); cn.autocommit=False; cur=cn.cursor()
cur.execute("DROP TABLE IF EXISTS public.pl_addresses")
cur.execute("""CREATE TABLE public.pl_addresses(
  region text, powiat text, city text, street text, postcode text,
  num_min int, num_max int, has_odd bool, has_even bool, cnt int,
  city_norm text, street_norm text)""")
buf=io.StringIO(); w=csv.writer(buf,delimiter="\t")
for (region,powiat,city,street,pc),(nmin,nmax,odd,even,cnt) in agg.items():
    w.writerow([region,powiat,city,street,pc,
                nmin if nmin is not None else "", nmax if nmax is not None else "",
                "t" if odd else "f","t" if even else "f",cnt,norm(city),norm(street)])
buf.seek(0)
cur.copy_expert("COPY public.pl_addresses FROM STDIN WITH (FORMAT csv, DELIMITER E'\\t', NULL '')",buf)
cur.execute("CREATE INDEX ix_pl_loc ON public.pl_addresses(city_norm, street_norm)")
cur.execute("CREATE INDEX ix_pl_pc  ON public.pl_addresses(postcode)")
cn.commit()
for q,l in [("SELECT count(*) FROM public.pl_addresses","randuri"),
            ("SELECT count(DISTINCT postcode) FROM public.pl_addresses WHERE postcode<>''","coduri postale"),
            ("SELECT count(*) FROM public.pl_addresses WHERE street<>''","chei cu strada"),
            ("SELECT count(DISTINCT city_norm) FROM public.pl_addresses","localitati")]:
    cur.execute(q); print(f"{l}: {cur.fetchone()[0]:,}",flush=True)
cn.close(); print("DONE",flush=True)
