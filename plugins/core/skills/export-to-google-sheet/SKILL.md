---
name: export-to-google-sheet
description: Write tabular data (rows + header) to a Google Sheet via the Google Sheets API v4, authenticating with OAuth Desktop credentials at ~/.config/gcp (the token represents your Google account, NOT a DB secret or service account). Use whenever the user wants to export, push, sync, or publish a table/list/query-result into an existing Google Spreadsheet, or read one back. Clears the tab, writes values, then formats a bold/frozen header and autosizes columns.
---

# export-to-google-sheet

> Author: **Arona core**. OAuth Desktop creds at `~/.config/gcp`, not a DB secret.

This skill writes a 2-D table into a Google Sheet you can already open as
yourself. Auth is your own Google account via an **OAuth Desktop** client —
this is intentionally *not* a service account and *not* a value from the
SharedClaude `secrets` store. Leave that auth as-is.

## Auth (OAuth Desktop)

| File | Purpose |
|---|---|
| `~/.config/gcp/oauth-client.json` | the Desktop OAuth **client** secrets (downloaded from GCP) |
| `~/.config/gcp/sheets-token.json` | the cached **user token** (created on first run) |

Scope: `https://www.googleapis.com/auth/spreadsheets`.

- **First run on a machine opens a browser** for consent
  (`InstalledAppFlow.run_local_server`), then caches the token to
  `sheets-token.json` (chmod 600). Subsequent runs refresh silently.
- The token *is you* — to grant access to a new spreadsheet, just share it
  with your own Google account. No service-account email to invite.
- For new spreadsheets you don't own: share with your Google account first.

## Working template

Adapt **`${CLAUDE_PLUGIN_ROOT}/scripts/write_databases_to_sheet.py`** — it is the
canonical, working example. The proven pattern is:

1. **Auth** — load `sheets-token.json` (refresh if expired, else run the browser
   consent flow and cache the token).
2. **Resolve the target tab** — `spreadsheets().get(..., fields="sheets.properties")`
   to read the first tab's `title` and `sheetId` (the numeric `sheetId` is what
   formatting requests need).
3. **Clear** the tab — `values().clear(range="'<tab>'")`.
4. **Write** the data — `values().update(range="'<tab>'!A1",
   valueInputOption="USER_ENTERED", body={"values": [HEADER, *rows]})`.
   `USER_ENTERED` makes any `=FORMULA` strings actually compute.
5. **Format** in one `batchUpdate({"requests": [...]})`:
   - `repeatCell` over row 0 → **bold** header + light-grey background,
   - `updateSheetProperties` → **freeze** the header row (`frozenRowCount: 1`),
   - `autoResizeDimensions` over the columns → **autosize** to content.

### Skeleton

```python
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
svc = build("sheets", "v4", credentials=creds).spreadsheets()

svc.values().clear(spreadsheetId=SID, range=f"'{tab}'").execute()
svc.values().update(
    spreadsheetId=SID, range=f"'{tab}'!A1",
    valueInputOption="USER_ENTERED",
    body={"values": [HEADER] + rows},
).execute()
svc.batchUpdate(spreadsheetId=SID, body={"requests": [
    {"repeatCell": {  # bold + grey header
        "range": {"sheetId": gid, "startRowIndex": 0, "endRowIndex": 1},
        "cell": {"userEnteredFormat": {"textFormat": {"bold": True},
                 "backgroundColor": {"red": .93, "green": .93, "blue": .93}}},
        "fields": "userEnteredFormat(textFormat,backgroundColor)"}},
    {"updateSheetProperties": {  # freeze header
        "properties": {"sheetId": gid, "gridProperties": {"frozenRowCount": 1}},
        "fields": "gridProperties.frozenRowCount"}},
    {"autoResizeDimensions": {  # autosize columns
        "dimensions": {"sheetId": gid, "dimension": "COLUMNS",
                       "startIndex": 0, "endIndex": len(HEADER)}}},
]}).execute()
```

## Reading a sheet back

**`${CLAUDE_PLUGIN_ROOT}/scripts/read_databases_sheet.py`** dumps every tab as
TSV: `values().get(range="'<tab>'")` per tab from `sheets.properties`.

## Running

```bash
# uv installs the Google client libs from each script's PEP 723 deps block
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/write_databases_to_sheet.py"
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/read_databases_sheet.py"
```

## Adapting for a new export

- Replace `SPREADSHEET_ID` with the target sheet's id and swap `HEADER` / `ROWS`
  for your data (or pass them in / pull from `query-postgres`).
- Keep the clear → update → batchUpdate order; it makes re-runs idempotent.
- `valueInputOption="USER_ENTERED"` for human-style values & formulas;
  use `"RAW"` only if you need strings stored verbatim.

## Notes / gotchas

- Always single-quote tab names in ranges (`'My Tab'!A1`) so spaces work.
- Formatting requests use the numeric **`sheetId`** (the gid), not the tab title.
- This skill needs no entry from the `secrets` table — it's pure OAuth Desktop.
  If you ever want to *source the data* from a company DB, fetch the connection
  string via `kb.py secret-get` (see the `fetch-secret` skill) and import
  `kb_env.load_secrets_into_env`, but the Sheets auth itself stays OAuth.
