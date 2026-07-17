# -*- coding: utf-8 -*-
"""address_nomenclator.py — validator + auto-corector RO pe nomenclator (portat sync din AWB Hub v8.3.1).

Sursa nomenclatorului: metrics.public.romania_addresses (judet/localitate/tip_artera/nume_strada/numar/
cod_postal/sector + judet_norm/localitate_norm). Funcțiile pure (parsing stradă/număr, București, fuzzy)
sunt copiate 1:1 din services/address_service.py (AWB Hub, github gbeschea/AWB_b22). Query-urile DB rescrise
sync (psycopg2), interogând tabelul din metrics — NU async SQLAlchemy.

API: `validate_and_correct(cur, province, city, zip, address1, address2) -> dict`
  {status: valid|corrected|needs_geocoder|cs, address: {province,city,zip,address1}, source, note}
  - status='corrected' → `address` are câmpurile corectate (de scris în comandă); 'valid' → e bună ca-atare;
    'needs_geocoder' → nomenclatorul n-o rezolvă (candidat pt HERE); 'cs' → fără număr / la CS.
Pipeline: număr obligatoriu → ZIP→stradă (fwd, 84% unic) → invers localitate+stradă→ZIP (ZIP gunoi) → rural-valid.
"""
import re, unicodedata
from collections import Counter
from difflib import SequenceMatcher

# ===== normalizare (copiat) =====
def strip_diacritics(s):
    if not s: return ""
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return (s.replace("ș","s").replace("ş","s").replace("ț","t").replace("ţ","t")
             .replace("ă","a").replace("â","a").replace("î","i"))
def norm_text(s):
    s = strip_diacritics(s or "").lower()
    s = re.sub(r"[',’`\"“”]", " ", s)
    s = re.sub(r"[,.;:()_/\\\-]+", " ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()
def same_locality(a, b):
    na, nb = norm_text(a), norm_text(b)
    return bool(na and nb and (na == nb or na in nb or nb in na))

ALIASES = {"mendelev":"mendeleev","dr taberei":"drumul taberei","drumultaberei":"drumul taberei"}
def apply_aliases(s):
    p = norm_text(s)
    for k,v in ALIASES.items():
        if k in p: p = p.replace(k,v)
    return p

LOCKER = re.compile(r"(easybox|locker|sameday|fanbox|collect\s*point|pick[\s\-]*up)", re.I)
HAS_PREFIX_NUM = re.compile(r'(?i)\b(?:nr|no|numar|număr)\.?\s*(\d+[a-zA-Z]?|\d+/\d+)\b')
TRAILING_NUM   = re.compile(r'(?i)(\d+[a-zA-Z]?|\d+/\d+)\s*($|,|\s+bl|bloc|sc|scara|ap|et)')
SECTOR_RE = re.compile(r"\b(?:sector(?:ul)?|sec\.?|sect\.)\s*([1-6])\b", re.I)
MONTHS = {"ianuarie","februarie","martie","aprilie","mai","iunie","iulie","august","septembrie","octombrie","noiembrie","decembrie"}
NO_NUM_RE = re.compile(r"\b(f\.?\s*n\.?|fara\s+nr\.?|fara\s+numar|fără\s+număr)\b", re.I)

def _strip_leading_number_patterns(s):
    if not s: return s
    s = re.sub(r'^\s*(?:nr|no|numar|număr)\.?\s*(\d+[a-z]?|\d+/\d+)\s*[,/ \-]*'
               r'(?=(str(?:\.|ada)?|bd\.?|blvd\.?|bulevardul|calea|drumul?|dr|soseaua|sos\.?|aleea)\b)','',s,flags=re.I)
    s = re.sub(r'^\s*(\d+[a-z]?|\d+/\d+)\s*[,/ \-]*'
               r'(?=(str(?:\.|ada)?|bd\.?|blvd\.?|bulevardul|calea|drumul?|dr|soseaua|sos\.?|aleea)\b)','',s,flags=re.I)
    m = re.match(r'^\s*(?:nr|no|numar|număr)?\.?\s*(\d+[a-z]?|\d+/\d+)\s+([A-Za-zĂÂÎȘŞȚŢăâîșşțţ].+)$', s)
    if m:
        nxt = norm_text(m.group(2)).split()[:1]
        if nxt and nxt[0] not in {"bl","bloc","sc","scara","ap","et","etaj","lot"}: s = m.group(2)
    return s

def has_real_house_number(text):
    t = text or ""
    t = re.sub(r"([A-Za-zÀ-ÿ])(\d)", r"\1 \2", t)
    m = HAS_PREFIX_NUM.search(t)
    if m: return m.group(1).replace(" ","")
    m = TRAILING_NUM.search(t)
    if m: return m.group(1).replace(" ","")
    toks = norm_text(t).split()
    for i,tok in enumerate(toks):
        if re.fullmatch(r'\d+[a-z]?|\d+/\d+', tok):
            prev = toks[i-1] if i>0 else ""
            if prev in {"calea","strada","bulevardul","bd","bd.","aleea","soseaua","sos","drum"}:
                nx = toks[i+1] if i+1<len(toks) else ""
                if nx in MONTHS or (nx and nx.isalpha()): continue
            return tok.replace(" ","")
    return None

def _truncate_after_real_number(text):
    t = text or ""
    t = _strip_leading_number_patterns(t)
    t = re.sub(r"([A-Za-zÀ-ÿ])(\d)", r"\1 \2", t)
    m = HAS_PREFIX_NUM.search(t)
    if m: return t[:m.start()].strip()
    m = TRAILING_NUM.search(t)
    if m: return t[:m.start()].strip()
    toks = norm_text(t).split(); orig = re.split(r"\s+", t.strip())
    for i,tok in enumerate(toks):
        if re.fullmatch(r'\d+[a-z]?|\d+/\d+', tok):
            prev = toks[i-1] if i>0 else ""; nx = toks[i+1] if i+1<len(toks) else ""
            if prev in {"calea","strada","bulevardul","bd","bd.","aleea","soseaua","sos","drum"} and (nx in MONTHS or (nx and nx.isalpha())): continue
            return " ".join(orig[:i]).strip()
    return text

def street_core(s):
    s = apply_aliases(s or "")
    s = re.sub(r"\b(str\.)\b","strada",s,flags=re.I); s = re.sub(r"\b(str)\b","strada",s,flags=re.I)
    s = re.sub(r"\b(bd\.?|blvd\.?)\b","bulevardul",s,flags=re.I); s = re.sub(r"\b(sos\.?|soseaua)\b","soseaua",s,flags=re.I)
    s = re.sub(r"\b(alee|aleea)\b","aleea",s,flags=re.I); s = re.sub(r"\b(cal\.?)\b","calea",s,flags=re.I)
    s = re.sub(r"\b(drumul?|dr)\b","drum",s,flags=re.I)
    s = norm_text(_truncate_after_real_number(s))
    s = re.sub(r"^\b(strada|bulevardul|calea|aleea|soseaua|sos|drum)\b","",s).strip()
    s = re.sub(r"\b(bloc|bl|scara|sc|ap|ap\.|et|etaj|sector|jud|cartier|lot|sc\.)\b.*","",s).strip()
    return re.sub(r"\s+"," ",s)

def same_street(a, b):
    ca, cb = street_core(a), street_core(b)
    if not ca or not cb: return False
    if ca == cb: return True
    if SequenceMatcher(None, ca, cb).ratio() >= 0.86: return True
    ta, tb = set(ca.split()), set(cb.split())
    if ta and tb:
        inter = len(ta & tb); bigger = max(len(ta),len(tb))
        if bigger and inter/bigger >= 0.75: return True
        if min(len(ta),len(tb))==1 and inter==1: return True
    return False

_TIP_MAP = {"cale":"calea","calea":"calea","cal":"calea",
            "alee":"aleea","aleea":"aleea",
            "bulevard":"bulevardul","bulevardul":"bulevardul","bd":"bulevardul","blvd":"bulevardul",
            "strada":"strada","str":"strada",
            "sosea":"soseaua","soseaua":"soseaua","sos":"soseaua",
            "drum":"drum","drumul":"drum","dr":"drum"}
def _tip_canon(s):
    """Normalizează tipul arterei la o formă canonică comună — nomenclatorul ține forme SCURTE ('Cale','Alee',
    'Bulevard'), iar `detect_tip_from_raw` întoarce forme lungi ('calea','aleea','bulevardul'). Fără asta, filtrul
    de tip din `rows_for_street` respinge greșit toate rândurile Cale/Alee/Bulevard."""
    t = norm_text(s)
    return _TIP_MAP.get(t, t)
def detect_tip_from_raw(raw):
    t = (raw or "").lower()
    if re.search(r"\bdrumul?\b",t): return "drum"
    if re.search(r"\bstr(?:\.|ada)?\b|\bstrada\b",t): return "strada"
    if re.search(r"\bbd\.?|blvd\.?|bulevardul\b",t): return "bulevardul"
    if re.search(r"\bsoseaua|sos\.?\b",t): return "soseaua"
    if re.search(r"\baleea\b",t): return "aleea"
    if re.search(r"\bcalea\b|\bcal\.?\b",t): return "calea"
    return None

def block_meta(a1):
    """coada bl/sc/ap/et din adresa clientului — de PĂSTRAT când rescriu strada (altfel pierd apartamentul)."""
    m = re.search(r'\b(bl|bloc|sc|scara|ap|apartament|et|etaj)\b.*$', a1 or '', re.I)
    return (" " + re.sub(r"\s+"," ", m.group(0).strip())) if m else ""
def street_is_garbage(cust, city):
    """True dacă „strada" clientului e de fapt gunoi (goală sau = numele orașului, ex „cluj").
    DOAR atunci am voie să completez strada din ZIP; o stradă REALĂ care nu se potrivește = conflict (nu o suprascriu)."""
    if not cust: return True
    nc = norm_text(cust); ncity = norm_text(city)
    if ncity and (nc == ncity or nc in ncity or ncity in nc): return True
    return len(nc) <= 2
def detect_easybox(*parts): return bool(LOCKER.search(" ".join(p or "" for p in parts)))
def detect_sector(*parts):
    m = SECTOR_RE.search(" ".join(p or "" for p in parts)); return m.group(1) if m else None
def bucharest_fix(judet, city, *addr):
    jud = judet or ""; cty = city or ""; sector=None
    mc = SECTOR_RE.search(cty); mj = SECTOR_RE.search(jud)
    if mc or mj:
        jud, cty, sector = "Bucuresti","Bucuresti",(mc or mj).group(1)
    else:
        sa = detect_sector(*addr)
        if sa: jud, cty, sector = "Bucuresti","Bucuresti",sa
    if norm_text(jud) in {"bucuresti","mun bucuresti","bucuresti municipiu"}: jud, cty = "Bucuresti","Bucuresti"
    return jud, cty, sector

# ===== DB sync (metrics.public.romania_addresses) =====
_COLS = "judet, localitate, tip_artera, nume_strada, numar, cod_postal, sector"
def _dictify(cur):
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]
def _siruta_row(judet, denumire, cod_postal):
    """Rând sintetic locality-level din SIRUTA (fără stradă) — ca validatorul să recunoască
    localitatea/ZIP-ul oficial chiar dacă n-avem străzile lui în romania_addresses."""
    return {"judet": judet or "", "localitate": denumire, "tip_artera": None,
            "nume_strada": None, "numar": None, "cod_postal": cod_postal, "sector": None}
def load_by_zip(cur, z6):
    if not z6: return []
    cur.execute(f"SELECT {_COLS} FROM public.romania_addresses WHERE cod_postal=%s LIMIT 25000", (z6,))
    rows = _dictify(cur)
    if rows: return rows
    # FALLBACK SIRUTA: ZIP oficial care aparține unei localități reale (nu supra-respinge)
    cur.execute("SELECT judet_norm, denumire, cod_postal FROM public.romania_siruta WHERE cod_postal=%s AND niv IN (2,3) LIMIT 1", (str(z6).strip(),))
    r = cur.fetchone()
    return [_siruta_row(r[0], r[1], r[2])] if r else []
def load_by_locality(cur, judet, loc):
    ln = norm_text(loc)
    if not ln: return []
    jn = norm_text(judet)
    if jn:
        cur.execute(f"SELECT {_COLS} FROM public.romania_addresses WHERE judet_norm=%s AND localitate_norm=%s LIMIT 25000", (jn, ln))
        rows = _dictify(cur)
        if rows: return rows
        # SIRUTA județ-specific ÎNAINTEA fallback-ului cross-județ: (județ,localitate) validă oficial
        # dar fără străzi la noi → păstrează JUDEȚUL CORECT (nu „corecta" spre alt județ cu același nume).
        cur.execute("SELECT denumire, cod_postal FROM public.romania_siruta WHERE niv IN (2,3) AND judet_norm=%s AND localitate_norm=%s ORDER BY niv DESC LIMIT 1", (jn, ln))
        r = cur.fetchone()
        if r: return [_siruta_row(judet, r[0], r[1])]
    # județ greșit/gol (ex. ZIP suspect) → cad pe localitate-only; candidate_zip alege dominant
    cur.execute(f"SELECT {_COLS} FROM public.romania_addresses WHERE localitate_norm=%s LIMIT 25000", (ln,))
    rows = _dictify(cur)
    if rows: return rows
    # FALLBACK SIRUTA: localitatea e reală chiar dacă n-avem străzile ei (recuperează satele mici)
    s = locality_in_siruta(cur, judet, loc)
    return [_siruta_row(judet, s["denumire"], s["cod_postal"])] if s else []
# ===== SIRUTA (registru oficial complet INS — îmbogățire nomenclator; vezi siruta_sync.py) =====
def locality_in_siruta(cur, judet, loc):
    """Localitatea EXISTĂ în registrul oficial SIRUTA? Prinde satele reale care lipsesc din tabelul
    postal parțial (romania_addresses) → validatorul NU mai supra-respinge o adresă doar fiindcă
    n-avem străzile acelui sat. Întoarce {cod_siruta, cod_postal, judet_norm, denumire} sau None."""
    ln = norm_text(loc)
    if not ln: return None
    jn = norm_text(judet)
    if jn:
        cur.execute("SELECT cod_siruta,cod_postal,judet_norm,denumire FROM public.romania_siruta "
                    "WHERE niv IN (2,3) AND judet_norm=%s AND localitate_norm=%s ORDER BY niv DESC LIMIT 1", (jn, ln))
        r = cur.fetchone()
        if r: return {"cod_siruta": r[0], "cod_postal": r[1], "judet_norm": r[2], "denumire": r[3]}
    cur.execute("SELECT cod_siruta,cod_postal,judet_norm,denumire FROM public.romania_siruta "
                "WHERE niv IN (2,3) AND localitate_norm=%s ORDER BY niv DESC LIMIT 1", (ln,))
    r = cur.fetchone()
    return {"cod_siruta": r[0], "cod_postal": r[1], "judet_norm": r[2], "denumire": r[3]} if r else None
def zip_owner_siruta(cur, z):
    """județ+localitate care DEȚINE un cod poștal, din SIRUTA (fallback când romania_addresses n-are ZIP-ul)."""
    if not z: return (None, None)
    cur.execute("SELECT judet_norm,denumire FROM public.romania_siruta WHERE cod_postal=%s AND niv=3 LIMIT 1", (str(z).strip(),))
    r = cur.fetchone()
    return (r[0], r[1]) if r else (None, None)


def _cand_street(r): return " ".join(x for x in [(r.get("tip_artera") or "").strip(), (r.get("nume_strada") or "").strip()] if x).strip()
def zip_owner(rows):
    pairs = [(r.get("judet") or "", r.get("localitate") or "") for r in rows]
    cnt = Counter((norm_text(j),norm_text(l)) for j,l in pairs if j and l)
    if not cnt: return None,None
    (jn,ln),_ = cnt.most_common(1)[0]
    for j,l in pairs:
        if norm_text(j)==jn and norm_text(l)==ln: return j,l
    return None,None
def zip_owner_of(cur, z):
    """judeţ+localitate care DEŢINE ZIP-ul z (din nomenclator) — ca să corectez și jud/loc, nu doar ZIP-ul, la INVERS."""
    rows = load_by_zip(cur, z)
    return zip_owner(rows) if rows else (None, None)
def rows_for_street(rows, street_raw):
    if not street_raw: return []
    tip_pref = detect_tip_from_raw(street_raw); out=[]
    for r in rows:
        full = _cand_street(r)
        if full and same_street(street_raw, full):
            if tip_pref and _tip_canon(r.get("tip_artera")) != tip_pref: continue
            out.append(r)
    return out
_INF_NUM = 10**9
def _parse_numar_ranges(numar):
    """Parsează coloana `numar` a nomenclatorului → listă de (lo, hi, paritate) cu paritate ∈ {0 par, 1 impar, None ambele}.
    Format RO: 'nr. 29-37'→(29,37,1) · 'nr. 32-34'→(32,34,0) · 'nr. 155'→(155,155,1) · 'nr. 157-T'→(157,∞,1 impar până la capăt)
    · 'nr. 2-T'→(2,∞,0) · 'nr. 1-T; 2-T'→[(1,∞,1),(2,∞,0)] (toată strada). T = terminus (până la capătul străzii)."""
    out = []
    for part in re.split(r"[;,]", (numar or "").lower()):
        part = part.replace("nr.", " ")
        nums = re.findall(r"\d+", part)
        has_t = bool(re.search(r"(?:^|[\s\-])t\b", part))
        if not nums:
            continue
        lo = int(nums[0])
        hi = int(nums[1]) if len(nums) >= 2 else (_INF_NUM if has_t else lo)
        if hi < lo:
            lo, hi = hi, lo
        par = (lo % 2) if (hi == _INF_NUM or lo % 2 == hi % 2) else None
        out.append((lo, hi, par))
    return out

def _num_in_ranges(n, ranges):
    for lo, hi, par in ranges:
        if lo <= n <= hi and (par is None or n % 2 == par):
            return True
    return False

def candidate_zip_from_locality(rows, street_raw, number=None):
    """Derivă ZIP din localitate+stradă:
      · 1 singur ZIP pe stradă (oraș mic) → îl întorc (neambiguu).
      · MULTE ZIP-uri (oraș mare — „zip-urile sunt inclusiv pe numere", ex. Calea Victoriei = 35 ZIP-uri, câte unul pe
        interval de numere) → dezambiguez pe NUMĂRUL casei (paritate inclusă: par/impar = laturi diferite de stradă).
        Dacă numărul cade fix pe un ZIP → îl întorc; altfel None (→ HERE, NU ghicesc)."""
    sub = rows_for_street(rows, street_raw)
    if not sub:
        return None
    def _z6(r):
        z = str(r.get("cod_postal") or "").strip()
        return z if re.fullmatch(r"\d{6}", z) else None
    zips = {_z6(r) for r in sub if _z6(r)}
    if len(zips) == 1:
        return next(iter(zips))
    if not zips:
        return None
    n = None
    if number is not None:
        m = re.search(r"\d+", str(number))
        if m:
            n = int(m.group(0))
    if n is None:
        return None
    hit = {_z6(r) for r in sub if _z6(r) and _num_in_ranges(n, _parse_numar_ranges(r.get("numar")))}
    return next(iter(hit)) if len(hit) == 1 else None

# ===== validare + corecție =====
def validate_and_correct(cur, province, city, zip_, address1, address2=""):
    a1 = address1 or ""; a2 = address2 or ""
    prov = province or ""; cty = city or ""
    # 0) easybox
    if detect_easybox(a1, a2):
        z6 = re.sub(r"\D","", zip_ or "").zfill(6)
        if re.fullmatch(r"\d{6}", z6) and load_by_zip(cur, z6):
            return {"status":"valid","address":None,"source":"easybox","note":"locker"}
        return {"status":"cs","address":None,"source":"easybox","note":"locker fără ZIP valid"}
    # 1) București sector
    prov, cty, sector = bucharest_fix(prov, cty, a1, a2)
    # 2) număr obligatoriu
    if NO_NUM_RE.search(a1+" "+a2): num = None
    else: num = has_real_house_number(a1) or has_real_house_number(a2)
    if not num:
        return {"status":"cs","address":None,"source":"nomenclator","note":"fără număr de casă"}
    s1, s2 = street_core(a1), street_core(a2)
    cust = s1 if len(s1) >= len(s2) else s2   # ca v8.3.1: iau strada din câmpul mai substanțial (a1 SAU a2)
    z6 = re.sub(r"\D","", zip_ or "").zfill(6)
    zip_rows = load_by_zip(cur, z6) if (re.fullmatch(r"\d{6}", z6) and z6 != "000000") else []

    if zip_rows:
        jo, lo = zip_owner(zip_rows)
        out_prov = jo or prov; out_city = lo or cty
        jl_fixed = bool(jo and lo and (not same_locality(jo,prov) or not same_locality(lo,cty)))
        streets = sorted({_cand_street(r) for r in zip_rows if _cand_street(r)})
        garbage = street_is_garbage(cust, cty) or street_is_garbage(cust, out_city)

        if not garbage:
            # clientul a dat o stradă REALĂ → NU o schimb NICIODATĂ (nici dacă ZIP-ul are mai multe străzi).
            in_zip = bool(rows_for_street(zip_rows, cust)) or any(same_street(cust, st) for st in streets)
            if in_zip or not streets:
                # strada e la acest ZIP (orice tip) SAU ZIP rural fără străzi → PĂSTREZ adresa clientului, fix doar jud/loc
                return {"status":"corrected" if jl_fixed else "valid",
                        "address":{"province":out_prov,"city":out_city,"zip":z6,"address1":a1} if jl_fixed else None,
                        "source":"nomenclator",
                        "note":("stradă păstrată (client)" if streets else "rural") + (" +fix jud/loc" if jl_fixed else "")}
            # stradă reală care NU e la acest ZIP → ZIP suspect. INVERS: păstrez strada, caut ZIP-ul ei din localitate.
            cands = load_by_locality(cur, prov, cty)
            dz = candidate_zip_from_locality(cands, cust, num) if cands else None
            if dz and dz != z6:
                jo2, lo2 = zip_owner_of(cur, dz)   # fix și jud/loc din ZIP-ul derivat (altfel ex. Ilfov+Buc → rămâne UNKNOWN)
                return {"status":"corrected","address":{"province":jo2 or prov,"city":lo2 or cty,"zip":dz,"address1":a1},
                        "source":"nomenclator-invers","note":"stradă reală ≠ ZIP → ZIP+jud/loc corectat din localitate+stradă"}
            return {"status":"needs_geocoder","address":None,"source":"nomenclator",
                    "note":"stradă reală nepotrivită cu ZIP (ZIP suspect)"}

        # strada clientului e GUNOI (goală / = numele orașului) → o pot completa DOAR determinist:
        if not streets:
            # rural: ZIP fără străzi → valid pe localitate+număr (păstrez adresa clientului)
            return {"status":"corrected" if jl_fixed else "valid",
                    "address":{"province":out_prov,"city":out_city,"zip":z6,"address1":a1} if jl_fixed else None,
                    "source":"nomenclator","note":"rural (ZIP+localitate+număr, fără stradă)" + (" +fix jud/loc" if jl_fixed else "")}
        if len(streets) == 1:
            # ZIP are EXACT o stradă → completez fără ambiguitate (84% din ZIP-uri RO)
            new_a1 = f"{streets[0]} Nr. {num}{block_meta(a1)}".strip()
            changed = jl_fixed or (norm_text(new_a1) != norm_text(a1))
            return {"status":"corrected" if changed else "valid",
                    "address":{"province":out_prov,"city":out_city,"zip":z6,"address1":new_a1} if changed else None,
                    "source":"nomenclator","note":"stradă completată din ZIP (unic)" + (" +fix jud/loc" if jl_fixed else "")}
        # gunoi + ZIP cu MAI MULTE străzi → NU ghicesc care e → HERE (nu pun altă stradă)
        return {"status":"needs_geocoder","address":None,"source":"nomenclator",
                "note":f"stradă gunoi + ZIP multi-stradă ({len(streets)})"}

    # 3) ZIP gunoi/lipsă → INVERS: derivă ZIP din localitate(+stradă)
    cands = load_by_locality(cur, prov, cty)
    if not cands:
        return {"status":"needs_geocoder","address":None,"source":"nomenclator","note":"localitate negăsită în nomenclator"}
    dz = candidate_zip_from_locality(cands, cust, num)
    if dz:
        # completez ZIP-ul + jud/loc din ZIP-ul derivat; PĂSTREZ strada clientului (doar ZIP-ul lipsea)
        jo2, lo2 = zip_owner_of(cur, dz)
        return {"status":"corrected","address":{"province":jo2 or prov,"city":lo2 or cty,"zip":dz,"address1":a1},
                "source":"nomenclator-invers","note":"ZIP+jud/loc completat din localitate+stradă"}
    return {"status":"needs_geocoder","address":None,"source":"nomenclator","note":"localitate OK dar ZIP nederivabil"}
