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
  3. repo-ul e PUBLIC: lista facturilor OH manuale rămâne goală în git.
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


def main():
    for t in (t_atribute, t_colete, t_prefix, t_dpd, t_capture, t_registru, t_smartbill, t_igiena):
        t()
    print()
    if FAILS:
        print("PICATE: %d — %s" % (len(FAILS), ", ".join(FAILS)))
        return 1
    print("toate verificările au trecut")
    return 0


if __name__ == "__main__":
    sys.exit(main())
