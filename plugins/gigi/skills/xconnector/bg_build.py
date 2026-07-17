# /// script
# requires-python = ">=3.10"
# dependencies = ["osmium", "psycopg2-binary"]
# ///
"""Construieste metrics.public.bg_localities + bg_streets din OSM Bulgaria (pbf).
BG e locality/neighborhood-driven + multe ridicari de la oficiu curier. Cyrilic nativ + transliterare Latina fallback.
Gazetteer localitati (place nodes + addr:city) cu cod postal; strazi unde OSM le are (confirmare optionala)."""
import re, io, csv, unicodedata, subprocess
import osmium, psycopg2, urllib.parse as up
KB="/Users/gheorghebeschea/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"
def secret(k): return subprocess.run(["uv","run",KB,"secret-get",k],capture_output=True,text=True).stdout.strip()

# transliterare oficiala BG cirilica -> latina (fallback pt input latin gen 'Varna','Sofia')
_TR={'а':'a','б':'b','в':'v','г':'g','д':'d','е':'e','ж':'zh','з':'z','и':'i','й':'y','к':'k','л':'l',
'м':'m','н':'n','о':'o','п':'p','р':'r','с':'s','т':'t','у':'u','ф':'f','х':'h','ц':'ts','ч':'ch',
'ш':'sh','щ':'sht','ъ':'a','ь':'y','ю':'yu','я':'ya'}
def translit(s):
    return "".join(_TR.get(ch,ch) for ch in (s or "").lower())
def norm_cyr(s):
    s=(s or "").lower()
    s=re.sub(r"[^0-9a-zа-я ]+"," ",s)
    return re.sub(r"\s+"," ",s).strip()
def norm_lat(s):  # pt matching latin: transliterat apoi normalizat
    s=translit(s); s=unicodedata.normalize("NFD",s)
    s="".join(c for c in s if unicodedata.category(c)!="Mn")
    s=re.sub(r"[^a-z0-9 ]+"," ",s)
    return re.sub(r"\s+"," ",s).strip()
def pc4(z):
    d=re.sub(r"\D","",z or ""); return d if len(d)==4 else ""

class H(osmium.SimpleHandler):
    def __init__(s):
        super().__init__()
        s.loc={}     # (name_norm,pc) -> [name, place_type, cnt]
        s.streets={} # (city_norm,street_norm) -> [city,street,pc,nmin,nmax,cnt]
    def _addr(s,t):
        hn=t.get("addr:housenumber")
        if not hn: return
        city=t.get("addr:city"); street=t.get("addr:street"); pc=pc4(t.get("addr:postcode"))
        if city:
            k=(norm_cyr(city),pc); a=s.loc.get(k)
            if a is None: s.loc[k]=[city,"addr",1]
            else: a[2]+=1
        if city and street:
            ck=norm_cyr(city); sk=norm_cyr(street)
            n=None; m=re.match(r"\s*(\d{1,4})",hn)
            if m: n=int(m.group(1))
            k=(ck,sk); a=s.streets.get(k)
            if a is None: s.streets[k]=[city,street,pc,n,n,1]
            else:
                a[5]+=1
                if n is not None:
                    if a[3] is None or n<a[3]: a[3]=n
                    if a[4] is None or n>a[4]: a[4]=n
    def node(s,n):
        s._addr(n.tags)
        pl=n.tags.get("place")
        if pl in ("city","town","village","hamlet"):
            nm=n.tags.get("name")
            if nm:
                pc=pc4(n.tags.get("addr:postcode") or n.tags.get("postal_code"))
                k=(norm_cyr(nm),pc); a=s.loc.get(k)
                if a is None: s.loc[k]=[nm,pl,1]
                elif a[1]=="addr": a[1]=pl   # prefera tipul de place
    def way(s,w): s._addr(w.tags)
    def area(s,a): s._addr(a.tags)

h=H(); h.apply_file("bg.osm.pbf")
print(f"localitati (name,pc) chei = {len(h.loc):,}   strazi chei = {len(h.streets):,}")

dsn=secret("DATABASE_URL_METRICS"); p=up.urlsplit(dsn)
cn=psycopg2.connect(up.urlunsplit((p.scheme,p.netloc,p.path,"",""))); cn.autocommit=False; cur=cn.cursor()
cur.execute("DROP TABLE IF EXISTS public.bg_localities")
cur.execute("DROP TABLE IF EXISTS public.bg_streets")
cur.execute("""CREATE TABLE public.bg_localities(name text, name_norm text, name_lat text,
  place_type text, postcode text, cnt int)""")
cur.execute("""CREATE TABLE public.bg_streets(city text, city_norm text, street text, street_norm text,
  street_lat text, postcode text, num_min int, num_max int, cnt int)""")
b=io.StringIO(); w=csv.writer(b,delimiter="\t")
for (nn,pc),(name,pt,cnt) in h.loc.items():
    if not nn: continue
    w.writerow([name,nn,norm_lat(name),pt,pc,cnt])
b.seek(0); cur.copy_expert("COPY public.bg_localities FROM STDIN WITH (FORMAT csv, DELIMITER E'\\t', NULL '')",b)
b=io.StringIO(); w=csv.writer(b,delimiter="\t")
for (ck,sk),(city,street,pc,nmin,nmax,cnt) in h.streets.items():
    w.writerow([city,ck,street,sk,norm_lat(street),pc,nmin if nmin is not None else "",nmax if nmax is not None else "",cnt])
b.seek(0); cur.copy_expert("COPY public.bg_streets FROM STDIN WITH (FORMAT csv, DELIMITER E'\\t', NULL '')",b)
cur.execute("CREATE INDEX ix_bgl_norm ON public.bg_localities(name_norm)")
cur.execute("CREATE INDEX ix_bgl_lat  ON public.bg_localities(name_lat)")
cur.execute("CREATE INDEX ix_bgl_pc   ON public.bg_localities(postcode)")
cur.execute("CREATE INDEX ix_bgs_city ON public.bg_streets(city_norm, street_norm)")
cn.commit()
for q,l in [("SELECT count(*) FROM public.bg_localities","localitati randuri"),
            ("SELECT count(DISTINCT name_norm) FROM public.bg_localities","localitati distincte"),
            ("SELECT count(DISTINCT postcode) FROM public.bg_localities WHERE postcode<>''","coduri postale"),
            ("SELECT count(*) FROM public.bg_streets","strazi")]:
    cur.execute(q); print(f"{l}: {cur.fetchone()[0]:,}")
cn.close(); print("DONE")
