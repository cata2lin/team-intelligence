# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
"""Make room (remove N Gemini squares) then link the Belasil banner image assets. --apply to run."""
import os, sys, json
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2, psycopg2.extras, requests
API="v21"; CID="7566352958"; AG="customers/7566352958/assetGroups/6570957552"; AGID="6570957552"
REMOVE_SQUARES=8
_PG_OK={"host","port","dbname","user","password","sslmode","sslrootcert","sslcert","sslkey","connect_timeout","application_name","options","channel_binding"}
def clean(d):
    p=urlsplit(d)
    return d if not p.query else urlunsplit((p.scheme,p.netloc,p.path,urlencode([(x,y) for x,y in parse_qsl(p.query,keep_blank_values=True) if x.lower() in _PG_OK]),p.fragment))
cx=psycopg2.connect(clean(os.environ["DATABASE_URL_METRICS"])); cx.set_session(readonly=True)
with cx.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
    c.execute('SELECT "developerToken" dev,"loginCustomerId" mcc,"oauthClientId" cid,"oauthClientSecret" csec,"refreshToken" rt FROM google_ads_connections WHERE "isActive"=true'); r=c.fetchone()
tok=requests.post("https://oauth2.googleapis.com/token",data={"grant_type":"refresh_token","client_id":r["cid"],"client_secret":r["csec"],"refresh_token":r["rt"]},timeout=20).json()["access_token"]
H={"Authorization":f"Bearer {tok}","developer-token":r["dev"],"login-customer-id":"".join(ch for ch in str(r["mcc"]) if ch.isdigit()),"Content-Type":"application/json"}
apply="--apply" in sys.argv
def post(svc,ops,partial=False):
    body={"operations":ops,"validateOnly":(not apply)}
    if partial: body["partialFailure"]=True
    return requests.post(f"https://googleads.googleapis.com/{API}/customers/{CID}/{svc}:mutate",headers=H,json=body,timeout=90)
def search(qq):
    out=[];url=f"https://googleads.googleapis.com/{API}/customers/{CID}/googleAds:search";body={"query":qq}
    while True:
        d=requests.post(url,headers=H,json=body,timeout=60).json(); out+=d.get("results",[])
        if not d.get("nextPageToken"): break
        body["pageToken"]=d["nextPageToken"]
    return out

# squares currently linked
squares=[r["assetGroupAsset"]["resourceName"] for r in search(f"SELECT asset_group_asset.resource_name FROM asset_group_asset WHERE asset_group.id={AGID} AND asset_group_asset.field_type='SQUARE_MARKETING_IMAGE'")]
torem=squares[:REMOVE_SQUARES]
# my banner assets
FT={"bel_ls":"MARKETING_IMAGE","bel_sq":"SQUARE_MARKETING_IMAGE","bel_pt":"PORTRAIT_MARKETING_IMAGE"}
banners={}
for r in search("SELECT asset.resource_name, asset.name FROM asset WHERE asset.type='IMAGE' AND asset.name LIKE 'Belasil banner%'"):
    nm=r["asset"]["name"]; key=nm.split()[-1]  # bel_ls_spalari
    pref=key[:6]
    if pref in FT and key not in banners: banners[key]=(r["asset"]["resourceName"],FT[pref])
print(f"squares legate: {len(squares)} | de scos: {len(torem)} | bannere de adăugat: {len(banners)}")
for k,(_,ft) in banners.items(): print("  +",k,ft)
if not apply: print("DRY-RUN — rulează cu --apply"); sys.exit(0)
# 1) remove squares
r1=post("assetGroupAssets",[{"remove":x} for x in torem],partial=True)
print("1) scoatere pătrate:",r1.status_code,"| scoase:",len(r1.json().get("results",[])))
# 2) link banners
r2=post("assetGroupAssets",[{"create":{"assetGroup":AG,"asset":res,"fieldType":ft}} for res,ft in banners.values()],partial=True)
print("2) adăugare bannere:",r2.status_code,"| adăugate:",len(r2.json().get("results",[])))
pf=r2.json().get("partialFailureError")
if pf: print("   err:",json.dumps(pf["details"][0]["errors"][0],ensure_ascii=False)[:200])
