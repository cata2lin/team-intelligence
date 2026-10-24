# /// script
# dependencies = []
# ///
"""Teste pentru garda de PO — pure, fara baze de date: verific DOAR regulile, cu date puse de mana."""
import datetime, importlib.util, pathlib, sys

spec = importlib.util.spec_from_file_location("po_garda", pathlib.Path(__file__).with_name("po_garda.py"))
g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g)

FOND = {
    "HA-PLIN":  {"stoc": 3000, "pe_drum": 0, "viteza30": 100, "nume": "produs plin", "componenta": False},
    "HA-BUN":   {"stoc": 100,  "pe_drum": 0, "viteza30": 200, "nume": "produs rapid", "componenta": False},
    "HA-NOU":   {"stoc": 0,    "pe_drum": 0, "viteza30": 0,   "nume": "produs nou", "componenta": False},
    "EST-CUTIE":{"stoc": 0,    "pe_drum": 0, "viteza30": 0,   "nume": "ambalaj", "componenta": True},
    "HA-VARA":  {"stoc": 0,    "pe_drum": 0, "viteza30": 50,  "nume": "Șezlong de grădină", "componenta": False},
    "HA-CRACIUN": {"stoc": 0,  "pe_drum": 0, "viteza30": 50,  "nume": "Instalație luminoasă Crăciun", "componenta": False},
    "HA-CADOU": {"stoc": 0,    "pe_drum": 0, "viteza30": 50,  "nume": "Set cadou parfum", "componenta": False},
    "HA-CAT":   {"stoc": 0,    "pe_drum": 0, "viteza30": 50,  "nume": "Decor masa", "categorie": "Crăciun", "componenta": False},
    "HA-GOL":   {"stoc": 0,    "pe_drum": 0, "viteza30": 100, "nume": "produs", "componenta": False},
    "HA-PROASPAT": {"stoc": 0, "pe_drum": 0, "viteza30": 40, "nume": "produs", "componenta": False,
                    "listat_la": datetime.date(2026, 10, 5)},
    "HA-NELISTAT": {"stoc": 0, "pe_drum": 0, "viteza30": 0, "nume": "produs", "componenta": False},
    "HA-VECHI": {"stoc": 0, "pe_drum": 0, "viteza30": 100, "nume": "produs", "componenta": False,
                 "listat_la": datetime.date(2026, 3, 29)},
}
g.date_de_fond = lambda skus: {s: FOND.get(s.upper(), {}) for s in skus}
import datetime
NOIEMBRIE = datetime.date(2026, 11, 15)
IUNIE = datetime.date(2026, 6, 15)
MAI = datetime.date(2026, 10, 15)   # zi neutra pentru regulile care nu tin de sezon (in fereastra libera de vara)

def ruleaza(linii, azi=MAI, blacklist=()):
    return {r["sku"]: r for r in g.verifica(linii, azi=azi, blacklist=blacklist)}

def test(nume, cond):
    print(("ok   " if cond else "PICAT") + "  " + nume)
    return cond

bun = True
r = ruleaza([{"sku": "HA-PLIN", "qty": 300}])["HA-PLIN"]
bun &= test("regula 2 trece (3 luni), dar raftul plin duce la 33 de luni = blocat",
            r["blocat"] and "luni de stoc dupa comanda" in r["motive"][0])

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
bun &= test("acelasi produs comandat in martie = blocat (nu mai ajunge pana la 15 martie)", r["blocat"])

# Fereastra de vara (Orchestrator 24-oct): trece 1 sep – 1 nov, blocat 2 nov – 31 aug.
for zi, trebuie in [((9, 1), False), ((10, 24), False), ((11, 1), False), ((11, 2), True),
                    ((8, 31), True), ((1, 15), True), ((6, 1), True)]:
    r = ruleaza([{"sku": "HA-VARA", "qty": 100}], azi=datetime.date(2026, *zi))["HA-VARA"]
    bun &= test("vara comandat pe %02d-%02d = %s" % (zi[1], zi[0], "blocat" if trebuie else "trece"),
                r["blocat"] == trebuie)

# Craciun: blocat 2 iul – 31 dec; „cadou" singur nu e Craciun; fara diacritice; categoria conteaza.
for zi, trebuie in [((7, 1), False), ((7, 2), True), ((12, 31), True), ((1, 10), False)]:
    r = ruleaza([{"sku": "HA-CRACIUN", "qty": 10}], azi=datetime.date(2026, *zi))["HA-CRACIUN"]
    bun &= test("Craciun comandat pe %02d-%02d = %s" % (zi[1], zi[0], "blocat" if trebuie else "trece"),
                r["blocat"] == trebuie)
r = ruleaza([{"sku": "HA-CADOU", "qty": 10}], azi=datetime.date(2026, 10, 24))["HA-CADOU"]
bun &= test("set cadou in octombrie = trece (cadou nu e Craciun)", not r["blocat"])
r = ruleaza([{"sku": "HA-CAT", "qty": 10}], azi=datetime.date(2026, 10, 24))["HA-CAT"]
bun &= test("produs din categoria Craciun, titlu neutru = blocat in octombrie", r["blocat"])
r = ruleaza([{"sku": "HA-BUN", "qty": 10, "nume": "Globuri decorative pentru brad"}],
            azi=datetime.date(2026, 10, 24))["HA-BUN"]
bun &= test("globuri / brad in titlul liniei = Craciun", r["blocat"])

# Regula 2, exact: cantitatea > viteza x 4 luni blocheaza, oricare ar fi stocul.
# Produs nou (Orchestrator 24-oct): listat de < 30 de zile = regula 3, niciodata regula 2.
r = ruleaza([{"sku": "HA-PROASPAT", "qty": 250}])["HA-PROASPAT"]
bun &= test("listat de 10 zile, vinde 40/luna, 250 buc = trece (test sub 300, nu regula 2)", not r["blocat"])
r = ruleaza([{"sku": "HA-PROASPAT", "qty": 301}])["HA-PROASPAT"]
bun &= test("listat de 10 zile, 301 buc = blocat pe regula 3", r["blocat"] and r["motive"][0].startswith("regula 3")
            and r["produs_nou"])
r = ruleaza([{"sku": "HA-NELISTAT", "qty": 200}])["HA-NELISTAT"]
bun &= test("nelistat, fara vanzari, 200 buc = trece (test)", not r["blocat"])
r = ruleaza([{"sku": "HA-VECHI", "qty": 401}])["HA-VECHI"]
bun &= test("listat de 200 de zile, 401 buc la 100/luna = regula 2", r["blocat"] and r["motive"][0].startswith("regula 2"))
bun &= test("rezumatul spune luni, viteza si plafonul",
            r["rezumat"] == "401 buc = 4,0 luni de vânzări (100/lună în Orders), peste plafonul de 4 luni")

bun &= test("tipul blocajului = luni", r["tip_blocaj"] == "luni")
bun &= test("zile de stoc 0 la rand, de la cel mai recent", g.zile_stoc_zero([0, 0, 0, 5, 0]) == 3 and g.zile_stoc_zero([4, 0]) == 0)

# Viteza pe zilele cu stoc (Orchestrator 24-oct): vanzari pe zi cunoscute, T(k) = suma ultimelor k zile.
def _T(pe_zi):
    return lambda k: sum(pe_zi[:k])
vand = [0] * 26 + [8] * 64          # 26 de zile fara stoc (0 vandut), inainte 8/zi
r2 = g.viteza_pe_zile_cu_stoc(range(26), _T(vand))
bun &= test("stoc 0 de 26 de zile: 8/zi pe zilele cu stoc = 240/luna", r2["metoda"] == "zile_cu_stoc"
            and round(r2["viteza"]) == 240 and r2["zile_cu_stoc"] == 64)
vand2 = [3] * 26 + [8] * 64          # in zilele fara stoc tot au intrat 3/zi (anulate mai tarziu / marketplace)
r2 = g.viteza_pe_zile_cu_stoc(range(26), _T(vand2))
bun &= test("vanzarile din zilele fara stoc nu intra", round(r2["viteza"]) == 240)
zz = list(range(5)) + list(range(40, 50))  # doua siruri de zile fara stoc
vand3 = [0] * 5 + [10] * 35 + [99] * 10 + [10] * 40
r2 = g.viteza_pe_zile_cu_stoc(zz, _T(vand3))
bun &= test("doua siruri de zile fara stoc: 10/zi = 300/luna", round(r2["viteza"]) == 300 and r2["zile_cu_stoc"] == 75)
r2 = g.viteza_pe_zile_cu_stoc(range(80), _T([1] * 90))
bun &= test("sub 14 zile cu stoc: viteza simpla pe 90 de zile, nesigura", r2["nesigura"] and r2["metoda"] == "90z"
            and round(r2["viteza"]) == 30)
bun &= test("fara stoc 0 in ultimele 30 de zile: viteza obisnuita", g.viteza_pe_zile_cu_stoc([45, 46], _T([1] * 90)) is None)

r = ruleaza([{"sku": "HA-GOL", "qty": 401}])["HA-GOL"]
bun &= test("regula 2: 401 buc la 100/luna, raft gol = blocat", r["blocat"] and "regula 2" in r["motive"][0])
r = ruleaza([{"sku": "HA-GOL", "qty": 400}])["HA-GOL"]
bun &= test("regula 2: 400 buc la 100/luna = trece (fix 4 luni)", not r["blocat"])

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
