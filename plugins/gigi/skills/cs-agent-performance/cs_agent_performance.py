# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
cs_agent_performance.py — volum + PROFITABILITATE per agent de Customer Service, pe baza
comenzilor PLASATE de ei în Shopify (tag-uri CS: Raluca/Oana/Andra/Anna/OanaO — ca în tool-ul
CS din aplicația Scripturi). Profit cu FORMULA din Scripturi (api/profitability.py): venit pe
livrate − COGS − transport (colete plecate × cost/colet), fără TVA. Comenzile CS sunt manuale
(fără reclamă), deci profit = contribuția directă. NU scrie nimic.

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
        wexpr = "substr(created_at,1,7)=?"; wval = a.month; label = a.month
    else:
        cutoff = (datetime.date.today() - datetime.timedelta(days=a.days)).isoformat()
        wexpr = "substr(created_at,1,10)>=?"; wval = cutoff; label = "ultimele %d zile" % a.days
    py = (
        "import sqlite3,json;c=sqlite3.connect('data/profitability.db');"
        "row=c.execute(\"SELECT value FROM profit_settings WHERE key='cs_tags'\").fetchone();"
        "tags=json.loads(row[0]) if row else ['Raluca','Oana','Andra','Anna','OanaO'];"
        "wval=" + repr(wval) + ";"
        "q='SELECT prefix,currency,month,status_category,COUNT(*),SUM(revenue),SUM(cogs) FROM profit_orders "
        "WHERE tags LIKE ? AND " + wexpr + " GROUP BY prefix,currency,month,status_category';"
        "res={t:c.execute(q,('%'+t+'%',wval)).fetchall() for t in tags};"
        "tc=[[r[0],r[1],r[2]] for r in c.execute('SELECT month,prefix,cost_per_parcel FROM profit_transport_costs')];"
        "fx=[[r[0],r[1],r[2]] for r in c.execute('SELECT month,currency,rate_to_ron FROM profit_exchange_rates')];"
        "st={r[0]:r[1] for r in c.execute('SELECT key,value FROM profit_settings')};"
        "print(json.dumps({'tags':tags,'res':res,'tc':tc,'fx':fx,'cmap':st.get('country_map','{}'),'vat':st.get('vat_rates','{}')}))"
    )
    cmd = "cd /root/Scripturi && .venv/bin/python3 -c " + shlex.quote(py)
    out = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", VPS, cmd],
                         capture_output=True, text=True, timeout=90).stdout.strip()
    try:
        d = json.loads(out.splitlines()[-1])
    except Exception:
        print("Eroare la citirea datelor:", out[:200]); return
    tc = {(r[0], r[1]): r[2] for r in d["tc"]}
    fx = {(r[0], r[1]): r[2] for r in d["fx"]}
    cmap = json.loads(d["cmap"] or "{}"); vat = json.loads(d["vat"] or "{}")
    PLECATE = ("Livrata", "Refuzata", "In curs de livrare")
    rows = []
    for t in d["tags"]:
        n = liv = ref = 0; venit = profit = 0.0
        for pfx, cur, mon, sc, cnt, srev, scogs in d["res"][t]:
            cnt = int(cnt or 0); srev = float(srev or 0); scogs = float(scogs or 0)
            vr = vat.get(cmap.get(pfx, "RO"), 0.21)
            n += cnt
            rev = cog = 0.0
            if sc == "Livrata":
                liv += cnt; rev = srev * fx.get((mon, cur), 1.0); cog = scogs; venit += rev / (1 + vr)
            elif sc == "Refuzata":
                ref += cnt
            tr = cnt * tc.get((mon, pfx), 13) if sc in PLECATE else 0
            profit += (rev - cog - tr) / (1 + vr)
        rows.append((t, n, liv, ref, venit, profit))
    print("=== AGENȚI CS: volum + PROFIT (comenzi plasate) — %s ===" % label)
    print("Profit = formula Scripturi: venit livrate − COGS − transport, fără TVA (comenzi manuale, fără reclamă).\n")
    print("%-9s %8s %8s %7s %12s %12s %7s" % ("agent", "plasate", "livrate", "refuz", "venit_noTVA", "PROFIT", "marja"))
    print("-" * 70)
    tp = tv = 0
    for t, n, liv, ref, venit, profit in sorted(rows, key=lambda x: -x[5]):
        m = (profit / venit * 100) if venit else 0
        print("%-9s %8d %8d %7d %12s %12s %6.0f%%" % (t, n, liv, ref, "{:,.0f}".format(venit), "{:,.0f}".format(profit), m))
        tp += profit; tv += venit
    print("-" * 70)
    print("%-9s %26s %12s %12s %6.0f%%" % ("TOTAL", "{:,.0f}".format(tv), "{:,.0f}".format(tp), "", (tp / tv * 100 if tv else 0)))


if __name__ == "__main__":
    main()
