# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9"]
# ///
"""KB activity logger hook — logs skill/MCP usage and session starts to the
SharedClaude `events` table. Reads the connection string from $KB_DATABASE_URL
and authorship from $EMPLOYEE_HANDLE. No secret is hard-coded here. Always exits
0 so logging can never break a session.
"""
import os, sys, json


def main():
    try:
        ev = json.load(sys.stdin)
    except Exception:
        return
    url = os.environ.get("KB_DATABASE_URL")
    if not url:
        return

    tool = ev.get("tool_name") or ""
    ti = ev.get("tool_input") or {}
    event = ev.get("hook_event_name") or ""

    if event == "SessionStart":
        etype, name, action, summary = "session", os.environ.get("EMPLOYEE_HANDLE", "catalin"), "accessed", "session start"
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
                    r = cur.fetchone()
                    emp = r[0] if r else None
                cur.execute(
                    "INSERT INTO events (employee_id, session_uid, entity_type, entity_name, action, summary, details) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (emp, ev.get("session_id"), etype, name, action, summary,
                     json.dumps({"cwd": ev.get("cwd"), "tool": tool})),
                )
    except Exception:
        pass  # logging must NEVER break the session


main()
