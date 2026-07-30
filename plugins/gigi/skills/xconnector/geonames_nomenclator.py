# -*- coding: utf-8 -*-
"""geonames_nomenclator.py — validator+corector locality-level pe metrics.public.geonames_localities (GeoNames postal).
Generic per ȚARĂ (HU/SK/…). Localitate + cod poștal + județ (fără stradă — GeoNames e locality-level; livrare pe
localitate). Confirmă localitatea, completează codul poștal lipsă, corectează orașul din cod, tolerează typo.
API: gn_validate_and_correct(cur, country, city, zip_, address1, address2) -> {status, address, note}."""
import re, unicodedata
from difflib import SequenceMatcher

# ZIP: HU = 4 cifre, SK = 5 cifre (scris „NNN NN")
_ZIP_LEN = {"HU": 4, "SK": 5, "HUN": 4, "SVK": 5}
_CC = {"HUN": "HU", "SVK": "SK", "HU": "HU", "SK": "SK"}


def norm(s):
    s = unicodedata.normalize("NFD", (s or "")).lower()
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", s)).strip()


def _pc(zip_, cc):
    d = re.sub(r"\D", "", zip_ or "")
    return d if len(d) == _ZIP_LEN.get(cc, 4) else ""


def _city_denoise(city):
    """Curăță câmpul oraș: taie sufix după virgulă/paranteză + prefix admin (megye/okres/kraj/district)."""
    c = (city or "").strip()
    c = re.split(r"[,(]", c)[0].strip()
    c = re.sub(r"(?i)\b(megye|jaras|okres|kraj|district|county|obec|mesto|město)\b\.?", " ", c)
    return re.sub(r"\s+", " ", c).strip(" .-")


_LOC_CACHE = {}
def _all_localities(cur, cc):
    if cc not in _LOC_CACHE:
        cur.execute("SELECT name, name_norm, postcode, county FROM public.geonames_localities WHERE country=%s", (cc,))
        _LOC_CACHE[cc] = cur.fetchall()
    return _LOC_CACHE[cc]


def find_locality(cur, cc, city):
    """(name, postcode, county) pt o localitate (match EXACT pe name_norm; prefer rândul cu cod poștal). None dacă nu."""
    nc = norm(city)
    if not nc:
        return None
    cur.execute("""SELECT name, postcode, county FROM public.geonames_localities
                   WHERE country=%s AND name_norm=%s ORDER BY (postcode IS NOT NULL AND postcode<>'') DESC LIMIT 1""",
                (cc, nc))
    r = cur.fetchone()
    return (r[0], r[1], r[2]) if r else None


def locality_for_pc(cur, cc, pc):
    if not pc:
        return None
    # postcode stocat poate avea format nativ cu spațiu (SK „974 01"); pc-ul primit e digits-only → compar normalizat
    cur.execute("SELECT name, county FROM public.geonames_localities WHERE country=%s AND replace(postcode,' ','')=%s LIMIT 1", (cc, pc))
    r = cur.fetchone()
    return (r[0], r[1]) if r else None


def locality_fuzzy(cur, cc, city):
    """Typo de localitate (fuzzy pe name_norm, prag 0.9). Întoarce (name, postcode, county) sau None."""
    nc = norm(city)
    if len(nc) < 4:
        return None
    best = None; bestr = 0.0
    for name, nn, pc_, county in _all_localities(cur, cc):
        if not nn:
            continue
        r = SequenceMatcher(None, nc, nn).ratio()
        if r > bestr:
            bestr = r; best = (name, pc_, county)
    return best if (bestr >= 0.9 and best) else None


# ── street-level (metrics.public.geonames_streets, OSM HU+SK) ─────────────────
_STREET_CACHE = {}
def _streets_for(cur, cc, city_norm):
    key = (cc, city_norm)
    if key not in _STREET_CACHE:
        try:
            cur.execute("""SELECT street, street_norm, postcode FROM public.geonames_streets
                           WHERE country=%s AND city_norm=%s AND postcode<>''""", (cc, city_norm))
            _STREET_CACHE[key] = cur.fetchall()
        except Exception:
            _STREET_CACHE[key] = []   # tabela poate lipsi (build nerulat) → degradează la locality-only
    return _STREET_CACHE[key]


def _street_core(a1):
    """Miezul străzii din address1: taie numărul casei de la final (HU/SK: „<stradă> <nr>", ex 12, 5/A, 12-14, 5A)."""
    s = re.split(r"[,(]", (a1 or ""))[0].strip()
    s = re.sub(r"[\s.,]+\d+[\da-zA-Z/\-]*$", "", s)   # nr casă la final (începe cu cifră)
    return norm(s)


def find_street(cur, cc, locality, address1):
    """(street, postcode) pt o stradă dintr-o localitate (fuzzy pe street_norm, prag 0.86). None dacă nu.
    Codul poștal al străzii e mai precis decât cel „central" al localității (critic la Budapesta: 1xxx variază pe stradă)."""
    cn = norm(locality); sc = _street_core(address1)
    if not cn or len(sc) < 3:
        return None
    best = None; bestr = 0.0
    for street, snorm, pc_ in _streets_for(cur, cc, cn):
        if not snorm:
            continue
        r = SequenceMatcher(None, sc, snorm).ratio()
        if sc in snorm or snorm in sc:      # prefix/substr (abrevieri gen „Váci u." vs „Váci utca")
            r = max(r, 0.92)
        if r > bestr:
            bestr = r; best = (street, pc_)
    return best if (bestr >= 0.86 and best) else None


def gn_validate_and_correct(cur, country, city, zip_, address1, address2=""):
    cc = _CC.get((country or "").upper())
    if not cc or cur is None:
        return None
    cty = _city_denoise(city)
    pc = _pc(zip_, cc)
    a1 = address1 or ""

    # 1) localitatea e reală
    loc = find_locality(cur, cc, cty)
    if loc:
        name, lpc, county = loc
        st = find_street(cur, cc, name, a1)          # cod poștal stradă-specific (mai precis ca cel central)
        spc = st[1] if (st and st[1]) else None
        if not pc:                                    # zip lipsă → completează (stradă > localitate)
            fillpc = spc or lpc
            if fillpc:
                src = "stradă %s" % st[0] if spc else "localitate %s" % name
                return {"status": "corrected", "address": {"city": name, "zip": fillpc, "address1": a1},
                        "note": "localitate reală (%s) + zip completat %s din %s" % (name, fillpc, src)}
        return {"status": "valid", "address": None,
                "note": "localitate reală (%s)%s" % (name, " + stradă confirmată (%s)" % st[0] if st else "")}

    # 2) localitate negăsită dar avem cod poștal → corectez orașul din cod
    if pc:
        d = locality_for_pc(cur, cc, pc)
        if d:
            return {"status": "corrected", "address": {"city": d[0], "zip": pc, "address1": a1},
                    "note": "oraș corectat din cod poștal (%s)" % d[0]}

    # 3) typo de localitate (fuzzy)
    fz = locality_fuzzy(cur, cc, cty)
    if fz:
        name, lpc, county = fz
        addr = {"city": name, "address1": a1}
        if not pc and lpc:
            addr["zip"] = lpc
        return {"status": "corrected", "address": addr, "note": "localitate corectată (typo→%s)" % name}

    # 4) nerezolvabil determinist → HERE
    return {"status": "needs_geocoder", "address": None, "note": "localitate/cod negăsite în GeoNames %s" % cc}
