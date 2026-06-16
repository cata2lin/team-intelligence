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

### Roadmap (from skills-audit.md), add as `--table`:
`order_enriched` (per-order join of orders+outcome+customer for one-row CS lookups),
`ticket_order_link` (mostly already in `richpanel_tickets` cols),
`product_basket_pairs`, `rma_signal_daily`, `daily_brand_pnl` (replaces SSH `daily_perf.db`),
`dataforseo_cache` (the only pay-per-call source).

## Refresh / cron
Schedule on the VPS that already runs the team crons (see memory
`profitability-marketing-feed-fix`): e.g. `customer_agg` daily, `order_enriched` hourly
once it exists. One line per table: `uv run build_cache.py --table <t> --apply`.

## Adding a table
Add an entry to `TABLES` in `build_cache.py` with its `ddl`, column list, and refresh
`select` (validate the SELECT read-only first via the postgres-metrics MCP). Keep all
objects in the `cache` schema.
