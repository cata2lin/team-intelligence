# /// script
# requires-python=">=3.9"
# dependencies=["google-api-python-client","google-auth","google-auth-oauthlib"]
# ///
import os,sys,json,re,unicodedata
sys.stdout.reconfigure(encoding="utf-8",errors="replace")
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
D=os.path.dirname(os.path.abspath(__file__))
C=sys.argv[1]; FX=float(sys.argv[2]); DATE=sys.argv[3]
HROW=int(sys.argv[4]); SI=int(sys.argv[5]); TI=int(sys.argv[6]); CI=int(sys.argv[7])
def norm(s):
    s=unicodedata.normalize("NFKD",str(s)).encode("ascii","ignore").decode().lower()
    return re.sub(r"\s+"," ",s).strip()
inv=json.load(open(os.path.join(D,f"inv_desc_{C}.json"),encoding="utf-8"))
for it in inv: it.append(norm(it[2]))  # [qty,usd,desc,ndesc]
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
    ti=(r[TI] if len(r)>TI else "").strip()
    q=(r[CI] if len(r)>CI else "")
    m=re.search(r"\d[\d,]*",str(q))
    if not (s or ti): continue
    rows.append({"sku":s,"title":ti,"qty":int(m.group().replace(",","")) if m else None})
from collections import defaultdict
qp=defaultdict(set)
for it in inv: qp[it[0]].add(it[1])
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
PARF=["esteban","-lab","nubra","parfumuri","pompite","cutii-","cosulete","recipient-inox","sticla-","sticle-","capac-","dop-","pulv-","eticheta","etichete","recipiente-parfum","recipient-parfum"]
ASTP={"140":1.5,"160":1.8,"180":2.0}
added=[];conf=[];parf=0;unm=[];ambig=[]
def price_for(row):
    # 1) model-number token in invoice desc
    toks=set(re.findall(r"\d{3,}",row["sku"]))|set(re.findall(r"\d{3,}",row["title"]))
    if toks:
        hit=[it for it in inv if any(t in it[3] for t in toks)]
        pr=set(it[1] for it in hit)
        if len(pr)==1: return next(iter(pr)),"model"
    # 2) title-word match, optionally narrowed by qty
    STOP={"pentru","copii","model","din","plastic","stil","industrial","piese"}
    def words(s):
        return {w for w in re.findall(r"[a-z]{4,}",norm(s)) if w not in STOP}
    tw=words(row["sku"].replace("-"," "))|words(row["title"])
    if tw:
        hit=[it for it in inv if tw & words(it[2])]
        pr=set(it[1] for it in hit)
        if len(pr)==1: return next(iter(pr)),"word"
        if len(pr)>1 and row["qty"] is not None:
            pr2=set(it[1] for it in hit if it[0]==row["qty"])
            if len(pr2)==1: return next(iter(pr2)),"word+qty"
    # 3) qty-unique
    if row["qty"] is not None:
        pr=qp.get(row["qty"],set())
        if len(pr)==1: return next(iter(pr)),"qty"
        if len(pr)>1: return ("AMBIG",sorted(pr))
    return None,None
for row in rows:
    sku=row["sku"]
    if any(x in sku.lower() for x in PARF) or any(x in row["title"].lower() for x in ["parfum","esteban","nubra"]): parf+=1; continue
    usd,how=price_for(row)
    if usd=="AMBIG": 
        s=resolve(sku); ambig.append((s or sku,row["qty"],how)); continue
    if usd is None:
        if sku: unm.append((sku,row["qty"])); continue
        continue
    s=resolve(sku)
    if not s:
        if sku: unm.append((sku,row["qty"]))
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
print(f"{C}: {len(rows)} rec | NOI {len(added)} | medie {len(conf)} | parfum {parf} | ambig {len(ambig)} {ambig[:8]} | nepot {unm[:8]}")
for s,u in added: print(f"   +{s:26} {u}$")
