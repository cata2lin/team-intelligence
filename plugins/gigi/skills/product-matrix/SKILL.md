---
name: product-matrix
description: Product Matrix / PMax Labelizer for Shopping & Performance Max — score every product by MARGIN-AWARE ad performance (POAS = ROAS × effective margin), spend, conversions and stock, then label it Scale / Hold / Trim / Cut / Test / Zombie and say what to do. Live product performance from the Google Ads API (shopping_performance_view, any MCC account) joined to the metrics DB for cost-of-goods (margin) and stock; handles bundle offers like Esteban's 2+1. Use to find which products to scale, which to exclude, and which to keep testing — instead of judging PMax/Shopping at the campaign level.
---

# Product Matrix / PMax Labelizer

PMax and Shopping hide product-level truth behind a campaign-level ROAS. This skill pulls
**per-product** performance and labels each SKU so you act on the products, not the average.

## Why POAS, not ROAS (read this first)
Revenue ROAS lies when margins differ. **POAS = ROAS × effective margin** is profit per ad lei.
- An 80%-margin dupe at ROAS 2 → POAS 1.6 = prints money.
- A 15%-margin item at ROAS 4 → POAS 0.6 = loses money.
- **Bundles change the margin.** Esteban runs **2+1 free** (pay 2, ship 3): a 90-lei order ships
  3 units of COGS, so effective margin = `1 − (3/2)·(cogs/price)` = `1 − 1.5·(9/45)` = **70%**, not
  the 80% list margin. Always pass `--bundle 2+1` for Esteban. POAS ≥ 1 = breakeven on profit.

## The labels (and what to DO)
| Label | Meaning | Action |
|---|---|---|
| **SCALE** | POAS ≥ 1.5 with real data | Raise budget/priority; give the winners their **own asset group / Shopping priority**; feed more. |
| **HOLD** | POAS 1.0–1.5 | Keep; the profitable core. |
| **TRIM** | POAS 0.8–1.0 | Marginal — lower bid/target ROAS, don't kill yet. |
| **CUT** | spent enough, POAS < 0.8 or 0 conv | **Exclude** via listing-group / `custom_label`; it's burning budget. |
| **TEST** | thin data (low spend **and** low conv) | Leave it to learn, or give a small isolated push. |
| **ZOMBIE** | in the feed, 0 impressions | Feed/price/approval problem — it isn't even serving. Fix the feed. |
| `⚠STOCK` | SCALE/HOLD but stock ≤ 5 | Don't over-push something about to sell out; restock or cap. |

> The point of the matrix is **concentration**: a small-budget PMax spread over 177 products
> learns nothing. Move SCALE winners into their own asset group, CUT the wasters, and let the
> budget compound on what prints money.

## Run it
```bash
uv run scripts/product_matrix.py --customer 5229815058 --brand esteban --bundle 2+1
uv run scripts/product_matrix.py --customer 5229815058 --brand esteban --bundle 2+1 --days 14 --top 30
uv run scripts/product_matrix.py --customer 5229815058 --brand esteban --bundle 2+1 --format csv > matrix.csv
```
- `--customer` = Google Ads CID (live `shopping_performance_view`). `--brand` = metrics slug for the
  margin/stock join (`esteban`). Tune `--target-roas`, `--min-spend`, `--min-conv`, `--scale`, `--cut`, `--low-stock`.
- Output: per-product label + ROAS/margin/POAS/conv/stock, sorted by spend, plus a **summary**
  (how much spend sits in CUT = waste, how much SCALE can absorb).

## Data sources & coverage (important)
- **Performance**: live Google Ads `shopping_performance_view` — works for **any** MCC account.
- **Margin + stock**: metrics DB `variants` (`costPerItem`, `inventoryQuantity`) + `products`,
  joined on the variant id inside `productItemId` (`shopify_zz_<product>_<variant>`).
- **Coverage now:** `variants`/`products` are synced for **Esteban (177/200 with COGS), Grandia, GT,
  Nubra, Bonhaus** — not for Belasil (0 synced) or the discount stores. Without `--brand`/COGS the
  matrix falls back to **ROAS-only** (`--target-roas` then matters). The pre-synced
  `google_ads_product_insights_daily` table holds **Grandia only** (agency account); for Esteban we
  pull live.

## Act on the labels (PMax workflow)
1. **Split by label** — write the label to `custom_label_0` (Merchant feed or Shopify metafield),
   then build asset groups / listing-group filters per label: a **Scale** asset group with its own
   budget, a **Cut** exclusion. *(v2: a `--write-labels` mode to push `custom_label_0` via
   `gigi:shopify-stores` metafields / Merchant feed — not yet wired; do it by hand for now.)*
2. **Re-run weekly** — promotions move between buckets as data accrues.
3. For exact per-order P&L (shipping, COD fee, real basket mix) use the profitability engine
   (`profit_orders`); this matrix is a fast, directional, margin-aware triage.

## Gotchas
- Shopping attributes a conversion to the **clicked** product, but a 2+1 basket ships 3 different
  perfumes — product-level value is approximate. Fine for directional labels, not for the books.
- A product with **few conversions but high efficiency** is a winner, not TEST — that's why TEST
  needs **both** low spend and low conv.
- `costPerItem` missing on some variants → those show margin `-` and fall back to ROAS-vs-target.
