# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Test de regresie: garda OH pe facturare (30-sep-2026).

Pe 30-sep-2026 un `inv-bulk --shop all --days 60 --apply` pornit de pe o stație, cu copia din plugin (fără garda OH,
care trăia doar pe VPS), a emis ~2.780 de facturi xConnector, dintre care 2.068 DUBLE peste facturile Order Hub.
Testul verifică, FĂRĂ nicio cerere de rețea (xConnector, Shopify și DPD sunt înlocuite cu dubluri în memorie)
și trecând prin `main()` — adică exact pe drumul unei comenzi reale din linia de comandă:
  1. `oh_guard_motiv` + citirea stării DPD (inclusiv „Delivered Back to Sender" = întors, nu livrat);
  2. `inv-bulk --apply` se refuză în afara VPS-ului (cod 2, zero apeluri), cu ocolire DOAR prin XC_INV_BULK_PERMIS=1;
  3. `inv-bulk` facturează numai comenzile cu eticheta vie xConnector, livrate; sare tagul factura-oh, tagurile
     necitite, eticheta vie străină și coletele întoarse — și cu --force;
  4. `inv-make` / `inv-regen` refuză aceleași comenzi (și cu --force), iar pe o comandă xConnector merg normal.
Comenzile, AWB-urile și magazinul sunt inventate.

  uv run test_garda_oh.py
"""
import datetime
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout

AICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AICI)

import xconnector as X  # noqa: E402

FAILS = []


def check(nume, ok, extra=""):
    print(("  ok   " if ok else "  PICAT ") + nume + (("  — " + extra) if extra else ""))
    if not ok:
        FAILS.append(nume)


# ── lumea de test: un magazin, comenzi TST*, AWB-uri 8… (eticheta xConnector) și 7… (eticheta OH) ──
SHOP = "garda-test.myshopify.com"
ACUM = datetime.datetime.now(datetime.timezone.utc)
ACUM_3Z = ACUM - datetime.timedelta(days=3)
ACUM_2H = ACUM - datetime.timedelta(hours=2)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_dpd(dt):
    return dt.astimezone(datetime.timezone(datetime.timedelta(hours=3))).strftime("%Y-%m-%dT%H:%M:%S+0300")


def eticheta(awb, curier="DPD Romania"):
    return {"documentType": "SHIPPING_LABEL", "trackingNumber": awb, "connectorName": curier}


FACTURA_XC = {"documentType": "INVOICE", "name": "TEST 1"}


def nod(oid, name, tags=(), tracking=(), platita=True, fara=()):
    n = {"id": "gid://shopify/Order/%s" % oid, "name": name, "cancelledAt": None, "test": False,
         "tags": list(tags), "paymentGatewayNames": ["Cash on Delivery (COD)"],
         "displayFinancialStatus": "PAID" if platita else "PENDING",
         "currentTotalPriceSet": {"shopMoney": {"amount": "149.90"}},
         "totalRefundedSet": {"shopMoney": {"amount": "0.0"}},
         "transactions": [{"kind": "SALE", "status": "SUCCESS", "processedAt": iso(ACUM_3Z)}],
         "fulfillments": ([{"status": "SUCCESS", "trackingInfo": [{"number": t} for t in tracking]}]
                          if tracking else [])}
    for k in fara:
        n.pop(k, None)   # câmp necitit (acces lipsă / răspuns parțial)
    return n


# (nume, orderId, documente xConnector, nod Shopify)
COMENZI = [
    # forma incidentului: AWB făcut și facturat de OH, fără etichetă xConnector, tag factura-oh
    ("TST1001", "9100000001", [], nod("9100000001", "TST1001", ["factura-oh"], ["70000000001"])),
    # a xConnector-ului: etichetă DPD vie, livrată de 3 zile → SINGURA care se facturează (cu TST1008)
    ("TST1002", "9100000002", [eticheta("80000000002")], nod("9100000002", "TST1002", ["cod"], ["80000000002"])),
    # taguri necitite → neverificată → sare
    ("TST1003", "9100000003", [eticheta("80000000003")], nod("9100000003", "TST1003", [], ["80000000003"], fara=("tags",))),
    # tag OH scris altfel (majuscule + sufix) pe o comandă altfel facturabilă → sare
    ("TST1004", "9100000004", [eticheta("80000000004")], nod("9100000004", "TST1004", ["Factura-OH-manual"], ["80000000004"])),
    # eticheta xConnector există, dar eticheta VIE din Shopify e alta (a OH) → sare
    ("TST1005", "9100000005", [eticheta("80000000005")], nod("9100000005", "TST1005", [], ["70000000005"])),
    # a xConnector-ului, dar DPD: Delivered Back to Sender (124) → garda de stare sare
    ("TST1006", "9100000006", [eticheta("80000000006")], nod("9100000006", "TST1006", [], ["80000000006"])),
    # fulfillment-uri necitite → neverificată → sare
    ("TST1007", "9100000007", [eticheta("80000000007")], nod("9100000007", "TST1007", [], ["80000000007"], fara=("fulfillments",))),
    # etichetă cu 2 colete „<AWB>-<colet>"; Shopify le listează separat → e a xConnector-ului → se facturează
    ("TST1008", "9100000008", [eticheta("80000000008-80000000009")],
     nod("9100000008", "TST1008", [], ["80000000008", "80000000009"])),
    # factură OH manuală fără tag, dată prin env XC_OH_FACTURATE → sare
    ("TST1009", "9100000009", [eticheta("80000000019")], nod("9100000009", "TST1009", [], ["80000000019"])),
    # a xConnector-ului, cu factură xConnector deja → inv-bulk n-o atinge; inv-regen o reface
    ("TST1010", "9100000010", [eticheta("80000000010"), FACTURA_XC], nod("9100000010", "TST1010", [], ["80000000010"])),
    # dublura din incident: factură xConnector peste una OH (tag factura-oh, AWB OH) → inv-regen refuză
    ("TST1011", "9100000011", [FACTURA_XC], nod("9100000011", "TST1011", ["factura-oh"], ["70000000011"])),
    # livrată acum 2 ore → xConnector o poate factura singur → garda de stare o lasă pe tura următoare
    ("TST1013", "9100000013", [eticheta("80000000013")], nod("9100000013", "TST1013", [], ["80000000013"])),
]
DPD = {  # awb -> (cod operație, descriere, când)
    "80000000002": (-14, "Delivered", ACUM_3Z),
    "80000000006": (124, "Delivered Back to Sender", ACUM_3Z),
    "80000000008": (-14, "Delivered", ACUM_3Z),
    "80000000010": (-14, "Delivered", ACUM_3Z),
    "80000000013": (-14, "Delivered", ACUM_2H),
}
XC_ORDERS = [{"orderName": nm, "orderId": oid, "documents": docs, "dispatched": True} for nm, oid, docs, _ in COMENZI]
SHOPIFY = {oid: n for _, oid, _, n in COMENZI}
OID = {nm: oid for nm, oid, _, _ in COMENZI}

POSTS, GQL, INST = [], [], []
TOKENURI = [{"prefix": "TST", "shopDomain": SHOP, "adminToken": "tok-test"}]


class FakeXC:
    def __init__(self, apikey):
        INST.append(apikey)

    def orders(self, dfrom, dto, filters=None):
        return [dict(o) for o in XC_ORDERS]

    def list_connectors(self):
        return [{"id": 77, "type": "SMART_BILL", "active": True, "name": "SmartBill test"}]

    def by_id(self, oid):
        return {}

    def post(self, path, body):
        POSTS.append((path, dict(body)))
        if path.endswith("/create-invoice"):
            return 200, {"accepted": True, "invoices": [{"success": True, "invoiceSerie": "TEST",
                                                          "invoiceNumber": str(len(POSTS))}]}
        return 200, {"accepted": True}


def fake_gql(shop, token, query, variables=None):
    GQL.append(query)
    if "nodes(ids:" not in query:
        raise AssertionError("query Shopify neașteptat: %s" % query[:80])
    return {"data": {"nodes": [SHOPIFY.get(str(g).rsplit("/", 1)[-1]) for g in (variables or {}).get("ids", [])]}}


def fake_http(method, url, headers, body=None, timeout=45):
    if url != "https://api.dpd.ro/v1/track":
        raise AssertionError("rețea neașteptată: %s %s" % (method, url))
    parcels = []
    for p in body["parcels"]:
        st = DPD.get(p["id"])
        if st is None:
            parcels.append({"parcelId": p["id"], "error": {"message": "not found"}})
            continue
        cod, desc, cand = st
        parcels.append({"parcelId": p["id"], "operations": [
            {"operationCode": cod, "description": desc, "dateTime": iso_dpd(cand)}]})
    return 200, json.dumps({"parcels": parcels})


def fara_retea(*a, **k):
    raise AssertionError("subprocess neașteptat (KB?)")


X.XC = FakeXC
X.load_shops = lambda: [{"shopDomain": SHOP, "apiKey": "k-test"}]
X.load_shopify_tokens = lambda: [dict(t) for t in TOKENURI]
X.shopify_gql = fake_gql
X.http = fake_http
X._dpd_creds = lambda: ("u-test", "p-test")
X._kb_secret = lambda key: ("", False)
X.subprocess.run = fara_retea
X.time.sleep = lambda s: None
PE_REAL = X._pe_vps_facturare   # detectarea REALĂ (testată separat, pe un marcaj temporar)
PE_VPS = [False]
X._pe_vps_facturare = lambda: PE_VPS[0]
for k in ("XC_INV_BULK_PERMIS", "XC_INV_EXCLUDE", "XC_INV_ASTEPTARE_ORE", "XC_INV_GARDA_TSV"):
    os.environ.pop(k, None)
os.environ["XC_OH_FACTURATE"] = "TST1009"


def ruleaza(*args, permis=None, pe_vps=False):
    """main() cu argumentele date → (cod_ieșire, stdout). Golește jurnalele de apeluri înainte."""
    del POSTS[:], GQL[:], INST[:]
    PE_VPS[0] = pe_vps
    if permis is None:
        os.environ.pop("XC_INV_BULK_PERMIS", None)
    else:
        os.environ["XC_INV_BULK_PERMIS"] = permis
    sys.argv = ["xconnector.py"] + list(args)
    buf, cod = io.StringIO(), 0
    with redirect_stdout(buf):
        try:
            X.main()
        except SystemExit as e:
            cod = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    return cod, buf.getvalue()


def facturate():
    return sorted(b["orderId"] for p, b in POSTS if p.endswith("/create-invoice"))


def main():
    print("1. garda OH pe o comandă (oh_guard_motiv)")
    ok = {"tags": [], "fulfillments_ok": True, "tracking": {"80000000002"}}
    xc = X._trk_set("80000000002")
    check("eticheta xConnector vie → se poate factura", X.oh_guard_motiv(ok, True, xc, "TST1") is None)
    check("taguri necitite → sare", X.oh_guard_motiv(dict(ok, tags=None), True, xc, "TST1") == "taguri_necitite")
    check("fulfillment-uri necitite → sare",
          X.oh_guard_motiv(dict(ok, fulfillments_ok=False), True, xc, "TST1") == "taguri_necitite")
    check("s_ gol (citire picată) → sare", X.oh_guard_motiv({"tags": None}, True, xc, "TST1") == "taguri_necitite")
    check("tag factura-oh → sare", X.oh_guard_motiv(dict(ok, tags=["factura-oh"]), True, xc, "TST1") == "tag_factura_oh")
    check("tag ' Factura-OH-manual' (majuscule, sufix) → sare",
          X.oh_guard_motiv(dict(ok, tags=[" Factura-OH-manual"]), True, xc, "TST1") == "tag_factura_oh")
    check("tag care doar CONȚINE factura-oh nu e al OH",
          X.oh_guard_motiv(dict(ok, tags=["nu-factura-oh"]), True, xc, "TST1") is None)
    check("fără etichetă xConnector → sare", X.oh_guard_motiv(ok, False, set(), "TST1") == "fara_eticheta_xc")
    check("etichetă fără număr → sare", X.oh_guard_motiv(ok, True, set(), "TST1") == "tracking_xc_necitit")
    check("eticheta vie din Shopify e alta → sare",
          X.oh_guard_motiv(dict(ok, tracking={"70000000005"}), True, xc, "TST1") == "eticheta_vie_nu_e_xc")
    check("Shopify fără tracking + etichetă xConnector → se poate factura",
          X.oh_guard_motiv(dict(ok, tracking=set()), True, xc, "TST1") is None)
    check("colete „<AWB>-<colet>” se potrivesc pe numere",
          X.oh_guard_motiv(dict(ok, tracking=X._trk_set("80000000009")), True,
                           X._trk_set("80000000008-80000000009"), "TST1") is None)
    check("bucățile scurte („1”) nu intră în potrivire", "1" not in X._trk_set("80000000008-1"))
    check("factură OH manuală (listă) → sare, indiferent de majuscule",
          X.oh_guard_motiv(ok, True, xc, "tst1009", {"TST1009"}) == "factura_oh_manuala")
    salvat, kb = os.environ.pop("XC_OH_FACTURATE", None), X._kb_secret
    X._kb_secret = lambda key: ("TST2001, tst2002", True) if key == "XC_OH_FACTURATE" else ("", False)
    X._OH_FACTURATE_KB = None
    try:
        check("facturi OH manuale: fără env, lista vine din secretul KB",
              {"TST2001", "TST2002"} <= X._oh_facturate_fara_tag())
        os.environ["XC_OH_FACTURATE"] = "TST2003"
        check("facturi OH manuale: env-ul are întâietate", X._oh_facturate_fara_tag() == {"TST2003"})
    finally:
        X._kb_secret, X._OH_FACTURATE_KB = kb, None
        os.environ["XC_OH_FACTURATE"] = salvat or "TST1009"

    print("2. starea DPD (returul înaintea livrării)")
    check("„Delivered Back to Sender” = întors", X._dpd_state("Delivered Back to Sender") == "refused")
    check("„Delivered” = livrat", X._dpd_state("Delivered") == "delivered")
    check("„Administrative Closure” = în curs", X._dpd_state("Administrative Closure") == "progress")
    check("cod 124 = întors", X._dpd_op_state(124, "Delivered Back to Sender") == "refused")
    check("cod -14 = livrat", X._dpd_op_state(-14, "Delivered") == "delivered")
    check("„Delivered” fără cod = nesigur", X._dpd_op_state(None, "Delivered") == "unknown")
    check("cod 129 = nesigur", X._dpd_op_state(129, "Administrative Closure") == "unknown")

    print("3. inv-bulk --apply doar pe VPS")
    cod, out = ruleaza("inv-bulk", "--apply", "--force")
    check("în afara VPS-ului: refuz cu cod 2", cod == 2, "cod=%s" % cod)
    check("refuzul spune de ce și cum se ocolește", "REFUZ" in out and "2.068" in out and "XC_INV_BULK_PERMIS=1" in out)
    check("refuzul vine înaintea oricărui apel (xConnector/Shopify/facturi)", not INST and not GQL and not POSTS,
          "xc=%d gql=%d post=%d" % (len(INST), len(GQL), len(POSTS)))
    cod, out = ruleaza("inv-bulk", "--apply", permis="yes")
    check("XC_INV_BULK_PERMIS=yes nu ocolește (doar =1)", cod == 2 and not POSTS)
    cod, out = ruleaza("inv-bulk")
    check("dry-run-ul merge și în afara VPS-ului, fără nicio factură", cod == 0 and not POSTS and "DRY factură" in out)
    with tempfile.TemporaryDirectory() as d:
        marcaj, real = os.path.join(d, ".env.xconnector"), X.VPS_FACTURARE_ENV
        open(marcaj, "w").close()
        X.VPS_FACTURARE_ENV = marcaj
        try:
            check("marcajul VPS (mediul cronului) prezent → VPS", PE_REAL() is True)
            X.VPS_FACTURARE_ENV = os.path.join(d, "lipsa")
            check("marcajul VPS absent → nu e VPS", PE_REAL() is False)
        finally:
            X.VPS_FACTURARE_ENV = real

    print("4. inv-bulk facturează doar comenzile xConnector (garda OH + stare, și cu --force)")
    with tempfile.TemporaryDirectory() as d:
        tsv = os.path.join(d, "garda.tsv")
        cod, out = ruleaza("inv-bulk", "--apply", "--force", "--garda-tsv", tsv, pe_vps=True)
        asteptat = sorted([OID["TST1002"], OID["TST1008"]])
        check("pe VPS: facturează exact TST1002 + TST1008", cod == 0 and facturate() == asteptat,
              "facturate=%s" % facturate())
        check("niciun alt POST în afară de create-invoice", all(p.endswith("/create-invoice") for p, _ in POSTS))
        check("query-ul Shopify cere tagurile și tracking-ul fulfillment-urilor",
              bool(GQL) and all(("tags" in q and "fulfillments(" in q and "trackingInfo" in q) for q in GQL))
        rows = [l.split("\t") for l in open(tsv, encoding="utf-8").read().splitlines()[1:]]
        motiv = {r[1]: r[3] for r in rows}
        check("TSV: tag factura-oh", motiv.get("TST1001") == "tag_factura_oh" and motiv.get("TST1004") == "tag_factura_oh",
              str({k: motiv.get(k) for k in ("TST1001", "TST1004")}))
        check("TSV: taguri / fulfillment-uri necitite",
              motiv.get("TST1003") == "taguri_necitite" and motiv.get("TST1007") == "taguri_necitite")
        check("TSV: eticheta vie nu e a xConnector-ului", motiv.get("TST1005") == "eticheta_vie_nu_e_xc")
        check("TSV: colet întors", motiv.get("TST1006") == "colet_intors")
        check("TSV: factură OH manuală (env XC_OH_FACTURATE)", motiv.get("TST1009") == "factura_oh_manuala")
        check("TSV: livrată de 2 ore → tura următoare", motiv.get("TST1013") == "livrat_recent")
        check("TSV: de facturat = TST1002, TST1008",
              sorted(k for k, v in motiv.items() if v == "de_facturat") == ["TST1002", "TST1008"])
        check("comenzile cu factură xConnector nu sunt atinse", "TST1010" not in motiv and "TST1011" not in motiv)
    cod, out = ruleaza("inv-bulk", "--apply", "--force", permis="1")
    check("XC_INV_BULK_PERMIS=1 în afara VPS-ului: rulează, tot cu gărzile",
          cod == 0 and facturate() == sorted([OID["TST1002"], OID["TST1008"]]), "facturate=%s" % facturate())

    print("5. inv-make / inv-regen: aceeași gardă, --force nu o ocolește")
    for nm, mot in (("TST1001", "tag factura-oh"), ("TST1003", "taguri Shopify necitite"),
                    ("TST1005", "altă etichetă vie"), ("TST1009", "factură OH manuală")):
        cod, out = ruleaza("inv-make", "--order", nm, "--apply", "--force")
        check("inv-make --apply --force %s → refuzat (%s)" % (nm, mot),
              not POSTS and "GARDA OH" in out and mot in out, "posts=%d" % len(POSTS))
    cod, out = ruleaza("inv-make", "--order", "TST1001")
    check("inv-make dry-run pe o comandă OH → anunță refuzul", not POSTS and "GARDA OH" in out)
    cod, out = ruleaza("inv-make", "--order", "TST1002", "--apply")
    check("inv-make --apply pe o comandă xConnector → o factură", facturate() == [OID["TST1002"]] and len(POSTS) == 1,
          "posts=%s" % POSTS)
    TOKENURI[:] = []
    try:
        cod, out = ruleaza("inv-make", "--order", "TST1002", "--apply", "--force")
        check("fără token Shopify → refuzat (fail-closed)", not POSTS and "taguri Shopify necitite" in out)
    finally:
        TOKENURI[:] = [{"prefix": "TST", "shopDomain": SHOP, "adminToken": "tok-test"}]
    cod, out = ruleaza("inv-regen", "--order", "TST1011", "--apply", "--force")
    check("inv-regen --apply --force pe dublura OH → nimic anulat, nimic creat", not POSTS and "GARDA OH" in out,
          "posts=%s" % POSTS)
    cod, out = ruleaza("inv-regen", "--order", "TST1010", "--apply")
    check("inv-regen --apply pe o comandă xConnector → anulează + recreează",
          [p for p, _ in POSTS] == ["/api/actions/cancel-invoice", "/api/actions/create-invoice"], "posts=%s" % POSTS)
    del GQL[:]
    mot, _ = X.garda_oh_comanda({"shopDomain": SHOP}, {"orderName": "TST1002", "orderId": OID["TST1002"]}, "TST1002")
    check("comandă venită prin address-detail (fără documents) → sare", mot == "fara_eticheta_xc", str(mot))

    print()
    if FAILS:
        print("PICATE: %d — %s" % (len(FAILS), ", ".join(FAILS)))
        return 1
    print("toate verificările au trecut")
    return 0


if __name__ == "__main__":
    sys.exit(main())
