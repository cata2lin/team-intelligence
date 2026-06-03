---
description: End-to-end recipe — pull rows from an Arona production Postgres DB and write them into a styled .xlsx (or a Google Sheet) under $NAS_ROOT/exports.
---

# DB → Excel

> Author: **Arona core**. Read-only by default. Secrets come from the
> SharedClaude `secrets` table — never a `.env` file, never the NAS.

End-to-end: take a `SELECT` against a company database and turn it into a
styled `.xlsx` the user can download or email — or a living Google Sheet.

## Walkthrough

1. **Pick the source DB.** The five app databases are exposed as read-only MCP
   servers and their connection strings live in the secret store under
   `DATABASE_URL_*` keys. See the `query-postgres` skill for the table:
   `DATABASE_URL_METRICS` (multi-brand marketing), `DATABASE_URL_GRANDIA`,
   `DATABASE_URL_TOM`, `DATABASE_URL_ARONA_BI`, `DATABASE_URL_SCENTUM`.
   You pass the **env-key name**, not the credential itself.
2. **Write the SQL.** Always name explicit columns and add a `LIMIT` for first
   runs. Avoid `SELECT *` on large tables.
3. **Run the `write-xlsx` skill.** Its bundled script imports `kb_env` and calls
   `load_secrets_into_env()`, so `$KB_DATABASE_URL` is the only thing that must
   be set — it resolves `DATABASE_URL_*` from the secret store. Output goes
   under `$NAS_ROOT/exports/`.

## Concrete example: top 20 brands by Shopify revenue last 30 days

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/write_xlsx.py" \
  --db DATABASE_URL_METRICS \
  --sql "SELECT b.name AS brand,
                SUM(s.\"totalSales\") AS revenue_30d
         FROM shopify_analytics_daily s
         JOIN brands b ON b.id = s.\"brandId\"
         WHERE s.day >= CURRENT_DATE - INTERVAL '30 days'
         GROUP BY b.name
         ORDER BY revenue_30d DESC
         LIMIT 20" \
  --out "$NAS_ROOT/exports/top_brands.xlsx" \
  --sheet "top_brands"
```

The script runs the query in a `READ ONLY` transaction, then writes a workbook
with a **bold, grey-filled, frozen** header row and **auto-sized** columns, and
prints `wrote <path> (N rows, M cols)`. PEP 723 inline deps (`openpyxl`,
`psycopg2-binary`) auto-install on first `uv run`.

For a long or complex query, keep the SQL in a file instead of inline:

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/write_xlsx.py" \
  --db DATABASE_URL_METRICS \
  --sql-file ./report.sql \
  --out "$NAS_ROOT/exports/report.xlsx" \
  --sheet "Q2 brands"
```

## After writing — log it to the KB

Register the file you created on the NAS so the team shares one memory:

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/kb.py" \
  file-add --location nas --path "$NAS_ROOT/exports/top_brands.xlsx" \
  --category xlsx --action created
```

## Variants

- **Want a Google Sheet instead?** Use the `export-to-google-sheet` skill — it
  authenticates with OAuth Desktop creds at `~/.config/gcp` (your own Google
  account, *not* a DB secret) and clears → writes → bold/frozen-header-formats
  the tab. Source the data the same way: resolve a `DATABASE_URL_*` from the
  secret store. Prefer this for a living, shared document; prefer `.xlsx` for a
  downloadable/emailable file.
- **Want it pre-populated on a schedule?** Wrap the `write_xlsx.py` call in a
  scheduled remote agent / cron routine.

## Rules

- **Read-only.** `write_xlsx.py` opens the connection with
  `set_session(readonly=True)` and `--sql` is for `SELECT` only.
- **Never put a raw connection string on the command line.** Pass the env-key
  via `--db`; the value is resolved from the secret store by `kb_env`. If a
  shell/skill ever needs the value itself, fetch it with
  `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/kb.py" secret-get <KEY>` and pipe it —
  never print it.
- **Write outputs under `$NAS_ROOT/exports/`.** Parent dirs are created
  automatically.
- Avoid `SELECT *` on large tables (`test`, `trendyol`) — name columns, add a
  `LIMIT`.

## Related

- Skill `write-xlsx` — the `.xlsx` writer (`scripts/write_xlsx.py`).
- Skill `export-to-google-sheet` — the Sheets variant.
- Skill `query-postgres` — the DB list + read-only MCP servers.
- Skill `fetch-secret` — pulling a secret out of the store for piping.
