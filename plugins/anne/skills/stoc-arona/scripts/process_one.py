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
inv=json.load(open(os.path.join(D,f"inv_{C}.json"),encoding="utf-8"))
SID="1PjlFq31Es39jW6wZqpE5yuAnW0gO72M_7ElLPz7OitU"
SCOPES=["https://www.googleapis.com/auth/spreadsheets"]
creds=Credentials.from_authorized_user_file(os.path.expanduser("~/.config/gcp/sheets-token.json"),SCOPES)
if creds.expired and creds.refresh_token: creds.refresh(Request())
svc=build("sheets","v4",credentials=creds).spreadsheets()
meta=svc.get(spreadsheetId=SID,fields="sheets.properties").execute()
title=next(t["properties"]["title"] for t in meta["sheets"] if re.match(rf'\s*{C}\b',t["properties"]["title"].strip()))
vals=svc.values().get(spreadsheetId=SID,range=f"'{title}'").execute().get("values",[])
hi=next(i for i,r in enumerate(vals) if r and "SKU" in [str(x).strip() for x in r])
hdr=[str(x).strip() for x in vals[hi]]; si=hdr.index("SKU"); ci=hdr.index("Cantitate"); cati=hdr.index("Categorie")
rows=[]
for r in vals[hi+1:]:
    if len(r)>max(si,ci) and str(r[0]).strip().isdigit():
        rows.append({"sku":(r[si] if len(r)>si else "").strip(),"cat":(r[cati] if len(r)>cati else "").strip(),"qty":int(float(str(r[ci]))) if len(r)>ci and str(r[ci]).strip() else None})
stock={}
for fn in os.listdir(D):
    if fn.startswith("store_") and fn.endswith(".json"):
        for row in json.load(open(os.path.join(D,fn),encoding="utf-8")):
            if len(row)>=4 and row[2]: stock[str(row[2]).strip()]={"store":row[0],"qty":row[3]}
prices=json.load(open(os.path.join(D,"prices.json"),encoding="utf-8"))
def resolve(sku):
    if sku in stock: return sku
    n=sku.replace(" ","-")
    if n in stock: return n
    if sku=="oglinda": return "oglinda-acrilica" if "oglinda-acrilica" in stock else None
    if sku.isdigit():
        c=[s for s in stock if s.endswith(sku)]; return c[0] if len(c)==1 else None
    return None
ASTP={"140":1.5,"160":1.8,"180":2.0}
used=[False]*len(inv); i=0; added=[]; conf=[]; parf=0; unm=[]
for row in rows:
    q=row["qty"]; sku=row["sku"]; cat=(row["cat"] or "").lower()
    j=i
    while j<len(inv) and inv[j][0]!=q: j+=1
    if j>=len(inv): j=next((k for k in range(len(inv)) if not used[k] and inv[k][0]==q),None)
    if j is None: unm.append((sku,q)); continue
    usd=inv[j][1]; used[j]=True; i=j+1
    if cat.startswith("parfum") or any(x in sku.lower() for x in ["esteban","-lab","nubra","parfumuri","pompite","cutii-","cosulete","recipient-inox","sticla-","sticle-","capac-","dop-","pulv-","eticheta","etichete"]):
        parf+=1; continue
    s=resolve(sku)
    if not s: unm.append((sku,q)); continue
    m=re.search(r'asternut-\w+-(140|160|180)$',s)
    if m: usd=ASTP[m.group(1)]
    cogs_new=round(usd*FX,4)
    if s in prices:
        old=prices[s].get("cogs") or round(prices[s]["usd"]*prices[s].get("fx",4.358),4)
        if abs(old-cogs_new)>0.01:
            avg=round((old+cogs_new)/2,2); conf.append((s,round(old,2),cogs_new,avg))
            prices[s]={"usd":usd,"fx":FX,"cogs":avg,"container":f"{prices[s].get('container')}+{C}","date":DATE}
    else:
        prices[s]={"usd":usd,"fx":FX,"container":C,"date":DATE}; added.append((s,usd,stock[s]["store"]))
json.dump(prices,open(os.path.join(D,"prices.json"),"w",encoding="utf-8"),ensure_ascii=False)
print(f"{C} ({title!r}): {len(rows)} rec / {len(inv)} inv | NOI {len(added)} | medie {len(conf)} {conf} | parfum {parf} | nepot {unm}")
for s,u,st in added: print(f"   {s:24} {u}$ [{st}]")
