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
from pathlib import Path

# shared Postgres/secret helper — core/scripts/arona_pg.py (env-first, KB fallback, read-only)
_here = Path(__file__).resolve()
for _up in range(2, 8):
    _cand = _here.parents[_up] / "core" / "scripts"
    if (_cand / "arona_pg.py").exists():
        sys.path.insert(0, str(_cand)); break
import arona_pg

BRANDS = {
    "esteban": "cmo5v89380001fzw2jii507fk", "grandia": "cmo5ulyl80003h1w2xlzfzhvh",
    "gt": "cmo8ocp3l000504l7ikr6s94q", "george-talent": "cmo8ocp3l000504l7ikr6s94q",
    "nubra": "cmo8odsm6000804l729wajk3p", "belasil": "cmo8kir3g000204jugknzr9zk",
}
# brand → nume magazin în AWBprint (pt --source awb)
BRAND_TO_STORE = {
    "esteban": "esteban.ro", "grandia": "grandia.ro", "gt": "georgetalent.ro",
    "george-talent": "georgetalent.ro", "nubra": "nubra", "belasil": "belasil.ro",
}

def _conn():
    return arona_pg.connect("DATABASE_URL_METRICS")

# Market-basket din AWBprint (instant, ~99% complet vs warehouse care e incomplet).
# SKU e la inventory_item.sku; titlul îl rezolvăm separat din warehouse (suficient pt
# produsele cu volum, care oricum trec de pragul min_prod).
AWB_SQL = """
WITH li AS (
  SELECT DISTINCT o.order_number AS oid, lower(item->'inventory_item'->>'sku') AS sku
  FROM orders o JOIN stores s ON s.uid = o.store_uid
  CROSS JOIN LATERAL jsonb_array_elements(o.line_items::jsonb) AS item
  WHERE s.name = %(store)s
    AND o.frisbo_created_at >= NOW() - (%(days)s || ' days')::interval
    AND lower(coalesce(o.aggregated_status,'')) <> 'cancelled'
    AND nullif(lower(item->'inventory_item'->>'sku'),'') IS NOT NULL
),
prod AS (SELECT sku, COUNT(DISTINCT oid) n FROM li GROUP BY sku HAVING COUNT(DISTINCT oid) >= %(min_prod)s),
tot AS (SELECT COUNT(DISTINCT oid)::float t FROM li WHERE sku IN (SELECT sku FROM prod)),
pairs AS (
  SELECT a.sku pa, b.sku pb, COUNT(DISTINCT a.oid) co
  FROM li a JOIN li b ON a.oid = b.oid AND a.sku < b.sku
  WHERE a.sku IN (SELECT sku FROM prod) AND b.sku IN (SELECT sku FROM prod)
  GROUP BY a.sku, b.sku HAVING COUNT(DISTINCT a.oid) >= %(min_co)s
)
SELECT na.sku, nb.sku, p.co, na.n, nb.n,
       (p.co * (SELECT t FROM tot)) / (na.n * nb.n) AS lift,
       p.co::float / na.n, p.co::float / nb.n
FROM pairs p JOIN prod na ON na.sku = p.pa JOIN prod nb ON nb.sku = p.pb
ORDER BY lift DESC;
"""

def _titles_from_metrics(bid):
    """sku(lower) → titlu, din warehouse order_line_items (doar pt afișare)."""
    try:
        c = arona_pg.connect("DATABASE_URL_METRICS"); cur = c.cursor()
        cur.execute('SELECT lower(sku), MAX(title) FROM order_line_items '
                    'WHERE "brandId"=%s AND sku IS NOT NULL AND sku<>\'\' GROUP BY lower(sku)', (bid,))
        m = {r[0]: r[1] for r in cur.fetchall()}; c.close(); return m
    except Exception:
        return {}

def _rows_awb(a, bid):
    store = BRAND_TO_STORE.get(a.brand.lower())
    if not store:
        sys.exit(f"Brand necunoscut pt AWBprint: {a.brand} (adaugă în BRAND_TO_STORE)")
    c = arona_pg.connect("DATABASE_URL_AWBPRINT"); cur = c.cursor()
    cur.execute(AWB_SQL, {"store": store, "days": a.days, "min_prod": a.min_prod, "min_co": a.min_co})
    raw = cur.fetchall(); c.close()
    titles = _titles_from_metrics(bid)
    rows = []
    for sa, sb, co, na, nb, lift, cab, cba in raw:
        if lift >= a.min_lift:
            rows.append((titles.get(sa, sa), titles.get(sb, sb), co, na, nb, lift, cab, cba))
    return rows

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
    ap.add_argument("--cached", action="store_true",
                    help="read precomputed cache.product_basket_pairs (instant; fixed 180d window, "
                         "ignores --days/--min-prod). Refreshed nightly by gigi:metrics-cache.")
    ap.add_argument("--source", choices=["awb", "metrics"], default="awb",
                    help="awb = AWBprint (default, ~99%% complet, instant); metrics = warehouse (poate fi incomplet)")
    a = ap.parse_args()
    bid = BRANDS.get(a.brand.lower(), a.brand)
    if a.source == "awb" and not a.cached:
        rows = _rows_awb(a, bid)
        _print(a, rows); return
    c = _conn(); cur = c.cursor()
    if a.cached:
        # instant: read the precomputed market-basket (same data, no heavy live self-join)
        cur.execute(
            "SELECT title_a, title_b, co_count, NULL::int, NULL::int, lift, conf_a_to_b, conf_b_to_a "
            "FROM cache.product_basket_pairs WHERE brand_id = %s AND co_count >= %s ORDER BY lift DESC",
            (bid, a.min_co))
        rows = [r for r in cur.fetchall() if (r[5] or 0) >= a.min_lift]
        if a.days != 180:
            print("(--cached: fereastră fixă 180z; --days ignorat)")
    else:
        cur.execute(SQL, {"bid": bid, "days": a.days, "min_prod": a.min_prod, "min_co": a.min_co})
        rows = [r for r in cur.fetchall() if r[5] >= a.min_lift]
    _print(a, rows)

def _print(a, rows):
    src = "AWBprint" if getattr(a, "source", "metrics") == "awb" and not a.cached else "warehouse"
    if not rows:
        print(f"Niciun pattern de co-cumpărare peste praguri ({a.brand}, {a.days}z, min_co={a.min_co}). "
              f"Magazin cu coșuri mono-produs? Scade --min-co sau crește --days."); return
    if a.product:
        q = a.product.lower(); out = []
        for ta, tb, co, na, nb, lift, cab, cba in rows:
            if q in (ta or "").lower(): out.append((tb, co, lift, cab))
            elif q in (tb or "").lower(): out.append((ta, co, lift, cba))
        out.sort(key=lambda x: -x[2])
        print(f"\nComplementare pt '{a.product}' — {a.brand} ({a.days}z, sursă {src})")
        print(f"  {'lift':>5}{'co':>5}{'conf':>6}  produs recomandat")
        for tb, co, lift, conf in out[:a.top]:
            print(f"  {lift:>5.1f}{co:>5}{100*conf:>5.0f}%  {(tb or '')[:60]}")
    else:
        print(f"\nTop perechi cumpărate împreună — {a.brand} ({a.days}z, lift≥{a.min_lift}, sursă {src})")
        print(f"  {'lift':>5}{'co':>5}  A  +  B")
        for ta, tb, co, na, nb, lift, cab, cba in rows[:a.top]:
            print(f"  {lift:>5.1f}{co:>5}  {(ta or '')[:34]:<34} + {(tb or '')[:34]}")
        print("\n  → folosește ca 'frequently bought together' pe PDP + în flow post-purchase Klaviyo.")

if __name__ == "__main__":
    main()
