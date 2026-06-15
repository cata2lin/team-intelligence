---
name: merchant-center-feed
description: Google Merchant Center feed health — which products are DISAPPROVED / not eligible for Google Shopping & Performance Max, and why (per reason code). Disapproved products = lost Shopping/PMax impressions and sales, critical for stores that lean on PMax (Grandia). Read-only, via the new Merchant API. Use for "feed health", "disapproved products", "why isn't this product showing in Shopping", "Merchant Center issues", "produse dezaprobate", "feed Google Shopping", "PMax feed".
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

## Real finding (Jun 2026)
Grandia: 842 products, **22 disapproved (3%)** — top reasons `guns_parts_policy_violation ×14` (Google mis-flags kitchen **baterii/faucets** as weapon parts!), `landing_page_error ×6`, `item_missing_required_attribute ×4`, `price_out_of_range ×3`. The 14 faucets are money lost on a wrong policy flag → **appeal in Merchant Center + adjust titles** (avoid "baterie" ambiguity → "robinet/baterie de bucătărie"). `landing_page_error` = broken/redirecting product URLs (fix in Shopify). Missing-attribute = add GTIN/brand/etc.

## How to use
Run weekly → push the disapproved list to **ClickUp** for the catalog team. Fixes: policy false-positives → appeal + retitle; landing_page_error → fix the product URL (pairs with `gigi:shopify-seo`); missing attributes → add via Shopify; price_out_of_range → check feed price vs landing price. Re-run after fixes to confirm re-approval.

## Caveats
- `aggregatedReportingContextStatus` aggregates across destinations; a product `ELIGIBLE` for Shopping may still have demotions. The reason codes are the actionable signal.
- Old Content-API host (`shopping.content.googleapis.com`) is blocked in our sandbox — this uses the new `merchantapi.googleapis.com` only.
