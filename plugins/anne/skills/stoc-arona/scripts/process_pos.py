# /// script
# requires-python=">=3.9"
# dependencies=["google-api-python-client","google-auth","google-auth-oauthlib"]
# ///
import os,sys,json,re
sys.stdout.reconfigure(encoding="utf-8",errors="replace")
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
D=os.path.dirname(os.path.abspath(__file__))
C=sys.argv[1]; FX=float(sys.argv[2]); DATE=sys.argv[3]
HROW=int(sys.argv[4]); SI=int(sys.argv[5]); CI=int(sys.argv[6])
inv=json.load(open(os.path.join(D,f"inv_{C}.json"),encoding="utf-8"))
SID="1PjlFq31Es39jW6wZqpE5yuAnW0gO72M_7ElLPz7OitU"
S=["https://www.googleapis.com/auth/spreadsheets"]
c=Credentials.from_authorized_user_file(os.path.expanduser("~/.config/gcp/sheets-token.json"),S)
if c.expired and c.refresh_token: c.refresh(Request())
svc=build("sheets","v4",credentials=c).spreadsheets()
meta=svc.get(spreadsheetId=SID,fields="sheets.properties").execute()
title=next(t["properties"]["title"] for t in meta["sheets"] if re.match(rf'\s*{C}\b',t["properties"]["title"].strip()))
vals=svc.values().get(spreadsheetId=SID,range=f"'{title}'").execute().get("values",[])
rows=[]
for r in vals[HROW+1:]:
    s=(r[SI] if len(r)>SI else "").strip()
    q=(r[CI] if len(r)>CI else "")
    m=re.search(r"\d[\d,]*",str(q))
    if not m: continue
    rows.append({"sku":s,"qty":int(m.group().replace(",",""))})
stock={}
for fn in os.listdir(D):
    if fn.startswith("store_") and fn.endswith(".json"):
        for row in json.load(open(os.path.join(D,fn),encoding="utf-8")):
            if len(row)>=4 and row[2]: stock[str(row[2]).strip()]={"store":row[0],"qty":row[3]}
prices=json.load(open(os.path.join(D,"prices.json"),encoding="utf-8"))
def resolve(sku):
    if not sku: return None
    if sku in stock: return sku
    n=sku.replace(" ","-")
    if n in stock: return n
    if sku.isdigit():
        cd=[s for s in stock if s.endswith(sku)]; return cd[0] if len(cd)==1 else None
    return None
PARF=["esteban","-lab","nubra","parfumuri","pompite","cutii-","cosulete","recipient-inox","sticla-","sticle-","capac-","dop-","pulv-","eticheta","etichete","cutie-3-est","cutie-indiv"]
ASTP={"140":1.5,"160":1.8,"180":2.0}
used=[False]*len(inv); i=0; added=[];conf=[];parf=0;unm=[]
for row in rows:
    q=row["qty"]; sku=row["sku"]
    j=i
    while j<len(inv) and inv[j][0]!=q: j+=1
    if j>=len(inv): j=next((k for k in range(len(inv)) if not used[k] and inv[k][0]==q),None)
    if j is None: 
        if sku and not any(x in sku.lower() for x in PARF): unm.append((sku,q))
        continue
    usd=inv[j][1]; used[j]=True; i=j+1
    if any(x in sku.lower() for x in PARF): parf+=1; continue
    s=resolve(sku)
    if not s:
        if sku: unm.append((sku,q))
        continue
    m=re.search(r'asternut-\w+-(140|160|180)$',s)
    if m: usd=ASTP[m.group(1)]
    cn=round(usd*FX,4)
    if s in prices:
        old=prices[s].get("cogs") or round(prices[s]["usd"]*prices[s].get("fx",4.358),4)
        if abs(old-cn)>0.01:
            avg=round((old+cn)/2,2); conf.append((s,round(old,2),cn,avg))
            prices[s]={"usd":usd,"fx":FX,"cogs":avg,"container":f"{prices[s].get('container')}+{C}","date":DATE}
    else:
        prices[s]={"usd":usd,"fx":FX,"container":C,"date":DATE}; added.append((s,usd))
json.dump(prices,open(os.path.join(D,"prices.json"),"w",encoding="utf-8"),ensure_ascii=False)
print(f"{C} ({title!r}): {len(rows)} rec / {len(inv)} inv | NOI {len(added)} | medie {len(conf)} | parfum {parf} | nepot {unm[:8]}")
for s,u in added: print(f"   +{s:26} {u}$")
