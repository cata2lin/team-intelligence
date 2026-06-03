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

## Onboarding (one command per teammate)

Prereqs: replace `YOUR_ORG` in the installers + `marketplace.json`, push to
GitHub, apply `db/schema.sql` to the SharedClaude DB (once), and hand each
teammate the `KB_DATABASE_URL` (the SharedClaude connection string).

**macOS / Linux**
```bash
./install.sh --employee iulian --nas-root "/Volumes/team" \
             --kb-url "postgresql://scraper:****@38.242.226.83/SharedClaude"
```
**Windows (PowerShell)**
```powershell
./install.ps1 -Employee iulian -NasRoot "Z:\" `
              -KbUrl "postgresql://scraper:****@38.242.226.83/SharedClaude"
```

The installer installs `uv`, clones the repo to `~/team-intelligence`, writes the
per-machine env (`KB_DATABASE_URL`, `EMPLOYEE_HANDLE`, `NAS_ROOT`, `TEAM_REPO`)
+ the `CLAUDE.md` `@import`, adds the marketplace, and installs every plugin at
user scope. Restart Claude Code afterward.

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
