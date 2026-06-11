---
name: clickup-report
description: Read and report on the company (arona.ro) ClickUp workspace — open tasks by person, by list/department, overdue, unassigned, and stale/no-due-date junk for cleanup. Read-only companion to clickup-task-creator (which only CREATES). Use for 'what's open in ClickUp', 'what's on Anne's/Iulian's plate', 'what's overdue', 'tasks with no owner/due date', 'clean up the Rapoarte list', workload/backlog dashboard. Triggers: clickup report, open tasks, overdue, my tasks, workload, backlog, junk cleanup, tasks by person/department.
---

# ClickUp Report (company workspace, read-only)

Read-only reporting over the arona.ro ClickUp workspace. The companion to
**clickup-task-creator** (which only *creates*): this skill only *reads*. It
enumerates every space → folder → list (plus folderless lists), pulls all open
tasks, resolves assignee ids to names, and answers backlog/workload questions.

It never writes to ClickUp.

## How to run

```bash
S=/Users/gheorghebeschea/Downloads/Scripturi/team-intelligence/plugins/gigi/skills/clickup-report
uv run "$S/clickup_report.py" --by-list        # default: overview per list/department
uv run "$S/clickup_report.py" --by-person      # backlog per person (+overdue/no-due counts)
uv run "$S/clickup_report.py" --overdue        # tasks past their due_date, oldest first
uv run "$S/clickup_report.py" --unassigned     # open tasks with no owner
uv run "$S/clickup_report.py" --junk           # open tasks with NO due_date (cleanup candidates)
uv run "$S/clickup_report.py" --stale 30       # open tasks untouched 30+ days
uv run "$S/clickup_report.py" --list "Rapoarte"  # full detail of one list
uv run "$S/clickup_report.py" --all            # full dashboard (all of the above)
```

Useful options:
- `--space "Proiecte"` — restrict to one space (Departamente / Proiecte / Rapoarte / Documentatie).
- `--limit N` — cap rows per table (default 30).

Run with no flags → `--by-list` overview.

Secrets resolve automatically from the team KB (`CLICKUP_API_TOKEN`,
`CLICKUP_TEAM_ID`); or pass them as env vars to override.

## How it works

1. `secret-get CLICKUP_API_TOKEN` + `CLICKUP_TEAM_ID` (prefers env, falls back to
   `core/scripts/kb.py`). Auth header is the **raw token** (not `Bearer`).
2. Enumerate: `GET /team/{id}/space` → for each space `GET /space/{id}/folder`
   (folder lists) **and** `GET /space/{id}/list` (folderless lists).
3. For every list: `GET /list/{id}/task?include_closed=false&subtasks=true&page=N`
   (paginated until `last_page`). Only open tasks are returned.
4. `GET /team` → map numeric assignee ids → usernames.
5. Compute per task: due (`due_date` ms), overdue (due < now), no-due (junk),
   unassigned (empty `assignees`), staleness (`date_updated`), priority, status,
   list/space. Aggregate and print Romanian-friendly console tables.

Workspace layout (verified live): **Departamente** (Customer Service, IT,
Operational, Administrativ), **Proiecte** (Grandia, Lab Noir, Nubra, Artevita),
**Rapoarte**, **Documentatie**.

## Limitations
- Read-only: it reports, it does not create/edit/close tasks. Use
  `clickup-task-creator` to create.
- "Open" = `include_closed=false`; closed/done tasks are excluded by design.
- Stale/junk are heuristics (last-updated age, missing due_date) — review before
  acting; the skill never deletes anything.
- ClickUp rate limit ~100 req/min; the script backs off on HTTP 429. Large
  workspaces take a few seconds.
- `--space`/`--list` match case-insensitively and accent-insensitively (partial
  match ok).
