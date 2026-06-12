# /// script
# requires-python = ">=3.10"
# dependencies = ["pg8000>=1.30"]
# ///
"""
cs_comment_intelligence.py — INTELIGENȚĂ pe comentariile la reclame FB/IG.

Cele ~12.000 comentarii la reclame nu sunt zgomot: ascund LEAD-uri de cumpărare
(„cum comand?", „pret?", „vreau și eu") = vânzări pierdute, RECLAMAȚII PUBLICE pe
reclame live (scad CTR / cresc CPA + reputație) și TESTIMONIALE (social proof de refolosit).
Skill-ul le clasifică, le grupează PE MAGAZIN (din maparea pagină→magazin) și scoate
liste acționabile. Citește din DB-ul exportat de `gigi:richpanel-export` (după `richpanel_link.py`).

  uv run cs_comment_intelligence.py summary                       # tablou per magazin
  uv run cs_comment_intelligence.py leads --store Esteban         # intenții de cumpărare (lead-uri)
  uv run cs_comment_intelligence.py leads --open                  # doar cele încă deschise
  uv run cs_comment_intelligence.py complaints --store Grandia    # reclamații publice de moderat
  uv run cs_comment_intelligence.py praise --store Nubra          # testimoniale de refolosit
  uv run cs_comment_intelligence.py leads --store GT --json

Read-only. NU scrie/răspunde nimic (răspunsul rămâne manual / draft).
"""
import os, re, sqlite3, argparse, json

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", ".."))
DB = os.environ.get("RICHPANEL_DB") or os.path.join(REPO, "data", "richpanel_tickets.db")
import sys as _sys
_sys.path.insert(0, os.path.join(HERE, "..", "richpanel-export"))
import rp_db  # sursă: SQLite local (pipeline) sau Postgres partajat (agent CS)


# ── reguli de clasificare (fără diacritice, tunate pe exemple reale) ──
COMPLAINT = re.compile(r"nu\s*recomand|teap[ăa]|tzeap|escroc|prostea|proast|prosti|prost\b|ruginit|"
                       r"jale|nasol|naspa|groaznic|incult|minciun|inseal|in[sș]el|slab(a|ă)?\b|nu\s*(mai\s*)?(funct|merg)|"
                       r"ru[sș]ine|dezamag|oroare|ho[tț]i|bataie de joc|bătaie de joc|nu am primit|nu mi-a|"
                       r"reclama[tț]i|de proast|penibil|aiurea|furt|nu se vede|nu arata ca|altceva decat|"
                       r"oribil|oribil|nu\s*(mai\s*)?cump[ăa]r|prea\s*scump|ave[tț]i\s*grij|nu\s*e(ste)?\s*ceea|"
                       r"nu[- ]?i\s*recomand|dezgust|catastrof|tzap[aă]|de\s*c[aă]cat", re.I)
BUY = re.compile(r"cum\s*(pot|fac|se)?\s*comand|comand[ăa]?\s*\?|cum\s*cump|unde\s*(pot|comand|gasesc|cump)|"
                 r"pre[tț]\b|pretu|cat\s*cost|cât\s*cost|vreau\s*(si|și|sa|să)?\s*(eu|io|comand)|a[sș]\s*(dori|vrea)|"
                 r"doresc|m[ăa]\s*interes|ave[tț]i\b|se\s*mai\s*(poate|gase|găse)|mai\s*ave[tț]i|link\b|disponibil|"
                 r"in\s*stoc|pe\s*stoc|cum\s*platesc|livra[tț]i", re.I)
PRAISE = re.compile(r"recomand\b|recomand cu|super\b|excelent|mul[tț]umesc|f(oarte)?\s*bun|perfect|"
                    r"calitate|frumos|frumoas|mul[tț]umit|de\s*top\b|minunat|genial|ador\b|imi place|îmi place|"
                    r"foarte mul[tț]umit|nota 10|deosebit", re.I)
NOISE = re.compile(r"avertisment de la facebook|известие|încălcare gravă|incalcare grava|shared file", re.I)



# ── dicționare CZ/PL/BG (generate din comentarii reale, măsurate) ──
_CZ_DEACC = str.maketrans("áčďéěíňóřšťúůýžÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ", "acdeeinorstuuyzACDEEINORSTUUYZ")
def _deacc_cz(s):
    return (s or "").translate(_CZ_DEACC)

LANG = {
    "cz": {"complaint": re.compile('\\bnefunguj|\\bnefungoval|\\bnefungovaly?\\b|\\bnedoporucuji\\b|\\bnedoporucuju\\b|\\bpodvod|\\bokrada|\\bnaletel|\\bsmejd|\\bsmejdi\\b|\\bfake\\b|\\bklamav|\\bklamete|\\bklamano|\\bpredrazen|\\bdrahe\\b|\\bdrahy\\s+hadr|\\bstale\\s+(?:jeste\\s+)?drahe|\\bvyhozene?\\s+penize|\\bvychozene\\s+penize|\\bskoda\\s+penez|\\btlucka\\s+na\\s+penize|\\bvyhodit\\s+penize|\\bnic\\s+moc\\b|\\bje\\s+nanic\\b|\\bnanic\\b|\\bk\\s+nicemu\\b|\\bna\\s+nic\\b|\\bnani(?:c|ce)\\b|\\bza\\s+hovno\\b|\\bza\\s+prd\\b|\\bnestoji\\s+za|\\bstoji\\s+za\\s+(?:hovno|prd|nic|hovi)|\\bsmouh|\\bcmouh|\\bslizk(?:ej|y|a)\\s+hadr|\\bslizkej|\\bmazlav|\\bblemcav|\\bhroznej?\\s+hadr|\\bhroznej\\b|\\bnespokojen|\\bzklaman|\\bnespokojenost|\\bkravina\\b|\\bblbost\\b|\\bkecy\\b|\\bhnus\\b|\\btrapn|\\bhruza\\b|\\bzadny\\s+zazrak\\b|\\bzazrak\\s+(?:to\\s+)?neni\\b|\\bmagicka\\s+urcite\\s+neni\\b|\\bnevycisti|\\bnecisti\\b|\\bneutre\\b|\\bneutiraj|\\bnesaje|\\bneodstrani|\\bnepris(?:lo|ly|el|la)\\b|\\bnepris\\w*\\b|\\bobjednavka\\s+(?:nikde|nepris)|\\bzbozi\\s+nikde\\b|\\bobjednano\\s+a\\s+nic\\b|\\bobjednal?a?\\s+(?:jsem\\s+)?(?:si\\s+)?.{0,40}\\b(?:a\\s+nic|nepris|nikde)\\b|\\bcekam\\s+(?:na\\s+ne\\s+)?(?:uz\\s+)?(?:dva\\s+mesice|mesic|dva\\s+tydny|tyden)|\\bdva\\s+tydny\\b.*\\bnikde\\b|\\bnikde\\b.*\\bobjednav|\\bje\\s+to\\s+lez\\b|\\bto\\s+je\\s+lez\\b|\\blez\\b|\\bnic\\s+nepris|\\breklamace\\b|\\bnesrazil|\\bporadne\\s+to\\s+nemuzu\\b|\\bnemuzu\\s+ani\\b|\\bspokojena?\\s+nejsem\\b|\\bnejsem\\s+spokojen|\\bnesem\\s+spokojen|\\bskutek\\s+utek\\b|\\bvubec\\s+tak\\s+nefunguje\\b|\\bneoslovila\\b|\\bneosvedcila\\b', re.I), "lead": re.compile('jak\\s+(?:to\\s+)?(?:se\\s+)?(?:to\\s+)?objedna|jak\\s+(?:to\\s+|si\\s+(?:to|ji|je)\\s+)?koupit|\\bkde\\s+(?:se\\s+)?(?:to|da|ji|je)?\\s*(?:da\\s+)?(?:koupit|poridit|objednat|sehnat)|\\bkde\\s+jste\\s+objednav|\\bda\\s+se\\s+(?:to\\s+|ji\\s+)?koupit|\\bje\\s+mozne\\s+si\\s+(?:ji|to|je)\\s+koupit|\\b(?:chci|chtela?\\s+bych|chtel\\s+bych|rad\\s+bych|rada\\s+bych)\\b(?=.*(?:objedna|koupit|vyzkouset|poridit))|\\bjaka?\\s+(?:je\\s+)?cena\\b|\\bkolik\\s+(?:to\\s+)?stoji\\b|\\bza\\s+kolik\\b|\\bnapiste\\s+cenu\\b|\\bnenapis\\w*\\s+cenu\\b|\\bnenapise\\s+cenu\\b|\\bmate\\s+i?\\s*(?:jine|vetsi|jiny|jeste)\\s+(?:rozmer|velikost|rozmery)|\\bproc\\s+ne(?:jde|lze)\\s+koupit', re.I), "praise": re.compile('(?<!ne)\\bdoporucuji\\b|\\bvrele\\s+doporucuji\\b|(?<!ne)\\bdoporucuju\\b|\\bsupr\\b|\\bsuper\\b|\\bvyborn|\\bperfektni\\b|\\bskvel|\\buzasn|\\bparada\\b|\\bparadni|\\bbomba\\b|\\bbezva\\b|\\bspokojen(?!ost\\s+nikde)|\\bvelka\\s+spokojenost\\b|\\bvelika\\s+spokojenost\\b|\\bvelmi\\s+dekuji\\b|\\bmoc\\s+dekuji\\b|\\bdekuju\\s+(?:moc|vam)\\b|\\btop\\b|\\bnejlepsi\\b|\\bjsou\\s+dobre\\b|\\bje\\s+dobra\\b|\\bjsou\\s+fajn\\b|\\bvynikajici\\b|\\bkouzeln|\\bvecicka\\b|\\bdela\\s+divy\\b|\\bzari\\s+cistotou\\b|\\bkupte\\s+si\\b|\\bneudelate\\s+chybu\\b|\\bunesen|\\bnadsen|\\bprekvapilo|\\bmuzu\\s+potvrdit\\b|\\bmohu\\s+potvrdit\\b|\\bnemuzu\\s+si\\s+vynachvalit\\b|\\bvynachvalit\\b', re.I), "praise_kill": re.compile('\\bspokojena?\\s+nejsem\\b|\\bnejsem\\s+spokojen|\\bnesem\\s+spokojen|\\bnesem\\s+s\\s+ni\\s+spokojen|\\bco\\s+je\\s+na\\s+(?:ni|nich|nem)\\s+super\\b|\\bsuper\\s+neni\\b|\\bnic\\s+moc\\b|\\bk\\s+nicemu\\b|\\b(?:jaky|jaka)\\s+(?:je\\s+)?(?:prosim\\s+)?rozmer\\b|\\bsdelit\\s+rozmer\\b|\\bnefunguj.*\\b(?:super|doporucuji)|\\b(?:super|doporucuji).*\\bnefunguj', re.I), "deacc": True},
    "pl": {"complaint": re.compile('(?i)nie\\s+poleca\\w*|\\bodradzam\\b|\\bnie\\s+radze\\b|oszust\\w*|wyludze\\w*|naciag\\w*|nabij\\w*|nabra[c]?\\b|nabran\\w*|nabier\\w*|fals\\w*|falsz\\w*|podrob\\w*|podrub\\w*|\\bsciem\\w*|\\bscim\\w*|\\blip[ay]\\b|\\bszajs\\w*|bzdur\\w*|bzdet\\w*|bujd\\w*|klamstw\\w*|klamcz\\w*|\\bklam\\w*|\\bbubel\\b|\\bbuble\\b|badziew\\w*|\\bszmat\\w*|\\bdruciak|do\\s+(dupy|kitu|bani|niczego|wyrzuceni|kosza|smierci|wyrzucenia)|nadaj\\w*\\s+sie\\s+do\\s+(niczego|wyrzuc|kosza|kitu)|nic\\s+nie\\s+(wart|czysc|robi|usuw|schodzi|pomog|zrobi|scier|wyczysc|zmyw)|nic\\s+(niewart|wart\\w*)|\\bnic\\s+wart|szkoda\\s+(pieni|kasy|forsy|slow|grosza)|strat\\w*\\s+pieni|zmarnowan\\w*|wyrzucon\\w*\\s+kas|wyrzucil\\w*\\s+pieni|stracone\\s+pieni|wydane\\s+pieni|tylko\\s+wydan|wyciagan\\w*\\s+(z\\s+)?(kas|portfel|kiesz)|bije\\s+po\\s+kiesz|po\\s+kieszeni|wyczysci\\s+(ale\\s+)?portfel|gowno\\s+prawd|\\bg\\s+prawda|\\bgowno\\b|gupot\\w*|glupot\\w*|glupi\\w*|dyrdymal\\w*|pic\\s+na\\s+wod|nie\\s+prawd\\w*|nieprawd\\w*|nie\\s+(dzial|sprawdz|usuw|czysc|nadaj|spelni|zmyw|schodz|skuteczn)\\w*|nieskuteczn\\w*|nie\\s+(jestem|byl\\w*)\\s+zadowol|niezadowol\\w*|niezadowo\\b|zawiedz\\w*|zadna\\s+rewelacj|zadnego\\s+rezultat|bez\\s+rewelacj|szal\\w*\\s+(nie\\s*)?ma\\b|szoku\\s+nie\\s+ma|nic\\s+specjaln|nic\\s+rewelacyjn|przereklamow\\w*|przesadzon\\w*|bez\\s+przesady|\\bprzesada\\b|beznadziej\\w*|okropn\\w*|nie\\s+kupuj|nie\\s+dajcie\\s+sie|nie\\s+dajmy\\s+sie|nie\\s+oklam|nie\\s+nabij|tylko\\s+na\\s+(filmiku|filmie|reklamie)|nie\\s+(jest|sa)\\s+tak\\w*\\s+(jak|dobr|skuteczn)|nie\\s+jest\\s+taka\\s+(dobra|jak)|nie\\s+takie\\s+jak|wcale\\s+(tak\\s+)?nie|nie\\s+zauwazyl\\w*\\s+\\w*\\s*efekt|reklama\\s+falszyw|falszyw\\w*\\s+rekl|nie\\s+otrzymal\\w*', re.I), "lead": re.compile('(?i)\\bjak(a|ie|i)?\\s+(cena|kosztuj)|\\bw\\s+jakiej\\s+cenie|\\bjakiej\\s+cenie\\b|\\bile\\s+kosztuj|\\bile\\s+(te|to|za)\\b|\\bkosztuj\\w*|\\bgdzie\\s+(zamow|kupi|moz|dostan)|\\bjak\\s+(zamow|kupi|moz|ich\\s+uzywac|uzywac|dlugo\\s+czeka)|\\bczy\\s+mozna\\s+kupi|\\bmozna\\s+kupic|\\bza\\s+pobranie\\b|\\bchce\\s+kupi|\\bchetnie\\s+kup|\\bkupie\\b|\\bchcial\\w*\\s+kupi|\\bpoprosze\\s+o\\s+cen|\\bpoprosze\\s+cen|\\bjakie\\s+rozmiary|\\bjaki\\s+rozmiar|\\bdlaczego\\s+tak\\s+drogo|\\btak\\s+drogo\\b|^\\s*cena\\??\\s*$|^\\s*cene\\b|^\\s*jaka\\s+cena', re.I), "praise": re.compile('(?i)(?:(?<![a-z])polecam\\b)|godne\\s+poleceni|poleca\\s+sie\\b|polecam\\s+serdeczn|\\bswietn\\w*|\\bswietnie\\b|(?<!nie\\s)\\bsuper\\b|\\bcudown\\w*|\\bcudo\\b|\\bpiekn\\w*|\\bnajlepsz\\w*|\\bzajebist\\w*|jestem\\s+zadowol|bardzo\\s+dobre|\\bsa\\s+dobre\\b|\\bsa\\s+super\\b|\\bjest\\s+super\\b|\\bsa\\s+swietne\\b|dziekuj\\w*|to\\s+rewelacja', re.I)},
    "bg": {"complaint": re.compile('(?i)л[ъь]ж(?:а|и|ете|ци|еш|е|ат)|\\bфалшив|изкуственяк|боклу[кч]|глупост[и]?|нищо\\s+(?:не\\s+)?(?:върш|струва|чист|изтрив|изпир|почист|общо|повече|вярно)|не\\s+(?:върш|струва|чист|почист)|не\\s+(?:с[ъь]м|съм)\\s+доволн|нес[ъь]м\\s+доволн|не\\s+(?:вярвайте|купувайте|се\\s+л[ъь]жете)|не\\s+са\\s+(?:хубави|добри)|не\\s+е\\s+(?:добра|хубава|вярно)|за\\s+(?:изхвърл|аруц)|изхвърлих|de\\s+aruncat|реклама\\s*!|само\\s+(?:си\\s+)?(?:правят\\s+)?реклама|нищо\\s+повече\\s+от\\s+реклама|престанете\\s+да\\s+л[ъь]ж|умеете\\s+да\\s+л[ъь]ж|спрете\\s+да\\s+л[ъь]ж|никаква\\s+работа|не\\s+вършат\\s+(?:това|работа)|не\\s+вършат\\b|кон[цч]и|ламе|лигав|замазват|сапълни\\s+боклу|nu\\s+(?:e\\s+bun|functioneaz|se\\s+conecteaz)|nimic|т[еи]са\\s+фалшив|пълна\\s+(?:л[ъь]жа|глупост)', re.I), "lead": re.compile('(?i)как\\s+да\\s+(?:поръч|пор[ъь]ч|взем|купя|я\\s+взем|ги\\s+взем|се\\s+поръч)|искам\\s+да\\s+(?:поръч|пор[ъь]ч|пот[ъь]ч|взем|я\\s+поръч|ги\\s+поръч|купя)|искам\\s+(?:ги|едно|и\\s+аз|да\\s+си)|да\\s+(?:поръч|пор[ъь]ч)а[мт]?\\b|\\bпоръч(?:вам|ай|айте|ка|кам)\\b|\\bцена\\b|цена\\?|\\bцената\\b|каква\\s+е\\s+цена|колко\\s+струва|капацитет|каква\\s+(?:е\\s+)?капацит|ce\\s+capacitate|capacitate|а\\s+купи(?:л|ло)\\s+ли\\s+някой|купил\\s+ли\\s+някой|a\\s+cumparat\\s+cineva|откъде\\s+(?:да\\s+)?(?:купя|поръч|взем)|къде\\s+(?:да\\s+)?(?:купя|поръч|взем|се\\s+поръч)|как\\s+да\\s+ги\\s+(?:взема|купя|поръчам)|интересувам\\s+се|интересува\\s+ме', re.I), "praise": re.compile('(?i)страхотн|срахотно|стахотн|препоръчвам|препор[ъь]чвам|върши\\s+(?:много\\s+)?(?:добра|добре)\\s+работа|върши\\s+работа|добра\\s+работа|работят\\s+(?:чудесно|страхотно|отлично)|чудесн|отличн|\\bсупер\\b|перфектн|прекрасн|почистват\\s+(?:страхотно|чудесно|перфектно|отлично)|срахотно\\s+почистват|благодар|мерси|(?<!не\\s)(?<!не)много\\s+(?:съм\\s+)?доволн|(?<!не\\s)(?<!не)съм\\s+доволн|доволен\\s+съм|доволна\\s+съм|препоръчвам\\s+ги|горещо\\s+препоръчвам', re.I)},
}
STORE_LANG = {"Bonhaus CZ": "cz", "Bonhaus PL": "pl", "Bonhaus BG": "bg"}

def classify(text, store=None):
    t = text or ""
    if NOISE.search(t):
        return "zgomot"
    lang = STORE_LANG.get(store)
    if lang:
        L = LANG[lang]
        tt = _deacc_cz(t) if L.get("deacc") else t
        if L["complaint"].search(tt):
            return "reclamatie"
        if L["lead"].search(tt):
            return "lead"
        if L["praise"].search(tt) and not (L.get("praise_kill") and L["praise_kill"].search(tt)):
            return "testimonial"
        if "?" in t:
            return "intrebare"
        return "neutru"
    if COMPLAINT.search(t):
        return "reclamatie"
    if BUY.search(t):
        return "lead"
    if PRAISE.search(t):
        return "testimonial"
    if "?" in t:
        return "intrebare"
    return "neutru"


LABEL = {"lead": "🟢 lead", "reclamatie": "🔴 reclamație", "testimonial": "⭐ testimonial",
         "intrebare": "❓ întrebare", "neutru": "· neutru", "zgomot": "zgomot"}


def load():
    con = rp_db.open(DB)
    has_rs = any(r[1] == "resolved_store" for r in con.execute("PRAGMA table_info(tickets)"))
    sc = "resolved_store" if has_rs else "store"
    rows = con.execute(f"SELECT id,conversation_no,{sc},status,created_at,COALESCE(first_message,'')||' '||COALESCE(subject,'') "
                       "FROM tickets WHERE channel LIKE '%comment%'").fetchall()
    con.close()
    out = []
    for tid, no, store, status, created, text in rows:
        out.append({"id": tid, "no": no, "store": store or "necunoscut", "status": status,
                    "date": str(created or "")[:10] if not str(created or "").isdigit() else created,
                    "text": " ".join((text or "").split()), "type": classify(text, store or "necunoscut")})
    return out


def summary(items):
    stores = {}
    for it in items:
        s = stores.setdefault(it["store"], {"lead": 0, "reclamatie": 0, "testimonial": 0, "intrebare": 0, "neutru": 0, "zgomot": 0, "tot": 0})
        s[it["type"]] += 1; s["tot"] += 1
    print("═" * 78)
    print("  COMMENT INTELLIGENCE — %d comentarii la reclame FB/IG" % len(items))
    print("═" * 78)
    print("  %-16s %6s %7s %7s %7s %7s" % ("MAGAZIN", "total", "🟢lead", "🔴recl", "⭐testi", "❓intr"))
    for st, s in sorted(stores.items(), key=lambda x: -x[1]["tot"]):
        if s["tot"] < 5:
            continue
        print("  %-16s %6d %7d %7d %7d %7d" % (st[:16], s["tot"], s["lead"], s["reclamatie"], s["testimonial"], s["intrebare"]))
    tl = sum(s["lead"] for s in stores.values())
    tr = sum(s["reclamatie"] for s in stores.values())
    tt = sum(s["testimonial"] for s in stores.values())
    op_lead = sum(1 for it in items if it["type"] == "lead" and (it["status"] or "").upper() == "OPEN")
    print("─" * 78)
    print("  💰 %d LEAD-uri (intenție de cumpărare) — vânzări de recuperat (%d încă DESCHISE)" % (tl, op_lead))
    print("  🔴 %d RECLAMAȚII publice pe reclame live — moderare/reputație + CPA" % tr)
    print("  ⭐ %d TESTIMONIALE — social proof de refolosit" % tt)
    print("\n  → detalii: leads / complaints / praise  [--store X] [--open]")


def listing(items, typ, store, only_open, as_json):
    sel = [it for it in items if it["type"] == typ
           and (not store or it["store"].lower() == store.lower())
           and (not only_open or (it["status"] or "").upper() == "OPEN")]
    sel.sort(key=lambda x: str(x["date"]), reverse=True)
    if as_json:
        print(json.dumps(sel, ensure_ascii=False, indent=2, default=str)); return
    head = {"lead": "🟢 LEAD-URI (intenție cumpărare)", "reclamatie": "🔴 RECLAMAȚII publice",
            "testimonial": "⭐ TESTIMONIALE"}[typ]
    print("═" * 78)
    print("  %s — %d%s%s" % (head, len(sel), (" | magazin: " + store) if store else "", " | doar deschise" if only_open else ""))
    print("═" * 78)
    for it in sel[:80]:
        st = (it["status"] or "")[:6]
        print("  [%-12s %-6s %s] %s" % (it["store"][:12], st, str(it["date"])[:10], it["text"][:120]))
    if len(sel) > 80:
        print("  … încă %d (folosește --json pt toate)" % (len(sel) - 80))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["summary", "leads", "complaints", "praise"])
    ap.add_argument("--store"); ap.add_argument("--open", action="store_true"); ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if not os.path.exists(DB):
        print("Nu găsesc DB-ul:", DB, "\nRulează întâi gigi:richpanel-export (pull + richpanel_link.py)."); return
    items = load()
    if a.mode == "summary":
        summary(items)
    else:
        typ = {"leads": "lead", "complaints": "reclamatie", "praise": "testimonial"}[a.mode]
        listing(items, typ, a.store, a.open, a.json)


if __name__ == "__main__":
    main()
