# /// script
# requires-python = ">=3.10"
# dependencies = ["pg8000>=1.30", "paramiko>=3.0"]
# ///
"""Comutarea pe Order Hub: forma rândurilor din coada OH + eroare clară fără cheie (fără rețea, fără producție).
  uv run test_sursa_oh.py"""
import importlib.util, os, sys

AICI = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("g", os.path.join(AICI, "cs_ghost_shipments.py"))
g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g)
esec = []
def check(nume, ok):
    print(("  ok   " if ok else "  FAIL ") + nume)
    if not ok: esec.append(nume)

d = {"ok": True, "pe_motiv": [{"motiv": "culeasa_nepredata", "explicatie": "cules, nepredat"}],
     "comenzi": [
        {"comanda": "GRAND1", "magazin": "Grandia.ro", "zile": 9, "motiv": "culeasa_nepredata",
         "motive": ["culeasa_nepredata", "expediata_in_shopify", "awb_nepreluat"], "awb": ["813"],
         "etichete": [{"awb": "813", "curier": "DPD", "ultim_status": "Shipment data received"}],
         "bani": {"total": 99.5, "moneda": "RON"}},
        {"comanda": "EST2", "magazin": "Esteban", "zile": 5, "motiv": "fara_awb",
         "motive": ["fara_awb", "expediata_in_shopify"], "awb": [], "etichete": [], "bani": {"total": 10, "moneda": "RON"}},
        {"comanda": "GT3", "magazin": "GT", "zile": 4, "motiv": "adresa_invalida",
         "motive": ["adresa_invalida", "fara_awb"], "awb": [], "etichete": [], "bani": {}},
     ]}
r = {x["o"]: x for x in g.build_rows_oh(d)}
check("etichetă vie = fantomă, cu motivul OH ca status", r["GRAND1"]["flag"] == "fantoma" and r["GRAND1"]["status"] == "cules, nepredat")
check("fără etichetă dar expediată în Shopify = lipsă AWB", r["EST2"]["flag"] == "lipsa_awb")
check("adresă invalidă neexpediată nu e colet fantomă", "GT3" not in r)
check("prefixul cel mai lung câștigă (GRAND, nu GRAN)", r["GRAND1"]["prefix"] == "GRAND")
check("acțiunea pe culeasă-nepredată trimite în hală", "hală" in g.suggested_action("fantoma", 9, "culeasa_nepredata"))

# gărzile pe colet din Orders (24-oct): marketplace-urile sărite de coada CS intră, fără dubluri pe comandă
garzi = g.build_rows_garzi([
    ("eticheta_in_teanc", "printată, necules", {"colete": [
        {"comanda": "EMAG000000001", "magazin": "eMAG", "awb": "00000000001", "zile": 3, "plata": "paid",
         "total": 164.99, "moneda": "RON", "statie": "Bartolomeu", "curier": "EMAG"},
        {"comanda": "OLXT00000001", "magazin": "OLX", "awb": "00000000002", "zile": 4, "statie": "Uzina 2"}]}),
    ("culeasa_nepredata", "culeasă", {"colete": [
        {"comanda": "EMAG000000002", "magazin": "eMAG", "awb": "4EMGLN000000001", "zile": 1, "plata": "paid",
         "statie": "Uzina 2"},
        {"comanda": "GRAND1", "magazin": "Grandia.ro", "awb": "813", "zile": 9}]})])
u = {x["o"]: x for x in g.uneste(g.build_rows_oh(d), garzi)}
check("EMAG000000001 (printată, neculeasă, plătită) e fantomă", u["EMAG000000001"]["motiv"] == "eticheta_in_teanc"
      and u["EMAG000000001"]["platita"] is True and "PLĂTITĂ" in u["EMAG000000001"]["status"])
check("OLXT00000001 e fantomă, cu stația", u["OLXT00000001"]["statie"] == "Uzina 2")
check("EMAG000000002 (culeasă, neridicată) e fantomă", u["EMAG000000002"]["motiv"] == "culeasa_nepredata")
check("comanda din ambele surse apare o singură dată", sum(1 for x in g.uneste(g.build_rows_oh(d), garzi)
                                                            if x["o"] == "GRAND1") == 1)
check("filtrul pe magazin se aplică și gărzilor", set(x["o"] for x in g.uneste([], garzi, "eMAG")) ==
      {"EMAG000000001", "EMAG000000002"})

for k in ("OH_TEAM_READ_TOKEN", "OH_PROFIT_CITIRE_TOKEN", "OH_CS_SB_TOKEN"):
    os.environ.pop(k, None)
g.secret = lambda k: ""
try:
    g.fetch_oh(3, ""); check("fără cheie → OHIndisponibil", False)
except g.OHIndisponibil as e:
    check("fără cheie → OHIndisponibil (nu cifra veche)", "cheia" in str(e))
os.environ["OH_PROFIT_CITIRE_TOKEN"] = "x2"; os.environ["OH_CS_SB_TOKEN"] = "x3"
check("ordinea cheilor: OH_PROFIT_CITIRE_TOKEN înaintea OH_CS_SB_TOKEN", g._token_oh() == "x2")

print("TOATE TREC" if not esec else "EȘUATE: %d" % len(esec)); sys.exit(1 if esec else 0)
