"""
arona_pg.py — the ONE shared Postgres + secret helper for team skills.

Kills the most-duplicated code in the marketplace (the audit found `_clean_dsn`
copy-pasted in ~40 files, plus a per-skill `secret()` wrapper in most). New skills
should import this instead of re-implementing; existing skills migrate opportunistically.

Drop-in usage from any skill script — RETETA CANONICA de bootstrap (copiaz-o ca atare;
varianta veche `for up in range(2, 8): here.parents[up]` era o bomba: index fix pe `parents`
=> IndexError pe fisiere putin adanci, si nu cunostea layout-ul plugin-cache
`core/<commit12>/scripts`, deci orice import gigi->core murea in instalarea LIVE):

    # core/scripts in orice layout (clona repo, marketplace, plugin-cache core/<commit>/scripts).
    # GARDA: iteram parents (fara index fix) si preferam core-ul din ACELASI commit.
    def _core_scripts(need="arona_pg.py"):
        from pathlib import Path
        import os
        h = Path(__file__).resolve()
        c = [Path(os.environ["ARONA_CORE_SCRIPTS"])] if os.environ.get("ARONA_CORE_SCRIPTS") else []
        for up in h.parents:
            c += [up / "core" / "scripts", up / "plugins" / "core" / "scripts"] + \
                 (sorted((up / "core").glob("*/scripts")) if (up / "core").is_dir() else [])
        ok = [x for x in c if (x / need).exists()]
        return next((x for x in ok if x.parent.name in h.parts), ok[0] if ok else None)

    import sys
    _cs = _core_scripts()
    if _cs is None:
        sys.exit("core/scripts (arona_pg.py) negasit — actualizeaza plugin-urile "
                 "sau seteaza ARONA_CORE_SCRIPTS=/cale/spre/plugins/core/scripts")
    sys.path.insert(0, str(_cs))
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


def find_core_scripts(need="arona_pg.py", start=None):
    """Gaseste directorul `core/scripts` in ORICE layout de instalare, cu garda pe parents.

    Trei layout-uri reale, toate vazute in productie:
      - clona repo / marketplace : <root>/plugins/core/scripts  sau  <root>/core/scripts
      - plugin-cache Claude Code : ~/.claude/plugins/cache/team-intelligence/core/<commit12>/scripts
        (nivelul de commit in plus e motivul pentru care mergea in repo si murea in uz real)
    `ARONA_CORE_SCRIPTS` bate totul. Cand skill-ul apelant vine din plugin-cache, preferam
    core-ul din ACELASI commit — altfel legam cod nou de un helper vechi (drift tacut, mai rau
    decat un crash zgomotos). Intoarce Path sau None; apelantul da eroarea explicita.
    """
    here = Path(start or __file__).resolve()
    cands = []
    env = os.environ.get("ARONA_CORE_SCRIPTS")
    if env:
        cands.append(Path(env))
    for up in here.parents:                      # GARDA: iteram parents, fara index fix
        cands += [up / "core" / "scripts", up / "plugins" / "core" / "scripts"]
        if (up / "core").is_dir():
            cands += sorted((up / "core").glob("*/scripts"))
    ok = [c for c in cands if (c / need).exists()]
    return next((c for c in ok if c.parent.name in here.parts), ok[0] if ok else None)


def _kb_path():
    env = os.environ.get("KB_PATH")
    if env and Path(env).exists():
        return env
    here = Path(__file__).resolve()
    cand = here.parent / "kb.py"            # we live next to kb.py in core/scripts
    if cand.exists():
        return str(cand)
    c = find_core_scripts("kb.py")
    return str(c / "kb.py") if c else None


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
