# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
"""
Populate Belasil Asset Group 1 (6570921716) — the first/default PMax asset group
that still has placeholder 'Test' copy. Removes Test links, adds full 15/5/5 copy
identical to the [ALS] P.Max group so both groups compete on real text.

Run: DATABASE_URL_METRICS=$(kb.py secret-get DATABASE_URL_METRICS) \\
       uv run build_belasil_ag1_assets.py [--apply]
"""
import os, sys, json
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2, psycopg2.extras, requests

API = "v21"
CID = "7566352958"
AG  = "customers/7566352958/assetGroups/6570921716"

# ── Placeholder asset-group-asset links to remove ──────────────────────────
TEST_LINKS = [
    "customers/7566352958/assetGroupAssets/6570921716~230026758785~LONG_HEADLINE",  # "Test Test Test Test"
    "customers/7566352958/assetGroupAssets/6570921716~230113523395~HEADLINE",        # "Test"
    "customers/7566352958/assetGroupAssets/6570921716~230113523398~HEADLINE",        # "Test Test"
    "customers/7566352958/assetGroupAssets/6570921716~230113523398~DESCRIPTION",     # "Test Test"
    "customers/7566352958/assetGroupAssets/6570921716~230113523401~HEADLINE",        # "Test Test Test"
    "customers/7566352958/assetGroupAssets/6570921716~230113523401~DESCRIPTION",     # "Test Test Test"
]

# ── Real copy ───────────────────────────────────────────────────────────────
HEADLINES = [
    "Detergent Gel Concentrat",      # 24
    "200 de Spălări pe Bidon",       # 23
    "Doar 0,49 lei pe Spălare",      # 24
    "10L la 99 lei, de la 255",      # 23
    "Direct de la Producător",       # 23
    "Balsam Inclus în Detergent",    # 26
    "Spală Alb și Color",            # 18
    "Detergent Rufe Premium",        # 22
    "4,7/5 din 1.250+ Recenzii",     # 25
    "Parfum de Lungă Durată",        # 22
    "Gel Dens, Delicat cu Tine",     # 25
    "Transport Gratuit 150 lei",     # 25
    "5 Parfumuri la Alegere",        # 22
    "Detergent 100% Românesc",       # 23
    "Detergent Concentrat 10L",      # 24
]

LONG = [
    "Detergent gel ultra-concentrat: 200 de spălări dintr-un bidon de 10L, 0,49 lei/spălare",
    "Direct de la producător: 10L la 99 lei (de la 255). Transport gratuit peste 150 lei",
    "Gel dens, delicat cu hainele și pielea. Balsam inclus, spală alb și color",
    "4,7/5 din peste 1.250 de recenzii. 5 parfumuri, persistă mult, garanție 14 zile",
    "Detergent 100% românesc, de la fabrică: mai puține bidoane, mai multe spălări",
]

DESCR = [
    "Detergent gel concentrat. 200 spălări, 0,49 lei/spălare.",
    "De la producător, fără intermediari. 10L la 99 lei. Transport gratuit peste 150 lei.",
    "Gel dens, delicat cu hainele. Balsam inclus. Spală alb și color, manual și automat.",
    "4,7/5 din peste 1.250 de recenzii. 5 parfumuri la alegere. Garanție retur 14 zile.",
    "Detergent 100% românesc, de la fabrică. Ultra-concentrat, persistent, preț corect.",
]

# ── infra ───────────────────────────────────────────────────────────────────
_PG_OK = {"host","port","dbname","user","password","sslmode","sslrootcert","sslcert",
          "sslkey","connect_timeout","application_name","options","channel_binding"}

def clean(dsn):
    p = urlsplit(dsn)
    if not p.query: return dsn
    kept = [(k,v) for k,v in parse_qsl(p.query, keep_blank_values=True) if k.lower() in _PG_OK]
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(kept), p.fragment))

cx = psycopg2.connect(clean(os.environ["DATABASE_URL_METRICS"]))
cx.set_session(readonly=True)
with cx.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
    c.execute('SELECT "developerToken" dev,"loginCustomerId" mcc,"oauthClientId" cid,'
              '"oauthClientSecret" csec,"refreshToken" rt '
              'FROM google_ads_connections WHERE "isActive"=true')
    creds = c.fetchone()
cx.close()

tok = requests.post("https://oauth2.googleapis.com/token",
    data={"grant_type":"refresh_token","client_id":creds["cid"],
          "client_secret":creds["csec"],"refresh_token":creds["rt"]},
    timeout=20).json()["access_token"]

H = {"Authorization": f"Bearer {tok}",
     "developer-token": creds["dev"],
     "login-customer-id": "".join(ch for ch in str(creds["mcc"]) if ch.isdigit()),
     "Content-Type": "application/json"}

apply = "--apply" in sys.argv

def post(svc, ops, partial=False):
    body = {"operations": ops, "validateOnly": not apply}
    if partial: body["partialFailure"] = True
    r = requests.post(
        f"https://googleads.googleapis.com/{API}/customers/{CID}/{svc}:mutate",
        headers=H, json=body, timeout=90)
    return r

# ── char-limit pre-check ────────────────────────────────────────────────────
bad = ([t for t in HEADLINES if len(t) > 30] +
       [t for t in LONG    if len(t) > 90] +
       [t for t in DESCR   if len(t) > 90])
if bad:
    print("EROARE limite caractere:", bad); sys.exit(1)
print("Verificare limite: OK")

# ── 1) remove placeholder links ─────────────────────────────────────────────
r0 = post("assetGroupAssets", [{"remove": ln} for ln in TEST_LINKS], partial=True)
print(f"1) remove Test links: HTTP {r0.status_code}")
if r0.status_code != 200:
    print("   WARN:", r0.text[:400])

if not apply:
    print("\nDRY-RUN — adaugă --apply ca să execuți")
    sys.exit(0)

# ── 2) create all text assets (idempotent — returnează IDs existente) ────────
allt = ([(t, "HEADLINE")      for t in HEADLINES] +
        [(t, "LONG_HEADLINE")  for t in LONG] +
        [(t, "DESCRIPTION")    for t in DESCR])
ra = post("assets", [{"create": {"textAsset": {"text": t}}} for t,_ in allt], partial=True)
print(f"2) create text assets: HTTP {ra.status_code}")
if ra.status_code != 200: print(ra.text[:600]); sys.exit(1)

res = ra.json().get("results", [])
names = [(res[i].get("resourceName"), allt[i][1])
         for i in range(len(allt)) if res[i].get("resourceName")]
print(f"   asset-uri: {len(names)}")

# ── 3) link to asset group ───────────────────────────────────────────────────
rl = post("assetGroupAssets",
          [{"create": {"assetGroup": AG, "asset": rn, "fieldType": ft}}
           for rn, ft in names], partial=True)
print(f"3) link to AG: HTTP {rl.status_code}")
pf = rl.json().get("partialFailureError")
if pf:
    errs = pf.get("details", [{}])[0].get("errors", [])
    for e in errs[:3]:
        print("   partial err:", json.dumps(e, ensure_ascii=False)[:200])
else:
    print(f"   linkuri create: {len(rl.json().get('results', []))}")

print("\nGATA — Belasil Asset Group 1 populat cu copy real.")
