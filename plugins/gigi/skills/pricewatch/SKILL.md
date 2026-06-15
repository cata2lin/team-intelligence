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
```

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
