# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9"]
# ///
"""Interactive onboarding walkthrough for the Arona intelligence center.

Run once after cloning the repo (the install.sh / install.ps1 bootstrap launches
this). Cross-platform (macOS + Windows). It asks who you are and your database
connection, then configures Claude Code GLOBALLY so the team skills, the
SharedClaude knowledge base, and the NAS work in EVERY project from then on.

The ONLY things you provide: the database host/IP, user, password, and name.
Everything else (all secrets and API keys) is read from that database.

Flow:
  1. Collect DB host / port / user / password / name -> assemble + TEST the URL.
  2. Pick which employee you are (the list is pulled live from the database).
  3. (Optional) NAS mount path for shared files.
  4. Save it locally: ~/.claude/settings.json env (KB_DATABASE_URL, EMPLOYEE_HANDLE,
     NAS_ROOT, TEAM_REPO) -- applies to every project for this user.
  5. Enable the team marketplace + every plugin at USER scope (settings.json),
     and @import the shared procedures into the global ~/.claude/CLAUDE.md.
  6. Register this machine in the knowledge base.

Re-runnable: running it again just updates your configuration.
"""
import argparse
import getpass
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import urllib.parse

# Force UTF-8 output so messages render on any console (Windows cp1252 included).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import configure  # noqa: E402  reuse set_env_vars / set_plugins / ensure_import / claude_home

MARKETPLACE_REPO = "cata2lin/team-intelligence"   # GitHub owner/repo (the marketplace source)
MARKETPLACE_NAME = "team-intelligence"            # name in .claude-plugin/marketplace.json


def _ask(label, default=None, secret=False):
    suffix = f" [{default}]" if default else ""
    reader = getpass.getpass if secret else input
    while True:
        try:
            val = reader(f"{label}{suffix}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\naborted.")
            sys.exit(1)
        if not val and default is not None:
            return default
        if val:
            return val
        print("  (required)")


def build_url(host, user, password, dbname, port="5432"):
    u = urllib.parse.quote(user, safe="")
    p = urllib.parse.quote(password, safe="")
    return f"postgresql://{u}:{p}@{host}:{port}/{dbname}"


def test_connection(url):
    import psycopg2
    with psycopg2.connect(url, connect_timeout=12) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")
        cur.fetchone()


def list_employees(url):
    import psycopg2
    with psycopg2.connect(url, connect_timeout=12) as conn, conn.cursor() as cur:
        cur.execute("SELECT handle, name FROM employees WHERE active ORDER BY id")
        return cur.fetchall()


def register_machine(url, handle, nas, repo):
    import psycopg2
    host = socket.gethostname()
    with psycopg2.connect(url, connect_timeout=12) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM employees WHERE handle=%s", (handle,))
        row = cur.fetchone()
        if not row:
            raise RuntimeError(f"employee '{handle}' no longer exists in the database")
        emp = row[0]
        cur.execute(
            """INSERT INTO machines (employee_id, hostname, os, nas_mount, agent_path, last_seen_at)
               VALUES (%s,%s,%s,%s,%s,now())
               ON CONFLICT (employee_id, hostname) DO UPDATE SET
                   os=EXCLUDED.os, nas_mount=EXCLUDED.nas_mount,
                   agent_path=EXCLUDED.agent_path, last_seen_at=now()
               RETURNING id""",
            (emp, host, sys.platform, nas or None, repo),
        )
        mid = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO events (employee_id, machine_id, entity_type, entity_name, action, summary)
               VALUES (%s,%s,'session',%s,'onboarded',%s)""",
            (emp, mid, host, f"{handle} onboarded on {host} ({sys.platform})"),
        )
    return emp


def store_nas_credentials(url, handle, username, password):
    import psycopg2
    with psycopg2.connect(url, connect_timeout=12) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM employees WHERE handle=%s", (handle,))
        emp = cur.fetchone()[0]
        cur.execute("""INSERT INTO nas_credentials (employee_id, username, password, updated_at)
                       VALUES (%s,%s,%s,now())
                       ON CONFLICT (employee_id) DO UPDATE SET
                           username=EXCLUDED.username, password=EXCLUDED.password, updated_at=now()""",
                    (emp, username, password))
        cur.execute("INSERT INTO events (employee_id, entity_type, entity_name, action, summary) "
                    "VALUES (%s,'secret','nas_credentials','set',%s)", (emp, f"stored NAS login for {handle}"))


def nas_connect(url, handle):
    """Run nas_connect.py (authenticate + ensure folder) and return the NAS_ROOT it prints."""
    script = os.path.join(REPO, "plugins", "core", "scripts", "nas_connect.py")
    env = {**os.environ, "KB_DATABASE_URL": url, "EMPLOYEE_HANDLE": handle}
    uv = shutil.which("uv") or "uv"
    try:
        r = subprocess.run([uv, "run", "--no-project", script], env=env,
                           capture_output=True, text=True, timeout=120)
    except Exception as exc:
        sys.stderr.write(f"   [nas] connect failed: {exc}\n")
        return ""
    lines = [l for l in (r.stdout or "").splitlines() if l.strip()]
    if r.returncode == 0 and lines:
        return lines[-1].strip()
    sys.stderr.write((r.stderr or "")[:300])
    return ""


def marketplace_plugins():
    with open(os.path.join(REPO, ".claude-plugin", "marketplace.json"), encoding="utf-8") as fh:
        return [p["name"] for p in json.load(fh)["plugins"]]


def db_current_user(url):
    import psycopg2
    try:
        with psycopg2.connect(url, connect_timeout=12) as conn, conn.cursor() as cur:
            cur.execute("SELECT current_user")
            return (cur.fetchone()[0] or "").lower()
    except Exception:
        return ""


def _run(cmd, **kw):
    print("   $", " ".join(cmd))
    return subprocess.run(cmd, **kw)


def main():
    ap = argparse.ArgumentParser(description="Arona intelligence center onboarding")
    ap.add_argument("--employee"); ap.add_argument("--db-host"); ap.add_argument("--db-port", default=None)
    ap.add_argument("--db-user"); ap.add_argument("--db-pass"); ap.add_argument("--db-name", default=None)
    ap.add_argument("--nas-root", default="")
    ap.add_argument("--nas-user", default=""); ap.add_argument("--nas-pass", default="")
    ap.add_argument("--non-interactive", action="store_true", help="use flags instead of prompts")
    ap.add_argument("--skip-plugins", action="store_true", help="config only; don't touch the claude CLI")
    ap.add_argument("--home", help="override config dir (testing)")
    a = ap.parse_args()
    interactive = not a.non_interactive

    print("\n=== Arona intelligence center -- onboarding ===")
    print("(the only thing you need: your database host/IP, user, and password)\n")

    # 1) DB connection -- only host/user/password; everything else is pulled from the DB
    if interactive:
        host = a.db_host or _ask("Database host / IP")
        user = a.db_user or _ask("Database user")
        pw   = a.db_pass or _ask("Database password", secret=True)
        port = a.db_port or "5432"
        name = a.db_name or "SharedClaude"
    else:
        host, user, pw = a.db_host, a.db_user, a.db_pass
        port = a.db_port or "5432"
        name = a.db_name or "SharedClaude"
        if not all([host, user, pw, name]):
            sys.exit("non-interactive mode needs --db-host --db-user --db-pass --db-name")

    url = build_url(host, user, pw, name, port)
    print("\n   testing connection...")
    try:
        test_connection(url)
        print("   [ok] connected to the knowledge base")
    except Exception as exc:
        print(f"   [ERROR] could not connect: {exc}")
        sys.exit(1)

    # 2) identity -- resolved automatically from your database login (DB role == handle)
    employees = list_employees(url)
    known = {h for h, _ in employees}
    dbuser = db_current_user(url)
    if a.employee:
        handle = a.employee.lower().strip()
        if handle not in known:
            sys.exit(f"'{handle}' is not a known employee handle: {sorted(known)}")
    elif dbuser in known:
        handle = dbuser
        print(f"   -> identified you as '{handle}' from your database login")
    elif interactive:
        print("\nYour database login isn't tied to an employee. Which one are you?")
        for i, (h, n) in enumerate(employees, 1):
            print(f"   {i}. {n}  ({h})")
        while True:
            sel = input("   number: ").strip()
            if sel.isdigit() and 1 <= int(sel) <= len(employees):
                handle = employees[int(sel) - 1][0]
                break
            print("   pick a valid number")
    else:
        sys.exit("could not identify the employee (DB user not mapped); pass --employee")
    print(f"   -> you are: {handle}")

    # 3) NAS -- pulled from the DB (admin stores each employee's login once via kb.py nas-set)
    nas = a.nas_root  # explicit path override, if given
    if not nas and a.nas_user and a.nas_pass:   # optional: set my own NAS login during onboarding
        store_nas_credentials(url, handle, a.nas_user, a.nas_pass)
    if not nas:
        nas = nas_connect(url, handle)   # reads my stored NAS login from the DB
        if nas:
            print(f"   [ok] NAS connected: {nas}")
        else:
            print("   [i] no NAS login stored for you yet (ask the admin: kb.py nas-set). NAS skipped for now.")

    # 4-5) save config + enable plugins (global / user scope -> every project)
    home = a.home or configure.claude_home()
    os.makedirs(home, exist_ok=True)
    settings_path = os.path.join(home, "settings.json")
    claude_md_path = os.path.join(home, "CLAUDE.md")
    env = {"KB_DATABASE_URL": url, "EMPLOYEE_HANDLE": handle, "TEAM_REPO": REPO}
    if nas:
        env["NAS_ROOT"] = nas
    configure.set_env_vars(settings_path, env)
    plugins = marketplace_plugins()
    configure.set_plugins(settings_path, MARKETPLACE_REPO, MARKETPLACE_NAME, plugins)
    shared = os.path.join(REPO, "shared", "CLAUDE.team.md").replace(os.sep, "/")
    configure.ensure_import(claude_md_path, f"@{shared}")
    print(f"\n   [ok] saved {settings_path}")
    print(f"        env: KB_DATABASE_URL, EMPLOYEE_HANDLE{', NAS_ROOT' if nas else ''}, TEAM_REPO")
    print(f"        enabled (user scope, every project): {', '.join(plugins)}")
    print(f"   [ok] global procedures @import added to {claude_md_path}")

    # best-effort: activate immediately if the claude CLI is available (not required)
    npx_ok = shutil.which("npx") is not None
    if not a.skip_plugins:
        cli = shutil.which("claude")
        if cli:
            print("\n   activating now via the claude CLI:")
            _run([cli, "plugin", "marketplace", "add", MARKETPLACE_REPO], check=False)
            for p in plugins:
                _run([cli, "plugin", "install", f"{p}@{MARKETPLACE_NAME}", "--scope", "user"], check=False)
        else:
            print("\n   (claude CLI not on PATH -- the plugins are enabled in settings.json and")
            print("    will load when you start Claude Code; no further action needed.)")
    if not npx_ok:
        print("\n   [!] Node.js / npx not found -- install it (https://nodejs.org) so the 5")
        print("       read-only Postgres MCP servers can start. The DB/KB still work without it.")

    # 6) register this machine
    try:
        register_machine(url, handle, nas, REPO)
        print("\n   [ok] machine registered in the knowledge base")
    except Exception as exc:
        print(f"\n   (could not register machine: {exc})")

    print("\n=== Done ===")
    print(f"You are '{handle}'.  Start (or restart) Claude Code.")
    print("From now on, in EVERY project you have: the team skills, the knowledge")
    print("base, the secret store, and the NAS" + (", and the 5 read-only Postgres MCP servers." if npx_ok else "."))
    print("All secrets are read from the database -- nothing to configure per project.")
    if not nas:
        print("\nTip: re-run this onboarding to add your NAS login once you have it.")


if __name__ == "__main__":
    main()
