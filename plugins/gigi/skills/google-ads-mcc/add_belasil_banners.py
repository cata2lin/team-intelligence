# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
"""Upload Belasil banner PNGs as image assets and link them to the PMax asset group."""
import os, sys, json, base64
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2, psycopg2.extras, requests
API="v21"; CID="7566352958"; AG="customers/7566352958/assetGroups/6570957552"
DIR="/Users/gheorghebeschea/Downloads/Scripturi/belasil-creatives"
IMGS=[("bel_ls_spalari.png","MARKETING_IMAGE"),("bel_ls_pret.png","MARKETING_IMAGE"),
 ("bel_ls_producator.png","MARKETING_IMAGE"),("bel_ls_recenzii.png","MARKETING_IMAGE"),
 ("bel_sq_spalari.png","SQUARE_MARKETING_IMAGE"),("bel_sq_pret.png","SQUARE_MARKETING_IMAGE"),
 ("bel_pt_producator.png","PORTRAIT_MARKETING_IMAGE"),("bel_pt_parfum.png","PORTRAIT_MARKETING_IMAGE")]
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
def b64(p): return base64.b64encode(open(p,"rb").read()).decode()
def rn(resp,i):
    res=resp.json().get("results",[]); return res[i].get("resourceName") if i<len(res) and res[i] else None

r1=post("assets",[{"create":{"name":f"Belasil banner {f.replace('.png','')}","imageAsset":{"data":b64(f"{DIR}/{f}")}}} for f,_ in IMGS],partial=True)
print("1) create image assets:",r1.status_code, "" if r1.status_code==200 else r1.text[:500])
if not apply: print("DRY-RUN — rulează cu --apply"); sys.exit(0)
names=[(rn(r1,i),IMGS[i][1]) for i in range(len(IMGS)) if rn(r1,i)]
print("   imagini create:",len(names))
r2=post("assetGroupAssets",[{"create":{"assetGroup":AG,"asset":n,"fieldType":ft}} for n,ft in names],partial=True)
print("2) link to asset group:",r2.status_code,"| linkuri:",len(r2.json().get("results",[])))
pf=r2.json().get("partialFailureError")
if pf: print("   err:",json.dumps(pf["details"][0]["errors"][0],ensure_ascii=False)[:200])
