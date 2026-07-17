# /// script
# requires-python = ">=3.10"
# dependencies = ["osmium","psycopg2-binary"]
# ///
"""Completează metrics.public.pl_addresses cu străzile Varșoviei/Mazoviei din OSM (OpenAddresses ratează ~½ Varșovia).
Adaugă (city_norm, street_norm, postcode) care nu există deja. Idempotent (șterge întâi rândurile region='OSM-MAZ')."""
import re, io, csv, unicodedata, subprocess
import osmium, psycopg2, urllib.parse as up
KB="/Users/gheorghebeschea/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"
def secret(k): return subprocess.run(["uv","run",KB,"secret-get",k],capture_output=True,text=True).stdout.strip()
_PL={ord("ł"):"l",ord("Ł"):"l"}
def strip_dia(s):
    s=(s or "").translate(_PL); s=unicodedata.normalize("NFD",s)
    return "".join(c for c in s if unicodedata.category(c)!="Mn")
def norm(s):
    s=strip_dia(s).lower(); s=re.sub(r"[^a-z0-9 ]+"," ",s); return re.sub(r"\s+"," ",s).strip()
def pcn(p):
    d=re.sub(r"\D","",p or ""); return f"{d[:2]}-{d[2:5]}" if len(d)==5 else ""
_PREF=re.compile(r"^(ul|al|pl|os|aleja|ulica|plac|osiedle)\.?\s+",re.I)

class H(osmium.SimpleHandler):
    def __init__(s): super().__init__(); s.agg={}
    def _a(s,t):
        st=t.get("addr:street"); city=t.get("addr:city"); hn=t.get("addr:housenumber")
        if not (st and city): return
        pc=pcn(t.get("addr:postcode"))
        n=None; m=re.match(r"(\d{1,4})",hn or "")
        if m: n=int(m.group(1))
        stc=_PREF.sub("",st).strip()
        key=(city.strip(),stc,pc); a=s.agg.get(key)
        odd = n is not None and n%2==1; even=n is not None and n%2==0
        if a is None: s.agg[key]=[n,n,odd,even,1]
        else:
            a[4]+=1
            if n is not None:
                if a[0] is None or n<a[0]: a[0]=n
                if a[1] is None or n>a[1]: a[1]=n
            a[2]=a[2] or odd; a[3]=a[3] or even
    def node(s,n): s._a(n.tags)
    def way(s,w): s._a(w.tags)
    def area(s,a): s._a(a.tags)
h=H(); h.apply_file("mazowieckie.osm.pbf")
print("OSM Mazowieckie chei (city,street,pc):",len(h.agg))

dsn=secret("DATABASE_URL_METRICS"); pp=up.urlsplit(dsn)
cn=psycopg2.connect(up.urlunsplit((pp.scheme,pp.netloc,pp.path,"",""))); cn.autocommit=False; cur=cn.cursor()
cur.execute("SELECT count(*) FROM public.pl_addresses WHERE city_norm='warszawa'"); print("Varsovia INAINTE:",cur.fetchone()[0],"chei")
cur.execute("DELETE FROM public.pl_addresses WHERE region='OSM-MAZ'")
cur.execute("SELECT DISTINCT city_norm, street_norm, postcode FROM public.pl_addresses WHERE city_norm IN (SELECT DISTINCT city_norm FROM public.pl_addresses)")
# incarca setul existent doar pt orasele din Mazovia (evit sa tin 320k in ram inutil): ia toate, e ok
cur.execute("SELECT city_norm||'|'||street_norm||'|'||postcode FROM public.pl_addresses")
seen=set(r[0] for r in cur.fetchall())
buf=io.StringIO(); w=csv.writer(buf,delimiter="\t"); added=0
for (city,street,pc),(nmin,nmax,odd,even,cnt) in h.agg.items():
    cnn=norm(city); snn=norm(street)
    if not (cnn and snn): continue
    k=f"{cnn}|{snn}|{pc}"
    if k in seen: continue
    seen.add(k)
    w.writerow(["OSM-MAZ","",city,street,pc,nmin if nmin is not None else "",nmax if nmax is not None else "",
                "t" if odd else "f","t" if even else "f",cnt,cnn,snn]); added+=1
buf.seek(0)
cur.copy_expert("COPY public.pl_addresses FROM STDIN WITH (FORMAT csv, DELIMITER E'\\t', NULL '')",buf)
cn.commit()
cur.execute("SELECT count(*) FROM public.pl_addresses WHERE city_norm='warszawa'"); print("Varsovia DUPA:",cur.fetchone()[0],"chei")
cur.execute("SELECT 1 FROM public.pl_addresses WHERE city_norm='warszawa' AND street_norm='marszalkowska' LIMIT 1")
print("Marszalkowska Varsovia acum:", "GASITA" if cur.fetchone() else "inca lipsa")
print("adaugat %d chei OSM"%added); cn.close(); print("DONE")
