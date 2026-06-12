# /// script
# requires-python = ">=3.10"
# dependencies = ["pg8000>=1.30"]
# ///
"""
richpanel_sync.py — SYNC baza locală (SQLite, lucrul pipeline-ului) → metrics.richpanel_tickets (Postgres).
UPSERT idempotent: re-rulabil, ia doar diferențele. Asta face Postgres-ul SURSA PARTAJATĂ pe care
o citesc toți agenții CS + aplicațiile. E pasul final al pipeline-ului.

  uv run richpanel_sync.py            # sincronizează tot
  uv run richpanel_sync.py --since 2026-05-01   # doar tichetele create/actualizate după dată (sync incremental)
"""
import os, sqlite3, urllib.parse, time, subprocess, argparse
import pg8000.dbapi as pg

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", ".."))
SQLITE = os.environ.get("RICHPANEL_DB") or os.path.join(REPO, "data", "richpanel_tickets.db")
KB = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")
COLS = ["id", "conversation_no", "subject", "status", "priority", "assignee_id", "channel", "from_email",
        "to_email", "customer_id", "customer_name", "customer_email", "tags", "first_message", "comment_count",
        "created_at", "updated_at", "store", "order_name", "category", "resolved_store", "contact_email",
        "contact_phone", "match_order", "link_method", "sentiment", "sent_intensity", "comment_type",
        "quality_flags", "raw"]
TYPES = {"conversation_no": "BIGINT", "comment_count": "INTEGER"}


def secret(k):
    return os.environ.get(k) or subprocess.run(["uv", "run", KB, "secret-get", k], capture_output=True, text=True).stdout.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="doar created_at >= dată (sync incremental)")
    a = ap.parse_args()
    u = urllib.parse.urlparse(secret("DATABASE_URL_METRICS"))
    pc = pg.connect(ssl_context=True, user=urllib.parse.unquote(u.username or ""), password=urllib.parse.unquote(u.password or ""),
                    host=u.hostname, port=u.port or 5432, database=(u.path or "/").lstrip("/"))
    pcur = pc.cursor()
    ddl = "CREATE TABLE IF NOT EXISTS richpanel_tickets (\n" + ",\n".join(
        '  "%s" %s%s' % (c, TYPES.get(c, "TEXT"), " PRIMARY KEY" if c == "id" else "") for c in COLS) + "\n)"
    pcur.execute(ddl)
    for ix, col in [("ix_rpt_store", "resolved_store"), ("ix_rpt_cat", "category"), ("ix_rpt_created", "created_at"), ("ix_rpt_match", "match_order")]:
        pcur.execute("CREATE INDEX IF NOT EXISTS %s ON richpanel_tickets(%s)" % (ix, col))
    pc.commit()

    s = sqlite3.connect("file:" + SQLITE + "?mode=ro", uri=True, timeout=60)
    where = " WHERE substr(created_at,1,10) >= '%s'" % a.since if a.since else ""
    sel = "SELECT %s FROM tickets%s" % (",".join('"%s"' % c for c in COLS), where)
    ins = 'INSERT INTO richpanel_tickets (%s) VALUES ' % ",".join('"%s"' % c for c in COLS)
    upd = " ON CONFLICT (id) DO UPDATE SET " + ",".join('"%s"=EXCLUDED."%s"' % (c, c) for c in COLS if c != "id")
    placeholders = "(" + ",".join(["%s"] * len(COLS)) + ")"
    t0 = time.time(); n = 0; BATCH = 500; batch = []
    for row in s.execute(sel):
        batch.append(list(row))
        if len(batch) >= BATCH:
            pcur.execute(ins + ",".join([placeholders] * len(batch)) + upd, [x for r in batch for x in r])
            n += len(batch); batch = []
            if n % 20000 == 0:
                pc.commit(); print("  ..%d (%.0fs)" % (n, time.time() - t0), flush=True)
    if batch:
        pcur.execute(ins + ",".join([placeholders] * len(batch)) + upd, [x for r in batch for x in r])
        n += len(batch)
    pc.commit(); s.close()
    pcur.execute("SELECT COUNT(*) FROM richpanel_tickets")
    print("✅ SYNC: %d upsert | total în metrics.richpanel_tickets: %d (%.0fs)" % (n, pcur.fetchone()[0], time.time() - t0), flush=True)
    pc.close()


if __name__ == "__main__":
    main()
