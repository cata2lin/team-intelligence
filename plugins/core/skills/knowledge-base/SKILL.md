---
name: knowledge-base
description: Record and query the team knowledge base (SharedClaude DB) — log skill/file usage and changes, register files created or ported on the NAS, look up reference links (IPs, URLs, docs), and review recent team activity. Use this to LOG what you do and to RECALL shared knowledge.
---

# knowledge-base

> Author: **Arona core**. The SharedClaude database is the team's knowledge center.

Everything the team does is recorded here so every employee shares one memory:
who used/created/modified which skill, which files exist on the NAS or a local
machine and who put them there, the secret store, and reference links.

The tool is `kb.py` (run with `uv`). It reads `$KB_DATABASE_URL` (the SharedClaude
connection) and attributes activity to `$EMPLOYEE_HANDLE` + this machine.

## Always log these (per the team rules)
- **Used a skill** → `kb.py log --type skill --action used --name <plugin>:<skill>`
- **Created / modified a skill** → `kb.py skill-register --plugin <p> --name <n> --author <handle> --path <repo path>`
- **Created a file on the NAS** → `kb.py file-add --location nas --path "$NAS_ROOT/data/foo.xlsx" --category xlsx --action created`
- **Ported a file in/out of the NAS** → `kb.py file-add --location nas --path "<dest>" --source "<origin>" --action ported_in`
- **Learned a useful IP/URL/doc** → `kb.py resource-add --category url --label <name> --value <url> --description "..."`

## Recall
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/kb.py" recent --limit 20          # recent team activity
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/kb.py" secret-list --service shopify
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/kb.py" whoami
```
For ad-hoc questions, query the DB directly (read-only): tables `events`,
`skills`, `files`, `secrets`, `resources`, `employees`, `machines`; views
`v_recent_activity`, `v_skill_usage`.

## Notes
- `$KB_DATABASE_URL` is the one bootstrap secret on each machine (set at install).
- Never print secret *values* into chat; `secret-list` shows keys only.
