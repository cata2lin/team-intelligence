---
name: create-hooks
description: Scaffold and wire up Claude Code hooks — deterministic scripts that run at lifecycle events (PreToolUse, PostToolUse, UserPromptSubmit, Stop, SubagentStop, SessionStart/End, Notification, PreCompact). Use when asked to "create a hook", "add a hook", "auto-format on save", "block/guard a command", "log to the KB on every tool use", "run something on session start", or to set up project / global / plugin hooks. Includes the team's SharedClaude knowledge-base logging hooks.
argument-hint: "<what the hook should do, e.g. 'log skills to KB' | 'format on edit' | 'block dangerous bash'>"
---

# create-hooks

> Author: Catalin.

Hooks are shell commands Claude Code runs **deterministically** at lifecycle events — they can't hallucinate, so use them for formatting, guardrails, completion checks, and **logging to the SharedClaude KB**. Reach for a hook (code), not a prompt, for anything that must happen *every* time.

## 1. Pick the event
| Event | Fires | Common use |
|---|---|---|
| `PreToolUse` | before a tool runs | block/guard (exit 2 denies), confirm, lint inputs |
| `PostToolUse` | after a tool succeeds | auto-format edits, **log to KB** |
| `UserPromptSubmit` | when you submit a prompt | inject context, validate/redact |
| `Stop` | main agent finished a turn | completion checks, **flush a KB buffer** |
| `SubagentStop` | a subagent finished | per-subagent logging |
| `SessionStart` | session opens (`matcher: startup\|resume\|clear`) | load context, `git pull` team repo, connect NAS |
| `SessionEnd` | session closes | cleanup, final flush |
| `Notification` | Claude sends a notification | desktop / Slack alerts |
| `PreCompact` | before context compaction | persist / re-inject critical state |

## 2. Where it lives (scope)
- **Project**: `.claude/settings.json` (shared, committed) or `.claude/settings.local.json` (personal, gitignored).
- **Global / every project**: `~/.claude/settings.json`.
- **Plugin (team-wide)**: `plugins/<you>/hooks/hooks.json` using `${CLAUDE_PLUGIN_ROOT}` — ships to everyone on plugin update (see the `core` plugin's `hooks/hooks.json`).

> Don't hand-edit settings.json blindly — use the **`update-config`** skill, which validates the JSON and writes it at the right scope.

## 3. Shape (settings.json)
```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          { "type": "command", "command": "npx prettier --write \"$CLAUDE_FILE_PATHS\" || true", "timeout": 30 }
        ]
      }
    ]
  }
}
```
- `matcher` is a regex on the **tool name** for Pre/PostToolUse (`Write|Edit`, `Bash`, `Skill|mcp__.*`); omit it (or use `"*"`) to match all. For `SessionStart` it's `startup|resume|clear`.
- `command` runs through the shell; **quote paths**, and end non-blocking hooks with `|| true`.

## 4. Input & output (the contract)
A hook receives a JSON object on **stdin**: `session_id`, `transcript_path`, `cwd`, `hook_event_name`, plus event-specific fields (`tool_name`, `tool_input`, `tool_response`, `prompt`, …).
Control Claude via:
- **Exit code** — `0` = ok (stdout is surfaced for some events); `2` = **block** and feed stderr back to Claude; any other code = non-blocking error (stderr shown to the user).
- **JSON on stdout** (richer) — `{ "decision": "block"|"approve", "reason": "…", "continue": false, "hookSpecificOutput": { "additionalContext": "…" }, "suppressOutput": true }`.

Read stdin in a script:
```bash
#!/usr/bin/env bash
payload="$(cat)"; tool="$(printf '%s' "$payload" | jq -r '.tool_name')"
# …decide… ; exit 0
```

## 5. Test before you trust it
- Run it by hand with a sample event:
  `echo '{"tool_name":"Bash","tool_input":{"command":"ls"}}' | uv run --no-project ~/.claude/hooks/your_hook.py`
- Watch hooks fire live: `claude --debug` (prints each hook + its exit code).
- A logging/format hook must **never block** — always `exit 0` (or `|| true`).

## 6. Safety (hard rules)
- Hooks run **arbitrary code with your permissions** — review any hook before enabling it.
- **Never put a secret in a hook file or in git.** Read it from the environment, or from the KB: `KEY=$(uv run <repo>/plugins/core/scripts/kb.py secret-get KEY)` (the `core:fetch-secret` skill). Reference variable *names* only.
- Validate and quote every path from `tool_input`; assume hostile input.
- Keep hooks fast, set a `timeout`, and fail **open** for non-guard hooks.

## 7. KB-database hooks (SharedClaude)
Log team activity to the SharedClaude `events` table (`entity_type`, `entity_name`, `action`, `summary`, `details` jsonb, `session_uid`, `employee_id`). The one bootstrap secret **`$KB_DATABASE_URL` lives in `~/.claude/settings.json` `env` (local only — NEVER committed); `$EMPLOYEE_HANDLE` sets authorship** (resolved to `employees.id`).

**Easiest — let the `core` plugin do it.** Enable the `team-intelligence` marketplace + `core` plugin: it already ships `kb_hook_log.py` (PostToolUse `Skill|mcp__.*` → buffers) and `kb_hook_flush.py` (Stop → flushes to `events`). Nothing to wire by hand.

**Standalone — one self-contained logger** (no plugin needed). Drop this in `~/.claude/hooks/kb_log.py`; it reads stdin + `$KB_DATABASE_URL` / `$EMPLOYEE_HANDLE` and inserts one row. **No secret in the file.**
```python
# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9"]
# ///
import os, sys, json
def main():
    try: ev = json.load(sys.stdin)
    except Exception: return
    url = os.environ.get("KB_DATABASE_URL")
    if not url: return
    tool = ev.get("tool_name") or ""
    ti = ev.get("tool_input") or {}
    if ev.get("hook_event_name") == "SessionStart":
        etype, name, action, summary = "session", os.environ.get("EMPLOYEE_HANDLE","catalin"), "accessed", "session start"
    elif tool == "Skill":
        etype, name, action, summary = "skill", (ti.get("command") or ti.get("name") or "skill"), "used", "skill used"
    elif tool.startswith("mcp__"):
        etype, name, action, summary = "other", tool, "used", "mcp tool used"
    else:
        return  # ignore everything else
    try:
        import psycopg2
        with psycopg2.connect(url, connect_timeout=8) as c:
            c.autocommit = True
            with c.cursor() as cur:
                handle = (os.environ.get("EMPLOYEE_HANDLE") or "").lower().strip()
                emp = None
                if handle:
                    cur.execute("SELECT id FROM employees WHERE handle=%s", (handle,))
                    r = cur.fetchone(); emp = r[0] if r else None
                cur.execute(
                    "INSERT INTO events (employee_id, session_uid, entity_type, entity_name, action, summary, details) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (emp, ev.get("session_id"), etype, name, action, summary,
                     json.dumps({"cwd": ev.get("cwd"), "tool": tool})))
    except Exception:
        pass  # logging must NEVER break the session
main()
```
Wire it with the **`update-config`** skill: set `env.KB_DATABASE_URL` (the `postgresql://…/SharedClaude` URL — local only) and `env.EMPLOYEE_HANDLE` in `~/.claude/settings.json`, then add hooks:
```jsonc
"PostToolUse": [{ "matcher": "Skill|mcp__.*",
  "hooks": [{ "type": "command", "command": "uv run --no-project \"$HOME/.claude/hooks/kb_log.py\"", "timeout": 15 }]}],
"SessionStart": [{ "matcher": "startup|resume",
  "hooks": [{ "type": "command", "command": "uv run --no-project \"$HOME/.claude/hooks/kb_log.py\"", "timeout": 15 }]}]
```
Recall what's been logged with `kb.py recent` (or `core:knowledge-base`). To add a **guardrail** instead of a logger, use a `PreToolUse` matcher and `exit 2` with a reason on stderr to deny — or register a team-wide regex guard with `kb.py guard-add deny|ask "<regex>" --reason "…"`.
