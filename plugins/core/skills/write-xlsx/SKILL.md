---
name: write-xlsx
description: Generate a styled .xlsx (Excel) workbook from a Postgres SELECT using openpyxl — styled header (bold, grey fill), frozen top row, auto-sized columns. Use whenever the user wants to export, download, or email query results from an Arona database as a real Excel file (not a living Google Sheet). Read-only.
---

# write-xlsx

> Author: **Arona core**. Read-only — the query runs in a READ ONLY transaction.

Turns any `SELECT` against an Arona Postgres DB into a downloadable, styled
`.xlsx` file. The header row is bold with a grey fill and frozen; columns are
auto-sized. Built on `openpyxl` (already used in arona-bi).

The bundled script imports `kb_env` and loads secrets from the SharedClaude
`secrets` table, so you pass the **env-key name** of the connection string
(e.g. `DATABASE_URL_METRICS`) rather than the credential itself. Run it with
`uv` — PEP 723 inline deps (`openpyxl`, `psycopg2-binary`) auto-install.

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/write_xlsx.py" \
  --db DATABASE_URL_METRICS \
  --sql "SELECT id, name, created_at FROM brands ORDER BY id" \
  --out "$NAS_ROOT/exports/brands.xlsx"
```

For a long/complex query, keep the SQL in a file instead of inline:

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/write_xlsx.py" \
  --db DATABASE_URL_METRICS \
  --sql-file ./report.sql \
  --out "$NAS_ROOT/exports/report.xlsx" \
  --sheet "Q2 brands"
```

## Options
- `--db KEY` — env var holding the Postgres connection string (e.g.
  `DATABASE_URL_METRICS`, `DATABASE_URL_GRANDIA`). Required. `$KB_DATABASE_URL`
  must be set so `kb_env` can populate these.
- `--sql QUERY` / `--sql-file PATH` — the `SELECT` (one of the two is required).
- `--out PATH` — destination `.xlsx`; write under `$NAS_ROOT/exports/`. Parent
  dirs are created. Default `out.xlsx`.
- `--sheet NAME` — worksheet title (default `data`).

## When to use this vs Google Sheets
- **`.xlsx`** if the user wants a downloadable file to save or email.
- **Google Sheets** if it's a living, shared document.

## Rules
- Read-only: the script opens the connection with `set_session(readonly=True)`.
  Use `--sql` for `SELECT` only — do not attempt writes through this skill.
- Never put a raw connection string on the command line; pass the env-key via
  `--db` (the value is resolved from the secret store).
- Avoid `SELECT *` on large tables — name columns and add a `LIMIT`.
