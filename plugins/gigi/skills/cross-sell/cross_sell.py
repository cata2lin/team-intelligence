# /// script
# requires-python = ">=3.9"
# dependencies = ["psycopg2-binary>=2.9"]
# ///
"""
Cross-sell recommender — market-basket analysis on our own order data: which
products are bought together (support / confidence / lift), per store. Feeds
"frequently bought together" PDP blocks, Klaviyo post-purchase flows, and the
2+1 surprise-perfume offer. Read-only on the metrics DB.

Connects via DATABASE_URL_METRICS (KB secret), same as gads.py.

Usage:
    KB=~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py
    export DATABASE_URL_METRICS="$(uv run "$KB" secret-get DATABASE_URL_METRICS)"
    uv run cross_sell.py --brand grandia                    # top cross-sell pairs (last 180d)
    uv run cross_sell.py --brand esteban --product "scandal" # complements for a product (title match)
    uv run cross_sell.py --brand gt --days 365 --min-co 10 --top 25
"""
import argparse, os, sys
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2

BRANDS = {
    "esteban": "cmo5v89380001fzw2jii507fk", "grandia": "cmo5ulyl80003h1w2xlzfzhvh",
    "gt": "cmo8ocp3l000504l7ikr6s94q", "george-talent": "cmo8ocp3l000504l7ikr6s94q",
    "nubra": "cmo8odsm6000804l729wajk3p", "belasil": "cmo8kir3g000204jugknzr9zk",
}
_OK = {"sslmode", "sslrootcert", "sslcert", "sslkey", "connect_timeout", "application_name", "options"}

def _conn():
    d = os.environ.get("DATABASE_URL_METRICS") or sys.exit("Set DATABASE_URL_METRICS (kb.py secret-get).")
    p = urlsplit(d); q = [(k, v) for k, v in parse_qsl(p.query) if k in _OK]
    return psycopg2.connect(urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), p.fragment)))

SQL = """
WITH li AS (
  SELECT DISTINCT o.id AS oid, l."productId" pid, l.title
  FROM order_line_items l JOIN orders o ON o.id = l."orderId"
  WHERE o."brandId" = %(bid)s AND l."productId" IS NOT NULL
    AND o."cancelledAt" IS NULL
    AND o."shopifyCreatedAt" >= NOW() - (%(days)s || ' days')::interval
),
prod AS (SELECT pid, MAX(title) title, COUNT(DISTINCT oid) n FROM li GROUP BY pid HAVING COUNT(DISTINCT oid) >= %(min_prod)s),
tot AS (SELECT COUNT(DISTINCT oid)::float t FROM li WHERE pid IN (SELECT pid FROM prod)),
pairs AS (
  SELECT a.pid pa, b.pid pb, COUNT(DISTINCT a.oid) co
  FROM li a JOIN li b ON a.oid = b.oid AND a.pid < b.pid
  WHERE a.pid IN (SELECT pid FROM prod) AND b.pid IN (SELECT pid FROM prod)
  GROUP BY a.pid, b.pid HAVING COUNT(DISTINCT a.oid) >= %(min_co)s
)
SELECT na.title, nb.title, p.co, na.n, nb.n,
       (p.co * (SELECT t FROM tot)) / (na.n * nb.n) AS lift,
       p.co::float / na.n AS conf_ab, p.co::float / nb.n AS conf_ba
FROM pairs p JOIN prod na ON na.pid = p.pa JOIN prod nb ON nb.pid = p.pb
ORDER BY lift DESC;
"""

def main():
    ap = argparse.ArgumentParser(description="Cross-sell recommender (market-basket).")
    ap.add_argument("--brand", required=True); ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--min-co", type=int, default=15, dest="min_co", help="min co-occurrences")
    ap.add_argument("--min-prod", type=int, default=20, dest="min_prod", help="min orders/product to consider")
    ap.add_argument("--min-lift", type=float, default=1.5, dest="min_lift")
    ap.add_argument("--product", help="title substring → show complements for it")
    ap.add_argument("--top", type=int, default=20)
    a = ap.parse_args()
    bid = BRANDS.get(a.brand.lower(), a.brand)
    c = _conn(); cur = c.cursor()
    cur.execute(SQL, {"bid": bid, "days": a.days, "min_prod": a.min_prod, "min_co": a.min_co})
    rows = [r for r in cur.fetchall() if r[5] >= a.min_lift]
    if not rows:
        print(f"Niciun pattern de co-cumpărare peste praguri ({a.brand}, {a.days}z, min_co={a.min_co}). "
              f"Magazin cu coșuri mono-produs? Scade --min-co sau crește --days."); return
    if a.product:
        q = a.product.lower(); out = []
        for ta, tb, co, na, nb, lift, cab, cba in rows:
            if q in (ta or "").lower(): out.append((tb, co, lift, cab))
            elif q in (tb or "").lower(): out.append((ta, co, lift, cba))
        out.sort(key=lambda x: -x[2])
        print(f"\nComplementare pt '{a.product}' — {a.brand} ({a.days}z)")
        print(f"  {'lift':>5}{'co':>5}{'conf':>6}  produs recomandat")
        for tb, co, lift, conf in out[:a.top]:
            print(f"  {lift:>5.1f}{co:>5}{100*conf:>5.0f}%  {(tb or '')[:60]}")
    else:
        print(f"\nTop perechi cumpărate împreună — {a.brand} ({a.days}z, lift≥{a.min_lift})")
        print(f"  {'lift':>5}{'co':>5}  A  +  B")
        for ta, tb, co, na, nb, lift, cab, cba in rows[:a.top]:
            print(f"  {lift:>5.1f}{co:>5}  {(ta or '')[:34]:<34} + {(tb or '')[:34]}")
        print("\n  → folosește ca 'frequently bought together' pe PDP + în flow post-purchase Klaviyo.")

if __name__ == "__main__":
    main()
