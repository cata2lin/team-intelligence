"""
arona_pg.py — the ONE shared Postgres + secret helper for team skills.

Kills the most-duplicated code in the marketplace (the audit found `_clean_dsn`
copy-pasted in ~40 files, plus a per-skill `secret()` wrapper in most). New skills
should import this instead of re-implementing; existing skills migrate opportunistically.

Drop-in usage from any skill script:

    import sys, os
    # make core/scripts importable (walk up to the marketplace, like kb.py is found)
    from pathlib import Path
    here = Path(__file__).resolve()
    for up in range(2, 8):
        cand = here.parents[up] / "core" / "scripts"
        if (cand / "arona_pg.py").exists():
            sys.path.insert(0, str(cand)); break
    import arona_pg

    dsn = arona_pg.secret("DATABASE_URL_METRICS")     # env-first, KB fallback
    with arona_pg.connect("DATABASE_URL_METRICS") as conn:   # read-only by default
        rows = arona_pg.query(conn, "SELECT 1")

Design notes:
- `secret()` is ENV-FIRST (works on servers whose .env has the value, no uv/KB needed),
  then falls back to `kb.py secret-get` (the onboarded-workstation path). Never prints values.
- `clean_dsn()` strips Prisma-style params psycopg2 rejects (?schema=, pgbouncer, …).
- `connect()` opens a session that defaults to READ ONLY (override with readonly=False for the
  rare app-DB write, which must still follow the team rule: dry-run SELECT + confirmation).
"""
import os
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

try:
    import psycopg2
except Exception:  # pragma: no cover - psycopg2 optional until connect() is used
    psycopg2 = None

_LIBPQ_OK = {"sslmode", "sslrootcert", "sslcert", "sslkey", "connect_timeout", "application_name"}


def _kb_path():
    env = os.environ.get("KB_PATH")
    if env and Path(env).exists():
        return env
    here = Path(__file__).resolve()
    cand = here.parent / "kb.py"            # we live next to kb.py in core/scripts
    if cand.exists():
        return str(cand)
    for up in range(1, 7):
        c = here.parents[up] / "core" / "scripts" / "kb.py"
        if c.exists():
            return str(c)
    return None


def secret(key: str) -> str:
    """Fetch a secret/config value. ENV first (server .env), then the SharedClaude KB.
    Never prints the value."""
    v = os.environ.get(key)
    if v:
        return v.strip()
    kb = _kb_path()
    if not kb:
        return ""
    try:
        return subprocess.run(["uv", "run", kb, "secret-get", key],
                              capture_output=True, text=True, timeout=60).stdout.strip()
    except Exception:
        return ""


def clean_dsn(dsn: str) -> str:
    """Strip query params libpq/psycopg2 rejects (Prisma's ?schema=, pgbouncer, connection_limit…)."""
    if not dsn:
        return dsn
    u = urlsplit(dsn)
    q = [(k, v) for k, v in parse_qsl(u.query) if k in _LIBPQ_OK]
    return urlunsplit((u.scheme, u.netloc, u.path, urlencode(q), u.fragment))


def connect(key_or_dsn: str, readonly: bool = True, **kw):
    """Open a psycopg2 connection. `key_or_dsn` is a secret KEY (e.g. 'DATABASE_URL_METRICS')
    or a raw DSN. READ ONLY by default (the team default; flip readonly=False only for an
    app's own DB write after a dry-run + confirmation)."""
    if psycopg2 is None:
        raise RuntimeError("psycopg2 not installed (add psycopg2-binary to the script deps)")
    dsn = key_or_dsn
    if "://" not in (key_or_dsn or ""):
        dsn = secret(key_or_dsn)
        if not dsn:
            raise RuntimeError(f"secret {key_or_dsn!r} not found (env or KB)")
    kw.setdefault("connect_timeout", 20)
    conn = psycopg2.connect(clean_dsn(dsn), **kw)
    if readonly:
        try:
            conn.set_session(readonly=True, autocommit=True)
        except Exception:
            pass
    return conn


def query(conn, sql, params=None):
    """Run a SELECT, return list of dict rows."""
    cur = conn.cursor()
    cur.execute(sql, params or ())
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]
