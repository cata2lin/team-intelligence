# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9"]
# ///
"""SessionStart hook: pull the team's active guardrails from the DB into a local
cache (~/.claude/team-guardrails.json) so the PreToolUse guard can read them
fast (no DB call on the hot path). One read per session. Exit 0 (best-effort).
"""
import json
import os
import sys


def main():
    url = os.environ.get("KB_DATABASE_URL")
    out = os.path.expanduser("~/.claude/team-guardrails.json")
    if not url:
        sys.exit(0)
    try:
        import psycopg2
        deny, ask = [], []
        with psycopg2.connect(url, connect_timeout=12) as conn, conn.cursor() as cur:
            cur.execute("SELECT kind, pattern, reason FROM guardrails WHERE active ORDER BY id")
            for kind, pattern, reason in cur.fetchall():
                (deny if kind == "deny" else ask).append({"pattern": pattern, "reason": reason or ""})
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            json.dump({"deny": deny, "ask": ask}, fh)
    except Exception as exc:
        sys.stderr.write(f"[guard-sync] {exc}\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
