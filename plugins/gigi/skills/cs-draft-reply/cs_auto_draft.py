# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
cs_auto_draft.py — FLOW de auto-DRAFT + triaj/escaladare + acțiuni propuse, pe coada Richpanel.

Pentru fiecare tichet OPEN care AȘTEAPTĂ RĂSPUNS DE LA NOI (ultimul mesaj e de la client):
  1. IDENTIFICĂ problema (pas LLM de triaj — NU doar regex): problemă concretă, categorie,
     limbă, severitate, dacă trebuie ESCALADAT (+motiv), acțiune sugerată pt CS, și eventuala
     acțiune executabilă (modify/cancel/swap/resend) cu parametri.
  2. Pune TOTUL cap la cap: platformă (channel→stil), identitate + TOATE comenzile
     (customer-identity), UNDE A MAI SCRIS (convos cross-canal), sentiment.
  3. NORMAL → DRAFT în vocea agenților, adaptat platformei (public FB/IG = scurt, fără date
     personale, invită în privat; email = complet+semnătură).
  4. ESCALADARE (ANPC/juridic, refund promis-neefectuat, client foarte supărat/repetat, VIP) →
     NU rezolvă automat: draft scurt de AȘTEPTARE + rutare în Richpanel ca s-o vadă CS ușor —
     prioritate HIGH + tag escaladare/de-sunat + NOTĂ-BRIEF cu tot contextul + acțiune sugerată.
  5. ACȚIUNE (modify/cancel/swap/resend) — model PROPUNE+APROBĂ: verifică PRE-FULFILLMENT,
     reconciliază comanda referită cu comenzile reale, rulează cs-actions DRY-RUN. NU aplică
     până nu aprobi cu --approve. Draftul confirmă acțiunea ca FĂCUTĂ doar după aplicare.

⚠️ Răspunsul către client rămâne DOAR DRAFT (niciodată trimis). Rutarea de escaladare e internă
(prioritate/tag/notă — niciun mesaj la client). Scrierile în Richpanel se fac doar cu --create-draft.

  uv run cs_auto_draft.py                          # DRY-RUN: identifică+draft+escaladări+propuneri, nimic scris
  uv run cs_auto_draft.py --channel email --limit 8
  uv run cs_auto_draft.py --actions modify,cancel  # ce acțiuni sunt active (restul doar draft)
  uv run cs_auto_draft.py --create-draft           # scrie DRAFTURI + rutare escaladare (NU trimite, NU aplică acțiuni)
  uv run cs_auto_draft.py --approve 273790 --agent Oana   # aplică acțiunea propusă la un tichet + draft

LLM: ANTHROPIC_API_KEY (Claude) dacă există în KB, altfel OPENAI_API_KEY. Model: env DRAFT_MODEL.
"""
import os, re, sys, json, subprocess, urllib.request, urllib.parse, urllib.error, argparse, time

HERE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")
CI = os.path.join(HERE, "..", "customer-identity", "customer_identity.py")
CSA = os.path.join(HERE, "..", "cs-actions", "scripts", "cs_actions.py")
QUEUE = os.path.join(HERE, ".auto_draft_proposals.json")
MCP_URL = "https://mcp.richpanel.com/mcp"

PLATFORM = {
    "email": ("Email", "răspuns COMPLET cu salut + semnătură; date de comandă OK (privat)."),
    "chat": ("Chat live pe site", "conversațional, scurt; date de comandă OK (privat)."),
    "messenger": ("Facebook Messenger", "conversațional, prietenos; privat → date OK."),
    "instagram_dm": ("Instagram DM", "conversațional, prietenos; privat → date OK."),
    "sms": ("SMS", "FOARTE scurt, fără semnătură lungă; privat → date OK."),
    "facebook_feed_comment": ("Comentariu public Facebook", "PUBLIC, SCURT (1-2 fraze), cald, cu 1-2 emoji, POLITICOS la PLURAL (dumneavoastra/va, NU la 'tu'); NU expune comanda/AWB/telefon. Pozitiv/lauda -> multumire calda. Intrebare/reclamatie -> raspuns scurt + INVITA CLIENTUL sa ne SCRIE in privat (inbox/Messenger) SAU sa ne SUNE la TELEFON_COMANDA (daca e dat). NU spune 'v-am scris noi in privat' (nu trimitem noi DM)."),
    "instagram_comment": ("Comentariu public Instagram", "PUBLIC, SCURT (1-2 fraze), cald, cu 1-2 emoji, POLITICOS la PLURAL (dumneavoastra/va, NU la 'tu'); NU expune comanda/AWB/telefon. Pozitiv/lauda -> multumire calda. Intrebare/reclamatie -> raspuns scurt + INVITA CLIENTUL sa ne SCRIE in privat (DM) SAU sa ne SUNE la TELEFON_COMANDA (daca e dat). NU spune 'v-am scris noi in privat' (nu trimitem noi DM)."),
}
PAGE_STORE = {
    "426248277236834": "Esteban", "364899953373966": "Ofertele Zilei", "775068272350568": "Magdeal",
    "569610726226886": "Reduceri bune", "676105508924341": "George Talent", "568416516348894": "Belasil",
    "582569158278392": "Nubra", "700342149818211": "Bonhaus PL", "629666993566339": "Grandia",
    "434151126459295": "Bonhaus CZ", "651700798017858": "Casa Ofertelor", "582681401604162": "Ofertele Zilei",
    "1678573069021466": "Orasul Verde", "522811567592063": "Gento", "621560724373069": "Carpetto",
    "680369271815957": "Bonhaus BG", "421367954403103": "Apreciat", "1805415543098993": "Rossi Nails",
}
ORDER_PFX = {"EST": "Esteban", "GT": "George Talent", "NUB": "Nubra", "GEN": "Gento", "GRAN": "Grandia",
             "GRAND": "Grandia", "BELA": "Belasil", "MAG": "Magdeal", "OFER": "Ofertele Zilei", "RED": "Reduceri bune",
             "BON": "Bonhaus RO", "BONBG": "Bonhaus BG", "CZ": "Bonhaus CZ", "PL": "Bonhaus PL", "CARP": "Carpetto",
             "COV": "Covoria", "APR": "Apreciat", "ROSSI": "Rossi Nails"}
ORDER_RE = re.compile(r"\b(EST|GT|NUB|GRAND|GRAN|MAG|OFER|RED|BONBG|BON|CZ|PL|BELA|GEN|CARP|COV|APR|ROSSI)[ -]?(\d{4,7})\b", re.I)
AWB_RE = re.compile(r"\b\d{10,16}\b")   # număr de AWB menționat în mesaj (curier) — pt căutare în profit_orders
# brand -> limba pieței (semnal SIGUR de limbă, mai fiabil decât detecția LLM pe comentarii scurte)
STORE_LANG = {"Bonhaus CZ": "cz", "Bonhaus PL": "pl", "Bonhaus BG": "bg"}
# brand (store_name) -> telefon comenzi (luat de pe site-urile publice; fetch-brand-phones)
STORE_PHONE = {
    "Esteban": "0732 781 468", "George Talent": "0732 781 468",
    "Nubra": "0729 748 961", "Grandia": "0729 748 961",
    "Belasil": "0729 748 943", "Gento": "0729 748 943",
    "Carpetto": "0729 748 943", "Covoria": "0729 748 943", "Rossi Nails": "0729 748 943",
    "Apreciat": "0729 748 943", "Casa Ofertelor": "0729 748 943",
    "Bonhaus BG": "0885493926", "Bonhaus CZ": "+420 724 216 967", "Bonhaus PL": "0376300646",
    "Nocturna BG": "0876813240",
}
# brand din domeniul adresei magazinului (pt email, când nu avem 360/orders) — ex. contact@esteban.ro → Esteban
EMAIL_BRAND = {
    "esteban.ro": "Esteban", "george-talent.ro": "George Talent", "nubra.ro": "Nubra",
    "grandia.ro": "Grandia", "labnoir.ro": "Lab Noir", "belasil.ro": "Belasil",
    "gento.ro": "Gento", "carpetto.ro": "Carpetto", "covoria.ro": "Covoria",
    "rossinails.ro": "Rossi Nails", "apreciat.ro": "Apreciat", "casaofertelor.ro": "Casa Ofertelor",
    "magdeal.ro": "Magdeal", "ofertele-zilei.ro": "Ofertele Zilei", "reduceribune.ro": "Reduceri bune",
    "orasulverde.ro": "Orasul Verde", "nocturna.ro": "Nocturna",
}
def brand_from_email(addr):
    m = re.search(r"@([\w.-]+)", (addr or "").lower())
    if not m:
        return None
    dom = m.group(1)
    if dom in EMAIL_BRAND:
        return EMAIL_BRAND[dom]
    if any(b in dom for b in ("shopify", "gmail", "yahoo", "ymail", "icloud", "hotmail", "outlook", "anaf", "judgeme", "facebook", ".tech")):
        return None
    root = dom.split(".")[0].replace("-", " ").strip()
    return root.title() if root else None
STORE_NORM = {"GRAND": "GRAN"}
CH_LABEL = {"facebook_feed_comment": "FB comentariu", "facebook_message": "FB mesaj", "messenger": "Messenger",
            "instagram_comment": "IG comentariu", "instagram_message": "IG mesaj", "instagram_dm": "IG DM",
            "email": "Email", "email_from_widget": "Email widget", "chat": "Chat", "sms": "SMS"}

DEACC = str.maketrans("ăâîșşțţ", "aaissttt"[0:7])
def deacc(s): return (s or "").lower().translate(DEACC)
def _f(v, d=0.0):
    """float defensiv — LLM-ul poate întoarce confidence ca string non-numeric; nu lăsăm să crape tot lotul."""
    try:
        return float(v)
    except Exception:
        return d

_CYR = re.compile(r"[Ѐ-ӿ]")
def detect_lang(text):
    """Detectează limba în care a scris CLIENTUL, după script/diacritice specifice. None dacă e ambiguu (ASCII)."""
    t = (text or "")
    if _CYR.search(t):
        return "bg"                       # chirilic → bulgară
    low = t.lower()
    if any(c in low for c in "łąężśćźń"):
        return "pl"                       # litere specific poloneze
    if any(c in low for c in "řůě"):
        return "cz"                       # litere specific cehe
    if any(c in low for c in "ăâîșțşţ"):
        return "ro"                       # diacritice românești
    return None                           # ASCII / ambiguu → lasă fallback (brand/LLM)
# regex DOAR ca hint/fallback — identificarea reală o face LLM-ul (identify)
RULES = [
    ("spam_automat", r"left a \d star review|left the following|judge\.?me|chargeflow|do[- ]?not[- ]?reply|newsletter|unsubscribe"),
    ("recenzie_feedback", r"recomand|ce parere|review|feedback|multumesc pentru|miroase (foarte )?bine|am lesinat|super produs"),
    ("anulare", r"anulez|anulare|anulati|renunt la comanda|nu mai vreau comanda|cancel|storno"),
    ("modificare_comanda", r"adresa gresita|alta adresa|schimb (nr|numarul|adresa|telefonul)|modific(a|are)? (comanda|adresa|telefon)|wrong address|change.*address|modific datele"),
    ("retur", r"\bretur|returnez|returna|banii inapoi|refund|vreau banii|\breturn\b|sa le returnez|nu mi plac"),
    ("schimb_swap", r"schimb produs|alt model|alta marime|alta culoare|inlocui|exchange|sa il schimb"),
    ("problema_produs", r"defect|stricat|nu functioneaza|nu aspira|lipseste|deteriorat|spart|teapa|nu corespunde|am primit (alt|gresit)|altceva|damaged|broken|missing"),
    ("livrare_wismo", r"unde (e|este|imi)|cand ajunge|coletul|nu a ajuns|nu am primit|awb|curier|tracking|intarzi|where is my order|track.*order|kde je|gdzie"),
    ("refuz_livrare", r"refuz|nu primesc coletul|nu accept coletul"),
    ("plata_factura", r"factura|am platit de doua|card.*(debitat|taxat)|chitanta|invoice"),
    ("presale_intrebare", r"aveti (pe |in )?stoc|cat costa|ce pret|livrati in|cand revine|dimensiuni|mai aveti|disponibil|how much|in stock"),
    ("comanda_noua", r"vreau sa comand|cum comand|doresc sa cumpar|i want to order"),
]
ESCAL = re.compile(r"anpc|protectia consumator|dau in judecat|instanta|avocat|denunt|reclamatie|chargeback|politi[ae]", re.I)
ACTION_CATS = {"modificare_comanda", "anulare", "schimb_swap", "problema_produs", "refuz_livrare"}
# post-filtru ANTI-HALUCINARE: tipare de FABRICARE (lookup/status/preț/dimensiune inventate) când NU avem datele în context
HALLU = re.compile(
    r"am verificat|am c[ăa]utat|nu am g[ăa]sit|n-?am g[ăa]sit|nu (am )?identificat|am identificat comanda|"
    r"nu exist[ăa] (nicio|o) comand|comanda (dumneavoastr[ăa]|nr|#)?\s*[A-Z]{2,5}\d+ (este|a fost|nu)|"
    r"este în procesare|a fost predat|nu a fost predat[ăa]|urmeaz[ăa] s[ăa] fie preluat|"
    r"livrare[a]? (se face |va fi |în )?\b\d+\s?(-\s?\d+\s?)?zile|în \d+ zile lucr|"
    r"\b\d{1,3}\s?x\s?\d{1,3}\b|\b\d{2,3}\s?cm\b|"
    r"\b\d{2,4}\s*(de\s+)?(lei|ron)\b",   # orice preț concret (N lei) — în lean nu avem prețuri → inventat
    re.I)
def has_order_data(od_ctx):
    """True dacă în context CHIAR avem comenzi (nu lean / nu redactat)."""
    s = (od_ctx or "")
    return bool(s) and "nicio comandă" not in s and "ascunse" not in s and "lean" not in s.lower()
def categorize_hint(blob, channel=None):
    t = deacc(blob)
    for cat, pat in RULES:
        if re.search(pat, t):
            return cat
    if channel in ("facebook_feed_comment", "instagram_comment"):
        return "comentariu_social"
    return "altele"

NEG = ["teapa", "prost", "groaznic", "dezamagit", "nu functioneaza", "nu aspira", "stricat", "defect", "rusine",
       "inadmisibil", "scandal", "anpc", "oribil", "jignit", "nervos", "furios", "niciodata", "reclamatie", "escroc",
       "bataie de joc", "hoti", "inselat", "nu am primit", "intarzi", "nemultumit", "awful", "broken", "scam", "refuz",
       "nu mi plac", "nu corespunde"]
POS = ["recomand", "multumesc", "super", "excelent", "minunat", "perfect", "iubesc", "frumos", "calitate", "rapid",
       "am lesinat", "bravo", "felicitari", "ador", "miroase foarte bine", "multumita"]
def sentiment(text):
    t = deacc(text)
    n = sum(t.count(w) for w in NEG); p = sum(t.count(w) for w in POS)
    excl = t.count("!"); caps = sum(1 for c in text if c.isupper())
    lab = "negativ" if n > p else ("pozitiv" if p > n else "neutru")
    score = n + p + excl // 2 + (1 if caps > 15 else 0)
    inten = "puternic" if (score >= 3 or excl >= 2 or caps > 20) else ("mediu" if score >= 1 else "slab")
    return lab, inten

_SECRET_CACHE = {}
def secret(k):
    # env mai întâi (cron/VPS exportă cheile din .env); altfel KB via uv — dar NU crăpa dacă uv lipsește (cron PATH minimal)
    v = os.environ.get(k)
    if v:
        return v
    if k in _SECRET_CACHE:
        return _SECRET_CACHE[k]
    try:
        v = subprocess.run(["uv", "run", KB, "secret-get", k], capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        v = ""   # uv negăsit / KB inaccesibil → gol, NU excepție (altfel llm() iese „(eroare LLM ...'uv')")
    _SECRET_CACHE[k] = v
    return v

class MCP:
    def __init__(self, token):
        self.t = token
        self._post({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                    "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "autodraft", "version": "1"}}})
    def _post(self, p):
        h = {"Authorization": "Bearer " + self.t, "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        # rezilient la rate-limit Richpanel: pe 429 (sau 5xx) așteaptă (Retry-After / backoff) și reîncearcă
        for attempt in range(6):
            try:
                req = urllib.request.Request(MCP_URL, data=json.dumps(p).encode(), headers=h)
                with urllib.request.urlopen(req, timeout=60) as r:
                    body = r.read().decode()
                ln = [l for l in body.splitlines() if l.startswith("data:")]
                return json.loads(ln[-1][5:]) if ln else json.loads(body)
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < 5:
                    ra = e.headers.get("Retry-After") if hasattr(e, "headers") else None
                    wait = float(ra) if (ra and str(ra).isdigit()) else min(60, 2 ** attempt * 2)
                    time.sleep(wait)
                    continue
                raise
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:   # SSL handshake / read timeout / conn reset → tranzitoriu
                if attempt < 5:
                    time.sleep(min(30, 2 ** attempt * 2))
                    continue
                raise
    def call(self, name, args):
        try:
            r = self._post({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
            txt = r["result"]["content"][0]["text"]
            try: return json.loads(txt)
            except Exception: return {"_text": txt}
        except Exception as e:
            return {"_error": str(e)}

def _llm_http(url, body, headers):
    # retry+backoff pe rate-limit/5xx (esențial la rulări în paralel — altfel iese „(eroare LLM 429)")
    for attempt in range(6):
        try:
            req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
            return json.loads(urllib.request.urlopen(req, timeout=90).read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 5:
                ra = e.headers.get("Retry-After") if hasattr(e, "headers") else None
                wait = float(ra) if (ra and str(ra).replace(".", "", 1).isdigit()) else min(90, 2 ** attempt * 3)
                time.sleep(wait)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:   # SSL handshake / read timeout / conn reset → tranzitoriu, reîncearcă
            if attempt < 5:
                time.sleep(min(30, 2 ** attempt * 2))
                continue
            raise

def llm(system, user, js=False):
    ak = secret("ANTHROPIC_API_KEY")
    if ak:
        body = {"model": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"), "max_tokens": 900,
                "system": system, "messages": [{"role": "user", "content": user}]}
        r = _llm_http("https://api.anthropic.com/v1/messages", body,
                      {"x-api-key": ak, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        return r["content"][0]["text"], "claude"
    ok = secret("OPENAI_API_KEY")
    if ok:
        body = {"model": os.environ.get("DRAFT_MODEL", "gpt-4o"), "temperature": 0.2,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if js: body["response_format"] = {"type": "json_object"}
        r = _llm_http("https://api.openai.com/v1/chat/completions", body,
                      {"Authorization": "Bearer " + ok, "content-type": "application/json"})
        return r["choices"][0]["message"]["content"], "openai/gpt"
    raise SystemExit("Nicio cheie LLM în KB (ANTHROPIC_API_KEY / OPENAI_API_KEY).")

# ---- pasul de IDENTIFICARE (triaj) ----
IDENTIFY_SYS = """Ești triajul Customer Service ARONA (magazine COD: parfumuri Esteban/GT/Nubra/Gento/Lab Noir; casă Grandia/Carpetto/Covoria; Bonhaus RO/CZ/PL/BG; Belasil; Magdeal/Ofertele Zilei/Reduceri bune/Apreciat/Rossi Nails).
IMPORTANT — răspundem la ULTIMUL mesaj al clientului (marcat cu „>>> ULTIMUL MESAJ AL CLIENTULUI" în conversație). Firul de dinainte = DOAR context. Dacă ultimul mesaj e mulțumire / feedback pozitiv / „a ajuns" / „sunt foarte bune", atunci `category`=recenzie_feedback și NU mai e WISMO/problemă — chiar dacă firul a ÎNCEPUT cu o întrebare de livrare. Nu trata o întrebare deja rezolvată ca fiind încă deschisă.
SARCASM/IRONIE: un comentariu aparent neutru/pozitiv dar critic (ex. persistență mică „au persistat 4 ore 😅" la un parfum reclamat 12h, „super... 🙄", emoji 😅😂🙄 + reproș) NU e `recenzie_feedback` — e NEMULȚUMIRE (`problema_produs` sau `comentariu_social`), sentiment NEGATIV.
Citește mesajul clientului + comenzile + istoricul și IDENTIFICĂ exact problema. Întoarce STRICT JSON:
{"problem":"<1 frază concretă: ce vrea / ce s-a întâmplat>",
 "category":"livrare_wismo|retur|schimb_swap|anulare|modificare_comanda|problema_produs|refuz_livrare|plata_factura|presale_intrebare|comanda_noua|recenzie_feedback|comentariu_social|spam_automat|altele",
 "language":"ro|cz|pl|bg|en",
 "severity":"none|HIGH|URGENT",
 "escalate":true|false,
 "escalation_reason":"<de ce, dacă escalate; altfel ''>",
 "suggested_action":"<ce să facă agentul, concret — pt nota internă CS>",
 "action":"none|modify|cancel|swap|resend",
 "order":"<nr comandă referit explicit sau ''>",
 "new_address":"","new_city":"","new_zip":"","new_phone":"","items":"<sku/titlu:cant sau ''>",
 "product":"<produsul concret la care se referă clientul, dacă reiese, altfel ''>",
 "comment_action":"hide|public|none",
 "spam":true|false,
 "confidence":0.0-1.0,"missing":["ce date lipsesc"]}
SPAM (pe ORICE canal — email, DM FB/IG, comentariu): true dacă mesajul NU necesită răspuns CS — notificări automate (Meta/Facebook business, judge.me „left a review”, newsletter, reset parolă, „do not reply”, out-of-office, confirmări automate), boți, mesaje promoționale nesolicitate / spam evident. Aceste tichete se EXCLUD (nu primesc draft).
ESCALADARE: URGENT = ANPC/juridic/amenințare (avocat, instanță, dau în judecată, denunț), chargeback, refund PROMIS dar neefectuat, client foarte agresiv. HIGH = reclamație serioasă (produs/livrare) cu client clar SUPĂRAT, SAU client care a scris REPETAT despre ACEEAȘI problemă nerezolvată și e frustrat. Altfel none. ATENȚIE: o simplă întrebare de status (WISMO) politicoasă NU se escaladează — chiar dacă clientul are nr. comandă, multe comenzi sau istoric de tichete (volumul/„a mai scris de N ori" în istoric NU e, singur, motiv de escaladare). Se rezolvă direct. COMENTARII PUBLICE (FB/IG): nemulțumire de produs / „nu funcționează" / „păcălit" / „țeapă" / „mic"/„prost" FĂRĂ ANPC/juridic/amenințare → NU escalada; primește răspuns public scurt + invitație în privat. Escaladează un comentariu public DOAR la semnale URGENT (ANPC/juridic/amenințare/refund promis).
COMMENT_ACTION (doar comentarii PUBLICE FB/IG; pe celelalte canale = "none"). NU trimitem mesaje private (DM) — răspundem PUBLIC, scurt; dacă e nevoie de rezolvare, INVITĂM clientul să ne scrie în privat sau să sune:
  • "hide" = DOAR spam/troll/abuz/vulgaritate/ofense/reclamă străină (de ascuns de pe postare).
  • "public" = orice comentariu care merită un răspuns public scurt — laudă/recenzie (mulțumire caldă), întrebare presale (preț/stoc/disponibilitate/„mai aveți?"), reclamație ușoară. La întrebare/reclamație → invită clientul să ne scrie în privat (inbox) sau să ne SUNE la numărul magazinului.
  • "none" = comentariu pur zgomot (tag de prieten fără conținut, off-topic) care nu necesită niciun răspuns → se lasă cum e.
RĂSPUNSURILE ȘI PROCEDURILE DEPIND DE BRAND ȘI DE PRODUS: ține cont de magazin (parfumuri Esteban/GT/Nubra/Gento vs casă/mobilă Grandia/Carpetto/Covoria vs cosmetice/deals) și de produsul concret (extrage-l în „product").
ACȚIUNE: action!="none" DOAR dacă e cerere clară de modificare adresă/telefon (modify), anulare (cancel), schimb produs (swap) sau retrimitere produs spart/lipsă (resend). Dacă nu e clar ce comandă sau lipsesc date → action="none" + missing. NU inventa nimic. Răspunde DOAR JSON."""

# ---- generarea DRAFTULUI ----
SYSTEM = """Ești agent Customer Service ARONA. Scrii ca un agent REAL (Cristina/Diana/Irina/Martina/Alexandra) — cald, politicos, natural, cu diacritice, fără limbaj robotic.
⛔ ANTI-HALUCINARE — REGULA #1, MAI PRESUS DE ORICE: NU ai făcut niciun lookup live. Folosește DOAR informația care apare EXPLICIT în context (secțiunea COMENZILE CLIENTULUI / AWB / PRODUS / TELEFON_COMANDĂ). Dacă o informație NU e în context, NU o INVENTA și NU pretinde că o știi/ai verificat-o. Concret, INTERZIS:
  • să spui „am verificat / am căutat / am găsit / NU am găsit comanda / nu există nicio comandă" — NU cauți tu, nu ai cum să știi;
  • să afirmi un STATUS de comandă/livrare („e în procesare / a fost predată / nu a fost predată / urmează preluarea"), un AWB, o dată sau un termen de livrare în zile — dacă nu e în context;
  • să INVENTEZI specificații de produs: dimensiuni (cm), preț (lei), culoare, material, disponibilitate/stoc — dacă nu sunt în context;
  • să INVENTEZI un număr de telefon — folosește DOAR `TELEFON_COMANDĂ` dacă apare în context; altfel NU da niciun număr;
  • să confirmi capabilități nesigure (ex. livrare internațională) fără bază.
CE FACI ÎN SCHIMB când NU ai datele: cere-i POLITICOS clientului ce-ți lipsește — **numărul comenzii SAU un număr de telefon** (pt orice ține de o comandă: status/anulare/retur/modificare) — sau spune ONEST „verificăm și revenim cât mai curând", FĂRĂ să inventezi. La întrebări de produs (preț/dimensiuni/stoc) fără date: îndrumă spre site sau spune că revii cu detaliile exacte — NU inventa cifre.
RĂSPUNZI LA ULTIMUL MESAJ AL CLIENTULUI (marcat „>>> ULTIMUL MESAJ AL CLIENTULUI" în conversație); restul firului = context. Dacă ultimul mesaj e mulțumire / „a ajuns" / feedback pozitiv (ex. „Foarte bune, mulțumesc!") → răspunde CALD la el (te bucuri că i-au plăcut, mulțumești), NU relua întrebarea veche, NU cere AWB/nr comandă/telefon și NU trata ca WISMO.
REGISTRU (important): FORMAL, la PLURAL, pe TOATE canalele (email, DM, chat, comentariu) — în română „dumneavoastră/vă/-ți" (NICIODATĂ „tu/ție/te/-i"); în alte limbi registrul politicos echivalent. Așa scriu agenții ARONA reali („Vă rugăm", „Vă informăm", „Vă mulțumim").
PROCEDURI:
- LIVRARE/WISMO: răspunde DIRECT, dar NU INVENTA. DOAR dacă în context ai AWB+curier confirmat → dă statusul real + linkul corect DUPĂ curier (DPD https://tracking.dpd.ro?shipmentNumber=<AWB>; Sameday https://www.sameday.ro/#awb=<AWB>; Packeta https://tracker.packeta.com/ro/?id=<AWB>; Econt https://www.econt.com/en/services/track-shipment/<AWB>) + scuze dacă e întârziat. Dacă NU GĂSEȘTI comanda în context (nicio comandă / fără AWB) → NU pretinde că ai găsit-o sau că o cauți tu acum, NU afirma statusul (NU spune „e în procesare / urmează să fie preluată / verific eu") și NU promite termen/tracking. În schimb, cere-i clientului POLITICOS **numărul comenzii SAU un număr de telefon** ca să putem identifica și verifica comanda (ex: „Ca să verific exact comanda dumneavoastră, îmi puteți spune numărul comenzii sau un număr de telefon asociat? Revin imediat cu statusul."). Nu cere date pe care le ai deja în context.
- RETUR: ARONA e COD și NU încurajează returul → întreabă motivul + oferă alternativă; insistă și e eligibil → formular https://bi.grandia.ro/returns?order=<nr>&email=<email> + „Suma vă va fi returnată în maximum 14 zile de la ajungerea coletului." Parfum/igienă DESIGILAT → refuz politicos.
- PRODUS SPART (parfum): NU refund → RETRIMITERE GRATUITĂ + parfum CADOU. DEFECT/LIPSĂ (casă): cere poză; pe stoc → retrimitere/schimb; altfel retur+refund.
- PRE-VÂNZARE / INTENȚIE DE CUMPĂRARE („vreau și eu", „dacă sunt bune", „cum comand", „îl iau"): răspuns CALD și entuziast care CONFIRMĂ și ÎNCURAJEAZĂ comanda — spune CUM comandă (direct de pe site SAU sunând la `TELEFON_COMANDĂ` dacă apare în context); NU deflecta seac cu „dacă aveți întrebări scrieți-ne". RECENZIE/COMPLIMENT: mulțumește scurt și cald.
- DESCRIE PRODUSUL POTRIVIT CATEGORIEI (NU generic): parfumuri (Esteban/GT/Nubra/Gento) → miros/arome inspirate din branduri cunoscute/persistență/preț accesibil — NU „aspect plăcut" (e parfum, nu obiect); genți/încălțări → piele ecologică/aspect frumos; casă/covoare (Grandia/Carpetto/Covoria) → calitate/utilitate. Evită lauda generică „produse de calitate bună și aspect plăcut" care nu se potrivește categoriei.
- COMANDĂ / „vreau să comand": recomandă clientului să SUNE pentru a plasa comanda, la numărul magazinului — dacă apare în context ca `TELEFON_COMANDĂ`, dă-l explicit („ne puteți suna la <număr> pentru comandă"); altfel îndrumă-l să comande de pe site / să lase un număr ca să-l sunăm.
PRODUSE — ONESTITATE: multe produse ARONA sunt REPLICI/imitații, NU originale. Parfumurile sunt INSPIRATE din branduri cunoscute (la o fracțiune din preț), nu sunt parfumurile originale. Genți/accesorii „din piele" sunt de regulă PIELE ECOLOGICĂ / imitație, nu piele naturală. La întrebări de tip „e original?", „e piele adevărată?" → răspunde ONEST și pozitiv: spune sincer că e imitație/piele ecologică/parfum inspirat — NU pretinde că e original sau piele naturală, dar valorifică (calitate bună, aspect frumos, preț accesibil).
COMENTARII PUBLICE (FB/IG) — CALD, NU robotic, SCURT (1-2 fraze), cu 1-2 emoji potrivite (😊❤️🙏🔥🌸): răspunde la SPIRITUL comentariului. REGULĂ CS FERMĂ pt TOATE răspunsurile la comentarii: NU începe cu „Bună ziua!" / „Bună!" / „Salut" / niciun salut de deschidere — intră DIRECT în mesaj (ex. „Ne pare rău că...", „Mă bucur că...", „Da, sunt foarte apreciate..."). Salut + semnătură DOAR pe email, niciodată pe comentarii. SARCASM/IRONIE: dacă un comentariu pare neutru/pozitiv dar e de fapt un REPROȘ (ex. „au persistat 4 ore 😅" la un parfum dat ca 12h, „merge perfect... 🙄", emoji 😅😂🙄 + critică) → NU răspunde ca la o laudă („Ne bucurăm..."); recunoaște cu TACT nemulțumirea (la parfumuri: persistența variază după tipul pielii, cantitate, zona de aplicare, familia olfactivă), FĂRĂ justificări defensive, și oferă ajutor / invită în privat. Laudă / „subscriu" / tag de prieten / entuziasm → mulțumire caldă + entuziasm, FĂRĂ să împingi inutil „scrieți-ne în privat". Întrebare reală / nemulțumire → răspuns scurt la obiect, apoi INVITĂ CLIENTUL să ne scrie în privat (inbox/Messenger/DM) SAU să ne SUNE la `TELEFON_COMANDĂ` (dacă apare în context, dă numărul explicit). NU spune „v-am scris în privat" / „ți-am trimis detalii" — NOI nu trimitem DM; clientul ne contactează. La o întrebare SIMPLĂ (preț, dimensiune, disponibilitate) NU împinge automat „în privat": dacă ai informația, dă-o pe loc în comentariu; dacă NU o ai (nu știi produsul exact), întreabă SCURT chiar în comentariu la ce produs se referă, sau invită-l să sune/comande — „scrieți-ne în privat" doar când chiar e nevoie de date personale. Dacă în context apare „POSTAREA/RECLAMA la care comentează", folosește-o ca să identifici PRODUSUL și răspunde la obiect (NU mai întreba „ce produs", clientul comentează exact la acel produs). Dacă reclamă un canal care nu merge (ex. „sun de zile și nu răspunde nimeni"), recunoaște problema și asigură-l că revenim noi, nu-l trimite înapoi la același canal. Evită formula seacă „Vă mulțumim pentru comentariu! Dacă aveți nevoie… scrieți-ne în privat".
REGULA DE ACȚIUNE: dacă în context apare `ACTIUNE_APLICATA: …` → confirmă acțiunea ca FĂCUTĂ. Dacă NU → nu spune niciodată că ai modificat/anulat ceva; confirmă că ai PRELUAT solicitarea sau cere datele lipsă. NU inventa.
CALIBRARE SENTIMENT: negativ → scuze sincere + asumare + soluție; pozitiv → cald; neutru → la obiect.
SALUT PE NUME: pe canale PRIVATE (email/DM/chat) folosește doar PRENUMELE dacă e curat; dacă numele pare concatenat/neformatat (prenume+nume lipite, fără spațiu, majusculă în interior — ex. „GheorghesiGerda") sau incert → adresare neutră. Pe COMENTARII PUBLICE (FB/IG) NU folosi numele clientului (nici prenume, nici nume de familie — ex. „doamnă Nechita") — e expunere de date personale într-un spațiu public; adresează-te neutru („Bună ziua").
CANAL RECLAMAT: dacă clientul spune explicit că un canal NU funcționează (ex. „sun de zile și nu răspunde nimeni") → NU-l trimite înapoi la acel canal; recunoaște problema și oferă o ALTERNATIVĂ (scrieți-ne în privat cu nr. comenzii, revenim noi).
REGULI: limba clientului; DOAR datele din context (fără AWB/prețuri/nr inventate); respectă STILUL platformei; pe canale PUBLICE (comentarii FB/IG) scrie POLITICOS, la PLURAL (dumneavoastră/vă, NICIODATĂ „tu/ție/te") și nu scrie date personale (invită în privat); gramatică corectă („ți-am scris/v-am scris", nu „te-am scris"); DOAR textul răspunsului. Email → salut + semnătură „Cu drag, Echipa <Magazin>"; dacă magazinul e necunoscut/generic, semnează „Cu drag, echipa noastră" (NU „Echipa magazinul nostru"). Comentariu public → 1-3 fraze."""

HOLDING = """Ești agent CS ARONA. Cazul e ESCALADAT spre un coleg. Scrie DOAR un mesaj SCURT de AȘTEPTARE în limba clientului: confirmă că ai preluat sesizarea și că un coleg revine cât mai curând (azi/în cel mai scurt timp). Ton cald, empatic dacă e supărat. REGISTRU FORMAL, la PLURAL — în română „dumneavoastră/vă" (NU „tu/ție/te"); alte limbi: registrul politicos echivalent. NU promite soluții concrete, NU da detalii de comandă pe canal public. Email → salut + „Cu drag, Echipa <Magazin>"; dacă magazinul e necunoscut/generic, semnează „Cu drag, echipa noastră" (NU „Echipa magazinul nostru"). Comentariu public → 1-2 fraze + invitație în privat, FĂRĂ salut de deschidere („Bună ziua"/„Bună"/„Salut") — intră direct (regulă CS pt comentarii)."""


def store_prefix(order_name, fallback_brand=None):
    m = ORDER_RE.search(order_name or "")
    if m:
        pfx = m.group(1).upper(); return STORE_NORM.get(pfx, pfx)
    if fallback_brand:
        for p, b in ORDER_PFX.items():
            if b.lower() == fallback_brand.lower():
                return STORE_NORM.get(p, p)
    return None


def resolve_target_order(text, orders):
    """Reconciliază comanda REFERITĂ de client cu comenzile lui REALE. (obj|None, name, ambiguous, motiv)."""
    if not orders:
        return None, "", False, "fără comenzi în cont"
    by_name = {}
    for o in orders:
        nm = (o.get("o") or "").replace(" ", "").replace("-", "").upper()
        if nm: by_name[nm] = o
    refs, matched, seen = [], [], set()
    for m in ORDER_RE.finditer(text or ""):
        refs.append(m.group(0).replace(" ", "").replace("-", "").upper())
    for r in refs:
        if r in by_name and r not in seen:
            seen.add(r); matched.append(by_name[r])
    if len(matched) == 1:
        return matched[0], matched[0].get("o") or "", False, "comandă referită explicit"
    if len(matched) > 1:
        return None, "", True, "clientul a referit mai multe comenzi"
    if refs:
        # clientul a referit o comandă pe care NU o putem confirma → niciodată substitui tăcut altă comandă
        return None, "", True, ("o singură comandă dar referința nu se potrivește" if len(orders) == 1
                                else "referință necunoscută + mai multe comenzi")
    if len(orders) == 1:
        return orders[0], orders[0].get("o") or "", False, "o singură comandă în cont"
    return None, "", True, "mai multe comenzi, niciuna referită clar"


PRE_STATES = {"netrimisa", "comanda noua", "noua", "plasata", "in asteptare", "draft",
              "unfulfilled", "open", "neexpediat", "de expediat", "nefinalizata"}
def fulfillment_state(order):
    """FAIL-SAFE: 'pre' DOAR dacă e clar neexpediat (fără AWB + status explicit pre).
    Necunoscut/gol/'?'/in-curs/lipsă-awb → 'post' (blochează modify/cancel — nu ghicim pe colete posibil plecate)."""
    awb = (order or {}).get("awb") or ""
    deliv = deacc((order or {}).get("deliv") or "").strip()
    if not awb and deliv in PRE_STATES:
        return "pre"
    return "post"


def customer_ident(conv_no):
    try:
        out = subprocess.run(["uv", "run", CI, "--conv", str(conv_no), "--json"], capture_output=True, text=True, timeout=90).stdout
        return json.loads(out[out.index("{"):]) if "{" in out else {}
    except Exception:
        return {}


def norm_phone(p):
    d = "".join(ch for ch in str(p or "") if ch.isdigit())
    return d[-9:] if len(d) >= 9 else ""

PROFIT_DB = os.environ.get("PROFIT_DB", "/root/Scripturi/data/profitability.db")
def lookup_orders(email, phone, order_names=(), awbs=()):
    """GROUNDING self-contained (pt VPS, fără SSH/uv): comenzile clientului din DB metrics (pg8000, după email/telefon/nr-comandă)
    + status livrare/AWB/curier din profitability.db (sqlite local), inclusiv căutare DUPĂ AWB. [] dacă nu merge (fail-safe)."""
    try:
        import pg8000.dbapi, sqlite3
    except Exception:
        return []
    byname = {}
    # 1) metrics (comenzi Shopify) după email/telefon/nr-comandă
    url = secret("DATABASE_URL_METRICS")
    if url and (email or phone or order_names):
        try:
            u = urllib.parse.urlparse(url)
            conn = pg8000.dbapi.connect(ssl_context=True, user=urllib.parse.unquote(u.username or ""),
                                        password=urllib.parse.unquote(u.password or ""), host=u.hostname,
                                        port=u.port or 5432, database=(u.path or "/").lstrip("/"))
            cur = conn.cursor()
            cols = 'name,"totalPrice","financialStatus","shopifyCreatedAt"'
            rows = []
            if email:
                cur.execute('SELECT %s FROM orders WHERE lower(email)=lower(%%s) ORDER BY "shopifyCreatedAt" DESC LIMIT 12' % cols, (email,))
                rows += cur.fetchall()
            ph = norm_phone(phone)
            if ph:
                cur.execute('SELECT %s FROM orders WHERE phone LIKE %%s OR "shippingPhone" LIKE %%s ORDER BY "shopifyCreatedAt" DESC LIMIT 12' % cols, ("%" + ph, "%" + ph))
                rows += cur.fetchall()
            ons = [o for o in dict.fromkeys(order_names) if o]
            if ons:
                cur.execute('SELECT %s FROM orders WHERE name IN (%s)' % (cols, ",".join(["%s"] * len(ons))), ons)
                rows += cur.fetchall()
            conn.close()
            for r in rows:
                byname[r[0]] = {"o": r[0], "total": float(r[1] or 0), "fin": r[2], "date": str(r[3])[:10],
                                "brand": store_prefix(r[0]) or "?", "deliv": "?", "awb": "", "courier": "", "skus": ""}
        except Exception:
            pass
    # 2) profit_orders (sqlite LOCAL): status/AWB/curier — pe nume (îmbogățire) ȘI pe AWB (găsește comanda din AWB-ul din mesaj)
    aws = [a for a in dict.fromkeys(awbs) if a]
    if os.path.exists(PROFIT_DB) and (byname or aws):
        try:
            c = sqlite3.connect(PROFIT_DB)
            def _apply(r, create=False):
                nm = r[0]
                o = byname.get(nm)
                if o is None and create:
                    o = {"o": nm, "total": 0, "fin": "", "date": "", "brand": store_prefix(nm) or "?",
                         "deliv": "?", "awb": "", "courier": "", "skus": ""}
                    byname[nm] = o
                if o is not None:
                    o["deliv"] = r[1] or "?"; o["skus"] = r[2] or ""; o["awb"] = r[3] or ""; o["courier"] = r[4] or ""
            if byname:
                names = list(byname.keys())
                q = "SELECT order_name,status_category,skus,awb,courier_key FROM profit_orders WHERE order_name IN (%s)" % ",".join("?" * len(names))
                for r in c.execute(q, names):
                    _apply(r)
            if aws:   # căutare DUPĂ AWB → găsește comanda chiar dacă n-avem email/nr-comandă (WISMO cu AWB)
                qa = "SELECT order_name,status_category,skus,awb,courier_key FROM profit_orders WHERE awb IN (%s)" % ",".join("?" * len(aws))
                for r in c.execute(qa, aws):
                    _apply(r, create=True)
            c.close()
        except Exception:
            pass
    return list(byname.values())


_TAG_CACHE = {}
AI_TAG = "ai-draft"   # tag-ul pus pe tichetele tratate de AI; suprascris de --tag (ex. ai-live la rulări live)
def tag_id(mcp, name):
    """Rezolvă nume tag → ID (add_tags_to_conversation acceptă DOAR UUID). Caută; dacă nu există, creează. Cache."""
    if name in _TAG_CACHE:
        return _TAG_CACHE[name]
    tid = None
    r = mcp.call("list_tags", {"query": name})
    for t in (r.get("tags") or []) if isinstance(r, dict) else []:
        if t.get("name") == name:
            tid = t.get("id"); break
    if not tid:
        c = mcp.call("create_tag", {"name": name})
        tid = c.get("id") if isinstance(c, dict) else None
    if tid:
        _TAG_CACHE[name] = tid
    return tid

def add_tags(mcp, cid, names):
    """Atașează tag-uri (după NUME) rezolvându-le în ID-uri — fiindcă MCP cere UUID."""
    ids = [i for i in (tag_id(mcp, n) for n in names) if i]
    if ids and cid:
        mcp.call("add_tags_to_conversation", {"conversation_id": cid, "tags": ids})


def load_queue():
    try:
        with open(QUEUE) as f: return json.load(f)
    except Exception:
        return {}

def save_queue(q):
    try:
        with open(QUEUE, "w") as f: json.dump(q, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def load_playbook():
    """Încarcă playbook-ul ÎNVĂȚAT din tichete reale (cs-procedures → .learned_playbook.md),
    parsat pe categorii. Conține procedura de-facto + replici-șablon REALE ale agenților (=voce)."""
    path = os.path.join(HERE, ".learned_playbook.md")
    try:
        txt = open(path, encoding="utf-8").read()
    except Exception:
        return {}
    out, cur, buf = {}, None, []
    for line in txt.splitlines():
        m = re.match(r"^##\s+([A-Za-z_]+)\b", line)
        if m:
            if cur:
                out[cur.lower()] = "\n".join(buf).strip()
            cur, buf = m.group(1), []
        elif cur is not None:
            buf.append(line)
    if cur:
        out[cur.lower()] = "\n".join(buf).strip()
    return out

LEARNED = load_playbook()  # gol până rulează cs-procedures → fallback pe playbook-ul din SYSTEM


def build_action_cmd(p, store, order):
    a = p.get("action")
    if a == "modify":
        # modify tratează DOAR adresa/telefonul (pre-fulfillment). Schimbările de PRODUS merg pe swap —
        # NU mapăm „adaugă produs" pe --set (care doar schimbă cantitatea unei linii deja existente).
        if not (p.get("new_address") and p.get("new_city") and p.get("new_zip")):
            return None
        return ["modify", "--order", order, "--store", store,
                "--address", p["new_address"], "--city", p["new_city"], "--zip", p["new_zip"]]
    if a == "cancel":
        return ["cancel", "--order", order, "--store", store, "--reason", "customer"]
    if a == "swap":
        return ["swap", "--from-order", order, "--store", store, "--items", p["items"]] if p.get("items") else None
    if a == "resend":
        return ["resend", "--from-order", order, "--store", store, "--items", p["items"]] if p.get("items") else None
    return None


def run_cs_action(cmd_args, apply=False, agent=None):
    """Întoarce (returncode, output). rc!=0 = eșec real (cs_actions face sys.exit pe erori)."""
    if not os.path.exists(CSA):
        return 1, "(cs_actions.py negăsit la %s)" % CSA
    full = ["uv", "run", CSA] + cmd_args
    if agent: full += ["--agent", agent]
    if apply: full += ["--apply"]
    try:
        r = subprocess.run(full, capture_output=True, text=True, timeout=120)
        return r.returncode, (r.stdout + ("\n" + r.stderr if r.stderr else "")).strip()
    except Exception as e:
        return 1, "(eroare cs-actions: %s)" % e


_PAGE_TOK_CACHE = {}
def fb_page_token(page_id):
    """Token de PAGINĂ pt page_id. Încearcă pe rând tokenurile de sistem (un cont vede doar paginile lui).
    Întoarce token de pagină REAL doar dacă vreun token chiar are acces la pagină; altfel None (NU minți callerul)."""
    if not page_id:
        return None
    if page_id in _PAGE_TOK_CACHE:
        return _PAGE_TOK_CACHE[page_id]
    for key in ("META_PAGES_TOKEN", "META_SYSTEM_TOKEN_3", "META_SYSTEM_TOKEN", "META_SYSTEM_TOKEN_2", "META_SYSTEM_TOKEN_4", "META_USER_TOKEN"):
        sys_tok = secret(key)
        if not sys_tok or sys_tok.startswith("REVOKED"):
            continue
        try:
            u = "https://graph.facebook.com/v19.0/%s?fields=access_token&access_token=%s" % (page_id, urllib.parse.quote(sys_tok))
            r = json.loads(urllib.request.urlopen(u, timeout=30).read())
            if isinstance(r, dict) and r.get("access_token"):
                _PAGE_TOK_CACHE[page_id] = r["access_token"]
                return r["access_token"]
        except Exception:
            continue
    _PAGE_TOK_CACHE[page_id] = None
    return None


def fb_post_text(post_id, page_id):
    """Textul postării/reclamei la care comentează clientul (Graph, token de pagină). '' dacă nu avem acces."""
    tok = fb_page_token(page_id)
    if not tok or not post_id:
        return ""
    for cand in ("%s_%s" % (page_id, post_id), str(post_id)):
        try:
            u = "https://graph.facebook.com/v19.0/%s?fields=message,story&access_token=%s" % (urllib.parse.quote(cand), urllib.parse.quote(tok))
            r = json.loads(urllib.request.urlopen(u, timeout=30).read())
            msg = (r.get("message") or r.get("story") or "") if isinstance(r, dict) else ""
            if msg:
                return " ".join(msg.split())[:500]
        except Exception:
            continue
    return ""


def fb_hide_comment(comment_id, page_id, hide=True):
    """Ascunde/afișează un comentariu FB/IG prin Graph API (token de pagină). Raportează răspunsul."""
    tok = fb_page_token(page_id)
    if not tok:
        return "(fără token Meta în KB)"
    segs = str(comment_id).split("_")
    cands = []
    if len(segs) >= 2:
        cands += [segs[-2] + "_" + segs[-1], segs[-1]]
    cands.append(str(comment_id))
    last = ""
    for c in cands:
        try:
            u = "https://graph.facebook.com/v19.0/%s?is_hidden=%s&access_token=%s" % (
                urllib.parse.quote(c), "true" if hide else "false", urllib.parse.quote(tok))
            r = json.loads(urllib.request.urlopen(urllib.request.Request(u, method="POST"), timeout=30).read())
            if r is True or (isinstance(r, dict) and r.get("success")):
                return "✅ comentariu %s %s" % (c, "ascuns" if hide else "afișat")
            last = json.dumps(r)
        except Exception as e:
            last = str(e)
    return "⚠️ hide eșuat (scope token / format comment-id de validat): %s" % last[:200]


def fb_private_reply(comment_id, page_id, message):
    """Trimite un MESAJ PRIVAT (DM) ca răspuns la un comentariu public FB/IG (Graph private_replies).
    Constrângeri FB: 1 singur private reply / comentariu, în fereastra de 7 zile, token pagină cu scope de mesagerie."""
    tok = fb_page_token(page_id)
    if not tok:
        return "(fără token Meta în KB)"
    if not (message or "").strip():
        return "(fără mesaj de trimis)"
    segs = str(comment_id).split("_")
    cands = []
    if len(segs) >= 2:
        cands += [segs[-2] + "_" + segs[-1], segs[-1]]
    cands.append(str(comment_id))
    last = ""
    for c in cands:
        try:
            u = "https://graph.facebook.com/v19.0/%s/private_replies" % urllib.parse.quote(c)
            data = urllib.parse.urlencode({"message": message, "access_token": tok}).encode()
            r = json.loads(urllib.request.urlopen(urllib.request.Request(u, data=data, method="POST"), timeout=30).read())
            if isinstance(r, dict) and (r.get("id") or r.get("message_id")):
                return "✅ mesaj privat (DM) trimis pentru comentariul %s" % c
            last = json.dumps(r)
        except Exception as e:
            last = str(e)
    return "⚠️ private reply eșuat (scope token / fereastră 7 zile / format comment-id de validat): %s" % last[:200]


def do_approve(mcp, conv_no, agent):
    q = load_queue()
    p = q.get(str(conv_no))
    if not p:
        print("Nicio propunere salvată pentru #%s. Rulează întâi flow-ul (dry-run)." % conv_no); return
    if p.get("applied"):
        print("#%s a fost deja aplicat — nu reaplic (evit dublarea)." % conv_no); return
    applied_ok = False
    if p.get("cmd"):
        if not agent:
            print("Acțiunea necesită --agent <Raluca/Oana/Andra/Anna/OanaO>."); return
        print("APLIC acțiune #%s: %s" % (conv_no, " ".join(p["cmd"])))
        rc, out = run_cs_action(p["cmd"], apply=True, agent=agent); print(out)
        # succes = exit 0 ȘI marker ✅ ȘI fără avertisment de eșec parțial (⚠). NU mai ghicim din 'error'/'eroare'.
        if rc != 0 or "✅" not in out or "⚠" in out:
            print("⚠️ Acțiunea a EȘUAT / e incompletă — NU salvez draftul de confirmare (nu mint clientul). Entry-ul rămâne ne-aplicat (poți reîncerca)."); return
        applied_ok = True
        # confirmarea „FĂCUT" se generează DOAR acum, după aplicare reușită
        if p.get("ctx") and p.get("action_desc"):
            try:
                p["draft"] = llm(SYSTEM, p["ctx"] + "\nACTIUNE_APLICATA: %s la comanda %s." % (p["action_desc"], p.get("order") or ""))[0].strip()
            except Exception:
                pass
    if p.get("hide"):
        h = p["hide"]
        print("HIDE #%s:" % conv_no, fb_hide_comment(h.get("comment_id"), h.get("page_id"), hide=True))
        applied_ok = True
    # la hide NU salvăm draft (comentariul se ascunde); altfel salvăm draftul public
    _cmode = (p.get("hide") or {}).get("mode")
    if p.get("draft") and _cmode != "hide":
        res = mcp.call("create_draft", {"conversation_id": p["cid"], "body": p["draft"]})
        ok = not (isinstance(res, dict) and res.get("_error"))
        print("✅ DRAFT salvat (NU trimis)." if ok else "⚠️ create_draft: %s" % res)
    if applied_ok and p.get("cid"):  # marchează tichetul drept tratat de AI
        add_tags(mcp, p["cid"], [AI_TAG])
    if applied_ok:  # consumă acțiunile ca să nu se reaplice la o a doua rulare --approve
        p["cmd"] = None; p["hide"] = None; p["applied"] = True
        q[str(conv_no)] = p; save_queue(q)


def do_send(mcp, conv_no, agent):
    """TRIMITE LIVE răspunsul (draftul din coadă) la client prin send_message. Customer-facing, ireversibil.
    Doar per-tichet, explicit. Refuză escaladările (acelea cer om) și retrimiterea."""
    q = load_queue()
    p = q.get(str(conv_no))
    if not p:
        print("Nicio propunere salvată pentru #%s. Rulează întâi flow-ul." % conv_no); return
    if p.get("sent"):
        print("#%s a fost deja TRIMIS — nu retrimit (evit dublarea)." % conv_no); return
    if p.get("escalate"):
        print("#%s e ESCALADAT → preia un om, NU trimit automat (draftul e doar mesaj de așteptare)." % conv_no); return
    if (p.get("hide") or {}).get("mode") == "hide":
        print("#%s e propus la HIDE (spam) — nu e de trimis un răspuns." % conv_no); return
    draft = (p.get("draft") or "").strip()
    cid = p.get("cid")
    if not draft or not cid:
        print("#%s nu are draft/conversation_id de trimis." % conv_no); return
    res = mcp.call("send_message", {"conversation_id": cid, "body": draft})
    ok = not (isinstance(res, dict) and res.get("_error"))
    if ok:
        print("📤 TRIMIS LIVE la client #%s (send_message)." % conv_no)
        add_tags(mcp, cid, [AI_TAG, "ai-sent"])
        # tichetul a primit răspuns → îl ÎNCHIDEM (să nu rămână open)
        cres = mcp.call("update_conversation_status", {"conversation_id": cid, "status": "CLOSED"})
        print("   ✅ Tichet ÎNCHIS (CLOSED) după răspuns." if not (isinstance(cres, dict) and cres.get("_error")) else "   ⚠️ close eșuat: %s" % cres)
        p["sent"] = True; q[str(conv_no)] = p; save_queue(q)
    else:
        print("⚠️ send_message a EȘUAT pentru #%s: %s" % (conv_no, res))


def escalation_note(level, reason, problem, name, phone, email, order_line, elsewhere, sent, suggested):
    return ("⚠️ ESCALADARE [%s] — %s\n"
            "Problemă: %s\n"
            "Client: %s | tel: %s | email: %s\n"
            "Comandă: %s\n"
            "A mai scris pe: %s\n"
            "Sentiment: %s\n"
            "→ ACȚIUNE SUGERATĂ: %s\n"
            "(brief auto cs_auto_draft — verifică înainte de a acționa)" % (
                level, reason, problem, name or "?", phone or "—", email or "—",
                order_line or "—", elsewhere, sent, suggested or "preia și contactează clientul"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument("--channel", default=None)
    ap.add_argument("--create-draft", action="store_true", help="scrie DRAFTURI + rutare escaladare (NU trimite, NU aplică acțiuni)")
    ap.add_argument("--approve", default=None, help="nr conversație: aplică acțiunea propusă + salvează draftul")
    ap.add_argument("--agent", default=os.environ.get("CS_AGENT"))
    ap.add_argument("--actions", default="modify,cancel,swap,resend", help="acțiuni ACTIVE (restul doar draft); ex --actions modify,cancel | none")
    ap.add_argument("--scan", type=int, default=150)
    ap.add_argument("--sleep", type=float, default=0.2, help="pauză (s) între tichete — crește pt rate-safety pe loturi mari (ex. 0.5)")
    ap.add_argument("--lean", action="store_true", help="proces REDUS pt volum mare: fără 360/SSH (comenzi), fără rutare escaladare (priority/notă) — doar transcript → draft → create_draft. Mult mai rapid + mai puține scrieri Richpanel.")
    ap.add_argument("--ground", action="store_true", help="GROUNDING self-contained (pt VPS/cron): caută comenzile clientului DIRECT din DB metrics + profitability.db (fără SSH/uv) → draftul are status/AWB real. Mai lent ca lean, dar fără halucinări de comandă.")
    ap.add_argument("--skip-tagged", action="store_true", help="sare tichetele care AU deja tag-ul AI (--tag) — pt cron/reluare: draftează DOAR tichetele noi, fără dubluri")
    ap.add_argument("--no-comments", action="store_true", help="exclude complet canalele de comentarii (facebook_feed_comment/instagram_comment) — nu le draftează (ex. pt cron: comentariile rămân pt CS)")
    ap.add_argument("--apply-send", action="store_true", help="⚠️ LIVE: TRIMITE răspunsul la client (send_message) + închide tichetul, în loc de draft — DOAR pe ne-escaladate + ne-comentariu (escaladările/comentariile rămân draft, le ia un om). Customer-facing, IREVERSIBIL. Necesită --create-draft.")
    ap.add_argument("--json", action="store_true", help="emite drafturile structurat (audit/integrare) — JSON pe ultima linie după marcajul @@JSON@@")
    ap.add_argument("--close-spam", action="store_true", help="închide (CLOSED) + tag 'spam' tichetele detectate ca spam/notificare automată")
    ap.add_argument("--tag", default="ai-draft", help="tag-ul pus pe tichetele tratate de AI (ex. --tag ai-live pt o rulare live)")
    ap.add_argument("--only", default=None, help="procesează DOAR aceste numere de conversație (lista separată prin virgulă) — pt regenerare țintită")
    ap.add_argument("--send", default=None, help="nr conversație: TRIMITE LIVE răspunsul (draftul din coadă) la client via send_message — customer-facing, IREVERSIBIL (refuză escaladări/hide/retrimitere)")
    a = ap.parse_args()
    global AI_TAG
    AI_TAG = a.tag
    ALLOWED = set(x.strip().lower() for x in a.actions.split(",") if x.strip() and x.strip().lower() != "none")
    mcp = MCP(secret("RICHPANEL_MCP_TOKEN"))
    COURIER = {"dpd-ro": "DPD", "dpd": "DPD", "sameday": "Sameday", "packeta": "Packeta", "econt": "Econt"}

    if a.approve:
        do_approve(mcp, a.approve, a.agent); return
    if a.send:
        do_send(mcp, a.send, a.agent); return

    ONLY = [x.strip().lstrip("#") for x in (a.only or "").split(",") if x.strip()]
    picked = []
    if ONLY:
        # regenerare ȚINTITĂ: stub-uri (fără fetch upfront) — tichetul se ia INCREMENTAL în buclă (evită burst-ul de citiri)
        picked = [{"conversation_no": x, "_stub": True} for x in ONLY]
    else:
        page, seen = 1, set()
        while len(picked) < a.limit and len(seen) < a.scan:
            args = {"status": "open", "page": page, "per_page": 50, "sortKey": "last_message_at", "order": "desc"}
            if a.channel: args["channel"] = a.channel
            r = mcp.call("list_conversations", args)
            batch = (r.get("tickets") or r.get("conversations") or r.get("results") or []) if isinstance(r, dict) else []
            if not batch: break
            new_this_page = 0
            for t in batch:
                cid = t.get("id") or t.get("conversation_no")
                if cid in seen: continue
                seen.add(cid); new_this_page += 1
                if (t.get("last_message_sender_type") or "").lower() != "customer": continue
                picked.append(t)
                if len(picked) >= a.limit: break
            # API-ul nu setează 'has_more' fiabil → paginează cât timp apar tichete NOI; oprește când nu mai apar
            if new_this_page == 0: break
            page += 1
            time.sleep(0.15)  # pauză între pagini (rate-safe pe listare)

    head = "APLICAT — drafturi + rutare escaladare scrise (acțiuni NU)" if a.create_draft else "DRY-RUN — nimic scris"
    print("═" * 92)
    print("  CS AUTO-DRAFT  |  %s  |  %d tichete care așteaptă răspuns" % (head, len(picked)))
    print("═" * 92)
    queue = load_queue()
    rows = []
    n_spam = 0

    for i, t in enumerate(picked, 1):
        if t.get("_stub"):   # --only: ia tichetul ACUM (incremental), nu upfront → fără burst de citiri
            cv0 = mcp.call("get_conversation", {"conversation_number": str(t.get("conversation_no")), "mode": "compact"})
            tk = cv0.get("ticket") if isinstance(cv0, dict) else None
            if not tk:
                print("  [%d/%d] #%s negăsit (sărit)." % (i, len(picked), t.get("conversation_no"))); continue
            t = tk
        no = t.get("conversation_no"); cid = t.get("id")
        channel = (t.get("channel") or "").lower()
        plat_label, plat_rule = PLATFORM.get(channel, (channel or "necunoscut", "ton prietenos, la obiect."))
        is_public = channel in ("facebook_feed_comment", "instagram_comment")
        if a.no_comments and is_public:   # cron: comentariile NU se draftează → rămân pt CS (hide spam se face separat)
            continue
        cur_tags = list(t.get("tag_names") or [])   # pt --skip-tagged
        cust = t.get("customer") or {}
        name = cust.get("name") or ""
        email = (cust.get("email") or (t.get("from") or {}).get("email") or "").lower()
        raw_phone = str(cust.get("phone") or "")
        phone = raw_phone if (raw_phone.isdigit() and 9 <= len(raw_phone) <= 13) else ""
        subj = t.get("subject") or ""; first = t.get("first_message") or ""
        blob = subj + " " + first

        last_cust = " ".join(first.split())[:400]   # mesajul CURENT al clientului (la care răspundem); implicit = primul
        if (t.get("comment_count") or 1) <= 1 and is_public:
            tr = "- [CLIENT] " + last_cust
        else:
            cv = mcp.call("get_conversation", {"conversation_number": str(no), "mode": "audit", "max_messages": 20})
            cur_tags = (cv.get("ticket") or {}).get("tag_names") or cur_tags   # tag_names reale (lista summary nu le are)
            msgs = (cv.get("messages_page") or {}).get("messages") or cv.get("messages") or []
            lines = []
            for m in msgs[-12:]:
                if m.get("is_private"): continue
                txt = " ".join((m.get("text") or "").split())
                if not txt: continue
                is_client = not m.get("is_ai") and not m.get("author_is_workspace_agent")
                who = "[AI]" if m.get("is_ai") else ("[AGENT]" if m.get("author_is_workspace_agent") else "[CLIENT]")
                lines.append("- %s %s" % (who, txt[:400]))
                if is_client:
                    last_cust = txt[:400]   # reține ULTIMUL mesaj al clientului din fir
            tr = "\n".join(lines) or ("- [CLIENT] " + last_cust)
        # marchează explicit ULTIMUL mesaj al clientului — la EL răspundem; restul firului = doar context
        tr = tr + "\n>>> ULTIMUL MESAJ AL CLIENTULUI (răspunde la ACESTA; restul firului = context): " + last_cust
        if a.skip_tagged and AI_TAG and AI_TAG in cur_tags:   # deja draftat (cron/reluare) → sări, fără dublu
            print("  [%d/%d] #%s · deja %s → sărit." % (i, len(picked), no, AI_TAG)); continue
        # pe comentarii publice: atașează textul POSTĂRII/reclamei la care comentează clientul (clientul o vede → și noi trebuie),
        # dacă avem token de pagină pt brandul respectiv (altfel '' → fallback la a întreba ce produs)
        if is_public:
            _pg = ((t.get("to") or {}).get("id") if isinstance(t.get("to"), dict) else "") or ""
            _segs = str(cid).split("_")
            _post_txt = fb_post_text(_segs[1], _pg) if (len(_segs) >= 2 and _pg) else ""
            if _post_txt:
                tr = "POSTAREA/RECLAMA la care comentează clientul (folosește-o ca să identifici PRODUSUL și să răspunzi la obiect): " + _post_txt + "\n" + tr

        sent_lab, sent_int = sentiment(blob + " " + tr)
        store_name = PAGE_STORE.get(((t.get("to") or {}).get("id") if isinstance(t.get("to"), dict) else None) or "") or "magazinul nostru"
        if store_name == "magazinul nostru":   # email: derivă brandul din domeniul adresei magazinului (ex. contact@esteban.ro)
            _to_email = (t.get("to") or {}).get("email") if isinstance(t.get("to"), dict) else ""
            store_name = brand_from_email(_to_email) or store_name

        orders, other = [], []
        elsewhere = "necunoscut (fără email/telefon real — pe comentarii publice nu se poate lega)"
        _gtxt = blob + " " + last_cust
        if a.ground and (email or phone or ORDER_RE.search(_gtxt) or AWB_RE.search(_gtxt)):
            # GROUNDING self-contained (VPS/cron): comenzi reale din DB metrics + status/AWB din profitability.db (nume, nr-comandă, AWB)
            _onames = ["".join(m.group(0).split()).replace("-", "").upper() for m in ORDER_RE.finditer(_gtxt)]
            _awbs = AWB_RE.findall(_gtxt)
            orders = lookup_orders(email, phone, _onames, _awbs)
            if (store_name == "magazinul nostru") and orders:
                store_name = orders[0].get("brand") or store_name
            elsewhere = ("grounded — %d comenzi găsite în DB" % len(orders)) if orders else "grounded — nicio comandă găsită în DB"
        elif a.lean:
            elsewhere = "(lean — fără context 360)"
        elif (not a.lean) and (email or phone):
            ci = customer_ident(no)
            orders = ci.get("orders", []) or []
            if (store_name == "magazinul nostru") and orders:
                store_name = orders[0].get("brand") or store_name
            other = [c for c in (ci.get("convos") or []) if str(c.get("no")) != str(no)]
            ch_counts = {}
            for c in other:
                lab = CH_LABEL.get(c.get("channel"), c.get("channel") or "?")
                ch_counts[lab] = ch_counts.get(lab, 0) + 1
            elsewhere = ", ".join("%s×%d" % (k, v) for k, v in sorted(ch_counts.items(), key=lambda x: -x[1])) or "doar aici (niciun alt tichet)"

        od = "\n".join("    • %s (%s): status=%s, curier=%s, AWB=%s, produse=%s" % (
            o.get("o"), o.get("brand", o.get("store", "?")), o.get("deliv", "?"),
            COURIER.get((o.get("courier") or "").lower(), o.get("courier") or "?"),
            o.get("awb", "") or "—", (o.get("skus") or "")[:40]) for o in orders[:6]) or "    (nicio comandă găsită)"
        hist_txt = "\n".join("    • #%s [%s] %s [%s]" % (c.get("no"), CH_LABEL.get(c.get("channel"), c.get("channel") or "?"),
                             " ".join((c.get("subject") or "").split())[:42], (c.get("status") or "")[:6]) for c in other[:8]) or "    (fără alte tichete)"

        # ---- 1) IDENTIFICARE (LLM triaj) ----
        idn = {"problem": "", "category": categorize_hint(blob, channel), "severity": "none", "escalate": False,
               "escalation_reason": "", "suggested_action": "", "action": "none", "order": "", "comment_action": "none",
               "product": "", "spam": False, "confidence": 0.0, "missing": []}
        ident_user = ("PLATFORMĂ: %s\nMESAJ CLIENT:\n%s\n\nCOMENZILE LUI:\n%s\n\nA MAI SCRIS PE: %s | nr alte tichete: %d\nSENTIMENT euristic: %s/%s\nHINT categorie: %s" % (
            plat_label, tr, od, elsewhere, len(other), sent_lab, sent_int, idn["category"]))
        try:
            raw, _ = llm(IDENTIFY_SYS, ident_user, js=True)
            got = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
            idn.update({k: got.get(k, idn.get(k)) for k in idn})
            for k in ("new_address", "new_city", "new_zip", "new_phone", "items"):
                idn[k] = got.get(k, "")
        except Exception as e:
            print("  ⚠️ triaj LLM eșuat (#%s) → fallback pe regex/euristici: %s" % (no, str(e)[:80]), file=sys.stderr)
        cat = idn.get("category") if (isinstance(idn.get("category"), str) and idn.get("category")) else "altele"

        # ---- SPAM / notificare automată → EXCLUS din draft (pe ORICE canal); opțional închide+arhivează ----
        if bool(idn.get("spam")) or cat == "spam_automat":
            n_spam += 1
            print("\n" + "─" * 92)
            print("  [%d/%d] #%s · %s · %s · 🚫 SPAM/automat → EXCLUS (fără draft)" % (i, len(picked), no, store_name, channel))
            print("  motiv: %s" % (idn.get("problem") or " ".join((first or subj).split())[:70]))
            if a.close_spam and cid:
                add_tags(mcp, cid, ["spam"])
                res = mcp.call("update_conversation_status", {"conversation_id": cid, "status": "CLOSED"})
                ok = not (isinstance(res, dict) and res.get("_error"))
                print("  🗑️ Închis + tag spam." if ok else "  ⚠️ close: %s" % res)
            else:
                print("  → ar închide+arhiva (rulează cu --close-spam ca să le închizi).")
            rows.append({"no": no, "store": store_name, "channel": channel, "cat": cat, "spam": True, "draft": ""})
            continue

        # escaladare: identificată de LLM SAU semnale dure
        is_esc = bool(idn.get("escalate")) or str(idn.get("severity")).upper() in ("HIGH", "URGENT") or bool(ESCAL.search(deacc(blob)))
        level = "URGENT" if (str(idn.get("severity")).upper() == "URGENT" or ESCAL.search(deacc(blob))) else "HIGH"

        # ---- comanda țintă (reconciliată) ----
        target, order_name, ambiguous, why_amb = resolve_target_order(blob + " " + tr, orders)
        target_line = None
        if target:
            target_line = "%s (%s) status=%s AWB=%s" % (target.get("o"), target.get("brand", "?"), target.get("deliv", "?"), target.get("awb") or "—")

        action_note = ""; proposal_line = ""; cmd = None; action_desc = ""

        if is_esc:
            proposal_line = "⛳ ESCALADARE %s: %s → HIGH + tag escaladare%s + notă-brief (preia un coleg)." % (
                level, idn.get("escalation_reason") or idn.get("problem") or cat, "/de-sunat" if phone else "")
        elif cat in ACTION_CATS and not is_public:
            if ambiguous or not order_name:
                proposal_line = "❓ Comandă neclară (%s) → NICIO acțiune; draftul cere clientului să confirme comanda." % why_amb
            else:
                act = idn.get("action") or "none"
                if act not in ("none", None) and act not in ALLOWED:
                    proposal_line = "🔕 Acțiune '%s' dezactivată (--actions) → doar draft." % act; act = "none"
                st = fulfillment_state(target)
                store_pfx = store_prefix(order_name, store_name)
                if act in ("modify", "cancel") and st == "post":
                    proposal_line = "🚫 PRE-FULFILLMENT NU: %s pare deja expediată (AWB) → NU modific/anulez; draftul oferă alternativă curier." % order_name
                elif act != "none" and _f(idn.get("confidence")) >= 0.55 and store_pfx:
                    idn["order"] = order_name
                    cmd = build_action_cmd(idn, store_pfx, order_name)
                    if cmd:
                        _, dry = run_cs_action(cmd, apply=False)
                        proposal_line = "🔧 PROPUNERE %s pe %s (necesită aprobare):\n      cmd: %s\n      %s" % (
                            act.upper(), order_name, " ".join(cmd), " ".join(dry.split())[:280])
                        action_desc = {"modify": "adresa/datele actualizate", "cancel": "comanda anulată",
                                       "swap": "produs schimbat", "resend": "retrimitere gratuită inițiată"}.get(act, act)
                        # NU injectăm ACTIUNE_APLICATA acum (e doar PROPUNERE). Draftul rămâne „am preluat solicitarea";
                        # confirmarea „făcut" se regenerează în do_approve DOAR după aplicare reușită.
                    else:
                        proposal_line = "⚠️ %s — date insuficiente (%s) → draftul cere completarea." % (act, ",".join(idn.get("missing") or []))
                elif act != "none":
                    proposal_line = "ℹ️ Acțiune neclară/insuficientă → doar draft (cere date)."

        # ---- 2.5) Moderare comentariu public FB/IG (corectează replyzen): hide spam/abuz; restul = răspuns PUBLIC scurt (NU trimitem DM — invităm clientul în privat/telefon) ----
        hide_obj = None
        cact = idn.get("comment_action") or "none"
        page_id = ((t.get("to") or {}).get("id") if isinstance(t.get("to"), dict) else "") or ""
        if is_public and cact == "hide":
            hide_obj = {"comment_id": cid, "page_id": page_id, "mode": "hide"}
            proposal_line = (proposal_line + "\n  " if proposal_line else "") + "🙈 PROPUNERE HIDE (spam/abuz) — ascunde comentariul; aprobă cu --approve %s" % no

        # ---- 3) DRAFT ----
        sys_prompt = HOLDING if is_esc else SYSTEM
        # pe canale PUBLICE redactăm datele personale STRUCTURAL (nu doar prin prompt) — comentariile rămân publice, fără DM
        od_ctx = od if not is_public else "(comenzi ascunse — canal public, NU expune date personale)"
        phone_ctx = (phone or "—") if not is_public else "—"
        email_ctx = (email or "—") if not is_public else "—"
        learned = LEARNED.get(cat, "")
        learned_blk = ("\nPROCEDURA INVATATA + VOCEA AGENTILOR REALI pt '%s' (urmeaza procedura; imita tonul/structura replicilor; NU copia datele din exemple):\n%s\n" % (cat, learned[:1800])) if learned else ""
        # limba = ce a scris CLIENTUL (detecție pe text) → apoi piața brandului → apoi LLM → ro
        lang = detect_lang(blob + " " + tr) or STORE_LANG.get(store_name) or idn.get("language") or "ro"
        # telefon de comandă (recomandă sunatul la comenzi/presale, dacă avem numărul brandului)
        phone_order = STORE_PHONE.get(store_name, "")
        # numărul e util la comenzi/presale ȘI la comentarii publice (invităm clientul să sune)
        tel_blk = ("\nTELEFON_COMANDĂ: %s" % phone_order) if (phone_order and (is_public or cat in ("comanda_noua", "presale_intrebare"))) else ""
        ctx = ("PLATFORMĂ: %s — STIL: %s\nMAGAZIN/BRAND: %s\nCLIENT: %s | email=%s | tel=%s\n"
               "PROBLEMA IDENTIFICATĂ: %s\nPRODUS: %s\nCATEGORIE: %s | LIMBA: %s | SENTIMENT: %s/%s%s\n%s\n\n"
               "CONVERSAȚIA:\n%s\n\nCOMENZILE CLIENTULUI:\n%s\n\nA MAI SCRIS PE: %s\nALTE TICHETE:\n%s\n%s\n"
               "SCRIE ÎN LIMBA ÎN CARE A SCRIS CLIENTUL în conversația de mai sus (orientativ: limba≈%s; ro/cz/pl/bg/en). Brandurile pe Cehia/Polonia/Bulgaria (Bonhaus CZ/PL/BG) răspund de regulă în limba pieței, DAR dacă clientul a scris clar în altă limbă (ex. engleză), răspunde în limba LUI. Exemplele de procedură/voce pot fi în română — folosește-le DOAR pentru pași+ton, NU pentru limbă. Răspunsul depinde de brand+produs. Scrie DOAR textul răspunsului, respectând stilul platformei." % (
                   plat_label, plat_rule, store_name, name or "?", email_ctx, phone_ctx,
                   idn.get("problem") or "(neclar)", idn.get("product") or "—", cat, lang, sent_lab, sent_int,
                   "  [ESCALAT — doar mesaj de așteptare]" if is_esc else "",
                   (("\n" + action_note) if action_note else "") + tel_blk, tr, od_ctx, elsewhere, hist_txt, learned_blk, lang))
        try:
            draft, engine = llm(sys_prompt, ctx)
        except Exception as e:
            draft, engine = "(eroare LLM: %s)" % e, "—"

        # POST-FILTRU ANTI-HALUCINARE: dacă NU avem datele comenzii dar draftul afirmă lookup/status/preț/dimensiune → corectează
        if not is_esc and not draft.startswith("(eroare") and not has_order_data(od_ctx) and HALLU.search(draft):
            corr = ctx + ("\n\n⛔ Răspunsul tău anterior CONȚINEA INFORMAȚIE INVENTATĂ (lookup/„am verificat/nu am găsit”, status comandă, preț, dimensiune sau telefon pe care NU le ai în context). "
                          "Rescrie complet, FĂRĂ să inventezi NIMIC și FĂRĂ să spui că ai căutat/găsit/verificat ceva. "
                          "Cere clientului numărul comenzii SAU un număr de telefon (pt orice ține de o comandă), sau, la întrebări de produs, spune onest că revii cu detaliile exacte / poate verifica pe site. Scrie DOAR răspunsul.")
            try:
                d2, _ = llm(SYSTEM, corr)
                if d2 and not HALLU.search(d2):
                    draft, engine = d2.strip(), engine + "+corectat"
            except Exception:
                pass
            if HALLU.search(draft):   # tot fabrică → șablon SIGUR, onest (pe categorie)
                if cat in ("presale_intrebare", "comanda_noua"):
                    draft = "Bună ziua! Vă mulțumim pentru interes. Vă revin cu detaliile exacte cât mai curând; între timp puteți vedea informațiile actualizate și pe site. Vă mulțumim!"
                else:
                    draft = "Bună ziua! Ca să verific exact comanda dumneavoastră, îmi puteți spune numărul comenzii sau un număr de telefon asociat? Revin imediat cu detaliile. Vă mulțumesc!"
                engine = "șablon-sigur"

        print("\n" + "─" * 92)
        cmoji = "🙈" if (hide_obj or {}).get("mode") == "hide" else ""
        flag = ("⛳%s " % level if is_esc else "") + ("🔧" if cmd else "") + cmoji
        print("  [%d/%d] #%s · %s · %s · %s · sent=%s/%s %s" % (i, len(picked), no, store_name, plat_label, cat, sent_lab, sent_int, flag))
        print("  client: %s | comenzi: %d | a mai scris: %s" % (name or "?", len(orders), elsewhere))
        print("  problemă: %s" % (idn.get("problem") or " ".join((first or subj).split())[:80]))
        if proposal_line:
            for ln in proposal_line.splitlines(): print("  " + ln)
        print("  ┌─ DRAFT%s (%s) " % (" AȘTEPTARE" if is_esc else "", engine) + "─" * 12)
        for ln in draft.strip().splitlines(): print("  │ " + ln)
        print("  └" + "─" * 42)

        queue[str(no)] = {"cid": cid, "draft": draft.strip(), "cmd": cmd, "hide": hide_obj,
                          "ctx": ctx, "action_desc": action_desc, "order": order_name,
                          "store": store_name, "cat": cat, "escalate": is_esc}
        rows.append({"no": no, "store": store_name, "channel": channel, "cat": cat, "escalate": is_esc,
                     "language": lang, "cust_msg": (last_cust or first or subj or "")[:240],
                     "comment_action": (hide_obj or {}).get("mode"), "draft": draft.strip()})
        if cmd or hide_obj:
            print("  → aprobă:  uv run cs_auto_draft.py --approve %s%s" % (no, " --agent <Nume>" if cmd else ""))

        # ---- scrieri în Richpanel (doar cu --create-draft) ----
        if a.create_draft and cid:
            # rutarea escaladării (priority/tag/notă) se face DOAR în modul complet; lean = doar draftul
            if is_esc and not a.lean:
                tags = ([AI_TAG] if AI_TAG else []) + ["escaladare", "esc-%s" % level.lower()] + (["de-sunat"] if phone else [])
                note = escalation_note(level, idn.get("escalation_reason") or "", idn.get("problem") or "", name, phone, email,
                                       target_line, elsewhere, "%s/%s" % (sent_lab, sent_int), idn.get("suggested_action"))
                mcp.call("update_conversation", {"conversation_id": cid, "priority": "HIGH"})
                add_tags(mcp, cid, tags)
                mcp.call("add_private_note", {"conversation_id": cid, "body": note})
                print("  ⛳ Rutat: HIGH + %s + notă-brief." % ("+".join(tags)))
            # la hide NU salvăm draft — comentariul se ascunde la --approve
            if (hide_obj or {}).get("mode") == "hide":
                print("  (hide → fără draft; se ascunde la --approve)")
            elif draft.strip().startswith("(eroare LLM") or len(draft.strip()) < 5:
                # GARDĂ: nu salva gunoi în Richpanel (LLM picat / draft gol) — sare tichetul
                print("  ⛔ draft invalid (eroare LLM / gol) → NU salvez (sar tichetul).")
            elif a.apply_send and not is_esc and not is_public:
                # LIVE: trimite răspunsul la client + închide tichetul. NUMAI ne-escaladat + ne-comentariu
                # (escaladările + comentariile rămân DRAFT, le ia un om). Garda de mai sus a exclus deja draft invalid.
                res = mcp.call("send_message", {"conversation_id": cid, "body": draft.strip()})
                ok = not (isinstance(res, dict) and res.get("_error"))
                if ok:
                    add_tags(mcp, cid, [t for t in (AI_TAG, "ai-sent") if t])
                    mcp.call("update_conversation_status", {"conversation_id": cid, "status": "CLOSED"})
                    print("  📤 TRIMIS LIVE la client + tichet ÎNCHIS.")
                else:
                    print("  ⚠️ send_message EȘUAT → NU închid, NU marchez: %s" % res)
            else:
                res = mcp.call("create_draft", {"conversation_id": cid, "body": draft.strip()})
                ok = not (isinstance(res, dict) and res.get("_error"))
                if ok and AI_TAG:   # tag DOAR dacă a fost cerut (--tag ""  → fără tag)
                    add_tags(mcp, cid, [AI_TAG])
                print(("  ✅ DRAFT salvat%s (NU trimis)." % (" + tag %s" % AI_TAG if AI_TAG else "")) if ok else "  ⚠️ create_draft: %s" % res)
        time.sleep(a.sleep)

    save_queue(queue)
    print("\n  🚫 Spam/automat: %d %s." % (n_spam, "închise (CLOSED+tag spam)" if a.close_spam else "excluse din draft (rulează --close-spam ca să le închizi)"))
    if a.json:
        print("@@JSON@@" + json.dumps(rows, ensure_ascii=False))
    if not a.create_draft:
        print("\n  → DRY-RUN. --create-draft: salvează drafturile + rutează escaladările (URGENT/tag/notă). Acțiunile rămân propuneri (--approve).")


if __name__ == "__main__":
    main()
