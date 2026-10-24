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
    r = subprocess.run([sys.executable, os.path.join(AICI, "data_analytics.py"), "rfm", "--source", "awb"],
                       capture_output=True, text=True, env=env)
    check("--source awb pe laptop: cod 2, „doar pe 84.46”, fără conectare", r.returncode == 2 and "84.46" in r.stderr
          and "OperationalError" not in r.stderr)

# ── RFM / cohort / LTV din OH (date inventate, fără rețea) ──
COLC = COL + ["canal","magazin","plata","reducere","oras","client_email","client_telefon"]
def fake_c(luna, cu_client=False):
    r = {"2026-08": [["EST10", 100, "RON", "", "RO", "Livrata", "", "", 0, "", "2026-08-05", "", "", "", "", "", "e1", "t1"],
                     ["EST11", 40, "RON", "", "RO", "Livrata", "", "", 0, "", "2026-08-09", "", "", "", "", "", "e2", "t2"],
                     ["EST12", 60, "RON", "", "RO", "Refuzata", "", "", 0, "", "2026-08-10", "", "", "", "", "", "e3", "t3"]],
         "2026-09": [["EST13", 50, "RON", "", "RO", "Livrata", "", "", 0, "", "2026-09-02", "", "", "", "", "", "eX", "t1"],
                     ["EST14", 30, "RON", "", "RO", "Livrata", "", "", 0, "", "2026-09-03", "", "", "", "", "", "e4", ""],
                     ["GRAND15", 70, "RON", "", "RO", "Livrata", "", "", 0, "", "2026-09-04", "", "", "", "", "", "e5", "t5"]]}
    return {"ok": True, "coloane": COLC, "randuri": r.get(luna, [])}
com = da.comenzi_client_oh("esteban.ro", "2026-08", azi=dt.date(2026, 9, 30), citeste=fake_c, cache=False)
check("identitatea = telefon (alt email, același telefon = același client); fără telefon → email",
      sorted(c for c, _, _ in com) == ["e:e4", "t:t1", "t:t1", "t:t2"])
coh = {(str(c)[:7], o): n for c, o, n in da.cohort_rows_oh(com)}
check("cohortă: t1 revine în M1", coh == {("2026-08", 0): 2, ("2026-08", 1): 1, ("2026-09", 0): 1})
ltv = {str(c)[:7]: (cu, o, v, rp) for c, cu, o, v, rp in da.ltv_rows_oh(com)}
check("LTV pe cohortă: aug = 2 clienți, 1,5 comenzi, 95 lei, 50% repeat", ltv["2026-08"] == (2, 1.5, 95, 50.0))
check("NTILE ca în Postgres (7 în 5: 2,2,1,1,1)", da._ntile([5, 1, 7, 3, 2, 6, 4]) == [3, 1, 5, 2, 1, 4, 2])
check("RFM: toți clienții intră într-o grupă", sum(n for *_, n, _, _ in da.rfm_rows_oh(com)) == 3)

for k in ("OH_TEAM_READ_TOKEN","OH_PROFIT_CITIRE_TOKEN","OH_CS_SB_TOKEN"): os.environ.pop(k, None)
da.kb = lambda k: ""
try: da.oh_luna("2026-10"); check("fără cheie → OHIndisponibil", False)
except da.OHIndisponibil: check("fără cheie → OHIndisponibil (nu cifra veche)", True)

print("TOATE TREC" if not esec else "EȘUATE: %d" % len(esec)); sys.exit(1 if esec else 0)
