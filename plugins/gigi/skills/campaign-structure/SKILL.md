---
name: campaign-structure
description: Campaign Structure Reviewer for Google Ads — pull every ENABLED campaign (type, budget, bidding, spend, ROAS, #ad-groups/#asset-groups) and flag structure problems: campaigns below profit breakeven, spend with zero conversions, budget that isn't on the most efficient campaign, missing dedicated brand campaign, thin/zero-spend groups, dead campaigns, and bidding-strategy mismatches. Read-only; prints prioritised recommendations. Use to decide what to separate, consolidate, reallocate, or pause.
---

# Campaign Structure Reviewer

Most accounts don't have a performance problem, they have a **structure** problem — budget on the
wrong campaign, brand and non-brand mixed, thin ad groups that never learn. This reviews the shape.

## Structure principles it checks against
- **Brand ≠ non-brand.** Brand is cheap demand-capture (high ROAS, capped volume); non-brand is
  prospecting. They need **separate campaigns** with separate budgets and targets, or brand's cheap
  conversions flatter the average and hide non-brand waste. (Flags if there's no dedicated brand
  campaign — competitors can bid your brand cheaply.)
- **Budget follows efficiency** — among non-brand campaigns, the biggest budget should be on the best
  ROAS, not the oldest campaign. (Brand is excluded from this check — its ROAS always "wins" but
  can't absorb more budget.)
- **Profit breakeven, not revenue.** A campaign under **ROAS = 1/(margin × delivery)** loses money —
  fix (copy/feed/targeting) or pause. Pass `--margin`/`--delivery-rate`.
- **Don't fragment.** Many thin ad groups (low spend each) never gather enough data — consolidate.
- **Kill the dead.** Enabled, budgeted, 0 spend in 30 days = not serving (disapproved? bid too low?).
- **Bidding fit.** Brand on Maximize-conversions can overpay for clicks you'd win anyway; consider
  Manual/Max-clicks with a high target impression share.

## Run it
```bash
uv run scripts/campaign_review.py --customer 7566352958 --brand-terms belasil --margin 0.45 --delivery-rate 0.85
uv run scripts/campaign_review.py --customer 5229815058 --brand-terms "esteban,maison" --margin 0.70
```
`--brand-terms` + the word "brand" detect brand campaigns. Output: a structure table (type, budget,
spend, ROAS, #groups, bidding) + 🔴/🟠/🟡 recommendations.

## Real examples
- **Belasil:** 🔴 "Non-Brand - Detergent" ROAS 0.9 < breakeven 2.6 → loses money; we diversified its
  RSAs (`gigi:ad-copy`) — if it doesn't recover, pause it. Brand Protect (ROAS 9, headroom per
  `budget-simulator`) is the place to scale.
- **Esteban:** structure is clean (PMax + dedicated Search-Brand); only a bidding-strategy nudge.

## How to act
- Use with **`budget-simulator`** (does the high-ROAS campaign have headroom to take the budget?),
  **`product-matrix`** (split SCALE products into their own PMax asset group), **`ad-copy`** (fix the
  below-breakeven campaign's copy before pausing), and `gads.py set-budget`/`set-status` to execute.
- Re-review monthly or after any restructure.
