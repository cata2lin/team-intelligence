---
name: budget-simulator
description: Budget Simulator / Forecaster for Google Ads — model "what if I change a campaign's budget by ±X%?" using the last N days plus the budget-lost impression share (headroom), projecting conversions / revenue / ROAS under diminishing returns, and a PROFIT view (margin × COD delivery rate) with the breakeven ROAS. Tells you which campaigns have room to scale profitably and which are at the demand ceiling or below breakeven. Read-only, transparent assumptions.
---

# Budget Simulator / Forecaster

Before you move a budget, size the move. This projects each campaign under ±X% budget so you scale
the ones with headroom and stop pouring money into the ones at the ceiling — and it judges on
**profit**, not revenue ROAS.

## The model (transparent on purpose)
- **Headroom = budget-lost impression share.** If a Search campaign loses, say, 22% of impressions to
  *budget*, there's demand you're not buying → extra budget captures it at near-current efficiency.
  ~0% lost = you're at the **demand ceiling**; more budget just bids up the same auctions (diminishing).
- **Diminishing returns:** `conv ∝ budget^elasticity`. Elasticity 1 = linear (fully constrained), lower
  = faster diminishing. Auto-derived from budget-lost IS (high IS-lost → elasticity ~0.85; near-0 →
  ~0.55); override with `--elasticity`.
- **Profit, not revenue.** `profit = revenue × margin × delivery_rate − spend`. **Breakeven ROAS =
  1 / (margin × delivery_rate)** — for a 45%-margin product at 85% COD delivery, you need ROAS **2.6**
  just to not lose money. Revenue ROAS above 2.6 but a campaign still "scaling into" lower ROAS can
  cross below breakeven — the profit column shows it.

## Run it
```bash
uv run budget_sim.py --customer 7566352958 --margin 0.45 --delivery-rate 0.85 --scenarios=-20,20,50,100
uv run budget_sim.py --customer 5229815058 --campaign "Performance Max" --margin 0.70
```
`--margin` enables the profit view (use the **real** margin — Esteban 2+1 ≈ 0.70, see `product-matrix`).
`--delivery-rate` = COD orders actually delivered/paid. **Lead negatives with `=`** (`--scenarios=-20,...`)
so argparse doesn't read them as flags. Output per campaign: current vs each scenario (budget/day,
conv, revenue, ROAS, profit) + breakeven ROAS + a scale/ceiling verdict.

## How to read it (real example — Belasil)
- **PMax "All Products": budget-lost IS 0%** → at the demand ceiling. +100% budget → ROAS 5.1→3.8 and
  **profit falls**. Don't scale here; the volume isn't there.
- **Brand Protect (Search): 22% IS lost to budget** → +100% budget → ROAS 9.0→7.3 but **profit +47%**.
  This is the scale lever.
- **Non-Brand: ROAS 0.9, profit −159 (below breakeven 2.6)** → loses money; **fix or cut**, never scale.

## Caveats
- It's an **estimate**, not a guarantee — elasticity is a model, demand/competition/seasonality move.
  Use it to *size* a move, then verify with `ads-anomalies` after.
- **Verify utilisation before trusting a "scale" verdict.** Budget-lost IS can be **intraday** (caps on
  busy days) even when the *average* daily spend is below budget — Belasil Brand Protect showed 22%
  IS-lost but spent ~30 of a 45 budget on average, capping only on peak days. Pull daily spend vs
  budget (7d) and raise only if it actually caps; a campaign not spending its budget won't use more.
- PMax doesn't expose budget-lost IS → elasticity defaults conservative; treat PMax projections as
  rougher. Step budgets ~20–30% and leave 1–2 weeks (learning), per `gigi:google-ads-mcc`.
- Pairs with **`product-matrix`** (which products to scale), **`weekly-insights`** (real revenue), and
  `gads.py set-budget` to execute.
