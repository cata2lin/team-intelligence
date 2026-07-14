---
name: metrics-app
description: Operate the team Marketing-metrics APP (metrics.arona.ro — repo contact546/metrics, the BI warehouse behind ad-spend/orders/brand data) from the CLI, doing ANY operation the web UI can. Covers the full HTTP API (47 routes, zero server actions): trigger Meta/TikTok/Google-Ads insights syncs NOW, run a Shopify brand sync/backfill, discover Google Ads MCC child accounts, refresh a TikTok token, and — most importantly — manage the brand↔ad-account MAPPING that all attribution depends on (link/unlink Meta/TikTok/Google accounts to a brand with a campaignFilter), pause/unpause a brand, create/edit brands, stores, connections and tokens. Auth is a session cookie (creds from the team KB); reads run free, mutations are dry-run unless --yes; reads can also go straight to the postgres-metrics warehouse. Use for "run the Meta sync", "backfill brand X", "map this ad account to brand Y", "why is spend 0 for brand Z", "pause a brand", "refresh the TikTok token", "add a Meta token", or any write to the metrics app. Companion to metrics-cache (which builds cache.* on the same DB) and to meta-ads/tiktok-ads/google-ads-mcc (which read the ad platforms live).
argument-hint: "routes | brands | sync-google --account <cuid> --days 7 --yes | map-google add --brand <id> --account <cuid> --yes | call POST /api/… --json '{…}' --yes"
---

# metrics-app — operate metrics.arona.ro from the CLI
> Author: **Gigi**. Drives the team's Marketing-metrics app over its HTTP API. Same code paths as the UI.

## What this is
`contact546/metrics` = the Next.js app that syncs **Shopify + Meta + TikTok + Google Ads**
across ~30 brands into the `metrics` warehouse (the `postgres-metrics` DB the whole team reads).
It has a **complete HTTP API and ZERO server actions**, so the CLI can do *everything* the UI can.

> Not to be confused with **`gigi:metrics-cache`** — that builds the derived `cache.*` tables on
> the *same DB* via VPS scripts. This skill operates the *app* (syncs, mappings, tokens).

## Setup (once)
Creds live in the team KB (already set): `METRICS_ADMIN_EMAIL`, `METRICS_ADMIN_PASSWORD`.
The CLI fetches them via `kb.py secret-get`, logs in, and caches the `metrics_session` cookie in
`~/.config/arona-metrics/cookie` (0600). No manual login. Base URL defaults to
`https://metrics.arona.ro` (override with `METRICS_BASE`).

## Run
```bash
cd scripts
uv run --no-project --with requests metrics.py <cmd>
# sql reads also need: --with psycopg2-binary
```

## Commands
```bash
metrics.py routes                                  # the full route map (read this first)
metrics.py brands                                  # list brands (read)
metrics.py get /api/meta/tokens                    # any GET, pretty-printed
metrics.py call POST /api/brands/<id>/sync --json '{"entities":["orders"]}' --yes

# ad-platform syncs (run NOW). Omit --account to sync ALL linked accounts.
metrics.py sync-meta   --account <cuid> --days 7 --yes
metrics.py sync-tiktok --account <cuid> --days 7 --yes
metrics.py sync-google --account <cuid> --days 7 --yes
metrics.py discover-google --yes                   # MCC → upsert child accounts

# brand ↔ ad-account MAPPING (the attribution-critical writes)
metrics.py map-meta   add --brand <id> --account <cuid> [--filter "GT|APRECIAT"] --yes
metrics.py map-google remove --brand <id> --account <cuid> --yes
metrics.py brand-pause   --brand <id> --yes
metrics.py brand-unpause --brand <id> --yes

metrics.py sql "select slug,is_paused from brands order by slug"   # read-only, postgres-metrics
```

## Safety (enforced)
- **Reads (GET) run free. Every non-GET is DRY-RUN unless you pass `--yes`** — it prints the exact
  method/path/body it would send.
- Cookie + secret values are **never printed**.
- `sql` opens the warehouse **read-only** (`SET SESSION READ ONLY`).

## The two things that actually matter here
1. **Mapping = money.** An ad account with **no brand link, or an empty `campaignFilter`**, silently
   syncs spend to the wrong brand or to nothing — the "spend reads 0" / "shared account bleeds into
   the wrong brand" bug from [[bi-data-integrity-check]] and [[mapping-tiktok-attribution]]. Fix it
   with `map-meta/tiktok/google add … --filter …`. Shared accounts (one TikTok advertiser → several
   brands) need a `campaignFilter` token per brand.
2. **ID gotcha.** All link/sync routes take the **internal cuid** (`MetaAdAccount.id`,
   `TikTokAdAccount.id`, `GoogleAdsCustomerAccount.id`), **not** the platform id (`act_…`,
   advertiser_id, `123-456-7890`). Resolve with `get /api/brands/<id>/{meta,tiktok,google-ads}-accounts`
   (returns `linked` + `available`) or `get /api/google-ads/accounts-list`.

## Gotcha: long backfills time out
`/api/{meta,tiktok,google-ads}/sync` run the sync **synchronously inside the HTTP request** (no
`maxDuration`), so a `--days 90` all-accounts call gets killed mid-run. Either loop per-account with
a modest `--days`, or run the heavy job locally from the repo's `scripts/*.ts` (they import the sync
functions directly and need only `DATABASE_URL`). Only the **Shopify** brand sync is async (Inngest).

## Full detail
`reference/routes.md` — every route, method, body shape, and auth note.

**Related:** [[metrics-cache]], [[bi-data-integrity-check]], [[mapping-tiktok-attribution]], [[meta-tiktok-ads-skills]], [[scentum-erp-cli]]
