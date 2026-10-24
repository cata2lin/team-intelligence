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
