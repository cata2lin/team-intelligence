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
# „HA" = TOATE magazinele care vind HA, adica exact ce face eticheta `skus LIKE 'HA-%'` (decizia
# ownerului prin Orchestrator, 23-oct-2026). NU tinem lista aici: ar minti. Prima versiune a acestui
# comentariu enumera 9 magazine, din doua luni masurate — pe trei luni sint 13 (MAG, OFER, BON, RED,
# CZ, BUC, BONBG, GRAN, PL, HU, SK, APR, ORC), si se schimba de la lună la lună. Raportul o deriva
# singur, la fiecare rulare, in defalcarea pe magazin a liniei HA.

# BAZA DE ALOCARE A MARKETINGULUI pentru linia HA. Principiul ownerului (23-oct-2026): unde venitul
# HA intra in linie, intra si partea lui de marketing — din override-ul fiecarui magazin se da lui HA
# o cota egala cu procentul lui de colete HA.
#
# NU e o lista codificata, dinadins: se DERIVA din date (vezi `ha_share`), adica toate magazinele cu
# colete HA, MINUS cele raportate ca linie separata (`--prefixes`, de obicei GRAN). Altfel marketingul
# Grandiei ar fi numarat o data la linia GRAN si partial din nou la HA — dublare.
#
# Lista veche era `["MAG","OFER","RED","BON"]`, cu comentariul „magazinele unde se vand SKU-urile
# HA-*" — care NU era ce facea variabila, si oricum era incompleta: HA se vinde pe 13 magazine. Cu
# baza derivata, trec in alocare si CZ (96.382 lei), Bucsa (93.943), BONBG (23.830), PL (14.911),
# SK (5.017), HU (3.052), Apreciat (1.536) si ORC (830).

SHIPPED = ("Livrata", "In curs de livrare", "Refuzata")

# core/scripts in orice layout de instalare (clona repo, marketplace, plugin-cache
# core/<commit>/scripts). GARDA: iteram parents (fara index fix => fara IndexError) si
# preferam core-ul din ACELASI commit ca skill-ul.
def _core_scripts(need="arona_ssh.py"):
    from pathlib import Path
    h = Path(__file__).resolve()
    c = [Path(os.environ["ARONA_CORE_SCRIPTS"])] if os.environ.get("ARONA_CORE_SCRIPTS") else []
    for up in h.parents:
        c += [up / "core" / "scripts", up / "plugins" / "core" / "scripts"] + \
             (sorted((up / "core").glob("*/scripts")) if (up / "core").is_dir() else [])
    ok = [x for x in c if (x / need).exists()]
    return next((x for x in ok if x.parent.name in h.parts), ok[0] if ok else None)


def remote(sql):
    """Interogheaza profitability.db de pe VPS. Autentificarea vine din helper-ul PARTAJAT
    (cheie -> ssh-agent -> parola ultim resort), deci NU mai e nevoie de `export PROFIT_SSH_PASS`."""
    cs = _core_scripts()
    if cs is None:
        sys.exit("core/scripts/arona_ssh.py negasit — actualizeaza plugin-urile echipei "
                 "sau seteaza ARONA_CORE_SCRIPTS=/cale/spre/plugins/core/scripts")
    if str(cs) not in sys.path:
        sys.path.insert(0, str(cs))
    import arona_ssh
    script = ("import sqlite3,json,os\n"
              "os.chdir('/root/Scripturi')\n"
              "p=sqlite3.connect('data/profitability.db'); p.row_factory=sqlite3.Row\n"
              f'print(json.dumps([dict(r) for r in p.execute("""{sql}""").fetchall()],default=str))\n')
    try:
        c = arona_ssh.connect()
    except arona_ssh.SSHAuthError as e:
        sys.exit(str(e))
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
      SELECT lbl, status_category st, COUNT(*) c, SUM(rev_lei) rev, SUM(cogs) cogs FROM (
        SELECT CASE WHEN o.skus LIKE 'HA-%' THEN 'HA'
                    WHEN o.prefix IN ({",".join("'%s'"%p for p in prefixes)}) THEN o.prefix
                    END lbl, o.status_category,
               -- ⚠️ UNITĂȚI AMESTECATE: `revenue` e în MONEDA MAGAZINULUI, iar `cogs` în LEI. Fără
               -- conversie, CZK și HUF (monede mici, sume brute uriașe) umflau venitul: găleata HA
               -- ieșea cu +320.637 lei pe septembrie (20,5%) și +414.954 pe octombrie (32,9%), fiindcă
               -- Bonhaus CZ e cel mai mare contribuitor la ea. COGS-ul NU se convertește: e deja în lei.
               o.revenue * COALESCE(fx.rate_to_ron, 1.0) rev_lei, o.cogs
        FROM profit_orders o
        LEFT JOIN profit_exchange_rates fx
               ON fx.month = o.month AND fx.currency = COALESCE(NULLIF(o.currency,''),'RON')
        WHERE o.month IN {mlist} AND {no_test.replace("tags", "o.tags")}
          AND (o.skus LIKE 'HA-%' OR o.prefix IN ({",".join("'%s'"%p for p in prefixes)})) )
      WHERE lbl IS NOT NULL GROUP BY lbl, st"""
    rows = remote(lines_sql)

    # 2. transport costs + marketing override (toate prefixele relevante)
    # cautam transportul si marketingul pe TOATE magazinele care pot intra in alocare
    allp = list(set(prefixes + [r["prefix"] for r in remote(
        f"SELECT DISTINCT prefix FROM profit_orders WHERE month IN {mlist} AND skus LIKE 'HA-%'")]))
    inp = "(" + ",".join("'%s'" % p for p in allp) + ")"
    tcost = {r["prefix"]: r["cost_per_parcel"] for r in remote(
        f"SELECT prefix, AVG(cost_per_parcel) cost_per_parcel FROM profit_transport_costs WHERE month IN {mlist} AND prefix IN {inp} GROUP BY prefix")}
    mkt = {r["prefix"]: r["amount"] for r in remote(
        f"SELECT prefix, SUM(amount) amount FROM profit_marketing_override WHERE month IN {mlist} AND prefix IN {inp} GROUP BY prefix")}
    # 3. alocare marketing HA: cota HA din plecate per deals-prefix
    # Baza derivata: orice magazin cu colete HA, mai putin cele raportate separat (ca sa nu dublam).
    _exclus = ",".join("'%s'" % p for p in prefixes) or "''"
    ha_share = remote(f"""SELECT prefix,
        SUM(CASE WHEN skus LIKE 'HA-%' AND status_category IN {tuple(SHIPPED)} THEN 1 ELSE 0 END) ha,
        SUM(CASE WHEN status_category IN {tuple(SHIPPED)} THEN 1 ELSE 0 END) tot
        FROM profit_orders WHERE month IN {mlist} AND {no_test} AND prefix NOT IN ({_exclus})
        GROUP BY prefix HAVING ha > 0""")

    # 3b. defalcarea liniei HA pe MAGAZIN: linia HA amesteca deals, Bonhaus CZ, Bucsa si Grandia,
    # iar pina acum nu se vedea de unde vine marja. Venitul se converteste in lei (vezi nota de unitati).
    ha_pe_magazin = remote(f"""
        SELECT o.prefix,
               SUM(CASE WHEN o.status_category = 'Livrata'
                        THEN o.revenue * COALESCE(fx.rate_to_ron, 1.0) ELSE 0 END) rev,
               SUM(CASE WHEN o.status_category = 'Livrata' THEN o.cogs ELSE 0 END) cogs,
               SUM(CASE WHEN o.status_category IN {tuple(SHIPPED)} THEN 1 ELSE 0 END) plecate
        FROM profit_orders o
        LEFT JOIN profit_exchange_rates fx
               ON fx.month = o.month AND fx.currency = COALESCE(NULLIF(o.currency,''),'RON')
        WHERE o.month IN {mlist} AND o.skus LIKE 'HA-%' AND {no_test.replace("tags", "o.tags")}
        GROUP BY o.prefix ORDER BY 2 DESC""")

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
        print(f"\n(marketing HA alocat = {ha_mkt:,.0f} lei — cota HA din override-ul a "
              f"{len([s for s in ha_share if s['tot']])} magazine cu colete HA, "
              f"fara cele raportate separat: {prefixes})")
        print("\n--- linia HA, de unde vine: pe magazin (livrate, ex-TVA) ---")
        print(f"  {'magazin':10}{'venit':>13}{'COGS':>12}{'marjă brută':>14}{'%':>7}{'plecate':>9}")
        for s in ha_pe_magazin:
            rv, cg = (s["rev"] or 0) / VAT, (s["cogs"] or 0) / VAT
            if rv <= 0 and not s["plecate"]:
                continue
            m = rv - cg
            print(f"  {s['prefix']:10}{rv:>13,.0f}{cg:>12,.0f}{m:>14,.0f}"
                  f"{(100.0*m/rv if rv else 0):>6.0f}%{s['plecate']:>9,}")
        print("  (marjă brută = venit − COGS, fără transport și marketing: alea se alocă doar pe "
              "linia HA întreagă, nu pe magazin)")

    # --- Return on inventory (din profit_inventory_value, dacă există snapshot) ---
    try:
        snap = remote("SELECT snapshot FROM profit_inventory_value ORDER BY snapshot DESC LIMIT 1")
        if snap:
            sn = snap[0]["snapshot"]
            inv = {r["line"]: r["stock_value"] for r in remote(
                f"SELECT line, stock_value FROM profit_inventory_value WHERE snapshot='{sn}'")}
            INV_MAP = {"HA": "HA", "GRAN": "Grandia"}
            print(f"\n--- Return on inventory (stoc la {sn}) ---")
            for lbl, *_, net in out:
                key = INV_MAP.get(lbl, lbl)
                sv = inv.get(key)
                if sv:
                    print(f"  {lbl:6} net {net:>10,.0f} / stoc {sv:>10,.0f} = {net/sv*100:>5.1f}% pe perioadă")
    except Exception:
        pass

    print("\nNotă: contribuție pre-overhead; HA marketing ALOCAT (per-SKU incomplet). "
          "Inventar din profit_inventory_value (snapshot din sheet-ul de inventar, un tab/lună). "
          "Containere HA care vin: tabel profit_container_ha.")

if __name__ == "__main__":
    main()
