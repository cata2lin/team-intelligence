#!/usr/bin/env python3
"""ro_sqlite_mcp.py — server MCP READ-ONLY peste un SQLite, stdio, ZERO dependențe (stdlib pură).

Rulează pe VPS (system python3) și e legat local prin SSH stdio (vezi sqlite_ssh_mcp_launch.py).
Există fiindcă motorul de profit trăiește într-un SQLite pe VPS (`profitability.db`) — până acum
orice interogare cerea „ssh + python + psql-de-mână". Acum e un MCP normal, exact ca cele 6 Postgres.

READ-ONLY pe trei straturi, ca să NU poată atinge niciodată DB-ul viu al engine-ului de profit:
  1. deschidere `file:...?mode=ro` (SQLite refuză scrierea la nivel de fișier);
  2. `PRAGMA query_only=1`;
  3. whitelist pe interogare: doar SELECT/WITH/EXPLAIN/PRAGMA, o singură instrucțiune.

Protocol: JSON-RPC 2.0 newline-delimited (transportul stdio MCP). Metode: initialize,
tools/list, tools/call (query · list_tables · describe_table).

  ro_sqlite_mcp.py <cale_db>
"""
import sys, json, sqlite3, re

DB = sys.argv[1] if len(sys.argv) > 1 else "/root/Scripturi/data/profitability.db"
PROTOCOL = "2024-11-05"
_ALLOWED = re.compile(r"^\s*(SELECT|WITH|EXPLAIN|PRAGMA)\b", re.IGNORECASE)

TOOLS = [
    {"name": "query", "description": "Rulează un SELECT read-only pe profitability.db (motorul de profit ARONA). "
                                     "Doar SELECT/WITH/EXPLAIN/PRAGMA, o singură instrucțiune.",
     "inputSchema": {"type": "object", "properties": {
         "sql": {"type": "string", "description": "interogare SELECT"},
         "limit": {"type": "integer", "description": "plafon rânduri (implicit 1000)"}},
         "required": ["sql"]}},
    {"name": "list_tables", "description": "Listează tabelele și view-urile din profitability.db.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "describe_table", "description": "Coloanele (nume, tip) unui tabel din profitability.db.",
     "inputSchema": {"type": "object", "properties": {"table": {"type": "string"}}, "required": ["table"]}},
]


def _connect():
    cx = sqlite3.connect("file:%s?mode=ro" % DB, uri=True, timeout=15)
    cx.execute("PRAGMA query_only=1;")
    cx.row_factory = sqlite3.Row
    return cx


def _guard(sql):
    if ";" in sql.rstrip().rstrip(";"):
        raise ValueError("o singură instrucțiune (fără ';' multiple)")
    if not _ALLOWED.match(sql):
        raise ValueError("doar SELECT/WITH/EXPLAIN/PRAGMA sunt permise (read-only)")


def _rows_text(cur, limit):
    cols = [d[0] for d in cur.description] if cur.description else []
    rows = cur.fetchmany(limit)
    data = [dict(zip(cols, r)) for r in rows]
    more = "" if len(rows) < limit else "\n(plafon %d rânduri atins — rafinează cu LIMIT/WHERE)" % limit
    return json.dumps(data, ensure_ascii=False, default=str, indent=1) + more


def _call(name, args):
    if name == "query":
        sql = args.get("sql", ""); _guard(sql)
        limit = int(args.get("limit") or 1000)
        with _connect() as cx:
            return _rows_text(cx.execute(sql), limit)
    if name == "list_tables":
        with _connect() as cx:
            r = cx.execute("SELECT type, name FROM sqlite_master WHERE type IN ('table','view') "
                           "AND name NOT LIKE 'sqlite_%' ORDER BY type, name").fetchall()
        return "\n".join("%-5s %s" % (x["type"], x["name"]) for x in r)
    if name == "describe_table":
        t = args.get("table", "")
        if not re.match(r"^[A-Za-z0-9_]+$", t):
            raise ValueError("nume de tabel invalid")
        with _connect() as cx:
            r = cx.execute("PRAGMA table_info(%s)" % t).fetchall()
        if not r:
            return "tabel inexistent: %s" % t
        return "\n".join("%-28s %s" % (x["name"], x["type"]) for x in r)
    raise ValueError("tool necunoscut: %s" % name)


def _send(obj):
    sys.stdout.write(json.dumps(obj) + "\n"); sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
        if method == "initialize":
            _send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
                "serverInfo": {"name": "ro-sqlite-profitability", "version": "1.0.0"}}})
        elif method == "notifications/initialized":
            continue  # notificare, fără răspuns
        elif method == "tools/list":
            _send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            name = params.get("name"); args = params.get("arguments") or {}
            try:
                text = _call(name, args)
                _send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}]}})
            except Exception as e:
                _send({"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [{"type": "text", "text": "EROARE: %s: %s" % (type(e).__name__, e)}], "isError": True}})
        elif mid is not None:
            _send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found: %s" % method}})


if __name__ == "__main__":
    main()
