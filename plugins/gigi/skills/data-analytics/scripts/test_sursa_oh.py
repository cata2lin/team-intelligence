# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9"]
# ///
"""Comutarea forecast → Order Hub și garda „doar pe server" pentru RFM/cohort/LTV. Fără rețea, fără producție.
  uv run test_sursa_oh.py"""
import importlib.util, os, sys, datetime as dt, subprocess

AICI = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("da", os.path.join(AICI, "data_analytics.py"))
da = importlib.util.module_from_spec(spec); spec.loader.exec_module(da)
esec = []
def check(n, ok):
    print(("  ok   " if ok else "  FAIL ") + n)
    if not ok: esec.append(n)

COL = ["comanda","total","moneda","judet","tara","stare","curier","ultim_status","transport_fara_tva","skus","zi"]
def fake(luna):
    r = []
    if luna == "2026-10":
        r = [["EST1", 100, "RON", "", "RO", "Livrata", "", "", 0, "", "2026-10-13"],
             ["EST2", 50, "RON", "", "RO", "Refuzata", "", "", 0, "", "2026-10-14"],
             ["GRAND3", 70, "RON", "", "RO", "Livrata", "", "", 0, "", "2026-10-15"],
             ["EST4", 30, "RON", "", "RO", "Livrata", "", "", 0, "", "2026-10-19"]]
    return {"ok": True, "coloane": COL, "randuri": r}
w = da.saptamani_oh("esteban.ro", azi=dt.date(2026, 10, 21), sapt=2, citeste=fake)
check("doar livratele magazinului, pe săptămâna comenzii (luni)", w == [(dt.date(2026,10,12),1,100),(dt.date(2026,10,19),1,30)])
check("prefixul GRAND → grandia.ro", da.domeniu("GRAND000001") == "grandia.ro")

os.environ.pop("ARONA_PE_SERVER", None)
import socket
check("laptop nu e server", da.pe_server() is (socket.gethostname() in da.SERVERE))
os.environ["ARONA_PE_SERVER"] = "1"; check("ARONA_PE_SERVER=1 = server", da.pe_server()); os.environ.pop("ARONA_PE_SERVER")

if socket.gethostname() not in da.SERVERE:
    env = {k: v for k, v in os.environ.items() if not k.startswith("DATABASE_URL")}
    env["DATABASE_URL_AWBPRINT"] = "postgres://nu:se@127.0.0.1:1/deschide"
    r = subprocess.run([sys.executable, os.path.join(AICI, "data_analytics.py"), "rfm"], capture_output=True, text=True, env=env)
    check("rfm pe laptop: cod 2, „ruta OH lipsește”, fără conectare", r.returncode == 2 and "ruta OH lipsește" in r.stderr
          and "OperationalError" not in r.stderr)

for k in ("OH_TEAM_READ_TOKEN","OH_PROFIT_CITIRE_TOKEN","OH_CS_SB_TOKEN"): os.environ.pop(k, None)
da.kb = lambda k: ""
try: da.oh_luna("2026-10"); check("fără cheie → OHIndisponibil", False)
except da.OHIndisponibil: check("fără cheie → OHIndisponibil (nu cifra veche)", True)

print("TOATE TREC" if not esec else "EȘUATE: %d" % len(esec)); sys.exit(1 if esec else 0)
