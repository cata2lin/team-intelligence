# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Teste de REGRESIE pentru porțile motorului de draft (`cs_auto_draft.py`).

Rulează complet OFFLINE: fără Richpanel, fără LLM, fără Postgres, pe tichete SINTETICE
(niciun dat real de client). Verifică cele patru defecte de poartă reparate în runda
„porți asimetrice":

  1. poarta de CĂUTARE și poarta de SUPRIMARE folosesc aceeași decizie — un tichet nu mai
     poate rămâne fără draft fără ca cineva să fi căutat comanda lui;
  2. `COMANDA_CATS` conține TOATE categoriile care ating o comandă;
  3. un BOUNCE aterizat în fir nu mai transformă clientul în „expeditor-mașină";
  4. magazinul se recuperează din oglinda PROASPĂTĂ, nu doar din indexul vechi.

  uv run teste_porti_asimetrice.py
"""
import importlib.util, json, os, sys, io, sqlite3, tempfile, contextlib

HERE = os.path.dirname(os.path.abspath(__file__))
MOTOR = os.path.join(HERE, "cs_auto_draft.py")


def incarca():
    spec = importlib.util.spec_from_file_location("motor_test", MOTOR)
    m = importlib.util.module_from_spec(spec)
    sys.modules["motor_test"] = m
    spec.loader.exec_module(m)
    m.secret = lambda k: ("TEST" if k == "RICHPANEL_MCP_TOKEN" else "")
    m.save_queue = lambda q: None      # NU scrie coada pe disc în teste
    m.load_queue = lambda: {}
    return m


BOUNCE = ("Delivery incomplete There was a temporary problem delivering your message to "
          "contact@example.com . Gmail will retry for 21 more hours.")

# Tichete SINTETICE. Adresele sunt pe example.com (rezervat prin RFC 2606), numărul de comandă
# EST999999 nu există (serie inventată). Niciun dat real de client nu are voie să intre în fișierul ăsta.  pii-ok: serie inventata, doar forma identificatorului
TICHETE = {
    # hint (subiect + PRIMUL mesaj) = „altele"; categoria din fir = „anulare" → porțile divergeau
    "900001": {
        "ticket": {"id": "t900001", "conversation_no": "900001", "channel": "email", "status": "OPEN",
                   "to": {"id": "", "email": "contact@esteban.ro"},
                   "customer": {"name": "Client Sintetic", "email": "client.test@example.com", "phone": ""},
                   "tag_names": [], "subject": "O întrebare", "first_message": "Bună ziua, am o întrebare.",
                   "comment_count": 3},
        "messages": [
            {"id": "m1", "text": "Bună ziua, am o întrebare.", "is_private": False, "is_ai": False,
             "author_is_workspace_agent": False, "attachments": []},
            {"id": "m2", "text": "Bună ziua, cu ce vă putem ajuta?", "is_private": False, "is_ai": False,
             "author_is_workspace_agent": True, "attachments": []},
            {"id": "m3", "text": "Vreau sa anulez comanda EST999999, nu mai am nevoie de ea.",  # pii-ok: serie inventata (999999), tichet sintetic
             "is_private": False, "is_ai": False, "author_is_workspace_agent": False, "attachments": []},
        ]},
    # client REAL cu retur, peste al cărui fir a căzut un bounce ca ULTIM mesaj
    "900002": {
        "ticket": {"id": "t900002", "conversation_no": "900002", "channel": "email", "status": "OPEN",
                   "to": {"id": "", "email": "contact@esteban.ro"},
                   "customer": {"name": "Client Sintetic", "email": "client2.test@example.com", "phone": ""},
                   "tag_names": [], "subject": "Retur produs", "first_message": "Bună ziua, aș dori să fac retur.",
                   "comment_count": 3},
        "messages": [
            {"id": "m1", "text": "Bună ziua, aș dori să fac retur la comanda EST999999.", "is_private": False,  # pii-ok: serie inventata (999999), tichet sintetic
             "is_ai": False, "author_is_workspace_agent": False, "attachments": []},
            {"id": "m2", "text": "Care este motivul returului?", "is_private": False, "is_ai": False,
             "author_is_workspace_agent": True, "attachments": []},
            {"id": "m3", "text": BOUNCE, "is_private": False, "is_ai": False,
             "author_is_workspace_agent": False, "attachments": []},
        ]},
    # tichet care e NUMAI bounce (mailer-daemon curat) — trebuie să rămână EXCLUS
    "900003": {
        "ticket": {"id": "t900003", "conversation_no": "900003", "channel": "email", "status": "OPEN",
                   "to": {"id": "", "email": "contact@esteban.ro"},
                   "customer": {"name": "", "email": "client3.test@example.com", "phone": ""},
                   "tag_names": [], "subject": "Delivery incomplete", "first_message": BOUNCE,
                   "comment_count": 2},
        "messages": [
            {"id": "m1", "text": BOUNCE, "is_private": False, "is_ai": False,
             "author_is_workspace_agent": False, "attachments": []},
        ]},
}


class MCPFals:
    """Înlocuiește clasa MCP. Orice SCRIERE ridică excepție — testul nu poate atinge Richpanel."""
    SCRIERI = ("create_draft", "send_message", "update_conversation", "update_conversation_status",
               "add_private_note", "add_tags_to_conversation", "create_tag")

    def call(self, name, args):
        if name in self.SCRIERI:
            raise AssertionError("SCRIERE în Richpanel interzisă în teste: %s" % name)
        if name == "list_tags":
            return {"tags": []}
        if name == "get_conversation":
            return TICHETE.get(str(args.get("conversation_number")), {})
        return {"_error": "nesuportat în teste: %s" % name}


def ruleaza(m, nr, categorie_triaj):
    """Rulează motorul pe UN tichet sintetic. Întoarce (nr apeluri lookup_orders, prompturi, stdout)."""
    m.MCP = lambda tok=None: MCPFals()
    apeluri = []

    def _lookup(email, phone, onames=(), awbs=(), raport=None):
        apeluri.append((email, phone, tuple(onames), tuple(awbs)))
        if raport is not None:
            raport["ok"] = True      # căutarea a REUȘIT, doar că n-a găsit nimic
        return []
    m.lookup_orders = _lookup
    m.customer_ident = lambda *a_, **k_: {}
    prompturi = []

    def llm(system, user, js=False):
        prompturi.append(user)
        if js:
            return json.dumps({"problem": "test", "category": categorie_triaj, "severity": "none",
                               "escalate": False, "escalation_reason": "", "suggested_action": "",
                               "action": "none", "order": "", "comment_action": "none", "product": "",
                               "spam": False, "confidence": 0.9, "missing": []}), "stub"
        return "((DRAFT-STUB))", "stub"
    m.llm = llm
    sys.argv = ["cs_auto_draft.py", "--only", nr, "--lean", "--no-photos", "--sleep", "0", "--json"]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            m.main()
        except SystemExit:
            pass
    return len(apeluri), prompturi, buf.getvalue()


def t1_porti_simetrice():
    """Hint='altele' dar categoria triajului='anulare' → căutarea TREBUIE să se facă oricum."""
    m = incarca()
    assert m.categorize_hint("O întrebare Bună ziua, am o întrebare.") not in m.COMANDA_CATS, \
        "premisa testului: hint-ul NU trebuie să ceară căutare"
    n, _prompturi, out = ruleaza(m, "900001", "anulare")
    assert n >= 1, "poarta de căutare nu s-a aliniat cu cea de suprimare (0 căutări pe un tichet de anulare)"
    assert "SUPRIMAT" not in out or "căutare TÂRZIE" in out, \
        "tichetul a fost suprimat fără să se fi căutat vreodată pentru el"


def t2_comanda_cats_complet():
    m = incarca()
    for c in ("schimb_swap", "refuz_livrare", "plata_factura"):
        assert c in m.COMANDA_CATS, "%s atinge o comandă, dar lipsește din COMANDA_CATS" % c
    # categoriile din RULES care ating o comandă trebuie să existe toate ca reguli reale
    reguli = {k for k, _ in m.RULES}
    assert m.COMANDA_CATS <= reguli, "COMANDA_CATS conține categorii pe care RULES nu le poate produce: %s" % (
        m.COMANDA_CATS - reguli)


def t3_bounce_nu_e_expeditor_masina():
    m = incarca()
    assert m.BOUNCE_BODY_RE.search(BOUNCE), "bounce-ul nu e recunoscut ca atare"
    assert m.expeditor_masina("client2.test@example.com", "Retur produs",
                              "Bună ziua, aș dori să fac retur.", BOUNCE) == "", \
        "bounce-ul din ULTIMUL mesaj transformă din nou clientul în expeditor-mașină"
    # chargeback-ul, care CHIAR e notificare de mașină, rămâne prins
    assert m.expeditor_masina("client.test@example.com", "",
                              "A chargeback response for order 123 was submitted to the bank", "") != "", \
        "notificarea de chargeback nu mai e prinsă"
    n, prompturi, out = ruleaza(m, "900002", "retur")
    assert "EXPEDITOR NON-CLIENT" not in out, "tichetul de client a fost aruncat ca expeditor non-client"
    assert prompturi, "tichetul n-a ajuns niciodată la model"
    assert "problem delivering your message" not in prompturi[-1], \
        "bounce-ul a rămas în transcript și devine mesajul la care răspundem"
    assert "PROBLEME DE LIVRARE" in prompturi[-1] or "retur" in prompturi[-1].lower()


def t5_tichet_doar_bounce_ramane_exclus():
    """Un mailer-daemon curat (fără niciun mesaj de om) NU are voie să primească draft."""
    m = incarca()
    _n, prompturi, out = ruleaza(m, "900003", "altele")
    assert "EXCLUS (fără draft)" in out, "tichetul care e NUMAI bounce a scăpat de gărzi"
    assert not any("DRAFT" in p for p in prompturi[1:]), "s-a cerut un draft pentru un mailer-daemon"


def t4_magazin_din_oglinda_proaspata():
    m = incarca()
    with tempfile.TemporaryDirectory() as d:
        cale = os.path.join(d, "cs_mirror_live.db")
        cx = sqlite3.connect(cale)
        cx.execute("CREATE TABLE rp_ticket (conversation_no INTEGER, store_resolved TEXT)")
        cx.execute("INSERT INTO rp_ticket VALUES (900001, 'Esteban ')")   # cu spațiu la coadă, ca în date reale
        cx.commit(); cx.close()
        vechi = os.environ.get("CS_MIRROR_DB")
        os.environ["CS_MIRROR_DB"] = cale
        try:
            m._IDX_STORE.clear(); m._IDX_ACOPERIRE.clear()
            assert m.store_din_index("900001") == "Esteban", "magazinul nu e citit din oglinda proaspătă"
            m._IDX_STORE.clear()
            assert m.store_din_index("900999") is None, "un tichet absent trebuie să dea None, nu să crape"
            m._IDX_STORE.clear(); m._IDX_ACOPERIRE.clear()
            acop = m.acoperire_magazin()
            assert any(x[2] == 900001 for x in acop), "acoperirea sursei nu e declarată (%s)" % (acop,)
        finally:
            if vechi is None:
                os.environ.pop("CS_MIRROR_DB", None)
            else:
                os.environ["CS_MIRROR_DB"] = vechi


if __name__ == "__main__":
    nereusite = 0
    for f in (t1_porti_simetrice, t2_comanda_cats_complet, t3_bounce_nu_e_expeditor_masina,
              t4_magazin_din_oglinda_proaspata, t5_tichet_doar_bounce_ramane_exclus):
        try:
            f()
            print("✅ %s" % f.__name__)
        except Exception as e:   # și AttributeError: pe motorul NEreparat simbolurile noi lipsesc
            nereusite += 1
            print("❌ %s: %s: %s" % (f.__name__, type(e).__name__, e))
    print("\n%d/%d teste trecute" % (5 - nereusite, 5))
    sys.exit(1 if nereusite else 0)
