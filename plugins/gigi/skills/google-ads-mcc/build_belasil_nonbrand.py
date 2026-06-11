# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
"""One-off: create Belasil non-brand Search campaign (PAUSED) atomically via GoogleAdsService.mutate."""
import os, sys, json
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2, psycopg2.extras, requests

API="v20"; CID="7566352958"
_PG_OK={"host","port","dbname","user","password","sslmode","sslrootcert","sslcert","sslkey","connect_timeout","application_name","options","channel_binding"}
def clean(dsn):
    p=urlsplit(dsn)
    if not p.query: return dsn
    k=[(x,y) for x,y in parse_qsl(p.query,keep_blank_values=True) if x.lower() in _PG_OK]
    return urlunsplit((p.scheme,p.netloc,p.path,urlencode(k),p.fragment))
def conn_creds():
    dsn=os.environ["DATABASE_URL_METRICS"]; cx=psycopg2.connect(clean(dsn)); cx.set_session(readonly=True)
    with cx.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
        c.execute('SELECT "developerToken" dev,"loginCustomerId" mcc,"oauthClientId" cid,"oauthClientSecret" csec,"refreshToken" rt FROM google_ads_connections WHERE "isActive"=true')
        r=c.fetchone()
    cx.close(); return dict(r)
def token(c):
    return requests.post("https://oauth2.googleapis.com/token",data={"grant_type":"refresh_token","client_id":c["cid"],"client_secret":c["csec"],"refresh_token":c["rt"]},timeout=20).json()["access_token"]

c=conn_creds(); tok=token(c)
H={"Authorization":f"Bearer {tok}","developer-token":c["dev"],"login-customer-id":"".join(ch for ch in str(c["mcc"]) if ch.isdigit()),"Content-Type":"application/json"}
def rn(kind,i): return f"customers/{CID}/{kind}/{i}"

URL="https://belasil.ro/"
HEADLINES=["Detergent Gel Concentrat","200 Spălări dintr-un Bidon","Doar 0,49 lei pe Spălare","De la Producător, -36%","Detergent Rufe Premium","Balsam Inclus în Detergent","10L la 99 lei (de la 255)","Transport Gratuit 150 lei","Gel Dens, Delicat cu Haine","4,7/5 din 1.250+ Recenzii","Spală Alb și Color","Parfum de Lungă Durată","Direct de la Fabrică","Belasil Detergent Gel","5 Parfumuri la Alegere"]
DESCR=["Detergent gel concentrat: 200 de spălări dintr-un bidon de 10L, doar 0,49 lei/spălare.","De la producător, fără intermediari. 10L la 99 lei. Transport gratuit peste 150 lei.","Gel dens, delicat cu hainele. Balsam inclus. Spală alb și color, manual și automat.","4,7/5 din peste 1.250 de recenzii. Garanție retur 14 zile. 5 parfumuri la alegere."]
ADGROUPS=[
 ("Detergent gel / lichid", ["detergent lichid","detergent gel","detergent gel concentrat","detergent lichid concentrat","detergent rufe lichid","detergent lichid rufe","detergent concentrat rufe"]),
 ("Detergent ieftin / producator", ["detergent ieftin si bun","detergent de la producator","detergent rufe ieftin","cel mai bun detergent lichid","detergent lichid bun"]),
 ("Detergent cantitate (5-10L)", ["detergent lichid 5 litri","detergent 5 litri","detergent rufe 5 litri","detergent bidon","detergent 10 litri"]),
]
NEG=["belasil","dero","detergent profesional","detergent doritta","detergent profesional rufe"]

ops=[]
ops.append({"campaignBudgetOperation":{"create":{"resourceName":rn("campaignBudgets",-1),"name":"Belasil - Non-Brand Search","amountMicros":40_000_000,"deliveryMethod":"STANDARD","explicitlyShared":False}}})
ops.append({"campaignOperation":{"create":{"resourceName":rn("campaigns",-2),"name":"AllSoft - [Search] - Non-Brand - Detergent","status":"PAUSED","advertisingChannelType":"SEARCH","campaignBudget":rn("campaignBudgets",-1),"maximizeConversions":{},"containsEuPoliticalAdvertising":"DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING","networkSettings":{"targetGoogleSearch":True,"targetSearchNetwork":False,"targetContentNetwork":False,"targetPartnerSearchNetwork":False}}}})
ops.append({"campaignCriterionOperation":{"create":{"campaign":rn("campaigns",-2),"location":{"geoTargetConstant":"geoTargetConstants/2642"}}}})  # RO
ops.append({"campaignCriterionOperation":{"create":{"campaign":rn("campaigns",-2),"language":{"languageConstant":"languageConstants/1038"}}}})  # RO
ops.append({"campaignCriterionOperation":{"create":{"campaign":rn("campaigns",-2),"language":{"languageConstant":"languageConstants/1000"}}}})  # EN
for t in NEG:
    ops.append({"campaignCriterionOperation":{"create":{"campaign":rn("campaigns",-2),"negative":True,"keyword":{"text":t,"matchType":"PHRASE"}}}})
agid=-10
for name,kws in ADGROUPS:
    ops.append({"adGroupOperation":{"create":{"resourceName":rn("adGroups",agid),"name":name,"campaign":rn("campaigns",-2),"status":"ENABLED","type":"SEARCH_STANDARD","cpcBidMicros":1_500_000}}})
    for kw in kws:
        ops.append({"adGroupCriterionOperation":{"create":{"adGroup":rn("adGroups",agid),"status":"ENABLED","keyword":{"text":kw,"matchType":"PHRASE"}}}})
    ops.append({"adGroupAdOperation":{"create":{"adGroup":rn("adGroups",agid),"status":"ENABLED","ad":{"finalUrls":[URL],"responsiveSearchAd":{"headlines":[{"text":h} for h in HEADLINES],"descriptions":[{"text":d} for d in DESCR],"path1":"detergent","path2":"gel"}}}}})
    agid-=1

apply = "--apply" in sys.argv
url=f"https://googleads.googleapis.com/{API}/customers/{CID}/googleAds:mutate"
body={"mutateOperations":ops,"validateOnly":(not apply),"partialFailure":False}
r=requests.post(url,headers=H,json=body,timeout=90)
print("HTTP",r.status_code, "| APLICAT" if apply else "| DRY-RUN (validateOnly)")
if r.status_code==200:
    print(r.text[:800]);
else:
    d=r.json()
    for f in d.get("error",{}).get("details",[]):
        for e in f.get("errors",[]):
            ec=e.get("errorCode",{});
            print(" •", list(ec.values())[0] if ec else "?", "|", e.get("message","")[:100])
            tr=e.get("trigger");
            if tr: print("     trigger:", json.dumps(tr,ensure_ascii=False)[:140])
            loc=e.get("location",{}).get("fieldPathElements",[])
            print("     @", " > ".join(f"{x.get('fieldName')}[{x.get('index')}]" if 'index' in x else x.get('fieldName','') for x in loc))
            det=e.get("details",{})
            if det: print("     details:", json.dumps(det,ensure_ascii=False)[:300])
