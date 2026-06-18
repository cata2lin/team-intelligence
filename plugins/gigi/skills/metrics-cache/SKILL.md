---
name: metrics-cache
description: Materialize shared CACHE tables in the metrics warehouse (schema `cache.*`) so Customer-Service and other skills READ precomputed aggregates instead of recomputing the same expensive queries live every run. Idempotent, transactional, dry-run by default. Use for "build/refresh the cache layer", "customer_agg", "speed up CS skills", "stop recomputing LTV/refusal every run". Companion to the team skills-audit (shared/skills-audit.md).
argument-hint: "--table customer_agg [--apply]  |  --all --apply"
---

# metrics-cache — the shared read-cache for the warehouse
> Author: **Gigi**. Implements the cache layer from `shared/skills-audit.md`.

Many skills (esp. the ~25 CS skills) re-derive the same expensive aggregates from
`public.orders` / `richpanel_tickets` on every run. This skill precomputes them once
into a dedicated **`cache` schema** (never touches the BI app's `public` schema) so
skills just `SELECT` from `cache.*`.

```bash
uv run scripts/build_cache.py --table customer_agg            # DRY-RUN (counts + sample, no writes)
uv run scripts/build_cache.py --table customer_agg --apply    # materialize / refresh
uv run scripts/build_cache.py --all --apply                   # refresh every cache table
```

## Safety model
- **Dry-run by default**; `--apply` is the only thing that writes.
- Writes go to a **separate `cache` schema** via `DATABASE_URL_METRICS` (DSN cleaned of
  Prisma params). Never the BI `public` tables.
- Refresh = `CREATE … IF NOT EXISTS` then a single transactional `TRUNCATE + INSERT…SELECT`
  (atomic; readers never see a half-built table). Re-runnable any time.
- Read-only secret handling via `kb.py secret-get` (never printed).

## Tables (LIVE)
### `cache.order_outcome`  (~294k rows; SSH ETL from VPS `profit_orders`)
The delivery outcome + AWB that the metrics warehouse does NOT have, mirrored from the
profitability SQLite over SSH (operational columns only, **no PII**):
`shop, prefix, order_name, created_at, status_category (Livrata/Refuzata/Anulata/…),
delivery_status, is_refusal, awb, courier_key, courier_status, payment/fulfillment_status`.
Joins to `public.orders` on `order_name = orders.name` (**91% coverage**, 110,990/122,057).
Readers: every CS skill that needs "was it refused / where's the AWB".

### `cache.customer_agg`  (~107k rows; in-DB from `orders` ⨝ `order_outcome`)
Per-customer identity (normalized phone, else email):
`order_count, cancelled, delivered, refused, refusal_rate, serial_refuser,
net_value (Σ totalPrice−totalRefunded on non-cancelled), brand_count, brand_ids[],
first/last_order, sample_name/email`. **1,005 serial-refusers / 16,551 with ≥1 refusal**
precomputed. Readers: `cs-customer-360`, `cs-profile`, `cs-conversation-profile`,
`cs-draft-reply`, `cod-confirmation`, `customer-identity`.

> `metrics.fx_rates` already exists — skills should read it rather than re-deriving FX.

### `cache.daily_ad_spend_ron`  (in-DB from metrics ad-insights ⨝ brand_*_ad_accounts)
Per `date × brand_id × platform(meta|google|tiktok)` spend in RON — **RON already precomputed**
in the insights tables (`spendRon`/`costRon`), no FX needed. Readers: multi-brand-pnl,
agency-audit, weekly-insights, grandia-pnl, daily-ops-briefing. _Caveat:_ insights are
account-level, so an ad account shared across brands attributes full spend to each (the gap
`bi-data-integrity-check` flags); fix mapping there.

### `cache.product_refusal_rate`  (in-DB from order_line_items ⨝ orders ⨝ order_outcome)
Per `sku × brand_id`: distinct-order `delivered, refused, refusal_pct`. Matches the known
"HA-* products refuse 40-56%" reality. Readers: cod-confirmation, product-quality-radar,
anne:ha-refuz, cs-* risk scoring.

### `cache.product_basket_pairs`  (in-DB market-basket, last 180d, co-count ≥ 3)
Per `brand × product_a × product_b`: `co_count, conf_a_to_b, conf_b_to_a, lift` + titles.
"Frequently bought together" — powers PDP cross-sell, Klaviyo post-purchase flows, 2+1 pairings.
Readers: gigi:cross-sell, PDP FBT blocks, klaviyo flows.

### `cache.product_ad_spend`  (per-SKU ad spend — Google native + Meta/TikTok via Nomenclator) ✅
`date × sku × platform → spend_ron` (+ brand_id, product_title, source). **Google** native per-product via
`google_ads_product_insights_daily`. **Meta+TikTok** now mapped per-SKU/group from **LIVE campaign/ad names**
via the KB Nomenclator rules (`kb_meta['ad_campaign_rules']`: `HA-####`→SKU, else product_group; TEST bucketed
separately), built by `scripts/ad_spend_live.py` — per-day FX, exact brand attribution from the Mapping sheet,
monthly chunking. **Validated ±1% vs Raport Zilnic 2.** Source `meta_tiktok_campaign_map`. VPS cron 5:30
(`/root/ad-spend/run_daily.sh`, incremental); year backfill `ad_spend_live.py --since 2025-01-01 --apply`.
KB rules: `meta-ads/kb_rules.py seed`; coverage: `kb_rules.py coverage`. (build_cache product_ad_spend is now
INCREMENTAL — never drops.) Feeds product_economics/POAS **and per-SKU profitability** (below).
**Conturi TikTok partajate** (un advertiser, mai multe branduri): atribuire pe **token global** din numele
campaniei (orice `ESTEBAN/MAGDEAL/…` → brandul lui, oriunde rulează; token-ul cel mai lung câștigă),
fallback pe **owner**-ul contului (brandul dedicat, filter None din Mapping); fără token ȘI fără owner =
orfan (raportat pe stderr, nu inventat). Reguli specifice pe cont în `ACCT_BRAND_RULES` (ex. pe contul
`Belasil`, testele `NEW TIKTOK` fără token = Esteban). Re-backfill **doar o platformă** fără să atingi
cealaltă: `ad_spend_live.py --platform tiktok --since 2025-01-01 --apply` (pur upsert → Facebook neatins;
0 rânduri = nu scrie, ca să nu strice datele la rețea flaky).

### Per-SKU / per-category PROFITABILITY (`scripts/profit_by_sku.py`, `profit_by_category.py`) ✅
Real P&L per SKU and per product_group — **same formula as `api.profitability` / `grandia_pnl`**
(Venit − COGS − Transport − Marketing, ex-VAT / TVA deductibil, gross+net), additive & read-only (does NOT
touch the prod engine). Sources: `profit_order_lines` (per-line sku/qty/revenue/cogs captured by
`profit_lines_sync.py` via extended Shopify GraphQL line price — handles 2+1 free via net discountedTotal)
JOINed to `profit_orders` (delivered only); transport allocated, flagged **REAL** (DPD audit nomenclator)
vs **ESTIMAT** with a ⚠️ notification of SKUs/%revenue lacking real transport; marketing = real per-SKU from
`cache.product_ad_spend` (NOT a flat assumption). Run `uv run profit_by_sku.py 2026-05` / `profit_by_category.py 2026-05`.
VPS cron 6:15 (`run_lines_daily.sh` refreshes `profit_order_lines`). Reconciles with the engine per-prefix.
TODO: exact per-order transport from AWBprint `order_awbs.transport_cost_fara_tva` (order-level match).

### `cache.daily_brand_pnl`  (mirror of the VPS `daily_perf.db`, per-brand daily P&L — ESTIMATE)
`date × brand → orders, revenue, cogs, transport, fb/tk/google/total spend, contribution_margin, roas, cpa, aov`.
Daily granularity + FB/Google/TikTok split. ⚠ **GROSS revenue (with VAT) on ALL orders** — this
OVERSTATES profit (does not account for COD non-delivery or VAT). Good for daily trend / platform
split, NOT for real profit. 9,392 rows, 29 brands. (Read locally on the VPS cron host.)

### `cache.brand_pnl_monthly`  (CANONICAL real P&L — from the Scripturi profitability engine)
`month × brand → delivered_orders, sent_parcels, revenue_exvat, cogs_exvat, transport_exvat,
marketing, net_profit, margin_pct`. Built by running the real engine `api.profitability.get_report`
on the VPS (revenue = **DELIVERED orders only, EX-VAT**, minus COGS + transport + marketing).
This is the **REAL net profit** — use it for "profit per brand", "% of profit", P&L. MONTHLY
granularity (delivered status settles over weeks; current month is incomplete). 118 rows (~20
brands × 6 months). Refresh key `brand_pnl_real`. Reader: **multi-brand-pnl** (default).
Validated to the cent vs the Scripturi app (Apr net 1,161,124 / May 1,392,955).

### `cache.ticket_order_link`  (in-DB; refreshed in `--group cs` intraday)
Per Richpanel ticket with an order: `order_name, resolved_store, ticket_status, category, contact_*` +
the linked order's delivery outcome (`status_category, is_refusal, delivery_status, awb`). Lets CS
triage/profile/draft see "is this ticket about a refused/stuck order" instantly. ~30k links.
Readers: cs-conversation-profile, cs-draft-reply, richpanel-auto-triage.

### `cache.product_returns`  (cross-DB from Grandia RMA)
Per `brand × sku`: `return_requests, return_qty, top_reason, last_return` (180d). Grandia-only (the
only brand with structured RMA). Feeds product-quality-radar / returns reporting. 143 SKUs.

### Roadmap (from skills-audit.md):
`dataforseo_cache` (on-call TTL cache, belongs in gigi:analytics, not this batch builder).

## Freshness — ALWAYS know what you're reading (it's a snapshot, not live)
The cache is materialized on demand; between refreshes it goes stale. Every refresh is
recorded in **`cache.refresh_log`** and exposed via the view **`cache.freshness`**
(rows, refreshed_at, age_hours, max_age_hours, **stale**, **data_from**, **data_to**).

```bash
uv run scripts/build_cache.py --status      # human table: age + STALE flag + DATA COVERS period
```
```
TABLE                  ROWS  AGE(h) STATUS    DATA COVERS
order_outcome        294293     0.0 fresh     2025-12-31 → 2026-06-10   (delivery outcome lags ~days)
daily_ad_spend_ron      300     0.0 fresh     2026-04-07 → 2026-06-16   (ad insights retain ~2 months)
customer_agg         106925     0.0 fresh     2025-01-23 → 2026-06-16
order_enriched       122057     0.0 fresh     2025-01-23 → 2026-06-16
product_refusal_rate   1337     0.0 fresh     (all-time, no date)
```
**Reader contract:** any skill (and any answer to a user) built on `cache.*` MUST first
`SELECT * FROM cache.freshness` and surface "data as of {refreshed_at}, covers {data_from}→{data_to};
STALE — refresh" when `stale`. Don't present cached numbers as live without the as-of line.
Note the **coverage windows differ** per table (above): e.g. ad spend only goes back ~2 months,
delivery outcome trails real-time by a few days — say so when it matters.

## Refresh (easy, one command)
```bash
uv run scripts/build_cache.py --all --apply        # refresh every table (dependency-ordered)
uv run scripts/build_cache.py --table <t> --apply  # just one
```
Automate on the VPS that runs the team crons (see memory `profitability-marketing-feed-fix`),
e.g. nightly:
```cron
0 5 * * *  cd <repo>/plugins/gigi/skills/metrics-cache/scripts && uv run build_cache.py --all --apply
```
`--all` order is fixed so `order_outcome` builds before the tables that LEFT JOIN it.

## Adding a table
Add an entry to `TABLES` in `build_cache.py` with its `ddl`, column list, and refresh
`select` (validate the SELECT read-only first via the postgres-metrics MCP). Keep all
objects in the `cache` schema.
