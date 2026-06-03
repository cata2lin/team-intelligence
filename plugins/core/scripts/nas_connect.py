# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9"]
# ///
"""Connect this machine to the team NAS using the logged-in employee's stored
SMB credentials, and make sure their ClaudeShared/<handle> folder exists.

Reads the team NAS config (NAS_HOST / NAS_SHARE / NAS_BASE) and the employee's
personal NAS login from the SharedClaude DB (via $KB_DATABASE_URL +
$EMPLOYEE_HANDLE). Cross-platform and idempotent:

  - Windows: stores the credential in Credential Manager (`cmdkey`) so UNC access
    to \\host\share\... works persistently and non-interactively.
    NAS_ROOT = \\host\share\base\handle
  - macOS:   mounts smb://host/share at ~/nas/share via mount_smbfs.
    NAS_ROOT = ~/nas/share/base/handle

Prints the resolved NAS_ROOT as its LAST stdout line (onboarding captures it).
Best-effort: writes warnings to stderr but still prints the path so callers can
proceed; only fails hard if no login is stored.
"""
import os
import subprocess
import sys
import urllib.parse

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass


def fetch():
    import psycopg2
    url = os.environ.get("KB_DATABASE_URL")
    handle = (os.environ.get("EMPLOYEE_HANDLE") or "").lower().strip()
    if not url or not handle:
        sys.stderr.write("[nas] KB_DATABASE_URL or EMPLOYEE_HANDLE not set.\n")
        sys.exit(3)
    with psycopg2.connect(url, connect_timeout=12) as conn, conn.cursor() as cur:
        cur.execute("SELECT key, value FROM secrets WHERE key IN ('NAS_HOST','NAS_SHARE','NAS_BASE')")
        cfg = dict(cur.fetchall())
        cur.execute("""SELECT n.username, n.password FROM nas_credentials n
                       JOIN employees e ON e.id = n.employee_id WHERE e.handle=%s""", (handle,))
        row = cur.fetchone()
    return cfg, handle, (row[0] if row else None), (row[1] if row else None)


def ensure_dirs(root, sep):
    for sub in ("", "data", "exports"):
        p = root if not sub else root + sep + sub
        try:
            os.makedirs(p, exist_ok=True)
        except OSError as exc:
            sys.stderr.write(f"[nas] could not create {p}: {exc}\n")


def main():
    cfg, handle, user, pw = fetch()
    host = cfg.get("NAS_HOST"); share = cfg.get("NAS_SHARE"); base = cfg.get("NAS_BASE") or "ClaudeShared"
    if not host or not share:
        sys.stderr.write("[nas] NAS_HOST/NAS_SHARE not configured in the DB.\n")
        sys.exit(1)
    if not user or not pw:
        sys.stderr.write(f"[nas] no NAS login stored for '{handle}'. Re-run onboarding to add it.\n")
        sys.exit(1)

    if os.name == "nt":
        # Persist the credential so UNC access is non-interactive across reboots.
        subprocess.run(["cmdkey", f"/add:{host}", f"/user:{user}", f"/pass:{pw}"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        root = f"\\\\{host}\\{share}\\{base}\\{handle}"
        ensure_dirs(root, "\\")
    else:
        mount = os.path.expanduser(f"~/nas/{share}")
        os.makedirs(mount, exist_ok=True)
        if not os.path.ismount(mount):
            u = urllib.parse.quote(user, safe=""); p = urllib.parse.quote(pw, safe="")
            if sys.platform == "darwin":
                subprocess.run(["mount_smbfs", f"//{u}:{p}@{host}/{share}", mount],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:  # linux
                subprocess.run(["mount", "-t", "cifs", f"//{host}/{share}", mount,
                                "-o", f"username={user},password={pw}"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        root = os.path.join(mount, base, handle)
        ensure_dirs(root, "/")

    print(root)


if __name__ == "__main__":
    main()
