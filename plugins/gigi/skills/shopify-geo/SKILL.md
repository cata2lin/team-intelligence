---
name: shopify-geo
description: GEO/AEO readiness — score how likely a page is to be CITED by AI search engines (ChatGPT, Perplexity, Google AI Overviews) and to win featured snippets, and audit whether AI crawlers can even read the site. Pure offline heuristics (no API keys), Romanian-aware. Use for "GEO audit", "AEO", "will AI cite this page", "optimize for AI Overviews / ChatGPT / Perplexity", "answer engine optimization", "are AI crawlers blocked", "featured snippet readiness" on any of our stores' pages or before publishing blog articles.
argument-hint: "score --url <page>  |  robots --url <domain>"
---

# Shopify GEO/AEO — get cited by AI search
> Author: Gigi.

Classic SEO (rank in blue links) is covered by `gigi:analytics` (GSC) + `gigi:shopify-seo`. This skill covers the NEW surface: **being cited by AI answer engines** (ChatGPT, Perplexity, Google AI Overviews) and winning featured snippets — a gap none of our other tools touched. All offline, no keys.

```bash
uv run geo.py score  --url https://esteban.ro/collections/dama      # GEO/AEO readiness /100 + prioritized fixes
uv run geo.py robots --url https://esteban.ro                       # can the 14 AI crawlers read the site?
```

## What `score` measures (weighted → /100)
| Signal | Why it matters for AI citation |
|---|---|
| **citable_passages** | self-contained answer blocks ~130–170 words — the unit AI engines lift |
| **question_headings** | H2/H3 phrased as real questions ("Ce parfum…", "Cum alegi…") match how people prompt |
| **front_loaded** | the answer/definition in the first 1–2 sentences (front of the page gets cited most) |
| **evidence_density** | numbers/stats with sources raise citability |
| **freshness** | `dateModified` + a visible "Actualizat" date (fresh content is cited more) |
| **entity_sameAs** | `sameAs` (IG/FB/TikTok/Wikidata) in Organization schema = entity recognition |
| **structured_data** | FAQPage / Product / Article JSON-LD |
| **ai_crawler_access** | are GPTBot/ClaudeBot/PerplexityBot… allowed in robots.txt (else you can't be cited at all) |

## How to use it
- Run `score` on product/collection/blog pages and on the output of `core:esteban/gt/nubra-articles` **as a pre-publish gate**.
- Run `robots` per store first — if AI crawlers are blocked, nothing else matters. (Note: Shopify controls parts of robots.txt; check what's editable on the plan before promising fixes.)

## Honesty / caveats
- **Romanian-aware but tune it:** question-word and definition patterns include RO ("este/înseamnă/reprezintă", "ce/cum/de ce/care"). Refine for perfume/product copy as needed.
- **The GEO correlation stats circulating online (3×, 0.737, 44% "first 30% of page") are vendor marketing — unsourced.** Use this score as a *relative* tuning heuristic, not a guarantee, and don't quote those figures to clients.
- **`llms.txt` is NOT a citation lever** (documented with evidence in the source skills). Generating one is harmless but don't sell it as an AEO win.
- Complements: `gigi:shopify-seo` (fix on-page/schema), `gigi:analytics` (gsc rank/opportunities for classic SEO).
