# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
"""
Belasil — completează asseturile pentru EXCELLENT.

Diagnoză:
  [ALS] P.Max (campanie 22478321481): logo+BN deja la campanie ✅ — nimic de adăugat.
  AG1 (campanie 22478291976): are 2 landscape/2 square/2 portrait → minimum 3 fiecare.
                               0 YouTube videos → adăugăm 5 din [ALS] P.Max.

Operații:
  A) Link 1 landscape + 1 square + 1 portrait + 5 video la AG1 (6570921716)
     Asseturile există deja în [ALS] P.Max (6570957552), le reutilizăm.

Run: DATABASE_URL_METRICS=<dsn> uv run fix_belasil_als_and_ag1.py [--apply]
"""
import os, sys, json
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2, psycopg2.extras, requests

API = "v21"
CID = "7566352958"

AG1 = "customers/7566352958/assetGroups/6570921716"

# Imagini din [ALS] P.Max pentru AG1 (1 per tip, ca să completăm la 3)
LANDSCAPE_FOR_AG1 = "customers/7566352958/assets/371198252617"   # BEST în ALS
SQUARE_FOR_AG1    = "customers/7566352958/assets/371195411827"   # LOW în ALS
PORTRAIT_FOR_AG1  = "customers/7566352958/assets/371097127529"   # din ALS

# Videos din [ALS] P.Max pentru AG1 (AG1 are 0 videos)
VIDEOS_FOR_AG1 = [
    "customers/7566352958/assets/371258526396",
    "customers/7566352958/assets/371258526399",
    "customers/7566352958/assets/371258526402",
    "customers/7566352958/assets/371258526405",
    "customers/7566352958/assets/371258526408",
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

def link_batch(label, ops):
    body = {"operations": ops, "partialFailure": True}
    if not apply:
        body["validateOnly"] = True
    r = requests.post(
        f"https://googleads.googleapis.com/{API}/customers/{CID}/assetGroupAssets:mutate",
        headers=H, json=body, timeout=60)
    pf = r.json().get("partialFailureError") if r.status_code == 200 else None
    ok = len(r.json().get("results", [])) if r.status_code == 200 else 0
    errs = []
    if pf:
        for e in pf.get("details", [{}])[0].get("errors", [])[:5]:
            errs.append(e.get("message","?")[:120])
    elif r.status_code != 200:
        errs.append(r.text[:300])
    print(f"  {label}: HTTP {r.status_code}  ok={ok}  errs={len(errs)}")
    for e in errs: print(f"    err: {e}")

print("=== Belasil AG1 — link imagini + video ===")
ops_ag1 = (
    [{"create": {"assetGroup": AG1, "asset": LANDSCAPE_FOR_AG1, "fieldType": "MARKETING_IMAGE"}}] +
    [{"create": {"assetGroup": AG1, "asset": SQUARE_FOR_AG1,    "fieldType": "SQUARE_MARKETING_IMAGE"}}] +
    [{"create": {"assetGroup": AG1, "asset": PORTRAIT_FOR_AG1,  "fieldType": "PORTRAIT_MARKETING_IMAGE"}}] +
    [{"create": {"assetGroup": AG1, "asset": rn, "fieldType": "YOUTUBE_VIDEO"}}
     for rn in VIDEOS_FOR_AG1]
)
link_batch("AG1 imagini+video", ops_ag1)

if not apply:
    print("\nDRY-RUN — adaugă --apply ca să execuți")
else:
    print("\nGATA — Belasil AG1 completat: 3 landscape, 3 square, 3 portrait, 5 video.")
