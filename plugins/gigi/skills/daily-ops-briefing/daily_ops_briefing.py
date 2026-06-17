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
import os, sys, json, subprocess, shlex, urllib.parse, sqlite3, re
import pg8000.dbapi

VPS = "root@84.46.242.181"
HERE = os.path.dirname(os.path.abspath(__file__))
RP_DB = os.environ.get("RICHPANEL_DB") or os.path.join(HERE, "..", "..", "..", "..", "..", "data", "richpanel_tickets.db")
_BUY = re.compile(r"cum.*comand|pre[tț]\b|cat\s*cost|cât\s*cost|vreau\s*(si|și)?\s*eu|a[sș]\s*dori|doresc|m[ăa]\s*interes|ave[tț]i\b", re.I)
_FRUST = re.compile(r"al\s*(doilea|treilea)\s*(e?mail|mesaj)|nu\s*r[ăa]spunde\s*nimeni|nici\s*un\s*r[ăa]spuns|niciun\s*r[ăa]spuns|anpc|v-?am\s*scris", re.I)


def richpanel_lines():
    if not os.path.exists(RP_DB):
        return ["  🟣 Tichete CS (Richpanel): — (rulează gigi:richpanel-export pull)"]
    try:
        c = sqlite3.connect(RP_DB)
        real = "channel NOT LIKE '%comment%' AND category NOT IN ('spam_automat','recenzie_feedback','comentariu_social','salut_fara_continut','formular_contact')"
        open_cs = c.execute(f"SELECT COUNT(*) FROM tickets WHERE status='OPEN' AND {real}").fetchone()[0]
        frust = sum(1 for (t,) in c.execute(f"SELECT COALESCE(first_message,'')||' '||COALESCE(subject,'') FROM tickets WHERE status='OPEN' AND {real}") if _FRUST.search(t or ""))
        leads = sum(1 for (t,) in c.execute("SELECT COALESCE(first_message,'')||' '||COALESCE(subject,'') FROM tickets WHERE status='OPEN' AND channel LIKE '%comment%'") if _BUY.search(t or ""))
        c.close()
        return [
            "  🟣 Tichete CS deschise (Richpanel): %d  (frustrate/ANPC: %d)   → gigi:cs-quality-audit frustrated" % (open_cs, frust),
            "  🟢 Lead-uri deschise în comentarii la reclame: %d            → gigi:cs-comment-intelligence leads --open" % leads,
        ]
    except Exception as e:
        return ["  🟣 Tichete CS (Richpanel): eroare citire (%s)" % str(e)[:40]]


def secret(k):
    v = os.environ.get(k)
    if v:
        return v
    kb = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")
    return subprocess.run(["uv", "run", kb, "secret-get", k], capture_output=True, text=True).stdout.strip()


def _pg(url):
    u = urllib.parse.urlparse(url)
    return pg8000.dbapi.connect(ssl_context=True, user=urllib.parse.unquote(u.username or ""),
                                password=urllib.parse.unquote(u.password or ""), host=u.hostname,
                                port=u.port or 5432, database=(u.path or "/").lstrip("/"))


def main():
    import datetime
    today = datetime.date.today()
    yd = (today - datetime.timedelta(days=1)).isoformat()
    ms = today.replace(day=1).isoformat()
    d7 = (today - datetime.timedelta(days=7)).isoformat()
    d5 = (today - datetime.timedelta(days=5)).isoformat()
    s60 = (today - datetime.timedelta(days=60)).isoformat()
    s6 = (today - datetime.timedelta(days=6)).isoformat()
    # P&L from cache.daily_brand_pnl + outcome from cache.order_outcome (was SSH→daily_perf/profitability.db)
    try:
        mc = _pg(secret("DATABASE_URL_METRICS")).cursor()
    except Exception as e:
        print("Nu am putut conecta la metrics:", str(e)[:160]); return
    def agg(wsql, params):
        mc.execute("SELECT COALESCE(SUM(revenue),0)::float8, COALESCE(SUM(total_spend),0)::float8, "
                   "COALESCE(SUM(cogs),0)::float8, COALESCE(SUM(transport),0)::float8, "
                   "COALESCE(SUM(orders),0)::int FROM cache.daily_brand_pnl WHERE " + wsql, params)
        return list(mc.fetchone())
    y = agg("date=%s", (yd,))
    m = agg("date>=%s AND date<=%s", (ms, yd))
    mc.execute("SELECT brand_name, SUM(revenue)::float8, SUM(total_spend)::float8 FROM cache.daily_brand_pnl "
               "WHERE date=%s GROUP BY brand_name ORDER BY 2 DESC LIMIT 6", (yd,))
    tb = [list(r) for r in mc.fetchall()]
    mc.execute("SELECT COUNT(*)::int, COALESCE(SUM(revenue),0)::float8 FROM cache.order_outcome "
               "WHERE status_category='Refuzata' AND created_at::date >= %s", (d7,))
    refz = list(mc.fetchone())
    mc.execute("SELECT COUNT(*)::int FROM cache.order_outcome WHERE status_category='Netrimisa' "
               "AND created_at::date >= %s", (d5,))
    netr = mc.fetchone()[0]
    mc.execute("SELECT COUNT(*)::int FROM cache.order_outcome WHERE status_category='In curs de livrare' "
               "AND created_at::date BETWEEN %s AND %s", (s60, s6))
    stuck = mc.fetchone()[0]
    d = {"yd": yd, "ms": ms, "y": y, "m": m, "tb": tb, "refz": refz, "netr": netr, "stuck": stuck}
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
    print("(contribuție = ESTIMAT daily_perf, venit brut cu TVA; profit REAL livrat-fara-TVA: gigi:multi-brand-pnl)")
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
    for ln in richpanel_lines():
        print(ln)
    print("-" * 60)


if __name__ == "__main__":
    main()
