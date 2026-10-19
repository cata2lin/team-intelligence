"""
richpanel_schema.py — UN SINGUR loc care știe cum arată un tichet Richpanel (vechi ȘI nou).

De ce există (măsurat 19-oct-2026): de pe ~5-oct-2026 17:00 MCP-ul Richpanel întoarce sumarul
conversației cu altă schemă: `last_message_from` în loc de `last_message_sender_type`, fără
`to` (pagina FB/IG sau cutia de e-mail pe care a venit tichetul), iar `brand_id` e „nocturna9540"
pe 97 din 100 de tichete (contul, nu magazinul). Fiecare unealtă își citea singură `to.id` →
PAGE_STORE și cădea TĂCUT pe „necunoscut": oglinda CS a scăzut de la ~73% tichete cu magazin la
~12%, iar botul a aruncat toate tichetele.

Aici: maparea pagină → magazin, rezolvarea magazinului cu toate fallback-urile, normalizarea
expeditorului și verificarea răspunsului MCP (eroare = EXCEPȚIE, nu listă goală).
Fără dependențe (oglinda rulează zero-dependency) — se importă din orice skill cu:

    sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "core", "scripts"))
    import richpanel_schema as rps
"""
import re

# pagină FB/IG → magazin. Superset al hărților din auto-triage + cs_auto_draft (memoria
# fb-page-store-map). Pagină nouă → adaug-o AICI, nu într-o copie locală.
PAGE_STORE = {
    "426248277236834": "Esteban", "364899953373966": "Ofertele Zilei", "775068272350568": "Magdeal",
    "569610726226886": "Reduceri bune", "676105508924341": "George Talent", "568416516348894": "Belasil",
    "582569158278392": "Nubra", "700342149818211": "Bonhaus PL", "629666993566339": "Grandia",
    "434151126459295": "Bonhaus CZ", "651700798017858": "Casa Ofertelor", "582681401604162": "Ofertele Zilei",
    "1678573069021466": "Orasul Verde", "522811567592063": "Gento", "621560724373069": "Carpetto",
    "680369271815957": "Bonhaus BG", "421367954403103": "Apreciat", "1805415543098993": "Rossi Nails",
    "61586834387211": "Lab Noir",
    "814175968452902": "Duppo BG", "516792924847762": "Bonhaus SK", "425006144024872": "Bonhaus HU",
    "622653260933284": "Duppo Hungary", "617080764812731": "Duppo Czechia", "575422458989566": "Duppo Moldova",
    "103675612509107": "Bonhaus", "425122607349368": "Nocturna Lux", "575484808985734": "Covoria",
    "628544790345906": "Genti Promo", "704271086093669": "Ce-Pat-Ai", "506398435900401": "Produse Bisericesti",
    "132189989971450": "Stemma", "606625622532373": "Super Detergent", "104553898590313": "Unelte Potrivite",
    "115983611500696": "Manscout", "122095975544011424": "Rossi Nails",
    # identificate 19-oct-2026 (titlul public al paginii + conținutul tichetelor din oglindă):
    "984329328095386": "De la Bucsa", "898588036681214": "Lab Noir",
    "336777842857489": "Nocturna", "995674783622734": "Orice Redus",
}

# cutia de e-mail / domeniul menționat → magazin
STORE_BY_EMAIL = {
    "esteban.ro": "Esteban", "george-talent.ro": "George Talent", "nubra.ro": "Nubra",
    "grandia.ro": "Grandia", "magdeal.ro": "Magdeal", "ofertelezilei.ro": "Ofertele Zilei", "reduceri-bune.ro": "Reduceri bune",
    "reduceribune.ro": "Reduceri bune", "bonhaus.ro": "Bonhaus RO", "bonhaus.cz": "Bonhaus CZ",
    "bonhaus.pl": "Bonhaus PL", "bonhaus.bg": "Bonhaus BG", "bonhaus.sk": "Bonhaus SK", "bonhaus.hu": "Bonhaus HU",
    "belasil.ro": "Belasil", "gento.ro": "Gento", "carpetto.ro": "Carpetto", "covoria.ro": "Covoria",
    "rossinails.ro": "Rossi Nails", "apreciat.ro": "Apreciat", "labnoir.ro": "Lab Noir",
    "casaofertelor.ro": "Casa Ofertelor", "duppo.md": "Duppo Moldova", "delabucsa.ro": "De la Bucsa",
    # Duppo EU (un singur Shopify multi-piață din 22-sep-2026: BG+CZ+HU+SK); cutiile duppo.eu sunt
    # aliasuri ale aceleiași căsuțe → toate = magazinul „Duppo BG" (piața o dă limba clientului)
    "duppo.eu": "Duppo BG", "duppo.bg": "Duppo BG", "duppo.cz": "Duppo Czechia", "duppo.hu": "Duppo HU",
}
# inboxuri PARTAJATE (primesc tichete pt mai multe magazine) — NU identifică un magazin
# (aceeași regulă ca în richpanel_link.SHARED_INBOX)
SHARED_INBOX = {"nocturna.ro", "trynocturna.eu", "nocturna.bg", "nocturna.pl", "nocturna.hu"}
# „bulgaria@duppo.eu" — cutia, nu domeniul, spune magazinul
MAILBOX_STORE = {"bulgaria@duppo.eu": "Duppo BG"}

ORDER_PFX = {"EST": "Esteban", "GT": "George Talent", "NUB": "Nubra", "GEN": "Gento", "GRAN": "Grandia",
             "GRAND": "Grandia", "BELA": "Belasil", "MAG": "Magdeal", "OFER": "Ofertele Zilei",
             "RED": "Reduceri bune", "BON": "Bonhaus RO", "BONBG": "Bonhaus BG", "CZ": "Bonhaus CZ",
             "PL": "Bonhaus PL", "CARP": "Carpetto", "COV": "Covoria", "NOC": "Nocturna", "APR": "Apreciat",
             "ROSSI": "Rossi Nails", "LUX": "Nocturna Lux", "DUPBG": "Duppo BG",
             # piețe străine (prefixele din cs_auto_draft): DUP = Duppo EU după 22-sep-2026
             "DUP": "Duppo BG", "MD": "Duppo Moldova", "SK": "Bonhaus SK", "HU": "Bonhaus HU"}
ORDER_RE = re.compile(r"\b(" + "|".join(sorted(ORDER_PFX, key=len, reverse=True)) + r")[ -]?(\d{4,7})\b", re.I)
_DOMAIN_RE = re.compile(r"\b(" + "|".join(re.escape(d) for d in sorted(STORE_BY_EMAIL, key=len, reverse=True))
                        + r")\b", re.I)

# brand_id Richpanel → magazin. GOL INTENȚIONAT: măsurat 19-oct-2026 pe 100 de tichete OPEN,
# brand_id e „nocturna9540" (97) / „nocturna95414" (3) — contul, nu magazinul; list_brands
# răspunde AUTH_UNAVAILABLE. Dacă Richpanel începe să trimită branduri reale, mapează-le aici.
BRAND_STORE = {}

_SENDER_ALIASES = {"operator": "agent", "user": "agent", "bot": "agent", "ai": "agent",
                   "client": "customer", "contact": "customer"}


class RichpanelError(RuntimeError):
    """Richpanel MCP a răspuns cu eroare. NU o transforma în „0 tichete"."""


def check(r, tool=""):
    """Ridică RichpanelError pe orice formă de eroare: `{"_error"}` (clientul local),
    `success: false` (503 AUTH_UNAVAILABLE etc.), `isError`, text „Error: …"."""
    if r is None:
        raise RichpanelError("%s: răspuns gol de la Richpanel" % tool)
    if isinstance(r, dict):
        err = r.get("_error")
        if not err and r.get("success") is False:
            err = "%s %s" % (r.get("code") or "success=false", r.get("error") or "")
        if r.get("isError"):
            err = err or r.get("_text") or "isError"
        if not err and r.get("jsonrpc") and r.get("error"):  # plicul JSON-RPC brut
            err = r["error"]
        res = r.get("result") if isinstance(r.get("result"), dict) else {}
        if not err and res.get("isError"):
            err = str((res.get("content") or [{}])[0].get("text") or "isError")
        txt = r.get("_text")
        if not err and isinstance(txt, str) and txt.lstrip().lower().startswith("error"):
            err = txt
        if err:
            raise RichpanelError("%s: %s" % (tool or "richpanel", str(err)[:300]))
    return r


def tickets(r, tool="list_conversations"):
    """Lista de tichete dintr-un răspuns list_* — după check(). Cheie necunoscută = eroare."""
    check(r, tool)
    if isinstance(r, list):
        return r
    for k in ("tickets", "conversations", "results", "data"):
        v = r.get(k)
        if isinstance(v, list):
            return v
        if isinstance(v, dict) and isinstance(v.get("tickets"), list):
            return v["tickets"]
    raise RichpanelError("%s: schemă necunoscută, chei=%s" % (tool, sorted(r)[:12]))


def ticket_of(r, tool="get_conversation"):
    """Sumarul tichetului dintr-un get_conversation (schema nouă îl pune sub `ticket`)."""
    check(r, tool)
    t = r.get("ticket") if isinstance(r.get("ticket"), dict) else None
    return t or r.get("conversation") or r


def sender(t):
    """Cine a scris ULTIMUL: 'customer' | 'agent' | '' — din `last_message_from` (nou) sau
    `last_message_sender_type` (vechi; „operator" = agent)."""
    v = (t.get("last_message_from") or t.get("last_message_sender_type") or "")
    if isinstance(v, dict):
        v = v.get("type") or v.get("role") or ""
    v = str(v).strip().lower()
    return _SENDER_ALIASES.get(v, v)


def page_id(t):
    """Pagina FB/IG a tichetului: `to.id` (schema veche) sau, la comentariile FB, primul segment
    al id-ului conversației („<page>_<post>_<post>_<comment>"; măsurat: = to.id pe 93% din
    5.942 comentarii vechi). Segmentele de 17+ cifre sunt id-uri de postare/media, nu pagini."""
    to = t.get("to") if isinstance(t.get("to"), dict) else {}
    if to.get("id") and "@" not in str(to.get("id")):
        return str(to["id"])
    cid = str(t.get("id") or "")
    if "_" in cid:
        head = cid.split("_", 1)[0]
        if head in PAGE_STORE or (head.isdigit() and len(head) <= 16):
            return head
    return ""


def to_email(t):
    to = t.get("to") if isinstance(t.get("to"), dict) else {}
    return str(to.get("email") or (to.get("id") if "@" in str(to.get("id") or "") else "") or "").lower()


def store_from_email(addr):
    addr = (addr or "").lower()
    if not addr:
        return None
    if addr in MAILBOX_STORE:
        return MAILBOX_STORE[addr]
    for dom, name in STORE_BY_EMAIL.items():
        if addr.endswith("@" + dom) or addr.endswith("." + dom) or addr == dom:
            return name
    return None


def store_from_text(text):
    """Prefixul comenzii (EST123…), apoi un domeniu de magazin citat în text."""
    text = text or ""
    m = ORDER_RE.search(text)
    if m:
        return ORDER_PFX.get(m.group(1).upper())
    m = _DOMAIN_RE.search(text)
    if m:
        return STORE_BY_EMAIL.get(m.group(1).lower())
    return None


def resolve_store(t, extra_text="", mailbox=None):
    """Magazinul tichetului. Ordinea: pagina (to.id / prefixul id-ului) → cutia de e-mail
    (to.email sau `mailbox` dat de apelant, ex. din gm_message) → brand_id mapat → prefixul de
    comandă / domeniul din subiect+prim mesaj(+extra). NU numele/brandul afișat în Richpanel."""
    s = PAGE_STORE.get(page_id(t))
    if s:
        return s
    s = store_from_email(to_email(t)) or store_from_email(mailbox)
    if s:
        return s
    s = BRAND_STORE.get(str(t.get("brand_id") or ""))
    if s:
        return s
    return store_from_text(" ".join(str(x or "") for x in (t.get("subject"), t.get("first_message"), extra_text)))


def conv_args(ref, **kw):
    """Argumentele get_conversation (schema nouă: `id` = id, nr. tichet sau URL)."""
    d = {"id": str(ref)}
    d.update(kw)
    return d


if __name__ == "__main__":  # autotest, fără rețea
    new = {"id": "775068272350568_1221_1221_1652", "channel": "facebook_feed_comment",
           "brand_id": "nocturna9540", "last_message_from": "customer", "subject": "Super"}
    old = {"id": "x", "to": {"id": "426248277236834"}, "last_message_sender_type": "operator"}
    mail = {"id": "<a@b>", "channel": "email", "subject": "Re: Comanda EST123456 a fost confirmata"}
    post = {"id": "122182139462669812_15249_122182_1441", "channel": "facebook_feed_comment"}
    assert resolve_store(new) == "Magdeal" and sender(new) == "customer"
    assert resolve_store(old) == "Esteban" and sender(old) == "agent"
    assert resolve_store(mail) == "Esteban"
    assert resolve_store({"id": "<c@d>", "first_message": "scris de contact@nubra.ro"}) == "Nubra"
    assert resolve_store({"id": "<c@d>"}, mailbox="bulgaria@duppo.eu") == "Duppo BG"
    assert page_id(post) == "" and resolve_store(post) is None
    assert resolve_store({"id": "984329328095386_122139264543229860_1_2"}) == "De la Bucsa"
    assert resolve_store({"id": "<e@f>", "subject": "Поръчка DUP12345"}) == "Duppo BG"
    assert resolve_store({"id": "<e@f>", "subject": "Comanda MD1145"}) == "Duppo Moldova"
    assert resolve_store({"id": "<e@f>", "first_message": "pisu na contact@duppo.cz"}) == "Duppo Czechia"
    for bad in ({"_error": "timeout"}, {"success": False, "error": "x", "code": "AUTH_UNAVAILABLE"},
                {"_text": "Error: timezone is required"}):
        try:
            tickets(bad)
            raise SystemExit("check() a lăsat să treacă: %s" % bad)
        except RichpanelError:
            pass
    assert tickets({"tickets": [1], "page": 1}) == [1]
    print("richpanel_schema: OK")
