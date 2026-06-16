# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
import os, sys, json
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2, psycopg2.extras, requests
API="v24"; CID="7566352958"; AG="customers/7566352958/assetGroups/6570957552"
WANT={"BwVxQuKUBQc","VfzTVMjh3Yg","OxnWXTLNF-o","eVAfQZBPRdY","s_R9iTZTUVo","ziDe5qiOQWM"}
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
def search(q):
    out=[];url=f"https://googleads.googleapis.com/{API}/customers/{CID}/googleAds:search";body={"query":q}
    while True:
        d=requests.post(url,headers=H,json=body,timeout=60).json();out+=d.get("results",[])
        if not d.get("nextPageToken"): break
        body["pageToken"]=d["nextPageToken"]
    return out
have={}
for row in search("SELECT asset.resource_name, asset.youtube_video_asset.youtube_video_id FROM asset WHERE asset.type='YOUTUBE_VIDEO'"):
    a=row["asset"]; vid=(a.get("youtubeVideoAsset") or {}).get("youtubeVideoId")
    if vid in WANT: have[vid]=a["resourceName"]
print("video assets găsite:",len(have),"din",len(WANT))
ops=[{"create":{"assetGroup":AG,"asset":res,"fieldType":"YOUTUBE_VIDEO"}} for res in have.values()]
body={"operations":ops,"validateOnly":(not apply),"partialFailure":True}
rr=requests.post(f"https://googleads.googleapis.com/{API}/customers/{CID}/assetGroupAssets:mutate",headers=H,json=body,timeout=60)
print(("APLICAT" if apply else "DRY-RUN"),"| HTTP",rr.status_code)
j=rr.json()
print("  linkuri:",len(j.get("results",[])))
if rr.status_code!=200: print("  ERR:",rr.text[:500])
elif j.get("partialFailureError"): print("  partial:",json.dumps(j["partialFailureError"]["details"][0]["errors"][0],ensure_ascii=False)[:300])
