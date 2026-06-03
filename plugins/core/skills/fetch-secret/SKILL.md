---
name: fetch-secret
description: Retrieve a credential / API key / connection string from the team secret store (the SharedClaude `secrets` table) instead of a file. Use whenever a script or command needs a secret like a DATABASE_URL, OPENAI_API_KEY, SHOPIFY_CLIENT_SECRET, or a TOM/DPD key. Never print the value into chat.
---

# fetch-secret

> Author: **Arona core**. Secrets live in the DB, not on the NAS, not in git.

All credentials are rows in `SharedClaude.secrets`. Read one with `kb.py`
(reads `$KB_DATABASE_URL`); it prints the raw value with no newline so you can
pipe it straight into a process — do **not** echo it to the chat.

```bash
KB=${CLAUDE_PLUGIN_ROOT}/scripts/kb.py            # core plugin path

# Use a secret without revealing it (example: open psql):
DBURL=$(uv run "$KB" secret-get DATABASE_URL_METRICS)
# ... pass "$DBURL" to the process, never print it

# Set / rotate a secret:
uv run "$KB" secret-set OPENAI_API_KEY 'sk-proj-...' --service openai

# See which secrets exist (keys only, never values):
uv run "$KB" secret-list
```

## Rules
- Never paste a secret value into chat, code, a skill, or git.
- `secret-get` is for piping into a running process only.
- Per-brand tokens (Shopify/Meta/TikTok per store) still live in their app DB
  tables (e.g. `metrics.shopify_stores`); use `query-postgres` for those.
- Postgres connection strings for the 5 app DBs are already in the store and are
  what the read-only MCP servers use.
