# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Test de contract: ce folosesc scripturile de pe VPS din `xconnector.py` (reconciliere v3.1, 5-oct-2026).

Pe VPS, checkout-ul team-intelligence a rulat săptămâni întregi o copie MODIFICATĂ LOCAL a xconnector.py („v3.1",
capture fără AWBprint, registrul facturilor, oprirea la SmartBill fără credite, 5 buc/colet...). Copia din git nu avea
acele funcții, deci un `git pull` / `checkout` pe VPS ar fi rupt cronurile și scripturile care le importă (cu
AttributeError la prima rulare, de obicei duminică noaptea, la facturare). Testul fixează contractul:
  1. fiecare atribut pe care îl importă un script de pe VPS există în modul (lista: grep pe /root/Scripturi,
     4-oct-2026 — `import xconnector as X` + `X.<atribut>`, plus cele cerute de runbook-ul de cutover);
  2. funcțiile aduse din v3.1 se comportă ca pe VPS (fără rețea: doar date în memorie și un fișier temporar);
  3. repo-ul e PUBLIC: lista facturilor OH manuale rămâne goală în git;
  4. prin main(), pe dublurile din test_garda_oh: plasele v3.1 din inv-bulk (registru, listă externă, 410,
     răspuns neconfirmat, registru care nu se scrie) DUPĂ garda OH și refuzul de pe stație; capture fără AWBprint;
  5. tokenurile: load_shopify_tokens emite marcajele OAUTH (VPS), garda pe o comandă (și _oh_shopify din #611,
     dacă există) emite doar pt magazinul ei, iar o emitere eșuată / KB inaccesibil nu se reia la fiecare apel;
     după merge-ul cu #611: secretele din KB, altfel din env (și cu KB inaccesibil), garda gazdei înaintea lor.
Fiecare plasă din 4-5 a fost scoasă pe rând (mutație) și testul a picat de fiecare dată.
Importul se face fără env și fără rețea (exact ca `python3 -c 'import xconnector'` din cronuri).

  uv run test_contract_vps.py
"""
import datetime
import os
import sys
import tempfile

AICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AICI)

import xconnector as X  # noqa: E402

FAILS = []


def check(nume, ok, extra=""):
    print(("  ok   " if ok else "  PICAT ") + nume + (("  — " + extra) if extra else ""))
    if not ok:
        FAILS.append(nume)


# Cerute explicit de runbook-ul de cutover (pasul 2/3/5): cronurile de facturare / capture / parcel_watch.
RUNBOOK = """
_default_box _prefix_potrivit decizie_capture registru_citeste registru_scrie _smartbill_fara_credite
shopify_pending_detaliat load_shops XC awb_doc doc_tracking order_parcel_count load_shopify_tokens shopify_gql
_stores_csv_tokens awbprint_status shopify_order_cancel
""".split()

# Atributele citite de scripturile de pe VPS (/root/Scripturi/*.py, */*.py, plugin-urile din checkout și
# shared/scripturi-tools) — grep AST pe `import xconnector as <alias>` + `<alias>.<atribut>`, 4-oct-2026.
VPS_CONSUMATORI = """
AWB_EVENT_LOG HERE_COUNTRY HERE_MIN_SCORE PLECATA RO_DPD_LOCALITY_ALIAS XBASE XC _ST_MARK _awbprint_customer_email
_bl_addr _bl_phone _city_denoise _create_label _default_box _delabel _dpd_auth _dpd_creds _dpd_state _expand_fullname
_expand_street_abbrev _expand_street_initial _fold _intl_phone _maybe_swap_fields _prefix_potrivit _pull_artery_prefix
_pull_block_details _pull_landmark _resolve_target_shops _stores_csv_tokens _street_deglue _street_from_a2 _street_tail
_strip_loc_prefix awb_doc awbprint_batch awbprint_status correct_address doc_tracking domain_for_order dpd_site_by_city
find_order has_awb here_geocode here_key here_street_ok here_validate here_zip_fill http load_blocklist load_here_ok
load_shopify_tokens load_shops metrics_cursor metrics_cursor_live nomenclator_correct order_parcel_count pick_connector
resolve_order resolve_parcels route_connector shopify_add_tags shopify_gql shopify_mark_paid shopify_order_cancel
zip_confirm
""".split()

# Restul codului v3.1 de pe VPS (facturare + capture) — folosit intern de cmd_inv_bulk / cmd_capture.
V31_INTERN = """
facturate_extern _registru_motiv _termen_inv _raspuns_nesigur _eroare_completa _text_fara_diacritice _cod_dpd
_ore_intors_final DPD_INTORS_FINAL DPD_IN_CURS DPD_NESIGUR _FF_MAX _TRK_MAX _DPD_CIRCUIT DPD_LOTURI_MOARTE_MAX
REGISTRU_MOTIVE NESIGURE_MAX DEFAULT_BOX SHOPS_FARA_COLETE_MULTIPLE shopify_remove_tags shopify_pending_orders
"""

# Ce are main și NU avea copia de pe VPS: garda OH pe facturare și tokenurile Shopify reemise.
MAIN = """
_refuz_inv_bulk_apply _pe_vps_facturare VPS_FACTURARE_ENV garda_oh_comanda _garda_oh_refuza _shop_accepta
_tokenuri_statice _token_viu_sau_emis _mint_din_marcaj grandia_pe_dragon _order_lines OH_GUARD_TOKEN
""".split()


def t_atribute():
    print("1. atributele importate de pe VPS există")
    for grup, nume in (("runbook", RUNBOOK), ("consumatori VPS", VPS_CONSUMATORI),
                       ("v3.1 intern", V31_INTERN.split()), ("main", MAIN)):
        lipsa = [n for n in dict.fromkeys(nume) if not hasattr(X, n)]
        check("%s: %d atribute" % (grup, len(set(nume))), not lipsa, "lipsesc: %s" % lipsa)
    check("_stores_csv_tokens() se poate apela fără argumente (cod_paid_watch.py)",
          X._stores_csv_tokens.__code__.co_argcount == len(X._stores_csv_tokens.__defaults__ or ()))
    check("XC.post acceptă err_max (create-invoice păstrează corpul întreg al erorii)",
          "err_max" in X.XC.post.__code__.co_varnames)


def t_colete():
    print("2. coletele: 5 buc/colet, 0 pe magazinele de parfumuri (regula owner 20-aug)")
    check("DEFAULT_BOX = 1/5", abs(X.DEFAULT_BOX - 0.2) < 1e-9)
    parfum = sorted(X.SHOPS_FARA_COLETE_MULTIPLE)[0]
    check("magazin de parfumuri → 0", X._default_box(parfum) == 0.0, parfum)
    check("alt magazin → 1/5", abs(X._default_box("alt-magazin-xx") - 0.2) < 1e-9)


def t_prefix():
    print("3. _prefix_potrivit: cel mai lung prefix înregistrat care începe comanda")
    toks = {"GRAN": "g", "BON": "b", "BONBG": "bg"}
    check("GRAND → GRAN", X._prefix_potrivit("GRAND", toks) == "g")
    check("BONBG bate BON", X._prefix_potrivit("BONBG", toks) == "bg")
    check("nimic potrivit → None", X._prefix_potrivit("ZZZ", toks) is None)


def t_dpd():
    print("4. DPD v3.1: 120/195, retur final, circuit")
    check("120 Refusal to send = nesigur (nu refuz de client)", X._dpd_op_state(120, "Refusal to send") == "unknown")
    check("195 Refuse contents check = în curs", X._dpd_op_state(195, "Refuse contents check/test") == "progress")
    check("124 = întors", X._dpd_op_state(124, "Delivered Back to Sender") == "refused")
    check("-14 cu text de retur = nesigur", X._dpd_op_state(-14, "Returned to sender") == "unknown")
    check("fără cod, text de retur = întors", X._dpd_op_state(None, "Return to sender") == "refused")
    check("retur FINAL doar 124", X.DPD_INTORS_FINAL == {124})
    check("circuitul DPD pornește închis", X._DPD_CIRCUIT.get("deschis") is False)


def t_capture():
    print("5. decizie_capture (capture fără AWBprint)")
    acum = datetime.datetime.now(datetime.timezone.utc)
    vechi, nou = acum - datetime.timedelta(days=4), acum - datetime.timedelta(hours=2)
    cod = {"gateways": ["Cash on Delivery (COD)"], "tags": [], "trunchiat": False, "awbs": [("DPD", "80000000001")]}

    def info(code, at):
        return {"80000000001": {"code": code, "desc": "", "at": at, "redirect": None, "eroare": None}}
    check("livrat -14 → paid", X.decizie_capture(cod, info(-14, vechi), acum)[0] == "paid")
    check("124 → refuzata", X.decizie_capture(cod, info(124, nou), acum)[0] == "refuzata")
    check("111 recent → leave (DPD poate relivra)", X.decizie_capture(cod, info(111, nou), acum)[0] == "leave")
    check("111 de 4 zile → refuzata", X.decizie_capture(cod, info(111, vechi), acum)[0] == "refuzata")
    check("are deja tag refuzata → leave", X.decizie_capture(dict(cod, tags=["refuzata"]), info(124, nou), acum)[0] == "leave")
    check("card → leave", X.decizie_capture(dict(cod, gateways=["shopify_payments"]), info(-14, vechi), acum)[0] == "leave")
    check("AWB-uri trunchiate → leave", X.decizie_capture(dict(cod, trunchiat=True), info(-14, vechi), acum)[0] == "leave")
    check("curier non-DPD → leave", X.decizie_capture(dict(cod, awbs=[("Sameday", "1")]), {}, acum)[0] == "leave")
    check("DPD necitit → leave", X.decizie_capture(cod, {}, acum)[0] == "leave")


def t_registru():
    print("6. registrul cronului de facturare + lista externă")
    with tempfile.TemporaryDirectory() as d:
        cale = os.path.join(d, "registru.tsv")
        check("registru inexistent → {}", X.registru_citeste(cale) == {})
        ok = (X.registru_scrie(cale, "shop", "TST1", "1", "emisa", "TEST 1")
              and X.registru_scrie(cale, "shop", "TST2", "2", "nesigura", "timeout")
              and X.registru_scrie(cale, "shop", "TST3", "3", "nesigura", "timeout")
              and X.registru_scrie(cale, "shop", "TST3", "-", "verificata", "om"))
        reg = X.registru_citeste(cale)
        check("scriere + citire: emisa / nesigura / verificata", ok and reg == {"TST1": "emisa", "TST2": "nesigura"},
              str(reg))
        check("motiv: emisă de cron", X._registru_motiv("TST1", reg, set()) == "deja_emisa_cron")
        check("motiv: neconfirmată", X._registru_motiv("TST2", reg, set()) == "emitere_nesigura")
        check("motiv: în lista externă", X._registru_motiv("TST9", reg, {"TST9"}) == "factura_smartbill")
        check("registru ilizibil (director) → None (fail-closed)", X.registru_citeste(d) is None)
        ext = os.path.join(d, "extern.tsv")
        with open(ext, "w", encoding="utf-8") as f:
            f.write("TST1\nTST2\n")
        check("lista externă sub prag → None (fail-closed)", X.facturate_extern(ext, minim=3) is None)
        check("lista externă peste prag → nume", X.facturate_extern(ext, minim=2) == {"TST1", "TST2"})


def t_smartbill():
    print("7. SmartBill: fără credite (410) și răspuns neconfirmat")
    check("HTTP 410 → fără credite", X._smartbill_fara_credite(410, "Gone"))
    check("422 cu „reîncărcați soldul de credite” → fără credite",
          X._smartbill_fara_credite(422, {"errorMessage": "Vă rugăm reîncărcați soldul de credite"}))
    check("422 produs fără cod → NU e lipsă de credite",
          not X._smartbill_fara_credite(422, {"errorMessage": "Produsul nu are codul specificat"}))
    check("ERR transport → neconfirmat", X._raspuns_nesigur("ERR", "timed out"))
    check("„cannot confirm” → neconfirmat", X._raspuns_nesigur(422, {"errorMessage": "We cannot confirm whether"}))
    check("422 business → confirmat (refuz clar)", not X._raspuns_nesigur(422, {"errorMessage": "cod lipsă"}))
    check("NESIGURE_MAX = 2", X.NESIGURE_MAX == 2)


def t_igiena():
    print("8. repo PUBLIC: fără nume de comenzi în cod")
    check("OH_FACTURATE_FARA_TAG gol în git", len(X.OH_FACTURATE_FARA_TAG) == 0)
    check("tracking trunchiat = motiv de gardă de verificat", "tracking_trunchiat" in X.OH_GUARD_DE_VERIFICAT)
    check("motivele de token din main păstrate", set(X.OH_GUARD_TOKEN) <= set(X.OH_GUARD_MOTIVE))


# ── 9-11: drumurile reale (prin main()), pe dublurile fără rețea din test_garda_oh. Importul lui test_garda_oh
#    înlocuiește xConnector/Shopify/DPD/KB/subprocess din modul, deci rulează DUPĂ verificările de mai sus. ──
T = None


def _lume_garda():
    global T
    if T is None:
        import test_garda_oh as _t   # noqa: E402 — instalează dublurile pe X (fără rețea)
        T = _t
    return T


def _env_facturare(**kw):
    for k in ("XC_INV_REGISTRU", "XC_INV_FACTURATE_EXTERN", "XC_INV_FACTURATE_EXTERN_MIN", "XC_INV_TERMEN_EPOCH"):
        os.environ.pop(k, None)
    os.environ.update(kw)


def t_tokenuri():
    """Marcajele OAUTH: load_shopify_tokens le emite pe toate (VPS v3.1 — cod_paid_watch & co. primesc tokenuri),
    dar garda pe O comandă (și _oh_shopify din #611, dacă există) emite doar pt magazinul comenzii. O emitere eșuată
    nu se reia la fiecare apel, iar cu KB inaccesibil nu se mai cheamă KB."""
    import json
    T = _lume_garda()
    print("9. tokenurile Shopify: emitere pe toate (load_shopify_tokens) vs doar pe magazinul comenzii")
    alt = "alt-test.myshopify.com"
    marcaj_alt = "OAUTH:SHOPIFY_ALT_CLIENT_ID+SECRET"
    app_alt = {"SHOPIFY_ALT_CLIENT_ID": "cid-alt", "SHOPIFY_ALT_CLIENT_SECRET": "csec-alt"}
    kb_citiri, oauth = [], []

    def kb_numarat(key):
        kb_citiri.append(key)
        return T.fake_kb_secret(key)

    def http_numarat(method, url, headers, body=None, **kw):
        if url.endswith("/admin/oauth/access_token"):
            host = url.split("/")[2]
            oauth.append((host, (body or {}).get("client_id")))
            if host != T.SHOP:
                if ((body or {}).get("client_id"), (body or {}).get("client_secret")) == ("cid-alt", "csec-alt"):
                    T.VALIDE.add("tok-alt")
                    return 200, json.dumps({"access_token": "tok-alt", "expires_in": 86399})
                return 400, json.dumps({"error": "app_not_installed"})
        return T.fake_http(method, url, headers, body, **kw)

    def csv(tok_tst, tok_alt=marcaj_alt):
        os.environ["SHOPIFY_STORES_CSV"] = "prefix,shop,token\nTST,%s,%s\nALT,%s,%s\n" % (T.SHOP, tok_tst, alt, tok_alt)
        os.environ["SHOPIFY_ADMIN_TOKENS"] = "[]"

    def zero():
        del kb_citiri[:], oauth[:]

    X._kb_secret, X.http = kb_numarat, http_numarat
    try:
        T.lume(kb=dict(T.APP_TST, **app_alt))
        csv(T.MARCAJ)
        zero()
        st = X._tokenuri_statice()
        check("_tokenuri_statice() implicit: marcajele rămân, zero citiri KB, zero emiteri",
              st[T.SHOP]["adminToken"] == T.MARCAJ and st[alt]["adminToken"] == marcaj_alt and not kb_citiri and not oauth,
              "kb=%s oauth=%s" % (kb_citiri, oauth))
        zero()
        lt = {t["shopDomain"]: t["adminToken"] for t in X.load_shopify_tokens()}
        check("load_shopify_tokens(): ambele marcaje emise (ca pe VPS), niciun marcaj întors ca token",
              lt.get(alt) == "tok-alt" and str(lt.get(T.SHOP, "")).startswith("tok-emis-cid-tst")
              and not any(str(v).startswith("OAUTH:") for v in lt.values()), str(sorted(oauth)))
        T.lume(kb=dict(T.APP_TST, **app_alt))
        csv(T.MARCAJ)
        zero()
        sc = X._stores_csv_tokens()
        check("_stores_csv_tokens() fără argumente (cod_paid_watch.py): tokenuri emise, nu marcaje",
              len(sc) == 2 and not any(t["adminToken"].startswith("OAUTH:") for t in sc), str([t["shopDomain"] for t in sc]))

        T.lume(kb=dict(T.APP_TST, **app_alt))
        csv(T.MARCAJ)
        zero()
        cod, out = T.ruleaza("inv-make", "--order", "TST1002", "--apply")
        check("inv-make pe o comandă: emite DOAR pt magazinul ei (un singur client_id la OAuth)",
              T.facturate() == [T.OID["TST1002"]] and oauth == [(T.SHOP, "cid-tst")]
              and not any(k.startswith("SHOPIFY_ALT") for k in kb_citiri), "oauth=%s kb=%s" % (oauth, kb_citiri))

        oh_shopify = getattr(X, "_oh_shopify", None)   # din #611 (Order Hub întâi); lipsește pe branch-ul fără #611
        if oh_shopify is not None:
            import argparse
            T.lume(kb=dict(T.APP_TST, **app_alt))
            csv(T.TOK_BUN)
            zero()
            r = oh_shopify(argparse.Namespace(order="TST1002", shop=T.SHOP))
            check("_oh_shopify (#611): tokenul magazinului comenzii, zero citiri KB / emiteri pt celelalte",
                  bool(r) and r.get("adminToken") == T.TOK_BUN and not oauth and not kb_citiri,
                  "oauth=%s kb=%s" % (oauth, kb_citiri))

        T.lume(kb_jos=True)
        csv(T.MARCAJ)
        zero()
        n1 = X.load_shopify_tokens()
        c1 = len(kb_citiri)
        zero()
        n2 = X.load_shopify_tokens()
        check("KB inaccesibil: o singură citire KB eșuată pe proces, apoi niciuna (nu 12+36 la fiecare apel)",
              c1 == 1 and not kb_citiri and not oauth, "apel1=%d apel2=%d oauth=%s" % (c1, len(kb_citiri), oauth))
        check("KB inaccesibil: magazinele cu marcaj lipsesc (fără token fals)",
              not n1 and not n2, str([t["shopDomain"] for t in n1]))

        T.lume(kb=dict(T.APP_TST, SHOPIFY_ALT_CLIENT_ID="cid-x", SHOPIFY_ALT_CLIENT_SECRET="csec-x"))
        csv(T.MARCAJ)
        zero()
        X.load_shopify_tokens()
        o1 = [h for h, _c in oauth if h == alt]
        zero()
        X.load_shopify_tokens()
        o2 = [h for h, _c in oauth if h == alt]
        check("app neinstalat (400): emiterea eșuată se ține minte, nu se reia la al doilea apel",
              len(o1) == 1 and not o2 and not any(k.startswith("SHOPIFY_ALT") for k in kb_citiri),
              "apel1=%d apel2=%d kb2=%s" % (len(o1), len(o2), kb_citiri))

        # magazin din XCONNECTOR_SHOPS fără rând în CSV → _shopify_mint prin app-urile client_credentials (Lab Noir & co.)
        T.lume(tok=None, kb={"SHOPIFY_ARONA_CLIENT_ID": "cid-x", "SHOPIFY_ARONA_CLIENT_SECRET": "csec-x"})
        zero()
        X.load_shopify_tokens()
        o1, k1 = list(oauth), len(kb_citiri)
        zero()
        X.load_shopify_tokens()
        check("fără token static, app-uri neinstalate: emiterea eșuată se ține minte (_shopify_mint)",
              o1 == [(T.SHOP, "cid-x")] and k1 > 0 and not oauth and not kb_citiri,
              "apel1 oauth=%s kb=%d; apel2 oauth=%s kb=%d" % (o1, k1, oauth, len(kb_citiri)))

        # După merge-ul cu main (#611, 0469c1a): secretele vin din KB, altfel din env (Second Brain n-are KB). Cu KB
        # inaccesibil nu se mai cheamă KB după primul eșec, dar env-ul se citește; iar garda gazdei rulează înaintea
        # oricărei citiri de secret.
        chei_env = ["SHOPIFY_ALT_CLIENT_ID", "SHOPIFY_ALT_CLIENT_SECRET"] + [k for p in X._SHOPIFY_APPS for k in p]
        env_vechi = {k: os.environ.get(k) for k in chei_env}
        try:
            T.lume(kb_jos=True)
            csv(T.TOK_BUN)
            os.environ.update(app_alt)
            zero()
            lt = {t["shopDomain"]: t["adminToken"] for t in X.load_shopify_tokens()}
            k1 = len(kb_citiri)
            zero()
            X.load_shopify_tokens()
            check("KB inaccesibil, secretele marcajului în env: se emite din env, cu o singură citire KB pe proces",
                  lt.get(alt) == "tok-alt" and k1 == 1 and not kb_citiri and not oauth,
                  "token=%s kb1=%d kb2=%d oauth2=%s" % (bool(lt.get(alt)), k1, len(kb_citiri), oauth))

            T.lume(kb_jos=True)
            for k in app_alt:
                os.environ.pop(k, None)
            os.environ.update({X._SHOPIFY_APPS[-1][0]: "cid-alt", X._SHOPIFY_APPS[-1][1]: "csec-alt"})
            zero()
            tk = X._shopify_mint("lab-test.myshopify.com")
            check("KB inaccesibil, doar ultimul app are secretele în env: _shopify_mint trece la el și emite",
                  tk == "tok-alt" and oauth == [("lab-test.myshopify.com", "cid-alt")] and len(kb_citiri) <= 1,
                  "token=%s oauth=%s kb=%s" % (bool(tk), oauth, kb_citiri))

            T.lume(kb=dict(app_alt, SHOPIFY_ARONA_CLIENT_ID="cid-x", SHOPIFY_ARONA_CLIENT_SECRET="csec-x"))
            zero()
            emise = [X._shopify_mint("attacker.example"), X._shopify_mint(alt + ".evil.net"),
                     X._mint_din_marcaj("attacker.example", marcaj_alt), X._mint_din_marcaj(alt + ".evil.net", marcaj_alt)]
            check("gazdă care nu e <magazin>.myshopify.com: nicio citire de secret (KB) și nicio cerere, la ambele emiteri",
                  emise == [None] * 4 and not kb_citiri and not oauth, "kb=%s oauth=%s" % (kb_citiri, oauth))
        finally:
            for k, v in env_vechi.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    finally:
        X._kb_secret, X.http = T.fake_kb_secret, T.fake_http
        T.lume()


def t_facturare_v31():
    """Plasele v3.1 din cmd_inv_bulk, prin main(): registrul, lista externă, SmartBill 410, răspunsul neconfirmat,
    registrul care nu se scrie — DUPĂ garda OH (care rămâne prima) și după refuzul de pe stație (main)."""
    T = _lume_garda()
    print("10. inv-bulk --apply: plasele v3.1 (registru, extern, 410, neconfirmat) + garda OH + refuzul pe stație")
    post_orig = T.FakeXC.post

    def post_cu(raspunsuri):
        def post(self, path, body, err_max=300):
            T.POSTS.append((path, dict(body)))
            if path.endswith("/create-invoice") and str(body.get("orderId")) in raspunsuri:
                return raspunsuri[str(body.get("orderId"))]
            return post_orig(self, path, body, err_max)
        return post

    def creari():
        return [b for p, b in T.POSTS if p.endswith("/create-invoice")]

    try:
        with tempfile.TemporaryDirectory() as d:
            reg = os.path.join(d, "registru.tsv")
            with open(reg, "w", encoding="utf-8") as f:
                f.write("ts\tshop\tTST1002\t%s\temisa\tTEST 1\n" % T.OID["TST1002"])
                f.write("ts\tshop\tTST1001\t%s\temisa\tTEST 2\n" % T.OID["TST1001"])
            _env_facturare(XC_INV_REGISTRU=reg)
            T.lume()
            tsv = os.path.join(d, "garda.tsv")
            cod, out = T.ruleaza("inv-bulk", "--apply", "--force", "--garda-tsv", tsv, pe_vps=True)
            motiv = {r.split("\t")[1]: r.split("\t")[3] for r in open(tsv, encoding="utf-8").read().splitlines()[1:]}
            check("registru „emisa” → TST1002 nu se refacturează; se facturează doar TST1008",
                  cod == 0 and T.facturate() == [T.OID["TST1008"]], "cod=%s facturate=%s" % (cod, T.facturate()))
            check("garda OH rămâne prima: TST1001 = tag_factura_oh (nu motivul din registru)",
                  motiv.get("TST1001") == "tag_factura_oh", str(motiv.get("TST1001")))
            check("TST1002 sărită ca deja_emisa_cron", motiv.get("TST1002") == "deja_emisa_cron", str(motiv.get("TST1002")))
            text = open(reg, encoding="utf-8").read()
            check("factura emisă se scrie în registru", "\tTST1008\t" in text and "\temisa\t" in text.split("TST1008", 1)[1][:40])

        with tempfile.TemporaryDirectory() as d:
            _env_facturare(XC_INV_REGISTRU=d)   # un director: registru ilizibil
            T.lume()
            cod, out = T.ruleaza("inv-bulk", "--apply", "--force", pe_vps=True)
            check("registru ilizibil → BLOCAT, zero apeluri create-invoice", not T.POSTS and "BLOCAT" in out,
                  "cod=%s posts=%d" % (cod, len(T.POSTS)))
            ext = os.path.join(d, "extern.tsv")
            with open(ext, "w", encoding="utf-8") as f:
                f.write("A\nB\n")
            _env_facturare(XC_INV_FACTURATE_EXTERN=ext)
            T.lume()
            cod, out = T.ruleaza("inv-bulk", "--apply", "--force", pe_vps=True)
            check("lista externă sub prag → BLOCAT, zero apeluri create-invoice", not T.POSTS and "BLOCAT" in out,
                  "cod=%s posts=%d" % (cod, len(T.POSTS)))

        _env_facturare()
        T.lume()
        T.FakeXC.post = post_cu({T.OID["TST1002"]: (410, "Gone: reincarcati soldul de credite")})
        cod, out = T.ruleaza("inv-bulk", "--apply", "--force", pe_vps=True)
        check("SmartBill 410 (fără credite) → oprire după prima, ieșire 4", cod == 4 and len(creari()) == 1,
              "cod=%s creari=%d" % (cod, len(creari())))
        T.FakeXC.post = post_cu({T.OID["TST1002"]: (
            503, "SmartBill is currently unavailable. We cannot confirm whether the invoice was created")})
        cod, out = T.ruleaza("inv-bulk", "--apply", "--force", pe_vps=True)
        check("răspuns neconfirmat fără registru → oprire, ieșire 5, o singură creare", cod == 5 and len(creari()) == 1,
              "cod=%s creari=%d" % (cod, len(creari())))
        T.FakeXC.post = post_orig

        with tempfile.TemporaryDirectory() as d:
            reg = os.path.join(d, "registru.tsv")
            open(reg, "w").close()
            os.chmod(reg, 0o400)
            try:
                _env_facturare(XC_INV_REGISTRU=reg)
                T.lume()
                cod, out = T.ruleaza("inv-bulk", "--apply", "--force", pe_vps=True)
                if os.access(reg, os.W_OK):   # root ignoră 0400: verificarea n-are sens aici
                    print("  (sărit: fișierul 0400 se poate scrie — rulezi ca root)")
                else:
                    check("registrul nu se poate scrie după o factură → oprire, ieșire 6", cod == 6 and len(creari()) == 1,
                          "cod=%s creari=%d" % (cod, len(creari())))
            finally:
                os.chmod(reg, 0o600)

        with tempfile.TemporaryDirectory() as d:
            _env_facturare(XC_INV_REGISTRU=os.path.join(d, "registru.tsv"))
            T.lume()
            cod, out = T.ruleaza("inv-bulk", "--apply")
            check("--apply de pe o stație: refuzat (cod 2) înaintea oricărui apel, și cu registrul setat",
                  cod == 2 and not T.INST and not T.GQL and not T.POSTS, "cod=%s" % cod)
        ok = {"tags": [], "fulfillments_ok": True, "tracking": {"80000000002"}, "tracking_trunchiat": True}
        check("tracking Shopify trunchiat → garda sare (tracking_trunchiat)",
              X.oh_guard_motiv(ok, True, X._trk_set("80000000002"), "TST1", set()) == "tracking_trunchiat")
    finally:
        T.FakeXC.post = post_orig
        _env_facturare()
        T.lume()


def t_capture_v31():
    """cmd_capture v3.1 (fără AWBprint), prin main(): DPD live decide paid / refuzata / leave; marcajul OAUTH se
    emite (nu ajunge la Shopify ca token); token mort fără reemitere → magazin sărit, nimic scris."""
    T = _lume_garda()
    print("11. capture --apply (DPD live, fără AWBprint)")
    acum = datetime.datetime.now(datetime.timezone.utc)
    v4, n2 = acum - datetime.timedelta(days=4), acum - datetime.timedelta(hours=2)

    def nod(oid, nume, awb, tags=(), gw="Cash on Delivery (COD)"):
        return {"cursor": "c" + oid, "node": {
            "id": "gid://shopify/Order/" + oid, "name": nume, "cancelledAt": None, "test": False,
            "displayFinancialStatus": "PENDING", "tags": list(tags), "paymentGatewayNames": [gw],
            "currentTotalPriceSet": {"shopMoney": {"amount": "99.0"}},
            "fulfillments": [{"status": "SUCCESS", "trackingInfo": [{"number": awb, "company": "DPD Romania"}]}]}}
    pend = [nod("1", "TSTC1", "80000000101"), nod("2", "TSTC2", "80000000102"), nod("3", "TSTC3", "80000000103"),
            nod("4", "TSTC4", "80000000104", tags=["refuzata"]), nod("5", "TSTC5", "80000000105", gw="shopify_payments")]
    dpd_salvat = dict(T.DPD)
    T.DPD.update({"80000000101": (-14, "Delivered", v4), "80000000102": (124, "Delivered Back to Sender", n2),
                  "80000000103": (111, "Return to Sender", n2), "80000000104": (-14, "Delivered", v4),
                  "80000000105": (-14, "Delivered", v4)})
    scrise = []
    salvate = (X.shopify_gql, X.shopify_mark_paid, X.shopify_add_tags, X.shopify_remove_tags)

    def gql(shop, token, query, variables=None):
        if "financial_status:pending" in query:
            T.GQL.append((token, query))
            if T.SHOPIFY_JOS[0]:
                return {"_status": 503, "_raw": "x"}
            if token not in T.VALIDE:
                return {"errors": "[API] Invalid API key or access token"}
            return {"data": {"orders": {"edges": pend, "pageInfo": {"hasNextPage": False}}}}
        return T.fake_gql(shop, token, query, variables)

    X.shopify_gql = gql
    X.shopify_mark_paid = lambda shop, tok, gid: (scrise.append(("paid", gid.rsplit("/", 1)[-1], tok)) or (True, None))
    X.shopify_add_tags = lambda shop, tok, gid, tags: (scrise.append(("tag+", gid.rsplit("/", 1)[-1], tok)) or (True, None))
    X.shopify_remove_tags = lambda shop, tok, gid, tags: (scrise.append(("tag-", gid.rsplit("/", 1)[-1], tok)) or (True, None))

    def ruleaza():
        del scrise[:]
        return T.ruleaza("capture", "--days", "30", "--apply", "--shop", "all")

    try:
        T.lume()
        cod, out = ruleaza()
        f = {(a, o) for a, o, _t in scrise}
        check("livrat -14 de 4 zile → paid; tagul refuzata rămas se scoate (TSTC4)",
              ("paid", "1") in f and ("paid", "4") in f and ("tag-", "4") in f, str(sorted(f)))
        check("124 Delivered Back to Sender → tag refuzata (TSTC2)", ("tag+", "2") in f, str(sorted(f)))
        check("111 de 2 ore → lăsată (DPD poate relivra) (TSTC3)", not any(o == "3" for _a, o in f), str(sorted(f)))
        check("plată cu cardul → lăsată (TSTC5)", not any(o == "5" for _a, o in f), str(sorted(f)))
        check("capture: ieșire 0, fără Traceback", cod == 0 and "Traceback" not in out, "cod=%s" % cod)
        T.lume(tok=T.MARCAJ, kb=T.APP_TST)
        cod, out = ruleaza()
        tok = {t for a, _o, t in scrise if a == "paid"}
        check("marcaj OAUTH → scrie cu tokenul emis, niciodată cu marcajul",
              bool(tok) and all(str(t).startswith("tok-emis") for t in tok), str(tok))
        T.lume(tok="tok-mort", kb={})
        cod, out = ruleaza()
        check("token mort, fără reemitere → magazin sărit, nimic scris", not scrise and "SĂRITE" in out, out[-200:])
        T.lume(shopify_jos=True)
        cod, out = ruleaza()
        check("Shopify căzut → nimic scris", not scrise, str(scrise))
    finally:
        X.shopify_gql, X.shopify_mark_paid, X.shopify_add_tags, X.shopify_remove_tags = salvate
        T.DPD.clear()
        T.DPD.update(dpd_salvat)
        T.lume()


def main():
    for t in (t_atribute, t_colete, t_prefix, t_dpd, t_capture, t_registru, t_smartbill, t_igiena,
              t_tokenuri, t_facturare_v31, t_capture_v31):
        t()
    print()
    if FAILS:
        print("PICATE: %d — %s" % (len(FAILS), ", ".join(FAILS)))
        return 1
    print("toate verificările au trecut")
    return 0


if __name__ == "__main__":
    sys.exit(main())
