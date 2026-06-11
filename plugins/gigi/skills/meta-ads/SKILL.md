---
name: meta-ads
description: Read AND operate Meta (Facebook/Instagram) Ads for any team brand. Read — accounts, performance reports at account/campaign/adset/ad level (spend, purchases, revenue, ROAS, CPA, CTR, CPM), demographic & placement breakdowns (age/gender/platform/country), daily trend, creative/ad ranking (with optional match to local video files), and `list` (ids/status/budget). Write — gated mutations: pause/activate a campaign/adset/ad and set daily/lifetime budget (DRY-RUN by default via Meta validate_only; `--apply` to execute). Token + ad accounts come from the `metrics` DB (no per-account login). Use to answer "which Meta ads/creatives/audiences perform best for brand X", to pull a brand's Meta spend/ROAS, to pick winning creatives to reuse on Google, or to pause/scale Meta campaigns. Also `products` — spend per product (Nomenclator/HA mapping) split TEST vs SALES, for multi-product "deals" accounts like Reflexino/Magdeal. All amounts in RON (per-day FX from AWBprint.exchange_rates). Companion to `gigi:google-ads-mcc` and `gigi:tiktok-ads`.
---

# Meta Ads performance (read-only)

The team's **Meta ad accounts + access tokens live in the `metrics` DB** (tables
`meta_ad_accounts`, `meta_access_tokens`, `brand_meta_ad_accounts`). One token reads its accounts'
**insights** via the Meta Marketing API (Graph **v23.0**). No per-account login. **Read-only** — this
skill never mutates campaigns.

> **Brand → accounts uses the canonical mapping** (the team's "CPA și financiar" sheet → `Mapping`
> tab), cached in `brand_map.json` by `brandmap.py`. This is the source of truth — name-ILIKE is only a
> fallback. It matters: e.g. **Magdeal → FB account "Reflexino"**, **Ofertele Zilei → "Genti promo,
> Esteban 3"** — a plain ILIKE would attribute the wrong accounts (or none). A brand can have several
> accounts; the tool queries all and aggregates. **All output is in RON** — USD/EUR/… accounts are
> converted **per day** from the dynamic BNR rates in `AWBprint.exchange_rates` (via `DATABASE_URL_AWBPRINT`,
> forward-filled; fixed `CURRENCY_RATES_RON` KB config as fallback). Validated vs "Raport Zilnic 2":
> Belasil/GT/Esteban/Nubra match the Facebook column within ~1%.
>
> **Config/creds come from the KB** (no hardcoded paths): `GA4_SA_JSON` (the Sheets/GA4 service account,
> used in-memory — never written to disk), `MAPPING_SHEET_ID`, `NOMENCLATOR_SHEET_ID`, `DATABASE_URL_METRICS`,
> `DATABASE_URL_AWBPRINT`. Refresh caches after a sheet changes: `uv run brandmap.py sync` (accounts) /
> `uv run prodmap.py sync` (product rules).
>
> **TikTok shared accounts:** the mapping also captures TikTok, where one account (e.g. "ROSSI Nails
> Romania") runs **several brands** split by a campaign-name token (col `Campanie`, e.g. `APRECIAT`,
> `GT`, `COVORIA`). `brand_map.json` stores `tiktok`, `tiktok_shared`, `campaign_token` per brand — ready
> for a future TikTok skill (filter that account's campaigns by the token to attribute spend correctly).

## Setup
```bash
KB=~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py
export DATABASE_URL_METRICS="$(uv run "$KB" secret-get DATABASE_URL_METRICS)"
```
All commands run with `uv` (deps inline). Brand is a free-text match: `belasil`, `esteban`, `grandia`, `nubra`, `gento`.

## Commands (`meta.py`)
```bash
# which Meta ad accounts a brand has
uv run meta.py accounts belasil

# performance report — level: account | campaign | adset | ad ; sort: spend | roas | purchases | cpa
uv run meta.py report belasil --level campaign --range last_30d --sort roas
uv run meta.py report esteban --level ad       --range last_7d  --sort purchases --limit 20

# demographic / placement breakdown — --by accepts Meta breakdown keys
uv run meta.py breakdown belasil --by age,gender              --range last_30d
uv run meta.py breakdown belasil --by publisher_platform,platform_position --range last_30d
uv run meta.py breakdown belasil --by country                 --range last_90d

# daily trend (account-level, time_increment=1)
uv run meta.py trend belasil --range last_14d

# rank creatives/ads by ROAS or purchases; optionally match ad names to local video files
uv run meta.py creatives belasil --range last_90d --sort roas --min-spend 150
uv run meta.py creatives belasil --range last_90d --match-folder "/path/Creative Belasil"

# list entities WITH ids (to find what to mutate) — id · status · budget/day · name
uv run meta.py list belasil --level campaign
uv run meta.py list belasil --level adset

# spend per PRODUCT, split VÂNZARE vs TEST (for multi-product "deals" accounts, e.g. Reflexino/Magdeal)
uv run meta.py products magdeal --range last_30d
```

## Products & TEST vs SALES (`products`)
For accounts that sell **many products, one campaign each** (e.g. **Reflexino = Magdeal's FB account**),
`products` attributes each campaign's spend to a **product** and separates **TEST** from **VÂNZARE (sales)**:
- **Product** = a `HA-<digits>` code in the campaign name, else the team's **Nomenclator** rules
  (`ACCOUNT` / `CAMPAIGN_KEYWORD` / `AD_KEYWORD`, accent-insensitive substring) — same logic as the ARONA
  product-profitability `apply_mapping`. Rules sync from the Nomenclator sheet: `uv run prodmap.py sync`.
- **TEST vs SALES**: a campaign whose name contains **"TEST"** is TEST; everything else is sales. The brand
  total in `report` includes both; `products` shows the **sales** P&L per product and the **TEST** spend
  apart. (This is why a raw `report` on Magdeal reads ~19% higher than the team's sales figure — the gap is TEST.)
- Validated vs "Raport Zilnic 2": Magdeal `products` sales total matched the sheet's Magdeal FB to ~0.4%.

`prodmap.py` + `prod_rules.json` are shared with `gigi:tiktok-ads`.

## Mutations (writes) — DRY-RUN by default, add `--apply` to execute
Treat like a live ad-account write: `list` to find the id, dry-run (Meta `validate_only`), confirm with the user, then `--apply`.
```bash
uv run meta.py pause    belasil <id>                 # set status PAUSED (campaign/adset/ad)
uv run meta.py activate belasil <id>                 # set status ACTIVE
uv run meta.py budget   belasil <id> --daily 80      # daily_budget in account currency
uv run meta.py budget   belasil <id> --lifetime 2000 # lifetime_budget
#   ...add --apply to any of the above to actually execute
```
- The tool **auto-finds the owning account + token** for the id (no need to pass the account).
- Budget goes on the **campaign** for CBO, on the **adset** for ABO — pass that id.
- Verified: the stored tokens carry **ads_management** (writes succeed); dry-run uses Meta's server-side `validate_only` so nothing changes until `--apply`.
- **Currency is the account's** (Belasil/Esteban Meta accounts are USD) — `--daily 80` = $80, not 80 RON.

**Ranges:** Meta `date_preset` keywords (`today`, `yesterday`, `last_7d`, `last_14d`, `last_30d`,
`last_90d`, `this_month`, `last_month`, `maximum`, …) **or** a custom `--range "2025-04-01,2026-06-10"`.

**Metrics:** purchases/revenue are read from `actions`/`action_values` using the priority
`omni_purchase → offsite_conversion.fb_pixel_purchase → purchase` (avoids double-counting); ROAS from
`purchase_roas` (fallback revenue/spend). CTR/CPM are recomputed from clicks/impr/spend on aggregation.

## Companion scripts
- **`meta_resolve.py`** — deep creative resolution: for the top ads, fetch `ad → creative → video_id →
  video title` and match to local files. Heavier (per-video calls); the `creative{video_id,object_story_spec}`
  field can 500 ("reduce the amount of data") — request `creative{video_id}` only with a small page size.
- **`meta_top_ads.py`** — original Belasil ad-ranking + filename-match script (superseded by
  `meta.py creatives --match-folder`, kept for reference).

## Typical uses
- **Pick winning creatives to reuse on Google** (PMax video): `meta.py creatives <brand>` → take the
  top-ROAS ads with real volume → upload + attach via `gigi:google-ads-mcc`.
- **Audience intelligence**: `breakdown <brand> --by age,gender` reveals the converting core (e.g. Belasil
  = women 45-64) → informs Google audience signals & creative.
- **Spend/ROAS pull** for a P&L or a quick "how is brand X doing on Meta" without opening Ads Manager.

## Guardrails
- **Writes are DRY-RUN by default** (Meta `validate_only`). Require `--apply` AND user confirmation before changing a live campaign — these spend real money (some brands do 800+ orders/day on Meta).
- **Never print** the access token. It's read from the DB and used in-process only.
- Watch the **currency** per account; don't sum USD + RON brands together, and remember `--daily` is in the account currency.
- The account→brand match is by **name ILIKE** for now; for accounts that run **multiple brands** the canonical mapping (campaign-name → brand, e.g. "APRECIAT") lives in the team's **CPA/financial mapping** (a Google Sheet / the `Profitabilitate-Livrabilitate` DB) — wire that in before trusting brand totals on shared accounts.
- The Meta token is a stored system/user token (works headless); the DB is the only interactively-authenticated piece.
