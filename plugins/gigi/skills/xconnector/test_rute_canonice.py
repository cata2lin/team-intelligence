# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Test de regresie: xConnector retrage pe 2-oct-2026 (23:59:59 UTC) șapte aliasuri de rută
(https://xconnector.app/api-migration.html). După data aceea un apel rămas pe alias NU crapă zgomotos:
`by_id` și `list_connectors` întorc {} / [] la orice non-200, deci arată ca „comanda nu există" și
„niciun connector activ", iar descărcarea de rezervă a etichetei pică fără mesaj.

Testul verifică, FĂRĂ nicio cerere de rețea:
  1. nicio rută retrasă în CODUL repo-ului (șiruri Python — nu comentarii/docstring-uri, care pot povesti
     migrarea — plus liniile ne-comentate din .sh/.ps1/.js/.ts);
  2. `XC.by_id` -> GET /api/orders/{id}/address-detail; `XC.list_connectors` -> GET /api/connectors;
  3. `XC.address_correction` -> POST /api/orders/{id}/address-correction, cu comanda în CALE și corpul
     fără `orderId`, exact cheile din spec (`AiCorrectAddressCanonicalRequest`) — aceleași ca Order Hub;
  4. toate scrierile de corecție trec prin helper și un 409 nu împinge nimic în Shopify.

  uv run test_rute_canonice.py
"""
import ast
import io
import json
import os
import sys
from contextlib import redirect_stderr

AICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AICI)

import xconnector as X  # noqa: E402

# Ghidul de migrare xConnector: alias retras -> înlocuitor canonic.
RUTE_RETRASE = {
    "/api/orders/by-id": "GET /api/orders/{shopifyOrderId}/address-detail",
    "/api/orders/ai-correct-address": "POST /api/orders/{shopifyOrderId}/address-correction",
    "/api/merchant/connectors": "GET /api/connectors",
    "/api/document/shipping-label": "GET /api/documents/shipping-labels",
    "/api/document/invoice": "GET /api/documents/invoices (link: POST /api/orders/{shopifyOrderId}/documents)",
    "/api/v1/picking-lists/add-order": "POST /api/picking-lists/{id}/add-order",
}
# Fără prefixul /api, ca să prindă și XBASE + "/orders/by-id" sau f"{baza}/merchant/connectors".
FRAGMENTE = ("orders/by-id", "orders/ai-correct-address", "merchant/connectors",
             "document/shipping-label", "document/invoice", "v1/picking-lists")
SARITE = {".git", "node_modules", ".venv", "venv", "__pycache__"}
SHELL = (".sh", ".ps1", ".js", ".ts", ".mjs", ".gs")

# AiCorrectAddressCanonicalRequest din https://xconnector.app/api-spec.yaml
OBLIGATORII = {"idempotencyKey", "appliedShippingAddress", "expectedAddressHash", "expectedStatusHash"}
PERMISE = OBLIGATORII | {"expectedEvidenceHash", "agentClaimedConfidence", "agentRationale", "modelName", "mcpClientId"}

FAILS = []


def check(nume, ok, extra=""):
    print(("  ok   " if ok else "  PICAT ") + nume + (("  — " + extra) if extra else ""))
    if not ok:
        FAILS.append(nume)


def radacina_repo():
    d = AICI
    while d != os.path.dirname(d):
        if os.path.isfile(os.path.join(d, ".claude-plugin", "marketplace.json")):
            return d
        d = os.path.dirname(d)
    return None


def siruri_din_cod(sursa):
    """Șirurile din cod (inclusiv părțile de f-string), FĂRĂ docstring-uri. Comentariile nu sunt în AST."""
    arbore = ast.parse(sursa)
    docstringuri = set()
    for nod in ast.walk(arbore):
        if isinstance(nod, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and nod.body:
            prim = nod.body[0]
            if isinstance(prim, ast.Expr) and isinstance(prim.value, ast.Constant):
                docstringuri.add(id(prim.value))
    return [(n.lineno, n.value) for n in ast.walk(arbore)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstringuri]


def aliasuri_in(cale, sursa):
    if cale.endswith(".py"):
        try:
            siruri = siruri_din_cod(sursa)
        except SyntaxError:
            siruri = list(enumerate(sursa.splitlines(), 1))
        return [(ln, f) for ln, s in siruri for f in FRAGMENTE if f in s]
    gasite = []
    for ln, linie in enumerate(sursa.splitlines(), 1):
        t = linie.strip()
        if t.startswith("#") or t.startswith("//"):
            continue
        gasite += [(ln, f) for f in FRAGMENTE if f in linie]
    return gasite


def t_plasa_prinde_codul_nu_comentariul():
    cod = ('"""Antet: `GET /api/orders/by-id` era aliasul."""\n'
           '# comentariu: /api/merchant/connectors\n'
           'def f(baza):\n'
           '    """Docstring: /api/orders/ai-correct-address."""\n'
           '    return "/api/orders/by-id", f"{baza}/document/shipping-label", "/api/orders/%s/address-detail"\n')
    gasite = sorted(f for _, f in aliasuri_in("x.py", cod))
    check("plasa prinde aliasul din cod (și din f-string), nu din comentariu/docstring",
          gasite == ["document/shipping-label", "orders/by-id"], str(gasite))
    check("plasa ignoră rutele canonice", aliasuri_in("y.py", 'u = "/api/documents/shipping-labels"\n') == [])
    check("plasa pe shell ignoră comentariul, prinde comanda",
          [f for _, f in aliasuri_in("z.sh", "# /api/merchant/connectors\ncurl $B/api/merchant/connectors\n")]
          == ["merchant/connectors"])


def t_niciun_alias_in_repo():
    rad = radacina_repo()
    check("rădăcina repo-ului găsită (.claude-plugin/marketplace.json)", bool(rad))
    if not rad:
        return
    eu = os.path.abspath(__file__)
    gasite, scanate = [], 0
    for d, dirs, fisiere in os.walk(rad):
        dirs[:] = [x for x in dirs if x not in SARITE]
        for f in fisiere:
            cale = os.path.join(d, f)
            if cale == eu or not (f.endswith(".py") or f.endswith(SHELL)):
                continue
            try:
                with open(cale, encoding="utf-8", errors="replace") as h:
                    sursa = h.read()
            except OSError:
                continue
            scanate += 1
            if not any(fr in sursa for fr in FRAGMENTE):
                continue
            gasite += ["%s:%s %s" % (os.path.relpath(cale, rad), ln, fr) for ln, fr in aliasuri_in(cale, sursa)]
    check("nicio rută retrasă în codul repo-ului (%d fișiere scanate)" % scanate, gasite == [],
          ("; ".join(gasite) + "  → înlocuitorii sunt în RUTE_RETRASE") if gasite else "")


class _Get:
    """Înlocuiește XC.get: înregistrează ruta și întoarce răspunsul pregătit — fără rețea."""

    def __init__(self):
        self.cereri, self.raspuns = [], (200, {})

    def __call__(self, path, q=""):
        self.cereri.append((path, q))
        return self.raspuns


def t_citiri_pe_rute_canonice():
    get = _Get()
    vechi = X.XC.get
    X.XC.get = lambda xc, path, q="": get(path, q)   # funcție, ca să se lege ca metodă
    try:
        xc = X.XC("cheie-falsa")
        get.raspuns = (200, {"orderId": 7001, "addressHash": "h"})
        d = xc.by_id(7001)
        check("by_id -> GET /api/orders/7001/address-detail, fără query",
              get.cereri[-1] == ("/api/orders/7001/address-detail", "") and d.get("orderId") == 7001, str(get.cereri[-1:]))
        n = len(get.cereri)
        check("by_id cu id nenumeric (gid) -> {} fără apel", xc.by_id("gid://shopify/Order/1") == {} and len(get.cereri) == n)
        get.raspuns = (404, {"errorCode": "order_not_found", "errorDescription": "Order not found"})
        err = io.StringIO()
        with redirect_stderr(err):
            r = xc.by_id(1)
        check("404 order_not_found (spec: comandă inexistentă) -> {} în liniște", r == {} and err.getvalue() == "")
        get.raspuns = (404, {"timestamp": "t", "status": 404, "error": "Not Found", "path": "/api/x"})
        err = io.StringIO()
        with redirect_stderr(err):
            r = xc.by_id(2)
        check("404 FĂRĂ order_not_found = rută lipsă -> {} + avertisment pe stderr", r == {} and "rută lipsă" in err.getvalue())
        get.raspuns = (200, [{"id": 17541, "type": "DPD", "active": True}])
        cons = X.XC("cheie-falsa").list_connectors()
        check("list_connectors -> GET /api/connectors",
              get.cereri[-1] == ("/api/connectors", "") and cons[0]["id"] == 17541, str(get.cereri[-1:]))
    finally:
        X.XC.get = vechi


def t_corectia_pe_ruta_canonica():
    if not hasattr(X.XC, "address_correction"):
        check("XC.address_correction există (helperul rutei canonice)", False)
        return
    trimise, impinse = [], []

    def http_fals(method, url, headers, body=None, timeout=45):
        trimise.append((method, url, body))
        return 409, json.dumps({"conflict": True})   # 409 = starea s-a schimbat; nimic de împins în Shopify

    vechi_http, vechi_push, vechi_by_id = X.http, X.shopify_push_corrected, X.XC.by_id
    X.http = http_fals
    X.shopify_push_corrected = lambda *a, **k: impinse.append(a)
    try:
        xc = X.XC("cheie-falsa")
        xc.address_correction(9001, {"orderId": 9001, "idempotencyKey": "k", "appliedShippingAddress": {"city": "c"},
                                     "expectedAddressHash": "a", "expectedStatusHash": "b"})
        m, url, corp = trimise[-1]
        check("helper -> POST /api/orders/9001/address-correction",
              m == "POST" and url == X.XBASE + "/api/orders/9001/address-correction", url)
        check("helper scoate orderId din corp (spec: identitatea e DOAR în cale)", "orderId" not in corp)
        n = len(trimise)
        s, _ = xc.address_correction("gid://shopify/Order/9", {"idempotencyKey": "k"})
        check("helper cu id nenumeric -> 400 local, fără apel", s == 400 and len(trimise) == n)

        det = {"orderName": "T-1", "addressHash": "ah", "statusHash": "sh", "evidenceHash": None}
        adr = {"firstName": "A", "lastName": "B", "company": None, "address1": "Str 1", "address2": None,
               "city": "Oras", "zip": "100000", "province": "PH", "country": "Romania", "phone": "0700000000"}
        ok = X._nomen_write(xc, 5551, det, adr, dict(adr, address1="Strada 1"), "test.myshopify.com")
        m, url, corp = trimise[-1]
        check("_nomen_write -> ruta canonică, id în cale", url == X.XBASE + "/api/orders/5551/address-correction", url)
        check("_nomen_write: corp = spec, fără orderId", OBLIGATORII <= set(corp) <= PERMISE, str(sorted(corp)))
        check("_nomen_write: 409 -> False, nimic împins în Shopify", ok is False and not impinse)

        X.XC.by_id = lambda self, oid: dict(det, orderId=oid, shippingAddress=dict(adr))
        ok = X.intl_correct_write(xc, {"orderId": 5552, "orderName": "T-2"}, "test.myshopify.com", {"city": "Praha"})
        m, url, corp = trimise[-1]
        check("intl_correct_write -> ruta canonică, id în cale", url == X.XBASE + "/api/orders/5552/address-correction", url)
        check("intl_correct_write: corp = spec, fără orderId", OBLIGATORII <= set(corp) <= PERMISE, str(sorted(corp)))
        check("intl_correct_write: adresa trimisă e COMPLETĂ (spec: câmpurile omise se golesc în Shopify)",
              set(adr) <= set(corp["appliedShippingAddress"]) and corp["appliedShippingAddress"]["city"] == "Praha")
    finally:
        X.http, X.shopify_push_corrected, X.XC.by_id = vechi_http, vechi_push, vechi_by_id


def t_toate_scrierile_trec_prin_helper():
    """Cele două scrieri din correct_address cer validatorul live, deci le verific pe AST: fiecare apel al
    helperului primește un dict literal cu cheile din spec și fără orderId."""
    with open(os.path.join(AICI, "xconnector.py"), encoding="utf-8") as h:
        arbore = ast.parse(h.read())
    apeluri = {}
    for fn in [n for n in ast.walk(arbore) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        if fn.name == "address_correction":
            continue
        dicturi = {}
        for nod in ast.walk(fn):
            if isinstance(nod, ast.Assign) and isinstance(nod.value, ast.Dict):
                for t in nod.targets:
                    if isinstance(t, ast.Name):
                        dicturi.setdefault(t.id, []).append(nod)
        for nod in ast.walk(fn):
            if (isinstance(nod, ast.Call) and isinstance(nod.func, ast.Attribute) and nod.func.attr == "address_correction"
                    and len(nod.args) == 2 and isinstance(nod.args[1], ast.Name)):
                inainte = [a for a in dicturi.get(nod.args[1].id, []) if a.lineno < nod.lineno]
                chei = {k.value for k in inainte[-1].value.keys if isinstance(k, ast.Constant)} if inainte else None
                apeluri.setdefault(nod.lineno, (fn.name, chei))   # funcțiile imbricate apar de 2 ori în ast.walk
    check("cel puțin 4 scrieri de corecție prin helper (vsug, aac, nomen, intl)", len(apeluri) >= 4,
          ", ".join("%s:%d" % (f, ln) for ln, (f, _) in sorted(apeluri.items())))
    for ln, (f, chei) in sorted(apeluri.items()):
        check("  %s:%d corp = spec, fără orderId" % (f, ln),
              chei is not None and OBLIGATORII <= chei <= PERMISE, str(sorted(chei or [])))


def main():
    for fn in (t_plasa_prinde_codul_nu_comentariul, t_niciun_alias_in_repo, t_citiri_pe_rute_canonice,
               t_corectia_pe_ruta_canonica, t_toate_scrierile_trec_prin_helper):
        print(fn.__name__)
        fn()
    print("\n%s — %d test(e) picate" % ("PICAT" if FAILS else "TOATE TRECUTE", len(FAILS)))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")   # stațiile Windows (cp1252)
        except Exception:
            pass
    main()
