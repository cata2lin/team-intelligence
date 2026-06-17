# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary"]
# ///
"""
multi_brand_pnl.py — P&L live "all-in" pentru ORICARE sau TOATE cele 16+ branduri Arona
(Esteban, George Talent, Nubra, Bonhaus RO/CZ/PL/BG/SK, Ofertele Zilei, Reduceri bune,
Magdeal, Belasil, Gento, Carpetto, Covoria, Nocturna, Rossi Nails, Apreciat...) pentru un
interval de date.

Sursa = cache.daily_brand_pnl din warehouse-ul metrics (oglinda daily_perf.db, reimprospatata
de gigi:metrics-cache pe cron). Inainte foloseam SSH la daily_perf.db direct; acum citim din
cache (aceleasi cifre, fara dependenta de SSH). Pentru fiecare brand agreghează: venit,
cheltuiala FB/Google/TikTok, COGS, transport, profit de contributie, MER, ROAS, CPA, AOV.

Citeste DOAR (SELECT). Verifica prospetimea cu: gigi:metrics-cache --status (cache.freshness).

Folosire:
  uv run multi_brand_pnl.py --today
  uv run multi_brand_pnl.py --brands all --from 2026-06-01 --to 2026-06-11
  uv run multi_brand_pnl.py --brands esteban,nubra,gt --from 2026-06-01 --to 2026-06-11
  uv run multi_brand_pnl.py --brands all --from 2026-06-01 --to 2026-06-11 --consolidated
"""
import sys
import os
import json
import argparse
import datetime
import subprocess
from pathlib import Path
import psycopg2


def _kb_path():
    env = os.environ.get("KB_PATH")
    if env and Path(env).exists():
        return env
    here = Path(__file__).resolve()
    for up in range(2, 7):
        c = here.parents[up] / "core" / "scripts" / "kb.py"
        if c.exists():
            return str(c)
    return None


def secret(key):
    v = os.environ.get(key)
    if v:
        return v.strip()
    kb = _kb_path()
    if kb:
        try:
            return subprocess.run(["uv", "run", kb, "secret-get", key],
                                  capture_output=True, text=True, timeout=60).stdout.strip()
        except Exception:
            return ""
    return ""


def _clean_dsn(dsn):
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
    u = urlsplit(dsn)
    keep = {"sslmode", "sslrootcert", "sslcert", "sslkey", "connect_timeout", "application_name"}
    q = [(k, v) for k, v in parse_qsl(u.query) if k in keep]
    return urlunsplit((u.scheme, u.netloc, u.path, urlencode(q), u.fragment))

# Alias prietenos -> fragment care apare in coloana brand din DB (lower, substring match).
ALIASES = {
    "esteban": "esteban",
    "gt": "george talent",
    "george talent": "george talent",
    "george-talent": "george talent",
    "nubra": "nubra",
    "bonhaus ro": "bonhaus ro",
    "bonhaus cz": "bonhaus cz",
    "bonhaus pl": "bonhaus pl",
    "bonhaus bg": "bonhaus bg",
    "bonhaus sk": "bonhaus sk",
    "ofertele zilei": "ofertele zilei",
    "oz": "ofertele zilei",
    "reduceri bune": "reduceri bune",
    "magdeal": "magdeal",
    "belasil": "belasil",
    "gento": "gento",
    "carpetto": "carpetto",
    "covoria": "covoria",
    "nocturna": "nocturna",
    "rossi nails": "rossi nails",
    "rossi": "rossi nails",
    "apreciat": "apreciat",
}


def resolve_brands(arg):
    """Return ('all', None) sau ('list', [fragmente lower])."""
    if not arg or arg.strip().lower() == "all":
        return ("all", None)
    frags = []
    for part in arg.split(","):
        p = part.strip().lower()
        if not p:
            continue
        frags.append(ALIASES.get(p, p))
    return ("list", frags)


# --- agregare per brand din cache.daily_brand_pnl (warehouse metrics) ---
def run_remote(date_from, date_to, mode, frags):
    """Same shape as before (list of dicts: brand,o,rev,fb,tk,ggl,sp,cogs,tr,profit,days),
    but now read from cache.daily_brand_pnl instead of SSH to daily_perf.db. ::float8 casts
    keep the values as plain floats (identical downstream behaviour)."""
    where = ["date >= %s", "date <= %s"]
    params = [date_from, date_to]
    if mode == "list" and frags:
        where.append("(" + " OR ".join(["LOWER(brand_name) LIKE %s"] * len(frags)) + ")")
        params += ["%" + f + "%" for f in frags]
    sql = (
        "SELECT brand_name AS brand, "
        "SUM(orders)::int o, "
        "SUM(revenue)::float8 rev, "
        "SUM(fb_spend)::float8 fb, "
        "SUM(tk_spend)::float8 tk, "
        "SUM(google_spend)::float8 ggl, "
        "SUM(total_spend)::float8 sp, "
        "SUM(cogs)::float8 cogs, "
        "SUM(transport)::float8 tr, "
        "SUM(contribution_margin)::float8 profit, "
        "COUNT(DISTINCT date)::int days "
        "FROM cache.daily_brand_pnl WHERE " + " AND ".join(where) + " "
        "GROUP BY brand_name "
        "HAVING SUM(revenue) > 0 OR SUM(total_spend) > 0 OR SUM(orders) > 0 "
        "ORDER BY brand_name"
    )
    dsn = secret("DATABASE_URL_METRICS")
    if not dsn:
        sys.exit("EROARE: DATABASE_URL_METRICS lipseste (env sau KB).")
    try:
        conn = psycopg2.connect(_clean_dsn(dsn), connect_timeout=20)
    except Exception as e:
        sys.exit("EROARE conexiune metrics: " + str(e)[:200])
    cur = conn.cursor()
    cur.execute(sql, params)
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.close()
    return rows


# --- metrici derivate per brand ---
def enrich(r):
    rev = r.get("rev") or 0.0
    sp = r.get("sp") or 0.0
    cogs = r.get("cogs") or 0.0
    tr = r.get("tr") or 0.0
    o = r.get("o") or 0
    contrib = rev - cogs - tr - sp           # = profit de contributie all-in
    return {
        "brand": r["brand"],
        "days": r.get("days") or 0,
        "orders": o,
        "rev": rev,
        "fb": r.get("fb") or 0.0,
        "tk": r.get("tk") or 0.0,
        "ggl": r.get("ggl") or 0.0,
        "spend": sp,
        "cogs": cogs,
        "transport": tr,
        "contrib": contrib,
        "margin": (contrib / rev * 100) if rev else 0.0,
        "mer": (rev / sp) if sp else 0.0,
        "roas": (rev / sp) if sp else 0.0,   # MER si ROAS coincid aici (spend = total ads)
        "cpa": (sp / o) if o else 0.0,
        "aov": (rev / o) if o else 0.0,
    }


def fmt(n):
    return "{:,.0f}".format(n)


def f1(n):
    return "{:,.1f}".format(n)


def print_table(rows, title):
    rows = sorted(rows, key=lambda r: r["contrib"], reverse=True)
    print("=== %s ===" % title)
    hdr = "%-16s%6s%10s%10s%9s%9s%11s%6s%6s%6s%7s%6s" % (
        "brand", "cmd", "venit", "ads", "COGS", "transp", "CONTRIB", "mer", "cpa", "aov", "marja%", "roas")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print("%-16s%6d%10s%10s%9s%9s%11s%6s%6s%6s%7s%6s" % (
            r["brand"][:16], r["orders"], fmt(r["rev"]), fmt(r["spend"]),
            fmt(r["cogs"]), fmt(r["transport"]), fmt(r["contrib"]),
            f1(r["mer"]), fmt(r["cpa"]), fmt(r["aov"]), f1(r["margin"]), f1(r["roas"])))
    # totaluri portofoliu
    t = consolidate(rows, "PORTOFOLIU")
    print("-" * len(hdr))
    print("%-16s%6d%10s%10s%9s%9s%11s%6s%6s%6s%7s%6s" % (
        "TOTAL", t["orders"], fmt(t["rev"]), fmt(t["spend"]), fmt(t["cogs"]),
        fmt(t["transport"]), fmt(t["contrib"]), f1(t["mer"]), fmt(t["cpa"]),
        fmt(t["aov"]), f1(t["margin"]), f1(t["roas"])))
    profitable = sum(1 for r in rows if r["contrib"] > 0)
    print("\n%d branduri active | %d profitabile | %d in pierdere" % (
        len(rows), profitable, len(rows) - profitable))


def consolidate(rows, name):
    agg = {"rev": 0.0, "fb": 0.0, "tk": 0.0, "ggl": 0.0, "spend": 0.0,
           "cogs": 0.0, "transport": 0.0, "orders": 0}
    for r in rows:
        for k in ("rev", "fb", "tk", "ggl", "spend", "cogs", "transport", "orders"):
            agg[k] += r[k]
    contrib = agg["rev"] - agg["cogs"] - agg["transport"] - agg["spend"]
    return {
        "brand": name, "orders": agg["orders"], "rev": agg["rev"],
        "fb": agg["fb"], "tk": agg["tk"], "ggl": agg["ggl"], "spend": agg["spend"],
        "cogs": agg["cogs"], "transport": agg["transport"], "contrib": contrib,
        "margin": (contrib / agg["rev"] * 100) if agg["rev"] else 0.0,
        "mer": (agg["rev"] / agg["spend"]) if agg["spend"] else 0.0,
        "roas": (agg["rev"] / agg["spend"]) if agg["spend"] else 0.0,
        "cpa": (agg["spend"] / agg["orders"]) if agg["orders"] else 0.0,
        "aov": (agg["rev"] / agg["orders"]) if agg["orders"] else 0.0,
        "days": 0,
    }


def print_consolidated(rows, date_from, date_to):
    t = consolidate(rows, "ARONA (consolidat)")
    print("=== P&L CONSOLIDAT ARONA  %s -> %s ===" % (date_from, date_to))
    print("  Branduri active:        %12d" % len(rows))
    print("  Comenzi:                %12s" % fmt(t["orders"]))
    print("  Venit (cu TVA):         %12s RON" % fmt(t["rev"]))
    print("  -")
    print("  Ads FB:                 %12s" % fmt(t["fb"]))
    print("  Ads Google:             %12s" % fmt(t["ggl"]))
    print("  Ads TikTok:             %12s" % fmt(t["tk"]))
    print("  Total ads:              %12s" % fmt(t["spend"]))
    print("  COGS:                   %12s" % fmt(t["cogs"]))
    print("  Transport:              %12s" % fmt(t["transport"]))
    print("  =")
    print("  PROFIT CONTRIBUTIE:     %12s RON" % fmt(t["contrib"]))
    print("  Marja contributie:      %12s %%" % f1(t["margin"]))
    print("  MER (venit/ads):        %12s" % f1(t["mer"]))
    print("  ROAS:                   %12s" % f1(t["roas"]))
    print("  CPA:                    %12s" % fmt(t["cpa"]))
    print("  AOV:                    %12s" % fmt(t["aov"]))


def print_today(rows_y, rows_m, yday, first_m, today):
    """Snapshot o linie / brand: ieri + MTD."""
    by_m = {r["brand"]: r for r in rows_m}
    order = sorted(set([r["brand"] for r in rows_m] + [r["brand"] for r in rows_y]),
                   key=lambda b: -(by_m.get(b, {}).get("contrib", 0)))
    by_y = {r["brand"]: r for r in rows_y}
    print("=== SNAPSHOT %s | IERI %s | MTD %s->%s ===" % (today, yday, first_m, yday))
    hdr = "%-16s | %s | %s" % ("brand",
                               "IERI: cmd venit ads contrib",
                               "MTD: venit ads CONTRIB mer")
    print(hdr)
    print("-" * 92)
    for b in order:
        y = by_y.get(b)
        m = by_m.get(b)
        if not m and not y:
            continue
        ys = "%4d %8s %7s %8s" % (
            (y["orders"] if y else 0), fmt(y["rev"] if y else 0),
            fmt(y["spend"] if y else 0), fmt(y["contrib"] if y else 0))
        ms = "%9s %8s %9s %4s" % (
            fmt(m["rev"] if m else 0), fmt(m["spend"] if m else 0),
            fmt(m["contrib"] if m else 0), f1(m["mer"] if m else 0))
        print("%-16s | %s | %s" % (b[:16], ys, ms))
    ty = consolidate(rows_y, "T") if rows_y else None
    tm = consolidate(rows_m, "T")
    print("-" * 92)
    if ty:
        print("%-16s | %4d %8s %7s %8s | %9s %8s %9s %4s" % (
            "TOTAL", ty["orders"], fmt(ty["rev"]), fmt(ty["spend"]), fmt(ty["contrib"]),
            fmt(tm["rev"]), fmt(tm["spend"]), fmt(tm["contrib"]), f1(tm["mer"])))


def main():
    ap = argparse.ArgumentParser(description="P&L all-in pentru brandurile Arona (daily_perf.db)")
    ap.add_argument("--brands", default="all", help="all sau csv: esteban,gt,nubra,belasil...")
    ap.add_argument("--from", dest="dfrom", help="data start YYYY-MM-DD")
    ap.add_argument("--to", dest="dto", help="data final YYYY-MM-DD")
    ap.add_argument("--consolidated", action="store_true", help="un singur P&L pe toata compania")
    ap.add_argument("--today", action="store_true", help="snapshot: ieri + MTD, o linie/brand")
    a = ap.parse_args()

    today = datetime.date.today()

    if a.today:
        yday = today - datetime.timedelta(days=1)
        first_m = today.replace(day=1)
        rows_y = [enrich(r) for r in run_remote(yday.isoformat(), yday.isoformat(), "all", None)]
        rows_m = [enrich(r) for r in run_remote(first_m.isoformat(), yday.isoformat(), "all", None)]
        print_today(rows_y, rows_m, yday.isoformat(), first_m.isoformat(), today.isoformat())
        return

    # interval implicit = luna curenta pana azi
    dfrom = a.dfrom or today.replace(day=1).isoformat()
    dto = a.dto or today.isoformat()
    mode, frags = resolve_brands(a.brands)
    raw = run_remote(dfrom, dto, mode, frags)
    rows = [enrich(r) for r in raw]
    if not rows:
        print("Nicio activitate pentru selectia data (%s -> %s, branduri=%s)." % (dfrom, dto, a.brands))
        return

    if a.consolidated:
        print_consolidated(rows, dfrom, dto)
    else:
        scope = "TOATE brandurile" if mode == "all" else ", ".join(frags)
        print_table(rows, "P&L Arona  %s -> %s  (%s)" % (dfrom, dto, scope))


if __name__ == "__main__":
    main()
