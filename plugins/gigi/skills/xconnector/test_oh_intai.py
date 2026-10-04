# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Test de regresie: Order Hub ÎNTÂI pe comenzile CS din xconnector.py (2-oct-2026).

Pe 29-sep-2026 anulări cerute prin xConnector pe comenzi cu eticheta făcută de Order Hub au anulat comanda în
Shopify și au lăsat eticheta vie la curier (cinci comenzi). Testul verifică, FĂRĂ nicio cerere de rețea
(xConnector, Shopify, Order Hub și KB sunt dubluri în memorie, cu stare) și trecând prin `main()`:
   1. order-cancel pe o comandă cunoscută de Order Hub: hotărăște el, fără nicio căutare în xConnector; un refuz iese
      cu cod 3; după „anulata" se cere dovada din Shopify, iar „necitit" nu se dă drept „neanulată";
   2. eticheta din Shopify se trimite ca `awb` (iar celelalte etichete vii se verifică, cu câte o probă): una pe care
      Order Hub n-o cunoaște oprește tot; o comandă care nu se poate citi din Shopify (502, token respins, răspuns
      parțial, negăsită) pleacă doar ca probă, iar --apply iese cu cod 2; tokenul se ia și după prefixul din lista de
      magazine, și dintr-un marcaj OAUTH;
   3. anularea, oprirea și refacerea prin xConnector rulează DOAR la 404 `necunoscuta`, iar Order Hub e întrebat
      înainte; nici acolo nu se anulează o comandă cu o etichetă pe care xConnector n-o are, sau pe care n-a putut-o
      citi din Shopify;
   4. fără un răspuns valid de la Order Hub (cheie lipsă sau stricată, 401, 500, rețea, 404 de rută, 409, corpuri
      care doar seamănă cu „necunoscuta") --apply iese cu cod 2 și nu scrie nimic, pe toate comenzile; un răspuns
      pierdut după o cerere de execuție e anunțat ca stare necunoscută, nu ca refuz;
   5. cine cere: --agent / CS_AGENT / EMPLOYEE_HANDLE, altfel contul și mașina;
   6. awb-void = oprire (hold); awb-regen = refacere, executată doar cu --parcels și --awb, iar repetată pe aceeași
      etichetă nu face a treia; un număr de colete necitit nu se ghicește; o etichetă făcută prin xConnector nu se
      reface cât el are altă adresă decât Shopify;
   7. awb-make: refuzat pe o comandă cu AWB viu; pe una fără AWB merge, dar nu eliberează un hold pus de Order Hub —
      nici awb-auto, nici când cine a pus hold-ul nu se poate citi;
   8. awb-create nu eliberează un hold pus de Order Hub, nici unul pe care nu-l poate citi;
   9. addr-set: avertizează că eticheta rămâne cu adresa veche (și sub pauză de AWB, și pe o comandă pe care Order
      Hub n-o cunoaște); --make-awb doar pe o comandă fără AWB viu, altfel cod 3 după schimbarea adresei;
  10. dup_guard.py și cod_paid_watch.py cheamă comenzile cum trebuie; 11. uneltele MCP; 12. oh_client;
  13. un --shop care nu e magazinul nostru nu primește secretele app-urilor Shopify (nicio emitere de token).
Dublura de Order Hub răspunde cu formele reale ale rutelor /api/depozit (măsurate cu probe pe 2-oct-2026).
Comenzile, AWB-urile, magazinul și cheile sunt inventate.

  uv run test_oh_intai.py
"""
import copy
import email.message
import http.client
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import types
import urllib.request
import urllib.response
from contextlib import redirect_stdout

AICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AICI)
ADRESE_STRAINE = ("ORDERHUB_URL", "ORDER_HUB_URL", "OH_URL", "OH_BASE_URL", "OH_BASE")
for _k in ADRESE_STRAINE:     # înaintea importului: adresa Order Hub nu are voie să vină din mediu
    os.environ[_k] = "https://alt-server.example"

import oh_client as OH  # noqa: E402
import xconnector as X  # noqa: E402

FAILS = []


def check(nume, ok, extra=""):
    print(("  ok   " if ok else "  PICAT ") + nume + (("  — " + str(extra)) if extra and not ok else ""))
    if not ok:
        FAILS.append(nume)


SHOP = "oh-test.myshopify.com"
CHEIE = "ohsvc.cs." + "x" * 40
TOKENURI_BUNE = {"tok-test", "tok-emis"}     # ce acceptă dublura de Shopify
RESPINS = {"errors": "[API] Invalid API key or access token (unrecognized login or wrong password)"}
FARA_COMANDA_EGAL_COD = "o cerere către Order Hub fără `comanda` = `cod`"


def eticheta(awb):
    return {"documentType": "SHIPPING_LABEL", "trackingNumber": awb, "connectorId": 5}


# ── ce vede xConnector ──
XC_ORDERS = [
    {"orderName": "TST1", "orderId": "91", "documents": []},
    {"orderName": "TST2", "orderId": "92", "documents": [eticheta("80000000008-80000000009")]},
    {"orderName": "TST3", "orderId": "93", "documents": []},
    {"orderName": "TST5", "orderId": "95", "documents": []},
    {"orderName": "TST8", "orderId": "96", "documents": []},
    {"orderName": "TST77", "orderId": "97", "documents": [eticheta("80000000077")]},   # Order Hub nu cunoaște comanda
    {"orderName": "TST78", "orderId": "98", "documents": []},                          # Order Hub nu cunoaște comanda
    {"orderName": "TST79", "orderId": "99", "documents": []},   # nici Order Hub, nici xConnector nu-i știu eticheta
    {"orderName": "TST6", "orderId": "90", "documents": []},
    {"orderName": "TST80", "orderId": "180", "documents": [eticheta("80000000080")]},   # Shopify are și 80000000081
    {"orderName": "TST81", "orderId": "181", "documents": [eticheta("80000000082")]},   # Shopify are doar 80000000083
    {"orderName": "TST82", "orderId": "182", "documents": [eticheta("81000000086")]},   # + un cod de colet DPD al ei
    {"orderName": "TST83", "orderId": "183", "documents": [eticheta("80000000088")]},   # + un număr de 14 cifre străin
]
# ── ce știe Order Hub ──
OH_COMENZI_0 = {
    "TST1": {"vii": ["70000000001"], "cont": "dpd-ro-arona"},        # etichetă OH, fără fulfillment în Shopify
    "TST2": {"vii": ["80000000008"], "cont": "xconnector-oh-test"},  # etichetă făcută prin xConnector
    "TST3": {"vii": []},                                             # fără etichetă; hold pus de Order Hub
    "TST4": {"vii": [], "anulata": True},
    "TST5": {"vii": ["70000000005"], "cont": "dpd-ro-arona", "plecat": True},
    "TST6": {"vii": []},                                             # Shopify arată o etichetă pe care OH n-o știe
    "TST8": {"vii": []},                                             # fără etichetă, fără hold
    "TST9": {"vii": ["70000000009"], "cont": "dpd-ro-arona"},        # xConnector n-o are deloc
    "TST10": {"vii": ["70000000010"], "cont": "dpd-ro-arona"},       # Shopify (magazinul de test) n-o are
    "TST11": {"vii": ["70000000011"], "cont": "dpd-ro-arona"},       # Shopify are și o a doua etichetă, străină
    "TST12": {"vii": ["70000000012", "70000000013"], "cont": "dpd-ro-arona"},   # două etichete, amândouă ale lui
    "TST13": {"vii": [], "anulata": True},                           # anulată în Order Hub, deschisă în Shopify
    "TST14": {"vii": ["70000000015"], "cont": "dpd-ro-arona", "arhiva": ["70000000014"]},   # Shopify arată încă 14
}
# ── ce arată Shopify: tracking-ul fulfillment-urilor vii (și al celor anulate) și hold-urile (motiv, notă, aplicație) ──
SHOPIFY_0 = {
    "TST1": {"tracking": []},
    "TST2": {"tracking": ["80000000008"], "anulate": ["80000000007"], "erori": ["80000000006"]},
    "TST3": {"tracking": [], "holds": [("OTHER", "Blocklist: serial-refuser (>=2 refuzuri)", "Order Hub")]},
    "TST4": {"tracking": [], "anulata": True},
    "TST5": {"tracking": ["70000000005"]},
    "TST6": {"tracking": ["80000000066"]},
    "TST8": {"tracking": []},
    "TST9": {"tracking": ["70000000009"]},
    "TST77": {"tracking": ["80000000077"]},
    "TST78": {"tracking": [], "holds": [("OTHER", "xc-review", "ARONA Assistant")]},
    "TST79": {"tracking": ["80000000079"]},
    "TST11": {"tracking": ["70000000011", "80000000012"]},
    "TST12": {"tracking": ["70000000012", "70000000013"]},
    "TST13": {"tracking": []},
    "TST14": {"tracking": ["70000000014"]},
    "TST80": {"tracking": ["80000000080", "80000000081"]},
    "TST81": {"tracking": ["80000000083"], "anulate": ["80000000082"]},
    "TST82": {"tracking": ["81000000086", "81000000080002"]},
    "TST83": {"tracking": ["80000000088", "80000000080002"]},
}
OH_COMENZI, SHOPIFY = {}, {}
OH_RASPUNS = [None]          # (status, corp) impus cererilor, sau funcție (cale, corp) -> (status, corp) | None
SHOPIFY_CONFIRMA = [True]    # False = Order Hub zice „anulata", dar Shopify nu arată comanda anulată
PICA = {}                    # bucată din query-ul Shopify -> răspunsul stricat întors în locul celui bun
CITIRI_STARE = [0, None, 0]  # [câte citiri ale stării comenzii, după a câta pică (None = niciodată), câte pică la început]
CONFIRMA_DUPA = [0]          # câte citiri după anulare arată Shopify comanda încă deschisă
TOKEN = ["tok-test"]         # tokenul magazinului din lista de magazine (poate fi expirat sau un marcaj OAUTH)
COLETE = [2]                 # metafield-ul de colete al comenzii; None = Shopify nu răspunde, "partial" = răspuns parțial
ORAS_SHOPIFY = ["Vechi"]     # orașul comenzii în Shopify; xConnector are „Cluj"
EVENIMENTE, MUTATII, ANULARI, KB, SUBPROC = [], [], [], {}, []


def oh_raspuns(cale, c):
    """Rutele /api/depozit, cu formele reale de răspuns (services/depozit_actiuni din Order Hub)."""
    for camp in ("cod", "utilizator"):
        if not str(c.get(camp) or "").strip():
            return 400, {"detail": "%s: lipsă" % camp.capitalize()}
    refa = cale.endswith("/refa")
    if refa:
        try:
            colete = int(c.get("colete"))
        except (TypeError, ValueError):
            return 400, {"detail": "Colete: număr invalid"}
        if not 1 <= colete <= 99:
            return 400, {"detail": "Colete: între 1 și 99"}
    elif c.get("actiune") not in ("anulare", "hold"):
        return 400, {"detail": "Acțiune: anulare sau hold"}
    elif not str(c.get("motiv") or "").strip():
        return 400, {"detail": "Motiv: lipsă"}
    cod, o = c["cod"], OH_COMENZI.get(c["cod"])
    if o is None:
        return 404, {"detail": {"rezultat": "necunoscuta", "mesaj": "Cod: %s · necunoscut în Order Hub" % cod}}

    def rez(ok, rezultat, mesaj="", plan=(), pasi=(), **extra):
        d = {"ok": ok, "rezultat": rezultat, "mesaj": mesaj, "pasi": list(pasi), "plan": list(plan),
             "comanda": cod, "order_id": 1, "magazin": "Magazin Test", "anulata": bool(o.get("anulata"))}
        d.update(extra)
        return 200, d
    awb, arhiva = c.get("awb") or "", o.setdefault("arhiva", [])
    if awb and awb not in o["vii"] and awb not in arhiva:
        return rez(False, "eticheta_necunoscuta",
                   "Etichetă: %s · necunoscută în Order Hub · comanda: %s · nimic făcut" % (awb, cod))
    de_anulat = ["AWB de anulat: %s (DPD, cont %s)" % (a, o.get("cont")) for a in o["vii"]] or ["AWB de anulat: niciunul viu"]
    plecate = ["%s (in_transit)" % a for a in o["vii"]] if o.get("plecat") else []
    proba = c.get("dry_run") is not False
    if refa:
        if o.get("anulata"):
            return rez(False, "anulata", "Comandă: %s · anulată · AWB nou: nu" % cod)
        if awb in arhiva:
            return rez(False, "eticheta_anulata", "AWB: %s · deja anulat · AWB curent: %s · nimic refăcut"
                       % (awb, ", ".join(o["vii"]) or "niciunul"))
        if not o["vii"]:
            return rez(False, "fara_awb", "Comandă: %s · AWB viu: niciunul" % cod)
        if plecate:
            return rez(False, "plecat", "Colet: %s · plecat la curier · nimic refăcut" % "; ".join(plecate), plecate=plecate)
        if proba:
            return rez(True, "previzualizare", plan=de_anulat + ["AWB nou: %d colete, pe același cont" % colete])
        vechi, nou = o["vii"][0], "7000000%04d" % (99 - len(arhiva))
        arhiva.append(vechi)
        o["vii"] = [nou]
        if cod in SHOPIFY:   # eticheta nouă apare în Shopify ca tracking; fulfillment-ul celei vechi rămâne, anulat
            SHOPIFY[cod]["anulate"] = SHOPIFY[cod].get("anulate", []) + SHOPIFY[cod]["tracking"]
            SHOPIFY[cod]["tracking"] = [nou]
        return rez(True, "refacut", "AWB vechi: %s · anulat · AWB nou: %s · colete: %d" % (vechi, nou, colete),
                   pasi=[{"pas": "refa", "ok": True, "text": "AWB nou: %s" % nou}], awb_vechi=[vechi], awb_nou=nou)
    if c.get("actiune") == "hold":
        if o.get("anulata"):
            return rez(False, "anulata", "Comandă: %s · deja anulată · nimic de oprit" % cod)
        plan = de_anulat + ["Comandă: rămâne · hold (nu pleacă acum)", "Notă: [OPRIT depozit · %s] %s" % (c["utilizator"], c["motiv"])]
        if plecate:
            return rez(False, "plecat", "Colet: %s · plecat la curier · nimic făcut" % "; ".join(plecate), plecate=plecate,
                       plan=plan if proba else [])
        if proba:
            return rez(True, "previzualizare", plan=plan)
        anulate, o["vii"] = o["vii"], []
        return rez(True, "oprita", "Comandă: %s · OPRITĂ (hold)" % cod, etichete_anulate=anulate,
                   pasi=[{"pas": "void", "ok": True, "text": "AWB anulat: %s" % ", ".join(anulate)},
                         {"pas": "hold", "ok": True, "text": "Hold: pus pe 1 fulfillment order-e"}])
    if o.get("anulata") and not o["vii"]:
        return rez(not proba, "deja_anulata", "Comandă: %s · deja anulată · AWB viu: niciunul" % cod,
                   plan=de_anulat if proba else [])
    if plecate:
        return rez(False, "plecat", "Colet: %s · plecat la curier · nimic făcut" % "; ".join(plecate), plecate=plecate,
                   plan=de_anulat if proba else [], pasi=[] if proba else [{"pas": "void", "ok": False, "text": "AWB: plecat la curier"}])
    if proba:
        return rez(True, "previzualizare", plan=de_anulat + [
            "Comandă: se anulează în magazin", "Stoc: %s" % ("se repune" if c.get("restock") else "nu se repune"),
            "Notă: [ANULAT depozit · %s] %s" % (c["utilizator"], c["motiv"]), "Tag: anulat-depozit"])
    anulate, o["vii"], o["anulata"] = o["vii"], [], True
    if SHOPIFY_CONFIRMA[0] and cod in SHOPIFY:
        SHOPIFY[cod]["anulata_dupa"] = CITIRI_STARE[0] + CONFIRMA_DUPA[0]
    return rez(True, "anulata", "Comandă: %s · ANULATĂ" % cod, anulata=True, etichete_anulate=anulate,
               pasi=[{"pas": "void", "ok": True, "text": "AWB anulat: %s" % ", ".join(anulate)},
                     {"pas": "anulare", "ok": True, "text": "Comandă: anulată în magazin; stoc repus"}])


def fake_oh_http(method, url, headers, body=None, timeout=90):
    if not url.startswith(OH.BASE + "/api/depozit/"):
        raise AssertionError("rețea neașteptată: %s %s" % (method, url))
    cale = url[len(OH.BASE):]
    EVENIMENTE.append(("oh", cale, dict(body), headers.get("Authorization"), timeout))
    if body.get("comanda") != body.get("cod") and FARA_COMANDA_EGAL_COD not in FAILS:
        FAILS.append(FARA_COMANDA_EGAL_COD)   # pe server, `comanda` e singura gardă contra etichetei altei comenzi
    impus = OH_RASPUNS[0](cale, body) if callable(OH_RASPUNS[0]) else OH_RASPUNS[0]
    if impus is not None:
        s, corp = impus
    elif headers.get("Authorization") != "Bearer " + CHEIE:
        s, corp = 401, {"detail": "Cheie de serviciu: invalidă"}
    else:
        s, corp = oh_raspuns(cale, body)
    return s, (corp if isinstance(corp, str) else json.dumps(corp))


def _nod_fo(nume):
    holds = SHOPIFY.get(nume, {}).get("holds") or []
    return {"id": "gid://shopify/FulfillmentOrder/%s" % nume, "status": "ON_HOLD" if holds else "OPEN",
            "fulfillmentHolds": [{"reason": r, "reasonNotes": n, "heldByApp": {"title": app}} for r, n, app in holds]}


def fake_gql(shop, token, query, variables=None):
    if token not in TOKENURI_BUNE:
        return dict(RESPINS)
    if "shop { id }" in query:
        return {"data": {"shop": {"id": "gid://shopify/Shop/1"}}}
    for bucata, stricat in PICA.items():
        if bucata in query:
            return copy.deepcopy(stricat)
    if "fulfillmentOrderReleaseHold" in query:
        nume = re.search(r"FulfillmentOrder/(\w+)", query).group(1)
        MUTATII.append("release " + nume)
        SHOPIFY[nume]["holds"] = []
        return {"data": {"fulfillmentOrderReleaseHold": {"fulfillmentOrder": {"status": "OPEN"}, "userErrors": []}}}
    if "orderUpdate" in query:
        MUTATII.append("orderUpdate")
        return {"data": {"orderUpdate": {"order": {"id": "gid://shopify/Order/1"}, "userErrors": []}}}
    nume = re.search(r"name:(\w+)", query).group(1)
    s = SHOPIFY.get(nume)
    if s is None:
        return {"data": {"orders": {"edges": []}}}
    if "parcel-count" in query:      # pentru order_parcel_count adevărat
        if COLETE[0] is None:
            return {"_status": 502, "_raw": "Bad Gateway"}
        if COLETE[0] == "partial":
            return {"errors": [{"message": "Access denied for product field"}],
                    "data": {"orders": {"edges": [{"node": {"pc": None, "lineItems": None}}]}}}
        return {"data": {"orders": {"edges": [{"node": {"pc": {"value": str(COLETE[0])}, "lineItems": {"edges": []}}}]}}}
    if "fulfillmentOrders" in query:
        return {"data": {"orders": {"edges": [{"node": {"fulfillmentOrders": {"edges": [{"node": _nod_fo(nume)}]}}}]}}}
    if "cancelledAt" in query:
        CITIRI_STARE[0] += 1
        if (CITIRI_STARE[1] is not None and CITIRI_STARE[0] > CITIRI_STARE[1]) or CITIRI_STARE[0] <= CITIRI_STARE[2]:
            return {"_status": 502, "_raw": "Bad Gateway"}
        anulata = s.get("anulata") or ("anulata_dupa" in s and CITIRI_STARE[0] > s["anulata_dupa"])
        return {"data": {"orders": {"edges": [{"node": {
            "id": "gid://shopify/Order/1", "cancelledAt": "2026-10-02T10:00:00Z" if anulata else None,
            "fulfillments": [{"status": "CANCELLED", "trackingInfo": [{"number": t}]} for t in s.get("anulate", [])]
            + [{"status": "ERROR", "trackingInfo": [{"number": t}]} for t in s.get("erori", [])]
            + [{"status": "SUCCESS", "trackingInfo": [{"number": t}]} for t in s["tracking"]]}}]}}}
    raise AssertionError("query Shopify neașteptat: %s" % query[:90])


class FakeXC:
    def __init__(self, apikey):
        pass

    def orders(self, dfrom, dto, filters=None):
        EVENIMENTE.append(("xc-scan",))
        return [dict(o) for o in XC_ORDERS]

    def by_id(self, oid):
        EVENIMENTE.append(("xc-adresa",))
        return {"shippingAddress": {"zip": "100001", "city": "Cluj", "address1": "Str Veche 1"}}

    def post(self, path, body):
        EVENIMENTE.append(("xc", path, dict(body)))
        nume = next(o["orderName"] for o in XC_ORDERS if o["orderId"] == body["orderId"])
        if path.endswith("create-shipping-label") and SHOPIFY[nume].get("holds"):
            return 200, {"accepted": False, "errorMessage": "Shipping label was not created: no open fulfillment orders"}
        return 200, {"accepted": True, "shippingLabels": [{"success": True}]}


def fara_retea(*a, **k):
    SUBPROC.append(str((a[0] if a else k.get("args")) or "")[:80])
    raise AssertionError("subprocess sau rețea neașteptată")


OH_HTTP_REAL = OH._http    # transportul adevărat, pentru verificările din 12 (cu un opener fals dedesubt)
OH._http = fake_oh_http
X.http = fara_retea        # nimic din xconnector.py n-are voie să iasă în rețea pe lângă dubluri
X.XC = FakeXC
X.PREFIX_DOMAIN["TST"] = SHOP.replace(".myshopify.com", "")
X.load_shops = lambda: [{"shopDomain": SHOP, "apiKey": "k-test"}]
X.load_shopify_tokens = lambda: [{"shopDomain": SHOP, "adminToken": "tok-test", "prefix": "TST"}]
X._tokenuri_statice = lambda: {SHOP: {"shopDomain": SHOP, "adminToken": TOKEN[0], "prefix": "TST"}}
X.shopify_gql = fake_gql
X.shopify_order_id = lambda name, st: None
X.find_order = lambda shop, token, name: {
    "id": "gid://shopify/Order/1", "cancelledAt": None, "displayFulfillmentStatus": "UNFULFILLED",
    "fulfillmentOrders": {"edges": [{"node": {k: _nod_fo(name)[k] for k in ("id", "status")}}]}}
X.shopify_order_cancel = lambda shop, token, gid, **k: ANULARI.append(gid) or []
X.shopify_order_address = lambda shop, token, name: ("gid://shopify/Order/1", {
    "address1": "Str Veche 1", "city": ORAS_SHOPIFY[0], "zip": "100001", "province": "Prahova", "countryCodeV2": "RO"})
X.shopify_order_tags = lambda name, toks: []
X.awbprint_status = lambda name: None
X.awbprint_recent_dup = lambda name: None
X.pick_connector = lambda xc, a: ({"id": 5, "name": "DPD test"}, [])
X.route_connector = lambda sh, st, order, cons, con: con
X._kb_secret = lambda key: (KB[key], True) if key in KB else ("", False)
X.subprocess.run = fara_retea
X.time.sleep = lambda s: None


def ruleaza(*args, raspuns=None, cheie=CHEIE, agent="Raluca", handle=None, confirma=True, pastreaza=False,
            pica=None, stare_pica_dupa=None, stare_pica_primele=0, confirma_dupa=0, token="tok-test", colete=2,
            oras="Vechi"):
    """main() cu argumentele date → (cod_ieșire, stdout). `raspuns` = (status, corp) impus de la Order Hub, sau o
    funcție (cale, corp). `pastreaza` = lumea rămâne cum a lăsat-o rularea dinainte (a doua cerere pe aceeași comandă).
    `pica` / `stare_pica_dupa` / `token` = cum se strică Shopify; `colete` = ce se citește de acolo; `oras` = orașul."""
    del EVENIMENTE[:], MUTATII[:], ANULARI[:]
    if not pastreaza:
        OH_COMENZI.clear(); OH_COMENZI.update(copy.deepcopy(OH_COMENZI_0))
        SHOPIFY.clear(); SHOPIFY.update(copy.deepcopy(SHOPIFY_0))
    OH_RASPUNS[0], SHOPIFY_CONFIRMA[0] = raspuns, confirma
    PICA.clear(); PICA.update(pica or {})
    CITIRI_STARE[:] = [0, stare_pica_dupa, stare_pica_primele]
    CONFIRMA_DUPA[0] = confirma_dupa
    TOKEN[0], COLETE[0], ORAS_SHOPIFY[0] = token, colete, oras
    X._ARONA_TOK.clear()      # un token emis într-o rulare nu trece în următoarea
    for k, v in ((OH.TOKEN_ENV, cheie), ("CS_AGENT", agent), ("EMPLOYEE_HANDLE", handle)):
        os.environ.pop(k, None)
        if v:
            os.environ[k] = v
    sys.argv = ["xconnector.py"] + list(args)
    buf, cod = io.StringIO(), 0
    with redirect_stdout(buf):
        try:
            X.main()
        except SystemExit as e:
            cod = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    return cod, buf.getvalue()


def oh():
    """Cererile către Order Hub: [(cale, corp, antet)]."""
    return [e[1:4] for e in EVENIMENTE if e[0] == "oh"]


def doar_probe():
    return all(c[1]["dry_run"] is True for c in oh())


def xc():
    return [e[1].rsplit("/", 1)[-1] for e in EVENIMENTE if e[0] == "xc"]


def scanari():
    return sum(1 for e in EVENIMENTE if e[0] == "xc-scan")


def fara_scrieri():
    return not xc() and not ANULARI and not MUTATII


FARA_RASPUNS = (("cheie lipsă", dict(cheie="")),
                ("cheie fără forma ohsvc", dict(cheie="parola-gresita\nBearer x")),
                ("401 cheie revocată", dict(raspuns=(401, {"detail": "Cheie de serviciu: revocată"}))),
                ("403 fără drept", dict(raspuns=(403, {"detail": "Cheie de serviciu: fără dreptul «depozit»"}))),
                ("500", dict(raspuns=(500, "Internal Server Error"))),
                ("rețea", dict(raspuns=("ERR", "TimeoutError: timed out"))),
                ("200 necitit", dict(raspuns=(200, "<html>"))),
                ("404 de rută", dict(raspuns=(404, {"detail": "Not Found"}))),
                ("404 cu rezultat la vârf, fără detail", dict(raspuns=(404, {"rezultat": "necunoscuta"}))),
                ("404 cu alt rezultat", dict(raspuns=(404, {"detail": {"rezultat": "alta", "mesaj": "necunoscuta"}}))),
                ("500 cu detail necunoscuta", dict(raspuns=(500, {"detail": {"rezultat": "necunoscuta"}}))),
                ("409 cod ambiguu", dict(raspuns=(409, {"detail": "Cod: TST77 · 2 comenzi în Order Hub"}))),
                ("422 corp respins", dict(raspuns=(422, {"detail": [{"msg": "x"}]}))))
PAUZA = {"ok": False, "rezultat": "pauza", "plan": [], "pasi": [], "anulata": False,
         "mesaj": "AWB nou: oprit (pauză de AWB pe magazin: trecut pe FAN) · eticheta veche: rămâne"}

print("1. order-cancel pe o comandă cunoscută de Order Hub — hotărăște el")
cod, out = ruleaza("order-cancel", "--order", "TST1", "--motiv", "client s-a răzgândit")
cale, corp, auth = oh()[0]
check("probă: o singură cerere, la /api/depozit/anulare, cu dry_run, fără nicio căutare în xConnector", cod == 0
      and len(oh()) == 1 and cale == "/api/depozit/anulare" and corp["dry_run"] is True and corp["actiune"] == "anulare"
      and scanari() == 0, (cod, len(oh()), scanari()))
check("probă: cheia, codul (și în `comanda`), agentul, motivul și restock-ul ajung la Order Hub", auth == "Bearer " + CHEIE
      and corp["cod"] == corp["comanda"] == "TST1" and corp["utilizator"] == "Raluca"
      and corp["motiv"] == "client s-a răzgândit" and corp["restock"] is True, corp)
check("probă: planul Order Hub și magazinul sunt tipărite, fără scrieri", "prin ORDER HUB" in out and "PROBĂ" in out
      and "(Magazin Test)" in out and "plan: AWB de anulat: 70000000001 (DPD, cont dpd-ro-arona)" in out
      and fara_scrieri(), out)
cod, out = ruleaza("order-cancel", "--order", "TST1", "--apply", "--no-restock", "--refund", "--notify")
check("--apply: dry_run False, restock False, xConnector neatins, dovada cerută din Shopify", cod == 0
      and oh()[0][1]["dry_run"] is False and oh()[0][1]["restock"] is False and fara_scrieri() and scanari() == 0
      and "ANULATĂ" in out and "confirmat în Shopify" in out and "⛔" not in out, out)
check("--refund și --notify: se spune că nu se aplică", "--refund nu se aplică" in out and "--notify nu se aplică" in out, out)
cod, out = ruleaza("order-cancel", "--order", "TST1", "--apply", confirma=False)
check("Order Hub zice „anulata”, Shopify nu: se spune, nu se dă drept confirmat", cod == 0
      and "Shopify arată comanda încă NEANULATĂ" in out and "confirmat în Shopify" not in out, out)
cod, out = ruleaza("order-cancel", "--order", "TST1", "--apply", confirma_dupa=1)
check("Shopify arată anularea abia la a doua citire: tot confirmat", cod == 0 and "confirmat în Shopify" in out
      and "NEANULATĂ" not in out, out)
cod, out = ruleaza("order-cancel", "--order", "TST13", "--apply")
check("„deja anulată” în Order Hub, deschisă în Shopify: se spune, nu trece drept reușită", cod == 0
      and "deja anulată" in out and "Shopify arată comanda încă NEANULATĂ" in out and fara_scrieri(), out)
cod, out = ruleaza("order-cancel", "--order", "TST4")
check("probă pe o comandă deja anulată: fără semn de refuz", cod == 0 and "deja anulată" in out and "⛔" not in out, out)
cod, out = ruleaza("order-cancel", "--order", "TST1", "--apply", stare_pica_dupa=1)
check("dovada nu se poate citi (Shopify pică după anulare): „neconfirmat”, nu „neanulată”", cod == 0
      and oh()[0][1]["dry_run"] is False and "Neconfirmat în Shopify" in out and "NEANULATĂ" not in out
      and "confirmat în Shopify:" not in out, out)
cod, out = ruleaza("order-cancel", "--order", "TST5", "--apply", "--force")
check("refuzul Order Hub (plecat) oprește tot, și cu --force, cu cod 3", cod == 3 and fara_scrieri()
      and "Order Hub: plecat" in out and "--force nu există" in out, (cod, out))
cod, out = ruleaza("order-cancel", "--order", "TST5")
check("același refuz la probă: cod 0", cod == 0 and "Order Hub: plecat" in out and fara_scrieri(), (cod, out))
cod, out = ruleaza("order-cancel", "--order", "TST4", "--apply")
check("comandă deja anulată: se spune, fără semn de refuz, cod 0", cod == 0 and "deja anulată" in out and "⛔" not in out
      and fara_scrieri(), (cod, out))
cod, out = ruleaza("order-cancel", "--order", "TST9", "--apply")
check("comandă pe care xConnector n-o are: tot Order Hub hotărăște", cod == 0 and len(oh()) == 1
      and "negăsită" not in out and fara_scrieri(), out)
cod, out = ruleaza("order-cancel", "--order", "TST1", "--apply", raspuns=(200, {
    "ok": True, "rezultat": "anulata", "mesaj": "Comandă: TST1 · ANULATĂ", "plan": [], "anulata": True, "pasi": [
        {"pas": "void", "ok": True, "text": "AWB anulat: 70000000001"},
        {"pas": "fulfillment", "ok": False, "text": "Fulfillment Shopify: 70000000001 SUCCESS"}]}))
check("ok cu un pas picat (fulfillment încă viu): avertisment, nu succes curat", cod == 0
      and "pași neconfirmați" in out and "❌ Fulfillment Shopify: 70000000001 SUCCESS" in out, out)
cod, out = ruleaza("order-cancel", "--order", "TST1", "--apply", raspuns=(200, {
    "ok": False, "rezultat": "shopify_refuz", "anulata": False, "plan": [], "etichete_anulate": ["70000000001"],
    "mesaj": "AWB anulat: 70000000001 · Comandă: TST1 · NEANULATĂ (Shopify: 403) · tichet CS: deschis",
    "pasi": [{"pas": "void", "ok": True, "text": "AWB anulat: 70000000001"},
             {"pas": "anulare", "ok": False, "text": "Comandă: NEANULATĂ (Shopify: 403)"}]}))
check("Shopify refuză după anularea etichetei: cod 3, comanda rămasă deschisă e spusă", cod == 3 and fara_scrieri()
      and "Order Hub: shopify_refuz" in out and "DESCHISĂ în Shopify" in out and "tichet CS: deschis" in out, (cod, out))

print("2. eticheta din Shopify merge la Order Hub ca `awb`")
cod, out = ruleaza("order-cancel", "--order", "TST2", "--apply")
check("etichetă făcută prin xConnector, cunoscută de Order Hub: o anulează el (tracking-ul unui fulfillment anulat nu"
      " contează)", cod == 0 and oh()[0][1]["awb"] == "80000000008" and "ANULATĂ" in out and fara_scrieri(), out)
cod, out = ruleaza("order-cancel", "--order", "TST6", "--apply")
check("etichetă pe care Order Hub n-o cunoaște: nimic făcut, nici prin xConnector, cod 3", cod == 3 and fara_scrieri()
      and oh()[0][1]["awb"] == "80000000066" and "Order Hub: eticheta_necunoscuta" in out
      and "nu anula nimic din xConnector" in out, (cod, out))
cod, out = ruleaza("order-cancel", "--order", "TST1", "--apply")
check("etichetă Order Hub fără fulfillment în Shopify: `awb` gol, hotărăște după ce știe el", oh()[0][1]["awb"] == "")
PARTIAL = {"errors": [{"message": "Access denied for fulfillments field"}], "data": {"orders": {"edges": [{"node": {
    "id": "gid://shopify/Order/1", "cancelledAt": None, "fulfillments": None}}]}}}
for nume, kw in (("Shopify răspunde 502", dict(pica={"cancelledAt": {"_status": 502, "_raw": "Bad Gateway"}})),
                 ("tokenul magazinului e respins", dict(token="tok-expirat")),
                 ("răspuns parțial, fără fulfillment-uri", dict(pica={"cancelledAt": PARTIAL}))):
    picate = []
    for cmd in ("order-cancel", "awb-void"):
        cod, out = ruleaza(cmd, "--order", "TST6", "--apply", **kw)
        if not (cod == 2 and fara_scrieri() and oh() and doar_probe() and "nu s-a putut citi din Shopify" in out
                and "nu s-a executat nimic" in out and not OH_COMENZI["TST6"].get("anulata")):
            picate.append("%s --apply: cod %s, probe %s" % (cmd, cod, doar_probe()))
        cod, out = ruleaza(cmd, "--order", "TST6", **kw)
        if not (cod == 0 and fara_scrieri() and "nu s-a putut citi din Shopify" in out and "plan: " in out):
            picate.append("%s probă: cod %s" % (cmd, cod))
    check("%s: order-cancel și awb-void pleacă doar ca probă, --apply iese cu cod 2" % nume, not picate, "; ".join(picate))
for cmd in ("order-cancel", "awb-void"):
    cod, out = ruleaza(cmd, "--order", "TST11", "--apply")
    check("%s: a doua etichetă din Shopify e străină lui Order Hub: nimic executat, cod 3" % cmd, cod == 3
          and doar_probe() and fara_scrieri() and OH_COMENZI["TST11"]["vii"] == ["70000000011"]
          and not OH_COMENZI["TST11"].get("anulata") and "are în Shopify și eticheta 80000000012" in out, (cod, out))
cod, out = ruleaza("order-cancel", "--order", "TST11")
check("aceeași comandă la probă: se spune, cod 0", cod == 0 and "eticheta 80000000012" in out and "doar proba" in out, out)
cod, out = ruleaza("order-cancel", "--order", "TST12", "--apply")
check("două etichete, amândouă cunoscute de Order Hub: anularea merge, a doua doar verificată cu o probă", cod == 0
      and OH_COMENZI["TST12"].get("anulata") and [c[1]["dry_run"] for c in oh()] == [True, False]
      and oh()[0][1]["awb"] == "70000000013" and oh()[1][1]["awb"] == "70000000012", (cod, oh(), out))
cod, out = ruleaza("order-cancel", "--order", "TST10", "--apply")
check("comandă cunoscută de Order Hub, dar negăsită în Shopify: tot doar probă, cod 2", cod == 2 and doar_probe()
      and OH_COMENZI["TST10"]["vii"] == ["70000000010"] and "nu s-a putut citi din Shopify" in out, (cod, out))
cod, out = ruleaza("awb-void", "--order", "TST77", "--apply", token="tok-expirat")
check("comandă necitită din Shopify, pe care Order Hub n-o cunoaște: calea xConnector rămâne deschisă", cod == 0
      and doar_probe() and xc() == ["cancel-shipping-label"], (cod, xc(), out))
del X.PREFIX_DOMAIN["TST"]
cod, out = ruleaza("order-cancel", "--order", "TST6", "--apply")
X.PREFIX_DOMAIN["TST"] = SHOP.replace(".myshopify.com", "")
check("prefix care lipsește din PREFIX_DOMAIN: magazinul se află din lista de tokenuri, eticheta tot se trimite",
      cod == 3 and oh()[0][1]["awb"] == "80000000066", (cod, out))
KB.update({"TST_CLIENT_ID": "id", "TST_CLIENT_SECRET": "secret"})
X.http = lambda method, url, headers, body=None, timeout=45: (
    (200, json.dumps({"access_token": "tok-emis", "expires_in": 86400})) if url == "https://%s/admin/oauth/access_token" % SHOP
    else fara_retea(url))
cod, out = ruleaza("order-cancel", "--order", "TST6", "--apply", token="OAUTH:TST_CLIENT_ID+SECRET")
X.http = fara_retea
KB.clear()
check("marcaj OAUTH în loc de token: se emite un token și eticheta tot se trimite", cod == 3
      and oh()[0][1]["awb"] == "80000000066" and "tok-emis" not in out, (cod, out))

print("3. calea xConnector doar la 404 necunoscuta, cu Order Hub întrebat înainte")
cod, out = ruleaza("order-cancel", "--order", "TST77", "--apply")
check("order-cancel: întâi Order Hub, apoi eticheta xConnector, apoi comanda", cod == 0
      and [e[0] for e in EVENIMENTE][:2] == ["oh", "xc-scan"] and xc() == ["cancel-shipping-label"]
      and len(ANULARI) == 1 and len(oh()) == 1, (EVENIMENTE, out))
cod, out = ruleaza("awb-void", "--order", "TST77", "--apply")
check("awb-void: anulare prin xConnector", EVENIMENTE[0][0] == "oh" and xc() == ["cancel-shipping-label"] and not ANULARI, out)
cod, out = ruleaza("awb-regen", "--order", "TST77", "--connector", "9", "--apply")
check("awb-regen --connector: void + create prin xConnector", EVENIMENTE[0][0] == "oh"
      and xc() == ["cancel-shipping-label", "create-shipping-label"], out)
cod, out = ruleaza("awb-regen", "--order", "TST77", "--apply")
check("awb-regen fără --awb pe o comandă necunoscută de Order Hub: merge ca până acum", cod == 0
      and xc() == ["cancel-shipping-label", "create-shipping-label"], (cod, out))
cod, out = ruleaza("order-cancel", "--order", "TST77")
check("probă: planul xConnector, fără avertisment", cod == 0 and "DRY-RUN" in out and "Fără un răspuns" not in out
      and fara_scrieri(), out)
cod, out = ruleaza("order-cancel", "--order", "TST79", "--apply")
check("etichetă vie în Shopify pe care n-o are nici Order Hub, nici xConnector: comanda nu se anulează, cod 3", cod == 3
      and fara_scrieri() and "are în Shopify eticheta 80000000079" in out, (cod, xc(), ANULARI, out))
cod, out = ruleaza("order-cancel", "--order", "TST79")
check("aceeași comandă la probă: același refuz, nu un plan de anulare", cod == 0 and "are în Shopify eticheta" in out
      and "DRY-RUN" not in out, out)
cod, out = ruleaza("order-cancel", "--order", "TST79", "--apply", pica={"cancelledAt": {"_status": 502, "_raw": "Bad Gateway"}})
check("comandă pe care Order Hub n-o cunoaște, cu Shopify necitit: calea veche nu anulează nimic, cod 2", cod == 2
      and fara_scrieri() and doar_probe() and "nu s-a putut citi din Shopify" in out, (cod, ANULARI, out))
for comanda, straina in (("TST80", "80000000081"), ("TST81", "80000000083")):
    cod, out = ruleaza("order-cancel", "--order", comanda, "--apply")
    check("calea veche: Shopify are o etichetă vie (%s) pe care xConnector n-o are, deși are alta: nimic anulat, cod 3"
          % straina, cod == 3 and fara_scrieri() and ("are în Shopify eticheta %s," % straina) in out, (cod, xc(), ANULARI, out))
cod, out = ruleaza("order-cancel", "--order", "TST80")
check("…la probă: același refuz, nu un plan de anulare", cod == 0 and "are în Shopify eticheta 80000000081" in out
      and "DRY-RUN" not in out, out)
cod, out = ruleaza("order-cancel", "--order", "TST82", "--apply")
check("calea veche: codul de colet DPD al etichetei xConnector nu e o etichetă străină", cod == 0
      and xc() == ["cancel-shipping-label"] and len(ANULARI) == 1, (cod, xc(), out))
cod, out = ruleaza("order-cancel", "--order", "TST83", "--apply")
check("…dar un număr de 14 cifre care nu e cod de colet DPD (nu începe cu 81 / 85) rămâne străin", cod == 3
      and fara_scrieri() and "eticheta 80000000080002" in out, (cod, xc(), out))
for comanda, straina in (("TST79", "80000000079"), ("TST80", "80000000081"), ("TST81", "80000000083")):
    cod, out = ruleaza("awb-regen", "--order", comanda, "--parcels", "2", "--apply")
    check("awb-regen pe calea veche: eticheta %s din Shopify, pe care n-o are xConnector, oprește refacerea, cod 3"
          % straina, cod == 3 and fara_scrieri() and straina in out, (cod, xc(), out))
cod, out = ruleaza("awb-regen", "--order", "TST79")
check("…la probă: același refuz, nu planul de refacere", cod == 0 and "80000000079" in out and "DRY-RUN" not in out, out)
for nume, kw in (("Shopify 502", dict(pica={"cancelledAt": {"_status": 502, "_raw": "Bad Gateway"}})),
                 ("token respins", dict(token="tok-expirat"))):
    cod, out = ruleaza("awb-regen", "--order", "TST77", "--parcels", "2", "--awb", "80000000077", "--apply", **kw)
    check("awb-regen pe calea veche, cu Shopify necitit (%s): nimic refăcut, cod 2" % nume, cod == 2 and fara_scrieri()
          and doar_probe() and "nu s-a putut citi din Shopify" in out, (cod, xc(), out))
cod, out = ruleaza("awb-regen", "--order", "TST79", "--parcels", "2", "--apply", stare_pica_primele=1)
check("awb-regen pe calea veche: eticheta se recitește cu magazinul găsit de xConnector, iar cea străină oprește tot",
      cod == 3 and fara_scrieri() and "80000000079" in out, (cod, xc(), out))
cod, out = ruleaza("awb-regen", "--order", "TST82", "--parcels", "2", "--apply")
check("awb-regen pe calea veche: codul de colet DPD nu oprește refacerea", cod == 0
      and xc() == ["cancel-shipping-label", "create-shipping-label"], (cod, xc(), out))
cod, out = ruleaza("order-cancel", "--order", "TST79", "--apply", stare_pica_primele=1)
check("…iar dacă Shopify răspunde la a doua citire, eticheta străină tot oprește anularea, cod 3", cod == 3
      and fara_scrieri() and "are în Shopify eticheta 80000000079" in out, (cod, ANULARI, out))

print("4. fără un răspuns valid de la Order Hub nu se scrie nimic")
for cmd in (["order-cancel", "--order", "TST77"], ["awb-void", "--order", "TST77"],
            ["awb-regen", "--order", "TST77", "--parcels", "2", "--awb", "80000000077"],
            ["awb-make", "--order", "TST78"], ["awb-create", "--order", "TST78"],
            ["addr-set", "--order", "TST78", "--city", "Cluj", "--make-awb"]):
    picate = []
    for nume, kw in FARA_RASPUNS:
        cod, out = ruleaza(*(cmd + ["--apply", "--force"]), **kw)
        if not (cod == 2 and fara_scrieri()):
            picate.append("%s --apply: cod %s, scrieri %s" % (nume, cod, not fara_scrieri()))
        cod, out = ruleaza(*cmd, **kw)
        if not (cod == 0 and fara_scrieri() and "Fără un răspuns valid" in out):
            picate.append("%s probă: cod %s" % (nume, cod))
    check("%s: --apply iese cu cod 2 fără scrieri, proba avertizează (%d forme de eșec)"
          % (" ".join(cmd[:1] + cmd[3:]), len(FARA_RASPUNS)), not picate, "; ".join(picate))
cod, out = ruleaza("order-cancel", "--order", "TST77", raspuns=(500, "x"))
check("probă fără răspuns: planul xConnector e marcat doar orientativ", "DOAR orientativ" in out and "DRY-RUN" in out, out)
cod, out = ruleaza("order-cancel", "--order", "TST1", "--apply", cheie="")
check("cheie lipsă: nicio cerere către Order Hub, „nu s-a scris nimic”", cod == 2 and not oh() and "nu s-a scris nimic" in out, out)
cod, out = ruleaza("order-cancel", "--order", "TST1", "--apply", cheie="parola-gresita\nBearer x")
check("cheie stricată: nicio cerere, iar cheia nu apare în ce se tipărește", cod == 2 and not oh()
      and "parola-gresita" not in out, out)
for nume, r in (("500", (500, "Internal Server Error")), ("timeout", ("ERR", "TimeoutError: timed out")), ("200 necitit", (200, "<html>"))):
    cod, out = ruleaza("order-cancel", "--order", "TST1", "--apply", raspuns=r)
    check("%s după o cerere de execuție: stare necunoscută, nu „refuzat, nimic scris”" % nume, cod == 2
          and "POATE să fi fost executată" in out and "Nu reîncerca orbește" in out and "nu s-a scris nimic" not in out, out)
cod, out = ruleaza("order-cancel", "--order", "TST1", "--apply", raspuns=(401, {"detail": "Cheie de serviciu: revocată"}))
check("401 la o cerere de execuție: respinsă înainte de orice, deci „nu s-a scris nimic”", cod == 2
      and "nu s-a scris nimic" in out and "POATE" not in out, out)
KB[OH.TOKEN_ENV] = CHEIE
X.KB_UNREACHABLE = False
cod, out = ruleaza("order-cancel", "--order", "TST1", cheie="")
check("cheia se ia din KB când nu e în env", cod == 0 and oh() and oh()[0][2] == "Bearer " + CHEIE, out)
KB.clear()


def kb_jos(key):
    X.KB_UNREACHABLE = True     # ca _kb_secret adevărat, la o conexiune picată
    return "", False


X._kb_secret = kb_jos
cod, out = ruleaza("order-cancel", "--order", "TST1", "--apply", cheie="")
X._kb_secret = lambda key: (KB[key], True) if key in KB else ("", False)
check("KB inaccesibil: se spune că n-a răspuns KB (nu „cheia nu e setată”), iar marcajul celorlalte citiri rămâne",
      cod == 2 and not oh() and "KB nu răspunde" in out and X.KB_UNREACHABLE is False, (cod, X.KB_UNREACHABLE, out))

print("5. cine cere")
cod, out = ruleaza("order-cancel", "--order", "TST1", "--apply", "--agent", "Oana")
check("--agent bate env-ul", cod == 0 and oh()[0][1]["utilizator"] == "Oana", out)
cod, out = ruleaza("order-cancel", "--order", "TST1", "--apply", handle="sonia")
check("CS_AGENT bate EMPLOYEE_HANDLE", cod == 0 and oh()[0][1]["utilizator"] == "Raluca", oh()[0][1]["utilizator"])
cod, out = ruleaza("order-cancel", "--order", "TST1", "--apply", agent="", handle="sonia")
check("EMPLOYEE_HANDLE ține loc de agent", cod == 0 and oh()[0][1]["utilizator"] == "sonia" and "ca autor" not in out, out)
cod, out = ruleaza("order-cancel", "--order", "TST1", "--apply", agent="")
check("fără agent: contul și mașina, cu îndemnul de a da --agent", cod == 0 and "@" in oh()[0][1]["utilizator"]
      and "--agent" in out and "ca autor" in out, (oh()[0][1]["utilizator"], out))

print("6. awb-void = oprire, awb-regen = refacere")
cod, out = ruleaza("awb-void", "--order", "TST1", "--connector", "7")
check("awb-void, probă: spune că e oprire, trimite la awb-regen, --connector nu se aplică", cod == 0
      and oh()[0][1]["actiune"] == "hold" and oh()[0][1]["dry_run"] is True and "awb-void = OPRIRE" in out
      and "awb-regen" in out and "--connector nu se aplică" in out and scanari() == 0, out)
cod, out = ruleaza("awb-void", "--order", "TST1", "--apply")
check("awb-void --apply: hold prin Order Hub, cu trimitere la Order Hub pentru AWB-ul următor", cod == 0
      and oh()[0][1]["dry_run"] is False and "OPRITĂ (hold)" in out and "nu awb-make" in out and fara_scrieri(), out)
cod, out = ruleaza("awb-void", "--order", "TST4", "--apply")
check("awb-void pe o comandă deja anulată: refuz + trimitere la order-cancel", cod == 3
      and "o anulează order-cancel" in out, (cod, out))
cod, out = ruleaza("awb-void", "--order", "TST2")
check("awb-void trimite eticheta din Shopify ca `awb`", cod == 0 and oh()[0][1]["awb"] == "80000000008" and fara_scrieri(), out)
cod, out = ruleaza("awb-void", "--order", "TST6", "--apply")
check("awb-void pe o comandă cu o etichetă pe care Order Hub n-o cunoaște: nimic oprit, cod 3", cod == 3 and fara_scrieri()
      and oh()[0][1]["awb"] == "80000000066" and "Order Hub: eticheta_necunoscuta" in out, (cod, out))
cod, out = ruleaza("awb-regen", "--order", "TST1")
check("awb-regen, probă: coletele din Shopify și comanda de execuție gata scrisă", cod == 0
      and oh()[0][0] == "/api/depozit/refa" and oh()[0][1]["colete"] == 2 and oh()[0][1]["dry_run"] is True
      and "→ execuție: xconnector.py awb-regen --order TST1 --parcels 2 --awb 70000000001 --apply" in out
      and "verifică numărul" in out, out)
cod, out = ruleaza("awb-regen", "--order", "TST1", "--shop", SHOP, "--agent", "Oana", "--motiv", "adresă schimbată")
check("rândul de execuție păstrează --shop, --agent și --motiv date la probă", cod == 0 and (
    '--awb 70000000001 --shop "%s" --agent "Oana" --motiv "adresă schimbată" --apply' % SHOP) in out, out)
cod, out = ruleaza("awb-regen", "--order", "TST1", token="tok-expirat")
check("awb-regen, probă cu Shopify necitit: se spune, iar rândul de execuție nu apare (execuția s-ar refuza)", cod == 0
      and "nu s-a putut citi din Shopify" in out and "→ execuție" not in out and oh()[0][1]["colete"] == 1
      and "Numărul de colete nu s-a putut citi" in out and "calculat din Shopify" not in out, out)
cod, out = ruleaza("awb-regen", "--order", "TST11", "--parcels", "2")
check("awb-regen, probă cu o etichetă străină în Shopify: refuzul, fără rândul de execuție", cod == 0
      and "eticheta 80000000012" in out and "→ execuție" not in out, out)
for nume, kw in (("Shopify nu răspunde", dict(colete=None)), ("răspuns parțial", dict(colete="partial"))):
    cod, out = ruleaza("awb-regen", "--order", "TST1", **kw)
    check("număr de colete necitit (%s): proba pleacă cu 1, dar rândul de execuție nu-l ghicește" % nume, cod == 0
          and oh()[0][1]["colete"] == 1 and "Numărul de colete nu s-a putut citi" in out and "calculat din Shopify" not in out
          and "--parcels <nr. colete> --awb 70000000001" in out and not re.search(r"--parcels \d", out), out)
cod, out = ruleaza("awb-regen", "--order", "TST1", colete=3)
check("numărul de colete se citește din Shopify (metafield-ul comenzii)", cod == 0 and oh()[0][1]["colete"] == 3
      and "--parcels 3 --awb 70000000001" in out, out)
cod, out = ruleaza("awb-regen", "--order", "TST2")
check("proba de refacere trimite eticheta din Shopify ca `awb`", cod == 0 and oh()[0][1]["awb"] == "80000000008", out)
cod, out = ruleaza("awb-regen", "--order", "TST6", "--apply")
check("awb-regen pe o comandă cu o etichetă pe care Order Hub n-o cunoaște: refuz, cod 3", cod == 3
      and oh()[0][1]["awb"] == "80000000066" and "Order Hub: eticheta_necunoscuta" in out and doar_probe(), (cod, out))
cod, out = ruleaza("awb-regen", "--order", "TST1", "--parcels", "3", "--apply")
check("--apply fără --awb: doar probă, cod 2, nimic refăcut", cod == 2 and len(oh()) == 1
      and oh()[0][1]["dry_run"] is True and "--apply cere --parcels și --awb" in out
      and OH_COMENZI["TST1"]["vii"] == ["70000000001"], (cod, out))
cod, out = ruleaza("awb-regen", "--order", "TST1", "--awb", "70000000001", "--apply")
check("--apply fără --parcels: doar probă, cod 2", cod == 2 and doar_probe(), (cod, out))
cod, out = ruleaza("awb-regen", "--order", "TST1", "--parcels", "3", "--awb", "70000000001", "--apply")
check("--apply cu --parcels și --awb: refăcut prin Order Hub, cu eticheta numită", cod == 0
      and oh()[-1][0] == "/api/depozit/refa"
      and oh()[-1][1] == dict(oh()[-1][1], cod="TST1", comanda="TST1", colete=3, awb="70000000001", dry_run=False)
      and "AWB nou: 70000000099" in out and fara_scrieri(), out)
check("etichetă pe un cont direct al Order Hub: adresa din xConnector nu se întreabă", not any(
    e[0] == "xc-adresa" for e in EVENIMENTE), EVENIMENTE)
cod, out = ruleaza("awb-regen", "--order", "TST1", "--parcels", "3", "--awb", "70000000001", "--apply", pastreaza=True)
check("aceeași cerere repetată (răspuns pierdut), cu eticheta nouă deja în Shopify: se trimite tot cea numită, e"
      " refuzată și nu iese a treia etichetă", cod == 3 and oh()[-1][1]["awb"] == "70000000001"
      and SHOPIFY["TST1"]["tracking"] == ["70000000099"] and "Order Hub: eticheta_anulata" in out
      and OH_COMENZI["TST1"]["vii"] == ["70000000099"] and len(OH_COMENZI["TST1"]["arhiva"]) == 1,
      (cod, out, OH_COMENZI["TST1"]))
for steag in (["--connector", "9"], ["--type", "ENVELOPE"]):
    cod, out = ruleaza("awb-regen", "--order", "TST1", "--parcels", "2", "--awb", "70000000001", "--apply", *steag)
    check("%s pe o comandă a Order Hub: refuz cu cod 3, cererea rămâne probă" % steag[0], cod == 3 and doar_probe()
          and "nu se aplică pe o comandă a Order Hub" in out and fara_scrieri(), (cod, out))
cod, out = ruleaza("awb-regen", "--order", "TST11", "--parcels", "2", "--awb", "70000000011", "--apply")
check("awb-regen: a doua etichetă din Shopify e străină lui Order Hub: nimic refăcut, cod 3", cod == 3 and doar_probe()
      and OH_COMENZI["TST11"]["vii"] == ["70000000011"] and "are în Shopify și eticheta 80000000012" in out, (cod, out))
cod, out = ruleaza("awb-regen", "--order", "TST1", "--parcels", "3", "--awb", "70000000001", "--apply", token="tok-expirat")
check("awb-regen: comandă necitită din Shopify: doar probă, cod 2", cod == 2 and doar_probe()
      and OH_COMENZI["TST1"]["vii"] == ["70000000001"] and "nu s-a putut citi din Shopify" in out, (cod, out))
cod, out = ruleaza("awb-regen", "--order", "TST14", "--parcels", "3")
check("Shopify arată încă o etichetă înlocuită de Order Hub: proba se reface pe comandă și arată eticheta vie", cod == 0
      and doar_probe() and [c[1]["awb"] for c in oh() if c[0].endswith("/refa")] == ["70000000014", ""]
      and "deja înlocuită în Order Hub" in out
      and "→ execuție: xconnector.py awb-regen --order TST14 --parcels 3 --awb 70000000015 --apply" in out, (cod, oh(), out))
cod, out = ruleaza("awb-regen", "--order", "TST14", "--parcels", "3", "--awb", "70000000014", "--apply")
check("…iar cu eticheta înlocuită dată ca --awb: refuzul rămâne, nu iese a treia etichetă", cod == 3
      and OH_COMENZI["TST14"]["vii"] == ["70000000015"] and "Order Hub: eticheta_anulata" in out, (cod, out))
cod, out = ruleaza("awb-regen", "--order", "TST1", "--parcels", "2", "--awb", "70000000001", "--notify")
check("awb-regen --notify pe calea Order Hub: se spune că nu se aplică", cod == 0 and "--notify nu se aplică" in out, out)
cod, out = ruleaza("awb-regen", "--order", "TST8", "--parcels", "1", "--awb", "x", "--apply")
check("awb-regen cu o etichetă pe care Order Hub n-o are: refuz, cod 3", cod == 3 and fara_scrieri(), (cod, out))
cod, out = ruleaza("awb-regen", "--order", "TST8")
check("awb-regen pe o comandă fără AWB viu: se spune cine îi face AWB-ul", "Order Hub: fara_awb" in out
      and "îl face Order Hub" in out and fara_scrieri(), out)
cod, out = ruleaza("awb-regen", "--order", "TST2", "--parcels", "1", "--awb", "80000000008", "--apply")
check("etichetă făcută prin xConnector, iar el are altă adresă decât Shopify: nu se reface, cod 2", cod == 2
      and doar_probe() and oh()[0][1]["actiune"] == "hold" and OH_COMENZI["TST2"]["vii"] == ["80000000008"]
      and "xConnector are altă adresă decât Shopify (Shopify: Vechi 100001 · xConnector: Cluj 100001)" in out, (cod, out))
cod, out = ruleaza("awb-regen", "--order", "TST2", "--parcels", "1", "--awb", "80000000008", "--apply", oras="Cluj")
check("aceeași etichetă, cu adresa ajunsă și în xConnector: se reface", cod == 0 and oh()[-1][1]["dry_run"] is False
      and "AWB nou: 70000000099" in out and any(e[0] == "xc-adresa" for e in EVENIMENTE)
      and [e[0] for e in EVENIMENTE].index("oh") < [e[0] for e in EVENIMENTE].index("xc-adresa"), (cod, EVENIMENTE, out))
cod, out = ruleaza("awb-regen", "--order", "TST2", "--parcels", "1", "--awb", "80000000008", "--apply", oras="Cluj",
                   pastreaza=True)
check("repetată pe o comandă a cărei etichetă era în Shopify de la început: tot refuzată", cod == 3
      and oh()[-1][1]["awb"] == "80000000008" and OH_COMENZI["TST2"]["vii"] == ["70000000099"], (cod, out))

print("7. awb-make")
for nume, comanda, text in (("are AWB făcut de Order Hub", "TST1", "are deja AWB: 70000000001 (DPD, cont dpd-ro-arona)"),
                            ("e anulată", "TST4", "comanda e anulată"),
                            ("a plecat", "TST5", "coletul a plecat (70000000005 (in_transit))")):
    cod, out = ruleaza("awb-make", "--order", comanda, "--apply", "--force")
    check("comanda %s: refuzat cu cod 3, fără xConnector" % nume, cod == 3 and fara_scrieri() and scanari() == 0
          and text in out and oh()[0][0] == "/api/depozit/refa" and oh()[0][1]["dry_run"] is True, (cod, out))
cod, out = ruleaza("awb-make", "--order", "TST1")
check("probă pe o comandă cu AWB: același refuz, nu „aș POST”", cod == 0 and "are deja AWB" in out and "aș POST" not in out, out)
cod, out = ruleaza("awb-make", "--order", "TST1", "--apply", raspuns=(200, PAUZA))
check("AWB-urile magazinului pe pauză în Order Hub: refuzat", cod == 3 and fara_scrieri() and "pauză de AWB pe magazin" in out, out)
cod, out = ruleaza("awb-make", "--order", "TST3", "--apply", "--force")
check("comandă fără AWB, cu hold pus de Order Hub: refuzat cu cod 3, hold-ul rămâne, nicio etichetă", cod == 3
      and fara_scrieri() and SHOPIFY["TST3"]["holds"] and "hold pus de Order Hub (Blocklist: serial-refuser" in out
      and "Se eliberează din Order Hub" in out, (cod, xc(), MUTATII, out))
cod, out = ruleaza("awb-make", "--order", "TST3")
check("aceeași comandă la probă: același refuz, nu „aș POST”", cod == 0 and "hold pus de Order Hub" in out
      and "aș POST" not in out, out)
ruleaza("awb-make", "--order", "TST3")
nrel, motive, sarite = X.shopify_release_holds(SHOP, "tok-test", "TST3")
check("shopify_release_holds sare hold-ul pus de Order Hub (plasă și pentru cron)", nrel == 0 and not MUTATII
      and sarite == ["Order Hub: Blocklist: serial-refuser (>=2 refuzuri)"], (nrel, motive, sarite))
PICA["fulfillmentHolds{ reason reasonNotes"] = {"errors": [{"message": "Access denied for heldByApp field"}], "data": {
    "orders": {"edges": [{"node": {"fulfillmentOrders": {"edges": [{"node": dict(_nod_fo("TST3"), fulfillmentHolds=[
        {"reason": "OTHER", "reasonNotes": "Blocklist", "heldByApp": None}])}]}}}]}}}
nrel, motive, sarite = X.shopify_release_holds(SHOP, "tok-test", "TST3")
PICA.clear()
check("shopify_release_holds nu eliberează nimic când nu se poate citi cine a pus hold-ul (răspuns parțial)", nrel == 0
      and not MUTATII and sarite == ["hold-uri necitite din Shopify"], (nrel, motive, sarite))
check("release_hold (awb-auto) lasă neatinsă comanda cu hold pus de Order Hub", X.release_hold(SHOP, "tok-test", "TST3") == (0, 1)
      and not MUTATII, MUTATII)
PICA["fulfillmentHolds{ reasonNotes"] = {"_status": 502, "_raw": "Bad Gateway"}
necitit = X.release_hold(SHOP, "tok-test", "TST78")
PICA.clear()
check("release_hold nu eliberează nimic când hold-urile nu se pot citi", necitit == (0, 1) and not MUTATII, (necitit, MUTATII))
check("release_hold eliberează un hold care nu e al Order Hub, ca până acum", X.release_hold(SHOP, "tok-test", "TST78") == (1, 1)
      and MUTATII == ["release TST78"], MUTATII)
cod, out = ruleaza("awb-make", "--order", "TST6", "--apply")
check("awb-make: Order Hub n-are etichetă, dar Shopify arată una vie (făcută de mână): refuzat, cod 3", cod == 3
      and fara_scrieri() and scanari() == 0 and "Shopify arată AWB viu (80000000066)" in out, (cod, xc(), out))
cod, out = ruleaza("awb-make", "--order", "TST6")
check("…la probă: același refuz, nu „aș POST”", cod == 0 and "Shopify arată AWB viu" in out and "aș POST" not in out, out)
cod, out = ruleaza("awb-create", "--order", "TST6", "--apply")
check("awb-create: la fel, hold-ul nu se eliberează", cod == 3 and not MUTATII and "Shopify arată AWB viu" in out, (cod, out))
cod, out = ruleaza("awb-make", "--order", "TST79", "--apply")
check("awb-make pe o comandă pe care Order Hub n-o cunoaște, cu AWB viu doar în Shopify: refuzat, cod 3, fără trimitere"
      " la Order Hub", cod == 3 and fara_scrieri() and "Shopify arată AWB viu (80000000079)" in out
      and "reîncearcă peste ~20 s" in out and "Vezi comanda în Order Hub" not in out, (cod, out))
cod, out = ruleaza("awb-create", "--order", "TST79", "--apply")
check("awb-create pe aceeași comandă: refuzat, cod 3", cod == 3 and not MUTATII and "Shopify arată AWB viu" in out, (cod, out))
cod, out = ruleaza("awb-make", "--order", "TST78", "--apply", token="tok-expirat")
check("awb-make pe o comandă pe care Order Hub n-o cunoaște, cu Shopify necitit: nimic făcut, cod 2", cod == 2
      and fara_scrieri() and "nu s-a putut citi din Shopify" in out, (cod, xc(), MUTATII, out))
cod, out = ruleaza("awb-make", "--order", "TST8", "--apply", token="tok-expirat")
check("awb-make: comandă necitită din Shopify: nimic făcut, cod 2", cod == 2 and fara_scrieri()
      and "nu s-a putut citi din Shopify" in out, (cod, out))
cod, out = ruleaza("awb-make", "--order", "TST8", "--apply")
check("comandă cunoscută de Order Hub, fără AWB și fără hold: AWB prin xConnector, cu lămurirea", cod == 0
      and xc() == ["create-shipping-label"] and not MUTATII and "fără AWB viu" in out, (cod, xc(), out))
cod, out = ruleaza("awb-make", "--order", "TST78", "--apply", agent="")
check("comandă necunoscută de Order Hub, cu hold străin: se eliberează și se face AWB, ca până acum", cod == 0
      and MUTATII == ["release TST78"] and xc() == ["create-shipping-label", "create-shipping-label"]
      and oh()[0][1]["dry_run"] is True, (xc(), MUTATII, out))
SHOPIFY_0["TST78"]["holds"] = [("HIGH_RISK_OF_FRAUD", "", "Shopify")]
cod, out = ruleaza("awb-make", "--order", "TST78", "--apply")
check("hold de fraudă: rămâne neatins, ca până acum", not MUTATII and "HOLD LEGITIM (HIGH_RISK_OF_FRAUD)" in out, out)
SHOPIFY_0["TST78"]["holds"] = [("OTHER", "xc-review", "ARONA Assistant")]

print("8. awb-create")
cod, out = ruleaza("awb-create", "--order", "TST3", "--apply")
check("hold pus de Order Hub: nu se eliberează, cod 3", cod == 3 and not MUTATII
      and "Hold pus de Order Hub (Blocklist: serial-refuser" in out, (cod, out))
for nume, stricat in (("erori GraphQL", {"errors": [{"message": "Throttled"}]}), ("502", {"_status": 502, "_raw": "x"}),
                      ("rețea", {"_status": "ERR", "_raw": "timed out"})):
    cod, out = ruleaza("awb-create", "--order", "TST3", "--apply", pica={"fulfillmentHolds{ reasonNotes": stricat})
    check("hold-urile nu se pot citi (%s): nu se eliberează nimic, cod 2" % nume, cod == 2 and not MUTATII
          and SHOPIFY["TST3"]["holds"] and "nu s-au putut citi din Shopify" in out, (cod, MUTATII, out))
cod, out = ruleaza("awb-create", "--order", "TST1", "--apply")
check("comandă cu AWB în Order Hub: refuzat", cod == 3 and not MUTATII and "are deja AWB" in out, (cod, out))
cod, out = ruleaza("awb-create", "--order", "TST78", "--apply")
check("comandă necunoscută de Order Hub: hold-ul se eliberează, ca până acum", cod == 0 and MUTATII == ["release TST78"], out)

print("9. addr-set")
cod, out = ruleaza("addr-set", "--order", "TST2", "--city", "Cluj")
check("probă pe o comandă cu AWB: avertizează că eticheta rămâne cu adresa veche", cod == 0 and fara_scrieri()
      and "are deja AWB (80000000008 (DPD, cont xconnector-oh-test))" in out and "awb-regen --order TST2" in out
      and len(oh()) == 1 and oh()[0][1]["actiune"] == "hold" and doar_probe(), out)
cod, out = ruleaza("addr-set", "--order", "TST2", "--city", "Cluj", "--apply")
check("--apply: adresa se schimbă, avertismentul rămâne, Order Hub primește doar probe", cod == 0
      and MUTATII == ["orderUpdate"] and "are deja AWB" in out and doar_probe() and not xc(), (cod, MUTATII, out))
cod, out = ruleaza("addr-set", "--order", "TST1", "--city", "Cluj", "--make-awb", "--apply")
check("--make-awb pe o comandă cu etichetă pe care doar Order Hub o știe: adresa se schimbă, al doilea AWB nu se face,"
      " cod 3", cod == 3 and not xc() and MUTATII == ["orderUpdate"] and "--make-awb nu se aplică" in out
      and "NU s-a făcut" in out and doar_probe(), (cod, MUTATII, xc(), out))
cod, out = ruleaza("addr-set", "--order", "TST5", "--city", "Cluj")
check("colet plecat: spune că adresa nu mai ajunge pe el", "Coletul a plecat (70000000005 (DPD, cont dpd-ro-arona))" in out, out)
cod, out = ruleaza("addr-set", "--order", "TST1", "--city", "Cluj",
                   raspuns=lambda cale, c: (200, PAUZA) if cale.endswith("/refa") else None)
check("AWB-urile magazinului pe pauză: eticheta veche tot se vede și se anunță", cod == 0
      and "are deja AWB (70000000001" in out and len(oh()) == 1, out)
cod, out = ruleaza("addr-set", "--order", "TST8", "--city", "Cluj", "--make-awb", "--apply",
                   raspuns=lambda cale, c: (200, PAUZA) if cale.endswith("/refa") else None)
check("--make-awb sub pauză de AWB: adresa se schimbă, AWB-ul nu se face, cod 3", cod == 3 and not xc()
      and MUTATII == ["orderUpdate"] and "pauză de AWB pe magazin" in out, (cod, xc(), out))
cod, out = ruleaza("addr-set", "--order", "TST3", "--city", "Cluj", "--make-awb", "--apply")
check("--make-awb pe o comandă ținută de Order Hub pe hold: AWB-ul nu se face, hold-ul rămâne, cod 3", cod == 3
      and not xc() and MUTATII == ["orderUpdate"] and SHOPIFY["TST3"]["holds"] and "hold pus de Order Hub" in out,
      (cod, xc(), MUTATII, out))
cod, out = ruleaza("addr-set", "--order", "TST6", "--city", "Cluj", "--make-awb", "--apply")
check("--make-awb pe o comandă cu o etichetă vie doar în Shopify: adresa se schimbă, al doilea AWB nu se face, cod 3",
      cod == 3 and not xc() and MUTATII == ["orderUpdate"]
      and "--make-awb nu se aplică: comanda are deja AWB (80000000066)" in out, (cod, MUTATII, xc(), out))
cod, out = ruleaza("addr-set", "--order", "TST6", "--city", "Cluj", "--make-awb", "--apply", stare_pica_primele=1)
check("--make-awb cu starea comenzii necitită din Shopify: nimic scris, cod 2", cod == 2 and fara_scrieri()
      and "nu s-a putut citi din Shopify" in out, (cod, MUTATII, xc(), out))
cod, out = ruleaza("addr-set", "--order", "TST6", "--city", "Cluj", "--make-awb", stare_pica_primele=1)
check("…la probă: se spune, iar planul nu mai promite AWB", cod == 0 and fara_scrieri()
      and "nu s-a putut citi din Shopify" in out and "apoi awb-make" not in out, out)
cod, out = ruleaza("addr-set", "--order", "TST6", "--city", "Cluj")
check("comandă a Order Hub fără etichetă la el, dar cu una făcută de mână (în Shopify): se anunță", cod == 0
      and "are deja AWB (80000000066)" in out, out)
FRISBO = {"ok": False, "rezultat": "refuzat", "plan": [], "pasi": [], "anulata": False, "comanda": "TST1",
          "mesaj": "Etichetă: 70000000001 · a depozitului Frisbo · anulare: doar la Frisbo"}
cod, out = ruleaza("addr-set", "--order", "TST1", "--city", "Cluj",
                   raspuns=lambda cale, c: (200, FRISBO) if c.get("actiune") == "hold" else None)
check("eticheta depozitului Frisbo: se spune ce zice Order Hub, fără sfatul de refacere", cod == 0
      and "a depozitului Frisbo" in out and "Refă eticheta" not in out, out)
cod, out = ruleaza("addr-set", "--order", "TST77", "--city", "Cluj")
check("comandă pe care Order Hub n-o cunoaște, cu etichetă în xConnector: tot se anunță", cod == 0
      and "are deja AWB (80000000077)" in out and "awb-regen --order TST77" in out, out)
cod, out = ruleaza("addr-set", "--order", "TST78", "--city", "Cluj", "--make-awb", "--apply")
check("comandă necunoscută de Order Hub: adresă + AWB prin xConnector, o singură întrebare la Order Hub", cod == 0
      and MUTATII[0] == "orderUpdate" and xc()[-1] == "create-shipping-label" and len(oh()) == 1, (MUTATII, xc(), out))
cod, out = ruleaza("addr-set", "--order", "TST78", "--city", "Cluj", "--apply", raspuns=(500, "x"))
check("fără --make-awb, adresa se schimbă și fără răspunsul Order Hub, iar lipsa lui se spune", cod == 0
      and MUTATII == ["orderUpdate"] and not xc() and "Order Hub n-a răspuns" in out, out)

print("10. dup_guard.py și cod_paid_watch.py")
RADACINA = os.path.normpath(os.path.join(AICI, "..", "..", "..", ".."))


def incarca(nume, cale):
    spec = importlib.util.spec_from_file_location(nume, cale)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


sys.modules.setdefault("psycopg2", types.ModuleType("psycopg2"))
DG = incarca("dup_guard", os.path.join(AICI, "..", "cs-duplicate-orders", "dup_guard.py"))
APELURI, RASPUNSURI = [], []


def fake_run(args, **k):
    APELURI.append(list(args))
    cod, text = RASPUNSURI.pop(0) if RASPUNSURI else (0, "ok")
    return types.SimpleNamespace(returncode=cod, stdout=text, stderr="")


subprocess.run = fake_run
RASPUNSURI[:] = [(0, "ANULATĂ"), (3, "⛔ Order Hub: in_lucru — xConnector nu se atinge."), (0, "pus în hold")]
r1, r2, r3 = DG.xc("order-cancel", "TST1", apply=True), DG.xc("order-cancel", "TST1", apply=True), DG.xc("awb-hold", "TST1", apply=True)
check("dup_guard: order-cancel pleacă cu --agent și --motiv", APELURI[0][-5:] == [
    "--agent", "dup-guard", "--motiv", "comandă dublă", "--apply"], APELURI[0][-6:])
check("dup_guard: refuzul Order Hub (cod 3) e eșec, nu „rezolvat”", " ✓" in r1 and " ✓" not in r2 and "✗" in r2, (r1, r2))
check("dup_guard: awb-hold rămâne cum era", "--agent" not in APELURI[2] and APELURI[2][-1] == "--apply", APELURI[2][-4:])

CPW = incarca("cod_paid_watch", os.path.join(RADACINA, "shared", "scripturi-tools", "cod_paid_watch.py"))
CPW._dispatched_live = lambda name, shop: False
ruleaza("order-cancel", "--order", "TST1")     # lumea de la capăt, cheia în mediu
del APELURI[:], EVENIMENTE[:]
check("cod_paid_watch: comanda pe care Order Hub o cunoaște nu se atinge de aici (o reface el), cu o singură probă",
      CPW.recall("TST1", SHOP) == "ORDER-HUB" and not APELURI and len(oh()) == 1 and doar_probe()
      and OH_COMENZI["TST1"]["vii"] == ["70000000001"], (APELURI, oh()))
del APELURI[:], EVENIMENTE[:]
RASPUNSURI[:] = [(0, "  ✅ anulat"), (0, "  ✅ AWB creat"), (0, "  AWB 80000000078 · DPD")]
nou = CPW.recall("TST77", SHOP)
comenzi = [a[2:] for a in APELURI]
check("cod_paid_watch: pe o comandă pe care Order Hub n-o cunoaște, void + make prin CLI, ca până acum",
      nou == "80000000078" and [c[0] for c in comenzi] == ["awb-void", "awb-make", "links"]
      and all("--apply" in c for c in comenzi[:2]) and oh()[0][1]["dry_run"] is True, (nou, comenzi))
del APELURI[:]
RASPUNSURI[:] = [(0, "  AWB void · TST77 · connector 5 · tracking 80000000077\n  ❌ 422: {'accepted': False}")]
check("cod_paid_watch: xConnector refuză anularea (iese tot cu 0, „❌”) → fără awb-make, nimic raportat ca refăcut",
      CPW.recall("TST77", SHOP) is None and len(APELURI) == 1, APELURI)
del APELURI[:]
RASPUNSURI[:] = [(0, "  AWB void · TST77 · connector 5 · tracking 80000000077\n  ✅ anulat"),
                 (0, "  ⚠ TST77 ARE deja AWB (80000000077) — folosește awb-regen"), (0, "  TST77 · AWB 80000000077")]
check("cod_paid_watch: eticheta de după e tot cea veche → nu e raportată ca refăcută", CPW.recall("TST77", SHOP) is None
      and len(APELURI) == 3, APELURI)
del APELURI[:]
RASPUNSURI[:] = [(0, "  AWB void · TST77 · connector 5 · tracking 80000000077\n  ✅ anulat"),
                 (3, "  ⛔ awb-make · TST77: Shopify arată AWB viu (80000000077)"), (0, "  ✅ AWB creat"),
                 (0, "  TST77 · AWB 80000000078")]
nou = CPW.recall("TST77", SHOP)
check("cod_paid_watch: Shopify arată încă eticheta anulată → awb-make se reîncearcă și eticheta nouă iese",
      nou == "80000000078" and [a[2] for a in APELURI] == ["awb-void", "awb-make", "awb-make", "links"], (nou, APELURI))
del APELURI[:]
RASPUNSURI[:] = [(2, "  ⛔ awb-void refuzat, nu s-a scris nimic")]
check("cod_paid_watch: awb-void refuzat (cod ≠ 0) → fără awb-make după el", CPW.recall("TST77", SHOP) is None
      and len(APELURI) == 1, APELURI)
del APELURI[:]
check("cod_paid_watch: Order Hub zice că coletul a plecat (AWBprint e în urmă) → ca la plecat, nu „o reface el”",
      CPW.recall("TST5", SHOP) == "PLECAT-skip" and not APELURI, APELURI)
check("cod_paid_watch: comandă a Order Hub fără etichetă vie → spus pe nume, nu „o reface el”",
      CPW.recall("TST8", SHOP) == "ORDER-HUB:fara_awb" and not APELURI, APELURI)
del APELURI[:]
OH_RASPUNS[0] = (500, "x")
fara = [CPW.recall("TST1", SHOP), CPW.recall("TST77", SHOP)]
OH_RASPUNS[0] = None
check("cod_paid_watch: fără răspunsul Order Hub nu se atinge nimic, iar motivul se spune",
      all(str(x).startswith("OH-FARA-RASPUNS:") and "HTTP 500" in x for x in fara) and not APELURI, (fara, APELURI))
EMAILURI = []
originale = (CPW.recall, CPW.detect, CPW.classify, CPW.send_email)
CPW.detect = lambda since: [dict(shop=SHOP, name=n, created="", total="10", cur="RON", fulfil="UNFULFILLED", gw=["ramburs"])
                            for n in ("TST1", "TST5", "TST8", "TST77", "TST78", "TST9")]
CPW.classify = lambda h: h.update(ship="created_awb") or "RECUPERABIL"
CPW.recall = lambda name, shop: {"TST1": "ORDER-HUB", "TST5": "PLECAT-skip", "TST8": "ORDER-HUB:fara_awb",
                                 "TST77": "OH-FARA-RASPUNS:HTTP 500: x", "TST78": "80000000078", "TST9": None}[name]
CPW.send_email = lambda to, sender, subject, body: EMAILURI.append(body)
os.environ["COD_SEEN_FILE"] = os.path.join(tempfile.gettempdir(), "cod_paid_seen_test_%d.json" % os.getpid())
sys.argv = ["cod_paid_watch.py", "--apply", "--recall", "--email", "cs@aronagroup.ro"]
with redirect_stdout(io.TextIOWrapper(io.BytesIO(), encoding="utf-8")):   # main() își reconfigurează stdout-ul
    CPW.main()
corp = EMAILURI[0] if EMAILURI else ""
check("cod_paid_watch: emailul spune pe fiecare comandă ce s-a întâmplat, iar ce n-a refăcut nu apare ca refăcut",
      len(EMAILURI) == 1 and corp.count("AWB refăcut") == 1 and "AWB refăcut 80000000078" in corp
      and "TST1 (" in corp and "o tratează el" in corp and "a plecat între timp" in corp and "(fara_awb)" in corp
      and "HTTP 500: x" in corp and "refacere EȘUATĂ" in corp, corp)
try:
    os.remove(os.environ.pop("COD_SEEN_FILE"))
except OSError:
    pass
CPW.recall, CPW.detect, CPW.classify, CPW.send_email = originale
CPW._dispatched_live = lambda name, shop: True
del EVENIMENTE[:]
check("cod_paid_watch: colet plecat între timp → nici măcar nu întreabă", CPW.recall("TST1", SHOP) == "PLECAT-skip"
      and not oh() and not APELURI, (oh(), APELURI))

print("11. uneltele MCP")
for k in ("DATABASE_URL_AWBPRINT", "DATABASE_URL_METRICS"):
    os.environ.setdefault(k, "postgresql://test")
fals = types.ModuleType("mcp.server.fastmcp")
fals.FastMCP = lambda nume: types.SimpleNamespace(tool=lambda: (lambda f: f), run=lambda *a, **k: None)
sys.modules.update({"mcp": types.ModuleType("mcp"), "mcp.server": types.ModuleType("mcp.server"), "mcp.server.fastmcp": fals})
M = incarca("mcp_server", os.path.join(AICI, "mcp_server.py"))
VAZUT = []


def fake_run_mcp(args, **k):
    VAZUT.append(k)
    if "awb-void" in args:   # pe Windows `uv run` nu-și oprește copilul: ieșirea vine cu excepția
        raise subprocess.TimeoutExpired(args, k.get("timeout"), output="  ✅ OPRITĂ (hold)\n")
    return types.SimpleNamespace(returncode=3, stdout="  ═══ ⛔ Order Hub: plecat\n", stderr="")


subprocess.run = fake_run_mcp
text = M._run(M.XC, ["order-cancel", "--order", "TST1"])
check("_run citește ieșirea ca UTF-8 la ambele capete și spune codul de ieșire", VAZUT[0].get("encoding") == "utf-8"
      and VAZUT[0].get("errors") == "replace" and VAZUT[0]["env"].get("PYTHONIOENCODING") == "utf-8"
      and "⛔ Order Hub: plecat" in text and "[cod de ieșire 3]" in text, (VAZUT, text))
text = M._run(M.XC, ["awb-void", "--order", "TST1", "--apply"], timeout=300)
check("_run: o comandă trecută de timpul limită: ce a apucat să spună + „poate executată”, nu „oprită”",
      "POATE să fi fost executată" in text and "300" in text and "OPRITĂ (hold)" in text and "oprită." not in text, text)
subprocess.run = fara_retea
RULARI = []
M._run = lambda script, args, timeout=180: RULARI.append((os.path.basename(script), list(args), timeout)) or "ok"
M.xc_order_cancel("TST1", apply=True, agent="Raluca", motiv="client")
M.xc_awb_void("TST1")
M.xc_awb_regen("TST1", parcels=2, awb="70000000001", apply=True, agent="Raluca")
M.xc_awb_make("TST1")
check("xc_order_cancel trimite agentul și motivul", RULARI[0][1] == [
    "order-cancel", "--order", "TST1", "--apply", "--agent", "Raluca", "--motiv", "client"], RULARI[0])
check("xc_awb_void fără agent: fără --agent gol, fără --apply", RULARI[1][1] == ["awb-void", "--order", "TST1"], RULARI[1])
check("xc_awb_regen există și trimite coletele și eticheta", RULARI[2][1] == [
    "awb-regen", "--order", "TST1", "--parcels", "2", "--awb", "70000000001", "--apply", "--agent", "Raluca"], RULARI[2])
check("uneltele care trec prin Order Hub (și xc_awb_make) au timp peste cele 90 s ale cererii", len(RULARI) == 4
      and all(r[2] >= 300 for r in RULARI), RULARI)

print("12. oh_client")
r = OH._cere("/api/depozit/anulare", {"dry_run": True}, CHEIE, http=lambda *a: (200, json.dumps({"detail": "x"})))
check("răspuns 200 fără `rezultat` = eroare, nu hotărâre", r.stare == OH.EROARE and not r.ok and not r.incert)
check("fără cheie nu pleacă nicio cerere", OH.anulare("X", "u", "m", http=fara_retea).stare == OH.FARA_CHEIE)
check("cheie cu rând nou: nicio cerere, nimic din ea în mesaj", OH.anulare("X", "u", "m", token="ohsvc.cs.abc\nxyz" + "x" * 40,
      http=fara_retea).stare == OH.FARA_CHEIE and "abc" not in OH.anulare("X", "u", "m", token="ohsvc.cs.abc\nxyz" + "x" * 40, http=fara_retea).mesaj)
check("adresa Order Hub e fixă: OH_BASE_URL din mediu nu o schimbă", OH.BASE == "https://orderhub.arona.ro"
      and "OH_BASE_URL" not in io.open(os.path.join(AICI, "oh_client.py"), encoding="utf-8").read().replace("OH_BASE_URL din", ""))
vazut = []
tine_minte = lambda m, u, h, b, t: vazut.append((u, b, t)) or (404, json.dumps({"detail": "Not Found"}))  # noqa: E731
OH.stie("TST1", "u", token=CHEIE, http=tine_minte)
OH.eticheta("TST1", "u", token=CHEIE, http=tine_minte)
OH.anulare("TST1", "u", "m", aplica=True, token=CHEIE, http=tine_minte)
check("stie = proba unei refaceri, niciodată execuție", vazut[0][0].endswith("/api/depozit/refa") and vazut[0][1]["dry_run"] is True
      and vazut[0][1]["comanda"] == vazut[0][1]["cod"] == "TST1")
check("eticheta = proba unei opriri, niciodată execuție", vazut[1][0].endswith("/api/depozit/anulare")
      and vazut[1][1]["actiune"] == "hold" and vazut[1][1]["dry_run"] is True and vazut[1][1]["motiv"], vazut[1])
pe_awb = {"70000000001": {"ok": True, "rezultat": "previzualizare", "plan": []},
          "80000000002": {"ok": False, "rezultat": "eticheta_necunoscuta", "plan": []}}
nec, nev = OH.alte_etichete("TST1", "u", ["70000000009", "70000000001", "80000000002", "80000000003"], token=CHEIE,
                            http=lambda m, u, h, b, t: (200, json.dumps(pe_awb[b["awb"]])) if b["awb"] in pe_awb else (500, "x"))
check("alte_etichete: prima nu se verifică (pleacă în cerere), apoi necunoscutele și neverificatele, doar cu probe",
      nec == ["80000000002"] and nev == ["80000000003"], (nec, nev))
check("o probă așteaptă 30 s, o execuție 90 s", [v[2] for v in vazut] == [30, 30, 90], [v[2] for v in vazut])
r = OH.Raspuns(OH.DECIS, 200, {"ok": True, "rezultat": "previzualizare", "plan": ["AWB de anulat: niciunul viu"]})
check("„AWB de anulat: niciunul viu” nu e un AWB", OH.awb_vii(r) == [])


class _HttpsFals(urllib.request.HTTPSHandler):
    """Ține locul rețelei sub transportul adevărat al oh_client: răspunsuri la alegere, cu cererile ținute minte."""
    cereri, raspuns = [], None

    def https_open(self, req):
        _HttpsFals.cereri.append((req.full_url, req.get_header("Authorization")))
        cod, antete, corp = _HttpsFals.raspuns
        h = email.message.Message()
        for k, v in antete.items():
            h[k] = v
        r = urllib.response.addinfourl(corp, h, req.full_url, cod)
        r.msg = "x"
        return r


class _CorpRupt(io.BytesIO):
    def read(self, *a):
        raise http.client.IncompleteRead(b"")


build_opener_real = urllib.request.build_opener
urllib.request.build_opener = lambda *h: build_opener_real(*h, _HttpsFals)
try:
    _HttpsFals.raspuns = (302, {"Location": "https://alt-server.example/api/depozit/anulare"}, io.BytesIO(b""))
    s, b = OH_HTTP_REAL("POST", OH.BASE + "/api/depozit/anulare", {"Authorization": "Bearer " + CHEIE}, {"cod": "X"})
    check("un redirect nu e urmat: o singură cerere, cheia nu pleacă la altă adresă", s == 302
          and [c[0] for c in _HttpsFals.cereri] == [OH.BASE + "/api/depozit/anulare"], (s, _HttpsFals.cereri))
    del _HttpsFals.cereri[:]
    _HttpsFals.raspuns = (404, {}, io.BytesIO(json.dumps({"detail": {"rezultat": "necunoscuta"}}).encode()))
    OH.stie("TST1", "u", token=CHEIE, http=OH_HTTP_REAL)
    check("cu adrese străine în mediu (%s), cererea pleacă tot la orderhub.arona.ro" % ", ".join(ADRESE_STRAINE),
          [c[0] for c in _HttpsFals.cereri] == ["https://orderhub.arona.ro/api/depozit/refa"], _HttpsFals.cereri)
    _HttpsFals.raspuns = (502, {}, _CorpRupt())
    s, b = OH_HTTP_REAL("POST", OH.BASE + "/api/depozit/anulare", {"Authorization": "Bearer " + CHEIE}, {"cod": "X"})
    check("eroare HTTP cu corpul necitit: rămâne codul, fără excepție", s == 502 and "corp necitit" in b, (s, b))
    r = OH._cere("/api/depozit/anulare", {"cod": "X", "dry_run": False}, CHEIE, http=OH_HTTP_REAL)
    check("…iar pe o cerere de execuție starea e „poate executată”, nu un refuz", r.stare == OH.EROARE and r.incert, r.mesaj)
finally:
    urllib.request.build_opener = build_opener_real

print("13. --shop: secretele app-urilor Shopify nu pleacă la o gazdă străină")
CERERI_HTTP = []


def tine_minte_http(method, url, headers, body=None, timeout=45):
    CERERI_HTTP.append(url)
    return 400, json.dumps({"errors": "app_not_installed"})


KB.update({k: "FAKE-" + k for pereche in X._SHOPIFY_APPS for k in pereche})
X.http = tine_minte_http
for gazda in ("attacker.example", "6f9e22-9d.myshopify.com.evil.net"):
    picate = []
    for cmd in (["order-cancel", "--order", "TST1"], ["awb-void", "--order", "TST1"],
                ["awb-regen", "--order", "TST1", "--parcels", "2", "--awb", "70000000001"], ["awb-make", "--order", "TST8"]):
        for aplica in ([], ["--apply"]):
            del CERERI_HTTP[:]
            cod, out = ruleaza(*(cmd + ["--shop", gazda] + aplica))
            if CERERI_HTTP or cod != 2 or not fara_scrieri() or not doar_probe() or "nu e un magazin cunoscut" not in out:
                picate.append("%s%s: cod %s, cereri %s" % (cmd[0], " --apply" if aplica else "", cod, CERERI_HTTP))
            elif cmd[0] != "awb-make" and oh():
                picate.append("%s: a plecat o cerere la Order Hub" % cmd[0])
    check("--shop %s: order-cancel / awb-void / awb-regen / awb-make (și probă, și --apply) — nicio emitere de token, cod"
          " 2, nimic scris" % gazda, not picate, "; ".join(picate))
del CERERI_HTTP[:]
emise = [X._shopify_mint("attacker.example"), X._shopify_mint("6f9e22-9d.myshopify.com.evil.net"),
         X._shopify_mint("6f9e22-9d.myshopify.com\n"), X._mint_din_marcaj("attacker.example", "OAUTH:SHOPIFY_ARONA_CLIENT_ID+SECRET"),
         X._token_viu_sau_emis("6f9e22-9d.myshopify.com.evil.net", None)[0]]
check("a doua gardă: _shopify_mint / _mint_din_marcaj refuză orice gazdă care nu e <magazin>.myshopify.com, înainte de"
      " orice cerere", emise == [None] * 5 and not CERERI_HTTP, (emise, CERERI_HTTP))
del CERERI_HTTP[:]
cod, out = ruleaza("order-cancel", "--order", "TST1", "--shop", "6f9e22-9d.myshopify.com")
check("control: un magazin cunoscut (din PREFIX_DOMAIN) e acceptat ca --shop, iar emiterea merge doar la el",
      "nu e un magazin cunoscut" not in out and len(CERERI_HTTP) == len(X._SHOPIFY_APPS)
      and all(u == "https://6f9e22-9d.myshopify.com/admin/oauth/access_token" for u in CERERI_HTTP), (CERERI_HTTP, out))
X.http = fara_retea
KB.clear()

check("niciun subprocess și nicio ieșire în rețea pe lângă dubluri", not SUBPROC, SUBPROC)

print()
if FAILS:
    print("PICATE: %d — %s" % (len(FAILS), "; ".join(FAILS)))
    sys.exit(1)
print("TOATE TREC")
