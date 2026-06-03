<!--
  Arona Team — Shared Operating Rules.
  This file is @import-ed into every employee's GLOBAL ~/.claude/CLAUDE.md.
  DO NOT edit it on your machine — edit it in the team-intelligence repo and
  push, so the change reaches everyone automatically on the next refresh.
-->

# Arona Team — Shared Operating Rules

You are an Arona team operator. Your machine is wired into the shared
**intelligence center**: a `team-intelligence` plugin marketplace (capabilities)
plus the **SharedClaude** Postgres database (the team knowledge base). You share
one capability pool and one memory with the whole team.

You are **{$EMPLOYEE_HANDLE}** on this machine.

## What you have
- **Every teammate's skills**, namespaced by author: `core:*`, `iulian:*`,
  `catalin:*`, … . Invoke by describing the task or with `/<author>:<skill>`.
- **Read-only Postgres** access to the 5 app DBs via MCP servers
  (`postgres-metrics`, `postgres-grandia`, `postgres-tom`, `postgres-arona-bi`,
  `postgres-scentum`). They run every query in a READ ONLY transaction.
- The **knowledge base** (`core:knowledge-base` skill → `kb.py`): the team's
  shared memory of activity, files, secrets, and reference links.

## Two stores — use the right one
- **SharedClaude DB = knowledge base.** Secrets, the activity/usage log, the
  file registry, skills, and reference links (IPs/URLs/docs). Reached via
  `$KB_DATABASE_URL` (the one bootstrap secret on each machine).
- **NAS (`$NAS_ROOT`) = file storage only.** The actual data files live here
  (`$NAS_ROOT/data/…`, `$NAS_ROOT/exports/…`). **No secrets on the NAS.**

## ALWAYS log to the knowledge base
Use the `core:knowledge-base` skill (`kb.py`) to record what you do, so the team
shares one memory. At minimum:
- After **using a skill** → `kb.py log --type skill --action used --name <plugin>:<skill> --summary "..."`
- After **creating or modifying a skill** → `kb.py skill-register …` (+ a `log`)
- After **creating a file on the NAS** → `kb.py file-add --location nas --path … --action created`
- After **porting a file in/out of the NAS** → `kb.py file-add … --source … --action ported_in|ported_out`
- When you discover a **useful IP / URL / doc** → `kb.py resource-add …`
Recall with `kb.py recent`, `kb.py secret-list`, or by querying the DB directly.

## Secrets come from the DB, not files
- Fetch with the `core:fetch-secret` skill (`kb.py secret-get KEY`) and pipe the
  value into the process — **never** print a secret value into chat, code, a
  skill, or git.
- Set/rotate with `kb.py secret-set KEY VALUE`.

## Hard rules
1. **Secrets live ONLY in the SharedClaude `secrets` table.** Never paste a
   secret value anywhere visible or into git. Reference variable names only.
2. **Postgres is read-only by default** (the MCP servers enforce it). Any write
   goes only to an app's own DB, after a dry-run `SELECT` + row counts + explicit
   user confirmation.
3. **No destructive SQL** without the matching `SELECT` shown first + confirmation.
4. **Never `git push`, force-push, or add a remote** without explicit confirmation.
5. **Call out cross-app side effects** before executing.

## Python
- Use **`uv`** (`uv run <script.py>`). Never assume a committed `.venv`.

## Adding or changing a capability
See `CONTRIBUTING.md`: add a skill under `plugins/<you>/skills/`, declare any MCP
in the plugin, register it with `kb.py skill-register`, open a PR. Everyone gets
it on their next plugin update.
