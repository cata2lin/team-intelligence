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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import profit_core as pc   # funcții CANONICE (vat/cogs/transport/marketing) — single source, vezi profit_core.py


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
    fx = {r["currency"]: r["rate_to_ron"] for r in cx.execute(
        "SELECT currency, rate_to_ron FROM profit_exchange_rates WHERE month=?", (a.month,)).fetchall()}
    fx["RON"] = 1.0   # monedă magazin → RON (CZK/EUR/PLN/BGN). Lipsă → 1.0 (tratat ca RON).
    cogs_ov = {r["sku"]: (r["unit_cost"], r["currency"]) for r in cx.execute(
        "SELECT sku, unit_cost, currency FROM profit_cogs_override").fetchall()}   # override COGS (H), ca engine-ul

    # delivered lines, joined to status + monedă comandă
    rows = cx.execute("""
        SELECT pol.prefix, pol.order_name, pol.sku, pol.qty, pol.line_revenue, pol.line_cogs,
               po.revenue AS order_total, po.currency AS curr
        FROM profit_order_lines pol
        JOIN profit_orders po ON po.month=pol.month AND po.prefix=pol.prefix AND po.order_name=pol.order_name
        WHERE pol.month=? AND po.status_category='Livrata'
    """, (a.month,)).fetchall()
    cx.close()

    # per-order: line-revenue total (share) + order total (incl. transport ÎNCASAT) + monedă + SKU-urile coletului
    oline = defaultdict(float); ototal = {}; ocurr = {}; order_skus = defaultdict(set)
    for r in rows:
        k = (r["prefix"], r["order_name"])
        oline[k] += r["line_revenue"] or 0; ototal[k] = r["order_total"] or 0
        ocurr[k] = r["curr"] or "RON"; order_skus[k].add(r["sku"])
    # transport REAL pe COLET: media costului real DPD (dpd_nomenclator) pe SKU-urile coletului, per comandă;
    # fallback cost_per_parcel/magazin. Un colet = un cost, alocat pe linii după venit (suma cotelor = 1).
    parcel_cost = {}; parcel_real = {}
    for k, sks in order_skus.items():
        parcel_cost[k], parcel_real[k] = pc.parcel_transport(sks, dpd, tc.get(k[0], 0))

    # name2id + prefix->brand_id (pt alocarea marketingului de brand pe SKU-urile brandului corect)
    import psycopg2
    name2id = {}; pref2bid = {}; mconn = None; cur = None
    try:
        mconn = psycopg2.connect(_clean(os.environ["DATABASE_URL_METRICS"])); cur = mconn.cursor()
        cur.execute("SELECT id, name FROM brands"); name2id = {n.strip().lower(): i for i, n in cur.fetchall()}
        pref2bid = pc.prefix_brandid(name2id)
    except Exception as e:
        sys.stderr.write(f"[brand-map] {type(e).__name__}: {e}\n")

    sku = defaultdict(lambda: [0, 0.0, 0.0, 0.0])            # qty, rev_exvat, cogs_exvat, transport_exvat
    sku_orders = defaultdict(set)                            # sku -> {(prefix,order)} = nr comenzi
    brand_sku_orders = defaultdict(lambda: defaultdict(set)) # brand_id -> sku -> {(prefix,order)}
    for r in rows:
        # (A) NU mai sărim liniile cadou — cadoul are venit 0 dar COGS+bucăți reale (cost real, ca engine-ul).
        s = r["sku"]
        vat = pc.vat_for_prefix(r["prefix"])                 # TVA per țară (canonic)
        k = (r["prefix"], r["order_name"]); olt = oline[k] or 1
        rate = fx.get(ocurr[k], 1.0)                          # (G) monedă magazin → RON
        share = (r["line_revenue"] or 0) / olt               # cotă pe venit (rație, moneda se simplifică)
        rev_ex = (ototal[k] * rate * share) / (1 + vat)      # venit = cotă din total comandă, în RON, ex-TVA
        cogs = pc.cogs_ron(r["qty"], line_cogs_store=r["line_cogs"], rate_store=rate, override=cogs_ov.get(s), fx=fx)
        cogs_ex = cogs / (1 + vat)                            # (H) override + (G) RON, ca engine-ul
        tcost = parcel_cost[k] * share / (1 + vat)           # transport REAL pe colet (DPD), alocat pe venit, ex-TVA
        d = sku[s]
        d[0] += r["qty"] or 0; d[1] += rev_ex; d[2] += cogs_ex; d[3] += tcost
        sku_orders[s].add(k)
        bid = pref2bid.get(r["prefix"])
        if bid: brand_sku_orders[bid][s].add(k)
    orders_count = {s: len(v) for s, v in sku_orders.items()}
    brand_oc = {b: {s: len(v) for s, v in d.items()} for b, d in brand_sku_orders.items()}

    # --- MARKETING alocat pe COMENZI (CPA uniform): direct (HA-####/Google PMax) rămâne EXACT; brand/grup/unmapped
    # se distribuie pe SKU-urile țintă proporțional cu NR. COMENZI → fiecare produs poartă CPA-ul categoriei/brandului.
    mk = {}; leftover = 0.0
    try:
        y, mm = a.month.split("-"); mm = int(mm)
        nxt = "%d-%02d-01" % (int(y) + (1 if mm == 12 else 0), 1 if mm == 12 else mm + 1)
        if mconn is None:
            mconn = psycopg2.connect(_clean(os.environ["DATABASE_URL_METRICS"])); cur = mconn.cursor()
        cur.execute("SELECT sku, brand_id, SUM(spend_ron) FROM cache.product_ad_spend WHERE date>=%s AND date<%s GROUP BY sku, brand_id",
                    (a.month + "-01", nxt))
        cache_rows = cur.fetchall()
        if mconn: mconn.close()
        # alocare CANONICĂ pe comenzi (profit_core) — direct/grup/brand, CPA uniform
        mk, leftover = pc.allocate_marketing_by_orders(cache_rows, set(sku.keys()), s2g, orders_count, brand_oc)
    except Exception as e:
        sys.stderr.write(f"[mkt] {type(e).__name__}: {e}; marketing=0\n")
    if leftover > 100:
        sys.stderr.write(f"[mkt] ⚠ {round(leftover)} RON marketing NEALOCAT (brand fără SKU vândut în lună sau brand_id lipsă)\n")

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

    print(f"=== PROFIT PER CATEGORIE — {a.month} (venit exTVA, transport alocat, marketing alocat pe COMENZI) ===")
    print(f"{'categorie':26}{'buc':>7}{'venit':>11}{'COGS':>10}{'transp':>9}{'mkt':>9}{'contrib':>11}")
    for g, (q, rev, cg, tr, m) in sorted(cat.items(), key=lambda x: -x[1][1]):
        print(f"{str(g)[:26]:26}{q:>7}{rev:>11.0f}{cg:>10.0f}{tr:>9.0f}{m:>9.0f}{rev-cg-tr-m:>11.0f}")
    T = [sum(x) for x in zip(*[(q, rev, cg, tr, m) for q, rev, cg, tr, m in cat.values()])] or [0, 0, 0, 0, 0]
    print(f"{'─'*74}")
    print(f"{'TOTAL (RON ex-TVA)':26}{T[0]:>7}{T[1]:>11.0f}{T[2]:>10.0f}{T[3]:>9.0f}{T[4]:>9.0f}{T[1]-T[2]-T[3]-T[4]:>11.0f}")
    print(f"  (venit reconciliază cu engine-ul RON ex-TVA pe livrate; marketing alocat pe comenzi; transport real DPD)")
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
