# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
"""Disable Brand Guidelines on the Belasil PMax, then link all text assets to the asset group."""
import os, sys, json
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2, psycopg2.extras, requests
API="v24"; CID="7566352958"; CAMP="customers/7566352958/campaigns/22478321481"; AG="customers/7566352958/assetGroups/6570957552"
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
def rn(resp,i):
    res=resp.json().get("results",[]); return res[i].get("resourceName") if i<len(res) and res[i] else None

# 1) disable brand guidelines
r0=post("campaigns",[{"update":{"resourceName":CAMP,"brandGuidelinesEnabled":False},"updateMask":"brand_guidelines_enabled"}])
print("0) disable brand guidelines:",r0.status_code, "" if r0.status_code==200 else r0.text[:400])
if not apply: print("DRY-RUN — rulează cu --apply"); sys.exit(0)
# 2) text assets + link
allt=[(t,"HEADLINE") for t in HEAD]+[(t,"LONG_HEADLINE") for t in LONG]+[(t,"DESCRIPTION") for t in DESCR]
ra=post("assets",[{"create":{"textAsset":{"text":t}}} for t,_ in allt],partial=True)
names=[(rn(ra,i),allt[i][1]) for i in range(len(allt)) if rn(ra,i)]
rl=post("assetGroupAssets",[{"create":{"assetGroup":AG,"asset":n,"fieldType":ft}} for n,ft in names],partial=True)
print("2) link text:",rl.status_code,"| linkuri:",len(rl.json().get("results",[])))
pf=rl.json().get("partialFailureError")
if pf: print("   primul err:",json.dumps(pf["details"][0]["errors"][0],ensure_ascii=False)[:200])
