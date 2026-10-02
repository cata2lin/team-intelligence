"""
xc_rute_watch.py — plasă PE VPS pentru rutele xConnector retrase pe 2-oct-2026 (23:59:59 UTC).

De ce: `plugins/gigi/skills/xconnector/test_rute_canonice.py` vede doar repo-ul. Aliasurile au rămas însă în COPIILE
PLATE de pe VPS (`/root/Scripturi/*.py`), unde nu le verifică nimic: pe 29-sep patru one-off-uri încă cereau
`/api/document/shipping-label`. După retragere, un apel pe alias nu crapă zgomotos — `by_id`/`list_connectors` întorc
{} / [] (arată ca „comanda nu există" / „niciun connector"), iar descărcarea de rezervă a etichetei pică fără mesaj.
Un fișier restaurat dintr-un `.bak` sau copiat dintr-o versiune veche ar readuce aliasul tăcut.

Ce face: scanează `*.py` și `*.sh` sub rădăcină (implicit /root/Scripturi, recursiv; sare .git, venv-uri,
node_modules, __pycache__, copiile `*.bak*` / `BACKUP_*`, testele `test_*` și pe sine) după:
  - cele șase fragmente de alias din ghidul de migrare (aceleași ca testul din repo);
  - orice segment singular `api/document/` (prinde și un constructor despărțit, ex. "/api/document/" + tip) —
    în afară de API-ul SmartBill (`ws.smartbill.ro/SBORO/api/document/...`), care e altă rută, legitimă.
La Python se uită doar în șirurile din cod (nu în comentarii/docstring-uri, care pot povesti migrarea); la shell,
doar în liniile ne-comentate. Iese cu 1 dacă găsește ceva și, cu --email, trimite raportul (același drum ca
data_health.py). Doar citire.

  .venv/bin/python xc_rute_watch.py                          # raport în consolă
  .venv/bin/python xc_rute_watch.py --email X --key ...      # + email DOAR dacă găsește ceva
"""
import argparse
import ast
import os
import re
import sys
from datetime import datetime

FRAGMENTE = ("orders/by-id", "orders/ai-correct-address", "merchant/connectors",
             "document/shipping-label", "document/invoice", "v1/picking-lists")
GENERIC = re.compile(r"api/document/")
SMARTBILL = re.compile(r"smartbill|sboro", re.I)
SARITE = {".git", "node_modules", ".venv", "venv", "__pycache__", "site-packages"}
SHELL = (".sh",)
MAX_BYTES = 3 * 1024 * 1024


def _sarit(nume):
    # copiile de rezervă, testele (au aliasurile ca date de test) și plasa însăși (le are în FRAGMENTE)
    return (".bak" in nume or nume.startswith("BACKUP_") or nume.endswith(".orig") or nume.startswith("test_")
            or nume == os.path.basename(__file__))


def _siruri_din_cod(sursa):
    """(linie, șir) pt șirurile din cod, fără docstring-uri. Comentariile nu sunt în AST."""
    arbore = ast.parse(sursa)
    doc = set()
    for nod in ast.walk(arbore):
        if isinstance(nod, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and nod.body:
            prim = nod.body[0]
            if isinstance(prim, ast.Expr) and isinstance(prim.value, ast.Constant):
                doc.add(id(prim.value))
    return [(n.lineno, n.value) for n in ast.walk(arbore)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in doc]


def _in_text(text):
    """Fragmentele de alias dintr-un șir/linie (fără ruta SmartBill)."""
    gasite = [f for f in FRAGMENTE if f in text]
    if not gasite and GENERIC.search(text) and not SMARTBILL.search(text):
        gasite.append("api/document/")
    return gasite


def aliasuri_in(cale, sursa):
    if cale.endswith(".py"):
        try:
            bucati = _siruri_din_cod(sursa)
        except SyntaxError:
            bucati = [(ln, l) for ln, l in enumerate(sursa.splitlines(), 1) if not l.strip().startswith("#")]
        return [(ln, f) for ln, s in bucati for f in _in_text(s)]
    return [(ln, f) for ln, l in enumerate(sursa.splitlines(), 1)
            if not l.strip().startswith("#") for f in _in_text(l)]


def scaneaza(radacina):
    """([cale:linie fragment], nr fișiere scanate)."""
    gasite, scanate = [], 0
    for d, dirs, fisiere in os.walk(radacina):
        dirs[:] = [x for x in dirs if x not in SARITE and not _sarit(x)]
        for f in fisiere:
            if not (f.endswith(".py") or f.endswith(SHELL)) or _sarit(f):
                continue
            cale = os.path.join(d, f)
            try:
                if os.path.getsize(cale) > MAX_BYTES:
                    continue
                with open(cale, encoding="utf-8", errors="replace") as h:
                    sursa = h.read()
            except OSError:
                continue
            scanate += 1
            if not any(fr in sursa for fr in FRAGMENTE) and not GENERIC.search(sursa):
                continue
            gasite += ["%s:%s %s" % (os.path.relpath(cale, radacina), ln, fr) for ln, fr in aliasuri_in(cale, sursa)]
    return gasite, scanate


def main():
    ap = argparse.ArgumentParser(description="Rute xConnector retrase (2-oct-2026) rămase în copiile de pe VPS")
    ap.add_argument("--root", default="/root/Scripturi")
    ap.add_argument("--email", help="destinatar (email trimis DOAR dacă găsește ceva)")
    ap.add_argument("--from", dest="sender", default="gheorghe.beschea@overheat.agency")
    ap.add_argument("--key", default="/root/Scripturi/google_credentials.json")
    a = ap.parse_args()
    gasite, scanate = scaneaza(a.root)
    cap = "RUTE xCONNECTOR RETRASE — %s · %d fișiere .py/.sh scanate sub %s" % (
        datetime.now().strftime("%Y-%m-%d %H:%M"), scanate, a.root)
    if not gasite:
        print(cap + "\n🟢 niciun alias retras")
        return 0
    raport = "\n".join([cap, "🔴 %d apeluri pe rute retrase (după 2-oct 23:59 UTC răspund 404 tăcut):" % len(gasite)]
                       + ["  " + g for g in gasite]
                       + ["", "Înlocuitori: GET /api/orders/{id}/address-detail · POST /api/orders/{id}/address-correction ·",
                          "GET /api/connectors · GET /api/documents/shipping-labels · GET /api/documents/invoices ·",
                          "POST /api/picking-lists/{id}/add-order (https://xconnector.app/api-migration.html)."])
    print(raport)
    if a.email:
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from data_health import _send_email
            _send_email(a.email, "[xc-rute] 🔴 %d apeluri pe rute xConnector retrase" % len(gasite), raport, a.key, a.sender)
            print("\n[email] trimis către %s" % a.email)
        except Exception as e:
            print("\n[email] EȘUAT: %s: %s" % (type(e).__name__, e))
    return 1


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    sys.exit(main())
