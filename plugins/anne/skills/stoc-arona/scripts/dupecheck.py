# /// script
# requires-python=">=3.9"
# dependencies=["google-api-python-client","google-auth","google-auth-oauthlib"]
# ///
import os,sys
sys.stdout.reconfigure(encoding="utf-8",errors="replace")
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from collections import Counter,defaultdict
SCOPES=["https://www.googleapis.com/auth/spreadsheets"]
creds=Credentials.from_authorized_user_file(os.path.expanduser("~/.config/gcp/sheets-token.json"),SCOPES)
if creds.expired and creds.refresh_token: creds.refresh(Request())
svc=build("sheets","v4",credentials=creds).spreadsheets()
vals=svc.values().get(spreadsheetId="1Pke-2fMv8MnHyt9hFAwPNRtZHmZIWLMPSsqr3JzYaE0",range="'1 iulie'").execute().get("values",[])
data=[r for r in vals[1:] if r and len(r)>=4 and str(r[0]).strip()]  # skip header + blank rows
percount=Counter(); perstore=defaultdict(list); tot=defaultdict(int)
skuglobal=Counter(); skuwhere=defaultdict(list)
for r in data:
    mag,cat,sku,qty=r[0],r[1],r[2],r[3]
    perstore[mag].append(sku); tot[mag]+=int(float(str(qty)))
    skuglobal[(mag,sku)]+=1
    skuwhere[sku].append(mag)
print("Per magazin: SKU / buc")
for mag,skus in perstore.items():
    print(f"  {mag:10} {len(skus)} SKU / {tot[mag]:>7,} buc | dubluri in magazin: {[s for s,c in Counter(skus).items() if c>1]}")
# dup within same store
dupin=[(m,s,c) for (m,s),c in skuglobal.items() if c>1]
print("\nSKU duplicat in ACELASI magazin:", dupin or "NICIUNUL")
# same SKU across DIFFERENT stores (potential cross double-count)
cross={s:set(w) for s,w in skuwhere.items() if len(set(w))>1}
print("SKU care apare in MAI MULTE magazine:", cross or "NICIUNUL")
print(f"\nTOTAL r? nduri de date: {len(data)}  |  total buc: {sum(tot.values()):,}")
