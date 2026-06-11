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

> All team plugins are enabled at **user scope**, so they work in every project.
> If you ever see per-project approval prompts for the `postgres-*` MCP servers,
> they were enabled at the wrong scope -- re-run the onboarding.

## Two stores — use the right one
- **SharedClaude DB = knowledge base.** Secrets, the activity/usage log, the
  file registry, skills, and reference links (IPs/URLs/docs). Reached via
  `$KB_DATABASE_URL` (the one bootstrap secret on each machine).
- **NAS (`$NAS_ROOT`) = file storage only.** The actual data files live here
  (`$NAS_ROOT/data/…`, `$NAS_ROOT/exports/…`). **No secrets on the NAS.**

## Logging to the knowledge base
**Skill and MCP/DB usage is logged automatically** — a PostToolUse hook buffers
it and a Stop hook flushes it to the `events` table every turn (guaranteed; you
don't have to remember). You still record the things hooks can't see, with the
`core:knowledge-base` skill (`kb.py`):
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

> These rules are also **enforced** by a PreToolUse guardrail hook: catastrophic
> commands are blocked outright, and destructive SQL / `git push` require
> confirmation. Add team guardrails with `kb.py guard-add deny|ask <regex>
> --reason "..."` (applies to everyone next session); list with `kb.py guard-list`.

## NAS files (your shared storage)
Your files live on the NAS at **`$NAS_ROOT`** = your `ClaudeShared/<you>` folder
(`$NAS_ROOT/data`, `$NAS_ROOT/exports`; the sibling `_shared` is team-wide). It
auto-connects each session using your NAS login stored in the database;
reconnect or inspect with the **`core:nas`** skill. Record files you create:
`kb.py file-add --location nas --path "$NAS_ROOT/..."`. No secrets on the NAS.

## Python
- Use **`uv`** (`uv run <script.py>`). Never assume a committed `.venv`.

## Adding or changing a capability
See `CONTRIBUTING.md`: add a skill under `plugins/<you>/skills/`, declare any MCP
in the plugin, register it with `kb.py skill-register`, open a PR. Everyone gets
it on their next plugin update.

## Working practices — Claude Code (productivity & quality)
Distilled from heavy real-world use (ykdojo/claude-code-tips + community). Universal
across **every** project; complements the **Hard rules** above (which already cover
git-push, secrets, and read-only Postgres — not repeated here). The named helper
skills are reusable Claude skills — add your own per `CONTRIBUTING.md`.

**Quality & verification**
- **Verify before "done".** Never declare a task complete without a real feedback loop
  — run the tests, boot the app, or check actual output — then end with a short table of
  each claim and how it was verified. This is the single highest-impact habit.
  (skill: `verify-output`)
- **Prefer TDD for non-trivial logic:** write a failing test, commit it, then implement
  to green. Ship a regression test in the *same* change as the bug fix.
- **Simplify & explain.** Claude biases toward more code. When output looks
  overcomplicated or adds unrequested changes, ask it to simplify and to explain *why*
  each change was made before accepting.

**Context hygiene** (context is best served fresh and condensed)
- **Fresh context beats a long thread.** One conversation per topic. After ~two
  corrections, `/clear` and rewrite a precise prompt instead of fighting a cluttered
  thread; use `/compact` only for related work in the same session.
- **Hand off before clearing.** On unfinished work, write a `HANDOFF.md`
  (Goal / Current Progress / What Worked / What Didn't Work / Next Steps) and start the
  next session from that file. (skill: `handoff`)
- **Keep CLAUDE.md short and always-true.** Start empty; add a rule only when you repeat
  yourself; after every mistake, record the rule so it never recurs; prune periodically.
  (skill: `review-claudemd`)  Team-wide rules belong here in `shared/CLAUDE.team.md`,
  not on your own machine.

**Planning & parallelism**
- **Decompose, then plan.** Break hard tasks into one-shottable subtasks (A→A1→A2→B)
  and use **plan mode** (Shift+Tab) to perfect the plan before implementing.
  (skill: `break-down`)
- **Use subagents** for exploration and large fan-out so the main context stays small;
  **git worktrees** for true parallel work (cap ~3–5; keep one read-only "analysis"
  worktree for logs/queries — ask Claude to create them, no syntax needed).

**Automation & safety** (beyond the enforced Hard rules)
- **Deterministic hooks, not prompts**, for repeatable steps: auto-format on
  PostToolUse(Write|Edit), gate commits on PreToolUse, verify on Stop. (Add team
  guardrails with `kb.py guard-add deny|ask <regex>`.)
- **Allowlist safe commands** via `/permissions` (wildcards, checked into
  `.claude/settings.json`). **Never** run `--dangerously-skip-permissions` on a host
  machine — only inside a container. Audit approved commands with `npx cc-safe .`.
- **MCP servers** belong in a checked-in config (`.mcp.json` / plugin), not ad-hoc;
  enable lazy-loading of MCP tool definitions to save context.

**Efficiency**
- **Don't watch long jobs** (builds/CI) — poll with exponential backoff (1m/2m/4m…) or
  background with Ctrl+B and let Claude check via BashOutput. (CI deep-dive: skill
  `gha-investigate`.)
- **Absolute paths** for cross-folder file references (`realpath`); when a page won't
  fetch, Ctrl+A → copy → paste it in.
- **Schema changes need a migration** — adding a DB column requires a reversible,
  idempotent migration (`ADD COLUMN IF NOT EXISTS`); an ORM's `create_all`/sync will
  **not** alter an existing table.
