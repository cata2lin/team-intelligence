---
name: weekly-insights
description: Weekly Performance Insights for a brand — week-over-week combining Google Ads (live) with REAL Shopify orders (metrics `orders`). Reports paid spend/ROAS/CPA, real revenue/orders/AOV, the blended ad-cost ratio (spend ÷ real revenue ≈ MER), the Ads-vs-real reconciliation (paid attribution vs total sales), per-campaign movers, and a "what to do" narrative. Read-only; built to run weekly. Use to see the true picture instead of trusting Ads-reported ROAS alone.
---

# Weekly Performance Insights

The single most misleading number in advertising is **Ads-reported ROAS** in isolation. This report
puts paid performance next to **real Shopify revenue** so you see what actually happened.

## What it shows (week-over-week)
- **Google Ads (reported):** spend, conversions, value, ROAS, CPA + WoW Δ.
- **Shopify (real, all channels):** orders, revenue, AOV from the metrics `orders` table + WoW Δ.
- **Blended:** ad-cost ratio = Ads spend ÷ real revenue (≈ inverse MER); and the **reconciliation** —
  Ads-attributed value vs total revenue (Ads typically over-credits, esp. brand + PMax).
- **Per-campaign movers** (ROAS up/down) and an auto **"what to do"** (scale risers, investigate
  fallers, prune if ad-cost ratio is high).

## Why this matters (real example — Esteban)
- Ads "conversions" **−53% WoW** but real **orders only −9%** → the drop was the conversion-goals
  cleanup (measurement), **not** a sales drop. Without the real-orders line you'd panic.
- Ad spend is **0.7% of revenue** (MER ~145×) → Google Ads is a *tiny* slice of an organic/Meta-driven
  brand; judging it on its own inflated ROAS misleads. Ads claims **25%** of sales while spending 0.7%
  = classic last-click brand over-attribution. Discount accordingly.

## Run it (weekly)
```bash
uv run scripts/weekly_insights.py --customer 5229815058 --brand esteban   # Ads + real orders
uv run scripts/weekly_insights.py --customer 7566352958                   # Ads-only (no orders synced)
```
`--brand` (metrics slug) adds the real-orders reconciliation. Window auto-adapts: full **7v7** when
14+ days of data exist, else half-vs-half (Esteban's ENABLED campaigns were restructured ~10 Jun, so
it currently compares 3v3 — the header always prints the exact ranges).

## Data sources & coverage
- **Ads**: live `googleAds:searchStream` (any MCC account).
- **Orders**: metrics `orders` table by `brandId` — synced for **Esteban**; **not Belasil** (Ads-only).
- GA4 channel breakdown is available via **`gigi:analytics`** (`ga4`) and Shopify session/traffic via
  `shopify_analytics_traffic_daily` — fold in for a channel-level view (v2).

## How to read it
- **Trust real revenue over Ads ROAS.** If Ads ROAS dives but real revenue holds, it's measurement.
- **ad-cost ratio** is your blended truth: a healthy DTC sits a few % to ~15%; 0.7% means paid is
  barely contributing (or barely used). Rising ad-cost ratio with flat revenue = waste → `search-terms`.
- Pair with **`gigi:ads-anomalies`** (what broke) and **`change_history.py`** (who changed what) when a
  metric moves.

## Unghiuri noi (adoptate MIT)
- **gigi:marketing-plan** — layer strategic (plan 12 luni, audit). **gigi:ads-math** — modelare rapidă.
