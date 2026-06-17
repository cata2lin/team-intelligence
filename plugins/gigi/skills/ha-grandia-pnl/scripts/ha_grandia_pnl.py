# /// script
# requires-python = ">=3.10"
# dependencies = ["paramiko"]
# ///
"""
ha_grandia_pnl.py — NET P&L per "linie de business" din engine-ul de profitabilitate Scripturi.

Compară HA (linia de SKU-uri HA-* importate pe container, vândute COD prin deals stores)
vs Grandia (magazinul grandia.ro = prefix GRAN) — sau orice alt prefix — la NET, cu formula
canonică din api/profitability.py:  NET = (rev_livrat − COGS − transport)/1.21 − marketing.

Sursa: SQLite pe VPS (profit_orders), via SSH. Secrete in KB: PROFIT_SSH_HOST/USER/PASS.
  KB=~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py
  export PROFIT_SSH_HOST="$(uv run "$KB" secret-get PROFIT_SSH_HOST)"
  export PROFIT_SSH_USER="$(uv run "$KB" secret-get PROFIT_SSH_USER)"
  export PROFIT_SSH_PASS="$(uv run "$KB" secret-get PROFIT_SSH_PASS)"

Usage:
  uv run ha_grandia_pnl.py --months 2026-04,2026-05
  uv run ha_grandia_pnl.py --months 2026-05 --prefixes GRAN,EST,GT

Reguli (canonice, vezi memorie ha-vs-grandia-profit):
  * HA = skus LIKE 'HA-%'  (regex canonic HA-\\d{3,5}); Grandia = prefix 'GRAN'.
  * Exclude mereu comenzile cu tag 'test' (teste de funnel).
  * TVA RO 21% se scade din rev/COGS/transport; marketing e net.
  * transport = colete plecate (Livrata+In curs+Refuzata) × cost/colet (din profit_transport_costs, brut cu TVA).
  * marketing: prefix -> profit_marketing_override (= daily_perf). HA nu e prefix -> se ALOCĂ:
    pt fiecare deals-prefix care vinde HA, marketing_HA = (HA plecate / total plecate al prefixului) × override-ul prefixului.
"""
import argparse, json, os, sys
VAT = 1.21
DEALS_PREFIXES = ["MAG", "OFER", "RED", "BON"]   # magazinele unde se vand SKU-urile HA-*
SHIPPED = ("Livrata", "In curs de livrare", "Refuzata")

def remote(sql):
    import paramiko
    host = os.environ.get("PROFIT_SSH_HOST", "84.46.242.181")
    user = os.environ.get("PROFIT_SSH_USER", "root")
    pwd  = os.environ.get("PROFIT_SSH_PASS")
    if not pwd: sys.exit("Lipsă PROFIT_SSH_PASS (export din kb.py secret-get).")
    script = ("import sqlite3,json,os\n"
              "os.chdir('/root/Scripturi')\n"
              "p=sqlite3.connect('data/profitability.db'); p.row_factory=sqlite3.Row\n"
              f'print(json.dumps([dict(r) for r in p.execute("""{sql}""").fetchall()],default=str))\n')
    c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username=user, password=pwd, timeout=30)
    sftp = c.open_sftp()
    with sftp.open("/tmp/_pnl_q.py", "w") as f: f.write(script)
    sftp.close()
    _, out, err = c.exec_command("/root/Scripturi/.venv/bin/python /tmp/_pnl_q.py", timeout=120)
    o = out.read().decode(); e = err.read().decode().strip(); c.close()
    if e and not o: sys.exit(f"[remote] {e[:300]}")
    return json.loads(o)

def main():
    ap = argparse.ArgumentParser(description="NET P&L HA vs Grandia (sau orice prefix).")
    ap.add_argument("--months", default="2026-04,2026-05", help="luni CSV, ex. 2026-04,2026-05")
    ap.add_argument("--prefixes", default="GRAN", help="prefixe magazin de raportat alături de HA (CSV)")
    ap.add_argument("--no-ha", action="store_true", help="nu raporta linia HA")
    a = ap.parse_args()
    months = [m.strip() for m in a.months.split(",") if m.strip()]
    prefixes = [p.strip() for p in a.prefixes.split(",") if p.strip()]
    mlist = "(" + ",".join("'%s'" % m for m in months) + ")"

    no_test = "LOWER(COALESCE(tags,'')) NOT LIKE '%test%'"
    # 1. per linie (HA + prefixe), pe status: rev/cogs/plecate
    lines_sql = f"""
      SELECT lbl, status_category st, COUNT(*) c, SUM(revenue) rev, SUM(cogs) cogs FROM (
        SELECT CASE WHEN skus LIKE 'HA-%' THEN 'HA'
                    WHEN prefix IN ({",".join("'%s'"%p for p in prefixes)}) THEN prefix
                    END lbl, status_category, revenue, cogs
        FROM profit_orders WHERE month IN {mlist} AND {no_test}
          AND (skus LIKE 'HA-%' OR prefix IN ({",".join("'%s'"%p for p in prefixes)})) )
      WHERE lbl IS NOT NULL GROUP BY lbl, st"""
    rows = remote(lines_sql)

    # 2. transport costs + marketing override (toate prefixele relevante)
    allp = list(set(prefixes + DEALS_PREFIXES))
    inp = "(" + ",".join("'%s'" % p for p in allp) + ")"
    tcost = {r["prefix"]: r["cost_per_parcel"] for r in remote(
        f"SELECT prefix, AVG(cost_per_parcel) cost_per_parcel FROM profit_transport_costs WHERE month IN {mlist} AND prefix IN {inp} GROUP BY prefix")}
    mkt = {r["prefix"]: r["amount"] for r in remote(
        f"SELECT prefix, SUM(amount) amount FROM profit_marketing_override WHERE month IN {mlist} AND prefix IN {inp} GROUP BY prefix")}
    # 3. alocare marketing HA: cota HA din plecate per deals-prefix
    ha_share = remote(f"""SELECT prefix,
        SUM(CASE WHEN skus LIKE 'HA-%' AND status_category IN {tuple(SHIPPED)} THEN 1 ELSE 0 END) ha,
        SUM(CASE WHEN status_category IN {tuple(SHIPPED)} THEN 1 ELSE 0 END) tot
        FROM profit_orders WHERE month IN {mlist} AND prefix IN ({",".join("'%s'"%p for p in DEALS_PREFIXES)}) AND {no_test} GROUP BY prefix""")

    # agregare
    def blank(): return {"rev": 0.0, "cogs": 0.0, "plecate": 0}
    agg = {}
    for r in rows:
        d = agg.setdefault(r["lbl"], blank())
        if r["st"] == "Livrata":
            d["rev"] += r["rev"] or 0; d["cogs"] += r["cogs"] or 0
        if r["st"] in SHIPPED:
            d["plecate"] += r["c"]

    ha_mkt = sum((s["ha"] / s["tot"] * mkt.get(s["prefix"], 0)) for s in ha_share if s["tot"])

    lines = ([] if a.no_ha else ["HA"]) + prefixes
    print(f"NET P&L — luni {months} (livrate, fără test, TVA 21%)\n" + "="*64)
    out = []
    for lbl in lines:
        d = agg.get(lbl)
        if not d: print(f"  {lbl}: fără date"); continue
        cost = tcost.get(lbl, 13) if lbl != "HA" else 13   # HA = 13 (deals stores)
        transport = d["plecate"] * cost
        marketing = ha_mkt if lbl == "HA" else mkt.get(lbl, 0)
        rev_n, cogs_n, tr_n = d["rev"]/VAT, d["cogs"]/VAT, transport/VAT
        contrib = rev_n - cogs_n - tr_n
        net = contrib - marketing
        liv = None  # delivered count
        out.append((lbl, rev_n, cogs_n, tr_n, marketing, net))
        print(f"\n### {lbl}  (transport {cost}/colet, {d['plecate']} plecate)")
        print(f"  rev exTVA {rev_n:>12,.0f}  − COGS {cogs_n:>11,.0f}  − transport {tr_n:>10,.0f}")
        print(f"  = contribuție {contrib:>12,.0f}  − marketing {marketing:>11,.0f}")
        print(f"  = NET {net:>12,.0f}   ({net/rev_n*100:.1f}% margin)")
    if not a.no_ha:
        print(f"\n(marketing HA alocat = {ha_mkt:,.0f} lei — cotă HA din override-ul {DEALS_PREFIXES})")
    print("\nNotă: contribuție pre-overhead; HA marketing ALOCAT (per-SKU incomplet); capital de stoc neinclus (vezi sheet inventar).")

if __name__ == "__main__":
    main()
