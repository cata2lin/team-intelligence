# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
"""Set asset group Final URL, then attach the 6 YouTube videos. --apply to execute."""
import os, sys, json
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2, psycopg2.extras, requests
API="v21"; CID="7566352958"; AG="customers/7566352958/assetGroups/6570957552"; URL="https://belasil.ro/"
VIDS={"iWh_ReD3mJ4":"Belasil - 99lei ad 1","zSnRxkkCS1A":"Belasil - 99 lei ad 2 (subtitrat)",
      "OHcOiWI5SIo":"Belasil - ad 1","DUTm825dWew":"Belasil - proba 1 (vertical)",
      "A5zoYFDhiS8":"Belasil - proba 2 (vertical)","KFT-OKV0EkI":"Belasil - UGC Madalina"}
_PG_OK={"host","port","dbname","user","password","sslmode","sslrootcert","sslcert","sslkey","connect_timeout","application_name","options","channel_binding"}
def clean(d):
    p=urlsplit(d)
    if not p.query: return d
    return urlunsplit((p.scheme,p.netloc,p.path,urlencode([(x,y) for x,y in parse_qsl(p.query,keep_blank_values=True) if x.lower() in _PG_OK]),p.fragment))
cx=psycopg2.connect(clean(os.environ["DATABASE_URL_METRICS"])); cx.set_session(readonly=True)
with cx.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
    c.execute('SELECT "developerToken" dev,"loginCustomerId" mcc,"oauthClientId" cid,"oauthClientSecret" csec,"refreshToken" rt FROM google_ads_connections WHERE "isActive"=true'); r=c.fetchone()
tok=requests.post("https://oauth2.googleapis.com/token",data={"grant_type":"refresh_token","client_id":r["cid"],"client_secret":r["csec"],"refresh_token":r["rt"]},timeout=20).json()["access_token"]
H={"Authorization":f"Bearer {tok}","developer-token":r["dev"],"login-customer-id":"".join(ch for ch in str(r["mcc"]) if ch.isdigit()),"Content-Type":"application/json"}
apply="--apply" in sys.argv
def post(svc,ops,partial=False):
    body={"operations":ops,"validateOnly":(not apply)}
    if partial: body["partialFailure"]=True
    return requests.post(f"https://googleads.googleapis.com/{API}/customers/{CID}/{svc}:mutate",headers=H,json=body,timeout=60)
def search(qq):
    out=[];url=f"https://googleads.googleapis.com/{API}/customers/{CID}/googleAds:search";body={"query":qq}
    while True:
        rr=requests.post(url,headers=H,json=body,timeout=60); d=rr.json(); out+=d.get("results",[])
        if not d.get("nextPageToken"): break
        body["pageToken"]=d["nextPageToken"]
    return out

# 1) set Final URL on the asset group
r1=post("assetGroups",[{"update":{"resourceName":AG,"finalUrls":[URL]},"updateMask":"final_urls"}])
print("1) set final_url:",r1.status_code, "" if r1.status_code==200 else r1.text[:300])

# 2) find existing video assets, create any missing
have={}
for row in search("SELECT asset.resource_name, asset.youtube_video_asset.youtube_video_id FROM asset WHERE asset.type='YOUTUBE_VIDEO'"):
    a=row["asset"]; vid=(a.get("youtubeVideoAsset") or {}).get("youtubeVideoId")
    if vid: have[vid]=a["resourceName"]
missing=[v for v in VIDS if v not in have]
if missing:
    cr=post("assets",[{"create":{"name":VIDS[v],"youtubeVideoAsset":{"youtubeVideoId":v}}} for v in missing],partial=True)
    print("   created missing assets:",cr.status_code)
    if apply:
        for v,res in zip(missing,[x.get("resourceName") for x in cr.json().get("results",[])]):
            if res: have[v]=res

# 3) link all 6 to the asset group as VIDEO
if apply:
    res=[have[v] for v in VIDS if v in have]
    print("   asset resource names:",len(res),res[:2])
    r3=post("assetGroupAssets",[{"create":{"assetGroup":AG,"asset":rn,"fieldType":"YOUTUBE_VIDEO"}} for rn in res],partial=True)
    print("3) link videos:",r3.status_code)
    print(r3.text[:1600])
else:
    print("DRY-RUN — rulează cu --apply")
