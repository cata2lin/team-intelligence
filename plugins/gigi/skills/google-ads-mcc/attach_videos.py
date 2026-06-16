# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
"""Attach uploaded YouTube videos to a Belasil PMax asset group (2 steps; dry-run unless --apply)."""
import os, sys, json
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2, psycopg2.extras, requests
API="v21"; CID="7566352958"
AG="customers/7566352958/assetGroups/6570957552"
VIDEOS=[("iWh_ReD3mJ4","Belasil - 99lei ad 1"),("zSnRxkkCS1A","Belasil - 99 lei ad 2 (subtitrat)"),
        ("OHcOiWI5SIo","Belasil - ad 1"),("DUTm825dWew","Belasil - proba 1 (vertical)"),
        ("A5zoYFDhiS8","Belasil - proba 2 (vertical)"),("KFT-OKV0EkI","Belasil - UGC Madalina")]
_PG_OK={"host","port","dbname","user","password","sslmode","sslrootcert","sslcert","sslkey","connect_timeout","application_name","options","channel_binding"}
def clean(d):
    p=urlsplit(d)
    if not p.query: return d
    k=[(x,y) for x,y in parse_qsl(p.query,keep_blank_values=True) if x.lower() in _PG_OK]
    return urlunsplit((p.scheme,p.netloc,p.path,urlencode(k),p.fragment))
cx=psycopg2.connect(clean(os.environ["DATABASE_URL_METRICS"])); cx.set_session(readonly=True)
with cx.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
    c.execute('SELECT "developerToken" dev,"loginCustomerId" mcc,"oauthClientId" cid,"oauthClientSecret" csec,"refreshToken" rt FROM google_ads_connections WHERE "isActive"=true'); r=c.fetchone()
tok=requests.post("https://oauth2.googleapis.com/token",data={"grant_type":"refresh_token","client_id":r["cid"],"client_secret":r["csec"],"refresh_token":r["rt"]},timeout=20).json()["access_token"]
H={"Authorization":f"Bearer {tok}","developer-token":r["dev"],"login-customer-id":"".join(ch for ch in str(r["mcc"]) if ch.isdigit()),"Content-Type":"application/json"}
apply="--apply" in sys.argv
def post(service, ops, partial=False):
    body={"operations":ops,"validateOnly":(not apply),"partialFailure":partial}
    return requests.post(f"https://googleads.googleapis.com/{API}/customers/{CID}/{service}:mutate",headers=H,json=body,timeout=60)

# step 1: create the YouTube video assets
aops=[{"create":{"name":n,"youtubeVideoAsset":{"youtubeVideoId":v}}} for v,n in VIDEOS]
r1=post("assets",aops,partial=True)
print("STEP1 assets:",r1.status_code)
if r1.status_code!=200: print(r1.text[:800]); sys.exit(1)
res=r1.json().get("results",[])
names=[x.get("resourceName") for x in res if x and x.get("resourceName")]
print("  asset resource names:",len(names))
if not apply:
    print("  (dry-run: validateOnly nu întoarce resourceName real; rulează cu --apply)"); sys.exit(0)

# step 2: link assets to the asset group as VIDEO
lops=[{"create":{"assetGroup":AG,"asset":rn,"fieldType":"VIDEO"}} for rn in names]
r2=post("assetGroupAssets",lops,partial=True)
print("STEP2 link:",r2.status_code)
print(r2.text[:900])
