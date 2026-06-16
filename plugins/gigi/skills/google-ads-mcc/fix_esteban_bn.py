# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
"""
Swap Esteban AG1 business name from 'ARONA SRL' → 'Maison d'Esteban'.
Sends both operations (remove + add) in a single assetGroupAssets:mutate call
so the swap is atomic. Requires partialFailure=false.

Run: DATABASE_URL_METRICS=$(kb.py secret-get DATABASE_URL_METRICS) \\
       uv run fix_esteban_bn.py [--apply]
"""
import os, sys, json
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2, psycopg2.extras, requests

API = "v21"
CID = "5229815058"
AG  = "customers/5229815058/assetGroups/6720307893"

ARONA_BN_LINK   = "customers/5229815058/assetGroupAssets/6720307893~370843778323~BUSINESS_NAME"
MAISON_TEXT     = "Maison d'Esteban"   # existing asset 370791436004 in this account

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

# ── Step 1: ensure 'Maison d'Esteban' text asset exists, get resource name ──
r_asset = requests.post(
    f"https://googleads.googleapis.com/{API}/customers/{CID}/assets:mutate",
    headers=H,
    json={"operations": [{"create": {"textAsset": {"text": MAISON_TEXT}}}],
          "validateOnly": not apply, "partialFailure": True},
    timeout=30)
print(f"1) ensure asset: HTTP {r_asset.status_code}")
if r_asset.status_code != 200:
    print("   ERR:", r_asset.text[:400]); sys.exit(1)

if not apply:
    print("   DRY-RUN OK")
    print("\nDRY-RUN — adaugă --apply ca să execuți")
    sys.exit(0)

asset_rn = r_asset.json()["results"][0].get("resourceName")
print(f"   asset RN: {asset_rn}")

# ── Step 2: atomic swap — remove ARONA SRL + add Maison d'Esteban in one call
ops = [
    {"remove": ARONA_BN_LINK},
    {"create": {"assetGroup": AG, "asset": asset_rn, "fieldType": "BUSINESS_NAME"}},
]
r_swap = requests.post(
    f"https://googleads.googleapis.com/{API}/customers/{CID}/assetGroupAssets:mutate",
    headers=H,
    json={"operations": ops, "validateOnly": False, "partialFailure": False},
    timeout=30)
print(f"2) BN swap: HTTP {r_swap.status_code}")
if r_swap.status_code == 200:
    print(f"   OK — 'ARONA SRL' eliminat, 'Maison d'Esteban' adăugat")
    print(json.dumps(r_swap.json(), ensure_ascii=False)[:300])
else:
    print("   FAILED:", r_swap.text[:500])
    print("\n   Încerc remove-only (poate nu necesită BN minim)...")
    r_rm = requests.post(
        f"https://googleads.googleapis.com/{API}/customers/{CID}/assetGroupAssets:mutate",
        headers=H,
        json={"operations": [{"remove": ARONA_BN_LINK}], "validateOnly": False, "partialFailure": True},
        timeout=30)
    print(f"   remove-only: HTTP {r_rm.status_code}", r_rm.text[:300])
