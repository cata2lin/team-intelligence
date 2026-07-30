# -*- coding: utf-8 -*-
"""cz_nomenclator.py — validator+corector CZ pe metrics.public.cz_addresses (RÚIAN agregat).
CZ e localitate-driven (74% obec = 1 PSČ; PSČ grosier). Confirmă PSČ+localitate (livrabil) chiar când HERE pică
pe sate, curăță orașul garbled, derivă PSČ lipsă din localitate. Număr casă obligatoriu.
API: cz_validate_and_correct(cur, city, zip_, address1, address2) -> {status, address, note}."""
import re, unicodedata
from collections import Counter
from difflib import SequenceMatcher

def strip_dia(s):
    if not s: return ""
    s=unicodedata.normalize("NFD",s)
    return "".join(ch for ch in s if unicodedata.category(ch)!="Mn")
def norm(s):
    s=strip_dia(s or "").lower()
    s=re.sub(r"[^a-z0-9 ]+"," ",s)
    return re.sub(r"\s+"," ",s).strip()

def _psc(z):
    d=re.sub(r"\D","",z or "")
    return d if len(d)==5 else ""
def house_number(*parts):
    """număr casă CZ: č.p. (domovní) sau orientační. Forme '899','915/31','28/1738','390' — dar ADESEA LIPIT
    de stradă ('Ptacnik714','Hodonin20','Sejřek46'): regex-ul vechi cu \\b rata astea → 'fără număr casă' fals."""
    for t in parts:
        if not t: continue
        m=re.search(r"(\d{1,4}(?:/\d{1,4})?[a-zA-Z]?)", t)   # fără \b la început: prinde numărul lipit de literă
        if m: return m.group(1)
    return None

def _dictify(cur):
    cols=[d[0] for d in cur.description]
    return [dict(zip(cols,r)) for r in cur.fetchall()]
def load_by_psc(cur, psc):
    cur.execute("SELECT obec,district,cast_obce,ulice,psc,num_min,num_max,cnt,obec_norm,ulice_norm FROM public.cz_addresses WHERE psc=%s",(psc,))
    return _dictify(cur)
def load_by_locality(cur, obec):
    on=norm(obec)
    if not on: return []
    cur.execute("SELECT obec,district,cast_obce,ulice,psc,num_min,num_max,cnt,obec_norm,ulice_norm FROM public.cz_addresses WHERE obec_norm=%s",(on,))
    return _dictify(cur)

def psc_localities(rows):
    """localitățile unui PSČ, sortate după nr de adrese (cea mai mare = principală)."""
    c=Counter()
    for r in rows: c[r["obec"]] += (r.get("cnt") or 1)
    return [o for o,_ in c.most_common()]
def city_matches(city, rows):
    nc=norm(city)
    if not nc: return None
    locs={r["obec_norm"] for r in rows}
    if nc in locs: return True
    # fuzzy: prescurtări / substring (ex 'haviřov sumbark' ⊃ 'havirov')
    for ln in locs:
        if ln and (ln in nc or nc in ln or SequenceMatcher(None,ln,nc).ratio()>=0.86): return True
    return False

# tip-arteră CZ (ul.=ulice/stradă, nám.=náměstí/piață, tř.=třída/bulevard, sídl.=sídliště) de scos pt match stradă
_CZ_ARTERY=re.compile(r"(?i)\b(ul|ulice|nam|namesti|tr|trida|nabr|nabrezi|sidl|sidliste)\b\.?")
def _cz_street_core(a1):
    s=norm(a1); s=_CZ_ARTERY.sub(" ",s); s=re.sub(r"\d.*$","",s)  # taie de la prima cifră (nr casă)
    return re.sub(r"\s+"," ",s).strip()
def cz_street_psc(rows, address1):
    """Din rândurile localității (load_by_locality) potrivește STRADA clientului → PSČ STRADĂ-specific (mai precis
    decât 'localitate cu N PSČ, ambiguu'). Fuzzy pe ulice_norm ≥0.88 (sau substring). None dacă nu leagă."""
    core=_cz_street_core(address1)
    if not core or len(core)<3: return None
    best=None; bestr=0.0
    for r in rows:
        un=r.get("ulice_norm") or ""
        if not un: continue
        rr=SequenceMatcher(None,core,un).ratio()
        if core in un or un in core: rr=max(rr,0.9)
        if rr>bestr and r.get("psc"): bestr=rr; best=r
    return best.get("psc") if (bestr>=0.88 and best) else None

def _cz_city_denoise(city):
    """Curăță câmpul ORAȘ CZ: 'Praha 10'/'Praha 110'→'Praha' (curierul livrează Praha+stradă), 'Obec Bukovec'→
    'Bukovec', sufix după virgulă ('Hradec Králové, Kralov'→'Hradec Králové'), district lipit ('Plzeň-jih'→'Plzeň')."""
    c=(city or "").strip()
    c=re.sub(r"(?i)^\s*(obec|okres|mesto|město|statutární město|mč)\s+","",c)  # prefix admin
    c=c.split(",")[0].strip()                                                   # sufix după virgulă
    if re.match(r"(?i)^praha\b",c): return "Praha"                              # Praha N / district → Praha
    c=re.sub(r"\s+\d.*$","",c).strip()                                          # taie de la primul număr (district/junk)
    return c or (city or "").strip()

_CZ_LOCS=None
def cz_locality_fuzzy(cur, city):
    """Typo de localitate ('Vsetim'→'Vsetín', 'Ostrva'→'Ostrava'): fuzzy pe obec_norm distinct, prag 0.9 (sigur).
    Întoarce (obec, rows) sau None. Cache pe proces (5.3k localități)."""
    global _CZ_LOCS
    nc=norm(city)
    if len(nc)<4: return None
    if _CZ_LOCS is None:
        cur.execute("SELECT DISTINCT obec, obec_norm FROM public.cz_addresses WHERE obec_norm<>''")
        _CZ_LOCS=cur.fetchall()
    best=None; bestr=0.0
    for obec, on in _CZ_LOCS:
        if not on: continue
        rr=SequenceMatcher(None,nc,on).ratio()
        if rr>bestr: bestr=rr; best=obec
    if bestr>=0.9 and best:
        return best, load_by_locality(cur, best)
    return None

def cz_validate_and_correct(cur, city, zip_, address1, address2=""):
    a1=address1 or ""; a2=address2 or ""; cty=_cz_city_denoise(city)
    num=house_number(a1,a2)
    # CZ: adresele FĂRĂ număr casă SE LIVREAZĂ (istoric 46.996 livrate → 1.729 fără număr = 3,7%; curierul CZ
    # livrează pe stradă+oraș, inclusiv Praha). NU mai blocăm pe lipsa numărului — lăsăm localitatea/PSČ să decidă:
    # localitate/PSČ bune ⇒ valid/corrected (livrabil); localitate proastă ⇒ needs_geocoder → CS oricum, mai jos.
    # (decizie user 2026-07-25: „vezi istoric... si zic sa le trimiti".) `num` rămas doar pt notă.
    psc=_psc(zip_)
    if psc:
        rows=load_by_psc(cur,psc)
        if rows:
            locs=psc_localities(rows)
            if city_matches(cty,rows):
                return {"status":"valid","address":None,"note":"PSČ+localitate OK (livrabil)"}
            # oraș garbled/prescurtat: dacă PSČ are 1 localitate dominantă → corectez orașul la ea
            if locs:
                new_city=locs[0]
                if norm(new_city)!=norm(cty):
                    # Ambiguu (oraș ≠ localitatea PSČ). TIEBREAKER = address1:
                    #  - dacă address1 CONȚINE localitatea PSČ -> clientul e ACOLO (a pus orașul apropiat din
                    #    obișnuință) -> corectez orașul la localitatea PSČ (ex 'Šumperk' dar a1='Jindřichov 81').
                    #  - altfel, dacă orașul clientului e o localitate REALĂ -> probabil PSČ-ul e greșit, nu orașul
                    #    -> PĂSTREZ orașul, corectez PSČ-ul (ex 'Mělník', a1='...Mělník...') (ca la PL/RO).
                    psc_loc_in_a1 = bool(norm(new_city)) and norm(new_city) in norm(a1)
                    if not psc_loc_in_a1:
                        own=load_by_locality(cur,cty)
                        if own:
                            opscs=Counter(r["psc"] for r in own if r.get("psc"))
                            dz=opscs.most_common(1)[0][0] if opscs else ""
                            if dz and dz!=psc:
                                return {"status":"corrected","address":{"city":cty,"zip":dz,"address1":a1},
                                        "note":"oraș real păstrat, PSČ corectat din localitate"}
                            return {"status":"valid","address":None,"note":"oraș real păstrat (PSČ inconsistent)"}
                    # address1 confirmă localitatea PSČ, SAU orașul e garbled -> corectez orașul din PSČ
                    return {"status":"corrected","address":{"city":new_city,"zip":psc,"address1":a1},
                            "note":"oraș corectat din PSČ (%d loc.)"%len(locs)}
                return {"status":"valid","address":None,"note":"PSČ OK"}
        # PSČ inexistent în RÚIAN → încerc invers din localitate
    cands=load_by_locality(cur,cty)
    if cands:
        pscs=Counter(r["psc"] for r in cands if r.get("psc"))
        if len(pscs)==1:
            dz=next(iter(pscs));
            return {"status":"corrected","address":{"city":cty,"zip":dz,"address1":a1},
                    "note":"PSČ derivat din localitate (unic)"}
        if pscs:
            # multe PSČ: dacă orașul e găsit, e livrabil pe localitate+număr (PSČ grosier oricum) → valid dacă PSČ dat era ok
            if psc and psc in pscs:
                return {"status":"valid","address":None,"note":"localitate+PSČ consistente"}
            # localitate REALĂ dar PSČ ambiguu/greșit: (b) încearcă PSČ STRADĂ-specific din cz_addresses; altfel CZ
            # livrează pe localitate+stradă (docstring + decizie owner) → PSČ reprezentativ, NU respinge un oraș real.
            spc=cz_street_psc(cands, a1)
            if spc:
                return {"status":"corrected","address":{"city":cty,"zip":spc,"address1":a1},
                        "note":"PSČ stradă-specific (localitate cu %d PSČ)"%len(pscs)}
            if psc:  # PSČ plauzibil (5 cifre) dar nu în lista localității + fără match stradă → PĂSTREZ (nu ghicesc peste)
                return {"status":"valid","address":None,"note":"localitate reală, PSČ păstrat (livrabil pe loc.+stradă)"}
            rep=pscs.most_common(1)[0][0]  # PSČ LIPSĂ → completez cu reprezentativul localității
            return {"status":"corrected","address":{"city":cty,"zip":rep,"address1":a1},
                    "note":"localitate reală, PSČ reprezentativ completat"}
    # localitate negăsită direct → TYPO? fuzzy (Vsetim→Vsetín, Ostrva→Ostrava, prag 0.9)
    fz=cz_locality_fuzzy(cur, cty)
    if fz:
        fobec, frows=fz
        fpscs=Counter(r["psc"] for r in frows if r.get("psc"))
        spc=cz_street_psc(frows, a1) or (fpscs.most_common(1)[0][0] if fpscs else "")
        if spc:
            return {"status":"corrected","address":{"city":fobec,"zip":spc,"address1":a1},
                    "note":"localitate corectată (typo→%s) + PSČ"%fobec}
        return {"status":"corrected","address":{"city":fobec,"address1":a1},
                "note":"localitate corectată (typo→%s)"%fobec}
    return {"status":"needs_geocoder","address":None,
            "note":"localitate/PSČ negăsite în RÚIAN" + ("" if num else " (+fără număr)")}


if __name__=="__main__":
    import subprocess, psycopg2, urllib.parse as up, json
    KB="/Users/gheorghebeschea/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"
    def secret(k): return subprocess.run(["uv","run",KB,"secret-get",k],capture_output=True,text=True).stdout.strip()
    dsn=secret("DATABASE_URL_METRICS"); p=up.urlsplit(dsn)
    cn=psycopg2.connect(up.urlunsplit((p.scheme,p.netloc,p.path,"",""))); cn.set_session(readonly=True); cur=cn.cursor()
    SAMPLE=[
        {"a1":"Zdechovice 3","city":"Nový Bydžov","zip":"504 01"},
        {"a1":"Mouřínov","city":"Bučovice","zip":"685 01"},
        {"a1":"Plchůvky 40, 565 01 Choceň","city":"Pardubický Kraj","zip":"565 01"},
        {"a1":"Čalounická 899","city":"Šenov u Ostravy","zip":"739 34"},
        {"a1":"Masarykova Třída 915/31,kadernictvi Adora beauty","city":"Teplice","zip":"415 01"},
        {"a1":"316","city":"Děčín 32","zip":"407 11"},
        {"a1":"Evidenční 112","city":"Svinařov","zip":"273 05"},
        {"a1":"Hamr 126 Trhanov","city":"34533 | Chodov | Trhanov | Domažlice | CZ","zip":"345 33"},
        {"a1":"514","city":"Ostopovice 514","zip":"664 49"},
        {"a1":"V K Klicpery 287/2, 736 01 Havířov","city":"Haviřov Šumbark","zip":"736 01"},
        {"a1":"Větrná 12","city":"Ustin.l.","zip":"400 11"},
        {"a1":"Května 28/1738","city":"Bruntal","zip":"792 01"},
        {"a1":"Čsa 390","city":"Hlinsko","zip":"539 01"},
        {"a1":"Robčice","city":"Plzeň Nepřišli","zip":"333 09"},
        {"a1":"Kpt. Jaroše z","city":"Tovačov","zip":"751 01"},
    ]
    from collections import Counter as C
    st=C()
    for s in SAMPLE:
        r=cz_validate_and_correct(cur,s.get("city"),s.get("zip"),s.get("a1"),"")
        st[r["status"]]+=1
        addr=("→ %s"%r["address"]) if r.get("address") else ""
        print("%-9s %s | %s %s  %s [%s]"%(r["status"].upper(),s["a1"][:28],s["city"][:20],s["zip"],addr,r["note"]))
    print("\n", dict(st))
    cn.close()
