"""
rp_db — shim sqlite-compatibil peste Postgres (metrics.richpanel_tickets), SURSA PARTAJATĂ.
Skill-urile consumatoare schimbă DOAR conexiunea: `rp_db.connect()` în loc de sqlite3.connect(local).
Traduce automat: tabel `tickets`→`richpanel_tickets`, `PRAGMA table_info(tickets)`→information_schema,
placeholder `?` (qmark) și `%` literal funcționează. Read-only.
"""
import os, re, urllib.parse, subprocess
import pg8000.dbapi
pg8000.dbapi.paramstyle = "qmark"  # ca '?' din skill-uri să meargă + '%' literal (LIKE '%x%')

_KB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "core", "scripts", "kb.py")


def _secret(k):
    return os.environ.get(k) or subprocess.run(["uv", "run", _KB, "secret-get", k], capture_output=True, text=True).stdout.strip()


class _Conn:
    def __init__(self):
        u = urllib.parse.urlparse(_secret("DATABASE_URL_METRICS"))
        self.c = pg8000.dbapi.connect(ssl_context=True, user=urllib.parse.unquote(u.username or ""),
                                      password=urllib.parse.unquote(u.password or ""), host=u.hostname,
                                      port=u.port or 5432, database=(u.path or "/").lstrip("/"))

    def execute(self, sql, params=()):
        cur = self.c.cursor()
        if "PRAGMA table_info" in sql:
            cur.execute("SELECT ordinal_position, column_name, data_type, 0, NULL, 0 FROM information_schema.columns "
                        "WHERE table_name='richpanel_tickets' ORDER BY ordinal_position")
            return cur  # r[1] = column_name, ca la sqlite PRAGMA
        sql = re.sub(r"\btickets\b", "richpanel_tickets", sql)
        cur.execute(sql, tuple(params) if params else ())
        return cur

    def cursor(self):
        return self.c.cursor()

    def commit(self):
        self.c.commit()

    def close(self):
        self.c.close()


def connect(*a, **k):
    """Drop-in pt sqlite3.connect(...) — ignoră argumentele de fișier, întoarce conexiune Postgres."""
    return _Conn()


def open(db_path=None):
    """SURSĂ AUTOMATĂ: dacă există SQLite-ul local (pipeline pe VPS) → SQLite read-only;
    altfel (agent CS pe altă mașină) → Postgres partajat (metrics.richpanel_tickets).
    Forțează Postgres cu env RICHPANEL_PG=1."""
    if not os.environ.get("RICHPANEL_PG") and db_path and os.path.exists(db_path):
        import sqlite3
        return sqlite3.connect("file:" + db_path + "?mode=ro", uri=True, timeout=30)
    return _Conn()
