# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary", "paramiko"]
# ///
"""
multi_brand_pnl.py — P&L live "all-in" pentru ORICARE sau TOATE cele 16+ branduri Arona
(Esteban, George Talent, Nubra, Bonhaus RO/CZ/PL/BG/SK, Ofertele Zilei, Reduceri bune,
Magdeal, Belasil, Gento, Carpetto, Covoria, Nocturna, Rossi Nails, Apreciat...) pentru un
interval de date.

SURSA DEFAULT = cache.brand_pnl_monthly = profitul REAL din engine-ul de profitabilitate
Scripturi (api/profitability.py): venit = comenzi LIVRATE, FARA TVA, minus COGS + transport
(colete plecate x cost) + marketing. Granularitate LUNARA. Reimprospatat de gigi:metrics-cache
(--table brand_pnl_real) pe cron, ruland engine-ul real pe VPS.

--estimat / --today => sursa VECHE cache.daily_brand_pnl (oglinda daily_perf): venit BRUT cu TVA,
TOATE comenzile, split FB/Google/TikTok, zilnic. SUPRAESTIMEAZA profitul (nu tine cont de livrare
/ TVA) — buna doar pt tendinta zilnica si defalcarea pe platforme, NU pt profitul real.

Citeste DOAR (SELECT). Verifica prospetimea cu: gigi:metrics-cache --status (brand_pnl_real).

Folosire:
  uv run multi_brand_pnl.py --brands all --from 2026-05-01 --to 2026-05-31 --consolidated  # profit REAL
  uv run multi_brand_pnl.py --brands esteban,nubra,gt --from 2026-04-01 --to 2026-05-31
  uv run multi_brand_pnl.py --today                 # snapshot zilnic (estimat daily_perf)
  uv run multi_brand_pnl.py --estimat --from 2026-06-01 --to 2026-06-11   # vedere veche zilnica
"""
import sys
import os
import json
import argparse
import datetime
import subprocess
from pathlib import Path

# shared Postgres/secret helper — core/scripts/arona_pg.py (env-first secret + clean_dsn + connect)
_here = Path(__file__).resolve()
for _up in range(2, 8):
    _cand = _here.parents[_up] / "core" / "scripts"
    if (_cand / "arona_pg.py").exists():
        sys.path.insert(0, str(_cand)); break
import arona_pg
secret = arona_pg.secret

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
    try:
        conn = arona_pg.connect("DATABASE_URL_METRICS")
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


# ═══════════════════════════════════════════════════════════════════════
# SURSA CANONICA (default): cache.brand_pnl_monthly — profitul REAL din engine-ul
# de profitabilitate Scripturi (venit = comenzi LIVRATE, FARA TVA, minus COGS +
# transport + marketing). Granularitate LUNARA. daily_brand_pnl (--estimat) e
# oglinda daily_perf = venit brut, toate comenzile, cu TVA → supraestimeaza profitul.
# ═══════════════════════════════════════════════════════════════════════
def months_in_range(dfrom, dto):
    """Lista lunilor YYYY-MM acoperite de interval (inclusiv capetele)."""
    y, m = int(dfrom[:4]), int(dfrom[5:7])
    ey, em = int(dto[:4]), int(dto[5:7])
    out = []
    while (y, m) <= (ey, em):
        out.append("%04d-%02d" % (y, m))
        m += 1
        if m == 13:
            m = 1; y += 1
    return out


def run_canonical(months, mode, frags):
    where = ["month = ANY(%s)"]
    params = [months]
    if mode == "list" and frags:
        where.append("(" + " OR ".join(["LOWER(brand_name) LIKE %s"] * len(frags)) + ")")
        params += ["%" + f + "%" for f in frags]
    sql = (
        "SELECT brand_name AS brand, SUM(delivered_orders)::int o, "
        "SUM(revenue_exvat)::float8 rev, SUM(cogs_exvat)::float8 cogs, "
        "SUM(transport_exvat)::float8 tr, SUM(marketing)::float8 sp, "
        "SUM(net_profit)::float8 profit, COUNT(DISTINCT month)::int months, "
        "SUM(sent_parcels)::int sent "
        "FROM cache.brand_pnl_monthly WHERE " + " AND ".join(where) + " "
        "GROUP BY brand_name HAVING SUM(revenue_exvat) > 0 OR SUM(marketing) > 0 "
        "ORDER BY brand_name")
    try:
        conn = arona_pg.connect("DATABASE_URL_METRICS")
    except Exception as e:
        sys.exit("EROARE conexiune metrics: " + str(e)[:200])
    cur = conn.cursor()
    cur.execute(sql, params)
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.close()
    return rows


# ═══════════════════════════════════════════════════════════════════════
# --range : profit REAL pe o FEREASTRĂ EXACTĂ de zile (ex. 1–15 iunie), nu lună întreagă.
# Rulează engine-ul canonic (api/profitability.get_report) DIRECT pe VPS cu from_date/to_date —
# venit/COGS/transport filtrate pe created_at, marketing însumat DOAR pe fereastră (fix 2026-06).
# Granularitate = zi. Atenție: fereastră RECENTĂ = livrare încă neașezată (comenzi „în curs"),
# deci profitul livrat e încă incomplet → raportăm și gradul de așezare (livrate/plecate).
# ═══════════════════════════════════════════════════════════════════════
PREFIX_NAME = {
    "EST": "esteban", "GT": "george talent", "NUB": "nubra", "GRAN": "grandia", "BELA": "belasil",
    "CARP": "carpetto", "COV": "covoria", "OFER": "ofertele zilei", "MAG": "magdeal", "NOC": "nocturna",
    "APR": "apreciat", "RED": "reduceri bune", "GEN": "gento", "PAT": "ce pat ai", "ROSSI": "rossi nails",
    "BON": "bonhaus", "CZ": "bonhaus cz", "PL": "bonhaus pl", "BG": "bonhaus bg", "BONBG": "bonhaus bg",
    "LUX": "nocturna lux",
}

RANGE_REMOTE = r'''
import sys, os, asyncio, json
sys.path.insert(0, "/root/Scripturi"); os.chdir("/root/Scripturi")
from api.profitability import get_report
async def main():
    r = await get_report(month=os.environ["WM"], from_date=os.environ["WF"], to_date=os.environ["WT"])
    deliv = {d["prefix"]: d for d in (r.get("deliverability") or [])}
    out = []
    for p in (r.get("profitability") or []):
        pfx = p.get("prefix", ""); d = deliv.get(pfx, {})
        inc = p.get("incasari_fara_tva") or 0; cg = p.get("cogs_fara_tva") or 0
        tr = p.get("transport_fara_tva") or 0; mk = p.get("marketing_fara_tva") or 0
        out.append({"prefix": pfx, "livrata": d.get("livrata", 0), "in_curs": d.get("in_curs", 0),
                    "refuzata": d.get("refuzata", 0), "total": d.get("total", 0),
                    "plecate": p.get("plecate", 0), "rev": inc, "cogs": cg, "tr": tr, "mk": mk,
                    "net": inc - cg - tr - mk})
    print(json.dumps(out))
asyncio.run(main())
'''


def run_canonical_range(dfrom, dto, frags):
    """Profit REAL pe fereastra exactă [dfrom, dto] via engine-ul de pe VPS. Listă dict/prefix."""
    env = {"WM": ",".join(months_in_range(dfrom, dto)), "WF": dfrom, "WT": dto}
    base = os.environ.get("SCRIPTURI_DIR") or "/root/Scripturi"
    pybin = os.path.join(base, ".venv", "bin", "python")
    if os.path.exists(os.path.join(base, "api", "profitability.py")) and os.path.exists(pybin):
        # pe VPS: rulează engine-ul în venv-ul lui
        with open("/tmp/_range_pnl.py", "w") as f:
            f.write(RANGE_REMOTE)
        r = subprocess.run([pybin, "/tmp/_range_pnl.py"], capture_output=True, text=True,
                           timeout=900, cwd=base, env={**os.environ, **env})
        if r.returncode != 0:
            sys.exit("EROARE engine pe VPS: " + (r.stderr or "")[:400])
        data = r.stdout
    else:
        # off-VPS: SSH la VPS (paramiko + secrete PROFIT_SSH_*)
        import paramiko
        pwd = secret("PROFIT_SSH_PASS")
        if not pwd:
            sys.exit("Lipsește PROFIT_SSH_PASS în KB — nu pot rula engine-ul canonic pe VPS.")
        cli = paramiko.SSHClient(); cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        cli.connect(secret("PROFIT_SSH_HOST") or "84.46.242.181",
                    username=secret("PROFIT_SSH_USER") or "root", password=pwd, timeout=30)
        sftp = cli.open_sftp()
        with sftp.open("/tmp/_range_pnl.py", "w") as f:
            f.write(RANGE_REMOTE)
        sftp.close()
        envstr = " ".join("%s=%s" % (k, v) for k, v in env.items())
        _, o, e = cli.exec_command(
            "cd /root/Scripturi && %s /root/Scripturi/.venv/bin/python /tmp/_range_pnl.py" % envstr, timeout=900)
        data = o.read().decode("utf-8", "replace"); err = e.read().decode().strip(); cli.close()
        if err and not data.strip():
            sys.exit("EROARE engine (VPS): " + err[:400])
    rows = json.loads(data) if data.strip() else []
    if frags:
        def match(r):
            nm = PREFIX_NAME.get(r["prefix"], r["prefix"]).lower()
            return any(fr in nm or fr in r["prefix"].lower() for fr in frags)
        rows = [r for r in rows if match(r)]
    return rows


def print_table_range(rows, dfrom, dto):
    rows = sorted(rows, key=lambda r: r["net"], reverse=True)
    print("=== P&L REAL pe FEREASTRĂ  %s → %s  (profit livrat fără TVA − COGS − transport − marketing) ===" % (dfrom, dto))
    hdr = "%-14s%7s%7s%7s%11s%10s%9s%9s%12s%7s%9s" % (
        "brand", "livr", "curs", "refuz", "venit(fT)", "mkt", "COGS", "transp", "PROFIT NET", "marja%", "asezat%")
    print(hdr); print("-" * len(hdr))
    tot = {"rev": 0.0, "cogs": 0.0, "tr": 0.0, "mk": 0.0, "net": 0.0, "livrata": 0, "in_curs": 0, "refuzata": 0, "plecate": 0}
    for r in rows:
        plecate = r.get("plecate") or 0
        asez = (r["livrata"] / plecate * 100) if plecate else 0.0
        marja = (r["net"] / r["rev"] * 100) if r["rev"] else 0.0
        nm = PREFIX_NAME.get(r["prefix"], r["prefix"])
        print("%-14s%7d%7d%7d%11s%10s%9s%9s%12s%7s%9s" % (
            nm[:14], r["livrata"], r["in_curs"], r["refuzata"], fmt(r["rev"]), fmt(r["mk"]),
            fmt(r["cogs"]), fmt(r["tr"]), fmt(r["net"]), f1(marja), f1(asez)))
        for k in tot:
            tot[k] += r.get(k, 0)
    print("-" * len(hdr))
    marja_t = (tot["net"] / tot["rev"] * 100) if tot["rev"] else 0.0
    asez_t = (tot["livrata"] / tot["plecate"] * 100) if tot["plecate"] else 0.0
    print("%-14s%7d%7d%7d%11s%10s%9s%9s%12s%7s%9s" % (
        "TOTAL", tot["livrata"], tot["in_curs"], tot["refuzata"], fmt(tot["rev"]), fmt(tot["mk"]),
        fmt(tot["cogs"]), fmt(tot["tr"]), fmt(tot["net"]), f1(marja_t), f1(asez_t)))
    if tot["in_curs"] > 0:
        print("\n⚠ %d comenzi încă in curs de livrare in fereastra — venitul LIVRAT e inca incomplet, deci"
              % tot["in_curs"])
        print("  PROFITUL NET real va CRESTE pe masura ce se aseaza livrarea (asezat% = livrate/plecate).")
        print("  Cu cat fereastra e mai recenta, cu atat numarul de acum e mai sub-evaluat.")


def enrich_canonical(r):
    rev = r.get("rev") or 0.0
    sp = r.get("sp") or 0.0
    o = r.get("o") or 0
    net = r.get("profit") or 0.0
    return {
        "brand": r["brand"], "months": r.get("months") or 0, "orders": o,
        "rev": rev, "spend": sp, "cogs": r.get("cogs") or 0.0, "transport": r.get("tr") or 0.0,
        "contrib": net, "margin": (net / rev * 100) if rev else 0.0,
        "mer": (rev / sp) if sp else 0.0, "roas": (rev / sp) if sp else 0.0,
        "cpa": (sp / o) if o else 0.0, "aov": (rev / o) if o else 0.0,
        "sent": r.get("sent") or 0,
    }


def consolidate_canonical(rows):
    agg = {"rev": 0.0, "spend": 0.0, "cogs": 0.0, "transport": 0.0, "orders": 0}
    for r in rows:
        for k in agg:
            agg[k] += r[k]
    net = agg["rev"] - agg["cogs"] - agg["transport"] - agg["spend"]
    return {
        "orders": agg["orders"], "rev": agg["rev"], "spend": agg["spend"], "cogs": agg["cogs"],
        "transport": agg["transport"], "contrib": net,
        "margin": (net / agg["rev"] * 100) if agg["rev"] else 0.0,
        "mer": (agg["rev"] / agg["spend"]) if agg["spend"] else 0.0,
        "cpa": (agg["spend"] / agg["orders"]) if agg["orders"] else 0.0,
        "aov": (agg["rev"] / agg["orders"]) if agg["orders"] else 0.0,
    }


def print_table_canonical(rows, title):
    rows = sorted(rows, key=lambda r: r["contrib"], reverse=True)
    print("=== %s ===" % title)
    print("(profit REAL: venit livrat FARA TVA - COGS - transport - marketing)")
    hdr = "%-16s%7s%11s%10s%9s%9s%12s%7s%6s" % (
        "brand", "livr", "venit(fT)", "mkt", "COGS", "transp", "PROFIT NET", "marja%", "mer")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print("%-16s%7d%11s%10s%9s%9s%12s%7s%6s" % (
            r["brand"][:16], r["orders"], fmt(r["rev"]), fmt(r["spend"]), fmt(r["cogs"]),
            fmt(r["transport"]), fmt(r["contrib"]), f1(r["margin"]), f1(r["mer"])))
    t = consolidate_canonical(rows)
    print("-" * len(hdr))
    print("%-16s%7d%11s%10s%9s%9s%12s%7s%6s" % (
        "TOTAL", t["orders"], fmt(t["rev"]), fmt(t["spend"]), fmt(t["cogs"]),
        fmt(t["transport"]), fmt(t["contrib"]), f1(t["margin"]), f1(t["mer"])))
    prof = sum(1 for r in rows if r["contrib"] > 0)
    print("\n%d branduri | %d profitabile | %d in pierdere" % (len(rows), prof, len(rows) - prof))


def print_consolidated_canonical(rows, months):
    t = consolidate_canonical(rows)
    print("=== P&L CONSOLIDAT ARONA (REAL)  luni: %s ===" % ", ".join(months))
    print("  (venit = comenzi LIVRATE, fara TVA; profit net all-in)")
    print("  Branduri:                %12d" % len(rows))
    print("  Comenzi livrate:         %12s" % fmt(t["orders"]))
    print("  Venit livrat (fara TVA): %12s RON" % fmt(t["rev"]))
    print("  Marketing:               %12s" % fmt(t["spend"]))
    print("  COGS:                    %12s" % fmt(t["cogs"]))
    print("  Transport:               %12s" % fmt(t["transport"]))
    print("  =")
    print("  PROFIT NET:              %12s RON" % fmt(t["contrib"]))
    print("  Marja neta:              %12s %%" % f1(t["margin"]))
    print("  MER (venit/mkt):         %12s" % f1(t["mer"]))
    print("  CPA:                     %12s" % fmt(t["cpa"]))
    print("  AOV:                     %12s" % fmt(t["aov"]))


def main():
    ap = argparse.ArgumentParser(description="P&L pentru brandurile Arona — DEFAULT: profit REAL (engine profitabilitate, lunar)")
    ap.add_argument("--brands", default="all", help="all sau csv: esteban,gt,nubra,belasil...")
    ap.add_argument("--from", dest="dfrom", help="data start YYYY-MM-DD (rotunjit la luni)")
    ap.add_argument("--to", dest="dto", help="data final YYYY-MM-DD (rotunjit la luni)")
    ap.add_argument("--consolidated", action="store_true", help="un singur P&L pe toata compania")
    ap.add_argument("--today", action="store_true", help="snapshot zilnic ieri + MTD (sursa estimat daily_perf)")
    ap.add_argument("--estimat", action="store_true",
                    help="sursa VECHE daily_perf (zilnic, BRUT cu TVA, toate comenzile, split FB/Google/TikTok) "
                         "in loc de profitul REAL lunar. Supraestimeaza — foloseste pt tendinta zilnica, nu pt profit.")
    ap.add_argument("--range", dest="rng", action="store_true",
                    help="profit REAL pe FEREASTRA EXACTA de zile (--from/--to), nu lună întreagă — rulează "
                         "engine-ul canonic pe VPS cu from_date/to_date. Arată și gradul de așezare a livrării.")
    a = ap.parse_args()

    today = datetime.date.today()

    # ---- sursa estimat (daily_perf): doar la cerere (--estimat / --today) ----
    if a.today:
        yday = today - datetime.timedelta(days=1)
        first_m = today.replace(day=1)
        rows_y = [enrich(r) for r in run_remote(yday.isoformat(), yday.isoformat(), "all", None)]
        rows_m = [enrich(r) for r in run_remote(first_m.isoformat(), yday.isoformat(), "all", None)]
        print("(SURSA ESTIMAT daily_perf — brut cu TVA, toate comenzile; nu e profitul real livrat)")
        print_today(rows_y, rows_m, yday.isoformat(), first_m.isoformat(), today.isoformat())
        return

    if a.estimat:
        dfrom = a.dfrom or today.replace(day=1).isoformat()
        dto = a.dto or today.isoformat()
        mode, frags = resolve_brands(a.brands)
        rows = [enrich(r) for r in run_remote(dfrom, dto, mode, frags)]
        if not rows:
            print("Nicio activitate (%s -> %s, branduri=%s)." % (dfrom, dto, a.brands)); return
        print("(SURSA ESTIMAT daily_perf — brut cu TVA, toate comenzile; pt profit REAL ruleaza fara --estimat)")
        if a.consolidated:
            print_consolidated(rows, dfrom, dto)
        else:
            scope = "TOATE brandurile" if mode == "all" else ", ".join(frags)
            print_table(rows, "P&L Arona (estimat)  %s -> %s  (%s)" % (dfrom, dto, scope))
        return

    # ---- --range: profit REAL pe FEREASTRA EXACTA de zile (engine pe VPS, granularitate ZI) ----
    if a.rng:
        dfrom = a.dfrom or today.replace(day=1).isoformat()
        dto = a.dto or today.isoformat()
        mode, frags = resolve_brands(a.brands)
        rows = run_canonical_range(dfrom, dto, frags if mode == "list" else None)
        if not rows:
            print("Nicio comandă în fereastra %s → %s (branduri=%s)." % (dfrom, dto, a.brands)); return
        print_table_range(rows, dfrom, dto)
        print("\n(profit REAL livrat-fără-TVA pe fereastra de zile; marketing = spend DOAR pe fereastră. "
              "Pt P&L lunar canonic rulează fără --range.)")
        return

    # ---- DEFAULT: profit REAL canonic (cache.brand_pnl_monthly), granularitate lunara ----
    dfrom = a.dfrom or today.replace(day=1).isoformat()
    dto = a.dto or today.isoformat()
    months = months_in_range(dfrom, dto)
    mode, frags = resolve_brands(a.brands)
    rows = [enrich_canonical(r) for r in run_canonical(months, mode, frags)]
    if not rows:
        print("Nicio activitate pentru selectie (luni=%s, branduri=%s). "
              "Cache gol? Verifica: gigi:metrics-cache --status (brand_pnl_real)." % (", ".join(months), a.brands))
        return

    if a.consolidated:
        print_consolidated_canonical(rows, months)
    else:
        scope = "TOATE brandurile" if mode == "all" else ", ".join(frags)
        print_table_canonical(rows, "P&L REAL Arona  luni: %s  (%s)" % (", ".join(months), scope))
    print("\n(profit REAL livrat-fara-TVA, granularitate LUNARA. Luna curenta = incompleta "
          "[comenzi inca nelivrate]. Pt tendinta zilnica/split FB-Google-TikTok: --estimat.)")


if __name__ == "__main__":
    main()
