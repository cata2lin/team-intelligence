# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
"""Add account-level Search extensions (sitelinks + callouts + structured snippet) — lifts every RSA's
ad strength + ad rank at once. Dry-run by default; --apply to execute. CIDARG selects the account."""
import os, sys, json
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2, psycopg2.extras, requests
API=os.environ.get("GADS_API_VERSION","v21")
CID=os.environ.get("CIDARG","")

DATA = {
 "5229815058": {  # Esteban — Maison d'Esteban
   "url":"https://esteban.ro/",
   "sitelinks":[("Parfumuri Bărbați","Inspirate din mari case","12h+ persistență"),
                ("Parfumuri Damă","Esența care contează","Livrare 24-48h"),
                ("Set Cadou 2+1","Cadou la fiecare comandă","Ofertă limitată"),
                ("Toate Parfumurile","Peste 100 de arome","De la 99 lei"),
                ("Recenzii Clienți","Mii de clienți mulțumiți","Evaluare 4,8 din 5")],
   "callouts":["Livrare 24-48h","Plata ramburs","12h+ persistență","Inspirate din branduri","Retur 14 zile","Esența de designer"],
   "snippet":("Types",["Florale","Lemnoase","Orientale","Citrice","Gourmand"]),
 },
 "7566352958": {  # Belasil — detergent gel concentrat
   "url":"https://belasil.ro/",
   "sitelinks":[("Detergent 10L","200 de spălări/bidon","0,49 lei pe spălare"),
                ("Lavete Microfibră","Curăță fără urme","Set complet"),
                ("Oferta -36%","10L la 99 lei (de la 255)","Stoc limitat"),
                ("Toate Parfumurile","5 arome la alegere","Balsam inclus"),
                ("Recenzii","4,7 din 5 stele","din 1.250+ recenzii")],
   "callouts":["200 spălări/bidon","0,49 lei/spălare","De la producător","Balsam inclus","Transport gratuit 150 lei+","Garanție 14 zile"],
   "snippet":("Types",["Alpin","Ocean","Lavandă","Citrice","Floral"]),
 },
}
if CID not in DATA: sys.exit("set CIDARG=5229815058 (Esteban) sau 7566352958 (Belasil)")
D=DATA[CID]
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

# 1) create assets
ops=[{"create":{"finalUrls":[D["url"]],"sitelinkAsset":{"linkText":t,"description1":d1,"description2":d2}}} for t,d1,d2 in D["sitelinks"]]
ops+=[{"create":{"calloutAsset":{"calloutText":c}}} for c in D["callouts"]]
ops+=[{"create":{"structuredSnippetAsset":{"header":D["snippet"][0],"values":D["snippet"][1]}}}]
r1=post("assets",ops,partial=True)
print("1) create assets:",r1.status_code, "" if r1.status_code==200 else r1.text[:500])
if not apply: print("DRY-RUN — rulează cu --apply"); sys.exit(0)
res=[rn(r1,i) for i in range(len(ops))]
nsl=len(D["sitelinks"]); nco=len(D["callouts"])
slk=res[:nsl]; clt=res[nsl:nsl+nco]; snp=res[nsl+nco:]
# 2) link at account level
links=[{"create":{"asset":a,"fieldType":"SITELINK"}} for a in slk if a]
links+=[{"create":{"asset":a,"fieldType":"CALLOUT"}} for a in clt if a]
links+=[{"create":{"asset":a,"fieldType":"STRUCTURED_SNIPPET"}} for a in snp if a]
r2=post("customerAssets",links,partial=True)
print("2) link account-level:",r2.status_code,"| linkuri:",len(r2.json().get("results",[])))
pf=r2.json().get("partialFailureError")
if pf: print("   err:",json.dumps(pf["details"][0]["errors"][0],ensure_ascii=False)[:220])
else: print(f"   ✓ {len(slk)} sitelinks + {len(clt)} callouts + 1 structured snippet pe contul {CID}")
