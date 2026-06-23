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
import os, re, sys, json, subprocess, urllib.request, urllib.parse, argparse, time

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
    "facebook_feed_comment": ("Comentariu public Facebook", "PUBLIC → SCURT, cald, POLITICOS la PLURAL (dumneavoastra/va, NU la 'tu'); NU expune comanda/AWB/telefon; invita in privat (DM/inbox)."),
    "instagram_comment": ("Comentariu public Instagram", "PUBLIC → SCURT, cald, POLITICOS la PLURAL (dumneavoastra/va, NU la 'tu'); NU expune comanda/AWB/telefon; invita in privat (DM)."),
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
# brand -> limba pieței (semnal SIGUR de limbă, mai fiabil decât detecția LLM pe comentarii scurte)
STORE_LANG = {"Bonhaus CZ": "cz", "Bonhaus PL": "pl", "Bonhaus BG": "bg"}
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

def secret(k):
    return os.environ.get(k) or subprocess.run(["uv", "run", KB, "secret-get", k], capture_output=True, text=True).stdout.strip()

class MCP:
    def __init__(self, token):
        self.t = token
        self._post({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                    "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "autodraft", "version": "1"}}})
    def _post(self, p):
        h = {"Authorization": "Bearer " + self.t, "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        req = urllib.request.Request(MCP_URL, data=json.dumps(p).encode(), headers=h)
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read().decode()
        ln = [l for l in body.splitlines() if l.startswith("data:")]
        return json.loads(ln[-1][5:]) if ln else json.loads(body)
    def call(self, name, args):
        try:
            r = self._post({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
            txt = r["result"]["content"][0]["text"]
            try: return json.loads(txt)
            except Exception: return {"_text": txt}
        except Exception as e:
            return {"_error": str(e)}

def llm(system, user, js=False):
    ak = secret("ANTHROPIC_API_KEY")
    if ak:
        body = {"model": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"), "max_tokens": 900,
                "system": system, "messages": [{"role": "user", "content": user}]}
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(),
                                     headers={"x-api-key": ak, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=90).read())["content"][0]["text"], "claude"
    ok = secret("OPENAI_API_KEY")
    if ok:
        body = {"model": os.environ.get("DRAFT_MODEL", "gpt-4o"), "temperature": 0.2,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if js: body["response_format"] = {"type": "json_object"}
        req = urllib.request.Request("https://api.openai.com/v1/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Authorization": "Bearer " + ok, "content-type": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=90).read())["choices"][0]["message"]["content"], "openai/gpt"
    raise SystemExit("Nicio cheie LLM în KB (ANTHROPIC_API_KEY / OPENAI_API_KEY).")

# ---- pasul de IDENTIFICARE (triaj) ----
IDENTIFY_SYS = """Ești triajul Customer Service ARONA (magazine COD: parfumuri Esteban/GT/Nubra/Gento/Lab Noir; casă Grandia/Carpetto/Covoria; Bonhaus RO/CZ/PL/BG; Belasil; Magdeal/Ofertele Zilei/Reduceri bune/Apreciat/Rossi Nails).
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
 "comment_action":"hide|private_reply|public_and_private|public|none",
 "spam":true|false,
 "confidence":0.0-1.0,"missing":["ce date lipsesc"]}
SPAM (pe ORICE canal — email, DM FB/IG, comentariu): true dacă mesajul NU necesită răspuns CS — notificări automate (Meta/Facebook business, judge.me „left a review”, newsletter, reset parolă, „do not reply”, out-of-office, confirmări automate), boți, mesaje promoționale nesolicitate / spam evident. Aceste tichete se EXCLUD (nu primesc draft).
ESCALADARE: URGENT = ANPC/juridic/amenințare (avocat, instanță, dau în judecată, denunț), chargeback, refund PROMIS dar neefectuat, client foarte agresiv. HIGH = reclamație serioasă (produs/livrare) cu client supărat, client care a scris REPETAT fără rezolvare, comandă de valoare mare / client cu multe comenzi. Altfel none.
COMMENT_ACTION (doar comentarii PUBLICE FB/IG; pe celelalte canale = "none"). REGULA: la comentariile care merită răspuns CS punem DOUĂ mesaje — unul SCURT în comentariu (public) + unul DETALIAT în privat (DM):
  • "hide" = DOAR spam/troll/abuz/vulgaritate/ofense (de ascuns de pe reclamă).
  • "public_and_private" = RECLAMAȚIE/nemulțumire SAU întrebare (presale: preț/stoc/disponibilitate/culori/mărimi/„mai aveți?", ori orice întrebare reală) → mesaj PUBLIC SCURT în comentariu (reclamație: scuze scurte + „ți-am scris în privat să rezolvăm"; întrebare: răspuns scurt/general + „ți-am trimis detalii în privat"), FĂRĂ date personale + mesaj PRIVAT (DM) detaliat care chiar rezolvă.
  • "public" = comentariu benign / întrebare minoră care merită DOAR un răspuns public scurt (fără nevoie de DM).
  • "private_reply" = caz RAR, foarte sensibil, unde un mesaj public NU e potrivit deloc → doar privat.
  • "none" = pozitiv/neutru/recenzie/laudă fără întrebare → se lasă cum e.
RĂSPUNSURILE ȘI PROCEDURILE DEPIND DE BRAND ȘI DE PRODUS: ține cont de magazin (parfumuri Esteban/GT/Nubra/Gento vs casă/mobilă Grandia/Carpetto/Covoria vs cosmetice/deals) și de produsul concret (extrage-l în „product").
ACȚIUNE: action!="none" DOAR dacă e cerere clară de modificare adresă/telefon (modify), anulare (cancel), schimb produs (swap) sau retrimitere produs spart/lipsă (resend). Dacă nu e clar ce comandă sau lipsesc date → action="none" + missing. NU inventa nimic. Răspunde DOAR JSON."""

# ---- generarea DRAFTULUI ----
SYSTEM = """Ești agent Customer Service ARONA. Scrii ca un agent REAL (Cristina/Diana/Irina/Martina/Alexandra) — cald, politicos, natural, cu diacritice, fără limbaj robotic.
REGISTRU (important): FORMAL, la PLURAL, pe TOATE canalele (email, DM, chat, comentariu) — în română „dumneavoastră/vă/-ți" (NICIODATĂ „tu/ție/te/-i"); în alte limbi registrul politicos echivalent. Așa scriu agenții ARONA reali („Vă rugăm", „Vă informăm", „Vă mulțumim").
PROCEDURI:
- LIVRARE/WISMO: cu AWB+curier dă linkul corect DUPĂ curier (nu presupune DPD): DPD https://tracking.dpd.ro?shipmentNumber=<AWB>; Sameday https://www.sameday.ro/#awb=<AWB>; Packeta https://tracker.packeta.com/ro/?id=<AWB>; Econt https://www.econt.com/en/services/track-shipment/<AWB>. Întârziat → scuze+estimare. Fără AWB → spui că verifici și revii sau ceri nume+telefon+nr comandă.
- RETUR: ARONA e COD și NU încurajează returul → întreabă motivul + oferă alternativă; insistă și e eligibil → formular https://bi.grandia.ro/returns?order=<nr>&email=<email> + „Suma vă va fi returnată în maximum 14 zile de la ajungerea coletului." Parfum/igienă DESIGILAT → refuz politicos.
- PRODUS SPART (parfum): NU refund → RETRIMITERE GRATUITĂ + parfum CADOU. DEFECT/LIPSĂ (casă): cere poză; pe stoc → retrimitere/schimb; altfel retur+refund.
- PRE-VÂNZARE: răspuns clar, încurajează comanda. RECENZIE/COMPLIMENT: mulțumește scurt și cald.
REGULA DE ACȚIUNE: dacă în context apare `ACTIUNE_APLICATA: …` → confirmă acțiunea ca FĂCUTĂ. Dacă NU → nu spune niciodată că ai modificat/anulat ceva; confirmă că ai PRELUAT solicitarea sau cere datele lipsă. NU inventa.
CALIBRARE SENTIMENT: negativ → scuze sincere + asumare + soluție; pozitiv → cald; neutru → la obiect.
REGULI: limba clientului; DOAR datele din context (fără AWB/prețuri/nr inventate); respectă STILUL platformei; pe canale PUBLICE (comentarii FB/IG) scrie POLITICOS, la PLURAL (dumneavoastră/vă, NICIODATĂ „tu/ție/te") și nu scrie date personale (invită în privat); gramatică corectă („ți-am scris/v-am scris", nu „te-am scris"); DOAR textul răspunsului. Email → salut + semnătură „Cu drag, Echipa <Magazin>"; dacă magazinul e necunoscut/generic, semnează „Cu drag, echipa noastră" (NU „Echipa magazinul nostru"). Comentariu public → 1-3 fraze."""

HOLDING = """Ești agent CS ARONA. Cazul e ESCALADAT spre un coleg. Scrie DOAR un mesaj SCURT de AȘTEPTARE în limba clientului: confirmă că ai preluat sesizarea și că un coleg revine cât mai curând (azi/în cel mai scurt timp). Ton cald, empatic dacă e supărat. REGISTRU FORMAL, la PLURAL — în română „dumneavoastră/vă" (NU „tu/ție/te"); alte limbi: registrul politicos echivalent. NU promite soluții concrete, NU da detalii de comandă pe canal public. Email → salut + „Cu drag, Echipa <Magazin>"; dacă magazinul e necunoscut/generic, semnează „Cu drag, echipa noastră" (NU „Echipa magazinul nostru"). Comentariu public → 1-2 fraze + invitație în privat."""


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


def fb_page_token(page_id):
    sys_tok = secret("META_SYSTEM_TOKEN") or secret("META_USER_TOKEN")
    if not sys_tok or not page_id:
        return None
    try:
        u = "https://graph.facebook.com/v19.0/%s?fields=access_token&access_token=%s" % (page_id, urllib.parse.quote(sys_tok))
        r = json.loads(urllib.request.urlopen(u, timeout=30).read())
        return r.get("access_token") or sys_tok
    except Exception:
        return sys_tok


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
        h = p["hide"]; mode = h.get("mode", "hide")
        if mode == "private_reply":
            print("PRIVATE REPLY (DM) #%s:" % conv_no, fb_private_reply(h.get("comment_id"), h.get("page_id"), p.get("draft") or ""))
        elif mode == "public_and_private":
            print("PUBLIC draft salvat + PRIVATE REPLY (DM) #%s:" % conv_no,
                  fb_private_reply(h.get("comment_id"), h.get("page_id"), h.get("private_msg") or ""))
        else:
            print("HIDE #%s:" % conv_no, fb_hide_comment(h.get("comment_id"), h.get("page_id"), hide=True))
        applied_ok = True
    # NU pune textul pe comentariul public la private_reply (DM-only) sau hide — ar fi un foot-gun de publicare
    _cmode = (p.get("hide") or {}).get("mode")
    if p.get("draft") and _cmode not in ("private_reply", "hide"):
        res = mcp.call("create_draft", {"conversation_id": p["cid"], "body": p["draft"]})
        ok = not (isinstance(res, dict) and res.get("_error"))
        print("✅ DRAFT salvat (NU trimis)." if ok else "⚠️ create_draft: %s" % res)
    if applied_ok:  # consumă acțiunile ca să nu se reaplice la o a doua rulare --approve
        p["cmd"] = None; p["hide"] = None; p["applied"] = True
        q[str(conv_no)] = p; save_queue(q)


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
    ap.add_argument("--json", action="store_true", help="emite drafturile structurat (audit/integrare) — JSON pe ultima linie după marcajul @@JSON@@")
    ap.add_argument("--close-spam", action="store_true", help="închide (CLOSED) + tag 'spam' tichetele detectate ca spam/notificare automată")
    a = ap.parse_args()
    ALLOWED = set(x.strip().lower() for x in a.actions.split(",") if x.strip() and x.strip().lower() != "none")
    mcp = MCP(secret("RICHPANEL_MCP_TOKEN"))
    COURIER = {"dpd-ro": "DPD", "dpd": "DPD", "sameday": "Sameday", "packeta": "Packeta", "econt": "Econt"}

    if a.approve:
        do_approve(mcp, a.approve, a.agent); return

    picked, page, seen = [], 1, set()
    while len(picked) < a.limit and len(seen) < a.scan:
        args = {"status": "open", "page": page, "per_page": 50, "sortKey": "last_message_at", "order": "desc"}
        if a.channel: args["channel"] = a.channel
        r = mcp.call("list_conversations", args)
        batch = (r.get("tickets") or r.get("conversations") or r.get("results") or []) if isinstance(r, dict) else []
        if not batch: break
        for t in batch:
            cid = t.get("id") or t.get("conversation_no")
            if cid in seen: continue
            seen.add(cid)
            if (t.get("last_message_sender_type") or "").lower() != "customer": continue
            picked.append(t)
            if len(picked) >= a.limit: break
        if not (isinstance(r, dict) and r.get("has_more")): break
        page += 1

    head = "APLICAT — drafturi + rutare escaladare scrise (acțiuni NU)" if a.create_draft else "DRY-RUN — nimic scris"
    print("═" * 92)
    print("  CS AUTO-DRAFT  |  %s  |  %d tichete care așteaptă răspuns" % (head, len(picked)))
    print("═" * 92)
    queue = load_queue()
    rows = []
    n_spam = 0

    for i, t in enumerate(picked, 1):
        no = t.get("conversation_no"); cid = t.get("id")
        channel = (t.get("channel") or "").lower()
        plat_label, plat_rule = PLATFORM.get(channel, (channel or "necunoscut", "ton prietenos, la obiect."))
        is_public = channel in ("facebook_feed_comment", "instagram_comment")
        cust = t.get("customer") or {}
        name = cust.get("name") or ""
        email = (cust.get("email") or (t.get("from") or {}).get("email") or "").lower()
        raw_phone = str(cust.get("phone") or "")
        phone = raw_phone if (raw_phone.isdigit() and 9 <= len(raw_phone) <= 13) else ""
        subj = t.get("subject") or ""; first = t.get("first_message") or ""
        blob = subj + " " + first

        if (t.get("comment_count") or 1) <= 1 and is_public:
            tr = "- [CLIENT] " + " ".join(first.split())[:400]
        else:
            cv = mcp.call("get_conversation", {"conversation_number": str(no), "mode": "audit", "max_messages": 20})
            msgs = (cv.get("messages_page") or {}).get("messages") or cv.get("messages") or []
            lines = []
            for m in msgs[-12:]:
                if m.get("is_private"): continue
                txt = " ".join((m.get("text") or "").split())
                if not txt: continue
                who = "[AI]" if m.get("is_ai") else ("[AGENT]" if m.get("author_is_workspace_agent") else "[CLIENT]")
                lines.append("- %s %s" % (who, txt[:400]))
            tr = "\n".join(lines) or ("- [CLIENT] " + " ".join(first.split())[:400])

        sent_lab, sent_int = sentiment(blob + " " + tr)
        store_name = PAGE_STORE.get(((t.get("to") or {}).get("id") if isinstance(t.get("to"), dict) else None) or "") or "magazinul nostru"

        orders, other = [], []
        elsewhere = "necunoscut (fără email/telefon real — pe comentarii publice nu se poate lega)"
        if email or phone:
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
                mcp.call("add_tags_to_conversation", {"conversation_id": cid, "tags": ["spam"]})
                res = mcp.call("update_conversation_status", {"conversation_id": cid, "status": "CLOSED"})
                ok = not (isinstance(res, dict) and res.get("_error"))
                print("  🗑️ Închis + tag spam." if ok else "  ⚠️ close: %s" % res)
            else:
                print("  → ar închide+arhiva (rulează cu --close-spam ca să le închizi).")
            rows.append({"no": no, "store": store_name, "channel": channel, "cat": cat, "spam": True, "draft": "", "private_msg": ""})
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

        # ---- 2.5) Moderare comentariu public FB/IG (depinde de brand+produs): hide spam / privat la reclamații / public+privat la presale ----
        hide_obj = None
        cact = idn.get("comment_action") or "none"
        page_id = ((t.get("to") or {}).get("id") if isinstance(t.get("to"), dict) else "") or ""
        PRIV_STYLE = "se trimite PRIVAT (nu public) → empatic, preia problema, cere nr comenzii + detalii ca să rezolvi; NU mai spune „scrie-ne în privat” (deja ești în privat)."
        if is_public and cact == "hide":
            hide_obj = {"comment_id": cid, "page_id": page_id, "mode": "hide"}
            proposal_line = (proposal_line + "\n  " if proposal_line else "") + "🙈 PROPUNERE HIDE (spam/abuz) — ascunde comentariul; aprobă cu --approve %s" % no
        elif is_public and cact == "private_reply":
            hide_obj = {"comment_id": cid, "page_id": page_id, "mode": "private_reply"}
            proposal_line = (proposal_line + "\n  " if proposal_line else "") + "📩 PROPUNERE PRIVATE REPLY — contactează clientul în PRIVAT (DM), nu public; aprobă cu --approve %s" % no
            plat_label, plat_rule = "Mesaj PRIVAT (DM) către client", PRIV_STYLE   # draftul = mesajul privat
        elif is_public and cact == "public_and_private":
            hide_obj = {"comment_id": cid, "page_id": page_id, "mode": "public_and_private"}
            proposal_line = (proposal_line + "\n  " if proposal_line else "") + "📣+📩 PROPUNERE PUBLIC + PRIVAT — comentariu public SCURT + DM detaliat; aprobă cu --approve %s" % no
            plat_label, plat_rule = "Comentariu public (SCURT, politicos, PLURAL)", "PUBLIC, politicos, la PLURAL (dumneavoastra/va), 1-2 fraze, FARA date personale. Reclamatie -> scuze scurte + 'v-am scris in privat sa rezolvam'. Intrebare -> raspuns scurt/general + 'v-am trimis detalii in privat'. Gramatica corecta: 'v-am scris' / 'ti-am scris', NICIODATA 'te-am scris'. Mesajul detaliat merge SEPARAT in DM."

        # ---- 3) DRAFT ----
        sys_prompt = HOLDING if is_esc else SYSTEM
        # pe canale PUBLICE redactăm datele personale STRUCTURAL (nu doar prin prompt)
        is_dm = (hide_obj or {}).get("mode") == "private_reply"   # canal PRIVAT → datele clientului pot fi folosite în DM
        od_ctx = od if (not is_public or is_dm) else "(comenzi ascunse — canal public, NU expune date personale)"
        phone_ctx = (phone or "—") if (not is_public or is_dm) else "—"
        email_ctx = (email or "—") if (not is_public or is_dm) else "—"
        learned = LEARNED.get(cat, "")
        learned_blk = ("\nPROCEDURA INVATATA + VOCEA AGENTILOR REALI pt '%s' (urmeaza procedura; imita tonul/structura replicilor; NU copia datele din exemple):\n%s\n" % (cat, learned[:1800])) if learned else ""
        # limba = ce a scris CLIENTUL (detecție pe text) → apoi piața brandului → apoi LLM → ro
        lang = detect_lang(blob + " " + tr) or STORE_LANG.get(store_name) or idn.get("language") or "ro"
        ctx = ("PLATFORMĂ: %s — STIL: %s\nMAGAZIN/BRAND: %s\nCLIENT: %s | email=%s | tel=%s\n"
               "PROBLEMA IDENTIFICATĂ: %s\nPRODUS: %s\nCATEGORIE: %s | LIMBA: %s | SENTIMENT: %s/%s%s\n%s\n\n"
               "CONVERSAȚIA:\n%s\n\nCOMENZILE CLIENTULUI:\n%s\n\nA MAI SCRIS PE: %s\nALTE TICHETE:\n%s\n%s\n"
               "SCRIE ÎN LIMBA ÎN CARE A SCRIS CLIENTUL în conversația de mai sus (orientativ: limba≈%s; ro/cz/pl/bg/en). Brandurile pe Cehia/Polonia/Bulgaria (Bonhaus CZ/PL/BG) răspund de regulă în limba pieței, DAR dacă clientul a scris clar în altă limbă (ex. engleză), răspunde în limba LUI. Exemplele de procedură/voce pot fi în română — folosește-le DOAR pentru pași+ton, NU pentru limbă. Răspunsul depinde de brand+produs. Scrie DOAR textul răspunsului, respectând stilul platformei." % (
                   plat_label, plat_rule, store_name, name or "?", email_ctx, phone_ctx,
                   idn.get("problem") or "(neclar)", idn.get("product") or "—", cat, lang, sent_lab, sent_int,
                   "  [ESCALAT — doar mesaj de așteptare]" if is_esc else "",
                   ("\n" + action_note) if action_note else "", tr, od_ctx, elsewhere, hist_txt, learned_blk, lang))
        try:
            draft, engine = llm(sys_prompt, ctx)
        except Exception as e:
            draft, engine = "(eroare LLM: %s)" % e, "—"
        # presale public → și un MESAJ PRIVAT (DM) detaliat, pe lângă răspunsul public scurt
        if hide_obj and hide_obj.get("mode") == "public_and_private":
            try:
                # DM = canal privat (poate folosi datele reale). Context DEDICAT, ca să nu repete răspunsul public.
                priv_ctx = ("Scrie un MESAJ PRIVAT (DM) către client (canal privat — poți folosi datele lui).\n"
                            "MAGAZIN/BRAND: %s | CLIENT: %s | tel: %s | email: %s\n"
                            "PROBLEMA: %s | PRODUS: %s | CATEGORIE: %s\nCOMENZILE CLIENTULUI:\n%s\n\n"
                            "MESAJUL CLIENTULUI (din comentariu):\n%s\n%s\n"
                            "Empatic, răspunde concret (ține cont de brand+produs), cere produsul/nr comandă/detaliile lipsă ca să rezolvi. "
                            "SCRIE ÎN LIMBA ÎN CARE A SCRIS CLIENTUL (orientativ: %s; dacă a scris în engleză/altă limbă, răspunde în limba lui). "
                            "Scrie DOAR mesajul privat — NU relua răspunsul public și fără separatoare ('---')." % (
                                store_name, name or "?", phone or "—", email or "—",
                                idn.get("problem") or "?", idn.get("product") or "—", cat, od, tr, learned_blk, lang))
                hide_obj["private_msg"] = llm(SYSTEM, priv_ctx)[0].strip()
            except Exception:
                hide_obj["private_msg"] = ""

        print("\n" + "─" * 92)
        cmoji = {"hide": "🙈", "private_reply": "📩", "public_and_private": "📣📩"}.get((hide_obj or {}).get("mode"), "") if hide_obj else ""
        flag = ("⛳%s " % level if is_esc else "") + ("🔧" if cmd else "") + cmoji
        print("  [%d/%d] #%s · %s · %s · %s · sent=%s/%s %s" % (i, len(picked), no, store_name, plat_label, cat, sent_lab, sent_int, flag))
        print("  client: %s | comenzi: %d | a mai scris: %s" % (name or "?", len(orders), elsewhere))
        print("  problemă: %s" % (idn.get("problem") or " ".join((first or subj).split())[:80]))
        if proposal_line:
            for ln in proposal_line.splitlines(): print("  " + ln)
        print("  ┌─ DRAFT%s%s (%s) " % (" PUBLIC" if (hide_obj or {}).get("mode") == "public_and_private" else "", " AȘTEPTARE" if is_esc else "", engine) + "─" * 12)
        for ln in draft.strip().splitlines(): print("  │ " + ln)
        print("  └" + "─" * 42)
        if hide_obj and hide_obj.get("mode") == "public_and_private" and hide_obj.get("private_msg"):
            print("  ┌─ + MESAJ PRIVAT (DM, detalii) " + "─" * 8)
            for ln in hide_obj["private_msg"].splitlines(): print("  │ " + ln)
            print("  └" + "─" * 42)

        queue[str(no)] = {"cid": cid, "draft": draft.strip(), "cmd": cmd, "hide": hide_obj,
                          "ctx": ctx, "action_desc": action_desc, "order": order_name,
                          "store": store_name, "cat": cat, "escalate": is_esc}
        rows.append({"no": no, "store": store_name, "channel": channel, "cat": cat, "escalate": is_esc,
                     "language": lang, "cust_msg": " ".join((first or subj or tr or "").split())[:240],
                     "comment_action": (hide_obj or {}).get("mode"), "draft": draft.strip(),
                     "private_msg": (hide_obj or {}).get("private_msg", "")})
        if cmd or hide_obj:
            print("  → aprobă:  uv run cs_auto_draft.py --approve %s%s" % (no, " --agent <Nume>" if cmd else ""))

        # ---- scrieri în Richpanel (doar cu --create-draft) ----
        if a.create_draft and cid:
            if is_esc:
                tags = ["escaladare", "esc:%s" % level.lower()] + (["de-sunat"] if phone else [])
                note = escalation_note(level, idn.get("escalation_reason") or "", idn.get("problem") or "", name, phone, email,
                                       target_line, elsewhere, "%s/%s" % (sent_lab, sent_int), idn.get("suggested_action"))
                mcp.call("update_conversation", {"conversation_id": cid, "priority": "HIGH"})
                mcp.call("add_tags_to_conversation", {"conversation_id": cid, "tags": tags})
                mcp.call("add_private_note", {"conversation_id": cid, "body": note})
                print("  ⛳ Rutat: HIGH + %s + notă-brief." % ("+".join(tags)))
            # la private_reply (DM-only) și hide NU salvăm draft pe comentariul public — se aplică la --approve
            if (hide_obj or {}).get("mode") in ("private_reply", "hide"):
                print("  (mode %s → fără draft public; se aplică la --approve)" % hide_obj.get("mode"))
            else:
                res = mcp.call("create_draft", {"conversation_id": cid, "body": draft.strip()})
                ok = not (isinstance(res, dict) and res.get("_error"))
                print("  ✅ DRAFT salvat (NU trimis)." if ok else "  ⚠️ create_draft: %s" % res)
        time.sleep(0.2)

    save_queue(queue)
    print("\n  🚫 Spam/automat: %d %s." % (n_spam, "închise (CLOSED+tag spam)" if a.close_spam else "excluse din draft (rulează --close-spam ca să le închizi)"))
    if a.json:
        print("@@JSON@@" + json.dumps(rows, ensure_ascii=False))
    if not a.create_draft:
        print("\n  → DRY-RUN. --create-draft: salvează drafturile + rutează escaladările (URGENT/tag/notă). Acțiunile rămân propuneri (--approve).")


if __name__ == "__main__":
    main()
