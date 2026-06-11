# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
"""Populate the Belasil PMax asset group with headlines / long headlines / descriptions / business name."""
import os, sys, json
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2, psycopg2.extras, requests
API="v20"; CID="7566352958"; AG="customers/7566352958/assetGroups/6570957552"
HEADLINES=["Detergent Gel Concentrat","200 de Spălări pe Bidon","Doar 0,49 lei pe Spălare","10L la 99 lei, de la 255",
 "Direct de la Producător","Balsam Inclus în Detergent","Spală Alb și Color","Detergent Rufe Premium",
 "4,7/5 din 1.250+ Recenzii","Parfum de Lungă Durată","Gel Dens, Delicat cu Tine","Transport Gratuit 150 lei",
 "5 Parfumuri la Alegere","Economic la Fiecare Spălare","Detergent Concentrat 10L"]
LONG=["Detergent gel ultra-concentrat: 200 de spălări dintr-un bidon de 10L, 0,49 lei/spălare",
 "Direct de la producător: 10L la 99 lei (de la 255). Transport gratuit peste 150 lei",
 "Gel dens, delicat cu hainele și pielea. Balsam inclus, spală alb și color",
 "4,7/5 din peste 1.250 de recenzii. 5 parfumuri, persistă mult, garanție 14 zile",
 "Mai puține bidoane, mai multe spălări, preț de la producător — detergentul inteligent"]
DESCR=["Detergent gel concentrat. 200 spălări, 0,49 lei/spălare.",
 "De la producător, fără intermediari. 10L la 99 lei. Transport gratuit peste 150 lei.",
 "Gel dens, delicat cu hainele. Balsam inclus. Spală alb și color, manual și automat.",
 "4,7/5 din peste 1.250 de recenzii. 5 parfumuri la alegere. Garanție retur 14 zile.",
 "Economisești la fiecare spălare: ultra-concentrat, persistent, preț de la producător."]
BUSINESS="Belasil"
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
    return requests.post(f"https://googleads.googleapis.com/{API}/customers/{CID}/{svc}:mutate",headers=H,json=body,timeout=60)

# 1) create all text assets
allt=[(t,"HEADLINE") for t in HEADLINES]+[(t,"LONG_HEADLINE") for t in LONG]+[(t,"DESCRIPTION") for t in DESCR]+[(BUSINESS,"BUSINESS_NAME")]
r1=post("assets",[{"create":{"textAsset":{"text":t}}} for t,_ in allt],partial=True)
print("1) create text assets:",r1.status_code)
if r1.status_code!=200: print(r1.text[:600]); sys.exit(1)
if not apply:
    # check char limits locally
    bad=[t for t in HEADLINES if len(t)>30]+[t for t in LONG if len(t)>90]+[t for t in DESCR if len(t)>90]
    print("   over-limit:",bad if bad else "none"); print("   DRY-RUN — rulează cu --apply"); sys.exit(0)
res=r1.json().get("results",[]); names=[(res[i].get("resourceName"),allt[i][1]) for i in range(len(allt)) if res[i].get("resourceName")]
print("   asset-uri create/găsite:",len(names))
# 2) link to asset group by field type
r2=post("assetGroupAssets",[{"create":{"assetGroup":AG,"asset":rn,"fieldType":ft}} for rn,ft in names],partial=True)
print("2) link to asset group:",r2.status_code)
print(r2.text[:1500])
