# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9"]
# ///
"""kb_env — load team secrets from the SharedClaude `secrets` table into the
process environment.

Import-and-call at the top of any script that used to depend on a .env file:

    from kb_env import load_secrets_into_env
    load_secrets_into_env()          # now os.environ has DATABASE_URL_*, API keys, ...

Reads $KB_DATABASE_URL (the bootstrap). No-ops quietly if the KB is unreachable
so scripts can still fall back to a pre-populated environment. Never prints
values.
"""
import os
import sys


def load_secrets_into_env(keys=None, overwrite=False):
    """Populate os.environ from the secret store. Returns the dict that was set.

    keys: optional iterable of specific keys to load (default: all non-empty).
    overwrite: replace existing env vars (default: keep what's already set).
    """
    url = os.environ.get("KB_DATABASE_URL")
    if not url:
        sys.stderr.write("[kb_env] KB_DATABASE_URL not set; relying on existing environment.\n")
        return {}
    try:
        import psycopg2
    except ImportError:
        sys.stderr.write("[kb_env] psycopg2 not available; relying on existing environment.\n")
        return {}

    out = {}
    try:
        with psycopg2.connect(url, connect_timeout=12) as conn, conn.cursor() as cur:
            if keys:
                cur.execute("SELECT key, value FROM secrets WHERE key = ANY(%s)", (list(keys),))
            else:
                cur.execute("SELECT key, value FROM secrets WHERE value IS NOT NULL AND value <> ''")
            for key, value in cur.fetchall():
                if value is None:
                    continue
                if overwrite or key not in os.environ:
                    os.environ[key] = value
                out[key] = value
    except Exception as exc:  # KB down — degrade to whatever is already in env
        sys.stderr.write(f"[kb_env] could not load secrets ({exc}); relying on existing environment.\n")
    return out
