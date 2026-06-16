# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
"""Attach the 2nd batch of YouTube videos to the Belasil PMax asset group. --apply to run."""
import os, sys, json
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2, psycopg2.extras, requests
API="v24"; CID="7566352958"; AG="customers/7566352958/assetGroups/6570957552"
VIDS={"BwVxQuKUBQc":"Belasil - ad 2","VfzTVMjh3Yg":"Belasil - awareness","OxnWXTLNF-o":"Belasil - risipa",
      "eVAfQZBPRdY":"Belasil - vecina curieru","s_R9iTZTUVo":"Belasil - UGC Maria Vlad","ziDe5qiOQWM":"Belasil - turnat gel (demo)"}
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
def rn(resp,i):
    res=resp.json().get("results",[]); return res[i].get("resourceName") if i<len(res) and res[i] else None
vids=list(VIDS.items())
r1=post("assets",[{"create":{"name":n,"youtubeVideoAsset":{"youtubeVideoId":v}}} for v,n in vids],partial=True)
print("1) create video assets:",r1.status_code)
if not apply: print("DRY-RUN"); sys.exit(0)
names=[rn(r1,i) for i in range(len(vids))]
# existing assets dedupe: query the just-uploaded by video id if create returned existing
r2=post("assetGroupAssets",[{"create":{"assetGroup":AG,"asset":n,"fieldType":"YOUTUBE_VIDEO"}} for n in names if n],partial=True)
print("2) link to asset group:",r2.status_code,"| linkuri:",len(r2.json().get("results",[])))
if r2.status_code!=200: print("   FULL:",r2.text[:600])
pf=r2.json().get("partialFailureError")
if pf: print("   err:",json.dumps(pf["details"][0]["errors"][0],ensure_ascii=False)[:300])
