# Contributing to the intelligence center

Everything here is shared with the whole team the moment it's merged. Two common
tasks:

## Add a skill (so everyone gets it)

1. Create it under **your** plugin so authorship is clear:
   ```
   plugins/<you>/skills/<skill-name>/SKILL.md
   plugins/<you>/scripts/<optional script>.py
   ```
2. `SKILL.md` needs YAML frontmatter — the `description` is how Claude decides
   when to use it, so make it specific:
   ```markdown
   ---
   name: my-skill
   description: <what it does and WHEN to use it — be concrete>
   argument-hint: "<args>"   # optional
   ---
   # my-skill
   > Author: <you>.
   …instructions…
   ```
3. If the skill needs a tool exposed over MCP, declare the server in
   `plugins/<you>/.mcp.json` (top-level `mcpServers`). It will auto-configure on
   every teammate's machine when they update — no manual MCP setup.
4. Put Python deps inline in the script (PEP 723) so `uv run` installs them:
   ```python
   # /// script
   # dependencies = ["openpyxl>=3.1", "requests>=2.31"]
   # ///
   ```
5. **Never** put a secret in a skill/script/commit. Read them from the knowledge
   base with `kb.py secret-get KEY` (the `core:fetch-secret` skill) and pipe the
   value into the process. Reference variable names only.
6. **Register it in the knowledge base** so the team catalog and authorship stay
   current:
   ```bash
   uv run plugins/core/scripts/kb.py skill-register \
     --plugin <you> --name <skill> --author <You> \
     --description "..." --path plugins/<you>/skills/<skill>/SKILL.md
   ```
   > **Catalog rendering (token discipline):** when the skills catalog in
   > `shared/CLAUDE.team.md` is (re)generated from the DB, render each skill's
   > description **compact (≤10 words + …)**, NOT the full description. The catalog
   > loads into every session on every turn; full descriptions there cost ~3.3k
   > tokens/turn team-wide for zero benefit (routing uses `find_skills` on the DB,
   > which keeps the full text). Keep the DB descriptions full; keep the rendered
   > catalog short. (Measured 2026-07-15: 30.8KB→17.4KB, −44%.)
   > Drop-in final step after any regen (format-agnostic, idempotent):
   > `uv run plugins/core/scripts/compact_catalog.py shared/CLAUDE.team.md --apply`
7. Open a PR. On merge, teammates get it on their next `claude plugin update`
   (or automatically if `autoUpdate` is on for the marketplace).

> **Shortcut — automate steps 6–7.** Once your `SKILL.md` is in place, ship the
> whole thing (register → branch → commit → push → PR → merge → sync → log) with
> one command — the **`gigi:publish-skill`** skill:
> ```bash
> uv run plugins/gigi/skills/publish-skill/scripts/publish_skill.py \
>   --path plugins/<you>/skills/<name>            # add --no-merge to review first
> ```
> It needs `gh` once (`brew install gh`); auth is automatic — it reads
> `GITHUB_TOKEN` from the knowledge base (or the keychain `git push` already uses).

## Add a new teammate

1. Create `plugins/<name>/.claude-plugin/plugin.json` (copy `plugins/alex/`),
   set `name` and `author`.
2. Add an entry to `.claude-plugin/marketplace.json` `plugins[]` pointing at
   `./plugins/<name>`.
3. Add `<name>` to the `PLUGINS`/`$Plugins` list in `install.sh` and
   `install.ps1` so future onboarding enables it.
4. PR + merge. Everyone enables it on their next update.

## Promote a personal skill to `core`
When a skill becomes a team standard, move it from `plugins/<you>/skills/…` into
`plugins/core/skills/…` (keep a `> Author:` line for credit). It then lives under
the `core:` namespace.

## Rules
- Read the shared rules in `shared/CLAUDE.team.md` — they apply to every change.
- Postgres stays read-only by default; writes need a dry-run + confirmation.
- Test a skill locally first: `claude plugin marketplace add .` then
  `claude plugin install <name>@team-intelligence`.
