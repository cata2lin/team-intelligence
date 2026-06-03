---
name: excel-api-push
description: Read an Excel (.xlsx) file, push each not-yet-sent row to an external program's API as JSON, then mark those rows as "sent" in the spreadsheet. Use when the user wants to export/sync spreadsheet rows into another system and keep track of what has already been sent. Idempotent — safe to re-run.
---

# excel-api-push

> Author: **Catalin**. Shared with the whole team via the `catalin` plugin.

Reads any `.xlsx`, sends every row that isn't already marked sent to a target
API, and writes a timestamp back into a status column so re-runs skip what was
already delivered.

## Run it

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/excel_api_push.py" \
  "$NAS_ROOT/data/orders.xlsx" "https://api.example.com/import" \
  --status-col "sent_at"
```

Optional flags: `--sheet <name>` (default: first sheet), `--status-col <header>`
(default: `sent_at`), `--timeout <seconds>`, `--dry-run` (print what would be
sent, change nothing). Spreadsheets live on the NAS — pass paths under
`$NAS_ROOT/data/...`.

## After running — log it to the knowledge base
```bash
KB=${CLAUDE_PLUGIN_ROOT}/../../core/scripts/kb.py   # or the core plugin's kb.py
uv run "$KB" log --type skill --action used --name catalin:excel-api-push --summary "pushed N rows from <file>"
```
(If you created/modified the file on the NAS, also `kb.py file-add ...`.)

## How it works
1. Opens the workbook with openpyxl, reads the header row, ensures the status
   column exists.
2. For each row whose status cell is empty → `POST` the row as a JSON object.
   Date/datetime cells are sent as ISO strings; formula cells send their cached
   value (read via a `data_only` handle).
3. On a 2xx response → stamps an ISO timestamp into the status cell. Already-
   stamped rows are skipped, so it's idempotent and resumable.
4. Prints `sent=… skipped=… failed=…` to stderr.
