# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Test offline pentru xc_rute_watch.py: prinde aliasurile din COD (și constructorul despărțit), nu comentariile,
docstring-urile, copiile .bak sau API-ul SmartBill.   uv run test_xc_rute_watch.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xc_rute_watch as W  # noqa: E402

FAILS = []


def check(nume, ok, extra=""):
    print(("  ok   " if ok else "  PICAT ") + nume + (("  — " + extra) if extra else ""))
    if not ok:
        FAILS.append(nume)


def scrie(d, rel, text):
    cale = os.path.join(d, rel)
    os.makedirs(os.path.dirname(cale), exist_ok=True)
    with open(cale, "w", encoding="utf-8") as f:
        f.write(text)


def main():
    with tempfile.TemporaryDirectory() as d:
        scrie(d, "curat.py", 'u = X.XBASE + "/api/documents/shipping-labels?connectorId=%s"\n')
        scrie(d, "comentariu.py", '"""Istoric: /api/document/shipping-label era aliasul."""\n# /api/merchant/connectors\nx = 1\n')
        scrie(d, "trimite_vechi.py", 'u = X.XBASE + "/api/document/shipping-label?connectorId=%s&trackingNumber=%s"\n')
        scrie(d, "despartit.py", 'def f(tip):\n    return X.XBASE + "/api/document/" + tip\n')
        scrie(d, "smartbill.py", 'u = "https://ws.smartbill.ro/SBORO/api/document/send"\n')
        scrie(d, "vechi.py.bak-rute-canonice-20260929", 'u = "/api/orders/by-id"\n')
        scrie(d, "BACKUP_x.py", 'u = "/api/orders/by-id"\n')
        scrie(d, "sub/altul.sh", '# curl $B/api/merchant/connectors\ncurl -H "$H" $B/api/merchant/connectors\n')
        scrie(d, ".venv/lib/pachet.py", 'u = "/api/orders/by-id"\n')
        scrie(d, "test_ceva.py", 'RUTE = ("/api/orders/by-id",)\n')
        gasite, scanate = W.scaneaza(d)
        g = sorted(gasite)
        check("prinde aliasul din cod", "trimite_vechi.py:1 document/shipping-label" in g, str(g))
        check("prinde constructorul despărțit (/api/document/ + tip)", "despartit.py:2 api/document/" in g, str(g))
        check("prinde comanda din shell, nu comentariul", [x for x in g if x.startswith("sub/")] == ["sub/altul.sh:2 merchant/connectors"], str(g))
        check("ignoră comentariile și docstring-urile", not any(x.startswith("comentariu.py") for x in g), str(g))
        check("ignoră ruta SmartBill /SBORO/api/document/", not any(x.startswith("smartbill.py") for x in g), str(g))
        check("ignoră copiile .bak / BACKUP_, testele și venv-urile", not any(("bak" in x) or ("BACKUP_" in x) or (".venv" in x) or ("test_" in x) for x in g), str(g))
        check("ruta canonică e curată", not any(x.startswith("curat.py") for x in g), str(g))
        check("exact 3 apeluri găsite", len(g) == 3, str(g))
        check("fișierele sărite nu se numără", scanate == 6, str(scanate))
    print("\n%s — %d test(e) picate" % ("PICAT" if FAILS else "TOATE TRECUTE", len(FAILS)))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
