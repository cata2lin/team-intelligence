# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Regresie (30-sep-2026): rezerva de descărcare a etichetei din awb_station.py.

Când documentul SHIPPING_LABEL n-are `url`/`awbPdfUrl` (sau URL-ul semnat a expirat), eticheta se cere pe ruta
canonică `GET /api/documents/shipping-labels`. Ruta cere cheia xConnector: fără antetul Bearer răspunde 401 (și
fosta rută, cu segmentul singular, la fel), deci rezerva era cod mort. URL-ul semnat din document NU primește
antetul (un URL presemnat poate refuza un al doilea mecanism de autentificare).

Fără rețea: `awb_fast` (modul doar pe VPS) și `urllib.request.urlopen` sunt înlocuite.   uv run test_awb_station_ruta.py
"""
import io
import os
import sys
import tempfile
import types
import urllib.request

AICI = os.path.dirname(os.path.abspath(__file__))
FAILS = []


def check(nume, ok, extra=""):
    print(("  ok   " if ok else "  PICAT ") + nume + (("  — " + extra) if extra else ""))
    if not ok:
        FAILS.append(nume)


class _Raspuns(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def main():
    cereri = []

    def urlopen_fals(req, timeout=None):
        url = req.full_url if isinstance(req, urllib.request.Request) else req
        antete = dict(req.header_items()) if isinstance(req, urllib.request.Request) else {}
        cereri.append((url, antete))
        return _Raspuns(b"%PDF-1.4 fals")

    xc = types.SimpleNamespace(h={"Authorization": "Bearer cheie-falsa", "Content-Type": "application/json"})
    docs = {
        "FARAURL1": {"documentType": "SHIPPING_LABEL", "connectorId": 17541},
        "SEMNAT2": {"documentType": "SHIPPING_LABEL", "connectorId": 17541, "url": "https://cdn.exemplu/eticheta.pdf?sig=abc"},
    }
    fals = types.ModuleType("awb_fast")
    fals.X = types.SimpleNamespace(XBASE="https://xconnector.app")
    fals.WA_DIR = tempfile.mkdtemp()
    fals.WA_GROUP = "grup-test"
    fals.prefetch = lambda orders: None
    fals.resolve = lambda od: ({"shopDomain": "t"}, xc, {"orderId": 1}, docs[od], "81300000001-81300000002", 2)
    fals._merge_pdfs = lambda bucati: b"%PDF-1.4 unit"
    sys.modules["awb_fast"] = fals
    sys.path.insert(0, AICI)
    vechi = urllib.request.urlopen
    urllib.request.urlopen = urlopen_fals
    os.environ["NO_SEND"] = "1"
    try:
        import awb_station as S
        S.build_and_send("TEST", ["FARAURL1", "SEMNAT2"])
    finally:
        urllib.request.urlopen = vechi
    rezerva = [c for c in cereri if "/api/documents/shipping-labels" in c[0]]
    semnat = [c for c in cereri if c[0].startswith("https://cdn.exemplu/")]
    check("rezerva cere ruta canonică", len(rezerva) == 1, str([c[0] for c in cereri]))
    check("rezerva trimite cheia xConnector (fără ea: 401)",
          bool(rezerva) and rezerva[0][1].get("Authorization") == "Bearer cheie-falsa", str(rezerva[:1]))
    check("rezerva are connectorId + trackingNumber",
          bool(rezerva) and "connectorId=17541" in rezerva[0][0] and "trackingNumber=81300000001-81300000002" in rezerva[0][0])
    check("URL-ul semnat NU primește antetul Authorization", len(semnat) == 1 and "Authorization" not in semnat[0][1], str(semnat))
    check("nicio cerere pe ruta retrasă (segmentul singular /api/document/)", not any("/api/document/" in c[0] for c in cereri))
    print("\n%s — %d test(e) picate" % ("PICAT" if FAILS else "TOATE TRECUTE", len(FAILS)))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    main()
