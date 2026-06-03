---
name: query-postgres
description: Query the Arona production Postgres databases (metrics, Grandia, tom_wms, test/arona-bi, Parfum_Iulian, and others). Use whenever the user wants to read, inspect, count, or export data from a company database. Read-only by default.
---

# query-postgres

> Author: **Arona core**. Read-only by default.

The 5 app databases are exposed as **read-only MCP servers** by the `core`
plugin — prefer these for any read, they run every query in a READ ONLY
transaction:

| App | Database | MCP server | Access |
|---|---|---|---|
| metrics | `metrics` | `postgres-metrics` | read-only |
| grandia-inventory | `Grandia` | `postgres-grandia` | read-only |
| tom | `tom_wms` | `postgres-tom` | read-only |
| arona-bi | `test` | `postgres-arona-bi` | read-only |
| scentum | `Parfum_Iulian` | `postgres-scentum` | read-only |

Just ask the relevant MCP server to run your `SELECT`.

## Direct psql / Python (for DBs without an MCP server, or for writes)

Credentials come from the **NAS**, never from git:
`$NAS_ROOT/secrets/credentials.env` holds `PG_HOST`, `PG_USER`, `PG_PASSWORD`,
and per-app `DATABASE_URL_*` strings.

```bash
# psql one-shot (clean, machine-parseable)
set -a; . "$NAS_ROOT/secrets/credentials.env"; set +a
psql "$DATABASE_URL_METRICS" -A -F $'\t' -t -c "SELECT id, name FROM brands ORDER BY id;"
```

```python
# uv run this — python-dotenv + psycopg2 come from the repo's pyproject
import os
from dotenv import load_dotenv
import psycopg2

load_dotenv(os.path.join(os.environ["NAS_ROOT"], "secrets", "credentials.env"))
conn = psycopg2.connect(os.environ["DATABASE_URL_METRICS"])
conn.set_session(readonly=True)
with conn.cursor() as cur:
    cur.execute("SELECT id, name FROM brands ORDER BY id LIMIT 50;")
    for row in cur.fetchall():
        print(row)
```

## Safety rules
- **Read-only by default.** For an `UPDATE`/`DELETE`: run the matching `SELECT`
  first, show the row count, and ask before mutating. Writes go only to an
  app's own DB.
- Never `DROP`/`TRUNCATE`/alter schema without explicit confirmation.
- Avoid `SELECT *` on large tables (`test`, `trendyol`) — name columns and add a
  `LIMIT`.
