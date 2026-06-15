---
name: shopify-seo
description: End-to-end SEO + good-practice optimisation for a Shopify store via the Admin API — audit, then fix on-page meta, duplicate content, image alt, structured data (Product/Offer/AggregateRating/Organization/WebSite/BreadcrumbList/Article/FAQ), navigation, sales-channel publication, technical hygiene (canonical, og/twitter, https, noindex), and a blog content cluster. Use when asked to "improve SEO", "audit SEO", fix meta titles/descriptions, add schema/rich results, fix social-share previews, surface collections in the menu, or otherwise raise organic visibility for a Shopify shop. Works on any store the ARONA Assistant app is installed on. Battle-tested on Esteban / GT / Nubra.
---

# shopify-seo

> Author: **Gigi**. Shared with the whole team via the `gigi` plugin.

A repeatable playbook + tooling to take a Shopify store from "audited" to
"fixed and verified" across the whole SEO surface. Encodes every fix **and every
hard-won gotcha** from the ARONA stores rollout, so the next site goes fast and
avoids the same traps.

## Golden rules (read first — these cost real time to learn)

1. **`seo:{}` REPLACES, it does not merge.** On `productUpdate`/`collectionUpdate`,
   if you send `seo:{title:…}` *without* `description`, Shopify **wipes** the
   description (and vice-versa). **Always send BOTH** title and description, even
   when changing only one. This silently nuked 300+ meta descriptions once.
2. **Themes often append the brand to `<title>`.** If your SEO title already ends
   with `| Brand`, the rendered title doubles: `… | Brand – Brand by X`. **Strip
   the brand** from SEO titles on stores whose theme appends it (check live first;
   it differs per template — product vs page vs article).
3. **Verify before you "fix".** Half the "issues" in the first audit were false:
   WebP *was* served (CDN content-negotiates on the `Accept` header), canonical
   *was* present (attribute order fooled a naive grep), Judge.me stars *were*
   there (injected client-side). Check with a real parser / proper headers / the
   API read-back **before** changing anything.
4. **Edge cache lies.** Storefront pages (esp. product & homepage) serve stale
   HTML even with `?nc=…`. The **API read-back is authoritative**; for rendered
   output, confirm a FAIL in a **real browser** (chrome-devtools) before believing it.
5. **Admin API talks to `*.myshopify.com`, not the custom domain.** Token + Admin
   API only work on the myshopify domain (from the secret). The custom domain is
   for storefront/live checks only. `Store` handles both.
6. **Publish to ALL sales channels, not just Online Store.** New smart collections
   default to Online Store only → invisible on Google & YouTube / Shop / etc.
7. **Never print a secret or token.** Fetch via `kb.py secret-get`, pipe into the process.

## Auth / setup

The **ARONA Assistant** custom app (full scopes) is installed on the team stores.
Tokens are minted per-run via OAuth `client_credentials`. App id/secret/version
live in the SharedClaude `secrets` table; per-store admin domain in
`SHOPIFY_ARONA_<STORE>_DOMAIN`. All of this is wrapped in `scripts/shopify_lib.py`:

```python
import sys, os; sys.path.insert(0, os.path.dirname(__file__))
from shopify_lib import Store, fetch_live
s = Store("esteban")            # store key -> SHOPIFY_ARONA_ESTEBAN_DOMAIN, or pass a *.myshopify.com
s.gql("{ shop { name } }")      # Admin GraphQL (throttle-retry built in)
s.gql_all("products", "handle seo{title description}", "status:active")  # auto-paginate
s.rest("GET", "pages.json?handle=contact")
s.asset_get("layout/theme.liquid"); s.asset_put("layout/theme.liquid", new)  # main theme
fetch_live(f"https://{s.public}/products/x")   # storefront HTML, cache-busted
```

## Process — always in this order

### 1. Audit (read-only, no writes)
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/seo_audit.py" --store <key-or-myshopify-domain>
```
Prints a pass/fail matrix: meta coverage (products/collections), structured-data
presence & validity, og:image https + twitter:image, canonical, single H1,
breadcrumbs, brand-doubling in `<title>`, homepage SEO text, channel breadth,
sitemap. **Re-check any FAIL in a browser** before acting (cache).

### 2. Prioritise by impact
Rough order of ROI (see `reference/playbook.md` for the full list & how-to):
1. **Duplicate content** → unique product/collection descriptions (the #1 killer).
2. **Missing meta** → SEO title + meta description on products, collections, pages.
3. **Structured data** → Product+Offer, AggregateRating (verify Judge.me first!),
   Organization+sameAs, WebSite+SearchAction, BreadcrumbList, Article, FAQPage.
4. **Image alt** → bulk, but detect shared images (generic alt on shared, specific on unique).
5. **Navigation/structure** → high-intent collections into the menu; publish to all channels.
6. **Technical hygiene** → canonical, og:image https, twitter:image, noindex utility pages.
7. **Content** → blog cluster (topic articles + internal links), homepage SEO text.

### 3. Fix — dry-run, apply, verify
Every change: build → print a dry-run diff → `--apply` → verify (API read-back +
cache-busted live + browser for anything visible). Get user approval for anything
**visible or structural** (menus, homepage content, redirects, noindex).

### 4. Re-audit
Re-run `seo_audit.py`; confirm the matrix is green. Note any deliberate skips.

## What "cover everything" means — the full checklist

On-page: unique descriptions · SEO title ≤60 · meta desc ≤155 · no brand-doubling ·
no double-spaces · H1 single & meaningful. Collections: intro copy + SEO meta +
internal cross-links. Pages: title_tag/description_tag metafields on real pages
(skip utility/funnel). Images: alt everywhere (shared-image aware); WebP is
automatic via CDN (verify, don't "fix"). Structured data: all of the types above,
**valid JSON** (parse every block). Technical: canonical present · og:image
**https** · twitter:image · hreflang only if multi-locale · 404 real · noindex
search/utility pages. Structure: gender + family + brand collections in the menu
(`menuUpdate` preserves existing items faithfully) · everything published to all
sales channels · sitemap includes new collections/articles. Content: blog cluster
with AI hero images · homepage SEO text block.

Deep detail, exact patterns, and copy templates: **`reference/playbook.md`**.
The traps that waste hours: **`reference/pitfalls.md`**.
Drop-in Liquid / JSON-LD: **`reference/snippets.md`**.

## Crawl-based internal-link audit — `linkgraph.py`
The Admin-API audit above sees pages in isolation; this sees the **link graph**. BFS-crawls from the homepage (seeded with the full sitemap incl. Shopify's `?from=&to=` sub-sitemaps), builds the internal link graph, computes internal **PageRank**, click-depth and inbound counts, and flags **orphan** (0 inbound), **under-linked** (<3), and **too-deep** (>3 clicks) pages. Pure stdlib + requests/bs4, no keys.
```bash
uv run linkgraph.py audit --site esteban.ro --max 150 --threads 10
```
Typical win it surfaces: **blog articles orphaned** (0 internal inbound) — on esteban.ro every blog post was an orphan, so the AEO/organic content wasn't linked from anywhere. Fix = add contextual internal links from high-PageRank pages (top collections, a blog hub) down to the buried product/collection/blog pages it lists. Pairs with `gigi:shopify-geo` (that orphaned blog content is exactly the AEO play).

## SEO drift baseline — `drift.py`
Catches **silent regressions**: a theme update or app that quietly drops a title/canonical/schema or flips a page to `noindex`. Snapshots the SEO-critical fields into local SQLite (`~/.cache/arona-seo/drift.db`) and diffs later — the complement to GSC week-over-week (which sees traffic, not the cause). Pure stdlib + requests/bs4.
```bash
uv run drift.py baseline --site esteban.ro --max 40        # snapshot top pages (weekly, e.g. cron)
uv run drift.py compare  --url https://esteban.ro/collections/dama   # diff vs last snapshot
uv run drift.py history  --url https://esteban.ro/collections/dama
```
Snapshots title/meta/canonical/robots/H1/H2-count/JSON-LD types/OG/word-count + a hash. `compare` flags 🔴 CRITIC (title/canonical/robots-noindex/status/schema removed), 🟡 WARN (meta desc/H1/OG/word-count drop >30%), ℹ️ info. Run `baseline` weekly; `compare` (or re-baseline) to see what changed since. Pairs with GSC `wow` (`gigi:analytics`): wow tells you traffic dropped, drift tells you *which on-page element broke*.

## Apply listing/SEO fixes to a product — `scripts/product_fix.py` (DRY-RUN default)
The write-layer that turns diagnoses (from `gigi:cro`, `gigi:pricewatch compare`, `gigi:merchant-center-feed`, `gigi:cross-sell`) into actual product changes — **selectively + safely**.
```bash
# DRY-RUN (writes nothing) — shows before → after for each fix you pass:
uv run scripts/product_fix.py --store esteban --product <handle> \
   --seo-title "..." --seo-description "..." --body-file new.html
# Grandia & the single 'shopify' app: --app SHOPIFY --store n12w89-yy.myshopify.com
# Cross-sell metafield:  --metafield "custom.bought_together=gid://shopify/Product/..."
# Execute only after approval:  add --apply
```
- **DRY-RUN by default** — nothing is written without `--apply` (team write-guardrail, like `cs-actions`).
- **Selective approval** = you pass only the fix flags you approve (`--seo-title`/`--seo-description`/`--body`/`--metafield`); each shows `era → nou`.
- **Two apps:** `--app SHOPIFY_ARONA` (default: esteban/gt/nubra/labnoir) or `--app SHOPIFY` (the `n12w89-yy.myshopify.com` store = Grandia etc.).
- **Scope (all / low-sellers / specific):** the *selection* of which products comes from the diagnosis skills (cross-sell low-sellers, merchant-feed disapprovals, pricewatch compare) — state in chat which set + how many you're acting on; `product_fix` applies per product. Verified DRY-RUN: Grandia "raft-depozitare…" had an **empty SEO description** → the writer would fill it.

## Catalog/nav structure — `scripts/brand_collections.py` + `scripts/menu_addbrands.py`
For dupe/inspired-by catalogs: build **smart collections by inspiration brand** (SEO hubs + internal-link targets) and a **"După Brand" menu dropdown**.
```bash
uv run scripts/brand_collections.py --store esteban --min 3            # DRY-RUN: brands (from "...by <Brand>" titles) → proposed smart collections
uv run scripts/brand_collections.py --store esteban --min 3 --apply    # create smart collections (rule: title contains "by <Brand>", case-insensitive)
uv run scripts/menu_addbrands.py    --store esteban --top 8            # DRY-RUN: add a top-level "După Brand" item with the top-N brands
uv run scripts/menu_addbrands.py    --store esteban --top 8 --apply    # menuUpdate (preserves the whole existing tree)
```
DRY-RUN by default. `menu_addbrands` keeps only the **top-N brands by product count** in the menu (the rest stay as collections for SEO/links — don't dump 21 items in the nav). `brand_collections --apply` **auto-publishes each new collection to ALL sales channels** (golden rule #6 — new collections default to ZERO channels → 404 on storefront + invisible on Google/Shop; this bit us once: 21 collections created but unpublished → all 404 until `publishablePublish` on every channel). Done on Esteban (Jun 2026): 21 brand collections + "După Brand" menu with top 8.

## Internal linking — `scripts/internal_links.py` (DRY-RUN default)
Distribuie PageRank intern + de-orfanizează conținut. **Fără emoji** în textul inserat (convenție echipă). Toate inserțiile sunt **idempotente** (un marker regex șterge blocul anterior înainte de re-adăugare) și fiecare `collectionUpdate` retrimite SEO title+description existente (golden rule #1). Trei moduri:
```bash
uv run scripts/internal_links.py cluster   --store esteban --top 8           # interlink top-N colecții de brand (fiecare -> 3 frați circular)
uv run scripts/internal_links.py pdp-brand --store esteban --top 0 --apply    # link spre colecția de brand pe TOATE produsele (top 0 = toate brandurile)
uv run scripts/internal_links.py deorphan  --store esteban --map deorphan.json --apply  # colecție<->articol bidirecțional dintr-un JSON
```
- **cluster**: huburile de brand se leagă între ele -> crawl + flux PageRank. Ancoră = numele brandului.
- **pdp-brand**: pe fiecare produs „... by <Brand>" adaugă la finalul descrierii `Vezi toate <a>parfumurile inspirate din <Brand></a>` (ancoră = brandul, țintă = colecția lui). `--top 0` = toate brandurile (Esteban: **133 produse**); `--top 8` = doar topurile.
- **deorphan**: dă fiecărui articol orfan un inbound dintr-o colecție-hub tematică (colecțiile sunt în meniu => PageRank mare) + un CTA înapoi din articol. Maparea (colecție↔articole) e specifică magazinului -> fișier JSON: `[{"collection":"dama","label":"Damă","articles":[{"handle":"...","title":"..."}]}]`.

Făcut pe Esteban (Jun 2026): cluster top-8 + de-orfanizate toate 15 articolele blog (bidirecțional colecție↔articol) + link de brand pe toate 133 produse. **`mutation` type-uri care înșeală:** `articleUpdate.body` = **HTML!**, `productUpdate.descriptionHtml` = **String!** (opuse — nu le confunda).

## Logging (team convention)
After a run: `kb.py log --type skill --action used --name gigi:shopify-seo --summary "…"`.
