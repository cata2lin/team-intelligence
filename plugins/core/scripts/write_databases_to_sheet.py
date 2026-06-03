"""Write the list of databases on 38.242.226.83 into a Google Sheet."""
import os
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
CLIENT_FILE = Path.home() / ".config" / "gcp" / "oauth-client.json"
TOKEN_FILE = Path.home() / ".config" / "gcp" / "sheets-token.json"

SPREADSHEET_ID = "1uhKzZ1jXfNuZ_szvm8jz8uUU-JfZlN7MDu78w2Imh68"

# (database, size_pretty, size_bytes, mapped_app, notes)
ROWS = [
    ("test",                          "79 GB",   84825907200, "arona-bi",          "Primary DB for arona-bi (DATABASE_URL points here)"),
    ("trendyol",                      "77 GB",   82678120000, "(unmapped)",        "Marketplace scraper data"),
    ("InventorySync",                 "3151 MB", 3303604224,  "grandia-inventory", "Likely current grandia DB"),
    ("Profitabilitate-Livrabilitate", "1590 MB", 1667235840,  "(unmapped)",        "RO: Profitability / Deliverability"),
    ("AWBprint",                      "1569 MB", 1645215744,  "tom",               "Read-only catalog source synced into tom_wms"),
    ("metrics",                       "984 MB",  1031798784,  "metrics",           "Multi-brand marketing metrics aggregator"),
    ("Grandia",                       "647 MB",  678428672,   "grandia-inventory", "Older grandia DB (verify which is current)"),
    ("orders1",                       "556 MB",  583008256,   "(unmapped)",        ""),
    ("orders",                        "274 MB",  287309824,   "(unmapped)",        ""),
    ("Gigi",                          "122 MB",  127926272,   "(unmapped)",        ""),
    ("tom_wms",                       "14 MB",   14680064,    "tom",               "TOM primary DB (purchase orders, products, shipments)"),
    ("Parfum_Iulian",                 "14 MB",   14680064,    "scentum",           "Perfume essences & packaging inventory"),
    ("Productie_parfum",              "9524 kB", 9752576,     "(unmapped)",        "RO: Perfume Production"),
    ("parfumuri",                     "9060 kB", 9277440,     "(unmapped)",        "RO: perfumes"),
    ("Trading",                       "8284 kB", 8482816,     "(unmapped)",        ""),
    ("MPI",                           "8180 kB", 8376320,     "(unmapped)",        ""),
    ("postgres",                      "7540 kB", 7720960,     "(system)",          "Postgres maintenance DB — do not touch"),
    ("mattermost",                    "7484 kB", 7663616,     "(unmapped)",        "Mattermost chat backend — do not touch"),
]

HEADER = ["Database", "Size", "Size (bytes)", "Mapped App", "Notes"]


def get_creds():
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_FILE), SCOPES)
            creds = flow.run_local_server(port=0, open_browser=True)
        TOKEN_FILE.write_text(creds.to_json())
        os.chmod(TOKEN_FILE, 0o600)
    return creds


def main():
    creds = get_creds()
    svc = build("sheets", "v4", credentials=creds)
    api = svc.spreadsheets()

    meta = api.get(spreadsheetId=SPREADSHEET_ID, fields="sheets.properties").execute()
    first = meta["sheets"][0]["properties"]
    tab_name = first["title"]
    sheet_id = first["sheetId"]
    print(f"Writing to tab: {tab_name!r} (sheetId={sheet_id})")

    values = [HEADER] + [list(r) for r in ROWS]

    # Clear and write
    api.values().clear(spreadsheetId=SPREADSHEET_ID, range=f"'{tab_name}'").execute()
    api.values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{tab_name}'!A1",
        valueInputOption="USER_ENTERED",
        body={"values": values},
    ).execute()

    # Format: bold header, freeze row 1, auto-resize cols
    requests = [
        {"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
            "cell": {"userEnteredFormat": {
                "textFormat": {"bold": True},
                "backgroundColor": {"red": 0.93, "green": 0.93, "blue": 0.93},
            }},
            "fields": "userEnteredFormat(textFormat,backgroundColor)",
        }},
        {"updateSheetProperties": {
            "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
            "fields": "gridProperties.frozenRowCount",
        }},
        {"autoResizeDimensions": {
            "dimensions": {"sheetId": sheet_id, "dimension": "COLUMNS",
                           "startIndex": 0, "endIndex": len(HEADER)},
        }},
    ]
    api.batchUpdate(spreadsheetId=SPREADSHEET_ID, body={"requests": requests}).execute()

    print(f"OK — wrote {len(ROWS)} rows + header to {tab_name!r}")


if __name__ == "__main__":
    main()
