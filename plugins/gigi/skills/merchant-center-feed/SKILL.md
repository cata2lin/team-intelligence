---
name: merchant-center-feed
description: Google Merchant Center feed health — which products are DISAPPROVED / not eligible (and why, per reason code) AND which are ELIGIBLE_LIMITED (eligible but reach-reduced — e.g. pending initial policy review on a new account, or missing GTIN) with their reasons. Disapproved/limited products = lost Shopping/PMax impressions and sales, critical for stores that lean on PMax (Grandia) and for new launches (Carpetto/Gento). Read-only, via the new Merchant API; --store accepts a raw merchant ID. Use for "feed health", "disapproved products", "why isn't this product showing in Shopping", "produsele sunt approved?", "pending review", "Merchant Center issues", "produse dezaprobate", "feed Google Shopping", "PMax feed".
argument-hint: "--store <grandia|esteban|belasil> | --all"
---

# merchant-center-feed — Google Shopping/PMax feed health
> Author: Gigi.

Surfaces the products Google has **disapproved or made ineligible** for Shopping/PMax, grouped by reason — the silent leak that throttles PMax (we run Google Ads in-house; Grandia leans on PMax).

```bash
uv run merchant_feed.py --store grandia      # status counts + disapproved products + reasons
uv run merchant_feed.py --all                # all connected stores
```

## Connected stores & auth
Merchant Center accounts: **Grandia `5677157050`, Esteban `5676783307`, Belasil `5582663665`** (GCP project registered with each). Uses the **new Merchant API** (`merchantapi.googleapis.com/reports/v1`, `product_view`) with a **human OAuth token** in KB (`MERCHANT_OAUTH_REFRESH_TOKEN` + `YOUTUBE_OAUTH_CLIENT_ID/SECRET`) — the service account can't self-register the project, so a human (gheorghe@) registered it once. Add a store by registering its account + adding it to `ACCOUNTS`.
> **`--store` accepts a raw merchant ID** too (falls back to the arg if not in `ACCOUNTS`) — handy for newly-launched stores (e.g. Carpetto `5810819833`, Gento `5583322058`) before they're added to the map.

## ELIGIBLE vs ELIGIBLE_LIMITED vs disapproved
- **ELIGIBLE** = serving fully. **ELIGIBLE_LIMITED** = serving but reach-reduced — the skill now prints the *reasons* (🟡 line). **Not eligible** = disapproved (won't serve) → the per-product list.
- Common **ELIGIBLE_LIMITED** reasons:
  - `pending_initial_policy_review_shopping_ads` / `_free_listings` → **a NEW Merchant Center / new feed under Google's initial review**. **No action — clears on its own in a few hours to ~3 days**, then goes full ELIGIBLE. (Don't "fix" it; just wait. Seen on the Carpetto/Gento launch — all 15 Carpetto products were pending review, *not* a GTIN problem.)
  - `missing/invalid GTIN` (`item_id_inconsistent`, identifier issues) → for generic goods (carpets, handbags, no manufacturer barcode) set **`identifier_exists = no`** on the feed (declare "no GTIN") rather than inventing barcodes — Google then stops penalising the missing identifier.

## Real finding (Jun 2026)
Grandia: 842 products, **22 disapproved (3%)** — top reasons `guns_parts_policy_violation ×14` (Google mis-flags kitchen **baterii/faucets** as weapon parts!), `landing_page_error ×6`, `item_missing_required_attribute ×4`, `price_out_of_range ×3`. The 14 faucets are money lost on a wrong policy flag → **appeal in Merchant Center + adjust titles** (avoid "baterie" ambiguity → "robinet/baterie de bucătărie"). `landing_page_error` = broken/redirecting product URLs (fix in Shopify). Missing-attribute = add GTIN/brand/etc.

## How to use
Run weekly → push the disapproved list to **ClickUp** for the catalog team. Fixes: policy false-positives → appeal + retitle; landing_page_error → fix the product URL (pairs with `gigi:shopify-seo`); missing attributes → add via Shopify; price_out_of_range → check feed price vs landing price. Re-run after fixes to confirm re-approval.

## Caveats
- `aggregatedReportingContextStatus` aggregates across destinations; a product `ELIGIBLE` for Shopping may still have demotions. The reason codes are the actionable signal.
- Old Content-API host (`shopping.content.googleapis.com`) is blocked in our sandbox — this uses the new `merchantapi.googleapis.com` only.
