# -*- coding: utf-8 -*-
"""bg_nomenclator.py — validator+corector BG pe metrics.public.bg_localities/bg_streets (OSM Bulgaria).
BG e locality/neighborhood-driven + FOARTE multe ridicari de la OFICIU curier (Еконт/Спиди) unde strada/codul
sunt irelevante. Cyrilic nativ (transliterare Latina = fallback). Fara numar casa obligatoriu.
Prioritati: (1) office-pickup -> valid; (2) localitate reala -> valid; (3) oras garbled -> corecteaza din cod.
API: bg_validate_and_correct(cur, city, zip_, address1, address2) -> {status, address, note}."""
import re, unicodedata

_TR = {'а':'a','б':'b','в':'v','г':'g','д':'d','е':'e','ж':'zh','з':'z','и':'i','й':'y','к':'k','л':'l',
'м':'m','н':'n','о':'o','п':'p','р':'r','с':'s','т':'t','у':'u','ф':'f','х':'h','ц':'ts','ч':'ch',
'ш':'sh','щ':'sht','ъ':'a','ь':'y','ю':'yu','я':'ya'}
def translit(s): return "".join(_TR.get(ch, ch) for ch in (s or "").lower())
def norm_cyr(s):
    s = (s or "").lower()
    s = re.sub(r"[^0-9a-zа-я ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()
def norm_lat(s):
    s = translit(s); s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()
def pc4(z):
    d = re.sub(r"\D", "", z or ""); return d if len(d) == 4 else ""

# tip-arteră BG (ул.=улица/stradă, бул.=булевард, пл.=площад/piață, ж.к.=cartier) + bloc/intrare/etaj/ap — de scos
_BG_ARTERY = re.compile(r"(?i)\b(ул|улица|бул|булевард|пл|площад|жк|ж\s*к|бл|блок|вх|вход|ет|етаж|ап|апартамент|"
                        r"ul|bul|pl|str|ulica|bulevard|no|nr|№)\b\.?")
def _bg_street_core(a1):
    """Numele străzii din a1 BG: scoate tipul de arteră + tot de la primul NUMĂR (nr casă + bloc/etaj). Pt match în bg_streets."""
    s = norm_cyr(a1)                    # lowercase cirilic, alnum+space
    s = _BG_ARTERY.sub(" ", s)
    s = re.sub(r"\d.*$", "", s)         # taie de la prima cifră (nr casă + restul)
    return re.sub(r"\s+", " ", s).strip()

def find_street(cur, locality, a1):
    """Potrivește STRADA clientului în bg_streets (pt localitate) → cod poștal STRADĂ-specific (mai precis decât
    cel al localității). Fuzzy pe street_norm (cirilic) / street_lat (transliterat), prag 0.86. None dacă nu leagă.
    BG e locality-driven → asta NU respinge, doar RAFINEAZĂ zip-ul când strada chiar există."""
    core = _bg_street_core(a1)
    if not core or len(core) < 3:
        return None
    try:
        cur.execute("SELECT street_norm, street_lat, postcode FROM public.bg_streets WHERE city_norm=%s", (norm_cyr(locality),))
        rows = cur.fetchall()
    except Exception:
        return None
    if not rows:
        return None
    from difflib import SequenceMatcher
    lat = norm_lat(core); best_pc = None; best_r = 0.0
    for sn, sl, pc_ in rows:
        r = max(SequenceMatcher(None, core, sn or "").ratio(), SequenceMatcher(None, lat, sl or "").ratio())
        if r > best_r and pc_:
            best_r = r; best_pc = pc4(pc_)
    return best_pc if (best_r >= 0.86 and best_pc) else None

# ridicare de la oficiu curier / punct de livrare -> strada irelevanta, adresa e livrabila
_OFFICE = re.compile(
    r"(офис|еконт|еконтомат|спиди|спийди|автомат|автогара|куриер|до\s*офис|пощ|каса\s*на|"
    # + transliterări LATINE frecvente pe care clienții le scriu (Еконт→Ekont cu k, Спиди→Spidi/Spedi):
    r"econt|ekont|e-?kont|speedy|spidi|spiidi|spedi|spidy|office|ofis|ofice|kurier|courier|paketomat|dhl|gls|posta|bgpost)", re.I)
def is_office(text): return bool(_OFFICE.search(text or ""))
# „stradă lipsă" literală — clientul a scris explicit că n-are stradă (Няма / НямаС / nie ma / undefined)
_NOSTREET = re.compile(r"(няма|nyama|nqma|nie\s*ma|undefined|^\s*-*\s*$)", re.I)

# prefixe de tip localitate pe care le scoatem inainte de match
_LOCPFX = re.compile(r"^(гр|град|с|село|общ|община|обл|област|кв|ж\s*к)\b\.?\s*", re.I)
_KVSPLIT = re.compile(r"\b(кв|ж\s*к|квартал|жилищен|блок|бл|ул|улица|бул)\b\.?", re.I)
def city_candidates(city):
    """variante normalizate de incercat pt un camp 'city' murdar: 'Дупница , с. Червен брег',
    'ЕЛИН ПЕЛИН [СОФИЯ]', 'Бургас Кв Сарафово', 'Казанлък, Стара Загора' -> liste de nume curate.
    find_locality face match EXACT -> a genera candidati in plus e sigur (nu produce match fals)."""
    raw = city or ""
    out = []
    def add(x):
        nc = norm_cyr(_LOCPFX.sub("", _LOCPFX.sub("", (x or "").strip())))
        if nc and nc not in out: out.append(nc)
    for p in re.split(r"[\[\],/|]+", raw): add(p)   # bucati pe , [ ] / |
    for p in re.split(r"\s+-\s+", raw): add(p)      # pe ' - '
    for p in re.split(r"[\[\],/|]+", raw):          # prefixul dinaintea 'кв/жк/ул' (oras + cartier)
        m = _KVSPLIT.search(p)
        if m: add(p[:m.start()])
    add(raw)
    # n-grame din fata (prinde 'Бургас ...' / 'Стара Загора ...') — match exact => fara fals pozitiv
    toks = norm_cyr(_LOCPFX.sub("", raw.strip())).split()
    for k in (3, 2, 1):
        if len(toks) >= k: add(" ".join(toks[:k]))
    return out

_LAT_VAR = [("x", "h"), ("kh", "h"), ("ck", "k"), ("cz", "ch"), ("sch", "sh"), ("j", "y"), ("w", "v"), ("q", "k")]
_DEVOICE = {"t": "d", "p": "b", "k": "g", "s": "z", "f": "v", "sh": "zh"}


def fold_lat(s):
    """Pliază variantele cu care bulgarii scriu LATINIZAT același nume — sursa reală de „localitate negăsită".
    Două tipare măsurate pe comenzi blocate (2026-08-18):
      • „Xaskovo" = Хасково — X folosit pentru Х (Х seamănă cu X pe tastatură);
      • „Asenovgrat" = Асеновград — T final în loc de D, fiindcă AȘA SE AUDE (consoanele finale se
        devocalizează în bulgară). La fel: -p/-b, -k/-g, -s/-z, -f/-v.
    Nu inventăm nimic: pliem AMBELE părți la aceeași formă și comparăm. O localitate inexistentă tot nu se leagă."""
    t = norm_lat(s)
    for a, b in _LAT_VAR:
        t = t.replace(a, b)
    out = []
    for w in t.split():
        if len(w) > 3 and w[-1] in _DEVOICE:
            w = w[:-1] + _DEVOICE[w[-1]]
        out.append(w)
    return " ".join(out)


def find_locality_folded(cur, cands):
    """Ultima plasă înainte de „negăsit": compară forma PLIATĂ a numelui scris de client cu forma pliată a
    fiecărei localități. Cache pe proces (~11k localități, o singură interogare)."""
    global _ALL_LOC
    try:
        _ALL_LOC
    except NameError:
        _ALL_LOC = None
    if _ALL_LOC is None:
        cur.execute("SELECT name, name_lat, postcode FROM public.bg_localities WHERE name_lat<>'' AND postcode IS NOT NULL")
        _ALL_LOC = [(n, fold_lat(nl), pc) for n, nl, pc in cur.fetchall()]
    for c in cands:
        if not c or len(c) < 4:
            continue
        f = fold_lat(c)
        for name, fl, pc in _ALL_LOC:
            if fl == f:
                return name, pc
    return None


def find_locality(cur, cands):
    """intoarce (name, postcode) daca vreo varianta se potriveste (cirilic sau transliterat latin)."""
    for c in cands:
        if not c: continue
        # PREFERĂ rândul CU cod poștal (localitatea are multe rânduri — nodul place e des fără postcode; altul îl are)
        cur.execute("""SELECT name, postcode FROM public.bg_localities
                       WHERE name_norm=%s OR name_lat=%s
                       ORDER BY (postcode IS NOT NULL AND postcode<>'') DESC, cnt DESC LIMIT 1""", (c, norm_lat(c)))
        r = cur.fetchone()
        if r: return r[0], r[1]
    return None
def locality_for_pc(cur, pc):
    if not pc: return None
    cur.execute("""SELECT name FROM public.bg_localities WHERE postcode=%s
                   ORDER BY cnt DESC LIMIT 1""", (pc,))
    r = cur.fetchone()
    return r[0] if r else None

_MAJOR = None
def _major_cities(cur):
    """orașele MARI (city/town) — nume distinctive, sigure pt fuzzy/scan fără fals-pozitive. Cache pe date."""
    global _MAJOR
    if _MAJOR is None:
        cur.execute("SELECT DISTINCT name, name_norm FROM public.bg_localities WHERE place_type IN ('city','town') AND name_norm<>''")
        _MAJOR = cur.fetchall()
    return _MAJOR
def find_major_fuzzy(cur, cands):
    """typo de oraș MARE: 'стара затора'->'Стара Загора' (prag mare 0.88, doar orașe mari = sigur)."""
    from difflib import SequenceMatcher
    best = None; bestr = 0.0
    for c in cands:
        if len(c) < 5: continue
        for name, nn in _major_cities(cur):
            r = SequenceMatcher(None, c, nn).ratio()
            if r > bestr: bestr, best = r, name
    return best if bestr >= 0.88 else None
def find_major_anywhere(cur, text):
    """oraș MARE ascuns oriunde în text (ex notă 'Номерът ми е 42 Бургас ...' -> Бургас). Token/bigram EXACT."""
    toks = norm_cyr(text).split()
    majors = {nn: name for name, nn in _major_cities(cur)}
    for t in toks:
        if len(t) >= 4 and t in majors: return majors[t]
    for i in range(len(toks) - 1):
        bg = toks[i] + " " + toks[i + 1]
        if bg in majors: return majors[bg]
    return None

def bg_validate_and_correct(cur, city, zip_, address1, address2=""):
    a1 = address1 or ""; a2 = address2 or ""; cty = city or ""
    pc = pc4(zip_)

    # 1) ridicare de la oficiu curier -> livrabila (strada/cod irelevante)
    if is_office(a1) or is_office(a2) or is_office(cty):
        return {"status": "valid", "address": None, "note": "ridicare de la oficiu curier"}

    # 1b) FĂRĂ STRADĂ reală (Няма/НямаС/gol/doar-număr): localitatea poate fi validă DAR curierul cere o stradă
    #     → NU întoarce „valid" (ar duce la AWB nelivrabil, refuzat de DPD), NU e office (verificat mai sus)
    #     → status „cs": contact client pt stradă. (Distinct de needs_geocoder ca CS să știe EXACT ce lipsește.)
    _core = _bg_street_core(a1)
    if len(_core) < 2 or _NOSTREET.search(_core):
        # EXCEPȚIA SATULUI (regulă owner, 2026-08-18): la SAT adresa ESTE „localitate + număr" — strada nu
        # există prin construcție. Clientul care scrie „Няма" („niciuna"), „." sau doar numărul casei NU are
        # adresă incompletă; așa arată o adresă rurală. Măsurat: BONBG28212 = satul Добра поляна, cod 8580
        # UNIC → perfect livrabilă, dar stătea pe hold ca „fără stradă".
        # Condiție strictă: localitatea să fie `village` ȘI să aibă cod poștal. La ORAȘ strada rămâne
        # obligatorie (acolo „număr fără stradă" chiar e ambiguu) → rămâne „cs".
        _loc = find_locality(cur, city_candidates(cty)) or find_locality_folded(cur, city_candidates(cty))
        if _loc and _loc[1]:
            cur.execute("SELECT place_type FROM public.bg_localities WHERE name=%s AND postcode=%s LIMIT 1",
                        (_loc[0], _loc[1]))
            _r = cur.fetchone()
            if _r and (_r[0] or "") == "village":
                _nr = re.sub(r"\D", "", a1 or "") or re.sub(r"\D", "", a2 or "")
                return {"status": "corrected",
                        "address": {"city": _loc[0], "zip": _loc[1], "address1": (a1 or "").strip() or _nr},
                        "note": "adresă de SAT (%s, cod unic %s): localitate+număr, strada nu există" % (_loc[0], _loc[1])}
        return {"status": "cs", "address": None, "note": "fără stradă reală (%s) → contact client pt adresă" % ((a1 or "gol")[:20])}

    # 2) localitatea clientului e reala -> valid (BG locality-driven; strada/numar optionale).
    #    ZIP-FILL (owner): dacă lipsește codul poștal (4 cifre) → completează-l — STRADĂ-specific din bg_streets
    #    dacă strada se potrivește (mai precis), altfel reprezentativul localității din bg_localities.
    cands = city_candidates(cty)
    loc = find_locality(cur, cands)
    if loc:
        if not pc:
            z = find_street(cur, loc[0], a1) or (pc4(loc[1]) if loc[1] else "")
            if z:
                return {"status": "corrected", "address": {"city": loc[0], "zip": z, "address1": a1},
                        "note": "localitate reala (%s) + zip completat %s" % (loc[0], z)}
        return {"status": "valid", "address": None, "note": "localitate reala (%s)" % loc[0]}

    # 3) localitate negasita -> relocheaza din cod postal (4 cifre)
    if pc:
        dom = locality_for_pc(cur, pc)
        if dom:
            return {"status": "corrected", "address": {"city": dom, "zip": pc, "address1": a1},
                    "note": "oras corectat din cod postal"}

    # 3b) typo de oraș MARE ('Стара затора'->'Стара Загора') -- prag mare, doar orașe mari = sigur
    fd = find_locality_folded(cur, cands)
    if fd:
        return {"status": "corrected", "address": {"city": fd[0], "zip": fd[1] or pc, "address1": a1},
                "note": "localitate recunoscută prin transliterare pliată (%s → %s)" % (city, fd[0])}
    fz = find_major_fuzzy(cur, cands)
    if fz:
        return {"status": "corrected", "address": {"city": fz}, "note": "oras major corectat (typo)"}

    # 3c) oraș MARE ascuns în notă ('Номерът ми е 42 Бургас ...' -> Бургас)
    mj = find_major_anywhere(cur, cty + " " + a1)
    if mj:
        return {"status": "corrected", "address": {"city": mj}, "note": "oras major extras din text"}

    # 4) nerezolvabil determinist -> HERE
    return {"status": "needs_geocoder", "address": None, "note": "localitate/cod negasite in OSM"}


if __name__ == "__main__":
    import subprocess, psycopg2, urllib.parse as up
    KB = "/Users/gheorghebeschea/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"
    def secret(k): return subprocess.run(["uv", "run", KB, "secret-get", k], capture_output=True, text=True).stdout.strip()
    dsn = secret("DATABASE_URL_METRICS"); p = up.urlsplit(dsn)
    cn = psycopg2.connect(up.urlunsplit((p.scheme, p.netloc, p.path, "", ""))); cn.set_session(readonly=True); cur = cn.cursor()
    SAMPLE = [
        {"a1": "Офис на Спиди", "city": "Пловдив", "zip": "4000"},
        {"a1": "Еконт Бяла Слатина", "city": "Бяла Слатина", "zip": "3200"},
        {"a1": "с. Червен брег, ул. Бенковски 14", "city": "Дупница , с. Червен брег", "zip": "2629"},
        {"a1": "Ул.Васил Левски  - 1", "city": "Луковит", "zip": "Иконд"},
        {"a1": "Чаталджа 14", "city": "Девня", "zip": "9160"},
        {"a1": "Selu snejina", "city": "Varna", "zip": "9244"},
        {"a1": "Марица 20", "city": "ЕЛИН ПЕЛИН [СОФИЯ]", "zip": "2100"},
        {"a1": "ул. Непозната 999", "city": "НекadeSat", "zip": "1000"},
    ]
    from collections import Counter as C
    st = C()
    for s in SAMPLE:
        r = bg_validate_and_correct(cur, s.get("city"), s.get("zip"), s.get("a1"), "")
        st[r["status"]] += 1
        addr = ("→ %s" % r["address"]) if r.get("address") else ""
        print("%-13s %-30s %-22s %-6s %s [%s]" % (r["status"].upper(), s["a1"][:30], s["city"][:22], s["zip"], addr, r["note"]))
    print("\n", dict(st))
    cn.close()
