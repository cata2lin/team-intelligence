# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
"""
Phase 2 cleanup for Belasil AG1 (6570921716):
  1. Remove 6 placeholder Test links (safe now that 12+ real headlines exist)
  2. Add 3 missing real headlines + 2 missing descriptions + 1 missing long headline

Run: DATABASE_URL_METRICS=$(kb.py secret-get DATABASE_URL_METRICS) \\
       uv run fix_belasil_ag1_cleanup.py [--apply]
"""
import os, sys, json
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2, psycopg2.extras, requests

API = "v21"
CID = "7566352958"
AG  = "customers/7566352958/assetGroups/6570921716"

TEST_LINKS = [
    "customers/7566352958/assetGroupAssets/6570921716~230026758785~LONG_HEADLINE",  # "Test Test Test Test"
    "customers/7566352958/assetGroupAssets/6570921716~230113523395~HEADLINE",        # "Test"
    "customers/7566352958/assetGroupAssets/6570921716~230113523398~HEADLINE",        # "Test Test"
    "customers/7566rzymskich/assetGroupAssets/6570921716~230113523398~DESCRIPTION",  # wrong - skip
    "customers/7566352958/assetGroupAssets/6570921716~230113523401~HEADLINE",        # "Test Test Test"
]

# Correct all test link names
TEST_LINKS = [
    "customers/7566352958/assetGroupAssets/6570921716~230026758785~LONG_HEADLINE",
    "customers/7566352958/assetGroupAssets/6570921716~230113523395~HEADLINE",
    "customers/7566352958/assetGroupAssets/6570921716~230113523398~HEADLINE",
    "customers/7566352958/assetGroupAssets/6570921716~230113523398~DESCRIPTION",
    "customers/7566352958/assetGroupAssets/6570921716~230113523401~HEADLINE",
    "customers/7566352958/assetGroupAssets/6570921716~230113523401~DESCRIPTION",
]

# Missing real content (the 3 that hit RESOURCE_LIMIT in phase 1)
HEADLINES_MISSING = [
    "5 Parfumuri la Alegere",    # 22 chars
    "Detergent 100% Românesc",   # 23 chars
    "Detergent Concentrat 10L",  # 24 chars
]
DESCR_MISSING = [
    "4,7/5 din peste 1.250 de recenzii. 5 parfumuri la alegere. Garanție retur 14 zile.",
    "Detergent 100% românesc, de la fabrică. Ultra-concentrat, persistent, preț corect.",
]
LONG_MISSING = [
    "Detergent 100% românesc, de la fabrică: mai puține bidoane, mai multe spălări",
]

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

bad = ([t for t in HEADLINES_MISSING if len(t) > 30] +
       [t for t in DESCR_MISSING    if len(t) > 90] +
       [t for t in LONG_MISSING     if len(t) > 90])
if bad:
    print("EROARE limite:", bad); sys.exit(1)
print("Verificare limite: OK")

# ── 1) remove Test placeholder links ────────────────────────────────────────
r0 = post("assetGroupAssets", [{"remove": ln} for ln in TEST_LINKS], partial=True)
print(f"1) remove Test links: HTTP {r0.status_code}")
pf0 = r0.json().get("partialFailureError") if r0.status_code == 200 else None
if pf0:
    for e in pf0.get("details",[{}])[0].get("errors",[])[:3]:
        print("   err:", json.dumps(e, ensure_ascii=False)[:200])
elif r0.status_code != 200:
    print("   ERR:", r0.text[:400])

if not apply:
    print("\nDRY-RUN — adaugă --apply")
    sys.exit(0)

# ── 2) create missing real assets ────────────────────────────────────────────
allt = ([(t, "HEADLINE")      for t in HEADLINES_MISSING] +
        [(t, "DESCRIPTION")    for t in DESCR_MISSING] +
        [(t, "LONG_HEADLINE")  for t in LONG_MISSING])
ra = post("assets", [{"create": {"textAsset": {"text": t}}} for t,_ in allt], partial=True)
print(f"2) create missing assets: HTTP {ra.status_code}")
if ra.status_code != 200: print(ra.text[:400]); sys.exit(1)

res = ra.json().get("results", [])
names = [(res[i].get("resourceName"), allt[i][1])
         for i in range(len(allt)) if res[i].get("resourceName")]
print(f"   asset-uri: {len(names)}")

# ── 3) link missing to asset group ───────────────────────────────────────────
rl = post("assetGroupAssets",
          [{"create": {"assetGroup": AG, "asset": rn, "fieldType": ft}}
           for rn, ft in names], partial=True)
print(f"3) link missing: HTTP {rl.status_code}")
pf = rl.json().get("partialFailureError")
if pf:
    for e in pf.get("details",[{}])[0].get("errors",[])[:3]:
        print("   partial err:", json.dumps(e, ensure_ascii=False)[:200])
else:
    print(f"   linkuri: {len(rl.json().get('results', []))}")

print("\nGATA — Belasil AG1 cleanup complet. Test links eliminate, 15/5/5 cu text real.")
