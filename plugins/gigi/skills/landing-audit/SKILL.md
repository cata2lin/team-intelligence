---
name: landing-audit
description: Landing / product page CRO audit — fetch a page (mobile) and score the conversion essentials (offer, price, CTA, trust signals, reviews + rich-result schema, FAQ/objections, urgency, structured data, mobile/tech) into a checklist + prioritised fixes, optionally with Core Web Vitals. Use on a product page that gets traffic but doesn't convert, on new launches, or to QA the store's offer/trust/mobile before scaling ad spend to it.
---

# Landing / Product Page Audit

Sending more ad budget to a weak page just buys more bounces. This audits the page's
**conversion fundamentals** and tells you what to fix first.

## What it scores (the CRO checklist = the methodology)
- **OFFER** — H1, price visible above the fold, a clear buy CTA, the promo communicated (2+1, % off).
- **TRUST** — free shipping, returns, guarantee, cash-on-delivery, secure checkout (≥2 signals).
- **REVIEWS** — reviews present + `AggregateRating` schema (drives the star rich result in Google).
- **OBJECTIONS** — a FAQ (delivery, returns, authenticity, longevity) + `FAQPage` schema.
- **URGENCY** — honest scarcity (limited stock, offer ends) — use with restraint.
- **SEO/RICH** — `Product` + `Offer` JSON-LD, meta description.
- **MOBILE/TECH** — viewport meta, lazy-loaded images, weight.
- **SPEED** — Core Web Vitals (LCP/CLS/TBT) — the #1 silent killer on mobile.

## Run it
```bash
uv run scripts/landing_audit.py --url https://esteban.ro/products/<handle>
uv run scripts/landing_audit.py --url https://belasil.ro/products/<handle> --speed
```
Output: per-group ✓/✗ checklist, a CRO score, and a prioritised fix list. `--trust` / `--urgency`
tune the RO keyword lists for a different store/theme.

## Speed (Core Web Vitals) — two ways
- **PSI** (`--speed`): uses PageSpeed Insights. Needs the API enabled on the GCP project that owns
  `GADS_GOOGLE_API_KEY` (currently **disabled** → enable "PageSpeed Insights API" once, then it works;
  the script reads `PSI_API_KEY` or `GADS_GOOGLE_API_KEY`). Degrades gracefully with a clear message.
- **In-session (reliable now):** the **chrome-devtools MCP** `lighthouse_audit` runs a real Lighthouse
  on the page — use it for LCP/CLS/perf when PSI is unavailable.

## The visual layer (do this too)
HTML heuristics can't see *layout*. For the real above-the-fold judgement, drive the **chrome-devtools
MCP**: `navigate_page` → `resize_page` to a phone width → `take_screenshot`, and look: is the offer +
price + CTA + trust visible without scrolling? Is the hero image fast and clear? That visual check +
this script together = the full audit.

## How to act
- Fix in impact order: **speed → above-the-fold offer/CTA → trust → reviews/schema → FAQ → urgency**.
- Pairs with **`gigi:cro`** (deeper RO/COD conversion playbook) and **`gigi:shopify-seo`** /
  `gigi:shopify-stores` (to implement schema, meta, theme changes).
- Audit a page **before** you scale ad spend to it (`product-matrix` SCALE products especially).

## Unghiuri noi (adoptate MIT)
- **gigi:ads-landing** — audit post-click cu message-match ad→LP + Core Web Vitals (Playwright). **gigi:seo-sxo** — page-type vs intent (de ce nu rankează pagina).
