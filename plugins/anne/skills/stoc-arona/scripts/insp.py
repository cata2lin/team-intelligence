# /// script
# requires-python=">=3.9"
# dependencies=["google-api-python-client","google-auth","google-auth-oauthlib"]
# ///
import os,sys,re,json
sys.stdout.reconfigure(encoding="utf-8",errors="replace")
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
SID="1PjlFq31Es39jW6wZqpE5yuAnW0gO72M_7ElLPz7OitU"
S=["https://www.googleapis.com/auth/spreadsheets"]
c=Credentials.from_authorized_user_file(os.path.expanduser("~/.config/gcp/sheets-token.json"),S)
if c.expired and c.refresh_token: c.refresh(Request())
svc=build("sheets","v4",credentials=c).spreadsheets()
meta=svc.get(spreadsheetId=SID,fields="sheets.properties").execute()
titles=[t["properties"]["title"] for t in meta["sheets"]]
print("ALL:",titles)
for C in sys.argv[1:]:
    t=next((x for x in titles if re.match(rf'\s*{C}\b',x.strip())),None)
    print(f"\n=== {C} -> {t!r} ===")
    if not t: print("   TAB LIPSA"); continue
    vals=svc.values().get(spreadsheetId=SID,range=f"'{t}'").execute().get("values",[])
    for i,r in enumerate(vals[:5]): print(i,r)
