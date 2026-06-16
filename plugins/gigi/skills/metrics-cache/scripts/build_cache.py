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
    return subprocess.run(["uv", "run", _kb(), "secret-get", key],
                          capture_output=True, text=True).stdout.strip()

def clean_dsn(dsn):
    """Strip Prisma-style query params psycopg2 rejects (schema, pgbouncer,
    connection_limit, …); keep only a libpq-safe allowlist."""
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
    u = urlsplit(dsn)
    keep = {"sslmode", "sslrootcert", "sslcert", "sslkey", "connect_timeout", "application_name"}
    q = [(k, v) for k, v in parse_qsl(u.query) if k in keep]
    return urlunsplit((u.scheme, u.netloc, u.path, urlencode(q), u.fragment))

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

DAILY_AD_SPEND_DDL = """
CREATE SCHEMA IF NOT EXISTS cache;
DROP TABLE IF EXISTS cache.daily_ad_spend_ron CASCADE;
CREATE TABLE cache.daily_ad_spend_ron (
  date        date,
  brand_id    text,
  platform    text,   -- meta / google / tiktok
  spend_ron   numeric,
  computed_at timestamptz DEFAULT now(),
  PRIMARY KEY (date, brand_id, platform)
);
CREATE INDEX IF NOT EXISTS daily_ad_spend_brand_idx ON cache.daily_ad_spend_ron(brand_id, date);
"""
# RON is already precomputed in the insights tables (spendRon/costRon) — no FX needed.
# Caveat: an ad account shared across brands (campaignFilter) attributes full account
# spend to each mapped brand (insights are account-level, not campaign-level).
DAILY_AD_SPEND_SELECT = """
SELECT date, brand_id, platform, ROUND(SUM(spend_ron),2)
FROM (
  SELECT i.date, m."brandId" AS brand_id, 'meta' AS platform, i."spendRon" AS spend_ron
  FROM meta_ad_insights_daily i JOIN brand_meta_ad_accounts m
    ON m."adAccountId"=i."adAccountId" AND m."isActive"
  UNION ALL
  SELECT i.date, g."brandId", 'google', i."costRon"
  FROM google_ads_insights_daily i JOIN brand_google_ads_accounts g
    ON g."customerAccountId"=i."customerAccountId" AND g."isActive"
  UNION ALL
  SELECT i.date, t."brandId", 'tiktok', i."spendRon"
  FROM tiktok_ad_insights_daily i JOIN brand_tiktok_ad_accounts t
    ON t."adAccountId"=i."adAccountId" AND t."isActive"
) s
WHERE spend_ron IS NOT NULL
GROUP BY date, brand_id, platform;
"""

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

TABLES = {
  "daily_ad_spend_ron": {
    "ddl": DAILY_AD_SPEND_DDL,
    "cols": "(date,brand_id,platform,spend_ron)",
    "select": DAILY_AD_SPEND_SELECT,
    "count_sql": "SELECT COUNT(*) FROM (" + DAILY_AD_SPEND_SELECT.rstrip().rstrip(';') + ") q",
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
  computed_at         timestamptz DEFAULT now(),
  PRIMARY KEY (shop, order_name)
);
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
            payment_status, fulfillment_status
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

def run_order_outcome(apply):
    tsv = _ssh_pull_tsv()
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
        r = (r + [""] * 11)[:11]
        buf.write("\t".join(r) + "\n")
    buf.seek(0)
    cur.copy_expert(
        "COPY _oo_stage (shop,prefix,order_name,created_at,status_category,delivery_status,"
        "awb,courier_key,courier_status,payment_status,fulfillment_status) "
        "FROM STDIN WITH (FORMAT text, NULL '')", buf)
    cur.execute("TRUNCATE cache.order_outcome")
    cur.execute("""
      INSERT INTO cache.order_outcome
        (shop,prefix,order_name,created_at,status_category,delivery_status,is_refusal,
         awb,courier_key,courier_status,payment_status,fulfillment_status)
      SELECT DISTINCT ON (shop,order_name)
        shop,prefix,order_name,
        created_at,
        status_category,delivery_status,
        (status_category='Refuzata'),
        awb,courier_key,courier_status,payment_status,fulfillment_status
      FROM _oo_stage
      ORDER BY shop, order_name, created_at DESC NULLS LAST
    """)
    cur.execute("SELECT COUNT(*), COUNT(*) FILTER (WHERE is_refusal) FROM cache.order_outcome")
    tot, ref = cur.fetchone()
    conn.commit(); conn.close()
    print(f"[order_outcome] APPLIED — cache.order_outcome has {tot} rows ({ref} refusals).")

def run(table, apply):
    if table == "order_outcome":
        return run_order_outcome(apply)
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
    conn.commit(); conn.close()
    print(f"[{table}] APPLIED — cache.{table} now has {written} rows.")

if __name__ == "__main__":
    ALL = list(TABLES) + ["order_outcome"]
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", choices=ALL)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--apply", action="store_true", help="write to prod (default is dry-run)")
    a = ap.parse_args()
    targets = ALL if a.all else ([a.table] if a.table else [])
    if not targets:
        print("specify --table <name> or --all"); sys.exit(1)
    for t in targets:
        run(t, a.apply)
