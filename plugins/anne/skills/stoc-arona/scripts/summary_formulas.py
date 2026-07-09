# /// script
# requires-python=">=3.9"
# dependencies=["google-api-python-client","google-auth","google-auth-oauthlib"]
# ///
# Afla limitele REALE ale blocurilor (col A = Magazin, rand gol = separator) si scrie tabelul sumar
# G1:J{n}: G=eticheta, H==SUM(F range), I==H*0.23 (≈USD), J==H/$H$1 (procent). NU copia range-uri vechi.
# Uz: uv run summary_formulas.py "1 iulie"
import os,sys
sys.stdout.reconfigure(encoding="utf-8",errors="replace")
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
SID="1Pke-2fMv8MnHyt9hFAwPNRtZHmZIWLMPSsqr3JzYaE0"
TAB=sys.argv[1] if len(sys.argv)>1 else "1 iulie"
S=["https://www.googleapis.com/auth/spreadsheets"]
c=Credentials.from_authorized_user_file(os.path.expanduser("~/.config/gcp/sheets-token.json"),S)
if c.expired and c.refresh_token: c.refresh(Request())
svc=build("sheets","v4",credentials=c).spreadsheets()
A=svc.values().get(spreadsheetId=SID,range=f"'{TAB}'!A1:A2000").execute().get("values",[])
# blocuri contigue per Magazin; rand gol = separator; blocuri cu acelasi nume (ex Parfumuri) le unim la sfarsit
blocks=[]; cur=None; start=None
for i,row in enumerate(A):
    v=str(row[0]).strip() if row else ""
    r=i+1
    if v and v!=cur:
        if cur is not None: blocks.append([cur,start,r-1])
        cur=v; start=r
    elif not v:
        if cur is not None: blocks.append([cur,start,r-1]); cur=None; start=None
if cur is not None: blocks.append([cur,start,len(A)])
blocks=[b for b in blocks if b[0].lower()!="magazin"]
# uneste blocuri consecutive cu acelasi nume intr-un range continuu (start primul .. end ultimul)
merged=[]
for b in blocks:
    if merged and merged[-1][0]==b[0]: merged[-1][2]=b[2]
    else: merged.append(b)
rows=[["Total","=SUM(F:F)","=H1*0.23","=H1/$H$1"]]
for i,(name,s,e) in enumerate(merged):
    r=i+2
    rows.append([name,f"=SUM(F{s}:F{e})",f"=H{r}*0.23",f"=H{r}/$H$1"])
n=len(rows)
svc.values().update(spreadsheetId=SID,range=f"'{TAB}'!G1:J{n}",valueInputOption="USER_ENTERED",
    body={"values":rows}).execute()
svc.values().clear(spreadsheetId=SID,range=f"'{TAB}'!G{n+1}:J50").execute()
print(f"Scris sumar G1:J{n}. Blocuri:")
for name,s,e in merged: print(f"  {name:20} F{s}:F{e}")
out=svc.values().get(spreadsheetId=SID,range=f"'{TAB}'!G1:J{n}").execute().get("values",[])
print("Valori calculate:")
for v in out: print("  ",v)
