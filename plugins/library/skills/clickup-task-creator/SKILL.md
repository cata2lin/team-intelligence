---
name: clickup-task-creator
description: Create a task in the company (arona.ro) ClickUp workspace via the ClickUp REST API, placed in the correct department/project LIST with the right native fields (assignee, priority, due date, tags) and any list-specific custom fields auto-set. Use whenever the user wants to create, file, open, or log a ClickUp task (e.g. "create a ClickUp task for IT to fix the printer", "open a task in the Grandia project assigned to Iulian, high priority, due Friday"). Documents the real workspace structure, every field, mandatory vs optional, and a one-command helper that does it end-to-end.
---

# ClickUp Task Creator (company workspace)

Create a task in the company's ClickUp workspace from natural language — placed in the **correct list** (which is how this workspace models department/project), with the right **assignee, priority, due date, tags**, and any **list-specific custom fields** — via the ClickUp REST API v2.

> **Workspace reality (verified live):** classification in this workspace is **by LIST**, not by custom fields. There is *no* Department/Area/Complexity/Source/Brand custom field — those don't exist here. "Put it in the right department" = "create it in the right list." Only one custom field exists at all today: **`NR TEL`** (a phone-number field on *Customer Service*). The helper **discovers each list's real fields at runtime**, so if classification custom fields are added later it will set them automatically — you never hardcode field ids.

## When to use
Any time the user wants a task created in ClickUp. Figure out **which list** it belongs in (see the catalog), collect the native fields they imply, then run the helper (preferred).

## Prerequisites (environment)
| Var | What | How to get it |
|---|---|---|
| `CLICKUP_API_TOKEN` | Personal API token, starts `pk_` | ClickUp → Settings → Apps → API Token |
| `CLICKUP_TEAM_ID` | Workspace (team) id | the number in `app.clickup.com/<TEAM_ID>/...`, or `GET /api/v2/team` |

> **Auth header is the raw token** (`Authorization: pk_...`) — **not** `Bearer`. API base `https://api.clickup.com/api/v2`. Rate limit ~100 req/min (the helper paces itself).

> **Team setup (Arona intelligence center):** the token + team id live in the shared secret store, not your shell. Fetch them via the `core:fetch-secret` skill (`kb.py secret-get`) and pass them as env to the helper:
> ```bash
> CLICKUP_API_TOKEN=$(uv run "$KB" secret-get CLICKUP_API_TOKEN) \
> CLICKUP_TEAM_ID=$(uv run "$KB" secret-get CLICKUP_TEAM_ID) \
>   node "${CLAUDE_PLUGIN_ROOT}/skills/clickup-task-creator/create-task.mjs" <args>
> ```
> where `$KB` is the core plugin's `scripts/kb.py`. (Set the values once with `kb.py secret-set CLICKUP_API_TOKEN '<pk_...>'`.)

---

## The workspace — where tasks go (this IS the classification)

```
Departamente   (internal departments)
  ├─ Customer Service   (has custom field: NR TEL — phone number)
  ├─ IT
  ├─ Operational
  └─ Administrativ
Proiecte       (client projects / brands)
  ├─ Grandia
  ├─ Lab Noir
  ├─ Nubra
  └─ Artevita
Rapoarte
  └─ Rapoarte           (reports)
```

- **Internal task** → a `Departamente` list (Customer Service / IT / Operational / Administrativ).
- **Client/brand work** → a `Proiecte` list (Grandia / Lab Noir / Nubra / Artevita).
- The helper resolves `--list "<name>"` to the right list id (case-insensitive, partial match ok). Always re-confirm the catalog at runtime — lists can change:
  ```bash
  H="Authorization: $CLICKUP_API_TOKEN"; B=https://api.clickup.com/api/v2
  curl -s -H "$H" "$B/team/$CLICKUP_TEAM_ID/space"           # spaces[]
  curl -s -H "$H" "$B/space/<SPACE_ID>/folder"               # folders[].lists[]
  curl -s -H "$H" "$B/space/<SPACE_ID>/list"                 # folderless lists[]
  ```

## Members (for `--assignee` / reviewer) — verified live
`iulian radu <radu@arona.ro>` · `mugurel nicolae <mugurel.nicolae@arona.ro>` · `Andreea Popa` · `Anna Rugina` · `Olariu Oana-Maria` · `Adriana Ciovana` · `Anne Gheorghe <…@overheat.agency>` · `Oana-Georgiana Vasile` · `Raluca Diaconu` · `Gheorghe Beschea <…@overheat.agency>` · `Catalin Carcu <catalin.carcu@arona.ro>`.
Assignees must be **numeric user ids** — resolve a name/email via `GET /api/v2/team` → `teams[].members[].user.{id,username,email}` (the helper does this).

---

## Quick start — the helper (preferred)

```bash
node ~/.claude/skills/clickup-task-creator/create-task.mjs \
  --list "IT" \
  --title "Fix the office label printer" \
  --priority 2 \
  --assignee "mugurel" \
  --due "2026-06-20" \
  --description "Printer in the warehouse won't feed labels." \
  --tags "hardware,urgent"
```

It resolves the list, discovers that list's custom fields, resolves the assignee, creates the task, and prints the URL. `--dry-run` previews the payload (and, if the list name is wrong, prints the real list catalog). `--help` for full usage.

Client-project example:
```bash
node ~/.claude/skills/clickup-task-creator/create-task.mjs \
  --list "Grandia" --title "Refresh hero creatives" --priority 3 --due "2026-07-01"
```
Customer-service example (uses the one real custom field — **`NR TEL` is a number field, so pass digits only**, no `+`/spaces):
```bash
node ~/.claude/skills/clickup-task-creator/create-task.mjs \
  --list "Customer Service" --title "Call back unhappy client" --nr-tel 0712345678 --priority 1
```

---

## Field reference — what you can send

### Native task fields (`POST /list/{id}/task`)
| Field | Required? | Type / format | Notes |
|---|---|---|---|
| `name` | **Required** | string | The task title. Helper: `--title`. |
| `description` | optional | string | Plain text (`--description`). Use `markdown_content` for markdown. |
| `assignees` | optional | number[] | **Numeric user ids**, not names. Helper resolves `--assignee <name|email>`. |
| `priority` | optional | 1–4 or null | **1=Urgent, 2=High, 3=Normal, 4=Low.** `--priority`. |
| `due_date` (+ `due_date_time`) | optional | Unix **ms** (+bool) | `--due 2026-06-20` (date) or an ISO datetime (sets time flag). |
| `start_date` (+ `start_date_time`) | optional | ms (+bool) | — |
| `time_estimate` | optional | **milliseconds** | Helper takes **minutes** via `--time-estimate` and converts. |
| `tags` | optional | string[] | `--tags "a,b,c"`. Created if missing. |
| `status` | optional | string | Defaults to the list's first status; `--status` to override. |
| `parent` | optional | task id | Make it a subtask. |
| `links_to` | optional | task id | Link to another task. |
| `notify_all` | optional | boolean | Notify assignees/watchers. |
| `custom_fields` | optional | `{id,value}[]` | Only fields that exist on the chosen list (auto-discovered). |

### Custom fields (auto-discovered per list)
The helper calls `GET /list/{id}/field`, then sets only the fields that exist, matching by name:
- **Complexity** (number 1–5) and **Task Type** (dropdown) — the two enriched fields the analytics app uses. Create them once (see setup below), then pass `--complexity 3` / `--task-type Operational`; the helper resolves the dropdown option automatically. On lists where they don't exist it skips with a warning.
- **Customer Service** → `NR TEL` (number). Helper flag: `--nr-tel <digits>`.
- Any other custom field you add later is also auto-mapped by name (`--department`, `--area`, `--source`, `--brand`, `--kpi`) — no code changes needed.

### Setting up Complexity & Task Type (one-time, in ClickUp)
The ClickUp API can't create custom fields — create these once in the ClickUp UI; the analytics app then reads them on sync and this helper writes them:
1. **Complexity** — a **Number** field named exactly `Complexity` (use values 1–5: 1=Very Easy, 2=Easy, 3=Medium, 4=Hard, 5=Strategic).
2. **Task Type** — a **Dropdown** field named exactly `Task Type` with options: Operational, Strategic, Repetitive, Urgent, Bug, Research.

Create them at the **Space** level (Departamente and Proiecte) so every list inherits them: ClickUp → open the Space → Space Settings → **Custom Fields → Create Field** (or a list's column header **＋ → Add Field**), set name/type/options, apply to the Space. Names must match exactly (case-insensitive) for the app + helper to map them.

**How custom values are encoded** (when such fields exist): `drop_down` → the chosen option's `id` (match label to `type_config.options[].name`); `labels` → array of option ids; `number` → raw number; `text` → string; `date` → ms; `users` → post-create `POST /task/{id}/field/{id}` with `{value:{add:[userId]}}`; `checkbox` → `"true"`/`"false"`.

### Mandatory vs optional (this workspace)
- **Mandatory:** `--list` (the department/project) and `--title`. That's all ClickUp + this workspace strictly need.
- **Strongly recommended:** `--priority`, `--assignee`, `--due`.
- **Optional:** `--description`, `--tags`, `--time-estimate`, `--status`, `--nr-tel` (Customer Service), `--parent`.

---

## Raw API flow (if not using the helper)
```bash
# 1) find the list id (hierarchy calls above) → LIST_ID
# 2) (optional) discover its fields:
curl -s -H "Authorization: $CLICKUP_API_TOKEN" "$B/list/<LIST_ID>/field"
# 3) create the task:
curl -s -X POST -H "Authorization: $CLICKUP_API_TOKEN" -H "Content-Type: application/json" \
  "$B/list/<LIST_ID>/task" -d '{
    "name":"Fix the office label printer",
    "description":"…",
    "assignees":[<USER_ID>],
    "priority":2,
    "due_date":1750377600000,"due_date_time":false,
    "tags":["hardware"]
  }'
# response.url = https://app.clickup.com/t/<id>
```

## Gotchas
- **Classification = list**, not a custom field, in this workspace. Pick the right `Departamente`/`Proiecte` list.
- **Auth:** raw token, no `Bearer`.
- **Timestamps are milliseconds**; validate the date parses before sending (a bad date silently becomes no-date — the helper rejects it).
- **Assignees are numeric ids**, never names.
- If the user names a "department" that isn't a list (e.g. "Marketing"), tell them the available lists and ask which fits — don't invent one.
- Re-list spaces/lists at runtime; the catalog above is a verified snapshot, not a constant.

## Reference specs
Full OpenAPI specs live in the ClickUp-integration project: `clickup-api-v2-reference.json` (v2), `ClickUp_PUBLIC_API_V3.yaml` (v3).
