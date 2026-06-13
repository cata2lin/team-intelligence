# /// script
# requires-python = ">=3.10"
# dependencies = ["pg8000>=1.30"]
# ///
"""
cs_sentiment.py — SCOR DE SENTIMENT per tichet CS (negativ / neutru / pozitiv + intensitate).
Pe toate canalele, multilingv (RO + CZ/PL/BG). Sortează „cei mai furioși clienți întâi",
arată trendul de sentiment pe magazin/agent și scoate tichetele negative deschise pt triaj.
Reciclează dicționarele de reclamație/laudă (cs-comment-intelligence) + semnalele de
frustrare/escaladare (cs-quality-audit). Citește richpanel_tickets.db (read-only).

  uv run cs_sentiment.py summary                 # distribuție sentiment per magazin + agent
  uv run cs_sentiment.py negative --open         # cele mai negative tichete DESCHISE (triaj)
  uv run cs_sentiment.py negative --store Grandia
  uv run cs_sentiment.py trend                   # trend sentiment pe luni
  uv run cs_sentiment.py negative --json
Read-only. NU scrie nimic.
"""
import os, re, sqlite3, argparse, json, collections, unicodedata, urllib.request, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", ".."))
DB = os.environ.get("RICHPANEL_DB") or os.path.join(REPO, "data", "richpanel_tickets.db")
import sys as _sys
_sys.path.insert(0, os.path.join(HERE, "..", "richpanel-export"))
import rp_db  # sursă: SQLite local (pipeline) sau Postgres partajat (agent CS)


# ── RO: reclamație + laudă ──
RO_NEG = re.compile(r"nu\s*recomand|teap[\u0103a]|escroc|prostea|proast|prost\b|ruginit|jale|nasol|naspa|groaznic|"
                    r"incult|minciun|in[s\u0219]el|slab|nu\s*(mai\s*)?(funct|merg)|ru[s\u0219]ine|dezamag|oroare|"
                    r"ho[t\u021b]i|bataie de joc|reclama[t\u021b]i|penibil|aiurea|furt|oribil|nu\s*(mai\s*)?cump[\u0103a]r|"
                    r"prea\s*scump|dezgust|catastrof|de\s*c[\u0103a]cat|nu\s*e(ste)?\s*ceea|inadmisibil|jaf|"
                    # \u2500\u2500 recall boost: reclama\u021bii reale care lipseau (variante cu/f\u0103r\u0103 diacritice) \u2500\u2500
                    r"nemul[t\u021b]umit|dezam[\u0103a]g|[i\u00ee]n[s\u0219]el(?:at|[\u0103a]t|[\u0103a]ci|[\u0103a]tor|[\u0103a]ri)|"
                    r"p[\u0103a]c[\u0103a]lit|nu\s*(ce|ceea\s*ce)\s*am\s*comandat|alt\s*produs(\s*dec[\u0103a\u00e2]t)?|"
                    r"mi\s*s-?au\s*gre[sș]|gre[sș]it[ea]?\s*(parfum|produs|comand|marim|culoar|model)|am\s*primit\s*(alt|alte|gre[sș])|"
                    r"produs\s*gre[s\u0219]it|nu\s*corespunde|nu\s*(e|este)\s*ca\s*[i\u00ee]n\s*(poz|reclam)|"
                    r"proast[\u0103a]\s*calit|calitate\s*slab|nu\s*func[t\u021b]ioneaz|nu\s*merge|"
                    r"lips[\u0103a]\s*(din\s*comand|produs)?|incomplet|b[\u0103a]taie\s*de\s*joc|"
                    r"\bjaf\b|ho[t\u021b]ie|mizerie|oroare|scandalos|inacceptabil", re.I)
RO_POS = re.compile(r"recomand\b|recomand cu|super\b|excelent|f(oarte)?\s*bun|perfect|calitate|"
                    r"(?<!rog\s)frumos|frumoas|mul[t\u021b]umit|de\s*top\b|minunat|genial|ador|imi place|îmi place|deosebit|"
                    r"recomand cu incredere|produs bun|rapid si", re.I)
# frustrare + escaladare (intensitate)
FRUST = re.compile(r"al\s*(doilea|treilea|patrulea)\s*(e?mail|mesaj|oar)|nu\s*(imi\s*)?r[\u0103a]spunde\s*nimeni|"
                   r"nici\s*un\s*r[\u0103a]spuns|niciun\s*r[\u0103a]spuns|nu\s*am\s*primit\s*(niciun\s*)?r[\u0103a]spuns|"
                   r"v-?am\s*(tot\s*)?scris|nu\s*mi-?a\s*r[\u0103a]spuns|de\s*(atatea|cate)\s*ori|tot\s*nu", re.I)
ESCAL = re.compile(r"anpc|protec[t\u021b]ia\s*consumator|dau\s*[i\u00ee]n\s*judecat|instan[t\u021b]|avocat|poli[t\u021b]i[ae]|denun[t\u021b]|reclama[t\u021b]ie\s*oficial", re.I)
NOISE_CAT = {"comentariu_social", "spam_automat", "recenzie_feedback", "salut_fara_continut", "formular_contact"}

LANG = {
    "cz": {"complaint": re.compile('\\bnefunguj|\\bnefungoval|\\bnefungovaly?\\b|\\bnedoporucuji\\b|\\bnedoporucuju\\b|\\bpodvod|\\bokrada|\\bnaletel|\\bsmejd|\\bsmejdi\\b|\\bfake\\b|\\bklamav|\\bklamete|\\bklamano|\\bpredrazen|\\bdrahe\\b|\\bdrahy\\s+hadr|\\bstale\\s+(?:jeste\\s+)?drahe|\\bvyhozene?\\s+penize|\\bvychozene\\s+penize|\\bskoda\\s+penez|\\btlucka\\s+na\\s+penize|\\bvyhodit\\s+penize|\\bnic\\s+moc\\b|\\bje\\s+nanic\\b|\\bnanic\\b|\\bk\\s+nicemu\\b|\\bna\\s+nic\\b|\\bnani(?:c|ce)\\b|\\bza\\s+hovno\\b|\\bza\\s+prd\\b|\\bnestoji\\s+za|\\bstoji\\s+za\\s+(?:hovno|prd|nic|hovi)|\\bsmouh|\\bcmouh|\\bslizk(?:ej|y|a)\\s+hadr|\\bslizkej|\\bmazlav|\\bblemcav|\\bhroznej?\\s+hadr|\\bhroznej\\b|\\bnespokojen|\\bzklaman|\\bnespokojenost|\\bkravina\\b|\\bblbost\\b|\\bkecy\\b|\\bhnus\\b|\\btrapn|\\bhruza\\b|\\bzadny\\s+zazrak\\b|\\bzazrak\\s+(?:to\\s+)?neni\\b|\\bmagicka\\s+urcite\\s+neni\\b|\\bnevycisti|\\bnecisti\\b|\\bneutre\\b|\\bneutiraj|\\bnesaje|\\bneodstrani|\\bnepris(?:lo|ly|el|la)\\b|\\bnepris\\w*\\b|\\bobjednavka\\s+(?:nikde|nepris)|\\bzbozi\\s+nikde\\b|\\bobjednano\\s+a\\s+nic\\b|\\bobjednal?a?\\s+(?:jsem\\s+)?(?:si\\s+)?.{0,40}\\b(?:a\\s+nic|nepris|nikde)\\b|\\bcekam\\s+(?:na\\s+ne\\s+)?(?:uz\\s+)?(?:dva\\s+mesice|mesic|dva\\s+tydny|tyden)|\\bdva\\s+tydny\\b.*\\bnikde\\b|\\bnikde\\b.*\\bobjednav|\\bje\\s+to\\s+lez\\b|\\bto\\s+je\\s+lez\\b|\\blez\\b|\\bnic\\s+nepris|\\breklamace\\b|\\bnesrazil|\\bporadne\\s+to\\s+nemuzu\\b|\\bnemuzu\\s+ani\\b|\\bspokojena?\\s+nejsem\\b|\\bnejsem\\s+spokojen|\\bnesem\\s+spokojen|\\bskutek\\s+utek\\b|\\bvubec\\s+tak\\s+nefunguje\\b|\\bneoslovila\\b|\\bneosvedcila\\b', re.I), "praise": re.compile('(?<!ne)\\bdoporucuji\\b|\\bvrele\\s+doporucuji\\b|(?<!ne)\\bdoporucuju\\b|\\bsupr\\b|\\bsuper\\b|\\bvyborn|\\bperfektni\\b|\\bskvel|\\buzasn|\\bparada\\b|\\bparadni|\\bbomba\\b|\\bbezva\\b|\\bspokojen(?!ost\\s+nikde)|\\bvelka\\s+spokojenost\\b|\\bvelika\\s+spokojenost\\b|\\bvelmi\\s+dekuji\\b|\\bmoc\\s+dekuji\\b|\\bdekuju\\s+(?:moc|vam)\\b|\\btop\\b|\\bnejlepsi\\b|\\bjsou\\s+dobre\\b|\\bje\\s+dobra\\b|\\bjsou\\s+fajn\\b|\\bvynikajici\\b|\\bkouzeln|\\bvecicka\\b|\\bdela\\s+divy\\b|\\bzari\\s+cistotou\\b|\\bkupte\\s+si\\b|\\bneudelate\\s+chybu\\b|\\bunesen|\\bnadsen|\\bprekvapilo|\\bmuzu\\s+potvrdit\\b|\\bmohu\\s+potvrdit\\b|\\bnemuzu\\s+si\\s+vynachvalit\\b|\\bvynachvalit\\b', re.I), "praise_kill": re.compile('\\bspokojena?\\s+nejsem\\b|\\bnejsem\\s+spokojen|\\bnesem\\s+spokojen|\\bnesem\\s+s\\s+ni\\s+spokojen|\\bco\\s+je\\s+na\\s+(?:ni|nich|nem)\\s+super\\b|\\bsuper\\s+neni\\b|\\bnic\\s+moc\\b|\\bk\\s+nicemu\\b|\\b(?:jaky|jaka)\\s+(?:je\\s+)?(?:prosim\\s+)?rozmer\\b|\\bsdelit\\s+rozmer\\b|\\bnefunguj.*\\b(?:super|doporucuji)|\\b(?:super|doporucuji).*\\bnefunguj', re.I), "deacc": True},
    "pl": {"complaint": re.compile('(?i)nie\\s+poleca\\w*|\\bodradzam\\b|\\bnie\\s+radze\\b|oszust\\w*|wyludze\\w*|naciag\\w*|nabij\\w*|nabra[c]?\\b|nabran\\w*|nabier\\w*|fals\\w*|falsz\\w*|podrob\\w*|podrub\\w*|\\bsciem\\w*|\\bscim\\w*|\\blip[ay]\\b|\\bszajs\\w*|bzdur\\w*|bzdet\\w*|bujd\\w*|klamstw\\w*|klamcz\\w*|\\bklam\\w*|\\bbubel\\b|\\bbuble\\b|badziew\\w*|\\bszmat\\w*|\\bdruciak|do\\s+(dupy|kitu|bani|niczego|wyrzuceni|kosza|smierci|wyrzucenia)|nadaj\\w*\\s+sie\\s+do\\s+(niczego|wyrzuc|kosza|kitu)|nic\\s+nie\\s+(wart|czysc|robi|usuw|schodzi|pomog|zrobi|scier|wyczysc|zmyw)|nic\\s+(niewart|wart\\w*)|\\bnic\\s+wart|szkoda\\s+(pieni|kasy|forsy|slow|grosza)|strat\\w*\\s+pieni|zmarnowan\\w*|wyrzucon\\w*\\s+kas|wyrzucil\\w*\\s+pieni|stracone\\s+pieni|wydane\\s+pieni|tylko\\s+wydan|wyciagan\\w*\\s+(z\\s+)?(kas|portfel|kiesz)|bije\\s+po\\s+kiesz|po\\s+kieszeni|wyczysci\\s+(ale\\s+)?portfel|gowno\\s+prawd|\\bg\\s+prawda|\\bgowno\\b|gupot\\w*|glupot\\w*|glupi\\w*|dyrdymal\\w*|pic\\s+na\\s+wod|nie\\s+prawd\\w*|nieprawd\\w*|nie\\s+(dzial|sprawdz|usuw|czysc|nadaj|spelni|zmyw|schodz|skuteczn)\\w*|nieskuteczn\\w*|nie\\s+(jestem|byl\\w*)\\s+zadowol|niezadowol\\w*|niezadowo\\b|zawiedz\\w*|zadna\\s+rewelacj|zadnego\\s+rezultat|bez\\s+rewelacj|szal\\w*\\s+(nie\\s*)?ma\\b|szoku\\s+nie\\s+ma|nic\\s+specjaln|nic\\s+rewelacyjn|przereklamow\\w*|przesadzon\\w*|bez\\s+przesady|\\bprzesada\\b|beznadziej\\w*|okropn\\w*|nie\\s+kupuj|nie\\s+dajcie\\s+sie|nie\\s+dajmy\\s+sie|nie\\s+oklam|nie\\s+nabij|tylko\\s+na\\s+(filmiku|filmie|reklamie)|nie\\s+(jest|sa)\\s+tak\\w*\\s+(jak|dobr|skuteczn)|nie\\s+jest\\s+taka\\s+(dobra|jak)|nie\\s+takie\\s+jak|wcale\\s+(tak\\s+)?nie|nie\\s+zauwazyl\\w*\\s+\\w*\\s*efekt|reklama\\s+falszyw|falszyw\\w*\\s+rekl|nie\\s+otrzymal\\w*', re.I), "praise": re.compile('(?i)(?:(?<![a-z])polecam\\b)|godne\\s+poleceni|poleca\\s+sie\\b|polecam\\s+serdeczn|\\bswietn\\w*|\\bswietnie\\b|(?<!nie\\s)\\bsuper\\b|\\bcudown\\w*|\\bcudo\\b|\\bpiekn\\w*|\\bnajlepsz\\w*|\\bzajebist\\w*|jestem\\s+zadowol|bardzo\\s+dobre|\\bsa\\s+dobre\\b|\\bsa\\s+super\\b|\\bjest\\s+super\\b|\\bsa\\s+swietne\\b|dziekuj\\w*|to\\s+rewelacja', re.I)},
    "bg": {"complaint": re.compile('(?i)л[ъь]ж(?:а|и|ете|ци|еш|е|ат)|\\bфалшив|изкуственяк|боклу[кч]|глупост[и]?|нищо\\s+(?:не\\s+)?(?:върш|струва|чист|изтрив|изпир|почист|общо|повече|вярно)|не\\s+(?:върш|струва|чист|почист)|не\\s+(?:с[ъь]м|съм)\\s+доволн|нес[ъь]м\\s+доволн|не\\s+(?:вярвайте|купувайте|се\\s+л[ъь]жете)|не\\s+са\\s+(?:хубави|добри)|не\\s+е\\s+(?:добра|хубава|вярно)|за\\s+(?:изхвърл|аруц)|изхвърлих|de\\s+aruncat|реклама\\s*!|само\\s+(?:си\\s+)?(?:правят\\s+)?реклама|нищо\\s+повече\\s+от\\s+реклама|престанете\\s+да\\s+л[ъь]ж|умеете\\s+да\\s+л[ъь]ж|спрете\\s+да\\s+л[ъь]ж|никаква\\s+работа|не\\s+вършат\\s+(?:това|работа)|не\\s+вършат\\b|кон[цч]и|ламе|лигав|замазват|сапълни\\s+боклу|nu\\s+(?:e\\s+bun|functioneaz|se\\s+conecteaz)|nimic|т[еи]са\\s+фалшив|пълна\\s+(?:л[ъь]жа|глупост)', re.I), "praise": re.compile('(?i)страхотн|срахотно|стахотн|препоръчвам|препор[ъь]чвам|върши\\s+(?:много\\s+)?(?:добра|добре)\\s+работа|върши\\s+работа|добра\\s+работа|работят\\s+(?:чудесно|страхотно|отлично)|чудесн|отличн|\\bсупер\\b|перфектн|прекрасн|почистват\\s+(?:страхотно|чудесно|перфектно|отлично)|срахотно\\s+почистват|благодар|мерси|(?<!не\\s)(?<!не)много\\s+(?:съм\\s+)?доволн|(?<!не\\s)(?<!не)съм\\s+доволн|доволен\\s+съм|доволна\\s+съм|препоръчвам\\s+ги|горещо\\s+препоръчвам', re.I)},
}
STORE_LANG = {"Bonhaus CZ": "cz", "Bonhaus PL": "pl", "Bonhaus BG": "bg"}
_CZ = str.maketrans("\u00e1\u010d\u010f\u00e9\u011b\u00ed\u0148\u00f3\u0159\u0161\u0165\u00fa\u016f\u00fd\u017e", "acdeeinorstuuyz")


def sentiment(text, store):
    t = text or ""
    intensity = 0
    if ESCAL.search(t):
        intensity += 3
    if FRUST.search(t):
        intensity += 2
    lang = STORE_LANG.get(store)
    neg = pos = False
    if lang:
        L = LANG[lang]
        tt = t.lower().translate(_CZ) if L.get("deacc") else t
        neg = bool(L["complaint"].search(tt))
        pos = bool(L["praise"].search(tt)) and not (L.get("praise_kill") and L["praise_kill"].search(tt))
    else:
        neg = bool(RO_NEG.search(t)); pos = bool(RO_POS.search(t))
    if neg:
        intensity += 1
    if intensity > 0:
        return "negativ", intensity
    if pos:
        return "pozitiv", 0
    return "neutru", 0


AGENTS = {"0964e420-84e7-457f-b0b5-57253b9a0dc8": "Alexandra", "245b9936-837a-4c9b-8fad-fe2d179a4ddf": "Martina(CZ)",
          "76459f48-c911-4c69-871e-537e0ac645ac": "Irina", "ecd1325c-8da5-409f-ad90-3405c062ff44": "Diana",
          "20458195-be56-4eb0-a42d-e439ec9bc864": "Cristina", "6acebee5-9015-4e63-9646-ebfe32017be9": "Mariana"}


def load():
    con = rp_db.open(DB)
    cols = [r[1] for r in con.execute("PRAGMA table_info(tickets)")]
    sc = "resolved_store" if "resolved_store" in cols else "store"
    rows = con.execute("SELECT conversation_no,%s,assignee_id,status,category,created_at,"
                       "COALESCE(first_message,'')||' '||COALESCE(subject,'') FROM tickets "
                       "WHERE channel NOT LIKE '%%comment%%'" % sc).fetchall()
    con.close()
    out = []
    for no, store, aid, status, cat, created, text in rows:
        if cat in NOISE_CAT:
            continue
        s, inten = sentiment(text, store or "")
        out.append({"no": no, "store": store or "?", "agent": AGENTS.get(aid, "—"), "status": status,
                    "month": str(created or "")[:7], "sent": s, "inten": inten, "text": " ".join((text or "").split())})
    return out


KB = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")


def _secret(k):
    return os.environ.get(k) or subprocess.run(["uv", "run", KB, "secret-get", k], capture_output=True, text=True).stdout.strip()


def _llm(system, user):
    ak = _secret("ANTHROPIC_API_KEY")
    if ak:
        body = {"model": os.environ.get("SENT_MODEL", "claude-3-5-sonnet-20241022"), "max_tokens": 1500,
                "system": system, "messages": [{"role": "user", "content": user}]}
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(),
                                     headers={"x-api-key": ak, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=90).read())["content"][0]["text"]
    ok = _secret("OPENAI_API_KEY")
    if ok:
        body = {"model": os.environ.get("SENT_MODEL", "gpt-4o-mini"), "temperature": 0,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        req = urllib.request.Request("https://api.openai.com/v1/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Authorization": "Bearer " + ok, "content-type": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=90).read())["choices"][0]["message"]["content"]
    raise SystemExit("Nicio cheie LLM (ANTHROPIC_API_KEY / OPENAI_API_KEY).")


def _llm_score(pool):
    out = []
    sysp = "Ești analist Customer Service. Evaluează sentimentul CLIENTULUI în fiecare mesaj (RO/CZ/PL/BG/EN)."
    for i in range(0, len(pool), 15):
        batch = pool[i:i + 15]
        listing = "\n".join("%d. %s" % (j, x["text"][:320]) for j, x in enumerate(batch))
        usr = ("Pentru fiecare tichet, dă: sentiment (negativ/neutru/pozitiv) + intensitate 0-3 "
               "(0 neutru, 1 ușor nemulțumit, 2 frustrat/insistă, 3 furios/amenință ANPC/juridic) + motiv scurt. "
               "IMPORTANT: prinde și nemulțumirile CALME, factuale (ex. 'ați trimis alt produs', 'lipsește o piesă') ca NEGATIVE. "
               'Răspunde DOAR JSON: [{"i":0,"sent":"negativ","inten":2,"reason":"..."}]\n\nTICHETE:\n' + listing)
        try:
            resp = _llm(sysp, usr)
            arr = json.loads(resp[resp.index("["):resp.rindex("]") + 1])
            m = {d.get("i"): d for d in arr if isinstance(d, dict)}
        except Exception:
            m = {}
        for j, x in enumerate(batch):
            d = m.get(j, {})
            y = dict(x); y["sent"] = d.get("sent", "neutru"); y["inten"] = int(d.get("inten", 0) or 0); y["reason"] = d.get("reason", "")
            out.append(y)
        print("  …%d/%d scorate" % (min(i + 15, len(pool)), len(pool)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["summary", "negative", "trend", "llm"])
    ap.add_argument("--store"); ap.add_argument("--open", action="store_true"); ap.add_argument("--json", action="store_true"); ap.add_argument("--limit", type=int, default=100)
    a = ap.parse_args()
    if not os.path.exists(DB):
        print("Nu găsesc DB-ul:", DB); return
    items = load()
    if a.store:
        items = [x for x in items if x["store"].lower() == a.store.lower()]
    if a.mode == "summary":
        by = collections.defaultdict(lambda: collections.Counter())
        for x in items:
            by[x["store"]][x["sent"]] += 1
        print("═" * 64)
        print("  SENTIMENT TICHETE CS — %d tichete reale" % len(items))
        print("═" * 64)
        print("  %-18s %6s %8s %8s %8s" % ("MAGAZIN", "total", "🔴neg", "·neutru", "🟢poz"))
        for st, c in sorted(by.items(), key=lambda x: -sum(x[1].values())):
            tot = sum(c.values())
            if tot < 10:
                continue
            print("  %-18s %6d %7d%% %7d%% %7d%%" % (st[:18], tot, 100 * c["negativ"] // tot, 100 * c["neutru"] // tot, 100 * c["pozitiv"] // tot))
        tneg = sum(1 for x in items if x["sent"] == "negativ")
        print("─" * 64)
        print("  🔴 %d negative (%d%%) | din care %d escaladări (ANPC/juridic)" % (
            tneg, 100 * tneg // max(len(items), 1), sum(1 for x in items if x["inten"] >= 3)))
        print("  → negative --open pt triaj | trend pt evoluție")
    elif a.mode == "negative":
        sel = [x for x in items if x["sent"] == "negativ" and (not a.open or (x["status"] or "").upper() == "OPEN")]
        sel.sort(key=lambda x: -x["inten"])
        if a.json:
            print(json.dumps(sel[:200], ensure_ascii=False, indent=1, default=str)); return
        print("═" * 80)
        print("  🔴 TICHETE NEGATIVE%s — %d (sortate după intensitate)" % (" DESCHISE" if a.open else "", len(sel)))
        print("═" * 80)
        for x in sel[:60]:
            flag = "🚨" if x["inten"] >= 3 else ("⚠️" if x["inten"] >= 2 else "  ")
            print("  %s #%-7s %-12s %-9s %-6s %s" % (flag, x["no"] or "?", x["store"][:12], x["agent"][:9], (x["status"] or "")[:6], x["text"][:70]))
    elif a.mode == "llm":
        pool = [x for x in items if (not a.open or (x["status"] or "").upper() == "OPEN")]
        pool = pool[:a.limit]
        print("Scorez %d tichete CS reale cu LLM (prinde și reclamațiile calme)…" % len(pool))
        scored = _llm_score(pool)
        dist = collections.Counter(x["sent"] for x in scored)
        negs = sorted([x for x in scored if x["sent"] == "negativ"], key=lambda x: -x["inten"])
        if a.json:
            print(json.dumps(scored, ensure_ascii=False, indent=1, default=str)); return
        print("\n=== SENTIMENT LLM — %d tichete | neg %d · neutru %d · poz %d ===" % (
            len(scored), dist["negativ"], dist["neutru"], dist["pozitiv"]))
        print("🔴 NEGATIVE (sortate după intensitate):")
        for x in negs[:50]:
            flag = "🚨" if x["inten"] >= 3 else ("⚠️" if x["inten"] >= 2 else "  ")
            print("  %s #%-7s %-12s %-8s | %s | %s" % (flag, x["no"] or "?", x["store"][:12], (x["status"] or "")[:6], x.get("reason", "")[:32], x["text"][:50]))
    else:
        by = collections.defaultdict(lambda: collections.Counter())
        for x in items:
            if x["month"]:
                by[x["month"]][x["sent"]] += 1
        print("=== TREND SENTIMENT pe luni (% negativ) ===")
        for m in sorted(by):
            c = by[m]; tot = sum(c.values())
            if tot >= 20:
                print("  %s  neg %3d%%  (%d tichete)" % (m, 100 * c["negativ"] // tot, tot))


if __name__ == "__main__":
    main()
