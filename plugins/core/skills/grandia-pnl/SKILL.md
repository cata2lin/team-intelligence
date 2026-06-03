---
name: grandia-pnl
description: Build a live monthly P&L for the Grandia brand from Shopify (orders/revenue/refunds) + AWBprint (per-SKU COGS and transport actuals) + Meta/Google Ads/TikTok ad spend, optionally writing a styled Google Sheet. Use when asked for a Grandia P&L, monthly profit & loss, contribution margin, or MER for a date range.
---

# grandia-pnl

> Author: **Arona core**.

End-to-end recipe for a Profit & Loss report for the **Grandia** brand, fully
populated from live sources — Shopify Admin GraphQL for orders, AWBprint for
transport actuals + per-SKU cost, and Meta / Google Ads / TikTok marketing APIs
for ad spend. Optionally renders a styled Google Sheet tab.

**Implementation:** `${CLAUDE_PLUGIN_ROOT}/scripts/grandia_pnl.py`
**Diagnostics:** `${CLAUDE_PLUGIN_ROOT}/scripts/diag_shopify_orders.py`,
`${CLAUDE_PLUGIN_ROOT}/scripts/_diag_quick.py` (both import helpers from
`grandia_pnl`).

## ⚠️ Prerequisite — third-party secrets must be populated

This skill talks to five external platforms. It can only run **end-to-end** once
the relevant secrets are populated in the **SharedClaude secret store**:

- **Shopify** store client id/secret (in `metrics.shopify_stores`, reached via `DATABASE_URL_METRICS`)
- **Meta / Google Ads / TikTok** tokens (in `metrics.*`, reached via `DATABASE_URL_METRICS`)
- **DPD** REST creds: `DPD_RO_USERNAME`, `DPD_RO_PASSWORD`, optional `DPD_API_BASE` (and `DPD_JG_*`)
- **Google Sheets** OAuth (only if `--sheet-id` is used): `GOOGLE_OAUTH_TOKEN_FILE`, `GOOGLE_OAUTH_SCOPES`
- DB connection strings: `DATABASE_URL_METRICS`, `DATABASE_URL_AWBPRINT`, `DATABASE_URL_GRANDIA`

The script loads all of these at startup via `from kb_env import load_secrets_into_env;
load_secrets_into_env()` (which fills `os.environ` from `$KB_DATABASE_URL`). Check what's
present with `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/kb.py" secret-list`. If a required key
is missing, the run exits with `missing <KEY> in the knowledge base secret store`. Populate
it (`kb.py secret-set <KEY> …`) before re-running — never print secret values into chat.

## CLI usage

Run with `uv` (PEP 723 inline deps auto-install; `psycopg2-binary` is declared):

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/grandia_pnl.py" \
    --start 2026-04-01 --end 2026-04-30 \
    --sheet-id 1n9Pl-yCaTse-acdvXtiLHtrSxq5mD2iD72y9Z3Am4mk \
    --tab "P&L April 2026" \
    --spot-check 50
```

Flags:
- `--start` / `--end` — inclusive civil dates (Europe/Bucharest). **Required.**
- `--sheet-id` — optional; **only this flag triggers a Sheet write.** Always dry-run
  without it first and validate the numbers before pushing.
- `--tab` — sheet tab name (default `P&L <Mon YYYY>`).
- `--spot-check N` — N random DPD AWBs verified via `/track` (default 50; existence smoke test).
- `--transport-flat-per-order R` — override: assume R RON net transport per included order
  (for partial months where AWBs aren't invoiced yet).
- `--json` — also emit a machine-readable summary to stdout.

The script prints a step-by-step summary to **stderr** first; the Sheet write is the only
side effect on `--sheet-id`.

## Why Shopify, not AWBprint, for orders

Grandia is **cash-on-delivery dominant**. The Frisbo → AWBprint sync is stale: AWBprint
reports ~12% of orders as `paid` while Shopify (which reconciles the actual COD courier
cash) reports ~80% as `PAID`. **Shopify is the source of truth for revenue and financial
status.** AWBprint remains authoritative for shipping actuals (courier, AWB count, net
transport cost) and for per-SKU landed cost.

## Frozen identifiers (constants in the script)

| Thing | Value |
| --- | --- |
| Grandia Shopify domain | `n12w89-yy.myshopify.com` |
| Grandia store UID (AWBprint) | `8a438d7e-fed5-4114-8ef9-61d9ceaed6a9-1765442303-RZF1BEIFMY` |
| Grandia brand ID (metrics) | `cmo5ulyl80003h1w2xlzfzhvh` |
| Meta ad account | `act_1733723547182468` (RON) |
| Google Ads customer | `9069610821` (RON), login `7467110480` (Novos MCC) |
| TikTok advertiser | `7538854926504558610` (USD) |

To repoint at another brand, change `GRANDIA_STORE_UID`, `GRANDIA_BRAND_ID`,
`GRANDIA_SHOPIFY_DOMAIN`; the metrics brand→account joins key off the brand ID.

## Conventions

- **VAT**: Romanian standard rate **21%** → `VAT_DIVISOR = 1.21`. Net = gross / 1.21
  (`VAT_RATE` is the single knob).
- **Period filter** (Shopify side): `created_at:>=<start> created_at:<<end+1d>` using
  explicit **Europe/Bucharest** ISO+TZ boundaries (so the window matches the Shopify admin
  date filter; bare `YYYY-MM-DD` is interpreted loosely / UTC-ish and over-counts). `--end`
  is **inclusive**.
- **Order filter**: `displayFinancialStatus ∈ {PAID, PARTIALLY_REFUNDED, REFUNDED}`.
  Anything else (`PENDING`, `VOIDED`, …) is reported informationally and excluded from
  revenue, COGS and transport.
- **Refunds**: use `currentTotalPriceSet` (already post-refund) and `lineItems.currentQuantity`
  (already post-refund). `totalRefundedSet` is shown as a separate informational line — it is
  **not** subtracted again.
- **Currency**: every monetary line is RON; native amounts preserved for non-RON ad accounts.
- **FX**: per-day rate from `AWBprint.exchange_rates` (`ron_per_unit = rate / multiplier`),
  forward-filled across weekends/holidays from the most recent prior business-day rate. The
  index pads 10 days before `--start` to make weekend forward-fill possible.

## P&L line definitions

1. **Revenue (gross, incl VAT)** — `Σ order.currentTotalPriceSet.shopMoney.amount` over
   included orders. Net = gross / 1.21. Sub-lines: subtotal, discounts, shipping charged,
   refunds issued (informational).
2. **VAT collected on revenue** — `gross_revenue − net_revenue`.
3. **COGS** — `Σ (lineItem.currentQuantity × sku_costs.cost)` joined on `lineItem.sku =
   sku_costs.sku` (falls back to `variant.sku`). `sku_costs.cost` is gross; net = gross / 1.21.
   All Grandia `sku_costs.currency = RON`; a non-RON cost emits a stderr WARN and is summed
   verbatim. The script logs `lines_missing_cost`, `units_missing_cost`, and the distinct
   missing-SKU list — investigate if it grows.
4. **Gross margin** — `net_revenue − net_cogs` (plus % of net revenue).
5. **Transport** — see the 3-stage pipeline below.
6. **Ad spend (live APIs)** — fetched at run time, **no** reading from `metrics.*_insights_daily`
   caches; the metrics DB is consulted only for brand → account → token mappings.
   - **Meta**: `GET graph.facebook.com/v23.0/{account}/insights?fields=spend&time_range=…&level=account`.
     Token via `meta_access_tokens` ⨯ `meta_ad_accounts` ⨯ `brand_meta_ad_accounts`. RON, no FX.
   - **Google Ads**: `POST googleads.googleapis.com/v20/customers/{cid}/googleAds:search`, GAQL
     `SELECT segments.date, metrics.cost_micros FROM customer WHERE segments.date BETWEEN … AND …`.
     Refresh-token exchange each run; `developerToken/refreshToken/oauthClientId/oauthClientSecret`
     from `google_ads_connections`; header `login-customer-id`. `cost_micros / 1e6`, RON, no FX.
   - **TikTok**: `GET business-api.tiktok.com/open_api/v1.3/report/integrated/get/`,
     `report_type=BASIC`, `data_level=AUCTION_ADVERTISER`, `dimensions=["stat_time_day"]`,
     `metrics=["spend"]`. Token via `tiktok_access_tokens`. Currency **USD** → converted to
     RON **per `stat_time_day`** (not month-average) from the AWBprint FX index.
7. **Contribution margin** — `net_revenue − net_cogs − net_transport − ad_spend_total`. First
   profitability line including variable marketing; fixed costs are not sourced here.
8. **MER** — `net_revenue / total_ad_spend` (unit-less; `2.54` = 2.54 RON net per 1 RON ad).

## Transport — the 3-stage pipeline

Base set: all rows in `AWBprint.order_awbs` whose `order_id` maps to an `AWBprint.orders`
row with `store_uid = <Grandia>` and `order_number ∈ {included Shopify order names}`. Join key:
`AWBprint.orders.order_number == Shopify order.name` (e.g. `GRAND7060`). `transport_cost_fara_tva`
is **net** (feeds contribution margin); `transport_cost` is **gross**. Every AWB ends up with a
`source` tag, surfaced as rows 5a/5b/5c:

1. **`awbprint` (measured)** — `transport_cost_fara_tva > 0` directly on `order_awbs` (courier's
   invoiced/quoted net price captured by Frisbo at create-time).
2. **`grandia_shipments` (backfilled)** — for measured-cost-missing rows, look up the tracking
   number in `Grandia.courier_shipments` with `dpdResponse.price.total/vat` populated. Only
   catches AWBs **grandia-inventory** created directly (RMA returns, manual reshipments). Bulk
   Frisbo-generated AWBs are **not** here.
3. **`estimated`** — for rows still missing cost, fill with the **same-courier mean net cost
   per AWB** for the period (e.g. April-2026 DPD mean × N missing DPD AWBs). If a courier has
   zero measured rows, fall back to the global mean. The report always shows measured /
   backfilled / estimated counts and amounts in separate rows.

**Why estimate at all:** the DPD Romania REST API does not expose price for already-created
parcels we don't own — `/track` returns events but no price (and `shipment-not-accessible` for
non-RO accounts), `/shipment/search` returns barcodes not price, `/shipment/calculate` prices a
hypothetical. Historical price lives only in the original create response (captured solely for
grandia-inventory-direct shipments) or the monthly portal-PDF invoice. Bulk AWBs run on Frisbo's
own DPD account, so even the create response never reaches our DBs.

`--spot-check N` picks random DPD AWBs and calls `/track` to confirm they exist;
`agree=0 disagree=0 checked=N` is normal (existence smoke test, not price reconciliation).

**Diagnostics** (stderr): `orders_missing_awb` (included orders with no `order_awbs` row —
usually very recent, not yet flushed through Frisbo) and `unmatched_order_names` (included
Shopify orders with no matching `AWBprint.orders` row — should be 0 for any non-current month).

## Output sheet layout

Single tab `P&L <Mon YYYY>` (or `--tab`), 9 columns: Line · Qty · Unit · Gross (RON) ·
Net (RON, ex-VAT) · Currency · % of net rev · Notes. Verdana 11, hidden gridlines, dark-blue
header bands, frozen top rows, methodology + caveats footer + DPD spot-check counts. Per-AWB
DPD drift detail stays in stderr / `--json`, not the sheet.

## Sanity checks before publishing

1. `included_orders / raw_order_count` in the expected range (Grandia Apr 2026 ≈ 83%).
2. `transport.matched_order_count == len(included_orders)` (or unmatched is a tiny recent tail).
3. `cogs.lines_missing_cost / cogs.line_count < 1%`.
4. Per-account ad spend matches the platform UI within ~1%.
5. `net_revenue / gross_revenue ≈ 0.8264` (= 1/1.21).
6. `MER` and `contribution_margin %` in the same range as the prior month.
