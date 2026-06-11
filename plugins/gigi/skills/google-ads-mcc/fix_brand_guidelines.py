# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
"""Satisfy PMax Brand Guidelines (campaign-level business name + logo), then link asset-group text."""
import os, sys, json, base64
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2, psycopg2.extras, requests
API="v20"; CID="7566352958"; CAMP="customers/7566352958/campaigns/22478321481"; AG="customers/7566352958/assetGroups/6570957552"
LOGODIR="/Users/gheorghebeschea/Downloads/Scripturi/belasil-creatives"
HEAD=["Detergent Gel Concentrat","200 de Spălări pe Bidon","Doar 0,49 lei pe Spălare","10L la 99 lei, de la 255",
 "Direct de la Producător","Balsam Inclus în Detergent","Spală Alb și Color","Detergent Rufe Premium",
 "4,7/5 din 1.250+ Recenzii","Parfum de Lungă Durată","Gel Dens, Delicat cu Tine","Transport Gratuit 150 lei",
 "5 Parfumuri la Alegere","Detergent 100% Românesc","Detergent Concentrat 10L"]
LONG=["Detergent gel ultra-concentrat: 200 de spălări dintr-un bidon de 10L, 0,49 lei/spălare",
 "Direct de la producător: 10L la 99 lei (de la 255). Transport gratuit peste 150 lei",
 "Gel dens, delicat cu hainele și pielea. Balsam inclus, spală alb și color",
 "4,7/5 din peste 1.250 de recenzii. 5 parfumuri, persistă mult, garanție 14 zile",
 "Detergent 100% românesc, de la fabrică: mai puține bidoane, mai multe spălări"]
DESCR=["Detergent gel concentrat. 200 spălări, 0,49 lei/spălare.",
 "De la producător, fără intermediari. 10L la 99 lei. Transport gratuit peste 150 lei.",
 "Gel dens, delicat cu hainele. Balsam inclus. Spală alb și color, manual și automat.",
 "4,7/5 din peste 1.250 de recenzii. 5 parfumuri la alegere. Garanție retur 14 zile.",
 "Detergent 100% românesc, de la fabrică. Ultra-concentrat, persistent, preț corect."]
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
def b64(path): return base64.b64encode(open(path,"rb").read()).decode()
def rn(resp,i):
    res=resp.json().get("results",[]); return res[i].get("resourceName") if i<len(res) and res[i] else None

# 1) create brand assets: business name + 2 logos
acreate=[{"create":{"textAsset":{"text":"Belasil"}}},
         {"create":{"name":"Belasil Logo 1x1","imageAsset":{"data":b64(f"{LOGODIR}/logo_belasil_1x1.png")}}},
         {"create":{"name":"Belasil Logo 4x1","imageAsset":{"data":b64(f"{LOGODIR}/logo_belasil_4x1.png")}}}]
r1=post("assets",acreate,partial=True)
print("1) brand assets:",r1.status_code, "" if r1.status_code==200 else r1.text[:400])
if not apply: print("DRY-RUN ok — rulează cu --apply"); sys.exit(0)
bn,lg1,lg4=rn(r1,0),rn(r1,1),rn(r1,2)
print("   bn:",bn,"| logo1x1:",lg1,"| logo4x1:",lg4)
if r1.json().get("partialFailureError"): print("   ASSET ERR:",json.dumps(r1.json()["partialFailureError"]["details"][0],ensure_ascii=False)[:300])
# link them as CAMPAIGN assets
clink=[{"create":{"campaign":CAMP,"asset":bn,"fieldType":"BUSINESS_NAME"}},
       {"create":{"campaign":CAMP,"asset":lg1,"fieldType":"LOGO"}},
       {"create":{"campaign":CAMP,"asset":lg4,"fieldType":"LANDSCAPE_LOGO"}}]
r2=post("campaignAssets",clink,partial=True)
print("2) link brand to campaign:",r2.status_code)
if r2.json().get("partialFailureError"): print("   LINK ERR:",json.dumps(r2.json()["partialFailureError"]["details"][0],ensure_ascii=False)[:300])
else: print("   ok, linkuri:",len(r2.json().get("results",[])))

# 3) now the asset-group text (re-create returns existing) + link
allt=[(t,"HEADLINE") for t in HEAD]+[(t,"LONG_HEADLINE") for t in LONG]+[(t,"DESCRIPTION") for t in DESCR]
r3=post("assets",[{"create":{"textAsset":{"text":t}}} for t,_ in allt],partial=True)
names=[(rn(r3,i),allt[i][1]) for i in range(len(allt)) if rn(r3,i)]
r4=post("assetGroupAssets",[{"create":{"assetGroup":AG,"asset":n,"fieldType":ft}} for n,ft in names],partial=True)
print("3) link text to asset group:",r4.status_code,"| linkuri:",len(r4.json().get("results",[])))
if r4.json().get("partialFailureError"): print("   detalii:",json.dumps(r4.json()["partialFailureError"]["details"][0],ensure_ascii=False)[:200])
