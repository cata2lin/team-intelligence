# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
dbq.py — interoghează READ-ONLY bazele SQLite locale din `Scripturi/data/`.
Deschide mereu read-only (WAL → fallback `immutable=1`), REFUZĂ orice non-SELECT, și nu atinge
niciodată baza vie de profit (aia e pe VPS via MCP `sqlite-profitability`).

  uv run dbq.py list                              # bazele + mărime + nr tabele
  uv run dbq.py tables --db richpanel_tickets     # tabelele + nr rânduri
  uv run dbq.py query  --db marketing --sql "select platform,count(*) from marketing_facebook_raw"
"""
import argparse, glob, os, sqlite3, sys

for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass

# baze cu SECRETE — nu le dumpa
SECRET_DBS = {"shopify_tokens.db"}
ALLOWED = ("select", "with", "pragma", "explain")


def base_dir(a):
    if a.dir:
        return a.dir
    for c in ("data", os.path.join(os.path.expanduser("~"), "Downloads", "Scripturi", "data")):
        if os.path.isdir(c):
            return c
    sys.exit("Nu găsesc folderul `data/`. Dă --dir /cale/către/data")


def resolve(a):
    d = base_dir(a)
    name = a.db if a.db.endswith(".db") else a.db + ".db"
    p = a.db if os.path.isfile(a.db) else os.path.join(d, name)
    if not os.path.isfile(p):
        sys.exit(f"Baza {a.db!r} nu există în {d}")
    return p


def connect_ro(path):
    """Read-only; pt WAL care refuză mode=ro → immutable=1 (copii locale statice = sigur)."""
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        con.execute("select 1 from sqlite_master limit 1")
        return con
    except sqlite3.OperationalError:
        return sqlite3.connect(f"file:{path}?immutable=1", uri=True)


def tables_of(con):
    return [r[0] for r in con.execute(
        "select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name")]


def cmd_list(a):
    d = base_dir(a)
    print(f"baze SQLite în {d}:")
    for p in sorted(glob.glob(os.path.join(d, "*.db"))):
        sz = os.path.getsize(p)
        if sz == 0:
            print(f"  {os.path.basename(p):26} 0B (goală)"); continue
        try:
            con = connect_ro(p); n = len(tables_of(con)); con.close()
        except Exception as e:
            n = f"? ({str(e)[:30]})"
        tag = "  🔒 SECRETE" if os.path.basename(p) in SECRET_DBS else ""
        print(f"  {os.path.basename(p):26} {sz/1e6:7.1f}MB  {n} tabele{tag}")


def cmd_tables(a):
    p = resolve(a); con = connect_ro(p)
    for t in tables_of(con):
        try:
            n = con.execute(f'select count(*) from "{t}"').fetchone()[0]
        except Exception:
            n = "?"
        print(f"  {t:34} {n} rânduri")
    con.close()


def cmd_query(a):
    p = resolve(a)
    if os.path.basename(p) in SECRET_DBS:
        sys.exit("🔒 Baza asta ține SECRETE (tokenuri) — refuz interogarea din prudență.")
    q = a.sql.strip()
    if q.lower().split(None, 1)[0] not in ALLOWED:
        sys.exit("Doar SELECT/WITH/PRAGMA/EXPLAIN (read-only).")
    con = connect_ro(p)
    cur = con.execute(q)
    cols = [c[0] for c in cur.description] if cur.description else []
    rows = cur.fetchall()
    if cols:
        print(" | ".join(cols))
    for r in rows[:a.limit]:
        print(" | ".join("" if v is None else str(v) for v in r))
    if len(rows) > a.limit:
        print(f"… +{len(rows)-a.limit} (crește --limit)")
    con.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", help="folderul cu .db (implicit: ./data sau ~/Downloads/Scripturi/data)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    s = sub.add_parser("tables"); s.add_argument("--db", required=True); s.set_defaults(fn=cmd_tables)
    s = sub.add_parser("query"); s.add_argument("--db", required=True); s.add_argument("--sql", required=True)
    s.add_argument("--limit", type=int, default=50); s.set_defaults(fn=cmd_query)
    a = ap.parse_args(); a.fn(a)


if __name__ == "__main__":
    main()
