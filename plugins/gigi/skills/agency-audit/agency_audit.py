# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary"]
# ///
"""
Agency accountability auditor — the agency runs Meta + TikTok; this checks whether
they spend our money WELL, on OUR real economics. Reads the per-brand P&L from
cache.daily_brand_pnl (warehouse metrics — mirror of daily_perf.db, refreshed by
gigi:metrics-cache on cron), the same source as gigi:multi-brand-pnl. Compares agency
spend (fb_spend + tk_spend) against REAL contribution margin (revenue - COGS - transport
- all ad spend) — not the vanity ROAS the agency reports — with week-over-week deltas.

Read-only. Was SSH→daily_perf.db; now reads the cache (same numbers, no SSH).
Check freshness with: gigi:metrics-cache --status (cache.freshness).

Usage:
    uv run agency_audit.py                 # last 7 days vs prior 7
    uv run agency_audit.py --days 30
    uv run agency_audit.py --from 2026-06-01 --to 2026-06-14
"""
import argparse, datetime as dt, json, os, subprocess, sys
from pathlib import Path

# shared Postgres/secret helper — core/scripts/arona_pg.py
_here = Path(__file__).resolve()
for _up in range(2, 8):
    _cand = _here.parents[_up] / "core" / "scripts"
    if (_cand / "arona_pg.py").exists():
        sys.path.insert(0, str(_cand)); break
import arona_pg
secret = arona_pg.secret


def run_remote(dfrom, dto):
    sql = (
        "SELECT brand_name AS brand, SUM(orders)::int o, SUM(revenue)::float8 rev, "
        "SUM(fb_spend)::float8 fb, SUM(tk_spend)::float8 tk, SUM(google_spend)::float8 ggl, "
        "SUM(total_spend)::float8 sp, SUM(cogs)::float8 cogs, SUM(transport)::float8 tr, "
        "SUM(contribution_margin)::float8 profit, COUNT(DISTINCT date)::int days "
        "FROM cache.daily_brand_pnl WHERE date >= %s AND date <= %s GROUP BY brand_name "
        "HAVING (SUM(fb_spend)+SUM(tk_spend)) > 0 ORDER BY (SUM(fb_spend)+SUM(tk_spend)) DESC")
    try:
        conn = arona_pg.connect("DATABASE_URL_METRICS")
    except Exception as e:
        sys.exit("EROARE conexiune metrics: " + str(e)[:200])
    cur = conn.cursor()
    cur.execute(sql, [dfrom, dto])
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.close()
    return rows

def enrich(r):
    rev=r.get("rev") or 0.0; fb=r.get("fb") or 0.0; tk=r.get("tk") or 0.0
    sp=r.get("sp") or 0.0; cogs=r.get("cogs") or 0.0; tr=r.get("tr") or 0.0; o=r.get("o") or 0
    agency=fb+tk
    contrib=rev-cogs-tr-sp
    return {"brand":r["brand"],"agency":agency,"fb":fb,"tk":tk,"google":r.get("ggl") or 0.0,
            "spend":sp,"rev":rev,"orders":o,"contrib":contrib,
            "cm_margin":(contrib/rev*100) if rev else 0.0,
            "agency_share":(agency/sp*100) if sp else 0.0,
            "mer":(rev/sp) if sp else 0.0,
            "agency_mer":(rev/agency) if agency else 0.0}

def main():
    ap=argparse.ArgumentParser(description="Agency (Meta+TikTok) accountability auditor.")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--from", dest="dfrom"); ap.add_argument("--to", dest="dto")
    a=ap.parse_args()
    if a.dfrom and a.dto:
        cur_f, cur_t = a.dfrom, a.dto; prev_f=prev_t=None
    else:
        end=dt.date.today()-dt.timedelta(days=1)
        cur_f=(end-dt.timedelta(days=a.days-1)).isoformat(); cur_t=end.isoformat()
        prev_f=(end-dt.timedelta(days=2*a.days-1)).isoformat(); prev_t=(end-dt.timedelta(days=a.days)).isoformat()

    cur={e["brand"]:e for e in map(enrich, run_remote(cur_f, cur_t))}
    prev={e["brand"]:e for e in map(enrich, run_remote(prev_f, prev_t))} if prev_f else {}

    print(f"\nAUDIT AGENȚIE (Meta+TikTok) — {cur_f}..{cur_t}" + (f"  vs {prev_f}..{prev_t}" if prev_f else ""))
    print(f"{'='*92}")
    print("  ⚠ CM = ESTIMAT (daily_perf: venit BRUT cu TVA, TOATE comenzile) — supraestimeaza.")
    print("    Profit REAL livrat-fara-TVA per brand (lunar): gigi:multi-brand-pnl.")
    print(f"  {'Brand':<16}{'Agency RON':>11}{'%spend':>7}{'Revenue':>11}{'CM real':>11}{'CM%':>7}{'MER':>6}  flags")
    tot_ag=tot_contrib=0.0
    for b,e in sorted(cur.items(), key=lambda x:-x[1]["agency"]):
        tot_ag+=e["agency"]; tot_contrib+=e["contrib"]
        flags=[]
        if e["contrib"]<0: flags.append("🔴 PIERDERE (CM real negativ)")
        elif e["cm_margin"]<8: flags.append("🟡 marjă subțire")
        if e["agency_share"]>60 and e["mer"]<2.5: flags.append("🟡 dependent agenție + MER slab")
        p=prev.get(b)
        if p:
            if e["agency"]>p["agency"]*1.1 and e["contrib"]<p["contrib"]:
                flags.append("🟡 spend↑ dar profit↓ WoW")
            if e["cm_margin"]-p["cm_margin"]<-5: flags.append(f"🟡 marjă -{p['cm_margin']-e['cm_margin']:.0f}pp WoW")
        print(f"  {b[:16]:<16}{e['agency']:>11,.0f}{e['agency_share']:>6.0f}%{e['rev']:>11,.0f}{e['contrib']:>11,.0f}{e['cm_margin']:>6.0f}%{e['mer']:>6.1f}  {', '.join(flags)}")
    print(f"{'-'*92}")
    print(f"  {'TOTAL':<16}{tot_ag:>11,.0f}{'':>7}{'':>11}{tot_contrib:>11,.0f}")
    print(f"\n  CM real = revenue − COGS − transport − TOT ads (nu ROAS-ul platformă al agenției).")
    print(f"  🔴 = brand pe pierdere pe ansamblu; agenția arde buget. Cere-le justificare + tăiere/restructurare.")

if __name__ == "__main__":
    main()
