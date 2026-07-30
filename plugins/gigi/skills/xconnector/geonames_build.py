# /// script
# requires-python = ">=3.10"
# dependencies = ["pg8000"]
# ///
"""GeoNames postal (HU/SK) → metrics.public.geonames_localities. Batch multi-row (rapid). DATABASE_URL_METRICS."""
import sys, os, unicodedata, re
sys.path.insert(0, "/Users/gheorghebeschea/Downloads/Scripturi/team-intelligence/plugins/gigi/skills/xconnector")
import xconnector as X
def norm(s):
    s = unicodedata.normalize("NFD", (s or "")).lower()
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+"," ", re.sub(r"[^a-z0-9 ]+"," ", s)).strip()
cur = X.metrics_cursor(); assert cur
cur.execute("""CREATE TABLE IF NOT EXISTS public.geonames_localities(
  country text, postcode text, name text, name_norm text, county text, lat double precision, lon double precision)""")
cur.execute("CREATE INDEX IF NOT EXISTS idx_gn_cn ON public.geonames_localities(country, name_norm)")
cur.execute("CREATE INDEX IF NOT EXISTS idx_gn_cp ON public.geonames_localities(country, postcode)")
GNDIR = os.path.dirname(os.path.abspath(__file__))
for cc, fn in [("HU","HU.txt"),("SK","SK.txt")]:
    cur.execute("DELETE FROM public.geonames_localities WHERE country=%s", (cc,))
    rows=[]
    for line in open(os.path.join(GNDIR, fn), encoding="utf-8"):
        p=line.rstrip("\n").split("\t")
        if len(p)<5: continue
        rows.append((cc, p[1].strip(), p[2].strip(), norm(p[2]), p[3].strip(),
                     float(p[9]) if len(p)>9 and p[9] else None, float(p[10]) if len(p)>10 and p[10] else None))
    B=400
    for i in range(0, len(rows), B):
        chunk=rows[i:i+B]
        ph=",".join(["(%s,%s,%s,%s,%s,%s,%s)"]*len(chunk))
        flat=[v for r in chunk for v in r]
        cur.execute("INSERT INTO public.geonames_localities(country,postcode,name,name_norm,county,lat,lon) VALUES "+ph, flat)
    print(f"  {cc}: {len(rows)} rânduri")
cur.execute("SELECT country, count(*), count(distinct name_norm), count(distinct postcode) FROM public.geonames_localities GROUP BY country")
for r in cur.fetchall(): print("  verificare (country, rows, localități, coduri):", r)
