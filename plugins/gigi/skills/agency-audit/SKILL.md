---
name: agency-audit
description: Agency accountability auditor for paid social. The agency runs Meta + TikTok; this checks whether they spend our money WELL — on OUR real economics, not the vanity ROAS they report. Per brand it pulls the real P&L (agency spend = fb_spend + tk_spend vs revenue − COGS − transport − all ads = real contribution margin) with week-over-week deltas and flags (loss-making brands, thin margin, "spend up but profit down"). Use for "is the agency worth it", "audit agency spend", "are Meta/TikTok profitable per brand", "auditor agenție", "isi merita banii agentia", "raport agentie paid social".
argument-hint: "[--days 7] | [--from YYYY-MM-DD --to YYYY-MM-DD]"
---

# agency-audit — hold the paid-social agency accountable
> Author: Gigi.

We run Google Ads + Klaviyo in-house; the **agency runs Meta + TikTok**. The risk with outsourced paid social is they optimise to the **platform ROAS they report** (attribution-inflated, ignores COGS, transport, and COD refusals). This audits their spend against our **real contribution margin** so you can challenge them with data.

```bash
uv run agency_audit.py              # last 7 days vs prior 7
uv run agency_audit.py --days 30
uv run agency_audit.py --from 2026-06-01 --to 2026-06-14
```

## What it shows (per brand, sorted by agency spend)
- **Agency RON** = `fb_spend + tk_spend`, and its **% of total ad spend**.
- **Revenue**, and **CM real** = `revenue − COGS − transport − ALL ad spend` (the all-in contribution), + **CM%** and **MER**.
- **Week-over-week flags**: 🔴 loss-making (CM real negative — agency burning budget), 🟡 thin margin (<8%), 🟡 agency-dependent + weak MER, 🟡 "spend↑ but profit↓ WoW", 🟡 margin dropped >5pp WoW.

## Data source
Reads the real per-brand P&L from the VPS (`/root/Scripturi/data/daily_perf.db`, fed from the 'Raport Zilnic 2' sheet) over SSH — the **same source as `gigi:multi-brand-pnl`** (FB/TikTok/Google spend + revenue + COGS + transport + profit). No keys; uses the existing VPS SSH access. Brands with zero Meta+TikTok spend are hidden (agency-run brands only).

## How to use it
Run weekly. Take the flagged brands to the agency: "you spent X on Brand Y, real contribution was Z% / negative — justify or restructure." For a single brand's campaign/creative detail, pair with `gigi:meta-ads` / `gigi:tiktok-ads` (live API). Note: CM real here is the all-in figure (not platform-attributed), which is exactly why it catches what the agency's ROAS hides — COD refusals + COGS + transport.

## Caveats
- Revenue is all-channel (can't isolate Meta/TikTok-attributed revenue from daily_perf), so MER is **blended**, not per-channel — but for accountability "is this brand profitable given the agency's spend" that's the right question.
- A v2 could overlay the agency's platform-reported ROAS (from `meta_ad_insights_daily`) to quantify the attribution gap.
