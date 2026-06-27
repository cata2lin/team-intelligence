---
name: cross-sell
description: Cross-sell / "frequently bought together" recommender from our own order data — market-basket analysis (support / confidence / lift) per store. Default source AWBprint (--source awb, instant, ~99% complet); warehouse (--source metrics) e fallback dar e incomplet pe unele branduri (GT ~15% comenzi lipsă). Surfaces which products are actually bought together so we can add PDP "frequently bought together" blocks, power Klaviyo post-purchase flows, and pick the 2+1 surprise-perfume pairings. Use for "what's bought together", "cross-sell", "frequently bought together", "produse complementare", "ce se cumpără împreună", "upsell pairings". Read-only.
argument-hint: "--brand <esteban|grandia|gt|nubra|belasil> [--source awb|metrics] [--product <title>] [--days 180]"
---

# cross-sell — frequently-bought-together (our data)
> Author: Gigi.

Pure market-basket weighted by **lift** (how much more often two products sell together than chance). Read-only.

**Sursă (`--source`, default `awb`):** `awb` = AWBprint (DB AWB/Frisbo, `DATABASE_URL_AWBPRINT`) — instant, ~99% complet; coșul vine din `line_items.inventory_item.sku`, titlul produsului e rezolvat din warehouse (`order_line_items`, suficient pt produsele cu volum). `metrics` = warehouse direct (`DATABASE_URL_METRICS`) — poate fi INCOMPLET (GT ~15% comenzi lipsă), păstrat ca fallback + pt `--cached`. Vezi `gigi:product-sales` / `gigi:fulfillment-analytics` pt același pattern.

```bash
uv run cross_sell.py --brand grandia                      # top pairs (AWBprint, last 180d)
uv run cross_sell.py --brand esteban --product "scandal"  # complements for a product (title match)
uv run cross_sell.py --brand gt --days 365 --min-co 10 --top 25
uv run cross_sell.py --brand esteban --source metrics --cached   # warehouse precomputed (instant)
```

## Output & metrics
Per pair: **lift** (×chance — >1.5 = real association), **co** (orders containing both), **confidence** (P(B | bought A)). Default window 180d; `--min-co` (min co-occurrences, default 15), `--min-prod`, `--min-lift`. `--product "<title substring>"` → ranked complements for that product.

**Real signal (Jun 2026):** Esteban — L'Essence No.43+No.69 (lift ~240), No.134+No.88 (297 co-orders); Grandia — storage rafts together, LED-ceiling variants, storage-box sets, co-sleeper + baby bath. These are the buy-together patterns.

## How to use
1. PDP "**frequently bought together**" block (top 3 complements per hero product) → write as metafields via `library:shopify-admin-api`.
2. **Klaviyo post-purchase** flow: recommend the complement of what they just bought.
3. **2+1 surprise-perfume** pairing: pick the surprise from high-lift complements of the cart items.
4. Bundles / "completează setul" offers.

## Caveats / v2
- Mono-product stores/baskets → few pairs; lower `--min-co` or raise `--days`.
- v2: rank by **lift × REAL per-SKU margin** (from the profitability engine) so we push profitable complements, not just frequent ones; auto-write top-3 to product metafields (behind dry-run, like cs-actions).

## Unghiuri noi (adoptate MIT)
- **gigi:offers** — bundle / value-stacking / 2+1 frameworks pt cross-sell.
