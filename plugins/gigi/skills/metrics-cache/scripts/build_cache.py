# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary", "paramiko>=3.0"]
# ///
"""
build_cache.py — materialize shared CACHE tables in the metrics warehouse so CS
(and other) skills READ precomputed aggregates instead of recomputing live every run.

Lives in a dedicated `cache` schema (never pollutes the BI app's `public`).
Idempotent: CREATE SCHEMA/TABLE IF NOT EXISTS + transactional TRUNCATE+INSERT refresh.

SAFETY: default is --dry-run (SELECT counts only, NO writes). Pass --apply to write.
Connects via DATABASE_URL_METRICS (from the SharedClaude secret store). Never prints it.

Tables (v1):
  cache.customer_agg   — per-customer identity aggregate from public.orders
                         (order_count, cancelled, net_value, brands, first/last order).
                         Readers: cs-customer-360, cs-profile, cs-conversation-profile,
                         cs-draft-reply, cod-confirmation, customer-identity.
                         NOTE: delivered-vs-refused / AWB status are NOT in the metrics
                         warehouse (they live in profit_orders on the VPS + courier APIs);
                         this v1 gives order/spend/cancel signals. Refusal-rate columns
                         get added when the delivery-outcome sync lands (see SKILL.md).

Usage:
  uv run build_cache.py --table customer_agg --dry-run
  uv run build_cache.py --table customer_agg --apply
  uv run build_cache.py --all --apply        # refresh every cache table
"""
import os, sys, subprocess, argparse
from pathlib import Path
import psycopg2

def _kb():
    env = os.environ.get("KB_PATH")
    if env and Path(env).exists():
        return env
    here = Path(__file__).resolve()
    for up in range(3, 8):
        c = here.parents[up] / "core" / "scripts" / "kb.py"
        if c.exists():
            return str(c)
    raise FileNotFoundError("kb.py not found; set KB_PATH")

def secret(key):
    # env-first (works on servers that have the value in their .env / environment, no uv/KB needed),
    # then fall back to the SharedClaude KB via kb.py (the onboarded-workstation path).
    v = os.environ.get(key)
    if v:
        return v.strip()
    try:
        return subprocess.run(["uv", "run", _kb(), "secret-get", key],
                              capture_output=True, text=True, timeout=60).stdout.strip()
    except Exception:
        return ""

def clean_dsn(dsn):
    """Strip Prisma-style query params psycopg2 rejects (schema, pgbouncer,
    connection_limit, …); keep only a libpq-safe allowlist."""
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
    u = urlsplit(dsn)
    keep = {"sslmode", "sslrootcert", "sslcert", "sslkey", "connect_timeout", "application_name"}
    q = [(k, v) for k, v in parse_qsl(u.query) if k in keep]
    return urlunsplit((u.scheme, u.netloc, u.path, urlencode(q), u.fragment))

# Recommended freshness window per table (hours) — drives the STALE flag in cache.freshness.
MAX_AGE = {
  "order_outcome": 24, "customer_agg": 24, "daily_ad_spend_ron": 24,
  "product_refusal_rate": 24, "order_enriched": 12, "product_basket_pairs": 48,
  "product_ad_spend": 24, "daily_brand_pnl": 24,
}
# Per-table date span (min,max) so readers know WHICH PERIOD the cache covers.
# None = table has no time dimension (e.g. all-time per-SKU aggregate).
SPAN_SQL = {
  "order_outcome":        "SELECT min(created_at)::date, max(created_at)::date FROM cache.order_outcome",
  "customer_agg":         "SELECT min(first_order), max(last_order) FROM cache.customer_agg",
  "daily_ad_spend_ron":   "SELECT min(date), max(date) FROM cache.daily_ad_spend_ron",
  "order_enriched":       "SELECT min(placed_at)::date, max(placed_at)::date FROM cache.order_enriched",
  "product_refusal_rate": None,
  "product_basket_pairs": None,
  "product_ad_spend": "SELECT min(date), max(date) FROM cache.product_ad_spend",
  "daily_brand_pnl": "SELECT min(date), max(date) FROM cache.daily_brand_pnl",
}

CACHE_META_DDL = """
CREATE SCHEMA IF NOT EXISTS cache;
CREATE TABLE IF NOT EXISTS cache.refresh_log (
  table_name   text PRIMARY KEY,
  rows         int,
  refreshed_at timestamptz DEFAULT now(),
  max_age_hours int,
  data_from    date,
  data_to      date
);
ALTER TABLE cache.refresh_log ADD COLUMN IF NOT EXISTS data_from date;
ALTER TABLE cache.refresh_log ADD COLUMN IF NOT EXISTS data_to   date;
CREATE OR REPLACE VIEW cache.freshness AS
SELECT table_name, rows, refreshed_at,
  round(extract(epoch FROM (now()-refreshed_at))/3600.0, 1) AS age_hours,
  max_age_hours,
  (now()-refreshed_at) > make_interval(hours => max_age_hours) AS stale,
  data_from, data_to
FROM cache.refresh_log
ORDER BY stale DESC, age_hours DESC;
"""

def log_refresh(cur, table, rows):
    cur.execute(CACHE_META_DDL)
    dfrom = dto = None
    span = SPAN_SQL.get(table)
    if span:
        cur.execute(span); r = cur.fetchone()
        if r: dfrom, dto = r[0], r[1]
    cur.execute(
        "INSERT INTO cache.refresh_log (table_name,rows,refreshed_at,max_age_hours,data_from,data_to) "
        "VALUES (%s,%s,now(),%s,%s,%s) "
        "ON CONFLICT (table_name) DO UPDATE SET rows=EXCLUDED.rows, refreshed_at=now(), "
        "max_age_hours=EXCLUDED.max_age_hours, data_from=EXCLUDED.data_from, data_to=EXCLUDED.data_to",
        (table, rows, MAX_AGE.get(table, 24), dfrom, dto))

def show_status():
    dsn = secret("DATABASE_URL_METRICS")
    conn = psycopg2.connect(clean_dsn(dsn)); cur = conn.cursor()
    cur.execute(CACHE_META_DDL); conn.commit()
    cur.execute("SELECT table_name, rows, age_hours, max_age_hours, stale, data_from, data_to FROM cache.freshness")
    rows = cur.fetchall(); conn.close()
    if not rows:
        print("No cache tables refreshed yet. Run: uv run build_cache.py --all --apply"); return
    print(f"{'TABLE':22} {'ROWS':>9} {'AGE(h)':>7} {'STATUS':<16} DATA COVERS")
    any_stale = False
    for t, n, age, mx, stale, dfrom, dto in rows:
        flag = "STALE-refresh!" if stale else "fresh"
        if stale: any_stale = True
        period = f"{dfrom} → {dto}" if dfrom else "(all-time, no date)"
        print(f"{t:22} {n:>9} {age:>7} {flag:<16} {period}")
    if any_stale:
        print("\n>>> Some tables are STALE. Refresh:  uv run build_cache.py --all --apply")

# ---- cache table definitions: (ddl, refresh_select) ----
CUSTOMER_AGG_DDL = """
CREATE SCHEMA IF NOT EXISTS cache;
DROP TABLE IF EXISTS cache.customer_agg CASCADE;
CREATE TABLE cache.customer_agg (
  identity       text PRIMARY KEY,
  identity_type  text,
  sample_name    text,
  sample_email   text,
  order_count    int,
  cancelled      int,
  delivered      int,      -- real outcome from cache.order_outcome
  refused        int,
  refusal_rate   numeric,  -- refused / (delivered+refused) * 100
  serial_refuser boolean,
  net_value      numeric,
  brand_count    int,
  brand_ids      text[],
  first_order    date,
  last_order     date,
  computed_at    timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS customer_agg_lastorder_idx ON cache.customer_agg(last_order);
CREATE INDEX IF NOT EXISTS customer_agg_serial_idx ON cache.customer_agg(serial_refuser) WHERE serial_refuser;
"""

CUSTOMER_AGG_SELECT = """
WITH base AS (
  SELECT
    COALESCE(NULLIF(regexp_replace(COALESCE(o."phone",o."shippingPhone",''),'[^0-9]','','g'),''),
             lower(NULLIF(o."email",''))) AS identity,
    CASE WHEN NULLIF(regexp_replace(COALESCE(o."phone",o."shippingPhone",''),'[^0-9]','','g'),'') IS NOT NULL
         THEN 'phone' ELSE 'email' END AS identity_type,
    COALESCE(o."shippingName",o."name") AS nm, o."email" AS em,
    o."brandId", o."totalPrice", o."totalRefunded", o."cancelledAt", o."shopifyCreatedAt",
    oo.status_category, oo.is_refusal
  FROM public.orders o
  LEFT JOIN cache.order_outcome oo ON oo.order_name = o."name"
  WHERE o."deletedAt" IS NULL
)
SELECT identity, MIN(identity_type),
  MAX(nm), MAX(em),
  COUNT(*)::int,
  COUNT(*) FILTER (WHERE "cancelledAt" IS NOT NULL)::int,
  COUNT(*) FILTER (WHERE status_category='Livrata')::int,
  COUNT(*) FILTER (WHERE is_refusal)::int,
  ROUND(100.0*COUNT(*) FILTER (WHERE is_refusal)
        / NULLIF(COUNT(*) FILTER (WHERE status_category IN ('Livrata','Refuzata')),0), 0),
  (COUNT(*) FILTER (WHERE is_refusal) >= 2
   AND COUNT(*) FILTER (WHERE is_refusal) >= COUNT(*) FILTER (WHERE status_category='Livrata')),
  ROUND(COALESCE(SUM(COALESCE("totalPrice",0)-COALESCE("totalRefunded",0))
        FILTER (WHERE "cancelledAt" IS NULL),0),2),
  COUNT(DISTINCT "brandId")::int,
  array_agg(DISTINCT "brandId"),
  MIN("shopifyCreatedAt")::date, MAX("shopifyCreatedAt")::date
FROM base WHERE identity IS NOT NULL
GROUP BY identity;
"""

# daily_ad_spend_ron = closed days from AWBprint.marketing_daily_costs (sheet-fed 'Raport Zilnic 2',
# ~1-2 day human-entry lag) + a CURRENT-DAY overlay read live from the consolidated "Facebook Azi"
# sheet tab (public CSV, no auth). Includes TikTok (the metrics TikTok API sync is dead). RON.
# Grandia is not in these sheets (its spend comes from the Grandia pipeline).
SHEET_ID = "1IVg0fI-_Rm7IptmOl3BmGrqtyyzn3auf0ZPuftr9vQo"
AZI_TAB  = "Facebook Azi"   # consolidated current-day per-brand table (FB+TikTok+Google), all brands
STORE_OVERRIDES = {"casaofertelor.ro": "bonhaus"}  # Casa Ofertelor = the RO Bonhaus storefront

DAILY_AD_SPEND_DDL = """
CREATE SCHEMA IF NOT EXISTS cache;
DROP TABLE IF EXISTS cache.daily_ad_spend_ron CASCADE;
CREATE TABLE cache.daily_ad_spend_ron (
  date        date,
  store_name  text,    -- AWBprint store domain (closed days) or '<brand>.azi' (overlay)
  brand_id    text,    -- mapped via brands.slug/name (+ STORE_OVERRIDES); NULL if no metrics brand
  platform    text,    -- meta / google / tiktok
  spend_ron   numeric,
  source      text,    -- 'awbprint' (closed days) | 'sheet_today' (live current day)
  computed_at timestamptz DEFAULT now(),
  PRIMARY KEY (date, store_name, platform)
);
CREATE INDEX IF NOT EXISTS daily_ad_spend_brand_idx ON cache.daily_ad_spend_ron(brand_id, date);
"""

def _ro_num(s):
    s = (s or "").strip().replace("\xa0", "")
    if not s: return 0.0
    if "," in s and "." in s: s = s.replace(".", "").replace(",", ".")
    elif "," in s: s = s.replace(",", ".")
    try: return float(s)
    except ValueError: return 0.0

def _fetch_azi():
    import urllib.request
    url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq?tqx=out:csv&sheet=" + urllib.request.quote(AZI_TAB)
    for _ in range(4):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=45).read().decode("utf-8", "ignore")
        except Exception:
            continue
    return None

def run_daily_ad_spend(apply):
    import re, csv, io, datetime
    from psycopg2.extras import execute_values
    norm = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").strip().lower())
    mdsn = secret("DATABASE_URL_METRICS"); adsn = secret("DATABASE_URL_AWBPRINT")
    if not adsn:
        print("!! DATABASE_URL_AWBPRINT not in secret store"); sys.exit(1)
    def skey(s):
        s = (s or "").strip().lower(); parts = s.split(".")
        base = parts[0] if len(parts) == 1 else ("".join(parts[:-1]) if parts[-1] == "ro" else "".join(parts))
        return re.sub(r"[^a-z0-9]", "", base)
    mconn = psycopg2.connect(clean_dsn(mdsn)); mcur = mconn.cursor()
    mcur.execute("SELECT id, slug, name FROM brands")
    bslug, bname = {}, {}
    for bid, slug, name in mcur.fetchall():
        if slug: bslug[norm(slug)] = bid
        if name: bname[norm(name)] = bid
    ovr = {dom: bslug.get(norm(slug)) for dom, slug in STORE_OVERRIDES.items()}
    # 1) closed days from AWBprint
    aconn = psycopg2.connect(clean_dsn(adsn)); acur = aconn.cursor()
    acur.execute("SELECT cost_date, store_name, facebook, tiktok, google FROM marketing_daily_costs")
    rows, unmapped, brand_store, awb_max = [], set(), {}, None
    for d, store, fb, tk, gg in acur.fetchall():
        bid = ovr.get((store or "").strip().lower()) or bslug.get(skey(store))
        if bid is None: unmapped.add(store)
        else: brand_store.setdefault(bid, store)
        if awb_max is None or d > awb_max: awb_max = d
        for plat, val in (("meta", fb), ("tiktok", tk), ("google", gg)):
            if val is not None:
                rows.append((d, store, bid, plat, float(val), "awbprint"))
    aconn.close()
    # 2) current-day overlay from the live sheet tab (only dates AWBprint doesn't have yet)
    overlay = 0; azi = _fetch_azi()
    if azi:
        rd = list(csv.reader(io.StringIO(azi))); idx = {h: i for i, h in enumerate(rd[0])}
        for r in rd[1:]:
            try: dd = datetime.datetime.strptime(r[idx["Data"]].strip(), "%d.%m.%Y").date()
            except Exception: continue
            if awb_max and dd <= awb_max: continue   # AWBprint is authoritative for closed days
            bn = norm(r[idx["Brand"]])
            bid = bname.get(bn) or (bname.get(bn[:-2]) if bn.endswith("ro") else None)
            store = brand_store.get(bid) or (bn + ".azi")
            for plat, col in (("meta", "Facebook"), ("tiktok", "Tiktok"), ("google", "Google")):
                if col in idx:
                    rows.append((dd, store, bid, plat, _ro_num(r[idx[col]]), "sheet_today")); overlay += 1
    else:
        print("[daily_ad_spend_ron] WARN: current-day sheet tab unreachable; AWBprint-only this run")
    print(f"[daily_ad_spend_ron] {len(rows)} rows (AWBprint thru {awb_max} + {overlay} sheet-overlay); "
          f"unmapped stores: {sorted(unmapped) or 'none'}")
    if not apply:
        mconn.rollback(); mconn.close()
        print("DRY-RUN — nothing written. Re-run with --apply."); return
    mcur.execute(DAILY_AD_SPEND_DDL)
    mcur.execute("TRUNCATE cache.daily_ad_spend_ron")
    execute_values(mcur,
        "INSERT INTO cache.daily_ad_spend_ron (date,store_name,brand_id,platform,spend_ron,source) VALUES %s",
        rows, page_size=2000)
    mcur.execute("SELECT COUNT(*) FROM cache.daily_ad_spend_ron"); n = mcur.fetchone()[0]
    log_refresh(mcur, "daily_ad_spend_ron", n)
    mconn.commit(); mconn.close()
    print(f"[daily_ad_spend_ron] APPLIED — cache.daily_ad_spend_ron now has {n} rows.")

# Per-SKU ad spend (the SKU↔ad-spend "parity"). GOOGLE is native per-product
# (google_ads_product_insights_daily.productItemId = shopify_zz_<prod>_<variant> → variants.sku);
# FB/TikTok have NO product-level spend in the warehouse — only AWBprint.sku_ad_spend_daily
# (currently HA-* SKUs, built from campaign-name parsing). General Meta/TikTok SKU mapping = TODO.
PRODUCT_AD_SPEND_DDL = """
CREATE SCHEMA IF NOT EXISTS cache;
DROP TABLE IF EXISTS cache.product_ad_spend CASCADE;
CREATE TABLE cache.product_ad_spend (
  date          date,
  brand_id      text,
  sku           text,
  product_title text,
  platform      text,    -- google | meta | tiktok
  spend_ron     numeric,
  source        text,    -- google_product_insights | awbprint_sku
  computed_at   timestamptz DEFAULT now(),
  PRIMARY KEY (date, sku, platform)
);
CREATE INDEX IF NOT EXISTS product_ad_spend_brand_idx ON cache.product_ad_spend(brand_id, date);
CREATE INDEX IF NOT EXISTS product_ad_spend_sku_idx   ON cache.product_ad_spend(sku);
"""
# Google per-SKU, in-DB (metrics): productItemId → variant → sku, aggregated.
PRODUCT_AD_SPEND_GOOGLE = """
INSERT INTO cache.product_ad_spend (date, brand_id, sku, product_title, platform, spend_ron, source)
SELECT g.date, v."brandId", v.sku, MAX(g."productTitle"), 'google', ROUND(SUM(g."costRon"),2), 'google_product_insights'
FROM google_ads_product_insights_daily g
JOIN variants v ON v."shopifyNumericId" = (regexp_match(g."productItemId", '_(\\d+)$'))[1]::bigint
WHERE g."productItemId" ~ '_\\d+$' AND v.sku IS NOT NULL AND v.sku<>'' AND g."costRon" IS NOT NULL
GROUP BY g.date, v."brandId", v.sku
"""

def run_product_ad_spend(apply):
    from psycopg2.extras import execute_values
    mdsn = secret("DATABASE_URL_METRICS"); adsn = secret("DATABASE_URL_AWBPRINT")
    mconn = psycopg2.connect(clean_dsn(mdsn)); mcur = mconn.cursor()
    # sku -> brand_id map from metrics variants (for the AWBprint fb/tiktok rows)
    mcur.execute("SELECT sku, MAX(\"brandId\") FROM variants WHERE sku IS NOT NULL AND sku<>'' GROUP BY sku")
    skubrand = dict(mcur.fetchall())
    fbtk, unmatched = [], set()
    if adsn:
        aconn = psycopg2.connect(clean_dsn(adsn)); acur = aconn.cursor()
        acur.execute("SELECT date, sku, amount_fb_ron, amount_tk_ron FROM sku_ad_spend_daily")
        for d, sku, fb, tk in acur.fetchall():
            bid = skubrand.get(sku)
            if bid is None: unmatched.add(sku)
            if fb: fbtk.append((d, bid, sku, None, "meta", float(fb), "awbprint_sku"))
            if tk: fbtk.append((d, bid, sku, None, "tiktok", float(tk), "awbprint_sku"))
        aconn.close()
    else:
        print("[product_ad_spend] WARN: no DATABASE_URL_AWBPRINT — google only")
    print(f"[product_ad_spend] google: in-DB; fb/tiktok(AWBprint): {len(fbtk)} rows; "
          f"unmatched fb/tiktok skus: {len(unmatched)}")
    if not apply:
        mconn.rollback(); mconn.close(); print("DRY-RUN — nothing written."); return
    mcur.execute(PRODUCT_AD_SPEND_DDL)
    mcur.execute(PRODUCT_AD_SPEND_GOOGLE)
    if fbtk:
        execute_values(mcur,
            "INSERT INTO cache.product_ad_spend (date,brand_id,sku,product_title,platform,spend_ron,source) "
            "VALUES %s ON CONFLICT (date,sku,platform) DO UPDATE SET spend_ron=EXCLUDED.spend_ron, "
            "brand_id=COALESCE(EXCLUDED.brand_id,cache.product_ad_spend.brand_id), source=EXCLUDED.source",
            fbtk, page_size=2000)
    mcur.execute("SELECT COUNT(*), platform FROM cache.product_ad_spend GROUP BY platform")
    by = dict((p, c) for c, p in mcur.fetchall())
    mcur.execute("SELECT COUNT(*) FROM cache.product_ad_spend"); n = mcur.fetchone()[0]
    log_refresh(mcur, "product_ad_spend", n)
    mconn.commit(); mconn.close()
    print(f"[product_ad_spend] APPLIED — {n} rows ({by}).")

PRODUCT_REFUSAL_DDL = """
CREATE SCHEMA IF NOT EXISTS cache;
DROP TABLE IF EXISTS cache.product_refusal_rate CASCADE;
CREATE TABLE cache.product_refusal_rate (
  sku          text,
  brand_id     text,
  delivered    int,
  refused      int,
  refusal_pct  numeric,   -- refused / (delivered+refused) * 100, distinct orders
  computed_at  timestamptz DEFAULT now(),
  PRIMARY KEY (sku, brand_id)
);
CREATE INDEX IF NOT EXISTS product_refusal_pct_idx ON cache.product_refusal_rate(refusal_pct DESC);
"""
PRODUCT_REFUSAL_SELECT = """
WITH li AS (
  SELECT oli.sku, oli."brandId" AS brand_id, o."name" AS order_name
  FROM order_line_items oli JOIN orders o ON o.id=oli."orderId"
  WHERE oli.sku IS NOT NULL AND oli.sku<>'' AND o."deletedAt" IS NULL
)
SELECT li.sku, li.brand_id,
  COUNT(DISTINCT li.order_name) FILTER (WHERE oo.status_category='Livrata')::int,
  COUNT(DISTINCT li.order_name) FILTER (WHERE oo.is_refusal)::int,
  ROUND(100.0*COUNT(DISTINCT li.order_name) FILTER (WHERE oo.is_refusal)
    / NULLIF(COUNT(DISTINCT li.order_name) FILTER (WHERE oo.status_category IN ('Livrata','Refuzata')),0),1)
FROM li JOIN cache.order_outcome oo ON oo.order_name=li.order_name
GROUP BY li.sku, li.brand_id;
"""

ORDER_ENRICHED_DDL = """
CREATE SCHEMA IF NOT EXISTS cache;
DROP TABLE IF EXISTS cache.order_enriched CASCADE;
CREATE TABLE cache.order_enriched (
  order_id           text PRIMARY KEY,
  order_name         text,
  brand_id           text,
  phone              text,
  email              text,
  customer_name      text,
  financial_status   text,
  fulfillment_status text,
  total_price        numeric,
  total_refunded     numeric,
  placed_at          timestamp,
  cancelled_at       timestamp,
  status_category    text,
  delivery_status    text,
  is_refusal         boolean,
  awb                text,
  courier_status     text,
  computed_at        timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS order_enriched_name_idx  ON cache.order_enriched(order_name);
CREATE INDEX IF NOT EXISTS order_enriched_phone_idx ON cache.order_enriched(phone);
"""
ORDER_ENRICHED_SELECT = """
SELECT o.id, o."name", o."brandId",
  NULLIF(regexp_replace(COALESCE(o."phone",o."shippingPhone",''),'[^0-9]','','g'),''),
  lower(NULLIF(o."email",'')),
  COALESCE(o."shippingName",o."name"),
  o."financialStatus"::text, o."fulfillmentStatus"::text,
  o."totalPrice", o."totalRefunded",
  o."shopifyCreatedAt", o."cancelledAt",
  oo.status_category, oo.delivery_status, oo.is_refusal, oo.awb, oo.courier_status
FROM public.orders o
LEFT JOIN cache.order_outcome oo ON oo.order_name = o."name"
WHERE o."deletedAt" IS NULL;
"""

# Market-basket "frequently bought together" per brand (last 180 days, co-count >= 3).
# Powers PDP cross-sell blocks, Klaviyo post-purchase flows, and 2+1 surprise pairings.
PRODUCT_BASKET_DDL = """
CREATE SCHEMA IF NOT EXISTS cache;
DROP TABLE IF EXISTS cache.product_basket_pairs CASCADE;
CREATE TABLE cache.product_basket_pairs (
  brand_id     text,
  product_a    text,
  product_b    text,
  title_a      text,
  title_b      text,
  co_count     int,       -- orders containing both products
  conf_a_to_b  numeric,   -- P(B | A)
  conf_b_to_a  numeric,   -- P(A | B)
  lift         numeric,
  window_days  int,
  computed_at  timestamptz DEFAULT now(),
  PRIMARY KEY (brand_id, product_a, product_b)
);
CREATE INDEX IF NOT EXISTS basket_brand_co_idx ON cache.product_basket_pairs(brand_id, co_count DESC);
"""
PRODUCT_BASKET_SELECT = """
WITH items AS (
  SELECT DISTINCT o."brandId" AS brand_id, oli."orderId" AS oid, oli."productId" AS pid
  FROM order_line_items oli JOIN orders o ON o.id=oli."orderId"
  WHERE o."deletedAt" IS NULL AND o."shopifyCreatedAt" >= now()-interval '180 days'
    AND oli."productId" IS NOT NULL AND oli."productId"<>''
),
titles AS (SELECT "productId" pid, MAX(title) title FROM order_line_items WHERE "productId" IS NOT NULL GROUP BY 1),
bo AS (SELECT brand_id, COUNT(DISTINCT oid) n FROM items GROUP BY 1),
pc AS (SELECT brand_id, pid, COUNT(DISTINCT oid) c FROM items GROUP BY 1,2),
pairs AS (
  SELECT a.brand_id, a.pid pa, b.pid pb, COUNT(*) co
  FROM items a JOIN items b ON a.oid=b.oid AND a.pid<b.pid
  GROUP BY 1,2,3 HAVING COUNT(*) >= 3
)
SELECT p.brand_id, p.pa, p.pb, ta.title, tb.title, p.co,
  ROUND(p.co::numeric/ca.c,4), ROUND(p.co::numeric/cb.c,4),
  ROUND((p.co::numeric*bo.n)/(ca.c*cb.c),3), 180
FROM pairs p
JOIN bo ON bo.brand_id=p.brand_id
JOIN pc ca ON ca.brand_id=p.brand_id AND ca.pid=p.pa
JOIN pc cb ON cb.brand_id=p.brand_id AND cb.pid=p.pb
LEFT JOIN titles ta ON ta.pid=p.pa
LEFT JOIN titles tb ON tb.pid=p.pb;
"""

TABLES = {
  "product_basket_pairs": {
    "ddl": PRODUCT_BASKET_DDL,
    "cols": "(brand_id,product_a,product_b,title_a,title_b,co_count,conf_a_to_b,conf_b_to_a,lift,window_days)",
    "select": PRODUCT_BASKET_SELECT,
    "count_sql": "SELECT COUNT(*) FROM (" + PRODUCT_BASKET_SELECT.rstrip().rstrip(';') + ") q",
  },
  "order_enriched": {
    "ddl": ORDER_ENRICHED_DDL,
    "cols": "(order_id,order_name,brand_id,phone,email,customer_name,financial_status,fulfillment_status,total_price,total_refunded,placed_at,cancelled_at,status_category,delivery_status,is_refusal,awb,courier_status)",
    "select": ORDER_ENRICHED_SELECT,
    "count_sql": "SELECT COUNT(*) FROM (" + ORDER_ENRICHED_SELECT.rstrip().rstrip(';') + ") q",
  },
  "product_refusal_rate": {
    "ddl": PRODUCT_REFUSAL_DDL,
    "cols": "(sku,brand_id,delivered,refused,refusal_pct)",
    "select": PRODUCT_REFUSAL_SELECT,
    "count_sql": "SELECT COUNT(*) FROM (" + PRODUCT_REFUSAL_SELECT.rstrip().rstrip(';') + ") q",
  },
  "customer_agg": {
    "ddl": CUSTOMER_AGG_DDL,
    "cols": "(identity,identity_type,sample_name,sample_email,order_count,cancelled,delivered,refused,refusal_rate,serial_refuser,net_value,brand_count,brand_ids,first_order,last_order)",
    "select": CUSTOMER_AGG_SELECT,
    "count_sql": "SELECT COUNT(*) FROM (" + CUSTOMER_AGG_SELECT.rstrip().rstrip(';') + ") q",
  },
}

# ---- order_outcome: SSH ETL from the VPS profit_orders SQLite (delivery outcome + AWB,
#      which are NOT in the metrics warehouse). Mirrors only operational columns (no PII). ----
ORDER_OUTCOME_DDL = """
CREATE SCHEMA IF NOT EXISTS cache;
CREATE TABLE IF NOT EXISTS cache.order_outcome (
  shop                text,
  prefix              text,
  order_name          text,
  created_at          timestamp,
  status_category     text,   -- Livrata / Refuzata / Anulata / Netrimisa / ...
  delivery_status     text,   -- DELIVERED / NOT_DELIVERED / OUT_FOR_DELIVERY / ...
  is_refusal          boolean,
  awb                 text,
  courier_key         text,
  courier_status      text,
  payment_status      text,
  fulfillment_status  text,
  revenue             numeric,   -- order revenue from profit_orders (for refused-revenue metrics)
  computed_at         timestamptz DEFAULT now(),
  PRIMARY KEY (shop, order_name)
);
ALTER TABLE cache.order_outcome ADD COLUMN IF NOT EXISTS revenue numeric;
CREATE INDEX IF NOT EXISTS order_outcome_name_idx ON cache.order_outcome(order_name);
CREATE INDEX IF NOT EXISTS order_outcome_refusal_idx ON cache.order_outcome(is_refusal) WHERE is_refusal;
"""

# Remote (runs on the VPS venv): emit clean TSV of operational columns only.
ORDER_OUTCOME_REMOTE = r"""
import sqlite3
p=sqlite3.connect('/root/Scripturi/data/profitability.db')
def cl(x):
    if x is None: return ''
    return str(x).replace('\t',' ').replace('\r',' ').replace('\n',' ')
q='''SELECT shop, prefix, order_name, created_at, status_category,
            shopify_delivery_status, awb, courier_key, courier_status,
            payment_status, fulfillment_status, revenue
     FROM profit_orders WHERE order_name IS NOT NULL AND order_name<>'' '''
import sys
for r in p.execute(q):
    sys.stdout.write('\t'.join(cl(c) for c in r)+'\n')
"""

def _ssh_pull_tsv():
    import paramiko, io
    host = secret("PROFIT_SSH_HOST") or "84.46.242.181"
    user = secret("PROFIT_SSH_USER") or "root"
    pwd  = secret("PROFIT_SSH_PASS")
    if not pwd:
        print("!! PROFIT_SSH_PASS not in secret store"); sys.exit(1)
    cl = paramiko.SSHClient(); cl.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cl.connect(host, username=user, password=pwd, timeout=30)
    sftp = cl.open_sftp()
    with sftp.open("/tmp/_oo.py", "w") as f:
        f.write(ORDER_OUTCOME_REMOTE)
    sftp.close()
    _, out, err = cl.exec_command("/root/Scripturi/.venv/bin/python /tmp/_oo.py", timeout=300)
    data = out.read().decode("utf-8", "replace"); e = err.read().decode().strip(); cl.close()
    if e: print(f"[remote stderr] {e[:300]}", file=sys.stderr)
    return data

def _local_db_path():
    import os
    for p in (os.environ.get("PROFITABILITY_DB"), "/root/Scripturi/data/profitability.db"):
        if p and os.path.exists(p):
            return p
    return None

def _local_pull_tsv(path):
    # Runs on the VPS itself (profit_orders is local) — no SSH needed.
    import sqlite3
    p = sqlite3.connect(path)
    def cl(x):
        return "" if x is None else str(x).replace("\t", " ").replace("\r", " ").replace("\n", " ")
    q = ("SELECT shop, prefix, order_name, created_at, status_category, shopify_delivery_status, "
         "awb, courier_key, courier_status, payment_status, fulfillment_status, revenue "
         "FROM profit_orders WHERE order_name IS NOT NULL AND order_name<>''")
    return "\n".join("\t".join(cl(c) for c in r) for r in p.execute(q))

def _pull_tsv():
    lp = _local_db_path()
    return _local_pull_tsv(lp) if lp else _ssh_pull_tsv()

def run_order_outcome(apply):
    tsv = _pull_tsv()
    rows = [l.split("\t") for l in tsv.splitlines() if l]
    print(f"[order_outcome] pulled {len(rows)} rows from profit_orders")
    if not apply:
        from collections import Counter
        c = Counter(r[4] for r in rows if len(r) > 4)
        print("[order_outcome] status_category:", dict(c.most_common()))
        print("DRY-RUN — nothing written. Re-run with --apply to materialize.")
        return
    dsn = secret("DATABASE_URL_METRICS")
    conn = psycopg2.connect(clean_dsn(dsn)); cur = conn.cursor()
    cur.execute(ORDER_OUTCOME_DDL)
    cur.execute("CREATE TEMP TABLE _oo_stage (LIKE cache.order_outcome INCLUDING DEFAULTS) ON COMMIT DROP")
    cur.execute("ALTER TABLE _oo_stage DROP COLUMN is_refusal, DROP COLUMN computed_at")
    import io
    buf = io.StringIO()
    for r in rows:
        r = (r + [""] * 12)[:12]
        buf.write("\t".join(r) + "\n")
    buf.seek(0)
    cur.copy_expert(
        "COPY _oo_stage (shop,prefix,order_name,created_at,status_category,delivery_status,"
        "awb,courier_key,courier_status,payment_status,fulfillment_status,revenue) "
        "FROM STDIN WITH (FORMAT text, NULL '')", buf)
    cur.execute("TRUNCATE cache.order_outcome")
    cur.execute("""
      INSERT INTO cache.order_outcome
        (shop,prefix,order_name,created_at,status_category,delivery_status,is_refusal,
         awb,courier_key,courier_status,payment_status,fulfillment_status,revenue)
      SELECT DISTINCT ON (shop,order_name)
        shop,prefix,order_name,
        created_at,
        status_category,delivery_status,
        (status_category='Refuzata'),
        awb,courier_key,courier_status,payment_status,fulfillment_status,revenue
      FROM _oo_stage
      ORDER BY shop, order_name, created_at DESC NULLS LAST
    """)
    cur.execute("SELECT COUNT(*), COUNT(*) FILTER (WHERE is_refusal) FROM cache.order_outcome")
    tot, ref = cur.fetchone()
    log_refresh(cur, "order_outcome", tot)
    conn.commit(); conn.close()
    print(f"[order_outcome] APPLIED — cache.order_outcome has {tot} rows ({ref} refusals).")

# ---- daily_brand_pnl: mirror the VPS daily_perf.db (per-brand daily P&L) into the warehouse
#      so multi-brand-pnl / agency-audit / daily-ops READ it from metrics instead of SSHing the SQLite.
DAILY_BRAND_PNL_DDL = """
CREATE SCHEMA IF NOT EXISTS cache;
DROP TABLE IF EXISTS cache.daily_brand_pnl CASCADE;
CREATE TABLE cache.daily_brand_pnl (
  date         date,
  brand_name   text,
  brand_id     text,
  orders       int,
  revenue      numeric,
  cogs         numeric,
  transport    numeric,
  fb_spend     numeric,
  tk_spend     numeric,
  google_spend numeric,
  total_spend  numeric,
  contribution_margin numeric,   -- daily_perf.profit = revenue - cogs - transport - total_spend
  roas         numeric,
  cpa          numeric,
  aov          numeric,
  computed_at  timestamptz DEFAULT now(),
  PRIMARY KEY (date, brand_name)
);
CREATE INDEX IF NOT EXISTS daily_brand_pnl_brand_idx ON cache.daily_brand_pnl(brand_id, date);
"""
DAILY_PERF_REMOTE = r"""
import sqlite3,sys
p=sqlite3.connect('/root/Scripturi/data/daily_perf.db')
def cl(x): return '' if x is None else str(x).replace('\t',' ').replace('\n',' ')
q='''SELECT date,brand,orders,revenue,cogs,transport,fb_spend,tk_spend,google_spend,
            total_spend,profit,roas,cpa,aov FROM daily_perf'''
for r in p.execute(q): sys.stdout.write('\t'.join(cl(c) for c in r)+'\n')
"""
def _pull_daily_perf():
    import os
    lp = os.environ.get("DAILY_PERF_DB") or "/root/Scripturi/data/daily_perf.db"
    if os.path.exists(lp):
        import sqlite3
        p=sqlite3.connect(lp)
        q=("SELECT date,brand,orders,revenue,cogs,transport,fb_spend,tk_spend,google_spend,"
           "total_spend,profit,roas,cpa,aov FROM daily_perf")
        return [tuple(r) for r in p.execute(q)]
    # SSH fallback (running off the VPS)
    import paramiko
    pwd=secret("PROFIT_SSH_PASS")
    cli=paramiko.SSHClient(); cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(secret("PROFIT_SSH_HOST") or "84.46.242.181", username=secret("PROFIT_SSH_USER") or "root", password=pwd, timeout=30)
    sftp=cli.open_sftp()
    with sftp.open("/tmp/_dpp.py","w") as f: f.write(DAILY_PERF_REMOTE)
    sftp.close()
    _,out,_=cli.exec_command("/root/Scripturi/.venv/bin/python /tmp/_dpp.py", timeout=120)
    data=out.read().decode("utf-8","replace"); cli.close()
    rows=[]
    for ln in data.splitlines():
        if not ln: continue
        rows.append(tuple((ln.split("\t")+[""]*14)[:14]))
    return rows

def run_daily_brand_pnl(apply):
    import re
    from psycopg2.extras import execute_values
    norm=lambda s: re.sub(r"[^a-z0-9]","",(s or "").strip().lower())
    src=_pull_daily_perf()
    mconn=psycopg2.connect(clean_dsn(secret("DATABASE_URL_METRICS"))); mcur=mconn.cursor()
    mcur.execute("SELECT id,name FROM brands WHERE name IS NOT NULL")
    bname={norm(n):i for i,n in mcur.fetchall()}
    def num(x):
        try: return float(x) if x not in (None,"") else None
        except: return None
    rows=[]; unmatched=set()
    for r in src:
        d,brand,orders,rev,cogs,tr,fb,tk,gg,tot,profit,roas,cpa,aov=r
        bn=norm(brand); bid=bname.get(bn) or (bname.get(bn[:-2]) if bn.endswith("ro") else None)
        if bid is None: unmatched.add(brand)
        rows.append((d or None, brand, bid, int(float(orders)) if orders not in (None,"") else None,
                     num(rev),num(cogs),num(tr),num(fb),num(tk),num(gg),num(tot),num(profit),num(roas),num(cpa),num(aov)))
    print(f"[daily_brand_pnl] {len(rows)} rows from daily_perf; brands unmatched to metrics: {sorted(unmatched) or 'none'}")
    if not apply:
        mconn.rollback(); mconn.close(); print("DRY-RUN — nothing written."); return
    mcur.execute(DAILY_BRAND_PNL_DDL); mcur.execute("TRUNCATE cache.daily_brand_pnl")
    execute_values(mcur,
        "INSERT INTO cache.daily_brand_pnl (date,brand_name,brand_id,orders,revenue,cogs,transport,"
        "fb_spend,tk_spend,google_spend,total_spend,contribution_margin,roas,cpa,aov) VALUES %s "
        "ON CONFLICT (date,brand_name) DO NOTHING", rows, page_size=2000)
    mcur.execute("SELECT COUNT(*) FROM cache.daily_brand_pnl"); n=mcur.fetchone()[0]
    log_refresh(mcur,"daily_brand_pnl",n)
    mconn.commit(); mconn.close()
    print(f"[daily_brand_pnl] APPLIED — cache.daily_brand_pnl now has {n} rows.")

def run(table, apply):
    if table == "order_outcome":
        return run_order_outcome(apply)
    if table == "daily_brand_pnl":
        return run_daily_brand_pnl(apply)
    if table == "daily_ad_spend_ron":
        return run_daily_ad_spend(apply)
    if table == "daily_ad_spend_ron":
        return run_daily_ad_spend(apply)
    if table == "product_ad_spend":
        return run_product_ad_spend(apply)
    spec = TABLES[table]
    dsn = secret("DATABASE_URL_METRICS")
    if not dsn:
        print("!! DATABASE_URL_METRICS not found in secret store"); sys.exit(1)
    conn = psycopg2.connect(clean_dsn(dsn)); conn.autocommit = False
    cur = conn.cursor()
    # dry-run: count + sample, no writes
    cur.execute(spec["count_sql"]); n = cur.fetchone()[0]
    print(f"[{table}] rows the refresh would produce: {n}")
    if not apply:
        cur.execute(spec["select"].rstrip().rstrip(';') + " LIMIT 5")
        print(f"[{table}] sample rows:")
        for r in cur.fetchall():
            print("   ", " | ".join(str(x) for x in r[:8]))
        conn.rollback(); conn.close()
        print("DRY-RUN — nothing written. Re-run with --apply to materialize.")
        return
    # apply: ensure schema/table, then transactional truncate+insert
    cur.execute(spec["ddl"])
    cur.execute(f"TRUNCATE cache.{table}")
    cur.execute(f"INSERT INTO cache.{table} {spec['cols']} " + spec["select"])
    cur.execute(f"SELECT COUNT(*) FROM cache.{table}")
    written = cur.fetchone()[0]
    log_refresh(cur, table, written)
    conn.commit(); conn.close()
    print(f"[{table}] APPLIED — cache.{table} now has {written} rows.")

if __name__ == "__main__":
    # dependency order: order_outcome first (the others LEFT JOIN it)
    ALL = ["order_outcome", "daily_brand_pnl", "daily_ad_spend_ron", "product_ad_spend", "product_refusal_rate", "product_basket_pairs", "customer_agg", "order_enriched"]
    # named groups for cron cadences (see SKILL.md): cs = intraday CS-facing; ads = current-day spend
    GROUPS = {"cs": ["order_enriched", "customer_agg"], "ads": ["daily_ad_spend_ron"], "all": ALL}
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", choices=ALL)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--group", choices=list(GROUPS), help="refresh a named group (cs|ads|all)")
    ap.add_argument("--status", action="store_true", help="show freshness + data period of every cache table")
    ap.add_argument("--apply", action="store_true", help="write to prod (default is dry-run)")
    a = ap.parse_args()
    if a.status:
        show_status(); sys.exit(0)
    targets = ALL if a.all else (GROUPS[a.group] if a.group else ([a.table] if a.table else []))
    if not targets:
        print("specify --table <name>, --group <cs|ads|all>, --all, or --status"); sys.exit(1)
    for t in targets:
        run(t, a.apply)
