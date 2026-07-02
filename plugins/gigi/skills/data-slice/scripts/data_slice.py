# /// script
# requires-python = ">=3.10"
# dependencies = ["duckdb"]
# ///
"""Interoghează/filtrează fișiere mari (CSV/JSON/Parquet/Excel) cu SQL, LOCAL (duckdb) — dai
LLM-ului doar rândurile/coloanele care contează, nu tot fișierul. Uriaș pe export-uri mari.

  uv run data_slice.py <fisier> [--sql "SELECT ... FROM t WHERE ..."] [--limit 50] [--cols a,b] [--csv] [--stdout]
    fara --sql: arata SCHEMA + primele rânduri (recon ieftin)
    tabelul se numeste 't' in SQL. Ex: --sql "SELECT brand, SUM(profit) FROM t GROUP BY 1 ORDER BY 2 DESC"
"""
import os, sys, argparse
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import duckdb

def reader(path):
    ext = os.path.splitext(path)[1].lower()
    p = path.replace("'", "''")
    if ext in (".csv", ".tsv", ".txt"): return f"read_csv_auto('{p}')"
    if ext == ".parquet": return f"read_parquet('{p}')"
    if ext in (".json", ".ndjson"): return f"read_json_auto('{p}')"
    if ext in (".xlsx", ".xls"): return f"read_xlsx('{p}')"
    return f"read_csv_auto('{p}')"

def to_md(rel):
    cols = rel.columns; rows = rel.fetchall()
    out = ["| " + " | ".join(cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
    for r in rows:
        out.append("| " + " | ".join("" if v is None else str(v) for v in r) + " |")
    return "\n".join(out)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--sql", default=None)
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--cols", default=None)
    ap.add_argument("--csv", action="store_true")
    ap.add_argument("--stdout", action="store_true")
    a = ap.parse_args()
    con = duckdb.connect()
    if a.file.lower().endswith((".xlsx", ".xls")):
        try: con.execute("INSTALL excel; LOAD excel;")
        except Exception: pass
    src = reader(a.file)
    con.execute(f"CREATE VIEW t AS SELECT * FROM {src}")
    if not a.sql:
        # schema + preview
        schema = con.execute("DESCRIBE t").fetchall()
        n = con.execute("SELECT COUNT(*) FROM t").fetchone()[0]
        print(f"# {os.path.basename(a.file)} — {n} rânduri\n\n## Coloane")
        print("\n".join(f"- {c[0]} ({c[1]})" for c in schema))
        sel = a.cols or "*"
        print(f"\n## Primele {a.limit} rânduri")
        rel = con.sql(f"SELECT {sel} FROM t LIMIT {a.limit}")
        print(to_md(rel))
        return
    q = a.sql if a.limit is None or "limit" in a.sql.lower() else f"SELECT * FROM ({a.sql}) LIMIT {a.limit}"
    rel = con.sql(q)
    if a.csv:
        import io, csv
        buf = io.StringIO(); w = csv.writer(buf); w.writerow(rel.columns); w.writerows(rel.fetchall())
        out = buf.getvalue()
    else:
        out = to_md(rel)
    print(out)

if __name__ == "__main__":
    main()
