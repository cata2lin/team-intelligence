"""
heartbeat.py — dead-man-switch pentru cronuri (echivalentul suplu al Healthchecks, fără Docker/Django).

DE CE: watchdog-ul data_health prinde DATE stale, dar NU „cronul n-a rulat DELOC" (token mort 11 zile,
cron TikTok oprit necunoscut). Un job pinguie AICI DOAR la SUCCES (`&& heartbeat.py <name>` la coada
liniei de cron). Dacă jobul pică sau nu rulează, nu vine ping → data_health îl semnalează ca overdue.

Ping (la coada cronului, DOAR pe succes fiindcă e după `&&`):
    ... comanda_reala ... && /root/Scripturi/.venv/bin/python /root/Scripturi/heartbeat.py profit_sync

Înregistrare interval (o dată, ca să știm ce e „overdue"):
    heartbeat.py profit_sync --interval-min 1440 --note "run_profit_sync.sh 02:30"

Verificarea = în data_health.py (check_heartbeats): overdue dacă now - last_ping > interval × grace.
"""
import sys, os, sqlite3, argparse
from datetime import datetime, timezone

PF_DB = os.environ.get("PROFITABILITY_DB", "/root/Scripturi/data/profitability.db")


def _cx():
    cx = sqlite3.connect(PF_DB, timeout=15)
    cx.execute("PRAGMA busy_timeout=8000;")
    cx.execute("""CREATE TABLE IF NOT EXISTS cron_heartbeat (
        name TEXT PRIMARY KEY, last_ping TEXT, expected_interval_min INTEGER, note TEXT)""")
    return cx


def main():
    ap = argparse.ArgumentParser(description="Ping heartbeat pentru un cron (dead-man-switch)")
    ap.add_argument("name")
    ap.add_argument("--interval-min", type=int, help="intervalul așteptat între rulări (minute)")
    ap.add_argument("--note")
    a = ap.parse_args()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    cx = _cx()
    row = cx.execute("SELECT expected_interval_min, note FROM cron_heartbeat WHERE name=?", (a.name,)).fetchone()
    interval = a.interval_min if a.interval_min is not None else (row[0] if row else None)
    note = a.note if a.note is not None else (row[1] if row else None)
    cx.execute("INSERT INTO cron_heartbeat (name,last_ping,expected_interval_min,note) VALUES (?,?,?,?) "
               "ON CONFLICT(name) DO UPDATE SET last_ping=excluded.last_ping, "
               "expected_interval_min=COALESCE(excluded.expected_interval_min, cron_heartbeat.expected_interval_min), "
               "note=COALESCE(excluded.note, cron_heartbeat.note)",
               (a.name, now, interval, note))
    cx.commit(); cx.close()
    print("[hb] %s @ %s UTC" % (a.name, now))


if __name__ == "__main__":
    main()
