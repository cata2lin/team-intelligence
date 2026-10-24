# /// script
# dependencies = []
# ///
"""Teste pure pentru serviciul HTTP al gardii: semnatura si validarea intrarii, fara baze de date."""
import hashlib, hmac, importlib.util, os, pathlib, sys, time

os.environ.setdefault("PO_GARDA_HMAC_SECRET", "test")
spec = importlib.util.spec_from_file_location("po_garda_http", pathlib.Path(__file__).with_name("po_garda_http.py"))
h = importlib.util.module_from_spec(spec); spec.loader.exec_module(h)


def test(nume, cond):
    print(("ok   " if cond else "PICAT") + "  " + nume)
    return cond


def semneaza(sec, corp, ts):
    c = "POST\n/po-garda/verifica\n%d\n%s" % (ts, hashlib.sha256(corp).hexdigest())
    return hmac.new(sec, c.encode(), hashlib.sha256).hexdigest()


bun = True
corp = b'{"linii":[{"sku":"ha-1","qty":5}]}'
acum = int(time.time())
sig = semneaza(b"s3cret", corp, acum)
bun &= test("semnatura corecta = valida", h.semnatura_valida(b"s3cret", "POST", "/po-garda/verifica", str(acum), corp, sig))
bun &= test("alt secret = invalida", not h.semnatura_valida(b"altul", "POST", "/po-garda/verifica", str(acum), corp, sig))
bun &= test("corp schimbat = invalida", not h.semnatura_valida(b"s3cret", "POST", "/po-garda/verifica", str(acum), corp + b" ", sig))
bun &= test("expirata (>300 s) = invalida", not h.semnatura_valida(b"s3cret", "POST", "/po-garda/verifica", str(acum), corp, sig, acum=acum + 301))
bun &= test("timestamp lipsa = invalida", not h.semnatura_valida(b"s3cret", "POST", "/po-garda/verifica", None, corp, sig))

l, e = h.valideaza_linii({"linii": [{"sku": " ha-1 ", "qty": "5", "valoare_lei": 100}]})
bun &= test("sku normalizat la majuscule, qty intreg", e is None and l[0]["sku"] == "HA-1" and l[0]["qty"] == 5)
bun &= test("qty 0 = refuzat", h.valideaza_linii({"linii": [{"sku": "A", "qty": 0}]})[1] is not None)
bun &= test("fara linii = refuzat", h.valideaza_linii({"linii": []})[1] is not None)
bun &= test("sku gol = refuzat", h.valideaza_linii({"linii": [{"sku": "", "qty": 3}]})[1] is not None)

print("\n%s" % ("toate testele trec" if bun else "SUNT TESTE PICATE"))
sys.exit(0 if bun else 1)
