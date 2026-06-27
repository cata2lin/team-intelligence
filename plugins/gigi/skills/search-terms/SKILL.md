---
name: search-terms
description: Search Term Analyzer for Google Ads — mine what people ACTUALLY typed (search_term_view), find WASTE (spend with zero conversions) and turn it into ready-to-paste negative keywords, flag COMPETITOR and GENERIC spend, surface converting NON-BRAND terms as keyword opportunities, and split spend brand vs non-brand. Live from any MCC account; read-only, prints the `gads.py add-negatives` command you run after review. Use weekly to stop wasted spend and capture cheap converting queries.
---

# Search Term Analyzer

Your keywords are guesses; **search terms are the truth** — the actual queries that spent your
money. This skill turns that list into decisions.

## What it finds
- **Waste → negatives.** Terms with spend and **0 conversions** are budget leaks. The script ranks
  them by wasted RON, tags `[COMP]` (competitor), `[GENERIC]` (one-word, broad), `[JUNK]`
  (gratis/pdf/reteta/job…), and emits a `gads.py add-negatives` command.
- **Competitor spend.** How much you pay to show on rival names (dero, ariel, chanteclair…). Usually
  low-intent for you → negative, unless you're deliberately conquesting.
- **Winners → keyword opportunities.** Converting **non-brand** terms not yet exact keywords — add
  them as exact to capture cheaply and pull them out of broad matching.
- **Brand vs non-brand split.** Brand terms are cheap and high-ROAS (you'd get them anyway);
  non-brand is real prospecting. Know the ratio before you judge ROAS.

## Run it
```bash
uv run scripts/search_terms.py --customer 7566352958 --brand-terms belasil \
    --competitor-terms "dero,ariel,persil,chanteclair,bonux,tide" --min-waste 5
uv run scripts/search_terms.py --customer 5229815058 --brand-terms "esteban,maison d'esteban" --days 14
uv run scripts/search_terms.py --customer 7566352958 --brand-terms belasil --campaign "Non-Brand - Detergent"
```
`--brand-terms` keeps brand queries out of the negatives. `--competitor-terms` / `--junk-terms`
tag those buckets. `--min-waste` = RON of 0-conv spend to flag.

## How to act
1. **Review the negative list** (never auto-apply — a 0-conv term might just be new). Then run the
   printed `add-negatives` command with the right campaign ID, `--match PHRASE` for themes,
   `EXACT` for specific junk. Dry-run → `--apply` (per `gigi:google-ads-mcc`).
2. **Add winners as exact keywords** in the matching ad group (`gads.py add-keywords … --match EXACT`).
3. **Re-run weekly.** New terms appear constantly; waste compounds if you don't prune.

## Notes & gotchas
- `search_term_view` covers **Search + Shopping** queries. **PMax** only exposes category-level
  `campaign_search_term_insight` (no raw terms) — far less actionable; mine the Search campaigns.
- One word ("detergent") is almost always too broad to convert — strong negative unless it's your
  core single-product term.
- A 0-conv term with only 1–2 clicks may just lack data; weight by **wasted spend**, not by existence.
- Competitor terms that *do* convert (e.g. "dero belasil") are comparison shoppers — keep those.
- Pairs with **`gigi:google-ads-mcc`** (the `add-negatives`/`add-keywords` execution) and the
  brand/non-brand insight from `campaign_search_term_insight`.

## Unghiuri noi (adoptate MIT)
- **gigi:ads-budget** — alocare buget pe baza search-terms profitabile. **gigi:seo-content-brief** — termenii cu intenție → conținut SEO.
