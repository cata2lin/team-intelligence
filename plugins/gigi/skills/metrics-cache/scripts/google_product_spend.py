# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31", "psycopg2-binary>=2.9"]
# ///
"""
Google per-SKU spend pentru conturile PMax (în afară de Grandia, care vine deja nativ din
google_ads_product_insights_daily). Trage shopping_performance_view (segments.product_item_id +
metrics.cost_micros) via MCC (gads.py), mapează product_item_id (ultimul număr = variant
shopifyNumericId) → variants.sku, scrie cache.product_ad_spend (platform='google', source='google_pmax').
Conturi RON → fără FX. Aditiv: nu atinge sursa google_product_insights (Grandia).

  cd .../google-ads-mcc && DATABASE_URL_METRICS=... uv run <acest dir>/google_product_spend.py --since 2025-01-01 --apply
"""
import os, sys, re, datetime, argparse
from collections import defaultdict
from pathlib import Path
sys.path.insert(0, os.environ.get("GADS_DIR") or str(Path(__file__).resolve().parents[1] / "google-ads-mcc"))
sys.path.insert(0, "/root/Scripturi")   # core.stores (Shopify tokens) pe VPS
import gads

# conturi ARONA pe Google (RON), FĂRĂ Grandia (deja nativ). account_id → (brand metrics, prefix store).
ACCOUNTS = {"5229815058": ("Esteban", "EST"), "7566352958": ("Belasil", "BELA"),
            "4069952156": ("Carpetto", "CARP"), "8148962111": ("Gento", "GEN")}


def shopify_variant_skus(shop, token):
    """variant legacyResourceId (= ultimul număr din product_item_id) -> sku, direct din Shopify."""
    import httpx
    url = f"https://{shop}/admin/api/2024-10/graphql.json"
    H = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
    q = "query($c:String){ productVariants(first:250, after:$c){ pageInfo{hasNextPage endCursor} nodes{ legacyResourceId sku } } }"
    out = {}; cur = None
    with httpx.Client() as cl:
        while True:
            r = cl.post(url, headers=H, json={"query": q, "variables": {"c": cur}}, timeout=60).json()
            d = (r.get("data") or {}).get("productVariants") or {}
            for n in d.get("nodes") or []:
                if n.get("sku"):
                    out[str(n.get("legacyResourceId"))] = n["sku"].strip()
            pi = d.get("pageInfo") or {}
            if not pi.get("hasNextPage"):
                break
            cur = pi.get("endCursor")
    return out


def _clean(dsn):
    dsn = re.sub(r"([?&])(schema|channel_binding|pgbouncer|connection_limit)=[^&]*", r"\1", dsn)
    return re.sub(r"[?&]+(&|$)", r"\1", dsn).rstrip("?&")


def main():
    import psycopg2
    from psycopg2.extras import execute_values
    ap = argparse.ArgumentParser(); ap.add_argument("--days", type=int, default=35)
    ap.add_argument("--since"); ap.add_argument("--until"); ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    end = a.until or datetime.date.today().isoformat()
    start = a.since or (datetime.date.today() - datetime.timedelta(a.days)).isoformat()

    from core.stores import list_stores
    mconn = psycopg2.connect(_clean(os.environ["DATABASE_URL_METRICS"])); cur = mconn.cursor()
    cur.execute("SELECT name, id FROM brands"); name2id = {n.strip().lower(): i for n, i in cur.fetchall()}
    stores = {s["prefix"]: s for s in list_stores()}

    c = gads.get_connection()
    q = (f"SELECT segments.date, segments.product_item_id, metrics.cost_micros "
         f"FROM shopping_performance_view WHERE segments.date BETWEEN '{start}' AND '{end}' AND metrics.cost_micros > 0")
    agg = defaultdict(float)   # (date, sku, brand_id) -> ron
    unmatched = 0
    for acct, (brand, prefix) in ACCOUNTS.items():
        bid = name2id.get(brand.lower())
        st = stores.get(prefix)
        vskus = shopify_variant_skus(st["shop"], st["token"]) if st else {}   # variant_id -> sku din Shopify
        try:
            rows = gads.search(c, acct, q)
        except Exception as e:
            sys.stderr.write(f"[google] {brand}: {type(e).__name__} {str(e)[:100]}\n"); continue
        nm = 0
        for r in rows:
            seg = r.get("segments", {}); pid = seg.get("productItemId", "") or ""
            m = re.search(r"_(\d+)$", pid)
            if not m:
                continue
            sku = vskus.get(m.group(1))
            if not sku:
                unmatched += 1; continue
            ron = int(r.get("metrics", {}).get("costMicros", 0)) / 1e6
            agg[(seg["date"], sku, bid)] += ron; nm += 1
        print(f"  {brand}: {len(rows)} product-rows, {nm} mapate ({len(vskus)} variants Shopify)")
    out = [(d, bid, sku, None, "google", round(v, 2), "google_pmax") for (d, sku, bid), v in agg.items() if v > 0]
    print(f"[google_pmax] {len(out)} rânduri (date×sku); unmatched item_ids: {unmatched}; spend total {round(sum(x[5] for x in out))} RON")
    for d, bid, sku, _, _, sp, _ in sorted(out, key=lambda x: -x[5])[:8]:
        print(f"    {d} {sku[:30]:30} {sp:>8.0f}")
    if not a.apply:
        print("DRY-RUN — nimic scris."); return
    execute_values(cur,
        "INSERT INTO cache.product_ad_spend (date,brand_id,sku,product_title,platform,spend_ron,source) VALUES %s "
        "ON CONFLICT (date,sku,platform) DO UPDATE SET spend_ron=EXCLUDED.spend_ron, "
        "brand_id=COALESCE(EXCLUDED.brand_id,cache.product_ad_spend.brand_id), source=EXCLUDED.source",
        out, page_size=2000)
    mconn.commit(); print(f"[google_pmax] APPLIED — {len(out)} rânduri.")


if __name__ == "__main__":
    main()
