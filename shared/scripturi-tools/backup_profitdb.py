"""
backup_profitdb.py — backup CONSISTENT + rotit al motorului de profit (profitability.db).

DE CE: tot istoricul de profit (335k comenzi, marketing, mapare) e într-UN singur fișier SQLite de
~333MB, cu doar câteva .bak manuale (unul din 11-iun). Dacă fișierul se corupe → pierdere totală.

Folosește API-ul de backup online SQLite (nu `cp`) → snapshot consistent CHIAR dacă engine-ul scrie
în acel moment. Comprimă (gzip) și rotește (păstrează ultimele N). Pinguie heartbeat la succes.

  backup_profitdb.py                 # backup + rotație (implicit 7 zile)
  backup_profitdb.py --keep 14
"""
import os, sys, gzip, shutil, sqlite3, argparse, glob
from datetime import datetime, timezone

SRC = os.environ.get("PROFITABILITY_DB", "/root/Scripturi/data/profitability.db")
DEST_DIR = os.environ.get("PROFIT_BACKUP_DIR", "/root/backups/profitability")


def main():
    ap = argparse.ArgumentParser(description="Backup consistent + rotit al profitability.db")
    ap.add_argument("--keep", type=int, default=7, help="câte backup-uri păstrezi (implicit 7)")
    a = ap.parse_args()
    os.makedirs(DEST_DIR, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    raw = os.path.join(DEST_DIR, "profitability_%s.db" % stamp)
    gz = raw + ".gz"

    # 1) snapshot consistent via backup API online (sigur cu writeri activi)
    src = sqlite3.connect("file:%s?mode=ro" % SRC, uri=True, timeout=30)
    dst = sqlite3.connect(raw)
    with dst:
        src.backup(dst)
    dst.close(); src.close()

    # 2) comprimă (333MB → mult mai puțin) și șterge necomprimatul
    with open(raw, "rb") as f_in, gzip.open(gz, "wb", compresslevel=6) as f_out:
        shutil.copyfileobj(f_in, f_out)
    os.remove(raw)
    size_mb = os.path.getsize(gz) / 1e6

    # 3) rotație: păstrează ultimele --keep
    backups = sorted(glob.glob(os.path.join(DEST_DIR, "profitability_*.db.gz")))
    removed = 0
    for old in backups[:-a.keep]:
        os.remove(old); removed += 1

    print("[backup] %s (%.1f MB) · păstrate %d · șterse %d" % (gz, size_mb, min(len(backups), a.keep), removed))

    # 4) heartbeat (dead-man-switch): dacă backup-ul nu rulează, data_health semnalează
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import heartbeat  # noqa
        hb = sqlite3.connect(SRC, timeout=15); hb.execute("PRAGMA busy_timeout=8000;")
        hb.execute("""CREATE TABLE IF NOT EXISTS cron_heartbeat (
            name TEXT PRIMARY KEY, last_ping TEXT, expected_interval_min INTEGER, note TEXT)""")
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        hb.execute("INSERT INTO cron_heartbeat (name,last_ping,expected_interval_min,note) VALUES (?,?,?,?) "
                   "ON CONFLICT(name) DO UPDATE SET last_ping=excluded.last_ping",
                   ("profitdb_backup", now, 1440, "backup_profitdb.py 03:30"))
        hb.commit(); hb.close()
    except Exception as e:
        sys.stderr.write("[backup] heartbeat fail: %s\n" % e)


if __name__ == "__main__":
    main()
