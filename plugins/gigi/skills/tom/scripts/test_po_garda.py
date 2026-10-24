# /// script
# dependencies = []
# ///
"""Teste pentru garda de PO — pure, fara baze de date: verific DOAR regulile, cu date puse de mana."""
import importlib.util, pathlib, sys

spec = importlib.util.spec_from_file_location("po_garda", pathlib.Path(__file__).with_name("po_garda.py"))
g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g)

FOND = {
    "HA-PLIN":  {"stoc": 3000, "pe_drum": 0, "viteza30": 100, "nume": "produs plin", "componenta": False},
    "HA-BUN":   {"stoc": 100,  "pe_drum": 0, "viteza30": 200, "nume": "produs rapid", "componenta": False},
    "HA-NOU":   {"stoc": 0,    "pe_drum": 0, "viteza30": 0,   "nume": "produs nou", "componenta": False},
    "EST-CUTIE":{"stoc": 0,    "pe_drum": 0, "viteza30": 0,   "nume": "ambalaj", "componenta": True},
    "HA-VARA":  {"stoc": 0,    "pe_drum": 0, "viteza30": 50,  "nume": "sezlong de gradina", "componenta": False},
}
g.date_de_fond = lambda skus: {s: FOND.get(s.upper(), {}) for s in skus}
import datetime
NOIEMBRIE = datetime.date(2026, 11, 15)
IUNIE = datetime.date(2026, 6, 15)
MAI = datetime.date(2026, 5, 15)

def ruleaza(linii, azi=MAI, blacklist=()):
    return {r["sku"]: r for r in g.verifica(linii, azi=azi, blacklist=blacklist)}

def test(nume, cond):
    print(("ok   " if cond else "PICAT") + "  " + nume)
    return cond

bun = True
r = ruleaza([{"sku": "HA-PLIN", "qty": 1000}])["HA-PLIN"]
bun &= test("stoc plin peste 4 luni = blocat", r["blocat"] and "luni de stoc" in r["motive"][0])

r = ruleaza([{"sku": "HA-BUN", "qty": 400}])["HA-BUN"]
bun &= test("produs rapid, sub 500 buc = trece", not r["blocat"])

r = ruleaza([{"sku": "HA-BUN", "qty": 600}])["HA-BUN"]
bun &= test("600 buc pe un produs rapid (3 luni) = trece, dar cu avertisment",
            not r["blocat"] and any("te bazezi pe ritmul" in a for a in r["avertismente"]))

r = ruleaza([{"sku": "HA-BUN", "qty": 5000}])["HA-BUN"]
bun &= test("5000 buc = blocat (si marime, si luni de stoc)", r["blocat"])

r = ruleaza([{"sku": "EST-CUTIE", "qty": 300000}])["EST-CUTIE"]
bun &= test("ambalajul nu e judecat nici pe numarul de bucati", not r["blocat"])

r = ruleaza([{"sku": "HA-NOU", "qty": 1000}])["HA-NOU"]
bun &= test("produs nou peste testul de 300 = blocat", r["blocat"])

r = ruleaza([{"sku": "HA-NOU", "qty": 250}])["HA-NOU"]
bun &= test("produs nou sub 300 (test) = trece", not r["blocat"])

r = ruleaza([{"sku": "EST-CUTIE", "qty": 300000}])["EST-CUTIE"]
bun &= test("ambalajul nu se judeca pe viteza", not r["blocat"])

r = ruleaza([{"sku": "HA-VARA", "qty": 100}], azi=NOIEMBRIE)["HA-VARA"]
bun &= test("produs de vara comandat in noiembrie = blocat", r["blocat"])

MARTIE = datetime.date(2026, 3, 15)
r = ruleaza([{"sku": "HA-VARA", "qty": 100}], azi=MARTIE)["HA-VARA"]
bun &= test("acelasi produs comandat in martie = trece", not r["blocat"])

r = ruleaza([{"sku": "HA-BUN", "qty": 100}], blacklist={"HA-BUN"})["HA-BUN"]
bun &= test("blacklist = blocat", r["blocat"])

r = ruleaza([{"sku": "HA-BUN", "qty": 100, "refuz": 35.0}])["HA-BUN"]
bun &= test("refuz 35% = blocat", r["blocat"])

r = ruleaza([{"sku": "HA-PLIN", "qty": 1000, "valoare_lei": 25000}])["HA-PLIN"]
bun &= test("linie de 25.000 lei pe produs lent = avertisment de valoare",
            r["blocat"] and any("25.000 lei" in a for a in r["avertismente"]))

r = ruleaza([{"sku": "HA-BUN", "qty": 400, "valoare_lei": 90000}])["HA-BUN"]
bun &= test("valoare mare pe produs rapid = fara avertisment de valoare",
            not any(" lei pe un produs" in a for a in r["avertismente"]))

print("\n%s" % ("toate testele trec" if bun else "SUNT TESTE PICATE"))
sys.exit(0 if bun else 1)
