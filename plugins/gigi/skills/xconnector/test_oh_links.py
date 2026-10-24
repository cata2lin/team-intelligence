# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Test oh_links.py (links din Order Hub), FĂRĂ rețea: Order Hub e o dublură în memorie.

  1. fișa 200 → status + etichete + linkuri; tracking DPD construit din AWB;
  2. după AWB nu se caută nimic (OH nu primește AWB) → cod 2, nicio cerere;
  3. 401 / 404 / ambiguă / răspuns necitit → cod 1 și eroare clară, fără altă sursă;
  4. cheia: ordinea env OH_TEAM_READ_TOKEN → OH_PROFIT_CITIRE_TOKEN → OH_CS_SB_TOKEN; cheia nu apare în ieșire;
  5. `xconnector.py links` merge implicit pe Order Hub; calea veche doar cu XCONNECTOR_LINKS_SURSA=xc.
"""
import io
import json
import os
import sys
import contextlib

AICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AICI)
import oh_links as L  # noqa: E402

PICATE = []


def check(nume, cond, info=None):
    print(("  ok   " if cond else "  PICAT ") + nume + ("" if cond else "  — %r" % (info,)))
    if not cond:
        PICATE.append(nume)


FISA = {"ok": True, "comanda": "GT000001", "magazin": {"nume": "GT", "domeniu": "test-magazin.myshopify.com"},
        "stare": {"anulata": False, "livrare": "❌ Refuzată", "procesare": "Procesată", "adresa": "valid",
                  "stadiu": "picked", "colete": 1},
        "etichete": [{"awb": "00000000001", "curier": "DPD", "status": "refused", "ultim_status": "Return to Sender",
                      "printata": True}],
        "hold": {"pe_hold": False}, "tichet": None}
CERERI = []


def oh(cod, corp):
    def h(url, cheie):
        CERERI.append((url, cheie))
        return cod, corp if isinstance(corp, str) else json.dumps(corp)
    return h


for k in L.ENV_CHEI:
    os.environ.pop(k, None)
os.environ["OH_TEAM_READ_TOKEN"] = "cheie-secreta-test"

print("1. fișa")
cod, text = L.ruleaza("GT000001", http=oh(200, FISA))
check("cod 0, status + etichetă + tracking DPD", cod == 0 and "❌ Refuzată" in text and "00000000001 · DPD · refused" in text
      and "https://tracking.dpd.ro/?shipmentNumber=00000000001" in text, text)
check("cererea merge la /api/cs/comanda cu numărul, cu cheia din env", CERERI[-1][0].endswith("/api/cs/comanda?comanda=GT000001")
      and CERERI[-1][1] == "cheie-secreta-test", CERERI)
check("cheia nu apare în ieșire", "cheie-secreta-test" not in text)
L.ruleaza("#GT000001", shop="gt", http=oh(200, FISA))
check("# scos, magazinul trimis", "comanda=GT000001&magazin=gt" in CERERI[-1][0], CERERI[-1])

print("2. după AWB")
n = len(CERERI)
cod, text = L.ruleaza(awb="00000000001", http=oh(200, FISA))
check("cod 2, nicio cerere, trimitere la awb_track", cod == 2 and len(CERERI) == n and "awb_track" in text, (cod, text))

print("3. erori")
for nume, raspuns in (("401", (401, {"detail": "Cheie de serviciu: invalidă"})),
                      ("404", (404, {"detail": {"mesaj": "comanda: X · necunoscută în Order Hub"}})),
                      ("ambiguă", (409, {"detail": {"mesaj": "2 comenzi", "candidati": [{"comanda": "A1", "magazin": "M"}]}})),
                      ("necitit", (502, "<html>bad gateway</html>")),
                      ("200 fără ok", (200, {"ok": False}))):
    cod, text = L.ruleaza("X", http=oh(*raspuns))
    check("%s → cod 1, EROARE" % nume, cod == 1 and text.startswith("EROARE") and "cheie-secreta-test" not in text, text)

print("4. ordinea cheilor")
os.environ.pop("OH_TEAM_READ_TOKEN")
os.environ["OH_CS_SB_TOKEN"] = "c3"; os.environ["OH_PROFIT_CITIRE_TOKEN"] = "c2"
check("fără OH_TEAM_READ_TOKEN → OH_PROFIT_CITIRE_TOKEN", L.token() == "c2")
os.environ["OH_TEAM_READ_TOKEN"] = "c1"
check("OH_TEAM_READ_TOKEN are prioritate", L.token() == "c1")

print("5. xconnector.py links")
import types  # noqa: E402
import xconnector as X  # noqa: E402
X.find_by_awb = X.resolve_order = lambda *a, **k: (_ for _ in ()).throw(AssertionError("xConnector atins"))
X.awbprint_status = lambda *a, **k: (_ for _ in ()).throw(AssertionError("AWBprint atins"))
real = L.fisa
L.fisa = lambda comanda, magazin="", cheie=None, http=None: FISA
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    X.cmd_links(types.SimpleNamespace(order="GT000001", awb=None, shop=None, days=60, open=False))
check("implicit Order Hub, xConnector și AWBprint neatinse", "❌ Refuzată" in buf.getvalue(), buf.getvalue())
os.environ["XCONNECTOR_LINKS_SURSA"] = "xc"
try:
    X.cmd_links(types.SimpleNamespace(order="GT000001", awb=None, shop=None, days=60, open=False))
    vechi = False
except AssertionError as e:
    vechi = "xConnector" in str(e)
check("XCONNECTOR_LINKS_SURSA=xc → calea veche (explicit)", vechi)
os.environ.pop("XCONNECTOR_LINKS_SURSA")
L.fisa = real

print("\n" + ("TOATE TREC" if not PICATE else "%d PICATE" % len(PICATE)))
sys.exit(1 if PICATE else 0)
