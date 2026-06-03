# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9"]
# ///
"""Launch the read-only Postgres MCP server for one database.

The connection string is read from the SharedClaude `secrets` table (via
$KB_DATABASE_URL), so app credentials live only in the knowledge base — never
in git, never on the NAS, never in this plugin. If the KB is unreachable, falls
back to a same-named environment variable.

The official @modelcontextprotocol/server-postgres runs every query inside a
READ ONLY transaction — the "Postgres read-only by default" guarantee.

Usage:
    pg_mcp_launch.py <SECRET_KEY>        # e.g. DATABASE_URL_METRICS
"""
import os
import shutil
import subprocess
import sys


def resolve(key: str):
    url = os.environ.get("KB_DATABASE_URL")
    if url:
        try:
            import psycopg2

            with psycopg2.connect(url, connect_timeout=12) as conn, conn.cursor() as cur:
                cur.execute("SELECT value FROM secrets WHERE key=%s", (key,))
                row = cur.fetchone()
                if row and row[0]:
                    return row[0]
        except Exception as exc:  # KB down / network — degrade to env
            sys.stderr.write(f"[pg_mcp_launch] KB lookup failed ({exc}); trying env fallback.\n")
    return os.environ.get(key)


def main() -> None:
    if len(sys.argv) < 2:
        sys.stderr.write("usage: pg_mcp_launch.py <SECRET_KEY>\n")
        sys.exit(2)
    key = sys.argv[1]

    url = resolve(key)
    if not url:
        sys.stderr.write(
            f"[pg_mcp_launch] {key} not found in SharedClaude.secrets or the environment. "
            f"Is KB_DATABASE_URL set and the secret populated?\n"
        )
        sys.exit(1)

    npx = shutil.which("npx")
    if not npx:
        sys.stderr.write("[pg_mcp_launch] npx not found on PATH; install Node.js.\n")
        sys.exit(127)

    argv = [npx, "-y", "@modelcontextprotocol/server-postgres", url]
    try:
        if os.name == "posix":
            os.execvp(npx, argv)                 # true exec; stdio transport preserved
        else:
            sys.exit(subprocess.run(argv).returncode)   # Windows: child inherits stdio
    except OSError as exc:
        sys.stderr.write(f"[pg_mcp_launch] failed to launch npx: {exc}\n")
        sys.exit(127)


if __name__ == "__main__":
    main()
