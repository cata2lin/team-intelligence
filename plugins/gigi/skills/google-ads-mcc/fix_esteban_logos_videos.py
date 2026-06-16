# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
"""
Esteban — două operații:
  A) Swap BN la nivel de campanie (23924430848):
     'ARONA SRL' (370843778323) → 'Maison d'Esteban' (370791436004)
     brand_guidelines_enabled=True → BN trebuie la campanie, nu la asset group.
  B) Link 5 video + 3 landscape + 3 square + 3 portrait la AG1 (6720307893)
     Imagini/video reutilizate din Bărbați AG (deja în cont).

Run: DATABASE_URL_METRICS=<dsn> uv run fix_esteban_logos_videos.py [--apply]
"""
import os, sys, json
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2, psycopg2.extras, requests

API = "v21"
CID = "5229815058"

CAMPAIGN_RN = "customers/5229815058/campaigns/23924430848"

# BN la nivel campanie: de eliminat / de adăugat
ARONA_BN_CAMPAIGN_LINK = "customers/5229815058/campaignAssets/23924430848~370843778323~BUSINESS_NAME"
MAISON_BN_RN           = "customers/5229815058/assets/370791436004"   # "Maison d'Esteban"

AG1 = "customers/5229815058/assetGroups/6720307893"

# Imagini BEST/GOOD din Bărbați AG (pentru AG1)
LANDSCAPE_FOR_AG1 = [
    "customers/5229815058/assets/370825475513",   # BEST
    "customers/5229815058/assets/370995000945",   # GOOD
    "customers/5229815058/assets/370829356529",   # LOW (al 3-lea)
]
SQUARE_FOR_AG1 = [
    "customers/5229815058/assets/370998754887",   # BEST
    "customers/5229815058/assets/370995591672",   # GOOD
    "customers/5229815058/assets/370747578101",   # GOOD
]
PORTRAIT_FOR_AG1 = [
    "customers/5229815058/assets/370829356490",
    "customers/5229815058/assets/370922189227",
    "customers/5229815058/assets/370926744724",
]
VIDEOS_FOR_AG1 = [
    "customers/5229815058/assets/370823509940",
    "customers/5229815058/assets/370823622710",
    "customers/5229815058/assets/370823757464",
    "customers/5229815058/assets/370823911382",
    "customers/5229815058/assets/370921686052",
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

def mutate_campaign_assets(ops, partial=True):
    body = {"operations": ops, "partialFailure": partial}
    if not apply:
        body["validateOnly"] = True
    r = requests.post(
        f"https://googleads.googleapis.com/{API}/customers/{CID}/campaignAssets:mutate",
        headers=H, json=body, timeout=60)
    return r

def mutate_ag_assets(ops):
    body = {"operations": ops, "partialFailure": True}
    if not apply:
        body["validateOnly"] = True
    r = requests.post(
        f"https://googleads.googleapis.com/{API}/customers/{CID}/assetGroupAssets:mutate",
        headers=H, json=body, timeout=60)
    return r

def show(label, r):
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

# ── A) Swap BN la nivel campanie (atomic) ─────────────────────────────────────
print("=== A) Swap BN campanie 23924430848: ARONA SRL → Maison d'Esteban ===")
ops_bn = [
    {"remove": ARONA_BN_CAMPAIGN_LINK},
    {"create": {"campaign": CAMPAIGN_RN, "asset": MAISON_BN_RN, "fieldType": "BUSINESS_NAME"}},
]
r_bn = mutate_campaign_assets(ops_bn, partial=False)
show("BN swap campanie", r_bn)

# ── B) Video + imagini la AG1 ─────────────────────────────────────────────────
print("\n=== B) Link video + imagini la AG1 ===")
ops_ag1 = (
    [{"create": {"assetGroup": AG1, "asset": rn, "fieldType": "YOUTUBE_VIDEO"}}
     for rn in VIDEOS_FOR_AG1] +
    [{"create": {"assetGroup": AG1, "asset": rn, "fieldType": "MARKETING_IMAGE"}}
     for rn in LANDSCAPE_FOR_AG1] +
    [{"create": {"assetGroup": AG1, "asset": rn, "fieldType": "SQUARE_MARKETING_IMAGE"}}
     for rn in SQUARE_FOR_AG1] +
    [{"create": {"assetGroup": AG1, "asset": rn, "fieldType": "PORTRAIT_MARKETING_IMAGE"}}
     for rn in PORTRAIT_FOR_AG1]
)
r_ag1 = mutate_ag_assets(ops_ag1)
show("AG1 media", r_ag1)

if not apply:
    print("\nDRY-RUN — adaugă --apply ca să execuți")
else:
    print("\nGATA — BN campanie Esteban → Maison d'Esteban; AG1 completat cu video+imagini.")
