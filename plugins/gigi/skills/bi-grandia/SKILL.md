---
name: bi-grandia
description: Operate the BI Grandia app (bi.grandia.ro — repo contact546/grandia-inventory), Grandia.ro's internal ERP/BI platform, from the CLI — ANY operation the web UI can do. 190 HTTP routes, zero server actions, driven over a grandia_session cookie (creds in the team KB). Covers: PURCHASE ORDERS (create, approve/cancel/complete — which write incoming inventory to Shopify — plus send-to-TOM / amend-TOM / refresh-from-TOM, receptions, auto-generate from restock), RETURNS/RMA "tickets" (approve + issue DPD AWB, cancel AWB, mark delivered/close, refund-amount, bank/IBAN, mark-paid, refund-shopify), SHOPIFY SYNC (bootstrap/incremental/snapshot/fulfillments + the 12 scheduler jobs), GA4/Google-Ads/Meta-Ads syncs, PRICING engine (apply a price to Shopify, run pipeline, competitors), AI CATALOG-QUALITY (audit, improve, push title/description/images to Shopify), IMAGE optimization, forecasts, dev-requests, team-tasks, users, settings. Reads run free; every mutation is DRY-RUN unless --yes — and PO/reception dry-runs call the app's own preview-approve/preview-cancel/preview-complete so you see the real Shopify inventory deltas before executing. Reads can also go straight to the postgres DB. Use for "approve PO X", "send PO to TOM", "issue the return AWB", "refund this RMA", "run the incremental sync", "apply this price", "why is this product bleeding money", or any write to the Grandia BI app. NOTE: this is a DIFFERENT app + DB from InventorySync (the stock-pooling app) — do not confuse them.
argument-hint: "routes | get /api/admin/... | po approve <id> [--yes] | rma approve <id> --service <sid> --weight 2 --yes | sync incremental --yes | sql \"…\""
---

# bi-grandia — operate bi.grandia.ro from the CLI
> Author: **Gigi**. Drives Grandia.ro's internal ERP/BI platform over its HTTP API. Same code paths as the UI.

## What this is
`contact546/grandia-inventory` = a 17-module internal platform for **Grandia.ro**: Shopify sync,
inventory, product performance, **RMA/returns** (with DPD AWB + refunds), **purchase orders** (with
**TOM ERP** integration), a **pricing engine**, **AI catalog-quality**, image optimization, and the
GA4/Google/Meta ad syncs — 190 HTTP routes, **zero server actions**, so the CLI does everything the UI can.

> ⚠️ **Not** the InventorySync app. This app = `bi.grandia.ro`, DB secret `DATABASE_URL_GRANDIA`.
> InventorySync (stock-pooling, Trendyol push) = `bi.arona.ro:8002`, DB `DATABASE_URL_INVENTORYSYNC`.
> See [[inventorysync-app]] and [[bi-grandia-app-infra]].

## Setup (once)
Creds are in the team KB (`BIGRANDIA_EMAIL`, `BIGRANDIA_PASSWORD`). The CLI fetches them, logs in, and
caches the `grandia_session` cookie in `~/.config/arona-bi-grandia/cookie` (0600). Base URL defaults
to `https://bi.grandia.ro` (override with `BIGRANDIA_BASE`).

## Run
```bash
cd scripts
uv run --no-project --with requests bi.py <cmd>          # sql reads: also --with psycopg2-binary
```

## Commands
```bash
bi.py routes                                    # the full route map (read first)
bi.py get /api/admin/purchase-orders --query limit=20
bi.py call POST /api/admin/... --json '{...}' --yes      # universal escape hatch (any of 190 routes)

# PURCHASE ORDERS — dry-run calls the app's REAL preview action (shows Shopify inventory deltas)
bi.py po approve  <poId>          # DRY-RUN → preview-approve (shows +N incoming per SKU)
bi.py po approve  <poId> --yes    # execute (writes incoming inventory to Shopify)
bi.py po cancel   <poId>          # DRY-RUN → preview-cancel   ·  --yes to execute
bi.py reception complete <recId>  # DRY-RUN → preview-complete (shows on-hand stock deltas)

# RETURNS / RMA
bi.py rma approve <rmaId> --service <dpdServiceId> --weight 2 --parcels 1 --yes   # approve + issue AWB
bi.py rma deliver <rmaId> --yes · bi.py rma close <rmaId> --yes
bi.py rma mark-paid <rmaId> --amount 149.90 --yes · bi.py rma refund-shopify <rmaId> --yes

# SYNC / JOBS
bi.py sync incremental --yes            # bootstrap | incremental | snapshot | fulfillments
bi.py job ga4-daily-sync --yes          # trigger any of the 12 scheduler jobs

bi.py sql "select status, count(*) from po_purchase_orders group by 1 order by 2 desc"   # read-only
```

## Safety (enforced)
- **Reads run free. Every non-GET is DRY-RUN unless `--yes`.**
- **PO + reception dry-runs are LIVE previews** — they call the app's own `preview-approve` /
  `preview-cancel` / `preview-complete` endpoints and print the exact inventory deltas that would be
  written to Shopify. Nothing is written until you add `--yes`.
- `refund-shopify`, `po approve/cancel`, `reception complete`, `rma awb` have **real external
  side-effects** (Shopify refund, Shopify inventory, DPD AWB) — treat `--yes` accordingly.
- Cookie + secret values are **never printed**. `sql` opens the DB **read-only**.

## Security note (raise with the team)
`middleware.ts` waves **`/api/admin/*` through with no session check**, so ~130 of the 190 routes —
including Shopify-writing and DPD-AWB-issuing ones — have **no authentication** if the app is
internet-exposed. This CLI still logs in (the other ~60 routes — PO, RMA, forecasts, users,
dev/tasks — *are* session-guarded), but the open routes are a real exposure worth fixing.

## Full detail
`reference/routes.md` — every route, method, body, and which ones are session-guarded vs open.

**Related:** [[bi-grandia-app-infra]], [[inventorysync-app]], [[metrics-app]], [[tom]], [[container-pipeline-kdocs]], [[scentum-erp-cli]]
