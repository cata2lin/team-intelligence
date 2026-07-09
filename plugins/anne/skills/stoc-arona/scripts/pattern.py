# /// script
# requires-python=">=3.9"
# dependencies=["google-api-python-client","google-auth","google-auth-oauthlib"]
# ///
# Masoara tiparul Cogs_factura / Cogs_iunie per magazin, ca sa afli factorul de estimare
# pt SKU-urile care NU apar in nicio factura. Ruleaza din folderul de lucru (are prices.json + store_*.json).
import os,sys,json,statistics
sys.stdout.reconfigure(encoding="utf-8",errors="replace")
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
D=os.path.dirname(os.path.abspath(__file__)) if "--here" not in sys.argv else os.getcwd()
D=os.getcwd()
SID="1Pke-2fMv8MnHyt9hFAwPNRtZHmZIWLMPSsqr3JzYaE0"
MONTH=sys.argv[1] if len(sys.argv)>1 else "1 iunie"
S=["https://www.googleapis.com/auth/spreadsheets"]
c=Credentials.from_authorized_user_file(os.path.expanduser("~/.config/gcp/sheets-token.json"),S)
if c.expired and c.refresh_token: c.refresh(Request())
svc=build("sheets","v4",credentials=c).spreadsheets()
vals=svc.values().get(spreadsheetId=SID,range=f"'{MONTH}'").execute().get("values",[])
jun={}
for r in vals:
    if len(r)>4 and str(r[2]).strip():
        try: jun[str(r[2]).strip()]=float(str(r[4]).replace(",",".").strip())
        except: pass
prices=json.load(open(os.path.join(D,"prices.json"),encoding="utf-8"))
sku_store={}
for fn in os.listdir(D):
    if fn.startswith("store_") and fn.endswith(".json"):
        st=fn[6:-5]
        for r in json.load(open(os.path.join(D,fn),encoding="utf-8")):
            if len(r)>=4 and r[2]: sku_store[str(r[2]).strip()]=st
from collections import defaultdict
byst=defaultdict(list)
for sku,p in prices.items():
    cont=str(p.get("container",""))
    if any(t in cont for t in ["iunie","EST","MANUAL","GENTO","mai","apr"]): continue  # doar factura
    cg=p.get("cogs") if p.get("cogs") is not None else round((p.get("usd") or 0)*p.get("fx",4.334),2)
    if not cg or sku not in jun or jun[sku]<=0: continue
    byst[sku_store.get(sku,"?")].append(cg/jun[sku])
print(f"Factor de estimare = mediana(Cogs_factura / Cogs_{MONTH}) per magazin:")
for st,rs in sorted(byst.items()):
    if len(rs)<3: continue
    print(f"  {st:16} n={len(rs):4} media={statistics.mean(rs):.3f} MEDIANA={statistics.median(rs):.3f}")
miss=defaultdict(int); missjun=defaultdict(int)
for sku,st in sku_store.items():
    if sku not in prices:
        miss[st]+=1
        if sku in jun: missjun[st]+=1
print(f"\nLipsa (au {MONTH} / total lipsa) — candidati la estimare iunie×factor:")
for st in miss: print(f"  {st:16} {missjun[st]}/{miss[st]}")
