# -*- coding: utf-8 -*-
"""pl_nomenclator.py — validator+corector PL pe metrics.public.pl_addresses (OpenAddresses/GUGiK PRG).
PL e ZIP-driven ca RO (cod postal = strada + interval numar + paritate; ex Marszalkowska = 20+ coduri).
FREE-FIRST: codurile din PRG au erori spatiale documentate -> NU ne batem cu codul clientului daca strada
exista (clientul isi stie codul); derivam cod DOAR cand lipseste/pointeaza alt oras. Corectam orasul garbled
din cod (inverse). Nu suprascriem strada reala.
API: pl_validate_and_correct(cur, city, zip_, address1, address2) -> {status, address, note}."""
import re, unicodedata
from collections import Counter

_PL = {ord("ł"): "l", ord("Ł"): "l"}
def strip_dia(s):
    s = (s or "").translate(_PL)
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn")
def norm(s):
    s = strip_dia(s).lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()
def pc_norm(p):
    d = re.sub(r"\D", "", p or "")
    pc = f"{d[:2]}-{d[2:5]}" if len(d) == 5 else ""
    return "" if pc == "00-000" else pc   # 00-000 = placeholder junk in PRG
def house_int(num):
    m = re.match(r"\s*(\d{1,4})", num or "")
    return int(m.group(1)) if m else None

def bldg_number(a2, a1):
    """Numărul CLĂDIRII (nu apartamentul). În PL numărul e ADESEA în address2 ca '2m39'/'15m24'/'2B1'
    (bloc + 'm'/'/'/'_' + apartament). Regex-ul vechi (cu \\b) rata formatele lipite → comenzi bune la CS.
    a2 (câmp de număr) prioritar; altfel a1 (ultimul număr, ca să evit numărul din numele străzii ex '3 Maja')."""
    if a2 and re.search(r"\d", a2):
        m = re.search(r"\d{1,4}", a2)           # primul număr din câmpul de număr = blocul
        if m: return int(m.group(0))
    if a1:
        m = re.search(r"(\d{1,4})\s*[a-zA-Z]?\s*[m/_]\s*\d", a1, re.I)  # bloc ÎNAINTE de apartament
        if m: return int(m.group(1))
        toks = re.findall(r"\d{1,4}", a1)
        if toks: return int(toks[-1])
    return None

# prefixe de tip strada pe care clientii le pun/scapa: ul. al. pl. os. rondo
_PREF = re.compile(r"^\s*(ul\.?|ulica|al\.?|aleja|aleje|pl\.?|plac|os\.?|osiedle|rondo)\s+", re.I)
def parse_street_number(a1):
    """din 'ul. Kochanowskiego 30' -> ('Kochanowskiego','30'); din 'os. Tysiaclecia 5/12' -> ('Tysiaclecia','5/12')."""
    a1 = (a1 or "").strip()
    m = re.search(r"\b(\d{1,4}[a-zA-Z]?(?:/\d+[a-zA-Z]?)?)\b", a1)
    number = m.group(1) if m else None
    street = a1[:m.start()] if m else a1
    street = _PREF.sub("", street).strip(" ,.")
    return street, number

def _dictify(cur):
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]
def rows_for_street(cur, city_norm, street_norm):
    if not (city_norm and street_norm): return []
    cur.execute("""SELECT city,street,postcode,num_min,num_max,has_odd,has_even,cnt
                   FROM public.pl_addresses WHERE city_norm=%s AND street_norm=%s""", (city_norm, street_norm))
    return _dictify(cur)
def city_for_postcode(cur, pc):
    """orasul dominant (dupa nr adrese) al unui cod postal — pt corectare oras garbled/oblast."""
    if not pc: return None
    cur.execute("""SELECT city, sum(cnt) s FROM public.pl_addresses WHERE postcode=%s
                   GROUP BY city ORDER BY s DESC LIMIT 1""", (pc,))
    r = cur.fetchone()
    return r[0] if r else None
def city_exists(cur, city_norm):
    if not city_norm: return False
    cur.execute("SELECT 1 FROM public.pl_addresses WHERE city_norm=%s LIMIT 1", (city_norm,))
    return cur.fetchone() is not None

def pick_postcode(rows, n, par):
    """codul potrivit pt (numar,paritate): intervalul care contine n cu paritatea corecta; altfel cel mai frecvent."""
    cands = [r for r in rows if r["postcode"] and r["postcode"] != "00-000"]
    if not cands: return None
    if n is not None:
        hit = [r for r in cands if r["num_min"] is not None and r["num_min"] <= n <= r["num_max"]
               and ((par == 1 and r["has_odd"]) or (par == 0 and r["has_even"]))]
        if hit: return max(hit, key=lambda r: r["cnt"])["postcode"]
    return max(cands, key=lambda r: r["cnt"])["postcode"]

# cele 16 voievodate (normate, fără diacritice) — clienții le lipesc la oraș ('Barzkowice zachodniopomorskie')
_VOIV = {"dolnoslaskie","kujawsko pomorskie","lubelskie","lubuskie","lodzkie","malopolskie","mazowieckie",
"opolskie","podkarpackie","podlaskie","pomorskie","slaskie","swietokrzyskie","warminsko mazurskie",
"wielkopolskie","zachodniopomorskie"}
_LOCPFX = re.compile(r"^(gm|gmina|woj|wojew[oó]dztwo|pow|powiat|m|miasto|wie[sś])\b\.?\s*", re.I)
def pl_city_candidates(city):
    """variante de localitate pt un câmp 'city' murdar: 'Kraków Nowa Huta'->krakow, 'Barzkowice zachodniopomorskie'
    ->barzkowice, 'Dąbrowa, woj. śląskie'->dabrowa. Match EXACT în rows/city_exists => candidați extra = siguri."""
    raw = city or ""; out = []
    def add(x):
        nc = norm(_LOCPFX.sub("", (x or "").strip()))
        for v in _VOIV:
            if nc.endswith(" " + v): nc = nc[:-(len(v) + 1)].strip()
        if nc and nc not in out: out.append(nc)
    for p in re.split(r"[\[\],/|]+", raw): add(p)
    add(raw)
    toks = norm(_LOCPFX.sub("", raw.strip())).split()
    for k in (3, 2, 1):
        if len(toks) >= k: add(" ".join(toks[:k]))
    return out

def pl_validate_and_correct(cur, city, zip_, address1, address2=""):
    # câmpuri INVERSATE: city='42-436' zip='Pilica' -> swap (city arată ca un cod, zip are litere = nume oraș)
    if pc_norm(city) and not pc_norm(zip_) and re.search(r"[a-ząćęłńóśźż]", zip_ or "", re.I):
        city, zip_ = zip_, city
    street, _ = parse_street_number(address1 or "")
    sn = norm(street); pc = pc_norm(zip_)
    cands = pl_city_candidates(city)
    cn = cands[0] if cands else norm(city)      # forma principală de localitate
    n = bldg_number(address2, address1)         # numărul e des în address2 ('2m39'); apoi address1
    par = None if n is None else n % 2

    # curierul are nevoie de numar casa; fara el = incomplet -> CS
    if n is None:
        return {"status": "cs", "address": None, "note": "fara numar casa"}

    # 1) strada exista in vreo varianta de localitate? -> adresa e REALA (livrabila chiar daca HERE a picat)
    #    free-first: codurile PRG au erori spatiale + clientul isi stie codul -> daca a dat un cod valid, il PASTRAM;
    #    derivam cod DOAR cand lipseste (paritate ca la RO).
    for cand in cands:
        rows = rows_for_street(cur, cand, sn)
        if rows:
            if pc:
                return {"status": "valid", "address": None, "note": "strada exista + cod prezent (pastrez codul clientului)"}
            chosen = pick_postcode(rows, n, par)
            if chosen:
                return {"status": "corrected", "address": {"city": city, "zip": chosen, "address1": address1},
                        "note": "cod lipsa derivat din strada+numar (paritate)"}
            return {"status": "valid", "address": None, "note": "strada exista (fara cod)"}

    # 2a) vreo varianta de localitate e REALA (doar strada nu-i in date) -> NU suprascrie, livrabila
    #     (codul postal rural acopera multe sate; a inlocui satul real cu orasul-oficiu = gresit, ca la RO)
    for cand in cands:
        if city_exists(cur, cand):
            return {"status": "valid", "address": None, "note": "localitate reala (strada neconfirmata in PRG)"}

    # 2b) localitatea NU exista (jumble zip+nume / oblast / typo) -> relocheaza din cod postal
    if pc:
        dom = city_for_postcode(cur, pc)
        if dom and norm(dom) != cn:
            return {"status": "corrected", "address": {"city": dom, "zip": pc, "address1": address1},
                    "note": "oras corectat din cod postal"}

    # 3) nerezolvabil determinist -> HERE
    return {"status": "needs_geocoder", "address": None, "note": "strada/cod negasite in PRG"}


if __name__ == "__main__":
    import subprocess, psycopg2, urllib.parse as up
    KB = "/Users/gheorghebeschea/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"
    def secret(k): return subprocess.run(["uv", "run", KB, "secret-get", k], capture_output=True, text=True).stdout.strip()
    dsn = secret("DATABASE_URL_METRICS"); p = up.urlsplit(dsn)
    cn = psycopg2.connect(up.urlunsplit((p.scheme, p.netloc, p.path, "", ""))); cn.set_session(readonly=True); cur = cn.cursor()
    SAMPLE = [
        {"a1": "Kochanowskiego 30", "city": "Białogard", "zip": "78-200"},   # strada+cod OK
        {"a1": "ul. Marszałkowska 84", "city": "Warszawa", "zip": "00-514"}, # ZIP-driven
        {"a1": "Marszalkowska 101", "city": "Warszawa", "zip": ""},          # deriva cod din numar impar
        {"a1": "ul. Długa 5", "city": "mazowieckie", "zip": "00-238"},       # oras=voievodat -> corecteaza din cod
        {"a1": "Piotrkowska 100", "city": "Lodz", "zip": "90-006"},          # diacritice lipsa (Łódź)
        {"a1": "Krakowska", "city": "Krakow", "zip": ""},                    # fara numar -> cs
    ]
    from collections import Counter as C
    st = C()
    for s in SAMPLE:
        r = pl_validate_and_correct(cur, s.get("city"), s.get("zip"), s.get("a1"), "")
        st[r["status"]] += 1
        addr = ("→ %s" % r["address"]) if r.get("address") else ""
        print("%-11s %-26s %-14s %-8s %s [%s]" % (r["status"].upper(), s["a1"][:26], s["city"][:14], s["zip"], addr, r["note"]))
    print("\n", dict(st))
    cn.close()
