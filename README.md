# team-intelligence — Arona shared intelligence center

A GitHub repo + a Postgres database that give all 6 teammates **one shared pool
of skills** and **one shared memory**. Anything one person builds, everyone gets
— attributed to its author, with its MCP servers auto-configured. Everything the
team does (skills used/created, files created/ported, secrets, reference links)
is recorded in a central knowledge base.

Two backing stores:
- **`SharedClaude` Postgres DB = the knowledge base** — activity log, secret
  store, file registry, skill registry, reference links.
- **The NAS (`$NAS_ROOT`) = file storage only** — the actual data files. No
  secrets on the NAS.

Distribution is a **Claude Code plugin marketplace** (this repo).

## How it works

```
GitHub repo (this)                          SharedClaude DB  ($KB_DATABASE_URL)
────────────────────────────────           ────────────────────────────────────
.claude-plugin/marketplace.json   catalog   employees / machines   who & where
plugins/core/                     Arona     skills                 capability registry
plugins/<employee>/               6 people  files                  NAS + local file registry
shared/CLAUDE.team.md             rules      secrets                the credential store
db/schema.sql                     KB schema  resources              IPs / URLs / docs / links
install.sh / install.ps1          onboarding events                 the activity / usage log

NAS ($NAS_ROOT)            data/   exports/        ← files only, no secrets
```

- **Per-employee plugins** (`plugins/iulian`, `plugins/catalin`, …) → a skill's
  namespace shows who made it: `/catalin:excel-api-push`. `plugins/core` holds
  company-wide tools.
- **MCP auto-config**: `core` inlines 5 read-only Postgres MCP servers in its
  `plugin.json`; they start automatically on enable and read their connection
  strings from `SharedClaude.secrets` at launch (nothing in git).
- **Knowledge base**: the `core:knowledge-base` skill (`kb.py`) is how the agent
  logs activity and recalls shared knowledge; `core:fetch-secret` reads secrets.
- **Global rules stay current**: each machine's `~/.claude/CLAUDE.md` `@import`s
  `shared/CLAUDE.team.md`; a `git pull` (SessionStart hook) refreshes it.
- **Updates**: `claude plugin update` / marketplace `autoUpdate`.

## Onboarding (clone, then one command — zero‑touch)

Each teammate gets their **own database login** (their username *is* their
handle, e.g. `iulian`). The admin gives them just **three things: DB host/IP,
user, password**. Everything else — identity, every secret/API key, the NAS, the
plugins, the global config — is pulled from the database.

```bash
git clone https://github.com/cata2lin/team-intelligence.git
cd team-intelligence
./install.sh            # macOS / Linux        (Windows: ./install.ps1)
```

The walkthrough then:
1. asks only for the DB **host / user / password** (db name defaults to `SharedClaude`);
2. **identifies you automatically** from your DB login (the role = your handle) — no picking;
3. pulls **your NAS login from the DB** and connects it (no NAS prompt);
4. installs `uv`, enables every team plugin at **user scope**, writes the global
   `~/.claude/settings.json` env (`KB_DATABASE_URL`, `EMPLOYEE_HANDLE`,
   `NAS_ROOT`, `TEAM_REPO`) + the `CLAUDE.md` `@import`, and registers your machine.

Restart Claude Code. From then on, in **every** project: the team skills, the
read‑only Postgres MCP, the knowledge base, all secrets, and your NAS folder.

> **Admin one‑time setup (all in the DB):** push this repo to GitHub, apply
> `db/schema.sql`, create one DB role per employee (login = handle, limited to
> `SharedClaude`), load secrets with `kb.py secret-set`, and load each person's
> NAS login with `kb.py nas-set --employee <handle> --username <u> --password <p>`.
> Microsoft/OneDrive is a one‑time `microsoft_auth.py --login` (token then shared
> via the DB).

Plugins are enabled at **user scope** (written to `~/.claude/settings.json`) so
they work in *every* project, and the walkthrough does it without needing the
`claude` CLI on PATH (Claude Code reads the marketplace + `enabledPlugins` on
start). If you ever get per-project MCP approval prompts, a plugin was enabled at
the wrong scope — re-run onboarding.

## The knowledge base (`db/schema.sql`)

| Table | Holds |
|---|---|
| `employees`, `machines` | the 6 people + each one's machine (host, NAS mount, agent path) |
| `skills` | capability registry — plugin, name, author, version, repo path |
| `files` | every file on the NAS or a local agent — path, who created/ported it, when |
| `secrets` | the credential store (replaces the NAS credentials file) |
| `resources` | IPs, URLs, hosts, endpoints, docs, links |
| `events` | the unified append-only activity / usage / change log |
| views | `v_recent_activity`, `v_skill_usage` |

`kb.py` (the `core:knowledge-base` skill) is the interface:
`kb.py log … | skill-register … | file-add … | secret-get/-set … | resource-add … | recent`.

## Requirement → mechanism

| Goal | How |
|---|---|
| On GitHub | this marketplace repo |
| NAS as storage | `$NAS_ROOT` for data files |
| Everyone has everyone's skills | one marketplace, all 7 plugins enabled |
| Identify the author of a skill | per-employee plugin namespace + `skills.author` |
| Auto-install & configure MCP | `core` plugin's inline `mcpServers` auto-start |
| Track skills/files/usage/changes | the `events` log + `skills`/`files` registries (`kb.py`) |
| Secrets off the NAS | the `secrets` table (`kb.py secret-get/-set`) |
| IPs / URLs / docs | the `resources` table |
| Global CLAUDE.md always current | `@import` of `shared/CLAUDE.team.md` + auto `git pull` |
| Usable from the start | `install.sh` / `install.ps1` |

## Adding a teammate or a skill
See [CONTRIBUTING.md](CONTRIBUTING.md).

## Status
Live foundation: the `SharedClaude` schema is applied and seeded (6 employees,
51 secret keys, reference data); the Postgres credentials are populated so the
read-only MCP servers work; `core` ships `query-postgres`, `knowledge-base`,
`fetch-secret`; `catalin` ships the `excel-api-push` example. **To finish:** fill
the 29 sensitive third-party secrets (`kb.py secret-set …`), and port the
remaining `assistant/` skills/playbooks into `core`.
