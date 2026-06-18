# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "google-api-python-client>=2.0", "google-auth>=2.0"]
# ///
"""
Profit/contribuție PER SKU (și roll-up pe CATEGORIE) — reunește:
  - profit_order_lines (venit+COGS+buc pe linie, capturate cu prețul real) JOIN profit_orders (doar 'Livrata'),
  - transport: cost_per_parcel (profit_transport_costs per magazin) alocat pe linii proporțional cu venitul,
  - ex-TVA (÷(1+vat)),
  - marketing: metrics cache.product_ad_spend per SKU (luna).
→ contribuție = venit_exTVA − COGS − transport − marketing, per SKU și per categorie (SKU→Group din WMS).
Read-only pe profit_orders/engine. Secrete din ENV (run.env).
  cd /root/Scripturi && .venv/bin/python profit_by_sku.py 2026-05 [--db data/profitability.db]
"""
import os, sys, re, json, sqlite3, argparse
from collections import defaultdict

DEFAULT_VAT = {"RO": 0.21, "BG": 0.20, "CZ": 0.21, "PL": 0.23, "HU": 0.27, "SK": 0.20, "HR": 0.25}
# prefix → country (pt VAT). Restul = RO.
PREFIX_COUNTRY = {"BG": "BG", "BONBG": "BG", "CZ": "CZ", "PL": "PL", "LUX": "RO", "NOC": "RO"}
GIFT = {"surpriza", "cutie-cadou", "cad", "cadou", "gift", "surprise"}


def sku_to_group():
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
    sa = json.loads(os.environ["GA4_SA_JSON"])
    cr = Credentials.from_service_account_info(sa, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    svc = build("sheets", "v4", credentials=cr).spreadsheets()
    pg = svc.values().get(spreadsheetId=os.environ["NOMENCLATOR_SHEET_ID"], range="'Product Group'!A2:B").execute().get("values", [])
    return {r[0].strip(): r[1].strip() for r in pg if len(r) >= 2 and r[0].strip() and r[1].strip()}


def _clean(dsn):
    dsn = re.sub(r"([?&])(schema|channel_binding|pgbouncer|connection_limit)=[^&]*", r"\1", dsn)
    return re.sub(r"[?&]+(&|$)", r"\1", dsn).rstrip("?&")


def load_dpd_costs(path="data/dpd_nomenclator.json"):
    """SKU(upper) -> avg_transport_cost real (din auditul DPD). Gol dacă fișierul lipsește."""
    out = {}
    try:
        d = json.load(open(path, encoding="utf-8"))
        items = d if isinstance(d, list) else [{**v, "sku": k} for k, v in d.items()]
        for it in items:
            s = str(it.get("sku") or "").strip().upper()
            avg = float(it.get("avg_transport_cost") or 0)
            if s and avg > 0:
                out[s] = avg
    except Exception:
        pass
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("month"); ap.add_argument("--db", default="data/profitability.db")
    ap.add_argument("--top", type=int, default=25); a = ap.parse_args()
    s2g = sku_to_group()
    dpd = load_dpd_costs()   # SKU-uri cu cost transport REAL în DB (audit DPD)
    cx = sqlite3.connect(a.db); cx.row_factory = sqlite3.Row

    tc = {r["prefix"]: r["cost_per_parcel"] for r in cx.execute(
        "SELECT prefix, cost_per_parcel FROM profit_transport_costs WHERE month=?", (a.month,)).fetchall()}

    # delivered lines, joined to status
    rows = cx.execute("""
        SELECT pol.prefix, pol.order_name, pol.sku, pol.qty, pol.line_revenue, pol.line_cogs,
               po.revenue AS order_total
        FROM profit_order_lines pol
        JOIN profit_orders po ON po.month=pol.month AND po.prefix=pol.prefix AND po.order_name=pol.order_name
        WHERE pol.month=? AND po.status_category='Livrata'
    """, (a.month,)).fetchall()
    cx.close()

    # per-order: line-revenue total (for share) + order total (incl. transport ÎNCASAT de la client, ca engine-ul)
    oline = defaultdict(float); ototal = {}
    for r in rows:
        k = (r["prefix"], r["order_name"]); oline[k] += r["line_revenue"] or 0; ototal[k] = r["order_total"] or 0

    sku = defaultdict(lambda: [0, 0.0, 0.0, 0.0])  # qty, rev_exvat, cogs_exvat, transport_exvat
    for r in rows:
        if (r["sku"] or "").lower() in GIFT:
            continue
        country = PREFIX_COUNTRY.get(r["prefix"], "RO"); vat = DEFAULT_VAT.get(country, 0.21)
        k = (r["prefix"], r["order_name"]); olt = oline[k] or 1
        share = (r["line_revenue"] or 0) / olt
        # venit alocat = cota din TOTALUL comenzii (incl. transport încasat) → reconciliază cu engine; ex-TVA pe tot
        rev_ex = (ototal[k] * share) / (1 + vat)
        cogs_ex = (r["line_cogs"] or 0) / (1 + vat)
        tcost = tc.get(r["prefix"], 0) * share / (1 + vat)   # cost transport alocat, ex-TVA
        d = sku[r["sku"]]
        d[0] += r["qty"] or 0; d[1] += rev_ex; d[2] += cogs_ex; d[3] += tcost

    # marketing per sku (metrics)
    import psycopg2
    mk = defaultdict(float)
    try:
        pc = psycopg2.connect(_clean(os.environ["DATABASE_URL_METRICS"])); cur = pc.cursor()
        cur.execute("SELECT sku, SUM(spend_ron) FROM cache.product_ad_spend WHERE to_char(date,'YYYY-MM')=%s GROUP BY sku", (a.month,))
        for s, sp in cur.fetchall(): mk[s] += float(sp or 0)
        pc.close()
    except Exception as e:
        sys.stderr.write(f"[mkt] {type(e).__name__}; marketing=0\n")

    # per-SKU rows
    out = []
    for s, (q, rev, cg, tr) in sku.items():
        m = mk.get(s, 0.0)
        out.append((s, s2g.get(s, "?"), q, rev, cg, tr, m, rev - cg - tr - m))
    out.sort(key=lambda x: -x[3])

    # category rollup
    cat = defaultdict(lambda: [0, 0.0, 0.0, 0.0, 0.0])
    for s, g, q, rev, cg, tr, m, ct in out:
        d = cat[g]; d[0] += q; d[1] += rev; d[2] += cg; d[3] += tr; d[4] += m

    print(f"=== PROFIT PER CATEGORIE — {a.month} (venit exTVA, transport alocat, marketing din spend) ===")
    print(f"{'categorie':26}{'buc':>7}{'venit':>11}{'COGS':>10}{'transp':>9}{'mkt':>9}{'contrib':>11}")
    for g, (q, rev, cg, tr, m) in sorted(cat.items(), key=lambda x: -x[1][1]):
        print(f"{str(g)[:26]:26}{q:>7}{rev:>11.0f}{cg:>10.0f}{tr:>9.0f}{m:>9.0f}{rev-cg-tr-m:>11.0f}")
    print(f"\n=== TOP {a.top} SKU dupa venit ===")
    print(f"{'sku':30}{'categorie':16}{'buc':>6}{'venit':>10}{'COGS':>9}{'transp':>8}{'mkt':>8}{'contrib':>10}{'  transp?'}")
    for s, g, q, rev, cg, tr, m, ct in out[:a.top]:
        flag = "REAL" if str(s).upper() in dpd else "ESTIMAT"
        print(f"{str(s)[:30]:30}{str(g)[:16]:16}{q:>6}{rev:>10.0f}{cg:>9.0f}{tr:>8.0f}{m:>8.0f}{ct:>10.0f}  {flag}")

    # ⚠️ NOTIFICARE: produse fără cost de transport REAL în DB (audit DPD) → profit estimat
    tot_rev = sum(x[3] for x in out) or 1
    no_real = [(s, rev) for s, g, q, rev, cg, tr, m, ct in out if str(s).upper() not in dpd]
    nr_rev = sum(r for _, r in no_real)
    if no_real:
        print(f"\n⚠️  ATENȚIE: {len(no_real)} SKU-uri ({nr_rev/tot_rev*100:.0f}% din venit) NU au cost de transport REAL în DB (audit DPD).")
        print(f"    Pentru acestea transportul e ESTIMAT (cost_per_parcel) → profitul lor e aproximativ. Rulează auditul DPD pe ele.")
        for s, rev in sorted(no_real, key=lambda x: -x[1])[:10]:
            print(f"      - {s}  (venit {rev:.0f})")
    else:
        print("\n✓ Toate SKU-urile au cost de transport real (audit DPD).")


if __name__ == "__main__":
    main()
