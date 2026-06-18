# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "google-api-python-client>=2.0", "google-auth>=2.0"]
# ///
"""
Profit/contribuție per CATEGORIE (product_group) — REUTILIZEAZĂ output-urile engine-ului de profitabilitate
(profit_orders: revenue/COGS/status_category calculate de api.profitability) re-agregate pe categorie în loc de
prefix, + leagă spend-ul per-SKU din metrics cache.product_ad_spend. NU reimplementează TVA/transport/2+1.

Categoria unei comenzi = product_group-ul SKU-urilor ei (din WMS 'Product Group'), ignorând cadourile.
Per-categorie e exact (comenzile rar trec între categorii). Per-SKU fin = necesită captură pe linie (altă fază).

  uv run profit_by_category.py 2026-05 [--db /root/Scripturi/data/profitability.db]
Secrete din ENV (run.env pe VPS): DATABASE_URL_METRICS, GA4_SA_JSON, NOMENCLATOR_SHEET_ID.
"""
import os, sys, re, json, sqlite3, argparse
from collections import defaultdict

GIFT = {"surpriza", "cutie-cadou", "cad", "cadou", "gift", "surprise"}


def sku_to_group():
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
    sa = json.loads(os.environ["GA4_SA_JSON"])
    cr = Credentials.from_service_account_info(sa, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    svc = build("sheets", "v4", credentials=cr).spreadsheets()
    sid = os.environ["NOMENCLATOR_SHEET_ID"]
    pg = svc.values().get(spreadsheetId=sid, range="'Product Group'!A2:B").execute().get("values", [])
    m = {}
    for r in pg:
        if len(r) >= 2 and r[0].strip() and r[1].strip():
            m[r[0].strip()] = r[1].strip()
    return m


def order_category(skus_str, s2g):
    groups = set()
    for s in (skus_str or "").split(";"):
        s = s.strip()
        if not s or s.lower() in GIFT:
            continue
        groups.add(s2g.get(s, s2g.get(s.lower(), "Necunoscut")))
    groups.discard("Necunoscut")
    if not groups:
        return "Necunoscut/cadou"
    return next(iter(groups)) if len(groups) == 1 else "Mixt (multi-categorie)"


def _clean(dsn):
    dsn = re.sub(r"([?&])(schema|channel_binding|pgbouncer|connection_limit)=[^&]*", r"\1", dsn)
    return re.sub(r"[?&]+(&|$)", r"\1", dsn).rstrip("?&")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("month"); ap.add_argument("--db", default="/root/Scripturi/data/profitability.db")
    a = ap.parse_args()
    s2g = sku_to_group()

    # 1) revenue/cogs per category from the engine's profit_orders (delivered only)
    cx = sqlite3.connect(a.db); cx.row_factory = sqlite3.Row
    cat = defaultdict(lambda: {"orders": 0, "revenue": 0.0, "cogs": 0.0})
    for r in cx.execute("SELECT revenue,cogs,skus,status_category FROM profit_orders WHERE month=? AND status_category='Livrata'", (a.month,)):
        c = order_category(r["skus"], s2g)
        cat[c]["orders"] += 1; cat[c]["revenue"] += r["revenue"] or 0; cat[c]["cogs"] += r["cogs"] or 0
    cx.close()

    # 2) marketing per category from metrics cache.product_ad_spend (my per-SKU spend), same month
    import psycopg2
    mk = defaultdict(float)
    try:
        pc = psycopg2.connect(_clean(os.environ["DATABASE_URL_METRICS"])); pcur = pc.cursor()
        pcur.execute("SELECT sku, SUM(spend_ron) FROM cache.product_ad_spend WHERE to_char(date,'YYYY-MM')=%s GROUP BY sku", (a.month,))
        for sku, sp in pcur.fetchall():
            g = s2g.get(sku, s2g.get((sku or "").lower(), sku))   # spend sku/group → category
            mk[g] += float(sp or 0)
        pc.close()
    except Exception as e:
        sys.stderr.write(f"[mkt] cache.product_ad_spend indisponibil ({type(e).__name__}); marketing=0\n")

    rows = []
    for c, v in cat.items():
        m = mk.get(c, 0.0)
        gross = v["revenue"] - v["cogs"]
        rows.append((c, v["orders"], v["revenue"], v["cogs"], gross, m, gross - m))
    rows.sort(key=lambda x: -x[2])
    print(f"Profit per CATEGORIE — {a.month} (revenue/COGS din engine profit_orders 'Livrata'; marketing din cache.product_ad_spend)\n")
    print(f"{'categorie':28}{'com':>6}{'revenue':>11}{'COGS':>10}{'marja bruta':>12}{'marketing':>11}{'contrib':>11}")
    print("-" * 90)
    T = [0, 0, 0, 0, 0]
    for c, n, rev, cg, gr, m, ct in rows:
        print(f"{c[:28]:28}{n:>6}{rev:>11.0f}{cg:>10.0f}{gr:>12.0f}{m:>11.0f}{ct:>11.0f}")
        T = [T[0]+rev, T[1]+cg, T[2]+gr, T[3]+m, T[4]+ct]
    print("-" * 90)
    print(f"{'TOTAL':28}{'':>6}{T[0]:>11.0f}{T[1]:>10.0f}{T[2]:>12.0f}{T[3]:>11.0f}{T[4]:>11.0f}")


if __name__ == "__main__":
    main()
