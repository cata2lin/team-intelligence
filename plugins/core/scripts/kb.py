# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9"]
# ///
"""kb.py — interface to the SharedClaude knowledge base.

Bootstrap: reads the SharedClaude connection string from $KB_DATABASE_URL.
Identity:  attributes activity to $EMPLOYEE_HANDLE + this machine's hostname.

The whole team's knowledge lives in this DB: activity/usage log, the secret
store, the file registry (NAS + local), the skill registry, and reference links.
Skills, hooks, and the agent call this to read and to record.

CLI examples:
  kb.py log --type skill --action used --name core:query-postgres
  kb.py secret-get DATABASE_URL_METRICS
  kb.py secret-set OPENAI_API_KEY 'sk-...' --service openai
  kb.py file-add --location nas --path "$NAS_ROOT/data/x.xlsx" --category xlsx --action created
  kb.py resource-add --category url --label foo --value https://example.com
  kb.py skill-register --plugin core --name query-postgres --author Arona
  kb.py recent --limit 20
  kb.py whoami
"""
import argparse
import json
import os
import socket
import sys

import psycopg2

# Force UTF-8 output so messages render on any console (Windows cp1252 included).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _conn():
    url = os.environ.get("KB_DATABASE_URL")
    if not url:
        sys.stderr.write("[kb] KB_DATABASE_URL not set - cannot reach the knowledge base.\n")
        sys.exit(3)
    return psycopg2.connect(url, connect_timeout=12)


def _employee_id(cur, handle=None):
    handle = (handle or os.environ.get("EMPLOYEE_HANDLE") or "").lower().strip()
    if not handle:
        return None
    cur.execute("SELECT id FROM employees WHERE handle=%s", (handle,))
    r = cur.fetchone()
    return r[0] if r else None


def _machine_id(cur, emp_id):
    host = socket.gethostname()
    cur.execute(
        "SELECT id FROM machines WHERE hostname=%s ORDER BY (employee_id=%s) DESC NULLS LAST LIMIT 1",
        (host, emp_id),
    )
    r = cur.fetchone()
    if r:
        cur.execute("UPDATE machines SET last_seen_at=now() WHERE id=%s", (r[0],))
        return r[0]
    cur.execute(
        """INSERT INTO machines (employee_id, hostname, os, nas_mount, agent_path, last_seen_at)
           VALUES (%s,%s,%s,%s,%s,now()) RETURNING id""",
        (emp_id, host, sys.platform, os.environ.get("NAS_ROOT"), os.environ.get("TEAM_REPO")),
    )
    return cur.fetchone()[0]


def cmd_log(a):
    details = json.loads(a.details) if a.details else None
    with _conn() as conn, conn.cursor() as cur:
        emp = _employee_id(cur, a.employee)
        mac = _machine_id(cur, emp)
        cur.execute(
            """INSERT INTO events (employee_id, machine_id, session_uid, entity_type,
                   entity_id, entity_name, action, summary, details)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (emp, mac, a.session or os.environ.get("CLAUDE_SESSION_ID"),
             a.type, a.id, a.name, a.action, a.summary,
             json.dumps(details) if details is not None else None),
        )
        print("logged event", cur.fetchone()[0])


def cmd_secret_get(a):
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT value FROM secrets WHERE key=%s", (a.key,))
        r = cur.fetchone()
        if not r or r[0] is None or r[0] == "":
            sys.stderr.write(f"[kb] secret '{a.key}' is not set\n")
            sys.exit(1)
        sys.stdout.write(r[0])  # raw value, no trailing newline — safe to pipe


def cmd_secret_set(a):
    with _conn() as conn, conn.cursor() as cur:
        emp = _employee_id(cur, a.employee)
        cur.execute(
            """INSERT INTO secrets (key, value, service, kind, is_sensitive, created_by, updated_by, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,now())
               ON CONFLICT (key) DO UPDATE SET
                   value=EXCLUDED.value,
                   service=COALESCE(EXCLUDED.service, secrets.service),
                   updated_by=EXCLUDED.updated_by, updated_at=now(),
                   rotated_at=CASE WHEN secrets.value IS DISTINCT FROM EXCLUDED.value
                                   THEN now() ELSE secrets.rotated_at END""",
            (a.key, a.value, a.service, a.kind, not a.config, emp, emp),
        )
        cur.execute(
            "INSERT INTO events (employee_id, entity_type, entity_name, action, summary) "
            "VALUES (%s,'secret',%s,'set',%s)",
            (emp, a.key, f"secret {a.key} set/updated"),
        )
        print(f"secret '{a.key}' set")


def cmd_secret_list(a):
    with _conn() as conn, conn.cursor() as cur:
        if a.service:
            cur.execute("SELECT key, service, kind, (value IS NOT NULL AND value<>'') "
                        "FROM secrets WHERE service=%s ORDER BY key", (a.service,))
        else:
            cur.execute("SELECT key, service, kind, (value IS NOT NULL AND value<>'') "
                        "FROM secrets ORDER BY service, key")
        for key, service, kind, has in cur.fetchall():
            print(f"{'set  ' if has else 'EMPTY'} {kind:6} {(service or '-'):12} {key}")


def cmd_skill_register(a):
    with _conn() as conn, conn.cursor() as cur:
        emp = _employee_id(cur, a.employee)
        author = _employee_id(cur, a.author) if a.author else None
        cur.execute(
            """INSERT INTO skills (plugin,name,author_employee_id,description,version,file_path,created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (plugin,name) DO UPDATE SET
                   description=COALESCE(EXCLUDED.description, skills.description),
                   version=COALESCE(EXCLUDED.version, skills.version),
                   file_path=COALESCE(EXCLUDED.file_path, skills.file_path),
                   author_employee_id=COALESCE(EXCLUDED.author_employee_id, skills.author_employee_id),
                   updated_at=now()
               RETURNING id, (xmax=0)""",
            (a.plugin, a.name, author, a.description, a.version, a.path, emp),
        )
        sid, created = cur.fetchone()
        cur.execute(
            """INSERT INTO events (employee_id, entity_type, entity_id, entity_name, action, summary)
               VALUES (%s,'skill',%s,%s,%s,%s)""",
            (emp, sid, f"{a.plugin}:{a.name}", "created" if created else "modified",
             f"skill {a.plugin}:{a.name} {'registered' if created else 'updated'}"),
        )
        print(f"skill {a.plugin}:{a.name} {'registered' if created else 'updated'} (id {sid})")


def cmd_file_add(a):
    with _conn() as conn, conn.cursor() as cur:
        emp = _employee_id(cur, a.employee)
        mac = _machine_id(cur, emp) if a.location == "local" else None
        cur.execute(
            """INSERT INTO files (location,path,machine_id,name,category,size_bytes,description,source,created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (location, COALESCE(machine_id,0), path) DO UPDATE SET
                   updated_at=now(),
                   description=COALESCE(EXCLUDED.description, files.description),
                   category=COALESCE(EXCLUDED.category, files.category)
               RETURNING id, (xmax=0)""",
            (a.location, a.path, mac, a.name or os.path.basename(a.path),
             a.category, a.size, a.description, a.source, emp),
        )
        fid, created = cur.fetchone()
        action = a.action or ("created" if created else "modified")
        cur.execute(
            """INSERT INTO events (employee_id, machine_id, entity_type, entity_id, entity_name, action, summary, details)
               VALUES (%s,%s,'file',%s,%s,%s,%s,%s)""",
            (emp, mac, fid, a.path, action, f"file {action}: {a.path}",
             json.dumps({"source": a.source}) if a.source else None),
        )
        print(f"file {action}: {a.path} (id {fid})")


def cmd_resource_add(a):
    with _conn() as conn, conn.cursor() as cur:
        emp = _employee_id(cur, a.employee)
        cur.execute(
            """INSERT INTO resources (category,label,value,service,description,created_by)
               VALUES (%s,%s,%s,%s,%s,%s)
               ON CONFLICT (category,label) DO UPDATE SET
                   value=EXCLUDED.value,
                   description=COALESCE(EXCLUDED.description, resources.description),
                   updated_at=now()
               RETURNING id""",
            (a.category, a.label, a.value, a.service, a.description, emp),
        )
        rid = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO events (employee_id, entity_type, entity_id, entity_name, action, summary) "
            "VALUES (%s,'resource',%s,%s,'added',%s)",
            (emp, rid, a.label, f"resource {a.category}/{a.label} added"),
        )
        print(f"resource {a.category}/{a.label} (id {rid})")


def cmd_recent(a):
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT occurred_at, who, entity_type, entity_name, action, summary "
                    "FROM v_recent_activity LIMIT %s", (a.limit,))
        for ts, who, et, en, act, summ in cur.fetchall():
            print(f"{ts:%Y-%m-%d %H:%M} {(who or '-'):10} {et:8} {act:10} {(en or ''):28} {summ or ''}")


def cmd_whoami(a):
    with _conn() as conn, conn.cursor() as cur:
        emp = _employee_id(cur)
        print("employee:", os.environ.get("EMPLOYEE_HANDLE") or "(unset)",
              "| id:", emp, "| host:", socket.gethostname())


def main():
    p = argparse.ArgumentParser(prog="kb", description="SharedClaude knowledge base interface")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("log", help="record an activity/usage/change event")
    s.add_argument("--type", required=True, help="skill | file | secret | resource | session | claude_md | other")
    s.add_argument("--action", required=True, help="used | created | modified | removed | ported_in | ...")
    s.add_argument("--name"); s.add_argument("--id", type=int); s.add_argument("--summary")
    s.add_argument("--details", help="JSON string"); s.add_argument("--employee"); s.add_argument("--session")
    s.set_defaults(fn=cmd_log)

    s = sub.add_parser("secret-get", help="print a secret value (no newline)")
    s.add_argument("key"); s.set_defaults(fn=cmd_secret_get)
    s = sub.add_parser("secret-set", help="set/rotate a secret value")
    s.add_argument("key"); s.add_argument("value"); s.add_argument("--service")
    s.add_argument("--kind", default="secret"); s.add_argument("--config", action="store_true")
    s.add_argument("--employee"); s.set_defaults(fn=cmd_secret_set)
    s = sub.add_parser("secret-list", help="list secret keys (never values)")
    s.add_argument("--service"); s.set_defaults(fn=cmd_secret_list)

    s = sub.add_parser("skill-register", help="register/update a skill in the catalog")
    s.add_argument("--plugin", required=True); s.add_argument("--name", required=True)
    s.add_argument("--author"); s.add_argument("--description"); s.add_argument("--version")
    s.add_argument("--path"); s.add_argument("--employee"); s.set_defaults(fn=cmd_skill_register)

    s = sub.add_parser("file-add", help="register a file (NAS or local) + log the event")
    s.add_argument("--location", required=True, choices=["nas", "local", "repo", "external"])
    s.add_argument("--path", required=True); s.add_argument("--name"); s.add_argument("--category")
    s.add_argument("--size", type=int); s.add_argument("--description"); s.add_argument("--source")
    s.add_argument("--action"); s.add_argument("--employee"); s.set_defaults(fn=cmd_file_add)

    s = sub.add_parser("resource-add", help="add an IP / URL / doc / link")
    s.add_argument("--category", required=True); s.add_argument("--label", required=True)
    s.add_argument("--value", required=True); s.add_argument("--service"); s.add_argument("--description")
    s.add_argument("--employee"); s.set_defaults(fn=cmd_resource_add)

    s = sub.add_parser("recent", help="show recent activity"); s.add_argument("--limit", type=int, default=20)
    s.set_defaults(fn=cmd_recent)
    s = sub.add_parser("whoami"); s.set_defaults(fn=cmd_whoami)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
