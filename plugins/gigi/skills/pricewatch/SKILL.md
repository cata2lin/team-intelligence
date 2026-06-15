---
name: pricewatch
description: Competitor price monitor for commodity products (primarily GRANDIA home/garden goods, which compete head-to-head on price with eMAG and other RO retailers — unlike the perfume dupes). Keep a watchlist of competitor product URLs, extract current price + availability (JSON-LD first, then meta/selector fallbacks), store append-only price history, and flag when a competitor drops price or undercuts our price. Use for "monitor competitor prices", "are we being undercut", "price tracking", "monitorizare preț concurență", "preț competitor". Local SQLite, no keys.
argument-hint: "add --url <competitor url> --our <RON> | check | list | history"
---

# pricewatch — competitor price monitor (Grandia / commodity goods)
> Author: Gigi.

For **Grandia** (and other home-goods stores) the products are generic and shoppers compare price directly across eMAG/marketplaces — so knowing when a competitor undercuts us is real money. (For perfume stores it's less direct — our products are "inspired-by", not the same SKU.)

```bash
uv run pricewatch.py add --url "https://www.emag.ro/.../pd/..." --label "Pernă cervicală @ eMAG" --our 89
uv run pricewatch.py check                 # current price + Δ vs last + 🔻dropped / 🔴undercuts-us flags
uv run pricewatch.py list
uv run pricewatch.py history --url "..."
uv run pricewatch.py listing --url "<competitor product>"              # title/desc/IMAGES/bullets of a listing
uv run pricewatch.py compare --our-url "<grandia product>" --comp "<competitor>"   # our listing vs theirs → fix gaps
```

## Listing intelligence — fix the products that DON'T sell (ours)
Beyond price, `listing`/`compare` extract a product's **title, description length, image URLs, and bullets/specs** — to diagnose *why a Grandia product underperforms* and copy what better competitor listings do.
- Workflow: pull **Grandia's non-sellers** (low units / views-but-no-conversions — from our `arona-bi`/`grandia` DB or `metrics` order_line_items), then `compare --our-url <ours> --comp <a competitor listing>` for each. It flags concrete gaps: **too few images**, **thin description**, **no specs table**.
- Real example: `grandia.ro/products/raft-depozitare...` has only **2 images + 91-word description** — a thin PDP that hurts conversion. Hand the gaps to `gigi:shopify-seo` (write meta/description), the content/`*-articles` skills (copy), and the photo brief (more angles/lifestyle/scale shots).
> ⚠️ The **bullets/specs** extraction is heuristic (can catch breadcrumb/popup text) — trust **image count + description length** as the hard signals; eyeball bullets.

## Fix → approve part or all → apply (the action loop)
`compare` only diagnoses. To actually improve a product, the loop is **itemized + selectively approvable** (same posture as `cs-actions`):
1. `compare` surfaces the gaps for a Grandia non-seller.
2. I draft the **concrete fixes as a numbered list** — e.g. `1) meta title`, `2) expanded RO description (~250w: dimensiuni/material/utilizare/beneficii)`, `3) specs table`, `4) image brief (which shots to add)` — each shown **before → after**.
3. **You approve a subset or all** ("aplică 1 și 3" / "aplică tot").
4. Approved text fixes are written to Shopify via **`gigi:shopify-seo`** (Admin API) — **dry-run by default**, applied only on confirm; image shots are a brief for the photo team (can't auto-shoot). Nothing writes to the live store without your explicit go.
> So `pricewatch` = the diagnosis + the gap list; the copy is drafted in-chat; `shopify-seo` is the safe writer (per-fix, dry-run → apply). A future `pricewatch fix --apply 1,3` can wire this end-to-end.

## How it works
- **Watchlist**: competitor product URLs + optional `--our` (Grandia's price for the matching product).
- **Extraction**: JSON-LD `offers.price`/`availability` first (works on most Shopify/WooCommerce/standard sites), then `product:price:amount` / `og:price` / `itemprop=price` fallbacks. Price normalization handles RO formats (1.234,56).
- **History**: append-only in `~/.cache/arona-pricewatch/prices.db`. `check` flags 🔻 (price dropped vs last) and 🔴 (below our price = we're undercut).

## How to use for Grandia
1. Take Grandia's price-sensitive / best-selling SKUs (their prices live in our `arona-bi` / `grandia` DB — `products` + `price_history`).
2. Find the competitor listing(s) for each (eMAG etc.), `add` them with `--our <Grandia price>`.
3. Run `check` on a **cron** (daily/weekly) → wire significant changes (🔴 undercut / 🔻 drop) to a **ClickUp task** (we use ClickUp for alerts) so the team reprices.

## Caveats / v2
- Hardened sites (eMAG/Notino have anti-bot) may block a plain fetch → `add`/`check` prints "neextras"; fall back to **Firecrawl MCP** or the `library:scraper-construction` escalation ladder (proxies/Selenium).
- v2: auto-seed `--our` from the Grandia DB (`price_history`) and a competitor-URL matcher; push undercut alerts straight into ClickUp.
