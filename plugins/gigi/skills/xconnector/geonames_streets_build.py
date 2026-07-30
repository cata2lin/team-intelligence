# /// script
# requires-python = ">=3.10"
# dependencies = ["osmium", "psycopg2-binary"]
# ///
"""Construieste metrics.public.geonames_streets din OSM (Ungaria + Slovacia, pbf Geofabrik).
Street-level (confirmare strada + cod postal strada-specific) peste geonames_localities (locality-level GeoNames).
Latin nativ (fold accents). Extrage addr:street/housenumber/postcode/city din nodes+ways+areas.
Ruleaza:  uv run geonames_streets_build.py HU hungary.osm.pbf   (idempotent: DELETE per-country apoi COPY)."""
import re, io, csv, sys, unicodedata, subprocess
import osmium, psycopg2, urllib.parse as up
KB = "/Users/gheorghebeschea/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"
def secret(k): return subprocess.run(["uv", "run", KB, "secret-get", k], capture_output=True, text=True).stdout.strip()

# ZIP: HU = 4 cifre, SK = 5 cifre
_ZIP_LEN = {"HU": 4, "SK": 5}

def norm(s):
    s = unicodedata.normalize("NFD", (s or "")).lower()
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", s)).strip()

def pc(z, cc):
    d = re.sub(r"\D", "", z or "")
    return d if len(d) == _ZIP_LEN[cc] else ""


class H(osmium.SimpleHandler):
    def __init__(s, cc):
        super().__init__()
        s.cc = cc
        s.streets = {}  # (city_norm,street_norm) -> [city,street,pc,nmin,nmax,cnt]
    def _addr(s, t):
        hn = t.get("addr:housenumber")
        city = t.get("addr:city"); street = t.get("addr:street")
        if not (city and street):
            return
        p = pc(t.get("addr:postcode"), s.cc)
        ck = norm(city); sk = norm(street)
        if not (ck and sk):
            return
        n = None
        if hn:
            m = re.match(r"\s*(\d{1,5})", hn)
            if m: n = int(m.group(1))
        k = (ck, sk); a = s.streets.get(k)
        if a is None:
            s.streets[k] = [city, street, p, n, n, 1]
        else:
            a[5] += 1
            if not a[2] and p: a[2] = p            # completeaza pc daca lipsea
            if n is not None:
                if a[3] is None or n < a[3]: a[3] = n
                if a[4] is None or n > a[4]: a[4] = n
    def node(s, n): s._addr(n.tags)
    def way(s, w): s._addr(w.tags)
    def area(s, a): s._addr(a.tags)


def main():
    cc = sys.argv[1].upper()
    pbf = sys.argv[2]
    assert cc in _ZIP_LEN, "tara %s nesuportata" % cc
    h = H(cc); h.apply_file(pbf)
    print("[%s] chei strazi (city,street) = %d" % (cc, len(h.streets)))

    dsn = secret("DATABASE_URL_METRICS"); pr = up.urlsplit(dsn)
    cn = psycopg2.connect(up.urlunsplit((pr.scheme, pr.netloc, pr.path, "", "")))
    cn.autocommit = False; cur = cn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS public.geonames_streets(
        country text, city text, city_norm text, street text, street_norm text,
        postcode text, num_min int, num_max int, cnt int)""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_gns_cs ON public.geonames_streets(country, street_norm)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_gns_cc ON public.geonames_streets(country, city_norm)")
    cur.execute("DELETE FROM public.geonames_streets WHERE country=%s", (cc,))
    b = io.StringIO(); w = csv.writer(b, delimiter="\t")
    for (ck, sk), (city, street, p, nmin, nmax, cnt) in h.streets.items():
        w.writerow([cc, city, ck, street, sk, p,
                    nmin if nmin is not None else "", nmax if nmax is not None else "", cnt])
    b.seek(0)
    cur.copy_expert("COPY public.geonames_streets FROM STDIN WITH (FORMAT csv, DELIMITER E'\\t', NULL '')", b)
    cn.commit()
    cur.execute("SELECT count(*), count(*) FILTER (WHERE postcode<>'') FROM public.geonames_streets WHERE country=%s", (cc,))
    tot, wpc = cur.fetchone()
    print("[%s] incarcat: %d strazi (%d cu cod postal)" % (cc, tot, wpc))
    cn.close()


if __name__ == "__main__":
    main()
