# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
cs_agent_performance.py — performanța agenților de Customer Service pe baza comenzilor
PLASATE de ei în Shopify (tag-uri CS: Raluca/Oana/Andra/Anna/OanaO — vezi tool-ul CS din
aplicația Scripturi). NB: deocamdată = doar comenzi plasate manual de agent; throughput-ul
de tichete vine când tragem Richpanel. NU scrie nimic.

  uv run cs_agent_performance.py --days 30
  uv run cs_agent_performance.py --month 2026-05
"""
import os, sys, json, subprocess, shlex, argparse, datetime

VPS = "root@84.46.242.181"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--month", default="")
    a = ap.parse_args()
    if a.month:
        where = "substr(created_at,1,7)='%s'" % a.month
        label = a.month
    else:
        cutoff = (datetime.date.today() - datetime.timedelta(days=a.days)).isoformat()
        where = "substr(created_at,1,10)>='%s'" % cutoff
        label = "ultimele %d zile" % a.days
    py = ("import sqlite3,json;c=sqlite3.connect('data/profitability.db');"
          "row=c.execute(\"SELECT value FROM profit_settings WHERE key='cs_tags'\").fetchone();"
          "tags=json.loads(row[0]) if row else ['Raluca','Oana','Andra','Anna','OanaO'];"
          "res={};"
          "[res.__setitem__(t, c.execute(\"SELECT COUNT(*),SUM(status_category='Livrata'),SUM(status_category='Refuzata'),SUM(CASE WHEN status_category='Livrata' THEN revenue ELSE 0 END) FROM profit_orders WHERE tags LIKE ? AND " + where + "\",('%'+t+'%',)).fetchone()) for t in tags];"
          "print(json.dumps(res))")
    cmd = "cd /root/Scripturi && .venv/bin/python3 -c " + shlex.quote(py)
    out = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", VPS, cmd],
                         capture_output=True, text=True, timeout=80).stdout.strip()
    try:
        res = json.loads(out.splitlines()[-1])
    except Exception:
        print("Eroare la citirea datelor:", out[:200]); return
    print("=== PERFORMANȚĂ AGENȚI CS (comenzi plasate) — %s ===" % label)
    print("NB: doar comenzi plasate de agent în Shopify (tag CS). Tichete = când avem Richpanel.\n")
    print("%-10s %10s %10s %10s %9s %8s" % ("agent", "plasate", "livrate", "refuzate", "val.livr", "liv%"))
    print("-" * 62)
    rows = []
    for t, r in res.items():
        n, liv, refz, val = int(r[0] or 0), int(r[1] or 0), int(r[2] or 0), float(r[3] or 0)
        rows.append((t, n, liv, refz, val, (liv / (liv + refz) * 100 if (liv + refz) else 0)))
    for t, n, liv, refz, val, lr in sorted(rows, key=lambda x: -x[1]):
        print("%-10s %10d %10d %10d %9s %7.0f%%" % (t, n, liv, refz, "{:,.0f}".format(val), lr))
    tn = sum(x[1] for x in rows); tv = sum(x[4] for x in rows)
    print("-" * 62)
    print("%-10s %10d %32s" % ("TOTAL", tn, "{:,.0f}".format(tv)))


if __name__ == "__main__":
    main()
