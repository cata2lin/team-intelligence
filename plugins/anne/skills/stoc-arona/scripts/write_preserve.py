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
SID="1Pke-2fMv8MnHyt9hFAwPNRtZHmZIWLMPSsqr3JzYaE0"; TAB="1 iulie"
SCOPES=["https://www.googleapis.com/auth/spreadsheets"]
creds=Credentials.from_authorized_user_file(os.path.expanduser("~/.config/gcp/sheets-token.json"),SCOPES)
if creds.expired and creds.refresh_token: creds.refresh(Request())
svc=build("sheets","v4",credentials=creds).spreadsheets()
meta=svc.get(spreadsheetId=SID,fields="sheets.properties").execute()
gid=next(s["properties"]["sheetId"] for s in meta["sheets"] if s["properties"]["title"]==TAB)
# capture existing Parfumuri block verbatim
cur=svc.values().get(spreadsheetId=SID,range=f"'{TAB}'").execute().get("values",[])
pidx=next((i for i,r in enumerate(cur) if r and str(r[0]).strip()=="Parfumuri"), None)
parf=[]
if pidx is not None:
    parf=cur[pidx:]  # everything from first Parfumuri row to end (verbatim)
    # trim trailing fully-empty rows
    while parf and not any(str(c).strip() for c in parf[-1]): parf.pop()
print(f"Parfumuri pastrat: {len(parf)} randuri")
# assemble stores
HEADER=["Magazin","Categorie","SKU","Cantitate"]
values=[HEADER]; first=True
for fn in ["store_ROSSI.json","store_Nocturna.json","store_Gento.json","store_Covoria.json","store_CasaOfertelor.json","store_Facebook.json","store_Grandia.json"]:
    p=os.path.join(D,fn)
    if os.path.exists(p):
        if not first: values.append([])
        values+=json.load(open(p,encoding="utf-8")); first=False
if parf:
    values.append([]); values+=parf
svc.values().clear(spreadsheetId=SID,range=f"'{TAB}'").execute()
svc.values().update(spreadsheetId=SID,range=f"'{TAB}'!A1",valueInputOption="USER_ENTERED",body={"values":values}).execute()
svc.batchUpdate(spreadsheetId=SID,body={"requests":[
 {"repeatCell":{"range":{"sheetId":gid,"startRowIndex":0,"endRowIndex":1},
   "cell":{"userEnteredFormat":{"textFormat":{"bold":True},"backgroundColor":{"red":.93,"green":.93,"blue":.93}}},
   "fields":"userEnteredFormat(textFormat,backgroundColor)"}},
 {"updateSheetProperties":{"properties":{"sheetId":gid,"gridProperties":{"frozenRowCount":1}},"fields":"gridProperties.frozenRowCount"}},
 {"autoResizeDimensions":{"dimensions":{"sheetId":gid,"dimension":"COLUMNS","startIndex":0,"endIndex":5}}},
]}).execute()
print(f"Scris {len(values)} randuri total (incl header + Parfumuri)")
