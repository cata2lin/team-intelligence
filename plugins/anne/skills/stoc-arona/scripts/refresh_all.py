# /// script
# requires-python=">=3.9"
# dependencies=["requests","google-api-python-client","google-auth","google-auth-oauthlib"]
# ///
import os,sys,json,time,re
sys.stdout.reconfigure(encoding="utf-8",errors="replace")
sys.path.insert(0,os.path.expanduser("~/team-intelligence-main/plugins/gigi/skills/shopify-stores/scripts"))
os.environ.setdefault("KB_PY",os.path.expanduser("~/team-intelligence-main/plugins/core/scripts/kb.py"))
from shopify_gql import resolve_store
import requests
from collections import defaultdict,Counter
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
API="2026-01"; D=os.path.dirname(os.path.abspath(__file__))
SID="1Pke-2fMv8MnHyt9hFAwPNRtZHmZIWLMPSsqr3JzYaE0"
def gql(shop,token,q,v=None):
    for a in range(6):
        r=requests.post(f"https://{shop}/admin/api/{API}/graphql.json",
          headers={"X-Shopify-Access-Token":token,"Content-Type":"application/json"},
          json={"query":q,"variables":v or {}},timeout=60); d=r.json()
        if "data" not in d: return d
        if d.get("errors") and any("throttl" in str(e).lower() for e in d["errors"]): time.sleep(2+a); continue
        return d
    return d
QV=('query($c:String){ productVariants(first:100, after:$c){ pageInfo{hasNextPage endCursor} '
   'edges{ node{ sku inventoryQuantity product{ status productType title } } } } }')
def pull(pfx):
    shop,token=resolve_store(pfx); m={}; meta={}; cur=None
    while True:
        d=gql(shop,token,QV,{"c":cur})
        if "data" not in d: raise SystemExit(f"{pfx} pull err {json.dumps(d)[:150]}")
        pv=d["data"]["productVariants"]
        for e in pv["edges"]:
            v=e["node"]
            if v["product"]["status"]!="ACTIVE": continue
            q=v["inventoryQuantity"] or 0
            if q<=0: continue
            s=(v["sku"] or "").strip()
            if s:
                m.setdefault(s,q); meta.setdefault(s,(v["product"]["title"],v["product"].get("productType") or ""))
        if pv["pageInfo"]["hasNextPage"]: cur=pv["pageInfo"]["endCursor"]
        else: break
    return m,meta
print("Pulling stores...",flush=True)
ROSSI,ROSSIm=pull("ROSSI"); print(" ROSSI",len(ROSSI),flush=True)
NOC,_=pull("NOC"); print(" NOC",len(NOC),flush=True)
GEN,_=pull("GEN"); print(" GEN",len(GEN),flush=True)
CARP,CARPm=pull("CARP"); print(" CARP",len(CARP),flush=True)
COV,_=pull("COV"); print(" COV",len(COV),flush=True)
BON,BONm=pull("BON"); print(" BON",len(BON),flush=True)
MAG,_=pull("MAG"); print(" MAG",len(MAG),flush=True)
OFER,_=pull("OFER"); print(" OFER",len(OFER),flush=True)
RED,_=pull("RED"); print(" RED",len(RED),flush=True)
GRAN,GRANm=pull("GRAN"); print(" GRAN",len(GRAN),flush=True)

# MAG test-tagged HA set
def test_set():
    shop,token=resolve_store("MAG"); s=set(); cur=None
    Q=('query($c:String){ products(first:60, after:$c, query:"tag:test"){ pageInfo{hasNextPage endCursor} '
       'edges{ node{ variants(first:40){ edges{ node{ sku } } } } } } }')
    while True:
        d=gql(shop,token,Q,{"c":cur})
        if "data" not in d: break
        pr=d["data"]["products"]
        for e in pr["edges"]:
            for ve in e["node"]["variants"]["edges"]:
                sk=(ve["node"]["sku"] or "").strip()
                if sk.upper().startswith("HA"): s.add(sk)
        if pr["pageInfo"]["hasNextPage"]: cur=pr["pageInfo"]["endCursor"]
        else: break
    return s
TEST=test_set(); print(" MAG test HA:",len(TEST),flush=True)

# sheets for June reference
SCOPES=["https://www.googleapis.com/auth/spreadsheets"]
creds=Credentials.from_authorized_user_file(os.path.expanduser("~/.config/gcp/sheets-token.json"),SCOPES)
if creds.expired and creds.refresh_token: creds.refresh(Request())
gs=build("sheets","v4",credentials=creds).spreadsheets()
jv=gs.values().get(spreadsheetId=SID,range="'1 iunie'").execute().get("values",[])
def june_group(g,ha=None):
    out=[]
    for r in jv:
        if r and r[0]==g and len(r)>2:
            s=r[2].strip()
            if ha is True and not s.upper().startswith("HA"): continue
            if ha is False and s.upper().startswith("HA"): continue
            if s not in out: out.append(s)
    return out

def dump(name, rows):
    json.dump(rows, open(os.path.join(D,f"store_{name}.json"),"w",encoding="utf-8"), ensure_ascii=False)

# ROSSI
EXR={"kitpolygel-3","R196-base+top","kit-culoare","kit-3culori","kit-6culori"}
def subR(pt):
    pt=pt.lower()
    if "pudr" in pt or "polygel" in pt: return "Pudra"
    if "esen" in pt: return "Esentiale"
    if "acces" in pt: return "Accesorii unghii"
    return pt
rossi={s:q for s,q in ROSSI.items() if s not in EXR}
ordR={"Pudra":0,"Esentiale":1,"Accesorii unghii":2}
rossi_rows=sorted([["ROSSI",subR(ROSSIm[s][1]),s,q] for s,q in rossi.items()], key=lambda x:(ordR.get(x[1],9),x[2]))
dump("ROSSI",rossi_rows)

# Nocturna
noc={s:q for s,q in NOC.items() if s!="surpriza"}
szo={"XS":0,"S":1,"M":2,"L":3,"XL":4,"2XL":5,"3XL":6,"4XL":7,"5XL":8}
def nk(s): c=s.rsplit("-",1)[0]; z=s.rsplit("-",1)[-1]; return (c,szo.get(z,99))
noc_rows=[["Nocturna","Pijamale",s,noc[s]] for s in sorted(noc,key=nk)]
dump("Nocturna",noc_rows)

# Gento
gento={s:q for s,q in GEN.items() if s!="surpriza"}
gento_rows=[["Gento","Genti",s,q] for s,q in sorted(gento.items())]
dump("Gento",gento_rows)

# Covoria = CARP rugs + baie-verde from COV
def subC(s): return "Covorase" if s.lower().startswith("baie") else "Covoare"
cov=dict(CARP)
if "baie-verde" in COV: cov["baie-verde"]=COV["baie-verde"]
ordC={"Covoare":0,"Covorase":1}
cov_rows=sorted([["Covoria",subC(s),s,q] for s,q in cov.items()], key=lambda x:(ordC[x[1]],x[2]))
cov_sku_set={r[2] for r in cov_rows}
dump("Covoria",cov_rows)

# Casa Ofertelor = June list ∩ BON active (non-HA)
casa_june=june_group("Casa Ofertelor")
casa_rows=[["Casa Ofertelor","Mixtit",s,BON[s]] for s in casa_june if s in BON and not s.upper().startswith("HA")]
dump("CasaOfertelor",casa_rows)

# Facebook = union HA(4 stores, positive) - TEST  +  June non-HA current
ha_union={}
for store in (BON,MAG,OFER,RED):
    for s,q in store.items():
        if s.upper().startswith("HA"): ha_union.setdefault(s,q)
# prefer MAG qty then BON then OFER then RED for consistency
def ha_qty(s):
    for st in (MAG,BON,OFER,RED):
        if s in st: return st[s]
fb_ha={s:ha_qty(s) for s in ha_union if s not in TEST}
fb_non_june=june_group("Facebook",ha=False)
def nonha_qty(s):
    for st in (BON,MAG,OFER,RED):
        if s in st: return st[s]
    return None
fb_rows=[]
for s in sorted(fb_ha, key=lambda x:(int(re.findall(r'\d+',x)[0]) if re.findall(r'\d+',x) else 0, x)):
    fb_rows.append(["Facebook","",s,fb_ha[s]])
for s in fb_non_june:
    q=nonha_qty(s)
    if q is not None: fb_rows.append(["Facebook","",s,q])
dump("Facebook",fb_rows)

# Grandia = GRAN active - Covoria rug skus - R203-naildrill (ROSSI)
GEXCL=set(cov_sku_set)|{"R203-naildrill"}
grandia_rows=[["Grandia","",s,q] for s,q in sorted(GRAN.items()) if s not in GEXCL]
dump("Grandia",grandia_rows)

print("\n=== COUNTS (refresh azi) ===")
for name,rows in [("ROSSI",rossi_rows),("Nocturna",noc_rows),("Gento",gento_rows),("Covoria",cov_rows),("CasaOfertelor",casa_rows),("Facebook",fb_rows),("Grandia",grandia_rows)]:
    print(f"  {name:16} {len(rows):>4} SKU / {sum(r[3] for r in rows):>8,} buc")
print(f"  Grandia excluded naildrill: {'R203-naildrill' in GRAN}")
