# /// script
# requires-python = ">=3.10"
# dependencies = ["pg8000>=1.30"]
# ///
"""
richpanel_auto_triage.py — TRIAJ AUTOMAT pe conversațiile OPEN din Richpanel:
propune TAG magazin + categorie + PRIORITATE (VIP / escaladare ANPC). Azi 99,7% din
conversații n-au niciun tag → tagging-ul ajută enorm la rutare.

⚠️ DRY-RUN implicit (arată ce AR seta, nu scrie nimic). Cu --apply scrie în Richpanel
DOAR taguri + prioritate (operații interne). NICIODATĂ mesaj la client.

  uv run richpanel_auto_triage.py                 # DRY-RUN — ce ar tagui/prioritiza
  uv run richpanel_auto_triage.py --limit 100
  uv run richpanel_auto_triage.py --json
  uv run richpanel_auto_triage.py --apply         # scrie tag+prioritate (niciun mesaj la client)
"""
import os, re, json, subprocess, urllib.parse, urllib.request, argparse, collections
import pg8000.dbapi

HERE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")
MCP_URL = "https://mcp.richpanel.com/mcp"

# pagină FB/IG -> magazin (din memoria fb-page-store-map)
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
# Setul COMPLET de reguli, copiat EXACT din richpanel-export/richpanel_export.py
# (categorie, regex pe subiect+prim_mesaj, lowercase, diacritice scoase). Ordinea contează (primul match câștigă).
RULES = [
    ("spam_automat", r"left a \d star review|left the following|judge\.?me|chargeflow|out of office|automat[ae] reply|do[- ]?not[- ]?reply|weekly tiktok|performance report|newsletter|unsubscribe|password reset|verify your email|ordine.*confermato"),
    ("recenzie_feedback", r"ce parere ai|parerea ta|recenzi|review|feedback|multumesc pentru|sunt multumit"),
    ("retur", r"\bretur|returnez|returna|trimit inapoi|banii inapoi|ramburs(?!.*plata)|refund|vreau banii|\breturn\b|odstoupeni|\bzwrot|\breso\b"),
    ("schimb_swap", r"schimb produs|schimb cu alt|alt model|alta marime|alta culoare|inlocui|exchange|wymiana"),
    ("anulare", r"anulez|anulare|anulati|renunt la comanda|nu mai vreau comanda|cancel|anuluj|zrusit|storno|cancellare"),
    ("modificare_comanda", r"gresit (nr|numarul|adresa)|schimb (nr|numarul|adresa|telefonul)|modific (comanda|adresa|telefonul)|alta adresa|adresa gresita|actualizez|edit my order|change (my|the) order|change.*address|modify.*order|wrong address|update.*address|zmiana zamowienia"),
    ("problema_produs", r"mi s-?au gresit|gresit[ea]? (parfum|produs|marime|culoare|model|comand|articol)|am primit (alt|alte|gresit|gresita|gresite|altceva)|alt[ea]? (parfum|produs|model|culoare) (decat|in loc|fata)|nu (sunt|este|e) (ce|cele|ceea ce) am comandat|nu corespunde cu (ce am comandat|comanda)"),
    ("livrare_wismo", r"unde (e|este|imi)|cand ajunge|coletul|nu a ajuns|nu am primit( inca)?|awb|curier|tracking|livrarea mea|status.*comand|comanda mea.*(ajun|liv)|intarzi|where is my order|how long will it take|when will.*(arrive|receive|get)|track.*(order|my)|delivery status|haven'?t received|aktualizace zasilky|kde je (moje|ma)|kdy dorazi|gdzie (jest|moja)|kiedy.*(dotrze|przesylka)|przesylka|dov.* il mio ordine|quando arriva|spedizione"),
    ("problema_produs", r"defect|stricat|nu functioneaza|lipseste|lipsesc|lipsa (din|produs)|gresit produs|alt produs decat|incomplet|deteriorat|spart|fara (pompita|capac|accesori)|damaged|broken|missing (part|piece)|poskozen|uszkodzon"),
    ("refuz_livrare", r"refuz|nu primesc coletul|nu accept coletul"),
    ("plata_factura", r"factura|plata nu|am platit de doua|card.*(debitat|taxat)|chitanta|bon fiscal|\binvoice|faktur"),
    ("presale_intrebare", r"aveti (pe stoc|in stoc)|este pe stoc|cat costa|ce pret|livrati in|cand revine|dimensiuni|este original|mai aveti|se potriveste|disponibil|how much|what.*price|in stock|do you have|available|jaka cena|na sklad"),
    ("comanda_noua", r"vreau sa comand|as dori sa comand|plasez o comanda|cum comand|doresc sa cumpar|i want to order|how (do i|to) order"),
    ('livrare_wismo', '\\bfirstname:[^\\n]*(colet|comanda (mea|nu)|nu am primit|nu a ajuns)'),
    ('livrare_wismo', 'cat dureaza (pana )?(sa )?(primesc|ajunge|soseste)|suport clienti &gt; livrare|shipping &amp; delivery &gt;'),
    ('presale_intrebare', 'cum se numeste (parfumul|produsul)|ma intereseaza sa( i|i)?l cumpar|do you ship international'),
    ('salut_fara_continut', '^\\s*(chat with us\\s+)?(start a conversation\\s*&gt;\\s*)?chat with us[\\s.!]*$'),
    ('salut_fara_continut', 'suport clienti &gt; discuta cu un specialist[\\s.!]*$'),
    ('salut_fara_continut', '^[\\s.,!]*((buna( ziua| seara)?|salut(are)?|neata)[\\s.,!]*){1,2}(am o (nelamurire|intrebare)[\\s.?!]*)?$'),
    ('formular_contact', 'kapcsolati k[ée]relme|cseveg[ée]s a |aitima( sas)? gia epikoinonia|\\bchat pe \\S'),
    ('formular_contact', '\\bfirstname:(?![^\\n]*(colet|comand|parfum|produs|retur|factur|buna|\\bcum\\b|unde|cand|vreau|primit))[^\\n]{0,30}\\n[\\s\\S]{0,200}(?m:^(lastname|email|phone):)'),
    ('formular_contact', '(?m)^email:\\s*\\S+@\\S+\\s*$[\\s\\S]{0,80}^phone:'),
    ("formular_contact", r"cererea dvs\.? de contact|contact form|chat with us|start a conversation|how can we help|shared files?$"),
]
DEACC = str.maketrans("ăâîșşțţ", "aaissttt"[0:7])
ESCAL = re.compile(r"anpc|protectia consumator|dau in judecat|instanta|avocat|denunt", re.I)


def secret(k):
    return os.environ.get(k) or subprocess.run(["uv", "run", KB, "secret-get", k], capture_output=True, text=True).stdout.strip()


def deacc(s):
    return (s or "").lower().translate(DEACC)


def categorize(subject, first_message="", channel=None):
    txt = deacc((subject or "") + " " + (first_message or ""))
    for cat, pat in RULES:
        if re.search(pat, txt):
            return cat
    if channel in ("facebook_feed_comment", "instagram_comment"):
        return "comentariu_social"
    return "altele"


class MCP:
    def __init__(self, token):
        self.t = token
        self._post({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                    "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "triage", "version": "1"}}})

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
            try:
                return json.loads(txt)
            except Exception:
                return {"_text": txt}
        except Exception as e:
            return {"_error": str(e)}


def vip_set():
    """Telefoane/emailuri cu LTV livrat >=1000 RON (din metrics)."""
    try:
        u = urllib.parse.urlparse(secret("DATABASE_URL_METRICS"))
        c = pg8000.dbapi.connect(ssl_context=True, user=urllib.parse.unquote(u.username or ""), password=urllib.parse.unquote(u.password or ""),
                                 host=u.hostname, port=u.port or 5432, database=(u.path or "/").lstrip("/"))
        cur = c.cursor()
        cur.execute('SELECT lower(email), SUM("totalPrice") FROM orders GROUP BY 1 HAVING SUM("totalPrice")>=1000')
        vips = set(r[0] for r in cur.fetchall() if r[0])
        c.close()
        return vips
    except Exception:
        return set()


def triage(t, vips):
    frm = t.get("from") or {}
    to = t.get("to") or {}
    email = (frm.get("email") or (frm.get("id") if isinstance(frm.get("id"), str) and "@" in (frm.get("id") or "") else "") or "").lower()
    blob = (t.get("subject") or "") + " " + (t.get("first_message") or "")
    # magazin = PAGINA pe care a venit tichetul (`to.id` → PAGE_STORE). NU brandul/`last_message_sender_id`
    # din Richpanel: ăla începe cu org-ul `nocturna954_...` pe ORICE tichet (cont, nu magazin) → ar da fals „Nocturna".
    store = PAGE_STORE.get((to.get("id") if isinstance(to, dict) else None) or "")
    m = ORDER_RE.search(blob)
    if not store and m:
        store = ORDER_PFX.get(m.group(1).upper())
    if not store and "@" in email:
        dom = email.split("@")[-1].split(".")[0]
        store = next((s for p, s in ORDER_PFX.items() if dom in s.lower().replace(" ", "")), None)
    cat = categorize(t.get("subject"), t.get("first_message"), t.get("channel"))
    if ESCAL.search(blob):
        prio, why = "URGENT", "escaladare ANPC/juridic"
    elif email and email in vips:
        prio, why = "HIGH", "client VIP (LTV ≥1000)"
    elif cat in ("retur", "problema_produs", "anulare", "refuz_livrare", "schimb_swap"):
        prio, why = "HIGH", cat
    else:
        prio, why = "NORMAL", cat
    return {"no": t.get("conversation_no"), "id": t.get("id"), "store": store or "necunoscut",
            "cat": cat, "prio": prio, "why": why, "subj": " ".join(blob.split())[:50]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=80); ap.add_argument("--apply", action="store_true"); ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    mcp = MCP(secret("RICHPANEL_MCP_TOKEN"))
    vips = vip_set()
    convs, page, seen = [], 1, set()
    while len(convs) < a.limit:
        r = mcp.call("list_conversations", {"status": "open", "page": page, "per_page": 50})
        batch = (r.get("conversations") or r.get("tickets") or r.get("results") or []) if isinstance(r, dict) else []
        if not batch:
            break
        for t in batch:
            cid = t.get("id") or t.get("conversation_no")
            if cid not in seen:
                seen.add(cid); convs.append(t)
        page += 1
        if page > 6:
            break
    rows = [triage(t, vips) for t in convs[:a.limit]]
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1, default=str)); return
    head = "APLICAT (scris în Richpanel)" if a.apply else "DRY-RUN (nu am scris nimic)"
    print("═" * 84)
    print("  RICHPANEL AUTO-TRIAGE — %s  |  %d conversații OPEN" % (head, len(rows)))
    print("  Distribuție prioritate: " + " · ".join("%s=%d" % (k, v) for k, v in collections.Counter(x["prio"] for x in rows).most_common()))
    print("═" * 84)
    pr_rank = {"URGENT": 0, "HIGH": 1, "NORMAL": 2}
    for x in sorted(rows, key=lambda z: pr_rank.get(z["prio"], 3)):
        print("  #%-7s %-9s %-14s %-16s %s" % (x["no"] or "?", x["prio"], x["store"][:14], x["cat"][:16], x["subj"]))
        if a.apply and x["id"]:
            tags = [t for t in ["magazin:" + x["store"], "cat:" + x["cat"]] if "necunoscut" not in t]
            mcp.call("add_tags_to_conversation", {"conversation_id": x["id"], "tags": tags})
            if x["prio"] in ("URGENT", "HIGH"):
                mcp.call("update_conversation", {"conversation_id": x["id"], "priority": x["prio"]})
    if not a.apply:
        print("\n  → rulează cu --apply ca să scrie tag+prioritate (niciun mesaj la client).")


if __name__ == "__main__":
    main()
