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

def cz_validate_and_correct(cur, city, zip_, address1, address2=""):
    a1=address1 or ""; a2=address2 or ""; cty=city or ""
    num=house_number(a1,a2)
    if not num:
        return {"status":"cs","address":None,"note":"fără număr casă"}
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
            return {"status":"needs_geocoder","address":None,"note":"localitate cu %d PSČ, ambiguu"%len(pscs)}
    return {"status":"needs_geocoder","address":None,"note":"localitate/PSČ negăsite în RÚIAN"}


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
