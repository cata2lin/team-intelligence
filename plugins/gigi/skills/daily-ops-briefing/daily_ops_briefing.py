# /// script
# requires-python = ">=3.10"
# dependencies = ["pg8000>=1.30"]
# ///
"""
daily_ops_briefing.py — brief-ul de dimineață al operațiunilor Arona, într-un singur comand:
vânzări + cheltuială + profit ieri & MTD (toate brandurile), + lista de ACȚIUNI a zilei cu cifre
(refuzate de recuperat, COD de confirmat, colete blocate, RMA deschise) și ce skill rulezi pt fiecare.
NU scrie nimic.

  uv run daily_ops_briefing.py
"""
import os, sys, json, subprocess, shlex, urllib.parse
import pg8000.dbapi

VPS = "root@84.46.242.181"
HERE = os.path.dirname(os.path.abspath(__file__))


def secret(k):
    v = os.environ.get(k)
    if v:
        return v
    kb = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")
    return subprocess.run(["uv", "run", kb, "secret-get", k], capture_output=True, text=True).stdout.strip()


PY = r"""
import sqlite3,json,datetime
today=datetime.date.today(); yd=(today-datetime.timedelta(days=1)).isoformat()
ms=today.replace(day=1).isoformat()
dp=sqlite3.connect('data/daily_perf.db')
def agg(w,p):
    r=dp.execute("SELECT COALESCE(SUM(revenue),0),COALESCE(SUM(total_spend),0),COALESCE(SUM(cogs),0),COALESCE(SUM(transport),0),COALESCE(SUM(orders),0) FROM daily_perf WHERE "+w,p).fetchone()
    return list(r)
y=agg("date=?",(yd,)); m=agg("date>=? AND date<=?",(ms,yd))
tb=dp.execute("SELECT brand,SUM(revenue),SUM(total_spend) FROM daily_perf WHERE date=? GROUP BY brand ORDER BY 2 DESC LIMIT 6",(yd,)).fetchall()
pf=sqlite3.connect('data/profitability.db')
d7=(today-datetime.timedelta(days=7)).isoformat(); d5=(today-datetime.timedelta(days=5)).isoformat()
s6=(today-datetime.timedelta(days=6)).isoformat(); s60=(today-datetime.timedelta(days=60)).isoformat()
refz=pf.execute("SELECT COUNT(*),COALESCE(SUM(revenue),0) FROM profit_orders WHERE status_category='Refuzata' AND substr(created_at,1,10)>=?",(d7,)).fetchone()
netr=pf.execute("SELECT COUNT(*) FROM profit_orders WHERE status_category='Netrimisa' AND substr(created_at,1,10)>=?",(d5,)).fetchone()[0]
stuck=pf.execute("SELECT COUNT(*) FROM profit_orders WHERE status_category='In curs de livrare' AND substr(created_at,1,10) BETWEEN ? AND ?",(s60,s6)).fetchone()[0]
print(json.dumps({'yd':yd,'ms':ms,'y':y,'m':m,'tb':tb,'refz':list(refz),'netr':netr,'stuck':stuck}))
"""


def main():
    out = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", VPS,
                          "cd /root/Scripturi && .venv/bin/python3 -c " + shlex.quote(PY)],
                         capture_output=True, text=True, timeout=90).stdout.strip()
    try:
        d = json.loads(out.splitlines()[-1])
    except Exception:
        print("Nu am putut citi datele:", out[:160]); return
    # open RMAs (grandia)
    rma = "?"
    try:
        url = secret("DATABASE_URL_GRANDIA"); u = urllib.parse.urlparse(url)
        c = pg8000.dbapi.connect(ssl_context=True, user=urllib.parse.unquote(u.username or ""),
                                 password=urllib.parse.unquote(u.password or ""), host=u.hostname,
                                 port=u.port or 5432, database=(u.path or "/").lstrip("/")).cursor()
        c.execute("SELECT COUNT(*) FROM rma_requests WHERE status IN ('NEW','IN_PROGRESS','AWAITING_REFUND')")
        rma = c.fetchone()[0]
    except Exception:
        pass

    def contrib(a):
        return a[0] - a[2] - a[3] - a[1]  # rev - cogs - transport - spend

    def mer(a):
        return (a[0] / a[1]) if a[1] else 0
    y, m = d["y"], d["m"]
    f = lambda n: "{:,.0f}".format(n)
    print("=" * 60)
    print("  BRIEF OPERAȚIUNI ARONA — %s" % d["yd"])
    print("=" * 60)
    print("IERI (%s):" % d["yd"])
    print("  venit %s | reclame %s | contribuție %s | MER %.1f | comenzi %d" % (
        f(y[0]), f(y[1]), f(contrib(y)), mer(y), int(y[4])))
    print("MTD (%s → %s):" % (d["ms"], d["yd"]))
    print("  venit %s | reclame %s | contribuție %s | MER %.1f | comenzi %d" % (
        f(m[0]), f(m[1]), f(contrib(m)), mer(m), int(m[4])))
    print("\nTop branduri ieri (venit | reclame):")
    for b, rev, sp in d["tb"]:
        print("  %-16s %10s | %9s" % (b[:16], f(rev or 0), f(sp or 0)))
    print("\n" + "-" * 60)
    print("ACȚIUNILE ZILEI:")
    print("  🔴 Refuzate de recuperat (7z): %d comenzi / %s lei   → gigi:cs-refused-recovery" % (int(d["refz"][0]), f(d["refz"][1])))
    print("  🟡 COD de confirmat înainte de livrare (5z): %d        → gigi:cod-confirmation" % d["netr"])
    print("  🟠 Colete blocate în tranzit (>6z): %d                → gigi:cs-proactive-delays" % d["stuck"])
    print("  🔵 RMA deschise (Grandia): %s                          → gigi:returns-rma-report" % rma)
    # EXTENSIE: linia de tichete Richpanel se adaugă aici când se conectează Richpanel (API).
    print("  🟣 Tichete CS deschise (Richpanel): — (se conectează în curând)")
    print("-" * 60)


if __name__ == "__main__":
    main()
