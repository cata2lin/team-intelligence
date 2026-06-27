# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9"]
# ///
"""Generic MCP launcher — resolve secrets from the SharedClaude KB, inject them as ENV
(or as a temp credentials FILE), then exec the real MCP server. Keeps API keys / SA JSON
only in the knowledge base (never in git/plugin), exactly like pg_mcp_launch.py.

Usage:
  mcp_env_launch.py ENVVAR=SECRET_KEY [FILE:ENVVAR=SECRET_KEY] ... -- <command> [args...]

  # Postgres MCP Pro (restricted = read-mostly):
  mcp_env_launch.py DATABASE_URI=DATABASE_URL_METRICS -- uvx postgres-mcp --access-mode restricted
  # Klaviyo (private key as env):
  mcp_env_launch.py PRIVATE_API_KEY=KLAVIYO_ESTEBAN_PRIVATE_KEY -- uvx klaviyo-mcp-server
  # GA4 (SA JSON written to a temp file, path in GOOGLE_APPLICATION_CREDENTIALS):
  mcp_env_launch.py FILE:GOOGLE_APPLICATION_CREDENTIALS=GA4_SA_JSON -- uvx analytics-mcp
"""
import os, sys, tempfile

def resolve(key):
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
            sys.stderr.write(f"[mcp_env_launch] KB lookup failed ({e}); env fallback.\n")
    return os.environ.get(key)

def main():
    a = sys.argv[1:]
    if "--" not in a:
        sys.exit("mcp_env_launch: need '--' separating secrets from command")
    i = a.index("--")
    pairs, cmd = a[:i], a[i+1:]
    if not cmd:
        sys.exit("mcp_env_launch: no command after '--'")
    for p in pairs:
        spec, key = p.split("=", 1)
        if spec.startswith("LIT:"):           # literal env (not a secret), e.g. LIT:READ_ONLY=true
            os.environ[spec[4:]] = key; continue
        val = resolve(key)
        if not val:
            sys.stderr.write(f"[mcp_env_launch] missing secret {key}\n"); continue
        if spec.startswith("FILE:"):
            env = spec[5:]
            fd, path = tempfile.mkstemp(suffix=".json", prefix="mcpcred_")
            os.write(fd, val.encode()); os.close(fd); os.chmod(path, 0o600)
            os.environ[env] = path
        else:
            os.environ[spec] = val
    os.execvp(cmd[0], cmd)

if __name__ == "__main__":
    main()
