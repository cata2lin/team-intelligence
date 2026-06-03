---
name: query-postgres
description: Query the Arona production Postgres databases (metrics, Grandia, tom_wms, test/arona-bi, Parfum_Iulian, and others). Use whenever the user wants to read, inspect, count, or export data from a company database. Read-only by default.
---

# query-postgres

> Author: **Arona core**. Read-only by default. Credentials come from the knowledge base, never from a file or the NAS.

## Reads -- use the MCP servers (preferred)
The 5 app databases are exposed as **read-only MCP servers** by the `core`
plugin; they run every query in a READ ONLY transaction:

| App | Database | MCP server |
|---|---|---|
| metrics | `metrics` | `postgres-metrics` |
| grandia-inventory | `Grandia` | `postgres-grandia` |
| tom | `tom_wms` | `postgres-tom` |
| arona-bi | `test` | `postgres-arona-bi` |
| scentum | `Parfum_Iulian` | `postgres-scentum` |

Just ask the relevant MCP server to run your `SELECT`. Their connection strings
are read from the `secrets` table at launch -- nothing to configure.

## Other DBs, or writes -- pull the connection string from the secret store
Connection strings live in `SharedClaude.secrets`, not on disk. Fetch one with
`kb.py` (the `core:fetch-secret` skill) and pipe it straight into the process --
never print it:

```bash
KB="${CLAUDE_PLUGIN_ROOT}/scripts/kb.py"
DBURL=$(uv run "$KB" secret-get DATABASE_URL_METRICS)
psql "$DBURL" -A -F $'\t' -t -c "SELECT id, name FROM brands ORDER BY id LIMIT 50;"
```

In Python, `kb_env.load_secrets_into_env()` fills `os.environ` with every
`DATABASE_URL_*`, then:
```python
import os, psycopg2
from kb_env import load_secrets_into_env
load_secrets_into_env()
conn = psycopg2.connect(os.environ["DATABASE_URL_METRICS"])
conn.set_session(readonly=True)
```

## Safety rules
- **Read-only by default.** For an `UPDATE`/`DELETE`: run the matching `SELECT`
  first, show the row count, and ask before mutating. Writes go only to an app's
  own DB.
- Never `DROP`/`TRUNCATE`/alter schema without explicit confirmation.
- Avoid `SELECT *` on large tables (`test`, `trendyol`) -- name columns + `LIMIT`.
- Never paste a secret value into chat.
