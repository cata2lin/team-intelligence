# /// script
# requires-python=">=3.9"
# dependencies=["google-api-python-client","google-auth","google-auth-oauthlib"]
# ///
import os,sys,json
sys.stdout.reconfigure(encoding="utf-8",errors="replace")
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
D=os.path.dirname(os.path.abspath(__file__))
SID="1Pke-2fMv8MnHyt9hFAwPNRtZHmZIWLMPSsqr3JzYaE0"; TAB="1 iulie"; FX=4.358
prices=json.load(open(os.path.join(D,"prices.json"),encoding="utf-8"))
SCOPES=["https://www.googleapis.com/auth/spreadsheets"]
creds=Credentials.from_authorized_user_file(os.path.expanduser("~/.config/gcp/sheets-token.json"),SCOPES)
if creds.expired and creds.refresh_token: creds.refresh(Request())
svc=build("sheets","v4",credentials=creds).spreadsheets()
vals=svc.values().get(spreadsheetId=SID,range=f"'{TAB}'").execute().get("values",[])
# find Parfumuri start (don't touch its E/F)
pidx=next((i for i,r in enumerate(vals) if r and str(r[0]).strip()=="Parfumuri"), len(vals))
n=len(vals)
colE=[]; colF=[]; filled=0
for i,r in enumerate(vals):
    curE=r[4] if len(r)>4 else ""
    curF=r[5] if len(r)>5 else ""
    if i>=pidx:  # Parfumuri block -> keep as-is
        colE.append([curE]); colF.append([curF]); continue
    sku=str(r[2]).strip() if len(r)>2 else ""
    store=str(r[0]).strip() if r else ""
    if sku and sku in prices and store and store!="Parfumuri":
        usd=prices[sku]["usd"]; fx=prices[sku].get("fx",FX); qty=int(float(str(r[3]))) if len(r)>3 and str(r[3]).strip() else 0
        cogs=round(prices[sku]["cogs"],2) if prices[sku].get("cogs") is not None else round(usd*fx,2); val=round(qty*cogs,2)
        colE.append([cogs]); colF.append([val]); filled+=1
    else:
        colE.append([curE]); colF.append([curF])
# header labels row 1
colE[0]=["Cogs"]; colF[0]=["Valoare stoc"]
svc.values().update(spreadsheetId=SID,range=f"'{TAB}'!E1:E{n}",valueInputOption="USER_ENTERED",body={"values":colE}).execute()
svc.values().update(spreadsheetId=SID,range=f"'{TAB}'!F1:F{n}",valueInputOption="USER_ENTERED",body={"values":colF}).execute()
tot=sum(x[0] for x in colF if isinstance(x[0],(int,float)))
print(f"Completat Cogs+Valoare pe {filled} SKU | Valoare parțială (magazinele mele): {tot:,.2f} RON")
# delete the separate 'Valoare stoc' tab if exists
meta=svc.get(spreadsheetId=SID,fields="sheets.properties").execute()
vg=next((s["properties"]["sheetId"] for s in meta["sheets"] if s["properties"]["title"]=="Valoare stoc"),None)
if vg is not None:
    svc.batchUpdate(spreadsheetId=SID,body={"requests":[{"deleteSheet":{"sheetId":vg}}]}).execute()
    print("Șters tab-ul separat 'Valoare stoc' (consolidat în 1 iulie).")
