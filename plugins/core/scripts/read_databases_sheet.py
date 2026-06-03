"""Read the databases sheet back."""
from pathlib import Path
import json
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
TOKEN_FILE = Path.home() / ".config" / "gcp" / "sheets-token.json"
SPREADSHEET_ID = "1uhKzZ1jXfNuZ_szvm8jz8uUU-JfZlN7MDu78w2Imh68"

creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
if not creds.valid:
    creds.refresh(Request())

svc = build("sheets", "v4", credentials=creds)
api = svc.spreadsheets()
meta = api.get(spreadsheetId=SPREADSHEET_ID, fields="sheets.properties").execute()
for s in meta["sheets"]:
    tab = s["properties"]["title"]
    res = api.values().get(spreadsheetId=SPREADSHEET_ID, range=f"'{tab}'").execute()
    print(f"=== TAB: {tab} ===")
    for row in res.get("values", []):
        print("\t".join(row))
    print()
