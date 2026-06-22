"""
sync_raport_zilnic.py — Backfill daily_perf + profit_marketing_override from the
COMPLETE "Raport Zilnic 2" sheet (read via public CSV export, so it does NOT depend
on the gspread service-account path that left daily_perf with missing days).

- Parsing + profit formula are IDENTICAL to api/daily_perf.py (_read_sheet_data).
- Idempotent: INSERT OR REPLACE keyed on (date, brand) / (month, prefix).
- Safe to run on a schedule (cron) so scripts.arona always reflects Raport Zilnic 2.

Grandia is intentionally NOT in this sheet -> its marketing override is left untouched.
"""
import sys, csv, io, sqlite3, urllib.request
from datetime import datetime

BASE = "/root/Scripturi"
sys.path.insert(0, BASE)
from core.brands import BRAND_TO_PREFIX

DATA = BASE + "/data"
DP_DB = DATA + "/daily_perf.db"
PF_DB = DATA + "/profitability.db"
CSV_URL = ("https://docs.google.com/spreadsheets/d/"
           "1IVg0fI-_Rm7IptmOl3BmGrqtyyzn3auf0ZPuftr9vQo/export?format=csv&gid=1025107216")


def _parse_number(val):
    if not val or val.strip() == "":
        return 0.0
    s = val.strip().replace("\xa0", "")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _parse_date(val):
    s = (val or "").strip()
    if not s:
        return ""
    if "." in s and len(s) == 10:
        p = s.split(".")
        if len(p) == 3 and len(p[2]) == 4:
            return "%s-%s-%s" % (p[2], p[1], p[0])
    if len(s) >= 10 and s[4] == "-":
        return s[:10]
    return s


def fetch_rows():
    req = urllib.request.Request(CSV_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=90) as r:
        text = r.read().decode("utf-8")
    return list(csv.reader(io.StringIO(text)))


def build_records(rows):
    recs = []
    for row in rows[1:]:
        if len(row) < 6:
            continue
        date_str = _parse_date(row[0]); brand = row[1].strip() if len(row) > 1 else ""
        if not date_str or not brand:
            continue
        fb = _parse_number(row[2]) if len(row) > 2 else 0
        tk = _parse_number(row[3]) if len(row) > 3 else 0
        orders = _parse_number(row[4]) if len(row) > 4 else 0
        revenue = _parse_number(row[5]) if len(row) > 5 else 0
        cogs = _parse_number(row[6]) if len(row) > 6 else 0
        transport = _parse_number(row[7]) if len(row) > 7 else 0
        consum = _parse_number(row[8]) if len(row) > 8 else 0
        ctom = _parse_number(row[9]) if len(row) > 9 else 0
        cag = _parse_number(row[10]) if len(row) > 10 else 0
        abon = _parse_number(row[11]) if len(row) > 11 else 0
        cpa = _parse_number(row[13]) if len(row) > 13 else 0
        fbc = int(_parse_number(row[14])) if len(row) > 14 else 0
        tkc = int(_parse_number(row[15])) if len(row) > 15 else 0
        fbi = int(_parse_number(row[16])) if len(row) > 16 else 0
        tki = int(_parse_number(row[17])) if len(row) > 17 else 0
        gg = _parse_number(row[18]) if len(row) > 18 else 0
        ggc = int(_parse_number(row[19])) if len(row) > 19 else 0
        ggi = int(_parse_number(row[20])) if len(row) > 20 else 0
        aov = _parse_number(row[21]) if len(row) > 21 else 0
        total = round(fb + tk + gg, 2)
        profit = round(revenue - cogs - transport - consum - ctom - cag - abon - total, 2)
        roas = round(revenue / total, 2) if total > 0 else 0.0
        recs.append((date_str, brand, int(orders), round(revenue, 2), profit,
                     round(fb, 2), round(tk, 2), round(gg, 2), total, roas, round(cpa, 2),
                     round(cogs, 2), round(transport, 2), fbc, tkc, fbi, tki, ggc, ggi,
                     round(aov, 2), datetime.now().isoformat()))
    return recs


def upsert_daily_perf(recs):
    conn = sqlite3.connect(DP_DB); conn.execute("PRAGMA busy_timeout=5000;")
    conn.executemany("""
        INSERT OR REPLACE INTO daily_perf
        (date, brand, orders, revenue, profit, fb_spend, tk_spend, google_spend, total_spend,
         roas, cpa, cogs, transport, fb_clicks, tk_clicks, fb_impressions, tk_impressions,
         google_clicks, google_impressions, aov, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, recs)
    conn.commit(); conn.close()


def refresh_overrides(months=None):
    dp = sqlite3.connect(DP_DB); dp.row_factory = sqlite3.Row
    pf = sqlite3.connect(PF_DB)
    q = "SELECT substr(date,1,7) m, brand, ROUND(SUM(total_spend),2) s FROM daily_perf GROUP BY m, brand"
    n = 0; changes = []
    for r in dp.execute(q):
        pfx = BRAND_TO_PREFIX.get(r["brand"])
        if not pfx:
            continue
        if months and r["m"] not in months:
            continue
        old = pf.execute("SELECT amount FROM profit_marketing_override WHERE month=? AND prefix=?",
                         (r["m"], pfx)).fetchone()
        oldv = old[0] if old else None
        pf.execute("INSERT OR REPLACE INTO profit_marketing_override (month,prefix,amount) VALUES (?,?,?)",
                   (r["m"], pfx, r["s"]))
        n += 1
        if r["m"] >= "2026-01":
            changes.append((r["m"], pfx, oldv, r["s"]))
    pf.commit(); dp.close(); pf.close()
    return n, changes


def grandia_overrides():
    """Grandia is NOT in 'Raport Zilnic 2' -> pull its marketing (FB+Google) from its
    own Postgres (authoritative source) and write the GRAN override. Best-effort:
    any failure is swallowed so the sheet sync still completes."""
    url = None
    try:
        for line in open(BASE + "/.env", encoding="utf-8"):
            line = line.strip()
            if line.startswith("DATABASE_URL_GRANDIA="):
                url = line.split("=", 1)[1].strip().strip('"').strip("'"); break
    except Exception:
        return 0
    if not url:
        return 0
    try:
        import urllib.parse
        import pg8000.dbapi
        u = urllib.parse.urlparse(url)
        kw = dict(user=urllib.parse.unquote(u.username or ""), password=urllib.parse.unquote(u.password or ""),
                  host=u.hostname, port=u.port or 5432, database=(u.path or "/").lstrip("/"))
        try:
            conn = pg8000.dbapi.connect(ssl_context=True, **kw)
        except Exception:
            conn = pg8000.dbapi.connect(**kw)
        cur = conn.cursor()
        lo = "2000-01-01"  # pull ALL available history from Grandia's DB (no year limit)
        agg = {}
        for tbl in ("fbads_daily_ad_totals", "gads_daily_product_spend"):
            cur.execute('SELECT to_char("reportDate", \'YYYY-MM\'), COALESCE(SUM(spend),0) FROM '
                        + tbl + ' WHERE "reportDate" >= %s GROUP BY 1', (lo,))
            for m, s in cur.fetchall():
                agg[m] = agg.get(m, 0.0) + float(s)
        conn.close()
        pf = sqlite3.connect(PF_DB); n = 0
        for m, amt in agg.items():
            pf.execute("INSERT OR REPLACE INTO profit_marketing_override (month,prefix,amount) VALUES (?,?,?)",
                       (m, "GRAN", round(amt, 2)))
            n += 1
        pf.commit(); pf.close()
        return n
    except Exception as e:
        print("grandia sync skipped:", type(e).__name__, e)
        return 0


if __name__ == "__main__":
    rows = fetch_rows()
    recs = build_records(rows)
    upsert_daily_perf(recs)
    n, changes = refresh_overrides()  # ALL months present in daily_perf (full history)
    gn = grandia_overrides()  # Grandia: pull full history from its own DB
    print("CSV rows: %d | daily_perf upserted: %d | overrides refreshed: %d | Grandia months: %d" % (len(rows) - 1, len(recs), n, gn))
    # verification
    dp = sqlite3.connect(DP_DB)
    for b in ("Esteban", "George Talent", "Nubra", "Ofertele Zilei"):
        d, s = dp.execute("SELECT COUNT(*), ROUND(SUM(total_spend)) FROM daily_perf WHERE brand=? AND date BETWEEN '2026-05-01' AND '2026-05-31'", (b,)).fetchone()
        print("  daily_perf %-16s MAY: days=%s total_spend=%s" % (b, d, "{:,.0f}".format(s or 0)))
    dp.close()
    print("\nOverride changes (2026), key brands:")
    for m, pfx, old, new in sorted(changes):
        if pfx in ("EST", "GT", "NUB", "GEN", "OFER", "MAG", "RED", "CZ", "BON", "BELA") and m >= "2026-04":
            o = "{:,.0f}".format(old) if old is not None else "—"
            print("  %s %-5s  %12s -> %12s" % (m, pfx, o, "{:,.0f}".format(new)))
