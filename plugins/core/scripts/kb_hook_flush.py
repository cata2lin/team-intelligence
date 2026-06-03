# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9"]
# ///
"""Stop hook: flush this session's buffered events into the `events` table in
one batch (one DB round-trip per turn). Reads the per-session buffer written by
kb_hook_log.py. Attributes to $EMPLOYEE_HANDLE. On failure, leaves the buffer so
the next turn retries (no partial inserts -> nothing is double-logged). Exit 0.
"""
import datetime
import json
import os
import sys
import tempfile

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}
    sid = data.get("session_id", "nosession")
    buf = os.path.join(tempfile.gettempdir(), f"claude_kb_{sid}.jsonl")
    if not os.path.exists(buf):
        sys.exit(0)
    try:
        recs = [json.loads(l) for l in open(buf, encoding="utf-8") if l.strip()]
    except Exception:
        recs = []
    if not recs:
        try:
            os.remove(buf)
        except OSError:
            pass
        sys.exit(0)

    url = os.environ.get("KB_DATABASE_URL")
    handle = (os.environ.get("EMPLOYEE_HANDLE") or "").lower().strip()
    if not url:
        sys.exit(0)  # cannot flush now; buffer kept for a later turn
    try:
        import psycopg2
        with psycopg2.connect(url, connect_timeout=12) as conn, conn.cursor() as cur:
            emp = None
            if handle:
                cur.execute("SELECT id FROM employees WHERE handle=%s", (handle,))
                row = cur.fetchone()
                emp = row[0] if row else None
            for rec in recs:
                ts = datetime.datetime.fromtimestamp(rec.get("ts") or 0)
                cur.execute(
                    """INSERT INTO events (employee_id, session_uid, entity_type, entity_name, action, summary, occurred_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    (emp, sid, rec.get("type", "tool"), rec.get("name"),
                     rec.get("action", "used"), rec.get("summary"), ts),
                )
        os.remove(buf)  # only on a clean commit (the `with` committed)
    except Exception as exc:
        sys.stderr.write(f"[kb-flush] {exc}\n")  # keep buffer; retry next turn
    sys.exit(0)


if __name__ == "__main__":
    main()
