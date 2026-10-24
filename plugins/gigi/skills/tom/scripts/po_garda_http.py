# /// script
# dependencies = ["psycopg2-binary>=2.9"]
# ///
"""Garda de PO ca serviciu HTTP, pentru TOM (tom.arona.ro): aceeasi garda, nu o copie.

TOM ruleaza pe Vercel si nu ajunge la stocul din scaner sau la Order Hub. In loc sa rescriem regulile
in TypeScript (a doua implementare, care s-ar desparti de prima la prima schimbare de prag), TOM
intreaba garda asta, care IMPORTA `po_garda.verifica` — exact functia pe care o folosesc
`po_from_sheet.py` si umbra din cron.

    POST /po-garda/verifica   {"linii": [{"sku": "HA-0001", "qty": 3000, "nume": "...", "valoare_lei": 12000}]}
    GET  /po-garda/sanatate

Semnatura, ca la /api/v1 din TOM: canonical = METHOD\\nPATH\\nUNIX_TS\\nSHA256_HEX(body),
antete `X-Garda-Timestamp` (+-300 s) si `X-Garda-Signature` = HMAC_SHA256_hex(secret, canonical).
Secretul: `PO_GARDA_HMAC_SECRET` (in seif; pe VPS in .env; in TOM ca variabila Vercel).

Ruleaza pe 127.0.0.1:8733, in spatele nginx (scripts.arona.ro/po-garda/), din systemd.
"""
import hashlib, hmac, json, os, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
AICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AICI)
import po_garda  # noqa: E402

PORT = int(os.environ.get("PO_GARDA_PORT", "8733"))
MAX_LINII = 500
MAX_CORP = 512 * 1024
FEREASTRA_S = 300
# O singura verificare odata: fiecare deschide conexiuni la TOM, metrics si Order Hub. Doua PO-uri
# trimise in aceeasi secunda asteapta una dupa alta, nu dubleaza presiunea pe bazele altora.
LACAT = threading.Lock()


def versiune():
    try:
        return subprocess.run(["git", "-C", AICI, "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=5).stdout.strip() or "?"
    except Exception:
        return "?"


VERSIUNE = versiune()


def secret():
    v = os.environ.get("PO_GARDA_HMAC_SECRET") or ""
    if not v:
        raise SystemExit("lipseste PO_GARDA_HMAC_SECRET")
    return v.encode()


def semnatura_valida(secret_b, metoda, cale, ts, corp, sig, acum=None):
    try:
        t = int(ts)
    except (TypeError, ValueError):
        return False
    if abs((acum or time.time()) - t) > FEREASTRA_S:
        return False
    canonic = "%s\n%s\n%d\n%s" % (metoda, cale, t, hashlib.sha256(corp).hexdigest())
    asteptat = hmac.new(secret_b, canonic.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(asteptat, (sig or "").strip().lower())


def valideaza_linii(d):
    """Intrarea, verificata la granita. Intoarce (linii, eroare)."""
    if not isinstance(d, dict) or not isinstance(d.get("linii"), list):
        return None, "corpul trebuie sa fie {\"linii\": [...]}"
    linii = d["linii"]
    if not linii or len(linii) > MAX_LINII:
        return None, "intre 1 si %d linii" % MAX_LINII
    out = []
    for i, l in enumerate(linii):
        if not isinstance(l, dict):
            return None, "linia %d nu e obiect" % i
        sku = str(l.get("sku") or "").strip().upper()
        try:
            qty = int(l.get("qty"))
        except (TypeError, ValueError):
            return None, "linia %d: qty lipsa sau invalid" % i
        if not sku or qty <= 0:
            return None, "linia %d: sku gol sau qty <= 0" % i
        x = {"sku": sku[:120], "qty": qty, "nume": str(l.get("nume") or "")[:300]}
        for k in ("valoare_lei", "valoare_usd"):
            if l.get(k) is not None:
                try:
                    x[k] = float(l[k])
                except (TypeError, ValueError):
                    pass
        out.append(x)
    return out, None


class Garda(BaseHTTPRequestHandler):
    server_version = "po-garda"

    def _json(self, cod, d):
        b = json.dumps(d, ensure_ascii=False).encode()
        self.send_response(cod)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, fmt, *args):  # fara corpul cererii in log: doar linia de acces
        sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))

    def do_GET(self):
        if self.path.rstrip("/") in ("/po-garda/sanatate", "/sanatate"):
            return self._json(200, {"ok": True, "versiune": VERSIUNE, "praguri": po_garda.PRAGURI})
        return self._json(404, {"ok": False, "eroare": "nu exista"})

    def do_POST(self):
        cale = self.path.split("?", 1)[0]
        if cale not in ("/po-garda/verifica", "/verifica"):
            return self._json(404, {"ok": False, "eroare": "nu exista"})
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_CORP:
            return self._json(413, {"ok": False, "eroare": "corp gol sau prea mare"})
        corp = self.rfile.read(n)
        # Semnatura se verifica pe calea PUBLICA (/po-garda/verifica): nginx o trimite neschimbata.
        if not semnatura_valida(secret(), "POST", "/po-garda/verifica",
                                self.headers.get("X-Garda-Timestamp"), corp,
                                self.headers.get("X-Garda-Signature")):
            return self._json(401, {"ok": False, "eroare": "semnatura invalida sau expirata"})
        try:
            linii, err = valideaza_linii(json.loads(corp))
        except ValueError:
            linii, err = None, "JSON invalid"
        if err:
            return self._json(422, {"ok": False, "eroare": err})
        try:
            with LACAT:
                rez = po_garda.verifica(linii)
        except SystemExit as e:          # secret / sursa lipsa: garda NU a judecat
            return self._json(503, {"ok": False, "eroare": "garda indisponibila: %s" % e})
        except Exception as e:
            return self._json(503, {"ok": False, "eroare": "garda a cazut: %s" % type(e).__name__})
        return self._json(200, {"ok": True, "versiune": VERSIUNE, "praguri": po_garda.PRAGURI,
                                "linii": rez})


def main():
    secret()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Garda)
    print("po-garda %s asculta pe 127.0.0.1:%d" % (VERSIUNE, PORT), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
