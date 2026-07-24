# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9"]
# ///
"""sqlite_ssh_mcp_launch.py — leagă LOCAL un SQLite de pe VPS ca MCP, prin SSH stdio.

De ce prin SSH: `profitability.db` (motorul de profit) e un SQLite pe VPS — nu există un MCP
remote curat pentru SQLite. Soluția: rulăm serverul MCP read-only (ro_sqlite_mcp.py, stdlib pură)
CHIAR pe VPS, cu system python3, iar transportul stdio MCP curge prin canalul SSH. Credențialele
SSH (host/user/parolă) vin din KB — niciodată în git/plugin — exact ca pg_mcp_launch.py.

Pe fiecare pornire copiază serverul pe VPS (scp) → mereu la zi, apoi exec ssh.

  sqlite_ssh_mcp_launch.py [cale_db_pe_vps]
"""
import os, sys, subprocess

REMOTE_SERVER = "/root/Scripturi/ro_sqlite_mcp.py"
DEFAULT_DB = "/root/Scripturi/data/profitability.db"
HERE = os.path.dirname(os.path.abspath(__file__))


def secret(key):
    url = os.environ.get("KB_DATABASE_URL")
    if url:
        try:
            import psycopg2
            with psycopg2.connect(url, connect_timeout=12) as c, c.cursor() as cur:
                cur.execute("SELECT value FROM secrets WHERE key=%s", (key,))
                r = cur.fetchone()
                if r and r[0]:
                    return r[0]
        except Exception as e:
            sys.stderr.write("[sqlite_ssh_mcp] KB lookup fail (%s); env fallback\n" % e)
    return os.environ.get(key)


def main():
    db = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB
    host = secret("PROFIT_SSH_HOST"); user = secret("PROFIT_SSH_USER"); pw = secret("PROFIT_SSH_PASS")
    if not (host and user and pw):
        sys.stderr.write("[sqlite_ssh_mcp] lipsesc PROFIT_SSH_HOST/USER/PASS din KB\n"); sys.exit(1)

    env = dict(os.environ, SSHPASS=pw)
    ssh_base = ["sshpass", "-e", "ssh", "-o", "StrictHostKeyChecking=no", "-o", "LogLevel=ERROR"]

    # 1) urcă serverul (mic, stdlib) pe VPS — mereu la zi
    local_server = os.path.join(HERE, "ro_sqlite_mcp.py")
    scp = subprocess.run(["sshpass", "-e", "scp", "-o", "StrictHostKeyChecking=no", "-o", "LogLevel=ERROR",
                          local_server, "%s@%s:%s" % (user, host, REMOTE_SERVER)],
                         env=env, capture_output=True, text=True)
    if scp.returncode != 0:
        sys.stderr.write("[sqlite_ssh_mcp] scp eșuat: %s\n" % scp.stderr.strip()); sys.exit(1)

    # 2) exec ssh cu serverul — stdio MCP curge prin SSH
    argv = ssh_base + ["%s@%s" % (user, host), "python3 %s %s" % (REMOTE_SERVER, db)]
    try:
        os.execvpe("sshpass", argv, env)
    except OSError as e:
        sys.stderr.write("[sqlite_ssh_mcp] exec ssh eșuat: %s (sshpass instalat?)\n" % e); sys.exit(127)


if __name__ == "__main__":
    main()
